"""
Start the Typesense server via WSL.
Binary and data are stored in the WSL home dir (~/.local/typesense/)
to avoid /mnt/ cross-filesystem performance issues.

Usage:
    python start_server.py [--stop] [--log]
"""


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
import subprocess
import argparse
import urllib.request

import pwd as _pwd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from codesearch.config import API_KEY, PORT, TYPESENSE_VERSION

TAR_URL = (
    f"https://dl.typesense.org/releases/{TYPESENSE_VERSION}/"
    f"typesense-server-{TYPESENSE_VERSION}-linux-amd64.tar.gz"
)

# Everything lives in WSL home - no /mnt/ cross-filesystem overhead
_HOME          = _pwd.getpwuid(os.getuid()).pw_dir
_RUN_DIR       = os.path.join(_HOME, ".local", "typesense")
os.makedirs(_RUN_DIR, exist_ok=True)

WSL_HOME_BIN   = os.path.join(_RUN_DIR, "typesense-server")
WSL_HOME_DATA  = os.path.join(_RUN_DIR, "data")
WSL_LOG        = os.path.join(_RUN_DIR, "typesense.log")
PID_FILE       = os.path.join(_RUN_DIR, "typesense.pid")


def _sh(cmd, **kwargs):
    return subprocess.run(["bash", "-c", cmd], **kwargs)


def _sh_out(cmd) -> str:
    r = _sh(cmd, capture_output=True, text=True)
    return r.stdout.strip()


def is_running() -> bool:
    if not os.path.exists(PID_FILE):
        return False
    pid = open(PID_FILE).read().strip()
    alive = _sh_out(f"kill -0 {pid} 2>/dev/null && echo yes || echo no")
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
    exists = _sh_out(f"test -x {WSL_HOME_BIN} && echo ok || echo missing")
    if exists == "ok":
        return
    print(f"Downloading Typesense {TYPESENSE_VERSION} to WSL home (~/.local/typesense/)...")
    # Download directly to WSL home - no /mnt/ path involved
    _sh(f"mkdir -p ~/.local/typesense", check=True)
    result = _sh(
        f"curl -L --progress-bar '{TAR_URL}' | tar -xz -C ~/.local/typesense/",
        check=False
    )
    if result.returncode != 0:
        print("ERROR: Failed to download/extract Typesense binary")
        sys.exit(1)
    # Find wherever the binary landed and chmod it
    actual = _sh_out(
        "find ~/.local/typesense -name 'typesense-server' -type f 2>/dev/null | head -1"
    )
    if not actual:
        print("ERROR: typesense-server binary not found after extraction")
        sys.exit(1)
    # Move to canonical location only if it's in a subdir
    canonical = _sh_out("echo ~/.local/typesense/typesense-server")
    if actual != canonical:
        _sh(f"mv '{actual}' '{canonical}'", check=True)
    _sh(f"chmod +x '{canonical}'", check=True)
    print("Binary ready.")


def start():
    if is_running():
        print(f"Typesense is already running on port {PORT}.")
        return

    ensure_binary()
    os.makedirs(WSL_HOME_DATA, exist_ok=True)

    launch = (
        f"setsid '{WSL_HOME_BIN}' "
        f"--data-dir='{WSL_HOME_DATA}' "
        f"--api-key={API_KEY} "
        f"--port={PORT} "
        f"--enable-cors "
        f">{WSL_LOG} 2>&1 & sleep 0.5; pgrep -f 'typesense-server' | head -1"
    )
    pid = _sh_out(launch)
    if not pid.isdigit():
        print(f"ERROR: Could not get PID. Output: '{pid}'")
        print(f"Check log: cat {WSL_LOG}")
        sys.exit(1)

    open(PID_FILE, "w").write(pid)
    print(f"Typesense started (WSL pid={pid}). Waiting for health check", end="")

    if wait_for_ready():
        print(f"Ready at http://localhost:{PORT}")
    else:
        print(f"\nWARNING: did not respond in 40s. Check log with:")
        print(f"  cat {WSL_LOG}")


def stop():
    if not os.path.exists(PID_FILE):
        _sh("pkill -f typesense-server 2>/dev/null || true")
        print("Sent kill signal (no PID file found).")
        return
    pid = open(PID_FILE).read().strip()
    _sh(f"kill {pid} 2>/dev/null || true")
    os.remove(PID_FILE)
    print(f"Typesense (pid={pid}) stopped.")


def show_log():
    _sh(f"cat {WSL_LOG}")


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
