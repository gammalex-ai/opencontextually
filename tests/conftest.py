"""Fail loudly if the tests are exercising a different checkout.

`pyproject.toml`'s `[tool.pytest.ini_options] pythonpath = ["src"]` makes a
bare `pytest` import this repository's own source. This guard is the belt to
that suspenders: if the setting is ever removed, overridden (`-p no:cacheprovider`
style config juggling, a stray `PYTHONPATH`, an old pytest without the
option), or the package is imported from a site-packages copy, the run stops
with an explanation instead of quietly testing someone else's code.

The failure this prevents is specific and was hit for real: `pip install -e .`
writes a .pth file naming the clone it was installed from. Working in a git
worktree, `pytest` then resolves `opencontextually` to the *original* clone.
Every test passes, and none of them touched the code being changed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_SRC = REPO_ROOT / "src" / "opencontextually"


def pytest_configure(config: pytest.Config) -> None:
    import opencontextually

    imported = Path(opencontextually.__file__).resolve().parent
    if imported != EXPECTED_SRC:
        raise pytest.UsageError(
            "opencontextually was imported from a different checkout, so this "
            "test run would not exercise the code in this repository.\n\n"
            f"  imported from: {imported}\n"
            f"  expected:      {EXPECTED_SRC}\n\n"
            "This usually means an editable install (`pip install -e .`) from "
            "another clone is shadowing this one -- common when working in a "
            "git worktree. Re-run with:\n\n"
            f'    PYTHONPATH="{REPO_ROOT / "src"}" pytest\n\n'
            "or reinstall the package from this checkout."
        )
