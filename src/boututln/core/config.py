"""Validated application configuration.

Sources, in order: ``config/settings.toml`` (committed, safe defaults),
``config/local.toml`` (git-ignored, user overrides), then a small set of
environment variables. Unknown keys are rejected so typos cannot silently
disable a safety setting.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from decimal import Decimal
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from boututln.core.errors import ConfigError, LiveTradingLockedError
from boututln.domain.enums import TradingMode
from boututln.domain.market import Market

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_FILES = (
    PROJECT_ROOT / "config" / "settings.toml",
    PROJECT_ROOT / "config" / "local.toml",
)


def is_loopback_host(host: str) -> bool:
    if host in LOOPBACK_HOSTS:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TradingConfig(_Strict):
    # "live" is intentionally not a valid value in P0.
    mode: Literal["backtest", "paper"] = "backtest"


class PathsConfig(_Strict):
    data_dir: Path

    def db_path(self, mode: TradingMode) -> Path:
        if mode is TradingMode.LIVE:
            raise LiveTradingLockedError("there is no live database in P0")
        return self.data_dir / "db" / f"{mode.value}.db"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def lock_dir(self) -> Path:
        return self.data_dir / "locks"


class KBConfig(_Strict):
    # Real network access to KB is not approved in P0. Only False is accepted.
    network_enabled: Literal[False] = False
    base_url: str = "https://developer.kbsec.com:32484"
    # Header values required by the guide; their exact rules are unverified (kb research 7.4).
    ip_addr: str | None = None
    mac_addr: str | None = None
    # KB has not published rate limits; conservative default.
    max_requests_per_second: int = Field(default=1, ge=1, le=5)
    token_refresh_margin_seconds: int = Field(default=3600, ge=60)


class AIModelConfig(_Strict):
    role: Literal["primary", "comparison", "fallback"]
    tag: str
    digest: str | None = None  # must be filled after local install; unpinned models are refused
    quantization: str = "Q4_K_M"


class AIOptions(_Strict):
    num_ctx: int = Field(default=8192, ge=2048, le=32768)
    temperature: Decimal = Decimal("0.1")
    seed: int = 42
    num_predict: int = Field(default=800, ge=64, le=4096)


class AIConfig(_Strict):
    enabled: bool = False
    provider: Literal["ollama", "null"] = "ollama"
    endpoint: str = "http://127.0.0.1:11434"
    lambda_: Decimal = Field(default=Decimal("0.5"), alias="lambda", ge=0, le=1)
    candidates_from_quant: int = Field(default=30, ge=1, le=30)
    deep_analysis_count: int = Field(default=12, ge=10, le=15)
    time_budget_minutes: int = Field(default=30, ge=1, le=120)
    request_timeout_seconds: int = Field(default=180, ge=5, le=900)
    # Ollama "think" flag. Default kept at False (PLAN 12.3); P2 compares False vs True.
    think: bool | None = False
    keep_alive_after_run: int = 0
    options: AIOptions = AIOptions()
    models: list[AIModelConfig] = []

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @field_validator("endpoint")
    @classmethod
    def _loopback_only(cls, v: str) -> str:
        host = urlparse(v).hostname or ""
        if not is_loopback_host(host):
            raise ValueError("AI endpoint must be a loopback address (local Ollama only)")
        return v

    @model_validator(mode="after")
    def _models_valid(self) -> AIConfig:
        roles = [m.role for m in self.models]
        if len(roles) != len(set(roles)):
            raise ValueError("each AI model role may appear only once")
        for m in self.models:
            if "cloud" in m.tag.lower():
                raise ValueError(f"cloud model tags are forbidden: {m.tag}")
        if self.enabled and self.provider == "ollama":
            if "primary" not in roles:
                raise ValueError("ai.enabled requires a primary model")
            unpinned = [m.tag for m in self.models if not m.digest]
            if unpinned:
                raise ValueError(f"ai.enabled requires pinned digests for: {unpinned}")
        return self

    def model_for(self, role: str) -> AIModelConfig | None:
        return next((m for m in self.models if m.role == role), None)


class ProviderConfig(_Strict):
    name: str
    cost_class: Literal["free", "free_limited", "conditional_free", "paid", "unknown"]
    hosts: list[str] = []
    daily_limit: int | None = Field(default=None, ge=1)
    per_minute_limit: int | None = Field(default=None, ge=1)
    per_second_limit: int | None = Field(default=None, ge=1)
    license_tag: str = "unspecified"
    note: str = ""


class SchedulerConfig(_Strict):
    daily_run_time: str = "07:30"
    markets: list[Market] = [Market.KR, Market.US]
    # PC was off during a session: never trade late (PLAN 8).
    missed_session_policy: Literal["skip"] = "skip"
    stale_after_sessions: int = Field(default=1, ge=1, le=5)


class PaperConfig(_Strict):
    """Paper-trading starting cash per book (PLAN 5); KRW and USD stay separate."""

    initial_cash_krw: Decimal = Field(default=Decimal("10000000"), gt=0)
    initial_cash_usd: Decimal = Field(default=Decimal("7000.00"), gt=0)

    @field_validator("initial_cash_krw", "initial_cash_usd", mode="before")
    @classmethod
    def _no_float(cls, v: object) -> object:
        if isinstance(v, float):
            raise ValueError("use a quoted decimal string, not a float")
        return v

    @field_validator("initial_cash_krw")
    @classmethod
    def _krw_whole(cls, v: Decimal) -> Decimal:
        if v != v.to_integral_value():
            raise ValueError("KRW amount must be a whole number")
        return v


class DashboardConfig(_Strict):
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1024, le=65535)

    @field_validator("host")
    @classmethod
    def _loopback(cls, v: str) -> str:
        if not is_loopback_host(v):
            raise ValueError("dashboard must bind to a loopback address only (127.0.0.1)")
        return v


class AppConfig(_Strict):
    environment: Literal["development", "production"] = "development"
    secret_store: Literal["keyring", "env", "memory"] = "keyring"  # noqa: S105 - backend name
    trading: TradingConfig = TradingConfig()
    paths: PathsConfig
    kb: KBConfig = KBConfig()
    ai: AIConfig = AIConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    paper: PaperConfig = PaperConfig()
    dashboard: DashboardConfig = DashboardConfig()
    providers: list[ProviderConfig] = []

    @model_validator(mode="after")
    def _environment_rules(self) -> AppConfig:
        if self.environment == "production" and self.secret_store != "keyring":  # noqa: S105
            raise ValueError("production requires secret_store = 'keyring'")
        names = [p.name for p in self.providers]
        if len(names) != len(set(names)):
            raise ValueError("duplicate provider names")
        return self

    @property
    def mode(self) -> TradingMode:
        return TradingMode(self.trading.mode)

    def db_path(self) -> Path:
        return self.paths.db_path(self.mode)


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, Mapping) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


ENV_OVERRIDES = {
    "BOUTUTLN_ENV": ("environment",),
    "BOUTUTLN_TRADING_MODE": ("trading", "mode"),
    "BOUTUTLN_DATA_DIR": ("paths", "data_dir"),
    "BOUTUTLN_SECRET_STORE": ("secret_store",),
}


def load_config(
    files: tuple[Path, ...] | list[Path] = DEFAULT_CONFIG_FILES,
    environ: Mapping[str, str] | None = None,
    *,
    check_environment: bool = True,
) -> AppConfig:
    env = dict(os.environ if environ is None else environ)
    data: dict[str, Any] = {}
    for f in files:
        if f.exists():
            with f.open("rb") as fh:
                data = _deep_merge(data, tomllib.load(fh))
    for var, path in ENV_OVERRIDES.items():
        if var in env:
            node = data
            for key in path[:-1]:
                node = node.setdefault(key, {})
            node[path[-1]] = env[var]

    requested_mode = str(data.get("trading", {}).get("mode", "backtest")).strip().lower()
    if requested_mode == TradingMode.LIVE.value:
        raise LiveTradingLockedError("trading.mode = 'live' is not available in this build (P0)")

    if "paths" not in data or "data_dir" not in data.get("paths", {}):
        raise ConfigError("missing required setting: paths.data_dir")
    data_dir = Path(str(data["paths"]["data_dir"]))
    if not data_dir.is_absolute():
        # Task Scheduler may start in another working directory; anchor to the project.
        data["paths"]["data_dir"] = str(PROJECT_ROOT / data_dir)
    try:
        cfg = AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration:\n{exc}") from exc

    if check_environment:
        from boututln.core.env_guard import enforce_environment

        enforce_environment(env, cfg)
    return cfg
