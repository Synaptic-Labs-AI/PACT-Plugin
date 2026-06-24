"""Comprehensive detect-and-align matrix for get_team_name() / #989.

session_init persists the COMPUTED team name (``generate_team_name`` ->
``session-<id8>``) at SessionStart, but in divergent launch contexts (Desktop
child / print / rename-skip) the platform names the real team dir with the FULL
36-char session UUID instead. ``_resolve_aligned_team_name`` finds the dir that
ACTUALLY belongs to this session by IDENTITY MATCH (``config.json['leadSessionId']
== session_id``) so ``get_team_name`` returns the namespace the tasks really live
under. ``get_team_name`` is a PURE READER that short-circuits to "" on an EMPTY
persisted SSOT (the deliberate Option-B fail-closed security gate) and only runs
identity-match on a NON-EMPTY SSOT.

This file owns the #989 unit/integration matrix:

  * _resolve_aligned_team_name — identity-match, full-UUID dirs, fail-safe
    totality (never-raises), is_safe_path_component skip, half-formed window.
  * get_team_name — Option-B empty-SSOT fail-closed short-circuit, resume-revert
    upgrade, per-process _aligned_cache memoization + reset_for_tests isolation.
  * heal_context_if_missing — crash-recovery: context-ABSENT + real-dir-present
    -> writes the ALIGNED name (converges in one prompt).
  * session_init convergence — persists the ALIGNED name to kill oscillation.
  * bootstrap_marker_writer write-back — two-file context-first ordering +
    CLAUDE.md-absent skip (no create in a worktree).
  * FULL-UUID caller sweep — the ~15 get_team_name callers accept a 36-char
    return with no fixed-width / "session-[a-f0-9]{8}" regex narrowing.

The DUAL-MODE PERMANENT CONTRACT: every behavioral test that depends on the
running frame's topology covers BOTH in-process (session_id == leadSessionId)
AND tmux (session_id != leadSessionId). The both-modes DISPATCH-GATE legs live
in test_team_name_resolution_both_modes.py (the standing merge gate); this file
adds the both-modes RESOLVER legs.

Non-vacuity: every behavioral assertion seeds a matchable store so the assertion
BITES. The resolver-level non-vacuity is proven structurally — the identity match
returns the FULL-UUID dir that a name-prefix resolver could never produce, and the
empty-SSOT short-circuit returns "" even when a matchable dir IS present (so the
'' is attributable to the guard, not to a missing dir).
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# ── Two real-shaped session ids + the divergent dir-naming schemes ────────────
# The LEAD's id keys the team. A tmux teammate runs under a DISTINCT id so the
# two topologies are structurally different (== vs != leadSessionId).
LEAD_SID = "0001639f-a74f-41c4-bd0b-93d9d206e7f7"
TMUX_SID = "ffff8888-bbbb-4ccc-9ddd-eeeeeeeeeeee"

# The three FIRST-CLASS dir-naming schemes the resolver must handle launcher-
# agnostically (there is deliberately NO dir-name-prefix shortcut):
FULL_UUID_DIR = LEAD_SID                # Desktop 2.1.177 child: bare 36-char UUID
SESSION_ID8_DIR = "session-0001639f"   # 2.1.178+ CLI: session-<first8>
PACT_ID8_DIR = "pact-0001639f"         # legacy PACT-minted (still first-class)


# ── seeding helpers ───────────────────────────────────────────────────────────


def _seed_team_dir(teams_root, dir_name, *, lead_session_id, with_config=True,
                   with_inboxes=False):
    """Create teams/<dir_name>/ optionally with config.json (carrying
    leadSessionId) and/or an inboxes/ subdir (the half-formed-window signal)."""
    team_dir = teams_root / dir_name
    team_dir.mkdir(parents=True, exist_ok=True)
    if with_config:
        (team_dir / "config.json").write_text(
            json.dumps({"name": dir_name, "leadSessionId": lead_session_id,
                        "members": []}),
            encoding="utf-8",
        )
    if with_inboxes:
        (team_dir / "inboxes").mkdir(exist_ok=True)
    return team_dir


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    """Fresh pact_context module state for each test: home -> tmp_path, caches
    cleared. Returns (module, teams_root). teams_root is the real
    <home>/.claude/teams so the resolver's default teams_dir resolves to it."""
    import shared.pact_context as ctx_module
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ctx_module.reset_for_tests()
    teams_root = tmp_path / ".claude" / "teams"
    teams_root.mkdir(parents=True, exist_ok=True)
    yield ctx_module, teams_root
    ctx_module.reset_for_tests()


