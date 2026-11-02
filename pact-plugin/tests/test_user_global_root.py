"""
Location: pact-plugin/tests/test_user_global_root.py
Summary: Pins that a directory whose canonical document is the user-global
         `<config>/CLAUDE.md` is NOT a project-memory root: the classifier
         reports cause "user_global", and no reader or writer touches the
         global file.
Used by: pytest. Exercises hooks/shared/project_scope.py (`memory_root_of`,
         `session_memory_root`, `unknown_root_reason`) and, through them,
         every consumer: the SessionStart hook and the `cli.py project-file`
         command as subprocesses, and in process the memory sync,
         archive_pin, `gate_target`, `match_project_claude_md`, the session
         reader and pin_marker_writer.

Every home is a tmp_path directory with CLAUDE_CONFIG_DIR=<home>/.claude and
HOME=<home>, except the split-profile arms, where CLAUDE_CONFIG_DIR=<t>/cfg and
<home>/.claude/CLAUDE.md is the default profile's global file. The operator's
real ~/.claude is never read or written. Further arms pin the identity test:
a hardlink or a case variant of the global file is the global file, a
case-distinct sibling of home is not, and an undecidable comparison refuses.

Layouts (a)-(f) are user-global; (g) is the control. Removing the check from
`memory_root_of` turns every (a)-(f) arm red: the root becomes the anchor and
the readers name, and the writers rewrite, the global file.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import make_claude_md_with_pins, make_pin_entry
from shared import project_scope
from shared.project_scope import (
    ROOT_CAUSE_USER_GLOBAL,
    memory_root_of,
    session_memory_root,
    stays_in_declared_project,
    unknown_root_reason,
)

LEAD = "PACT:pact-orchestrator"
SESSION_ID = "c0c0c0c0-1111-2222-3333-444444444444"
_PLUGIN = Path(__file__).resolve().parent.parent
_HOOK = _PLUGIN / "hooks" / "session_init.py"
_CLI = _PLUGIN / "skills" / "pact-memory" / "scripts" / "cli.py"


def _git(*args, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "-c", "init.defaultBranch=main", *args],
        cwd=str(cwd), env=env, capture_output=True, text=True,
        timeout=30, check=True,
    ).stdout


def _global_text() -> str:
    return make_claude_md_with_pins(
        [make_pin_entry("Global pin")]
    )


class _World:
    def __init__(self, tmp_path: Path, monkeypatch, layout: str):
        self.home = tmp_path / "home"
        self.config = self.home / ".claude"
        self.config.mkdir(parents=True)
        self.global_md = self.config / "CLAUDE.md"
        if layout != "e_absent":
            self.global_md.write_text(_global_text(), encoding="utf-8")
        self.before = self._snapshot()
        if layout == "a_config":
            self.anchor = self.config
        elif layout in ("b_home_plain", "e_absent"):
            self.anchor = self.home
        elif layout == "c_home_repo":
            _git("init", "-q", ".", cwd=self.home)
            _git("commit", "-q", "--allow-empty", "-m", "seed", cwd=self.home)
            self.anchor = self.home
        elif layout == "d_home_worktree":
            _git("init", "-q", ".", cwd=self.home)
            _git("commit", "-q", "--allow-empty", "-m", "seed", cwd=self.home)
            self.anchor = tmp_path / "wt"
            _git("worktree", "add", "-q", str(self.anchor), "-b", "wt", cwd=self.home)
        elif layout == "f_symlink":
            self.anchor = tmp_path / "link"
            self.anchor.symlink_to(self.config, target_is_directory=True)
        elif layout == "g_control":
            self.anchor = tmp_path / "project"
            self.anchor.mkdir()
        else:  # pragma: no cover
            raise AssertionError(layout)
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR",
                    "GIT_CEILING_DIRECTORIES"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(self.config))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(self.anchor))
        monkeypatch.chdir(self.anchor)
        monkeypatch.setattr(project_scope, "recorded_project_dir", lambda: "")
        monkeypatch.setattr(
            project_scope, "get_worktree_identity_from_session_record", lambda: {}
        )

    def _snapshot(self):
        return self.global_md.read_bytes() if self.global_md.exists() else None

    def assert_global_untouched(self):
        assert self._snapshot() == self.before, "the user-global CLAUDE.md changed"

    @property
    def reason(self) -> str:
        return unknown_root_reason(ROOT_CAUSE_USER_GLOBAL, self.anchor)

    def env(self) -> dict:
        env = {
            k: v for k, v in os.environ.items()
            if not k.startswith(("GIT_", "CLAUDE_", "PYTEST_"))
        }
        env.update(
            HOME=str(self.home),
            CLAUDE_CONFIG_DIR=str(self.config),
            CLAUDE_PROJECT_DIR=str(self.anchor),
        )
        return env


USER_GLOBAL = ["a_config", "b_home_plain", "c_home_repo", "d_home_worktree",
               "e_absent", "f_symlink"]


@pytest.fixture(params=USER_GLOBAL)
def world(request, tmp_path, monkeypatch):
    return _World(tmp_path, monkeypatch, request.param)


@pytest.fixture
def control(tmp_path, monkeypatch):
    return _World(tmp_path, monkeypatch, "g_control")


def test_the_reason_text():
    assert unknown_root_reason(ROOT_CAUSE_USER_GLOBAL, "/h") == (
        "the project directory /h holds the user-global CLAUDE.md, which "
        "every session loads, so it is not project memory"
    )


def test_the_classifier_reports_user_global(world):
    assert memory_root_of(world.anchor) == (None, ROOT_CAUSE_USER_GLOBAL)
    session = session_memory_root()
    assert (session.root, session.cause) == (None, ROOT_CAUSE_USER_GLOBAL)


def test_a_lead_session_start_leaves_the_global_file_alone(world):
    stdin = json.dumps(
        {"session_id": SESSION_ID, "source": "startup", "agent_type": LEAD}
    )
    result = subprocess.run(
        [sys.executable, str(_HOOK)], input=stdin, cwd=str(world.anchor),
        env=world.env(), capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr[-500:]
    output = json.loads(result.stdout)
    context = output.get("hookSpecificOutput", {}).get("additionalContext", "")
    system_message = output.get("systemMessage", "")

    world.assert_global_untouched()
    assert f"Project CLAUDE.md skipped: {world.reason}" in system_message, (
        system_message[-600:]
    )
    assert f"Project memory file unavailable ({world.reason})." in context, (
        context[-600:]
    )


def test_the_cli_names_the_cause(world):
    result = subprocess.run(
        [sys.executable, str(_CLI), "project-file"], cwd=str(world.anchor),
        env=world.env(), capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 3, result.stderr[-500:]
    assert result.stdout == f"unavailable: {world.reason}\n"
    world.assert_global_untouched()


def test_every_reader_and_gate_refuses_the_global_file(world):
    from shared.claude_md_manager import gate_target, match_project_claude_md
    from staleness import resolve_session_claude_md_path

    target = str(world.global_md)
    assert resolve_session_claude_md_path() is None
    assert match_project_claude_md(target) is None
    assert gate_target(target) is None


def test_the_archive_refuses_with_the_cause(world):
    import archive_pin

    with pytest.raises(archive_pin._Unevaluable) as refused:
        archive_pin.resolve_claude_md()
    assert world.reason in str(refused.value)
    world.assert_global_untouched()


def test_the_memory_sync_writes_nothing(world):
    from scripts.working_memory import SyncResult, sync_to_claude_md

    result = sync_to_claude_md({"context": "c", "goal": "g"}, memory_id="a" * 32)

    assert result.reason == SyncResult.ROOT_UNKNOWN
    world.assert_global_untouched()


def test_the_pin_marker_writer_writes_nothing(world):
    import pin_marker_writer

    assert pin_marker_writer._plan_and_write() == "noop_memory_root_unknown"
    world.assert_global_untouched()


def test_a_project_directory_keeps_its_own_root(control):
    """Control (g): a plain project dir beside a home holding the global file
    is a KNOWN root, and the session's document is its own."""
    from shared.claude_md_manager import session_document

    assert memory_root_of(control.anchor) == (control.anchor, None)
    session, document = session_document()
    assert session.cause is None
    assert document.path == control.anchor / ".claude" / "CLAUDE.md"


