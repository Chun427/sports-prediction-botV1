"""
backtester.py — 回測引擎
用法：
  python backtester.py                  # 單次回測（預設參數）
  python backtester.py 0.07 0.25        # 指定 threshold / kelly_fraction
  python backtester.py grid             # Grid Search
  python backtester.py wc               # 世界盃 Brier Score
"""
import logging
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BASE_DIR = Path(__file__).parent

DEFAULT_THRESHOLD     = 0.05   # Value edge 門檻
DEFAULT_KELLY_FRAC    = 1.0    # Kelly 全額
INITIAL_BANKROLL      = 1000.0


# ══════════════════════════════════════════════════════════
#  主回測函式
# ══════════════════════════════════════════════════════════

def run_backtest(threshold: float = DEFAULT_THRESHOLD,
                 kelly_fraction: float = DEFAULT_KELLY_FRAC) -> dict:
    """
    對 historical_dataset.csv 執行回測。
    只對 value_edge > threshold 的場次下注。
    """
    try:
        import data_manager as dm
        rows = dm.load_dataset()
    except Exception as exc:
        logger.warning("[backtest] 無法讀取 dataset: %s", exc)
        return _empty_result()

    complete = [
        r for r in rows
        if r.get("actual_home_score") and r.get("actual_away_score")
        and r.get("moneyline_hit") != ""
    ]

    if not complete:
        logger.info("[backtest] 無完整資料，跳過")
        return _empty_result()

    bankroll = INITIAL_BANKROLL
    bets     = []
    peak     = bankroll

    for r in complete:
        try:
            edge = float(r.get("value_edge") or 0)
            if edge < threshold * 100:
                continue

            home_win_prob = float(r.get("home_win_prob") or 0.5)
            home_odds     = float(r.get("home_odds") or 2.0)
            moneyline_hit = int(r.get("moneyline_hit") or 0)

            # Kelly 計算下注金額
            b = home_odds - 1
            q = 1 - home_win_prob
            if b <= 0:
                continue
            kelly = max((b * home_win_prob - q) / b, 0) * kelly_fraction
            stake = bankroll * kelly

            if stake <= 0:
                continue

            profit = stake * b if moneyline_hit else -stake
            bankroll += profit
            bets.append(profit)
            peak = max(peak, bankroll)

        except (ValueError, TypeError):
            continue

    if not bets:
        return _empty_result()

    wins      = sum(1 for p in bets if p > 0)
    total     = len(bets)
    roi       = round((bankroll - INITIAL_BANKROLL) / INITIAL_BANKROLL * 100, 2)
    win_rate  = round(wins / total * 100, 1)
    max_dd    = _max_drawdown(bets, INITIAL_BANKROLL)

    result = {
        "total_bets":    total,
        "wins":          wins,
        "win_rate":      win_rate,
        "roi":           roi,
        "final_bankroll": round(bankroll, 2),
        "max_drawdown":  max_dd,
        "threshold":     threshold,
        "kelly_fraction": kelly_fraction,
    }
    logger.info("[backtest] %s", result)
    return result


def _max_drawdown(profits: list[float], initial: float) -> float:
    """計算最大回撤（%）。"""
    bankroll = initial
    peak     = initial
    max_dd   = 0.0
    for p in profits:
        bankroll += p
        peak      = max(peak, bankroll)
        dd        = (peak - bankroll) / peak * 100
        max_dd    = max(max_dd, dd)
    return round(max_dd, 2)


def _empty_result() -> dict:
    return {
        "total_bets": 0, "wins": 0, "win_rate": 0.0,
        "roi": 0.0, "final_bankroll": INITIAL_BANKROLL,
        "max_drawdown": 0.0,
        "threshold": DEFAULT_THRESHOLD,
        "kelly_fraction": DEFAULT_KELLY_FRAC,
    }


# ══════════════════════════════════════════════════════════
#  Grid Search
# ══════════════════════════════════════════════════════════

def grid_search() -> Optional[dict]:
    """掃描最佳 threshold / kelly_fraction 組合（以 ROI 為目標）。"""
    thresholds     = [0.03, 0.05, 0.07, 0.10]
    kelly_fracs    = [0.25, 0.50, 0.75, 1.0]

    best_roi    = float("-inf")
    best_params = None
    results     = []

    for th in thresholds:
        for kf in kelly_fracs:
            r = run_backtest(threshold=th, kelly_fraction=kf)
            r["threshold"] = th
            r["kelly_fraction"] = kf
            results.append(r)
            if r["roi"] > best_roi and r["total_bets"] >= 5:
                best_roi    = r["roi"]
                best_params = r

    logger.info("[backtest][grid] 最佳參數: %s", best_params)
    _print_grid_table(results)
    return best_params


def _print_grid_table(results: list[dict]):
    print(f"\n{'threshold':>10} {'kelly':>6} {'bets':>5} {'win%':>6} {'roi%':>7} {'maxDD%':>7}")
    print("-" * 50)
    for r in sorted(results, key=lambda x: -x["roi"]):
        print(
            f"{r['threshold']:>10.2f} {r['kelly_fraction']:>6.2f} "
            f"{r['total_bets']:>5} {r['win_rate']:>6.1f} "
            f"{r['roi']:>7.2f} {r['max_drawdown']:>7.2f}"
        )


# ══════════════════════════════════════════════════════════
#  世界盃 Brier Score
# ══════════════════════════════════════════════════════════

def backtest_wc_mode():
    try:
        import tournament_engine as te
        score = te.backtest_wc()
        if score is None:
            print("[backtest] 世界盃資料不足，跳過")
        else:
            print(f"[backtest] 世界盃 Brier Score: {score}")
    except ImportError:
        logger.warning("[backtest] tournament_engine 未找到，跳過 wc 回測")


# ══════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════

if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        r = run_backtest()
        print(r)
    elif args[0] == "grid":
        grid_search()
    elif args[0] == "wc":
        backtest_wc_mode()
    else:
        try:
            th = float(args[0])
            kf = float(args[1]) if len(args) > 1 else DEFAULT_KELLY_FRAC
            r  = run_backtest(th, kf)
            print(r)
        except ValueError:
            print("用法: backtester.py [threshold kelly_fraction | grid | wc]")

    sys.exit(0)