def _write_context_file(monkeypatch, ctx_module, tmp_path, *, team_name,
                        session_id):
    """Persist the pact-session-context.json the reader treats as the SSOT and
    point the module's _context_path at it. Clears caches."""
    ctx_path = tmp_path / "pact-session-context.json"
    ctx_path.write_text(
        json.dumps({
            "team_name": team_name,
            "session_id": session_id,
            "project_dir": str(tmp_path / "project"),
            "plugin_root": str(tmp_path / "plugin"),
            "started_at": "2026-01-01T00:00:00Z",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(ctx_module, "_context_path", ctx_path)
    monkeypatch.setattr(ctx_module, "_cache", None)
    monkeypatch.setattr(ctx_module, "_aligned_cache", None)
    return ctx_path


# ══════════════════════════════════════════════════════════════════════════════
# 1. _resolve_aligned_team_name — IDENTITY MATCH (the core predicate)
# ══════════════════════════════════════════════════════════════════════════════


class TestResolverIdentityMatch:
    """The identity-match predicate: config.json['leadSessionId'] == session_id."""

    def test_full_uuid_dir_identity_matches(self, ctx):
        """SCENARIO 2 — FULL-UUID team-dir fixture (the live divergent case): a
        dir named with the bare 36-char UUID is resolved by identity match. NO
        dir-name-prefix assumption — the resolver finds it purely via leadSessionId."""
        ctx_module, teams_root = ctx
        _seed_team_dir(teams_root, FULL_UUID_DIR, lead_session_id=LEAD_SID)
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default="fallback"
        )
        # The resolver lands on the full-UUID dir — a name-prefix resolver
        # ('session-' / 'pact-') could NEVER produce this (non-vacuity).
        assert resolved == FULL_UUID_DIR

    def test_session_id8_dir_identity_matches(self, ctx):
        """The 2.1.178+ CLI shape resolves identically — launcher-agnostic."""
        ctx_module, teams_root = ctx
        _seed_team_dir(teams_root, SESSION_ID8_DIR, lead_session_id=LEAD_SID)
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default="fallback"
        )
        assert resolved == SESSION_ID8_DIR

    def test_pact_id8_dir_identity_matches(self, ctx):
        """The legacy PACT-minted shape is still first-class via identity match."""
        ctx_module, teams_root = ctx
        _seed_team_dir(teams_root, PACT_ID8_DIR, lead_session_id=LEAD_SID)
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default="fallback"
        )
        assert resolved == PACT_ID8_DIR

    def test_foreign_leadsessionid_is_rejected_collision_proof(self, ctx):
        """COLLISION-PROOF: a dir whose config.leadSessionId belongs to ANOTHER
        session is NOT matched, even if its id8 prefix collides. Falls back."""
        ctx_module, teams_root = ctx
        # A stale/foreign dir keyed on a DIFFERENT lead session id.
        _seed_team_dir(teams_root, SESSION_ID8_DIR, lead_session_id=TMUX_SID)
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default="fallback"
        )
        # No dir matches LEAD_SID -> the fail-safe default, NOT the foreign dir.
        assert resolved == "fallback"

    def test_no_config_json_dir_is_rejected(self, ctx):
        """A PACT-artifact dir carrying only file-edits.json (no config.json) —
        e.g. a stale session dir — is cleanly rejected by identity match."""
        ctx_module, teams_root = ctx
        stale = teams_root / "session-deadbeef"
        stale.mkdir(parents=True)
        (stale / "file-edits.json").write_text("{}", encoding="utf-8")
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default="fallback"
        )
        assert resolved == "fallback"

    def test_correct_dir_selected_among_distractors(self, ctx):
        """Non-vacuity: with several sibling dirs present (foreign + half-formed +
        the real one), the resolver selects ONLY the identity-matched real one."""
        ctx_module, teams_root = ctx
        _seed_team_dir(teams_root, "session-aaaaaaaa", lead_session_id=TMUX_SID)
        _seed_team_dir(teams_root, "session-bbbbbbbb", lead_session_id="other-sid")
        _seed_team_dir(teams_root, FULL_UUID_DIR, lead_session_id=LEAD_SID)
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default="fallback"
        )
        assert resolved == FULL_UUID_DIR


# ══════════════════════════════════════════════════════════════════════════════
# 7. HALF-FORMED window — dir + inboxes/ present but config.json absent
# ══════════════════════════════════════════════════════════════════════════════


class TestHalfFormedWindow:
    def test_half_formed_dir_no_config_returns_default(self, ctx):
        """SCENARIO 7 — the team dir exists and inboxes/ is present but config.json
        has NOT yet landed (the ~38s birth window / 2.1.177-Desktop half-formed
        team). No leadSessionId to match -> return the persisted default, NOT a
        wrong dir. Self-heals on the next per-process probe once config lands."""
        ctx_module, teams_root = ctx
        _seed_team_dir(teams_root, SESSION_ID8_DIR, lead_session_id=None,
                       with_config=False, with_inboxes=True)
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default="session-0001639f"
        )
        assert resolved == "session-0001639f"

    def test_half_formed_then_config_lands_resolves(self, ctx):
        """Once config.json lands (a later per-process probe), the same inputs now
        identity-match -> the resolver UPGRADES from default to the real dir."""
        ctx_module, teams_root = ctx
        team_dir = _seed_team_dir(teams_root, SESSION_ID8_DIR, lead_session_id=None,
                                  with_config=False, with_inboxes=True)
        # config.json lands.
        (team_dir / "config.json").write_text(
            json.dumps({"name": SESSION_ID8_DIR, "leadSessionId": LEAD_SID}),
            encoding="utf-8",
        )
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default="placeholder"
        )
        assert resolved == SESSION_ID8_DIR


# ══════════════════════════════════════════════════════════════════════════════
# 8. FAIL-SAFE TOTALITY — _resolve_aligned_team_name NEVER raises
# ══════════════════════════════════════════════════════════════════════════════


