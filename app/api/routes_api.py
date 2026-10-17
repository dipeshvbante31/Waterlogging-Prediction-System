from __future__ import annotations

import io
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select

from app.api.deps import RateLimiter, get_c, require_admin
from app.container import Container
from app.database.models import Dataset, Job, PredictionRecord
from app.services import job_service as js
from app.services.cleanup_service import run_cleanup
from app.services.data_validation import parse_single_timestamp, validate_dataframe
from app.services.dashboard_service import build_dashboard
from app.services.explore_service import explore as explore_dataset
from app.services.weather_service import WeatherUnavailable
from app.services.dataset_service import get_dataset, load_dataframe, register_dataset
from app.services.model_registry import RegistryError
from app.services.feature_service import resolve_features
from app.services.predict_flow import DISCLAIMER, run_single
from app.services.prediction_service import NoModelError
from app.services.schema import DEFAULT_SCHEMA
from app.upload import read_limited
from app.utils.csv_safety import to_safe_csv_bytes

router = APIRouter(prefix="/api")
log = logging.getLogger("wps.api")
upload_limit, predict_limit = RateLimiter("upload"), RateLimiter("predict")

STATE_TEXT = {
    "not_trained": "No model has been trained. Predictions are unavailable.",
    "experimental": "Trained but NOT validated. Outputs are experimental estimates only.",
    "validated": "Passed validation gates on a held-out test split of the supplied dataset.",
}


def _job_dict(j: Job) -> dict:
    return {"id": j.id, "type": j.type, "status": j.status, "dataset_id": j.dataset_id, "result": j.result,
            "error": j.error, "created_at": j.created_at, "started_at": j.started_at, "finished_at": j.finished_at,
            "downloads": sorted((j.result_keys or {}).keys())}


def model_state(c: Container) -> dict:
    v = c.registry.active_version()
    if v is None:
        return {"state": "not_trained", "description": STATE_TEXT["not_trained"], "version": None}
    try:
        md = c.registry.get_metadata(v)
    except RegistryError:
        return {"state": "not_trained", "description": STATE_TEXT["not_trained"], "version": None}
    return {"state": md["status"], "description": STATE_TEXT[md["status"]], "version": v,
            "status_reason": md["status_reason"], "selected_model": md["selected_model"],
            "created_at": md["created_at"], "test_metrics": md["test_metrics"], "gates": md["gates"],
            "region": md["region"]["name"] if md.get("region") else None,
            "data": md["data"], "risk_thresholds_available": bool(md.get("risk_thresholds")) and md["status"] == "validated"}


@router.get("/status")
def status(c: Container = Depends(get_c)):
    with c.session() as s:
        n_pred = s.scalar(select(func.count(PredictionRecord.id)))
        last_job = s.scalar(select(Job).where(Job.type == "train", Job.status == "succeeded").order_by(Job.finished_at.desc()).limit(1))
        ds_ready = s.scalar(select(func.count(Dataset.id)).where(Dataset.kind == "train", Dataset.status == "validated"))
        risk = dict(s.execute(select(PredictionRecord.risk_level, func.count()).group_by(PredictionRecord.risk_level)).all())
        recent = s.scalars(select(Job).order_by(Job.created_at.desc()).limit(6)).all()
        recent_jobs = [_job_dict(j) for j in recent]
        ds_total = s.scalar(select(func.count(Dataset.id)).where(Dataset.status != "expired"))
    return {"model": model_state(c), "region": c.city.to_dict(), "predictions_recorded": n_pred,
            "recent_jobs": recent_jobs, "datasets_available": ds_total,
            "risk_distribution": risk, "validated_training_datasets_available": ds_ready,
            "last_training_job": _job_dict(last_job) if last_job else None}


