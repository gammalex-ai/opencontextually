"""Bounded, safe file discovery.

discover() walks a single project root and returns the files that are
candidates for selection, plus a count of how many files were excluded and
why. No scoring, no import graph, no task awareness -- that is selector.py
(step 3+). This module only answers: "given a root, what files exist that
are worth considering at all?"
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

import pathspec

MAX_FILE_BYTES = 512_000

# Narrow, explicit denylist. Dot-files and dot-directories are NOT
# blanket-excluded -- .github/, .env, .eslintrc, .circleci/ are frequently
# the most relevant files in a repo. Only VCS internals and common
# dependency/build caches are denied outright; everything else is subject
# only to .gitignore / .opencontextuallyignore, binary sniffing, and the
# size cap.
DENYLIST_DIRS = {".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv"}

CONFIG_EXTENSIONS = {".yaml", ".yml", ".toml", ".ini", ".env", ".json", ".cfg"}
DOCS_EXTENSIONS = {".md", ".rst", ".txt"}

# Directories that hold bulk data, vendored code, or build output rather
# than hand-written project configuration or documentation, even when a
# file inside them has a "config-like" or "docs-like" extension -- e.g.
# data/results/2026-05-26/openai_quality.json is a benchmark output, not
# app config, and should not earn the config role bonus or be scanned by
# configuration_discrepancy. Source code inside these directories (rare,
# but real -- a data-processing script under data/) is unaffected; only
# the config/docs reclassification below is skipped for it. Selector.py's
# DATA_PATH_PENALTY down-weights these paths' *scores* directly; this set
# is imported from there rather than duplicated.
DATA_DIR_SEGMENTS = {
    "data", "datasets", "fixtures", "fixture", "testdata", "test-data",
    "__snapshots__", "vendor", "dist", "build", "generated",
}
_SOURCE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".rb", ".c", ".cpp", ".h", ".hpp",
}

REASON_IGNORED = "ignored"
REASON_BINARY = "binary"
REASON_OVERSIZE = "oversize"
REASON_DUPLICATE = "duplicate"

# Directories that hold vendored/tooling output rather than something a
# developer would open and edit -- when several byte-identical copies of a
# file exist (a vendored dependency, a worktree, a build artifact), a copy
# under one of these is the last choice for the kept representative even if
# it happens to be the shallowest path. See _dedupe_by_content().
DUPLICATE_TOOLING_DIR_SEGMENTS = {".claude", "vendor", "third_party", "build", "dist"}


@dataclass
class DiscoveredFile:
    """A single file found by discover(), classified but not yet scored."""

    path: str  # relative to root, forward-slash separated
    abs_path: Path
    role: str  # "source" | "test" | "config" | "docs" | "other"
    size: int
    # Number of byte-identical copies of this file that were dropped in
    # its favor by _dedupe_by_content() -- 0 for a file with no known
    # duplicates, or for a duplicate that was itself dropped (it never
    # reaches the returned list). Surfaced in the kept item's reason by
    # selector.py so a collapsed duplicate is never silent.
    duplicate_count: int = 0


# --------------------------------------------------------------------------
# gitignore semantics across all three sources git honors, not just the
# root .gitignore.
#
# Git combines patterns from (lowest precedence first):
#   1. the global excludes file (core.excludesFile)
#   2. $GIT_DIR/info/exclude
#   3. .gitignore in the root directory
#   4. .gitignore in each subdirectory, deeper directories overriding
#      shallower ones
# with the *last matching pattern, across all of these, deciding the
# outcome* -- so a negation (`!pattern`) in a more specific source can
# re-include something a less specific source excluded, and vice versa.
# This was verified empirically against real `git check-ignore -v` output
# (info/exclude beats the global excludes file; root .gitignore beats
# info/exclude; a subdirectory's .gitignore beats its parent's).
#
# Sources 1-3 are all anchored at the project root, so they can be
# concatenated into one root-scoped PathSpec and matched with ordinary
# last-line-wins semantics (.opencontextuallyignore is appended after
# the root .gitignore, for the same reason it always has been: it is the
# most specific "this tool" override at that scope). Source 4 needs one
# PathSpec per directory that actually has its own .gitignore, because its
# patterns are anchored relative to *that* directory, not the root -- see
# _final_ignore_verdict().
# --------------------------------------------------------------------------


def _read_lines_if_exists(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        return path.read_text(errors="replace").splitlines()
    except OSError:
        return []


_CONFIG_SECTION_RE = re.compile(r"^\[([^\]\s]+)")
_CONFIG_EXCLUDES_FILE_RE = re.compile(r"^excludesfile\s*=\s*(.+)$", re.IGNORECASE)


def _read_excludes_file_from_config(config_path: Path) -> str | None:
    """Parse a git config file for `[core] excludesFile = ...`, without
    shelling out to git. Minimal on purpose: only the one key this tool
    needs, tolerant of missing/unparseable files (returns None rather than
    raising).
    """
    try:
        text = config_path.read_text(errors="replace")
    except OSError:
        return None

    section = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        section_match = _CONFIG_SECTION_RE.match(line)
        if section_match:
            # "[core]" -> "core"; "[core \"sub\"]" -> "core" (the
            # subsection, if any, is irrelevant to excludesFile).
            section = section_match.group(1).split()[0].strip('"').lower()
            continue
        if section != "core":
            continue
        value_match = _CONFIG_EXCLUDES_FILE_RE.match(line)
        if value_match:
            value = value_match.group(1).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            # Strip an unquoted trailing inline comment.
            for marker in (" #", " ;"):
                idx = value.find(marker)
                if idx != -1:
                    value = value[:idx].rstrip()
            return value or None
    return None


def _xdg_config_home() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg)
    return Path.home() / ".config"


def _resolve_core_excludes_file(root: Path) -> Path | None:
    """Resolve core.excludesFile the way git does, without invoking git:
    local repo config first, then the user's global config locations, then
    -- even when nothing set it explicitly -- git's own documented default
    location for this file. Returns an expanded, absolute Path, or None if
    no source names one (the caller still needs to check the result
    exists before reading it -- a configured-but-missing file is not an
    error, it just contributes no patterns).
    """
    candidates = [
        root / ".git" / "config",
        Path.home() / ".gitconfig",
        _xdg_config_home() / "git" / "config",
    ]
    for config_path in candidates:
        value = _read_excludes_file_from_config(config_path)
        if value:
            return Path(value).expanduser()

    # git's documented default for core.excludesFile when it is never set
    # at all.
    default_path = _xdg_config_home() / "git" / "ignore"
    if default_path.is_file():
        return default_path
    return None


def _load_root_spec(root: Path) -> pathspec.PathSpec:
    """Build the root-scoped PathSpec: global excludes, repo-local
    info/exclude, root .gitignore, and .opencontextuallyignore, in
    increasing order of precedence (see module note above).
    """
    lines: list[str] = []

    excludes_file = _resolve_core_excludes_file(root)
    if excludes_file is not None:
        lines.extend(_read_lines_if_exists(excludes_file))

    lines.extend(_read_lines_if_exists(root / ".git" / "info" / "exclude"))
    lines.extend(_read_lines_if_exists(root / ".gitignore"))
    lines.extend(_read_lines_if_exists(root / ".opencontextuallyignore"))

    return pathspec.PathSpec.from_lines("gitignore", lines)


def _scope_verdict(spec: pathspec.PathSpec, match_path: str) -> bool | None:
    """The verdict a single PathSpec gives for `match_path` (already
    scoped/relative to that spec's own directory, with a trailing "/" for
    directories): True if the last matching pattern excludes it, False if
    the last matching pattern is a negation that re-includes it, or None
    if no pattern in this spec says anything about the path at all.

    None (rather than collapsing to False, as PathSpec.match_file() does)
    is what lets a shallower scope's verdict survive when a deeper scope
    has no opinion, and be overridden when it does -- see
    _final_ignore_verdict().
    """
    verdict: bool | None = None
    for pattern in spec.patterns:
        regex = getattr(pattern, "regex", None)
        if regex is None:
            continue
        if regex.match(match_path):
            verdict = pattern.include
    return verdict


def _dir_ancestors(dir_rel: str) -> list[str]:
    """Root-relative ancestor directory paths of `dir_rel`, shallowest
    first, including `dir_rel` itself. "" (the root) is never included --
    the root is handled separately via the root-scoped spec.
    """
    if not dir_rel:
        return []
    parts = dir_rel.split("/")
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


def _final_ignore_verdict(
    root_spec: pathspec.PathSpec,
    ancestor_scopes: list[tuple[str, pathspec.PathSpec]],
    rel_path: str,
    is_dir: bool,
) -> bool:
    """Whether `rel_path` (root-relative) is ignored, combining the root
    scope and every ancestor directory's own .gitignore (shallowest to
    deepest, so a deeper directory's pattern overrides a shallower one's --
    verified against real `git check-ignore` behavior).
    """
    match_path = rel_path + "/" if is_dir else rel_path
    verdict = _scope_verdict(root_spec, match_path)

    for scope_dir, spec in ancestor_scopes:
        sub_path = rel_path[len(scope_dir) + 1 :]
        sub_match = sub_path + "/" if is_dir else sub_path
        scope_result = _scope_verdict(spec, sub_match)
        if scope_result is not None:
            verdict = scope_result

    return bool(verdict)


def _is_binary(path: Path) -> bool:
    """Null-byte sniff on the first 8KB."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
    except OSError:
        return True
    return b"\x00" in chunk


