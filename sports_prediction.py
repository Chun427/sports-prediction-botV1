"""
sports_prediction.py — 主入口
指令：push / fetch / weekly / wc / results / verify_all / metrics / train / backtest / --debug
GitHub Actions 使用：python sports_prediction.py push
"""
import logging
import sys
from datetime import datetime, timezone, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

TW = timezone(timedelta(hours=8))

# ── fail-safe 模組導入 ──────────────────────────────────────
try:
    import data_manager as dm
except ImportError:
    logger.error("data_manager 未找到，系統無法啟動")
    sys.exit(1)

try:
    import data_fetcher as df
except ImportError:
    logger.error("data_fetcher 未找到，系統無法啟動")
    sys.exit(1)

try:
    import prediction_engine as pe
except ImportError:
    logger.error("prediction_engine 未找到，系統無法啟動")
    sys.exit(1)

try:
    import notifier as nt
except ImportError:
    logger.error("notifier 未找到，系統無法啟動")
    sys.exit(1)

try:
    import result_verifier as rv
except ImportError:
    logger.error("result_verifier 未找到，系統無法啟動")
    sys.exit(1)

# 選用模組（缺失時靜默跳過）
try:
    import tournament_engine as te
    _HAS_TOURNAMENT = True
except ImportError:
    _HAS_TOURNAMENT = False
    logger.warning("tournament_engine 未找到，世界盃功能停用")

# ══════════════════════════════════════════════════════════
#  賽事快取管理
# ══════════════════════════════════════════════════════════

def _get_games(force_refresh: bool = False) -> list[dict]:
    """
    取得賽事列表。
    快取過期 / 強制刷新 / 所有賽事已結束 → 重新抓取。
    """
    refresh = (
        force_refresh
        or dm.weekly_games_expired()
        or dm.all_games_finished(dm.load_weekly_games())
    )

    if refresh:
        logger.info("[main] 抓取最新賽事...")
        games = df.fetch_all_sports_games()
        if games:
            dm.save_weekly_games(games)
        else:
            games = dm.load_weekly_games()
            logger.warning("[main] 抓取失敗，使用快取賽事 (%d 場)", len(games))
    else:
        games = dm.load_weekly_games()

    return games

# ══════════════════════════════════════════════════════════
#  push_today（核心流程）
# ══════════════════════════════════════════════════════════

def push_today(debug: bool = False):
    """
    每 30 分鐘執行一次的主流程：
    1. 取得賽事
    2. 判斷推播視窗
    3. 賽前預測推播（每天每場一次）
    4. 賽後驗證推播
    5. 無推播條件 → standby
    """
    games = _get_games()
    pushed_count = 0

    for game in games:
        game_id       = game.get("game_id", "")
        game_time_utc = game.get("game_time_utc", "")

        try:
            # ── 推播視窗判斷 ──────────────────────────────
            in_window = rv.in_push_window(game_time_utc)
            if not in_window and not debug:
                continue

            # ── 賽後驗證優先 ──────────────────────────────
            if dm.get_flag(game_id).get("pre_pushed") and not dm.is_post_pushed(game_id):
                post_items = rv.auto_results([game])
                for item in post_items:
                    _push_post_game(item)
                    pushed_count += 1
                continue

            # ── 賽前推播 ──────────────────────────────────
            if rv.is_silent_hours() and not debug:
                logger.info("[main] 靜音時段，跳過推播 game_id=%s", game_id)
                continue

            if dm.is_pushed_today(game_id) and not debug:
                continue   # 今日已推，跳過

            # 組裝 game_data
            game_data = _build_game_data(game)

            if debug:
                logger.info("[DEBUG] game_id=%s window=%s pushed_today=%s",
                            game_id, in_window, dm.is_pushed_today(game_id))
                if pushed_count >= 2:
                    continue   # debug 模式只強制模擬前兩場

            # 執行預測
            result = pe.run_full_prediction(game_data)

            # 推播
            ok = _push_pre_game(game, result)
            if ok:
                dm.mark_pushed(game_id, sim_result={**result,
                    "home_team":  game.get("home_team"),
                    "away_team":  game.get("away_team"),
                    "home_short": game.get("home_short"),
                    "away_short": game.get("away_short"),
                })
                dm.append_pre_game_row(_build_csv_row(game, result))
                pushed_count += 1

        except Exception as exc:
            logger.warning("[main] push_today game_id=%s 失敗: %s", game_id, exc)

    # ── 無推播條件 → standby ─────────────────────────────
    if pushed_count == 0:
        nt.push_standby(
            game_count=len(games),
            reason="本輪無符合推播視窗之賽事",
            silent=True,
        )

    # ── 提交狀態 ─────────────────────────────────────────
    dm.git_commit_state()

