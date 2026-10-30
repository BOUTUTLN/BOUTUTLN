"""Redact credentials from log output.

Two layers: (1) exact values of secrets registered at runtime (anything read
through a SecretStore), (2) key/value patterns such as ``appSecret=...`` or
``Authorization: Bearer ...`` so unregistered values are still hidden.
"""

from __future__ import annotations

import logging
import re
import threading

MASK = "***"

_SENSITIVE_KEYS = (
    "appkey",
    "app_key",
    "appsecret",
    "app_secret",
    "access_token",
    "accesstoken",
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "account_no",
    "acnt_no",
    "ac_no",
)

_KEY_VALUE = re.compile(
    r"(?P<key>[\"']?(?:" + "|".join(_SENSITIVE_KEYS) + r")[\"']?\s*[:=]\s*)"
    r"(?P<q>[\"']?)(?P<val>[^\"'\s,}&]+)(?P=q)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]+")


class _Registry:
    def __init__(self) -> None:
        self._values: set[str] = set()
        self._lock = threading.Lock()

    def add(self, value: str) -> None:
        if len(value) >= 4:
            with self._lock:
                self._values.add(value)

    def snapshot(self) -> list[str]:
        with self._lock:
            return sorted(self._values, key=len, reverse=True)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


_registry = _Registry()


def register_secret(value: str) -> None:
    _registry.add(value)


def clear_registered_secrets() -> None:
    _registry.clear()


def mask_text(text: str) -> str:
    for v in _registry.snapshot():
        text = text.replace(v, MASK)
    text = _BEARER.sub(lambda m: m.group(1) + MASK, text)
    return _KEY_VALUE.sub(lambda m: f"{m.group('key')}{m.group('q')}{MASK}{m.group('q')}", text)


class MaskingFilter(logging.Filter):
    """Logging filter that renders the message and masks it before output."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except (TypeError, ValueError):
            rendered = str(record.msg)
        record.msg = mask_text(rendered)
        record.args = None
        if record.exc_text:
            record.exc_text = mask_text(record.exc_text)
        return True


def install_masking(logger: logging.Logger | None = None) -> MaskingFilter:
    target = logger or logging.getLogger()
    flt = MaskingFilter()
    target.addFilter(flt)
    for handler in target.handlers:
        handler.addFilter(flt)
    return flt
