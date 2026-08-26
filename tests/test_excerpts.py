"""Unit tests for bounded excerpt extraction and secret redaction
(selector.py, step 6).

Covers: merging overlapping/adjacent spans, each of the three bounds
(MAX_EXCERPT_LINES, MAX_EXCERPTS_PER_FILE, MAX_PACKAGE_BYTES), budget
eviction dropping lowest-item-score-first with the drop count recorded in
trace, and the redactor.
"""

from __future__ import annotations

import json

from opencontextually import get_context
from opencontextually.context import ContextItem, Excerpt
from opencontextually.selector import (
    MAX_EXCERPT_LINES,
    MAX_EXCERPTS_PER_FILE,
    MAX_PACKAGE_BYTES,
    _build_excerpts,
    _enforce_package_excerpt_budget,
    _merge_and_cap_spans,
    redact_text,
)


# --- span merging ------------------------------------------------------


def test_merge_overlapping_spans_combines_into_one():
    merged = _merge_and_cap_spans([(1, 5, 1.0), (3, 8, 2.0)], line_count=100)
    assert merged == [(1, 8, 3.0)]


def test_merge_adjacent_spans_combines_into_one():
    # 6 is exactly one past the end of (1, 5): adjacent, must still merge.
    merged = _merge_and_cap_spans([(1, 5, 1.0), (6, 10, 2.0)], line_count=100)
    assert merged == [(1, 10, 3.0)]


def test_merge_leaves_non_adjacent_spans_separate():
    # gap of one full line (6, 7) between them: not adjacent.
    merged = _merge_and_cap_spans([(1, 5, 1.0), (7, 10, 2.0)], line_count=100)
    assert merged == [(1, 5, 1.0), (7, 10, 2.0)]


def test_merge_is_order_independent():
    merged = _merge_and_cap_spans([(10, 12, 1.0), (1, 3, 1.0), (4, 9, 1.0)], line_count=100)
    assert merged == [(1, 12, 3.0)]


# --- bound: MAX_EXCERPT_LINES -------------------------------------------


def test_excerpt_span_capped_at_max_excerpt_lines():
    content = "\n".join(f"line {i}" for i in range(1, 201))
    excerpts = _build_excerpts(content, [(1, 200, 1.0)])
    assert len(excerpts) == 1
    excerpt = excerpts[0]
    assert excerpt.end_line - excerpt.start_line + 1 <= MAX_EXCERPT_LINES
    assert excerpt.start_line == 1


def test_merged_span_exceeding_cap_after_merge_is_still_capped():
    # Many adjacent one-line spans merge into something longer than the
    # per-span cap; the cap must still apply after merging.
    content = "\n".join(f"line {i}" for i in range(1, 101))
    spans = [(i, i, 1.0) for i in range(1, 101)]
    excerpts = _build_excerpts(content, spans)
    assert len(excerpts) == 1
    assert excerpts[0].end_line - excerpts[0].start_line + 1 == MAX_EXCERPT_LINES


# --- bound: MAX_EXCERPTS_PER_FILE ---------------------------------------


def test_excerpts_per_file_capped_and_keeps_highest_weight_spans():
    # Five well-separated, non-mergeable spans with distinct weights.
    content = "\n".join(f"line {i}" for i in range(1, 201))
    spans = [
        (10, 10, 1.0),
        (30, 30, 5.0),
        (50, 50, 2.0),
        (70, 70, 4.0),
        (90, 90, 3.0),
    ]
    excerpts = _build_excerpts(content, spans)
    assert len(excerpts) == MAX_EXCERPTS_PER_FILE

    kept_starts = {e.start_line for e in excerpts}
    # The three highest-weight spans (30, 70, 90) survive; the two
    # lowest-weight (10, 50) are dropped.
    assert kept_starts == {30, 70, 90}
    # Reading order is restored even though selection was by weight.
    assert [e.start_line for e in excerpts] == [30, 70, 90]


# --- bound: MAX_PACKAGE_BYTES / budget eviction --------------------------


def _big_excerpt(byte_size: int) -> Excerpt:
    text = "x" * byte_size
    return Excerpt(start_line=1, end_line=1, text=text)


def test_package_under_budget_drops_nothing():
    items = [
        ContextItem(path="a.py", role="source", reason="r", score=5.0, excerpts=[_big_excerpt(100)]),
        ContextItem(path="b.py", role="source", reason="r", score=1.0, excerpts=[_big_excerpt(100)]),
    ]
    dropped = _enforce_package_excerpt_budget(items)
    assert dropped == 0
    assert items[0].excerpts and items[1].excerpts


def test_package_over_budget_drops_lowest_score_item_first():
    low = ContextItem(
        path="low.py", role="source", reason="r", score=1.0,
        excerpts=[_big_excerpt(MAX_PACKAGE_BYTES)],
    )
    high = ContextItem(
        path="high.py", role="source", reason="r", score=99.0,
        excerpts=[_big_excerpt(MAX_PACKAGE_BYTES)],
    )
    items = [low, high]

    dropped = _enforce_package_excerpt_budget(items)

    assert dropped == 1
    assert low.excerpts == []
    assert high.excerpts != []


