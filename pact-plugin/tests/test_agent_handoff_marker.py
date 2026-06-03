"""
Direct unit coverage for shared/agent_handoff_marker.py — the SSOT shared by
BOTH agent_handoff emit paths (agent_handoff_emitter "b1" + the lead-side
task_lifecycle_gate emit "b2"). #880 Fix B (#887 occupant-identity marker key).

This file is the HOME for module-level coverage of the SSOT primitives. It
focuses on the surface NOT already exercised through the emitter's main()
in the sibling files:

  - occupant_hash(): cross-process determinism (the hashlib-not-builtin-hash
    property #887 rests on), distinctness, path-safety, separator-collision
    resistance.
  - is_signal_task(): the shared emit-eligibility atom.
  - already_emitted(): the occupant-keyed test-and-set — specifically the
    #887 HEART (same team + same task_id + DIFFERENT occupant → BOTH emit)
    and the within-session ~37x re-fire dedup, with same-fixture positive
    controls.
  - the NEW post-mkdir TOCTOU containment re-check (realpath + commonpath
    after mkdir) — distinct from the is_symlink() PRE-check that the
    TestMarkerDirSymlinkGuard family in test_emitter_idempotency.py already
    covers. O_NOFOLLOW guards only the FINAL path component, so a symlinked
    marker_dir appearing in the race window would still let os.open write
    THROUGH it — the re-check is the only thing stopping the escaped write.

Already covered ELSEWHERE (not duplicated here):
  - sanitize_path_component() + degenerate-value guards → test_emitter_path_sanitization.py
  - is_symlink() PRE-check + fail-open (PermissionError/ENOSPC) → test_emitter_idempotency.py
  - 8-thread concurrent O_EXCL race → test_emitter_idempotency.py::TestConcurrentFireRace

Non-vacuity (#887): the same-task_id-different-occupant rows below FAIL if the
marker key is reverted to the bare {task_id} (pre-#887) shape — they are the
delete-the-fix counter-tests. See the docstring on TestOccupantKeyedDedup.
"""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from shared.agent_handoff_marker import (
    already_emitted,
    is_signal_task,
    occupant_hash,
    sanitize_path_component,
)

HOOKS_DIR = Path(__file__).parent.parent / "hooks"


# =============================================================================
# occupant_hash — the #887 key-derivation primitive
# =============================================================================
class TestOccupantHash:
    def test_pins_hashlib_sha256_algorithm(self):
        """occupant_hash MUST be hashlib SHA-256 of f'{agent}\\x00{subject}',
        first 16 hex chars. Pinning the exact algorithm (not just 'some
        stable hash') guards against a refactor silently swapping in the
        builtin hash() — which PYTHONHASHSEED salts per process and would
        break the cross-process O_EXCL dedup."""
        agent, subject = "backend-coder", "implement feature X"
        expected = hashlib.sha256(
            f"{agent}\x00{subject}".encode("utf-8")
        ).hexdigest()[:16]
        assert occupant_hash(agent, subject) == expected

    def test_digest_is_path_safe_hex_of_fixed_length(self):
        occ = occupant_hash("a/../b", "subject\x00with/controls")
        assert len(occ) == 16
        assert all(c in "0123456789abcdef" for c in occ), (
            "hex digest must carry no path separators / traversal / control "
            f"chars; got {occ!r}"
        )

    def test_in_process_determinism(self):
        assert occupant_hash("x", "y") == occupant_hash("x", "y")

    def test_distinct_agent_distinct_hash(self):
        assert occupant_hash("agent-a", "same subject") != occupant_hash(
            "agent-b", "same subject"
        )

    def test_distinct_subject_distinct_hash(self):
        assert occupant_hash("same agent", "subject 1") != occupant_hash(
            "same agent", "subject 2"
        )

    def test_nul_separator_resists_boundary_collision(self):
        """The \\x00 join means (agent='a', subject='bc') and
        (agent='ab', subject='c') do NOT collide — without the separator,
        naive concatenation 'abc' would alias them and let one occupant's
        marker suppress a genuinely different occupant."""
        assert occupant_hash("a", "bc") != occupant_hash("ab", "c")

    def test_cross_process_determinism_under_varying_hashseed(self):
        """THE #887-load-bearing property: the digest is identical across
        processes with DIFFERENT PYTHONHASHSEED values, and equals the
        in-process value. b1 (teammate process) and b2 (lead process) must
        derive the same marker key for the cross-process O_EXCL dedup to
        work; a PYTHONHASHSEED-salted builtin hash() would diverge."""
        agent, subject = "occupant-agent", "a representative task subject"
        in_proc = occupant_hash(agent, subject)
        seen = {in_proc}
        for seed in ("0", "1", "12345"):
            out = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import sys; sys.path.insert(0, sys.argv[1]); "
                        "from shared.agent_handoff_marker import occupant_hash; "
                        "print(occupant_hash(sys.argv[2], sys.argv[3]))"
                    ),
                    str(HOOKS_DIR),
                    agent,
                    subject,
                ],
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONHASHSEED": seed},
                check=True,
            )
            seen.add(out.stdout.strip())
        assert seen == {in_proc}, (
            "occupant_hash diverged across PYTHONHASHSEED values "
            f"{sorted(seen)} — the digest is NOT cross-process stable. "
            "This breaks the b1/b2 shared-marker dedup (#887)."
        )

    def test_builtin_hash_would_diverge_contrast(self):
        """Contrast / rationale guard: demonstrate that the builtin hash()
        of the SAME payload is NOT stable across PYTHONHASHSEED values — the
        exact failure occupant_hash() avoids by using hashlib. Robust against
        a rare seed-collision by sampling 5 seeds and asserting they are not
        ALL identical."""
        # Reconstruct the NUL-joined payload INSIDE the child — an embedded
        # NUL cannot be passed through argv.
        digests = set()
        for seed in ("1", "2", "3", "4", "5"):
            out = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    'print(hash("occupant-agent\\x00a representative task subject"))',
                ],
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONHASHSEED": seed},
                check=True,
            )
            digests.add(out.stdout.strip())
        assert len(digests) > 1, (
            "builtin hash() was identical across 5 PYTHONHASHSEED values — "
            "extraordinarily unlikely; the contrast that motivates hashlib "
            "may no longer hold."
        )


