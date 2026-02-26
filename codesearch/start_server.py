"""
Start the Typesense server via WSL.
Binary and data are stored in the WSL home dir (~/.local/typesense/)
to avoid /mnt/ cross-filesystem performance issues.

Usage:
    python start_server.py [--stop] [--log]
"""

import os
import sys
import time
import subprocess
import argparse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from codesearch.config import API_KEY, PORT, TYPESENSE_VERSION

TAR_URL = (
    f"https://dl.typesense.org/releases/{TYPESENSE_VERSION}/"
    f"typesense-server-{TYPESENSE_VERSION}-linux-amd64.tar.gz"
)

# Everything lives in WSL home - no /mnt/ cross-filesystem overhead
WSL_HOME_BIN   = "~/.local/typesense/typesense-server"
WSL_HOME_DATA  = "~/.local/typesense/data"
WSL_LOG        = "/tmp/typesense.log"

PID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "typesense.pid")


def wsl(cmd, **kwargs):
    return subprocess.run(["wsl", "bash", "-c", cmd], **kwargs)


def wsl_out(cmd) -> str:
    r = wsl(cmd, capture_output=True, text=True)
    return r.stdout.strip()


def is_running() -> bool:
    if not os.path.exists(PID_FILE):
        return False
    pid = open(PID_FILE).read().strip()
    alive = wsl_out(f"kill -0 {pid} 2>/dev/null && echo yes || echo no")
    return alive == "yes"


def wait_for_ready(timeout=40) -> bool:
    url = f"http://localhost:{PORT}/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        print(".", end="", flush=True)
        time.sleep(1)
    print()
    return False


def ensure_binary():
    exists = wsl_out(f"test -x {WSL_HOME_BIN} && echo ok || echo missing")
    if exists == "ok":
        return
    print(f"Downloading Typesense {TYPESENSE_VERSION} to WSL home (~/.local/typesense/)...")
    # Download directly to WSL home - no /mnt/ path involved
    wsl(f"mkdir -p ~/.local/typesense", check=True)
    result = wsl(
        f"curl -L --progress-bar '{TAR_URL}' | tar -xz -C ~/.local/typesense/",
        check=False
    )
    if result.returncode != 0:
        print("ERROR: Failed to download/extract Typesense binary")
        sys.exit(1)
    # Find wherever the binary landed and chmod it
    actual = wsl_out(
        "find ~/.local/typesense -name 'typesense-server' -type f 2>/dev/null | head -1"
    )
    if not actual:
        print("ERROR: typesense-server binary not found after extraction")
        sys.exit(1)
    # Move to canonical location only if it's in a subdir
    canonical = wsl_out("echo ~/.local/typesense/typesense-server")
    if actual != canonical:
        wsl(f"mv '{actual}' '{canonical}'", check=True)
    wsl(f"chmod +x '{canonical}'", check=True)
    print("Binary ready.")


def start():
    if is_running():
        print(f"Typesense is already running on port {PORT}.")
        return

    ensure_binary()
    # Resolve ~ to absolute path so it works in exec args
    wsl_home = wsl_out("echo $HOME")
    bin_abs  = f"{wsl_home}/.local/typesense/typesense-server"
    data_abs = f"{wsl_home}/.local/typesense/data"
    wsl(f"mkdir -p '{data_abs}'", check=True)

    launch = (
        f"setsid '{bin_abs}' "
        f"--data-dir='{data_abs}' "
        f"--api-key={API_KEY} "
        f"--port={PORT} "
        f"--enable-cors "
        f">{WSL_LOG} 2>&1 & sleep 0.5; pgrep -f 'typesense-server' | head -1"
    )
    pid = wsl_out(launch)
    if not pid.isdigit():
        print(f"ERROR: Could not get PID. Output: '{pid}'")
        print(f"Check log: wsl bash -c 'cat {WSL_LOG}'")
        sys.exit(1)

    open(PID_FILE, "w").write(pid)
    print(f"Typesense started (WSL pid={pid}). Waiting for health check", end="")

    if wait_for_ready():
        print(f"Ready at http://localhost:{PORT}")
    else:
        print(f"\nWARNING: did not respond in 40s. Check log with:")
        print(f"  wsl bash -c 'cat {WSL_LOG}'")


def stop():
    if not os.path.exists(PID_FILE):
        wsl("pkill -f typesense-server 2>/dev/null || true")
        print("Sent kill signal (no PID file found).")
        return
    pid = open(PID_FILE).read().strip()
    wsl(f"kill {pid} 2>/dev/null || true")
    os.remove(PID_FILE)
    print(f"Typesense (pid={pid}) stopped.")


def show_log():
    wsl(f"cat {WSL_LOG}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stop", action="store_true", help="Stop the server")
    ap.add_argument("--log",  action="store_true", help="Print the server log")
    args = ap.parse_args()
    if args.log:
        show_log()
    elif args.stop:
        stop()
    else:
        start()
