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

GIT UNAVAILABILITY IS ONE FAILURE SHAPE, NOT ALL OF THEM. Every failing-git
arm here empties PATH (`_hide_git`), so the binary lookup fails in
`project_scope.run_git` AND in every direct `subprocess.run(["git", ...])` an
arm's code path can reach. Patching only `run_git` left those direct calls on
the real binary (#59). THIS COVERS "COULD NOT RUN" ONLY. A git that RUNS and
exits non-zero is a KNOWN answer -- the helper treats the directory itself as
the root -- which is correct for a directory OUTSIDE git, but NOT for a real
worktree whose repo is broken: a moved or deleted main checkout, a
dubious-ownership refusal, a permission-denied .git. There the fallback
re-plants the worktree copy. That residual is the ACCEPTED TRADE-OFF (the
alternative skips project-memory maintenance on every repo hiccup), and it is
pinned by `test_a_moved_main_checkout_is_a_known_root_and_creates_the_copy`
so it stays visible rather than implied.
"""
from __future__ import annotations

import io
import json
import logging
import os
import shutil
import subprocess
from unittest.mock import patch

import pytest

import session_init

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


def _forged_gitfile(repo, tmp_path, name):
    """A directory whose `.git` FILE names a REAL worktree's admin dir.

    git RAN on this directory and answered -- the answer names another
    checkout -- so it is the VERIFICATION-FAILURE shape, not a git failure.
    The worktree listing does not contain the asking directory, which is what
    makes the answer fail verification."""
    real = _worktree(repo, name + "-target")
    admin = _git("rev-parse", "--absolute-git-dir", cwd=real).strip()
    forged = tmp_path / name
    forged.mkdir()
    (forged / ".git").write_text(f"gitdir: {admin}\n", encoding="utf-8")
    return forged


def _hide_git(monkeypatch):
    """Make git genuinely UNAVAILABLE: an empty PATH fails every git call at
    the binary lookup, both `project_scope.run_git` and the direct
    `subprocess.run` calls in staleness's and working_memory's resolvers.

    Patching only `run_git` leaves those direct calls on the real binary,
    which can still answer from the actual repository -- a failing-git arm
    that passes for the wrong reason (#1653 task #59). Note the distinction
    the helper itself draws: git EXITING NON-ZERO is a KNOWN answer that must
    keep working -- correct for a non-git directory, and for a broken real
    repo it falls back to the input, the documented residual -- while git
    being ABSENT is the unknown case this helper simulates."""
    monkeypatch.setenv("PATH", "")


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
    _hide_git(monkeypatch)

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


def test_a_declared_directory_that_does_not_exist_is_not_re_created(
    tmp_path, monkeypatch
):
    """F1: `git -C <missing>` exits 128 exactly as `git -C <non-repo>` does,
    so the classifier called the missing dir a KNOWN root and SessionStart
    created it. THE ASSERTION IS ON THE DIRECTORY, not only on a planted file:
    a session that re-creates the path and then writes nothing leaves the
    check for `.claude/CLAUDE.md` green while the bug is still present."""
    missing = tmp_path / "deleted-project"
    assert not missing.exists()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(missing))
    monkeypatch.chdir(tmp_path)

    stdin_data = json.dumps(
        {"session_id": SESSION_ID, "source": "startup", "agent_type": LEAD}
    )
    with patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc:
            session_init.main()

    assert exc.value.code == 0
    assert not missing.exists(), (
        "SessionStart re-created a declared directory that does not exist"
    )
    system_message = json.loads(mock_stdout.getvalue()).get("systemMessage", "")
    assert "Project CLAUDE.md skipped" in system_message


def _run_with_declaration(monkeypatch, cwd, declared):
    """Run SessionStart with `cwd` as the process directory and `declared` as
    CLAUDE_PROJECT_DIR. Separate from `_run` because the declared directory
    may not exist, and `_run` chdirs into it."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(declared))
    monkeypatch.chdir(cwd)
    stdin_data = json.dumps(
        {"session_id": SESSION_ID, "source": "startup", "agent_type": LEAD}
    )
    with patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc:
            session_init.main()
    assert exc.value.code == 0
    return json.loads(mock_stdout.getvalue())


def test_a_removed_worktree_declaration_leaves_main_clean(repo, monkeypatch):
    """F1, the runtime lane's stronger shape: a worktree that `git worktree
    remove --force` deleted. The DIRECTORY assertion alone would pass on a
    re-created path with nothing in it, so this also asserts MAIN's working
    tree stays clean -- that catches a planted directory, and any future
    variant that plants something other than CLAUDE.md."""
    worktree = _worktree(repo, "f1-removed")
    _git("worktree", "remove", "--force", str(worktree), cwd=repo)
    assert not worktree.exists()

    _run_with_declaration(monkeypatch, repo, worktree)

    assert not worktree.exists(), (
        "SessionStart re-created a removed worktree directory"
    )
    status = _git("status", "--porcelain", cwd=repo)
    assert ".worktrees/" not in status, (
        f"SessionStart planted something in MAIN's worktrees dir: {status!r}"
    )


def test_a_ghost_worktree_declaration_leaves_main_clean(repo, monkeypatch):
    """The trigger is the DECLARATION, not the cwd: a path that never existed
    must not be created either."""
    ghost = repo / ".worktrees" / "never-created"
    assert not ghost.exists()

    _run_with_declaration(monkeypatch, repo, ghost)

    assert not ghost.exists()
    status = _git("status", "--porcelain", cwd=repo)
    assert ".worktrees/" not in status, (
        f"SessionStart planted something for a ghost declaration: {status!r}"
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
    _hide_git(monkeypatch)

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
    _hide_git(monkeypatch)
    _patch_bootstrap_context(monkeypatch, writer, worktree, session_dir)

    status = writer._write_back_aligned_team_name({"agent_type": LEAD})

    assert status is not None and "Project CLAUDE.md skipped" in status, (
        f"the unknown memory root did not surface a skip: {status!r}"
    )
    assert "could not answer" in status
    assert worktree_md.read_text(encoding="utf-8") == original, (
        "a git failure wrote the session block into the worktree"
    )


def test_a_forged_gitfile_names_the_verification_failure_in_the_write_back(
    repo, tmp_path, monkeypatch
):
    """THE THIRD CAUSE in the bootstrap marker writer's skip line: git RAN and
    answered, and the answer failed verification. The skip must say that, not
    that git could not answer and not that the directory is gone."""
    import bootstrap_marker_writer as writer

    forged = _forged_gitfile(repo, tmp_path, "bootstrap-forge")
    session_dir = _session_with_session_start(repo)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(forged))
    monkeypatch.chdir(forged)
    _patch_bootstrap_context(monkeypatch, writer, forged, session_dir)

    status = writer._write_back_aligned_team_name({"agent_type": LEAD})

    assert status is not None and "Project CLAUDE.md skipped" in status, status
    assert (
        "exists but could not be verified as a checkout of a repository"
        in status
    ), status
    assert "git could not answer" not in status, status
    assert "no longer exists" not in status, status
    assert str(forged) in status, status


def test_the_write_back_reports_nothing_when_no_cause_is_identified(
    repo, tmp_path, monkeypatch
):
    """Negative control: the guard believes the root is unknown while the
    cause resolver says otherwise, so the channel emits NOTHING."""
    import bootstrap_marker_writer as writer

    forged = _forged_gitfile(repo, tmp_path, "no-cause-write-back")
    session_dir = _session_with_session_start(repo)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(forged))
    monkeypatch.chdir(forged)
    _patch_bootstrap_context(monkeypatch, writer, forged, session_dir)
    monkeypatch.setattr(writer, "unknown_root_cause", lambda pd: None)

    status = writer._write_back_aligned_team_name({"agent_type": LEAD})

    assert status is None, status


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
    _hide_git(monkeypatch)

    _write_team_line("sid", "session-new", str(session_dir), "/plugin")

    err = capsys.readouterr().err
    assert "project CLAUDE.md skipped" in err, (
        f"the unknown memory root did not surface a skip: {err!r}"
    )
    assert "could not answer" in err
    assert worktree_md.read_text(encoding="utf-8") == original, (
        "a git failure wrote the team line into the worktree"
    )


def test_a_forged_gitfile_names_the_verification_failure_in_realign_team(
    repo, tmp_path, monkeypatch, capsys
):
    """THE THIRD CAUSE on realign_team's stderr line: git RAN and answered, and
    the answer failed verification."""
    from shared.realign_team import _write_team_line

    forged = _forged_gitfile(repo, tmp_path, "realign-forge")
    session_dir = _session_with_session_start(repo)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(forged))
    monkeypatch.chdir(forged)

    _write_team_line("sid", "session-new", str(session_dir), "/plugin")

    err = capsys.readouterr().err
    assert "project CLAUDE.md skipped" in err, err
    assert (
        "exists but could not be verified as a checkout of a repository" in err
    ), err
    assert "git could not answer" not in err, err
    assert "no longer exists" not in err, err
    assert str(forged) in err, err


