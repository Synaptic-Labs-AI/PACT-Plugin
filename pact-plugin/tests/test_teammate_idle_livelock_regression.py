"""
Dogfood regression harness for the #497 TeammateIdle livelock fix.

Plan row 25 / AC #10 — reproduces the multi-commit stage->notify->lead-commits
workflow that produced 100+ Holding.-pattern nag loops in PR #477 round-8
dogfooding. Asserts:

  - Against pre-fix source (SHA 1922c64, one commit before bef7f24): the
    nag DOES fire on every idle tick during a protocol-defined wait,
    demonstrating the bug.
  - Against current source (post-bef7f24/7ed354e): the nag is SUPPRESSED
    when intentional_wait is set, and re-enables only on staleness or
    flag-clear.

Coverage of the three livelock phases from #497 issue body:
  - pre-work:  teachback gate (awaiting teachback_approved)
  - mid-work:  inter-commit gate (awaiting lead_commit after stage-ready notify)
  - post-work: final-hold gate (awaiting post_handoff_decision)

Harness shape (pytest-native, no tmp-worktree required):

  Pre-fix hook modules are obtained via `git show <pre-fix-commit>:<path>`,
  written to tempfiles, and loaded with `importlib.util.spec_from_file_location`
  under a unique module name so they coexist with the currently-imported
  (post-fix) modules. This lets a single test run both variants and compare
  their behavior side-by-side — no filesystem side effects, no worktree churn.

  The pre-fix-loaded modules depend on `shared.*` modules; those resolve to
  the currently-checked-out versions via the existing hooks path on sys.path.
  This is safe because the shared modules did not change between pre-fix and
  post-fix (verified: `git diff 1922c64 HEAD -- pact-plugin/hooks/shared/`
  only adds `shared/intentional_wait.py` — never a breaking edit to an
  existing shared module).

Counter-claim defense: without the pre-fix variant loaded, a harness that
only exercises the current source cannot distinguish "bug absent because
fix works" from "bug absent because test setup doesn't reach the bug path."
Loading pre-fix source and asserting the bug DOES reproduce there pins
harness validity.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import pytest

# #538 C2a: teammate_completion_gate.py is deleted. The #497 regression
# harness below imports that module + depends on dual-hook parity which no
# longer exists. C3 rewrites this file to cover the surviving teammate_idle
# threshold-escalation path (plus the new #538 dogfood-regression file at
# tests/test_dogfood_livelock_invariant.py supersedes the livelock coverage).
# Skip at module load to keep the repo green between C2a and C3.
pytest.skip(
    "Superseded by #538 C3 teammate_idle rewrite + #538 C5 dogfood regression. "
    "Pre-#497 livelock pattern no longer reproducible after teammate_completion_gate "
    "deletion in #538 C2a.",
    allow_module_level=True,
)

# --- setup -----------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

# Git ref to the pre-fix state of both TeammateIdle hooks. The tag
# `test-fixture/pre-497-fix` is created pre-merge and pushed to origin,
# pointing at SHA 1922c64 — the commit that added shared/intentional_wait.py
# but did NOT yet modify either hook to call it. At that commit both hooks
# are in their pre-fix shape (verified: `git show test-fixture/pre-497-fix:
# pact-plugin/hooks/teammate_{idle,completion_gate}.py` contains neither
# "intentional_wait" nor "wait_stale" nor the type/stalled metadata skips
# in completion_gate).
#
# Tag-pinned rather than SHA-pinned so the harness survives squash-merge:
# if the PR squash-merges, commit 1922c64 becomes unreachable from main
# and is eventually reclaimed — a bare SHA reference would then fail.
# The tag keeps 1922c64 alive regardless of branch history changes.
PRE_FIX_COMMIT = "test-fixture/pre-497-fix"


def _iso_seconds(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _fresh_wait(reason: str = "awaiting_teachback_approved",
                resolver: str = "lead",
                seconds_ago: int = 60) -> dict:
    return {
        "reason": reason,
        "expected_resolver": resolver,
        "since": _iso_seconds(datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)),
    }


def _load_pre_fix_module(hook_name: str, tmp_path: Path) -> ModuleType:
    """Materialize pre-fix hook source into a loadable module.

    Uses `git show <pre-fix-commit>:<path>` to read the old source, writes
    it to tmp_path, and loads it under a unique module name so it doesn't
    shadow the currently-imported post-fix module.

    shared.* imports inside the pre-fix module resolve normally because
    (a) they are path-agnostic top-level imports and (b) the shared
    modules themselves were not breakingly changed between pre-fix and
    post-fix (verified via `git diff 1922c64 HEAD -- hooks/shared/`).
    """
    repo_root = Path(__file__).parent.parent.parent
    relative_path = f"pact-plugin/hooks/{hook_name}.py"
    result = subprocess.run(
        ["git", "show", f"{PRE_FIX_COMMIT}:{relative_path}"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )
    pre_fix_src = result.stdout
    assert pre_fix_src, f"pre-fix source for {hook_name} is empty"
    # Structural precondition: pre-fix source must lack the intentional_wait skip
    assert "intentional_wait" not in pre_fix_src, (
        f"PRE_FIX_COMMIT ({PRE_FIX_COMMIT}) is not actually pre-fix — "
        f"intentional_wait appears in source. Check commit history."
    )

    pre_fix_file = tmp_path / f"{hook_name}_prefix.py"
    pre_fix_file.write_text(pre_fix_src, encoding="utf-8")

    module_name = f"{hook_name}_prefix_v497"
    spec = importlib.util.spec_from_file_location(module_name, str(pre_fix_file))
    assert spec is not None, f"Failed to create module spec for {pre_fix_file}"
    assert spec.loader is not None, f"Module spec has no loader for {pre_fix_file}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_in_progress_task(owner: str = "coder-a",
                           task_id: str = "5",
                           subject: str = "CODE: auth",
                           metadata: dict | None = None) -> dict:
    return {
        "id": task_id,
        "subject": subject,
        "status": "in_progress",
        "owner": owner,
        "metadata": metadata or {},
    }


# --- pre-fix bug reproduction ---------------------------------------------

class TestLivelockReproducesOnPreFixSource:
    """Asserts the bug DOES fire on pre-fix source. If these tests do NOT
    fail on pre-fix, the harness is not actually reaching the bug path and
    all post-fix assertions are phantom-green."""

    def test_pre_fix_detect_stall_nags_on_in_progress_idle(self, tmp_path):
        """Pre-fix: in_progress + idle -> nag (no suppression)."""
        mod = _load_pre_fix_module("teammate_idle", tmp_path)
        tasks = [_make_in_progress_task()]
        result = mod.detect_stall(tasks, "coder-a")
        assert result is not None
        assert "stall" in result.lower()

    def test_pre_fix_nags_even_with_intentional_wait_set(self, tmp_path):
        """Pre-fix root-cause demonstration: intentional_wait is ignored,
        so the nag fires despite the flag — this is the livelock."""
        mod = _load_pre_fix_module("teammate_idle", tmp_path)
        tasks = [_make_in_progress_task(metadata={
            "intentional_wait": _fresh_wait(),
        })]
        result = mod.detect_stall(tasks, "coder-a")
        assert result is not None, (
            "Pre-fix source must nag even with intentional_wait set — "
            "that's the #497 bug. If this assertion fails, the harness is "
            "not reaching the pre-fix code path."
        )
        assert "stall" in result.lower()

    def test_pre_fix_completion_gate_scans_idle_task_as_missing_handoff(self, tmp_path):
        """Pre-fix completion_gate also ignores intentional_wait, surfacing
        the task as missing_handoff on every idle tick."""
        mod = _load_pre_fix_module("teammate_completion_gate", tmp_path)
        team_dir = tmp_path / ".claude" / "tasks" / "pact-test"
        team_dir.mkdir(parents=True)
        task = _make_in_progress_task(owner="backend-coder")
        task["metadata"] = {"intentional_wait": _fresh_wait()}
        (team_dir / "5.json").write_text(json.dumps(task))

        completable, missing = mod._scan_owned_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(missing) == 1, (
            "Pre-fix completion_gate must surface the task as missing_handoff "
            "despite intentional_wait — that's the livelock second hook."
        )

    def test_pre_fix_nags_across_10_idle_ticks(self, tmp_path):
        """Pre-fix: repeat detect_stall 10 times for the same task — every
        call returns a nag. This is the shape of the 100+ holding-pattern
        loop observed in PR #477 dogfooding."""
        mod = _load_pre_fix_module("teammate_idle", tmp_path)
        tasks = [_make_in_progress_task(metadata={
            "intentional_wait": _fresh_wait(),
        })]
        nags = [mod.detect_stall(tasks, "coder-a") for _ in range(10)]
        assert all(n is not None for n in nags), (
            "Pre-fix reproduction: all 10 idle ticks produce a nag message. "
            "That is the livelock."
        )


