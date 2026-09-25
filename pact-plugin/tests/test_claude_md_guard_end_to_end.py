"""End-to-end arms for the CLAUDE.md session tripwire: a REAL nested pytest
process, a REAL write, the REAL hook firing.

Location: pact-plugin/tests/test_claude_md_guard_end_to_end.py
Sibling of: pact-plugin/tests/test_claude_md_guard.py

WHY A SEPARATE FILE. Every arm in the sibling imports `claude_md_guard`
directly and asserts against handmade dicts or the live tree -- none of them
crosses a process boundary. That is the whole gap these arms close: the
in-process half of the protection is a `monkeypatch` fixture, `monkeypatch`
does not cross a process boundary, and so the child-process route can only be
OBSERVED by the before/after comparison. Observing it for real costs a
subprocess per arm, which is why it sits here rather than beside a table of
pure-function cases.

WHAT CONFINES THE CHILD. The guard fixes its watched set at configure from the
child's own inputs: CLAUDE_PROJECT_DIR, the working directory and its git
roots, and the config roots under CLAUDE_CONFIG_DIR, $HOME and the password
database's home. `_run_nested` points the first three, and CLAUDE_CONFIG_DIR,
into tmp_path, so every project location the child watches is a temp path by
construction. The suite's per-test scrubs take no part in it.

ONE REAL PATH STAYS IN THE CHILD'S SET, AND NOTHING HERE CAN REMOVE IT. The
password database's home comes from the password database, not the
environment, so `<that home>/.claude/CLAUDE.md` -- the operator's global
CLAUDE.md on a developer machine -- is watched by every child, and so is
`$HOME/.claude/CLAUDE.md`. Neither is written: the inner test writes only the
path an arm names. Watching them adds no exposure, because the OUTER run
watches the same files over a window that contains every child's; a write to
either during the suite reddens the outer run regardless, and a child adds a
second report on whichever arm was running.

`_assert_confined` therefore pins the set EXACTLY: the temp paths an arm built,
plus the two home config files computed here independently of the guard. A
real project CLAUDE.md entering the set fails it and names the set -- including
the case where tmp_path sits inside a git repository.

WHAT THESE ARMS DO NOT ASSERT, AND WHY. They do not check that the real
CLAUDE.md is byte-unchanged across the child run. The guard itself is that
assertion, running over the whole session; a second copy of it here would add
nothing but a second way to redden when the operator's own session
legitimately rewrites that file mid-suite. The confinement assertion is the
structural substitute and it is the stronger claim -- it constrains what the
child CAN reach, rather than observing what it happened not to touch.

THE CLEAN ARM'S LIVENESS CHECK IS NOT DECORATION. "exit 0 and no report" is
satisfied identically by a guard that ran and found nothing and by one that
never loaded. The clean arm requires the one-line summary the guard prints on a
clean run, and the stash check stays as a second witness.
"""

import json
import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

_SUMMARY_PREFIX = "[PACT CLAUDE.md guard] clean:"

# The child's test module. It writes its observations to a FILE rather than
# printing them: pytest's capture swallows output from some phases, and a
# marker that can be swallowed cannot distinguish "did not run" from "ran and
# was captured".
#
# NOTE FOR ANY EDITOR: this string, and the observer's below, are scanned by
# the module-search-path pin, which reads string constants under tests/ as
# well as real code. The child's import roots are supplied through PYTHONPATH
# in `_run_nested` for exactly that reason. Do not add a path mutation here.
_INNER_TEST = '''
import json
import os
from pathlib import Path

import claude_md_guard as guard


def test_inner(pytestconfig):
    before = pytestconfig.stash.get(guard._BEFORE, None)
    Path(os.environ["GUARD_E2E_DUMP"]).write_text(
        json.dumps(
            {
                "stash_is_none": before is None,
                "watched": sorted(before) if before else [],
            }
        ),
        encoding="utf-8",
    )
    target = os.environ.get("GUARD_E2E_MODIFY")
    action = os.environ.get("GUARD_E2E_ACTION")
    if action == "atomic":
        from shared.claude_md_manager import _atomic_write_text

        _atomic_write_text(
            Path(target), "changed by a writer", Path(os.environ["GUARD_E2E_ROOT"])
        )
    elif action == "strip":
        from shared.claude_md_manager import strip_orphan_kernel_block

        strip_orphan_kernel_block()
    elif target:
        Path(target).write_text("modified by the inner test", encoding="utf-8")
'''

