#!/usr/bin/env python3
"""End-to-end tests for the HTTP transport.

Spins up a real ThreadingHTTPServer on an ephemeral loopback port and drives it
with http.client, exercising the transport behaviors that unit tests can't:
auth, Origin, method gating, Content-Type, and the Mcp-Session-Id handshake.

Run with: python3 -m unittest connector.tests.test_server
"""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from connector.config import ConnectorConfig
from connector.server import build_server

TOKEN = "test-owner-token"


class ServerE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name).resolve()
        (cls.root / "hello.txt").write_text("hello e2e", encoding="utf-8")
        config = ConnectorConfig(
            allowed_roots=(cls.root,), host="127.0.0.1", port=0, owner_token=TOKEN
        )
        cls.httpd = build_server(config, quiet=True)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)
        cls.tmp.cleanup()

    # --- helpers ------------------------------------------------------------

    def _request(self, method, path="/mcp", body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, dict(resp.getheaders()), data
        finally:
            conn.close()

    def _rpc(
        self, payload, *, path="/mcp", token=TOKEN, origin=None,
        session=None, ctype="application/json"
    ):
        headers = {}
        if ctype is not None:
            headers["Content-Type"] = ctype
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if origin is not None:
            headers["Origin"] = origin
        if session is not None:
            headers["Mcp-Session-Id"] = session
        status, resp_headers, data = self._request(
            "POST", path=path, body=json.dumps(payload), headers=headers
        )
        parsed = json.loads(data) if data else None
        return status, resp_headers, parsed

    def _handshake(self) -> str:
        status, headers, body = self._rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["result"]["protocolVersion"], "2025-06-18")
        sid = headers.get("Mcp-Session-Id")
        self.assertTrue(sid)
        return sid

    # --- transport gating ---------------------------------------------------

    def test_get_returns_405(self) -> None:
        status, headers, _ = self._request("GET", headers={"Authorization": f"Bearer {TOKEN}"})
        self.assertEqual(status, 405)
        self.assertEqual(headers.get("Allow"), "POST")

    def test_missing_token_returns_401(self) -> None:
        status, _, _ = self._rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"}, token=None)
        self.assertEqual(status, 401)

    def test_wrong_token_returns_401(self) -> None:
        status, _, _ = self._rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token="wrong"
        )
        self.assertEqual(status, 401)

    def test_cross_origin_returns_403(self) -> None:
        status, _, _ = self._rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "ping"}, origin="http://evil.example"
        )
        self.assertEqual(status, 403)

    def test_loopback_origin_allowed(self) -> None:
        status, _, _ = self._rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            origin="http://localhost:1234",
        )
        self.assertEqual(status, 200)

    def test_wrong_content_type_returns_415(self) -> None:
        status, _, _ = self._rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "ping"}, ctype="text/plain"
        )
        self.assertEqual(status, 415)

    def test_unknown_path_returns_404(self) -> None:
        status, _, _ = self._request(
            "POST", path="/other",
            body="{}", headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        )
        self.assertEqual(status, 404)

    def test_legacy_rpc_path_still_works(self) -> None:
        status, _, body = self._rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "ping"}, path="/rpc"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["result"], {})

    def test_security_headers_present(self) -> None:
        _, headers, _ = self._rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("Cache-Control"), "no-store")

    # --- protocol over the wire ---------------------------------------------

    def test_requests_blocked_before_initialize(self) -> None:
        status, _, body = self._rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(status, 200)
        self.assertEqual(body["error"]["code"], -32002)

    def test_full_flow_open_and_read(self) -> None:
        sid = self._handshake()
        _, _, listed = self._rpc(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, session=sid
        )
        self.assertEqual(len(listed["result"]["tools"]), 6)

        _, _, opened = self._rpc(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "open_workspace", "arguments": {"path": str(self.root)}}},
            session=sid,
        )
        sc = opened["result"]["structuredContent"]
        self.assertNotIn("root", sc)  # no absolute path leak
        ws = sc["workspace_id"]

        _, _, read = self._rpc(
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "read", "arguments": {"workspace_id": ws, "path": "hello.txt"}}},
            session=sid,
        )
        self.assertFalse(read["result"]["isError"])
        self.assertEqual(read["result"]["structuredContent"]["content"], "hello e2e")

    def test_notification_returns_202_empty(self) -> None:
        sid = self._handshake()
        status, _, _ = self._request(
            "POST",
            body=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Content-Type": "application/json",
                "Mcp-Session-Id": sid,
            },
        )
        self.assertEqual(status, 202)

    def test_path_escape_over_wire_is_iserror(self) -> None:
        sid = self._handshake()
        _, _, opened = self._rpc(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "open_workspace", "arguments": {"path": str(self.root)}}},
            session=sid,
        )
        ws = opened["result"]["structuredContent"]["workspace_id"]
        _, _, read = self._rpc(
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "read", "arguments": {"workspace_id": ws, "path": "../../etc/hosts"}}},
            session=sid,
        )
        self.assertNotIn("error", read)
        self.assertTrue(read["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