class TestFailSafeTotality:
    """The resolver NEVER raises. Two distinct mechanisms guarantee this:

      * A poisoned (path-unsafe) raw session_id — NUL byte or '/' — is NOT an
        uncaught raise source. In the identity-match loop it is only
        STRING-COMPARED against each config.json's leadSessionId. In the
        branch-2 fallthrough it WOULD be composed into a Path
        (``teams_root / session_id``), but ``is_safe_path_component(session_id)``
        is the FIRST conjunct there and rejects the poisoned id BEFORE any Path
        composition — so the poisoned id never matches an identity dir and is
        gated out of branch-2 -> default (the two
        ``..._poisoned_session_id_no_match...`` tests below).
      * The genuine raise sources — a non-str teams_dir (TypeError), a
        HOME-unresolvable home (RuntimeError), and a per-entry config.json
        that is-a-dir/unreadable (OSError/JSONDecodeError) — ARE caught (by the
        outer bare `except Exception` for the first two, by the inner typed
        except for the per-entry read). NO exception escapes."""

    def test_nul_poisoned_session_id_no_match_returns_default(self, ctx):
        """A NUL byte in the raw session_id does NOT raise. In the identity
        loop it is only string-compared to leadSessionId; in the branch-2
        fallthrough is_safe_path_component(session_id) rejects it BEFORE the
        teams_root / session_id composition. So the poisoned id cannot match the
        seeded (different-leadSessionId) dir AND is gated out of branch-2 ->
        NO-MATCH -> default. NON-VACUITY: a real matchable dir is seeded under a
        DIFFERENT leadSessionId, so the default is returned because the
        string-compare ran and missed, not because the store is empty."""
        ctx_module, teams_root = ctx
        # Seed a real, well-formed dir whose leadSessionId is NOT the poisoned id.
        _seed_team_dir(teams_root, SESSION_ID8_DIR, lead_session_id=LEAD_SID)
        resolved = ctx_module._resolve_aligned_team_name(
            "bad\x00id", teams_dir=str(teams_root), default="safe-default"
        )
        # The poisoned id never equals LEAD_SID -> no match; branch-2 then gates
        # it out via is_safe_path_component before any path compose -> default.
        # The well-formed dir IS scanned (the assertion bites: a resolver that
        # path-composed the session_id and raised would ALSO return the default,
        # so we additionally pin that the SAME dir DOES match its OWN id below.)
        assert resolved == "safe-default"
        # Counter-proof the dir is genuinely matchable (so the no-match above is
        # attributable to the poisoned id, not an unscannable store):
        assert ctx_module._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default="safe-default"
        ) == SESSION_ID8_DIR

    def test_slash_poisoned_session_id_no_match_returns_default(self, ctx):
        """A '/' (traversal) in the raw session_id must not traverse OR raise: it
        is string-compared in the identity loop and rejected by
        is_safe_path_component (the FIRST branch-2 conjunct) before the
        teams_root / session_id compose, so it never matches AND never traverses
        -> default. NON-VACUITY: same as above — a real matchable dir under a
        DIFFERENT leadSessionId is seeded, so the default is the gated miss."""
        ctx_module, teams_root = ctx
        _seed_team_dir(teams_root, SESSION_ID8_DIR, lead_session_id=LEAD_SID)
        resolved = ctx_module._resolve_aligned_team_name(
            "../../etc", teams_dir=str(teams_root), default="safe-default"
        )
        assert resolved == "safe-default"
        assert ctx_module._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default="safe-default"
        ) == SESSION_ID8_DIR

    def test_non_str_teams_dir_does_not_raise(self, ctx):
        """teams_dir of a non-str type (e.g. an int) raises TypeError when
        composed via Path() — caught by the bare except -> default."""
        ctx_module, _teams_root = ctx
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_SID, teams_dir=12345, default="safe-default"  # type: ignore[arg-type]
        )
        assert resolved == "safe-default"

    def test_home_unresolvable_does_not_raise(self, ctx, monkeypatch):
        """get_claude_config_dir() -> Path.home() can raise RuntimeError when HOME
        is unresolvable; with teams_dir=None the resolver composes via home, so a
        RuntimeError there must be caught -> default."""
        ctx_module, _teams_root = ctx

        def _boom():
            raise RuntimeError("no home")

        monkeypatch.setattr(Path, "home", _boom)
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_SID, teams_dir=None, default="safe-default"
        )
        assert resolved == "safe-default"

    def test_config_json_is_a_directory_skips_entry(self, ctx):
        """A sibling whose config.json is itself a DIRECTORY (read_text raises
        IsADirectoryError/OSError) must be skipped, not abort the scan — the real
        dir later in the sorted order is still found."""
        ctx_module, teams_root = ctx
        # Sorts BEFORE the real dir ('a...' < 'session-...' < full-uuid '0...').
        # Use a name that sorts first to prove the scan CONTINUES past the bad one.
        bad = teams_root / "aaaa-corrupt"
        bad.mkdir(parents=True)
        (bad / "config.json").mkdir()  # config.json is a DIR -> read_text raises
        _seed_team_dir(teams_root, SESSION_ID8_DIR, lead_session_id=LEAD_SID)
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default="fallback"
        )
        # The bad sibling did not abort the scan; the real dir is still resolved.
        assert resolved == SESSION_ID8_DIR

    def test_unreadable_config_json_skips_entry(self, ctx):
        """A sibling with malformed (non-JSON) config.json is skipped; the real
        dir is still resolved."""
        ctx_module, teams_root = ctx
        bad = teams_root / "aaaa-malformed"
        bad.mkdir(parents=True)
        (bad / "config.json").write_text("{not json", encoding="utf-8")
        _seed_team_dir(teams_root, FULL_UUID_DIR, lead_session_id=LEAD_SID)
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default="fallback"
        )
        assert resolved == FULL_UUID_DIR


# ══════════════════════════════════════════════════════════════════════════════
# 9. is_safe_path_component — a matched dir with a path-unsafe name is SKIPPED
# 10. empty session_id -> no match -> default
# ══════════════════════════════════════════════════════════════════════════════


class TestPathSafetyAndEmptyId:
    def test_path_unsafe_matched_dir_name_is_skipped(self, ctx, monkeypatch):
        """SCENARIO 9 — a dir whose config.leadSessionId MATCHES but whose own
        NAME is path-unsafe (e.g. contains a '.' traversal char that
        is_safe_path_component rejects) must be SKIPPED, not returned. A tampered
        config could name a path-unsafe dir; the resolver path-safety-checks the
        raw matched name BEFORE returning it."""
        ctx_module, teams_root = ctx
        # is_safe_path_component allows only [A-Za-z0-9_-]+, so a '.' is unsafe.
        # We can't easily create a '..'-named dir, so simulate a matched-but-unsafe
        # name by monkeypatching is_safe_path_component to reject the matched name.
        import shared.pact_context as pc
        _seed_team_dir(teams_root, SESSION_ID8_DIR, lead_session_id=LEAD_SID)

        real_guard = pc.is_safe_path_component
        monkeypatch.setattr(
            pc, "is_safe_path_component",
            lambda name: False if name == SESSION_ID8_DIR else real_guard(name),
        )
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default="fallback"
        )
        # Matched dir name is path-unsafe -> skipped -> fall back.
        assert resolved == "fallback"

    def test_path_unsafe_name_dir_on_disk_skipped(self, ctx):
        """A real on-disk dir whose NAME contains a '.' (path-unsafe per
        is_safe_path_component) but which identity-matches is skipped."""
        ctx_module, teams_root = ctx
        _seed_team_dir(teams_root, "team.with.dots", lead_session_id=LEAD_SID)
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default="fallback"
        )
        assert resolved == "fallback"

    def test_empty_session_id_returns_default(self, ctx):
        """SCENARIO 10 — an empty session_id can never identity-match (no value to
        compare) -> returns the default without scanning."""
        ctx_module, teams_root = ctx
        _seed_team_dir(teams_root, SESSION_ID8_DIR, lead_session_id="")
        resolved = ctx_module._resolve_aligned_team_name(
            "", teams_dir=str(teams_root), default="fallback"
        )
        assert resolved == "fallback"