# A plugin whose unconfigure hook leaves a file behind. Registered BEFORE the
# guard, so without the guard's trylast the guard's hook runs first and its
# raise skips this one.
_OBSERVER = '''
import os
from pathlib import Path


def pytest_unconfigure(config):
    Path(os.environ["GUARD_E2E_OBSERVER"]).write_text("ran", encoding="utf-8")
'''

# The root conftest's registration route, reproduced in the child: the guard's
# hooks re-exported by name from a conftest rather than loaded with `-p`.
_REEXPORTING_CONFTEST = '''
from claude_md_guard import (  # noqa: F401 -- hook-registration re-export
    pytest_configure,
    pytest_unconfigure,
)
'''

_NEEDS_GIT = pytest.mark.skipif(
    shutil.which("git") is None, reason="this arm builds a real git repository"
)


def _run_nested(
    tmp_path, *, modify_target=None, argv_extra=(), cwd=None, project_dir=None,
    via_conftest=False, action=None, setup=None, home=None, config_dir=True,
):
    """Launch a real nested pytest under `tmp_path`; return (completed, dump).

    `modify_target`, when given, is the one path the inner test writes (and
    creates if absent). The child's CLAUDE_PROJECT_DIR is `project_dir` or
    <tmp>/proj, its working directory is `cwd` or <tmp>/proj, and its
    CLAUDE_CONFIG_DIR is <tmp>/cfg. With `via_conftest` the guard is
    registered the way the root conftest registers it, by re-export, instead
    of with `-p`. `action` swaps the plain write for a real PACT writer:
    "atomic" replaces `modify_target` the way `_atomic_write_text` does, and
    "strip" runs the global kernel-block strip. `setup(tmp_path)` runs after
    the default layout is built; `home` sets the child's HOME, and
    `config_dir=False` leaves its CLAUDE_CONFIG_DIR unset.
    """
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True, exist_ok=True)
    (proj / ".claude" / "CLAUDE.md").write_text("original", encoding="utf-8")
    (proj / "test_inner.py").write_text(_INNER_TEST, encoding="utf-8")
    (proj / "observer_plugin.py").write_text(_OBSERVER, encoding="utf-8")
    if via_conftest:
        (proj / "conftest.py").write_text(_REEXPORTING_CONFTEST, encoding="utf-8")
    load_guard = () if via_conftest else ("-p", "claude_md_guard")
    (tmp_path / "cfg").mkdir(exist_ok=True)
    if setup is not None:
        setup(tmp_path)
    dump = tmp_path / "dump.json"

    roots = [
        str(proj),
        str(PLUGIN_ROOT / "tests"),
        str(PLUGIN_ROOT / "hooks"),
        str(PLUGIN_ROOT / "skills" / "pact-memory" / "scripts"),
    ]
    env = dict(os.environ)
    inherited = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(roots + ([inherited] if inherited else []))
    env["CLAUDE_PROJECT_DIR"] = str(project_dir or proj)
    env["CLAUDE_CONFIG_DIR"] = str(tmp_path / "cfg")
    if not config_dir:
        env.pop("CLAUDE_CONFIG_DIR")
    if home is not None:
        env["HOME"] = str(home)
    env["GUARD_E2E_ROOT"] = str(proj)
    env.pop("GUARD_E2E_ACTION", None)
    if action is not None:
        env["GUARD_E2E_ACTION"] = action
    env["GUARD_E2E_DUMP"] = str(dump)
    env["GUARD_E2E_OBSERVER"] = str(tmp_path / "observer.ran")
    env.pop("GUARD_E2E_MODIFY", None)
    if modify_target is not None:
        env["GUARD_E2E_MODIFY"] = str(modify_target)

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         *argv_extra, *load_guard, str(proj / "test_inner.py")],
        cwd=str(cwd or proj),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed, json.loads(dump.read_text(encoding="utf-8"))


