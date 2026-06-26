"""
Location: pact-plugin/tests/test_hook_infra_classifier.py
Summary: CI meta-tests (hard teeth) for the hook-infra seam classifier SSOT
(shared/hook_infra_classifier.py) — the seam-dependent-hook enumeration + the
per-hook transitive helper closure that the non-mocked seam-test requirement
references. Three families:
  C6-A (LOAD-BEARING) — CLOSURE NON-VACUITY: re-derive the per-hook transitive
        helper-import closure from the LIVE hooks/ import graph (an independent
        AST walk) and assert the classifier's precomputed _SEAM_HOOK_HELPER_CLOSURE
        literal MATCHES it. This is NOT a restate-the-literal self-comparison: the
        oracle is computed from the real `import`/`from shared.X` edges, so a wrong
        or drifted literal recreates the #903 inert trap AT THE CLASSIFIER LAYER
        and this test catches it. Non-vacuity is PROVEN below by perturbing the
        live graph (drop a top-level edge AND a shared edge) and asserting the
        equality breaks.
  C6-B PRESENCE — every SEAM_DEPENDENT_HOOKS member is accounted for: it either
        has a non-mocked L2 integration test (COVERED) or is on a documented
        forward-only BACKLOG. A NEW seam hook with neither trips the partition.
  C6-B ANTI-MOCK — the seam-hook L2 integration files do NOT monkeypatch the
        resolver symbols (mocking the seam reproduces the inert trap) AND carry a
        revert-cardinality non-vacuity docstring.

Used by: the C4 GitHub Actions suite run (locus-a auto-enforcement). Plugin-
internal CI only — NOT a runtime hook, so no consumer pollution.

ORACLE SCOPE (must match the classifier literal's documented semantics): the
closure is TRANSITIVE over EVERY hooks/ module — both top-level helpers AND
hooks/shared/ helpers — and follows BOTH absolute (`from X import` /
`from shared.X import` / `import X`) AND relative (`from .X import`) edges. A
shared-only OR absolute-only OR direct-only derivation would FALSELY pass on a
sub-graph it never traverses (e.g. it would miss session_init->staleness->pin_caps
top-level, task_lifecycle_gate->teachback_schema->variety_scorer shared, or
the relative pact_context->.session_registry edge that makes a session_registry
edit implicate the L3 hooks). The two-hop edges are pinned BY NAME below, and a
top-level-edge perturbation is the explicit non-vacuity anchor.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

HOOKS = (Path(__file__).parent.parent / "hooks").resolve()
sys.path.insert(0, str(HOOKS))

from shared.hook_infra_classifier import (  # noqa: E402
    SEAM_DEPENDENT_HOOKS,
    SEAM_READING_HELPERS,
    L3_LIVE_PROBE_HOOKS,
    _SEAM_HOOK_HELPER_CLOSURE,
    classify_diff,
)

# ── Resolver symbols that an L2 seam integration test MUST NOT monkeypatch ──
# Mocking any of these reproduces the exact gap that shipped the missed-wake
# surfacer inert (the broken seam was the one every prior test stubbed).
RESOLVER_SYMBOLS = (
    "get_task_list",
    "iter_team_task_jsons",
    "find_stale_missed_wakes",
    "read_task_json",
    "get_team_name",
    "get_session_id",
)


# ── The independent oracle: re-derive the closure from the live import graph ──

def _module_index() -> dict[str, Path]:
    """stem -> path for every top-level hooks/*.py AND hooks/shared/*.py."""
    idx: dict[str, Path] = {}
    for p in sorted(HOOKS.glob("*.py")):
        idx.setdefault(p.stem, p)
    for p in sorted((HOOKS / "shared").glob("*.py")):
        idx.setdefault(p.stem, p)
    return idx


def _is_shared(stem: str, idx: dict[str, Path]) -> bool:
    """True iff the module resolves under hooks/shared/ (vs a top-level hooks/ helper)."""
    return "/shared/" in f"/{idx[stem].as_posix()}"


def _direct_hook_imports(
    path: Path, idx: dict[str, Path], shared_only: bool = False,
) -> set[str]:
    """hooks/ module stems imported DIRECTLY by `path` — resolves top-level
    (`import X` / `from X import`), shared (`from shared.X import` /
    `import shared.X`), AND relative (`from .X import`, level>0) edges, and
    descends into function/try-nested imports via ast.walk (e.g. session_init's
    function-level `from pin_staleness_gate import ...`).

    `shared_only` models the BUG the architect caught — a derivation that only
    follows hooks/shared/ edges and never traverses top-level helper modules."""
    out: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            cand = parts[1] if parts[0] == "shared" and len(parts) > 1 else parts[0]
            if cand in idx:
                out.add(cand)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                cand = parts[1] if parts[0] == "shared" and len(parts) > 1 else parts[0]
                if cand in idx:
                    out.add(cand)
    if shared_only:
        out = {m for m in out if _is_shared(m, idx)}
    return out


def derive_closure(
    hook: str,
    idx: dict[str, Path],
    drop_edges: frozenset[tuple[str, str]] = frozenset(),
    shared_only: bool = False,
) -> frozenset[str]:
    """BFS the live import graph from `hook`, collecting every reachable helper
    module (top-level OR shared), EXCLUDING the seam hooks themselves. `drop_edges`
    removes specific (module, imported) edges and `shared_only` restricts to
    shared edges — both used ONLY by the non-vacuity tests to prove the oracle
    responds to a real graph change / scope restriction."""
    seam = set(SEAM_DEPENDENT_HOOKS)
    seen: set[str] = set()
    stack = [hook]
    result: set[str] = set()
    while stack:
        mod = stack.pop()
        if mod not in idx:
            continue
        for dep in _direct_hook_imports(idx[mod], idx, shared_only=shared_only):
            if (mod, dep) in drop_edges or dep in seen:
                continue
            seen.add(dep)
            result.add(dep)
            stack.append(dep)
    return frozenset(result - seam)


def _derive_all(drop_edges: frozenset[tuple[str, str]] = frozenset()) -> dict[str, frozenset[str]]:
    idx = _module_index()
    return {h: derive_closure(h, idx, drop_edges) for h in SEAM_DEPENDENT_HOOKS}


# ═══════════════════════════════════════════════════════════════════════════
# C6-A — CLOSURE NON-VACUITY (the load-bearing meta-test)
# ═══════════════════════════════════════════════════════════════════════════

class TestClosureMatchesLiveImportGraph:
    """The committed _SEAM_HOOK_HELPER_CLOSURE literal must equal an INDEPENDENT
    transitive AST re-derivation from the live hooks/ import graph. Guards the
    classifier from a drifted/wrong closure (the classifier-layer inert trap)."""

    def test_each_hook_closure_matches_live_derivation(self):
        derived = _derive_all()
        for hook in sorted(SEAM_DEPENDENT_HOOKS):
            assert _SEAM_HOOK_HELPER_CLOSURE[hook] == derived[hook], (
                f"closure literal for {hook!r} has DRIFTED from the live import "
                f"graph.\n  literal-only: "
                f"{sorted(_SEAM_HOOK_HELPER_CLOSURE[hook] - derived[hook])}\n"
                f"  graph-only:   {sorted(derived[hook] - _SEAM_HOOK_HELPER_CLOSURE[hook])}"
            )

    def test_literal_covers_exactly_the_seam_hooks(self):
        assert set(_SEAM_HOOK_HELPER_CLOSURE) == set(SEAM_DEPENDENT_HOOKS)

    def test_seam_reading_helpers_is_the_union(self):
        union = frozenset().union(*_SEAM_HOOK_HELPER_CLOSURE.values())
        assert SEAM_READING_HELPERS == union


class TestTwoHopEdgesPinnedByName:
    """Pin BOTH transitive 2-hop classes by name so a future direct-only OR
    shared-only OR absolute-only regression is caught with a named failure, not
    a silent under-derivation."""

    def test_toplevel_helpers_present_and_are_top_level(self):
        # The 3 TOP-LEVEL helpers (hooks/pin_caps.py etc., NOT hooks/shared/)
        # reached from session_init must be in its closure — a shared-only
        # derivation (the architect-caught bug) would miss them entirely.
        idx = _module_index()
        for top in ("pin_caps", "staleness", "pin_staleness_gate"):
            assert top in _SEAM_HOOK_HELPER_CLOSURE["session_init"]
            assert not _is_shared(top, idx), f"{top} must be a top-level hooks/ module"

    def test_shared_2hop_task_lifecycle_teachback_variety(self):
        # task_lifecycle_gate -> teachback_schema -> variety_scorer (shared chain).
        tlg = _SEAM_HOOK_HELPER_CLOSURE["task_lifecycle_gate"]
        assert "teachback_schema" in tlg
        assert "variety_scorer" in tlg, (
            "variety_scorer is reachable ONLY 2-hop via teachback_schema; its "
            "absence means a direct-import-only derivation (the bug class)"
        )
        idx = _module_index()
        # Drop the 2-hop spine edge -> variety_scorer must disappear from the
        # derived closure (proves it's genuinely a transitive, not direct, dep).
        without = derive_closure(
            "task_lifecycle_gate", idx,
            drop_edges=frozenset({("teachback_schema", "variety_scorer")}),
        )
        assert "variety_scorer" not in without


class TestClosureOracleIsNonVacuous:
    """PROVE the equality assertion is non-vacuous: perturb the LIVE graph and
    show the derived closure changes, so a real edge drift WOULD break
    test_each_hook_closure_matches_live_derivation. A vacuous (literal==literal)
    oracle would NOT respond to these perturbations."""

    def test_shared_only_derivation_misses_toplevel_helpers(self):
        # The architect-caught bug: a shared-only walk never traverses the
        # top-level `from staleness import` / `from pin_caps import` edges, so
        # session_init's closure LOSES the 3 top-level helpers. Proves the
        # oracle's FULL hooks/ traversal (not shared-only) is load-bearing — a
        # shared-only oracle would FALSELY pass on this sub-graph.
        idx = _module_index()
        full = derive_closure("session_init", idx)
        shared_only = derive_closure("session_init", idx, shared_only=True)
        for top in ("pin_caps", "staleness", "pin_staleness_gate"):
            assert top in full
            assert top not in shared_only
        # And the equality test would FAIL if the oracle were shared-only:
        assert _SEAM_HOOK_HELPER_CLOSURE["session_init"] != shared_only

    def test_dropping_a_shared_edge_changes_derivation(self):
        idx = _module_index()
        full = derive_closure("task_lifecycle_gate", idx)
        perturbed = derive_closure(
            "task_lifecycle_gate", idx,
            drop_edges=frozenset({("teachback_schema", "variety_scorer")}),
        )
        assert "variety_scorer" in full and "variety_scorer" not in perturbed
        assert _SEAM_HOOK_HELPER_CLOSURE["task_lifecycle_gate"] != perturbed

    def test_dropping_the_relative_registry_edge_changes_derivation(self):
        # pact_context -> .session_registry is a RELATIVE edge; the full-transitive
        # oracle DOES follow it, so a relative-only-blind derivation differs.
        # This pins that relative edges are part of the canonical closure.
        idx = _module_index()
        full = derive_closure("missed_wake_scan", idx)
        perturbed = derive_closure(
            "missed_wake_scan", idx,
            drop_edges=frozenset({("pact_context", "session_registry")}),
        )
        assert "session_registry" in full, (
            "the canonical (full-transitive) closure follows the relative "
            "pact_context->.session_registry edge"
        )
        assert "session_registry" not in perturbed


# ═══════════════════════════════════════════════════════════════════════════
# C6-B — PRESENCE (every seam hook is COVERED by an L2 test or on the BACKLOG)
# ═══════════════════════════════════════════════════════════════════════════

# Seam hook -> the non-mocked L2 integration test file that exercises its real
# seam. teammate_idle's coverage is the IT-6 smoke inside the missed_wake file.
TESTS_DIR = Path(__file__).parent
COVERED_L2 = {
    "missed_wake_scan": "test_missed_wake_scan_integration.py",
    "teammate_idle": "test_missed_wake_scan_integration.py",
    "session_end": "test_session_end_integration.py",
    "agent_handoff_emitter": "test_agent_handoff_emitter_integration.py",
    "session_init": "test_session_init_integration.py",
    "dispatch_gate": "test_dispatch_gate_integration.py",
    # The on-disk authorization token is the post(mint)→pre(read) integration
    # seam for the merge guards; the non-mocked S1 seam test exercises it for
    # real (real mint → real read over a temp token_dir). Guards DENY via
    # exit(2) (fail-LOUD), so they are L2-only / never-L3 (no live-probe).
    "merge_guard_pre": "test_merge_guard_seam_integration.py",
    "merge_guard_post": "test_merge_guard_seam_integration.py",
}

# Documented forward-only BACKLOG: seam hooks whose non-mocked L2 test is a named
# fast-follow (not this cycle). The requirement BINDS new/modified seam hooks;
# this list is the auditable record of the known gaps (promote on touch).
#   - task_lifecycle_gate: heavy unit coverage; L3 real-session probe is the
#     documented follow-up. Its L2 seam test is fast-follow.
#   - bootstrap_gate / bootstrap_marker_writer: iter_team_task_jsons readers.
#   - file_tracker / peer_inject: L2-only (held), watch-list per the classifier.
#   - validate_handoff: stdin-only contract (no disk/task/journal seam).
BACKLOG_L2 = frozenset({
    "task_lifecycle_gate", "bootstrap_gate", "bootstrap_marker_writer",
    "file_tracker", "peer_inject", "validate_handoff",
})


class TestSeamHookL2Presence:
    def test_every_seam_hook_is_covered_or_backlogged(self):
        accounted = set(COVERED_L2) | set(BACKLOG_L2)
        unaccounted = set(SEAM_DEPENDENT_HOOKS) - accounted
        assert not unaccounted, (
            f"seam hook(s) with NEITHER an L2 test NOR a backlog entry: "
            f"{sorted(unaccounted)} — a new seam hook MUST ship an L2 "
            f"non-mocked integration test or be added to the documented backlog"
        )

    def test_covered_and_backlog_are_disjoint_and_exact(self):
        assert set(COVERED_L2).isdisjoint(BACKLOG_L2)
        assert set(COVERED_L2) | set(BACKLOG_L2) == set(SEAM_DEPENDENT_HOOKS)

    def test_covered_l2_test_files_exist(self):
        for hook, fname in sorted(COVERED_L2.items()):
            assert (TESTS_DIR / fname).exists(), (
                f"COVERED seam hook {hook!r} maps to missing L2 file {fname!r}"
            )

    def test_presence_fires_on_a_seam_hook_without_test(self):
        # NON-VACUITY (quiet side): a hypothetical new seam hook absent from both
        # COVERED and BACKLOG must be flagged as unaccounted.
        hypothetical = set(SEAM_DEPENDENT_HOOKS) | {"brand_new_seam_hook"}
        accounted = set(COVERED_L2) | set(BACKLOG_L2)
        assert (hypothetical - accounted) == {"brand_new_seam_hook"}


# ═══════════════════════════════════════════════════════════════════════════
# C6-B — ANTI-MOCK (seam L2 tests must exercise the REAL seam, not stub it)
# ═══════════════════════════════════════════════════════════════════════════

def _monkeypatches_resolver(text: str) -> list[str]:
    """Return resolver symbols this file monkeypatches/patches (the anti-pattern).
    Heuristic tripwire (backstopped by the revert-cardinality non-vacuity gate —
    a mocked-seam test, however it mocks, cannot pass a source-revert): flags any
    patch/setattr line that names a resolver symbol as the TARGET. Ignores plain
    imports/calls of the resolver (those are the REAL-seam exercise we want).

    FIX (review cycle 1, finding MINOR-2): the original only caught
    `monkeypatch.setattr` / `.patch(` / `@patch`. It was EVADABLE by
    `patch.object(...)`, a bare builtin `setattr(...)`, and an aliased
    `mp.setattr(...)`. Broadened below to catch all three; still a tripwire, not
    a proof (a string-built target or an exotic idiom can still slip — the
    revert-cardinality gate is the airtight layer)."""
    hits: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        is_patch = (
            "setattr(" in s            # monkeypatch.setattr / aliased mp.setattr / bare builtin setattr
            or ".patch(" in s          # mock.patch( / patch( context-manager
            or "patch.object(" in s    # mock.patch.object(...) — the prior evasion
            or s.startswith("patch(")  # bare patch(
            or s.startswith("@patch")  # @patch / @patch.object decorator
        )
        if not is_patch:
            continue
        for sym in RESOLVER_SYMBOLS:
            if sym in s:
                hits.append(sym)
    return hits


# The seam-hook L2 files that exist NOW and must obey the anti-mock invariant.
_EXISTING_L2_FILES = sorted({
    f for f in COVERED_L2.values() if (TESTS_DIR / f).exists()
})


class TestSeamL2FilesDoNotMockTheResolver:
    @pytest.mark.parametrize("fname", _EXISTING_L2_FILES)
    def test_no_resolver_monkeypatch(self, fname):
        text = (TESTS_DIR / fname).read_text(encoding="utf-8")
        hits = _monkeypatches_resolver(text)
        assert not hits, (
            f"{fname} monkeypatches resolver symbol(s) {sorted(set(hits))} — "
            f"mocking the seam reproduces the inert trap; exercise the REAL "
            f"resolver via a Path.home / tasks_base_dir redirect instead"
        )

    @pytest.mark.parametrize("fname", _EXISTING_L2_FILES)
    def test_carries_revert_cardinality_docstring(self, fname):
        text = (TESTS_DIR / fname).read_text(encoding="utf-8").lower()
        assert "revert" in text and ("cardinality" in text or "failed" in text), (
            f"{fname} must document a revert-cardinality non-vacuity gate "
            f"(source-revert the resolver fix -> documented {{N failed}} cardinality)"
        )

    def test_anti_mock_detector_is_non_vacuous(self):
        # Proves the detector FIRES on a real violation and is SILENT on a clean
        # snippet — else test_no_resolver_monkeypatch could pass vacuously.
        violating = 'monkeypatch.setattr(task_utils, "get_task_list", lambda: [])'
        clean = "tasks = task_utils.get_task_list(tasks_base_dir=str(root))"
        assert _monkeypatches_resolver(violating) == ["get_task_list"]
        assert _monkeypatches_resolver(clean) == []

    def test_anti_mock_detector_catches_evasion_idioms(self):
        # FIX (MINOR-2): the 3 idioms that EVADED the original detector are now
        # caught. A plain CALL of the resolver (not a patch target) stays clean.
        assert _monkeypatches_resolver(
            'patch.object(task_utils, "get_task_list")') == ["get_task_list"]
        assert _monkeypatches_resolver(
            'setattr(task_utils, "get_task_list", x)') == ["get_task_list"]
        assert _monkeypatches_resolver(
            'mp.setattr(task_utils, "get_task_list", x)') == ["get_task_list"]
        assert _monkeypatches_resolver(
            'with mock.patch.object(tu, "get_task_list"):') == ["get_task_list"]
        # a real-seam CALL (the pattern we WANT) must NOT be flagged:
        assert _monkeypatches_resolver(
            "members = dispatch_gate._team_member_names(team)") == []


# ═══════════════════════════════════════════════════════════════════════════
# C6-B — CLASSIFIER quiet/loud behavior (silent on non-hook, fires on seam)
# ═══════════════════════════════════════════════════════════════════════════

class TestClassifierQuietAndLoud:
    def test_silent_on_a_non_hook_pr(self):
        c = classify_diff(["README.md", "pact-plugin/agents/pact-test-engineer.md"])
        assert not c.primary and not c.secondary and not c.waiver_required

    def test_primary_only_change_requires_waiver(self):
        # A hooks/ file that is neither a seam hook nor a seam helper -> PRIMARY,
        # not SECONDARY -> the auditable waiver path (never a silent pass).
        c = classify_diff(["pact-plugin/hooks/shared/gh_helpers.py"])
        assert c.primary and not c.secondary and c.waiver_required

    def test_fires_secondary_on_a_seam_hook(self):
        c = classify_diff(["pact-plugin/hooks/missed_wake_scan.py"])
        assert c.primary and c.secondary
        assert "missed_wake_scan" in c.seam_hooks

    def test_l3_set_is_subset_of_seam_set(self):
        assert L3_LIVE_PROBE_HOOKS <= SEAM_DEPENDENT_HOOKS


# ═══════════════════════════════════════════════════════════════════════════
# C6-A BOUND BACKSTOP — enforce the oracle's STATIC-import / module-set bound
# (review cycle 1, finding MINOR-1)
# ═══════════════════════════════════════════════════════════════════════════
#
# The closure oracle (TestClosureMatchesLiveImportGraph) re-derives the closure
# from STATIC `import` / `from X import` AST nodes within the hooks/*.py +
# hooks/shared/*.py module set. Two edge classes would be invisible to BOTH the
# oracle AND the SSOT literal's authoring derivation (same algorithm) → they
# would AGREE VACUOUSLY (a false-pass) on a missed edge:
#   (1) a DYNAMIC import (importlib.import_module / __import__) of a hook/helper;
#   (2) an edge into the hooks/refresh/ subpackage (NOT in the oracle's idx glob).
# Both are ABSENT today (verified at authoring). This backstop fails LOUDLY if a
# future edit introduces either, so the oracle's bound is ENFORCED rather than
# silently exceeded. If a legitimate non-hook dynamic import is ever needed,
# allowlist that specific line explicitly (never blanket-disable the scan).

import re as _re  # noqa: E402


def _is_dynamic_import_line(line: str) -> bool:
    """True for a dynamic-import idiom the static AST oracle cannot resolve."""
    s = line.strip()
    if s.startswith("#"):
        return False
    return ("importlib" in s) or bool(_re.search(r"\b__import__\s*\(", s))


def _is_refresh_edge_line(line: str) -> bool:
    """True for an import edge into the hooks/refresh/ subpackage (outside the
    oracle's idx glob of hooks/*.py + hooks/shared/*.py)."""
    s = line.strip()
    if s.startswith("#"):
        return False
    return bool(_re.search(r"\b(?:from|import)\s+\.?refresh\b", s))


def _scan_hook_modules(predicate) -> list[tuple[str, int, str]]:
    """Run `predicate(line)` over every line of hooks/*.py + hooks/shared/*.py;
    return (filename, lineno, line) hits. Mirrors the oracle's module-set scope."""
    hits: list[tuple[str, int, str]] = []
    for d in (HOOKS, HOOKS / "shared"):
        for p in sorted(d.glob("*.py")):
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if predicate(line):
                    hits.append((p.name, i, line.strip()))
    return hits


class TestOracleStaticImportBoundBackstop:
    """Enforce the C6-A oracle's bound so a future dynamic/refresh edge fails
    HERE instead of slipping past the closure equality as a vacuous false-pass."""

    def test_no_dynamic_import_of_hook_modules(self):
        hits = _scan_hook_modules(_is_dynamic_import_line)
        assert not hits, (
            "a DYNAMIC import (importlib/__import__) appeared in the hooks tree — "
            "the C6-A static-AST oracle CANNOT see it, so the closure literal could "
            "silently false-pass on this edge. Either use a static import, or (if a "
            "legit non-hook dynamic import) allowlist this exact line + extend the "
            f"oracle. Offending: {hits}"
        )

    def test_no_refresh_subpackage_edge_in_hooks(self):
        hits = _scan_hook_modules(_is_refresh_edge_line)
        assert not hits, (
            "an import edge into hooks/refresh/ appeared — it is OUTSIDE the C6-A "
            "oracle's idx glob (hooks/*.py + hooks/shared/*.py), so both the oracle "
            "and the literal would miss it (vacuous false-pass). Extend the oracle's "
            f"idx to include hooks/refresh/ before adding such an edge. Offending: {hits}"
        )

    def test_backstop_detectors_are_non_vacuous(self):
        # PROVE the backstop would FIRE on an injected edge (else it is vacuous).
        assert _is_dynamic_import_line('mod = importlib.import_module("staleness")')
        assert _is_dynamic_import_line('__import__("task_utils")')
        assert not _is_dynamic_import_line("from shared.task_utils import get_task_list")
        assert not _is_dynamic_import_line("# importlib note in a comment")
        assert _is_refresh_edge_line("from refresh.checkpoint_builder import build")
        assert _is_refresh_edge_line("import refresh.patterns")
        assert _is_refresh_edge_line("from .refresh import x")
        assert not _is_refresh_edge_line("refresh_token = compute()")  # word-boundary
        assert not _is_refresh_edge_line("# refresh the cache")
