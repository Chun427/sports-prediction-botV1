"""
notifier.py — Telegram 推播模組（唯一 UI 層）
格式鎖定：只能填入數值，不可更改任何 emoji / 標題 / 排版 / 分隔線
"""
import os
import logging
import time
import requests
from typing import Optional

logger = logging.getLogger(__name__)

# ── 環境變數 ──────────────────────────────────────────────
TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT  = os.environ.get("TG_CHAT", "")

RETRY_COUNT   = 3
RETRY_DELAY   = 2   # seconds
REQUEST_TIMEOUT = 15


# ══════════════════════════════════════════════════════════
#  底層發送（帶 retry）
# ══════════════════════════════════════════════════════════
def _send(text: str, silent: bool = False) -> bool:
    """發送 Telegram 訊息，失敗最多重試 RETRY_COUNT 次，永不 raise。"""
    if not TG_TOKEN or not TG_CHAT:
        logger.error("[notifier] TG_TOKEN 或 TG_CHAT 未設定，無法推播")
        return False

    url     = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {
        "chat_id":                  TG_CHAT,
        "text":                     text,
        "parse_mode":               "HTML",
        "disable_notification":     silent,
        "disable_web_page_preview": True,
    }

    for attempt in range(1, RETRY_COUNT + 1):
        try:
            resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return True
            logger.warning(
                "[notifier] 推播失敗 attempt=%d status=%d body=%s",
                attempt, resp.status_code, resp.text[:200],
            )
        except requests.RequestException as exc:
            logger.warning("[notifier] 推播例外 attempt=%d: %s", attempt, exc)

        if attempt < RETRY_COUNT:
            time.sleep(RETRY_DELAY)

    logger.error("[notifier] 推播最終失敗（已重試 %d 次）", RETRY_COUNT)
    return False


# ══════════════════════════════════════════════════════════
#  ❗ 格式常數區（禁止修改結構，只能改佔位符）
# ══════════════════════════════════════════════════════════

# ── 賽前預測 ──────────────────────────────────────────────
_PRE_GAME_TMPL = """\
🎯 精算師預測系統
⚡ 終極鎖定盤口（賽前 30 分鐘）
━━━━━━━━━━━━━━━━
📅 台灣時間 {date}
{sport_emoji} {league}
{home} 🆚 {away}
━━━━━━━━━━━━━━━━
🎲 1,000,000 次蒙特卡羅模擬
📊 參考莊家數：{bookmaker_count} 家｜抽水：{vig_pct}%
🗃️ 數據來源：{data_source}
⚙️ 預測模式：{pred_mode}
🎯 信心指數：{confidence}
{value_line}
━━━━━━━━━━━━━━━━
📈 勝率分析
{home_short} 勝率：{home_win_pct}%｜{away_short} 勝率：{away_win_pct}%{draw_line}
━━━━━━━━━━━━━━━━
🏆 最可能比分 Top 5
{score_top5}
━━━━━━━━━━━━━━━━
📐 讓分分析
{spread_line}
📊 大小分分析
大分門檻：{total_line} 分｜Over：{over_pct}%｜Under：{under_pct}%
━━━━━━━━━━━━━━━━
📈 Value分析
{home_short} 優勢：{home_edge}%｜{away_short} 優勢：{away_edge}%
💰 Kelly 建議：{kelly_pct}% 資金
━━━━━━━━━━━━━━━━
⚠️ 數據分析，請理性投注。"""

# ── 賽後驗證報告 ──────────────────────────────────────────
_POST_GAME_TMPL = """\
📊 預測驗證報告 {date}
🎯 今日命中率：{hit}/{total}（{hit_pct}%）
⚙️ 預測模式：{pred_mode}
━━━━━━━━━━━━━━━━
獨贏盤      {moneyline_result}   {moneyline_hit}
精準比分    {exact_score}         {exact_hit}
讓分盤      {spread_result}        {spread_hit}
總分大小    {ou_result}           {ou_hit}"""

# ── 系統自學指標（週報後附帶）────────────────────────────
_METRICS_TMPL = """\
📈 系統自學指標（{sample_count} 場樣本）
━━━━━━━━━━━━━━━━
獨贏命中率：{moneyline_acc}%
大小盤命中：{ou_acc}%
讓分命中：{spread_acc}%
Kelly命中：{kelly_acc}%
Edge偏差：{edge_bias}%（+偏高估，-偏低估）
各運動命中：
{sport_breakdown}
━━━━━━━━━━━━━━━━
⚠️ 數據分析，請理性投注。"""

# ── 週報摘要 ─────────────────────────────────────────────
_WEEKLY_TMPL = """\
📅 本週預測週報 {week_range}
━━━━━━━━━━━━━━━━
總場次：{total_games} 場｜已驗證：{verified_games} 場
🎯 獨贏命中：{moneyline_hit}/{moneyline_total}（{moneyline_pct}%）
📐 讓分命中：{spread_hit}/{spread_total}（{spread_pct}%）
📊 大小命中：{ou_hit}/{ou_total}（{ou_pct}%）
🎯 精準比分：{exact_hit}/{exact_total}（{exact_pct}%）
━━━━━━━━━━━━━━━━
{game_list}
━━━━━━━━━━━━━━━━
⚠️ 數據分析，請理性投注。"""

