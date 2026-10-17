from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class UTCDateTime(TypeDecorator):
    """Always returns timezone-aware UTC datetimes (SQLite drops tzinfo)."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc) if value is not None else None

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class Base(DeclarativeBase):
    pass


class Dataset(Base):
    """Metadata for an uploaded CSV. The bytes live in the artifact store and EXPIRE."""
    __tablename__ = "datasets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(10))                       # train | predict
    display_name: Mapped[str] = mapped_column(String(120))
    storage_key: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    n_rows: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(12), default="uploaded", index=True)  # uploaded|validated|invalid|expired
    validation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    type: Mapped[str] = mapped_column(String(20), index=True)          # train | batch_predict
    status: Mapped[str] = mapped_column(String(12), default="queued", index=True)  # queued|running|succeeded|failed|abandoned
    dataset_id: Mapped[str | None] = mapped_column(ForeignKey("datasets.id"), nullable=True)
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)      # user-safe message only
    result_keys: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # name -> storage key
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(20))                    # validated | experimental
    selected_model: Mapped[str] = mapped_column(String(40))
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # key metrics for listings
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    train_job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class PredictionRecord(Base):
    __tablename__ = "predictions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(10))                    # single | batch | weather
    location_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model_version: Mapped[str] = mapped_column(String(64), index=True)
    model_status: Mapped[str] = mapped_column(String(20))
    row_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ts: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    inputs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    predicted_class: Mapped[int] = mapped_column(Integer)
    probability: Mapped[float] = mapped_column(Float)
    risk_level: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class CleanupLog(Base):
    __tablename__ = "cleanup_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ran_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    files_deleted: Mapped[int] = mapped_column(Integer, default=0)
    datasets_expired: Mapped[int] = mapped_column(Integer, default=0)
    jobs_abandoned: Mapped[int] = mapped_column(Integer, default=0)
    history_pruned: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
