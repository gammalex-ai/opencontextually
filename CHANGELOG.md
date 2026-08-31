# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.1] - 2026-08-31

### Fixed

- **PyPI's project page told visitors to install a package they were
  already looking at on PyPI from GitHub.** 0.2.0 was published from a
  README written before the first PyPI release existed, so its "Not on
  PyPI yet" caveat and `pip install git+https://...` instructions shipped
  as the package's PyPI description verbatim. PyPI does not allow
  re-uploading files for an already-published version, so this release
  exists to carry the corrected README — no code changes.

## [0.2.0] - 2026-08-31

### Changed

- **Ranking now scores a file on its own relevance to the task, not on who
  imports it.** Import expansion used to give a reached file
  `parent_score * 0.5` with no reference to the task at all, so on httpx all
  seven of `_models.py`'s import neighbours — `_content.py` and
  `_decoders.py` among them, which match nothing in *"redirect loses the
  authorization header"* — outranked `_auth.py`, the file that builds the
  Authorization header. A reached file now keeps its own score plus a small
  capped relationship bonus that decays per hop. An import edge answers
  "should I look at this?", not "this is half as relevant as its
  neighbour".
- **Candidate discovery and delivery are separate budgets.** One cap used to
  decide both how many files were considered and how many shipped.
  Considering a file is free — every file is already scored — while
  delivering one costs the reader context. sqlfluff's
  `rules/layout/LT02.py`, the rule its task names, ranks 16th of 5,451 on
  its own merits and was cut by a cap of 12 before it could compete.
- **Changelogs and release notes are damped.** They name every feature ever
  shipped, so they match almost any task and match it repeatedly — the same
  problem test files already have. They still rank when nothing else does.

Measured across six pinned repositories with hand-checked answer keys:
known-relevant files recovered went from 17/19 to 18/19, and files
surfaced in the default eight-line view from 12/19 to 16/19, with
compression unchanged.

Those six were also the repositories the constants were tuned against, so
four more — black, rich, pydantic, fastapi — were held out: keys written
and committed before the tool was run against them, nothing tuned
afterwards. They score **11/16 recovered and 9/16 in the default view**,
against 18/19 and 16/19 on the tuned six. Across all ten: **29/35 (83%)
and 25/35 (71%)**. The held-out figures are the ones that predict
behaviour on an unfamiliar repository, and they are the ones the README
quotes.

### Fixed

- **Exclusion counts no longer double-count.** Three separate cap events
  were summed on top of a below-threshold count taken over every scored
  file, so httpx reported "17 relevant · 161 excluded" for 115 scannable
  files, and described import candidates that never cleared the bar as
  "relevant files dropped". Every scanned file now lands in exactly one
  bucket, and included + excluded reconciles with files scanned.
- **The source distribution is self-testing.** It shipped `tests/` without
  the `examples/` fixture those tests read, so `pip download` + `pytest`
  failed 22 tests on a missing directory rather than on anything real.

### Known limitations

