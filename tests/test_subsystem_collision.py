"""The vocabulary-collision failure class, as a permanent guard.

A task's words can be dense in a file that has nothing to do with the
task, because a different subsystem happens to share the vocabulary. This
is the failure mode that hurts a coding agent most: the context is
plausible, so nothing about it reads as wrong.

The corpus case is django, "queryset filter drops the second condition":
`django/contrib/admin/filters.py` -- admin UI list filters, not the ORM --
scores 86.4 and takes rank 1, while `django/db/models/query_utils.py`,
where `Q` composes the conditions the task is about, ranks 158th. The
fixture below is that case in miniature, and it fails the same way.

It is marked strict-xfail rather than deleted or softened: this is a known
gap in lexical scoring, not a bug with a pending patch, and term-rarity
(IDF) weighting was measured against the corpus and made it *worse* --
the rarest words in a task like this are its filler ("drops"), not its
subject. Fixing it needs a signal that reads task structure rather than
token frequency. When something does fix it, strict=True turns this into
a failure so the win is noticed and locked in rather than drifting past.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opencontextually import get_context

TASK = "queryset filter drops the second condition"


def _write(root: Path, rel_path: str, content: str) -> None:
    full = root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


def _collision_repo(root: Path) -> None:
    # Same words, different subsystem: admin list filters.
    _write(
        root,
        "admin/filters.py",
        '"""Admin list filters."""\n'
        "class ListFilter:\n"
        "    def queryset(self, request, queryset):\n"
        "        return queryset\n"
        "    def has_filter_output(self): ...\n"
        "    def filter_choices(self): ...\n"
        "    def build_filter(self): ...\n"
        "class FieldListFilter(ListFilter):\n"
        "    def filter_queryset(self, queryset): return queryset\n"
        "    def second_condition(self): ...\n",
    )
    # The subsystem the task is actually about.
    _write(
        root,
        "db/query.py",
        '"""QuerySet implementation."""\n'
        "from db.query_utils import Q\n\n"
        "class QuerySet:\n"
        "    def filter(self, *args, **kwargs):\n"
        "        return self._filter_or_exclude(False, args, kwargs)\n"
        "    def _filter_or_exclude(self, negate, args, kwargs):\n"
        "        clone = self._chain()\n"
        "        clone.query.add_q(Q(*args, **kwargs))\n"
        "        return clone\n",
    )
    # Where the second condition is actually combined -- lexically weak,
    # reachable only as a dependency of the file above.
    _write(
        root,
        "db/query_utils.py",
        '"""Q objects: combine conditions."""\n'
        "class Q:\n"
        "    def _combine(self, other, conn):\n"
        '        """Combine this condition with the second condition."""\n'
        "        return self\n",
    )


@pytest.mark.xfail(
    strict=True,
    reason="vocabulary collision: admin/filters.py outranks the ORM query module",
)
def test_task_subsystem_outranks_a_vocabulary_collision(tmp_path):
    _collision_repo(tmp_path)

    package = get_context(TASK, root=str(tmp_path))
    ranked = [item.path for item in package.included]

    assert "db/query.py" in ranked
    assert "admin/filters.py" in ranked
    assert ranked.index("db/query.py") < ranked.index("admin/filters.py")


def test_the_dependency_holding_the_task_semantics_is_selected(tmp_path):
    """`query_utils.py` matches almost none of the task's words -- it is
    reached because `query.py` imports `Q` from it. That edge is enough to
    include it here, where the package has room. On django itself it is
    still missed: the same file competes with 5,580 others for 18 slots and
    a relationship bonus alone does not carry it. See the module docstring.
    """
    _collision_repo(tmp_path)

    package = get_context(TASK, root=str(tmp_path))

    assert "db/query_utils.py" in [item.path for item in package.included]
