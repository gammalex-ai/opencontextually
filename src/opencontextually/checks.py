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
from .selector import tokenize

RULE_ID = "configuration_discrepancy"
RULE_ID_TEST_REFERENCE_GAP = "test_reference_gap"

# Key names that look like they hold a secret are never reported -- even
# though only numeric scalars can become a finding in practice, this is a
# defense-in-depth guard so a secret-looking key can never appear in a
# conflicts entry.
_SECRET_KEY_SUBSTRINGS = ("key", "token", "secret", "password", "passwd", "credential")

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


def _extract_line_value(line: str) -> tuple[str, float, str] | None:
    """Return (kind, normalized_seconds_or_number, raw_matched_text) for
    the first value assertion on `line`, or None. A duration is only
    recognized when the line spells out a unit explicitly ("30 minutes",
    "1800s") -- a bare number with no unit is reported as an untyped
    "number" and only ever compared against another untyped config value,
    never guessed into a duration. Guessing there would trade precision
    for recall, which this rule is not willing to do.
    """
    duration_match = _DURATION_RE.search(line)
    if duration_match:
        unit = duration_match.group("unit").lower()
        factor = _UNIT_SECONDS.get(unit)
        if factor is not None:
            return "duration", float(duration_match.group("num")) * factor, duration_match.group(0)

    number_match = _NUMBER_RE.search(line)
    if number_match:
        return "number", float(number_match.group("num")), number_match.group(0)

    return None


