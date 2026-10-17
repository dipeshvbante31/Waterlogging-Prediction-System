"""One shared single-prediction path (manual entry, feature lookup, live weather)."""
from __future__ import annotations

from app.container import Container
from app.services.data_validation import humanize_reason
from app.services.schema import DatasetSchema
from app.services import job_service as js
from app.services.explainability_service import local_explanation
from app.services.prediction_service import IncompatibleInputError, NoModelError, predict_records

DISCLAIMER = ("Model output is a statistical estimate learned from the supplied data, not a measurement of "
              "street flooding, and is only meaningful inside the time and area covered by the training data.")


def run_single(c: Container, rec: dict, source: str, save_history: bool = False, provenance: dict | None = None,
               location_name: str | None = None) -> dict:
    bundle = c.registry.load()
    if bundle is None:
        raise NoModelError("No validated model is active. Train and activate a validated model first.")
    out = predict_records([rec], bundle, city=c.city)
    if out.results.empty:
        schema = DatasetSchema.from_dict(bundle.metadata["schema"])
        reasons = sorted({humanize_reason(c, schema) for cell in out.rejected["rejection_reason"].astype(str) for c in cell.split(";") if c})
        raise IncompatibleInputError("Input rejected: " + "; ".join(reasons) + ".")
    row = {k: (v.item() if hasattr(v, "item") else v) for k, v in out.results.iloc[0].to_dict().items()}
    cov = c.city.coverage(rec["latitude"], rec["longitude"])
    history_id = None
    if save_history:
        with c.session() as s:
            rows = js.history_rows(out, None, source, bundle.metadata["input_columns"], provenance, location_name)
            s.add_all(rows)
            s.flush()
            history_id = rows[0].id
    warnings = list(out.report.warnings)
    if not cov["eligible"]:
        warnings.insert(0, cov["reason"])
    return {"result": row, "history_id": history_id, "coverage": cov,
            "explanation": {"method": "median-reference substitution (model association, not causation)",
                            "items": local_explanation(bundle, out.report.clean.iloc[[0]])},
            "model": {"version": bundle.version, "status": bundle.status},
            "feature_provenance": provenance, "warnings": warnings, "disclaimer": DISCLAIMER}
