"""Lexical selection: task -> tokens -> scored, ranked ContextItems.

This is the first cut at SELECT. It has no notion of imports or transitive
reach (that is step 5) -- it scores each discovered file against the task's
tokens using filename hits, Python symbol-definition hits, and damped
content frequency, then keeps the files that score above a threshold, up to
a cap.
"""

from __future__ import annotations

import ast
import math
import re
from pathlib import PurePosixPath

from .context import ContextItem, Excerpt
from .discovery import CONFIG_EXTENSIONS, DATA_DIR_SEGMENTS, DiscoveredFile
from .filecache import RunCache

# --------------------------------------------------------------------------
# Tunable constants. Everything a future tuning pass (step 11) might touch
# lives in this block so it can be adjusted in one place.
# --------------------------------------------------------------------------

# --- bug fix: threshold too permissive ---
# Below this bar, a bare score > 0.0 admitted a single incidental content
# mention (e.g. "user experience" in a marketing bio, matched only once,
# with no filename/symbol signal and no role bonus) as if it were a real
# result. Observed on a real Next.js/TypeScript repo with no login/session
# code at all: such incidental mentions scored ~1.4, while every genuine
# signal in the auth_bug fixture (filename hit, symbol hit, or a single
# config/doc/test mention plus its ROLE_BONUS) scores comfortably above 2.0.
# Raising the threshold to 2.0 means a bare, role-bonus-less, single
# content mention no longer clears the bar on its own -- consistent with
# the plan's "no relevant context found" being a correct, valuable answer
# rather than something to avoid.
SCORE_THRESHOLD = 2.0
MAX_SEEDS = 12

WEIGHT_FILENAME = 10.0
WEIGHT_SYMBOL = 6.0
CONTENT_CAP = 6.0
CONTENT_MULT = 2.0
ROLE_BONUS = 2.0
# "test" deliberately excluded -- see TEST_SIGNAL_DAMPING below. A term-
# matching test file no longer gets a flat bonus on top of its (already
# repetition-heavy) symbol/content signal; config and docs, which do not
# have this repetition problem, are unaffected.
ROLE_BONUS_ROLES = {"config", "docs"}

MIN_TOKEN_LEN = 3

# --- bug fix: test files systematically outrank the source they test ---
# A test file's `def test_<scenario>` names are, by construction, written
# in task vocabulary -- a handful of source functions implement a feature,
# but dozens of test cases each *describe* it in a slightly different way
# ("test_redirect_with_cookie", "test_session_cookie_persists", ...). That
# repetition is real, but it is repetition of description, not evidence of
# implementation centrality the way a source file's own symbol/content
# matches are. Left undamped, symbol_score alone (uncapped, WEIGHT_SYMBOL
# per matching def) routinely reaches the hundreds for a large test module
# while the source file that actually implements the fix scores in the
# tens -- observed on psf/requests: tests/test_requests.py scored 405.2
# (396 of it from 66 term-matching test-method defs) against
# src/requests/sessions.py's 55.5, even though sessions.py is where a
# session-cookie-redirect fix would actually be made. TEST_SIGNAL_DAMPING
# scales down symbol_score and content_score -- the two components that
# scale with how many times a file *mentions* a term -- for test-role
# files only. filename_score is left untouched: a test file's own name
# (e.g. test_sessions.py) is a deliberate, non-repeated signal, not a
# repetition artifact, and remains a legitimate (if now typically
# insufficient on its own) reason to include it.
TEST_SIGNAL_DAMPING = 0.1

# --- real-repo tuning (step 11) ---
# Observed on a data-heavy real repo (~/Dali): a 700KB benchmark-results
# JSON outranked the source file that actually implements the task, purely
# on repeated-content volume. Two independent, narrowly-scoped penalties
# address this without touching the filename or symbol-definition signals
# (the strongest, most trustworthy evidence of relevance regardless of
# file size or location):
#   - LARGE_FILE_CONTENT_PENALTY damps only the content-frequency
#     component for files over LARGE_FILE_BYTES that look data-like (see
#     below) -- a big data blob mentioning a term hundreds of times is not
#     hundreds of times more relevant than a small file mentioning it once.
#   - DATA_PATH_PENALTY damps the *whole* score for files under a
#     recognizable data/vendor/build directory (see discovery.DATA_DIR_SEGMENTS),
#     since bulk data and vendored/generated output are not the files a
#     developer wants surfaced first even when they happen to score well.
# Generated files (marked as such in their own header) get the same
# treatment for the same reason.
#
# --- bug fix: the size penalty punished large *source* files identically
# to large *data* files -----------------------------------------------
# The original LARGE_FILE_CONTENT_PENALTY applied to every file over
# LARGE_FILE_BYTES regardless of what the file actually was. That is
# backwards for a code tool: on this very repo, `octx "the redaction masks
# ordinary code"` omitted selector.py (85,609 bytes -- the module that
# *implements* redact_text/_redact_line/_looks_like_secret_key) while
# keeping three smaller files that merely *describe* redaction, purely
# because selector.py's content score was multiplied by 0.3 for being over
# LARGE_FILE_BYTES. sqlfluff has 23 Python files over 50KB -- its rule
# engine, its dialects, its core linter -- every one of them would be
# steered away from under the old flat rule for any task that happened to
# touch them.
#
# The fix: judge whether a large file looks like *data* or like *source*
# before deciding how hard to damp it, reusing signals the file cache
# already computed (no re-parsing):
#   - A large `.json`/`.csv`/`.tsv`/`.jsonl`/`.ndjson` file, or a
#     recognized lockfile/minified asset (already excluded from content
#     scoring entirely via `_is_asset_like`), is data by construction --
#     full LARGE_FILE_CONTENT_PENALTY.
#   - A large `.py` file that parses and defines a plausible number of
#     top-level/nested symbols relative to its size (see
#     LARGE_PY_MIN_SYMBOL_DENSITY, computed from `cache.get_record(...)
#     .defs`, already gathered for the symbol-matching pass above -- no
#     extra parse) is source, not data: LARGE_SOURCE_CONTENT_PENALTY (no
#     damping) applies instead. A `.py` file that fails to parse, or
#     parses but defines almost nothing relative to its size (a huge
#     generated data literal saved with a `.py` extension), still gets the
#     full data penalty -- extension alone is not enough.
#   - A large file in another known source-language extension (JS/TS/Go/
#     Rust/Java/Ruby/C/C++ -- no stdlib AST available for these) falls
#     back to the same average-line-length signal `_is_asset_like` already
#     uses to detect minified/serialized output: plausible line lengths
#     get the gentler LARGE_WEAK_SOURCE_CONTENT_PENALTY rather than the
#     full data penalty, since we cannot verify symbol structure the way
#     we can for Python.
#   - Everything else (docs, config, unrecognized extensions) keeps the
#     original full penalty -- the conservative default for a file we have
#     no structural signal for.
#
# Verified against sqlfluff (23 large .py files, density 0.084-4.7
# defs/KB, all now retained with the source penalty) and against a
# fixture pairing a large data-like JSON with a smaller genuinely-relevant
# source file matching the same terms -- the JSON is still damped below
# the source file (see test_large_data_file_still_demoted_below_source /
# test_large_source_file_with_symbols_still_ranks in test_selector.py).
LARGE_FILE_BYTES = 50_000
LARGE_FILE_CONTENT_PENALTY = 0.3
LARGE_SOURCE_CONTENT_PENALTY = 1.0
LARGE_WEAK_SOURCE_CONTENT_PENALTY = 0.6

# Defs (top-level or nested -- same full-tree walk cache.get_record()
# already provides) per KB of file size. Calibrated against real repos:
# the *lowest*-density large source files actually observed (fastapi's
# tests/test_include_router_defaults_overrides.py at 0.084 defs/KB and
# fastapi/param_functions.py at 0.129 defs/KB, both genuine hand-written
# source with unusually large docstrings) clear this with several times
# margin, while a data literal saved as .py (0 defs, any size) does not.
LARGE_PY_MIN_SYMBOL_DENSITY = 0.05

# Formats that are data by construction regardless of how they score --
# never given the source-file exemption above even if large. (Lockfiles
# and other minified/vendored assets are already excluded from content
# scoring entirely by `_is_asset_like` and so are unaffected by this list
# either way.)
LARGE_FILE_DATA_EXTENSIONS = {".json", ".csv", ".tsv", ".jsonl", ".ndjson"}

# Source-language extensions with no stdlib AST available to measure
# symbol density directly -- these fall back to the average-line-length
# check instead (see LARGE_WEAK_SOURCE_CONTENT_PENALTY above). Kept
# separate from (and not imported from) discovery._SOURCE_EXTENSIONS,
# which is a private module-internal name.
LARGE_WEAK_SOURCE_EXTENSIONS = {
    ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".rb", ".c", ".cpp", ".h", ".hpp",
}
# Below this average line length, a large non-Python source-extension file
# still reads like hand-written code rather than a serialized blob --
# comfortably under ASSET_AVG_LINE_LENGTH (500), which already catches the
# actually-minified case via `_is_asset_like` before this check ever runs.
LARGE_WEAK_SOURCE_AVG_LINE_LENGTH = 200

DATA_PATH_PENALTY = 0.25

# --- bug fix: tutorial/example directories outrank implementation ---
# Observed on a real repo (fastapi): for "dependency override not applied
# in nested routers", the top two results were
# docs_src/dependency_testing/tutorial001_an_py310.py and a sibling
# tutorial script -- documentation example code -- ranked above the actual
# dependency-injection implementation. Like the test files TEST_SIGNAL_DAMPING
# addresses, a tutorial/example script is written in feature vocabulary (it
# exists to demonstrate the feature by name) and so wins on term density,
# while being the least useful place to make a fix. EXAMPLE_PATH_PENALTY
# damps the *whole* score for files under a recognizable example/tutorial
# directory (EXAMPLE_DIR_SEGMENTS below) -- damped, not excluded, since on
# some tasks (e.g. "the tutorial's install instructions are wrong") an
# example genuinely is the right file, and the project's own
# examples/auth_bug fixture lives under "examples" itself.
#
# Computed from the path *relative to the given root* (DiscoveredFile.path
# is already root-relative), not from an absolute path -- critical because
# the e2e tests point --root directly at examples/auth_bug, so "examples"
# never appears as a path segment relative to that root and the fixture is
# unaffected.
EXAMPLE_DIR_SEGMENTS = {
    "docs_src", "examples", "example", "tutorial", "tutorials", "samples",
    "snippets", "demo",
}
EXAMPLE_PATH_PENALTY = 0.35

