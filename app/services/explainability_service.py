"""Explanations describe model associations, not causes."""
from __future__ import annotations

import pandas as pd

from app.services.feature_engineering import FeatureConfig, engineer_features


def global_importance(metadata: dict) -> list[dict]:
    return metadata.get("permutation_importance", [])


def local_explanation(bundle, row: pd.DataFrame, top: int = 6) -> list[dict]:
    """Median-reference substitution: for each raw input, replace it with the training-set median and
    report the change in predicted probability. Positive delta = this input value pushes the estimate
    UP relative to a typical value. An association learned by the model, NOT a causal effect."""
    md = bundle.metadata
    fcfg = FeatureConfig(**md["config"]["features"])
    med = md["train_feature_medians"]
    cols = md["input_columns"]

    def prob(frame):
        X = engineer_features(frame, fcfg)[md["feature_columns"]]
        return bundle.model.predict_proba(X)[:, 1]

    base = prob(row)[0]
    out = []
    for c in cols:
        if med.get(c) is None or pd.isna(row[c].iloc[0]):
            continue
        alt = row.copy()
        alt[c] = med[c]
        out.append({"feature": c, "value": float(row[c].iloc[0]), "typical_value": med[c],
                    "delta_probability": float(base - prob(alt)[0])})
    out.sort(key=lambda d: -abs(d["delta_probability"]))
    return out[:top]
