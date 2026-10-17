"""CSV/DataFrame validation: strict, explainable, never silently repairs data."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.cities import CityProfile
from app.services.schema import DatasetSchema, normalize_name

_TZ_SUFFIX = re.compile(r"[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?\s*(?:Z|[+-]\d{2}(?::?\d{2})?)$")
_TARGET_MAP = {"0": 0, "1": 1, "0.0": 0, "1.0": 1, "true": 1, "false": 0}


class ValidationError(Exception):
    pass


@dataclass
class ValidationReport:
    mode: str
    ok: bool
    fatal_errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    n_rows_input: int = 0
    n_rows_valid: int = 0
    n_rows_rejected: int = 0
    rejection_counts: dict = field(default_factory=dict)
    missing_by_column: dict = field(default_factory=dict)
    class_balance: dict | None = None
    ignored_columns: list = field(default_factory=list)
    column_mapping: dict = field(default_factory=dict)
    present_features: list = field(default_factory=list)
    out_of_region_rows: int = 0
    time_range: list | None = None
    clean: pd.DataFrame = field(default_factory=pd.DataFrame, repr=False)
    rejected: pd.DataFrame = field(default_factory=pd.DataFrame, repr=False)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k not in ("clean", "rejected")}


def resolve_columns(columns, schema: DatasetSchema, mapping: dict | None = None):
    """Map uploaded column names to canonical names.

    Only (a) exact canonical names after case/space normalisation, (b) the schema's
    explicit aliases, and (c) an explicit user mapping are applied. Nothing is guessed.
    """
    errors, rename = [], {}
    canon = {f.name: f for f in schema.features}
    alias = {a: f.name for f in schema.features for a in f.aliases}
    targets = set(canon) | {schema.target}
    if mapping:
        cols = set(map(str, columns))
        for src, dst in mapping.items():
            if src not in cols:
                errors.append(f"Mapping refers to unknown column '{src}'.")
            elif dst not in targets:
                errors.append(f"Mapping target '{dst}' is not a schema field.")
            else:
                rename[src] = dst
    for col in columns:
        col = str(col)
        if col in rename:
            continue
        n = normalize_name(col)
        if n in targets:
            rename[col] = n
        elif n in alias:
            rename[col] = alias[n]
    seen = {}
    for src, dst in rename.items():
        if dst in seen:
            errors.append(f"Columns '{seen[dst]}' and '{src}' both map to '{dst}'.")
        seen[dst] = src
    return rename, errors


def _blank(s: pd.Series) -> pd.Series:
    return s.isna() | (s.astype(str).str.strip() == "")


def _parse_timestamps(s: pd.Series, tz: str) -> pd.Series:
    txt = s.astype(str).str.strip()
    has_tz = txt.str.contains(_TZ_SUFFIX)
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns, UTC]")
    blank = _blank(s)
    m1 = has_tz & ~blank
    if m1.any():
        out.loc[m1] = pd.to_datetime(txt[m1], format="ISO8601", errors="coerce", utc=True).astype("datetime64[ns, UTC]")
    m2 = ~has_tz & ~blank
    if m2.any():
        naive = pd.to_datetime(txt[m2], format="ISO8601", errors="coerce")
        out.loc[m2] = naive.dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC").astype("datetime64[ns, UTC]")
    return out


def validate_dataframe(df: pd.DataFrame, schema: DatasetSchema, mode: str = "train",
                       mapping: dict | None = None, city: CityProfile | None = None,
                       max_rows: int | None = None) -> ValidationReport:
    assert mode in ("train", "predict")
    df = df.reset_index(drop=True)  # row_id is the 1-based positional data-row number
    rep = ValidationReport(mode=mode, ok=False, n_rows_input=len(df))
    tz = city.timezone if city else "Asia/Kolkata"

    if len(df) == 0:
        rep.fatal_errors.append("The file contains no data rows.")
        return rep
    if max_rows is not None and len(df) > max_rows:
        rep.fatal_errors.append(f"The file has {len(df)} rows; the limit is {max_rows}.")
        return rep

    rename, errs = resolve_columns(list(df.columns), schema, mapping)
    rep.fatal_errors += errs
    rep.column_mapping = {k: v for k, v in rename.items() if k != v}
    missing_cols = [c for c in schema.required_columns(mode) if c not in rename.values()]
    for c in missing_cols:
        rep.fatal_errors.append(f"Missing required column '{c}'.")
    present = [f.name for f in schema.features if f.kind == "feature" and f.name in rename.values()]
    rep.present_features = present
    if mode == "train" and not present and not missing_cols:
        rep.fatal_errors.append("No model input feature columns were found (e.g. rainfall_24h_mm).")
    rep.ignored_columns = [str(c) for c in df.columns if str(c) not in rename]
    if rep.ignored_columns:
        rep.warnings.append(f"Ignored {len(rep.ignored_columns)} unrecognised column(s): {rep.ignored_columns[:10]}")
    for f in schema.features:
        if f.kind == "feature" and f.name not in present:
            rep.warnings.append(f"Optional feature '{f.name}' not provided.")
    if rep.fatal_errors:
        return rep

    inv = {v: k for k, v in rename.items()}
    work = pd.DataFrame({dst: df[src] for dst, src in inv.items()})
    work.index = df.index
    n = len(work)
    row_id = pd.Series(np.arange(1, n + 1), index=work.index)  # 1-based data-row number
    reason = np.array([""] * n, dtype=object)
    counts: dict[str, int] = {}

    def flag(name: str, mask: pd.Series):
        nonlocal reason
        m = np.asarray(mask, dtype=bool)
        if m.any():
            counts[name] = counts.get(name, 0) + int(m.sum())
            reason = np.where(m, np.where(reason == "", name, reason + ";" + name), reason)

    # timestamp
    ts = _parse_timestamps(work["timestamp"], tz)
    flag("missing:timestamp", _blank(work["timestamp"]))
    flag("invalid_timestamp", ts.isna() & ~_blank(work["timestamp"]))
    work["timestamp"] = ts

    # numeric columns incl. coordinates
    for f in schema.features:
        if f.kind == "core" and f.name == "timestamp":
            continue
        if f.name not in work.columns:
            continue
        s = work[f.name]
        blank = _blank(s)
        num = pd.to_numeric(s, errors="coerce")
        num = num.where(np.isfinite(num))
        if f.required:
            flag(f"missing:{f.name}", blank)
        flag(f"non_numeric:{f.name}", ~blank & num.isna())
        lo = f.hard_min if f.hard_min is not None else -np.inf
        hi = f.hard_max if f.hard_max is not None else np.inf
        flag(f"out_of_range:{f.name}", num.notna() & ((num < lo) | (num > hi)))
        if f.soft_max is not None:
            k = int((num > f.soft_max).sum())
            if k:
                rep.warnings.append(f"{k} row(s) have {f.name} > {f.soft_max} {f.unit} (suspiciously high).")
        work[f.name] = num

    # cumulative rainfall windows must be non-decreasing
    rain = sorted([f for f in schema.features if f.window_hours and f.name in work.columns], key=lambda f: f.window_hours)
    for a, b in zip(rain, rain[1:]):
        flag("inconsistent_rainfall_windows", work[a.name].notna() & work[b.name].notna() & (work[a.name] > work[b.name] + 1e-6))

    # target
    if mode == "train":
        t = work[schema.target]
        blank = _blank(t)
        mapped = t.astype(str).str.strip().str.lower().map(_TARGET_MAP)
        flag(f"missing:{schema.target}", blank)
        flag("invalid_target", ~blank & mapped.isna())
        work[schema.target] = mapped
    elif schema.target in work.columns:
        work = work.drop(columns=[schema.target])
        rep.warnings.append("Target column present in prediction file; ignored.")

    # duplicates among otherwise valid rows
    ok_now = pd.Series(reason == "", index=work.index)
    cols_all = [c for c in work.columns]
    dup_exact = work[ok_now].duplicated(subset=cols_all, keep="first")
    flag("duplicate", dup_exact.reindex(work.index, fill_value=False))
    if mode == "train":
        ok_now = pd.Series(reason == "", index=work.index)
        sub = work[ok_now].copy()
        sub["_lat"], sub["_lon"] = sub["latitude"].round(6), sub["longitude"].round(6)
        grp = sub.groupby(["timestamp", "_lat", "_lon"])[schema.target]
        conflict = grp.transform("nunique") > 1
        flag("conflicting_label", conflict.reindex(work.index, fill_value=False))
        feats = [c for c in present]
        if feats:
            ok_now = pd.Series(reason == "", index=work.index)
            sub = work[ok_now].copy()
            sub["_lat"], sub["_lon"] = sub["latitude"].round(6), sub["longitude"].round(6)
            kd = sub.duplicated(subset=["timestamp", "_lat", "_lon"], keep=False)
            if kd.sum():
                rep.warnings.append(f"{int(kd.sum())} rows share timestamp+location with another row but differ in features.")

    rejected_mask = reason != ""
    rep.rejection_counts = counts
    rep.n_rows_rejected = int(rejected_mask.sum())
    rep.n_rows_valid = n - rep.n_rows_rejected

    clean = work.loc[~rejected_mask].copy()
    clean.insert(0, "row_id", row_id[~rejected_mask].astype(int))
    if mode == "train":
        clean[schema.target] = clean[schema.target].astype(int)
    for c in present:
        clean[c] = clean[c].astype(float)
    rep.clean = clean.reset_index(drop=True)
    rej = df.loc[rejected_mask].copy()
    rej.insert(0, "row_id", row_id[rejected_mask].astype(int).values)
    rej["rejection_reason"] = reason[rejected_mask]
    rep.rejected = rej.reset_index(drop=True)

    if rep.n_rows_valid == 0:
        rep.fatal_errors.append("No valid rows remain after validation.")
    else:
        miss = {}
        for c in present:
            k = int(rep.clean[c].isna().sum())
            miss[c] = {"count": k, "fraction": round(k / len(rep.clean), 4)}
        rep.missing_by_column = miss
        rep.time_range = [rep.clean["timestamp"].min().isoformat(), rep.clean["timestamp"].max().isoformat()]
        if city is not None:
            oor = ~rep.clean.apply(lambda r: city.contains(r["latitude"], r["longitude"]), axis=1)
            rep.out_of_region_rows = int(oor.sum())
            if rep.out_of_region_rows:
                rep.warnings.append(f"{rep.out_of_region_rows} row(s) lie outside the configured region '{city.name}'.")
        if mode == "train":
            y = rep.clean[schema.target]
            pos = int(y.sum()); neg = int(len(y) - pos)
            rep.class_balance = {"positive": pos, "negative": neg, "positive_rate": round(pos / len(y), 4)}
            if pos == 0 or neg == 0:
                rep.fatal_errors.append("Training needs both classes (0 and 1); only one class is present.")
    if rep.n_rows_rejected:
        rep.warnings.append(f"{rep.n_rows_rejected} row(s) rejected; see the rejected-rows report.")
    rep.ok = not rep.fatal_errors
    return rep


def parse_single_timestamp(text: str, tz: str = "Asia/Kolkata"):
    """Parse one ISO-8601 timestamp to a UTC Timestamp; returns None if invalid/blank."""
    if text is None or not str(text).strip():
        return None
    t = _parse_timestamps(pd.Series([str(text)]), tz).iloc[0]
    return None if pd.isna(t) else t


def humanize_reason(code: str, schema: DatasetSchema) -> str:
    """Turn a machine rejection code into a sentence an ordinary user can act on."""
    kind, _, col = code.partition(":")
    spec = next((x for x in schema.features if x.name == col), None)
    rng = ""
    if spec and (spec.hard_min is not None or spec.hard_max is not None):
        rng = f" (plausible range {spec.hard_min if spec.hard_min is not None else '−∞'}–{spec.hard_max if spec.hard_max is not None else '∞'} {spec.unit})"
    return {
        "missing": f"{col} is required but empty",
        "non_numeric": f"{col} must be a number",
        "out_of_range": f"{col} is outside the allowed range{rng}",
        "invalid_timestamp": "the timestamp must be ISO 8601, e.g. 2026-07-01 10:00",
        "inconsistent_rainfall_windows": "rainfall totals must not decrease with a longer window (1 h ≤ 24 h ≤ 72 h)",
        "no_model_inputs": "none of the model's input values were provided",
        "duplicate": "duplicate of an earlier row",
        "conflicting_label": "the same time and place has conflicting labels",
        "invalid_target": "the label must be 0 or 1",
    }.get(kind, code)
