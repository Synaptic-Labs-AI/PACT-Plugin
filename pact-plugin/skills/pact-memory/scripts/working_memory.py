"""
Working Memory Sync Module

Location: pact-plugin/skills/pact-memory/scripts/working_memory.py

Summary: Handles synchronization of memories to the Working Memory section
in CLAUDE.md. Maintains a rolling window of the most recent memories for
quick reference during Claude sessions. Applies token budgets to prevent
unbounded growth of memory sections.

Used by:
- memory_api.py: Calls sync_to_claude_md() after saving memories
- Test files: test_working_memory.py tests all functions in this module
"""

from __future__ import annotations

import errno
import fcntl
import logging
import os
import re
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# THE STORE ORIGIN IS ASKED FOR, NEVER RE-DERIVED. `_refuse_ambient_sync_from_a_
# redirected_store` must know if the row it is about to project went to the
# DEFAULT store or to a redirected one. Reading `PACT_TEST_MEMORY_DIR` here
# instead would be a second derivation of a rule `config` owns, and it would be
# BLIND to the `--db-path` store scope, which is the other redirect route.
#
# Dual import: relative (when loaded as a package) vs absolute (when a caller
# adds scripts/ to sys.path). This module is loaded BOTH ways -- the package
# imports it as `.working_memory`, and callers that put scripts/ on the path
# import it bare -- so a relative-only import would break the bare route. Same
# idiom and same reason as the pact_session import in memory_api.
try:
    from .config import STORE_ORIGIN_HOME, store_path_origin
except ImportError:
    from config import STORE_ORIGIN_HOME, store_path_origin

# Same dual-import idiom as the config import above. pact_session carries the
# sys.path bootstrap that makes hooks/shared importable from this package (the
# precedent the amended twin comments below now cite), and holds the
# session-record project_dir rung plus the env/record disagreement refusal
# this module's resolvers and sync guard consume.
try:
    from .pact_session import (
        ProjectScopeDisagreementError,
        env_record_project_dir_disagreement,
        format_project_dir_disagreement,
        get_project_dir_from_session_record,
        get_worktree_identity_from_session_record,
    )
except ImportError:
    from pact_session import (
        ProjectScopeDisagreementError,
        env_record_project_dir_disagreement,
        format_project_dir_disagreement,
        get_project_dir_from_session_record,
        get_worktree_identity_from_session_record,
    )

# Configure logging
logger = logging.getLogger(__name__)

# Constants for working memory section (saved memories).
# Working Memory provides structured, PACT-specific context (goals, decisions,
# lessons) synced from the SQLite database. It coexists with the platform's
# auto-memory (MEMORY.md), which captures free-form session learnings. Reduced
# from 5 to 3 entries to limit token overlap between the two systems while
# retaining the structured format that auto-memory does not provide.
WORKING_MEMORY_HEADER = "## Working Memory"
# THE COUNT CLAUSE WAS REMOVED BECAUSE IT WAS FALSE IN THE COMMON REGIME, NOT
# BECAUSE IT WAS UNTIDY. `_apply_token_budget` never compresses `entries[0]`
# and its drop loop is `while len(result) > 1`, so when the newest entry ALONE
# exceeded the whole-section budget the older entries were dropped and the
# section showed ONE entry. This string is written INTO the artifact it
# describes, so every agent loading a CLAUDE.md read the false claim inline,
# beside a section that often held a single entry.
#
# THAT ONE-ENTRY REGIME IS NOW CLOSED, AND THE COUNT CLAUSE STAYS OUT ANYWAY.
# `_apply_entry_token_ceiling` bounds each entry so that the newest one
# cannot exhaust the section alone, and the per-field character bound in
# `_format_memory_entry` puts a typical full entry far below the budget. A
# fixed count is still the wrong thing to promise: the cap is a CAP, the
# store can hold fewer entries than it, and a promise here would go stale the
# next time either bound moves.
#
# WHAT REPLACED IT IS UNCONDITIONAL. The searchability clause is TRUE in every
# regime and is kept: it is the clause that tells a reader where the durable
# copy lives. Deleting the whole comment would have removed a true, useful
# statement along with the false one.
#
# DO NOT "RESTORE" A COUNT, and do not replace it with "entries are not
# addressable by ID" either -- that is a NEW false claim in the other
# direction, because only OLDER entries lose their ID. The newest entry is
# always full and always carries its Memory ID.
#
# MIRRORED IN ONE OTHER DEFINITION -- `hooks/shared/claude_md_manager.py`, the
# only other file that spells this string. Change both in ONE commit: fixing
# one converts a consistent statement into a disagreement.
# `hooks/shared/session_resume.py` IMPORTS the name and must not be given a
# definition of its own; the mirror gate names it the importer for that reason.
WORKING_MEMORY_COMMENT = "<!-- Auto-managed by pact-memory skill. Full history searchable via pact-memory skill. Keyed by folder name, so another checkout with the same name shares this section. -->"
MAX_WORKING_MEMORIES = 3

# Constants for retrieved context section (searched/retrieved memories)
RETRIEVED_CONTEXT_HEADER = "## Retrieved Context"
RETRIEVED_CONTEXT_COMMENT = "<!-- Auto-managed by pact-memory skill. Last 3 retrieved memories shown. -->"
MAX_RETRIEVED_MEMORIES = 3

# Token budget constants.
# Approximation: 1 token ~ 0.75 words, so word_count * 1.3 ~ token count.
WORKING_MEMORY_TOKEN_BUDGET = 800
RETRIEVED_CONTEXT_TOKEN_BUDGET = 500
# Note: PINNED_CONTEXT_TOKEN_BUDGET is defined solely in hooks/staleness.py

# Maximum characters `_compress_memory_entry` keeps of a summary before it
# appends "...". NAMED BECAUSE THE BARE LITERAL HAD A DECOY. This value was
# spelled 120 at five sites in `_compress_memory_entry`, while
# OVERRIDE_RATIONALE_MAX below is a DIFFERENT 120 that bounds an override
# rationale. A reader who greps the literal to find the source of the
# compressed-entry arithmetic can bind to the override cap, and the two
# values AGREE, so nothing goes red and the mistake is invisible. The name
# is the fix: COMPRESSED_ENTRY_TOKEN_CEILING derives from THIS constant.
COMPRESSED_SUMMARY_CHAR_CAP = 120

# Token cost of ONE compressed neighbour, at its maximum.
#
# THIS IS AN ESTIMATE AND IT IS LABELLED ONE DELIBERATELY. It rests on
# THREE premises, and a change to any of them moves it: the summary cap is
# COMPRESSED_SUMMARY_CHAR_CAP plus the 3 characters of the truncation
# marker; a memory id is bounded at _REFRESH_IDENTIFIER_TRUNCATION_LIMIT
# characters; and the worst-case density is 2 characters for each word
# ROUNDED DOWN, which is one character plus one space and is the densest
# input `str.split()` can meet. THE DIRECTION OF THE ROUND IS PART OF THE
# RULE AND IT IS REPEATED AT EACH RESTATEMENT, because a reader who meets
# this premise alone would otherwise hold the rule without its direction.
#
# COUNTING RULE, AND IT STATES ITS ROUNDING BECAUSE THE ARITHMETIC IS ODD:
# measure the ASSEMBLED three-line compressed form, being the date header,
# the `**Summary**` line at the cap, and the `**Memory ID**` line. At 2
# characters for each word, ROUNDED DOWN, 123 characters gives 61 words
# rather than 61.5. A reader who rounds UP reproduces none of the numbers
# here, so the direction is part of the rule. MEASURED: 128.
#
# THE VARIABLE IS DENSITY AND NOT LENGTH, WHICH IS THE WHOLE CAUSE OF THIS
# CONSTANT MOVING. A character bound cannot enforce a token budget, because
# the producer of the value controls the ratio. At the 64-character
# identifier bound, a DENSE id costs 128 and a one-token id costs 88, from
# the same character count. The 128 is the dense case, so the bound holds
# for the adversarial shape rather than for the friendly one.
#
# DO NOT DERIVE THIS BY ADDING PARTS. The estimator applies `int()` ONCE
# to the word count of the WHOLE string, so two separately rounded parts
# do not sum to the rounded whole.
#
# This is the per-entry cost the SITE A ceiling reserves for the two
# neighbours it compresses; see `_apply_token_budget`.
COMPRESSED_ENTRY_TOKEN_CEILING = 128

# The line prefix that carries the pointer to the durable record.
#
# NAMED BECAUSE FOUR EXECUTABLE SITES MUST AGREE, AND A RENAME AT SOME OF
# THEM IS A SILENT DEFECT. Two sites WRITE the line
# (`_format_memory_entry` and `_format_retrieved_entry`). Two sites READ it
# by prefix: `_compress_memory_entry` keeps it, and
# `_apply_entry_token_ceiling` holds it out of the cut.
#
# THAT EXCLUSION FROM THE CUT IS THE PROPERTY THE CUT RULE RESTS ON. This
# design accepts truncation rather than refusal ONLY WHILE the recovery
# pointer survives the cut. So a rename at the two writers without the two
# readers, or the opposite, makes the id line droppable again: THE
# RECOVERY ROUTE GOES, nothing raises and nothing reddens. One name for
# the four sites makes that silent rename not possible.
#
# THE VALUE CARRIES NO COLON, because the readers test a PREFIX and the
# writers append `: ` and the value.
_MEMORY_ID_LABEL = "**Memory ID**"

# Pin caps constants (twin copy of hooks/pin_caps.py — the import IS possible
# but requires the sys.path bootstrap pact_session.py in this directory
# carries; the drift-gated twin remains the chosen mechanism here).
# Drift-detection test in
# tests/test_staleness.py guards against divergence; if you change these,
# update hooks/pin_caps.py in the SAME commit.
#
# Forward-looking drift anchors: no skill-side code currently consumes these
# constants — they exist here solely so a future skills-side pin-cap
# consumer can read the budget without needing to cross the package
# boundary. Anchored only by TestPinCapsTwinCopyDrift. Do NOT remove
# even if unused at read time; the drift test + forward-compat intent are
# the justification for the twin copy.
PIN_COUNT_CAP = 12
PIN_SIZE_CAP = 1500
PIN_STALE_BLOCK_THRESHOLD = 2
OVERRIDE_RATIONALE_MAX = 120

# PACT-managed boundary marker prefixes. Used by _find_terminator_offset to
# terminate section scans on any PACT boundary marker. The canonical
# definition lives in hooks/shared/claude_md_manager.py as
# PACT_BOUNDARY_PREFIXES — importing it would require the sys.path bootstrap
# pact_session.py in this directory carries, so the alternation is inlined
# here instead. The three prefixes rarely change; if a 4th is added, update
# this string.
_PACT_BOUNDARY_ALT = "PACT_MEMORY_|PACT_MANAGED_|PACT_ROUTING_"

# Session-block boundary marker prefix, and it is deliberately NOT a member of
# PACT_BOUNDARY_PREFIXES. That set is canonical in
# hooks/shared/claude_md_manager.py, a drift gate holds the copy above equal to
# it, and the SESSION markers carry no PACT_ prefix. A SESSION member in a set
# named for PACT_ prefixes makes the name incorrect about its own contents, so
# the scans below embed this alternation WITH _PACT_BOUNDARY_ALT and not in it.
#
# WHAT IT DEFENDS, MEASURED. The session block sits in the managed region
# ABOVE the memory markers. When the memory marker pair is absent, the window
# below falls back to the wide managed region, and a forged section heading in
# the session block wins the first-match search. The body scan then runs
# THROUGH the session-end marker line, and the rebuild replaces that span, so
# the marker is gone from the emitted document. CLAUDE.md is not a tracked
# file, so no commit can restore it.
_SESSION_BOUNDARY_ALT = "SESSION_"

# The session-end marker line, DERIVED from the prefix above rather than
# spelled again. A drift gate holds that prefix equal to its canonical
# source, so a rename of the canonical marker reaches this line through the
# gate. A literal here would be a twin with no gate, which is the shape this
# branch keeps removing.
_SESSION_END_MARKER = f"<!-- {_SESSION_BOUNDARY_ALT}END -->"

# Managed-region boundary markers. Twin copies of the canonical definitions
# in hooks/shared/claude_md_manager.py (the import would require the sys.path
# bootstrap pact_session.py in this directory carries; the drift-gated twin
# remains the chosen mechanism here).
_MANAGED_START_MARKER = "<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->"
_MANAGED_END_MARKER = "<!-- PACT_MANAGED_END -->"

# The INNER memory-region boundary, nested in the managed region above.
#
# THESE TWO CARRY THE CANONICAL NAMES AND NOT THE LOCAL PRIVATE PREFIX, AND
# THAT IS A DELIBERATE DEPARTURE FROM THE TWO LINES ABOVE. The prefix is a
# choice made at some sites here and not at others: `MAX_WORKING_MEMORIES`
# and `WORKING_MEMORY_TOKEN_BUDGET` carry none. `_narrow_to_memory_region`
# below reads these two names IN ITS BODY, and its body is byte-compared
# against the canonical copy by a drift gate. A prefix here puts a
# difference in that body, and the gate would go RED ON ARRIVAL on a choice
# somebody made rather than on divergence.
#
# `extract_managed_region` records the opposite call for its own twin, and
# the difference is the GATE rather than the taste: that one is not
# byte-compared, so its local names cost nothing.
MEMORY_START_MARKER = "<!-- PACT_MEMORY_START -->"
MEMORY_END_MARKER = "<!-- PACT_MEMORY_END -->"

# file_lock: vendored twin of hooks/shared/claude_md_manager.file_lock —
# the import IS possible via the sys.path bootstrap pact_session.py in this
# directory carries; the drift-gated twin remains the chosen mechanism here.
# Cross-process correctness is preserved because
# fcntl.flock serializes on the sidecar inode, not the Python object: a hook
# process and this skill process locking the SAME .{name}.lock sidecar
# contend on the same kernel lock. The drift-detection test
# (TestFileLockTwinCopyDrift in tests/test_staleness.py) guards byte-alignment
# of the function body with the canonical copy; if you change either, update
# both in the SAME commit. The two constants below are part of the twin and
# must match the canonical values.
_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_POLL_INTERVAL = 0.1

# _sanitize_prompt_field: vendored twin of
# hooks/shared/session_resume._sanitize_prompt_field — the import IS possible
# via the sys.path bootstrap pact_session.py in this directory carries; the
# drift-gated twin remains the chosen mechanism here.
# The drift-detection test (TestSanitizePromptFieldTwinCopyDrift in
# tests/test_staleness.py) guards byte-alignment of the function body with
# the canonical copy; if you change either, update both in the SAME commit.
# The three values below are part of the twin and must match the canonical
# ones, which is what test_sanitize_prompt_field_constants_match asserts.
#
# Bounds for record field values interpolated into the managed regions of
# CLAUDE.md. The store is plain SQLite on disk and a field value is
# caller-influenced, so a hand-crafted or corrupted record must not be able
# to open a heading inside a PACT-managed region or flood the always-loaded
# context. Free-text fields get the tight bound; paths get a wider one
# because legitimate absolute paths can be long.
_REFRESH_FIELD_TRUNCATION_LIMIT = 200
_REFRESH_PATH_TRUNCATION_LIMIT = 512

# IDENTIFIER is a THIRD field kind, and its absence was a defect rather
# than an omission. A memory id took the FREE-TEXT bound of 200, which is
# 3 times what the generator emits and lets one field dominate the token
# cost of a compressed entry. The store does NOT bound this value: the
# ingress validates the KEY SET of a record rather than the length of a
# value, so a caller-supplied id reaches the formatter unbounded.
# 64 covers a 32-character generated id with double the margin.
#
# CLASSIFY BY FIELD KIND, NOT BY DEFAULT. A field with no row in the
# classification falls to free text, and free text is the WIDEST bound, so
# a missing row always errs toward the loose end.
_REFRESH_IDENTIFIER_TRUNCATION_LIMIT = 64

# Control characters collapsed in interpolated field values: C0 controls
# (includes \n, \r, \t), DEL plus the full C1 block (which includes NEL
# U+0085 — a str.splitlines boundary), and the Unicode line/paragraph
# separators — anything that could break a value onto a new line and
# masquerade as a heading or a separate entry.
#
# 🔴 A THIRD CLASS EXISTS AND IT IS NARROWER ON PURPOSE. DO NOT MERGE THEM.
# `hooks/shared/session_state.SESSION_ID_CONTROL_CHARS_RE` covers the same
# line breakers and omits the non-line-breaking C1 characters. The two do
# different jobs: that one is a DETECTOR, used only through `.search()` on
# identifiers, so it carries no `+` and needs none. THIS one is a REPLACER,
# used through `.sub(" ", value)`, and HERE THE `+` IS LOAD-BEARING: without
# it a run of N control characters becomes N spaces rather than one. Widening
# that one to match this one would refuse session ids over characters that
# break no line.
_PROMPT_CONTROL_CHARS_RE = re.compile("[\\x00-\\x1f\\x7f-\\x9f\\u2028\\u2029]+")


