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

    def render(self, verbose: bool = False, show_all: bool = False) -> str:
        """Plain-text rendering for humans.

        Default: one line per included file (path, reason, and a "via"
        marker for import-reached items), the top DEFAULT_SHOWN of them,
        plus one line per check finding and a footer naming what was
        hidden. No code excerpts -- that is what -v is for.

        verbose=True: append a tightly-capped excerpt view under each shown
        item (today's excerpt content, presented much smaller).

        show_all=True: list every included item instead of the top slice.

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

            path_width = min(
                max(len(item.path) for item in shown), MAX_PATH_COLUMN
            )
            term_width = self._terminal_width()

            for item in shown:
                lines.append(self._render_item_line(item, path_width, term_width, glyphs))
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

    def _render_item_line(self, item: ContextItem, path_width: int, term_width: int, glyphs) -> str:
        path_field = item.path.ljust(path_width)
        detail = item.reason
        via = _via_marker(item)
        if via:
            detail += f"  {glyphs.VIA} via {via}"

        prefix = f"  {path_field}  "
        budget = max(term_width - len(prefix), 16)
        detail = _truncate(detail, budget)
        return f"{prefix}{detail}"

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

        lines = [f"  {glyphs.WARN} {head}"]

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


class _UnicodeGlyphs:
    WARN = "⚠"
    GAP = "○"
    VIA = "←"
    DOT = "·"


class _AsciiGlyphs:
    WARN = "!"
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
