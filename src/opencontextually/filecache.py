"""Per-run cache of file content and derived AST facts.

Four stages -- scoring (selector._analyze), the import graph
(selector._build_import_graph), test-reference stems (checks.py), and
excerpt extraction (selector.attach_excerpts) -- each used to open, read,
and `ast.parse`/`ast.walk` the same files independently. On a large repo
(e.g. django, ~2,900 .py files) that meant every file was parsed up to four
times and fully walked up to four times, which profiling showed dominates
wall time (ast.walk/ast.iter_child_nodes were the top two cumulative-time
entries).

RunCache fixes this by reading and parsing each file **at most once per
run** and caching the *derived facts* each stage needs -- not the raw AST
tree itself, to keep peak memory reasonable on a multi-thousand-file repo.
A single `ast.walk` per file collects every node type any stage might need
(def/class nodes, import statements, identifier-reference nodes, `if`
statements) in one pass; consumers then iterate the cached lists instead of
re-walking the tree. Because it is the *same* underlying `ast.walk`
traversal just filtered differently, the node order and membership in each
cached list is identical to what each stage's own independent walk would
have produced -- this is a pure performance change, not a behavior change.

Parsing is lazy: a file's content/AST facts are only computed the first
time some stage actually asks about that file (`get_content`/`get_record`),
not eagerly for the whole repo up front.

**Lifetime:** a RunCache must be constructed fresh for each `get_context()`
call and discarded afterward -- never stored at module/global scope. The
MCP server process is long-lived and handles many `get_context` calls over
its lifetime; a shared cache would accumulate unbounded memory across calls
and could serve stale facts if files change between calls. `get_context`
builds one RunCache per call and threads it through explicitly.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_IMPORT_TYPES = (ast.Import, ast.ImportFrom)
# Node types checks._python_identifier_words draws words from, besides
# def/class nodes (which it also draws from -- see FileRecord.identifier_nodes).
_WORD_NODE_TYPES = (ast.Name, ast.Attribute, ast.arg, ast.alias, ast.keyword)


@dataclass
class FileRecord:
    """Cached content and derived AST facts for one file.

    `defs`, `imports`, `identifier_nodes`, and `if_nodes` are populated by a
    single `ast.walk` over the file's parse tree, filtered by node type --
    each list holds the actual AST node objects, in the same order a
    dedicated `ast.walk` filtered to just that type would yield them.
    Left empty (not None) when the file has no content, fails to parse, or
    parsing hasn't been requested yet -- callers never need a None check.
    """

    content: str = ""
    content_ok: bool = False
    parsed: bool = False
    parse_ok: bool = False
    defs: list = field(default_factory=list)
    imports: list = field(default_factory=list)
    identifier_nodes: list = field(default_factory=list)
    if_nodes: list = field(default_factory=list)


class RunCache:
    """Per-run cache keyed by discovered-file path. See module docstring."""

    def __init__(self) -> None:
        self._records: dict[str, FileRecord] = {}

    def _record_for(self, discovered_file) -> FileRecord:
        rec = self._records.get(discovered_file.path)
        if rec is None:
            rec = FileRecord()
            self._records[discovered_file.path] = rec
        return rec

    def get_content(self, discovered_file) -> str:
        """Read `discovered_file`'s text content, memoized. Returns "" (and
        never raises) if the file cannot be read, matching every call site
        this replaces.
        """
        rec = self._record_for(discovered_file)
        if not rec.content_ok and not rec.parsed:
            try:
                rec.content = discovered_file.abs_path.read_text(errors="replace")
                rec.content_ok = True
            except OSError:
                rec.content = ""
                rec.content_ok = False
        return rec.content

    def get_record(self, discovered_file) -> FileRecord:
        """Return the FileRecord for `discovered_file`, parsing and
        AST-walking it on first request (a no-op thereafter). A
        SyntaxError (or empty content) leaves `parse_ok` False and every
        node list empty -- never raises, matching every ast.parse() call
        site this replaces, which all tolerate SyntaxError by skipping.
        """
        rec = self._record_for(discovered_file)
        if rec.parsed:
            return rec

        content = self.get_content(discovered_file)
        rec.parsed = True
        if not content:
            return rec

        try:
            tree = ast.parse(content)
        except (SyntaxError, ValueError):
            # ValueError alongside SyntaxError: some call sites this
            # centralizes (selector._imports_of / _imported_names_of) parse
            # a file's raw text as read (embedded null bytes possible past
            # discovery's 8KB binary sniff window), which ast.parse rejects
            # with ValueError rather than SyntaxError. Catching both here
            # keeps every consumer at least as tolerant as it was before
            # centralization -- never a crash where one wasn't possible
            # previously.
            return rec

        rec.parse_ok = True
        for node in ast.walk(tree):
            if isinstance(node, _DEF_TYPES):
                rec.defs.append(node)
                rec.identifier_nodes.append(node)
            elif isinstance(node, _IMPORT_TYPES):
                rec.imports.append(node)
            elif isinstance(node, _WORD_NODE_TYPES):
                rec.identifier_nodes.append(node)
            elif isinstance(node, ast.If):
                rec.if_nodes.append(node)
        return rec
