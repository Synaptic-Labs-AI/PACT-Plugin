#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/pin_staleness_gate.py
Summary: PreToolUse marker-gate that denies Edit/Write on the project
         CLAUDE.md's Pinned Context section when a stale-pins-pending
         marker is present in the session directory.
Used by: hooks.json PreToolUse with matcher \"Edit|Write\"

Phase F defense-in-depth backstop for #492. The SessionStart
additionalContext directive (session_init.py step 4b) is the primary
enforcement; this hook is meant to be the secondary guard that fires at the
moment of the Edit/Write call rather than relying on the orchestrator honoring
the directive. TODAY IT NEVER ARMS: the marker's only writer
(session_init.check_pin_stale_block_directive) runs before the session dir is
resolved, so the marker is never written and this gate never denies (issue
#1651).

Gate triggers only when ALL hold:
  1. Tool is Edit or Write (enforced by hooks.json matcher)
  2. Target file path resolves to the project CLAUDE.md
  3. The POST-EDIT DOCUMENT has more pins than the current one.
     THE GATE ASKS WHAT THE FILE BECOMES. It does not ask where the
     edit sat. Edit and Write are the same question here: build the
     document the tool call will produce, then compare the two
     documents. See `_simulate_post_edit_document`.
       THERE IS NO LOCUS, NO ANCHOR AND NO BOUNDARY IN THIS CONDITION,
       and that is the repair rather than an omission. An earlier
       revision tested where an Edit fragment sat against the pinned
       span. Two edits that put THE SAME PIN IN THE SAME PLACE got
       OPPOSITE verdicts, because an anchor is not the object the
       decision is about.
       THE SLICE THE COUNT USES IS A DIFFERENT MATTER and it survives:
       `_counts_show_an_add` bounds the COUNT to the pinned section,
       which answers which bytes belong to the pins. Do not read that
       bound as a locus and do not remove it with one.
  4. Stale-pins-pending marker exists in session_dir
  5. The session role is LEAD. The predicate is `pact_context.is_lead`, so a teammate frame and an `unknown` frame alike do not reach this gate.

SACROSANCT (post-load runtime): every raisable path after module load is
wrapped in try/except that defaults to allow (exit 0 with suppressOutput).
A gate-logic bug must never block a tool call. Fail-open: missing
session_dir, unparseable CLAUDE.md, unresolvable marker → allow.
Module LOAD failure is the deliberate exception: a failed import would
otherwise crash the hook (exit 1 = platform-non-blocking = silent
fail-open), so it denies via _emit_load_failure_deny instead.

Input: JSON from stdin with tool_name, tool_input, session_id, etc.
Output: JSON with hookSpecificOutput.permissionDecision (deny case)
        or {\"suppressOutput\": true} (allow / passthrough)
"""

from __future__ import annotations

# ─── stdlib first (used by _emit_load_failure_deny BEFORE wrapped imports) ─
import json
import re
import sys
from pathlib import Path
from typing import NoReturn

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def _emit_load_failure_deny(stage: str, error: BaseException) -> NoReturn:
    """Stdlib-only fail-closed deny for module-load failure. Mirrors the
    ``dispatch_gate`` / ``bootstrap_gate`` analogue.

    Without this, a raise from the cross-package imports below would crash the
    hook (exit 1), which the platform treats as a NON-blocking PreToolUse hook
    — the Edit/Write tool would PROCEED and the staleness gate would silently
    FAIL-OPEN. Emitting a deny + exit 2 keeps the gate fail-CLOSED.
    hookEventName MUST be present.
    """
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"PACT pin_staleness_gate {stage} failure — blocking for safety. "
                f"{type(error).__name__}: {error}. Check hook installation "
                "and shared module availability."
            ),
        }
    }))
    print(
        f"Hook load error (pin_staleness_gate / {stage}): {error}",
        file=sys.stderr,
    )
    sys.exit(2)


def _report_fail_open(stage: str, error: BaseException) -> None:
    """Emit on a fail-open path, so a silent catch stops being silent.

    FAIL-OPEN ON AN EXCEPTION IS THE CORRECT DIRECTION AND IT STAYS. A gate
    that refuses the edits of a user must not break an edit over a defect of
    its own. THE DEFECT WAS THAT THE CATCH SAID NOTHING, not that it was wide.

    WHY A SILENT CATCH IS THE PROBLEM. It cannot separate A DEFECT IN THIS GATE
    from AN ANOMALY IN THE DATA, and the two want different volumes. MEASURED
    ON THIS BRANCH: a rename left a return naming variables that no longer
    existed. The NameError reached the catch below, the whole Edit path
    returned the quiet value for each payload, and NOTHING REPORTED IT. A plain
    coding error presented as total permissiveness with no signal.

    THIS COVERS THE RUN TIME AND AN ARM COVERS THE BUILD TIME. A positive case
    that only the intended logic can produce catches a dead path when the suite
    runs. It does NOT catch a path that a later edit kills in production. The
    emit catches that one, and neither covers the other, so both are required.

    STDERR IS THE CHANNEL because stdout carries the hook protocol. This
    function never raises: an emit that fails must not become the fault it
    reports.
    """
    try:
        print(
            f"PACT pin_staleness_gate fail-open at {stage}: "
            f"{type(error).__name__}: {error}",
            file=sys.stderr,
        )
    except Exception:  # noqa: BLE001 - the report must not become the fault
        pass


# ─── fail-closed wrapper on cross-package imports ──────────────────────────
try:
    import shared.pact_context as pact_context
    from shared import match_project_claude_md
    from shared.constants import PIN_STALENESS_MARKER_NAME
    from pin_caps import parse_pins
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_deny("module imports", _module_load_error)

# THE MARKER NAME IS DEFINED IN `shared.constants` AND IT IS IMPORTED ABOVE,
# INSIDE THE FAIL-CLOSED WRAPPER. Three modules in three different frames read
# that name, and a module with no exit path is the only safe home for a name
# that a PostToolUse hook and a SessionStart hook also read. The per-session
# scoping, the two-surface lifecycle, and the `pin_marker_writer.py`
# name-collision trap are recorded at that definition. Read it before you
# touch this marker.
#
# THE IMPORT IS A RE-EXPORT AS WELL AS A READ. Other modules and the arms take
# this name from `pin_staleness_gate`, so the binding must stay available here.
#
# 🔴 THIS MODULE IS READ ONLY ON THIS MARKER, AND THAT IS A CONSTRAINT RATHER
# THAN A PREFERENCE. The gate tests whether the marker is present. It must
# never create it, and it must never remove it. IF A REFUSAL REMOVED ITS OWN
# TRIGGER, the trap would disappear by DELETING THE EVIDENCE OF IT rather than
# by repair: the next add would be allowed for the wrong cause, and every arm
# over this gate would go green while that happened. A coder can close the trap
# by accident that way, which is why the prohibition is written at the name
# rather than left implied.

# 🔴 THE COMMAND NAMED HERE MUST BE THE COMMAND THAT ARCHIVES, AND FOR A TIME
# IT WAS NOT. This text named `/PACT:pin-memory`, which does not archive: it
# ADDS a pin and it points the user at `/PACT:prune-memory` for removal. So a
# user who OBEYED this refusal arrived at a command that cannot clear the
# condition, and stayed denied. THAT IS THE TRAP THIS GATE EXISTS TO PREVENT,
# reached through the MESSAGE rather than through the predicate, which is why
# it survived every arm aimed at the predicate.
# BEFORE YOU EDIT THIS STRING, OPEN THE COMMAND FILE AND CONFIRM THE COMMAND
# ARCHIVES. The deny text is not evidence about its own subject.
#
# AND IT PROMISES NO ORDER. An earlier wording said to archive BEFORE editing,
# which the gate could not deliver: the marker was created and removed at
# SessionStart only, so a user who obeyed was denied for the rest of the
# session. `track_files.clear_pin_staleness_marker_if_resolved` now clears the
# marker when the signal clears, on a hand edit and on the archive command, so
# the sentence below is a statement about a mechanism that is available rather
# than a promise.
_DENY_REASON = (
    "Pinned Context edits are gated: stale pins detected. "
    "Run /PACT:prune-memory to archive the stale pins. "
    "PACT re-checks the pins after that command runs, so the archive "
    "clears this gate inside the same session."
)

_GATED_TOOLS = frozenset({"Edit", "Write"})


# A heading a memory writer emits. `working_memory.py` builds each one as
# `f"### {date_str}"` with `date_str = now.strftime("%Y-%m-%d %H:%M")`, so the
# shipped shape is a date and a time.
#
# THIS PATTERN HAS THREE PRODUCERS AND ONLY TWO OF THEM ARE WRITERS. Recorded
# here because a reader who sees one writer proposes to derive this pattern
# from that writer's format, and the count is what refuses it:
#   1. `working_memory.py`, a machine writer, emits a date AND a time.
#   2. The retrieved-entry writer, a machine writer, reaches the same region.
#   3. A HUMAN, who hand-writes an entry and is bound by no format at all.
#   And a fourth ROUTE that is not a writer: a memory-record FIELD VALUE can
#   carry a heading into the managed region, so a title can arrive with no
#   author who intended a title.
# AN EMITTER SAYS WHAT TO PRODUCE. AN ACCEPTOR SAYS WHAT TO ADMIT. They are
# different jobs, and the acceptor must admit what every producer emits.
#
# THE PATTERN WAS WRITTEN BY READING THE WRITERS. IT IS NOT COMPUTED FROM THEM,
# AND IT MUST NOT BE. Do not replace it with a pattern built from the writer's
# `strftime` format. That derivation is refused on three causes, each one
# sufficient alone:
#   CAUSE 1, IT NARROWS THE ACCEPTOR TO ONE PRODUCER. The writers emit a date
#     AND a time, so a derived pattern has a REQUIRED time and REFUSES
#     `### 2026-08-05`. That heading is then counted as a PIN, a memory write
#     that adds one reads as a pin add, and the gate DENIES a faithful edit.
#     The cost is cardinal and it is measured, not predicted.
#   CAUSE 2, THE ALPHABET RELOCATES RATHER THAN VANISHES. A derivation needs a
#     DIRECTIVE MAP from `strftime` directives to fragments, and an EXPRESSION
#     TRACER, because the writer holds no literal title format: it builds
#     `f"### {date_str}"` at one site and `date_str` at another. One alphabet
#     becomes two.
#   CAUSE 3, THE COUPLING HAS NO PRECEDENT. No shipped hook imports below the
#     memory skill package. A count of zero is evidence of a convention and
#     not proof of a rule, so the coupling is priced as new. Where a hook and
#     that package need one behaviour, this repository ships two copies and a
#     drift gate, and that is the house answer rather than a workaround.
#
# WHAT SURVIVES THE REFUSAL, STATED BECAUSE IT RUNS AGAINST IT. A tolerance
# list is an author-written alphabet, and a later widening can be ABSORBED by
# adding a line to it. That is a maintenance hazard and it is real. It is NOT
# removed by deriving: a DIRECTIVE MAP IS ITSELF A LIST, so the absorption
# moves one layer down and returns as an AUTHOR CHOICE at the unknown-directive
# arm, raise-and-fail-open or fall-back-and-widen. Smaller, and not absent.
#
# EACH TOLERANCE BELOW CARRIES ITS REASON AT ITS SITE, so a later widening
# cannot arrive as a bare line with no argument attached to it.
# 🔴 A REASON PER TOLERANCE REDUCES THE ABSORPTION. IT DOES NOT BAR IT. A
# future editor can write a reason too, and a plausible reason beside a wrong
# line reads as more considered than a bare line, not less. This is the
# residual, stated rather than closed.
#
# THE ARM THAT HOLDS THIS IS IN `tests/test_working_memory.py`, WITH THE
# WRITERS, because a person who edits a writer does not read the tests of this
# gate. IT PINS A RELATION RATHER THAN A FORMAT, AND THE DIFFERENCE IS
# LOAD-BEARING: an arm pinning only the emitted format would redden when a
# WRITER moves and stay GREEN when THIS pattern moves, so tightening the
# pattern here would break the pair with a passing arm over it. Do not replace
# that arm with a format assertion.
#
# VERBOSE MODE IS WHAT PUTS EACH REASON AT ITS SITE. It changes no accepted
# string here: the pattern carries no literal space, so the whitespace that
# verbose mode discards does not occur in it, and the marker is escaped
# because an unescaped `#` would start a comment.
_DATE_LED_HEADING_RE = re.compile(
    r"""
    ^\#\#\#                # The marker. NO TOLERANCE: a heading at another
                           # depth is a different structural thing, not a
                           # spelling of this one.
    \s+                    # TOLERANCE, one-or-more. A person types a second
                           # space and cannot see it. Withdraw this only if a
                           # single space becomes enforceable at every
                           # producer, and producer 3 is a human, so it cannot.
    \d{4}-\d{2}-\d{2}      # The date. NO TOLERANCE on the widths or the
                           # separator: a fixed-width date is what makes this
                           # predicate decidable, and every producer that
                           # emits a date emits this shape.
    (?:\s+\d{2}:\d{2})?    # TOLERANCE, the whole time group is OPTIONAL, and
                           # this is the load-bearing one. Producers 1 and 2
                           # always emit a time, so this branch exists for
                           # producer 3 alone. REMOVE IT and a hand-written
                           # date-only entry counts as a PIN, the count rises,
                           # and a faithful edit is DENIED. That is an
                           # over-block and it is the cardinal direction.
                           # KEEP IT and a pin titled a bare date with no
                           # marker drops out of the count, which is an
                           # under-block over a population a user ruling
                           # declares empty. Withdraw this only when that
                           # ruling is withdrawn.
    \s*$                   # TOLERANCE, zero-or-more, AND THE ANCHOR IS NOT
                           # part of it. Trailing space is invisible to a
                           # person and must not change a verdict. THE `$`
                           # BESIDE IT IS NOT A TOLERANCE AND MUST NOT BE
                           # WIDENED: let text follow the date and
                           # `### 2026-08-05 Draft notes` stops counting as a
                           # pin, so a user who RENAMES that pin to drop the
                           # date loses a title from the old count and the
                           # gate FIRES on a faithful rename. The user ruling
                           # covers a BARE date only, so it does not cover
                           # that class.
    """,
    re.VERBOSE,
)


def _is_memory_entry(pin) -> bool:
    """Report whether a parsed entry is a memory entry rather than a pin.

    THE PREDICATE IS A CONJUNCTION AND EACH HALF IS LOAD-BEARING. An entry is a
    memory entry when its heading is DATE-LED and it carries NO
    `<!-- pinned: -->` marker. The date alone does not decide: a curator can
    title a pin with a date, and that pin carries a marker.

    WHY THE DATE ALONE CANNOT DECIDE, WHICH IS A PROOF RATHER THAN A PREFERENCE.
    A hand-written date-only memory entry and a pin titled a bare date are THE
    SAME STRING, and the two want opposite verdicts. So no function of the
    heading alone returns two answers, whatever it is spelled as. The marker is
    the second signal that makes the two separable.

    THE RESIDUAL THIS CARRIES, RECORDED RATHER THAN HIDDEN.
      R1, an add of a date-titled pin with NO marker, is not counted, so the
      gate stays quiet. That direction is an under-block.
      R2, an edit that ADDS a missing marker to a date-titled pin, moves no
      pin, and this predicate counts 0 then 1, so the gate FIRES. That
      direction is a cardinal OVER-BLOCK.
    THE TRIGGER FOR THE TWO IS ONE POPULATION: a date-titled pin with no
    marker. A USER RULING DECLARES THAT POPULATION EMPTY, because a pin is not
    written that way in good faith. THE RULING IS WHAT MAKES R2 ACCEPTABLE, and
    R2 is cardinal, so the ruling carries more weight than the under-block it
    was first priced against.

    WHAT MAKES THE POPULATION NON-EMPTY AGAIN, WHICH IS THE THING TO WATCH: a
    writer that emits a pin with a bare-date title and no marker. If one
    appears, R1 and R2 stop being hypothetical and this predicate wants a
    re-price rather than a patch.

    A GUARD ON THE MARKER-ADDING EDIT WAS CONSIDERED AND REFUSED, AND THE
    REASON IS THE TRADE RATHER THAN AN IMPOSSIBILITY. An earlier note here said
    such a guard cannot separate the marker-adding edit from a true add without
    reading a file the edit has not landed in. THAT REASON IS RETIRED, AND THE
    SIMULATION RETIRES IT TWICE OVER: the gate now builds the post-edit
    document and compares it against the current one, so the file the edit has
    not landed in IS the object this predicate reads. See
    `_simulate_post_edit_document`.
    THE TRADE IS WHAT REFUSES THE GUARD, and it is a set difference:
      THE CANDIDATE, the strongest of the two that were driven: if the MULTISET
      of `### ` heading lines is unchanged across the edit, no heading
      appeared, so no pin appeared. Decline.
      IT CLOSES R2. IT OPENS B3, A PROMOTION: a date-titled entry moves from
      Working Memory INTO the pinned region and gains a marker. The heading
      multiset is unchanged, so the guard declines a GENUINE pin add.
      B3 IS REACHABLE IN GOOD FAITH. Promotion of an entry to a pin is the
      purpose of the pin command, and a user who does it by hand writes that
      edit with no knowledge of this gate. It is not the deliberate class this
      project treats as tolerable.
    AND THE TRIGGER IS EMPTY, WHICH IS THE SECOND AND INDEPENDENT REASON. R2
    needs a date-titled pin with NO marker, and a user ruling declares that
    population empty. So the guard is INSURANCE, its premium is a reachable
    missed add, and the insured event is one the ruling says does not happen.
    IF A WRITER EVER EMITS A BARE-DATE PIN WITH NO MARKER, this ruling is the
    one to re-open first, and the project rule then decides it the other way,
    because a live cardinal over-block outranks a missed staleness reminder.

    AND THE POPULATION IS RE-MEASURABLE, WHICH IS THE DURABLE HALF OF THIS
    REASON: a ruling is a claim that a reader must go and find, and a
    measurement with its counting rule is one that anybody can re-derive.
    MEASURED 2026-08-13 over the live pinned region: 12 headings, 12 markers,
    and 0 that are date-led AND unmarked, so the R2 trigger population is
    EMPTY. COUNTING RULE: bound the region with
    `staleness._parse_pinned_section`, count `^### ` and `^<!-- pinned:` at
    column 0, and judge with `_is_memory_entry` over `parse_pins`, the oracle
    of this guard. CONTROLS: an impossible pattern returned 0, and
    `_is_memory_entry` returned True on a synthetic bare-date unmarked
    heading, so the 0 means EMPTY rather than an inert predicate. RE-RUN IT.

    🔴 THE RECIPROCAL HALF OF A COUPLING, PLACED AT THE SITE OF THE EDIT THAT
    WOULD REMOVE IT. This exclusion and the whole-text fallback in
    `_counts_show_an_add` ARE A PAIR WHERE THE COUNT BOUND DECLINES. Where the
    two sides resolve a `## Pinned Context` span, that bound alone holds a
    memory write quiet and this predicate decides nothing. Where they do not,
    the fallback keeps the memory entries in the slice and THIS function is
    what drops them, so a deletion here re-introduces the over-count. DO NOT
    REMOVE ONE WITHOUT THE OTHER, and read the matching sentence at the
    fallback arm before you change either.
    """
    if not _DATE_LED_HEADING_RE.match(pin.heading.strip()):
        return False
    return pin.date_comment is None


def _count_pin_comments(text: str) -> int:
    """Count pins using `parse_pins` as the canonical oracle.

    Symmetric-oracle invariant (closes 2 HIGH bypasses): the gate MUST
    count pins using the same parser that enforces the count cap at
    add-time (`pin_caps.parse_pins`).

    🔴 AND THE SAME PARSER IS NOT ENOUGH. IT MUST READ THE SAME KIND OF
    OBJECT. The invariant said PARSER alone, and measured, the two gates
    then used one parser on TWO OBJECTS: the cap gate parsed a SIMULATED
    POST-EDIT DOCUMENT and this gate parsed an EDIT FRAGMENT. That is the
    same defect one level up, because a parser agreeing on two different
    inputs proves nothing about the two verdicts. THE INVARIANT NOW HAS TWO
    CLAUSES: the same parser, AND the same kind of object, which is a
    post-edit document. `_simulate_post_edit_document` is what supplies the
    second clause. A future change that feeds this counter a fragment again
    satisfies clause one and breaks clause two.

    A regex substring count of
    `<!-- pinned:` is asymmetric with `parse_pins`, which:
      (a) recognizes a bare `### Heading` (no date comment) as a Pin,
      (b) tolerates arbitrary whitespace between `<!--` and `pinned:`
          via its `\\s*` patterns (e.g. `<!--  pinned:` double-space),
      (c) matches case-insensitively.
    Substring counts undercount (a) and (b), letting an adversarial ADD
    slip past the ADD-shape gate while still landing in CLAUDE.md as a
    parse_pins-visible pin.

    THIS FUNCTION DOES NOT CHOOSE A SLICE, AND IT USED TO. It counted the
    managed region when the markers were present and the whole text when they
    were not, so the branch was decided PER STRING. The two sides of one
    decision could then reach different branches, and a difference taken across
    two different slices is not a comparison. That is the STRADDLE, and it bit
    in the two directions: a straddle over-blocked when no pin moved, and a
    straddle MISSED a true add of four pins to five.

    THE SLICE NOW BELONGS TO THE DECISION, in `_counts_show_an_add`, which is
    the only place that can see the two sides at one time. This function counts
    across the body its caller supplies and nothing else. (`_is_add_shaped_edit`
    chooses WHICH SIMULATION runs, the Write one or the Edit one. It does not
    choose the slice, and it no longer chooses between two comparisons either,
    because there is ONE comparison over two documents.)

    THE COUNT PREDICATE, WHICH IS THE OTHER HALF OF THE PAIR. `parse_pins`
    stays the oracle, so the symmetric-oracle invariant above holds. One class
    is then dropped from the result: a heading that is date-led AND carries no
    `<!-- pinned: -->` marker is a memory entry rather than a pin. See
    `_is_memory_entry` for the residual this predicate carries and for the
    population a user ruling declares empty.

    Fail-open: non-str input returns 0. Any parse_pins failure (should
    not raise by its own contract, but defense-in-depth) returns 0.
    """
    if not isinstance(text, str):
        return 0
    try:
        return sum(1 for pin in parse_pins(text) if not _is_memory_entry(pin))
    except Exception as exc:  # noqa: BLE001 — fail-open
        # BROAD CATCH, SO IT EMITS. A parse fault here is a defect rather
        # than an expected state of the data.
        _report_fail_open("pin count", exc)
        return 0


def _counts_show_an_add(old_text: str, new_text: str) -> bool:
    """Compare the two sides of ONE decision across ONE slice.

    RULE 1, AND IT IS THE WHOLE REASON THIS FUNCTION EXISTS. A difference taken
    across two DIFFERENT slices is not a comparison. The counter used to pick
    its own slice per string, so the two sides of one decision could reach
    different branches. That is the STRADDLE and it bit in the two directions:
    one straddle over-blocked when no pin moved, and one MISSED a true add of
    four pins to five.

    THE SELECTION, and it is a TOTAL function with no decline arm:
      0. THE COUNT SLICE FIRST. Ask each side for its `## Pinned Context`
         span. If BOTH
         sides resolve one, that section is the slice for the two.
      1. Otherwise, ask each side whether it carries the managed markers.
      2. If the two agree, use that branch for the two.
      3. If the two disagree, use the WHOLE TEXT for the two.
    The whole text is a slice each side can always carry, so there is no
    failure direction to choose here and no exception arm to add.

    STEP 0 IS A COUNT BOUND AND NOT A LOCUS. IT ANSWERS WHICH BYTES BELONG TO
    THE PINS, AND THE GATE NO LONGER ASKS WHERE AN EDIT SAT AT ALL. Do not
    remove this bound when retiring a locus: they are different questions and
    removing this one re-opens the memory-entry over-count.
    ITS SAFETY IS A SUBSET RELATION RATHER THAN
    A CASE LIST. It narrows the slice ONLY where both sides resolve a pinned
    section, and it falls back to the shipped selection everywhere else, so the
    documents it treats differently are a STRICT SUBSET of the ones the shipped
    selection got wrong. It cannot be worse on the cardinal axis, for any
    document, including shapes nobody has enumerated.
    WHY BOTH SIDES AND NOT ONE. A bound taken from ONE side counts 0 against 0
    on any payload that carries no heading, and every Edit fragment is such a
    payload, so the gate would stop firing on the whole Edit branch. That is a
    MISSED ADD, and it is the direction this counter exists to close. Measured:
    the one-sided form fixes five over-blocks and opens two missed adds. The
    two-sided form fixes two and opens none.

    WHAT STEP 0 DOES NOT CLOSE, RECORDED SO A LATER READER DOES NOT READ THE
    COUNT BOUND AS COMPLETE. A document with NO `## Pinned Context` heading has NO
    span, so the position is UNDEFINED rather than unavailable, and no file
    read and no instrument change supplies one. A prose-titled entry added to
    such a document continues to over-block. THE POPULATION IS NOT HAND EDITS
    ALONE: a memory-record field value can carry a prose title into the managed
    region, so a machine writer reaches this shape with no human in the route,
    and a fresh project file with no pins is exactly this shape.
    WHAT WOULD MAKE IT REDUCIBLE: a SECOND ANCHOR. These documents carry a
    `## Working Memory` heading, and a predicate that treats a heading below
    that anchor as a memory entry has a defined position. That is a new
    predicate with its own failure direction, so it is recorded as a route and
    not taken here.

    THE SCOPE OF THE ENUMERATION ABOVE, STATED SO IT IS NOT READ AS THE WHOLE
    SET. It covers the shapes step 0 cannot REACH, which are the documents
    where no pinned span resolves. It does NOT cover the shapes INSIDE the
    bounded slice. Measured example: a `### ` line added to a PIN BODY, fenced
    or bare, sits within the span, and `parse_pins` counts it, so the two sides
    differ by one and the verdict is an add. That verdict is the same before
    and after this bound, so the bound neither opened it nor closed it.

    AND THIS CASE IS RULED. A residual on this gate sits in one of THREE
    states, and not two. (1) DECLARED empty by a user ruling: that is R2, at
    `_is_memory_entry`. (2) MEASURED at zero, with the shape treated as REAL
    and the refusal to close it ruled: that is THIS case. (3) UNRULED, with
    neither a declaration nor a measurement. THE WARRANT HERE BEGAN AS AN
    ARCHITECT DECISION AND A USER RULING NOW CARRIES IT. Re-open it on a
    measurement above zero rather than on preference. The counting rule for
    that measurement is recorded at `_is_memory_entry`.

    🔴 THE WHOLE-TEXT FALLBACK KEEPS THE MEMORY ENTRIES AND `_is_memory_entry`
    DROPS THEM, AND THE TWO ARE A PAIR WHERE STEP 0 DECLINES. Where the two
    sides resolve a pinned span, step 0 alone holds a memory write quiet. Where
    they do not, this fallback is the wider slice and the exclusion is what
    keeps the count correct. DO NOT REMOVE THE DATE-LED EXCLUSION AND KEEP THIS
    FALLBACK.

    THE EXCEPTION BEHAVIOUR IS UNCHANGED. This selects a slice. The fail-open
    arms that the caller declares SACROSANCT stay where they are.
    """
    # THESE IMPORTS ARE FUNCTION-LOCAL AND THAT IS THE EXCEPTION POSTURE, NOT A
    # STYLE CHOICE. A raise from either one lands inside the caller's
    # SACROSANCT fail-open catch, so an unresolvable import ALLOWS the edit.
    # That is the correct direction for this gate and it is the direction the
    # module-load block at the top deliberately does NOT take. Moving either
    # import to module scope would put it under the fail-CLOSED load wrapper
    # and turn a missing dependency into a DENY of the user's own edit.
    from shared.claude_md_manager import extract_managed_region
    from staleness import _parse_pinned_section

    # STEP 0, THE COUNT BOUND. `allow_empty_section=True` is what makes an
    # EMPTY pinned section resolve. Without it an empty section is
    # indistinguishable from an absent one, this step declines, and the empty
    # side falls back to a wider slice while the other side does not. That is
    # the straddle again, in a new place.
    old_pinned = _parse_pinned_section(old_text, allow_empty_section=True)
    new_pinned = _parse_pinned_section(new_text, allow_empty_section=True)
    if old_pinned is not None and new_pinned is not None:
        return (
            _count_pin_comments(new_pinned[2])
            > _count_pin_comments(old_pinned[2])
        )

    old_is_managed = extract_managed_region(old_text) is not None
    new_is_managed = extract_managed_region(new_text) is not None

    if old_is_managed and new_is_managed:
        old_slice = extract_managed_region(old_text)[0]
        new_slice = extract_managed_region(new_text)[0]
    else:
        # Either the two sides agree that no managed region is present, or they
        # DISAGREE and the whole text is the slice the two can always carry.
        old_slice = old_text
        new_slice = new_text

    return _count_pin_comments(new_slice) > _count_pin_comments(old_slice)


def _simulate_post_edit_document(
    tool_input: dict, current: str, tool_name: str = ""
) -> str | None:
    """Build the document as it WILL BE after this tool call.

    THE GATE ASKS WHAT THE FILE BECOMES, NOT WHERE THE EDIT ANCHORED. An
    earlier revision compared the two Edit FRAGMENTS and tested where the
    fragment sat. Two edits that put THE SAME PIN IN THE SAME PLACE got
    OPPOSITE verdicts, because the anchor is not the object the decision is
    about. Four bound repairs each moved that boundary and
    each was superseded. THE SIMULATION REMOVES THE BOUNDARY RATHER THAN
    MOVES IT: with a post-edit document there is no fragment, no anchor and
    no locus to bound.

    THE SHAPE IS PORTED FROM `pin_caps.build_simulated_pins`, WHICH DOES THIS
    FOR THE CAP GATE, so the two gates now read the same KIND of object. Its
    three Edit edges are reproduced here:
      `replace_all` TRUE  -> replace each occurrence.
      `replace_all` FALSE -> replace the first occurrence.
      an EMPTY `old_string` -> return the PRE-state. `str.replace` with an
        empty needle puts the replacement between each character, which is a
        document the tool never produces. Returning the pre-state makes the
        caller compare pre against pre, so the normal contract applies and a
        malformed payload cannot become a silent bypass.

    WHERE I DIVERGE FROM THE PRECEDENT, AND WHY. `pin_caps` RAISES on a
    non-string payload and leaves the fail-open to its caller. This returns
    None instead, because the caller here is a SACROSANCT fail-open gate that
    must not depend on an exception arriving at the correct handler. None and
    a raise reach the same verdict, and None reaches it without a handler.

    THE KNOWN LIMIT OF THE SIMULATION, STATED RATHER THAN HIDDEN. For a
    NON-UNIQUE `old_string` with `replace_all` FALSE, this replaces the first
    occurrence while the platform REFUSES the edit. So the gate judges a
    document the platform will not produce. The edit fails either way, so
    neither verdict reaches the file, and this is recorded as a limit rather
    than repaired here.

    Returns the post-edit document, or None when the payload cannot be read
    as an edit at all.
    """
    if tool_name:
        is_write = tool_name == "Write"
    else:
        is_write = "content" in tool_input

    if is_write:
        # A Write IS the post-edit document. No simulation is necessary.
        new_content = tool_input.get("content", "")
        if not isinstance(new_content, str):
            return None
        return new_content

    old_string = tool_input.get("old_string", "")
    new_string = tool_input.get("new_string", "")
    if not isinstance(old_string, str) or not isinstance(new_string, str):
        return None
    if old_string == "":
        return current
    if bool(tool_input.get("replace_all", False)):
        return current.replace(old_string, new_string)
    return current.replace(old_string, new_string, 1)


def _is_add_shaped_edit(
    tool_input: dict, claude_md_path: Path, tool_name: str = ""
) -> bool:
    """Return True if the Edit/Write adds a net-new pin comment.

    The marker-gate fires only on ADD-shaped edits so the user can still
    ARCHIVE stale pins (reducing pin count) to resolve the condition.
    Archival edits (old_string contains `<!-- pinned:`, new_string does
    not, or count strictly decreases) and refactor edits (pin count
    unchanged) are allowed.

    THERE IS ONE COMPARISON AND IT IS OVER TWO DOCUMENTS. The tool name selects
    WHICH SIMULATION builds the post-edit document. It does not select a
    comparison. `_simulate_post_edit_document` builds the document the tool call
    will produce, and `_counts_show_an_add` then compares the current document
    against that one. Edit and Write differ in the simulation alone.
      - ADD: the post-edit document holds MORE pins than the current one. Deny.
      - ARCHIVE: it holds fewer. Allow.
      - REFACTOR: the count is equal. Allow. A pin body rewrite and a STALE
        marker injection are of this shape.

    AN EDIT FRAGMENT IS NOT THE OBJECT COMPARED, AND IT WAS ONCE. An earlier
    revision counted the replacement strings of an Edit against each other,
    which is a different kind of object from the whole-file content of a Write,
    so the two tools reached two different comparisons. Do not put a fragment
    count back here.

    Fail-open: any shape-detection error returns False (allow). This
    preserves the SACROSANCT gate invariant.
    """
    # WHY net-new detection: the gate exists to stop the user adding a 13th
    # pin while stale pins remain. Archival is the REMEDIATION the user is
    # directed to perform — denying it causes a same-session livelock
    # (reviewer-security F1). A substring match on `<!-- pinned:` is
    # symmetric across add and archive shapes, so it cannot distinguish
    # them. A strict count increase is asymmetric by construction: ADD
    # raises the count, ARCHIVE lowers it, REFACTOR leaves it unchanged.
    try:
        # RULE 4, THE TOOL NAME. The caller knows the tool, because it checked
        # it against `_GATED_TOOLS`, so this branch asks the tool rather than
        # guessing from a payload key. A non-Write tool carrying a `content`
        # key took the whole-document branch before this.
        #
        # THE TWO PATHS NOW ASK ONE QUESTION OF ONE KIND OF OBJECT. The tool
        # name selects WHICH SIMULATION runs, and it no longer selects which
        # COMPARISON runs, because there is one comparison. See
        # `_simulate_post_edit_document`.
        try:
            # Read-only: a byte that is not UTF-8 decodes to U+FFFD.
            current = claude_md_path.read_text(encoding="utf-8", errors="replace")
        except (IOError, OSError):
            # Cannot read the pre-state, so there is nothing to compare
            # against. Fail-open.
            return False

        simulated = _simulate_post_edit_document(tool_input, current, tool_name)
        if simulated is None:
            # The payload cannot be read as an edit. Fail-open.
            return False

        return _counts_show_an_add(current, simulated)
    except Exception as exc:  # noqa: BLE001 — SACROSANCT fail-open
        # THIS IS THE CATCH THAT SWALLOWED A NameError AND KILLED THE WHOLE
        # EDIT PATH IN SILENCE. It keeps its direction and loses its silence.
        _report_fail_open("add-shape detection", exc)
        return False


def _check_tool_allowed(input_data: dict) -> str | None:
    """Determine whether the tool call should be denied.

    Returns the deny reason string if blocked, or None to allow.
    """
    tool_name = input_data.get("tool_name", "")
    if tool_name not in _GATED_TOOLS:
        return None

    pact_context.init(input_data)

    # Lead-role gate (#878, DENY-gate enforcement RESTORATION) — teammates
    # don't edit project CLAUDE.md (worktree scope rule), so this gate is
    # team-lead-only. Migrated from the negative `resolve_agent_name(...) != ""`
    # heuristic — which returned non-empty for BOTH lead spellings, so the lead
    # itself took this bypass branch and the DENY gate was silently DEAD for
    # the lead. is_lead keys on the harness-set agent_type directly; it is total
    # (never raises), preserving the caller's existing exception posture — which
    # for this gate is fail-OPEN (the SACROSANCT default: any exception in gate
    # logic allows the edit). A raising predicate would have perturbed that.
    if not pact_context.is_lead(input_data):
        return None

    session_dir = pact_context.get_session_dir()
    if not session_dir:
        return None

    marker_path = Path(session_dir) / PIN_STALENESS_MARKER_NAME
    if not marker_path.exists():
        return None

    tool_input = input_data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return None

    file_path_str = tool_input.get("file_path", "")
    claude_md_path = match_project_claude_md(file_path_str)
    if claude_md_path is None:
        return None

    # Narrow matcher: block only ADD-shaped edits (net-new pin comment).
    # Archival edits (pin removal) and refactor edits (pin body rewrite)
    # are allowed so the user can resolve the stale-pins condition by
    # running /PACT:pin-memory within the same session. Fix for #492
    # F1 marker livelock.
    if not _is_add_shaped_edit(tool_input, claude_md_path, tool_name):
        return None

    return _DENY_REASON


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    try:
        deny_reason = _check_tool_allowed(input_data)
    except Exception as exc:
        # SACROSANCT: any exception in gate logic gives fail-open, AND IT
        # REPORTS. This is the outermost of the three broad catches.
        _report_fail_open("gate decision", exc)
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    if deny_reason:
        # hookEventName is required by the harness; missing it silently fails open
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": deny_reason,
            }
        }
        print(json.dumps(output))
        sys.exit(2)

    print(_SUPPRESS_OUTPUT)
    sys.exit(0)


if __name__ == "__main__":
    main()
