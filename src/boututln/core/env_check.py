"""PC environment self-check (``boututln check-env``).

Runs on the user's Windows PC. In the cloud dev environment only the logic is
tested with injected values; GPU checks there prove nothing about the real PC.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from boututln.core.config import AppConfig
from boututln.core.env_guard import inspect_environment

Status = Literal["ok", "warn", "fail", "info"]


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


def run_checks(
    cfg: AppConfig,
    env: Mapping[str, str] | None = None,
    *,
    system: str | None = None,
    python_version: tuple[int, int] | None = None,
    which: object = shutil.which,
) -> list[Check]:
    env = dict(os.environ if env is None else env)
    system = system or platform.system()
    py = python_version or (sys.version_info.major, sys.version_info.minor)
    out: list[Check] = []

    out.append(Check("python", "ok" if py == (3, 12) else "fail", f"Python {py[0]}.{py[1]}"))
    out.append(
        Check("os", "ok" if system == "Windows" else "warn", f"{system} (target is Windows 11)")
    )

    want = {
        "OLLAMA_NO_CLOUD": "1",
        "OLLAMA_NUM_PARALLEL": "1",
        "OLLAMA_MAX_LOADED_MODELS": "1",
        "OLLAMA_CONTEXT_LENGTH": "8192",
    }
    for k, v in want.items():
        got = env.get(k)
        sev: Status = "ok" if got == v else ("fail" if k == "OLLAMA_NO_CLOUD" else "warn")
        out.append(Check(k, sev, f"expected {v}, found {got!r}"))

    rep = inspect_environment(env, cfg)
    for e in rep.errors:
        out.append(Check("environment", "fail", e))
    for w in rep.warnings:
        out.append(Check("environment", "warn", w))

    out.append(Check("trading_mode", "ok", f"{cfg.trading.mode} (live trading locked)"))
    data_dir = Path(cfg.paths.data_dir)
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        out.append(Check("data_dir", "ok", str(data_dir)))
    except OSError as exc:
        out.append(Check("data_dir", "fail", f"{data_dir}: {exc}"))

    try:
        import keyring

        backend = type(keyring.get_keyring()).__name__
        good = "fail" not in backend.lower() and "null" not in backend.lower()
        out.append(Check("keyring", "ok" if good else "warn", backend))
    except Exception as exc:  # noqa: BLE001 - report any keyring failure
        out.append(Check("keyring", "warn", f"unavailable: {exc}"))

    finder = which if callable(which) else shutil.which
    out.append(
        Check(
            "ollama_cli",
            "ok" if finder("ollama") else "warn",
            "found" if finder("ollama") else "ollama not on PATH",
        )
    )
    out.append(
        Check(
            "nvidia_smi",
            "info" if finder("nvidia-smi") else "warn",
            "found; verify GPU with 'ollama ps' during a run"
            if finder("nvidia-smi")
            else "nvidia-smi not found",
        )
    )
    return out


def has_failures(checks: list[Check]) -> bool:
    return any(c.status == "fail" for c in checks)