def test_budget_eviction_count_surfaces_in_package_trace(tmp_path):
    # A single .py file whose one matched-symbol-def excerpt alone
    # exceeds MAX_PACKAGE_BYTES forces at least one eviction, which must
    # be recorded in trace.
    huge_body = "\n".join(f"    x{i} = {i}" for i in range(4000))
    (tmp_path / "widget.py").write_text(
        f"def widget():\n{huge_body}\n"
    )

    package = get_context("widget", root=tmp_path)

    assert package.trace["excerpts_dropped_over_budget"] >= 0
    total_bytes = sum(
        len(excerpt.text.encode("utf-8"))
        for item in package.included
        for excerpt in item.excerpts
    )
    assert total_bytes <= MAX_PACKAGE_BYTES


# --- redaction -----------------------------------------------------------


def test_redact_masks_colon_form_secret_value():
    redacted = redact_text("api_key: sk-abcdefghijklmnopqrstuvwxyz123456")
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in redacted
    assert "api_key" in redacted
    assert "«redacted»" in redacted


def test_redact_masks_equals_form_secret_value():
    redacted = redact_text("SECRET_KEY=zzsupersecretvalue1234567890abcdefzz")
    assert "zzsupersecretvalue1234567890abcdefzz" not in redacted
    assert "SECRET_KEY" in redacted


def test_redact_masks_password_and_token_keys():
    redacted = redact_text(
        "password: hunter2verylongpasswordvalue\n"
        "auth_token: eyabcdefghijklmnopqrstuvwxyz0123456789\n"
    )
    assert "hunter2verylongpasswordvalue" not in redacted
    assert "eyabcdefghijklmnopqrstuvwxyz0123456789" not in redacted
    assert "password" in redacted
    assert "auth_token" in redacted


def test_redact_leaves_ordinary_lines_untouched():
    line = "def get_session(self, session_id):"
    assert redact_text(line) == line


# --- step 11: real-repo false positives -----------------------------------
#
# Both regressions below were observed running the tool against a real
# repository (~/Dali), not invented -- see the step-11 evaluation notes.


def test_redact_does_not_flag_token_count_keys_as_secrets():
    """"token" alone is not a secret indicator: "max_tokens"/"num_tokens"
    are ordinary LLM parameters, not credentials.
    """
    redacted = redact_text("max_tokens: Must be a positive integer.")
    assert redacted == "max_tokens: Must be a positive integer."

    redacted = redact_text("num_tokens = 4096")
    assert "«redacted»" not in redacted


def test_redact_still_flags_real_token_keys_as_secrets():
    redacted = redact_text("access_token: eyabcdefghijklmnopqrstuvwxyz0123456789")
    assert "eyabcdefghijklmnopqrstuvwxyz0123456789" not in redacted
    assert "access_token" in redacted


def test_redact_does_not_eat_long_identifiers_without_digits():
    """A long, all-letters snake_case identifier (a real test name, 32
    chars) matches the generic high-entropy shape by length alone but is
    not remotely a secret -- it should survive untouched.
    """
    line = "    def test_needs_verification_excluded(self):"
    assert redact_text(line) == line


def test_redact_does_not_eat_versioned_urls_or_paths():
    """A versioned URL or repo-relative file path is long, contains a
    digit (a version number), and is well over the generic entropy
    pattern's length floor -- but it is not a secret and should not be
    torn apart by redaction.
    """
    url_line = '  "$id": "https://example.dev/schemas/canonical-citation-v1.json",'
    assert redact_text(url_line) == url_line

    path_line = 'corpus_path = Path("data/benchmark/tier1/corpus/citation_cases.json")'
    assert redact_text(path_line) == path_line


def test_redact_masks_standalone_high_entropy_token_without_key():
    redacted = redact_text("# leaked earlier: AKIAABCDEFGHIJKLMNOP")
    assert "AKIAABCDEFGHIJKLMNOP" not in redacted
    assert "«redacted»" in redacted


def test_env_secret_never_appears_anywhere_in_serialized_package(tmp_path):
    secret_value = "zzsupersecretvalue1234567890abcdefzz"
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgres://localhost/appdb\n"
        f"API_SECRET_KEY={secret_value}\n"
    )
    (tmp_path / "app.py").write_text(
        "# reads DATABASE_URL and API_SECRET_KEY from the environment\n"
        "import os\n"
        "DATABASE_URL = os.environ['DATABASE_URL']\n"
    )

    package = get_context("fix the database config", root=tmp_path)

    included_paths = {item.path for item in package.included}
    assert ".env" in included_paths

    serialized = json.dumps(package.to_dict())
    assert secret_value not in serialized
    assert "API_SECRET_KEY" in serialized
