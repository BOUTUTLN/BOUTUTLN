from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from boututln.core.clock import FixedClock
from boututln.core.config import AppConfig
from boututln.core.env_check import has_failures, run_checks
from boututln.core.market_calendar import ExchangeCalendar, StaticCalendar
from boututln.dashboard.api import create_app
from boututln.database import repositories as repo
from boututln.database import schema as s
from boututln.database.engine import create_db_engine
from boututln.database.migrations import upgrade_to_head
from boututln.domain.market import Market
from boututln.providers.base import Bar
from boututln.scheduler.daily import JOB_NAME, markets_ready, p0_steps
from boututln.scheduler.runner import (
    ConcurrentRunError,
    DailyJobRunner,
    Step,
    StepContext,
    snapshot_id_for,
)
from tests.conftest import SAFE_ENV

DAY = date(2026, 10, 30)


class Boom(Exception):
    pass


def counting_steps(calls: list[str], fail_at: str | None = None) -> list[Step]:
    def mk(name: str) -> Step:
        def run(ctx: StepContext) -> dict[str, Any]:
            calls.append(name)
            if name == fail_at:
                raise Boom(f"{name} crashed, token=abc123secret")
            return {"done": name}

        return Step(name, run)

    return [mk("collect"), mk("signals"), mk("ai"), mk("orders")]


def test_run_completes_and_duplicate_is_blocked(engine: Engine, clock: FixedClock) -> None:
    calls: list[str] = []
    r = DailyJobRunner(engine, "job", clock=clock)
    out = r.run(DAY, "snap", counting_steps(calls))
    assert out.status == "completed" and out.executed_steps == [
        "collect",
        "signals",
        "ai",
        "orders",
    ]
    again = r.run(DAY, "snap", counting_steps(calls))
    assert again.status == "already_completed" and len(calls) == 4


def test_crash_then_resume_from_checkpoint(engine: Engine, clock: FixedClock) -> None:
    calls: list[str] = []
    r = DailyJobRunner(engine, "job", clock=clock)
    out = r.run(DAY, "snap", counting_steps(calls, fail_at="ai"))
    assert out.status == "failed" and "abc123secret" not in (out.error or "")
    calls.clear()
    out2 = r.run(DAY, "snap", counting_steps(calls))
    assert out2.resumed and out2.skipped_steps == ["collect", "signals"]
    assert calls == ["ai", "orders"] and out2.status == "completed"
    assert out2.results["collect"] == {"done": "collect"}  # restored from checkpoint


def test_changed_snapshot_discards_old_run(engine: Engine, clock: FixedClock) -> None:
    r = DailyJobRunner(engine, "job", clock=clock)
    r.run(DAY, "snap-a", counting_steps([], fail_at="signals"))
    calls: list[str] = []
    out = r.run(DAY, "snap-b", counting_steps(calls))
    assert not out.resumed and calls == ["collect", "signals", "ai", "orders"]
    with engine.connect() as c:
        statuses = c.execute(select(s.job_runs.c.status).order_by(s.job_runs.c.id)).scalars().all()
    assert statuses == ["discarded", "completed"]


def test_concurrent_run_rejected_and_pc_restart_recovery(engine: Engine, clock: FixedClock) -> None:
    r = DailyJobRunner(engine, "job", clock=clock)
    with engine.begin() as c:  # a run that was in progress when the PC switched off
        c.execute(
            s.job_runs.insert().values(
                job_name="job",
                run_date=DAY.isoformat(),
                data_snapshot_id="snap",
                status="running",
                started_at=clock.now(),
            )
        )
    with pytest.raises(ConcurrentRunError):
        r.run(DAY, "snap", counting_steps([]))
    clock.advance(timedelta(hours=4))  # PC restarted much later
    assert r.recover_interrupted() != []
    out = r.run(DAY, "snap", counting_steps([]))
    assert out.status == "completed" and out.resumed


