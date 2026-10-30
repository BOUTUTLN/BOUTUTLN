"""Programmatic Alembic migrations (no alembic.ini needed on Windows)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine

from boututln.database.schema import SCHEMA_HEAD

SCRIPT_LOCATION = Path(__file__).with_name("alembic")


def _config(engine: Engine) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(SCRIPT_LOCATION))
    cfg.attributes["engine"] = engine
    return cfg


def upgrade_to_head(engine: Engine) -> str:
    command.upgrade(_config(engine), "head")
    return current_revision(engine) or ""


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def is_up_to_date(engine: Engine) -> bool:
    return current_revision(engine) == SCHEMA_HEAD