@contextmanager
def file_lock(target_file: Path):
    """Acquire an exclusive sidecar file lock for a target CLAUDE.md path.

    Twin of hooks/shared/claude_md_manager.file_lock — kept local as a
    drift-gated twin; importing the canonical would require the sys.path
    bootstrap pact_session.py in this directory carries. Body MUST
    stay byte-identical to the canonical copy (drift test enforces this).

    NOT RE-ENTRANT: fcntl.flock is non-re-entrant at the OS level. Nesting one
    sync site inside another under the SAME target would self-deadlock until the
    fail-open TimeoutError (after _LOCK_TIMEOUT_SECONDS). This is not reachable
    on the current call graph — the two sync sites are independent top-level
    calls, never nested — so no behavioral re-entrancy guard is added (a guard
    would alter this body and trip the drift test; the OS-level non-re-entrancy
    plus the callers' fail-open already bound the worst case).
    """
    # Key the sidecar on the DIRECTORY THE WRITE BINDS INTO plus the LITERAL
    # leaf name, so lock identity and write identity are the same thing and
    # cannot diverge when the write replaces the leaf entry.
    # The PARENT is resolved so two spellings of one directory produce one
    # sidecar, and therefore one lock. The LEAF is deliberately NOT resolved:
    # os.replace is renameat(2) and binds the final component as a directory
    # ENTRY without following it, so resolving the leaf would key the lock on a
    # path the write never touches -- and would make the key CHANGE across the
    # write, which is a lock whose identity is a function of the state it is
    # supposed to protect. Mirrors the write's own os.open(target.parent) +
    # target.name; see the write-shape dependency noted in _atomic_write_text.
    # NOT provided, deliberately: two NAMES for one INODE do not collapse onto
    # one sidecar. A hardlink pair is exactly that and never collapsed under
    # any spelling of this formula, because resolve() canonicalises symlinks
    # and not inodes -- the justification this comment replaced claimed
    # otherwise and was wrong about its own code.
    lock_path = target_file.parent.resolve() / f".{target_file.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # 0o600: the lock file is adjacent to user-private CLAUDE.md content;
    # match the same permissions to avoid leaving a world-readable sidecar.
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    try:
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    # S8 (security-engineer-review): emit a stderr
                    # warning before raising. Callers fail-open on
                    # TimeoutError (skip the cleanup pass), so without
                    # this warning a stuck holder would silently defer
                    # kernel-block / managed-block cleanup forever.
                    # Stderr from hooks does not surface in the user
                    # transcript, but it does land in Claude Code's
                    # debug logs — repeated warnings make the
                    # contention-vs-bug class observable.
                    print(
                        f"PACT file_lock timeout: failed to acquire "
                        f"lock on {lock_path} within "
                        f"{_LOCK_TIMEOUT_SECONDS}s; falling open",
                        file=sys.stderr,
                    )
                    raise TimeoutError(
                        f"Failed to acquire lock on {lock_path} within "
                        f"{_LOCK_TIMEOUT_SECONDS}s"
                    )
                time.sleep(_LOCK_POLL_INTERVAL)
        yield
    finally:
        # Release before close. flock is released automatically on fd close
        # by the kernel, but an explicit LOCK_UN ensures immediate release
        # even if close is delayed (e.g., by subsequent finalizer work).
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


# Why the sync sites lock the WHOLE read->mutate->write window (not just the
# write). This rationale is shared by both sync_to_claude_md and
# sync_retrieved_to_claude_md, which each carry only a short pointer back here.
# (Distinct from the "why an inline twin" note above: that explains WHY the lock
# is vendored; this explains WHY the lock spans the whole window.)
#   - read-under-lock is the load-bearing no-clobber property: a write-only lock
#     would let a 2nd writer read stale (pre-this-write) content, mutate it, and
#     overwrite this writer's entry the instant the lock releases — the exact
#     lost update the lock exists to prevent.
#   - lock identity is the sidecar inode of the RESOLVED CLAUDE.md path, so this
#     serializes against session_init / session_resume: all writers resolve to
#     the same .claude/CLAUDE.md (CLAUDE_PROJECT_DIR is set every session → all
#     hit the env-var branch first) and thus share one .CLAUDE.md.lock sidecar.
#   - CLAUDE_PROJECT_DIR edge: if it were ever unset AND the git-root/cwd
#     fallbacks diverged between processes, the sidecars would differ and the
#     lock would not serialize — accepted as out-of-contract (no safe fallback
#     action exists if the paths genuinely diverge).


class ContainmentError(OSError):
    """A CLAUDE.md write target escaped its project containment boundary (#1247).

    Subclasses OSError so a caller that does not name it explicitly still
    catches it via `except OSError`. Callers convert it to an OPAQUE skip
    message that does not leak the resolved victim path.

    Twin of ContainmentError in `hooks/shared/claude_md_manager.py` (importing
    it would require the sys.path bootstrap pact_session.py in this directory
    carries; the twin remains the chosen mechanism). The two class defs are
    trivial markers;
    the load-bearing logic is the containment CHECK inside `_atomic_write_text`,
    drift-gated by TestAtomicWriteTwinCopyDrift.
    """


def _detect_line_ending(name: str, parent_fd: int) -> str:
    """
    Report the line ending `name` uses today, read THROUGH `parent_fd`.

    READ THE BYTES, BECAUSE EVERY TEXT READ HAS ALREADY LOST THE ANSWER.
    `Path.read_text` applies universal-newline translation, so a CRLF file
    arrives as LF and the original ending is unrecoverable from the string a
    caller holds. This is the only place that looks.

    IT TAKES A DESCRIPTOR AND A NAME RATHER THAN A PATH, AND THAT IS THE WHOLE
    POINT OF THE SIGNATURE. `_atomic_write_text` pins the parent directory open
    and binds the write through that descriptor. A read by name inside it would
    reintroduce the name-based race the descriptor design removes, so this
    samples the same kernel object the containment walk approved.

    DOMINANT WINS, AND A TIE GOES TO LF. The write this feeds is unrecoverable,
    because CLAUDE.md is gitignored, so the correct rule is the one that changes
    the fewest lines. Dominant-wins minimises that count by construction. A file
    with no CRLF at all has a dominant of LF, so no file can gain an ending it
    did not have. A file written only with bare carriage returns reports LF.

    A TARGET THAT IS NOT ON DISK REPORTS LF, and that arm is load-bearing rather
    than defensive: it is why file creation is not a gap. A create-only caller
    writes its LF template to a name that is not there, so nothing converts.
    A read failure reports LF for the same reason.

    Twin copy: the canonical definition is in `hooks/shared/claude_md_manager.py`;
    importing it would require the sys.path bootstrap pact_session.py in this
    directory carries, the same consideration that keeps the `file_lock` and
    `_atomic_write_text` twins. The two bodies are gated identical by
    TestLineEndingHelperTwinCopyDrift.

    Args:
        name: The leaf name of the target, resolved against `parent_fd`.
        parent_fd: An open descriptor for the directory the write binds into.

    Returns:
        Either the two-character CRLF sequence or a single newline.
    """
    try:
        fd = os.open(name, os.O_RDONLY, dir_fd=parent_fd)
    except (OSError, NotImplementedError):
        return "\n"
    try:
        chunks = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
    except OSError:
        return "\n"
    finally:
        os.close(fd)
    raw = b"".join(chunks)
    crlf_count = raw.count(b"\r\n")
    lf_count = raw.count(b"\n") - crlf_count
    return "\r\n" if crlf_count > lf_count else "\n"


def _restore_line_ending(content: str, line_ending: str) -> str:
    """
    Apply `line_ending` to `content`, whatever endings `content` arrives with.

    IT NORMALISES FIRST, AND IT IS NOT SAFE WITHOUT THAT. An earlier form
    recorded a PRECONDITION that content reaches it with no carriage return in
    it, and applied a plain replace. That held while one caller fed it. At this
    seam the callers are ten and the seam cannot see where their content came
    from, so the precondition is a claim about callers rather than a property of
    this function. A plain replace on a string that carries CRLF gives a doubled
    carriage return, which is corruption of the user's own file.

    SO CRLF GOES BACK TO LF FIRST, AND THEN THE ENDING GOES ON. A caller that
    restores for itself is then a no-op rather than a corruption, which is a
    defect this function makes harmless rather than one it hides. The LF branch
    keeps its early return, so an LF file is byte-identical to what this wrote
    before.

    Twin copy: the canonical definition is in `hooks/shared/claude_md_manager.py`;
    importing it would require the sys.path bootstrap pact_session.py in this
    directory carries. The
    two bodies are gated identical by TestLineEndingHelperTwinCopyDrift.

    Args:
        content: Full file contents, with any line endings.
        line_ending: The ending to write, from `_detect_line_ending`.

    Returns:
        `content` with its endings replaced, or unchanged when the target is LF.
    """
    if line_ending == "\n":
        return content
    return content.replace("\r\n", "\n").replace("\n", line_ending)


