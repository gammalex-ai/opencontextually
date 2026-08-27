from opencontextually.context import ContextPackage, ContextItem, Excerpt


def test_render_and_to_dict_roundtrip():
    item = ContextItem(
        path="src/auth/session.py",
        role="source",
        reason="imported by middleware.py",
        score=0.82,
        provenance=["src/auth/middleware.py", "src/auth/session.py"],
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
    # The compact default shows a "via" marker for import-reached items,
    # naming the edge path -- truncated to fit the terminal width if
    # needed, so check for the marker and the importer rather than the
    # full (possibly truncated) chain text.
    assert "via" in rendered
    assert "src/auth/middleware.py" in rendered

    # A wide enough terminal shows the full edge chain untruncated.
    import shutil as _shutil

    original_get_terminal_size = _shutil.get_terminal_size
    _shutil.get_terminal_size = lambda fallback=(80, 24): _shutil.os.terminal_size((200, 24))
    try:
        wide_rendered = package.render()
    finally:
        _shutil.get_terminal_size = original_get_terminal_size
    assert "src/auth/middleware.py -> src/auth/session.py" in wide_rendered

    verbose_rendered = package.render(verbose=True)
    assert "def expire():" in verbose_rendered

    as_dict = package.to_dict()
    assert as_dict["task"] == "fix the authentication bug"
    assert as_dict["included"][0]["path"] == "src/auth/session.py"
    assert as_dict["included"][0]["provenance"] == [
        "src/auth/middleware.py",
        "src/auth/session.py",
    ]
    assert as_dict["excluded_by_reason"] == {"ignored": 2, "binary": 1}


def test_empty_package_renders_without_error():
    package = ContextPackage(task="do something")
    rendered = package.render()
    assert "No relevant context found for this task." in rendered
    assert package.to_dict()["included"] == []
