"""
Structural pin tests for the dual-channel acceptance rules.

Pins the reader-facing instruction surfaces that teach both channels of
teachback/HANDOFF acceptance — the notify SendMessage now carries the
canonical payload verbatim (message channel), while the raw task-metadata
read is demoted to the lead's DEFERRED audit (disk channel):

  teammate-side payload encoding (skills/pact-agent-teams/SKILL.md,
  skills/pact-teachback/SKILL.md):
    - HANDOFF-PAYLOAD-BEGIN / TEACHBACK-PAYLOAD-BEGIN delimiter tokens
      (grep-able payload boundaries in the notify templates).
    - The retired one-line-notify renderings stay retired (absence pins).
  lead-side acceptance keying (protocols/pact-completion-authority.md +
    its byte-mirrored region in protocols/pact-protocols.md):
    - T2: accept/reject on the message-carried payload; a missing or
      diverging disk copy is an integrity finding, never a basis for
      rejecting an already-acted-on submission.
    - T1 point 4: the deferred disk-vs-message audit runs at the lead's
      next turn boundary, BEFORE acting on that teammate's next
      submission.
  vocabulary (all six surfaces):
    - "deferred audit" per surface — count-free presence (no fixed
      recurrence intended), like the wake-ordering module's phrase pins.
  dispatch templates (commands/orchestrate.md):
    - both Task-A and Task-B description strings name the payload-carrying
      notify (per-site pins; the two sites carry different full phrases).
  write order + flow surface (added at TEST-phase review):
    - P10: the HANDOFF-path On Completion ordering invariant (metadata
      write → notify → intentional_wait SET) — the teachback-path twin is
      pinned in test_skill_loading_agent_teams.py; the HANDOFF-path triple
      had no pin anywhere before this case.
    - P11: the ct-teachback Flow step-3 payload-carrying-notify reference
      (extract + SSOT mirror) — its lockstep revert kept both this module
      and the byte-mirror gate green before this case.
    - P12: the payload read-back check (verify + mid-JSON write-error
      mechanism) on both teammate notify surfaces — unpinned before this
      case; the dual-channel design carries the payload on the message
      channel AND the metadata channel, and a sender-side output cut can
      truncate either one mid-JSON.
  review-remediation pins (same module, coverage-review fix):
    - P13: the summary-never-carries-payload rule (200 chars is the
      channel's ONLY truncating element) on both teammate surfaces,
      per-surface casing like P5.
    - P14: the payload END delimiter tokens — P1/P2 pin BEGIN only.
    - P15: the OTHER payload type's delimiter ABSENT per surface
      (delimiter confusion: a stray foreign BEGIN alongside the right
      one flips no presence pin).
    - P16: the deferred audit applies identically to the HANDOFF path
      (extract + SSOT mirror).
    - P17: the non-goal span — instruction-layer by necessity; reject
      future proposals to mechanize it (extract + SSOT mirror).
    - P18: the metadata-write-failure fallback — still send the
      payload-carrying notify; missing disk copy is an integrity
      finding, never a skipped submission.
    - P19: the deferred-audit repair clause — re-write from the message
      copy while it is still in your context (extract + SSOT mirror).
      The integrity_finding key NAME is deliberately unpinned: no
      reader exists in hooks or tests today, so the name is
      advisory-only until a consumer lands.
    - P20: the T2 paragraph's OPENING acceptance-keying sentence
      (extract + SSOT mirror) — hardening-audit finding: every other
      sentence of that paragraph had a witness (P3/P4/P5/P19) while the
      core "Accept or reject on the payload carried by the teammate's
      notify SendMessage" sentence had none (stubbing it alone flipped
      nothing, measured).
  rejection-direction pins (fold of the #1540 payload symmetry):
    - P21/P22: REJECTION-PAYLOAD-BEGIN / END token presence on the three
      lead-side template surfaces (completion-authority extract, its
      SSOT mirror, the orchestrator persona). The orchestrate dispatch
      template teaches the payload-carrying send by PHRASE (P23), not
      tokens — it does not carry them.
    - P23: "wake-signal SendMessage carrying the rejection payload
      verbatim" on the orchestrate dispatch template, the persona's
      rejection-path paragraph, and the teachback skill's On-rejection
      cross-ref (one span satisfies all three surfaces).
    - P24: the teammate-side reading instruction — the agent-teams On
      Rejection step 2 header span naming the field-labeled block and
      its four fields.
    - P25: "the disk copy is confirmation, not the primary" (per-surface
      casing: lowercase on completion-authority, the SSOT, and the
      agent-teams intro; sentence-initial capital on the agent-teams
      step 2).
    - P26: the integrity_finding advisory ruling — advisory by design,
      no automated reader consumes it, the lead records and acts.
    - P27: the retired pointer-format SendMessages ("See
      metadata.teachback_rejection" / "See metadata.handoff_rejection")
      absent from every LLM-loaded instruction surface, tree-wide.

Inherited vocabulary guard (P9, no new case here): the NEW lead-side
prose is covered by the existing retired-"wake-send"/"wake send" absence
pin in test_wake_ordering_pinned.py, already parametrized over
completion-authority, the SSOT region, and the orchestrator persona —
the three surfaces this change edits lead-side. Reintroducing the retired
send-term through the new wording trips that pin; nothing here duplicates
it.

PRESENCE pins, not counts. None of these phrases is intended to recur a
fixed number of times per surface, so count pins would add
lockstep-maintenance cost without catching a real erosion shape. (The
Read-Trigger marker phrase keeps its own EXPECTED_COUNTS lockstep in
test_read_trigger_precondition_pinned.py; this change deliberately does
NOT quote that phrase — paraphrase keeps those counts byte-stable.)

PHRASES, not line shapes. Matching is backtick-and-whitespace-normalized
(see _phrase: strip backticks, then " ".join(text.split())) per the
wake-ordering convention — re-wrapping and inline-code rendering survive,
a re-WORD does not. Matching is CASE-SENSITIVE by design; the "deferred
audit" pin set uses per-surface casing (the completion-authority extract
and its SSOT mirror carry the T2 sentence's uppercase "DEFERRED audit",
every other surface carries the lowercase mid-sentence form) — do not
normalize case to unify them.

P4 anchoring rationale (phantom-green guard, same shape as the
wake-ordering postdating pin): the T2 sentence on the same surface
contains the LOWERCASE span "before acting on that teammate's next
submission". A case-insensitive or shorter pin would be satisfied by T2
alone and stay green if T1's point 4 regressed; the uppercase anchored
long form exists only at point 4.

Counter-test-by-revert (verified at authoring time, measured in a TEMP
COPY of the plugin tree — the live worktree is never mutated): with each
edited file individually restored to its pre-change state
(git show <pre-change-ref>:<path> over the copy) and this module run
against the copy, exactly that file's P-cases fail. The measured flip-set
per file and the total are recorded in the module-level comment at the
bottom of this file.
"""

import re
from pathlib import Path

import subprocess
import pytest

from fixtures.tracked_files import tracked_glob as _tracked_glob

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

AGENT_TEAMS_SKILL = PLUGIN_ROOT / "skills" / "pact-agent-teams" / "SKILL.md"
TEACHBACK_SKILL = PLUGIN_ROOT / "skills" / "pact-teachback" / "SKILL.md"
COMPLETION_AUTHORITY = PLUGIN_ROOT / "protocols" / "pact-completion-authority.md"
PROTOCOLS_SSOT = PLUGIN_ROOT / "protocols" / "pact-protocols.md"
ORCHESTRATOR = PLUGIN_ROOT / "agents" / "pact-orchestrator.md"
ORCHESTRATE = PLUGIN_ROOT / "commands" / "orchestrate.md"


