"""
worldcup_engine.py — 世界盃獨立模組
生效期間：2026/06/11 ～ 2026/07/19
功能：
  - 104 場賽程 cache（worldcup_schedule.json）
  - 賽前推播（同 NBA/MLB 邏輯，主窗口 30 分鐘～3 小時）
  - 賽後驗證（四項命中）
  - 每日 20:00 台灣時間推播獎項預測（冠軍/金靴/金球/金手套）
  - 每踢滿 4 場觸發一次深度分析推播
缺失時系統不 crash，NBA/MLB 完全不受影響。
"""
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 所有依賴模組皆 lazy import，缺失時靜默降級
_dm  = None   # data_manager
_rv  = None   # result_verifier
_te  = None   # tournament_engine
_nt  = None   # notifier
_df  = None   # data_fetcher

def _get_dm():
    global _dm
    if _dm is None:
        try: import data_manager as m; _dm = m
        except ImportError: pass
    return _dm

def _get_rv():
    global _rv
    if _rv is None:
        try: import result_verifier as m; _rv = m
        except ImportError: pass
    return _rv

def _get_te():
    global _te
    if _te is None:
        try: import tournament_engine as m; _te = m
        except ImportError: pass
    return _te

def _get_nt():
    global _nt
    if _nt is None:
        try: import notifier as m; _nt = m
        except ImportError: pass
    return _nt

def _get_df():
    global _df
    if _df is None:
        try: import data_fetcher as m; _df = m
        except ImportError: pass
    return _df

TW = timezone(timedelta(hours=8))

BASE_DIR        = Path(__file__).parent
SCHEDULE_FILE   = BASE_DIR / "worldcup_schedule.json"
WC_FLAGS_KEY    = "wc_daily_push"
DEEP_ANALYSIS_INTERVAL = 4   # 每踢幾場觸發深度分析

# 世界盃有效期間（台灣時間）
WC_START = datetime(2026, 6, 11, tzinfo=TW)
WC_END   = datetime(2026, 7, 20, tzinfo=TW)   # 7/19 決賽 +1 天緩衝

# 推播時間：每日 20:00～20:59（台灣時間）
DAILY_PUSH_HOUR = 20

# 推播提前天數（開賽前 N 天開始每日推播）
ADVANCE_DAYS = 3


# ══════════════════════════════════════════════════════════
#  有效期間判斷
# ══════════════════════════════════════════════════════════

def is_wc_active() -> bool:
    """現在是否在世界盃有效期間。"""
    now = datetime.now(TW)
    return WC_START <= now <= WC_END


def is_daily_push_hour() -> bool:
    """現在是否在每日推播時段（台灣時間 20:00～20:59）。"""
    return datetime.now(TW).hour == DAILY_PUSH_HOUR


# ══════════════════════════════════════════════════════════
#  賽程 cache（worldcup_schedule.json）
# ══════════════════════════════════════════════════════════

def _load_schedule() -> dict:
    try:
        if SCHEDULE_FILE.exists():
            with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        logger.warning("[wc] 載入賽程失敗: %s", exc)
    return {"games": [], "updated_at": "", "completed_count": 0}


def _save_schedule(data: dict) -> bool:
    try:
        with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as exc:
        logger.warning("[wc] 儲存賽程失敗: %s", exc)
        return False


def get_wc_games() -> list[dict]:
    """回傳世界盃賽程列表。"""
    return _load_schedule().get("games", [])


def save_wc_games(games: list[dict]) -> bool:
    data = _load_schedule()
    data["games"]      = games
    data["updated_at"] = datetime.now(TW).isoformat()
    return _save_schedule(data)


def get_completed_count() -> int:
    return _load_schedule().get("completed_count", 0)


def increment_completed_count() -> int:
    data = _load_schedule()
    data["completed_count"] = data.get("completed_count", 0) + 1
    _save_schedule(data)
    return data["completed_count"]


# ══════════════════════════════════════════════════════════
#  每日推播 flag
# ══════════════════════════════════════════════════════════

def _get_wc_flags() -> dict:
    """從 data_manager 的 flags 中讀取 wc 專屬 flag。"""
    try:
        dm = _get_dm()
        flags = dm.load_flags()
        return flags.get(WC_FLAGS_KEY, {})
    except Exception as exc:
        logger.warning("[wc] 讀取 wc flags 失敗: %s", exc)
        return {}


