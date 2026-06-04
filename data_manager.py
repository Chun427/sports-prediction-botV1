"""
data_manager.py — 狀態層
負責：flags.json / weekly_games.json / team_stats.json /
      metrics.json / historical_dataset.csv 的讀寫與 metrics 計算。
不包含任何 prediction / push 邏輯。
"""
import csv
import json
import logging
import os
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

TW = timezone(timedelta(hours=8))

# ── 檔案路徑 ────────────────────────────────────────────────
BASE_DIR        = Path(__file__).parent
FLAGS_FILE      = BASE_DIR / "flags.json"
WEEKLY_FILE     = BASE_DIR / "weekly_games.json"
TEAM_STATS_FILE = BASE_DIR / "team_stats.json"
METRICS_FILE    = BASE_DIR / "metrics.json"
DATASET_FILE    = BASE_DIR / "historical_dataset.csv"

# historical_dataset.csv 欄位定義
DATASET_COLUMNS = [
    "game_id", "date", "sport", "league",
    "home_team", "away_team",
    "home_odds", "away_odds", "draw_odds",
    "spread", "total_line",
    "home_win_prob", "away_win_prob",
    "home_avg_score", "away_avg_score",
    "home_std", "away_std",
    "bookmaker_count", "vig_pct",
    "value_edge", "kelly_fraction",
    "pred_mode",
    # 賽後填入
    "actual_home_score", "actual_away_score",
    "moneyline_hit", "spread_hit", "ou_hit", "exact_hit",
]


# ══════════════════════════════════════════════════════════
#  JSON 通用讀寫
# ══════════════════════════════════════════════════════════

def _load_json(path: Path, default: Any = None) -> Any:
    """讀取 JSON，失敗回傳 default（不 raise）。"""
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        logger.warning("[data_manager] 無法讀取 %s: %s", path.name, exc)
    return default if default is not None else {}


def _save_json(path: Path, data: Any) -> bool:
    """寫入 JSON，失敗 log warning 不 raise。"""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as exc:
        logger.warning("[data_manager] 無法寫入 %s: %s", path.name, exc)
        return False


# ══════════════════════════════════════════════════════════
#  flags.json（推播狀態 + 模擬結果快取）
# ══════════════════════════════════════════════════════════

