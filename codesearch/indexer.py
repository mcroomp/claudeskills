"""
Index C# source files into Typesense.
Uses tree-sitter to extract class/interface/method/property symbols.

Usage:
    python indexer.py [--reset]   # --reset drops and recreates the collection
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
import hashlib
import argparse

# Ensure the util venv tree-sitter is importable and config is on path
_util_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _util_dir)

import typesense
import tree_sitter_c_sharp as tscsharp
from tree_sitter import Language, Parser

from codesearch.config import (
    TYPESENSE_CLIENT_CONFIG, COLLECTION, SRC_ROOT,
    INCLUDE_EXTENSIONS, EXCLUDE_DIRS, MAX_FILE_BYTES, MAX_CONTENT_CHARS,
)

CS = Language(tscsharp.language())
_parser = Parser(CS)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = {
    "name": COLLECTION,
    "fields": [
        {"name": "id",            "type": "string"},
        {"name": "path",          "type": "string", "optional": True, "index": False},
        {"name": "relative_path", "type": "string"},
        {"name": "filename",      "type": "string"},
        {"name": "extension",     "type": "string", "facet": True},
        {"name": "subsystem",     "type": "string", "facet": True},
        {"name": "namespace",     "type": "string", "optional": True},
        {"name": "class_names",   "type": "string[]", "optional": True},
        {"name": "method_names",  "type": "string[]", "optional": True},
        {"name": "symbols",       "type": "string[]"},
        {"name": "content",       "type": "string"},
        {"name": "mtime",         "type": "int64"},
        # Tier 1 semantic fields
        {"name": "base_types",    "type": "string[]", "optional": True},
        {"name": "call_sites",    "type": "string[]", "optional": True},
        {"name": "method_sigs",   "type": "string[]", "optional": True},
        # Tier 2 semantic fields
        {"name": "type_refs",     "type": "string[]", "optional": True},
        {"name": "attributes",    "type": "string[]", "optional": True, "facet": True},
        {"name": "usings",        "type": "string[]", "optional": True},
        # Search ranking boost: .cs=3, native(.h/.cpp)=2, scripts/web=1, config/docs=0
        {"name": "priority",      "type": "int32"},
    ],
}


# ---------------------------------------------------------------------------
# Tree-sitter symbol extraction
# ---------------------------------------------------------------------------

def _find_all(node, predicate, results=None):
    if results is None:
        results = []
    if predicate(node):
        results.append(node)
    for child in node.children:
        _find_all(child, predicate, results)
    return results


def _node_text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


_TYPE_DECL_NODES = {
    "class_declaration",
    "interface_declaration",
    "struct_declaration",
    "enum_declaration",
    "record_declaration",
    "delegate_declaration",
}

_MEMBER_DECL_NODES = {
    "method_declaration",
    "constructor_declaration",
    "property_declaration",
    "field_declaration",
    "event_declaration",
    "local_function_statement",
}


def _dedupe(seq):
    seen = set()
    out = []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def extract_cs_metadata(src_bytes: bytes):
    """Extract rich C# metadata for tier 1+2 semantic indexing.

    Returns dict with keys:
        namespace   : str
        class_names : list[str]   — type declaration names
        method_names: list[str]   — member declaration names
        base_types  : list[str]   — implemented interfaces + base classes  [T1]
        call_sites  : list[str]   — invoked method names                   [T1]
        method_sigs : list[str]   — "ReturnType MethodName(ParamTypes)"    [T1]
        type_refs   : list[str]   — type names used in declarations        [T2]
        attributes  : list[str]   — [Attribute] names (faceted)            [T2]
        usings      : list[str]   — using-imported namespaces              [T2]
    """
    try:
        tree = _parser.parse(src_bytes)
    except Exception:
        return {
            "namespace": "", "class_names": [], "method_names": [],
            "base_types": [], "call_sites": [], "method_sigs": [],
            "type_refs": [], "attributes": [], "usings": [],
        }

    root = tree.root_node

    namespace = ""
    class_names = []
    method_names = []
    base_types = []
    call_sites = []
    method_sigs = []
    type_refs = []
    attributes = []
    usings = []

    # ── Namespace ────────────────────────────────────────────────────────────
    ns_nodes = _find_all(root, lambda n: n.type in (
        "namespace_declaration", "file_scoped_namespace_declaration"
    ))
    if ns_nodes:
        name_node = ns_nodes[0].child_by_field_name("name")
        if name_node:
            namespace = _node_text(name_node, src_bytes)

    # ── T2: using imports ────────────────────────────────────────────────────
    for node in _find_all(root, lambda n: n.type == "using_directive"):
        name_node = node.child_by_field_name("name")
        if name_node:
            usings.append(_node_text(name_node, src_bytes))

    # ── T2: attributes ───────────────────────────────────────────────────────
    for node in _find_all(root, lambda n: n.type == "attribute"):
        name_node = node.child_by_field_name("name")
        if name_node:
            attr_name = _node_text(name_node, src_bytes)
            # Normalise: strip trailing "Attribute" suffix for cleaner search
            if attr_name.endswith("Attribute"):
                attr_name = attr_name[:-len("Attribute")]
            attributes.append(attr_name)

    # ── Type declarations ─────────────────────────────────────────────────────
    for node in _find_all(root, lambda n: n.type in _TYPE_DECL_NODES):
        name_node = node.child_by_field_name("name")
        if name_node:
            class_names.append(_node_text(name_node, src_bytes))

        # T1: base_types — base_list contains the parent class / interfaces
        base_list = node.child_by_field_name("bases")
        if base_list:
            for child in base_list.children:
                if child.type in ("simple_base_type", "primary_constructor_base_type"):
                    type_node = child.child_by_field_name("type")
                    if type_node:
                        base_types.append(_node_text(type_node, src_bytes))
                    elif child.named_child_count > 0:
                        base_types.append(_node_text(child.named_children[0], src_bytes))

    # ── Member declarations ───────────────────────────────────────────────────
    for node in _find_all(root, lambda n: n.type in _MEMBER_DECL_NODES):
        name_node = node.child_by_field_name("name")
        if name_node:
            method_names.append(_node_text(name_node, src_bytes))
        elif node.type == "field_declaration":
            for var in _find_all(node, lambda n: n.type == "variable_declarator"):
                vname = var.child_by_field_name("name")
                if vname:
                    method_names.append(_node_text(vname, src_bytes))

        # T1: method signatures — "ReturnType Name(P1Type, P2Type, ...)"
        if node.type in ("method_declaration", "local_function_statement"):
            ret_node = node.child_by_field_name("type")
            name_node2 = node.child_by_field_name("name")
            params_node = node.child_by_field_name("parameters")
            if name_node2 and params_node:
                ret_txt = _node_text(ret_node, src_bytes).strip() if ret_node else ""
                mname = _node_text(name_node2, src_bytes)
                # Collect parameter types (not names)
                param_types = []
                for param in _find_all(params_node, lambda n: n.type == "parameter"):
                    ptype = param.child_by_field_name("type")
                    if ptype:
                        param_types.append(_node_text(ptype, src_bytes).strip())
                sig = f"{ret_txt} {mname}({', '.join(param_types)})".strip()
                method_sigs.append(sig)

        # T2: type_refs — collect type names appearing in member declarations
        if node.type in ("field_declaration", "property_declaration", "event_declaration"):
            type_node = node.child_by_field_name("type")
            if type_node:
                type_refs.append(_node_text(type_node, src_bytes).strip())
        if node.type == "method_declaration":
            ret_node = node.child_by_field_name("type")
            if ret_node:
                type_refs.append(_node_text(ret_node, src_bytes).strip())
            params_node = node.child_by_field_name("parameters")
            if params_node:
                for param in _find_all(params_node, lambda n: n.type == "parameter"):
                    ptype = param.child_by_field_name("type")
                    if ptype:
                        type_refs.append(_node_text(ptype, src_bytes).strip())

    # ── T1: call sites ────────────────────────────────────────────────────────
    for node in _find_all(root, lambda n: n.type == "invocation_expression"):
        fn_node = node.child_by_field_name("function")
        if fn_node:
            if fn_node.type == "member_access_expression":
                name_node = fn_node.child_by_field_name("name")
                if name_node:
                    call_sites.append(_node_text(name_node, src_bytes))
            elif fn_node.type == "identifier":
                call_sites.append(_node_text(fn_node, src_bytes))

    return {
        "namespace":    namespace,
        "class_names":  _dedupe(class_names),
        "method_names": _dedupe(method_names),
        "base_types":   _dedupe(base_types),
        "call_sites":   _dedupe(call_sites),
        "method_sigs":  _dedupe(method_sigs),
        "type_refs":    _dedupe(type_refs),
        "attributes":   _dedupe(attributes),
        "usings":       _dedupe(usings),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def file_id(relative_path: str) -> str:
    return hashlib.md5(relative_path.encode()).hexdigest()


def subsystem_from_path(relative_path: str) -> str:
    parts = relative_path.replace("\\", "/").split("/")
    return parts[0] if parts else ""


def should_skip_dir(dirname: str) -> bool:
    return dirname in EXCLUDE_DIRS or dirname.startswith(".")


_PRIORITY = {
    ".cs":   3,   # C# source — highest priority
    ".h": 2, ".hpp": 2, ".cpp": 2, ".c": 2, ".idl": 2,  # native source
    ".py": 1, ".ts": 1, ".js": 1, ".ps1": 1, ".sh": 1, ".cmd": 1, ".bat": 1,  # scripts
}


def _file_priority(ext: str) -> int:
    return _PRIORITY.get(ext, 0)


def build_document(full_path: str, relative_path: str) -> dict:
    stat = os.stat(full_path)
    try:
        src_bytes = open(full_path, "rb").read()
    except OSError:
        return None

    ext = os.path.splitext(full_path)[1].lower()
    if ext == ".cs":
        meta = extract_cs_metadata(src_bytes)
    else:
        meta = {
            "namespace": "", "class_names": [], "method_names": [],
            "base_types": [], "call_sites": [], "method_sigs": [],
            "type_refs": [], "attributes": [], "usings": [],
        }

    symbols = list(dict.fromkeys(meta["class_names"] + meta["method_names"]))

    content = src_bytes.decode("utf-8", errors="replace")[:MAX_CONTENT_CHARS]

    return {
        "id":            file_id(relative_path),
        "path":          full_path,
        "relative_path": relative_path.replace("\\", "/"),
        "filename":      os.path.basename(full_path),
        "extension":     ext.lstrip("."),
        "subsystem":     subsystem_from_path(relative_path),
        "namespace":     meta["namespace"],
        "class_names":   meta["class_names"],
        "method_names":  meta["method_names"],
        "symbols":       symbols if symbols else [""],
        "content":       content,
        "mtime":         int(stat.st_mtime),
        "priority":      _file_priority(ext),
        # Tier 1
        "base_types":    meta["base_types"],
        "call_sites":    meta["call_sites"],
        "method_sigs":   meta["method_sigs"],
        # Tier 2
        "type_refs":     meta["type_refs"],
        "attributes":    meta["attributes"],
        "usings":        meta["usings"],
    }


# ---------------------------------------------------------------------------
# Collection management
# ---------------------------------------------------------------------------

def get_client():
    return typesense.Client(TYPESENSE_CLIENT_CONFIG)


def ensure_collection(client, reset=False):
    try:
        client.collections[COLLECTION].retrieve()
        if reset:
            print(f"Dropping existing collection '{COLLECTION}'...")
            client.collections[COLLECTION].delete()
            raise Exception("deleted")
        print(f"Collection '{COLLECTION}' already exists.")
    except Exception:
        print(f"Creating collection '{COLLECTION}'...")
        client.collections.create(SCHEMA)
        print("Collection created.")


# ---------------------------------------------------------------------------
# Full index walk
# ---------------------------------------------------------------------------

def walk_source_files(src_root: str):
    """Yield (full_path, relative_path) for all source files visible to git.
    Uses 'git ls-files -co --exclude-standard' to include:
      - tracked (staged/committed) files
      - untracked files that are NOT gitignored (new files not yet staged)
    Streams line-by-line - no full list loaded into memory.
    """
    import subprocess as _sp
    proc = _sp.Popen(
        ["git", "-C", src_root, "ls-files",
         "--cached", "--others", "--exclude-standard"],
        stdout=_sp.PIPE, stderr=_sp.DEVNULL,
    )
    try:
        for raw in proc.stdout:
            rel = raw.rstrip(b"\n").decode("utf-8", errors="replace")
            if not rel:
                continue
            ext = os.path.splitext(rel)[1].lower()
            if ext not in INCLUDE_EXTENSIONS:
                continue
            full_path = os.path.join(src_root, rel.replace("/", os.sep))
            try:
                if os.path.getsize(full_path) > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield full_path, rel
    finally:
        proc.stdout.close()
        proc.wait()


def _fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    return f"{m}m{s:02d}s"


def run_index(src_root=SRC_ROOT, reset=False, batch_size=50, verbose=False):
    client = get_client()
    ensure_collection(client, reset=reset)

    docs_batch = []
    total = 0
    errors = 0
    t0 = time.time()
    last_report_t = t0
    last_report_n = 0
    current_sub = ""

    print(f"Indexing source files under: {src_root}")
    print(f"Extensions: {', '.join(sorted(INCLUDE_EXTENSIONS))}")
    print()

    for full_path, rel in walk_source_files(src_root):
        sub = subsystem_from_path(rel)
        if sub != current_sub:
            current_sub = sub
            elapsed = time.time() - t0
            print(f"  [{_fmt_time(elapsed)}] subsystem: {sub}  (total so far: {total})")

        doc = build_document(full_path, rel)
        if doc is None:
            errors += 1
            continue

        docs_batch.append(doc)

        if len(docs_batch) >= batch_size:
            _flush(client, docs_batch, verbose)
            total += len(docs_batch)
            docs_batch = []

            now = time.time()
            if now - last_report_t >= 30:
                elapsed = now - t0
                delta_n = total - last_report_n
                delta_t = now - last_report_t
                rate = delta_n / delta_t if delta_t > 0 else 0
                print(f"  [{_fmt_time(elapsed)}] {total:,} files indexed  "
                      f"({rate:.0f} files/s)  errors={errors}")
                last_report_t = now
                last_report_n = total

    if docs_batch:
        _flush(client, docs_batch, verbose)
        total += len(docs_batch)

    elapsed = time.time() - t0
    rate = total / elapsed if elapsed > 0 else 0
    print()
    print(f"Done in {_fmt_time(elapsed)}. "
          f"Indexed {total:,} files  ({rate:.0f} files/s)  errors={errors}")


def _flush(client, docs, verbose):
    try:
        results = client.collections[COLLECTION].documents.import_(
            docs, {"action": "upsert"}
        )
        if verbose:
            failed = [r for r in results if not r.get("success")]
            for f in failed:
                print(f"  WARN: {f}")
    except Exception as e:
        print(f"  ERROR during batch import: {e}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Index source files into Typesense")
    ap.add_argument("--reset", action="store_true",
                    help="Drop and recreate the collection first")
    ap.add_argument("--src", default=SRC_ROOT,
                    help=f"Root directory to index (default: {SRC_ROOT})")
    ap.add_argument("--status", action="store_true",
                    help="Show index stats and exit")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.status:
        client = get_client()
        try:
            info = client.collections[COLLECTION].retrieve()
            n = info.get("num_documents", "?")
            print(f"Collection '{COLLECTION}': {n:,} documents indexed")
        except Exception as e:
            print(f"Cannot retrieve index stats: {e}")
            print("Is the server running? Try: python start_server.py")
    else:
        run_index(src_root=args.src, reset=args.reset, verbose=args.verbose)
