"""A worktree writer during a git failure writes NOTHING (#1653 item 4).

`project_memory_root` fails safe to its input, which during a git failure would
re-plant a CLAUDE.md in the worktree. Every WRITER that resolves the project
memory root therefore uses `project_memory_root_or_none` and skips when the
root is UNKNOWN: the session-start writers (session_init steps 3/3b/5b) and the
prompt-time writers (`pin_marker_writer`, `bootstrap_marker_writer`,
`realign_team`'s CLAUDE.md line).

Arms, one failing-git arm per writer plus a working-git control:
  - session start: no CLAUDE.md in the worktree OR the main checkout, and a
    "skipped" status reaches systemMessages;
  - session-start stale-pin step: the worktree copy does not gain a STALE row;
  - pin markers: the status is `noop_memory_root_unknown` and the worktree
    copy is byte-unchanged;
  - bootstrap write-back: the returned skip status names the unknown root and
    the worktree copy is byte-unchanged;
  - realign team line: the same skip reaches stderr and the worktree copy is
    byte-unchanged;
  - prompt gate: the returned status names the skip and the worktree's session
    block is not rewritten;
  - memory sync: the result is ROOT_UNKNOWN and neither checkout gains the
    entry;
  - archive resolution: `_Unevaluable` names the unknown root and neither
    checkout is touched (the archive is a writer: it saves through the CLI);
  - each control keeps the layout and gives git its answer: the same writer
    runs and updates MAIN, so an absence above is a measured skip rather than
    a fixture that never reached the writer.

`test_project_memory_root.py` pins the helper's None/answer distinction; this
module pins the writer consequences.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
from unittest.mock import patch

import pytest

import session_init
import shared.project_scope as project_scope

LEAD = "PACT:pact-orchestrator"
SESSION_ID = "dddddddd-eeee-ffff-0000-111111111111"


def _git(*args, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "-c", "init.defaultBranch=main", *args],
        cwd=str(cwd), env=env, capture_output=True, text=True,
        timeout=30, check=True,
    ).stdout


@pytest.fixture
def repo(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    _git("init", "-q", ".", cwd=main)
    (main / "README").write_text("seed\n")
    _git("add", ".", cwd=main)
    _git("commit", "-qm", "seed", cwd=main)
    return main


def _worktree(repo, name):
    path = repo / ".worktrees" / name
    _git("worktree", "add", "-q", str(path), "-b", name, cwd=repo)
    return path


def _run(monkeypatch, project_dir):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.chdir(project_dir)
    stdin_data = json.dumps(
        {"session_id": SESSION_ID, "source": "startup", "agent_type": LEAD}
    )
    with patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc:
            session_init.main()
    assert exc.value.code == 0
    output = json.loads(mock_stdout.getvalue())
    return (
        output.get("hookSpecificOutput", {}).get("additionalContext", ""),
        output.get("systemMessage", ""),
    )


def test_a_git_failure_skips_the_session_start_writers(repo, monkeypatch):
    worktree = _worktree(repo, "failing-git")
    monkeypatch.setattr(project_scope, "run_git", lambda *args, **kwargs: None)

    _context, system_message = _run(monkeypatch, worktree)

    assert "Project CLAUDE.md skipped" in system_message, (
        "the unknown memory root did not surface a skip; "
        f"system_message={system_message[:300]!r}"
    )
    assert "could not answer" in system_message
    assert not (worktree / ".claude" / "CLAUDE.md").exists(), (
        "a git failure re-planted a CLAUDE.md in the worktree"
    )
    assert not (worktree / "CLAUDE.md").exists()
    assert not (repo / ".claude" / "CLAUDE.md").exists(), (
        "a git failure wrote to an unverified main checkout"
    )


def test_the_control_with_a_working_git_creates_main(repo, monkeypatch):
    worktree = _worktree(repo, "working-git")

    context, _system_message = _run(monkeypatch, worktree)

    assert (repo / ".claude" / "CLAUDE.md").exists(), (
        "control: the same frame with working git did not create MAIN's file, "
        "so the skip arm never reached the writer"
    )
    assert "Created project CLAUDE.md" in context
    assert not (worktree / ".claude" / "CLAUDE.md").exists()
    assert not (worktree / "CLAUDE.md").exists()


# ---------------------------------------------------------------------------
# Prompt-time writers (#1653 item 4, widened by the lead's #54 note).
# ---------------------------------------------------------------------------

def _session_with_session_start(tmp_path):
    """A session dir whose journal already holds `session_start`, which is the
    precondition the bootstrap and realign CLAUDE.md writes share."""
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    (session_dir / "session-journal.jsonl").write_text(
        json.dumps(
            {"v": 1, "type": "session_start", "ts": "2026-01-01T00:00:00Z"}
        ) + "\n",
        encoding="utf-8",
    )
    return session_dir


def _patch_bootstrap_context(monkeypatch, writer, project_dir, session_dir):
    """Make the write-back see a lead frame with a divergent team name, at
    `project_dir`, without touching pact_context's real on-disk state."""
    ctx = {"team_name": "session-old", "started_at": "2026-01-01T00:00:00Z"}
    monkeypatch.setattr(writer.pact_context, "is_lead", lambda frame: True)
    monkeypatch.setattr(writer.pact_context, "get_team_name", lambda: "session-new")
    monkeypatch.setattr(writer.pact_context, "get_pact_context", lambda: dict(ctx))
    monkeypatch.setattr(writer.pact_context, "get_session_id", lambda: "sid")
    monkeypatch.setattr(
        writer.pact_context, "get_session_dir", lambda: str(session_dir)
    )
    monkeypatch.setattr(writer.pact_context, "get_plugin_root", lambda: "/plugin")
    monkeypatch.setattr(
        writer.pact_context, "get_project_dir", lambda: str(project_dir)
    )
    monkeypatch.setattr(writer.pact_context, "write_context", lambda *a, **k: None)


