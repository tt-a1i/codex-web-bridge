#!/usr/bin/env python3
"""Tests for the MCP protocol layer.

Run with: python3 -m unittest connector.tests.test_protocol
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from connector.config import ConnectorConfig
from connector.protocol import (
    INVALID_PARAMS,
    LATEST_PROTOCOL_VERSION,
    METHOD_NOT_FOUND,
    NOT_INITIALIZED,
    ProtocolHandler,
    SUPPORTED_PROTOCOL_VERSIONS,
)
from connector.tools import READONLY_TOOLS, Tool, ToolContext, ToolRegistry
from connector.workspace import WorkspaceRegistry


def _handler(root: Path, trust: str = "readonly") -> ProtocolHandler:
    config = ConnectorConfig(allowed_roots=(root,), trust_level=trust)
    ctx = ToolContext(config=config, registry=WorkspaceRegistry(config))
    return ProtocolHandler(ToolRegistry(ctx))


class _Session:
    """Helper: a handler plus an initialized session id."""

    def __init__(self, root: Path, trust: str = "readonly") -> None:
        self.h = _handler(root, trust)
        self.sid = self.h.start_session(mark_initialized=True)

    def request(self, method: str, params: dict | None = None, _id: int = 1) -> dict:
        msg = {"jsonrpc": "2.0", "id": _id, "method": method}
        if params is not None:
            msg["params"] = params
        resp = self.h.handle(msg, session_id=self.sid)
        assert resp is not None
        return resp


class HandshakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.h = _handler(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_initialize_echoes_supported_version(self) -> None:
        resp = self.h.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}}
        )
        assert resp is not None
        self.assertEqual(resp["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", resp["result"]["capabilities"])
        self.assertEqual(resp["result"]["serverInfo"]["name"], "codex-web-bridge-connector")

    def test_initialize_negotiates_down_on_unknown_version(self) -> None:
        resp = self.h.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "1.0.0"}}
        )
        assert resp is not None
        self.assertEqual(resp["result"]["protocolVersion"], LATEST_PROTOCOL_VERSION)

    def test_initialized_notification_returns_none(self) -> None:
        sid = self.h.start_session()
        resp = self.h.handle(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, session_id=sid
        )
        self.assertIsNone(resp)

    def test_ping_allowed_without_init(self) -> None:
        resp = self.h.handle({"jsonrpc": "2.0", "id": 9, "method": "ping"})
        assert resp is not None
        self.assertEqual(resp["result"], {})

    def test_requests_blocked_before_initialize(self) -> None:
        # No session / not initialized -> tools/list must be rejected.
        resp = self.h.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert resp is not None
        self.assertEqual(resp["error"]["code"], NOT_INITIALIZED)

    def test_initialized_notification_unlocks_requests(self) -> None:
        sid = self.h.start_session()
        self.h.handle(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, session_id=sid
        )
        resp = self.h.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, session_id=sid)
        assert resp is not None
        self.assertIn("result", resp)

    def test_unknown_method_is_protocol_error(self) -> None:
        sid = self.h.start_session(mark_initialized=True)
        resp = self.h.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "does/not/exist"}, session_id=sid
        )
        assert resp is not None
        self.assertEqual(resp["error"]["code"], METHOD_NOT_FOUND)

    def test_supported_versions_nonempty(self) -> None:
        self.assertTrue(SUPPORTED_PROTOCOL_VERSIONS)


class ToolsListTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.s = _Session(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_tools_list_has_input_schema(self) -> None:
        resp = self.s.request("tools/list")
        tools = resp["result"]["tools"]
        self.assertTrue(tools)
        for t in tools:
            self.assertIn("name", t)
            self.assertIn("description", t)
            self.assertEqual(t["inputSchema"]["type"], "object")
        names = {t["name"] for t in tools}
        self.assertIn("open_workspace", names)

    def test_readonly_tools_labelled(self) -> None:
        resp = self.s.request("tools/list")
        for t in resp["result"]["tools"]:
            self.assertTrue(t["description"].startswith("[readonly]"))


class ToolsCallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "a.txt").write_text("hello", encoding="utf-8")
        self.s = _Session(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _open(self) -> str:
        resp = self.s.request(
            "tools/call",
            {"name": "open_workspace", "arguments": {"path": str(self.root)}},
        )
        return resp["result"]["structuredContent"]["workspace_id"]

    def test_open_workspace_does_not_leak_absolute_root(self) -> None:
        resp = self.s.request(
            "tools/call",
            {"name": "open_workspace", "arguments": {"path": str(self.root)}},
        )
        sc = resp["result"]["structuredContent"]
        self.assertIn("workspace_id", sc)
        self.assertNotIn("root", sc)  # absolute path must not be returned
        self.assertEqual(sc["name"], self.root.name)

    def test_call_returns_content_and_structured(self) -> None:
        ws = self._open()
        resp = self.s.request(
            "tools/call",
            {"name": "read", "arguments": {"workspace_id": ws, "path": "a.txt"}},
        )
        res = resp["result"]
        self.assertFalse(res["isError"])
        self.assertEqual(res["structuredContent"]["content"], "hello")
        parsed = json.loads(res["content"][0]["text"])
        self.assertEqual(parsed["content"], "hello")

    def test_path_escape_is_tool_error_not_protocol_error(self) -> None:
        ws = self._open()
        resp = self.s.request(
            "tools/call",
            {"name": "read", "arguments": {"workspace_id": ws, "path": "../x"}},
        )
        self.assertNotIn("error", resp)
        self.assertTrue(resp["result"]["isError"])

    def test_unknown_tool_is_protocol_error(self) -> None:
        resp = self.s.request("tools/call", {"name": "nope", "arguments": {}})
        self.assertEqual(resp["error"]["code"], INVALID_PARAMS)

    def test_handler_oserror_becomes_iserror_without_leak(self) -> None:
        # A handler raising a bare OSError must not crash; it returns isError
        # with a generic message (no OS detail / path forwarded).
        config = ConnectorConfig(allowed_roots=(self.root,), trust_level="readonly")
        ctx = ToolContext(config=config, registry=WorkspaceRegistry(config))

        def _boom(_c, _a):
            raise PermissionError("/secret/abs/path denied")

        boom = Tool("boom", "readonly", _boom, "boom", {"type": "object", "properties": {}})
        h = ProtocolHandler(ToolRegistry(ctx, tools=[boom]))
        sid = h.start_session(mark_initialized=True)
        resp = h.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "boom", "arguments": {}}},
            session_id=sid,
        )
        assert resp is not None
        self.assertNotIn("error", resp)
        self.assertTrue(resp["result"]["isError"])
        text = resp["result"]["content"][0]["text"]
        self.assertEqual(json.loads(text)["error"], "io error")
        self.assertNotIn("/secret/abs/path", text)

    def test_trust_denied_is_iserror_result(self) -> None:
        # A readonly server asked for an execute-level tool returns isError, not
        # a protocol error. We simulate by registering a fake execute tool.
        config = ConnectorConfig(allowed_roots=(self.root,), trust_level="readonly")
        ctx = ToolContext(config=config, registry=WorkspaceRegistry(config))
        fake = Tool(
            "fake_exec", "execute", lambda c, a: {"ok": True}, "fake",
            {"type": "object", "properties": {}},
        )
        h = ProtocolHandler(ToolRegistry(ctx, tools=[*READONLY_TOOLS, fake]))
        sid = h.start_session(mark_initialized=True)
        resp = h.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "fake_exec", "arguments": {}}},
            session_id=sid,
        )
        assert resp is not None
        self.assertNotIn("error", resp)
        self.assertTrue(resp["result"]["isError"])
        # And it must not be listed at readonly level.
        listing = h.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, session_id=sid
        )
        assert listing is not None
        names = {t["name"] for t in listing["result"]["tools"]}
        self.assertNotIn("fake_exec", names)


if __name__ == "__main__":
    unittest.main()
