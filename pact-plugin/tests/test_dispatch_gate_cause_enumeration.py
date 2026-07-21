"""
Cause enumeration appended to the rule-⑧ (``no_task_assigned``) deny.

Rule ⑧ fires whenever no task for this owner is OBSERVED, which conflates a
genuinely-missing task with an unreadable store, a symlink escape, an unsafe
team name and an I/O error. ``_compose_deny_diagnosis`` therefore appends a
four-cause enumeration plus a self-check the reader can run — but only when the
incumbent stale-team detector did NOT fire, and only on rule ⑧.

SCOPE OF THIS FILE. It covers the A-xor-B precedence on BOTH stale-diagnosable
rules, the graceful-degradation path, rule isolation, the healthy ALLOW path,
journal purity, branch completeness across all four causes, advice soundness,
the emitted-versus-journaled boundary, and cause (4) as a real runtime state.

WHAT A GREEN RUN HERE STILL DOES NOT ESTABLISH — three named residuals, none
of them a gap that more coverage closes:

  * Whether the text HELPS. Every assertion below is about presence, absence
    and structure. None of them can tell whether an operator reading this deny
    cold reaches a correct next action, which is the primary risk of a change
    that is almost entirely text. That is a review question with a human owner.

  * Advice soundness in NOVEL wording. Two tests here guard known-bad phrasings
    with denylists, and a phrase denylist over free prose cannot be complete.
    Each declares the bound in its own docstring; neither is evidence that the
    text contains no unsound claim.

  * Verification-before-remedy ordering in the README section the gates point
    at. ``test_readme_pointer_has_a_referent`` pins that the referent EXISTS,
    and deliberately not that it still leads with a falsifiable check before
    showing the reader a high-blast-radius setting. That protection is a
    property of the gate/README pair which no single artifact can enforce.

NON-VACUITY DISCIPLINE — the reasons, so a later editor does not undo them:

  * Markers are module-level SHARED CONSTANTS and the assertion helper reads
    them from there. ``_assert_deny_text`` takes a BOOL for which side of the
    pair it is checking, never a marker string. If the signature ever accepts
    a marker from the caller, the first caller that types a hyphen for U+2014
    makes the negative arm vacuous forever — the exact defect the helper
    exists to prevent, reintroduced at its own call site.

  * Every marker is ASCII-only and none SPANS a non-ASCII character. The
    enumeration prose does contain em-dashes; the markers are chosen to sit
    clear of them. ``blocking for safety`` is used rather than the full
    ``failure — blocking for safety``, which contains U+2014.

  * The positive and negative legs are COUPLED IN ONE TEST BODY. Split across
    two tests they would yield revert-cardinality {1}: the negative leg passes
    by absence and contributes nothing on its own.

  * Absence assertions on PRE-EXISTING text derive their literal from a
    pre-existing symbol (``_STALE_REALIGN_HINT``) so they survive a revert.
    Markers for POST-FIX text are hardcoded, since importing a post-fix symbol
    would yield an ImportError artifact under revert rather than a behavioural
    failure.

  * Exit 2 is NOT asserted as proof that no exception escaped. ``main()``
    catches runtime exceptions and routes them through the load-failure
    emitter, which also denies and also exits 2. The load-failure marker is
    asserted ABSENT instead, which only the intended path satisfies.

  * TWO TESTS SIT DELIBERATELY OUTSIDE THE REVERT-NON-VACUITY SET and must not
    be counted in it: ``test_composer_is_total_over_message_type`` and
    ``test_enumeration_has_no_negative_arm_on_cause_four`` import POST-FIX
    symbols directly, so under a revert they raise ImportError — an import
    artifact, not a behavioural failure, which the plan disqualifies. That is
    unavoidable and correct for these two: both assert properties OF the new
    code, and there is no pre-fix behaviour to compare against. Every test
    that DOES carry revert-cardinality drives ``main()`` end-to-end and
    asserts only on observable deny text.

    EXACTLY TWO, VERIFIED BY MEASUREMENT rather than by reading the names.
    Under a source-only revert of the gate every failing test's traceback was
    inspected: 9 fail on assertions, and only the 2 named above fail on the
    import. Any test added here that imports from ``dispatch_gate`` inside its
    body joins that list and must be named in it — the check is not "does it
    look like a unit test", it is "does the symbol it reaches for exist at
    base".
"""

import json
import os
import re
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from test_dispatch_gate import (  # noqa: E402 — sibling harness reuse
    _make_input,
    _run_main,
    _full_setup,
    _capture_journal,
    _TEAM,
    _NAME,
)

# Pre-existing production symbol — deriving the stale-diagnosis literal from it
# keeps the absence assertions valid under a revert of this change.
from dispatch_gate import _STALE_REALIGN_HINT  # noqa: E402

# ── Shared markers ────────────────────────────────────────────────────────
# ASCII-only, and each verified to sit clear of the prose's em-dashes.
# POST-FIX text → hardcoded on purpose (see module docstring).
_ENUM_MARKER = "did not OBSERVE"           # opening line, most stable
_ENUM_ACTION_MARKER = "TO NARROW IT DOWN"  # action-first block
_CAUSE4_MARKER = "could not be READ"       # discriminates 4-cause from 3-cause

# PRE-EXISTING text → derived, not typed.
_STALE_MARKER = _STALE_REALIGN_HINT[:40]

# PRE-EXISTING load-failure text. Hardcoded as an ASCII-only substring because
# it lives inside an f-string rather than a named constant; its liveness is
# pinned by test_load_failure_marker_is_live below, so this absence assertion
# cannot silently go vacuous.
_LOAD_FAILURE_MARKER = "blocking for safety"

_LIVE_SESSION_ID = "test-session"
_STALE_SESSION_ID = "0000dead-beef-4000-8000-000000000000"


# ── Helpers ───────────────────────────────────────────────────────────────