- A task's vocabulary can be dense in the wrong subsystem: for *"queryset
  filter drops the second condition"*, django's `contrib/admin/filters.py`
  outranks the ORM. Term-rarity weighting was measured and made other
  repositories worse. Tracked in
  [#3](https://github.com/gammalex-ai/opencontextually/issues/3) and pinned
  by a regression test.
- A repository holding more than one copy of something defeats ranking, in
  three forms found by the held-out run: a bundled previous major version
  (six of eighteen slots go to `pydantic/v1/*`, while `main.py` is missed),
  translated documentation counted once per language (five README
  translations in rich, three doc languages in fastapi), and benchmark or
  example trees inside the project itself.
- `test_reference_gap` reports symbols no test *names*. On black — whose
  formatting tests are data-driven fixture files — that produced five
  findings that are literally true and probably not actionable.

## [0.1.1] - 2026-08-30

### Changed

- **Primary CLI renamed to `gctx` (GammaLex Context); `octx` and
  `opencontextually` remain supported aliases.** All three are installed and
  behave identically, so anything already calling `octx` keeps working —
  scripts, shell aliases, and CI invocations need no change. Documentation
  and examples now show `gctx`; if you have seen older docs using `octx`,
  they are still correct.
- Usage text and error messages name whichever command was actually
  invoked, instead of always printing `octx`. Running `octx --help` says
  `octx`, `gctx --help` says `gctx`.
- The MCP server entry point is **unchanged**: `opencontextually-mcp`.

### Fixed

- Warnings raised while parsing a scanned file no longer reach the terminal.
  `ast.parse` emits `SyntaxWarning` for constructs like an invalid escape
  sequence, and reported them as `<unknown>:108` — a line number with no
  filename, useless and alarming in equal measure. Running against psf/black
  produced 24 lines of stderr against 22 lines of output. This tool reads
  code, it does not lint it.
- **Discovery no longer crashes on an ignore pattern `pathspec` rejects.** A
  `.gitignore` containing a bare `!` line — which `git status` reads without
  complaint — raised `GitIgnorePatternError` out of `discover()`, so
  `get_context()` died with a traceback on any repository containing one.
  Found by running against psf/black, which ships two such files as fixtures
  for its own handling of them. Only the offending line is skipped now; the
  file's remaining patterns still apply, so nothing it legitimately excluded
  becomes visible.
- `configuration_discrepancy` no longer reads JSON/YAML **test fixtures as
  project configuration**. Directory names were matched whole, so `fixtures/`
  was recognized as test data but `demo_fixtures/` was not. Found on a real
  42,000-file repository, where a golden test bundle was compared against a
  roadmap document and produced two findings — both false. Compound fixture
  directory names (`demo_fixtures`, `test-fixtures`, `golden_fixture_data`)
  are now recognized; `data`, `build`, and `vendor` are deliberately still
  matched whole, since `data_models/` and `build_tools/` are plausible
  hand-written source directories.
- `trace["rules_run"]` (and the "Checks run:" footer) no longer claim a check
  ran when it did not. The list was hardcoded, so a task matching nothing in
  the repo — which skips both checks — still reported both as having run. An
  empty selection now correctly reports `Checks run: selection only`.
- README's flagship example output was several releases stale, showing the
  pre-rewrite exclusion footer. It is now regenerated and pinned by a test.
- README's `configuration_discrepancy` precision claim ("fired on zero of
  nine real repos") is corrected to record the false positives found on the
  tenth and eleventh.
- `pytest` now always tests the current checkout. An editable install from
  another clone put its own path on `sys.path` ahead of the repository under
  test, so a run from a second clone or a git worktree could pass while
  exercising different code entirely. `pythonpath = ["src"]` fixes the
  default and `tests/conftest.py` fails loudly if it is ever circumvented.

## [0.1.0] - 2026-08-27

Initial release. Not yet published on PyPI — install with `pip install -e .`
from a clone.

### Added

- The hero API, `get_context(task, root=".")`, returning a
  `ContextPackage`: the files relevant to a task, each with a reason,
  score, and bounded excerpts, plus a count of everything excluded and why.
- `octx` / `opencontextually` CLI: one positional task string, `--root`,
  `-v`/`--verbose` for excerpts, `--all` to list every included file, and
  `--json` for the full-fidelity machine representation
  (`package.to_dict()`).
- Lexical file selection (path, filename, and content matching against
  the task) plus transitive Python import-graph expansion via the stdlib
  `ast` module: files that don't mention any of the task's words are
  still surfaced when they're reachable from a matching file through
  Python's own `import` statements, with the edge path recorded in the
  file's `provenance`.
- Bounded excerpts per included file, capped per span, per file, and by a
  package-wide byte budget, with secret redaction applied before any
  excerpt leaves a file (see `SECURITY.md`).
- Two narrow, named, high-precision/low-recall checks:
  `configuration_discrepancy` (the same config key declared with
  different scalar values across a config file and a doc) and
  `test_reference_gap` (a task-relevant term or config key with no
  discovered test referencing it). Both report nothing when a match
  isn't clear, rather than risk a false positive.
- Optional MCP stdio server (`opencontextually[mcp]`, command
  `opencontextually-mcp`) exposing a single `get_context` tool with the
  same JSON shape as `octx --json`.
