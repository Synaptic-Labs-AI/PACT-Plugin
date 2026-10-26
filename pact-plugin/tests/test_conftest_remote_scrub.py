"""
Location: pact-plugin/tests/test_conftest_remote_scrub.py
Summary: Meta-test for the autouse ``_scrub_claude_code_remote_env`` fixture
in tests/conftest.py (#1671 review B-MINOR-1). The parent test starts a child
pytest run with CLAUDE_CODE_REMOTE=true already in its environment, the way a
cloud container exports it, before any fixture runs. The child test then
asserts that the scrub removed it: ``is_cloud_session()`` is False inside the
test body. This proves the scrub on every machine, including one where the
variable is normally unset (where ``test_unset_is_local`` alone is vacuous).
Used by: the full-suite merge gate (`cd pact-plugin && python3 -m pytest -q`).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import shared.pact_context as pact_context

# Set only in the child run the parent starts; the child test is skipped in
# every ordinary run.
_CHILD_FLAG = "PACT_REMOTE_SCRUB_META_CHILD"

# Captured at import (collection) time, before any fixture runs: in the
# child, this records that the variable really was in the environment.
_REMOTE_AT_IMPORT = os.environ.get("CLAUDE_CODE_REMOTE")

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(os.environ.get(_CHILD_FLAG) != "1",
                    reason="runs only inside the meta-test's child pytest")
def test_child_sees_the_variable_scrubbed():
    assert _REMOTE_AT_IMPORT == "true", "the parent must export the variable"
    assert "CLAUDE_CODE_REMOTE" not in os.environ
    assert pact_context.is_cloud_session() is False


def test_scrub_removes_an_exported_cloud_signal():
    env = dict(os.environ)
    env["CLAUDE_CODE_REMOTE"] = "true"
    env[_CHILD_FLAG] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-p", "no:randomly", "-p", "no:xdist",
         f"{Path(__file__).name}::test_child_sees_the_variable_scrubbed"],
        cwd=str(_PLUGIN_ROOT / "tests"),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    # The child test must have RUN, not been skipped or deselected.
    assert "1 passed" in output, output
