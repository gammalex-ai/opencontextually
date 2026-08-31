# Contributing

Thanks for being here. The fastest ways in:

| I want to… | Go here |
| --- | --- |
| Report that an agent read the wrong context | [Context failure](https://github.com/gammalex-ai/opencontextually/issues/new?template=context_failure.yml) — no code needed |
| Report a bug in OpenContextually itself | [Bug report](https://github.com/gammalex-ai/opencontextually/issues/new?template=bug_report.yml) |
| Add a benchmark case from a repo you know | [ContextBench case](https://github.com/gammalex-ai/opencontextually/issues/new?template=contextbench_case.yml) |
| Tell us about something you built | [Integration](https://github.com/gammalex-ai/opencontextually/issues/new?template=integration.yml) |
| Find something concrete to work on | [GOOD_FIRST_CONTEXT.md](GOOD_FIRST_CONTEXT.md) |
| Ask a question first | [Discord](DISCORD_INVITE_URL) · [Discussions](https://github.com/gammalex-ai/opencontextually/discussions) |

[COMMUNITY.md](COMMUNITY.md) describes each contribution path in full. This
file is the mechanics: setup, tests, and what is in scope.

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

## Changing selection

Any change to ranking, exclusion or relationship-following needs a
measurement, not an argument. Run ContextBench before and after and put
both numbers in the PR — see [benchmarks/README.md](benchmarks/README.md).
Two things make a selection PR easy to accept:

- the corpus numbers moved the way you say they did, and you show the
  cases that moved
- a regression test that fails without your change

Two things make one hard: "it looks better on my repository", and a change
tuned against the same cases used to justify it. The corpus separates
tuned from held-out repositories precisely because that distinction is easy
to lose.

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

Integrations are the exception to most of this: they live in *your*
repository, not this one, and are not bound by the scope rules above. Build
against `gctx --json` or the MCP server, then
[tell us](https://github.com/gammalex-ai/opencontextually/issues/new?template=integration.yml)
so it can be listed in the README's Ecosystem section.

## Code of conduct

Participation is covered by the [Code of Conduct](CODE_OF_CONDUCT.md).
Security reports go through [SECURITY.md](SECURITY.md), not public issues.
