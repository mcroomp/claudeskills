"""
Basic smoke tests for the search.py and query.py skills.

Tests run against the live Typesense index and the real file system.
Fixtures are discovered dynamically from the index rather than hardcoded.

Usage (Windows):
    .venv\Scripts\python.exe codesearch\test_skills.py

Usage (WSL):
    ~/.local/mcp-venv/bin/python codesearch/test_skills.py
"""

from __future__ import annotations

import io
import os
import re
import sys
import traceback

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_UTIL_DIR  = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _UTIL_DIR)

from codesearch.config import IS_WSL, SRC_ROOT, to_native_path
from codesearch.search import search
from codesearch.query  import (
    process_file, files_from_search,
    q_field_type, q_param_type,
    _base_type_names, _parser,
)

# ── helpers ───────────────────────────────────────────────────────────────────

_PASS = 0
_FAIL = 0

def ok(name: str) -> None:
    global _PASS
    _PASS += 1
    print(f"  [PASS]  {name}")

def fail(name: str, detail: str) -> None:
    global _FAIL
    _FAIL += 1
    print(f"  [FAIL]  {name}")
    print(f"          {detail}")

def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        ok(name)
    else:
        fail(name, detail or "condition was False")

def section(title: str) -> None:
    print(f"\n{'-'*60}")
    print(f"  {title}")
    print(f"{'-'*60}")

def run(path, mode, mode_arg=None, context=0):
    buf = io.StringIO()
    old, sys.stdout = sys.stdout, buf
    try:
        n = process_file(path, mode, mode_arg, show_path=False, count_only=False, context=context)
    finally:
        sys.stdout = old
    return n, buf.getvalue()


# ── 1. Config / platform detection ───────────────────────────────────────────

section("1. Config: platform detection and path conversion")

check("IS_WSL is bool",        isinstance(IS_WSL, bool))
check("SRC_ROOT is non-empty", bool(SRC_ROOT))
if IS_WSL:
    check("SRC_ROOT starts with /mnt/",  SRC_ROOT.startswith("/mnt/"), SRC_ROOT)
    check("to_native_path converts drive letter",
          to_native_path("Q:/foo/bar.cs") == "/mnt/q/foo/bar.cs",
          to_native_path("Q:/foo/bar.cs"))
    check("to_native_path converts backslash",
          to_native_path(r"Q:\foo\bar.cs") == "/mnt/q/foo/bar.cs",
          to_native_path(r"Q:\foo\bar.cs"))
else:
    check("SRC_ROOT is non-empty", bool(SRC_ROOT), SRC_ROOT)
    check("to_native_path is identity",
          to_native_path("Q:/foo/bar.cs") == "Q:/foo/bar.cs",
          to_native_path("Q:/foo/bar.cs"))


# ── 2. AST: _base_type_names ──────────────────────────────────────────────────

section("2. AST: _base_type_names")

def _first_class(tree):
    """Return the first class_declaration node."""
    def find(node):
        if node.type == "class_declaration":
            return node
        for c in node.children:
            r = find(c)
            if r: return r
        return None
    return find(tree.root_node)

# Simple implements
_code = b"class Foo : IFooService { }"
_cls  = _first_class(_parser.parse(_code))
_bases = _base_type_names(_cls, _code)
check("simple implements: finds 1 base type",  len(_bases) == 1,  str(_bases))
check("simple implements: correct name",        _bases == ["IFooService"], str(_bases))

# Multiple implements
_code = b"private class X : IFooService, IBarService { }"
_cls  = _first_class(_parser.parse(_code))
_bases = _base_type_names(_cls, _code)
check("multiple implements: finds 2 base types", len(_bases) == 2,  str(_bases))
check("multiple implements: IFooService present", "IFooService" in _bases, str(_bases))
check("multiple implements: IBarService present", "IBarService" in _bases, str(_bases))

# Generic base type
_code = b"class Foo : IFoo<int> { }"
_cls  = _first_class(_parser.parse(_code))
_bases = _base_type_names(_cls, _code)
check("generic base type: finds name without <T>", _bases == ["IFoo"], str(_bases))

# No base types
_code = b"class Bare { }"
_cls  = _first_class(_parser.parse(_code))
_bases = _base_type_names(_cls, _code)
check("no base types: returns empty list", _bases == [], str(_bases))


# ── Discover test fixtures ────────────────────────────────────────────────────
# _FIXTURE is a minimal CS file bundled with the tests — always available.
# _test_cs_files are real indexed files found dynamically (need Typesense).

_FIXTURE = os.path.join(_THIS_DIR, "test_fixture.cs")

_test_cs_files: list[str] = []
try:
    _test_cs_files = files_from_search("class", ext="cs", limit=10)
except Exception:
    pass

_test_file = next((f for f in _test_cs_files if os.path.isfile(f)), None)


# ── 3. query.py: process_file on fixture file ─────────────────────────────────

