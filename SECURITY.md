# Security policy

## What this tool reads

OpenContextually is a local, offline file scanner. When you run
`get_context(task)` / `gctx`, it walks the given project root on disk and
reads file contents to select, rank, and excerpt files relevant to `task`.

It does **not** blanket-exclude dot-files or dot-directories. `.env`,
`.eslintrc`, `.circleci/`, and similar are frequently the most
task-relevant files in a repo, so they are discoverable like any other
file, subject only to a narrow denylist (`.git/`, `.hg/`, `.svn/`), binary
detection, a size cap, and whatever `.gitignore` / `.opencontextuallyignore`
already excludes. In practice this means OpenContextually will read `.env`
files if they exist and are not gitignored.

## Redaction is lexical and best-effort — not a guarantee

Before any excerpt is emitted (in `gctx` plain-text output, `--json`, or
the MCP tool), it passes through a redactor that masks values on
secret-shaped keys (`key`, `token`, `secret`, `password`, `credential`,
`api_key`, and similar) and standalone high-entropy strings. Key names and
line numbers are preserved — that's what `configuration_discrepancy`
needs — but the values themselves are masked before they leave the file.

This is a **pattern match, not a secrets scanner**. It has already needed
fixing more than once during development as new leakage shapes were found.
Concretely, it can miss:

- a credential under a key name it doesn't recognize as secret-shaped
- a secret embedded mid-string rather than as a clean key/value pair
- a low-entropy or short secret that doesn't clear the entropy heuristic
- any secret-bearing format the redactor hasn't been taught to recognize yet

Do not rely on OpenContextually as a control that prevents credential
exposure. If your project has files you don't want read at all, exclude
them via `.gitignore` or `.opencontextuallyignore` before running the tool
against a repo — that's an exclusion, not a redaction, and it's the
reliable option.

## Reporting a vulnerability

If you find a case where OpenContextually's output includes a credential,
token, or other secret that should have been redacted, or any other
security-relevant issue, please report it **privately** rather than in a
public issue:

- Preferred: use [GitHub private vulnerability
  reporting](https://github.com/gammalex-ai/opencontextually/security/advisories/new)
  on this repository.
- Alternative: email **yen.kha@gammalex.com**.

Please include the task string and repo shape (or a minimal reproduction)
that triggered the leak — that pairing is what actually gets these fixed.
Do not include the real secret value itself in the report; a redacted or
synthetic stand-in is enough to reproduce the bug.

We aim to acknowledge private reports within a few business days. This is
a small open-source project maintained on a best-effort basis, not a
service with a contractual SLA.

## Supported versions

OpenContextually is pre-1.0 (`0.1.x`). Security fixes land on the latest
release; there is no back-porting to older `0.1.x` versions.
