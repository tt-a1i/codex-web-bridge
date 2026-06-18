#!/usr/bin/env python3
"""Readonly tool surface for the connector.

Every tool declares the lowest trust level that permits it. The registry
enforces that level against the configured trust level before dispatch, so a
readonly server can never run a review or execute tool even if a host requests
it. Outputs are bounded to keep transmission cheap and to avoid leaking large
file contents wholesale.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import ConnectorConfig
from .workspace import WorkspaceRegistry

# Bounds applied to readonly responses.
MAX_READ_BYTES = 64 * 1024
MAX_LIST_ENTRIES = 500
MAX_SEARCH_RESULTS = 200
GIT_TIMEOUT = 10

_IGNORED_DIRS = {".git", "node_modules", "dist", "build", ".cache", "__pycache__"}


class ToolError(ValueError):
    """Raised when a tool call is invalid or not permitted."""


@dataclass(frozen=True)
class Tool:
    name: str
    level: str  # one of TRUST_LEVELS
    handler: Callable[["ToolContext", dict[str, Any]], dict[str, Any]]
    description: str


@dataclass(frozen=True)
class ToolContext:
    config: ConnectorConfig
    registry: WorkspaceRegistry


def _require(args: dict[str, Any], key: str) -> Any:
    if key not in args:
        raise ToolError(f"missing required argument: {key}")
    return args[key]


def _git(root: Path, git_args: list[str]) -> str:
    try:
        proc = subprocess.run(
            ["git", *git_args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        raise ToolError("git is not installed") from None
    except subprocess.TimeoutExpired:
        raise ToolError("git command timed out") from None
    if proc.returncode != 0:
        raise ToolError(f"git failed: {proc.stderr.strip() or 'unknown error'}")
    return proc.stdout


# --- Readonly handlers -------------------------------------------------------


def _open_workspace(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    path = _require(args, "path")
    ws = ctx.registry.open_workspace(str(path))
    return {"workspace_id": ws.workspace_id, "root": str(ws.root)}


def _read(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    ws = ctx.registry.get(str(_require(args, "workspace_id")))
    target = ws.resolve(str(_require(args, "path")))
    if not target.is_file():
        raise ToolError(f"not a file: {args['path']}")
    data = target.read_bytes()[:MAX_READ_BYTES]
    text = data.decode("utf-8", errors="replace")
    truncated = target.stat().st_size > MAX_READ_BYTES
    return {"path": str(args["path"]), "content": text, "truncated": truncated}


def _list(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    ws = ctx.registry.get(str(_require(args, "workspace_id")))
    target = ws.resolve(str(args.get("path", ".")))
    if not target.is_dir():
        raise ToolError(f"not a directory: {args.get('path', '.')}")
    entries: list[dict[str, Any]] = []
    for child in sorted(target.iterdir()):
        if child.name in _IGNORED_DIRS:
            continue
        entries.append({"name": child.name, "dir": child.is_dir()})
        if len(entries) >= MAX_LIST_ENTRIES:
            break
    return {"entries": entries, "truncated": len(entries) >= MAX_LIST_ENTRIES}


def _search(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    ws = ctx.registry.get(str(_require(args, "workspace_id")))
    query = str(_require(args, "query"))
    if not query:
        raise ToolError("query must be non-empty")
    base = ws.resolve(str(args.get("path", ".")))
    results: list[dict[str, Any]] = []
    for file in base.rglob("*"):
        if any(part in _IGNORED_DIRS for part in file.parts):
            continue
        if not file.is_file():
            continue
        try:
            for line_no, line in enumerate(
                file.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
            ):
                if query in line:
                    results.append(
                        {
                            "path": str(file.relative_to(ws.root)),
                            "line": line_no,
                            "text": line.strip()[:200],
                        }
                    )
                    if len(results) >= MAX_SEARCH_RESULTS:
                        return {"results": results, "truncated": True}
        except OSError:
            continue
    return {"results": results, "truncated": False}


def _git_status(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    ws = ctx.registry.get(str(_require(args, "workspace_id")))
    branch = _git(ws.root, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    head = _git(ws.root, ["rev-parse", "--short", "HEAD"]).strip()
    status = _git(ws.root, ["status", "--short"])
    return {"branch": branch, "head": head, "status": status}


def _git_diff(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    ws = ctx.registry.get(str(_require(args, "workspace_id")))
    stat = _git(ws.root, ["diff", "--stat"])
    diff = _git(ws.root, ["diff"])[:MAX_READ_BYTES]
    return {"stat": stat, "diff": diff, "truncated": len(diff) >= MAX_READ_BYTES}


READONLY_TOOLS = [
    Tool("open_workspace", "readonly", _open_workspace,
         "Open a path inside an allowed root and return a workspace id."),
    Tool("read", "readonly", _read,
         "Read bounded text from a workspace-relative file."),
    Tool("list", "readonly", _list,
         "List a workspace-relative directory with bounded entries."),
    Tool("search", "readonly", _search,
         "Search text with ignore rules and bounded results."),
    Tool("git_status", "readonly", _git_status,
         "Return branch, HEAD, and short status."),
    Tool("git_diff", "readonly", _git_diff,
         "Return bounded diff and stat for the workspace."),
]


class ToolRegistry:
    """Holds tools and enforces trust-level permission before dispatch."""

    def __init__(self, ctx: ToolContext, tools: list[Tool] | None = None) -> None:
        self._ctx = ctx
        self._tools = {t.name: t for t in (tools if tools is not None else READONLY_TOOLS)}

    def available(self) -> list[Tool]:
        """Tools permitted at the configured trust level."""
        return [t for t in self._tools.values() if self._ctx.config.allows(t.level)]

    def describe(self) -> list[dict[str, str]]:
        return [
            {"name": t.name, "level": t.level, "description": t.description}
            for t in self.available()
        ]

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"unknown tool: {name}")
        if not self._ctx.config.allows(tool.level):
            raise ToolError(
                f"tool {name!r} requires '{tool.level}' trust, "
                f"server is '{self._ctx.config.trust_level}'"
            )
        return tool.handler(self._ctx, args)
