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

try:
    import worldcup_engine as wce; _HAS_WC_ENGINE = True
except ImportError:
    _HAS_WC_ENGINE = False; logger.warning("worldcup_engine 未找到，世足每日推播停用")

#  賽事快取管理

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
            # Layer 1 過濾：只保留今天的比賽進池
            today_games = [g for g in games
                           if rv.is_in_pool(g.get("game_time_utc",""), g.get("game_time",""))]
            logger.info("[main] 建池完成：%d 場（今日）/ %d 場（總計）",
                        len(today_games), len(games))
            dm.save_weekly_games(today_games)
            games = today_games
        else:
            games = dm.load_weekly_games()
            logger.warning("[main] 抓取失敗，使用快取賽事 (%d 場)", len(games))
    else:
        games = dm.load_weekly_games()

    return games

#  push_today（核心流程）

def push_today(debug: bool = False):
    games = _get_games()
    pushed_count       = 0
    time_window_block  = 0
    silent_hours_block = 0
    duplicate_block    = 0
    model_reject_count = 0
    send_fail_count    = 0
    eligible_games     = 0
    game_reject_log    = []

    for game in games:
        game_id      = game.get('game_id', '')
        game_time_utc = game.get('game_time_utc', '')
        game_time_tw  = game.get('game_time', '')
        hs = game.get('home_short', game.get('home_team', '?'))
        aw = game.get('away_short', game.get('away_team', '?'))

        def _log_reject(reason, extra='', _hs=hs, _aw=aw, _tw=game_time_tw, _gid=game_id):
            label = f'{_hs} vs {_aw} [{_tw}] -> {reason}' + (f' ({extra})' if extra else '')
            game_reject_log.append(label)
            logger.info('[main] reject game_id=%s reason=%s %s', _gid, reason, extra)

        try:
            diff_h_str = ''
            try:
                from result_verifier import _parse_utc_to_tw as _putw
                from datetime import datetime, timezone, timedelta as td
                gdt = _putw(game_time_utc)
                if gdt:
                    diff_h = (gdt - datetime.now(timezone(td(hours=8)))).total_seconds() / 3600
                    diff_h_str = f'diff={diff_h:+.1f}h'
            except Exception:
                pass

            in_window = rv.in_push_window(game_time_utc, game_time_tw)
            if not in_window and not debug:
                time_window_block += 1
                _log_reject('out_of_time_window', diff_h_str)
                continue

            if dm.get_flag(game_id).get('pre_pushed') and not dm.is_post_pushed(game_id):
                [(_push_post_game(i), pushed_count := pushed_count+1) for i in rv.auto_results([game])]
                continue

            if rv.is_silent_hours() and not debug:
                silent_hours_block += 1
                _log_reject('silent_hours', diff_h_str)
                continue

            if dm.is_pushed_today(game_id) and not debug:
                duplicate_block += 1
                _log_reject('already_pushed')
                continue

            eligible_games += 1

            if debug:
                logger.info('[DEBUG] game_id=%s window=%s pushed_today=%s',
                            game_id, in_window, dm.is_pushed_today(game_id))
                if pushed_count >= 2:
                    continue

            game_data = _build_game_data(game)
            result    = pe.run_full_prediction(game_data)

            if result.get('reject', False):
                model_reject_count += 1
                _log_reject('model_reject')
                continue

            ok = _push_pre_game(game, result)
            if ok:
                dm.mark_pushed(game_id, sim_result={**result,
                    'home_team':  game.get('home_team'),
                    'away_team':  game.get('away_team'),
                    'home_short': game.get('home_short'),
                    'away_short': game.get('away_short'),
                })
                dm.append_pre_game_row(_build_csv_row(game, result))
                pushed_count += 1
            else:
                send_fail_count += 1
                _log_reject('send_fail')

        except Exception as exc:
            logger.warning('[main] push_today game_id=%s 失敗: %s', game_id, exc)
            send_fail_count += 1
            if debug:
                nt.push_raw(f'[DEBUG] game_id={game_id} exception: {exc}', silent=True)

    if _HAS_WC_ENGINE:
        try: pushed_count += wce.check_and_push()
        except Exception as exc: logger.warning('[main] worldcup_engine 失敗: %s', exc)

    if pushed_count == 0:
        logger.info('[main] decision total=%d tw=%d silent=%d dup=%d model_reject=%d send_fail=%d eligible=%d',
                    len(games), time_window_block, silent_hours_block,
                    duplicate_block, model_reject_count, send_fail_count, eligible_games)
        for entry in game_reject_log:
            logger.info('[main] reject_detail: %s', entry)
        sep = '\u2501' * 14
        parts = ['\u2699\ufe0f \u63a8\u64ad\u6c7a\u7b56\u5831\u544a', sep, '',
                 '\U0001f4cb \u7e3d\u8cfd\u4e8b\uff1a' + str(len(games)), '',
                 '\u26d4 \u6642\u9593\u7a97\u963b\u64cb\uff1a' + str(time_window_block),
                 '\u26d4 \u975c\u9ed8\u6642\u6bb5\uff1a' + str(silent_hours_block),
                 '\u26d4 \u5df2\u63a8\u64ad\u904e\uff1a' + str(duplicate_block),
                 '\u26d4 \u6a21\u578b\u62d2\u7d55\uff1a' + str(model_reject_count),
                 '\u274c \u63a8\u64ad\u5931\u6557\uff1a' + str(send_fail_count), '',
                 '\u2705 \u53ef\u9032\u5165\u6a21\u578b\uff1a' + str(eligible_games),
                 '\U0001f4e1 \u6700\u7d42\u63a8\u64ad\uff1a0', '',
                 sep, '\u2139\ufe0f \u672c\u8f2a\u7121\u7b26\u5408\u63a8\u64ad\u689d\u4ef6']
        nt.push_raw('\n'.join(parts), silent=True)

    dm.git_commit_state()

