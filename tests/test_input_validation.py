"""Invalid `root`/`task` input, checked for parity across the CLI, the
Python API, and the MCP server.

Before this, the CLI alone checked `--root` before ever calling
`get_context()`. The Python API and MCP callers went straight through:
`Path(root).resolve()` accepts any string, and `discover()` on a
nonexistent or non-directory path simply finds nothing -- so a typo'd
root, or an empty/whitespace-only task, returned a normal-looking, empty
ContextPackage instead of surfacing the mistake. `get_context()` itself
now raises `ValueError`/`NotADirectoryError`, which the CLI translates
into its existing friendly message and exit code, and FastMCP translates
into a `ToolError` on its own.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from opencontextually import get_context

FIXTURE_ROOT = Path(__file__).parent.parent / "examples" / "auth_bug"
TASK = "fix the authentication bug"


def _run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "opencontextually.cli", *args],
        capture_output=True,
        text=True,
    )


# --- Python API ------------------------------------------------------------


def test_api_rejects_nonexistent_root():
    with pytest.raises(NotADirectoryError):
        get_context(TASK, root="/no/such/directory/opencontextually-test")


def test_api_rejects_a_root_that_is_a_regular_file(tmp_path):
    a_file = tmp_path / "not_a_directory.txt"
    a_file.write_text("x")

    with pytest.raises(NotADirectoryError):
        get_context(TASK, root=a_file)


def test_api_rejects_empty_task(tmp_path):
    with pytest.raises(ValueError):
        get_context("", root=tmp_path)


def test_api_rejects_whitespace_only_task(tmp_path):
    with pytest.raises(ValueError):
        get_context("   \n\t", root=tmp_path)


# --- CLI ---------------------------------------------------------------


def test_cli_rejects_nonexistent_root():
    result = _run_cli(TASK, "--root", "/no/such/directory/opencontextually-test")
    assert result.returncode == 2
    assert "does not exist or is not a directory" in result.stderr


def test_cli_rejects_a_root_that_is_a_regular_file(tmp_path):
    a_file = tmp_path / "not_a_directory.txt"
    a_file.write_text("x")

    result = _run_cli(TASK, "--root", str(a_file))
    assert result.returncode == 2
    assert "does not exist or is not a directory" in result.stderr


def test_cli_rejects_empty_task():
    result = _run_cli("", "--root", str(FIXTURE_ROOT))
    assert result.returncode == 2
    assert "task must be a non-empty string" in result.stderr


def test_cli_rejects_whitespace_only_task():
    result = _run_cli("   ", "--root", str(FIXTURE_ROOT))
    assert result.returncode == 2
    assert "task must be a non-empty string" in result.stderr


