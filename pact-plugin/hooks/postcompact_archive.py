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
2. Asks shared.compaction_owner whose compaction it is. An in-process
   teammate compacts inside the lead's process and its frame is lead-shaped,
   so the transcripts decide. The verdict is journaled as compaction_attributed
   in the destination's session, and a teammate verdict skips step 3.
3. Writes it to {session_dir}/compact-summary.txt — the SESSION that produced
   it owns the file (#1504) — degrading LOSS-FREE to the root singleton when
   the frame is unidentified. The resolution and its degradation live in ONE
   total call: pact_context.resolve_compact_summary_path.
4. Emits suppressOutput to avoid false "hook error" UI display on clean exits

This is a non-blocking side effect (always exits 0), not a gate.

Input: JSON from stdin with compact_summary field
Output: JSON suppressOutput on stdout (clean path) or hook_error_json (failure)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from shared import compaction_owner
from shared.constants import COMPACT_SUMMARY_NAME, get_compact_summary_path
from shared.error_output import hook_error_json
from shared.pact_context import is_lead, resolve_compact_summary_path
from shared.session_journal import append_event, make_event


# ---------------------------------------------------------------------------
# Compact summary persistence
# ---------------------------------------------------------------------------


def write_compact_summary(
    summary: str,
    session_dir: str | None = None,
) -> bool:
    """
    Write the compact summary to disk for the secretary.

    ``session_dir`` is the FULLY RESOLVED destination directory (the
    test-injection seam, replacing the old ``sessions_base_dir``). Production
    callers resolve it via resolve_compact_summary_path and pass its parent;
    a bare call falls back to the root singleton path for the filename.
    Creates the directory if needed. Uses secure file permissions (0o600).
    Returns True on success, False on any error.
    """
    try:
        if session_dir:
            path = Path(session_dir) / COMPACT_SUMMARY_NAME
        else:
            path = get_compact_summary_path()
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


def record_attribution(destination: Path, verdict: str, basis: str) -> None:
    """Journal the compaction verdict in the destination's session.

    A root-singleton destination means the frame named no session, so there is
    no session journal to write to. Never raises: the summary write must not
    depend on the journal.
    """
    if destination == get_compact_summary_path():
        return
    try:
        append_event(
            make_event("compaction_attributed", verdict=verdict, basis=basis),
            session_dir=str(destination.parent),
        )
    except Exception:
        pass


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
        #
        # Lead-only (#881, re-scoped by #1504): the write is O_TRUNC, and
        # in-process subagent topology COLLAPSES a teammate's session_id onto
        # the LEAD's — so ungated, a teammate PostCompact writes into the
        # lead's own session directory, resurrecting the clobber #881 fixed,
        # now inside it. Gate the write behind is_lead. is_lead is total and
        # only reaches stdin_data here when compact_summary is truthy, which
        # the isinstance(dict) guard above already established — so stdin_data
        # is a dict and the .get inside is_lead cannot raise.
        #
        # The destination resolves via the TOTAL resolver: session-scoped when
        # the frame is identifiable, root singleton otherwise. Degradation
        # lives INSIDE that one call — no fallback branch here.
        #
        # An in-process teammate's frame is ALSO lead-shaped: it carries the
        # lead's agent_type, session_id and transcript_path. compaction_owner
        # reads the transcripts to tell them apart, and only a teammate verdict
        # skips the write; lead and unknown write as before.
        if compact_summary and is_lead(stdin_data):
            destination = resolve_compact_summary_path(stdin_data)
            verdict, basis = compaction_owner.attribute_compaction(stdin_data)
            record_attribution(destination, verdict, basis)
            if verdict != compaction_owner.TEAMMATE:
                write_compact_summary(compact_summary, str(destination.parent))

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
