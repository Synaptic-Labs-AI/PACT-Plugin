"""
Structural pin tests for the wake/idle message-ordering hardening rules.

Pins the reader-facing instruction surfaces that teach both sides of the
wake/idle delivery-ordering race:

  teammate-side (skills/pact-agent-teams/SKILL.md):
    - "On Wake: Disk-First Re-Read (Seam-Agnostic)" — wake classes split
      the reading authority: pure-signal wakes read durable state first
      (durable state authoritative, message content advisory — scoped to
      signal wakes); content-carrying wakes read the field-labeled
      payload from the message body, with the disk read as confirmation.
    - "Counter-Confirm Suppression" — fresh disk read before any
      state-clarification message; suppress when already resolved.
    - "Boundary-Drain Rule" — inbox drain + drain report before every
      protocol-boundary message.
  lead-side (protocols/pact-completion-authority.md + its byte-mirrored
  region in protocols/pact-protocols.md, and agents/pact-orchestrator.md):
    - "Crossed-Wake Idles: Discriminate by Timestamp Direction" — including
      the behavioral non-goal note that synchronous wake/send detection is
      dead-by-construction, and the pre-directive idle-straggler
      discrimination (a tick predating the directive send is a straggler,
      never a stall signal).
    - "Directive-Reflection Check" — mid-turn directives verified against
      boundary-message deliverables before acting.
  stall-detection (protocols/pact-agent-stall.md + its byte-mirrored
  region in pact-protocols.md):
    - post-wake / live-intentional_wait idles are delivery-ordering
      artifacts, not stalls, and the exemption is bidirectional: an idle
      crossing a just-sent directive in either order is not stall
      evidence (a predating tick is a straggler).
  persona (agents/pact-orchestrator.md):
    - the §12 crossed-wake summary incl. the predating-straggler outcome
      ("take no action at all").

PRESENCE pins, not counts. Unlike the Read-Trigger marker phrase (see
test_read_trigger_precondition_pinned.py EXPECTED_COUNTS), none of these
phrases is intended to recur a fixed number of times per surface, so count
pins would add lockstep-maintenance cost without catching a real erosion
shape. No new EXPECTED_COUNTS-style lockstep is introduced by this module.

PHRASES, not line shapes. Several pinned sentences are hard-wrapped in the
shipped markdown and several rule sentences share a line with pre-existing
list-item text (semantically identical markdown renderings), and tool
language inside a pinned span is inline-code formatted. Phrase pins
therefore match against backtick-AND-whitespace-NORMALIZED text (see
_phrase: strip backticks, then `" ".join(text.split())`) so they survive
re-wrapping and inline-code rendering alike — a retired rendering whose
backtick sits inside the phrase span ("`SendMessage` FIRST") is caught,
not masked. Heading pins match line-anchored raw lines
(per the section-presence convention in test_read_trigger_precondition_
pinned.py — substring matching for headings is a phantom-green shape: an
H4 line contains its H3 prefix as a substring).

Mirror discipline: pact-protocols.md is pinned as its own surface for every
phrase that lives in a byte-mirrored region (Completion Authority, Agent
Stall Detection). verify-protocol-extracts.sh enforces byte-parity upstream;
the duplicate pins here match the actual reader-facing surface set rather
than relying solely on the upstream script (same rationale as DOC_SURFACES
in test_read_trigger_precondition_pinned.py).

Counter-test-by-revert (verified at authoring time): with the five doc
surfaces reverted to their pre-hardening state (git checkout <pre-fix-ref>
-- <5 doc paths>), every test in this module fails EXCEPT the absence pins
(which also pass pre-fix only where the retired token was already absent;
the orchestrator absence pin goes RED pre-fix because the retired
Monitor-signal token was present there). Restore with git checkout HEAD --
<paths>. The exact flip-set cardinality observed at authoring time is
recorded in the module-level comment at the bottom of this file.
"""

import re
import subprocess
from pathlib import Path

import pytest

from fixtures.tracked_files import tracked_glob as _shared_tracked_glob

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def _tracked_glob(repo: Path, pattern: str, known_member: str, *,
                  keep_deleted: bool = False) -> list[Path]:
    """Deleted index entries are opt-in; the default is the safe direction.

    A population read as TEXT must not keep deleted entries: the reader would
    raise FileNotFoundError on the deleted path instead of the named coverage
    failure the scan already reports. Only a structural assertion that must
    still SEE a deleted member passes True.
    """
    return _shared_tracked_glob(
        repo, pattern, known_member, keep_deleted=keep_deleted
    )


SKILL = PLUGIN_ROOT / "skills" / "pact-agent-teams" / "SKILL.md"
COMPLETION_AUTHORITY = PLUGIN_ROOT / "protocols" / "pact-completion-authority.md"
PROTOCOLS_SSOT = PLUGIN_ROOT / "protocols" / "pact-protocols.md"
CT_TEACHBACK = PLUGIN_ROOT / "protocols" / "pact-ct-teachback.md"
ORCHESTRATOR = PLUGIN_ROOT / "agents" / "pact-orchestrator.md"
AGENT_STALL = PLUGIN_ROOT / "protocols" / "pact-agent-stall.md"

ALL_SURFACES = [SKILL, COMPLETION_AUTHORITY, PROTOCOLS_SSOT, ORCHESTRATOR, AGENT_STALL]


