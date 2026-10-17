"""Leakage-aware training: split -> fit preprocessing on train only -> compare models on
validation -> calibrate on validation -> report on untouched test split."""
from __future__ import annotations

import hashlib
import platform
import sys
from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd
import sklearn
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.frozen import FrozenEstimator
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.services.evaluation_service import (binary_metrics, calibration_bins,
                                              choose_decision_threshold, derive_risk_thresholds)
from app.services.feature_engineering import (FeatureConfig, engineer_features,
                                               final_feature_columns, select_input_columns)
from app.services.schema import DatasetSchema


class TrainingError(Exception):
    """Raised when data cannot support honest training (insufficient, single-class, ...)."""


@dataclass
class TrainConfig:
    split_strategy: str = "chronological"   # chronological | spatial_group | random_stratified
    train_frac: float = 0.6
    val_frac: float = 0.2
    min_rows: int = 200
    min_pos_train: int = 20
    min_pos_val: int = 10
    min_pos_test: int = 10
    min_locations: int = 10                  # distinct ~1 km cells needed for "validated"
    min_pr_auc_margin: float = 0.05          # test PR-AUC must beat prevalence by this margin
    spatial_cell_deg: float = 0.01
    random_state: int = 42
    n_permutation_repeats: int = 10
    models: tuple = ("logistic_regression", "random_forest", "hist_gradient_boosting")
    label_provenance: str = ""               # free text: where the labels come from
    provenance_attested: bool = False        # user attests labels are verified observations
    features: FeatureConfig = field(default_factory=FeatureConfig)

    def to_dict(self):
        d = asdict(self)
        d["models"] = list(self.models)
        return d


@dataclass
class TrainingResult:
    status: str                     # validated | experimental | no_eligible_model
    status_reason: str
    metadata: dict
    model: object | None            # fitted calibrated model (None if no eligible model)
    splits: dict = field(repr=False, default_factory=dict)        # name -> row_id array
    test_predictions: pd.DataFrame | None = field(repr=False, default=None)


def _estimator(name: str, seed: int):
    if name == "logistic_regression":
        return LogisticRegression(class_weight="balanced", max_iter=2000, random_state=seed)
    if name == "random_forest":
        return RandomForestClassifier(n_estimators=300, min_samples_leaf=5, class_weight="balanced_subsample",
                                      n_jobs=1, random_state=seed)
    if name == "hist_gradient_boosting":
        return HistGradientBoostingClassifier(class_weight="balanced", max_iter=200, learning_rate=0.05,
                                              random_state=seed)
    raise TrainingError(f"Unknown model '{name}'.")


def _pipeline(name: str, cols: list[str], seed: int) -> Pipeline:
    prep = ColumnTransformer([("num", Pipeline([
        ("impute", SimpleImputer(strategy="median", add_indicator=True)),
        ("scale", StandardScaler())]), cols)], remainder="drop")
    return Pipeline([("prep", prep), ("clf", _estimator(name, seed))])


# ---------------------------------------------------------------- splitting
def _chronological(df, cfg):
    d = df.sort_values("timestamp", kind="stable")
    day = d["timestamp"].dt.strftime("%Y-%m-%d")
    days = day.drop_duplicates().tolist()
    if len(days) < 3:
        raise TrainingError("Chronological split needs at least 3 distinct dates.")
    counts = day.value_counts().reindex(days).values.cumsum()
    n = len(d)
    i_tr = int(np.searchsorted(counts, cfg.train_frac * n))
    i_va = int(np.searchsorted(counts, (cfg.train_frac + cfg.val_frac) * n))
    i_tr = min(i_tr, len(days) - 3)
    i_va = min(max(i_va, i_tr + 1), len(days) - 2)
    tr_days, va_days = set(days[: i_tr + 1]), set(days[i_tr + 1: i_va + 1])
    tr = day.isin(tr_days); va = day.isin(va_days); te = ~(tr | va)
    return d[tr], d[va], d[te]


def _cells(df, cfg):
    return (np.floor(df["latitude"] / cfg.spatial_cell_deg).astype(int).astype(str) + "_" +
            np.floor(df["longitude"] / cfg.spatial_cell_deg).astype(int).astype(str))


