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

# Sec-F5b / cycle-7: Line terminators that must not survive inside an
# override rationale. Stripped via str.translate in parse_pins.
# U+2028 LINE SEPARATOR, U+2029 PARAGRAPH SEPARATOR, U+0085 NEXT LINE,
# U+000D CARRIAGE RETURN, U+000A LINE FEED (ASCII newline) — any of
# these can span logical lines in some renderers or split a
# single-line HTML comment across multiple lines, enabling
# prompt-injection or comment-boundary spoofing. ASCII newline was
# the Sec residual added in cycle-7: the original table covered
# Unicode variants but missed the most common terminator.
_FORBIDDEN_TERMINATOR_TABLE = str.maketrans("", "", "\u2028\u2029\u0085\r\n")


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

    kind: Literal["count", "size", "stale", "embedded_pin", "empty", "invalid_override"]
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

    # Embedded-pin cap-bypass defense: a candidate body containing a
    # level-3 heading (`### `) would be counted as an additional pin by
    # parse_pins on reload, defeating the count cap. Detect by running
    # the candidate body through parse_pins directly — any non-empty
    # result means the body smuggles at least one pin structure (either
    # a full `<!-- pinned:...-->\n### Heading` pair OR a lone heading,
    # both of which parse_pins treats as a Pin on reload). Conservative
    # by design: curators can structure pin bodies with H4+ (`#### `)
    # or bold/italic instead of H3 — rejecting H3 in bodies closes the
    # smuggle vector regardless of whether a date-comment accompanies it.
    if parse_pins(new_body):
        return CapViolation(
            kind="embedded_pin",
            detail=(
                "candidate body contains an embedded pin structure "
                "(a `### ` heading); would smuggle past the count cap "
                "on reload. Use `#### ` or bold for in-body structure."
            ),
            offending_pin_chars=None,
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


# ---------------------------------------------------------------------------
# Hook-primary cap enforcement helpers (cycle-8).
#
# These extend pin_caps's pure-helper surface with post-state predicates used
# by the PreToolUse gate (pin_caps_gate.py). They are additive — nothing here
# changes existing `check_add_allowed` semantics. Shared between the gate and
# the advisory CLI (check_pin_caps.py) so deny-reason phrasing stays in one
# place (Risk R9 — phrasing drift).
#
# Key semantic differences vs. `check_add_allowed`:
#   - `>`  (strict), not `>=`  — this is a POST-state check, not a pre-add gate.
#   - No new_body param at predicate layer — the post-state pin list already
#     reflects any simulated add.
# ---------------------------------------------------------------------------


# Shared deny-reason templates. Plain instructional text aimed at the curator
# (the LLM driving Edit/Write). Rendered verbatim into permissionDecisionReason
# so the curator sees the next-step action.
DENY_REASON_COUNT = (
    "Pin count cap reached ({count}/{cap}). "
    "Run /PACT:prune-memory to evict an existing pin before adding."
)

DENY_REASON_SIZE = (
    "New pin body is {chars} chars (cap: {cap}). "
    "Compress the body, or add a pin-size-override rationale "
    "if the content is verbatim load-bearing."
)

DENY_REASON_EMBEDDED_PIN = (
    "Candidate body contains an embedded pin structure "
    "(a `### ` heading). On reload this would be counted as an extra pin "
    "and defeat the count cap. Use `#### ` or bold for in-body structure."
)

DENY_REASON_OVERRIDE_MISSING = (
    "New pin exceeds the size cap ({chars} > {cap}) and carries no valid "
    "pin-size-override rationale. Add a rationale or compress the body."
)


def evaluate_full_state(pins: List[Pin]) -> Optional[CapViolation]:
    """Check cap violations on a parsed post-edit pin list.

    POST-state predicate: `>` (strict), not `>=`. A state at the cap
    exactly (e.g. 12/12) is NOT a violation here — only a strict
    overshoot is. Compared to `check_add_allowed` which is pre-add
    (`>=` refuses the 12th add), `evaluate_full_state` refuses only the
    13th+ slot. The gate (pin_caps_gate.py) then layers a net-worse
    predicate on top of this to prevent pre-malformed livelock.

    Checks, in order of precedence:
      1. count:   len(pins) > PIN_COUNT_CAP
      2. size:    any pin has body_chars > PIN_SIZE_CAP AND no valid override

    Embedded-pin smuggle is not re-checked here — by the time `pins`
    exists, parse_pins has already visited the structure; the bypass
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

    for pin in pins:
        if pin.body_chars > PIN_SIZE_CAP and not has_size_override(pin):
            return CapViolation(
                kind="size",
                detail=(
                    f"pin '{pin.heading}' body is {pin.body_chars} chars "
                    f"(cap: {PIN_SIZE_CAP})"
                ),
                offending_pin_chars=pin.body_chars,
                current_count=count,
            )

    return None


def apply_edit_and_parse(current_content: str, tool_input: dict) -> List[Pin]:
    """Simulate the post-tool CLAUDE.md state and return parsed pins.

    For Edit:
      Applies `old_string → new_string` via `str.replace(...)`. When
      `replace_all` is true, Python's no-count str.replace matches the
      tool's actual apply behavior (PREPARE task #41 confirmed byte-
      identical). When `replace_all` is false, replaces only the first
      occurrence (`count=1`), matching the tool's single-match semantics.

    For Write:
      Uses `tool_input['content']` directly as the full new file content.
      `current_content` is ignored in that path — Write is a full-file
      replacement.

    After producing the simulated post-edit content, extracts the
    Pinned Context section via `_parse_pinned_section` and returns
    `parse_pins(pinned_content)`. Section-bounded by construction so
    `### ` headings elsewhere (Working Memory, user prose) do NOT
    inflate the count. If the post-edit content has no Pinned Context
    section, returns [] (no pins → below every cap).

    Raises on malformed tool_input (missing required keys, non-string
    values). The caller (pin_caps_gate.main) is responsible for wrapping
    the exception in the gate's outer fail-open. Embedding try/except
    inside this helper would hide input corruption from the gate, which
    needs to emit a failure_log entry on that path.
    """
    # Lazy import to avoid module-level coupling between pin_caps (pure
    # helpers) and staleness (has CLAUDE.md resolution logic). The
    # section-bounding contract lives in staleness._parse_pinned_section.
    from staleness import _parse_pinned_section

    if "content" in tool_input:
        # Write path — full-file replacement.
        new_content = tool_input["content"]
        if not isinstance(new_content, str):
            raise TypeError(
                f"Write tool_input.content must be str, got "
                f"{type(new_content).__name__}"
            )
        simulated = new_content
    else:
        # Edit path — old_string / new_string with replace_all.
        old_string = tool_input.get("old_string")
        new_string = tool_input.get("new_string")
        if not isinstance(old_string, str) or not isinstance(new_string, str):
            raise TypeError(
                "Edit tool_input.old_string and .new_string must both be str"
            )
        replace_all = bool(tool_input.get("replace_all", False))
        if replace_all:
            simulated = current_content.replace(old_string, new_string)
        else:
            simulated = current_content.replace(old_string, new_string, 1)

    parsed = _parse_pinned_section(simulated)
    if parsed is None:
        # No Pinned Context section in the post-edit state. Treat as
        # "no pins" — caps cannot be violated when the section is absent.
        return []

    _, _, pinned_content = parsed
    return parse_pins(pinned_content)


def compute_deny_reason(
    pre_pins: List[Pin],
    post_pins: List[Pin],
    new_body: str,
) -> Optional[str]:
    """Net-worse deny predicate: return a rendered deny-reason or None.

    Compares `evaluate_full_state` on pre vs post. Denies ONLY when the
    post state is strictly worse than pre — i.e., a violation appears
    (or worsens) that didn't exist before. Pre-malformed state alone
    never denies: if the user already has 14 pins from a manual paste,
    every subsequent Edit would loop in deny (F1 livelock precedent).

    Rules:
      - Pre OK,   post OK   → allow (None)
      - Pre OK,   post bad  → deny, render the post-state violation
      - Pre bad,  post same kind → allow unless strictly worse numerically
      - Pre bad,  post different kind → deny (introduced a NEW violation)

    Embedded-pin smuggle is a separate check — if `new_body` itself parses
    as a pin structure, deny with DENY_REASON_EMBEDDED_PIN even when
    post_pins look fine (the new pin may not have been added yet at the
    Edit-simulation granularity).

    Args:
        pre_pins: Parsed pins from the pre-edit CLAUDE.md state.
        post_pins: Parsed pins from the simulated post-edit state.
        new_body: The candidate body text that is about to be added,
            for embedded-pin detection. "" when not applicable (Write
            full-file replacement or refactor Edit).

    Returns:
        Rendered deny-reason string if the edit should be denied, else None.
    """
    # Embedded-pin smuggle: check the candidate body independently. A
    # curator's new pin body containing `### ` would inflate count on
    # next parse. Conservative check — rejects H3 in bodies regardless
    # of whether the candidate has yet been added to post_pins.
    if new_body and parse_pins(new_body):
        return DENY_REASON_EMBEDDED_PIN

    pre_violation = evaluate_full_state(pre_pins)
    post_violation = evaluate_full_state(post_pins)

    if post_violation is None:
        return None

    # Post has a violation. Decide whether it's strictly worse than pre.
    if pre_violation is None:
        # Pre clean, post bad → strictly worse; deny with templated reason.
        return _render_deny_reason(post_violation)

    # Both bad. Deny only if post is strictly worse than pre:
    #   - different kind (e.g., was size, now count+size) → worse
    #   - same kind but larger numeric overshoot → worse
    if post_violation.kind != pre_violation.kind:
        return _render_deny_reason(post_violation)

    if post_violation.kind == "count":
        pre_count = pre_violation.current_count or 0
        post_count = post_violation.current_count or 0
        if post_count > pre_count:
            return _render_deny_reason(post_violation)
        return None

    if post_violation.kind == "size":
        pre_chars = pre_violation.offending_pin_chars or 0
        post_chars = post_violation.offending_pin_chars or 0
        if post_chars > pre_chars:
            return _render_deny_reason(post_violation)
        return None

    # Unknown kind — conservative: deny (safer than silent allow).
    return _render_deny_reason(post_violation)


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
    if violation.kind == "embedded_pin":
        return DENY_REASON_EMBEDDED_PIN
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
