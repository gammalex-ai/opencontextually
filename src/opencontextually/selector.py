"""Lexical selection: task -> tokens -> scored, ranked ContextItems.

This is the first cut at SELECT. It has no notion of imports or transitive
reach (that is step 5) -- it scores each discovered file against the task's
tokens using filename hits, Python symbol-definition hits, and damped
content frequency, then keeps the files that score above a threshold, up to
a cap.
"""

from __future__ import annotations

import ast
import math
import re

from .context import ContextItem
from .discovery import DiscoveredFile

# --------------------------------------------------------------------------
# Tunable constants. Everything a future tuning pass (step 11) might touch
# lives in this block so it can be adjusted in one place.
# --------------------------------------------------------------------------

SCORE_THRESHOLD = 0.0
MAX_SEEDS = 25

WEIGHT_FILENAME = 10.0
WEIGHT_SYMBOL = 6.0
CONTENT_CAP = 6.0
CONTENT_MULT = 2.0
ROLE_BONUS = 2.0
ROLE_BONUS_ROLES = {"config", "docs", "test"}

MIN_TOKEN_LEN = 3

STOPWORDS = {
    "fix", "bug", "the", "a", "an", "in", "to", "for", "of", "and",
    "issue", "error", "problem", "add", "update", "make", "when", "with",
    "is", "are", "was", "were", "be", "been", "being", "on", "at", "by",
    "this", "that", "these", "those", "it", "its", "as", "or", "not",
    "please", "need", "needs", "can", "should", "would", "could", "will",
    "file", "files", "code", "does", "doesn", "why", "how", "what",
    "into", "from", "there", "here", "some", "any", "all", "new", "old",
}

# --------------------------------------------------------------------------


def _split_camel_and_snake(text: str) -> str:
    """Insert spaces at camelCase and snake_case boundaries so both split
    into separate tokens on the later non-alnum split.
    """
    # lower/digit -> Upper boundary: "authBug" -> "auth Bug"
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    # Upper Upper -> Upper lower boundary: "HTTPServer" -> "HTTP Server"
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    return text


def tokenize(task: str) -> list[str]:
    """lowercase, split on non-alphanumerics and camelCase/snake_case
    boundaries, drop stopwords, drop tokens under MIN_TOKEN_LEN chars.
    """
    text = _split_camel_and_snake(task)
    text = text.lower()
    raw_tokens = re.split(r"[^a-z0-9]+", text)
    tokens = [
        t for t in raw_tokens
        if t and len(t) >= MIN_TOKEN_LEN and t not in STOPWORDS
    ]
    return tokens


def _path_segments(path: str) -> list[str]:
    """Lowercased path segments, including the filename stem split off
    from its extension, for filename/path-segment matching.
    """
    lower = path.lower()
    parts = re.split(r"[\\/]", lower)
    segments: list[str] = []
    for part in parts:
        segments.append(part)
        stem = re.sub(r"\.[a-z0-9]+$", "", part)
        if stem and stem != part:
            segments.append(stem)
    return segments


def _analyze(discovered_file: DiscoveredFile, terms: list[str]) -> tuple[float, list[str]]:
    """Score a single discovered file against `terms`, returning
    (score, reason_fragments).
    """
    score = 0.0
    reasons: list[str] = []
    matched_any = False

    # --- filename / path segment match: highest-weight signal ---
    segments = _path_segments(discovered_file.path)
    filename_terms = [t for t in terms if any(t in seg for seg in segments)]
    if filename_terms:
        score += WEIGHT_FILENAME * len(filename_terms)
        matched_any = True
        for t in filename_terms:
            reasons.append(f"filename matches '{t}'")

    content = ""
    try:
        content = discovered_file.abs_path.read_text(errors="replace")
    except OSError:
        content = ""

    # --- Python symbol definitions ---
    if discovered_file.path.endswith(".py") and content:
        tree = None
        try:
            tree = ast.parse(content)
        except SyntaxError:
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    name_lower = node.name.lower()
                    for t in terms:
                        if t in name_lower:
                            score += WEIGHT_SYMBOL
                            matched_any = True
                            kind = "class" if isinstance(node, ast.ClassDef) else "function"
                            reasons.append(f"defines {kind} {node.name}")

    # --- damped content term frequency ---
    if content:
        content_lower = content.lower()
        for t in terms:
            count = content_lower.count(t)
            if count > 0:
                score += min(CONTENT_CAP, CONTENT_MULT * math.log(1 + count))
                matched_any = True
                if not filename_terms and not any(t in r for r in reasons):
                    reasons.append(f"mentions '{t}' {count}x")

    # --- role bonus ---
    if matched_any and discovered_file.role in ROLE_BONUS_ROLES:
        score += ROLE_BONUS
        reasons.append(f"{discovered_file.role} file matching task terms")

    return score, reasons


def score_file(discovered_file: DiscoveredFile, terms: list[str]) -> float:
    """Score `discovered_file` against `terms`. See module docstring for
    the weighting scheme.
    """
    score, _reasons = _analyze(discovered_file, terms)
    return score


def select(
    discovered: list[DiscoveredFile], task: str
) -> tuple[list[ContextItem], dict[str, int]]:
    """Tokenize `task`, score every discovered file, and return
    (ranked ContextItems above threshold and within the cap,
    extra exclusion-reason counts for "below_threshold" and "over_cap").
    """
    terms = tokenize(task)

    scored: list[tuple[float, DiscoveredFile, list[str]]] = []
    for discovered_file in discovered:
        score, reasons = _analyze(discovered_file, terms)
        scored.append((score, discovered_file, reasons))

    above = [s for s in scored if s[0] > SCORE_THRESHOLD]
    below_count = len(scored) - len(above)

    # Stable rank: score descending, tie-break on path ascending for
    # determinism.
    above.sort(key=lambda s: (-s[0], s[1].path))

    kept = above[:MAX_SEEDS]
    over_cap_count = len(above) - len(kept)

    items = [
        ContextItem(
            path=discovered_file.path,
            role=discovered_file.role,
            reason="; ".join(reasons) if reasons else "matches task terms",
            score=score,
        )
        for score, discovered_file, reasons in kept
    ]

    extra_exclusions = {
        "below_threshold": below_count,
        "over_cap": over_cap_count,
    }
    return items, extra_exclusions
