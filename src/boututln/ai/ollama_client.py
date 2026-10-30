"""Minimal Ollama HTTP client restricted to the local loopback server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from boututln.ai.allowlist import LocalModel
from boututln.core.config import is_loopback_host
from boututln.core.errors import ExternalCallBlockedError
from boututln.providers.http_guard import GuardedTransport


class OllamaUnavailableError(Exception):
    pass


class OllamaRequestError(Exception):
    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind


@dataclass(frozen=True)
class ChatResult:
    content: str
    model: str
    prompt_tokens: int | None
    output_tokens: int | None
    total_duration_ns: int | None


class OllamaClient:
    def __init__(
        self,
        endpoint: str,
        *,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        url = urlparse(endpoint)
        if not is_loopback_host(url.hostname or ""):
            raise ExternalCallBlockedError("Ollama endpoint must be loopback")
        port = url.port or 11434
        inner = transport or httpx.HTTPTransport(retries=0)
        self._http = httpx.Client(
            base_url=endpoint,
            timeout=timeout_seconds,
            transport=GuardedTransport(inner, allowed_hosts=(), allow_loopback_ports=(port,)),
            trust_env=False,  # never route local traffic through a proxy
        )

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str) -> Any:
        try:
            r = self._http.get(path)
        except httpx.TimeoutException as exc:
            raise OllamaUnavailableError(f"timeout: {exc}") from exc
        except httpx.TransportError as exc:
            raise OllamaUnavailableError(f"connection failed: {exc}") from exc
        if r.status_code != 200:
            raise OllamaUnavailableError(f"HTTP {r.status_code}")
        return r.json()

    def version(self) -> str:
        return str(self._get("/api/version").get("version", "unknown"))

    def installed_models(self) -> list[LocalModel]:
        data = self._get("/api/tags")
        return [
            LocalModel(str(m.get("name") or m.get("model")), str(m.get("digest", "")))
            for m in data.get("models", [])
        ]

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        options: dict[str, Any],
        think: bool | None,
        keep_alive: int | str | None = None,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "format": schema,
            "options": options,
            "stream": False,
        }
        if think is not None:
            payload["think"] = think
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive
        try:
            r = self._http.post("/api/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise OllamaRequestError("timeout", str(exc)) from exc
        except httpx.TransportError as exc:
            raise OllamaRequestError("connection", str(exc)) from exc
        if r.status_code != 200:
            kind = "out_of_memory" if "memory" in r.text.lower() else "http_error"
            raise OllamaRequestError(kind, f"HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        msg = data.get("message") or {}
        return ChatResult(
            content=str(msg.get("content", "")),
            model=str(data.get("model", model)),
            prompt_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
            total_duration_ns=data.get("total_duration"),
        )

    def unload(self, model: str) -> None:
        """Release the model from GPU memory (keep_alive=0)."""
        try:
            self._http.post("/api/generate", json={"model": model, "keep_alive": 0})
        except httpx.TransportError:
            pass
