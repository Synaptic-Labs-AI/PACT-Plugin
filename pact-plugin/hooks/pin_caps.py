"""
Pin Caps Enforcement Module

Location: pact-plugin/hooks/pin_caps.py

Summary: Parses the Pinned Context section of CLAUDE.md and enforces
per-session caps (count, per-pin size, stale-block threshold). Pure
helpers — no I/O, no side effects. Invoked by three consumers:
  - scripts/check_pin_caps.py: CLI for /PACT:pin-memory add-time enforcement
  - staleness.py: SessionStart stale-block signal emission
  - session_init.py: slot-count + stale-block directive surfacing

Owns the cap-enforcement constants (semantic-owner convention, sibling to
staleness.py's PINNED_STALENESS_DAYS / PINNED_CONTEXT_TOKEN_BUDGET).

Twin copy of the three public constants exists in
skills/pact-memory/scripts/working_memory.py (skill-to-hooks import
barrier); a drift-detection test in test_staleness.py guards against
divergence.
"""

import re
from typing import List, Literal, NamedTuple, Optional

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

# Strict regex for the combined pin date + size-override comment.
# Live form (CLAUDE.md:69):
#   <!-- pinned: 2026-04-11, pin-size-override: verbatim dispatch form... -->
# Capture group 1 = rationale text — matches any character EXCEPT a run
# that terminates the HTML comment (`-->`). Reluctant `.+?` was sufficient
# under .fullmatch() but is vulnerable to future .search()/.match() misuse
# by a downstream consumer. Self-anchoring via \A...\Z + a rationale
# pattern that positively refuses to consume `-->` closes both a
# latent-misuse vector (Sec-M2) and call-convention drift (Sec-F5).
#
# Rationale pattern: `(?:[^-]|-(?!->))+` — one or more chars that are
# either not `-` at all, or `-` NOT followed by `->`. Equivalent to "any
# char except the `-->` terminator" without resorting to reluctant
# backtracking.
OVERRIDE_COMMENT_RE = re.compile(
    r'\A<!--\s*pinned:\s*[^,]+,\s*pin-size-override:\s*((?:[^-]|-(?!->))+?)\s*-->\Z',
    re.IGNORECASE,
)

# Standalone <!-- pinned: YYYY-MM-DD[, ...] --> comment without override.
_DATE_COMMENT_RE = re.compile(
    r'<!--\s*pinned:\s*[^>]+?-->',
    re.IGNORECASE,
)

# <!-- STALE: Last relevant YYYY-MM-DD --> marker — excluded from body_chars.
_STALE_MARKER_RE = re.compile(
    r'<!--\s*STALE:\s*Last relevant\s+\d{4}-\d{2}-\d{2}\s*-->',
    re.IGNORECASE,
)

# Pin heading anchor — "### " at start of line.
_PIN_HEADING_RE = re.compile(r'^### ', re.MULTILINE)

# Sec-F5b: Unicode line terminators that must not survive inside an
# override rationale. Stripped via str.translate in parse_pins.
# U+2028 LINE SEPARATOR, U+2029 PARAGRAPH SEPARATOR, U+0085 NEXT LINE,
# U+000D CARRIAGE RETURN — any of these can span logical lines in some
# renderers, enabling prompt-injection or comment-boundary spoofing.
_FORBIDDEN_TERMINATOR_TABLE = str.maketrans("", "", "\u2028\u2029\u0085\r")


class Pin(NamedTuple):
    """A single pinned entry with its boundaries and override state."""

    heading: str                     # "### Entry Title"
    body: str                        # entry body (after heading line)
    body_chars: int                  # len(body) excluding date-comment + STALE marker
    date_comment: Optional[str]      # "<!-- pinned: YYYY-MM-DD[, ...] -->" preceding heading
    override_rationale: Optional[str]  # captured rationale; None if no override
    is_stale: bool                   # whether a STALE marker is present


class CapViolation(NamedTuple):
    """A cap-enforcement refusal result."""

    kind: Literal["count", "size", "stale"]
    detail: str
    offending_pin_chars: Optional[int]
    current_count: Optional[int]


def _extract_body_chars(body: str) -> int:
    """Count body chars excluding auto-generated markers.

    The date comment and STALE marker are plugin-managed — they MUST NOT
    count against the user's 1500-char budget. Prevents self-referential
    inflation from override rationale + tool-generated markers.
    """
    stripped = _DATE_COMMENT_RE.sub("", body)
    stripped = _STALE_MARKER_RE.sub("", stripped)
    return len(stripped.strip())


