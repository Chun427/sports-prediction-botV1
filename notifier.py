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

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT  = os.environ.get("TG_CHAT", "")

RETRY_COUNT     = 3
RETRY_DELAY     = 2
REQUEST_TIMEOUT = 15


# ══════════════════════════════════════════════════════════
#  底層發送（帶 retry）
# ══════════════════════════════════════════════════════════
def _send(text: str, silent: bool = False) -> bool:
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
            logger.warning("[notifier] 推播失敗 attempt=%d status=%d body=%s",
                           attempt, resp.status_code, resp.text[:200])
        except requests.RequestException as exc:
            logger.warning("[notifier] 推播例外 attempt=%d: %s", attempt, exc)
        if attempt < RETRY_COUNT:
            time.sleep(RETRY_DELAY)

    logger.error("[notifier] 推播最終失敗（已重試 %d 次）", RETRY_COUNT)
    return False


# ══════════════════════════════════════════════════════════
#  ❗ 格式常數區（禁止修改結構，只能改佔位符）
# ══════════════════════════════════════════════════════════

# ── 賽前預測（新版格式）─────────────────────────────────
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
📐 去Vig真實勝率
{home_short} {vig_bar_home} {vig_home_pct}%
{away_short} {vig_bar_away} {vig_away_pct}%

