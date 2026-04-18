"""
Location: pact-plugin/hooks/shared/gh_helpers.py
Summary: Shared gh CLI wrappers for PACT hooks. Fail-open by construction.
Used by: shared.session_resume (resume decisions), hooks.session_end (#453
         consolidation-detection defense-in-depth).

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
"" (empty string) instead of raising.
"""

from __future__ import annotations

import subprocess


def check_pr_state(pr_number: int | str) -> str:
    """
    Check PR state via ``gh pr view``. Returns uppercase state string or
    empty string on any error.

    Fail-open: returns "" on ANY exception — gh missing, network
    timeout, auth expired, PR not found, OSError, decoding failure,
    memory pressure, or unanticipated future exception classes. A
    5-second subprocess timeout caps wall-clock latency so a slow or
    hung gh call cannot delay hook termination. `KeyboardInterrupt`
    and `SystemExit` intentionally propagate so user-initiated cancel
    and explicit exits still work.

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
    except Exception:
        # Catch-all fail-open. The fail-open invariant is SACROSANCT for
        # this hook helper — any raisable error (gh missing, timeout,
        # auth failure, OSError, UnicodeDecodeError on non-UTF-8 stdout,
        # MemoryError under resource pressure, or a future exception
        # class we have not anticipated) must surface as the "" sentinel
        # rather than propagate into session_end / session_resume. An
        # explicit tuple would drift as new failure modes emerge;
        # `Exception` covers every non-BaseException raise in one place.
        # KeyboardInterrupt and SystemExit intentionally still propagate
        # — neither is a gh failure, and swallowing them would break
        # Ctrl-C on the hot path.
        pass
    return ""
