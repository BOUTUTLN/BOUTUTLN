from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from boututln.brokers.kb import endpoints as ep
from boututln.brokers.kb.client import KB_PROVIDER, KBQueryClient
from boututln.brokers.kb.envelope import (
    KBAuthError,
    KBBusinessError,
    KBHttpError,
    KBMalformedResponseError,
    KBRateLimitedError,
    kb_decimal,
    unwrap,
)
from boututln.brokers.kb.rate_limit import TokenBucket
from boututln.brokers.kb.transport import DisabledTransport, FixtureTransport
from boututln.core.clock import FixedClock
from boututln.core.config import KBConfig, ProviderConfig
from boututln.core.errors import (
    ConfigError,
    NetworkNotApprovedError,
    OrderEndpointBlockedError,
    PaidServiceBlockedError,
)
from boututln.core.masking import mask_text
from boututln.core.secrets import InMemorySecretStore
from boututln.domain.money import Currency, Money
from boututln.providers.registry import ProviderRegistry
from tests.conftest import kb_fixture

KB_CFG = KBConfig(ip_addr="192.0.2.10", mac_addr="00-11-22-33-44-55")
SECRETS = {"kb_app_key": "test-app-key-0001", "kb_app_secret": "test-app-secret-0002"}


def ok(name: str) -> list[tuple[int, Any]]:
    return [(200, kb_fixture(name))]


def make_client(
    clock: FixedClock,
    responses: dict[str, Any],
    *,
    approved: bool = True,
    cfg: KBConfig = KB_CFG,
) -> tuple[KBQueryClient, FixtureTransport]:
    responses = {ep.TOKEN.path: ok("token"), **responses}
    transport = FixtureTransport(responses)
    reg = ProviderRegistry(
        [ProviderConfig(name=KB_PROVIDER, cost_class="unknown", hosts=["developer.kbsec.com"])],
        {KB_PROVIDER} if approved else set(),
        clock,
    )
    limiter = TokenBucket(5, clock=clock, sleeper=lambda s: clock.advance(timedelta(seconds=s)))
    client = KBQueryClient(
        cfg, transport, InMemorySecretStore(SECRETS), reg, clock=clock, limiter=limiter
    )
    return client, transport


# --- numeric format (verified against official samples) ------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("000000000339901", Decimal("339901")),
        ("-00000000000000561", Decimal("-561")),
        (" 209.7800", Decimal("209.78")),
        ("0000000360050.00", Decimal("360050.00")),
        ("    0.00", Decimal("0")),
        ("      ", None),
    ],
)
def test_kb_numbers(raw: str, expected: Decimal | None) -> None:
    assert kb_decimal(raw) == expected


@pytest.mark.parametrize("raw", ["12a", "1,000", "1e5", 1.5])
def test_kb_numbers_reject_garbage(raw: object) -> None:
    with pytest.raises(KBMalformedResponseError):
        kb_decimal(raw)


# --- parsing of each query API ----------------------------------------------------


def test_quotes_deposit_balance_holdings_fx(clock: FixedClock) -> None:
    client, transport = make_client(
        clock,
        {
            ep.DOMESTIC_QUOTE.path: ok("ivu10140"),
            ep.OVERSEAS_QUOTE.path: ok("gss10030"),
            ep.DOMESTIC_DEPOSIT.path: ok("ssqm0004"),
            ep.DOMESTIC_BALANCE.path: ok("ssqm2952"),
            ep.DOMESTIC_HOLDINGS.path: ok("ssqm1801"),
            ep.OVERSEAS_CASH.path: ok("skqm3350"),
            ep.OVERSEAS_BALANCE.path: ok("spqm2226"),
            ep.FX_RATES.path: ok("iva60190"),
        },
    )
    q = client.domestic_quote("005930")
    assert q.price == Money.of(71200, "KRW") and q.change == Money.of(-300, "KRW")
    assert q.quote_time is None  # KB gave no usable timestamp -> not invented

    uq = client.overseas_quote("TEST", "NAS")
    assert uq.price == Money.of("123.45", "USD")
    assert uq.quote_time is not None and uq.quote_time.hour == 5

    dep = client.domestic_deposit()
    assert dep.orderable_cash == Money.of(500000, "KRW")

    bal = client.domestic_balance()
    assert bal.total_pnl == Money.of(-561, "KRW")
    line = bal.lines[0]
    assert line.instrument.symbol == "005930"  # "A" prefix stripped
    assert line.quantity == Decimal(10) and line.average_price == Money.of(70000, "KRW")

    assert client.domestic_holdings().lines[0].orderable_quantity == Decimal(10)

    cash = client.overseas_cash()
    # Uses the pure foreign-currency usable amount, never the integrated-margin amount.
    assert cash.usd_cash() == Money.of("6900.50", "USD")

    ob = client.overseas_balance()
    assert ob.lines[0].quantity == Decimal(3) and ob.lines[0].average_price == Money.of(110, "USD")
    assert client.fx_rates().usd_krw() == Decimal("1400.5")

    # Token issued once and reused; every request carried the configured identity headers.
    paths = [c[0] for c in transport.calls]
    assert paths.count(ep.TOKEN.path) == 1
    assert all(
        c[1]["dataHeader"] == {"ipAddr": "192.0.2.10", "macAddr": "00-11-22-33-44-55"}
        for c in transport.calls
    )
    # Credentials are registered with the masker.
    assert "test-app-secret-0002" not in mask_text("secret test-app-secret-0002")
    assert "test-access-token-abc123" not in mask_text("Bearer test-access-token-abc123")