# ══════════════════════════════════════════════════════════
#  推播輔助（格式組裝 → notifier）
# ══════════════════════════════════════════════════════════

def _push_pre_game(game: dict, result: dict) -> bool:
    from notifier import fmt_score_top5
    try:
        data = {
            "date":            game.get("game_time", ""),
            "sport_emoji":     game.get("sport_emoji", "🏆"),
            "league":          game.get("league", ""),
            "home":            game.get("home_team", ""),
            "away":            game.get("away_team", ""),
            "home_short":      game.get("home_short", ""),
            "away_short":      game.get("away_short", ""),
            "bookmaker_count": game.get("bookmaker_count", 0),
            "vig_pct":         game.get("vig_result", {}).get("vig_pct", 0),
            "data_source":     _data_source_label(result),
            "pred_mode":       result.get("pred_mode", "📐 規則模擬"),
            "confidence":      result.get("confidence", "🔴 低"),
            "has_value":       result.get("has_value", False),
            "value_team":      _resolve_value_team(game, result),
            "value_edge":      result.get("value_edge", 0),
            "home_win_pct":    result.get("home_win_pct", 50),
            "away_win_pct":    result.get("away_win_pct", 50),
            "draw_pct":        result.get("draw_pct"),
            "score_top5":      fmt_score_top5(result.get("top5_scores", [])),
            "spread_line":     result.get("spread_line_txt", "暫無"),
            "total_line":      result.get("total_line", 0),
            "over_pct":        result.get("over_pct", 50),
            "under_pct":       result.get("under_pct", 50),
            "home_edge":       result.get("home_edge", 0),
            "away_edge":       result.get("away_edge", 0),
            "kelly_pct":       result.get("kelly_pct", 0),
        }
        return nt.push_pre_game(data)
    except Exception as exc:
        logger.warning("[main] _push_pre_game 失敗: %s", exc)
        return False

def _push_post_game(item: dict) -> bool:
    game   = item["game"]
    verify = item["verify"]
    sim    = item["sim"]
    try:
        now = datetime.now(TW).strftime("%m/%d")
        data = {
            "date":             now,
            "hit":              verify["hit_count"],
            "total":            verify["total"],
            "hit_pct":          verify["hit_pct"],
            "pred_mode":        sim.get("pred_mode", "📐 規則模擬"),
            "moneyline_result": verify["moneyline_result"],
            "moneyline_hit":    "✅" if verify["moneyline_hit"] else "❌",
            "exact_score":      verify["exact_score"],
            "exact_hit":        "✅" if verify["exact_hit"]     else "❌",
            "spread_result":    verify["spread_result"],
            "spread_hit":       "✅" if verify["spread_hit"]    else "❌",
            "ou_result":        verify["ou_result"],
            "ou_hit":           "✅" if verify["ou_hit"]        else "❌",
        }
        return nt.push_post_game(data)
    except Exception as exc:
        logger.warning("[main] _push_post_game 失敗: %s", exc)
        return False

def _resolve_value_team(game: dict, result: dict) -> str:
    vt = result.get("value_team", "")
    if vt == "home":
        return game.get("home_short", game.get("home_team", ""))
    if vt == "away":
        return game.get("away_short", game.get("away_team", ""))
    return ""

def _data_source_label(result: dict) -> str:
    mode = result.get("pred_mode", "")
    if "AI" in mode:
        return "AI模型+真實數據+賠率"
    return "規則模型+真實數據+賠率"

# ══════════════════════════════════════════════════════════
#  game_data 組裝（for prediction_engine）
# ══════════════════════════════════════════════════════════

def _build_game_data(game: dict) -> dict:
    """
    組裝 prediction_engine 所需的 game_data dict。
    包含：去 Vig 結果、真實球隊統計（nba_api / pybaseball）。
    """
    sport   = game.get("sport", "NBA").upper()
    h2h     = game.get("h2h_odds", {})

    # 去 Vig
    vig_result = df.remove_vig_multi_bookmaker(h2h)

    # 真實數據修正
    home_stat = _get_team_stats(game.get("home_team", ""), sport)
    away_stat = _get_team_stats(game.get("away_team", ""), sport)

    team_stats = {
        "home_avg": home_stat.get("avg_score") if home_stat else None,
        "away_avg": away_stat.get("avg_score") if away_stat else None,
        "home_std": home_stat.get("std_score") if home_stat else None,
        "away_std": away_stat.get("std_score") if away_stat else None,
    }

    return {
        **game,
        "vig_result":  vig_result,
        "team_stats":  team_stats,
    }

