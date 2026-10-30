"""KB request/response envelope and numeric parsing.

Verified from the official sample responses (spec JSON, 2026-10-30):
- Response = ``{"dataHeader": {resultCode, resultMessage, processFlag, ...},
  "dataBody": {...}}``.
- Success = HTTP 200, ``resultCode == "200"`` and ``o_clsf`` (account APIs) or
  ``clsfP`` (quote APIs) equal to ``"SC"``.
- Numbers are strings: zero-padded integers (``"000000000339901"``), sign before
  the padding (``"-00000000000000561"``), and ``*_pN`` fields carry an explicit
  decimal point padded with spaces (``" 209.7800"``). Blank means "no value".

Not verified: the shape of error responses (the official error-code list is
empty), so anything that is not an explicit success is treated as an error.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict

SUCCESS_CODE = "SC"
_NUMBER = re.compile(r"^[+-]?\d+(\.\d+)?$")


class KBError(Exception):
    """Base for KB client errors."""


class KBHttpError(KBError):
    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(f"HTTP {status} {message}".strip())
        self.status = status


class KBAuthError(KBError):
    pass


class KBBusinessError(KBError):
    def __init__(self, code: str | None, message: str) -> None:
        super().__init__(f"KB business error {code}: {message}")
        self.code = code
        self.message = message


class KBMalformedResponseError(KBError):
    pass


class KBRateLimitedError(KBError):
    pass


def kb_decimal(value: Any) -> Decimal | None:
    """Parse a KB numeric string. Blank -> None. Anything non-numeric raises."""
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, float):
        raise KBMalformedResponseError(f"unexpected numeric type: {type(value).__name__}")
    if isinstance(value, int):
        return Decimal(value)
    text = str(value).strip()
    if text == "":
        return None
    if not _NUMBER.match(text):
        raise KBMalformedResponseError(f"not a KB number: {value!r}")
    try:
        return Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - regex already guards
        raise KBMalformedResponseError(str(exc)) from exc


def kb_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class DataHeader(BaseModel):
    model_config = ConfigDict(extra="allow")

    resultCode: str | None = None
    resultMessage: str | None = None
    processFlag: str | None = None
    processCode: str | None = None
    processMessage: str | None = None
    processTime: str | None = None


def build_request(body: dict[str, Any], ip_addr: str, mac_addr: str) -> dict[str, Any]:
    """Request envelope as shown in the official samples."""
    return {"dataHeader": {"ipAddr": ip_addr, "macAddr": mac_addr}, "dataBody": body}


def unwrap(status: int, payload: Any) -> dict[str, Any]:
    """Validate the envelope and return ``dataBody`` or raise a KBError."""
    if status == 401:
        raise KBAuthError("unauthorized (token expired or invalid)")
    if status == 429:
        raise KBRateLimitedError("rate limited")
    if status != 200:
        raise KBHttpError(status)
    if not isinstance(payload, dict):
        raise KBMalformedResponseError("response is not a JSON object")
    header_raw = payload.get("dataHeader")
    body = payload.get("dataBody")
    if not isinstance(header_raw, dict) or body is None:
        raise KBMalformedResponseError("missing dataHeader/dataBody")
    header = DataHeader.model_validate(header_raw)
    if header.resultCode != "200":
        raise KBBusinessError(header.resultCode, header.resultMessage or "")
    if not isinstance(body, dict):
        raise KBMalformedResponseError("dataBody is not an object")
    flag = body.get("o_clsf", body.get("clsfP"))
    if flag is not None:
        if str(flag).strip() != SUCCESS_CODE:
            msg = kb_text(body.get("o_msg", body.get("msg"))) or ""
            raise KBBusinessError(str(flag).strip(), msg)
    return body
