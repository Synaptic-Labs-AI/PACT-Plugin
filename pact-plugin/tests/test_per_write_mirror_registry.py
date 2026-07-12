"""
Tests for the per-write mirror foundations: the PER_WRITE_MIRROR_KEYS
registry constant (shared/task_metadata_snapshot.py) and the
canonical-journal-frame predicate (shared/pact_context.py).

Covers:

Registry invariants:
1. PER_WRITE_MIRROR_KEYS is disjoint from SNAPSHOT_EXCLUDE — a targeted key
   that the substrate then drops from the payload would be a fire that
   mirrors nothing (silent durability hole), so disjointness is pinned.
2. Registry is a non-empty frozenset of strings (shape pin — exact
   membership is deliberately NOT pinned so a registry extension stays a
   one-string source diff).
3. The five ruled keys are a MINIMUM SUBSET of the registry — deletion of
   a ruled key goes red while extensions stay one-string diffs (the
   deletion-protection complement of the no-exact-membership choice).

_read_lead_session_id() (shared copy):
4. Returns the leadSessionId string from a valid team config
5. teams_dir override is honored
6. Returns "" on: unsafe team_name, missing config, malformed JSON,
   non-object top-level, missing key, non-string key value

is_canonical_journal_frame():
7. Lead frame → True with NO config and NO session_id (is_lead leg is
   independent of the topology leg's resolvability)
8. Teammate frame with session_id == leadSessionId → True (in-process
   topology; the positive control for the topology leg)
9. Teammate frame with session_id != leadSessionId → False (tmux topology)
10. Resolution failures all → False: missing session_id, non-string
   session_id, missing config, malformed config, non-string leadSessionId,
   empty team context (fail-closed team resolution), empty input frame
"""

import json

import pytest

import shared.pact_context as ctx_module
from shared.task_metadata_snapshot import (
    PER_WRITE_MIRROR_KEYS,
    SNAPSHOT_EXCLUDE,
)


LEAD_SESSION = "lead-session-0001"
TEAMMATE_SESSION = "teammate-session-0002"


class TestPerWriteMirrorKeysRegistry:
    """Shape + disjointness pins on the registry constant."""

    def test_disjoint_from_snapshot_exclude(self):
        """A key in both sets would fire the per-write seam and then have
        the substrate drop it from the payload — a mirror of nothing."""
        assert PER_WRITE_MIRROR_KEYS & SNAPSHOT_EXCLUDE == frozenset()

    def test_registry_is_nonempty_frozenset_of_strings(self):
        assert isinstance(PER_WRITE_MIRROR_KEYS, frozenset)
        assert PER_WRITE_MIRROR_KEYS
        assert all(isinstance(k, str) and k for k in PER_WRITE_MIRROR_KEYS)

    def test_ruled_keys_are_minimum_subset(self):
        """Deletion protection for the ruled keys: a superset assert keeps
        registry EXTENSIONS a one-string source diff while the silent
        removal of any ruled key goes red. Without this pin (and with
        exact membership deliberately unpinned above), dropping a key
        that no seam test uses as its fire trigger would ship green —
        un-mirroring that key class with zero test signal."""
        assert frozenset({
            "scope_contract",
            "nesting_depth",
            "worktree_path",
            "teachback_submit",
            "teachback_rejection",
        }) <= PER_WRITE_MIRROR_KEYS


