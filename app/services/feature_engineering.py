"""Reusable, unit-tested feature transforms. Every derived feature uses only inputs
that are also available at prediction time (no target, no future data)."""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from app.services.schema import DatasetSchema

# name -> (larger-window column, smaller-window column); unit: mm over the stated period
ANTECEDENT = {
    "rain_prior_23h_mm": ("rainfall_24h_mm", "rainfall_1h_mm"),   # rain in hours 2..24 before timestamp
    "rain_prior_48h_mm": ("rainfall_72h_mm", "rainfall_24h_mm"),  # rain in hours 25..72 before timestamp
}


@dataclass
class FeatureConfig:
    use_coordinates_as_features: bool = False  # off: avoids memorising locations
    use_antecedent_features: bool = True
    use_seasonal_features: bool = False        # month_sin / month_cos (UTC month)
    min_feature_coverage: float = 0.5          # drop inputs with < 50% non-missing in training data
    exclude: tuple = ()                        # features deliberately left out (e.g. ones no data source can supply at prediction time)

    def to_dict(self):
        return asdict(self)


def engineer_features(df: pd.DataFrame, cfg: FeatureConfig) -> pd.DataFrame:
    out = df.copy()
    if cfg.use_antecedent_features:
        for name, (big, small) in ANTECEDENT.items():
            if big in out.columns and small in out.columns:
                out[name] = (out[big] - out[small]).clip(lower=0)
    if cfg.use_seasonal_features and "timestamp" in out.columns:
        m = out["timestamp"].dt.month.astype(float)
        out["month_sin"] = np.sin(2 * np.pi * m / 12)
        out["month_cos"] = np.cos(2 * np.pi * m / 12)
    return out


def select_input_columns(clean: pd.DataFrame, schema: DatasetSchema, cfg: FeatureConfig):
    """Raw schema inputs used by the model, plus the list dropped for low coverage."""
    cols, dropped = [], []
    for name in schema.feature_names:
        if name in cfg.exclude:
            dropped.append(name)
            continue
        if name in clean.columns:
            cov = 1 - clean[name].isna().mean()
            (cols if cov >= cfg.min_feature_coverage else dropped).append(name)
    if cfg.use_coordinates_as_features:
        cols += ["latitude", "longitude"]
    return cols, dropped


def final_feature_columns(input_cols: list[str], cfg: FeatureConfig) -> list[str]:
    cols = list(input_cols)
    if cfg.use_antecedent_features:
        for name, (big, small) in ANTECEDENT.items():
            if big in input_cols and small in input_cols:
                cols.append(name)
    if cfg.use_seasonal_features:
        cols += ["month_sin", "month_cos"]
    return cols
