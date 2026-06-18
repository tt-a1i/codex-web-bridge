#!/usr/bin/env python3
"""MCP protocol layer for the connector.

Implements the Model Context Protocol lifecycle (initialize -> initialized ->
operation) on top of JSON-RPC 2.0, so an MCP host such as ChatGPT Pro or Claude
can connect and drive the readonly tools. The transport (HTTP) lives in
``server.py``; this module is transport-agnostic and turns a parsed JSON-RPC
message into a JSON-RPC response (or ``None`` for notifications).

Two error channels per the MCP spec:

- Protocol errors (unknown method, bad params, unknown tool) -> JSON-RPC error.
- Tool execution errors (path escape, missing file) -> a normal result with
  ``isError: true`` and the message in a text content block.
"""

from __future__ import annotations

import json
import secrets
import threading
from typing import Any

from . import __version__
from .tools import ToolError, ToolPermissionError, ToolRegistry
from .workspace import WorkspaceError

# Protocol versions this server speaks, newest first.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

SERVER_INFO = {
    "name": "codex-web-bridge-connector",
    "title": "Codex Web Bridge Connector",
    "version": __version__,
}

# JSON-RPC error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
# Server-defined: request received before initialize completed.
NOT_INITIALIZED = -32002


def error(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def result(req_id: Any, payload: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": payload}


def _negotiate_version(requested: Any) -> str:
    """Return same version if supported, else our latest (per MCP spec)."""
    if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return LATEST_PROTOCOL_VERSION


def _tool_result(payload: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    """Wrap a handler payload into an MCP tool result.

    Returns both a serialized text content block (for unstructured clients) and
    structuredContent (for structured clients), per the tools spec.
    """
    text = json.dumps(payload, ensure_ascii=False)
    out: dict[str, Any] = {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }
    if not is_error:
        out["structuredContent"] = payload
    return out


class SessionStore:
    """Thread-safe record of which sessions have completed initialization.

    The HTTP server is threaded and a single ProtocolHandler is shared across
    connections, so initialization state must be keyed per session rather than
    held as a single mutable flag.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._initialized: set[str] = set()

    @staticmethod
    def new_session_id() -> str:
        return secrets.token_hex(16)

    def mark_initialized(self, session_id: str) -> None:
        with self._lock:
            self._initialized.add(session_id)

    def is_initialized(self, session_id: str | None) -> bool:
        if not session_id:
            return False
        with self._lock:
            return session_id in self._initialized


class ProtocolHandler:
    """MCP handler. Stateless except for the shared, thread-safe SessionStore."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._sessions = SessionStore()

    # --- public API ---------------------------------------------------------

    def handle(
        self, message: dict[str, Any], session_id: str | None = None
    ) -> dict[str, Any] | None:
        """Handle one JSON-RPC message for a session.

        Returns a response dict, or None for notifications. ``session_id`` keys
        per-session initialization state.
        """
        method = message.get("method")
        is_notification = "id" not in message
        req_id = message.get("id")

        if not isinstance(method, str):
            if is_notification:
                return None
            return error(req_id, INVALID_REQUEST, "method must be a string")

        if is_notification:
            self._handle_notification(method, session_id)
            return None

        return self._handle_request(
            req_id, method, message.get("params") or {}, session_id
        )

    def start_session(self, *, mark_initialized: bool = False) -> str:
        """Create a new session id, optionally marking it initialized.

        Over HTTP the handshake is often coalesced, so the transport may mark a
        session initialized as soon as the ``initialize`` request is processed
        rather than waiting for a separate ``notifications/initialized``.
        """
        session_id = self._sessions.new_session_id()
        if mark_initialized:
            self._sessions.mark_initialized(session_id)
        return session_id

    # --- internals ----------------------------------------------------------

    def _handle_notification(self, method: str, session_id: str | None) -> None:
        if method == "notifications/initialized" and session_id:
            self._sessions.mark_initialized(session_id)
        # Unknown notifications are silently ignored per JSON-RPC.

    def _handle_request(
        self,
        req_id: Any,
        method: str,
        params: dict[str, Any],
        session_id: str | None,
    ) -> dict[str, Any]:
        if method == "initialize":
            return self._initialize(req_id, params)
        if method == "ping":
            return result(req_id, {})
        # All other requests require a completed handshake.
        if not self._sessions.is_initialized(session_id):
            return error(
                req_id, NOT_INITIALIZED, "session not initialized; call initialize first"
            )
        if method == "tools/list":
            return result(req_id, {"tools": self._registry.describe()})
        if method == "tools/call":
            return self._tools_call(req_id, params)
        return error(req_id, METHOD_NOT_FOUND, f"unknown method: {method}")

    def _initialize(self, req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        version = _negotiate_version(params.get("protocolVersion"))
        return result(
            req_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": (
                    "Readonly connector. Open a workspace inside an allowed root, "
                    "then read/list/search files and inspect git state."
                ),
            },
        )

    def _tools_call(self, req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str):
            return error(req_id, INVALID_PARAMS, "params.name must be a string")
        if not isinstance(arguments, dict):
            return error(req_id, INVALID_PARAMS, "params.arguments must be an object")
        try:
            payload = self._registry.call(name, arguments)
        except ToolPermissionError as exc:
            # Tool exists but is above trust level -> tool execution error so the
            # model can see and reason about the denial.
            return result(req_id, _tool_result({"error": str(exc)}, is_error=True))
        except ToolError as exc:
            # Unknown tool / bad arguments -> protocol error.
            return error(req_id, INVALID_PARAMS, str(exc))
        except WorkspaceError as exc:
            # Containment failure -> tool execution error.
            return result(req_id, _tool_result({"error": str(exc)}, is_error=True))
        except OSError:
            # IO error / TOCTOU race (file removed, permission denied) -> generic
            # tool error. Never forward the OS message; it can contain abs paths.
            return result(req_id, _tool_result({"error": "io error"}, is_error=True))
        return result(req_id, _tool_result(payload))
