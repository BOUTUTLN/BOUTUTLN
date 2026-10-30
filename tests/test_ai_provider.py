"""Local AI provider tests with a mocked Ollama server (no GPU, no real model)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, select

from boututln.ai.allowlist import LocalModel, ModelAllowlist, ModelNotAllowedError, is_cloud_tag
from boututln.ai.ollama_client import OllamaClient
from boututln.ai.provider import AnalysisRequest, NullProvider, OllamaProvider
from boututln.ai.schema import SchemaValidationFailed, output_json_schema, parse_ai_output
from boututln.core.config import AIConfig, AIModelConfig
from boututln.core.errors import ExternalCallBlockedError
from boututln.database import repositories as repo
from boututln.database import schema as s
from boututln.domain.market import Market

T = datetime(2026, 10, 30, 0, 0, tzinfo=UTC)
PRIMARY = "qwen3.5:9b-q4_K_M"
COMPARE = "gemma4:12b-it-q4_K_M"
FALLBACK = "qwen3.5:4b-q4_K_M"
DIGESTS = {
    PRIMARY: "56671c2ab938" + "0" * 52,
    COMPARE: "6114515d63c1" + "0" * 52,
    FALLBACK: "d8b0f5e9760c" + "0" * 52,
}


def good(symbol: str = "005930", **over: Any) -> str:
    d: dict[str, Any] = dict(
        schema_version="1",
        symbol=symbol,
        action="increase",
        adjustment_level=1,
        confidence=70,
        horizon_days=20,
        rationale="earnings revision up",
        risk_flags=[],
    )
    d.update(over)
    return json.dumps(d)


def ai_config(**over: Any) -> AIConfig:
    models = [
        AIModelConfig(role="primary", tag=PRIMARY, digest=DIGESTS[PRIMARY][:12]),
        AIModelConfig(role="comparison", tag=COMPARE, digest=DIGESTS[COMPARE][:12]),
        AIModelConfig(role="fallback", tag=FALLBACK, digest=DIGESTS[FALLBACK][:12]),
    ]
    return AIConfig(enabled=True, models=models, **over)


class FakeOllama:
    """Scripted Ollama: per-model list of chat replies (str content or (status, text))."""

    def __init__(
        self,
        replies: dict[str, list[Any]],
        installed: dict[str, str] | None = None,
        down: bool = False,
    ) -> None:
        self.replies = replies
        self.installed = installed if installed is not None else dict(DIGESTS)
        self.down = down
        self.chats: list[dict[str, Any]] = []
        self.unloads: list[str] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused")
        if req.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.5"})
        if req.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"name": n, "digest": d} for n, d in self.installed.items()]}
            )
        body = json.loads(req.content)
        if req.url.path == "/api/generate" and body.get("keep_alive") == 0:
            self.unloads.append(body["model"])
            return httpx.Response(200, json={})
        if req.url.path == "/api/chat":
            self.chats.append(body)
            reply = self.replies[body["model"]].pop(0)
            if isinstance(reply, tuple):
                return httpx.Response(reply[0], text=reply[1])
            return httpx.Response(
                200,
                json={
                    "model": body["model"],
                    "message": {"role": "assistant", "content": reply},
                    "prompt_eval_count": 900,
                    "eval_count": 120,
                    "total_duration": 2_500_000_000,
                },
            )
        return httpx.Response(404)


def make(
    fake: FakeOllama, records: list[dict[str, Any]], cfg: AIConfig | None = None
) -> OllamaProvider:
    client = OllamaClient(
        "http://127.0.0.1:11434", timeout_seconds=5, transport=httpx.MockTransport(fake.handler)
    )
    return OllamaProvider(cfg or ai_config(), client, records.append)


def request(symbol: str = "005930") -> AnalysisRequest:
    return AnalysisRequest(
        run_id="run-1",
        market=Market.KR,
        symbol=symbol,
        prompt_version="p0.1",
        system_prompt="You review one quant candidate.",
        payload={"symbol": symbol, "score": "0.82"},
        data_as_of=T,
        data_snapshot_id="snap-1",
    )


# --- schema ------------------------------------------------------------------------


def test_schema_has_no_quantity_or_price_and_forbids_extras() -> None:
    schema = output_json_schema()
    props = set(schema["properties"])
    assert not props & {"quantity", "qty", "price", "limit_price", "order"}
    assert schema["additionalProperties"] is False
    with pytest.raises(SchemaValidationFailed):
        parse_ai_output(good(quantity=100), "005930")  # AI may not size orders


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[1, 2]",
        good(confidence=150),
        good(action="buy_now"),
        good(adjustment_level="1"),  # strict: string not accepted
        good(action="keep", adjustment_level=1),
        good(action="decrease", adjustment_level=1),
        good(symbol="000660"),  # wrong symbol
        good(horizon_days=7),
        good(rationale=""),
    ],
)
def test_invalid_outputs_are_discarded(raw: str) -> None:
    with pytest.raises(SchemaValidationFailed):
        parse_ai_output(raw, "005930")


def test_valid_output_parses() -> None:
    r = parse_ai_output(good(), "005930")
    assert r.action == "increase" and r.adjustment_level == 1


# --- allowlist ----------------------------------------------------------------------


def test_allowlist_tag_and_digest() -> None:
    cfg = ai_config()
    allow = ModelAllowlist(list(cfg.models))
    installed = [LocalModel(n, d) for n, d in DIGESTS.items()]
    assert allow.verify("primary", installed).tag == PRIMARY
    with pytest.raises(ModelNotAllowedError, match="not installed"):
        allow.verify("primary", [])
    tampered = [LocalModel(PRIMARY, "ffffffffffff" + "0" * 52)]
    with pytest.raises(ModelNotAllowedError, match="digest mismatch"):
        allow.verify("primary", tampered)
    with pytest.raises(ModelNotAllowedError):
        allow.check_requested_tag("llama3.1:8b")
    with pytest.raises(ModelNotAllowedError):
        allow.check_requested_tag("gpt-oss:120b-cloud")
    unpinned = ModelAllowlist([AIModelConfig(role="primary", tag=PRIMARY)])
    with pytest.raises(ModelNotAllowedError, match="not pinned"):
        unpinned.verify("primary", installed)
    assert is_cloud_tag("qwen3.5:cloud") and not is_cloud_tag(PRIMARY)


def test_client_refuses_remote_endpoints() -> None:
    with pytest.raises(ExternalCallBlockedError):
        OllamaClient("https://ollama.com", timeout_seconds=1)
    with pytest.raises(ExternalCallBlockedError):
        OllamaClient("http://192.168.0.10:11434", timeout_seconds=1)


# --- provider behaviour ---------------------------------------------------------------


def test_primary_success_is_recorded_and_used() -> None:
    fake = FakeOllama({PRIMARY: [good()]})
    recs: list[dict[str, Any]] = []
    out = make(fake, recs).analyze(request())
    assert out.status == "valid" and out.model_tag == PRIMARY and not out.use_quant_fallback
    assert len(recs) == 1 and recs[0]["status"] == "valid" and recs[0]["used_for_signal"]
    rec = recs[0]
    assert rec["model_digest"].startswith("56671c2ab938") and rec["data_as_of"] == T
    assert rec["prompt_version"] == "p0.1" and rec["output_tokens"] == 120
    chat = fake.chats[0]
    assert chat["options"] == {"num_ctx": 8192, "temperature": 0.1, "seed": 42, "num_predict": 800}
    assert chat["think"] is False and chat["format"]["additionalProperties"] is False
    assert fake.unloads == [PRIMARY]  # model released after use


def test_bad_json_retry_then_fallback_model() -> None:
    fake = FakeOllama({PRIMARY: ["oops", good(quantity=5)], FALLBACK: [good()]})
    recs: list[dict[str, Any]] = []
    out = make(fake, recs).analyze(request())
    assert out.status == "valid" and out.model_role == "fallback"
    assert [r["status"] for r in recs] == ["discarded", "discarded", "valid"]
    assert all(r.get("result_json") is None for r in recs[:2])
    assert [c["model"] for c in fake.chats] == [PRIMARY, PRIMARY, FALLBACK]


def test_out_of_memory_switches_to_fallback_immediately() -> None:
    fake = FakeOllama({PRIMARY: [(500, "model requires more system memory")], FALLBACK: [good()]})
    recs: list[dict[str, Any]] = []
    out = make(fake, recs).analyze(request())
    assert out.model_role == "fallback"
    assert recs[0]["error_kind"] == "out_of_memory"


def test_ollama_down_means_quant_fallback() -> None:
    recs: list[dict[str, Any]] = []
    out = make(FakeOllama({}, down=True), recs).analyze(request())
    assert out.status == "failed" and out.use_quant_fallback
    assert recs[0]["error_kind"] == "ollama_unavailable"


def test_everything_fails_means_quant_fallback() -> None:
    fake = FakeOllama({PRIMARY: ["x", "y"], FALLBACK: ["z", "w"]})
    out = make(fake, []).analyze(request())
    assert out.status == "failed" and out.use_quant_fallback and out.result is None


def test_digest_mismatch_never_runs_the_model() -> None:
    fake = FakeOllama(
        {FALLBACK: [good()]}, installed={PRIMARY: "0" * 64, FALLBACK: DIGESTS[FALLBACK]}
    )
    recs: list[dict[str, Any]] = []
    out = make(fake, recs).analyze(request())
    assert out.model_role == "fallback"
    assert recs[0]["error_kind"] == "not_allowed"
    assert [c["model"] for c in fake.chats] == [FALLBACK]


def test_comparison_model_never_feeds_signals() -> None:
    fake = FakeOllama({COMPARE: [good()]})
    recs: list[dict[str, Any]] = []
    out = make(fake, recs).compare(request())
    assert out.status == "valid" and out.model_role == "comparison"
    assert recs[0]["used_for_signal"] is False
    sig = FakeOllama({PRIMARY: [good()]})
    make(sig, []).analyze(request())
    assert all(c["model"] != COMPARE for c in sig.chats)


def test_disabled_ai_is_quant_only() -> None:
    assert NullProvider().analyze(request()).use_quant_fallback
    fake = FakeOllama({})
    out = make(fake, [], ai_config().model_copy(update={"enabled": False})).analyze(request())
    assert out.status == "disabled" and fake.chats == []


def test_records_persist_to_database(engine: Engine) -> None:
    fake = FakeOllama({PRIMARY: ["bad"] * 2, FALLBACK: [good()]})

    def recorder(rec: dict[str, Any]) -> None:
        with engine.begin() as c:
            repo.record_ai_analysis(c, rec)

    client = OllamaClient(
        "http://127.0.0.1:11434", timeout_seconds=5, transport=httpx.MockTransport(fake.handler)
    )
    OllamaProvider(ai_config(), client, recorder).analyze(request())
    with engine.connect() as c:
        rows = c.execute(select(s.ai_analysis).order_by(s.ai_analysis.c.id)).all()
    assert [(r.model_role, r.status, r.used_for_signal) for r in rows] == [
        ("primary", "discarded", False),
        ("primary", "discarded", False),
        ("fallback", "valid", True),
    ]
    assert rows[-1].quantization == "Q4_K_M" and rows[-1].ollama_version == "0.40.5"


def test_no_request_leaves_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    fake = FakeOllama({PRIMARY: [good()]})
    handler: Callable[[httpx.Request], httpx.Response] = fake.handler

    def spy(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url.host))
        return handler(req)

    client = OllamaClient(
        "http://127.0.0.1:11434", timeout_seconds=5, transport=httpx.MockTransport(spy)
    )
    OllamaProvider(ai_config(), client, lambda r: None).analyze(request())
    assert set(seen) == {"127.0.0.1"}
