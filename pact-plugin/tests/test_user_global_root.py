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
    ROOT_CAUSE_HOME_UNKNOWN,
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


def test_no_computable_global_location_refuses_with_its_own_cause(control, monkeypatch):
    """Cannot tell where the global files are: refuse every root, naming the
    missing home rather than claiming the root holds the global file."""
    monkeypatch.setattr(project_scope, "user_global_documents", lambda: ())

    assert memory_root_of(control.anchor) == (None, ROOT_CAUSE_HOME_UNKNOWN)


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


def test_a_removed_declaration_never_resolves_into_a_git_home(tmp_path, monkeypatch):
    """No record: a removed declaration under a git-versioned $HOME, landing
    at $HOME with the legacy $HOME/CLAUDE.md, is refused. $HOME's own
    .claude/CLAUDE.md is the default profile's global file, so the root is
    user-global whichever document was found."""
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR",
                "GIT_CEILING_DIRECTORIES"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    home = Path.home()
    home.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", ".", cwd=home)
    _git("commit", "-q", "--allow-empty", "-m", "seed", cwd=home)
    legacy = home / "CLAUDE.md"
    legacy.write_text("# legacy\n", encoding="utf-8")

    assert stays_in_declared_project(home / "proj-gone", home, legacy) is False


# ---------------------------------------------------------------------------
# The containment guard when the HOME DIRECTORY CANNOT BE COMPUTED. Path.home()
# raises. `get_claude_config_dir` reads the home before it reads
# CLAUDE_CONFIG_DIR, so NEITHER global location can be computed, and "unable to
# tell" reads as user-global: the removed-declaration branch refuses every
# resolution, its own repository included. Fail-closed, so no write can reach a
# global file. Each arm asserts the raising home was consulted, so none can
# pass on a route that never reached the lookup; the control shows the same
# layout is admitted when the home is computable.
# ---------------------------------------------------------------------------


class _HomeUnknown:
    def __init__(self, tmp_path, monkeypatch):
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR",
                    "GIT_CEILING_DIRECTORIES"):
            monkeypatch.delenv(key, raising=False)
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.config = self.repo("cfg")
        (self.config / "CLAUDE.md").write_text(_global_text(), encoding="utf-8")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(self.config))
        self.calls = 0

    def repo(self, name, document=False):
        repo = self.tmp_path / name
        repo.mkdir()
        _git("init", "-q", ".", cwd=repo)
        _git("commit", "-q", "--allow-empty", "-m", "seed", cwd=repo)
        if document:
            (repo / ".claude").mkdir()
            (repo / ".claude" / "CLAUDE.md").write_text("# p\n", encoding="utf-8")
        return repo

    def judge(self, declared, resolved, document):
        assert not declared.exists()

        def unknown_home(*_args, **_kwargs):
            self.calls += 1
            raise RuntimeError("Could not determine home directory.")

        self.monkeypatch.setattr(Path, "home", unknown_home)
        verdict = stays_in_declared_project(declared, resolved, document)
        assert self.calls >= 1, "the guard never consulted the home directory"
        return verdict


def test_a_removed_declaration_in_its_own_repository_is_admitted_when_home_is_known(
    tmp_path, monkeypatch
):
    """Control for the three arms below: with a computable home this layout
    is admitted, so their refusals come from the unknown home."""
    world = _HomeUnknown(tmp_path, monkeypatch)
    repo = world.repo("repo", document=True)

    assert stays_in_declared_project(
        repo / "gone", repo, repo / ".claude" / "CLAUDE.md"
    ) is True


def test_home_unknown_refuses_a_removed_declaration_in_its_own_repository(
    tmp_path, monkeypatch
):
    """Red if an unknown home lets the same-repository rules admit it, which
    happens when the config root is computed without the home."""
    world = _HomeUnknown(tmp_path, monkeypatch)
    repo = world.repo("repo", document=True)

    verdict = world.judge(repo / "gone", repo, repo / ".claude" / "CLAUDE.md")

    assert verdict is False


