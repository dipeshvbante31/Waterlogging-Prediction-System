"""Metric computation. All numbers come from actual predictions; nothing is hardcoded."""
from __future__ import annotations

import numpy as np
from sklearn.calibration import calibration_curve
from sklearn.metrics import (average_precision_score, brier_score_loss, confusion_matrix,
                             f1_score, precision_recall_curve, precision_score, recall_score,
                             roc_auc_score)


def binary_metrics(y_true, proba, threshold: float) -> dict:
    y = np.asarray(y_true).astype(int)
    p = np.asarray(proba, dtype=float)
    pred = (p >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    both = len(np.unique(y)) == 2
    return {
        "n": int(len(y)), "n_positive": int(y.sum()), "prevalence": float(y.mean()),
        "threshold": float(threshold),
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred, zero_division=0)),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "pr_auc": float(average_precision_score(y, p)) if both else None,
        "roc_auc": float(roc_auc_score(y, p)) if both else None,
        "brier": float(brier_score_loss(y, p)),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }


def calibration_bins(y_true, proba) -> dict:
    y = np.asarray(y_true).astype(int)
    n_bins = int(min(10, max(2, y.sum() // 5)))
    try:
        frac_pos, mean_pred = calibration_curve(y, proba, n_bins=n_bins, strategy="quantile")
    except ValueError:
        return {"n_bins": 0, "mean_predicted": [], "fraction_positive": []}
    return {"n_bins": int(len(frac_pos)), "mean_predicted": [float(x) for x in mean_pred],
            "fraction_positive": [float(x) for x in frac_pos]}


def choose_decision_threshold(y_val, p_val) -> float:
    """Threshold maximising F1 on the VALIDATION split (never the test split)."""
    prec, rec, thr = precision_recall_curve(y_val, p_val)
    f1 = 2 * prec[:-1] * rec[:-1] / np.clip(prec[:-1] + rec[:-1], 1e-12, None)
    return float(thr[int(np.argmax(f1))]) if len(thr) else 0.5


def derive_risk_thresholds(y_val, p_val, medium_recall: float = 0.80, high_precision: float = 0.50):
    """Data-derived operating points from VALIDATION data (documented policy, configurable):
    medium = largest threshold keeping validation recall >= ``medium_recall``;
    high   = smallest threshold reaching validation precision >= ``high_precision``.
    These are statistical operating points, NOT physical flood thresholds.
    Returns None if the data cannot support distinct, ordered thresholds."""
    prec, rec, thr = precision_recall_curve(y_val, p_val)
    prec, rec = prec[:-1], rec[:-1]
    ok_m = np.where(rec >= medium_recall)[0]
    ok_h = np.where(prec >= high_precision)[0]
    if len(ok_m) == 0 or len(ok_h) == 0:
        return None
    medium, high = float(thr[ok_m.max()]), float(thr[ok_h.min()])
    if not medium < high:
        return None
    return {"medium": medium, "high": high, "policy":
            {"medium_min_val_recall": medium_recall, "high_min_val_precision": high_precision}}
