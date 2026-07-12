"""
Location: pact-plugin/tests/test_marker_namespace.py
Summary: Tests for the keyword-only marker `namespace` parameter on
         agent_handoff_marker's resolver/claim/rollback trio, and the
         cross-namespace independence pair between the agent_handoff
         default namespace and the task_metadata_snapshot namespace
         (via the substrate's hard-bound wrappers).
Used by: pytest. The byte-identical-DEFAULT proof is the existing
         test_agent_handoff_marker.py suite passing UNMODIFIED — this file
         only covers what that suite structurally cannot: the non-default
         namespace and the two-namespace interaction.
"""

from pathlib import Path

from shared.agent_handoff_marker import (
    _DEFAULT_MARKER_NAMESPACE,
    _marker_dir,
    already_emitted,
    unclaim,
)
from shared.task_metadata_snapshot import (
    SNAPSHOT_MARKER_NAMESPACE,
    snapshot_already_emitted,
    snapshot_unclaim,
)

TEAM = "team-ns"
TASK = "7"
KEY = "aaaabbbb"  # content-key / occupant slot — just a string to the marker


class TestNamespaceParameter:
    def test_default_dir_name_unchanged(self, tmp_path, monkeypatch):
        """The default namespace resolves the pre-refactor directory name —
        the constant pins the byte-identical-default contract."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert _DEFAULT_MARKER_NAMESPACE == ".agent_handoff_emitted"
        assert _marker_dir(TEAM).name == ".agent_handoff_emitted"
        assert _marker_dir(TEAM) == _marker_dir(
            TEAM, namespace=_DEFAULT_MARKER_NAMESPACE
        )

    def test_namespace_changes_leaf_dir_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        default_dir = _marker_dir(TEAM)
        snapshot_dir = _marker_dir(TEAM, namespace=SNAPSHOT_MARKER_NAMESPACE)
        assert snapshot_dir.name == ".task_metadata_snapshot_emitted"
        assert snapshot_dir.parent == default_dir.parent

    def test_claim_and_dedup_in_custom_namespace(self, tmp_path, monkeypatch):
        """Claim/dedup semantics are namespace-scoped and unchanged: first
        call claims (False), second dedups (True), unclaim re-opens."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        namespace = SNAPSHOT_MARKER_NAMESPACE
        assert (
            already_emitted(TEAM, TASK, KEY, namespace=namespace) is False
        )
        assert already_emitted(TEAM, TASK, KEY, namespace=namespace) is True
        unclaim(TEAM, TASK, KEY, namespace=namespace)
        assert (
            already_emitted(TEAM, TASK, KEY, namespace=namespace) is False
        )


class TestCrossNamespaceIndependence:
    """The ratified independence pair: neither event family's marker can
    suppress the other's."""

    def test_handoff_claim_does_not_suppress_snapshot(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Claim the HANDOFF marker (default namespace) for (team, task).
        assert already_emitted(TEAM, TASK, KEY) is False
        assert already_emitted(TEAM, TASK, KEY) is True  # positive control
        # The snapshot family still claims fresh.
        assert snapshot_already_emitted(TEAM, TASK, KEY) is False

    def test_snapshot_claim_does_not_suppress_handoff(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Claim the SNAPSHOT marker for (team, task).
        assert snapshot_already_emitted(TEAM, TASK, KEY) is False
        assert snapshot_already_emitted(TEAM, TASK, KEY) is True
        # The handoff family still claims fresh.
        assert already_emitted(TEAM, TASK, KEY) is False

    def test_snapshot_unclaim_never_touches_handoff_marker(
        self, tmp_path, monkeypatch
    ):
        """The hard-bound wrapper rollback removes ONLY the snapshot-
        namespace marker — the poisoned-marker shape a forgotten namespace
        arg would produce is impossible through the wrappers."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert already_emitted(TEAM, TASK, KEY) is False
        assert snapshot_already_emitted(TEAM, TASK, KEY) is False
        snapshot_unclaim(TEAM, TASK, KEY)
        # Snapshot marker gone; handoff marker still owned.
        assert snapshot_already_emitted(TEAM, TASK, KEY) is False
        assert already_emitted(TEAM, TASK, KEY) is True
