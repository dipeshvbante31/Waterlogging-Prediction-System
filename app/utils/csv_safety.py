"""CSV export hardening against spreadsheet formula injection."""
from __future__ import annotations

import io

import pandas as pd

_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def _neutralize(v):
    if isinstance(v, str) and v and v.startswith(_DANGEROUS):
        return "'" + v
    return v


def sanitize_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Prefix text cells that a spreadsheet would interpret as formulas.

    Only string cells are touched; numeric columns (including negatives) are untouched.
    """
    out = df.copy()
    out.columns = [_neutralize(c) if isinstance(c, str) else c for c in out.columns]
    for col in out.columns:
        if out[col].dtype == object or pd.api.types.is_string_dtype(out[col]):
            out[col] = out[col].map(_neutralize)
    return out


def to_safe_csv_bytes(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    sanitize_frame(df).to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")
