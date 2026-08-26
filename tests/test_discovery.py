import os

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