# ══════════════════════════════════════════════════════════════════════════════
# 4. OPTION B — EMPTY-SSOT FAIL-CLOSED (the security gate) — get_team_name
# 3. RESUME-REVERT / divergence UPGRADE — get_team_name
# 11. reset_for_tests clears _aligned_cache (cross-test isolation)
# ══════════════════════════════════════════════════════════════════════════════


class TestGetTeamNameOptionB:
    """get_team_name reads the persisted SSOT FIRST; EMPTY -> '' WITHOUT
    identity-match (fail-closed); NON-EMPTY -> identity-match upgrade."""

    def test_empty_ssot_fails_closed_even_with_matching_dir(self, ctx, monkeypatch,
                                                            tmp_path):
        """SCENARIO 4 — the Option-B security gate. EMPTY persisted SSOT +
        a real identity-matching dir present -> get_team_name returns '' (does NOT
        recover via identity-match). NON-VACUITY: a matchable dir IS seeded, so the
        '' is attributable to the fail-closed short-circuit, not a missing dir."""
        ctx_module, teams_root = ctx
        _seed_team_dir(teams_root, FULL_UUID_DIR, lead_session_id=LEAD_SID)
        _write_context_file(monkeypatch, ctx_module, tmp_path,
                            team_name="", session_id=LEAD_SID)
        assert ctx_module.get_team_name() == ""

    def test_empty_ssot_short_circuit_memoizes_empty(self, ctx, monkeypatch,
                                                      tmp_path):
        """The empty-SSOT short-circuit MEMOIZES '' in _aligned_cache (None is the
        'unresolved' sentinel; '' is a legitimate resolved-empty). A second
        get_team_name() must serve the cached '' WITHOUT re-reading the context.
        NON-VACUITY: after the first call we neuter get_pact_context to RAISE on
        any further call — if the second get_team_name() did NOT use the cache it
        would re-read and blow up; serving '' proves the memoization is real."""
        import shared.pact_context as pc
        ctx_module, _teams_root = ctx
        _write_context_file(monkeypatch, ctx_module, tmp_path,
                            team_name="", session_id=LEAD_SID)
        # First call: empty SSOT -> short-circuit -> '' cached.
        assert ctx_module.get_team_name() == ""
        assert ctx_module._aligned_cache == ""  # '' memoized, not None

        # Neuter the context reader: a cache MISS would now re-read and raise.
        def _boom():
            raise AssertionError("get_pact_context re-read despite cached ''")

        monkeypatch.setattr(pc, "get_pact_context", _boom)
        # Second call served from the cached '' -> no re-read, no raise.
        assert ctx_module.get_team_name() == ""

    def test_nonempty_wrong_ssot_upgrades_to_full_uuid(self, ctx, monkeypatch,
                                                       tmp_path):
        """SCENARIO 3 — RESUME-REVERT / divergence: a NON-EMPTY but WRONG SSOT
        (the computed session-<id8>) + a real full-UUID dir -> get_team_name
        UPGRADES to the real full-UUID team via identity match."""
        ctx_module, teams_root = ctx
        _seed_team_dir(teams_root, FULL_UUID_DIR, lead_session_id=LEAD_SID)
        # Persisted SSOT is the WRONG computed short name.
        _write_context_file(monkeypatch, ctx_module, tmp_path,
                            team_name=SESSION_ID8_DIR, session_id=LEAD_SID)
        # Upgrades to the real on-disk full-UUID dir (lowercased).
        assert ctx_module.get_team_name() == FULL_UUID_DIR.lower()

    def test_nonempty_ssot_no_match_noops_to_persisted(self, ctx, monkeypatch,
                                                       tmp_path):
        """NON-EMPTY SSOT but no identity-matching dir (cold-start / unborn) ->
        get_team_name no-ops back to the persisted value (zero regression)."""
        ctx_module, _teams_root = ctx  # teams_root empty -> no match
        _write_context_file(monkeypatch, ctx_module, tmp_path,
                            team_name=SESSION_ID8_DIR, session_id=LEAD_SID)
        assert ctx_module.get_team_name() == SESSION_ID8_DIR.lower()

    def test_aligned_cache_memoizes_per_process(self, ctx, monkeypatch, tmp_path):
        """get_team_name memoizes in _aligned_cache: a second call returns the
        cached value even after the on-disk dir is removed (born-and-die / process)."""
        ctx_module, teams_root = ctx
        team_dir = _seed_team_dir(teams_root, FULL_UUID_DIR, lead_session_id=LEAD_SID)
        _write_context_file(monkeypatch, ctx_module, tmp_path,
                            team_name=SESSION_ID8_DIR, session_id=LEAD_SID)
        first = ctx_module.get_team_name()
        assert first == FULL_UUID_DIR.lower()
        # Remove the dir; a non-memoized resolver would now fall back.
        import shutil
        shutil.rmtree(team_dir)
        second = ctx_module.get_team_name()
        assert second == first  # served from _aligned_cache

    def test_reset_for_tests_clears_aligned_cache(self, ctx, monkeypatch, tmp_path):
        """SCENARIO 11 — reset_for_tests() clears _aligned_cache: after reset, a
        fresh resolution is performed (no cross-test bleed)."""
        ctx_module, teams_root = ctx
        team_dir = _seed_team_dir(teams_root, FULL_UUID_DIR, lead_session_id=LEAD_SID)
        _write_context_file(monkeypatch, ctx_module, tmp_path,
                            team_name=SESSION_ID8_DIR, session_id=LEAD_SID)
        assert ctx_module.get_team_name() == FULL_UUID_DIR.lower()
        assert ctx_module._aligned_cache is not None

        ctx_module.reset_for_tests()
        assert ctx_module._aligned_cache is None  # the bleed-prevention assertion

        # After reset + a fresh context with no matchable dir, the cache does NOT
        # bleed the prior full-UUID value.
        import shutil
        shutil.rmtree(team_dir)
        _write_context_file(monkeypatch, ctx_module, tmp_path,
                            team_name=SESSION_ID8_DIR, session_id=LEAD_SID)
        assert ctx_module.get_team_name() == SESSION_ID8_DIR.lower()


# ══════════════════════════════════════════════════════════════════════════════
# 1./3. BOTH-MODES resolver legs (the DUAL-MODE PERMANENT CONTRACT, resolver side)
# ══════════════════════════════════════════════════════════════════════════════