🎲 蒙特卡羅模擬勝率
{home_short} {mc_bar_home} {mc_home_pct}%
{away_short} {mc_bar_away} {mc_away_pct}%
━━━━━━━━━━━━━━━━
📈 Value分析
{home_short} 優勢：{home_edge}%｜{away_short} 優勢：{away_edge}%
━━━━━━━━━━━━━━━━
🏆 最可能出現的比分
{score_top5}
━━━━━━━━━━━━━━━━
📊 盤口深度分析
讓分盤口 {spread_display}
總分大小 {total_line}（{ou_direction}）
獨贏賠率 {home_short}:{home_odds_display}｜{away_short}:{away_odds_display}
━━━━━━━━━━━━━━━━
💰 台灣運彩實戰建議
{betting_advice}
━━━━━━━━━━━━━━━━
⚠️ 數據分析，請理性投注。"""

# ── 賽後驗證報告 ─────────────────────────────────────────
_POST_GAME_TMPL = """\
📊 預測驗證報告 {date}
🎯 今日命中率：{hit}/{total}（{hit_pct}%）
⚙️ 預測模式：{pred_mode}
━━━━━━━━━━━━━━━━
獨贏盤      {moneyline_result}   {moneyline_hit}
精準比分    {exact_score}         {exact_hit}
讓分盤      {spread_result}        {spread_hit}
總分大小    {ou_result}           {ou_hit}"""

# ── 系統自學指標 ─────────────────────────────────────────
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
#  格式輔助函式
# ══════════════════════════════════════════════════════════

def fmt_bar(pct: float, width: int = 10) -> str:
    """產生進度條：█ 填滿部分，░ 空白部分。"""
    filled = round(pct / 100 * width)
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def _shorten(name: str, max_len: int = 18) -> str:
    """全名優先；超過 max_len 字元自動取最後一個單詞（官方簡稱）。"""
    if len(name) <= max_len:
        return name
    parts = name.strip().split()
    return parts[-1] if parts else name


def fmt_score_top5(scores: list) -> str:
    """
    scores = [(home, away, pct) 或 (home, away, pct, home_name, away_name)]
    格式：🥇 Los Angeles Angels 5–3 Colorado Rockies（3.2%）
    隊名超過 18 字元自動縮為官方簡稱。
    """
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines  = []
    for i, item in enumerate(scores[:5]):
        medal = medals[i] if i < len(medals) else f"{i+1}."
        if len(item) >= 5:
            h, a, p, h_name, a_name = item[0], item[1], item[2], item[3], item[4]
        else:
            h, a, p = item[0], item[1], item[2]
            h_name, a_name = "", ""

        hn = _shorten(h_name) if h_name else ""
        an = _shorten(a_name) if a_name else ""

        if h == a:
            score_str = f"和局 {h}–{a}"
        elif hn and an:
            score_str = f"{hn} {h}–{a} {an}" if h > a else f"{an} {a}–{h} {hn}"
        else:
            score_str = f"{h}–{a}"

        lines.append(f"{medal} {score_str}（{p:.1f}%）")

    return "\n".join(lines) if lines else "  資料不足"


def fmt_betting_advice(
    home_name: str,
    away_name: str,
    mc_home_pct: float,
    mc_away_pct: float,
    spread_line: float,
    total_line: float,
    over_pct: float,
    kelly_pct: float,
    has_value: bool,
    value_team: str,
    home_edge: float = 0.0,
    away_edge: float = 0.0,
) -> str:
    """
    動態產生台灣運彩實戰建議（三層：主推 / 次要 / 備選）。
    排序依據：各盤口實際 Edge（優勢）由大到小。
    Edge 最大 = 🔮主推 / 第二 = 💎次要 / 第三 = ⭐備選
    """
    candidates = []

    # ── 獨贏 Edge：取主客較大的那側 ──
    if mc_home_pct >= mc_away_pct:
        ml_team, ml_label = home_name, "選 [主隊勝]"
        ml_edge = home_edge
    else:
        ml_team, ml_label = away_name, "選 [客隊勝]"
        ml_edge = away_edge
    kelly_note = f"💡 Kelly建議：{kelly_pct}% 資金" if kelly_pct > 0 else ""
    candidates.append({
        "edge":   ml_edge,
        "desc":   f"獨贏盤 → {ml_team} 勝出",
        "detail": f"› 劃位：{ml_label}\n› {kelly_note}" if kelly_note else f"› 劃位：{ml_label}",
    })

    # ── 大小分 Edge：over/under 偏離 50% 的程度 ──
    under_pct = 100 - over_pct
    if over_pct >= under_pct:
        ou_edge, ou_desc = over_pct - 50, f"總分大小 → 大分({total_line})"
        ou_label = f"選 [大分] {total_line}"
    else:
        ou_edge, ou_desc = under_pct - 50, f"總分大小 → 小分({total_line})"
        ou_label = f"選 [小分] {total_line}"
    candidates.append({
        "edge":   round(ou_edge, 1),
        "desc":   ou_desc,
        "detail": f"› 劃位：{ou_label}",
    })

    # ── 讓分 Edge：MC 覆蓋率偏離 50% 的程度 ──
    if spread_line and spread_line != 0:
        if mc_home_pct > 50:
            sp_team  = home_name
            sp_line  = spread_line
            sp_label = "選 [主隊讓球]" if sp_line < 0 else "選 [主隊受讓]"
            sp_edge  = mc_home_pct - 50
        else:
            sp_team  = away_name
            sp_line  = -spread_line
            sp_label = "選 [客隊讓球]" if sp_line < 0 else "選 [客隊受讓]"
            sp_edge  = mc_away_pct - 50
        sp_sign = f"{sp_line:+.1f}"
        candidates.append({
            "edge":   round(sp_edge, 1),
            "desc":   f"讓分盤 → {sp_team}({sp_sign})",
            "detail": f"› 劃位：{sp_label}",
        })

    # ── 依 Edge 由大到小排序 ──
    candidates.sort(key=lambda x: -x["edge"])
    icons = ["🔮【主推】", "💎【次要】", "⭐【備選】"]
    lines = []
    for i, c in enumerate(candidates[:3]):
        lines.append(f"{icons[i]}\n{c['desc']}\n{c['detail']}\n› Edge：+{c['edge']:.1f}%")

    return "\n\n".join(lines)


def fmt_sport_breakdown(breakdown: dict) -> str:
    sport_emoji = {"NBA": "🏀", "MLB": "⚾", "FIFA": "⚽", "WBC": "⚾", "OLYMPICS": "🏅"}
    lines = []
    for sport, acc in breakdown.items():
        emoji = sport_emoji.get(sport.upper(), "🏆")
        lines.append(f"  {emoji} {acc:.1f}%")
    return "\n".join(lines) if lines else "  暫無數據"


def _fmt_odds_display(odds: float) -> str:
    """賠率轉為美式顯示（>2.0 → 正數，<2.0 → 負數）。"""
    if not odds or odds <= 0:
        return "N/A"
    if odds >= 2.0:
        return f"+{round((odds - 1) * 100)}"
    else:
        return str(round(-100 / (odds - 1)))


# ══════════════════════════════════════════════════════════
#  公開推播函式
# ══════════════════════════════════════════════════════════

def push_pre_game(data: dict) -> bool:
    # value_line
    if data.get("has_value"):
        data["value_line"] = (
            f"💎 Value Bet！{data.get('value_team','')} "
            f"優勢 +{data.get('value_edge','')}%"
        )
    else:
        data["value_line"] = ""

    # 信心指數加百分比（若尚未包含 % 數字，自動附加）
    conf = data.get("confidence", "🔴 低")
    conf_pct = data.get("confidence_pct")
    if conf_pct and "%" not in conf:
        data["confidence"] = f"{conf}（{conf_pct}%）"

    # 進度條
    vig_home = float(data.get("vig_home_pct", 50))
    vig_away = float(data.get("vig_away_pct", 50))
    mc_home  = float(data.get("mc_home_pct",  50))
    mc_away  = float(data.get("mc_away_pct",  50))
    data["vig_bar_home"] = fmt_bar(vig_home)
    data["vig_bar_away"] = fmt_bar(vig_away)
    data["mc_bar_home"]  = fmt_bar(mc_home)
    data["mc_bar_away"]  = fmt_bar(mc_away)

    # 大小分方向
    data["ou_direction"] = "大分 🔼" if float(data.get("over_pct", 50)) >= 50 else "小分 🔽"

    # 賠率美式顯示
    data["home_odds_display"] = _fmt_odds_display(data.get("home_odds_raw"))
    data["away_odds_display"] = _fmt_odds_display(data.get("away_odds_raw"))

    # 讓分顯示
    spread = data.get("spread_line_val", 0)
    if spread and spread != 0:
        data["spread_display"] = f"{data.get('home_short','')} {spread:+.1f}"
    else:
        data["spread_display"] = "暫無資料"

    try:
        msg = _PRE_GAME_TMPL.format(**data)
        return _send(msg)
    except KeyError as exc:
        logger.error("[notifier] push_pre_game 缺少欄位: %s", exc)
        return False


def push_post_game(data: dict) -> bool:
    try:
        msg = _POST_GAME_TMPL.format(**data)
        return _send(msg)
    except KeyError as exc:
        logger.error("[notifier] push_post_game 缺少欄位: %s", exc)
        return False


def push_metrics(data: dict) -> bool:
    try:
        msg = _METRICS_TMPL.format(**data)
        return _send(msg)
    except KeyError as exc:
        logger.error("[notifier] push_metrics 缺少欄位: %s", exc)
        return False


def push_weekly(data: dict) -> bool:
    try:
        msg = _WEEKLY_TMPL.format(**data)
        return _send(msg)
    except KeyError as exc:
        logger.error("[notifier] push_weekly 缺少欄位: %s", exc)
        return False


def push_world_cup(data: dict) -> bool:
    try:
        msg = _WC_TMPL.format(**data)
        return _send(msg)
    except KeyError as exc:
        logger.error("[notifier] push_world_cup 缺少欄位: %s", exc)
        return False


def push_standby(game_count: int, reason: str, silent: bool = True) -> bool:
    from datetime import datetime, timezone, timedelta
    tw = timezone(timedelta(hours=8))
    dt_str = datetime.now(tw).strftime("%m/%d %H:%M")
    msg = _STANDBY_TMPL.format(datetime=dt_str, game_count=game_count, reason=reason)
    return _send(msg, silent=silent)


def push_raw(text: str, silent: bool = False) -> bool:
    return _send(text, silent=silent)
