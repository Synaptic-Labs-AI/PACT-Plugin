"""
Location: pact-plugin/hooks/shared/claude_md_manager.py
Summary: CLAUDE.md file manipulation for PACT environment setup.
Used by: session_init.py during SessionStart hook to scaffold the project
         CLAUDE.md PACT_MANAGED region (outer boundary, session block,
         PACT_MEMORY-wrapped memory sections) and to migrate legacy project
         CLAUDE.md files into the boundary structure.

Manages the project CLAUDE.md at $CLAUDE_PROJECT_DIR — preferred at
.claude/CLAUDE.md, legacy at ./CLAUDE.md — with the PACT_MANAGED outer
boundary, optional SESSION_START/SESSION_END block, and PACT_MEMORY inner
boundary wrapping memory sections.

Project CLAUDE.md location resolution:
Claude Code supports two locations for project-level memory:
  - $CLAUDE_PROJECT_DIR/.claude/CLAUDE.md  (preferred / new default)
  - $CLAUDE_PROJECT_DIR/CLAUDE.md          (legacy)
The resolve_project_claude_md_path() helper picks whichever exists, with
.claude/CLAUDE.md taking priority. When neither exists, it returns the new
default path so creators land at the preferred location.
"""

from __future__ import annotations

import fcntl  # Unix-only; PACT supports macOS/Linux. No Windows compat shim.
import os
import re
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from .failure_cause import failure_cause
from .paths import get_claude_config_dir

# The finder is imported inside the functions that use it: shared/__init__.py
# imports this module, so a module-level import would load it in every hook.
if TYPE_CHECKING:
    from .claude_md_markers import Document, Line, Located

# Project-level CLAUDE.md is preferred at .claude/CLAUDE.md (the new default)
# but Claude Code also accepts ./CLAUDE.md for backwards compatibility.
_DOT_CLAUDE_RELATIVE = Path(".claude") / "CLAUDE.md"
_LEGACY_RELATIVE = Path("CLAUDE.md")

# Concurrency guard: callers performing read-mutate-write on managed
# CLAUDE.md files (ensure_project_memory_md, migrate_to_managed_structure,
# session_resume.update_session_info) acquire this lock to prevent two
# concurrent session_init hooks (e.g., resuming session A while starting
# session B on the same project) from interleaving and clobbering each
# other's writes. A sidecar lock (`.{filename}.lock` adjacent to the
# target) serializes the critical sections.
#
# Sidecar is chosen over direct target-file locking because:
#   1. The target file may be recreated (rename/delete) during the write; a
#      sidecar lock file is independent of the target's inode lifetime.
#   2. Locking the target itself would interleave with its own read/write.
#   3. Sidecar is standard UNIX practice for cross-process coordination.
_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_POLL_INTERVAL = 0.1


