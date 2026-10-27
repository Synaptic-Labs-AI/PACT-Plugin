"""Tracker hermeticity guard shared by the two backlog test modules.

Location: pact-plugin/tests/_backlog_tracker_guard.py
Used by:  tests/test_backlog.py and tests/test_backlog_rest_fallback.py, each
          of which imports both fixtures below by name. Importing an autouse
          fixture into a test module makes it autouse FOR THAT MODULE ONLY, so
          this guard covers exactly those two files and no other module in the
          suite. That is why it lives here and not in conftest.py, where an
          autouse fixture would run for every test in the repository.

Not a test module (no `test_` prefix), so pytest never collects it.
"""
import os
import subprocess

import pytest


@pytest.fixture(scope="session")
def _gh_tripwire_dir(tmp_path_factory):
    """A `gh` that records being run, first on PATH for every child process.

    The in-process guard below cannot see a CHILD process — the CLI arms spawn
    `python backlog.py ...`, which would reach the real `gh` (and the network)
    if a fixture ever carried a live ref. This one can: it writes a marker the
    per-test guard checks, and exits 1 with no stdout, which every caller
    already reads as "no answer".
    """
    directory = tmp_path_factory.mktemp("gh-tripwire")
    marker = directory / "ran"
    script = directory / "gh"
    script.write_text(
        "#!/bin/sh\n"
        f"echo \"$*\" >> '{marker}'\n"
        "exit 1\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return directory, marker


@pytest.fixture(autouse=True)
def _no_real_tracker(monkeypatch, _gh_tripwire_dir):
    """Every test in an importing module is hermetic with respect to the tracker.

    1. `GH_REPO` and `GH_HOST` are removed, so a developer or CI shell that
       sets either cannot settle the slug or the host and route an arm past
       the rung it means to exercise.
    2. Any IN-PROCESS `subprocess.run` of `gh` that no test stubbed fails the
       test by name. An arm that installs its own fake replaces this guard for
       its own duration, which is the point: only an UNSTUBBED call trips it.
    3. Any CHILD-process `gh` lands on the tripwire above and fails the test at
       teardown.

    RED WHEN a test reaches a real `gh`. Measured when this guard was added:
    with a logging `gh` and `git` on PATH the whole module made zero `gh`
    calls — so the guard pins a property the suite already had.
    """
    directory, marker = _gh_tripwire_dir
    monkeypatch.delenv("GH_REPO", raising=False)
    monkeypatch.delenv("GH_HOST", raising=False)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ.get('PATH', '')}")
    real_run = subprocess.run

    def guarded_run(command, *args, **kwargs):
        head = command[0] if isinstance(command, (list, tuple)) and command else command
        if isinstance(head, str) and os.path.basename(head) == "gh":
            pytest.fail(f"an unstubbed `gh` call reached subprocess.run: {command!r}")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded_run)
    if marker.exists():
        marker.unlink()
    yield
    if marker.exists():
        ran = marker.read_text(encoding="utf-8")
        marker.unlink()
        pytest.fail(f"a child process ran the real `gh`: {ran!r}")


@pytest.fixture
def hermetic_git(monkeypatch):
    """Real `git` with no global, system or environment-injected config.

    `git remote get-url` applies `url.<base>.insteadOf` rules, and harnesses
    inject them through GIT_CONFIG_COUNT / GIT_CONFIG_KEY_n / GIT_CONFIG_VALUE_n
    (Claude Code cloud sessions do) as well as through ~/.gitconfig and
    /etc/gitconfig. A real-git arm that reads a remote must see only the
    config the arm itself wrote, or a developer's mirror rule turns it red for
    a reason unrelated to the code. Applied BEFORE the arm's first git call.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in list(os.environ):
        if name in ("GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS") or name.startswith(
                ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            monkeypatch.delenv(name, raising=False)
