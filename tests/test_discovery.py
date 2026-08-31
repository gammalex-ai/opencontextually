import os
from pathlib import Path

import pytest

from opencontextually.discovery import discover


def _write(path, content=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _paths(discovered):
    return {f.path for f in discovered}


def test_gitignore_negation(tmp_path):
    _write(tmp_path / ".gitignore", "*.log\n!keep.log\n")
    _write(tmp_path / "a.log", "drop me")
    _write(tmp_path / "keep.log", "keep me")

    discovered, reasons = discover(tmp_path)

    assert "keep.log" in _paths(discovered)
    assert "a.log" not in _paths(discovered)
    assert reasons["ignored"] == 1


def test_gitignore_double_star(tmp_path):
    _write(tmp_path / ".gitignore", "**/build/**\n")
    _write(tmp_path / "src" / "build" / "out.txt", "generated")
    _write(tmp_path / "src" / "main.py", "print(1)")

    discovered, reasons = discover(tmp_path)

    assert "src/main.py" in _paths(discovered)
    assert "src/build/out.txt" not in _paths(discovered)
    assert reasons["ignored"] >= 1


def test_gitignore_directory_only_pattern(tmp_path):
    # trailing slash means "dist" only matches a directory, not a file
    # named "dist"
    _write(tmp_path / ".gitignore", "dist/\n")
    _write(tmp_path / "dist" / "bundle.js", "var x = 1;")
    _write(tmp_path / "dist_notes.txt", "not a dir")

    discovered, reasons = discover(tmp_path)

    assert "dist/bundle.js" not in _paths(discovered)
    assert "dist_notes.txt" in _paths(discovered)


def test_gitignore_anchored_vs_unanchored(tmp_path):
    # "/only_root.txt" anchors to the root; "anywhere.txt" (no slash)
    # matches at any depth.
    _write(tmp_path / ".gitignore", "/only_root.txt\nanywhere.txt\n")
    _write(tmp_path / "only_root.txt", "root only")
    _write(tmp_path / "sub" / "only_root.txt", "should survive, not anchored to sub")
    _write(tmp_path / "anywhere.txt", "top level")
    _write(tmp_path / "sub" / "anywhere.txt", "nested")

    discovered, reasons = discover(tmp_path)
    found = _paths(discovered)

    assert "only_root.txt" not in found
    assert "sub/only_root.txt" in found
    assert "anywhere.txt" not in found
    assert "sub/anywhere.txt" not in found


def test_opencontextuallyignore_honored(tmp_path):
    _write(tmp_path / ".opencontextuallyignore", "secret_stuff/\n")
    _write(tmp_path / "secret_stuff" / "data.txt", "shh")
    _write(tmp_path / "public.txt", "hello")

    discovered, reasons = discover(tmp_path)
    found = _paths(discovered)

    assert "secret_stuff/data.txt" not in found
    assert "public.txt" in found


def test_dotfiles_are_discoverable(tmp_path):
    _write(tmp_path / ".env", "SECRET=1")
    _write(tmp_path / ".github" / "workflows" / "ci.yml", "name: ci")

    discovered, reasons = discover(tmp_path)
    found = _paths(discovered)

    assert ".env" in found
    assert ".github/workflows/ci.yml" in found


def test_git_directory_excluded(tmp_path):
    _write(tmp_path / ".git" / "config", "[core]")
    _write(tmp_path / "real.py", "print(1)")

    discovered, reasons = discover(tmp_path)
    found = _paths(discovered)

    assert not any(p.startswith(".git/") for p in found)
    assert "real.py" in found


def test_binary_file_skipped(tmp_path):
    (tmp_path / "image.bin").write_bytes(b"\x89PNG\x00\x01\x02\x03")
    _write(tmp_path / "text.py", "print('hi')")

    discovered, reasons = discover(tmp_path)
    found = _paths(discovered)

    assert "image.bin" not in found
    assert "text.py" in found
    assert reasons["binary"] == 1


def test_oversize_file_skipped(tmp_path):
    from opencontextually.discovery import MAX_FILE_BYTES

    big = tmp_path / "big.txt"
    big.write_text("x" * (MAX_FILE_BYTES + 1))
    _write(tmp_path / "small.txt", "small")

    discovered, reasons = discover(tmp_path)
    found = _paths(discovered)

    assert "big.txt" not in found
    assert "small.txt" in found
    assert reasons["oversize"] == 1


def test_symlink_escape_refused(tmp_path):
    outside = tmp_path.parent / "outside_target"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("outside data")

    project = tmp_path / "project"
    project.mkdir()
    _write(project / "inside.txt", "inside data")

    try:
        (project / "escape").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported in this environment")

    discovered, reasons = discover(project)
    found = _paths(discovered)

    assert "inside.txt" in found
    assert not any(p.startswith("escape/") for p in found)


def test_symlink_file_escape_refused(tmp_path):
    outside = tmp_path.parent / "outside_file_target"
    outside.mkdir(exist_ok=True)
    outside_file = outside / "secret.txt"
    outside_file.write_text("outside data")

    project = tmp_path / "project2"
    project.mkdir()
    _write(project / "inside.txt", "inside data")

    try:
        (project / "link.txt").symlink_to(outside_file)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported in this environment")

    discovered, reasons = discover(project)
    found = _paths(discovered)

    assert "inside.txt" in found
    assert "link.txt" not in found


def test_works_in_non_git_directory(tmp_path):
    # No .git, no .gitignore at all.
    _write(tmp_path / "plain.py", "print('ok')")

    discovered, reasons = discover(tmp_path)
    found = _paths(discovered)

    assert "plain.py" in found


def test_nested_gitignore_scoped_to_its_own_subtree(tmp_path):
    # A nested .gitignore's patterns apply only under its own directory --
    # a sibling directory with a same-named file is unaffected.
    _write(tmp_path / "pkg_a" / ".gitignore", "local.txt\n")
    _write(tmp_path / "pkg_a" / "local.txt", "ignored here")
    _write(tmp_path / "pkg_b" / "local.txt", "not ignored here")

    discovered, reasons = discover(tmp_path)
    found = _paths(discovered)

    assert "pkg_a/local.txt" not in found
    assert "pkg_b/local.txt" in found


def test_nested_gitignore_overrides_root_gitignore(tmp_path):
    # A deeper .gitignore takes precedence over a shallower one -- here a
    # subdirectory re-includes (negates) a file the root .gitignore
    # excludes, matching real `git check-ignore` behavior.
    _write(tmp_path / ".gitignore", "keepme.txt\n")
    _write(tmp_path / "sub" / ".gitignore", "!keepme.txt\n")
    _write(tmp_path / "keepme.txt", "excluded at root")
    _write(tmp_path / "sub" / "keepme.txt", "re-included by nested gitignore")

    discovered, reasons = discover(tmp_path)
    found = _paths(discovered)

    assert "keepme.txt" not in found
    assert "sub/keepme.txt" in found


def test_git_info_exclude_honored(tmp_path):
    _write(tmp_path / ".git" / "info" / "exclude", "worktrees/\n")
    _write(tmp_path / "worktrees" / "wt1" / "file.py", "print(1)")
    _write(tmp_path / "real.py", "print(2)")

    discovered, reasons = discover(tmp_path)
    found = _paths(discovered)

    assert not any(p.startswith("worktrees/") for p in found)
    assert "real.py" in found
    assert reasons["ignored"] == 1


def test_root_gitignore_overrides_info_exclude(tmp_path):
    # Verified against real `git check-ignore -v`: root .gitignore beats
    # info/exclude regardless of which direction (exclude vs negate) each
    # one goes.
    _write(tmp_path / ".git" / "info" / "exclude", "!decided.txt\n")
    _write(tmp_path / ".gitignore", "decided.txt\n")
    _write(tmp_path / "decided.txt", "content")

    discovered, reasons = discover(tmp_path)
    assert "decided.txt" not in _paths(discovered)


def test_global_excludes_file_honored(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    global_ignore = home / "gitignore_global"
    global_ignore.write_text("*.globalskip\n")

    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    project = tmp_path / "project"
    _write(project / ".git" / "config", f"[core]\n\texcludesFile = {global_ignore}\n")
    _write(project / "drop.globalskip", "should be excluded")
    _write(project / "keep.txt", "should remain")

    discovered, reasons = discover(project)
    found = _paths(discovered)

    assert "drop.globalskip" not in found
    assert "keep.txt" in found
    assert reasons["ignored"] == 1


def test_global_excludes_file_from_xdg_git_config(tmp_path, monkeypatch):
    # core.excludesFile resolved via $XDG_CONFIG_HOME/git/config when
    # there is no repo-local or ~/.gitconfig setting.
    home = tmp_path / "home"
    home.mkdir()
    xdg = tmp_path / "xdgconfig"
    (xdg / "git").mkdir(parents=True)
    global_ignore = xdg / "my_global_ignore"
    global_ignore.write_text("*.xdgskip\n")
    (xdg / "git" / "config").write_text(f"[core]\nexcludesfile={global_ignore}\n")

    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    project = tmp_path / "project"
    _write(project / "drop.xdgskip", "should be excluded")
    _write(project / "keep.txt", "should remain")

    discovered, reasons = discover(project)
    found = _paths(discovered)

    assert "drop.xdgskip" not in found
    assert "keep.txt" in found


def test_info_exclude_negation_overrides_global_excludes(tmp_path, monkeypatch):
    # Verified against real `git check-ignore -v`: info/exclude beats the
    # global excludes file, in either direction.
    home = tmp_path / "home"
    home.mkdir()
    global_ignore = home / "global_ignore"
    global_ignore.write_text("reinclude.txt\n")

    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    project = tmp_path / "project"
    _write(project / ".git" / "config", f"[core]\nexcludesFile = {global_ignore}\n")
    _write(project / ".git" / "info" / "exclude", "!reinclude.txt\n")
    _write(project / "reinclude.txt", "content")

    discovered, reasons = discover(project)
    assert "reinclude.txt" in _paths(discovered)


def test_gitignore_negation_still_works_at_root(tmp_path):
    # Regression guard: the new multi-source combining logic must not
    # break ordinary single-file negation.
    _write(tmp_path / ".gitignore", "*.log\n!important.log\n")
    _write(tmp_path / "a.log", "drop")
    _write(tmp_path / "important.log", "keep")

    discovered, reasons = discover(tmp_path)
    found = _paths(discovered)

    assert "a.log" not in found
    assert "important.log" in found


def test_duplicate_files_collapse_to_shallower_non_vendor_path(tmp_path):
    content = "export function Applications() { return null; }\n"
    _write(tmp_path / "src" / "components" / "Applications.tsx", content)
    _write(tmp_path / ".claude" / "worktrees" / "wt1" / "src" / "components" / "Applications.tsx", content)
    _write(tmp_path / ".claude" / "worktrees" / "wt2" / "src" / "components" / "Applications.tsx", content)

    discovered, reasons = discover(tmp_path)
    found = {f.path: f for f in discovered}

    assert "src/components/Applications.tsx" in found
    assert ".claude/worktrees/wt1/src/components/Applications.tsx" not in found
    assert ".claude/worktrees/wt2/src/components/Applications.tsx" not in found
    assert reasons["duplicate"] == 2
    assert found["src/components/Applications.tsx"].duplicate_count == 2


def test_duplicate_prefers_shallower_path_over_deeper_non_vendor_path(tmp_path):
    content = "same content\n"
    _write(tmp_path / "a" / "b" / "c" / "note.txt", content)
    _write(tmp_path / "note.txt", content)

    discovered, reasons = discover(tmp_path)
    found = _paths(discovered)

    assert "note.txt" in found
    assert "a/b/c/note.txt" not in found
    assert reasons["duplicate"] == 1


def test_near_identical_files_are_both_kept(tmp_path):
    _write(tmp_path / "one.txt", "hello world\n")
    _write(tmp_path / "two.txt", "hello world!\n")

    discovered, reasons = discover(tmp_path)
    found = _paths(discovered)

    assert "one.txt" in found
    assert "two.txt" in found
    assert reasons["duplicate"] == 0


def test_empty_files_are_not_treated_as_duplicates(tmp_path):
    # Every empty file is trivially "byte-identical" -- collapsing them
    # would silently drop unrelated empty files (e.g. multiple package
    # __init__.py) that just happen to have no content yet.
    _write(tmp_path / "pkg_a" / "__init__.py", "")
    _write(tmp_path / "pkg_b" / "__init__.py", "")

    discovered, reasons = discover(tmp_path)
    found = _paths(discovered)

    assert "pkg_a/__init__.py" in found
    assert "pkg_b/__init__.py" in found
    assert reasons["duplicate"] == 0


def test_role_classification(tmp_path):
    _write(tmp_path / "src" / "app.py", "def f(): pass")
    _write(tmp_path / "tests" / "test_app.py", "def test_f(): pass")
    _write(tmp_path / "config" / "settings.yaml", "key: value")
    _write(tmp_path / "docs" / "guide.md", "# Guide")
    _write(tmp_path / "data.bin_ok", "not really binary but odd ext")

    discovered, reasons = discover(tmp_path)
    roles = {f.path: f.role for f in discovered}

    assert roles["src/app.py"] == "source"
    assert roles["tests/test_app.py"] == "test"
    assert roles["config/settings.yaml"] == "config"
    assert roles["docs/guide.md"] == "docs"


# --- bug fix: an invalid .gitignore pattern crashed the whole run ---------
#
# Found by running against psf/black, which ships
# `tests/data/invalid_gitignore_tests/.gitignore` containing a single `!`
# line (plus a nested variant) as fixtures for its own handling of malformed
# ignore files. `pathspec` raises GitIgnorePatternError("Invalid git pattern:
# '!'") for that line, and discover() let the exception escape -- so
# get_context() crashed outright with a traceback on a repository that
# `git status` is perfectly happy with.
#
# Git tolerates these lines; so must we. Discovery must never fail because of
# what a repository happens to contain -- an unreadable ignore rule is a
# reason to skip that rule, not to refuse to look at the project. The fix
# skips only the offending line and keeps every other pattern in the file,
# rather than discarding the whole file's rules (which would silently
# un-ignore directories the file legitimately excluded).


def test_invalid_root_gitignore_pattern_does_not_crash(tmp_path):
    (tmp_path / ".gitignore").write_text("!\n")
    (tmp_path / "app.py").write_text("x = 1\n")
    discovered, _reasons = discover(tmp_path)
    assert "app.py" in {f.path for f in discovered}


def test_invalid_nested_gitignore_pattern_does_not_crash(tmp_path):
    nested = tmp_path / "a"
    nested.mkdir()
    (nested / ".gitignore").write_text("!\n")
    (nested / "app.py").write_text("x = 1\n")
    discovered, _reasons = discover(tmp_path)
    assert "a/app.py" in {f.path for f in discovered}


def test_valid_patterns_survive_alongside_an_invalid_one(tmp_path):
    """One bad line must not discard the rest of the file's rules -- that
    would silently un-ignore whatever the valid patterns excluded.
    """
    (tmp_path / ".gitignore").write_text("!\nsecrets.txt\n")
    (tmp_path / "secrets.txt").write_text("token\n")
    (tmp_path / "app.py").write_text("x = 1\n")
    discovered, reasons = discover(tmp_path)
    paths = {f.path for f in discovered}
    assert "app.py" in paths
    assert "secrets.txt" not in paths, "the valid ignore rule was dropped too"
    assert reasons["ignored"] >= 1
