"""CHECK: narrow, high-precision, low-recall rules over discovered files.

v0.1 ships one rule here: `configuration_discrepancy`. It is deliberately
**not** general conflict detection -- it finds the same named scalar
setting declared with different values across a config file and a doc, and
nothing broader than that. When correspondence between a config key and a
doc assertion is not clear, or the two agree, it stays silent. A false
positive here destroys the product's core trust claim faster than a miss
ever would, so this rule is written to under-report rather than
over-report.

Config parsing below is a **regex `key: value` / `key = value` scalar line
scan** (plus stdlib `json` for `.json`) -- not a real YAML/TOML parser. It
is a lexical rule, not a semantic one: it does not understand YAML anchors,
multi-line scalars, TOML arrays-of-tables, or anything beyond a flat or
indentation-nested `key: value` line. That is a deliberate scope limit, not
an oversight.
"""

from __future__ import annotations

import ast
import json
import math
import re

from .discovery import DiscoveredFile
from .filecache import RunCache
from .selector import _looks_like_secret_key

RULE_ID = "configuration_discrepancy"
RULE_ID_TEST_REFERENCE_GAP = "test_reference_gap"

# Key names that look like they hold a secret are never reported -- even
# though only numeric scalars can become a finding in practice, this is a
# defense-in-depth guard so a secret-looking key can never appear in a
# conflicts entry. Shares its definition (including the "max_tokens" vs.
# "access_token" distinction) with selector.py's excerpt redaction, via
# _looks_like_secret_key, so a key is judged sensitive or not the same way
# everywhere in the package.

_UNIT_SECONDS = {
    "ms": 0.001,
    "millisecond": 0.001,
    "milliseconds": 0.001,
    "s": 1.0,
    "sec": 1.0,
    "secs": 1.0,
    "second": 1.0,
    "seconds": 1.0,
    "m": 60.0,
    "min": 60.0,
    "mins": 60.0,
    "minute": 60.0,
    "minutes": 60.0,
    "h": 3600.0,
    "hr": 3600.0,
    "hrs": 3600.0,
    "hour": 3600.0,
    "hours": 3600.0,
}
_UNIT_WORDS = set(_UNIT_SECONDS)

# Longest alternatives first so "minutes" matches before "min", "m", etc.
_DURATION_RE = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>milliseconds?|minutes?|seconds?|hours?|mins?|secs?|hrs?|ms|m|s|h)\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"\b(?P<num>\d+(?:\.\d+)?)\b")
_NUMERIC_VALUE_RE = re.compile(r"-?\d+(?:\.\d+)?")

_YAML_KEY_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<key>[A-Za-z_][A-Za-z0-9_\-]*)\s*:\s*(?P<value>.*)$")
_INI_SECTION_RE = re.compile(r"^\[(?P<section>[^\]]+)\]\s*$")
_INI_KEY_RE = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_.\-]*)\s*=\s*(?P<value>.*)$")

_TOKEN_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")


def _strip_inline_comment(value: str) -> str:
    """Cut a trailing ` # ...` comment off an unquoted scalar value. This
    is a heuristic, not a real tokenizer -- quoted values are left alone.
    """
    if value.startswith(('"', "'")):
        return value.strip()
    idx = value.find(" #")
    if idx != -1:
        value = value[:idx]
    idx = value.find(" ;")
    if idx != -1:
        value = value[:idx]
    return value.strip()


def _parse_yaml_like(content: str) -> list[tuple[str, str, int]]:
    """Regex `key: value` scan with indentation tracking, so a nested key
    like:

        session:
          timeout_minutes: 60

    resolves to the dotted path "session.timeout_minutes" rather than
    just "timeout_minutes". This is line-based, not a real YAML parser --
    block scalars (`|`, `>`), flow mappings (`{...}`), and lists are not
    understood; a value that looks like one of those is treated as a
    mapping header (no value) rather than misparsed as a scalar.
    """
    entries: list[tuple[str, str, int]] = []
    stack: list[tuple[int, str]] = []

    for lineno, raw in enumerate(content.splitlines(), start=1):
        line = raw.expandtabs(2)
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("- "):
            continue

        match = _YAML_KEY_RE.match(line)
        if not match:
            continue

        indent = len(match.group("indent"))
        key = match.group("key")
        value = _strip_inline_comment(match.group("value"))

        while stack and stack[-1][0] >= indent:
            stack.pop()

        if not value or value in ("|", ">", "|-", ">-", "{", "["):
            stack.append((indent, key))
            continue

        path = ".".join([k for _, k in stack] + [key])
        entries.append((path, value, lineno))

    return entries


