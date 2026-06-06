"""
prediction_engine.py — 純預測引擎
負責：去 Vig 真實勝率 → AI/規則混合預測 → Monte Carlo 100 萬次 →
      Value Betting 偵測 → Kelly Criterion。
不包含任何 API / data fetch / push 邏輯。
"""
import logging
import os
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

BASE_DIR    = Path(__file__).parent
MODEL_A_PATH = BASE_DIR / "model_A.pkl"   # 主隊得分 XGBoost
MODEL_B_PATH = BASE_DIR / "model_B.pkl"   # 客隊得分 XGBoost

MC_SIMULATIONS   = 1_000_000
CORRELATION      = -0.12        # 主客隊得分負相關
AI_DEVIATION_CAP = 0.50         # AI 預測偏離 >50% 自動降回規則
VALUE_THRESHOLD  = 0.05         # Value Bet 門檻：5%
MIN_KELLY_EDGE   = 0.01         # Kelly 最小 edge


# ══════════════════════════════════════════════════════════
#  AI 模型載入（lazy，缺失靜默）
# ══════════════════════════════════════════════════════════

_model_A = None
_model_B = None
_models_loaded = False


def _load_models():
    global _model_A, _model_B, _models_loaded
    if _models_loaded:
        return
    _models_loaded = True
    try:
        import joblib
        if MODEL_A_PATH.exists():
            _model_A = joblib.load(MODEL_A_PATH)
            logger.info("[engine] model_A 載入成功")
        if MODEL_B_PATH.exists():
            _model_B = joblib.load(MODEL_B_PATH)
            logger.info("[engine] model_B 載入成功")
    except ImportError:
        logger.warning("[engine] joblib 未安裝，AI 模型停用")
    except Exception as exc:
        logger.warning("[engine] 模型載入失敗（降回規則模式）: %s", exc)
        _model_A = _model_B = None


def _has_models() -> bool:
    _load_models()
    return _model_A is not None and _model_B is not None


# ══════════════════════════════════════════════════════════
#  特徵向量建構（供 AI 推理用）
# ══════════════════════════════════════════════════════════

def _build_features(game_data: dict) -> Optional[list]:
    """
    從 game_data 建構 13 維特徵向量：
    [home_odds, away_odds, spread_line, total_line,
     home_win_prob, away_win_prob,
     home_avg_score, away_avg_score, home_std, away_std,
     bookmaker_count, vig_pct, value_edge]
    """
    try:
        h2h     = game_data.get("h2h_odds", {})
        spread  = game_data.get("spread", {})
        totals  = game_data.get("totals", {})
        vig_res = game_data.get("vig_result", {})
        stats   = game_data.get("team_stats", {})

        features = [
            float(h2h.get("home") or 2.0),
            float(h2h.get("away") or 2.0),
            float(spread.get("line") or 0.0),
            float(totals.get("line") or 220.0),
            float(vig_res.get("home_prob") or 0.5),
            float(vig_res.get("away_prob") or 0.5),
            float(stats.get("home_avg") or 110.0),
            float(stats.get("away_avg") or 110.0),
            float(stats.get("home_std") or 10.0),
            float(stats.get("away_std") or 10.0),
            float(game_data.get("bookmaker_count") or 1),
            float(vig_res.get("vig_pct") or 0.0),
            float(game_data.get("value_edge") or 0.0),
        ]
        return features
    except Exception as exc:
        logger.warning("[engine] _build_features 失敗: %s", exc)
        return None


# ══════════════════════════════════════════════════════════
#  預測得分均值（AI or 規則）
# ══════════════════════════════════════════════════════════

