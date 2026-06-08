"""
Location: pact-plugin/tests/test_session_init_integration.py
Summary: NON-MOCKED L2 integration coverage for session_init's caller-3 seam —
the post-compaction checkpoint built from get_task_list() (session_init.py
~:1211). Under Agent Teams this was PARTIAL pre-#923 (the broken get_task_list
session_id key degraded the checkpoint to the bootstrap safety-net). The #923
GLOBAL team-first fix in task_utils.get_task_list repaired it; this L2 is the
REGRESSION PIN that a re-inert resolver can never again degrade the checkpoint
silently.

This drives the REAL get_team_name -> team-dir -> glob resolution (the exact
caller-3 seam) + the REAL checkpoint builders, NO resolver stub.

================================ ANTI-MOCK INVARIANT ===========================
MUST NOT monkeypatch get_task_list / iter_team_task_jsons / get_team_name /
find_feature_task / find_current_phase / find_active_agents. The real team-dir
resolution IS the seam. The ONLY doubles are Path.home redirection + the
pact_context fixture.

============================ NON-VACUITY (source-revert) =======================
The checkpoint's feature/phase/agent lines are DOWNSTREAM of get_task_list
resolving the real team dir. Source-revert the #923 team-first fix in
hooks/shared/task_utils.py get_task_list (so a TEAM session resolves the absent
{session_id} dir instead of {team_name}), then run:
    python -m pytest tests/test_session_init_integration.py -k non_vacuity_gate
EXPECTED cardinality: {1 failed} — get_task_list() returns None under the team
session -> build_post_compaction_checkpoint emits the "Unable to identify feature
task" safety-net shape instead of the real feature/phase, so the feature-id
assertion fails. Restore -> green. The same-fixture NEGATIVE control (empty team dir ->
safety-net shape) proves the assertion is coupled to the real resolution.
================================================================================
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

# Surgical: exercise the caller-3 resolver via task_utils directly. session_init
# caller-3 (session_init.py ~:1211) calls the SAME function — session_init
# re-exports it verbatim (`from shared.task_utils import get_task_list`), so an
# arg-less task_utils.get_task_list() is byte-identical to the caller-3 seam.
# We deliberately do NOT `import session_init` here: importing the 73KB module at
# collection pulls staleness/pin_caps/claude_md_manager and perturbs a
# pre-existing latent test-isolation defect (tracked separately as #928); this
# focused import keeps the blast radius on the seam under test.
from shared import task_utils  # noqa: E402

TEAM = "pact-testteam"
SID = "aaaaaaaa-1111-2222-3333-444444444444"


def _write_task(tasks_dir: Path, task_id: str, subject: str, status: str,
                phase: str | None = None, owner: str | None = None,
                blocked_by: list[str] | None = None) -> None:
    tasks_dir.mkdir(parents=True, exist_ok=True)
    task: dict = {"id": task_id, "subject": subject, "status": status,
                  "metadata": {}}
    if phase:
        task["metadata"]["pact_phase"] = phase
    if owner:
        task["owner"] = owner
    if blocked_by:
        task["blockedBy"] = blocked_by
    (tasks_dir / f"{task_id}.json").write_text(json.dumps(task), encoding="utf-8")


@pytest.fixture
def live_env(tmp_path, monkeypatch, pact_context):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name=TEAM, session_id=SID, project_dir="/test/project")
    return tmp_path / ".claude" / "tasks" / TEAM


def _build_checkpoint() -> str:
    """Reproduce session_init caller-3: the arg-less get_task_list() seam ->
    finders -> build_post_compaction_checkpoint. task_utils.get_task_list IS
    session_init.get_task_list (re-export), so this is the identical resolver."""
    tasks = task_utils.get_task_list()  # arg-less — the caller-3 resolver
    feature = task_utils.find_feature_task(tasks or [])
    phase = task_utils.find_current_phase(tasks or [])
    agents = task_utils.find_active_agents(tasks or [])
    blockers = task_utils.find_blockers(tasks or [])
    return task_utils.build_post_compaction_checkpoint(feature, phase, agents, blockers)


class TestSessionInitCheckpointRealSeam:
    def test_non_vacuity_gate_real_team_tasks_populate_checkpoint(self, live_env):
        # Exactly ONE feature-qualifying task (100): unblocked, in_progress, no
        # phase prefix. 101 is a CODE: phase task (find_feature_task skips phase
        # prefixes); 102 is a blocked child (find_feature_task skips blockedBy) —
        # so the feature line is deterministic regardless of glob order.
        _write_task(live_env, "100", "Build the feature (#924)", "in_progress")
        _write_task(live_env, "101", "CODE: the feature", "in_progress", phase="CODE")
        _write_task(live_env, "102", "backend: implement", "in_progress",
                    owner="backend", blocked_by=["101"])
        cp = _build_checkpoint()
        assert "[POST-COMPACTION CHECKPOINT]" in cp
        assert "Unable to identify feature task" not in cp, (
            "the real team dir must resolve via get_task_list() -> a real "
            "feature line; the safety-net shape here is the inert caller-3 bug"
        )
        assert "Build the feature" in cp and "id: 100" in cp

    def test_negative_control_empty_team_dir_safety_net(self, live_env):
        live_env.mkdir(parents=True, exist_ok=True)  # team dir exists but empty
        cp = _build_checkpoint()
        # get_task_list() -> None -> finders get [] -> safety-net feature line.
        assert "Unable to identify feature task" in cp
