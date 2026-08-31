# OpenContextually

[![Tests](https://github.com/gammalex-ai/opencontextually/actions/workflows/test.yml/badge.svg)](https://github.com/gammalex-ai/opencontextually/actions/workflows/test.yml)

**Describe a task. Get the files that actually matter, each with a reason.**

A fast, offline, deterministic second opinion on what to read before you
touch a task — including files that mention none of your words but are
reachable through the codebase's own Python imports.

No LLM, no API key, no network, no database, no config file. One
dependency.

## Install

```
pip install -e .
```

Not yet on PyPI, so install from a clone. Python 3.10+.

## Quick start

```
octx "fix the authentication bug"
```

Run from `examples/auth_bug/` — a small fixture with an auth module, a
config file, docs, and a test — this is the real, unedited output:

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

Excluded: 21 files
  21 files scanned, not relevant enough

Checks run: configuration_discrepancy, test_reference_gap
```

Three things happened there beyond ranking:

- **`session.py` was found through an import**, not through text. It
  matches none of the task's words; `middleware.py` imports it, and the
  `← via` marker shows that edge.
- **A config/doc disagreement was flagged** — 60 minutes vs 30.
- **Every file carries a reason**, and everything excluded is counted.

Write tasks the way you'd describe the bug — a behavior or a symptom.
Naming a specific symbol or behavior beats a directory-shaped noun.

## Using it

### CLI

| Command | What you get |
| --- | --- |
| `octx "task"` | Ranked files, each with a reason |
| `octx "task" -v` | Adds the code excerpt that justified each file |
| `octx "task" --all` | Every included file, not just the top slice |
| `octx "task" --json` | Full machine representation, for handing to an agent |
| `octx "task" --root PATH` | Search somewhere other than the current directory |

`opencontextually` is a longer alias for `octx`. Flags compose (`-v --all`).
`--json` is unaffected by `-v`/`--all` — it is always full fidelity.

### Python

```python
from opencontextually import get_context

package = get_context("fix the authentication bug")

print(package.render())     # the text above
package.to_dict()           # the same content as JSON
```

### MCP

Agents that speak [MCP](https://modelcontextprotocol.io) can call it
directly. Requires the optional extra:

```
pip install -e ".[mcp]"
```

Point your MCP client at the `opencontextually-mcp` command:

```json
{
  "mcpServers": {
    "opencontextually": {
      "command": "opencontextually-mcp"
    }
  }
}
```

It exposes exactly one tool — `get_context(task, root=".")` — returning the
same shape as `octx --json`.

## What it reads

Every file under `--root`, minus what git already ignores. Discovery
honors nested `.gitignore` files, `.git/info/exclude`, and the global
`core.excludesFile`, plus an optional `.opencontextuallyignore` — resolved
without shelling out to git, so it also works in a directory that isn't a
repository at all.

Excerpts pass through a redactor that masks secret-shaped keys and
high-entropy strings before they leave a file. This is best-effort pattern
matching, not a secrets scanner — see [SECURITY.md](SECURITY.md).

Scale: a 42,000-file repository completes in about two seconds; a typical
project is well under one. Repeat runs are byte-identical.

## Why not just grep

Because some of the files you need don't contain your words.

Testing against [yenklabs/Dali](https://github.com/yenklabs/Dali), the task
*"citation verification returns wrong confidence score"* surfaced
`dali/scoring/existence.py` — a file with zero occurrences of "citation" or
"confidence". It was reached because `verification.py`, which does match,
contains:

```python
from dali.scoring.existence import score_existence
```

and `score_existence` computes the score being asked about. No text search
can get there; the import is the only path, and `provenance` records that
edge so the reason isn't "trust me".

Honestly, that's the minority of the value. Most runs are ranking and
compression of files grep *could* have found — one took 147 unranked grep
hits down to 12 ranked files — just sorted, explained, and bounded.

## What it won't do

- **Follow imports outside Python.** Import expansion uses the stdlib
  `ast` module. Other languages get lexical matching only.
- **Understand your code.** Ranking is lexical scoring plus import
  expansion. It is weakest when your task's words are also the repo's
  naming convention — asking about "the context agent" where many files
  are named `*context*` — because filename matches then dominate. A
  rarity weight damps this but does not eliminate it.
- **Find problems for you.** The two checks are narrow, named rules that
  expect to stay quiet. `configuration_discrepancy` compares a scalar
  config value against a doc; `test_reference_gap` reports terms no test
  references. Across eleven real repositories one produced a false
  positive — since fixed — which is the honest measure of how much
  "high precision" has actually been tested. Silence is the normal
  outcome; a footer always names which checks ran.
- **Guarantee secrets stay out of excerpts.** See "What it reads" above.

## License

MIT
