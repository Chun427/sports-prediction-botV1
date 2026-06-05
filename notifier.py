"""
notifier.py — Telegram 推播模組（唯一 UI 層）
格式鎖定：只能填入數值，不可更改任何 emoji / 標題 / 排版 / 分隔線
"""
import os
import logging
import time
import requests

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
        logger.error("[notifier] TG_TOKEN 或 TG_CHAT 未設定")
        return False
    url     = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT, "text": text,
        "parse_mode": "HTML", "disable_notification": silent,
        "disable_web_page_preview": True,
    }
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return True
            logger.warning("[notifier] 推播失敗 attempt=%d status=%d", attempt, resp.status_code)
        except requests.RequestException as exc:
            logger.warning("[notifier] 推播例外 attempt=%d: %s", attempt, exc)
        if attempt < RETRY_COUNT:
            time.sleep(RETRY_DELAY)
    logger.error("[notifier] 推播最終失敗（已重試 %d 次）", RETRY_COUNT)
    try:
        alert = "\U0001f6a8 推播失敗\nfunction: _send\nstatus: Telegram API failed after retries"
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": alert}, timeout=5)
    except Exception:
        pass
    return False


# ══════════════════════════════════════════════════════════
#  ❗ 格式常數區（禁止修改結構，只能改佔位符）
# ══════════════════════════════════════════════════════════

_PRE_GAME_TMPL = """\
🎯 精算師預測系統
⚡ 量化預測模型（賽前 30 分鐘）

━━━━━━━━━━━━━━━━
📅 台灣時間 {date}
{sport_emoji} {league}
{home} 🆚 {away}
━━━━━━━━━━━━━━━━

📐 去Vig真實勝率
{home} {vig_bar_home} {vig_home_pct}%
{away} {vig_bar_away} {vig_away_pct}%

蒙特卡羅模擬勝率
{home} {mc_bar_home} {mc_home_pct}%
{away} {mc_bar_away} {mc_away_pct}%

━━━━━━━━━━━━━━━━
📊 Edge（模型優勢）
{home} {home_edge_fmt}%
{away} {away_edge_fmt}%

━━━━━━━━━━━━━━━━
🏆 最可能出現的比分
{score_top5}

━━━━━━━━━━━━━━━━
📊 盤口深度分析
讓分盤口     {spread_display}
總分大小     {total_line}（{ou_direction}）
獨贏賠率     {home}:{home_odds_display}｜{away}:{away_odds_display}

━━━━━━━━━━━━━━━━
💰 台灣運彩實戰建議
{betting_advice}

━━━━━━━━━━━━━━━━
📊 風控資訊
- Kelly：{kelly_pct}%
- Risk Level：{risk_level}

━━━━━━━━━━━━━━━━
📡 數據來源：{data_source}
⚠️ 請理性投注。"""

_POST_GAME_TMPL = """\
📊 賽後結果
📅 台灣時間 {date}

{sport_emoji} {home} vs {away}

━━━━━━━━━━━━━━━
命中結果：{hit} / {total}（{hit_pct}%）
━━━━━━━━━━━━━━━

獨贏：{moneyline_hit}
精準比分：{exact_hit}
讓分：{spread_hit}
大小分：{ou_hit}

────────────────

📊 模型表現
- EV預測準確性：{ev_accuracy}
- Edge命中：{edge_hit}

📊 模型 vs 市場
模型優勢：{value_team} {value_edge}%
市場偏差：{market_bias}

────────────────

📌 預測模式：{pred_mode}"""

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

_STANDBY_TMPL = """\
⚙️ 系統執行完畢｜{datetime}
📋 監控賽事：{game_count} 場
ℹ️ {reason}"""


# ══════════════════════════════════════════════════════════
#  格式輔助函式
# ══════════════════════════════════════════════════════════

def fmt_bar(pct: float, width: int = 10) -> str:
    filled = max(0, min(width, round(pct / 100 * width)))
    return "█" * filled + "░" * (width - filled)


def _shorten(name: str, max_len: int = 18) -> str:
    if len(name) <= max_len:
        return name
    parts = name.strip().split()
    return parts[-1] if parts else name


