"""Data model for OpenContextually's output: the ContextPackage.

These are plain dataclasses. No selection, discovery, or check logic lives
here. ContextPackage has exactly two methods -- render() and to_dict() -- and
they are the *only* place in the codebase that formats a package for output.
Anything that needs to show a package to a human or serialize it to JSON goes
through one of these two methods rather than reimplementing formatting.

render() has two audiences that must not be conflated (see the CLI's -v /
--all flags): a human wants a scannable list of *which files matter and why*,
not the file contents -- they can already read the file. An agent wants
excerpts, and gets them from to_dict() / --json, which stays full-fidelity
always. So the plain-text default is compact (no excerpts); -v opts into a
tightly-capped excerpt view for a human who wants a quick look without
opening the files; --all lists every included item instead of the top slice.
None of this touches to_dict() or the selection/extraction limits in
selector.py that feed it -- only how much of that already-computed data
render() chooses to print.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field, asdict
from pathlib import PurePosixPath

# How many included items the compact default shows before summarizing the
# rest as "+N more". Chosen so the common case (a handful of genuinely
# relevant files) fits on one screen without scrolling; --all bypasses this.
DEFAULT_SHOWN = 8

# Roughly how many lines of excerpt text -v prints per file. This is a
# *display* cap, independent of selector.py's MAX_EXCERPT_LINES /
# MAX_EXCERPTS_PER_FILE / MAX_PACKAGE_BYTES, which bound what gets extracted
# and shipped in to_dict() in the first place. -v trims what's already been
# extracted down to a skimmable size; it never asks for more than selection
# already produced.
VERBOSE_EXCERPT_BUDGET = 9

# Longest a path column is allowed to grow before reasons start getting
# crowded out in a narrow terminal.
MAX_PATH_COLUMN = 48

# --- bug fix: reasons truncated into uselessness -------------------------
#
# The path column used to claim whatever width the longest shown path
# needed (up to MAX_PATH_COLUMN), and the reason got whatever was left --
# which, in a narrow terminal with a long path (e.g.
# ".github/ISSUE_TEMPLATE/bug_report.yml", 38 chars), could be as little
# as ~15 usable characters before the trailing "...". Truncation to "..."
# with no real content is never acceptable: a reason exists to be read.
#
# The fix budgets columns the other way around: the reason is guaranteed
# at least MIN_REASON_WIDTH characters first, and the path column shrinks
# to make room for that -- down to MIN_PATH_WIDTH, below which a path is
# no longer identifiable even abbreviated. A shrunk path is middle-elided
# (see _elide_path_middle()) rather than truncated from the right, so the
# basename -- the most identifying part of a path -- always survives; only
# the middle directory segments are dropped. When the terminal is too
# narrow for even MIN_PATH_WIDTH + MIN_REASON_WIDTH to fit side by side,
# the layout gives up on sharing one line at all: the path prints on its
# own line and the reason wraps onto a second, indented line with (nearly)
# the whole terminal width to itself, per _column_layout()'s `wrap` flag.
INDENT_WIDTH = 2
GUTTER_WIDTH = 2
WRAP_INDENT_WIDTH = 4
MIN_REASON_WIDTH = 28
MIN_PATH_WIDTH = 16

_INDENT = " " * INDENT_WIDTH
_GUTTER = " " * GUTTER_WIDTH
_WRAP_INDENT = " " * WRAP_INDENT_WIDTH

# The middle-elision marker used by _elide_path_middle(). Plain ASCII
# (not the Unicode/ASCII glyph pair used elsewhere) since it sits inside a
# file path, which callers may copy-paste or grep -- an ellipsis character
# there is one more thing that could fail to round-trip.
_PATH_ELIDE_MARKER = ".../"


@dataclass
class Excerpt:
    """A bounded, 1-indexed, inclusive span of a file's text."""

    start_line: int
    end_line: int
    text: str


@dataclass
class ContextItem:
    """One file included in a ContextPackage."""

    path: str  # relative to the project root
    role: str  # "source" | "test" | "config" | "docs" | "other"
    reason: str
    score: float
    provenance: list[str] = field(default_factory=list)
    excerpts: list[Excerpt] = field(default_factory=list)
    # Task terms this item itself directly corroborated (filename, symbol,
    # or content match) -- the same set selector._analyze() computes
    # internally for coverage_ratio. Empty for transitively-expanded items,
    # which were reached by import edges rather than a direct term match.
    # Threaded through so weak-signal detection (see ContextPackage.
    # weak_signal) can see real per-file term coverage instead of
    # re-deriving it from `reason`, which only names one signal.
    matched_terms: list[str] = field(default_factory=list)


