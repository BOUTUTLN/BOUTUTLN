"""AI providers. The trading system depends only on ``AIProvider``.

``OllamaProvider`` runs local models only: primary first, then the fallback.
Every attempt is recorded. Invalid output is discarded and the caller falls
back to the quant-only weight (PLAN 12.8). The comparison model is run through
a separate method and its output can never be used for a trading signal.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

from boututln.ai.allowlist import ModelAllowlist, ModelNotAllowedError
from boututln.ai.ollama_client import OllamaClient, OllamaRequestError, OllamaUnavailableError
from boututln.ai.schema import (
    SCHEMA_VERSION,
    AIAnalysisResult,
    SchemaValidationFailed,
    output_json_schema,
    parse_ai_output,
)
from boututln.core.clock import Clock, SystemClock
from boututln.core.config import AIConfig
from boututln.domain.market import Market

Recorder = Callable[[dict[str, Any]], None]
Status = Literal["valid", "failed", "disabled"]


@dataclass(frozen=True)
class AnalysisRequest:
    run_id: str
    market: Market
    symbol: str
    prompt_version: str
    system_prompt: str
    payload: Mapping[str, Any]
    data_as_of: datetime
    data_snapshot_id: str
    input_reduced: bool = False


@dataclass
class AnalysisOutcome:
    status: Status
    result: AIAnalysisResult | None = None
    model_role: str | None = None
    model_tag: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def use_quant_fallback(self) -> bool:
        return self.status != "valid"


class AIProvider(Protocol):
    def analyze(self, req: AnalysisRequest) -> AnalysisOutcome: ...


class NullProvider:
    """AI disabled (default until the local benchmark passes)."""

    def analyze(self, req: AnalysisRequest) -> AnalysisOutcome:
        return AnalysisOutcome(status="disabled")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


class OllamaProvider:
    def __init__(
        self,
        config: AIConfig,
        client: OllamaClient,
        recorder: Recorder,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._cfg = config
        self._client = client
        self._record = recorder
        self._clock = clock or SystemClock()
        self._allow = ModelAllowlist(list(config.models))
        self._schema = output_json_schema()

    def _options(self) -> dict[str, Any]:
        o = self._cfg.options
        # temperature is a sampling parameter, not money; Ollama expects a JSON number.
        return {
            "num_ctx": o.num_ctx,
            "temperature": float(o.temperature),
            "seed": o.seed,
            "num_predict": o.num_predict,
        }

    def _messages(self, req: AnalysisRequest) -> list[dict[str, str]]:
        system = (
            req.system_prompt
            + "\n\nRespond with one JSON object that matches this JSON Schema exactly:\n"
            + canonical_json(self._schema)
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": canonical_json(dict(req.payload))},
        ]

    def _base_record(
        self,
        req: AnalysisRequest,
        role: str,
        tag: str,
        digest: str | None,
        version: str | None,
        attempt: int,
    ) -> dict[str, Any]:
        spec = self._allow.spec(role) if role in {"primary", "fallback", "comparison"} else None
        return {
            "run_id": req.run_id,
            "market": req.market.value,
            "symbol": req.symbol,
            "model_role": role,
            "model_tag": tag,
            "model_digest": digest,
            "quantization": spec.quantization if spec else None,
            "ollama_version": version,
            "options_json": canonical_json(self._options()),
            "think": str(self._cfg.think).lower(),
            "prompt_version": req.prompt_version,
            "prompt_hash": _sha(req.system_prompt + canonical_json(self._schema)),
            "schema_version": SCHEMA_VERSION,
            "data_as_of": req.data_as_of,
            "data_snapshot_id": req.data_snapshot_id,
            "input_hash": _sha(canonical_json(dict(req.payload))),
            "input_reduced": req.input_reduced,
            "requested_at": self._clock.now(),
            "attempt": attempt,
            "used_for_signal": False,
        }

    def _try_model(
        self,
        req: AnalysisRequest,
        role: str,
        version: str,
        installed: list[Any],
        *,
        for_signal: bool,
        outcome: AnalysisOutcome,
    ) -> AIAnalysisResult | None:
        try:
            spec = self._allow.verify(role, installed)
        except ModelNotAllowedError as exc:
            tag = self._allow.spec(role).tag if role in self._roles() else "unregistered"
            rec = self._base_record(req, role, tag, None, version, 1)
            rec.update(status="failed", error_kind="not_allowed", error_detail=str(exc))
            self._record(rec)
            outcome.errors.append(str(exc))
            return None
        try:
            for attempt in (1, 2):
                rec = self._base_record(req, role, spec.tag, spec.digest, version, attempt)
                try:
                    chat = self._client.chat(
                        model=spec.tag,
                        messages=self._messages(req),
                        schema=self._schema,
                        options=self._options(),
                        think=self._cfg.think,
                    )
                except OllamaRequestError as exc:
                    rec.update(status="failed", error_kind=exc.kind, error_detail=str(exc))
                    rec["responded_at"] = self._clock.now()
                    self._record(rec)
                    outcome.errors.append(str(exc))
                    if exc.kind in ("out_of_memory", "connection"):
                        return None  # go straight to the fallback model
                    continue
                rec["responded_at"] = self._clock.now()
                rec["input_tokens"] = chat.prompt_tokens
                rec["output_tokens"] = chat.output_tokens
                if chat.total_duration_ns is not None:
                    rec["duration_ms"] = int(chat.total_duration_ns // 1_000_000)
                try:
                    result = parse_ai_output(chat.content, req.symbol)
                except SchemaValidationFailed as exc:
                    rec.update(status="discarded", error_kind="schema", error_detail=str(exc))
                    self._record(rec)
                    outcome.errors.append(str(exc))
                    continue
                rec.update(
                    status="valid", result_json=result.model_dump_json(), used_for_signal=for_signal
                )
                self._record(rec)
                return result
            return None
        finally:
            self._client.unload(spec.tag)

    def _roles(self) -> set[str]:
        return {m.role for m in self._cfg.models}

    def _preflight(
        self, req: AnalysisRequest, outcome: AnalysisOutcome
    ) -> tuple[str, list[Any]] | None:
        try:
            return self._client.version(), self._client.installed_models()
        except OllamaUnavailableError as exc:
            tag = self._allow.spec("primary").tag if "primary" in self._roles() else "none"
            rec = self._base_record(req, "primary", tag, None, None, 1)
            rec.update(status="failed", error_kind="ollama_unavailable", error_detail=str(exc))
            self._record(rec)
            outcome.errors.append(str(exc))
            return None

    def analyze(self, req: AnalysisRequest) -> AnalysisOutcome:
        """Signal path: primary, then fallback. Never uses the comparison model."""
        outcome = AnalysisOutcome(status="failed")
        if not self._cfg.enabled:
            return AnalysisOutcome(status="disabled")
        pre = self._preflight(req, outcome)
        if pre is None:
            return outcome
        version, installed = pre
        for role in ("primary", "fallback"):
            if role not in self._roles():
                continue
            result = self._try_model(
                req, role, version, installed, for_signal=True, outcome=outcome
            )
            if result is not None:
                outcome.status, outcome.result = "valid", result
                outcome.model_role, outcome.model_tag = role, self._allow.spec(role).tag
                return outcome
        return outcome

    def compare(self, req: AnalysisRequest) -> AnalysisOutcome:
        """Comparison path: same request, comparison model, never used for signals."""
        outcome = AnalysisOutcome(status="failed")
        if not self._cfg.enabled or "comparison" not in self._roles():
            return AnalysisOutcome(status="disabled")
        pre = self._preflight(req, outcome)
        if pre is None:
            return outcome
        version, installed = pre
        result = self._try_model(
            req, "comparison", version, installed, for_signal=False, outcome=outcome
        )
        if result is not None:
            outcome.status, outcome.result = "valid", result
            outcome.model_role = "comparison"
            outcome.model_tag = self._allow.spec("comparison").tag
        return outcome