@pytest.fixture
def teams_root(tmp_path, monkeypatch):
    """Hermetic claude-config root: both the predicate's config read and
    get_team_name's identity-match scan resolve under tmp_path."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    teams = tmp_path / "teams"
    teams.mkdir()
    return teams


def _seed_config(teams, team="test-team", lead_session_id=LEAD_SESSION):
    """Write a minimal team config carrying leadSessionId."""
    team_dir = teams / team
    team_dir.mkdir(exist_ok=True)
    (team_dir / "config.json").write_text(
        json.dumps({"leadSessionId": lead_session_id}), encoding="utf-8"
    )
    return team_dir


class TestReadLeadSessionId:
    """Unit rows for the shared guarded read-helper. Behavior must stay in
    LOGIC-PARITY with the two private copies its docstring cross-references."""

    def test_returns_lead_session_id_from_valid_config(self, teams_root):
        _seed_config(teams_root)
        assert (
            ctx_module._read_lead_session_id("test-team") == LEAD_SESSION
        )

    def test_teams_dir_override_honored(self, tmp_path):
        teams = tmp_path / "alt-teams"
        teams.mkdir()
        _seed_config(teams)
        result = ctx_module._read_lead_session_id(
            "test-team", teams_dir=str(teams)
        )
        assert result == LEAD_SESSION

    def test_unsafe_team_name_returns_empty(self, teams_root):
        _seed_config(teams_root)
        assert ctx_module._read_lead_session_id("../test-team") == ""
        assert ctx_module._read_lead_session_id("") == ""

    def test_missing_config_returns_empty(self, teams_root):
        assert ctx_module._read_lead_session_id("test-team") == ""

    def test_malformed_json_returns_empty(self, teams_root):
        team_dir = teams_root / "test-team"
        team_dir.mkdir()
        (team_dir / "config.json").write_text("{not json", encoding="utf-8")
        assert ctx_module._read_lead_session_id("test-team") == ""

    def test_non_object_top_level_returns_empty(self, teams_root):
        team_dir = teams_root / "test-team"
        team_dir.mkdir()
        (team_dir / "config.json").write_text("[1, 2]", encoding="utf-8")
        assert ctx_module._read_lead_session_id("test-team") == ""

    def test_missing_key_returns_empty(self, teams_root):
        team_dir = teams_root / "test-team"
        team_dir.mkdir()
        (team_dir / "config.json").write_text(
            json.dumps({"members": []}), encoding="utf-8"
        )
        assert ctx_module._read_lead_session_id("test-team") == ""

    def test_non_string_value_returns_empty(self, teams_root):
        _seed_config(teams_root, lead_session_id=42)
        assert ctx_module._read_lead_session_id("test-team") == ""


class TestIsCanonicalJournalFrame:
    """Both-modes topology rows for the frame predicate. Structural signals
    only: agent_type for the role leg, session_id vs the team config's
    leadSessionId for the topology leg. False must be the answer on every
    resolution failure (skip defers durability to the completion-time seams;
    a misclassified emit could silo the event and poison the shared
    content-hash marker namespace)."""

    def test_lead_frame_true_without_config_or_session_id(self):
        """The is_lead leg must be independent of topology resolvability:
        no context, no team config, no session_id — still True."""
        assert ctx_module.is_canonical_journal_frame(
            {"agent_type": "pact-orchestrator"}
        )
        assert ctx_module.is_canonical_journal_frame(
            {"agent_type": "PACT:pact-orchestrator"}
        )

    def test_in_process_teammate_frame_true(self, teams_root, pact_context):
        """Positive control for the topology leg: one process, one session —
        the frame's session_id equals the team config's leadSessionId."""
        pact_context(team_name="test-team", session_id=LEAD_SESSION)
        _seed_config(teams_root)
        assert ctx_module.is_canonical_journal_frame(
            {"agent_type": "pact-backend-coder", "session_id": LEAD_SESSION}
        )

    def test_tmux_teammate_frame_false(self, teams_root, pact_context):
        """A distinct session_id is the tmux teammate signature — skip."""
        pact_context(team_name="test-team", session_id=TEAMMATE_SESSION)
        _seed_config(teams_root)
        assert not ctx_module.is_canonical_journal_frame(
            {
                "agent_type": "pact-backend-coder",
                "session_id": TEAMMATE_SESSION,
            }
        )

    def test_missing_session_id_false(self, teams_root, pact_context):
        pact_context(team_name="test-team", session_id=LEAD_SESSION)
        _seed_config(teams_root)
        assert not ctx_module.is_canonical_journal_frame(
            {"agent_type": "pact-backend-coder"}
        )

    def test_non_string_session_id_false(self, teams_root, pact_context):
        pact_context(team_name="test-team", session_id=LEAD_SESSION)
        _seed_config(teams_root)
        assert not ctx_module.is_canonical_journal_frame(
            {"agent_type": "pact-backend-coder", "session_id": 123}
        )

    def test_missing_config_false(self, teams_root, pact_context):
        pact_context(team_name="test-team", session_id=LEAD_SESSION)
        assert not ctx_module.is_canonical_journal_frame(
            {"agent_type": "pact-backend-coder", "session_id": LEAD_SESSION}
        )

    def test_malformed_config_false(self, teams_root, pact_context):
        pact_context(team_name="test-team", session_id=LEAD_SESSION)
        team_dir = teams_root / "test-team"
        team_dir.mkdir()
        (team_dir / "config.json").write_text("{not json", encoding="utf-8")
        assert not ctx_module.is_canonical_journal_frame(
            {"agent_type": "pact-backend-coder", "session_id": LEAD_SESSION}
        )

    def test_non_string_lead_session_id_false(self, teams_root, pact_context):
        pact_context(team_name="test-team", session_id=LEAD_SESSION)
        _seed_config(teams_root, lead_session_id=42)
        assert not ctx_module.is_canonical_journal_frame(
            {"agent_type": "pact-backend-coder", "session_id": LEAD_SESSION}
        )

    def test_empty_team_context_false(self, teams_root):
        """No session context → get_team_name fails closed to "" → the
        topology leg cannot resolve → False, even with a matching config."""
        _seed_config(teams_root)
        assert not ctx_module.is_canonical_journal_frame(
            {"agent_type": "pact-backend-coder", "session_id": LEAD_SESSION}
        )

    def test_empty_input_frame_false(self):
        """Never-raises floor: an empty frame is not canonical."""
        assert not ctx_module.is_canonical_journal_frame({})
