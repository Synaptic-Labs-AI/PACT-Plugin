"""
Location: pact-plugin/hooks/shared/git_helpers.py
Summary: Narrow subprocess wrapper for git CLI calls from PACT hooks.
Used by: git_commit_check.get_staged_files, get_staged_file_content,
         check_env_file_in_gitignore.

Encapsulates try/except + subprocess boilerplate only. Callers own:
- success-path processing (list/string/tuple conversion)
- fail-open default (what to return when run_git returns None)
- return-type shape

Fail-open contract: run_git returns None on TimeoutExpired or
FileNotFoundError. All other exceptions propagate — this is NARROWER
than gh_helpers.run_gh's `except Exception` catch-all. See arch §8
"two-wrapper rationale" for why the git and gh wrappers have
deliberately different exception postures.
"""

from __future__ import annotations

import subprocess
from typing import Optional, Sequence


def run_git(
    args: Sequence[str],
    timeout: int = 5,
    text: bool = True,
) -> Optional[subprocess.CompletedProcess]:
    """
    Invoke `git <args...>` with capture_output and a 5-second default timeout.

    Returns the CompletedProcess on any invocation that reaches git (including
    non-zero exits that the caller needs to triage on `returncode`). Returns
    None on TimeoutExpired or FileNotFoundError (fail-open).

    `text=True` by default so stdout/stderr are str, not bytes. Callers that
    need raw bytes (e.g., binary git output) pass `text=False`.

    Args:
        args: git subcommand + flags (without leading "git"). Example:
              ["diff", "--name-only", "--cached"] or ["check-ignore", "-q", ".env"].
        timeout: seconds before subprocess.TimeoutExpired is raised. Default 5s,
                 matching gh_helpers.run_gh / check_pr_state convention.
        text: decode stdout/stderr as UTF-8 if True (default); return bytes if False.

    Returns:
        CompletedProcess on success-path (regardless of returncode); None on
        (TimeoutExpired, FileNotFoundError). Callers MUST check for None
        before touching result.returncode or result.stdout.
    """
    try:
        return subprocess.run(
            ["git", *args],
            capture_output=True,
            text=text,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
