"""Bounded, safe file discovery.

discover() walks a single project root and returns the files that are
candidates for selection, plus a count of how many files were excluded and
why. No scoring, no import graph, no task awareness -- that is selector.py
(step 3+). This module only answers: "given a root, what files exist that
are worth considering at all?"
"""

from __future__ import annotations

import os
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


@dataclass
class DiscoveredFile:
    """A single file found by discover(), classified but not yet scored."""

    path: str  # relative to root, forward-slash separated
    abs_path: Path
    role: str  # "source" | "test" | "config" | "docs" | "other"
    size: int


def _load_ignore_spec(root: Path) -> pathspec.PathSpec:
    """Build a combined PathSpec from root-level .gitignore and
    .opencontextuallyignore, using gitignore semantics (negation, **,
    directory-only, anchoring).

    Nested .gitignore files (in subdirectories) are out of scope for v0.1:
    only the root-level ignore files are honored.
    """
    lines: list[str] = []
    for ignore_filename in (".gitignore", ".opencontextuallyignore"):
        ignore_path = root / ignore_filename
        if ignore_path.is_file():
            try:
                lines.extend(ignore_path.read_text(errors="replace").splitlines())
            except OSError:
                pass
    return pathspec.PathSpec.from_lines("gitignore", lines)


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


def discover(root: Path) -> tuple[list[DiscoveredFile], dict[str, int]]:
    """Walk `root` and return (discovered files, exclusion reason counts).

    Bounded: never traverses outside root, never follows symlinks that
    resolve outside root. Honors root-level .gitignore and
    .opencontextuallyignore. Excludes only the narrow DENYLIST_DIRS, plus
    binaries and oversize files.

    Exclusion reason bucket keys are exactly: "ignored", "binary", "oversize".
    """
    root = root.resolve()
    ignore_spec = _load_ignore_spec(root)

    discovered: list[DiscoveredFile] = []
    reasons: dict[str, int] = {REASON_IGNORED: 0, REASON_BINARY: 0, REASON_OVERSIZE: 0}

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dir_abs = Path(dirpath)

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
            if ignore_spec.match_file(d_rel + "/"):
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

            if ignore_spec.match_file(f_rel):
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

    return discovered, reasons