class TestBothModesResolver:
    """The resolver keys identity match on config.leadSessionId == the LEAD's id.
    The result is INDEPENDENT of the running frame's own session_id (in-process
    frame_sid == LEAD_SID; tmux frame_sid != LEAD_SID) — because get_team_name
    threads get_session_id() (the persisted id, which is the LEAD's), NOT the
    acting frame's. We pin BOTH topologies as a standing both-modes contract."""

    @pytest.mark.parametrize("frame_sid,mode", [
        (LEAD_SID, "in-process"),
        (TMUX_SID, "tmux"),
    ])
    def test_get_team_name_resolves_lead_team_both_modes(self, ctx, monkeypatch,
                                                         tmp_path, frame_sid, mode):
        """Both modes: the persisted SSOT session_id is the LEAD's id (that is what
        session_init wrote), so get_team_name identity-matches the LEAD's full-UUID
        dir in BOTH topologies. The running frame's own sid is irrelevant — the
        resolver never recomputes from it."""
        ctx_module, teams_root = ctx
        _seed_team_dir(teams_root, FULL_UUID_DIR, lead_session_id=LEAD_SID)
        # The persisted context session_id is ALWAYS the LEAD's (that is the SSOT
        # session_init persists); 'mode' models which process is reading, but the
        # SSOT id does not change. get_session_id() returns LEAD_SID either way.
        _write_context_file(monkeypatch, ctx_module, tmp_path,
                            team_name=SESSION_ID8_DIR, session_id=LEAD_SID)
        assert ctx_module.get_team_name() == FULL_UUID_DIR.lower(), (
            f"{mode}: identity match must resolve the LEAD's team"
        )

    def test_resolver_uses_lead_sid_not_teammate_sid(self, ctx):
        """A teammate's OWN (tmux) session id does NOT identity-match the LEAD's
        team (no dir carries leadSessionId == TMUX_SID) -> fail-safe default. This
        is why resolution must thread the persisted (lead) id, never the frame's."""
        ctx_module, teams_root = ctx
        _seed_team_dir(teams_root, FULL_UUID_DIR, lead_session_id=LEAD_SID)
        # Resolving with the TEAMMATE's own sid finds nothing -> default.
        resolved = ctx_module._resolve_aligned_team_name(
            TMUX_SID, teams_dir=str(teams_root), default="default-for-teammate"
        )
        assert resolved == "default-for-teammate"


# ══════════════════════════════════════════════════════════════════════════════
# 6. CRASH-RECOVERY / heal — context-ABSENT + real-dir-present -> ALIGNED name
# 5. COLD-START — first-ever SessionStart persists the COMPUTED name, not ""
# ══════════════════════════════════════════════════════════════════════════════


