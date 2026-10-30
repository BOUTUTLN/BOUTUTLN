"""KB Securities Open API endpoint catalogue (from the official spec, 2026-10-30).

Only *query* endpoints are callable. Order endpoints appear here solely as a
denylist; there is no code in this project that builds an order request.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Endpoint:
    code: str
    path: str
    description: str


TOKEN = Endpoint("token", "/oauth2/token", "OAuth2 client-credentials token")
REVOKE = Endpoint("revoke", "/oauth2/revoke", "token revoke")

DOMESTIC_QUOTE = Endpoint("IVU10140", "/api/v1/ivu10140", "domestic stock current price")
OVERSEAS_QUOTE = Endpoint("GSS10030", "/api/v1/gss10030", "overseas stock current price")
DOMESTIC_DEPOSIT = Endpoint("SSQM0004", "/api/v1/ssqm0004", "domestic deposit (KRW cash)")
DOMESTIC_HOLDINGS = Endpoint("SSQM1801", "/api/v1/ssqm1801", "domestic holdings (quantities)")
DOMESTIC_BALANCE = Endpoint("SSQM2952", "/api/v1/ssqm2952", "domestic balance (execution basis)")
OVERSEAS_BALANCE = Endpoint("SPQM2226", "/api/v1/spqm2226", "overseas balance valuation")
OVERSEAS_CASH = Endpoint("SKQM3350", "/api/v1/skqm3350", "overseas orderable cash by currency")
FX_RATES = Endpoint("IVA60190", "/api/v1/iva60190", "exchange rates")
MARKET_STATUS = Endpoint("SZQM0771", "/api/v1/szqm0771", "market operating status")

QUERY_ENDPOINTS: dict[str, Endpoint] = {
    e.path: e
    for e in (
        TOKEN,
        REVOKE,
        DOMESTIC_QUOTE,
        OVERSEAS_QUOTE,
        DOMESTIC_DEPOSIT,
        DOMESTIC_HOLDINGS,
        DOMESTIC_BALANCE,
        OVERSEAS_BALANCE,
        OVERSEAS_CASH,
        FX_RATES,
        MARKET_STATUS,
    )
}

# Order / modify / cancel / reservation endpoints (domestic and overseas).
ORDER_ENDPOINT_CODES = frozenset(
    {
        "ssam0831",
        "ssam1801",
        "ssam1802",
        "ssam1805",
        "ssam1806",
        "ssam5762",
        "ssam5763",
        "ssam5764",
        "skam2101",
        "skam2102",
        "skam2201",
        "skam2202",
        "spao2104",
        "spao2106",
    }
)


def is_order_path(path: str) -> bool:
    last = path.rstrip("/").rsplit("/", 1)[-1].lower()
    return last in ORDER_ENDPOINT_CODES


def is_query_path(path: str) -> bool:
    return path in QUERY_ENDPOINTS and not is_order_path(path)
