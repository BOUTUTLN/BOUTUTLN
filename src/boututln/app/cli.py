"""Command line entry points (also used by Windows Task Scheduler).

boututln check-env     PC / environment self-check
boututln init-db       create or upgrade the database for the configured mode
boututln recover       mark interrupted runs so the next run resumes (run at startup)
boututln daily-run     once-a-day pipeline (P0: calendar + data freshness gate)
boututln ai-check      check local Ollama, list installed models and digests
boututln dashboard     local read-only status API on 127.0.0.1
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime

from boututln.core.config import AppConfig, load_config
from boututln.core.errors import BoututlnError
from boututln.core.masking import install_masking
from boututln.database.engine import create_db_engine
from boututln.database.migrations import upgrade_to_head
from boututln.domain.market import Market

log = logging.getLogger("boututln")


def _engine(cfg: AppConfig):  # type: ignore[no-untyped-def]
    engine = create_db_engine(cfg.db_path())
    upgrade_to_head(engine)
    return engine


def cmd_check_env(cfg: AppConfig) -> int:
    from boututln.core.env_check import has_failures, run_checks

    checks = run_checks(cfg)
    for c in checks:
        print(f"[{c.status.upper():4}] {c.name}: {c.detail}")
    return 1 if has_failures(checks) else 0


def cmd_init_db(cfg: AppConfig) -> int:
    engine = create_db_engine(cfg.db_path())
    rev = upgrade_to_head(engine)
    print(f"database ready: {cfg.db_path()} (schema {rev}, mode {cfg.trading.mode})")
    return 0


def cmd_recover(cfg: AppConfig) -> int:
    from boututln.scheduler.daily import JOB_NAME
    from boututln.scheduler.runner import DailyJobRunner

    ids = DailyJobRunner(_engine(cfg), JOB_NAME).recover_interrupted()
    print(f"interrupted runs marked for resume: {ids}")
    return 0


def cmd_daily_run(cfg: AppConfig, symbols_kr: list[str], symbols_us: list[str]) -> int:
    from boututln.core.market_calendar import ExchangeCalendar
    from boututln.database import repositories as repo
    from boututln.scheduler.daily import JOB_NAME, markets_ready, p0_steps
    from boututln.scheduler.runner import DailyJobRunner, snapshot_id_for

    engine = _engine(cfg)
    cal = ExchangeCalendar()
    today = datetime.now(Market.KR.timezone).date()
    required = {Market.KR: symbols_kr, Market.US: symbols_us}
    required = {m: v for m, v in required.items() if m in cfg.scheduler.markets}
    with engine.connect() as conn:
        latest = {
            f"{m.value}:{k}": v for m in required for k, v in repo.latest_bar_times(conn, m).items()
        }
    runner = DailyJobRunner(engine, JOB_NAME)
    out = runner.run(today, snapshot_id_for(today, latest), p0_steps(cal, required))
    print(
        json.dumps(
            {
                "status": out.status,
                "resumed": out.resumed,
                "executed": out.executed_steps,
                "skipped": out.skipped_steps,
                "ready_markets": markets_ready(out.results),
                "error": out.error,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if out.status != "failed" else 1


def cmd_ai_check(cfg: AppConfig) -> int:
    from boututln.ai.allowlist import ModelAllowlist, ModelNotAllowedError
    from boututln.ai.ollama_client import OllamaClient, OllamaUnavailableError

    client = OllamaClient(cfg.ai.endpoint, timeout_seconds=10)
    try:
        print(f"ollama version: {client.version()}")
        installed = client.installed_models()
    except OllamaUnavailableError as exc:
        print(f"Ollama is not reachable at {cfg.ai.endpoint}: {exc}")
        return 1
    finally:
        client.close()
    for local in installed:
        print(f"installed: {local.name}  digest={local.digest[:12]}")
    allow = ModelAllowlist(list(cfg.ai.models))
    rc = 0
    for m in cfg.ai.models:
        try:
            allow.verify(m.role, installed)
            print(f"[OK]   {m.role}: {m.tag}")
        except ModelNotAllowedError as exc:
            print(f"[FAIL] {m.role}: {exc}")
            rc = 1
    return rc


def cmd_dashboard(cfg: AppConfig) -> int:  # pragma: no cover - interactive
    from boututln.dashboard.api import serve

    serve(cfg, _engine(cfg))
    return 0


def _split(text: str) -> list[str]:
    return [x.strip() for x in text.split(",") if x.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="boututln")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("check-env", "init-db", "recover", "ai-check", "dashboard"):
        sub.add_parser(name)
    dr = sub.add_parser("daily-run")
    dr.add_argument("--kr", default="", help="comma separated KR symbols that must be fresh")
    dr.add_argument("--us", default="", help="comma separated US symbols that must be fresh")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    install_masking()
    try:
        cfg = load_config()
        if args.command == "check-env":
            return cmd_check_env(cfg)
        if args.command == "init-db":
            return cmd_init_db(cfg)
        if args.command == "recover":
            return cmd_recover(cfg)
        if args.command == "daily-run":
            return cmd_daily_run(cfg, _split(args.kr), _split(args.us))
        if args.command == "ai-check":
            return cmd_ai_check(cfg)
        if args.command == "dashboard":
            return cmd_dashboard(cfg)
    except BoututlnError as exc:
        log.error("%s: %s", type(exc).__name__, exc)
        return 2
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
