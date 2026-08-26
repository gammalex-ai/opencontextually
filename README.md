# OpenContextually

Give your AI agent the right context before it acts.

Status: early development.

## Install

```
pip install -e .
```

OpenContextually is not yet published on PyPI.

## MCP server (optional)

An agent that speaks [MCP](https://modelcontextprotocol.io) can call
OpenContextually directly instead of going through the CLI. This requires
the optional `mcp` extra, which is **not** part of the core install:

```
pip install -e ".[mcp]"
```

Run the stdio server with:

```
opencontextually-mcp
```

It exposes exactly one tool, `get_context(task, root=".")`, returning the
same JSON shape as `octx --json` (`ContextPackage.to_dict()`). Point your
MCP client's config at the `opencontextually-mcp` command, e.g.:

```json
{
  "mcpServers": {
    "opencontextually": {
      "command": "opencontextually-mcp"
    }
  }
}
```

## License

MIT