def test_home_unknown_refuses_a_removed_declaration_landing_in_another_repository(
    tmp_path, monkeypatch
):
    world = _HomeUnknown(tmp_path, monkeypatch)
    repo = world.repo("repo")
    other = world.repo("other", document=True)

    verdict = world.judge(repo / "gone", other, other / ".claude" / "CLAUDE.md")

    assert verdict is False


def test_home_unknown_refuses_the_config_roots_global_file(tmp_path, monkeypatch):
    """The config root is a git checkout and a declaration removed inside it
    resolves to its CLAUDE.md, which every session loads."""
    world = _HomeUnknown(tmp_path, monkeypatch)
    config = world.config

    verdict = world.judge(config / "gone", config, config / "CLAUDE.md")

    assert verdict is False


# ---------------------------------------------------------------------------
# The case-variant rule, on ANY volume. The arm above needs a case-insensitive
# tmp volume, so it skips on Linux CI. Here the volume's answer is supplied:
# `_case_insensitive` reports the config root's directory as case-insensitive,
# and the global file does not exist yet, so the comparison takes the
# create-target route (same existing ancestor, names equal case-folded).
# Red if that route stops folding case.
# ---------------------------------------------------------------------------


def _config_root_without_a_document(tmp_path, monkeypatch):
    config = tmp_path / "cfg"
    config.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    assert not (config / "CLAUDE.md").exists()
    assert not (Path.home() / ".claude" / "CLAUDE.md").exists()
    return config


def _report_case_insensitive_at(monkeypatch, folding_dir):
    seen = []

    def case_insensitive(directory):
        seen.append(directory)
        return os.path.samefile(directory, folding_dir)

    monkeypatch.setattr(project_scope, "_case_insensitive", case_insensitive)
    return seen


def test_a_case_variant_of_the_global_file_counts_as_user_global_on_any_volume(
    tmp_path, monkeypatch
):
    config = _config_root_without_a_document(tmp_path, monkeypatch)
    seen = _report_case_insensitive_at(monkeypatch, config)

    assert project_scope.is_user_global_document(config / "claude.md") is True
    assert seen, "the case-folding route was never consulted"


def test_a_different_name_beside_the_global_file_is_not_user_global(
    tmp_path, monkeypatch
):
    """No over-block: on the same case-insensitive volume, a name that is not
    the global file's under case folding stays a different file."""
    config = _config_root_without_a_document(tmp_path, monkeypatch)
    _report_case_insensitive_at(monkeypatch, config)

    assert project_scope.is_user_global_document(config / "AGENTS.md") is False


def test_a_case_variant_is_not_user_global_where_the_volume_matches_case(
    tmp_path, monkeypatch
):
    """Control: with the volume reported case-sensitive, the same spelling
    names another file, so the arm above is decided by the case answer."""
    config = _config_root_without_a_document(tmp_path, monkeypatch)
    monkeypatch.setattr(project_scope, "_case_insensitive", lambda _directory: False)

    assert project_scope.is_user_global_document(config / "claude.md") is False


def _plain_project(tmp_path, monkeypatch) -> Path:
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR",
                "GIT_CEILING_DIRECTORIES"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(project_scope, "recorded_project_dir", lambda: "")
    project = tmp_path / "proj"
    project.mkdir()
    return project


def test_a_config_root_spelled_with_dots_over_a_missing_segment_is_the_same_root(
    tmp_path, monkeypatch
):
    """CLAUDE_CONFIG_DIR=<proj>/missing/../.claude names <proj>/.claude, so the
    project's .claude/CLAUDE.md is the global file however it is spelled."""
    project = _plain_project(tmp_path, monkeypatch)
    monkeypatch.setenv(
        "CLAUDE_CONFIG_DIR", os.path.join(str(project), "missing", "..", ".claude")
    )
    assert not (project / "missing").exists()

    assert memory_root_of(project) == (None, ROOT_CAUSE_USER_GLOBAL)


