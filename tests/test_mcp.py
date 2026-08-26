"""Tests for the optional MCP server (opencontextually.mcp), step 12.

Covers: the tool registers under the name "get_context" and only that
name (one tool, not a grab-bag); calling it against the examples/auth_bug
fixture returns the same structured data as `get_context(...).to_dict()`
-- the one serialization path in the codebase, not a second formatting
path; and that core `import opencontextually` keeps working when the
optional `mcp` extra is not installed, while `import opencontextually.mcp`
fails with a clear, actionable message naming the extra.

These tests require `mcp` to be installed in the venv (it is an optional
extra, not a core dependency -- see pyproject.toml's
`[project.optional-dependencies]`). They are skipped if it is not
present, so `pytest` stays green on a core-only install too.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp", reason="mcp is an optional extra; install with opencontextually[mcp]")

from opencontextually import get_context  # noqa: E402

FIXTURE_ROOT = Path(__file__).parent.parent / "examples" / "auth_bug"
TASK = "fix the authentication bug"


def _run(coro):
    return asyncio.run(coro)


def test_exactly_one_tool_named_get_context():
    from opencontextually import mcp as ocmcp

    tools = _run(ocmcp.server.list_tools())
    assert [t.name for t in tools] == ["get_context"]


def test_get_context_tool_returns_same_data_as_to_dict():
    from opencontextually import mcp as ocmcp

    _content, structured = _run(
        ocmcp.server.call_tool("get_context", {"task": TASK, "root": str(FIXTURE_ROOT)})
    )

    expected = get_context(TASK, root=FIXTURE_ROOT).to_dict()
    assert structured == expected


def test_get_context_tool_defaults_root_to_cwd():
    from opencontextually import mcp as ocmcp

    tools = _run(ocmcp.server.list_tools())
    schema = tools[0].inputSchema
    assert schema["properties"]["task"]["type"] == "string"
    assert "root" in schema["properties"]
    # `task` is required; `root` has a default and so is not.
    assert schema.get("required") == ["task"]


def test_core_import_works_without_mcp_and_mcp_module_fails_clearly():
    # Simulate the `mcp` extra not being installed by shadowing it in
    # sys.modules with None, which makes any `import mcp` (or submodule
    # thereof) raise ImportError immediately -- without actually
    # uninstalling anything from this venv. Run in a subprocess so it
    # cannot disturb sys.modules for the rest of the test session.
    script = textwrap.dedent(
        """
        import sys
        sys.modules["mcp"] = None

        import opencontextually
        assert opencontextually.get_context is not None

        try:
            import opencontextually.mcp
        except ImportError as exc:
            assert "opencontextually[mcp]" in str(exc)
        else:
            raise AssertionError("expected ImportError when mcp is not installed")

        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout
