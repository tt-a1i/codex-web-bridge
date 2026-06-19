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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import ConnectorConfig
from .workspace import WorkspaceRegistry

_WS_ID = {"type": "string", "description": "Workspace id from open_workspace."}
_REL_PATH = {"type": "string", "description": "Workspace-relative path."}

# Bounds applied to readonly responses.
MAX_READ_BYTES = 64 * 1024
MAX_LIST_ENTRIES = 500
MAX_SEARCH_RESULTS = 200
MAX_SEARCH_FILES = 5000  # cap files scanned per search to bound CPU/IO
MAX_SEARCH_FILE_BYTES = 1 * 1024 * 1024  # skip files larger than this in search
SEARCH_DEADLINE_SECONDS = 5.0
GIT_TIMEOUT = 10

_IGNORED_DIRS = {".git", "node_modules", "dist", "build", ".cache", "__pycache__"}


class ToolError(ValueError):
    """Raised when a tool call is invalid (e.g. unknown tool, bad arguments)."""


class ToolPermissionError(ToolError):
    """Raised when a tool exists but is above the configured trust level."""


@dataclass(frozen=True)
class Tool:
    name: str
    level: str  # one of TRUST_LEVELS
    handler: Callable[["ToolContext", dict[str, Any]], dict[str, Any]]
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


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
        # Don't forward raw git stderr (may contain absolute paths/config).
        raise ToolError("git command failed")
    return proc.stdout


# --- Readonly handlers -------------------------------------------------------