_GENERATED_MARKERS = (
    "do not edit", "do not modify", "autogenerated", "auto-generated",
    "@generated", "generated by",
)
GENERATED_FILE_PENALTY = 0.15
_GENERATED_SNIFF_CHARS = 4096

# --- bug fix: frequency rewarded, distinct-term coverage ignored ---
# A file that repeats one incidental task term many times ("mobile" as a
# CSS breakpoint value, 21 times; "navigation" as a homonym import from
# next/navigation) could out-rank nothing on its own -- content_score is
# capped at CONTENT_CAP -- but it still cleared SCORE_THRESHOLD and
# consumed one of MAX_SEEDS/MAX_INCLUDED's limited slots, crowding out nothing
# in particular but adding pure noise to the package. Observed on a real
# TypeScript repo (task "navigation dropdown is broken on mobile", 4 terms):
# ranks 4-8 of an 8-result package each matched exactly one of the four
# terms, while ranks 1-3 (the actually relevant files) each matched three.
# Observed again on ~/Dali: four files tied at an identical score, three of
# them tests matching only "citation" via a shared import, with excerpts
# ("test_empty_names_returns_empty") that have nothing to do with the task.
#
# A file matching several *distinct* task terms is categorically more
# likely to be relevant than one matching a single term many times -- that
# is the signal frequency-based scoring cannot see on its own.
# COVERAGE_EXPONENT makes coverage a real multiplier, not a small tiebreak:
# at exponent 1 (linear in coverage_ratio), a file matching 1 of 4 terms
# keeps only 25% of its symbol/content score against 75% for a 3-of-4
# match -- already a 3x spread, and content_score's own CONTENT_CAP means
# raw repetition (21x, 40x, ...) was never more than a fixed 6.0 to begin
# with, so linear coverage on top of that cap is already enough to push a
# single-term repeater below SCORE_THRESHOLD on a multi-term task (verified
# below and in test_coverage.py).
#
# A steeper exponent (2, 3, ...) was tried first and rejected after real-
# repo testing (fastapi): symbol_score has no equivalent cap, so a file
# that legitimately *defines* many symbols named after a single task term
# -- fastapi/dependencies/utils.py, which defines a dozen-plus functions
# with "dependency" in the name, for the task "dependency override not
# applied in nested routers" -- accumulates a large uncapped symbol_score
# from one term. That is exactly the file a developer fixing the bug would
# open, but it does not literally say "override", "nested", or "routers"
# anywhere. At exponent 2 its score dropped from 18.0 to 0.72 (below
# threshold, wrongly excluded); at exponent 1 it drops to 3.60 (still
# comfortably above threshold) while the two confirmed-noise files from
# the TypeScript case above still fall from ~6.0-content-cap to ~1.4
# (still below threshold). Exponent 1 is the value that satisfies both
# real-repo observations at once; a future tuning pass could instead cap
# symbol_score the way CONTENT_CAP already caps content_score, which would
# likely tolerate a steeper exponent without this tension.
#
# Two things this deliberately does NOT touch, both load-bearing:
#
#   1. filename_score is excluded from the coverage multiplier entirely.
#      `lib/navigation.ts` matching only the "navigation" term in its own
#      filename is still exactly the kind of direct, deliberate signal
#      WEIGHT_FILENAME exists to reward -- coverage is a signal about
#      *content* repetition vs. breadth, and a filename match is neither.
#      Scaling it down would re-break the auth_bug fixture's own
#      single-term-per-file filename matches.
#
#   2. Coverage is computed as (distinct matched terms) / (terms in the
#      task), so a single-word task ("applications") always has
#      coverage_ratio == 1.0 for any file that matches at all -- there is
#      only one term to cover. Multi-term tasks are the only ones where
#      the ratio can fall below 1.0. Verified explicitly in
#      test_coverage.py and against a real single-term CLI run.
#
# Transitively-reached files (expand_transitively) never call _analyze at
# all -- their score is the seed's score decayed by IMPORT_DECAY per hop,
# not a fresh lexical analysis. Coverage therefore cannot penalize them:
# dali/scoring/existence.py, reached only via import with zero lexical
# match of its own, is untouched by this change. Verified explicitly in
# test_coverage.py and against ~/Dali.
COVERAGE_EXPONENT = 1.0

# --- transitive import expansion (step 5) ---
# First-party Python import graph, walked in both directions from the seed
# set: files a seed imports, and files that import a seed. Bounded by
# MAX_DEPTH hops, MAX_EXPANDED files total, and per-hop score decay so
# distant, weakly-connected files do not crowd out direct matches.
MAX_DEPTH = 2
IMPORT_DECAY = 0.5
MAX_EXPANDED = 8
# Tuned down from an earlier 40 after step-11 evaluation against a real
# repo (~/Dali): with a permissive cap and SCORE_THRESHOLD = 0.0, a single
# task pulled in 39 files and an 800+ line rendering -- technically all
# above-threshold, but well past what a developer will actually read.
# MAX_SEEDS/MAX_EXPANDED/MAX_INCLUDED together now bound a run to roughly
# the files worth opening first, not everything that scored above zero.
MAX_INCLUDED = 18

# --- bounded excerpt extraction + redaction (step 6) ---
# Excerpts are the spans that justified an item's inclusion, not whole
# files: matched symbol definitions, matched config/doc/content lines, and
# -- for import-reached files -- the specific definitions the importing
# file actually imported. Three bounds keep this a budget rather than a
# second copy of the repo: a per-span line cap, a per-file excerpt-count
# cap, and a package-wide byte cap. Overlapping/adjacent spans are merged
# before any cap is applied. When the package-wide budget is exceeded,
# whole excerpts are dropped lowest-item-score-first and the drop count is
# recorded in trace["excerpts_dropped_over_budget"].
MAX_EXCERPT_LINES = 40
MAX_EXCERPTS_PER_FILE = 3
MAX_PACKAGE_BYTES = 60_000

# --- bug fix: excerpt bounds are line-based only ---
# MAX_EXCERPT_LINES does nothing against a minified file that is a single
# multi-thousand-character line: one "line" can still blow the whole
# package byte budget by itself. MAX_EXCERPT_CHARS caps a single excerpt's
# *text* length directly, independent of how many lines it spans, and is
# comfortably below MAX_PACKAGE_BYTES so no one excerpt can ever exhaust
# the package budget on its own. Over-long text is truncated with a clear
# trailing marker rather than silently cut off mid-content.
MAX_EXCERPT_CHARS = 2_000
TRUNCATION_MARKER = " …[truncated]"

# --- bug fix: minified/generated assets scanned as prose ---
# Content-frequency matching over a minified/generated asset produces
# coincidental "hits" that have nothing to do with the task -- e.g. the SVG
# keyword `userSpaceOnUse` containing "user" as a raw substring. Path and
# filename matching still applies (a file literally named after a task
# term is still relevant); only the content-frequency scoring component
# and fallback content excerpting are skipped for files classified as an
# asset. Kept as a small, comprehensible rule set rather than an
# exhaustive list, per the plan.
ASSET_EXTENSIONS = {".svg", ".map"}
ASSET_FILENAME_SUFFIXES = (".min.js", ".min.css")
ASSET_LOCKFILE_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "composer.lock",
    "gemfile.lock", "cargo.lock", "poetry.lock", "pipfile.lock",
}
ASSET_DIR_SEGMENTS = {"public", "dist", "build", "vendor"}
# A very high average line length is a strong minification signal --
# hand-written prose and source rarely average anywhere near this many
# characters per line, while minified/bundled output routinely does.
ASSET_AVG_LINE_LENGTH = 500

# --- bug fix: framework filename conventions dominate ranking ---
# WEIGHT_FILENAME rewards a term matching any path segment (directory name,
# filename, or filename stem) at full, flat weight regardless of how many
# other files in the *same repo* share that word. That is right for a
# distinctive name (`navigation.ts`, the only file in the repo with that
# word anywhere in its path) and wrong for a framework convention
# (`page.tsx`, one of 14 identically-named route files in a Next.js App
# Router repo): matching "page" carries almost no information about which
# page, yet scored exactly as high as matching a name that appears once.
# Observed on a real Next.js/TypeScript repo: every one of the top 8
# results for "improve landing page conversion rate" was a different
# page.tsx, each included solely because its path contains "page".
#
# FILENAME_TERM_RARITY_WEIGHT scales a matched filename term by how rare
# that specific *word* is across every discovered file's path segments in
# this run -- not a hardcoded list of conventions (page.tsx, index.js,
# __init__.py, route.ts, layout.tsx, mod.rs, ...), which could never cover
# every framework's convention and would not adapt to an unknown one. The
# rarity map is built once per run from whatever the repo actually
# contains (see compute_filename_word_counts()), so it naturally derates
# "page" in a Next.js repo, "mod" in a Rust repo, or any other convention
# neither of us anticipated, while leaving a name that happens to be used
# once completely untouched.
#
# Falloff is 1 / (1 + log(count)) -- smooth and logarithmic rather than a
# hard cutoff, so a word appearing twice (0.59) is not treated anywhere
# near as harshly as one appearing fifty times (0.20), per the plan's
# preference for graduated damping over cliffs. count == 1 (a name unique
# in the repo) always yields exactly 1.0 -- full, unchanged weight -- which
# is what keeps `lib/navigation.ts` (exactly one `navigation.ts` in the
# Next.js fixture repo) ranking #1 for "navigation dropdown is broken on
# mobile" exactly as it did before this fix.
#
# Applied per matched *term*, not per whole file or whole basename: a file
# `user_page.tsx` in a repo full of `page.tsx` still gets full weight for
# matching "user" (a word unique to that one file) even though "page" -- a
# different word that also happens to match -- is damped to near zero. A
# whole-basename comparison would miss this, since "user_page.tsx" as a
# complete filename is itself unique; rarity has to be judged word by word,
# the same granularity filename_terms are already computed at.
# Span weights used only to rank *which* spans survive the per-file and
# package-wide caps -- not exposed on the item itself.
#
# Priority order (step 7): symbol definitions > config key lines > doc
# headings/assertions > everything else. The "everything else" tier
# (WEIGHT_MATCHED_LINE, bare content-frequency lines) is only used as a
# fallback when a file has no spans in any higher tier -- see
# attach_excerpts(). This is what keeps prose/docstring sentences out of
# files that already have a structural match.
WEIGHT_IMPORTED_SYMBOL = 100.0
WEIGHT_MATCHED_SYMBOL = 50.0
WEIGHT_CONFIG_KEY = 40.0
WEIGHT_DOC_ASSERTION = 30.0
WEIGHT_MATCHED_LINE = 1.0

REDACTED = "«redacted»"

