"""MCP-specific half of the input-validation parity tests (see
test_input_validation.py). Split into its own module rather than sharing a
module-level `pytest.importorskip("mcp")`: that skip is per-module, so
putting it alongside the CLI/API tests in one file silently skipped all of
them -- not just the MCP ones -- whenever the optional `mcp` extra wasn't
installed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp", reason="mcp is an optional extra; install with opencontextually[mcp]")

FIXTURE_ROOT = Path(__file__).parent.parent / "examples" / "auth_bug"
TASK = "fix the authentication bug"


def _call_tool(arguments: dict):
    from opencontextually import mcp as ocmcp

    return asyncio.run(ocmcp.server.call_tool("get_context", arguments))


def test_mcp_rejects_nonexistent_root():
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="does not exist or is not a directory"):
        _call_tool({"task": TASK, "root": "/no/such/directory/opencontextually-test"})


def test_mcp_rejects_empty_task():
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="task must be a non-empty string"):
        _call_tool({"task": "", "root": str(FIXTURE_ROOT)})
