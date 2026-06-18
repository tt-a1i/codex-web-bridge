#!/usr/bin/env python3
"""Local-only JSON-RPC server for the codex-web-bridge connector.

This is a minimal, readonly-by-default MCP-style endpoint built on the standard
library. It binds to loopback unless explicitly configured otherwise, and it
gates every request behind an owner token when one is set. Public exposure is
intentionally out of scope: a tunnel is user-managed configuration, and a tunnel
URL is not a secret, so the owner token is what actually protects access.

Wire shape (JSON-RPC 2.0 subset) at POST /rpc::

    {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
     "params": {"name": "open_workspace", "arguments": {"path": "..."}}}

Auth: send the owner token via ``Authorization: Bearer <token>`` when configured.
"""

from __future__ import annotations

import argparse
import hmac
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import ConnectorConfig, load_config
from .tools import ToolContext, ToolError, ToolRegistry
from .workspace import WorkspaceError, WorkspaceRegistry

MAX_BODY_BYTES = 1 * 1024 * 1024


def build_registry(config: ConnectorConfig) -> ToolRegistry:
    ctx = ToolContext(config=config, registry=WorkspaceRegistry(config))
    return ToolRegistry(ctx)


def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def dispatch(registry: ToolRegistry, payload: dict[str, Any]) -> dict[str, Any]:
    req_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}

    if method == "tools/list":
        return _result(req_id, {"tools": registry.describe()})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str):
            return _error(req_id, -32602, "params.name must be a string")
        try:
            return _result(req_id, registry.call(name, arguments))
        except (ToolError, WorkspaceError) as exc:
            return _error(req_id, -32000, str(exc))

    return _error(req_id, -32601, f"unknown method: {method}")


def _make_handler(config: ConnectorConfig, registry: ToolRegistry):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            # Log method + path only; never request bodies or file contents.
            sys.stderr.write(
                f"[connector] {self.command} {self.path} {fmt % args}\n"
            )

        def _authorized(self) -> bool:
            if not config.owner_token:
                return True
            header = self.headers.get("Authorization", "")
            prefix = "Bearer "
            if not header.startswith(prefix):
                return False
            presented = header[len(prefix):]
            return hmac.compare_digest(presented, config.owner_token)

        def _send_json(self, status: int, obj: dict[str, Any]) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/rpc":
                self._send_json(404, _error(None, -32601, "not found"))
                return
            if not self._authorized():
                self._send_json(401, _error(None, -32001, "unauthorized"))
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length > MAX_BODY_BYTES:
                self._send_json(413, _error(None, -32600, "request too large"))
                return
            raw = self.rfile.read(length) if length else b""
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._send_json(400, _error(None, -32700, "parse error"))
                return
            if not isinstance(payload, dict):
                self._send_json(400, _error(None, -32600, "invalid request"))
                return
            self._send_json(200, dispatch(registry, payload))

    return Handler


def serve(config: ConnectorConfig) -> None:
    registry = build_registry(config)
    handler = _make_handler(config, registry)
    httpd = ThreadingHTTPServer((config.host, config.port), handler)
    where = f"http://{config.host}:{config.port}/rpc"
    auth = "owner-token required" if config.owner_token else "NO owner token (loopback only)"
    sys.stderr.write(
        f"[connector] readonly={config.trust_level} listening on {where} ({auth})\n"
    )
    if not config.is_loopback():
        sys.stderr.write(
            "[connector] WARNING: bound to a non-loopback host. Ensure the tunnel "
            "and owner token are user-managed and intentional.\n"
        )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("[connector] shutting down\n")
    finally:
        httpd.server_close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Path to connector JSON config.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    config = load_config(args.config)
    serve(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
