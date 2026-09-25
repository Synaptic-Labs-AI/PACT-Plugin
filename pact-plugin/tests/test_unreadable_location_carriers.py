"""
Location: pact-plugin/tests/test_unreadable_location_carriers.py
Summary: Holds every existence probe in the project CLAUDE.md writers, the
         stale-session reader, the global kernel-block strip, the project-id
         walk and the worktree guard to ONE behaviour on every supported
         interpreter. The pact-memory and staleness CLAUDE.md resolvers join
         the probe table here; their own arms are in
         test_claude_md_resolver_parity.py.
Used by: the full pytest suite, on each CI interpreter.

THE SPLIT THESE ARMS CLOSE. `Path.exists()` and `Path.is_dir()` re-raise a
PermissionError on 3.9-3.13 and return False on 3.14, so a directory the
process cannot search aborted a caller on two CI interpreters and was skipped
as absent on the third. Every arm below failed against the pre-fix code on at
least one CI interpreter, and its docstring names which one.

WHAT "UNREADABLE" MEANS HERE DEPENDS ON THE CALLER, AND THE ARMS SAY WHICH:
- a project CLAUDE.md writer REPORTS it as a failed status and writes nothing,
  and never falls back to the lower-priority legacy file; the stale-session
  reader, which follows the same precedence, stays silent;
- the global kernel-block strip treats it as ABSENT, since it cannot strip a
  file it cannot read;
- an upward walk STOPS at it, rather than climbing to a parent's marker.

EACCES NEEDS A NON-ROOT PROCESS. Root searches a mode-0 directory, so those
arms SKIP under root with that reason; they never pass without the trigger.
"""

import ast
import importlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import bootstrap_marker_writer as bmw
import worktree_guard
from scripts import memory_api
from scripts.memory_api import PACTMemory
from shared import claude_md_manager as cmm
from shared import session_resume

_NEEDS_NON_ROOT = pytest.mark.skipif(
    os.geteuid() == 0,
    reason="root searches a mode-0 directory, so no EACCES can be built",
)

_LEGACY_TEXT = "# Project Memory\n\n## Working Memory\n\nuser notes\n"

_WORKTREE_GUARD = Path(__file__).parent.parent / "hooks" / "worktree_guard.py"


@pytest.fixture
def lock():
    """Make directories unsearchable for one test, and restore them after it.

    The restore is what lets tmp_path's cleanup remove the tree afterwards.
    """
    locked = []

    def _lock(path):
        path.chmod(0o000)
        locked.append(path)

    yield _lock
    for path in reversed(locked):
        path.chmod(0o700)


def _symlink_loop(path):
    """Make `path` one end of a two-link symlink loop, and return it."""
    partner = path.with_name(path.name + "_partner")
    os.symlink(partner.name, path)
    os.symlink(path.name, partner)
    return path


# --- The probe, copied into three modules ------------------------------------

_PROBE_OWNERS = (
    "shared.claude_md_manager",
    "shared.stale_session",
    "scripts.memory_api",
    "worktree_guard",
    "scripts.working_memory",
    "staleness",
)


def _outcome(probe, path):
    try:
        return "absent" if probe(path) is None else "present"
    except OSError as exc:
        return type(exc).__name__


