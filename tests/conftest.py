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

`pythonpath` and the guard below only govern the *in-process* import. Several
tests spawn a subprocess (`sys.executable -m opencontextually.cli`, and the
MCP server test), and a fresh interpreter re-reads the editable install's
.pth file -- so those tests kept resolving the wrong checkout even once the
in-process import was correct. This was invisible until the version strings
of the two checkouts diverged, at which point `--version` returned the other
tree's version. `pytest_configure` therefore also exports PYTHONPATH, which
every subprocess inherits.
"""

from __future__ import annotations

import os
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

    # Every subprocess a test spawns starts a fresh interpreter, which
    # re-reads the editable install's .pth file and would resolve the wrong
    # checkout again. Exporting PYTHONPATH here is inherited by all of them.
    # Prepended, never replacing, so an existing PYTHONPATH still applies.
    src = str(REPO_ROOT / "src")
    existing = os.environ.get("PYTHONPATH", "")
    if src not in existing.split(os.pathsep):
        os.environ["PYTHONPATH"] = (
            src + os.pathsep + existing if existing else src
        )