# --- current source (fix applied) -----------------------------------------

class TestLivelockFixedOnCurrentSource:
    """Asserts the bug does NOT fire on current source with intentional_wait
    set. Paired with the pre-fix reproduction tests above — both must pass
    for the harness to be considered valid proof of the fix.

    Covers the three livelock phases from the #497 issue body:
    pre-work (teachback), mid-work (inter-commit), post-work (final-hold).
    """

    def test_pre_work_teachback_wait_suppresses_across_10_ticks(self):
        """Phase 1 — pre-work: teammate awaiting teachback_approved. 10 idle
        ticks, zero nags."""
        from teammate_idle import detect_stall
        tasks = [_make_in_progress_task(metadata={
            "intentional_wait": _fresh_wait(
                reason="awaiting_teachback_approved",
                resolver="lead",
            ),
        })]
        nags = [detect_stall(tasks, "coder-a") for _ in range(10)]
        assert all(n is None for n in nags), (
            f"Phase 1 (teachback wait) livelock fixed: expected 0 nags, "
            f"got {sum(1 for n in nags if n is not None)}"
        )

    def test_mid_work_inter_commit_wait_suppresses_across_10_ticks(self):
        """Phase 2 — mid-work: teammate staged work, notified lead, awaiting
        commit before next stage. 10 idle ticks, zero nags."""
        from teammate_idle import detect_stall
        tasks = [_make_in_progress_task(metadata={
            "intentional_wait": _fresh_wait(
                reason="awaiting_lead_commit",
                resolver="lead",
            ),
        })]
        nags = [detect_stall(tasks, "coder-a") for _ in range(10)]
        assert all(n is None for n in nags)

    def test_post_work_final_hold_suppresses_across_10_ticks(self):
        """Phase 3 — post-work: HANDOFF stored, awaiting lead's final
        decision before TaskUpdate(status=completed). 10 idle ticks,
        zero nags, even though HANDOFF is present (completable branch)."""
        from teammate_idle import detect_stall
        tasks = [_make_in_progress_task(metadata={
            "handoff": {
                "produced": ["x.py"], "decisions": ["y"],
                "uncertainty": [], "integration": [], "open_questions": [],
            },
            "intentional_wait": _fresh_wait(
                reason="awaiting_post_handoff_decision",
                resolver="lead",
            ),
        })]
        nags = [detect_stall(tasks, "coder-a") for _ in range(10)]
        assert all(n is None for n in nags)

    def test_completion_gate_suppresses_missing_handoff_branch(self, tmp_path):
        """Phase 1+2 via completion_gate: idle teammate with intentional_wait
        but no HANDOFF yet — the missing_handoff branch that drove the
        second livelock hook — now suppresses."""
        from teammate_completion_gate import _scan_owned_tasks
        team_dir = tmp_path / ".claude" / "tasks" / "pact-test"
        team_dir.mkdir(parents=True)
        task = _make_in_progress_task(owner="backend-coder")
        task["metadata"] = {"intentional_wait": _fresh_wait()}
        (team_dir / "5.json").write_text(json.dumps(task))

        for _ in range(10):
            completable, missing = _scan_owned_tasks(
                "backend-coder", "pact-test",
                tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
            )
            assert completable == []
            assert missing == []

    def test_completion_gate_suppresses_completable_branch(self, tmp_path):
        """Phase 3 via completion_gate: HANDOFF present + wait set — the
        completable branch that fires "agent forgot to self-complete" nag —
        now suppresses."""
        from teammate_completion_gate import _scan_owned_tasks
        team_dir = tmp_path / ".claude" / "tasks" / "pact-test"
        team_dir.mkdir(parents=True)
        task = _make_in_progress_task(owner="backend-coder")
        task["metadata"] = {
            "handoff": {
                "produced": ["x.py"], "decisions": ["y"],
                "uncertainty": [], "integration": [], "open_questions": [],
            },
            "intentional_wait": _fresh_wait(
                reason="awaiting_post_handoff_decision",
            ),
        }
        (team_dir / "5.json").write_text(json.dumps(task))

        for _ in range(10):
            completable, missing = _scan_owned_tasks(
                "backend-coder", "pact-test",
                tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
            )
            assert completable == []
            assert missing == []