class TestHealCrashRecovery:
    """heal_context_if_missing is the 3rd context-writer. Option B relies on heal
    for ABSENT-context recovery (the reader fail-closes on empty SSOT and does NOT
    recover). We PROVE heal converges to the aligned name in one prompt."""

    def _lead_frame(self, session_id):
        # is_lead reads agent_type; a lead frame carries the lead agent_type.
        # Reuse the canonical lead-frame fixture shape used elsewhere.
        from fixtures.role_frames import lead_frame_qualified
        return lead_frame_qualified(session_id=session_id)

    def _prime_absent_context(self, ctx_module, monkeypatch, tmp_path, session_id):
        """Point _context_path at an ABSENT file under a real session-scoped dir
        so heal's init()-derived path + exists() checks operate on tmp_path."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "project"))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "plugin"))
        # init() derives the path from the frame; call it then ensure absent.
        ctx_module.init({"session_id": session_id,
                         "cwd": str(tmp_path / "project")})
        if ctx_module._context_path is not None and ctx_module._context_path.exists():
            ctx_module._context_path.unlink()

    def test_heal_writes_aligned_name_when_real_dir_present(self, ctx, monkeypatch,
                                                            tmp_path):
        """SCENARIO 6 — context ABSENT + the real full-UUID team dir present -> heal
        persists the IDENTITY-MATCHED (aligned) full-UUID name, NOT the computed
        session-<id8>. Converges the context in one prompt."""
        ctx_module, teams_root = ctx
        _seed_team_dir(teams_root, FULL_UUID_DIR, lead_session_id=LEAD_SID)
        frame = self._lead_frame(LEAD_SID)
        self._prime_absent_context(ctx_module, monkeypatch, tmp_path, LEAD_SID)

        healed = ctx_module.heal_context_if_missing(frame)
        assert healed is True
        persisted = json.loads(
            ctx_module._context_path.read_text(encoding="utf-8")
        )
        # Heal wrote the ALIGNED full-UUID name (identity-matched), not the
        # computed session-<id8> default.
        assert persisted["team_name"] == FULL_UUID_DIR

    def test_heal_writes_computed_default_when_dir_absent_coldstart(self, ctx,
                                                                    monkeypatch,
                                                                    tmp_path):
        """SCENARIO 5/6 cold-start — context ABSENT + NO real team dir (the ~38s
        unborn window) -> heal persists the COMPUTED name (generate_team_name's
        session-<id8>), NOT '' (the resolver's default is threaded as the computed
        name on the writer path)."""
        ctx_module, _teams_root = ctx  # teams_root empty -> no identity match
        frame = self._lead_frame(LEAD_SID)
        self._prime_absent_context(ctx_module, monkeypatch, tmp_path, LEAD_SID)

        healed = ctx_module.heal_context_if_missing(frame)
        assert healed is True
        persisted = json.loads(
            ctx_module._context_path.read_text(encoding="utf-8")
        )
        # Cold-start: computed session-<id8>, never "".
        assert persisted["team_name"] == "session-0001639f"
        assert persisted["team_name"] != ""


class TestColdStartSessionInit:
    """SCENARIO 5 — first-ever SessionStart (no prior context + no team dir):
    session_init persists the COMPUTED name, NOT ''. The session_init convergence
    call (_resolve_aligned_team_name(session_id, default=team_name)) returns the
    computed default when the dir is unborn."""

    def test_cold_start_session_init_persists_computed_not_empty(self, ctx):
        """At a cold SessionStart the team dir is unborn -> the convergence resolver
        returns the computed default (NOT ''). Modeled at the resolver boundary the
        way session_init.py:1048 calls it: default = the freshly-computed name."""
        ctx_module, _teams_root = ctx  # no team dir seeded
        computed = "session-0001639f"
        # session_init: team_name = _resolve_aligned_team_name(sid, default=computed)
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(_teams_root), default=computed
        )
        assert resolved == computed
        assert resolved != ""

    def test_cold_start_then_born_dir_converges_to_aligned(self, ctx):
        """Once the dir is born, the same convergence call UPGRADES from the
        computed default to the aligned full-UUID dir — both writers then agree."""
        ctx_module, teams_root = ctx
        _seed_team_dir(teams_root, FULL_UUID_DIR, lead_session_id=LEAD_SID)
        computed = "session-0001639f"
        resolved = ctx_module._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default=computed
        )
        assert resolved == FULL_UUID_DIR  # converged to the aligned name


# ══════════════════════════════════════════════════════════════════════════════
# 13. TWO-FILE write-back — context-first ordering + CLAUDE.md-absent skip
# ══════════════════════════════════════════════════════════════════════════════


class TestWriteBackTwoFileConsistency:
    """bootstrap_marker_writer._write_back_aligned_team_name reconciles the
    persisted record to the aligned name. Two files: the context file (load-
    bearing, written FIRST) and the CLAUDE.md '- Team:' line (cosmetic, exists()-
    guarded). We pin (a) CLAUDE.md-ABSENT -> SKIP CLAUDE.md (no create) + context
    still written, and (b) the write ORDER is context-first (structural)."""

    def test_claude_md_absent_skips_no_create_context_still_written(
        self, monkeypatch, tmp_path
    ):
        """SCENARIO 13a — CLAUDE.md absent (gitignored worktree): the write-back
        must NOT create CLAUDE.md, but MUST still write the context file."""
        import shared.pact_context as pc
        import bootstrap_marker_writer as bmw

        calls = {"write_context": 0, "update_session_info": 0}

        monkeypatch.setattr(pc, "get_team_name", lambda: FULL_UUID_DIR.lower())
        monkeypatch.setattr(
            pc, "get_pact_context",
            lambda: {"team_name": SESSION_ID8_DIR},  # differs -> divergence
        )
        monkeypatch.setattr(pc, "get_session_id", lambda: LEAD_SID)
        monkeypatch.setattr(pc, "get_session_dir", lambda: str(tmp_path / "sess"))
        monkeypatch.setattr(pc, "get_plugin_root", lambda: str(tmp_path / "plugin"))
        monkeypatch.setattr(pc, "get_project_dir", lambda: str(tmp_path / "project"))

        def _fake_write_context(*a, **k):
            calls["write_context"] += 1

        def _fake_update_session_info(*a, **k):
            calls["update_session_info"] += 1

        monkeypatch.setattr(pc, "write_context", _fake_write_context)
        monkeypatch.setattr(bmw, "update_session_info", _fake_update_session_info)

        # CLAUDE.md is ABSENT: resolve_project_claude_md_path points at a
        # non-existent file under tmp_path.
        absent_md = tmp_path / "project" / "CLAUDE.md"
        monkeypatch.setattr(
            bmw, "resolve_project_claude_md_path",
            lambda project_dir: (absent_md, "test"),
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "project"))

        bmw._write_back_aligned_team_name()

        # Context written; CLAUDE.md NOT touched; file NOT created.
        assert calls["write_context"] == 1
        assert calls["update_session_info"] == 0
        assert not absent_md.exists()

    def test_write_order_is_context_first(self, monkeypatch, tmp_path):
        """SCENARIO 13b — the load-bearing context write precedes the cosmetic
        CLAUDE.md update. Record the call ORDER and assert context-first (so a
        crash between them leaves the load-bearing record correct)."""
        import shared.pact_context as pc
        import bootstrap_marker_writer as bmw

        order = []

        monkeypatch.setattr(pc, "get_team_name", lambda: FULL_UUID_DIR.lower())
        monkeypatch.setattr(
            pc, "get_pact_context", lambda: {"team_name": SESSION_ID8_DIR}
        )
        monkeypatch.setattr(pc, "get_session_id", lambda: LEAD_SID)
        monkeypatch.setattr(pc, "get_session_dir", lambda: str(tmp_path / "sess"))
        monkeypatch.setattr(pc, "get_plugin_root", lambda: str(tmp_path / "plugin"))
        monkeypatch.setattr(pc, "get_project_dir", lambda: str(tmp_path / "project"))

        monkeypatch.setattr(
            pc, "write_context", lambda *a, **k: order.append("context")
        )
        monkeypatch.setattr(
            bmw, "update_session_info", lambda *a, **k: order.append("claude_md")
        )
        # CLAUDE.md PRESENT so update_session_info is reached.
        present_md = tmp_path / "project" / "CLAUDE.md"
        present_md.parent.mkdir(parents=True, exist_ok=True)
        present_md.write_text("# CLAUDE.md\n", encoding="utf-8")
        monkeypatch.setattr(
            bmw, "resolve_project_claude_md_path",
            lambda project_dir: (present_md, "test"),
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "project"))

        bmw._write_back_aligned_team_name()

        assert order == ["context", "claude_md"], (
            "context file (load-bearing) must be written BEFORE the CLAUDE.md line"
        )

    def test_write_back_noop_when_aligned_equals_persisted(self, monkeypatch,
                                                           tmp_path):
        """The normal no-divergence CLI case: aligned == persisted -> clean no-op
        (no writes at all). Proves the write-back only fires on a real divergence."""
        import shared.pact_context as pc
        import bootstrap_marker_writer as bmw

        calls = {"write_context": 0}
        monkeypatch.setattr(pc, "get_team_name", lambda: SESSION_ID8_DIR)
        monkeypatch.setattr(
            pc, "get_pact_context", lambda: {"team_name": SESSION_ID8_DIR}
        )
        monkeypatch.setattr(
            pc, "write_context",
            lambda *a, **k: calls.__setitem__("write_context",
                                              calls["write_context"] + 1),
        )
        bmw._write_back_aligned_team_name()
        assert calls["write_context"] == 0

    def test_write_back_inert_on_empty_aligned(self, monkeypatch):
        """Option-B interaction: when get_team_name() returns '' (empty SSOT
        fail-closed), the write-back's `if not aligned: return` fires -> NO write.
        The reader's write-back is intentionally inert on an empty SSOT."""
        import shared.pact_context as pc
        import bootstrap_marker_writer as bmw

        calls = {"write_context": 0}
        monkeypatch.setattr(pc, "get_team_name", lambda: "")
        monkeypatch.setattr(
            pc, "write_context",
            lambda *a, **k: calls.__setitem__("write_context",
                                              calls["write_context"] + 1),
        )
        bmw._write_back_aligned_team_name()
        assert calls["write_context"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# 12. FULL-UUID CALLER SWEEP — the ~15 get_team_name callers accept a 36-char UUID
# ══════════════════════════════════════════════════════════════════════════════


class TestFullUuidCallerSweep:
    """The detect-align fix makes get_team_name() return a 36-char full-UUID in
    divergent contexts. Every caller consumes that return as a path segment; NONE
    may narrow it to a fixed width or a 'session-[a-f0-9]{8}' regex. This sweep
    exercises each caller's ACTUAL keying with a full-UUID team name and asserts
    it addresses the right store (not the resolver boundary alone).

    REGRESSION-FINDER: if any caller truncates/rejects a 36-char name, the
    relevant test FAILS -> a RED finding routed back to devops, NOT papered over.
    """

    def _seed_tasks_store(self, tmp_path, team_name, tasks):
        tasks_dir = tmp_path / ".claude" / "tasks" / team_name
        tasks_dir.mkdir(parents=True, exist_ok=True)
        for i, t in enumerate(tasks):
            (tasks_dir / f"{i}.json").write_text(json.dumps(t), encoding="utf-8")
        return tasks_dir

    def test_is_safe_path_component_accepts_full_uuid(self):
        """The shared path-safety allowlist [A-Za-z0-9_-]+ accepts a 36-char UUID
        (hex + hyphens). This is the gate EVERY path-keying caller routes through,
        so accepting the full UUID here is the linchpin of the whole sweep."""
        from shared.session_state import is_safe_path_component
        assert is_safe_path_component(FULL_UUID_DIR) is True
        assert is_safe_path_component(SESSION_ID8_DIR) is True
        assert is_safe_path_component(PACT_ID8_DIR) is True

    def test_task_utils_get_task_list_keys_full_uuid_dir(self, monkeypatch, tmp_path):
        """task_utils.get_task_list: a full-UUID team_name keys
        tasks/<full-uuid>/*.json correctly (no width assumption)."""
        import shared.task_utils as tu
        monkeypatch.setattr(tu, "get_team_name", lambda: FULL_UUID_DIR)
        tasks_base = tmp_path / ".claude" / "tasks"
        self._seed_tasks_store(tmp_path, FULL_UUID_DIR,
                               [{"id": "1", "owner": "x", "status": "pending"}])
        result = tu.get_task_list(tasks_base_dir=str(tasks_base))
        assert result is not None and len(result) == 1

    def test_iter_team_task_jsons_accepts_full_uuid(self, tmp_path):
        """iter_team_task_jsons (the SSOT per-team reader several callers route
        through) yields from tasks/<full-uuid>/ with no narrowing."""
        from shared.task_utils import iter_team_task_jsons
        tasks_base = tmp_path / ".claude" / "tasks"
        self._seed_tasks_store(tmp_path, FULL_UUID_DIR,
                               [{"id": "1"}, {"id": "2"}])
        got = list(iter_team_task_jsons(FULL_UUID_DIR,
                                        tasks_base_dir=str(tasks_base)))
        assert len(got) == 2

    def test_cleanup_old_teams_skip_protects_full_uuid_dir(self, tmp_path):
        """session_end.cleanup_old_teams: the current-team skip protects a
        full-UUID dir from reaping (exact-match, case-insensitive). A full-UUID
        current_team_name does not break the skip."""
        from session_end import cleanup_old_teams
        teams_base = tmp_path / "teams"
        live = teams_base / FULL_UUID_DIR
        live.mkdir(parents=True)
        (live / "config.json").write_text("{}", encoding="utf-8")
        # Age the live dir well past TTL so ONLY the skip protects it.
        old = 1
        os.utime(live, (old, old))
        os.utime(live / "config.json", (old, old))
        reaped, _ = cleanup_old_teams(
            current_team_name=FULL_UUID_DIR, teams_base_dir=str(teams_base),
            max_age_days=30,
        )
        # The live full-UUID dir is NOT a '^pact-' candidate at all, AND it is the
        # skip target — so it survives. (reaped counts only matched candidates.)
        assert live.exists()
        assert reaped == 0

    def test_assemble_tasks_skip_set_keeps_full_uuid_team(self):
        """session_end._assemble_tasks_skip_set: a full-UUID team_name survives the
        is_safe_path_component allowlist into the skip-set (so the live tasks dir is
        protected from the task reaper). NON-NARROWING is the load-bearing property
        — a '^pact-' narrowing here would DROP the live dir -> DATA LOSS."""
        from session_end import _assemble_tasks_skip_set
        skip = _assemble_tasks_skip_set(
            team_name=FULL_UUID_DIR, task_list_id="", session_id=LEAD_SID,
        )
        assert FULL_UUID_DIR in skip

    def test_cleanup_old_tasks_skip_protects_full_uuid_tasks_dir(self, tmp_path):
        """session_end.cleanup_old_tasks: a full-UUID name in the skip-set protects
        tasks/<full-uuid>/ from reaping even when aged past TTL — the end-to-end
        data-loss guard for the live divergent team."""
        from session_end import cleanup_old_tasks
        tasks_base = tmp_path / "tasks"
        live = tasks_base / FULL_UUID_DIR
        live.mkdir(parents=True)
        (live / "0.json").write_text("{}", encoding="utf-8")
        old = 1
        os.utime(live / "0.json", (old, old))
        os.utime(live, (old, old))
        reaped, _ = cleanup_old_tasks(
            skip_names={FULL_UUID_DIR}, tasks_base_dir=str(tasks_base),
            max_age_days=30,
        )
        assert live.exists()
        assert reaped == 0

    def test_session_state_accepts_full_uuid_team(self, monkeypatch, tmp_path):
        """session_state.build_session_state: a full-UUID team_name is carried into
        the state's team_names list (no width assumption on the display/aggregation
        path)."""
        import shared.session_state as ss
        # _default_state lists the team_name verbatim.
        state = ss._default_state(FULL_UUID_DIR)
        assert state["team_names"] == [FULL_UUID_DIR]

    # (peer-context sweep test below)
    def test_get_peer_context_keys_full_uuid_team(self, tmp_path):
        """peer_inject routes get_team_name() into get_peer_context(team_name=...),
        which keys teams/<team_name>/config.json. A full-UUID team_name addresses
        the right config (no narrowing). Pass teams_dir explicitly (the override
        seam) so the test does not depend on CLAUDE_CONFIG_DIR resolution."""
        from shared.peer_context import get_peer_context
        teams_base = tmp_path / "teams"
        team_dir = teams_base / FULL_UUID_DIR
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            json.dumps({
                "name": FULL_UUID_DIR,
                "members": [
                    {"name": "architect", "agentType": "pact-architect"},
                    {"name": "tester", "agentType": "pact-test-engineer"},
                ],
            }),
            encoding="utf-8",
        )
        # A peer of a DIFFERENT type should be discoverable -> non-empty context.
        context = get_peer_context(
            agent_type="pact-test-engineer", team_name=FULL_UUID_DIR,
            agent_name="tester", teams_dir=str(teams_base),
        )
        # The full-UUID team dir was found and read (architect peer surfaced).
        # NON-VACUITY: a narrowing caller would return None (config not found).
        assert context is not None
        assert "architect" in context


