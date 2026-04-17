"""
Location: pact-plugin/hooks/shared/gh_helpers.py
Summary: Shared gh CLI wrappers for PACT hooks. Fail-open by construction.
Used by: shared.session_resume (resume decisions), hooks.session_end (#453
         consolidation-detection defense-in-depth).

Fail-open contract: every wrapper returns a safe sentinel on any error
(gh missing, auth expired, network timeout, unknown state, unexpected
OS error). Callers MUST treat the sentinel as "unknown — fall through
to existing logic," never as "gh said no."

Rationale: gh is an external dependency that can be absent, unauthenticated,
or unreachable at any time. Hooks must not break the user's session when
gh is unavailable, so these helpers swallow every raisable error and return
"" (empty string) instead of raising.
"""

from __future__ import annotations

import subprocess


def check_pr_state(pr_number: int | str) -> str:
    """
    Check PR state via ``gh pr view``. Returns uppercase state string or
    empty string on any error.

    Fail-open: returns "" if gh is missing, network times out, auth is
    expired, or the PR is not found. A 5-second subprocess timeout caps
    wall-clock latency so a slow or hung gh call cannot delay hook
    termination.

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
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "state", "--jq", ".state"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip().upper()
    except (
        FileNotFoundError,
        subprocess.TimeoutExpired,
        subprocess.CalledProcessError,
        OSError,
    ):
        # CalledProcessError is unreachable under the current call form
        # (check=False + explicit returncode==0 gate above), but listing it
        # here is defense-in-isolation: a future change to check=True or a
        # relocated subprocess call that invokes .check_returncode() would
        # raise CalledProcessError, and the fail-open contract requires
        # returning "" rather than propagating. Review cycle-1 L2 hardening.
        pass
    return ""