@contextmanager
def file_lock(target_file: Path):
    """Acquire an exclusive sidecar file lock for a target CLAUDE.md path.

    Not re-entrant: nested acquisition from the same thread will deadlock
    (TimeoutError after ``_LOCK_TIMEOUT_SECONDS``).

    Creates (or opens) a sidecar named `.{target_file.name}.lock` inside the
    RESOLVED parent directory -- `target_file.parent.resolve()`, not the
    unresolved parent, so two spellings of one directory share one sidecar --
    and takes an ``fcntl`` exclusive advisory lock on its file descriptor.
    Polls with non-blocking acquire + sleep so a stuck holder cannot hang
    session_init forever: raises ``TimeoutError`` after
    ``_LOCK_TIMEOUT_SECONDS``.

    The lock file is intentionally NOT cleaned up on exit. Stale lock files
    are cheap (an empty byte-0 file per managed target), and removing the
    sidecar inside the lock window is a classic race: another waiter may
    have already opened the same path and would be locking a now-orphaned
    inode. Leaving the file in place is correct and safe.

    Args:
        target_file: The managed CLAUDE.md path whose read-mutate-write
            section must be serialized. Must have an existing parent
            directory (caller ensures this); this function does not
            create parents for the target, only for the sidecar lock.

    Raises:
        TimeoutError: Lock not acquired within the timeout window. Caller
            should treat this as a transient failure and return a
            fail-open status string so session_init can surface it.
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
        # Each step ignores its own OSError: the locked work is over, so a
        # release failure must not fail a body that succeeded or replace the
        # error of one that did not.
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(lock_fd)
        except OSError:
            pass

# Outer boundary wrapping all PACT-managed content in project CLAUDE.md.
# User-owned content goes OUTSIDE this block.
MANAGED_START_MARKER = "<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->"
MANAGED_END_MARKER = "<!-- PACT_MANAGED_END -->"

# Inner boundary wrapping project memory sections (Retrieved Context,
# Pinned Context, Working Memory) for hook targeting (#404).
MEMORY_START_MARKER = "<!-- PACT_MEMORY_START -->"
MEMORY_END_MARKER = "<!-- PACT_MEMORY_END -->"

# The auto-managed comment each memory heading carries. NAMED HERE so the two
# writers in THIS module cannot drift apart, which they did: the creation
# template emitted these and the migration did not, so a migrated document
# came out with headings and no comments.
#
# THE TEXT IS SPELLED HERE RATHER THAN IMPORTED, AND THAT IS FORCED RATHER THAN
# PREFERRED. The canonical definition is `WORKING_MEMORY_COMMENT` and
# `RETRIEVED_CONTEXT_COMMENT` in `skills/pact-memory/scripts/working_memory.py`.
# THIS MODULE CANNOT IMPORT THEM. A hook runs as
# `python3 <plugin_root>/hooks/<name>.py`, so `hooks/` is the only entry on
# `sys.path` and `working_memory` does not resolve. MEASURED with
# `importlib.util.find_spec` on that reconstructed path: NOT FOUND. Do not
# "tidy" these into an import: it resolves inside pytest, because `conftest.py`
# adds paths a hook does not have, and raises for every real user.
#
# `TestTheManagedCommentsAgreeAcrossEveryWriter` in
# `tests/test_managed_comment_mirror.py` holds this copy to that definition.
# `## Pinned Context` carries no comment in the template, so it gets none here.
RETRIEVED_CONTEXT_COMMENT = "<!-- Auto-managed by pact-memory skill. Last 3 retrieved memories shown. -->"
WORKING_MEMORY_COMMENT = "<!-- Auto-managed by pact-memory skill. Full history searchable via pact-memory skill. Keyed by folder name, so another checkout with the same name shares this section. -->"

# Declared START boundary of the `## Pinned Context` section. The pinned
# region's extent was INFERRED before this pair existed -- every reader guessed
# where the section ends from a terminator pattern. This literal declares where
# it BEGINS, and PINNED_END_MARKER below declares where it ENDS.
#
# THE PAIR SHIPS WITH A MARKER-AWARE WRITER, AND THAT COUPLING IS THE WHOLE
# SAFETY ARGUMENT. An END marker alone CREATES a gap rather than closing one: a
# writer that anchors on the heading and appends at the end of the section
# places a new pin BELOW the END marker, where no cap measures it. Measured, at
# a cap of 12 with a 13th pin appended: no markers denies, a pair with an
# insertion INSIDE denies, and a pair with an append BELOW the END is ALLOWED.
# So the marker and the writer that respects it are one unit. Neither ships
# alone.
#
# A PRIOR REVISION OF THIS COMMENT ARGUED THAT NO END MARKER COULD EXIST, and
# every load-bearing claim in it was FALSIFIED BY EXECUTION. It said a matched
# END was an enforcement change in disguise and that an unmatched one inflated
# the last pin's charge by 26 characters. Run against the removed code: the
# removed marker WAS matched, it sat AFTER the pinned body, and it charged
# ZERO. Body length, pin count and per-pin charges were byte-identical to the
# same document with no markers at all. The magnitude 26 is unreproduced -- do
# not reconstruct a source for it. The coupling it drew to a later strip has no
# basis either, because there is no charge to remove.
#
# STATE THE CONCLUSION PRECISELY, because a wrong one sits next to it. This does
# NOT show the removal was groundless. It shows that the UNMATCHED-NAME arm was
# inapplicable here, since no unmatched name was ever used. The matched-name arm
# is a REAL hazard, and its remedy is the marker-aware writer above, not the
# absence of a marker.
#
# TWO PROPERTIES OF THIS EXACT NAME ARE LOAD-BEARING, both measured:
#
# 1. The name carries a `PACT_MEMORY_` prefix, so it is matched by every
#    scan-terminator alternation built from PACT_BOUNDARY_PREFIXES below. That
#    is not decoration. Canonical section order puts `## Retrieved Context`
#    ABOVE `## Pinned Context`, so a start marker on a new line above the
#    pinned heading sits inside the span that the Retrieved Context writer
#    REBUILDS from recognised entries only. A marker the alternation does not
#    match fails to terminate that scan, rides the last dated entry as a
#    passenger, and is deleted when rotation evicts that entry -- or at the
#    first save when the section holds no dated entry. A matched marker
#    terminates the scan at the true end of Retrieved Context.
#
#    THE TWO MARKERS CARRY DIFFERENT RISK, and only the END one needs a writer
#    to guard it. This START marker cannot open an enforcement hole, because its
#    gap lies ABOVE the `## Pinned Context` heading and the pinned scan BEGINS at
#    that heading. Gap content there is outside the pinned region by every
#    reading, with a marker or without one. A boundary marker can only open an
#    enforcement hole on the side where the section's content legitimately
#    lives, and nothing above the heading calls itself a pin. Below the END
#    marker is exactly that side, which is why the pair needs a marker-aware
#    writer and this marker alone never did.
#
# 2. The literal CONTAINS no existing marker literal as a substring, and the
#    qualifier sits BEFORE the START word for that reason. Containment matters
#    because the parser's stray rule reads a row that holds a marker literal
#    anywhere but on a marker line of its own as MALFORMED: a literal that
#    contained the memory start marker would make every memory-block lookup,
#    and every writer that depends on one, refuse.
PINNED_START_MARKER = "<!-- PACT_MEMORY_PINNED_START -->"

# Declared END boundary of the `## Pinned Context` section. Read by
# `staleness._parse_pinned_section`, emitted by `pin_marker_writer.py` in the
# same composition as its START twin.
#
# THE `PACT_MEMORY_` PREFIX IS THE LOAD-BEARING PART, and it is what makes the
# declared parse a no-op on every document that carries this exact name. The
# prefix puts the marker in every scan-terminator alternation built from
# PACT_BOUNDARY_PREFIXES below, so the INFERRED forward scan already stops at
# this line. Declared and inferred therefore agree, and every reader this change
# does not touch keeps the extent it already had.
#
# SO WHAT DOES THE DECLARED PARSE BUY? It converts an INCIDENTAL exclusion into
# an INTENTIONAL one. Today this marker escapes the pinned body only because its
# NAME happens to match a generic alternation. Measured: rename it out of the
# family and the inferred scan overruns it and charges the marker text, while a
# declared parse still excludes it. The declared parse is a guard against a
# future rename, not a repair of a present fault. That is the only measured
# difference between the two parses, and a certification that omits the rename
# arm proves nothing at all.
#
# TWO PROPERTIES OF THIS EXACT NAME ARE LOAD-BEARING, and they are the same two
# that govern the START twin:
#
# 1. The `PACT_MEMORY_` prefix, as above.
# 2. The literal CONTAINS no existing marker literal as a substring, so the
#    parser's stray rule never reads its row as a misplaced copy of one, which
#    would make that marker's lookup MALFORMED. The qualifier sits BEFORE the
#    END word for that reason, exactly as the START twin puts it before START.
#    Note that `PINNED_START_MARKER` is NOT a substring of this literal and
#    this literal is not a substring of it: the two differ before either
#    reaches its final word.
PINNED_END_MARKER = "<!-- PACT_MEMORY_PINNED_END -->"

# Canonical H1 title for the managed block. Extracted as a constant so
# the three template sites (ensure_project_memory_md, _plan_migration,
# session_resume.update_session_info Case 0) cannot drift apart. Changing this
# value changes the title everywhere in one place.
MANAGED_TITLE = "# PACT Framework and Managed Project Memory"

# Plugin-managed HTML comment boundary prefixes. Used by parsers and regex
# sites that need to terminate scans on any PACT-managed boundary marker.
# Extracted as a constant so the three-prefix union is defined once.
#
# Twin copy: working_memory.py maintains `_PACT_BOUNDARY_ALT`, a STRING that
# spells this tuple as a regex alternation, because skills/pact-memory/scripts/
# cannot cleanly import from hooks/shared/. A drift-detection test asserts the
# twin string equals the alternation built from this tuple.
PACT_BOUNDARY_PREFIXES: tuple[str, ...] = (
    "PACT_MEMORY_",
    "PACT_MANAGED_",
    "PACT_ROUTING_",
)

# Regex alternation used by scan-terminator patterns in this module.
# Mirrors the `_BOUNDARY_ALT` constant in `staleness.py`:
# any regex that needs to terminate on a PACT boundary marker must embed
# this alternation rather than hard-coding the three-prefix literal. That
# way, adding a fourth prefix to `PACT_BOUNDARY_PREFIXES` automatically
# picks it up everywhere via a one-line constant change.
_BOUNDARY_ALT = "|".join(PACT_BOUNDARY_PREFIXES)

# Session-block boundary markers, and the scan-terminator prefix DERIVED from
# them.
#
# THESE ARE NOT MEMBERS OF `PACT_BOUNDARY_PREFIXES` AND MUST NOT BE ADDED TO
# IT. They carry no `PACT_` prefix, so a SESSION member makes that name
# incorrect about its own contents.
#
# THE PREFIX IS DERIVED AND NOT DECLARED, AND THAT IS THE WHOLE POINT.
# MEASURED: the marker text was spelled SIX times in THREE pairs, each one
# local to a function or to a template, with no module-level constant
# anywhere. A hand-written prefix beside them would have been a SEVENTH
# spelling that unifies none of the others, and the failure direction is the
# bad one: rename a marker at a producer, the prefix does not follow, and a
# terminator that matches nothing stops nothing. Deriving it removes that
# drift axis rather than adding to it.
SESSION_START_MARKER = "<!-- SESSION_START -->"
SESSION_END_MARKER = "<!-- SESSION_END -->"
# Derived from the COMMON PREFIX OF THE TWO MARKERS, so a rename of either
# one carries into every scan that terminates on them. A literal here would
# be the seventh spelling.
#
# THE COMMON PREFIX AND NOT THE START MARKER ALONE. A first version read the
# START marker only. Rename the END marker by itself and that version keeps
# returning a prefix which matches the START marker, so the terminator goes
# on and the END marker escapes it with nothing red. The common prefix of
# the two shrinks the moment either name moves.
SESSION_BOUNDARY_PREFIX = os.path.commonprefix(
    [SESSION_START_MARKER, SESSION_END_MARKER]
).removeprefix("<!-- ")

# 🔴 AND THE DERIVATION HAS ITS OWN FAILURE MODE, WHICH THIS REFUSES.
# MEASURED: rename the END marker to a name that shares NO word with the
# START marker and the common prefix is the EMPTY STRING. An empty term in a
# scan-terminator alternation matches EVERY HTML comment, so every section
# body stops at its first comment and the sections TRUNCATE. That is the
# silent-loss direction, and it arrives from a one-line edit above.
#
# The refusal is loud and it is at import. A gate that cannot import this
# module takes its own fail-open path and allows the edit, which is the safe
# direction. A wildcard terminator is not.
if not SESSION_BOUNDARY_PREFIX:
    raise ValueError(
        "SESSION_BOUNDARY_PREFIX derived empty: "
        f"{SESSION_START_MARKER!r} and {SESSION_END_MARKER!r} share no "
        "common prefix. An empty prefix matches every HTML comment and "
        "truncates every section scan. Give the two markers a common name."
    )

# Stale line from the legacy project CLAUDE.md template. The line lingers
# in upgraded files; strip it during migration. Allows optional trailing
# period / whitespace.
#
# Matched against the parser's visible PROSE rows only (`find_lines` rows
# whose `in_html` is False), never against the whole text with `re.MULTILINE`:
# a fenced, code or uncertain row that quotes the line, or a row inside an
# HTML block that hides it, is the user's text and stays. A row's content
# holds no line break, so `$` needs no flag.
_STALE_ORCHESTRATOR_LINE_RE = re.compile(
    r"^The global PACT Orchestrator is loaded from `~/\.claude/CLAUDE\.md`\.?\s*$",
)


class ContainmentError(OSError):
    """A CLAUDE.md write target escaped its project containment boundary (#1247).

    Subclasses OSError so a caller that does not name it explicitly still
    catches it via `except OSError`. Callers convert it to an OPAQUE skip
    message ("path precondition not met") that does not leak the resolved
    victim path -- matching what the leaf `is_symlink` guards returned before
    containment replaced them.

    Twin of ContainmentError in
    `skills/pact-memory/scripts/working_memory.py` (skills cannot import from
    hooks/shared). The two class defs are trivial markers; the load-bearing
    logic is the containment CHECK inside `_atomic_write_text`, drift-gated by
    TestAtomicWriteTwinCopyDrift.
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
    A read failure reports LF for the same reason, which is what this code did
    before the detection moved here.

    Twin copy: `skills/pact-memory/scripts/working_memory.py` carries this
    function because that package cannot import from `hooks/shared/`. The two
    bodies are gated identical by TestLineEndingHelperTwinCopyDrift.

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

    Twin copy: `skills/pact-memory/scripts/working_memory.py` carries this
    function because that package cannot import from `hooks/shared/`. The two
    bodies are gated identical by TestLineEndingHelperTwinCopyDrift.

    Args:
        content: Full file contents, with any line endings.
        line_ending: The ending to write, from `_detect_line_ending`.

    Returns:
        `content` with its endings replaced, or unchanged when the target is LF.
    """
    if line_ending == "\n":
        return content
    return content.replace("\r\n", "\n").replace("\n", line_ending)


def _read_replaced(path: Path) -> str:
    """`path` decoded with replacement, for a rewriter whose strict decode just
    failed to ask whether it had anything to do. Replacement never swallows an
    ASCII byte, so every marker sits where the bytes put it. NEVER WRITE THE
    RESULT BACK: that would put U+FFFD over the user's bytes. An unreadable
    file reads as empty.
    """
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


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
    `skills/pact-memory/scripts/working_memory.py`, which cannot import from
    `hooks/shared/` (separate package). This twin IS drift-gated by
    TestAtomicWriteTwinCopyDrift (mirroring TestFileLockTwinCopyDrift): the
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
        # The only step after the rename. A close that fails once the rename
        # has put the new file in place is not a failed write, and after a
        # failed write it must not replace the error that stopped it.
        try:
            os.close(parent_fd)
        except OSError:
            pass


def _legacy_spans_after_cuts(doc: Document, cuts: list[tuple[int, int]],
                             header_end: int) -> list[tuple[int, int]]:
    """The stale loader lines the ORIGINAL file `doc` reads as visible prose,
    as spans in the text left after dropping `cuts` from `doc.text` and then
    its first `header_end` characters. Parsing that text instead would decide
    on a text the cuts have changed: a cut session block that closes an HTML
    block can leave a fenced quote below it reading as prose. A row inside a
    cut maps to an empty span, so it stays; a row a cut only touches is a
    marker row, and the header's rows (the title, blank rows, the
    description) never match."""
    return [(_offset_after_drop(start, cuts) - header_end, _offset_after_drop(end, cuts) - header_end)
            for start, end in _legacy_line_spans(doc)]


def _legacy_line_spans(doc: Document) -> list[tuple[int, int]]:
    """The (start, end) span in `doc.text` of each stale loader line the
    parser reads as visible prose (`find_lines` rows whose `in_html` is
    False), with its line break. A copy inside an HTML block that hides it,
    such as a comment, is the user's text and stays. A leading U+FEFF is not
    part of row 0's span, so it stays at byte 0."""
    return [(doc.row_start(row), doc.lines[row].end)
            for row in doc.find_lines(_STALE_ORCHESTRATOR_LINE_RE)
            if not doc.lines[row].in_html]