def fmt_score_top5(scores: list) -> str:
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
    home_name: str, away_name: str,
    mc_home_pct: float, mc_away_pct: float,
    spread_line: float, total_line: float,
    over_pct: float, kelly_pct: float,
    has_value: bool, value_team: str,
    home_edge: float = 0.0, away_edge: float = 0.0,
) -> str:
    candidates = []

    # 獨贏
    if mc_home_pct >= mc_away_pct:
        ml_team, ml_label, ml_edge = home_name, "選 [主隊勝]", home_edge
    else:
        ml_team, ml_label, ml_edge = away_name, "選 [客隊勝]", away_edge
    candidates.append({
        "edge": ml_edge,
        "line": f"🔮【主推】獨贏盤 → {ml_team} 勝出",
    })

    # 大小分
    under_pct = 100 - over_pct
    if over_pct >= under_pct:
        ou_edge = over_pct - 50
        ou_line = f"💎【次要】總分大小 → 大分({total_line})"
    else:
        ou_edge = under_pct - 50
        ou_line = f"💎【次要】總分大小 → 小分({total_line})"
    candidates.append({"edge": round(ou_edge, 1), "line": ou_line})

    # 讓分
    if spread_line and spread_line != 0:
        if mc_home_pct > 50:
            sp_team = home_name
            sp_sign = f"{spread_line:+.1f}"
            sp_edge = mc_home_pct - 50
        else:
            sp_team = away_name
            sp_sign = f"{-spread_line:+.1f}"
            sp_edge = mc_away_pct - 50
        candidates.append({
            "edge": round(sp_edge, 1),
            "line": f"⭐【備選】讓分盤 → {sp_team}({sp_sign})",
        })

    # 依 Edge 排序，重新分配圖示
    candidates.sort(key=lambda x: -x["edge"])
    icons = ["🔮【主推】", "💎【次要】", "⭐【備選】"]
    lines = []
    for i, c in enumerate(candidates[:3]):
        # 替換圖示
        raw = c["line"]
        for old_icon in ["🔮【主推】", "💎【次要】", "⭐【備選】"]:
            raw = raw.replace(old_icon, "")
        lines.append(f"{icons[i]}{raw.strip()}")
    return "\n".join(lines)


def _fmt_odds_display(odds: float) -> str:
    if not odds or odds <= 0:
        return "N/A"
    if odds >= 2.0:
        return f"+{round((odds - 1) * 100)}"
    return str(round(-100 / (odds - 1)))


def fmt_sport_breakdown(breakdown: dict) -> str:
    sport_emoji = {"NBA": "🏀", "MLB": "⚾", "FIFA": "⚽", "WBC": "⚾", "OLYMPICS": "🏅"}
    lines = []
    for sport, acc in breakdown.items():
        emoji = sport_emoji.get(sport.upper(), "🏆")
        lines.append(f"  {emoji} {acc:.1f}%")
    return "\n".join(lines) if lines else "  暫無數據"


def _market_bias_label(value_edge: float) -> str:
    if abs(value_edge) < 2:
        return "市場定價合理"
    if value_edge > 0:
        strength = "嚴重" if value_edge > 8 else "中度"
        return f"{strength}低估 {value_edge:.1f}%"
    strength = "嚴重" if value_edge < -8 else "中度"
    return f"{strength}高估 {abs(value_edge):.1f}%"


# ══════════════════════════════════════════════════════════
#  公開推播函式
# ══════════════════════════════════════════════════════════

def push_pre_game(data: dict, silent: bool = False) -> bool:
    # 信心指數加百分比
    conf     = data.get("confidence", "🔴 低")
    conf_pct = data.get("confidence_pct")
    if conf_pct and "%" not in conf:
        data["confidence"] = f"{conf}（{conf_pct}%）"

    # 進度條
    data["vig_bar_home"] = fmt_bar(float(data.get("vig_home_pct", 50)))
    data["vig_bar_away"] = fmt_bar(float(data.get("vig_away_pct", 50)))
    data["mc_bar_home"]  = fmt_bar(float(data.get("mc_home_pct",  50)))
    data["mc_bar_away"]  = fmt_bar(float(data.get("mc_away_pct",  50)))

    # 大小分方向
    data["ou_direction"] = "大分 🔼" if float(data.get("over_pct", 50)) >= 50 else "小分 🔽"

    # 賠率顯示
    data["home_odds_display"] = _fmt_odds_display(data.get("home_odds_raw"))
    data["away_odds_display"] = _fmt_odds_display(data.get("away_odds_raw"))

    # 讓分顯示
    spread = data.get("spread_line_val", 0)
    data["spread_display"] = (
        f"{data.get('away','')} {spread:+.1f}" if spread and spread != 0 else "暫無資料"
    )

    # Edge 格式化（帶正負號）
    home_edge = float(data.get("home_edge", 0))
    away_edge = float(data.get("away_edge", 0))
    data["home_edge_fmt"] = f"+{home_edge:.1f}" if home_edge >= 0 else f"{home_edge:.1f}"
    data["away_edge_fmt"] = f"+{away_edge:.1f}" if away_edge >= 0 else f"{away_edge:.1f}"

    # Risk Level（依 Kelly % 判斷）
    kp = float(data.get("kelly_pct", 0))
    data["risk_level"] = "低" if kp < 2 else ("高" if kp > 5 else "中")

    try:
        msg = _PRE_GAME_TMPL.format(**data)
        return _send(msg, silent=silent)
    except KeyError as exc:
        logger.error("[notifier] push_pre_game 缺少欄位: %s", exc)
        return False


def push_post_game(data: dict, silent: bool = False) -> bool:
    try:
        msg = _POST_GAME_TMPL.format(**data)
        return _send(msg, silent=silent)
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
