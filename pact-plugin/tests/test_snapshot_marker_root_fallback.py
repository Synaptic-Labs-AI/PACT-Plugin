"""
Location: pact-plugin/tests/test_snapshot_marker_root_fallback.py
Summary: Verification tests for the snapshot family's session-dir marker-root
         fallback — when team_name is empty but the journal resolves, the
         content-key dedup marker claims under the session directory instead
         of silently disabling dedup (fail-open). Covers: empty-team first
         fire emits AND claims under the session root; an identical repeat is
         suppressed; the healthy-team marker path is byte-identical to the
         pre-fallback behavior (no session-dir marker dir is created); an
         unresolvable journal still defers before any claim; and a falsy or
         relative root_dir at the resolver level fails open (emit, no marker).
         Runs against the REAL marker SSOT and a REAL on-disk journal (no
         mocking of agent_handoff_marker internals) so claim, dedup, and
         append exercise the production paths end to end.
Used by: pytest. The comprehensive bidirectional certification suite
         (cross-seam dedup, unclaim parity, both-modes matrix, subsystem-
         fault fail-open) is authored in the test phase; this file proves
         the implementation works.
"""

from pathlib import Path

import shared.pact_context as pact_context_module
import shared.session_journal as session_journal
from shared.agent_handoff_marker import already_emitted, unclaim
from shared.task_metadata_snapshot import (
    SNAPSHOT_MARKER_NAMESPACE,
    emit_task_metadata_snapshot,
)

TEAM = "pact-test"
PAYLOAD = {"scope_contract": {"files": ["hooks/shared/example.py"]}}


def _snapshot_events(session_dir: str) -> list:
    return session_journal.read_events_from(
        session_dir, "task_metadata_snapshot"
    )


def _marker_files(root: Path) -> list:
    marker_dir = root / SNAPSHOT_MARKER_NAMESPACE
    if not marker_dir.is_dir():
        return []
    return sorted(p.name for p in marker_dir.iterdir())


class TestEmptyTeamSessionRootFallback:
    def test_first_fire_emits_and_claims_under_session_dir(
        self, tmp_path, monkeypatch, pact_context
    ):
        """Empty team + resolvable journal: the emit appends exactly one
        event AND claims the content-key marker under the session dir —
        dedup is enabled, not silently disabled."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name="", session_id="s-fallback")
        session_dir = pact_context_module.get_session_dir()
        assert session_dir, "harness: session dir must resolve"

        emit_task_metadata_snapshot("", "5", "subject", "owner", PAYLOAD)

        events = _snapshot_events(session_dir)
        assert len(events) == 1
        assert events[0]["metadata"] == PAYLOAD
        markers = _marker_files(Path(session_dir))
        assert len(markers) == 1
        assert markers[0].startswith("5-")

    def test_identical_repeat_is_suppressed(
        self, tmp_path, monkeypatch, pact_context
    ):
        """The bounded-churn direction: a second emit of a byte-identical
        payload claims nothing new and appends nothing."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name="", session_id="s-dedup")
        session_dir = pact_context_module.get_session_dir()

        emit_task_metadata_snapshot("", "5", "subject", "owner", PAYLOAD)
        emit_task_metadata_snapshot("", "5", "subject", "owner", PAYLOAD)

        assert len(_snapshot_events(session_dir)) == 1
        assert len(_marker_files(Path(session_dir))) == 1

    def test_unresolvable_journal_defers_before_any_claim(
        self, tmp_path, monkeypatch, pact_context
    ):
        """All-empty context (the unpersisted-teammate shape): the
        writability precondition still returns before the fallback root is
        ever consulted — no event, no marker anywhere, no exception."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name="", session_id="", project_dir="")
        assert pact_context_module.get_session_dir() == ""

        emit_task_metadata_snapshot("", "5", "subject", "owner", PAYLOAD)

        marker_dirs = list(tmp_path.rglob(SNAPSHOT_MARKER_NAMESPACE))
        assert marker_dirs == []
        journals = list(tmp_path.rglob("session-journal.jsonl"))
        assert journals == []


class TestHealthyTeamTwoRootRegression:
    def test_marker_path_identical_and_no_session_dir_marker(
        self, tmp_path, monkeypatch, pact_context
    ):
        """Non-empty team: the marker lands at the pre-fallback team-scoped
        path and NO session-dir marker dir is created — the fallback branch
        is unreachable while team_name is non-empty."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s-healthy")
        session_dir = pact_context_module.get_session_dir()

        emit_task_metadata_snapshot(TEAM, "9", "subject", "owner", PAYLOAD)

        assert len(_snapshot_events(session_dir)) == 1
        team_root = tmp_path / ".claude" / "teams" / TEAM
        team_markers = _marker_files(team_root)
        assert len(team_markers) == 1
        assert team_markers[0].startswith("9-")
        assert _marker_files(Path(session_dir)) == []


class TestRootDirResolverGuards:
    def test_falsy_root_dir_fails_open(self, tmp_path, monkeypatch):
        """An empty root_dir is 'no valid target': never claims (repeat
        calls stay False → caller emits) and creates nothing."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert already_emitted("", "5", "k1", root_dir="") is False
        assert already_emitted("", "5", "k1", root_dir="") is False
        unclaim("", "5", "k1", root_dir="")  # must not raise
        assert list(tmp_path.rglob("5-k1")) == []

    def test_relative_root_dir_fails_open(self, tmp_path, monkeypatch):
        """A relative root cannot anchor the containment check → same
        fail-open signal, nothing created."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        relative = "relative/marker/root"
        assert already_emitted("", "5", "k1", root_dir=relative) is False
        assert already_emitted("", "5", "k1", root_dir=relative) is False
        unclaim("", "5", "k1", root_dir=relative)  # must not raise
        assert list(tmp_path.rglob("5-k1")) == []
