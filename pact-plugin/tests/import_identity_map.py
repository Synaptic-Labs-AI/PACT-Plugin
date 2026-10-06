"""Import-identity map capture — layer 4 of the test-import-restructure proof.

Per test module, record which FILE each imported plugin module resolves to
(importlib.util.find_spec origin), as diffable JSON. A green gate cannot see
a precedence flip (same module name resolving from a different directory
still imports); this map is the only proof layer that catches one.

Production registration: the plugin-root conftest re-exports the three hook
functions below (name-based hook discovery picks up imported callables), so
every run rooted at or below pact-plugin/ carries the capture. Emission is
gated on PACT_IDENTITY_MAP_OUT; normal gate runs write nothing.

Around a restructure, capture both halves into temporary paths, one before
the change and one after it, each from the full suite so every test file is
collected:

    cd pact-plugin
    PACT_IDENTITY_MAP_OUT=/tmp/identity-before.json \\
        python3 -m pytest -q        # full suite, no path argument

Compare with diff_maps(before["map"], after["map"], renames=<the change's
bare->package list>). An empty diff after rename normalization = no
precedence flip. Keep both maps out of the repository: a stored map goes
stale with every change to a test's imports.

Prototype-era fallback, if conftest registration is ever bypassed:

    PYTHONPATH=tests PACT_IDENTITY_MAP_OUT=/tmp/map.json \\
        python3 -m pytest -p import_identity_map <files> -q

Capture timing (measured on the 5-strata demo): pytest_sessionstart fires
BEFORE tests/conftest.py loads — conftest's own inserts appear in the
sessionstart->sessionfinish drift list alongside pytest prepend-mode basedir
inserts. So sessionstart is too early for the post-conftest state; the
capture point is pytest_collection_modifyitems, which fires after ALL
collection, when conftest, carrier inserts, and prepend-mode basedirs have
all landed — matching the runtime state function-level imports actually
resolve under (collection-phase masking is order-independent). The
sessionstart snapshot remains as the drift canary: an EXPECTED drift list
contains conftest's targets and test-file basedirs; anything else is the
signal that would force per-module collection-time capture instead.
"""

import ast
import importlib.util
import json
import os
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# Modules whose resolution the restructure contract cares about. Anything
# resolving outside the plugin tree (stdlib, site-packages) is noise for
# precedence purposes and is omitted from the map; resolution FAILURES are
# recorded as None so a silently-missing import stays visible.
def imported_names(test_file):
    """All absolute-import dotted names in a test file, module- AND
    function-level (ast.walk descends into function bodies). Full dotted
    path kept: `from scripts.working_memory import x` records
    'scripts.working_memory', so the bare->package codemod renames are
    visible as key changes rather than collapsing onto 'scripts'."""
    tree = ast.parse(Path(test_file).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            # `from scripts import working_memory` — the submodule rides the
            # package's name, so record the full dotted path too, or the
            # bare->package rename set can't match it. Non-module aliases
            # (functions/classes) capture as None on both sides: no diff.
            names.update(f"{node.module}.{a.name}" for a in node.names)
    return names


def resolve_origins(names):
    """find_spec each dotted name -> origin, repo-relative and
    resolve-normalized when under the plugin root (no 'tests/../' spellings),
    None when unresolvable. Externals omitted entirely.

    find_spec on a dotted name imports its parent packages — acceptable
    here (scripts/__init__.py is trivial); flagged so a future heavyweight
    __init__ doesn't surprise.
    """
    out = {}
    for name in sorted(names):
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError, AttributeError):
            out[name] = None
            continue
        origin = getattr(spec, "origin", None) if spec else None
        if not origin:
            out[name] = None
            continue
        # resolve() before relative_to: sys.path entries carrying '..' (e.g.
        # a tests/../skills insert) otherwise leak 'tests/../'-spelled origins
        # into the map — pure spelling noise that a baseline diff then has to
        # normalize away. PLUGIN_ROOT is resolved, so the pair stays consistent.
        # Non-path sentinel origins ('frozen', 'built-in') are relative, so the
        # is_absolute gate must precede resolve(): resolving first would anchor
        # them under cwd and leak them into the map as externals.
        p = Path(origin)
        if not p.is_absolute():
            continue  # sentinel origin or relative external — not our concern
        p = p.resolve()
        try:
            out[name] = str(p.relative_to(PLUGIN_ROOT))
        except ValueError:
            continue  # external — not the precedence contract's concern
    return out


def capture_map(test_paths):
    """{test_file_relpath: {module_name: origin_relpath|None}}."""
    result = {}
    for tp in test_paths:
        p = Path(tp)
        try:
            key = str(p.resolve().relative_to(PLUGIN_ROOT))
        except ValueError:
            key = str(p)
        result[key] = resolve_origins(imported_names(p))
    return result


def diff_maps(baseline, current, renames=None):
    """Compare origin FILES per (test_file, module). renames maps baseline
    keys to current keys (the codemod's bare->package conversions): a rename
    is expected and NOT a diff when the origin file is unchanged. Returns a
    list of human-readable differences; empty = no precedence flip."""
    renames = renames or {}
    diffs = []
    for tf in sorted(set(baseline) | set(current)):
        b, c = baseline.get(tf, {}), current.get(tf, {})
        if tf not in baseline:
            diffs.append(f"{tf}: NEW FILE (no baseline)")
            continue
        if tf not in current:
            diffs.append(f"{tf}: MISSING (collected pre-restructure only)")
            continue
        b_keys = {renames.get(k, k) for k in b}
        for k in sorted(set(b_keys) | set(c)):
            b_origin = b.get(k) if k in b else b.get(_unrename(renames, k))
            c_origin = c.get(k)
            if b_origin != c_origin:
                diffs.append(f"{tf}: {k} was {b_origin} now {c_origin}")
    return diffs


def _unrename(renames, key):
    for old, new in renames.items():
        if new == key:
            return old
    return key


# --- pytest hook integration (prototype: -p registration; production: conftest) ---

_state = {"sessionstart_path": None, "collected_files": set()}

# Nested-pytest guard: several suite tests spawn subprocess pytest runs, and
# env propagates to children — without this, a nested session inherits
# PACT_IDENTITY_MAP_OUT and overwrites the map at ITS sessionfinish while the
# outer gate is still running (measured: a nested write landed ~90s into an
# 18-minute gate). The outermost process marks the env at sessionstart;
# nested sessions see the mark and skip emission. Process-local: the shell's
# env is never mutated, so sequential runs are unaffected.
_GUARD_ENV = "PACT_IDENTITY_MAP_ACTIVE"


def pytest_sessionstart(session):
    # Fires BEFORE tests/conftest.py loads (measured) — this snapshot is the
    # pre-conftest baseline the drift canary compares against, nothing more.
    if os.environ.get(_GUARD_ENV):
        _state["nested"] = True
    else:
        os.environ[_GUARD_ENV] = "1"
    _state["sessionstart_path"] = list(sys.path)


def pytest_collection_modifyitems(session, config, items):
    for item in items:
        try:
            _state["collected_files"].add(str(item.path))
        except AttributeError:  # very old pytest: fspath
            _state["collected_files"].add(str(item.fspath))


def pytest_sessionfinish(session, exitstatus):
    out = os.environ.get("PACT_IDENTITY_MAP_OUT")
    if not out or _state.get("nested"):
        return
    drift = [p for p in sys.path if p not in (_state["sessionstart_path"] or [])]
    payload = {
        "map": capture_map(sorted(_state["collected_files"])),
        "path_drift_since_sessionstart": drift,
    }
    Path(out).write_text(json.dumps(payload, indent=2, sort_keys=True))
