"""Optional MCP (Model Context Protocol) stdio server for OpenContextually.

This module is the *only* place `mcp` (the optional extra) is imported.
`opencontextually` (the core package) never imports this module, so
`import opencontextually` keeps working with the extra not installed --
only `import opencontextually.mcp` requires it, and does so with a clear,
actionable error rather than a bare ImportError traceback.

Exposes exactly **one** tool, `get_context`, because MCP is how existing
coding agents *consume* OpenContextually -- it is not a second product
surface. No `list_files`, `search`, `explain`, or other convenience tools:
an agent that wants more than get_context() already has its own file
tools. The tool's output is `ContextPackage.to_dict()` -- the same
serializer `octx --json` uses -- so there is exactly one formatting path
in the whole codebase (see context.py's module docstring); this module
does not reformat or add a second one.
"""

from __future__ import annotations

from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:
    raise ImportError(
        "opencontextually.mcp requires the optional 'mcp' extra, which is "
        "not installed. Install it with:\n\n"
        "    pip install 'opencontextually[mcp]'\n"
    ) from exc

from . import get_context as _get_context

server = FastMCP("opencontextually")


@server.tool(name="get_context", structured_output=True)
def get_context(task: str, root: str = ".") -> dict[str, Any]:
    """Select the local files relevant to `task`, bounded to `root`, and
    return the resulting ContextPackage as a dict.

    This is the hero API (`opencontextually.get_context`) exposed as an
    MCP tool. The return value is exactly `package.to_dict()` -- identical
    in shape to `octx --json` -- so a caller gets the same structured
    data (included files with reasons/provenance/excerpts, conflicts,
    missing, exclusion counts, trace) regardless of whether it reaches
    OpenContextually via the CLI, the Python API, or MCP.
    """
    package = _get_context(task, root=root)
    return package.to_dict()


def main() -> None:
    """Entry point for the `opencontextually-mcp` console script: run the
    stdio MCP server. This is the only supported transport for v0.1 --
    OpenContextually runs locally, alongside the agent invoking it, not as
    a network service.
    """
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