def _set_wc_flag(key: str, value) -> bool:
    try:
        dm = _get_dm()
        flags = dm.load_flags()
        wc    = flags.get(WC_FLAGS_KEY, {})
        wc[key] = value
        flags[WC_FLAGS_KEY] = wc
        return dm.save_flags(flags)
    except Exception as exc:
        logger.warning("[wc] 寫入 wc flags 失敗: %s", exc)
        return False


def is_daily_pushed_today() -> bool:
    """今日（台灣時間）是否已推播每日獎項特報。"""
    today  = datetime.now(TW).strftime("%Y-%m-%d")
    pushed = _get_wc_flags().get("daily_pushed_date", "")
    return pushed == today


def mark_daily_pushed() -> bool:
    today = datetime.now(TW).strftime("%Y-%m-%d")
    return _set_wc_flag("daily_pushed_date", today)


def should_trigger_deep_analysis() -> bool:
    """每踢滿 DEEP_ANALYSIS_INTERVAL 場觸發一次深度分析。"""
    completed = get_completed_count()
    last_deep = _get_wc_flags().get("last_deep_analysis_count", 0)
    if completed == 0:
        return False
    return (completed // DEEP_ANALYSIS_INTERVAL) > (last_deep // DEEP_ANALYSIS_INTERVAL)


def mark_deep_analysis_triggered() -> bool:
    return _set_wc_flag("last_deep_analysis_count", get_completed_count())


# ══════════════════════════════════════════════════════════
#  賽前推播條件
# ══════════════════════════════════════════════════════════

def has_upcoming_matches(advance_days: int = ADVANCE_DAYS) -> bool:
    """
    未來 advance_days 天內是否有世界盃賽事。
    用於判斷是否啟動每日獎項推播。
    """
    now     = datetime.now(TW)
    cutoff  = now + timedelta(days=advance_days)
    games   = get_wc_games()
    for g in games:
        gdt = _parse_game_time(g)
        if gdt and now <= gdt <= cutoff:
            return True
    return False


def get_pushable_games() -> list[dict]:
    """
    回傳符合推播窗口的世界盃賽事（主窗口 + 備用窗口）。
    直接呼叫 result_verifier.in_push_window。
    """
    try:
        rv = _get_rv()
        import data_manager   as dm
    except ImportError as exc:
        logger.warning("[wc] 依賴模組缺失: %s", exc)
        return []

    pushable = []
    for game in get_wc_games():
        game_id = game.get("game_id", "")
        if dm.is_pushed_today(game_id):
            continue
        utc_str = game.get("game_time_utc", "")
        tw_str  = game.get("game_time", "")
        if rv.in_push_window(utc_str, tw_str):
            pushable.append(game)
    return pushable


# ══════════════════════════════════════════════════════════
#  獎項預測資料組裝
# ══════════════════════════════════════════════════════════

def build_award_data() -> Optional[dict]:
    """
    組裝獎項推播所需資料。
    優先使用 tournament_engine，失敗則 fallback Odds API，
    再失敗回傳 None（推播靜音通知）。
    """
    # 優先：tournament_engine
    try:
        te = _get_te()
        awards = te.compute_award_probabilities()
        if awards.get("champion"):
            logger.info("[wc] 使用 tournament_engine 獎項資料")
            return awards
    except ImportError:
        logger.info("[wc] tournament_engine 不存在，嘗試 Odds API fallback")
    except Exception as exc:
        logger.warning("[wc] tournament_engine 失敗: %s", exc)

    # Fallback：Odds API
    try:
        df = _get_df()
        odds = df.fetch_odds("soccer_fifa_world_cup_winner", markets="h2h")
        if odds:
            from tournament_engine import _normalize_from_odds
            champ = {item["home_team"]: item.get("h2h_odds", {}).get("home", 0) for item in odds[:8]}
            return {
                "champion":     _normalize_from_odds(champ, top_n=8),
                "golden_boot":  [],
                "golden_ball":  [],
                "golden_glove": [],
                "calc_mode":    "即時盤口（fallback）",
            }
    except Exception as exc:
        logger.warning("[wc] Odds API fallback 失敗: %s", exc)

    return None


# ══════════════════════════════════════════════════════════
#  深度分析推播資料
# ══════════════════════════════════════════════════════════

def build_deep_analysis_data() -> Optional[dict]:
    """
    每踢滿 4 場觸發一次深度分析。
    回傳推播所需資料，失敗回傳 None。
    """
    completed = get_completed_count()
    award_data = build_award_data()
    if not award_data:
        return None

    award_data["deep_analysis"] = True
    award_data["completed_count"] = completed
    award_data["calc_mode"] = f"深度分析（已完賽 {completed} 場）"
    return award_data


# ══════════════════════════════════════════════════════════
#  主入口：check_and_push（供 sports_prediction.py 呼叫）
# ══════════════════════════════════════════════════════════

def check_and_push() -> int:
    """
    每次 CI 執行時呼叫此函式。
    回傳推播次數（0 表示本輪無推播）。
    任何 exception 絕對不影響 push_today()，全部 try/except 包覆。
    """
    try:
        if not is_wc_active():
            return 0
        pushed = 0
        try:
            if is_daily_push_hour() and not is_daily_pushed_today():
                if has_upcoming_matches():
                    _push_daily_awards()
                    pushed += 1
        except Exception as exc:
            logger.warning("[wc] 每日推播失敗（繼續）: %s", exc)
        try:
            if should_trigger_deep_analysis():
                _push_deep_analysis()
                mark_deep_analysis_triggered()
                pushed += 1
        except Exception as exc:
            logger.warning("[wc] 深度分析失敗（繼續）: %s", exc)
        return pushed
    except Exception as exc:
        logger.warning("[wc] check_and_push 完全失敗（不影響主系統）: %s", exc)
        return 0


def _push_daily_awards():
    """組裝並推播每日獎項特報。"""
    try:
        nt = _get_nt()
        data = build_award_data()
        if not data:
            nt.push_raw("⚽ 世界盃獎項資料暫時無法取得，系統持續監控中", silent=True)
            mark_daily_pushed()
            return

        def _fmt(lst):
            if not lst:
                return "  暫無資料"
            return "\n".join(f"  {i+1}. {t}  {p}%" for i, (t, p) in enumerate(lst))

        nt.push_world_cup({
            "champion_list":     _fmt(data.get("champion", [])),
            "golden_boot_list":  _fmt(data.get("golden_boot", [])),
            "golden_ball_list":  _fmt(data.get("golden_ball", [])),
            "golden_glove_list": _fmt(data.get("golden_glove", [])),
            "calc_mode":         data.get("calc_mode", "動態引擎"),
        })
        mark_daily_pushed()
        logger.info("[wc] 每日獎項特報推播完成")
    except Exception as exc:
        logger.warning("[wc] _push_daily_awards 失敗: %s", exc)


def _push_deep_analysis():
    """推播深度分析特報。"""
    try:
        nt = _get_nt()
        data = build_deep_analysis_data()
        if not data:
            return

        def _fmt(lst):
            if not lst:
                return "  暫無資料"
            return "\n".join(f"  {i+1}. {t}  {p}%" for i, (t, p) in enumerate(lst))

        completed = data.get("completed_count", 0)
        header    = f"🔍 深度分析特報（已完賽 {completed} 場）\n"

        nt.push_world_cup({
            "champion_list":     _fmt(data.get("champion", [])),
            "golden_boot_list":  _fmt(data.get("golden_boot", [])),
            "golden_ball_list":  _fmt(data.get("golden_ball", [])),
            "golden_glove_list": _fmt(data.get("golden_glove", [])),
            "calc_mode":         data.get("calc_mode", "深度分析"),
        })
        logger.info("[wc] 深度分析推播完成（%d 場）", completed)
    except Exception as exc:
        logger.warning("[wc] _push_deep_analysis 失敗: %s", exc)


# ══════════════════════════════════════════════════════════
#  輔助
# ══════════════════════════════════════════════════════════

def _parse_game_time(game: dict) -> Optional[datetime]:
    """從 game dict 解析開賽時間（TW aware datetime）。"""
    try:
        rv = _get_rv()
        utc = game.get("game_time_utc", "")
        tw  = game.get("game_time", "")
        dt  = rv._parse_utc_to_tw(utc)
        if dt is None and tw:
            dt = rv._parse_tw_str(tw)
        return dt
    except Exception:
        return None
