#!/usr/bin/env python3
"""Tests for connector safety: path containment and tool permission levels.

Run with: python3 -m unittest connector.tests.test_connector
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from connector.config import ConfigError, ConnectorConfig
from connector.tools import ToolContext, ToolError, ToolRegistry
from connector.workspace import WorkspaceError, WorkspaceRegistry


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_rejects_broad_root(self) -> None:
        with self.assertRaises(ConfigError):
            ConnectorConfig(allowed_roots=(Path("/"),))

    def test_rejects_home_root(self) -> None:
        with self.assertRaises(ConfigError):
            ConnectorConfig(allowed_roots=(Path.home(),))

    def test_requires_at_least_one_root(self) -> None:
        with self.assertRaises(ConfigError):
            ConnectorConfig(allowed_roots=())

    def test_rejects_unknown_trust_level(self) -> None:
        with self.assertRaises(ConfigError):
            ConnectorConfig(allowed_roots=(self.root,), trust_level="root")

    def test_non_loopback_requires_owner_token(self) -> None:
        with self.assertRaises(ConfigError):
            ConnectorConfig(allowed_roots=(self.root,), host="0.0.0.0")
        # With a token it is allowed.
        cfg = ConnectorConfig(
            allowed_roots=(self.root,), host="0.0.0.0", owner_token="secret"
        )
        self.assertFalse(cfg.is_loopback())

    def test_allows_permission_levels(self) -> None:
        ro = ConnectorConfig(allowed_roots=(self.root,), trust_level="readonly")
        self.assertTrue(ro.allows("readonly"))
        self.assertFalse(ro.allows("review"))
        self.assertFalse(ro.allows("execute"))

        ex = ConnectorConfig(allowed_roots=(self.root,), trust_level="execute")
        self.assertTrue(ex.allows("readonly"))
        self.assertTrue(ex.allows("review"))
        self.assertTrue(ex.allows("execute"))


class WorkspaceContainmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "inside.txt").write_text("ok", encoding="utf-8")
        (self.root / "sub").mkdir()
        self.config = ConnectorConfig(allowed_roots=(self.root,))
        self.registry = WorkspaceRegistry(self.config)
        self.ws = self.registry.open_workspace(str(self.root))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_resolves_inside(self) -> None:
        self.assertEqual(self.ws.resolve("inside.txt"), self.root / "inside.txt")

    def test_rejects_absolute_path(self) -> None:
        with self.assertRaises(WorkspaceError):
            self.ws.resolve("/etc/passwd")

    def test_rejects_parent_traversal(self) -> None:
        with self.assertRaises(WorkspaceError):
            self.ws.resolve("../escape.txt")

    def test_rejects_symlink_escape(self) -> None:
        outside = Path(tempfile.mkdtemp())
        try:
            (outside / "secret.txt").write_text("nope", encoding="utf-8")
            link = self.root / "link"
            os.symlink(outside / "secret.txt", link)
            with self.assertRaises(WorkspaceError):
                self.ws.resolve("link")
        finally:
            import shutil

            shutil.rmtree(outside)

    def test_open_workspace_outside_root_rejected(self) -> None:
        outside = Path(tempfile.mkdtemp())
        try:
            with self.assertRaises(WorkspaceError):
                self.registry.open_workspace(str(outside))
        finally:
            import shutil

            shutil.rmtree(outside)


class ToolPermissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "a.txt").write_text("hello world", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _registry(self, trust: str) -> ToolRegistry:
        config = ConnectorConfig(allowed_roots=(self.root,), trust_level=trust)
        ctx = ToolContext(config=config, registry=WorkspaceRegistry(config))
        return ToolRegistry(ctx)

    def test_readonly_tools_available(self) -> None:
        names = {t["name"] for t in self._registry("readonly").describe()}
        self.assertEqual(
            names,
            {"open_workspace", "read", "list", "search", "git_status", "git_diff"},
        )

    def test_read_roundtrip(self) -> None:
        reg = self._registry("readonly")
        ws = reg.call("open_workspace", {"path": str(self.root)})
        result = reg.call("read", {"workspace_id": ws["workspace_id"], "path": "a.txt"})
        self.assertEqual(result["content"], "hello world")
        self.assertFalse(result["truncated"])

    def test_read_rejects_escape_via_tool(self) -> None:
        reg = self._registry("readonly")
        ws = reg.call("open_workspace", {"path": str(self.root)})
        with self.assertRaises(WorkspaceError):
            reg.call("read", {"workspace_id": ws["workspace_id"], "path": "../x"})

    def test_unknown_tool_rejected(self) -> None:
        with self.assertRaises(ToolError):
            self._registry("readonly").call("shell", {})

    def test_list_skips_ignored_dirs(self) -> None:
        (self.root / "node_modules").mkdir()
        reg = self._registry("readonly")
        ws = reg.call("open_workspace", {"path": str(self.root)})
        result = reg.call("list", {"workspace_id": ws["workspace_id"]})
        names = {e["name"] for e in result["entries"]}
        self.assertIn("a.txt", names)
        self.assertNotIn("node_modules", names)


if __name__ == "__main__":
    unittest.main()