def _spatial(df, cfg):
    cell = _cells(df, cfg)
    ucells = cell.unique()
    if len(ucells) < 3:
        raise TrainingError("Spatial split needs at least 3 distinct location cells.")
    y = df["_y"]
    need = (cfg.min_pos_train, cfg.min_pos_val, cfg.min_pos_test)
    for k in range(50):  # seed search uses ONLY label counts, never model scores
        rng = np.random.RandomState(cfg.random_state + k)
        order = rng.permutation(ucells)
        sizes = cell.value_counts()
        cum, assign = 0, {}
        for c in order:
            frac = cum / len(df)
            assign[c] = 0 if frac < cfg.train_frac else (1 if frac < cfg.train_frac + cfg.val_frac else 2)
            cum += sizes[c]
        part = cell.map(assign)
        if all(((part == i) & (y == 1)).sum() >= need[i] and ((part == i) & (y == 0)).sum() >= 1 for i in range(3)):
            return df[part == 0], df[part == 1], df[part == 2]
    raise TrainingError("No location-grouped split satisfies the minimum positives per split.")


def _random(df, cfg):
    tr, rest = train_test_split(df, train_size=cfg.train_frac, stratify=df["_y"], random_state=cfg.random_state)
    va, te = train_test_split(rest, train_size=cfg.val_frac / (1 - cfg.train_frac), stratify=rest["_y"],
                              random_state=cfg.random_state)
    return tr, va, te


def split_dataset(df, cfg):
    fn = {"chronological": _chronological, "spatial_group": _spatial, "random_stratified": _random}.get(cfg.split_strategy)
    if fn is None:
        raise TrainingError(f"Unknown split strategy '{cfg.split_strategy}'.")
    return fn(df, cfg)


