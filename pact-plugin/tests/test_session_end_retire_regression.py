"""Comprehensive TEST-phase regression matrix for the SessionEnd leadSessionId
retirement pass (Commit 2: session_end._retire_session_leadsessionid_claims).

Builds ON — and does NOT duplicate — the backend-coder's SMOKE file
``test_session_end_leadsession_retire.py`` (skip-live, null-competing,
foreign-untouched, idempotent, fail-closed, no-rmtree, best-effort-on-read,
missing-root). Here we add what the smoke tests do NOT cover:

  * §6 ``test_retire_leaves_registry_prune_untouched`` — invariant 7: the
    retirement pass is SEPARATE from ``_prune_registry_dead_teams``; it rewrites
    only team ``config.json`` files and never touches the registry JSONL.
  * The coder's two DEFENSIVE hardenings that ship with NO test:
      - symlink-skip (lstat semantics) — a planted symlinked team dir cannot
        redirect the config rewrite outside the teams root (security).
      - non-dict config skip — a config.json that parses to a list/str/number
        (not an object) is skipped, never raises.
  * best-effort on a WRITE error (smoke only covers a READ/parse error): a dir
    whose config cannot be atomically rewritten is skipped and NOT counted as a
    retirement, and the pass still retires the healthy competitor.
  * A realistic MIXED corpus integration exercising every predicate branch at
    once (live + competing + foreign + already-None + malformed + non-dict).

Invariant 8 (no rmtree, name-shape gate unchanged) is pinned by the smoke
``test_does_not_rmtree_preserves_all_fields`` plus the mixed-corpus test here
asserting all dirs survive.
"""

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from session_end import _retire_session_leadsessionid_claims  # noqa: E402

SID = "0be9512d-5f6d-490e-bca3-b4ccd68f11f8"
FOREIGN_SID = "64f7b112-ef89-434c-a0c0-00a038002136"
LIVE_TEAM = "session-0be9512d"


@pytest.fixture
def env(tmp_path):
    """Isolated ~/.claude/teams tree with seed/inspect/run helpers."""
    teams_dir = tmp_path / ".claude" / "teams"
    teams_dir.mkdir(parents=True)

    class _Env:
        teams = teams_dir
        root = tmp_path

        @staticmethod
        def seed(name, *, lead_session_id, extra=None):
            d = teams_dir / name
            d.mkdir(parents=True, exist_ok=True)
            config = {"name": name, "leadSessionId": lead_session_id,
                      "members": [], "createdAt": 1771543196549}
            if extra:
                config.update(extra)
            (d / "config.json").write_text(json.dumps(config), encoding="utf-8")
            return d

        @staticmethod
        def seed_raw(name, raw_text):
            """Seed a config.json with arbitrary raw bytes (malformed / non-dict)."""
            d = teams_dir / name
            d.mkdir(parents=True, exist_ok=True)
            (d / "config.json").write_text(raw_text, encoding="utf-8")
            return d

        @staticmethod
        def lead_of(name):
            return json.loads(
                (teams_dir / name / "config.json").read_text(encoding="utf-8")
            ).get("leadSessionId")

        @staticmethod
        def retire(session_id=SID, team_name=LIVE_TEAM):
            return _retire_session_leadsessionid_claims(
                current_session_id=session_id,
                current_team_name=team_name,
                teams_dir=teams_dir,
            )

    return _Env


# ══════════════════════════════════════════════════════════════════════════════
# 1. §6 — Invariant 7: the retirement pass leaves the registry JSONL untouched
# ══════════════════════════════════════════════════════════════════════════════


