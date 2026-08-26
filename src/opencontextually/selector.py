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
from pathlib import PurePosixPath

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

# --- transitive import expansion (step 5) ---
# First-party Python import graph, walked in both directions from the seed
# set: files a seed imports, and files that import a seed. Bounded by
# MAX_DEPTH hops, MAX_EXPANDED files total, and per-hop score decay so
# distant, weakly-connected files do not crowd out direct matches.
MAX_DEPTH = 2
IMPORT_DECAY = 0.5
MAX_EXPANDED = 15
MAX_INCLUDED = 40

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


def _module_parts(rel_path: str) -> tuple[tuple[str, ...], bool]:
    """Return (dotted-module-parts, is_package_init) for a first-party
    Python file's root-relative path.

    `src/users/session.py` -> (("src", "users", "session"), False)
    `src/users/__init__.py` -> (("src", "users"), True)
    """
    pure = PurePosixPath(rel_path)
    stem = pure.stem
    if stem == "__init__":
        return tuple(pure.parent.parts), True
    return (*pure.parent.parts, stem), False


def _resolve_module_tuple(module_tuple: tuple[str, ...], file_index: set[str]) -> str | None:
    """Resolve a dotted-module-parts tuple to a first-party file path, or
    None if it does not map to any discovered Python file (i.e. it is
    stdlib, third-party, or simply not part of this project).
    """
    if not module_tuple:
        return None
    base = "/".join(module_tuple)
    module_candidate = base + ".py"
    package_candidate = base + "/__init__.py"
    if module_candidate in file_index:
        return module_candidate
    if package_candidate in file_index:
        return package_candidate
    return None


def _imports_of(rel_path: str, content: str, file_index: set[str]) -> set[str]:
    """Parse `content` (the source of `rel_path`) and return the set of
    first-party file paths it imports. Third-party/stdlib imports (those
    that do not resolve to a file under file_index) are silently skipped.
    Files that fail to parse (SyntaxError) yield no imports.
    """
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return set()

    module_parts, is_init = _module_parts(rel_path)
    base_package = list(module_parts) if is_init else list(module_parts[:-1])

    targets: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod_tuple = tuple(alias.name.split("."))
                resolved = _resolve_module_tuple(mod_tuple, file_index)
                if resolved and resolved != rel_path:
                    targets.add(resolved)

        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # level 1 ("from . import x" / "from .x import y") means
                # "relative to the current package"; each extra level
                # walks one package up from there.
                up = node.level - 1
                if up > len(base_package):
                    continue
                target_package = base_package[: len(base_package) - up]
            else:
                target_package = []

            if node.module:
                target_prefix = tuple(target_package) + tuple(node.module.split("."))
            else:
                target_prefix = tuple(target_package)

            resolved = _resolve_module_tuple(target_prefix, file_index)
            if resolved and resolved != rel_path:
                targets.add(resolved)

            # The imported name may itself be a submodule rather than an
            # attribute of the resolved module (e.g. "from src.users
            # import session" where session.py is a module) -- try that
            # too.
            for alias in node.names:
                if alias.name == "*":
                    continue
                sub_resolved = _resolve_module_tuple(target_prefix + (alias.name,), file_index)
                if sub_resolved and sub_resolved != rel_path:
                    targets.add(sub_resolved)

    return targets


def _build_import_graph(
    discovered: list[DiscoveredFile],
) -> tuple[dict[str, DiscoveredFile], dict[str, set[str]], dict[str, set[str]]]:
    """Build the first-party Python import graph from `discovered`.

    Returns (py_files, outbound, inbound):
      - py_files: rel path -> DiscoveredFile, for every discovered .py file
      - outbound: rel path -> set of rel paths it imports
      - inbound: rel path -> set of rel paths that import it
    """
    py_files = {f.path: f for f in discovered if f.path.endswith(".py")}
    file_index = set(py_files)

    outbound: dict[str, set[str]] = {}
    for path, discovered_file in py_files.items():
        try:
            content = discovered_file.abs_path.read_text(errors="replace")
        except OSError:
            content = ""
        outbound[path] = _imports_of(path, content, file_index) if content else set()

    inbound: dict[str, set[str]] = {path: set() for path in py_files}
    for source, targets in outbound.items():
        for target in targets:
            inbound.setdefault(target, set()).add(source)

    return py_files, outbound, inbound