def test_a_document_path_that_cannot_be_compared_counts_as_user_global(
    control, monkeypatch
):
    """Fail closed: when stat raises on a canonical document path, the
    identity test cannot tell the root's document from the user-global file,
    so the classifier refuses the root as user_global. The same layout is a
    KNOWN root in the control above, so only the cannot-tell rule can turn it
    user_global. Red if cannot-tell answers "not global"."""
    real_stat = os.stat

    def refusing_stat(path, *args, **kwargs):
        if os.fspath(path).endswith("CLAUDE.md"):
            raise PermissionError(13, "cannot stat", os.fspath(path))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(project_scope.os, "stat", refusing_stat)

    assert memory_root_of(control.anchor) == (None, ROOT_CAUSE_USER_GLOBAL)


def test_no_computable_global_location_counts_as_user_global(control, monkeypatch):
    """Cannot tell where the global files are: refuse every root."""
    monkeypatch.setattr(project_scope, "user_global_documents", lambda: ())

    assert memory_root_of(control.anchor) == (None, ROOT_CAUSE_USER_GLOBAL)


def test_a_hardlink_to_the_global_file_counts_as_user_global(control):
    """A project's legacy CLAUDE.md that is a hardlink of the global file is
    the same file under another name: realpath strings differ, identity does
    not."""
    os.link(control.global_md, control.anchor / "CLAUDE.md")

    assert memory_root_of(control.anchor) == (None, ROOT_CAUSE_USER_GLOBAL)


