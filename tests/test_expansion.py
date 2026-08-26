"""Unit tests for transitive import expansion (selector.expand_transitively
and the import-resolution helpers it relies on).

These build small synthetic file trees under tmp_path rather than reusing
examples/auth_bug, so each case isolates exactly one behavior: depth
limiting, cycle safety, bidirectionality, relative imports, __init__.py
package resolution, third-party/stdlib exclusion, syntax-error tolerance,
and per-hop score decay.
"""

from __future__ import annotations

from pathlib import Path

from opencontextually.context import ContextItem
from opencontextually.discovery import discover
from opencontextually.selector import (
    IMPORT_DECAY,
    MAX_DEPTH,
    expand_transitively,
)


def _write(root: Path, rel_path: str, content: str) -> None:
    full = root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


def _discovered(root: Path):
    files, _reasons = discover(root)
    return files


def _seed(path: str, score: float = 10.0) -> ContextItem:
    return ContextItem(path=path, role="source", reason="seed", score=score)


def _expanded_paths(items: list[ContextItem]) -> set[str]:
    return {item.path for item in items}


# --- depth limit -------------------------------------------------------


def test_depth_limit_stops_a_third_hop_file(tmp_path):
    # a -> b -> c -> d, MAX_DEPTH=2 from seed a: b (hop1), c (hop2) reached,
    # d (hop3) is not.
    _write(tmp_path, "a.py", "from b import X\n")
    _write(tmp_path, "b.py", "from c import Y\n")
    _write(tmp_path, "c.py", "from d import Z\n")
    _write(tmp_path, "d.py", "Z = 1\n")

    discovered = _discovered(tmp_path)
    items, _over_cap = expand_transitively([_seed("a.py")], discovered)

    paths = _expanded_paths(items)
    assert "b.py" in paths
    assert "c.py" in paths
    assert "d.py" not in paths


def test_max_depth_constant_is_two():
    assert MAX_DEPTH == 2


# --- cycle safety --------------------------------------------------------


def test_cycle_between_two_modules_terminates(tmp_path):
    _write(tmp_path, "a.py", "from b import X\n")
    _write(tmp_path, "b.py", "from a import Y\n")

    discovered = _discovered(tmp_path)
    # Should terminate promptly (no infinite loop) and not re-include the
    # seed itself.
    items, _over_cap = expand_transitively([_seed("a.py")], discovered)

    paths = _expanded_paths(items)
    assert "a.py" not in paths
    assert "b.py" in paths
    assert len(items) == 1


# --- both directions -------------------------------------------------------


def test_both_importer_and_importee_are_reached(tmp_path):
    # caller.py imports seed.py; seed.py imports callee.py.
    _write(tmp_path, "seed.py", "from callee import X\n")
    _write(tmp_path, "callee.py", "X = 1\n")
    _write(tmp_path, "caller.py", "from seed import Y\n")

    discovered = _discovered(tmp_path)
    items, _over_cap = expand_transitively([_seed("seed.py")], discovered)

    paths = _expanded_paths(items)
    assert "callee.py" in paths
    assert "caller.py" in paths

    callee_item = next(i for i in items if i.path == "callee.py")
    caller_item = next(i for i in items if i.path == "caller.py")
    assert callee_item.provenance == ["seed.py imports callee.py"]
    assert caller_item.provenance == ["caller.py imports seed.py"]
    assert "imported by" in callee_item.reason
    assert "imports" in caller_item.reason


# --- relative imports --------------------------------------------------------


def test_relative_import_is_resolved(tmp_path):
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/middleware.py", "from .session import SessionStore\n")
    _write(tmp_path, "pkg/session.py", "class SessionStore: ...\n")

    discovered = _discovered(tmp_path)
    items, _over_cap = expand_transitively([_seed("pkg/middleware.py")], discovered)

    paths = _expanded_paths(items)
    assert "pkg/session.py" in paths
    item = next(i for i in items if i.path == "pkg/session.py")
    assert item.provenance == ["pkg/middleware.py imports pkg/session.py"]


