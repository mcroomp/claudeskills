"""
MCP server for code search.

Exposes Typesense full-text search and tree-sitter C# structural queries
as native Claude tools — no copy-paste, results go straight into context.

Runs via WSL Python 3.10 (mcp requires >=3.10).
Registered with:  setup_mcp.cmd  (run once from repo root)

Tools:
    search_code    - Typesense full-text / semantic search across the index
    query_cs       - tree-sitter structural C# query (uses/calls/implements/...)
    service_status - Check if Typesense is running and how many docs are indexed
"""

from __future__ import annotations

import io
import json
import os
import sys
import urllib.request

# ── Path setup ────────────────────────────────────────────────────────────────
# Add the repo root to sys.path so we can import codesearch.* modules.
# Uses __file__ so this works regardless of where the repo is cloned.
_THIS_DIR  = os.path.dirname(os.path.abspath(__file__))
_UTIL_DIR  = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _UTIL_DIR)

from mcp.server.fastmcp import FastMCP

# ── File resolution ───────────────────────────────────────────────────────────
# Re-use the shared files_from_search from query.py.
# config.py detects WSL and sets to_native_path() accordingly, so
# files_from_search already returns platform-correct paths on both Windows and WSL.

from codesearch.query import files_from_search as _files_from_search

def _wsl_files_from_search(query: str, sub: str | None = None,
                            ext: str = "cs", limit: int = 50) -> list[str]:
    """Delegate to the shared files_from_search (handles WSL path conversion)."""
    return _files_from_search(query=query, sub=sub, ext=ext, limit=limit)


def _glob_to_regex(pattern: str) -> "re.Pattern[str]":
    """
    Convert a glob pattern (supporting ** for recursive matching) to a regex.

    *   matches any character except /
    **  (or **/) matches any sequence of characters including /
    ?   matches any single character except /
    """
    import re as _re
    pattern = pattern.replace("\\", "/")
    parts   = _re.split(r"(\*\*/?|\*|\?)", pattern)
    rx      = ""
    for part in parts:
        if part in ("**/", "**"):
            rx += ".*"
        elif part == "*":
            rx += "[^/]*"
        elif part == "?":
            rx += "[^/]"
        else:
            rx += _re.escape(part)
    return _re.compile("^" + rx + "$", _re.IGNORECASE)


def _ts_search_then_filter(glob_pattern: str, ts_query: str,
                            limit: int = 250) -> tuple[list[str], int]:
    """
    Search Typesense for ts_query, then filter results in-memory against
    glob_pattern — no filesystem glob expansion required.

    Returns (matched_file_list, total_ts_hits).
    """
    ts_files = _files_from_search(query=ts_query, limit=min(limit, 250))
    rx       = _glob_to_regex(glob_pattern)
    matched  = [f for f in ts_files if rx.match(f.replace("\\", "/"))]
    return matched, len(ts_files)


# ── MCP server ────────────────────────────────────────────────────────────────

mcp = FastMCP("codesearch")


@mcp.tool()
def search_code(
    query: str,
    sub:   str = "",
    ext:   str = "",
    limit: int = 20,
    mode:  str = "text",
) -> str:
    """
    Search the code index (C#, C++, Python, etc.)

    Args:
        query: Text or symbol to search for.
        sub:   Filter by subsystem — "myapp", "services", "core", etc.
               Leave empty to search all.
        ext:   Filter by extension — "cs", "h", "py". Default: all (.cs ranked first).
        limit: Maximum results to return. Default 20.
        mode:  Search strategy:
               "text"       — filename + class/method names + full content (default)
               "symbols"    — class/interface/method names only
               "implements" — files where query type appears in base_types (T1 field)
               "callers"    — files where query method appears in call_sites (T1 field)
               "uses"       — files where query type appears in type declarations (T2)
               "sig"        — files where query appears in method signatures (T1)
               "attr"       — files decorated with query attribute name (T2)
    """
    from codesearch.search import search, format_results

    try:
        result, query_by = search(
            query        = query,
            ext          = ext   or None,
            sub          = sub   or None,
            limit        = limit,
            symbols_only = (mode == "symbols"),
            implements   = (mode == "implements"),
            callers      = (mode == "callers"),
            sig          = (mode == "sig"),
            uses         = (mode == "uses"),
            attr         = (mode == "attr"),
        )
    except SystemExit:
        return ("Typesense search failed. Is the server running?\n"
                "Start it with: ts start\n"
                "Check status with: ts status")

    buf = io.StringIO()
    sys.stdout, old = buf, sys.stdout
    try:
        format_results(result, query, query_by, show_facets=True)
    finally:
        sys.stdout = old

    return buf.getvalue().strip() or "No results found."