def _collect_doc_assertions(doc_files: list[DiscoveredFile]) -> list[dict]:
    assertions: list[dict] = []
    for doc_file in doc_files:
        try:
            content = doc_file.abs_path.read_text(errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(content.splitlines(), start=1):
            extracted = _extract_line_value(line)
            if extracted is None:
                continue
            kind, normalized, raw = extracted
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


def find_configuration_discrepancies(discovered: list[DiscoveredFile]) -> list[dict]:
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
    config_files = [f for f in discovered if f.role == "config"]
    doc_files = [f for f in discovered if f.role == "docs"]
    if not config_files or not doc_files:
        return []

    doc_assertions = _collect_doc_assertions(doc_files)
    if not doc_assertions:
        return []

    findings: list[dict] = []
    seen: set[tuple[str, int, str, int]] = set()

    for config_file in config_files:
        try:
            content = config_file.abs_path.read_text(errors="replace")
        except OSError:
            continue

        entries = _parse_config_entries(config_file.path, content)

        for key, raw_value, lineno in entries:
            key_lower = key.lower()
            if any(sub in key_lower for sub in _SECRET_KEY_SUBSTRINGS):
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
#   - only two term sources: literal task terms, and multi-word concepts
#     decomposed from Python def/class names and config key paths. A
#     single generic word is never enough on its own to be "a concept" --
#     same discipline as configuration_discrepancy's two-token rule --
#     except for a task term itself, which is already a deliberate,
#     non-generic word by the time it reaches here (stopwords and short
#     tokens are dropped by tokenize()).
#   - secret-looking config keys are never turned into a concept.
#
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


def _python_identifier_words(content: str) -> list[str] | None:
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

    Returns None if the content does not parse as Python (the caller
    falls back to whole-text tokenization for non-Python test files).
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None

    words: list[str] = []
    for node in ast.walk(tree):
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


def _test_reference_stems(test_files: list[DiscoveredFile]) -> set[str]:
    """Every word (stemmed) that a discovered test file actually
    references as *code* -- see `_python_identifier_words`. Non-Python
    test files fall back to whole-text tokenization, since there is no
    code/prose distinction to draw there.
    """
    stems: set[str] = set()
    for test_file in test_files:
        try:
            content = test_file.abs_path.read_text(errors="replace")
        except OSError:
            continue

        words = _python_identifier_words(content) if test_file.path.endswith(".py") else None
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


def _source_symbol_concepts(content: str) -> list[tuple[list[str], int]]:
    """Multi-word concepts decomposed from Python def/class names, e.g.
    `is_session_expired` -> ["session", "expired"] (the filler word "is"
    is dropped). Single-word names never produce a concept here -- see
    module docstring.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []

    concepts: list[tuple[list[str], int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            words = _meaningful_words(_split_identifier_words(node.name))
            if len(words) >= _MIN_CONCEPT_WORDS:
                concepts.append((words, node.lineno))
    return concepts


def _config_key_concepts(path: str, content: str) -> list[tuple[list[str], int]]:
    """Multi-word concepts decomposed from config key paths, e.g.
    `session.timeout_minutes` -> ["session", "timeout", "minutes"].
    Secret-looking keys are skipped entirely, same defense-in-depth
    posture as configuration_discrepancy.
    """
    concepts: list[tuple[list[str], int]] = []
    for key, _value, lineno in _parse_config_entries(path, content):
        key_lower = key.lower()
        if any(sub in key_lower for sub in _SECRET_KEY_SUBSTRINGS):
            continue
        words: list[str] = []
        for segment in key.split("."):
            words.extend(_split_identifier_words(segment))
        words = _meaningful_words(words)
        if len(words) >= _MIN_CONCEPT_WORDS:
            concepts.append((words, lineno))
    return concepts


def find_test_reference_gaps(items: list, discovered: list[DiscoveredFile], task: str) -> list[dict]:
    """Find task terms and config keys with real presence in the
    *selected* source/config files (`items`) that no discovered test file
    references.

    `items` is the list of ContextItem produced by SELECT for this task
    -- only files SELECT actually chose are scanned for terms, per the
    v0.1 plan. `discovered` supplies the full set of discovered files, so
    every discovered test file (selected or not) can be checked for a
    reference, matching the "no discovered test file references them"
    wording in the plan.

    Returns a list of finding dicts, each carrying at minimum: "rule"
    (the rule id), "term" (the concept, human-readable), "path"/"line"
    (where the term is referenced in source/config -- the evidence for
    the gap), and a factual "message". Returns [] when nothing meets the
    bar above -- never a "nothing missing" placeholder entry, and never
    a finding when there are no discovered test files at all (see module
    docstring).
    """
    test_files = [f for f in discovered if f.role == "test"]
    if not test_files:
        return []

    selected = [item for item in items if item.role in ("source", "config")]
    if not selected:
        return []

    test_stems = _test_reference_stems(test_files)
    discovered_index = {f.path: f for f in discovered}
    terms = tokenize(task)

    # concept key (sorted unique words) -> (display words, path, line) for
    # the first occurrence encountered, so each concept is reported once.
    concepts: dict[tuple[str, ...], tuple[list[str], str, int]] = {}

    def _record(words: list[str], path: str, lineno: int) -> None:
        key = tuple(sorted(set(words)))
        if key and key not in concepts:
            concepts[key] = (words, path, lineno)

    for item in selected:
        discovered_file = discovered_index.get(item.path)
        if discovered_file is None:
            continue
        try:
            content = discovered_file.abs_path.read_text(errors="replace")
        except OSError:
            continue
        if not content:
            continue

        # -- task terms with real (literal) presence in this file --
        content_lower = content.lower()
        lines = content.splitlines()
        for term in terms:
            if term not in content_lower:
                continue
            lineno = next(
                (idx for idx, line in enumerate(lines, start=1) if term in line.lower()),
                1,
            )
            _record([term], item.path, lineno)

        # -- def/class names and config keys defined in this file --
        if item.role == "source" and item.path.endswith(".py"):
            for words, lineno in _source_symbol_concepts(content):
                _record(words, item.path, lineno)
        elif item.role == "config":
            for words, lineno in _config_key_concepts(item.path, content):
                _record(words, item.path, lineno)

    findings: list[dict] = []
    for words, path, lineno in concepts.values():
        if _concept_referenced(words, test_stems):
            continue
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
