#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/postcompact_archive.py
Summary: PostCompact hook that archives the compact_summary to disk so
         session_init (on the subsequent SessionStart:compact event) and
         the secretary (on post-compaction briefing) can read it.
         Renamed from postcompact_verify.py in PR #447 cleanup — #444
         Tertiary deleted the verification logic, leaving only the
         archival responsibility.
Used by: hooks.json PostCompact hook

After compaction completes:
1. Reads compact_summary from stdin (PostCompact input field)
2. Writes the compact_summary to the canonical COMPACT_SUMMARY_PATH
3. Emits suppressOutput to avoid false "hook error" UI display on clean exits

This is a non-blocking side effect (always exits 0), not a gate.

Input: JSON from stdin with compact_summary field
Output: JSON suppressOutput on stdout (clean path) or hook_error_json (failure)
"""

import json
import os
import sys
from pathlib import Path

from shared.constants import COMPACT_SUMMARY_PATH
from shared.error_output import hook_error_json


# ---------------------------------------------------------------------------
# Compact summary persistence
# ---------------------------------------------------------------------------


def _get_summary_path(
    sessions_base_dir: str | None = None,
) -> Path:
    """Get the path for the compact summary file."""
    if sessions_base_dir is None:
        return COMPACT_SUMMARY_PATH
    return Path(sessions_base_dir) / COMPACT_SUMMARY_PATH.name


def write_compact_summary(
    summary: str,
    sessions_base_dir: str | None = None,
) -> bool:
    """
    Write the compact summary to disk for the secretary.

    Creates the directory if needed. Uses secure file permissions (0o600).
    Returns True on success, False on any error.
    """
    try:
        path = _get_summary_path(sessions_base_dir)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Secure write: 0o600 permissions
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, summary.encode("utf-8"))
        finally:
            os.close(fd)
        return True
    except OSError:
        return False


def main():
    try:
        # Read PostCompact input
        stdin_data = {}
        try:
            stdin_data = json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            pass

        compact_summary = ""
        if isinstance(stdin_data, dict):
            compact_summary = stdin_data.get("compact_summary", "")

        # Write summary to disk for secretary (the only surviving side effect).
        # Per #444 Tertiary: no systemMessage emission. The previously-emitted
        # "Post-compaction: critical context preserved" message was reassurance
        # that could suppress orchestrator self-check (see issue #444 root cause).
        if compact_summary:
            write_compact_summary(compact_summary)

        # Suppress output to avoid false "hook error" UI display on clean exits.
        print(json.dumps({"suppressOutput": True}))
        sys.exit(0)

    except Exception as e:
        # Fail open — never block post-compaction
        print(
            f"Hook warning (postcompact_archive): {e}", file=sys.stderr
        )
        print(hook_error_json("postcompact_archive", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
