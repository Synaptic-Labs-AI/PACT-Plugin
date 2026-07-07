"""Harness-aware bootstrap: config-less Desktop/SDK substrate.

The PACT plugin is built on the interactive-CLI team model (home-dir registry,
``session-<id8>`` naming, an eagerly platform-written ``config.json``). Under the
Claude Desktop / Agent-SDK harness the platform provides NO native team scaffold:
it creates ``teams/<full-uuid>/`` with ``inboxes/`` + ``file-edits.json`` but NO
``config.json`` and NO ``members[]``. That mismatch deadlocks bootstrap two ways:

  * GATE 1 (dispatch deadlock): ``_resolve_aligned_team_name`` identity-match
    keys on ``config.json['leadSessionId']`` — absent under the config-less
    substrate, so it falls back to the minted ``session-<id8>`` while the real
    tasks live under ``tasks/<uuid>/`` -> the secretary spawn is refused with
    ``no Task assigned to owner='secretary'``.
  * GATE 2 (marker deadlock): ``bootstrap_marker_writer._team_has_secretary``
    reads ``members[]`` from ``config.json`` — empty under the config-less
    substrate -> the bootstrap-complete marker is never written ->
    ``Edit``/``Write``/``Agent`` stay blocked for the whole session.

THE FIX (two surgical edits, session-id-anchored, NO harness detection, NO
self-provision; both unreachable under new-CLI so the CLI path is byte-identical):

  * GATE 1 — ``_resolve_aligned_team_name`` branch-2 (``pact_context.py``): after
    the identity-match loop misses, BEFORE ``return fallback``, resolve to the
    running frame's own ``session_id`` IFF
    ``is_safe_path_component(session_id)`` (the FIRST conjunct — short-circuits
    before any Path composition) AND ``teams/<session_id>/`` ``is_dir()`` AND
    (``inboxes/`` ``is_dir()`` OR ``file-edits.json`` exists). POSITIVE,
    session-id-anchored — NOT config-absence (config-absence is shared with the
    CLI ~38s cold-start, so it is not a sound discriminator).
  * GATE 2 — ``_team_has_secretary`` inbox-witness (``bootstrap_marker_writer.py``):
    members[] check FIRST (CLI byte-identical), then fall back to
    ``teams/<team_name>/inboxes/secretary.json`` ``is_file()`` as the config-less
    "secretary joined" witness.

SOUNDNESS — why branch-2 cannot misfire under CLI: new-CLI names the platform
team dir ``session-<id8>``, so ``teams/<full-uuid>/`` NEVER exists (steady-state
AND the ~38s cold-start window). The decisive proof is FIXTURE C below
(``TestBranch2NoMisfireUnderCLI``): the ``is_dir()`` guard on the running frame's
full-UUID session_id is False, so branch-2 returns the default.

WHY THE INBOX-WITNESS IS THE RIGHT GATE-2 SIGNAL (code-ordering): PACT writes no
inbox files; its bootstrap ritual is ``TaskCreate -> TaskUpdate(owner=secretary)
-> Agent(name=secretary)`` with NO pre-spawn ``SendMessage`` to the secretary, so
the platform writes ``inboxes/secretary.json`` only on delivery to an
already-dispatched secretary — it cannot predate the spawn within PACT's
choreography. The residual (could the PLATFORM pre-create an inbox on a bare name
reference, OUTSIDE PACT's ritual?) is closed by the deferred negative live-probe
documented in ``test_deferred_desktop_validation_protocol`` below — NOT by code,
NOT by a CI test (it is platform-runtime-only).

DUAL-MODE × HARNESS: the dual-mode contract (in-process: frame session_id ==
leadSessionId; tmux: != ) is PERMANENT; the harness substrate is a THIRD
orthogonal axis. The tmux-leg non-misfire (``TestTmuxLegNoMisfire``) is
double-protected — (1) the is_lead gate upstream of the marker witness blocks a
teammate frame from reaching it; (2) even if reached, branch-2's ``is_dir`` guard
on ``teams/<teammate-sid>/`` returns default (TS4 ↔ TS12 cross-guard).

NON-MOCKED SEAM DISCIPLINE: ``dispatch_gate``, ``bootstrap_gate``, and
``bootstrap_marker_writer`` are in ``SEAM_DEPENDENT_HOOKS`` — these tests build
the real config-less team dir on disk (under a tmp HOME) and drive the UNSTUBBED
team-resolution seam; the team-resolution seam whose correct resolution IS the
thing under test is never mocked.

NON-VACUITY (counter-test-by-revert, SOURCE-ONLY): each behavioral cell carries a
``# COUNTER-TEST`` annotation naming the source-only revert that turns it RED. The
GATE-1 commit BUNDLES the source edit with a test-hygiene docstring edit to
test_team_name_detect_align.py, so a whole-commit ``git revert -n`` would mask the
cardinality — use SOURCE-ONLY revert of ``pact_context.py`` (GATE 1) /
``bootstrap_marker_writer.py`` (GATE 2), leaving these tests in place. The
measured RED cardinality is documented in this module's HANDOFF.
"""

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from shared import BOOTSTRAP_MARKER_NAME  # noqa: E402