def _atomic_write_text(target: Path, content: str, project_root: Path) -> None:
    """Replace `target`'s contents with `content` atomically, iff the directory
    the write will bind into is contained within `project_root` (#1247).

    `Path.write_text` truncates the file and THEN writes, so a crash, a full
    disk, or a kill between those two steps leaves a TRUNCATED CLAUDE.md. That
    file is gitignored and untracked in the projects this runs in, so there is
    no recovery path -- the user's pinned context is simply gone.

    Writing to a sibling temp file and renaming makes the replacement atomic: a
    reader sees either the whole old file or the whole new one, never a partial
    write. The temp file is created in the TARGET'S OWN DIRECTORY because
    `os.replace` is only atomic within a single filesystem.

    CONTAINMENT -- WHY THERE IS NO PATH RESOLVER HERE
    -------------------------------------------------

    PRECONDITION. This guard eliminates one class outright and narrows another.
    The class eliminated is disagreement between two path resolutions: only
    one traversal happens here and its result is held OPEN, so no second
    resolution exists to disagree with it. What is narrowed is time. The walk
    establishes ancestry AT THE INSTANT OF THE WALK, and a descriptor pins
    IDENTITY, not POSITION -- so containment holds provided the directory the
    write binds into does not change position relative to the anchor between
    the walk and the rename. Only the chain from that directory up to the
    anchor matters; relocating anything off it is irrelevant.

    An earlier form of this guard compared `str(target.resolve())` against the
    resolved root. `resolve()` follows every component INCLUDING the leaf; the
    write follows only the PARENT chain and then binds the leaf as a directory
    entry. Those are two independent traversals of two different path
    expressions, and on a two-leg symlink topology they name different
    directories -- so the guard certified a path the write never touched.

    The general defect is that `Path.resolve()` and `os.path.realpath` are
    SECOND implementations of path resolution, running in userspace, which the
    write does not use. A predicate built on one is sound exactly while the two
    agree, and the disagreement set (symlink loops, absent components) is
    discovered, never bounded. So this function asks the kernel instead:

      anchor  = (st_dev, st_ino) of os.stat(project_root)
      node    = the directory the write will bind into, held OPEN
      walk up via ".." comparing (st_dev, st_ino) until the anchor matches
      (CONTAINED) or a directory is its own parent (filesystem root, REFUSE)

    and then performs temp-create, fchmod, fsync, rename and cleanup THROUGH
    that same descriptor. The object CHECKED is the object MUTATED, by
    construction rather than by agreement. Consequences that fall out rather
    than being bolted on: version-invariant (no `Path.resolve()` exists here, so
    its 3.9-3.12 vs 3.13+ RuntimeError split is unreachable, not caught);
    strict (an absent parent raises instead of being lexically completed);
    immune to case-folding, Unicode normalisation form and trailing separators,
    because nothing is compared as text; and sibling-prefix (`/abc` under
    `/ab`) is refused structurally, since `/abc` never walks up to `/ab`'s
    inode.

    The parent is opened FOLLOWING symlinks -- see the inline note. Mounts
    beneath the project are ALLOWED: `..` from a mount root yields the mount
    POINT's parent, so the walk crosses one transparently, and the write still
    lands inside the anchor's subtree.

    THE LEAF IS NEVER CONSULTED, AND THAT COUPLES THIS TO THE WRITE SHAPE
    --------------------------------------------------------------------
    Containment is entirely a property of the parent chain, because the parent
    chain is the only part the kernel traverses on the way to the write. A leaf
    symlink pointing OUTSIDE the root is therefore ALLOWED, and that is the
    correct verdict, not a concession: `os.replace` is renameat(2), which
    unlinks whatever entry sits at the final name and binds the temp file's
    inode there. It never opens the leaf and never follows it, so the payload
    lands at the in-project entry and any outside victim is left byte-intact.

    That ALLOW is sound ONLY while the write replaces the leaf ENTRY rather
    than writing THROUGH it. Two rewrites would silently convert every such
    ALLOW into a real escape, with this predicate untouched: (1) resolving the
    target once and using it downstream -- proposed as a cheap way to make the
    check and the act agree, which it does, on the WRONG side, by making the
    write follow the leaf; (2) replacing temp-plus-rename with an open/truncate
    on the target. MEASURED, so the guarantee is stated at its real strength:
    with either rewrite spliced in, `TestR6InProjectRedirectResidualDocumented`
    turns RED on its victim-untouched assertion, so the in-project half of this
    coupling IS pinned by an executing test today. The out-of-project half is
    pinned separately, and could not be pinned at all before this predicate,
    because the earlier one refused that topology outright.

    Callers must already hold `file_lock` for the target. The lock closes the
    concurrent-writer window; this closes the crash/truncation window.

    Requires read+execute on the target's directory and write permission on it
    (to create the temp), where a bare `write_text` needed only permission on
    the file itself. A read-only directory holding a writable CLAUDE.md fails
    the write rather than truncating it -- a safe direction, but a real
    behaviour change.

    SCOPE LIMIT, stated because the natural summary of this change overclaims:
    `file_lock` runs BEFORE this function at every call site and does its own
    unprotected `target_file.resolve()`. Removing `Path.resolve()` from this
    guard makes the GUARD version-invariant; it does NOT make the write path
    version-invariant end-to-end, and a symlink loop still raises upstream.

    NOTE: a deliberate duplicate of `_atomic_write_text` in
    `hooks/shared/claude_md_manager.py`. Importing it would require the
    sys.path bootstrap pact_session.py in this directory carries, the same
    consideration that keeps the `file_lock` twin above. This twin IS
    drift-gated by
    TestAtomicWriteTwinCopyDrift: the
    containment CHECK is a security invariant that must not silently diverge
    between the hook and skill copies (#1118-class hazard). That gate compares
    only THIS function's body, which is why the check is inlined here rather
    than extracted -- a named helper would have to be twinned too, and would
    sit outside every drift gate in the repo.

    Args:
        target: Path to replace. Its parent directory must already exist.
        content: Full file contents to write.
        project_root: The trusted base directory `target` must be contained in.

    Raises:
        ContainmentError: the write's parent directory is not the project root
            or a descendant of it; or the boundary could not be established at
            all. Fail-CLOSED in every case, with a distinct message per cause.
    """
    # A MISSING ANCHOR IS REFUSED AT THE CONTROL, not left to the callers.
    #
    # None is not reachable here today: both writers guard, and the resolver
    # returns a PAIRED (None, None). But both of those guards test the TARGET
    # path, not the anchor, so the containment guarantee currently rests on a
    # resolver invariant that neither writer states. A resolver branch returning
    # `(path, None)` would put None here.
    #
    # AND TODAY IT WOULD FAIL CLOSED BY ACCIDENT, WHICH IS THE REASON THIS LINE
    # EXISTS. `os.stat(str(None))` stats the literal relative path "None",
    # which raises -- until a directory named `None` exists in the working
    # directory, at which point that directory silently BECOMES the containment
    # anchor and every write is measured against it. A security control must not
    # depend on a filename not existing. Make the state unrepresentable here.
    if project_root is None:
        raise ContainmentError(
            "refusing write: no containment anchor was supplied"
        )

    # #1247 CONTAINMENT, fail-CLOSED, BEFORE anything is created: kernel object
    # ancestry on a pinned directory descriptor. No Path.resolve(), no
    # os.path.realpath, no string comparison takes part in this decision.
    try:
        anchor_stat = os.stat(str(project_root))
        anchor_key = (anchor_stat.st_dev, anchor_stat.st_ino)
        # FOLLOWS symlinks, deliberately: this is exactly how the kernel will
        # traverse the parent chain for the write. O_NOFOLLOW here would refuse
        # any symlinked final component of the parent path -- including a benign
        # in-project `.claude` -> `<project>/config/claude`, which the pre-#1247
        # code allowed -- a NEW over-block on an axis that is not containment.
        # It would also add nothing: the ancestry test below runs ON this
        # descriptor, so there is no check-then-open gap for it to close.
        parent_fd = os.open(str(target.parent), os.O_RDONLY | os.O_DIRECTORY)
    except (OSError, NotImplementedError):
        # An absent parent, a parent that is not a directory, a symlink loop
        # (ELOOP), and an unsupported-primitive failure all land here. Bare
        # RuntimeError is deliberately NOT caught: it is unreachable, because no
        # Path.resolve() call exists in this function, and catching it would
        # imply one still did and invite one back. NotImplementedError is named
        # explicitly -- it is a RuntimeError SUBCLASS, and it is Python's
        # documented signal for an unsupported dir_fd argument.
        raise ContainmentError(
            "refusing write: cannot establish the containment boundary"
        )

    walked = []
    try:
        # 1024 is an INLINE LITERAL rather than a module constant because a
        # constant would sit outside the region the twin drift gate compares
        # (only this function's body) and could diverge between the twins
        # silently. It is a LIVENESS backstop, not a policy ceiling: a POSIX
        # path is PATH_MAX-bounded and every component costs at least two bytes
        # including its separator, so no reachable path carries this many
        # components. Reaching it means the filesystem is misreporting "..",
        # not that the path is legitimately deep -- which is why exhaustion
        # raises its own message below instead of the escape one. Normal
        # operation terminates at the filesystem root and never arrives here.
        contained = False
        node = parent_fd
        for _ in range(1024):
            node_stat = os.fstat(node)
            if (node_stat.st_dev, node_stat.st_ino) == anchor_key:
                contained = True
                break
            try:
                up = os.open("..", os.O_RDONLY | os.O_DIRECTORY, dir_fd=node)
            except NotImplementedError:
                # NARROW BY DESIGN -- only NotImplementedError is mapped here.
                # A genuine OSError from this open (EACCES on an ancestor the
                # user cannot read) must keep propagating RAW through the outer
                # handler; relabelling it would report a permission failure as
                # a capability failure.
                # NotImplementedError has to be named explicitly because it is
                # a RuntimeError SUBCLASS, NOT an OSError one, while
                # ContainmentError subclasses OSError. So the callers'
                # `except ContainmentError` / `except OSError` arms are blind
                # to it: unmapped, it would escape as-is and CRASH the hook
                # instead of failing closed into the site's opaque skip status.
                # This is the same reason given at the parent-directory open;
                # it applies wherever a dir_fd argument is passed, and this is
                # the second such site.
                raise ContainmentError(
                    "refusing write: platform lacks directory-descriptor "
                    "ancestry traversal"
                )
            walked.append(up)
            up_stat = os.fstat(up)
            if (up_stat.st_dev, up_stat.st_ino) == (
                node_stat.st_dev,
                node_stat.st_ino,
            ):
                # A directory that is its own parent is the filesystem root:
                # the walk is over and the anchor was never reached.
                break
            node = up
        else:
            raise ContainmentError(
                "refusing write: containment walk did not terminate"
            )
        if not contained:
            raise ContainmentError(
                "refusing write: target escapes the project containment boundary"
            )
    except BaseException:
        os.close(parent_fd)
        raise
    finally:
        for extra in walked:
            os.close(extra)

    try:
        # THE SEAM KEEPS THE LINE ENDING OF THE TARGET, FOR EVERY CALLER.
        # A caller reads the file with universal-newline translation, changes a
        # region, and hands the whole document back, so a CRLF file would be
        # written as LF and the user sees a whole-file rewrite they did not
        # make. Repairing that at the call sites is what produced one defect for
        # each site, so the seam owns it and a new write site inherits it.
        #
        # THE DETECTION RUNS HERE FOR TWO REASONS, AND THE POSITION IS PART OF
        # THE GUARANTEE. It is AFTER the containment walk, so it never reads a
        # path the walk has not blessed, and it goes THROUGH `parent_fd`, so the
        # bytes it samples come from the same kernel object the walk approved. A
        # read by name here would reintroduce the race this descriptor design
        # exists to remove, exactly as a chmod by name would.
        content = _restore_line_ending(
            content, _detect_line_ending(target.name, parent_fd)
        )
        tmp_name = f".{target.name}.{uuid.uuid4().hex}.tmp"
        try:
            fd = os.open(
                tmp_name,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
                dir_fd=parent_fd,
            )
        except NotImplementedError:
            raise ContainmentError(
                "refusing write: platform lacks directory-descriptor file creation"
            )
        try:
            # os.fdopen takes ownership of fd only on success; if it raises, the
            # raw fd would leak (the cleanup below unlinks the temp FILE but
            # cannot close a descriptor it never received a handle for).
            try:
                # newline="" so this handle performs NO line-ending translation.
                # With newline=None, Python rewrites each "\n" to os.linesep,
                # which is "\n" here and "\r\n" on Windows, so the restore above
                # would emit "\r\r\n" there.
                #
                # THIS PRIMITIVE CHOOSES THE LINE ENDING AND THE CALLER MUST
                # NOT. That inverts what this comment said before the restore
                # moved here, and the inversion is the point: one property, one
                # owner. A caller that restores for itself makes the
                # substitution run two times. `_restore_line_ending` normalises
                # first, so that mistake is a no-op rather than a doubled
                # carriage return, and it is a defect either way. A source-level
                # arm reports a second owner.
                handle = os.fdopen(fd, "w", encoding="utf-8", newline="")
            except BaseException:
                os.close(fd)
                raise
            with handle:
                handle.write(content)
                handle.flush()
                # fchmod on the OPEN HANDLE, never os.chmod by name: a chmod by
                # name after the close would reintroduce the name-based race
                # this descriptor design exists to remove. It is NOT guarding
                # against over-permissiveness -- umask can only CLEAR bits, so
                # os.open(..., 0o600) cannot yield anything more permissive than
                # 0o600. Its actual effect is the opposite: it RESTORES an
                # owner-write bit a restrictive umask removed (measured: with
                # the open mode alone, umask 0o277 leaves 0o400). The property
                # is DETERMINISM -- the mode belongs to this function rather
                # than to the caller's umask. It precedes the fsync so the mode
                # change is part of what that fsync flushes.
                os.fchmod(handle.fileno(), 0o600)
                # Without the fsync the rename can be persisted while the data
                # behind it is not, which reintroduces the empty-file failure
                # this function exists to prevent.
                os.fsync(handle.fileno())
            try:
                os.replace(
                    tmp_name,
                    target.name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
            except NotImplementedError:
                raise ContainmentError(
                    "refusing write: platform lacks directory-descriptor rename"
                )
        except BaseException:
            # Remove the temp rather than leave it beside the user's CLAUDE.md.
            # BEST-EFFORT, not absolute: if the removal itself fails the temp
            # survives, because nothing downstream of here can remove it. That
            # is the deliberate trade below -- a stray file is preferable to
            # losing the reason the write was refused.
            try:
                os.unlink(tmp_name, dir_fd=parent_fd)
            except (OSError, NotImplementedError):
                # SWALLOWED, not mapped -- the one capability site handled this
                # way, and deliberately so. This cleanup runs while an exception
                # is already in flight; anything raised here REPLACES it, and
                # the bare `raise` below never runs. The caller would then see
                # a cleanup failure instead of the containment refusal that
                # actually stopped the write, so a leftover temp file would
                # outrank the reason the write was refused. The original
                # exception wins; best-effort cleanup stays best-effort.
                # NotImplementedError is named for the same reason as at the
                # dir_fd sites above: it subclasses RuntimeError, NOT OSError,
                # so a bare `except OSError` is blind to it.
                pass
            raise
    finally:
        os.close(parent_fd)


def extract_managed_region(content: str) -> Optional[Tuple[str, int]]:
    """
    Extract the PACT-managed region from CLAUDE.md content.

    ⚠️ THIS TWIN IS DELIBERATELY NOT BYTE-IDENTICAL, AND IS NOT DRIFT-GATED.
    Its siblings (`file_lock`, `_atomic_write_text`) are pinned byte-for-byte;
    this one cannot be, because two differences here are LOCAL CONVENTIONS
    rather than divergence:

      * `Optional[Tuple[str, int]]` here vs `tuple[str, int] | None` there
      * `_MANAGED_START_MARKER` here vs `MANAGED_START_MARKER` there
        (this module private-prefixes what that one exports)

    The executable logic is otherwise identical. Do NOT "fix" either side
    toward the other and do NOT add a byte-identity gate: it would go red on
    arrival, on choices someone made, and a gate that is red on arrival gets
    deleted rather than investigated. A NORMALISED gate — mapping the constant
    names and the annotation syntax before comparing — is the real remedy and
    is deliberately deferred rather than invented here.

    Twin of hooks/shared/claude_md_manager.extract_managed_region — kept
    local as a drift-gated twin; importing the canonical would require the
    sys.path bootstrap pact_session.py in this directory carries.

    Returns (region_text, start_offset) where start_offset is the absolute
    position of the first character after MANAGED_START_MARKER. Returns None
    if either marker is missing.
    """
    start_idx = content.find(_MANAGED_START_MARKER)
    if start_idx == -1:
        return None
    region_start = start_idx + len(_MANAGED_START_MARKER)
    end_idx = content.find(_MANAGED_END_MARKER, region_start)
    if end_idx == -1:
        return None
    return content[region_start:end_idx], region_start


def marker_line_span(text: str, literal: str) -> tuple[int, int] | None:
    """Span of the first line of `text` that IS `literal`, else None.

    TWIN OF `hooks/shared/pin_markers.marker_line_span`, kept local because
    the production entry point does not put `hooks/` on `sys.path`. See
    `_narrow_to_memory_region` for the measurement behind that sentence.

    THE EXECUTABLE BODY MUST STAY BYTE-IDENTICAL TO THE CANONICAL COPY.
    CHANGE THE TWO TOGETHER. Read the canonical docstring for why ONE
    implementation of `the marker occupies a line` matters: a second,
    independently-written predicate produced drift in this repository once,
    and a document marked by one reading and unmarked by another is what
    came out of it.
    """
    offset = 0
    for line in text.splitlines(keepends=True):
        if line.strip() == literal:
            return offset, offset + len(line)
        offset += len(line)
    return None


def _resolve_write_window(content: str) -> tuple[str, int] | None:
    """Resolve the window a section write may search, or None to DECLINE.

    THE THREE STEPS, IN ORDER.
    STEP 1. The memory marker pair resolves: use the memory region.
    STEP 2. ELSE, if the managed-end marker and the session-end marker each
            resolve: use (end of the session block, managed end).
    STEP 3. ELSE decline. The caller must NOT widen.

    A document with NO managed region keeps today's whole-file window. That
    is the pre-migration class and it is outside this rule.

    🔴 R3: ALL THREE STEPS SHARE ONE BLIND SPOT, AND STEP 1 IS NOT THE SAFE
    CASE. The bound defends against a forgery ABOVE the memory region and NOT
    against one INSIDE it, because the marker pair BOUNDS that region and
    cannot exclude what it bounds. Read the step-1 window as `the smallest
    window we can justify`, and not as `the forgery is out`.

    PROPORTIONALITY, AND THE TWO HALVES TRAVEL TOGETHER. The control-character
    sanitizer covers the newline and is applied to all four session-block
    values and each memory-record field, so a forged section title is a
    FIXTURE GIVEN and this bound is DEFENCE IN DEPTH. AND a separate
    measurement found a second load-bearing control, the label prefix, that
    the sanitizer does not touch. One half alone is not the honest statement.
    """
    region_result = extract_managed_region(content)
    if region_result is None:
        return content, 0

    narrowed = _narrow_to_memory_region(region_result[0], region_result[1])
    if narrowed is not None:
        return narrowed

    # STEP 2, AND ITS CAUSE IS AVAILABILITY RATHER THAN SECURITY.
    #
    # 🔴 R1: THE GRANTED SECURITY CAUSE OF THIS STEP IS DEAD. An effective
    # forgery must sit in [0, genuine title), the session-block end SPLITS
    # that interval, and the two memory-entry formatters write BELOW the
    # split, WHERE THIS WINDOW INCLUDES THE FORGERY. This step excludes only
    # a forgery ABOVE the session-block end, which is the session-block
    # writer's own territory. DO NOT READ A NARROWED WINDOW AS AN EXCLUDED
    # FORGERY. No byte size is quoted for the uncovered band on purpose: it
    # was measured as a FLOOR on a one-entry fixture and it GROWS with each
    # memory entry.
    #
    # WHAT IT IS FOR. It keeps a document with no memory marker pair
    # WRITABLE, so it shrinks the population that reaches the decline below.
    # That population is ordinary users with a document not fully migrated or
    # hand-edited, and not attackers.
    #
    # 🔴 R2: A CONSUMED SESSION-END MARKER MAKES THIS STEP UNAVAILABLE, SO
    # CONTROL PASSES TO THE DECLINE AND THE WRITE DECLINES. It does NOT fall
    # back to the wide window. A consumed session-end marker is EVIDENCE that
    # a forged title has run against this document, so a widening there
    # rewards the attack. Such documents can be on disk today: the terminator
    # fix stops NEW ones entering that state and repairs NONE in it.
    region_text, region_start = region_result
    if _MANAGED_END_MARKER in content:
        session_end = marker_line_span(region_text, _SESSION_END_MARKER)
        if session_end is not None:
            inner_start = session_end[1]
            return region_text[inner_start:], region_start + inner_start

    # STEP 3.
    return None


def _narrow_to_memory_region(
    region_text: str, region_start: int
) -> tuple[str, int] | None:
    """Narrow an already-extracted managed region to the MEMORY region inside
    it, or None when the memory marker pair is not there.

    TWIN OF `hooks/shared/pin_markers._narrow_to_memory_region`. THE
    EXECUTABLE BODY MUST STAY BYTE-IDENTICAL TO THE CANONICAL COPY, AND THE
    TWO DOCSTRINGS DIFFER ON PURPOSE: this copy states only what is local.
    CHANGE THE BODIES TOGETHER, and compare them with an extractor that
    PARSES rather than one that counts leading lines. The signature above
    spans several lines, so a line-counting extractor leaves the parameter
    lines and then the docstring inside what it calls the body, and reports
    a difference that is not a difference in logic.

    WHY A TWIN RATHER THAN AN IMPORT, MEASURED RATHER THAN INHERITED. The
    comments elsewhere in this module say the two trees are a different
    package. That cause does not hold, because `hooks/__init__.py` and
    `hooks/shared/__init__.py` each exist. THE OPERATIVE FACT IS A PATH
    BOOTSTRAP DIVERGENCE: the production entry `cli.py` puts ONLY the skill
    root on `sys.path`, and `tests/conftest.py` puts `hooks/` on it. So an
    import here RESOLVES IN PYTEST AND RAISES FROM THE CLI. The failure
    direction of that mistake is the dangerous one: green tests and a
    broken shipped path.

    WHY THE CALLERS NEED IT. `extract_managed_region` returns the WIDE
    region, and the session block sits inside it ABOVE the memory markers
    while it interpolates caller-influenced values. The two write-side
    parsers below search their heading FIRST-MATCH in the window they are
    given, and the offset of that match rebuilds the file. MEASURED on a
    production-shaped document with a forged `## Working Memory` line in the
    session block: the splice landed at 234 against a memory start marker at
    274, so the write would have gone OUTSIDE the memory region.
    """
    start_span = marker_line_span(region_text, MEMORY_START_MARKER)
    if start_span is None:
        return None
    # The END of the marker line, so the window begins on the NEXT line and
    # `region_start` stays a line start for every offset computed below it.
    inner_start = start_span[1]
    tail = region_text[inner_start:]
    end_span = marker_line_span(tail, MEMORY_END_MARKER)
    if end_span is None:
        return None
    return tail[:end_span[0]], region_start + inner_start


# The errnos that mean "not there" rather than "cannot be examined".
_ABSENT_ERRNOS = frozenset({errno.ENOENT, errno.ENOTDIR, errno.EBADF, errno.ELOOP})


def _stat_if_present(path) -> Optional[os.stat_result]:
    """Return `os.stat(path)`, or None when the path is not there.

    A copy of `_stat_if_present` in hooks/shared/claude_md_manager.py, which
    this module does not import (see the twin notes above).
    tests/test_unreadable_location_carriers.py holds every copy to one table:
    an errno in _ABSENT_ERRNOS, or an unencodable path, is absent; any other
    OSError, EACCES and EPERM included, propagates.
    """
    try:
        return os.stat(path)
    except OSError as exc:
        if exc.errno in _ABSENT_ERRNOS:
            return None
        raise
    except ValueError:
        return None


def _find_existing_claude_md(base: Path) -> Optional[Path]:
    """
    Return the first existing CLAUDE.md under `base`, checking both
    supported locations in priority order.

    Claude Code accepts project memory at either `.claude/CLAUDE.md` (new
    default) or `./CLAUDE.md` (legacy). This helper checks `.claude/CLAUDE.md`
    first, then falls back to `./CLAUDE.md`, returning the first match or
    None if neither exists.

    A LOCATION THAT CANNOT BE EXAMINED RAISES, ON EVERY INTERPRETER.
    `Path.exists()` re-raises a PermissionError on 3.9 and 3.13 and returns
    False on 3.14, so the same unsearchable directory aborted resolution on two
    CI interpreters and fell through on the third. `_stat_if_present` decides
    what is absent, the same way as the project CLAUDE.md writers, and lets any
    other OSError propagate. So the legacy file is never tried past a preferred
    one that could not be examined: the preferred file may exist behind the
    error, and writing the legacy file beside it would leave the project with
    two diverging memory files. Every resolver in this module ends resolution
    on the error.

    Args:
        base: Directory to probe for CLAUDE.md.

    Returns:
        Path to the existing CLAUDE.md, or None if neither location exists.

    Raises:
        OSError: a location could not be examined.
    """
    for candidate in (base / ".claude" / "CLAUDE.md", base / "CLAUDE.md"):
        if _stat_if_present(candidate) is not None:
            return candidate
    return None


def _get_claude_md_path() -> Optional[Path]:
    """
    Get the path to CLAUDE.md in the project root.

    Uses CLAUDE_PROJECT_DIR environment variable if set, then the session
    record's project_dir, then git worktree/repo root detection, then the
    current working directory. At each level, checks both `.claude/CLAUDE.md`
    (new default) and `./CLAUDE.md` (legacy) in priority order.

    Note: This mirrors the resolution strategy in hooks/staleness.py
    (get_project_claude_md_path). Kept as a local copy: importing staleness
    would require the sys.path bootstrap pact_session.py in this directory
    carries, and the drift-noted twin remains the chosen mechanism here.

    A location that cannot be examined, at any level, ends resolution with
    None, as in _resolve_display_claude_md_with_base; a failure of git itself
    moves on to the next level.

    Returns:
        Path to CLAUDE.md if it exists, None otherwise.
    """
    try:
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
        if project_dir:
            found = _find_existing_claude_md(Path(project_dir))
            if found is not None:
                return found

        # Session-record rung: the directory session_init recorded at
        # SessionStart, discovered via the CLAUDE_CODE_SESSION_ID glob in
        # pact_session. Below env (a present declaration wins), ABOVE the
        # git/cwd derivations — in a multi-repo workspace the cwd's git root
        # can be the WRONG scope. The existence coupling is preserved: the
        # record supplies the base to PROBE, and a miss falls through exactly
        # like an env miss (this resolver never creates CLAUDE.md).
        record_dir = get_project_dir_from_session_record()
        if record_dir:
            found = _find_existing_claude_md(Path(record_dir))
            if found is not None:
                return found

        # Fallback: detect git root (worktree-safe)
        # Uses --git-common-dir instead of --show-toplevel because the latter
        # returns the worktree path when run inside a worktree, which may not
        # contain CLAUDE.md. --git-common-dir always points to the shared .git
        # directory; its parent is the main repo root where CLAUDE.md lives.
        # git returns this path relative to the invoking directory when run at a
        # repo root (the bare ".git") and absolute elsewhere, so resolve a
        # relative result against the cwd before taking its parent.
        # NOTE: Twin pattern in memory_api.py (_detect_project_id) and
        #       hooks/staleness.py (get_project_claude_md_path) -- keep in sync.
        # Function-level: the shared package is importable only after
        # pact_session's sys.path bootstrap has run at module import.
        from shared.project_scope import git_env_without_location

        # The inner try covers git's own work only. The probe sits outside it,
        # so a location git names that cannot be examined ends resolution
        # instead of reading as a failed rung.
        repo_root = None
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-common-dir"],
                capture_output=True,
                text=True,
                timeout=5,
                env=git_env_without_location(),
            )
            if result.returncode == 0 and result.stdout.strip():
                common_dir = Path(result.stdout.strip())
                if not common_dir.is_absolute():
                    common_dir = Path.cwd() / common_dir
                # os.path.realpath, not Path.resolve(): on 3.9 resolve() raises
                # RuntimeError on a symlink loop, while 3.13 and 3.14 return the
                # path with the looping component unresolved. realpath does that
                # on every interpreter, as in memory_api.main_repo_root.
                repo_root = Path(os.path.realpath(common_dir)).parent
        except (subprocess.TimeoutExpired, OSError):
            pass
        if repo_root is not None:
            found = _find_existing_claude_md(repo_root)
            if found is not None:
                return found
    except OSError:
        return None

    # Last resort: current working directory. `Path.cwd()` stays outside the
    # handlers, so a deleted working directory still raises, as in staleness.
    cwd = Path.cwd()
    try:
        return _find_existing_claude_md(cwd)
    except OSError:
        return None


