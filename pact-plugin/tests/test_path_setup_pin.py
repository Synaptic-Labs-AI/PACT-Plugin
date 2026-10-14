"""Structural pin: path setup is centralized — no per-file path inserts return.

The suite's path setup now lives in the two conftests (pact-plugin/conftest.py
and tests/conftest.py). This pin keeps it that way:

1. MODULE-LEVEL arm — no module-level path mutation outside MODULE_ALLOWLIST
   (the files whose inserts are load-bearing at import time: spawn-parent
   setup and the baseline loader).
2. ANY-FORM arm — no path mutation of ANY kind (module-level, function-level,
   or embedded in a codegen string) outside KEEP_SET. Spawn workers must
   rebuild sys.path in the child (it does not cross a spawn boundary) and
   subprocess harnesses generate their own path lines; both stay legitimate,
   but their file set is FIXED here so the set cannot grow silently.
3. COLLISION arm — no two sanctioned path roots expose the same module stem.
   Zero collisions today; a future skills/<new>/scripts/config.py shadowing
   pact-memory's config.py is the silent wrong-module failure this arm
   exists to catch.

Matched mutation forms: sys.path.insert/append/extend calls, slice-assignment
(sys.path[0:0] = [...]), and augmented assignment (+=) on sys.path — all but
insert were added after review findings that they dodged every arm while
being plausible honest-mistake forms, not adversarial constructions.
ACCEPTED UNDER-BLOCK (adversarial-only, documented boundary): aliasing sys
(`import sys as s`, `from sys import path`), wholesale rebinding
(`sys.path = [...]`), `getattr(sys.path, ...)` with a verb string, and
slice-assign/augmented forms with exotic spacing inside codegen strings —
this pin is an honest-mistake guard, not an adversary-proof one. OUT OF PARTITION (different failure mode): removal
mutations (remove/pop/clear/del) sabotage conftest setup rather than
re-introduce per-file path setup; zero occurrences at introduction, and the
failure would be loud (imports break), unlike a silent added root.

The matcher legs are pinned against silent rot by a committed negative
fixture (tests/fixtures/pin_negative_path_mutations.py) carrying one
violation per leg, plus test_negative_fixture_flags_every_matcher_leg
asserting the matcher flags each fixture line exactly. The fixture is
exempt from arms 1/2 via _NEGATIVE_FIXTURE — a single exact path, pinned
by the self-test so the exemption cannot widen silently.

Population: tests/**/*.py plus skills-adjacent test files
(skills/*/test_*.py). conftest.py files are the mechanism and are exempt.

Keep-set provenance (enumerated from the tree, not inherited from plan
text): 5 module-level carriers, 2 function-level (spawn-worker) carriers,
15 codegen-string carriers. Plan-level expectations counted pre-deletion
category memberships (16 spawn/subprocess + 8 top-level-scripts importers);
the 8 top-level-scripts importers retain no inserts at all once scripts/ is
conftest-owned, so they do not appear here.
"""

import ast
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# Built as concatenations so THIS file's own source never contains a needle
# as a contiguous literal — the any-form arm scans string constants and must
# not flag the pin itself. Call-form tokens plus augmented assignment in its
# two common spacings; slice-assignment inside a codegen string is accepted
# under-block (see module docstring).
_STR_TOKENS = tuple(
    "sys.path" + t for t in (".insert(", ".append(", ".extend(", " += ", "+=")
)
_STR_VERBS = (".insert", ".append", ".extend", "+=")

_MODULE_ALLOWLIST = {
    "tests/merge_guard_baseline_loader.py",
    "tests/test_session_journal.py",
    "tests/test_task_claim_gate.py",
    "tests/test_task_utils.py",
    "tests/test_working_memory_concurrency.py",
}

_KEEP_SET = _MODULE_ALLOWLIST | {
    # function-level (spawn workers re-derive path in the child process)
    "tests/test_session_registry_concurrency.py",
    "tests/test_working_memory_concurrency_comprehensive.py",
    # codegen strings (subprocess harnesses embed path lines in child code)
    "tests/test_agent_handoff_marker.py",
    "tests/test_containment_certification.py",
    "tests/test_embedding_status_contract.py",
    "tests/test_memory_cli.py",
    "tests/test_memory_init.py",
    "tests/test_memory_reachability.py",
    "tests/test_memory_store_isolation.py",
    "tests/test_merge_guard.py",
    "tests/test_pact_session_config_dir_parity.py",
    "tests/test_pin_growth.py",
    "tests/test_pin_growth_populations.py",
    "tests/test_pin_marker_writer_adversarial.py",
    "tests/test_pinned_section_parser_migration.py",
    "tests/test_project_dir_resolution.py",
    "tests/test_sync_result_contract.py",
}

# Sanctioned path roots, mirroring the two conftests: tests/conftest.py owns
# tests/, hooks/, skills/pact-memory/, skills/pact-memory/scripts/,
# skills/pact-coding-standards/scripts/, scripts/; the root conftest owns
# skills/*/scripts (glob, future skills included) and the plugin root.
# tests/fixtures/ is reachable via an allowlisted per-file insert.
def _sanctioned_roots():
    roots = [
        PLUGIN_ROOT / "tests",
        PLUGIN_ROOT / "tests" / "fixtures",
        PLUGIN_ROOT / "hooks",
        PLUGIN_ROOT / "skills" / "pact-memory",
        PLUGIN_ROOT / "scripts",
        PLUGIN_ROOT,  # telegram package family
    ]
    roots.extend(sorted(PLUGIN_ROOT.glob("skills/*/scripts")))
    # resolve() so equivalent spellings dedupe
    seen, out = set(), []
    for r in roots:
        key = str(r.resolve())
        if key not in seen and r.is_dir():
            seen.add(key)
            out.append(r.resolve())
    return out


