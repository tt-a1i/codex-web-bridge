#!/usr/bin/env python3
"""Workspace resolution and path containment for the connector.

Path containment is the core security boundary for readonly mode. Every
workspace-relative path must resolve to a location strictly inside an allowed
root, with symlink escapes rejected.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path

from .config import ConnectorConfig


class WorkspaceError(ValueError):
    """Raised when a workspace or contained path request is invalid or unsafe."""


def _within(root: Path, target: Path) -> bool:
    """Return True if resolved target is root or strictly inside root."""
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class Workspace:
    workspace_id: str
    root: Path

    def resolve(self, relative: str) -> Path:
        """Resolve a workspace-relative path, enforcing containment.

        Rejects absolute paths, parent-traversal escapes, and symlink targets
        that point outside the workspace root.
        """

        if relative in ("", "."):
            return self.root

        candidate = Path(relative)
        if candidate.is_absolute():
            raise WorkspaceError(f"path must be workspace-relative: {relative}")

        # Resolve through symlinks, then verify containment on the real path.
        resolved = (self.root / candidate).resolve()
        if not _within(self.root, resolved):
            raise WorkspaceError(f"path escapes workspace root: {relative}")
        return resolved


class WorkspaceRegistry:
    """Tracks opened workspaces, gated by the configured allowed roots."""

    def __init__(self, config: ConnectorConfig) -> None:
        self._config = config
        self._workspaces: dict[str, Workspace] = {}

    def _is_under_allowed_root(self, path: Path) -> Path | None:
        for root in self._config.allowed_roots:
            if path == root or _within(root, path):
                return root
        return None

    def open_workspace(self, path: str) -> Workspace:
        """Open a path inside an allowed root and return a Workspace."""
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            raise WorkspaceError(f"path does not exist: {resolved}")
        if not resolved.is_dir():
            raise WorkspaceError(f"path is not a directory: {resolved}")
        if self._is_under_allowed_root(resolved) is None:
            raise WorkspaceError(
                f"path is not inside any allowed root: {resolved}"
            )
        workspace_id = "ws_" + secrets.token_hex(6)
        workspace = Workspace(workspace_id=workspace_id, root=resolved)
        self._workspaces[workspace_id] = workspace
        return workspace

    def get(self, workspace_id: str) -> Workspace:
        try:
            return self._workspaces[workspace_id]
        except KeyError:
            raise WorkspaceError(f"unknown workspace id: {workspace_id}") from None

    def list_open(self) -> list[Workspace]:
        return list(self._workspaces.values())