# The obsolete kernel block's markers. The start is a prefix: the marker line
# carries a version (`<!-- PACT_START: v3 -->`).
_KERNEL_START_MARKER = "<!-- PACT_START:"
_KERNEL_END_MARKER = "<!-- PACT_END -->"


def _marker_span(doc: Document, row: int) -> tuple[int, int]:
    """(start, end) in the original text of the marker comment on `row`,
    without the up-to-3-space indent before it or the blanks after it."""
    content = doc.lines[row].content
    start = doc.row_start(row)
    return start + len(content) - len(content.lstrip(" ")), start + len(content.rstrip(" \t"))


def _plan_kernel_strip(
    content: str, target_file: Path
) -> tuple[str | None, str | None]:
    """Decide the kernel strip for `content`: (notice, new_content).

    `new_content` is `content` with the kernel block removed, or None when the
    strip is not due. `notice` is then what the pass returns: None with no
    block, or why the block was left alone. Pure, so a file that is not valid
    UTF-8 gets the same decision from its replace-decoded copy. The block is
    located by the parser, so a fenced or inline-code mention is not it. A
    removal that would leave part of the file unreadable is skipped, with the
    line named.
    """
    from .claude_md_markers import State, parse, uncertainty_added

    doc = parse(content)
    block = doc.find_block(_KERNEL_START_MARKER, _KERNEL_END_MARKER)
    if block.state is State.ABSENT:
        # Normal idempotent no-op for already-migrated installs.
        return None, None
    if block.state is State.UNKNOWN and not (
        doc.may_hold(_KERNEL_START_MARKER) or doc.may_hold(_KERNEL_END_MARKER)
    ):
        # The file runs on into an uncertain region (an unclosed fence), but
        # no kernel marker text is in it, so there is no block to strip and
        # nothing to report.
        return None, None
    if block.state is not State.FOUND:
        # A duplicate, a marker with no partner, or a block the parser
        # cannot place: a defensive no-op to avoid data loss. The status
        # names the line, and session_init routes it via systemMessage.
        return (
            f"Migration skipped: {target_file}: {block.reason}. To avoid data "
            "loss the file was left unchanged; inspect it and remove or "
            "repair the PACT_START / PACT_END kernel markers."
        ), None

    first, last = block.spans[0]
    pre_marker = content[:_marker_span(doc, first)[0]]
    post_marker = content[_marker_span(doc, last)[1]:]

    # Preserve one blank line at the removal boundary so the
    # user's spacing around the obsolete block survives the strip. A leading
    # byte order mark is not text above the block: the file strips as it would
    # without one, and the mark stays first.
    bom = "\ufeff" if pre_marker.startswith("\ufeff") else ""
    pre_clean = pre_marker[len(bom):].rstrip("\r\n")
    post_clean = post_marker.lstrip("\r\n")
    if pre_clean and post_clean:
        new_content = bom + pre_clean + "\n\n" + post_clean
    elif pre_clean:
        new_content = bom + pre_clean + "\n"
    else:
        new_content = bom + post_clean
    # The block can hold the line that closes an HTML block the user opened
    # above it; removing it would leave the rest of the file unreadable.
    reason = uncertainty_added(doc, parse(new_content))
    if reason:
        return (
            f"Migration skipped: {target_file}: {reason}. To avoid data loss the "
            "file was left unchanged; inspect it and close the HTML block that "
            "line opens."
        ), None
    return None, new_content