def _resolve_display_claude_md_with_base(
    errors: Optional[list] = None,
) -> Tuple[Optional[Path], Optional[Path]]:
    """
    Resolve the display CLAUDE.md AND the trusted base directory it was found
    under, so a write caller can containment-check the target against the base
    the resolver actually used (#1247).

    Same resolution order as `_resolve_display_claude_md_path` (which is now a
    thin wrapper returning `[0]`):
      1. CLAUDE_PROJECT_DIR env var, if set -> that dir's .claude/CLAUDE.md
         (preferred) or ./CLAUDE.md (legacy).
      1.5. Session record — the project_dir session_init recorded at
         SessionStart -> the same probe under the recorded dir. Below env
         (a present declaration wins), above the git derivations: in a
         multi-repo workspace the cwd's git root can be the WRONG scope. The
         existence coupling is preserved — the record supplies the base to
         PROBE and a miss falls through; this resolver never creates
         CLAUDE.md. (Numbered 1.5, matching memory_api's Strategy 1.5, so the
         long-standing branch-2/branch-3 references to the git anchors below
         keep their meaning.)
      2. Git worktree root via `git rev-parse --show-toplevel` -> the same
         .claude/-then-legacy probe under the worktree root.
      3. Main repo root via `git rev-parse --git-common-dir`.parent -> the
         same probe. Reached only when the worktree is NOT a session root
         (branch 2 found nothing): under the PACT `.worktrees/` convention no
         session is rooted in the worktree, so the file the session reads is
         the main repo's. Without this branch that write is lost (returns
         None); with it the write lands where the session reads.
      4. Current working directory -> the same probe.

    Branch 2 anchors the WORKTREE root (--show-toplevel) so a worktree that IS
    a session root updates its OWN display file; branch 3 falls back to
    _get_claude_md_path's MAIN-repo anchor (--git-common-dir) for the common
    case where it is not. Because branch 2 precedes branch 3, the two resolvers
    now differ ONLY in that worktree-root branch: in a non-worktree checkout
    both branches resolve the same directory, so the [0] of this result is
    identical to _get_claude_md_path's.

    The returned `base` is the branch's directory captured BEFORE descending
    into `.claude` (the arg passed to `_find_existing_claude_md`), NOT the
    returned path and NOT a re-derivation. That is the trusted pre-resolve
    anchor that makes the #1247 containment check non-vacuous: an F1
    symlinked-parent `.claude` perturbs the target's resolve() but not the
    base's, so containment catches the escape.

    A LOCATION THAT CANNOT BE EXAMINED ENDS RESOLUTION, AT ANY BRANCH. When a
    probe raises (see _find_existing_claude_md), this returns (None, None) and
    records why, instead of trying a lower-priority file or the next branch.
    The file behind the error may be the one this session displays; writing
    another one would leave two diverging memory files, and past a declared
    branch it could write into a different project. A location that is merely
    ABSENT still falls through, which is what keeps PACT's own worktree
    declarations (where CLAUDE.md is gitignored) landing on the main checkout.
    A branch that fails before examining anything -- git missing or timing
    out -- is recorded, and resolution moves on.

    This never CREATES a CLAUDE.md (the orchestrator manages the file's
    lifecycle); it only probes for an existing one.

    Args:
        errors: Optional list. Pass one to receive a message for every
            location that could not be examined, every git branch that
            failed, and every failure that ended resolution, so "nothing
            found" and "failed to look" stay distinguishable.

    Returns:
        (path, base) where path is the existing display CLAUDE.md and base is
        the directory it was found under; (None, None) if none exists.
    """
    # Resolution must never raise into the sync path. A probe that cannot
    # examine a location raises, and so does a deleted working directory or a
    # decode error in git's output; the outer handler records each one and
    # returns (None, None), so the caller skips the sync and the save still
    # succeeds. The git branches' inner handlers cover git's own work only, so
    # a failed git call moves on while a failed probe ends resolution.
    errors = [] if errors is None else errors
    try:
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
        if project_dir:
            base = Path(project_dir)
            found = _find_existing_claude_md(base)
            if found is not None:
                return found, base

        # Branch 1.5: session record (see the docstring's ordering). Same probe
        # shape as the env branch; a miss falls through to the git anchors.
        record_dir = get_project_dir_from_session_record()
        if record_dir:
            base = Path(record_dir)
            found = _find_existing_claude_md(base)
            if found is not None:
                return found, base

        # Worktree root: --show-toplevel returns the worktree directory when run
        # inside a worktree (and the main repo root otherwise), matching the
        # directory session_init/session_resume target for the session's CLAUDE.md.
        # Function-level: the shared package is importable only after
        # pact_session's sys.path bootstrap has run at module import.
        from shared.project_scope import git_env_without_location

        worktree_root = None
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                timeout=5,
                env=git_env_without_location(),
            )
            if result.returncode == 0 and result.stdout.strip():
                worktree_root = Path(result.stdout.strip())
        except (subprocess.TimeoutExpired, OSError) as exc:
            errors.append(f"git rung: {type(exc).__name__}: {exc}")
        if worktree_root is not None:
            found = _find_existing_claude_md(worktree_root)
            if found is not None:
                return found, worktree_root

        # Main-repo root via --git-common-dir. Under the PACT `.worktrees/`
        # convention no session is ever rooted in the worktree, so branch 2
        # found nothing and the file the session actually reads is the MAIN
        # repo's. --git-common-dir points at the shared .git dir whether run
        # from the main repo or a linked worktree, so its parent is the main
        # repo root in both. This is _get_claude_md_path's exact anchor.
        #
        # The is_absolute() guard is load-bearing, not decoration: git returns
        # a RELATIVE path (".git", "../.git") when run at a repo root or subdir,
        # and _find_existing_claude_md does a bare `base / "CLAUDE.md"` with no
        # normalisation, so a relative base would yield a cwd-relative Path and
        # a cwd-relative lock sidecar (the exact divergence D2 just closed).
        # Reused verbatim from _get_claude_md_path.
        repo_root = None
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-common-dir"],
                capture_output=True,
                text=True,
                timeout=5,
                env=git_env_without_location(),
            )
            if result.returncode == 0 and result.stdout.strip():
                common_dir = Path(result.stdout.strip())
                if not common_dir.is_absolute():
                    common_dir = Path.cwd() / common_dir
                # os.path.realpath, not Path.resolve(): on 3.9 resolve() raises
                # RuntimeError on a symlink loop, while 3.13 and 3.14 return the
                # path with the looping component unresolved. realpath does that
                # on every interpreter, as in memory_api.main_repo_root.
                repo_root = Path(os.path.realpath(common_dir)).parent
        except (subprocess.TimeoutExpired, OSError) as exc:
            errors.append(f"git rung: {type(exc).__name__}: {exc}")
        if repo_root is not None:
            found = _find_existing_claude_md(repo_root)
            if found is not None:
                return found, repo_root

        # Last resort: current working directory
        cwd = Path.cwd()
        found = _find_existing_claude_md(cwd)
        return (found, cwd) if found is not None else (None, None)
    except Exception as e:
        logger.debug("display CLAUDE.md resolution failed, skipping sync: %s", e)
        errors.append(f"{type(e).__name__}: {e}")
        return None, None


def _resolve_display_claude_md_path() -> Optional[Path]:
    """
    Resolve the CLAUDE.md the CURRENT SESSION displays (path only).

    Thin wrapper over `_resolve_display_claude_md_with_base` (added for #1247);
    read-only callers and the resolver-parity lint use this Path-only name,
    while the 2 write callers use the with-base variant to get the containment
    anchor. See that function for the full resolution order and the base
    semantics.

    Returns:
        Path to the existing display CLAUDE.md, or None if none exists.
    """
    return _resolve_display_claude_md_with_base()[0]


def _estimate_tokens(text: str) -> int:
    """
    Estimate token count for a text string.

    Uses word count multiplied by 1.3 as a simple approximation for
    English text. No external tokenizer dependency required.

    NOTE: Twin copy exists in hooks/staleness.py (estimate_tokens) -- keep in sync.

    Args:
        text: The text to estimate tokens for.

    Returns:
        Estimated token count (integer).
    """
    if not text:
        return 0
    return int(len(text.split()) * 1.3)


def _compress_memory_entry(entry: str) -> str:
    """
    Compress a full memory entry to a date header, a summary and its key.

    Preserves the date header, extracts the first sentence from the Context
    field, and KEEPS THE `**Memory ID**` LINE. The other fields (Goal,
    Decisions, Lessons, Files) are dropped.

    THE POINTER IS KEPT BECAUSE THE ROUTE IS NOT THE KEY. The section
    comment tells a reader that the full history is searchable through the
    pact-memory skill, which is the ROUTE. The `**Memory ID**` line is the
    KEY. This function dropped the key and left the route, so recovery of a
    compressed entry fell back to a content search across at most 120
    characters of summary. One line of 47 characters restores the key, and
    the whole design accepts loss at this rendering ONLY because the loss is
    recoverable from the store.

    Args:
        entry: Full markdown memory entry string starting with ### YYYY-MM-DD.

    Returns:
        Compressed entry: date header, one-line summary, and the memory id
        where the entry carried one.
    """
    lines = entry.strip().split("\n")
    if not lines:
        return entry

    # Preserve the date header line (### YYYY-MM-DD HH:MM)
    date_line = lines[0]

    # Preserve the recovery key. An entry that carried no id keeps none,
    # because there was none to keep. That is not a regression, and the
    # worst-case cost below assumes the line IS present, so the derived
    # ceiling is conservative for the entries that lack it.
    id_line = ""
    for line in lines[1:]:
        if line.startswith(_MEMORY_ID_LABEL):
            id_line = line
            break

    # Find the Context field and extract its first sentence
    summary_text = ""
    for line in lines[1:]:
        if line.startswith("**Context**:"):
            context_value = line.split("**Context**:", 1)[1].strip()
            # Take the first sentence, up to the first ". " boundary, or the
            # first COMPRESSED_SUMMARY_CHAR_CAP characters. Uses ". " instead
            # of "." to avoid truncating at version numbers like v2.3.1 or
            # decimal values. THE BOUNDARY TEST TAKES THE SAME CONSTANT AS THE
            # CUT, and it takes it for the same reason: a sentence longer than
            # the cap cannot be the summary, so the cut applies instead.
            period_idx = context_value.find(". ")
            if period_idx > 0 and period_idx < COMPRESSED_SUMMARY_CHAR_CAP:
                summary_text = context_value[:period_idx + 1]
            else:
                summary_text = context_value[:COMPRESSED_SUMMARY_CHAR_CAP]
                if len(context_value) > COMPRESSED_SUMMARY_CHAR_CAP:
                    summary_text += "..."
            break

    if not summary_text:
        # Fallback: use first non-header line content
        for line in lines[1:]:
            stripped = line.strip()
            if stripped and stripped.startswith("**") and "**:" in stripped:
                # Extract value from any bold field
                summary_text = (
                    stripped.split("**:", 1)[1].strip()[:COMPRESSED_SUMMARY_CHAR_CAP]
                )
                if len(stripped.split("**:", 1)[1].strip()) > COMPRESSED_SUMMARY_CHAR_CAP:
                    summary_text += "..."
                break

    # The id line is appended LAST, so the compressed entry keeps the same
    # "pointer at the end" shape as an uncompressed one, and
    # `_apply_entry_token_ceiling` exempts it by PREFIX at whatever index it
    # sits, so the two agree without either depending on a position.
    tail = f"\n{id_line}" if id_line else ""
    if summary_text:
        return f"{date_line}\n**Summary**: {summary_text}{tail}"
    return f"{date_line}{tail}"


def _apply_entry_token_ceiling(entry: str, ceiling: int) -> str:
    """
    Cut ONE entry to a token ceiling by dropping whole field LINES.

    A CHARACTER BOUND CANNOT ENFORCE A TOKEN BUDGET. The per-field bound in
    the two formatters is in CHARACTERS, the section budget is in TOKENS,
    and the producer of a field value controls the ratio through whitespace
    density. So the budget is enforced a second time, in its own unit, here.

    THE CUT DROPS WHOLE LINES FROM THE END. It never cuts inside a line: a
    mid-line cut can leave a partial ``**Field**: `` fragment, and a cut at
    a line break puts the remainder at the START of a line, which is the
    shape the whole sanitize exists to prevent.

    TWO LINES ARE EXEMPT AND ALWAYS SURVIVE:

    1. The ``### {date}`` header. Without it the entry stops parsing as an
       entry, and the date-led heading is what excludes it from the pin count.
    2. The ``**Memory ID**`` line. It is the pointer to the durable record.
       The whole design accepts truncation rather than refusal BECAUSE a
       loss at this rendering is recoverable from the store, and that
       argument holds only while the pointer survives the cut.

    THE MEMORY ID LINE IS THE LAST LINE OF AN ENTRY, so a drop-from-the-end
    that did not exempt it would remove the recovery pointer FIRST, quietly
    undoing the argument above while every test stayed green.

    Args:
        entry: One formatted markdown entry, starting with its date header.
        ceiling: Maximum estimated tokens for this entry alone.

    Returns:
        The entry, cut to whole lines, at or below the ceiling where the
        two exempt lines permit it.
    """
    if _estimate_tokens(entry) <= ceiling:
        return entry

    lines = entry.split("\n")
    if len(lines) <= 1:
        return entry

    # Index 0 is the date header. A `**Memory ID**` line is matched by
    # PREFIX wherever it sits, rather than by position, so the exemption
    # does not depend on it staying last.
    exempt = {0}
    for index, line in enumerate(lines):
        if line.startswith(_MEMORY_ID_LABEL):
            exempt.add(index)

    # DROP WHOLE LINES FIRST, from the end, and stop at ONE remaining
    # droppable line. That last line is handled below instead of dropped.
    kept = list(range(len(lines)))
    droppable = [i for i in reversed(range(len(lines))) if i not in exempt]

    # FLOOR WHEN THE EXEMPT LINES ALONE COST MORE THAN THE CEILING: RETURN
    # THE ENTRY WHOLE. This is reached when EVERY line is exempt, which is a
    # date header plus one or more `**Memory ID**` lines and nothing else.
    # There is then no line this function is permitted to drop and none it is
    # permitted to cut, so the ceiling cannot be met at any input.
    #
    # FLOOR IS NOT A NEW POLICY HERE. It is what the last-resort branch below
    # does at a word budget below 1: that branch removes the final droppable
    # line and RETURNS THE EXEMPT LINES, which can sit above the ceiling. This
    # writes the same direction into the path where it was omitted, so the two
    # paths agree rather than one returning and one raising.
    #
    # WITHOUT THIS THE FUNCTION RAISED IndexError AT `droppable[-1]` BELOW, and
    # a raise is the worst of the three directions. The ceiling exists to stop
    # ONE entry exhausting the SECTION, and an entry of exempt lines alone is
    # the SMALLEST entry this function can meet. Refusing on the smallest input
    # propagates out of the formatters and takes the sync down, which loses
    # every entry rather than bounding one.
    if not droppable:
        return entry

    for index in droppable[:-1] if droppable else []:
        if _estimate_tokens("\n".join(lines[i] for i in kept)) <= ceiling:
            break
        kept.remove(index)

    if _estimate_tokens("\n".join(lines[i] for i in kept)) <= ceiling:
        return "\n".join(lines[i] for i in kept)

    # LAST RESORT: TRUNCATE THE FINAL DROPPABLE LINE IN PLACE RATHER THAN
    # DROP IT, AND THE CAUSE IS A MEASURED PRODUCTION DEFECT.
    #
    # A save that carries a CONTEXT and nothing else is an ORDINARY save, and
    # it renders as TWO lines: the date header and one field line. The header
    # is exempt, so that field line is the only droppable one. A pure
    # whole-line rule removed it and left a DATED HEADING WITH NO CONTENT.
    # Where such a save carries no memory id, the entry then kept NO POINTER
    # TO THE STORE either, and the argument that makes this design prefer
    # truncation to refusal is that the loss at this rendering is RECOVERABLE
    # FROM THE STORE. At that shape the recovery pointer was gone too, so the
    # cut destroyed the property the whole design rests on.
    #
    # THIS DOES NOT REOPEN THE MID-LINE-CUT TRAP. That trap has two causes: a
    # cut can leave a partial `**Field**: ` fragment, and a cut at a line
    # break can put the remainder at the START of a line. A truncation that
    # KEEPS THE LINE PREFIX and appends "..." does neither, because it EMITS
    # NO NEWLINE, so it can open no line. The forbidden class is wider than
    # the cause that motivates it, and this is the part outside the cause.
    #
    # THE CODE BELOW APPENDS THE MARKER. IT DOES NOT WRITE THE MARKER ON TOP
    # OF THE KEPT TEXT. The field sanitize uses `[:limit - 3] + "..."` because
    # it bounds CHARACTERS, so the marker must sit within that bound. This
    # function bounds WORDS, so no character bound applies to the marker, and
    # a marker written on top of the kept text breaks the field name. See
    # the two bounds below.
    last = droppable[-1]
    line = lines[last]
    fitted = list(kept)
    overhead = _estimate_tokens(
        "\n".join(lines[i] if i != last else "" for i in fitted)
    )
    budget_words = max(0, int((ceiling - overhead) / 1.3))

    # THE PROPERTY A READER CAN CHECK, AND IT IS A PROPERTY RATHER THAN A
    # NUMBER: THE EMITTED FIELD LINE KEEPS ITS COMPLETE `**Field**:` NAME,
    # OR THE LINE IS ABSENT. There is no third outcome. A number cannot
    # state that property, and a bound written as a number was incorrect
    # two times. The append below is what makes the property hold at each
    # input, because it cannot reach into the words that the cut keeps.
    #
    # THE VALUE CAN BE EMPTY AND THE PROPERTY HOLDS. At a budget of 1 the
    # line reads `**Context**:...`, which keeps the complete name and
    # elides all of the value. THAT SHAPE IS DELIBERATE. A bound of 2
    # removes it and reopens the defect above: an entry with ONE field line
    # then drops that line and renders as a dated heading with no content
    # and no recovery pointer.
    #
    # DEGENERATE EDGE: DROP THE LINE RATHER THAN EMIT A BARE MARKER. At a
    # word budget of 0 the cut keeps no word, so the appended marker IS the
    # line and the output is a bare `...`. That output carries no field
    # name, so it breaks the property above, and the line goes.
    #
    # AN EARLIER BOUND OF 2 CAME FROM A CUT THAT WROTE THE MARKER ON TOP OF
    # THE KEPT TEXT. That bound was one too low for its own rule: at a
    # budget of 2 with a one-character second word, the cut went into the
    # field name and emitted `**Context**...`. A THIRD BOUND ANSWERS ONE
    # MORE INPUT AND LEAVES THE NEXT, so the append removes the class.
    if budget_words < 1:
        fitted.remove(last)
        return "\n".join(lines[i] for i in fitted)

    words = line.split()
    truncated = " ".join(words[:budget_words])
    if len(truncated) < len(line):
        # APPEND, DO NOT WRITE ON TOP. `" ".join` of one or more words does
        # not end with a space, so the marker attaches to the last kept word
        # and adds NO word to `str.split()`. The line therefore costs
        # `budget_words` tokens with the marker and without it, which is why
        # this repair moves no constant.
        truncated += "..."
    lines[last] = truncated
    return "\n".join(lines[i] for i in fitted)