def _case_insensitive(directory: Path) -> bool:
    probe = directory / "CaseProbe"
    probe.write_text("x", encoding="utf-8")
    try:
        return (directory / "caseprobe").exists()
    finally:
        probe.unlink()


def test_a_case_variant_spelling_of_the_config_dir_counts_as_user_global(
    tmp_path, monkeypatch
):
    world = _World(tmp_path, monkeypatch, "g_control")
    if not _case_insensitive(tmp_path):
        pytest.skip("the tmp volume matches names with case")
    variant = world.home / ".CLAUDE"

    assert memory_root_of(variant) == (None, ROOT_CAUSE_USER_GLOBAL)


def test_a_case_distinct_sibling_of_home_is_its_own_root(tmp_path, monkeypatch):
    """No over-block: where names match with case, <t>/Home beside <t>/home is
    another directory, so it keeps its own root."""
    world = _World(tmp_path, monkeypatch, "g_control")
    if _case_insensitive(tmp_path):
        pytest.skip("the tmp volume folds case, so Home and home are one directory")
    sibling = tmp_path / "Home"
    sibling.mkdir()

    assert memory_root_of(sibling) == (sibling, None)


class _SplitProfile:
    """V2: the active config root is <t>/cfg, HOME is <t>/home, and the
    session runs in <t>/home. <t>/home/.claude/CLAUDE.md is the default
    profile's global file, so <t>/home is not a project-memory root."""

    def __init__(self, tmp_path: Path, monkeypatch):
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.config = tmp_path / "cfg"
        self.config.mkdir()
        (self.config / "CLAUDE.md").write_text(_global_text(), encoding="utf-8")
        self.default_global = self.home / ".claude" / "CLAUDE.md"
        self.anchor = self.home
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR",
                    "GIT_CEILING_DIRECTORIES"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(self.config))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(self.anchor))
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: self.home))
        monkeypatch.chdir(self.anchor)
        monkeypatch.setattr(project_scope, "recorded_project_dir", lambda: "")
        monkeypatch.setattr(
            project_scope, "get_worktree_identity_from_session_record", lambda: {}
        )

    def session_start(self):
        env = {
            k: v for k, v in os.environ.items()
            if not k.startswith(("GIT_", "CLAUDE_", "PYTEST_"))
        }
        env.update(
            HOME=str(self.home),
            CLAUDE_CONFIG_DIR=str(self.config),
            CLAUDE_PROJECT_DIR=str(self.anchor),
        )
        stdin = json.dumps(
            {"session_id": SESSION_ID, "source": "startup", "agent_type": LEAD}
        )
        result = subprocess.run(
            [sys.executable, str(_HOOK)], input=stdin, cwd=str(self.anchor),
            env=env, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr[-500:]
        return json.loads(result.stdout).get("systemMessage", "")


def test_home_under_another_config_root_is_user_global(tmp_path, monkeypatch):
    world = _SplitProfile(tmp_path, monkeypatch)

    assert memory_root_of(world.anchor) == (None, ROOT_CAUSE_USER_GLOBAL)


def test_a_session_start_in_home_under_another_config_root_creates_nothing(
    tmp_path, monkeypatch
):
    world = _SplitProfile(tmp_path, monkeypatch)

    message = world.session_start()

    assert not world.default_global.exists()
    reason = unknown_root_reason(ROOT_CAUSE_USER_GLOBAL, world.anchor)
    assert f"Project CLAUDE.md skipped: {reason}" in message, message[-600:]


def test_a_session_start_in_home_leaves_the_default_profile_file_alone(
    tmp_path, monkeypatch
):
    world = _SplitProfile(tmp_path, monkeypatch)
    world.default_global.parent.mkdir()
    world.default_global.write_text(_global_text(), encoding="utf-8")
    before = world.default_global.read_bytes()

    world.session_start()

    assert world.default_global.read_bytes() == before


def _removed_declaration_in_a_repo(tmp_path, monkeypatch, dot_claude_is_home):
    """A removed declaration <repo>/gone resolving to <repo>, whose document
    is <repo>/.claude/CLAUDE.md. The config root is <t>/cfg, so the default
    profile's ~/.claude/CLAUDE.md is global only by the default-profile rule.
    With `dot_claude_is_home`, <repo>/.claude is a symlink to ~/.claude, so
    the document IS that global file under another path."""
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR",
                "GIT_CEILING_DIRECTORIES"):
        monkeypatch.delenv(key, raising=False)
    home = Path.home()
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "CLAUDE.md").write_text(_global_text(), encoding="utf-8")
    config = tmp_path / "cfg"
    config.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", ".", cwd=repo)
    _git("commit", "-q", "--allow-empty", "-m", "seed", cwd=repo)
    if dot_claude_is_home:
        (repo / ".claude").symlink_to(home / ".claude", target_is_directory=True)
    else:
        (repo / ".claude").mkdir()
        (repo / ".claude" / "CLAUDE.md").write_text("# p\n", encoding="utf-8")
    return repo / "gone", repo, repo / ".claude" / "CLAUDE.md"


def test_a_removed_declaration_never_resolves_into_the_default_profile_file(
    tmp_path, monkeypatch
):
    """The containment guard refuses a resolution whose document is the
    default profile's global file, even with CLAUDE_CONFIG_DIR elsewhere.
    Red when the guard compares only against the active config root."""
    declared, resolved, document = _removed_declaration_in_a_repo(
        tmp_path, monkeypatch, dot_claude_is_home=True
    )

    assert stays_in_declared_project(declared, resolved, document) is False


def test_a_removed_declaration_still_resolves_into_its_own_repository(
    tmp_path, monkeypatch
):
    """Control: the same layout with a project-owned .claude is admitted."""
    declared, resolved, document = _removed_declaration_in_a_repo(
        tmp_path, monkeypatch, dot_claude_is_home=False
    )

    assert stays_in_declared_project(declared, resolved, document) is True