def test_realign_reports_nothing_when_no_cause_is_identified(
    repo, tmp_path, monkeypatch, capsys
):
    """Negative control: the guard believes the root is unknown while the
    cause resolver says otherwise, so nothing reaches stderr."""
    import shared.project_scope as project_scope
    from shared.realign_team import _write_team_line

    forged = _forged_gitfile(repo, tmp_path, "no-cause-realign")
    session_dir = _session_with_session_start(repo)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(forged))
    monkeypatch.chdir(forged)
    monkeypatch.setattr(project_scope, "unknown_root_cause", lambda pd: None)

    _write_team_line("sid", "session-new", str(session_dir), "/plugin")

    err = capsys.readouterr().err
    assert err == "", err


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
    _hide_git(monkeypatch)

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


def _stale_marked_pin(heading: str = "Old A") -> str:
    """A pin the stale-block reader counts: it reads the STALE marker in the
    body, not the pin date, so step 4 cannot be what puts it there. Two of
    them are needed to reach PIN_STALE_BLOCK_THRESHOLD."""
    return (
        f"<!-- pinned: 2026-10-01 -->\n### {heading}\n"
        "body <!-- STALE: Last relevant 2020-01-05 -->\n\n"
    )


def _stale_pair() -> str:
    return _stale_marked_pin("Old A") + _stale_marked_pin("Old B")


