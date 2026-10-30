"""Secret storage interface.

Production uses Windows Credential Manager through the ``keyring`` package.
Secrets are never stored in code, config files or the repository. Every secret
read through a store is registered with the log masker.
"""

from __future__ import annotations

import os
from typing import Protocol

from boututln.core.errors import ConfigError
from boututln.core.masking import MASK, register_secret

KEYRING_SERVICE = "boututln"


class SecretValue:
    """Holds a secret; ``str()``/``repr()`` never reveal it."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not value:
            raise ValueError("empty secret")
        self._value = value
        register_secret(value)

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"SecretValue({MASK})"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SecretValue) and other._value == self._value

    def __hash__(self) -> int:
        return hash(("SecretValue", self._value))


class SecretStore(Protocol):
    name: str

    def get(self, key: str) -> SecretValue | None: ...


class InMemorySecretStore:
    """Test-only store."""

    name = "memory"

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = dict(values or {})

    def get(self, key: str) -> SecretValue | None:
        v = self._values.get(key)
        return SecretValue(v) if v else None

    def set(self, key: str, value: str) -> None:
        self._values[key] = value


class EnvSecretStore:
    """Development-only store reading ``BOUTUTLN_SECRET_<KEY>`` variables."""

    name = "env"
    PREFIX = "BOUTUTLN_SECRET_"

    def __init__(self, environ: dict[str, str] | None = None) -> None:
        self._env = environ if environ is not None else dict(os.environ)

    def get(self, key: str) -> SecretValue | None:
        v = self._env.get(self.PREFIX + key.upper())
        return SecretValue(v) if v else None


class KeyringSecretStore:
    """Windows Credential Manager (or the OS keyring) via ``keyring``."""

    name = "keyring"

    def __init__(self, service: str = KEYRING_SERVICE) -> None:
        self._service = service

    def get(self, key: str) -> SecretValue | None:
        import keyring  # imported lazily so tests do not need a keyring backend

        v = keyring.get_password(self._service, key)
        return SecretValue(v) if v else None


def build_secret_store(kind: str, environment: str) -> SecretStore:
    if kind == "keyring":
        return KeyringSecretStore()
    if kind == "env":
        if environment == "production":
            raise ConfigError("env secret store is not allowed in production; use keyring")
        return EnvSecretStore()
    if kind == "memory":
        return InMemorySecretStore()
    raise ConfigError(f"unknown secret store: {kind}")