def _write_project_claude_md(monkeypatch, root, recorded_session_id):
    """Point CLAUDE_PROJECT_DIR at a CLAUDE.md recording ``recorded_session_id``.

    Inlined rather than imported from test_dispatch_gate_stale_diagnosis: a
    sibling-module import executes that module at import time, and this file
    needs none of the rest of it.
    """
    proj = root / "project_md"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "CLAUDE.md").write_text(
        "# Project\n\n## Current Session\n"
        f"- Resume: `claude --resume {recorded_session_id}`\n"
        f"- Team: `{_TEAM}`\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))


def _reset_context_caches(monkeypatch):
    """Drop pact_context's memoised reads so a second leg in the same test body
    re-resolves against the state that leg just wrote."""
    import shared.pact_context as ctx_module

    monkeypatch.setattr(ctx_module, "_cache", None, raising=False)
    monkeypatch.setattr(ctx_module, "_aligned_cache", None, raising=False)


def _assert_deny_text(reason, *, rule_text, enumeration):
    """Assert the deny text against the shared markers.

    ``enumeration`` is a BOOL — which SIDE of the coupled pair to check — not a
    marker string. Callers supply only ``rule_text``, the rule's own wording.
    Letting a caller pass the marker is what makes a negative arm vacuous.
    """
    assert rule_text in reason, f"base deny text missing: {rule_text!r}"

    if enumeration:
        assert _ENUM_MARKER in reason, "enumeration expected but absent"
        assert _ENUM_ACTION_MARKER in reason, "action block expected but absent"
        assert _CAUSE4_MARKER in reason, "cause (4) expected but absent"
    else:
        assert _ENUM_MARKER not in reason, "enumeration present but not expected"
        assert _ENUM_ACTION_MARKER not in reason, "action block leaked"
        assert _CAUSE4_MARKER not in reason, "cause (4) leaked"

    # Only the intended path satisfies this; exit 2 alone would not, since the
    # load-failure emitter also denies and also exits 2.
    assert _LOAD_FAILURE_MARKER not in reason, "routed through load-failure path"


# ── Marker liveness ───────────────────────────────────────────────────────


def test_load_failure_marker_is_live():
    """The load-failure marker asserted ABSENT elsewhere must be a real
    substring of the gate's load-failure text, else every such assertion is
    vacuous. Read from source rather than imported so this still means
    something if the constant is restructured."""
    src = (Path(__file__).parent.parent / "hooks" / "dispatch_gate.py").read_text(
        encoding="utf-8"
    )
    assert _LOAD_FAILURE_MARKER in src, (
        "load-failure marker no longer appears in dispatch_gate.py — every "
        "absence assertion using it is now vacuous"
    )


def test_markers_are_ascii_and_span_no_multibyte():
    """Each hardcoded marker must be ASCII-only. A marker that spans a
    non-ASCII character invites an eyeball transcription that substitutes a
    hyphen for U+2014, after which the assertion can never fail."""
    for marker in (
        _ENUM_MARKER,
        _ENUM_ACTION_MARKER,
        _CAUSE4_MARKER,
        _LOAD_FAILURE_MARKER,
    ):
        assert marker.isascii(), f"marker is not ASCII-only: {marker!r}"


# ── A-xor-B precedence: the coupled non-vacuity pair ──────────────────────


def test_enumeration_appends_exactly_when_incumbent_does_not_fire(
    tmp_path, monkeypatch, capsys
):
    """COUPLED PAIR IN ONE BODY — both legs, one test, revert-cardinality {2}.

    Leg A (healthy, no stale mismatch): the incumbent finds nothing, so the
    enumeration appends and no stale diagnosis appears.
    Leg B (stale mismatch): the incumbent fires, so its specific diagnosis is
    returned and the generic enumeration is suppressed.

    Together these pin A-xor-B. Either leg alone would pass by absence.
    """
    # ── Leg A — healthy: enumeration present, stale diagnosis absent ──
    leg_a = tmp_path / "leg_a"
    leg_a.mkdir()
    _full_setup(monkeypatch, leg_a, tasks=(("someone-else", "pending"),))
    _write_project_claude_md(monkeypatch, leg_a, _LIVE_SESSION_ID)
    _reset_context_caches(monkeypatch)

    code_a, out_a = _run_main(_make_input(), capsys)
    reason_a = out_a["hookSpecificOutput"]["permissionDecisionReason"]

    assert code_a == 2, "decision must still be DENY"
    _assert_deny_text(reason_a, rule_text="no Task assigned", enumeration=True)
    assert _STALE_MARKER not in reason_a, "stale diagnosis must not fire when healthy"

    # ── Leg B — stale mismatch: incumbent wins, enumeration suppressed ──
    leg_b = tmp_path / "leg_b"
    leg_b.mkdir()
    _full_setup(monkeypatch, leg_b, tasks=(("someone-else", "pending"),))
    _write_project_claude_md(monkeypatch, leg_b, _STALE_SESSION_ID)
    _reset_context_caches(monkeypatch)

    code_b, out_b = _run_main(_make_input(), capsys)
    reason_b = out_b["hookSpecificOutput"]["permissionDecisionReason"]

    assert code_b == 2, "decision must still be DENY"
    assert _STALE_MARKER in reason_b, "incumbent stale diagnosis must fire"
    _assert_deny_text(reason_b, rule_text="no Task assigned", enumeration=False)


# ── Graceful degradation: A's failure degrades INTO B ─────────────────────


def test_detector_raise_degrades_into_enumeration_not_silence(
    tmp_path, monkeypatch, capsys
):
    """When the stale detector RAISES, the incumbent's never-raises wrap returns
    the message unchanged. The composer cannot distinguish that from 'no
    mismatch', so the enumeration appends: A's failure degrades INTO B rather
    than into silence.

    The input is a stale-mismatch input, so without the raise leg B above would
    have suppressed the enumeration — that is what makes this non-vacuous.
    """
    import dispatch_gate

    _full_setup(monkeypatch, tmp_path, tasks=(("someone-else", "pending"),))
    _write_project_claude_md(monkeypatch, tmp_path, _STALE_SESSION_ID)
    _reset_context_caches(monkeypatch)

    def _boom(_input_data):
        raise RuntimeError("detector blew up")

    monkeypatch.setattr(dispatch_gate, "detect_stale_session_block", _boom)

    code, out = _run_main(_make_input(), capsys)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]

    assert code == 2, "deny still fires despite the detector raising"
    _assert_deny_text(reason, rule_text="no Task assigned", enumeration=True)
    assert _STALE_MARKER not in reason, "no partial stale diagnosis"


# ── Rule isolation ────────────────────────────────────────────────────────


def test_rule_six_deny_never_receives_the_enumeration(
    tmp_path, monkeypatch, capsys
):
    """Rule ⑥ (team_name_unavailable) is the OTHER member of the
    stale-diagnosable set, so it is the rule most likely to be caught by a
    frozenset-based gate instead of the rule-equality gate the composer uses.
    It must never carry the enumeration: an empty session team says nothing
    about whether the task store was observable."""
    import shared.pact_context as ctx_module

    plugin_root = _full_setup(monkeypatch, tmp_path)
    ctx_path = tmp_path / "pact-session-context.json"
    ctx_path.write_text(
        json.dumps(
            {
                "team_name": "",
                "session_id": _LIVE_SESSION_ID,
                "project_dir": str(tmp_path / "project"),
                "plugin_root": str(plugin_root),
                "started_at": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ctx_module, "_context_path", ctx_path)
    _reset_context_caches(monkeypatch)
    _write_project_claude_md(monkeypatch, tmp_path, _LIVE_SESSION_ID)

    code, out = _run_main(_make_input(), capsys)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]

    assert code == 2, "rule 6 must actually fire, else this test is vacuous"
    _assert_deny_text(reason, rule_text="team_name is unavailable",
                      enumeration=False)


def test_non_symptom_deny_receives_neither_diagnosis(
    tmp_path, monkeypatch, capsys
):
    """A deny rule outside the stale-diagnosable set gets neither block, even
    with a stale-recorded CLAUDE.md present. A name-validation failure is not a
    restart symptom and not a store-observability symptom."""
    _full_setup(monkeypatch, tmp_path)
    _write_project_claude_md(monkeypatch, tmp_path, _STALE_SESSION_ID)
    _reset_context_caches(monkeypatch)

    code, out = _run_main(_make_input(name=""), capsys)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]

    assert code == 2, "name_required must still DENY"
    _assert_deny_text(reason, rule_text="name=", enumeration=False)
    assert _STALE_MARKER not in reason, "stale diagnosis must not reach this rule"


# ── Healthy ALLOW is untouched ────────────────────────────────────────────


def test_healthy_allow_path_is_untouched(tmp_path, monkeypatch, capsys):
    """The composer runs only on the DENY branch. A healthy dispatch must still
    exit 0 and emit the suppress-output payload with no diagnosis text
    anywhere."""
    _full_setup(monkeypatch, tmp_path)
    _reset_context_caches(monkeypatch)

    code, out = _run_main(_make_input(), capsys)

    assert code == 0, "healthy dispatch must ALLOW"
    assert out.get("suppressOutput") is True, "suppressOutput payload preserved"
    assert _ENUM_MARKER not in json.dumps(out), "no diagnosis on the ALLOW path"


