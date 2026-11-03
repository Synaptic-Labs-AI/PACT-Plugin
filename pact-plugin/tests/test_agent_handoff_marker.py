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
    PARTITION_FOREIGN,
    PARTITION_FROM,
    PARTITION_OLD_FORMAT,
    PARTITION_THIS,
    already_emitted,
    handoff_already_emitted,
    is_signal_task,
    occupant_hash,
    partition_destination,
    sanitize_path_component,
    unclaim,
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
class TestContentKeyedReemit:
    """A REVISED handoff must RE-EMIT: the handoff discriminator composes the
    occupant term with a content term.

    This pins BEHAVIOUR, not a description. It fails if anyone makes the
    handoff marker content-blind — the shape the module's own `WHAT THIS
    REPLACED` note describes, where the first emit for an occupant claimed the
    marker and every later revision was suppressed for the lifespan of the
    team, reaching no carrier and leaving no record it had been suppressed.

    NON-VACUITY: the third call is the control. Without it this arm would pass
    against a marker that dedups nothing at all, which is the opposite defect
    and equally wrong.
    """

    def test_changed_handoff_content_re_emits(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team, task_id = "pact-test", "5"
        occ = occupant_hash("agent-alpha", "alpha's task")

        first = handoff_already_emitted(team, task_id, occ, "content-v1")
        changed = handoff_already_emitted(team, task_id, occ, "content-v2")
        repeat = handoff_already_emitted(team, task_id, occ, "content-v1")

        assert first is False, "first emit must win"
        assert changed is False, (
            "a REVISED handoff must re-emit — the discriminator carries a "
            "content term. True here means the content term was dropped, and "
            "every later revision of a handoff is silently suppressed."
        )
        assert repeat is True, (
            "CONTROL: unchanged content must still be suppressed. False here "
            "means the marker dedups nothing and the assertion above passes "
            "for the wrong reason."
        )


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
# dir_fd intermediate-dir TOCTOU race (remediation cycle 2)
# =============================================================================
@pytest.mark.skipif(
    os.open not in os.supports_dir_fd,
    reason="platform lacks openat/dir_fd support",
)
class TestDirFdIntermediateDirRace:
    """The marker create opens marker_dir as a dir_fd (O_DIRECTORY|O_NOFOLLOW)
    and creates the marker RELATIVE to that fd. This closes the residual
    INTERMEDIATE-DIR TOCTOU window: between the realpath/commonpath containment
    re-check and the create, marker_dir itself could be swapped to a symlink,
    and a full-path os.open would then write THROUGH the symlinked DIRECTORY
    (O_NOFOLLOW on the marker FILE guards only the final component).

    DISTINCT from TestTocTouContainmentRecheck: that family covers PRE-PLANTED
    symlinks (caught by the is_symlink() pre-check OR the realpath/commonpath
    re-check). The dir_fd fix is load-bearing ONLY for the RACE — marker_dir is
    a REAL contained dir at pre-check AND containment-check time, THEN swapped to
    an escaping symlink BEFORE the dir_fd open. So this test injects the swap in
    exactly that window (as a side effect of the containment check's realpath
    call), which a pre-planted symlink cannot exercise.

    NON-VACUITY: reverting the create to the plain full-path
    `os.open(str(marker_path), ...)` (pre-cycle-2 shape) makes the symlinked
    marker_dir get followed and the marker written THROUGH to the escape target
    — the no-write-through assertion then fails. Verified by isolated-worktree
    revert.
    """

    def test_marker_dir_swapped_to_symlink_after_containment_is_contained(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team, task_id, occ = "pact-test", "race", "occ"

        team_base = tmp_path / ".claude" / "teams" / team
        team_base.mkdir(parents=True)
        escape_target = tmp_path / "escape_target"
        escape_target.mkdir()
        marker_dir = team_base / ".agent_handoff_emitted"

        # Inject the TOCTOU swap as a side effect of the containment check's
        # realpath(marker_dir) call: marker_dir is a REAL empty dir when
        # resolved (so commonpath passes with the real contained path), then
        # is swapped to an escaping symlink BEFORE the dir_fd open runs.
        orig_realpath = os.path.realpath
        state = {"swapped": False}

        def _realpath_then_swap(path, *args, **kwargs):
            resolved = orig_realpath(path, *args, **kwargs)
            if not state["swapped"] and str(path).endswith(".agent_handoff_emitted"):
                # marker_dir was just mkdir'd (real, empty) — swap it now.
                Path(marker_dir).rmdir()
                Path(marker_dir).symlink_to(escape_target, target_is_directory=True)
                state["swapped"] = True
            return resolved

        monkeypatch.setattr(os.path, "realpath", _realpath_then_swap)

        result = already_emitted(team, task_id, occ)

        assert state["swapped"] is True, (
            "vacuity guard: the race-swap must have fired (else the test "
            "passes trivially via normal creation)"
        )
        assert marker_dir.is_symlink(), "marker_dir must be the escaping symlink at open time"
        assert result is False, (
            "a marker_dir swapped to a symlink in the race window must "
            "fail-open EMIT (return False) — the dir_fd O_DIRECTORY|O_NOFOLLOW "
            "open fails on the symlink"
        )
        assert not (escape_target / f"{task_id}-{occ}").exists(), (
            "NO write-through: the marker must NOT be created at the escape "
            "target. If this file exists, the dir_fd pinning was removed and a "
            "full-path open followed the symlinked directory."
        )

    def test_real_contained_marker_dir_still_creates_positive_control(
        self, tmp_path, monkeypatch
    ):
        """POSITIVE CONTROL: with NO swap, an ordinary contained marker_dir
        creates the marker via the dir_fd path (first call → False + marker;
        second → EEXIST → True). Proves the dir_fd open succeeds on the normal
        path and the race assertion above isn't 'dir_fd always fails'."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team, task_id, occ = "pact-test", "ok", "occ"
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


# =============================================================================
# task_id str-normalisation inside the marker resolver
# =============================================================================
class TestTaskIdNormalisation:
    """_resolve_marker_target coerces task_id with str() BEFORE handing it to
    sanitize_path_component, whose bare re.sub RAISES TypeError on a non-str.

    WHY A RAISE IS THE SEVERE CASE, not a mis-key: marker callers sit inside
    emit paths that wrap their whole body in `except Exception: pass` (see
    task_lifecycle_gate's agent_handoff emit). The TypeError is therefore
    SWALLOWED — the emit is skipped and the event silently vanishes from the
    journal rather than failing loudly, so a consumer counting those events
    measures a population smaller than the one that occurred with no signal
    that anything was lost.

    NON-VACUITY — two INDEPENDENT mutations are needed, because no single one
    can red both properties:
      - delete the str() (revert to `sanitize_path_component(task_id)`)
        → every int row below ERRORS with TypeError.
      - collapse the coercion (e.g. `str(task_id)` → a constant)
        → the int rows stop raising and PASS, and only
          test_distinct_int_task_ids_still_discriminate reds.
    That second mutation is the point of the discrimination row: a
    normalisation that maps everything to one value ALSO removes the raise,
    so "no longer raises" alone cannot tell a fix from a lobotomy.

    NOT a restored capability — a NEW one. At base the int rows do not fail
    an assertion, they never REACH one: already_emitted(team, 83, occ) dies
    with TypeError before any comparison happens. So 83-vs-84 discrimination
    was not "working and preserved", it was UNREACHABLE. These rows establish
    that it is now reachable AND that it discriminates.

    SCOPE BOUND: task_id only, and the exclusions are not symmetric with it.
    occupant is a hex digest from occupant_hash() (str by construction).
    team_name is validated upstream against [A-Za-z0-9_-]+ and is NOT
    coerced — a non-str team_name raises AttributeError at .lower(), a
    DIFFERENT exception from a DIFFERENT line than the task_id TypeError this
    class is about. Do not read these rows as "all marker path components are
    normalised"; exactly one is. test_team_name_is_not_coerced pins the
    boundary so a later reader sees it was chosen rather than overlooked.

    The int arguments below are DELIBERATE contract violations, carrying
    type-checker suppressions at each site. task_id's annotation stays `str`
    on purpose: the coercion is a defensive backstop, not a widened contract.
    Passing a non-str task_id remains a CALL-SITE BUG — what changed is that
    the bug stops turning into a silent event loss.
    """

    def test_int_task_id_does_not_raise(self, tmp_path, monkeypatch):
        """Un-normalised, this ERRORS with
        `TypeError: expected string or bytes-like object, got 'int'`."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        occ = occupant_hash("coder", "int task_id subject")
        assert already_emitted("pact-test", 83, occ) is False  # type: ignore[arg-type]  # int is the point

    def test_int_task_id_produces_same_key_as_its_string_form(
        self, tmp_path, monkeypatch
    ):
        """83 and '83' are the SAME dispatch and must share one marker, so a
        claim under either form suppresses a re-fire under the other. Without
        this, a caller that changed the task_id's type between two fires would
        double-emit."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team, occ = "pact-test", occupant_hash("coder", "cross-form subject")
        assert already_emitted(team, 83, occ) is False, "int form claims"  # type: ignore[arg-type]  # int is the point
        assert already_emitted(team, "83", occ) is True, (
            "the str form must resolve to the SAME marker key as the int "
            "form and be suppressed; a False here means the two type forms "
            "derive different keys and the dispatch double-emits"
        )
        marker_dir = (
            tmp_path / ".claude" / "teams" / team / ".agent_handoff_emitted"
        )
        assert [p.name for p in marker_dir.iterdir()] == [f"83-{occ}"], (
            "exactly ONE marker, keyed by the normalised task_id"
        )

    def test_distinct_int_task_ids_still_discriminate(self, tmp_path, monkeypatch):
        """THE ANTI-LOBOTOMY ROW. Coercing to a CONSTANT would also stop the
        raise, so this is what separates a normalisation from a collapse:
        83 and 84 are different dispatches and must NOT share a marker."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team, occ = "pact-test", occupant_hash("coder", "discrimination subject")
        assert already_emitted(team, 83, occ) is False, "83 claims"  # type: ignore[arg-type]  # int is the point
        assert already_emitted(team, 84, occ) is False, (  # type: ignore[arg-type]
            "84 is a DIFFERENT task_id and must win its own marker — a True "
            "here means the coercion collapsed distinct ids onto one key, "
            "which would suppress genuine events"
        )
        marker_dir = (
            tmp_path / ".claude" / "teams" / team / ".agent_handoff_emitted"
        )
        assert sorted(p.name for p in marker_dir.iterdir()) == [
            f"83-{occ}",
            f"84-{occ}",
        ]

    def test_claim_and_rollback_share_the_key_for_an_int_task_id(
        self, tmp_path, monkeypatch
    ):
        """The property the placement buys: already_emitted (the CLAIM) and
        unclaim (the compensating ROLLBACK) both resolve through
        _resolve_marker_target, so normalising there makes it structurally
        impossible for them to derive different keys.

        If the rollback saw a different key it would unlink nothing, the claim
        would stay poisoned, and every later fire for that task would be
        suppressed forever — so the observable is that a re-fire AFTER the
        rollback wins again."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team, occ = "pact-test", occupant_hash("coder", "rollback subject")
        marker_dir = (
            tmp_path / ".claude" / "teams" / team / ".agent_handoff_emitted"
        )

        assert already_emitted(team, 83, occ) is False, "int claim wins"  # type: ignore[arg-type]  # int is the point
        assert [p.name for p in marker_dir.iterdir()] == [f"83-{occ}"]

        unclaim(team, 83, occ)  # type: ignore[arg-type]  # int is the point
        assert list(marker_dir.iterdir()) == [], (
            "the rollback must remove the marker the claim created; a "
            "surviving file means claim and rollback derived DIFFERENT keys "
            "and the marker is permanently poisoned"
        )
        assert already_emitted(team, 83, occ) is False, (  # type: ignore[arg-type]
            "after a successful rollback a later fire must be able to claim "
            "again (this is what the unclaim exists to preserve)"
        )

    def test_unclaim_no_longer_raises_on_an_int_task_id(
        self, tmp_path, monkeypatch
    ):
        """unclaim documents itself as fail-SAFE and 'Never raises', but
        _resolve_marker_target is called OUTSIDE its try — so before the
        coercion an int task_id propagated a TypeError straight through and
        falsified that contract. Pin the repaired contract."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        occ = occupant_hash("coder", "unclaim contract subject")
        unclaim("pact-test", 83, occ)  # type: ignore[arg-type]  # int is the point

    def test_str_task_ids_are_byte_identical_to_the_unnormalised_form(
        self, tmp_path, monkeypatch
    ):
        """EQUIVALENCE CERTIFICATE for the population that already reached the
        resolver. str(x) is x for a str, so every currently-arriving task_id
        derives a byte-identical marker key and the dedup namespace cannot
        fragment — which is the property the shared-seam constraint protects.
        Pinned against a real sample rather than argued from the source."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team = "pact-test"
        occ = occupant_hash("coder", "equivalence subject")
        marker_dir = (
            tmp_path / ".claude" / "teams" / team / ".agent_handoff_emitted"
        )
        for task_id in ("5", "standing-7", "amp", "probe", "a b", "83"):
            assert already_emitted(team, task_id, occ) is False
            assert (marker_dir / f"{task_id}-{occ}").exists(), (
                f"str task_id {task_id!r} must key exactly as it did before "
                "the coercion"
            )

    def test_team_name_is_not_coerced(self, tmp_path, monkeypatch):
        """SCOPE BOUND, pinned. team_name is validated upstream against
        [A-Za-z0-9_-]+ and is additionally gated on being resolvable, so it is
        deliberately NOT coerced here — it still reaches .lower() raw. This
        row records that as an assessed decision; if a later change extends
        the coercion to team_name, this row is the one that should be
        consciously deleted rather than silently satisfied."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        occ = occupant_hash("coder", "team bound subject")
        with pytest.raises(AttributeError):
            already_emitted(7, "5", occ)  # type: ignore[arg-type]  # asserts it RAISES


class TestPartitionDestination:
    """The NEW-FORMAT partition router, extracted so the skill branches on
    exported constants instead of inlining the comparison. One rule, one
    place — the same shape as `old_format_destination`."""

    def test_absent_or_empty_team_is_old_format(self):
        for value in (None, ""):
            assert (
                partition_destination(value, "session-me", ["session-x"])
                == PARTITION_OLD_FORMAT
            )

    def test_this_team(self):
        assert (
            partition_destination("session-me", "session-me", ["session-x"])
            == PARTITION_THIS
        )

    def test_realigned_from_team(self):
        assert (
            partition_destination("session-x", "session-me", ["session-x"])
            == PARTITION_FROM
        )

    def test_unknown_team_is_foreign(self):
        assert (
            partition_destination("session-z", "session-me", ["session-x"])
            == PARTITION_FOREIGN
        )

    def test_the_labels_are_distinct(self):
        """A caller compares against the constants, so two labels must never
        collide."""
        labels = {
            PARTITION_THIS,
            PARTITION_FROM,
            PARTITION_FOREIGN,
            PARTITION_OLD_FORMAT,
        }
        assert len(labels) == 4

    def test_a_non_str_team_is_foreign_even_when_the_list_holds_it(self):
        """HARDENING: `["x"]` matched a non-str element of `from_teams` and
        returned FROM, reopening the unhashable-key path for any caller that
        passes raw journal values. A non-str is never a valid team name."""
        assert (
            partition_destination(["x"], "session-abc", [["x"]])
            == PARTITION_FOREIGN
        )
        # An int is equally invalid, even when the list stringifies to it.
        assert (
            partition_destination(7, "session-abc", ["7"]) == PARTITION_FOREIGN
        )

    def test_non_str_containers_are_foreign_not_absent(self):
        """A falsy non-str is still a non-str: `{}` and `[]` must not slip
        into the absent/empty OLD-FORMAT branch. Only `None` means absent."""
        assert partition_destination({}, "session-abc", []) == PARTITION_FOREIGN
        assert partition_destination([], "session-abc", []) == PARTITION_FOREIGN

    def test_none_still_means_absent_and_is_old_format(self):
        assert (
            partition_destination(None, "session-abc", [])
            == PARTITION_OLD_FORMAT
        )

    def test_string_routes_are_unchanged(self):
        """CONTROL for the guard: every legitimate string route is exactly
        as before. Padded and case-varied names are DIFFERENT names."""
        assert (
            partition_destination("session-me", "session-me", [])
            == PARTITION_THIS
        )
        assert (
            partition_destination("session-x", "session-me", ["session-x"])
            == PARTITION_FROM
        )
        assert (
            partition_destination(" session-me", "session-me", [])
            == PARTITION_FOREIGN
        )
        assert (
            partition_destination("SESSION-ME", "session-me", [])
            == PARTITION_FOREIGN
        )
        assert (
            partition_destination("session-nope", "session-me", ["session-x"])
            == PARTITION_FOREIGN
        )
