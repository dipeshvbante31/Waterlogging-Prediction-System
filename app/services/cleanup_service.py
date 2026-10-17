"""Safe retention cleanup. Never touches data used by queued/running jobs."""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import delete, select, update

from app.container import Container
from app.database.models import CleanupLog, Dataset, Job, PredictionRecord, utcnow
from app.services.job_service import ACTIVE, recover_abandoned
from app.storage import StorageError

log = logging.getLogger("wps.cleanup")


def run_cleanup(c: Container) -> dict:
    now = utcnow()
    stats = dict(files_deleted=0, datasets_expired=0, jobs_abandoned=0, history_pruned=0, errors=0)
    stats["jobs_abandoned"] = recover_abandoned(c)

    with c.session() as s:
        busy = set(s.scalars(select(Job.dataset_id).where(Job.status.in_(ACTIVE), Job.dataset_id.is_not(None))))
        expired = list(s.scalars(select(Dataset).where(Dataset.expires_at < now, Dataset.status != "expired")))
    for ds in expired:
        if ds.id in busy:
            continue  # never delete a file that an active job still needs
        try:
            if c.store.delete(ds.storage_key):
                stats["files_deleted"] += 1
            with c.session() as s:
                s.execute(update(Dataset).where(Dataset.id == ds.id).values(status="expired"))
            stats["datasets_expired"] += 1
        except (StorageError, OSError):
            stats["errors"] += 1
            log.warning("cleanup failed for dataset=%s; will retry next run", ds.id)

    with c.session() as s:
        old_jobs = list(s.scalars(select(Job).where(Job.expires_at < now, Job.status.not_in(ACTIVE), Job.result_keys.is_not(None))))
    for job in old_jobs:
        try:
            for key in (job.result_keys or {}).values():
                if c.store.delete(key):
                    stats["files_deleted"] += 1
            with c.session() as s:
                s.execute(update(Job).where(Job.id == job.id).values(result_keys=None))
        except (StorageError, OSError):
            stats["errors"] += 1

    cutoff = now - timedelta(days=c.settings.history_retention_days)
    with c.session() as s:
        stats["history_pruned"] = s.execute(delete(PredictionRecord).where(PredictionRecord.created_at < cutoff)).rowcount
        s.add(CleanupLog(**stats))
    log.info("cleanup %s", stats)
    return stats