def test_a_git_failure_emits_no_pin_advisories_from_the_worktree_copy(
    repo, monkeypatch
):
    """F2: the unknown-root refusal covered the writers but not the three
    advisory readers. A start that said it wrote nothing to either checkout
    then read the WORKTREE copy through the substituted fail-open root and
    told the user to run /PACT:prune-memory -- whose own guard refuses on the
    same root."""
    worktree = _worktree(repo, "f2-pin-advisories")
    wt_md = worktree / ".claude" / "CLAUDE.md"
    wt_md.parent.mkdir()
    wt_md.write_text(_managed_doc(_stale_pair()), encoding="utf-8")
    _hide_git(monkeypatch)

    context, system_message = _run(monkeypatch, worktree)

    assert "Project CLAUDE.md skipped" in system_message
    assert "prune-memory" not in context, (
        "the start recommended a command that refuses on this root: "
        f"{context[:400]!r}"
    )
    assert "Pin slots" not in context


def test_the_pin_advisories_still_follow_main_with_working_git(repo, monkeypatch):
    """Control for the arm above: with a known root the same readers DO
    report MAIN's stale pin, so the absence there is a measured skip rather
    than a fixture that never reached the reader."""
    worktree = _worktree(repo, "f2-pin-control")
    main_md = repo / ".claude" / "CLAUDE.md"
    main_md.parent.mkdir()
    main_md.write_text(_managed_doc(_stale_pair()), encoding="utf-8")

    context, _system_message = _run(monkeypatch, worktree)

    assert "prune-memory" in context
    assert "Pin slots" in context


def test_a_git_failure_emits_no_refusal_report_from_the_worktree_copy(
    repo, monkeypatch
):
    """F2, third reader: `check_claude_md_refusals` also read the substituted
    root, so it named the worktree copy's malformed blocks as the document
    PACT would not update."""
    from shared.claude_md_manager import MANAGED_END_MARKER, MANAGED_START_MARKER

    worktree = _worktree(repo, "f2-refusals")
    wt_md = worktree / ".claude" / "CLAUDE.md"
    wt_md.parent.mkdir()
    wt_md.write_text(
        _managed_doc() + f"{MANAGED_START_MARKER}\n{MANAGED_END_MARKER}\n",
        encoding="utf-8",
    )
    _hide_git(monkeypatch)

    _context, system_message = _run(monkeypatch, worktree)

    assert "Project CLAUDE.md skipped" in system_message
    assert "PACT could not update" not in system_message


def test_the_refusal_report_still_names_main_with_working_git(repo, monkeypatch):
    """Control for the arm above."""
    from shared.claude_md_manager import MANAGED_END_MARKER, MANAGED_START_MARKER

    worktree = _worktree(repo, "f2-refusals-control")
    main_md = repo / ".claude" / "CLAUDE.md"
    main_md.parent.mkdir()
    main_md.write_text(
        _managed_doc() + f"{MANAGED_START_MARKER}\n{MANAGED_END_MARKER}\n",
        encoding="utf-8",
    )

    _context, system_message = _run(monkeypatch, worktree)

    assert "PACT could not update" in system_message
    assert str(main_md) in system_message


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
    _hide_git(monkeypatch)

    out = writer._record_unrecorded_lead(_prompt_gate_frame(), str(session_dir))

    assert "Project CLAUDE.md skipped" in out, (
        f"the unknown root was not reported: {out[-300:]!r}"
    )
    assert worktree_md.read_text(encoding="utf-8") == original, (
        "a git failure let the prompt gate rewrite the worktree's block"
    )


def test_a_forged_gitfile_names_the_verification_failure_in_the_prompt_gate(
    repo, tmp_path, monkeypatch
):
    """THE THIRD CAUSE in the prompt gate's block status: git RAN and
    answered, and the answer failed verification."""
    import bootstrap_prompt_gate as writer

    forged = _forged_gitfile(repo, tmp_path, "prompt-gate-forge")
    session_dir = _session_with_session_start(repo)
    _seed_session_blocks(monkeypatch, writer, repo, forged, session_dir)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(forged))
    monkeypatch.chdir(forged)

    out = writer._record_unrecorded_lead(_prompt_gate_frame(), str(session_dir))

    assert "Project CLAUDE.md skipped" in out, (
        f"the unknown root was not reported: {out[-300:]!r}"
    )
    assert (
        "exists but could not be verified as a checkout of a repository" in out
    ), out[-300:]
    assert "git could not answer" not in out, out[-300:]
    assert "no longer exists" not in out, out[-300:]
    assert str(forged) in out, out[-300:]