@router.get("/template.csv")
def template():
    cols = [f.name for f in DEFAULT_SCHEMA.features] + [DEFAULT_SCHEMA.target]
    return Response(",".join(cols) + "\n", media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="waterlogging_template.csv"'})


@router.get("/schema")
def schema():
    return DEFAULT_SCHEMA.to_dict()


# ---------------------------------------------------------------- datasets
@router.post("/datasets", status_code=201, dependencies=[Depends(upload_limit)])
async def upload_dataset(kind: str = Query(pattern="^(train|predict)$"), file: UploadFile = File(...),
                         c: Container = Depends(get_c)):
    data = await read_limited(file, c.settings.max_upload_bytes)
    ds, rep = register_dataset(c, kind, file.filename, data)
    return {"dataset_id": ds.id, "kind": ds.kind, "status": ds.status, "expires_at": ds.expires_at,
            "report": rep.to_dict()}


@router.get("/datasets/{dataset_id}")
def dataset_info(dataset_id: str, c: Container = Depends(get_c)):
    ds = get_dataset(c, dataset_id)
    return {"dataset_id": ds.id, "kind": ds.kind, "status": ds.status, "n_rows": ds.n_rows,
            "expires_at": ds.expires_at, "report": ds.validation}


@router.get("/datasets/{dataset_id}/rejected.csv")
def dataset_rejected(dataset_id: str, c: Container = Depends(get_c)):
    ds = get_dataset(c, dataset_id)
    rep = validate_dataframe(load_dataframe(c, ds), DEFAULT_SCHEMA, ds.kind, city=c.city)
    return Response(to_safe_csv_bytes(rep.rejected), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="rejected_rows.csv"'})


# ---------------------------------------------------------------- training & models
class TrainIn(BaseModel):
    dataset_id: str = Field(min_length=32, max_length=32)
    split_strategy: str = Field("chronological", pattern="^(chronological|spatial_group|random_stratified)$")
    label_provenance: str = Field("", max_length=1000)
    provenance_attested: bool = False
    exclude_features: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("exclude_features")
    @classmethod
    def _known(cls, v):
        bad = [x for x in v if x not in DEFAULT_SCHEMA.feature_names]
        if bad:
            raise ValueError(f"unknown feature(s): {bad}")
        return v


@router.post("/train", status_code=202, dependencies=[Depends(require_admin)])
def train(body: TrainIn, c: Container = Depends(get_c)):
    job = js.submit_train(c, body.dataset_id, body.model_dump(exclude={"dataset_id"}))
    return _job_dict(js.get_job(c, job.id))


@router.get("/jobs/{job_id}")
def job_status(job_id: str, c: Container = Depends(get_c)):
    j = js.get_job(c, job_id)
    if j is None:
        raise HTTPException(404, "Job not found.")
    return _job_dict(j)


@router.get("/jobs/{job_id}/download/{name}")
def job_download(job_id: str, name: str, c: Container = Depends(get_c)):
    j = js.get_job(c, job_id)
    if j is None or not j.result_keys or name not in j.result_keys:  # key comes from DB, never from the URL
        raise HTTPException(404, "File not found or expired.")
    return Response(c.store.get_bytes(j.result_keys[name]), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


@router.get("/models")
def models(c: Container = Depends(get_c)):
    return {"active": c.registry.active_version(), "versions": c.registry.list_versions()}


@router.get("/models/active")
def active_model(c: Container = Depends(get_c)):
    v = c.registry.active_version()
    if not v:
        raise HTTPException(404, "No active model.")
    md = c.registry.get_metadata(v)
    return {k: md[k] for k in ("version", "status", "status_reason", "selected_model", "created_at", "split", "data",
                               "candidates", "test_metrics", "test_brier_uncalibrated", "calibration", "gates",
                               "permutation_importance", "input_columns", "feature_columns", "caveats",
                               "decision_threshold", "risk_thresholds", "region", "label_provenance")}


@router.post("/models/{version}/activate", dependencies=[Depends(require_admin)])
def activate(version: str, c: Container = Depends(get_c)):
    try:
        c.registry.set_active(version)
    except RegistryError as e:
        raise HTTPException(409 if "validated" in str(e) else 404, str(e) if "validated" in str(e) else "Unknown model version.")
    return {"active": version}


# ---------------------------------------------------------------- prediction
class SinglePredictIn(BaseModel):
    timestamp: str = Field(max_length=40)
    latitude: float
    longitude: float
    features: dict[str, float | None] = Field(default_factory=dict, max_length=30)
    feature_sources: dict[str, str] | None = Field(default=None, max_length=30)
    location_name: str | None = Field(default=None, max_length=120)
    save_history: bool = False

    @field_validator("features")
    @classmethod
    def _keys(cls, v):
        if any(len(k) > 64 for k in v):
            raise ValueError("feature name too long")
        return v


@router.post("/predict", dependencies=[Depends(predict_limit)])
def predict_single(body: SinglePredictIn, c: Container = Depends(get_c)):
    rec = {"timestamp": body.timestamp, "latitude": body.latitude, "longitude": body.longitude, **body.features}
    prov = body.feature_sources or {k: "user-supplied" for k in body.features}
    r = run_single(c, rec, "single", body.save_history, prov, body.location_name)
    r["model"]["description"] = STATE_TEXT[r["model"]["status"]]
    r["risk_note"] = None if r["result"]["risk_level"] != "pending_validation" else \
        "Risk categories are pending validation for this model; only the binary estimate and probability are shown."
    return r


class BatchIn(BaseModel):
    dataset_id: str = Field(min_length=32, max_length=32)
    save_history: bool = False


@router.post("/predict/batch", status_code=202, dependencies=[Depends(predict_limit)])
def predict_batch(body: BatchIn, c: Container = Depends(get_c)):
    if c.registry.active_version() is None:
        raise HTTPException(409, "No trained model is available. Train a model first.")
    job = js.submit_batch(c, body.dataset_id, body.model_dump(exclude={"dataset_id"}))
    return _job_dict(js.get_job(c, job.id))


# ---------------------------------------------------------------- history, map, admin
def _record(r: PredictionRecord) -> dict:
    return {c_.name: getattr(r, c_.name) for c_ in PredictionRecord.__table__.columns}


@router.get("/history")
def history(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), risk_level: str | None = Query(None, max_length=24),
            source: str | None = Query(None, pattern="^(single|batch|weather)$"), q: str | None = Query(None, max_length=60),
            c: Container = Depends(get_c)):
    qy = select(PredictionRecord).order_by(PredictionRecord.id.desc())
    if risk_level:
        qy = qy.where(PredictionRecord.risk_level == risk_level)
    if source:
        qy = qy.where(PredictionRecord.source == source)
    if q:
        qy = qy.where(PredictionRecord.location_name.ilike(f"%{q.strip()}%"))
    with c.session() as s:
        total = s.scalar(select(func.count()).select_from(qy.subquery()))
        rows = s.scalars(qy.limit(limit).offset(offset)).all()
    return {"total": total, "items": [_record(r) for r in rows]}


@router.get("/history/export.csv")
def history_export(limit: int = Query(5000, ge=1, le=10000), c: Container = Depends(get_c)):
    with c.session() as s:
        rows = s.scalars(select(PredictionRecord).order_by(PredictionRecord.id.desc()).limit(limit)).all()
    cols = ["id", "created_at", "source", "location_name", "model_version", "model_status", "latitude", "longitude", "predicted_class", "probability", "risk_level"]
    df = pd.DataFrame([{k: getattr(r, k) for k in cols} for r in rows], columns=cols)
    return Response(to_safe_csv_bytes(df), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="prediction_history.csv"'})


@router.get("/history/{record_id}")
def history_detail(record_id: int, c: Container = Depends(get_c)):
    with c.session() as s:
        r = s.get(PredictionRecord, record_id)
    if r is None:
        raise HTTPException(404, "Prediction record not found.")
    return {"record": _record(r), "disclaimer": DISCLAIMER}


@router.delete("/history", dependencies=[Depends(require_admin)])
def clear_history(c: Container = Depends(get_c)):
    with c.session() as s:
        n = s.execute(delete(PredictionRecord)).rowcount
    return {"deleted": n}


@router.post("/admin/cleanup", dependencies=[Depends(require_admin)])
def admin_cleanup(c: Container = Depends(get_c)):
    return run_cleanup(c)


# ---------------------------------------------------------------- weather (independent of the ML code)
class FeaturesIn(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    timestamp: str | None = Field(default=None, max_length=40)
    manual: dict[str, float | None] = Field(default_factory=dict, max_length=30)


class WeatherPredictIn(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    save_history: bool = False


def _need(c: Container) -> tuple[list[str], dict | None]:
    v = c.registry.active_version()
    if v:
        md = c.registry.get_metadata(v)
        return list(md["input_columns"]), {"version": v, "status": md["status"]}
    return list(DEFAULT_SCHEMA.feature_names), None


@router.get("/weather", dependencies=[Depends(predict_limit)])
def weather(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180), name: str | None = Query(None, max_length=120),
            fresh: bool = Query(True), c: Container = Depends(get_c)):
    """Weather availability is independent of model coverage; both are reported separately.
    fresh=true (the default for user-triggered requests) bypasses the cache so every click makes a new provider request;
    if that request fails, a cached result may be returned but is flagged stale with its timestamp."""
    fc = c.weather.forecast(lat, lon, fresh=fresh)
    try:
        elev = {"value_m": c.weather.elevation(lat, lon, fresh=fresh), "status": "available", "error": None}
    except WeatherUnavailable as e:
        elev = {"value_m": None, "status": "unavailable", "error": str(e)}
    return {"location": {"name": name, "latitude": lat, "longitude": lon}, "weather": fc, "elevation": elev, "elevation_m": elev["value_m"], "coverage": c.city.coverage(lat, lon),
            "in_configured_region": c.city.contains(lat, lon),
            "note": "Model-derived precipitation is not proof that any street is waterlogged."}


@router.get("/geocode", dependencies=[Depends(predict_limit)])
def geocode(q: str = Query(min_length=2, max_length=100), c: Container = Depends(get_c)):
    res = c.weather.geocode(q)
    for r in res:
        r["coverage_eligible"] = c.city.contains(r["latitude"], r["longitude"])
    res.sort(key=lambda r: not r["coverage_eligible"])  # in-region first, stable otherwise
    return {"results": res[:8]}


@router.post("/features", dependencies=[Depends(predict_limit)])
def features(body: FeaturesIn, c: Container = Depends(get_c)):
    """Resolve model inputs for a point/time with per-feature source labels. Never defaults a value to 0."""
    now = datetime.now(timezone.utc)
    ts = parse_single_timestamp(body.timestamp, c.city.timezone) if body.timestamp else pd.Timestamp(now)
    if ts is None:
        raise HTTPException(422, "Timestamp must be ISO 8601, e.g. 2026-07-01 10:00.")
    need, model = _need(c)
    res = resolve_features(c.weather, body.latitude, body.longitude, ts.to_pydatetime(), need, body.manual, now)
    return {"model": model, "needed_features": need, "timestamp_utc": ts.isoformat(), "ready": not res["missing"],
            "coverage": c.city.coverage(body.latitude, body.longitude), **res}


@router.post("/weather/predict", dependencies=[Depends(predict_limit)])
def weather_predict(body: WeatherPredictIn, c: Container = Depends(get_c)):
    """Compatibility wrapper: resolves features for 'now' and uses the shared single-prediction path."""
    need, model = _need(c)
    if model is None:
        raise NoModelError("No validated model is active. Train and activate a validated model first.")
    now = datetime.now(timezone.utc)
    res = resolve_features(c.weather, body.latitude, body.longitude, now, need, None, now)
    if res["missing"]:
        return {"available": False, "missing_inputs": res["missing"], "features": res["features"], "model": model,
                "reason": "Live prediction is unavailable: these inputs cannot be supplied by the weather provider: "
                          f"{', '.join(res['missing'])}. Enter them manually on the Predictions page."}
    rec = {"timestamp": now.isoformat(), "latitude": body.latitude, "longitude": body.longitude,
           **{k: v["value"] for k, v in res["features"].items() if k not in ("latitude", "longitude")}}
    prov = {k: v["source"] for k, v in res["features"].items()}
    out = run_single(c, rec, "weather", body.save_history, prov)
    out["model"]["description"] = STATE_TEXT[out["model"]["status"]]
    return {"available": True, **out, "inputs_used": {k: v["value"] for k, v in res["features"].items()},
            "warnings": [SOURCE_NOTE] + out["warnings"]}


SOURCE_NOTE = ("Rainfall inputs are model-derived precipitation from the weather provider and may differ from the "
               "rainfall source the model was trained on. The result is an estimate, not a measurement of waterlogging.")


PROBE_INTERVAL_S = 60


@router.get("/weather/status")
def weather_status(c: Container = Depends(get_c)):
    """Health of the weather provider as of the last REAL probe (cache bypassed, at most one probe per minute).
    A success is never inferred from cached data; if only a stale fallback exists the result says stale."""
    now = time.monotonic()
    last = getattr(c, "_weather_probe", None)
    if last and now - last[0] < PROBE_INTERVAL_S:
        return last[1]
    try:
        fc = c.weather.forecast(c.city.center_lat, c.city.center_lon, fresh=True)
        res = {"ok": True, "provider": c.settings.weather_provider, "last_updated_utc": fc["last_updated_utc"], "stale": fc["stale"],
               "checked_at_utc": datetime.now(timezone.utc).isoformat()}
    except WeatherUnavailable as e:
        res = {"ok": False, "provider": c.settings.weather_provider, "error": str(e), "checked_at_utc": datetime.now(timezone.utc).isoformat()}
    c._weather_probe = (now, res)
    return res


@router.get("/jobs/{job_id}/preview")
def job_preview(job_id: str, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
                predicted_class: int | None = Query(None, ge=0, le=1), risk_level: str | None = Query(None, max_length=24),
                c: Container = Depends(get_c)):
    j = js.get_job(c, job_id)
    if j is None or not j.result_keys or "predictions" not in j.result_keys:
        raise HTTPException(404, "Results not found or expired.")
    df = pd.read_csv(io.BytesIO(c.store.get_bytes(j.result_keys["predictions"])))
    if predicted_class is not None:
        df = df[df["predicted_class"] == predicted_class]
    if risk_level:
        df = df[df["risk_level"] == risk_level]
    total = len(df)
    part = df.iloc[(page - 1) * page_size: page * page_size].astype(object).where(lambda x: x.notna(), None)
    return {"total": total, "page": page, "page_size": page_size, "columns": list(df.columns), "rows": part.values.tolist()}


# ---------------------------------------------------------------- datasets (list / preview), models (detail), activity, export
@router.get("/datasets")
def list_datasets(limit: int = Query(20, ge=1, le=100), kind: str | None = Query(None, pattern="^(train|predict)$"),
                  status: str | None = Query(None, pattern="^(validated|invalid)$"), c: Container = Depends(get_c)):
    q = select(Dataset).where(Dataset.status != "expired")
    if kind:
        q = q.where(Dataset.kind == kind)
    if status:
        q = q.where(Dataset.status == status)
    with c.session() as s:
        rows = s.scalars(q.order_by(Dataset.created_at.desc()).limit(limit)).all()
    return {"items": [{"dataset_id": d.id, "kind": d.kind, "name": d.display_name, "status": d.status, "n_rows": d.n_rows,
                       "created_at": d.created_at, "expires_at": d.expires_at,
                       "n_valid": (d.validation or {}).get("n_rows_valid"), "n_rejected": (d.validation or {}).get("n_rows_rejected")}
                      for d in rows]}


@router.get("/datasets/{dataset_id}/preview")
def dataset_preview(dataset_id: str, rows: int = Query(20, ge=1, le=100), c: Container = Depends(get_c)):
    """First rows exactly as uploaded (strings). Bounded; display only."""
    ds = get_dataset(c, dataset_id)
    df = load_dataframe(c, ds).head(rows)
    return {"columns": list(df.columns), "rows": df.values.tolist(), "n_rows_total": ds.n_rows}


@router.get("/models/{version}")
def model_detail(version: str, c: Container = Depends(get_c)):
    try:
        md = c.registry.get_metadata(version)
    except RegistryError:
        raise HTTPException(404, "Unknown model version.")
    keys = ("version", "status", "status_reason", "selected_model", "created_at", "split", "data", "candidates", "test_metrics",
            "test_brier_uncalibrated", "calibration", "gates", "permutation_importance", "input_columns", "feature_columns",
            "caveats", "decision_threshold", "risk_thresholds", "region", "label_provenance", "provenance_attested", "library_versions")
    return {k: md.get(k) for k in keys} | {"active": c.registry.active_version() == version}


@router.get("/activity")
def activity(type: str | None = Query(None, pattern="^(train|batch_predict)$"), limit: int = Query(25, ge=1, le=200),
             offset: int = Query(0, ge=0), c: Container = Depends(get_c)):
    q = select(Job).order_by(Job.created_at.desc())
    if type:
        q = q.where(Job.type == type)
    with c.session() as s:
        total = s.scalar(select(func.count()).select_from(q.subquery()))
        rows = s.scalars(q.limit(limit).offset(offset)).all()
    return {"total": total, "items": [_job_dict(j) for j in rows]}


@router.get("/verification")
def verification():
    """Last recorded verification run (written by the maintainer after actually running the checks)."""
    f = Path(__file__).resolve().parent.parent / "verification.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {"recorded": False}


# ---------------------------------------------------------------- dashboard, contract, explorer
@router.get("/dashboard")
def dashboard(c: Container = Depends(get_c)):
    return build_dashboard(c)


_SOURCE = {"rainfall_1h_mm": ("provider", "Open-Meteo precipitation (model analysis/reanalysis, not gauge data)"),
           "rainfall_24h_mm": ("provider", "Open-Meteo precipitation (model analysis/reanalysis, not gauge data)"),
           "rainfall_72h_mm": ("provider", "Open-Meteo precipitation (model analysis/reanalysis, not gauge data)"),
           "elevation_m": ("provider", "Open-Meteo elevation (DEM)"), "slope_deg": ("derived", "Derived from DEM elevation samples (approximate)"),
           "drainage_capacity": ("user", "No public source: enter from your own/municipal data, in the unit used for training"),
           "latitude": ("location", "The selected location"), "longitude": ("location", "The selected location")}


@router.get("/contract")
def contract(c: Container = Depends(get_c)):
    """The single feature contract for single AND batch prediction: what the active model expects, with units, ranges and sources."""
    need, model = _need(c)
    feats = []
    for n in need:
        spec = DEFAULT_SCHEMA.spec(n)
        src, note = _SOURCE.get(n, ("user", ""))
        feats.append({"name": n, "unit": spec.unit, "description": spec.description, "min": spec.hard_min, "max": spec.hard_max,
                      "source": src, "source_note": note})
    csv_cols = ["timestamp", "latitude", "longitude"] + [n for n in need if n not in ("latitude", "longitude")]
    return {"model": model, "features": feats, "csv_columns": csv_cols, "for_active_model": model is not None,
            "note": None if model else "No validated model is active; this is the default schema, not a model contract."}


@router.get("/datasets/{dataset_id}/explore")
def dataset_explore(dataset_id: str, offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100), c: Container = Depends(get_c)):
    return explore_dataset(c, get_dataset(c, dataset_id), offset, limit)