# ── Advice soundness: no negative arm on cause (4) ────────────────────────

# Phrasings that would ELIMINATE cause (4) on a successful listing. Each is
# unsound for the same reason: `ls` follows symlinks and validates no
# containment, while the gate's iterator refuses a symlink escape, so a team
# dir symlinked outside the tasks root lists fine AND denies. Any of these
# would tell that operator "not case (4)" in precisely the case that IS (4).
#
# SCOPED TO CAUSE (4) ON PURPOSE. Bare "rules out" is NOT here: it is a SOUND
# construction ("rules out case (3)" is correct, and the README ships "rules
# out one cause, not the rest"). A guard that forbade the phrasing our own
# peer artifact uses would be deleted by the first author who hit it.
_FORBIDDEN_CAUSE4_ELIMINATIONS = (
    "not in case (4)",
    "not case (4)",
    "rules out case (4)",
    "rules out cause (4)",
    "excludes case (4)",
    "eliminates case (4)",
    "cannot be case (4)",
)

# The SOUND positive arm this pairs with: a permissions error PROVES (4).
_CAUSE4_POSITIVE_PROOF = "PERMISSIONS ERROR"


def test_enumeration_has_no_negative_arm_on_cause_four():
    """TRIPWIRE FOR A KNOWN REGRESSION — NOT A PROOF OF ADVICE SOUNDNESS.

    Read this limitation before trusting a green result. A phrase denylist over
    free prose CANNOT be complete: the next author who reintroduces a negative
    arm in different words passes this test. Its actual job is narrower and
    still worth having — when the known-bad construction returns, a human is
    forced to look, exactly like the `_CANONICAL_DENY_REASON_LITERAL` byte-pin.

    What a green result here means: "the specific unsound phrasings we have
    seen are absent." What it does NOT mean: "the text contains no negative
    arm." Do not cite this test for the latter.

    Coupled with the POSITIVE arm so the pair cannot both pass vacuously: if
    the whole self-check block were deleted, the positive assertion fails.
    Checking only for absence would pass trivially on an empty string.
    """
    from dispatch_gate import _CAUSE_ENUMERATION

    lowered = _CAUSE_ENUMERATION.lower()
    for phrase in _FORBIDDEN_CAUSE4_ELIMINATIONS:
        assert phrase.lower() not in lowered, (
            f"unsound negative arm reintroduced: {phrase!r}. A successful "
            "listing does NOT rule out cause (4) — ls follows symlinks and "
            "does no containment validation, while the gate's iterator "
            "refuses a symlink escape."
        )

    assert _CAUSE4_POSITIVE_PROOF in _CAUSE_ENUMERATION, (
        "the sound positive arm (a permissions error PROVES cause 4) is gone; "
        "without it the absence assertions above pass vacuously"
    )


# ── Precondition totality: the failure DIRECTION ──────────────────────────


def test_composer_is_total_over_message_type():
    """A non-str message must return the fail-closed fallback, never raise.

    WHY THIS IS NOT DEFENSIVE CLUTTER. Every DENY path in evaluate_dispatch
    supplies a real message (11 of 11, verified by enumeration), so this is
    unreachable today. It is pinned because the failure DIRECTION of the
    alternative is wrong: the composer runs OUTSIDE main()'s runtime try, so a
    TypeError here escapes uncaught and exits 1 — and a nonzero-non-2 exit is
    NON-BLOCKING, meaning the tool call proceeds. A gate whose job is to refuse
    would silently become a pass-through.

    Asserted on the returned VALUE, not merely on "did not raise": the fallback
    must be a real non-empty sentence. An empty string would also avoid the
    crash while telling the operator nothing, which is the degraded outcome
    this fallback exists to rule out.
    """
    from dispatch_gate import _compose_deny_diagnosis, _MISSING_DENY_REASON

    for bad_message in (None, 0, [], {}, object()):
        out = _compose_deny_diagnosis("no_task_assigned", bad_message, {})
        assert out == _MISSING_DENY_REASON, f"not fail-closed for {type(bad_message)}"
        assert isinstance(out, str) and out.strip(), "fallback must be non-empty"
        assert _ENUM_MARKER not in out, "must not claim to have diagnosed a cause"

    # The guard must not have cost the normal path anything.
    assert _compose_deny_diagnosis("some_other_rule", "plain deny", {}) == "plain deny"


# ── Journal purity ────────────────────────────────────────────────────────


def test_journaled_reason_excludes_the_enumeration(tmp_path, monkeypatch, capsys):
    """The composer runs strictly AFTER _journal_decision, so the journal keeps
    the canonical un-augmented reason even on the rule whose emitted text grew.

    Asserted as a COUPLED pair against the same run: the enumeration is present
    in the emitted text and absent from the journaled reason. Checking only the
    journal would pass trivially if the enumeration never appended at all.
    """
    from test_dispatch_gate import _capture_journal

    _full_setup(monkeypatch, tmp_path, tasks=(("someone-else", "pending"),))
    _write_project_claude_md(monkeypatch, tmp_path, _LIVE_SESSION_ID)
    _reset_context_caches(monkeypatch)
    journal = _capture_journal(monkeypatch)

    code, out = _run_main(_make_input(), capsys)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]

    assert code == 2
    assert _ENUM_MARKER in reason, "positive conjunct: the emitted text grew"

    events = [e for e in journal if e.get("type") == "dispatch_decision"]
    assert len(events) == 1, f"expected one dispatch_decision, got {len(events)}"
    journaled = events[0].get("reason")
    assert journaled, "journal must carry a reason on a DENY"
    assert _ENUM_MARKER not in journaled, "journal must keep the canonical reason"
    assert journaled == reason[: len(journaled)], (
        "the emitted text must be the journaled reason plus appended diagnosis, "
        "not a rewritten message"
    )


# ══════════════════════════════════════════════════════════════════════════
# Branch completeness — every KNOWN CAUSE is pinned by its own marker
# ══════════════════════════════════════════════════════════════════════════

# One ASCII marker per cause. POST-FIX text, so hardcoded rather than derived:
# importing the constant would raise ImportError under a revert, which is an
# import artifact rather than a behavioural failure and would disqualify this
# from the non-vacuity set. Cause (4) reuses the shared discriminator marker
# rather than typing a second literal for the same span.
#
# WHY EACH CAUSE NEEDS ITS OWN MARKER. Prose deletes silently. Measured before
# this test existed, independently by two parties against the same tree:
# deleting cause (1), (2) or (3) from the enumeration left the entire suite
# green at 114 passed. Only cause (4) failed anything, and only INCIDENTALLY —
# ``could not be READ`` was chosen to discriminate a four-cause enumeration
# from a three-cause one, never as a completeness pin, and nothing about that
# choice generalised to the other three.
#
# Each marker was confirmed against the live constant (not merely proposed):
# ASCII-only, occurring exactly once, and landing inside its own cause block.
_CAUSE_MARKERS = {
    1: "No task exists for this owner",
    2: "recorded team no longer matches",
    3: "not available in this session at all",
    4: _CAUSE4_MARKER,
}


