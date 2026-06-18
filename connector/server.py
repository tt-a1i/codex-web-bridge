#!/usr/bin/env python3
"""Local-only HTTP transport for the codex-web-bridge MCP connector.

This is a readonly-by-default MCP endpoint built on the standard library. It
binds to loopback unless explicitly configured otherwise, and it gates every
request behind an owner token when one is set. Public exposure is intentionally
out of scope: a tunnel is user-managed configuration, and a tunnel URL is not a
secret, so the owner token is what actually protects access.

The protocol itself (initialize / initialized / tools/list / tools/call) lives
in ``protocol.py``. This module only handles HTTP transport and auth. Clients
POST JSON-RPC messages to ``/rpc``; notifications (no ``id``) receive HTTP 202
with no body, requests receive HTTP 200 with a JSON-RPC response.

Security boundaries enforced here:

- ``Origin`` header is validated to block DNS-rebinding from a browser pointed
  at the loopback server.
- ``Content-Type: application/json`` is required, rejecting browser "simple
  request" form posts that skip CORS preflight.
- Owner token is checked in constant time when configured.
- ``GET``/``DELETE`` return an explicit ``405`` (no server-initiated SSE stream
  is offered) instead of the default ``501``.

Auth: send the owner token via ``Authorization: Bearer <token>`` when configured.
Session: the server issues an ``Mcp-Session-Id`` on ``initialize``; clients echo
it on subsequent requests so initialization state is tracked per session.
"""

from __future__ import annotations

import argparse
import hmac
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from .config import ConnectorConfig, load_config
from .protocol import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    ProtocolHandler,
    error,
)
from .tools import ToolContext, ToolRegistry
from .workspace import WorkspaceRegistry

MAX_BODY_BYTES = 1 * 1024 * 1024
ENDPOINT = "/rpc"
# Loopback origins a local MCP host / inspector may legitimately use.
_ALLOWED_ORIGIN_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")


def build_registry(config: ConnectorConfig) -> ToolRegistry:
    ctx = ToolContext(config=config, registry=WorkspaceRegistry(config))
    return ToolRegistry(ctx)


def _make_handler(config: ConnectorConfig, protocol: ProtocolHandler, quiet: bool = False):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            if quiet:
                return
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
            presented = header[len(prefix):].encode("utf-8")
            return hmac.compare_digest(presented, config.owner_token.encode("utf-8"))

        def _origin_ok(self) -> bool:
            """Reject cross-origin browser requests (DNS-rebinding defense)."""
            origin = self.headers.get("Origin")
            if origin is None:
                return True  # non-browser clients omit Origin
            try:
                host = (urlparse(origin).hostname or "").lower()
            except ValueError:
                return False
            return host in _ALLOWED_ORIGIN_HOSTS

        def _send_json(self, status: int, obj: dict[str, Any],
                       session_id: str | None = None) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            if session_id:
                self.send_header("Mcp-Session-Id", session_id)
            self.end_headers()
            self.wfile.write(body)

        def _send_empty(self, status: int) -> None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _reject_method(self) -> None:
            # No server-initiated SSE stream offered: explicit 405 (not 501).
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            self._reject_method()

        def do_HEAD(self) -> None:  # noqa: N802
            self._reject_method()

        def do_DELETE(self) -> None:  # noqa: N802
            self._reject_method()

        def do_POST(self) -> None:  # noqa: N802
            if self.path != ENDPOINT:
                self._send_json(404, error(None, METHOD_NOT_FOUND, "not found"))
                return
            if not self._origin_ok():
                self._send_json(403, error(None, -32001, "forbidden origin"))
                return
            if not self._authorized():
                self._send_json(401, error(None, -32001, "unauthorized"))
                return
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ctype != "application/json":
                self._send_json(415, error(None, INVALID_REQUEST, "content-type must be application/json"))
                return
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
            except ValueError:
                self._send_json(400, error(None, INVALID_REQUEST, "bad content-length"))
                return
            if length > MAX_BODY_BYTES:
                self._send_json(413, error(None, INVALID_REQUEST, "request too large"))
                return
            raw = self.rfile.read(length) if length else b""
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._send_json(400, error(None, PARSE_ERROR, "parse error"))
                return
            if not isinstance(payload, dict):
                self._send_json(400, error(None, INVALID_REQUEST, "invalid request"))
                return

            # Track the session: issue one on initialize, echo client's otherwise.
            session_id = self.headers.get("Mcp-Session-Id")
            issued_session: str | None = None
            if payload.get("method") == "initialize":
                session_id = protocol.start_session(mark_initialized=True)
                issued_session = session_id

            try:
                response = protocol.handle(payload, session_id=session_id)
            except Exception:  # noqa: BLE001 - defense in depth
                # Never leak a traceback to the client; log locally only.
                sys.stderr.write("[connector] internal error handling request\n")
                self._send_json(500, error(payload.get("id"), INTERNAL_ERROR, "internal error"))
                return
            if response is None:
                self._send_empty(202)
                return
            self._send_json(200, response, session_id=issued_session)

    return Handler


def build_server(config: ConnectorConfig, quiet: bool = False) -> ThreadingHTTPServer:
    """Create the HTTP server without starting its loop (used by tests)."""
    registry = build_registry(config)
    protocol = ProtocolHandler(registry)
    handler = _make_handler(config, protocol, quiet=quiet)
    return ThreadingHTTPServer((config.host, config.port), handler)


def serve(config: ConnectorConfig) -> None:
    httpd = build_server(config)
    where = f"http://{config.host}:{config.port}/rpc"
    auth = "owner-token required" if config.owner_token else "NO owner token (loopback only)"
    sys.stderr.write(
        f"[connector] trust={config.trust_level} MCP listening on {where} ({auth})\n"
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