@_NEEDS_NON_ROOT
def test_every_copy_of_the_probe_counts_the_same_errors_as_absent(tmp_path, lock):
    """Four modules carry their own copy of the probe: worktree_guard imports
    only the stdlib, stale_session does not import claude_md_manager at
    runtime, and memory_api and working_memory sit outside hooks/. staleness
    imports the canonical one, which its CLAUDE.md resolver decides existence
    with. This arm holds all six to one table, so an edit to one copy reddens
    here instead of drifting silently.

    The table is 3.9-3.13 pathlib's own rule, made explicit so 3.14 follows it:
    a path that is not there (ENOENT, ENOTDIR, ELOOP, an unencodable path) is
    absent; a path the process may not search raises.
    """
    present = tmp_path / "present.txt"
    present.write_text("x")
    locked = tmp_path / "locked"
    (locked / "target").mkdir(parents=True)
    marker = tmp_path / "marker"
    marker.symlink_to(locked / "target")
    lock(locked)
    cases = {
        "present": (present, "present"),
        "ENOENT": (tmp_path / "missing", "absent"),
        "ENOTDIR": (present / "child", "absent"),
        "ELOOP": (_symlink_loop(tmp_path / "loop"), "absent"),
        "NUL byte": (str(tmp_path / "nul\0byte"), "absent"),
        "EACCES under a mode-0 dir": (locked / "target", "PermissionError"),
        "EACCES through a marker symlink": (marker, "PermissionError"),
    }
    expected = {name: want for name, (_path, want) in cases.items()}
    for owner in _PROBE_OWNERS:
        probe = getattr(importlib.import_module(owner), "_stat_if_present")
        observed = {name: _outcome(probe, path) for name, (path, _want) in cases.items()}
        assert observed == expected, f"{owner} disagrees with the shared table"


_PROBED_FUNCTIONS = {
    "hooks/shared/claude_md_manager.py": {
        "strip_orphan_kernel_block",
        "resolve_project_claude_md_path",
        "ensure_dot_claude_parent",
        "ensure_project_memory_md",
        "migrate_to_managed_structure",
    },
    "hooks/shared/session_resume.py": {"update_session_info"},
    "hooks/shared/stale_session.py": {"detect_stale_session_block"},
    "hooks/bootstrap_marker_writer.py": {"_write_back_aligned_team_name"},
    "hooks/worktree_guard.py": {
        "_find_project_root",
        "_suggest_worktree_path",
        "check_worktree_boundary",
    },
    "skills/pact-memory/scripts/memory_api.py": {"_find_project_root", "main_repo_root"},
}

_SPLIT_PREDICATES = {"exists", "is_dir", "is_file", "is_symlink"}


def _split_predicate_calls(source, names):
    """Every `<expr>.exists()`-style call inside the named functions, and the
    set of those functions found. `os.path.exists(...)` is uniform across
    interpreters and is not reported."""
    found, seen = [], set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef) or node.name not in names:
            continue
        seen.add(node.name)
        for inner in ast.walk(node):
            if not (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)):
                continue
            receiver = inner.func.value
            is_os_path = isinstance(receiver, ast.Attribute) and receiver.attr == "path"
            if inner.func.attr in _SPLIT_PREDICATES and not is_os_path:
                found.append(f"{node.name}:{inner.lineno} .{inner.func.attr}()")
    return found, seen


def test_the_scanner_finds_a_split_predicate():
    """The control for the pin below: a scanner that finds nothing here is
    blind, not satisfied."""
    source = "def f(p):\n    return p.exists() or p.is_dir() or os.path.exists(p)\n"
    found, seen = _split_predicate_calls(source, {"f"})
    assert found == ["f:2 .exists()", "f:2 .is_dir()"]
    assert seen == {"f"}


def test_no_carrier_calls_a_split_predicate():
    """The functions this file covers call no pathlib predicate that splits.

    A STATIC PIN, BECAUSE SOME SITES HAVE NO BEHAVIOURAL ARM. The existence
    probes in ensure_dot_claude_parent, inside ensure_project_memory_md's lock
    and inside update_session_info's lock run on a path the resolver has just
    probed, so only a permission change between the two could reach them with
    an unreadable target. Re-adding `.exists()` there reddens here instead.
    """
    plugin_root = Path(__file__).parent.parent
    for rel, names in _PROBED_FUNCTIONS.items():
        found, seen = _split_predicate_calls((plugin_root / rel).read_text(), names)
        assert seen == names, f"{rel}: functions not found: {names - seen}"
        assert found == [], f"{rel}: split predicates remain: {found}"


# --- Carrier 1: the project CLAUDE.md writers --------------------------------


def _preferred_unsearchable(tmp_path, lock):
    """An unsearchable .claude/ beside a readable legacy ./CLAUDE.md.

    The preferred file EXISTS. Only the process's permission to reach it is
    missing, so a writer that reads this as absent rewrites the legacy file
    and leaves the project with two diverging memory files.
    """
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "CLAUDE.md").write_text("preferred\n")
    legacy = proj / "CLAUDE.md"
    legacy.write_text(_LEGACY_TEXT)
    lock(proj / ".claude")
    return proj, legacy


