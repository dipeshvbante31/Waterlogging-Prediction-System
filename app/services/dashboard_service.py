"""Dashboard statistics. Every figure is computed from stored records; each has an explicit definition shown in the UI."""
from __future__ import annotations

from sqlalchemy import func, select

from app.container import Container
from app.database.models import Dataset, Job, PredictionRecord

CATEGORIES = ("Low", "Medium", "High")
DEFINITIONS = {
    "total_records": "Prediction records saved to History (single, batch and weather-based). Uploaded dataset rows and training rows are NOT counted.",
    "high_risk": "Saved predictions whose risk category is High. Only predictions made by a validated model with validated thresholds have a category.",
    "average_probability": "Mean model probability over saved predictions made by validated models. Probabilities are model outputs, not guarantees.",
    "overall_level": "The category obtained by applying the active validated model's thresholds to the mean probability. Shown only when those thresholds exist.",
}


def build_dashboard(c: Container) -> dict:
    with c.session() as s:
        total = s.scalar(select(func.count(PredictionRecord.id))) or 0
        dist = dict(s.execute(select(PredictionRecord.risk_level, func.count()).group_by(PredictionRecord.risk_level)).all())
        avg_p, n_valid = s.execute(select(func.avg(PredictionRecord.probability), func.count(PredictionRecord.id))
                                   .where(PredictionRecord.model_status == "validated")).one()
        latest = s.scalar(select(PredictionRecord).order_by(PredictionRecord.created_at.desc(), PredictionRecord.id.desc()).limit(1))
        recent = s.scalars(select(PredictionRecord).order_by(PredictionRecord.created_at.desc(), PredictionRecord.id.desc()).limit(8)).all()
        ds_total = s.scalar(select(func.count(Dataset.id)).where(Dataset.status != "expired")) or 0
        last_train = s.scalar(select(Job).where(Job.type == "train").order_by(Job.created_at.desc()).limit(1))

    categorised = sum(dist.get(k, 0) for k in CATEGORIES)
    high = dist.get("High", 0)
    pending = dist.get("pending_validation", 0)

    overall = None
    avg = float(avg_p) if avg_p is not None else None
    v = c.registry.active_version()
    md = c.registry.get_metadata(v) if v else None
    thr = (md or {}).get("risk_thresholds") if md and md["status"] == "validated" else None
    if avg is not None and thr:
        overall = "High" if avg >= thr["high"] else ("Medium" if avg >= thr["medium"] else "Low")

    def row(r: PredictionRecord) -> dict:
        return {"id": r.id, "location_name": r.location_name, "latitude": r.latitude, "longitude": r.longitude, "risk_level": r.risk_level,
                "predicted_class": r.predicted_class, "probability": r.probability, "created_at": r.created_at, "source": r.source,
                "model_version": r.model_version, "model_status": r.model_status}

    insights = []
    if categorised:
        insights.append(f"{high} of {categorised} saved predictions with a risk category ({100 * high / categorised:.0f}%) are in the High category.")
    if pending:
        insights.append(f"{pending} saved prediction(s) have no risk category because they were made without validated thresholds.")
    if last_train is not None:
        insights.append(f"The most recent training job {last_train.status}" + (f" ({last_train.result.get('outcome')})" if last_train.result and last_train.result.get("outcome") else "") + ".")
    return {
        "total_records": total, "high_risk": high, "high_risk_denominator": categorised,
        "average_probability": avg, "average_probability_n": n_valid, "overall_level": overall,
        "distribution": {k: dist.get(k, 0) for k in (*CATEGORIES, "pending_validation") if dist.get(k, 0)},
        "distribution_denominator": total, "latest": row(latest) if latest else None, "recent": [row(r) for r in recent],
        "datasets_available": ds_total, "insights": insights, "definitions": DEFINITIONS,
        "active_model": {"version": v, "status": md["status"]} if md else None,
    }
