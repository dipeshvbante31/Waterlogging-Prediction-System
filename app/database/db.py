from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


def _ensure_sqlite_dir(url: str) -> None:
    """Create the directory of a file-based SQLite database so a fresh checkout starts without manual setup."""
    from sqlalchemy.engine import make_url
    db = make_url(url).database
    if db and db != ":memory:":
        Path(db).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def make_engine(url: str):
    if url.startswith("sqlite"):
        _ensure_sqlite_dir(url)
    kw = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        kw["connect_args"] = {"check_same_thread": False, "timeout": 30}
    eng = create_engine(url, **kw)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_con, _):  # better concurrency for a small SQLite file
            cur = dbapi_con.cursor()
            cur.execute("PRAGMA journal_mode=WAL"); cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return eng


def make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
