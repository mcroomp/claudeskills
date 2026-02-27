"""
Typesense heartbeat watchdog.

Runs as a background Windows process. Every CHECK_INTERVAL seconds it hits the
/health endpoint. After FAIL_THRESHOLD consecutive failures it restarts the
server. Also revives the file watcher if it dies while the server is healthy.

Usage (normally started by `ts start` or `ts heartbeat`):
    python heartbeat.py
"""

from __future__ import annotations


def _require_wsl_venv():
    import sys, os
    if sys.platform != "linux":
        sys.exit("ERROR: must run under WSL, not Windows Python.")
    try:
        if "microsoft" not in open("/proc/version").read().lower():
            sys.exit("ERROR: must run under WSL (Microsoft kernel).")
    except OSError:
        sys.exit("ERROR: cannot read /proc/version.")
    if sys.prefix == sys.base_prefix:
        sys.exit("ERROR: must run inside a virtualenv (activate ~/.local/mcp-venv).")
_require_wsl_venv()
del _require_wsl_venv


import os
import sys
import time
import datetime
import json
import subprocess
import urllib.request

_util_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _util_dir)

from codesearch.config import API_KEY, PORT, HOST, COLLECTION  # noqa: E402

import pwd as _pwd
_HOME          = _pwd.getpwuid(os.getuid()).pw_dir
_RUN_DIR       = os.path.join(_HOME, ".local", "typesense")
os.makedirs(_RUN_DIR, exist_ok=True)

_THIS_DIR      = os.path.dirname(os.path.abspath(__file__))
_VENV_PY       = os.path.join(_HOME, ".local", "mcp-venv", "bin", "python")
_SERVER_PY     = os.path.join(_THIS_DIR, "start_server.py")
_WATCHER_PY    = os.path.join(_THIS_DIR, "watcher.py")
_SERVER_PID    = os.path.join(_RUN_DIR, "typesense.pid")
_WATCHER_PID   = os.path.join(_RUN_DIR, "watcher.pid")
_HEARTBEAT_PID = os.path.join(_RUN_DIR, "heartbeat.pid")
_INDEXER_PID   = os.path.join(_RUN_DIR, "indexer.pid")
_INDEXER_LOG   = os.path.join(_RUN_DIR, "indexer.log")
HEARTBEAT_LOG  = os.path.join(_RUN_DIR, "heartbeat.log")

CHECK_INTERVAL = 30   # seconds between checks
FAIL_THRESHOLD = 3    # consecutive failures before restarting the server
HEALTH_TIMEOUT = 5    # seconds to wait for a /health response


# ── logging ────────────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(HEARTBEAT_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ── probes ─────────────────────────────────────────────────────────────────────

def _health_ok() -> bool:
    url = f"http://{HOST}:{PORT}/health"
    try:
        with urllib.request.urlopen(url, timeout=HEALTH_TIMEOUT) as r:
            body = json.loads(r.read())
            return bool(body.get("ok", False))
    except Exception:
        return False


def _pid_alive_wsl(pid_file: str) -> bool:
    if not os.path.exists(pid_file):
        return False
    pid = open(pid_file).read().strip()
    if not pid:
        return False
    r = subprocess.run(
        ["bash", "-c", f"kill -0 {pid} 2>/dev/null && echo yes || echo no"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() == "yes"



# ── index status ───────────────────────────────────────────────────────────────

def _index_status() -> str:
    """Return a short string describing the index state, e.g. '42,301 docs' or 'no index'."""
    url = f"http://{HOST}:{PORT}/collections/{COLLECTION}"
    req = urllib.request.Request(url, headers={"X-TYPESENSE-API-KEY": API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            stats = json.loads(r.read())
        ndocs = stats.get("num_documents", 0)
        status = f"{ndocs:,} docs"
    except Exception:
        return "no index"

    # Check if the indexer is actively running
    if _pid_alive_wsl(_INDEXER_PID):
        progress = ""
        if os.path.exists(_INDEXER_LOG):
            try:
                with open(_INDEXER_LOG, encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                last = lines[-1].rstrip() if lines else ""
                # Trim long lines so the log stays readable
                if len(last) > 80:
                    last = last[:77] + "..."
                if last:
                    progress = f" — {last}"
            except OSError:
                pass
        return f"{status} [indexing{progress}]"

    return status


# ── recovery ───────────────────────────────────────────────────────────────────

def _restart_server() -> None:
    _log("Stopping Typesense server...")
    subprocess.run([_VENV_PY, _SERVER_PY, "--stop"], capture_output=True)
    time.sleep(2)
    _log("Starting Typesense server...")
    result = subprocess.run([_VENV_PY, _SERVER_PY], capture_output=True, text=True)
    if result.returncode == 0:
        _log("Server restarted OK.")
    else:
        _log(f"Server restart FAILED (rc={result.returncode}): {result.stderr[:300]}")


def _restart_watcher() -> None:
    _log("Restarting file watcher...")
    p = subprocess.Popen(
        [_VENV_PY, _WATCHER_PY],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    open(_WATCHER_PID, "w").write(str(p.pid))
    _log(f"Watcher started (PID {p.pid})")


# ── main loop ──────────────────────────────────────────────────────────────────

def run() -> None:
    open(_HEARTBEAT_PID, "w").write(str(os.getpid()))
    _log(
        f"Heartbeat started  PID={os.getpid()}  "
        f"interval={CHECK_INTERVAL}s  threshold={FAIL_THRESHOLD}"
    )

    # Ensure watcher is running immediately on startup
    if not _pid_alive_wsl(_WATCHER_PID) and _health_ok():
        _log("Watcher not running - starting it...")
        _restart_watcher()

    failures = 0
    while True:
        time.sleep(CHECK_INTERVAL)

        # ── server health ──────────────────────────────────────────────────────
        if _health_ok():
            if failures > 0:
                _log(f"Server recovered after {failures} failure(s).")
            failures = 0
            _log(f"OK  index={_index_status()}")
        else:
            failures += 1
            _log(f"Health check FAILED ({failures}/{FAIL_THRESHOLD})  index=?")
            if failures >= FAIL_THRESHOLD:
                _restart_server()
                failures = 0

        # ── watcher watchdog ───────────────────────────────────────────────────
        if not _pid_alive_wsl(_WATCHER_PID) and _health_ok():
            _log("Watcher is dead - reviving...")
            _restart_watcher()


if __name__ == "__main__":
    run()