def _apply_token_budget(
    entries: List[str],
    token_budget: int
) -> List[str]:
    """
    Apply a token budget to a list of memory entries.

    Strategy: Cut each entry to the per-entry ceiling. Then compress older
    entries to single-line summaries, and if the total is above budget,
    reduce the number of entries shown.

    THE NEWEST ENTRY IS NEVER COMPRESSED AND NEVER DROPPED. IT CAN BE
    BOUNDED. This docstring said "keep the newest entry in full", which
    conflated THREE properties: not compressed, not dropped, not modified.
    The ceiling does not compress and it does not drop. IT BOUNDS. So the
    first two properties survive and the third does not, and the third is
    the product change that came with the per-entry ceiling: an entry above
    the ceiling loses its last field lines in this rendering, and the store
    keeps the full record.

    THE PER-ENTRY CEILING IS A FIXED EXPRESSION OVER THE MODULE CONSTANTS,
    NOT A FUNCTION OF THE `token_budget` ARGUMENT. Deriving it from the
    argument looks safer and is not: at a SMALL argument the ceiling falls
    below the size of an ordinary entry, so the newest entry gets cut and
    this function stops keeping it in full, which is its stated contract.
    The ceiling exists to stop ONE entry exhausting the SECTION, and the
    section is what the constants describe.

    Args:
        entries: List of memory entry strings (newest first).
        token_budget: Maximum estimated tokens for all entries combined.

    Returns:
        List of entries (some possibly cut or compressed) fitting within budget.
    """
    if not entries:
        return entries

    # Reserve room for the neighbours this function COMPRESSES rather than
    # drops, then give the rest of the section budget to the newest entry.
    # The newest entry is never compressed and the drop loop is
    # `while len(result) > 1`, so without this ceiling one dense entry can
    # exhaust the section on its own and evict every genuine neighbour.
    entry_ceiling = (
        WORKING_MEMORY_TOKEN_BUDGET
        - (MAX_WORKING_MEMORIES - 1) * COMPRESSED_ENTRY_TOKEN_CEILING
    )
    entries = [_apply_entry_token_ceiling(e, entry_ceiling) for e in entries]

    # Check if already within budget
    total_tokens = sum(_estimate_tokens(e) for e in entries)
    if total_tokens <= token_budget:
        return entries

    # Strategy: keep newest entry full, compress the rest
    result = [entries[0]]
    for entry in entries[1:]:
        compressed = _compress_memory_entry(entry)
        result.append(compressed)

    # Check if compressed version fits
    total_tokens = sum(_estimate_tokens(e) for e in result)
    if total_tokens <= token_budget:
        return result

    # Still over budget: drop entries from the end until we fit.
    # Subtract the popped entry's tokens instead of recalculating the full sum.
    while len(result) > 1 and total_tokens > token_budget:
        removed = result.pop()
        total_tokens -= _estimate_tokens(removed)

    return result


def _sanitize_prompt_field(
    value: str,
    limit: int = _REFRESH_FIELD_TRUNCATION_LIMIT,
) -> str:
    """Sanitize a record field value for interpolation into CLAUDE.md.

    Twin of hooks/shared/session_resume._sanitize_prompt_field — kept local
    as a drift-gated twin; importing the canonical would require the sys.path
    bootstrap pact_session.py in this directory carries.
    Body MUST stay byte-identical to the canonical copy (drift test enforces
    this); this docstring is allowed to differ. Change either copy and you
    change both in the SAME commit.

    Collapses control characters to single spaces, strips, and bounds the
    length. Callers MUST sanitize BEFORE they test the value for
    truthiness: an internal failure returns ``""`` so the caller drops that
    field's LINE, and a test of the RAW value would emit the field label
    with an empty value instead.
    """
    try:
        cleaned = _PROMPT_CONTROL_CHARS_RE.sub(" ", value).strip()
        if len(cleaned) > limit:
            cleaned = cleaned[:limit - 3] + "..."
        return cleaned
    except Exception:
        return ""


def _recover_identifier(raw: str) -> str:
    """Accept a raw identifier for the recovery-pointer fallback, or refuse it.

    CALLER-SIDE COUNTERPART TO `_sanitize_prompt_field`, AND DELIBERATELY NOT A
    SECOND SANITIZER. That helper catches bare `Exception` and returns "" on an
    internal failure, and the two recovery-key sites gate on the truthiness of
    its output, so A FAILURE INSIDE THE GUARD SILENTLY DROPS THE
    `**Memory ID**` LINE. That line is the pointer to the durable record, and
    the entry-cut design accepts truncation rather than refusal ONLY WHILE the
    pointer survives. A failure in the guard must not be the one path that
    spends the guarantee the cut rule rests on.

    THE HELPER CANNOT SAY WHY IT RETURNED "", SO THE DISCRIMINATOR IS BUILT
    HERE, AT THE CALLER: a NON-EMPTY input with an EMPTY output is either an
    internal failure or a value made only of control characters. This function
    separates the two by accepting the input or refusing it.

    IT IS AN ACCEPTOR AND NOT A TRANSFORMER, WHICH IS WHAT MAKES AN EMITTED
    POINTER RESOLVE. It returns the input UNCHANGED, or it returns "". It does
    not cut and it does not rewrite, so an emitted value is byte-identical to
    the id the caller received and resolves against the store by construction.
    A fallback that cut to the bound, or that stripped the characters it does
    not accept, would emit a pointer that is PRESENT and does NOT RESOLVE,
    which is the shape this fallback exists to avoid.

    IT HAS NO FAILURE PATH OF ITS OWN, WHICH IS WHY IT NEEDS NO FALLBACK. There
    is no pattern engine, no encode step and no arithmetic: one length compare
    and one character test, and both are total over `str`. A fallback that can
    itself fail needs a fallback, and that regress is the sign of a wrong
    design.

    THE CHARACTER TEST COVERS THE INJECTION PROPERTY WITHOUT NAMING IT. Every
    character that can open a new line (the C0 and C1 controls, NEL, and
    U+2028 and U+2029) is a control or a separator, and NONE of them is
    alphanumeric, so the accepted set cannot hold one.

    Args:
        raw: The identifier as the caller received it, already `str`.

    Returns:
        `raw` unchanged when it is a bounded, line-safe identifier. `""`
        otherwise, and the caller then emits NO pointer line. That is honest:
        where no key can be recovered, an absent line says so and a labelled
        empty value does not.
    """
    if not raw or len(raw) > _REFRESH_IDENTIFIER_TRUNCATION_LIMIT:
        return ""
    for character in raw:
        if not (character.isalnum() or character in "-_."):
            return ""
    return raw


def _record_timestamp(value: Any) -> Optional[datetime]:
    """Parse a record's `created_at` as stored or as `to_dict()` emits it.

    The writer stores ISO-8601 with a `T`, microsecond precision and a
    `+00:00` offset, and `MemoryObject.to_dict()` emits that form. The space
    form `YYYY-MM-DD HH:MM:SS` reaches here only from the schema DEFAULT or
    from direct SQL. Both parse. A value that parses as neither returns
    None so the formatter stamps the entry with now: a malformed row still
    renders, and the header that disagrees with `get` is what shows it.
    """
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _format_memory_entry(
    memory: Dict[str, Any],
    files: Optional[List[str]] = None,
    memory_id: Optional[str] = None,
    created_at: Optional[datetime] = None,
) -> str:
    """
    Format a memory as a markdown entry for CLAUDE.md.

    Args:
        memory: Memory dictionary with context, goal, decisions, etc.
        files: Optional list of file paths associated with this memory.
        memory_id: Optional memory ID to include for database reference.
        created_at: Timestamp for the entry header. None stamps the entry
            with the current time, which is what a save does; a projection
            from the store passes the record's own `created_at`.

    Returns:
        Formatted markdown string for the memory entry.
    """
    # Get date and time for header
    stamp = created_at if created_at is not None else datetime.now(timezone.utc)
    date_str = stamp.strftime("%Y-%m-%d %H:%M")

    lines = [f"### {date_str}"]

    # EVERY field value below is SANITIZED BEFORE IT IS TESTED FOR
    # TRUTHINESS, and the order is load-bearing. `_sanitize_prompt_field`
    # returns "" on an internal failure so the caller drops that field's
    # LINE; a test of the RAW value would pass, and then emit a bare
    # "**Context**: " with no value after it. Sanitize, test the SANITIZED
    # value, then append.

    # Add context if present
    context = _sanitize_prompt_field(str(memory.get("context") or ""))
    if context:
        lines.append(f"**Context**: {context}")

    # Add goal if present
    goal = _sanitize_prompt_field(str(memory.get("goal") or ""))
    if goal:
        lines.append(f"**Goal**: {goal}")

    # Add decisions if present
    decisions = memory.get("decisions")
    if decisions:
        if isinstance(decisions, list):
            # Extract decision text from list of dicts or strings
            decision_texts = []
            for d in decisions:
                if isinstance(d, dict):
                    decision_texts.append(d.get("decision", str(d)))
                else:
                    decision_texts.append(str(d))
            # Sanitize the JOINED value, not each item: the join is what
            # reaches the file, and a per-item bound would let N items
            # multiply past the line bound the sanitize exists to set.
            joined = _sanitize_prompt_field(", ".join(str(t) for t in decision_texts))
            if joined:
                lines.append(f"**Decisions**: {joined}")
        elif isinstance(decisions, str):
            cleaned = _sanitize_prompt_field(decisions)
            if cleaned:
                lines.append(f"**Decisions**: {cleaned}")

    # Add lessons if present
    lessons = memory.get("lessons_learned")
    if lessons:
        if isinstance(lessons, list) and lessons:
            joined = _sanitize_prompt_field(", ".join(str(l) for l in lessons))
            if joined:
                lines.append(f"**Lessons**: {joined}")
        elif isinstance(lessons, str):
            cleaned = _sanitize_prompt_field(lessons)
            if cleaned:
                lines.append(f"**Lessons**: {cleaned}")

    # Add reasoning chains if present
    reasoning = memory.get("reasoning_chains")
    if reasoning:
        if isinstance(reasoning, list) and reasoning:
            joined = _sanitize_prompt_field(", ".join(str(r) for r in reasoning))
            if joined:
                lines.append(f"**Reasoning chains**: {joined}")
        elif isinstance(reasoning, str):
            cleaned = _sanitize_prompt_field(reasoning)
            if cleaned:
                lines.append(f"**Reasoning chains**: {cleaned}")

    # Add agreements if present
    agreements = memory.get("agreements_reached")
    if agreements:
        if isinstance(agreements, list) and agreements:
            joined = _sanitize_prompt_field(", ".join(str(a) for a in agreements))
            if joined:
                lines.append(f"**Agreements**: {joined}")
        elif isinstance(agreements, str):
            cleaned = _sanitize_prompt_field(agreements)
            if cleaned:
                lines.append(f"**Agreements**: {cleaned}")

    # Add disagreements resolved if present
    disagreements = memory.get("disagreements_resolved")
    if disagreements:
        if isinstance(disagreements, list) and disagreements:
            joined = _sanitize_prompt_field(", ".join(str(d) for d in disagreements))
            if joined:
                lines.append(f"**Disagreements resolved**: {joined}")
        elif isinstance(disagreements, str):
            cleaned = _sanitize_prompt_field(disagreements)
            if cleaned:
                lines.append(f"**Disagreements resolved**: {cleaned}")

    # Add files if present
    if files:
        # A path field, so the wider bound: legitimate absolute paths are long.
        joined_files = _sanitize_prompt_field(
            ", ".join(str(f) for f in files), _REFRESH_PATH_TRUNCATION_LIMIT
        )
        if joined_files:
            lines.append(f"**Files**: {joined_files}")

    # Add memory ID if provided
    if memory_id:
        # AN IDENTIFIER, NOT FREE TEXT. The free-text bound of 200 is 3
        # times what the generator emits, and the store does not bound this
        # value at its ingress, so a caller-supplied id took the widest
        # bound in the classification.
        raw_id = str(memory_id)
        cleaned_id = _sanitize_prompt_field(
            raw_id, _REFRESH_IDENTIFIER_TRUNCATION_LIMIT
        )
        # THE SANITIZER IS ONE OF THE PATHS THAT CAN DROP THE RECOVERY
        # POINTER. It returns "" on an internal failure as well as for a value
        # made only of control characters, and the truthiness gate below cannot
        # tell the two apart. NON-EMPTY IN AND EMPTY OUT is the discriminator.
        # `_recover_identifier` then accepts the raw id unchanged or refuses
        # it. See its docstring for why it is an acceptor and not a second
        # sanitizer.
        #
        # THE LENGTH TEST IS THE SECOND ROUTE TO THE ACCEPTOR, AND IT COVERS
        # A STATE THE EMPTY TEST CANNOT SEE. For a raw id past the bound the
        # sanitizer CUTS to `cleaned[:limit - 3] + "..."`, which is NON-EMPTY,
        # so the empty test cannot fire and a cut key reaches the line. THE
        # THIRD CASE IS WHAT DECIDES THIS, and it is not the one the acceptor
        # docstring weighs: an ABSENT line says no key is here, a labelled
        # EMPTY value says the key is empty and is visibly broken, and a CUT
        # value says HERE IS THE KEY while being INDISTINGUISHABLE FROM A GOOD
        # ONE. It fails at the READER, far from this writer, and it reads as a
        # loss in the store rather than as a loss at the rendering. So an id
        # past the bound goes to the acceptor, which refuses it, and no line
        # is emitted. The compressed form copies this line verbatim, so a bad
        # key would outlive the entry text that could identify the record
        # another way.
        if raw_id and (
            not cleaned_id
            or len(raw_id) > _REFRESH_IDENTIFIER_TRUNCATION_LIMIT
        ):
            cleaned_id = _recover_identifier(raw_id)
        if cleaned_id:
            lines.append(f"{_MEMORY_ID_LABEL}: {cleaned_id}")

    return "\n".join(lines)


def _find_terminator_offset(
    content: str,
    start: int,
    terminator_pattern: "re.Pattern[str]",
) -> int:
    """
    Find the absolute offset of the first line matching `terminator_pattern`.

    Simple line-by-line search — no fence tracking needed because callers
    operate within the PACT-managed region (round 10 structural guarantee).
    The managed region contains only plugin-generated content; user-authored
    fenced code blocks live outside PACT_MANAGED_START/END.

    Args:
        content: Text to scan (typically the managed region extract, not
            the full file).
        start: Absolute offset in `content` where scanning begins.
        terminator_pattern: Compiled regex matched against individual lines
            via `.match`.

    Returns:
        Absolute offset of the first terminator line, or `len(content)` if
        none found.
    """
    pos = start
    while pos < len(content):
        nl = content.find("\n", pos)
        if nl == -1:
            line = content[pos:]
            line_end = len(content)
        else:
            line = content[pos:nl]
            line_end = nl + 1

        if terminator_pattern.match(line):
            return pos

        pos = line_end

    return len(content)


