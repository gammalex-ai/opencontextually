# Good first context

Concrete places to start, with the real state of each. Nothing here is
invented to look welcoming — every item is either an open failure we have
measured or a gap we know is there.

New to the project? Read the
[Quickstart](README.md#quickstart) first, run `gctx` on a repository you
know well, and see whether you agree with what it picked. Disagreeing
usefully is itself a contribution.

## Bring a case — no code needed

**Report a context failure.** An agent read the wrong files and did the
wrong thing. Yours or anyone's, `gctx` involved or not.
[Template.](https://github.com/gammalex-ai/opencontextually/issues/new?template=context_failure.yml)

**Add a ContextBench case.** Pick a public repository we don't already
cover, phrase a task the way a developer would describe the symptom, and
list the files someone would actually need open to fix it. Then run it and
tell us what happened — including if the answer was good.
[Template.](https://github.com/gammalex-ai/opencontextually/issues/new?template=contextbench_case.yml)

The corpus is Python-heavy and Python-only. A case from a TypeScript, Go,
Rust or Java repository is worth more than another Python one right now.

## Open problems, measured

These are real, reproducible, and unfixed. Each has evidence in the repo.

**Vocabulary collisions across subsystems.**
[#6](https://github.com/gammalex-ai/opencontextually/issues/6). Asked
"queryset filter drops the second condition", django ranks
`contrib/admin/filters.py` — admin UI list filters — first, because it uses
the task's words more densely than the ORM does. Term-rarity (IDF)
weighting was tried and measured; it made other repositories worse.
`tests/test_subsystem_collision.py` pins the failure as a strict-xfail, so
a fix announces itself.

**One document, many copies.** rich spends five of eighteen slots on README
translations; fastapi repeats a page across `docs/en`, `docs/hi` and
`docs/tr`; pytest has 50 release announcements. A near-duplicate family cap
was written, measured, and reverted for collapsing genuinely different
pages that share a filename (`docs/index.md` vs
`docs/contributing/index.md`). The waste is real; the fix is open.

**Import-following is Python-only.** `selector.py` walks the import graph
with the standard library `ast` module. Every other language falls back to
lexical matching, which is the weakest thing the tool does. A resolver for
one more language — JS/TS imports, Go packages — is the highest-value
change available.

**The prose tail.** Roughly a third of a typical package is documentation,
some of it matched on filler words: flask's `docs/config.rst` is selected
for a task containing the word "set". The top of the package is clean; the
tail is not earned.

## Smaller, well-defined

- **Reasons that read well.** Every included file carries a one-line
  reason. Some are excellent ("defines `_mask_hidden_input`"), some are
  filler ("references session"). Better reasons for the weaker cases.
- **A new narrow check.** In the spirit of `configuration_discrepancy` and
  `test_reference_gap`: named, deterministic, high-precision, quiet by
  default, with documented scope. Any false positive is a bug.
- **Docs and examples.** A second worked example under `examples/`, in a
  language or layout the current one doesn't cover.

## Before you send a PR

- `pytest` passes (see [CONTRIBUTING.md](CONTRIBUTING.md)).
- Anything touching selection comes with corpus numbers before and after.
  `benchmarks/README.md` explains how to run it; "it looks better" is not
  reviewable.
- A fix for a real failure comes with a regression test that fails without
  it.
- Ask first if you are unsure it is in scope. A declined PR is a worse
  outcome for you than a five-minute issue.