@mcp.tool()
def query_cs(
    mode:         str,
    pattern:      str = "",
    search_query: str = "",
    search_sub:   str = "",
    files:        str = "",
    context_lines: int = 0,
    count_only:   bool = False,
) -> str:
    """
    Structural C# AST query using tree-sitter.
    Semantically precise: skips comments and string literals, understands syntax.
    Use instead of text search when you need exact type references or call sites.

    Args:
        mode:          Query type — one of:
                       "uses"        every type reference to TYPE in declarations
                       "calls"       every call site of METHOD
                       "implements"  types that inherit or implement TYPE
                       "field_type"  fields/properties declared with TYPE (migration analysis)
                       "param_type"  method/constructor parameters typed as TYPE
                       "casts"       every explicit cast expression (TYPE)expr
                       "ident"       every identifier occurrence (semantic grep — skips comments/strings)
                       "methods"     all method/field/property signatures
                       "fields"      all field/property declarations with types
                       "classes"     all type declarations with base types
                       "find"        full source body of method/type named NAME
                       "params"      parameter list of METHOD
                       "attrs"       all [Attribute] decorators
                       "usings"      all using directives
        pattern:       The TYPE, METHOD, or NAME to search for.
                       Required for: uses, calls, implements, find, params.
                       Optional for: attrs (filters by attribute name when provided).
        search_query:  Typesense query to pre-filter files (STRONGLY RECOMMENDED).
                       Finds ~50 most relevant files via the index before parsing.
                       Example: use "Blobber" to find files mentioning Blobber.
        search_sub:    Subsystem to scope the Typesense pre-filter search.
                       Example: "myapp", "services", "core".
        files:         WSL glob pattern for direct file query, e.g.
                       "$SRC_ROOT/myapp/services/**/*.cs"
                       Use this when you know exactly which directory to search.
        context_lines: Surrounding source lines to show per match (like grep -C N).
        count_only:    Return match counts per file instead of full match text.

    Examples:
        query_cs("uses", "StorageProvider", search_query="StorageProvider", search_sub="myapp")
        query_cs("calls", "DeleteItems", search_query="DeleteItems", search_sub="myapp")
        query_cs("implements", "IStorageProvider", search_query="IStorageProvider")
        query_cs("field_type", "StorageProvider", search_query="StorageProvider")
        query_cs("field_type", "IStorageProvider", search_query="IStorageProvider")
        query_cs("param_type", "StorageProvider", search_query="StorageProvider", search_sub="myapp")
        query_cs("methods", files="$SRC_ROOT/myapp/services/ItemProcessor.cs")
        query_cs("find", "DeleteItems", files="$SRC_ROOT/myservice/StorageApi.cs")
        query_cs("uses", "StorageProvider", search_query="StorageProvider", search_sub="myapp", count_only=True)
    """
    import glob as _glob
    from codesearch.query import process_file

    VALID_MODES = ("uses", "calls", "implements", "methods", "fields",
                   "classes", "find", "params", "attrs", "usings",
                   "field_type", "param_type", "casts", "ident")

    m = mode.lower().strip().replace("-", "_")
    if m not in VALID_MODES:
        return f"Unknown mode: {mode!r}. Valid modes: {', '.join(VALID_MODES)}"

    _PATTERN_REQUIRED = ("uses", "calls", "implements", "find", "params",
                         "field_type", "param_type", "casts", "ident")
    if m in _PATTERN_REQUIRED and not pattern:
        return (f"Mode '{m}' requires a pattern argument. "
                f"Example: query_cs('{m}', 'TypeOrMethodName', search_query='...')")

    # ── Resolve file list ─────────────────────────────────────────────────────
    _prefilter_note = ""

    if search_query:
        file_list = _wsl_files_from_search(
            search_query, sub=search_sub or None, limit=50
        )
    elif files:
        if pattern:
            # Fast path: search Typesense, then filter results by glob pattern
            # in-memory — no filesystem traversal needed.
            file_list, ts_hits = _ts_search_then_filter(files, pattern)
            _prefilter_note = (
                f"[Typesense '{pattern}' → {ts_hits} hits, "
                f"{len(file_list)} matched glob]\n"
            )
        else:
            # Modes with no pattern (classes, methods, fields, usings, attrs)
            # must actually glob — warn if the scope is large.
            expanded  = sorted(_glob.glob(files, recursive=True))
            file_list = [f for f in expanded if os.path.isfile(f)]
            if len(file_list) > 300:
                return (
                    f"Glob matched {len(file_list)} files. Provide a tighter "
                    f"files glob (e.g. a specific subdirectory) for modes "
                    f"that don't take a pattern."
                )
    else:
        return ("Provide either search_query (recommended for large subsystems) "
                "or a files glob pattern.")

    if not file_list:
        return "No matching files found in index or on disk."

    # ── Run tree-sitter query ─────────────────────────────────────────────────
    buf = io.StringIO()
    sys.stdout, old = buf, sys.stdout
    match_counts: dict[str, int] = {}
    try:
        for fpath in file_list:
            n = process_file(
                path       = fpath,
                mode       = m,
                mode_arg   = pattern,
                show_path  = True,
                count_only = False,
                context    = context_lines,
            )
            if n:
                match_counts[fpath] = n
    finally:
        sys.stdout = old

    if count_only:
        rows = sorted(match_counts.items(), key=lambda x: -x[1])
        lines = [f"  {n:4d}  {os.path.basename(p)}" for p, n in rows]
        total = sum(match_counts.values())
        lines.append(f"\nTotal: {total} matches in {len(match_counts)} files "
                     f"(searched {len(file_list)} files)")
        return _prefilter_note + "\n".join(lines)

    output = buf.getvalue().strip()
    if not output:
        return (_prefilter_note or "") + f"No matches found (searched {len(file_list)} files)."

    # Cap output to ~200 lines to avoid context overflow
    output_lines = output.splitlines()
    if len(output_lines) > 200:
        output = "\n".join(output_lines[:200])
        output += f"\n\n[truncated — {len(output_lines) - 200} more lines]"

    return _prefilter_note + output


@mcp.tool()
def service_status() -> str:
    """
    Check whether the Typesense code search service is running.
    Returns server health, document count, and whether the index is up to date.
    If not running, returns instructions to start it.
    """
    from codesearch.config import API_KEY, PORT, HOST, COLLECTION

    url = f"http://{HOST}:{PORT}/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            health = json.loads(r.read())
    except Exception as e:
        return (f"Typesense is NOT running on port {PORT}.\n"
                f"Start it with: ts start\n"
                f"Error: {e}")

    if not health.get("ok"):
        return "Typesense responded but health check returned not-ok."

    req = urllib.request.Request(
        f"http://{HOST}:{PORT}/collections/{COLLECTION}",
        headers={"X-TYPESENSE-API-KEY": API_KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            stats = json.loads(r.read())
        ndocs        = stats.get("num_documents", "?")
        has_priority = any(f["name"] == "priority" for f in stats.get("fields", []))
        return (f"Typesense running on port {PORT}.\n"
                f"Index: {ndocs:,} documents.\n"
                f"Priority field (.cs ranked first): "
                f"{'yes' if has_priority else 'NO — run: ts index --reset'}")
    except Exception:
        return (f"Server running but collection '{COLLECTION}' not found.\n"
                f"Run: ts index --reset")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
