"""SQLite engine factory (WAL, foreign keys, busy timeout).

The rest of the code only depends on SQLAlchemy Core, so the database can be
moved to PostgreSQL later by changing the URL (PLAN 16).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def create_db_engine(path: Path, *, echo: bool = False) -> Engine:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(sqlite_url(path), echo=echo, future=True)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn: Any, _record: Any) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=FULL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    return engine
