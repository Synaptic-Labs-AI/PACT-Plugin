"""
Location: pact-plugin/tests/test_unreadable_location_carriers.py
Summary: Holds every existence probe in the project CLAUDE.md writers, the
         stale-session reader, the global kernel-block strip, the project-id
         walk, project_scope's ancestor walk, the backlog's project lookup and
         the worktree guard to ONE behaviour on every supported interpreter.
         The pact-memory and staleness CLAUDE.md resolvers join the probe
         table here; their own arms are in test_claude_md_resolver_parity.py.
Used by: the full pytest suite, on each CI interpreter.

THE SPLIT THESE ARMS CLOSE. `Path.exists()` and `Path.is_dir()` re-raise a
PermissionError on 3.9-3.13 and return False on 3.14, so a directory the
process cannot search aborted a caller on two CI interpreters and was skipped
as absent on the third. Every arm below that is about a split failed against
the pre-fix code on at least one CI interpreter, and its docstring names
which one. The rest are matched controls and pins, and say so.

WHAT "UNREADABLE" MEANS HERE DEPENDS ON THE CALLER, AND THE ARMS SAY WHICH:
- a project CLAUDE.md writer REPORTS it as a failed status and writes nothing,
  and never falls back to the lower-priority legacy file; the stale-session
  reader, which follows the same precedence, stays silent;
- the global kernel-block strip treats it as ABSENT, since it cannot strip a
  file it cannot read;
- an upward walk STOPS at it, rather than climbing to a parent's marker, and
  its caller says so: the project id is unresolved, the backlog read declines
  loudly naming the level, and the backlog write refuses naming it.

EACCES NEEDS A NON-ROOT PROCESS. Root searches a mode-0 directory, so those
arms SKIP under root with that reason; they never pass without the trigger.
"""

import ast
import errno
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
from shared import backlog
from shared import backlog_store
from shared import claude_md_manager as cmm
from shared import project_scope
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

    def _lock(path, mode=0o000):
        path.chmod(mode)
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


# --- The probe, in every module that defines or imports it --------------------

_PROBE_OWNERS = (
    "shared.claude_md_manager",
    "shared.stale_session",
    "scripts.memory_api",
    "worktree_guard",
    "scripts.working_memory",
    "staleness",
    "shared.project_scope",
    "shared.backlog_store",
    "shared.backlog",
    "shared.session_resume",
    "bootstrap_marker_writer",
)

# The errors every copy counts as "not there". EBADF cannot be produced by a
# stat of a path, so no behavioural row can hold it; this set is asserted
# directly instead.
_ABSENT_ERRNOS = frozenset({errno.ENOENT, errno.ENOTDIR, errno.EBADF, errno.ELOOP})


def _outcome(probe, path):
    try:
        return "absent" if probe(path) is None else "present"
    except OSError as exc:
        return type(exc).__name__