def test_relative_import_with_double_dot(tmp_path):
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/sub/__init__.py", "")
    _write(tmp_path, "pkg/sub/mod.py", "from ..top import Thing\n")
    _write(tmp_path, "pkg/top.py", "class Thing: ...\n")

    discovered = _discovered(tmp_path)
    items, _over_cap = expand_transitively([_seed("pkg/sub/mod.py")], discovered)

    assert "pkg/top.py" in _expanded_paths(items)


# --- __init__.py package resolution --------------------------------------


def test_import_of_package_resolves_to_init(tmp_path):
    _write(tmp_path, "users/__init__.py", "SENTINEL = 1\n")
    _write(tmp_path, "app.py", "from users import SENTINEL\n")

    discovered = _discovered(tmp_path)
    items, _over_cap = expand_transitively([_seed("app.py")], discovered)

    assert "users/__init__.py" in _expanded_paths(items)


def test_import_of_submodule_via_package_import(tmp_path):
    # "from src.users import session" where session is itself a module
    # file, not an attribute of the package __init__.
    _write(tmp_path, "src/__init__.py", "")
    _write(tmp_path, "src/users/__init__.py", "")
    _write(tmp_path, "src/users/session.py", "class SessionStore: ...\n")
    _write(tmp_path, "src/auth/__init__.py", "")
    _write(tmp_path, "src/auth/middleware.py", "from src.users import session\n")

    discovered = _discovered(tmp_path)
    items, _over_cap = expand_transitively([_seed("src/auth/middleware.py")], discovered)

    assert "src/users/session.py" in _expanded_paths(items)


# --- third-party / stdlib imports ignored -----------------------------------


def test_stdlib_and_third_party_imports_pull_in_nothing(tmp_path):
    _write(tmp_path, "app.py", "import os\nimport pathspec\nfrom collections import OrderedDict\n")

    discovered = _discovered(tmp_path)
    items, over_cap = expand_transitively([_seed("app.py")], discovered)

    assert items == []
    assert over_cap == 0


# --- syntax error tolerance --------------------------------------------------


def test_file_with_syntax_error_does_not_crash_expansion(tmp_path):
    _write(tmp_path, "good.py", "from broken import X\n")
    _write(tmp_path, "broken.py", "def oops(:\n    pass\n")

    discovered = _discovered(tmp_path)
    # Should not raise, even though broken.py fails to parse.
    items, _over_cap = expand_transitively([_seed("good.py")], discovered)

    # broken.py is still a real file on disk; expansion should reach it as
    # an import target even though *its own* content can't be parsed for
    # further outbound edges.
    assert "broken.py" in _expanded_paths(items)


def test_seed_file_itself_with_syntax_error_does_not_crash(tmp_path):
    _write(tmp_path, "broken.py", "def oops(:\n    pass\n")
    _write(tmp_path, "other.py", "X = 1\n")

    discovered = _discovered(tmp_path)
    items, _over_cap = expand_transitively([_seed("broken.py")], discovered)

    # No exception, and no spurious edges from a file that can't be parsed.
    assert items == []


# --- score decay per hop --------------------------------------------------


def test_score_decays_per_hop(tmp_path):
    _write(tmp_path, "a.py", "from b import X\n")
    _write(tmp_path, "b.py", "from c import Y\n")
    _write(tmp_path, "c.py", "Y = 1\n")

    discovered = _discovered(tmp_path)
    seed_score = 10.0
    items, _over_cap = expand_transitively([_seed("a.py", score=seed_score)], discovered)

    by_path = {item.path: item for item in items}
    assert by_path["b.py"].score == seed_score * IMPORT_DECAY
    assert by_path["c.py"].score == seed_score * IMPORT_DECAY * IMPORT_DECAY
