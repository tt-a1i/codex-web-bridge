#!/usr/bin/env python3
"""Workspace resolution and path containment for the connector.

Path containment is the core security boundary for readonly mode. Every
workspace-relative path must resolve to a location strictly inside an allowed
root. Symlinks are never followed out of the root: a path is rejected if its
real (symlink-resolved) location escapes the root, and the final component must
not itself be a symlink. Comparisons are case-normalized so a case-insensitive
filesystem (default on macOS) cannot be used to slip a path past the check.
"""

from __future__ import annotations

import os
import secrets
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from .config import ConnectorConfig

# Cap concurrently open workspaces to bound memory against a looping host.
MAX_OPEN_WORKSPACES = 64


class WorkspaceError(ValueError):
    """Raised when a workspace or contained path request is invalid or unsafe."""


def _norm_parts(path: Path) -> tuple[str, ...]:
    """Case-normalized real-path components for containment comparison."""
    return Path(os.path.normcase(os.path.realpath(str(path)))).parts


def _parts_contain(root_parts: tuple[str, ...], target: Path) -> bool:
    """True if target's real path is root or strictly inside root.

    Uses realpath so any symlink in the chain is resolved before the prefix
    check, and normcase so a case-insensitive filesystem can't bypass it. We
    compare path components, not a bare string prefix, so a sibling like
    ``/a/foobar`` is not treated as inside ``/a/foo``.
    """
    target_parts = _norm_parts(target)
    return target_parts[: len(root_parts)] == root_parts


def is_contained(root: Path, target: Path) -> bool:
    """True if target's real path is root or strictly inside root."""
    return _parts_contain(_norm_parts(root), target)


@dataclass(frozen=True)
class Workspace:
    workspace_id: str
    root: Path

    @property
    def _root_parts(self) -> tuple[str, ...]:
        # Computed once per access; the root never changes for a frozen Workspace.
        return _norm_parts(self.root)

    def _resolve(self, relative: str) -> Path:
        if relative in ("", "."):
            return self.root
        candidate = Path(relative)
        if candidate.is_absolute():
            raise WorkspaceError(f"path must be workspace-relative: {relative}")
        if ".." in candidate.parts:
            raise WorkspaceError(f"path may not contain '..': {relative}")
        target = self.root / candidate
        if not _parts_contain(self._root_parts, target):
            raise WorkspaceError("path escapes workspace root")
        return target

    def resolve_dir(self, relative: str) -> Path:
        """Resolve a relative directory path with containment enforced."""
        target = self._resolve(relative)
        if target.is_symlink():
            raise WorkspaceError("path is a symlink")
        if not target.is_dir():
            raise WorkspaceError("not a directory")
        return target

    def resolve_file(self, relative: str) -> Path:
        """Resolve a relative file path with containment enforced.

        Rejects the final component being a symlink at all (even one pointing
        inside the root), so no link is ever followed during a read.
        """
        target = self._resolve(relative)
        if target.is_symlink():
            raise WorkspaceError("path is a symlink")
        if not target.is_file():
            raise WorkspaceError("not a file")
        return target

    def contains(self, path: Path) -> bool:
        """True if a discovered path is inside the root and is not a symlink.

        Used to re-validate every candidate during search so an in-tree symlink
        cannot leak a file from outside the root.
        """
        if path.is_symlink():
            return False
        return _parts_contain(self._root_parts, path)


class WorkspaceRegistry:
    """Tracks opened workspaces, gated by the configured allowed roots.

    Bounded with LRU eviction so a host cannot exhaust memory by opening
    workspaces in a loop.
    """

    def __init__(self, config: ConnectorConfig) -> None:
        self._config = config
        self._workspaces: "OrderedDict[str, Workspace]" = OrderedDict()

    def _is_under_allowed_root(self, path: Path) -> Path | None:
        for root in self._config.allowed_roots:
            if is_contained(root, path):
                return root
        return None

    def open_workspace(self, path: str) -> Workspace:
        """Open a path inside an allowed root and return a Workspace."""
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            raise WorkspaceError("path does not exist")
        if not resolved.is_dir():
            raise WorkspaceError("path is not a directory")
        if self._is_under_allowed_root(resolved) is None:
            raise WorkspaceError("path is not inside any allowed root")
        workspace_id = "ws_" + secrets.token_hex(6)
        workspace = Workspace(workspace_id=workspace_id, root=resolved)
        self._workspaces[workspace_id] = workspace
        self._workspaces.move_to_end(workspace_id)
        while len(self._workspaces) > MAX_OPEN_WORKSPACES:
            self._workspaces.popitem(last=False)
        return workspace

    def get(self, workspace_id: str) -> Workspace:
        try:
            ws = self._workspaces[workspace_id]
        except KeyError:
            raise WorkspaceError(f"unknown workspace id: {workspace_id}") from None
        self._workspaces.move_to_end(workspace_id)
        return ws

    def list_open(self) -> list[Workspace]:
        return list(self._workspaces.values())
