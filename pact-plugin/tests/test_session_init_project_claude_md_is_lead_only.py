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


def _run(monkeypatch, project_dir, agent_type, source="startup",
         session_id=_SESSION_ID):
    """Drive ``session_init.main()`` with nothing stubbed."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.chdir(project_dir)
    stdin_data = json.dumps(
        {"session_id": session_id, "source": source, "agent_type": agent_type}
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

    def test_a_dot_claude_worktree_file_gets_the_notice(self, repo, monkeypatch):
        """A worktree carrying ONLY `.claude/CLAUDE.md` is the third layout.

        The tracked-legacy arm above pins `./CLAUDE.md`; the creation arm pins
        a worktree with no file at all (the notice's negative control). The
        dot_claude source is the remaining branch of the notice's fire
        condition: the worktree file exists, the managed target is main, and
        the notice must name the DOT_CLAUDE path rather than the legacy one.
        """
        main, git = repo
        (main / "README").write_text("seed\n")
        git("add", ".", cwd=main)
        git("commit", "-qm", "seed", cwd=main)

        control = self._worktree(main, git, "dot-claude")
        (control / ".claude").mkdir()
        (control / ".claude" / "CLAUDE.md").write_text(USER_MD)

        context, system_message = _run(monkeypatch, control, LEAD)

        main_md = main / ".claude" / "CLAUDE.md"
        worktree_md = control / ".claude" / "CLAUDE.md"
        assert main_md.exists() and CREATED in context, (
            "control: the lead start did not create MAIN's CLAUDE.md, so the "
            "notice's managed target was never in play"
        )
        assert f"Project memory is managed in {main_md}." in context, (
            "the notice did not name the managed file, or named a legacy path"
        )
        assert f"This worktree also carries {worktree_md}," in context, (
            "the notice did not name the worktree's .claude/CLAUDE.md"
        )
        assert (
            "entries written there are lost when the worktree is removed"
            in context
        ), "the notice did not state the durability consequence"
        assert (
            "If that file is not meant to be part of the branch, remove or "
            "untrack it." in context
        ), "the notice did not carry the conditioned remedy"
        assert "Project memory is managed in" not in system_message, (
            "the notice belongs in the lead's context, not systemMessages"
        )
        assert worktree_md.read_text() == USER_MD, (
            "the worktree's own .claude/CLAUDE.md was rewritten"
        )

    def test_a_main_checkout_claude_md_gets_no_notice(self, repo, monkeypatch):
        """The notice's other failure direction: noisy when it should not fire.

        In a MAIN checkout the base equals the project dir, so nothing is
        diverted and the notice must stay silent even though the checkout
        carries a `.claude/CLAUDE.md`. The worktree arms cover the missing
        direction; this one covers the noisy direction.
        """
        main, git = repo
        (main / ".claude").mkdir()
        (main / ".claude" / "CLAUDE.md").write_text(USER_MD)
        (main / "README").write_text("seed\n")
        git("add", ".", cwd=main)
        git("commit", "-qm", "seed", cwd=main)

        context, system_message = _run(monkeypatch, main, LEAD)

        assert MIGRATED in context, (
            "control: the lead start did not reach the migration in a main "
            "checkout, so this layout never reaches the notice's guard"
        )
        assert "This worktree also carries" not in context, (
            "the pre-existing-file notice fired in a MAIN checkout, where the "
            "base and the project dir are the same"
        )
        assert "Project memory is managed in" not in context
        assert "Project memory is managed in" not in system_message

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
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc:
                session_init.main()
        assert exc.value.code == 0
        # Positive control: an absence assertion cannot speak for a frame that
        # never reached the lead branch. The byte-0 lead marker proves it did.
        context = json.loads(mock_stdout.getvalue()).get(
            "hookSpecificOutput", {}
        ).get("additionalContext", "")
        assert context.startswith("YOUR PACT ROLE: orchestrator."), (
            "control: the lead frame did not reach the lead branch, so the "
            "absence assertions below cannot speak for it"
        )
        assert not (worktree / ".claude" / "CLAUDE.md").exists(), (
            "a frame with no declared project dir created a CLAUDE.md in the cwd"
        )
        assert not (main / ".claude" / "CLAUDE.md").exists(), (
            "a frame with no declared project dir created a CLAUDE.md in the "
            "main checkout"
        )

    def test_a_compact_start_keeps_main_started(self, repo, monkeypatch):
        """F1 from review: step 5b's `Started` read follows the memory root.

        A compaction passes `started=_extract_session_started(memory_root)` so
        the rewrite keeps the session's recorded start. Read from the worktree
        instead, that returns None and the rewrite resets the line to now.
        The assertion is the VALUE, and the new session id proves the block
        was rewritten (otherwise the old value would survive trivially).
        """
        from shared.session_resume import update_session_info

        main, git = repo
        (main / "README").write_text("seed\n")
        git("add", ".", cwd=main)
        git("commit", "-qm", "seed", cwd=main)
        old_started = "2026-10-19 07:00:00 UTC"
        prev_dir = Path.home() / ".claude" / "pact-sessions" / "proj" / "f1"
        prev_dir.mkdir(parents=True)
        assert update_session_info(
            "prev-session", "pact-old", str(prev_dir), started=old_started,
            project_dir=str(main),
        ) is not None
        main_before = (main / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        assert f"- Started: {old_started}" in main_before
        assert _SESSION_ID not in main_before

        worktree = self._worktree(main, git, "compact")
        _run(monkeypatch, worktree, LEAD, source="compact")

        main_text = (main / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        assert _SESSION_ID in main_text, (
            "control: the compact start did not rewrite MAIN's session block, "
            "so the Started line below was never in play"
        )
        assert f"- Started: {old_started}" in main_text, (
            "the compact start reset MAIN's recorded Started value"
        )
        assert not (worktree / ".claude" / "CLAUDE.md").exists()
        assert not (worktree / "CLAUDE.md").exists()

    def test_a_read_only_main_checkout_fails_closed(self, repo, monkeypatch):
        """F8 from review: when MAIN's writers cannot run, nothing is written
        anywhere and the failure reaches systemMessage.

        The checkout is made unwritable with chmod (the suite runs as the
        owning uid, not root), so the real OSError path runs; nothing is
        mocked. If the platform ignored the mode bits the arm fails at the
        status assertion rather than passing silently.
        """
        main, git = repo
        (main / "README").write_text("seed\n")
        git("add", ".", cwd=main)
        git("commit", "-qm", "seed", cwd=main)
        worktree = self._worktree(main, git, "readonly")

        os.chmod(main, 0o500)
        try:
            context, system_message = _run(monkeypatch, worktree, LEAD)
        finally:
            os.chmod(main, 0o700)

        # The permission failure reaches systemMessage on either route: today
        # through session_init's safety net ("... Permission denied: <path>"),
        # and after the coder moves ensure_dot_claude_parent inside
        # update_session_info's try, through the writer's own status
        # ("Session info failed: PermissionError (EACCES)"). The assertion is
        # vocabulary-agnostic on purpose so this arm does not encode which
        # route is in place; the absence of files below is the fail-closed
        # claim.
        assert "permission" in system_message.lower(), (
            "the permission failure did not reach systemMessage on either "
            f"route; context={context[:200]!r} "
            f"system_message={system_message[:200]!r}"
        )
        assert not (main / ".claude" / "CLAUDE.md").exists(), (
            "a CLAUDE.md appeared in the read-only main checkout"
        )
        assert not (worktree / ".claude" / "CLAUDE.md").exists(), (
            "the failure was answered by planting a file in the worktree"
        )
        assert not (worktree / "CLAUDE.md").exists()

    def test_b6_skipping_steps_3_and_3b_still_writes_nothing_in_the_worktree(
        self, repo, monkeypatch
    ):
        """B6: the executable proof that option (b) cannot work.

        Option (b) was "skip create/migrate when the project dir is a linked
        worktree". With steps 3 and 3b no-oped, step 5b is still an
        independent writer: if it kept the old target it would create or
        rewrite the worktree file anyway, so skipping 3/3b alone cannot close
        the defect. At HEAD 5b shares the memory root, so the worktree gains
        nothing and MAIN gets the file.
        """
        main, git = repo
        (main / "README").write_text("seed\n")
        git("add", ".", cwd=main)
        git("commit", "-qm", "seed", cwd=main)
        worktree = self._worktree(main, git, "option-b")
        monkeypatch.setattr(
            session_init, "ensure_project_memory_md", lambda *a, **k: None
        )
        monkeypatch.setattr(
            session_init, "migrate_to_managed_structure", lambda *a, **k: None
        )

        context, _ = _run(monkeypatch, worktree, LEAD)

        assert not (worktree / ".claude" / "CLAUDE.md").exists(), (
            "with 3/3b skipped, step 5b still planted a CLAUDE.md in the "
            "worktree, which is why option (b) cannot work alone"
        )
        assert not (worktree / "CLAUDE.md").exists()
        assert (main / ".claude" / "CLAUDE.md").exists(), (
            "the remaining writer (step 5b) did not reach MAIN"
        )
        assert "Session info created in new project CLAUDE.md" in context, (
            "control: step 5b did not run, so the worktree's emptiness above "
            "proves nothing"
        )

    def test_r8_a_second_start_in_the_same_worktree_is_idempotent(
        self, repo, monkeypatch
    ):
        """R8: two consecutive lead starts in one worktree stay one block.

        MAIN starts with a user-authored marker-less CLAUDE.md. The first
        start migrates it and records the session; the second must rewrite the
        block in place -- exactly one of each marker pair, the user's line
        intact, the stale marking not duplicated -- and the worktree must gain
        nothing either time.
        """
        main, git = repo
        (main / ".claude").mkdir()
        (main / ".claude" / "CLAUDE.md").write_text(USER_MD)
        (main / "README").write_text("seed\n")
        git("add", ".", cwd=main)
        git("commit", "-qm", "seed", cwd=main)
        worktree = self._worktree(main, git, "relaunch")

        _run(monkeypatch, worktree, LEAD)
        # A second start is a NEW session in the same worktree (a fresh
        # launch, not a --resume), so the block must be replaced, not
        # appended. Reusing the id would make the writer a legitimate no-op
        # and the "updated" control below could not fire.
        context, _ = _run(
            monkeypatch, worktree, LEAD,
            session_id="dddddddd-eeee-ffff-0000-111111111111",
        )

        main_text = (main / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        assert "dddddddd-eeee-ffff-0000-111111111111" in main_text, (
            "control: the second start did not replace the session block"
        )
        assert main_text.count("<!-- PACT_MANAGED_START") == 1, (
            "the second start duplicated the managed boundary"
        )
        assert main_text.count("<!-- PACT_MANAGED_END") == 1
        assert main_text.count("<!-- SESSION_START") == 1, (
            "the second start duplicated the session block"
        )
        assert main_text.count("<!-- SESSION_END") == 1
        assert main_text.count("<!-- PACT_MEMORY_START") == 1
        assert "Build with make." in main_text, (
            "the user's own content did not survive the second start"
        )
        assert main_text.count(STALE) == 1, (
            "the stale marking was duplicated or lost on the second start"
        )
        assert "Session info updated in project CLAUDE.md" in context, (
            "control: the second start did not rewrite the block, so the "
            "single-block assertions above were never in play"
        )
        assert not (worktree / ".claude" / "CLAUDE.md").exists()
        assert not (worktree / "CLAUDE.md").exists()

    def test_r7_a_second_lead_waits_on_the_shared_lock_and_fails_open(
        self, repo, monkeypatch
    ):
        """R7: the retarget concentrates every session of a project on MAIN's
        lock, so the timeout path must be measured through the new target.

        A separate process holds MAIN's sidecar lock; the lead start must take
        the documented fail-open path -- "Failed to acquire lock" to
        systemMessage, MAIN's file byte-identical, nothing in the worktree.
        _LOCK_TIMEOUT_SECONDS is shortened so the arm does not cost 10s; the
        lock, the poll and the timeout branch are the real ones.
        """
        import subprocess
        import sys

        from shared import claude_md_manager

        main, git = repo
        (main / ".claude").mkdir()
        # A fresh pin date: no stale marking is due, so step 4 does not write
        # (and therefore does not add a second timeout wait).
        (main / ".claude" / "CLAUDE.md").write_text(
            "# Project\n\n## Pinned Context\n\n"
            "### A fresh pin 2026-10-20\n\nbody text\n"
        )
        (main / "README").write_text("seed\n")
        git("add", ".", cwd=main)
        git("commit", "-qm", "seed", cwd=main)
        worktree = self._worktree(main, git, "lock")

        lock_path = main / ".claude" / ".CLAUDE.md.lock"
        holder_script = (
            "import fcntl, sys, time\n"
            "f = open(sys.argv[1], 'a')\n"
            "fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "print('READY', flush=True)\n"
            "time.sleep(60)\n"
        )
        holder = subprocess.Popen(
            [sys.executable, "-c", holder_script, str(lock_path)],
            stdout=subprocess.PIPE, text=True,
        )
        monkeypatch.setattr(claude_md_manager, "_LOCK_TIMEOUT_SECONDS", 0.3)
        try:
            assert holder.stdout.readline().strip() == "READY", (
                "the lock holder never acquired MAIN's sidecar"
            )
            before = (main / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
            _context, system_message = _run(monkeypatch, worktree, LEAD)
        finally:
            holder.terminate()
            holder.wait(timeout=10)

        assert "Failed to acquire lock" in system_message, (
            "the contended start did not report the lock timeout; "
            f"system_message={system_message[:400]!r}"
        )
        after = (main / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        assert after == before, "the contended start modified MAIN's file"
        assert not (worktree / ".claude" / "CLAUDE.md").exists()
        assert not (worktree / "CLAUDE.md").exists()

    def test_the_notice_says_will_be_managed_when_main_has_no_file(self, repo):
        """R5: a failed creation must not be asserted as an existing file."""
        main, git = repo
        worktree = self._worktree(main, git, "notice-future")
        (worktree / "CLAUDE.md").write_text(USER_MD)

        notice = session_init._worktree_memory_notice(main, str(worktree))

        assert notice is not None, "the notice did not fire for a worktree file"
        assert "Project memory will be managed in" in notice, (
            "the notice asserted a managed file although the main checkout has "
            "none (step 3 failed or had not run)"
        )
        assert "Project memory is managed in" not in notice

    def test_the_notice_sanitises_both_paths(self, monkeypatch):
        """SEC-2: a path carrying a newline must not forge a context line."""
        main = Path("/tmp/main\ninject")
        worktree = "/tmp/wt\ninject"

        def fake_resolver(project_dir):
            if str(project_dir) == str(main):
                return Path("/tmp/main\ninject/.claude/CLAUDE.md"), "dot_claude"
            return Path("/tmp/wt\ninject/CLAUDE.md"), "legacy"

        monkeypatch.setattr(
            session_init, "resolve_project_claude_md_path", fake_resolver
        )
        notice = session_init._worktree_memory_notice(main, worktree)

        assert notice is not None
        assert "\n" not in notice, "a path injected a newline into the notice"
        assert "/tmp/main inject/.claude/CLAUDE.md" in notice
        assert "/tmp/wt inject/CLAUDE.md" in notice