def test_the_prompt_gate_reports_nothing_when_no_cause_is_identified(
    repo, tmp_path, monkeypatch
):
    """Negative control: the guard believes the root is unknown while the
    cause resolver says otherwise, so the gate emits no skip line."""
    import bootstrap_prompt_gate as writer
    import shared.project_scope as project_scope

    forged = _forged_gitfile(repo, tmp_path, "no-cause-prompt-gate")
    session_dir = _session_with_session_start(repo)
    _seed_session_blocks(monkeypatch, writer, repo, forged, session_dir)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(forged))
    monkeypatch.chdir(forged)
    monkeypatch.setattr(project_scope, "unknown_root_cause", lambda pd: None)

    out = writer._record_unrecorded_lead(_prompt_gate_frame(), str(session_dir))

    assert "Project CLAUDE.md skipped" not in out, out[-300:]
    assert "no longer exists" not in out, out[-300:]
    assert "could not be verified as a checkout" not in out, out[-300:]
    assert "git could not answer" not in out, out[-300:]


def test_the_archive_reports_no_unknown_cause_when_none_is_identified(
    repo, tmp_path, monkeypatch
):
    """Negative control for the refusal channel. `resolve_claude_md` is a
    RESOLUTION, not a write: with the cause resolver stubbed to None the guard
    and the resolver disagree, and the refusal must not invent one of the
    three causes. Whatever it does instead is allowed, but it may not name a
    cause that was never identified."""
    import archive_pin
    import shared.project_scope as project_scope

    forged = _forged_gitfile(repo, tmp_path, "no-cause-archive")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(forged))
    monkeypatch.chdir(forged)
    monkeypatch.setattr(project_scope, "unknown_root_cause", lambda pd: None)

    try:
        archive_pin.resolve_claude_md()
    except archive_pin._Unevaluable as exc:
        reason = str(exc.reason)
    else:
        reason = ""

    assert "no longer exists" not in reason, reason
    assert "could not be verified as a checkout" not in reason, reason
    assert "git could not answer" not in reason, reason


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


def test_a_git_failure_skips_the_memory_sync(repo, monkeypatch, caplog):
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

    _hide_git(monkeypatch)

    with caplog.at_level(logging.WARNING):
        result = wm.sync_to_claude_md({"context": "external entry"}, None, "id1")

    assert result.reason == wm.SyncResult.ROOT_UNKNOWN, (
        f"the unknown root was not named: {result.reason}"
    )
    # THE ANCHOR EXISTS HERE, so the warning must name the git failure and NOT
    # the missing directory.
    assert "git could not answer" in caplog.text, caplog.text
    assert "no longer exists" not in caplog.text, caplog.text
    assert "external entry" not in worktree_md.read_text(encoding="utf-8"), (
        "a git failure let the memory sync write the worktree"
    )


def test_a_removed_declaration_names_the_missing_directory_in_the_sync(
    tmp_path, monkeypatch, caplog
):
    """The sync warning names the cause that OCCURRED.

    A declared directory that is gone was never offered to git, so "git could
    not answer" was false there. Git stays available here for the same reason
    as the SessionStart arm: the discrimination must come from the anchor, not
    from a git that happened to be broken too."""
    import scripts.working_memory as wm

    missing = tmp_path / "gone-declaration"
    # A subdirectory, not tmp_path: the suite's autouse CLAUDE.md redirect
    # seeds tmp_path/CLAUDE.md, and the resolver's last-resort probe reads the
    # cwd exactly, so a cwd of tmp_path would find that seed and refuse on
    # scope escape before the warning this arm is about.
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(missing))
    monkeypatch.chdir(work)
    monkeypatch.setattr(wm, "get_project_dir_from_session_record", lambda: None)
    monkeypatch.setattr(wm, "env_record_project_dir_disagreement", lambda: None)

    with caplog.at_level(logging.WARNING):
        result = wm.sync_to_claude_md(
            {"context": "external entry"}, None, "missing1"
        )

    assert result.reason == wm.SyncResult.ROOT_UNKNOWN, result.reason
    assert "no longer exists" in caplog.text, caplog.text
    assert "git could not answer" not in caplog.text, caplog.text
    # NAMED, NOT JUST DESCRIBED: several worktrees can be in play and the user
    # must be able to tell which one the message is about.
    assert str(missing) in caplog.text, caplog.text


def test_a_forged_gitfile_names_the_verification_failure_in_the_sync(
    repo, tmp_path, monkeypatch, caplog
):
    """THE THIRD CAUSE. A directory whose `.git` FILE names another
    repository's worktree admin dir: git RAN and answered, and the answer
    failed verification. Saying "git could not answer" there is false, and so
    is saying the directory is gone -- it exists."""
    import scripts.working_memory as wm

    forged = _forged_gitfile(repo, tmp_path, "forged-sync")

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(forged))
    monkeypatch.chdir(forged)
    monkeypatch.setattr(wm, "get_project_dir_from_session_record", lambda: None)
    monkeypatch.setattr(wm, "env_record_project_dir_disagreement", lambda: None)

    with caplog.at_level(logging.WARNING):
        result = wm.sync_to_claude_md({"context": "external entry"}, None, "forge1")

    assert result.reason == wm.SyncResult.ROOT_UNKNOWN, result.reason
    assert (
        "exists but could not be verified as a checkout of a repository"
        in caplog.text
    ), caplog.text
    assert "no longer exists" not in caplog.text, caplog.text
    assert "git could not answer" not in caplog.text, caplog.text
    assert str(forged) in caplog.text, caplog.text


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
    _hide_git(monkeypatch)

    with pytest.raises(archive_pin._Unevaluable) as excinfo:
        archive_pin.resolve_claude_md()

    reason = str(excinfo.value)
    assert "project memory root unknown" in reason
    # The anchor refusal specifically; the base sibling shares the prefix.
    assert "git could not answer" in reason, reason
    assert "could not classify" not in reason, reason
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