def _parse_ini_like(content: str) -> list[tuple[str, str, int]]:
    """`[section]` + `key = value` scan for .ini/.cfg/.toml, and flat
    `key = value` (or `key: value`) for .env, which has no sections.
    Section headers become a one-level dotted prefix; nested TOML tables
    of the form `[a.b]` are used verbatim as that prefix, which is enough
    to disambiguate same-named leaf keys without understanding TOML.
    """
    entries: list[tuple[str, str, int]] = []
    section: str | None = None

    for lineno, raw in enumerate(content.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue

        section_match = _INI_SECTION_RE.match(stripped)
        if section_match:
            section = section_match.group("section").strip()
            continue

        match = _INI_KEY_RE.match(stripped)
        if not match:
            continue

        key = match.group("key")
        value = _strip_inline_comment(match.group("value"))
        if not value:
            continue
        path = f"{section}.{key}" if section else key
        entries.append((path, value, lineno))

    return entries


def _find_json_key_line(lines: list[str], leaf_key: str) -> int:
    """Best-effort line number for a JSON leaf key: the first line
    containing `"leaf_key":`. json.loads() gives no positions of its own,
    so this is a lexical fallback, not a guarantee for repeated key names
    at different nesting levels.
    """
    pattern = re.compile(r'"' + re.escape(leaf_key) + r'"\s*:')
    for lineno, line in enumerate(lines, start=1):
        if pattern.search(line):
            return lineno
    return 1


def _parse_json(content: str) -> list[tuple[str, str, int]]:
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return []

    flat: list[tuple[str, object]] = []

    def _walk(obj: object, prefix: str) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                _walk(value, f"{prefix}.{key}" if prefix else str(key))
        elif isinstance(obj, (str, int, float, bool)) or obj is None:
            if prefix:
                flat.append((prefix, obj))

    _walk(data, "")

    lines = content.splitlines()
    entries: list[tuple[str, str, int]] = []
    for path, value in flat:
        leaf_key = path.rsplit(".", 1)[-1]
        lineno = _find_json_key_line(lines, leaf_key)
        entries.append((path, "" if value is None else str(value), lineno))
    return entries


def _parse_config_entries(path: str, content: str) -> list[tuple[str, str, int]]:
    """Dispatch to the right lexical scalar scan by extension. Any parse
    failure returns no entries for that file rather than raising -- a
    malformed config file must never crash the run.
    """
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    try:
        if ext in ("yaml", "yml"):
            return _parse_yaml_like(content)
        if ext in ("ini", "cfg", "toml"):
            return _parse_ini_like(content)
        if ext == "env" or path.endswith(".env"):
            return _parse_ini_like(content)
        if ext == "json":
            return _parse_json(content)
    except Exception:
        return []
    return []


def _infer_unit_from_key(key_lower: str) -> str | None:
    """Infer a duration unit from a key name's substrings (e.g.
    "timeout_minutes" -> "minutes"), for a config value that is a bare
    number with no unit of its own. Returns None when the key gives no
    unit hint, in which case the value is compared as a unitless number.
    """
    if "millisecond" in key_lower or key_lower.endswith(("_ms", ".ms")):
        return "ms"
    if "minute" in key_lower or "_min" in key_lower or key_lower.endswith(".min"):
        return "minutes"
    if "second" in key_lower or "_sec" in key_lower or key_lower.endswith(".sec"):
        return "seconds"
    if "hour" in key_lower or "_hr" in key_lower or key_lower.endswith(".hr"):
        return "hours"
    return None


def _key_tokens(key: str) -> set[str]:
    return {t.lower() for t in _TOKEN_SPLIT_RE.split(key) if t}


def _extract_line_value(line: str) -> tuple[str, float, str, int] | None:
    """Return (kind, normalized_seconds_or_number, raw_matched_text,
    match_start) for the first value assertion on `line`, or None. A
    duration is only recognized when the line spells out a unit explicitly
    ("30 minutes", "1800s") -- a bare number with no unit is reported as an
    untyped "number" and only ever compared against another untyped config
    value, never guessed into a duration. Guessing there would trade
    precision for recall, which this rule is not willing to do.

    `match_start` is the character offset of the match within `line`, used
    by the caller to check whether the match falls inside an inline code
    span (single backticks) -- see _collect_doc_assertions.
    """
    duration_match = _DURATION_RE.search(line)
    if duration_match:
        unit = duration_match.group("unit").lower()
        factor = _UNIT_SECONDS.get(unit)
        if factor is not None:
            return (
                "duration",
                float(duration_match.group("num")) * factor,
                duration_match.group(0),
                duration_match.start(),
            )

    number_match = _NUMBER_RE.search(line)
    if number_match:
        return "number", float(number_match.group("num")), number_match.group(0), number_match.start()

    return None


# --------------------------------------------------------------------------
# Bug fix: doc code examples misread as configuration requirements.
#
# A doc line that only *demonstrates* a value -- inside a fenced code block,
# an rst `.. code-block::`/`.. sourcecode::` directive, an rst `::` literal
# block, or an inline code span -- is not the docs asserting a requirement.
# Observed on a real repo (sqlfluff): a `.. code-block:: sql` tutorial
# showing how to *override* the default `tab_space_size` for a single file
# ("-- Set a smaller indent for this file") was read as the docs declaring
# the project-wide default should be 2, producing a false-positive conflict
# against the real default of 4. The project's own stated bar is that any
# false-positive configuration_discrepancy is a bug, so doc lines inside a
# code example are excluded from consideration entirely -- never turned
# into a doc assertion in the first place. This is a lexical, line-based
# exclusion (mirroring the rest of this module's lexical approach), not a
# real Markdown/rst parser: it is deliberately conservative, erring toward
# excluding a line that might be prose over including one that is actually
# code.
# --------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
_RST_DIRECTIVE_RE = re.compile(r"^\s*\.\.\s+(code-block|sourcecode)::")
_INLINE_CODE_SPAN_RE = re.compile(r"`[^`\n]+`")


def _indent_of(line: str) -> int:
    expanded = line.expandtabs(4)
    return len(expanded) - len(expanded.lstrip(" "))


def _fenced_code_line_mask(lines: list[str]) -> set[int]:
    """1-indexed line numbers inside a Markdown-style fenced code block
    (``` or ~~~), including the fence lines themselves. Works for any text
    file that happens to use fences, not just `.md`.
    """
    code_lines: set[int] = set()
    fence_char: str | None = None
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        match = _FENCE_RE.match(stripped)
        if match:
            marker = match.group(1)[0]
            if fence_char is None:
                fence_char = marker
                code_lines.add(idx)
                continue
            if marker == fence_char:
                fence_char = None
                code_lines.add(idx)
                continue
        if fence_char is not None:
            code_lines.add(idx)
    return code_lines


def _indented_markdown_block_mask(lines: list[str]) -> set[int]:
    """1-indexed line numbers of Markdown-style indented (4+ space) code
    blocks: a non-blank line indented >=4 spaces, immediately preceded by a
    blank line or another such indented line. A crude approximation of
    CommonMark's indented-code-block rule -- it does not special-case list
    items, which means some genuinely-indented list prose is also excluded,
    an acceptable false negative given this rule's precision-over-recall
    posture.
    """
    code_lines: set[int] = set()
    prev_blank_or_code = True
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            prev_blank_or_code = True
            continue
        if prev_blank_or_code and _indent_of(line) >= 4:
            code_lines.add(idx)
            prev_blank_or_code = True
        else:
            prev_blank_or_code = False
    return code_lines


def _rst_code_line_mask(lines: list[str]) -> set[int]:
    """1-indexed line numbers inside an rst `.. code-block::`/
    `.. sourcecode::` directive body, or an rst `::` literal-block body --
    any block of lines indented deeper than the line that introduced it,
    running until the indentation returns to (or below) the introducing
    line's own indentation.
    """
    code_lines: set[int] = set()
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        stripped = line.strip()
        is_directive = bool(_RST_DIRECTIVE_RE.match(line))
        # A paragraph/marker line ending in "::" introduces an rst literal
        # block (e.g. "Some examples are shown below::", or a bare "::").
        # Excludes the directive itself, which is handled by is_directive.
        is_literal_marker = (not is_directive) and stripped.endswith("::")

        if not (is_directive or is_literal_marker):
            i += 1
            continue

        trigger_indent = _indent_of(line)
        j = i + 1
        while j < n and lines[j].strip() == "":
            j += 1
        if j < n and _indent_of(lines[j]) > trigger_indent:
            while j < n:
                if lines[j].strip() == "":
                    j += 1
                    continue
                if _indent_of(lines[j]) <= trigger_indent:
                    break
                code_lines.add(j + 1)
                j += 1
        i = j if j > i else i + 1
    return code_lines


def _doc_code_line_mask(content: str, lines: list[str]) -> set[int]:
    """Union of every code-context detector above, for one doc file's
    lines. Extension-agnostic (fences and rst directives are each checked
    unconditionally) since real-world docs mix conventions.
    """
    mask = _fenced_code_line_mask(lines)
    mask |= _indented_markdown_block_mask(lines)
    mask |= _rst_code_line_mask(lines)
    return mask


def _in_inline_code_span(line: str, start: int) -> bool:
    """True if character offset `start` in `line` falls inside a single-
    backtick inline code span. Inline code is weaker evidence of a genuine
    requirement than plain prose (it is often used for a config key name
    or an example value), so a value found only inside one is not treated
    as a doc assertion.
    """
    for match in _INLINE_CODE_SPAN_RE.finditer(line):
        if match.start() <= start < match.end():
            return True
    return False


def _collect_doc_assertions(doc_files: list[DiscoveredFile], cache: RunCache) -> list[dict]:
    assertions: list[dict] = []
    for doc_file in doc_files:
        content = cache.get_content(doc_file)
        if not content:
            continue
        lines = content.splitlines()
        code_line_mask = _doc_code_line_mask(content, lines)
        for lineno, line in enumerate(lines, start=1):
            if lineno in code_line_mask:
                continue
            extracted = _extract_line_value(line)
            if extracted is None:
                continue
            kind, normalized, raw, start = extracted
            if _in_inline_code_span(line, start):
                continue
            tokens = _key_tokens(line)
            assertions.append(
                {
                    "path": doc_file.path,
                    "line": lineno,
                    "kind": kind,
                    "normalized": normalized,
                    "raw": raw,
                    "tokens": tokens,
                }
            )
    return assertions


def find_configuration_discrepancies(
    discovered: list[DiscoveredFile], cache: RunCache | None = None
) -> list[dict]:
    """Find same-named scalar settings declared with different values
    across a config file and a doc.

    High precision, low recall by construction:
      - only config keys with >=2 meaningful (non-unit) name tokens are
        considered at all -- a single generic token like bare "timeout"
        is never enough to correlate two files;
      - a doc line is only a candidate match when it contains *every*
        one of those tokens;
      - only numeric scalar values participate (text/boolean scalars are
        not compared);
      - duration units are normalized to seconds so "30 minutes",
        "1800 seconds", and "30m" all compare correctly;
      - a duration-typed config value is only compared against a
        duration-typed doc assertion (one with an explicit unit), never
        guessed against a bare number;
      - secret-looking keys are never reported, even though in practice
        they never parse as numeric scalars either.

    Returns a list of finding dicts, each carrying at minimum: "rule"
    (the rule id), "setting" (the dotted key name), "config" and "doc"
    (each {"path", "line", "value"}), and a human-readable "message".
    Returns [] when nothing meets the bar above -- never a "no
    discrepancies found" placeholder entry.
    """
    cache = cache if cache is not None else RunCache()
    config_files = [f for f in discovered if f.role == "config"]
    doc_files = [f for f in discovered if f.role == "docs"]
    if not config_files or not doc_files:
        return []

    doc_assertions = _collect_doc_assertions(doc_files, cache)
    if not doc_assertions:
        return []

    findings: list[dict] = []
    seen: set[tuple[str, int, str, int]] = set()

    for config_file in config_files:
        content = cache.get_content(config_file)
        if not content:
            continue

        entries = _parse_config_entries(config_file.path, content)

        for key, raw_value, lineno in entries:
            key_lower = key.lower()
            if _looks_like_secret_key(key):
                continue

            core_tokens = _key_tokens(key) - _UNIT_WORDS
            if len(core_tokens) < 2:
                continue

            value_str = raw_value.strip().strip("\"'")
            if not _NUMERIC_VALUE_RE.fullmatch(value_str):
                continue
            number = float(value_str)

            unit = _infer_unit_from_key(key_lower)
            if unit:
                config_kind, config_norm = "duration", number * _UNIT_SECONDS[unit]
            else:
                config_kind, config_norm = "number", number

            for assertion in doc_assertions:
                if assertion["kind"] != config_kind:
                    continue
                if not core_tokens.issubset(assertion["tokens"]):
                    continue
                if math.isclose(config_norm, assertion["normalized"], rel_tol=1e-9, abs_tol=1e-9):
                    continue  # both sides agree -- not a discrepancy

                dedup_key = (config_file.path, lineno, assertion["path"], assertion["line"])
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                unit_suffix = f" {unit}" if unit else ""
                findings.append(
                    {
                        "rule": RULE_ID,
                        "setting": key,
                        "config": {
                            "path": config_file.path,
                            "line": lineno,
                            "value": value_str,
                        },
                        "doc": {
                            "path": assertion["path"],
                            "line": assertion["line"],
                            "value": assertion["raw"],
                        },
                        "message": (
                            f"{key}: {config_file.path}:{lineno} declares {value_str}{unit_suffix}, "
                            f"but {assertion['path']}:{assertion['line']} says {assertion['raw']}"
                        ),
                    }
                )

    return findings


# ==========================================================================
# test_reference_gap
# ==========================================================================
#
# "test_reference_gap", not "missing tests" or "coverage gap": this rule
# reports a verifiable lexical fact -- a term or config key with real
# presence in the *selected* source/config files has no discovered test
# file that references it (under a normalized word match, so a naming
# convention difference such as "session_expiry" vs "test_session_expires"
# still counts as a reference). It is deliberately not a coverage claim,
# which would require knowing what a test *should* assert -- that is out
# of scope. Findings are phrased factually ("no test references X"),
# never prescriptively ("add a test for X").
#
# Scope, by construction:
#   - only terms/keys found in files SELECT already chose for this task
#     (not every discovered source/config file, unlike
#     configuration_discrepancy, which is a repo-wide structural fact);
#   - concepts are multi-word only, decomposed from Python def/class names
#     and config key paths -- a single generic word is never enough on its
#     own to be "a concept" (same discipline as configuration_discrepancy's
#     two-token rule). An earlier version of this rule also spawned a
#     concept from any literal single-word task-term mention anywhere in a
#     file's text, on the theory that a task term is inherently
#     non-generic. Real-repo evaluation (step 11) disproved that: a task
#     like "the MCP server tool schema is wrong" tokenizes to ordinary
#     words ("server", "tool", "wrong") that show up incidentally all over
#     a real codebase, producing exactly the noise this rule exists to
#     avoid. Task terms still matter -- they drive which files SELECT
#     picks, and they are why the def/class-name and config-key concepts
#     below get considered in the first place -- but they no longer spawn
#     a finding purely from being present as text.
#   - secret-looking config keys are never turned into a concept.
#
# --- real-repo tuning (step 11): rank/cap findings -------------------------
#
# The version of this rule described above turns *every* unreferenced
# def/class name and *every* unreferenced config key into its own finding.
# On the flagship demo fixture that was six findings for one task -- most
# of them true but trivial (no test calls a private helper method by name)
# rather than genuinely risky. A wall of weak-but-true findings erodes
# trust exactly the way a false positive does: the developer stops
# reading. So a finding only survives if something the tool *already
# knows* corroborates that this particular gap matters, not just that it
# is technically true:
#
#   - a config-key gap is corroborated when that exact key is *also* the
#     subject of a configuration_discrepancy conflict -- an independent
#     rule has already flagged this setting as inconsistent, so "no test
#     covers it either" is compounding evidence, not a first guess.
#   - a source-symbol gap is corroborated when the symbol's call site is
#     the test of an `if` that raises or returns in its body, elsewhere in
#     the selected code -- i.e. it is not merely defined-and-uncalled, it
#     is a decision point another part of the codebase branches on, which
#     is what makes an untested path risky rather than merely unused.
# An earlier version of this list also had a third tier -- any literal
# task-term mention, uncorroborated -- on the theory that a task term is
# inherently non-generic. Real-repo evaluation showed otherwise (see the
# module docstring above): that tier is gone, not just deprioritized.
#
# This is a ranking signal built from facts the pipeline already computed
# (another rule's finding, real control-flow structure) -- not an invented
# confidence score. MAX_TEST_REFERENCE_GAP_FINDINGS is a hard backstop on
# top of that so a repo that happens to produce many corroborated findings
# still cannot become a wall of text.
MAX_TEST_REFERENCE_GAP_FINDINGS = 5

_STRENGTH_CORROBORATED = 2

# A project with **no test files at all** is a special case: naively this
# rule would then flag every single term as a gap, which is not new
# information -- it is one fact ("this project has no tests") repeated
# once per term, and that wall of noise is exactly the failure mode the
# v0.1 plan warns CHECK rules against. So when discover() found zero test
# files, this rule stays silent rather than reporting a gap for
# everything; "no tests exist" is a coarser and more useful signal than
# this rule is built to produce, and is visible elsewhere (an empty
# `role == "test"` inclusion set) without this rule inventing N findings.

_GENERIC_IDENTIFIER_WORDS = {
    "is", "get", "set", "has", "does", "do", "run", "new", "old", "the",
    "and", "for", "with", "from", "import", "return", "self", "init",
    "test", "tests", "true", "false", "none", "str", "int", "float",
    "bool", "list", "dict", "to", "of", "in", "on", "at", "by", "or",
    "an", "a", "not", "if", "else",
}

_MIN_CONCEPT_WORD_LEN = 3
_MIN_CONCEPT_WORDS = 2
_STEM_PREFIX_LEN = 5
_STEM_MIN_LEN = 6

_WORD_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")


def _split_identifier_words(text: str) -> list[str]:
    """Lowercase words from camelCase/snake_case/free text: insert a space
    at camelCase and snake_case boundaries, then split on non-alnum. Used
    both for def/class names and for raw file content, so an identifier
    and a mention of it in prose or a docstring normalize the same way.
    """
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", spaced)
    spaced = spaced.lower()
    return [w for w in _WORD_SPLIT_RE.split(spaced) if w]


def _stem(word: str) -> str:
    """A deliberately crude stemmer: for words of _STEM_MIN_LEN+ characters,
    compare only the first _STEM_PREFIX_LEN characters. This is enough to
    treat "expiry"/"expires"/"expired"/"expiration" as the same concept
    (they all share the "expir" prefix) without a real morphological
    stemmer or dependency. Short words are compared exactly, since a short
    shared prefix is far more likely to be a coincidence than a shared
    root.
    """
    if len(word) >= _STEM_MIN_LEN:
        return word[:_STEM_PREFIX_LEN]
    return word


def _meaningful_words(words: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for word in words:
        if len(word) < _MIN_CONCEPT_WORD_LEN or word in _GENERIC_IDENTIFIER_WORDS:
            continue
        if word not in seen:
            seen.add(word)
            ordered.append(word)
    return ordered


_IDENTIFIER_NODE_TYPES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
)


def _python_identifier_words(record) -> list[str] | None:
    """Words drawn only from *identifiers actually used as code* in a
    Python test file -- def/class names, name references, attribute
    accesses, call keyword args, and imported names -- deliberately
    excluding string literals, docstrings, and comments.

    This distinction matters: a test module's docstring can describe what
    it does *not* cover ("no test here for session expiration") using
    exactly the words a real assertion would use, without the test
    actually referencing anything. Scanning raw text would treat that
    prose as a reference and silently swallow the very gap it is
    describing. Only code identifiers count as a reference; the docstring
    that talks *about* session expiration is not equivalent to a test
    that calls `is_session_expired`.

    `record` is the file's filecache.FileRecord: `identifier_nodes` already
    holds exactly the def/class/Name/Attribute/arg/alias/keyword nodes a
    dedicated full-tree walk would find, in the same order, from the one
    walk filecache.RunCache performs per file. Returns None if the content
    did not parse as Python (the caller falls back to whole-text
    tokenization for non-Python test files).
    """
    if not record.parse_ok:
        return None

    words: list[str] = []
    for node in record.identifier_nodes:
        if isinstance(node, _IDENTIFIER_NODE_TYPES):
            words.extend(_split_identifier_words(node.name))
        elif isinstance(node, ast.Name):
            words.extend(_split_identifier_words(node.id))
        elif isinstance(node, ast.Attribute):
            words.extend(_split_identifier_words(node.attr))
        elif isinstance(node, ast.arg):
            words.extend(_split_identifier_words(node.arg))
        elif isinstance(node, ast.alias):
            words.extend(_split_identifier_words(node.name))
            if node.asname:
                words.extend(_split_identifier_words(node.asname))
        elif isinstance(node, ast.keyword) and node.arg:
            words.extend(_split_identifier_words(node.arg))
    return words


def _test_reference_stems(test_files: list[DiscoveredFile], cache: RunCache) -> set[str]:
    """Every word (stemmed) that a discovered test file actually
    references as *code* -- see `_python_identifier_words`. Non-Python
    test files fall back to whole-text tokenization, since there is no
    code/prose distinction to draw there.
    """
    stems: set[str] = set()
    for test_file in test_files:
        content = cache.get_content(test_file)
        if not content:
            continue

        words = None
        if test_file.path.endswith(".py"):
            words = _python_identifier_words(cache.get_record(test_file))
        if words is None:
            words = _split_identifier_words(content)

        for word in words:
            if len(word) < _MIN_CONCEPT_WORD_LEN:
                continue
            stems.add(_stem(word))
    return stems


def _concept_referenced(words: list[str], test_stems: set[str]) -> bool:
    if not test_stems:
        return False
    return all(_stem(word) in test_stems for word in words)


def _source_symbol_concepts(record) -> list[tuple[list[str], int, str]]:
    """Multi-word concepts decomposed from Python def/class names, e.g.
    `is_session_expired` -> ["session", "expired"] (the filler word "is"
    is dropped). Single-word names never produce a concept here -- see
    module docstring. The third element is the raw symbol name itself
    (not word-split), used to check corroboration against gated call
    sites -- see _gated_call_names().

    `record` is the file's filecache.FileRecord; `defs` already holds
    every FunctionDef/AsyncFunctionDef/ClassDef node from one full-tree
    walk, same as a dedicated walk here would find.
    """
    concepts: list[tuple[list[str], int, str]] = []
    for node in record.defs:
        words = _meaningful_words(_split_identifier_words(node.name))
        if len(words) >= _MIN_CONCEPT_WORDS:
            concepts.append((words, node.lineno, node.name))
    return concepts


def _config_key_concepts(path: str, content: str) -> list[tuple[list[str], int, str]]:
    """Multi-word concepts decomposed from config key paths, e.g.
    `session.timeout_minutes` -> ["session", "timeout", "minutes"].
    Secret-looking keys are skipped entirely, same defense-in-depth
    posture as configuration_discrepancy. The third element is the raw
    dotted key itself, used to check corroboration against
    configuration_discrepancy conflicts.
    """
    concepts: list[tuple[list[str], int, str]] = []
    for key, _value, lineno in _parse_config_entries(path, content):
        if _looks_like_secret_key(key):
            continue
        words: list[str] = []
        for segment in key.split("."):
            words.extend(_split_identifier_words(segment))
        words = _meaningful_words(words)
        if len(words) >= _MIN_CONCEPT_WORDS:
            concepts.append((words, lineno, key))
    return concepts


def _short_circuits(stmts: list[ast.stmt]) -> bool:
    """True if any statement in `stmts` raises, or returns a non-None
    value -- the shape of a guard clause, as opposed to a plain
    side-effecting call.
    """
    for stmt in stmts:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Raise):
                return True
            if isinstance(node, ast.Return) and node.value is not None:
                return True
    return False