def _predict_means(game_data: dict) -> tuple[float, float, str]:
    """
    回傳 (home_mean, away_mean, pred_mode)
    pred_mode: '🤖 AI預測' or '📐 規則模擬'
    """
    rule_home, rule_away = _rule_based_means(game_data)

    if not _has_models():
        return rule_home, rule_away, "📐 規則模擬"

    features = _build_features(game_data)
    if features is None:
        return rule_home, rule_away, "📐 規則模擬"

    try:
        import numpy as np
        X = np.array(features).reshape(1, -1)
        ai_home = float(_model_A.predict(X)[0])
        ai_away = float(_model_B.predict(X)[0])

        # 安全閥：AI 偏離規則 >50% 自動降回
        dev_home = abs(ai_home - rule_home) / (rule_home + 1e-9)
        dev_away = abs(ai_away - rule_away) / (rule_away + 1e-9)
        if dev_home > AI_DEVIATION_CAP or dev_away > AI_DEVIATION_CAP:
            logger.warning(
                "[engine] AI 偏離過大 (%.1f%% / %.1f%%)，降回規則模式",
                dev_home * 100, dev_away * 100,
            )
            return rule_home, rule_away, "📐 規則模擬"

        # AI 與規則的混合均值（各 50%）
        blend_home = (ai_home + rule_home) / 2
        blend_away = (ai_away + rule_away) / 2
        return blend_home, blend_away, "🤖 AI預測"

    except Exception as exc:
        logger.warning("[engine] AI 預測失敗，降回規則: %s", exc)
        return rule_home, rule_away, "📐 規則模擬"


def _rule_based_means(game_data: dict) -> tuple[float, float]:
    """
    規則公式：以去 Vig 勝率修正場均得分。
    sport 差異：NBA ~110分，MLB ~4.5分，足球 ~1.4球
    """
    sport      = game_data.get("sport", "NBA").upper()
    vig_result = game_data.get("vig_result", {})
    stats      = game_data.get("team_stats", {})
    totals     = game_data.get("totals", {})

    home_prob = float(vig_result.get("home_prob") or 0.5)
    away_prob = float(vig_result.get("away_prob") or 0.5)

    # 大小分中線作為總分錨點
    total_line = float(totals.get("line") or 0)

    if sport == "NBA":
        base      = total_line if total_line > 0 else 220.0
        home_avg  = float(stats.get("home_avg") or base * 0.5)
        away_avg  = float(stats.get("away_avg") or base * 0.5)
        factor    = 0.15
    elif sport == "MLB":
        base      = total_line if total_line > 0 else 9.0
        home_avg  = float(stats.get("home_avg") or base * 0.5)
        away_avg  = float(stats.get("away_avg") or base * 0.5)
        factor    = 0.20
    elif sport in ("FIFA", "OLYMPICS"):
        base      = total_line if total_line > 0 else 2.5
        home_avg  = float(stats.get("home_avg") or 1.35)
        away_avg  = float(stats.get("away_avg") or 1.10)
        factor    = 0.25
    else:
        base      = total_line if total_line > 0 else 220.0
        home_avg  = float(stats.get("home_avg") or base * 0.5)
        away_avg  = float(stats.get("away_avg") or base * 0.5)
        factor    = 0.15

    # 用勝率偏移修正均值
    home_mean = home_avg * (1 + factor * (home_prob - 0.5))
    away_mean = away_avg * (1 + factor * (away_prob - 0.5))
    return round(home_mean, 2), round(away_mean, 2)


# ══════════════════════════════════════════════════════════
#  Monte Carlo 主函式
# ══════════════════════════════════════════════════════════

