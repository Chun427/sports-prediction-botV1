"""
data_fetcher.py — 純資料抓取層
負責：The Odds API、nba_api、pybaseball、賽程整理、去 Vig preprocessing。
不包含任何 prediction / push 邏輯。
"""
import logging
import statistics
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

TW = timezone(timedelta(hours=8))

ODDS_API_KEY   = os.environ.get("ODDS_API_KEY", "")
ODDS_API_BASE  = "https://api.the-odds-api.com/v4"
RETRY_COUNT    = 3
RETRY_DELAY    = 3
REQUEST_TIMEOUT = 15

# 支援的聯盟 sport key（核心清單，不含已失效的 WBC/OLYMPICS）
SPORT_KEYS = {
    "NBA":  "basketball_nba",
    "MLB":  "baseball_mlb",
    "FIFA": "soccer_fifa_world_cup",
}

# 每個聯盟的 rolling lookahead（天數）
# NBA/MLB 每天都有賽事，2天夠；FIFA World Cup 需要提前建池
LOOKAHEAD_DAYS = {
    "NBA":  2,
    "MLB":  2,
    "FIFA": 14,
}
LOOKBACK_DAYS = 3   # 往回抓幾天（未結算賽事）

# 動態驗證快取（當日有效 sport keys）
_VALID_SPORT_KEYS_CACHE: set[str] = set()

SPORT_EMOJI = {
    "NBA": "🏀", "MLB": "⚾", "FIFA": "⚽",
    "WBC": "⚾", "OLYMPICS": "🏅",
}

# 賠率 API 失敗時的預設場次（至少不讓 CI 斷）
DEFAULT_GAMES: list[dict] = []

#  HTTP 通用 retry

def _get(url: str, params: dict = None) -> Optional[dict]:
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("[fetcher] HTTP %d attempt=%d url=%s", resp.status_code, attempt, url)
        except requests.RequestException as exc:
            logger.warning("[fetcher] 請求失敗 attempt=%d: %s", attempt, exc)
        if attempt < RETRY_COUNT:
            time.sleep(RETRY_DELAY)
    return None

#  The Odds API

def fetch_odds(sport_key: str, markets: str = "h2h,spreads,totals",
               commence_from: str = None, commence_to: str = None) -> list[dict]:
    if not ODDS_API_KEY:
        logger.error("[fetcher] ODDS_API_KEY 未設定")
        return []
    url    = f"{ODDS_API_BASE}/sports/{sport_key}/odds"
    params = {"apiKey": ODDS_API_KEY, "regions": "us,eu,uk",
              "markets": markets, "oddsFormat": "decimal"}
    if commence_from:
        params["commenceTimeFrom"] = commence_from
    if commence_to:
        params["commenceTimeTo"]   = commence_to
    data = _get(url, params)
    if data is None:
        logger.warning("[fetcher] fetch_odds 失敗，sport=%s", sport_key)
        return []
    return data if isinstance(data, list) else []

def fetch_valid_sport_keys() -> set[str]:
    global _VALID_SPORT_KEYS_CACHE
    if _VALID_SPORT_KEYS_CACHE:
        return _VALID_SPORT_KEYS_CACHE
    if not ODDS_API_KEY:
        return set(SPORT_KEYS.values())
    data = _get(f"{ODDS_API_BASE}/sports", {"apiKey": ODDS_API_KEY})
    if data and isinstance(data, list):
        _VALID_SPORT_KEYS_CACHE = {s["key"] for s in data if s.get("active")}
        logger.info("[fetcher] 動態 sports：%d 個有效 key", len(_VALID_SPORT_KEYS_CACHE))
        return _VALID_SPORT_KEYS_CACHE
    logger.warning("[fetcher] /sports 失敗，使用內建清單")
    return set(SPORT_KEYS.values())

