"""Read-only dataset exploration for the Data Explorer: structure, per-column quality, statistics, preview.
Nothing is modified or guessed; the file is re-validated with the same rules used for training/prediction."""
from __future__ import annotations

import pandas as pd

from app.container import Container
from app.database.models import Dataset
from app.services.data_validation import resolve_columns, validate_dataframe
from app.services.dataset_service import load_dataframe
from app.services.schema import DEFAULT_SCHEMA


def explore(c: Container, ds: Dataset, offset: int = 0, limit: int = 25) -> dict:
    raw = load_dataframe(c, ds)
    rep = validate_dataframe(raw, DEFAULT_SCHEMA, ds.kind, city=c.city, max_rows=c.settings.max_csv_rows)
    rename, _ = resolve_columns(list(raw.columns), DEFAULT_SCHEMA)
    cols = []
    for col in raw.columns:
        s = raw[col].astype(str).str.strip()
        blank = int((s == "").sum())
        nonblank = s[s != ""]
        numeric = pd.to_numeric(nonblank, errors="coerce")
        looks_numeric = len(nonblank) > 0 and numeric.notna().mean() > 0.95
        canon = rename.get(col)
        invalid = sum(v for k, v in rep.rejection_counts.items() if canon and k.endswith(":" + canon) and not k.startswith("missing"))
        cols.append({"name": col, "recognised_as": canon, "type": "number" if looks_numeric else "text", "missing": blank,
                     "missing_pct": round(100 * blank / max(len(raw), 1), 2), "invalid_rows": int(invalid)})
    required = [{"name": n, "present": n in rename.values()} for n in DEFAULT_SCHEMA.required_columns(ds.kind)]
    stats = []
    if len(rep.clean):
        num = rep.clean[[f for f in rep.present_features if f in rep.clean.columns]]
        for name, col in num.items():
            d = col.describe()
            stats.append({"feature": name, "unit": DEFAULT_SCHEMA.spec(name).unit, "count": int(d["count"]),
                          **{k: (None if pd.isna(d[k]) else round(float(d[k]), 4)) for k in ("mean", "std", "min", "25%", "50%", "75%", "max")}})
    part = raw.iloc[offset: offset + limit]
    return {"dataset_id": ds.id, "kind": ds.kind, "status": ds.status, "name": ds.display_name, "n_rows": int(len(raw)), "n_columns": int(raw.shape[1]),
            "required_columns": required, "columns": cols, "statistics": stats, "validation": rep.to_dict(),
            "preview": {"columns": [str(x) for x in raw.columns], "rows": part.values.tolist(), "offset": offset, "limit": limit}}