# ── Real-shaped ids + the two divergent dir-naming schemes ────────────────────
# Desktop/SDK names the team dir with the bare 36-char UUID; new-CLI names it
# session-<first8>. The fix resolves the former via branch-2 and leaves the
# latter on the existing identity-match path.
LEAD_UUID = "0001639f-a74f-41c4-bd0b-93d9d206e7f7"   # Desktop full-UUID team dir
LEAD_ID8 = "session-0001639f"                         # new-CLI session-<id8> dir
TMUX_TEAMMATE_SID = "ffff8888-bbbb-4ccc-9ddd-eeeeeeeeeeee"  # a teammate's own sid
_SECRETARY = "secretary"


# ── seeding helpers (config-less Desktop sim + CLI counter-fixtures) ──────────


def _seed_config_less_desktop(teams_root, uuid, *, with_secretary_inbox=True,
                              with_file_edits=True):
    """The config-less Desktop/SDK substrate: teams/<uuid>/ with inboxes/ +
    file-edits.json but NO config.json. Optionally drop the secretary's inbox
    file (the GATE-2 witness)."""
    team_dir = teams_root / uuid
    (team_dir / "inboxes").mkdir(parents=True, exist_ok=True)
    if with_secretary_inbox:
        (team_dir / "inboxes" / f"{_SECRETARY}.json").write_text(
            json.dumps([{"from": "team-lead", "text": "bootstrap"}]),
            encoding="utf-8",
        )
    if with_file_edits:
        (team_dir / "file-edits.json").write_text("{}", encoding="utf-8")
    # Deliberately NO config.json — that is the whole point.
    assert not (team_dir / "config.json").exists()
    return team_dir


def _seed_cli_with_config(teams_root, dir_name, *, lead_session_id, members=()):
    """The CLI substrate: teams/<dir_name>/config.json with leadSessionId +
    members[] (the regression firewall / identity-match path)."""
    team_dir = teams_root / dir_name
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "config.json").write_text(
        json.dumps({"name": dir_name, "leadSessionId": lead_session_id,
                    "members": [{"name": m} for m in members]}),
        encoding="utf-8",
    )
    return team_dir


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    """Fresh pact_context state: home -> tmp_path, caches cleared. Returns
    (module, teams_root). teams_root is the real <home>/.claude/teams so the
    resolver's default teams_dir resolves to it (non-mocked seam)."""
    import shared.pact_context as ctx_module
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ctx_module.reset_for_tests()
    teams_root = tmp_path / ".claude" / "teams"
    teams_root.mkdir(parents=True, exist_ok=True)
    yield ctx_module, teams_root
    ctx_module.reset_for_tests()


# ══════════════════════════════════════════════════════════════════════════════
# GATE 1 — _resolve_aligned_team_name branch-2 (config-less session-id anchor)
# ══════════════════════════════════════════════════════════════════════════════


