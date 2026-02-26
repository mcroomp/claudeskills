# claudeskills

Tools and configuration that extend [Claude Code](https://claude.ai/code) for working in a large monorepo.

## Contents

### `codesearch/`

Full-text and structural code search for a large source tree (C#, C++, Python, and more).

Runs a [Typesense](https://typesense.org) search server and exposes search as MCP tools so Claude can query the codebase directly without copy-pasting code into the chat.

**MCP tools exposed to Claude:**
- `search_code` — keyword, symbol, and semantic search (implements, callers, uses, attr)
- `query_cs` — structural C# AST queries (methods, calls, field types, etc.) via tree-sitter
- `service_status` — check health of the running search service

See [`codesearch/README.md`](codesearch/README.md) for setup and usage.

### `CLAUDE.md`

Project-level instructions for Claude Code — service management commands, architecture notes, and how to run tests.

## Quick start

```bat
rem 1. Create service wrapper (once)
echo @echo off > ts.cmd
echo .venv\Scripts\python.exe codesearch\service.py %%* >> ts.cmd

rem 2. Register MCP server with Claude Code (once)
codesearch\setup_mcp.cmd

rem 3. Start service and build initial index
ts start
ts index --reset
```

Then restart Claude Code. The `search_code` and `query_cs` tools will be available in your session.

## Keywords

MCP server · Claude Code tools · code search · Typesense · tree-sitter · C# AST · monorepo search · large codebase · code intelligence · symbol search · call graph · type references · incremental indexer · file watcher · WSL · Windows

## Requirements

- Windows 11 with WSL2
- Python 3.10+ virtualenv at `.venv\` (Windows) and `~/.local/mcp-venv/` (WSL)
- Typesense binary — auto-downloaded to `~/.local/typesense/` on first `ts start`