@dataclass
class ContextPackage:
    """The result of get_context(task): what to hand an agent, and why."""

    task: str
    included: list[ContextItem] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)
    excluded_count: int = 0
    excluded_by_reason: dict[str, int] = field(default_factory=dict)
    trace: dict = field(default_factory=dict)
    # Set (non-None) when SELECT found something to include, but the
    # signal behind it is weak: multiple task terms were given, yet no
    # single included file corroborated more than one of them, and the
    # term(s) that did match are either a repo-wide naming convention
    # (e.g. Next.js's `page.tsx`) or backed by only a handful of
    # incidental mentions. See selector.detect_weak_signal(). Never
    # suppresses `included` -- this only adds a warning ahead of it.
    # Shape when present:
    #   {
    #     "matched_terms": {term: file_count, ...},       # terms that
    #         actually contributed an inclusion, each with how many
    #         discovered files in the repo also contain that term
    #         (by filename, symbol, or content -- same signal _analyze()
    #         scores with).
    #     "term_file_counts": {term: file_count, ...},     # every task
    #         term (matched or not), same file-count metric, so a term
    #         that matched nothing at all is reported as 0 rather than
    #         omitted.
    #   }
    weak_signal: dict | None = None

    def render(self, verbose: bool = False, show_all: bool = False, width: int | None = None) -> str:
        """Plain-text rendering for humans.

        Default: one line per included file (path, reason, and a "via"
        marker for import-reached items), the top DEFAULT_SHOWN of them,
        plus one line per check finding and a footer naming what was
        hidden. No code excerpts -- that is what -v is for.

        verbose=True: append a tightly-capped excerpt view under each shown
        item (today's excerpt content, presented much smaller).

        show_all=True: list every included item instead of the top slice.

        width: override the terminal width used for column budgeting
        (see _column_layout()). Defaults to _terminal_width() -- this
        parameter exists so tests (and any other caller that wants a
        fixed-width render) do not have to monkeypatch shutil or the
        COLUMNS environment variable to exercise a specific width; it is
        the seam the plan asks _terminal_width() to provide.

        This and to_dict() are the only places in the codebase that format
        a package for output.
        """
        ascii_only = not self._stdout_supports_unicode()
        glyphs = _AsciiGlyphs if ascii_only else _UnicodeGlyphs

        lines: list[str] = []
        lines.append(self.task)
        lines.append(
            f"{len(self.included)} relevant {glyphs.DOT} {self.excluded_count} excluded"
        )
        lines.append("")

        if self.included:
            shown = self.included if show_all else self.included[:DEFAULT_SHOWN]
            hidden = len(self.included) - len(shown)

            if self.weak_signal:
                lines.extend(self._render_weak_signal_lines(glyphs))
                lines.append("")
                lines.append("Showing weak matches anyway:")
                lines.append("")

            term_width = width if width is not None else self._terminal_width()
            layout = _column_layout(shown, term_width)

            for item in shown:
                lines.extend(self._render_item_lines(item, layout, glyphs))
                if verbose:
                    lines.extend(self._render_verbose_excerpts(item))

            finding_lines = self._render_finding_lines(glyphs)
            if finding_lines:
                lines.append("")
                lines.extend(finding_lines)

            hint = self._render_footer_hint(hidden, verbose, glyphs)
            if hint:
                lines.append("")
                lines.append(hint)
        else:
            lines.append("No relevant context found for this task.")
            finding_lines = self._render_finding_lines(glyphs)
            if finding_lines:
                lines.append("")
                lines.extend(finding_lines)

        lines.append("")
        lines.append(self._render_exclusion_summary())
        lines.append("")
        lines.append(self._render_checks_footer())

        return "\n".join(lines)

    # -- item line ---------------------------------------------------------

    def _render_item_lines(self, item: ContextItem, layout: "_ColumnLayout", glyphs) -> list[str]:
        """One item as one or two lines, per `layout` (see _column_layout()).

        detail (the reason, plus the "via" marker) always gets at least
        MIN_REASON_WIDTH characters -- never the near-nothing a starved
        path column used to leave it. When even a minimally-shrunk path
        column can't free up MIN_REASON_WIDTH for the reason, the reason
        wraps onto its own indented line instead of being truncated to
        uselessness -- see _column_layout()'s `wrap` flag.
        """
        detail = item.reason
        via = _via_marker(item)
        if via:
            detail += f"  {glyphs.VIA} via {via}"

        if not layout.wrap:
            path_field = _elide_path_middle(item.path, layout.path_width).ljust(layout.path_width)
            detail = _truncate(detail, layout.reason_width)
            return [f"{_INDENT}{path_field}{_GUTTER}{detail}"]

        # Narrow terminal: path gets its own line (elided/truncated to fit),
        # reason wraps onto the next line at a deeper indent so it keeps a
        # full line's worth of width rather than sharing one line with the
        # path column.
        path_line = f"{_INDENT}{_elide_path_middle(item.path, layout.path_width)}"
        detail = _truncate(detail, layout.reason_width)
        reason_line = f"{_WRAP_INDENT}{detail}"
        return [path_line, reason_line]

    # -- weak signal ----------------------------------------------------------

    def _render_weak_signal_lines(self, glyphs) -> list[str]:
        """Render self.weak_signal (see its shape in the field comment
        above) as a short, concrete warning: which term(s) actually
        matched and how common they are, then the other task terms' file
        counts (including zero, for a term that matched nothing at all),
        then one actionable suggestion. Never hides the results that
        follow -- this is prefixed ahead of them, not instead of them.
        """
        ws = self.weak_signal
        matched: dict[str, int] = ws.get("matched_terms", {})
        all_counts: dict[str, int] = ws.get("term_file_counts", {})

        def _files(count: int) -> str:
            return "1 file" if count == 1 else f"{count} files"

        if len(matched) == 1:
            term, count = next(iter(matched.items()))
            head = f'Weak match. Only "{term}" matched anything, and {_files(count)} share that name.'
        else:
            names = ", ".join(f'"{t}"' for t in matched)
            head = f"Weak match. Only {names} matched anything, and each is common across many files."

        lines = [f"  {glyphs.WEAK} {head}"]

        others = [(t, c) for t, c in all_counts.items() if t not in matched]
        if others:
            parts = []
            for term, count in others:
                if count == 0:
                    parts.append(f'"{term}" in none')
                else:
                    parts.append(f'"{term}" appears in {_files(count)}')
            lines.append(f"    {', '.join(parts)}.")

        lines.append("    Try naming a specific component, behavior, or file.")
        return lines

    # -- verbose excerpts ----------------------------------------------------

    def _render_verbose_excerpts(self, item: ContextItem) -> list[str]:
        """A tightly-capped excerpt view for -v: at most
        VERBOSE_EXCERPT_BUDGET lines total (headers included), preferring
        whichever already-extracted excerpt contains the task's own terms
        over any other (e.g. a matched definition unrelated to the task).
        Reads item.excerpts as already computed by selector.py -- this
        never extracts new spans, only decides how much of what already
        exists to print.
        """
        if not item.excerpts:
            return []

        terms = _tokenize_for_ranking(self.task)
        ranked = sorted(
            range(len(item.excerpts)),
            key=lambda i: (-_term_hits(item.excerpts[i].text, terms), i),
        )

        out: list[str] = []
        budget = VERBOSE_EXCERPT_BUDGET
        for idx in ranked:
            if budget <= 0:
                break
            excerpt = item.excerpts[idx]
            body = excerpt.text.splitlines()
            header = f"     lines {excerpt.start_line}-{excerpt.end_line}:"
            if budget - 1 <= 0:
                break
            out.append(header)
            budget -= 1
            take = min(len(body), budget)
            for text_line in body[:take]:
                out.append(f"       {text_line}")
            budget -= take
            if take < len(body):
                out.append("       ...")
                budget -= 1
        return out

    # -- findings (checks) --------------------------------------------------

    def _render_finding_lines(self, glyphs) -> list[str]:
        """Each conflict/test_reference_gap finding as one line, always
        shown in compact mode -- these are findings, not files, so they
        are not subject to the top-slice/--all distinction that applies to
        included items.
        """
        lines: list[str] = []
        for conflict in self.conflicts:
            setting = conflict.get("setting", conflict.get("rule", "conflict"))
            message = conflict.get("message")
            if not message:
                doc = conflict.get("doc") or {}
                config = conflict.get("config") or {}
                parts = []
                if config:
                    parts.append(f"{config.get('path')}:{config.get('line')} sets {config.get('value')}")
                if doc:
                    parts.append(f"{doc.get('path')}:{doc.get('line')} says {doc.get('value')}")
                message = f"{setting}: " + ", but ".join(parts) if parts else setting
            lines.append(f"  {glyphs.WARN} {message}")

        for entry in self.missing:
            message = entry.get("message") or f"no test references {entry.get('term', '')}"
            message = message[:1].upper() + message[1:] if message else message
            path = entry.get("path")
            if path:
                message += f" ({path}:{entry.get('line')})"
            lines.append(f"  {glyphs.GAP} {message}")

        return lines

    # -- footer / summary ----------------------------------------------------

    def _render_footer_hint(self, hidden: int, verbose: bool, glyphs) -> str | None:
        parts: list[str] = []
        if hidden > 0:
            parts.append(f"+{hidden} more")
            parts.append("--all to list")
        if not verbose:
            parts.append("-v for code excerpts")
        if not parts:
            return None
        sep = f"  {glyphs.DOT}  "
        return "  " + sep.join(parts)

    def _render_exclusion_summary(self) -> str:
        if self.excluded_count:
            return f"Excluded: {self.excluded_count} unrelated files ({self._render_reason_buckets()})"
        return "Excluded: (none)"

    def _render_reason_buckets(self) -> str:
        if not self.excluded_by_reason:
            return "no reasons recorded"
        return ", ".join(
            f"{reason}={count}"
            for reason, count in sorted(self.excluded_by_reason.items())
        )

    def _render_checks_footer(self) -> str:
        """Name which checks actually ran. configuration_discrepancy and
        test_reference_gap add their rule ids to trace["rules_run"], and
        this footer picks them up automatically without any further
        changes to render() itself.
        """
        rule_ids = self.trace.get("rules_run", []) if self.trace else []
        check_rule_ids = {"configuration_discrepancy", "test_reference_gap"}
        checks_run = [rule_id for rule_id in rule_ids if rule_id in check_rule_ids]
        if checks_run:
            return "Checks run: " + ", ".join(checks_run)
        return "Checks run: selection only"

    # -- terminal / encoding helpers -----------------------------------------

    @staticmethod
    def _terminal_width() -> int:
        return shutil.get_terminal_size(fallback=(100, 24)).columns

    @staticmethod
    def _stdout_supports_unicode() -> bool:
        stream = getattr(sys, "stdout", None)
        encoding = getattr(stream, "encoding", None) or "utf-8"
        try:
            "⚠○←·".encode(encoding)
            return True
        except (LookupError, UnicodeEncodeError):
            return False

    def to_dict(self) -> dict:
        """JSON-ready representation of the package."""
        return asdict(self)


