"""Outbound HTTP guard used by every network client.

- Only hosts of authorized providers are reachable.
- External AI services are always blocked (local Ollama only, PLAN 12.4).
- KB order endpoints are always blocked in this build (PLAN 7).
"""

from __future__ import annotations

from collections.abc import Iterable

import httpx

from boututln.core.config import is_loopback_host
from boututln.core.errors import ExternalCallBlockedError, OrderEndpointBlockedError

BLOCKED_AI_HOSTS = frozenset(
    {
        "api.anthropic.com",
        "api.openai.com",
        "generativelanguage.googleapis.com",
        "aiplatform.googleapis.com",
        "api.mistral.ai",
        "api.groq.com",
        "openrouter.ai",
        "api.together.xyz",
        "api.deepseek.com",
        "api.cohere.com",
        "ollama.com",
        "registry.ollama.ai",
    }
)


def _is_blocked_ai_host(host: str) -> bool:
    h = host.lower().rstrip(".")
    return any(h == b or h.endswith("." + b) for b in BLOCKED_AI_HOSTS)


def check_url(
    url: httpx.URL,
    allowed_hosts: Iterable[str],
    *,
    allow_loopback_ports: Iterable[int] = (),
) -> None:
    host = (url.host or "").lower()
    if _is_blocked_ai_host(host):
        raise ExternalCallBlockedError(f"external AI API host is blocked: {host}")
    if is_loopback_host(host):
        if url.port in set(allow_loopback_ports):
            return
        raise ExternalCallBlockedError(f"loopback port {url.port} is not allowed")
    if host not in {h.lower() for h in allowed_hosts}:
        raise ExternalCallBlockedError(f"host not on the allowlist: {host}")
    from boututln.brokers.kb.endpoints import is_order_path

    if is_order_path(url.path):
        raise OrderEndpointBlockedError(f"KB order endpoint is blocked: {url.path}")


class GuardedTransport(httpx.BaseTransport):
    """Wraps a transport and enforces :func:`check_url` before every request."""

    def __init__(
        self,
        inner: httpx.BaseTransport,
        allowed_hosts: Iterable[str],
        *,
        allow_loopback_ports: Iterable[int] = (),
    ) -> None:
        self._inner = inner
        self._allowed = frozenset(h.lower() for h in allowed_hosts)
        self._ports = tuple(allow_loopback_ports)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        check_url(request.url, self._allowed, allow_loopback_ports=self._ports)
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()
