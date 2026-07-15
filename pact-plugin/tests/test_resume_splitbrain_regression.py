"""Comprehensive TEST-phase regression matrix for the resume-path dispatch-gate
stale-team split-brain fix (Commit 1: pact_context._resolve_aligned_team_name
genuine-collision disambiguation).

This file is the test-engineer's §6 deliverable. It builds ON — and deliberately
does NOT duplicate — the backend-coder's SMOKE class
``test_team_name_detect_align.py::TestGenuineCollisionDisambiguation`` (which
exercises each ladder tier once). Here we add what the smoke tests do NOT cover:

  * STRICT TIER PRECEDENCE walked in a single mutating corpus (tier1 > tier2 >
    tier3 > tier4 > tier5), so a future edit that reorders the ladder turns red.
  * The §6 BOTH-TOPOLOGIES integration test proving the ORIGINAL split-brain is
    closed through the real ``get_team_name`` gate — a corpus whose live team is
    NOT alphabetically-first now resolves to the LIVE team, in BOTH the same-id
    ``--resume`` divergence AND the changed-id stale-persist restart topology.
  * PINNED-INVARIANT regression guards exercised ON the new disambiguation path:
      - Invariant 1: empty-SSOT still fails CLOSED even when a >=2 collision
        corpus is present (disambiguation NEVER runs on an empty SSOT).
      - Invariant 2: foreign-id dirs are NEVER promoted by the liveness ladder,
        even when they carry a fresher task store / larger createdAt / the anchor
        name (the predicate stays the hard filter upstream).
      - Invariant 4: branch-2 config-less full-UUID fallback is intact behind the
        len>=2 disambiguation gate.
      - Invariant 6: never-raises totality holds under malformed createdAt and a
        combined all-signals-broken corpus.

Correctness target: ``~/.claude/pact-align.py`` steps 3+4 (make the live team
unambiguously identifiable). The both-topologies test is the L2 agreement check
that the tested behavior actually repairs the reported bug.

Every behavioral assertion seeds a corpus that makes the assertion BITE
(non-vacuity): the "wrong" alphabetically-first sibling is always present, so a
green result is attributable to the ladder picking the live team, not to an
empty/absent corpus.
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# ── Real-shaped session ids + canonical dir-name schemes ──────────────────────
# LEAD_SID keys the dominant --resume topology. RESTART_SID models a freshly
# minted id for the changed-id restart topology. FOREIGN_SID is a live OTHER
# session whose dirs must never be promoted.
LEAD_SID = "0001639f-a74f-41c4-bd0b-93d9d206e7f7"
RESTART_SID = "7c3d9e10-aaaa-4bbb-8ccc-ddddeeeeffff"
FOREIGN_SID = "ffff8888-bbbb-4ccc-9ddd-eeeeeeeeeeee"

LEAD_ID8 = "0001639f"
RESTART_ID8 = "7c3d9e10"

FULL_UUID_DIR = LEAD_SID                     # divergent-launch: bare 36-char UUID
SESSION_ID8_DIR = f"session-{LEAD_ID8}"      # 2.1.178+ CLI canonical
PACT_ID8_DIR = f"pact-{LEAD_ID8}"            # legacy PACT-minted canonical
RESTART_LIVE_DIR = f"session-{RESTART_ID8}"  # live canonical for the restart id


# ── seeding helpers (self-contained; mirror the coder's fixture shapes) ───────


def _seed_dir(teams_root, dir_name, *, lead_session_id, created_at=None,
              extra=None):
    """Seed teams/<dir_name>/config.json with a chosen leadSessionId and an
    optional createdAt (epoch millis, tier-4 signal)."""
    team_dir = teams_root / dir_name
    team_dir.mkdir(parents=True, exist_ok=True)
    config = {"name": dir_name, "leadSessionId": lead_session_id, "members": []}
    if created_at is not None:
        config["createdAt"] = created_at
    if extra:
        config.update(extra)
    (team_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return team_dir


def _touch_store(tasks_root, team_name, *, mtime):
    """Create tasks/<team_name>/1.json stamped to `mtime` — the tier-3
    task-store-recency signal (max child-mtime of the store)."""
    store = tasks_root / team_name
    store.mkdir(parents=True, exist_ok=True)
    f = store / "1.json"
    f.write_text("{}", encoding="utf-8")
    os.utime(f, (mtime, mtime))
    return store


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    """Fresh pact_context state: home -> tmp_path, caches cleared. Returns
    (module, teams_root, tasks_root) with the real <home>/.claude/{teams,tasks}
    layout so an un-injected resolve resolves to them too."""
    import shared.pact_context as ctx_module
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ctx_module.reset_for_tests()
    teams_root = tmp_path / ".claude" / "teams"
    teams_root.mkdir(parents=True, exist_ok=True)
    tasks_root = tmp_path / ".claude" / "tasks"
    tasks_root.mkdir(parents=True, exist_ok=True)
    yield ctx_module, teams_root, tasks_root
    ctx_module.reset_for_tests()


def _write_context(monkeypatch, ctx_module, tmp_path, *, team_name, session_id):
    """Persist the pact-session-context.json SSOT and point the module at it."""
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
# 1. STRICT TIER PRECEDENCE — the ladder walked top-to-bottom in one corpus
# ══════════════════════════════════════════════════════════════════════════════


class TestTierPrecedence:
    """The smoke suite proves each tier fires in isolation. These prove the
    ORDERING: when a higher tier CAN decide, it must win over every lower one,
    and removing the higher signal walks the winner down exactly one rung."""

    def test_tier1_anchor_beats_canonical_name(self, ctx):
        """(1 > 2) When `default` is itself a genuine match, it wins even though a
        canonical-named sibling (session-<id8>) is ALSO present. Anchor is the
        session's announced identity; own-canonical-name is only a fallback for
        when the anchor is absent."""
        import shared.pact_context as pc
        _m, teams_root, _t = ctx
        # 'random-live' is the announced/persisted default AND a genuine match.
        _seed_dir(teams_root, "random-live", lead_session_id=LEAD_SID)
        _seed_dir(teams_root, SESSION_ID8_DIR, lead_session_id=LEAD_SID)  # canonical
        resolved = pc._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default="random-live"
        )
        assert resolved == "random-live", "tier-1 anchor must outrank tier-2 canonical"

    def test_tier2_canonical_beats_taskstore_recency(self, ctx):
        """(2 > 3) With NO anchor (default not on disk), a canonical-named dir
        wins even though a non-canonical sibling has a strictly FRESHER task
        store. Identity (structural own-name) precedes the liveness heuristic."""
        import shared.pact_context as pc
        _m, teams_root, tasks_root = ctx
        _seed_dir(teams_root, PACT_ID8_DIR, lead_session_id=LEAD_SID)  # canonical
        _seed_dir(teams_root, "zzz-fresher", lead_session_id=LEAD_SID)
        _touch_store(tasks_root, PACT_ID8_DIR, mtime=1000.0)
        _touch_store(tasks_root, "zzz-fresher", mtime=9999.0)  # fresher — ignored
        resolved = pc._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default="not-on-disk",
            tasks_dir=str(tasks_root),
        )
        assert resolved == PACT_ID8_DIR, "tier-2 canonical must outrank tier-3 recency"

    def test_tier3_recency_beats_createdat(self, ctx):
        """(3 > 4) No anchor, no canonical. The dir with the fresher task store
        wins EVEN THOUGH it has the SMALLER createdAt — recency outranks
        createdAt. Non-vacuity: the createdAt ordering is deliberately OPPOSITE
        the recency ordering, so a green result can only come from recency."""
        import shared.pact_context as pc
        _m, teams_root, tasks_root = ctx
        _seed_dir(teams_root, "aaa-fresh-store", lead_session_id=LEAD_SID,
                  created_at=100)   # smaller createdAt
        _seed_dir(teams_root, "bbb-old-store", lead_session_id=LEAD_SID,
                  created_at=999)   # larger createdAt
        _touch_store(tasks_root, "aaa-fresh-store", mtime=9000.0)  # freshest
        _touch_store(tasks_root, "bbb-old-store", mtime=1000.0)
        resolved = pc._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default="not-a-match",
            tasks_dir=str(tasks_root),
        )
        assert resolved == "aaa-fresh-store", (
            "tier-3 recency must outrank tier-4 createdAt"
        )

    def test_tier4_createdat_beats_name(self, ctx):
        """(4 > 5) No anchor, no canonical, task stores tied/absent. The dir with
        the LARGER createdAt wins even though its name is lexicographically
        LARGER (so tier-5 smaller-first would have picked the other). createdAt
        outranks the name tie-break."""
        import shared.pact_context as pc
        _m, teams_root, tasks_root = ctx  # tasks_root empty -> tier-3 all 0.0
        _seed_dir(teams_root, "aaa-smaller-name", lead_session_id=LEAD_SID,
                  created_at=100)
        _seed_dir(teams_root, "zzz-larger-name", lead_session_id=LEAD_SID,
                  created_at=999)   # newest -> must win despite larger name
        resolved = pc._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default="not-a-match",
            tasks_dir=str(tasks_root),
        )
        assert resolved == "zzz-larger-name", (
            "tier-4 createdAt must outrank tier-5 name (else 'aaa' would win)"
        )

    def test_full_ladder_walks_down_one_rung_at_a_time(self, ctx, tmp_path):
        """The ladder walked end-to-end in ONE corpus: with every signal present
        the tier-1 anchor wins; removing each higher signal in turn walks the
        winner down to tier-2 (canonical), tier-3 (recency), tier-4 (createdAt),
        tier-5 (smallest name). A single coherent proof of the whole ordering.

        Corpus (all share LEAD_SID). Every dir carries a UNIFORM baseline
        createdAt=100 so that once the higher signals are stripped the createdAt
        tier is TIED and the name floor (tier-5) decides; only 'nnn-newest' is
        elevated to expose tier-4:
          * 'random-anchor'  — the announced default (tier-1)
          * SESSION_ID8_DIR  — canonical own-name (tier-2)
          * 'mmm-fresh'      — freshest task store (tier-3)
          * 'nnn-newest'     — largest createdAt=9999 (tier-4)
          * 'aaa-smallest'   — lexicographically smallest name (tier-5 floor)
        """
        import shared.pact_context as pc
        _m, teams_root, tasks_root = ctx
        _seed_dir(teams_root, "random-anchor", lead_session_id=LEAD_SID,
                  created_at=100)
        _seed_dir(teams_root, SESSION_ID8_DIR, lead_session_id=LEAD_SID,
                  created_at=100)
        _seed_dir(teams_root, "mmm-fresh", lead_session_id=LEAD_SID,
                  created_at=100)
        _seed_dir(teams_root, "nnn-newest", lead_session_id=LEAD_SID,
                  created_at=9999)  # elevated -> the ONLY tier-4 distinction
        _seed_dir(teams_root, "aaa-smallest", lead_session_id=LEAD_SID,
                  created_at=100)
        _touch_store(tasks_root, "mmm-fresh", mtime=9999.0)  # freshest store

        def resolve(default):
            return pc._resolve_aligned_team_name(
                LEAD_SID, teams_dir=str(teams_root), default=default,
                tasks_dir=str(tasks_root),
            )

        # tier-1: anchor present -> wins over all lower signals.
        assert resolve("random-anchor") == "random-anchor"
        # Remove anchor from contention (default not on disk) -> tier-2 canonical.
        assert resolve("not-on-disk") == SESSION_ID8_DIR
        # Remove the canonical dir -> tier-3 freshest task store (mmm-fresh),
        # which wins DESPITE nnn-newest's larger createdAt (recency > createdAt).
        (teams_root / SESSION_ID8_DIR / "config.json").unlink()
        (teams_root / SESSION_ID8_DIR).rmdir()
        assert resolve("not-on-disk") == "mmm-fresh"
        # Empty the fresh store (dir stays -> mtime 0.0) -> all recency tied ->
        # tier-4 largest createdAt (nnn-newest) decides.
        (tasks_root / "mmm-fresh" / "1.json").unlink()
        assert resolve("not-on-disk") == "nnn-newest"
        # Neutralize nnn-newest's createdAt (re-seed with none -> 0). Now every
        # remaining dir is tied at createdAt=100 (nnn-newest=0) -> tier-5 smallest
        # name decides.
        _seed_dir(teams_root, "nnn-newest", lead_session_id=LEAD_SID)  # no createdAt
        assert resolve("not-on-disk") == "aaa-smallest"


# ══════════════════════════════════════════════════════════════════════════════
# 2. §6 BOTH-TOPOLOGIES INTEGRATION — the original split-brain is CLOSED
# ══════════════════════════════════════════════════════════════════════════════


class TestBothTopologiesCollapseToLive:
    """The headline regression: a session with multiple same-leadSessionId team
    dirs where the harness-live team is NOT alphabetically-first now resolves to
    the LIVE team — through the REAL ``get_team_name`` gate, in BOTH resume
    topologies (PREPARE (B.3)). This is the L2 agreement check that the fix
    actually repairs the reported bug (pact-align.py steps 3+4 north-star)."""

    def test_same_id_resume_collapses_to_live_via_anchor(self, ctx, monkeypatch,
                                                         tmp_path):
        """TOPOLOGY 1 — same-id ``--resume`` (the dominant case). session_id is
        PRESERVED; the persisted SSOT team_name is the live team. The corpus has
        alphabetically-EARLIER stale siblings all claiming LEAD_SID (the historic
        name-sort would have returned 'aaa-stale-subteam'). get_team_name anchors
        on the persisted identity -> the LIVE team, NOT the alpha-first sibling."""
        ctx_module, teams_root, _t = ctx
        # Stale siblings sort BEFORE the live team -> the pre-fix bug's wrong pick.
        _seed_dir(teams_root, "aaa-stale-subteam", lead_session_id=LEAD_SID)
        _seed_dir(teams_root, "bbb-stale-subteam", lead_session_id=LEAD_SID)
        _seed_dir(teams_root, SESSION_ID8_DIR, lead_session_id=LEAD_SID)  # live
        _write_context(monkeypatch, ctx_module, tmp_path,
                       team_name=SESSION_ID8_DIR, session_id=LEAD_SID)

        resolved = ctx_module.get_team_name()
        assert resolved == SESSION_ID8_DIR.lower(), "must collapse to the LIVE team"
        assert resolved != "aaa-stale-subteam", (
            "the pre-fix alphabetically-first pick is exactly the split-brain bug"
        )

    def test_changed_id_restart_collapses_to_live_via_canonical(self, ctx,
                                                                monkeypatch,
                                                                tmp_path):
        """TOPOLOGY 2 — changed-id restart / stale-persist (PREPARE (B.3),
        hypothesis i). A FRESH session_id (RESTART_SID) is minted; the persisted
        SSOT team_name is STALE (a prior session's name, NOT on disk), so the
        tier-1 anchor MISSES. The new session already spawned sub-teams all
        stamped RESTART_SID, and the live team is the canonical session-<id8>.
        get_team_name recognizes the live team STRUCTURALLY via tier-2
        own-canonical-name -> the LIVE team, NOT the alpha-first sub-team."""
        ctx_module, teams_root, _t = ctx
        _seed_dir(teams_root, "aaa-subteam", lead_session_id=RESTART_SID)
        _seed_dir(teams_root, "bbb-subteam", lead_session_id=RESTART_SID)
        _seed_dir(teams_root, RESTART_LIVE_DIR, lead_session_id=RESTART_SID)  # live
        # Persisted team_name is a STALE prior-session value NOT among the matches
        # -> anchor cannot decide; the ladder must recover the live team.
        _write_context(monkeypatch, ctx_module, tmp_path,
                       team_name="session-deadbeef", session_id=RESTART_SID)

        resolved = ctx_module.get_team_name()
        assert resolved == RESTART_LIVE_DIR.lower(), (
            "changed-id restart must still collapse to the LIVE canonical team"
        )
        assert resolved != "aaa-subteam"

    def test_same_id_resume_collapses_via_recency_when_no_identity_signal(
        self, ctx, monkeypatch, tmp_path
    ):
        """TOPOLOGY 1 variant — the harness resumed into a RANDOM-named live dir
        (neither the persisted default nor a canonical own-name), and is actively
        routing tasks there. Anchor and canonical both miss; get_team_name must
        collapse to the team the harness ACTUALLY writes tasks into (tier-3
        recency) — the semantically-live team — not the alpha-first sibling."""
        ctx_module, teams_root, tasks_root = ctx
        _seed_dir(teams_root, "aaa-orphan", lead_session_id=LEAD_SID)
        _seed_dir(teams_root, "mmm-live-random", lead_session_id=LEAD_SID)
        _seed_dir(teams_root, "zzz-orphan", lead_session_id=LEAD_SID)
        # Only the live dir has an actively-written task store.
        _touch_store(tasks_root, "aaa-orphan", mtime=100.0)
        _touch_store(tasks_root, "mmm-live-random", mtime=9999.0)  # freshest = live
        # Persisted team_name is a stale value NOT on disk -> anchor misses; no
        # canonical dir present -> tier-2 misses; recency decides.
        _write_context(monkeypatch, ctx_module, tmp_path,
                       team_name="session-stale00", session_id=LEAD_SID)

        resolved = ctx_module.get_team_name()
        assert resolved == "mmm-live-random", (
            "must collapse to the team the harness is actively routing tasks into"
        )
        assert resolved != "aaa-orphan"


# ══════════════════════════════════════════════════════════════════════════════
# 3. PINNED-INVARIANT regression guards exercised ON the new disambiguation path
# ══════════════════════════════════════════════════════════════════════════════


class TestInvariantOneEmptySsotOnCollisionCorpus:
    """Invariant 1 — empty-SSOT fails CLOSED. The new disambiguation lives INSIDE
    the resolver, downstream of get_team_name's empty-SSOT short-circuit. So even
    a rich >=2 collision corpus (which WOULD disambiguate to a live team) must
    NOT be reached when the persisted team_name is empty."""

    def test_empty_ssot_fails_closed_despite_disambiguable_collision(
        self, ctx, monkeypatch, tmp_path
    ):
        """EMPTY persisted SSOT + a >=2 genuine collision corpus -> get_team_name
        returns '' WITHOUT ever entering disambiguation. NON-VACUITY: the SAME
        corpus is proven disambiguable at the resolver boundary (a non-'' live
        team), so the '' is attributable to the fail-closed guard, not to an
        empty/absent corpus."""
        import shared.pact_context as pc
        ctx_module, teams_root, _t = ctx
        _seed_dir(teams_root, "aaa-sibling", lead_session_id=LEAD_SID)
        _seed_dir(teams_root, SESSION_ID8_DIR, lead_session_id=LEAD_SID)
        # Empty persisted team_name -> the Option-B fail-closed short-circuit.
        _write_context(monkeypatch, ctx_module, tmp_path,
                       team_name="", session_id=LEAD_SID)

        assert ctx_module.get_team_name() == "", "empty SSOT must fail closed"

        # NON-VACUITY: the corpus DOES disambiguate when reached directly.
        assert pc._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default=SESSION_ID8_DIR
        ) == SESSION_ID8_DIR


class TestInvariantTwoForeignNeverPromoted:
    """Invariant 2 — identity-match stays collision-PROOF against FOREIGN ids.
    The liveness ladder ranks ONLY dirs that already passed the
    ``leadSessionId == session_id`` predicate. A foreign-id dir must never win,
    even when it carries the freshest task store, the largest createdAt, OR a
    name equal to the anchor default."""

    def test_foreign_dir_with_fresher_store_and_newer_createdat_never_wins(
        self, ctx, tmp_path
    ):
        """A foreign-id dir that is fresher AND newer than the genuine matches is
        never promoted; a genuine (LEAD_SID) match wins. NON-VACUITY: the foreign
        dir has the strictly-winning liveness signals, so a green result proves
        the predicate filtered it out BEFORE ranking."""
        import shared.pact_context as pc
        _m, teams_root, tasks_root = ctx
        # Two genuine matches (a real >=2 collision) ...
        _seed_dir(teams_root, "aaa-genuine", lead_session_id=LEAD_SID,
                  created_at=100)
        _seed_dir(teams_root, "bbb-genuine", lead_session_id=LEAD_SID,
                  created_at=200)
        # ... plus a FOREIGN dir that would win every liveness tier if considered.
        _seed_dir(teams_root, "zzz-foreign", lead_session_id=FOREIGN_SID,
                  created_at=999999)
        _touch_store(tasks_root, "aaa-genuine", mtime=100.0)
        _touch_store(tasks_root, "bbb-genuine", mtime=200.0)
        _touch_store(tasks_root, "zzz-foreign", mtime=999999.0)  # freshest

        resolved = pc._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default="not-a-match",
            tasks_dir=str(tasks_root),
        )
        assert resolved in {"aaa-genuine", "bbb-genuine"}, (
            "only a genuine LEAD_SID match may win"
        )
        assert resolved != "zzz-foreign", "foreign id must never be promoted"
        # It is 'bbb-genuine' specifically (larger createdAt among genuine).
        assert resolved == "bbb-genuine"

    def test_anchor_default_naming_a_foreign_dir_is_not_honored(self, ctx):
        """The tier-1 anchor returns `default` ONLY if it is itself a genuine
        match. A `default` string that happens to name a FOREIGN-id dir must NOT
        be honored — the anchor checks membership in the genuine-matches set."""
        import shared.pact_context as pc
        _m, teams_root, _t = ctx
        # 'shared-name' is a FOREIGN-id dir; the caller passes it as default.
        _seed_dir(teams_root, "shared-name", lead_session_id=FOREIGN_SID)
        # Two genuine matches force the >=2 disambiguation path.
        _seed_dir(teams_root, "aaa-genuine", lead_session_id=LEAD_SID)
        _seed_dir(teams_root, "bbb-genuine", lead_session_id=LEAD_SID)
        resolved = pc._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default="shared-name",
        )
        assert resolved != "shared-name", (
            "anchor must not honor a default that names a foreign-id dir"
        )
        assert resolved in {"aaa-genuine", "bbb-genuine"}


class TestInvariantFourBranchTwoIntact:
    """Invariant 4 — the branch-2 config-less full-UUID Desktop/SDK fallback is
    reached only on len(matches)==0 and is byte-unchanged. The len>=2
    disambiguation gate must not shadow or reorder it."""

    def test_branch2_full_uuid_intact_when_no_config_match(self, ctx):
        """No config.json carries LEAD_SID (len(matches)==0), but a config-less
        teams/<full-uuid>/ dir with inboxes/ IS present -> branch-2 resolves to
        session_id. Disambiguation (gated len>=2) does not run and cannot shadow
        this path."""
        import shared.pact_context as pc
        _m, teams_root, _t = ctx
        # Config-less full-UUID dir with an inboxes/ subdir (the branch-2 signal).
        divergent = teams_root / FULL_UUID_DIR
        divergent.mkdir(parents=True)
        (divergent / "inboxes").mkdir()
        # A noise sibling with a FOREIGN id (so it is not a match, len stays 0).
        _seed_dir(teams_root, "aaa-foreign", lead_session_id=FOREIGN_SID)
        resolved = pc._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default="fallback"
        )
        assert resolved == FULL_UUID_DIR, "branch-2 config-less fallback must fire"


class TestInvariantSixNeverRaisesOnDisambiguation:
    """Invariant 6 — _resolve_aligned_team_name NEVER raises. Every new disk read
    on the disambiguation path (task-store stat, createdAt parse) degrades to a
    neutral key; nothing escapes. Smoke covers an unreadable task store; here we
    cover malformed createdAt and a combined all-signals-broken corpus."""

    @pytest.mark.parametrize("bad_created_at", [
        "not-a-number", None, "", [], {}, "12.5abc",
    ])
    def test_malformed_createdat_degrades_to_name_never_raises(
        self, ctx, tmp_path, bad_created_at
    ):
        """A malformed createdAt on the tie-deciding dirs degrades to 0 (tier-4
        neutral) and the ladder falls to the tier-5 smallest name — never raises.
        Non-vacuity: both dirs carry the SAME malformed value so the decision can
        only come from the name floor."""
        import shared.pact_context as pc
        _m, teams_root, tasks_root = ctx  # empty tasks_root -> tier-3 all 0.0
        _seed_dir(teams_root, "aaa-small", lead_session_id=LEAD_SID,
                  created_at=bad_created_at)
        _seed_dir(teams_root, "zzz-large", lead_session_id=LEAD_SID,
                  created_at=bad_created_at)
        resolved = pc._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default="not-a-match",
            tasks_dir=str(tasks_root),
        )
        # Both createdAt degrade to 0 -> tier-5 smallest name wins deterministically.
        assert resolved == "aaa-small"

    def test_combined_all_signals_broken_falls_to_name_never_raises(
        self, ctx, tmp_path
    ):
        """Belt-and-suspenders: no anchor, no canonical, an UNREADABLE task store
        (tasks_dir points at a FILE so iterdir raises), AND malformed createdAt on
        every dir -> the resolver degrades every liveness tier and lands on the
        tier-5 smallest name without raising."""
        import shared.pact_context as pc
        _m, teams_root, _t = ctx
        _seed_dir(teams_root, "aaa-x", lead_session_id=LEAD_SID,
                  created_at="garbage")
        _seed_dir(teams_root, "mmm-y", lead_session_id=LEAD_SID, created_at=None)
        _seed_dir(teams_root, "zzz-z", lead_session_id=LEAD_SID,
                  created_at={"nested": "bad"})
        # tasks_dir is a FILE, not a dir -> every _task_store_mtime iterdir raises.
        bogus_tasks = tmp_path / "tasks-is-a-file"
        bogus_tasks.write_text("x", encoding="utf-8")
        resolved = pc._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default="not-a-match",
            tasks_dir=str(bogus_tasks),
        )
        assert resolved == "aaa-x", "all signals degraded -> smallest name, no raise"


# ══════════════════════════════════════════════════════════════════════════════
# 4. COUNTER-MODEL — the disambiguation result is attributable to the NEW ladder
# ══════════════════════════════════════════════════════════════════════════════


class TestDisambiguationAttribution:
    """A standing in-process non-vacuity guard: with the collision corpus fixed,
    NEUTERING the disambiguation helper to the pre-fix "return first name-sorted"
    behavior FLIPS the winner from the live team to the alphabetically-first
    sibling. The paired intact+neutered assertions prove the correct pick is
    caused by the new ladder, not by anything incidental in the fixture."""

    def test_live_pick_is_attributable_to_disambiguation(self, ctx, monkeypatch):
        """INTACT: anchor picks the live team over an alphabetically-earlier
        sibling. NEUTERED: with _disambiguate_collision stubbed to return the
        first (name-sorted) match — the pre-fix behavior — the SAME corpus
        resolves to the alpha-first sibling. Mutually exclusive outcomes."""
        import shared.pact_context as pc
        _m, teams_root, _t = ctx
        _seed_dir(teams_root, "aaa-stale", lead_session_id=LEAD_SID)
        _seed_dir(teams_root, SESSION_ID8_DIR, lead_session_id=LEAD_SID)  # live

        # INTACT — anchor on the live team.
        assert pc._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default=SESSION_ID8_DIR
        ) == SESSION_ID8_DIR

        # NEUTER to the pre-fix "first name-sorted match wins".
        monkeypatch.setattr(
            pc, "_disambiguate_collision",
            lambda matches, session_id, default, tasks_root: matches[0][0],
        )
        assert pc._resolve_aligned_team_name(
            LEAD_SID, teams_dir=str(teams_root), default=SESSION_ID8_DIR
        ) == "aaa-stale", "neutered pre-fix behavior returns the alpha-first sibling"