# --- bug fix: weak-match warning and conflict findings were visually
# indistinguishable ---------------------------------------------------
#
# Both the "weak match" warning (SELECT is unsure the results are relevant
# at all) and a configuration_discrepancy finding (a specific, concrete
# fact about two files disagreeing) used the same "⚠" marker, so a reader
# skimming the output could not tell which kind of thing they were
# looking at without reading the sentence. WARN stays "⚠" for conflicts
# (an earlier design's convention, restored here); WEAK gets its own
# marker, distinct from both WARN (conflicts) and GAP (missing test
# references, "○"), with an ASCII fallback of its own.
class _UnicodeGlyphs:
    WARN = "⚠"
    WEAK = "≈"
    GAP = "○"
    VIA = "←"
    DOT = "·"


class _AsciiGlyphs:
    WARN = "!"
    WEAK = "~"
    GAP = "o"
    VIA = "<-"
    DOT = "*"


# --- bug fix: redundant, truncated "via" text ---
# The compact line used to reuse the item's full provenance -- the entire
# ordered edge-path list, joined with " -> " -- as the "via" marker. For a
# one-hop item that is already the whole reason restated in a different
# shape ("dali/runners/run_synthetic.py imports dali/scoring/verification.py"
# duplicating a `reason` that already says something like "imported by
# run_synthetic.py"), and for a deeper chain it routinely blew past a
# standard 80-100 column terminal and got cut off mid-path by _truncate().
# Neither problem is what the marker is for -- it exists so a reader can
# tell *at a glance* which nearby file pulled this one in, not to restate
# the whole path selector.py already used to build the reason.
#
# The fix names the neighboring file once, concisely, by basename: the
# last edge in provenance (the hop that reached this item directly) is
# selector.py's "<importer> imports <importee>" sentence; whichever side
# is *not* this item's own path is the file to name. Structured
# provenance itself -- what to_dict()/--json emit -- is completely
# untouched by this; only what render() prints from it changes.
def _via_marker(item: ContextItem) -> str | None:
    if not item.provenance:
        return None
    edge = item.provenance[-1]
    if " imports " not in edge:
        # Not one of selector.py's import-edge sentences (e.g. some future
        # provenance shape) -- nothing safe to shorten, so show nothing
        # rather than guess.
        return None
    left, _, right = edge.partition(" imports ")
    other_path = right if left == item.path else left
    return PurePosixPath(other_path).name