def test_every_known_cause_carries_its_own_marker(tmp_path, monkeypatch, capsys):
    """COMPLETENESS PIN — each of the four causes must survive independently.

    Written against observable deny text only, importing no post-fix symbol, so
    it carries revert-cardinality rather than producing an import artifact.

    All four are checked in ONE body deliberately. Split into four tests, each
    would still fail on its own deletion — but the coupling is what makes the
    *set* the unit under test: a future edit that drops a cause AND its test
    together has to delete a named entry from the mapping above, which is a
    visible act, rather than quietly shrinking prose.

    The count is asserted as EXACTLY ONE rather than merely present: a marker
    appearing twice means the span it identifies is no longer unique, so the
    pin would no longer distinguish which cause survived.

    THIS TEST PASSING IS NOT WHAT MAKES IT SOUND. An absence-shaped property
    ("no cause can be deleted silently") is only established by deleting each
    cause and watching this fail — which is how the gap it closes was found in
    the first place, and which was re-run against this test after writing it.
    """
    _full_setup(monkeypatch, tmp_path, tasks=(("someone-else", "pending"),))
    _write_project_claude_md(monkeypatch, tmp_path, _LIVE_SESSION_ID)
    _reset_context_caches(monkeypatch)

    code, out = _run_main(_make_input(), capsys)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]

    # Without this the enumeration never appended and every check below would
    # be reporting on a string that was never produced.
    assert code == 2, "rule 8 must actually DENY, else this test is vacuous"
    assert _ENUM_MARKER in reason, "enumeration absent — nothing to check"

    for cause, marker in _CAUSE_MARKERS.items():
        assert marker.isascii(), (
            f"cause ({cause}) marker is not ASCII-only: {marker!r}. A marker "
            "that spans a non-ASCII character invites a transcription that "
            "substitutes a hyphen for U+2014, after which it can never fail."
        )

    missing = {
        cause: reason.count(marker)
        for cause, marker in _CAUSE_MARKERS.items()
        if reason.count(marker) != 1
    }
    assert not missing, (
        "every KNOWN CAUSE must appear exactly once in the emitted deny; "
        f"occurrence counts off for {missing}. A cause whose marker is gone "
        "has been deleted or reworded out of the enumeration, and the reader "
        "who is in that state is now given a list that does not contain them."
    )


# ══════════════════════════════════════════════════════════════════════════
# Advice soundness — the branch arms NARROW, they do not CLOSE
# ══════════════════════════════════════════════════════════════════════════

# The disclaimer that makes the "all four present" arm honest. This is the
# POSITIVE half and it is the half that generalises: any reintroduction of a
# closed-set claim has to remove or contradict this sentence, whereas the
# denylist below only catches wordings already seen.
_NARROWING_DISCLAIMER = "rules out one cause, not the rest"

# Wordings that would restore the FALSE-EXHAUSTIVENESS claim: naming the
# complement of case (3) as a closed set. Unsound because the four causes are
# the KNOWN ones, not a proven-exhaustive set — a task created and then cleared
# from the store before the spawn is evaluated leaves this gate denying against
# a store that is readable, correctly resolved and genuinely empty, and the
# clearing trigger is not understood well enough to exclude it.
#
# SCOPED TO THE COMPLEMENT CLAIM. "you are in case (4)" is deliberately absent
# from this set: it is the SOUND positive proof already in the text (a
# permissions error PROVES cause (4)), and a guard that forbade the shipped
# sound construction would be deleted by the first author who hit it. Each
# entry below was checked against the live constant for false positives.
_FORBIDDEN_EXHAUSTIVENESS_CLAIMS = (
    "you are in case (1)",
    "you are in case (2)",
    "must be case",
    "must be one of",
    "one of cases",
    "leaves only case",
)


def test_branch_arm_narrows_and_makes_no_closed_set_claim(
    tmp_path, monkeypatch, capsys
):
    """The self-check may eliminate case (3) and must claim nothing further.

    COUPLED: the positive disclaimer and the forbidden complement wordings are
    asserted against the SAME emitted text. Absence-only would pass trivially
    on an empty string, and on a revert where no enumeration exists at all.

    KNOWN BOUND, stated so a green result is not over-read. The negative half
    is a phrase denylist over free prose and CANNOT be complete — a complement
    claim in novel wording passes it. That limitation is real and is the same
    one the cause-(4) tripwire declares about itself. What carries the weight
    here is the POSITIVE half: the disclaimer must be present, and restoring a
    closed-set claim while leaving "rules out one cause, not the rest" in place
    produces text that contradicts itself in adjacent sentences. Do not cite
    this test as proof that the text makes no closed-set claim.
    """
    _full_setup(monkeypatch, tmp_path, tasks=(("someone-else", "pending"),))
    _write_project_claude_md(monkeypatch, tmp_path, _LIVE_SESSION_ID)
    _reset_context_caches(monkeypatch)

    code, out = _run_main(_make_input(), capsys)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]

    assert code == 2, "rule 8 must actually DENY, else this test is vacuous"

    # POSITIVE half — without this the absence checks below prove nothing.
    assert _NARROWING_DISCLAIMER in reason, (
        "the narrowing disclaimer is gone. The self-check discriminates case "
        "(3) from not-(3) and nothing else; without this sentence the arm "
        "reads as though it had identified the cause."
    )

    lowered = reason.lower()
    for phrase in _FORBIDDEN_EXHAUSTIVENESS_CLAIMS:
        assert phrase.lower() not in lowered, (
            f"closed-set claim reintroduced: {phrase!r}. Ruling out case (3) "
            "does not place the reader in the remaining listed causes — the "
            "enumeration is the KNOWN set, not a proven-exhaustive one, and "
            "naming the complement sends a reader outside it to chase causes "
            "that are not theirs."
        )


# ══════════════════════════════════════════════════════════════════════════
# Degraded-detection input still reaches the enumeration
# ══════════════════════════════════════════════════════════════════════════


def test_claude_md_absent_still_appends_the_enumeration(
    tmp_path, monkeypatch, capsys
):
    """With NO project CLAUDE.md the detector cannot compare recorded-vs-live
    and returns None, so the enumeration must append.

    Distinct from the healthy leg, which writes a CLAUDE.md recording the LIVE
    id and exercises the recorded==actual branch; here no file exists on either
    lookup path and the ``content is None`` branch runs instead.

    The sibling file covers this input for the stale-marker-ABSENCE cell and
    says so in its own docstring. Nothing there asserts the enumeration is
    PRESENT, so a composer that silently stopped appending on this path would
    not have been caught. This is the ADDED cell, not a tightened one.
    """
    _full_setup(monkeypatch, tmp_path, tasks=(("someone-else", "pending"),))

    empty_project = tmp_path / "no_claude_md_project"
    empty_project.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(empty_project))
    assert not (empty_project / "CLAUDE.md").exists()
    assert not (empty_project / ".claude" / "CLAUDE.md").exists()
    _reset_context_caches(monkeypatch)

    code, out = _run_main(_make_input(), capsys)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]

    assert code == 2, "decision unchanged (DENY) when CLAUDE.md is absent"
    _assert_deny_text(reason, rule_text="no Task assigned", enumeration=True)
    assert _STALE_MARKER not in reason, "no stale diagnosis without a CLAUDE.md"


# ══════════════════════════════════════════════════════════════════════════
# A-xor-B on the OTHER stale-diagnosable rule
# ══════════════════════════════════════════════════════════════════════════