def _raw(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _phrase(text: str) -> str:
    """Phrase-matching normalization: strip backticks, then collapse any
    whitespace run to a single space. Tool language is inline-code
    formatted in the shipped markdown, so a pinned phrase must match a
    subject rendered with backticks inside the phrase span. Applied to
    BOTH sides (file text and pinned phrase) per the wake-ordering
    module's convention."""
    return " ".join(text.replace("`", "").split())


def _normalized(path: Path) -> str:
    return _phrase(_raw(path))


# ---------------------------------------------------------------------------
# P1/P2 — payload delimiter tokens (teammate-side notify templates).
# ---------------------------------------------------------------------------

PAYLOAD_DELIMITER_PINS = [
    (AGENT_TEAMS_SKILL, "HANDOFF-PAYLOAD-BEGIN"),
    (TEACHBACK_SKILL, "TEACHBACK-PAYLOAD-BEGIN"),
]


@pytest.mark.parametrize(
    "doc_path, token",
    PAYLOAD_DELIMITER_PINS,
    ids=[f"{p.name}::{t}" for p, t in PAYLOAD_DELIMITER_PINS],
)
def test_payload_delimiter_present(doc_path: Path, token: str):
    """The notify template must carry its payload's grep-able BEGIN
    delimiter — the boundary marker the lead's message-side acceptance
    keys on. Delimiters are plain text (no backtick or wrap variance), so
    normalized matching is equivalent to raw here; the normalized form is
    used for uniformity."""
    assert _phrase(token) in _normalized(doc_path), (
        f"{doc_path.name}: payload delimiter {token!r} not found. The "
        f"notify template must carry the canonical payload verbatim inside "
        f"its BEGIN/END delimiters; if the encoding was changed "
        f"intentionally, update this pin in lockstep."
    )


# ---------------------------------------------------------------------------
# P3 — T2 acceptance-keying sentence (extract + byte-mirrored SSOT region).
# ---------------------------------------------------------------------------

T2_PHRASES = [
    # The classification sentence: divergence or a missing disk copy is an
    # integrity finding ("data-integrity finding" carries the pinned span).
    "integrity finding",
    # The never-a-rejection clause: the deferred audit must not convert a
    # disk/message divergence into a false rejection of an acted-on
    # submission.
    "never a basis for rejecting",
]

T2_SURFACES = [COMPLETION_AUTHORITY, PROTOCOLS_SSOT]

T2_PINS = [(p, ph) for p in T2_SURFACES for ph in T2_PHRASES]


@pytest.mark.parametrize(
    "doc_path, phrase",
    T2_PINS,
    ids=[f"{p.name}::{ph[:40]}" for p, ph in T2_PINS],
)
def test_t2_acceptance_keying_phrase_present(doc_path: Path, phrase: str):
    """The T2 rewrite keys acceptance on the message-carried payload and
    classifies disk/message divergence as an integrity finding — never as
    a rejection basis. Pinned on the extract AND its byte-mirrored SSOT
    region: a lockstep revert of the pair leaves them mutually
    byte-equal (the protocol-extract audit stays green), so each surface
    gets its own witness.

    Hardening-audit note (per-pin occurrence census): phrase-1
    ("integrity finding") occurs TWICE per mirrored surface — the T2
    sentence and Read-Trigger point 4 — so it is pair-level protection,
    not T2-anchored (deleting only T2's occurrence leaves it green; the
    opening sentence carries its own witness at P20, and the paragraph's
    later sentences at P3 phrase-2 + P19). LEAVE as designed."""
    assert _phrase(phrase) in _normalized(doc_path), (
        f"{doc_path.name}: T2 acceptance-keying phrase {phrase!r} not "
        f"found. The lead accepts on the payload the notify carries; a "
        f"missing or diverging disk copy is an integrity finding, never "
        f"a rejection. If reworded intentionally, update this pin in "
        f"lockstep on both mirrored surfaces."
    )


# ---------------------------------------------------------------------------
# P20 — T2 opening acceptance-keying sentence (extract + SSOT mirror).
# ---------------------------------------------------------------------------

# The T2 paragraph's FIRST sentence — the core accept-on-message keying —
# had no witness of its own (hardening-audit finding, measured: stubbing
# just this sentence flipped nothing suite-wide while P3/P4/P5/P19 all
# anchor LATER sentences of the same paragraph). The anchored span covers
# the instruction and its demonstrability rationale; a shorter
# "Accept or reject on the payload" would be satisfiable by paraphrase
# elsewhere on lead surfaces.
T2_OPENING_PHRASE = (
    "Accept or reject on the payload carried by the teammate's notify "
    "SendMessage"
)

T2_OPENING_SURFACES = [COMPLETION_AUTHORITY, PROTOCOLS_SSOT]


@pytest.mark.parametrize(
    "doc_path",
    T2_OPENING_SURFACES,
    ids=lambda p: p.name,
)
def test_t2_opening_acceptance_keying_pinned(doc_path: Path):
    """The Completion-Authority section must keep opening its acceptance
    rule with the message-carried-payload keying — the sentence the whole
    dual-channel contract hangs off. Later sentences of the paragraph
    carry the deferred-audit classification (P3/P5), the trigger (P4),
    and the repair (P19); this pin closes the opening sentence's gap."""
    assert _phrase(T2_OPENING_PHRASE) in _normalized(doc_path), (
        f"{doc_path.name}: T2 opening acceptance-keying sentence "
        f"{T2_OPENING_PHRASE!r} not found. The lead accepts or rejects on "
        f"the payload the notify carries; if reworded intentionally, update "
        f"this pin in lockstep on both mirrored surfaces."
    )


# ---------------------------------------------------------------------------
# P4 — T1 point 4 deferred-audit trigger (anchored long form).
# ---------------------------------------------------------------------------

P4_TRIGGER = "BEFORE acting on that teammate's next submission"

P4_SURFACES = [COMPLETION_AUTHORITY, PROTOCOLS_SSOT]


@pytest.mark.parametrize("doc_path", P4_SURFACES, ids=lambda p: p.name)
def test_t1_deferred_audit_trigger_present(doc_path: Path):
    """The deferred-audit deadline: the disk-vs-message comparison runs at
    the lead's next turn boundary, BEFORE acting on that teammate's next
    submission. The anchored uppercase long form exists only at T1's
    point 4 — the T2 sentence on the same surface carries the lowercase
    span, which would phantom-green a case-insensitive or shorter pin
    (see module docstring)."""
    assert _phrase(P4_TRIGGER) in _normalized(doc_path), (
        f"{doc_path.name}: deferred-audit trigger {P4_TRIGGER!r} not "
        f"found. The audit deadline is pinned to the teammate's next "
        f"submission; if the trigger was reworded intentionally, update "
        f"this pin in lockstep on both mirrored surfaces."
    )


# ---------------------------------------------------------------------------
# P5 — "deferred audit" vocabulary, count-free presence per surface.
# ---------------------------------------------------------------------------

# Per-surface casing is deliberate (see module docstring): the extract
# and its SSOT mirror carry the T2 sentence's uppercase "DEFERRED audit";
# every other surface carries the lowercase mid-sentence form.
DEFERRED_AUDIT_PINS = [
    (COMPLETION_AUTHORITY, "DEFERRED audit"),
    (PROTOCOLS_SSOT, "DEFERRED audit"),
    (ORCHESTRATOR, "deferred audit"),
    (AGENT_TEAMS_SKILL, "deferred audit"),
    (TEACHBACK_SKILL, "deferred audit"),
]


@pytest.mark.parametrize(
    "doc_path, phrase",
    DEFERRED_AUDIT_PINS,
    ids=[f"{p.name}::{ph}" for p, ph in DEFERRED_AUDIT_PINS],
)
def test_deferred_audit_vocabulary_present(doc_path: Path, phrase: str):
    """Every surface this change touched must keep teaching the
    disk-read's new role by name — the deferred audit — so no reader-
    facing surface silently reverts to acceptance-time raw reads while
    others move on. Count-free: the vocabulary legitimately recurs a
    variable number of times per surface; only its presence is the
    contract.

    Hardening-audit ruling (occurrence census, leave-as-designed): the
    lowercase form recurs 5x on the orchestrator persona (both Q-table
    rows, the metadata-blindness reminder, Teachback Review, the HANDOFF
    format) and 4x on the agent-teams skill (ordering invariant, Step-4
    idle, awaiting-lead bullet, and the write-order context) — every
    occurrence is a legitimate teaching site, none is a mere decoy, so
    locality is deliberately delegated to the anchored sibling pins
    (P3/P4/P16/P17/P19/P20 lead-side; P10/P12/P13/P14/P18 teammate-
    side). Measured boundary: removing ALL persona occurrences flips
    this pin; removing a strict subset does not (accepted by the
    presence contract)."""
    assert _phrase(phrase) in _normalized(doc_path), (
        f"{doc_path.name}: deferred-audit vocabulary ({phrase!r}, "
        f"per-surface casing) not found. The surface must name the raw "
        f"read's role as the deferred audit; if the term was retired "
        f"intentionally, update this pin in lockstep across all surfaces."
    )


# ---------------------------------------------------------------------------
# P6/P7 — retired one-line-notify renderings (absence pins).
# ---------------------------------------------------------------------------

RETIRED_FRAMING_PINS = [
    # The teachback Step 2 heading's retired framing: the notify was a
    # lightweight notice, explicitly NOT the payload. The dual-channel
    # design makes the payload-carrying notify the contract; the retired
    # framing must not return as instruction text.
    (TEACHBACK_SKILL, "lightweight prose, NOT the full payload"),
    # The agent-teams HANDOFF Step 2 retired one-line-notify format
    # ("1-2 sentences: what was done"). The template now carries the
    # HANDOFF payload verbatim; the one-line summary slot must not be
    # taught as the notify's content shape again.
    (AGENT_TEAMS_SKILL, "1-2 sentences: what was done"),
]


@pytest.mark.parametrize(
    "doc_path, retired",
    RETIRED_FRAMING_PINS,
    ids=[f"{p.name}::{r[:40]}" for p, r in RETIRED_FRAMING_PINS],
)
def test_retired_one_line_notify_framing_absent(doc_path: Path, retired: str):
    """Regression guard: the notify is payload-carrying by contract.
    Reintroducing either retired rendering — the "lightweight prose"
    framing on the teachback step or the "1-2 sentences" content shape on
    the HANDOFF step — would resurrect the one-line notify as taught
    behavior on a runtime-loaded surface. Normalized matching so a
    re-wrapped or backticked reintroduction cannot slip past."""
    assert _phrase(retired) not in _normalized(doc_path), (
        f"{doc_path.name}: retired notify framing {retired!r} present. "
        f"The notify carries the canonical payload verbatim; the one-line "
        f"notice is the retired shape (if a future rule genuinely needs "
        f"it, update this guard deliberately)."
    )


# ---------------------------------------------------------------------------
# P8 — dispatch-template payload-carrying notify (orchestrate.md, per site).
# ---------------------------------------------------------------------------

# The two sites carry different full phrases (Task-A names the teachback
# payload; Task-B names the HANDOFF payload), so each site gets its own
# anchored pin — a revert of either description string flips exactly its
# own case.
ORCHESTRATE_PINS = [
    "send the notify SendMessage carrying the canonical payload verbatim (pact-teachback Step 2)",
    "send notify SendMessage carrying the canonical HANDOFF payload (pact-agent-teams On Completion Step 2)",
]


@pytest.mark.parametrize("phrase", ORCHESTRATE_PINS, ids=lambda ph: ph[:48])
def test_orchestrate_dispatch_names_payload_carrying_notify(phrase: str):
    """Both dispatch description strings name the payload-carrying notify
    (and the Task-A string teaches notify-before-wait, matching the
    skill's ordering invariant). The commands are templates the lead
    copies at dispatch time; a template that reverts to the bare
    "send notify SendMessage" wording dispatches teammates who never
    carry the payload."""
    assert _phrase(phrase) in _normalized(ORCHESTRATE), (
        f"orchestrate.md: dispatch-template phrase {phrase!r} not found. "
        f"The dispatch text must name the payload-carrying notify; if the "
        f"template was reworded intentionally, update this pin in "
        f"lockstep."
    )


# ---------------------------------------------------------------------------
# P10 — HANDOFF-path teammate write order (On Completion ordering invariant).
# ---------------------------------------------------------------------------

# The teachback-path write order (metadata write → SendMessage →
# intentional_wait SET) is pinned inline by test_skill_loading_agent_teams.py
# (TestOnStartTeachbackGateSendMessageVisible). The HANDOFF-path twin — the
# On Completion ordering-invariant blockquote — had no pin anywhere: deleting
# the whole triple from that blockquote left every plausible guarding module
# green (measured: test_skill_loading_agent_teams.py,
# test_dual_channel_acceptance_pinned.py, test_agents_structure.py,
# test_wake_ordering_pinned.py — all green with the triple removed). This
# case closes that gap; the acceptance contract "teammate write order
# unchanged" names exactly this sequence.
WRITE_ORDER_PINS = [
    (
        AGENT_TEAMS_SKILL,
        "metadata.handoff write FIRST, then notify SendMessage to "
        "team-lead, then intentional_wait SET",
    ),
]


@pytest.mark.parametrize(
    "doc_path, phrase",
    WRITE_ORDER_PINS,
    ids=[f"{p.name}::write-order" for p, _ in WRITE_ORDER_PINS],
)
def test_handoff_path_write_order_pinned(doc_path: Path, phrase: str):
    """The On Completion ordering invariant must keep naming the full
    three-step sequence — metadata.handoff write FIRST, then the notify
    SendMessage, then the intentional_wait SET. The dual-channel change
    edits this exact blockquote (it appends the payload-carrying clauses);
    nothing else in the suite guards the HANDOFF-path ordering, so a reword
    that drops the triple would silently unpin the teammate write order this
    arc's acceptance criteria require to stay unchanged."""
    assert _phrase(phrase) in _normalized(doc_path), (
        f"{doc_path.name}: HANDOFF-path write-order triple {phrase!r} not "
        f"found in the On Completion ordering invariant. The teammate write "
        f"order (metadata write → notify → intentional_wait SET) must stay "
        f"pinned; if the invariant was reworded intentionally, update this "
        f"pin in lockstep."
    )


# ---------------------------------------------------------------------------
# P11 — ct-teachback Flow step 3 notify wording (extract + SSOT mirror).
# ---------------------------------------------------------------------------

# The ct-teachback extract teaches the teachback notify shape to protocol
# readers and was edited by this arc (its retired one-line notify template
# became the payload-carrying reference). Measured gap before this pin: a
# revert of that edit — extract and SSOT region together, which keeps the
# byte-mirror gate green — left this module and the mirror gate green. Both
# surfaces are pinned (same lockstep-revert rationale as T2/P4/P5).
CT_TEACHBACK_NOTIFY_PHRASE = (
    "carrying the canonical payload verbatim (per pact-teachback Step 2)"
)

CT_TEACHBACK_SURFACES = [
    PLUGIN_ROOT / "protocols" / "pact-ct-teachback.md",
    PROTOCOLS_SSOT,
]


@pytest.mark.parametrize(
    "doc_path",
    CT_TEACHBACK_SURFACES,
    ids=lambda p: p.name,
)
def test_ct_teachback_flow_names_payload_carrying_notify(doc_path: Path):
    """The ct-teachback Flow step 3 must keep directing the reader to the
    payload-carrying notify (pact-teachback Step 2), not back to the retired
    one-line notice. The extract and its byte-mirrored SSOT region each get
    a witness so a lockstep revert of the pair — which satisfies the
    protocol-extract mirror gate — still trips this pin."""
    assert (
        _phrase(CT_TEACHBACK_NOTIFY_PHRASE) in _normalized(doc_path)
    ), (
        f"{doc_path.name}: ct-teachback Flow step-3 payload-carrying notify "
        f"phrase {CT_TEACHBACK_NOTIFY_PHRASE!r} not found. The Flow must "
        f"reference the payload-carrying notify, not the retired one-line "
        f"notice; if reworded intentionally, update this pin in lockstep on "
        f"both mirrored surfaces."
    )


# ---------------------------------------------------------------------------
# P12 — payload read-back check (both teammate notify surfaces).
# ---------------------------------------------------------------------------

# The read-back check bounds the real hazard at any payload size. The
# earlier form of this pin required a "<5KB" bound that was never measured
# and is false: six writes this session ran 7132-14000 bytes and every one
# was verified intact by reading the stored JSON back. A SIZE claim cannot
# be pinned without measuring it, so the pin now anchors the VERIFICATION
# instead. The anchored span covers both the instruction and its mechanism;
# "read the task JSON back" alone would stay green under a reword that kept
# the action but dropped the mid-JSON write-error reason.
READBACK_PINS = [
    (
        AGENT_TEAMS_SKILL,
        "read the task JSON back and confirm every field is present, "
        "non-empty, and ends on its intended final content — a sender-side "
        "output cut lands mid-JSON and surfaces as a write error, not as "
        "silence",
    ),
    (
        TEACHBACK_SKILL,
        "read the task JSON back and confirm every field is present, "
        "non-empty, and ends on its intended final content — a sender-side "
        "output cut lands mid-JSON and surfaces as a write error, not as "
        "silence",
    ),
]


@pytest.mark.parametrize(
    "doc_path, phrase",
    READBACK_PINS,
    ids=[f"{p.name}::readback" for p, _ in READBACK_PINS],
)
def test_payload_readback_check_pinned(doc_path: Path, phrase: str):
    """Both teammate notify templates must keep teaching the payload
    read-back check — the dual-channel design carries the payload on two
    channels, and the failure that actually loses a payload is a
    sender-side output cut landing mid-JSON, which surfaces as a write
    error rather than as silence. Dropping the read-back instruction
    leaves that failure undetected at every size."""
    assert _phrase(phrase) in _normalized(doc_path), (
        f"{doc_path.name}: payload read-back check {phrase!r} not "
        f"found. The notify template must keep the read-back instruction "
        f"with its mid-JSON write-error mechanism; if it was reworded "
        f"intentionally, update this pin in lockstep on both surfaces."
    )


# ---------------------------------------------------------------------------
# P13 — summary never carries payload content (both teammate surfaces).
# ---------------------------------------------------------------------------

# The 200-char truncation is the message channel's ONLY measured
# truncation (the body is non-truncating through 32KB). The rule keeps
# payload content off the summary, where it would be cut at 200 chars
# with no error on either channel — and the disk write still succeeds,
# so the deferred audit would not flag the loss either. Unpinned before
# this case: deleting the sentence from either surface flipped nothing
# suite-wide. Per-surface casing is deliberate (see module docstring):
# the agent-teams blockquote carries the sentence-initial capital, the
# teachback blockquote the mid-sentence lowercase after "space-joined;".
SUMMARY_RULE_PINS = [
    (
        AGENT_TEAMS_SKILL,
        "The summary never carries payload content (it truncates at 200 "
        "chars)",
    ),
    (
        TEACHBACK_SKILL,
        "the summary never carries payload content (it truncates at 200 "
        "chars)",
    ),
]


@pytest.mark.parametrize(
    "doc_path, phrase",
    SUMMARY_RULE_PINS,
    ids=[f"{p.name}::summary-rule" for p, _ in SUMMARY_RULE_PINS],
)
def test_summary_never_carries_payload(doc_path: Path, phrase: str):
    """The notify template's summary slot must stay payload-free — the
    summary truncates at 200 chars, the only truncating element of the
    message channel. Payload content moved into the summary is silently
    lost past 200 chars on the message side while the disk copy stays
    complete, so no channel of the pair surfaces the loss."""
    assert _phrase(phrase) in _normalized(doc_path), (
        f"{doc_path.name}: summary-payload rule {phrase!r} not found. The "
        f"summary truncates at 200 chars — payload content belongs in the "
        f"message body; if reworded intentionally, update this pin in "
        f"lockstep (per-surface casing)."
    )


# ---------------------------------------------------------------------------
# P14 — payload END delimiter tokens (both teammate notify surfaces).
# ---------------------------------------------------------------------------

# P1/P2 pin only the BEGIN tokens; the END delimiters were unpinned
# before these cases: deleting either END token flipped nothing. The
# lead's message-side parsing keys on the BEGIN/END frame — a template
# that drops its END ships an unframed payload.
PAYLOAD_END_DELIMITER_PINS = [
    (AGENT_TEAMS_SKILL, "HANDOFF-PAYLOAD-END"),
    (TEACHBACK_SKILL, "TEACHBACK-PAYLOAD-END"),
]


@pytest.mark.parametrize(
    "doc_path, token",
    PAYLOAD_END_DELIMITER_PINS,
    ids=[f"{p.name}::{t}" for p, t in PAYLOAD_END_DELIMITER_PINS],
)
def test_payload_end_delimiter_present(doc_path: Path, token: str):
    """The notify template must close its payload frame with the matching
    END delimiter — the BEGIN token alone does not bound the payload the
    lead parses. Delimiters are plain text (no backtick or wrap
    variance), so normalized matching is equivalent to raw here; the
    normalized form is used for uniformity with P1/P2."""
    assert _phrase(token) in _normalized(doc_path), (
        f"{doc_path.name}: payload END delimiter {token!r} not found. The "
        f"notify template must close the payload frame it opens; if the "
        f"encoding was changed intentionally, update this pin in lockstep "
        f"with P1/P2."
    )


# ---------------------------------------------------------------------------
# P15 — wrong-payload delimiter absent per surface (delimiter confusion).
# ---------------------------------------------------------------------------

# Each notify template must use ONLY its own payload type's delimiters.
# A copy-paste of the wrong template (or a stray foreign BEGIN pasted
# into prose) leaves both delimiter families on one surface; the
# presence pins (P1/P2/P14) stay green because the right tokens are
# still there — measured before this case: a stray
# HANDOFF-PAYLOAD-BEGIN added to the teachback skill flipped nothing
# suite-wide. Like P6/P7, these absence pins flip under the
# re-introduction mutation, not under file revert (the foreign token
# never existed pre-change).
WRONG_DELIMITER_PINS = [
    (TEACHBACK_SKILL, "HANDOFF-PAYLOAD-BEGIN"),
    (AGENT_TEAMS_SKILL, "TEACHBACK-PAYLOAD-BEGIN"),
]


@pytest.mark.parametrize(
    "doc_path, token",
    WRONG_DELIMITER_PINS,
    ids=[f"{p.name}::not-{t}" for p, t in WRONG_DELIMITER_PINS],
)
def test_wrong_payload_delimiter_absent(doc_path: Path, token: str):
    """The surface must not carry the OTHER payload type's delimiter. A
    stray foreign delimiter on a teammate surface teaches an ambiguous
    frame the lead could mis-key on; if a future edit genuinely needs to
    reference the other channel's delimiter in prose, update this guard
    deliberately (same convention as the P6/P7 absence pins)."""
    assert _phrase(token) not in _normalized(doc_path), (
        f"{doc_path.name}: foreign payload delimiter {token!r} present. "
        f"Each notify template uses only its own payload type's "
        f"BEGIN/END delimiters; a stray foreign delimiter makes the "
        f"payload frame ambiguous."
    )


# ---------------------------------------------------------------------------
# P16 — deferred audit applies to the HANDOFF path (extract + SSOT).
# ---------------------------------------------------------------------------

# Without this sentence the deferred audit reads teachback-path-only:
# the Read-Trigger Precondition's context is teachback-arrival-keyed,
# and nothing else in point 4 names the HANDOFF path. Unpinned before
# this case: deleting the sentence from both mirrored surfaces flipped
# nothing, byte-parity gate included.
HANDOFF_EXT_PHRASE = (
    "The deferred audit of point 4 applies identically to the HANDOFF "
    "path"
)

HANDOFF_EXT_SURFACES = [COMPLETION_AUTHORITY, PROTOCOLS_SSOT]


@pytest.mark.parametrize(
    "doc_path",
    HANDOFF_EXT_SURFACES,
    ids=lambda p: p.name,
)
def test_deferred_audit_covers_handoff_path(doc_path: Path):
    """The deferred disk-vs-message audit must explicitly cover the
    HANDOFF path, not only teachback arrivals. Both mirrored surfaces
    carry their own witness (same lockstep-revert rationale as
    T2/P4/P5/P11: a lockstep revert of the pair keeps the byte-mirror
    gate green)."""
    assert _phrase(HANDOFF_EXT_PHRASE) in _normalized(doc_path), (
        f"{doc_path.name}: HANDOFF-path deferred-audit extension "
        f"{HANDOFF_EXT_PHRASE!r} not found. The deferred audit is not "
        f"teachback-only; if reworded intentionally, update this pin in "
        f"lockstep on both mirrored surfaces."
    )


# ---------------------------------------------------------------------------
# P17 — non-goal: hook-based content comparison is dead-by-construction.
# ---------------------------------------------------------------------------

# The layer ruling: the delivered message drains from disk on recipient
# consumption and survives only in the recipient's conversation context,
# so no hook running at a later turn boundary can read the message
# bytes. The non-goal exists so the dead-by-construction comparison is
# not re-attempted. Unpinned before this case: deleting the sentence
# from both mirrored surfaces flipped nothing.
NON_GOAL_PHRASE = (
    "the deferred disk-vs-message audit is instruction-layer by "
    "necessity; reject future proposals to mechanize it"
)

NON_GOAL_SURFACES = [COMPLETION_AUTHORITY, PROTOCOLS_SSOT]


@pytest.mark.parametrize(
    "doc_path",
    NON_GOAL_SURFACES,
    ids=lambda p: p.name,
)
def test_non_goal_mechanization_ban_pinned(doc_path: Path):
    """The non-goal sentence must keep both halves: the classification
    (instruction-layer by necessity) and the ban (reject future
    proposals to mechanize it). Losing the sentence invites a future
    re-attempt at a hook that cannot work on the storage model."""
    assert _phrase(NON_GOAL_PHRASE) in _normalized(doc_path), (
        f"{doc_path.name}: deferred-audit non-goal span "
        f"{NON_GOAL_PHRASE!r} not found. Hook-based content comparison "
        f"is dead-by-construction and must stay recorded as rejected; if "
        f"reworded intentionally, update this pin in lockstep on both "
        f"mirrored surfaces."
    )


# ---------------------------------------------------------------------------
# P18 — metadata-write-failure fallback (agent-teams HANDOFF Step 1).
# ---------------------------------------------------------------------------

# The dual-channel design's failure path: a failed metadata write must
# not suppress the submission. The teammate still sends the
# payload-carrying notify and states the write failure in it; the lead
# treats the missing disk copy as an integrity finding. Unpinned before
# this case: reverting to the retired fallback wording ("include the
# full HANDOFF in your SendMessage content as a fallback") flipped
# nothing.
WRITE_FAILURE_FALLBACK_PINS = [
    (
        AGENT_TEAMS_SKILL,
        "still send the payload-carrying notify and state the write "
        "failure in it; the lead treats the missing disk copy as an "
        "integrity finding, never as a reason to skip your submission",
    ),
]


@pytest.mark.parametrize(
    "doc_path, phrase",
    WRITE_FAILURE_FALLBACK_PINS,
    ids=[f"{p.name}::write-failure-fallback" for p, _ in WRITE_FAILURE_FALLBACK_PINS],
)
def test_write_failure_fallback_pinned(doc_path: Path, phrase: str):
    """A failed metadata write must not suppress the submission — the
    notify still carries the payload and names the failure, and the
    missing disk copy is classified lead-side as an integrity finding.
    The anchored span covers both the action (still send) and the
    classification (never a skipped submission); a shorter pin would
    stay green under a reword that kept the sending but restored the
    retired fallback framing."""
    assert _phrase(phrase) in _normalized(doc_path), (
        f"{doc_path.name}: write-failure fallback {phrase!r} not found. "
        f"A failed metadata write must not suppress the submission; if "
        f"reworded intentionally, update this pin in lockstep."
    )


# ---------------------------------------------------------------------------
# P19 — deferred-audit repair clause (extract + SSOT mirror).
# ---------------------------------------------------------------------------

# When the deferred audit finds the disk copy absent, the lead repairs
# it by re-writing the payload to metadata FROM the message copy while
# it is still in context — the timing qualifier is load-bearing (the
# message copy has no durable on-disk home; after context loss the
# repair is impossible). The integrity_finding key NAME is deliberately
# NOT pinned here: no reader exists in hooks or tests today, so the
# name is advisory-only until a consumer lands. Unpinned before this
# case: deleting the clause from both mirrored surfaces flipped
# nothing.
REPAIR_CLAUSE_PHRASE = (
    "repair it by re-writing the payload to metadata from the message "
    "copy while it is still in your context"
)

REPAIR_CLAUSE_SURFACES = [COMPLETION_AUTHORITY, PROTOCOLS_SSOT]


@pytest.mark.parametrize(
    "doc_path",
    REPAIR_CLAUSE_SURFACES,
    ids=lambda p: p.name,
)
def test_repair_clause_pinned(doc_path: Path):
    """The deferred audit's repair action must keep its full form —
    re-write the payload to metadata from the message copy, while it is
    still in your context. Dropping the timing qualifier leaves an
    instruction that cannot be executed after context loss."""
    assert _phrase(REPAIR_CLAUSE_PHRASE) in _normalized(doc_path), (
        f"{doc_path.name}: deferred-audit repair clause "
        f"{REPAIR_CLAUSE_PHRASE!r} not found. The repair re-writes the "
        f"payload from the message copy while it is still in context; if "
        f"reworded intentionally, update this pin in lockstep on both "
        f"mirrored surfaces."
    )


# ---------------------------------------------------------------------------
# P21/P22 — rejection payload delimiter tokens (lead-side templates).
# ---------------------------------------------------------------------------

# The rejection-direction mirror of P1/P2/P14: the lead's rejection
# SendMessage templates must carry the grep-able BEGIN/END frame. On the
# extract and its SSOT mirror the tokens recur 3x each (the two-call
# atomic-pair template plus the teachback and HANDOFF rejection-flow
# blocks) — every occurrence is a template a lead copies, so presence is
# the contract. The persona carries the compact single-line form 1x. The
# orchestrate dispatch template deliberately has NO token pins: it
# teaches the send by phrase (P23), not by the delimiter frame.
REJECTION_DELIMITER_PINS = [
    (COMPLETION_AUTHORITY, "REJECTION-PAYLOAD-BEGIN"),
    (PROTOCOLS_SSOT, "REJECTION-PAYLOAD-BEGIN"),
    (ORCHESTRATOR, "REJECTION-PAYLOAD-BEGIN"),
    (COMPLETION_AUTHORITY, "REJECTION-PAYLOAD-END"),
    (PROTOCOLS_SSOT, "REJECTION-PAYLOAD-END"),
    (ORCHESTRATOR, "REJECTION-PAYLOAD-END"),
]


@pytest.mark.parametrize(
    "doc_path, token",
    REJECTION_DELIMITER_PINS,
    ids=[f"{p.name}::{t}" for p, t in REJECTION_DELIMITER_PINS],
)
def test_rejection_payload_delimiter_present(doc_path: Path, token: str):
    """Every lead-side rejection template surface must carry the
    rejection payload's BEGIN/END delimiter frame — the boundary markers
    the teammate's message-side reading keys on. Delimiters are plain
    text, so normalized matching is equivalent to raw (uniformity with
    P1/P2/P14)."""
    assert _phrase(token) in _normalized(doc_path), (
        f"{doc_path.name}: rejection payload delimiter {token!r} not "
        f"found. The lead's rejection SendMessage must carry the payload "
        f"verbatim inside its BEGIN/END delimiters; if the encoding was "
        f"changed intentionally, update this pin in lockstep."
    )


# ---------------------------------------------------------------------------
# P23 — payload-verbatim send phrase (dispatch template + persona + cross-ref).
# ---------------------------------------------------------------------------

# One span covers all three phrase-teaching surfaces: the orchestrate
# Task-A dispatch description ("wake-signal SendMessage carrying the
# rejection payload verbatim"), the persona's rejection-path paragraph,
# and the teachback skill's On-rejection cross-ref. A dispatch template
# or persona that reverts to the bare "with corrections" wording sends
# leads who never carry the payload.
REJECTION_VERBATIM_PHRASE = (
    "wake-signal SendMessage carrying the rejection payload verbatim"
)

REJECTION_VERBATIM_SURFACES = [ORCHESTRATE, ORCHESTRATOR, TEACHBACK_SKILL]


@pytest.mark.parametrize(
    "doc_path",
    REJECTION_VERBATIM_SURFACES,
    ids=lambda p: p.name,
)
def test_rejection_payload_verbatim_send_named(doc_path: Path):
    """Each surface that teaches the rejection send by PHRASE must keep
    naming the payload-carrying form — the corrections the teammate acts
    on ride the message."""
    assert _phrase(REJECTION_VERBATIM_PHRASE) in _normalized(doc_path), (
        f"{doc_path.name}: rejection payload-carrying phrase "
        f"{REJECTION_VERBATIM_PHRASE!r} not found. The rejection "
        f"wake-signal must be taught as carrying the payload verbatim; "
        f"if reworded intentionally, update this pin in lockstep."
    )


# ---------------------------------------------------------------------------
# P24 — teammate-side rejection reading instruction (agent-teams step 2).
# ---------------------------------------------------------------------------

# The teammate's primary read is now the message-carried payload block;
# the disk copy is confirmation. Two spans, because the step header is
# bold-marked (**...**) and normalization strips backticks, not
# asterisks — the header span sits inside its markers, the block
# reference starts after them. Together they cover the header, the
# delimiter boundaries, and the four fields; a shorter pin would stay
# green under a reword that kept the header but dropped the field map.
REJECTION_READING_HEADER = (
    "Read the rejection payload the wake-signal SendMessage carries"
)
REJECTION_READING_BLOCK_REF = (
    "the field-labeled block between REJECTION-PAYLOAD-BEGIN and "
    "REJECTION-PAYLOAD-END: reason, corrections, since, revision_number"
)


def test_rejection_reading_instruction_pinned():
    for span in (REJECTION_READING_HEADER, REJECTION_READING_BLOCK_REF):
        assert _phrase(span) in _normalized(AGENT_TEAMS_SKILL), (
            f"{AGENT_TEAMS_SKILL.name}: rejection reading instruction "
            f"span {span!r} not found. The teammate reads the rejection "
            f"payload the wake-signal carries; if reworded "
            f"intentionally, update this pin in lockstep."
        )


# ---------------------------------------------------------------------------
# P25 — "the disk copy is confirmation, not the primary" (per-surface casing).
# ---------------------------------------------------------------------------

# The rejection-direction role assignment for the raw read. Per-surface
# casing like P5/P13: the extract, the SSOT, and the agent-teams intro
# carry the lowercase mid-sentence form; the agent-teams step 2 carries
# the sentence-initial capital. Every occurrence is a governing teaching
# site (censused 1x per surface except agent-teams' two casing variants).
CONFIRMATION_PINS = [
    (COMPLETION_AUTHORITY, "the disk copy is confirmation, not the primary"),
    (PROTOCOLS_SSOT, "the disk copy is confirmation, not the primary"),
    (AGENT_TEAMS_SKILL, "the disk copy is confirmation, not the primary"),
    (AGENT_TEAMS_SKILL, "The disk copy is confirmation, not the primary"),
]


@pytest.mark.parametrize(
    "doc_path, phrase",
    CONFIRMATION_PINS,
    ids=[f"{p.name}::{'cap' if ph[0].isupper() else 'low'}-confirmation" for p, ph in CONFIRMATION_PINS],
)
def test_disk_copy_is_confirmation_pinned(doc_path: Path, phrase: str):
    """Each surface teaching the rejection flow must assign the raw read
    its confirmation role — the message-carried payload is the primary.
    A surface that reverts to metadata-as-primary silently re-opens the
    read the dual-channel contract demoted."""
    assert _phrase(phrase) in _normalized(doc_path), (
        f"{doc_path.name}: confirmation-role phrase {phrase!r} not found. "
        f"The disk copy is confirmation, not the primary; if reworded "
        f"intentionally, update this pin in lockstep (per-surface "
        f"casing)."
    )


# ---------------------------------------------------------------------------
# P26 — integrity_finding advisory ruling (extract + SSOT mirror).
# ---------------------------------------------------------------------------

# The fold ruling on the divergence key: advisory by design, no
# automated reader, the lead records and acts. The key NAME stays
# unpinned (P19's note); this pins the RULING sentence that makes the
# name's advisory status load-bearing.
ADVISORY_PHRASE = (
    "advisory by design — no automated reader consumes it; you record "
    "the divergence and act on it"
)

ADVISORY_SURFACES = [COMPLETION_AUTHORITY, PROTOCOLS_SSOT]


@pytest.mark.parametrize(
    "doc_path",
    ADVISORY_SURFACES,
    ids=lambda p: p.name,
)
def test_integrity_finding_advisory_ruling_pinned(doc_path: Path):
    """The deferred audit's divergence key must stay documented as
    advisory-by-design. Losing the ruling invites either a speculative
    reader (the mechanization P17 bans) or the belief that something
    consumes the key today."""
    assert _phrase(ADVISORY_PHRASE) in _normalized(doc_path), (
        f"{doc_path.name}: integrity_finding advisory ruling "
        f"{ADVISORY_PHRASE!r} not found. The key is advisory by design "
        f"with no automated reader; if reworded intentionally, update "
        f"this pin in lockstep on both mirrored surfaces."
    )


# ---------------------------------------------------------------------------
# P27 — retired pointer-format rejection SendMessages absent, tree-wide.
# ---------------------------------------------------------------------------

# The retired shape: a rejection notify that points at metadata instead
# of carrying the payload ("See metadata.teachback_rejection." /
# "See metadata.handoff_rejection."). Like P6/P7, these absence pins
# flip RED on file revert (the pointers are present pre-change on BOTH
# mirrored surfaces, so each P27 case flips under EITHER the extract OR
# the SSOT revert — a documented deviation from the exactly-one-file
# property every other case has). The sweep covers every LLM-loaded
# instruction surface, with a floor on the surface count so a glob
# regression cannot silently narrow the scan to nothing.
RETIRED_POINTER_PINS = [
    "See metadata.teachback_rejection",
    "See metadata.handoff_rejection",
]

INSTRUCTION_SURFACE_FLOOR = 60


def _instruction_population(root):
    """Tracked instruction surfaces; a working-tree glob reads a scratch file."""
    patterns = ("skills/*/SKILL.md", "agents/*.md", "commands/*.md", "protocols/*.md")
    return sorted({p for pattern in patterns
                   for p in _tracked_glob(root, pattern, allow_empty=True)})


def _instruction_surfaces() -> list:
    surfaces = _instruction_population(PLUGIN_ROOT)
    assert len(surfaces) >= INSTRUCTION_SURFACE_FLOOR, (
        f"instruction-surface sweep found only {len(surfaces)} surfaces "
        f"(floor {INSTRUCTION_SURFACE_FLOOR}) — a glob regression has "
        f"narrowed the tree-wide absence scan; fix the glob before "
        f"trusting this pin."
    )
    return surfaces


@pytest.mark.parametrize("pointer", RETIRED_POINTER_PINS, ids=lambda p: p[:44])
def test_retired_pointer_format_absent_tree_wide(pointer: str):
    """No instruction surface may reintroduce the pointer-format
    rejection notify — a teammate waking on the message alone must be
    able to read the corrections in the message itself. The sweep is
    tree-wide over LLM-loaded surfaces, not scoped to the surfaces that
    previously carried the pointers."""
    for surface in _instruction_surfaces():
        assert _phrase(pointer) not in _normalized(surface), (
            f"{surface.name}: retired pointer-format notify {pointer!r} "
            f"present. The rejection SendMessage carries the payload "
            f"verbatim; the pointer form starves a message-only wake. "
            f"If a future rule genuinely needs the pointer form, update "
            f"this guard deliberately."
        )


# ---------------------------------------------------------------------------
# P28-P32 — metadata-presence: every named reader must be one that can read.
#
# The contract: an instruction surface must not name `TaskGet` as the reader
# of a `metadata.*` key, because `TaskGet` surfaces subject / status / owner /
# description / blocks / blockedBy and NOT metadata. Before these cases the
# two CODE-phase gate lines were pinned by nothing at all (measured with a
# live two-file control), so both could be reverted with the suite green.
# ---------------------------------------------------------------------------

PLAN_MODE = PLUGIN_ROOT / "commands" / "plan-mode.md"
IMPACT = PLUGIN_ROOT / "commands" / "imPACT.md"
WORKFLOWS = PLUGIN_ROOT / "protocols" / "pact-workflows.md"


def _unique_line_containing(doc_path: Path, marker: str) -> str:
    """Return the ONE normalized line of ``doc_path`` carrying ``marker``.

    Exactly-once is asserted before the line is used, and the marker is a
    bold checklist label rather than a content prefix. Selecting a line by
    content prefix is the trap this file's arc measured: two blocks whose
    first seventy characters are identical, and a prefix match picks one at
    random and fails silently. A label that matches twice means the anchor
    stopped identifying a single site, so the location is refused rather
    than guessed at."""
    hits = [
        line for line in _raw(doc_path).splitlines()
        if _phrase(marker) in _phrase(line)
    ]
    assert len(hits) == 1, (
        f"{doc_path.name}: line anchor {marker!r} matched {len(hits)} lines "
        f"(want exactly 1). The line-scoped gate pins below cannot identify "
        f"their site through an ambiguous or absent anchor — fix the anchor "
        f"before trusting them."
    )
    return _phrase(hits[0])


# ---------------------------------------------------------------------------
# P28 — the two CODE-phase gate lines carry their new acceptance/coverage text.
# ---------------------------------------------------------------------------

# D1 is an ACCEPTANCE site (it decides about a payload already in the lead's
# context) and D2 a COVERAGE site (it decides whether an event happened
# elsewhere), so they get different spans. D2's span requires BOTH key names:
# a lead `TaskUpdate` that overwrites an auditor-authored verdict routes the
# lead's value to `lead_close_note` and preserves the original at
# `audit_summary_authored`, so a coverage check reading only `audit_summary`
# asks the wrong key after a lead close.
GATE_PRESENCE_PINS = [
    (
        ORCHESTRATE,
        "**HANDOFF acceptance**: on receiving each Task-complete "
        "SendMessage, accept on the HANDOFF payload the notify carries",
    ),
    (
        ORCHESTRATE,
        "The disk copy is the deferred audit, not the acceptance surface",
    ),
    (
        ORCHESTRATE,
        "metadata.audit_summary OR metadata.audit_summary_authored",
    ),
]


@pytest.mark.parametrize(
    "doc_path, phrase",
    GATE_PRESENCE_PINS,
    ids=[f"{p.name}::{ph[:44]}" for p, ph in GATE_PRESENCE_PINS],
)
def test_code_phase_gate_lines_present(doc_path: Path, phrase: str):
    """Both CODE-phase gate lines must keep naming a surface their instrument
    can actually read — the notify-carried payload for acceptance, the task
    file's two audit keys for coverage.

    WHAT THIS CANNOT CATCH: presence alone is the phantom-green shape. A
    correct sentence can be ADDED while the broken one stays, and every case
    here still passes. P29 and P30 are the other half and must not be
    dropped as redundant."""
    assert _phrase(phrase) in _normalized(doc_path), (
        f"{doc_path.name}: CODE-phase gate phrase {phrase!r} not found. The "
        f"gate must name a readable surface; if reworded intentionally, "
        f"update this pin in lockstep with the P29/P30 absence guards."
    )


# ---------------------------------------------------------------------------
# P29 — the retired gate instruments stay retired (file-wide absence).
# ---------------------------------------------------------------------------

# Both spans are the verbatim pre-change text. Pinning the real retired
# sentence rather than one authored to match the pin is what makes a revert
# the mutation these cases answer to: a mutant written to fit its own pin
# proves the pin matches itself and nothing else.
#
# FILE-WIDE HERE IS DELIBERATE, AND IT IS NOT AN OVERSIGHT TO "FIX" BACK TO
# A LINE-SCOPED PIN. File-wide strictly dominates line-scoped for THIS job:
# it also catches the retired sentence reappearing on a NEW line beside the
# corrected one, which a line-scoped check cannot see. Line-scoping is used
# only at P30, where it buys something file-wide cannot have — it catches a
# NEW wrong variant on the gate's own line rather than a replay of the
# retired text. Different jobs, different scopes, both measured.
RETIRED_GATE_INSTRUMENT_PINS = [
    (
        ORCHESTRATE,
        "verify via TaskGet — confirm status=completed AND "
        "metadata.handoff populated/non-empty",
    ),
    (
        ORCHESTRATE,
        "metadata.audit_summary is present (verify via TaskGet)",
    ),
]


@pytest.mark.parametrize(
    "doc_path, retired",
    RETIRED_GATE_INSTRUMENT_PINS,
    ids=[f"{p.name}::not-{r[:38]}" for p, r in RETIRED_GATE_INSTRUMENT_PINS],
)
def test_retired_gate_instrument_absent(doc_path: Path, retired: str):
    """Neither gate may go back to reading task metadata through `TaskGet`.
    File-wide rather than line-scoped on purpose: the retired sentence is
    wrong wherever it appears, and a file-wide sweep also catches it being
    re-added on a NEW line beside the corrected one — which a line-scoped
    check would miss."""
    assert _phrase(retired) not in _normalized(doc_path), (
        f"{doc_path.name}: retired gate instrument {retired!r} present. "
        f"`TaskGet` does not surface metadata, so this check cannot "
        f"evaluate; the acceptance surface is the notify-carried payload "
        f"and the coverage surface is the task file."
    )


# ---------------------------------------------------------------------------
# P30 — line-scoped: the gate line must not name TaskGet as a metadata reader.
# ---------------------------------------------------------------------------

# The one case here that catches a NEW wrong variant rather than a replay of
# the retired text. Each entry is a set of tokens that must not ALL appear on
# the anchored line. D1's line no longer names `TaskGet` at all — the status
# conjunct was deleted because it ran before the lead's own completion write,
# so it could never read `completed` — and this case now guards the pairing
# against re-introduction rather than around a legitimate use. Tightening it
# to a bare-token absence pin is a separate judgement needing a driven red
# and a false-positive check; it is not made here. D2's line legitimately
# carries both `TaskGet` and a `metadata.` key (it names the blindness
# explicitly), so its forbidden span is the retired instrument phrase instead.
#
# THE FORBIDDEN TOKEN IS THE `metadata.` PREFIX, NOT A KEY NAME, AND THAT IS
# LOAD-BEARING. Keyed on `metadata.handoff` this case saw one key rather than
# the class: naming `TaskGet` as the reader of `metadata.teachback_submit` on
# this line passed while the identical sentence keyed on `metadata.handoff`
# reddened. The prefix form catches every key and does not redden the correct
# line, which now carries neither token. A future author who legitimately
# names a metadata key on this line alongside `TaskGet` must update this pin
# deliberately; that is intended behaviour, not a defect.
GATE_LINE_FORBIDDEN_COOCCURRENCE = [
    ("**HANDOFF acceptance**", ("TaskGet", "metadata.")),
    ("**Concurrent-audit coverage check**", ("verify via TaskGet",)),
]


@pytest.mark.parametrize(
    "label, forbidden",
    GATE_LINE_FORBIDDEN_COOCCURRENCE,
    ids=[lbl.strip("*") for lbl, _ in GATE_LINE_FORBIDDEN_COOCCURRENCE],
)
def test_gate_line_does_not_name_taskget_as_metadata_reader(
    label: str, forbidden: tuple
):
    """On the gate's own line, `TaskGet` must not be named as the reader of
    a metadata key. Scoped to the anchored line so a future rewording that
    re-imports the disk read is caught even when it shares none of the
    retired sentence's wording.

    WHAT THIS CANNOT CATCH: it is blind to the same defect on any OTHER line
    of the file, and blind to the line being deleted outright — P28 and P29
    cover those two directions."""
    line = _unique_line_containing(ORCHESTRATE, label)
    present = [tok for tok in forbidden if _phrase(tok) in line]
    assert len(present) < len(forbidden), (
        f"orchestrate.md: the {label} line names {present!r} together. "
        f"`TaskGet` surfaces status, not metadata — a metadata key on this "
        f"line must be read from the task file or carried by the notify."
    )


# ---------------------------------------------------------------------------
# P31 — per-site: retired reader-naming clauses stay retired.
# ---------------------------------------------------------------------------

# Every span is the verbatim pre-change clause, so each case answers to a
# revert of the commit that changed it. Two spans on the agent-teams skill
# cover successive states of the SAME line: the first is the state before
# the reader was swapped, the second the state before the resulting orphaned
# label was collapsed — reverting either commit reddens exactly one.
RETIRED_TASKGET_READER_PINS = [
    (AGENT_TEAMS_SKILL,
     "If upstream tasks are referenced, read them via TaskGet."),
    (ORCHESTRATOR,
     "If upstream task references are provided, read them via TaskGet first."),
    (PLAN_MODE,
     "If upstream context is referenced, read it first by using TaskGet tool."),
    (AGENT_TEAMS_SKILL,
     "any reader of the flag (team-lead TaskGet, audit, future consumers)"),
    (AGENT_TEAMS_SKILL,
     "will be flagged by the team-lead's TaskGet verification"),
    (AGENT_TEAMS_SKILL,
     "the team-lead's HANDOFF-presence check"),
    (COMPLETION_AUTHORITY,
     "as well as by your TaskGet inspection and audit tooling"),
    (PROTOCOLS_SSOT,
     "as well as by your TaskGet inspection and audit tooling"),
    (IMPACT, "Reconstruct from memory + TaskGet chain"),
    (WORKFLOWS, "Reconstruct from memory/TaskGet"),
    (PROTOCOLS_SSOT, "Reconstruct from memory/TaskGet"),
]


@pytest.mark.parametrize(
    "doc_path, retired",
    RETIRED_TASKGET_READER_PINS,
    ids=[f"{p.name}::not-{r[:38]}" for p, r in RETIRED_TASKGET_READER_PINS],
)
def test_retired_taskget_reader_clause_absent(doc_path: Path, retired: str):
    """No surface may instruct a read of upstream task content through
    `TaskGet`, or describe a mechanism as reading metadata with it. The two
    mirrored surfaces each carry their own case: a lockstep revert of an
    extract and its SSOT region keeps the byte-mirror gate green, so a single
    witness on one side would miss the pair moving together."""
    assert _phrase(retired) not in _normalized(doc_path), (
        f"{doc_path.name}: retired reader-naming clause {retired!r} "
        f"present. `TaskGet` does NOT surface metadata — name the task file "
        f"read, or name the property rather than the instrument."
    )


# ---------------------------------------------------------------------------
# P31b — the two DISPATCH TEMPLATES keep their corrected instruction.
#
# P31 above is absence-only, so DELETING a corrected clause stays green. That
# is tolerable at the nine sites that describe or assert — a deletion there
# removes a claim, and the diff shows it. It is NOT tolerable at these two,
# which are dispatch TEMPLATES: deleting the clause silently strips the
# instruction from every dispatch subsequently written from the template,
# which is the same blast radius as the reversion P31 does catch. Guarding
# one direction only, against defects of equal reach, is the gap these close.
# ---------------------------------------------------------------------------

DISPATCH_TEMPLATE_PRESENCE_PINS = [
    (ORCHESTRATOR, "read their task files first"),
    (PLAN_MODE, "read the upstream task file first"),
]


@pytest.mark.parametrize(
    "doc_path, phrase",
    DISPATCH_TEMPLATE_PRESENCE_PINS,
    ids=[f"{p.name}::{ph[:34]}" for p, ph in DISPATCH_TEMPLATE_PRESENCE_PINS],
)
def test_dispatch_template_keeps_task_file_instruction(
    doc_path: Path, phrase: str
):
    """Each dispatch template must keep telling the dispatched agent to read
    the upstream TASK FILE. Presence rather than absence: the paired P31 case
    catches the clause being reverted, and this catches it being removed."""
    assert _phrase(phrase) in _normalized(doc_path), (
        f"{doc_path.name}: dispatch template no longer instructs "
        f"{phrase!r}. Every dispatch written from this template inherits "
        f"its wording, so a deletion here reaches far past this file."
    )


# ---------------------------------------------------------------------------
# P32 — tree-wide: no blindness claim scoped to `handoff`.
# ---------------------------------------------------------------------------

# TREE-WIDE, NOT PER-SITE, and that is the whole design. A per-site arm goes
# green the moment a sixth narrow claim appears in a NEW file, which is
# exactly how this class spread: five surfaces stated the blindness correctly
# but scoped it to one key, and a reader of any of them can conclude the
# other keys ARE surfaced. Blindness is total across keys.
#
# The sweep inherits INSTRUCTION_SURFACE_FLOOR through _instruction_surfaces()
# so a glob regression cannot narrow it to nothing, and it carries its own
# positive control below: an absence result from a search with no known-present
# term in the same run is worth nothing.
# KEYED ON THE CONSTRUCTION, NOT ON A RENDERING, because two literals saw
# only two of the six renderings this claim has in the tree. `does NOT surface
# TASK metadata` is the MAJORITY form (eight of the governed lines) and is not
# a superstring of the canonical claim, so the positive control below cannot
# even count it, let alone police it; the lowercase form is live today in the
# agent-teams skill. Both evaded the literals. These two patterns are a strict
# superset of them, fire on every key and either casing, and were measured to
# produce zero hits on the correct tree.
NARROW_BLINDNESS_CLAIMS = [
    re.compile(r"TaskGet does not surface (?:task )?metadata\.\w+", re.I),
    re.compile(r"TaskGet is metadata-blind for \w+", re.I),
]

CANONICAL_BLINDNESS_CLAIM = "TaskGet does NOT surface metadata"

# Measured 12 surfaces carrying the canonical form. The floor sits below the
# measurement so ordinary editing does not redden it, and above zero so a
# normalization or glob failure that silently matches nothing does.
BLINDNESS_CONTROL_FLOOR = 10


@pytest.mark.parametrize(
    "narrow", NARROW_BLINDNESS_CLAIMS, ids=lambda n: n.pattern[:44]
)
def test_blindness_claim_not_scoped_to_handoff_tree_wide(narrow: "re.Pattern"):
    """No instruction surface may state the metadata blindness as applying
    to `handoff` alone. Swept tree-wide with a known-present control in the
    SAME run, so a scan that has stopped matching anything reports as broken
    rather than as clean.

    WHAT THIS CANNOT CATCH: it keys on two narrowing CONSTRUCTIONS, so it
    reaches any key and either casing, but a site that narrows the claim
    without either construction — naming the key in a following sentence,
    say — still passes. It is not a general claim-scope detector."""
    surfaces = _instruction_surfaces()

    control_hits = sum(
        1 for s in surfaces
        if _phrase(CANONICAL_BLINDNESS_CLAIM) in _normalized(s)
    )
    assert control_hits >= BLINDNESS_CONTROL_FLOOR, (
        f"positive control failed: the canonical blindness claim "
        f"{CANONICAL_BLINDNESS_CLAIM!r} was found on only {control_hits} of "
        f"{len(surfaces)} surfaces (floor {BLINDNESS_CONTROL_FLOOR}). The "
        f"absence result below is not trustworthy until this passes — a "
        f"scan matching nothing reports every claim as absent."
    )

    for surface in surfaces:
        hit = narrow.search(_normalized(surface))
        assert hit is None, (
            f"{surface.name}: blindness claim {hit.group(0)!r} is scoped to "
            f"one key (matched {narrow.pattern!r}). `TaskGet` surfaces NO "
            f"metadata, so a reader of this sentence can wrongly conclude "
            f"other keys are surfaced — state the blindness generally."
        )


# ---------------------------------------------------------------------------
# Counter-test flip-set record (measured in TEMP COPIES of the plugin
# tree; see module docstring). Each edited file restored individually to
# its pre-change state (git show e2afa3e5:<path> over the copy), module
# run against the copy, then the edited version restored.
#
# POST-REJECTION-FOLD (current record, 55 cases) — re-measured after the
# review-remediation pins (P13-P19), the hardening-audit pin (P20), and
# the rejection-direction fold pins (P21-P27):
#
#   skills/pact-agent-teams/SKILL.md   -> 10 failed (P1, P5, P7-absence,
#                                          P12, P13, P14, P18, P24,
#                                          P25-lower, P25-cap)
#   skills/pact-teachback/SKILL.md     -> 7 failed (P2, P5, P6-absence,
#                                          P12, P13, P14, P23)
#   protocols/pact-completion-authority.md
#                                      -> 14 failed (P3 x2, P4, P5, P16,
#                                          P17, P19, P20, P21, P22,
#                                          P25-lower, P26, P27 x2)
#   protocols/pact-protocols.md        -> 15 failed (P3 x2, P4, P5, P11,
#                                          P16, P17, P19, P20, P21, P22,
#                                          P25-lower, P26, P27 x2)
#   protocols/pact-ct-teachback.md     -> 1 failed  (P11)
#   agents/pact-orchestrator.md        -> 4 failed (P5, P21, P22, P23)
#   commands/orchestrate.md            -> 3 failed (P8 x2, P23)
#
# Measured: 52 of the module's 55 cases flip under file revert; the
# all-edited state is 55/55 green. Per-file sums total 54 because P27's
# two tree-wide absence cases flip under EITHER the extract OR the SSOT
# revert (the retired pointers lived on both mirrored surfaces) — a
# documented deviation from the exactly-one-file property every other
# case has. The three revert-immune cases flip under their targeted
# in-place mutations instead: P10 (the On Completion write-order triple
# pre-dates this arc, so the pre-change file already carries the pinned
# wording — deleting the triple in place flips it) and P15 x2 (absence
# pins; the foreign delimiter never existed pre-change — planting a
# stray wrong-type BEGIN flips exactly that surface's case). Absence
# pins P6/P7/P27 flip RED on revert because the retired renderings are
# present pre-change, by design.
#
# Earlier milestones of the same record (superseded above, kept for the
# arc's audit trail): pre-P12 19 of 20; post-P12 21 of 22 (P10 sole
# revert-immune); post-remediation 32 of 35 (at 7, tb 6, ca 7, ssot 8,
# ct 1, orchestrator 1, orchestrate 2); post-hardening 34 of 37 (ca 8,
# ssot 9, rest as prior).
#
# Targeted in-place mutations (file otherwise at HEAD), measured in the
# same temp copies — predicted cardinalities all confirmed, each flip
# list containing exactly the intended case:
#   point-4 "BEFORE" reworded            -> 1 failed (P4)
#   T2 sentence reverted in place        -> 2 failed (P3 phrase-2, P5);
#     P3 phrase-1 stays green — point 4 carries "data-integrity finding"
#     too, so phrase-1 is pair-level protection, not T2-anchored
#   T2 "DEFERRED audit" lowercased       -> 1 failed (P5)
#   retired teachback framing re-added   -> 1 failed (P6)
#   retired 1-2-sentences shape re-added -> 1 failed (P7)
#   orchestrate Task-B phrase reworded   -> 1 failed (P8 Task-B case)
#   HANDOFF-PAYLOAD-BEGIN renamed        -> 1 failed (P1)
#   "wake-send" planted in new T2 prose  -> 1 failed in
#     test_wake_ordering_pinned.py (P9 inherited guard, verified live)
#   On Completion write-order deleted    -> 1 failed (P10; was 0 before
#     the pin — the gap this case closes)
#   ct-teachback Flow edit reverted BOTH sides (extract + SSOT, mirror
#     gate green)                          -> 2 failed (P11; was 0 before
#     the pin — the gap this case closes)
#   200-char summary sentence deleted (either surface)
#                                        -> 1 failed (P13, that surface;
#     was 0 before the pin)
#   payload END token deleted (either surface)
#                                        -> 1 failed (P14, that surface;
#     was 0 before the pin)
#   stray wrong-type BEGIN planted (either surface)
#                                        -> 1 failed (P15, that surface;
#     was 0 before the pin)
#   HANDOFF-path extension deleted (extract + SSOT)
#                                        -> 2 failed (P16; was 0 before
#     the pin)
#   non-goal sentence deleted (extract + SSOT)
#                                        -> 2 failed (P17; was 0 before
#     the pin)
#   write-failure fallback reverted to the retired wording
#                                        -> 1 failed (P18; was 0 before
#     the pin)
#   repair clause deleted (extract + SSOT)
#                                        -> 2 failed (P19; was 0 before
#     the pin)
#   T2 opening sentence stubbed (either mirrored surface)
#                                        -> 1 failed (P20, that surface;
#     was 0 before the pin — the hardening-audit gap it closes)
#   persona rejection-END token deleted  -> 1 failed (P21/P22 persona
#     case)
#   ONE of the extract's 3 rejection-END tokens renamed
#                                        -> 0 failed (presence contract
#     over the three template occurrences, documented); ALL 3 renamed
#                                        -> 1 failed
#   orchestrate rejection phrase reworded -> 1 failed (P23 cmd case)
#   agent-teams step-2 reading header reworded
#                                        -> 1 failed (P24)
#   advisory ruling deleted (extract + SSOT)
#                                        -> 2 failed (P26)
#   retired pointer planted on a surface that never carried it
#                                        -> 1 failed (P27 — the
#     tree-wide sweep catching reintroduction ANYWHERE, not just on the
#     surfaces that previously had the pointers)
#   confirmation phrase reworded (extract)
#                                        -> 1 failed (P25 that surface)
#
# METADATA-PRESENCE ARMS (P28-P32, 22 cases in this module — 20 as first
# landed, plus the 2 P31b dispatch-template presence pins). Every
# cardinality below was PREDICTED before the run and then measured; the one
# deviation is recorded with its cause rather than smoothed over. Reverts are
# source-only (`git checkout <sha>^ -- <paths>`), so the arms stay in place
# and the retired text is the real pre-change text rather than a mutant
# authored to match its own pin.
#
#   revert of the gate-line commit (both lines)
#                                        -> 7 failed (P28 x3, P29 x2,
#     P30 x2). NOTE WHICH ASSERTION FIRES: P30's acceptance case reddens on
#     the ANCHOR assert (the label is reverted too, so the line anchor
#     matches 0 lines), NOT on the co-occurrence assert. The co-occurrence
#     assert is exercised only by the targeted mutation below.
#   TaskGet named as reader of metadata.handoff on the acceptance line,
#     label left intact                   -> 1 failed (P30 acceptance case,
#     on `assert 2 < 2`). P28 and P29 stay GREEN on this real defect — it is
#     a NEW variant sharing no wording with the retired sentence, and P30 is
#     the only case that sees it.
#   source-only revert of the class-C commit
#                                        -> 14 failed (P31 x11, P31b x2,
#     plus P32's narrow-claim case). Originally PREDICTED 11 and MEASURED 12:
#     the narrow-5 commit is LATER than the class-C commit on two shared
#     files, so restoring them at the class-C parent also un-widens the
#     blindness claim. Not an arm defect — an artifact of checking out an
#     ancestor ref on a file later commits also touched. The 12 -> 14 move is
#     the two P31b pins, whose sites this commit also wrote; RE-MEASURED, not
#     inferred from the arm count. The other four rows were re-run unchanged
#     at 7 / 1 / 2 / 2.
#   revert of the orphaned-label commit   -> 1 failed (P31, the
#     HANDOFF-presence case; the two agent-teams spans pin successive states
#     of the SAME line, so each commit reddens exactly its own)
#   source-only revert of the narrow-5 commit
#                                        -> 2 failed (P32 x2; the positive
#     control still passes, since the narrow form contains the canonical one)
#   P32's positive control pointed at a phrase absent from the tree
#                                        -> both P32 cases fail ON THE
#     CONTROL assert, reporting 0 of 70 surfaces against a floor of 10. An
#     instrument check, not a tree mutation: it proves the control fires, so
#     a sweep that has stopped matching reports as broken rather than clean.
# ---------------------------------------------------------------------------


def test_an_untracked_instruction_surface_is_not_swept(tmp_path):
    """A scratch markdown file must not join the tree-wide absence scan."""
    root = tmp_path / "plugin"
    (root / "commands").mkdir(parents=True)
    tracked = root / "commands" / "tracked.md"
    tracked.write_text("a clean command\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"],
                   check=True, capture_output=True)

    needle = root / "commands" / "zzprobe.md"
    needle.write_text("See metadata.teachback_rejection\n", encoding="utf-8")

    population = _instruction_population(root)

    assert tracked in population, "the tracked control is missing from the population"
    assert needle not in population, (
        f"untracked needle entered the instruction-surface sweep: {needle}")
    assert any(_phrase(pointer) in _normalized(needle)
               for pointer in RETIRED_POINTER_PINS), "the needle would be flagged"
    assert not [path for path in population
                if any(_phrase(pointer) in _normalized(path)
                       for pointer in RETIRED_POINTER_PINS)]