def test_a_git_failure_skips_the_pin_marker_writer(repo, monkeypatch):
    from test_pin_marker_writer import build_claude_md

    import pin_marker_writer

    worktree = _worktree(repo, "pin-marker-git-failure")
    original = build_claude_md()
    worktree_md = worktree / ".claude" / "CLAUDE.md"
    worktree_md.parent.mkdir()
    worktree_md.write_text(original, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
    monkeypatch.chdir(worktree)
    monkeypatch.setattr(project_scope, "run_git", lambda *args, **kwargs: None)

    outcome = pin_marker_writer._plan_and_write()

    assert outcome == "noop_memory_root_unknown", (
        f"the unknown memory root did not skip the pin writer: {outcome}"
    )
    assert worktree_md.read_text(encoding="utf-8") == original, (
        "a git failure planted pin markers in the worktree"
    )


def test_the_pin_marker_writer_still_writes_main_with_working_git(repo, monkeypatch):
    from shared.pin_markers import START_LINE
    from test_pin_marker_writer import build_claude_md

    import pin_marker_writer

    worktree = _worktree(repo, "pin-marker-working-git")
    original = build_claude_md()
    main_md = repo / ".claude" / "CLAUDE.md"
    main_md.parent.mkdir()
    main_md.write_text(original, encoding="utf-8")
    worktree_md = worktree / "CLAUDE.md"
    worktree_md.write_text(original, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
    monkeypatch.chdir(worktree)

    outcome = pin_marker_writer._plan_and_write()

    assert outcome == "written", (
        f"control: the pin writer did not run: {outcome}"
    )
    assert START_LINE in main_md.read_text(encoding="utf-8")
    assert worktree_md.read_text(encoding="utf-8") == original


def test_a_git_failure_skips_the_bootstrap_write_back(repo, monkeypatch):
    import bootstrap_marker_writer as writer

    worktree = _worktree(repo, "bootstrap-git-failure")
    original = "# WT\n"
    worktree_md = worktree / ".claude" / "CLAUDE.md"
    worktree_md.parent.mkdir()
    worktree_md.write_text(original, encoding="utf-8")
    session_dir = _session_with_session_start(repo)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
    monkeypatch.chdir(worktree)
    monkeypatch.setattr(project_scope, "run_git", lambda *args, **kwargs: None)
    _patch_bootstrap_context(monkeypatch, writer, worktree, session_dir)

    status = writer._write_back_aligned_team_name({"agent_type": LEAD})

    assert status is not None and "Project CLAUDE.md skipped" in status, (
        f"the unknown memory root did not surface a skip: {status!r}"
    )
    assert "could not answer" in status
    assert worktree_md.read_text(encoding="utf-8") == original, (
        "a git failure wrote the session block into the worktree"
    )


def test_the_bootstrap_write_back_updates_main_with_working_git(repo, monkeypatch):
    import bootstrap_marker_writer as writer

    worktree = _worktree(repo, "bootstrap-working-git")
    main_md = repo / ".claude" / "CLAUDE.md"
    main_md.parent.mkdir()
    main_md.write_text("# MAIN\n", encoding="utf-8")
    worktree_md = worktree / ".claude" / "CLAUDE.md"
    worktree_md.parent.mkdir()
    worktree_md.write_text("# WT\n", encoding="utf-8")
    session_dir = _session_with_session_start(repo)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
    monkeypatch.chdir(worktree)
    _patch_bootstrap_context(monkeypatch, writer, worktree, session_dir)

    status = writer._write_back_aligned_team_name({"agent_type": LEAD})

    assert status is None, f"control: an unexpected status: {status!r}"
    assert "session-new" in main_md.read_text(encoding="utf-8"), (
        "control: the same write-back did not reach MAIN"
    )
    assert worktree_md.read_text(encoding="utf-8") == "# WT\n"


def test_a_git_failure_skips_the_realign_team_line(repo, monkeypatch, capsys):
    from shared.realign_team import _write_team_line

    worktree = _worktree(repo, "realign-git-failure")
    original = "# WT\n"
    worktree_md = worktree / ".claude" / "CLAUDE.md"
    worktree_md.parent.mkdir()
    worktree_md.write_text(original, encoding="utf-8")
    session_dir = _session_with_session_start(repo)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
    monkeypatch.chdir(worktree)
    monkeypatch.setattr(project_scope, "run_git", lambda *args, **kwargs: None)

    _write_team_line("sid", "session-new", str(session_dir), "/plugin")

    err = capsys.readouterr().err
    assert "project CLAUDE.md skipped" in err, (
        f"the unknown memory root did not surface a skip: {err!r}"
    )
    assert "could not answer" in err
    assert worktree_md.read_text(encoding="utf-8") == original, (
        "a git failure wrote the team line into the worktree"
    )


def test_the_realign_team_line_updates_main_with_working_git(repo, monkeypatch):
    from shared.realign_team import _write_team_line

    worktree = _worktree(repo, "realign-working-git")
    main_md = repo / ".claude" / "CLAUDE.md"
    main_md.parent.mkdir()
    main_md.write_text("# MAIN\n", encoding="utf-8")
    worktree_md = worktree / ".claude" / "CLAUDE.md"
    worktree_md.parent.mkdir()
    worktree_md.write_text("# WT\n", encoding="utf-8")
    session_dir = _session_with_session_start(repo)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
    monkeypatch.chdir(worktree)

    _write_team_line("sid", "session-new", str(session_dir), "/plugin")

    assert "session-new" in main_md.read_text(encoding="utf-8"), (
        "control: the same realign line did not reach MAIN"
    )
    assert worktree_md.read_text(encoding="utf-8") == "# WT\n"


# ---------------------------------------------------------------------------
# The remaining writers found by the #57 census: session_init's stale-pin
# step, bootstrap_prompt_gate's session-block rewrite, working_memory's two
# sync writers, and archive_pin's resolution (a writer: it archives through
# the memory CLI).
# ---------------------------------------------------------------------------

def _managed_doc(pins: str = "") -> str:
    """A managed CLAUDE.md with an empty pinned section (or `pins`)."""
    from shared.claude_md_manager import (
        MANAGED_END_MARKER,
        MANAGED_START_MARKER,
        MANAGED_TITLE,
        MEMORY_END_MARKER,
        MEMORY_START_MARKER,
    )

    return (
        f"{MANAGED_START_MARKER}\n{MANAGED_TITLE}\n\n{MEMORY_START_MARKER}\n"
        "## Retrieved Context\n\n"
        f"## Pinned Context\n\n{pins}"
        "## Working Memory\n"
        f"{MEMORY_END_MARKER}\n\n{MANAGED_END_MARKER}\n"
    )


def _stale_capable_pin() -> str:
    """A pin whose ENTRY BODY carries an old date: `detect_stale_entries` reads
    the date from the entry text (heading + body), not the pin comment."""
    return (
        "<!-- pinned: 2026-10-01 -->\n### Old A\n"
        "Last relevant 2020-01-05, PR #1, merged 2020-01-05\n\n"
    )


def test_a_git_failure_skips_the_session_start_staleness_step(repo, monkeypatch):
    worktree = _worktree(repo, "staleness-git-failure")
    original = _managed_doc(_stale_capable_pin())
    worktree_md = worktree / ".claude" / "CLAUDE.md"
    worktree_md.parent.mkdir()
    worktree_md.write_text(original, encoding="utf-8")
    monkeypatch.setattr(project_scope, "run_git", lambda *args, **kwargs: None)

    _context, system_message = _run(monkeypatch, worktree)

    assert "Project CLAUDE.md skipped" in system_message
    assert worktree_md.read_text(encoding="utf-8") == original, (
        "a git failure let session_init's stale-pin step mark the worktree"
    )


def test_the_session_start_staleness_step_still_marks_main(repo, monkeypatch):
    worktree = _worktree(repo, "staleness-working-git")
    main_md = repo / ".claude" / "CLAUDE.md"
    main_md.parent.mkdir()
    main_md.write_text(_managed_doc(_stale_capable_pin()), encoding="utf-8")
    worktree_md = worktree / ".claude" / "CLAUDE.md"
    worktree_md.parent.mkdir()
    original = _managed_doc(_stale_capable_pin())
    worktree_md.write_text(original, encoding="utf-8")

    _run(monkeypatch, worktree)

    assert "<!-- STALE" in main_md.read_text(encoding="utf-8"), (
        "control: the same start did not mark MAIN's stale pin"
    )
    assert worktree_md.read_text(encoding="utf-8") == original


def _prompt_gate_frame():
    return {
        "session_id": SESSION_ID,
        "hook_event_name": "UserPromptSubmit",
        "agent_type": LEAD,
    }


def _seed_session_blocks(monkeypatch, writer, main, worktree, session_dir):
    """Give both checkouts a FOUND session block naming SESSION_ID, and pin the
    team name the rewrite must change. Patched via monkeypatch: `writer` and
    `wm` both hold the SHARED pact_context module, so a direct assignment would
    leak into every later test in the process."""
    from shared.session_resume import update_session_info

    for root in (main, worktree):
        (root / ".claude").mkdir(exist_ok=True)
        update_session_info(
            SESSION_ID, "old-team", str(session_dir), "/plugin", project_dir=root
        )
    monkeypatch.setattr(writer.pact_context, "get_team_name", lambda: "session-new")
    monkeypatch.setattr(writer.pact_context, "get_plugin_root", lambda: "/plugin")
    monkeypatch.setattr(writer.pact_context, "get_lead_address", lambda: "lead")


def test_a_git_failure_skips_the_prompt_gate_block_rewrite(repo, monkeypatch):
    import bootstrap_prompt_gate as writer

    worktree = _worktree(repo, "prompt-gate-git-failure")
    session_dir = _session_with_session_start(repo)
    _seed_session_blocks(monkeypatch, writer, repo, worktree, session_dir)
    worktree_md = worktree / ".claude" / "CLAUDE.md"
    original = worktree_md.read_text(encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
    monkeypatch.chdir(worktree)
    monkeypatch.setattr(project_scope, "run_git", lambda *args, **kwargs: None)

    out = writer._record_unrecorded_lead(_prompt_gate_frame(), str(session_dir))

    assert "Project CLAUDE.md skipped" in out, (
        f"the unknown root was not reported: {out[-300:]!r}"
    )
    assert worktree_md.read_text(encoding="utf-8") == original, (
        "a git failure let the prompt gate rewrite the worktree's block"
    )


def test_the_prompt_gate_block_rewrite_updates_main(repo, monkeypatch):
    import bootstrap_prompt_gate as writer

    worktree = _worktree(repo, "prompt-gate-working-git")
    session_dir = _session_with_session_start(repo)
    _seed_session_blocks(monkeypatch, writer, repo, worktree, session_dir)
    worktree_md = worktree / ".claude" / "CLAUDE.md"
    original = worktree_md.read_text(encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
    monkeypatch.chdir(worktree)

    out = writer._record_unrecorded_lead(_prompt_gate_frame(), str(session_dir))

    assert "session-new" in out
    assert "session-new" in (repo / ".claude" / "CLAUDE.md").read_text(
        encoding="utf-8"
    ), "control: the same prompt did not rewrite MAIN's block"
    assert worktree_md.read_text(encoding="utf-8") == original


def _display_doc() -> str:
    return "# Doc\n\n## Working Memory\n<!-- Auto-managed by pact-memory skill. -->\n"


def _align_memory_scope(monkeypatch, wm, worktree):
    """Point the session record at the worktree and silence the ambient
    disagreement guard, so the arm exercises the root, not the guard. Patched
    via monkeypatch so neither lambda outlives its test."""
    monkeypatch.setattr(
        wm, "get_project_dir_from_session_record", lambda: str(worktree)
    )
    monkeypatch.setattr(wm, "env_record_project_dir_disagreement", lambda: None)


def test_a_git_failure_skips_the_memory_sync(repo, monkeypatch):
    import scripts.working_memory as wm

    worktree = _worktree(repo, "memory-sync-git-failure")
    main_md = repo / ".claude" / "CLAUDE.md"
    main_md.parent.mkdir()
    main_md.write_text(_display_doc(), encoding="utf-8")
    worktree_md = worktree / ".claude" / "CLAUDE.md"
    worktree_md.parent.mkdir()
    original = _display_doc()
    worktree_md.write_text(original, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
    monkeypatch.chdir(worktree)
    _align_memory_scope(monkeypatch, wm, worktree)

    def _no_git(*args, **kwargs):
        raise FileNotFoundError("git missing")

    monkeypatch.setattr(wm.subprocess, "run", _no_git)

    result = wm.sync_to_claude_md({"context": "external entry"}, None, "id1")

    assert result.reason == wm.SyncResult.ROOT_UNKNOWN, (
        f"the unknown root was not named: {result.reason}"
    )
    assert "external entry" not in worktree_md.read_text(encoding="utf-8"), (
        "a git failure let the memory sync write the worktree"
    )


def test_the_memory_sync_still_writes_main(repo, monkeypatch):
    import scripts.working_memory as wm

    worktree = _worktree(repo, "memory-sync-working-git")
    main_md = repo / ".claude" / "CLAUDE.md"
    main_md.parent.mkdir()
    main_md.write_text(_display_doc(), encoding="utf-8")
    worktree_md = worktree / ".claude" / "CLAUDE.md"
    worktree_md.parent.mkdir()
    original = _display_doc()
    worktree_md.write_text(original, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
    monkeypatch.chdir(worktree)
    _align_memory_scope(monkeypatch, wm, worktree)

    result = wm.sync_to_claude_md({"context": "external entry"}, None, "id2")

    assert result.reason == wm.SyncResult.WROTE, (
        f"control: the same sync did not run: {result.reason}"
    )
    assert "external entry" in main_md.read_text(encoding="utf-8")
    assert worktree_md.read_text(encoding="utf-8") == original


def test_a_git_failure_refuses_the_archive_resolution(repo, monkeypatch):
    import archive_pin

    worktree = _worktree(repo, "archive-git-failure")
    doc = _managed_doc(_stale_capable_pin())
    main_md = repo / ".claude" / "CLAUDE.md"
    main_md.parent.mkdir()
    main_md.write_text(doc, encoding="utf-8")
    worktree_md = worktree / ".claude" / "CLAUDE.md"
    worktree_md.parent.mkdir()
    worktree_md.write_text(doc, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
    monkeypatch.chdir(worktree)
    monkeypatch.setattr(project_scope, "run_git", lambda *args, **kwargs: None)

    with pytest.raises(archive_pin._Unevaluable) as excinfo:
        archive_pin.resolve_claude_md()

    assert "project memory root unknown" in str(excinfo.value)
    assert worktree_md.read_text(encoding="utf-8") == doc
    assert main_md.read_text(encoding="utf-8") == doc


def test_the_archive_resolution_still_names_main(repo, monkeypatch):
    import archive_pin

    worktree = _worktree(repo, "archive-working-git")
    doc = _managed_doc(_stale_capable_pin())
    main_md = repo / ".claude" / "CLAUDE.md"
    main_md.parent.mkdir()
    main_md.write_text(doc, encoding="utf-8")
    worktree_md = worktree / ".claude" / "CLAUDE.md"
    worktree_md.parent.mkdir()
    worktree_md.write_text(doc, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
    monkeypatch.chdir(worktree)

    path, _base = archive_pin.resolve_claude_md()

    assert path.resolve() == main_md.resolve(), (
        f"control: the archive resolved {path}, not MAIN's file"
    )