def _rolling_window(sport_name: str) -> tuple[str, str]:
    """
    依 sport 產生 rolling 時間窗口：
      from = 現在 - LOOKBACK_DAYS（抓未結算舊賽事）
      to   = 現在 + LOOKAHEAD_DAYS[sport]（依聯盟動態）
    """
    now      = datetime.now(TW)
    lookback = LOOKBACK_DAYS
    lookahead = LOOKAHEAD_DAYS.get(sport_name.upper(), 2)
    start    = now - timedelta(days=lookback)
    end      = now + timedelta(days=lookahead)
    fmt      = "%Y-%m-%dT%H:%M:%SZ"
    utc      = timezone.utc
    return start.astimezone(utc).strftime(fmt), end.astimezone(utc).strftime(fmt)


def fetch_all_sports_games() -> list[dict]:
    """
    抓取今日賽事（commenceTimeFrom=現在 ~ commenceTimeTo=今天23:59 TW）。
    cache 內禁止出現明天以後的賽事。
    """
    valid_keys                = fetch_valid_sport_keys()
    games                     = []
    fetch_log                 = {"ok": [], "skip": [], "fail": []}
    for sport_name, sport_key in SPORT_KEYS.items():
        if sport_key not in valid_keys:
            fetch_log["skip"].append(sport_name); continue
        try:
            # 每個 sport 用自己的 rolling window
            cf, ct = _rolling_window(sport_name)
            lookahead = LOOKAHEAD_DAYS.get(sport_name.upper(), 2)
            logger.info("[fetcher] %s window: from=%s lookahead=%dd", sport_name, cf[:10], lookahead)
            raw = fetch_odds(sport_key, commence_from=cf, commence_to=ct)
            n = len(games)
            games += [g for item in raw if (g := _parse_game(item, sport_name))]
            fetch_log["ok"].append(f"{sport_name}+{len(games)-n}")
        except Exception as exc:
            logger.warning("[fetcher] %s 失敗: %s", sport_name, exc)
            fetch_log["fail"].append(sport_name)

    logger.info("[fetcher] Rolling Pool Report: ok=%s skip=%s fail=%s total=%d",
                fetch_log["ok"], fetch_log["skip"], fetch_log["fail"], len(games))
    if not games:
        logger.warning("[fetcher] 所有賽事抓取失敗，回退 DEFAULT_GAMES")
        return DEFAULT_GAMES
    games.sort(key=lambda g: g.get("game_time", ""))
    return games

def _parse_game(item: dict, sport_name: str) -> Optional[dict]:
    try:
        game_id   = item.get("id", "")
        home_team = item.get("home_team", "")
        away_team = item.get("away_team", "")
        game_time = item.get("commence_time", "")   # UTC ISO string

        # 轉台灣時間字串
        tw_time = _utc_to_tw(game_time)

        # 解析各市場賠率
        h2h_odds     = _extract_h2h(item)
        spread_data  = _extract_spread(item)
        totals_data  = _extract_totals(item)

        if not home_team or not away_team:
            return None

        return {
            "game_id":       game_id,
            "sport":         sport_name,
            "league":        _league_label(sport_name, item),
            "sport_emoji":   SPORT_EMOJI.get(sport_name, "🏆"),
            "home_team":     home_team,
            "away_team":     away_team,
            "home_short":    _short_name(home_team),
            "away_short":    _short_name(away_team),
            "game_time":     tw_time,
            "game_time_utc": game_time,
            "h2h_odds":      h2h_odds,      # {home: x, away: x, draw: x, bookmakers: [...]}
            "spread":        spread_data,   # {line: x, home_odds: x, away_odds: x}
            "totals":        totals_data,   # {line: x, over_odds: x, under_odds: x}
            "bookmaker_count": len(item.get("bookmakers", [])),
        }
    except Exception as exc:
        logger.warning("[fetcher] _parse_game 失敗 id=%s: %s", item.get("id", "?"), exc)
        return None

