# Community

**What should an agent know before it acts, and how do we prove it got the
right context?**

That is the whole question. The first half is a design problem and the
second half is a measurement problem, and this project is not going to
answer either one from a single laptop and ten open-source repositories.
It needs repositories we have never seen, tasks we would not have thought
to phrase, and agents we do not run.

## Where to find people

| | |
| --- | --- |
| **Discord** | [Join the server](DISCORD_INVITE_URL) — questions, half-formed ideas, and showing off what you built |
| **GitHub Discussions** | [Ask or propose](https://github.com/gammalex-ai/opencontextually/discussions) — anything that benefits from being searchable later |
| **Issues** | [Report something concrete](https://github.com/gammalex-ai/opencontextually/issues) — a wrong selection, a crash, a benchmark case, an integration |

Rule of thumb: if it has a reproduction, open an issue. If it needs a
conversation first, Discord or Discussions.

Everything here is covered by the [Code of Conduct](CODE_OF_CONDUCT.md).
Security issues go through [SECURITY.md](SECURITY.md) instead — please
don't open a public issue for those.

## The most valuable thing you can bring us

> **Agent failed because it read the wrong context? Bring us the case.**

[Open a context failure report.](https://github.com/gammalex-ai/opencontextually/issues/new?template=context_failure.yml)

Not a bug report about OpenContextually — a case where *any* coding agent
did the wrong thing because it was working from the wrong files. Those are
the ground truth this project is built to chase, and they are almost
impossible to synthesize convincingly. Whether or not you were running
`gctx` at the time, the case is useful.

What makes one good: the task as you actually phrased it, what the agent
read, what it should have read, and whether we can reproduce it on a public
repository.

## Ways to contribute

Every one of these is a real contribution. They are not ranked.

**Report a context failure** — the above. No code required.

**Contribute a ContextBench case** — a public repository, a pinned commit,
a task phrased as a symptom, and the files a developer would actually need
open. That last part is the contribution; the rest is bookkeeping. See
[benchmarks/README.md](benchmarks/README.md).

**Improve selection** — ranking, exclusion, relationship-following. The
bar is a measurement, not an argument: run the corpus before and after and
show what moved. Known-open problems are in
[GOOD_FIRST_CONTEXT.md](GOOD_FIRST_CONTEXT.md).

**Add language support** — import-following is Python-only today, which
means every other language gets lexical matching and nothing else. This is
the single biggest gap in the project.

**Build an integration** — the `--json` output and the MCP server are
stable interfaces. Editor extensions, agent-harness wrappers, CI actions,
alternative renderers. [Tell us about it](https://github.com/gammalex-ai/opencontextually/issues/new?template=integration.yml)
and we may feature it in the README.

**Improve docs and examples** — including telling us where the README lies,
oversells, or assumes something you did not know.

## What we will say no to

Not to be discouraging — to save you the work. OpenContextually is
deliberately small: no model, no API key, no network, no database, no
config file, one hero API. Agents, orchestration, memory products, vector
or graph stores, hosted services and plugin marketplaces are out of scope,
and [CONTRIBUTING.md](CONTRIBUTING.md#scope-discipline) explains why in
more detail. If you are unsure, ask before you build.
