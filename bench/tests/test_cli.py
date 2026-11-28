from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1]
# Each subcommand with a flag only its own parser defines, to prove dispatch.
COMMANDS = {
    ("prepare",): "--plugin-ref",
    ("native-eval",): "--cases-dir",
    ("plugin-eval",): "--plugin-eval-dir",
    ("tasks", "validate"): "--root",
}


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(BENCH / "pact-bench"), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_cli_help_lists_exactly_the_implemented_commands() -> None:
    proc = _cli("--help")
    assert proc.returncode == 0
    listed = [line.split("pact-bench ", 1)[1].split(" [")[0] for line in proc.stdout.splitlines() if "pact-bench " in line]
    assert listed == [" ".join(cmd) for cmd in COMMANDS]


@pytest.mark.parametrize("command", COMMANDS, ids=" ".join)
def test_cli_dispatches_each_subcommand_help(command: tuple[str, ...]) -> None:
    proc = _cli(*command, "--help")
    assert proc.returncode == 0, proc.stderr
    assert COMMANDS[command] in proc.stdout


def test_cli_imports_the_validator_by_package_path() -> None:
    probe = (
        "import runpy, sys\n"
        f"runpy.run_path({str(BENCH / 'pact-bench')!r}, run_name='not_main')\n"
        "assert 'validate' not in sys.modules, 'a bare validate module was imported'\n"
        f"assert {str(BENCH / 'tasks')!r} not in sys.path\n"
        "assert 'tasks.validate' in sys.modules\n"
    )
    proc = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("args", [("compare",), ("report",), ("bogus",), ("tasks",), ("tasks", "bogus")])
def test_cli_unknown_command_exits_2(args: tuple[str, ...]) -> None:
    assert _cli(*args).returncode == 2