def _parse_working_memory_section(
    content: str
) -> Optional[Tuple[str, str, str, List[str]]]:
    """
    Parse CLAUDE.md content to extract working memory section.

    Round 10 structural guarantee: the parser searches within the
    PACT-managed region only. This region contains only plugin-generated
    content (no user-authored fenced code blocks), so fence-aware scanning
    is unnecessary. If the managed region is not present (pre-migration
    file), falls back to scanning the full content. Returned slices
    (before_section, after_section) are always from the FULL content for
    correct write-back.

    Args:
        content: Full CLAUDE.md file content.

    Returns:
        Tuple of (before_section, section_header_with_comment, after_section, existing_entries)
        where existing_entries is a list of individual memory entry strings.
    """
    # Bound to the MEMORY region, not to the managed region.
    #
    # THE WINDOW AND THE TARGET MUST BE THE SAME REGION. The managed region
    # holds the SESSION BLOCK above the memory markers, and that block
    # interpolates caller-influenced values. The first-match search below
    # takes the FIRST `## Working Memory` line in the window it is given, so
    # a forged heading in the session block wins over the genuine one, and
    # the offset of that match rebuilds the file.
    #
    # MEASURED on a production-shaped document, with the boundary taken from
    # the production emitter rather than a literal: the splice landed at 234
    # against a memory start marker at 274, with the genuine heading at 350.
    # THE WRITE WOULD HAVE GONE OUTSIDE THE MEMORY REGION.
    # THE MISSING-PAIR DIRECTION IS SETTLED NOW, AND THE RESOLVER OWNS IT.
    # `None` means DECLINE, and this function returns `None` to say so. It
    # must NOT return the not-found tuple, because that sends the caller to
    # its append-at-end branch, which writes the section OUTSIDE every marker.
    # A decline and an append-at-end are one line apart and they are opposite
    # outcomes. Read `_resolve_write_window` for the three steps.
    window = _resolve_write_window(content)
    if window is None:
        return None
    scan_text, offset = window

    # Pattern to find the Working Memory section.
    # Negative lookahead excludes the three plugin-managed boundary prefixes
    # from being consumed as the auto-managed comment — otherwise an empty
    # Working Memory section followed immediately by <!-- PACT_MEMORY_END -->
    # would greedily swallow the marker (#404).
    section_pattern = re.compile(
        r'^(## Working Memory)\s*\n'
        rf'(<!-- (?!(?:{_PACT_BOUNDARY_ALT}|{_SESSION_BOUNDARY_ALT}))'
        r'[^>]*-->)?\s*\n?',
        re.MULTILINE
    )

    match = section_pattern.search(scan_text)

    if not match:
        # Section doesn't exist
        return content, "", "", []

    section_start = match.start() + offset
    section_header_end = match.end()

    # Find where the next ## section starts (end of working memory section).
    # No fence-awareness needed — managed region contains only plugin-generated
    # content (round 10 structural guarantee).
    next_section_pattern = re.compile(
        rf'(#\s|##\s(?!Working Memory)|---|'
        rf'<!-- (?:{_PACT_BOUNDARY_ALT}|{_SESSION_BOUNDARY_ALT}))',
    )
    section_end_rel = _find_terminator_offset(
        scan_text, section_header_end, next_section_pattern
    )
    section_end = section_end_rel + offset

    before_section = content[:section_start]
    section_content = scan_text[section_header_end:section_end_rel].strip()
    after_section = content[section_end:]

    # Parse existing entries (each starts with ### YYYY-MM-DD)
    entry_pattern = re.compile(r'^### \d{4}-\d{2}-\d{2}', re.MULTILINE)
    entry_starts = [m.start() for m in entry_pattern.finditer(section_content)]

    existing_entries = []
    for i, start in enumerate(entry_starts):
        if i + 1 < len(entry_starts):
            entry = section_content[start:entry_starts[i + 1]].strip()
        else:
            entry = section_content[start:].strip()
        existing_entries.append(entry)

    return before_section, WORKING_MEMORY_HEADER, after_section, existing_entries


def _project_root_of(claude_md_path: Path) -> Path:
    """
    Return the project directory that owns `claude_md_path`.

    CLAUDE.md lives at either `<project>/.claude/CLAUDE.md` (preferred) or
    `<project>/CLAUDE.md` (legacy), so the root is one or two levels up
    depending on which form the caller resolved.

    Used only for an EXPLICIT target, to produce the same containment anchor
    that `_resolve_display_claude_md_with_base` returns for a resolved one —
    the directory captured before descending into `.claude`, never a
    re-derivation from the leaf.

    The two-layout knowledge is owned by `hooks/shared/claude_md_manager.py`
    (`_DOT_CLAUDE_RELATIVE` / `_LEGACY_RELATIVE`); importing it would require
    the sys.path bootstrap pact_session.py in this directory carries, and
    this module vendors twins throughout, so this mirrors it. If a
    THIRD location is ever supported, this must be swept with the others.
    """
    parent = claude_md_path.parent
    return parent.parent if parent.name == ".claude" else parent


class SyncResult:
    """Outcome of a working-memory sync: DID IT WRITE, and WHY NOT.

    `__bool__` is `wrote`, so every existing read of the result keeps its exact
    present meaning. `.reason` carries the discrimination that a bare bool
    could not: a refusal, a suppression and an unresolved target were all
    `False`, and arm 3 of the archival suppression suite proved that a refused
    sync and a suppressed one leave identical evidence on disk.

    THIS DELIBERATELY DOES NOT FOLLOW `_store_embedding`'s CONVENTION, AND THE
    POLARITY IS THE REASON. That function treats `None` as success and a string
    as a problem. THIS function treats `True` as success. The two conventions
    are inverted, so they cannot be shared: adopting the sibling's convention
    here would make a truthiness read report success on a refusal.

    THE ARGUMENT IS THE PRESERVED MEANING, NOT A COUNT OF CALL SITES.
    `__bool__ == wrote` returns exactly what a bare bool returned, so NO
    TRUTHINESS READER changes behaviour -- neither the truthiness readers that
    exist now, nor one written later by an author who never reads this class.
    Do not restate that argument as a tally of reads in the suite. A tally rots
    the next time a test lands, and it invites a future editor to re-derive the
    decision from a population instead of from the property. State the property.

    THE PROPERTY IS ABOUT TRUTHINESS READERS AND NOT ABOUT ALL CALLERS, AND
    THAT WIDTH IS THE CORRECTION RATHER THAN A QUALIFICATION. This paragraph
    said "no caller changes behaviour" and that was too wide: `__bool__`
    cannot rescue an IDENTITY comparison. For an instance of this class,
    `bool(s)` is True while `s is True` and `s == True` are both False, so
    `assert x is True` breaks where `assert x` does not. Seven assertions in
    the suite were identity comparisons and each one had to change. A reader
    who takes the wider claim will predict no breakage and be incorrect.

    Do not "fix" the inconsistency with `_store_embedding`; it is load-bearing.
    """

    __slots__ = ("wrote", "reason")

    # Reasons. WROTE is the only one for which `bool()` is True.
    WROTE = "wrote"
    REFUSED = "refused"          # guard declined; raised, then caught upstream
    SUPPRESSED = "suppressed"    # caller passed sync_to_claude=False
    UNRESOLVED = "unresolved"    # no CLAUDE.md resolved
    MISSING = "missing"          # resolved a path that does not exist
    FAILED = "failed"            # the write itself raised
    EMPTY = "empty"              # nothing to write; caller passed no entries
    # A NEW REASON RATHER THAN `REFUSED`, AND THE CAUSE IS THE SIGNAL AND NOT
    # THE ENUM SIZE. A new CLASS of document stops being written here, and a
    # reader must be able to see WHICH class. `REFUSED` is the ambient-target
    # guard, which arrives by a RAISE and is set by whoever catches it. This
    # one arrives by a RETURN, on the same route as UNRESOLVED and MISSING.
    # Merging the two into one reason would make a signal that cannot
    # separate its own causes.
    #
    # WHERE THAT RETURN ARRIVES DIFFERS BY WRITER, AND THE TWO ARE NOT ALIKE.
    # From `sync_to_claude_md` it reaches `sync_status` on the structured
    # channel with no handler change, because `PACTMemory.save` assigns the
    # reason to `last_sync_status` and `cmd_save` puts that field in the
    # success envelope. From `sync_retrieved_to_claude_md` IT REACHES NOBODY:
    # its one caller, `PACTMemory.search`, discards the returned object, so
    # the reason is produced and dropped. THAT IS A PROPERTY OF THE CALLER
    # AND NOT OF THIS ENUM, so it holds for each reason here rather than for
    # this one alone. Do not read the first sentence as covering the two
    # writers together.
    NO_WINDOW = "no_window"      # no write window resolved; see _resolve_write_window
    # A NEW REASON RATHER THAN `UNRESOLVED`, FOR THE SAME REASON AS NO_WINDOW:
    # THE CAUSE IS THE SIGNAL. Resolution found no CLAUDE.md AND hit an error
    # looking -- a location it could not read, or a failure that ended it -- so
    # "there is no file" and "the file could not be looked for" stop reading
    # alike. It arrives by a RETURN, on the same route as UNRESOLVED, and the
    # errors are logged at WARNING where it is produced.
    RESOLVE_ERROR = "resolve_error"

    def __init__(self, reason: str) -> None:
        self.reason = reason
        self.wrote = reason == self.WROTE

    def __bool__(self) -> bool:
        return self.wrote

    def __repr__(self) -> str:
        return f"SyncResult({self.reason!r})"

    def __eq__(self, other) -> bool:
        if isinstance(other, SyncResult):
            return self.reason == other.reason
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.reason)


def _log_resolve_errors(errors: List[str]) -> None:
    """Log what the display resolver could not read, at WARNING."""
    if errors:
        logger.warning(
            "CLAUDE.md resolution met %d error(s): %s", len(errors), "; ".join(errors)
        )


def _unresolved_reason(claude_md_path: Optional[Path], errors: List[str]) -> str:
    """RESOLVE_ERROR when nothing was found and resolution met an error,
    UNRESOLVED otherwise."""
    if claude_md_path is None and errors:
        return SyncResult.RESOLVE_ERROR
    return SyncResult.UNRESOLVED


class AmbientSyncRefused(RuntimeError):
    """Raised when a test process would sync to an ambiently-resolved CLAUDE.md."""


def _refuse_ambient_sync_on_project_dir_disagreement(
    target: Optional[Path],
    claude_md_root: Optional[Path] = None,
) -> None:
    """Refuse an AMBIENT working-memory sync when CLAUDE_PROJECT_DIR and the
    session record name different project directories.

    The write-path half of the read contract's disagreement policy. READS
    follow the env value (deliberate per-command cross-scope inspection is
    legitimate); WRITES fail closed, because a sync under a disagreed scope
    projects one scope's records over another scope's file — the silent
    mis-scope this family of issues pays for. The refusal text (both values +
    remedy) comes from pact_session's ONE formatter, shared with the backlog
    and memory-save refusals so all three paths say the same words.

    SCOPE mirrors the sibling ambient guards deliberately: an explicit
    `target` or a declared `claude_md_root` is a warrant that names the
    destination, making the ambient disagreement moot.

    Raises ProjectScopeDisagreementError rather than returning a falsy
    SyncResult, matching the sibling guard's rationale: a quiet falsy would
    leave a deliberate refusal indistinguishable from a broken one.
    """
    if target is not None:
        return
    if claude_md_root is not None:
        return
    disagreement = env_record_project_dir_disagreement()
    if disagreement is None:
        return
    raise ProjectScopeDisagreementError(
        format_project_dir_disagreement(*disagreement)
    )


def _refuse_ambient_sync_on_declared_scope_escape(
    target: Optional[Path],
    claude_md_root: Optional[Path],
    resolved_root: Optional[Path],
    claude_md_path: Optional[Path],
) -> None:
    """Refuse an AMBIENT sync that resolved OUTSIDE its declared scope.

    THE DISCRIMINATOR IS ESCAPE, NOT ABSENCE, AND THAT DISTINCTION IS THE
    WHOLE GUARD. A declared scope whose probe finds no CLAUDE.md falls through
    to the git anchors. That is CORRECT when it lands back in the same project
    -- PACT's own spawned paths declare a worktree, where CLAUDE.md is
    gitignored and absent, and the main checkout's file is the intended
    answer. Refusing on absence alone would break that on every such
    invocation, which is a cardinal over-block; refusing on escape breaks
    none of it.

    WHAT IT STOPS: a declared scope resolving into a DIFFERENT project's
    CLAUDE.md and projecting this project's memories there. Absence of a file
    under a named scope was being read as permission to keep looking, and the
    write landed wherever the search ended.

    SCOPE mirrors the sibling ambient guards: an explicit `target` or a
    declared `claude_md_root` names the destination, so there is no ambient
    resolution to police.

    Raises AmbientSyncRefused rather than returning, matching the siblings:
    `save()` records `sync_status='refused'` and still returns the memory id,
    so a refusal costs the projection and never the record -- and a quiet
    skip would leave a deliberate refusal indistinguishable from a miss.
    """
    if target is not None or claude_md_root is not None:
        return
    if resolved_root is None or claude_md_path is None:
        return
    declared = os.environ.get("CLAUDE_PROJECT_DIR") or (
        get_project_dir_from_session_record() or ""
    )
    if not declared:
        return
    # Function-level: the shared package is importable only after
    # pact_session's sys.path bootstrap has run at module import.
    from shared.project_scope import stays_in_declared_project

    if stays_in_declared_project(
        Path(declared),
        Path(resolved_root),
        Path(claude_md_path),
        worktree_identity=get_worktree_identity_from_session_record(),
    ):
        return
    raise AmbientSyncRefused(
        f"the declared project scope ({declared}) resolved to a CLAUDE.md "
        f"under a different project ({resolved_root}); the projection was "
        "refused. Point CLAUDE_PROJECT_DIR at the project you mean, or pass "
        "an explicit target= / claude_md_root= to name the destination."
    )


def _refuse_ambient_target_under_pytest(
    target: Optional[Path],
    claude_md_root: Optional[Path] = None,
) -> None:
    """Refuse an AMBIENT working-memory sync when a TEST PROCESS spawned us.

    THE GAP THIS CLOSES, AND WHY A FLAG WAS NOT ENOUGH. Three paths reach live
    operator state from a test: the database, refused by
    `cli._refuse_live_db_under_pytest`; the session marker, refused by the
    `PYTEST_CURRENT_TEST` check in `pact_session`; and this one, which had no
    refusal at all. Two of three failed closed and the third always wrote.

    `--no-sync` exists and works, but it is a CONVENTION -- it must be
    remembered at every call site. It was forgotten twice in one evening by the
    two people most alert to this exact hazard, so roughly twenty probe saves
    reached the operator's real file. A refusal keyed on the condition needs
    nobody to remember anything.

    AND A SANDBOXED HOME NEVER COVERED IT, which is why it went unnoticed: the
    target is not under HOME. It is resolved from CLAUDE_PROJECT_DIR, then two
    git anchors, then the working directory -- and that resolver is TOTAL, so
    there is no configuration in which it declines to pick a file.

    SCOPE, mirroring `_refuse_live_db_under_pytest` deliberately rather than
    inventing a second shape:
    - An EXPLICIT `target` is always allowed. A caller that names its file has
      said which file it means, and tests legitimately sync to a tmp path.
    - A DECLARED `claude_md_root` is always allowed, AND IT IS A STRONGER
      WARRANT THAN `target` RATHER THAN A SECOND LOOSENING. `target` is blind
      trust: the caller names a file and it is written. An anchor is CHECKED --
      the write must land inside it or `_atomic_write_text` refuses -- so a
      caller that declares a sandbox cannot escape it even by resolving to
      somebody else's file. That is what makes exempting the refusal sound for
      a test process instead of merely convenient.
    - An IN-PROCESS caller (`pytest` already imported) is out of scope, because
      the suite's own working-memory tests call this ambiently on purpose.
    - The same bounded gap applies: pytest pops `PYTEST_CURRENT_TEST` between
      items, so a spawn during collection or session-fixture setup is NOT
      covered.

    Raises AmbientSyncRefused rather than returning False, because save()
    already treats a sync failure as non-critical and logs it -- a quiet False
    would leave the refusal invisible, which is the failure mode being fixed.
    """
    if target is not None:
        return
    if claude_md_root is not None:
        return
    if "pytest" in sys.modules:
        return
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return
    raise AmbientSyncRefused(
        "refusing to sync working memory to an ambiently-resolved CLAUDE.md: "
        "PYTEST_CURRENT_TEST is set in this process's environment, so the "
        "destination would be the operator's live file. Pass an explicit "
        "target=, or use the CLI's --no-sync flag."
    )


def _target_is_inside_the_declared_project_dir(resolved_target: Path) -> bool:
    """Report whether resolution landed inside the caller's declared project dir.

    `CLAUDE_PROJECT_DIR` IS A DECLARATION, THE SAME KIND OF WARRANT AS
    `claude_md_root`, AND THE INCIDENT IS WHAT SHOWS THE DIFFERENCE MATTERS. The
    write that reached an operator's file came from a process started with the
    environment CLEARED, so the variable was absent and resolution fell through
    to a git anchor that pointed at a checkout nobody had named.

    THE CHECK IS CONTAINMENT, NEVER THE PRESENCE OF THE VARIABLE. A set variable
    is a PROXY and a wrong one: `_find_existing_claude_md` probes that directory
    and CONTINUES when it finds nothing, so resolution can begin at a declared
    root and finish somewhere else entirely. Presence would exempt exactly that
    escape. Comparing the RESOLVED path against the declared root reports what
    resolution did rather than what the caller intended.

    Both sides are resolved before the comparison so that a symlink or a `..`
    cannot make an outside path read as an inside one.

    Returns False when the variable is unset, when it is empty, or when it names
    a directory the target does not sit under.
    """
    declared = os.environ.get("CLAUDE_PROJECT_DIR")
    if not declared:
        return False
    # os.path.realpath, not Path.resolve(): on 3.9 resolve() raises
    # RuntimeError on a symlink loop, while 3.13 and later return the path
    # with the looping component unresolved. realpath does that on every
    # interpreter, so a looped declaration gets the same answer everywhere.
    try:
        Path(os.path.realpath(resolved_target)).relative_to(
            Path(os.path.realpath(declared))
        )
    except (ValueError, OSError):
        return False
    return True