# =============================================================================
# is_signal_task — the shared emit-eligibility atom
# =============================================================================
class TestIsSignalTask:
    @pytest.mark.parametrize("ttype", ["blocker", "algedonic"])
    def test_signal_types_are_excluded(self, ttype):
        assert is_signal_task({"type": ttype}) is True

    @pytest.mark.parametrize(
        "metadata",
        [
            {"type": "work"},
            {"type": "teachback"},
            {},
            {"type": None},
            None,
            "not-a-dict",
            ["type", "blocker"],
        ],
    )
    def test_non_signal_or_malformed_is_false(self, metadata):
        assert is_signal_task(metadata) is False


# =============================================================================
# already_emitted — occupant-keyed test-and-set (#887 heart)
# =============================================================================
class TestOccupantKeyedDedup:
    """The #887 fix: keying the marker on (team, task_id, occupant) rather
    than the bare (team, task_id).

    NON-VACUITY: test_same_task_id_different_occupant_both_emit is the
    delete-the-fix counter-test — if occupant_hash is collapsed to a constant
    (reverting to the bare-{task_id} key), the two DIFFERENT occupants share
    a marker and the second is suppressed (1 winner, not 2), failing the
    assertion. Documented expected cardinality: {2 winners}; pre-#887: {1}.
    """

    def test_same_task_id_different_occupant_both_emit(self, tmp_path, monkeypatch):
        """HEART OF #887: a reused (team, task_id) under two DIFFERENT
        occupants must produce TWO winners — the new occupant's HANDOFF is
        not falsely suppressed by the prior occupant's stale marker."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team, task_id = "pact-test", "5"
        occ_a = occupant_hash("agent-alpha", "alpha's task")
        occ_b = occupant_hash("agent-beta", "beta's task")
        assert occ_a != occ_b  # precondition: genuinely different occupants

        first = already_emitted(team, task_id, occ_a)
        second = already_emitted(team, task_id, occ_b)
        assert first is False, "first occupant must win (emit)"
        assert second is False, (
            "DIFFERENT occupant on the same task_id must ALSO win (emit) — "
            "this is the #887 collision fix. If this is True, the marker key "
            "has regressed to the bare {task_id} shape."
        )
        # Two distinct marker files exist.
        marker_dir = tmp_path / ".claude" / "teams" / team / ".agent_handoff_emitted"
        assert (marker_dir / f"{task_id}-{occ_a}").exists()
        assert (marker_dir / f"{task_id}-{occ_b}").exists()

    def test_same_occupant_dedups_positive_control(self, tmp_path, monkeypatch):
        """POSITIVE CONTROL for the row above: the SAME occupant on the same
        task_id is deduped (fire-once preserved) — proving the row above
        emits twice because the occupants DIFFER, not because dedup is
        globally broken."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team, task_id, occ = "pact-test", "5", occupant_hash("agent", "subj")
        assert already_emitted(team, task_id, occ) is False, "first fire wins"
        assert already_emitted(team, task_id, occ) is True, (
            "same occupant re-fire must be suppressed (standing-task "
            "fire-once-across-lifespan preserved)"
        )

    def test_cross_session_same_occupant_fire_once(self, tmp_path, monkeypatch):
        """A standing task spanning sessions (same occupant, separate
        already_emitted calls simulating separate session fires) must emit
        exactly once across the team lifespan — the marker dir is
        task-scoped, not session-scoped."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team, task_id, occ = "pact-team", "standing-7", occupant_hash("secretary", "harvest")
        winners = sum(
            1 for _ in range(3) if already_emitted(team, task_id, occ) is False
        )
        assert winners == 1, f"expected fire-once across sessions, got {winners}"

    def test_within_session_37x_stop_sweep_refire_dedups(self, tmp_path, monkeypatch):
        """The platform's Stop flow dispatches TaskCompleted on every matching
        owner — empirically ~37x in the #528 amplification class. The
        occupant marker must collapse all of them to exactly ONE winner."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team, task_id, occ = "pact-test", "amp", occupant_hash("coder", "amplified task")
        winners = sum(
            1 for _ in range(37) if already_emitted(team, task_id, occ) is False
        )
        assert winners == 1, (
            f"expected exactly 1 winner across 37 re-fires, got {winners} — "
            "the #528 amplification dedup has regressed."
        )

    def test_marker_filename_is_occupant_keyed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team, task_id = "pact-test", "probe"
        occ = occupant_hash("probe-agent", "probe subject")
        already_emitted(team, task_id, occ)
        expected = (
            tmp_path / ".claude" / "teams" / team / ".agent_handoff_emitted"
            / f"{task_id}-{occ}"
        )
        assert expected.exists(), "marker must be written at {task_id}-{occupant}"