def test_restart_with_new_engine_resumes(tmp_path: Path, clock: FixedClock) -> None:
    path = tmp_path / "db" / "paper.db"
    e1 = create_db_engine(path)
    upgrade_to_head(e1)
    DailyJobRunner(e1, "job", clock=clock).run(DAY, "s", counting_steps([], fail_at="orders"))
    e1.dispose()  # simulate process exit / reboot
    e2 = create_db_engine(path)
    calls: list[str] = []
    out = DailyJobRunner(e2, "job", clock=clock).run(DAY, "s", counting_steps(calls))
    assert calls == ["orders"] and out.status == "completed"
    e2.dispose()


def _bar(market: Market, symbol: str, day: date) -> Bar:
    sess = ExchangeCalendar().session(market, day)
    return Bar(
        market=market,
        symbol=symbol,
        timeframe="1d",
        bar_time=sess.close_utc,
        open="10",
        high="11",
        low="9",
        close="10",
        volume="1",
        currency=market.currency,
        source="t",
        available_at=sess.close_utc + timedelta(hours=1),
        ingested_at=sess.close_utc + timedelta(hours=1),
    )  # type: ignore[arg-type]


def test_daily_pipeline_skips_holiday_and_stale_markets(engine: Engine, clock: FixedClock) -> None:
    cal = ExchangeCalendar()
    with engine.begin() as c:
        repo.insert_bars(
            c,
            [
                _bar(Market.KR, "005930", date(2026, 10, 29)),
                _bar(Market.US, "SPY", date(2026, 10, 27)),
            ],
        )  # US data is stale
    required = {Market.KR: ["005930"], Market.US: ["SPY"]}
    out = DailyJobRunner(engine, JOB_NAME, clock=clock).run(DAY, "s", p0_steps(cal, required))
    fr = out.results["data_freshness"]
    assert fr["KR"]["status"] == "ready" and fr["US"]["status"] == "skipped_stale"
    assert markets_ready(out.results) == ["KR"]
    holiday = DailyJobRunner(engine, JOB_NAME, clock=clock).run(
        date(2026, 10, 9), "s2", p0_steps(cal, required)
    )  # Hangul day: KR closed
    assert holiday.results["data_freshness"]["KR"]["status"] == "skipped_holiday"


def test_snapshot_id_is_deterministic() -> None:
    t = datetime(2026, 10, 29, 6, 30, tzinfo=UTC)
    assert snapshot_id_for(DAY, {"a": t}) == snapshot_id_for(DAY, {"a": t})
    assert snapshot_id_for(DAY, {"a": t}) != snapshot_id_for(DAY, {"a": t + timedelta(days=1)})
    assert StaticCalendar({}).trading_days(Market.KR, DAY, DAY) == []


# --- PC environment check -------------------------------------------------------------


def test_env_check_good_windows_setup(cfg: AppConfig) -> None:
    checks = run_checks(
        cfg,
        dict(SAFE_ENV),
        system="Windows",
        python_version=(3, 12),
        which=lambda name: f"C:/bin/{name}.exe",
    )
    assert not has_failures(checks)
    assert {c.name for c in checks} >= {"python", "os", "OLLAMA_NO_CLOUD", "data_dir", "keyring"}


def test_env_check_flags_problems(cfg: AppConfig) -> None:
    bad_env = {"OLLAMA_HOST": "0.0.0.0", "LIVE_TRADING_ENABLED": "1"}
    checks = run_checks(
        cfg, bad_env, system="Windows", python_version=(3, 13), which=lambda name: None
    )
    failed = {c.name for c in checks if c.status == "fail"}
    assert {"python", "OLLAMA_NO_CLOUD", "environment"} <= failed


# --- local dashboard ---------------------------------------------------------------------


def test_dashboard_status_endpoints(cfg: AppConfig, engine: Engine) -> None:
    client = TestClient(create_app(cfg, engine))
    assert client.get("/health").json() == {"ok": True, "schema": "0001"}
    st = client.get("/status").json()
    assert st["live_trading"] == "locked" and st["halt_levels"]["global"] == "NONE"
    costs = client.get("/costs").json()
    assert {"name": "kb_openapi", "cost_class": "unknown", "label": "확인 필요"} in costs[
        "non_zero_or_unconfirmed"
    ]
    assert client.get("/docs").status_code == 404