def _population():
    files = sorted(PLUGIN_ROOT.glob("tests/**/*.py"))
    files += sorted(PLUGIN_ROOT.glob("skills/*/test_*.py"))
    return [f for f in files if f.name != "conftest.py"]


def _is_sys_path(value):
    return (
        isinstance(value, ast.Attribute)
        and value.attr == "path"
        and isinstance(value.value, ast.Name)
        and value.value.id == "sys"
    )


_MUTATION_VERBS = frozenset({"insert", "append", "extend"})


def _is_mutation_call(node):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _MUTATION_VERBS
        and _is_sys_path(node.func.value)
    )


def _is_slice_assign(node):
    """An Assign whose target subscripts sys.path (sys.path[0:0] = [...]).
    Insert-equivalent precedence, but not a Call, so the call predicate
    structurally cannot see it. Any subscript target matches (index
    replacement included) — all of them rewrite the search path."""
    return isinstance(node, ast.Assign) and any(
        isinstance(t, ast.Subscript) and _is_sys_path(t.value) for t in node.targets
    )


def _is_aug_assign(node):
    """An AugAssign whose target is the sys.path attribute (the += form).
    Extend-equivalent, and neither a Call nor an Assign, so both other
    predicates structurally miss it."""
    return isinstance(node, ast.AugAssign) and _is_sys_path(node.target)


def _is_path_mutation(node):
    return _is_mutation_call(node) or _is_slice_assign(node) or _is_aug_assign(node)


def _module_level_mutations(path):
    """Line numbers of path mutations in MODULE scope only (defs/classes
    excluded — a spawn worker's in-function rebuild is not module setup)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines = []

    def walk(node, in_def):
        for child in ast.iter_child_nodes(node):
            if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                walk(child, True)
                continue
            if _is_path_mutation(child) and not in_def:
                lines.append(child.lineno)
            walk(child, in_def)

    walk(tree, False)
    return lines


def _any_form_present(path):
    """True if the file contains a path mutation at any level OR embeds a
    mutation token in a string constant (codegen). Comments are
    AST-invisible and never match."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if _is_path_mutation(node):
            return True
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and any(tok in node.value for tok in _STR_TOKENS)
        ):
            return True
        if isinstance(node, ast.JoinedStr):
            text = "".join(
                c.value
                for c in node.values
                if isinstance(c, ast.Constant) and isinstance(c.value, str)
            )
            if "sys.path" in text and any(v in text for v in _STR_VERBS):
                return True
    return False


def _rel(path):
    return str(path.resolve().relative_to(PLUGIN_ROOT))


# The committed negative fixture is DELIBERATE violation evidence (pinned by
# test_negative_fixture_flags_every_matcher_leg), so the population arms skip
# exactly this one path. The self-test asserts this constant's value — an
# exemption widening requires editing this line and failing the self-test.
_NEGATIVE_FIXTURE = "tests/fixtures/pin_negative_path_mutations.py"


def test_no_module_level_insert_outside_allowlist():
    violations = []
    for f in _population():
        rel = _rel(f)
        if rel in _MODULE_ALLOWLIST or rel == _NEGATIVE_FIXTURE:
            continue
        for ln in _module_level_mutations(f):
            violations.append(f"{rel}:{ln}")
    assert not violations, (
        "module-level path mutation outside the allowlist (path setup is "
        "conftest-owned now): " + ", ".join(violations)
    )


def test_no_insert_of_any_form_outside_keep_set():
    violations = []
    for f in _population():
        rel = _rel(f)
        if rel in _KEEP_SET or rel == _NEGATIVE_FIXTURE:
            continue
        if _any_form_present(f):
            violations.append(rel)
    assert not violations, (
        "path mutation (real or codegen) outside the keep-set — spawn-worker "
        "and subprocess files are a FIXED set; adding one means updating "
        "this pin deliberately: " + ", ".join(violations)
    )


def test_negative_fixture_flags_every_matcher_leg():
    """Pins the pin: the matcher must flag each committed violation line.

    The fixture carries one module-level mutation per leg — insert, append,
    extend (call leg), slice-assign, augmented assign. Exact line numbers
    are the assertion: editing the fixture without updating this test fails
    here, so neither silent matcher rot nor silent fixture drift is
    possible. Also pins the exemption to exactly the fixture path."""
    fixture = PLUGIN_ROOT / _NEGATIVE_FIXTURE
    assert fixture.is_file(), f"negative fixture missing: {_NEGATIVE_FIXTURE}"
    assert _module_level_mutations(fixture) == [18, 19, 20, 21, 22]
    assert _any_form_present(fixture)
    assert _NEGATIVE_FIXTURE not in _MODULE_ALLOWLIST
    assert _NEGATIVE_FIXTURE not in _KEEP_SET


def test_no_basename_collision_across_sanctioned_roots():
    """No two sanctioned roots may expose the same module stem. A collision
    makes resolution order decide which module an import gets — the silent
    wrong-module failure the identity map can only see after the fact.
    Scope: .py stems (dunder-init excluded). Directory-name duality (the
    plugin-root scripts/ dir vs the pact-memory scripts package) is a
    designed state and out of scope for this arm."""
    stems = {}  # stem -> root that first exposed it
    collisions = []
    for root in _sanctioned_roots():
        for py in sorted(root.glob("*.py")):
            if py.name in ("__init__.py", "conftest.py"):
                continue
            stem = py.stem
            if stem in stems and stems[stem] != root:
                collisions.append(
                    f"{stem}.py exposed by both {stems[stem]} and {root}"
                )
            else:
                stems.setdefault(stem, root)
    assert not collisions, "module-stem collision(s): " + "; ".join(collisions)
