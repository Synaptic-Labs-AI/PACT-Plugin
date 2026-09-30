"""Only a lead creates or migrates the project CLAUDE.md.

session_init runs ensure_project_memory_md and migrate_to_managed_structure for
a lead frame only. A teammate writes nothing into the project CLAUDE.md,
whether its agent_type is a registered PACT specialist (``pact-architect``,
qualified or not), a typo'd lead spelling (``pact-orchestrater``) or a user's
own non-PACT agent (``my-reviewer``). A separate-process teammate whose project
dir is a linked worktree would otherwise plant a template there that diverts
every CLAUDE.md resolver away from the main repo's file, or rewrite a tracked
CLAUDE.md inside the worktree. In the lead's own dir the lead has already
written the file.

Each absence arm runs a lead control on the same layout, so it cannot pass
because the layout never reaches the writer. The lead control pins the
lead's current behaviour, including inside a worktree.
"""
import io
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import session_init  # noqa: E402
from session_init import _UNKNOWN_ROLE_NOTICE, _should_warn_unknown_role  # noqa: E402

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_SESSION_ID = "cccccccc-dddd-eeee-ffff-000000000000"
LEAD = "PACT:pact-orchestrator"
USER_MD = "# My project\n\nBuild with make.\n"
CREATED = "Created project CLAUDE.md"
MIGRATED = "Migrated project CLAUDE.md"

UNRECOGNIZED = ["pact-orchestrater", "my-reviewer"]
SPECIALIST = ["pact-architect", "PACT:pact-architect"]
TEAMMATES = UNRECOGNIZED + SPECIALIST


@pytest.fixture(autouse=True)
def _live_registry(monkeypatch):
    """Point the specialist registry at this plugin's agents/ directory.

    conftest scrubs CLAUDE_PLUGIN_ROOT, which empties the registry and would
    make every specialist read as unrecognized, so the specialist arms would
    no longer test a registered specialist.
    """
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(_PLUGIN_ROOT))
    assert _should_warn_unknown_role({"agent_type": "pact-architect"}) is False, (
        "the registry is not live, so a specialist reads as unrecognized"
    )


