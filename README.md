<div align="center">

# OpenContextually

### The open context layer before the coding agent.

**Turns a task into a small, ranked, explainable context package from your repository.**

[![PyPI](https://img.shields.io/pypi/v/opencontextually?label=PyPI&color=blue)](https://pypi.org/project/opencontextually/)
[![Python](https://img.shields.io/pypi/pyversions/opencontextually)](https://pypi.org/project/opencontextually/)
[![Tests](https://github.com/gammalex-ai/opencontextually/actions/workflows/test.yml/badge.svg)](https://github.com/gammalex-ai/opencontextually/actions/workflows/test.yml)
[![License](https://img.shields.io/github/license/gammalex-ai/opencontextually)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-ready-8A2BE2)](#mcp)
[![Local](https://img.shields.io/badge/runs-100%25%20local-success)](#scope-determinism-and-safety)

**No model · No API key · No network · No database**

**Works with Claude Code · Cursor · Codex · MCP-compatible agents · custom coding agents**

[Quickstart](#quickstart) · [How it works](#how-it-works) · [MCP](#mcp) · [Contributing](#contributing)

[Discord](https://discord.gg/ZNqyQtz5cV) · [Discussions](https://github.com/gammalex-ai/opencontextually/discussions) · [ContextBench](benchmarks/README.md) · [Ecosystem](#ecosystem--community) · [Community](COMMUNITY.md)

</div>

---

## Overview
Agents perform better when they receive small, relevant, explainable task context instead of blindly consuming everything.

That’s why we built OpenContextually, an open-source context layer for coding agents. Give it a task, and it identifies, ranks, and explains the context that matters before the agent starts working.

Give it a task:

```bash
gctx "fix the authentication bug"
```

It scans your repository, follows the relationships a keyword search would
miss — imports, calls, tests, configuration, documentation — and returns a
small, ranked, explainable package of what to look at before acting. No
model, no API key, no network call, no database; the same repository and
task produce byte-identical output every time.

It is not another coding agent. It is the layer that helps the agent — or
you — figure out what it should know before it starts working.

## How it works

```text
your task
   │
   ▼
SELECT → FOLLOW → BOUND → CHECK → EXPLAIN
   │
   ▼
small, ranked, explainable context
```

| Step | What happens |
| --- | --- |
| **SELECT** | Score every file against the task — filename, symbols, content |
| **FOLLOW** | Walk the import graph to reach files that share no vocabulary with the task at all |
| **BOUND** | Cap what ships to a small, readable package — considering a file is free, delivering one costs the reader |
| **CHECK** | Run two deterministic checks for config/doc drift and untested symbols |
| **EXPLAIN** | Every file in the package carries a reason — a filename match, a symbol match, or "called by X" |

The selection itself is local and deterministic — no model decides what
matters.

## Quickstart

```bash
pip install opencontextually

gctx "fix the authentication bug"
```

**No model. No API key. No network. No database. No setup.** One dependency.
The same repository and task produce byte-identical output every time.

## What it looks like

```
gctx "dependency override not applied in nested routers"
```

Run from a clone of [fastapi/fastapi](https://github.com/fastapi/fastapi) at
[`49033471`](https://github.com/fastapi/fastapi/commit/49033471594e) — this
is the real, unedited output:

```
dependency override not applied in nested routers
18 relevant · 3121 excluded

  fastapi/routing.py                             defines _frontend_dependency_endpoint
  fastapi/applications.py                        references applied
  tests/test_frontend.py                         defines record_dependency
  tests/test_dependency_overrides.py             filename matches 'dependency'
  tests/test_dependency_wrapped.py               filename matches 'dependency'
  docs/hi/docs/advanced/testing-dependencies.md  defines dependency requirements
  docs/en/docs/advanced/testing-dependencies.md  defines dependency requirements
  docs/tr/docs/advanced/testing-dependencies.md  defines dependency requirements

  +10 more  ·  --all to list  ·  -v for code excerpts

Excluded: 3121 files
  ⚠ 205 relevant files dropped -- the result list was already full
  2715 files scanned, not relevant enough
  201 files not scanned (187 binary, 7 exact duplicate of another file, 7 too large to scan)

Checks run: configuration_discrepancy, test_reference_gap
```

Two files were reached through imports, not text — `--all` marks
`fastapi/openapi/utils.py` and `fastapi/exceptions.py` `← via routing.py`,
the file that actually implements dependency overrides. One doc page shows
up three times, correctly: `testing-dependencies.md` is genuinely
translated under `docs/en`, `docs/hi`, and `docs/tr`. Every file carries a
reason, and everything excluded is accounted for — including the 205 that
scored relevant but missed the result budget.

### Try it yourself

```bash
git clone https://github.com/fastapi/fastapi.git && cd fastapi
python -m venv .venv && source .venv/bin/activate   # fish: activate.fish
pip install opencontextually

gctx "dependency override not applied in nested routers"   # ~2s
```

Flask clones the same way — `gctx "session cookie is not set on redirect"`
— in about 0.2s. No model, no API key, no network call; your code never
leaves your machine. Add `-v` to see the excerpt behind each match.

Already have a project open? Skip the clone:

```bash
gctx "where does authentication actually happen?"
gctx "what will break if I change the User model?"
```

Give it something you're actually working on — you'll know in seconds
whether it's right, because it's your code. (More on what to do with a bad
result under [Help us break it](#help-us-break-it).)

## Search finds matches. Context needs relationships.

Search answers *"where do these words occur?"*. That is a different
question from *"what should be read for this task?"*.

```
grep / ripgrep

  task ──────────────────►  keyword matches


OpenContextually

  task ──►  direct matches
                 │
                 ├──►  imported dependencies
                 ├──►  tests that exercise them
                 ├──►  relevant configuration
                 └──►  governing documentation
                              │
                              ▼
                     bounded context package
```

A task like `fix the authentication bug` can need a file that never
contains the words *authentication* or *bug*. OpenContextually starts from
direct matches, follows Python's own import graph, applies repository
boundaries, runs two deterministic checks, and explains every inclusion.

Most of the value, though, is ranking and compression of files search could
already find: a grep for sqlfluff's *"indentation rule fires on a templated
line"* matches **415 files**; OpenContextually returns **12**, each with a
reason — reproducible from the corpus below.

## Install

```bash
pip install opencontextually
```

Python 3.10+, one runtime dependency. To work on OpenContextually itself:

```bash
git clone https://github.com/gammalex-ai/opencontextually
cd opencontextually
pip install -e ".[dev]"
```

## Using it

### CLI

| Command | What you get |
| --- | --- |
| `gctx "task"` | Ranked files, each with a reason |
| `gctx "task" -v` | Adds the code excerpt that justified each file |
| `gctx "task" --all` | Every included file, not just the top slice |
| `gctx "task" --json` | Full machine representation, for handing to an agent |
| `gctx "task" --root PATH` | Search somewhere other than the current directory |

`gctx` is short for *GammaLex Context*. The same command is also installed
as `octx` (the original name, kept working) and `opencontextually`. Flags
compose (`-v --all`); `--json` is unaffected by either and is always full
fidelity.

Write tasks the way you'd describe the bug. Naming a specific behavior or
symbol beats a directory-shaped noun.

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

```bash
pip install "opencontextually[mcp]"
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
same shape as `gctx --json`.

## Tested on real repositories

Scripted tests lock in fixes; they do not find them. Most real defects in
this project were found by running it against unfamiliar repositories and
reading the output, so a standing corpus is part of the project
(`benchmarks/`, with a runner that also checks determinism and sweeps for
leaked secrets).

### What it selected, and what it missed

Fourteen repositories, each with a hand-checked answer key
([`benchmarks/answer-keys.json`](benchmarks/answer-keys.json)): the files
that actually implement or test the behaviour the task names. Six were used
while tuning ranking. Eight were held out — keys written and committed
**before** the tool ran against them, nothing tuned afterward.

| Group | Repositories | Key files found | In the default view |
| --- | --- | ---: | ---: |
| Tuned | httpx, requests, flask, click, sqlfluff, django | 18/19 (95%) | 16/19 (84%) |
| Held out | black, rich, pydantic, fastapi, attrs, urllib3, pytest, scrapy | 23/29 (79%) | 19/29 (66%) |
| **All fourteen** | | **41/48 (85%)** | **35/48 (73%)** |

**79% and 66%** — the held-out figures — are the ones to argue with: they
predict a repository this project has never seen, and the default view
(compact output shows eight files) matters more than the total.

Across all fourteen: **zero** fixture, vendor, generated or CI files
selected, and **0.05%–2.1%** of repository bytes delivered.

What the held-out repos caught that tuning missed:

- **A bundled previous major version.** pydantic ships Pydantic 1 inside
  Pydantic 2 — six of eighteen slots went to `pydantic/v1/*`. **Fixed.**
- **Repeated documents.** rich's README translations, fastapi's
  `docs/en`/`docs/hi`/`docs/tr` — genuinely different pages sharing a
  filename. **Not fixed** — a dedup cap was tried and reverted.
- **Vocabulary collisions.** On django, rich ranks `progress.py`
  (`ProgressColumn`) first for a table-width task. Tracked as
  [issue #3](https://github.com/gammalex-ai/opencontextually/issues/3).

### The corpus

A 10-repository, timed subset of the fourteen above, reproducible with
`benchmarks/dogfood.py`:

| Repository | Commit | Files | Time | Task |
| --- | --- | ---: | ---: | --- |
| [encode/httpx](https://github.com/encode/httpx) | [`b5addb64`](https://github.com/encode/httpx/commit/b5addb64f016) | 125 | 0.18s | redirect loses the authorization header |
| [psf/requests](https://github.com/psf/requests) | [`5460f467`](https://github.com/psf/requests/commit/5460f467b02e) | 128 | 0.12s | session cookie persists across redirects |
| [pallets/click](https://github.com/pallets/click) | [`36baa15f`](https://github.com/pallets/click/commit/36baa15ff831) | 166 | 0.25s | option prompt does not hide the input |
| [pallets/flask](https://github.com/pallets/flask) | [`d318b683`](https://github.com/pallets/flask/commit/d318b6834711) | 236 | 0.19s | session cookie is not set on redirect |
| [psf/black](https://github.com/psf/black) | [`8947c48e`](https://github.com/psf/black/commit/8947c48ef207) | 482 | 0.48s | string normalization changes the wrong quotes |
| [Textualize/rich](https://github.com/Textualize/rich) | [`9d8f9a37`](https://github.com/Textualize/rich/commit/9d8f9a372cc5) | 553 | 0.63s | table column width ignores the terminal size |
| [pydantic/pydantic](https://github.com/pydantic/pydantic) | [`f512b087`](https://github.com/pydantic/pydantic/commit/f512b087202f) | 824 | 1.70s | field validator not called on assignment |
| [fastapi/fastapi](https://github.com/fastapi/fastapi) | [`49033471`](https://github.com/fastapi/fastapi/commit/49033471594e) | 3,139 | 2.11s | dependency override not applied in nested routers |
| [sqlfluff/sqlfluff](https://github.com/sqlfluff/sqlfluff) | [`642e2e4a`](https://github.com/sqlfluff/sqlfluff/commit/642e2e4a34a8) | 5,955 | 2.18s | indentation rule fires on a templated line |
| [django/django](https://github.com/django/django) | [`73cc09f1`](https://github.com/django/django/commit/73cc09f14f13) | 7,085 | 7.52s | queryset filter drops the second condition |

All ten are MIT- or BSD-licensed, unaffiliated with this project, cloned
`--depth 1` on 2026-08-30 at the commits above. 18,693 files total, but
only 16,331 are actually scanned — the rest excluded before reading, mostly
gitignored build output. **Zero secret-shaped strings** reached any
package; every run was **byte-identical** across repeats. Times are
best-of-three on an M-series Mac, Python 3.13, warm cache — treat as orders
of magnitude, not a benchmark.

## Ecosystem & Community

### Works with today

Verified by the test suite and by hand against a clean install — nothing
here is aspirational.

| Surface | What it is | Status |
| --- | --- | --- |
| **`gctx` CLI** | `gctx "task"`, plus `--json`, `-v`, `--all`, `--root` | Supported |
| **Python API** | `get_context(task, root=".")` returning a `ContextPackage` | Supported |
| **MCP server** | `opencontextually-mcp`, stdio, one tool: `get_context(task, root)` | Supported — see [MCP](#mcp) |
| **Any MCP-speaking client** | Anything that can launch a stdio MCP server and call one tool | Should work; only the server is tested |

### Dedicated integrations

OpenContextually already works with Claude Code, Cursor, Codex,
MCP-compatible agents, and custom coding agents through the CLI and MCP.

Dedicated editor extensions, wrappers, and deeper integrations are still
early. `--json` and the MCP server are both stable, so anything below is
buildable today:

- an editor/IDE extension, or a wrapper for an agent harness (Cursor,
  Continue, OpenCode, Aider, your own)
- a GitHub Action posting the context package onto an issue's PR
- language support beyond Python's import graph — see
  [GOOD_FIRST_CONTEXT.md](GOOD_FIRST_CONTEXT.md)

Nothing here yet? Build one. [Tell us](https://github.com/gammalex-ai/opencontextually/issues/new?template=integration.yml)
and we may feature it here.

### The question this project is trying to answer

> **What should an agent know before it acts — and how do we prove it got
> the right context?**

Every claim above is checkable against committed answer keys in
[ContextBench](benchmarks/README.md). Found a wrong-files case, or want to
argue with a number? [COMMUNITY.md](COMMUNITY.md) has every way in.

## What it deliberately does not do

- **Follow imports outside Python.** Expansion uses the stdlib `ast`
  module; other languages get lexical matching only.
- **Understand your code.** Ranking is lexical scoring plus import
  expansion — weakest when a task's words are also the repo's naming
  convention, since filename matches then dominate.
- **Find problems for you.** Two narrow checks flag *detectable* conflicts
  — a config value contradicting docs, a symbol no test references — not
  arbitrary missing context. Quiet by default: zero findings across the
  six answer-key corpus tasks, one false positive (since fixed) across
  eleven real repos. A footer always names which checks ran.
- **Guarantee secrets stay out of excerpts.** Redaction masks
  secret-shaped keys and high-entropy strings, but it is best-effort
  pattern matching, not a secrets scanner. See [SECURITY.md](SECURITY.md).

## Scope, determinism, and safety

Discovery reads everything under `--root` minus what git already ignores —
honoring nested `.gitignore` files, `.git/info/exclude`, and the global
`core.excludesFile`, plus an optional `.opencontextuallyignore`. All
resolved without shelling out to git, so it works in directories that
aren't repositories at all.

Runs are deterministic: the same task and repository produce byte-identical
output, which is asserted in the test suite and re-checked by the corpus
runner. Nothing is written anywhere, and no network call is ever made.

## Help us break it

OpenContextually is early. The most useful contribution right now isn't
telling us it works — it's finding where it doesn't.

```bash
pip install opencontextually
gctx "the bug you're currently fighting"
```

**Bad result?** [Bring us the context failure](https://github.com/gammalex-ai/opencontextually/issues/new?template=context_failure.yml)
with the task, the files you expected, and the files it actually returned —
that's exactly what shapes ContextBench and the next fixes.

## Contributing

Bug reports, context failures, ContextBench cases, integrations, language
support and documentation fixes are all welcome —
[CONTRIBUTING.md](CONTRIBUTING.md) covers the workflow and the scope
boundaries, [GOOD_FIRST_CONTEXT.md](GOOD_FIRST_CONTEXT.md) lists concrete
places to start, and [COMMUNITY.md](COMMUNITY.md) is where to find people.

## License

MIT

---

<div align="center">

Give your agent better context before it starts working.

```bash
pip install opencontextually
gctx "fix the bug"
```

⭐ [Star OpenContextually](https://github.com/gammalex-ai/opencontextually) · Built by [GammaLex AI](https://github.com/gammalex-ai)

</div>