def _classify_role(rel_path: str) -> str:
    parts = rel_path.split("/")
    name = parts[-1]
    ext = Path(name).suffix.lower()
    if not ext and name.lower() in CONFIG_EXTENSIONS:
        # A dotfile whose entire name is the "extension" (".env", the
        # canonical case) has no suffix by pathlib's definition -- Path
        # treats a leading-dot-only name as having no extension at all --
        # even though ".env" is already listed in CONFIG_EXTENSIONS. Fall
        # back to the whole lowered filename so these are still recognized
        # as config rather than silently landing in "other".
        ext = name.lower()
    dir_parts = parts[:-1]

    if "tests" in dir_parts or name.startswith("test_") or name.endswith("_test.py"):
        return "test"

    if ext not in _SOURCE_EXTENSIONS and any(p.lower() in DATA_DIR_SEGMENTS for p in dir_parts):
        return "other"

    if ext in CONFIG_EXTENSIONS or "config" in dir_parts:
        return "config"
    if ext in DOCS_EXTENSIONS or "docs" in dir_parts:
        return "docs"
    if ext in _SOURCE_EXTENSIONS:
        return "source"
    return "other"


def _count_files_recursive(dir_abs: Path, root: Path) -> int:
    """Count plain files under dir_abs (used to attribute an ignored
    directory's contents to the "ignored" exclusion bucket), without
    descending into denylisted subdirectories or following symlinks that
    escape root.
    """
    count = 0
    for dirpath, dirnames, filenames in os.walk(dir_abs, followlinks=False):
        dp = Path(dirpath)
        kept = []
        for dname in dirnames:
            if dname in DENYLIST_DIRS:
                continue
            d_abs = dp / dname
            if d_abs.is_symlink():
                target = d_abs.resolve()
                if root not in target.parents and target != root:
                    continue
            kept.append(dname)
        dirnames[:] = kept

        for fname in filenames:
            f_abs = dp / fname
            if f_abs.is_symlink():
                target = f_abs.resolve()
                if root not in target.parents and target != root:
                    continue
            count += 1
    return count