def _get_team_stats(team_name: str, sport: str) -> dict | None:
    """
    取得球隊統計（優先用快取）。
    """
    cached = dm.get_team_stat(team_name)
    if cached:
        return cached

    stat = None
    if sport == "NBA":
        stat = df.fetch_nba_team_stats(team_name)
    elif sport == "MLB":
        stat = df.fetch_mlb_team_stats(team_name)

    if stat:
        # 更新快取
        all_stats = dm.load_team_stats().get("stats", {})
        all_stats[team_name] = stat
        dm.save_team_stats(all_stats)

    return stat

def _build_csv_row(game: dict, result: dict) -> dict:
    h = game.get("h2h_odds", {}); sp = game.get("spread", {})
    to = game.get("totals", {}); vi = game.get("vig_result", {})
    ts = result.get("team_stats", {})
    return {
        "game_id": game.get("game_id",""), "date": datetime.now(TW).strftime("%Y-%m-%d"),
        "sport": game.get("sport",""), "league": game.get("league",""),
        "home_team": game.get("home_team",""), "away_team": game.get("away_team",""),
        "home_odds": h.get("home",""), "away_odds": h.get("away",""),
        "draw_odds": h.get("draw",""), "spread": sp.get("line",""),
        "total_line": to.get("line",""),
        "home_win_prob": result.get("home_win_pct",50)/100,
        "away_win_prob": result.get("away_win_pct",50)/100,
        "home_avg_score": ts.get("home_avg",""), "away_avg_score": ts.get("away_avg",""),
        "home_std": ts.get("home_std",""), "away_std": ts.get("away_std",""),
        "bookmaker_count": game.get("bookmaker_count",""), "vig_pct": vi.get("vig_pct",""),
        "value_edge": result.get("value_edge",""), "kelly_fraction": result.get("kelly_pct",""),
        "pred_mode": result.get("pred_mode",""),
    }

# ══════════════════════════════════════════════════════════
#  週報
# ══════════════════════════════════════════════════════════

def push_weekly_report():
    games = _get_games()

    # 統計本週命中
    stats = {"ml": [0, 0], "sp": [0, 0], "ou": [0, 0], "ex": [0, 0]}
    lines = []

    for game in games:
        game_id = game.get("game_id", "")
        flag    = dm.get_flag(game_id)
        sim     = dm.get_sim_result(game_id)
        if not flag.get("post_pushed") or not sim:
            continue

        verify = flag.get("last_verify", {})
        if not verify:
            continue

        for k, (hit_key, arr) in zip(
            ["ml", "sp", "ou", "ex"],
            [("moneyline_hit", stats["ml"]), ("spread_hit", stats["sp"]),
             ("ou_hit", stats["ou"]), ("exact_hit", stats["ex"])],
        ):
            hit = int(verify.get(hit_key, 0))
            arr[0] += hit
            arr[1] += 1

        lines.append(
            f"{game.get('game_time','')[:5]} {game.get('home_short','')} vs "
            f"{game.get('away_short','')}  "
            f"{'✅' if verify.get('moneyline_hit') else '❌'}"
            f"{'✅' if verify.get('spread_hit') else '❌'}"
            f"{'✅' if verify.get('ou_hit') else '❌'}"
            f"{'✅' if verify.get('exact_hit') else '❌'}"
        )

    def _pct(arr):
        return round(arr[0] / arr[1] * 100, 1) if arr[1] > 0 else 0

    now = datetime.now(TW)
    week_start = (now - timedelta(days=6)).strftime("%m/%d")
    week_end   = now.strftime("%m/%d")

    weekly_data = {
        "week_range":      f"{week_start} ～ {week_end}",
        "total_games":     len(games),
        "verified_games":  stats["ml"][1],
        "moneyline_hit":   stats["ml"][0], "moneyline_total": stats["ml"][1],
        "moneyline_pct":   _pct(stats["ml"]),
        "spread_hit":      stats["sp"][0], "spread_total": stats["sp"][1],
        "spread_pct":      _pct(stats["sp"]),
        "ou_hit":          stats["ou"][0], "ou_total": stats["ou"][1],
        "ou_pct":          _pct(stats["ou"]),
        "exact_hit":       stats["ex"][0], "exact_total": stats["ex"][1],
        "exact_pct":       _pct(stats["ex"]),
        "game_list":       "\n".join(lines) if lines else "本週無已驗證場次",
    }
    nt.push_weekly(weekly_data)

    # 自學指標
    metrics = dm.compute_and_save_metrics()
    if metrics:
        from notifier import fmt_sport_breakdown
        nt.push_metrics({
            "sample_count":     metrics["sample_count"],
            "moneyline_acc":    metrics["moneyline_acc"],
            "ou_acc":           metrics["ou_acc"],
            "spread_acc":       metrics["spread_acc"],
            "kelly_acc":        metrics["kelly_acc"],
            "edge_bias":        metrics["edge_bias"],
            "sport_breakdown":  fmt_sport_breakdown(metrics.get("sport_breakdown", {})),
        })

    dm.git_commit_state()

