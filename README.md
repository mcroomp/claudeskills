# claudeskills

A collection of MCP servers and Claude Code skills for developer productivity.

Each tool lives in its own subdirectory and ships with a setup script that registers it as an MCP server with a single command.

## MCPs

### `codesearch/`

Full-text and structural code search for a large source tree (C#, C++, Python, and more).

Runs a [Typesense](https://typesense.org) search server and exposes search as MCP tools so Claude can query the codebase directly without copy-pasting code into the chat.

**Tools:** `search_code`, `query_cs`, `service_status`

See [`codesearch/README.md`](codesearch/README.md) for setup and usage.

### `d3figurer/`

Generates D3.js data visualizations from natural language descriptions.

**Tools:** _(see `d3figurer/README.md`)_

See [`d3figurer/README.md`](d3figurer/README.md) for setup and usage.

## Installation pattern

Each MCP is self-contained and registers itself via a single setup script:

```bat
codesearch\setup_mcp.cmd
d3figurer\setup_mcp.cmd
```

Then restart Claude Code. The tools for each MCP will be available in your session.

## Keywords

MCP server · Claude Code tools · code search · Typesense · tree-sitter · C# AST · monorepo search · D3.js · data visualization · WSL · Windows