# =============================================================================
# TOCTOU containment re-check (NEW in #880 — post-mkdir realpath/commonpath)
# =============================================================================
class TestTocTouContainmentRecheck:
    """The post-mkdir re-check (realpath(marker_dir) must stay inside
    realpath(team_base)) closes the race window between the is_symlink()
    PRE-check and mkdir(exist_ok=True): a symlink swapped in AFTER the
    pre-check would be silently followed by mkdir, and O_NOFOLLOW only
    protects the FINAL path component — so os.open would write THROUGH a
    symlinked marker_dir to an escaped location. This family pins that the
    re-check fail-open emits (returns False) WITHOUT an escaped marker write.

    Distinct from TestMarkerDirSymlinkGuard (test_emitter_idempotency.py),
    which covers the is_symlink() PRE-check only.
    """

    def test_symlink_appearing_after_precheck_is_contained(self, tmp_path, monkeypatch):
        """Simulate the TOCTOU race: marker_dir IS a symlink escaping the
        team base, but is_symlink() reports False (as it would have at
        pre-check time). The re-check must catch it via realpath/commonpath,
        return False (fail-open emit), and write NO marker at the escape
        target.

        NON-VACUITY: deleting the re-check lets os.open create
        `<escape>/{task_id}-{occ}` (O_NOFOLLOW does not stop traversal
        through a symlinked DIRECTORY) — the final assertion would then fail.
        """
        team = "pact-test"
        team_base = tmp_path / ".claude" / "teams" / team
        team_base.mkdir(parents=True)
        escape_target = tmp_path / "escape_target"
        escape_target.mkdir()
        marker_dir = team_base / ".agent_handoff_emitted"
        marker_dir.symlink_to(escape_target, target_is_directory=True)

        # Patch is_symlink → False AFTER constructing the symlink: models the
        # pre-check passing (path was not a symlink at check time) while the
        # path is a symlink by the time mkdir/realpath run.
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(Path, "is_symlink", lambda self: False)

        result = already_emitted(team, "5", "occ")

        assert result is False, (
            "TOCTOU breach must fail-open emit (return False)"
        )
        assert not (escape_target / "5-occ").exists(), (
            "no marker may be written at the escaped (symlink) target — the "
            "post-mkdir containment re-check must short-circuit before "
            "os.open. If this file exists, the re-check was removed/bypassed."
        )

    def test_realpath_resolution_error_fails_open(self, tmp_path, monkeypatch):
        """Any resolution error during the containment re-check (OSError /
        ValueError from realpath/commonpath) is treated as fail-open emit."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        def _boom(_path):
            raise OSError("simulated resolution failure")

        monkeypatch.setattr(os.path, "realpath", _boom)
        assert already_emitted("pact-test", "5", "occ") is False

    def test_contained_marker_dir_proceeds_positive_control(self, tmp_path, monkeypatch):
        """POSITIVE CONTROL: an ordinary contained marker_dir passes the
        re-check — first call wins (False + marker created), second observes
        EEXIST (True). Proves the re-check discriminates breach from the
        normal path rather than rejecting everything."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team, task_id, occ = "pact-test", "5", "occ"
        assert already_emitted(team, task_id, occ) is False
        assert already_emitted(team, task_id, occ) is True
        marker = (
            tmp_path / ".claude" / "teams" / team / ".agent_handoff_emitted"
            / f"{task_id}-{occ}"
        )
        assert marker.exists()


# =============================================================================
# Degenerate-value guard (smoke — full matrix in test_emitter_path_sanitization)
# =============================================================================
class TestDegenerateGuardSmoke:
    @pytest.mark.parametrize("bad", ["", ".", ".."])
    def test_degenerate_task_id_emits_without_marker(self, tmp_path, monkeypatch, bad):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # bad task_id sanitizes to a degenerate value → fail-open emit, no marker
        assert already_emitted("pact-test", bad, "occ") is False

    def test_missing_occupant_emits_without_marker(self, tmp_path, monkeypatch):
        """An empty occupant (only reachable if a caller bypasses
        occupant_hash) is treated as 'no valid key' → fail-open emit."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert already_emitted("pact-test", "5", "") is False
        # sanitize_path_component is the SSOT used internally; sanity-pin it
        assert sanitize_path_component("..") == ""
