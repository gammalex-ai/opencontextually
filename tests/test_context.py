from opencontextually.context import ContextPackage, ContextItem, Excerpt


def test_render_and_to_dict_roundtrip():
    item = ContextItem(
        path="src/auth/session.py",
        role="source",
        reason="imported by middleware.py",
        score=0.82,
        provenance=["src/auth/middleware.py imports src/auth/session.py"],
        excerpts=[Excerpt(start_line=10, end_line=14, text="def expire():\n    pass")],
    )
    package = ContextPackage(
        task="fix the authentication bug",
        included=[item],
        conflicts=[{"rule": "configuration_discrepancy"}],
        missing=[],
        excluded_count=3,
        excluded_by_reason={"ignored": 2, "binary": 1},
        trace={"rules_run": ["configuration_discrepancy"]},
    )

    rendered = package.render()
    assert "fix the authentication bug" in rendered
    assert "src/auth/session.py" in rendered
    # The compact default shows a "via" marker for import-reached items --
    # naming the neighboring file once, by its own basename, rather than
    # restating the full provenance edge (which duplicates `reason` and
    # can run well past a normal terminal's width -- see test_via_marker.py
    # for the truncation case). The full edge sentence must NOT appear
    # verbatim in the rendered line.
    assert "via" in rendered
    assert "middleware.py" in rendered
    assert "src/auth/middleware.py imports src/auth/session.py" not in rendered

    verbose_rendered = package.render(verbose=True)
    assert "def expire():" in verbose_rendered

    as_dict = package.to_dict()
    assert as_dict["task"] == "fix the authentication bug"
    assert as_dict["included"][0]["path"] == "src/auth/session.py"
    assert as_dict["included"][0]["provenance"] == [
        "src/auth/middleware.py imports src/auth/session.py",
    ]
    assert as_dict["excluded_by_reason"] == {"ignored": 2, "binary": 1}


def test_empty_package_renders_without_error():
    package = ContextPackage(task="do something")
    rendered = package.render()
    assert "No relevant context found for this task." in rendered
    assert package.to_dict()["included"] == []


# --- bug fix: the exclusion summary was unreadable -------------------------
#
# The old footer was one comma-joined line of every internal bucket name
# ("below_threshold=61, binary=0, duplicate=0, ignored=11, over_budget=2,
# over_cap=0, oversize=0"), always printing all seven keys even when four
# of them were 0, and describing every excluded file as "unrelated" even
# though a gitignored file was never evaluated for relevance and an
# over-budget file was judged relevant and then dropped. See the
# _EXCLUSION_LABELS comment block in context.py for the full rationale.


def test_exclusion_summary_hides_zero_buckets_and_drops_jargon():
    package = ContextPackage(
        task="anything",
        excluded_count=72,
        excluded_by_reason={
            "ignored": 11,
            "binary": 0,
            "oversize": 0,
            "duplicate": 0,
            "below_threshold": 61,
            "over_cap": 0,
            "over_budget": 0,
        },
    )
    rendered = package.render()

    # Zero buckets never appear.
    for jargon in ("binary", "oversize", "duplicate", "over_cap", "over_budget"):
        assert jargon not in rendered

    # Internal bucket names never leak into the text, even for non-zero
    # buckets -- only the plain-language label does.
    assert "below_threshold" not in rendered
    assert "ignored=" not in rendered

    # Plain-language content for the two buckets that are actually
    # non-zero here.
    assert "11" in rendered
    assert "gitignored" in rendered
    assert "61" in rendered
    assert "not relevant enough" in rendered

    # The old blanket "unrelated files" description is gone.
    assert "unrelated" not in rendered


def test_exclusion_summary_marks_dropped_relevant_files_distinctly():
    """over_budget/over_cap files cleared the relevance bar and were then
    dropped -- that is a materially different (and more important) fact
    than "gitignored" or "below threshold", so it must render as its own,
    visually distinct line rather than a slot in a list.
    """
    package = ContextPackage(
        task="anything",
        excluded_count=63,
        excluded_by_reason={
            "ignored": 11,
            "below_threshold": 50,
            "over_budget": 2,
        },
    )
    rendered = package.render()
    lines = rendered.splitlines()

    dropped_lines = [line for line in lines if "2" in line and "relevant" in line and "dropped" in line]
    assert dropped_lines, rendered
    dropped_line = dropped_lines[0]

    # It is its own line, not folded into the "not scanned" or "not
    # relevant enough" lines.
    assert "gitignored" not in dropped_line
    assert "not relevant enough" not in dropped_line

    # Distinct marker (glyph or ASCII fallback) sets it apart from a
    # routine housekeeping line.
    assert any(marker in dropped_line for marker in ("⚠", "!"))


def test_exclusion_summary_no_excluded_files():
    package = ContextPackage(task="anything", excluded_count=0, excluded_by_reason={})
    rendered = package.render()
    assert "Excluded" in rendered
    assert "0 files" not in rendered  # "Excluded: none", not "Excluded: 0 files"


def test_exclusion_summary_is_presentation_only():
    """render() changes; to_dict()'s excluded_by_reason -- what machine
    consumers key off of -- must stay exactly the stable bucket dict, no
    keys added, removed, or renamed.
    """
    reasons = {
        "ignored": 11,
        "binary": 0,
        "oversize": 0,
        "duplicate": 0,
        "below_threshold": 61,
        "over_cap": 0,
        "over_budget": 2,
    }
    package = ContextPackage(task="anything", excluded_count=74, excluded_by_reason=dict(reasons))
    package.render()  # rendering must not mutate the underlying data
    assert package.to_dict()["excluded_by_reason"] == reasons
