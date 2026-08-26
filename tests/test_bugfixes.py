"""Regression tests for four defects found evaluating OpenContextually
against a real Next.js/TypeScript repo (no LLM/auth/session code at all),
where `octx "fix the user login and session handling"` ranked a minified
SVG and a marketing bio as the #1 and #2 results instead of reporting no
relevant context.

1. word-boundary term matching (selector.has_word_match / count_word_matches)
2. excerpt character cap (selector.MAX_EXCERPT_CHARS / _truncate_chars)
3. minified/generated asset exclusion from content-frequency matching
   (selector._is_asset_like)
4. SCORE_THRESHOLD raised so incidental single-mention prose hits do not
   clear the bar
"""

from __future__ import annotations

from opencontextually import get_context
from opencontextually.discovery import discover
from opencontextually.selector import (
    MAX_EXCERPT_CHARS,
    TRUNCATION_MARKER,
    _is_asset_like,
    _truncate_chars,
    count_word_matches,
    has_word_match,
    select,
)


def _write(path, content=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# ==========================================================================
# Bug 1: word-boundary term matching
# ==========================================================================


def test_user_does_not_match_userspaceonuse():
    # The concrete failure: "user" in "userSpaceOnUse" is True as a plain
    # Python substring check, matching an SVG coordinate-system keyword
    # that has nothing to do with "user" as a task concept.
    assert not has_word_match("userSpaceOnUse", "user")


def test_user_matches_user_id():
    assert has_word_match("user_id", "user")


def test_user_matches_getuser():
    assert has_word_match("getUser", "user")


def test_user_matches_usersession():
    assert has_word_match("UserSession", "user")


def test_user_does_not_match_username_or_users():
    # A lowercase leading/embedded run with no real delimiter and no
    # capitalized boundary is exactly the ambiguous shape this fix targets.
    assert not has_word_match("username", "user")
    assert not has_word_match("users", "user")


def test_count_word_matches_counts_only_genuine_words():
    text = "user_id belongs to the user, not userSpaceOnUse or username"
    # Two genuine matches: "user_id" (delimited) and the standalone word
    # "user" -- "userSpaceOnUse" and "username" do not count.
    assert count_word_matches(text, "user") == 2


def test_scoring_does_not_match_bare_substring_in_identifier(tmp_path):
    # End-to-end: a file whose only "hit" is a bare substring inside a
    # longer identifier must not be selected on that basis.
    _write(
        tmp_path / "styles.svg",
        '<linearGradient gradientUnits="userSpaceOnUse"></linearGradient>\n',
    )
    _write(tmp_path / "auth" / "user_session.py", "class UserSession:\n    pass\n")

    discovered, _reasons = discover(tmp_path)
    items, _extra = select(discovered, "fix the user session")

    paths = {item.path for item in items}
    assert "auth/user_session.py" in paths
    assert "styles.svg" not in paths


# ==========================================================================
# Bug 2: excerpt bounds are line-based only -- add a character cap
# ==========================================================================


def test_truncate_chars_leaves_short_text_untouched():
    short = "a short line\n"
    assert _truncate_chars(short) == short


def test_truncate_chars_caps_a_single_very_long_line():
    huge_line = "x" * 50_000
    truncated = _truncate_chars(huge_line)
    assert len(truncated) <= MAX_EXCERPT_CHARS + len(TRUNCATION_MARKER)
    assert truncated.endswith(TRUNCATION_MARKER)


def test_excerpt_from_one_giant_line_is_bounded_end_to_end(tmp_path):
    # A single 4,000-character line among ordinary short ones -- kept
    # short on average so this file is not itself classified as a
    # minified asset (that is bug 3's job; this test is only about the
    # per-excerpt character cap). MAX_EXCERPT_LINES=40 does nothing to
    # bound a single such line on its own; the character cap must.
    short_lines = [f"x{i} = {i}  # widget" for i in range(20)]
    giant_line = "y = '" + ("A" * 4000) + "'  # widget config"
    _write(tmp_path / "blob.py", "\n".join(short_lines + [giant_line]) + "\n")

    package = get_context("fix the widget", root=tmp_path)

    item = next((i for i in package.included if i.path == "blob.py"), None)
    assert item is not None
    for excerpt in item.excerpts:
        assert len(excerpt.text) <= MAX_EXCERPT_CHARS + len(TRUNCATION_MARKER)


# ==========================================================================
# Bug 3: minified/generated assets scanned as prose
# ==========================================================================


def test_svg_file_is_asset_like():
    assert _is_asset_like("public/placeholder.svg", "<svg></svg>")


def test_minified_js_is_asset_like():
    assert _is_asset_like("static/app.min.js", "var a=1;")


def test_lockfile_is_asset_like():
    assert _is_asset_like("package-lock.json", '{"name": "x"}')


def test_high_average_line_length_is_asset_like():
    assert _is_asset_like("weird.txt", "x" * 5000)


def test_ordinary_python_source_is_not_asset_like():
    assert not _is_asset_like("src/session.py", "class Session:\n    pass\n")


def test_minified_asset_is_not_content_matched(tmp_path):
    # A minified/asset file that happens to contain a task term many times
    # in its content must not be selected on content alone -- only a
    # filename/path match should be able to pull it in.
    _write(
        tmp_path / "public" / "bundle.min.js",
        "var user=1,userAgain=2,user3=3;" * 50,
    )
    _write(tmp_path / "src" / "widget.py", "def handle_widget():\n    pass\n")

    discovered, _reasons = discover(tmp_path)
    items, _extra = select(discovered, "fix the user widget")

    paths = {item.path for item in items}
    assert "public/bundle.min.js" not in paths
    assert "src/widget.py" in paths


# ==========================================================================
# Bug 4: SCORE_THRESHOLD too permissive
# ==========================================================================


def test_single_incidental_prose_mention_does_not_clear_threshold(tmp_path):
    # The team.tsx-style false positive: a single "user experience" mention
    # in marketing prose, no filename/symbol signal, no role bonus.
    _write(
        tmp_path / "components" / "team.tsx",
        "export const bio = 'a passion for technology and user experience';\n",
    )

    discovered, _reasons = discover(tmp_path)
    items, _extra = select(discovered, "fix the user login and session handling")

    assert items == []


def test_repo_with_nothing_relevant_yields_clean_no_context_message(tmp_path):
    # End-to-end: a repo containing only an SVG asset with a coincidental
    # substring hit and prose with one incidental mention -- no real
    # login/session code at all -- must produce the clean message, not a
    # ranked (but bogus) result.
    _write(
        tmp_path / "public" / "placeholder.svg",
        '<svg><linearGradient gradientUnits="userSpaceOnUse"></linearGradient></svg>\n',
    )
    _write(
        tmp_path / "components" / "team.tsx",
        "export const bio = 'a passion for technology and user experience';\n",
    )

    package = get_context("fix the user login and session handling", root=tmp_path)

    assert package.included == []
    rendered = package.render()
    assert "No relevant context found for this task." in rendered
    assert "placeholder.svg" not in rendered
    assert "team.tsx" not in rendered
