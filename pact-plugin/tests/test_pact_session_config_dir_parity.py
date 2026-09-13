"""
Location: pact-plugin/tests/test_pact_session_config_dir_parity.py
Summary: Pins that skills/pact-memory/scripts/pact_session.py anchors its paths
         at the resolved Claude config root, and that it can actually IMPORT
         that resolver the way production invokes it.
Used by: the merge gate only — nothing imports this module.

SCOPE, ON PURPOSE — do not "restore" the missing coverage:

  This file does NOT re-test the resolver's precedence contract (unset / empty /
  whitespace / "~" / "~/x" / absolute / relative / trailing slash / nonexistent).
  tests/test_paths.py owns that, with 21 hardcoded-literal tests including
  test_exact_prefix_slice_not_lstrip and test_no_expanduser_uses_resolved_home.
  Restating those clauses here would be a second statement of one contract, free
  to drift, with nothing comparing the two — the duplicate-implementation problem
  moved to the test layer.

  What is left for this file is what test_paths.py cannot see: whether
  pact_session ANCHORS at the resolved root, and whether it REACHES the resolver
  at all.

EXPECTED VALUES ARE HARDCODED, never computed by calling the resolver. Since
pact_session now imports the same function a test would call, deriving the
expectation from it would move expectation and subject together — a resolver
that stopped honoring $CLAUDE_CONFIG_DIR would green every arm here. (The
opposite rule applied while pact_session held its own copy: with two
implementations, restating would have been a third. The rule inverts on how many
implementations exist.)

WHY THE SUBPROCESS ARM IS NOT OPTIONAL: tests/conftest.py puts BOTH hooks/ and
skills/pact-memory/ on sys.path for the whole suite. So in-process, the import
under test succeeds whether or not pact_session's own bootstrap works — a wrong
parents[] index would leave every other arm in this file green while production
raised ModuleNotFoundError on each cli.py invocation. Only a subprocess without
conftest's help can tell those apart.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from scripts import pact_session
from clock_shift.clock_shift_env import carry_clock_shift

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "pact-memory" / "scripts"


def test_context_file_path_anchors_at_relocated_root(tmp_path, monkeypatch):
    """A relocated root must move the context path off home entirely."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "relocated"))

    # Hardcoded, not get_claude_config_dir(). conftest redirects home to tmp_path,
    # so a home-anchored regression lands on tmp_path/".claude"/... instead.
    expected = (
        tmp_path / "relocated" / "pact-sessions"
        / "proj" / "sid" / "pact-session-context.json"
    )

    assert pact_session._context_file_path("sid", "/somewhere/proj") == expected


def test_context_file_path_honors_tilde_root(tmp_path, monkeypatch):
    """`~` resolves to home ITSELF, not home/".claude".

    Catches a re-hardcode that reads $CLAUDE_CONFIG_DIR directly instead of
    delegating — such a version passes the absolute-root arm above and fails
    here, because it would treat "~" as a literal directory name.
    """
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "~")

    expected = tmp_path / "pact-sessions" / "proj" / "sid" / "pact-session-context.json"

    assert pact_session._context_file_path("sid", "/somewhere/proj") == expected


def test_disk_discovery_anchors_at_relocated_root(tmp_path, monkeypatch):
    """Session discovery must GLOB the resolved root — a second call site.

    A fix that re-anchors only the path builder leaves this one reading home,
    and every arm above still passes.
    """
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "relocated"))
    session_id = "session-abc"

    context_file = (
        tmp_path / "relocated" / "pact-sessions"
        / "proj" / session_id / "pact-session-context.json"
    )
    context_file.parent.mkdir(parents=True)
    context_file.write_text(json.dumps({"session_id": session_id}), encoding="utf-8")

    assert pact_session._resolve_context_on_disk(session_id) == session_id


def test_import_reaches_resolver_from_direct_script_context(tmp_path):
    """pact_session must import the resolver ON ITS OWN, as production does.

    Runs in a subprocess with ONLY the scripts dir on sys.path — the direct-script
    layout `python3 .../scripts/cli.py` produces — from a cwd outside the repo,
    with PYTHONPATH scrubbed. conftest's path help is absent by construction, so
    this fails if pact_session's bootstrap is wrong, missing, or silently falling
    back to a local copy.
    """
    root = tmp_path / "relocated"
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "import pact_session;"
        "print(pact_session._context_file_path('sid', '/somewhere/proj'))"
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["CLAUDE_CONFIG_DIR"] = str(root)

    result = subprocess.run(
        [sys.executable, "-c", probe, str(SCRIPTS_DIR)],
        cwd=tmp_path, env=carry_clock_shift(env), capture_output=True, text=True,
    )

    assert result.returncode == 0, f"import failed as production would:\n{result.stderr}"
    expected = root / "pact-sessions" / "proj" / "sid" / "pact-session-context.json"
    assert result.stdout.strip() == str(expected)