def test_a_relative_config_root_is_read_against_the_cwd(tmp_path, monkeypatch):
    """A relative CLAUDE_CONFIG_DIR is the cwd-relative path, as every other
    consumer of the config root reads it: from <t>/work, `../proj/.claude`
    names the project's .claude, and from elsewhere the same value names
    another directory, so the project keeps its root."""
    project = _plain_project(tmp_path, monkeypatch)
    work = tmp_path / "work"
    (work / "deeper").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", os.path.join("..", "proj", ".claude"))

    monkeypatch.chdir(work)
    assert memory_root_of(project) == (None, ROOT_CAUSE_USER_GLOBAL)

    monkeypatch.chdir(work / "deeper")
    assert memory_root_of(project) == (project, None)


def test_an_absolute_config_root_elsewhere_leaves_a_project_known(tmp_path, monkeypatch):
    project = _plain_project(tmp_path, monkeypatch)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))

    assert memory_root_of(project) == (project, None)


def test_a_relative_config_root_is_also_read_against_the_declared_project(
    tmp_path, monkeypatch
):
    """Launched in <proj> with CLAUDE_CONFIG_DIR=.claude, then run from
    <proj>/sub: the cwd spelling names <proj>/sub/.claude, but the platform's
    launch-time reading is <proj>/.claude, so <proj> is still user-global."""
    project = _plain_project(tmp_path, monkeypatch)
    (project / "sub").mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", ".claude")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.chdir(project / "sub")

    assert memory_root_of(project) == (None, ROOT_CAUSE_USER_GLOBAL)


def test_a_relative_config_root_outside_the_declared_project_leaves_it_known(
    tmp_path, monkeypatch
):
    """Control: a relative value naming a directory outside <proj> from both
    the cwd and the declaration does not touch <proj>'s root."""
    project = _plain_project(tmp_path, monkeypatch)
    (project / "sub").mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", os.path.join("..", "cfg"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.chdir(project / "sub")

    assert memory_root_of(project) == (project, None)


def test_the_home_unknown_reason_text():
    assert unknown_root_reason(ROOT_CAUSE_HOME_UNKNOWN, "/h") == (
        "the home directory cannot be found"
    )


def test_a_session_with_no_home_and_no_config_root_names_the_missing_home(
    tmp_path, monkeypatch
):
    """CLAUDE_CONFIG_DIR unset and Path.home() raising: neither user-global
    location exists, so the root is refused (fail closed) with the cause that
    says why, and the skip line carries that text."""
    project = _plain_project(tmp_path, monkeypatch)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    def no_home(cls=None):
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", classmethod(no_home))
    assert project_scope.user_global_documents() == ()

    assert memory_root_of(project) == (None, ROOT_CAUSE_HOME_UNKNOWN)
    assert project_scope.unknown_root_status(
        "Project CLAUDE.md", ROOT_CAUSE_HOME_UNKNOWN, project, "nothing was written."
    ).startswith("Project CLAUDE.md skipped: the home directory cannot be found, ")


def _symlink_then_dots(tmp_path, monkeypatch, leaf: str) -> Path:
    """<proj>/link points at <t>/elsewhere/inner, and CLAUDE_CONFIG_DIR is
    <proj>/link/../<leaf>: the kernel reads <t>/elsewhere/<leaf>, a lexical
    normaliser reads <proj>/<leaf>."""
    project = _plain_project(tmp_path, monkeypatch)
    inner = tmp_path / "elsewhere" / "inner"
    inner.mkdir(parents=True)
    (project / "link").symlink_to(inner, target_is_directory=True)
    monkeypatch.setenv(
        "CLAUDE_CONFIG_DIR", os.path.join(str(project), "link", "..", leaf)
    )
    return project


def test_a_symlink_then_dots_config_root_is_also_read_lexically(tmp_path, monkeypatch):
    project = _symlink_then_dots(tmp_path, monkeypatch, ".claude")

    assert memory_root_of(project) == (None, ROOT_CAUSE_USER_GLOBAL)


def test_a_symlink_then_dots_config_root_naming_neither_form_leaves_a_project_known(
    tmp_path, monkeypatch
):
    """Control: <proj>/link/../cfg is <proj>/cfg lexically and
    <t>/elsewhere/cfg by the kernel; neither is a canonical document of
    <proj>."""
    project = _symlink_then_dots(tmp_path, monkeypatch, "cfg")

    assert memory_root_of(project) == (project, None)
