"""
tournament_engine.py — 世界盃動態引擎（完全獨立模組）
負責：淘汰偵測、出局標記、四大獎項（冠軍/金靴/金球/金手套）Monte Carlo。
缺失時系統靜默跳過，不 crash。
"""
import json
import logging
import random
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

BASE_DIR        = Path(__file__).parent
STATE_FILE      = BASE_DIR / "tournament_state.json"

MC_RUNS = 200_000   # 世界盃用 20 萬次（速度 / 精度平衡）


# ══════════════════════════════════════════════════════════
#  State I/O
# ══════════════════════════════════════════════════════════

def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        logger.warning("[tournament] 載入狀態失敗: %s", exc)
    return {"teams": {}, "eliminated": [], "results": []}


def _save_state(state: dict) -> bool:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        return True
    except Exception as exc:
        logger.warning("[tournament] 儲存狀態失敗: %s", exc)
        return False


# ══════════════════════════════════════════════════════════
#  淘汰管理
# ══════════════════════════════════════════════════════════

def mark_eliminated(team: str) -> bool:
    """標記球隊出局，更新 tournament_state.json。"""
    state = _load_state()
    if team not in state.get("eliminated", []):
        state.setdefault("eliminated", []).append(team)
        logger.info("[tournament] %s 已出局", team)
    return _save_state(state)


def get_active_participants() -> list[str]:
    """回傳尚在賽的球隊列表（排除已出局）。"""
    state = _load_state()
    all_teams  = list(state.get("teams", {}).keys())
    eliminated = state.get("eliminated", [])
    active = [t for t in all_teams if t not in eliminated]
    return active


def update_team_data(team: str, data: dict) -> bool:
    """更新球員 / 球隊數據（用於獎項計算）。"""
    state = _load_state()
    state.setdefault("teams", {})[team] = data
    return _save_state(state)


def record_result(game_result: dict) -> bool:
    """記錄賽事結果供後續模型使用。"""
    state = _load_state()
    state.setdefault("results", []).append(game_result)
    return _save_state(state)


# ══════════════════════════════════════════════════════════
#  動態獎項機率計算（Monte Carlo）
# ══════════════════════════════════════════════════════════

def compute_award_probabilities(odds_data: Optional[dict] = None) -> dict:
    """
    計算四大獎項機率。
    odds_data = {'champion': {team: odds}, 'golden_boot': {player: odds}, ...}
    無盤口時使用內部 MC 模型。
    """
    try:
        state  = _load_state()
        active = get_active_participants()

        if not active:
            logger.warning("[tournament] 無在賽球隊，跳過獎項計算")
            return _empty_awards()

        # 冠軍
        champion_probs = _calc_champion_probs(active, state, odds_data)
        # 金靴
        golden_boot    = _calc_golden_boot(state, odds_data)
        # 金球
        golden_ball    = _calc_golden_ball(state, odds_data)
        # 金手套
        golden_glove   = _calc_golden_glove(state, odds_data)

        calc_mode = "即時盤口" if odds_data else "動態引擎"

        return {
            "champion":     champion_probs,
            "golden_boot":  golden_boot,
            "golden_ball":  golden_ball,
            "golden_glove": golden_glove,
            "calc_mode":    calc_mode,
        }
    except Exception as exc:
        logger.error("[tournament] 獎項計算失敗: %s", exc)
        return _empty_awards()


def _calc_champion_probs(
    active: list[str],
    state: dict,
    odds_data: Optional[dict],
) -> list[tuple[str, float]]:
    """
    回傳 [(team, prob%), ...] Top 8，機率加總 100%。
    優先使用即時賠率；無賠率則用 MC 加權模擬。
    """
    # 即時賠率路徑
    if odds_data and "champion" in odds_data:
        raw = {t: odds for t, odds in odds_data["champion"].items() if t in active}
        if raw:
            return _normalize_from_odds(raw, top_n=8)

    # 內部 MC 路徑
    teams_data = state.get("teams", {})
    strengths  = {
        t: float(teams_data.get(t, {}).get("elo", 1500)) for t in active
    }
    probs = _mc_knockout_champion(active, strengths)
    top8  = sorted(probs.items(), key=lambda x: -x[1])[:8]
    return [(t, round(p * 100, 1)) for t, p in top8]


def _mc_knockout_champion(teams: list[str], strengths: dict) -> dict[str, float]:
    """
    簡化淘汰賽 Monte Carlo：每輪兩兩配對，依 Elo 差距計算勝率。
    """
    wins: dict[str, int] = {t: 0 for t in teams}
    n = len(teams)
    if n == 0:
        return wins

    for _ in range(MC_RUNS):
        pool = teams[:]
        random.shuffle(pool)
        while len(pool) > 1:
            next_round = []
            for i in range(0, len(pool) - 1, 2):
                a, b   = pool[i], pool[i + 1]
                p_a    = _elo_win_prob(strengths.get(a, 1500), strengths.get(b, 1500))
                winner = a if random.random() < p_a else b
                next_round.append(winner)
            if len(pool) % 2 == 1:
                next_round.append(pool[-1])   # bye
            pool = next_round
        if pool:
            wins[pool[0]] = wins.get(pool[0], 0) + 1

    return {t: v / MC_RUNS for t, v in wins.items()}


