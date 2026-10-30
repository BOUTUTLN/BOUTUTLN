from __future__ import annotations

import logging
from pathlib import Path

import pytest

from boututln.core.config import AIConfig, AppConfig, DashboardConfig, load_config
from boututln.core.env_guard import enforce_environment, inspect_environment
from boututln.core.errors import ConfigError, LiveTradingLockedError, UnsafeEnvironmentError
from boututln.core.masking import MASK, install_masking, mask_text
from boututln.core.secrets import (
    EnvSecretStore,
    InMemorySecretStore,
    SecretValue,
    build_secret_store,
)
from tests.conftest import SAFE_ENV, SETTINGS


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "local.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_default_settings_are_safe(cfg: AppConfig) -> None:
    assert cfg.trading.mode == "backtest"
    assert cfg.ai.enabled is False
    assert cfg.kb.network_enabled is False
    assert cfg.dashboard.host == "127.0.0.1"
    assert cfg.secret_store == "keyring"
    assert [m.tag for m in cfg.ai.models] == [
        "qwen3.5:9b-q4_K_M",
        "gemma4:12b-it-q4_K_M",
        "qwen3.5:4b-q4_K_M",
    ]
    assert cfg.ai.options.num_ctx == 8192 and str(cfg.ai.options.temperature) == "0.1"
    kb = next(p for p in cfg.providers if p.name == "kb_openapi")
    assert kb.cost_class == "unknown"  # KB fee not confirmed -> never assumed free


def test_paper_starting_cash_is_configurable(tmp_path: Path) -> None:
    env = dict(SAFE_ENV, BOUTUTLN_DATA_DIR=str(tmp_path))
    local = _write(
        tmp_path, '[paper]\ninitial_cash_krw = "5000000"\ninitial_cash_usd = "3500.50"\n'
    )
    cfg = load_config([SETTINGS, local], env)
    assert str(cfg.paper.initial_cash_krw) == "5000000"
    assert str(cfg.paper.initial_cash_usd) == "3500.50"
    for bad in (
        "initial_cash_usd = 3500.5",
        'initial_cash_krw = "100.5"',
        'initial_cash_krw = "-1"',
    ):
        with pytest.raises(ConfigError):
            load_config([SETTINGS, _write(tmp_path, f"[paper]\n{bad}\n")], env)


def test_missing_required_setting(tmp_path: Path) -> None:
    empty = _write(tmp_path, "[trading]\nmode = 'paper'\n")
    with pytest.raises(ConfigError, match="paths.data_dir"):
        load_config([empty], SAFE_ENV)


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    typo = _write(tmp_path, "[trading]\nmod = 'paper'\n")
    with pytest.raises(ConfigError):
        load_config([SETTINGS, typo], dict(SAFE_ENV, BOUTUTLN_DATA_DIR=str(tmp_path)))


def test_live_mode_is_locked_in_file_and_env(tmp_path: Path) -> None:
    live = _write(tmp_path, "[trading]\nmode = 'LIVE'\n")
    env = dict(SAFE_ENV, BOUTUTLN_DATA_DIR=str(tmp_path))
    with pytest.raises(LiveTradingLockedError):
        load_config([SETTINGS, live], env)
    with pytest.raises(LiveTradingLockedError):
        load_config([SETTINGS], dict(env, BOUTUTLN_TRADING_MODE="live"))
    for flag in ("LIVE_TRADING_ENABLED", "BOUTUTLN_ALLOW_ORDERS", "BOUTUTLN_KB_ORDER_API"):
        with pytest.raises(LiveTradingLockedError):
            load_config([SETTINGS], dict(env, **{flag: "true"}))


def test_dangerous_env_combinations(cfg: AppConfig, tmp_path: Path) -> None:
    with pytest.raises(UnsafeEnvironmentError):
        enforce_environment(dict(SAFE_ENV, OLLAMA_HOST="0.0.0.0:11434"), cfg)
    with pytest.raises(UnsafeEnvironmentError):
        enforce_environment(dict(SAFE_ENV, BOUTUTLN_DB_PATH="data/db/live.db"), cfg)
    rep = inspect_environment(dict(SAFE_ENV, OPENAI_API_KEY="sk-x"), cfg)
    assert any("OPENAI_API_KEY" in w for w in rep.warnings) and not rep.errors
    enforce_environment(dict(SAFE_ENV, OLLAMA_HOST="127.0.0.1:11434"), cfg)


def test_ai_enabled_requires_no_cloud_and_pinned_digests(tmp_path: Path) -> None:
    env = dict(SAFE_ENV, BOUTUTLN_DATA_DIR=str(tmp_path))
    on = _write(tmp_path, "[ai]\nenabled = true\n")
    with pytest.raises(ConfigError, match="digest"):
        load_config([SETTINGS, on], env)
    with pytest.raises(ValueError):
        AIConfig(models=[{"role": "primary", "tag": "gpt-oss:120b-cloud", "digest": "abc"}])  # type: ignore[list-item]
    with pytest.raises(ValueError):
        AIConfig(endpoint="https://ollama.com")
    ok = AIConfig(
        enabled=True,
        models=[
            {
                "role": "primary",
                "tag": "qwen3.5:9b-q4_K_M",  # type: ignore[list-item]
                "digest": "56671c2ab938",
            }
        ],
    )
    cfg = load_config([SETTINGS], env)
    no_cloud_env = {k: v for k, v in SAFE_ENV.items() if k != "OLLAMA_NO_CLOUD"}
    with pytest.raises(UnsafeEnvironmentError):
        enforce_environment(no_cloud_env, cfg.model_copy(update={"ai": ok}))


def test_dashboard_loopback_only_and_production_rules(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        DashboardConfig(host="0.0.0.0")
    env = dict(
        SAFE_ENV,
        BOUTUTLN_DATA_DIR=str(tmp_path),
        BOUTUTLN_ENV="production",
        BOUTUTLN_SECRET_STORE="env",
    )
    with pytest.raises(ConfigError):
        load_config([SETTINGS], env)
    with pytest.raises(ConfigError):
        build_secret_store("env", "production")


def test_secret_values_never_print(caplog: pytest.LogCaptureFixture) -> None:
    store = InMemorySecretStore({"kb_app_secret": "S3cr3tValue!"})
    secret = store.get("kb_app_secret")
    assert secret is not None
    assert "S3cr3t" not in repr(secret) and "S3cr3t" not in str(secret)
    logger = logging.getLogger("test.masking")
    install_masking(logger)
    with caplog.at_level(logging.INFO, logger="test.masking"):
        logger.info("calling with %s and %s", secret.reveal(), "appKey=ABCD-1234-EFGH")
        logger.info("header Authorization: Bearer eyJhbGciOiJIUzUxMiJ9.payload.sig")
    text = caplog.text
    assert "S3cr3tValue!" not in text and "ABCD-1234-EFGH" not in text
    assert "eyJhbGciOiJIUzUxMiJ9" not in text and MASK in text


def test_mask_patterns() -> None:
    assert "xyz789" not in mask_text('{"appSecret": "xyz789"}')
    assert "tok123" not in mask_text("access_token=tok123&x=1")
    assert mask_text("price=71200") == "price=71200"


def test_env_secret_store_dev_only() -> None:
    st = EnvSecretStore({"BOUTUTLN_SECRET_KB_APP_KEY": "devkey123"})
    v = st.get("kb_app_key")
    assert isinstance(v, SecretValue) and v.reveal() == "devkey123"
    assert st.get("missing") is None