# --------------------------------------------------------------------------
# Byte-identical duplicate collapsing.
#
# A repo containing several copies of the same file (a vendored dependency,
# a git worktree, a build artifact, a backup directory) would otherwise
# have every copy scored and surfaced independently, crowding out distinct
# results with repeats of the same content. Hashing is bounded to actual
# collision candidates: files are first grouped by size (byte-identical
# files necessarily share a size), and only a size group with more than
# one member -- and never the common size-0 group, where "byte-identical"
# is true of every empty file in the repo and not a meaningful signal --
# is actually read and hashed. Most files in a real repo have a size no
# other file shares, so this reads far fewer files than a full-repo hash
# pass would.
# --------------------------------------------------------------------------


def _hash_file_contents(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _is_tooling_or_vendor_path(rel_path: str) -> bool:
    dir_parts = rel_path.split("/")[:-1]
    return any(part in DUPLICATE_TOOLING_DIR_SEGMENTS for part in dir_parts)


def _pick_representative(files: list[DiscoveredFile]) -> DiscoveredFile:
    """Of several byte-identical files, choose the one a developer would
    actually edit: prefer a path outside a tooling/vendor-ish directory
    (DUPLICATE_TOOLING_DIR_SEGMENTS) over one inside such a directory, then
    the shallowest path, then alphabetical for determinism -- never
    arbitrary discovery order.
    """
    return sorted(
        files,
        key=lambda f: (
            1 if _is_tooling_or_vendor_path(f.path) else 0,
            f.path.count("/"),
            f.path,
        ),
    )[0]


def _dedupe_by_content(discovered: list[DiscoveredFile]) -> tuple[list[DiscoveredFile], int]:
    """Collapse groups of byte-identical files down to one representative
    each (see _pick_representative). Returns (deduplicated files, count of
    duplicate copies dropped). The kept representative's `duplicate_count`
    is set to the number of copies collapsed into it, so the drop is never
    silent -- selector.py surfaces it in the item's reason.
    """
    by_size: dict[int, list[DiscoveredFile]] = {}
    for f in discovered:
        by_size.setdefault(f.size, []).append(f)

    drop_paths: set[str] = set()
    duplicate_counts: dict[str, int] = {}

    for size, group in by_size.items():
        if size == 0 or len(group) < 2:
            continue

        by_hash: dict[str, list[DiscoveredFile]] = {}
        for f in group:
            digest = _hash_file_contents(f.abs_path)
            if digest is None:
                continue
            by_hash.setdefault(digest, []).append(f)

        for files in by_hash.values():
            if len(files) < 2:
                continue
            representative = _pick_representative(files)
            duplicates = [f for f in files if f is not representative]
            for dup in duplicates:
                drop_paths.add(dup.path)
            duplicate_counts[representative.path] = len(duplicates)

    if not drop_paths:
        return discovered, 0

    kept: list[DiscoveredFile] = []
    for f in discovered:
        if f.path in drop_paths:
            continue
        if f.path in duplicate_counts:
            f.duplicate_count = duplicate_counts[f.path]
        kept.append(f)

    return kept, len(drop_paths)


def discover(root: Path) -> tuple[list[DiscoveredFile], dict[str, int]]:
    """Walk `root` and return (discovered files, exclusion reason counts).

    Bounded: never traverses outside root, never follows symlinks that
    resolve outside root. Honors every gitignore source git itself does --
    nested .gitignore files (scoped to their own subtree), repo-local
    $GIT_DIR/info/exclude, the global core.excludesFile, and the root
    .gitignore -- plus the optional .opencontextuallyignore. Works
    unchanged in a directory that is not a git repo at all: sources that
    do not exist simply contribute no patterns. Excludes only the narrow
    DENYLIST_DIRS, plus binaries and oversize files.

    Exclusion reason bucket keys are exactly: "ignored", "binary",
    "oversize", "duplicate".
    """
    root = root.resolve()
    root_spec = _load_root_spec(root)
    # dir_rel -> PathSpec, populated lazily as the walk discovers each
    # directory's own .gitignore (if any). A directory whose contents are
    # pruned because the directory itself is ignored never contributes an
    # entry here, matching git's own behavior of not consulting a
    # .gitignore that lives inside an already-ignored directory.
    nested_specs: dict[str, pathspec.PathSpec] = {}

    discovered: list[DiscoveredFile] = []
    reasons: dict[str, int] = {
        REASON_IGNORED: 0,
        REASON_BINARY: 0,
        REASON_OVERSIZE: 0,
        REASON_DUPLICATE: 0,
    }

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dir_abs = Path(dirpath)
        dir_rel = "" if dir_abs == root else dir_abs.relative_to(root).as_posix()

        if dir_rel and ".gitignore" in filenames:
            gi_lines = _read_lines_if_exists(dir_abs / ".gitignore")
            if gi_lines:
                nested_specs[dir_rel] = pathspec.PathSpec.from_lines("gitignore", gi_lines)

        ancestor_scopes = [
            (scope_dir, nested_specs[scope_dir])
            for scope_dir in _dir_ancestors(dir_rel)
            if scope_dir in nested_specs
        ]

        # Prune denylisted and symlink-escaping directories in place so
        # os.walk does not descend into them.
        kept_dirnames = []
        for dname in dirnames:
            if dname in DENYLIST_DIRS:
                continue
            d_abs = dir_abs / dname
            if d_abs.is_symlink():
                target = d_abs.resolve()
                if root not in target.parents and target != root:
                    continue
            d_rel = d_abs.relative_to(root).as_posix()
            if _final_ignore_verdict(root_spec, ancestor_scopes, d_rel, is_dir=True):
                reasons[REASON_IGNORED] += _count_files_recursive(d_abs, root)
                continue
            kept_dirnames.append(dname)
        dirnames[:] = kept_dirnames

        for fname in filenames:
            f_abs = dir_abs / fname

            if f_abs.is_symlink():
                target = f_abs.resolve()
                if root not in target.parents and target != root:
                    continue

            f_rel = f_abs.relative_to(root).as_posix()

            if _final_ignore_verdict(root_spec, ancestor_scopes, f_rel, is_dir=False):
                reasons[REASON_IGNORED] += 1
                continue

            try:
                size = f_abs.stat().st_size
            except OSError:
                continue

            if size > MAX_FILE_BYTES:
                reasons[REASON_OVERSIZE] += 1
                continue

            if _is_binary(f_abs):
                reasons[REASON_BINARY] += 1
                continue

            discovered.append(
                DiscoveredFile(
                    path=f_rel,
                    abs_path=f_abs,
                    role=_classify_role(f_rel),
                    size=size,
                )
            )

    discovered, duplicate_count = _dedupe_by_content(discovered)
    reasons[REASON_DUPLICATE] = duplicate_count

    return discovered, reasons
