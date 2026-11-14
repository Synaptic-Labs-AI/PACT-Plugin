"""Shared target shapes and resolver routes for the identity matrix tests.

Location: pact-plugin/tests/fixtures/identity_routes.py

Used by tests/test_identity_rung_parity.py (every route that reaches a shape
must answer alike) and by the skill-prose override matrix (the routes the
skill names as never reading the override file). Neither test imports the
other; both import this module.

SHAPES maps a name to `builder(base) -> target`. Every target has a `sub/`
directory. Hostile shapes redirect the target's `.git` at a git directory
that is not its own; the `control_` shapes are genuine checkouts. Each
`control_own_` builder carries `layout`, its row name in PACTMemory.__init__'s
git-call table.

ROUTES maps a name to `(fn(monkeypatch, target, elsewhere), sources)`.
`fn` points one resolver rung at the target and returns the detected
identity (`_DetectedIdentity`, which carries `override_ignored`). `sources`
is the set of decline or identity sources that show this route's own rung
answered. `elsewhere` is a plain folder outside any repository. A route
named `(forced)` stubs a module attribute through the monkeypatch it is
given, so pass one that is undone after each cell.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from fixtures.identity_git import make_repo, run_git

from scripts import memory_api
from scripts.memory_api import PACTMemory

VICTIM = "https://github.com/victim/private.git"
VICTIM_ID = "github.com/victim/private"
OWN = "https://github.com/own/project.git"
UNUSABLE_PIN = "not a canonical id\n"
USER_SETTING_ID = "github.com/user/chosen"


# --- target shapes ---------------------------------------------------------


def _victim(base: Path) -> Path:
    return make_repo(base / "victim", remote=VICTIM)


def _target_with_gitfile(base: Path, gitdir) -> Path:
    target = base / "target"
    target.mkdir()
    (target / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    return target


def _target_with_git_link(base: Path, destination: Path) -> Path:
    target = base / "target"
    target.mkdir()
    (target / ".git").symlink_to(destination)
    return target


def _victim_with_submodule(base: Path) -> Path:
    upstream = make_repo(base / "upstream-sub", remote=VICTIM)
    victim = make_repo(base / "victim", remote="https://github.com/victim/super.git")
    run_git(
        "-c", "protocol.file.allow=always", "submodule", "add", "-q",
        str(upstream), "libs/sub", cwd=victim,
    )
    run_git("remote", "set-url", "origin", VICTIM, cwd=victim / "libs" / "sub")
    return victim


def _forged_worktree_admin(base: Path) -> Path:
    victim = _victim(base)
    run_git("worktree", "add", "-q", str(base / "victim-wt"), "-b", "wt", cwd=victim)
    return _target_with_gitfile(base, victim / ".git" / "worktrees" / "victim-wt")


def _forged_submodule_admin(base: Path) -> Path:
    victim = _victim_with_submodule(base)
    return _target_with_gitfile(base, victim / ".git" / "modules" / "libs" / "sub")


def _symlinked_submodule_admin(base: Path) -> Path:
    victim = _victim_with_submodule(base)
    return _target_with_git_link(base, victim / ".git" / "modules" / "libs" / "sub")


def _forged_separate_store_worktree(base: Path) -> Path:
    run_git("init", "-q", f"--separate-git-dir={base / 'store.git'}", str(base / "victim"), cwd=base)
    run_git("remote", "add", "origin", VICTIM, cwd=base / "victim")
    run_git(
        "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q",
        "--allow-empty", "-m", "i", cwd=base / "victim",
    )
    run_git("worktree", "add", "-q", str(base / "victim-wt"), "-b", "wt", cwd=base / "victim")
    return _target_with_gitfile(base, base / "store.git" / "worktrees" / "victim-wt")


def _foreign_gitdir(base: Path) -> Path:
    return _target_with_gitfile(base, _victim(base) / ".git")


def _relative_foreign_gitdir(base: Path) -> Path:
    _victim(base)
    return _target_with_gitfile(base, "../victim/.git")


def _symlinked_git(base: Path) -> Path:
    return _target_with_git_link(base, _victim(base) / ".git")


def _dangling_gitfile(base: Path) -> Path:
    return _target_with_gitfile(base, base / "missing")


def _victim_bare(base: Path) -> Path:
    run_git("init", "-q", "--bare", str(base / "victim.git"), cwd=base)
    run_git("remote", "add", "origin", VICTIM, cwd=base / "victim.git")
    return base / "victim.git"


def _bare_target(base: Path) -> Path:
    return _target_with_gitfile(base, _victim_bare(base))


def _symlinked_bare(base: Path) -> Path:
    return _target_with_git_link(base, _victim_bare(base))


def _foreign_external_store(base: Path) -> Path:
    run_git("init", "-q", f"--separate-git-dir={base / 'store.git'}", str(base / "victim"), cwd=base)
    run_git("remote", "add", "origin", VICTIM, cwd=base / "victim")
    return _target_with_gitfile(base, base / "store.git")


def _own_checkout(base: Path) -> Path:
    return make_repo(base / "target", remote=OWN)


def _own_linked_worktree(base: Path) -> Path:
    main = make_repo(base / "main", remote=OWN)
    run_git("worktree", "add", "-q", str(base / "target"), "-b", "wt", cwd=main)
    return base / "target"


def _own_submodule(base: Path) -> Path:
    upstream = make_repo(base / "upstream", remote=OWN)
    superproject = make_repo(base / "super", remote="https://github.com/own/super.git")
    run_git(
        "-c", "protocol.file.allow=always", "submodule", "add", "-q",
        str(upstream), "target", cwd=superproject,
    )
    run_git("remote", "set-url", "origin", OWN, cwd=superproject / "target")
    return superproject / "target"


def _own_separate_git_dir(base: Path) -> Path:
    run_git("init", "-q", f"--separate-git-dir={base / 'store.git'}", str(base / "target"), cwd=base)
    run_git("remote", "add", "origin", OWN, cwd=base / "target")
    run_git(
        "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q",
        "--allow-empty", "-m", "i", cwd=base / "target",
    )
    return base / "target"


def _own_committed_superproject(base: Path) -> Path:
    """A superproject holding the own repository as submodule `target`, with
    the submodule recorded in a commit so worktrees of either can be added."""
    upstream = make_repo(base / "upstream", remote=OWN)
    superproject = make_repo(base / "super", remote="https://github.com/own/super.git")
    run_git(
        "-c", "protocol.file.allow=always", "submodule", "add", "-q",
        str(upstream), "target", cwd=superproject,
    )
    run_git("-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "-m", "s", cwd=superproject)
    run_git("remote", "set-url", "origin", OWN, cwd=superproject / "target")
    return superproject


def _own_submodule_worktree(base: Path) -> Path:
    superproject = _own_committed_superproject(base)
    run_git("worktree", "add", "-q", str(base / "target"), "-b", "wt", cwd=superproject / "target")
    return base / "target"


def _own_superproject_worktree_submodule(base: Path) -> Path:
    superproject = _own_committed_superproject(base)
    run_git("worktree", "add", "-q", str(base / "super-wt"), "-b", "swt", cwd=superproject)
    run_git(
        "-c", "protocol.file.allow=always", "submodule", "update", "-q", "--init",
        cwd=base / "super-wt",
    )
    run_git("remote", "set-url", "origin", OWN, cwd=base / "super-wt" / "target")
    return base / "super-wt" / "target"


# THE OWN LAYOUTS' NAMES IN PACTMemory.__init__'s git-call table. Every
# `control_own_` builder carries one; the git-call floor reads its layouts
# from these, so a new own layout must state its row there.
_own_checkout.layout = "checkout"
_own_linked_worktree.layout = "linked worktree"
_own_submodule.layout = "submodule"
_own_separate_git_dir.layout = "separate-git-dir checkout"
_own_submodule_worktree.layout = "linked worktree of a submodule"
_own_superproject_worktree_submodule.layout = "submodule of a superproject worktree"


_BUILDERS = {
    "forged_worktree_admin": _forged_worktree_admin,
    "forged_submodule_admin": _forged_submodule_admin,
    "symlinked_submodule_admin": _symlinked_submodule_admin,
    "forged_separate_store_worktree": _forged_separate_store_worktree,
    "foreign_gitdir": _foreign_gitdir,
    "relative_foreign_gitdir": _relative_foreign_gitdir,
    "symlinked_git": _symlinked_git,
    "dangling_gitfile": _dangling_gitfile,
    "bare_target": _bare_target,
    "symlinked_bare": _symlinked_bare,
    "foreign_external_store": _foreign_external_store,
    "control_own_checkout": _own_checkout,
    "control_own_linked_worktree": _own_linked_worktree,
    "control_own_submodule": _own_submodule,
    "control_own_separate_git_dir": _own_separate_git_dir,
    "control_own_submodule_worktree": _own_submodule_worktree,
    "control_own_superproject_worktree_submodule": _own_superproject_worktree_submodule,
}


def _with_sub(build):
    def builder(base: Path) -> Path:
        target = build(base)
        (target / "sub").mkdir(exist_ok=True)
        return target

    builder.__name__ = build.__name__
    if hasattr(build, "layout"):
        builder.layout = build.layout
    return builder


SHAPES = {name: _with_sub(build) for name, build in _BUILDERS.items()}


def plant_unusable_pin(target: Path, text: str = UNUSABLE_PIN) -> Optional[Path]:
    """Write `text` (an unusable id unless given) as the `pact-project-id` in
    the common dir git reports for `target`. Returns its path, or None when
    git reports no common dir."""
    result = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    pin = Path(result.stdout.strip()) / "pact-project-id"
    pin.write_text(text, encoding="utf-8")
    return pin


# --- routes ----------------------------------------------------------------


def _clear(monkeypatch):
    for name in ("CLAUDE_PROJECT_DIR", "GIT_CEILING_DIRECTORIES"):
        monkeypatch.delenv(name, raising=False)
    memory_api._LEGACY_SCOPE_CACHE.clear()


def _detect():
    return PACTMemory._detect_project_identity_with_legacy()


def _git_root(monkeypatch, target, elsewhere):
    monkeypatch.chdir(target)
    return _detect()


def _git_root_subdirectory(monkeypatch, target, elsewhere):
    monkeypatch.chdir(target / "sub")
    return _detect()


def _git_root_via_symlink(monkeypatch, target, elsewhere):
    link = elsewhere / "cwd-link"
    if not link.exists():
        link.symlink_to(target)
    monkeypatch.chdir(link / "sub")
    return _detect()


def _declared(monkeypatch, target, elsewhere):
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(target))
    return _detect()


def _declared_subdirectory(monkeypatch, target, elsewhere):
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(target / "sub"))
    return _detect()


def _declared_via_symlink(monkeypatch, target, elsewhere):
    link = elsewhere / "link-to-target"
    if not link.exists():
        link.symlink_to(target)
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(link))
    return _detect()


def _session_record(monkeypatch, target, elsewhere):
    """FORCED: the session record is inert in a test process."""
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(memory_api, "get_project_dir_from_session_record", lambda: str(target))
    return _detect()


def _session_record_subdirectory(monkeypatch, target, elsewhere):
    """FORCED: as `_session_record`, naming a subdirectory."""
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(
        memory_api, "get_project_dir_from_session_record", lambda: str(target / "sub")
    )
    return _detect()


def _worktree_identity_record(monkeypatch, target, elsewhere):
    """FORCED: a record the record check accepts names a verified main
    checkout, so the record check is made to return the target."""
    from shared import project_scope

    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(elsewhere / "gone-worktree"))
    monkeypatch.setattr(project_scope, "recorded_main_root", lambda declared, identity: target)
    return _detect()


def _cwd_marker(monkeypatch, target, elsewhere):
    """A `GIT_CEILING_DIRECTORIES` entry at the checkout root hides it from
    git and from the marker walk, so resolution reaches the cwd-marker rung."""
    monkeypatch.chdir(target / "sub")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(target))
    return _detect()


def _cwd_marker_deeper(monkeypatch, target, elsewhere):
    """As `_cwd_marker`, with the ceiling one level below the root and the
    cwd one level below the ceiling."""
    (target / "sub" / "deeper").mkdir(exist_ok=True)
    monkeypatch.chdir(target / "sub" / "deeper")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(target / "sub"))
    return _detect()


def _user_setting_over_cwd_marker(monkeypatch, target, elsewhere):
    """FORCED: as `_cwd_marker`, with a user-scope `project_id` set. The
    setting's directory is stubbed (`get_user_memory_dir`) because the suite
    points the real one at a shared test directory; the stub replaces no git
    answer. A repository the cwd marker finds outranks the setting, so the
    cwd rung answers on every repository it finds and the setting is read
    only where it finds none."""
    import json

    setting_dir = elsewhere / "user-memory"
    setting_dir.mkdir(exist_ok=True)
    (setting_dir / "config.json").write_text(
        json.dumps({"project_id": USER_SETTING_ID}), encoding="utf-8"
    )
    monkeypatch.setattr(memory_api, "get_user_memory_dir", lambda: setting_dir)
    return _cwd_marker(monkeypatch, target, elsewhere)


ROUTES = {
    "git root": (_git_root, {"git root", "origin remote"}),
    "git root (subdirectory)": (_git_root_subdirectory, {"git root", "origin remote"}),
    "git root (via symlinked cwd)": (_git_root_via_symlink, {"git root", "origin remote"}),
    "CLAUDE_PROJECT_DIR": (_declared, {"CLAUDE_PROJECT_DIR"}),
    "CLAUDE_PROJECT_DIR (subdirectory)": (_declared_subdirectory, {"CLAUDE_PROJECT_DIR"}),
    "CLAUDE_PROJECT_DIR (via symlink)": (_declared_via_symlink, {"CLAUDE_PROJECT_DIR"}),
    "session record (forced)": (_session_record, {"session record"}),
    "session record (subdirectory, forced)": (_session_record_subdirectory, {"session record"}),
    # The guard's decline names the record's rung; an identity-layer decline
    # names the declaration that asked, as on the declared rung.
    "worktree identity record (forced)": (
        _worktree_identity_record, {"worktree identity record", "CLAUDE_PROJECT_DIR"},
    ),
    "cwd marker": (_cwd_marker, {"cwd"}),
    "cwd marker (ceiling one level down)": (_cwd_marker_deeper, {"cwd"}),
    # The setting answers only where the cwd marker finds no repository.
    "user setting over a cwd marker (forced)": (
        _user_setting_over_cwd_marker, {"cwd", "user setting"},
    ),
}

FORCED_ROUTES = frozenset(name for name in ROUTES if "(forced)" in name or ", forced)" in name)


def _answer(detected) -> tuple:
    """(answer, answering rung): the decline reason, or the bound id."""
    record = detected.decline_reason or {}
    if record:
        return record.get("reason"), record.get("source")
    return f"bound:{detected.project_id}", detected.source


def _override(detected) -> Optional[tuple]:
    """(reason, path withheld) of a reported unusable override file, or None
    when none was reported."""
    ignored = detected.override_ignored
    if not ignored:
        return None
    return ignored.get("reason"), ignored.get("path") is None


# Routes that do not reach a shape, by measurement: {(shape, route): the rung
# that answered instead}.
NOT_REACHED: dict = {}