def strip_orphan_kernel_block() -> str | None:
    """
    SUNSET BEFORE v5.0.0: one-version-window migration helper.

    Strips the obsolete `<!-- PACT_START:... -->...<!-- PACT_END -->` kernel
    block from `~/.claude/CLAUDE.md` if present. The block was injected by
    pre-v4.0 plugin versions that delivered the orchestrator persona via
    home-dir CLAUDE.md routing; v4.0+ delivers the persona via the
    `claude --agent` flag instead, so the block is now stale.

    Called from session_init.py on every SessionStart. Idempotent no-op
    when the markers are absent (i.e., for fresh installs or after first
    cleanup). Once the v4.0.0 release has been in the field long enough
    that resumed users will have hit at least one v4.x SessionStart, this
    function and its caller can be deleted.

    Hardening:
    - Symlink guard inside the lock (TOCTOU defense): refuses to operate
      if `~/.claude/CLAUDE.md` is a symlink. Practical exploitability is
      low (requires pre-existing local write access) but the defensive
      guard is cheap.
    - Malformed-pair feedback: when the strip skips a block the parser cannot
      place (a duplicate, an orphan marker, END before START, or a block past
      an unclosed fence), returns the warning, naming the line, as a status
      string so session_init.py surfaces it via systemMessage.
      Hook stderr is NOT shown to users by Claude Code, so a returned
      string is the only way to deliver the warning.

    Returns:
        Status message on successful removal, None on no-op (clean,
        absent markers) or error, or a "Migration skipped: ..." string
        on defensive no-op (malformed marker state; session_init.py
        routes these to systemMessages via the "failed"/"skipped" check).
    """
    target_file = get_claude_config_dir() / "CLAUDE.md"
    if not target_file.exists():
        return None

    # Concurrency guard: serialize read-mutate-write so two concurrent
    # session_init hooks on the same home file cannot clobber each other.
    # Fail-open on timeout — next session start will retry.
    try:
        with file_lock(target_file):
            # #1247: the containment check in _atomic_write_text REPLACES the
            # former leaf is_symlink guard. It runs inside this lock (TOCTOU-
            # safe, since callers hold file_lock) and is the RIGHT control
            # here: kernel-object ancestry on a pinned parent descriptor
            # catches the symlinked-PARENT escape the leaf is_symlink guard
            # MISSED (F1). No resolver runs inside the guard -- see
            # _atomic_write_text; do NOT reintroduce one, and in particular do
            # not resolve the target and reuse it downstream, which would make
            # the WRITE follow the leaf.
            # It does NOT dominate is_symlink -- the two catch
            # overlapping-but-different sets: containment safely ALLOWS a
            # benign in-project leaf redirect (os.replace swaps the leaf, no
            # write-through) that the old blanket guard refused.
            try:
                content = target_file.read_text(encoding="utf-8")
            except OSError:
                return None
            except UnicodeDecodeError:
                # This pass rewrites the file, so it is decoded strictly and
                # left untouched rather than written back with U+FFFD. When the
                # strip was not due, the replaced copy gets the valid file's
                # own result. The copy is never written.
                notice, due = _plan_kernel_strip(_read_replaced(target_file), target_file)
                if due is None:
                    return notice
                return (
                    f"Kernel block strip skipped: {target_file} is not valid "
                    "UTF-8, so it was left unchanged."
                )

            notice, new_content = _plan_kernel_strip(content, target_file)
            if new_content is None:
                return notice

            try:
                # anchor: GLOBAL config dir, NOT a project root -- do not unify
                # onto CLAUDE_PROJECT_DIR / a project root (R4). This file lives
                # at ~/.claude/CLAUDE.md, a different trust boundary; project-
                # rooting it would over-block every invocation.
                _atomic_write_text(
                    target_file, new_content, get_claude_config_dir()
                )
                return (
                    f"Removed obsolete PACT kernel block from {target_file}"
                )
            except ContainmentError:
                # Opaque skip, matching the message the removed is_symlink
                # guard returned -- do not leak the resolved victim path.
                # target_file is built at the top of this function as
                # get_claude_config_dir() / "CLAUDE.md" and is never resolved,
                # so it cannot be the symlink victim this message must not
                # leak. It names only the config root, which the platform
                # already injects into every agent context.
                return (
                    f"Migration skipped: {target_file} path "
                    "precondition not met."
                )
            except OSError as e:
                # `Failed` IS THE ROUTING TOKEN. session_init step 3c routes
                # this return into system_messages on a substring test. The
                # prefix stays byte-identical; only the cause token changed,
                # from a cut of the caller's message (which carries the
                # absolute path an OSError attaches) to a closed vocabulary.
                return (
                    f"Failed to remove stale kernel block: {failure_cause(e)}"
                )
    except TimeoutError:
        return (
            f"Failed to acquire lock on {target_file} within 5s "
            "(another session_init hook may be running concurrently). "
            "Kernel-block migration skipped; will retry on next session "
            "start."
        )
    except OSError:
        # #1245: file_lock ACQUISITION (sidecar mkdir/open) can raise
        # PermissionError etc., which is not a TimeoutError and would escape
        # uncaught. The inner except handles post-acquisition write failures;
        # this catches acquisition failures at the same skip-and-retry level.
        # Opaque (no str(e)) so the sidecar path is not leaked into a status
        # string -- matches the sibling TimeoutError message's non-disclosure.
        return (
            f"Could not acquire lock on {target_file} "
            "(path precondition not met); kernel-block migration skipped."
        )


def extract_managed_region(content: str) -> tuple[str, int] | None:
    """
    Extract the PACT-managed region from a CLAUDE.md file.

    Returns the content between MANAGED_START_MARKER and MANAGED_END_MARKER
    (exclusive of the markers themselves), or None unless the fence-aware
    parser finds exactly one pair. A fenced or inline-code copy of a marker is
    not the region, and neither is a pair the parser cannot place.

    Args:
        content: Full CLAUDE.md file content.

    Returns:
        Tuple of (region_text, start_offset) where start_offset is the absolute
        offset of the first character after MANAGED_START_MARKER in the
        original content. Callers that need to write back to the full file must
        add start_offset to any positions computed within region_text.
        Returns None when the pair is not found.
    """
    from .claude_md_markers import State, parse

    doc = parse(content)
    region = doc.find_block(MANAGED_START_MARKER, MANAGED_END_MARKER)
    if region.state is not State.FOUND:
        return None
    first, last = region.spans[0]
    region_start = _marker_span(doc, first)[1]
    return content[region_start:_marker_span(doc, last)[0]], region_start


