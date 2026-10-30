"""Read-only KB Securities Open API client.

This class has no order methods, and ``_post`` refuses any path that is not on
the query allowlist. Unverified items (rate limits, overseas-IP blocking
responses, ipAddr/macAddr rules) are listed in docs/kb-openapi-research.md 7-8.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from boututln.brokers.kb import endpoints as ep
from boututln.brokers.kb.envelope import (
    KBAuthError,
    KBRateLimitedError,
    build_request,
    unwrap,
)
from boututln.brokers.kb.models import (
    DomesticBalance,
    DomesticDeposit,
    DomesticHoldings,
    FxRates,
    OverseasBalance,
    OverseasCash,
    Quote,
    TokenInfo,
)
from boututln.brokers.kb.rate_limit import TokenBucket
from boututln.brokers.kb.transport import KBTransport
from boututln.core.clock import Clock, SystemClock
from boututln.core.config import KBConfig
from boututln.core.errors import ConfigError, OrderEndpointBlockedError
from boututln.core.secrets import SecretStore, SecretValue
from boututln.providers.registry import ProviderRegistry

KB_PROVIDER = "kb_openapi"
US_EXCHANGES = ("NAS", "NYS", "AMX")


class KBQueryClient:
    def __init__(
        self,
        config: KBConfig,
        transport: KBTransport,
        secrets: SecretStore,
        registry: ProviderRegistry,
        *,
        clock: Clock | None = None,
        limiter: TokenBucket | None = None,
    ) -> None:
        self._cfg = config
        self._transport = transport
        self._secrets = secrets
        self._registry = registry
        self._clock = clock or SystemClock()
        self._limiter = limiter or TokenBucket(config.max_requests_per_second, clock=self._clock)
        self._token: SecretValue | None = None
        self._token_expiry: datetime | None = None

    # -- low level ---------------------------------------------------------

    def _identity(self) -> tuple[str, str]:
        if not self._cfg.ip_addr or not self._cfg.mac_addr:
            raise ConfigError("kb.ip_addr and kb.mac_addr must be configured (rules unverified)")
        return self._cfg.ip_addr, self._cfg.mac_addr

    def _post(self, endpoint: ep.Endpoint, body: dict[str, Any], *, auth: bool) -> dict[str, Any]:
        if ep.is_order_path(endpoint.path) or not ep.is_query_path(endpoint.path):
            raise OrderEndpointBlockedError(f"not a query endpoint: {endpoint.path}")
        self._registry.acquire(KB_PROVIDER)  # cost-class + quota gate (KB fee is unconfirmed)
        self._limiter.acquire_or_raise()
        ip, mac = self._identity()
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if auth:
            headers["Authorization"] = f"Bearer {self._access_token().reveal()}"
        status, payload = self._transport.post(endpoint.path, build_request(body, ip, mac), headers)
        return unwrap(status, payload)

    def _query(self, endpoint: ep.Endpoint, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._post(endpoint, body, auth=True)
        except KBAuthError:
            self._token = None  # re-issue once, then give up
            return self._post(endpoint, body, auth=True)

    def _access_token(self) -> SecretValue:
        now = self._clock.now()
        margin = timedelta(seconds=self._cfg.token_refresh_margin_seconds)
        if self._token and self._token_expiry and now < self._token_expiry - margin:
            return self._token
        key = self._secrets.get("kb_app_key")
        secret = self._secrets.get("kb_app_secret")
        if key is None or secret is None:
            raise ConfigError("KB credentials are not set in the secret store")
        body = {
            "appKey": key.reveal(),
            "appSecret": secret.reveal(),
            "grantType": "client_credentials",
        }
        info = TokenInfo.parse(self._post(ep.TOKEN, body, auth=False))
        self._token = SecretValue(info.access_token)
        self._token_expiry = now + timedelta(seconds=info.expires_in or 0)
        return self._token

    # -- public query API ----------------------------------------------------

    def domestic_quote(self, symbol: str, exchange: str = "0") -> Quote:
        """IVU10140. exchange: 0 integrated, 1 KRX, 2 NXT."""
        body = self._query(ep.DOMESTIC_QUOTE, {"excg_clsf": exchange, "shrt_cd": symbol})
        return Quote.parse_domestic(symbol, body)

    def overseas_quote(self, symbol: str, exchange: str) -> Quote:
        """GSS10030. exchange: NAS / NYS / AMX."""
        if exchange not in US_EXCHANGES:
            raise ValueError(f"exchange must be one of {US_EXCHANGES}")
        body = self._query(ep.OVERSEAS_QUOTE, {"krx_cd": exchange, "is_cd": symbol})
        return Quote.parse_overseas(symbol, body)

    def domestic_deposit(self) -> DomesticDeposit:
        return DomesticDeposit.parse(self._query(ep.DOMESTIC_DEPOSIT, {"is_no": ""}))

    def domestic_balance(self) -> DomesticBalance:
        """Account summary + holdings with average price (SSQM2952)."""
        return DomesticBalance.parse(self._query(ep.DOMESTIC_BALANCE, {"excg_mktpr_ccd": ""}))

    def domestic_holdings(self) -> DomesticHoldings:
        body = {"inq_clsf": "0", "is_no": "", "mkt_tm_ccd": "1", "nxt_key": ""}
        return DomesticHoldings.parse(self._query(ep.DOMESTIC_HOLDINGS, body))

    def overseas_cash(self) -> OverseasCash:
        return OverseasCash.parse(self._query(ep.OVERSEAS_CASH, {}))

    def overseas_balance(self) -> OverseasBalance:
        body = {"std_crncy_f": "1", "exch_r_aplc_f": "1", "fee_clsf": "", "cn_f": "", "nxt_key": ""}
        return OverseasBalance.parse(self._query(ep.OVERSEAS_BALANCE, body))

    def fx_rates(self) -> FxRates:
        return FxRates.parse(self._query(ep.FX_RATES, {}))


__all__ = ["KBQueryClient", "KB_PROVIDER", "KBRateLimitedError"]
