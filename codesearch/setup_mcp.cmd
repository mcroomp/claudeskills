@echo off
:: Register (or unregister) the codesearch MCP server with Claude Code.
::
:: Usage:
::   setup_mcp.cmd <src-dir>         -- install: write config.json, set up WSL venv, register MCP
::   setup_mcp.cmd --uninstall       -- unregister MCP server (venv is left in place)
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

:: ── Require src-dir argument ───────────────────────────────────────────────
if "%~1"=="" (
    echo Usage: setup_mcp.cmd ^<src-dir^> [api-key]
    echo   src-dir  Windows path to the source tree to index ^(e.g. Q:\spocore\src^)
    echo   api-key  Typesense API key ^(default: codesearch-local^)
    exit /b 1
)

set "SRC_DIR=%~1"
set "API_KEY=codesearch-local"
if not "%~2"=="" set "API_KEY=%~2"

:: ── Convert paths for WSL ──────────────────────────────────────────────────
for /f "delims=" %%p in ('wsl wslpath -u "%REPO%"') do set WSL_REPO=%%p

:: ── [1/3] Write config.json ────────────────────────────────────────────────
echo.
echo [1/3] Writing codesearch/config.json ...
set "SRC_FWD=%SRC_DIR:\=/%"
(echo {) > "%REPO%\config.json"
(echo   "src_root": "%SRC_FWD%",) >> "%REPO%\config.json"
(echo   "api_key": "%API_KEY%") >> "%REPO%\config.json"
(echo }) >> "%REPO%\config.json"
if errorlevel 1 (
    echo ERROR: Failed to write config.json.
    exit /b 1
)
echo   src_root = %SRC_FWD%
echo   api_key  = %API_KEY%

:: ── [2/3] Set up WSL venv ──────────────────────────────────────────────────
echo.
echo [2/3] Setting up WSL venv at ~/.local/mcp-venv/ ...
wsl -e bash -c "python3 -m venv ~/.local/mcp-venv && ~/.local/mcp-venv/bin/pip install --quiet --upgrade mcp typesense tree-sitter tree-sitter-c-sharp watchdog && echo '  packages installed'"
if errorlevel 1 (
    echo ERROR: Failed to create WSL venv. Is WSL installed?
    exit /b 1
)

:: ── [3/3] Register MCP ────────────────────────────────────────────────────
echo.
echo [3/3] Registering MCP server with Claude Code ...
claude mcp remove --scope user tscodesearch >nul 2>&1
claude mcp add --scope user tscodesearch -- wsl bash "%WSL_REPO%/mcp.sh"
if errorlevel 1 (
    echo ERROR: Failed to register MCP server.
    exit /b 1
)

echo.
echo Done. Restart Claude Code for the change to take effect.
endlocal