class TestBranch2ResolvesConfigLess:
    """Branch-2 resolves the config-less full-UUID team to the running frame's
    own session_id. This is the POSITIVE config-less path that clears GATE 1."""

    def test_inboxes_witness_resolves_uuid(self, ctx):
        """config-less teams/<uuid>/ with inboxes/ (no config.json) -> resolves
        to <uuid> via branch-2.

        # COUNTER-TEST (source-only revert of pact_context.py): without branch-2
        # the identity-match misses and the resolver returns the fallback
        # ('minted-fallback'), not <uuid> -> RED.
        """
        ctx_module, teams_root = ctx
        _seed_config_less_desktop(teams_root, LEAD_UUID,
                                  with_secretary_inbox=False)  # inboxes/ alone
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_UUID, teams_dir=str(teams_root), default="minted-fallback"
        )
        assert resolved == LEAD_UUID

    def test_file_edits_witness_resolves_uuid(self, ctx):
        """The OR-arm: file-edits.json present, inboxes/ absent -> still resolves
        via branch-2 (the witness is inboxes/ OR file-edits.json)."""
        ctx_module, teams_root = ctx
        team_dir = teams_root / LEAD_UUID
        team_dir.mkdir(parents=True, exist_ok=True)
        (team_dir / "file-edits.json").write_text("{}", encoding="utf-8")
        # No inboxes/ subdir, no config.json.
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_UUID, teams_dir=str(teams_root), default="minted-fallback"
        )
        assert resolved == LEAD_UUID

    def test_bare_dir_no_witness_returns_default(self, ctx):
        """A teams/<uuid>/ dir with NEITHER inboxes/ NOR file-edits.json is NOT a
        genuine team substrate -> branch-2's witness conjunct is False -> default.
        Proves the witness conjunct is load-bearing (not just the is_dir probe).

        # COUNTER-TEST: deleting the witness conjunct from branch-2 would resolve
        # this bare dir to <uuid> -> this assertion (== default) goes RED.
        """
        ctx_module, teams_root = ctx
        (teams_root / LEAD_UUID).mkdir(parents=True, exist_ok=True)  # bare dir
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_UUID, teams_dir=str(teams_root), default="bare-default"
        )
        assert resolved == "bare-default"

    def test_identity_match_wins_first_over_branch2_distinguishable(self, ctx):
        """Desktop-WITH-config outlier (e.g. f1d72df4): branch-1 identity-match
        MUST run BEFORE branch-2, and this cell PROVES the ORDER by making the
        two branches resolve to DISTINGUISHABLE dirs.

        Setup: the config.json (carrying leadSessionId == session_id) lives on a
        DIFFERENT dir name (LEAD_ID8) than the running-frame session_id
        (LEAD_UUID). So:
          - branch-1 (identity-match scans for config['leadSessionId'] == sid)
            resolves to LEAD_ID8 (the dir whose config matches).
          - branch-2 (anchors on teams/<session_id>/) would resolve to LEAD_UUID
            (the running-frame sid dir, which we ALSO seed with a witness so
            branch-2 COULD fire if it were reached first).
        Correct ordering (identity-match first) -> LEAD_ID8. If branch-2 wrongly
        won, the result would be LEAD_UUID. Asserting == LEAD_ID8 therefore proves
        identity-match fired FIRST — the order the prior fixture (config + witness
        on the SAME dir, both branches -> the same value) could NOT discriminate.

        # COUNTER-TEST: reordering the resolver so branch-2 precedes the
        # identity-match loop would return LEAD_UUID here -> RED.
        """
        ctx_module, teams_root = ctx
        # branch-1's match dir: config.json on LEAD_ID8, leadSessionId == the sid.
        _seed_cli_with_config(teams_root, LEAD_ID8, lead_session_id=LEAD_UUID,
                              members=("secretary",))
        # branch-2's would-be dir: teams/<session_id>/ with a witness, so branch-2
        # COULD fire — the discriminator is which branch the resolver consults.
        (teams_root / LEAD_UUID / "inboxes").mkdir(parents=True, exist_ok=True)
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_UUID, teams_dir=str(teams_root), default="fallback"
        )
        # LEAD_ID8 (identity-match) NOT LEAD_UUID (branch-2) -> order proven.
        assert resolved == LEAD_ID8


