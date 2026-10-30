"""P0 daily pipeline: trading-day check and data freshness gate per market.

Strategy, AI and order steps are added in P1-P3 on top of this skeleton. If a
market is closed or its data is stale, that market is skipped for the day.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

from boututln.core.market_calendar import MarketCalendar
from boututln.database import repositories as repo
from boututln.domain.market import Market
from boututln.providers.validation import check_freshness
from boututln.scheduler.runner import Step, StepContext

JOB_NAME = "daily_analysis"


def calendar_step(calendar: MarketCalendar, markets: Iterable[Market]) -> Step:
    mlist = list(markets)

    def run(ctx: StepContext) -> dict[str, Any]:
        return {m.value: {"trading_day": calendar.is_trading_day(m, ctx.run_date)} for m in mlist}

    return Step("market_calendar", run)


def freshness_step(calendar: MarketCalendar, required: dict[Market, list[str]]) -> Step:
    def run(ctx: StepContext) -> dict[str, Any]:
        cal_result = ctx.results.get("market_calendar", {})
        out: dict[str, Any] = {}
        with ctx.engine.connect() as conn:
            for market, symbols in required.items():
                if not cal_result.get(market.value, {}).get("trading_day", False):
                    out[market.value] = {"status": "skipped_holiday"}
                    continue
                fr = check_freshness(
                    market, ctx.run_date, repo.latest_bar_times(conn, market), symbols, calendar
                )
                out[market.value] = {
                    "status": "ready" if fr.is_fresh else "skipped_stale",
                    "expected_session": fr.expected_session.isoformat(),
                    "stale": fr.stale_symbols,
                    "missing": fr.missing_symbols,
                }
        return out

    return Step("data_freshness", run)


def p0_steps(calendar: MarketCalendar, required: dict[Market, list[str]]) -> list[Step]:
    return [calendar_step(calendar, required.keys()), freshness_step(calendar, required)]


def markets_ready(results: dict[str, dict[str, Any]]) -> list[str]:
    fr = results.get("data_freshness", {})
    return [m for m, v in fr.items() if v.get("status") == "ready"]


def run_date_for(today: date) -> date:
    return today