def _open_workspace(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    path = _require(args, "path")
    ws = ctx.registry.open_workspace(str(path))
    # Do not leak the absolute local root to the remote host; only the basename.
    return {"workspace_id": ws.workspace_id, "name": ws.root.name}


def _read(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    ws = ctx.registry.get(str(_require(args, "workspace_id")))
    rel = str(_require(args, "path"))
    target = ws.resolve_file(rel)
    data = target.read_bytes()[:MAX_READ_BYTES]
    text = data.decode("utf-8", errors="replace")
    truncated = target.stat().st_size > MAX_READ_BYTES
    return {"path": rel, "content": text, "truncated": truncated}


def _list(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    ws = ctx.registry.get(str(_require(args, "workspace_id")))
    target = ws.resolve_dir(str(args.get("path", ".")))
    entries: list[dict[str, Any]] = []
    truncated = False
    for child in sorted(target.iterdir()):
        if child.name in _IGNORED_DIRS:
            continue
        if len(entries) >= MAX_LIST_ENTRIES:
            truncated = True
            break
        entries.append(
            {
                "name": child.name,
                "dir": child.is_dir() and not child.is_symlink(),
                "symlink": child.is_symlink(),
            }
        )
    return {"entries": entries, "truncated": truncated}


def _search(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    ws = ctx.registry.get(str(_require(args, "workspace_id")))
    query = str(_require(args, "query"))
    if not query:
        raise ToolError("query must be non-empty")
    base = ws.resolve_dir(str(args.get("path", ".")))
    results: list[dict[str, Any]] = []
    deadline = time.monotonic() + SEARCH_DEADLINE_SECONDS
    scanned = 0
    for file in base.rglob("*"):
        if any(part in _IGNORED_DIRS for part in file.parts):
            continue
        # Re-validate containment for every candidate and skip symlinks so a
        # symlink inside the tree can't leak a file from outside the root.
        if not ws.contains(file):
            continue
        if not file.is_file():
            continue
        scanned += 1
        if scanned > MAX_SEARCH_FILES or time.monotonic() > deadline:
            return {"results": results, "truncated": True}
        try:
            if file.stat().st_size > MAX_SEARCH_FILE_BYTES:
                continue
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


def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _array_of(item: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": item}


READONLY_TOOLS = [
    Tool(
        "open_workspace", "readonly", _open_workspace,
        "Open a path inside an allowed root and return a workspace id.",
        {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Absolute path inside an allowed root."}},
            "required": ["path"],
        },
        _object_schema(
            {
                "workspace_id": {"type": "string"},
                "name": {"type": "string"},
            },
            ["workspace_id", "name"],
        ),
    ),
    Tool(
        "read", "readonly", _read,
        "Read bounded text from a workspace-relative file.",
        {
            "type": "object",
            "properties": {"workspace_id": _WS_ID, "path": _REL_PATH},
            "required": ["workspace_id", "path"],
        },
        _object_schema(
            {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "truncated": {"type": "boolean"},
            },
            ["path", "content", "truncated"],
        ),
    ),
    Tool(
        "list", "readonly", _list,
        "List a workspace-relative directory with bounded entries.",
        {
            "type": "object",
            "properties": {
                "workspace_id": _WS_ID,
                "path": {"type": "string", "description": "Workspace-relative directory (default '.')."},
            },
            "required": ["workspace_id"],
        },
        _object_schema(
            {
                "entries": _array_of(
                    _object_schema(
                        {
                            "name": {"type": "string"},
                            "dir": {"type": "boolean"},
                            "symlink": {"type": "boolean"},
                        },
                        ["name", "dir", "symlink"],
                    )
                ),
                "truncated": {"type": "boolean"},
            },
            ["entries", "truncated"],
        ),
    ),
    Tool(
        "search", "readonly", _search,
        "Search text with ignore rules and bounded results.",
        {
            "type": "object",
            "properties": {
                "workspace_id": _WS_ID,
                "query": {"type": "string", "description": "Substring to search for."},
                "path": {"type": "string", "description": "Workspace-relative base directory (default '.')."},
            },
            "required": ["workspace_id", "query"],
        },
        _object_schema(
            {
                "results": _array_of(
                    _object_schema(
                        {
                            "path": {"type": "string"},
                            "line": {"type": "integer"},
                            "text": {"type": "string"},
                        },
                        ["path", "line", "text"],
                    )
                ),
                "truncated": {"type": "boolean"},
            },
            ["results", "truncated"],
        ),
    ),
    Tool(
        "git_status", "readonly", _git_status,
        "Return branch, HEAD, and short status.",
        {
            "type": "object",
            "properties": {"workspace_id": _WS_ID},
            "required": ["workspace_id"],
        },
        _object_schema(
            {
                "branch": {"type": "string"},
                "head": {"type": "string"},
                "status": {"type": "string"},
            },
            ["branch", "head", "status"],
        ),
    ),
    Tool(
        "git_diff", "readonly", _git_diff,
        "Return bounded diff and stat for the workspace.",
        {
            "type": "object",
            "properties": {"workspace_id": _WS_ID},
            "required": ["workspace_id"],
        },
        _object_schema(
            {
                "stat": {"type": "string"},
                "diff": {"type": "string"},
                "truncated": {"type": "boolean"},
            },
            ["stat", "diff", "truncated"],
        ),
    ),
]


class ToolRegistry:
    """Holds tools and enforces trust-level permission before dispatch."""

    def __init__(self, ctx: ToolContext, tools: list[Tool] | None = None) -> None:
        self._ctx = ctx
        self._tools = {t.name: t for t in (tools if tools is not None else READONLY_TOOLS)}

    def available(self) -> list[Tool]:
        """Tools permitted at the configured trust level."""
        return [t for t in self._tools.values() if self._ctx.config.allows(t.level)]

    def describe(self) -> list[dict[str, Any]]:
        """MCP tool definitions for tools/list."""
        out: list[dict[str, Any]] = []
        for t in self.available():
            out.append(
                {
                    "name": t.name,
                    "title": t.name.replace("_", " ").title(),
                    "description": f"[{t.level}] {t.description}",
                    "inputSchema": t.input_schema,
                    "outputSchema": t.output_schema,
                    "annotations": {
                        "readOnlyHint": t.level == "readonly",
                        "destructiveHint": False,
                        "openWorldHint": False,
                    },
                }
            )
        return out

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"unknown tool: {name}")
        if not self._ctx.config.allows(tool.level):
            raise ToolPermissionError(
                f"tool {name!r} requires '{tool.level}' trust, "
                f"server is '{self._ctx.config.trust_level}'"
            )
        return tool.handler(self._ctx, args)