# ── 世界盃特報 ───────────────────────────────────────────
_WC_TMPL = """\
🏆 世界盃特報
━━━━━━━━━━━━━━━━
🥇 奪冠熱門
{champion_list}
━━━━━━━━━━━━━━━━
👟 金靴熱門
{golden_boot_list}
━━━━━━━━━━━━━━━━
⚽ 金球熱門
{golden_ball_list}
━━━━━━━━━━━━━━━━
🧤 金手套熱門
{golden_glove_list}
━━━━━━━━━━━━━━━━
⚙️ 計算模式：{calc_mode}
⚠️ 數據分析，請理性投注。"""

# ── 系統待機通知（靜音）─────────────────────────────────
_STANDBY_TMPL = """\
⚙️ 系統執行完畢｜{datetime}
📋 監控賽事：{game_count} 場
ℹ️ {reason}"""


# ══════════════════════════════════════════════════════════
#  公開推播函式
# ══════════════════════════════════════════════════════════

def push_pre_game(data: dict) -> bool:
    """
    推播賽前預測。
    data 必須包含所有模板佔位符的鍵值。
    """
    # value_line 條件處理（有 value bet 才顯示）
    if data.get("has_value"):
        data["value_line"] = (
            f"💎 Value Bet！{data.get('value_team','')} "
            f"優勢 +{data.get('value_edge','')}%"
        )
    else:
        data["value_line"] = ""

    # draw_line（NBA/MLB 無平局，足球才顯示）
    if data.get("draw_pct") is not None:
        data["draw_line"] = f"｜和局：{data['draw_pct']}%"
    else:
        data["draw_line"] = ""

    try:
        msg = _PRE_GAME_TMPL.format(**data)
        return _send(msg)
    except KeyError as exc:
        logger.error("[notifier] push_pre_game 缺少欄位: %s", exc)
        return False


def push_post_game(data: dict) -> bool:
    """推播賽後驗證報告。"""
    try:
        msg = _POST_GAME_TMPL.format(**data)
        return _send(msg)
    except KeyError as exc:
        logger.error("[notifier] push_post_game 缺少欄位: %s", exc)
        return False


def push_metrics(data: dict) -> bool:
    """推播系統自學指標。"""
    try:
        msg = _METRICS_TMPL.format(**data)
        return _send(msg)
    except KeyError as exc:
        logger.error("[notifier] push_metrics 缺少欄位: %s", exc)
        return False


def push_weekly(data: dict) -> bool:
    """推播週報摘要。"""
    try:
        msg = _WEEKLY_TMPL.format(**data)
        return _send(msg)
    except KeyError as exc:
        logger.error("[notifier] push_weekly 缺少欄位: %s", exc)
        return False


def push_world_cup(data: dict) -> bool:
    """推播世界盃特報。"""
    try:
        msg = _WC_TMPL.format(**data)
        return _send(msg)
    except KeyError as exc:
        logger.error("[notifier] push_world_cup 缺少欄位: %s", exc)
        return False


def push_standby(game_count: int, reason: str, silent: bool = True) -> bool:
    """
    系統待機通知（預設靜音）。
    reason 例：'本輪無符合推播視窗之賽事'
    """
    from datetime import datetime, timezone, timedelta
    tw = timezone(timedelta(hours=8))
    dt_str = datetime.now(tw).strftime("%m/%d %H:%M")
    msg = _STANDBY_TMPL.format(
        datetime=dt_str,
        game_count=game_count,
        reason=reason,
    )
    return _send(msg, silent=silent)


def push_raw(text: str, silent: bool = False) -> bool:
    """直接推播任意文字（供 debug / 緊急告警用）。"""
    return _send(text, silent=silent)


# ══════════════════════════════════════════════════════════
#  格式輔助：產生 Top5 比分字串
# ══════════════════════════════════════════════════════════

def fmt_score_top5(scores: list[tuple]) -> str:
    """
    scores = [(home_score, away_score, probability), ...]
    回傳多行字串，格式：
      1. 112-108 (8.3%)
      2. 110-105 (7.1%)
      ...
    """
    lines = []
    for i, (h, a, p) in enumerate(scores[:5], 1):
        lines.append(f"  {i}. {h}-{a} ({p:.1f}%)")
    return "\n".join(lines) if lines else "  資料不足"


def fmt_sport_breakdown(breakdown: dict) -> str:
    """
    breakdown = {'NBA': 75.0, 'MLB': 60.0}
    回傳多行字串，格式：
      🏀 75.0%
      ⚾ 60.0%
    """
    sport_emoji = {
        "NBA": "🏀", "MLB": "⚾", "FIFA": "⚽",
        "WBC": "⚾", "OLYMPICS": "🏅",
    }
    lines = []
    for sport, acc in breakdown.items():
        emoji = sport_emoji.get(sport.upper(), "🏆")
        lines.append(f"  {emoji} {acc:.1f}%")
    return "\n".join(lines) if lines else "  暫無數據"
