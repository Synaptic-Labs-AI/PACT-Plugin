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
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import session_init
from shared.claude_md_manager import ensure_project_memory_md
from helpers import doc_at  # noqa: E402

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
    assert ensure_project_memory_md(doc_at(repo)) is not None
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
    assert ensure_project_memory_md(doc_at(worktree)) is not None
    copy = worktree / ".claude" / "CLAUDE.md"
    copy.write_text(
        copy.read_text(encoding="utf-8").replace(
            "## Current Session\n",
            f"## Current Session\n- Session dir: `{predecessor}`\n",
        ),
        encoding="utf-8",
    )
    # Control: the copy really names the predecessor to a reader.
    assert session_init._extract_prev_session_dir(str(worktree), doc=doc_at(str(worktree))) == str(
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


def test_the_resume_line_fallback_uses_the_sessions_own_slug(repo):
    """A worktree session's document lives in MAIN, but its session
    directory is named from the WORKTREE (project_slug never normalises to
    the main checkout). A Current Session block with only a Resume line must
    therefore resolve the worktree slug's directory. Revert check: deriving
    the slug from the document's base names `<sessions>/main/<sid>`, a
    directory no writer used, and this goes red."""
    from shared import build_session_path, project_slug
    from shared.claude_md_manager import resolve_managed_document
    from shared.session_resume import _extract_prev_session_dir, update_session_info

    worktree = repo / ".worktrees" / "feature-x"
    _git("worktree", "add", "-q", str(worktree), "-b", "feature-x", cwd=repo)
    assert ensure_project_memory_md(doc_at(repo)) is not None
    doc = resolve_managed_document(repo)
    previous = "12121212-3434-5656-7878-909090909090"
    # No session_dir: the block carries the Resume line and no Session dir.
    assert update_session_info(previous, "session-12121212", doc=doc)
    assert "- Session dir:" not in doc.path.read_text(encoding="utf-8")

    found = _extract_prev_session_dir(str(worktree), doc=doc)

    expected = str(build_session_path(project_slug(str(worktree)), previous))
    assert found == expected, (found, expected)
    assert project_slug(str(worktree)) != project_slug(str(repo))


def test_a_subdirectory_with_its_own_document_keeps_it(repo, monkeypatch):
    """Control: the enclosing rung applies only when the subdirectory holds
    no document; its own document wins and the toplevel's is untouched."""
    assert ensure_project_memory_md(doc_at(repo)) is not None
    toplevel_doc = repo / ".claude" / "CLAUDE.md"
    before = toplevel_doc.read_bytes()
    subdir = repo / "pkg"
    subdir.mkdir()
    assert ensure_project_memory_md(doc_at(subdir)) is not None

    _start(monkeypatch, subdir)

    assert SESSION_ID in (subdir / ".claude" / "CLAUDE.md").read_text(
        encoding="utf-8"
    )
    assert toplevel_doc.read_bytes() == before


# ---------------------------------------------------------------------------
# The "Project memory file:" line in the lead's SessionStart context. Revert
# check for every arm: without the emission in session_init, or with the
# safety check removed from format_project_memory_line, the arm goes red.
# ---------------------------------------------------------------------------

_KNOWN = "Project memory file: `{}`. Use this file wherever a command refers to the project CLAUDE.md."
_UNSAFE = (
    "Project memory file unavailable (not a safe path). Do not create or edit "
    "a project CLAUDE.md this session."
)


def test_a_worktree_lead_is_told_the_main_document_over_the_real_seam(
    repo, tmp_path
):
    """The hook as the platform runs it: a subprocess with stdin JSON and
    CLAUDE_PROJECT_DIR naming a linked worktree."""
    worktree = repo / ".worktrees" / "seam"
    _git("worktree", "add", "-q", str(worktree), "-b", "seam", cwd=repo)
    hook = Path(session_init.__file__)
    home = tmp_path / "home"
    home.mkdir()
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith(("GIT_", "CLAUDE_", "PYTEST_"))
    }
    env.update(
        HOME=str(home),
        CLAUDE_CONFIG_DIR=str(home / ".claude"),
        CLAUDE_PROJECT_DIR=str(worktree),
    )
    stdin = json.dumps(
        {"session_id": SESSION_ID, "source": "startup", "agent_type": LEAD}
    )
    result = subprocess.run(
        [sys.executable, str(hook)], input=stdin, cwd=str(worktree), env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr[-500:]
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]

    assert _KNOWN.format(repo / ".claude" / "CLAUDE.md") in context, context[-600:]
    assert not (worktree / ".claude" / "CLAUDE.md").exists()


@pytest.mark.parametrize("source", ["startup", "compact"])
def test_the_line_names_the_document_on_every_source(repo, monkeypatch, source):
    context, _system_message = _start(monkeypatch, repo, source=source)

    assert _KNOWN.format(repo / ".claude" / "CLAUDE.md") in context


def test_an_unknown_root_names_the_cause_not_a_path(repo, monkeypatch):
    worktree = repo / ".worktrees" / "unknown-line"
    _git("worktree", "add", "-q", str(worktree), "-b", "unknown-line", cwd=repo)
    monkeypatch.setenv("PATH", "")  # git unavailable: the root is unknown

    context, _system_message = _start(monkeypatch, worktree)

    assert (
        "Project memory file unavailable (git could not answer). Do not create "
        "or edit a project CLAUDE.md this session."
    ) in context
    assert "Project memory file: `" not in context


def test_a_teammate_is_not_told_a_project_memory_file(repo, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    monkeypatch.chdir(repo)
    stdin_data = json.dumps(
        {"session_id": SESSION_ID, "source": "startup",
         "agent_type": "PACT:pact-backend-coder"}
    )
    with patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        with pytest.raises(SystemExit):
            session_init.main()

    assert "Project memory file" not in mock_stdout.getvalue()


def test_a_backtick_in_the_path_is_never_interpolated(tmp_path, monkeypatch):
    project = tmp_path / "we`ird"
    project.mkdir()
    _git("init", "-q", ".", cwd=project)

    context, _system_message = _start(monkeypatch, project)

    assert _UNSAFE in context
    assert "we`ird" not in context.split("Project memory file", 1)[1][:200]


@pytest.mark.parametrize(
    "name", ["line\nbreak", "bidi‮override", "para sep", "tab\there"]
)
def test_an_unsafe_character_in_the_path_is_refused(tmp_path, name):
    from shared.claude_md_manager import ManagedDocument
    from shared.project_scope import SessionRoot
    from shared.session_resume import format_project_memory_line

    root = tmp_path / name
    doc = ManagedDocument(root / ".claude" / "CLAUDE.md", root, False)

    line = format_project_memory_line(SessionRoot(root, True, root, None), doc)

    assert line == _UNSAFE


def test_an_unsafe_anchor_in_the_cause_is_refused(tmp_path):
    from shared.project_scope import ROOT_CAUSE_MISSING, SessionRoot
    from shared.session_resume import format_project_memory_line

    anchor = tmp_path / "gone`\nnow"

    line = format_project_memory_line(
        SessionRoot(anchor, True, None, ROOT_CAUSE_MISSING), None
    )

    assert line == _UNSAFE
