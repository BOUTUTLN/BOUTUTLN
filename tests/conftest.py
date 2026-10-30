from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from boututln.core.clock import FixedClock
from boututln.core.config import AppConfig, load_config
from boututln.core.masking import clear_registered_secrets
from boututln.database.engine import create_db_engine
from boututln.database.migrations import upgrade_to_head

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
SETTINGS = ROOT / "config" / "settings.toml"

SAFE_ENV = {
    "OLLAMA_NO_CLOUD": "1",
    "OLLAMA_NUM_PARALLEL": "1",
    "OLLAMA_MAX_LOADED_MODELS": "1",
    "OLLAMA_CONTEXT_LENGTH": "8192",
}


def kb_fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / "kb" / f"{name}.json").read_text("utf-8"))
    return data


@pytest.fixture(autouse=True)
def _clean_secrets() -> Iterator[None]:
    yield
    clear_registered_secrets()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 10, 30, 0, 0, tzinfo=UTC))


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    e = create_db_engine(tmp_path / "db" / "paper.db")
    upgrade_to_head(e)
    yield e
    e.dispose()


@pytest.fixture
def cfg(tmp_path: Path) -> AppConfig:
    env = dict(SAFE_ENV, BOUTUTLN_DATA_DIR=str(tmp_path / "data"))
    return load_config([SETTINGS], env)
