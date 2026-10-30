"""AI analysis output schema (PLAN 12.1).

The model may only say what to do with a candidate the quant strategy already
selected. There are no quantity or price fields, and extra fields are rejected,
so the AI cannot size or price orders.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SCHEMA_VERSION = "1"


class AIAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["1"]
    symbol: str = Field(min_length=1, max_length=16)
    action: Literal["exclude", "keep", "increase", "decrease"]
    # -2..+2 steps; converted to a weight change of lambda * 5%p * level/2 elsewhere.
    adjustment_level: int = Field(ge=-2, le=2)
    confidence: int = Field(ge=0, le=100)
    horizon_days: Literal[5, 20]
    rationale: str = Field(min_length=1, max_length=800)
    risk_flags: list[str] = Field(default_factory=list, max_length=5)


def output_json_schema() -> dict[str, Any]:
    schema = AIAnalysisResult.model_json_schema()
    schema["additionalProperties"] = False
    return schema


class SchemaValidationFailed(ValueError):
    pass


def parse_ai_output(raw: str, expected_symbol: str) -> AIAnalysisResult:
    """Strictly parse model output. Any problem -> SchemaValidationFailed (result discarded)."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise SchemaValidationFailed(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SchemaValidationFailed("output is not a JSON object")
    try:
        result = AIAnalysisResult.model_validate(data)
    except ValidationError as exc:
        raise SchemaValidationFailed(str(exc).splitlines()[0]) from exc
    if result.symbol.upper() != expected_symbol.upper():
        raise SchemaValidationFailed("symbol in output does not match the request")
    if result.action == "keep" and result.adjustment_level != 0:
        raise SchemaValidationFailed("keep must have adjustment_level 0")
    if result.action == "increase" and result.adjustment_level <= 0:
        raise SchemaValidationFailed("increase needs a positive adjustment_level")
    if result.action == "decrease" and result.adjustment_level >= 0:
        raise SchemaValidationFailed("decrease needs a negative adjustment_level")
    return result
