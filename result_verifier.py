"""
result_verifier.py — 賽後驗證模組
負責：四項命中驗證、auto_results()、verify_all()、完賽 4 小時強制觸發。
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

TW = timezone(timedelta(hours=8))

FORCE_TRIGGER_HOURS = 4   # completed=False 但逾 N 小時強制觸發


# ══════════════════════════════════════════════════════════
#  四項命中驗證
# ══════════════════════════════════════════════════════════

def verify_prediction(sim_result: dict, actual: dict) -> dict:
    """
    四項驗證：獨贏 / 精準比分 / 讓分 / 大小分。
    回傳：{moneyline_hit, exact_hit, spread_hit, ou_hit,
           moneyline_result, exact_score, spread_result, ou_result,
           hit_count, total=4}
    """
    home_score = int(actual.get("home_score", -1))
    away_score = int(actual.get("away_score", -1))

    if home_score < 0 or away_score < 0:
        logger.warning("[verifier] 比分無效，跳過驗證")
        return _empty_verify()

    # ── 獨贏盤 ────────────────────────────────────────────
    pred_winner = _pred_winner(sim_result)
    if home_score > away_score:
        actual_winner = sim_result.get("home_team", "主隊")
        ml_result     = f"{sim_result.get('home_short', actual_winner)} 勝"
    elif away_score > home_score:
        actual_winner = sim_result.get("away_team", "客隊")
        ml_result     = f"{sim_result.get('away_short', actual_winner)} 勝"
    else:
        actual_winner = "Draw"
        ml_result     = "平局"

    ml_hit = 1 if pred_winner == actual_winner else 0

    # ── 精準比分 ──────────────────────────────────────────
    exact_score = f"{home_score}-{away_score}"
    top5 = sim_result.get("top5_scores", [])
    exact_hit = 0
    if top5:
        top5_set = {(int(h), int(a)) for h, a, _ in top5}
        exact_hit = 1 if (home_score, away_score) in top5_set else 0

    # ── 讓分盤 ────────────────────────────────────────────
    spread_line = float(sim_result.get("spread_line") or 0)
    home_cover  = (home_score + spread_line) > away_score
    spread_result = "覆蓋" if home_cover else "未覆蓋"
    pred_cover    = float(sim_result.get("home_cover_pct") or 50) > 50
    spread_hit    = 1 if home_cover == pred_cover else 0

    # ── 大小分 ────────────────────────────────────────────
    total_line = float(sim_result.get("total_line") or 0)
    actual_total = home_score + away_score
    is_over    = actual_total > total_line
    pred_over  = float(sim_result.get("over_pct") or 50) > 50
    ou_result  = "Over" if is_over else "Under"
    ou_hit     = 1 if is_over == pred_over else 0

    hit_count = ml_hit + exact_hit + spread_hit + ou_hit

    return {
        "moneyline_hit":    ml_hit,
        "exact_hit":        exact_hit,
        "spread_hit":       spread_hit,
        "ou_hit":           ou_hit,
        "moneyline_result": ml_result,
        "exact_score":      exact_score,
        "spread_result":    spread_result,
        "ou_result":        ou_result,
        "hit_count":        hit_count,
        "total":            4,
        "hit_pct":          round(hit_count / 4 * 100),
    }


def _pred_winner(sim_result: dict) -> str:
    """從 sim_result 判斷模型預測勝者。"""
    home_pct = float(sim_result.get("home_win_pct") or 50)
    away_pct = float(sim_result.get("away_win_pct") or 50)
    draw_pct = float(sim_result.get("draw_pct") or 0)

    if draw_pct > home_pct and draw_pct > away_pct:
        return "Draw"
    if home_pct >= away_pct:
        return sim_result.get("home_team", "home")
    return sim_result.get("away_team", "away")


def _empty_verify() -> dict:
    return {
        "moneyline_hit": 0, "exact_hit": 0,
        "spread_hit": 0,    "ou_hit": 0,
        "moneyline_result": "—", "exact_score": "—",
        "spread_result": "—",    "ou_result": "—",
        "hit_count": 0, "total": 4, "hit_pct": 0,
    }


# ══════════════════════════════════════════════════════════
#  時間視窗判斷
# ══════════════════════════════════════════════════════════

def in_push_window(game_time_utc: str) -> bool:
    """
    推播視窗：賽後 6 小時 ～ 未來 16 小時。
    game_time_utc = ISO 字串（UTC）
    """
    try:
        game_dt = datetime.fromisoformat(game_time_utc.replace("Z", "+00:00"))
        now     = datetime.now(TW).astimezone(timezone.utc)
        diff_h  = (game_dt - now).total_seconds() / 3600
        return -6 <= diff_h <= 16
    except Exception as exc:
        logger.warning("[verifier] in_push_window 解析失敗 %s: %s", game_time_utc, exc)
        return False


def should_force_trigger(game_time_utc: str, completed: bool) -> bool:
    """
    completed=False 但開賽已達 FORCE_TRIGGER_HOURS 小時 → 強制觸發。
    """
    if completed:
        return False
    try:
        game_dt = datetime.fromisoformat(game_time_utc.replace("Z", "+00:00"))
        now     = datetime.now(TW).astimezone(timezone.utc)
        elapsed = (now - game_dt).total_seconds() / 3600
        return elapsed >= FORCE_TRIGGER_HOURS
    except Exception as exc:
        logger.warning("[verifier] should_force_trigger 解析失敗: %s", exc)
        return False


def is_silent_hours() -> bool:
    """靜音模式：台灣時間 23:00 ~ 08:00。"""
    hour = datetime.now(TW).hour
    return hour >= 23 or hour < 8


# ══════════════════════════════════════════════════════════
#  auto_results（主推播流程呼叫）
# ══════════════════════════════════════════════════════════

def auto_results(games: list[dict]) -> list[dict]:
    """
    遍歷賽事，對「已推播賽前 + 尚未推播賽後」場次執行賽後驗證。
    回傳需要推播的驗證結果 list。
    """
    import data_manager as dm
    import data_fetcher  as df

    to_push = []

    for game in games:
        game_id = game.get("game_id", "")
        try:
            if not dm.get_flag(game_id).get("pre_pushed"):
                continue   # 賽前未推，跳過
            if dm.is_post_pushed(game_id):
                continue   # 已推過賽後，跳過

            game_time_utc = game.get("game_time_utc", "")

            # 抓取比分
            result = df.fetch_game_result(game)
            completed = result.get("completed", False) if result else False
            force     = should_force_trigger(game_time_utc, completed)

            if not result:
                if force:
                    logger.warning("[verifier] game_id=%s 強制觸發但無比分資料", game_id)
                continue

            if not completed and not force:
                continue   # 還沒完賽且未到強制觸發時間

            # 取回賽前模擬結果
            sim_result = dm.get_sim_result(game_id)
            if not sim_result:
                logger.warning("[verifier] game_id=%s 無賽前模擬快取，重新補填空快取", game_id)
                sim_result = {}

            # 補充 team 名稱（給 verifier 用）
            sim_result["home_team"]  = game.get("home_team", "")
            sim_result["away_team"]  = game.get("away_team", "")
            sim_result["home_short"] = game.get("home_short", "")
            sim_result["away_short"] = game.get("away_short", "")

            verify = verify_prediction(sim_result, result)

            # 更新 CSV
            dm.update_post_game_row(game_id, {
                "actual_home_score": result["home_score"],
                "actual_away_score": result["away_score"],
                "moneyline_hit":     verify["moneyline_hit"],
                "spread_hit":        verify["spread_hit"],
                "ou_hit":            verify["ou_hit"],
                "exact_hit":         verify["exact_hit"],
            })

            dm.mark_post_pushed(game_id)

            to_push.append({
                "game":   game,
                "result": result,
                "verify": verify,
                "sim":    sim_result,
            })

        except Exception as exc:
            logger.warning("[verifier] auto_results game_id=%s 失敗: %s", game_id, exc)

    return to_push


# ══════════════════════════════════════════════════════════
#  verify_all（強制補漏）
# ══════════════════════════════════════════════════════════

def verify_all(games: list[dict]) -> list[dict]:
    """
    強制遍歷所有未推播賽後場次（帶 DEBUG log）。
    忽略 post_pushed flag，重新驗證所有賽前已推場次。
    """
    import data_manager as dm
    import data_fetcher  as df

    to_push = []

    for game in games:
        game_id = game.get("game_id", "")
        try:
            if not dm.get_flag(game_id).get("pre_pushed"):
                logger.debug("[verifier][verify_all] %s 賽前未推，跳過", game_id)
                continue

            logger.debug("[verifier][verify_all] 強制處理 game_id=%s", game_id)

            result = df.fetch_game_result(game)
            if not result:
                logger.debug("[verifier][verify_all] %s 無比分資料", game_id)
                continue

            sim_result = dm.get_sim_result(game_id) or {}
            sim_result["home_team"]  = game.get("home_team", "")
            sim_result["away_team"]  = game.get("away_team", "")
            sim_result["home_short"] = game.get("home_short", "")
            sim_result["away_short"] = game.get("away_short", "")

            verify = verify_prediction(sim_result, result)

            dm.update_post_game_row(game_id, {
                "actual_home_score": result["home_score"],
                "actual_away_score": result["away_score"],
                "moneyline_hit":     verify["moneyline_hit"],
                "spread_hit":        verify["spread_hit"],
                "ou_hit":            verify["ou_hit"],
                "exact_hit":         verify["exact_hit"],
            })
            dm.mark_post_pushed(game_id)

            to_push.append({
                "game":   game,
                "result": result,
                "verify": verify,
                "sim":    sim_result,
            })
            logger.debug("[verifier][verify_all] %s 驗證完成 hits=%d/4",
                         game_id, verify["hit_count"])

        except Exception as exc:
            logger.warning("[verifier][verify_all] game_id=%s 失敗: %s", game_id, exc)

    logger.info("[verifier][verify_all] 補漏完成，共推播 %d 場", len(to_push))
    return to_push
