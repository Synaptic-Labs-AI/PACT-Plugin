"""Structural guard: the two finders in missed_wake_scan.py stay uncoupled.

Location: pact-plugin/tests/test_missed_wake_scan_d5_structure.py
Summary: bars two specific future edits to hooks/missed_wake_scan.py — hoisting
         the reason filter to module scope, and routing both finders through one
         selector. Asserts SHAPE, not behaviour; every behavioural arm for these
         functions lives in test_missed_wake_scan.py.
Used by: the suite. No module-level sys.path.insert — path setup is
         conftest-owned; see tests/test_path_setup_pin.py.

WHY A SHAPE GUARD EXISTS HERE AT ALL, AND WHY IT IS NOT PARANOIA. The two
finders used to live in SEPARATE HOOKS, which made a scope disagreement between
them structurally impossible to trigger. They now live in ONE FILE as separate
functions. The all-clear was re-derived and still holds — but its guarantor
moved from architecture to CODE LAYOUT. An invariant that depends on two
functions not being refactored together dies to a tidy-up commit, the tidy-up
looks like an improvement, and nothing reddens: both finders are individually
correct today and every behavioural test would still pass.

WHAT IT ASSERTS, deliberately narrow — the two edits that were NAMED, nothing
broader. A descriptive framing like "the all-clear is layout-dependent" cannot
be tested; these two can:

  GUARD A — the missed-wake reason (`_MISSED_WAKE_REASON` and the literal it
  holds) is READ only from inside the MISSED-WAKE LANE. Its module-level
  definition is fine; a module-level USE is not, and a read from the other
  alarm's functions never is.

  GUARD B — the two finders share no callee defined in this module, and neither
  calls the other.

Neither guard subsumes the other. Hoisting the filter without routing both
finders through it trips A only; routing both through a selector that does not
touch the reason trips B only.

A CORRECTION WORTH KEEPING, because it is the guard catching its own author.
Guard A first read "inside `find_stale_missed_wakes`", and it FIRED ON THE
UNMUTATED TREE. The premise was wrong, not the guard: there are TWO legitimate
reads — the finder filters on the reason and `emit_forensic` writes it into the
`missed_wake` journal event. The assertion is a LANE, not a single function, and
the lane is named explicitly below so that widening it is a decision rather than
a side effect.

THE FALSE-POSITIVE BUDGET IS DELIBERATE AND IS THE NARROW ONE. This will NOT
catch every conceivable coupling — a third route nobody has named will pass it.
That is the accepted trade: a shape guard that reddens on an innocent rename is
one people learn to wave through, and then it protects nothing. Callees are
restricted to functions DEFINED IN THIS MODULE, so shared builtins and shared
imports do not trip it.

HOW THIS GUARD WAS VERIFIED, because a guard that cannot fire is
indistinguishable from one that passes correctly. Four couplings were
CONSTRUCTED as real edits to real source in a detached worktree and each was
required to kill it — two idioms per barred edit, the second idiom written
specifically because the first was the one the author had in mind:

  1a  reason filter hoisted into a module-level helper function
  1b  reason filter hoisted into a module-level lambda
  2a  both finders routed through a shared module-level selector
  2b  the unflagged finder calling the missed-wake finder directly

plus an unmutated control that had to pass. Re-run:
`python3 <scratchpad>/d5_mutants.py` against a detached worktree.

WHEN TO DELETE THIS FILE RATHER THAN MAINTAIN IT. The guard protects a PARKED
scope disagreement. If that disagreement is ever resolved — the two finders are
deliberately unified, or the reason vocabulary is merged on purpose — this guard
is protecting nothing and should be DELETED, not amended. A structural guard
kept past its premise becomes a monument that blocks the very change it was
written to make safe to reason about.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "hooks" / "missed_wake_scan.py"

MISSED_WAKE_FINDER = "find_stale_missed_wakes"
UNFLAGGED_FINDER = "find_stale_unflagged_background"
REASON_CONST = "_MISSED_WAKE_REASON"

# The missed-wake LANE — the functions permitted to read the reason.
#
# MEASURED, and the first draft of this guard got it wrong: there are TWO
# legitimate reads, not one. `find_stale_missed_wakes` filters on the reason and
# `emit_forensic` writes it into the `missed_wake` journal event. The guard fired
# on the unmutated tree until this was checked, which is the guard doing its job
# on its own author.
#
# ADDING A NAME HERE IS A DELIBERATE ACT, NOT A FIX FOR A RED TEST. The whole
# point is that widening the lane must be a decision someone makes in this file,
# with the parked scope disagreement re-derived first — not a side effect of a
# tidy-up. The test below asserts that no unflagged-lane function has been
# quietly added, so the cheapest way to silence this guard also reddens another.
MISSED_WAKE_LANE = frozenset({MISSED_WAKE_FINDER, "emit_forensic"})

# Functions belonging to the OTHER alarm. These may never read the reason, and
# may never appear in MISSED_WAKE_LANE.
UNFLAGGED_LANE = frozenset(
    {UNFLAGGED_FINDER, "emit_unflagged_forensic", "build_unflagged_surface"}
)


@pytest.fixture(scope="module")
def tree() -> ast.Module:
    return ast.parse(SOURCE.read_text(encoding="utf-8"))


def _module_functions(tree: ast.Module) -> dict:
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _docstring_nodes(tree: ast.Module) -> set:
    """Constant nodes that are docstrings, by identity.

    Excluded from the literal scan: a docstring MENTIONING the reason is
    documentation, not a use, and forbidding it would make the guard fire on
    someone improving a comment.
    """
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) and isinstance(
            body[0].value, ast.Constant
        ) and isinstance(body[0].value.value, str):
            out.add(id(body[0].value))
    return out


def _enclosing_function(tree: ast.Module, target: ast.AST) -> str | None:
    """Name of the nearest enclosing def, or None for module scope.

    A Lambda is NOT a def: a reference inside a module-level lambda resolves to
    None here, which is exactly the 1b idiom and must read as a violation.
    """
    stack: list = []

    def walk(node, current):
        if node is target:
            stack.append(current)
            return
        name = current
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
        for child in ast.iter_child_nodes(node):
            walk(child, name)

    walk(tree, None)
    return stack[0] if stack else None


class TestTheGuardsSubjectsExist:
    """If either finder is renamed this guard is pointed at nothing.

    Asserted explicitly so a rename reddens HERE with a message saying to
    re-point the guard, rather than silently passing because the thing it
    inspects no longer exists — which is the vacuity mode a shape guard fails
    into most easily.
    """

    @pytest.mark.parametrize("name", [MISSED_WAKE_FINDER, UNFLAGGED_FINDER])
    def test_both_finders_are_still_module_level_functions(self, tree, name):
        assert name in _module_functions(tree), (
            f"{name} is gone or is no longer module-level. This guard inspects it "
            "by name; re-point the guard rather than deleting this assertion."
        )

    def test_the_reason_constant_is_still_defined_at_module_scope(self, tree):
        assigned = [
            t.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for t in node.targets
            if isinstance(t, ast.Name)
        ]
        assert REASON_CONST in assigned, (
            f"{REASON_CONST} is no longer assigned at module scope — Guard A "
            "keys on that name and would silently measure nothing."
        )


class TestGuardA_TheReasonFilterStaysInsideOneFinder:
    """BARRED EDIT 1: hoisting the reason filter to module scope.

    The module-level DEFINITION of the constant is fine. Every READ of it must
    sit inside `find_stale_missed_wakes`. Hoisting the test into a helper or a
    lambda moves the read out, which is the coupling: once the filter is
    reachable from module scope, the other finder can adopt it, and the day it
    does, the two alarms share a vocabulary the design says they must not.
    """

    def test_the_constant_is_read_only_inside_the_missed_wake_lane(self, tree):
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Name) or node.id != REASON_CONST:
                continue
            if isinstance(node.ctx, ast.Store):
                continue  # the module-level definition itself
            where = _enclosing_function(tree, node)
            if where not in MISSED_WAKE_LANE:
                offenders.append(where or "<module scope>")
        assert not offenders, (
            "BARRED EDIT 1 — the reason filter has been hoisted out of the "
            f"missed-wake lane. {REASON_CONST} is now read from: "
            + ", ".join(sorted(set(offenders)))
            + f". The lane is {sorted(MISSED_WAKE_LANE)}. The two alarms must "
            "not share a reason vocabulary. If the new site is genuinely part "
            "of the missed-wake alarm, widen MISSED_WAKE_LANE deliberately — "
            "and if it is on the unflagged side, that is the coupling this "
            "guard exists to stop; re-derive the parked scope disagreement "
            "first. See this file's docstring."
        )

    def test_the_lane_has_not_been_widened_to_swallow_the_other_alarm(self):
        """The cheapest way to silence the arm above is to widen the lane.

        This makes that visible rather than silent: the lane may grow, but not
        to include a function belonging to the unflagged alarm, which is the
        only widening that actually destroys the invariant.
        """
        overlap = MISSED_WAKE_LANE & UNFLAGGED_LANE
        assert not overlap, (
            "BARRED EDIT 1 — MISSED_WAKE_LANE now contains unflagged-lane "
            "function(s): " + ", ".join(sorted(overlap))
            + ". Widening the lane to silence the reason-read assertion is the "
            "coupling wearing the guard's own clothes."
        )

    def test_the_reason_literal_is_not_inlined_anywhere_else(self, tree):
        """Keying on the NAME alone is not enough.

        A hoist that inlines the string instead of importing the constant would
        leave the name-based assertion green. Docstrings are excluded, so
        documenting the reason is still free.
        """
        reason_value = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == REASON_CONST:
                        reason_value = node.value
        assert reason_value is not None
        docstrings = _docstring_nodes(tree)
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or node.value != reason_value.value:
                continue
            if node is reason_value or id(node) in docstrings:
                continue
            where = _enclosing_function(tree, node)
            if where not in MISSED_WAKE_LANE:
                offenders.append(where or "<module scope>")
        assert not offenders, (
            "BARRED EDIT 1 — the reason literal is inlined outside the "
            "missed-wake lane, at: " + ", ".join(sorted(set(offenders)))
            + ". Use the module constant and keep the comparison inside the "
            "lane."
        )


class TestGuardB_TheTwoFindersShareNoSelector:
    """BARRED EDIT 2: routing both finders through one selector.

    Callees are restricted to functions DEFINED IN THIS MODULE. Shared builtins
    and shared imports are NOT a violation — `outstanding_unflagged` is imported
    inside the unflagged finder and is its correct gated read path; forbidding
    shared imports would redden on ordinary work and buy nothing.
    """

    @staticmethod
    def _module_callees(tree: ast.Module, fn_name: str) -> set:
        defined = set(_module_functions(tree))
        fn = _module_functions(tree)[fn_name]
        called = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called.add(node.func.id)
        return called & defined

    def test_no_module_defined_callee_is_shared(self, tree):
        a = self._module_callees(tree, MISSED_WAKE_FINDER)
        b = self._module_callees(tree, UNFLAGGED_FINDER)
        shared = a & b
        assert not shared, (
            "BARRED EDIT 2 — both finders now route through the same "
            "module-level selector(s): " + ", ".join(sorted(shared))
            + ". They are permitted to share a PROCESS, not a SELECTOR: a "
            "shared selector is what makes one alarm's scope decision bind the "
            "other's."
        )

    def test_neither_finder_calls_the_other(self, tree):
        assert MISSED_WAKE_FINDER not in self._module_callees(
            tree, UNFLAGGED_FINDER
        ), (
            f"BARRED EDIT 2 — {UNFLAGGED_FINDER} now calls {MISSED_WAKE_FINDER}. "
            "One finder driving the other couples their scope decisions "
            "directly."
        )
        assert UNFLAGGED_FINDER not in self._module_callees(
            tree, MISSED_WAKE_FINDER
        ), (
            f"BARRED EDIT 2 — {MISSED_WAKE_FINDER} now calls {UNFLAGGED_FINDER}."
        )
