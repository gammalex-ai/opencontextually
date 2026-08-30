"""Step 11 hardening: get_context() and discover() must degrade gracefully
-- never a traceback, never an unexplained empty shell of output -- on the
inputs a real filesystem can actually produce: an empty project, a huge
file, a deeply nested tree, non-UTF8 content, a broken symlink, a
permission-denied directory, and a task that matches nothing.
"""

from __future__ import annotations

import os
import sys

import pytest

from opencontextually import get_context
from opencontextually.discovery import MAX_FILE_BYTES, discover


def _write(path, content=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# --- empty project -----------------------------------------------------


def test_empty_project_produces_clean_message(tmp_path):
    package = get_context("do anything", root=tmp_path)

    assert package.included == []
    assert package.conflicts == []
    assert package.missing == []
    rendered = package.render()
    assert "No relevant context found for this task." in rendered
    assert "Traceback" not in rendered


# --- a huge single file --------------------------------------------------


def test_huge_file_is_excluded_not_crashed_on(tmp_path):
    _write(tmp_path / "src" / "small.py", "def handle_widget():\n    pass\n")
    huge = tmp_path / "src" / "huge.py"
    huge.parent.mkdir(parents=True, exist_ok=True)
    with open(huge, "w") as f:
        f.write("# widget data\n")
        # Well over MAX_FILE_BYTES, written in chunks rather than one huge
        # in-memory string.
        chunk = "x = 'widget'  # padding line to inflate file size\n"
        for _ in range((MAX_FILE_BYTES // len(chunk)) + 100):
            f.write(chunk)

    assert huge.stat().st_size > MAX_FILE_BYTES

    discovered, reasons = discover(tmp_path)
    assert reasons["oversize"] >= 1
    assert "src/huge.py" not in {f.path for f in discovered}

    package = get_context("fix the widget", root=tmp_path)
    assert any(item.path == "src/small.py" for item in package.included)


# --- deeply nested trees --------------------------------------------------


def test_deeply_nested_tree_is_discovered(tmp_path):
    deep = tmp_path
    for i in range(60):
        deep = deep / f"level{i}"
    _write(deep / "widget.py", "def handle_widget():\n    pass\n")

    discovered, _reasons = discover(tmp_path)
    paths = {f.path for f in discovered}
    assert any(p.endswith("widget.py") for p in paths)

    package = get_context("fix the widget", root=tmp_path)
    assert any(item.path.endswith("widget.py") for item in package.included)


# --- non-UTF8 files --------------------------------------------------------


def test_non_utf8_file_does_not_crash(tmp_path):
    bad = tmp_path / "src" / "legacy.py"
    bad.parent.mkdir(parents=True, exist_ok=True)
    # Latin-1 bytes that are not valid UTF-8 (0x80-0x9F range), no null
    # byte, so the binary sniff lets it through as "text".
    with open(bad, "wb") as f:
        f.write(b"# widget config \x93legacy\x94 handling\ndef handle_widget():\n    pass\n")

    discovered, _reasons = discover(tmp_path)
    assert "src/legacy.py" in {f.path for f in discovered}

    package = get_context("fix the widget", root=tmp_path)
    assert package.included  # did not raise, produced something


# --- broken symlinks ---------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need admin on Windows")
def test_broken_symlink_is_skipped_not_crashed_on(tmp_path):
    _write(tmp_path / "src" / "widget.py", "def handle_widget():\n    pass\n")
    broken = tmp_path / "src" / "broken_link.py"
    broken.symlink_to(tmp_path / "src" / "does_not_exist.py")

    discovered, _reasons = discover(tmp_path)
    paths = {f.path for f in discovered}
    assert "src/widget.py" in paths
    assert "src/broken_link.py" not in paths

    package = get_context("fix the widget", root=tmp_path)
    assert any(item.path == "src/widget.py" for item in package.included)


# --- permission-denied directories -----------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="posix permission bits")
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores directory permission bits",
)
def test_permission_denied_directory_is_skipped_not_crashed_on(tmp_path):
    _write(tmp_path / "src" / "widget.py", "def handle_widget():\n    pass\n")
    locked = tmp_path / "src" / "locked"
    _write(locked / "secret_stuff.py", "def handle_widget_secret():\n    pass\n")
    locked.chmod(0o000)

    try:
        discovered, _reasons = discover(tmp_path)
        paths = {f.path for f in discovered}
        assert "src/widget.py" in paths
        assert not any(p.startswith("src/locked/") for p in paths)

        package = get_context("fix the widget", root=tmp_path)
        assert any(item.path == "src/widget.py" for item in package.included)
    finally:
        locked.chmod(0o755)


# --- a task matching nothing at all ----------------------------------------


def test_task_matching_nothing_produces_clean_message(tmp_path):
    _write(tmp_path / "src" / "widget.py", "def handle_widget():\n    pass\n")

    package = get_context("zzz qqq nonexistent xylophone", root=tmp_path)

    assert package.included == []
    assert package.conflicts == []
    assert package.missing == []
    rendered = package.render()
    assert "No relevant context found for this task." in rendered
    assert "Traceback" not in rendered
    # Not an empty shell: the exclusion summary and checks footer are
    # still present and informative.
    assert "Excluded" in rendered
    assert "Checks run" in rendered
