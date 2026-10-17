from __future__ import annotations

import hashlib
from datetime import timedelta

from app.container import Container
from app.database.models import Dataset, new_id, utcnow
from app.services.data_validation import validate_dataframe
from app.services.schema import DEFAULT_SCHEMA
from app.upload import display_name, parse_csv


class DatasetNotFound(Exception):
    pass


def register_dataset(c: Container, kind: str, filename: str, data: bytes) -> tuple[Dataset, object]:
    assert kind in ("train", "predict")
    df = parse_csv(data, filename, c.settings.max_csv_rows)
    report = validate_dataframe(df, DEFAULT_SCHEMA, kind, city=c.city, max_rows=c.settings.max_csv_rows)
    did = new_id()
    key = f"uploads/{did}.csv"
    c.store.put_bytes(key, data)
    ds = Dataset(id=did, kind=kind, display_name=display_name(filename), storage_key=key,
                 sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), n_rows=len(df),
                 status="validated" if report.ok else "invalid", validation=report.to_dict(),
                 expires_at=utcnow() + timedelta(hours=c.settings.upload_retention_hours))
    with c.session() as s:
        s.add(ds)
    return ds, report


def get_dataset(c: Container, dataset_id: str) -> Dataset:
    with c.session() as s:
        ds = s.get(Dataset, dataset_id)
    if ds is None or ds.status == "expired":
        raise DatasetNotFound("Dataset not found or expired. Re-upload the CSV.")
    return ds


def load_dataframe(c: Container, ds: Dataset):
    return parse_csv(c.store.get_bytes(ds.storage_key), ds.display_name if ds.display_name.lower().endswith(".csv") else "x.csv",
                     c.settings.max_csv_rows)
