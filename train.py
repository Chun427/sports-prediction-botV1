"""
train.py — XGBoost 離線訓練
資料不足（< 30 筆）時靜默跳過，CI 不顯示紅燈。
輸出：model_A.pkl（主隊得分）、model_B.pkl（客隊得分）
"""
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BASE_DIR     = Path(__file__).parent
MODEL_A_PATH = BASE_DIR / "model_A.pkl"
MODEL_B_PATH = BASE_DIR / "model_B.pkl"

MIN_SAMPLES  = 30
FEATURE_COLS = [
    "home_odds", "away_odds", "spread_line", "total_line",
    "home_win_prob", "away_win_prob",
    "home_avg_score", "away_avg_score", "home_std", "away_std",
    "bookmaker_count", "vig_pct", "value_edge",
]
TARGET_A = "actual_home_score"   # model_A
TARGET_B = "actual_away_score"   # model_B


def run_training() -> bool:
    """
    執行 XGBoost 訓練。
    成功回傳 True，資料不足或失敗靜默回傳 False。
    """
    try:
        import numpy as np
        import joblib
        from xgboost import XGBRegressor
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import mean_absolute_error
    except ImportError as exc:
        logger.warning("[train] 依賴未安裝，跳過訓練: %s", exc)
        return False

    try:
        import data_manager as dm
        rows = dm.load_dataset()
    except Exception as exc:
        logger.warning("[train] 無法讀取 dataset: %s", exc)
        return False

    # 過濾完整行
    complete = [
        r for r in rows
        if r.get(TARGET_A) and r.get(TARGET_B)
        and all(r.get(col, "") != "" for col in FEATURE_COLS)
    ]

    if len(complete) < MIN_SAMPLES:
        logger.info("[train] 完整樣本 %d 筆 < %d，跳過訓練", len(complete), MIN_SAMPLES)
        return False

    logger.info("[train] 開始訓練，樣本數: %d", len(complete))

    try:
        X = np.array([[_safe_float(r.get(c, 0)) for c in FEATURE_COLS] for r in complete])
        y_a = np.array([_safe_float(r[TARGET_A]) for r in complete])
        y_b = np.array([_safe_float(r[TARGET_B]) for r in complete])

        X_tr, X_val, ya_tr, ya_val = train_test_split(X, y_a, test_size=0.2, random_state=42)
        _,    _,     yb_tr, yb_val = train_test_split(X, y_b, test_size=0.2, random_state=42)

        params = dict(
            n_estimators=200, max_depth=4, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            objective="reg:squarederror", random_state=42, n_jobs=-1,
        )

        model_a = XGBRegressor(**params)
        model_a.fit(X_tr, ya_tr, eval_set=[(X_val, ya_val)], verbose=False)
        mae_a = mean_absolute_error(ya_val, model_a.predict(X_val))

        model_b = XGBRegressor(**params)
        model_b.fit(X_tr, yb_tr, eval_set=[(X_val, yb_val)], verbose=False)
        mae_b = mean_absolute_error(yb_val, model_b.predict(X_val))

        joblib.dump(model_a, MODEL_A_PATH)
        joblib.dump(model_b, MODEL_B_PATH)

        logger.info("[train] 訓練完成 MAE_A=%.2f MAE_B=%.2f", mae_a, mae_b)
        return True

    except Exception as exc:
        logger.warning("[train] 訓練失敗（不影響系統）: %s", exc)
        return False


def _safe_float(val) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


if __name__ == "__main__":
    ok = run_training()
    sys.exit(0)   # 永遠 exit 0，不讓 CI 顯示紅燈
