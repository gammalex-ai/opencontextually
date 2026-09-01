"""Regression coverage for the coverage-scaled ROLE_BONUS fix.

ROLE_BONUS used to be a flat 2.0 for any docs/config file that matched
*anything*, regardless of how much of a multi-term task it actually
covered -- exactly SCORE_THRESHOLD, so a single incidental word was
already enough to clear the bar on the bonus alone. Coverage-scaling it
(selector.py, alongside symbol_score/content_score) fixes that without a
blanket "docs/config always ranks below code" rule, which would wrongly
punish the opposite, entirely legitimate case: a task that really is a
documentation or configuration lookup, where a docs/config file
genuinely covers most of the task's vocabulary and should still win.

These two tests pin that the fix is contextual, not a global demotion:
when the task is *about* the documentation or the configuration, that
file still ranks first -- only a docs/config file's *thin, incidental*
match loses its free pass.
"""

from __future__ import annotations

from pathlib import Path

from opencontextually import get_context


def _write(root: Path, rel_path: str, content: str) -> None:
    full = root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


def test_documentation_lookup_task_still_ranks_the_doc_page_first(tmp_path):
    # The doc page substantively covers the task's own vocabulary -- a
    # real documentation-lookup case, not an incidental one-word match.
    _write(
        tmp_path,
        "docs/caching.md",
        "# Caching decorator\n\n"
        "The caching decorator controls response expiration using a "
        "configurable ttl. Once the ttl elapses, the cached expiration "
        "timestamp is invalidated and the decorator recomputes the "
        "response on the next call.\n",
    )
    # Implements caching, but under different internal names -- only a
    # thin lexical overlap with the task, the way a real implementation
    # file's internal vocabulary often diverges from user-facing docs.
    _write(
        tmp_path,
        "src/cache.py",
        '"""Response memoization."""\n'
        "class ResponseMemo:\n"
        "    def __init__(self, max_age):\n"
        "        self._max_age = max_age\n"
        "    def is_stale(self, entry):\n"
        "        return entry.age > self._max_age\n",
    )

    package = get_context(
        "How does the caching decorator control expiration ttl", root=str(tmp_path)
    )
    ranked = [item.path for item in package.included]

    assert ranked, "expected at least one included item"
    assert ranked[0] == "docs/caching.md"


def test_configuration_lookup_task_still_ranks_the_config_file_first(tmp_path):
    # The config file substantively defines the exact keys the task asks
    # about -- a real configuration-lookup case.
    _write(
        tmp_path,
        "config/retry.yaml",
        "# Retry policy: request timeout and max attempts.\n"
        "retry_policy:\n"
        "  timeout_seconds: 30\n"
        "  max_attempts: 5\n"
        "  backoff: exponential\n",
    )
    # Implements retry logic under different internal names.
    _write(
        tmp_path,
        "src/retry.py",
        '"""Backoff scheduling."""\n'
        "class BackoffScheduler:\n"
        "    def __init__(self, limit, deadline):\n"
        "        self._limit = limit\n"
        "        self._deadline = deadline\n"
        "    def should_continue(self, attempt):\n"
        "        return attempt < self._limit\n",
    )

    package = get_context(
        "What timeout value and max attempts does the retry policy use",
        root=str(tmp_path),
    )
    ranked = [item.path for item in package.included]

    assert ranked, "expected at least one included item"
    assert ranked[0] == "config/retry.yaml"
