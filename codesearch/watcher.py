"""
File watcher: monitors SRC_ROOT for source changes and updates Typesense index.

Usage:
    python watcher.py [--src /path/to/src]
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
import threading
import argparse

_util_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _util_dir)

import typesense
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from codesearch.config import (
    TYPESENSE_CLIENT_CONFIG, INCLUDE_EXTENSIONS,
    EXCLUDE_DIRS, MAX_FILE_BYTES, SRC_ROOT,
)
from codesearch.indexer import build_document, file_id, subsystem_from_path

DEBOUNCE_SECONDS = 2.0


class CsChangeHandler(FileSystemEventHandler):
    def __init__(self, client, src_root):
        super().__init__()
        self.client = client
        self.src_root = src_root
        self._pending = {}   # path -> ('upsert'|'delete')
        self._lock = threading.Lock()
        self._timer = None

    def _schedule_flush(self):
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(DEBOUNCE_SECONDS, self._flush)
        self._timer.daemon = True
        self._timer.start()

    def _is_cs(self, path):
        return os.path.splitext(path)[1].lower() in INCLUDE_EXTENSIONS

    def _is_excluded(self, path):
        parts = path.replace("\\", "/").split("/")
        return any(p in EXCLUDE_DIRS or p.startswith(".") for p in parts)

    def on_created(self, event):
        if not event.is_directory and self._is_cs(event.src_path):
            if not self._is_excluded(event.src_path):
                with self._lock:
                    self._pending[event.src_path] = "upsert"
                self._schedule_flush()

    def on_modified(self, event):
        if not event.is_directory and self._is_cs(event.src_path):
            if not self._is_excluded(event.src_path):
                with self._lock:
                    self._pending[event.src_path] = "upsert"
                self._schedule_flush()

    def on_deleted(self, event):
        if not event.is_directory and self._is_cs(event.src_path):
            with self._lock:
                self._pending[event.src_path] = "delete"
            self._schedule_flush()

    def on_moved(self, event):
        if not event.is_directory:
            if self._is_cs(event.src_path):
                with self._lock:
                    self._pending[event.src_path] = "delete"
            if self._is_cs(event.dest_path) and not self._is_excluded(event.dest_path):
                with self._lock:
                    self._pending[event.dest_path] = "upsert"
            self._schedule_flush()

    def _flush(self):
        with self._lock:
            pending = dict(self._pending)
            self._pending.clear()

        upserts = []
        deletes = []

        for path, action in pending.items():
            rel = os.path.relpath(path, self.src_root)
            if action == "upsert":
                if os.path.exists(path) and os.path.getsize(path) <= MAX_FILE_BYTES:
                    doc = build_document(path, rel)
                    if doc:
                        upserts.append(doc)
            elif action == "delete":
                deletes.append(file_id(rel))

        from codesearch.config import COLLECTION
        col = self.client.collections[COLLECTION]

        if upserts:
            try:
                col.documents.import_(upserts, {"action": "upsert"})
                print(f"[watcher] Indexed {len(upserts)} file(s)")
                for d in upserts:
                    print(f"          + {d['relative_path']}")
            except Exception as e:
                print(f"[watcher] ERROR upserting: {e}")

        if deletes:
            for doc_id in deletes:
                try:
                    col.documents[doc_id].delete()
                    print(f"[watcher] Removed {doc_id}")
                except Exception:
                    pass


def run_watcher(src_root=SRC_ROOT):
    client = typesense.Client(TYPESENSE_CLIENT_CONFIG)
    handler = CsChangeHandler(client, src_root)
    observer = Observer()
    observer.schedule(handler, src_root, recursive=True)
    observer.start()
    print(f"[watcher] Watching {src_root} for .cs changes... (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    print("[watcher] Stopped.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Watch for .cs changes and update Typesense")
    ap.add_argument("--src", default=SRC_ROOT)
    args = ap.parse_args()
    run_watcher(src_root=args.src)
