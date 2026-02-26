# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A code search tool for a large source tree (98K+ files: C#, C++, Python, etc.). It runs a [Typesense](https://typesense.org) full-text search server and exposes search as MCP tools (`search_code`, `query_cs`, `service_status`) so Claude can query the codebase directly. The source root is configured via the `CODESEARCH_SRC_ROOT` environment variable.

## Service management

The main CLI entry point is `ts.cmd` (a wrapper around `service.py`):

```
ts status                    # show server, watcher, heartbeat, indexer state
ts start                     # start Typesense (WSL) + watcher + heartbeat
ts stop                      # stop everything
ts restart
ts index                     # re-index SRC_ROOT in background
ts index --reset             # drop + recreate collection, then re-index
ts log                       # tail Typesense server log (WSL)
ts log --indexer [-n 40]     # tail indexer log
ts log --heartbeat
```

These commands expect a virtualenv at `.venv\Scripts\python.exe` (relative to the repo root).

`ts.cmd` is a local wrapper script (not checked in) that invokes `service.py`. Create it as:
```bat
@echo off
.venv\Scripts\python.exe codesearch\service.py %*
```

## Running tests

Smoke tests run against the live Typesense index and real file system:

```
# Windows
.venv\Scripts\python.exe codesearch\test_skills.py

# WSL
~/.local/mcp-venv/bin/python codesearch/test_skills.py
```

Tests cover platform detection, AST parsing (`_base_type_names`, `q_field_type`, `q_param_type`), `process_file` on well-known files, `files_from_search`, `search()` modes, and the combined Typesense + tree-sitter flow. Typesense must be running for search-dependent tests.

## Direct CLI tools

```
# Full-text search
python codesearch/search.py "IStorageProvider"
python codesearch/search.py "GetItemsAsync" --ext cs --sub myservice
python codesearch/search.py "IStorageProvider" --implements
python codesearch/search.py "GetItemsAsync" --callers
python codesearch/search.py "Obsolete" --attr
python codesearch/search.py "ItemInfo" --uses

# Structural C# AST queries
python codesearch/query.py --methods ItemProcessor.cs
python codesearch/query.py --calls DeleteItems "$SRC_ROOT/myapp/**/*.cs"
python codesearch/query.py --implements IStorageProvider --search "IStorageProvider"
python codesearch/query.py --field-type StorageProvider --search "StorageProvider"
python codesearch/query.py --find Process ItemProcessor.cs
```

## MCP server registration

The MCP server runs via WSL Python (requires `mcp>=0.1`, Python ≥ 3.10). The WSL venv is at `~/.local/mcp-venv/`.

Run once from any directory — no hardcoded paths:
```
codesearch\setup_mcp.cmd
```

This converts the `codesearch/` directory to a WSL path and registers `codesearch/mcp.sh` (a self-locating wrapper) with `claude mcp add --scope user`.

## Architecture

### Two-layer search strategy

1. **Typesense** — fast keyword/text/facet search over pre-indexed metadata. Results include subsystem, extension, class/method names, base types, call sites, attributes. Runs inside WSL for performance; data stored in `~/.local/typesense/` in WSL.

2. **tree-sitter** (`query.py`) — precise C# AST queries on the file set returned by Typesense. Understands syntax: skips comments/strings, distinguishes type references from method calls, traverses inheritance hierarchies.

The typical flow for `query_cs`: Typesense narrows the haystack to ~50 relevant files → tree-sitter parses each file and applies the structural query.

### Process topology

| Process | Platform | Purpose |
|---------|----------|---------|
| Typesense server | WSL | Full-text search backend on port 8108 |
| `watcher.py` | Windows | Watches `SRC_ROOT` via watchdog; debounces and upserts changes into Typesense |
| `heartbeat.py` | Windows | Health-checks Typesense every 30s; auto-restarts server or watcher on failure |
| `indexer.py` | Windows | One-shot full re-index using `git ls-files` to enumerate source files |
| `mcp_server.py` | WSL | FastMCP server exposing tools to Claude |

### Key modules

- **`start_server.py`** — downloads the Typesense Linux binary to `~/.local/typesense/` on first run (auto-invoked by `service.py start`). Server log at `/tmp/typesense.log` (WSL).
- **`config.py`** — all shared constants (API key, port, collection name, `SRC_ROOT`, `INCLUDE_EXTENSIONS`, `EXCLUDE_DIRS`). Also handles Windows ↔ WSL path conversion via `to_native_path()` and `IS_WSL`.
- **`indexer.py`** — walks source via `git ls-files`, uses tree-sitter to extract rich C# metadata (namespace, class/method names, base types, call sites, method signatures, type refs, attributes, usings), batches into Typesense.
- **`search.py`** — Typesense search with mode-based `query_by` selection (text / symbols / implements / callers / sig / uses / attr).
- **`query.py`** — tree-sitter AST query functions (`q_classes`, `q_methods`, `q_calls`, `q_implements`, `q_uses`, `q_field_type`, `q_param_type`, `q_casts`, `q_ident`, `q_find`, `q_params`, `q_attrs`, `q_usings`). Also contains `files_from_search()` which bridges Typesense results to local file paths.
- **`mcp_server.py`** — wraps `search.py` and `query.py` as MCP tools. Captures stdout from format functions into a StringIO buffer to return as strings.

### Typesense schema fields

The collection `codesearch_files` has tiered semantic fields:
- **T1** (higher precision): `base_types`, `call_sites`, `method_sigs`
- **T2** (broader coverage): `type_refs`, `attributes`, `usings`
- Search ranking: `.cs` files have `priority=3`, native (`.h/.cpp`) = 2, scripts = 1, config/docs = 0

### Subsystems

The `subsystem` field is the first path component under `SRC_ROOT`. Use `--sub` / `search_sub` to scope searches to a subsystem.