def _refuse_ambient_sync_from_a_redirected_store(
    target: Optional[Path],
    claude_md_root: Optional[Path] = None,
    resolved_target: Optional[Path] = None,
) -> None:
    """Refuse an AMBIENT sync when the ROW went to a store that is not the default.

    THE HOLE ITS SIBLING LEAVES OPEN, AND THE TWO ARE NOT INTERCHANGEABLE.
    `_refuse_ambient_target_under_pytest` keys on `PYTEST_CURRENT_TEST` in the
    ENVIRONMENT. A caller that clears the environment (`env -i python3 cli.py
    save ...`) STRIPS that variable, so that guard reads a clean process and
    admits the write. That is not a hypothetical shape: it is the invocation
    that put three entries into an operator's live CLAUDE.md, entries the
    operator could not then look up, because the rows had gone to two throwaway
    stores below a scratch directory while the projection went to the real file.

    THE CLASS REFUSED IS KEYED ON THE RESULT, NOT ON THE CALLER. A caller-keyed
    rule ("refuse saves from a test agent") is defeated by the next new caller.
    The property that separates the incident from ordinary use is this: the row
    was written to a REDIRECTED store, and the projection was about to go to an
    AMBIENTLY RESOLVED file. Those two together produce an entry that displays
    in a document no reader can resolve back to a store. A save that uses the
    default store cannot produce that, whatever it calls itself.

    THE CROSSING IS THE DEFECT, NOT THE RESOLUTION. Do NOT read this as a rule
    against the main-repo resolution branch. That branch is deliberate: a
    session that runs in a worktree reads the MAIN checkout's CLAUDE.md, so the
    branch is how an ordinary save reaches the file the session displays.
    Refusing it would lose every worktree save projection, which is the
    over-block this guard is shaped to avoid.

    SCOPE, mirroring the sibling guard deliberately rather than inventing a
    second shape:
    - An EXPLICIT `target` is always allowed. The caller named its file.
    - A DECLARED `claude_md_root` is always allowed, and it is the STRONGER
      warrant: the write must land inside it or `_atomic_write_text` refuses,
      so a caller that declares a sandbox cannot escape it.
    - An IN-PROCESS caller (`pytest` already imported) is out of scope. The
      suite binds a redirected store for every test AND syncs ambiently on
      purpose, so refusing there would break the suite rather than the hazard.
      The incident process had no pytest in it, so this exemption does not
      reopen the class.
    - A TARGET THAT LANDED INSIDE `CLAUDE_PROJECT_DIR` is allowed, and this
      exemption was added because a MEASURED over-block demanded it. Without it
      the guard refused a suite arm that spawns a child with a redirected store
      and a tmp project directory, which is a legitimate and common shape: the
      caller declared a root through the environment and resolution stayed
      inside it. The incident does NOT come back, because that process ran with
      the environment cleared and resolution escaped to a git anchor.

    THE FAILURE DIRECTION IS DELIBERATE AND IT IS THE SAFE ONE. When this guard
    is wrong it refuses a GOOD projection rather than admitting a bad one. That
    is acceptable here and would not be elsewhere, because the refused thing is
    a PROJECTION and never a RECORD: `save` has already committed the row and
    goes on to return success, and the caller reads `sync_status='refused'`. So
    a wrong refusal costs a display line the next sync rebuilds, while a wrong
    admission corrupts a gitignored, always-loaded file that has no commit to
    restore it from.

    Raises AmbientSyncRefused, the same type as the sibling guard, so the one
    handler in `memory_api.save` maps both to `SyncResult.REFUSED` with no
    second branch to keep in step.
    """
    if target is not None:
        return
    if claude_md_root is not None:
        return
    if "pytest" in sys.modules:
        return
    origin = store_path_origin()
    if origin == STORE_ORIGIN_HOME:
        return
    # LAST, AND ONLY WITH A RESOLVED PATH IN HAND. A caller that has not
    # resolved yet passes None, and None cannot be inside anything, so the
    # refusal stands. That is the safe direction: the exemption has to be
    # EARNED by a resolution that landed inside the declared root.
    if resolved_target is not None and _target_is_inside_the_declared_project_dir(
        resolved_target
    ):
        return
    # THE ORIGIN WORD, NEVER THE PATH. `origin` is one of a closed set of words
    # ("scope", "environment"), so it names the redirect without putting a
    # filesystem path into a message that reaches a log and a caller.
    raise AmbientSyncRefused(
        "refusing to sync working memory to an ambiently-resolved CLAUDE.md: "
        f"the memory store is redirected (origin={origin}), so this entry "
        "would display in a file that does not read from the store that holds "
        "it. Pass an explicit target=, declare claude_md_root=, or use the "
        "CLI's --no-sync flag."
    )


def sync_to_claude_md(
    memory: Optional[Dict[str, Any]],
    files: Optional[List[str]] = None,
    memory_id: Optional[str] = None,
    target: Optional[Path] = None,
    claude_md_root: Optional[Path] = None,
    *,
    entries: Optional[List[str]] = None,
) -> "SyncResult":
    """
    Sync a memory entry to the Working Memory section of CLAUDE.md.

    Maintains a rolling window of AT MOST MAX_WORKING_MEMORIES entries. New
    entries are added at the top of the section, and older ones are removed.

    THE `entries` ARM REPLACES INSTEAD OF PREPENDING. When `entries` is given
    it is the whole section: the pre-formatted entries, newest first, are
    written in place of whatever the section holds, and the file's existing
    entries are not consulted. `memory`, `files` and `memory_id` are ignored
    on that arm. Everything else -- resolution, the ambient guards, the
    lock, the splice window, the budget, containment and the `SyncResult` --
    is the same code on both arms, which is why the replace is a keyword here
    and not a second writer.

    THE COUNT IS A CAP, NOT A PROMISE, and this docstring said "the last 3"
    until the claim was measured. `_apply_token_budget` keeps the newest entry
    IN FULL and its drop loop is `while len(result) > 1`, so when that entry
    ALONE exceeded the whole-section budget the older ones were dropped and the
    section showed ONE.

    THAT REGIME IS CLOSED AND THE CAP IS STILL A CAP. `_apply_entry_token_ceiling`
    now bounds each entry below the section budget, so the newest entry cannot
    exhaust the section by itself and the older ones survive at their compressed
    size. The count remains a cap rather than a promise for the ordinary reason:
    the store can hold fewer entries than MAX_WORKING_MEMORIES.

    ITS SIBLING `sync_retrieved_to_claude_md` HOLDS THE SAME CAP BY A DIFFERENT
    MECHANISM, AND THE EARLIER DESCRIPTION OF THAT MECHANISM HERE WAS INCORRECT
    IN TWO PLACES. It said `_format_retrieved_entry` truncates each ENTRY to 200
    chars; it bounded the CONTEXT FIELD only, and the query, the goal and the
    memory id carried no bound at all. It then said the sibling's drop loop
    "never runs"; that loop was DRIVEN, reaching 547 tokens against its budget
    of 500 and evicting one genuine neighbour.

    WHAT IS CORRECT NOW, WITH ITS CONDITION. Each field of both formatters is
    bounded, and each entry of both sections is bounded in TOKENS. The sibling's
    drop loop CAN still run: three retrieved entries at the full character bound
    cost more than its budget, so an entry at the ceiling loses lines and a
    third entry can be dropped. A realistic retrieved entry sits far below the
    ceiling, because a query and a memory id are short. The two functions differ
    in POLICY and not in whether they bound: this one compresses its older
    entries before it drops any, and the sibling drops without compressing.

    This function is designed for graceful degradation - if CLAUDE.md doesn't
    exist or the sync fails for any reason, it logs a warning but doesn't
    raise an exception.

    THE TARGET IS CALLER-SPECIFIABLE. Without `target` the destination is
    resolved AMBIENTLY — from CLAUDE_PROJECT_DIR, then two git anchors, then
    the working directory — and a caller who knows which file it means has no
    way to say so. That is the root defect: not that the resolver is wrong,
    but that there is no override, so a caller's knowledge cannot reach the
    write. `target` is that override.

    AN ABSENT DESTINATION IS A SKIP — on EITHER branch, explicit or ambient.
    The guard below the branch join states it rather than leaving it to be
    inferred. This matters because the obvious resolver to compute a target
    with — `claude_md_manager.resolve_project_claude_md_path` — is TOTAL: on a
    miss it returns a `"new_default"` path rather than None. That is correct
    for a caller whose job is to create the file, and wrong here, where the
    orchestrator owns the file's lifecycle.

    The guard covered only the explicit branch when it was first written, on
    the reasoning that the ambient resolver returns None when it finds nothing
    so that skip rode along free. That is true, but it is a property of the
    RESOLVER rather than of this function — and a TOTAL resolver never returns
    None, so the arrangement failed in exactly the case it was supposed to
    cover. Below the join it depends on nobody else's promise.

    MEASURED, because the stronger version of this warning is not true today
    and repeating it would misdescribe the code: handed a path to a file that
    does not exist, this function does NOT create it. The read precedes the
    write, so the read raises and the degradation handler below returns False.
    What it does do is take the sidecar lock first — leaving a `.CLAUDE.md.lock`
    artifact in a directory it should never have touched — and report a warning
    that reads like a real failure rather than a skip.

    So the protection against creating is currently an ACCIDENT OF ORDERING,
    not a contract: it holds only while the write path happens to read first.
    A future change that tolerates a missing file — or writes before reading —
    turns it into a create, and nothing would fail. The guard converts that
    accident into a stated contract, which is the whole point of putting it
    here rather than trusting the resolver to encode it.

    So: compute the target AT THE CALLER, pass it here, and let the existence
    check below decide.

    Args:
        memory: Memory dictionary with context, goal, decisions, lessons_learned, etc.
        files: Optional list of file paths associated with this memory.
        memory_id: Optional memory ID to include for database reference.
        target: Explicit CLAUDE.md path to write. When omitted, the display
            CLAUDE.md is resolved ambiently exactly as before, so existing
            callers are unaffected. When given, it is used verbatim and is
            never created if absent.
        claude_md_root: Declared containment anchor. The write must land inside
            it or the containment check refuses. It does NOT steer resolution:
            the target is still found the same way, and a target that resolves
            outside the declared root is REFUSED rather than redirected. Omit it
            for today's behaviour, where the anchor comes from the resolution.
            Supplying it also exempts the ambient-sync refusal, because a
            checked boundary is a stronger warrant than the unchecked `target`.

    Returns:
        A `SyncResult`. It is TRUTHY exactly when the write happened, so a
        caller that only asks "did it write" reads it unchanged. `.reason`
        names the outcome in EVERY case, the successful one included, so a
        caller that must tell a refusal from a suppression now can.

        A REFUSAL DOES NOT COME BACK THIS WAY. The ambient-target guard RAISES
        `AmbientSyncRefused` before any of these returns, so `SyncResult.REFUSED`
        is produced by whoever catches it, not here. See the guard's own
        docstring for why it raises rather than returns. The disagreement guard
        beside it raises `ProjectScopeDisagreementError` the same way, and for
        the same reason.
    """
    _refuse_ambient_target_under_pytest(target, claude_md_root)
    _refuse_ambient_sync_on_project_dir_disagreement(target, claude_md_root)

    resolve_errors: List[str] = []
    if target is not None:
        claude_md_path = Path(target)
        resolved_root = _project_root_of(claude_md_path)
    else:
        claude_md_path, resolved_root = _resolve_display_claude_md_with_base(
            errors=resolve_errors
        )
        _log_resolve_errors(resolve_errors)
    # Escape guard runs AFTER resolution because it needs the resolved
    # root; the guards above run before because they need only the
    # declaration. Same ordering in the sibling.
    _refuse_ambient_sync_on_declared_scope_escape(
        target, claude_md_root, resolved_root, claude_md_path
    )

    # THE DECLARED ANCHOR REPLACES THE CONTAINMENT BASE. IT DOES NOT STEER
    # RESOLUTION -- the target above is found exactly as it was before.
    # Narrowing the two Nones HERE is the point: `claude_md_root is None` means
    # "the caller declared nothing, behave as before", while `resolved_root is
    # None` means "resolution found nothing at all". Those are different facts,
    # so they are carried in different variables and only one decision reads
    # both. A `claude_md_root or resolved_root` would merge them and silently
    # treat a declared-nothing as a found-nothing.
    #
    # It is a PARAMETER and never a lookup: no line here derives an anchor. An
    # anchor computed from the same resolution the target came from would agree
    # with it by construction, which is precisely the independence the caller is
    # trying to buy.
    project_root = (
        Path(claude_md_root) if claude_md_root is not None else resolved_root
    )

    # BOTH HALVES, BECAUSE THE PAIRING IS THE RESOLVER'S PROMISE AND NOT THIS
    # FUNCTION'S. `_resolve_display_claude_md_with_base` returns either two
    # paths or two Nones -- every one of its five exits is guarded by an
    # `if found is not None` -- so today `project_root` cannot be None once
    # `claude_md_path` is not. MEASURED, and it is the ONLY reason the anchor
    # below is safe.
    #
    # That is exactly the arrangement the existence guard further down was moved
    # for: a caller depending on a property of a resolver it does not own. A
    # later branch returning `(found, None)` would slip a None past a check that
    # only asks about the path, and `_atomic_write_text` would stat the literal
    # relative path "None" as its containment anchor -- which raises today, but
    # silently anchors on a directory named `None` if one ever exists. Naming
    # both halves here costs one condition and depends on nobody's promise.
    if claude_md_path is None or project_root is None:
        logger.debug("CLAUDE.md not found, skipping working memory sync")
        return SyncResult(_unresolved_reason(claude_md_path, resolve_errors))

    # EXISTENCE GUARD, BELOW THE JOIN SO IT COVERS BOTH BRANCHES.
    #
    # It used to sit inside the explicit-target branch, on the reasoning that
    # an ambient resolve returns None when it finds nothing so that skip rides
    # along for free. That reasoning is TRUE, and it is a property of
    # `_resolve_display_claude_md_with_base` -- NOT of this function. The
    # comment then claimed the contract therefore held for both paths, which
    # did not follow: a TOTAL resolver never returns None, so the `is None`
    # check above would not fire and nothing else stood between it and the
    # write. The claim was exactly inverted against the hazard it named.
    #
    # Down here the guard depends on no other function's promise.
    #
    # THE TWO WAYS TO ARRIVE ARE DIFFERENT IN KIND, so they are reported
    # differently rather than collapsed into one message:
    #
    #   explicit + absent -- ORDINARY. A caller named a project whose CLAUDE.md
    #     does not exist yet. Expected, benign, fires in normal use: debug.
    #
    #   ambient + absent -- SHOULD NOT HAPPEN. The resolver is documented to
    #     return only existing paths, and a resolver that finds nothing returns
    #     None, which the check above already absorbed. So this arm is
    #     unreachable on the normal path, and an unreachable arm that fires is
    #     signal rather than noise: warning.
    #
    # A single undifferentiated check would make a resolver that quietly went
    # total indistinguishable from an ordinary skip, at debug level -- the same
    # two-unrelated-causes-on-one-check problem that costs you the control.
    #
    # THE WARNING NAMES BOTH CAUSES AND ASSERTS NEITHER. A file removed between
    # resolution and this line produces an identical observation and is not a
    # defect at all; the two are indistinguishable here. The level says "look
    # at this", the text must not say "this is a bug."
    #
    # NEITHER ARM CREATES. That is the contract: an absent target is a skip.
    if not claude_md_path.exists():
        if target is not None:
            logger.debug(
                "explicit sync target %s does not exist, skipping working "
                "memory sync (this never creates CLAUDE.md)", claude_md_path
            )
        else:
            logger.warning(
                "resolved display CLAUDE.md %s does not exist, skipping "
                "working memory sync (this never creates CLAUDE.md). Either "
                "the display resolver stopped returning only existing paths, "
                "or the file was removed after it resolved.", claude_md_path
            )
        return SyncResult(SyncResult.MISSING)

    # THE REDIRECTED-STORE REFUSAL SITS HERE, BELOW RESOLUTION AND ABOVE THE
    # LOCK, and the position is load-bearing rather than tidy. It needs the
    # RESOLVED path to judge whether the destination was declared or derived,
    # so it cannot run at the top with its sibling. It must run before
    # `file_lock`, which creates the sidecar's parent directories: a refusal
    # after that point would leave a write behind on the path it refused.
    _refuse_ambient_sync_from_a_redirected_store(
        target, claude_md_root, claude_md_path
    )

    try:
        # Serialize the FULL read-modify-write window under the shared sidecar
        # lock (see the "why lock the whole window" note above file_lock for the
        # read-under-lock / lock-identity / CLAUDE_PROJECT_DIR rationale).
        with file_lock(claude_md_path):
            # Read current content
            content = claude_md_path.read_text(encoding="utf-8")

            # Parse existing working memory section
            parsed = _parse_working_memory_section(content)
            if parsed is None:
                # THE DECLINE, AND IT IS LOUD RATHER THAN SILENT. `bool()` on
                # this result is False, so a caller that reads it as a success
                # flag sees the write did not happen, and `.reason` names the
                # cause on the structured `sync_status` channel. THE FAILURE
                # DIRECTION IS A LOST ENTRY, so it must not be silent: the
                # alternative shape here, the not-found tuple, would append
                # the section OUTSIDE every marker instead.
                return SyncResult(SyncResult.NO_WINDOW)
            before_section, section_header, after_section, existing_entries = parsed

            if entries is None:
                # Format new memory entry, then prepend it to the file's own.
                # A None here is a caller error; the raise lands in the
                # handler below as FAILED, the same as any other bad input.
                assert memory is not None, "the prepend arm needs a memory"
                new_entry = _format_memory_entry(memory, files, memory_id)
                all_entries = [new_entry] + existing_entries
            else:
                # REPLACE: the file's entries are not consulted
                all_entries = list(entries)
            trimmed_entries = all_entries[:MAX_WORKING_MEMORIES]

            # Apply token budget: compress older entries if over budget
            trimmed_entries = _apply_token_budget(
                trimmed_entries, WORKING_MEMORY_TOKEN_BUDGET
            )

            # Build new section content
            section_lines = [
                WORKING_MEMORY_HEADER,
                WORKING_MEMORY_COMMENT,
                ""  # Blank line after comment
            ]
            for entry in trimmed_entries:
                section_lines.append(entry)
                section_lines.append("")  # Blank line between entries

            section_text = "\n".join(section_lines)

            # Reconstruct file content
            if section_header:
                # Section existed, replace it
                new_content = before_section + section_text + after_section
            else:
                # Section didn't exist, append at end
                if not content.endswith("\n"):
                    content += "\n"
                new_content = content + "\n" + section_text

            # Write back to file (atomic: temp + rename, so a crash mid-write
            # cannot leave the always-loaded CLAUDE.md truncated)
            _atomic_write_text(claude_md_path, new_content, project_root)

        logger.info("Synced memory to CLAUDE.md Working Memory section")
        return SyncResult(SyncResult.WROTE)

    except Exception as e:
        logger.warning(f"Failed to sync memory to CLAUDE.md: {e}")
        return SyncResult(SyncResult.FAILED)


