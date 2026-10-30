"""Daily job runner with checkpoints, duplicate-run prevention and recovery.

- One job run per (job_name, run_date). A completed run is never repeated.
- Each step writes a checkpoint. After a crash or reboot the same date and
  data snapshot resume from the first unfinished step; a different snapshot
  discards the old run and starts over (PLAN 14.1).
- A run that is still marked ``running`` but older than ``stale_after`` is
  treated as interrupted (the PC was switched off).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import Connection, Engine, select
from sqlalchemy.exc import IntegrityError

from boututln.core.clock import Clock, SystemClock
from boututln.core.errors import BoututlnError
from boututln.core.masking import mask_text
from boututln.database import repositories as repo
from boututln.database import schema as s

ACTIVE = ("running", "completed", "failed", "skipped")


class ConcurrentRunError(BoututlnError):
    pass


@dataclass
class StepContext:
    engine: Engine
    run_date: date
    snapshot_id: str
    results: dict[str, dict[str, Any]] = field(default_factory=dict)


StepFn = Callable[[StepContext], dict[str, Any]]


@dataclass(frozen=True)
class Step:
    name: str
    run: StepFn


@dataclass
class JobOutcome:
    status: Literal["completed", "failed", "already_completed"]
    run_id: int | None
    executed_steps: list[str] = field(default_factory=list)
    skipped_steps: list[str] = field(default_factory=list)
    resumed: bool = False
    error: str | None = None
    results: dict[str, dict[str, Any]] = field(default_factory=dict)


class DailyJobRunner:
    def __init__(
        self,
        engine: Engine,
        job_name: str,
        *,
        clock: Clock | None = None,
        stale_after: timedelta = timedelta(hours=3),
    ) -> None:
        self.engine = engine
        self.job_name = job_name
        self.clock = clock or SystemClock()
        self.stale_after = stale_after

    def _existing(self, conn: Connection, run_date: date) -> Any:
        jr = s.job_runs
        return conn.execute(
            select(jr).where(
                jr.c.job_name == self.job_name,
                jr.c.run_date == run_date.isoformat(),
                jr.c.status.in_(ACTIVE),
            )
        ).first()

    def _claim(self, run_date: date, snapshot_id: str) -> tuple[int | None, bool, bool]:
        """Return (run_id, resumed, already_completed)."""
        jr = s.job_runs
        now = self.clock.now()
        with self.engine.begin() as conn:
            row = self._existing(conn, run_date)
            if row is not None:
                if row.status == "completed":
                    return row.id, False, True
                if row.status == "running" and now - row.started_at < self.stale_after:
                    raise ConcurrentRunError(f"{self.job_name} {run_date} is already running")
                if row.data_snapshot_id == snapshot_id:
                    conn.execute(
                        jr.update()
                        .where(jr.c.id == row.id)
                        .values(status="running", started_at=now)
                    )
                    return row.id, True, False
                conn.execute(
                    jr.update()
                    .where(jr.c.id == row.id)
                    .values(
                        status="discarded",
                        finished_at=now,
                        detail="data snapshot changed; previous run discarded",
                    )
                )
            try:
                res = conn.execute(
                    jr.insert().values(
                        job_name=self.job_name,
                        run_date=run_date.isoformat(),
                        data_snapshot_id=snapshot_id,
                        status="running",
                        started_at=now,
                    )
                )
            except IntegrityError as exc:
                raise ConcurrentRunError("another process claimed this run") from exc
            pk = res.inserted_primary_key
            assert pk is not None
            return int(pk[0]), False, False

    def _completed_steps(self, run_id: int) -> dict[str, dict[str, Any]]:
        cp = s.job_checkpoints
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(cp.c.step, cp.c.payload_json).where(
                    cp.c.job_run_id == run_id, cp.c.status == "completed"
                )
            ).all()
        return {r.step: json.loads(r.payload_json) for r in rows}

    def run(self, run_date: date, snapshot_id: str, steps: list[Step]) -> JobOutcome:
        run_id, resumed, done = self._claim(run_date, snapshot_id)
        if done:
            return JobOutcome("already_completed", run_id)
        assert run_id is not None
        ctx = StepContext(self.engine, run_date, snapshot_id)
        finished = self._completed_steps(run_id)
        ctx.results.update(finished)
        out = JobOutcome("completed", run_id, resumed=resumed, results=ctx.results)
        jr, cp = s.job_runs, s.job_checkpoints
        for step in steps:
            if step.name in finished:
                out.skipped_steps.append(step.name)
                continue
            try:
                payload = step.run(ctx)
            except Exception as exc:  # noqa: BLE001 - every failure must be checkpointed
                msg = mask_text(f"{type(exc).__name__}: {exc}")
                now = self.clock.now()
                with self.engine.begin() as conn:
                    conn.execute(
                        jr.update()
                        .where(jr.c.id == run_id)
                        .values(status="failed", finished_at=now, detail=f"{step.name}: {msg}")
                    )
                    repo.log_system(
                        conn,
                        ts=now,
                        level="ERROR",
                        component=self.job_name,
                        message=f"step {step.name} failed: {msg}",
                    )
                out.status, out.error = "failed", msg
                return out
            ctx.results[step.name] = payload
            with self.engine.begin() as conn:
                conn.execute(
                    cp.insert().values(
                        job_run_id=run_id,
                        step=step.name,
                        status="completed",
                        payload_json=json.dumps(payload, default=str),
                        updated_at=self.clock.now(),
                    )
                )
            out.executed_steps.append(step.name)
        with self.engine.begin() as conn:
            conn.execute(
                jr.update()
                .where(jr.c.id == run_id)
                .values(status="completed", finished_at=self.clock.now())
            )
        return out

    def recover_interrupted(self) -> list[int]:
        """Mark stale ``running`` rows as failed so the next run resumes them."""
        jr = s.job_runs
        now = self.clock.now()
        recovered = []
        with self.engine.begin() as conn:
            for row in conn.execute(
                select(jr).where(jr.c.job_name == self.job_name, jr.c.status == "running")
            ).all():
                if now - row.started_at >= self.stale_after:
                    conn.execute(
                        jr.update()
                        .where(jr.c.id == row.id)
                        .values(
                            status="failed",
                            finished_at=now,
                            detail="interrupted (process ended or PC was off); will resume",
                        )
                    )
                    recovered.append(row.id)
        return recovered


def snapshot_id_for(run_date: date, latest: dict[str, datetime]) -> str:
    """Deterministic data snapshot id from the latest bar time per symbol."""
    import hashlib

    blob = (
        run_date.isoformat()
        + "|"
        + "|".join(f"{k}={v.isoformat()}" for k, v in sorted(latest.items()))
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
