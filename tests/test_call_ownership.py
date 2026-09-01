"""Regression coverage for the call-ownership ranking fix.

The failure class: an implementation file owns the behavior a bug report
describes, but spells it in different words than the report does (the
report says "yield"/"cleaned up"/"order"; the implementation says
"generator"/"AsyncExitStack") -- while a file that merely *calls into* it
uses the report's vocabulary richly (because it also handles routing,
validation, and everything else, so it happens to share prose with almost
any task), and unrelated files coincidentally share a single word with the
task. Pure lexical/content-frequency scoring buries the file that actually
owns the mechanism under all three.

This is the real-world case observed on fastapi's own repository: for "Fix
a bug where nested dependencies using yield are cleaned up in the wrong
order", `fastapi/dependencies/utils.py` (owns `solve_dependencies`, the
function that resolves nested sub-dependencies and pushes their
generator-based cleanup onto the exit stack) did not appear in the
delivered package at all, while `fastapi/routing.py` (which calls it),
`docs/en/mkdocs.yml` (a doc-site nav listing that happens to repeat
"dependencies" fourteen times), and a same-named coincidence in an
unrelated test file about form-field ordering all outranked it.

The fixture below is that shape in miniature, using generic
routing/dependency-resolution vocabulary rather than fastapi's own names
-- nothing here is fastapi-specific, and the mechanism under test
(_build_call_ownership, in selector.py) reads call/def relationships, not
task words, so it applies identically on any repository.
"""

from __future__ import annotations

from pathlib import Path

from opencontextually import get_context

TASK = "Fix a bug where nested dependencies using yield are cleaned up in the wrong order"


def _write(root: Path, rel_path: str, content: str) -> None:
    full = root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


def _cleanup_order_repo(root: Path) -> None:
    # Calls into the dependency solver, and (like fastapi/routing.py's own
    # huge docstrings) independently discusses the task's vocabulary in
    # its own prose -- genuinely relevant on its own lexical merits, not
    # just as a pass-through.
    _write(
        root,
        "app/routing.py",
        '"""Route dispatch: resolves each request\'s nested dependencies '
        "before calling the handler. Yield-based dependencies must be "
        "cleaned up in the correct order -- see app.dependencies.utils, "
        'which owns the actual generator/exit-stack mechanism."""\n'
        "from app.dependencies.utils import solve_dependencies\n\n"
        "def handle_request(request):\n"
        "    result = solve_dependencies(request)\n"
        "    return result\n",
    )
    # Owns the actual nested-yield cleanup mechanism, spelled in different
    # words than the task ("generator", "exit stack") -- never literally
    # "yield", "nested", "cleaned", or "order".
    _write(
        root,
        "app/dependencies/utils.py",
        '"""Dependency graph resolution."""\n'
        "from contextlib import AsyncExitStack\n\n"
        "async def solve_dependencies(request):\n"
        "    async with AsyncExitStack() as stack:\n"
        "        for sub in request.sub_dependencies:\n"
        "            value = await _solve_generator(sub, stack)\n"
        "        return value\n\n"
        "async def _solve_generator(dependency, stack):\n"
        "    cm = dependency.as_context_manager()\n"
        "    return await stack.enter_async_context(cm)\n",
    )
    # Incidental, low-substance references -- one match apiece, not a
    # real discussion of the mechanism.
    _write(
        root,
        "config.yml",
        "# Project configuration: nested dependencies loaded at startup,\n"
        "# in declaration order.\n"
        "dependencies:\n  - app.routing\n  - app.dependencies.utils\n",
    )
    _write(
        root,
        "docs/dependencies.md",
        "# Dependencies\nThis project resolves dependencies before "
        "handling each request.\n",
    )
    # Vocabulary collision: "order" as in "the order files are processed
    # in", nothing to do with dependency cleanup ordering.
    _write(
        root,
        "tests/test_file_order.py",
        'def test_files_are_processed_in_directory_order():\n'
        '    """Regression test: files must be processed in the order '
        'they appear in the manifest."""\n'
        "    assert list_files() == expected_order()\n",
    )


def test_dependency_owner_outranks_caller_config_docs_and_lexical_collision(tmp_path):
    _cleanup_order_repo(tmp_path)

    package = get_context(TASK, root=str(tmp_path))
    ranked = [item.path for item in package.included]

    assert "app/dependencies/utils.py" in ranked
    for weaker in ("config.yml", "docs/dependencies.md", "tests/test_file_order.py"):
        assert weaker in ranked, f"{weaker} should still be included, just outranked"
        assert ranked.index("app/dependencies/utils.py") < ranked.index(weaker), (
            f"app/dependencies/utils.py (owns the cleanup mechanism) should outrank "
            f"{weaker} (incidental/coincidental match), got order {ranked}"
        )


def test_dependency_owner_reason_names_the_real_relationship(tmp_path):
    _cleanup_order_repo(tmp_path)

    package = get_context(TASK, root=str(tmp_path))
    owner = next(item for item in package.included if item.path == "app/dependencies/utils.py")

    # Not a generic "filename matches" or "references" fallback -- the
    # explanation should name the actual call relationship that promoted
    # it, per the explainability contract.
    assert "routing.py" in owner.reason
