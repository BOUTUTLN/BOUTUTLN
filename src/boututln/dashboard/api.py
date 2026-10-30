"""Read-only local status API (FastAPI). Binds to 127.0.0.1 only.

The full Streamlit dashboard is P3 scope. KB quotes may not be redistributed
(KB terms Art. 5(3)), so this API must never be exposed beyond loopback.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from sqlalchemy import Engine, desc, select

from boututln.core.config import AppConfig, is_loopback_host
from boututln.core.errors import ConfigError
from boututln.database import schema as s
from boututln.database.migrations import current_revision
from boututln.domain.money import Currency
from boututln.providers.registry import CostClass
from boututln.risk.kill_switch import get_level


def create_app(cfg: AppConfig, engine: Engine) -> FastAPI:
    app = FastAPI(title="BOUTUTLN local status", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "schema": current_revision(engine)}

    @app.get("/status")
    def status() -> dict[str, Any]:
        with engine.connect() as conn:
            runs = conn.execute(
                select(s.job_runs.c.job_name, s.job_runs.c.run_date, s.job_runs.c.status)
                .order_by(desc(s.job_runs.c.id))
                .limit(10)
            ).all()
            halts = {
                "global": get_level(conn, None).value,
                "KRW": get_level(conn, Currency.KRW).value,
                "USD": get_level(conn, Currency.USD).value,
            }
        return {
            "mode": cfg.trading.mode,
            "live_trading": "locked",
            "ai_enabled": cfg.ai.enabled,
            "halt_levels": halts,
            "recent_runs": [dict(r._mapping) for r in runs],
        }

    @app.get("/costs")
    def costs() -> dict[str, Any]:
        """Services that are not confirmed zero-cost are listed separately (PLAN 13)."""
        non_free = [
            {"name": p.name, "cost_class": p.cost_class, "label": CostClass(p.cost_class).label_ko}
            for p in cfg.providers
            if CostClass(p.cost_class).needs_approval
        ]
        return {"non_zero_or_unconfirmed": non_free, "monthly_target_krw": 0}

    return app


def serve(cfg: AppConfig, engine: Engine) -> None:  # pragma: no cover - interactive
    if not is_loopback_host(cfg.dashboard.host):
        raise ConfigError("dashboard host must be loopback")
    import uvicorn

    uvicorn.run(create_app(cfg, engine), host=cfg.dashboard.host, port=cfg.dashboard.port)
