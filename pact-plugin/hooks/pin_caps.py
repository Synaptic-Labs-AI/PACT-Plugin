"""
Pin Caps Enforcement Module

Location: pact-plugin/hooks/pin_caps.py

Summary: Parses the Pinned Context section of CLAUDE.md and enforces
per-session caps (count, per-pin size, stale-block threshold). Pure
helpers — no I/O, no side effects. Invoked by three consumers:
  - scripts/check_pin_caps.py: advisory slot-status CLI for the pin commands
  - staleness.py: SessionStart stale-block signal emission
  - session_init.py: slot-count + stale-block directive surfacing

Owns the cap-enforcement constants (semantic-owner convention, sibling to
staleness.py's PINNED_STALENESS_DAYS / PINNED_CONTEXT_TOKEN_BUDGET).

Twin copy of the three public constants exists in
skills/pact-memory/scripts/working_memory.py (skill-to-hooks import
barrier); a drift-detection test in test_staleness.py guards against
divergence.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List, Literal, NamedTuple, Optional, Tuple

# Hard cap on total pin count. Enforcement predicate is `len(existing) >= 12
# → refuse add` (off-by-one hazard per plan risk row 1).
PIN_COUNT_CAP = 12

# Hard cap on per-pin body character count. Body excludes the
# <!-- pinned: ... --> date comment and any <!-- STALE: ... --> marker.
# Override comment extends body grace (see has_size_override).
PIN_SIZE_CAP = 1500

# Number of stale pins that triggers the SessionStart stale-block
# directive. At or above threshold, curation is overdue.
PIN_STALE_BLOCK_THRESHOLD = 2

# Maximum length of the pin-size-override rationale (chars). Prevents
# rationale from itself becoming a back-channel for oversized pins.
OVERRIDE_RATIONALE_MAX = 120

# Single source for the pin-comment grammar. The strike pattern
# (`_DATE_COMMENT_RE`), the attribution row pattern (`_DATE_COMMENT_ROW`) and
# the override reader's patterns are built from these four fragments, so the
# strip path and the attribution path cannot drift apart on the shape of a
# comment.
#
# `_COMMENT_CHAR` is one character of a comment interior: either a character
# that is not `-`, or a `-` that does not start `-->`. A run of this class
# cannot contain `-->`, so no match can cross the end of a closed comment.
# This is the positive terminator refusal the override pattern already used.
# It replaces the older `[^>]` class, which refused EVERY `>` and so made the
# strip path blind to a comment that held one, while attribution still saw it.
#
# `_COMMENT_CHAR_NO_COMMA` is the same rule for the date field, which the
# comma delimits. It refuses the terminator too, so attribution cannot accept
# a line whose override clause sits after an early `-->`.
#
# The property these fragments deliver is DOMINANCE: every row that
# attribution accepts, the strip removes in full. The strip may remove more.
# It must never remove less, because less is a charge against the neighbour.
_COMMENT_CHAR = r'(?:[^-]|-(?!->))'
_COMMENT_CHAR_NO_COMMA = r'(?:[^-,]|-(?!->))'
_PIN_COMMENT_OPEN = r'<!--\s*pinned:\s*'
_PIN_COMMENT_CLOSE = r'-->'

# Standalone <!-- pinned: YYYY-MM-DD[, ...] --> comment without override.
# Unanchored BY DESIGN: `_charge` strikes it with `.sub` wherever it sits on a
# row, so it cannot be anchored. Terminator refusal, not anchoring, is what
# keeps it safe under every call convention (Sec-M2).
_DATE_COMMENT_RE = re.compile(
    rf'{_PIN_COMMENT_OPEN}{_COMMENT_CHAR}+?{_PIN_COMMENT_CLOSE}',
    re.IGNORECASE,
)

# <!-- STALE: Last relevant YYYY-MM-DD --> marker — excluded from body_chars.
_STALE_MARKER_RE = re.compile(
    r'<!--\s*STALE:\s*Last relevant\s+\d{4}-\d{2}-\d{2}\s*-->',
    re.IGNORECASE,
)

# The two plugin-managed comment kinds a body is not charged for, struck from a
# row in one pass.
_MANAGED_COMMENT_RE = re.compile(
    f"{_DATE_COMMENT_RE.pattern}|{_STALE_MARKER_RE.pattern}", re.IGNORECASE
)

# Row patterns. The fence-aware parser (`shared.claude_md_markers`) splits a
# text into rows, and these match ONE row's content, which never holds its line
# terminator, through `Document.find_lines`, which reads PROSE rows only. So a
# `### ` line or a pin comment inside a fenced code block is pin text, not
# structure.
#
# A pin heading: `### ` at column 0.
_PIN_HEADING_ROW = re.compile(r'### ')
# A pin comment alone on its row. The `\s*` either side is the tolerance
# attribution has always applied (it compared the stripped line).
_DATE_COMMENT_ROW = re.compile(
    rf'\s*{_PIN_COMMENT_OPEN}{_COMMENT_CHAR}+?{_PIN_COMMENT_CLOSE}\s*\Z',
    re.IGNORECASE,
)
# A reconfirmation, wherever it sits in a pin comment. `check_pin_caps` dates
# a pin from it and the override reader keeps it out of the rationale, so the
# two read one shape.
RECONFIRMED_DATE_RE = re.compile(r'reconfirmed:\s*(\d{4}-\d{2}-\d{2})', re.IGNORECASE)
# What may separate a reconfirmation from the text before it.
_RECONFIRM_SEPARATORS = " \t\u00a0,;(-\u2013\u2014"
# A combined date and size-override comment. The override field starts at the
# comment's first `, pin-size-override:`; before it sits the comma-free date,
# alone or followed by a reconfirmation (a comma may lead that). The rationale
# runs from the field name to the closing `-->`, less a reconfirmation written
# after it, so either placement keeps the override:
#   <!-- pinned: 2026-04-11, pin-size-override: verbatim dispatch form... -->
#   <!-- pinned: 2026-04-11; reconfirmed: 2026-07-25 because R, pin-size-override: O -->
#   <!-- pinned: 2026-04-11, pin-size-override: O (reconfirmed: 2026-07-25 because R) -->
_PIN_COMMENT_START = re.compile(rf'\s*{_PIN_COMMENT_OPEN}', re.IGNORECASE)
_OVERRIDE_FIELD = re.compile(r',\s*pin-size-override:', re.IGNORECASE)
_OVERRIDE_DATE_PART = re.compile(
    rf'{_COMMENT_CHAR_NO_COMMA}+?(?:,\s*)?{RECONFIRMED_DATE_RE.pattern}.*'
    rf'|{_COMMENT_CHAR_NO_COMMA}+',
    re.IGNORECASE | re.DOTALL,
)
# A row holding a STALE marker anywhere, which is where `is_stale` has always
# looked for one.
_STALE_MARKER_ANYWHERE_ROW = re.compile(rf'.*?{_STALE_MARKER_RE.pattern}', re.IGNORECASE)

# Characters `str.splitlines()` breaks a line at that the parser does not. A
# candidate comment row holding one is not attributed. Attribution used to split
# lines with `splitlines()`, so such a row was never one comment, and an
# override rationale carrying \v, \f or FS/GS/RS would pass the translate
# below untouched.
_SPLITLINES_ONLY_BREAKS = "\v\f\x1c\x1d\x1e\x85\u2028\u2029"

# Sec-F5b / cycle-7: Line terminators that must not survive inside an
# override rationale. Stripped via str.translate in `_override_rationale`.
# U+2028 LINE SEPARATOR, U+2029 PARAGRAPH SEPARATOR, U+0085 NEXT LINE,
# U+000D CARRIAGE RETURN, U+000A LINE FEED (ASCII newline) — any of
# these can span logical lines in some renderers or split a
# single-line HTML comment across multiple lines, enabling
# prompt-injection or comment-boundary spoofing. ASCII newline was
# the Sec residual added in cycle-7: the original table covered
# Unicode variants but missed the most common terminator.
#
# This table is narrower than `str.splitlines()`, which also breaks at \v
# (U+000B), \f (U+000C) and FS/GS/RS (U+001C/U+001D/U+001E). The parser splits
# rows at \r\n, \r and \n only, so `_date_comment_row` closes the gap before
# this translate runs: a candidate row holding any `_SPLITLINES_ONLY_BREAKS`
# character is not attributed. The translate is defense-in-depth behind that
# refusal; widen this table before relaxing it.
_FORBIDDEN_TERMINATOR_TABLE = str.maketrans("", "", "\u2028\u2029\u0085\r\n")


class Pin(NamedTuple):
    """A single pinned entry with its boundaries and override state."""

    heading: str                     # "### Entry Title"
    body: str                        # entry body (after heading line)
    body_chars: int                  # len(body) excluding date-comment + STALE marker
    date_comment: Optional[str]      # "<!-- pinned: YYYY-MM-DD[, ...] -->" preceding heading
    override_rationale: Optional[str]  # captured rationale; None if no override
    is_stale: bool                   # whether a STALE marker is present
    # The body's non-blank rows as the size cap charges them: pin comments
    # struck on prose rows, trailing blanks dropped. The per-pin size rule
    # reads which text a pin holds from them.
    lines: Tuple[str, ...] = ()


class CapViolation(NamedTuple):
    """A cap-enforcement refusal result."""

    kind: Literal["count", "size", "stale", "empty", "invalid_override"]
    detail: str
    offending_pin_chars: Optional[int]
    current_count: Optional[int]


def _charge(doc, first: int, last: int) -> int:
    """Characters rows `first`..`last` of `doc` charge against the size cap.

    On a PROSE row, the pin comments and STALE markers are plugin-managed and
    cost nothing: they are struck from the row wherever they sit, a comment
    sharing its row with prose included. A comment that spans two rows is not
    struck (attribution never treats it as a comment either). Every other row
    is charged in full: a CODE row, so a comment-shaped line inside a fenced
    block is user text like the rest of the snippet, and an UNKNOWN row, which
    then charges at least what the same row charges once it is known.

    Whitespace that changes nothing a reader sees is never charged: each row
    loses its trailing spaces and tabs, and each line break counts as one
    character whatever its terminator, so trailing blanks or a CRLF rewrite
    cannot push a pin past the cap. The charge is a function of the rows alone,
    so an unchanged pin is charged the same before and after any edit.

    The strike reads a row only up to its last `-->`: no comment either pattern
    strikes can end past it, so the result is the same, and a run of comment
    openers with no close after them is not rescanned from each opener.
    """
    return len("\n".join(_charged_rows(doc, first, last)).strip())


def _charged_rows(doc, first: int, last: int) -> List[str]:
    """Rows `first`..`last` of `doc` as `_charge` counts them."""
    from shared.claude_md_markers import Kind

    kept = []
    for line in doc.lines[first:last + 1]:
        content = line.content
        close = content.rfind(_PIN_COMMENT_CLOSE)
        if line.kind is Kind.PROSE and close >= 0:
            pieces, pos = [], 0
            for match in _MANAGED_COMMENT_RE.finditer(line.content, 0, close + len(_PIN_COMMENT_CLOSE)):
                pieces.append(content[pos:match.start()])
                pos = match.end()
            content = "".join(pieces) + content[pos:]
        kept.append(content.rstrip(" \t"))
    return kept


def _date_comment_row(doc, floor: int, heading: int) -> Optional[int]:
    """The row of the pin comment attributed to the pin headed at `heading`.

    Walk up from the heading over blank rows, no higher than `floor`; the
    first other row is the pin's comment when it is a PROSE row holding only
    a pin comment. A fenced comment-shaped line is not one, and neither is a
    row holding a character `str.splitlines()` would break at
    (`_SPLITLINES_ONLY_BREAKS`). None when there is no comment.
    """
    row = heading - 1
    while row >= floor and not doc.lines[row].content.strip():
        row -= 1
    if row < floor:
        return None
    content = doc.lines[row].content
    if any(char in content for char in _SPLITLINES_ONLY_BREAKS):
        return None
    # An override comment is a date comment whose text starts with the
    # override head (`override_rationale_text`), so this attributes both.
    if doc.find_lines(_DATE_COMMENT_ROW, (row, row)):
        return row
    return None


def override_rationale_text(doc, row: int) -> Optional[str]:
    """The rationale field of the override comment on row `row` of `doc`,
    stripped, before any validity check; None when that row is not a PROSE
    row holding only an override comment.

    The row is one closed pin comment with an override field: its first
    `, pin-size-override:`, with only the comma-free date, or the date and a
    reconfirmation, before it. The rationale is the text from the field name
    to the closing `-->`, less a reconfirmation written after it
    (`RECONFIRMED_DATE_RE`, then the rest) and the separator before that. The
    pin-cap gate validates it; `_override_rationale` decides with it.
    """
    from shared.claude_md_markers import Kind

    line = doc.lines[row]
    start = field = None
    if line.kind is Kind.PROSE:
        if _DATE_COMMENT_ROW.match(line.content):
            start = _PIN_COMMENT_START.match(line.content)
            field = _OVERRIDE_FIELD.search(line.content, start.end()) if start else None
    if start is None or field is None:
        return None
    if not _OVERRIDE_DATE_PART.fullmatch(line.content, start.end(), field.start()):
        return None
    rationale = line.content.rstrip()[field.end():-len(_PIN_COMMENT_CLOSE)]
    reconfirm = RECONFIRMED_DATE_RE.search(rationale)
    if reconfirm is not None:
        rationale = rationale[:reconfirm.start()].rstrip(_RECONFIRM_SEPARATORS)
    return rationale.strip()


def _override_rationale(doc, row: int) -> Optional[str]:
    """The valid rationale of the override comment on `row`, or None."""
    rationale = override_rationale_text(doc, row)
    if rationale is None:
        return None
    # Defense-in-depth: `_date_comment_row` has already refused every row
    # holding a line break this table lists.
    rationale = rationale.translate(_FORBIDDEN_TERMINATOR_TABLE)
    # Strict parser: empty rationale or > max → treat as no-override.
    if rationale and len(rationale) <= OVERRIDE_RATIONALE_MAX:
        return rationale
    return None


def pins_in_rows(doc, first: int, last: int) -> List[Pin]:
    """The pins whose heading rows lie in rows `first`..`last` of `doc`.

    A heading is a PROSE row starting `### `; a `### ` line inside a fenced
    block belongs to the body of the pin above it. A body runs to the row
    before the next heading, or to `last`, and is sliced from the original
    text, so it keeps its line terminators. A pin's comment is looked for no
    higher than `first`.
    """
    if first > last:
        return []
    headings = doc.find_lines(_PIN_HEADING_ROW, (first, last))
    pins: List[Pin] = []
    for index, heading in enumerate(headings):
        body_last = headings[index + 1] - 1 if index + 1 < len(headings) else last
        body_start = doc.lines[heading].end
        body_end = doc.lines[body_last].end
        comment_row = _date_comment_row(doc, first, heading)
        date_comment = None
        override_rationale = None
        if comment_row is not None:
            date_comment = doc.lines[comment_row].content.strip()
            override_rationale = _override_rationale(doc, comment_row)
        body_rows = (heading + 1, body_last)
        rows = _charged_rows(doc, heading + 1, body_last)
        pins.append(Pin(
            heading=doc.lines[heading].content,
            body=doc.text[body_start:body_end],
            body_chars=len("\n".join(rows).strip()),
            date_comment=date_comment,
            override_rationale=override_rationale,
            is_stale=bool(doc.find_lines(_STALE_MARKER_ANYWHERE_ROW, body_rows)),
            lines=tuple(row for row in rows if row.strip()),
        ))
    return pins


def section_pins(doc, located) -> List[Pin]:
    """The pins of a FOUND Pinned section, read from `doc`'s own rows.

    `located` is a FOUND result whose span is (heading row, last body row),
    as `staleness.locate_pinned` returns it; the pins are read from the rows
    after the heading. ValueError for any other state.
    """
    from shared.claude_md_markers import State

    if located.state is not State.FOUND:
        raise ValueError(f"section_pins needs a FOUND section, not {located.state.value}")
    heading, last = located.spans[0]
    return pins_in_rows(doc, heading + 1, last)


def has_size_override(pin: Pin) -> bool:
    """Return True if this pin carries a valid pin-size-override rationale."""
    return pin.override_rationale is not None


def check_stale_block(
    pins: List[Pin],
    threshold: int = PIN_STALE_BLOCK_THRESHOLD,
) -> Optional[CapViolation]:
    """Return a CapViolation describing stale overflow, or None.

    Fires when stale pin count is >= threshold. Downstream consumers
    surface this as an unconditional SessionStart directive (not an
    exit-2 — per plan row 6, exit-2 breaks /clear and /resume).
    """
    stale_count = sum(1 for p in pins if p.is_stale)
    if stale_count >= threshold:
        # STATE THE CONDITION HERE AND DO NOT NAME A COMMAND. The consumer
        # that shows this detail to a user appends the command that archives,
        # so a command named here becomes a second instruction that can
        # disagree with it. This text named the command that ADDS a pin, which
        # cannot clear a stale pin, and a user read that one first.
        return CapViolation(
            kind="stale",
            detail=f"{stale_count} stale pin(s) detected (threshold: {threshold})",
            offending_pin_chars=None,
            current_count=len(pins),
        )
    return None


# ---------------------------------------------------------------------------
# Hook-primary cap enforcement helpers (cycle-8).
#
# Post-state predicates used by the PreToolUse gate (pin_caps_gate.py), shared
# with the advisory CLI (check_pin_caps.py) so deny-reason phrasing stays in
# one place (Risk R9 — phrasing drift). They read the pin list after the
# change, so the count predicate is `>` (strict): a state at the cap is not a
# violation, only a pin past it is.
# ---------------------------------------------------------------------------


# Shared deny-reason templates. Plain instructional text aimed at the curator
# (the LLM driving Edit/Write). Rendered verbatim into permissionDecisionReason
# so the curator sees the next-step action.
# DEMOTE-NOT-DELETE framing. The curator meeting this deny is at the cap and
# is being told to remove something they previously judged worth keeping, so
# the wording has to answer "what happens to it?" before they decide. Naming
# the destination (long-term memory) rather than the act (evict) is the whole
# point: /PACT:prune-memory archives the pin's content to pact-memory and
# verifies it arrived BEFORE removing it, so the content survives the removal.
# "Evict" described only the deletion half and read as loss.
DENY_REASON_COUNT = (
    "Pin count cap reached ({count}/{cap}). "
    "Run /PACT:prune-memory to demote a pin to long-term memory before "
    "adding — demotion archives the pin to pact-memory first, so the "
    "content is preserved rather than lost."
)

DENY_REASON_SIZE = (
    "New pin body is {chars} chars (cap: {cap}). "
    "Compress the body, or add a pin-size-override rationale "
    "if the content is verbatim load-bearing."
)

DENY_REASON_OVERRIDE_MISSING = (
    "New pin exceeds the size cap ({chars} > {cap}) and carries no valid "
    "pin-size-override rationale. Add a rationale or compress the body."
)


def evaluate_full_state(pins: List[Pin]) -> Optional[CapViolation]:
    """Check cap violations on a parsed post-edit pin list.

    POST-state predicate: `>` (strict), not `>=`. A state at the cap
    exactly (e.g. 12/12) is NOT a violation here — only a strict
    overshoot is, so only the 13th+ slot counts. `compute_deny_reason`
    layers a net-worse predicate on top of this to prevent pre-malformed
    livelock.

    Checks, in order of precedence:
      1. count:   len(pins) > PIN_COUNT_CAP
      2. size:    any pin has body_chars > PIN_SIZE_CAP AND no valid override

    Embedded-pin smuggle is not re-checked here — by the time `pins`
    exists, `pins_in_rows` has already visited the structure; the bypass
    either inflated count (caught by 1) or is benign.

    Returns None when no violation, otherwise the first violation found.
    """
    count = len(pins)
    if count > PIN_COUNT_CAP:
        return CapViolation(
            kind="count",
            detail=(
                f"post-edit pin count {count} exceeds cap {PIN_COUNT_CAP}"
            ),
            offending_pin_chars=None,
            current_count=count,
        )

    # Return the LARGEST violator, not the first-by-list-order. The Pareto
    # net-worse predicate in `compute_deny_reason` compares
    # `offending_pin_chars` pre vs post; if this returned the first violator,
    # a curator could worsen any non-first violator silently while the
    # first-in-list stayed unchanged or improved (blind-backend-coder-2
    # #492 F5 PoC). Max-violator scalar makes the size axis a well-defined
    # scalar: "the worst offending body_chars currently present."
    worst: Optional[Pin] = None
    for pin in pins:
        if pin.body_chars > PIN_SIZE_CAP and not has_size_override(pin):
            if worst is None or pin.body_chars > worst.body_chars:
                worst = pin
    if worst is not None:
        return CapViolation(
            kind="size",
            detail=(
                f"pin '{worst.heading}' body is {worst.body_chars} chars "
                f"(cap: {PIN_SIZE_CAP})"
            ),
            offending_pin_chars=worst.body_chars,
            current_count=count,
        )

    return None


def _violation_for_kind(pins: List[Pin], kind: str) -> Optional[CapViolation]:
    """Return a violation of `kind` if one exists on `pins`, else None.

    Sibling of `evaluate_full_state` that skips the kind precedence used
    by the "first violation wins" shortcut. `evaluate_full_state` returns
    count before size when both fire, which is useful for rendering but
    hides multi-kind states from `compute_deny_reason`'s net-worse
    predicate. This helper lets the predicate ask "is post.kind ALSO
    present at pre-state?" without restructuring the primary return.

    Kinds this helper handles: `"count"`, `"size"`. Scope is defined by
    what the net-worse Pareto predicate needs on the comparison axes it
    knows about, NOT by post-parse derivability in general. Full
    `CapViolation.kind` enumeration and taxonomy:

    Post-parse-derivable from post_pins:
      - `"count"`   — handled here; derived from `len(pins)`.
      - `"size"`    — handled here; derived from `pin.body_chars`
                      across all violators (max-scalar, per #492 F5).
      - `"stale"`   — derivable via `check_stale_block` (reads
                      `pin.is_stale`), but OUT OF SCOPE for this helper.
                      Stale overflow has its own predicate + session-
                      start surfacing path; the cap-compare pipeline
                      does not ingest it here.

    Reserved-no-emitter (declared in the `CapViolation.kind` Literal
    but no constructor anywhere in the codebase):
      - `"empty"`            — reserved for a future empty-pin predicate.
      - `"invalid_override"` — intent was to represent an invalid
                      override rationale, but the gate reports one with
                      its own formatted string (`"Pin cap violation
                      (invalid override): {reason}"`) without
                      constructing a CapViolation. The render branch in
                      `_render_deny_reason` remains but is unreachable
                      under the current emitter graph. If a future
                      refactor routes override failures through a
                      CapViolation, the render branch + Literal entry
                      become live simultaneously.

    For any kind outside {count, size}, returns None and the caller
    treats it as "not-present on this axis." When a future cap-axis is
    added to the Pareto pipeline, extend this function with the
    corresponding branch AND update `_pareto_other_axis_deny`'s
    comparison switch so both sites stay in lockstep.

    For the size branch, returns the LARGEST violator (max `body_chars`
    among violators), matching the scalar-max contract used by
    `evaluate_full_state`'s size branch. This keeps `compute_deny_reason`'s
    numeric comparison on `offending_pin_chars` pointing at "the worst
    violating body currently present" — blind-backend-coder-2 #492 F5.
    """
    if kind == "count":
        count = len(pins)
        if count > PIN_COUNT_CAP:
            return CapViolation(
                kind="count",
                detail=(
                    f"post-edit pin count {count} exceeds cap {PIN_COUNT_CAP}"
                ),
                offending_pin_chars=None,
                current_count=count,
            )
        return None

    if kind == "size":
        count = len(pins)
        worst: Optional[Pin] = None
        for pin in pins:
            if pin.body_chars > PIN_SIZE_CAP and not has_size_override(pin):
                if worst is None or pin.body_chars > worst.body_chars:
                    worst = pin
        if worst is not None:
            return CapViolation(
                kind="size",
                detail=(
                    f"pin '{worst.heading}' body is {worst.body_chars} chars "
                    f"(cap: {PIN_SIZE_CAP})"
                ),
                offending_pin_chars=worst.body_chars,
                current_count=count,
            )
        return None

    return None


def compute_deny_reason(
    pre_pins: List[Pin],
    post_pins: List[Pin],
    *,
    growth: Optional[int] = None,
) -> Optional[str]:
    """Net-worse deny predicate: return a rendered deny-reason or None.

    Compares `evaluate_full_state` on pre vs post. Denies ONLY when the
    post state is strictly worse than pre — i.e., a violation appears
    (or worsens) that didn't exist before. Pre-malformed state alone
    never denies: if the user already has 14 pins from a manual paste,
    every subsequent Edit would loop in deny (F1 livelock precedent).

    Rules (post-#492 cycle-3 Pareto semantics):
      - Pre OK, post OK                        -> allow (None).
      - Pre OK, post bad                       -> deny with the post-state
                                                  violation rendered.
      - Pre bad, post bad, first-wins kinds
        differ (F2 kind-swap)                  -> query `_violation_for_kind`
                                                  on pre_pins for post's kind.
                                                  If pre also has that kind,
                                                  normalize pre_violation to
                                                  the same-kind view and fall
                                                  through to the same-kind
                                                  rules below. Else post kind
                                                  is genuinely new -> deny.
      - Pre bad, post bad, same first-wins kind
        + post strictly worse on that axis     -> deny.
      - Pre bad, post bad, same first-wins kind
        + post NOT worse on that axis AND
        post strictly worse on the OTHER axis
        (F4 Pareto hidden-axis check)          -> deny via
                                                  `_pareto_other_axis_deny`.
      - Pre bad, post bad, NOT strictly worse
        on any axis                            -> allow.

    WITH `growth` GIVEN, the count axis is decided by the growth alone. The
    count is worse exactly when the post state is over the count cap AND
    `growth > 0`; `len(pre_pins)` is never read, so a caller may put extra
    pins in `pre_pins` to set the size axis's pre worst. The size axis is
    unchanged: worse when post has a size violation that pre lacks or that
    exceeds pre's worst. Strictly worse on either axis denies, the count
    reason first.

    There is no embedded-pin check here. A prose `### ` line smuggled into a
    body is a pin, which the growth counts, so it is denied on the count
    axis; a fenced one is not a pin.

    Args:
        pre_pins: Parsed pins from the pre-edit CLAUDE.md state.
        post_pins: Parsed pins from the simulated post-edit state.
        growth: Pins the change added to the Pinned section, from the
            pin-growth rule. None compares the two pin counts instead.

    Returns:
        Rendered deny-reason string if the edit should be denied, else None.
    """
    if growth is not None:
        return _growth_deny_reason(pre_pins, post_pins, growth)

    pre_violation = evaluate_full_state(pre_pins)
    post_violation = evaluate_full_state(post_pins)

    if post_violation is None:
        return None

    # Post has a violation. Decide whether it's strictly worse than pre.
    if pre_violation is None:
        # Pre clean, post bad → strictly worse; deny with templated reason.
        return _render_deny_reason(post_violation)

    # Both bad. Deny only if post is strictly worse than pre.
    #
    # Multi-kind-leak guard: `evaluate_full_state` returns first-violation
    # only (count before size). If pre-state already has BOTH count AND
    # size violations, `pre_violation.kind == "count"`. A legitimate
    # remediation Edit that reduces count below the cap surfaces the
    # pre-existing size violation → post_violation.kind == "size", which
    # would look like a kind-swap and falsely deny — locking the user
    # into the pre-malformed state (exactly the livelock the net-worse
    # predicate was designed to prevent, architect-1 #492 cycle-8 F2).
    #
    # Fix: when kinds differ, ask "did post.kind ALSO violate at pre-state?"
    # via `_violation_for_kind`. If yes, the two violations are comparable
    # on the same axis — fall through to numeric-overshoot comparison using
    # the pre-state violation OF THE SAME KIND. If no, the post violation
    # is genuinely new and denying is correct.
    if post_violation.kind != pre_violation.kind:
        pre_same_kind = _violation_for_kind(pre_pins, post_violation.kind)
        if pre_same_kind is None:
            # Genuinely new violation → strictly worse.
            return _render_deny_reason(post_violation)
        # Pre-state ALSO had this kind; compare numerically on this axis.
        pre_violation = pre_same_kind

    # Pareto net-worse predicate: deny when post is strictly worse on ANY
    # axis, not just the first-wins axis. `evaluate_full_state` returns
    # count before size (first-wins), so a post-state that keeps the
    # first-wins kind numerically-equal-or-better on its axis but worsens
    # the OTHER axis silently slips through without a second-axis check.
    # blind-backend-coder-2's #492 F4 PoC:
    #   pre  = 13 pins + Huge body 1550 (count wins, size hidden at 1550)
    #   post = 13 pins + Huge body 1700 (count same, size worsened to 1700)
    # Pre-F4 returned None (allow); Pareto requires deny on the worsened
    # size axis. Symmetric with the count case.
    #
    # Implementation: after the same-axis numeric check, before `return None`,
    # query `_violation_for_kind(pre_pins, OTHER)` / `(post_pins, OTHER)`.
    # Deny when post has an OTHER-axis violation that is strictly new
    # (pre OTHER = None) or strictly worse numerically. The render path
    # points at the worsened axis so the curator sees the right remediation.
    if post_violation.kind == "count":
        pre_count = pre_violation.current_count or 0
        post_count = post_violation.current_count or 0
        if post_count > pre_count:
            return _render_deny_reason(post_violation)
        # Pareto: count didn't worsen on its axis — check size axis.
        other_deny = _pareto_other_axis_deny(pre_pins, post_pins, other="size")
        if other_deny is not None:
            return other_deny
        return None

    if post_violation.kind == "size":
        pre_chars = pre_violation.offending_pin_chars or 0
        post_chars = post_violation.offending_pin_chars or 0
        if post_chars > pre_chars:
            return _render_deny_reason(post_violation)
        # Pareto: size didn't worsen on its axis — check count axis.
        other_deny = _pareto_other_axis_deny(pre_pins, post_pins, other="count")
        if other_deny is not None:
            return other_deny
        return None

    # Unknown kind — conservative: deny (safer than silent allow).
    return _render_deny_reason(post_violation)


# The per-pin size rule's share: a post pin joins a pre pin when it holds at
# least this fraction of its own word pairs from it, and a pre pin has a
# successor when a post pin holds this fraction of the pre pin's pairs. Lower
# admits more renamed rewrites as the same pin; higher sends more of them to
# orphan pairing.
_DESCENT_SHARE = 0.5


def _violates(pin: Pin) -> bool:
    return pin.body_chars > PIN_SIZE_CAP and not has_size_override(pin)


def _word_pairs(pin: Pin) -> Counter:
    """The pin's text as a multiset of consecutive word pairs, the words of
    all its lines run together; a pin of one word is that word alone."""
    words = " ".join(pin.lines).split()
    if len(words) < 2:
        return Counter(words)
    return Counter(f"{left} {right}" for left, right in zip(words, words[1:]))


def size_violation(pre_pins: List[Pin], post_pins: List[Pin]) -> Optional[str]:
    """The per-pin size rule: the deny text for the first pin over the size cap
    that the change made new or grew, or None. For a Pinned section located
    before the change.

    A pin violates when its charged body is over the cap with no valid
    override. Pins before and after the change are joined into components by
    the same heading (one to one, in order), by shared word pairs, and by text
    that moved between them; renames and moves stay the same pin. In a
    component that held a violator before, no violator after may be larger
    than its largest violator before, and the violators after may not add up
    to more than those before. A violator after in a component that held none
    before must take a violator before with no successor, at least as large,
    that sits in a component with no violator after.
    """
    bad = [q for q, pin in enumerate(post_pins) if _violates(pin)]
    if not bad:
        return None
    parent: Dict[tuple, tuple] = {}

    def find(node):
        while parent.get(node, node) != node:
            node = parent[node]
        return node

    def join(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[a] = b

    def normal(heading):
        return " ".join(heading.split()).casefold()

    edges, descended, partnered = [], set(), set()
    for q, post in enumerate(post_pins):
        for p, pre in enumerate(pre_pins):
            if p not in partnered and normal(pre.heading) == normal(post.heading):
                partnered.add(p)
                edges.append((p, q))
                descended.add(p)
                break
    pre_pairs = [_word_pairs(pin) for pin in pre_pins]
    post_pairs = [_word_pairs(pin) for pin in post_pins]
    for q, mine in enumerate(post_pairs):
        size = sum(mine.values())
        if not size:
            continue
        for p, theirs in enumerate(pre_pairs):
            shared = sum((mine & theirs).values())
            if shared >= _DESCENT_SHARE * size:
                edges.append((p, q))
            if sum(theirs.values()) and shared >= _DESCENT_SHARE * sum(theirs.values()):
                descended.add(p)
    for p, q in edges:
        join(("q", q), ("p", p))

    # Text moved between components joins them, so a paragraph moved from one
    # oversize pin into another leaves the pair one maximum and one sum.
    post_lines = [Counter(pin.lines) for pin in post_pins]
    kept_lines: Dict[tuple, Counter] = {}
    for q, lines in enumerate(post_lines):
        kept_lines.setdefault(find(("q", q)), Counter()).update(lines)
    links = []
    for p, pin in enumerate(pre_pins):
        root = find(("p", p))
        lost = Counter(pin.lines) - kept_lines.get(root, Counter())
        if lost:
            links.extend((p, q) for q, lines in enumerate(post_lines)
                         if find(("q", q)) != root and lines & lost)
    pairs_before: Dict[tuple, Counter] = {}
    pairs_after: Dict[tuple, Counter] = {}
    first_pre: Dict[tuple, int] = {}
    for p, pairs in enumerate(pre_pairs):
        root = find(("p", p))
        pairs_before.setdefault(root, Counter()).update(pairs)
        first_pre.setdefault(root, p)
    for q, pairs in enumerate(post_pairs):
        pairs_after.setdefault(find(("q", q)), Counter()).update(pairs)
    lost_pairs = {root: pairs - pairs_after.get(root, Counter()) for root, pairs in pairs_before.items()}
    for q, pairs in enumerate(post_pairs):
        root = find(("q", q))
        gained = pairs - pairs_before.get(root, Counter())
        count = sum(gained.values())
        if not count:
            continue
        for other, lost in lost_pairs.items():
            if other != root and lost and 2 * sum((gained & lost).values()) >= count:
                links.append((first_pre[other], q))
    for p, q in links:
        join(("q", q), ("p", p))

    components: Dict[tuple, tuple] = {}
    for p, pin in enumerate(pre_pins):
        if _violates(pin):
            components.setdefault(find(("p", p)), ([], []))[0].append(pin.body_chars)
    for q in bad:
        components.setdefault(find(("q", q)), ([], []))[1].append(post_pins[q].body_chars)
    orphans = []
    for before, after in components.values():
        if not after:
            continue
        if not before:
            orphans.extend(after)
        elif max(after) > max(before) or sum(after) > sum(before):
            return DENY_REASON_SIZE.format(chars=max(after), cap=PIN_SIZE_CAP)
    taken = {root for root, (_, after) in components.items() if after}
    free = sorted(pin.body_chars for p, pin in enumerate(pre_pins)
                  if _violates(pin) and p not in descended and find(("p", p)) not in taken)
    for chars in sorted(orphans, reverse=True):
        fit = next((size for size in free if size >= chars), None)
        if fit is None:
            return DENY_REASON_SIZE.format(chars=chars, cap=PIN_SIZE_CAP)
        free.remove(fit)
    return None


def _growth_deny_reason(
    pre_pins: List[Pin],
    post_pins: List[Pin],
    growth: int,
) -> Optional[str]:
    """`compute_deny_reason` with the count axis decided by `growth`."""
    post_count = _violation_for_kind(post_pins, "count")
    if post_count is not None and growth > 0:
        return _render_deny_reason(post_count)
    return _pareto_other_axis_deny(pre_pins, post_pins, other="size")


def _pareto_other_axis_deny(
    pre_pins: List[Pin],
    post_pins: List[Pin],
    other: str,
) -> Optional[str]:
    """Pareto other-axis check: return a deny-reason if post is strictly
    worse than pre on the `other` axis, else None.

    Helper for `compute_deny_reason`. Called after the first-wins axis has
    been shown not-worse; this secondary check looks at the OTHER axis to
    enforce Pareto semantics — strictly worse on ANY axis denies.

    Comparison rules:
      - pre has no violation on `other`, post does → deny (new violation).
      - Both violate `other`; post's numeric axis > pre's → deny.
      - Otherwise → None (not strictly worse on this axis).
    """
    post_other = _violation_for_kind(post_pins, other)
    if post_other is None:
        return None

    pre_other = _violation_for_kind(pre_pins, other)
    if pre_other is None:
        # Newly introduced violation on this axis → strictly worse.
        return _render_deny_reason(post_other)

    if other == "count":
        pre_n = pre_other.current_count or 0
        post_n = post_other.current_count or 0
        if post_n > pre_n:
            return _render_deny_reason(post_other)
        return None

    if other == "size":
        pre_n = pre_other.offending_pin_chars or 0
        post_n = post_other.offending_pin_chars or 0
        if post_n > pre_n:
            return _render_deny_reason(post_other)
        return None

    return None


def _render_deny_reason(violation: CapViolation) -> str:
    """Render a CapViolation into a curator-facing deny-reason string."""
    if violation.kind == "count":
        return DENY_REASON_COUNT.format(
            count=violation.current_count or 0,
            cap=PIN_COUNT_CAP,
        )
    if violation.kind == "size":
        chars = violation.offending_pin_chars or 0
        return DENY_REASON_SIZE.format(chars=chars, cap=PIN_SIZE_CAP)
    if violation.kind == "invalid_override":
        return DENY_REASON_OVERRIDE_MISSING.format(
            chars=violation.offending_pin_chars or 0,
            cap=PIN_SIZE_CAP,
        )
    # Fallback: surface the violation detail verbatim rather than drop it.
    return f"Pin cap violation: {violation.detail}"


def format_slot_status(pins: List[Pin]) -> str:
    """Format a concise slot-status string for additionalContext surfacing.

    Example outputs:
        "Pin slots: 11/12 used, 340 chars remaining on largest pin"
        "Pin slots: 12/12 used (FULL)"
        "Pin slots: 0/12 used"

    Largest-pin headroom is computed only when at least one pin exists.
    Fail-open: always returns a non-empty string suitable for pipe-joined
    additionalContext.
    """
    count = len(pins)
    if count == 0:
        return f"Pin slots: 0/{PIN_COUNT_CAP} used"

    if count >= PIN_COUNT_CAP:
        return f"Pin slots: {count}/{PIN_COUNT_CAP} used (FULL)"

    largest_chars = max(p.body_chars for p in pins)
    remaining = PIN_SIZE_CAP - largest_chars
    if remaining < 0:
        # Existing oversized pin (presumably override-carrying) — don't
        # mislead by reporting negative headroom.
        return f"Pin slots: {count}/{PIN_COUNT_CAP} used"
    return (
        f"Pin slots: {count}/{PIN_COUNT_CAP} used, "
        f"{remaining} chars remaining on largest pin"
    )