def run_monte_carlo(game_data: dict) -> dict:
    """
    執行 100 萬次 Monte Carlo 模擬。
    回傳完整結果 dict，所有欄位都有預設值（fail-safe）。
    """
    result = _default_result()

    try:
        sport = game_data.get("sport", "NBA").upper()
        stats = game_data.get("team_stats", {})

        home_mean, away_mean, pred_mode = _predict_means(game_data)

        home_std = float(stats.get("home_std") or _default_std(sport))
        away_std = float(stats.get("away_std") or _default_std(sport))

        # 相關矩陣（主客得分 -0.12 負相關）
        cov = _build_cov(home_std, away_std, CORRELATION)
        means = [home_mean, away_mean]

        # 批次模擬
        samples = np.random.multivariate_normal(means, cov, size=MC_SIMULATIONS)
        home_scores = np.round(np.clip(samples[:, 0], 0, None)).astype(int)
        away_scores = np.round(np.clip(samples[:, 1], 0, None)).astype(int)

        n = MC_SIMULATIONS
        home_wins  = int(np.sum(home_scores > away_scores))
        away_wins  = int(np.sum(away_scores > home_scores))
        draws      = int(np.sum(home_scores == away_scores))

        if sport in ("FIFA",):
            # FIFA：平局是有效結果，保留三方機率，三者加總 = 100%
            home_win_pct = round(home_wins / n * 100, 1)
            away_win_pct = round(away_wins / n * 100, 1)
            draw_pct     = round(draws / n * 100, 1)
        else:
            # NBA / MLB：平局是 MC 數值產物，非真實事件
            # 重新正規化：把 draw 移除，home + away = 100%
            decisive = home_wins + away_wins
            if decisive > 0:
                home_win_pct = round(home_wins / decisive * 100, 1)
                away_win_pct = round(100 - home_win_pct, 1)
            else:
                home_win_pct = away_win_pct = 50.0
            draw_pct = None

        # 大小分
        total_line = float(game_data.get("totals", {}).get("line") or (home_mean + away_mean))
        totals_sim = home_scores + away_scores
        over_pct   = round(float(np.sum(totals_sim > total_line)) / n * 100, 1)
        under_pct  = round(100 - over_pct, 1)

        # 讓分覆蓋
        spread_line = float(game_data.get("spread", {}).get("line") or 0)
        home_cover  = round(float(np.sum(home_scores + spread_line > away_scores)) / n * 100, 1)

        # Top 5 比分
        score_pairs  = list(zip(home_scores.tolist(), away_scores.tolist()))
        score_counts: dict[tuple, int] = {}
        for h, a in score_pairs:
            k = (h, a)
            score_counts[k] = score_counts.get(k, 0) + 1
        top5 = sorted(score_counts.items(), key=lambda x: -x[1])[:5]
        top5_fmt = [(h, a, round(cnt / n * 100, 1)) for (h, a), cnt in top5]

        result.update({
            "home_win_pct":  home_win_pct,
            "away_win_pct":  away_win_pct,
            "draw_pct":      draw_pct if sport in ("FIFA",) else None,
            "over_pct":      over_pct,
            "under_pct":     under_pct,
            "home_cover_pct": home_cover,
            "spread_line":   spread_line,
            "total_line":    total_line,
            "top5_scores":   top5_fmt,
            "home_mean":     round(home_mean, 1),
            "away_mean":     round(away_mean, 1),
            "pred_mode":     pred_mode,
            "error":         None,
        })

        logger.info(
            "[engine] MC 完成 home_win=%.1f%% away_win=%.1f%% mode=%s",
            home_win_pct, away_win_pct, pred_mode,
        )

    except Exception as exc:
        logger.error("[engine] Monte Carlo 失敗: %s", exc)
        result["error"] = str(exc)

    return result


def _default_std(sport: str) -> float:
    defaults = {"NBA": 12.0, "MLB": 2.5, "FIFA": 1.0, "OLYMPICS": 10.0}
    return defaults.get(sport, 12.0)


def _build_cov(std_a: float, std_b: float, corr: float) -> list:
    cov_ab = corr * std_a * std_b
    return [[std_a**2, cov_ab], [cov_ab, std_b**2]]


def _default_result() -> dict:
    return {
        "home_win_pct":   50.0,
        "away_win_pct":   50.0,
        "draw_pct":       None,
        "over_pct":       50.0,
        "under_pct":      50.0,
        "home_cover_pct": 50.0,
        "spread_line":    0.0,
        "total_line":     220.0,
        "top5_scores":    [],
        "home_mean":      110.0,
        "away_mean":      110.0,
        "pred_mode":      "📐 規則模擬",
        "error":          "初始化",
    }


# ══════════════════════════════════════════════════════════
#  Value Betting
# ══════════════════════════════════════════════════════════

