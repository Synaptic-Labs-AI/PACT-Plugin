"""
Location: pact-plugin/hooks/shared/session_resume.py
Summary: Session resume and snapshot management for cross-session continuity.
Used by: session_init.py during SessionStart hook to write session info,
         restore previous session snapshots, check for resumable tasks,
         and detect paused work from previous sessions.

Manages:
1. Writing session resume info (team name, resume command) to project CLAUDE.md
2. Restoring last session context from session journal
3. Checking for in-progress tasks that indicate resumable work
4. Detecting paused state from session journal
5. Unified resume-claim resolution over paused/refreshed checkpoints
   (check_resume_state — the single seam session_init step 8 calls)
6. Reading the previous session's dir back from the Current Session block
   (_extract_prev_session_dir), and the session-placeholder substitution
   sentence (format_session_substitutions)
"""

from __future__ import annotations

import errno
import fcntl  # Unix-only; PACT supports macOS/Linux.
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shared.claude_md_manager import (
    MANAGED_END_MARKER,
    MANAGED_START_MARKER,
    MANAGED_TITLE,
    MEMORY_END_MARKER,
    MEMORY_START_MARKER,
    RETRIEVED_CONTEXT_COMMENT,
    SESSION_END_MARKER,
    SESSION_START_MARKER,
    WORKING_MEMORY_COMMENT,
    ContainmentError,
    _atomic_write_text,
    _read_replaced,
    ensure_dot_claude_parent,
    file_lock,
    resolve_project_claude_md_path,
)
from shared.failure_cause import failure_cause
from shared.handoff_schema import resolve_handoff_field
from shared.pact_context import (
    LEAD_ADDRESS_LOCAL,
    LEAD_ADDRESSES,
    _build_session_path,
    get_session_dir,
    project_slug,
)
from shared.paths import get_claude_config_dir
from shared.session_state import is_safe_path_component, is_safe_plugin_root
from shared.stale_session import recorded_session_id, session_block_rows
from shared.task_utils import iter_team_task_jsons
from shared.session_journal import (
    _parse_ts,
    _ts_supersedes,
    append_event,
    make_event,
    read_events_from,
    read_last_event_from,
)

# The finder is imported inside the functions that use it: shared/__init__.py
# imports this module, so a module-level import would load it in every hook.
if TYPE_CHECKING:
    from shared.claude_md_markers import Document

# Maximum characters for decision summaries in journal resume output
_DECISION_TRUNCATION_LIMIT = 80

# Staleness horizon for a session_refreshed checkpoint. A refresh is meant
# to be consumed within minutes (refresh → /compact → bootstrap); past this
# horizon the prompt gets an informational STALE prefix. Downgrade ONLY —
# never suppression: the mid-flight claim (and any HALT line) survives.
_REFRESH_STALE_HOURS = 48

# Bounds for event field values interpolated into the refreshed resume
# prompt. Journal events are written by the refresh command but the journal
# file itself is plain JSONL on disk — a hand-crafted or corrupted event
# must not be able to flood the SessionStart context or smuggle directive
# lines into the prompt. Free-text fields (feature_subject, next_phase,
# task ids) get the tight bound; worktree paths get a wider one because
# legitimate absolute paths can be long.
_REFRESH_FIELD_TRUNCATION_LIMIT = 200
_REFRESH_PATH_TRUNCATION_LIMIT = 512

# IDENTIFIER is a THIRD field kind. No value in THIS module takes it today.
# It is defined here because the field-kind classification is SHARED
# vocabulary with the twin copy in
# skills/pact-memory/scripts/working_memory.py, which bounds a memory id
# with it, and a classification that lives in one copy alone lets the two
# writers drift on what a field kind means. Held by the constants arm of
# TestSanitizePromptFieldTwinCopyDrift; if you change either, update both
# in the SAME commit.
_REFRESH_IDENTIFIER_TRUNCATION_LIMIT = 64

# Control characters stripped from interpolated refreshed-prompt fields:
# C0 controls (includes \n, \r, \t), DEL plus the full C1 block (which
# includes NEL U+0085 — a str.splitlines boundary), and the Unicode
# line/paragraph separators — anything that could break the prompt onto
# a new line and masquerade as a separate directive.
#
# 🔴 A THIRD CLASS EXISTS AND IT IS NARROWER ON PURPOSE. DO NOT MERGE THEM.
# `session_state.SESSION_ID_CONTROL_CHARS_RE` covers the same line breakers
# and omits the non-line-breaking C1 characters. The two do different jobs:
# that one is a DETECTOR, used only through `.search()` on identifiers, so it
# carries no `+` and needs none. THIS one is a REPLACER, used through
# `.sub(" ", value)`, and HERE THE `+` IS LOAD-BEARING: without it a run of N
# control characters becomes N spaces rather than one. Widening that one to
# match this one would refuse session ids over characters that break no line.
_PROMPT_CONTROL_CHARS_RE = re.compile("[\\x00-\\x1f\\x7f-\\x9f\\u2028\\u2029]+")

# Maximum characters for phase strings rendered into journal resume output.
# Phases are nominally short uppercase identifiers (CODE, TEST, etc.) but the
# consumer must defend against historical or hand-crafted events that stashed
# a long free-form string or a non-string type in the `phase` field.
_PHASE_TRUNCATION_LIMIT = 80


class TransientSessionInfoFailure(str):
    """An update_session_info status for a failure that may clear on its own:
    the lock held past its timeout, or an I/O error. The text is unchanged, so
    callers that route on its wording see no difference; a caller that retries
    tests for this type instead of matching the words."""


# OSErrors that say the path itself is unusable, so a retry meets the same
# refusal: no access, no such path, a directory where the lock file or the
# file belongs, and a filesystem without flock. Any other errno, and any other
# exception, may clear and counts as transient.
_PATH_PRECONDITION_ERRNOS = frozenset({
    errno.EACCES, errno.EPERM, errno.EROFS, errno.ENOENT, errno.ENOTDIR,
    errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOLCK, errno.EISDIR, errno.ELOOP,
    errno.ENAMETOOLONG,
})


def session_info_failure(e: BaseException) -> str:
    """The "Session info failed: <cause>." status for `e`: a plain str when
    the error says the path is unusable, a TransientSessionInfoFailure
    otherwise. The wording is a routing contract; see the backstop note in
    update_session_info."""
    status = (
        f"Session info failed: {failure_cause(e)}. "
        "The Current Session block in CLAUDE.md is now stale."
    )
    if isinstance(e, OSError) and e.errno in _PATH_PRECONDITION_ERRNOS:
        return status
    return TransientSessionInfoFailure(status)


# Case 2's legacy anchor, used only in a file with no memory block: the
# heading row itself, at column 0. A mention mid-line, a fenced or indented
# copy and a commented-out heading are not it.
_RETRIEVED_CONTEXT_HEADING = re.compile(r"## Retrieved Context\s*$")
# The previous session's directory line, and its value on a row already found.
_SESSION_DIR_ROW_RE = re.compile(r"- Session dir:\s*`[^`]+`")
_BACKTICK_VALUE_RE = re.compile(r"`([^`]+)`")


def _content_span(doc: Document, first: int, last: int) -> tuple[int, int]:
    """(start, end) in the original text of rows first..last, without the
    last row's terminator, which stays put. Document.offsets() would take the
    U+FEFF and the terminator with the rows."""
    return doc.row_start(first), doc.row_start(last) + len(doc.lines[last].content)


def _session_block_refusal(reason: str) -> str:
    """The status for a file whose Current Session block PACT will not rewrite.
    The word `skipped` routes it to the user-visible surface (see the routing
    note in update_session_info)."""
    return (
        f"Session info skipped: {reason}. The Current Session block in "
        "CLAUDE.md was left unchanged and is now stale."
    )


def _session_block_text(
    session_id: str,
    team_name: str,
    session_dir: str | None,
    plugin_root: str | None,
    timestamp: str,
) -> str:
    """The Current Session block, markers included, for these values. The one
    place the block's text is spelled: the file-creation path and the planner
    both call it."""
    # Build session dir line. MUST be written as an absolute path — command
    # files read this value via bash single-quoted expansion which does NOT
    # perform tilde expansion, and `session_journal._validate_cli_session_dir`
    # rejects non-absolute paths via `Path(session_dir).is_absolute()`. A
    # tilde-abbreviated path would break every journal write from command
    # files (R4 regression). Mirrors `plugin_root` below.
    #
    # SANITIZED HERE, AT THE VALUE, AND NOT AT THE LINE BELOW. The two
    # `*_line` variables each END WITH "\n", and that newline is the list
    # separator. `_PROMPT_CONTROL_CHARS_RE` covers "\n", so a sanitize call
    # wrapped around the assembled LINE would collapse its own separator and
    # merge three bullets into one. Sanitize the VALUE, keep the separator.
    # A newline inside the value is still stripped, because the value is
    # what the call receives.
    session_dir_line = ""
    if session_dir:
        cleaned_session_dir = _sanitize_prompt_field(
            str(session_dir), _REFRESH_PATH_TRUNCATION_LIMIT
        )
        session_dir_line = f"- Session dir: `{cleaned_session_dir}`\n"

    # Build plugin root line (no abbreviation — needs to be usable as-is in Bash)
    # The model pastes this value into double-quoted shell commands, so an
    # unsafe one (is_safe_plugin_root) drops the line, as an empty one does.
    plugin_root_line = ""
    if is_safe_plugin_root(plugin_root):
        cleaned_plugin_root = _sanitize_prompt_field(
            str(plugin_root), _REFRESH_PATH_TRUNCATION_LIMIT
        )
        plugin_root_line = f"- Plugin root: `{cleaned_plugin_root}`\n"

    # SANITIZED AT THE VALUE, like the two path values above, and for the same
    # cause: the newline after each bullet is the LIST SEPARATOR, so a call
    # wrapped around an assembled line would collapse its own separator.
    # These two are FREE TEXT by the repo classification, so they take the
    # tight bound rather than the path bound.
    #
    # THE BACKTICK WRAP BELOW IS NOT A GUARD. Inline code in markdown does not
    # span a line break, so a newline in one of these values breaks OUT of the
    # backtick span and reaches the managed region as a line of its own.
    cleaned_session_id = _sanitize_prompt_field(str(session_id))
    cleaned_team_name = _sanitize_prompt_field(str(team_name))

    return (
        f"{SESSION_START_MARKER}\n"
        f"## Current Session\n"
        f"<!-- Auto-managed by session_init hook. Overwritten each session. -->\n"
        f"- Resume: `claude --agent PACT:pact-orchestrator --resume {cleaned_session_id}`\n"
        f"- Team: `{cleaned_team_name}`\n"
        f"{session_dir_line}"
        f"{plugin_root_line}"
        f"- Started: {timestamp}\n"
        f"{SESSION_END_MARKER}"
    )