# ══════════════════════════════════════════════════════════
#  世界盃特報
# ══════════════════════════════════════════════════════════

def push_world_cup():
    if not _HAS_TOURNAMENT:
        logger.warning("[main] tournament_engine 不存在，跳過世界盃特報")
        return

    try:
        awards = te.compute_award_probabilities()

        def _fmt(lst):
            return "\n".join(f"  {i+1}. {t}  {p}%" for i, (t, p) in enumerate(lst))

        nt.push_world_cup({
            "champion_list":     _fmt(awards.get("champion", [])),
            "golden_boot_list":  _fmt(awards.get("golden_boot", [])),
            "golden_ball_list":  _fmt(awards.get("golden_ball", [])),
            "golden_glove_list": _fmt(awards.get("golden_glove", [])),
            "calc_mode":         awards.get("calc_mode", "動態引擎"),
        })
    except Exception as exc:
        logger.warning("[main] push_world_cup 失敗: %s", exc)

# ══════════════════════════════════════════════════════════
#  手動 metrics
# ══════════════════════════════════════════════════════════

def push_metrics_manual():
    metrics = dm.compute_and_save_metrics()
    if not metrics:
        nt.push_raw("📈 指標資料不足，無法計算", silent=True)
        return
    from notifier import fmt_sport_breakdown
    nt.push_metrics({
        "sample_count":    metrics["sample_count"],
        "moneyline_acc":   metrics["moneyline_acc"],
        "ou_acc":          metrics["ou_acc"],
        "spread_acc":      metrics["spread_acc"],
        "kelly_acc":       metrics["kelly_acc"],
        "edge_bias":       metrics["edge_bias"],
        "sport_breakdown": fmt_sport_breakdown(metrics.get("sport_breakdown", {})),
    })

# ══════════════════════════════════════════════════════════
#  CLI 入口
# ══════════════════════════════════════════════════════════

def main():
    args = sys.argv[1:]
    cmd  = args[0] if args else "push"

    if cmd == "push":
        push_today()

    elif cmd == "fetch":
        _get_games(force_refresh=True)
        logger.info("[main] fetch 完成")

    elif cmd == "weekly":
        _get_games(force_refresh=True)
        push_weekly_report()

    elif cmd == "wc":
        push_world_cup()

    elif cmd == "results":
        games      = _get_games()
        post_items = rv.auto_results(games)
        for item in post_items:
            _push_post_game(item)
        logger.info("[main] results 完成，共推播 %d 場", len(post_items))

    elif cmd == "verify_all":
        games      = _get_games()
        post_items = rv.verify_all(games)
        for item in post_items:
            _push_post_game(item)
        logger.info("[main] verify_all 完成，共補漏 %d 場", len(post_items))

    elif cmd == "metrics":
        push_metrics_manual()

    elif cmd == "train":
        import train
        train.run_training()

    elif cmd == "backtest":
        import backtester
        backtester.run_backtest()

    elif cmd == "--debug":
        logger.info("[main] DEBUG 模式啟動")
        push_today(debug=True)

    else:
        logger.warning("[main] 未知指令: %s", cmd)
        print("用法: python sports_prediction.py [push|fetch|weekly|wc|results|verify_all|metrics|train|backtest|--debug]")

if __name__ == "__main__":
    main()
