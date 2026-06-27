"""
Location: pact-plugin/tests/test_merge_guard_1037_smoke.py
Summary: CODE-phase smoke for the #1037 HYBRID benign-arg-literal suppressor in
         merge_guard_pre.py. A minimal go/no-go: a whitelisted false-positive is
         now ALLOWED, and the under-block guards still BLOCK. The COMPREHENSIVE
         ALLOW-positives + per-guard under-block canaries are the test-engineer's
         TEST-phase work (test_merge_guard.py); this file is intentionally small.
Used by: pytest (merge-guard suite).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import pytest

from merge_guard_pre import is_dangerous_command


# Whitelisted non-executor segments whose quoted arg merely NAMES a dangerous
# command — previously a false-positive BLOCK (#1037), now correctly ALLOWED.
ALLOWED_FALSE_POSITIVES = [
    'gh pr comment 5 --body "see gh pr merge 5 for context"',
    'gh issue comment 12 --body "do not git push --force origin main"',
    'grep -n "gh pr merge" notes.md',
    'pact_memory search "gh pr merge approval flow"',
    'git commit -am "wire up gh pr merge 5 handler"',
    # Quote-aware segment split: a shell separator INSIDE the quoted arg must NOT
    # split the segment (modeled on step-7 _gh_carrier_span). These were
    # over-blocked before the quote-aware boundary mask.
    'grep "a ; gh pr merge 5" file',
    'gh pr comment 5 --body "do it; gh pr merge 9 then ship"',
    "git commit -am 'fix: gh pr merge 7 | follow-up'",
    # Placeholder fail-closed is PER-SEGMENT: an earlier-step placeholder in one
    # segment (echo -> "echo STRIPPED") must not block a clean sibling segment's
    # suppression. The grep segment still clears.
    'echo "note" ; grep "gh pr merge 5" file',
]

# Must STILL be blocked — the suppressor must never open an under-block. Each
# exercises a distinct fail-closed path (indirection guard, cmd-subst inside a
# whitelisted arg, an arg-executing verb that is NOT whitelisted, a chained
# second op, and the bare destructive op itself).
STILL_BLOCKED = [
    'grep "gh pr merge 5" file | bash',                       # pipe-to-shell guard
    'grep "$(gh pr merge 5)" file',                           # cmd-subst executes in dq
    "python3 -c \"import os; os.system('gh pr merge 5')\"",   # python -c NOT whitelisted
    'gh pr comment 5 --body "ok" && gh pr merge 999',         # chained destructive op
    'gh pr merge 5',                                          # bare destructive op
    'grep "x" file ; gh pr merge 5',                          # REAL unquoted sep still splits
]


@pytest.mark.parametrize("command", ALLOWED_FALSE_POSITIVES)
def test_whitelisted_arg_literal_false_positive_now_allowed(command):
    assert is_dangerous_command(command) is False


@pytest.mark.parametrize("command", STILL_BLOCKED)
def test_suppressor_never_opens_an_under_block(command):
    assert is_dangerous_command(command) is True