# Key names that look like they hold a secret. Matched as a substring of
# the key (case-insensitive), so "api_key", "apikey", "access_key", and
# "private_key" are all covered by "key" without needing to be spelled out
# individually. Deliberately broad: an over-redacted key name is a minor
# annoyance, a leaked credential is not.
#
# "token" is handled separately, not folded into this substring list: on a
# real repo (step 11) it caught "max_tokens" (an LLM parameter, not a
# secret) because "token" is a substring of "tokens". "access_token" /
# "auth_token" / a bare "TOKEN" env var are real secrets and must still be
# caught -- see _looks_like_secret_key() below, which narrows "token" to
# exclude the token-*count* and tokenizer senses of the word rather than
# dropping it entirely.
_SECRET_KEY_SUBSTRINGS = ("key", "secret", "password", "passwd", "credential")

# Compounds where "token" means a token *count* or the tokenizer concept,
# not a credential -- extremely common in LLM-adjacent code. Matched after
# camelCase/snake_case normalization, so "maxTokens" and "max_tokens" both
# resolve the same way.
_NON_SECRET_TOKEN_RE = re.compile(
    r"(?:max|min|num|total|input|output|completion|prompt|context|budget)s?[_-]?tokens?\b"
    r"|tokens?[_-]?(?:count|limit|budget|length)\b"
    r"|tokeniz"
)

# `KEY = value` / `KEY: value` / `KEY=value`, case-insensitive on the key.
_SECRET_KEY_LINE_RE = re.compile(
    r"^(?P<prefix>\s*[A-Za-z_][A-Za-z0-9_.\-]*)(?P<sep>\s*[:=]\s*)(?P<value>.+)$"
)

# Standalone high-entropy shapes, even outside a `key: value` line. The
# prefixed patterns (sk-..., AKIA...) are specific enough to redact
# unconditionally. The generic 32+-char catch-all is not: a long, all-
# letters snake_case identifier -- e.g. a real test name like
# "test_needs_verification_excluded" (32 chars, observed on a real repo
# during step-11 evaluation) -- matches its character class just as well
# as a real secret does, and unconditionally redacting it silently mangles
# ordinary code with no security benefit. Real secrets are near-universally
# a mix of letters *and* digits (base64/hex/JWT output); requiring at
# least one digit keeps the catch-all as a defense-in-depth net for
# unlabeled secrets without also eating long identifiers that happen to be
# made of dictionary-ish words.
_ENTROPY_PREFIXED_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # GitHub's own prefixed token formats (personal access token, OAuth,
    # server-to-server, refresh, fine-grained PAT).
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
]

# JWT: three base64url segments joined by dots (header.payload.signature).
# Dots are not in _ENTROPY_GENERIC_RE's character class (see below), so a
# JWT needs its own shape check -- each segment is fenced off by the dots,
# meaning the generic catch-all would only ever see the three pieces in
# isolation and might not clear its length floor on any one of them.
_JWT_RE = re.compile(
    r"\b[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
)

# PEM key/cert blocks. Body lines are handled by tracking open/close state
# across the whole text in redact_text() (a single _redact_line() call only
# ever sees one line and has no memory of "we're inside a PEM block").
_PEM_BEGIN_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*(?:PRIVATE KEY|CERTIFICATE)-----")
_PEM_END_RE = re.compile(r"-----END [A-Z0-9 ]*(?:PRIVATE KEY|CERTIFICATE)-----")

# No "/" in the generic catch-all's character class: a versioned URL or a
# repo-relative file path (both observed on a real repo during step-11
# evaluation, e.g. "https://dali.yenk.dev/schemas/canonical-citation-v1.json"
# and "data/benchmark/tier1/corpus/citation_failure_cases.json") is a
# multi-segment "/"-joined string that is well over 32 chars and contains
# a digit, so it otherwise passes every other test this pattern applies. A
# real standalone secret is a single contiguous token; it does not need
# "/" to be recognized, and dropping it here removes an entire class of
# false positives on paths and URLs without giving up real coverage.
_ENTROPY_GENERIC_RE = re.compile(r"\b[A-Za-z0-9+_-]{32,}={0,2}\b")


def _redact_generic_entropy(match: re.Match) -> str:
    token = match.group(0)
    return REDACTED if any(c.isdigit() for c in token) else token

STOPWORDS = {
    "fix", "bug", "the", "a", "an", "in", "to", "for", "of", "and",
    "issue", "error", "problem", "add", "update", "make", "when", "with",
    "is", "are", "was", "were", "be", "been", "being", "on", "at", "by",
    "this", "that", "these", "those", "it", "its", "as", "or", "not",
    "please", "need", "needs", "can", "should", "would", "could", "will",
    "file", "files", "code", "does", "doesn", "why", "how", "what",
    "into", "from", "there", "here", "some", "any", "all", "new", "old",
    # Generic task verbs. A bug report's verb ("improve", "refactor",
    # "investigate") describes the intent, never the code -- it matches
    # incidental prose and dilutes the coverage signal that ranking and
    # the weak-match warning both depend on. "fix"/"add"/"update" were
    # already here; these are the rest of the same family.
    "improve", "refactor", "implement", "handle", "support", "remove",
    "change", "create", "build", "optimize", "cleanup", "investigate",
    "debug", "broken", "wrong", "fails", "failing", "failed", "better",
    "properly", "correctly", "instead", "still", "also", "then",
}

# --------------------------------------------------------------------------


def _split_camel_and_snake(text: str) -> str:
    """Insert spaces at camelCase and snake_case boundaries so both split
    into separate tokens on the later non-alnum split.
    """
    # lower/digit -> Upper boundary: "authBug" -> "auth Bug"
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    # Upper Upper -> Upper lower boundary: "HTTPServer" -> "HTTP Server"
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    return text


def tokenize(task: str) -> list[str]:
    """lowercase, split on non-alphanumerics and camelCase/snake_case
    boundaries, drop stopwords, drop tokens under MIN_TOKEN_LEN chars.
    """
    text = _split_camel_and_snake(task)
    text = text.lower()
    raw_tokens = re.split(r"[^a-z0-9]+", text)
    tokens = [
        t for t in raw_tokens
        if t and len(t) >= MIN_TOKEN_LEN and t not in STOPWORDS
    ]
    return tokens


def _path_segments(path: str) -> list[str]:
    """Path segments (original case preserved -- see _iter_word_matches,
    which needs case to tell a camelCase hump from a bare substring),
    including the filename stem split off from its extension, for
    filename/path-segment matching.
    """
    parts = re.split(r"[\\/]", path)
    segments: list[str] = []
    for part in parts:
        segments.append(part)
        stem = re.sub(r"\.[A-Za-z0-9]+$", "", part)
        if stem and stem != part:
            segments.append(stem)
    return segments


# --------------------------------------------------------------------------
# Bug fix: word-boundary term matching.
#
# Plain substring matching (`term in haystack`) matched "user" inside
# "userSpaceOnUse" (an SVG coordinate-system keyword) and let a single
# incidental "user experience" mention in prose outrank a genuinely
# relevant file. This is bounded matching instead: a term is a real word
# match only when it sits at a real boundary on *both* sides -- either a
# non-identifier character (matching the non-alnum split tokenize() already
# uses for the task side), or a camelCase hump (the same lower-followed-by-
# upper transition _split_camel_and_snake() inserts a space at).
#
# The camelCase side is deliberately one-directional: a hump is only
# accepted as a boundary when the matched text itself starts with an
# uppercase letter (i.e. the match is a properly-capitalized word segment,
# as in "getUser" or "UserSession"). A lowercase term sitting at the front
# of a longer lowerCamelCase identifier with no real delimiter --
# "userSpaceOnUse" is "user" + "Space" + "On" + "Use" by the same splitting
# rule -- does NOT get a free pass just because a capital happens to follow
# it; that leading lowercase run is exactly the ambiguous, false-positive-
# prone shape this fix targets. "user_id" and "UserSession" still match
# because they have (respectively) an explicit delimiter and a
# capitalized, hump-bounded match.
# --------------------------------------------------------------------------


def _iter_word_matches(text: str, term: str):
    """Yield the start indices in `text` where `term` occurs as a genuine
    word -- respecting identifier boundaries (camelCase humps and
    snake_case/other delimiters) rather than as an arbitrary substring.
    Matching is case-insensitive; boundary decisions use the original
    (unlowered) text so a camelCase hump can be detected.
    """
    if not term:
        return
    text_lower = text.lower()
    term_lower = term.lower()
    tlen = len(term_lower)
    n = len(text)
    pos = 0
    while True:
        idx = text_lower.find(term_lower, pos)
        if idx == -1:
            return
        pos = idx + 1
        end = idx + tlen
        matched_is_upper_start = text[idx].isupper()
        before = text[idx - 1] if idx > 0 else ""
        after = text[end] if end < n else ""

        left_ok = (
            idx == 0
            or not before.isalnum()
            or ((before.islower() or before.isdigit()) and matched_is_upper_start)
        )
        if not left_ok:
            continue

        right_ok = end == n or not after.isalnum()
        if not right_ok and matched_is_upper_start and after.isupper():
            right_ok = True

        if right_ok:
            yield idx


def has_word_match(text: str, term: str) -> bool:
    """True if `term` occurs anywhere in `text` as a genuine word."""
    for _ in _iter_word_matches(text, term):
        return True
    return False


def count_word_matches(text: str, term: str) -> int:
    """Count of genuine word occurrences of `term` in `text`."""
    return sum(1 for _ in _iter_word_matches(text, term))


def _is_asset_like(path: str, content: str) -> bool:
    """True when `path`/`content` looks like a minified, generated, or
    vendored asset rather than hand-written prose or source -- see the
    ASSET_* tunables above. Used only to disable content-frequency
    matching and the generic content-line excerpt fallback; path/filename
    matching is unaffected.
    """
    name = PurePosixPath(path).name
    lower_name = name.lower()
    if lower_name in ASSET_LOCKFILE_NAMES:
        return True
    ext = PurePosixPath(lower_name).suffix
    if ext in ASSET_EXTENSIONS:
        return True
    if lower_name.endswith(ASSET_FILENAME_SUFFIXES):
        return True
    dir_parts = path.lower().split("/")[:-1]
    if any(part in ASSET_DIR_SEGMENTS for part in dir_parts):
        return True
    if content:
        lines = content.splitlines() or [content]
        avg_len = sum(len(line) for line in lines) / len(lines)
        if avg_len > ASSET_AVG_LINE_LENGTH:
            return True
    return False


