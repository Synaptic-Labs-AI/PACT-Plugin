"""
Location: pact-plugin/hooks/shared/gh_helpers.py
Summary: Shared gh CLI wrappers for PACT hooks. Fail-open by construction.
Used by: shared.session_resume, hooks.session_end, and any hook shelling
         out to gh via run_gh.

Fail-open contract: every wrapper returns a safe sentinel on any
raisable exception (gh missing, auth expired, network timeout,
unknown state, OSError, decoding failure, memory pressure, or any
future exception class). Callers MUST treat the sentinel as
"unknown — fall through to existing logic," never as "gh said no."
`KeyboardInterrupt` and `SystemExit` are deliberately NOT caught so
Ctrl-C and explicit exits still work.

Rationale: gh is an external dependency that can be absent, unauthenticated,
or unreachable at any time. Hooks must not break the user's session when
gh is unavailable, so these helpers swallow every raisable error and return
a safe sentinel (None for run_gh, "" for check_pr_state) instead of raising.
"""

from __future__ import annotations

import subprocess
from typing import Optional, Sequence


def run_gh(
    args: Sequence[str],
    timeout: int = 5,
    text: bool = True,
) -> Optional[subprocess.CompletedProcess]:
    """
    Invoke `gh <args...>` with capture_output and a 5-second default timeout.

    Returns the CompletedProcess on any invocation that reaches gh. Returns
    None on ANY exception (catch-all `except Exception`) per the
    gh_helpers.py SACROSANCT fail-open contract (see module docstring):
    gh missing, timeout, auth failure, OSError, decoding failure, memory
    pressure, or future exception classes. KeyboardInterrupt and SystemExit
    intentionally propagate.

    Callers MUST check for None before touching result.returncode / result.stdout.
    Deliberately BROADER exception catch than shared.git_helpers.run_git
    because gh is a network-dependent external service with a wider failure
    surface than a local git binary; see arch §8 "two-wrapper rationale".

    Args:
        args: gh subcommand + flags (without leading "gh"). Example:
              ["pr", "view", "123", "--json", "state", "--jq", ".state"].
        timeout: seconds before subprocess.TimeoutExpired is raised.
                 Default 5s, matching check_pr_state's original convention.
        text: decode stdout/stderr as UTF-8 if True (default); return bytes if False.

    Returns:
        CompletedProcess on success-path; None on any exception.
    """
    try:
        return subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=text,
            timeout=timeout,
        )
    except Exception:
        # Catch-all per gh_helpers.py SACROSANCT fail-open contract.
        # See module docstring for rationale.
        return None


def check_pr_state(pr_number: int | str) -> str:
    """
    Check PR state via ``gh pr view``. Returns uppercase state string or
    empty string on any error.

    Fail-open: delegates to run_gh, which returns None on ANY exception —
    gh missing, network timeout, auth expired, PR not found, OSError,
    decoding failure, memory pressure, or unanticipated future exception
    classes. A 5-second subprocess timeout caps wall-clock latency so a slow
    or hung gh call cannot delay hook termination. `KeyboardInterrupt` and
    `SystemExit` intentionally propagate so user-initiated cancel and
    explicit exits still work.

    Possible non-empty returns (GitHub API canonical values, uppercased):
    - "OPEN"   — PR is open
    - "MERGED" — PR has been merged
    - "CLOSED" — PR was closed without merging

    Args:
        pr_number: PR number (int or str). Coerced to str for the gh call.

    Returns:
        Uppercase state string (e.g. "OPEN", "MERGED", "CLOSED"), or "" on
        any error or unknown response. Callers MUST treat "" as "unknown."
    """
    result = run_gh(
        ["pr", "view", str(pr_number), "--json", "state", "--jq", ".state"]
    )
    if result is None or result.returncode != 0:
        return ""
    return result.stdout.strip().upper()