def _raw(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _phrase(text: str) -> str:
    """Phrase-matching normalization: strip backticks, then collapse any
    whitespace run (including newlines from hard-wrapping) to a single
    space. Tool language is inline-code formatted in the shipped markdown,
    so a pinned phrase must match a subject rendered with backticks inside
    the phrase span. Measured blindness this closes: the retired rendering
    "`SendMessage` FIRST" was invisible to the contiguous "SendMessage
    FIRST" absence token — the backtick sat between the token halves, and a
    whitespace-only normalizer keeps it there. Applied to BOTH sides (file
    text and pinned phrase) so phrases stored with or without backticks
    match consistently."""
    return " ".join(text.replace("`", "").split())


def _normalized(path: Path) -> str:
    """Backtick-and-whitespace-normalized file text for phrase pins: an
    intentional re-wrap of a rule sentence does not fail the pin while a
    re-WORD still does, and inline-code backticks inside a phrase span do
    not mask a retired rendering. Heading pins deliberately match RAW
    lines (see _lines_outside_fences) — backticks are part of the heading
    contract there and are NOT stripped."""
    return _phrase(_raw(path))


def _lines_outside_fences(path: Path) -> list:
    """Stripped lines of the file, excluding fenced-code-block content and
    the fence delimiter lines themselves. A heading-shaped line inside a
    ``` / ~~~ fence is example text, not a real section heading, and must
    not satisfy a heading pin (a section deletion that leaves behind a
    fenced example of its own heading would otherwise stay green)."""
    lines = []
    in_fence = False
    for line in _raw(path).splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(stripped)
    return lines


# ---------------------------------------------------------------------------
# Heading pins — line-anchored exact match.
# ---------------------------------------------------------------------------

HEADING_PINS = [
    (SKILL, "### On Wake: Disk-First Re-Read (Seam-Agnostic)"),
    (SKILL, "### Counter-Confirm Suppression"),
    (SKILL, "## Boundary-Drain Rule"),
    (COMPLETION_AUTHORITY, "### Crossed-Wake Idles: Discriminate by Timestamp Direction"),
    (COMPLETION_AUTHORITY, "### Directive-Reflection Check"),
    (PROTOCOLS_SSOT, "### Crossed-Wake Idles: Discriminate by Timestamp Direction"),
    (PROTOCOLS_SSOT, "### Directive-Reflection Check"),
    # The orchestrator persona carries a summary of the lead-side check as
    # an H4 under its Teachback Review section (not a full H3 mirror).
    (ORCHESTRATOR, "#### Directive-Reflection Check"),
]


@pytest.mark.parametrize(
    "doc_path, heading",
    HEADING_PINS,
    ids=[f"{p.name}::{h.lstrip('# ')[:40]}" for p, h in HEADING_PINS],
)
def test_rule_heading_present(doc_path: Path, heading: str):
    """Each new rule section must exist as an exact heading line so the
    section is discoverable and its anchor slug is a stable cross-ref
    target. Line-anchored: `#### X` must NOT satisfy a `### X` pin (and
    vice versa) — heading LEVEL is part of the document contract. Fenced
    code blocks are excluded: a heading-shaped line inside an example
    fence is not a real section."""
    lines = _lines_outside_fences(doc_path)
    assert heading in lines, (
        f"{doc_path.name}: heading {heading!r} not found as an exact line. "
        f"If the section was intentionally renamed or re-leveled, update "
        f"this pin AND every cross-ref that targets its anchor slug in "
        f"lockstep (see the anchor-slug tests in this module)."
    )


# ---------------------------------------------------------------------------
# Phrase pins — whitespace-normalized presence.
# ---------------------------------------------------------------------------

PHRASE_PINS = [
    # --- teammate-side SKILL ---
    # The load-bearing interpretation rule for every wake. The F7 wake-
    # class split SCOPED this sentence to signal wakes; the short form
    # stays as the vocabulary pin, and the scoped long form below is the
    # anchored companion that catches an unscope-revert (a regression
    # that restores the unscoped sentence keeps this short pin green).
    (SKILL, "Durable state is authoritative"),
    # The F7 scoping marker itself: point 2's authority rule now names
    # its class. 1x-unique (premise-verified); deleting the scope tail
    # or reverting the scoping flips exactly this case.
    (SKILL, "Durable state is authoritative; message content is advisory confirmation — for signal wakes"),
    # The F7 class inversion — the sentence that resolves the authority
    # contradiction with §On Rejection: for content-carrying wakes the
    # message is the reading copy and the disk confirms. Without this
    # witness the section could revert to disk-first-everywhere while
    # the (green) short vocabulary pin above claims coverage.
    (SKILL, "For a content-carrying wake the authority inverts: the message is the reading copy for the content it carries, and the disk copy confirms it"),
    # The F7 class taxonomy defining the two wake classes; the scoping
    # and inversion pins above are meaningless without their terms
    # defined. Deleting the taxonomy alone flips exactly this case.
    (SKILL, "a CONTENT-CARRYING wake (rejection corrections, a payload-carrying notify) delivers its content in the message body itself"),
    # Residual-race mitigation: a wake asserting a resolution the disk does
    # not yet show gets a re-read, never a single-empty-read action.
    (SKILL, "never act on a single empty read"),
    # The drain-report convention every protocol-boundary message carries.
    (SKILL, "boundary-drain: inbox empty"),
    # The fail-safe branch of the drain mechanics: inbox read errors mean
    # "report unavailable and proceed", never "block". Pinned so the
    # fail-open wording is not edited away if inbox persistence changes.
    (SKILL, "report the drain as unavailable and proceed"),
    # Both-teammateMode applicability is stated in the rule text itself.
    (SKILL, "in-process and tmux teammateMode"),
    # The intentional_wait flag's consumer claim must stay accurate: the
    # missed_wake_scan hook IS a lead-side consumer (the prior "no
    # in-plugin consumers" claim was stale and contradicted the no-hook
    # non-goal note's framing).
    (SKILL, "missed_wake_scan"),
    # Counter-confirm suppression's operative outcome: when a fresh disk
    # read shows the situation already resolved, the clarification is
    # suppressed entirely. Without body pins the section heading survives
    # a rewrite that guts the rule (verified at authoring time: the whole
    # section body deleted with every other pin staying green).
    (SKILL, "send NOTHING"),
    (SKILL, "Durable state IS the reply"),
    # The read-only clause of the drain mechanics: the inbox file is
    # platform-owned; agents must never mutate it.
    (SKILL, "NEVER write, truncate, or delete"),
    # The single-empty-read rule's HOME (the On Wake residual-race step).
    # The shorter "never act on a single empty read" pin above is ALSO
    # satisfied by the On-Rejection parenthetical cross-ref, so deleting
    # the rule home alone would keep that pin green; this longer contiguous
    # span exists only at the rule home. (Hardening-audit re-verification:
    # rewording the rule home leaves the short pin green via the cross-ref
    # decoy and flips exactly this companion — measured, unchanged.)
    (SKILL, "re-read once after a brief pause; never act on a single empty read"),
    # The crossed mid-turn directive rule: an already-submitted deliverable
    # that reflects pre-directive scope is revised proactively, not on
    # request.
    (SKILL, "revise it on the same task without waiting to be asked"),
    # --- lead-side completion-authority (+ byte-mirrored SSOT region) ---
    (COMPLETION_AUTHORITY, "exactly ONE redundant confirm"),
    # The behavioral no-hook non-goal note: PreToolUse fires, but inbox
    # delivery is asynchronous and PostToolUse remains unmeasured, so no
    # synchronous hook can close the delivery-ordering race.
    (COMPLETION_AUTHORITY, "SendMessage does fire PreToolUse"),
    (COMPLETION_AUTHORITY, "that hook runs before delivery, so it cannot observe the wake"),
    (COMPLETION_AUTHORITY, "PostToolUse remains unmeasured, not refuted"),
    (COMPLETION_AUTHORITY, "inbox files are written asynchronously on delivery"),
    (COMPLETION_AUTHORITY, "Synchronous wake/send detection is therefore dead-by-construction"),
    (COMPLETION_AUTHORITY, "in-process and tmux teammateMode"),
    (COMPLETION_AUTHORITY, "boundary-drain: inbox empty"),
    # The evidence bar for escalating an idle to stall diagnosis.
    # (Hardening-audit census: 2x on the extract — the escalation bar and
    # the stall-definition parenthetical, both governing — and 3x on the
    # SSOT, whose extra occurrence is the agent-stall mirrored region.
    # Deleting both extract-region occurrences lockstep leaves the SSOT
    # case green via the stall-region decoy while the extract case flips;
    # compensated by the extract pin + the byte-parity gate. Measured,
    # leave as designed.)
    (COMPLETION_AUTHORITY, "task-file-mtime plus sustained-silence"),
    # The pre-directive straggler discrimination: an idle tick whose
    # timestamp predates the lead's directive send is prior-turn state,
    # not a stall signal.
    (COMPLETION_AUTHORITY, "predates your directive send is a straggler"),
    # The unified send-term on the postdating side: "directive send" is the
    # section's single term for the lead's resolving send (the retired
    # "wake-send" named only the wake archetype and missed acceptance and
    # confirm crossings). The span is anchored to the postdating item's own
    # wording — the shorter "postdates your directive send" is ALSO
    # satisfied by the bidirectional opener's sentence ("When the
    # notification postdates your directive send"), so it would stay green
    # if the #1081-era item itself regressed to the retired term (measured:
    # pre-unification revert flips only 1 of 3 presence pins with the
    # short form; this anchored form flips all mirrored-surface pins).
    (COMPLETION_AUTHORITY, "On an idle notification that postdates your directive send"),
    # The bidirectional opener: the section's first sentence must name
    # BOTH crossing orders, correcting a heading-level scan that reads the
    # title as postdating-only. (Hardening-audit census: on the SSOT the
    # phrase recurs 2x — the mirrored opener and the agent-stall region's
    # cross-ref — so the SSOT case alone stays green under a lockstep
    # opener reword; that decoy is compensated: the completion-authority
    # case is 1x-unique and flips, and the byte-parity gate forces any
    # extract edit to mirror. Measured, leave as designed.)
    (COMPLETION_AUTHORITY, "Discriminate by direction"),
    # The anti-acceleration rule: faster sends feed the crossed-message
    # rhythm; patience is the counter.
    (COMPLETION_AUTHORITY, "Never accelerate nudging in response to idle ticks"),
    # The ambiguity resolution: one durable read settles an unclear
    # timestamp; the predicate's claim-present outcome and claim-absent
    # fallthrough are the operative halves of the same bullet.
    (COMPLETION_AUTHORITY, "one durable read settles it"),
    (COMPLETION_AUTHORITY, "a claim absent falls through to the postdating procedure"),
    # The two-idle gate: a single tick is never evidence, and predating
    # straggler ticks are excluded from the count entirely.
    (COMPLETION_AUTHORITY, "A single tick is never evidence"),
    (COMPLETION_AUTHORITY, "a tick that predates your directive send never counts"),
    # The directive-reflection aphorism naming the failure mode the check
    # exists for. Matching is case-sensitive by design; this surface carries
    # the sentence-initial capitalized form.
    (COMPLETION_AUTHORITY, "Delivery is not processing"),
    (PROTOCOLS_SSOT, "exactly ONE redundant confirm"),
    (PROTOCOLS_SSOT, "SendMessage does fire PreToolUse"),
    (PROTOCOLS_SSOT, "that hook runs before delivery, so it cannot observe the wake"),
    (PROTOCOLS_SSOT, "PostToolUse remains unmeasured, not refuted"),
    (PROTOCOLS_SSOT, "inbox files are written asynchronously on delivery"),
    (PROTOCOLS_SSOT, "Synchronous wake/send detection is therefore dead-by-construction"),
    (PROTOCOLS_SSOT, "in-process and tmux teammateMode"),
    (PROTOCOLS_SSOT, "boundary-drain: inbox empty"),
    (PROTOCOLS_SSOT, "task-file-mtime plus sustained-silence"),
    (PROTOCOLS_SSOT, "predates your directive send is a straggler"),
    (PROTOCOLS_SSOT, "On an idle notification that postdates your directive send"),
    (PROTOCOLS_SSOT, "Discriminate by direction"),
    (PROTOCOLS_SSOT, "Never accelerate nudging in response to idle ticks"),
    (PROTOCOLS_SSOT, "one durable read settles it"),
    (PROTOCOLS_SSOT, "a claim absent falls through to the postdating procedure"),
    (PROTOCOLS_SSOT, "A single tick is never evidence"),
    (PROTOCOLS_SSOT, "a tick that predates your directive send never counts"),
    (PROTOCOLS_SSOT, "Delivery is not processing"),
    # --- orchestrator persona ---
    (ORCHESTRATOR, "exactly ONE redundant confirm"),
    # The Wait-in-Silence rule's single protocol-defined exception — without
    # it, the persona's no-reply-to-idle-turns reflex suppresses the one
    # legitimate redundant confirm.
    (ORCHESTRATOR, "the single redundant confirm after a crossed wake"),
    # The persona's §12 crossed-wake bullet carries the unified send-term on
    # both discrimination arms (postdating arm here; the predating arm is
    # covered by the "take no action at all" straggler-outcome pin below).
    (ORCHESTRATOR, "postdates your directive send"),
    # The persona §12 straggler outcome: a predating tick gets NO action at
    # all — the strongest form, distinct from §5's shorter parenthetical
    # "take no action" (this pin requires the "at all" tail).
    (ORCHESTRATOR, "take no action at all"),
    # The replacement content-arrival signal after the Read-Trigger rule was
    # reframed from the retired Monitor-token 4-point form to 3 points.
    (ORCHESTRATOR, "wake-signal SendMessage is the content-arrival signal"),
    (ORCHESTRATOR, "boundary-drain:"),
    (ORCHESTRATOR, "task-file-mtime plus sustained-silence"),
    # Staleness-signal bullet must name the hook that consumes the flag.
    (ORCHESTRATOR, "missed_wake_scan"),
    # Same aphorism as the completion-authority pin above; this surface
    # carries the mid-sentence lowercase form (per-surface casing is
    # deliberate — do not normalize case to unify these pins).
    (ORCHESTRATOR, "delivery is not processing"),
    # --- stall-detection protocol (+ byte-mirrored SSOT region) ---
    # The harmonizing exception: post-wake / live-intentional_wait idles are
    # not stall evidence.
    (AGENT_STALL, "delivery-ordering artifacts, not stalls"),
    (AGENT_STALL, "task-file-mtime plus sustained-silence"),
    # The stall-exemption clause is bidirectional: an idle crossing a just-
    # sent directive in EITHER order is exempt from immediate stall
    # treatment (predating ticks are stragglers, not stall evidence).
    (AGENT_STALL, "crosses a directive you just sent in either order"),
    (PROTOCOLS_SSOT, "delivery-ordering artifacts, not stalls"),
    (PROTOCOLS_SSOT, "crosses a directive you just sent in either order"),
]


@pytest.mark.parametrize(
    "doc_path, phrase",
    PHRASE_PINS,
    ids=[f"{p.name}::{ph[:40]}" for p, ph in PHRASE_PINS],
)
def test_rule_phrase_present(doc_path: Path, phrase: str):
    """Each load-bearing rule phrase must be present on its surface.
    Matching is whitespace-normalized: hard-wrap and same-line-rider
    renderings both satisfy the pin; a re-WORD does not. If a phrase was
    changed intentionally, update the pin in lockstep — otherwise the rule
    has eroded on a surface an LLM loads at runtime."""
    normalized_phrase = _phrase(phrase)
    assert normalized_phrase in _normalized(doc_path), (
        f"{doc_path.name}: rule phrase {phrase!r} not found "
        f"(backtick-and-whitespace-normalized match). If the wording was changed "
        f"intentionally, update this pin in lockstep; otherwise the "
        f"wake-ordering rule this phrase carries is missing from a "
        f"runtime-loaded surface."
    )


# ---------------------------------------------------------------------------
# Cross-ref pins — literal anchor slugs (what markdown actually navigates to).
# ---------------------------------------------------------------------------

CROSSED_WAKE_SLUG = "#crossed-wake-idles-discriminate-by-timestamp-direction"
DIRECTIVE_REFLECTION_SLUG = "#directive-reflection-check"
ON_WAKE_SLUG = "#on-wake-disk-first-re-read-seam-agnostic"
BOUNDARY_DRAIN_SLUG = "#boundary-drain-rule"

CROSS_REF_PINS = [
    # Orchestrator persona lazy-loads the full rules from the protocol.
    (ORCHESTRATOR, CROSSED_WAKE_SLUG),
    (ORCHESTRATOR, DIRECTIVE_REFLECTION_SLUG),
    # SKILL forward-links the teammate-side rules to the lead-side rule and
    # to its own sections.
    (SKILL, CROSSED_WAKE_SLUG),
    (SKILL, ON_WAKE_SLUG),
    (SKILL, BOUNDARY_DRAIN_SLUG),
    # Completion-authority links back to the teammate-side complements.
    (COMPLETION_AUTHORITY, ON_WAKE_SLUG),
    (COMPLETION_AUTHORITY, BOUNDARY_DRAIN_SLUG),
    (PROTOCOLS_SSOT, ON_WAKE_SLUG),
    (PROTOCOLS_SSOT, BOUNDARY_DRAIN_SLUG),
    # Stall protocol delegates the crossed-wake handling to the rule.
    (AGENT_STALL, CROSSED_WAKE_SLUG),
    (PROTOCOLS_SSOT, CROSSED_WAKE_SLUG),
]


@pytest.mark.parametrize(
    "doc_path, slug",
    CROSS_REF_PINS,
    ids=[f"{p.name}::{s.lstrip('#')[:40]}" for p, s in CROSS_REF_PINS],
)
def test_cross_ref_slug_present(doc_path: Path, slug: str):
    """Each referrer surface must carry the literal anchor slug so the
    lazy-load reference resolves. Pin the slug rather than prose link text
    — the slug is what GitHub-flavored markdown navigates to, and a heading
    rename that forgets a referrer leaves a 404 nav target. The match is
    terminator-guarded: the slug must not continue with slug characters
    ([a-z0-9-]), so a longer future slug that prefix-engulfs a pinned one
    (e.g. `...-check` inside `...-checklist`) does not satisfy the pin."""
    assert re.search(re.escape(slug) + r"(?![a-z0-9-])", _raw(doc_path)), (
        f"{doc_path.name}: anchor slug {slug!r} not found. Either the "
        f"cross-ref was removed (the lazy-load path to the full rule is "
        f"gone) or the target heading was renamed without updating this "
        f"referrer."
    )


# ---------------------------------------------------------------------------
# Anchor-slug integrity — pinned headings must slugify to the slugs the
# referrers actually use, so a heading rename cannot silently strand them.
# ---------------------------------------------------------------------------


def _github_slug(heading: str) -> str:
    """GitHub-flavored-markdown anchor slug for a heading line: strip the
    leading hashes, lowercase, drop everything but alphanumerics, spaces,
    and hyphens, then hyphenate spaces."""
    text = heading.lstrip("#").strip().lower()
    kept = "".join(ch for ch in text if ch.isalnum() or ch in " -")
    return "#" + kept.replace(" ", "-")


ANCHOR_INTEGRITY = [
    ("### Crossed-Wake Idles: Discriminate by Timestamp Direction", CROSSED_WAKE_SLUG),
    ("### Directive-Reflection Check", DIRECTIVE_REFLECTION_SLUG),
    ("### On Wake: Disk-First Re-Read (Seam-Agnostic)", ON_WAKE_SLUG),
    ("## Boundary-Drain Rule", BOUNDARY_DRAIN_SLUG),
]


@pytest.mark.parametrize(
    "heading, expected_slug",
    ANCHOR_INTEGRITY,
    ids=[s.lstrip("#")[:40] for _, s in ANCHOR_INTEGRITY],
)
def test_heading_slugifies_to_referenced_anchor(heading: str, expected_slug: str):
    """The pinned heading text must derive exactly the anchor slug the
    referrer surfaces use. Combined with the heading-presence and
    slug-presence pins above, this closes the rename loop: a heading
    rename fails here unless every referrer moves in lockstep."""
    assert _github_slug(heading) == expected_slug, (
        f"Heading {heading!r} slugifies to {_github_slug(heading)!r} but "
        f"referrers link {expected_slug!r} — the cross-refs would be 404 "
        f"nav targets."
    )


# ---------------------------------------------------------------------------
# Absence pin — retired Monitor-signal token.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("doc_path", ALL_SURFACES, ids=lambda p: p.name)
def test_retired_inbox_grew_token_absent(doc_path: Path):
    """Regression guard: the Read-Trigger Precondition rule was reframed
    to key on the wake-signal SendMessage as the content-arrival signal;
    the Monitor INBOX_GREW event is an alarm clock, not a content marker,
    and teaching it as part of the read-trigger rule was the drift this
    change removed. The token must not reappear on any of these surfaces —
    reintroduction would resurrect the retired signal as instruction text."""
    assert "INBOX_GREW" not in _raw(doc_path), (
        f"{doc_path.name}: retired token 'INBOX_GREW' reappeared. The "
        f"read-trigger rule keys on the wake-signal SendMessage, not the "
        f"Monitor event; do not reintroduce the event token into "
        f"instruction surfaces (if a future rule genuinely needs it, "
        f"update this guard deliberately)."
    )


# ---------------------------------------------------------------------------
# Absence pin — retired "SendMessage fires no hook" claim, in any wording, in
# every non-test text file of the plugin.
# ---------------------------------------------------------------------------

_RETIRED_CLAIM_SUFFIXES = {".md", ".py", ".json", ".sh", ".txt", ".toml", ".yaml", ".yml"}
_HOOK_TERM = r"(?:pre/posttooluse|pretooluse|posttooluse|hook(?:able)?|hook event)"
# Each alternative is a denial that SendMessage reaches a hook. A clause is
# flagged only when it ALSO names SendMessage, so the true statement "a tool
# that never fires PreToolUse is a dead entry" stays clean, and so does the
# corrected wording ("SendMessage does fire PreToolUse ... PostToolUse
# remains unmeasured, not refuted").
_RETIRED_CLAIM_DENIAL = re.compile(
    "|".join(
        (
            rf"\bfires? no (?:\w+ ){{0,2}}{_HOOK_TERM}",
            rf"\b(?:does|do|did) not fire (?:any |a )?{_HOOK_TERM}",
            rf"\b(?:doesn't|never) fires? (?:any |a )?{_HOOK_TERM}",
            rf"\bno (?:\w+ ){{0,2}}{_HOOK_TERM}s? event",
            r"\b(?:cannot|can't|can not) be hooked",
            r"\b(?:un|non-?)hookable",
            r"\bnot (?:a |an )?(?:\w+[- ]){0,3}hookable",
            r"\bnot in the \S*hookable",
        )
    ),
    re.IGNORECASE,
)


def _retired_claim_clauses(text: str) -> list:
    """Clauses of ``text`` that pair SendMessage with a hook denial.

    Backticks and line-leading comment markers (``#``, ``//``, ``*``, ``>``)
    are removed and whitespace collapsed first, so a claim wrapped across
    comment lines or split by inline code is still one clause. Clauses split
    at sentence punctuation, semicolons and spaced em-dashes.
    """
    flat = " ".join(
        re.sub(r"(?m)^\s*(?:#+|//|\*|>)\s?", " ", text.replace("`", "")).split()
    )
    return [
        clause
        for clause in re.split(r"(?<=[.;!?])\s+|\s+—\s+", flat)
        if re.search(r"sendmessage", clause, re.IGNORECASE)
        and _RETIRED_CLAIM_DENIAL.search(clause)
    ]


def _non_test_plugin_files() -> list:
    """Every tracked text file under the plugin root outside tests/.

    The directory name is READ from the plugin root rather than hardcoded: an
    installed plugin cache places it under a version directory
    (``.../PACT/4.8.34``), where a literal ``pact-plugin/`` prefix matches
    nothing and the empty population aborts collection of this module.
    """
    # ``**/*`` rather than a bare ``**``: identical pathspec in git (verified
    # against the index), but pathlib's glob yields only DIRECTORIES for a
    # trailing ``**``, so the gitless export fallback would return no files.
    tracked = _tracked_glob(
        PLUGIN_ROOT.parent, f"{PLUGIN_ROOT.name}/**/*",
        f"{PLUGIN_ROOT.name}/protocols/pact-protocols.md",
        keep_deleted=False,
    )
    return [
        path
        for path in tracked
        if path.suffix in _RETIRED_CLAIM_SUFFIXES
        and path.relative_to(PLUGIN_ROOT).parts[0] != "tests"
        and "__pycache__" not in path.parts
    ]


def test_deleted_tracked_protocol_is_not_read_back(tmp_path, monkeypatch):
    """A locally deleted tracked file must leave the text scan population.

    With ``keep_deleted=True`` the deleted path stays in the scan and the
    parametrized reader raises FileNotFoundError; the coverage arm's named
    ``required`` failure never gets to report it.
    """
    repo = tmp_path / "repo"
    plugin = repo / "pact-plugin"
    protocols = plugin / "protocols"
    protocols.mkdir(parents=True)
    known = protocols / "pact-protocols.md"
    known.write_text("known\n", encoding="utf-8")
    doomed = protocols / "pact-agent-stall.md"
    doomed.write_text("doomed\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "--", "pact-plugin"], cwd=repo, check=True)
    doomed.unlink()  # the index entry stays; the disk entry is gone
    monkeypatch.setitem(globals(), "PLUGIN_ROOT", plugin)

    files = _non_test_plugin_files()

    assert known in files
    assert doomed not in files, (
        f"the deleted tracked {doomed.name} stayed in the text scan "
        "population; every parametrized reader would raise FileNotFoundError "
        "instead of the named coverage failure")
    for path in files:  # the population is readable; that is the contract
        path.read_text(encoding="utf-8")


def test_tracked_population_excludes_untracked_and_keeps_deleted(tmp_path):
    repo = tmp_path / "repo"
    tracked = repo / "pact-plugin" / "protocols" / "tracked.md"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("tracked")
    (tracked.parent / "untracked.md").write_text("untracked")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "--", "pact-plugin/protocols/tracked.md"], cwd=repo, check=True)
    pattern = "pact-plugin/protocols/*.md"
    assert _tracked_glob(
        repo, pattern, "pact-plugin/protocols/tracked.md",
        keep_deleted=True) == [tracked]
    tracked.unlink()
    assert _tracked_glob(
        repo, pattern, "pact-plugin/protocols/tracked.md",
        keep_deleted=True) == [tracked]