def expand_transitively(
    seed_items: list[ContextItem], discovered: list[DiscoveredFile]
) -> tuple[list[ContextItem], int]:
    """Expand `seed_items` across the first-party Python import graph, both
    directions (files a seed imports, and files that import a seed).

    Bounded by MAX_DEPTH hops and MAX_EXPANDED files, with score decayed
    IMPORT_DECAY per hop. Cycle-safe: a `visited` set (seeds plus anything
    already expanded) is checked before a file is ever added as a
    candidate, so a cycle in the import graph simply stops contributing
    new files rather than looping.

    Returns (expanded ContextItems, count of candidates dropped for being
    over MAX_EXPANDED).
    """
    py_files, outbound, inbound = _build_import_graph(discovered)

    seed_paths = {item.path for item in seed_items}
    visited = set(seed_paths)

    # Only .py seeds participate in the import graph.
    frontier: list[tuple[str, float, list[str]]] = [
        (item.path, item.score, []) for item in seed_items if item.path in py_files
    ]

    expanded: dict[str, tuple[float, list[str], str]] = {}
    over_cap_count = 0
    depth = 1

    while depth <= MAX_DEPTH and frontier:
        candidates: dict[str, tuple[float, list[str], str]] = {}

        for path, score, provenance in frontier:
            decayed = score * IMPORT_DECAY
            friendly = PurePosixPath(path).name

            for target in outbound.get(path, ()):
                if target in visited or target in candidates:
                    continue
                edge = f"{path} imports {target}"
                candidates[target] = (decayed, provenance + [edge], f"imported by {friendly}")

            for source in inbound.get(path, ()):
                if source in visited or source in candidates:
                    continue
                edge = f"{source} imports {path}"
                candidates[source] = (decayed, provenance + [edge], f"imports {friendly}")

        if not candidates:
            break

        # Deterministic order: strongest (least-decayed) candidates first,
        # tie-broken on path, so the MAX_EXPANDED cap drops the weakest
        # candidates first rather than arbitrarily.
        ordered = sorted(candidates.items(), key=lambda kv: (-kv[1][0], kv[0]))

        next_frontier: list[tuple[str, float, list[str]]] = []
        for path, (score, provenance, reason) in ordered:
            if len(expanded) >= MAX_EXPANDED:
                over_cap_count += 1
                continue
            expanded[path] = (score, provenance, reason)
            visited.add(path)
            next_frontier.append((path, score, provenance))

        frontier = next_frontier
        depth += 1

    items = [
        ContextItem(
            path=path,
            role=py_files[path].role,
            reason=reason,
            score=score,
            provenance=provenance,
        )
        for path, (score, provenance, reason) in expanded.items()
    ]
    return items, over_cap_count


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
    seed_over_cap_count = len(above) - len(kept)

    seed_items = [
        ContextItem(
            path=discovered_file.path,
            role=discovered_file.role,
            reason="; ".join(reasons) if reasons else "matches task terms",
            score=score,
        )
        for score, discovered_file, reasons in kept
    ]

    expanded_items, expansion_over_cap_count = expand_transitively(seed_items, discovered)

    combined = seed_items + expanded_items
    final_items = combined[:MAX_INCLUDED]
    final_over_cap_count = len(combined) - len(final_items)

    extra_exclusions = {
        "below_threshold": below_count,
        "over_cap": seed_over_cap_count + expansion_over_cap_count + final_over_cap_count,
    }
    return final_items, extra_exclusions