def _key(path):
    """A path as the guard keys it: absolute, as written, not resolved."""
    return str(Path(path).absolute())


def _home_files(home=None):
    """The two home config files every child watches, computed here, not by
    the guard: $HOME's (the child's, when an arm sets one) and the password
    database home's `.claude/CLAUDE.md`, each as the absolute path written --
    the guard keys a path as written, not as resolved."""
    homes = [home or os.environ.get("HOME"), pwd.getpwuid(os.getuid()).pw_dir]
    return {_key(Path(h) / ".claude" / "CLAUDE.md") for h in homes if h}


def _default_tmp_set(tmp_path, config_dir=True):
    paths = {
        _key(tmp_path / "proj" / ".claude" / "CLAUDE.md"),
        _key(tmp_path / "proj" / "CLAUDE.md"),
    }
    if config_dir:
        paths.add(_key(tmp_path / "cfg" / "CLAUDE.md"))
    return paths


def _assert_confined(dump, tmp_expected, home=None):
    """The hook ran, and the child watched exactly the temp paths the arm
    built plus the two home config files.

    * the stash check catches a guard that never loaded;
    * the set equality catches a lost confinement -- a real project CLAUDE.md
      joining the set, including through a tmp_path that sits inside a git
      repository.
    """
    assert dump["stash_is_none"] is False, (
        "the guard's pytest_configure did not run in the child: the arm is "
        "measuring nothing"
    )
    expected = set(tmp_expected) | _home_files(home)
    assert set(dump["watched"]) == expected, (
        "the child's watched set is not the temp paths plus the home config "
        f"files: {sorted(dump['watched'])}"
    )


def test_a_child_process_write_is_caught_end_to_end(tmp_path):
    """A write inside a nested pytest run is reported and exits non-zero."""
    target = tmp_path / "proj" / ".claude" / "CLAUDE.md"
    completed, dump = _run_nested(tmp_path, modify_target=target)
    _assert_confined(dump, _default_tmp_set(tmp_path))

    assert completed.returncode != 0, (
        "a nested run that modified a watched CLAUDE.md exited zero; stderr:\n"
        + completed.stderr
    )
    assert "PACT CLAUDE.md GUARD" in completed.stderr
    assert "VIOLATION" in completed.stderr
    assert "MODIFIED" in completed.stderr
    assert _key(target) in completed.stderr


def test_the_violating_runs_summary_line_still_reads_passed(tmp_path):
    """The exit code and pytest's summary DISAGREE on a violating run.

    Pinned end to end rather than left in prose, because it is the one thing
    a CI step is most likely to get wrong: the guard raises from a teardown
    hook, after the summary has been composed, so the run reports its tests
    as passed AND exits non-zero. Anything gating on this guard must read the
    exit code.
    """
    target = tmp_path / "proj" / ".claude" / "CLAUDE.md"
    completed, dump = _run_nested(tmp_path, modify_target=target)
    _assert_confined(dump, _default_tmp_set(tmp_path))

    assert completed.returncode != 0
    assert "passed" in completed.stdout
    assert "failed" not in completed.stdout