# ══════════════════════════════════════════════════════════════════════════════
# NON-VACUITY — committed standing proofs that the assertions BITE
# ══════════════════════════════════════════════════════════════════════════════


class TestNonVacuity:
    """The fix ADDS net-new symbols (``_resolve_aligned_team_name``,
    ``_aligned_cache``, the empty-SSOT short-circuit), so a source-revert removes
    the symbol -> collection ImportError rather than a clean fail (the net-new-
    symbol pattern). Instead we prove non-vacuity IN-PROCESS by NEUTERING the
    behavior under test (monkeypatch) and asserting the result FLIPS — paired
    intact+neutered assertions in one test, a standing CI guard that re-runs every
    build. If the production behavior these tests pin is ever removed, the
    'intact' half here flips RED."""

    def test_upgrade_is_attributable_to_identity_match(self, ctx, monkeypatch,
                                                       tmp_path):
        """INTACT: a non-empty wrong SSOT + a real full-UUID dir UPGRADES to the
        full-UUID via identity match. NEUTERED: with the identity-match resolver
        stubbed to return its default (the pre-fix 'just return persisted'
        behavior), get_team_name returns the PERSISTED session-<id8> instead. The
        two outcomes are mutually exclusive -> the upgrade is attributable to the
        identity-match, not to anything incidental in the fixture."""
        import shared.pact_context as pc
        ctx_module, teams_root = ctx
        _seed_team_dir(teams_root, FULL_UUID_DIR, lead_session_id=LEAD_SID)
        _write_context_file(monkeypatch, ctx_module, tmp_path,
                            team_name=SESSION_ID8_DIR, session_id=LEAD_SID)

        # INTACT — identity-match upgrades.
        assert ctx_module.get_team_name() == FULL_UUID_DIR.lower()

        # NEUTER the resolver to the pre-fix behavior (return the default), reset
        # the per-process cache, and re-read.
        monkeypatch.setattr(
            pc, "_resolve_aligned_team_name",
            lambda session_id, teams_dir=None, default=None: default or "",
        )
        ctx_module.reset_for_tests()
        _write_context_file(monkeypatch, ctx_module, tmp_path,
                            team_name=SESSION_ID8_DIR, session_id=LEAD_SID)
        # NEUTERED — no upgrade; the persisted value is returned. FLIP proven.
        assert ctx_module.get_team_name() == SESSION_ID8_DIR.lower()

    def test_failclosed_is_attributable_to_short_circuit(self, ctx, monkeypatch,
                                                         tmp_path):
        """INTACT: empty SSOT + a real matchable dir -> get_team_name returns ''
        (Option-B fail-closed short-circuit). NEUTERED: if the empty-SSOT short-
        circuit were absent (modeled by removing the guard via a stubbed
        get_team_name that runs identity-match unconditionally), the SAME seeded
        dir WOULD identity-match to the full-UUID. The seeded matchable dir is what
        makes the '' attributable to the guard, not to a missing store."""
        import shared.pact_context as pc
        ctx_module, teams_root = ctx
        _seed_team_dir(teams_root, FULL_UUID_DIR, lead_session_id=LEAD_SID)
        _write_context_file(monkeypatch, ctx_module, tmp_path,
                            team_name="", session_id=LEAD_SID)

        # INTACT — empty SSOT short-circuits to '' DESPITE the matchable dir.
        assert ctx_module.get_team_name() == ""

        # Counter-model: WITHOUT the short-circuit (run identity-match on the same
        # empty-SSOT inputs), the seeded dir DOES match -> a non-'' result. This
        # proves the matchable dir is present (so the INTACT '' is the guard, not a
        # missing dir).
        bypass = pc._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default=""
        )
        assert bypass == FULL_UUID_DIR  # identity-match WOULD have recovered it

    def test_aligned_cache_neuter_shows_memoization_is_real(self, ctx, monkeypatch,
                                                            tmp_path):
        """The _aligned_cache memoization is real: with the cache populated, a
        second get_team_name() does NOT re-invoke the resolver (proven by stubbing
        the resolver to raise on a second call — it is never reached)."""
        import shared.pact_context as pc
        ctx_module, teams_root = ctx
        _seed_team_dir(teams_root, FULL_UUID_DIR, lead_session_id=LEAD_SID)
        _write_context_file(monkeypatch, ctx_module, tmp_path,
                            team_name=SESSION_ID8_DIR, session_id=LEAD_SID)

        first = ctx_module.get_team_name()
        assert first == FULL_UUID_DIR.lower()

        # After the first resolution, the resolver MUST NOT be called again.
        def _boom(*a, **k):
            raise AssertionError("resolver re-invoked despite _aligned_cache")

        monkeypatch.setattr(pc, "_resolve_aligned_team_name", _boom)
        assert ctx_module.get_team_name() == first  # served from cache, no re-call
