"""Merge-guard classifier fixes from the PR review: each row is an honest command.

DENY rows run a guarded command and must be refused. ALLOW rows only name one, or
run something else, and must be allowed: refusing them is the cardinal failure.
"""

import pytest
from shared.merge_guard_common import is_dangerous_command

D = "gh pr " + "merge 5"  # assembled so this file carries no guarded literal


# A call laid out over several lines, as black formats it.
MULTI_LINE_DENY = [
    ("stdin list", ("python3 - <<'EOF'\nimport subprocess\nsubprocess.run(\n"
                    "    [\"gh\", \"pr\", \"merge\", \"5\", \"--squash\"],\n    check=True,\n)\nEOF")),
    ("-c list", "python3 -c 'import subprocess\nsubprocess.run(\n    [\"gh\", \"pr\", \"merge\", \"5\"],\n)'"),
    ("stdin string", f"python3 - <<'EOF'\nimport os\nos.system(\n    \"{D}\"\n)\nEOF"),
]
MULTI_LINE_ALLOW = [
    ("stdin print", f"python3 - <<'EOF'\nprint(\n    \"{D}\"\n)\nEOF"),
]


@pytest.mark.parametrize("label,cmd", MULTI_LINE_DENY, ids=[r[0] for r in MULTI_LINE_DENY])
def test_multi_line_call_running_a_merge_is_refused(label, cmd):
    assert is_dangerous_command(cmd) is True


@pytest.mark.parametrize("label,cmd", MULTI_LINE_ALLOW, ids=[r[0] for r in MULTI_LINE_ALLOW])
def test_multi_line_call_printing_a_merge_is_allowed(label, cmd):
    assert is_dangerous_command(cmd) is False