def test_a_clean_nested_run_prints_its_watched_set_and_exits_zero(tmp_path):
    """The other direction: the guard loaded, watched, and printed the one
    line a clean run prints, naming every watched path."""
    completed, dump = _run_nested(tmp_path)
    tmp_set = _default_tmp_set(tmp_path)
    _assert_confined(dump, tmp_set)

    assert completed.returncode == 0, (
        "a nested run that changed nothing exited non-zero; stderr:\n"
        + completed.stderr
    )
    summary = [l for l in completed.stderr.splitlines() if l.startswith(_SUMMARY_PREFIX)]
    assert len(summary) == 1, completed.stderr
    for path in tmp_set:
        assert path in summary[0]
    assert "PACT CLAUDE.md GUARD" not in completed.stderr
    assert "VIOLATION" not in completed.stderr and "REPORT" not in completed.stderr


def test_a_config_root_the_child_creates_is_caught(tmp_path):
    """A CLAUDE.md created under the child's CLAUDE_CONFIG_DIR, absent before
    the run, reports as CREATED: the config roots are watched."""
    target = tmp_path / "cfg" / "CLAUDE.md"
    completed, dump = _run_nested(tmp_path, modify_target=target)
    _assert_confined(dump, _default_tmp_set(tmp_path))

    assert completed.returncode != 0, completed.stderr
    assert "CREATED" in completed.stderr
    assert _key(target) in completed.stderr