def _truncate(text: str, width: int) -> str:
    if width <= 1 or len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3].rstrip() + "..."


@dataclass
class _ColumnLayout:
    """How one render() pass lays out the path/reason columns -- computed
    once per call from the shown items and the terminal width, then reused
    for every item line. See _column_layout().
    """

    path_width: int
    reason_width: int
    wrap: bool


def _column_layout(shown: list[ContextItem], term_width: int) -> _ColumnLayout:
    """Decide path_width/reason_width/wrap for this render() call -- see
    the "bug fix: reasons truncated into uselessness" comment block above
    MIN_REASON_WIDTH for the full rationale. Reason width is guaranteed
    MIN_REASON_WIDTH first; the path column shrinks (down to
    MIN_PATH_WIDTH) to make room for it, and only when that still isn't
    enough does the layout fall back to a wrapped, two-line-per-item mode.
    """
    longest_path = max((len(item.path) for item in shown), default=0)
    desired_path_width = min(longest_path, MAX_PATH_COLUMN)
    available = max(term_width - INDENT_WIDTH - GUTTER_WIDTH, 0)

    if available - desired_path_width >= MIN_REASON_WIDTH:
        return _ColumnLayout(desired_path_width, available - desired_path_width, wrap=False)

    # Shrink the path column just enough to hand the reason column exactly
    # MIN_REASON_WIDTH, but never below MIN_PATH_WIDTH (a path any shorter
    # than that stops being recognizable even middle-elided).
    shrunk_path_width = min(desired_path_width, max(MIN_PATH_WIDTH, available - MIN_REASON_WIDTH))
    reason_width = available - shrunk_path_width

    if reason_width >= MIN_REASON_WIDTH:
        return _ColumnLayout(shrunk_path_width, reason_width, wrap=False)

    # Even a minimally-shrunk path column doesn't leave MIN_REASON_WIDTH
    # for the reason -- the terminal is too narrow for both columns to
    # share one line at all. Give the path its own line (as wide as the
    # terminal allows) and let the reason wrap onto a second, indented
    # line with (nearly) the whole width to itself.
    path_width = min(desired_path_width, max(term_width - INDENT_WIDTH, 1))
    wrapped_reason_width = max(term_width - WRAP_INDENT_WIDTH, 1)
    return _ColumnLayout(path_width, wrapped_reason_width, wrap=True)