class TestBranch2NoMisfireUnderCLI:
    """FIXTURE C — THE load-bearing non-misfire proof. Branch-2 must be
    unreachable under any CLI state because new-CLI names the team dir
    session-<id8>, so teams/<full-uuid>/ never exists."""

    def test_cli_coldstart_no_full_uuid_dir_returns_default(self, ctx):
        """CLI cold-start: the platform created teams/session-<id8>/ (no config
        yet — the ~38s window) and CRUCIALLY NO teams/<full-uuid>/. The running
        frame's session_id is the full UUID. branch-2's
        (teams_root / <full-uuid>).is_dir() is False -> returns default. No
        ~38s cold-start misfire.

        # COUNTER-TEST: this is the cell that proves branch-2 cannot fire under
        # CLI. If branch-2 keyed on config-ABSENCE instead of a positive
        # session-id-anchored is_dir probe, the session-<id8> dir (config-absent
        # in this window) would misfire. The is_dir guard on the full-UUID sid is
        # what makes it safe.
        """
        ctx_module, teams_root = ctx
        # CLI cold-start dir: session-<id8>, NO config.json yet, no full-uuid dir.
        (teams_root / LEAD_ID8).mkdir(parents=True, exist_ok=True)
        (teams_root / LEAD_ID8 / "inboxes").mkdir(exist_ok=True)
        assert not (teams_root / LEAD_UUID).exists()  # the key precondition
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_UUID, teams_dir=str(teams_root), default=LEAD_ID8
        )
        # Running frame is the full UUID; no teams/<full-uuid>/ -> default.
        assert resolved == LEAD_ID8

    def test_cli_steady_state_identity_match_byte_identical(self, ctx):
        """CLI steady-state: teams/session-<id8>/config.json present with
        leadSessionId. Identity-match resolves it; branch-2 never reached.
        Byte-identical to pre-fix behavior."""
        ctx_module, teams_root = ctx
        _seed_cli_with_config(teams_root, LEAD_ID8, lead_session_id=LEAD_UUID,
                              members=("secretary",))
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_UUID, teams_dir=str(teams_root), default="fallback"
        )
        assert resolved == LEAD_ID8


class TestTmuxLegNoMisfire:
    """TS4 — the tmux leg of the dual-mode contract. A teammate frame runs under
    its OWN session_id (!= the lead's). branch-2 keys on the RUNNING frame's
    session_id (get_team_name() passes get_session_id()), so a teammate frame
    whose own sid has no teams/<that-sid>/ dir returns default — it does NOT
    mis-resolve to the lead's dir."""

    def test_teammate_sid_no_own_dir_returns_default(self, ctx):
        """tmux teammate: the LEAD's config-less team dir exists at
        teams/<lead-uuid>/, but the teammate frame's session_id is its OWN
        (different) uuid with no teams/<teammate-sid>/ dir. branch-2 keyed on the
        teammate sid -> is_dir False -> default. No mis-resolve to the lead dir.

        # COUNTER-TEST: if branch-2 keyed on a hardcoded lead sid or scanned for
        # ANY config-less dir (rather than the running frame's own sid), this
        # would mis-resolve to <lead-uuid> -> RED.
        """
        ctx_module, teams_root = ctx
        _seed_config_less_desktop(teams_root, LEAD_UUID)  # the LEAD's team dir
        assert not (teams_root / TMUX_TEAMMATE_SID).exists()
        # The running frame is the TEAMMATE — branch-2 keys on ITS sid.
        resolved = ctx_module._resolve_aligned_team_name(
            TMUX_TEAMMATE_SID, teams_dir=str(teams_root), default="teammate-default"
        )
        assert resolved == "teammate-default"

    def test_inprocess_lead_frame_resolves_own_dir(self, ctx):
        """In-process: the lead frame's session_id IS the team uuid -> branch-2
        resolves to it. The in-process complement of the tmux leg above (both
        legs of the dual-mode contract, structurally keyed on session_id)."""
        ctx_module, teams_root = ctx
        _seed_config_less_desktop(teams_root, LEAD_UUID)
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_UUID, teams_dir=str(teams_root), default="fallback"
        )
        assert resolved == LEAD_UUID


