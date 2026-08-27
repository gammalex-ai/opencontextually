# OpenContextually

Give your AI agent the right context before it acts.

## Install

```
pip install -e .
```

OpenContextually is not yet published on PyPI.

## Try it

```
opencontextually "fix the authentication bug"
```

Run from `examples/auth_bug/` (a small fixture with an auth module, a
config file, docs, and a test file), this is the real output — trimmed
with `...` where noted, nothing added:

```
Task: fix the authentication bug

Included (6):
  1. src/auth/middleware.py [source] score=10.16
     reason: defines AuthenticationError
     lines 19-20:
       class AuthenticationError(Exception):
           """Raised when a request cannot be authenticated."""
  2. tests/test_auth.py [test] score=6.16
     ...
  3. src/users/session.py [source] score=5.08
     reason: imported by middleware.py
     via: src/auth/middleware.py imports src/users/session.py
     ...
  ... (3 more: README.md, config/auth.yaml, docs/security.md)

Conflicts (1):
  1. session.timeout_minutes
     config/auth.yaml:3 declares 60 minutes, but docs/security.md:6 says 30 minutes

Missing (2):
  1. No test references session timeout minutes
     referenced in config/auth.yaml:3
  2. No test references session expired
     referenced in src/users/session.py:67

Excluded: 20 unrelated files
  below_threshold=20, binary=0, duplicate=0, ignored=0, over_budget=0, over_cap=0, oversize=0

Checks run: configuration_discrepancy, test_reference_gap
```

`session.py` is not named in the task and does not match it lexically — it
is included because `middleware.py` imports it, and the `via:` line shows
that edge. That is the result a ranked grep cannot produce.

## What it does

OpenContextually selects the files in a project relevant to a task,
follows Python's own import graph to pull in files reachable only through
imports (not just lexical matches), and flags two narrow classes of
lexical discrepancy: a config value that disagrees with what the docs say,
and a task-relevant setting with no test that references it. Every
inclusion gets a reason; excluded files are counted and bucketed by why.

It runs with **no LLM, no API key, no network, no database, no Docker, and
no config file** — it is a local, deterministic scan of the files already
on disk.

The hero API:

```python
from opencontextually import get_context
package = get_context("fix the authentication bug")
```

`package.render()` gives the text above; `package.to_dict()` gives the
same content as JSON (also available via `octx --json`).

## Limitations

- **Python-first.** Transitive import-graph expansion (the `session.py`
  example above) only works for Python, via the stdlib `ast` module.
  Files in other languages are only found by lexical selection — path,
  filename, and content matching — with no import-graph expansion.
- **The two checks are narrow, named lexical rules, not general analysis.**
  `configuration_discrepancy` finds the same named scalar setting declared
  with different values across a config file (`.yaml`/`.yml`/`.toml`/
  `.ini`/`.env`/`.json`, via a regex `key: value` scan, not a real
  YAML/TOML parser) and a doc. `test_reference_gap` reports task-relevant
  terms with no discovered test file referencing them — a reference gap,
  not a coverage claim. Both are intentionally high-precision and
  low-recall: when a match isn't clear, they say nothing rather than risk
  a false positive.
- **Ignore rules match git's own.** Discovery honors every source git
  does — nested `.gitignore` files (scoped to their own subtree, with
  deeper rules overriding shallower ones), repo-local
  `.git/info/exclude`, and the global `core.excludesFile` — plus an
  optional `.opencontextuallyignore`. Resolved without shelling out to
  git, so this also works in a directory that isn't a git repository at
  all.
- **Redaction is lexical, not semantic.** Excerpts mask values on
  key-shaped secrets (`key`, `token`, `secret`, `password`, `api_key`, and
  similar) and standalone high-entropy strings. A secret that doesn't look
  key-shaped or high-entropy can still appear in an excerpt.

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
