"""
Staleness Detection Module

Location: pact-plugin/hooks/staleness.py

Summary: Detects stale pinned context entries in the project CLAUDE.md and
checks whether pinned content exceeds its token budget. Stale entries are
marked with HTML comments so they can be identified for cleanup.

Used by:
- session_init.py: Calls check_pinned_staleness() during SessionStart hook
- Test files: test_staleness.py tests all functions in this module

Extracted from session_init.py to keep that file focused on hook orchestration
and under the 500-line maintainability limit.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from shared.claude_md_manager import (
    MANAGED_END_MARKER,
    MANAGED_START_MARKER,
    MEMORY_END_MARKER,
    MEMORY_START_MARKER,
    PACT_BOUNDARY_PREFIXES,
    PINNED_END_MARKER,
    PINNED_START_MARKER,
    SESSION_BOUNDARY_PREFIX,
)
from shared.failure_cause import failure_cause
from shared.project_scope import git_env_without_location
from pin_caps import (
    PIN_STALE_BLOCK_THRESHOLD,
    _PIN_HEADING_ROW,
    CapViolation,
    check_stale_block,
    section_pins,
)

# THE ONE PINNED LOCATOR'S PATTERNS (`locate_pinned`). The heading and the
# terminator match ONE row's content at column 0, through the fence-aware
# parser, so a fenced or indented `## Pinned Context` is not the heading and a
# fenced `## ` line does not end the section. The stop prefixes end the section
# at any PACT boundary marker line, indented up to 3 spaces: the memory and
# managed markers, the pinned pair's end marker, the routing block and the
# session block.
_PINNED_HEADING = re.compile(r"## Pinned Context\s*$")
_PINNED_TERMINATOR = re.compile(r"#{1,2}\s")
_PINNED_STOP_PREFIXES = tuple(
    f"<!-- {prefix}" for prefix in (*PACT_BOUNDARY_PREFIXES, SESSION_BOUNDARY_PREFIX)
)

# A STALE marker this module writes, anywhere on a row, which is where the
# already-marked test has always looked for one.
_STALE_MARK_ROW = re.compile(r".*?<!-- STALE: Last relevant \d{4}-\d{2}-\d{2} -->")


# Staleness detection constants

# Number of days after which a pinned entry referencing a merged PR is
# considered stale and gets an HTML comment marker.
PINNED_STALENESS_DAYS = 30

# Approximate token budget for the entire Pinned Context section. When
# exceeded, a warning comment is added. No pin is ever deleted.
# This is the sole definition of this constant; session_init.py imports it.
#
# SIZED FROM CAPACITY, NOT FROM A MEASUREMENT OF THE CURRENT DOCUMENT. A bound
# derived from the region it bounds cannot see that region's next edit, and it
# lands so close to the present size that an ordinary pin edit re-trips the
# warning -- which reports "you touched a pin", not "your pins have bloated".
# This value leaves headroom above a full set of well-written pins.
#
# WHY THIS IS A FREE NUMBER RATHER THAN A VALUE DERIVED FROM THE CAPS IN
# pin_caps.py. A derived advisory cannot fire before enforcement binds. If the
# budget were f(caps) with f >= 1, then reaching it would require roughly full
# legal capacity -- and at that point PIN_SIZE_CAP and PIN_COUNT_CAP are
# already refusing edits, so the advice arrives at the wall, too late to act
# on. Advising EARLIER requires a coefficient below 1. Derivation therefore
# does not eliminate the free number; it relocates it into a coefficient. This
# constant IS that coefficient, stated directly instead of hidden in a formula.
#
# The two also bound DIFFERENT things, which is why one cannot be read off the
# other: PIN_SIZE_CAP counts body characters of a single pin, while this counts
# estimated tokens of the whole section, headings and markers included.
#
# AND A THIRD REASON, ABOUT WHO DECIDES. A derived budget would MOVE whenever
# the caps move. A raise of PIN_SIZE_CAP is an enforcement decision about a
# single pin, and it would then relocate an ADVISORY threshold over the whole
# section, with nobody deciding that. The relation between the two is guarded in
# the test suite instead, by a band that REFUSES a budget outside it. A guard
# makes a cap change LOUD and asks for a decision. A derivation makes the same
# change SILENT.
PINNED_CONTEXT_TOKEN_BUDGET = 3200

# The exact text this module writes at the head of an over-budget pinned
# section. It is BOTH the text of the warning and the probe that finds an
# earlier one, so the writer and the reader cannot describe different things.
_BUDGET_WARNING_PREFIX = "<!-- WARNING: Pinned context"

# THE SHAPE OF A WARNING LINE, WITHOUT AN ANCHOR. This is a regex SOURCE
# STRING and not a compiled pattern, on purpose: the two row patterns below are
# the only predicates that exist.
#
# THE SHAPE CARRIES ITS OWN BOUNDS. `[^\n]*?` cannot cross a newline, and it is
# LAZY so it stops at the FIRST `-->`. An HTML comment ends at its first `-->`;
# a greedy run to the LAST one on the line would swallow whatever a user
# appended after the comment had already closed.
#
# THE `~N tokens (budget: M)` SHAPE IS LOAD-BEARING, NOT DECORATION. It is what
# separates a line this module emitted from a line that merely opens with the
# same words, and requiring it is what keeps the strip off a user's own prose.
#
# THIS SHAPE AND THE EMITTED FORMAT IN `_budget_warning_line` ARE A MATCHED
# PAIR. Change one and change the other in the SAME commit: a format this shape
# cannot match is a warning that can never be refreshed or removed, and every
# later pass stacks another warning above it.
_BUDGET_WARNING_HEAD = rf"{re.escape(_BUDGET_WARNING_PREFIX)} ~\d+ tokens \(budget: \d+\)"
_BUDGET_WARNING_SHAPE = rf"{_BUDGET_WARNING_HEAD}[^\n]*?-->\n?"

# RECOGNITION AND MEASUREMENT ONLY: a PROSE row that STARTS with a warning,
# matched through the parser's `find_lines`. It lets `_has_budget_warning` see
# a warning wherever it sits and `apply_staleness_markings` leave each one out
# of the MEASUREMENT COPY it builds. A warning-shaped line inside a fenced block is the user's
# text and is neither. DO NOT GIVE THIS PATTERN TO CODE THAT DELETES FROM THE
# DOCUMENT: it matches a row that carries text after the comment closes.
_BUDGET_WARNING_ROW = re.compile(_BUDGET_WARNING_SHAPE)

# DELETION: a PROSE row that IS one warning and nothing more, its comment
# closing at the row's end. `_strip_budget_warnings` removes only such rows,
# and only the contiguous run that starts the body, the one position this
# module ever writes a warning to. STRICT ON PURPOSE: this deletes bytes from a
# user's CLAUDE.md, a file that is frequently gitignored, so an over-match has
# no commit to recover from. A row with text after the warning closes is not
# one, so that text is never deleted.
#
# THE SYMPTOM THAT WILL MAKE SOMEBODY WANT TO LOOSEN THE STRIP, AND WHY TO
# REFUSE. A warning line that is not at the head of the body keeps its place,
# because the strip takes only the leading run. The report arrives as "the hook
# shows two warnings". That one is real and it is accepted. The law is
# CONDITIONAL, not a constant:
#     count = N + (1 if estimate_tokens(user_text) > BUDGET else 0)
# where N is the number of warning rows the leading-run strip cannot reach. No
# pass raises the count. A pin written ABOVE an existing warning takes that
# warning off the head and raises N with no user action at all, and
# `commands/pin-memory.md` instructs the tail placement rather than enforcing
# it.
#
# DO NOT WIDEN THE STRIP TO REACH THEM. It DELETES, so a wider reach removes
# text a user wrote inside a pin body. The repair separates the two questions:
# EXCLUDE warning rows from the COUNT wherever they sit, and keep the DELETE on
# the leading run. `apply_staleness_markings` builds the body it writes and the
# copy it measures from the same rows, and leaves warning rows out of the
# MEASUREMENT COPY only. Never leave them out of the body it writes back: that
# DELETES a stranded line from the user's file.
#
# THE POSITION RULE AND THE WHOLE-ROW RULE ARE ENFORCED, NOT ONLY STATED: see
# `test_the_strip_cannot_reach_below_the_head` and
# `test_text_after_a_warning_on_its_row_is_never_deleted`.
_BUDGET_WARNING_WHOLE_ROW =re.compile(rf"{_BUDGET_WARNING_HEAD}(?:(?!-->).)*-->\Z")


def _existing_anchor(path: Path) -> Tuple[Path, Tuple[str, ...]]:
    """The deepest ancestor of `path` (itself included) that exists, and the
    names below it, in order."""
    rest: List[str] = []
    while not path.exists() and path.parent != path:
        rest.append(path.name)
        path = path.parent
    return path, tuple(reversed(rest))


def _case_insensitive(directory: Path) -> bool:
    """Whether names are matched without case where `directory` lives: its
    case-swapped spelling names the same directory. A name with no cased
    letters cannot be probed and reads as case-sensitive."""
    swapped = directory.name.swapcase()
    if swapped == directory.name:
        return False
    try:
        return os.path.samefile(directory, directory.with_name(swapped))
    except (OSError, ValueError):
        return False


def same_path(a: Path, b: Path) -> bool:
    """Whether `a` and `b` name the same file, created or not.

    When both exist, `os.path.samefile`. Otherwise the deepest existing
    ancestor of each must be the same directory, and the names below it must
    be equal, or equal case-folded where that directory's volume matches names
    without case. Never raises: an error reads as different paths.
    """
    try:
        if a.exists() and b.exists():
            return os.path.samefile(a, b)
        anchor_a, rest_a = _existing_anchor(a)
        anchor_b, rest_b = _existing_anchor(b)
        if not os.path.samefile(anchor_a, anchor_b):
            return False
        if rest_a == rest_b:
            return True
        folded = [name.casefold() for name in rest_a] == [name.casefold() for name in rest_b]
        return folded and _case_insensitive(anchor_a)
    except (OSError, ValueError):
        return False


def _find_existing_claude_md(base: Path, assume_present: Optional[Path] = None) -> Optional[Path]:
    """
    Look for an existing project CLAUDE.md under `base`, honoring both
    supported locations: `.claude/CLAUDE.md` (preferred) then `CLAUDE.md`
    (legacy). Returns the first match or None.

    `assume_present` is counted as an existing file: a candidate that does not
    exist matches when it is `same_path` to it, so a caller can ask which file
    would resolve once a Write creates it.
    """
    for candidate in (base / ".claude" / "CLAUDE.md", base / "CLAUDE.md"):
        if candidate.exists():
            return candidate
        if assume_present is not None and same_path(candidate, assume_present):
            return candidate
    return None


def _resolve_project_claude_md_with_base(
    assume_present: Optional[Path] = None,
) -> Tuple[Optional[Path], Optional[Path]]:
    """
    Resolve the project-level CLAUDE.md AND the trusted base directory it was
    found under, so a write caller can containment-check the target against the
    base the resolver actually used (#1247).

    Honors both supported locations:
      - $base/.claude/CLAUDE.md  (preferred / new default)
      - $base/CLAUDE.md          (legacy)

    Resolution order for $base:
      1. CLAUDE_PROJECT_DIR env var
      2. Git common-dir parent (worktree-safe; --show-toplevel would return
         the worktree path, which often does not contain CLAUDE.md)
      3. Current working directory

    The returned `base` is the branch's directory captured BEFORE descending
    into `.claude` (the arg to `_find_existing_claude_md`), NOT the returned
    path and NOT a re-derivation -- the trusted pre-resolve anchor that makes
    the #1247 containment check non-vacuous. `get_project_claude_md_path` is
    now a thin wrapper returning `[0]`, so read-only callers and the
    resolver-parity lint are unaffected.

    `assume_present` is counted as an existing file at its place in that
    order (see `_find_existing_claude_md`). Without it, every caller resolves
    exactly as before.

    Returns:
        (path, base) where path is an existing project CLAUDE.md and base is
        the directory it was found under; (None, None) if none exists.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        base = Path(project_dir)
        found = _find_existing_claude_md(base, assume_present)
        if found is not None:
            return found, base

    # Fallback: detect git root (worktree-safe)
    # Uses --git-common-dir instead of --show-toplevel because the latter
    # returns the worktree path when run inside a worktree, which may not
    # contain CLAUDE.md. --git-common-dir always points to the shared .git
    # directory; its parent is the main repo root where CLAUDE.md lives.
    # git returns this path relative to the invoking directory when run at a
    # repo root (the bare ".git") and absolute elsewhere, so resolve a relative
    # result against the cwd before taking its parent.
    # NOTE: Twin pattern in skills/pact-memory/scripts/memory_api.py
    #       (_detect_project_id) and working_memory.py (_get_claude_md_path)
    #       -- keep in sync.
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
            repo_root = common_dir.resolve().parent
            found = _find_existing_claude_md(repo_root, assume_present)
            if found is not None:
                return found, repo_root
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # Last resort: current working directory
    cwd = Path.cwd()
    found = _find_existing_claude_md(cwd, assume_present)
    return (found, cwd) if found is not None else (None, None)


def _lexical_base_of(claude_md_path: Path) -> Path:
    """Recover the pre-`.claude` base of a resolver-produced CLAUDE.md path by
    INVERTING the resolver's construction (base/.claude/CLAUDE.md | base/
    CLAUDE.md) -- #1247 option D, for a caller-SUPPLIED path with no separate
    base (session_init's production flow passes the path, not the base).

    Purely lexical (pathlib `.parent` never follows symlinks), so an F1
    symlinked-parent `.claude` still escapes on the target's resolve() and is
    refused. Locked to the resolver's own base by TestStalenessLexicalBaseParity
    -- if _resolve_project_claude_md_with_base ever grows a third path shape,
    that test turns this formula's divergence into a RED.

    Edge (adversarial-only UNDER-block, tolerated per the good-faith model): a
    project dir literally named ".claude" using the LEGACY layout lands one
    level too high. Requires deliberate construction; not a bug.
    """
    if claude_md_path.parent.name == ".claude":
        return claude_md_path.parent.parent
    return claude_md_path.parent


def get_project_claude_md_path() -> Optional[Path]:
    """
    Get the path to the project-level CLAUDE.md (path only).

    Thin wrapper over `_resolve_project_claude_md_with_base` (added for #1247);
    read-only callers, session_init, and the resolver-parity lint use this
    Path-only name, while the write caller (check_pinned_staleness) uses the
    with-base variant to get the containment anchor.

    Returns:
        Path to an existing project CLAUDE.md if found, None otherwise.
    """
    return _resolve_project_claude_md_with_base()[0]


# Backward-compatible alias (tests and session_init patch the underscore name)
_get_project_claude_md_path = get_project_claude_md_path


def estimate_tokens(text: str) -> int:
    """
    Estimate token count using word count * 1.3 approximation.

    NOTE: Twin copy exists in working_memory.py (_estimate_tokens) -- keep in sync.

    Args:
        text: The text to estimate tokens for.

    Returns:
        Estimated token count.
    """
    if not text:
        return 0
    return int(len(text.split()) * 1.3)


# Backward-compatible alias (tests and session_init import the underscore name)
_estimate_tokens = estimate_tokens


def _strip_budget_warnings(doc, first: int, last: int) -> int:
    """
    The first row after the run of budget-warning rows at the head of the
    pinned body, rows `first`..`last` of `doc`; `first` when there is none.

    The rows from there on are the body a user would have written, with this
    module's own earlier reports taken back out. THIS IS THE DELETING HALF and
    it takes only the leading run: a warning row that is not at the head
    SURVIVES this strip and keeps its place in the document. The note at
    `_BUDGET_WARNING_WHOLE_ROW` says why the repair for that is not a wider
    strip.

    IT IS NOT THE MEASURING HALF, AND THE TWO ARE SEPARATE.
    `apply_staleness_markings` leaves the surviving warning rows out of the
    copy it measures, so a line this strip cannot reach contributes no token.
    Do not read that as a reason to widen this one. The count and the delete
    answer different questions, and only this one removes bytes a user can lose.

    A run, not a single line, because taking back N lines is the exact inverse
    of writing one -- so the function stays correct if a document somehow
    carries more than one, and it can never leave a partial residue behind.
    """
    run = first
    for row in doc.find_lines(_BUDGET_WARNING_WHOLE_ROW, (first, last)):
        if row != run:
            break
        run += 1
    return run


def _has_budget_warning(doc, first: int, last: int) -> bool:
    """
    Report whether this module has already written a warning anywhere in the
    pinned body, rows `first`..`last` of `doc`.

    RECOGNITION, NOT DELETION, AND THAT IS WHY THE REACH DIFFERS. This
    predicate and `_strip_budget_warnings` share ONE shape and differ in
    position: the strip takes back the leading run, this reports a warning row
    ANYWHERE. The shape is what identifies a line as this module's own, so the
    wider reach does not widen what counts as a warning.

    THE ACCEPTED CONSEQUENCE, RULED ON AND NOT OVERLOOKED. A user can write a
    complete warning line into their own pinned prose: a maintainer who pastes
    the emitted format into a note is the realistic case. In a section with NO
    entries, that body now enters the pass. If it ALSO exceeds the budget, this
    module adds ONE current warning above it. BOTH conditions are required. A
    quoted line in a body below the budget changes nothing at all. A section
    WITH entries has always behaved this way, and the suite pins it: see
    `test_user_line_quoting_the_warning_is_preserved`.
    """
    return bool(doc.find_lines(_BUDGET_WARNING_ROW, (first, last)))


def locate_pinned(doc, *, unique: bool = False):
    """
    THE ONE PINNED LOCATOR: the `## Pinned Context` section of a parsed
    CLAUDE.md, as a `Located` whose FOUND span is (heading row, last body row).
    Every reader of the Pinned section and the pin-growth rule call this.

    1. The memory block. FOUND: the search runs inside it. ABSENT: a reader
       (`unique=False`) keeps the window it always had, the managed block when
       that is FOUND and otherwise the whole document; the cap decision
       (`unique=True`) gets the ABSENT result back, because it cannot tell
       PACT's section from a user's without the block. Any other state comes
       back as it is: a reader stays silent, the gate allows with its
       advisory.
    2. The optional pinned marker pair inside that scope. FOUND: the search
       narrows to the pair's interior (the start marker sits above the heading).
       ABSENT: the scope stands. Any other state comes back.
    3. The section: its first visible heading through the row before the first
       terminator row (`#` or `##` heading) or PACT boundary marker line. A
       heading only inside an HTML comment is UNKNOWN, never ABSENT. With
       `unique`, two visible headings are DUPLICATE.

    Args:
        doc: A `shared.claude_md_markers.Document`.
        unique: True only where the pin count decides the cap.

    Returns:
        The `Located` of the section, or of the block that stopped the search.
    """
    from shared.claude_md_markers import State

    memory = doc.find_block(MEMORY_START_MARKER, MEMORY_END_MARKER)
    if memory.state is State.FOUND:
        scope = _interior(memory)
    elif memory.state is State.ABSENT and not unique:
        managed = doc.find_block(MANAGED_START_MARKER, MANAGED_END_MARKER)
        scope = _interior(managed) if managed.state is State.FOUND else None
    else:
        return memory
    pair = doc.find_block(PINNED_START_MARKER, PINNED_END_MARKER, scope)
    if pair.state is State.FOUND:
        scope = _interior(pair)
    elif pair.state is not State.ABSENT:
        return pair
    return doc.find_section(_PINNED_HEADING, _PINNED_TERMINATOR, scope,
                            stop_prefixes=_PINNED_STOP_PREFIXES, unique=unique)


def _interior(located) -> Tuple[int, int]:
    """The rows strictly inside a FOUND block, as a scope (possibly empty)."""
    first, last = located.spans[0]
    return first + 1, last - 1


def _parse_pinned_section(
    content: str, *, allow_empty_section: bool = False
) -> Optional[Tuple[int, int, str]]:
    """
    Extract the Pinned Context section body from CLAUDE.md content.

    The offset view of `_pinned_body`, on the whole-file parse of `content`.
    Returns positions in the FULL file content so callers can use them
    directly for read-mutate-write on the file.

    AN EMPTY SECTION IS AN INSTRUMENT LIMIT, NOT AN ABSENT ONE, AND
    `allow_empty_section` IS THE OPT-IN THAT SAYS SO. A heading with a body of
    whitespace has a computable span, and the default returns None for it
    anyway, so a caller cannot tell "no section" from "an empty section". A
    gate that compares two documents needs that difference: falling back to a
    wider slice on the empty side counts memory entries as pins and denies a
    faithful edit. The default preserves the decline every other caller was
    written against, and the parameter is keyword-only so a positional
    argument cannot opt in by accident.

    Args:
        content: Full CLAUDE.md file content.
        allow_empty_section: When True, a FOUND section whose body is empty
            or whitespace returns its span with an empty body instead of None.

    Returns:
        Tuple of (pinned_start, pinned_end, pinned_content), or None when the
        section is not FOUND (absent, uncertain, or inside a malformed or
        duplicated block), or it is empty and `allow_empty_section` is False.
        Offsets are absolute positions in the original `content` string.
    """
    from shared.claude_md_markers import State, parse

    doc = parse(content)
    located = locate_pinned(doc)
    if located.state is not State.FOUND:
        return None
    body = _pinned_body(doc, located)
    if body is None:
        if not allow_empty_section:
            return None
        pinned_end = doc.lines[located.spans[0][1]].end
        return pinned_end, pinned_end, ""
    first, last = body
    pinned_start, pinned_end = doc.lines[first].start, doc.lines[last].end
    return pinned_start, pinned_end, content[pinned_start:pinned_end]


def _pinned_body(doc, located) -> Optional[Tuple[int, int]]:
    """
    The rows of a FOUND Pinned section's body in `doc`, or None when the body
    is only blank rows.

    THE BODY STARTS AT THE FIRST NON-BLANK ROW AFTER THE HEADING, not at the
    row after it. Blank rows between the heading and the first pin stay outside
    the body, as they always have, because `_strip_budget_warnings` takes back
    only a warning at the head of the body and `apply_staleness_markings`
    writes a new one there. The body ends with the section's last row,
    terminator included.

    Every reader of the body reads these rows of the whole-file parse; none
    parses the body's text on its own, because a parse that starts mid-file
    starts in a state the whole file does not have.
    """
    heading, last = located.spans[0]
    first = next((row for row in range(heading + 1, last + 1) if doc.lines[row].content.strip()), None)
    return None if first is None else (first, last)


def detect_stale_entries(doc, first: int, last: int) -> List[Tuple[int, str, str]]:
    """
    Detect stale pinned context entries without modifying them.

    A pinned entry is stale if it contains a date (in a merged-PR reference
    or as a standalone YYYY-MM-DD) older than PINNED_STALENESS_DAYS, and
    has not already been marked with a STALE comment.

    An entry starts at a PROSE row beginning `### `, read with the fence-aware
    parser, so a `### ` line inside a fenced block is part of the entry above.

    Args:
        doc: The whole-file parse.
        first, last: The rows of the Pinned body.

    Returns:
        List of (entry_index, date_string, entry_heading) tuples for each
        stale entry found. entry_index is the entry's position in the section.
    """
    headings = doc.find_lines(_PIN_HEADING_ROW, (first, last))
    if not headings:
        return []

    now = datetime.now(timezone.utc)
    stale_threshold = now - timedelta(days=PINNED_STALENESS_DAYS)

    # Pattern to match "PR #NNN, merged YYYY-MM-DD" in entry text
    pr_merged_pattern = re.compile(
        r'PR\s*#\d+,?\s*merged\s+(\d{4}-\d{2}-\d{2})'
    )
    # Fallback: any standalone YYYY-MM-DD date in the entry header line
    standalone_date_pattern = re.compile(r'(\d{4}-\d{2}-\d{2})')

    stale_entries: List[Tuple[int, str, str]] = []

    for i, (entry_first, entry_last) in enumerate(_entry_rows(headings, last)):
        # Skip entries already marked stale
        if doc.find_lines(_STALE_MARK_ROW, (entry_first, entry_last)):
            continue

        entry_text = doc.text[doc.lines[entry_first].start:doc.lines[entry_last].end]
        heading = doc.lines[entry_first].content

        # Look for PR merged date first (most specific)
        date_str = None
        pr_match = pr_merged_pattern.search(entry_text)
        if pr_match:
            date_str = pr_match.group(1)
        else:
            # Fallback: find any YYYY-MM-DD date in the heading line
            date_match = standalone_date_pattern.search(heading)
            if date_match:
                date_str = date_match.group(1)

        if not date_str:
            continue

        try:
            entry_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        if entry_date < stale_threshold:
            stale_entries.append((i, date_str, heading))

    return stale_entries


def _entry_rows(headings, last: int) -> List[Tuple[int, int]]:
    """(heading row, last row) of each entry: to the row before the next
    heading, the last entry to `last`, the body's last row."""
    ends = [row - 1 for row in headings[1:]] + [last]
    return list(zip(headings, ends))


def _stale_marker_line(date_str: str) -> str:
    """The STALE marker row this module inserts below a stale entry's heading."""
    return f"<!-- STALE: Last relevant {date_str} -->\n"


def _budget_warning_line(pinned_tokens: int) -> str:
    """The warning row written at the head of an over-budget pinned body.

    THIS FORMAT AND `_BUDGET_WARNING_SHAPE` ARE A MATCHED PAIR: change one and
    change the other in the SAME commit.
    """
    return (
        f"{_BUDGET_WARNING_PREFIX} ~{pinned_tokens} tokens "
        f"(budget: {PINNED_CONTEXT_TOKEN_BUDGET}). "
        f"Consider archiving stale pins. -->\n"
    )


def apply_staleness_markings(
    content: str,
    doc,
    first: int,
    last: int,
) -> Tuple[str, int, bool, str]:
    """
    Apply stale markers and budget warnings to pinned content.

    Detects stale entries, inserts STALE markers, and rewrites the budget
    warning comment to match the CURRENT content. Returns the modified full
    file content.

    THE WARNING IS REBUILT FROM THE BODY ON EVERY PASS, NEVER PATCHED IN PLACE.
    An earlier warning is removed first, the warning-free body is measured, and
    a fresh line goes back only when the measurement still exceeds the budget.

    Three properties follow from that order, and they are the whole reason for
    it:

      - THE NUMBER CANNOT GO STALE. It is recomputed against whatever the body
        holds now, so it tracks a growing or shrinking pinned section.
      - THE WARNING CANNOT INFLATE ITS OWN COUNT. The measured body contains NO
        line of this module's own shape, on pass 1 or pass 500, so the number
        does not creep upward as the report of it is re-read. The head run is
        taken back from the document, and the rest is left out of the
        measurement copy, so the figure reports the pins of the user and nothing
        this module wrote. A stranded line stays visible in the document and no
        longer counts against the budget.
      - THE PASS IS IDEMPOTENT BY CONSTRUCTION, not by a guard. The emitted line
        is a pure function of the user's pinned body, so a second pass over
        unchanged pins produces identical bytes and writes nothing.

    A BODY THAT DROPS BACK UNDER BUDGET LOSES ITS WARNING. A warning that
    reports a breach which has ended is the same defect as a frozen number,
    facing the other way. Removing it is a REPAIR of a line this module wrote,
    which is why it is safe; this function never deletes anything a user wrote.

    THE BODY IS READ AS ROWS OF THE WHOLE-FILE PARSE AND NEVER RE-PARSED. The
    body written back and the copy measured are both built from `doc`'s rows,
    with the STALE lines added below their headings, so no text is parsed on
    its own: a parse that starts mid-file starts in a state the whole file does
    not have.

    Args:
        content: Full CLAUDE.md file content.
        doc: The whole-file parse of `content`.
        first, last: The rows of the Pinned body (`_pinned_body`).

    Returns:
        Tuple of (new_full_content, stale_count, was_modified, budget_warning_str).
    """
    pinned_start = doc.lines[first].start
    pinned_end = doc.lines[last].end
    # The bytes to compare against at the end. `was_modified` is DERIVED from
    # this comparison rather than accumulated in a flag, so it cannot disagree
    # with what actually changed -- and a pass that rewrites a warning to the
    # same value reports no modification and skips the write.
    original_pinned_content = content[pinned_start:pinned_end]

    # STEP 1, BEFORE ANY ROW IS READ OR ANY TOKEN IS COUNTED: take back the
    # warning written by an earlier pass. Every step below then sees the user's
    # own pinned body.
    kept_first = _strip_budget_warnings(doc, first, last)
    entries = _entry_rows(doc.find_lines(_PIN_HEADING_ROW, (kept_first, last)), last)

    # Count already-marked entries
    already_stale = sum(1 for entry_first, entry_last in entries
                        if doc.find_lines(_STALE_MARK_ROW, (entry_first, entry_last)))

    # Detect new stale entries. A heading that is the file's last row and has
    # no line break gets no marker.
    stale_entries = detect_stale_entries(doc, kept_first, last)
    markers = {}
    for idx, date_str, _heading in stale_entries:
        heading = doc.lines[entries[idx][0]]
        if content[heading.end - 1:heading.end] in ("\n", "\r"):
            markers[heading.row] = _stale_marker_line(date_str)

    # Build the body to write and the copy to measure from the same rows.
    # The measured copy leaves out EVERY warning row of this module's own
    # shape, wherever it sits: step 1 removed the leading run FROM THE
    # DOCUMENT, and the rest stays in the body written back, so the count
    # stops depending on POSITION while the DELETE stays on the head run.
    warnings = set(doc.find_lines(_BUDGET_WARNING_ROW, (kept_first, last)))
    written: List[str] = []
    measured: List[str] = []
    for row in range(kept_first, last + 1):
        line = doc.lines[row]
        text = content[line.start:line.end]
        written.append(text)
        if row not in warnings:
            measured.append(text)
        if row in markers:
            written.append(markers[row])
            measured.append(markers[row])
    pinned_content = "".join(written)

    total_stale = already_stale + len(stale_entries)

    pinned_tokens = estimate_tokens("".join(measured))
    budget_warning = ""
    if pinned_tokens > PINNED_CONTEXT_TOKEN_BUDGET:
        pinned_content = _budget_warning_line(pinned_tokens) + pinned_content
        # ONE number, used by both consumers. The comment in the file and the
        # status string returned to the caller are built from the same
        # measurement, so a reader can never be shown two different figures for
        # one document.
        budget_warning = f", ~{pinned_tokens} tokens (budget: {PINNED_CONTEXT_TOKEN_BUDGET})"

    # Under budget, the body simply keeps no warning: the strip in step 1 has
    # already taken the outdated one away, and nothing puts it back.

    modified = pinned_content != original_pinned_content
    new_content = content[:pinned_start] + pinned_content + content[pinned_end:]
    return new_content, total_stale, modified, budget_warning


# Returned when the pass would mark the project CLAUDE.md and the file is not
# valid UTF-8. The write decodes strictly and leaves the file untouched rather
# than write replacement characters over the user's bytes. "skipped" routes it
# to session_init's systemMessage.
_UNDECODABLE_SKIP = (
    "Pinned staleness skipped: the project CLAUDE.md is not valid UTF-8, "
    "so it was left unchanged."
)


def check_pinned_staleness(claude_md_path: Optional[Path] = None) -> Optional[str]:
    """
    Detect stale pinned context entries in the project CLAUDE.md.

    A pinned entry is considered stale if it contains a date older than
    PINNED_STALENESS_DAYS. Dates are detected in PR merge references
    (e.g. "PR #123, merged 2026-01-15") and as standalone YYYY-MM-DD
    patterns in entry headings.

    Stale entries get a <!-- STALE: Last relevant YYYY-MM-DD --> comment
    inserted after their heading (if not already marked).

    Also checks if the total pinned content exceeds the token budget and
    adds a warning comment if so (does NOT auto-delete pins).

    This function orchestrates detection (detect_stale_entries) and
    mutation (apply_staleness_markings) as separate steps for testability.

    Args:
        claude_md_path: Explicit path to CLAUDE.md. If None, resolved via
            get_project_claude_md_path(). Callers (e.g. session_init.py)
            may pass the path explicitly so their own resolution can be
            patched independently in tests.

    Returns:
        Informational message about stale pins found, or None.
    """
    if claude_md_path is None:
        claude_md_path, project_root = _resolve_project_claude_md_with_base()
    else:
        # A caller-supplied path came from a TRUSTED resolver -- session_init
        # resolves via _get_project_claude_md_path() and PASSES it here, so
        # production DOES supply the param. Recover its pre-.claude base by
        # inverting the resolver's construction (see _lexical_base_of): the
        # SAME base the resolver used, not a re-derived root, and F1-safe
        # (purely lexical, no symlink follow) + parity-locked.
        project_root = _lexical_base_of(claude_md_path)
    if claude_md_path is None:
        return None

    # Decoded with replacement, so a file that is not UTF-8 is still planned on
    # and gets a valid file's no-op when no pin is due. The text is NEVER
    # WRITTEN as read: the write below happens only after a strict re-read
    # under the lock equals it, so a file that is not UTF-8 is refused there.
    try:
        content = claude_md_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    from shared.claude_md_markers import State, parse, uncertainty_added

    doc = parse(content)
    located = locate_pinned(doc)
    if located.state is not State.FOUND:
        return None
    body = _pinned_body(doc, located)
    if body is None:
        return None
    first, last = body

    has_entries = bool(doc.find_lines(_PIN_HEADING_ROW, body))

    # A section with no entries still needs a pass when a warning is sitting in
    # it. Delete the last pin and the old guard returned here, which stranded
    # that warning where nothing could ever reach it.
    #
    # THE PROBE READS ANY LINE START, THE STRIP READS OFFSET 0. That gap is
    # deliberate. A warning a user has moved below the head is still this
    # module's own report, so the section HAS been reported on and the pass may
    # run. The strip cannot reach that line, so this pass does not repair it and
    # the old line stays.
    #
    # WHETHER A CURRENT WARNING GOES ABOVE IT TURNS ON THE MEASURED BODY ALONE,
    # AND THAT CONDITION IS NEW. `apply_staleness_markings` leaves lines of this
    # shape out of the measurement wherever they sit, so the stranded line adds
    # no token to the decision.
    #   - If the pins of the user exceed the budget, this pass adds one warning
    #     and the section carries two lines, which is what a section WITH
    #     entries does in the same state.
    #   - If the pins of the user are within the budget, this pass adds NOTHING,
    #     writes nothing, and the stranded line is the only one left.
    # THE EARLIER WORDING ASSERTED THE ADDITION WITH NO CONDITION ON IT, and it
    # called the extra line a ratified cost of the residual. That was correct
    # while the count included the stranded line. The exclusion at the
    # measurement site retired it for the COUNT, and the line itself stays.
    #
    # THE FORBIDDEN DIRECTION IS UNCHANGED AND MUST STAY SO. A section carrying
    # NO line of this module's own shape never reaches the pass, whatever its
    # size, so this code never starts a report in a document it has not written
    # to before. The strict `~N tokens (budget: M)` shape carries that
    # discrimination, not the anchor.
    if not has_entries and not _has_budget_warning(doc, first, last):
        return None

    new_content, stale_count, modified, budget_warning = apply_staleness_markings(
        content, doc, first, last
    )

    # Write back if modified — under file_lock with TOCTOU symlink guard.
    # staleness.py is the 6th writer to project CLAUDE.md and must use the
    # same hardening as the other 5 (claude_md_manager + session_resume).
    # See `fcntl_sidecar_lock_pattern` for the canonical pattern.
    if modified:
        # A STALE row is an HTML comment line, so it can end a comment or
        # declaration the user left open above it, and the file then reads as
        # uncertain from the user's opener down. Write nothing when the new
        # text has more rows PACT cannot read than the file it was planned on.
        reason = uncertainty_added(doc, parse(new_content))
        if reason is not None:
            return f"Pinned staleness skipped: {reason}"

        # Function-level import to avoid circular dependency:
        # session_init.py imports staleness at module level, and also
        # imports from shared.claude_md_manager — a module-level
        # import here would create a staleness → claude_md_manager →
        # (indirectly) staleness cycle on some Python versions. That reason
        # constrains function-versus-module level only, so the statement is
        # free to sit here rather than lower down.
        #
        # KEEP IT ABOVE THE `try:` BELOW. That block handles ContainmentError,
        # which this import binds. An import failure inside the block leaves
        # the name unbound, so Python reports the handler and hides the cause.
        # Do not move it back in. Do not wrap it in its own handler, because
        # an ImportError must reach the caller.
        from shared.claude_md_manager import (
            ContainmentError,
            _atomic_write_text,
            file_lock,
        )
        try:
            with file_lock(claude_md_path):
                # #1247: containment (in _atomic_write_text) REPLACES the
                # former leaf is_symlink guard -- inside the lock (TOCTOU-safe).
                # It catches the symlinked-PARENT escape the leaf guard MISSED
                # (F1) and safely ALLOWS a benign in-project leaf redirect; it
                # does NOT dominate is_symlink (overlapping-but-different sets).
                # Status string stays opaque.
                # Re-read inside the lock — a concurrent update_session_info
                # may have landed between our outer
                # read at L348 and the lock acquisition. If content changed,
                # skip this pass: the staleness markers are idempotent and
                # the next session will re-detect any stale entries. This
                # avoids clobbering a concurrent writer's SESSION_START block.
                current = claude_md_path.read_text(encoding="utf-8")
                if current != content:
                    return None
                # Atomic (temp + rename) so a crash mid-write cannot truncate
                # the always-loaded CLAUDE.md. NOTE: unlike the other CLAUDE.md
                # write sites this one never set a mode, so `write_text` left
                # the file's existing permissions alone; the helper normalises
                # it to 0o600, matching every other writer in the plugin.
                #
                # THE LINE ENDING IS NOT THIS SITE'S BUSINESS ANY MORE, AND DO
                # NOT RESTORE ONE HERE. This module used to detect the ending
                # above and re-apply it on this line. `_atomic_write_text` now
                # reads the ending off the target and applies it for each of its
                # callers, so a restore here would run the substitution two
                # times. Every measurement above continues to run on the
                # LF-normalised `content`, exactly as it always did.
                # AN INSTRUCTION FOR A LATER EDITOR, NOT A STATEMENT ABOUT
                # TODAY. A statement about today goes stale in silence. An
                # instruction about what to do continues to apply.
                #
                # `project_root` is typed `Path | None` and this parameter
                # takes a `Path`. A type checker reports that. No None reaches
                # this line at this time, and the reason spans THREE HOPS:
                #   1. `_resolve_project_claude_md_with_base` returns a path
                #      and a base together, or returns None for the two.
                #   2. `_lexical_base_of` returns a `Path` and returns no None.
                #   3. The `claude_md_path is None` test above returns first.
                # A CHAIN OF THREE HOPS CAN BREAK WITH NO LOCAL SIGNAL. Each
                # hop is correct on its own, and one edit to one of them opens
                # this line while the other two continue to read as correct.
                #
                # IF YOU ADD A CALLER THAT CAN PASS None HERE, ADD A GUARD AND
                # CHOOSE ITS DIRECTION ON PURPOSE. THE CHOICE IS NOT FREE.
                # This function writes the project CLAUDE.md, and this
                # repository does not track that file, so an incorrect refusal
                # has no commit behind it to recover from. A guard that refuses
                # protects the write and can lose the update. A guard that
                # continues keeps the update and can write outside the base the
                # resolver trusted. Do not add a guard as a cleanup step: a
                # guard with an unchosen direction moves the fault instead of
                # removing it.
                _atomic_write_text(claude_md_path, new_content, project_root)
        except ContainmentError:
            return "Pinned staleness skipped: path precondition not met."
        except UnicodeDecodeError:
            # The strict re-read above: a pin was due and the file is not UTF-8.
            return _UNDECODABLE_SKIP
        except TimeoutError:
            return "Pinned staleness update skipped: lock contention."
        except OSError as e:
            # `Failed` IS THE ROUTING TOKEN. session_init step 3d routes this
            # return into system_messages on a substring test, so the prefix
            # stays byte-identical.
            #
            # THE CAUSE TOKEN COMES FROM A CLOSED VOCABULARY. The former
            # comment here said a truncation kept the absolute CLAUDE.md path
            # out of the status string. MEASURED, THAT IS INCORRECT: a cut
            # keeps the LEADING characters and an OSError renders as
            # `[Errno NN] <strerror>: '<path>'`, so a 50-character cut of a
            # PermissionError still emits the home directory and the user
            # name. A cut narrows the leak and does not close it.
            logger_msg = f"Failed to update pinned staleness: {failure_cause(e)}"
            return logger_msg

    if stale_count > 0:
        return f"Pinned context: {stale_count} stale pin(s) detected{budget_warning}"
    if budget_warning:
        return f"Pinned context{budget_warning}"

    return None


def check_pinned_block_signal(
    claude_md_path: Optional[Path] = None,
) -> Optional[CapViolation]:
    """Detect stale-pin overflow that warrants a SessionStart block directive.

    Returns a CapViolation(kind=\"stale\") when the stale pin count meets or
    exceeds PIN_STALE_BLOCK_THRESHOLD; None otherwise. Caller (session_init)
    emits an unconditional directive in additionalContext on positive
    detection — never exit-2 (would break /clear and /resume per plan
    key-decisions row 6).

    Fail-open: all I/O and parse errors yield None. The block directive
    ONLY fires on positive detection; ambiguous state never blocks.

    Args:
        claude_md_path: Explicit path. If None, resolved via
            get_project_claude_md_path(). Callers may patch resolution
            independently from this module.

    Returns:
        CapViolation describing the stale overflow, or None.
    """
    if claude_md_path is None:
        claude_md_path = _get_project_claude_md_path()
    if claude_md_path is None:
        return None

    try:
        # Read-only: a byte that is not UTF-8 decodes to U+FFFD and the rest
        # of the file still counts.
        content = claude_md_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    from shared.claude_md_markers import State, parse

    try:
        doc = parse(content)
        located = locate_pinned(doc)
        if located.state is not State.FOUND or _pinned_body(doc, located) is None:
            return None
        pins = section_pins(doc, located)
    except Exception:  # noqa: BLE001 — fail-open by construction
        return None

    return check_stale_block(pins, threshold=PIN_STALE_BLOCK_THRESHOLD)
