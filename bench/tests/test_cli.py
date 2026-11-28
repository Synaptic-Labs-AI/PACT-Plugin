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
    # The usage line names the subcommand, so it can be pasted back as printed.
    assert proc.stdout.startswith(f"usage: pact-bench {' '.join(command)} ")


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


def test_cli_returns_the_subcommands_exit_code(tmp_path: Path) -> None:
    (tmp_path / "no-task").mkdir()
    assert _cli("tasks", "validate", "--task", str(tmp_path / "no-task")).returncode == 1
    missing = str(tmp_path / "missing.json")
    assert _cli("native-eval", "--manifest", missing).returncode != 0
    assert _cli("plugin-eval", "--manifest", missing, "--plugin-eval-dir", str(tmp_path)).returncode != 0


def test_cli_tasks_help_is_help() -> None:
    proc = _cli("tasks", "--help")
    assert (proc.returncode, proc.stdout.startswith("usage: pact-bench tasks validate")) == (0, True)


@pytest.mark.parametrize("args", [("compare",), ("report",), ("bogus",), ("tasks",), ("tasks", "bogus")])
def test_cli_unknown_command_exits_2(args: tuple[str, ...]) -> None:
    assert _cli(*args).returncode == 2