# --- multi-commit amendment-review cycle ----------------------------------

class TestMultiCommitAmendmentCycleHasNoLivelock:
    """Simulates the full PR #477 cycle-8-style workflow:

      1. Teammate stages commit A, notifies lead, sets awaiting_lead_commit.
      2. Lead commits A, teammate CLEARs wait, stages commit B, notifies
         lead, sets awaiting_lead_commit.
      3. Reviewer flags amendment needed, teammate sets
         awaiting_amendment_review, stages amendment, notifies lead.
      4. Lead commits amendment, teammate CLEARs wait.

    Asserts: at every idle tick across this 4-phase workflow, both
    TeammateIdle hooks produce zero nags when intentional_wait is fresh.
    """

    def _detect_both(self, tasks, owner, task_dir_path):
        """Invoke both TeammateIdle hooks for a given task set + owner."""
        from teammate_idle import detect_stall
        from teammate_completion_gate import _scan_owned_tasks

        idle_result = detect_stall(tasks, owner)
        # _scan_owned_tasks reads from disk; persist the task for it
        for t in tasks:
            (Path(task_dir_path) / f"{t['id']}.json").write_text(
                json.dumps(t), encoding="utf-8"
            )
        completable, missing = _scan_owned_tasks(
            owner, "pact-test", tasks_base_dir=str(Path(task_dir_path).parent),
        )
        return idle_result, completable, missing

    def test_full_cycle_zero_nags(self, tmp_path):
        team_dir = tmp_path / ".claude" / "tasks" / "pact-test"
        team_dir.mkdir(parents=True)

        # --- Phase 1: stage A, await lead commit ---
        task = _make_in_progress_task(owner="coder-a", task_id="5")
        task["metadata"] = {
            "intentional_wait": _fresh_wait(reason="awaiting_lead_commit"),
        }
        tasks = [task]

        for _ in range(5):
            idle, completable, missing = self._detect_both(tasks, "coder-a",
                                                            str(team_dir))
            assert idle is None, "Phase 1: detect_stall must suppress"
            assert completable == [] and missing == [], \
                "Phase 1: completion_gate must suppress"

        # --- Phase 2: lead commits A; teammate clears wait, stages B ---
        task["metadata"] = {
            "intentional_wait": _fresh_wait(reason="awaiting_lead_commit"),
        }
        tasks = [task]
        for _ in range(5):
            idle, completable, missing = self._detect_both(tasks, "coder-a",
                                                            str(team_dir))
            assert idle is None
            assert completable == [] and missing == []

        # --- Phase 3: reviewer flags amendment, teammate sets
        # awaiting_amendment_review, stages amendment ---
        task["metadata"] = {
            "intentional_wait": _fresh_wait(
                reason="awaiting_amendment_review",
                resolver="peer",
            ),
        }
        tasks = [task]
        for _ in range(5):
            idle, completable, missing = self._detect_both(tasks, "coder-a",
                                                            str(team_dir))
            assert idle is None
            assert completable == [] and missing == []

        # --- Phase 4: amendment committed; teammate has HANDOFF + wait
        # (awaiting_post_handoff_decision) ---
        task["metadata"] = {
            "handoff": {
                "produced": ["a.py"], "decisions": ["pat"],
                "uncertainty": [], "integration": [], "open_questions": [],
            },
            "intentional_wait": _fresh_wait(
                reason="awaiting_post_handoff_decision",
            ),
        }
        tasks = [task]
        for _ in range(5):
            idle, completable, missing = self._detect_both(tasks, "coder-a",
                                                            str(team_dir))
            assert idle is None
            assert completable == [] and missing == []