def load_flags() -> dict:
    """
    讀取 flags.json。
    - 不存在 → 自動建立 {}
    - JSON 損壞 → backup 原檔 + reset {}
    - file lock-safe：讀取前先確認檔案完整性
    """
    if not FLAGS_FILE.exists():
        logger.info("[data_manager] flags.json 不存在，自動初始化")
        _save_json(FLAGS_FILE, {})
        return {}
    try:
        with open(FLAGS_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            raise ValueError("flags.json 為空")
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("flags.json 非 dict")
        return data
    except Exception as exc:
        # 損壞時 backup 原檔並 reset
        backup = FLAGS_FILE.with_suffix(".json.bak")
        try:
            import shutil
            shutil.copy2(FLAGS_FILE, backup)
            logger.warning("[data_manager] flags.json 損壞，已備份至 %s，重置為 {}: %s",
                           backup.name, exc)
        except Exception:
            pass
        _save_json(FLAGS_FILE, {})
        return {}


def save_flags(flags: dict) -> bool:
    return _save_json(FLAGS_FILE, flags)


def get_flag(game_id: str) -> dict:
    flags = load_flags()
    return flags.get(game_id, {})


def set_flag(game_id: str, updates: dict) -> bool:
    flags = load_flags()
    entry = flags.get(game_id, {})
    entry.update(updates)
    flags[game_id] = entry
    return save_flags(flags)


def is_pushed_today(game_id: str) -> bool:
    """
    判斷今日（台灣時間）是否已推播過此場次。
    daily flag 跨日自動重置。
    """
    entry = get_flag(game_id)
    pushed_date = entry.get("pushed_date", "")
    today = datetime.now(TW).strftime("%Y-%m-%d")
    return pushed_date == today


def mark_pushed(game_id: str, sim_result: Optional[dict] = None) -> bool:
    today = datetime.now(TW).strftime("%Y-%m-%d")
    updates = {"pushed_date": today, "pre_pushed": True}
    if sim_result:
        updates["sim_result"] = sim_result
    return set_flag(game_id, updates)


def is_post_pushed(game_id: str) -> bool:
    return get_flag(game_id).get("post_pushed", False)


def mark_post_pushed(game_id: str) -> bool:
    return set_flag(game_id, {"post_pushed": True})


def get_sim_result(game_id: str) -> Optional[dict]:
    return get_flag(game_id).get("sim_result")


# ══════════════════════════════════════════════════════════
#  weekly_games.json（本週賽事快取）
# ══════════════════════════════════════════════════════════

def load_weekly_games() -> list:
    data = _load_json(WEEKLY_FILE, {})
    return data.get("games", [])


def save_weekly_games(games: list) -> bool:
    data = {
        "updated_at": datetime.now(TW).isoformat(),
        "games": games,
    }
    return _save_json(WEEKLY_FILE, data)


def weekly_games_expired() -> bool:
    """快取超過 24 小時視為過期。"""
    data = _load_json(WEEKLY_FILE, {})
    updated_at = data.get("updated_at")
    if not updated_at:
        return True
    try:
        dt = datetime.fromisoformat(updated_at)
        age = (datetime.now(TW) - dt).total_seconds()
        return age > 86400
    except Exception:
        return True


def all_games_finished(games: list) -> bool:
    """若所有賽事的 post_pushed=True，視為本週快取可刷新。"""
    if not games:
        return True
    for g in games:
        if not is_post_pushed(g.get("game_id", "")):
            return False
    return True


# ══════════════════════════════════════════════════════════
#  team_stats.json（球隊統計快取，每日一次）
# ══════════════════════════════════════════════════════════

def load_team_stats() -> dict:
    return _load_json(TEAM_STATS_FILE, {})


def save_team_stats(stats: dict) -> bool:
    data = {
        "updated_date": datetime.now(TW).strftime("%Y-%m-%d"),
        "stats": stats,
    }
    return _save_json(TEAM_STATS_FILE, data)


def team_stats_expired() -> bool:
    """快取非今日即視為過期。"""
    data = _load_json(TEAM_STATS_FILE, {})
    return data.get("updated_date") != datetime.now(TW).strftime("%Y-%m-%d")


def get_team_stat(team: str) -> Optional[dict]:
    data = _load_json(TEAM_STATS_FILE, {})
    return data.get("stats", {}).get(team)


# ══════════════════════════════════════════════════════════
#  historical_dataset.csv
# ══════════════════════════════════════════════════════════

def _ensure_csv():
    if not DATASET_FILE.exists():
        with open(DATASET_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=DATASET_COLUMNS)
            writer.writeheader()


def append_pre_game_row(row: dict) -> bool:
    """賽前寫入特徵（賽後欄位留空）。"""
    try:
        _ensure_csv()
        full_row = {col: row.get(col, "") for col in DATASET_COLUMNS}
        with open(DATASET_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=DATASET_COLUMNS)
            writer.writerow(full_row)
        return True
    except Exception as exc:
        logger.warning("[data_manager] 寫入 CSV 失敗: %s", exc)
        return False


def update_post_game_row(game_id: str, post_data: dict) -> bool:
    """
    賽後補填：根據 game_id 找到對應行，更新賽後欄位。
    若找不到則 append 一行（防止遺漏）。
    """
    try:
        _ensure_csv()
        rows = []
        found = False
        with open(DATASET_FILE, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                if r.get("game_id") == game_id:
                    r.update(post_data)
                    found = True
                rows.append(r)

        with open(DATASET_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=DATASET_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

        if not found:
            logger.warning("[data_manager] game_id=%s 未找到，追加賽後行", game_id)
            full_row = {col: post_data.get(col, "") for col in DATASET_COLUMNS}
            full_row["game_id"] = game_id
            with open(DATASET_FILE, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=DATASET_COLUMNS)
                writer.writerow(full_row)
        return True
    except Exception as exc:
        logger.warning("[data_manager] 更新 CSV 失敗: %s", exc)
        return False


def load_dataset() -> list[dict]:
    """讀取完整歷史資料集，失敗回傳空 list。"""
    try:
        _ensure_csv()
        with open(DATASET_FILE, "r", newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception as exc:
        logger.warning("[data_manager] 讀取 CSV 失敗: %s", exc)
        return []


def count_complete_rows() -> int:
    """回傳同時有賽前特徵與賽後結果的完整行數。"""
    rows = load_dataset()
    return sum(
        1 for r in rows
        if r.get("actual_home_score") and r.get("actual_away_score")
    )


# ══════════════════════════════════════════════════════════
#  metrics.json（系統自學指標）
# ══════════════════════════════════════════════════════════

def compute_and_save_metrics() -> Optional[dict]:
    """
    從 historical_dataset.csv 計算自學指標並寫入 metrics.json。
    資料不足則靜默跳過，回傳 None。
    """
    rows = load_dataset()
    complete = [
        r for r in rows
        if r.get("actual_home_score") and r.get("actual_away_score")
           and r.get("moneyline_hit") != ""
    ]

    if len(complete) < 5:
        logger.info("[data_manager] 完整資料不足 5 筆，跳過 metrics 計算")
        return None

    def _pct(col: str) -> float:
        vals = [r[col] for r in complete if r.get(col) in ("1", "0", 1, 0)]
        if not vals:
            return 0.0
        return round(sum(int(v) for v in vals) / len(vals) * 100, 1)

    # Kelly 命中：edge > 0 且 moneyline_hit=1
    kelly_rows = [r for r in complete if float(r.get("value_edge", 0) or 0) > 0]
    kelly_acc  = 0.0
    if kelly_rows:
        kelly_acc = round(
            sum(int(r.get("moneyline_hit", 0)) for r in kelly_rows) / len(kelly_rows) * 100,
            1,
        )

    # Edge 偏差
    edge_vals = []
    for r in complete:
        try:
            pred = float(r.get("home_win_prob", 0) or 0)
            hit  = int(r.get("moneyline_hit", 0) or 0)
            edge_vals.append(pred - hit)
        except (ValueError, TypeError):
            pass
    edge_bias = round(sum(edge_vals) / len(edge_vals) * 100, 1) if edge_vals else 0.0

    # 各運動命中
    sports: dict[str, list] = {}
    for r in complete:
        sport = r.get("sport", "OTHER").upper()
        sports.setdefault(sport, [])
        try:
            sports[sport].append(int(r.get("moneyline_hit", 0) or 0))
        except (ValueError, TypeError):
            pass
    sport_breakdown = {
        s: round(sum(hits) / len(hits) * 100, 1)
        for s, hits in sports.items() if hits
    }

    metrics = {
        "sample_count":   len(complete),
        "moneyline_acc":  _pct("moneyline_hit"),
        "ou_acc":         _pct("ou_hit"),
        "spread_acc":     _pct("spread_hit"),
        "kelly_acc":      kelly_acc,
        "edge_bias":      f"+{edge_bias}" if edge_bias >= 0 else str(edge_bias),
        "sport_breakdown": sport_breakdown,
        "computed_at":    datetime.now(TW).isoformat(),
    }
    _save_json(METRICS_FILE, metrics)
    logger.info("[data_manager] metrics 計算完成：%s", metrics)
    return metrics


def load_metrics() -> Optional[dict]:
    return _load_json(METRICS_FILE, None)


# ══════════════════════════════════════════════════════════
#  git commit（雙重備份）
# ══════════════════════════════════════════════════════════

def git_commit_state() -> bool:
    """
    將狀態檔案提交至 repo（雙重備份）。
    無變更時靜默跳過，不顯示紅燈。
    """
    files = [
        str(FLAGS_FILE), str(WEEKLY_FILE), str(TEAM_STATS_FILE),
        str(METRICS_FILE), str(DATASET_FILE),
    ]
    existing = [f for f in files if os.path.exists(f)]
    if not existing:
        return True

    try:
        subprocess.run(["git", "config", "user.email", "bot@sports-prediction"], check=False)
        subprocess.run(["git", "config", "user.name",  "SportsBot"],             check=False)

        subprocess.run(["git", "add"] + existing, check=True)

        # 若無變更，git diff --cached --quiet 回傳 0 → 跳過
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"])
        if diff.returncode == 0:
            logger.info("[data_manager] git: 無變更，跳過 commit")
            return True

        subprocess.run(
            ["git", "commit", "-m", f"[bot] update state {datetime.now(TW).strftime('%Y-%m-%d %H:%M')}"],
            check=True,
        )
        logger.info("[data_manager] git commit 成功")
        return True
    except subprocess.CalledProcessError as exc:
        logger.warning("[data_manager] git commit 失敗（不影響系統）: %s", exc)
        return False
