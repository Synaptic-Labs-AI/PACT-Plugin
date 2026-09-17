"""
Location: pact-plugin/tests/test_session_init_integration.py
Summary: NON-MOCKED L2 integration coverage for TWO session_init seams. Seam A
is the caller-3 seam — the post-compaction checkpoint built from
get_task_list(). Seam B is the session-dir resolution behind the post-compaction
lead-read instruction; its own anti-mock invariant and non-vacuity gate are
documented on TestCompactReadInstructionRealSeam below.

Seam A — the post-compaction checkpoint built from get_task_list() (session_init.py
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
from pathlib import Path

import pytest

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


# ── Seam B: session-dir resolution behind the lead-read instruction ──────────


_LEAD_AGENT_TYPE = "pact-orchestrator"
_UNKNOWN_ROLE_FRAGMENT = "relaunch with `--agent PACT:pact-orchestrator`"


def _run_compact_main(tmp_path, monkeypatch, session_id,
                      agent_type=_LEAD_AGENT_TYPE):
    """Drive session_init.main() at source='compact' and return the emitted
    additionalContext.

    ``agent_type=None`` OMITS the key and builds an UNKNOWN frame, which is the
    only way to reach the unknown branch of the role gate. The DEFAULT is the
    lead role, because the archive instructions this file asserts are emitted on
    the lead path and on no other. The driver used to omit the key and reach
    those instructions only because a lead frame and an unknown frame emitted
    identical bytes before the gate became three-way.

    session_init is imported INSIDE the function, not at module scope, for the
    same reason the module header gives: a collection-time import of the 73KB
    module pulls staleness/pin_caps/claude_md_manager into every test in this
    file. The helper in tests/test_session_init.py imports it the same way.
    """
    import io
    from unittest.mock import patch

    import session_init

    payload: dict = {"source": "compact"}
    if session_id is not None:
        payload["session_id"] = session_id
    if agent_type is not None:
        payload["agent_type"] = agent_type

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/test/project")
    # session_init.main() now appends to $CLAUDE_ENV_FILE when both vars are
    # present — keep these drives hermetic against an ambient env file.
    monkeypatch.delenv("CLAUDE_ENV_FILE", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    with patch("session_init.setup_plugin_symlinks", return_value=None), \
         patch("session_init.ensure_project_memory_md", return_value=None), \
         patch("session_init.check_pinned_staleness", return_value=None), \
         patch("session_init.update_session_info", return_value=None), \
         patch("session_init.restore_last_session", return_value=None), \
         patch("session_init.check_resume_state", return_value=None), \
         patch("sys.stdin", io.StringIO(json.dumps(payload))), \
         patch("sys.stdout", new_callable=io.StringIO) as out:
        with pytest.raises(SystemExit) as exc:
            session_init.main()

    assert exc.value.code == 0
    return json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]


class TestCompactReadInstructionRealSeam:
    """The lead-read instruction must name where the compact summary WENT.

    The secretary archives the single-use compact-summary file into the session
    directory after processing it, so a lead following the instruction even
    slightly later finds the canonical path empty. The instruction therefore
    names the archive directory as a fallback, which puts session-dir resolution
    into the emitted text.

    ============================ ANTI-MOCK INVARIANT ========================
    MUST NOT patch get_session_dir, get_compact_summary_path, or
    build_context_cache. The real session-dir resolution IS this seam. The only
    doubles are Path.home redirection plus suppressors for start-up side effects
    (symlinks, project memory, staleness, session-info write, resume state) —
    none of them participates in building this instruction. get_task_list is
    NOT among them and must not be added: it is a RESOLVER_SYMBOL, and the real
    resolver already returns None under an empty redirected home, so a double
    would buy nothing and would forfeit the anti-mock invariant.

    ========================= NON-VACUITY (source-revert) ===================
    session_dir is "" when session_id is missing. Replace the guarded
    _archive_clause in hooks/session_init.py with a blind interpolation of
    session_dir, then run:
        python3 -m pytest tests/test_session_init_integration.py -k missing_session_id
    EXPECTED cardinality: {1 failed} — the blind form emits "archived it into
     as compact-summary-<timestamp>.txt", naming an empty directory, so the
    fallback-wording assertion fails. Restore -> green. The real-session_id case
    is the positive control: it fails in the opposite direction if resolution
    ever returns "" for a well-formed session.
    ==========================================================================
    """

    def test_unknown_frame_gets_the_notice_and_the_archive_instruction(
        self, tmp_path, monkeypatch
    ):
        """PAIRED UNKNOWN ARM, at the real-composition seam.

        The two arms below drive a LEAD frame. This arm drives the UNKNOWN
        frame, which is a no-`--agent` PRIMARY frame and takes the SAME compact
        ladder, so the seam covers both branches of the role gate rather than
        trading one for the other. It runs the assembled hook, so it also proves
        the branch survives composition and is not an artefact of the unit
        mocks.

        THE NOTICE IS WHAT SEPARATES THE TWO ROLES HERE. Both frames get the
        ladder and the archive instruction, and only the unknown frame gets the
        notice, so a collapse of the two roles reddens this arm.
        """
        sid = "aabb1122-0000-0000-0000-000000000000"
        ctx = _run_compact_main(tmp_path, monkeypatch, sid, agent_type=None)

        assert "YOUR PACT ROLE: orchestrator." in ctx
        assert _UNKNOWN_ROLE_FRAGMENT in ctx
        # The archive instruction rides the compact ladder, so a primary frame
        # that compacts receives it too.
        assert "compact-summary.txt" in ctx

    def test_real_session_id_names_the_resolved_archive_directory(
        self, tmp_path, monkeypatch
    ):
        sid = "aabb1122-0000-0000-0000-000000000000"
        ctx = _run_compact_main(tmp_path, monkeypatch, sid)

        # The READ TARGET is session-scoped (#1504): the instruction names the
        # file inside the resolved session dir — the writer put it there, so
        # the lead is sent where it actually is, not to the root singleton.
        assert f"/{sid}/compact-summary.txt" in ctx
        # The archive directory is the REAL resolved one. Asserted from the
        # inputs (home + session id), never by re-deriving the resolver, so a
        # resolver returning "" or the wrong dir fails here.
        assert f"archived it into {tmp_path}" in ctx
        assert sid in ctx
        assert "names the path in its briefing" not in ctx, (
            "the guarded fallback fired for a well-formed session — "
            "session-dir resolution returned empty when it should not"
        )

    def test_the_archive_instruction_allows_an_unattributed_summary_without_naming_it(
        self, tmp_path, monkeypatch
    ):
        """A summary no transcript attributes to this session is parked and never
        promoted, so compact-summary.txt can be absent without an archive. The
        lead is told to continue from its own context, and is never pointed at a
        parked file, which may hold a teammate's summary."""
        ctx = _run_compact_main(tmp_path, monkeypatch, "aabb1122-0000-0000-0000-000000000000")

        assert f"(if it is absent, either the secretary archived it into {tmp_path}" in ctx
        assert (
            "or it was not attributed to this session and you continue from the "
            "summary already in your context)" in ctx
        )
        assert "unattributed" not in ctx

    def test_missing_session_id_falls_back_instead_of_naming_an_empty_dir(
        self, tmp_path, monkeypatch
    ):
        ctx = _run_compact_main(tmp_path, monkeypatch, None)

        # The DEGRADED branch must follow the degraded WRITE: no session_id
        # means the writer fell back to the root singleton, so the pointer
        # names the sid-free ROOT path. The contiguous fragment cannot match
        # a session-scoped pointer (pact-sessions/{slug}/{sid}/...), so a
        # scoped misroute reddens here — the bare filename substring could not
        # tell the two apart (F-TEST-1).
        assert "pact-sessions/compact-summary.txt" in ctx
        assert "names the path in its briefing" in ctx
        # The two failure shapes a blind interpolation would produce.
        assert "archived it into  as" not in ctx
        assert "unknown-" not in ctx, (
            "the unknown-* fallback session id must never reach an instruction "
            "the lead may act on"
        )