# --- threshold boundary (staleness re-enables nag) ------------------------

class TestStaleWaitRestoresNagAsSafetyValve:
    """Belt-and-suspenders: if a teammate forgets to CLEAR their wait after
    state advancement, the 30-min staleness threshold re-enables the nag.
    This prevents a "set once and forget" failure mode from producing a
    silent permanent-wait state."""

    def test_stale_wait_re_enables_nag_on_detect_stall(self):
        from teammate_idle import detect_stall

        stale_since = datetime.now(timezone.utc) - timedelta(minutes=45)
        tasks = [_make_in_progress_task(metadata={
            "intentional_wait": {
                "reason": "awaiting_teachback_approved",
                "expected_resolver": "lead",
                "since": _iso_seconds(stale_since),
            }
        })]
        for _ in range(3):
            result = detect_stall(tasks, "coder-a")
            assert result is not None, (
                "Stale wait safety valve: nag must re-enable after "
                "threshold expires, preventing silent permanent waits."
            )

    def test_stale_wait_surfaces_in_completion_gate(self, tmp_path):
        from teammate_completion_gate import _scan_owned_tasks

        team_dir = tmp_path / ".claude" / "tasks" / "pact-test"
        team_dir.mkdir(parents=True)
        stale_since = datetime.now(timezone.utc) - timedelta(minutes=45)
        task = _make_in_progress_task(owner="backend-coder")
        task["metadata"] = {"intentional_wait": {
            "reason": "awaiting_teachback_approved",
            "expected_resolver": "lead",
            "since": _iso_seconds(stale_since),
        }}
        (team_dir / "5.json").write_text(json.dumps(task))

        completable, missing = _scan_owned_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(missing) == 1, (
            "Stale wait must surface in completion_gate too — both hooks "
            "must consult staleness symmetrically."
        )