def _extract_h2h(item: dict) -> dict:
    home_team = item.get("home_team", "")
    away_team = item.get("away_team", "")
    home_odds_list, away_odds_list, draw_odds_list = [], [], []

    for bk in item.get("bookmakers", []):
        for mkt in bk.get("markets", []):
            if mkt.get("key") != "h2h":
                continue
            for outcome in mkt.get("outcomes", []):
                name  = outcome.get("name", "")
                price = float(outcome.get("price", 0))
                if name == home_team:
                    home_odds_list.append(price)
                elif name == away_team:
                    away_odds_list.append(price)
                elif name == "Draw":
                    draw_odds_list.append(price)

    def _avg(lst):
        # 用中位數聚合，避免離群莊家造成偏誤 [M1]
        return round(statistics.median(lst), 1) if lst else None

    return {
        "home":       _avg(home_odds_list),
        "away":       _avg(away_odds_list),
        "draw":       _avg(draw_odds_list),
        "bookmakers": item.get("bookmakers", []),
    }

def _extract_spread(item: dict) -> dict:
    lines, home_odds_list, away_odds_list = [], [], []
    home_team = item.get("home_team", "")

    for bk in item.get("bookmakers", []):
        for mkt in bk.get("markets", []):
            if mkt.get("key") != "spreads":
                continue
            for outcome in mkt.get("outcomes", []):
                point = float(outcome.get("point", 0))
                price = float(outcome.get("price", 0))
                if outcome.get("name") == home_team:
                    lines.append(point)
                    home_odds_list.append(price)
                else:
                    away_odds_list.append(price)

    def _avg(lst):
        # 用中位數聚合，避免離群莊家造成偏誤 [M1]
        return round(statistics.median(lst), 1) if lst else None

    return {
        "line":       _avg(lines),
        "home_odds":  _avg(home_odds_list),
        "away_odds":  _avg(away_odds_list),
    }

def _extract_totals(item: dict) -> dict:
    over_list, under_list, line_list = [], [], []

    for bk in item.get("bookmakers", []):
        for mkt in bk.get("markets", []):
            if mkt.get("key") != "totals":
                continue
            for outcome in mkt.get("outcomes", []):
                price = float(outcome.get("price", 0))
                point = float(outcome.get("point", 0))
                name  = outcome.get("name", "")
                line_list.append(point)
                if name == "Over":
                    over_list.append(price)
                elif name == "Under":
                    under_list.append(price)

    def _avg(lst):
        # 用中位數聚合，避免離群莊家造成偏誤 [M1]
        return round(statistics.median(lst), 1) if lst else None

    return {
        "line":       _avg(line_list),
        "over_odds":  _avg(over_list),
        "under_odds": _avg(under_list),
    }

#  去 Vig（還原真實勝率）

def remove_vig(home_odds: float, away_odds: float, draw_odds: float = None) -> dict:
    """
    去除抽水，還原真實機率。
    回傳：{home_prob, away_prob, draw_prob, vig_pct, confidence}
    """
    try:
        imp_home = 1 / home_odds if home_odds else 0
        imp_away = 1 / away_odds if away_odds else 0
        imp_draw = 1 / draw_odds if draw_odds else 0

        total = imp_home + imp_away + imp_draw
        if total <= 0:
            raise ValueError("implied prob sum <= 0")

        vig_pct = round((total - 1) * 100, 2)

        true_home = imp_home / total
        true_away = imp_away / total
        true_draw = imp_draw / total if draw_odds else 0.0

        return {
            "home_prob":  round(true_home, 4),
            "away_prob":  round(true_away, 4),
            "draw_prob":  round(true_draw, 4),
            "vig_pct":    vig_pct,
            "confidence": _confidence(home_odds, away_odds),
        }
    except Exception as exc:
        logger.warning("[fetcher] remove_vig 失敗: %s", exc)
        return {"home_prob": 0.5, "away_prob": 0.5, "draw_prob": 0.0,
                "vig_pct": 0.0, "confidence": "低"}

