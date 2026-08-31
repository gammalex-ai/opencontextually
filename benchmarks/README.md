# ContextBench

The benchmark behind every selection-quality number in the README.

It asks one question: **does `gctx` select the files a developer would
actually need for a task, and leave out the rest?** Not how fast it walks a
repository — whether the context it hands an agent is the right context.

Two pieces:

- **[`answer-keys.json`](answer-keys.json)** — for each repository and
  task, the files that genuinely implement or test the behaviour, read out
  of the project at a pinned commit. This is the benchmark. The rest is
  plumbing.
- **[`dogfood.py`](dogfood.py)** — runs the corpus, and separately checks
  determinism and sweeps for anything secret-shaped surviving into a
  package.

## Tuned and held out

Answer keys carry a `group`. Six repositories were used while tuning
ranking constants, so their results are fitted to an unknown degree. Eight
were held out: their keys were written and committed *before* the tool was
run against them, and nothing was tuned afterwards. The held-out number is
the one that predicts behaviour on a repository this project has never
seen, and it is the one the README quotes.

Keeping that split intact matters more than any individual score. If you
tune against a held-out repository, say so in the PR — it moves to the
tuned group, and the project needs a new reserved set.

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

`fetch-corpus.sh` clones the ten public repositories the README's figures
were measured against, each pinned to the exact commit they were measured
at. Pinning is what makes those figures checkable: file counts and timings
drift as the projects change. The ten are unaffiliated public projects,
chosen for a spread of size and layout rather than for flattering results.

Repository paths are never committed — a corpus is personal to whoever runs
it, and some useful repositories are private.

`--json` emits machine-readable results (diff two runs to see what a change
moved). `--no-determinism-check` halves runtime by skipping the second run
per case.

## What it reports, and what to look for

Per case: latency, included/excluded counts, the top results with reasons,
which checks fired, whether two runs are byte-identical, and whether anything
secret-shaped survived redaction into the serialized package.

The script exits non-zero on the two outcomes that are never acceptable — a
secret-shaped string in the package, or a non-deterministic result. Everything
else is for a human to read. In particular, read for:

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
