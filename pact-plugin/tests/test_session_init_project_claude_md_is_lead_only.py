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
because the layout never reaches the writer. The worktree lead controls pin
the #1653 contract: the writers target the MAIN checkout, and the worktree's
own tracked CLAUDE.md is neither migrated nor marked. The controls that
asserted the pre-fix behaviour (a CLAUDE.md planted inside the worktree) were
FLIPPED, not deleted -- they still prove the layout reaches the writer.
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
# A marker-less user CLAUDE.md holding a pin dated years ago, so both the
# migration and the pinned-staleness writer have something to rewrite.
USER_MD = (
    "# My project\n\nBuild with make.\n\n"
    "## Pinned Context\n\n### An outdated pin 2020-01-05\n\nbody text\n"
)
STALE = "<!-- STALE"
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
        assert sorted(p.name for p in project.rglob("*")) == [], (
            f"agent_type={agent_type!r} wrote into the project"
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
        assert STALE in (control / "CLAUDE.md").read_text(), (
            "control: a lead start marked no stale pin, so this layout never "
            "reaches the staleness writer"
        )

        project = _project(tmp_path, "teammate", user_md=True)
        context, system_message = _run(monkeypatch, project, agent_type)
        assert (project / "CLAUDE.md").read_text() == USER_MD, (
            f"agent_type={agent_type!r} rewrote the user's own CLAUDE.md"
        )
        assert MIGRATED not in context and MIGRATED not in system_message

    @pytest.mark.parametrize("agent_type", TEAMMATES + [None])
    def test_no_lock_sidecar_is_left_beside_an_existing_claude_md(
        self, agent_type, monkeypatch, tmp_path
    ):
        """A teammate, or a frame whose stdin did not parse (None), still reads
        the project CLAUDE.md for the previous session dir. That read takes no
        lock, so the project's file set is unchanged. The spy proves the read
        ran, so the arm cannot pass on a path that never reaches the reader.
        """
        import shared.session_resume as session_resume

        reads = []
        real = session_resume.resolve_project_claude_md_path

        def spy(project_dir):
            reads.append(project_dir)
            return real(project_dir)

        monkeypatch.setattr(session_resume, "resolve_project_claude_md_path", spy)
        project = _project(tmp_path, "p", user_md=True)
        before = sorted(p.name for p in project.rglob("*"))
        if agent_type is None:
            monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
            monkeypatch.chdir(project)
            with patch("sys.stdin", io.StringIO("{not json")), \
                 patch("sys.stdout", new_callable=io.StringIO):
                with pytest.raises(SystemExit):
                    session_init.main()
        else:
            _run(monkeypatch, project, agent_type)
        assert reads, "the start never read the project CLAUDE.md"
        assert sorted(p.name for p in project.rglob("*")) == before

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

    def test_a_worktree_lead_creates_the_claude_md_in_the_main_checkout(
        self, repo, monkeypatch
    ):
        """The pre-#1653 control asserted `<worktree>/.claude/CLAUDE.md`
        existed. That pin recorded the defect and is FLIPPED here, not
        deleted: the lead must still reach the writer, in the main checkout.
        """
        main, git = repo
        (main / "README").write_text("seed\n")
        git("add", ".", cwd=main)
        git("commit", "-qm", "seed", cwd=main)

        control = self._worktree(main, git, "control")
        context, _ = _run(monkeypatch, control, LEAD)
        assert (main / ".claude" / "CLAUDE.md").exists() and CREATED in context, (
            "control: a lead start inside a worktree did not create the project "
            "CLAUDE.md in the MAIN checkout, so this layout never reaches the writer"
        )
        assert not (control / ".claude" / "CLAUDE.md").exists(), (
            "a lead start inside a worktree still planted "
            "<worktree>/.claude/CLAUDE.md"
        )
        assert "This worktree also carries" not in context, (
            "the pre-existing-file notice fired although the worktree holds no "
            "CLAUDE.md of its own"
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
        """The pre-#1653 control asserted the WORKTREE file gained the migration
        and the stale marking. That pin recorded the defect and is FLIPPED: the
        main checkout's file is migrated and marked, and the worktree's tracked
        copy is byte-identical with no diff on the branch.
        """
        main, git = repo
        (main / "CLAUDE.md").write_text(USER_MD)
        git("add", ".", cwd=main)
        git("commit", "-qm", "seed", cwd=main)

        control = self._worktree(main, git, "control")
        context, _ = _run(monkeypatch, control, LEAD)
        assert " M CLAUDE.md" in git("status", "--porcelain", cwd=main), (
            "control: a lead start inside a worktree did not migrate the MAIN "
            "checkout's CLAUDE.md, so this layout never reaches the migration"
        )
        assert STALE in (main / "CLAUDE.md").read_text(), (
            "control: a lead start inside a worktree marked no stale pin in the "
            "MAIN checkout, so this layout never reaches the staleness writer"
        )
        assert (control / "CLAUDE.md").read_text() == USER_MD, (
            "the worktree's tracked CLAUDE.md was rewritten"
        )
        status = git("status", "--porcelain", cwd=control).splitlines()
        assert not any(line[3:] == "CLAUDE.md" for line in status), (
            f"the worktree's tracked CLAUDE.md gained a diff: {status}"
        )
        assert "This worktree also carries" in context, (
            "the pre-existing-file notice did not fire although the worktree "
            "carries its own CLAUDE.md"
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

    def test_step_4_marks_the_main_checkout_not_the_worktree(self, repo, monkeypatch):
        """The step-4 wrapper has TWO routes: the patched alias for a
        non-worktree project dir, and the retargeted resolver for a linked
        worktree. This arm drives the second and asserts the destination."""
        main, git = repo
        (main / "CLAUDE.md").write_text(USER_MD)
        git("add", ".", cwd=main)
        git("commit", "-qm", "seed", cwd=main)
        worktree = self._worktree(main, git, "step4")
        assert STALE not in (main / "CLAUDE.md").read_text()

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
        monkeypatch.chdir(worktree)
        session_init.check_pinned_staleness()

        assert STALE in (main / "CLAUDE.md").read_text(), (
            "step 4 did not mark the MAIN checkout's CLAUDE.md"
        )
        assert STALE not in (worktree / "CLAUDE.md").read_text(), (
            "step 4 marked the worktree's CLAUDE.md"
        )

    def test_step_4_leaves_the_worktree_alone_when_main_has_no_file(
        self, repo, monkeypatch
    ):
        """`main` has NO CLAUDE.md, so the worktree branch has nothing to mark.
        Delegating None to the core would re-enter the env-first resolver and
        mark the worktree file -- the defect this arm pins. The arm above
        seeds main's file and never exercises this branch."""
        main, git = repo
        (main / "CLAUDE.md").write_text(USER_MD)
        git("add", ".", cwd=main)
        git("commit", "-qm", "seed", cwd=main)
        worktree = self._worktree(main, git, "step4-no-main")
        # Remove the MAIN checkout's copy from the working tree only; the
        # worktree keeps the tracked file from HEAD.
        (main / "CLAUDE.md").unlink()

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
        monkeypatch.chdir(worktree)
        assert session_init.check_pinned_staleness() is None
        assert (worktree / "CLAUDE.md").read_text() == USER_MD
        assert STALE not in (worktree / "CLAUDE.md").read_text()

    def test_a_lead_with_no_declared_project_dir_writes_nothing(
        self, repo, monkeypatch
    ):
        """A frame with no CLAUDE_PROJECT_DIR falls back to the cwd, and the
        writers keep their own no-op guard: a worktree-shaped cwd must not
        cause a file to be created in either checkout."""
        main, git = repo
        (main / "README").write_text("seed\n")
        git("add", ".", cwd=main)
        git("commit", "-qm", "seed", cwd=main)
        worktree = self._worktree(main, git, "envless")

        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.chdir(worktree)
        stdin_data = json.dumps(
            {"session_id": _SESSION_ID, "source": "startup", "agent_type": LEAD}
        )
        with patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO):
            with pytest.raises(SystemExit) as exc:
                session_init.main()
        assert exc.value.code == 0
        assert not (worktree / ".claude" / "CLAUDE.md").exists(), (
            "a frame with no declared project dir created a CLAUDE.md in the cwd"
        )
        assert not (main / ".claude" / "CLAUDE.md").exists(), (
            "a frame with no declared project dir created a CLAUDE.md in the "
            "main checkout"
        )
