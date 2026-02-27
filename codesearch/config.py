"""Shared configuration for Typesense search tooling."""

PORT = 8108
HOST = "localhost"

import json
import os
import re
import sys

UTIL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TYPESENSE_DIR = os.path.join(UTIL_DIR, "typesense")
DATA_DIR = os.path.join(TYPESENSE_DIR, "data")
BIN_DIR  = os.path.join(TYPESENSE_DIR, "bin")

# ── WSL detection and path conversion ────────────────────────────────────────
# Typesense always stores Windows-style paths (forward slashes, drive letter).
# When running in WSL these must be converted to /mnt/... for os.path calls.

def _detect_wsl() -> bool:
    if sys.platform != "linux":
        return False
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False

IS_WSL = _detect_wsl()

def to_native_path(path: str) -> str:
    """Convert a Windows-style path (X:/...) to the current platform's path.

    On Windows: returns the path as-is (with forward slashes normalised).
    On WSL:     X:/foo/bar  →  /mnt/x/foo/bar
    """
    # Normalise backslashes first
    path = path.replace("\\", "/")
    if IS_WSL:
        m = re.match(r"^([A-Za-z]):(.*)", path)
        if m:
            return f"/mnt/{m.group(1).lower()}{m.group(2)}"
    return path

# ── config.json ───────────────────────────────────────────────────────────────
# Stores src_root and api_key. Written by setup_mcp.cmd / setup_mcp.sh.
# Format: {"src_root": "Q:/my/repo/src", "api_key": "codesearch-local"}
_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def _read_config() -> dict:
    try:
        with open(_CONFIG_FILE) as _f:
            return json.load(_f)
    except (OSError, json.JSONDecodeError):
        return {}

_CONFIG = _read_config()

API_KEY: str = _CONFIG.get("api_key", "codesearch-local")

# ── Source root ───────────────────────────────────────────────────────────────
# Windows-style forward-slash path (what Typesense stores)
SRC_ROOT_WIN = _CONFIG.get("src_root", "").replace("\\", "/").rstrip("/")
# Platform-native path (for file I/O)
SRC_ROOT = to_native_path(SRC_ROOT_WIN) if SRC_ROOT_WIN else ""

TYPESENSE_VERSION = "27.1"
TYPESENSE_ZIP_URL = (
    f"https://dl.typesense.org/releases/{TYPESENSE_VERSION}/"
    f"typesense-server-{TYPESENSE_VERSION}-win-amd64.zip"
)
TYPESENSE_EXE = os.path.join(BIN_DIR, "typesense-server.exe")

INCLUDE_EXTENSIONS = {
    # C# (full symbol extraction via tree-sitter)
    ".cs",
    # Native C/C++
    ".cpp", ".c", ".h", ".hpp", ".idl",
    # Build system
    ".dsc", ".inc", ".props", ".targets", ".csproj",
    # Scripts
    ".py", ".sh", ".cmd", ".bat", ".ps1",
    # Web/config
    ".ts", ".js", ".json", ".xml", ".yaml", ".yml",
    # Docs
    ".md", ".txt",
    # SQL
    ".sql",
}

EXCLUDE_DIRS = {
    "Target", "Build", "Import", "nugetcache",
    ".git", "obj", "bin", "node_modules", ".venv",
    "target", "debug", "ship", "x64", "x86",
    "__pycache__", ".vs",
}

MAX_FILE_BYTES = 512 * 1024   # skip files larger than 512 KB
MAX_CONTENT_CHARS = 30000     # truncate content stored in Typesense

COLLECTION = "codesearch_files"

TYPESENSE_CLIENT_CONFIG = {
    "nodes": [{"host": HOST, "port": str(PORT), "protocol": "http"}],
    "api_key": API_KEY,
    "connection_timeout_seconds": 5,
}