def _elo_win_prob(elo_a: float, elo_b: float) -> float:
    """ELO 勝率公式。"""
    return 1 / (1 + 10 ** ((elo_b - elo_a) / 400))


def _calc_golden_boot(state: dict, odds_data: Optional[dict]) -> list[tuple[str, float]]:
    """金靴：射手得分機率。即時賠率優先。"""
    if odds_data and "golden_boot" in odds_data:
        return _normalize_from_odds(odds_data["golden_boot"], top_n=5)

    # 內部：以球員 goals 數排名
    players = _extract_players(state, "goals")
    if not players:
        return []
    total = sum(players.values()) + 1e-9
    top5  = sorted(players.items(), key=lambda x: -x[1])[:5]
    return [(p, round(v / total * 100, 1)) for p, v in top5]


def _calc_golden_ball(state: dict, odds_data: Optional[dict]) -> list[tuple[str, float]]:
    """金球：以 rating 評分排名。即時賠率優先。"""
    if odds_data and "golden_ball" in odds_data:
        return _normalize_from_odds(odds_data["golden_ball"], top_n=5)

    players = _extract_players(state, "rating")
    if not players:
        return []
    total = sum(players.values()) + 1e-9
    top5  = sorted(players.items(), key=lambda x: -x[1])[:5]
    return [(p, round(v / total * 100, 1)) for p, v in top5]


def _calc_golden_glove(state: dict, odds_data: Optional[dict]) -> list[tuple[str, float]]:
    """金手套：守門員 clean_sheets。即時賠率優先。"""
    if odds_data and "golden_glove" in odds_data:
        return _normalize_from_odds(odds_data["golden_glove"], top_n=5)

    goalkeepers = _extract_players(state, "clean_sheets")
    if not goalkeepers:
        return []
    total = sum(goalkeepers.values()) + 1e-9
    top5  = sorted(goalkeepers.items(), key=lambda x: -x[1])[:5]
    return [(p, round(v / total * 100, 1)) for p, v in top5]


def _extract_players(state: dict, stat_key: str) -> dict[str, float]:
    """從 teams data 中提取球員指定統計。"""
    result = {}
    for team_data in state.get("teams", {}).values():
        for player in team_data.get("players", []):
            name = player.get("name", "")
            val  = float(player.get(stat_key, 0) or 0)
            if name and val > 0:
                result[name] = result.get(name, 0) + val
    return result


def _normalize_from_odds(odds_map: dict, top_n: int = 5) -> list[tuple[str, float]]:
    """賠率 → 機率（去 Vig 後歸一化），回傳 Top N。"""
    if not odds_map:
        return []
    try:
        implied = {t: 1 / o for t, o in odds_map.items() if o and o > 0}
        total   = sum(implied.values())
        if total <= 0:
            return []
        normalized = {t: v / total for t, v in implied.items()}
        top = sorted(normalized.items(), key=lambda x: -x[1])[:top_n]
        return [(t, round(p * 100, 1)) for t, p in top]
    except Exception as exc:
        logger.warning("[tournament] _normalize_from_odds 失敗: %s", exc)
        return []


def _empty_awards() -> dict:
    return {
        "champion":     [],
        "golden_boot":  [],
        "golden_ball":  [],
        "golden_glove": [],
        "calc_mode":    "無資料",
    }


# ══════════════════════════════════════════════════════════
#  賽後淘汰偵測
# ══════════════════════════════════════════════════════════

def detect_and_mark_elimination(game: dict, result: dict) -> Optional[str]:
    """
    根據賽事類型判斷是否有球隊出局，若是則標記。
    回傳被淘汰球隊名稱（或 None）。
    """
    try:
        league     = game.get("league", "").upper()
        is_knockout = any(kw in league for kw in ("KNOCKOUT", "ROUND OF", "QUARTERFINAL", "SEMIFINAL", "FINAL"))
        if not is_knockout:
            return None

        home_score = int(result.get("home_score", 0))
        away_score = int(result.get("away_score", 0))

        if home_score == away_score:
            return None   # 可能延長賽，不立即判定

        loser = game["away_team"] if home_score > away_score else game["home_team"]
        mark_eliminated(loser)
        return loser
    except Exception as exc:
        logger.warning("[tournament] detect_and_mark_elimination 失敗: %s", exc)
        return None


# ══════════════════════════════════════════════════════════
#  回測（Brier Score）
# ══════════════════════════════════════════════════════════

def backtest_wc() -> Optional[float]:
    """
    計算動態模型 vs 市場賠率的 Brier Score。
    回傳 brier_score（越低越好），資料不足回傳 None。
    """
    try:
        state   = _load_state()
        results = state.get("results", [])
        if len(results) < 5:
            logger.info("[tournament] 賽事結果不足 5 筆，跳過回測")
            return None

        errors = []
        for r in results:
            pred_prob = float(r.get("pred_home_prob", 0.5))
            actual    = 1 if r.get("home_win") else 0
            errors.append((pred_prob - actual) ** 2)

        brier = round(sum(errors) / len(errors), 4)
        logger.info("[tournament] Brier Score: %s (n=%d)", brier, len(errors))
        return brier
    except Exception as exc:
        logger.warning("[tournament] backtest_wc 失敗: %s", exc)
        return None
