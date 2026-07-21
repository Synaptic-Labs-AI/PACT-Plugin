"""
Cause enumeration appended to the rule-⑧ (``no_task_assigned``) deny.

Rule ⑧ fires whenever no task for this owner is OBSERVED, which conflates a
genuinely-missing task with an unreadable store, a symlink escape, an unsafe
team name and an I/O error. ``_compose_deny_diagnosis`` therefore appends a
four-cause enumeration plus a self-check the reader can run — but only when the
incumbent stale-team detector did NOT fire, and only on rule ⑧.

SCOPE OF THIS FILE — deliberately partial. It covers the A-xor-B precedence,
the graceful-degradation path, rule isolation, and the journal-purity property.
It is NOT the full matrix: the T1-T9 integration matrix and the coupled
verbatim-equality pin are authored separately and extend THIS file. Do not read
a green run here as comprehensive coverage of the enumeration.

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
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from test_dispatch_gate import (  # noqa: E402 — sibling harness reuse
    _make_input,
    _run_main,
    _full_setup,
    _TEAM,
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