def project_memories_to_claude_md(
    memories: List[Dict[str, Any]],
    target: Optional[Path] = None,
    claude_md_root: Optional[Path] = None,
) -> "SyncResult":
    """Replace the Working Memory section with `memories`, newest first.

    Each dict is a `MemoryObject.to_dict()`; each entry's header is the
    record's own `created_at`. Empty input returns EMPTY and touches nothing:
    the check precedes the guards because nothing would be written, so
    nothing is refused. Input past MAX_WORKING_MEMORIES is cut to it.
    """
    if not memories:
        return SyncResult(SyncResult.EMPTY)
    entries = [
        _format_memory_entry(
            m, m.get("files") or None, m.get("id"),
            created_at=_record_timestamp(m.get("created_at")),
        )
        for m in memories[:MAX_WORKING_MEMORIES]
    ]
    return sync_to_claude_md(
        None, target=target, claude_md_root=claude_md_root, entries=entries
    )


def _parse_retrieved_context_section(
    content: str
) -> Optional[Tuple[str, str, str, List[str]]]:
    """
    Parse CLAUDE.md content to extract retrieved context section.

    Round 10 structural guarantee: same managed-region bounding as
    _parse_working_memory_section — see that function's docstring.

    Args:
        content: Full CLAUDE.md file content.

    Returns:
        Tuple of (before_section, section_header, after_section, existing_entries)
        where existing_entries is a list of individual memory entry strings.
    """
    # Bound to the MEMORY region, not to the managed region. Same bound as
    # `_parse_working_memory_section` and for the same cause, including the
    # missing-pair branch: read that function for both.
    #
    # MEASURED at THIS site, on a production-shaped document with a forged
    # `## Retrieved Context` line in the session block: the splice landed at
    # 234 against a memory start marker at 277.
    # Same three-step window and the same DECLINE as the sibling above. `None`
    # here means decline, and it must not be the not-found tuple, for the same
    # cause: that tuple sends the caller to its append-at-end branch.
    window = _resolve_write_window(content)
    if window is None:
        return None
    scan_text, offset = window

    # Pattern to find the Retrieved Context section.
    # Negative lookahead narrows to the plugin-managed boundary prefixes
    # — see _parse_working_memory_section for the full rationale (#404).
    section_pattern = re.compile(
        r'^(## Retrieved Context)\s*\n'
        rf'(<!-- (?!(?:{_PACT_BOUNDARY_ALT}|{_SESSION_BOUNDARY_ALT}))'
        r'[^>]*-->)?\s*\n?',
        re.MULTILINE
    )

    match = section_pattern.search(scan_text)

    if not match:
        # Section doesn't exist
        return content, "", "", []

    section_start = match.start() + offset
    section_header_end = match.end()

    # Find where the next ## section starts (end of retrieved context section).
    # No fence-awareness needed — managed region contains only plugin-generated
    # content (round 10 structural guarantee).
    next_section_pattern = re.compile(
        rf'(#\s|##\s(?!Retrieved Context)|---|'
        rf'<!-- (?:{_PACT_BOUNDARY_ALT}|{_SESSION_BOUNDARY_ALT}))',
    )
    section_end_rel = _find_terminator_offset(
        scan_text, section_header_end, next_section_pattern
    )
    section_end = section_end_rel + offset

    before_section = content[:section_start]
    section_content = scan_text[section_header_end:section_end_rel].strip()
    after_section = content[section_end:]

    # Parse existing entries (each starts with ### YYYY-MM-DD)
    entry_pattern = re.compile(r'^### \d{4}-\d{2}-\d{2}', re.MULTILINE)
    entry_starts = [m.start() for m in entry_pattern.finditer(section_content)]

    existing_entries = []
    for i, start in enumerate(entry_starts):
        if i + 1 < len(entry_starts):
            entry = section_content[start:entry_starts[i + 1]].strip()
        else:
            entry = section_content[start:].strip()
        existing_entries.append(entry)

    return before_section, RETRIEVED_CONTEXT_HEADER, after_section, existing_entries


def _format_retrieved_entry(
    memory: Dict[str, Any],
    query: str,
    score: Optional[float] = None,
    memory_id: Optional[str] = None
) -> str:
    """
    Format a retrieved memory as a markdown entry for CLAUDE.md.

    Args:
        memory: Memory dictionary with context, goal, decisions, etc.
        query: The search query that retrieved this memory.
        score: Optional similarity score.
        memory_id: Optional memory ID for reference.

    Returns:
        Formatted markdown string for the retrieved entry.
    """
    # Get date and time for header
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d %H:%M")

    lines = [f"### {date_str}"]
    # The query is caller-supplied text, so it is sanitized like every other
    # interpolated value. It is NOT guarded by a truthiness test: an empty
    # query renders an empty pair of quotes, which is the pre-fix behaviour.
    lines.append(f"**Query**: \"{_sanitize_prompt_field(str(query or ''))}\"")

    if score is not None:
        lines.append(f"**Relevance**: {score:.2f}")

    # Add context if present.
    # THE HAND-ROLLED TRUNCATION THAT STOOD HERE IS GONE, AND ITS REMOVAL IS
    # PART OF THE FIX RATHER THAN A TIDY-UP. It cut to `context[:197] +
    # "..."`, and `_sanitize_prompt_field` at limit 200 cuts to
    # `cleaned[:197] + "..."`. Leaving both in place would cut the value
    # TWICE, to 194 characters. The helper output is byte-identical to the
    # code removed for a control-character-free input, so the truncation
    # behaviour at this site does not change; only the control-character
    # collapse and the outer strip are new.
    context = _sanitize_prompt_field(str(memory.get("context") or ""))
    if context:
        lines.append(f"**Context**: {context}")

    # Add goal if present
    goal = _sanitize_prompt_field(str(memory.get("goal") or ""))
    if goal:
        lines.append(f"**Goal**: {goal}")

    # Add memory ID if provided
    if memory_id:
        # AN IDENTIFIER, NOT FREE TEXT. The free-text bound of 200 is 3
        # times what the generator emits, and the store does not bound this
        # value at its ingress, so a caller-supplied id took the widest
        # bound in the classification.
        raw_id = str(memory_id)
        cleaned_id = _sanitize_prompt_field(
            raw_id, _REFRESH_IDENTIFIER_TRUNCATION_LIMIT
        )
        # THE SANITIZER IS ONE OF THE PATHS THAT CAN DROP THE RECOVERY
        # POINTER. It returns "" on an internal failure as well as for a value
        # made only of control characters, and the truthiness gate below cannot
        # tell the two apart. NON-EMPTY IN AND EMPTY OUT is the discriminator.
        # `_recover_identifier` then accepts the raw id unchanged or refuses
        # it. See its docstring for why it is an acceptor and not a second
        # sanitizer.
        #
        # THE LENGTH TEST IS THE SECOND ROUTE TO THE ACCEPTOR, AND THE CAUSE
        # IS RECORDED IN FULL AT THE TWIN BLOCK IN `_format_memory_entry`. In
        # short: a raw id past the bound is CUT to a non-empty value, so the
        # empty test cannot fire, and a cut key is INDISTINGUISHABLE FROM A
        # GOOD ONE at the reader. The acceptor refuses it and no line is
        # emitted.
        if raw_id and (
            not cleaned_id
            or len(raw_id) > _REFRESH_IDENTIFIER_TRUNCATION_LIMIT
        ):
            cleaned_id = _recover_identifier(raw_id)
        if cleaned_id:
            lines.append(f"{_MEMORY_ID_LABEL}: {cleaned_id}")

    return "\n".join(lines)


def sync_retrieved_to_claude_md(
    memories: List[Dict[str, Any]],
    query: str,
    scores: Optional[List[float]] = None,
    memory_ids: Optional[List[str]] = None,
    claude_md_root: Optional[Path] = None
) -> SyncResult:
    """
    Sync retrieved memories to the Retrieved Context section of CLAUDE.md.

    Maintains a rolling window of the last 3 retrieved memories. New entries
    are added at the top of the section, and entries beyond MAX_RETRIEVED_MEMORIES
    are removed.

    Args:
        memories: List of memory dictionaries that were retrieved.
        query: The search query used.
        scores: Optional list of similarity scores (same order as memories).
        memory_ids: Optional list of memory IDs (same order as memories).
        claude_md_root: Declared containment anchor, exactly as on
            `sync_to_claude_md`. Omit it for today's behaviour.

    Returns:
        `SyncResult`. `bool()` of it is the value this function returned
        before the conversion below, and `.reason` says WHY when it is false.

    THIS IS THE SECOND AMBIENT WRITER AND IT HAD NO REFUSAL AT ALL. The save
    path at least had the `target` escape hatch; this one takes no target, so
    every call resolved ambiently with nothing standing between a test process
    and the operator's live file. It is reached from `PACTMemory.search()`
    whenever `sync_to_claude` is true, WHICH IS THE DEFAULT -- so an ordinary
    search was a write.

    ITS SIGNATURE WAS `bool` DELIBERATELY, AND THIS CONVERSION ANSWERS THAT
    DECISION RATHER THAN IGNORES IT. THE RECORDED PARAGRAPH CARRIED THREE
    CLAIMS AND TWO OF THEM NEED AN ANSWER HERE. The third, "this function
    is not being converted", was a statement of intent at the time, and the
    conversion settles it by being the act it describes, so it needs no
    separate answer. A reader who counts three and finds two answered has
    not met a claim that went missing.

    THE CONTINGENT ONE: "an annotation promising `SyncResult` here would
    describe code that returns `False`". That held only while the return
    sites stayed bare bools. ALL FIVE OF THEM ARE CONVERTED, so the
    annotation now describes the code and the cause is spent.

    THE OWNERSHIP ONE, WHICH THE CONVERSION DOES NOT TOUCH AND WHICH IS
    SUPERSEDED RATHER THAN DISCHARGED: the paragraph said the reason
    channel BELONGS to `sync_to_claude_md`. That was a true statement of
    the arrangement at the time and it is not contingent on this function.
    The architect superseded it: the channel is shared by both writers,
    because a caller of either one has the same need to separate a refusal
    from a no-op.

    WHY IT WAS WORTH CONVERTING, STATED WITHOUT OVERCLAIM. A REFUSED write and
    a DID-NOT-WRITE were one observation for every caller. THIS DOES NOT REPAIR
    A LIVE PRODUCTION OBSERVATION: the one production caller discards the
    return value, so no shipped code reads the bool today. What the conversion
    buys is CONSISTENCY with the sibling and a DIAGNOSTIC channel for a future
    caller and for the suite.

    NO TRUTHINESS READER CHANGES BEHAVIOUR, AND THAT IS NARROWER THAN "no
    caller". `SyncResult.__bool__` is `wrote`, so a truthiness read gets the
    value the bare bool gave. AN IDENTITY COMPARISON IS A DIFFERENT MATTER and
    `__bool__` cannot rescue it: `x is True` is False for an instance of this
    class however `bool(x)` reads. The suite held seven such comparisons and
    each one changed with this conversion.
    """
    if not memories:
        return SyncResult(SyncResult.EMPTY)

    # Same refusal as the save path, and the same exemptions: a declared anchor
    # is checked, so it is a stronger warrant than a named target. There is no
    # `target` parameter on this function, so the anchor is the ONLY way a test
    # process can legitimately drive it.
    _refuse_ambient_target_under_pytest(None, claude_md_root)

    # The disagreement guard joins it: this function is the FOURTH write path,
    # and its raise lands in the `except Exception` swallow at the tail, whose
    # warning interpolates the refusal text — so the diagnostic names both
    # values and the remedy even though the reason channel reads FAILED (the
    # one production caller passes sync_to_claude=False, so the guard is inert
    # in production today; it protects the path's future re-enablement).
    _refuse_ambient_sync_on_project_dir_disagreement(None, claude_md_root)

    resolve_errors: List[str] = []
    claude_md_path, resolved_root = _resolve_display_claude_md_with_base(
        errors=resolve_errors
    )
    _log_resolve_errors(resolve_errors)
    _refuse_ambient_sync_on_declared_scope_escape(
        None, claude_md_root, resolved_root, claude_md_path
    )

    # Declared anchor replaces the containment base; it does not steer
    # resolution. The two Nones stay in separate variables for the same reason
    # they do in the sibling.
    project_root = (
        Path(claude_md_root) if claude_md_root is not None else resolved_root
    )

    if claude_md_path is None or project_root is None:
        logger.debug("CLAUDE.md not found, skipping retrieved context sync")
        return SyncResult(_unresolved_reason(claude_md_path, resolve_errors))

    # EXISTENCE GUARD. The `is None` check above is not sufficient: it covers a
    # resolver that finds NOTHING, not a resolver that returns a path to a file
    # that is not there. A TOTAL resolver never returns None, so it would pass
    # straight through into the lock and the read.
    #
    # ONE ROUTE, ONE MESSAGE. Unlike `sync_to_claude_md` this function takes no
    # explicit target, so there is no second way to arrive here and nothing to
    # differentiate -- the cause is always the ambient resolver. Do not
    # restructure it into a branch join to match its sibling: the asymmetry in
    # the two guards reflects a real asymmetry in the two signatures.
    #
    # WITHOUT THIS, AN ABSENT PATH DOES NOT MERELY LEAVE A LOCK SIDECAR -- IT
    # CREATES DIRECTORIES. `file_lock` makes the sidecar's parents, so a
    # resolved path three levels deep materialises the whole chain before the
    # read fails: `a/`, `a/b/`, `a/b/c/`, `a/b/c/.CLAUDE.md.lock`. Measured.
    # That is a write to a location the caller never named, on a path whose
    # only job was to be read.
    #
    # NAMES BOTH CAUSES, ASSERTS NEITHER: a file removed after it resolved is
    # indistinguishable here from a resolver that stopped being partial, and
    # the first is not a defect at all.
    if not claude_md_path.exists():
        logger.warning(
            "resolved display CLAUDE.md %s does not exist, skipping retrieved "
            "context sync (this never creates CLAUDE.md). Either the display "
            "resolver stopped returning only existing paths, or the file was "
            "removed after it resolved.", claude_md_path
        )
        return SyncResult(SyncResult.MISSING)

    # Same position and same reasons as in the sibling: below resolution because
    # it judges the RESOLVED destination, above `file_lock` because that call
    # creates directories.
    _refuse_ambient_sync_from_a_redirected_store(
        None, claude_md_root, claude_md_path
    )

    try:
        # Serialize the FULL read-modify-write window under the shared sidecar
        # lock (see the "why lock the whole window" note above file_lock for the
        # read-under-lock / lock-identity / CLAUDE_PROJECT_DIR rationale).
        with file_lock(claude_md_path):
            # Read current content
            content = claude_md_path.read_text(encoding="utf-8")

            # Parse existing retrieved context section
            parsed = _parse_retrieved_context_section(content)
            if parsed is None:
                # The same decline as the sibling writer, for the same cause.
                return SyncResult(SyncResult.NO_WINDOW)
            before_section, section_header, after_section, existing_entries = parsed

            # Format new entries (only the top result to avoid clutter)
            new_entries = []
            top_memory = memories[0]
            score = scores[0] if scores else None
            memory_id = memory_ids[0] if memory_ids else None
            new_entry = _format_retrieved_entry(top_memory, query, score, memory_id)
            new_entries.append(new_entry)

            # Build new entries list: new entry first, then existing (up to max - 1)
            all_entries = new_entries + existing_entries
            trimmed_entries = all_entries[:MAX_RETRIEVED_MEMORIES]

            # Apply the PER-ENTRY token ceiling first, then the section
            # budget. THIS IS A SECOND CEILING SITE AND IT IS DIFFERENT CODE
            # FROM `_apply_token_budget`, which the Working Memory sync
            # calls. A ceiling placed only in that function reaches that
            # section alone and leaves this loop open. Each of the
            # MAX_RETRIEVED_MEMORIES entries gets an equal share here,
            # because this loop DROPS without compressing, so there is no
            # compressed-neighbour saving to redistribute.
            entry_ceiling = RETRIEVED_CONTEXT_TOKEN_BUDGET // MAX_RETRIEVED_MEMORIES
            trimmed_entries = [
                _apply_entry_token_ceiling(e, entry_ceiling) for e in trimmed_entries
            ]

            # Reduce entry count if over budget. Retrieved entries are
            # bounded per FIELD by `_format_retrieved_entry`; drop oldest
            # rather than compress. THE DROP-RATHER-THAN-COMPRESS CHOICE IS
            # DELIBERATE and the per-entry ceiling above is derived from it.
            # Subtract the popped entry's tokens instead of recalculating the full sum.
            total_tokens = sum(_estimate_tokens(e) for e in trimmed_entries)
            while len(trimmed_entries) > 1 and total_tokens > RETRIEVED_CONTEXT_TOKEN_BUDGET:
                removed = trimmed_entries.pop()
                total_tokens -= _estimate_tokens(removed)

            # Build new section content
            section_lines = [
                RETRIEVED_CONTEXT_HEADER,
                RETRIEVED_CONTEXT_COMMENT,
                ""  # Blank line after comment
            ]
            for entry in trimmed_entries:
                section_lines.append(entry)
                section_lines.append("")  # Blank line between entries

            section_text = "\n".join(section_lines)

            # Reconstruct file content
            if section_header:
                # Section existed, replace it
                # Ensure blank line before next section
                if after_section and not after_section.startswith("\n"):
                    new_content = before_section + section_text + "\n" + after_section
                else:
                    new_content = before_section + section_text + after_section
            else:
                # Section didn't exist, insert before Working Memory if it exists
                working_memory_match = re.search(
                    r'^## Working Memory',
                    content,
                    re.MULTILINE
                )
                if working_memory_match:
                    # Insert before Working Memory with blank line
                    insert_pos = working_memory_match.start()
                    new_content = content[:insert_pos] + section_text + "\n" + content[insert_pos:]
                else:
                    # Append at end
                    if not content.endswith("\n"):
                        content += "\n"
                    new_content = content + "\n" + section_text

            # Write back to file (atomic: temp + rename, so a crash mid-write
            # cannot leave the always-loaded CLAUDE.md truncated)
            _atomic_write_text(claude_md_path, new_content, project_root)

        logger.info("Synced retrieved memories to CLAUDE.md Retrieved Context section")
        return SyncResult(SyncResult.WROTE)

    except Exception as e:
        logger.warning(f"Failed to sync retrieved memories to CLAUDE.md: {e}")
        return SyncResult(SyncResult.FAILED)
