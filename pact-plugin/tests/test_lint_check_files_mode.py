"""
Location: pact-plugin/tests/test_lint_check_files_mode.py
Summary: Subprocess contract tests for lint-check.sh --files mode (the
         consumer-facing tier of the import-hygiene ladder): the three
         verdict outcomes and their exit codes, the crash-honesty guard
         (a checker crash degrades to SKIPPED, never a phantom FINDINGS),
         and the missing-path pre-filter.
Used by: pytest suite. The predicate these rungs call is covered in
         test_check_unused_imports.py; the dev-repo strict-tier gate lives
         in test_import_hygiene.py. THIS file pins the shell contract that
         coder dispatch prose relies on: the LAST stdout line is exactly one
         verdict, and exit 1 means real findings — nothing else.

Scope boundary — --files mode ONLY:
    The legacy whole-tree directory mode (reached by passing a directory
    instead of --files) is a separate mode with separate ownership and is
    deliberately NOT pinned here. Everything below invokes the script with
    --files as the first argument, which returns before the legacy mode's
    `set -e` is ever enabled.

Determinism across environments:
    Rows that expect findings use a module-level unused import, which every
    rung of the ladder (ruff, pyflakes, flake8, stdlib fallback) reports as
    exactly one path:line-format line — so the verdict and count assertions
    hold no matter which rung wins on the host. The crash-guard row controls
    the ladder explicitly through PATH: a fake ruff that passes its execution
    probe and then crashes, plus a fake python3 that fails every probe, so
    no real rung can run and the fail-open degradation path is forced.
"""

import os
import stat
import subprocess
from pathlib import Path

_SCRIPT = (
    Path(__file__).parent.parent
    / "skills"
    / "pact-coding-standards"
    / "scripts"
    / "lint-check.sh"
)

def _run(*argv, env=None):
    return subprocess.run(
        ["bash", str(_SCRIPT), "--files", *argv],
        capture_output=True,
        text=True,
        env=env,
    )


def _last_stdout_line(proc):
    lines = [line for line in proc.stdout.splitlines() if line]
    assert lines, f"no stdout at all (stderr: {proc.stderr!r})"
    return lines[-1]


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class TestVerdictContract:
    """The three verdict outcomes: every invocation ends in exactly one
    verdict as the LAST stdout line, with the documented exit code."""

    def test_clean_file_pass_exit_zero(self, tmp_path):
        f = tmp_path / "clean.py"
        f.write_text("import os\nprint(os.sep)\n", encoding="utf-8")
        proc = _run(str(f))
        assert proc.returncode == 0
        assert _last_stdout_line(proc) == "IMPORT-HYGIENE: PASS"

    def test_findings_exit_one_verdict_last(self, tmp_path):
        f = tmp_path / "dirty.py"
        f.write_text("import os\n", encoding="utf-8")
        proc = _run(str(f))
        assert proc.returncode == 1
        assert _last_stdout_line(proc) == "IMPORT-HYGIENE: FINDINGS (1)"
        # The finding itself is on stdout above the verdict, in the shared
        # path:line format every rung of the ladder emits.
        assert f"{f}:1" in proc.stdout

    def test_no_python_files_skipped_exit_zero(self, tmp_path):
        f = tmp_path / "notes.txt"
        f.write_text("not python\n", encoding="utf-8")
        proc = _run(str(f))
        assert proc.returncode == 0
        assert (
            _last_stdout_line(proc)
            == "IMPORT-HYGIENE: SKIPPED (no Python files given)"
        )


class TestCrashHonestyGuard:
    """An unhandled checker exception also exits 1 — exit code alone must
    never be read as findings. A rung that exits 1 WITHOUT a single
    path:line-format output line is a crash: the ladder notes it on stderr,
    tries the next rung, and when nothing usable remains it fails OPEN with
    a SKIPPED verdict — never a phantom FINDINGS block."""

    def test_checker_crash_degrades_to_skipped_not_findings(self, tmp_path):
        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        # Passes the execution probe, then "crashes": exit 1 with
        # traceback-shaped output that contains no path:line: token.
        _write_executable(
            fakebin / "ruff",
            "#!/bin/bash\n"
            'if [ "$1" = "--version" ]; then echo fake-ruff; exit 0; fi\n'
            'echo "Traceback (most recent call last)"\n'
            'echo "SomeError: boom"\n'
            "exit 1\n",
        )
        # Fails every probe, so no python3-based rung (pyflakes, flake8,
        # stdlib fallback) can run at all.
        _write_executable(fakebin / "python3", "#!/bin/bash\nexit 9\n")

        target = tmp_path / "dirty.py"
        target.write_text("import os\n", encoding="utf-8")

        env = dict(os.environ)
        env["PATH"] = f"{fakebin}:{env['PATH']}"
        proc = _run(str(target), env=env)

        assert proc.returncode == 0
        assert _last_stdout_line(proc) == (
            "IMPORT-HYGIENE: SKIPPED (no usable import checker on this system)"
        )
        assert "FINDINGS" not in proc.stdout
        # The crash is loud on stderr, not silently swallowed.
        assert "failed (exit 1)" in proc.stderr
        assert "trying next checker" in proc.stderr


class TestMissingPathPrefilter:
    """Paths that no longer exist (deleted/renamed in the same change set)
    are dropped with a stderr note; the remaining files still get checked,
    and the verdict reflects only the real files."""

    def test_ghost_path_noted_and_rest_still_checked(self, tmp_path):
        ghost = tmp_path / "ghost.py"  # never created
        dirty = tmp_path / "dirty.py"
        dirty.write_text("import os\n", encoding="utf-8")

        proc = _run(str(ghost), str(dirty))

        assert proc.returncode == 1
        assert "skipping missing path" in proc.stderr
        assert str(ghost) in proc.stderr
        assert _last_stdout_line(proc) == "IMPORT-HYGIENE: FINDINGS (1)"
        assert f"{dirty}:1" in proc.stdout
