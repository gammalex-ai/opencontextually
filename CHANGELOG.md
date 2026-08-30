# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
