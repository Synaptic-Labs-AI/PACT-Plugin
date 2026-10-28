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
a safe sentinel (None for run_gh and rest_json, "" for check_pr_state and
rest_pr_state) instead of raising.

REST fallback: `gh pr view` is GraphQL-backed, and some sessions refuse
GraphQL. When it reaches gh and exits non-zero, a reader may retry over REST
(`gh api`), which those sessions allow. The `{owner}/{repo}` placeholders
in a `gh api` path are filled by gh from the current repo's remotes with the
same choice of repo `gh pr view` makes for a bare number, and without a
network call, so the retry reads the PR the fast path would have read.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Optional, Sequence

_PR_NUMBER = re.compile(r"[0-9]+")


def run_gh(
    args: Sequence[str],
    timeout: int = 5,
    text: bool = True,
    cwd: str | None = None,
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
        cwd: directory gh runs in, which selects the repo it resolves a bare PR
             number against. Default None: the caller's working directory.

    Returns:
        CompletedProcess on success-path; None on any exception.
    """
    try:
        return subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=text,
            timeout=timeout,
            cwd=cwd,
        )
    except Exception:
        # Catch-all per gh_helpers.py SACROSANCT fail-open contract.
        # See module docstring for rationale.
        return None


def check_pr_state(pr_number: int | str, cwd: str | None = None) -> str:
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
        cwd: directory whose repo the number names (passed to run_gh). Default
             None: the caller's working directory.

    Returns:
        Uppercase state string (e.g. "OPEN", "MERGED", "CLOSED"), or "" on
        any error or unknown response. Callers MUST treat "" as "unknown."

    When `gh pr view` reaches gh and exits non-zero (GraphQL refused, among
    other causes), the state is read once more over REST from the same repo
    (see the module docstring). A run_gh None (gh missing, timeout) is not
    retried, and neither is a PR number that is not all digits.
    """
    result = run_gh(
        ["pr", "view", str(pr_number), "--json", "state", "--jq", ".state"],
        cwd=cwd,
    )
    if result is None:
        return ""
    if result.returncode == 0:
        return result.stdout.strip().upper()
    if not _PR_NUMBER.fullmatch(str(pr_number)):
        return ""
    return rest_pr_state(rest_json(f"repos/{{owner}}/{{repo}}/pulls/{pr_number}", cwd=cwd))


def rest_json(path: str, timeout: int = 5, cwd: str | None = None) -> object:
    """`gh api <path>` parsed as JSON, or None when gh is missing, exits
    non-zero (a 404 included) or prints something that is not JSON. `cwd`
    selects the repo gh fills `{owner}` and `{repo}` from."""
    result = run_gh(["api", path], timeout=timeout, cwd=cwd)
    if result is None or result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError):
        return None


def rest_pr_state(pull: object) -> str:
    """MERGED, CLOSED or OPEN from a REST pull-request body, or "" for any
    other shape. A list entry carries merged_at but not merged, so either
    marks a merge."""
    if not isinstance(pull, dict):
        return ""
    if pull.get("merged") is True or pull.get("merged_at"):
        return "MERGED"
    return {"closed": "CLOSED", "open": "OPEN"}.get(str(pull.get("state")), "")
