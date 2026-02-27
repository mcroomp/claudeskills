#!/usr/bin/env bash
# Git Bash wrapper — mirrors ts.cmd but usable from bash
REPO="$(cd "$(dirname "$0")" && pwd -W 2>/dev/null || pwd)"
wsl --cd "$REPO" '$HOME/.local/mcp-venv/bin/python' service.py "$@"
