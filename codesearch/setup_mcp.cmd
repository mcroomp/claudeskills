@echo off
:: Register (or unregister) the codesearch MCP server with Claude Code.
::
:: Usage:
::   setup_mcp.cmd             -- install: create WSL venv, install packages, register
::   setup_mcp.cmd --uninstall -- unregister MCP server (venv is left in place)
setlocal

set "REPO=%~dp0"
set "REPO=%REPO:~0,-1%"

:: ── Uninstall path ─────────────────────────────────────────────────────────
if /i "%~1"=="--uninstall" (
    echo Removing codesearch MCP server ...
    claude mcp remove --scope user tscodesearch
    if errorlevel 1 (
        echo WARNING: claude mcp remove failed ^(server may not have been registered^).
    ) else (
        echo Done. Restart Claude Code for the change to take effect.
    )
    goto :eof
)

:: ── Install path ───────────────────────────────────────────────────────────
for /f "delims=" %%p in ('wsl wslpath -u "%REPO%"') do set WSL_REPO=%%p

echo.
echo [1/2] Setting up WSL venv at ~/.local/mcp-venv/ ...
wsl -e bash -c "python3 -m venv ~/.local/mcp-venv && ~/.local/mcp-venv/bin/pip install --quiet --upgrade mcp typesense tree-sitter tree-sitter-c-sharp watchdog && echo '  packages installed'"
if errorlevel 1 (
    echo ERROR: Failed to create WSL venv. Is WSL installed?
    exit /b 1
)

echo.
echo [2/2] Registering MCP server with Claude Code ...
claude mcp remove --scope user tscodesearch >nul 2>&1
claude mcp add --scope user tscodesearch -- wsl bash "%WSL_REPO%/mcp.sh"
if errorlevel 1 (
    echo ERROR: Failed to register MCP server.
    exit /b 1
)

echo.
echo Done. Restart Claude Code for the change to take effect.
endlocal