@_NEEDS_GIT
def test_a_main_checkout_write_under_an_umbrella_declaration_is_caught(tmp_path):
    """CLAUDE_PROJECT_DIR names an umbrella directory above the repository,
    holding a CLAUDE.md of its own, and the child runs inside a linked
    worktree. A write to the main checkout's CLAUDE.md -- which a writer whose
    declaration was deleted reaches through git -- is reported: the git roots
    are watched whatever the declaration says. The umbrella's own file is what
    made a guard that asked the resolvers watch the umbrella and not the main
    checkout, and exit zero on this write."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)

    def git(*args, cwd):
        subprocess.run(
            ["git", "-c", "user.email=t@e", "-c", "user.name=T",
             "-c", "init.defaultBranch=main", *args],
            cwd=str(cwd), env=env, capture_output=True, check=True, timeout=30,
        )

    umbrella = tmp_path / "umbrella"
    main = umbrella / "main"
    main.mkdir(parents=True)
    git("init", cwd=main)
    (main / "README").write_text("seed")
    git("add", "README", cwd=main)
    git("commit", "-m", "seed", cwd=main)
    git("worktree", "add", ".worktrees/wt", cwd=main)
    wt = main / ".worktrees" / "wt"
    (umbrella / "CLAUDE.md").write_text("umbrella\n")
    main_md = main / "CLAUDE.md"
    main_md.write_text("main\n")

    completed, dump = _run_nested(
        tmp_path, modify_target=main_md, cwd=wt, project_dir=umbrella
    )
    # git names the worktree and the main checkout by their real paths.
    tmp_set = {
        os.path.join(os.path.realpath(d), shape)
        for d in (umbrella, wt, main)
        for shape in (os.path.join(".claude", "CLAUDE.md"), "CLAUDE.md")
    } | {_key(tmp_path / "cfg" / "CLAUDE.md")}
    _assert_confined(dump, tmp_set)

    assert completed.returncode != 0, completed.stderr
    assert "MODIFIED" in completed.stderr
    assert os.path.realpath(main_md) in completed.stderr


def test_a_plugin_registered_before_the_guard_still_runs_its_unconfigure(tmp_path):
    """The guard's unconfigure hook is trylast, so its raise on a violation
    skips no other plugin's unconfigure hook. The guard is registered the way
    the root conftest registers it, by re-export, and the observer with `-p`,
    which registers it FIRST: without trylast the guard's hook would run
    before it and its raise would skip it."""
    target = tmp_path / "proj" / ".claude" / "CLAUDE.md"
    completed, dump = _run_nested(
        tmp_path, modify_target=target, argv_extra=("-p", "observer_plugin"),
        via_conftest=True,
    )
    _assert_confined(dump, _default_tmp_set(tmp_path))

    assert completed.returncode != 0, completed.stderr
    assert (tmp_path / "observer.ran").exists(), (
        "the observer's unconfigure hook did not run after the guard raised; "
        "stderr:\n" + completed.stderr
    )


def test_a_skills_only_run_reads_every_input(tmp_path):
    """A run whose only path argument lies outside tests/, so tests/conftest.py
    is not an initial conftest. The root conftest's hooks/ entry is then the
    only thing that makes the guard's `shared` imports resolve at configure.
    Read-only: a collect-only run in the real tree."""
    candidates = sorted(PLUGIN_ROOT.glob("skills/*/test_*.py"))
    if not candidates:
        pytest.skip("no skills-adjacent test file exists to collect")
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", str(candidates[0])],
        cwd=str(PLUGIN_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert _SUMMARY_PREFIX in completed.stderr, completed.stderr
    assert "INPUT UNAVAILABLE" not in completed.stderr


def _link_project_claude_md(tmp_path):
    """Make proj/.claude/CLAUDE.md a symlink to a file outside the project."""
    real = tmp_path / "real" / "CLAUDE.md"
    real.parent.mkdir()
    real.write_text("original\n", encoding="utf-8")
    link = tmp_path / "proj" / ".claude" / "CLAUDE.md"
    link.unlink()
    link.symlink_to(real)


def test_a_writer_replacing_a_symlinked_project_claude_md_is_caught(tmp_path):
    """The project CLAUDE.md is a symlink, and a child replaces it the way
    every PACT writer does, by renaming a new file over the path. The link
    becomes a regular file and the file it pointed at is untouched, so a guard
    keyed by the resolved target saw nothing; this one keys the path."""
    link = tmp_path / "proj" / ".claude" / "CLAUDE.md"
    completed, dump = _run_nested(
        tmp_path, modify_target=link, action="atomic", setup=_link_project_claude_md
    )
    _assert_confined(dump, _default_tmp_set(tmp_path))

    assert not link.is_symlink(), "the writer did not replace the link"
    assert (tmp_path / "real" / "CLAUDE.md").read_text() == "original\n"
    assert completed.returncode != 0, completed.stderr
    assert "MODIFIED" in completed.stderr
    assert _key(link) in completed.stderr


def test_a_writer_replacing_a_symlinked_global_claude_md_is_caught(tmp_path):
    """The same replacement at the global file: $HOME/.claude/CLAUDE.md is a
    dotfiles symlink, and the real kernel-block strip rewrites it."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    dotfile = tmp_path / "dotfiles" / "CLAUDE.md"
    dotfile.parent.mkdir()
    kernel = "<!-- PACT_START:v1 -->\nkernel\n<!-- PACT_END -->\nuser content\n"
    dotfile.write_text(kernel, encoding="utf-8")
    link = home / ".claude" / "CLAUDE.md"
    link.symlink_to(dotfile)

    completed, dump = _run_nested(
        tmp_path, action="strip", home=home, config_dir=False
    )
    _assert_confined(dump, _default_tmp_set(tmp_path, config_dir=False), home=home)

    assert not link.is_symlink(), "the strip did not replace the link"
    assert dotfile.read_text() == kernel
    assert completed.returncode != 0, completed.stderr
    assert "MODIFIED" in completed.stderr
    assert _key(link) in completed.stderr


def test_a_symlinked_claude_md_left_alone_is_clean(tmp_path):
    """The control: the same symlinked project file, not written. The run is
    clean and its summary names the link as written, and as present."""
    link = tmp_path / "proj" / ".claude" / "CLAUDE.md"
    completed, dump = _run_nested(tmp_path, setup=_link_project_claude_md)
    _assert_confined(dump, _default_tmp_set(tmp_path))

    assert completed.returncode == 0, completed.stderr
    summary = [l for l in completed.stderr.splitlines() if l.startswith(_SUMMARY_PREFIX)]
    assert len(summary) == 1, completed.stderr
    assert f"{_key(link)} (present)" in summary[0]
