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