# ---------------------------------------------------------------- training
def train_models(clean: pd.DataFrame, schema: DatasetSchema, cfg: TrainConfig, city=None) -> TrainingResult:
    y_col = schema.target
    if len(clean) < cfg.min_rows:
        raise TrainingError(f"Insufficient data: {len(clean)} valid rows; at least {cfg.min_rows} required.")
    if clean[y_col].nunique() < 2:
        raise TrainingError("Training needs both classes (0 and 1); only one class is present.")

    input_cols, dropped = select_input_columns(clean, schema, cfg.features)
    if not input_cols:
        raise TrainingError("No usable input features (all missing or absent).")
    feat_cols = final_feature_columns(input_cols, cfg.features)

    df = engineer_features(clean, cfg.features)
    df["_y"] = df[y_col].astype(int)
    tr, va, te = split_dataset(df, cfg)
    for nm, part, need in (("train", tr, cfg.min_pos_train), ("validation", va, cfg.min_pos_val), ("test", te, cfg.min_pos_test)):
        pos = int(part["_y"].sum()); neg = len(part) - pos
        if pos < need or neg < 1:
            raise TrainingError(f"Insufficient data in {nm} split: {pos} positives / {neg} negatives "
                                f"(need >= {need} positives and >= 1 negative). Add more labelled records or relax the split.")

    Xtr, ytr = tr[feat_cols], tr["_y"].values
    Xva, yva = va[feat_cols], va["_y"].values
    Xte, yte = te[feat_cols], te["_y"].values
    val_prev = float(yva.mean())

    candidates, fitted = [], {}
    for name in cfg.models:
        pipe = _pipeline(name, feat_cols, cfg.random_state).fit(Xtr, ytr)  # preprocessing fitted on train only
        pv = pipe.predict_proba(Xva)[:, 1]
        ap = float(average_precision_score(yva, pv))
        fitted[name] = pipe
        candidates.append({"model": name, "val_pr_auc": ap, "eligible": ap > val_prev})
    elig = [c for c in candidates if c["eligible"]]
    meta = {
        "created_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "schema": schema.to_dict(), "config": cfg.to_dict(),
        "region": city.to_dict() if city else None,
        "input_columns": input_cols, "feature_columns": feat_cols, "dropped_low_coverage": dropped,
        "data": {
            "n_rows": int(len(clean)), "n_positive": int(df["_y"].sum()),
            "prevalence": float(df["_y"].mean()),
            "time_range": [clean["timestamp"].min().isoformat(), clean["timestamp"].max().isoformat()],
            "n_location_cells": int(_cells(clean, cfg).nunique()),
            "sha256": hashlib.sha256(clean.to_csv(index=False).encode()).hexdigest(),
        },
        "split": {"strategy": cfg.split_strategy, **{
            nm: {"n": int(len(p)), "n_positive": int(p["_y"].sum()),
                 "time_min": p["timestamp"].min().isoformat(), "time_max": p["timestamp"].max().isoformat()}
            for nm, p in (("train", tr), ("validation", va), ("test", te))}},
        "candidates": candidates, "baseline_val_prevalence": val_prev,
        "library_versions": {"python": sys.version.split()[0], "scikit_learn": sklearn.__version__,
                             "numpy": np.__version__, "pandas": pd.__version__, "platform": platform.platform()},
        "caveats": [],
    }
    splits = {"train": tr["row_id"].values, "validation": va["row_id"].values, "test": te["row_id"].values}
    if not elig:
        meta.update(status="no_eligible_model", selected_model=None)
        return TrainingResult("no_eligible_model",
                              "No candidate beat the validation prevalence baseline (PR-AUC); no model was saved.",
                              meta, None, splits)

    # documented selection rule: highest validation PR-AUC; ties -> earlier (simpler) model in cfg.models
    best = max(elig, key=lambda c: (round(c["val_pr_auc"], 12), -cfg.models.index(c["model"])))["model"]
    pipe = fitted[best]
    # FrozenEstimator: the base pipeline is NOT refitted; all validation rows are used to fit the 2-parameter sigmoid.
    cal = CalibratedClassifierCV(FrozenEstimator(pipe), method="sigmoid", cv=2).fit(Xva, yva)  # calibrator sees validation only
    pva = cal.predict_proba(Xva)[:, 1]
    thr = choose_decision_threshold(yva, pva)
    risk = derive_risk_thresholds(yva, pva)
    pte = cal.predict_proba(Xte)[:, 1]
    test_metrics = binary_metrics(yte, pte, thr)
    uncal_brier = float(binary_metrics(yte, pipe.predict_proba(Xte)[:, 1], thr)["brier"])
    pi = permutation_importance(pipe, Xte, yte, scoring="average_precision",
                                n_repeats=cfg.n_permutation_repeats, random_state=cfg.random_state)
    importance = sorted(({"feature": f, "mean": float(m), "std": float(s)}
                         for f, m, s in zip(feat_cols, pi.importances_mean, pi.importances_std)),
                        key=lambda d: -d["mean"])

    gates = []
    def gate(name, passed, detail):
        gates.append({"name": name, "passed": bool(passed), "detail": detail})
    gate("labels_attested_as_verified_observations", cfg.provenance_attested and bool(cfg.label_provenance.strip()),
         "User must describe the label source and attest labels are verified observations.")
    gate("evaluation_split_respects_time_or_space", cfg.split_strategy in ("chronological", "spatial_group"),
         f"split strategy = {cfg.split_strategy}; random splits leak correlated records.")
    gate("enough_distinct_locations", meta["data"]["n_location_cells"] >= cfg.min_locations,
         f"{meta['data']['n_location_cells']} cells; need >= {cfg.min_locations}.")
    pr = test_metrics["pr_auc"]
    gate("test_pr_auc_beats_prevalence", pr is not None and pr >= test_metrics["prevalence"] + cfg.min_pr_auc_margin,
         f"test PR-AUC={pr}, prevalence={test_metrics['prevalence']:.4f}, margin={cfg.min_pr_auc_margin}.")
    gate("risk_thresholds_derivable", risk is not None, "Validation data must support ordered medium/high operating points.")
    failed = [g for g in gates if not g["passed"] and g["name"] != "risk_thresholds_derivable"]
    status = "validated" if not failed else "experimental"
    reason = ("All validation gates passed on the held-out test split of the supplied dataset. This does not "
              "establish street-level accuracy outside the data's time and location coverage."
              if status == "validated" else "Not validated: " + "; ".join(g["name"] for g in failed))
    if cfg.split_strategy == "random_stratified":
        meta["caveats"].append("Random split: metrics are likely optimistic due to spatial/temporal correlation.")
    meta["caveats"].append("Calibration: sigmoid, fitted on the validation split only; decision threshold maximises validation F1.")
    meta.update(status=status, status_reason=reason, selected_model=best, decision_threshold=thr,
                risk_thresholds=risk, test_metrics=test_metrics, test_brier_uncalibrated=uncal_brier,
                calibration=calibration_bins(yte, pte), permutation_importance=importance, gates=gates,
                train_feature_medians={f: (None if pd.isna(v) else float(v)) for f, v in Xtr.median().items()},
                label_provenance=cfg.label_provenance, provenance_attested=cfg.provenance_attested)
    tp = pd.DataFrame({"row_id": te["row_id"].values, "y_true": yte, "proba": pte})
    return TrainingResult(status, reason, meta, cal, splits, tp)