def parse_pins(pinned_content: str) -> List[Pin]:
    """Parse the Pinned Context section body into a list of Pin entries.

    Fail-open: on any regex/structural anomaly, returns whatever pins
    could be parsed cleanly. Never raises — caller-observable behavior is
    degradation, not exception.

    The pinned_content input MUST be the body AFTER the "## Pinned
    Context\\n" heading (i.e., what _parse_pinned_section returns in its
    third tuple slot). Managed-region bounding is the caller's
    responsibility (#404 round-10 invariant).
    """
    if not pinned_content:
        return []

    try:
        heading_starts = [m.start() for m in _PIN_HEADING_RE.finditer(pinned_content)]
    except re.error:
        return []

    if not heading_starts:
        return []

    pins: List[Pin] = []

    for i, start in enumerate(heading_starts):
        end = heading_starts[i + 1] if i + 1 < len(heading_starts) else len(pinned_content)
        entry_text = pinned_content[start:end]

        nl_pos = entry_text.find("\n")
        if nl_pos == -1:
            heading = entry_text
            body = ""
        else:
            heading = entry_text[:nl_pos]
            body = entry_text[nl_pos + 1:]

        # Walk backward from the heading to find the preceding comment line
        # (date comment, possibly with override). Between heading and the
        # prior pin's body, only a date comment may appear — other content
        # terminates the search.
        preceding = pinned_content[:start]
        date_comment: Optional[str] = None
        override_rationale: Optional[str] = None

        # Scan prior non-empty line(s) for an <!-- pinned: ... --> comment.
        prior_lines = preceding.rstrip("\n").split("\n")
        # Walk backward over blank lines then inspect first non-blank.
        idx = len(prior_lines) - 1
        while idx >= 0 and not prior_lines[idx].strip():
            idx -= 1
        if idx >= 0:
            candidate = prior_lines[idx].strip()
            # Match override first (more specific), then fall back to plain
            # date comment. Multi-override: first wins — override captured
            # only from the line IMMEDIATELY preceding the heading.
            override_match = OVERRIDE_COMMENT_RE.fullmatch(candidate)
            if override_match:
                date_comment = candidate
                rationale = override_match.group(1).strip()
                # Sec-F5b: strip Unicode line terminators (U+2028 LINE
                # SEPARATOR, U+2029 PARAGRAPH SEPARATOR, U+0085 NEXT LINE,
                # \r CARRIAGE RETURN) from the rationale before accepting
                # it. These span logical lines in some renderers and are
                # latent prompt-injection / comment-boundary-spoofing risks.
                rationale = rationale.translate(_FORBIDDEN_TERMINATOR_TABLE)
                # Strict parser: empty rationale or > max → treat as no-override.
                if rationale and len(rationale) <= OVERRIDE_RATIONALE_MAX:
                    override_rationale = rationale
            elif _DATE_COMMENT_RE.fullmatch(candidate):
                date_comment = candidate

        is_stale = bool(_STALE_MARKER_RE.search(body))
        body_chars = _extract_body_chars(body)

        pins.append(Pin(
            heading=heading,
            body=body,
            body_chars=body_chars,
            date_comment=date_comment,
            override_rationale=override_rationale,
            is_stale=is_stale,
        ))

    return pins


def has_size_override(pin: Pin) -> bool:
    """Return True if this pin carries a valid pin-size-override rationale."""
    return pin.override_rationale is not None


def check_add_allowed(
    existing: List[Pin],
    new_body: str,
    new_has_override: bool,
) -> Optional[CapViolation]:
    """Check whether a new pin may be added given current state.

    Returns None if the add is allowed; a CapViolation otherwise. Count
    cap is strict (predicate: `len(existing) >= PIN_COUNT_CAP`). Size cap
    is strict unless new_has_override is True (override grants unlimited
    size per curator discretion — user decision 2026-04-20, no sub-cap).

    Args:
        existing: Current parsed pins.
        new_body: Body text of the proposed new pin (the text that would
            follow the heading). Counted via _extract_body_chars so
            date-comment + STALE markers do not inflate.
        new_has_override: Whether the proposed pin carries a valid
            override rationale. Caller is responsible for validating
            rationale shape (via OVERRIDE_COMMENT_RE) before passing True.
    """
    current_count = len(existing)

    if current_count >= PIN_COUNT_CAP:
        return CapViolation(
            kind="count",
            detail=(
                f"pin count cap reached ({current_count}/{PIN_COUNT_CAP}); "
                f"evict a pin before adding"
            ),
            offending_pin_chars=None,
            current_count=current_count,
        )

    new_chars = _extract_body_chars(new_body)
    if new_chars > PIN_SIZE_CAP and not new_has_override:
        return CapViolation(
            kind="size",
            detail=(
                f"new pin body is {new_chars} chars (cap: {PIN_SIZE_CAP}); "
                f"compress or add pin-size-override rationale"
            ),
            offending_pin_chars=new_chars,
            current_count=current_count,
        )

    return None


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
        return CapViolation(
            kind="stale",
            detail=(
                f"{stale_count} stale pin(s) detected (threshold: {threshold}); "
                f"run /PACT:pin-memory review"
            ),
            offending_pin_chars=None,
            current_count=len(pins),
        )
    return None


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
