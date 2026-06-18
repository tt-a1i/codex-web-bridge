"""Local readonly MCP connector for codex-web-bridge.

This package is deliberately separate from the skill-only Bridge Mode runtime.
Bridge Mode stays low trust: Codex sends a scrubbed packet to a web model and
brings an answer back. Connector Mode is a different, higher-trust path where an
MCP host (commonly ChatGPT Pro) connects to this local server and uses tools
against approved local workspaces.

The first public connector ships ``readonly`` only. Write, edit, shell, and
worktree tools are intentionally absent until they have their own explicit trust
model and tests.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
