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

import json
import math
import re

from .discovery import DiscoveredFile

RULE_ID = "configuration_discrepancy"

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
