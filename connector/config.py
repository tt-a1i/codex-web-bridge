#!/usr/bin/env python3
"""Configuration and trust model for the codex-web-bridge connector.

Defaults to the lowest useful trust level. Broad roots such as ``~``, ``/``, or
a whole user profile directory are rejected. Public tunnel setup is never done
here; a tunnel URL is treated as user-managed configuration, not a secret.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


TRUST_LEVELS = ("readonly", "review", "execute")

# Roots that are too broad to allow even with explicit opt-in.
_FORBIDDEN_ROOTS = {
    Path("/"),
    Path.home(),
}


class ConfigError(ValueError):
    """Raised when connector configuration is invalid or unsafe."""


def _resolve(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


@dataclass(frozen=True)
class ConnectorConfig:
    """Validated connector configuration.

    Attributes:
        allowed_roots: Resolved directories the connector may open workspaces in.
        trust_level: One of TRUST_LEVELS. The first public connector ships
            ``readonly``; higher levels require explicit user opt-in.
        host: Bind host. Defaults to loopback so nothing is exposed without a
            user-managed tunnel.
        port: Bind port.
        owner_token: Shared secret an MCP host must present. Required before any
            non-loopback exposure; recommended even for loopback.
    """

    allowed_roots: tuple[Path, ...]
    trust_level: str = "readonly"
    host: str = "127.0.0.1"
    port: int = 8765
    owner_token: str | None = None

    def __post_init__(self) -> None:
        if self.trust_level not in TRUST_LEVELS:
            raise ConfigError(
                f"trust_level must be one of {TRUST_LEVELS}, got {self.trust_level!r}"
            )
        if not self.allowed_roots:
            raise ConfigError("at least one allowed root is required")
        for root in self.allowed_roots:
            if not root.is_absolute():
                raise ConfigError(f"allowed root must be absolute: {root}")
            if root in _FORBIDDEN_ROOTS:
                raise ConfigError(f"allowed root is too broad to be safe: {root}")
        if not self._is_loopback() and not self.owner_token:
            raise ConfigError(
                "owner_token is required when binding to a non-loopback host"
            )

    def _is_loopback(self) -> bool:
        return self.host in {"127.0.0.1", "::1", "localhost"}

    def is_loopback(self) -> bool:
        return self._is_loopback()

    def allows(self, tool_level: str) -> bool:
        """Return True if the configured trust level permits a tool's level."""
        return TRUST_LEVELS.index(tool_level) <= TRUST_LEVELS.index(self.trust_level)


def normalize_roots(roots: list[str]) -> tuple[Path, ...]:
    resolved: list[Path] = []
    for raw in roots:
        path = _resolve(raw)
        if not path.exists():
            raise ConfigError(f"allowed root does not exist: {path}")
        if not path.is_dir():
            raise ConfigError(f"allowed root is not a directory: {path}")
        resolved.append(path)
    return tuple(resolved)


def load_config(path: str | Path) -> ConnectorConfig:
    """Load and validate a JSON config file.

    Expected shape::

        {
          "allowed_roots": ["/abs/path/to/repo"],
          "trust_level": "readonly",
          "host": "127.0.0.1",
          "port": 8765,
          "owner_token": "..."   // optional for loopback
        }
    """

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError("config root must be a JSON object")

    raw_roots = data.get("allowed_roots")
    if not isinstance(raw_roots, list) or not raw_roots:
        raise ConfigError("config must list a non-empty 'allowed_roots' array")

    return ConnectorConfig(
        allowed_roots=normalize_roots([str(r) for r in raw_roots]),
        trust_level=str(data.get("trust_level", "readonly")),
        host=str(data.get("host", "127.0.0.1")),
        port=int(data.get("port", 8765)),
        owner_token=(str(data["owner_token"]) if data.get("owner_token") else None),
    )
