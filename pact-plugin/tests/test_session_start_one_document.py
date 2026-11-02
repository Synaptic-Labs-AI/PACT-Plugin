"""SessionStart resolves the session ONCE and every step uses ONE document.

Location: pact-plugin/tests/test_session_start_one_document.py

session_init.main() takes one `session_memory_root()` observation and one
`resolve_managed_document()` answer, and passes that document to the creator
(step 3), the migration (3b), the staleness and advisory readers (4, 4a-4d),
the predecessor read (5a) and the Current Session writer (5b).

The layout that separates one document from several: a lead started in a
SUBDIRECTORY of a main checkout whose document sits at the toplevel. The one
resolver reaches the toplevel's document (its enclosing-checkout rung); a step
that re-derives from the raw subdirectory creates a second document there.

All repositories are real `git init` checkouts under tmp_path with no remote.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
from unittest.mock import patch

import pytest

import session_init
from shared.claude_md_manager import ensure_project_memory_md

LEAD = "PACT:pact-orchestrator"
SESSION_ID = "abababab-cdcd-efef-0101-232323232323"


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


def _start(monkeypatch, project_dir, source="startup"):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.chdir(project_dir)
    stdin_data = json.dumps(
        {"session_id": SESSION_ID, "source": source, "agent_type": LEAD}
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


def test_a_subdirectory_lead_manages_the_toplevel_document(repo, monkeypatch):
    """Revert check: with step 3 fed the raw subdirectory root (the pre-fix
    `ensure_project_memory_md(write_root)`), this start creates
    `pkg/.claude/CLAUDE.md` and writes its Current Session block there, so
    both assertions below go red."""
    assert ensure_project_memory_md(repo) is not None
    toplevel_doc = repo / ".claude" / "CLAUDE.md"
    subdir = repo / "pkg"
    subdir.mkdir()

    _start(monkeypatch, subdir)

    assert not (subdir / ".claude" / "CLAUDE.md").exists(), (
        "step 3 created a second document in the subdirectory"
    )
    assert not (subdir / "CLAUDE.md").exists()
    assert SESSION_ID in toplevel_doc.read_text(encoding="utf-8"), (
        "the Current Session block did not land in the toplevel document"
    )


def test_the_readers_see_the_document_step_3_just_created(repo, monkeypatch):
    """The document is resolved once before step 3, which can create it, so
    the steps after it must see the new file. Revert check: without the
    re-probe after step 3 the slot-status reader is handed a document that
    did not exist yet and the "Pin slots" line is missing from this start."""
    assert not (repo / ".claude" / "CLAUDE.md").exists()

    context, _system_message = _start(monkeypatch, repo)

    assert "Created project CLAUDE.md" in context
    assert "Pin slots: 0/" in context, context[-400:]


def test_an_unknown_root_reads_no_predecessor(repo, monkeypatch):
    """An unknown root has no document, so step 5a reads no previous
    session: not from the worktree copy and not from anywhere else. The
    worktree copy here names a real, valid session directory, so a read of it
    would return that directory. Revert check: with 5a falling back to the
    raw project dir, the spy records that directory and this goes red."""
    from shared.paths import get_claude_config_dir

    worktree = repo / ".worktrees" / "unknown-root"
    _git("worktree", "add", "-q", str(worktree), "-b", "unknown-root", cwd=repo)
    predecessor = get_claude_config_dir() / "pact-sessions" / "wt" / "prev-sid"
    predecessor.mkdir(parents=True)
    assert ensure_project_memory_md(worktree) is not None
    copy = worktree / ".claude" / "CLAUDE.md"
    copy.write_text(
        copy.read_text(encoding="utf-8").replace(
            "## Current Session\n",
            f"## Current Session\n- Session dir: `{predecessor}`\n",
        ),
        encoding="utf-8",
    )
    # Control: the copy really names the predecessor to a reader.
    assert session_init._extract_prev_session_dir(str(worktree)) == str(
        predecessor
    )

    found = []
    real = session_init._extract_prev_session_dir

    def spy(*args, **kwargs):
        result = real(*args, **kwargs)
        found.append(result)
        return result

    monkeypatch.setattr(session_init, "_extract_prev_session_dir", spy)
    monkeypatch.setenv("PATH", "")  # git unavailable: the root is unknown

    _context, system_message = _start(monkeypatch, worktree)

    assert "Project CLAUDE.md skipped" in system_message
    assert all(result is None for result in found), found


def test_a_subdirectory_with_its_own_document_keeps_it(repo, monkeypatch):
    """Control: the enclosing rung applies only when the subdirectory holds
    no document; its own document wins and the toplevel's is untouched."""
    assert ensure_project_memory_md(repo) is not None
    toplevel_doc = repo / ".claude" / "CLAUDE.md"
    before = toplevel_doc.read_bytes()
    subdir = repo / "pkg"
    subdir.mkdir()
    assert ensure_project_memory_md(subdir) is not None

    _start(monkeypatch, subdir)

    assert SESSION_ID in (subdir / ".claude" / "CLAUDE.md").read_text(
        encoding="utf-8"
    )
    assert toplevel_doc.read_bytes() == before