def test_a_moved_main_checkout_is_a_known_root_and_creates_the_copy(
    repo, monkeypatch
):
    """R-2: exit 128 is a KNOWN answer, not "git could not answer".

    A worktree whose gitfile names a moved or deleted main checkout exits 128
    on every rev-parse. `project_memory_root_or_none` therefore returns the
    directory itself, and a lead SessionStart writes `.claude/CLAUDE.md` there
    -- the worktree write #1653 exists to keep out. THIS IS THE ACCEPTED
    TRADE-OFF, asserted rather than implied: making exit 128 "unknown" would
    skip project-memory maintenance in every repo on any git hiccup. The
    comment at the top of this module names the other causes in this class
    (dubious ownership, permission-denied .git) that the PATH-emptied arms do
    not cover.
    """
    from shared.project_scope import project_memory_root_or_none

    gone = repo / ".worktrees" / "moved-main"
    gone.mkdir(parents=True)
    (gone / ".git").write_text(
        "gitdir: /nonexistent-main-checkout/.git/worktrees/moved-main\n",
        encoding="utf-8",
    )
    probe = subprocess.run(
        ["git", "-C", str(gone), "rev-parse", "--git-dir"],
        capture_output=True, text=True, timeout=30,
    )
    assert probe.returncode != 0, "precondition: a broken gitfile must fail"

    assert project_memory_root_or_none(str(gone)) == gone, (
        "exit 128 stopped being a known answer; if that is the new rule, "
        "re-rate this arm and the module's trade-off paragraph"
    )

    _context, system_message = _run(monkeypatch, gone)

    assert "Project CLAUDE.md skipped" not in system_message
    assert (gone / ".claude" / "CLAUDE.md").exists(), (
        "the documented residual changed: the broken-repo worktree no longer "
        "receives the copy, so the trade-off text is stale"
    )


def test_an_invalid_utf8_worktree_path_still_starts_the_session(repo, monkeypatch):
    """F1 arm D: an undecodable path must not fail the SessionStart.

    Before the fix, `project_memory_root_or_none` raised UnicodeDecodeError
    inside session_init, the safety net emitted "PACT hook warning ... codec
    can't decode byte", and the team was NOT generated. Now the root
    classifies and the launch is normal.
    """
    name = os.fsdecode(b"f1-\xff")
    worktree = repo / ".worktrees" / name
    _git(
        "worktree", "add", "-q", str(worktree), "-b", "f1-d",
        cwd=repo,
    )
    context, system_message = _run(monkeypatch, worktree)

    assert "codec" not in system_message, system_message[:300]
    assert "NOT GENERATED" not in context, context[:300]
    assert "Your team is `session-" in context


# --- D1/D2/D3 (#1653): one ANCHOR, not yet one document-resolution rule -----

_D1_LIVE = "11111111-2222-4000-8000-000000000000"
_D1_RECORDED = "99999999-8888-4000-8000-000000000000"


def _session_block(recorded_id: str) -> str:
    from shared.claude_md_manager import SESSION_END_MARKER, SESSION_START_MARKER

    return (
        f"{SESSION_START_MARKER}\n## Current Session\n"
        f"- Resume: `claude --resume {recorded_id}`\n"
        f"{SESSION_END_MARKER}\n"
    )


def test_d1_the_stale_block_reader_follows_the_memory_root(repo, monkeypatch):
    """D1: the reader built the path from the raw env var. Declared WORKTREE
    with no file, MAIN holding the block -- pre-fix it answered None and the
    stale block was never seen."""
    from shared.stale_session import detect_stale_session_block

    worktree = _worktree(repo, "d1-reader")
    main_md = repo / ".claude" / "CLAUDE.md"
    main_md.parent.mkdir()
    main_md.write_text(
        _managed_doc() + _session_block(_D1_RECORDED), encoding="utf-8"
    )
    assert not (worktree / ".claude" / "CLAUDE.md").exists()

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
    monkeypatch.chdir(worktree)

    warning = detect_stale_session_block({"session_id": _D1_LIVE})
    assert warning is not None, "MAIN's stale block was not seen"
    assert _D1_RECORDED in warning


def test_d1b_a_worktree_copy_is_not_reported(repo, monkeypatch):
    """D1's other half: a worktree copy nothing manages must not be reported
    when MAIN holds no file."""
    from shared.stale_session import detect_stale_session_block

    worktree = _worktree(repo, "d1b-reader")
    wt_md = worktree / ".claude" / "CLAUDE.md"
    wt_md.parent.mkdir()
    wt_md.write_text(_managed_doc() + _session_block(_D1_RECORDED))

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
    monkeypatch.chdir(worktree)

    assert detect_stale_session_block({"session_id": _D1_LIVE}) is None