def resolve_project_claude_md_path(
    project_dir: str | os.PathLike[str],
) -> tuple[Path, str]:
    """
    Resolve the project-level CLAUDE.md path with dual-location support.

    Detection priority:
      1. $project_dir/.claude/CLAUDE.md   -> ("dot_claude", existing)
      2. $project_dir/CLAUDE.md           -> ("legacy", existing)
      3. Neither exists                    -> ("new_default", .claude/CLAUDE.md)

    Callers that only read use the returned Path directly. Callers that
    create the file use the source string to know whether they need to
    `mkdir` the `.claude/` parent directory first.

    Args:
        project_dir: The CLAUDE_PROJECT_DIR root.

    Returns:
        Tuple of (path, source) where source is one of:
          - "dot_claude": existing .claude/CLAUDE.md
          - "legacy": existing ./CLAUDE.md
          - "new_default": neither exists; path points to .claude/CLAUDE.md
            so a creator can write to the preferred location.
    """
    base = Path(project_dir)
    dot_claude = base / _DOT_CLAUDE_RELATIVE
    legacy = base / _LEGACY_RELATIVE

    if dot_claude.exists():
        return dot_claude, "dot_claude"
    if legacy.exists():
        return legacy, "legacy"
    return dot_claude, "new_default"


def ensure_dot_claude_parent(path: Path) -> None:
    """
    Ensure the parent directory of a `.claude/CLAUDE.md` path exists.

    No-op when the parent already exists as a directory. Creates the
    directory with mode 0o700 to match the rest of the PACT plugin's
    secure-by-default file permissions. Safe to call for any CLAUDE.md
    path -- if the parent is not a `.claude` dir, this is just an
    existence check.

    Raises early with a clear message when the parent path exists but is
    a regular file (e.g., a local attacker deliberately blocking mkdir
    by creating a file where `.claude/` should be). Without this guard
    the code path would fall through to the subsequent `_atomic_write_text`
    call, whose `os.open(parent, O_DIRECTORY)` fails on a non-directory
    parent and reports it as a ContainmentError -- accurate as a fail-closed
    refusal, but it names the containment boundary rather than the blocking
    file, so the clearer error belongs here.

    Args:
        path: The target CLAUDE.md path (e.g. /proj/.claude/CLAUDE.md).

    Raises:
        OSError: When `path.parent` exists but is not a directory. The
            caller (ensure_project_memory_md) catches OSError and
            returns a user-facing failure status string.
    """
    parent = path.parent
    if parent.exists() and not parent.is_dir():
        raise OSError(f"{parent} exists but is not a directory")
    if not parent.exists():
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)


def ensure_project_memory_md() -> str | None:
    """
    Ensure project has a CLAUDE.md with memory sections.

    Creates a minimal project-level CLAUDE.md containing the PACT-managed
    structure: outer PACT_MANAGED boundary, session block, and inner
    PACT_MEMORY boundary wrapping memory sections (Retrieved Context,
    Pinned Context, Working Memory) if one doesn't exist. These sections
    are project-specific and managed by the pact-memory skill.

    Honors both supported project CLAUDE.md locations:
      - $CLAUDE_PROJECT_DIR/.claude/CLAUDE.md  (preferred / new default)
      - $CLAUDE_PROJECT_DIR/CLAUDE.md          (legacy)
    If either exists, no action is taken (preserves existing project
    configuration). When neither exists, creates the file at the preferred
    `.claude/CLAUDE.md` location, creating the `.claude/` directory if needed.

    Returns:
        Status message or None if no action taken.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        return None

    target_file, source = resolve_project_claude_md_path(project_dir)

    # Don't overwrite existing project CLAUDE.md (either location)
    if source != "new_default":
        return None

    # Create minimal CLAUDE.md with memory sections at the new default location.
    # Structure (#404): outer PACT_MANAGED boundary wraps all plugin-managed
    # content; inner PACT_MEMORY boundary wraps the memory sections.
    memory_template = f"""{MANAGED_START_MARKER}
{MANAGED_TITLE}

{SESSION_START_MARKER}
## Current Session
<!-- Auto-managed by session_init hook. Overwritten each session. -->
{SESSION_END_MARKER}

{MEMORY_START_MARKER}
## Retrieved Context
{RETRIEVED_CONTEXT_COMMENT}

## Pinned Context

## Working Memory
{WORKING_MEMORY_COMMENT}
{MEMORY_END_MARKER}

