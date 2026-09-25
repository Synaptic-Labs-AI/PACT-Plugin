"""End-to-end arms for the CLAUDE.md session tripwire: a REAL nested pytest
process, a REAL write, the REAL hook firing.

Location: pact-plugin/tests/test_claude_md_guard_end_to_end.py
Sibling of: pact-plugin/tests/test_claude_md_guard.py

WHY A SEPARATE FILE. Every arm in the sibling imports `claude_md_guard`
directly and asserts against handmade dicts or the live tree -- none of them
crosses a process boundary. That is the whole gap these two arms close: the
in-process half of the protection is a `monkeypatch` fixture, `monkeypatch`
does not cross a process boundary, and so the child-process route can only be
OBSERVED by the before/after comparison. Observing it for real costs a
subprocess per arm, which is why it sits here rather than beside a table of
pure-function cases.

THE HAZARD. The child's watched set is built by the same three resolver
evaluations as any other run, and pointing `CLAUDE_PROJECT_DIR` at a temp
directory IS NOT ENOUGH ON ITS OWN. The display resolver's branches are
probe-and-continue: a branch that finds no CLAUDE.md under its base FALLS
THROUGH to the next one. With an empty temp directory the env branch misses,
resolution reaches the recorded-session anchor, and THE OPERATOR'S REAL
CLAUDE.md ENTERS THE CHILD'S WATCHED SET -- measured from a parent process
carrying a live session id. An arm in that shape never writes the real file,
but it holds it under observation for the length of the child run, where an
unrelated concurrent write reddens the suite for a reason that has nothing
to do with this test.

`_run_nested` therefore CREATES `<tmp>/.claude/CLAUDE.md` BEFORE launching
the child, so the env branch hits and never falls through.

WHAT ACTUALLY CONFINES THE CHILD TODAY IS NOT THAT FILE, AND THE DIFFERENCE
IS THE WHOLE REASON THE ASSERTION BELOW IS WORTH READING. Three independent
conditions each suffice on their own, and only the first lives in this file:

  1. the pre-created temp CLAUDE.md, which makes the env branch hit;
  2. tests/conftest.py's autouse `_scrub_session_id_from_test_env`, which
     deletes CLAUDE_CODE_SESSION_ID from `os.environ` -- and `_run_nested`
     copies `os.environ` INSIDE the test body, so the child inherits the
     deletion. With no session id the recorded-session branch cannot find a
     record, so it can never name the real file;
  3. PYTEST_CURRENT_TEST, inherited the same way, which the session-record
     reader refuses outright.

MEASURED, all four cells -- the fourth being the shipped one, session id
scrubbed and the file pre-created, which is what every run here exercises.
Delete the pre-creation alone: the set is STILL
exactly one temp path, because 2 and 3 still hold, and every arm here stays
green. Put the session id back in the child AND delete the pre-creation:
the set becomes two paths, the real CLAUDE.md first, and the confinement
assertion fires. Put the session id back with the pre-creation KEPT: one
temp path again -- which is what makes (1) worth keeping rather than tidying
away. It is the leg that still holds when the two the suite owns are gone,
and that fixture's own docstring says both of those fail open if their
signal goes missing.

SO READ THE ASSERTION FOR WHAT IT IS: a counter-test for THE HAZARD -- the
real file entering the child's watched set -- and NOT for the pre-creation.
A green run here is not evidence that the pre-creation is doing the work.
The counter-run that does fire it (session id restored, nothing pre-created)
is deliberately NOT shipped: an arm that must hold the operator's live
document in its watched set to make its point is the very shape this file
exists to avoid.

WHAT THESE ARMS DO NOT ASSERT, AND WHY. They do not check that the real
CLAUDE.md is byte-unchanged across the child run. The guard itself is that
assertion, running over the whole session; a second copy of it here would add
nothing but a second way to redden when the operator's own session
legitimately rewrites that file mid-suite. The confinement assertion is the
structural substitute and it is the stronger claim -- it constrains what the
child CAN reach, rather than observing what it happened not to touch.

THE CLEAN ARM'S LIVENESS CHECK IS NOT DECORATION. A clean run is SILENT by
design, so "exit 0 and nothing on stderr" is satisfied identically by a guard
that ran and found nothing and by a guard that never loaded at all. The
`stash_is_none is False` assertion is what separates those two, and without
it the clean arm would be a permanent green that measures nothing. MEASURED:
drop the child's `-p claude_md_guard` and delete this file's confinement
assertions, and the clean arm PASSES while the two violating arms fail --
the guard absent, the criterion satisfied, nothing measured.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# The child's test module. It writes its observations to a FILE rather than
# printing them: pytest's capture swallows output from some phases, and a
# marker that can be swallowed cannot distinguish "did not run" from "ran and
# was captured".
#
# NOTE FOR ANY EDITOR: this string is scanned by the module-search-path pin,
# which reads string constants under tests/ as well as real code. The child's
# three import roots are supplied through PYTHONPATH in `_run_nested` for
# exactly that reason. Do not add a path mutation here.
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
                "rootpath": str(Path(pytestconfig.rootpath).resolve()),
                "stash_is_none": before is None,
                "watched": sorted(before) if before else [],
            }
        ),
        encoding="utf-8",
    )
    target = os.environ.get("GUARD_E2E_MODIFY")
    if target:
        Path(target).write_text("modified by the inner test", encoding="utf-8")
'''


def _run_nested(tmp_path, *, modify):
    """Launch a real nested pytest under `tmp_path`; return (completed, dump).

    Every path the child can reach is under `tmp_path`. `modify` decides only
    whether the inner test writes the watched file, so the two arms differ in
    one variable and nothing else.
    """
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)

    # Pre-created so the env branch hits instead of falling through to the
    # anchors that name the real file. Redundant with the suite's session-id
    # scrub today and kept as the leg that survives its removal -- see the
    # module docstring's four measured cells.
    watched = proj / ".claude" / "CLAUDE.md"
    watched.write_text("original", encoding="utf-8")

    (proj / "test_inner.py").write_text(_INNER_TEST, encoding="utf-8")
    dump = tmp_path / "dump.json"

    roots = [
        str(PLUGIN_ROOT / "tests"),
        str(PLUGIN_ROOT / "hooks"),
        str(PLUGIN_ROOT / "skills" / "pact-memory" / "scripts"),
    ]
    env = dict(os.environ)
    inherited = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(roots + ([inherited] if inherited else []))
    env["CLAUDE_PROJECT_DIR"] = str(proj)
    env["GUARD_E2E_DUMP"] = str(dump)
    env.pop("GUARD_E2E_MODIFY", None)
    if modify:
        env["GUARD_E2E_MODIFY"] = str(watched)

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "claude_md_guard", "test_inner.py"],
        cwd=str(proj),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed, json.loads(dump.read_text(encoding="utf-8"))


def _assert_confined(dump, tmp_path):
    """The child watched exactly one path, the temp one, and the hook ran.

    Three halves, each failing for its own reason:

    * the stash check catches a guard that never loaded -- which no exit code
      or stderr assertion can see on a clean run;
    * the rootdir check pins the arm's own precondition. Two of the guard's
      three resolver evaluations key on the child's rootdir, and pytest does
      not simply take the cwd: with no ini file it searches UPWARD for
      pytest.ini / pyproject.toml / tox.ini / setup.cfg and then setup.py.
      An ancestor of tmp_path carrying one moves the rootdir outside tmp --
      and because such an ancestor holds no CLAUDE.md either, the watched set
      could still read as one path. Confinement would then be an accident of
      the machine's temp layout rather than something this arm constructed,
      and nothing below would say so;
    * the set equality catches a lost confinement -- the real CLAUDE.md
      joining the set.
    """
    expected = str((tmp_path / "proj" / ".claude" / "CLAUDE.md").resolve())
    assert dump["stash_is_none"] is False, (
        "the guard's pytest_configure did not run in the child: the arm is "
        "measuring nothing"
    )
    assert dump["rootpath"] == str((tmp_path / "proj").resolve()), (
        "the child's rootdir resolved outside tmp_path, so the resolver "
        "evaluations keyed on it were never confined by construction: "
        f"{dump['rootpath']}"
    )
    assert dump["watched"] == [expected], (
        "the child's watched set is not confined to tmp_path. If the real "
        "CLAUDE.md is in it, every one of the three confining conditions in "
        f"the module docstring has been lost, not just one: {dump['watched']}"
    )


def test_a_child_process_write_is_caught_end_to_end(tmp_path):
    """A write inside a nested pytest run is reported and exits non-zero."""
    completed, dump = _run_nested(tmp_path, modify=True)
    _assert_confined(dump, tmp_path)

    assert completed.returncode != 0, (
        "a nested run that modified a watched CLAUDE.md exited zero; stderr:\n"
        + completed.stderr
    )
    assert "PACT CLAUDE.md GUARD" in completed.stderr
    assert "VIOLATION" in completed.stderr
    assert "MODIFIED" in completed.stderr
    assert dump["watched"][0] in completed.stderr


def test_the_violating_runs_summary_line_still_reads_passed(tmp_path):
    """The exit code and pytest's summary DISAGREE on a violating run.

    Pinned end to end rather than left in prose, because it is the one thing
    a CI step is most likely to get wrong: the guard raises from a teardown
    hook, after the summary has been composed, so the run reports its tests
    as passed AND exits non-zero. Anything gating on this guard must read the
    exit code.
    """
    completed, dump = _run_nested(tmp_path, modify=True)
    _assert_confined(dump, tmp_path)

    assert completed.returncode != 0
    assert "passed" in completed.stdout
    assert "failed" not in completed.stdout


def test_a_clean_nested_run_is_silent_and_exits_zero(tmp_path):
    """The other direction: the guard loaded, watched, and said nothing."""
    completed, dump = _run_nested(tmp_path, modify=False)
    _assert_confined(dump, tmp_path)

    assert completed.returncode == 0, (
        "a nested run that changed nothing exited non-zero; stderr:\n"
        + completed.stderr
    )
    assert "PACT CLAUDE.md GUARD" not in completed.stderr