def _plan_session_block(
    content: str,
    session_id: str,
    team_name: str,
    session_dir: str | None,
    plugin_root: str | None,
    timestamp: str,
) -> tuple[str | None, str | None]:
    """Plan the Current Session write for `content`: (new_content, status).

    Pure, so a file that is not valid UTF-8 gets the same plan from its
    replace-decoded copy. new_content is None when nothing is written: the
    block already holds these values (status None), or the file is left
    alone and status says why. Every location comes from the parser, so a
    fenced, indented or quoted copy of a marker or heading is never written.
    """
    from shared.claude_md_markers import State, parse

    session_block = _session_block_text(
        session_id, team_name, session_dir, plugin_root, timestamp
    )
    doc = parse(content)
    block = doc.find_block(SESSION_START_MARKER, SESSION_END_MARKER)
    if block.state is State.FOUND:
        start, end = _content_span(doc, *block.spans[0])
        new_content = content[:start] + session_block + content[end:]
        if new_content == content:
            return None, None
        status = "Session info updated in project CLAUDE.md"
    elif block.state is State.ABSENT:
        at, refusal = _session_block_insertion(doc)
        if refusal is not None:
            return None, _session_block_refusal(refusal)
        if at is None:
            # No anchor: append at end of file.
            base = content if content.endswith("\n") else content + "\n"
            start = len(base) + 1
            new_content = base + "\n" + session_block + "\n"
        else:
            start = at
            new_content = content[:at] + session_block + "\n\n" + content[at:]
        status = "Session info added to project CLAUDE.md"
    else:
        return None, _session_block_refusal(block.reason)
    # The write must read back as one block, exactly where it was put.
    written = parse(new_content)
    check = written.find_block(SESSION_START_MARKER, SESSION_END_MARKER)
    if check.state is State.FOUND and (
        _content_span(written, *check.spans[0]) == (start, start + len(session_block))
    ):
        return new_content, status
    written_line = sum(1 for line in written.lines if line.start <= start)
    if check.reason:
        where = check.reason
    elif check.state is State.FOUND:
        where = (f"it reads back at line {check.spans[0][0] + 1}, "
                 f"not line {written_line} where it was written")
    else:
        where = f"no block reads back at line {written_line}, where it was written"
    return None, _session_block_refusal(
        "the rewritten Current Session block did not read back as one block "
        f"where it was written: {where}")


def _session_block_insertion(doc: Document) -> tuple[int | None, str | None]:
    """Where a new Current Session block goes: (offset or None for end of file,
    refusal reason or None).

    The block is never inside PACT_MEMORY. (a) With the managed and memory
    start markers found, it goes before the memory start marker's row. (b) A
    memory block with no managed region: before its start row. (c) No memory
    block, the legacy shape: before a column-0 `## Retrieved Context`
    heading. (d) Otherwise, at end of file. A marker or heading the parser
    cannot place makes the writer refuse.
    """
    from shared.claude_md_markers import State

    managed = doc.find_marker(MANAGED_START_MARKER)
    memory_start = doc.find_marker(MEMORY_START_MARKER)
    for located in (managed, memory_start):
        if located.state in (State.DUPLICATE, State.MALFORMED):
            return None, located.reason
    if managed.state is State.FOUND and memory_start.state is State.FOUND:
        return doc.row_start(memory_start.spans[0][0]), None
    memory = doc.find_block(MEMORY_START_MARKER, MEMORY_END_MARKER)
    if memory.state is State.FOUND:
        return doc.row_start(memory.spans[0][0]), None
    if memory.state is not State.ABSENT:
        return None, memory.reason
    heading = doc.find_section(_RETRIEVED_CONTEXT_HEADING, None)
    if heading.state is State.FOUND:
        return doc.row_start(heading.spans[0][0]), None
    if heading.state is State.ABSENT:
        return None, None
    return None, heading.reason


