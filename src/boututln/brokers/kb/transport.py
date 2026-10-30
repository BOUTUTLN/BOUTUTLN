"""Transports for the KB client.

P0 ships no real network transport. ``DisabledTransport`` is the default and
refuses every call; ``FixtureTransport`` replays stored sample responses in
tests. A real HTTPS transport will be added only after the user approves
network access (and after the KB fee is confirmed, PLAN 3).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from boututln.brokers.kb.endpoints import is_order_path, is_query_path
from boututln.core.errors import NetworkNotApprovedError, OrderEndpointBlockedError


class KBTransport(Protocol):
    def post(
        self, path: str, json_body: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, Any]: ...


class DisabledTransport:
    def post(
        self, path: str, json_body: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, Any]:
        raise NetworkNotApprovedError(
            "KB network access is not approved in this build; use stored samples"
        )


Responder = Callable[[str, dict[str, Any], dict[str, str]], tuple[int, Any]]


class FixtureTransport:
    """Returns canned responses and records every call for assertions."""

    def __init__(self, responses: dict[str, list[tuple[int, Any]] | Responder]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def post(
        self, path: str, json_body: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, Any]:
        # Defence in depth: even a test transport refuses order endpoints.
        if is_order_path(path):
            raise OrderEndpointBlockedError(f"order endpoint blocked: {path}")
        if not is_query_path(path):
            raise OrderEndpointBlockedError(f"endpoint not on the query allowlist: {path}")
        self.calls.append((path, json_body, headers))
        r = self._responses.get(path)
        if r is None:
            return 404, {"error": "no fixture"}
        if callable(r):
            return r(path, json_body, headers)
        if not r:
            raise AssertionError(f"fixture for {path} exhausted")
        return r.pop(0) if len(r) > 1 else r[0]