class TestBranch2TraversalSecurity:
    """GATE-1 security: is_safe_path_component(session_id) is the FIRST conjunct
    of branch-2 — a path-unsafe session_id is rejected BEFORE any Path
    composition, EVEN IF the traversed dir exists. The path-safety lives in the
    RESOLVER (GATE 1); _team_has_secretary's team_name is path-safe by
    construction (it is get_team_name() output), so the security cell targets
    the resolver gate."""

    def test_traversal_id_rejected_even_if_dir_exists(self, ctx):
        """A path-unsafe session_id ('../sibling') whose Path composition WOULD
        traverse to an existing, witnessed dir must STILL be rejected by
        is_safe_path_component (the guard-order short-circuit) -> default. Proves
        the guard bites BEFORE the FS probe — the dir existence is a real lure.

        # COUNTER-TEST: moving is_safe_path_component out of the FIRST-conjunct
        # position (or removing it) would let the traversal resolve to the
        # sibling dir -> this assertion (== default) goes RED.
        """
        ctx_module, teams_root = ctx
        # Build a real, witnessed sibling the traversal would land on.
        sibling = teams_root / "sibling"
        (sibling / "inboxes").mkdir(parents=True, exist_ok=True)
        # The traversal target teams_root/'../teams/sibling' resolves to sibling.
        poisoned = "../teams/sibling"
        resolved = ctx_module._resolve_aligned_team_name(
            poisoned, teams_dir=str(teams_root), default="safe-default"
        )
        assert resolved == "safe-default"

    @pytest.mark.parametrize("poisoned", ["../escape", "a/b", "..", "\x00nul", ""])
    def test_path_unsafe_ids_never_raise_return_default(self, ctx, poisoned):
        """Adversarial session_ids (traversal, NUL, embedded slash, empty) are
        gated out of branch-2 and never raise -> default. Pins the never-raises
        contract across the branch-2 path-composition surface."""
        ctx_module, teams_root = ctx
        resolved = ctx_module._resolve_aligned_team_name(
            poisoned, teams_dir=str(teams_root), default="safe-default"
        )
        assert resolved == "safe-default"


# ══════════════════════════════════════════════════════════════════════════════
# GATE 2 — bootstrap_marker_writer inbox-witness (config-less secretary signal)
# ══════════════════════════════════════════════════════════════════════════════


