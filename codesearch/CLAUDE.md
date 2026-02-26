# codesearch — developer notes for Claude

This directory contains the full-text and structural search stack for a large monorepo. See `README.md` for setup and usage. These notes cover what you need to know when reading or modifying the code.

## Module responsibilities

| Module | Responsibility |
|--------|---------------|
| `config.py` | All shared constants and the Windows↔WSL path converter (`to_native_path`). Import from here — never hardcode paths or port numbers elsewhere. |
| `service.py` | Service manager CLI. Owns process lifecycle (start/stop/status) and pid files. Uses `tasklist` for Windows processes, `kill -0` via WSL for the Typesense server. |
| `start_server.py` | Downloads the Typesense Linux binary to `~/.local/typesense/` on first run and manages the WSL server process. Called by `service.py start`. |
| `indexer.py` | One-shot full re-index. Walks the repo with `git ls-files`, calls tree-sitter to extract C# metadata, batches upserts into Typesense. |
| `watcher.py` | Incremental updates. Uses `watchdog` to monitor the source root; debounces events and upserts changed files. |
| `heartbeat.py` | Watchdog loop running on Windows; checks Typesense health every 30s and restarts server or watcher if they die. |
| `search.py` | Wraps the Typesense client. `search()` selects `query_by` fields based on mode; `format_results()` prints human-readable output. |
| `query.py` | All tree-sitter AST query functions plus `files_from_search()` which converts Typesense hits to native file paths. |
| `mcp_server.py` | FastMCP wrapper. Captures `format_results` stdout into `StringIO` to return strings to Claude. |
| `test_skills.py` | Smoke tests against live Typesense index and real file system. |

## Key patterns

### Windows ↔ WSL path conversion

Typesense always stores Windows-style paths. When code runs under WSL (e.g., `mcp_server.py`), `to_native_path()` in `config.py` converts them to `/mnt/...` equivalents. `files_from_search()` in `query.py` applies this automatically, so callers always get platform-correct paths.

`IS_WSL` is detected once at import time via `/proc/version`. Don't re-detect it at call time.

### Stdout capture in mcp_server.py

`format_results()` and `process_file()` write to `sys.stdout`. `mcp_server.py` captures this with a `StringIO` swap:

```python
buf = io.StringIO()
sys.stdout, old = buf, sys.stdout
try:
    format_results(...)
finally:
    sys.stdout = old
return buf.getvalue().strip()
```

Don't change `format_results` or `process_file` to return strings — other callers (CLI) depend on the print-based interface.

### Pid files

Service processes write their PID to `codesearch/*.pid` on startup. `service.py` uses these to check liveness and kill processes on stop. WSL server pids are checked with `kill -0` via `wsl bash -c`; Windows pids use `tasklist`.

The `.gitignore` excludes `*.pid` and `*.log`.

## Typesense schema — search mode mapping

| `search_code` mode | `query_by` field(s) | What it finds |
|--------------------|---------------------|---------------|
| `text` (default) | filename, class/method names, content | Broad keyword search |
| `symbols` | class/interface/method names only | Faster symbol lookup |
| `implements` | `base_types` (T1) | Types that inherit/implement the query |
| `callers` | `call_sites` (T1) | Files that call the query method |
| `sig` | `method_sigs` (T1) | Methods whose signature contains the query |
| `uses` | `type_refs` (T2) | Files that reference the query type |
| `attr` | `attributes` (T2) | Files decorated with the query attribute |

## tree-sitter query modes

`query.py` exports one function per mode: `q_uses`, `q_calls`, `q_implements`, `q_field_type`, `q_param_type`, `q_casts`, `q_ident`, `q_methods`, `q_fields`, `q_classes`, `q_find`, `q_params`, `q_attrs`, `q_usings`.

`process_file(path, mode, mode_arg, ...)` dispatches to the right function and prints matches. It returns the match count, which `mcp_server.py` uses for `count_only` mode.

`files_from_search(query, sub, ext, limit)` calls `search.py` and converts results to native paths. It's the standard bridge between Typesense pre-filtering and tree-sitter parsing.

## Testing

`test_skills.py` uses well-known stable facts about the indexed codebase as test fixtures. Tests are pass/fail printed — no test framework dependency. Typesense must be running. Run with either the Windows venv or WSL mcp-venv.

When adding a new search mode or AST query function, add a corresponding test section.

## Things to watch out for

- **Path separators**: Typesense stores Windows-style paths with forward slashes. Windows `os.path` functions may return backslashes. Use `to_native_path()` and normalise with `.replace("\\", "/")` when comparing paths.
- **Output truncation**: `mcp_server.py` caps `query_cs` output at 200 lines to avoid context overflow. If a query produces more, it appends a `[truncated]` note.
- **Glob vs Typesense pre-filter**: For modes with a pattern, `query_cs` uses Typesense to find candidate files and then filters by glob in-memory — no filesystem traversal. For pattern-less modes (`methods`, `classes`, etc.) it must actually glob the filesystem; warn if >300 files matched.
- **sys.path**: Both `mcp_server.py` and `service.py` insert the repo root (`..`) into `sys.path` at the top of the file so that `from codesearch.xxx import ...` works correctly.
