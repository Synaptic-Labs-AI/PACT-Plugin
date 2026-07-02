"""
Structural pin tests for the wake/idle message-ordering hardening rules.

Pins the reader-facing instruction surfaces that teach both sides of the
wake/idle delivery-ordering race:

  teammate-side (skills/pact-agent-teams/SKILL.md):
    - "On Wake: Disk-First Re-Read (Seam-Agnostic)" — durable state is
      authoritative on every wake; message content is advisory.
    - "Counter-Confirm Suppression" — fresh disk read before any
      state-clarification message; suppress when already resolved.
    - "Boundary-Drain Rule" — inbox drain + drain report before every
      protocol-boundary message.
  lead-side (protocols/pact-completion-authority.md + its byte-mirrored
  region in protocols/pact-protocols.md, and agents/pact-orchestrator.md):
    - "Crossed-Wake Idles: One Redundant Confirm, Then Stop" — including
      the behavioral non-goal note that synchronous wake/send detection is
      dead-by-construction.
    - "Directive-Reflection Check" — mid-turn directives verified against
      boundary-message deliverables before acting.
  stall-detection (protocols/pact-agent-stall.md + its byte-mirrored
  region in pact-protocols.md):
    - post-wake / live-intentional_wait idles are delivery-ordering
      artifacts, not stalls.

PRESENCE pins, not counts. Unlike the Read-Trigger marker phrase (see
test_read_trigger_precondition_pinned.py EXPECTED_COUNTS), none of these
phrases is intended to recur a fixed number of times per surface, so count
pins would add lockstep-maintenance cost without catching a real erosion
shape. No new EXPECTED_COUNTS-style lockstep is introduced by this module.

PHRASES, not line shapes. Several pinned sentences are hard-wrapped in the
shipped markdown and several rule sentences share a line with pre-existing
list-item text (semantically identical markdown renderings). Phrase pins
therefore match against whitespace-NORMALIZED text (`" ".join(text.split())`)
so they survive re-wrapping; heading pins match line-anchored raw lines
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
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

SKILL = PLUGIN_ROOT / "skills" / "pact-agent-teams" / "SKILL.md"
COMPLETION_AUTHORITY = PLUGIN_ROOT / "protocols" / "pact-completion-authority.md"
PROTOCOLS_SSOT = PLUGIN_ROOT / "protocols" / "pact-protocols.md"
ORCHESTRATOR = PLUGIN_ROOT / "agents" / "pact-orchestrator.md"
AGENT_STALL = PLUGIN_ROOT / "protocols" / "pact-agent-stall.md"

ALL_SURFACES = [SKILL, COMPLETION_AUTHORITY, PROTOCOLS_SSOT, ORCHESTRATOR, AGENT_STALL]


def _raw(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(path: Path) -> str:
    """Whitespace-normalized file text: any run of whitespace (including
    newlines from hard-wrapping) collapses to a single space. Phrase pins
    match against this so an intentional re-wrap of a rule sentence does
    not fail the pin while a re-WORD still does."""
    return " ".join(_raw(path).split())


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
    (COMPLETION_AUTHORITY, "### Crossed-Wake Idles: One Redundant Confirm, Then Stop"),
    (COMPLETION_AUTHORITY, "### Directive-Reflection Check"),
    (PROTOCOLS_SSOT, "### Crossed-Wake Idles: One Redundant Confirm, Then Stop"),
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
    # The load-bearing interpretation rule for every wake.
    (SKILL, "Durable state is authoritative"),
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
    # span exists only at the rule home.
    (SKILL, "re-read once after a brief pause; never act on a single empty read"),
    # The crossed mid-turn directive rule: an already-submitted deliverable
    # that reflects pre-directive scope is revised proactively, not on
    # request.
    (SKILL, "revise it on the same task without waiting to be asked"),
    # --- lead-side completion-authority (+ byte-mirrored SSOT region) ---
    (COMPLETION_AUTHORITY, "exactly ONE redundant confirm"),
    # The behavioral no-hook non-goal note: the race cannot be closed with
    # a synchronous hook (SendMessage fires no hook event; inbox writes are
    # asynchronous-on-delivery; idle notifications are content-blind).
    (COMPLETION_AUTHORITY, "Synchronous wake/send detection is therefore dead-by-construction"),
    (COMPLETION_AUTHORITY, "in-process and tmux teammateMode"),
    (COMPLETION_AUTHORITY, "boundary-drain: inbox empty"),
    # The evidence bar for escalating an idle to stall diagnosis.
    (COMPLETION_AUTHORITY, "task-file-mtime plus sustained-silence"),
    # The directive-reflection aphorism naming the failure mode the check
    # exists for. Matching is case-sensitive by design; this surface carries
    # the sentence-initial capitalized form.
    (COMPLETION_AUTHORITY, "Delivery is not processing"),
    (PROTOCOLS_SSOT, "exactly ONE redundant confirm"),
    (PROTOCOLS_SSOT, "Synchronous wake/send detection is therefore dead-by-construction"),
    (PROTOCOLS_SSOT, "in-process and tmux teammateMode"),
    (PROTOCOLS_SSOT, "boundary-drain: inbox empty"),
    (PROTOCOLS_SSOT, "task-file-mtime plus sustained-silence"),
    (PROTOCOLS_SSOT, "Delivery is not processing"),
    # --- orchestrator persona ---
    (ORCHESTRATOR, "exactly ONE redundant confirm"),
    # The Wait-in-Silence rule's single protocol-defined exception — without
    # it, the persona's no-reply-to-idle-turns reflex suppresses the one
    # legitimate redundant confirm.
    (ORCHESTRATOR, "the single redundant confirm after a crossed wake"),
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
    (PROTOCOLS_SSOT, "delivery-ordering artifacts, not stalls"),
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
    normalized_phrase = " ".join(phrase.split())
    assert normalized_phrase in _normalized(doc_path), (
        f"{doc_path.name}: rule phrase {phrase!r} not found "
        f"(whitespace-normalized match). If the wording was changed "
        f"intentionally, update this pin in lockstep; otherwise the "
        f"wake-ordering rule this phrase carries is missing from a "
        f"runtime-loaded surface."
    )


# ---------------------------------------------------------------------------
# Cross-ref pins — literal anchor slugs (what markdown actually navigates to).
# ---------------------------------------------------------------------------

CROSSED_WAKE_SLUG = "#crossed-wake-idles-one-redundant-confirm-then-stop"
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
    ("### Crossed-Wake Idles: One Redundant Confirm, Then Stop", CROSSED_WAKE_SLUG),
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
# ---------------------------------------------------------------------------