def test_currency_of_results_is_fixed(clock: FixedClock) -> None:
    client, _ = make_client(clock, {ep.OVERSEAS_QUOTE.path: ok("gss10030")})
    assert client.overseas_quote("TEST", "NYS").price.currency is Currency.USD


# --- error handling ------------------------------------------------------------------


def test_business_and_header_errors(clock: FixedClock) -> None:
    client, _ = make_client(
        clock,
        {
            ep.DOMESTIC_DEPOSIT.path: [(200, kb_fixture("business_error"))],
            ep.DOMESTIC_BALANCE.path: [(200, kb_fixture("result_error"))],
            ep.FX_RATES.path: [(500, {"x": 1})],
            ep.DOMESTIC_HOLDINGS.path: [(200, {"unexpected": True})],
            ep.DOMESTIC_QUOTE.path: [
                (
                    200,
                    {
                        "dataHeader": {"resultCode": "200"},
                        "dataBody": {"clsfP": "SC", "now_prc": "N/A"},
                    },
                )
            ],
        },
    )
    with pytest.raises(KBBusinessError, match="조회할 수 없는"):
        client.domestic_deposit()
    with pytest.raises(KBBusinessError):
        client.domestic_balance()
    with pytest.raises(KBHttpError):
        client.fx_rates()
    with pytest.raises(KBMalformedResponseError):
        client.domestic_holdings()
    with pytest.raises(KBMalformedResponseError):
        client.domestic_quote("005930")


def test_unauthorized_reissues_token_once(clock: FixedClock) -> None:
    client, transport = make_client(
        clock,
        {
            ep.FX_RATES.path: [(401, {}), (200, kb_fixture("iva60190"))],
            ep.TOKEN.path: [(200, kb_fixture("token")), (200, kb_fixture("token"))],
        },
    )
    assert client.fx_rates().usd_krw() == Decimal("1400.5")
    assert [c[0] for c in transport.calls].count(ep.TOKEN.path) == 2

    client2, _ = make_client(clock, {ep.FX_RATES.path: [(401, {})]})
    with pytest.raises(KBAuthError):
        client2.fx_rates()


def test_unwrap_rejects_non_success_shapes() -> None:
    with pytest.raises(KBRateLimitedError):
        unwrap(429, {})
    with pytest.raises(KBMalformedResponseError):
        unwrap(200, ["not", "a", "dict"])
    with pytest.raises(KBMalformedResponseError):
        unwrap(200, {"dataHeader": {"resultCode": "200"}})


def test_local_rate_limit_waits_then_fails(clock: FixedClock) -> None:
    waits: list[float] = []
    tb = TokenBucket(
        1, clock=clock, sleeper=lambda s: (waits.append(s), clock.advance(timedelta(seconds=s)))
    )
    tb.acquire()
    tb.acquire()  # waits ~1s on the fake clock
    assert waits and waits[0] == pytest.approx(1.0)
    strict = TokenBucket(1, clock=clock, sleeper=lambda s: None, max_wait_seconds=0.1)
    strict.acquire()
    with pytest.raises(KBRateLimitedError):
        strict.acquire()


# --- safety ----------------------------------------------------------------------------


def test_kb_is_blocked_until_fee_is_confirmed(clock: FixedClock) -> None:
    client, transport = make_client(clock, {ep.FX_RATES.path: ok("iva60190")}, approved=False)
    with pytest.raises(PaidServiceBlockedError):
        client.fx_rates()
    assert transport.calls == []


def test_missing_identity_or_credentials(clock: FixedClock) -> None:
    client, _ = make_client(clock, {ep.FX_RATES.path: ok("iva60190")}, cfg=KBConfig())
    with pytest.raises(ConfigError, match="ip_addr"):
        client.fx_rates()


def test_default_transport_refuses_network() -> None:
    with pytest.raises(NetworkNotApprovedError):
        DisabledTransport().post(ep.FX_RATES.path, {}, {})
    with pytest.raises(ValueError):
        KBConfig(network_enabled=True)  # type: ignore[arg-type]


def test_order_endpoints_unreachable_through_client(clock: FixedClock) -> None:
    client, transport = make_client(clock, {})
    fake_order = ep.Endpoint("SSAM1802", "/api/v1/ssam1802", "domestic buy")
    with pytest.raises(OrderEndpointBlockedError):
        client._post(fake_order, {}, auth=False)
    with pytest.raises(OrderEndpointBlockedError):
        transport.post("/api/v1/skam2101", {}, {})
    assert not hasattr(client, "submit_order") and not hasattr(client, "buy")
    assert all(not ep.is_query_path(f"/api/v1/{c}") for c in ep.ORDER_ENDPOINT_CODES)
