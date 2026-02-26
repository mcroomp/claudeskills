"""
Typesense heartbeat watchdog.

Runs as a background Windows process. Every CHECK_INTERVAL seconds it hits the
/health endpoint. After FAIL_THRESHOLD consecutive failures it restarts the
server. Also revives the file watcher if it dies while the server is healthy.

Usage (normally started by `ts start` or `ts heartbeat`):
    python heartbeat.py
"""

from __future__ import annotations

import os
import sys
import time
import datetime
import json
import subprocess
import urllib.request

_util_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _util_dir)

from codesearch.config import API_KEY, PORT, HOST  # noqa: E402

_THIS_DIR      = os.path.dirname(os.path.abspath(__file__))
_VENV_PY       = os.path.join(_util_dir, ".venv", "Scripts", "python.exe")
_SERVER_PY     = os.path.join(_THIS_DIR, "start_server.py")
_WATCHER_PY    = os.path.join(_THIS_DIR, "watcher.py")
_SERVER_PID    = os.path.join(_THIS_DIR, "typesense.pid")
_WATCHER_PID   = os.path.join(_THIS_DIR, "watcher.pid")
_HEARTBEAT_PID = os.path.join(_THIS_DIR, "heartbeat.pid")
HEARTBEAT_LOG  = os.path.join(_THIS_DIR, "heartbeat.log")

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
        ["wsl", "bash", "-c", f"kill -0 {pid} 2>/dev/null && echo yes || echo no"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() == "yes"


def _pid_alive_win(pid_file: str) -> bool:
    if not os.path.exists(pid_file):
        return False
    pid = open(pid_file).read().strip()
    if not pid:
        return False
    r = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        capture_output=True, text=True,
    )
    return pid in r.stdout


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
    if not _pid_alive_win(_WATCHER_PID) and _health_ok():
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
        else:
            failures += 1
            _log(f"Health check FAILED ({failures}/{FAIL_THRESHOLD})")
            if failures >= FAIL_THRESHOLD:
                _restart_server()
                failures = 0

        # ── watcher watchdog ───────────────────────────────────────────────────
        if not _pid_alive_win(_WATCHER_PID) and _health_ok():
            _log("Watcher is dead - reviving...")
            _restart_watcher()


if __name__ == "__main__":
    run()
