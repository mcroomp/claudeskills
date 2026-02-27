#!/usr/bin/env bash
# Self-locating d3figurer MCP server launcher — no hardcoded paths.
# Registered via setup_mcp.sh; works wherever the repo is cloned.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="${D3FIGURER_WORK_DIR:-$HOME/.d3figurer-work}"
exec NODE_PATH="$WORK_DIR/d3figurer/node_modules" node "$DIR/mcp_server.js"
