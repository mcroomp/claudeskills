"""
Typesense service manager for code search.

Commands:
    status   - Show server health, document count, indexer/watcher state
    start    - Start server + watcher (if not already running)
    stop     - Stop server, watcher, and any running indexer
    restart  - stop then start
    index    - Run indexer in background (add --reset to recreate collection)
    log      - Tail the Typesense server log
    watcher  - Start the file watcher (standalone)

Usage:
    python service.py <command> [options]
    ts.cmd   <command> [options]
"""

from __future__ import annotations

import os
import sys
import subprocess
import argparse
import time
import urllib.request
import json

_util_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _util_dir)

from codesearch.config import API_KEY, PORT, HOST, COLLECTION, TYPESENSE_VERSION

# ── paths ──────────────────────────────────────────────────────────────────────
_THIS_DIR   = os.path.dirname(os.path.abspath(__file__))
_VENV_PY    = os.path.join(_util_dir, ".venv", "Scripts", "python.exe")
_SERVER_PY  = os.path.join(_THIS_DIR, "start_server.py")
_INDEXER_PY = os.path.join(_THIS_DIR, "indexer.py")
_WATCHER_PY = os.path.join(_THIS_DIR, "watcher.py")
_INDEXER_LOG   = os.path.join(_THIS_DIR, "indexer.log")
_SERVER_PID    = os.path.join(_THIS_DIR, "typesense.pid")
_WATCHER_PID   = os.path.join(_THIS_DIR, "watcher.pid")
_INDEXER_PID   = os.path.join(_THIS_DIR, "indexer.pid")
_HEARTBEAT_PID = os.path.join(_THIS_DIR, "heartbeat.pid")
_HEARTBEAT_LOG = os.path.join(_THIS_DIR, "heartbeat.log")
_HEARTBEAT_PY  = os.path.join(_THIS_DIR, "heartbeat.py")
_WSL_LOG       = "/tmp/typesense.log"


# ── helpers ────────────────────────────────────────────────────────────────────

def _wsl_out(cmd: str) -> str:
    r = subprocess.run(["wsl", "bash", "-c", cmd], capture_output=True, text=True)
    return r.stdout.strip()


def _pid_alive_win(pid_file: str) -> tuple[bool, str]:
    """Return (alive, pid_str) for a Windows-spawned process tracked by pid_file."""
    if not os.path.exists(pid_file):
        return False, ""
    pid = open(pid_file).read().strip()
    if not pid:
        return False, ""
    r = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        capture_output=True, text=True,
    )
    alive = pid in r.stdout
    return alive, pid


def _pid_alive_wsl(pid_file: str) -> tuple[bool, str]:
    """Return (alive, pid_str) for a WSL process tracked by pid_file."""
    if not os.path.exists(pid_file):
        return False, ""
    pid = open(pid_file).read().strip()
    if not pid:
        return False, ""
    result = _wsl_out(f"kill -0 {pid} 2>/dev/null && echo yes || echo no")
    return result == "yes", pid


def _typesense_health() -> dict:
    """Return {'ok': bool, 'status': str} from the Typesense health endpoint."""
    url = f"http://{HOST}:{PORT}/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            body = json.loads(r.read())
            return {"ok": body.get("ok", False), "status": "healthy"}
    except Exception as e:
        return {"ok": False, "status": str(e)}