{MANAGED_END_MARKER}
"""

    # Concurrency guard: serialize symlink check + write so two concurrent
    # session_init hooks on the same project cannot both see "new_default"
    # and race on the write. Fail-open on timeout — next session start retries.
    try:
        ensure_dot_claude_parent(target_file)
        with file_lock(target_file):
            # #1247: containment (in _atomic_write_text) REPLACES the former
            # leaf is_symlink guard -- it runs inside the lock (TOCTOU-safe)
            # and catches the symlinked-PARENT escape the leaf guard MISSED
            # (F1), via kernel-object ancestry on a pinned parent descriptor.
            # No resolver runs inside the guard -- do NOT reintroduce one, and
            # do not resolve the target and reuse it downstream: that makes the
            # WRITE follow the leaf. It does NOT dominate
            # is_symlink: it safely ALLOWS a benign in-project leaf redirect
            # (os.replace leaf-swap, no write-through) the old guard refused.
            if target_file.exists():
                return None
            try:
                _atomic_write_text(
                    target_file, memory_template, Path(project_dir)
                )
                return "Created project CLAUDE.md with memory sections"
            except ContainmentError:
                return "Project CLAUDE.md skipped: path precondition not met."
            except OSError as e:
                # `failed` IS THE ROUTING TOKEN (session_init step 3). The
                # prefix stays byte-identical. Only the cause token changed.
                return f"Project CLAUDE.md failed: {failure_cause(e)}"
    except TimeoutError:
        return (
            "Failed to acquire lock on project CLAUDE.md within 5s "
            "(another session_init hook may be running concurrently). "
            "Project CLAUDE.md creation skipped; will retry on next session start."
        )
    except OSError as e:
        # Lock-acquisition failure. Same routing token, same closed
        # vocabulary as the inner arm above.
        return f"Project CLAUDE.md failed: {failure_cause(e)}"


def migrate_to_managed_structure() -> str | None:
    """
    One-time migration: wrap existing project CLAUDE.md content in the
    PACT_MANAGED boundary and add PACT_MEMORY markers around memory sections.

    Called from session_init.py on every SessionStart. Idempotent no-op when
    the parser finds the PACT_MANAGED pair. Follows the same hardening
    pattern as the other managed-file writers: file_lock, symlink guard inside
    the lock, fail-open on timeout/error.

    Idempotency guard: with the PACT_MANAGED pair found, the function returns
    None without touching the file. A managed, session or memory pair the
    parser cannot place (a duplicate, a marker with no partner, an uncertain
    region) leaves the file unchanged and returns a "Migration skipped" status
    naming the line.

    Migration strategy (applied when the guard passes):
    1. Locate the existing sections by their markers/headings:
       - PACT_ROUTING block (between PACT_ROUTING_START/END)
       - SESSION block (between SESSION_START/END)
       - Memory sections: "## Retrieved Context", "## Pinned Context",
         "## Working Memory"
    2. Replace the legacy "# Project Memory" heading with the single canonical
       H1 "# PACT Framework and Managed Project Memory"
    3. Wrap memory sections in PACT_MEMORY_START/END (always emitting all
       three canonical H2 headings, even if some were absent in the source)
    4. Wrap the entire managed region in PACT_MANAGED_START/END; content
       outside the recognized PACT sections is preserved AFTER the closing
       boundary as user-owned content

    User content with fenced code blocks containing ## memory headings is
    preserved verbatim: every marker and heading is located by the
    fence-aware parser, so a fenced copy is never a real memory section.

    Returns:
        Status message on successful migration, None on no-op (already
        migrated or file doesn't exist), or a "failed"/"skipped" string
        on error (routed to systemMessages by session_init.py).
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        return None

    target_file, source = resolve_project_claude_md_path(project_dir)

    if source == "new_default":
        return None  # File doesn't exist; ensure_project_memory_md() handles creation

    try:
        with file_lock(target_file):
            # #1247: containment (in _atomic_write_text) REPLACES the former
            # leaf is_symlink guard -- inside the lock. It catches the
            # symlinked-PARENT escape the leaf guard MISSED (F1) and safely
            # ALLOWS a benign in-project leaf redirect; it does NOT dominate
            # is_symlink (the two catch overlapping-but-different sets).
            try:
                content = target_file.read_text(encoding="utf-8")
            except OSError:
                return None
            except UnicodeDecodeError:
                # This pass rewrites the file, so it is decoded strictly and
                # left untouched rather than written back with U+FFFD. The
                # planner runs on the replace-decoded copy: a file already
                # migrated had nothing to do, so no skip to report.
                new_content, refusal = _plan_migration(_read_replaced(target_file))
                if new_content is None and refusal is None:
                    return None
                return (
                    f"Migration skipped: {target_file} is not valid UTF-8, so "
                    "it was left unchanged."
                )

            new_content, refusal = _plan_migration(content)
            if refusal is not None:
                return (
                    f"Migration skipped: {target_file}: {refusal}. The file was "
                    "left unchanged."
                )
            if new_content is None:
                # Idempotent guard: already migrated
                return None

            try:
                _atomic_write_text(
                    target_file, new_content, Path(project_dir)
                )
                return "Migrated project CLAUDE.md to managed structure (#404)"
            except ContainmentError:
                return "Migration skipped: project CLAUDE.md path precondition not met."
            except OSError as e:
                # `failed` IS THE ROUTING TOKEN (session_init step 3b). The
                # prefix stays byte-identical. Only the cause token changed.
                return f"Migration failed: {failure_cause(e)}"
    except TimeoutError:
        return (
            "Failed to acquire lock on project CLAUDE.md within 5s "
            "(another session_init hook may be running concurrently). "
            "CLAUDE.md migration skipped; will retry on next session start."
        )
    except OSError:
        # #1245: lock ACQUISITION PermissionError escapes `except TimeoutError`;
        # catch it at the same skip-and-retry level (inner except handles the
        # post-acquisition write). Opaque, matching the sibling TimeoutError
        # message -- do not leak the sidecar path into a status string.
        return (
            "Could not acquire lock on project CLAUDE.md "
            "(path precondition not met); CLAUDE.md migration skipped."
        )


# The legacy template's title and description, stripped from the top of a
# file during migration, and a blank row between them.
_LEGACY_TITLE_RE = re.compile(r"# Project Memory\s*$")
_LEGACY_DESCRIPTION_RE = re.compile(
    r"This file contains project-specific memory managed by the PACT framework\.\s*$"
)
_BLANK_ROW_RE = re.compile(r"\s*$")
# The three memory headings, each at column 0 (the classifier's exact test).
_MEMORY_HEADING_RES = {
    heading: re.compile(re.escape(heading) + r"\s*$")
    for heading in ("## Retrieved Context", "## Pinned Context", "## Working Memory")
}
# Any `# ` or `## ` heading, indent-tolerant (see the boundary note in
# _plan_migration).
_SECTION_BOUNDARY_RE = re.compile(r"\s*#{1,2} +\S")


def _drop_spans(text: str, spans: list[tuple[int, int]]) -> str:
    """`text` without the given (start, end) spans; overlapping spans merge."""
    kept: list[str] = []
    pos = 0
    for start, end in sorted(spans):
        if start > pos:
            kept.append(text[pos:start])
        pos = max(pos, end)
    kept.append(text[pos:])
    return "".join(kept)


def _offset_after_drop(offset: int, spans: list[tuple[int, int]]) -> int:
    """Where `offset` in `text`, outside every span, stands in
    `_drop_spans(text, spans)`."""
    dropped = pos = 0
    for start, end in sorted(spans):
        if start >= offset:
            break
        dropped += max(pos, min(end, offset)) - max(pos, start)
        pos = max(pos, end)
    return offset - dropped


def _offset_before_drop(offset: int, spans: list[tuple[int, int]]) -> int:
    """Where `offset` in `_drop_spans(text, spans)` stands in `text`."""
    dropped = pos = 0
    for start, end in sorted(spans):
        if start > pos and offset < start - dropped:
            break
        dropped += max(pos, end) - max(pos, start)
        pos = max(pos, end)
    return offset + dropped


def _legacy_header_end(doc: Document) -> int:
    """Offset where the text starts after the legacy `# Project Memory` title,
    the blank rows below it, and the template's description row with the
    blank rows below that; 0 when the first row is not that title. Each
    stripped row must end with a line break, as the old pattern required."""
    lines = doc.lines
    if not lines or doc.text.startswith("\ufeff") or not doc.find_lines(_LEGACY_TITLE_RE, (0, 0)):
        return 0

    def ended(row: int) -> bool:
        return lines[row].end > lines[row].start + len(lines[row].content)

    if not ended(0):
        return 0
    row = 1
    while row < len(lines) and ended(row) and doc.find_lines(_BLANK_ROW_RE, (row, row)):
        row += 1
    if row < len(lines) and ended(row) and doc.find_lines(_LEGACY_DESCRIPTION_RE, (row, row)):
        row += 1
        while row < len(lines) and ended(row) and doc.find_lines(_BLANK_ROW_RE, (row, row)):
            row += 1
    return lines[row].start if row < len(lines) else len(doc.text)


# The PACT markers the migration carries as the user wrote them. The rebuilt
# file must read each the same way the original did, so a fenced or commented
# copy the rebuild made live is refused rather than written. The routing pair
# is read as a block, and each of its two literals on its own: its two marker
# lines share a prefix. The managed, memory and session markers are checked as
# blocks before these.
_ROUTING_START_PREFIX = "<!-- PACT_ROUTING_START"
_ROUTING_END_MARKER = "<!-- PACT_ROUTING_END -->"
_MIGRATION_CARRIED_MARKERS = (PINNED_START_MARKER, PINNED_END_MARKER, _KERNEL_START_MARKER, _KERNEL_END_MARKER)


def _split_bom(content: str) -> tuple[str, str]:
    """The leading U+FEFF of `content`, or "", and the text after it."""
    return ("\ufeff", content[1:]) if content.startswith("\ufeff") else ("", content)


def _trim_blank_edges(text: str) -> str:
    """`text` without its leading and trailing whitespace-only lines and without
    its final line ending. Every other byte is kept as written, the first kept
    line's indentation and the last kept line's trailing spaces included."""
    lines = text.split("\n")
    first, last = 0, len(lines)
    while first < last and not lines[first].strip():
        first += 1
    while last > first and not lines[last - 1].strip():
        last -= 1
    return "\n".join(lines[first:last])


def _readback_reason(name: str, located: Located) -> str:
    """Why the rebuilt file's `name` lookup failed, naming a line where one
    exists. A duplicate, malformed or uncertain result carries its own reason.
    A block found where the original had none names its first line in the
    rebuilt file, as the other reasons do."""
    if located.reason:
        return located.reason
    if located.spans:
        return f"it has a {name} at line {located.spans[0][0] + 1} that the original did not have"
    return f"its {name} is missing"


def _plan_migration(content: str) -> tuple[str | None, str | None]:
    """Plan the migration of `content` into the managed structure:
    (new_content, refusal).

    Both None: already migrated (the PACT_MANAGED pair is found). A refusal
    names why the file is left alone: a managed, session or memory pair the
    parser cannot place, a memory heading whose only copy is commented out, or
    a rebuilt file that does not read back the way it must (see the end of
    this function). Pure, so a file that is not valid UTF-8 gets the same plan
    from its replace-decoded copy.

    The user's text keeps every byte: only whitespace-only lines at its two
    edges are trimmed, and a leading byte-order mark stays at byte 0 of the
    rebuilt file, before PACT's marker.

    Extracts the PACT-managed sections (session, memory) from the existing
    content and reassembles them inside the new boundary markers. Any content
    that falls outside the recognized PACT sections is preserved AFTER the
    PACT_MANAGED_END marker as user-owned content. User content that appears
    ABOVE the first memory section heading lands BELOW PACT_MANAGED_END.

    Every marker and heading is located by the fence-aware parser. The session
    block is extracted first, so a block anywhere in the file (inside the
    memory block, before it, at the end) lands between the title and the
    memory block. A found memory block's two marker rows are dropped before
    the lines are classified: the rebuilt block writes its own pair.
    """
    from .claude_md_markers import Cause, State, parse

    bom, content = _split_bom(content)
    doc = parse(content)
    managed = doc.find_block(MANAGED_START_MARKER, MANAGED_END_MARKER)
    if managed.state is State.FOUND:
        return None, None
    if managed.state is not State.ABSENT:
        return None, managed.reason
    session = doc.find_block(SESSION_START_MARKER, SESSION_END_MARKER)
    memory = doc.find_block(MEMORY_START_MARKER, MEMORY_END_MARKER)
    for located in (session, memory):
        if located.state not in (State.FOUND, State.ABSENT):
            return None, located.reason
    # A memory heading whose only copy is commented out refuses, rather than
    # adopt or duplicate that section.
    for pattern in _MEMORY_HEADING_RES.values():
        section = doc.find_section(pattern, None)
        if section.cause is Cause.COMMENTED:
            return None, section.reason

    # Cut the session block (from its start marker to its end marker, as it
    # is placed) and the memory block's two marker rows out of the text.
    session_block = ""
    cuts: list[tuple[int, int]] = []
    if session.state is State.FOUND:
        first, last = session.spans[0]
        start, end = _marker_span(doc, first)[0], _marker_span(doc, last)[1]
        session_block = content[start:end]
        cuts.append((start, end))
    if memory.state is State.FOUND:
        for row in memory.spans[0]:
            cuts.append((doc.row_start(row), doc.lines[row].end))
    remaining = _drop_spans(content, cuts)

    # Remove the old top-level heading and description line
    header_end = _legacy_header_end(parse(remaining))
    remaining = remaining[header_end:]

    # Strip the stale template lines where the original file reads them as prose.
    legacy = _legacy_spans_after_cuts(doc, cuts, header_end)
    remaining = _drop_spans(remaining, legacy)

    # Classify the rows. A memory heading opens a memory section; any other
    # `# `/`## ` heading opens a user section. Headings are found only on prose
    # rows, so fenced, code and uncertain rows stay in the section they are in.
    #
    # INDENT-TOLERANT ON PURPOSE, AND THE EXACT MEMORY-HEADING TEST IS NOT.
    # THAT ASYMMETRY IS THE DESIGN AND NOT AN OVERSIGHT, SO DO NOT "FINISH"
    # IT BY RELAXING THE MEMORY-HEADING PATTERNS.
    #
    # THE CAUSE IS THE FAILURE DIRECTION. The boundary pattern makes an
    # indented heading a BOUNDARY, so the user text that follows it LEAVES the
    # managed region and the plugin does not own it. Make the memory-heading
    # test indent-tolerant as well and the plugin ADOPTS that text into a
    # section it rewrites and prunes, on a file that git does not track. A
    # boundary loses nothing. An adoption can lose the text.
    #
    # A heading inside a multi-row HTML comment is neither: the comment stays
    # whole in the section it sits in.
    rows = parse(remaining)
    heading_at: dict[int, str] = {}
    for heading, pattern in _MEMORY_HEADING_RES.items():
        heading_at.update(
            (row, heading) for row in rows.find_lines(pattern) if not rows.lines[row].in_html)
    boundaries = {
        row for row in rows.find_lines(_SECTION_BOUNDARY_RE) if not rows.lines[row].in_html}
    user_rows: list[Line] = []
    bodies: dict[str, list[list[Line]]] = {heading: [] for heading in _MEMORY_HEADING_RES}
    current: list[Line] = user_rows
    for line in rows.lines:
        if line.row in heading_at:
            current = []
            bodies[heading_at[line.row]].append(current)
            continue
        if line.row in boundaries:
            current = user_rows
        current.append(line)

    def text_of(lines: list[Line]) -> str:
        return "".join(remaining[line.start:line.end] for line in lines)

    # Each heading's sections, each without trailing blanks; empty ones dropped.
    memory_sections: dict[str, list[tuple[list[Line], str]]] = {heading: [] for heading in bodies}
    for heading, sections in bodies.items():
        for lines in sections:
            text = text_of(lines).rstrip()
            if text:
                memory_sections[heading].append((lines, text))
    user_all = text_of(user_rows)
    user_text = _trim_blank_edges(user_all)

    # Build the new structure — all content goes inside the managed block
    parts: list[str] = []
    # Each carried piece of the old file: its rows, where its text starts in
    # those rows' text, where it lands in the rebuilt file, and its length.
    carried: list[tuple[list[Line], int, int, int]] = []

    def carry(lines: list[Line], start: int, text: str) -> None:
        carried.append((lines, start, len(bom) + sum(map(len, parts)), len(text)))
        parts.append(text)

    parts.extend([MANAGED_START_MARKER, "\n", f"{MANAGED_TITLE}\n"])

    if session_block:
        parts.extend(["\n", session_block, "\n"])

    parts.extend(["\n", MEMORY_START_MARKER, "\n"])
    # THE COMMENT EACH HEADING CARRIES IN THE CREATION TEMPLATE, from the same
    # constants that template uses, so the two writers in this module cannot
    # emit different shapes again. `## Pinned Context` has no comment there and
    # gets none here.
    heading_comments = {
        "## Retrieved Context": RETRIEVED_CONTEXT_COMMENT,
        "## Working Memory": WORKING_MEMORY_COMMENT,
    }
    for index, heading in enumerate(("## Retrieved Context", "## Pinned Context", "## Working Memory")):
        pieces = memory_sections[heading]
        comment = heading_comments.get(heading)
        parts.append(f"\n{heading}\n" if index else f"{heading}\n")
        # ADD THE COMMENT ONLY WHEN THE SECTION ARRIVES WITHOUT ONE, and test
        # for it on EVERY ROW OF THE BODY rather than at the start.
        #
        # A PREFIX TEST GIVES A DUPLICATE ON THREE SHAPES A DOCUMENT REALLY
        # HAS: the comment after a blank line, the comment indented, and a
        # different comment first. Each one reads as absent to `startswith`,
        # so each one gains a second copy.
        #
        # A row that holds only the comment counts. A quote of it inside a
        # fence, in inline code or mid-line does not, so that body gains the
        # comment.
        if comment and not any(
                rows.find_marker(comment, (section_line.row, section_line.row)).state is State.FOUND
                for section_lines, _ in pieces for section_line in section_lines):
            parts.append(f"{comment}\n")
        for number, (lines, text) in enumerate(pieces):
            if number:
                parts.append("\n")
            carry(lines, 0, text)
        if pieces:
            parts.append("\n")
    parts.extend([MEMORY_END_MARKER, "\n"])

    parts.extend(["\n", MANAGED_END_MARKER, "\n"])

    if user_text:
        parts.append("\n")
        # The trim drops only whole whitespace-only lines, so the user's text
        # starts on the line of its first visible character.
        first_visible = len(user_all) - len(user_all.lstrip())
        carry(user_rows, user_all.rfind("\n", 0, first_visible) + 1, user_text)
        parts.append("\n")

    new_content = bom + "".join(parts)
    # The rebuilt file must read back as one managed block, one memory block,
    # the session block exactly when one was extracted, no uncertain region the
    # original did not have, and every other PACT marker as the original read
    # it. A rebuild that fails any of these changed the user's text.
    written = parse(new_content)
    session_after = State.FOUND if session_block else State.ABSENT
    for name, (start_marker, end_marker), wanted in (
        ("managed block", (MANAGED_START_MARKER, MANAGED_END_MARKER), State.FOUND),
        ("memory block", (MEMORY_START_MARKER, MEMORY_END_MARKER), State.FOUND),
        ("Current Session block", (SESSION_START_MARKER, SESSION_END_MARKER), session_after),
    ):
        located = written.find_block(start_marker, end_marker)
        if located.state is not wanted:
            return None, (
                "the migrated file did not read back as one managed block, one "
                "memory block and the Current Session block it had: "
                + _readback_reason(name, located)
            )
    if doc.boundary is None and written.boundary is not None:
        return None, (
            "the migrated file would leave a region PACT cannot read, which the "
            "original did not have"
        )
    # State, cause AND the number of marker lines or blocks found. A line that
    # held a marker as stray text and now holds it as a marker line reads
    # MALFORMED both times, for a different cause. A marker that already reads
    # DUPLICATE still does with one more live copy, on one more line.
    def reading(located):
        return located.state, located.cause, len(located.spans)

    routing = (_ROUTING_START_PREFIX, _ROUTING_END_MARKER)
    if reading(written.find_block(*routing)) != reading(doc.find_block(*routing)):
        return None, "the migrated file would change how the routing block reads"
    # Each routing literal on its own as well: an unpaired or nested block
    # carries no lines, so only these count a copy of one of its markers.
    for literal in (*_MIGRATION_CARRIED_MARKERS, *routing):
        if reading(written.find_marker(literal)) != reading(doc.find_marker(literal)):
            return None, f"the migrated file would change how {literal!r} reads"
    # Every carried row reads as it did in the original file: the same kind,
    # and hidden in an HTML block or not. A memory section moved above the
    # user's text can end inside an HTML block that is never closed, and turn
    # the user's fenced code into prose with no marker involved. The original
    # file is the reference, not the text the sections were cut from: cutting
    # out a session block that opens an HTML block uncovers what it covered.
    def origin(offset: int) -> int:
        return _offset_before_drop(_offset_before_drop(offset, legacy) + header_end, cuts)

    original_at = {line.start: line for line in doc.lines}
    written_at = {line.start: line for line in written.lines}
    for source_rows, start, out_start, length in carried:
        offset = 0
        for source in source_rows:
            before = original_at.get(origin(source.start))
            # The row the session cut leaves behind is skipped when it starts
            # mid-row in the original; the rows around it show any change.
            if start <= offset < start + length and before is not None:
                at = out_start + offset - start
                after = written_at.get(at)
                if after is None or (after.kind, after.in_html) != (before.kind, before.in_html):
                    number = sum(1 for other in written.lines if other.start <= at)
                    return None, f"the migrated file would change how line {number} reads"
            offset += source.end - source.start
    return new_content, None


def match_project_claude_md(file_path_str: str) -> Path | None:
    """Match a tool-input file_path against the canonical project CLAUDE.md.

    Returns the canonical resolved path if `file_path_str` points at the
    project's CLAUDE.md (either `.claude/CLAUDE.md` or the legacy
    `./CLAUDE.md`), otherwise None. Intended for PreToolUse gates that
    need to short-circuit on non-CLAUDE.md targets.

    Relative `file_path_str` values are anchored against
    CLAUDE_PROJECT_DIR (Back-M3/Sec-F4): `Path.resolve()` on a relative
    path uses cwd, and a hook's cwd can drift (worktree switches,
    subprocess invocations). The env var is the stable anchor the plugin
    sets on every session. If CLAUDE_PROJECT_DIR is unset, relative
    input returns None — safer than a silent cwd dependency.

    Worktree-safe: imports `staleness.get_project_claude_md_path` lazily
    to avoid circular-import and module-load cost on every Edit/Write.
    That function already handles env-var / git-root / cwd fallbacks.

    Fail-safe: any OSError / RuntimeError while resolving returns None.
    Callers treat None as "not our target; let the tool through."
    """
    if not file_path_str:
        return None

    try:
        from staleness import get_project_claude_md_path
    except ImportError:
        return None

    project_md = get_project_claude_md_path()
    if project_md is None:
        return None

    try:
        target_path = Path(file_path_str)
        if not target_path.is_absolute():
            project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
            if not project_dir:
                return None
            target_path = Path(project_dir) / target_path
        target = target_path.resolve()
        canonical = project_md.resolve()
    except (OSError, RuntimeError):
        return None

    if target != canonical:
        return None
    return canonical


class GateTarget(NamedTuple):
    """The project CLAUDE.md an Edit or Write lands on (`target`), the file the
    resolver returns before the change (`before`, None when none resolves),
    and the base directory the target resolved under."""

    target: Path
    before: Path | None
    base: Path


def gate_target(file_path: str) -> GateTarget | None:
    """The project CLAUDE.md the pin-cap gate checks for this Edit or Write
    `file_path`, or None when it checks none.

    Gated: an absolute path whose name is `claude.md` in any case, which the
    project resolver returns once the change exists. The resolver runs with
    the target counted as present, so a Write that creates the file is gated,
    and a Write to a file the resolver would not return (`./CLAUDE.md` while
    `.claude/CLAUDE.md` exists) is not. `before` is what the resolver returns
    now, which can be a different file from the target.

    A relative path is not gated, and any failure while resolving returns
    None: a path the gate is unsure of is allowed.
    """
    if not isinstance(file_path, str) or not file_path:
        return None
    target = Path(file_path)
    if not target.is_absolute() or target.name.casefold() != "claude.md":
        return None
    try:
        from staleness import _resolve_project_claude_md_with_base, same_path
    except ImportError:
        return None
    try:
        resolved, base = _resolve_project_claude_md_with_base(assume_present=target)
        if resolved is None or base is None or not same_path(resolved, target):
            return None
        before = _resolve_project_claude_md_with_base()[0]
    except (OSError, RuntimeError, ValueError):
        return None
    return GateTarget(target, before, base)


# What a team member is told when a change would add pins past the cap, in
# place of the pin command it cannot run. The gate's count denial and the drift
# report share this one copy.
MEMBER_PIN_INSTRUCTION = "Do not change CLAUDE.md yourself, by any route; tell the team-lead."


def gate_frame(input_data) -> str | None:
    """Which kind of hook frame the pin-cap checks cover: "lead", "member" (a
    frame whose session belongs to a PACT team: in-process teammates and
    Agent-tool subagents share the lead's session), "specialist" (a PACT
    specialist type in no team, such as a solo --agent session), or None for a
    frame they do not cover (a plain session, a non-PACT --agent session).

    The pin-cap gate and the CLAUDE.md drift checks share this one predicate.
    Never raises: a failed role read means None, and a failed team read means
    "not in a team". Both reads are imported here, not at module level, because
    every hook loads this module.
    """
    try:
        from .pact_context import is_lead, strip_pact_namespace

        if is_lead(input_data):
            return "lead"
        agent_type = input_data.get("agent_type")
        specialist = isinstance(agent_type, str) and strip_pact_namespace(agent_type).startswith("pact-")
    except Exception:  # noqa: BLE001 — an unreadable frame is not covered
        return None
    try:
        from .background_work import frame_team_and_name

        team = frame_team_and_name(input_data)[0]
    except Exception:  # noqa: BLE001 — a failed membership read covers nothing
        team = ""
    if team:
        return "member"
    return "specialist" if specialist else None