# --- dual-hook behavioral parity under livelock shapes --------------------

class TestDualHookParityUnderLivelockShapes:
    """Sibling of test_teammate_completion_gate.py::TestDualHookParityAllFourSkips
    — but focused on the specific shapes that produced the PR #477 livelock.
    Each shape must either silence BOTH hooks or fire on BOTH (never split).
    """

    @pytest.mark.parametrize("phase_label,metadata", [
        ("pre_work_teachback", {
            "intentional_wait": _fresh_wait(reason="awaiting_teachback_approved"),
        }),
        ("mid_work_inter_commit", {
            "intentional_wait": _fresh_wait(reason="awaiting_lead_commit"),
        }),
        ("post_work_amendment", {
            "intentional_wait": _fresh_wait(reason="awaiting_amendment_review",
                                            resolver="peer"),
        }),
        ("post_work_final_hold", {
            "handoff": {
                "produced": ["x.py"], "decisions": ["y"],
                "uncertainty": [], "integration": [], "open_questions": [],
            },
            "intentional_wait": _fresh_wait(
                reason="awaiting_post_handoff_decision"),
        }),
    ])
    def test_both_hooks_silence_for_phase(self, tmp_path, phase_label, metadata):
        from teammate_idle import detect_stall
        from teammate_completion_gate import _scan_owned_tasks

        team_dir = tmp_path / ".claude" / "tasks" / "pact-test"
        team_dir.mkdir(parents=True)
        task = _make_in_progress_task(owner="coder-a")
        task["metadata"] = metadata
        (team_dir / "5.json").write_text(json.dumps(task))

        idle_silenced = detect_stall([task], "coder-a") is None
        completable, missing = _scan_owned_tasks(
            "coder-a", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        cg_silenced = (completable == [] and missing == [])

        assert idle_silenced, f"Phase {phase_label}: detect_stall must silence"
        assert cg_silenced, f"Phase {phase_label}: completion_gate must silence"
        assert idle_silenced == cg_silenced, (
            f"Phase {phase_label}: dual-hook parity violation"
        )