def _setup_rule_six(monkeypatch, root):
    """Force rule ⑥ (team_name_unavailable) by writing an EMPTY context team
    name. Local to this file rather than shared with the sibling: importing it
    would execute that module at import time for no other benefit.

    The cache resets are load-bearing — ``_full_setup`` has already warmed
    pact_context with a VALID team, so rewriting the file alone leaves the gate
    reading the cached good value and rule ⑥ never fires.
    """
    import shared.pact_context as ctx_module

    plugin_root = _full_setup(monkeypatch, root)
    ctx_path = root / "pact-session-context.json"
    ctx_path.write_text(
        json.dumps(
            {
                "team_name": "",
                "session_id": _LIVE_SESSION_ID,
                "project_dir": str(root / "project"),
                "plugin_root": str(plugin_root),
                "started_at": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ctx_module, "_context_path", ctx_path)
    _reset_context_caches(monkeypatch)
    return plugin_root


def test_rule_six_under_stale_mismatch_gets_incumbent_without_enumeration(
    tmp_path, monkeypatch, capsys
):
    """Rule ⑥ WITH a stale mismatch: the incumbent fires and the enumeration
    must stay away.

    The sibling file asserts the stale marker PRESENT on this input but never
    asserts the enumeration ABSENT, so an A-xor-B break on rule ⑥ — the other
    member of the stale-diagnosable set — was silent. Coupled here: the
    incumbent's marker present AND the enumeration's markers absent, against
    the same emitted text, so neither half can pass by absence.
    """
    _setup_rule_six(monkeypatch, tmp_path)
    _write_project_claude_md(monkeypatch, tmp_path, _STALE_SESSION_ID)
    _reset_context_caches(monkeypatch)

    code, out = _run_main(_make_input(), capsys)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]

    assert code == 2, "rule 6 must actually fire, else this test is vacuous"
    assert _STALE_MARKER in reason, "incumbent stale diagnosis must fire"
    _assert_deny_text(reason, rule_text="team_name is unavailable",
                      enumeration=False)


# ══════════════════════════════════════════════════════════════════════════
# The surviving AC-2 invariant: no output change where neither block applies
# ══════════════════════════════════════════════════════════════════════════


def _journaled(journal, index):
    """Return (rule, reason) for the index-th dispatch_decision event.

    Asserting on the journaled RULE — not merely on the exit code — is what
    stops a leg certifying nothing. Exit 2 is reachable by every deny rule in
    the gate, so a leg that meant to exercise one rule and actually fired
    another looks identical from the outside.
    """
    events = [e for e in journal if e.get("type") == "dispatch_decision"]
    assert len(events) > index, (
        f"expected at least {index + 1} dispatch_decision events, "
        f"got {len(events)}"
    )
    return events[index].get("rule"), events[index].get("reason")


def test_emitted_equals_journaled_except_where_a_block_applies(
    tmp_path, monkeypatch, capsys
):
    """AC-2, SATISFIED AS AMENDED — the invariant that survived the design.

    AC-2 as written ("no output change for the ordinary 'lead skipped task
    creation' case") rests on a discriminator that no longer exists, and the
    case it names is cause (1), whose output changes BY CONSTRUCTION — that is
    the feature. The surviving, testable invariant is this: where NEITHER
    diagnosis block applies, the emitted deny is byte-identical to the
    canonical reason; where one does, the emitted text EXTENDS that reason and
    never rewrites it.

    The journal is legitimate ground truth for "canonical", because journalling
    provably precedes augmentation. That also keeps both legs free of post-fix
    symbols, so each survives a revert as a behavioural failure.

    COUPLED IN ONE BODY. Leg 1 alone is an equality that a composer doing
    nothing at all would satisfy; leg 2 alone cannot tell "extended" from
    "rewritten and coincidentally longer". Together they pin the boundary.
    """
    journal = _capture_journal(monkeypatch)

    # ── Leg 1 — rule ⑥, no mismatch: neither block applies ──
    leg_1 = tmp_path / "leg_untouched"
    leg_1.mkdir()
    _setup_rule_six(monkeypatch, leg_1)
    _write_project_claude_md(monkeypatch, leg_1, _LIVE_SESSION_ID)
    _reset_context_caches(monkeypatch)

    code_1, out_1 = _run_main(_make_input(), capsys)
    emitted_1 = out_1["hookSpecificOutput"]["permissionDecisionReason"]
    rule_1, journaled_1 = _journaled(journal, 0)

    assert code_1 == 2, "leg 1 must DENY"
    assert rule_1 == "team_name_unavailable", (
        f"leg 1 fired {rule_1!r}, not the rule it was built to exercise — "
        "the row certifies nothing about its intent"
    )
    assert emitted_1 == journaled_1, (
        "where neither block applies the user-facing text must be the "
        "canonical reason, byte for byte"
    )

    # ── Leg 2 — rule ⑧, no mismatch: the enumeration appends ──
    leg_2 = tmp_path / "leg_extended"
    leg_2.mkdir()
    _full_setup(monkeypatch, leg_2, tasks=(("someone-else", "pending"),))
    _write_project_claude_md(monkeypatch, leg_2, _LIVE_SESSION_ID)
    _reset_context_caches(monkeypatch)

    code_2, out_2 = _run_main(_make_input(), capsys)
    emitted_2 = out_2["hookSpecificOutput"]["permissionDecisionReason"]
    rule_2, journaled_2 = _journaled(journal, 1)

    assert code_2 == 2, "leg 2 must DENY"
    assert rule_2 == "no_task_assigned", (
        f"leg 2 fired {rule_2!r}, not the rule it was built to exercise"
    )
    assert emitted_2 != journaled_2, (
        "leg 2 must actually be augmented, else leg 1's equality proves only "
        "that nothing anywhere appends"
    )
    assert emitted_2.startswith(journaled_2), (
        "the emitted text must EXTEND the canonical reason, not rewrite it"
    )
    assert _ENUM_MARKER not in journaled_2, (
        "the journal must keep the un-augmented reason"
    )


# ══════════════════════════════════════════════════════════════════════════
# Cause (4) as a RUNTIME STATE — an unreadable store, not just wording
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(
    os.geteuid() == 0,
    reason="mode 0o000 does not deny root, so the unreadable-store leg cannot "
           "be constructed; the test would pass or fail for the wrong reason",
)
def test_unreadable_store_denies_while_the_owner_matching_task_survives(
    tmp_path, monkeypatch, capsys
):
    """THE OWNER-MATCH LEVER — cause (4) reached as real state, no mock.

    The gate cannot distinguish an unreadable store from an empty one, which
    is why cause (4) is in the enumeration at all. This pins that claim as
    BEHAVIOUR rather than as text: the store holds a task owned by the very
    name being dispatched, so the ONLY thing standing between ALLOW and DENY
    is whether the directory can be read.

    Non-mocked seam by construction — a real directory, a real mode change,
    the unstubbed ``iter_team_task_jsons`` read. Mocking the iterator would
    replace exactly the seam under test.

    THREE GUARDS AGAINST CERTIFYING NOTHING, because two states produce a
    rule-⑧ deny and this test asserts a claim about which one it is:
      * the readable leg must ALLOW — establishing the task is genuinely
        there and matching, so the deny cannot be blamed on a missing task;
      * the journaled RULE must be no_task_assigned — exit 2 alone is
        reachable by every deny rule, and by the load-failure emitter;
      * after the mode is restored the task file is re-read and asserted
        unchanged — so "only the readability moved" is asserted, not argued.
    """
    _full_setup(monkeypatch, tmp_path)  # default task owner == the dispatch name
    tasks_dir = tmp_path / ".claude" / "tasks" / _TEAM
    task_files = sorted(tasks_dir.glob("*.json"))
    assert task_files, "fixture must seed at least one task file"
    before = task_files[0].read_text(encoding="utf-8")
    assert json.loads(before)["owner"] == _NAME, (
        "the lever requires a task owned by the dispatched name"
    )

    journal = _capture_journal(monkeypatch)

    # ── Readable: the task is found, the dispatch is allowed ──
    _reset_context_caches(monkeypatch)
    code_readable, out_readable = _run_main(_make_input(), capsys)
    assert code_readable == 0, "a matching task in a readable store must ALLOW"
    assert out_readable.get("suppressOutput") is True

    # ── Unreadable: same store, same task, same input ──
    original_mode = stat.S_IMODE(tasks_dir.stat().st_mode)
    os.chmod(tasks_dir, 0o000)
    try:
        # The state the diagnosis's positive arm describes, established as an
        # OBSERVATION before the gate runs. Without this the assertion further
        # down would only show that the sentence is PRESENT, not that it is
        # TRUE here — which is the whole difference between pinning the text
        # and pinning the advice.
        with pytest.raises(PermissionError):
            os.listdir(tasks_dir)

        _reset_context_caches(monkeypatch)
        code_unreadable, out_unreadable = _run_main(_make_input(), capsys)
    finally:
        # Restored before any assertion can fail out of the block: a 0o000
        # directory left behind outlives this test and breaks unrelated ones.
        os.chmod(tasks_dir, original_mode)

    reason = out_unreadable["hookSpecificOutput"]["permissionDecisionReason"]
    rule, _journaled_reason = _journaled(journal, 1)

    assert code_unreadable == 2, "an unreadable store must DENY"
    assert rule == "no_task_assigned", (
        f"expected the no-task rule, got {rule!r} — an unreadable store must "
        "reach the same conflated rule a genuinely empty one does, which is "
        "the whole reason cause (4) is enumerated"
    )
    _assert_deny_text(reason, rule_text="no Task assigned", enumeration=True)

    assert task_files[0].read_text(encoding="utf-8") == before, (
        "the task file must be untouched — if the contents moved, the deny is "
        "not evidence about readability"
    )
    assert stat.S_IMODE(tasks_dir.stat().st_mode) == original_mode, (
        "the store's mode must be restored"
    )

    # THE ADVICE, NOT MERELY THE TEXT. The self-check has exactly one arm that
    # makes a proof-strength claim: a permissions error on the store PROVES
    # cause (4). Every other test in this file can only show that sentence is
    # PRESENT. This is the one scenario that is actually IN the state the
    # sentence describes — the listing above genuinely raised PermissionError —
    # so here the claim is verified rather than quoted.
    assert _CAUSE4_POSITIVE_PROOF in reason, (
        "the sound positive arm is missing from a deny emitted in exactly the "
        "state it describes: the store raised a permissions error and the "
        "reader is not told that this proves cause (4)"
    )


# ══════════════════════════════════════════════════════════════════════════
# The value property the A-xor-B inference rests on
# ══════════════════════════════════════════════════════════════════════════


def test_fired_incumbent_produces_a_strictly_longer_message(
    tmp_path, monkeypatch, capsys
):
    """When the incumbent fires it must return STRICTLY MORE than it was given.

    The composer infers "the incumbent fired" from a value difference rather
    than from a flag. That inference is sound only while the fire path is
    append-only with a NON-EMPTY suffix — and nothing was pinning the non-empty
    part. The concrete regression it admits: shrink the appended text to
    nothing and a fired incumbent returns the message unchanged, the composer
    reads that as "did not fire", and the enumeration appends on top of a case
    the design deliberately suppresses. Silent, and in the wrong direction.

    THE HALF THIS DOES NOT COVER, so it is not mistaken for the whole: "no
    second path can ever change the message without the incumbent firing" is a
    universal claim over future code and no test expresses it. That half stays
    a declared residual with an escalation trigger in the composer docstring.
    Only the VALUE property is testable today, and it is testable cheaply.

    Coupled against one run: the stale marker present (the incumbent really
    fired), the emitted text strictly longer than the journaled reason, and the
    enumeration absent. Length alone would be satisfied by the enumeration
    appending, which is the very case this must distinguish.
    """
    journal = _capture_journal(monkeypatch)

    _full_setup(monkeypatch, tmp_path, tasks=(("someone-else", "pending"),))
    _write_project_claude_md(monkeypatch, tmp_path, _STALE_SESSION_ID)
    _reset_context_caches(monkeypatch)

    code, out = _run_main(_make_input(), capsys)
    emitted = out["hookSpecificOutput"]["permissionDecisionReason"]
    rule, journaled = _journaled(journal, 0)

    assert code == 2, "the deny must fire"
    assert rule == "no_task_assigned", f"expected the no-task rule, got {rule!r}"

    # Guards the derived marker itself: _STALE_MARKER is a slice of a
    # production constant, and an empty constant would make every
    # "_STALE_MARKER in reason" assertion in this file vacuously true and every
    # "not in" assertion fail — so pin non-emptiness rather than assume it.
    assert _STALE_MARKER, (
        "the derived stale marker is empty — every assertion using it is now "
        "either vacuous or inverted"
    )
    assert _STALE_MARKER in emitted, "the incumbent must actually have fired"

    assert len(emitted) > len(journaled), (
        "a fired incumbent must return strictly more than it was given; an "
        "empty suffix makes the composer's fired-vs-not inference undecidable"
    )
    assert emitted.startswith(journaled), "the fire path must be append-only"
    assert _ENUM_MARKER not in emitted, (
        "the enumeration must stay suppressed when the incumbent fired — if it "
        "appended here, the length check above passed for the wrong reason"
    )


# ══════════════════════════════════════════════════════════════════════════
# The pointer has a referent — and that is ALL this section pins
# ══════════════════════════════════════════════════════════════════════════

# Repo root, per the convention already used by the version-bump suite. Used
# ONLY for the anchor-referent leg below — see the shipped-vs-dev note there.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# THE SHIPPED ARTIFACT. marketplace.json packages `./pact-plugin` only, so this
# README is what a consumer actually receives; the repo-root one is NOT
# packaged. Any pin aimed solely at the repo root tests the DEV surface.
_SHIPPED_README = Path(__file__).resolve().parent.parent / "README.md"

# Read as SOURCE rather than imported: the dispatch-gate pointer lives inside a
# post-fix constant, and importing it would make this an import artifact under
# a revert. Reading the file also keeps one assertion covering both gates.
_GATE_SOURCES = ("dispatch_gate.py", "bootstrap_gate.py")

# DERIVED from the gate sources, never hand-typed. A hand-typed expected URL is
# the failure mode this whole test exists to catch, one level up: it would go
# stale silently the moment the gates changed, and its greenness would then
# certify agreement with a string nobody emits.
_POINTER_URL_RE = re.compile(r"https://github\.com/[^\s\"'\\]+#[^\s\"'\\]+")

# THE VERIFIED PAIR — anchor as GitHub actually serves it, and the heading that
# produces it. Measured, not derived; see the anchor-referent leg for the exact
# command and why a computed slug would be unsound here. Both halves are pinned
# so an edit to EITHER side reds, which is what makes this a correspondence
# check rather than a restatement of one side.
_VERIFIED_ANCHOR = "enabling-agent-teams"
_VERIFIED_HEADING = "### Enabling Agent Teams"


def _pointer_urls(source_name):
    """Every anchored GitHub URL emitted by one gate's source."""
    source = (Path(__file__).parent.parent / "hooks" / source_name).read_text(
        encoding="utf-8"
    )
    return set(_POINTER_URL_RE.findall(source))


def test_readme_pointer_has_a_referent():
    """Both gates emit a pointer URL; it must resolve from what a consumer has.

    A deny message that sends an operator somewhere that is not there is worse
    than one that says nothing — it spends the reader's remaining patience on a
    search that cannot succeed. The artifacts are edited by different people at
    different times, and nothing else couples them.

    WHAT THIS PIN DEFENDS, STATED NARROWLY. A full URL in a deny message is
    SELF-RESOLVING: the reader can follow it holding no README at all. So the
    pointer's reachability is the URL's doing, not this test's. What this test
    defends is that the URL is CORRECT — that its anchor has a real referent and
    that the two gates have not drifted apart. Do not cite it as "consumers can
    reach the docs"; that claim belongs to the URL and would over-read a green.

    RESOLVE THE SUBJECT THE WAY THE READER DOES — the rule that fixes the aim.
    An earlier version asserted a heading in the repo-root README while the
    pointer named a SECTION BY TITLE, so the pin resolved a file the pointer did
    not name; it stayed green while the pointer dangled. The lesson is NOT
    "always aim at the packaged path" — under a URL pointer the packaged README
    is the wrong target and such an assertion could not pass, because the
    heading legitimately is not there. The aim follows the POINTER: under a URL,
    the file that URL serves. Whenever the pointer's FORM changes, re-derive
    this aim rather than carrying it over.

    THE URL IS DERIVED FROM THE GATE SOURCES, never hand-typed. A hand-typed
    expected URL is the same failure one level up — it goes stale silently the
    moment the gates change, and then agrees with a string nobody emits.

    COUPLED so it cannot pass by absence. Testing only the implication ("if a
    pointer is present then it resolves") would pass trivially the moment
    someone removed the pointer, which is exactly the state this is meant to
    notice. Removing a pointer legitimately is still allowed — it just has to
    come here and say so, the same two-site review path the canonical
    deny-reason literal forces. The four legs, in order of what they carry:

      ESSENTIAL — the anchor corresponds to a real heading in the ROOT README,
        the file the URL serves. Under a URL pointer this is the correctness
        case; the shipped README's own links to this anchor depend on it too.
      ANTI-REGRESSION — both gates carry a URL rather than a bare section name,
        catching the future author who "simplifies" the pointer back to a
        readable title. That simplification is what produced the original
        defect, so this leg is the one guarding against its return.
      ANTI-DRIFT — the two gates carry the IDENTICAL URL, so they cannot half-
        answer a reader the other sent somewhere else.
      CROSS-CHECK — the URL also appears in the shipped README. Corroborating
        rather than primary under a URL pointer, but it is the leg whose
        non-vacuity was demonstrated by mutation (the predecessor pin stayed
        GREEN under the same mutation that reds this one), so it is the one
        with direct evidence behind it. Do not drop it as redundant.

    WHAT THIS DOES NOT PIN, and must not be read as pinning. The clause in the
    bootstrap gate is safe to over-point partly because the reader is told how
    to confirm they have this problem before being shown a high-blast-radius
    setting.

    BE PRECISE ABOUT WHERE THAT PROTECTION LIVES — an earlier draft of this
    docstring said the referent "opens with a falsifiable check", and it does
    not. The pointed-at section opens with the Agent Teams settings block. The
    check lives further down, inside its "If specialist agents will not spawn"
    SUBSECTION, which opens with "First, confirm this is what you are hitting"
    and only then reaches "The setting". So the ordering invariant is
    check-before-remedy WITHIN THAT SUBSECTION, not at the top of the section
    the gates name. The distinction matters because this paragraph exists for a
    future auditor re-checking the ordering: one who reads "opens with" lands at
    the section heading, finds a settings block and no check, and concludes the
    protection has already been lost when it has not.

    That protection is a property of the PAIR, not of either file: reorder the
    subsection to lead with the remedy and the protection is gone while this
    test stays green. Verification-before-remedy ordering is a review-time
    invariant with no artifact that can enforce it. This test pins referent
    EXISTENCE only.
    """
    per_gate = {name: _pointer_urls(name) for name in _GATE_SOURCES}

    pointing = sorted(name for name, urls in per_gate.items() if urls)
    assert pointing == sorted(_GATE_SOURCES), (
        f"expected both gates to carry an anchored pointer URL; only {pointing} "
        "do. If a pointer was removed on purpose, update this test in the same "
        "change — silently dropping it is what this assertion exists to catch."
    )

    emitted = set().union(*per_gate.values())
    assert len(emitted) == 1, (
        f"the gates point at {len(emitted)} different URLs: {sorted(emitted)}. "
        "One referent, or the two gates drift apart and each half-answers a "
        "reader the other sent somewhere else."
    )
    url = emitted.pop()

    # THE SHIPPED-SURFACE LEG. This is the one that must not be aimed at the
    # repo root: that README is not packaged, so a pin against it stays green
    # under BOTH the broken and the fixed state, and its greenness then reads as
    # confirmation of a property it never checked.
    assert _SHIPPED_README.exists(), (
        f"shipped README not found at {_SHIPPED_README} — this leg cannot mean "
        "anything until it points at a file that exists"
    )
    shipped_text = _SHIPPED_README.read_text(encoding="utf-8")
    assert url in shipped_text, (
        f"the gates emit {url!r}, which does not appear in the SHIPPED README "
        f"({_SHIPPED_README.name}). marketplace.json packages ./pact-plugin "
        "only, so that file is the artifact a consumer receives. A URL the "
        "shipped docs never mention is one nobody can cross-check against the "
        "artifact they actually have."
    )

    # ANCHOR-REFERENT LEG — THE ESSENTIAL ONE under a URL pointer. The URL names
    # the REPO-ROOT README, so that file is the artifact the pointer actually
    # resolves to; this leg is about GitHub's rendering target, NOT about what
    # ships, and the two are different files ON PURPOSE.
    #
    # THE PAIR BELOW IS A MEASUREMENT, NOT A COMPUTATION, and that distinction
    # is the whole reason it is written this way. Deriving the slug from the
    # heading with an assumed lowercase-and-hyphenate rule would pass against a
    # slug GitHub does not actually serve — this repo has a pinned precedent of
    # a heading containing an em-dash, which strips to EMPTY and yields a
    # DOUBLED hyphen where the obvious rule predicts a single one. A computed
    # slug would then certify the transform rather than the correspondence.
    #
    # Verified empirically against GitHub's own renderer rather than assumed:
    #     gh api repos/<owner>/<repo>/readme -H "Accept: application/vnd.github.html"
    # which returned id="user-content-enabling-agent-teams" for this heading.
    # Re-run that command if either side of the pair below changes; do NOT
    # "simplify" this into a slugger.
    anchor = url.split("#", 1)[1]
    assert anchor == _VERIFIED_ANCHOR, (
        f"the gates now point at anchor {anchor!r}, but the verified pair pins "
        f"{_VERIFIED_ANCHOR!r}. Re-verify the anchor against GitHub's rendered "
        "HTML and update BOTH halves of the pair — do not compute the new slug."
    )

    root_readme = _REPO_ROOT / "README.md"
    assert root_readme.exists(), f"repo README not found at {root_readme}"
    assert _VERIFIED_HEADING in root_readme.read_text(encoding="utf-8"), (
        f"the gates point at anchor {anchor!r}, whose verified referent "
        f"{_VERIFIED_HEADING!r} is not in the repo-root README that GitHub "
        "renders at that URL. Either the heading was renamed and the gate texts "
        "were not updated, or the pointer shipped ahead of its referent. The "
        "shipped README's own links to this anchor break too."
    )


# ══════════════════════════════════════════════════════════════════════════
# No silent "just set this" — the setting never appears without its cost
# ══════════════════════════════════════════════════════════════════════════

# The setting has exactly ONE spelling and no synonym set. That is what makes
# the TRIGGER side of this pin complete — see the disclaimer below for the side
# that is not.
_SETTING = "DISABLE_GROWTHBOOK"

# Derived from the acceptance criteria, not composed fresh: the blast-radius
# statement, the verification step, and the retirement note. The remaining
# criteria (the prerequisite/env-block pairing, and project-agnostic wording)
# are not co-occurrence properties and are deliberately out of scope here.
#
# Matched on SUBSTANTIVE CONTENT rather than on the bold label that introduces
# each one, so relabelling a paragraph does not red while DELETING it does. A
# label is presentation; these strings are the claim the criterion asks for.
_DISCLOSURE_ELEMENTS = {
    "blast-radius statement": "remote configuration channel",
    "verification step": "select:TaskCreate,TaskUpdate,TaskList,TaskGet",
    "retirement note": "not a standing recommendation",
}

_DOC_SURFACES = ("README.md", "pact-plugin/README.md")


def _sections(text):
    """Split markdown into (heading, body) at every ATX heading, FENCE-AWARE.

    Lines inside ``` fences are never treated as headings. This matters
    concretely rather than theoretically: the root README has a fenced shell
    block whose comment lines begin with '#', and a naive splitter segments on
    them, silently moving the section boundary and letting the co-occurrence
    check straddle a heading it should have stopped at.
    """
    sections, heading, body, fenced = [], "<preamble>", [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and line.startswith("#"):
            sections.append((heading, "\n".join(body)))
            heading, body = line.strip(), []
        else:
            body.append(line)
    sections.append((heading, "\n".join(body)))
    return sections


def test_setting_is_never_named_without_its_cost():
    """Wherever the setting is NAMED, its cost is disclosed in the SAME section.

    WHAT THIS PROVES. The setting cannot be mentioned in an operator-facing
    markdown surface without the blast-radius statement, the verification step
    and the retirement note appearing in the same section. On the TRIGGER side
    this is COMPLETE, and that is unusual enough to be worth stating plainly:
    ``DISABLE_GROWTHBOOK`` is an environment variable with exactly one spelling
    and no synonyms, so a mention cannot be reworded out of detection the way a
    prose concept could. Nobody can add the setting to these files and evade
    this check except by not adding it.

    WHAT THIS DOES NOT PROVE, and it must not be cited for any of it. The
    SATISFACTION side is incomplete in three separate ways. (1) It cannot judge
    ADEQUACY — that a blast-radius paragraph exists says nothing about whether
    it describes the actual blast radius, or describes it correctly. (2) It
    cannot judge ADJACENCY IN RENDERED OUTPUT — section containment is not
    reading order, and an element can satisfy this check while sitting far
    enough from the setting that a scrolling reader never connects them.
    (3) It cannot judge SUFFICIENCY OF THE SET — these three elements come from
    the acceptance criteria, and criteria can be incomplete. So a green result
    here means "the setting is not named silently"; it does NOT mean "the
    trade-off is adequately disclosed". Do not cite it for the latter.

    WHICH REVIEW STEP OWNS THE REST. Human review of this subsection's CONTENT
    at PR time, on the same two-site path the deny-reason literal forces: a
    change that touches the setting's documentation must re-read the cost
    paragraph rather than confirm a green. That includes the ordering invariant
    named in ``test_readme_pointer_has_a_referent`` — verification before
    remedy — which no artifact can enforce and which this test does not attempt
    to. If the sole evidence offered for adequate disclosure is that this test
    passed, the review step has been skipped, not satisfied.

    COUPLED against the absence trap. The co-occurrence rule is an implication,
    and an implication with no true antecedent is free: delete the whole
    subsection and every co-occurrence check below passes trivially. So the
    premise is asserted FIRST — the setting must actually be documented
    somewhere — which is the same coupling discipline the enumeration pins use.
    """
    for name, marker in _DISCLOSURE_ELEMENTS.items():
        assert marker.isascii(), (
            f"{name} marker is not ASCII-only: {marker!r}. A marker spanning a "
            "non-ASCII character invites a transcription that substitutes a "
            "hyphen for U+2014, after which it silently stops matching."
        )

    surfaces = {}
    for rel in _DOC_SURFACES:
        path = _REPO_ROOT / rel
        assert path.exists(), f"operator-facing surface missing: {path}"
        surfaces[rel] = path.read_text(encoding="utf-8")

    # PREMISE — without this the implication below is vacuous.
    documenting = [rel for rel, text in surfaces.items() if _SETTING in text]
    assert documenting, (
        f"{_SETTING} is documented in NONE of {list(_DOC_SURFACES)}, so every "
        "co-occurrence assertion below passes by absence and this test proves "
        "nothing. If the setting was removed on purpose, delete this test in "
        "the same change rather than leaving it green and hollow."
    )

    for rel in documenting:
        naming = [
            (h, b) for h, b in _sections(surfaces[rel]) if _SETTING in b or _SETTING in h
        ]
        assert naming, f"{_SETTING} present in {rel} but in no section"
        for heading, body in naming:
            missing = {
                name: marker
                for name, marker in _DISCLOSURE_ELEMENTS.items()
                if marker not in body
            }
            assert not missing, (
                f"{rel} names {_SETTING} under {heading!r} without, in the same "
                f"section: {sorted(missing)}. A reader who applies the setting "
                "from this section alone does so without being told what it "
                "costs, how to confirm they need it, or when to take it back "
                "out. Add the element to THIS section — moving it elsewhere in "
                "the file satisfies a grep but not the reader."
            )


def test_gate_texts_point_at_the_setting_and_never_inline_it():
    """The gates carry a POINTER; they must never inline the setting itself.

    A deny message cannot carry the cost statement, the verification step and
    the retirement note — so a gate that names the setting has necessarily
    named it silently, which is the one form of disclosure the criteria
    forbid outright. The pointer exists precisely so the setting is only ever
    encountered next to its trade-off.

    COUPLED, because a bare absence assertion here would pass if the gate texts
    were emptied, deleted or renamed out of existence: the positive half
    asserts each gate still carries its pointer URL. Absence of the setting is
    only meaningful while the pointer is present to replace it.
    """
    for source_name in _GATE_SOURCES:
        source = (Path(__file__).parent.parent / "hooks" / source_name).read_text(
            encoding="utf-8"
        )
        assert _pointer_urls(source_name), (
            f"{source_name} carries no pointer URL, so the absence check below "
            "is vacuous — it would pass on an empty file."
        )
        assert _SETTING not in source, (
            f"{source_name} inlines {_SETTING} into gate text. A deny message "
            "cannot carry the blast radius, the verification step and the "
            "retirement note, so naming the setting here discloses it "
            "silently. Point at the documented section instead."
        )