def test_d2_step4_never_writes_the_worktree_copy(repo, monkeypatch):
    """D2: declared MAIN, cwd in the worktree, MAIN empty. The resolver's
    git/cwd fall-through reached the worktree copy and step 4 wrote a STALE
    marker there."""
    from session_init import check_pinned_staleness
    from shared.project_scope import project_memory_root

    worktree = _worktree(repo, "d2-write")
    doc = _managed_doc(_stale_capable_pin())
    wt_md = worktree / ".claude" / "CLAUDE.md"
    wt_md.parent.mkdir()
    wt_md.write_text(doc, encoding="utf-8")

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    monkeypatch.chdir(worktree)

    check_pinned_staleness(repo, project_memory_root(str(repo)))

    assert wt_md.read_text(encoding="utf-8") == doc, (
        "step 4 wrote into the worktree copy through the fall-through"
    )


def test_f4_no_env_step4_marks_the_memory_root(repo, monkeypatch):
    """F4 MEASUREMENT: with no declared dir and the cwd in the worktree, step
    4 marks MAIN -- the memory root -- and not the worktree copy. The code is
    right on this shape; the comment claiming the no-declared-dir rule stops
    every writer was the defect, so the comment was corrected rather than the
    asymmetry."""
    from session_init import check_pinned_staleness
    from shared.project_scope import project_memory_root_or_none

    worktree = _worktree(repo, "f4-no-env")
    main_md = repo / ".claude" / "CLAUDE.md"
    main_md.parent.mkdir()
    main_md.write_text(_managed_doc(_stale_capable_pin()), encoding="utf-8")
    wt_md = worktree / ".claude" / "CLAUDE.md"
    wt_md.parent.mkdir()
    original = _managed_doc(_stale_capable_pin())
    wt_md.write_text(original, encoding="utf-8")

    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(worktree)
    memory_root = project_memory_root_or_none(os.getcwd())
    assert memory_root == repo.resolve(), "the cwd-derived root is not MAIN"

    check_pinned_staleness(os.getcwd(), memory_root)

    assert "<!-- STALE" in main_md.read_text(encoding="utf-8"), (
        "step 4 did not reach the memory root on the no-declared-dir shape"
    )
    assert wt_md.read_text(encoding="utf-8") == original


def test_d3_no_declared_dir_and_no_git_writes_nothing(repo, monkeypatch):
    """D3: with the variable ABSENT the unknown-root rule did not hold, so an
    env-absent + no-git session in a worktree marked the worktree copy."""
    worktree = _worktree(repo, "d3-write")
    doc = _managed_doc(_stale_capable_pin())
    wt_md = worktree / ".claude" / "CLAUDE.md"
    wt_md.parent.mkdir()
    wt_md.write_text(doc, encoding="utf-8")

    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(worktree)
    _hide_git(monkeypatch)

    stdin_data = json.dumps(
        {"session_id": SESSION_ID, "source": "startup", "agent_type": LEAD}
    )
    with patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc:
            session_init.main()
    assert exc.value.code == 0
    output = json.loads(mock_stdout.getvalue())
    system_message = output.get("systemMessage", "")

    assert "Project CLAUDE.md skipped" in system_message, system_message[:300]
    # THE OTHER CAUSE, AND ONLY IT: this anchor EXISTS, so the skip is a git
    # failure and must not claim the directory is gone.
    assert "git could not answer" in system_message, system_message[:300]
    assert "no longer exists" not in system_message, system_message[:300]
    assert wt_md.read_text(encoding="utf-8") == doc, (
        "a no-declared-dir git failure let step 4 mark the worktree"
    )


def test_a_removed_declaration_names_the_missing_directory(repo, monkeypatch):
    """The SessionStart skip names the cause that OCCURRED.

    Git is deliberately LEFT AVAILABLE: the declaration is gone, so the root
    is unknown before any git call, and the git wording was false on exactly
    this shape (a removed worktree, the one users hit most)."""
    worktree = _worktree(repo, "removed-declaration")
    shutil.rmtree(worktree)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
    monkeypatch.chdir(repo)

    stdin_data = json.dumps(
        {"session_id": SESSION_ID, "source": "startup", "agent_type": LEAD}
    )
    with patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc:
            session_init.main()
    assert exc.value.code == 0
    system_message = json.loads(mock_stdout.getvalue()).get("systemMessage", "")

    assert "no longer exists" in system_message, system_message[:300]
    assert "git could not answer" not in system_message, system_message[:300]
    # NAMED, NOT JUST DESCRIBED: the user must be able to tell WHICH directory.
    assert str(worktree) in system_message, system_message[:300]


def test_a_forged_gitfile_names_the_verification_failure(repo, tmp_path, monkeypatch):
    """THE THIRD CAUSE at SessionStart. A directory whose `.git` FILE names
    another repository's worktree admin dir: git RAN and answered, and the
    answer failed verification. The skip must say that -- not that git could
    not answer, and not that the directory is gone."""
    forged = _forged_gitfile(repo, tmp_path, "forged-session")

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(forged))
    monkeypatch.chdir(forged)

    stdin_data = json.dumps(
        {"session_id": SESSION_ID, "source": "startup", "agent_type": LEAD}
    )
    with patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc:
            session_init.main()
    assert exc.value.code == 0
    system_message = json.loads(mock_stdout.getvalue()).get("systemMessage", "")

    assert (
        "exists but could not be verified as a checkout of a repository"
        in system_message
    ), system_message[:300]
    assert "no longer exists" not in system_message, system_message[:300]
    assert "git could not answer" not in system_message, system_message[:300]
    assert str(forged) in system_message, system_message[:300]