class TestSeparateFromRegistryPrune:
    """Invariant 7 — the leadSessionId retirement pass touches ONLY team
    config.json files. It is a SEPARATE pass from _prune_registry_dead_teams,
    which owns ~/.claude/pact-sessions/.teammate-registry.jsonl. Retirement must
    never open, read, or rewrite the registry JSONL."""

    def test_retire_leaves_registry_jsonl_byte_identical(self, env):
        """Seed a registry JSONL alongside the teams tree, run retirement, and
        assert the registry file is byte-for-byte unchanged (retirement does not
        touch it) while a competing team's config WAS retired."""
        # A realistic registry file living under pact-sessions/ (a SIBLING of
        # teams/, exactly where _prune_registry_dead_teams operates).
        sessions_dir = env.root / ".claude" / "pact-sessions"
        sessions_dir.mkdir(parents=True)
        registry = sessions_dir / ".teammate-registry.jsonl"
        registry_content = (
            json.dumps({"name": "alice", "team": LIVE_TEAM,
                        "session_id": SID}) + "\n"
            + json.dumps({"name": "bob", "team": "agile-swinging-shore",
                          "session_id": SID}) + "\n"
        )
        registry.write_text(registry_content, encoding="utf-8")
        before = registry.read_bytes()

        env.seed(LIVE_TEAM, lead_session_id=SID)
        env.seed("agile-swinging-shore", lead_session_id=SID)

        retired = env.retire()

        # The competing config WAS retired ...
        assert retired == 1
        assert env.lead_of("agile-swinging-shore") is None
        # ... but the registry JSONL is byte-identical (separate pass, untouched).
        assert registry.read_bytes() == before, (
            "retirement must not touch the registry JSONL (invariant 7)"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 2. Coder DEFENSIVE hardenings that ship without a smoke test
# ══════════════════════════════════════════════════════════════════════════════


class TestSymlinkSkipSecurity:
    """The pass skips symlinks via lstat semantics (``entry.is_symlink()``) so a
    planted symlinked team dir cannot redirect the config rewrite outside the
    teams root. This defensive hardening ships with no smoke coverage."""

    def test_symlinked_competing_dir_is_not_rewritten(self, env, tmp_path):
        """A symlink UNDER teams/ that points at an OUTSIDE real team dir claiming
        our SID is SKIPPED — the outside target's config is NOT retired (its
        leadSessionId is preserved), proving the rewrite cannot be redirected
        through a link. A genuine in-tree competitor is still retired."""
        env.seed(LIVE_TEAM, lead_session_id=SID)
        env.seed("real-competitor", lead_session_id=SID)

        # A real team dir OUTSIDE the teams root, claiming our SID.
        outside = tmp_path / "outside-teams" / "planted"
        outside.mkdir(parents=True)
        (outside / "config.json").write_text(
            json.dumps({"name": "planted", "leadSessionId": SID}),
            encoding="utf-8",
        )
        # A symlink inside teams/ pointing at it.
        link = env.teams / "zzz-symlink"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported on this platform")

        retired = env.retire()

        # The genuine in-tree competitor was retired ...
        assert env.lead_of("real-competitor") is None
        # ... but the symlink target OUTSIDE the tree was NOT touched.
        outside_lead = json.loads(
            (outside / "config.json").read_text(encoding="utf-8")
        ).get("leadSessionId")
        assert outside_lead == SID, (
            "a symlinked dir must be skipped — the rewrite cannot follow a link"
        )
        # Count reflects only the real in-tree retirement.
        assert retired == 1


class TestNonDictConfigSkip:
    """A config.json that is valid JSON but NOT an object (list / string /
    number) is skipped by the ``isinstance(data, dict)`` guard — never treated
    as a claim, never raises."""

    @pytest.mark.parametrize("raw", ["[]", '"a string"', "42", "null", "true"])
    def test_non_dict_config_is_skipped(self, env, raw):
        """A non-dict config is skipped; the healthy competitor is still retired
        and the pass never raises."""
        env.seed(LIVE_TEAM, lead_session_id=SID)
        env.seed("good", lead_session_id=SID)
        env.seed_raw("weird-nondict", raw)

        retired = env.retire()  # must not raise

        assert env.lead_of("good") is None       # healthy competitor retired
        assert retired == 1                        # only the healthy one counted
        # The non-dict config is left exactly as written (not rewritten to null).
        assert (env.teams / "weird-nondict" / "config.json").read_text(
            encoding="utf-8"
        ) == raw


# ══════════════════════════════════════════════════════════════════════════════
# 3. Best-effort on a WRITE error (smoke covers only a READ/parse error)
# ══════════════════════════════════════════════════════════════════════════════


class TestBestEffortOnWriteError:
    """A dir whose config parses fine but CANNOT be atomically rewritten (the
    _atomic_write_config temp-file rename fails) must be skipped and NOT counted
    as a retirement — a partial/failed write is never recorded as success — while
    the pass still retires the healthy competitor and never raises."""

    def test_unwritable_competing_dir_is_skipped_not_counted(self, env):
        """Make a competing dir READ-ONLY (0o500) so mkstemp/rename inside it
        raises; the pass skips it (not counted) and still retires the healthy
        sibling. Restores perms in teardown so tmp cleanup succeeds."""
        env.seed(LIVE_TEAM, lead_session_id=SID)
        env.seed("good-sibling", lead_session_id=SID)
        locked = env.seed("locked-sibling", lead_session_id=SID)

        # Read-only dir: reading config.json still works, but creating the temp
        # file for the atomic rewrite fails -> _atomic_write_config raises ->
        # caller counts the dir as a skip, not a retirement.
        os.chmod(locked, stat.S_IRUSR | stat.S_IXUSR)  # 0o500
        try:
            retired = env.retire()  # must not raise
        finally:
            os.chmod(locked, stat.S_IRWXU)  # restore for cleanup

        # The healthy sibling WAS retired; the locked one was NOT (still our SID).
        assert env.lead_of("good-sibling") is None
        assert env.lead_of("locked-sibling") == SID, (
            "an unwritable dir must be skipped, its claim left intact"
        )
        # Count reflects only the successful rewrite.
        assert retired == 1, "a failed write must not be counted as a retirement"


# ══════════════════════════════════════════════════════════════════════════════
# 4. MIXED-corpus integration — every predicate branch exercised at once
# ══════════════════════════════════════════════════════════════════════════════


class TestMixedCorpusIntegration:
    """A realistic end-of-session corpus hitting every branch of the predicate in
    a single pass: the live team (skipped), two same-id competitors (retired), a
    foreign-id dir (untouched), an already-retired None claim (no-op), a
    malformed config (skipped), and a non-dict config (skipped). Asserts the
    exact retired count, the correct per-dir outcomes, no rmtree, and no raise."""

    def test_mixed_corpus_retires_only_live_competitors(self, env):
        env.seed(LIVE_TEAM, lead_session_id=SID)                      # live -> skip
        env.seed("aaa-competitor", lead_session_id=SID)              # retire
        env.seed("bbb-competitor", lead_session_id=SID)              # retire
        env.seed("foreign-team", lead_session_id=FOREIGN_SID)        # untouched
        env.seed("already-retired", lead_session_id=None)           # no-op (!= SID)
        env.seed_raw("malformed", "{not valid json")                 # skip
        env.seed_raw("nondict", "[1, 2, 3]")                         # skip

        retired = env.retire()  # must not raise

        # Exactly the two same-id competitors were retired.
        assert retired == 2
        assert env.lead_of("aaa-competitor") is None
        assert env.lead_of("bbb-competitor") is None
        # Live team keeps its claim (pact-align step 3 parity).
        assert env.lead_of(LIVE_TEAM) == SID
        # Foreign session's claim untouched.
        assert env.lead_of("foreign-team") == FOREIGN_SID
        # Already-retired stays None (idempotent).
        assert env.lead_of("already-retired") is None
        # Malformed and non-dict left exactly as written.
        assert (env.teams / "malformed" / "config.json").read_text(
            encoding="utf-8") == "{not valid json"
        assert (env.teams / "nondict" / "config.json").read_text(
            encoding="utf-8") == "[1, 2, 3]"
        # NO rmtree — every dir still present (invariant 8).
        for name in ("aaa-competitor", "bbb-competitor", "foreign-team",
                     "already-retired", "malformed", "nondict", LIVE_TEAM):
            assert (env.teams / name).is_dir()