def _segment_words(segment: str) -> set[str]:
    """The distinct lowercased words inside one path segment, split the
    same way tokenize() splits task text (camelCase/snake_case boundaries,
    then non-alphanumeric runs) -- so a word extracted here lines up
    exactly with the terms filename matching already compares against.
    Unlike tokenize(), stopwords and short tokens are kept: a task term is
    never a stopword or under MIN_TOKEN_LEN to begin with (tokenize()
    already filtered those out before matching ever happens), so there is
    nothing to gain by filtering the rarity map's keys the same way, and
    doing so would only risk silently dropping a legitimate lookup.
    """
    text = _split_camel_and_snake(segment).lower()
    return {w for w in re.split(r"[^a-z0-9]+", text) if w}


def compute_filename_word_counts(discovered: list[DiscoveredFile]) -> dict[str, int]:
    """word -> number of distinct discovered files whose path contains
    that word in any path segment (directory name, filename, or filename
    stem -- the exact same universe _path_segments() draws filename_terms
    from). This is the rarity map behind the fix above: cheap to build (one
    pass over paths already in memory from discover(), no extra file
    reads), and derived entirely from the repo being scanned rather than
    any hardcoded list of framework conventions.
    """
    counts: dict[str, int] = {}
    for discovered_file in discovered:
        words: set[str] = set()
        for segment in _path_segments(discovered_file.path):
            words |= _segment_words(segment)
        for word in words:
            counts[word] = counts.get(word, 0) + 1
    return counts


def _filename_term_weight(term: str, word_counts: dict[str, int] | None) -> float:
    """Rarity weight for one matched filename term -- see the
    FILENAME_TERM_RARITY comment block above WEIGHT_IMPORTED_SYMBOL.
    `word_counts` is None for standalone callers (e.g. tests calling
    score_file() directly on a single file with no repo-wide view); that
    is treated the same as "this word is unique," i.e. full weight,
    which is also exactly the pre-fix behavior such callers already
    expect.
    """
    if not word_counts:
        return 1.0
    count = word_counts.get(term.lower(), 1)
    if count <= 1:
        return 1.0
    return 1.0 / (1.0 + math.log(count))


def _build_reason(
    role: str,
    filename_terms: list[str],
    symbol_names: list[str],
    content_term_counts: dict[str, int],
) -> str:
    """Turn the raw signals that made a file score above threshold into a
    short, human sentence fragment -- not a telemetry dump.

    Leads with the strongest signal (matching the score weighting:
    filename > symbol > content), never concatenates multiple signals with
    semicolons, never surfaces a raw score or occurrence count, and never
    emits a bare "matched".
    """
    if filename_terms:
        return f"filename matches '{filename_terms[0]}'"

    if symbol_names:
        return f"defines {symbol_names[0]}"

    if content_term_counts:
        # Strongest content signal: the term mentioned most often.
        term = max(content_term_counts, key=lambda t: content_term_counts[t])
        if role == "docs":
            return f"defines {term} requirements"
        if role == "config":
            return f"configuration referenced by {term} code"
        if role == "test":
            return "tests for affected functionality"
        if role == "source":
            return f"references {term}"
        return f"mentions {term}"

    return "matches task terms"


def _large_file_content_penalty(
    discovered_file: DiscoveredFile,
    content: str,
    is_asset: bool,
    cache: RunCache,
) -> float:
    """Multiplier for `content_score` on a file over LARGE_FILE_BYTES --
    see the tunables block above LARGE_FILE_CONTENT_PENALTY for the full
    rationale. Judges whether the file looks like *data* or like *source*
    before deciding how hard to damp it, instead of penalizing every large
    file identically.
    """
    if is_asset:
        # content_score is already 0 for an asset-like file (see
        # _analyze's is_asset branch below) -- the multiplier is moot, but
        # returning the full penalty keeps this function's contract
        # ("data-like gets the full penalty") consistent regardless of
        # call order.
        return LARGE_FILE_CONTENT_PENALTY

    ext = PurePosixPath(discovered_file.path).suffix.lower()

    if ext == ".py":
        record = cache.get_record(discovered_file)
        if record.parse_ok:
            density = len(record.defs) / (discovered_file.size / 1000)
            if density >= LARGE_PY_MIN_SYMBOL_DENSITY:
                return LARGE_SOURCE_CONTENT_PENALTY
        return LARGE_FILE_CONTENT_PENALTY

    if ext in LARGE_FILE_DATA_EXTENSIONS:
        return LARGE_FILE_CONTENT_PENALTY

    if ext in LARGE_WEAK_SOURCE_EXTENSIONS and content:
        lines = content.splitlines() or [content]
        avg_len = sum(len(line) for line in lines) / len(lines)
        if avg_len <= LARGE_WEAK_SOURCE_AVG_LINE_LENGTH:
            return LARGE_WEAK_SOURCE_CONTENT_PENALTY

    return LARGE_FILE_CONTENT_PENALTY


def _in_data_path(path: str) -> bool:
    """True when `path` is under a recognizable data/vendor/build
    directory (see discovery.DATA_DIR_SEGMENTS) -- used only to apply
    DATA_PATH_PENALTY, never to exclude the file outright.
    """
    dir_parts = path.lower().split("/")[:-1]
    return any(part in DATA_DIR_SEGMENTS for part in dir_parts)


def _looks_generated(content: str) -> bool:
    head = content[:_GENERATED_SNIFF_CHARS].lower()
    return any(marker in head for marker in _GENERATED_MARKERS)


def _in_example_path(path: str) -> bool:
    """True when `path` is under a recognizable example/tutorial/sample
    directory (EXAMPLE_DIR_SEGMENTS) -- used only to apply
    EXAMPLE_PATH_PENALTY, never to exclude the file outright. `path` is
    root-relative (DiscoveredFile.path always is), so this reflects the
    directory structure relative to whatever root was given, not an
    absolute path -- see EXAMPLE_PATH_PENALTY's note above.
    """
    dir_parts = path.lower().split("/")[:-1]
    return any(part in EXAMPLE_DIR_SEGMENTS for part in dir_parts)


def _analyze(
    discovered_file: DiscoveredFile,
    terms: list[str],
    cache: RunCache | None = None,
    filename_word_counts: dict[str, int] | None = None,
) -> tuple[float, str, frozenset[str]]:
    """Score a single discovered file against `terms`, returning
    (score, reason, distinct_matched_terms).

    `distinct_matched_terms` is the same set _analyze already builds
    internally to compute coverage_ratio (filename_terms | symbol_terms |
    content_term_counts) -- returned here too so callers (weak-signal
    detection in select()) can see *which* terms this file actually
    corroborated without re-deriving it from `reason`, which only ever
    names the single strongest signal and is lossy for that purpose.

    `cache`, when given, memoizes this file's content and parsed AST facts
    across the whole run so scoring, import-graph construction, and excerpt
    extraction do not each independently read and parse the same file --
    see filecache.RunCache. Optional (defaults to a throwaway per-call
    cache) so this stays callable standalone, e.g. from tests.

    `filename_word_counts` (see compute_filename_word_counts()), when
    given, is the whole run's basename-rarity map, used to damp a filename
    match that is a repo-wide naming convention rather than a distinctive
    name -- see the comment block above WEIGHT_IMPORTED_SYMBOL. Optional
    for the same standalone-caller reason as `cache`; a missing map is
    treated as "every matched word is unique," i.e. no damping.
    """
    cache = cache if cache is not None else RunCache()
    filename_score = 0.0
    symbol_score = 0.0
    content_score = 0.0
    role_bonus = 0.0
    matched_any = False

    # --- filename / path segment match: highest-weight signal ---
    segments = _path_segments(discovered_file.path)
    filename_terms = [t for t in terms if any(has_word_match(seg, t) for seg in segments)]
    if filename_terms:
        filename_score += WEIGHT_FILENAME * sum(
            _filename_term_weight(t, filename_word_counts) for t in filename_terms
        )
        matched_any = True

    content = cache.get_content(discovered_file)

    is_asset = _is_asset_like(discovered_file.path, content)

    # --- Python symbol definitions ---
    # Deliberately a full-tree walk (via cache.get_record, not a top-level-
    # only scan): a matching def/class can be nested inside a function,
    # method, or another class, and those nested definitions are genuine
    # signal (e.g. a class's own `def is_expired` method) that a top-level-
    # only scan would silently miss.
    symbol_names: list[str] = []
    symbol_terms: set[str] = set()
    if discovered_file.path.endswith(".py") and content:
        record = cache.get_record(discovered_file)
        for node in record.defs:
            node_matched = False
            for t in terms:
                if has_word_match(node.name, t):
                    symbol_score += WEIGHT_SYMBOL
                    matched_any = True
                    node_matched = True
                    symbol_terms.add(t)
            if node_matched:
                symbol_names.append(node.name)

    # --- damped content term frequency ---
    # Skipped for minified/generated/vendored assets (is_asset): a bare
    # word-boundary match there is still not a meaningful "mention" -- the
    # content is not prose or source a developer would read, and a single
    # multi-thousand-character line can otherwise dominate scoring on
    # coincidence alone.
    content_term_counts: dict[str, int] = {}
    if content and not is_asset:
        for t in terms:
            count = count_word_matches(content, t)
            if count > 0:
                content_score += min(CONTENT_CAP, CONTENT_MULT * math.log(1 + count))
                matched_any = True
                content_term_counts[t] = count

    # A large *data* file mentioning a term hundreds of times is not
    # hundreds of times more relevant than a small file mentioning it
    # once -- only the content-frequency component is damped; filename and
    # symbol matches (rarer, more deliberate signals) are unaffected by
    # file size either way. A large *source* file gets little or no
    # damping here -- see _large_file_content_penalty() and the tunables
    # block above LARGE_FILE_CONTENT_PENALTY.
    if discovered_file.size > LARGE_FILE_BYTES:
        content_score *= _large_file_content_penalty(discovered_file, content, is_asset, cache)

    # Test files describe a feature many times over, once per test case --
    # see TEST_SIGNAL_DAMPING above. Damp symbol and content score (the
    # repetition-sensitive components) for test-role files only; a
    # filename match is left at full weight.
    if discovered_file.role == "test":
        symbol_score *= TEST_SIGNAL_DAMPING
        content_score *= TEST_SIGNAL_DAMPING

    # --- role bonus ---
    if matched_any and discovered_file.role in ROLE_BONUS_ROLES:
        role_bonus = ROLE_BONUS

    # --- distinct-term coverage ---
    # See COVERAGE_EXPONENT above for the full rationale. Scaled here are
    # only symbol_score and content_score -- the two components that scale
    # with how many times a file *mentions* a term, the same repetition-
    # sensitive pair TEST_SIGNAL_DAMPING already singles out above.
    # filename_score is left untouched for the reasons given there.
    # role_bonus is also left untouched: it is a small, flat nudge for a
    # config/docs file that matched *anything*, not a repetition artifact,
    # and scaling it down punished the legitimate case of a short config
    # file (e.g. a two-line .env) that has only one task term to match in
    # the first place -- observed while adding this fix, against
    # test_env_secret_never_appears_anywhere_in_serialized_package.
    distinct_matched_terms = set(filename_terms) | symbol_terms | set(content_term_counts)
    coverage_ratio = (len(distinct_matched_terms) / len(terms)) if terms else 1.0
    coverage_factor = coverage_ratio ** COVERAGE_EXPONENT
    symbol_score *= coverage_factor
    content_score *= coverage_factor

    score = filename_score + symbol_score + content_score + role_bonus

    # Bulk data / vendored / build / generated files are down-weighted as a
    # whole, on top of the content-frequency damping above -- a strong
    # filename match inside a data dump is still probably not the file a
    # developer wants opened first.
    if _in_data_path(discovered_file.path):
        score *= DATA_PATH_PENALTY
    if content and _looks_generated(content):
        score *= GENERATED_FILE_PENALTY
    # Tutorial/example scripts are written in feature vocabulary but are
    # not the implementation -- see EXAMPLE_PATH_PENALTY above.
    if _in_example_path(discovered_file.path):
        score *= EXAMPLE_PATH_PENALTY

    reason = _build_reason(discovered_file.role, filename_terms, symbol_names, content_term_counts)
    if discovered_file.duplicate_count > 0:
        # Never let a collapsed duplicate be silent -- see
        # discovery._dedupe_by_content(). The dropped count is also
        # recorded in excluded_by_reason["duplicate"]; this is the
        # per-item explanation of *why* this representative was kept.
        copy_word = "copy" if discovered_file.duplicate_count == 1 else "copies"
        reason = f"{reason} ({discovered_file.duplicate_count} duplicate {copy_word} collapsed)"
    return score, reason, frozenset(distinct_matched_terms)