def _call_names_in(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def _gated_call_names(items: list, discovered: list[DiscoveredFile], cache: RunCache) -> set[str]:
    """Names of symbols called as the test of an `if` whose body raises or
    returns, in Python source files that SELECT matched *directly* against
    this task (empty `item.provenance` -- a real filename/symbol/content
    hit, not a file only reached by following an import edge). This is the
    corroboration signal for a source-symbol test_reference_gap finding --
    see the "real-repo tuning" note above.

    Restricted to directly-matched files, not every selected item: without
    this, a gated call sitting entirely inside a transitively-pulled-in
    file (e.g. one helper calling another inside the same unrelated
    module) corroborates a finding with no connection to the task at all
    -- observed on a real repo (click) where an unrelated stream-encoding
    helper module, reached only because something it imports also imports
    the seed file, produced four gated-and-uncalled-by-tests findings for
    a task about option parsing. Requiring the gate itself to live in a
    file the task's own terms actually matched keeps the corroboration
    signal tied to the task, while still catching the case that matters:
    a seed file (e.g. middleware.py, matched on "authentication") gating a
    call into a symbol defined in a transitively-reached file (e.g.
    session.py's `is_session_expired`) is exactly the risky, untested path
    this rule exists to surface.
    """
    discovered_index = {f.path: f for f in discovered}
    gated: set[str] = set()
    for item in items:
        if item.role != "source" or not item.path.endswith(".py"):
            continue
        if item.provenance:
            continue
        discovered_file = discovered_index.get(item.path)
        if discovered_file is None:
            continue
        record = cache.get_record(discovered_file)
        if not record.parse_ok:
            continue
        for node in record.if_nodes:
            call_names = _call_names_in(node.test)
            if call_names and _short_circuits(node.body):
                gated.update(call_names)
    return gated


def find_test_reference_gaps(
    items: list,
    discovered: list[DiscoveredFile],
    task: str,
    conflicts: list[dict] | None = None,
    cache: RunCache | None = None,
) -> list[dict]:
    """Find def/class names and config keys with real presence in the
    *selected* source/config files (`items`) that no discovered test file
    references.

    `items` is the list of ContextItem produced by SELECT for this task
    -- only files SELECT actually chose are scanned for concepts, per the
    v0.1 plan. `discovered` supplies the full set of discovered files, so
    every discovered test file (selected or not) can be checked for a
    reference, matching the "no discovered test file references them"
    wording in the plan. `conflicts` is the configuration_discrepancy
    findings for this run (may be empty/None), used only as a
    corroboration signal -- see the "real-repo tuning" note above. `task`
    is accepted for interface symmetry with the rest of CHECK/SELECT but
    is not itself a term source -- see the "real-repo tuning" note above
    for why literal task-term mentions were removed as a finding source.

    Returns a list of finding dicts, each carrying at minimum: "rule"
    (the rule id), "term" (the concept, human-readable), "path"/"line"
    (where the term is referenced in source/config -- the evidence for
    the gap), and a factual "message". Returns [] when nothing meets the
    bar above -- never a "nothing missing" placeholder entry, and never
    a finding when there are no discovered test files at all (see module
    docstring). Ranked by corroboration strength and capped at
    MAX_TEST_REFERENCE_GAP_FINDINGS.
    """
    test_files = [f for f in discovered if f.role == "test"]
    if not test_files:
        return []

    selected = [item for item in items if item.role in ("source", "config")]
    if not selected:
        return []

    cache = cache if cache is not None else RunCache()
    test_stems = _test_reference_stems(test_files, cache)
    discovered_index = {f.path: f for f in discovered}

    corroborated_settings = {
        c["setting"] for c in (conflicts or []) if c.get("rule") == RULE_ID and c.get("setting")
    }
    gated_names = _gated_call_names(items, discovered, cache)

    # concept key (sorted unique words) -> (display words, path, line,
    # kind, meta) for the first occurrence encountered, so each concept is
    # reported once. `kind`/`meta` carry what is needed to score
    # corroboration strength below without re-deriving it.
    concepts: dict[tuple[str, ...], tuple[list[str], str, int, str, str]] = {}

    def _record(words: list[str], path: str, lineno: int, kind: str, meta: str) -> None:
        key = tuple(sorted(set(words)))
        if key and key not in concepts:
            concepts[key] = (words, path, lineno, kind, meta)

    for item in selected:
        discovered_file = discovered_index.get(item.path)
        if discovered_file is None:
            continue
        content = cache.get_content(discovered_file)
        if not content:
            continue

        # -- def/class names and config keys defined in this file --
        if item.role == "source" and item.path.endswith(".py"):
            for words, lineno, name in _source_symbol_concepts(cache.get_record(discovered_file)):
                _record(words, item.path, lineno, "source_symbol", name)
        elif item.role == "config":
            for words, lineno, key in _config_key_concepts(item.path, content):
                _record(words, item.path, lineno, "config_key", key)

    scored: list[tuple[int, list[str], str, int]] = []
    for words, path, lineno, kind, meta in concepts.values():
        if _concept_referenced(words, test_stems):
            continue

        if kind == "config_key" and meta in corroborated_settings:
            strength = _STRENGTH_CORROBORATED
        elif kind == "source_symbol" and meta in gated_names:
            strength = _STRENGTH_CORROBORATED
        else:
            strength = 0

        if strength <= 0:
            continue
        scored.append((strength, words, path, lineno))

    # Strongest first; deterministic tie-break on path/line/term.
    scored.sort(key=lambda s: (-s[0], s[2], s[3], " ".join(s[1])))
    kept = scored[:MAX_TEST_REFERENCE_GAP_FINDINGS]

    findings: list[dict] = []
    for _strength, words, path, lineno in kept:
        term_phrase = " ".join(words)
        findings.append(
            {
                "rule": RULE_ID_TEST_REFERENCE_GAP,
                "term": term_phrase,
                "path": path,
                "line": lineno,
                "message": f"no test references {term_phrase}",
            }
        )

    findings.sort(key=lambda f: (f["path"], f["line"], f["term"]))
    return findings