def test_the_session_start_reports_nothing_when_no_cause_is_identified(
    repo, tmp_path, monkeypatch
):
    """NEGATIVE CONTROL for the fallback. A KNOWN root has no cause to report,
    so a channel that resolves a cause and gets None must emit NOTHING rather
    than guess a sentence. A plain known-root shape never reaches the guarded
    branch at all, so the discriminating shape is the disagreement: the guard
    still believes the root is unknown while the cause resolver says otherwise.
    Stubbing the resolver to None is exactly that disagreement, and it is the
    only route by which a reintroduced `or 'could not...'` could fire."""
    import session_init as gate

    forged = _forged_gitfile(repo, tmp_path, "no-cause-session")
    monkeypatch.setattr(gate, "unknown_root_cause", lambda pd: None)

    _context, system_message = _run(monkeypatch, forged)

    assert "Project CLAUDE.md skipped" not in system_message, system_message[:300]
    assert "no longer exists" not in system_message, system_message[:300]
    assert (
        "could not be verified as a checkout" not in system_message
    ), system_message[:300]
    assert "git could not answer" not in system_message, system_message[:300]


def test_a_bare_worktree_resolves_and_prints_no_skip(tmp_path, monkeypatch):
    """THE THIRD TEXT MUST NOT SWALLOW A LEGITIMATE CHECKOUT.

    A worktree of a BARE repository fails the same candidate verification a
    forged gitfile does, and `_failed_verification_root` answers (given, True)
    for it: it IS a genuine checkout. This arm pins that at the message layer,
    where a careless mapping to the new "could not be verified" text would
    show up as the absence it is supposed to keep."""
    from shared.project_scope import project_memory_root_or_none

    seed = tmp_path / "seed"
    seed.mkdir()
    _git("init", "-q", ".", cwd=seed)
    (seed / "README").write_text("seed\n")
    _git("add", ".", cwd=seed)
    _git("commit", "-qm", "seed", cwd=seed)
    bare = tmp_path / "bare.git"
    _git("clone", "-q", "--bare", str(seed), str(bare), cwd=tmp_path)
    worktree = tmp_path / "bare-wt"
    _git("worktree", "add", "-q", str(worktree), "main", cwd=bare)

    assert project_memory_root_or_none(worktree) is not None, (
        "a bare repository's worktree is a genuine checkout, not an unknown root"
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
    monkeypatch.chdir(worktree)

    stdin_data = json.dumps(
        {"session_id": SESSION_ID, "source": "startup", "agent_type": LEAD}
    )
    with patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc:
            session_init.main()
    assert exc.value.code == 0
    system_message = json.loads(mock_stdout.getvalue()).get("systemMessage", "")

    assert "Project CLAUDE.md skipped" not in system_message, system_message[:300]
    assert "no longer exists" not in system_message, system_message[:300]
    assert (
        "could not be verified as a checkout of a repository"
        not in system_message
    ), system_message[:300]
    assert "git could not answer" not in system_message, system_message[:300]


def test_d3b_the_archive_refuses_an_unknown_no_declared_root(repo, monkeypatch):
    """D3 in archive_pin: the guard ran only inside the env branch."""
    import archive_pin

    worktree = _worktree(repo, "d3-archive")
    wt_md = worktree / ".claude" / "CLAUDE.md"
    wt_md.parent.mkdir()
    wt_md.write_text(_managed_doc(_stale_capable_pin()), encoding="utf-8")

    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(worktree)
    _hide_git(monkeypatch)

    with pytest.raises(archive_pin._Unevaluable) as exc:
        archive_pin.resolve_claude_md()
    reason = str(exc.value)
    assert "project memory root unknown" in reason
    # THE ANCHOR REFUSAL, NOT ITS BASE SIBLING. The anchor EXISTS on this
    # shape, so with the anchor refusal removed the missing branch is skipped,
    # the cwd resolver fails (git hidden), the raw resolver finds the worktree
    # copy, and the BASE refusal raises with the same prefix. The absence
    # assertion is what tells the two mechanisms apart.
    assert "git could not answer" in reason, reason
    assert "cannot be identified" in reason, reason
    assert "could not classify" not in reason, reason


def test_d2b_the_exposure_persists_across_launches(repo, tmp_path, monkeypatch):
    """D2 WINDOW CORRECTION, armed: the exposure does NOT self-heal.

    MAIN's `.claude` is a symlink pointing OUTSIDE the root, so step 3's
    creation is refused by containment and step 5b's Case-0 write meets the
    same refusal -- MAIN never gains a file. On the pre-fix code that made
    step 4's fall-through write the WORKTREE copy on launch 1 AND launch 2
    (measured); the post-fix code leaves it unchanged on both. Two launches
    are driven here because a one-launch arm cannot tell "heals next launch"
    from "persists", and the difference is the urgency of the fix.
    """
    worktree = _worktree(repo, "d2b-persist")
    doc = _managed_doc(_stale_capable_pin())
    wt_md = worktree / ".claude" / "CLAUDE.md"
    wt_md.parent.mkdir()
    wt_md.write_text(doc, encoding="utf-8")

    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(str(outside), str(repo / ".claude"), target_is_directory=True)

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    monkeypatch.chdir(worktree)
    stdin_data = json.dumps(
        {"session_id": SESSION_ID, "source": "startup", "agent_type": LEAD}
    )

    for launch in (1, 2):
        with patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc:
                session_init.main()
        assert exc.value.code == 0
        output = json.loads(mock_stdout.getvalue())
        assert "Project CLAUDE.md skipped" in output.get("systemMessage", ""), (
            f"launch {launch}: the containment refusal was not surfaced"
        )
        assert wt_md.read_text(encoding="utf-8") == doc, (
            f"launch {launch}: step 4 wrote the worktree copy"
        )
        assert not (repo / ".claude" / "CLAUDE.md").exists(), (
            f"launch {launch}: MAIN gained a file through the outside symlink"
        )


# --- Tester-lane coverage gaps, armed rather than deferred -------------------

def _two_stale_pins() -> str:
    """Two pins carrying the STALE marker: `is_stale` reads the marker row,
    not the date, and the block-signal threshold is 2."""
    marker = "<!-- STALE: Last relevant 2020-01-05 -->\n"
    return (
        "<!-- pinned: 2026-10-01 -->\n### Old A\n" + marker
        + "Last relevant 2020-01-05, PR #1, merged 2020-01-05\n\n"
        + "<!-- pinned: 2026-10-01 -->\n### Old B\n" + marker
        + "Last relevant 2020-01-05, PR #2, merged 2020-01-05\n\n"
    )


def test_the_stale_block_directive_never_reads_the_worktree_copy(
    repo, monkeypatch
):
    """The known-root short-circuit is load-bearing, not decorative.

    Declared MAIN, cwd in a worktree, MAIN empty, and the WORKTREE copy carries
    two stale pins -- enough to fire the block signal. The directive must return
    None: the memory root has no document, and delegating None to the callee
    re-resolves env-first and reads the worktree copy. The non-vacuity line
    proves that fallback WOULD fire, so the guard is what changes the answer;
    the control then puts the document in MAIN and requires the directive.
    """
    from session_init import _staleness_block_check, check_pin_stale_block_directive
    from shared.project_scope import project_memory_root

    worktree = _worktree(repo, "directive-guard")
    doc = _managed_doc(_two_stale_pins())
    wt_md = worktree / ".claude" / "CLAUDE.md"
    wt_md.parent.mkdir()
    wt_md.write_text(doc, encoding="utf-8")

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    monkeypatch.chdir(worktree)
    root = project_memory_root(str(repo))

    assert _staleness_block_check(claude_md_path=None) is not None, (
        "the fixture no longer fires the block signal through the fallback, "
        "so the guard below proves nothing"
    )
    assert check_pin_stale_block_directive(repo, root) is None, (
        "the directive read the worktree copy through a None delegation"
    )

    main_md = repo / ".claude" / "CLAUDE.md"
    main_md.parent.mkdir()
    main_md.write_text(doc, encoding="utf-8")
    assert check_pin_stale_block_directive(repo, root) is not None, (
        "control: the same document in MAIN must fire the directive"
    )


def test_the_retrieved_sync_names_an_unknown_root(repo, monkeypatch):
    """`sync_retrieved_to_claude_md`'s ROOT_UNKNOWN branch, asserted rather
    than left diagnostic-only in a comment."""
    import scripts.working_memory as wm

    worktree = _worktree(repo, "retrieved-sync-git-failure")
    worktree_md = worktree / ".claude" / "CLAUDE.md"
    worktree_md.parent.mkdir()
    original = _display_doc()
    worktree_md.write_text(original, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
    monkeypatch.chdir(worktree)
    _align_memory_scope(monkeypatch, wm, worktree)
    _hide_git(monkeypatch)

    result = wm.sync_retrieved_to_claude_md(
        [{"id": "0" * 32, "context": "external entry"}], query="a query"
    )

    assert result.reason == wm.SyncResult.ROOT_UNKNOWN, (
        f"the unknown root was not named: {result.reason}"
    )
    assert worktree_md.read_text(encoding="utf-8") == original, (
        "a git failure let the retrieved-context sync write the worktree"
    )


def test_the_retrieved_sync_unknown_root_is_not_unresolved(repo, monkeypatch):
    """Control: with git answering and no document anywhere, the reason is
    UNRESOLVED -- the two causes are distinguishable."""
    import scripts.working_memory as wm

    plain = repo / "plain-no-document"
    plain.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(plain))
    monkeypatch.chdir(repo)
    _align_memory_scope(monkeypatch, wm, repo)

    result = wm.sync_retrieved_to_claude_md(
        [{"id": "0" * 32, "context": "external entry"}], query="a query"
    )

    assert result.reason == wm.SyncResult.UNRESOLVED, (
        f"expected UNRESOLVED, got {result.reason}"
    )
