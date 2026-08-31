# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