def test_tracked_population_refuses_empty_and_wrong_roots(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(AssertionError, match="empty tracked population"):
        _tracked_glob(repo, "pact-plugin/protocols/*.md", "pact-plugin/protocols/tracked.md")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    with pytest.raises(AssertionError, match="empty tracked population"):
        _tracked_glob(repo, "pact-plugin/protocols/*.md", "pact-plugin/protocols/tracked.md")


def _collection_population() -> list:
    """The collection-time scan, or a named skip in a layout that omits it."""
    try:
        return _non_test_plugin_files()
    except AssertionError:
        if PLUGIN_ROOT.name == "pact-plugin":
            raise
        pytest.skip(
            f"installed layout {PLUGIN_ROOT} ships no "
            f"{PLUGIN_ROOT.name}/protocols tree; the retired-claim scan is "
            "about the dev checkout",
            allow_module_level=True,
        )


@pytest.mark.parametrize(
    "doc_path",
    _collection_population(),
    ids=lambda p: p.relative_to(PLUGIN_ROOT).as_posix(),
)
def test_retired_sendmessage_hook_denial_absent(doc_path: Path):
    """SendMessage reaches PreToolUse; no plugin file may deny it in any wording."""
    hits = _retired_claim_clauses(doc_path.read_text(encoding="utf-8", errors="replace"))
    assert hits == [], (
        f"{doc_path.relative_to(PLUGIN_ROOT)}: retired SendMessage hook denial "
        f"reappeared: {hits!r}. PreToolUse fires on SendMessage; PostToolUse "
        "remains unmeasured, not refuted."
    )


def test_retired_claim_scan_covers_hooks_and_protocols():
    """The tree-derived scan must reach the surfaces that carried the claim."""
    scanned = {p.relative_to(PLUGIN_ROOT).as_posix() for p in _non_test_plugin_files()}
    for required in (
        "hooks/bootstrap_gate.py",
        "hooks/handoff_ordering_gate.py",
        "hooks/task_lifecycle_gate.py",
        "hooks/shared/HOOK_STDIN_DISCRIMINATORS.md",
        "protocols/pact-completion-authority.md",
        "protocols/pact-protocols.md",
        "skills/pact-agent-teams/SKILL.md",
        "commands/orchestrate.md",
    ):
        assert required in scanned
    assert not any(name.startswith("tests/") for name in scanned)


@pytest.mark.parametrize(
    "text",
    [
        "`SendMessage` fires no PreToolUse/PostToolUse event; inbox files are async.",
        "`SendMessage` does not fire `PreToolUse`.",
        "SendMessage doesn't fire any hook.",
        "SendMessage never fires PreToolUse.",
        "Calling SendMessage produces no PreToolUse event.",
        "SendMessage cannot be hooked.",
        "only `SendMessage` is verified-unhookable.",
        "it wakes via inbox/SendMessage, which is\n    # not hookable), so this event",
        "inbox / `SendMessage` (a context injection, not a hookable tool).",
        "# and SendMessage is not in the Pre/PostToolUse-hookable\n# tool set, so",
    ],
    ids=[
        "fires-no", "does-not-fire", "doesnt-fire-any-hook", "never-fires",
        "no-event", "cannot-be-hooked", "unhookable", "wrapped-comment",
        "not-a-hookable-tool", "not-in-hookable-set",
    ],
)
def test_retired_claim_matcher_catches_paraphrase(text):
    assert _retired_claim_clauses(text)


@pytest.mark.parametrize(
    "text",
    [
        "`SendMessage` does fire `PreToolUse`, but that hook runs before delivery, "
        "so it cannot observe the wake; `PostToolUse` remains unmeasured, not refuted; "
        "inbox files are written asynchronously on delivery;",
        "an entry for a tool that never fires PreToolUse is a harmless dead entry.",
        "No registered PostToolUse hook matches SendMessage, so zero runs say nothing.",
        "SendMessage does reach PreToolUse, but that fires before delivery, and "
        "PostToolUse on it is unmeasured.",
    ],
    ids=["corrected-protocol", "no-sendmessage", "true-registration-fact", "corrected-comment"],
)
def test_retired_claim_matcher_spares_true_statements(text):
    assert _retired_claim_clauses(text) == []


# ---------------------------------------------------------------------------
# Absence pin — retired "wake-send" send-term.
# ---------------------------------------------------------------------------

# The surfaces that carried the retired term before the vocabulary
# unification. The teammate-side SKILL and the stall protocol never used it
# (verify-only surfaces for the sweep), so the absence set is the three
# swept surfaces, not ALL_SURFACES.
SWEPT_SURFACES = [COMPLETION_AUTHORITY, PROTOCOLS_SSOT, ORCHESTRATOR]


@pytest.mark.parametrize("doc_path", SWEPT_SURFACES, ids=lambda p: p.name)
def test_retired_wake_send_term_absent(doc_path: Path):
    """Regression guard: "directive send" is the single send-term for the
    lead's resolving send on the crossed-wake surfaces. The retired
    "wake-send" named only the wake archetype and missed acceptance and
    confirm crossings — reintroducing it would split the vocabulary again
    and re-narrow the rule's scope in the reader's eye. Both the hyphenated
    and spaced renderings are locked out. ("wake-signal" is a different
    noun — the SendMessage itself — and is NOT covered by this pin.)"""
    text = _raw(doc_path)
    for retired in ("wake-send", "wake send"):
        assert retired not in text, (
            f"{doc_path.name}: retired send-term {retired!r} reappeared. "
            f"The crossed-wake surfaces use 'directive send' as the single "
            f"send-term; do not reintroduce {retired!r} (if a future rule "
            f"genuinely needs the wake-act archetype, update this guard "
            f"deliberately and re-split the vocabulary consciously)."
        )


# ---------------------------------------------------------------------------
# Acceptance-pair ordering pins — TaskUpdate-first two-call atomic pair.
# ---------------------------------------------------------------------------

# The five command files teach the teachback-acceptance one-sentence form of
# the pair. They join the absence set (the retired ordering tokens must not
# reappear there) but not the presence set — the commands' sentence form is
# "`TaskUpdate(A, status="completed")` FIRST", which does not contain the
# contiguous "TaskUpdate FIRST" token the normative surfaces carry.
COMMAND_SURFACES = [
    PLUGIN_ROOT / "commands" / "orchestrate.md",
    PLUGIN_ROOT / "commands" / "peer-review.md",
    PLUGIN_ROOT / "commands" / "plan-mode.md",
    PLUGIN_ROOT / "commands" / "comPACT.md",
    PLUGIN_ROOT / "commands" / "rePACT.md",
]

ORDERING_PRESENCE_SURFACES = [COMPLETION_AUTHORITY, PROTOCOLS_SSOT, ORCHESTRATOR]
ORDERING_ABSENCE_SURFACES = ORDERING_PRESENCE_SURFACES + COMMAND_SURFACES

# The ct-teachback extract (and its byte-mirrored teachback-flow region in
# the SSOT) taught the pair in the SAME retired call-syntax rendering as the
# commands — wake-`SendMessage` FIRST, then `TaskUpdate(A, ...)`. Neither
# surface carries the contiguous ordering tokens the arms above lock, and
# the extract and its SSOT region can revert to the retired rendering in
# LOCKSTEP without tripping the byte-parity audit (extract and region stay
# mutually byte-equal), so this arm is the only witness on those surfaces.
# Fragment verified green on both at the flipped HEAD and present in both
# pre-flip forms (whitespace-normalized; the retired rendering hard-wraps
# mid-fragment).
COMMAND_FORM_SURFACES = COMMAND_SURFACES + [CT_TEACHBACK, PROTOCOLS_SSOT]


@pytest.mark.parametrize("doc_path", ORDERING_PRESENCE_SURFACES, ids=lambda p: p.name)
def test_acceptance_pair_taskupdate_first_present(doc_path: Path):
    """The lead-side acceptance/rejection two-call atomic pair orders the
    durable write FIRST: the status flip (or rejection-metadata write) lands,
    then the wake-signal SendMessage is the last call. "TaskUpdate FIRST" on
    each normative surface locks that ordering — its erosion means the pair's
    ordering instruction reverted or was reworded away on a runtime-loaded
    surface. Matching is whitespace-normalized like the phrase pins so a
    re-wrap of the heading line does not fail the pin while a re-word does."""
    assert _phrase("TaskUpdate FIRST") in _normalized(doc_path), (
        f"{doc_path.name}: acceptance-pair ordering token 'TaskUpdate FIRST' "
        f"not found. The two-call atomic pair is TaskUpdate-first (wake is "
        f"the last call); if the ordering was changed intentionally, update "
        f"this pin in lockstep with every surface that teaches the pair."
    )


@pytest.mark.parametrize("doc_path", ORDERING_ABSENCE_SURFACES, ids=lambda p: p.name)
def test_retired_sendmessage_first_ordering_absent(doc_path: Path):
    """Regression guard: the acceptance/rejection pair was flipped from
    SendMessage-first to TaskUpdate-first, so any disk read the wake triggers
    observes already-correct state. The retired ordering tokens must not
    reappear on any surface that teaches the pair — reintroduction would
    resurrect the stranding-prone order on a runtime-loaded surface. Both
    tokens are checked against backtick-and-whitespace-normalized text so
    neither a hard-wrapped rendering nor the inline-code rendering (the
    backticked "`SendMessage` FIRST" form — the original retired rendering
    class, invisible to a whitespace-only matcher because the backtick sits
    between the token halves) can slip past a raw-substring scan."""
    text = _normalized(doc_path)
    for retired in ("SendMessage FIRST", "SendMessage must precede"):
        assert _phrase(retired) not in text, (
            f"{doc_path.name}: retired ordering token {retired!r} present. "
            f"The two-call atomic pair is TaskUpdate-first; do not "
            f"reintroduce the SendMessage-first order (if a future rule "
            f"genuinely needs it, update this guard deliberately)."
        )


@pytest.mark.parametrize("doc_path", COMMAND_FORM_SURFACES, ids=lambda p: p.name)
def test_retired_command_call_ordering_absent(doc_path: Path):
    """Regression guard for the retired call-syntax rendering of the
    acceptance pair. The five commands taught the pair as
    `SendMessage(to=X, ...)` FIRST, then `TaskUpdate(A, status="completed")`
    — a call-syntax rendering that contains neither contiguous token the
    ordering-absence pin above locks out, so that pin stayed green pre-flip
    on exactly these five surfaces (it guards reintroduction of the
    normative-form tokens there, not the rendering these files carried).
    The ct-teachback extract and its byte-mirrored teachback-flow region in
    the SSOT carried the same rendering (wake-`SendMessage` FIRST, then
    `TaskUpdate(A, status="completed")`) and are pinned here for the same
    reason — plus one specific to the mirrored pair: a lockstep revert of
    extract and SSOT region leaves them mutually byte-equal, so the
    protocol-extract audit passes and NOTHING but this arm witnesses.
    This pin locks out the retired rendering's load-bearing fragment: FIRST
    immediately followed by the TaskUpdate call. The flipped form cannot
    produce it — the wake-SendMessage is the last call, so no "FIRST, then
    `TaskUpdate" sequence can appear on a TaskUpdate-first surface. Verified
    against both forms before pinning: the fragment is present in all five
    pre-flip command files, both pre-flip ct-teachback surfaces, and absent
    from every flipped file (and from every other agents/commands/protocols/
    skills file). Backtick-and-whitespace-normalized like the sibling
    absence pin (both sides through _phrase) so a hard-wrapped rendering
    cannot slip past and the pinned fragment's own backtick cannot mask
    it."""
    assert _phrase("FIRST, then `TaskUpdate") not in _normalized(doc_path), (
        f"{doc_path.name}: retired command-form ordering token "
        f"'FIRST, then `TaskUpdate' present. The two-call atomic pair is "
        f"TaskUpdate-first (`TaskUpdate(A, status=...)` FIRST, then the "
        f"wake-signal SendMessage); do not reintroduce the SendMessage-first "
        f"call-syntax rendering (if a future rule genuinely needs it, update "
        f"this guard deliberately)."
    )


# ---------------------------------------------------------------------------
# Counter-test flip-set record (measured at authoring time; see module
# docstring). With the five surfaces reverted to their pre-hardening state
# and this module run against them: {53 failed, 8 passed}. Heading pins
# 8/8 RED, phrase pins 33/33 RED (no pinned phrase pre-existed on any
# surface), cross-ref pins 11/11 RED, absence pin RED on
# pact-orchestrator.md (retired token present pre-fix) and GREEN on the
# other four surfaces (token never present there). The 8 GREEN =
# anchor-integrity 4/4 (pure functions of module constants — intentionally
# revert-immune) + the 4 vacuously-satisfied absence pins.
# Section-body deletion probes (measured after the body-pin additions):
# gutting the Counter-Confirm Suppression body while keeping its heading
# fails exactly its 2 body pins; deleting the On Wake residual-race step
# fails exactly the rule-home span pin while the shorter single-empty-read
# pin stays green via the On-Rejection parenthetical.
# Matcher-robustness probes (measured): rewriting a referrer's slug to a
# longer slug that prefix-engulfs the pinned one flips the slug pin RED
# (terminator guard); deleting a section heading while leaving a fenced
# code example of the same heading line flips the heading pin RED
# (fence exclusion). Neither hardening changes the flip-set above.
#
# 2026-08-27 addendum (pre-directive idle-straggler cycle): the phrase-pin
# inventory above grew 33 (authoring) -> 39 (#1525 rule pins: straggler
# classification, bidirectional opener, anti-acceleration x both mirrored
# surfaces) -> 50 (remediation cycle 1: ambiguity durable-read + claim-absent
# fallthrough + two-idle gate + straggler-exclusion x both mirrored surfaces,
# stall-exemption bidirectional clause x stall + SSOT, persona straggler
# outcome). Module cases 61 -> 67 -> 78. Counter-test for the #1525 pins
# (measured 2026-08-27): identical both-surface reword of the straggler
# phrase — extract/SSOT byte-parity preserved, so the audit gate stays
# green and this module is the sole red source — flips exactly the 2
# per-surface pins for that phrase (predicted pre-run; observed exactly
# 2 failed). All 17 post-authoring phrase pins go RED under a pre-#1525
# revert: none of their phrases pre-existed on any surface
# (occurrence-verified at pin time).
#
# 2026-08-27 addendum (#1530 send-vocabulary unification cycle): the
# retired "wake-send" term was unified to "directive send" across the
# three surfaces that carried it (completion-authority extract, its SSOT
# region, orchestrator persona §5/§12); the teammate SKILL and stall
# protocol never carried it. Phrase pins 50 -> 53 (unified postdating
# send-term x 3 surfaces) and a new retired-term absence test x 3 swept
# surfaces; module cases 78 -> 84. PHANTOM-GREEN CAUGHT BY MEASUREMENT:
# the short pin "postdates your directive send" is satisfied pre-revert
# on both mirrored surfaces by the #1529 bidirectional opener's own
# sentence ("When the notification postdates your directive send"), so
# the mirrored-surface pins anchor to the longer postdating-item span
# ("On an idle notification that postdates your directive send"), which
# exists only at the reworded item. Counter-test (measured 2026-08-27,
# docs reverted to pre-unification in an isolated copy): exactly 6 failed
# = the 3 unified-term presence pins + the 3 retired-term absence cases;
# 78 passed; post-unification 84/84 green.
#
# 2026-08-28 addendum (acceptance-pair flip cycle): the lead-side
# acceptance/rejection two-call atomic pair was flipped from
# SendMessage-first to TaskUpdate-first across 9 files (persona, 5
# commands, completion-authority extract, ct-teachback extract, SSOT
# mirrors). New arm: 3 "TaskUpdate FIRST" presence cases (normative
# surfaces) + 8 retired-ordering absence cases (3 normative + 5
# commands); module cases 84 -> 95. Counter-test (measured 2026-08-28,
# 9 doc files stash-reverted to pre-flip): exactly 6 failed = the 3
# presence cases + the absence cases on the 3 normative surfaces (the
# retired tokens pre-existed there); the 5 command absence cases stayed
# GREEN pre-flip (the commands' retired form was
# "`SendMessage(to=X, ...)` FIRST" — not the contiguous token this pin
# locks out), so they guard reintroduction, not the flip itself.
# Post-flip 95/95 green.
#
# 2026-08-28 follow-up (TEST-phase, lead-ordered): the gap noted above —
# the command surfaces' own retired call-syntax rendering was unpinned —
# closed by test_retired_command_call_ordering_absent: 5 absence cases
# over COMMAND_SURFACES locking the fragment "FIRST, then `TaskUpdate"
# (the retired rendering's load-bearing sequence; the flipped form cannot
# produce it). Discriminator verified against BOTH forms before pinning:
# present in all 5 pre-flip files, absent from all 5 flipped files and
# every other instruction file. Module cases 95 -> 100. Counter-test
# (measured 2026-08-28): orchestrate.md alone reverted to pre-flip —
# exactly 1 failed = this test's orchestrate.md case (the 8 sibling
# ordering cases stayed green, as the original addendum predicted);
# post-restore 100/100 green.
#
# 2026-08-28 review-cycle extension (review-test fixer, lead-ordered): the
# same retired call-syntax rendering was unpinned on the ct-teachback
# extract and its byte-mirrored SSOT region — surfaces in NO arm of this
# module — and a LOCKSTEP revert of the pair leaves extract and region
# mutually byte-equal, so the protocol-extract audit passes too (measured
# during review: module + audit green with both reverted). The arm now
# parametrizes over COMMAND_FORM_SURFACES (5 commands + ct-teachback +
# SSOT): module cases 100 -> 102. Fragment discriminator verified in
# whitespace-normalized text (the retired rendering hard-wraps mid-fragment,
# so a raw line grep reads absent on both sides): present at base on both
# new surfaces, absent at flipped HEAD on both. Counter-test (measured
# 2026-08-28): ct-teachback.md alone reverted to pre-flip — exactly 1
# failed = this test's pact-ct-teachback.md case (101 passed); post-restore
# 102/102 green, audit module green alongside.
#
# 2026-08-28 cycle 2 (review-test, lead-ordered, user-directed EXCL-1
# adoption): _normalized now strips backticks via _phrase (applied to BOTH
# the file text and every pinned phrase), closing the rendering class where
# a backtick sits inside a phrase span — the retired "`SendMessage` FIRST"
# form was invisible to the contiguous absence token under a
# whitespace-only matcher. Full pin audit at HEAD under the new matcher:
# 79 pin x surface verdicts compared (53 phrase pins + 3 ordering-presence
# + 16 ordering-absence + 7 command-form) — ZERO verdict changes; module
# 102/102 green before and after. Counter-tests in a TEMP CLONE (no
# live-worktree mutation), all predictions exact: cell 1 — minimal
# backticked form "`SendMessage` FIRST" appended to orchestrate.md: OLD
# matcher 102 passed (blind), NEW matcher exactly 1 failed =
# ordering-absence[orchestrate.md]; cell 2 — full retired rendering
# wake-`SendMessage` FIRST, then `TaskUpdate(A, ...)`: NEW matcher exactly
# 2 failed (ordering-absence + command-form, both orchestrate.md), OLD
# matcher exactly 1 failed (command-form only — the measured pre-change
# blindness). Heading/slug/anchor/INBOX_GREW/wake-send pins match raw
# text or pure functions by design and are untouched.
# ---------------------------------------------------------------------------