def _module_parts(rel_path: str) -> tuple[tuple[str, ...], bool]:
    """Return (dotted-module-parts, is_package_init) for a first-party
    Python file's root-relative path.

    `src/users/session.py` -> (("src", "users", "session"), False)
    `src/users/__init__.py` -> (("src", "users"), True)
    """
    pure = PurePosixPath(rel_path)
    stem = pure.stem
    if stem == "__init__":
        return tuple(pure.parent.parts), True
    return (*pure.parent.parts, stem), False


def _resolve_module_tuple(module_tuple: tuple[str, ...], file_index: set[str]) -> str | None:
    """Resolve a dotted-module-parts tuple to a first-party file path, or
    None if it does not map to any discovered Python file (i.e. it is
    stdlib, third-party, or simply not part of this project).
    """
    if not module_tuple:
        return None
    base = "/".join(module_tuple)
    module_candidate = base + ".py"
    package_candidate = base + "/__init__.py"
    if module_candidate in file_index:
        return module_candidate
    if package_candidate in file_index:
        return package_candidate
    return None


def _imports_of(rel_path: str, import_nodes: list, file_index: set[str]) -> set[str]:
    """Return the set of first-party file paths `rel_path` imports, given
    the Import/ImportFrom AST nodes already collected for it (see
    filecache.RunCache -- a full-tree walk, since an import can legally
    appear nested inside a function/conditional, not just at module level).
    Third-party/stdlib imports (those that do not resolve to a file under
    file_index) are silently skipped. A file that failed to parse simply
    has no import nodes, so it yields no imports here -- same end result
    as the previous per-call ast.parse(), without re-parsing.
    """
    module_parts, is_init = _module_parts(rel_path)
    base_package = list(module_parts) if is_init else list(module_parts[:-1])

    targets: set[str] = set()

    for node in import_nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod_tuple = tuple(alias.name.split("."))
                resolved = _resolve_module_tuple(mod_tuple, file_index)
                if resolved and resolved != rel_path:
                    targets.add(resolved)

        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # level 1 ("from . import x" / "from .x import y") means
                # "relative to the current package"; each extra level
                # walks one package up from there.
                up = node.level - 1
                if up > len(base_package):
                    continue
                target_package = base_package[: len(base_package) - up]
            else:
                target_package = []

            if node.module:
                target_prefix = tuple(target_package) + tuple(node.module.split("."))
            else:
                target_prefix = tuple(target_package)

            resolved = _resolve_module_tuple(target_prefix, file_index)
            if resolved and resolved != rel_path:
                targets.add(resolved)

            # The imported name may itself be a submodule rather than an
            # attribute of the resolved module (e.g. "from src.users
            # import session" where session.py is a module) -- try that
            # too.
            for alias in node.names:
                if alias.name == "*":
                    continue
                sub_resolved = _resolve_module_tuple(target_prefix + (alias.name,), file_index)
                if sub_resolved and sub_resolved != rel_path:
                    targets.add(sub_resolved)

    return targets


def _build_import_graph(
    discovered: list[DiscoveredFile], cache: RunCache | None = None
) -> tuple[dict[str, DiscoveredFile], dict[str, set[str]], dict[str, set[str]]]:
    """Build the first-party Python import graph from `discovered`.

    Returns (py_files, outbound, inbound):
      - py_files: rel path -> DiscoveredFile, for every discovered .py file
      - outbound: rel path -> set of rel paths it imports
      - inbound: rel path -> set of rel paths that import it

    `cache` (see filecache.RunCache) memoizes each file's content and
    parsed import nodes so this does not re-read/re-parse a file that
    scoring (_analyze) or excerpt extraction already parsed this run.
    """
    cache = cache if cache is not None else RunCache()
    py_files = {f.path: f for f in discovered if f.path.endswith(".py")}
    file_index = set(py_files)

    outbound: dict[str, set[str]] = {}
    for path, discovered_file in py_files.items():
        content = cache.get_content(discovered_file)
        if content:
            import_nodes = cache.get_record(discovered_file).imports
            outbound[path] = _imports_of(path, import_nodes, file_index)
        else:
            outbound[path] = set()

    inbound: dict[str, set[str]] = {path: set() for path in py_files}
    for source, targets in outbound.items():
        for target in targets:
            inbound.setdefault(target, set()).add(source)

    return py_files, outbound, inbound


def expand_transitively(
    seed_items: list[ContextItem], discovered: list[DiscoveredFile], cache: RunCache | None = None
) -> tuple[list[ContextItem], int]:
    """Expand `seed_items` across the first-party Python import graph, both
    directions (files a seed imports, and files that import a seed).

    Bounded by MAX_DEPTH hops and MAX_EXPANDED files, with score decayed
    IMPORT_DECAY per hop. Cycle-safe: a `visited` set (seeds plus anything
    already expanded) is checked before a file is ever added as a
    candidate, so a cycle in the import graph simply stops contributing
    new files rather than looping.

    Returns (expanded ContextItems, count of candidates dropped for being
    over MAX_EXPANDED).
    """
    py_files, outbound, inbound = _build_import_graph(discovered, cache)

    seed_paths = {item.path for item in seed_items}
    visited = set(seed_paths)

    # Only .py seeds participate in the import graph.
    frontier: list[tuple[str, float, list[str]]] = [
        (item.path, item.score, []) for item in seed_items if item.path in py_files
    ]

    expanded: dict[str, tuple[float, list[str], str]] = {}
    over_cap_count = 0
    depth = 1

    while depth <= MAX_DEPTH and frontier:
        candidates: dict[str, tuple[float, list[str], str]] = {}

        for path, score, provenance in frontier:
            decayed = score * IMPORT_DECAY
            friendly = PurePosixPath(path).name

            for target in outbound.get(path, ()):
                if target in visited or target in candidates:
                    continue
                edge = f"{path} imports {target}"
                candidates[target] = (decayed, provenance + [edge], f"imported by {friendly}")

            for source in inbound.get(path, ()):
                if source in visited or source in candidates:
                    continue
                edge = f"{source} imports {path}"
                candidates[source] = (decayed, provenance + [edge], f"imports {friendly}")

        if not candidates:
            break

        # Deterministic order: strongest (least-decayed) candidates first,
        # tie-broken on path, so the MAX_EXPANDED cap drops the weakest
        # candidates first rather than arbitrarily.
        ordered = sorted(candidates.items(), key=lambda kv: (-kv[1][0], kv[0]))

        next_frontier: list[tuple[str, float, list[str]]] = []
        for path, (score, provenance, reason) in ordered:
            if len(expanded) >= MAX_EXPANDED:
                over_cap_count += 1
                continue
            expanded[path] = (score, provenance, reason)
            visited.add(path)
            next_frontier.append((path, score, provenance))

        frontier = next_frontier
        depth += 1

    items = [
        ContextItem(
            path=path,
            role=py_files[path].role,
            reason=reason,
            score=score,
            provenance=provenance,
        )
        for path, (score, provenance, reason) in expanded.items()
    ]
    return items, over_cap_count


def _looks_like_secret_key(key: str) -> bool:
    """Name-only judgment: does this identifier *look* like it names a
    secret? Used two ways elsewhere: (1) here, as one weak signal feeding
    the value-shape decision below -- never sufficient by itself; (2) in
    checks.py, to decide whether a config key is sensitive enough that a
    `configuration_discrepancy` finding should suppress it entirely
    (keeping that a name-only judgment is intentional there -- it must
    still refuse to *report* a discrepancy over a plausibly-secret key
    even when the "value" is a duration/port/etc. that isn't found by the
    value-shape rules below).
    """
    key_lower = _split_camel_and_snake(key.strip()).lower()
    if any(sub in key_lower for sub in _SECRET_KEY_SUBSTRINGS):
        return True
    if "token" in key_lower:
        return not _NON_SECRET_TOKEN_RE.search(key_lower)
    return False


