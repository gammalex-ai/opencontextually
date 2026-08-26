import subprocess
import sys

from opencontextually.discovery import discover
from opencontextually.selector import select, tokenize


def _write(path, content=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_tokenize_splits_camel_case():
    assert tokenize("fixAuthBug") == ["auth"]


def test_tokenize_splits_snake_case():
    assert tokenize("fix_auth_bug") == ["auth"]


def test_tokenize_drops_stopwords_and_short_tokens():
    tokens = tokenize("fix the bug in a session, to be or not to be")
    assert "the" not in tokens
    assert "in" not in tokens
    assert "a" not in tokens
    assert "be" not in tokens
    assert "or" not in tokens
    assert "session" in tokens


def test_tokenize_drops_tokens_under_three_chars():
    tokens = tokenize("fix ui io session")
    assert "ui" not in tokens
    assert "io" not in tokens
    assert "session" in tokens


def test_tokenize_lowercases():
    assert tokenize("Session") == ["session"]


def test_scoring_ranks_filename_and_symbol_matches_above_content_only(tmp_path):
    _write(
        tmp_path / "src" / "auth" / "session.py",
        "class SessionManager:\n    pass\n",
    )
    _write(
        tmp_path / "src" / "unrelated.py",
        "# this file talks about session a couple times\n"
        "# session session\n",
    )
    _write(tmp_path / "src" / "other.py", "def noop():\n    pass\n")

    discovered, _reasons = discover(tmp_path)
    items, _extra = select(discovered, "fix the session bug")

    paths_in_order = [item.path for item in items]
    assert "src/auth/session.py" in paths_in_order
    assert "src/unrelated.py" in paths_in_order
    assert "src/other.py" not in paths_in_order

    # filename + symbol match should outrank content-only mentions
    assert paths_in_order.index("src/auth/session.py") < paths_in_order.index(
        "src/unrelated.py"
    )

    session_item = next(i for i in items if i.path == "src/auth/session.py")
    assert "session" in session_item.reason.lower()


# --- step 11: real-repo tuning ---------------------------------------------
#
# Observed running the tool against a real, data-heavy repository
# (~/Dali): a large benchmark-results JSON file outranked the source file
# that actually implements the task, purely on repeated-content volume.


def test_data_directory_file_is_down_weighted_below_real_source(tmp_path):
    _write(
        tmp_path / "src" / "scoring.py",
        "def score_citation():\n    '''existence scoring'''\n    pass\n",
    )
    # A file under a recognizable data/ directory that happens to mention
    # the task terms just as much (or more) should not outrank real source.
    _write(
        tmp_path / "data" / "results" / "citation_scoring_dump.json",
        '{"citation": "scoring", "citation_scoring": "scoring citation scoring"}\n',
    )

    discovered, _reasons = discover(tmp_path)
    items, _extra = select(discovered, "fix citation scoring")

    paths_in_order = [item.path for item in items]
    assert "src/scoring.py" in paths_in_order
    if "data/results/citation_scoring_dump.json" in paths_in_order:
        assert paths_in_order.index("src/scoring.py") < paths_in_order.index(
            "data/results/citation_scoring_dump.json"
        )


def test_large_file_content_frequency_is_damped(tmp_path):
    from opencontextually.selector import LARGE_FILE_BYTES, score_file, tokenize

    small_content = "widget widget widget\n"
    _write(tmp_path / "small.txt", small_content)

    # Same repeated-term density, but padded well past LARGE_FILE_BYTES.
    padding = "widget " * ((LARGE_FILE_BYTES // len("widget ")) + 1000)
    _write(tmp_path / "large.txt", padding)

    discovered, _reasons = discover(tmp_path)
    small_file = next(f for f in discovered if f.path == "small.txt")
    large_file = next(f for f in discovered if f.path == "large.txt")
    assert large_file.size > LARGE_FILE_BYTES

    terms = tokenize("fix the widget")
    small_score = score_file(small_file, terms)
    large_score = score_file(large_file, terms)

    # The large file mentions "widget" far more often in absolute terms,
    # but the per-file content-frequency score is capped and damped, so it
    # should not run away to a wildly larger score than the small file.
    assert large_score < small_score * 5


def test_select_caps_at_max_seeds(tmp_path):
    from opencontextually.selector import MAX_SEEDS

    for i in range(MAX_SEEDS + 5):
        _write(tmp_path / f"auth_{i}.py", "# auth file\n")

    discovered, _reasons = discover(tmp_path)
    items, extra = select(discovered, "fix the auth bug")

    assert len(items) == MAX_SEEDS
    assert extra["over_cap"] == 5


def test_select_ignores_syntax_error_python_file(tmp_path):
    _write(tmp_path / "broken_auth.py", "def broken(:\n    this is not python\n")

    discovered, _reasons = discover(tmp_path)
    # Should not raise despite the SyntaxError during ast.parse.
    items, _extra = select(discovered, "fix the auth bug")
    assert any(item.path == "broken_auth.py" for item in items)


def test_included_is_ranked_by_score_across_seeds_and_expanded_items(tmp_path):
    # Regression test for a ranking bug: expanded (transitively-reached)
    # items were appended after all seeds regardless of score, instead of
    # being merged into the ranking. middleware.py imports session.py;
    # session.py's decayed score can still land above some low-scoring
    # seeds, and it must be ranked accordingly, not stuck at the end.
    _write(
        tmp_path / "src" / "auth" / "middleware.py",
        "from src.users.session import SessionStore\n\n"
        "class AuthenticationError(Exception):\n    pass\n",
    )
    _write(
        tmp_path / "src" / "users" / "session.py",
        "class SessionStore:\n    pass\n",
    )
    # A weak seed: content-only match, no filename or symbol hit, so it
    # scores far below both middleware.py and the decayed session.py.
    _write(
        tmp_path / "docs" / "notes.md",
        "authentication is mentioned here exactly once.\n",
    )

    discovered, _reasons = discover(tmp_path)
    items, _extra = select(discovered, "fix the authentication bug")

    scores = [item.score for item in items]
    assert scores == sorted(scores, reverse=True), scores

    paths_in_order = [item.path for item in items]
    assert "src/users/session.py" in paths_in_order
    assert "docs/notes.md" in paths_in_order
    # session.py (import-reached from a strong seed) must outrank the
    # weak content-only seed, even though it was appended after seeds by
    # the old (buggy) code.
    assert paths_in_order.index("src/users/session.py") < paths_in_order.index(
        "docs/notes.md"
    )


# --- bug fix: test files systematically outrank the source they test ------
#
# Observed against a fresh clone of psf/requests with the task "fix
# redirect handling when a session cookie is set": tests/test_requests.py
# scored 405.2 (dominated by dozens of `def test_<scenario>` names that
# each match multiple task terms) against src/requests/sessions.py's 55.5
# -- the file a developer would actually open. See TEST_SIGNAL_DAMPING in
# selector.py.


def test_source_outranks_test_file_that_mentions_terms_more_often(tmp_path):
    # The test file mentions "session" and "cookie" far more densely than
    # the source file -- many small test cases, each describing the
    # feature in task vocabulary -- exactly the shape that used to win on
    # raw content/symbol frequency alone.
    _write(
        tmp_path / "src" / "sessions.py",
        "class SessionRedirectMixin:\n"
        "    '''Handles redirect while carrying the session cookie.'''\n"
        "    def resolve_redirects(self):\n"
        "        '''Resolve redirects, preserving the session cookie.'''\n"
        "        pass\n",
    )
    test_defs = "\n".join(
        f"def test_session_cookie_redirect_case_{i}():\n    pass\n" for i in range(6)
    )
    _write(tmp_path / "tests" / "test_sessions.py", test_defs)

    discovered, _reasons = discover(tmp_path)
    items, _extra = select(discovered, "fix redirect handling when a session cookie is set")

    paths_in_order = [item.path for item in items]
    assert "src/sessions.py" in paths_in_order
    assert "tests/test_sessions.py" in paths_in_order
    assert paths_in_order.index("src/sessions.py") < paths_in_order.index(
        "tests/test_sessions.py"
    )


def test_test_file_still_included_not_dropped(tmp_path):
    # Damping must lower a test file's rank, not remove it from the
    # package -- tests are legitimate, useful context.
    _write(
        tmp_path / "src" / "sessions.py",
        "class SessionRedirectMixin:\n    pass\n",
    )
    test_defs = "\n".join(
        f"def test_session_cookie_redirect_case_{i}():\n    pass\n" for i in range(6)
    )
    _write(tmp_path / "tests" / "test_sessions.py", test_defs)

    discovered, _reasons = discover(tmp_path)
    items, _extra = select(discovered, "fix redirect handling when a session cookie is set")

    paths_in_order = [item.path for item in items]
    assert "tests/test_sessions.py" in paths_in_order


def test_auth_bug_demo_ordering_survives_test_damping(tmp_path):
    # The flagship demo's ordering must not regress: middleware.py first,
    # session.py surfaced via the import edge, and test_auth.py still
    # included even though it now scores lower relative to source.
    from opencontextually import get_context

    package = get_context("fix the authentication bug", root="examples/auth_bug")
    paths_in_order = [item.path for item in package.included]

    assert "src/auth/middleware.py" in paths_in_order
    assert "src/users/session.py" in paths_in_order
    assert "tests/test_auth.py" in paths_in_order

    session_item = next(i for i in package.included if i.path == "src/users/session.py")
    assert any(
        "middleware.py imports src/users/session.py" in edge for edge in session_item.provenance
    )

    conflict_settings = " ".join(c.get("setting", "") for c in package.conflicts)
    assert "timeout_minutes" in conflict_settings

    missing_messages = " ".join(m.get("message", "") for m in package.missing)
    assert "session expired" in missing_messages.lower()


def test_cli_exits_2_on_bad_root(tmp_path):
    bad_root = tmp_path / "does_not_exist"
    result = subprocess.run(
        [sys.executable, "-m", "opencontextually.cli", "fix the bug", "--root", str(bad_root)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


def test_cli_exits_0_and_prints_render_on_good_root(tmp_path):
    _write(tmp_path / "auth.py", "class AuthHandler:\n    pass\n")

    result = subprocess.run(
        [sys.executable, "-m", "opencontextually.cli", "fix the auth bug", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "auth.py" in result.stdout
    assert "Task: fix the auth bug" in result.stdout
