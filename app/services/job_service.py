"""Background jobs with persisted status. Claiming is an atomic compare-and-set in the DB,
so concurrent requests/workers can never run the same job twice."""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta

import pandas as pd
from sqlalchemy import select, update

from app.container import Container
from app.database.models import Job, ModelVersion, PredictionRecord, utcnow
from app.services.data_validation import validate_dataframe
from app.services.dataset_service import DatasetNotFound, get_dataset, load_dataframe
from app.services.model_registry import RegistryError
from app.services.prediction_service import (IncompatibleInputError, NoModelError, predict_dataframe)
from app.services.schema import DEFAULT_SCHEMA
from app.services.training_service import TrainConfig, TrainingError, train_models
from app.utils.csv_safety import to_safe_csv_bytes

log = logging.getLogger("wps.jobs")
ACTIVE = ("queued", "running")
MAX_HISTORY_ROWS_PER_JOB = 5000


class JobConflict(Exception):
    pass


def _set(c, job_id, **kw):
    with c.session() as s:
        s.execute(update(Job).where(Job.id == job_id).values(**kw))


def _claim(c, job_id) -> bool:
    with c.session() as s:
        r = s.execute(update(Job).where(Job.id == job_id, Job.status == "queued")
                      .values(status="running", started_at=utcnow(), heartbeat_at=utcnow()))
        return r.rowcount == 1


def get_job(c: Container, job_id: str) -> Job | None:
    with c.session() as s:
        return s.get(Job, job_id)


def _create(c, type_, dataset_id, params, exclusive=False) -> Job:
    with c.session() as s:
        if exclusive and s.scalar(select(Job.id).where(Job.type == type_, Job.status.in_(ACTIVE)).limit(1)):
            raise JobConflict("A job of this type is already queued or running. Try again when it finishes.")
        job = Job(type=type_, dataset_id=dataset_id, params=params,
                  expires_at=utcnow() + timedelta(hours=c.settings.upload_retention_hours))
        s.add(job)
        s.flush()
        return job


def _dispatch(c, pool, fn, job_id):
    if c.run_jobs_inline:
        fn(c, job_id)
    else:
        pool.submit(fn, c, job_id)


# ------------------------------------------------------------------ training
_TRAIN_PARAMS = {"split_strategy", "label_provenance", "provenance_attested", "exclude_features"}


def submit_train(c: Container, dataset_id: str, params: dict) -> Job:
    ds = get_dataset(c, dataset_id)
    if ds.kind != "train" or ds.status != "validated":
        raise TrainingError("The dataset must be a validated training dataset.")
    clean_params = {k: v for k, v in params.items() if k in _TRAIN_PARAMS}
    if not str(clean_params.get("label_provenance", "")).strip():
        raise TrainingError("Label provenance is required: describe where the 0/1 labels come from before training.")
    job = _create(c, "train", dataset_id, clean_params, exclusive=True)
    _dispatch(c, c.train_pool, run_train, job.id)
    return job


def run_train(c: Container, job_id: str):
    if not _claim(c, job_id):
        return
    try:
        job = get_job(c, job_id)
        ds = get_dataset(c, job.dataset_id)
        report = validate_dataframe(load_dataframe(c, ds), DEFAULT_SCHEMA, "train", city=c.city,
                                    max_rows=c.settings.max_csv_rows)
        if not report.ok:
            raise TrainingError("; ".join(report.fatal_errors))
        _set(c, job_id, heartbeat_at=utcnow())
        params = dict(job.params)
        excl = tuple(params.pop("exclude_features", None) or ())
        cfg = TrainConfig(**params)
        cfg.features.exclude = excl
        res = train_models(report.clean, DEFAULT_SCHEMA, cfg, c.city)
        _set(c, job_id, heartbeat_at=utcnow())
        if res.model is None:
            out = {"outcome": "no_eligible_model", "status": res.status, "reason": res.status_reason,
                   "candidates": res.metadata["candidates"]}
        else:
            version = c.registry.save(res)
            md = res.metadata
            with c.session() as s:
                s.add(ModelVersion(version=version, status=res.status, selected_model=md["selected_model"],
                                   train_job_id=job_id, summary={
                                       "test_pr_auc": md["test_metrics"]["pr_auc"], "test_recall": md["test_metrics"]["recall"],
                                       "test_precision": md["test_metrics"]["precision"], "n_rows": md["data"]["n_rows"]}))
            out = {"outcome": "model_saved", "version": version, "status": res.status, "reason": res.status_reason,
                   "selected_model": md["selected_model"], "test_metrics": md["test_metrics"],
                   "active": c.registry.active_version() == version}
        _set(c, job_id, status="succeeded", result=out, finished_at=utcnow())
    except (TrainingError, DatasetNotFound, RegistryError) as e:
        _set(c, job_id, status="failed", error=str(e)[:500], finished_at=utcnow())
    except Exception:
        ref = uuid.uuid4().hex[:8]
        log.exception("train job crashed job=%s ref=%s", job_id, ref)
        _set(c, job_id, status="failed", error=f"Unexpected error while training (ref {ref}).", finished_at=utcnow())