# --- bug fix: redaction judged the variable name, never the value ---
#
# `_looks_like_secret_key()` alone used to be sufficient to mask an entire
# `key = value` line. That does not converge: `key`, `secret`, and `token`
# are among the most common identifiers in ordinary code, so on a real
# repo it masked `token = token[CSRF_SECRET_LENGTH:]`, `key =
# sorted(d.keys())[0]`, `secret = compute_secret(user, salt)`,
# `password_field = form.fields["password"]`, `api_key_header =
# request.headers.get("X-Api-Key")`, and `self.private_key_path =
# Path(cfg.dir) / "id_rsa"` -- none of which hold a credential; all of
# which are an expression, a call, a subscript, or an attribute access.
# An earlier version of this fix tried to tell those apart from a real
# scalar using only value *punctuation* (does it contain `()[]{}`?), with
# no notion of which file the line came from. Sweeping that version
# against a real repo (django) showed it isn't enough: punctuation-only
# can't tell a bare *scalar* (`hunter2verylongpasswordvalue`, the shape a
# real `.env`/YAML value takes) from a bare *identifier reference*
# (`session_key = self.session.session_key`, `_csrf_id_token =
# MASKED_TEST_SECRET2`, `post_token=None`, `self.token_type = token_type`)
# -- neither contains any punctuation, so both are indistinguishable by
# shape alone. The two need different defaults, and that default is
# exactly what the file's role already tells us.
#
# The fix judges the *value*, not the name -- and, for a bare/unquoted
# value, judges it differently depending on whether the line came from a
# config-role file (`.env`/YAML/INI/JSON/TOML -- `is_config=True`) or a
# code file (`is_config=False`, the default):
#
#   - **config file, any value shape**: a suspicious key name is enough to
#     redact the whole value outright, exactly as before. This is where
#     real credentials actually live and it must stay conservative --
#     `configuration_discrepancy` also depends on key names (not values)
#     surviving redaction.
#   - **code file**: a suspicious key name only matters once the value is
#     a plain quoted string literal (`_extract_string_literal` below) --
#     something that really could be a hardcoded secret. A bare
#     identifier, dotted attribute path, keyword (`None`/`True`/`False`),
#     number, or any call/subscript/attribute-access expression is never
#     redacted on name alone, no matter how suspicious the name looks.
#     This is what leaves all six BAD lines above untouched while still
#     catching `SECRET_KEY = "django-insecure-..."` and `api_key =
#     "sk-proj-..."`.
#
# The quoted-literal rule for code files is deliberately a recall-over-
# precision trade in the *other* direction: `password = "hunter2"` still
# redacts even though "hunter2" clears no entropy/length bar on its own --
# leak risk is concentrated in "a literal string assigned to a
# suspicious-looking name" and the readability cost of occasionally
# over-redacting a short literal is near zero.
#
# High-entropy / known-shape values (sk-..., AKIA..., gh*_..., JWTs, PEM
# blocks, long base64/hex runs) are redacted independently of the key name
# -- and of is_config -- entirely separately, below. See
# _ENTROPY_PREFIXED_PATTERNS / _ENTROPY_GENERIC_RE / _JWT_RE / the PEM
# handling in redact_text().
_STRING_LITERAL_RE = re.compile(
    r"""^(?:[rRbBfFuU]{1,2})?(?P<q>'''|\"\"\"|'|")(?P<body>.*)(?P=q)$""",
    re.DOTALL,
)


def _extract_string_literal(value: str) -> str | None:
    """If `value` (whitespace-trimmed) is, in its entirety, a single
    quoted string literal -- optionally prefixed with a Python string
    flag (r/b/f/u, any case/combo) -- return its inner text. Otherwise
    (an expression, a call, a bare identifier, a number, a keyword, or
    anything else that isn't just "a string") return None.
    """
    match = _STRING_LITERAL_RE.match(value.strip())
    return match.group("body") if match else None


def _should_redact_by_name(key: str, value: str, is_config: bool) -> bool:
    """Whether a `key <sep> value` line's value should be masked on the
    strength of the key name alone -- see the comment block above for the
    config-vs-code rationale.
    """
    if not _looks_like_secret_key(key):
        return False
    value = value.strip()
    if not value:
        return False
    if is_config:
        return True
    return _extract_string_literal(value) is not None


# bug fix, take three -- found sweeping this fix against a real repo
# (sqlfluff): the caller originally passed `is_config=(discovered_file.role
# == "config")`, but `role == "config"` is a much broader signal than "this
# is a config-*format* file" -- discovery._classify_role() also assigns
# "config" to any *.py file that merely lives under a directory literally
# named "config" (e.g. sqlfluff's src/sqlfluff/core/config/validate.py,
# ordinary Python). That resurrected exactly the bug this fix addresses,
# just gated on directory name instead of variable name:
# `non_type_keys = set(layout_section.keys()) - {"type"}` was masked
# outright again because its file's *role* was "config" even though the
# line is Python code. `is_config` must track config *syntax* (would this
# line plausibly be a bare `.env`/YAML/INI/JSON scalar?), which is a
# narrower, extension-only question -- deliberately not reusing `role`.
def _is_config_format_file(path: str) -> bool:
    name = PurePosixPath(path).name
    ext = PurePosixPath(name).suffix.lower()
    if not ext and name.lower() in CONFIG_EXTENSIONS:
        # Mirrors discovery._classify_role()'s dotfile fallback: ".env" has
        # no pathlib suffix (a leading-dot-only name), so fall back to the
        # whole lowered filename.
        ext = name.lower()
    return ext in CONFIG_EXTENSIONS


def _redact_line(line: str, is_config: bool = False) -> str:
    """Mask the value on a secret-looking `key: value` / `key = value`
    line (see `_should_redact_by_name`), and mask any standalone
    high-entropy or known-credential-shaped token anywhere in the line
    (e.g. an `sk-...`/`AKIA...`/`ghp_...` key, a JWT, or a bare base64/hex
    secret that isn't behind a recognizable key at all) regardless of
    `is_config`. Key names and line structure survive; only
    sensitive-looking value text is replaced.
    """
    match = _SECRET_KEY_LINE_RE.match(line)
    if match and _should_redact_by_name(match.group("prefix"), match.group("value"), is_config):
        line = line[: match.start("value")] + REDACTED + line[match.end("value") :]

    for pattern in _ENTROPY_PREFIXED_PATTERNS:
        line = pattern.sub(REDACTED, line)
    line = _JWT_RE.sub(REDACTED, line)
    line = _ENTROPY_GENERIC_RE.sub(_redact_generic_entropy, line)

    return line


def redact_text(text: str, is_config: bool = False) -> str:
    """Apply `_redact_line` to every line of `text`, preserving line
    count and structure. This is the only place excerpt text is allowed
    to reach a ContextItem without going through redaction first.

    `is_config` should be True when `text` comes from a config-role file
    (`.env`/YAML/INI/JSON/TOML) -- see the comment block above
    `_should_redact_by_name` for what that changes. It defaults to False
    (code-file rules) since that is the conservative choice for a caller
    that doesn't know: a code-mode false negative can still be caught by
    the shape-based scans below, while config-mode's name-only masking
    applied to code would resurrect the original bug.

    Also tracks PEM `-----BEGIN ... PRIVATE KEY-----` / `...CERTIFICATE-----`
    block state across lines (a per-line shape check can't see this on its
    own): every line strictly between a BEGIN and its matching END marker
    is treated as key material and replaced outright, since PEM body lines
    are base64 but are not reliably long/mixed enough per *line* (bodies
    are typically wrapped at 64-76 chars) to always clear the standalone
    entropy bar on their own.
    """
    out_lines = []
    in_pem_block = False
    for line in text.splitlines():
        if in_pem_block:
            if _PEM_END_RE.search(line):
                in_pem_block = False
                out_lines.append(line)
            else:
                out_lines.append(REDACTED)
            continue
        if _PEM_BEGIN_RE.search(line):
            in_pem_block = True
            out_lines.append(line)
            continue
        out_lines.append(_redact_line(line, is_config=is_config))
    return "\n".join(out_lines)


def _def_span(node: ast.AST) -> tuple[int, int]:
    """(start_line, end_line), 1-indexed inclusive, for a def/class node,
    capped at MAX_EXCERPT_LINES so a single huge definition cannot blow
    the per-span budget on its own.
    """
    start = node.lineno
    end = getattr(node, "end_lineno", None) or start
    end = min(end, start + MAX_EXCERPT_LINES - 1)
    return start, end


def _matched_symbol_spans(defs: list, terms: list[str]) -> list[tuple[int, int]]:
    """Spans for def/class nodes whose name matches a task term -- the
    same signal _analyze() uses to score a file, but here we need the
    actual line range instead of just a boolean/score contribution.

    `defs` is the file's cached list of FunctionDef/AsyncFunctionDef/
    ClassDef AST nodes (see filecache.RunCache) -- already the product of
    a full-tree walk, so a nested match (a method inside a class) is
    included exactly as it was when this parsed the file itself.
    """
    spans = []
    for node in defs:
        if any(has_word_match(node.name, t) for t in terms):
            spans.append(_def_span(node))
    return spans


def _imported_symbol_spans(defs: list, names: set[str]) -> list[tuple[int, int]]:
    """Spans for def/class nodes in `defs` (see _matched_symbol_spans)
    whose name is in `names` -- the definitions actually imported by
    another file, for import-reached items. This is what makes the
    transitive result usable: session.py's excerpt is SessionStore, not an
    arbitrary head-of-file slice.
    """
    if not names:
        return []
    spans = []
    for node in defs:
        if node.name in names:
            spans.append(_def_span(node))
    return spans


def _matched_line_spans(content: str, terms: list[str]) -> list[tuple[int, int]]:
    """One-line spans for every line containing a task term. This is the
    lowest-priority, "everything else" tier -- used only as a fallback
    when a file has no config-key, doc-heading, or symbol-definition spans
    (see attach_excerpts()), so it does not pull in arbitrary prose lines
    from a file that already has a structural match.
    """
    spans = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        if any(has_word_match(line, t) for t in terms):
            spans.append((lineno, lineno))
    return spans


_CONFIG_KEY_LINE_RE = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_.\-]*\s*[:=]")


def _config_key_spans(content: str, terms: list[str]) -> list[tuple[int, int]]:
    """One-line spans for `key: value` / `key = value` lines whose key or
    value carries a task term -- the specific config key lines, not
    arbitrary comment prose. Comment lines are skipped even if they
    happen to contain a term (that case falls through to the generic
    fallback tier instead).
    """
    spans = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not _CONFIG_KEY_LINE_RE.match(stripped):
            continue
        if any(has_word_match(line, t) for t in terms):
            spans.append((lineno, lineno))
    return spans