class TestInboxWitnessGate2:
    """_team_has_secretary accepts teams/<team>/inboxes/secretary.json as the
    config-less 'secretary joined' witness, with members[] checked FIRST."""

    _TEAM = "0001639f-a74f-41c4-bd0b-93d9d206e7f7"  # config-less full-uuid team

    def _has_secretary(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from bootstrap_marker_writer import _team_has_secretary
        return _team_has_secretary(self._TEAM)

    def test_inbox_witness_passes_config_less(self, monkeypatch, tmp_path):
        """Config-less team (NO config.json) + inboxes/secretary.json present ->
        _team_has_secretary True via the inbox fallback.

        # COUNTER-TEST (source-only revert of bootstrap_marker_writer.py): without
        # the inbox fallback, members[] is empty (no config.json) -> False -> RED
        # (the marker would never write — the original deadlock).
        """
        teams_root = tmp_path / ".claude" / "teams"
        _seed_config_less_desktop(teams_root, self._TEAM)  # writes secretary.json
        assert self._has_secretary(monkeypatch, tmp_path) is True

    def test_config_less_without_secretary_inbox_is_false(self, monkeypatch,
                                                          tmp_path):
        """Config-less team, inboxes/ present but NO secretary.json -> False. The
        witness is specifically the SECRETARY's inbox, not any inbox dir.

        # COUNTER-TEST: a witness that accepted the inboxes/ DIR (rather than the
        # secretary.json FILE) would pass here -> this assertion (is False) RED.
        """
        teams_root = tmp_path / ".claude" / "teams"
        _seed_config_less_desktop(teams_root, self._TEAM,
                                  with_secretary_inbox=False)
        assert self._has_secretary(monkeypatch, tmp_path) is False

    def test_members_path_unchanged_cli(self, monkeypatch, tmp_path):
        """CLI byte-identical: config.json with members[] containing secretary ->
        True via the members[] arm (tried FIRST; the inbox arm is never reached).
        No inbox file is seeded, so a True here can only come from members[]."""
        teams_root = tmp_path / ".claude" / "teams"
        _seed_cli_with_config(teams_root, self._TEAM, lead_session_id=self._TEAM,
                              members=("secretary",))
        assert not (teams_root / self._TEAM / "inboxes").exists()
        assert self._has_secretary(monkeypatch, tmp_path) is True

    def test_no_witness_no_members_is_false(self, monkeypatch, tmp_path):
        """Neither members[] secretary NOR the inbox file -> False (fail-safe)."""
        teams_root = tmp_path / ".claude" / "teams"
        _seed_cli_with_config(teams_root, self._TEAM, lead_session_id=self._TEAM,
                              members=("backend-coder",))  # no secretary member
        assert self._has_secretary(monkeypatch, tmp_path) is False

    def test_inbox_isfile_raises_fails_safe_false(self, monkeypatch, tmp_path):
        """GATE-2 fail-safe arm (review FUTURE-1): an UNEXPECTED FS error from the
        inbox is_file() probe is swallowed by the `except Exception: return False`
        wrap, so _team_has_secretary degrades to the silent fail-safe False rather
        than propagating. Exercises the except arm that no other cell reached.

        Non-vacuity: the fixture seeds the secretary inbox so WITHOUT the raise the
        inbox arm would return True (proven by test_inbox_witness_passes_config_less
        on the identical fixture). With is_file monkeypatched to raise OSError, the
        result flips to False — attributable to the except arm specifically.

        # COUNTER-TEST: removing the try/except wrap would let the OSError
        # PROPAGATE out of _team_has_secretary -> this call would raise instead of
        # returning False -> RED (pytest.raises would be needed), proving the arm
        # is load-bearing.
        """
        teams_root = tmp_path / ".claude" / "teams"
        _seed_config_less_desktop(teams_root, self._TEAM)  # secretary.json present
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Force the inbox probe to raise an FS-family error (e.g. a stat failure
        # on a pathological filesystem). The members[] arm finds nothing first
        # (no config.json), so control reaches the inbox try-block.
        def _raise(self):
            raise OSError("simulated FS failure on is_file()")
        monkeypatch.setattr(Path, "is_file", _raise)
        from bootstrap_marker_writer import _team_has_secretary
        # Must NOT raise; must degrade to the fail-safe False.
        assert _team_has_secretary(self._TEAM) is False


# ══════════════════════════════════════════════════════════════════════════════
# Writability invariant (TS12) — non-lead frame cannot poison the marker
# ══════════════════════════════════════════════════════════════════════════════


class TestWritabilityInvariant:
    """The is_lead gate (bootstrap_marker_writer:471) sits UPSTREAM of the
    secretary witness — a non-lead frame returns before reaching it, so a
    teammate frame can never write the marker even when the inbox-witness is
    present. The second, independent layer of the tmux non-misfire protection
    (TS4 ↔ TS12 cross-guard)."""

    _SLUG = "project"
    _SESSION_ID = "0001639f-a74f-41c4-bd0b-93d9d206e7f7"  # config-less full-uuid
    _PLUGIN_VERSION = "9.9.9"

    def _setup_config_less_session(self, monkeypatch, tmp_path):
        import shared.pact_context as ctx_module
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        session_dir = (tmp_path / ".claude" / "pact-sessions" / self._SLUG
                       / self._SESSION_ID)
        session_dir.mkdir(parents=True, exist_ok=True)
        plugin_root = tmp_path / "plugin"
        (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (plugin_root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"version": self._PLUGIN_VERSION}), encoding="utf-8")
        context_file = session_dir / "pact-session-context.json"
        context_file.write_text(json.dumps({
            "team_name": self._SESSION_ID, "session_id": self._SESSION_ID,
            "project_dir": f"/test/{self._SLUG}", "plugin_root": str(plugin_root),
            "started_at": "2026-01-01T00:00:00Z",
        }), encoding="utf-8")
        monkeypatch.setattr(ctx_module, "_context_path", context_file)
        monkeypatch.setattr(ctx_module, "_cache", None)
        monkeypatch.setattr(ctx_module, "_aligned_cache", None)
        # The config-less substrate WITH the secretary inbox-witness present.
        teams_root = tmp_path / ".claude" / "teams"
        _seed_config_less_desktop(teams_root, self._SESSION_ID)
        return session_dir

    def _run(self, input_data, capsys):
        from bootstrap_marker_writer import main
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit):
                main()
        out = capsys.readouterr().out.strip()
        return json.loads(out) if out else {}

    def test_lead_frame_writes_marker_via_inbox_witness(self, monkeypatch,
                                                        tmp_path, capsys):
        """End-to-end positive: a LEAD frame over the config-less substrate with
        the inbox-witness present -> the marker is written (GATE 1 resolves the
        team, GATE 2 passes via inbox-witness). The deadlock is cleared.

        # COUNTER-TEST: source-only revert of EITHER gate breaks this -> the
        # marker is absent -> RED.
        """
        session_dir = self._setup_config_less_session(monkeypatch, tmp_path)
        self._run({"hook_event_name": "UserPromptSubmit",
                   "session_id": self._SESSION_ID, "prompt": "hi",
                   "source": "startup", "agent_type": "pact-orchestrator"},
                  capsys)
        assert (session_dir / BOOTSTRAP_MARKER_NAME).exists()

    def test_non_lead_frame_does_not_write_marker(self, monkeypatch, tmp_path,
                                                  capsys):
        """A NON-LEAD (teammate) frame over the SAME config-less substrate WITH
        the inbox-witness present does NOT write the marker — is_lead is upstream
        of the witness. Proves a teammate frame cannot poison the marker.

        # COUNTER-TEST: removing the is_lead gate would let this teammate frame
        # write the marker -> this assertion (not exists) RED.
        """
        session_dir = self._setup_config_less_session(monkeypatch, tmp_path)
        self._run({"hook_event_name": "UserPromptSubmit",
                   "session_id": self._SESSION_ID, "prompt": "hi",
                   "source": "startup", "agent_type": "pact-backend-coder"},
                  capsys)
        assert not (session_dir / BOOTSTRAP_MARKER_NAME).exists()

    @pytest.mark.parametrize(
        "agent_type, marker_expected",
        [
            ("pact-orchestrator", True),    # lead -> is_lead True -> marker writes
            ("pact-backend-coder", False),  # teammate -> is_lead False -> no marker
        ],
    )
    def test_is_lead_is_sole_cause_of_marker_write(self, monkeypatch, tmp_path,
                                                   capsys, agent_type,
                                                   marker_expected):
        """SAME-FIXTURE A/B isolating agent_type as the SOLE differing cause
        (review remediation of the GATE-2 attribution question). Both arms use
        the IDENTICAL _setup_config_less_session and the IDENTICAL input EXCEPT
        agent_type. The marker presence flips exactly with is_lead-ness, so the
        non-lead marker-ABSENCE is attributable to the is_lead gate SPECIFICALLY
        — not to any unrelated precondition (plugin_version, session_dir,
        witness), which are held identical across both arms and which the
        lead arm's PASS proves are satisfied.

        # COUNTER-TEST: removing the is_lead gate makes the False arm write the
        # marker -> RED. Source-only revert of either GATE breaks the True arm.
        """
        session_dir = self._setup_config_less_session(monkeypatch, tmp_path)
        self._run({"hook_event_name": "UserPromptSubmit",
                   "session_id": self._SESSION_ID, "prompt": "hi",
                   "source": "startup", "agent_type": agent_type},
                  capsys)
        assert (session_dir / BOOTSTRAP_MARKER_NAME).exists() is marker_expected