def update_session_info(
    session_id: str,
    team_name: str,
    session_dir: str | None = None,
    plugin_root: str | None = None,
    started: str | None = None,
) -> str | None:
    """
    Write the Current Session section to the project's CLAUDE.md.

    Inserts (or overwrites) a managed section containing the session resume
    command, team name, session directory, plugin root, and start timestamp.
    Uses <!-- SESSION_START --> / <!-- SESSION_END --> comment markers for
    reliable replacement across sessions.

    Args:
        session_id: Full session UUID (e.g. "93cf3da0-c792-4daa-888e-...")
        team_name: Generated team name (e.g. "PACT-93cf3da0")
        session_dir: Absolute path to the session directory (optional).
            When provided, written as "- Session dir:" line for next-session
            journal access.
        plugin_root: Absolute path to the installed plugin directory (optional).
            When provided, written as "- Plugin root:" line so the orchestrator
            can locate hook scripts without symlink traversal.
        started: The "Started" value to write; None writes now. A compaction is
            not a session start, so session_init passes the value already there.

    Returns:
        Status message or None if no action taken. A failure that may clear on
        its own returns a TransientSessionInfoFailure.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        return None

    # Honor both supported project CLAUDE.md locations.
    # Existing files take precedence (.claude/CLAUDE.md > legacy ./CLAUDE.md);
    # if neither exists, the resolver returns the new default
    # ($project_dir/.claude/CLAUDE.md) so we create at the preferred path.
    target_file, _source = resolve_project_claude_md_path(project_dir)

    timestamp = started or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Create the `.claude/` parent directory (with 0o700) BEFORE acquiring
    # the file lock. `file_lock` internally creates the target's parent
    # directory as a side effect of opening the sidecar lock file, but it
    # uses `mkdir(parents=True, exist_ok=True)` with no explicit mode —
    # which defaults to 0o755 under umask. Running `ensure_dot_claude_parent`
    # first guarantees the parent gets the intended 0o700 mode. The call is
    # idempotent, so concurrent callers are safe: whichever thread creates
    # the directory wins with 0o700, others see `parent.exists()` and no-op.
    ensure_dot_claude_parent(target_file)

    # Concurrency guard: serialize read-mutate-write so two concurrent
    # session_init hooks on the same project CLAUDE.md cannot interleave
    # update_session_info writes and clobber each other's managed blocks.
    # Fail-open on timeout — next session start will retry.
    try:
        with file_lock(target_file):
            # #1247: containment (in _atomic_write_text) REPLACES the former
            # leaf is_symlink guard -- inside the lock (TOCTOU-safe). It
            # catches the symlinked-PARENT escape the leaf guard MISSED (F1)
            # and safely ALLOWS a benign in-project leaf redirect; it does NOT
            # dominate is_symlink (overlapping-but-different sets).
            try:
                # Case 0: File doesn't exist -- create it with the full canonical
                # PACT_MANAGED structure so the orchestrator has a stable Current
                # Session block AND a ready PACT_MEMORY container on the very first
                # session in a project. The .claude/ parent was created above
                # (before the lock) with mode 0o700.
                #
                # Structure mirrors `ensure_project_memory_md`'s template — single
                # H1 ("# PACT Framework and Managed Project Memory"), session
                # block, PACT_MEMORY with three default section headings, all
                # wrapped by the PACT_MANAGED outer boundary.
                if not target_file.exists():
                    session_block = _session_block_text(
                        session_id, team_name, session_dir, plugin_root, timestamp
                    )
                    new_content = (
                        f"{MANAGED_START_MARKER}\n"
                        f"{MANAGED_TITLE}\n"
                        "\n"
                        f"{session_block}\n"
                        "\n"
                        f"{MEMORY_START_MARKER}\n"
                        "## Retrieved Context\n"
                        f"{RETRIEVED_CONTEXT_COMMENT}\n"
                        "\n"
                        "## Pinned Context\n"
                        "\n"
                        "## Working Memory\n"
                        f"{WORKING_MEMORY_COMMENT}\n"
                        f"{MEMORY_END_MARKER}\n"
                        "\n"
                        f"{MANAGED_END_MARKER}\n"
                    )
                    _atomic_write_text(target_file, new_content, Path(project_dir))
                    return "Session info created in new project CLAUDE.md"

                content = target_file.read_text(encoding="utf-8")

                # Cases 1 and 2: the planner locates the block, and its
                # insertion point, through the fence-aware parser. FOUND is
                # replaced by plain string slicing, so nothing in session_block
                # is interpreted: a backslash in a directory name stays a
                # backslash. ABSENT gets one fresh block. A block the parser
                # cannot place (a duplicate, a stray marker, an uncertain
                # region) is left alone and the status names the line.
                new_content, status = _plan_session_block(
                    content, session_id, team_name, session_dir, plugin_root, timestamp
                )
                if new_content is not None:
                    _atomic_write_text(target_file, new_content, Path(project_dir))
                return status

            except ContainmentError:
                # Opaque skip, matching the removed is_symlink guard's message.
                return "Session info skipped: path precondition not met."
            except UnicodeDecodeError:
                # The file is rewritten here, so it is decoded strictly and
                # left untouched, never rewritten with replacement characters.
                # The planner runs on the replace-decoded copy: a plan that
                # writes nothing (the block already equals this one, as on a
                # compaction) is no skip to report.
                new_content, status = _plan_session_block(
                    _read_replaced(target_file), session_id, team_name,
                    session_dir, plugin_root, timestamp,
                )
                if new_content is None and status is None:
                    return None
                return (
                    "Session info skipped: the project CLAUDE.md is not valid "
                    "UTF-8, so it was left unchanged. The Current Session block "
                    "in CLAUDE.md is now stale."
                )
            except Exception as e:
                # WHAT THIS HANDLER COVERS, WRITTEN DOWN BECAUSE ONE OF ITS
                # CAUSES WAS REMOVED AND A HANDLER THAT LOOKS THE SAME AFTER
                # ITS CAUSE GOES IS THE SHAPE THAT ROTS.
                #
                # IT USED TO CATCH `re.error` FROM A REGEX SUBSTITUTION,
                # raised when a caller-influenced value spelled an invalid
                # escape such as `\d`. THAT CAUSE IS GONE: the block is now
                # spliced by string slicing, and the parser never raises on a
                # str.
                #
                # IT IS NOT DEAD COVER. WHAT REMAINS UNDER IT IS THE FILE
                # LAYER, on the read path and the write path inside the lock:
                # `Path.exists` and `read_text` (OSError, and
                # UnicodeDecodeError for a CLAUDE.md that is not valid UTF-8),
                # and `_atomic_write_text` (OSError, UnicodeEncodeError).
                # `ContainmentError` is handled above and does not reach here.
                # So this stays a fail-open
                # I/O backstop: one unreadable or unwritable file degrades the
                # session block, and it does not take down SessionStart.
                #
                # KEEP THE `Session info failed: ` PREFIX BYTE-IDENTICAL.
                # THE WORD `failed` IS A MACHINE CONTRACT. session_init.py
                # step 5b (line 1589) routes this return with
                # `if "failed" in session_msg.lower() or "skipped" in ...`:
                # a hit goes to system_messages (the user-visible error
                # surface), a miss goes to context_parts. A REWORDED PREFIX
                # KEEPS THE HUMAN SIGNAL AND SILENTLY DOWNGRADES THE
                # ROUTING. The same predicate appears at five other call
                # sites for other producers; this handler feeds only 1589.
                #
                # THE CAUSE TOKEN IS A CLOSED VOCABULARY (see
                # shared/failure_cause.py, which the five sibling routed
                # producers share). The caller's message is not read: it can
                # carry a path with no filename attribute behind it, so a
                # filename filter or a length bound does not remove the
                # path. A length bound is worse than it looks -- it keeps
                # the LEADING characters, which is where the absolute path
                # sits.
                #
                # THE SECOND SENTENCE NAMES THE CONSEQUENCE. No other
                # message in this function tells the user that the Current
                # Session block stopped updating, which is the failure a
                # later session inherits when it reads the stale pointer.
                return session_info_failure(e)
    except TimeoutError:
        return TransientSessionInfoFailure(
            "Failed to acquire lock on project CLAUDE.md within 5s "
            "(another session_init hook may be running concurrently). "
            "Session info update skipped; will retry on next session start."
        )
    except OSError as e:
        # #1245: lock ACQUISITION PermissionError escapes `except TimeoutError`;
        # catch it at the same skip-and-retry level (the inner except Exception
        # handles only post-acquisition failures, inside the `with file_lock`).
        # Opaque, matching the sibling TimeoutError message -- no path leak.
        status = (
            "Could not acquire lock on project CLAUDE.md "
            "(path precondition not met); session info update skipped."
        )
        if e.errno in _PATH_PRECONDITION_ERRNOS:
            return status
        return TransientSessionInfoFailure(status)


# The "Started" line update_session_info writes, in its exact timestamp shape,
# and its value on a row already found.
_SESSION_STARTED_RE = re.compile(
    r"^- Started: (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)$", re.MULTILINE
)
_STARTED_VALUE_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC$")


def read_session_started(project_dir: str) -> str | None:
    """The "Started" value in the project CLAUDE.md's Current Session block.

    Pass it as update_session_info's ``started`` to rewrite the block without
    moving the session's start. None when there is no CLAUDE.md, it cannot be
    read, or the line is not in a Current Session block the parser finds (a
    copy in a fenced example is not read). Only the exact shape
    update_session_info writes is returned. Read without the sidecar lock:
    every writer replaces the file whole. Never raises.
    """
    try:
        if not project_dir:
            return None
        claude_md, source = resolve_project_claude_md_path(project_dir)
        if source == "new_default":
            return None
        # Read-only, and the pattern takes ASCII digits only, so a replaced
        # byte cannot reach the block.
        content = claude_md.read_text(encoding="utf-8", errors="replace")
        rows = session_block_rows(content, _SESSION_STARTED_RE)
        match = _STARTED_VALUE_RE.search(rows[0]) if rows else None
        return match.group(0) if match else None
    except Exception:
        return None


def _validate_under_pact_sessions(path: str) -> str | None:
    """Reject extracted session paths that escape the pact-sessions root.

    Defense-in-depth against tampered CLAUDE.md content. The Session dir / Resume
    lines are user-editable text, so a malicious or accidentally corrupted file
    could point _extract_prev_session_dir at any filesystem location (e.g.
    /etc, /var, a sibling project's secrets). Callers consume the returned path
    to read journal events; an attacker who controlled the path could exfiltrate
    or trigger reads outside the PACT sessions tree.

    The check calls ``Path.resolve(strict=False)`` on both the candidate AND the
    sessions root so ``..`` segments are collapsed and symlinks followed before
    the containment check. A naive string-prefix comparison against
    ``str(Path(path))`` is NOT sufficient: ``Path()`` normalizes redundant
    slashes but leaves ``..`` segments intact, so ``~/.claude/pact-sessions/../../etc/passwd``
    would textually start with the prefix yet resolve outside the tree once the
    filesystem is asked to dereference it. ``resolve(strict=False)`` does the
    canonicalization explicitly and does NOT require the path to exist.

    The containment check uses ``Path`` comparison semantics
    (``candidate == sessions_root or sessions_root in candidate.parents``)
    instead of string prefix + ``os.sep``. This eliminates the sibling-prefix
    collision class (``pact-sessions-evil`` vs ``pact-sessions``) by design,
    rather than relying on an explicit separator guard.

    Returns the original string on success and None on rejection (silent
    fail-closed — callers already treat None as "no previous session").
    """
    try:
        sessions_root = (get_claude_config_dir() / "pact-sessions").resolve()
        candidate = Path(path).resolve(strict=False)
        if candidate == sessions_root or sessions_root in candidate.parents:
            return path
    except (TypeError, ValueError, OSError):
        pass
    return None


def _extract_prev_session_dir(project_dir: str) -> str | None:
    """
    Extract the previous session's directory path from the project CLAUDE.md.

    Reads the "## Current Session" block written by update_session_info()
    and extracts the session dir from lines like
    "- Session dir: `~/.claude/pact-sessions/PACT-Plugin/abc12345-...`".

    Honors both supported project CLAUDE.md locations
    ($project_dir/.claude/CLAUDE.md preferred, $project_dir/CLAUDE.md legacy).

    Falls back to deriving the path from the Resume line's session_id +
    the resolved project slug if the Session dir line is absent (backward
    compat with sessions that wrote team name but not session dir) or names
    a directory that is no longer there (the line was written before the
    directory moved to the resolved slug).

    Both extracted paths (primary and fallback) are validated against the
    canonical pact-sessions prefix via _validate_under_pact_sessions before
    being returned. Defense-in-depth against tampered CLAUDE.md content.

    This is used to locate the previous session's journal for resume context
    and pause state detection. Returns None if neither CLAUDE.md exists, the
    session dir can't be extracted, or the extracted path is outside the
    pact-sessions tree.

    Args:
        project_dir: CLAUDE_PROJECT_DIR path

    Returns:
        Previous session directory path string, or None if not found
    """
    if not project_dir:
        return None

    try:
        claude_md, source = resolve_project_claude_md_path(project_dir)
        # source == "new_default" means neither location exists -- nothing to read
        if source == "new_default":
            return None

        # No lock. Every writer of the project CLAUDE.md replaces it whole
        # (_atomic_write_text: a temp file, then os.replace), so this read
        # sees the old file or the new one and never a torn one. Taking the
        # sidecar lock would only create a `.CLAUDE.md.lock` beside the file
        # for frames that never write it (a teammate, or input that did not
        # parse). Read-only, so a byte that is not UTF-8 decodes to U+FFFD;
        # the returned path is validated below.
        content = claude_md.read_text(encoding="utf-8", errors="replace")

        # Both lines are read only inside the Current Session block, through
        # the parser: no block, an uncertain one, or a copy in a fenced
        # example names no previous session.
        dir_rows = session_block_rows(content, _SESSION_DIR_ROW_RE)
        if dir_rows is None:
            return None

        # Primary: the "- Session dir: `<path>`" line.
        match = _BACKTICK_VALUE_RE.search(dir_rows[0]) if dir_rows else None
        if match:
            raw = match.group(1)
            # Expand ~ to actual home directory
            if raw.startswith("~/"):
                expanded = str(Path.home() / raw[2:])
            else:
                expanded = raw
            validated = _validate_under_pact_sessions(expanded)
            # A validated line naming a directory that is gone falls through
            # to the derivation below; a rejected line still returns None.
            if validated is None or Path(validated).is_dir():
                return validated
        else:
            # The primary regex missed even though CLAUDE.md is on disk. This
            # is usually benign (older sessions wrote only the Resume line,
            # not the Session dir line — handled by the fallback just below),
            # but it is also how a silent format regression would present.
            # Log a one-line stderr warning so future drift in the
            # SESSION_START block surfaces during testing instead of silently
            # degrading to the fallback.
            print(
                "session_resume: _extract_prev_session_dir regex failed on "
                "existing CLAUDE.md, falling back to Resume-line; file may "
                "have unexpected format",
                file=sys.stderr,
            )

        # Fallback: derive from Resume line session_id + project root basename.
        # The Resume pattern reads both the current line and one written
        # before the `--agent` flag was added.
        session_id = recorded_session_id(content)
        if session_id:
            # Same slug derivation and sanitisation as every session path,
            # so the fallback lands on the directory the writers used.
            derived = str(
                _build_session_path(project_slug(project_dir), session_id)
            )
            return _validate_under_pact_sessions(derived)

    except (IOError, OSError):
        pass
    return None


# Team names longer than this are not substituted. Real ones are
# session-<id8> (16), a full session UUID (36) or pact-<id8>.
TEAM_NAME_MAX_LENGTH = 128


def _safe_team_name(value: object) -> str:
    """The team name if it is a safe single path component that is not all
    dashes and at most TEAM_NAME_MAX_LENGTH characters, else ""."""
    if (
        isinstance(value, str)
        and is_safe_path_component(value)
        and value.strip("-")
        and len(value) <= TEAM_NAME_MAX_LENGTH
    ):
        return value
    return ""


def _safe_plugin_root(value: object) -> str:
    """The plugin root if it passes is_safe_plugin_root, else ""."""
    return value if is_safe_plugin_root(value) else ""


def _is_set(value: object) -> bool:
    """True when a placeholder value was supplied at all: not None and not
    an empty string. Separates "not set" from "set but failed the check" in
    the unavailable wording of format_session_substitutions."""
    return value is not None and value != ""


def format_session_substitutions(
    team_name: str,
    session_dir: str,
    plugin_root: str,
    lead_address: str,
) -> str:
    """The sentence telling the orchestrator which values replace the
    {team_name}, {session_dir}, {plugin_root} and {lead_address} placeholders
    in commands, spawn prompts and task text.

    Every value is checked before it reaches this LLM-facing sentence:

    - ``team_name`` must be a safe single path component
      (``is_safe_path_component``), not all dashes, and at most
      TEAM_NAME_MAX_LENGTH characters; ``plugin_root`` must pass
      ``is_safe_plugin_root`` (a positive allowlist, because the model pastes
      it into double-quoted shell commands). A value that fails is named as
      unavailable rather than substituted, and the sentence says why: "not
      set" for an empty value, "not a safe ..." for a value that failed the
      check. An empty ``session_dir`` (no session id or project dir) is named
      as not set the same way.
    - An unavailable ``plugin_root`` points the model at bootstrap.md's
      fallbacks (CLAUDE.md, then pact-session-context.json, then the
      Last-resort fallback for {plugin_root} symlink traversal), which
      resolve to safe values. The sentence carries no quote characters, so
      it passes the same marker checks as a substituted value. An unavailable team name or session dir has no
      safe fallback, so commands that depend on it wait for a clean start.
    - ``lead_address`` is required (no default: a silent ``team-lead``
      default would misroute a cloud session, #1666 item 1) and allowlisted.
      Any value outside LEAD_ADDRESSES (a non-str one included, checked first
      so an unhashable value cannot raise) is replaced by ``team-lead``, so
      only one of two fixed strings reaches the sentence.
    """
    if not isinstance(lead_address, str) or lead_address not in LEAD_ADDRESSES:
        lead_address = LEAD_ADDRESS_LOCAL
    team_was_set = _is_set(team_name)
    plugin_root_was_set = _is_set(plugin_root)
    team_name = _safe_team_name(team_name)
    plugin_root = _safe_plugin_root(plugin_root)

    if team_name:
        team_sentence = (
            f'Use the name `{team_name}` wherever {{team_name}} appears in commands. '
        )
    else:
        reason = 'not a safe name' if team_was_set else 'not set'
        team_sentence = (
            f'Team name unavailable ({reason}) — do not run commands that '
            'depend on {team_name} until next clean start. '
        )
    if session_dir:
        session_sentence = (
            f'Use `{session_dir}` wherever {{session_dir}} appears in commands. '
        )
    else:
        session_sentence = (
            'Session dir unavailable (not set: no session id or project dir) — '
            'do not run commands that depend on {session_dir} until next clean start. '
        )
    if plugin_root:
        plugin_sentence = (
            f'Use `{plugin_root}` wherever {{plugin_root}} appears in commands.'
        )
    else:
        reason = 'not a safe absolute path' if plugin_root_was_set else 'not set'
        plugin_sentence = (
            f'Plugin root unavailable ({reason}) — resolve {{plugin_root}} '
            'through the fallbacks in bootstrap.md, ending with its '
            'Last-resort fallback for {plugin_root}.'
        )
    lead_sentence = (
        f' Use `{lead_address}` wherever {{lead_address}} appears in spawn '
        f'prompts and task text.'
    )
    return (
        'Session placeholder variables (substitute before running commands): '
        + team_sentence
        + session_sentence
        + plugin_sentence
        + lead_sentence
    )


def restore_last_session(
    prev_session_dir: str | None = None,
) -> str | None:
    """
    Restore the last session context for cross-session continuity.

    Reads the previous session's journal (located by prev_session_dir) and
    constructs a resume summary from agent_handoff, phase_transition, and
    checkpoint events.

    Args:
        prev_session_dir: Previous session's directory path (from CLAUDE.md).
            When provided, reads that session's journal for resume context.

    Returns:
        Resume context string if available, None otherwise
    """
    if not prev_session_dir:
        return None

    return _build_journal_resume(prev_session_dir)


# Outcomes of append_team_realigned.
REALIGN_RECORDED = "recorded"
REALIGN_ALREADY_RECORDED = "already-recorded"
REALIGN_NOT_A_SPLIT = "not-a-split"
REALIGN_FAILED = "failed"

_REALIGN_LOCK_NAME = ".team-realigned.lock"


def _team_existed(team: str) -> bool:
    """True when the team has a config or a task store. A split's old team
    keeps its task store after the platform deletes its config; a name the
    platform never created has neither. Never raises."""
    try:
        if not is_safe_path_component(team):
            return False
        root = get_claude_config_dir()
        return (root / "teams" / team / "config.json").exists() or (
            root / "tasks" / team
        ).is_dir()
    except Exception:
        return False


def append_team_realigned(
    old_team: str, new_team: str, *, session_dir: str | None = None,
) -> str:
    """Append team_realigned {from, to} for a move of this session's team.

    Returns REALIGN_RECORDED when the event was appended,
    REALIGN_ALREADY_RECORDED when the journal's latest team_realigned already
    holds this move, REALIGN_NOT_A_SPLIT when the old team never existed (a
    computed name the platform did not use), or REALIGN_FAILED when the
    append did not land.

    Two processes can make the same move at once (a lead prompt and a
    teammate's compaction both read the old record before either writes).
    The check and the append run under an flock in the session dir, so only
    one of them appends; a later move back and forth still differs from the
    latest event and is recorded. A lock that cannot be taken is skipped and
    the append proceeds. Never raises.
    """
    try:
        if not _team_existed(old_team):
            return REALIGN_NOT_A_SPLIT
        target_dir = session_dir or get_session_dir()
        if not target_dir:
            return REALIGN_FAILED
        lock = None
        try:
            Path(target_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
            # O_NOFOLLOW: a symlink at the lock path is refused (ELOOP), never
            # followed to create or open its target.
            lock = os.open(
                Path(target_dir) / _REALIGN_LOCK_NAME,
                os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW,
                0o600,
            )
            fcntl.flock(lock, fcntl.LOCK_EX)
        except OSError:
            pass  # fail open: the append below still runs
        try:
            latest = read_last_event_from(target_dir, "team_realigned")
            if latest and latest.get("from") == old_team and latest.get("to") == new_team:
                return REALIGN_ALREADY_RECORDED
            event = make_event("team_realigned", **{"from": old_team, "to": new_team})
            if append_event(event, session_dir=target_dir):
                return REALIGN_RECORDED
            return REALIGN_FAILED
        finally:
            if lock is not None:
                os.close(lock)  # closing releases the flock
    except Exception:
        return REALIGN_FAILED


def record_team_realign(
    old_team: str,
    new_team: str,
    *,
    session_dir: str | None = None,
    notify_if_already_recorded: bool = False,
) -> str | None:
    """Record a move of this session's team and return the lead notice.

    Both hooks that move the recorded team call it: the prompt-time
    write-back and session_init when a launch realigns. Returns None when the
    old team never existed, so there is no split to recover from. When the
    journal cannot take the event, the notice says so and names the old
    team, which recovery would otherwise read from that event.

    When another process already recorded this move (a teammate's compaction
    racing the lead's prompt), only a caller passing
    notify_if_already_recorded gets the notice. The lead's prompt-time
    write-back passes it, so the lead keeps its notice; a SessionStart that
    lost the race gets none. Never raises.
    """
    outcome = append_team_realigned(old_team, new_team, session_dir=session_dir)
    if outcome == REALIGN_NOT_A_SPLIT:
        return None
    if outcome == REALIGN_ALREADY_RECORDED and not notify_if_already_recorded:
        return None
    if outcome == REALIGN_FAILED:
        print(
            "session_resume: could not record team_realigned "
            f"{old_team} -> {new_team} in the session journal",
            file=sys.stderr,
        )
        return (
            f"PACT moved this session's team from `{old_team}` to `{new_team}` "
            f"but could not record the move in the session journal. "
            f"`TaskList` shows only `{new_team}`; in-progress tasks may remain in "
            f"`tasks/{old_team}/`. Use `{old_team}` as the earlier team in the "
            f"team-split recovery."
        )
    # Task ids come from task files: sanitised as the resume claim's are.
    ids = ", ".join(
        clean for clean in map(_sanitize_prompt_field, in_progress_task_ids(old_team))
        if clean
    ) or "none"
    return (
        f"PACT moved this session from team `{old_team}` to `{new_team}`, "
        f"the team the platform is using. `TaskList` shows only `{new_team}`. "
        f"In-progress tasks still in `tasks/{old_team}/`: {ids}. "
        f"Follow the team-split recovery in `pact-state-recovery.md` "
        f"before spawning any specialist."
    )


def in_progress_task_ids(team_name: str) -> list[str]:
    """Ids of the in-progress tasks in tasks/<team_name>/, shortest first so
    numeric ids sort numerically. Empty on any error or an unsafe name.
    """
    ids = [
        str(task.get("id"))
        for task in iter_team_task_jsons(team_name)
        if task.get("status") == "in_progress" and task.get("id") is not None
    ]
    return sorted(ids, key=lambda i: (len(i), i))


def _coerce_decision_summary(decisions: Any) -> str:
    """
    Extract a short summary string from a handoff's `decisions` field.

    The `decisions` field is nominally a list of strings, but historical
    journal data and future schema drift can produce:
    - non-list values (dict, None, scalar)
    - empty lists
    - lists whose first element is not a string (dict, list, None)

    This helper returns an empty string for any of those shapes rather
    than raising IndexError/TypeError. When the first element is a
    non-string, it is stringified via str() so callers still get a
    useful, bounded summary.

    Truncation to _DECISION_TRUNCATION_LIMIT happens here so every caller
    gets consistent behavior.
    """
    if not isinstance(decisions, list) or not decisions:
        return ""
    first = decisions[0]
    if isinstance(first, str):
        summary = first
    elif first is None:
        return ""
    else:
        # Best-effort stringify for dict/list/number/other — bounded by
        # truncation below so even a giant dict becomes a readable stub.
        summary = str(first)
    if len(summary) > _DECISION_TRUNCATION_LIMIT:
        summary = summary[:_DECISION_TRUNCATION_LIMIT - 3] + "..."
    return summary


def _coerce_phase_string(phase: Any) -> str:
    """
    Stringify and bound a `phase` value drawn from a phase_transition event.

    Parallel to `_coerce_decision_summary`: the per-type validator rejects
    new writes that lack `phase`, but the defensive consumer backstop must
    still handle:
    - non-string phase values from older schema versions or hand-crafted
      journal files (dict, list, None, number)
    - pathologically long strings from a misconfigured writer that stashed
      a whole error message in `phase`

    None is handled explicitly (returns ``""``), matching
    ``_coerce_decision_summary``'s convention. Other non-string values
    are stringified via ``str()`` and truncated at
    ``_PHASE_TRUNCATION_LIMIT`` so a bad event can produce at worst a
    readable 80-character stub in the resume output instead of flooding
    the SessionStart hook context or raising a TypeError downstream.
    """
    if phase is None:
        return ""
    rendered = str(phase)
    if len(rendered) > _PHASE_TRUNCATION_LIMIT:
        rendered = rendered[:_PHASE_TRUNCATION_LIMIT - 3] + "..."
    return rendered


def _build_journal_resume(session_dir: str) -> str | None:
    """
    Build resume context from a previous session's journal events.

    Reads agent_handoff events (completed work), phase_transition events
    (progress), and checkpoint events (state snapshot) to produce a
    concise resume summary.

    Defensive consumer: this function MUST NOT raise on malformed events.
    Any KeyError/IndexError/TypeError propagates through restore_last_session
    into session_init.main()'s outer except, which replaces the entire
    constructed hook output dict (team-create instructions, working memory,
    retrieved context) with an error JSON — losing critical SessionStart
    context for one bad journal line. Per-type schema validation at write
    time (see session_journal._validate_event_schema) is the primary
    defense; this consumer is the backstop for events that slipped past
    an older validator, prior schema versions, or hand-crafted files.

    Failure mode: on any unexpected exception, log to stderr and return
    None so the caller continues with an empty resume.

    Args:
        session_dir: The previous session's directory path

    Returns:
        Formatted resume string, or None if journal is empty/missing/unreadable
    """
    try:
        return _build_journal_resume_inner(session_dir)
    except Exception as e:
        # Last-resort catch so one malformed event cannot nuke session_init's
        # hook output. Log so the bug is visible but fail-open.
        print(
            f"session_resume: _build_journal_resume failed "
            f"(fail-open, returning None): {e}",
            file=sys.stderr,
        )
        return None


def _build_journal_resume_inner(session_dir: str) -> str | None:
    """
    Inner implementation of _build_journal_resume.

    Separated so the outer wrapper can catch any unexpected exception
    without cluttering the main flow. Each field access uses `.get()`
    with safe defaults so normal missing-field cases don't raise —
    the outer try/except is defense-in-depth for unforeseen shapes.
    """
    all_events = read_events_from(session_dir)
    if not all_events:
        return None

    lines = ["Previous session summary (from journal -- read-only reference):", ""]

    # Extract completed handoffs. Every field access is guarded with
    # .get() and type checks — `decisions[0]` is the single historical
    # crash site (BugF1 secondary), now funneled through the helper.
    handoffs = [e for e in all_events if e.get("type") == "agent_handoff"]
    # agent_handoff is a MULTI-EVENT family: one task emits one event for
    # each DISTINCT handoff content, so a revised HANDOFF reaches the journal
    # adjacent to the copy it replaced. Rendering one bullet for each event shows
    # the superseded copy and the current one together, indistinguishable.
    # THIS IS A CONSUMER OF THE SELECTION RULE, NOT A SECOND STATEMENT OF IT.
    # The rule is authored in skills/pact-handoff-harvest/SKILL.md, Step 3,
    # in the SELECTION block. Read it there. If it changes, change this WITH it: the
    # two are coupled and nothing compares them.
    latest = {}
    for h in handoffs:
        key = (h.get("agent", "unknown"), h.get("task_subject", ""))
        # `>=` keeps the LATER journal line on an equal ts. Same tie-break.
        if key not in latest or h.get("ts", "") >= latest[key].get("ts", ""):
            latest[key] = h
    handoffs = list(latest.values())
    if handoffs:
        lines.append("## Completed Work")
        for h in handoffs:
            agent = h.get("agent", "unknown")
            subject = h.get("task_subject", "")
            handoff_data = h.get("handoff")
            if not isinstance(handoff_data, dict):
                handoff_data = {}
            # resolve_handoff_field reads the canonical key first and falls
            # back to a legacy spelling only when it is absent or falsy. The
            # journal is append-only, so handoffs written under a spelling
            # this repo once taught are on disk permanently; reading them
            # correctly does not teach anyone to write one.
            summary = _coerce_decision_summary(
                resolve_handoff_field(handoff_data, "decisions")
            )
            if summary:
                lines.append(f"- {agent}: {subject} -> {summary}")
            else:
                lines.append(f"- {agent}: {subject}")
        lines.append("")

    # Extract phase progress. Use .get("phase") with a safe default so a
    # malformed phase_transition event (missing `phase`) does not raise
    # KeyError — this is the BugF1 primary crash site. The filter requires
    # `phase` to be a non-empty string so dict/list/number/empty-string
    # shapes from older schema versions do not render as garbled trailers
    # (e.g. "Completed phases: " or "Completed phases: 0").
    #
    # Sort defensively by `ts` so we don't depend on the (currently true
    # but undocumented) chronological-order contract of read_events_from.
    phases = sorted(
        [e for e in all_events if e.get("type") == "phase_transition"],
        key=lambda e: e.get("ts", ""),
    )
    if phases:
        completed = [
            phase
            for p in phases
            if p.get("status") == "completed"
            and (phase := p.get("phase")) and isinstance(phase, str)
        ]

        # Track the latest event per phase name so we only report a phase
        # as "active" if its most recent transition was `started` — a
        # phase that started and then completed should not appear in the
        # active list.
        latest_per_phase: dict[str, tuple[str, str]] = {}
        for p in phases:
            name = p.get("phase")
            status = p.get("status")
            ts = p.get("ts", "")
            if isinstance(name, str) and name and isinstance(status, str):
                prev = latest_per_phase.get(name)
                # Use `>=` so the later-seen event wins on ties: when two
                # events for the same phase share the identical `ts`, the
                # strict `>` comparator would keep the first-seen record
                # and drop the second, causing a
                # `started` + `completed` pair at the same timestamp to
                # leave the phase visible as "active" (BugF2 territory).
                if prev is None or ts >= prev[0]:
                    latest_per_phase[name] = (ts, status)
        # Pick the active phase by max ts among still-started entries.
        # Dict insertion order does not match latest-ts order when multiple
        # phases are concurrently active, so scanning `latest_per_phase` and
        # taking the last insertion (R1 regression) could surface a stale
        # phase on the "Last active phase:" line.
        active_entries = [
            (ts, name)
            for name, (ts, status) in latest_per_phase.items()
            if status == "started"
        ]

        if completed:
            lines.append(
                "Completed phases: "
                + ", ".join(_coerce_phase_string(c) for c in completed)
            )
        if active_entries:
            latest_active = max(active_entries)[1]
            lines.append(
                f"Last active phase: {_coerce_phase_string(latest_active)}"
            )
        lines.append("")

    # Check for warnings in session_end events
    end_events = [e for e in all_events if e.get("type") == "session_end"]
    for end_event in end_events:
        warning = end_event.get("warning")
        if warning:
            lines.append(f"**Warning**: {warning}")
            lines.append("")

    # Minimal output check
    if len(lines) <= 2:
        return None

    return "\n".join(lines)


def check_resumption_context(tasks: list[dict[str, Any]]) -> str | None:
    """
    Check if there are in_progress Tasks indicating work to resume.

    This helps users understand the current state when starting a new session
    with a persistent task list (CLAUDE_CODE_TASK_LIST_ID set).

    Args:
        tasks: List of all tasks

    Returns:
        Status message describing resumption context, or None if nothing to report
    """
    in_progress = [t for t in tasks if t.get("status") == "in_progress"]
    pending = [t for t in tasks if t.get("status") == "pending"]

    if not in_progress and not pending:
        return None

    # Count by type
    feature_tasks = []
    phase_tasks = []
    agent_tasks = []
    blocker_tasks = []

    for task in in_progress:
        subject = task.get("subject", "")
        metadata = task.get("metadata") or {}

        if metadata.get("type") in ("blocker", "algedonic"):
            blocker_tasks.append(task)
        elif any(subject.startswith(p) for p in ("PREPARE:", "ARCHITECT:", "CODE:", "TEST:")):
            phase_tasks.append(task)
        elif any(subject.lower().startswith(p) for p in ("pact-",)):
            agent_tasks.append(task)
        else:
            # Assume it's a feature task
            feature_tasks.append(task)

    parts = []

    if feature_tasks:
        names = [t.get("subject", "unknown")[:30] for t in feature_tasks[:2]]
        if len(feature_tasks) > 2:
            parts.append(f"Features: {', '.join(names)} (+{len(feature_tasks)-2} more)")
        else:
            parts.append(f"Features: {', '.join(names)}")

    if phase_tasks:
        phases = [t.get("subject", "").split(":")[0] for t in phase_tasks]
        parts.append(f"Phases: {', '.join(phases)}")

    if agent_tasks:
        parts.append(f"Active agents: {len(agent_tasks)}")

    if blocker_tasks:
        parts.append(f"**Blockers: {len(blocker_tasks)}**")

    if parts:
        summary = f"Resumption context: {' | '.join(parts)}"
        if pending:
            summary += f" ({len(pending)} pending)"
        return summary

    return None


def check_paused_state(
    prev_session_dir: str | None = None,
) -> str | None:
    """
    Detect paused work from a previous session's /PACT:pause invocation.

    Reads the previous session's journal for session_paused events.
    The event contains pr_number, pr_url, branch, worktree_path,
    consolidation_completed, and team_name.

    Validation pipeline (ordered cheapest-first):
    1. TTL check: timestamp older than 14 days → return informational message
    2. Active PR validation via `gh pr view`: if MERGED/CLOSED → return info

    The journal is immutable — no file deletion is performed.

    Args:
        prev_session_dir: Previous session's directory path (from CLAUDE.md).
            When provided, reads that session's journal for pause state.

    Returns:
        Formatted context string if paused state exists, None otherwise
    """
    if not prev_session_dir:
        return None

    return _check_journal_paused_state(prev_session_dir)


def _check_journal_paused_state(session_dir: str) -> str | None:
    """Check for paused state in the previous session's journal."""
    event = read_last_event_from(session_dir, "session_paused")
    if not event:
        return None
    return _interpret_paused_event(event)


def _compose_pause_key(event: dict) -> str:
    """Compose the ` pause_ts=` consumption key for a session_paused event.

    Single composition point shared by ``_interpret_paused_event`` and
    ``_arbitrate``, so the interpreter and the losing-claim survival path can
    never render the same event's key differently — the same reason
    ``_compose_halt_line`` exists on the refreshed side.

    ONE COMPOSITION POINT BECAUSE EVERY SURFACING PATH NEEDS THE KEY, AND
    ARBITRATION IS ONE OF THEM. Anything that surfaces freezes the Working
    Memory block for that session, and the write bootstrap makes from this
    key is the only thing that retires the claim. The interpreter's three
    prompt branches were covered; the fourth path was not — when a refreshed
    claim is newer, ``_arbitrate`` returns the refresh prompt plus a bare
    mention of the paused claim, so a reader was told a claim existed and
    handed nothing to copy. Measured before the fix: that branch carried
    `refresh_ts=` and no `pause_ts=`. An agent with nothing to copy either
    skips the write or improvises from the rendered date, and `_claim_date`
    renders a DATE, not a ts, so an improvised value can never string-match.

    NOT SANITIZED, and this is the field in this module that must not be:
    bootstrap copies the value VERBATIM into the consumption event, and
    ``_pause_is_spent`` matches it against the claim's own ``ts`` by exact
    string compare. A sanitized echo would never match, so a ts carrying
    control characters renders UNAVAILABLE instead — failing toward
    surfacing, which is the safe direction. The refreshed side reaches the
    same answer through ``ts_valid``; keep the two separate (see
    ``_pause_is_spent`` on why this module does not merge across the two
    claim types).

    Returns a leading-space-prefixed clause, always non-empty — a caller can
    concatenate it unconditionally.
    """
    ts = event.get("ts", "")
    if (
        isinstance(ts, str)
        and ts.strip()
        and not _PROMPT_CONTROL_CHARS_RE.search(ts)
    ):
        return f" pause_ts={ts}"
    return (
        " pause_ts=UNAVAILABLE — consumption cannot be recorded; "
        "prompt may re-surface once."
    )


def _interpret_paused_event(event: dict) -> str | None:
    """Interpret an already-read session_paused event into a resume prompt.

    Split from _check_journal_paused_state so check_resume_state can feed
    it the event it already read (one journal read per event type). The
    split changed no DECISION: pr_number type-narrowing, the 14-day TTL,
    the `gh` PR-state probe and the silent-None branches all resolve as
    they did before it, and the branch structure is still the pre-split
    one.

    The PROMPT TEXT is not. Every branch that returns a prompt now appends
    the ` pause_ts=` consumption key rendered by _compose_pause_key, which
    bootstrap copies verbatim to retire the claim — so an audit of what
    this function renders must read the returns, not this sentence.

    Fail direction: PR-GATED SILENT-None is CORRECT here — a paused claim
    whose PR is gone (or that never had a valid PR) has nothing to resume.
    This is the OPPOSITE of the refresh interpreter's fail direction; the
    two interpreters deliberately share no predicate (see
    _interpret_refreshed_event).
    """
    pr_number = event.get("pr_number")
    branch = event.get("branch", "unknown")
    worktree_path = event.get("worktree_path", "unknown")

    # Defensive type narrowing: bool is a subclass of int, so we must
    # exclude it explicitly. 0/False/""/dict/list/None all fall through
    # to None so the formatter never sees a junk PR number.
    if not isinstance(pr_number, int) or isinstance(pr_number, bool) or pr_number <= 0:
        return None

    ts_str = event.get("ts", "")
    pause_key = _compose_pause_key(event)

    # TTL check: ts older than 14 days
    if ts_str:
        try:
            paused_at = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - paused_at).days
            if age_days > 14:
                paused_date = paused_at.strftime("%Y-%m-%d")
                return (
                    f"Stale paused state from {paused_date} "
                    f"(older than 14 days). PR #{pr_number} on {branch}."
                    f"{pause_key}"
                )
        except (ValueError, TypeError, OverflowError):
            pass

    # Active PR validation
    pr_state = _check_pr_state(pr_number)
    if pr_state in ("MERGED", "CLOSED"):
        return (
            f"Previously paused PR #{pr_number} has been "
            f"{pr_state.lower()}.{pause_key}"
        )

    consolidation = event.get("consolidation_completed", False)
    consolidation_note = ""
    if not consolidation:
        consolidation_note = (
            " Memory consolidation did NOT complete — "
            "run /PACT:pause or /PACT:wrap-up to capture session knowledge."
        )

    return (
        f"Paused work detected: PR #{pr_number} ({branch}) — awaiting merge. "
        f"Worktree at {worktree_path}. "
        f"Run /PACT:peer-review to resume review/merge."
        f"{consolidation_note}{pause_key}"
    )


# Added to the lead's context when a resumption claim surfaced but the
# session_resumption_surfaced journal write failed. Both sites that surface a
# claim use it: session_init step 8, and bootstrap_prompt_gate when it records
# a lead session_init did not.
RESUMPTION_MARKER_MISSING_DIRECTIVE = (
    "RESUMPTION MARKER MISSING: this session resumes an "
    "interrupted workstream, but the marker the secretary "
    "reads at spawn was not recorded. Tell the secretary "
    "NOT to rebuild the Working Memory block, in its spawn "
    "dispatch. Without that the block is rebuilt from the "
    "store, and agents spawned to judge this arc read the "
    "arc's own conclusions."
)


# How many sessions that did nothing a claim read walks past. Past three
# consecutive idle launches a remaining claim is taken as abandoned.
CLAIM_WALK_MAX_HOPS = 3

# The only event types a launch that took no turn writes. A session whose
# journal holds nothing else did nothing; any other type, including one added
# later, stops the walk.
_IDLE_LAUNCH_EVENT_TYPES = frozenset(
    {"session_start", "session_resumption_surfaced", "session_end", "cleanup_summary"}
)


def _session_did_nothing(session_dir: str) -> bool:
    """True when session_dir's journal records a launch and nothing but the
    events a launch with no turn writes. Never raises (False)."""
    try:
        events = read_events_from(session_dir)
        return (
            bool(events)
            and any(e.get("type") == "session_start" for e in events)
            and all(e.get("type") in _IDLE_LAUNCH_EVENT_TYPES for e in events)
        )
    except Exception:
        return False


def _recorded_predecessor(session_dir: str) -> str | None:
    """The previous session dir session_dir's last session_start recorded:
    the prev_session_dir of the last session_start whose value is not
    session_dir itself, when it lies under pact-sessions and exists. None
    otherwise. Never raises."""
    try:
        recorded = None
        for event in read_events_from(session_dir, "session_start"):
            value = event.get("prev_session_dir")
            if isinstance(value, str) and value and not _same_dir(value, session_dir):
                recorded = value
        if recorded is None:
            return None
        validated = _validate_under_pact_sessions(recorded)
        if validated is None or not Path(validated).is_dir():
            return None
        return validated
    except Exception:
        return None


def _claim_dir(
    prev_session_dir: str | None, own_session_dir: str | None = None,
) -> str | None:
    """The session dir to read resume claims from.

    prev_session_dir, or, while that session did nothing, the predecessor it
    recorded, at most CLAIM_WALK_MAX_HOPS steps back. Another session's dir
    is walked past only once that session has ended: a concurrent session
    that has just launched has done nothing YET, and the claim behind it is
    its to surface. This session's own dir needs no end. A cycle or a missing
    or invalid predecessor ends the walk at the current dir. Never raises: on
    any error it returns the dir reached so far.
    """
    current = prev_session_dir
    try:
        seen = {prev_session_dir}
        for _ in range(CLAIM_WALK_MAX_HOPS):
            if not current or not _session_did_nothing(current):
                return current
            if not _same_dir(current, own_session_dir) and not _session_ended(current):
                return current
            nxt = _recorded_predecessor(current)
            if not nxt or nxt in seen:
                return current
            seen.add(nxt)
            current = nxt
        return current
    except Exception:
        return current


def _same_dir(a: str | None, b: str | None) -> bool:
    """True when both paths are set and name the same directory once each
    is resolved (a ``~`` prefix, a trailing slash or a symlinked parent
    spells one directory several ways). Never raises."""
    try:
        if not a or not b:
            return False
        return os.path.realpath(os.path.expanduser(a)) == os.path.realpath(
            os.path.expanduser(b)
        )
    except Exception:
        return False


def open_workflow_phases(session_dir: str) -> list[str]:
    """The workflow phases a session left started and unfinished.

    Reads the phase_transition events after the journal's last
    session_consolidated, keeps each phase's last status, and returns the
    phases whose last status is ``started``, in the order they first
    appeared. Pure; ``[]`` on any error.
    """
    try:
        last_status: dict[str, str] = {}
        for event in read_events_from(session_dir):
            kind = event.get("type")
            if kind == "session_consolidated":
                last_status = {}
            elif kind == "phase_transition":
                phase = event.get("phase")
                if isinstance(phase, str) and phase:
                    last_status[phase] = event.get("status")
        return [p for p, s in last_status.items() if s == "started"]
    except Exception:
        return []


def _saved_team_of(session_dir: str) -> str:
    """The team_name in session_dir's context file when it is a safe path
    component, else ``""``. Never raises."""
    try:
        data = json.loads(
            (Path(session_dir) / "pact-session-context.json").read_text(
                encoding="utf-8"
            )
        )
        team = data.get("team_name") if isinstance(data, dict) else None
        return team if is_safe_path_component(team) else ""
    except Exception:
        return ""


def recorded_team_of(session_dir: str) -> str:
    """The team session_dir's context file records on disk, or ``""``.

    Read from the file, never from a cache, so a caller can tell a write that
    landed from one that did not. Never raises."""
    return _saved_team_of(session_dir) if session_dir else ""


def _session_ended(session_dir: str) -> bool:
    """True when session_dir's journal holds a session_end after its last
    session_start. Never raises."""
    try:
        ended = False
        for event in read_events_from(session_dir):
            kind = event.get("type")
            if kind == "session_start":
                ended = False
            elif kind == "session_end":
                ended = True
        return ended
    except Exception:
        return False


def _in_flight_prompt(
    claim_dir: str,
    own_session_dir: str | None,
    own_saved_team: str | None,
) -> str | None:
    """The resume prompt for work a session left in flight, or None.

    The team is the claim dir's recorded team, read before this launch
    rewrites it: for this session's own dir that is ``own_saved_team``, the
    value the caller read first. Another session's dir counts only once that
    session has ended, so a live parallel session is never read as this
    one's predecessor.
    """
    own = _same_dir(claim_dir, own_session_dir)
    if not own and not _session_ended(claim_dir):
        return None
    team = (own_saved_team or "") if own else _saved_team_of(claim_dir)
    if not is_safe_path_component(team):
        team = ""
    phases = open_workflow_phases(claim_dir)
    ids = in_progress_task_ids(team) if team else []
    if not phases and not ids:
        return None
    phase_text = ", ".join(_sanitize_prompt_field(p) for p in phases) or "none"
    id_text = ", ".join(_sanitize_prompt_field(i) for i in ids) or "none"
    team_text = _sanitize_prompt_field(team) if team else "none"
    return (
        f"Work was in progress when this session last ran: phases "
        f"{phase_text}; in-progress tasks {id_text} in `tasks/{team_text}/`. "
        f"Treat this as a resume."
    )


def check_resume_state(
    prev_session_dir: str | None = None,
    *,
    launch: bool = False,
    own_session_dir: str | None = None,
    own_saved_team: str | None = None,
) -> str | None:
    """Unified resume-claim resolver over {session_paused, session_refreshed},
    plus work left in flight on a launch.

    Single public seam: session_init step 8 calls THIS (and only this).
    Post-compact and same-session --resume, prev_session_dir self-resolves to
    the CURRENT session dir; on the quit path it names the previous session.
    Every arm reads its claim from _claim_dir(prev_session_dir,
    own_session_dir): that dir, or an earlier one when the sessions in
    between did nothing and have ended, at most CLAIM_WALK_MAX_HOPS back.

    On a launch (``launch`` True: a startup or a resume, never a compaction
    or a clear), when neither claim survives, a third arm with the lowest
    precedence reports work left in flight: phases started and unfinished,
    or in-progress tasks in the claim dir's recorded team. It returns None
    when there is none.

    The two per-event-type interpreters stay SEPARATE functions because
    their fail directions are opposite and must never share a predicate:
    the paused interpreter keeps its PR-gated silent-None (correct for
    pause), the refreshed interpreter is fail-safe-toward-surfacing (any
    unspent session_refreshed event yields a prompt). When both claims
    survive interpretation, _arbitrate picks the newer one — the losing
    claim is always mentioned, never silently dropped.

    Args:
        prev_session_dir: Previous session's directory path (from CLAUDE.md).
        launch: True on a startup or resume; enables the third arm.
        own_session_dir: This session's directory.
        own_saved_team: This session's recorded team, read before this
            launch rewrote it.

    Returns:
        The winning resume prompt, or None when no claim survives.
    """
    if not prev_session_dir:
        return None
    claim_dir = _claim_dir(prev_session_dir, own_session_dir)
    paused = read_last_event_from(claim_dir, "session_paused")
    refreshed = read_last_event_from(claim_dir, "session_refreshed")
    if refreshed is not None and _refresh_is_spent(claim_dir, refreshed):
        refreshed = None
    if paused is not None and _pause_is_spent(claim_dir, paused):
        paused = None
    paused_msg = (
        _interpret_paused_event(paused) if paused is not None else None
    )  # may be None (correct for pause)
    refresh_msg = (
        _interpret_refreshed_event(refreshed) if refreshed is not None else None
    )
    # Each message exists only when its event does; the None checks say so.
    if paused is not None and refreshed is not None and paused_msg and refresh_msg:
        return _arbitrate(paused, paused_msg, refreshed, refresh_msg)
    if refresh_msg or paused_msg:
        return refresh_msg or paused_msg
    if not launch:
        return None
    return _in_flight_prompt(claim_dir, own_session_dir, own_saved_team)


def _sanitize_prompt_field(
    value: str,
    limit: int = _REFRESH_FIELD_TRUNCATION_LIMIT,
) -> str:
    """Sanitize an event field value for interpolation into a resume prompt.

    Collapses control characters (C0, DEL, U+2028/U+2029 — anything that
    could break the prompt onto a new line and masquerade as a separate
    directive) to single spaces, strips, and bounds the length with the
    same ``...`` truncation convention as ``_coerce_decision_summary``.

    Total for any input: an internal failure returns ``""`` so the caller
    drops that field's LINE — content degrades, prompt presence never does
    (a sanitizer error must not become a new suppress path).
    """
    try:
        cleaned = _PROMPT_CONTROL_CHARS_RE.sub(" ", value).strip()
        if len(cleaned) > limit:
            cleaned = cleaned[:limit - 3] + "..."
        return cleaned
    except Exception:
        return ""


def _compose_halt_line(event: dict) -> str | None:
    """Compose the HALT verify-line for a session_refreshed event, or None
    when ``halt_active`` is not exactly True.

    Single composition point shared by ``_interpret_refreshed_event`` and
    ``_arbitrate`` so the interpreter and the losing-claim survival path
    can never render the same event's HALT state differently. Task ids are
    sanitized HERE, once — "verbatim" preservation downstream means this
    sanitized line. Include ids only when ``halt_task_ids`` is a list
    holding non-empty strings; ``halt_active`` malformed/absent ⇒ no line
    (live-task surfacing still covers the union's other leg).
    """
    if event.get("halt_active") is not True:
        return None
    halt_task_ids = event.get("halt_task_ids")
    ids = (
        [_sanitize_prompt_field(i) for i in halt_task_ids if isinstance(i, str)]
        if isinstance(halt_task_ids, list)
        else []
    )
    ids = [i for i in ids if i]
    id_note = f" (tasks: {', '.join(ids)})" if ids else ""
    return (
        f"A HALT/algedonic signal was ACTIVE at refresh{id_note} — "
        f"verify against TaskList before proceeding; do not assume it "
        f"resolved."
    )


def _interpret_refreshed_event(event: dict) -> str:
    """Interpret an already-read session_refreshed event into a prompt.

    MUST return a non-empty prompt for ANY dict input (fail-safe-toward-
    surfacing; return type is str, NOT str | None — totality by signature).
    Malformed fields degrade CONTENT, never PRESENCE. Do NOT copy the paused
    interpreter's early returns: no pr_number gate, no gh probe, no
    silent-None branch of any kind.

    Prompt composition: each absent/malformed field drops its line only.
    The 48h staleness horizon (_REFRESH_STALE_HOURS) prefixes an
    informational downgrade and changes nothing else — a stale prompt
    retains its HALT line. An unparseable/missing ts means no downgrade
    (treat as fresh — fail toward full surfacing).
    """
    ts = event.get("ts")
    # A ts carrying control characters is treated as INVALID for prompt
    # purposes: the consumption key must be echoed VERBATIM to keep the
    # spend-binding exact-match, so it cannot be sanitized — instead the
    # UNAVAILABLE branch renders (fails toward surfacing; the prompt may
    # re-surface once, bounded by the staleness downgrade). Clean ts
    # values keep the byte-exact echo.
    ts_valid = (
        isinstance(ts, str)
        and bool(ts.strip())
        and not _PROMPT_CONTROL_CHARS_RE.search(ts)
    )

    content: list[str] = []

    # String fields are SANITIZED at interpolation (_sanitize_prompt_field:
    # control-char strip + length bound) — the journal is plain JSONL on
    # disk, so a hand-crafted event must not smuggle directive lines or
    # flood the SessionStart context. Sanitization can empty a value
    # (all-control-chars input); an emptied field drops its line only.
    feature_subject = event.get("feature_subject")
    feature_task_id = event.get("feature_task_id")
    subject = (
        _sanitize_prompt_field(feature_subject)
        if isinstance(feature_subject, str)
        else ""
    )
    task_id = (
        _sanitize_prompt_field(feature_task_id)
        if isinstance(feature_task_id, str)
        else ""
    )
    if subject and task_id:
        content.append(f"Feature: {subject} (task {task_id}).")
    elif subject:
        content.append(f"Feature: {subject}.")
    elif task_id:
        content.append(f"Feature task: {task_id}.")

    next_phase = event.get("next_phase")
    if isinstance(next_phase, str):
        phase = _sanitize_prompt_field(next_phase)
        if phase:
            content.append(f"Next phase: {phase}.")

    worktrees = event.get("worktrees")
    if isinstance(worktrees, list):
        # Paths sanitized like every interpolated field (wider bound —
        # legitimate absolute paths can be long); existence checking still
        # happens at bootstrap, not here — the resolver stays a pure
        # journal reader.
        paths = [
            _sanitize_prompt_field(w, _REFRESH_PATH_TRUNCATION_LIMIT)
            for w in worktrees
            if isinstance(w, str)
        ]
        paths = [p for p in paths if p]
        if paths:
            content.append("Worktrees: " + ", ".join(paths) + ".")

    # HALT line (I2): composed by _compose_halt_line (shared with
    # _arbitrate's losing-claim path) — present iff halt_active is True;
    # NEVER omit the prompt itself.
    halt_line = _compose_halt_line(event)
    if halt_line:
        content.append(halt_line)

    # Capture-knowledge warning (wording mirrors the paused interpreter's
    # consolidation note). Trigger is an EXPLICIT False — the field is
    # required + bool-validated at write time, so a missing/malformed
    # value means a malformed event, which keeps the degraded floor below
    # instead of fabricating a warning.
    if event.get("consolidation_completed") is False:
        content.append(
            "Memory consolidation did NOT complete — "
            "run /PACT:pause or /PACT:wrap-up to capture session knowledge."
        )

    # Degraded floor: a dict with no usable field at all still surfaces.
    if not content and not ts_valid:
        return (
            "Refresh detected — run TaskList to recover state, "
            "then /PACT:bootstrap."
        )

    header = (
        "Refreshed workstream detected — mid-flight resume, "
        "not a fresh start."
    )
    if ts_valid:
        try:
            refreshed_at = _parse_ts(ts)
            if refreshed_at.tzinfo is None:
                refreshed_at = refreshed_at.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - refreshed_at
            if age > timedelta(hours=_REFRESH_STALE_HOURS):
                header = (
                    f"STALE checkpoint from "
                    f"{refreshed_at.strftime('%Y-%m-%d')} (older than "
                    f"{_REFRESH_STALE_HOURS}h). " + header
                )
        except (ValueError, TypeError):
            pass  # Unparseable ts ⇒ no downgrade — fail toward full surfacing.

    parts = [header]
    parts.extend(content)
    # Consumption key, ALWAYS when ts is a non-empty str — verbatim and
    # machine-copyable; bootstrap's consumption write substitutes this value.
    if ts_valid:
        parts.append(f"refresh_ts={ts}")
    else:
        parts.append(
            "refresh_ts=UNAVAILABLE — consumption cannot be recorded; "
            "prompt may re-surface once."
        )
    parts.append(
        "Run /PACT:bootstrap to respawn the secretary and resume. "
        "Do NOT message any pre-refresh teammate name before bootstrap "
        "respawns it."
    )
    return " ".join(parts)


def _refresh_is_spent(session_dir: str, refreshed: dict) -> bool:
    """True iff a session_refresh_consumed event retires this refresh claim.

    Fire-once via ts-bound consumption: the refresh event's `ts` IS the
    claim id; a consumption's `refresh_ts` must match it exactly (string
    compare — no parsing on the identity axis). Every failure path lands on
    UNSPENT (return False), so a malformed consumption can never suppress a
    prompt. The `>=` conjunct is a belt on the SUPPRESS direction only: the
    consumption must also be temporally sane (written at-or-after its
    refresh). It can never wrongly KEEP a prompt, and it blocks the only
    wrong-spend shape — a consumption record predating its claim.

    WHAT KEEPING A PROMPT NOW COSTS, because it is no longer one duplicate.
    A surfaced claim also makes `session_init` record
    `session_resumption_surfaced`, and the secretary reads that marker at
    spawn and SKIPS its Working Memory rebuild. So the price of an UNSPENT
    failure is a duplicate prompt AND a session whose block is not rebuilt.
    UNSPENT is still the right direction — a wrongly-rebuilt block hands an
    arc's own conclusions to the agents whose value is judging it
    independently, which is the defect this mechanism exists to prevent,
    while an unrebuilt block is recoverable and does not outlive its session
    (the marker is written to the CURRENT session dir, so one marker freezes
    one session; an unspent claim freezes each session that surfaces it,
    and is read at most CLAIM_WALK_MAX_HOPS idle sessions back). Weigh both
    costs before narrowing this predicate, not just the prompt.
    """
    ts = refreshed.get("ts")
    if not isinstance(ts, str) or not ts:
        return False  # fail toward surfacing
    for consumption in read_events_from(session_dir, "session_refresh_consumed"):
        if consumption.get("refresh_ts") != ts:  # exact string match — the ts IS the claim id
            continue
        try:
            if _parse_ts(consumption.get("ts")) >= _parse_ts(ts):
                return True
        except Exception:
            continue  # fail toward surfacing
    return False


def _pause_is_spent(session_dir: str, paused: dict) -> bool:
    """True iff a session_pause_consumed event retires this paused claim.

    A DELIBERATE MIRROR OF _refresh_is_spent. The two bodies are identical
    apart from three tokens (`refreshed`/`paused`, `refresh_ts`/`pause_ts`,
    `session_refresh_consumed`/`session_pause_consumed`) — measured, not
    estimated — so treat this as a copy kept on purpose and know what the
    purpose is and is not.

    WHAT ACTUALLY HOLDS THE TWO APART IS THE EVENT TYPE, AND THE TESTS PIN
    THAT AND NOTHING MORE. `test_a_pause_consumption_does_not_spend_a_refresh`
    and its mirror assert that the two consumption STREAMS MUST NOT CROSS: a
    `session_pause_consumed` must never retire a refresh claim, and the
    reverse. That is the property to preserve. A merge into one helper taking
    the consumed type and key field as arguments would keep both arms green;
    what reddens them is a merge that DROPS those arguments. So the tests do
    not forbid parameterising, and neither does this comment.

    WHAT IS NOT A REASON, stated because it was written here before and is
    the kind of claim that survives by sounding careful: that the two
    INTERPRETERS above have opposite fail directions. They do, and it is
    irrelevant to these two functions. Both predicates fail in the SAME
    direction — every path lands on UNSPENT — and nothing structural couples
    a merge here to a merge there; the interpreters differ in signature
    (`str | None` vs `str`) and are several times the size. An argument about
    what a later reader might be tempted to do next is not a property of the
    code, and it should not be doing a measurement's work.

    THREE COPIES REST ON THIS ONE CHOICE, so a change to any one of them
    needs the other two checked in the same pass: this predicate against
    `_refresh_is_spent`; `_compose_pause_key`'s validity triple against
    `_interpret_refreshed_event`'s `ts_valid`; and the `UNAVAILABLE` clause
    rendered in each. Nothing compares them, so a conjunct tightened in one
    place stays loose in the other two, silently and with a green suite.

    Fire-once via ts-bound consumption: the paused event's `ts` IS the claim
    id; a consumption's `pause_ts` must match it exactly (string compare — no
    parsing on the identity axis). Every failure path lands on UNSPENT (return
    False), so a malformed consumption can never suppress a prompt. The `>=`
    conjunct is a belt on the SUPPRESS direction only: the consumption must
    also be temporally sane (written at-or-after its claim). It can never
    wrongly KEEP a prompt, and it blocks the only wrong-spend shape — a
    consumption record predating its claim.

    WHAT KEEPING A PROMPT NOW COSTS, because it is no longer one duplicate —
    see the same paragraph on `_refresh_is_spent`. A surfaced claim also makes
    `session_init` record `session_resumption_surfaced`, and the secretary
    skips its Working Memory rebuild for that session. UNSPENT remains the
    right direction, and the cost of choosing it is a duplicate prompt AND an
    unrebuilt block, not a duplicate prompt alone.

    No public wrapper, unlike has_unspent_refresh: that one exists because
    session_init's compact branch consumes it as a presentation signal. A
    pause counterpart has no caller, and an exported function with no caller
    is a thing the next reader deletes or wires up somewhere it does not
    belong.
    """
    ts = paused.get("ts")
    if not isinstance(ts, str) or not ts:
        return False  # fail toward surfacing
    for consumption in read_events_from(session_dir, "session_pause_consumed"):
        if consumption.get("pause_ts") != ts:  # exact string match — the ts IS the claim id
            continue
        try:
            if _parse_ts(consumption.get("ts")) >= _parse_ts(ts):
                return True
        except Exception:
            continue  # fail toward surfacing
    return False


def _both_parse(*timestamps: Any) -> bool:
    """True iff every argument parses via _parse_ts without raising."""
    for value in timestamps:
        try:
            _parse_ts(value)
        except (ValueError, TypeError):
            return False
    return True


def _claim_date(ts: Any) -> str:
    """Render a claim timestamp as YYYY-MM-DD for the superseded-claim
    clause. Callers guarantee parseability via _both_parse."""
    return _parse_ts(ts).strftime("%Y-%m-%d")


def _arbitrate(
    paused_ev: dict,
    paused_msg: str,
    refreshed_ev: dict,
    refresh_msg: str,
) -> str:
    """Pick the newer of two surviving resume claims (newest-ts-wins).

    Never silently drop a resume claim: the losing claim is always
    mentioned in one clause; when either ts is unparseable the claims
    cannot be ordered, so BOTH surface in full with an explicit conflict
    note. Ties go to the refreshed claim (`_ts_supersedes` is `>=`) — the
    mid-flight claim is the more specific.

    HALT survival: a LOSING refreshed claim that carried an active HALT
    keeps its verify-line VERBATIM (the `_compose_halt_line` rendering —
    one composition point with the interpreter) and is labeled
    "superseded", not "stale" — arbitration must never become a suppress
    path for an algedonic signal.

    CONSUMPTION-KEY SURVIVAL, the same shape as HALT survival and the same
    reason. A losing PAUSED claim keeps its ` pause_ts=` key (the
    `_compose_pause_key` rendering — one composition point with the
    interpreter). Mentioning a claim is not enough: any claim that surfaces
    freezes the Working Memory block for that session, and bootstrap retires
    it by copying this key, so a mention without a key names a freeze the
    reader cannot end. The three branches that embed `paused_msg` carry the
    key inside it; the refresh-wins branch does not, and appends it here.
    """
    p_ts, r_ts = paused_ev.get("ts"), refreshed_ev.get("ts")
    if not _both_parse(p_ts, r_ts):
        return (
            refresh_msg + " | " + paused_msg +
            " | CONFLICT: both a paused and a refreshed claim exist with "
            "unordered timestamps — verify via TaskList before resuming."
        )
    if _ts_supersedes(r_ts, p_ts):
        return refresh_msg + (
            f" (A stale paused claim from {_claim_date(p_ts)} also exists."
            f"{_compose_pause_key(paused_ev)})"
        )
    halt_line = _compose_halt_line(refreshed_ev)
    if halt_line:
        return paused_msg + (
            f" (A superseded refreshed claim from {_claim_date(r_ts)} also "
            f"exists.) {halt_line}"
        )
    return paused_msg + (
        f" (A stale refreshed claim from {_claim_date(r_ts)} also exists.)"
    )


def has_unspent_refresh(session_dir: str | None) -> bool:
    """True iff the dir's latest session_refreshed exists and is unconsumed.

    Presentation-only signal for session_init's compact branch (suppress
    'Re-engage secretary', re-label the agent list). The FULL prompt comes
    only from check_resume_state at step 8 — this helper never composes
    surfacing text. Any internal error returns False (the directive keeps
    its current wording — degraded, not broken).
    """
    try:
        if not session_dir:
            return False
        refreshed = read_last_event_from(session_dir, "session_refreshed")
        if refreshed is None:
            return False
        return not _refresh_is_spent(session_dir, refreshed)
    except Exception:
        return False


def _check_pr_state(pr_number: int | str) -> str:
    """
    Check PR state via ``gh pr view``. Returns uppercase state or empty on error.

    Thin wrapper around ``shared.gh_helpers.check_pr_state`` — kept as a
    module-local function (not a bare re-export) so existing test patches
    of ``shared.session_resume._check_pr_state`` continue to work without
    modification (#453: relocated the implementation, preserved the call
    surface).
    """
    from shared import check_pr_state

    return check_pr_state(pr_number)
