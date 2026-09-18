"""
PACT Memory CLI Entry Point

Location: pact-plugin/skills/pact-memory/scripts/cli.py

Thin command-line facade over the PACTMemory API. Translates CLI arguments
to PACTMemory method calls and serializes results as JSON. Contains zero
business logic — all intelligence stays in memory_api.py.

Used by:
- SKILL.md: Documents CLI invocation for agents via ${CLAUDE_SKILL_DIR}
- Tests: test_memory_cli.py for unit and subprocess integration tests

Usage:
    python3 cli.py <command> [args] [--options]

Commands:
    save <json>          Save a memory object (or --stdin for piped input)
    search <query>       Semantic search across memories
    list [--limit N]     List recent memories (default: 20)
    get <id|prefix>      Retrieve a memory by full ID or unique prefix (>= 7 chars)
    update <id|prefix> <json>
                         Update an existing memory by full ID or unique prefix
                         (or --stdin for piped input). Ambiguous prefix is refused.
    delete <id|prefix>   Delete a memory by full ID or unique prefix.
                         Ambiguous prefix is refused.
    status               Show memory system status
    setup                Initialize/verify memory system
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import NoReturn

# Path resolution: add the skill root (parent of scripts/) to sys.path
# so that `from scripts import PACTMemory` works regardless of cwd.
_SKILL_ROOT = str(Path(__file__).resolve().parent.parent)
if _SKILL_ROOT not in sys.path:
    sys.path.insert(0, _SKILL_ROOT)

from scripts.config import store_scope
from scripts.database import (
    CALLER_FACING_CREATE_FIELDS,
    CALLER_FACING_UPDATE_FIELDS,
    AmbiguousPrefixError,
    PrefixTooShortError,
)
from scripts.memory_api import PACTMemory, ProjectScopeDisagreementError
from scripts.setup_memory import ensure_initialized, get_setup_status


# The stream the ERROR ENVELOPE is written to. None means "use sys.stderr",
# which is the state outside a guarded window.
#
# While `_own_stderr_for_envelope` holds fd 2, this is a private handle on the
# ORIGINAL stderr. So the envelope continues to leave the process on fd 2. The
# guard bounds WHO MAY WRITE to the channel. It does not move the payload to
# another channel, because the stdout and stderr split is a shipped contract:
# stdout carries the success envelope and stderr carries the error envelope,
# and merging them would trade a corrupt error envelope for a corrupt success
# envelope on the more common path.
_ENVELOPE_STREAM = None


def _envelope_stream():
    """Return the stream the error envelope must be written to."""
    return sys.stderr if _ENVELOPE_STREAM is None else _ENVELOPE_STREAM


def _best_effort_report(action, *args, **kwargs) -> bool:
    """Run one diagnostic write. Report whether it landed. Raise nothing.

    THE POLICY IN ONE PLACE: a diagnostic write is BEST EFFORT and it must not
    change the outcome of an operation that succeeded. A caller who sends
    stderr into a program that stops reading has decided it does not want the
    rest of the output, and turning that decision into a failure of the
    command is incorrect.

    THE TWO CLASSES ARE MEASURED AND THEY ARE NOT ONE CLASS. A write to a
    reader-less pipe raises BrokenPipeError, which is an OSError. A flush on a
    CLOSED Python file object raises ValueError, which is not. Catching
    OSError alone leaves the second class live.

    THIS BOUNDS REPORTING ONLY. It must not wrap an operation, and it must not
    wrap descriptor state. A caller that hides an operation behind this turns
    a failure into a silent success, which is the opposite of the defect it
    exists to close.
    """
    try:
        action(*args, **kwargs)
        return True
    except (OSError, ValueError):
        return False


def _neutralise_unwritable_std_streams() -> None:
    """Point a standard stream that cannot be written at the null device.

    WHY THE GUARDED WRITES ARE NOT ENOUGH, AND THIS IS THE WHOLE REASON THIS
    FUNCTION EXISTS. `_best_effort_report` bounds the write it wraps. It cannot
    bound the flush the INTERPRETER performs on the way out. A piped stdout is
    block buffered, so a small envelope lands in the buffer and the guarded
    `print` raises NOTHING. The bytes stay pending, the interpreter flushes them
    during finalization, that flush meets the reader-less pipe, and CPython
    reports `Exception ignored while flushing sys.stdout` and then exits 120.
    The 120 replaces the status the operation earned.

    THE CONDITION IS A STREAM THAT REFUSES A FLUSH, NOT A SIZE. Whether a write
    raises at the call or survives in a buffer until shutdown depends on the
    stdio buffer size and on the pipe capacity of the system. Those move with
    the platform and with the interpreter, so a byte threshold recorded here
    would decay with no event to show it. Ask the stream instead: attempt the
    flush, and act only when it refuses.

    THE REDIRECT IS WHAT MAKES THE LATER FLUSH SUCCEED. Pending bytes stay in
    the buffer after a failed flush, and the interpreter retries them. After the
    descriptor points at the null device that retry succeeds, so finalization
    reports nothing and the exit status stays the one the command chose.

    ⚠️ CALL THIS ONLY FROM THE `__main__` BLOCK. It rebinds a process-global
    descriptor, so an in-process caller of `main()`, which the unit tests of this
    CLI are, would have the descriptor of the TEST RUNNER pointed at the null
    device and lose the rest of its own output. The `__main__` block is the only
    caller that ends the process, which is the condition that makes the rebind
    safe. An AST arm pins that placement.
    """
    for stream, fd in ((sys.stdout, 1), (sys.stderr, 2)):
        if _best_effort_report(stream.flush):
            continue
        null_fd = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(null_fd, fd)
        finally:
            os.close(null_fd)


def _replay_capture(capture) -> None:
    """Copy the captured bytes to the restored stderr. Verbatim, and by design.

    To clean the bytes would mean to parse a progress format, which is the
    coupling the guard exists to avoid.
    """
    os.lseek(capture.fileno(), 0, os.SEEK_SET)
    while True:
        chunk = os.read(capture.fileno(), 65536)
        if not chunk:
            break
        os.write(2, chunk)


@contextmanager
def _own_stderr_for_envelope():
    """Own file descriptor 2 for the length of a command handler.

    THE DEFECT THIS CLOSES. Both envelopes are machine-readable, and a caller
    parses stderr to read the error one. A DEPENDENCY MAY ALSO WRITE THERE: the
    embedding backend emits a download progress bar, and the standard-library
    logging last-resort handler emits WARNING and above when no handler is
    configured. Either one puts bytes in front of the envelope, and the parse
    then fails on output that looks correct to a human reader.

    WHY THE FILE DESCRIPTOR AND NOT `sys.stderr`. Rebinding `sys.stderr` bounds
    a writer that goes through Python. It does NOT bound a writer that reaches
    file descriptor 2 directly, which a compiled dependency can do. That
    narrower guard passes the tests written for the emitter of the day and
    leaves the general fault open.

    WHY NOT A PROGRESS-BAR SWITCH IN THE DEPENDENCY. That names the emitter of
    the day. It goes stale without a sound when the backend changes, and it
    does not reach the logging handler at all. This guard names no library and
    no output shape.

    ⚠️ DISPOSAL DIFFERS BY EXIT PATH, AND EACH LOSS IS DELIBERATE.
      ON FAILURE the captured bytes are DISCARDED. There is nowhere to put
      them: stderr must hold the envelope alone, and stdout is the success
      channel. The loss is a dependency diagnostic, and it is bounded, because
      the envelope carries the error and the exit code carries the outcome.
      ON SUCCESS the captured bytes are REPLAYED to stderr. Nothing parses
      stderr on success, so the guard protects nothing there, and a silent
      command that takes 30 seconds for a first-run model download looks hung.
      WHAT REPLAY DOES NOT RESTORE IS THE TIMING. The bytes arrive after the
      wait they explain, so a progress bar reads as a block of finished frames
      rather than as live progress. That is a chosen trade and not an
      oversight. The bytes are replayed VERBATIM, because cleaning them would
      mean parsing a progress format, which is the coupling this guard exists
      to avoid.

    ⚠️ FILE DESCRIPTOR 2 IS PROCESS-GLOBAL, so for the length of this window
    ANYTHING in the process that writes to stderr is captured, and not the
    handler alone. The hazard is LATENT rather than live: this package starts
    no thread today. Add a worker thread that logs, and its output joins the
    capture. Do not read this guard as a per-caller channel.

    FAILS OPEN. If the descriptor cannot be duplicated, this yields without
    guarding, so the command behaves as it did before rather than failing for
    a reason the caller cannot act on.
    """
    global _ENVELOPE_STREAM

    try:
        saved_fd = os.dup(2)
    except OSError:
        yield None
        return

    capture = tempfile.TemporaryFile()
    previous_envelope = _ENVELOPE_STREAM
    envelope_stream = os.fdopen(saved_fd, "w", buffering=1, closefd=False)
    replay = False

    sys.stderr.flush()
    os.dup2(capture.fileno(), 2)
    _ENVELOPE_STREAM = envelope_stream

    try:
        yield capture
        replay = True
    except SystemExit as exc:
        # `_success` exits 0 and `_error` exits non-zero, so the exit code IS
        # the outcome. A bare `sys.exit()` carries None and counts as success.
        replay = exc.code in (0, None)
        raise
    finally:
        # ⚠️ A FAILURE IN THE REPORTING PATH MUST NOT CHANGE THE OUTCOME OF AN
        # OPERATION THAT SUCCEEDED. Writing a diagnostic is BEST EFFORT. The
        # exit status is a CONTRACT about the operation, and the two are not
        # the same promise.
        #
        # THE DEFECT THIS CLOSES, and it needs an ordinary command line rather
        # than a constructed one. `cmd 2>&1 | head` leaves stderr a pipe with
        # no reader once head stops. The replay below then raised, `_error`
        # failed writing its own envelope to that same broken stream, and the
        # process exited non-zero AFTER the handler had printed a success
        # envelope on stdout. Stdout said success and the exit code said
        # failure, for a command that did what it was asked.
        #
        # THE TWO FAILURE CLASSES ARE DIFFERENT AND BOTH ARE REACHABLE, so the
        # clause names each. MEASURED, not assumed: a write to a reader-less
        # pipe raises BrokenPipeError, which IS an OSError. A flush on a Python
        # file object that is CLOSED raises ValueError, which is NOT an
        # OSError. An OSError-only clause covers the first and lets the second
        # through.
        _best_effort_report(sys.stderr.flush)
        _best_effort_report(envelope_stream.flush)

        # STATE, NOT REPORTING, SO IT STAYS OUTSIDE THE BOUND. A descriptor
        # left pointing at the capture silences the whole process, which is a
        # worse outcome than a raise a caller can see.
        _ENVELOPE_STREAM = previous_envelope
        os.dup2(saved_fd, 2)

        # ⚠️ CLOSE THE PRIVATE HANDLE BEFORE ITS DESCRIPTOR GOES AWAY, AND CLOSE
        # IT BEST EFFORT. When the envelope write above met a reader-less
        # stream, the bytes are STILL PENDING inside this handle: a failed flush
        # does not discard them. The next line closes the descriptor beneath it.
        # Left open, the handle keeps that residue over a descriptor that has
        # gone, and its finalizer attempts one more flush and raises EBADF.
        #
        # WHY THAT MATTERS RATHER THAN BEING TIDINESS. In a SPAWNED process the
        # interpreter turns that into `Exception ignored` text and exit 120,
        # which discards the exit code the command chose. IN-PROCESS, which the
        # unit tests of this CLI are, it surfaces as an unraisable exception
        # inside the test runner. `_neutralise_unwritable_std_streams` cannot
        # reach either case: it runs from `__main__` only, and it acts on the
        # two standard streams rather than on this private handle.
        #
        # BEST EFFORT, because the close itself flushes and so can raise the
        # same BrokenPipeError. A bare close would replace the outcome of the
        # operation with a failure of the report, which is the defect this whole
        # block exists to prevent.
        _best_effort_report(envelope_stream.close)
        os.close(saved_fd)

        if replay:
            _best_effort_report(_replay_capture, capture)
        capture.close()


def _success(result):
    """Print a success JSON envelope to stdout and exit 0.

    THE EXIT STATUS DOES NOT DEPEND ON THE WRITE LANDING. A caller that pipes
    this command into a program that stops reading, such as `head`, leaves
    stdout a pipe with no reader. The operation is finished by then, so a
    failed write is a lost REPORT and not a failed COMMAND.

    ⚠️ THE WRAP HERE DELIVERS ONLY HALF OF THAT, AND AN EARLIER WORDING CLAIMED
    THE WHOLE OF IT. `_best_effort_report` bounds the write it wraps, which is
    the case where the write RAISES. A piped stdout is block buffered, so a
    small envelope lands in the buffer and this call raises NOTHING. The
    interpreter flushes those bytes during finalization, that flush meets the
    reader-less pipe, and the process exits 120 with `Exception ignored while
    flushing sys.stdout` on the terminal of the caller. Measured, on the shipped
    handler.
    `_neutralise_unwritable_std_streams`, called from the `__main__` block, is
    what closes the second case. Read the two together: this wrap keeps the
    exception out of the handler, and that step keeps the exit status intact.
    """
    _best_effort_report(
        print, json.dumps({"ok": True, "result": result}, indent=2, default=str)
    )
    sys.exit(0)


def _error(error_type, message, exit_code=1, **extra) -> NoReturn:
    """Print an error JSON envelope to stderr and exit with given code.

    Any extra kwargs are merged into the envelope (e.g. allowed_fields).

    WRITES THROUGH `_envelope_stream()`, NOT THROUGH `sys.stderr` DIRECTLY.
    Inside a guarded handler that resolves to a private handle on the original
    stderr, so the envelope leaves the process on file descriptor 2 while a
    dependency writing to that descriptor is captured instead.

    THE EXIT CODE SURVIVES A FAILED WRITE, AND IT IS THE POINT OF THE GUARD
    HERE. If the stream carrying this envelope is unwritable, the caller loses
    the TEXT and keeps the NON-ZERO EXIT CODE. That is the correct trade: the
    exit code is the smaller and more reliable channel, and it continues to
    carry the outcome. Left unguarded, a broken stream replaced this call with
    an exception, and the process died with a status that described the
    reporting failure rather than the operation.
    """
    envelope = {"ok": False, "error": error_type, "message": message}
    envelope.update(extra)
    _best_effort_report(print, json.dumps(envelope), file=_envelope_stream())
    sys.exit(exit_code)


def _refuse_live_db_under_pytest(db_path) -> None:
    """Refuse the live store when a TEST PROCESS spawned us.

    THE DEFECT THIS CLOSES. `--db-path` is how a test scopes its writes, and
    omitting it selects whatever the default resolves to -- the developer's
    real `memory.db` unless something has redirected it. A test that forgets it
    does not fail -- it succeeds, against whichever store that is. Requiring
    the parameter upstream makes the choice visible but not safe: the value may
    still be None, and an empty string is falsy, so it takes the same branch.
    This is the mechanical half.

    WHY AN ENVIRONMENT VARIABLE AND NOT `"pytest" in sys.modules`. This process
    is a FRESH INTERPRETER: the parent runs pytest, we do not. So a child-side
    guard cannot detect pytest by introspection and must key on something
    INHERITED. `PYTEST_CURRENT_TEST` is the only standard signal that crosses
    the boundary. The choice is FORCED, not preferred; an in-process check is
    not an available alternative.

    THE CONDITION THAT MAKES THAT TRUE, stated so it can be checked rather than
    trusted: `pytest` stays out of this interpreter's `sys.modules` SO LONG AS
    NO MODULE AUTO-IMPORTED AT STARTUP TRANSITIVELY REACHES IT. That is a
    property of the environment's IMPORT GRAPH -- `sitecustomize`, `usercustomize`,
    a `.pth` file, anything on `PYTHONPATH` that runs at startup -- and this
    function cannot verify it. Measurements confirm it holds today; they cannot
    establish it holds always, and an earlier wording here claimed the stronger
    thing.

    ⚠️ THE FAIL DIRECTION IS ALLOW, AND THE INSTRUMENT IS BLIND TO IT. A startup
    module whose import closure reaches pytest would take the early return in
    EVERY spawned child, disabling this guard everywhere at once. A spawn census
    would look byte-identical, because a census counts spawns and cannot see
    refusals that did not happen. Nothing here detects its own exemption.

    AND THE SAME BLINDNESS IS REACHABLE TODAY, WITHOUT WAITING FOR THAT CHANGE.
    A caller that clears the environment strips `PYTEST_CURRENT_TEST`, so this
    guard reads a clean process and admits the write. `env -i` does it, and so
    does a selective unset. THE HAZARD IS NOT IN THE FUTURE TENSE. The
    paragraph above describes only the startup-module route, which reads as
    though the danger has not arrived yet.

    THE PART THAT INVERTS THE USUAL THREAT INTUITION, and it is why this
    survived review: clearing the environment is what a CAREFUL caller does to
    isolate a probe. It is a habit, not an attack. So the more careful the
    caller, the more likely this guard goes blind, and a reviewer who imagines
    an adversary passes it.

    NO IN-PROCESS PROPERTY CLOSES THIS, and that is a BOUND rather than a
    to-do. The separating fact is CALLER IDENTITY, and for a fresh interpreter
    that lives only in the environment, so a stronger predicate is not
    available here. The parent-side sibling in `archive_pin._run_memory_cli`
    CAN add `"pytest" in sys.modules`, because that one runs in the pytest
    interpreter. This one cannot, which is the same forced choice recorded
    above, stated as a residual rather than as a justification.

    WHY IT IS GATED, AND WHY THE GATE IS NOT OPTIONAL. `archive_pin --index N`
    is the curator's documented production invocation and it passes NO
    `--db-path` -- production SHOULD use the real store. An ungated refusal
    would break that command outright, and it is the command
    `/PACT:prune-memory` keys its refuse-or-proceed decision on. That is a
    cardinal over-block on the one path whose purpose is not destroying
    content. Outside pytest this function returns immediately.

    DEPENDENCY WITH AN `ALLOW` FAIL DIRECTION -- the reason its tripwire test
    is not optional. This guard works only because the spawning parent hands us
    a FULL copy of its environment. Hardening that to a minimal allowlist is a
    plausible and otherwise desirable change, and it would DISABLE this guard
    silently while every test still passed. Nothing here can detect that; the
    detector is the test asserting the child actually receives the variable.

    SCOPE: SPAWNED CHILDREN ONLY, and the `sys.modules` check is what enforces
    it. `main()` is also called IN-PROCESS by the CLI's own unit tests, which
    patch `PACTMemory` and open no store at all -- and one of them exists
    precisely to assert that an omitted `--db-path` yields `db_path=None`.
    Firing there would refuse a contract the CLI is supposed to have. Because
    an in-process caller DOES have pytest imported, that case is separable,
    and the same fact that forces the env signal for children also identifies
    them: `"pytest" not in sys.modules` means "I am a fresh interpreter".

    This is the guard's specified reach, not a concession to those tests: the
    design bounds it to subprocess spawns and records that the in-process
    class is out of its range, covered upstream by `build_verdict`'s required
    parameter and by the caller-side falsy-but-present rejection. RESIDUAL,
    stated rather than implied: an in-process `main()` call with a real
    `PACTMemory` and no `--db-path` would still reach the live store.
    Nothing here catches that, and nothing currently does it.

    BOUNDED GAP, stated rather than implied: pytest POPS `PYTEST_CURRENT_TEST`
    between items, so it is absent during collection and around
    session-scoped-fixture setup. A spawn from either of those is NOT covered.
    """
    if db_path is not None:
        return
    if "pytest" in sys.modules:
        return          # in-process caller -- out of this guard's scope
    current_test = os.environ.get("PYTEST_CURRENT_TEST")
    if not current_test:
        return
    # THE MESSAGE STATES THE OBSERVATION, NOT AN INFERENCE FROM IT. The guard
    # sees an environment variable; it does NOT see a pytest run. Those come
    # apart -- an exported or inherited PYTEST_CURRENT_TEST reaches a plain
    # shell with no test anywhere -- and an earlier wording asserted the
    # inference ("this process was spawned from a pytest run"), which is
    # simply false in exactly the case a curator hits.
    #
    # IT NAMES THE VARIABLE, so the reader can check and clear it. A refusal
    # that will not say what it keyed on cannot be self-diagnosed.
    #
    # ⚠️ THE REMEDY IS AUDIENCE-SPECIFIC AND THE TWO ANSWERS ARE OPPOSITE.
    # For a test, --db-path is right. For a CURATOR archiving a pin, it is
    # actively destructive: the archive would land in a throwaway database,
    # the verdict would report success, and the pin would become eligible for
    # deletion with its only copy in a file about to be discarded. An earlier
    # wording gave the test answer to both. A correct guard with the wrong
    # remedy can destroy exactly what the guard protected, so the curator's
    # branch says do NOT pass --db-path.
    _error(
        "UNSCOPED_TEST_DB",
        "refusing to open the default memory database: PYTEST_CURRENT_TEST "
        "is set in this process's environment, no --db-path was given, and "
        "`pytest` is absent from this interpreter, which is what scopes this "
        "guard to spawned children. Those three facts are the whole of what "
        "it observed. It does "
        "NOT resolve the default location, so it cannot say WHICH database a "
        "write would reach: with PACT_TEST_MEMORY_DIR set the default is already "
        "redirected, and without it the default is the real store. If this "
        "IS a test, pass "
        "--db-path pointing at a temporary database. If you are ARCHIVING A "
        "PIN and meant to use the real store, do NOT pass --db-path -- that "
        "would archive into a throwaway database and make the pin eligible "
        "for deletion; instead unset PYTEST_CURRENT_TEST and run again. "
        f"PYTEST_CURRENT_TEST={current_test}",
        exit_code=2,
    )


def _scrub(msg: str) -> str:
    """
    Replace the user's home directory with '~' in an error message.

    Handles both the raw `~` expansion and the realpath form (which may
    differ on macOS where `/Users/foo` resolves through `/System/Volumes/Data`
    or similar). Guards against an empty/unset HOME — if expanduser returns
    the literal '~', no substitution is applied.

    Applied to caller-visible error envelopes so absolute paths don't leak
    into stderr for callers piping JSON envelopes into logs.
    """
    if not msg:
        return msg
    home = os.path.expanduser("~")
    # Empty HOME → expanduser returns the literal '~'. Don't substitute '~'
    # for '~' (no-op) and don't realpath an empty path.
    if home and home != "~":
        real_home = os.path.realpath(home)
        # Order matters: replace the longer/realpath form first so partial
        # overlaps don't leave a trailing suffix.
        if real_home != home:
            msg = msg.replace(real_home, "~")
        msg = msg.replace(home, "~")
    return msg


def cmd_save(args, db_path=None):
    """Handle the 'save' subcommand."""
    if args.stdin:
        raw = sys.stdin.read()
    elif args.json_data:
        raw = args.json_data
    else:
        _error("MISSING_INPUT", "Provide JSON as argument or use --stdin")

    try:
        memory_dict = json.loads(raw)
    except json.JSONDecodeError as exc:
        _error("INVALID_JSON", f"Failed to parse JSON: {exc}")

    if not isinstance(memory_dict, dict):
        _error("INVALID_INPUT", "JSON input must be an object, not a list or scalar")

    memory = PACTMemory(db_path=db_path)
    try:
        # Pass the kwarg ONLY when suppressing, so the default call site
        # stays literally `memory.save(memory_dict)`. That is a stronger form
        # of "existing callers are unaffected" than relying on the parameter
        # default: the call is byte-identical, not merely equivalent. The
        # suite's existing exact-call assertions pin this, and they were
        # right to -- they caught the weaker version.
        save_kwargs = {}
        if getattr(args, "no_sync", False):
            save_kwargs["sync_to_claude"] = False
        # Same conditional-kwarg discipline as --no-sync above, for the same
        # reason: an unflagged save must still call `memory.save(memory_dict)`
        # byte-identically, not merely equivalently.
        claude_md_root = getattr(args, "claude_md_root", None)
        if claude_md_root:
            save_kwargs["claude_md_root"] = Path(claude_md_root)
        memory_id = memory.save(memory_dict, **save_kwargs)
    except ProjectScopeDisagreementError as exc:
        # A deliberate fail-closed refusal (env vs session record), not bad
        # input: the message already names both values and the remedy.
        _error("SCOPE_DISAGREEMENT", _scrub(str(exc)))
    except ValueError as exc:
        _error(
            "ValueError",
            f"{_scrub(str(exc))} (Note: 'id' and 'created_at' are accepted "
            f"on save and stripped before validation.)",
            exit_code=2,
            allowed_fields=sorted(CALLER_FACING_CREATE_FIELDS),
        )
    # Carry the embedding outcome to the command-line caller. Without this the
    # CLI reports a bare memory_id, so a save that stored no vector is
    # indistinguishable from one that did -- which is the defect this field
    # exists to close, for the only consumer most callers have.
    #
    # `degraded:<mode>` is a SUCCESS state, not an error: the record saved, and
    # only semantic search is unavailable. It is added to the success envelope
    # alone; the error envelope's key set is pinned by a test and a degraded
    # save is not an error.
    #
    # `sync_status` JOINS IT HERE, AND THE TWO FIELDS DO NOT READ ALIKE.
    # `embedding_status` is PARTIAL: it reports a problem and is absent when the
    # embedding succeeded. `sync_status` is set on every branch that REACHES the
    # sync gate, `wrote` and `refused` included -- so Do NOT read an absent
    # `sync_status` as a successful sync. That inference is the defect the field
    # exists to remove: across this process boundary a refused sync and a
    # suppressed one both used to reach the parent as nothing at all, which is
    # indistinguishable from a sync that worked.
    #
    # IT IS NOT TOTAL, AND THIS COMMENT USED TO SAY IT WAS. The old wording read
    # "absent only when no save ran", which is false: `save()` clears the field
    # at entry and THEN calls `_ensure_ready()`, which installs dependencies and
    # runs embedding migration and can raise. A save that dies there leaves the
    # field absent with a save having run. The env/record refusal above it does
    # set `refused` before raising, so that path is covered -- but covering one
    # early exit is not totality, and naming the field total invited exactly the
    # "absent means nothing happened" inference the rest of this comment forbids.
    # Only the CLAIM is corrected here; making the field total would be a
    # behaviour change and is not in scope.
    result = {"memory_id": memory_id}
    embedding_status = memory.last_embedding_status
    if embedding_status is not None:
        result["embedding_status"] = embedding_status
    sync_status = memory.last_sync_status
    if sync_status is not None:
        result["sync_status"] = sync_status
    # `project_scope` JOINS THE TOTAL FAMILY, alongside `sync_status` and
    # unlike `embedding_status`. save() sets it on every branch, so an absent
    # value means no save ran -- it NEVER means the scope was fine. It reports
    # and does not judge, so it has no false-positive rate by construction.
    #
    # Its `location_divergence` key IS NOT A MISFILE FLAG: it compares the
    # process's working directory against what the record was filed under, so
    # False means only that those two agree. A record ABOUT another project,
    # written from the correct directory, shows False and is still misfiled.
    project_scope = memory.last_project_scope
    if project_scope is not None:
        result["project_scope"] = project_scope
    _success(result)


def cmd_search(args, db_path=None):
    """Handle the 'search' subcommand."""
    memory = PACTMemory(db_path=db_path)
    current_file = getattr(args, "current_file", None)
    # NO `sync_status` HERE, AND THE REASON IS AN OBSERVATION RATHER THAN A
    # PROPERTY. As of 2026-08-04 this call passes `sync_to_claude=False`
    # unconditionally, so no sync can happen on the search path. The sync
    # `search` would otherwise perform is `sync_retrieved_to_claude_md`, the
    # SIBLING writer, AND THE GAP THERE IS IN THE CONSUMER RATHER THAN IN THE
    # PRODUCER. That function is annotated `-> SyncResult` and all six of its
    # returns are `SyncResult`, so the reason IS produced. Its one caller,
    # `PACTMemory.search`, discards the returned object, so the reason feeds
    # no channel and reaches nobody. Both facts can change. If this argument
    # ever becomes caller-controlled, or that caller keeps what it is handed,
    # this envelope needs the field too. Do not read its absence as
    # "search never syncs".
    # Conditional kwarg, for the reason `cmd_save` states two functions up: an
    # unflagged call must stay BYTE-IDENTICAL, not merely equivalent, because
    # the suite pins these calls exactly. Passing `claude_md_root=None`
    # unconditionally is equivalent in behaviour and different in argv, and the
    # exact-call assertions were right to catch it.
    search_kwargs = {}
    search_root = getattr(args, "claude_md_root", None)
    if search_root:
        search_kwargs["claude_md_root"] = Path(search_root)
    results = memory.search(
        args.query, current_file=current_file, limit=args.limit,
        sync_to_claude=False, **search_kwargs
    )
    _success([r.to_dict() for r in results])


def cmd_list(args, db_path=None):
    """Handle the 'list' subcommand."""
    memory = PACTMemory(db_path=db_path)
    results = memory.list(limit=args.limit)
    _success([r.to_dict() for r in results])


def cmd_get(args, db_path=None):
    """Handle the 'get' subcommand.

    Accepts a full 32-char memory ID or a unique prefix. Ambiguous prefix
    surfaces as an AMBIGUOUS_PREFIX envelope including a capped match list,
    truncation flag, and total match count.
    """
    memory = PACTMemory(db_path=db_path)
    try:
        result = memory.get(args.memory_id)
    except PrefixTooShortError as exc:
        _error(
            "PREFIX_TOO_SHORT",
            str(exc),
            minimum=exc.minimum,
        )
    except AmbiguousPrefixError as exc:
        # Scrub user HOME from each match's `context` snippet so a memory
        # whose context recorded an absolute path doesn't leak it via the
        # disambiguation envelope. Per-site scrub keeps the redaction
        # obvious; do not centralize into `_error`.
        scrubbed_matches = [
            {**m, "context": _scrub(m["context"])} for m in exc.matches
        ]
        _error(
            "AMBIGUOUS_PREFIX",
            str(exc),
            prefix=exc.prefix,
            matches=scrubbed_matches,
            matches_capped=exc.matches_capped,
            total_matches=exc.total_matches,
        )
    if result is None:
        _error("NOT_FOUND", f"Memory '{args.memory_id}' not found")
    _success(result.to_dict())


def cmd_status(args, db_path=None):
    """Handle the 'status' subcommand."""
    memory = PACTMemory(db_path=db_path)
    status = memory.get_status()
    _success(status)


def cmd_setup(args, db_path=None):
    """Handle the 'setup' subcommand."""
    ok = ensure_initialized(db_path=db_path)
    if ok:
        status = get_setup_status()
        _success({
            "status": "ready",
            "message": "Memory system initialized successfully",
            "details": status,
        })
    else:
        _error("SETUP_FAILED", "Memory system initialization failed", exit_code=2)


def cmd_update(args, db_path=None):
    """Handle the 'update' subcommand.

    Accepts a full 32-char memory ID or a unique prefix. Ambiguous prefix
    refuses the update and surfaces an AMBIGUOUS_PREFIX envelope.
    """
    if args.stdin:
        raw = sys.stdin.read()
    elif args.json_data:
        raw = args.json_data
    else:
        _error("MISSING_INPUT", "Provide JSON as argument or use --stdin")

    try:
        updates = json.loads(raw)
    except json.JSONDecodeError as exc:
        _error("INVALID_JSON", f"Failed to parse JSON: {exc}")

    if not isinstance(updates, dict):
        _error("INVALID_INPUT", "JSON input must be an object, not a list or scalar")

    memory = PACTMemory(db_path=db_path)
    try:
        resolved_id = memory.update(args.memory_id, updates, replace=args.replace)
    except ProjectScopeDisagreementError as exc:
        # Same deliberate refusal shape as cmd_save: both values + remedy.
        _error("SCOPE_DISAGREEMENT", _scrub(str(exc)))
    except PrefixTooShortError as exc:
        # Order: PrefixTooShortError IS a ValueError; catch it before the
        # field-validation ValueError handler below.
        _error("PREFIX_TOO_SHORT", str(exc), minimum=exc.minimum)
    except AmbiguousPrefixError as exc:
        # Scrub user HOME from each match's `context` snippet so a memory
        # whose context recorded an absolute path doesn't leak it via the
        # disambiguation envelope. Per-site scrub keeps the redaction
        # obvious; do not centralize into `_error`.
        scrubbed_matches = [
            {**m, "context": _scrub(m["context"])} for m in exc.matches
        ]
        _error(
            "AMBIGUOUS_PREFIX",
            str(exc),
            prefix=exc.prefix,
            matches=scrubbed_matches,
            matches_capped=exc.matches_capped,
            total_matches=exc.total_matches,
        )
    except ValueError as exc:
        _error(
            "ValueError",
            f"{_scrub(str(exc))} (Note: 'id' and 'created_at' are stripped "
            f"before update validation.)",
            exit_code=2,
            allowed_fields=sorted(CALLER_FACING_UPDATE_FIELDS),
        )
    if resolved_id is None:
        _error("NOT_FOUND", f"Memory '{args.memory_id}' not found")
    # Mirror of the save envelope. An update that failed to re-embed is the
    # costlier case: a save that stored no vector leaves a record merely
    # invisible to semantic search, while an update leaves a vector describing
    # text the record no longer contains. Reporting on save alone would close
    # the milder path and leave the worse one silent.
    #
    # NO `sync_status` HERE EITHER, and again as an observation: as of
    # 2026-08-04 `update()` performs no CLAUDE.md sync at all, so the field
    # would either be absent or -- on a reused instance -- carry a PREVIOUS
    # save's outcome and misreport it as this update's. If `update` ever gains
    # a sync, add the field here rather than letting it inherit one.
    result = {"memory_id": resolved_id}
    embedding_status = memory.last_embedding_status
    if embedding_status is not None:
        result["embedding_status"] = embedding_status
    _success(result)


def cmd_sync(args, db_path=None):
    """Handle the 'sync' subcommand.

    Rebuilds CLAUDE.md's Working Memory section from this project's newest
    records. The envelope is total: `sync_status` names the outcome in every
    case, and `projected` is 0 with `memory_ids` [] on every outcome but
    `wrote`. `empty` means the project has no records and the file was not
    touched. `project_id` is the id the records were selected under, so an
    `empty` can be checked against the project the caller expected.
    """
    memory = PACTMemory(db_path=db_path)
    sync_kwargs = {}
    claude_md_root = getattr(args, "claude_md_root", None)
    if claude_md_root:
        sync_kwargs["claude_md_root"] = Path(claude_md_root)
    try:
        memory_ids = memory.sync(**sync_kwargs)
    except ProjectScopeDisagreementError as exc:
        # Same deliberate refusal shape as cmd_save: both values + remedy.
        _error("SCOPE_DISAGREEMENT", _scrub(str(exc)))
    _success({
        "sync_status": memory.last_sync_status,
        "projected": len(memory_ids),
        "memory_ids": memory_ids,
        "project_id": memory.project_id,
    })


def cmd_delete(args, db_path=None):
    """Handle the 'delete' subcommand.

    Accepts a full 32-char memory ID or a unique prefix. Ambiguous prefix
    refuses the delete and surfaces an AMBIGUOUS_PREFIX envelope.
    """
    memory = PACTMemory(db_path=db_path)
    try:
        resolved_id = memory.delete(args.memory_id)
    except ProjectScopeDisagreementError as exc:
        # Same deliberate refusal shape as cmd_save: both values + remedy.
        _error("SCOPE_DISAGREEMENT", _scrub(str(exc)))
    except PrefixTooShortError as exc:
        _error("PREFIX_TOO_SHORT", str(exc), minimum=exc.minimum)
    except AmbiguousPrefixError as exc:
        # Scrub user HOME from each match's `context` snippet so a memory
        # whose context recorded an absolute path doesn't leak it via the
        # disambiguation envelope. Per-site scrub keeps the redaction
        # obvious; do not centralize into `_error`.
        scrubbed_matches = [
            {**m, "context": _scrub(m["context"])} for m in exc.matches
        ]
        _error(
            "AMBIGUOUS_PREFIX",
            str(exc),
            prefix=exc.prefix,
            matches=scrubbed_matches,
            matches_capped=exc.matches_capped,
            total_matches=exc.total_matches,
        )
    if resolved_id is None:
        _error("NOT_FOUND", f"Memory '{args.memory_id}' not found")
    _success({"deleted": True, "memory_id": resolved_id})


def _positive_int(value):
    """Argparse type for positive integers. Rejects zero and negative values."""
    try:
        ivalue = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid int value: '{value}'")
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"--limit must be a positive integer, got {ivalue}")
    return ivalue


def _cli_examples(*lines: str) -> str:
    return "Examples:\n" + "\n".join(f"  {line}" for line in lines)


class _TeachParser(argparse.ArgumentParser):
    """Usage errors stay argparse exit 2 and append one pasteable example."""

    _teach_example = ""

    def error(self, message):
        self.print_usage(sys.stderr)
        extra = (
            f"\n\nExamples:\n  {self._teach_example}\n"
            if self._teach_example
            else "\n"
        )
        self.exit(2, f"{self.prog}: error: {message}{extra}")


def build_parser():
    """Build the argparse parser with all subcommands."""
    # Shared parent parser for the hidden --db-path flag.
    # Using a parent parser lets --db-path appear after any subcommand.
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--db-path",
        help=argparse.SUPPRESS,  # Hidden flag for testing
    )

    prog = sys.argv[0]
    fmt = argparse.RawDescriptionHelpFormatter
    # Interpreter-prefixed so a pasted example executes verbatim: the script
    # has no shebang or exec bit, so the bare path is not shell-executable.
    examples = {
        "save": f'python3 "{prog}" save --stdin',
        "search": f'python3 "{prog}" search "query"',
        "list": f'python3 "{prog}" list',
        "get": f'python3 "{prog}" get <memory-id>',
        "status": f'python3 "{prog}" status',
        "setup": f'python3 "{prog}" setup',
        "update": f'python3 "{prog}" update <memory-id> --stdin',
        "delete": f'python3 "{prog}" delete <memory-id>',
        "sync": f'python3 "{prog}" sync',
    }

    parser = _TeachParser(
        prog=prog,
        description="PACT Memory CLI — persistent memory for PACT agents",
        formatter_class=fmt,
        epilog=_cli_examples(*examples.values()),
    )
    parser._teach_example = examples["save"]

    subparsers = parser.add_subparsers(dest="command", parser_class=_TeachParser)

    # save
    save_parser = subparsers.add_parser(
        "save",
        help="Save a memory object",
        parents=[parent],
        formatter_class=fmt,
        epilog=_cli_examples(examples["save"]),
    )
    save_parser._teach_example = examples["save"]
    save_parser.add_argument("json_data", nargs="?", help="JSON memory object")
    save_parser.add_argument(
        "--stdin", action="store_true", help="Read JSON from stdin"
    )
    save_parser.add_argument(
        "--no-sync",
        action="store_true",
        help=(
            "Do not project this memory into CLAUDE.md's Working Memory. "
            "Use when the projection would undo the caller's purpose, e.g. "
            "archiving a pin that is about to be removed from CLAUDE.md."
        ),
    )
    save_parser.add_argument(
        "--claude-md-root",
        default=None,
        help=(
            "Declare the directory the CLAUDE.md write must stay inside. The "
            "write is refused if it would land outside. This does NOT choose "
            "which CLAUDE.md is written -- resolution is unchanged -- it "
            "bounds where the result may be."
        ),
    )

    # search
    search_parser = subparsers.add_parser(
        "search",
        help="Search memories",
        parents=[parent],
        formatter_class=fmt,
        epilog=_cli_examples(examples["search"]),
    )
    search_parser._teach_example = examples["search"]
    search_parser.add_argument("query", help="Search query text")
    search_parser.add_argument(
        "--limit", type=_positive_int, default=5, help="Max results (default: 5)"
    )
    # PLUMBED EVEN THOUGH THE SEARCH PATH CANNOT SYNC TODAY. `cmd_search`
    # passes `sync_to_claude=False`, so this is inert right now -- which is
    # exactly why it is here. That suppression is an observation about one call
    # site, not a property of the command; the day it changes, the anchor is
    # already available rather than needing to be discovered as missing.
    search_parser.add_argument(
        "--claude-md-root",
        default=None,
        help=(
            "Declare the directory a Retrieved Context write must stay inside. "
            "Inert while the search path suppresses its sync."
        ),
    )
    search_parser.add_argument(
        "--current-file", help="Current file path for graph-enhanced relevance boosting"
    )

    # list
    list_parser = subparsers.add_parser(
        "list",
        help="List recent memories",
        parents=[parent],
        formatter_class=fmt,
        epilog=_cli_examples(examples["list"]),
    )
    list_parser._teach_example = examples["list"]
    list_parser.add_argument(
        "--limit", type=_positive_int, default=20, help="Max results (default: 20)"
    )

    # get
    get_parser = subparsers.add_parser(
        "get",
        help="Get a memory by full ID or unique prefix (>= 7 chars)",
        description=(
            "Retrieve a memory by its full 32-char ID or a unique prefix of "
            "at least 7 characters. A unique prefix returns the matching "
            "memory; an ambiguous prefix returns an AMBIGUOUS_PREFIX error "
            "with a capped list of matching IDs (matches_capped/"
            "total_matches fields indicate when the cap was applied); "
            "a prefix shorter "
            "than 7 characters returns a PREFIX_TOO_SHORT error; no match "
            "returns NOT_FOUND. Prefix is case-insensitive."
        ),
        parents=[parent],
        formatter_class=fmt,
        epilog=_cli_examples(examples["get"]),
    )
    get_parser._teach_example = examples["get"]
    get_parser.add_argument(
        "memory_id",
        help="Full 32-char memory ID, or a unique prefix of >= 7 characters",
    )

    # status
    status_parser = subparsers.add_parser(
        "status",
        help="Show memory system status",
        parents=[parent],
        formatter_class=fmt,
        epilog=_cli_examples(examples["status"]),
    )
    status_parser._teach_example = examples["status"]

    # setup
    setup_parser = subparsers.add_parser(
        "setup",
        help="Initialize the memory system",
        parents=[parent],
        formatter_class=fmt,
        epilog=_cli_examples(examples["setup"]),
    )
    setup_parser._teach_example = examples["setup"]

    # update
    update_parser = subparsers.add_parser(
        "update",
        help="Update a memory by full ID or unique prefix (>= 7 chars)",
        description=(
            "Update an existing memory by its full 32-char ID or a unique "
            "prefix of at least 7 characters. An ambiguous prefix is refused "
            "(AMBIGUOUS_PREFIX error with a capped match list); a prefix "
            "shorter than 7 characters returns PREFIX_TOO_SHORT; no match "
            "returns NOT_FOUND. Prefix is case-insensitive."
        ),
        parents=[parent],
        formatter_class=fmt,
        epilog=_cli_examples(examples["update"]),
    )
    update_parser._teach_example = examples["update"]
    update_parser.add_argument(
        "memory_id",
        help="Full 32-char memory ID, or a unique prefix of >= 7 characters",
    )
    update_parser.add_argument("json_data", nargs="?", help="JSON with fields to update")
    update_parser.add_argument(
        "--stdin", action="store_true", help="Read JSON from stdin"
    )
    update_parser.add_argument(
        "--replace",
        action="store_true",
        help=(
            "Replace list-valued fields wholesale instead of merging "
            "additively (default: additive merge with content-hash dedup). "
            "Use when you intentionally want to remove items from a list."
        ),
    )

    # delete
    delete_parser = subparsers.add_parser(
        "delete",
        help="Delete a memory by full ID or unique prefix (>= 7 chars)",
        description=(
            "Delete a memory by its full 32-char ID or a unique prefix of "
            "at least 7 characters. An ambiguous prefix is refused "
            "(AMBIGUOUS_PREFIX error with a capped match list); a prefix "
            "shorter than 7 characters returns PREFIX_TOO_SHORT; no match "
            "returns NOT_FOUND. Prefix is case-insensitive."
        ),
        parents=[parent],
        formatter_class=fmt,
        epilog=_cli_examples(examples["delete"]),
    )
    delete_parser._teach_example = examples["delete"]
    delete_parser.add_argument(
        "memory_id",
        help="Full 32-char memory ID, or a unique prefix of >= 7 characters",
    )

    # sync
    sync_parser = subparsers.add_parser(
        "sync",
        help="Rebuild CLAUDE.md's Working Memory from this project's newest memories",
        description=(
            "Replace the Working Memory section of CLAUDE.md with this "
            "project's newest memories, each under its own date. Stateless: "
            "the section is a view of the store, so fix a record with update "
            "or delete and run sync again. A project with no memories reports "
            "sync_status 'empty' and leaves the file untouched."
        ),
        parents=[parent],
        formatter_class=fmt,
        epilog=_cli_examples(examples["sync"]),
    )
    sync_parser._teach_example = examples["sync"]
    sync_parser.add_argument(
        "--claude-md-root",
        default=None,
        help=(
            "Declare the directory the CLAUDE.md write must stay inside. The "
            "write is refused if it would land outside. This does NOT choose "
            "which CLAUDE.md is written -- resolution is unchanged -- it "
            "bounds where the result may be."
        ),
    )

    return parser


# Dispatch table mapping command names to handler functions
_COMMANDS = {
    "save": cmd_save,
    "search": cmd_search,
    "list": cmd_list,
    "get": cmd_get,
    "status": cmd_status,
    "setup": cmd_setup,
    "update": cmd_update,
    "delete": cmd_delete,
    "sync": cmd_sync,
}

# THE COMMANDS THAT MAY BRING A STORE INTO EXISTENCE AT A CALLER PATH.
#
# DECLARED AS A SET RATHER THAN TESTED AS A STRING, so the exemption has one
# name and a future author who adds a second command must justify the addition
# rather than widen a comparison in passing.
#
# ⚠️ EACH OTHER COMMAND CREATES A SCHEMA ON AN ABSENT STORE TODAY, so this set
# is NOT a description of which commands can create. `database.ensure_initialized`
# builds the schema for `save`, `get` and the rest. This set states which
# command is ALLOWED to, at a path a caller typed. Read it as a rule and not as
# a summary of the code below it.
_COMMANDS_THAT_MAY_CREATE_A_CALLER_PATH = frozenset({"setup"})


def main(argv=None):
    """
    CLI entry point. Parses arguments and dispatches to the appropriate
    command handler.

    Args:
        argv: Optional argument list (defaults to sys.argv[1:]).
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help(sys.stderr)
        sys.exit(1)

    handler = _COMMANDS.get(args.command)
    if handler is None:
        _error("UNKNOWN_COMMAND", f"Unknown command: {args.command}")

    db_path = Path(args.db_path) if args.db_path else None

    # Checked AFTER the falsy coercion above, deliberately: `--db-path ""`
    # collapses to None there, so guarding the coerced value covers the empty
    # string on the same branch as an omitted flag rather than needing a
    # second predicate for it.
    _refuse_live_db_under_pytest(db_path)

    # THE SCOPE ENTERS AFTER THE REFUSAL GUARD, AND THE ORDER IS LOAD-BEARING.
    # The guard keys on `db_path is None` and must read the caller's own
    # argument. Bind the store first and a later reader of the resolver sees a
    # path where the caller supplied none, which is how a guard gets disarmed by
    # a change that looks unrelated to it. An assertion in the test suite pins
    # this order.
    #
    # ONE SCOPE COVERS ALL EIGHT HANDLERS. That is what repairs the three legs
    # of `setup` together: the leg that CREATES the directory and the leg that
    # REPORTS it both reach `get_memory_dir()` with no argument, so neither one
    # could honour `--db-path` while only the schema leg took a parameter.
    # THE STDERR GUARD WRAPS THE SCOPE, NOT THE OTHER WAY ROUND, so that
    # anything the store scope reaches can write to stderr without reaching
    # the channel the error envelope leaves on.
    try:
        with _own_stderr_for_envelope():
            # ⚠️ A CALLER PATH IS OPENED, NEVER BROUGHT INTO EXISTENCE. THE FILE
            # HALF OF THE PATH REFUSAL LIVES HERE.
            #
            # A path a caller TYPED is a spelling somebody chose, so an absent
            # store at that path is a TYPO, and the correct answer to a typo is
            # to fail. `--db-path` aimed at a directory that is present with a
            # mistyped FILE NAME used to build a store and report an ordinary
            # result, which put a throwaway store inside the live store
            # directory. An archive then landed in a database about to be
            # discarded, while the pin it came from became eligible for delete.
            #
            # WHY THIS BOUNDARY AND NOT `database.get_connection`. That location
            # was built and rejected. A refusal there reaches EACH caller of the
            # connection factory, which breaks the custom-store contract that
            # production and the test suite depend on, and `get_connection`
            # cannot tell a person from a library caller. Here the command name
            # is a FACT on `args`, so the decision reads something rather than
            # infers it.
            #
            # THE DERIVED ROUTE IS UNTOUCHED, AND THAT OUTRANKS THE REFUSAL. A
            # caller that passes no `--db-path` never reaches this branch, so an
            # environment-derived or home-derived store still creates on its
            # first run. `config.DERIVED_STORE_ORIGINS` carries that rule.
            #
            # RESIDUAL 1, STATED RATHER THAN IMPLIED: a library caller that
            # passes a mistyped path stays uncovered here. The accepted reason
            # is that a library caller is code, and code does not typo.
            #
            # RESIDUAL 2, AND IT IS AN EXCEPTION TO THE RULE ABOVE. `--db-path
            # ""` is a caller path that is not a store, and it is NOT refused
            # here. The falsy coercion further up collapses it to None before
            # this branch reads it, so it takes the DERIVED route. That
            # coercion is deliberate and stays. Naming the exception is what
            # keeps the rule honest: the refusal covers a caller path that
            # ARRIVES as a path, not the empty string.
            #
            # RESIDUAL 3: a caller path that names a DIRECTORY is present to
            # this test, so it is not refused here. It fails later, loudly and
            # without a create, as SYSTEM_ERROR from sqlite. A tighter test
            # (`is_file`) would give it the named refusal, and it would also
            # redden two arms that pin SYSTEM_ERROR for that input, so the
            # loud-and-non-destructive answer stays.
            #
            # ⚠️ THE MESSAGE STATES THE OBSERVATION, NOT AN INFERENCE FROM IT,
            # for the same reason the guard above does. `Path.exists()` answers
            # False for a path that is ABSENT and for a path this process
            # cannot STAT, so an unreadable parent directory reads the same as
            # a typo. A message that asserts absence tells a caller with a
            # permission fault to run `setup`, which does not repair one.
            if (
                db_path is not None
                and args.command not in _COMMANDS_THAT_MAY_CREATE_A_CALLER_PATH
                and not db_path.exists()
            ):
                shown = _scrub(str(db_path))
                _error(
                    "DB_PATH_NOT_FOUND",
                    f"--db-path '{shown}' did not answer as a store that is "
                    f"present. The test is `Path.exists()`, which answers "
                    f"False for a path that is absent AND for a path this "
                    f"process cannot stat, so a permission fault on a parent "
                    f"directory reads the same way. That one fact is the "
                    f"whole of what it observed. This command opens a store "
                    f"that is present and does not bring one into existence. "
                    f"If the path is a typo, correct it. If the store should "
                    f"be there, check that this process can read the parent "
                    f"directory. To bring a store into existence at that "
                    f"path, run: setup --db-path '{shown}'",
                )

            with store_scope(db_path):
                handler(args, db_path=db_path)
    except SystemExit:
        raise  # Let _success/_error exits propagate
    except Exception as exc:
        # Scrub the user's home directory (both the literal expansion and
        # the realpath form) from the message so absolute paths
        # (e.g. ~/.claude/pact-memory/... — illustrative example, not a live path) don't leak into stderr for
        # callers piping the JSON envelope into logs.
        _error("SYSTEM_ERROR", _scrub(str(exc)), exit_code=2)


if __name__ == "__main__":
    # THE NEUTRALISE STEP BELONGS HERE AND NOT INSIDE `main()`. This block runs
    # only when a shell started this file, which is the only case where the
    # process is about to end and a rebind of a standard descriptor harms
    # nobody. `main()` is ALSO called in-process by the unit tests of this CLI,
    # and there the same rebind would point a descriptor of the TEST RUNNER at
    # the null device and silence the remainder of that run.
    #
    # `finally` AND NOT AN `except`: `main()` leaves through SystemExit on every
    # path, success and failure alike, so an except clause for one of them would
    # cover half the exits.
    try:
        main()
    finally:
        _neutralise_unwritable_std_streams()
