# ContextBench

The benchmark behind every selection-quality number in the README.

It asks one question: **does `gammx` select the files a developer would
actually need for a task, and leave out the rest?** Not how fast it walks a
repository — whether the context it hands an agent is the right context.

Two pieces:

- **[`answer-keys.json`](answer-keys.json)** — for each repository and
  task, the files that genuinely implement or test the behaviour, read out
  of the project at a pinned commit. A key may also carry `task_variants`
  (`technical` and `natural` wording) that reuse those exact files rather
  than creating a second truth set. This is the benchmark. The rest is
  plumbing.
- **[`dogfood.py`](dogfood.py)** — runs the corpus, and separately checks
  determinism and sweeps for anything secret-shaped surviving into a
  package.

## Development, regression, and regression 2

Answer keys carry a `group`: `development`, `regression`, or `regression_2`
— see [`answer-keys.json`](answer-keys.json)'s own `_comment` for the full
history. Six repositories (`development`) were used while tuning ranking
constants, so their results are fitted to an unknown degree.

`regression` and `regression_2` were both written and committed *before*
the tool was run against them, and nothing was tuned afterwards on first
sight of those results — but neither is a genuinely held-out set any more:
`regression`'s result exposed a failure class that then drove a ranking
change, and `regression_2` was reserved specifically to check whether that
change generalized (it informed the decision to partially revert it). Both
are now spent as evidence. Quote all three groups separately — the README
does, and so does this script's own report (`recall_by_group` in `--json`
output, or the `cohort ...` lines in the plain-text report) — never combine
them into one "held-out" number that reads as stronger evidence than it is.

Keeping the split honest matters more than any individual score. If you
tune against a `regression`/`regression_2` repository, say so in the PR —
its results move to `development`, and the project needs a genuinely
untouched reserved set before the next round of ranking work.

## Contributing a case

The valuable part is the answer key, and it does not require writing code:
a public repository, a pinned commit, a task phrased as a symptom, and the
files someone fixing it would need open.
[Open a case.](https://github.com/gammalex-ai/opencontextually/issues/new?template=contextbench_case.yml)

Cases from repositories that are **not Python** are worth the most right
now — the corpus is entirely Python, and so is import-following.

---

## Why this exists

Not installed, not imported by the package, not run in CI. `benchmarks/`
sits outside `src/`, so `pip install` never sees it.


Most real defects in this project were found by running the tool against
unfamiliar repositories and reading the output — not by the test suite. The
test suite locks in fixes; this harness is how the defects get found in the
first place. It exists so those runs are repeatable: same repositories, same
tasks, so a ranking change can be compared against a previous run instead of
argued about.

## Use

```
./benchmarks/fetch-corpus.sh ~/src
cp benchmarks/corpus.example.json benchmarks/corpus.local.json
$EDITOR benchmarks/corpus.local.json          # already gitignored
python benchmarks/dogfood.py benchmarks/corpus.local.json
```

The default `--phrasing existing` run preserves the historical task strings
and remains directly comparable with earlier results. To expose vocabulary
sensitivity against the same answer keys:

```
python benchmarks/dogfood.py benchmarks/corpus.local.json --phrasing technical
python benchmarks/dogfood.py benchmarks/corpus.local.json --phrasing natural
python benchmarks/dogfood.py benchmarks/corpus.local.json --phrasing all
```

`fetch-corpus.sh` clones the fourteen public repositories the README's
answer-key figures were measured against, each pinned to the exact commit
they were measured at (a timed subset of ten also appears in the README's
corpus table). Pinning is what makes those figures checkable: file counts
and timings drift as the projects change. All fourteen are unaffiliated
public projects, chosen for a spread of size and layout rather than for
flattering results.

Repository paths are never committed — a corpus is personal to whoever runs
it, and some useful repositories are private.

`--json` emits machine-readable results (diff two runs to see what a change
moved). `--no-determinism-check` halves runtime by skipping the second run
per case. For a CI-style validation run rather than casual dogfooding, add
`--strict` and a threshold:

```
python benchmarks/dogfood.py benchmarks/corpus.local.json --strict --min-recall 0.80
```

## What it reports, and what to look for

Per case: latency, included/excluded counts, answer-key files found in the
package and top eight, the top results with reasons, which checks fired,
whether two runs are byte-identical, and whether anything secret-shaped
survived redaction into the serialized package.

The script exits non-zero on: a secret-shaped string in the package, a
non-deterministic result, zero cases having run at all (a corpus that is
entirely missing on disk validates nothing and must never look like a
passing run), or — under `--strict` — a configured repository that isn't on
disk, a configured task with no matching answer key, a checkout whose HEAD
doesn't match the corpus config's pinned `commit`, or an answer key
referencing a file that no longer exists. `--min-recall`/`--min-top8-recall`
add an explicit regression threshold (combined across whatever cases had an
answer key) on top of that, for either mode. Use `--strict` plus a threshold
for CI/regression tracking against a known corpus; the plain default mode
stays a casual dogfooding tool that tolerates an incomplete personal corpus.
Everything else is for a human to read. In particular, read for:

- an obviously relevant file missing from the top results
- an irrelevant file outranking the implementation
- a `CONFLICT:` line that is not actually a conflict — the bar for
  `configuration_discrepancy` is that **any** false positive is a bug
- a `GAP:` line reporting something no one would call a gap
- `! N above-bar files dropped` — files that cleared the relevance bar and
  were then cut for space, which means the reader is missing real results
- a large jump in latency or package size against a previous run

When you find one: reproduce it, write a regression test, fix it, run the
unit tests, then re-run this corpus and check whether rankings moved anywhere
you did not intend.