def _run(monkeypatch, project_dir, agent_type):
    """Drive ``session_init.main()`` on ``startup`` with nothing stubbed."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.chdir(project_dir)
    stdin_data = json.dumps(
        {"session_id": _SESSION_ID, "source": "startup", "agent_type": agent_type}
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


def _project(tmp_path, name, user_md=False):
    project = tmp_path / name
    project.mkdir()
    if user_md:
        (project / "CLAUDE.md").write_text(USER_MD)
    return project


class TestTeammateWritesNothing:

    @pytest.mark.parametrize("agent_type", TEAMMATES)
    def test_no_project_claude_md_is_created(self, agent_type, monkeypatch, tmp_path):
        control = _project(tmp_path, "control")
        context, _ = _run(monkeypatch, control, LEAD)
        assert (control / ".claude" / "CLAUDE.md").exists() and CREATED in context, (
            "control: a lead start did not create the project CLAUDE.md, so "
            "this layout never reaches the writer"
        )

        project = _project(tmp_path, "teammate")
        context, system_message = _run(monkeypatch, project, agent_type)
        assert not (project / ".claude" / "CLAUDE.md").exists(), (
            f"agent_type={agent_type!r} created the project CLAUDE.md"
        )
        assert CREATED not in context and CREATED not in system_message

    @pytest.mark.parametrize("agent_type", TEAMMATES)
    def test_a_user_claude_md_is_left_byte_identical(
        self, agent_type, monkeypatch, tmp_path
    ):
        control = _project(tmp_path, "control", user_md=True)
        context, _ = _run(monkeypatch, control, LEAD)
        assert (control / "CLAUDE.md").read_text() != USER_MD and MIGRATED in context, (
            "control: a lead start did not migrate a marker-less CLAUDE.md, so "
            "this layout never reaches the migration"
        )

        project = _project(tmp_path, "teammate", user_md=True)
        context, system_message = _run(monkeypatch, project, agent_type)
        assert (project / "CLAUDE.md").read_text() == USER_MD, (
            f"agent_type={agent_type!r} rewrote the user's own CLAUDE.md"
        )
        assert MIGRATED not in context and MIGRATED not in system_message

    @pytest.mark.parametrize("agent_type", UNRECOGNIZED)
    def test_an_unrecognized_agent_type_still_gets_the_startup_notice(
        self, agent_type, monkeypatch, tmp_path
    ):
        _, system_message = _run(monkeypatch, _project(tmp_path, "p"), agent_type)
        assert _UNKNOWN_ROLE_NOTICE in system_message

    @pytest.mark.parametrize("agent_type", SPECIALIST)
    def test_a_registered_specialist_gets_no_startup_notice(
        self, agent_type, monkeypatch, tmp_path
    ):
        _, system_message = _run(monkeypatch, _project(tmp_path, "p"), agent_type)
        assert _UNKNOWN_ROLE_NOTICE not in system_message


class TestWorktreeTeammateWritesNothing:
    """A separate-process teammate whose project dir is a linked worktree."""

    @pytest.fixture
    def repo(self, tmp_path):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)

        def git(*args, cwd):
            return subprocess.run(
                ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
                 "-c", "init.defaultBranch=main", *args],
                cwd=str(cwd), env=env, capture_output=True, text=True,
                timeout=30, check=True,
            ).stdout

        main = tmp_path / "main"
        main.mkdir()
        git("init", "-q", ".", cwd=main)
        (main / ".gitignore").write_text(".claude/\n.worktrees/\n")
        return main, git

    @staticmethod
    def _worktree(main, git, name):
        path = main / ".worktrees" / name
        git("worktree", "add", "-q", str(path), "-b", name, cwd=main)
        return path

    def test_no_worktree_claude_md_is_created(self, repo, monkeypatch):
        main, git = repo
        (main / "README").write_text("seed\n")
        git("add", ".", cwd=main)
        git("commit", "-qm", "seed", cwd=main)

        control = self._worktree(main, git, "control")
        _run(monkeypatch, control, LEAD)
        assert (control / ".claude" / "CLAUDE.md").exists(), (
            "control: a lead start inside a worktree did not create "
            "<worktree>/.claude/CLAUDE.md, so this layout never reaches the writer"
        )

        for agent_type in SPECIALIST:
            teammate = self._worktree(main, git, agent_type.replace(":", "-"))
            _run(monkeypatch, teammate, agent_type)
            assert not (teammate / ".claude" / "CLAUDE.md").exists(), (
                f"agent_type={agent_type!r} planted a CLAUDE.md in its worktree"
            )

    def test_a_tracked_claude_md_in_the_worktree_is_left_unmodified(
        self, repo, monkeypatch
    ):
        main, git = repo
        (main / "CLAUDE.md").write_text(USER_MD)
        git("add", ".", cwd=main)
        git("commit", "-qm", "seed", cwd=main)

        control = self._worktree(main, git, "control")
        _run(monkeypatch, control, LEAD)
        assert " M CLAUDE.md" in git("status", "--porcelain", cwd=control), (
            "control: a lead start inside a worktree did not migrate the tracked "
            "CLAUDE.md, so this layout never reaches the migration"
        )

        for agent_type in SPECIALIST:
            teammate = self._worktree(main, git, agent_type.replace(":", "-"))
            _run(monkeypatch, teammate, agent_type)
            assert (teammate / "CLAUDE.md").read_text() == USER_MD
            status = git("status", "--porcelain", cwd=teammate).splitlines()
            assert not any(line[3:] == "CLAUDE.md" for line in status), (
                f"agent_type={agent_type!r} left a CLAUDE.md diff in its worktree: "
                f"{status}"
            )
