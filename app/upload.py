"""Safe CSV upload parsing. Server-generated storage keys; client filename is display-only."""
from __future__ import annotations

import io
import re

import pandas as pd


class UploadError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def display_name(raw: str | None) -> str:
    name = (raw or "upload.csv").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._ \-]", "_", name).strip(" .") or "upload.csv"
    return name[:100]


async def read_limited(upload, max_bytes: int) -> bytes:
    buf = bytearray()
    while chunk := await upload.read(1024 * 256):
        buf += chunk
        if len(buf) > max_bytes:
            raise UploadError(f"File exceeds the {max_bytes // (1024 * 1024)} MB limit.", 413)
    return bytes(buf)


def parse_csv(data: bytes, filename: str | None, max_rows: int) -> pd.DataFrame:
    if not (filename or "").lower().endswith(".csv"):
        raise UploadError("Only .csv files are accepted.", 415)
    if not data.strip():
        raise UploadError("The file is empty.")
    if b"\x00" in data:
        raise UploadError("The file does not look like a text CSV.", 415)
    try:
        df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, encoding="utf-8-sig",
                         nrows=max_rows + 1, skipinitialspace=True)
    except UnicodeDecodeError:
        raise UploadError("The file is not valid UTF-8 text.")
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as e:
        raise UploadError(f"The CSV could not be parsed: {str(e)[:200]}")
    if len(df) > max_rows:
        raise UploadError(f"The file has more than {max_rows} rows.", 413)
    df.columns = [str(c).strip() for c in df.columns]
    return df