# ══════════════════════════════════════════════════════════════════════════════
# SDK harness coverage (TS15) — by-construction note + cheap parametrization
# ══════════════════════════════════════════════════════════════════════════════


class TestSdkHarnessByConstruction:
    """The fix is HARNESS-AGNOSTIC: it never detects "Desktop" vs "sdk-cli" vs
    "sdk-py" — it anchors on the session_id substrate shape. So the sdk-* harness
    is covered BY CONSTRUCTION: any harness that produces the same config-less
    teams/<uuid>/ + inboxes/ substrate is resolved identically. The corpus shows
    no SDK session reached team-spawn, so this is documented (not a deadlock we
    can reproduce); the parametrization pins the by-construction equivalence
    cheaply over the substrate-producing harnesses."""

    @pytest.mark.parametrize("harness", ["claude-desktop", "sdk-cli", "sdk-py"])
    def test_same_substrate_resolves_identically(self, ctx, harness):
        """Whatever harness produced it, an identical config-less full-UUID
        substrate resolves to <uuid> via branch-2. The harness label is inert —
        the resolver reads only the on-disk shape."""
        ctx_module, teams_root = ctx
        _seed_config_less_desktop(teams_root, LEAD_UUID)
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_UUID, teams_dir=str(teams_root), default="fallback"
        )
        assert resolved == LEAD_UUID