@_NEEDS_NON_ROOT
def test_every_copy_of_the_probe_counts_the_same_errors_as_absent(tmp_path, lock):
    """claude_md_manager defines the probe, and five modules keep a copy:
    worktree_guard imports only the stdlib, stale_session does not import
    claude_md_manager at runtime, backlog_store's read path may not load the
    subprocess that module pulls in on Linux under 3.9, and memory_api and
    working_memory sit outside hooks/. Every other module that probes imports
    the canonical one.
    This PIN holds every one of them to one table, so an edit to one copy
    reddens here instead of drifting silently; the census below keeps the
    table complete.

    The table is 3.9-3.13 pathlib's own rule, made explicit so 3.14 follows it:
    a path that is not there (ENOENT, ENOTDIR, ELOOP, an unencodable path) is
    absent; any other error raises, whether the process may not search the
    path or the name is too long.
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
        "ENAMETOOLONG": (tmp_path / ("x" * 300), "OSError"),
    }
    expected = {name: want for name, (_path, want) in cases.items()}
    for owner in _PROBE_OWNERS:
        probe = getattr(importlib.import_module(owner), "_stat_if_present")
        observed = {name: _outcome(probe, path) for name, (path, _want) in cases.items()}
        assert observed == expected, f"{owner} disagrees with the shared table"


def test_every_copy_reads_exactly_the_same_absent_set():
    """The set each probe consults, read from the probe's own globals, so an
    importer is checked against the definition it actually runs. Dropping
    EBADF, which no row can build, or adding any errno reddens here."""
    for owner in _PROBE_OWNERS:
        probe = getattr(importlib.import_module(owner), "_stat_if_present")
        assert probe.__globals__["_ABSENT_ERRNOS"] == _ABSENT_ERRNOS, owner


def _probe_sites(source):
    """Lines that define the probe or import it by name."""
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if (isinstance(node, ast.FunctionDef) and node.name == "_stat_if_present")
        or (
            isinstance(node, ast.ImportFrom)
            and any(alias.name == "_stat_if_present" for alias in node.names)
        )
    ]


def test_every_module_that_defines_or_imports_the_probe_is_in_the_table():
    """A census of `def _stat_if_present` and of `import ... _stat_if_present`
    across pact-plugin/, tests included. A new copy, or a new module probing
    through the canonical one, reddens here until the table lists it, so
    neither can escape the two arms above."""
    plugin_root = Path(__file__).resolve().parent.parent
    sites = {
        path.relative_to(plugin_root).as_posix()
        for path in plugin_root.rglob("*.py")
        if "__pycache__" not in path.parts
        and _probe_sites(path.read_text(encoding="utf-8"))
    }
    listed = {
        Path(importlib.import_module(owner).__file__).resolve().relative_to(plugin_root).as_posix()
        for owner in _PROBE_OWNERS
    }
    assert "hooks/shared/claude_md_manager.py" in sites, (
        f"the census found no canonical definition, so it is not reading the tree: {sites}"
    )
    assert sites <= listed, f"probe sites the table does not list: {sorted(sites - listed)}"


def test_the_census_finds_a_definition_and_an_import():
    """The control for the census: its parser finds both kinds of site."""
    assert _probe_sites("x = 1\n\ndef _stat_if_present(path):\n    return None\n") == [3]
    assert _probe_sites("from .claude_md_manager import (\n    _stat_if_present,\n)\n") == [1]


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
    "skills/pact-memory/scripts/working_memory.py": {
        "_find_existing_claude_md",
        "_resolve_display_claude_md_with_base",
        "sync_to_claude_md",
        "sync_retrieved_to_claude_md",
    },
    "hooks/staleness.py": {
        "_find_existing_claude_md",
        "_resolve_project_claude_md_with_base",
    },
    "hooks/shared/project_scope.py": {"_nearest_existing_directory"},
    "hooks/shared/backlog_store.py": {"_enclosing_checkout"},
    "hooks/shared/backlog.py": {"_umbrella_refusal"},
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


class TestNearestExistingDirectory:
    """project_scope's ancestor walk for a declaration that no longer exists.
    It stops at a level it cannot examine, like the project-id walk."""

    @_NEEDS_NON_ROOT
    def test_an_unreadable_level_stops_the_walk(self, tmp_path, lock):
        """RED BEFORE THE FIX ON 3.14, which skipped the unreadable levels and
        returned the locked ancestor. 3.9 and 3.13 already stopped."""
        locked = tmp_path / "locked"
        (locked / "project").mkdir(parents=True)
        lock(locked)
        assert project_scope._nearest_existing_directory(locked / "project" / "gone") is None

    def test_a_removed_directory_maps_to_its_nearest_existing_ancestor(self, tmp_path):
        """The matched control: the same walk with nothing locked."""
        (tmp_path / "project").mkdir()
        gone = tmp_path / "project" / "gone" / "deeper"
        assert project_scope._nearest_existing_directory(gone) == tmp_path / "project"

    def test_a_file_is_passed_over_for_the_directory_above_it(self, tmp_path):
        """A PIN on the directory test: only a directory answers, and a
        regular file on the path is climbed past."""
        (tmp_path / "project").mkdir()
        a_file = tmp_path / "project" / "notes.txt"
        a_file.write_text("x")
        assert project_scope._nearest_existing_directory(a_file / "child") == tmp_path / "project"


# --- The backlog's project lookup ---------------------------------------------


def _backlog_file(store, root, title):
    """The smallest conforming backlog file, recording `root` as its checkout."""
    store.mkdir(exist_ok=True)
    (store / f"{root.name}.json").write_text(json.dumps({
        "version": 1,
        "project": root.name,
        "project_path": str(root),
        "roots": [str(root)],
        "updated": "2026-09-01T00:00:00Z",
        "items": [{
            "id": "a1b2", "title": title, "status": "planned", "rank": 1,
            "blocked_by": [], "batch_with": [], "ref": None, "plan": None,
            "memory": [], "note": "", "added": "2026-09-01", "touched": "2026-09-01",
        }],
    }))


def _project_below_an_outer_checkout(tmp_path):
    """outer/.git is a checkout; the project is outer/inner/sub, and the caller
    may lock `inner`, the level between them."""
    outer = tmp_path / "outer"
    (outer / ".git").mkdir(parents=True)
    sub = outer / "inner" / "sub"
    sub.mkdir(parents=True)
    return outer, sub


# Two ways to make the project's level unexaminable: its parent cannot be
# searched, so even a stat of the project fails; or the project itself cannot
# be searched, so a stat of it works and the probe of its `.git` fails.
_UNEXAMINABLE_LAYOUTS = ["locked-parent", "unsearchable-project"]


def _make_unexaminable(layout, sub, lock):
    if layout == "locked-parent":
        lock(sub.parent)
    else:
        lock(sub, 0o600)


class TestBacklogProjectLookup:
    """The enclosing-checkout walk stops at a level it cannot examine. The read
    path then declines the enclosing rung and says so; the write path refuses
    and names the level. Neither ever climbs to the outer checkout."""

    @_NEEDS_NON_ROOT
    @pytest.mark.parametrize("layout", _UNEXAMINABLE_LAYOUTS)
    def test_the_read_path_declines_loudly_rather_than_claim_the_outer_backlog(
        self, layout, tmp_path, lock
    ):
        """RED BEFORE THE FIX: 3.14 climbed past the unreadable level and
        rendered the OUTER checkout's backlog as this project's; 3.9 and 3.13
        raised into session_block's catch-all instead of reporting a
        resolution failure."""
        outer, sub = _project_below_an_outer_checkout(tmp_path)
        store = tmp_path / "store"
        _backlog_file(store, outer, "OUTER ITEM")
        _make_unexaminable(layout, sub, lock)

        notice = backlog_store.session_block(str(sub), backlog_dir=store)

        assert "OUTER ITEM" not in notice.context
        assert "resolution failure" in notice.alert, notice.alert
        assert f"{sub} could not be examined" in notice.alert, notice.alert

    def test_the_same_layout_readable_reaches_the_outer_backlog(self, tmp_path):
        """The matched control: nothing locked, the enclosing rung matches."""
        outer, sub = _project_below_an_outer_checkout(tmp_path)
        store = tmp_path / "store"
        _backlog_file(store, outer, "OUTER ITEM")

        notice = backlog_store.session_block(str(sub), backlog_dir=store)

        assert "OUTER ITEM" in notice.context, notice

    @_NEEDS_NON_ROOT
    def test_an_exact_root_still_matches_when_the_walk_cannot_tell(
        self, tmp_path, lock
    ):
        """Exact membership needs no walk, so the decline leaves it alone.
        RED BEFORE THE FIX ON 3.9 AND 3.13, which raised before comparing."""
        outer, sub = _project_below_an_outer_checkout(tmp_path)
        store = tmp_path / "store"
        _backlog_file(store, sub, "OWN ITEM")
        lock(outer / "inner")

        notice = backlog_store.session_block(str(sub), backlog_dir=store)

        assert "OWN ITEM" in notice.context, notice

    @_NEEDS_NON_ROOT
    @pytest.mark.parametrize("layout", _UNEXAMINABLE_LAYOUTS)
    def test_the_write_path_refuses_and_names_the_level(
        self, layout, tmp_path, lock, monkeypatch
    ):
        """RED BEFORE THE FIX: 3.9 and 3.13 raised PermissionError out of
        project_root; 3.14 refused with the wrong reason ('does not name an
        existing directory'). The unsearchable-project layout reaches the
        walk's own probe, and its refusal named `<dir>/.git` where the read
        path names `<dir>`; both now name the directory."""
        _outer, sub = _project_below_an_outer_checkout(tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(sub))
        _make_unexaminable(layout, sub, lock)

        with pytest.raises(backlog.BacklogWriteError) as refused:
            backlog.project_root()

        assert f"{sub} could not be examined" in str(refused.value), refused.value
        assert ".git could not be examined" not in str(refused.value), refused.value

    def test_a_looped_worktree_is_recorded_on_every_interpreter(
        self, tmp_path, monkeypatch
    ):
        """RED BEFORE THE FIX ON 3.9, where Path.resolve() raised RuntimeError
        on the loop and the write failed."""
        main = tmp_path / "main"
        main.mkdir()
        looped = _symlink_loop(tmp_path / "wt")
        monkeypatch.setattr(backlog, "project_root", lambda: main)
        monkeypatch.setattr(
            backlog, "_run_capture",
            lambda command: f"worktree {main}\nHEAD 1\n\nworktree {looped}\nHEAD 2\n",
        )

        assert backlog.checkout_roots() == [
            os.path.realpath(main), os.path.realpath(looped)
        ]


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
        """A looped path inside the worktree is inside it. Once red on 3.9,
        where Path.resolve() raised RuntimeError on the loop."""
        _proj, worktree = _worktree_under_marked_parent(tmp_path)
        looped = _symlink_loop(worktree / "loopdir")
        assert worktree_guard.check_worktree_boundary(
            str(looped / "app.py"), str(worktree)
        ) is None

    @pytest.mark.parametrize(
        "where, expected_rc",
        [("looped-inside", 0), ("looped-outside", 2), ("outside", 2)],
    )
    def test_the_hook_decision(self, where, expected_rc, tmp_path):
        """The decision as the platform sees it: the hook's exit code.

        A looped path is decided by where it sits, like any other path: inside
        the worktree it ALLOWS (rc 0), outside it DENIES (rc 2), on every
        interpreter. The looped-inside row was red on 3.9 when its resolve()
        raised past the "can't resolve, allow" handler into main()'s deny. The
        looped-outside row was red on 3.9 when that handler caught the raise
        and allowed. The plain outside path is the matched DENY, so an allow
        here can never be a hook that allows everything.
        """
        proj, worktree = _worktree_under_marked_parent(tmp_path)
        if where == "looped-inside":
            file_path = _symlink_loop(worktree / "loopdir") / "app.py"
        elif where == "looped-outside":
            file_path = _symlink_loop(proj / "loopdir") / "app.py"
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