section("3. query.py: process_file on fixture file")

check("fixture file exists", os.path.isfile(_FIXTURE), _FIXTURE)

if os.path.isfile(_FIXTURE):
    n, out = run(_FIXTURE, "classes")
    check("classes: finds Repository",       "Repository"       in out, repr(out[:300]))
    check("classes: finds CachedRepository", "CachedRepository" in out, repr(out[:300]))

    n, out = run(_FIXTURE, "methods")
    check("methods: finds GetById",  "GetById"  in out, repr(out[:300]))
    check("methods: finds Save",     "Save"     in out, repr(out[:300]))

    n, out = run(_FIXTURE, "fields")
    check("fields: finds _items",  "_items"  in out, repr(out[:300]))
    check("fields: finds _inner",  "_inner"  in out, repr(out[:300]))
    check("fields: finds _cache",  "_cache"  in out, repr(out[:300]))

    n, out = run(_FIXTURE, "usings")
    check("usings: finds System",             "System"             in out, repr(out[:300]))
    check("usings: finds Collections.Generic","Collections.Generic" in out, repr(out[:300]))

    n, out = run(_FIXTURE, "implements", "IRepository")
    check("implements IRepository: finds Repository",       "Repository"       in out, repr(out[:300]))
    check("implements IRepository: finds CachedRepository", "CachedRepository" in out, repr(out[:300]))

    n, out = run(_FIXTURE, "attrs")
    check("attrs: finds Serializable", "Serializable" in out, repr(out[:300]))


# ── 4. query.py: files_from_search ───────────────────────────────────────────

section("4. query.py: files_from_search (Typesense pre-filter)")

try:
    files = files_from_search("class", ext="cs", limit=10)
    check("files_from_search returns list",       isinstance(files, list))
    check("files_from_search: at least 1 result", len(files) > 0,  f"got {len(files)}")
    check("all returned paths exist on disk",
          all(os.path.isfile(f) for f in files),
          next((f for f in files if not os.path.isfile(f)), ""))
    if files:
        check("paths are platform-native (no drive letter in WSL)",
              not (IS_WSL and any(re.match(r"^[A-Za-z]:", f) for f in files)),
              str(files[0]))
except Exception as e:
    fail("files_from_search: exception", traceback.format_exc())


# ── 5. search.py: search() ───────────────────────────────────────────────────

section("5. search.py: search()")

try:
    result, qby = search("class", ext="cs", limit=5)
    hits = result.get("hits", [])
    check("search returns hits",       len(hits) > 0,  f"got {len(hits)}")
    check("each hit has relative_path",
          all("relative_path" in h["document"] for h in hits))

    # Symbols-only mode
    result2, qby2 = search("class", symbols_only=True, limit=5)
    check("symbols mode: query_by contains symbols",
          "symbols" in qby2, qby2)

    # Implements mode
    result3, qby3 = search("class", implements=True, limit=5)
    check("implements mode: query_by contains base_types",
          "base_types" in qby3, qby3)

    # Subsystem filter: pick the subsystem of the first hit and verify it filters correctly
    if hits:
        first_sub = hits[0]["document"].get("subsystem", "")
        if first_sub:
            result4, _ = search("class", sub=first_sub, limit=5)
            hits4 = result4.get("hits", [])
            if hits4:
                check(f"sub filter: all results in '{first_sub}'",
                      all(h["document"]["subsystem"] == first_sub for h in hits4),
                      str([h["document"]["subsystem"] for h in hits4]))

except SystemExit:
    fail("search(): Typesense not running", "Server unavailable — start with: ts start")
except Exception as e:
    fail("search(): exception", traceback.format_exc())


# ── 6. Combined: files_from_search + process_file ────────────────────────────

section("6. Combined: files_from_search + process_file")

try:
    files = files_from_search("class", ext="cs", limit=10)
    errors = 0
    parsed = 0
    for fpath in files:
        try:
            n, _ = run(fpath, "classes")
            parsed += 1
        except Exception:
            errors += 1
    check("combined: no parse errors",     errors == 0, f"{errors} errors in {len(files)} files")
    check("combined: at least one parsed", parsed > 0,  f"parsed={parsed}")
except Exception as e:
    fail("combined: exception", traceback.format_exc())


# ── 7. AST: q_field_type ──────────────────────────────────────────────────────

section("7. AST: q_field_type")

_FIELD_TYPE_CODE = b"""
class Outer {
    private MyStore _store;
    private IMyService _iface;
    public MyStore Store { get; set; }
    public IMyService IfaceStore { get; set; }
    private List<string> _names;
}
class Inner : ISomething {
    private MyStore _inner;
}
"""

_tree_ft = _parser.parse(_FIELD_TYPE_CODE)