# ------------------------------------------------------------------ batch prediction
def submit_batch(c: Container, dataset_id: str, params: dict) -> Job:
    ds = get_dataset(c, dataset_id)
    if ds.status != "validated":
        raise IncompatibleInputError("The dataset did not pass validation.")
    job = _create(c, "batch_predict", dataset_id, {"save_history": bool(params.get("save_history"))})
    _dispatch(c, c.batch_pool, run_batch, job.id)
    return job


def history_rows(out, job_id, source, input_cols, provenance=None, location_name=None):
    rows = []
    res = out.results.head(MAX_HISTORY_ROWS_PER_JOB)
    clean = out.report.clean.set_index("row_id")
    for r in res.itertuples(index=False):
        d = r._asdict()
        src = clean.loc[d["row_id"]]
        rows.append(PredictionRecord(
            job_id=job_id, source=source, location_name=(location_name or None), model_version=d["model_version"], model_status=d["model_status"],
            row_id=int(d["row_id"]), ts=src["timestamp"].to_pydatetime(), latitude=float(src["latitude"]),
            longitude=float(src["longitude"]),
            inputs={**{k: (None if pd.isna(src[k]) else float(src[k])) for k in input_cols if k in src.index},
                    **({"_sources": provenance} if provenance else {})},
            predicted_class=int(d["predicted_class"]), probability=float(d["probability"]), risk_level=d["risk_level"]))
    return rows


def run_batch(c: Container, job_id: str):
    if not _claim(c, job_id):
        return
    try:
        job = get_job(c, job_id)
        ds = get_dataset(c, job.dataset_id)
        bundle = c.registry.load()
        out = predict_dataframe(load_dataframe(c, ds), bundle, city=c.city, max_rows=c.settings.max_csv_rows)
        keys = {"predictions": f"results/{job_id}/predictions.csv", "rejected": f"results/{job_id}/rejected.csv"}
        c.store.put_bytes(keys["predictions"], to_safe_csv_bytes(out.results))
        c.store.put_bytes(keys["rejected"], to_safe_csv_bytes(out.rejected))
        saved = 0
        if job.params.get("save_history"):
            rows = history_rows(out, job_id, "batch", bundle.metadata["input_columns"])
            with c.session() as s:
                s.add_all(rows)
            saved = len(rows)
        r = out.results
        summary = {"n_input": out.report.n_rows_input, "n_predicted": int(len(r)), "n_rejected": int(len(out.rejected)),
                   "rejection_counts": out.report.rejection_counts, "model_version": out.model_version,
                   "model_status": out.model_status, "positive_predictions": int(r["predicted_class"].sum()),
                   "risk_counts": {k: int(v) for k, v in r["risk_level"].value_counts().items()},
                   "history_saved": saved, "warnings": out.report.warnings[:10]}
        _set(c, job_id, status="succeeded", result=summary, result_keys=keys, finished_at=utcnow(),
             expires_at=utcnow() + timedelta(hours=c.settings.upload_retention_hours))
    except (NoModelError, IncompatibleInputError, DatasetNotFound, RegistryError) as e:
        _set(c, job_id, status="failed", error=str(e)[:500], finished_at=utcnow())
    except Exception:
        ref = uuid.uuid4().hex[:8]
        log.exception("batch job crashed job=%s ref=%s", job_id, ref)
        _set(c, job_id, status="failed", error=f"Unexpected error while predicting (ref {ref}).", finished_at=utcnow())


def recover_abandoned(c: Container) -> int:
    """Jobs left 'running' by a crashed/restarted process; queued jobs lost with the process too."""
    cutoff = utcnow() - timedelta(minutes=c.settings.job_stale_minutes)
    with c.session() as s:
        r1 = s.execute(update(Job).where(Job.status == "running", Job.heartbeat_at < cutoff)
                       .values(status="abandoned", error="Job was interrupted (process restart or timeout).", finished_at=utcnow()))
        r2 = s.execute(update(Job).where(Job.status == "queued", Job.created_at < cutoff)
                       .values(status="abandoned", error="Job never started (process restart).", finished_at=utcnow()))
        return r1.rowcount + r2.rowcount