def detect_value_bet(
    model_home_prob: float,
    model_away_prob: float,
    market_home_prob: float,
    market_away_prob: float,
) -> dict:
    """
    比較模型勝率 vs 市場隱含勝率。
    差距 > VALUE_THRESHOLD 標記為 Value Bet。
    回傳 {has_value, value_team, value_edge, home_edge, away_edge}
    """
    home_edge = round((model_home_prob - market_home_prob) * 100, 1)
    away_edge = round((model_away_prob - market_away_prob) * 100, 1)

    best_edge  = max(home_edge, away_edge)
    has_value  = best_edge > VALUE_THRESHOLD * 100
    value_team = ""
    if has_value:
        value_team = "home" if home_edge >= away_edge else "away"

    return {
        "has_value":  has_value,
        "value_team": value_team,
        "value_edge": round(best_edge, 1),
        "home_edge":  home_edge,
        "away_edge":  away_edge,
    }


# ══════════════════════════════════════════════════════════
#  Kelly Criterion
# ══════════════════════════════════════════════════════════

def kelly_criterion(
    model_prob: float,
    odds_decimal: float,
    fraction: float = 1.0,
) -> float:
    """
    Kelly 公式：f = (b*p - q) / b
    b = odds - 1, p = model_prob, q = 1 - p
    fraction = Kelly 分數（建議 0.25 ~ 1.0）
    回傳建議下注資金百分比（0.0 ~ 1.0 scale，已×100）
    """
    try:
        b = odds_decimal - 1
        if b <= 0:
            return 0.0
        p = min(max(model_prob, 0.01), 0.99)
        q = 1 - p
        kelly = (b * p - q) / b
        kelly = max(kelly, 0.0)  # 不可為負
        return round(kelly * fraction * 100, 1)
    except Exception as exc:
        logger.warning("[engine] Kelly 計算失敗: %s", exc)
        return 0.0


# ══════════════════════════════════════════════════════════
#  整合：run_full_prediction
# ══════════════════════════════════════════════════════════

def run_full_prediction(game_data: dict) -> dict:
    """
    完整預測流程：
    1. Monte Carlo
    2. Value Betting
    3. Kelly Criterion
    回傳所有結果（含 notifier 需要的所有欄位）。
    fail-safe：任何步驟失敗都有預設值。
    """
    mc = run_monte_carlo(game_data)

    vig_result   = game_data.get("vig_result", {})
    market_home  = float(vig_result.get("home_prob") or 0.5)
    market_away  = float(vig_result.get("away_prob") or 0.5)

    model_home   = mc["home_win_pct"] / 100
    model_away   = mc["away_win_pct"] / 100

    vb = detect_value_bet(model_home, model_away, market_home, market_away)

    # Kelly：只在有 Value 時計算，無 Value → 0（避免 silent wrong output [C3]）
    h2h = game_data.get("h2h_odds", {})
    if vb["has_value"] and vb["value_team"] == "home":
        kelly_pct = kelly_criterion(model_home, float(h2h.get("home") or 2.0))
    elif vb["has_value"] and vb["value_team"] == "away":
        kelly_pct = kelly_criterion(model_away, float(h2h.get("away") or 2.0))
    else:
        kelly_pct = 0.0   # 無 Value Bet，不建議下注

    # 信心指數
    confidence = game_data.get("vig_result", {}).get("confidence", "🔴 低")

    # spread 文字
    spread_line = mc["spread_line"]
    if spread_line and spread_line != 0:
        home_team = game_data.get("home_short", "主")
        if spread_line > 0:
            spread_label = f"{home_team} -{spread_line}"
        else:
            spread_label = f"{home_team} +{abs(spread_line)}"
        spread_cover_pct = mc["home_cover_pct"]
        spread_txt = f"{spread_label}｜覆蓋率 {spread_cover_pct}%"
    else:
        spread_txt = "盤口暫無資料"

    mc.update({
        **vb,
        "kelly_pct":     kelly_pct,
        "confidence":    confidence,
        "spread_line_txt": spread_txt,
        "market_home_prob": round(market_home * 100, 1),
        "market_away_prob": round(market_away * 100, 1),
    })

    return mc
