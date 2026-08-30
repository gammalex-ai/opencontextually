# OpenContextually

[![Tests](https://github.com/gammalex-ai/opencontextually/actions/workflows/test.yml/badge.svg)](https://github.com/gammalex-ai/opencontextually/actions/workflows/test.yml)

Turns a task description and a codebase into a short, ranked list of the
files actually relevant to it — including files that mention none of the
task's words but are reachable through the codebase's own Python imports.
Tested across 11 real repos, the consistent result is compression: one run
took 147 unranked grep hits down to 12 ranked files, each with a reason.

It is not a general "context problem" solver. It's a fast, deterministic,
offline second opinion on what to read before you touch a task — the
files a plain grep would rank badly, miss the reason for, or miss
entirely because the match is behind an import rather than in the text.

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
config file, docs, and a test file), this is the real, unedited output of
`octx "fix the authentication bug"`:

```
fix the authentication bug
6 relevant · 21 excluded

  src/auth/middleware.py  defines AuthenticationError
  src/users/session.py    imported by middleware.py  ← via middleware.py
  tests/test_auth.py      imports middleware.py  ← via middleware.py
  README.md               defines authentication requirements
  config/auth.yaml        configuration referenced by authentication code
  docs/security.md        defines authentication requirements

  ⚠ session.timeout_minutes: config/auth.yaml:3 declares 60 minutes, but docs/security.md:6 says 30 minutes
  ○ No test references session timeout minutes (config/auth.yaml:3)
  ○ No test references session expired (src/users/session.py:67)

  -v for code excerpts

Excluded: 21 unrelated files (below_threshold=21, binary=0, duplicate=0, ignored=0, over_budget=0, over_cap=0, oversize=0)

Checks run: configuration_discrepancy, test_reference_gap
```

`session.py` is not named in the task and does not match it lexically —
it is included because `middleware.py` imports it, and the `← via`
marker shows that edge. That is the result a ranked grep cannot produce;
see "Import-following" below for why it matters more than it looks.

Run with `-v` to see why: it adds the bounded code excerpt that justified
each inclusion. This is the same fixture, with `-v` added, showing just
the two import-related lines:

```
  src/auth/middleware.py  defines AuthenticationError
     lines 19-20:
       class AuthenticationError(Exception):
           """Raised when a request cannot be authenticated."""
  src/users/session.py    imported by middleware.py  ← via middleware.py
     lines 30-69:
       class SessionStore:
           """In-memory store of active sessions.
       ...
```

`--all` lists every included file instead of the top slice (both compose:
`-v --all`). `octx --json` (equivalently `package.to_dict()`) is
unaffected by either flag — it's always the full-fidelity machine
representation, meant for handing to an agent rather than reading in a
terminal. See "MCP server" below for handing it to an agent directly.

## What it does

OpenContextually selects the files in a project relevant to a task,
follows Python's own import graph to pull in files reachable only through
imports (not just lexical matches), and flags two narrow classes of
lexical discrepancy: a config value that disagrees with what the docs
say, and a task-relevant setting with no test that references it. Every
inclusion gets a reason; excluded files are counted and bucketed by why.

It runs with **no LLM, no API key, no network, no database, no Docker, and
no config file** — it is a local, deterministic scan of the files already
on disk. On the repos tested so far this has meant sub-second on a
typical project and around 6.5 seconds on a ~7,000-file monorepo.

The hero API:

```python
from opencontextually import get_context
package = get_context("fix the authentication bug")
```

`package.render()` gives the text above; `package.to_dict()` gives the
same content as JSON (also available via `octx --json`).

## Import-following

The `session.py` example above is the small version of the thing this
tool is actually for: a file that shares none of the task's vocabulary,
found anyway because a file that *does* match imports it.

The clearest real case found while testing against real repos — and one
you can check yourself, since the repo is public
([yenklabs/Dali](https://github.com/yenklabs/Dali)): a task described as
**"citation verification returns wrong confidence score"** surfaced
`dali/scoring/existence.py` — a file containing zero occurrences of
"citation" or "confidence." It was included because `verification.py`
(which does match the task lexically) has:

```python
from dali.scoring.existence import score_existence
```

and `score_existence` is the function that actually computes the
confidence score being asked about. A lexical search — grep, ripgrep, a
full-text index — has no way to surface `existence.py` here: none of its
own words match. Following the import that reaches it is the only way to
get there, and `provenance` records the edge (`verification.py` →
`dali.scoring.existence`) so the reason isn't just "trust me."

This is also, honestly, a modest share of the value in practice, not the
whole story: across four tasks tested this way, only about four files
total were genuinely unreachable by text search. Real, worth having, but
don't expect it to dominate every run — most of what OpenContextually
does is rank and compress files that text search *could* have found, just
not sorted or explained.

## Limitations

- **Python-first.** Transitive import-graph expansion (the `session.py`
  and `existence.py` examples above) only works for Python, via the
  stdlib `ast` module. Files in other languages are only found by
  lexical selection — path, filename, and content matching — with no
  import-graph expansion.
- **The two checks are narrow, named lexical rules, not general analysis,
  and expect to stay quiet.** `configuration_discrepancy` finds the same
  named scalar setting declared with different values across a config
  file (`.yaml`/`.yml`/`.toml`/`.ini`/`.env`/`.json`, via a regex
  `key: value` scan, not a real YAML/TOML parser) and a doc.
  `test_reference_gap` reports task-relevant terms with no discovered
  test file referencing them — a reference gap, not a coverage claim.
  Both are intentionally high-precision and low-recall: across nine real
  repos tested, `configuration_discrepancy` fired on zero of them, and
  `test_reference_gap` fires rarely. Silence is the expected, common
  outcome, not a sign the checks aren't running — a footer always names
  which checks ran.
- **Ranking is not always right.** It's lexical scoring plus import
  expansion, not code understanding; on some tasks a relevant file will
  rank lower than it should, or an unrelated file will rank higher than
  ideal.
- **Ignore rules match git's own.** Discovery honors every source git
  does — nested `.gitignore` files (scoped to their own subtree, with
  deeper rules overriding shallower ones), repo-local
  `.git/info/exclude`, and the global `core.excludesFile` — plus an
  optional `.opencontextuallyignore`. Resolved without shelling out to
  git, so this also works in a directory that isn't a git repository at
  all.
- **Redaction is lexical, not semantic — see `SECURITY.md`.** Excerpts
  mask values on key-shaped secrets (`key`, `token`, `secret`,
  `password`, `api_key`, and similar) and standalone high-entropy
  strings before they leave a file. This is best-effort, not a
  guarantee: a secret that doesn't look key-shaped or high-entropy can
  still appear in an excerpt.

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
