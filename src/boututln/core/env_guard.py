"""Detect dangerous environment variable combinations before anything runs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from boututln.core.errors import LiveTradingLockedError, UnsafeEnvironmentError

if TYPE_CHECKING:
    from boututln.core.config import AppConfig

TRUTHY = {"1", "true", "yes", "on", "y"}

LIVE_FLAGS = (
    "LIVE_TRADING_ENABLED",
    "BOUTUTLN_LIVE_TRADING_ENABLED",
    "BOUTUTLN_ENABLE_LIVE",
    "BOUTUTLN_ALLOW_ORDERS",
    "BOUTUTLN_KB_ORDER_API",
)

EXTERNAL_AI_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "MISTRAL_API_KEY",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "OLLAMA_API_KEY",
)


@dataclass
class EnvReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    live_requested: bool = False


def inspect_environment(env: Mapping[str, str], cfg: AppConfig | None = None) -> EnvReport:
    from boututln.core.config import is_loopback_host

    r = EnvReport()
    if env.get("BOUTUTLN_TRADING_MODE", "").strip().lower() == "live":
        r.live_requested = True
        r.errors.append("BOUTUTLN_TRADING_MODE=live: live trading is locked in this build")
    for flag in LIVE_FLAGS:
        if env.get(flag, "").strip().lower() in TRUTHY:
            r.live_requested = True
            r.errors.append(f"{flag} is set: live trading / order APIs are locked in this build")

    db_override = env.get("BOUTUTLN_DB_PATH", "")
    if db_override:
        r.errors.append("BOUTUTLN_DB_PATH is not supported; the DB path is derived from the mode")
    if cfg is not None and "live" in cfg.db_path().name.lower():
        r.errors.append("non-live mode must not use a database named 'live'")

    host = env.get("OLLAMA_HOST", "").strip()
    if host:
        parsed = urlparse(host if "://" in host else f"http://{host}")
        if not is_loopback_host(parsed.hostname or ""):
            r.errors.append(f"OLLAMA_HOST={host} is not loopback; Ollama must stay local")

    if cfg is not None and cfg.ai.enabled:
        if env.get("OLLAMA_NO_CLOUD", "").strip() != "1":
            r.errors.append("AI is enabled but OLLAMA_NO_CLOUD=1 is not set")
    elif env.get("OLLAMA_NO_CLOUD", "").strip() != "1":
        r.warnings.append("OLLAMA_NO_CLOUD=1 is not set (required before enabling AI)")

    for key in EXTERNAL_AI_KEYS:
        if env.get(key):
            r.warnings.append(f"{key} is present; external AI APIs are blocked and it is ignored")
    return r


def enforce_environment(env: Mapping[str, str], cfg: AppConfig | None = None) -> EnvReport:
    report = inspect_environment(env, cfg)
    if report.live_requested:
        raise LiveTradingLockedError("; ".join(report.errors))
    if report.errors:
        raise UnsafeEnvironmentError("; ".join(report.errors))
    return report