# ══════════════════════════════════════════════════════════════════════════════
# Deferred validation (POST-MERGE, NOT a CI gate) — documented, not runnable
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.skip(
    reason="POST-MERGE deferred manual Desktop validation — documentation only; "
    "this CLI/CI substrate cannot exercise the real Desktop harness. Skipped "
    "(not passed) so a doc carrier never inflates the green coverage count."
)
def test_deferred_desktop_validation_protocol():
    """POST-MERGE DEFERRED VALIDATION (documentation cell — asserts nothing
    runtime; it carries the protocol the user runs in a REAL Desktop session,
    which this CLI/CI substrate cannot exercise). SKIPPED, not passed, so this
    documentation carrier does not count toward the green total (review MINOR-2).

    Fixtures here CAN prove: branch-2 resolves the config-less <uuid>; it does
    NOT misfire under CLI cold-start (FIXTURE C) or tmux (TS4); identity-match
    still wins for the with-config outlier; the inbox-witness passes GATE 2
    config-less while the members[] path stays byte-identical; the marker writes
    end-to-end and only from a lead frame (TS12); traversal ids are rejected.

    Fixtures CANNOT prove (platform-runtime-only, hence this deferred protocol):
      (1) that the REAL platform never pre-creates teams/<sid>/inboxes/<name>.json
          on a bare reference to a NEVER-spawned name (the inbox-witness
          false-positive vector) — closed by the NEGATIVE LIVE-PROBE below;
      (2) that the real Desktop substrate shape matches the simulated
          teams/<uuid>/{inboxes/secretary.json,file-edits.json} + tasks/<uuid>/
          layout — closed by manual STEP 1.

    ── NEGATIVE LIVE-PROBE (closes the inbox-precreation vector) ──
    In a throwaway session, reference / SendMessage a SYNTHETIC name that is
    NEVER Agent-spawned (use 'ghost-sec', NOT 'secretary'), then:
        sid=<live session uuid>
        ls ~/.claude/teams/$sid/inboxes/ | sort > /tmp/inbox_before.txt
        #   ... SendMessage to to="ghost-sec" in the session, then return ...
        ls ~/.claude/teams/$sid/inboxes/ | sort > /tmp/inbox_after.txt
        diff /tmp/inbox_before.txt /tmp/inbox_after.txt \
          && echo 'NO ghost-sec.json — witness SOUND' \
          || echo 'ghost-sec.json APPEARED — harden GATE 2 to require '\
                  'tasks/<team>/1.json status!=pending corroboration'
    Absent -> the inbox-witness is sound (inbox-create is spawn/delivery-gated).
    Present -> harden GATE 2. The platform may also REJECT an unknown recipient
    outright (no inbox, error) — that ALSO confirms soundness. Non-blocking.

    ── 7-STEP MANUAL DESKTOP PROTOCOL (Bash-observable; non-gated under deadlock) ──
    Pre: install the merged plugin; launch PACT in Claude Desktop; run bootstrap.
    STEP 0  capture the live UUID + persisted team_name from
            ~/.claude/pact-sessions/*/*/pact-session-context.json.
    STEP 1  confirm config-less substrate shape: ls -la ~/.claude/teams/<uuid>/
            (expect inboxes/ + file-edits.json, NO config.json);
            ls ~/.claude/teams/<uuid>/inboxes/ (expect secretary.json);
            ls ~/.claude/tasks/<uuid>/ (expect 1.json).
    STEP 2  GATE 1 cleared: the secretary spawns WITHOUT the
            "no Task assigned to owner='secretary'" deny; cat
            ~/.claude/tasks/<uuid>/1.json -> status advanced past 'pending'.
    STEP 3  GATE 2 cleared: ls -la ~/.claude/pact-sessions/*/<uuid>/ shows the
            bootstrap-complete marker file.
    STEP 4  Edit/Write/Agent UNBLOCKED: a trivial Edit/Write SUCCEEDS in-session
            (no 'bootstrap required' deny). End-to-end deadlock-clear signal.
    STEP 5  harvest NON-EMPTY: read_events_from(<session_dir>, 'agent_handoff')
            returns events (the silent-handoff-loss symptom is cleared).
    STEP 6  run the NEGATIVE LIVE-PROBE above once.
    CRUX gates: STEP 2 + STEP 3 + STEP 4. Report each PASS/FAIL.
    """
    pytest.fail("unreachable — this cell is @pytest.mark.skip (documentation only)")