def remove_vig_multi_bookmaker(h2h_data: dict, home_team: str = "", away_team: str = "") -> dict:
    """
    多家莊家去 Vig（用中位數聚合，避免離群莊家偏誤）。
    [C2 修正] 嚴格用 home_team/away_team 隊名比對，不靠 outcomes 順序。
    """
    import statistics
    home_probs, away_probs, draw_probs, vigs = [], [], [], []

    for bk in h2h_data.get("bookmakers", []):
        for mkt in bk.get("markets", []):
            if mkt.get("key") != "h2h":
                continue
            # 用隊名比對抓賠率，順序不影響
            home_odds = away_odds = draw_odds = None
            for o in mkt.get("outcomes", []):
                nm = o.get("name", "")
                pr = float(o.get("price", 0) or 0)
                if nm == home_team:
                    home_odds = pr
                elif nm == away_team:
                    away_odds = pr
                elif nm == "Draw":
                    draw_odds = pr

            if not home_odds or not away_odds:
                continue
            r = remove_vig(home_odds, away_odds, draw_odds)
            home_probs.append(r["home_prob"])
            away_probs.append(r["away_prob"])
            draw_probs.append(r["draw_prob"])
            vigs.append(r["vig_pct"])

    if not home_probs:
        return remove_vig(
            h2h_data.get("home") or 2.0,
            h2h_data.get("away") or 2.0,
            h2h_data.get("draw"),
        )

    med = lambda lst: round(statistics.median(lst), 4) if lst else 0.0
    dispersion = max(home_probs) - min(home_probs) if len(home_probs) > 1 else 0

    return {
        "home_prob":   med(home_probs),
        "away_prob":   med(away_probs),
        "draw_prob":   med(draw_probs),
        "vig_pct":     round(med(vigs), 2),
        "confidence":  _confidence_from_dispersion(dispersion),
    }

def _confidence(home_odds: float, away_odds: float) -> str:
    if not home_odds or not away_odds:
        return "低"
    diff = abs(1/home_odds - 1/away_odds)
    if diff > 0.25:
        return "🟢 高（{}%）".format(round((1 - min(home_odds, away_odds) / max(home_odds, away_odds)) * 100))
    if diff > 0.10:
        return "🟡 中"
    return "🔴 低"

def _confidence_from_dispersion(dispersion: float) -> str:
    if dispersion < 0.03:
        return "🟢 高"
    if dispersion < 0.08:
        return "🟡 中"
    return "🔴 低"

#  nba_api — 近 10 場場均得分 & 標準差

def fetch_nba_team_stats(team_name: str) -> Optional[dict]:
    """
    使用 nba_api 抓取球隊近 10 場場均得分與標準差。
    失敗回傳 None（不 crash）。
    """
    try:
        from nba_api.stats.endpoints import teamgamelog
        from nba_api.stats.static import teams
        import numpy as np

        nba_teams = teams.get_teams()
        matched = [t for t in nba_teams if _fuzzy_match(team_name, t["full_name"])]
        if not matched:
            matched = [t for t in nba_teams if _fuzzy_match(team_name, t["nickname"])]
        if not matched:
            logger.warning("[fetcher] NBA 找不到球隊: %s", team_name)
            return None

        team_id = matched[0]["id"]
        log     = teamgamelog.TeamGameLog(team_id=team_id, season="2024-25")
        df      = log.get_data_frames()[0].head(10)

        scores = df["PTS"].astype(float).tolist()
        return {
            "avg_score": round(float(np.mean(scores)), 1),
            "std_score": round(float(np.std(scores)),  1),
            "games":     len(scores),
        }
    except ImportError:
        logger.warning("[fetcher] nba_api 未安裝，跳過真實數據修正")
        return None
    except Exception as exc:
        logger.warning("[fetcher] nba_api 抓取失敗 team=%s: %s", team_name, exc)
        return None

#  pybaseball — 本季場均得分

def fetch_mlb_team_stats(team_name: str) -> Optional[dict]:
    """
    使用 pybaseball 抓取本季場均得分與標準差。
    失敗回傳 None（不 crash）。
    """
    try:
        from pybaseball import team_game_logs
        import numpy as np

        team_abbr = _mlb_abbr(team_name)
        if not team_abbr:
            logger.warning("[fetcher] MLB 找不到縮寫: %s", team_name)
            return None

        year   = datetime.now(TW).year
        df     = team_game_logs(year, team_abbr)
        scores = df["R"].astype(float).tolist()

        return {
            "avg_score": round(float(np.mean(scores)), 1),
            "std_score": round(float(np.std(scores)),  1),
            "games":     len(scores),
        }
    except ImportError:
        logger.warning("[fetcher] pybaseball 未安裝，跳過真實數據修正")
        return None
    except Exception as exc:
        logger.warning("[fetcher] pybaseball 抓取失敗 team=%s: %s", team_name, exc)
        return None

