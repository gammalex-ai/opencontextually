"""Data model for OpenContextually's output: the ContextPackage.

These are plain dataclasses. No selection, discovery, or check logic lives
here. ContextPackage has exactly two methods -- render() and to_dict() -- and
they are the *only* place in the codebase that formats a package for output.
Anything that needs to show a package to a human or serialize it to JSON goes
through one of these two methods rather than reimplementing formatting.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict


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

    def render(self) -> str:
        """Plain-text rendering for humans. Polished further at step 7."""
        lines: list[str] = []
        lines.append(f"Task: {self.task}")
        lines.append("")

        if self.included:
            lines.append(f"Included ({len(self.included)}):")
            for item in self.included:
                lines.append(f"  {item.path} [{item.role}] score={item.score:.2f}")
                lines.append(f"    reason: {item.reason}")
                if item.provenance:
                    lines.append(f"    via: {' -> '.join(item.provenance)}")
                for excerpt in item.excerpts:
                    lines.append(
                        f"    lines {excerpt.start_line}-{excerpt.end_line}:"
                    )
                    for text_line in excerpt.text.splitlines():
                        lines.append(f"      {text_line}")
        else:
            lines.append("Included: (none)")

        if self.conflicts:
            lines.append("")
            lines.append(f"Conflicts ({len(self.conflicts)}):")
            for conflict in self.conflicts:
                lines.append(f"  {conflict}")

        if self.missing:
            lines.append("")
            lines.append(f"Missing ({len(self.missing)}):")
            for item in self.missing:
                lines.append(f"  {item}")

        lines.append("")
        lines.append(
            f"Excluded: {self.excluded_count} "
            f"({self._render_reason_buckets()})"
        )

        if self.trace:
            lines.append("")
            lines.append(f"Trace: {self.trace}")

        return "\n".join(lines)

    def _render_reason_buckets(self) -> str:
        if not self.excluded_by_reason:
            return "no reasons recorded"
        return ", ".join(
            f"{reason}={count}"
            for reason, count in sorted(self.excluded_by_reason.items())
        )

    def to_dict(self) -> dict:
        """JSON-ready representation of the package."""
        return asdict(self)
