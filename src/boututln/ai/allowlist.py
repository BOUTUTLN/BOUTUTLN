"""Local model allowlist: exact tag + pinned weight digest (PLAN 12.4)."""

from __future__ import annotations

from dataclasses import dataclass

from boututln.core.config import AIModelConfig
from boututln.core.errors import BoututlnError

# Exact tags confirmed on ollama.com/library (2026-10-30).
CONFIRMED_TAGS = {
    "primary": "qwen3.5:9b-q4_K_M",
    "comparison": "gemma4:12b-it-q4_K_M",
    "fallback": "qwen3.5:4b-q4_K_M",
}


class ModelNotAllowedError(BoututlnError):
    pass


def is_cloud_tag(tag: str) -> bool:
    t = tag.lower()
    return "cloud" in t or t.endswith("-remote")


@dataclass(frozen=True)
class LocalModel:
    name: str
    digest: str


class ModelAllowlist:
    def __init__(self, models: list[AIModelConfig]) -> None:
        self._by_role: dict[str, AIModelConfig] = {m.role: m for m in models}
        self._by_tag: dict[str, AIModelConfig] = {m.tag: m for m in models}

    def spec(self, role: str) -> AIModelConfig:
        m = self._by_role.get(role)
        if m is None:
            raise ModelNotAllowedError(f"no model registered for role '{role}'")
        return m

    def verify(self, role: str, installed: list[LocalModel]) -> AIModelConfig:
        """Check tag and digest against what Ollama reports as installed locally."""
        spec = self.spec(role)
        if is_cloud_tag(spec.tag):
            raise ModelNotAllowedError(f"cloud model refused: {spec.tag}")
        if not spec.digest:
            raise ModelNotAllowedError(f"digest for {spec.tag} is not pinned; run 'ai-pin' first")
        local = next((m for m in installed if m.name == spec.tag), None)
        if local is None:
            raise ModelNotAllowedError(f"{spec.tag} is not installed locally")
        if not _digest_matches(spec.digest, local.digest):
            raise ModelNotAllowedError(
                f"digest mismatch for {spec.tag}: pinned {spec.digest[:12]}, "
                f"local {local.digest[:12]}"
            )
        return spec

    def check_requested_tag(self, tag: str) -> AIModelConfig:
        """Refuse any tag that is not registered (e.g. a cloud or ad-hoc model)."""
        if is_cloud_tag(tag):
            raise ModelNotAllowedError(f"cloud model refused: {tag}")
        spec = self._by_tag.get(tag)
        if spec is None:
            raise ModelNotAllowedError(f"model not on the allowlist: {tag}")
        return spec


def _digest_matches(pinned: str, local: str) -> bool:
    p = pinned.lower().removeprefix("sha256:")
    loc = local.lower().removeprefix("sha256:")
    # Accept a pinned short id (>= 12 hex chars, as shown on ollama.com) or the full digest.
    return len(p) >= 12 and loc.startswith(p)