#  賽後比分抓取

def fetch_game_result(game: dict) -> Optional[dict]:
    """
    嘗試從 Odds API scores endpoint 抓取賽後比分。
    回傳 {home_score, away_score, completed} 或 None。
    """
    if not ODDS_API_KEY:
        return None

    sport_name = game.get("sport", "NBA")
    sport_key  = SPORT_KEYS.get(sport_name.upper(), "basketball_nba")
    game_id    = game.get("game_id", "")

    url    = f"{ODDS_API_BASE}/sports/{sport_key}/scores"
    params = {"apiKey": ODDS_API_KEY, "daysFrom": 3}
    data   = _get(url, params)

    if not data:
        return None

    for item in data:
        if item.get("id") != game_id:
            continue
        completed = item.get("completed", False)
        scores    = item.get("scores") or []
        score_map = {s.get("name"): s.get("score") for s in scores}
        home = score_map.get(game.get("home_team"))
        away = score_map.get(game.get("away_team"))
        if home is not None and away is not None:
            return {
                "home_score": int(home),
                "away_score": int(away),
                "completed":  completed,
            }

    return None

#  輔助函式

def _utc_to_tw(utc_str: str) -> str:
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        dt_tw = dt.astimezone(TW)
        return dt_tw.strftime("%m/%d %H:%M")
    except Exception:
        return utc_str

def _parse_game_time(tw_str: str) -> datetime:
    try:
        year = datetime.now(TW).year
        return datetime.strptime(f"{year}/{tw_str}", "%Y/%m/%d %H:%M").replace(tzinfo=TW)
    except Exception:
        return datetime(1970, 1, 1, tzinfo=TW)

def _league_label(sport_name: str, item: dict) -> str:
    labels = {
        "NBA":      "NBA",
        "MLB":      "MLB",
        "FIFA":     "FIFA 世界盃",
        "WBC":      "WBC 世界經典賽",
        "OLYMPICS": "奧運男籃",
    }
    return labels.get(sport_name.upper(), sport_name)

def _short_name(full: str) -> str:
    parts = full.strip().split()
    return parts[-1] if parts else full

def _fuzzy_match(query: str, target: str) -> bool:
    try:
        from thefuzz import fuzz
        return fuzz.partial_ratio(query.lower(), target.lower()) >= 80
    except ImportError:
        return (query.lower() in target.lower() or target.lower() in query.lower())

_MLB_ABBR = {
    "New York Yankees":"NYY","Los Angeles Dodgers":"LAD","Boston Red Sox":"BOS",
    "Chicago Cubs":"CHC","Houston Astros":"HOU","Atlanta Braves":"ATL",
    "San Francisco Giants":"SF","New York Mets":"NYM","Philadelphia Phillies":"PHI",
    "St. Louis Cardinals":"STL","Chicago White Sox":"CWS","Cleveland Guardians":"CLE",
    "Detroit Tigers":"DET","Kansas City Royals":"KC","Minnesota Twins":"MIN",
    "Toronto Blue Jays":"TOR","Baltimore Orioles":"BAL","Tampa Bay Rays":"TB",
    "Seattle Mariners":"SEA","Texas Rangers":"TEX","Oakland Athletics":"OAK",
    "Los Angeles Angels":"LAA","Colorado Rockies":"COL","Arizona Diamondbacks":"ARI",
    "San Diego Padres":"SD","Miami Marlins":"MIA","Milwaukee Brewers":"MIL",
    "Cincinnati Reds":"CIN","Pittsburgh Pirates":"PIT","Washington Nationals":"WSN",
}

def _mlb_abbr(team_name: str) -> Optional[str]:
    for full, abbr in _MLB_ABBR.items():
        if _fuzzy_match(team_name, full):
            return abbr
    return None