def _elide_path_middle(path: str, width: int) -> str:
    """Shrink `path` to fit `width` columns, preserving the basename --
    the most identifying part of a path -- by dropping middle directory
    segments first rather than truncating from the right. E.g.
    ".github/ISSUE_TEMPLATE/bug_report.yml" at width 26 becomes
    ".../bug_report.yml", not "gh/ISSUE_TEMPLATE/bug_rep...".

    Only falls back to truncating the basename itself (via _truncate, so
    it still ends in "..." rather than being cut off silently) when even
    the elision marker plus the bare basename doesn't fit -- an
    exceptionally narrow column or an unusually long filename.
    """
    if width <= 0:
        return ""
    if len(path) <= width:
        return path

    basename = path.rsplit("/", 1)[-1]
    if len(_PATH_ELIDE_MARKER) + len(basename) > width:
        return _truncate(basename, width)

    segments = path.split("/")
    if len(segments) > 1:
        head = segments[0]
        candidate = f"{head}/{_PATH_ELIDE_MARKER}{basename}"
        if len(candidate) <= width:
            return candidate

    return f"{_PATH_ELIDE_MARKER}{basename}"


def _tokenize_for_ranking(task: str) -> list[str]:
    """Lazy import: selector.py imports ContextItem/Excerpt from this
    module, so importing selector's tokenize() at module scope here would
    be circular. Both modules are fully loaded by the time render() is
    ever called (get_context() imports both up front), so a local import
    resolves cleanly. This is presentation only -- picking which of the
    already-extracted excerpts to show first, not extracting anything new.
    """
    from .selector import tokenize

    return tokenize(task)


def _term_hits(text: str, terms: list[str]) -> int:
    if not terms:
        return 0
    lowered = text.lower()
    return sum(lowered.count(term) for term in terms)
