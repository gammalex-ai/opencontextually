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
        """Plain-text rendering for humans: an Included section (path,
        reason, excerpts), an Excluded section reading naturally, and a
        footer naming which checks ran. This and to_dict() are the only
        places in the codebase that format a package for output.
        """
        lines: list[str] = []
        lines.append(f"Task: {self.task}")
        lines.append("")

        if self.included:
            lines.append(f"Included ({len(self.included)}):")
            for idx, item in enumerate(self.included, start=1):
                lines.append(f"  {idx}. {item.path} [{item.role}] score={item.score:.2f}")
                lines.append(f"     reason: {item.reason}")
                if item.provenance:
                    lines.append(f"     via: {' -> '.join(item.provenance)}")
                for excerpt in item.excerpts:
                    lines.append(
                        f"     lines {excerpt.start_line}-{excerpt.end_line}:"
                    )
                    for text_line in excerpt.text.splitlines():
                        lines.append(f"       {text_line}")
        else:
            lines.append("No relevant context found for this task.")

        if self.conflicts:
            lines.append("")
            lines.append(f"Conflicts ({len(self.conflicts)}):")
            for idx, conflict in enumerate(self.conflicts, start=1):
                setting = conflict.get("setting", conflict.get("rule", "conflict"))
                lines.append(f"  {idx}. {setting}")
                message = conflict.get("message")
                if message:
                    # `message` is written as "<setting>: <details>" by
                    # the rule that produced it; the setting name is
                    # already the line above, so strip the duplicate
                    # prefix rather than printing it twice.
                    prefix = f"{setting}: "
                    if message.startswith(prefix):
                        message = message[len(prefix):]
                    lines.append(f"     {message}")
                else:
                    doc = conflict.get("doc") or {}
                    config = conflict.get("config") or {}
                    if doc:
                        lines.append(f"     {doc.get('path')}:{doc.get('line')} says {doc.get('value')}")
                    if config:
                        lines.append(f"     {config.get('path')}:{config.get('line')} sets {config.get('value')}")

        if self.missing:
            lines.append("")
            lines.append(f"Missing ({len(self.missing)}):")
            for idx, entry in enumerate(self.missing, start=1):
                message = entry.get("message") or f"no test references {entry.get('term', '')}"
                message = message[:1].upper() + message[1:] if message else message
                lines.append(f"  {idx}. {message}")
                path = entry.get("path")
                if path:
                    lines.append(f"     referenced in {path}:{entry.get('line')}")

        lines.append("")
        if self.excluded_count:
            lines.append(f"Excluded: {self.excluded_count} unrelated files")
            lines.append(f"  {self._render_reason_buckets()}")
        else:
            lines.append("Excluded: (none)")

        lines.append("")
        lines.append(self._render_checks_footer())

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

    def _render_checks_footer(self) -> str:
        """Name which checks actually ran. v0.1 step 7 ships selection
        only -- configuration_discrepancy and test_reference_gap (steps 8
        and 9) will add their rule ids to trace["rules_run"], and this
        footer picks them up automatically without any further changes
        to render() itself.
        """
        rule_ids = self.trace.get("rules_run", []) if self.trace else []
        check_rule_ids = {"configuration_discrepancy", "test_reference_gap"}
        checks_run = [rule_id for rule_id in rule_ids if rule_id in check_rule_ids]
        if checks_run:
            return "Checks run: " + ", ".join(checks_run)
        return "Checks run: selection only"

    def to_dict(self) -> dict:
        """JSON-ready representation of the package."""
        return asdict(self)