def _collection_stats() -> dict | None:
    """Return Typesense collection stats dict, or None if unavailable."""
    url = f"http://{HOST}:{PORT}/collections/{COLLECTION}"
    req = urllib.request.Request(url, headers={"X-TYPESENSE-API-KEY": API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _kill_win_pid(pid_file: str, label: str) -> None:
    alive, pid = _pid_alive_win(pid_file)
    if alive:
        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
        print(f"  Stopped {label} (PID {pid})")
    else:
        print(f"  {label}: not running")
    if os.path.exists(pid_file):
        os.remove(pid_file)


# ── commands ───────────────────────────────────────────────────────────────────

def cmd_status(args) -> None:
    print("-- Typesense Service Status ------------------------------------------")

    # Server (WSL)
    server_alive, server_pid = _pid_alive_wsl(_SERVER_PID)
    health = _typesense_health()
    if health["ok"]:
        print(f"  Server  : [OK]  running  (WSL pid={server_pid}, port={PORT})")
    elif server_alive:
        print(f"  Server  : [!!] process alive (pid={server_pid}) but health check failed: {health['status']}")
    else:
        print(f"  Server  : [--] not running")

    # Collection stats
    stats = _collection_stats()
    if stats:
        ndocs = stats.get("num_documents", 0)
        fields = [f["name"] for f in stats.get("fields", [])]
        has_priority = "priority" in fields
        print(f"  Index   : {ndocs:,} documents  (priority field: {'yes' if has_priority else 'NO - re-index with: ts index --reset'})")
    elif health["ok"]:
        print(f"  Index   : collection '{COLLECTION}' not found - run: ts index --reset")
    else:
        print(f"  Index   : (server unavailable)")

    # Watcher (Windows process)
    watcher_alive, watcher_pid = _pid_alive_win(_WATCHER_PID)
    if watcher_alive:
        print(f"  Watcher : [OK]  running  (PID {watcher_pid})")
    else:
        print(f"  Watcher : [--] not running")

    # Heartbeat (Windows process)
    hb_alive, hb_pid = _pid_alive_win(_HEARTBEAT_PID)
    if hb_alive:
        last_hb = ""
        if os.path.exists(_HEARTBEAT_LOG):
            with open(_HEARTBEAT_LOG, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            last_hb = f"  last: {lines[-1].rstrip()}" if lines else ""
        print(f"  Heartbt : [OK]  running  (PID {hb_pid}){last_hb}")
    else:
        print(f"  Heartbt : [--] not running")

    # Indexer (Windows process)
    indexer_alive, indexer_pid = _pid_alive_win(_INDEXER_PID)
    if indexer_alive:
        # Show tail of indexer log
        tail = ""
        if os.path.exists(_INDEXER_LOG):
            with open(_INDEXER_LOG, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            tail = lines[-1].rstrip() if lines else ""
        print(f"  Indexer : [>>] running  (PID {indexer_pid})")
        if tail:
            print(f"            {tail}")
    else:
        if os.path.exists(_INDEXER_LOG):
            with open(_INDEXER_LOG, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            last = lines[-1].rstrip() if lines else "(empty)"
            print(f"  Indexer : idle  (last: {last})")
        else:
            print(f"  Indexer : idle")

    print("----------------------------------------------------------------------")


def cmd_start(args) -> None:
    # Start server
    server_alive, _ = _pid_alive_wsl(_SERVER_PID)
    if not server_alive and not _typesense_health()["ok"]:
        print("Starting Typesense server...")
        result = subprocess.run(
            [_VENV_PY, _SERVER_PY],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
        if result.returncode != 0:
            print("ERROR: server failed to start")
            sys.exit(1)
    else:
        print("Server already running.")

    # Start watcher
    watcher_alive, _ = _pid_alive_win(_WATCHER_PID)
    if not watcher_alive:
        print("Starting file watcher...")
        p = subprocess.Popen(
            [_VENV_PY, _WATCHER_PY],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        open(_WATCHER_PID, "w").write(str(p.pid))
        print(f"  Watcher started (PID {p.pid})")
    else:
        print("Watcher already running.")

    # Start heartbeat
    hb_alive, _ = _pid_alive_win(_HEARTBEAT_PID)
    if not hb_alive:
        print("Starting heartbeat watchdog...")
        p = subprocess.Popen(
            [_VENV_PY, _HEARTBEAT_PY],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        open(_HEARTBEAT_PID, "w").write(str(p.pid))
        print(f"  Heartbeat started (PID {p.pid})")
    else:
        print("Heartbeat already running.")

    cmd_status(args)


def cmd_stop(args) -> None:
    print("Stopping services...")

    # Stop indexer first
    indexer_alive, indexer_pid = _pid_alive_win(_INDEXER_PID)
    if indexer_alive:
        subprocess.run(["taskkill", "/F", "/PID", indexer_pid], capture_output=True)
        print(f"  Stopped indexer (PID {indexer_pid})")
    if os.path.exists(_INDEXER_PID):
        os.remove(_INDEXER_PID)

    # Stop heartbeat
    _kill_win_pid(_HEARTBEAT_PID, "heartbeat")

    # Stop watcher
    _kill_win_pid(_WATCHER_PID, "watcher")

    # Stop server (via start_server.py --stop)
    print("  Stopping Typesense server...")
    subprocess.run([_VENV_PY, _SERVER_PY, "--stop"])
    if os.path.exists(_SERVER_PID):
        os.remove(_SERVER_PID)


def cmd_restart(args) -> None:
    cmd_stop(args)
    time.sleep(2)
    cmd_start(args)


def cmd_index(args) -> None:
    indexer_alive, indexer_pid = _pid_alive_win(_INDEXER_PID)
    if indexer_alive:
        print(f"Indexer already running (PID {indexer_pid}). Stop it first with: ts stop")
        sys.exit(1)

    if not _typesense_health()["ok"]:
        print("ERROR: Typesense server is not running. Start it first with: ts start")
        sys.exit(1)

    flags = ["--reset"] if args.reset else []
    print(f"Starting indexer {'(--reset) ' if args.reset else ''}in background...")
    print(f"  Log: {_INDEXER_LOG}")

    with open(_INDEXER_LOG, "w", encoding="utf-8") as log:
        p = subprocess.Popen(
            [_VENV_PY, _INDEXER_PY] + flags,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
    open(_INDEXER_PID, "w").write(str(p.pid))
    print(f"  Indexer running (PID {p.pid})")
    print(f"  Monitor with: ts status   or   ts log --indexer")


def cmd_log(args) -> None:
    if args.heartbeat:
        if not os.path.exists(_HEARTBEAT_LOG):
            print("No heartbeat log found. Run: ts heartbeat")
            return
        with open(_HEARTBEAT_LOG, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        n = args.lines or 40
        for line in lines[-n:]:
            print(line, end="")
    elif args.indexer:
        if not os.path.exists(_INDEXER_LOG):
            print("No indexer log found. Run: ts index")
            return
        # Tail last N lines
        with open(_INDEXER_LOG, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        n = args.lines or 40
        for line in lines[-n:]:
            print(line, end="")
    else:
        # Server log (WSL)
        n = args.lines or 40
        subprocess.run(["wsl", "bash", "-c", f"tail -{n} {_WSL_LOG}"])


def cmd_heartbeat(args) -> None:
    hb_alive, pid = _pid_alive_win(_HEARTBEAT_PID)
    if hb_alive:
        print(f"Heartbeat already running (PID {pid})")
        return
    if not _typesense_health()["ok"]:
        print("ERROR: Typesense server is not running. Start it first with: ts start")
        sys.exit(1)
    print("Starting heartbeat watchdog...")
    p = subprocess.Popen(
        [_VENV_PY, _HEARTBEAT_PY],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    open(_HEARTBEAT_PID, "w").write(str(p.pid))
    print(f"Heartbeat started (PID {p.pid})")
    print(f"  Log: {_HEARTBEAT_LOG}")


def cmd_watcher(args) -> None:
    watcher_alive, pid = _pid_alive_win(_WATCHER_PID)
    if watcher_alive:
        print(f"Watcher already running (PID {pid})")
        return
    if not _typesense_health()["ok"]:
        print("ERROR: Typesense server is not running. Start it first with: ts start")
        sys.exit(1)
    print("Starting file watcher...")
    p = subprocess.Popen(
        [_VENV_PY, _WATCHER_PY],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    open(_WATCHER_PID, "w").write(str(p.pid))
    print(f"Watcher started (PID {p.pid})")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="command", metavar="command")

    sub.add_parser("status",  help="Show service status")
    sub.add_parser("start",   help="Start server + watcher")
    sub.add_parser("stop",    help="Stop server + watcher + indexer")
    sub.add_parser("restart", help="Restart server + watcher")

    p_idx = sub.add_parser("index", help="Run indexer in background")
    p_idx.add_argument("--reset", action="store_true",
                       help="Drop and recreate the collection first")

    p_log = sub.add_parser("log", help="Show server, indexer, or heartbeat log")
    p_log.add_argument("--indexer",   action="store_true", help="Show indexer log instead of server log")
    p_log.add_argument("--heartbeat", action="store_true", help="Show heartbeat watchdog log")
    p_log.add_argument("--lines", "-n", type=int, default=40, help="Number of lines to show (default 40)")

    sub.add_parser("watcher",   help="Start the file watcher standalone")
    sub.add_parser("heartbeat", help="Start the heartbeat watchdog standalone")

    args = ap.parse_args()
    if not args.command:
        ap.print_help()
        sys.exit(0)

    dispatch = {
        "status":    cmd_status,
        "start":     cmd_start,
        "stop":      cmd_stop,
        "restart":   cmd_restart,
        "index":     cmd_index,
        "log":       cmd_log,
        "watcher":   cmd_watcher,
        "heartbeat": cmd_heartbeat,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