_ft = q_field_type(_FIELD_TYPE_CODE, _tree_ft, [], "MyStore")
check("q_field_type MyStore: finds 2 fields + 1 prop",        len(_ft) == 3, str(_ft))
_ft_texts = [t for _, t in _ft]
check("q_field_type MyStore: _store present",                  any("_store" in t for t in _ft_texts), str(_ft_texts))
check("q_field_type MyStore: Store prop present",              any("Store"  in t for t in _ft_texts), str(_ft_texts))
check("q_field_type MyStore: does NOT return IMyService field",not any("_iface" in t for t in _ft_texts), str(_ft_texts))
check("q_field_type MyStore: enclosing class in output",       any("Outer" in t or "Inner" in t for t in _ft_texts), str(_ft_texts))

_ft2 = q_field_type(_FIELD_TYPE_CODE, _tree_ft, [], "IMyService")
check("q_field_type IMyService: finds 1 field + 1 prop",      len(_ft2) == 2, str(_ft2))

_GENERIC_CODE = b"class A { private List<string> _items; private IEnumerable<int> _seq; }"
_tree_g = _parser.parse(_GENERIC_CODE)
_ft_list = q_field_type(_GENERIC_CODE, _tree_g, [], "List")
check("q_field_type generic: finds List<string> field",        len(_ft_list) == 1 and "_items" in _ft_list[0][1], str(_ft_list))
check("q_field_type generic: does not return IEnumerable",     not any("_seq" in t for _, t in _ft_list), str(_ft_list))

_ft_none = q_field_type(_FIELD_TYPE_CODE, _tree_ft, [], "NonExistentType")
check("q_field_type: empty list for unknown type",             _ft_none == [], str(_ft_none))


# ── 8. AST: q_param_type ──────────────────────────────────────────────────────

section("8. AST: q_param_type")

_PARAM_CODE = b"""
class Processor {
    public Processor(MyStore store, IMyService iface) { }
    public void Process(MyStore myStore, string name) { }
    public void UseInterface(IMyService service) { }
    private void Internal(int count) { }
}
"""

_tree_pt = _parser.parse(_PARAM_CODE)

_pt = q_param_type(_PARAM_CODE, _tree_pt, [], "MyStore")
check("q_param_type MyStore: finds ctor + method param",       len(_pt) == 2, str(_pt))
_pt_texts = [t for _, t in _pt]
check("q_param_type MyStore: ctor match shown",                any("Processor"    in t for t in _pt_texts), str(_pt_texts))
check("q_param_type MyStore: Process match shown",             any("Process"      in t for t in _pt_texts), str(_pt_texts))
check("q_param_type MyStore: does NOT return IMyService",      not any("iface" in t or "UseInterface" in t for t in _pt_texts), str(_pt_texts))

_pt2 = q_param_type(_PARAM_CODE, _tree_pt, [], "IMyService")
check("q_param_type IMyService: finds ctor + UseInterface",    len(_pt2) == 2, str(_pt2))

_pt_none = q_param_type(_PARAM_CODE, _tree_pt, [], "UnknownType")
check("q_param_type: empty list for unknown type",             _pt_none == [], str(_pt_none))


# ── 9. process_file: --field-type and --param-type on fixture file ────────────

section("9. process_file: --field-type and --param-type on fixture file")

if os.path.isfile(_FIXTURE):
    n, out = run(_FIXTURE, "field_type", "Repository")
    check("field_type Repository: finds _inner",              "_inner"  in out, repr(out[:300]))
    check("field_type Repository: does not return _items",    "_items"  not in out, repr(out[:300]))

    n, out = run(_FIXTURE, "field_type", "List")
    check("field_type List: finds _items",                    "_items"  in out, repr(out[:300]))

    n, out = run(_FIXTURE, "param_type", "Repository")
    check("param_type Repository: finds CachedRepository ctor", "CachedRepository" in out, repr(out[:300]))

    n, _ = run(_FIXTURE, "field_type", "ZZZNonExistentType")
    check("field_type: returns 0 for unknown type",           n == 0, f"n={n}")

    n, _ = run(_FIXTURE, "param_type", "ZZZNonExistentType")
    check("param_type: returns 0 for unknown type",           n == 0, f"n={n}")


# ── 10. CLI: mode dispatch via process_file ───────────────────────────────────

section("10. CLI: mode dispatch via process_file")

if os.path.isfile(_FIXTURE):
    for _mode in ("classes", "methods", "fields", "usings", "attrs",
                  "field_type", "param_type"):
        try:
            _arg = "ZZZNonExistentType" if _mode in ("field_type", "param_type") else None
            n, _ = run(_FIXTURE, _mode, _arg)
            check(f"dispatch {_mode}: returns int", isinstance(n, int), f"n={n!r}")
        except Exception:
            fail(f"dispatch {_mode}: exception", traceback.format_exc())


# ── Summary ───────────────────────────────────────────────────────────────────

section(f"Results: {_PASS} passed, {_FAIL} failed")
sys.exit(0 if _FAIL == 0 else 1)