def _doc_heading_and_assertion_spans(content: str, terms: list[str]) -> list[tuple[int, int]]:
    """One-line spans for a doc heading (`#`/`##`/...) that carries a task
    term, plus every non-blank line under it until the next heading -- the
    assertion lines the heading is introducing. A heading that does not
    match a term contributes nothing, even if a line under it happens to
    mention one (that line is still reachable via the generic fallback
    tier if nothing better exists in the file).
    """
    spans = []
    under_matching_heading = False
    for lineno, line in enumerate(content.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            if any(has_word_match(line, t) for t in terms):
                spans.append((lineno, lineno))
                under_matching_heading = True
            else:
                under_matching_heading = False
            continue
        if under_matching_heading and stripped:
            spans.append((lineno, lineno))
    return spans


def _merge_and_cap_spans(
    weighted_spans: list[tuple[int, int, float]], line_count: int
) -> list[tuple[int, int, float]]:
    """Merge overlapping/adjacent (start, end, weight) spans, summing the
    weight of whatever merged into each span, then cap each merged span's
    length at MAX_EXCERPT_LINES.
    """
    clipped = [
        (max(1, start), min(line_count, end), weight)
        for start, end, weight in weighted_spans
        if line_count > 0
    ]
    clipped.sort(key=lambda t: (t[0], t[1]))

    merged: list[list] = []
    for start, end, weight in clipped:
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
            merged[-1][2] += weight
        else:
            merged.append([start, end, weight])

    capped = []
    for start, end, weight in merged:
        if end - start + 1 > MAX_EXCERPT_LINES:
            end = start + MAX_EXCERPT_LINES - 1
        capped.append((start, end, weight))
    return capped


def _build_excerpts(
    content: str,
    weighted_spans: list[tuple[int, int, float]],
    is_config: bool = False,
) -> list[Excerpt]:
    """Turn weighted candidate spans into the (at most MAX_EXCERPTS_PER_FILE)
    redacted Excerpts for one file: merge, cap span length, keep the
    highest-weight spans up to the per-file cap, then restore reading
    order.

    `is_config` is forwarded to `redact_text` -- see the comment above
    `_should_redact_by_name` in the redaction section for what it changes.
    Defaults to False (code-file rules) so existing direct callers/tests
    that don't pass it keep the conservative (never-name-only-redact-code)
    behavior.
    """
    if not weighted_spans:
        return []
    lines = content.splitlines()
    merged = _merge_and_cap_spans(weighted_spans, len(lines))
    if not merged:
        return []

    merged.sort(key=lambda t: (-t[2], t[0]))
    kept = merged[:MAX_EXCERPTS_PER_FILE]
    kept.sort(key=lambda t: t[0])

    excerpts = []
    for start, end, _weight in kept:
        span_text = "\n".join(lines[start - 1 : end])
        span_text = _truncate_chars(span_text)
        excerpts.append(
            Excerpt(start_line=start, end_line=end, text=redact_text(span_text, is_config=is_config))
        )
    return excerpts


def _truncate_chars(text: str) -> str:
    """Cap `text` at MAX_EXCERPT_CHARS, appending TRUNCATION_MARKER when
    truncation happened. This is what MAX_EXCERPT_LINES cannot provide on
    its own: a single very long line (e.g. one 4,000-character minified
    line) is still bounded, because the cap is on character count, not
    line count.
    """
    if len(text) <= MAX_EXCERPT_CHARS:
        return text
    return text[:MAX_EXCERPT_CHARS].rstrip() + TRUNCATION_MARKER


def _imported_names_of(
    rel_path: str, import_nodes: list, file_index: set[str]
) -> dict[str, set[str]]:
    """Like _imports_of, but returns target path -> the specific names
    imported from it via `from target import name[, ...]`. Plain `import
    module` statements bring no specific names and are not represented
    here -- they carry no excerpt-worthy symbol list.

    `import_nodes` is the file's cached list of Import/ImportFrom AST
    nodes (see filecache.RunCache).
    """
    module_parts, is_init = _module_parts(rel_path)
    base_package = list(module_parts) if is_init else list(module_parts[:-1])

    result: dict[str, set[str]] = {}
    for node in import_nodes:
        if not isinstance(node, ast.ImportFrom):
            continue

        if node.level and node.level > 0:
            up = node.level - 1
            if up > len(base_package):
                continue
            target_package = base_package[: len(base_package) - up]
        else:
            target_package = []

        if node.module:
            target_prefix = tuple(target_package) + tuple(node.module.split("."))
        else:
            target_prefix = tuple(target_package)

        resolved = _resolve_module_tuple(target_prefix, file_index)
        if not resolved or resolved == rel_path:
            continue

        names = {alias.name for alias in node.names if alias.name != "*"}
        if names:
            result.setdefault(resolved, set()).update(names)

    return result


def _build_import_name_graph(
    discovered: list[DiscoveredFile], cache: RunCache | None = None
) -> dict[str, dict[str, set[str]]]:
    """rel path -> {target rel path -> names imported from it}, for every
    discovered first-party Python file.

    `cache` (see filecache.RunCache) reuses this run's already-parsed
    import nodes rather than re-reading/re-parsing every file.
    """
    cache = cache if cache is not None else RunCache()
    py_files = {f.path: f for f in discovered if f.path.endswith(".py")}
    file_index = set(py_files)

    graph: dict[str, dict[str, set[str]]] = {}
    for path, discovered_file in py_files.items():
        content = cache.get_content(discovered_file)
        if content:
            import_nodes = cache.get_record(discovered_file).imports
            graph[path] = _imported_names_of(path, import_nodes, file_index)
        else:
            graph[path] = {}
    return graph


def _importer_of(item_path: str, provenance: list[str]) -> str | None:
    """If `item_path` was reached because some other file in `provenance`
    imports it (i.e. this item is the *imported* file, not the importer),
    return that other file's path. Provenance edges are recorded as
    "<importer> imports <importee>" strings by expand_transitively().
    """
    for edge in reversed(provenance):
        if " imports " not in edge:
            continue
        importer, _, importee = edge.partition(" imports ")
        if importee == item_path:
            return importer
    return None


def attach_excerpts(
    items: list[ContextItem],
    discovered: list[DiscoveredFile],
    task: str,
    cache: RunCache | None = None,
) -> int:
    """Populate `item.excerpts` on every item with the bounded, redacted
    spans that justified its inclusion, then enforce the package-wide
    MAX_PACKAGE_BYTES budget across all items, dropping the lowest-scored
    items' excerpts first.

    Returns the number of excerpts dropped for being over the package
    budget (for trace["excerpts_dropped_over_budget"]).

    `cache` (see filecache.RunCache) reuses this run's already-read
    content and already-parsed def/import nodes rather than re-reading and
    re-parsing every included file.
    """
    cache = cache if cache is not None else RunCache()
    terms = tokenize(task)
    discovered_index = {f.path: f for f in discovered}
    import_name_graph = _build_import_name_graph(discovered, cache)

    for item in items:
        discovered_file = discovered_index.get(item.path)
        if discovered_file is None:
            continue
        content = cache.get_content(discovered_file)
        if not content:
            continue

        # Structural spans, highest priority first: the definitions
        # actually imported (import-reached files), matched symbol
        # definitions, config key lines, doc headings and their assertion
        # lines. A bare content-frequency line (the "everything else"
        # tier) is only used as a fallback when a file has none of these
        # -- otherwise it pads out excerpts with arbitrary prose that
        # happens to contain a term (the middleware.py docstring-sentence
        # problem this step fixes).
        structural_spans: list[tuple[int, int, float]] = []

        if item.path.endswith(".py"):
            defs = cache.get_record(discovered_file).defs
            importer = _importer_of(item.path, item.provenance)
            if importer:
                imported_names = import_name_graph.get(importer, {}).get(item.path, set())
                for start, end in _imported_symbol_spans(defs, imported_names):
                    structural_spans.append((start, end, WEIGHT_IMPORTED_SYMBOL))

            for start, end in _matched_symbol_spans(defs, terms):
                structural_spans.append((start, end, WEIGHT_MATCHED_SYMBOL))

        if discovered_file.role == "config":
            for start, end in _config_key_spans(content, terms):
                structural_spans.append((start, end, WEIGHT_CONFIG_KEY))

        if discovered_file.role == "docs":
            for start, end in _doc_heading_and_assertion_spans(content, terms):
                structural_spans.append((start, end, WEIGHT_DOC_ASSERTION))

        if structural_spans:
            weighted_spans = structural_spans
        elif _is_asset_like(discovered_file.path, content):
            # No structural spans (no imported/matched symbol, config key,
            # or doc heading) and the file itself is a minified/generated
            # asset -- the generic content-line fallback would otherwise
            # dump an arbitrary slice of a minified line as if it were
            # prose worth reading. Such an item (reached only via a
            # filename/path match) gets no excerpt rather than a
            # meaningless one.
            weighted_spans = []
        else:
            weighted_spans = [
                (start, end, WEIGHT_MATCHED_LINE)
                for start, end in _matched_line_spans(content, terms)
            ]

        item.excerpts = _build_excerpts(
            content, weighted_spans, is_config=_is_config_format_file(discovered_file.path)
        )

    return _enforce_package_excerpt_budget(items)


def _enforce_package_excerpt_budget(items: list[ContextItem]) -> int:
    """If total excerpt bytes across `items` exceeds MAX_PACKAGE_BYTES,
    drop whole excerpts -- lowest item score first -- until it does not.
    Returns the number of excerpts dropped.
    """
    entries: list[tuple[ContextItem, Excerpt, int]] = []
    total = 0
    for item in items:
        for excerpt in item.excerpts:
            size = len(excerpt.text.encode("utf-8"))
            entries.append((item, excerpt, size))
            total += size

    if total <= MAX_PACKAGE_BYTES:
        return 0

    # Ascending by item score (lowest first); deterministic tie-break.
    entries.sort(key=lambda entry: (entry[0].score, entry[0].path, entry[1].start_line))

    to_drop_ids: set[int] = set()
    remaining = total
    for item, excerpt, size in entries:
        if remaining <= MAX_PACKAGE_BYTES:
            break
        to_drop_ids.add(id(excerpt))
        remaining -= size

    for item in items:
        item.excerpts = [e for e in item.excerpts if id(e) not in to_drop_ids]

    return len(to_drop_ids)


def score_file(
    discovered_file: DiscoveredFile,
    terms: list[str],
    filename_word_counts: dict[str, int] | None = None,
) -> float:
    """Score `discovered_file` against `terms`. See module docstring for
    the weighting scheme. `filename_word_counts` is optional -- see
    _analyze()'s docstring; omitting it scores as if every filename word
    matched were unique to the repo (the pre-rarity-fix behavior), which
    is what existing single-file callers, including tests, already expect.
    """
    score, _reason, _matched_terms = _analyze(discovered_file, terms, filename_word_counts=filename_word_counts)
    return score


def select(
    discovered: list[DiscoveredFile], task: str, cache: RunCache | None = None
) -> tuple[list[ContextItem], dict[str, int], dict]:
    """Tokenize `task`, score every discovered file, and return
    (ranked ContextItems above threshold and within the cap,
    extra exclusion-reason counts for "below_threshold" and "over_cap",
    selection_stats).

    `selection_stats` is the raw ingredients for weak-signal detection
    (see detect_weak_signal()), not a verdict: {"terms": the tokenized
    task terms, "term_file_counts": term -> number of discovered files
    whose distinct_matched_terms include that term}. Computed here
    because this is the one place every discovered file is already
    scored against `terms` -- recomputing it downstream would mean
    re-reading and re-parsing every file a second time.

    `cache` (see filecache.RunCache), when given, is shared with the
    caller's later attach_excerpts()/checks calls for this same run, so a
    file scored here is not re-read and re-parsed by those later stages.
    Defaults to a private per-call cache so this stays independently
    callable (e.g. from tests).
    """
    cache = cache if cache is not None else RunCache()
    terms = tokenize(task)
    # Built once per run from the paths discover() already returned -- no
    # extra file reads -- and shared across every file's _analyze() call
    # below so a repo-wide naming convention (page.tsx, __init__.py, ...)
    # is recognized as such. See compute_filename_word_counts().
    filename_word_counts = compute_filename_word_counts(discovered)

    scored: list[tuple[float, DiscoveredFile, str, frozenset[str]]] = []
    for discovered_file in discovered:
        score, reason, matched_terms = _analyze(discovered_file, terms, cache, filename_word_counts)
        scored.append((score, discovered_file, reason, matched_terms))

    # term -> how many discovered files (regardless of whether they cleared
    # SCORE_THRESHOLD) corroborated that term by any signal. This is the
    # same file-count intuition compute_filename_word_counts() applies to
    # filenames, generalized to every signal _analyze() considers -- it is
    # what lets weak-signal detection tell "page" (a framework convention,
    # present in a dozen filenames) apart from a genuinely distinctive term
    # that happens to match only one file.
    term_file_counts: dict[str, int] = {t: 0 for t in terms}
    for _score, _discovered_file, _reason, matched_terms in scored:
        for t in matched_terms:
            term_file_counts[t] = term_file_counts.get(t, 0) + 1

    above = [s for s in scored if s[0] > SCORE_THRESHOLD]
    below_count = len(scored) - len(above)

    # Stable rank: score descending, tie-break on path ascending for
    # determinism.
    above.sort(key=lambda s: (-s[0], s[1].path))

    kept = above[:MAX_SEEDS]
    seed_over_cap_count = len(above) - len(kept)

    seed_items = [
        ContextItem(
            path=discovered_file.path,
            role=discovered_file.role,
            reason=reason,
            score=score,
            matched_terms=sorted(matched_terms),
        )
        for score, discovered_file, reason, matched_terms in kept
    ]

    expanded_items, expansion_over_cap_count = expand_transitively(seed_items, discovered, cache)

    # Merge seeds and expanded items into a single ranking -- score
    # descending across both groups, tie-broken on path -- rather than
    # appending expanded items after seeds regardless of score.
    combined = seed_items + expanded_items
    combined.sort(key=lambda item: (-item.score, item.path))

    final_items = combined[:MAX_INCLUDED]
    final_over_cap_count = len(combined) - len(final_items)

    extra_exclusions = {
        "below_threshold": below_count,
        "over_cap": seed_over_cap_count + expansion_over_cap_count + final_over_cap_count,
    }
    selection_stats = {
        "terms": terms,
        "term_file_counts": term_file_counts,
        "total_files": len(discovered),
    }
    return final_items, extra_exclusions, selection_stats


# --- weak-signal detection ------------------------------------------------
#
# A file the developer did not name but that the import graph reaches is
# the hypothesis; the failure mode this guards against is the opposite
# case, where nothing genuinely relevant exists but SELECT still clears
# SCORE_THRESHOLD -- typically because one task term happens to be a
# repo-wide naming convention (Next.js's `page.tsx`) or a word that shows
# up as an incidental one-off mention in a couple of files. Neither is
# "no relevant context" (something *did* score above threshold) nor a
# confident answer (nothing corroborates it) -- it is a third state.
#
# Two conditions, both required:
#
# 1. Multi-term only. A single-term task (`octx "applications"`) has
#    coverage_ratio == 1.0 for any file that matches at all -- "only one
#    *strong* term matched" is true of every single-term task by
#    construction, so the warning would be meaningless noise there.
#    Requiring >=2 terms is what makes condition 2 a real signal rather
#    than a tautology.
#
# 2. No included file corroborates with more than one *individually
#    strong* term. "Strong" is the same per-term weakness test used to
#    decide whether to warn at all (below): WEAK_FILENAME_COMMON_COUNT+
#    files sharing a path word is a repo convention (`page.tsx`), not a
#    distinctive name, and a term absent from every filename that backs
#    at most WEAK_CONTENT_RARE_COUNT files total is a thin, incidental
#    mention -- neither counts as real corroboration even when two of
#    them happen to land in the same file. Folding the weakness test into
#    the corroboration check (rather than treating "matches >1 term" and
#    "the matched terms are weak" as two independent conditions) matters
#    in practice: on a real repo it is common for an otherwise-correct,
#    single-strong-signal file (a `page.tsx` filename hit) to also
#    contain an incidental second-term mention -- a comment, an id
#    string, a nearby unrelated word -- that would otherwise look like
#    "multi-term corroboration" and wrongly suppress the warning. A file
#    that combines two genuinely rare, distinctive terms (filename or
#    symbol matches, not coincidental prose) is real convergent evidence
#    and correctly is NOT weak -- e.g. "navigation" matching only
#    lib/navigation.ts is a strong signal on its own even though it is a
#    single term matching "few files": a one-of-a-kind filename hit is
#    exactly the targeted result SELECT exists to surface.
#
# Read from ContextItem.matched_terms (see select() above), not
# re-derived from `reason` -- `reason` only ever names a file's single
# strongest signal (_build_reason()'s leading-signal rule), so a file
# that matched two terms but whose reason mentions only the stronger one
# would look single-term from `reason` alone. Transitively-expanded items
# (ContextItem.matched_terms == []) were reached by import edges, not a
# term match, so they neither add nor subtract corroboration evidence.
WEAK_FILENAME_COMMON_COUNT = 4
WEAK_CONTENT_RARE_COUNT = 5

# --- bug fix: a content-only term treated as weak only when rare, never
# when common -----------------------------------------------------------
#
# The rule above has a hole: a content-only term (fname_count == 0) was
# only ever judged weak for being *rare* (<= WEAK_CONTENT_RARE_COUNT
# files). There was no symmetric case for a content-only term that is
# very *common* -- appearing in a large share of the repo's files. But a
# word mentioned in, say, a fifth of every discovered file is generic
# prose (an ordinary English word, a boilerplate phrase, a word every doc
# happens to use) -- that is *weaker* evidence of relevance than a rare
# word, not stronger. Both tails of the distribution are weak; only a
# term with a meaningful-but-not-overwhelming presence -- a real, if
# imperfect, content signal -- is a genuine one.
#
# Observed on OpenContextually's own repo: `octx "what's wrong, in plain
# English"` tokenizes to ["plain", "english"]. "plain" has fname_count 0
# and appears in the content of 13 of 64 discovered files (~20%) --
# clearly not "rare" under WEAK_CONTENT_RARE_COUNT, so the old rule
# treated it as strong signal on its own and never warned, even though
# every included file matched only "plain" (a generic word appearing all
# over READMEs, templates, and docs) and "english" matched nothing at
# all. That is exactly the weak-match case this function exists to catch.
#
# WEAK_CONTENT_COMMON_RATIO is expressed as a *proportion* of the files
# this run actually discovered (`total_files`), not a fixed file count,
# so the same rule scales correctly from a 75-file repo (this one) to a
# 7,000-file monorepo: "appears in 20% of the repo" is the same strength
# of evidence regardless of how many files that percentage happens to be,
# whereas a fixed count like "appears in >=15 files" would falsely call a
# genuinely common term rare on a huge repo and falsely call a genuinely
# distinctive term common on a tiny one. 0.15 (15%) was chosen by testing
# against real tasks: it catches "plain" (~20% of files) while leaving a
# genuinely distinctive term corroborating a good match untouched (e.g.
# "mobile" at ~7% of files on a real Next.js repo). On ~/Dali, "citation"
# and "verification" both trip WEAK_FILENAME_COMMON_COUNT on their own
# (each names >=4 discovered files), so the per-item bail-out below finds
# a genuinely strong pair before this ratio ever has to judge the task's
# other, more common terms ("returns" included) -- exactly the outcome
# the real-repo check confirmed.
WEAK_CONTENT_COMMON_RATIO = 0.15


def detect_weak_signal(
    terms: list[str],
    included: list[ContextItem],
    term_file_counts: dict[str, int],
    filename_word_counts: dict[str, int],
    total_files: int = 0,
) -> dict | None:
    """Return a weak_signal summary dict (see ContextPackage.weak_signal)
    when `included` clears SCORE_THRESHOLD but the signal behind it is
    weak by the conditions above, else None. Never removes anything from
    `included` -- this only decides whether to warn ahead of it.

    Runs against `included` post-excerpt-eviction (get_context() calls
    this after dropping fully-evicted items), so a seed whose excerpts
    were entirely dropped for budget reasons -- and so never reaches the
    user as an inclusion -- does not spuriously count as corroboration
    either.
    """
    if len(terms) < 2 or not included:
        return None

    seeds = [item for item in included if item.matched_terms]
    if not seeds:
        return None

    def _term_is_weak(term: str) -> bool:
        fname_count = filename_word_counts.get(term.lower(), 0)
        if fname_count >= WEAK_FILENAME_COMMON_COUNT:
            return True
        if fname_count == 0:
            content_count = term_file_counts.get(term, 0)
            if content_count <= WEAK_CONTENT_RARE_COUNT:
                return True
            if total_files > 0 and content_count >= WEAK_CONTENT_COMMON_RATIO * total_files:
                return True
        return False

    # A file combining two individually-strong terms is real convergent
    # evidence -- bail out to "not weak" as soon as one is found.
    for item in seeds:
        strong_terms = [t for t in item.matched_terms if not _term_is_weak(t)]
        if len(strong_terms) > 1:
            return None

    matched_terms: set[str] = set()
    for item in seeds:
        matched_terms.update(item.matched_terms)
    if not matched_terms:
        return None

    if not all(_term_is_weak(t) for t in matched_terms):
        return None

    return {
        "matched_terms": {t: term_file_counts.get(t, 0) for t in sorted(matched_terms)},
        "term_file_counts": {t: term_file_counts.get(t, 0) for t in terms},
    }
