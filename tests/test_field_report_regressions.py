"""Regressions for the v0.2.4 external field-report failure classes."""

from __future__ import annotations

import math
from pathlib import Path

from opencontextually import get_context
from opencontextually.context import ContextItem, DEFAULT_SHOWN
from opencontextually.selector import (
    MAX_DOC_PREFIX_SHARE,
    MAX_INCLUDED,
    _build_reason,
    _diversify_delivery,
    tokenize,
)


def _write(root: Path, rel_path: str, content: str) -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _item(path: str, role: str, score: float) -> ContextItem:
    return ContextItem(path=path, role=role, reason="fixture", score=score)


def test_natural_bug_report_drops_function_words_but_keeps_domain_terms():
    terms = tokenize(
        "the community page never flips to ready after the installers finish building"
    )

    assert terms == ["community", "page", "flips", "ready", "installers", "finish", "building"]
    assert tokenize("one router includes another") == ["router", "includes"]


def test_content_only_reasons_never_invent_stronger_relationships():
    for role in ("docs", "config", "test", "source", "other"):
        assert _build_reason(role, [], [], {"authentication": 4}) == "mentions authentication"

    assert _build_reason("source", [], ["authenticate"], {"authentication": 4}) == "defines authenticate"
    assert _build_reason("docs", ["authentication"], [], {"authentication": 4}) == (
        "filename matches 'authentication'"
    )


def test_docs_heavy_polyglot_repository_surfaces_relevant_source_in_default_view(tmp_path):
    for index in range(12):
        _write(
            tmp_path,
            f"docs/note_{index}.md",
            f"# Ready notes {index}\n\nThe community page becomes ready.\n",
        )
    for index in range(8):
        _write(
            tmp_path,
            f"src/component_{index}.ts",
            f"export function transition{index}() {{ return 'community page ready'; }}\n",
        )

    package = get_context("community page ready", root=str(tmp_path))
    visible = package.included[:DEFAULT_SHOWN]

    assert any(item.role == "source" for item in visible)
    assert sum(item.role == "docs" for item in visible) <= math.ceil(
        DEFAULT_SHOWN * MAX_DOC_PREFIX_SHARE
    )


def test_docs_only_repository_uses_the_whole_package_budget(tmp_path):
    for index in range(MAX_INCLUDED + 4):
        _write(
            tmp_path,
            f"docs/ready_{index}.md",
            f"# Ready {index}\n\nCommunity page ready state {index}.\n",
        )

    package = get_context("community page ready", root=str(tmp_path))

    assert len(package.included) == MAX_INCLUDED
    assert all(item.role == "docs" for item in package.included)


def test_strongly_relevant_documentation_keeps_first_position(tmp_path):
    _write(
        tmp_path,
        "docs/community-ready.md",
        "# Community ready lifecycle\n\nInstallers build before the page transitions to ready.\n",
    )
    for index in range(5):
        _write(
            tmp_path,
            f"src/worker_{index}.ts",
            f"export const state{index} = 'ready';\n",
        )

    package = get_context("community ready lifecycle", root=str(tmp_path))

    assert package.included[0].path == "docs/community-ready.md"


def test_diversification_scales_for_small_and_large_packages():
    ranked = [
        *[_item(f"docs/{index}.md", "docs", 100 - index) for index in range(20)],
        *[_item(f"src/{index}.ts", "source", 50 - index) for index in range(20)],
    ]

    for limit in (5, MAX_INCLUDED, 30):
        delivered = _diversify_delivery(ranked, limit)
        assert len(delivered) == limit
        assert sum(item.role == "docs" for item in delivered) <= math.ceil(
            limit * MAX_DOC_PREFIX_SHARE
        )


def test_diversification_never_invents_an_irrelevant_source_candidate():
    docs = [_item(f"docs/{index}.md", "docs", 100 - index) for index in range(8)]

    delivered = _diversify_delivery(docs, 8)

    assert delivered == docs
