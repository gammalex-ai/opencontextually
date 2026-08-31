# Contributing

## Dev setup

```
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running the tests

```
pytest
```

232 tests, sub-second locally. To also exercise the MCP server module,
install the `mcp` extra first:

```
pip install -e ".[dev,mcp]"
```

`pytest` always tests *this* checkout. `pip install -e .` writes a path
file naming the clone it was installed from, and that path otherwise wins
over the repository you are sitting in — so working in a second clone or a
git worktree, `pytest` could pass while testing entirely different code. Two
things prevent that, and neither needs anything from your shell:

- `pythonpath = ["src"]` in `pyproject.toml` puts this checkout's source
  first on `sys.path` for every run.
- `tests/conftest.py` verifies the imported package really is this one, and
  stops the run with an explanation if it is not.

If you ever see that error, the escape hatch it prints is
`PYTHONPATH="$PWD/src" pytest`.

## CI

Every push and pull request runs `pytest` on Python 3.10, 3.11, 3.12, and
3.13 via GitHub Actions (`.github/workflows/test.yml`), matching the
`requires-python = ">=3.10"` claim in `pyproject.toml`. A PR is expected
to pass on all four before merge.

## Scope discipline

OpenContextually is deliberately small: no LLM, no API key, no network, no
database, no Docker, no config file, one hero API
(`get_context(task, root=".")`). That is a feature, not a gap to fill in.

Before proposing or building anything, ask the guardrail question:

> **Does this make `get_context(task)` materially better for the first
> developer who runs it?**

If the answer isn't a clear yes, it's out of scope for this project — not
because the idea is bad, but because it doesn't belong here.

**Out of scope, and will be declined:** agents, models, routers, memory,
RAG, vector or graph databases, dashboards, hosted services, auth,
accounts, RBAC, policy DSLs, third-party connectors, plugin marketplaces,
multi-agent frameworks, and automatic learning. Also out of scope: a
speculative extension-point framework for any of the above, even without
the feature itself.

Things that *are* in scope: making SELECT, BOUND, CHECK, or EXPLAIN more
accurate or more honest about their own limits; new narrow, named,
high-precision checks with documented scope (in the spirit of
`configuration_discrepancy` and `test_reference_gap`); bug fixes; and
tests.

If you're unsure whether something fits, open an issue describing the
problem it solves for the first developer — not the mechanism — before
sending a PR.