def _push_pre_game(game: dict, result: dict) -> bool:
    from notifier import fmt_score_top5, fmt_betting_advice
    try:
        vig     = game.get("vig_result", {})
        h2h     = game.get("h2h_odds", {})
        spread  = game.get("spread", {})
        totals  = game.get("totals", {})

        home_name  = game.get("home_team", "")
        away_name  = game.get("away_team", "")
        home_short = game.get("home_short", "")
        away_short = game.get("away_short", "")

        # 去 Vig 勝率（市場）
        vig_home_pct = round(float(vig.get("home_prob", 0.5)) * 100, 1)
        vig_away_pct = round(float(vig.get("away_prob", 0.5)) * 100, 1)

        # MC 模擬勝率（模型）
        mc_home_pct = float(result.get("home_win_pct", 50))
        mc_away_pct = float(result.get("away_win_pct", 50))

        # 讓分 / 大小分
        spread_val = float(spread.get("line") or 0)
        total_line = float(totals.get("line") or result.get("total_line", 0))
        over_pct   = float(result.get("over_pct", 50))
        kelly_pct  = float(result.get("kelly_pct", 0))

        # Top5 比分（帶隊名）
        top5_raw = result.get("top5_scores", [])
        top5_with_names = [
            (h, a, p, home_name, away_name) for h, a, p in top5_raw
        ]

        # 台灣運彩實戰建議（動態）
        betting_advice = fmt_betting_advice(
            home_name=home_name, away_name=away_name,
            mc_home_pct=mc_home_pct, mc_away_pct=mc_away_pct,
            spread_line=spread_val, total_line=total_line, over_pct=over_pct,
            kelly_pct=kelly_pct, has_value=result.get("has_value",False),
            value_team=result.get("value_team",""),
            home_edge=float(result.get("home_edge",0)),
            away_edge=float(result.get("away_edge",0)),
        )

        g = game; r = result
        data = {
            "date": g.get("game_time",""), "sport_emoji": g.get("sport_emoji","🏆"),
            "league": g.get("league",""), "home": home_name, "away": away_name,
            "home_short": home_short, "away_short": away_short,
            "bookmaker_count": g.get("bookmaker_count",0),
            "vig_pct": round(float(vig.get("vig_pct",0)),1),
            "data_source": _data_source_label(r), "pred_mode": r.get("pred_mode","📐 規則模擬"),
            "confidence": r.get("confidence","🔴 低"),
            "has_value": r.get("has_value",False), "value_team": _resolve_value_team(g,r),
            "value_edge": r.get("value_edge",0),
            "vig_home_pct": vig_home_pct, "vig_away_pct": vig_away_pct,
            "mc_home_pct": mc_home_pct, "mc_away_pct": mc_away_pct,
            "home_edge": r.get("home_edge",0), "away_edge": r.get("away_edge",0),
            "score_top5": fmt_score_top5(top5_with_names),
            "spread_line_val": spread_val, "total_line": total_line, "over_pct": over_pct,
            "home_odds_raw": h2h.get("home"), "away_odds_raw": h2h.get("away"),
            "betting_advice": betting_advice,
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
        now        = datetime.now(TW).strftime("%m/%d")
        hit_count  = verify["hit_count"]
        value_edge = float(sim.get("value_edge") or 0)
        value_team = sim.get("home_team","") if sim.get("value_team") == "home"                      else sim.get("away_team","")

        # EV / Edge / Kelly 模型表現評估
        ev_accuracy  = "✔ 正向" if float(sim.get("home_win_pct",50)) > 50 and verify["moneyline_hit"] else                        "✔ 符合預期" if verify["moneyline_hit"] else "✘ 本場偏差"
        edge_hit     = "✔ 有效" if value_edge > 0 and verify["moneyline_hit"] else                        "✔ 無Value場次" if value_edge <= 0 else "✘ Edge未命中"

        from notifier import _market_bias_label
        v = verify; ve = value_edge
        data = {
            "date": now, "sport_emoji": game.get("sport_emoji","🏆"),
            "home": game.get("home_team",""), "away": game.get("away_team",""),
            "hit": hit_count, "total": v["total"], "hit_pct": v["hit_pct"],
            "moneyline_hit": "✅" if v["moneyline_hit"] else "❌",
            "exact_hit":     "✅" if v["exact_hit"]     else "❌",
            "spread_hit":    "✅" if v["spread_hit"]    else "❌",
            "ou_hit":        "✅" if v["ou_hit"]        else "❌",
            "ev_accuracy": ev_accuracy, "edge_hit": edge_hit,
            "value_team": value_team or game.get("home_team",""),
            "value_edge": f"+{ve:.1f}" if ve >= 0 else f"{ve:.1f}",
            "market_bias": _market_bias_label(ve), "pred_mode": "量化分析",
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

def _confidence_pct(vig_result: dict) -> int:
    diff = abs(float(vig_result.get("home_prob",0.5)) - float(vig_result.get("away_prob",0.5)))
    return round(50+diff*100) if diff>0.25 else (round(40+diff*80) if diff>0.10 else round(30+diff*60))

def _data_source_label(result: dict) -> str:
    mode = result.get("pred_mode", "")
    if "AI" in mode:
        return "AI模型+真實數據+賠率"
    return "規則模型+真實數據+賠率"

#  game_data 組裝（for prediction_engine）

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

#  週報

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

        for hk, arr in [("moneyline_hit",stats["ml"]),("spread_hit",stats["sp"]),("ou_hit",stats["ou"]),("exact_hit",stats["ex"])]:
            arr[0]+=int(verify.get(hk,0)); arr[1]+=1
        hs=game.get('home_short',''); aw=game.get('away_short',''); v=verify
        lines.append(f"{game.get('game_time','')[:5]} {hs} vs {aw}  "
            f"{'✅' if v.get('moneyline_hit') else '❌'}{'✅' if v.get('spread_hit') else '❌'}"
            f"{'✅' if v.get('ou_hit') else '❌'}{'✅' if v.get('exact_hit') else '❌'}")

    def _pct(arr):
        return round(arr[0] / arr[1] * 100, 1) if arr[1] > 0 else 0

    now = datetime.now(TW)
    week_start = (now - timedelta(days=6)).strftime("%m/%d")
    week_end   = now.strftime("%m/%d")

    ml,sp,ou,ex = stats["ml"],stats["sp"],stats["ou"],stats["ex"]
    weekly_data = {
        "week_range": f"{week_start} ～ {week_end}", "total_games": len(games),
        "verified_games": ml[1],
        "moneyline_hit": ml[0], "moneyline_total": ml[1], "moneyline_pct": _pct(ml),
        "spread_hit": sp[0], "spread_total": sp[1], "spread_pct": _pct(sp),
        "ou_hit": ou[0], "ou_total": ou[1], "ou_pct": _pct(ou),
        "exact_hit": ex[0], "exact_total": ex[1], "exact_pct": _pct(ex),
        "game_list": "\n".join(lines) if lines else "本週無已驗證場次",
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

#  世界盃特報

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

#  手動 metrics

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

#  CLI 入口

def main():
    args = sys.argv[1:]
    cmd  = args[0] if args else "push"

    if cmd == "push":
        push_today()

    elif cmd == "fetch":
        _get_games(force_refresh=True); logger.info("[main] fetch 完成")

    elif cmd == "weekly":
        _get_games(force_refresh=True)
        push_weekly_report()

    elif cmd == "wc":
        push_world_cup()

    elif cmd == "results":
        games = _get_games(); post_items = rv.auto_results(games)
        [_push_post_game(i) for i in post_items]
        logger.info("[main] results 完成，共推播 %d 場", len(post_items))

    elif cmd == "verify_all":
        games = _get_games(); post_items = rv.verify_all(games)
        [_push_post_game(i) for i in post_items]
        logger.info("[main] verify_all 完成，共補漏 %d 場", len(post_items))

    elif cmd == "metrics":
        push_metrics_manual()

    elif cmd == "train":
        import train; train.run_training()
    elif cmd == "backtest":
        import backtester; backtester.run_backtest()
    elif cmd == "--debug":
        logger.info("[main] DEBUG 模式"); push_today(debug=True)
    else:
        print("用法: push|fetch|weekly|wc|results|verify_all|metrics|train|backtest|--debug")

if __name__ == "__main__":
    main()
