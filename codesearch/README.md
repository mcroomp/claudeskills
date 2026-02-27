# codesearch

Full-text and structural code search for a large monorepo. Runs a [Typesense](https://typesense.org) search server and exposes results as MCP tools so Claude can query the codebase directly without copy-pasting.

## Prerequisites

- Windows 11 with WSL2
- Python 3.10+ (Windows virtualenv at `..\.venv\`, WSL venv at `~/.local/mcp-venv/`)
- Typesense binary (auto-downloaded to `~/.local/typesense/` on first `ts start`)

## Setup

### 1. Create the service wrapper

Create `ts.cmd` one level up (not checked in):
```bat
@echo off
.venv\Scripts\python.exe codesearch\service.py %*
```

### 2. Register the MCP server (once)

```
codesearch\setup_mcp.cmd
```

Restart Claude Code after running. This registers `mcp.sh` with `claude mcp add --scope user`.

### 3. Start the service

```
ts start
ts index --reset   # first time: build the index
```

## Service management

```
ts status                  # show server, watcher, heartbeat, indexer state
ts start                   # start Typesense (WSL) + watcher + heartbeat
ts stop                    # stop everything
ts restart
ts index                   # re-index in background (incremental)
ts index --reset           # drop + recreate collection, then re-index
ts log                     # tail Typesense server log (WSL)
ts log --indexer [-n 40]   # tail indexer log
ts log --heartbeat         # tail heartbeat log
```

## Running tests

Tests run against the live Typesense index and real file system:

```
# Windows
.venv\Scripts\python.exe codesearch\test_skills.py

# WSL
~/.local/mcp-venv/bin/python codesearch/test_skills.py
```

## Direct CLI usage

```
# Full-text search
python codesearch/search.py "MyInterface"
python codesearch/search.py "MyMethod" --ext cs --sub mysubsystem
python codesearch/search.py "MyInterface" --implements
python codesearch/search.py "MyMethod" --callers
python codesearch/search.py "Obsolete" --attr
python codesearch/search.py "MyType" --uses

# Structural C# AST queries
python codesearch/query.py --methods MyClass.cs
python codesearch/query.py --calls MyMethod "src/mysubsystem/**/*.cs"
python codesearch/query.py --implements IMyInterface --search "IMyInterface"
python codesearch/query.py --field-type MyType --search "MyType"
python codesearch/query.py --find MyMethod MyClass.cs
```

## Architecture

### Two-layer search

1. **Typesense** — fast keyword/facet search over pre-indexed metadata (class names, method names, base types, call sites, etc.). Runs in WSL; data stored at `~/.local/typesense/`.

2. **tree-sitter** — precise C# AST queries on the file set returned by Typesense. Skips comments and string literals, understands syntax.

Typical flow: Typesense narrows the haystack to ~50 files → tree-sitter parses each one and applies the structural query.

### Process topology

| Process | Platform | Purpose |
|---------|----------|---------|
| Typesense server | WSL | Full-text search backend on port 8108 |
| `watcher.py` | Windows | Watches the source root; debounces and upserts changes into Typesense |
| `heartbeat.py` | Windows | Health-checks every 30s; auto-restarts server or watcher on failure |
| `indexer.py` | Windows | One-shot full re-index via `git ls-files` |
| `mcp_server.py` | WSL | FastMCP server exposing tools to Claude |

### Files

| File | Purpose |
|------|---------|
| `config.py` | Shared constants — API key, port, collection name, `SRC_ROOT`, path conversion |
| `service.py` | CLI service manager (`start`, `stop`, `status`, `index`, `log`, …) |
| `start_server.py` | Downloads Typesense binary; starts/stops the WSL server process |
| `indexer.py` | Full re-index: walks via `git ls-files`, extracts C# metadata with tree-sitter |
| `watcher.py` | Incremental updates: watchdog on the source root, upserts changed files |
| `heartbeat.py` | Watchdog loop — restarts server/watcher if they die |
| `search.py` | Typesense search with mode-based `query_by` selection |
| `query.py` | tree-sitter AST queries + `files_from_search()` bridge |
| `mcp_server.py` | FastMCP wrapper exposing `search_code`, `query_cs`, `service_status` |
| `test_skills.py` | Smoke tests against live index and file system |
| `mcp.sh` | Self-locating WSL launcher for `mcp_server.py` |
| `setup_mcp.cmd` | Registers `mcp.sh` with Claude Code (run once) |

### Typesense schema

The collection uses tiered semantic fields:

| Tier | Fields | Precision |
|------|--------|-----------|
| T1 | `base_types`, `call_sites`, `method_sigs` | High — specific symbols |
| T2 | `type_refs`, `attributes`, `usings` | Broader — file-level coverage |

Search ranking: `.cs` files `priority=3` → native (`.h/.cpp`) `2` → scripts `1` → config/docs `0`.

The `subsystem` field is the first path component under the source root. Use `--sub` to scope searches to a subsystem.
