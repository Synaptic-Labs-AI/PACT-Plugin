"""
Location: pact-plugin/tests/test_pin_scripts_reuse_loaded_modules.py

The pin scripts (scripts/check_pin_caps.py, scripts/archive_pin.py) load
pin_caps and staleness from hooks/ by file path. A process that already
loaded those files keeps its one copy: importing a script must not replace
it, or a patch to one copy misses the code that reads the other. A different
file already loaded under the same name is not reused.

Each row runs in a fresh interpreter, so no earlier import in the test
process can make it pass.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
HOOKS = PLUGIN / "hooks"
SCRIPTS = ("check_pin_caps", "archive_pin")


def _run(probe: str, tmp_path: Path) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path),
           "CLAUDE_CONFIG_DIR": str(tmp_path),
           "PYTHONPATH": os.pathsep.join((str(HOOKS), str(PLUGIN / "scripts")))}
    return subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                          env=env, cwd=tmp_path, timeout=60)


@pytest.mark.parametrize("script", SCRIPTS)
def test_a_script_reuses_the_hook_modules_already_loaded(script, tmp_path):
    result = _run(
        "import sys\n"
        "import pin_caps, staleness\n"
        f"import {script} as script\n"
        "assert sys.modules['pin_caps'] is pin_caps and script._pin_caps is pin_caps\n"
        "assert sys.modules['staleness'] is staleness and script._staleness is staleness\n"
        "print('reused')\n",
        tmp_path)
    assert result.stdout.strip() == "reused", result.stderr


@pytest.mark.parametrize("script", SCRIPTS)
def test_a_different_file_under_the_name_is_not_reused(script, tmp_path):
    elsewhere = tmp_path / "pin_caps.py"
    result = _run(
        "import sys, types\n"
        "stand_in = types.ModuleType('pin_caps')\n"
        f"stand_in.__file__ = {str(elsewhere)!r}\n"
        "sys.modules['pin_caps'] = stand_in\n"
        f"import {script} as script\n"
        "assert script._pin_caps is not stand_in and sys.modules['pin_caps'] is script._pin_caps\n"
        f"assert script._pin_caps.__file__ == {str(HOOKS / 'pin_caps.py')!r}\n"
        "print('loaded from hooks')\n",
        tmp_path)
    assert result.stdout.strip() == "loaded from hooks", result.stderr
