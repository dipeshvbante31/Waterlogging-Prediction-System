"""Prediction on validated input only; refuses incompatible files; labels model status."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.cities import CityProfile
from app.services.data_validation import ValidationReport, humanize_reason, validate_dataframe
from app.services.feature_engineering import FeatureConfig, engineer_features
from app.services.model_registry import ModelBundle
from app.services.schema import DatasetSchema


class IncompatibleInputError(Exception):
    pass


class NoModelError(Exception):
    pass


@dataclass
class PredictionOutput:
    results: pd.DataFrame
    rejected: pd.DataFrame
    report: ValidationReport
    model_version: str
    model_status: str


def risk_label(p: float, bundle: ModelBundle) -> str:
    md = bundle.metadata
    thr = md.get("risk_thresholds")
    if md["status"] != "validated" or not thr:
        return "pending_validation"
    return "High" if p >= thr["high"] else ("Medium" if p >= thr["medium"] else "Low")


def predict_dataframe(df: pd.DataFrame, bundle: ModelBundle | None, mapping: dict | None = None,
                      city: CityProfile | None = None, max_rows: int | None = None) -> PredictionOutput:
    if bundle is None:
        raise NoModelError("No trained model is available. Train a model on a labelled dataset first.")
    md = bundle.metadata
    schema = DatasetSchema.from_dict(md["schema"])
    rep = validate_dataframe(df, schema, "predict", mapping, city, max_rows)
    if rep.fatal_errors:
        if rep.n_rows_valid == 0 and len(rep.rejected):   # every row was rejected: say WHY in plain language
            reasons = sorted({humanize_reason(c, schema) for cell in rep.rejected["rejection_reason"].astype(str) for c in cell.split(";") if c})
            raise IncompatibleInputError("Input rejected: " + "; ".join(reasons) + ".")
        raise IncompatibleInputError("; ".join(rep.fatal_errors))
    missing = [c for c in md["input_columns"] if c not in rep.clean.columns]
    if missing:
        raise IncompatibleInputError(
            f"The input lacks columns required by model {bundle.version}: {missing}. "
            "Prediction is unavailable until these inputs are supplied (use null only if a missing value is acceptable).")

    clean = rep.clean
    inputs = clean[md["input_columns"]]
    no_inputs = inputs.isna().all(axis=1)
    if no_inputs.any():
        bad = clean[no_inputs]
        rej = df.iloc[bad["row_id"].values - 1].copy()
        rej.insert(0, "row_id", bad["row_id"].values)
        rej["rejection_reason"] = "no_model_inputs"
        rep.rejected = pd.concat([rep.rejected, rej], ignore_index=True).sort_values("row_id")
        rep.n_rows_rejected += int(no_inputs.sum()); rep.n_rows_valid -= int(no_inputs.sum())
        rep.rejection_counts["no_model_inputs"] = int(no_inputs.sum())
        clean = clean[~no_inputs].reset_index(drop=True)

    fcfg = FeatureConfig(**md["config"]["features"])
    X = engineer_features(clean, fcfg)[md["feature_columns"]]
    p = bundle.model.predict_proba(X)[:, 1]
    thr = md["decision_threshold"]
    now = pd.Timestamp.now(tz="UTC").isoformat()

    orig = df.iloc[clean["row_id"].values - 1].reset_index(drop=True)
    if "row_id" in orig.columns:
        orig = orig.rename(columns={"row_id": "input_row_id"})
    imputed = clean[md["input_columns"]].isna().apply(lambda r: ",".join(c for c, v in r.items() if v), axis=1)
    out = pd.concat([pd.DataFrame({"row_id": clean["row_id"].values}), orig], axis=1)
    out["predicted_class"] = (p >= thr).astype(int)
    out["probability"] = np.round(p, 6)
    out["risk_level"] = [risk_label(v, bundle) for v in p]
    out["imputed_inputs"] = imputed.values
    out["out_of_region"] = (~clean.apply(lambda r: city.contains(r["latitude"], r["longitude"]), axis=1)).values if city else False
    out["model_version"] = bundle.version
    out["model_status"] = bundle.status
    out["prediction_timestamp"] = now
    return PredictionOutput(out, rep.rejected, rep, bundle.version, bundle.status)


def predict_records(records: list[dict], bundle: ModelBundle | None, city: CityProfile | None = None) -> PredictionOutput:
    return predict_dataframe(pd.DataFrame(records), bundle, city=city)