def _ancestor_unsearchable(tmp_path, lock):
    """A project directory below a directory the process cannot search."""
    proj = tmp_path / "locked" / "proj"
    (proj / ".claude").mkdir(parents=True)
    lock(tmp_path / "locked")
    return proj, None


_LAYOUTS = {
    "preferred-unsearchable": _preferred_unsearchable,
    "ancestor-unsearchable": _ancestor_unsearchable,
}

_WRITERS = {
    "ensure_project_memory_md": (
        lambda: cmm.ensure_project_memory_md(),
        "Project CLAUDE.md failed:",
    ),
    "migrate_to_managed_structure": (
        lambda: cmm.migrate_to_managed_structure(),
        "Migration failed:",
    ),
    "update_session_info": (
        lambda: session_resume.update_session_info("sess-1", "team-1"),
        "Session info failed:",
    ),
}


@_NEEDS_NON_ROOT
class TestProjectClaudeMdWriters:
    """An unreadable preferred location is an ERROR the writer reports."""

    def test_the_resolver_raises_rather_than_falling_back_to_legacy(
        self, tmp_path, lock
    ):
        """RED BEFORE THE FIX ON 3.14, which returned the legacy file."""
        proj, _legacy = _preferred_unsearchable(tmp_path, lock)
        with pytest.raises(PermissionError):
            cmm.resolve_project_claude_md_path(proj)

    @pytest.mark.parametrize("layout", sorted(_LAYOUTS))
    @pytest.mark.parametrize("writer", sorted(_WRITERS))
    def test_each_writer_reports_the_error_and_writes_nothing(
        self, writer, layout, tmp_path, lock, monkeypatch
    ):
        """Each writer returns its routed failed status and raises nothing.

        RED BEFORE THE FIX: on 3.9 and 3.13 every writer RAISED, in both
        layouts, from a probe outside its try, which aborts session start. On
        3.14 migrate and update_session_info REWROTE the legacy file in the
        preferred-unsearchable layout, ensure_project_memory_md returned None
        there, and in the ancestor layout migrate returned None and
        update_session_info raised.
        """
        proj, legacy = _LAYOUTS[layout](tmp_path, lock)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
        call, prefix = _WRITERS[writer]

        result = call()

        assert result is not None and result.startswith(prefix), result
        assert "PermissionError (EACCES)" in result, result
        if legacy is not None:
            assert legacy.read_text() == _LEGACY_TEXT, "the legacy file was rewritten"

    def test_a_dot_claude_file_is_reported_rather_than_raised(
        self, tmp_path, monkeypatch
    ):
        """A BEHAVIOUR CHANGE BEYOND THE INTERPRETER SPLIT. When .claude is a
        regular FILE, ensure_dot_claude_parent refuses to write beneath it, as
        it always has. update_session_info used to call it outside its try,
        so on EVERY interpreter the refusal escaped into session_init's safety
        net and skipped the rest of session start. It is now the routed
        failed status. RED BEFORE THE FIX ON ALL THREE INTERPRETERS.
        """
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / ".claude").write_text("not a directory\n")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))

        result = session_resume.update_session_info("sess-1", "team-1")

        assert result is not None and result.startswith("Session info failed:"), result
        assert (proj / ".claude").read_text() == "not a directory\n"
        assert not (proj / "CLAUDE.md").exists(), "a legacy file was created"

    def test_session_start_reports_and_still_runs_its_later_steps(
        self, tmp_path, lock, monkeypatch
    ):
        """session_init runs all three writers and routes their statuses.

        "Session info failed" comes from step 5b, long after step 3, so its
        presence shows the step-3 error did not abort the rest of session
        start. RED BEFORE THE FIX: on 3.9 and 3.13 step 3 raised into main()'s
        safety net, which skips every later step; on 3.14 no writer failed and
        two of them rewrote the legacy file.
        """
        from session_init import main

        proj, legacy = _preferred_unsearchable(tmp_path, lock)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        stdin = json.dumps({
            "session_id": "aabb1122-0000-0000-0000-000000000000",
            "source": "startup",
            "agent_type": "pact-orchestrator",
        })

        with patch("session_init.setup_plugin_symlinks", return_value=None), \
             patch("session_init.check_pinned_staleness", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("session_init.restore_last_session", return_value=None), \
             patch("session_init.check_resume_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin)), \
             patch("sys.stdout", new_callable=io.StringIO) as stdout:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        message = json.loads(stdout.getvalue()).get("systemMessage", "")
        assert "PACT hook warning (session_init)" not in message, message
        for prefix in ("Project CLAUDE.md failed:", "Migration failed:", "Session info failed:"):
            assert prefix in message, message
        assert legacy.read_text() == _LEGACY_TEXT, "the legacy file was rewritten"

    def test_the_prompt_hook_write_back_absorbs_the_error(
        self, tmp_path, lock, monkeypatch, capsys
    ):
        """bootstrap_marker_writer runs on every prompt. Its team-name
        write-back must never raise: the marker write after it is the hook's
        load-bearing action. The resolver's error lands in the write-back's
        own handler, the context file is still written, and CLAUDE.md is not.

        RED BEFORE THE FIX ON 3.14, which resolved to the legacy file and
        rewrote it.
        """
        import shared.pact_context as pc

        proj, legacy = _preferred_unsearchable(tmp_path, lock)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
        context_writes = []
        monkeypatch.setattr(pc, "get_team_name", lambda: "session-aligned")
        monkeypatch.setattr(pc, "get_pact_context", lambda: {"team_name": "session-stale"})
        monkeypatch.setattr(pc, "get_session_id", lambda: "sess-1")
        monkeypatch.setattr(pc, "get_session_dir", lambda: str(tmp_path / "sess"))
        monkeypatch.setattr(pc, "get_plugin_root", lambda: str(tmp_path / "plugin"))
        monkeypatch.setattr(pc, "get_project_dir", lambda: str(proj))
        monkeypatch.setattr(pc, "write_context", lambda *a, **k: context_writes.append(a))

        bmw._write_back_aligned_team_name()

        assert len(context_writes) == 1, "the load-bearing context write did not run"
        assert legacy.read_text() == _LEGACY_TEXT, "the legacy file was rewritten"
        assert "team-name write-back failed" in capsys.readouterr().err

    def test_the_prompt_hook_reports_an_unreadable_target_it_is_handed(
        self, tmp_path, lock, monkeypatch, capsys
    ):
        """HARNESS ARM: it injects the resolver's answer. The natural route to
        the write-back's own existence probe with an unreadable target is a
        permission change between the resolver's probe and this one.

        That probe REPORTS the target, through the same handler as above,
        rather than reading it as absent. RED BEFORE THE FIX ON 3.14, which
        read it as absent and returned without a word.
        """
        import shared.pact_context as pc

        proj, _legacy = _preferred_unsearchable(tmp_path, lock)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
        monkeypatch.setattr(pc, "get_team_name", lambda: "session-aligned")
        monkeypatch.setattr(pc, "get_pact_context", lambda: {"team_name": "session-stale"})
        monkeypatch.setattr(pc, "get_session_id", lambda: "sess-1")
        monkeypatch.setattr(pc, "get_session_dir", lambda: str(tmp_path / "sess"))
        monkeypatch.setattr(pc, "get_plugin_root", lambda: str(tmp_path / "plugin"))
        monkeypatch.setattr(pc, "get_project_dir", lambda: str(proj))
        monkeypatch.setattr(pc, "write_context", lambda *a, **k: None)
        monkeypatch.setattr(
            bmw, "resolve_project_claude_md_path",
            lambda project_dir: (proj / ".claude" / "CLAUDE.md", "dot_claude"),
        )
        updates = []
        monkeypatch.setattr(bmw, "update_session_info", lambda *a, **k: updates.append(a))

        bmw._write_back_aligned_team_name()

        assert updates == []
        assert "team-name write-back failed" in capsys.readouterr().err


# --- Carrier 2: the global kernel-block strip --------------------------------

_KERNEL_BLOCK = "<!-- PACT_START:v1 -->\nkernel\n<!-- PACT_END -->\nuser content\n"


def _config_root(tmp_path, monkeypatch):
    cfg = tmp_path / "locked" / "cfg"
    cfg.mkdir(parents=True)
    (cfg / "CLAUDE.md").write_text(_KERNEL_BLOCK)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    return cfg


@_NEEDS_NON_ROOT
def test_the_strip_treats_an_unsearchable_config_root_as_absent(
    tmp_path, lock, monkeypatch
):
    """RED BEFORE THE FIX ON 3.9 AND 3.13, which raised out of the strip and
    aborted session start. It cannot strip a file it cannot read, so it does
    nothing, as 3.14 already did."""
    _config_root(tmp_path, monkeypatch)
    lock(tmp_path / "locked")
    assert cmm.strip_orphan_kernel_block() is None


def test_the_strip_still_reaches_a_readable_global_file(tmp_path, monkeypatch):
    """The matched control: the same file, readable, is stripped."""
    cfg = _config_root(tmp_path, monkeypatch)
    assert cmm.strip_orphan_kernel_block() is not None
    assert "PACT_START" not in (cfg / "CLAUDE.md").read_text()


# --- Carrier 3: the project-id walk -------------------------------------------


def _marker_symlink_layout(tmp_path, monkeypatch):
    """gp/.claude is a real directory; gp/work/.claude is a symlink into a
    directory the caller may lock; the cwd is gp/work/sub.

    Strategies 1 and 1.5 are empty (no CLAUDE_PROJECT_DIR; the session record
    refuses test processes) and git is stubbed out, so Strategy 3, the walk,
    decides.
    """
    gp = tmp_path / "gp"
    (gp / ".claude").mkdir(parents=True)
    sub = gp / "work" / "sub"
    sub.mkdir(parents=True)
    target = tmp_path / "locked" / "target"
    target.mkdir(parents=True)
    (gp / "work" / ".claude").symlink_to(target)
    monkeypatch.chdir(sub)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "")
    monkeypatch.setattr(memory_api, "main_repo_root", lambda *a, **k: None)
    return tmp_path / "locked", sub


class TestProjectIdWalk:
    @_NEEDS_NON_ROOT
    def test_an_unreadable_marker_level_stops_the_walk(
        self, tmp_path, lock, monkeypatch
    ):
        """RED BEFORE THE FIX ON 3.14, which skipped the unreadable level and
        filed the project under the GRANDPARENT's name, "gp"."""
        locked, sub = _marker_symlink_layout(tmp_path, monkeypatch)
        lock(locked)
        with pytest.raises(PermissionError):
            PACTMemory._find_project_root(sub)
        assert PACTMemory._detect_project_id_with_source() == (None, "unresolved")

    def test_the_same_marker_readable_names_its_own_level(self, tmp_path, monkeypatch):
        """The matched control: the one difference is the lock."""
        _marker_symlink_layout(tmp_path, monkeypatch)
        assert PACTMemory._detect_project_id_with_source() == ("work", "cwd")

    def test_a_looped_git_common_dir_names_one_root_on_every_interpreter(
        self, tmp_path, monkeypatch
    ):
        """RED BEFORE THE FIX ON 3.9, where Path.resolve() raised RuntimeError
        on the loop and crashed PACTMemory construction. 3.13 and 3.14 leave
        the looping component unresolved; 3.9 now does the same."""
        loop = tmp_path / "loop"
        loop.mkdir()
        looped = _symlink_loop(loop / "a")
        bindir = tmp_path / "bin"
        bindir.mkdir()
        git = bindir / "git"
        git.write_text(f'#!/bin/sh\necho "{looped / ".git"}"\n')
        git.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")

        assert memory_api.main_repo_root() == Path(os.path.realpath(tmp_path)) / "loop" / "a"


# --- Carrier 4: the worktree guard --------------------------------------------


def _worktree_under_marked_parent(tmp_path):
    """outer/.worktrees marks the PARENT; outer/proj holds the worktree."""
    (tmp_path / "outer" / ".worktrees").mkdir(parents=True)
    proj = tmp_path / "outer" / "proj"
    worktree = proj / ".worktrees" / "feat"
    (worktree / "src").mkdir(parents=True)
    (proj / "src").mkdir()
    return proj, worktree


def _suggested(worktree):
    return f"Did you mean: {Path(os.path.realpath(worktree)) / 'src' / 'app.py'}"


class TestWorktreeGuard:
    def test_the_suggestion_names_the_worktree_file(self, tmp_path):
        """The control for the two arms below: everything readable."""
        proj, worktree = _worktree_under_marked_parent(tmp_path)
        message = worktree_guard.check_worktree_boundary(
            str(proj / "src" / "app.py"), str(worktree)
        )
        assert message.startswith("Edit blocked:")
        assert message.endswith(_suggested(worktree)), message

    @_NEEDS_NON_ROOT
    def test_an_unsearchable_project_gets_no_suggestion(self, tmp_path, lock):
        """RED BEFORE THE FIX ON 3.14, whose walk skipped the unreadable
        project and settled on outer/.worktrees, suggesting a path with an
        extra proj/ in it. The deny itself is unchanged."""
        proj, worktree = _worktree_under_marked_parent(tmp_path)
        lock(proj)
        message = worktree_guard.check_worktree_boundary(
            str(proj / "src" / "app.py"), str(worktree)
        )
        assert message.startswith("Edit blocked:")
        assert "Did you mean" not in message, message

    @_NEEDS_NON_ROOT
    def test_an_unsearchable_worktrees_container_gets_no_suggestion(
        self, tmp_path, lock
    ):
        """RED BEFORE THE FIX ON 3.14, which climbed past the unreadable level
        and did suggest. Stopping there drops a suggestion that was right in
        this one layout; a missing suggestion is harmless and a wrong one is
        not, so the walk never climbs past a level it could not read."""
        proj, worktree = _worktree_under_marked_parent(tmp_path)
        lock(proj / ".worktrees")
        message = worktree_guard.check_worktree_boundary(
            str(proj / "src" / "app.py"), str(worktree)
        )
        assert message.startswith("Edit blocked:")
        assert "Did you mean" not in message, message

    def test_a_looped_path_inside_the_worktree_is_allowed(self, tmp_path):
        """RED BEFORE THE FIX ON 3.9, where Path.resolve() raised RuntimeError
        past the "can't resolve, allow" handler and main() denied."""
        _proj, worktree = _worktree_under_marked_parent(tmp_path)
        looped = _symlink_loop(worktree / "loopdir")
        assert worktree_guard.check_worktree_boundary(
            str(looped / "app.py"), str(worktree)
        ) is None

    @pytest.mark.parametrize(
        "where, expected_rc",
        [("looped-inside", 0), ("outside", 2)],
    )
    def test_the_hook_decision(self, where, expected_rc, tmp_path):
        """The decision as the platform sees it: the hook's exit code.

        The looped path ALLOWS (rc 0) on every interpreter; RED BEFORE THE FIX
        ON 3.9, which denied it. The outside path is the matched DENY (rc 2),
        so an allow here can never be a hook that allows everything.
        """
        proj, worktree = _worktree_under_marked_parent(tmp_path)
        if where == "looped-inside":
            file_path = _symlink_loop(worktree / "loopdir") / "app.py"
        else:
            file_path = proj / "src" / "app.py"
        env = {**os.environ, "PACT_WORKTREE_PATH": str(worktree)}
        proc = subprocess.run(
            [sys.executable, str(_WORKTREE_GUARD)],
            input=json.dumps({"tool_input": {"file_path": str(file_path)}}),
            capture_output=True, text=True, timeout=60, env=env,
        )
        assert proc.returncode == expected_rc, (proc.stdout, proc.stderr)
        if expected_rc == 2:
            decision = json.loads(proc.stdout)["hookSpecificOutput"]
            assert decision["permissionDecision"] == "deny"
            assert decision["permissionDecisionReason"].startswith("Edit blocked:")
