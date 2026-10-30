"""Typed results for the KB query APIs used in P0.

Field names follow the official spec. Only fields the system needs are mapped;
unknown fields are ignored. Values are converted to Decimal / Money here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from boututln.brokers.kb.envelope import KBMalformedResponseError, kb_decimal, kb_text
from boututln.domain.market import Instrument, Market
from boututln.domain.money import Currency, Money

KST = ZoneInfo("Asia/Seoul")


def _req_dec(body: dict[str, Any], key: str) -> Decimal:
    v = kb_decimal(body.get(key))
    if v is None:
        raise KBMalformedResponseError(f"missing numeric field: {key}")
    return v


def _records(body: dict[str, Any], key: str) -> list[dict[str, Any]]:
    recs = body.get(key) or []
    if not isinstance(recs, list):
        raise KBMalformedResponseError(f"{key} is not a list")
    return [r for r in recs if isinstance(r, dict)]


def _kst(d: str | None, t: str | None) -> datetime | None:
    if not d:
        return None
    t = (t or "000000")[:6].ljust(6, "0")
    try:
        return datetime.strptime(d + t, "%Y%m%d%H%M%S").replace(tzinfo=KST)
    except ValueError:
        return None


@dataclass(frozen=True)
class TokenInfo:
    access_token: str
    token_type: str
    expires_in: int

    @classmethod
    def parse(cls, body: dict[str, Any]) -> TokenInfo:
        tok = kb_text(body.get("access_token"))
        if not tok:
            raise KBMalformedResponseError("missing access_token")
        exp = kb_decimal(body.get("expires_in"))
        return cls(tok, kb_text(body.get("token_type")) or "Bearer", int(exp or 0))


@dataclass(frozen=True)
class Quote:
    instrument: Instrument
    name: str | None
    price: Money
    change: Money | None
    volume: Decimal | None
    exchange_code: str | None
    quote_time: datetime | None  # None when KB does not provide a usable timestamp
    raw_market_flag: str | None

    @classmethod
    def parse_domestic(cls, symbol: str, body: dict[str, Any]) -> Quote:
        # IVU10140 has no explicit quote timestamp in the sample (askprc_rcp_tm was blank).
        return cls(
            Instrument(Market.KR, symbol),
            kb_text(body.get("is_nm")),
            Money(_req_dec(body, "now_prc"), Currency.KRW),
            _opt_money(body.get("bdy_cmpr"), Currency.KRW),
            kb_decimal(body.get("acml_vlm")),
            kb_text(body.get("expct_ccls_excg_cd")),
            None,
            None,
        )

    @classmethod
    def parse_overseas(cls, symbol: str, body: dict[str, Any]) -> Quote:
        cur = kb_text(body.get("dl_crncy"))
        if cur != "USD":
            raise KBMalformedResponseError(f"unexpected overseas currency: {cur}")
        return cls(
            Instrument(Market.US, symbol),
            kb_text(body.get("eng_is_nm")) or kb_text(body.get("hngl_is_nm")),
            Money(_req_dec(body, "now_prc_p4"), Currency.USD),
            _opt_money(body.get("bdy_cmpr_p4"), Currency.USD),
            kb_decimal(body.get("vlm")),
            kb_text(body.get("krx_cd")),
            _kst(kb_text(body.get("kor_dt")), kb_text(body.get("kor_tm"))),
            kb_text(body.get("mkt_clsf")),
        )


def _opt_money(v: Any, cur: Currency) -> Money | None:
    d = kb_decimal(v)
    return None if d is None else Money(d, cur)


@dataclass(frozen=True)
class DomesticDeposit:
    orderable_cash: Money
    orderable_amount: Money
    deposit_today: Money
    deposit_d1: Money
    deposit_d2: Money

    @classmethod
    def parse(cls, body: dict[str, Any]) -> DomesticDeposit:
        def m(k: str) -> Money:
            return Money(_req_dec(body, k), Currency.KRW)

        return cls(
            m("ordr_psbl_csh"),
            m("ordr_psbl_amt"),
            m("tdy_tfnd_amt"),
            m("ndy_tfnd"),
            m("nxt2_dy_tfnd"),
        )


@dataclass(frozen=True)
class HoldingLine:
    instrument: Instrument
    name: str | None
    quantity: Decimal
    orderable_quantity: Decimal | None
    average_price: Money | None
    current_price: Money | None
    market_value: Money | None


def _strip_kr_prefix(code: str) -> str:
    # KB returns domestic codes with an "A" prefix (e.g. "A005930").
    return code[1:] if len(code) == 7 and code[0] in "AQJ" and code[1:].isdigit() else code


@dataclass(frozen=True)
class DomesticBalance:
    """SSQM2952 (execution basis): holdings with average price and valuation."""

    deposit_d0: Money
    deposit_d2: Money
    total_market_value: Money
    total_pnl: Money
    lines: list[HoldingLine]

    @classmethod
    def parse(cls, body: dict[str, Any]) -> DomesticBalance:
        lines = []
        for r in _records(body, "Record1"):
            code = kb_text(r.get("is_cd"))
            if not code:
                continue
            qty = kb_decimal(r.get("hld_q")) or kb_decimal(r.get("ordr_psbl_q")) or Decimal(0)
            lines.append(
                HoldingLine(
                    Instrument(Market.KR, _strip_kr_prefix(code)),
                    kb_text(r.get("is_nm")),
                    qty,
                    kb_decimal(r.get("ordr_psbl_q")),
                    _opt_money(r.get("byng_avr_prc"), Currency.KRW),
                    _opt_money(r.get("now_prc"), Currency.KRW),
                    _opt_money(r.get("val_amt"), Currency.KRW),
                )
            )
        return cls(
            Money(_req_dec(body, "dy_tfnd"), Currency.KRW),
            Money(_req_dec(body, "nxt2_dy_tfnd"), Currency.KRW),
            Money(_req_dec(body, "val_amt_sum"), Currency.KRW),
            Money(_req_dec(body, "val_pl_sum"), Currency.KRW),
            lines,
        )


@dataclass(frozen=True)
class DomesticHoldings:
    """SSQM1801: holding quantities only (no average price in this API)."""

    lines: list[HoldingLine]

    @classmethod
    def parse(cls, body: dict[str, Any]) -> DomesticHoldings:
        lines = []
        for r in _records(body, "Record1"):
            code = kb_text(r.get("is_no"))
            if not code:
                continue
            orderable = kb_decimal(r.get("ordr_psbl_q")) or Decimal(0)
            lines.append(
                HoldingLine(
                    Instrument(Market.KR, _strip_kr_prefix(code)),
                    kb_text(r.get("is_nm")),
                    orderable,
                    orderable,
                    None,
                    None,
                    None,
                )
            )
        return cls(lines)


@dataclass(frozen=True)
class CurrencyCash:
    currency_code: str
    deposit: Decimal
    usable: Decimal | None
    # Pure foreign-currency orderable amount; the integrated-margin amount is
    # recorded but never used for orders (integrated margin is forbidden, PLAN 6).
    integrated_orderable: Decimal | None


@dataclass(frozen=True)
class OverseasCash:
    """SKQM3350: cash by currency. Only the USD row maps to the USD book."""

    by_currency: list[CurrencyCash]

    @classmethod
    def parse(cls, body: dict[str, Any]) -> OverseasCash:
        rows = []
        for r in _records(body, "Record1"):
            code = kb_text(r.get("crncy_cd"))
            if not code:
                continue
            rows.append(
                CurrencyCash(
                    code,
                    kb_decimal(r.get("fcrncy_tfnd")) or Decimal(0),
                    kb_decimal(r.get("fcrncy_use_psbl_amt_p2")),
                    kb_decimal(r.get("fcrncy_unty_ordr_psbl_amt_p2")),
                )
            )
        return cls(rows)

    def usd_cash(self) -> Money | None:
        row = next((r for r in self.by_currency if r.currency_code == "USD"), None)
        if row is None:
            return None
        return Money(row.usable if row.usable is not None else row.deposit, Currency.USD)


@dataclass(frozen=True)
class OverseasBalance:
    """SPQM2226 with ``std_crncy_f=1`` (foreign-currency basis). Holdings are Record2."""

    lines: list[HoldingLine]

    @classmethod
    def parse(cls, body: dict[str, Any]) -> OverseasBalance:
        lines = []
        for r in _records(body, "Record2"):
            code = kb_text(r.get("is_cd"))
            if not code:
                continue
            cur = kb_text(r.get("crncy_clsf_nm"))
            if cur not in (None, "USD", "미국달러", "달러"):
                continue  # non-USD holdings are outside the scope of the USD book
            lines.append(
                HoldingLine(
                    Instrument(Market.US, code),
                    kb_text(r.get("is_nm")),
                    kb_decimal(r.get("frgn_hld_q_p6")) or Decimal(0),
                    kb_decimal(r.get("frgn_ordr_psbl_q_p6")),
                    _opt_money(r.get("byng_avr_prc_p4"), Currency.USD),
                    _opt_money(r.get("now_prc_p4"), Currency.USD),
                    None,
                )
            )
        return cls(lines)


@dataclass(frozen=True)
class FxQuote:
    code: str
    name: str | None
    close: Decimal
    receive_time: str | None


@dataclass(frozen=True)
class FxRates:
    quotes: list[FxQuote]
    as_of_date: date | None

    @classmethod
    def parse(cls, body: dict[str, Any]) -> FxRates:
        quotes = []
        for r in _records(body, "out2"):
            code = kb_text(r.get("crncy_cd"))
            close = kb_decimal(r.get("cls_prc_p4"))
            if code and close is not None:
                quotes.append(
                    FxQuote(
                        code, kb_text(r.get("crncy_cd_nm")), close, kb_text(r.get("fnl_rcp_tm"))
                    )
                )
        return cls(quotes, None)

    def usd_krw(self) -> Decimal | None:
        # The sample labels "USDKRWSMBS" as 원/달러. Exact code meaning is unverified.
        q = next((q for q in self.quotes if q.code.startswith("USDKRW")), None)
        return q.close if q else None
