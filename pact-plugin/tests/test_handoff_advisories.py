"""The two HANDOFF schema advisories wired into task_lifecycle_gate:

  handoff_schema_invalid_at_completion — lead-side, at the completing write
  handoff_schema_invalid_at_write_time — author-side, at their own write

EVERY ARM HERE IS A POSITIVE CONTROL OR ITS PAIRED NEGATIVE. A schema advisory
that never fires is indistinguishable from a green suite, so each SILENT row
sits beside a FIRES row built from the same fixture.

Both advisories key on a handoff that is PRESENT. Nothing here fires on a
completion carrying no handoff at all: signal completions, session briefings
and other exempt work legitimately store none, and "may this owner complete
without a HANDOFF?" is a question this gate does not answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import task_lifecycle_gate as tlg  # noqa: E402
from fixtures.emitter import VALID_HANDOFF as EMITTER_VALID_HANDOFF  # noqa: E402

# Cold round 10 added the produced TYPE gate to validate_handoff_schema (the
# watcher reads a list-typed `produced` as ABSENT and burns its whole
# deadline to a false EXPIRED), so this module's silence arms need a
# string-typed `produced`. The shared fixture keeps its list because five
# modules pin a content hash built from it — these arms use this derived
# string-typed view instead.
VALID_HANDOFF = {**EMITTER_VALID_HANDOFF, "produced": "src/auth.ts"}

TEAM = "pact-test"
LEAD = "PACT:pact-orchestrator"

# The case issue #1543 reports: legacy-spelled and otherwise perfect.
LEGACY_HANDOFF = {
    "produced": ["src/auth.ts"],
    "key_decisions": ["Used JWT"],
    "uncertainty": [],
    "integration": ["UserService"],
    "open_questions": [],
}


@pytest.fixture
def emit_events(monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(tlg, "append_event", lambda e: events.append(e) or True)
    return events


def _completed(*, subject="devops: implement", owner="devops", handoff=VALID_HANDOFF,
               task_type=None):
    """TaskUpdate(status=completed) post-state, as the lead's acceptance."""
    metadata: dict = {}
    if handoff is not None:
        metadata["handoff"] = handoff
    if task_type is not None:
        metadata["type"] = task_type
    return {
        "tool_name": "TaskUpdate",
        "agent_type": LEAD,
        "tool_input": {"taskId": "42", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "42",
                "subject": subject,
                "owner": owner,
                "metadata": metadata,
            }
        },
    }


def _write(handoff):
    """A non-completed metadata TaskUpdate — the author's own write."""
    metadata = {} if handoff is None else {"handoff": handoff}
    return {
        "tool_name": "TaskUpdate",
        "agent_type": LEAD,
        "tool_input": {"taskId": "42", "metadata": metadata},
        "tool_response": {},
    }


def _rules(payload, tmp_path, monkeypatch, pact_context):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name=TEAM, session_id="s1")
    return [rule for rule, _ in tlg.evaluate_lifecycle(payload)]


class TestCompletionTimeSchema:
    def test_fires_on_legacy_spelling(self, tmp_path, monkeypatch, pact_context,
                                      emit_events):
        rules = _rules(_completed(handoff=LEGACY_HANDOFF), tmp_path, monkeypatch,
                       pact_context)
        assert "handoff_schema_invalid_at_completion" in rules

    @pytest.mark.parametrize("handoff", [LEGACY_HANDOFF, VALID_HANDOFF],
                             ids=["legacy", "valid"])
    def test_populated_handoff_still_emits(self, handoff, tmp_path, monkeypatch,
                                           pact_context, emit_events):
        """One advisory, ZERO LOSS, zero blockage — and the same where no
        advisory is drawn at all.

        BOTH ROWS ARE LOAD-BEARING AND THEY COVER DIFFERENT THINGS. The legacy
        row shows an advisory does not cost the journal event. The valid row is
        the ONLY arm driving a non-empty dict past the completion gate while
        watching emits: every other emit-watching arm feeds a shape the gate
        rejects first and asserts ZERO, so without this row the positive
        direction of the emit path rests on the legacy case alone, and an emit
        eligibility narrowed to the advisory-drawing shapes would go unseen.
        """
        _rules(_completed(handoff=handoff), tmp_path, monkeypatch, pact_context)
        assert len(emit_events) == 1

    def test_silent_on_the_repos_valid_fixture(self, tmp_path, monkeypatch,
                                               pact_context, emit_events):
        rules = _rules(_completed(), tmp_path, monkeypatch, pact_context)
        assert "handoff_schema_invalid_at_completion" not in rules


class TestWriteTimeSchema:
    def test_fires_on_legacy_spelling(self, tmp_path, monkeypatch, pact_context):
        rules = _rules(_write(LEGACY_HANDOFF), tmp_path, monkeypatch, pact_context)
        assert "handoff_schema_invalid_at_write_time" in rules

    def test_silent_on_valid_handoff(self, tmp_path, monkeypatch, pact_context):
        rules = _rules(_write(VALID_HANDOFF), tmp_path, monkeypatch, pact_context)
        assert "handoff_schema_invalid_at_write_time" not in rules

    def test_silent_when_no_handoff_key(self, tmp_path, monkeypatch, pact_context):
        """A teammate writing only intentional_wait is not writing a HANDOFF.
        This silence is also what keeps the two-run advisory-equality pin in
        test_per_write_adversarial_matrix green."""
        rules = _rules(_write(None), tmp_path, monkeypatch, pact_context)
        assert "handoff_schema_invalid_at_write_time" not in rules


# The two wired sites, so one arm can assert both and catch them DIVERGING.
# What a wired arm buys over the validator's own tests in test_handoff_schema
# is not the validator's behaviour -- that is already pinned there -- but the
# WIRING: the gate passing the wrong object, applying a condition of its own
# before calling, or the two sites disagreeing.
SITES = [
    pytest.param(lambda h: _completed(handoff=h),
                 "handoff_schema_invalid_at_completion", id="completion"),
    pytest.param(_write, "handoff_schema_invalid_at_write_time", id="write_time"),
]


NON_DICT_HANDOFFS = ["not a handoff", ["produced"], 7]
NON_DICT_IDS = ["str", "list", "int"]


class TestBothSitesAgree:
    """The two sites DIVERGE on the malformed shape, deliberately, and these
    two arms pin the asymmetry rather than assuming symmetry.

    Malformed coverage is the WRITE-TIME advisory only. That is the reachable
    half: the completion site's own gate rejects a non-dict before the
    validator is ever called, so validate_handoff_schema's malformed branch is
    unreachable from there. Neither arm below can be derived from
    test_handoff_schema, which calls the validator with no gate in front of it
    and therefore sees the malformed branch from both directions.
    """

    @pytest.mark.parametrize("handoff", NON_DICT_HANDOFFS, ids=NON_DICT_IDS)
    def test_non_dict_fires_at_write_time(self, handoff, tmp_path, monkeypatch,
                                          pact_context):
        """The reachable half. The write-time gate is `is not None`, so a
        non-dict reaches the validator and the author is told the type they
        actually wrote."""
        rules = _rules(_write(handoff), tmp_path, monkeypatch, pact_context)
        assert "handoff_schema_invalid_at_write_time" in rules, (
            f"No advisory on a non-dict handoff ({type(handoff).__name__}) at "
            f"the write-time site. This is the ONLY site with malformed "
            f"coverage; losing it leaves the shape uncovered everywhere."
        )

    @pytest.mark.parametrize("handoff", NON_DICT_HANDOFFS + [{}],
                             ids=NON_DICT_IDS + ["empty_dict"])
    def test_completion_site_gate_precedes_the_validator(
        self, handoff, tmp_path, monkeypatch, pact_context, emit_events
    ):
        """The completion site draws nothing on a non-dict or an empty dict,
        because its gate tests dict-ness and non-emptiness BEFORE calling the
        validator -- not because the validator is silent on those shapes. It
        is not.

        PINNING A SILENCE, SO READ WHY IT IS NOT VACUOUS: the sibling arm
        above shows the same values DO fire at the other site, so this arm
        distinguishes gate-rejected from validator-silent. Reverting the
        completion gate to bare presence reds it.

        The asymmetry is deliberate, not an oversight to be tidied away:
        malformed coverage is write-time only, because that is the site that
        reaches the author while they can still fix it. Widening this gate to
        "restore symmetry" is the change this arm exists to stop.
        """
        rules = _rules(_completed(handoff=handoff), tmp_path, monkeypatch,
                       pact_context)
        assert "handoff_schema_invalid_at_completion" not in rules, (
            f"The completion advisory fired on {type(handoff).__name__}. Its "
            f"gate is meant to reject non-dict and empty handoffs before the "
            f"validator runs; malformed coverage belongs to the write-time "
            f"site, which reaches the author who can still fix it."
        )
        assert len(emit_events) == 0, (
            "A non-dict or empty handoff must produce no journal event -- the "
            "emit eligibility rejects it on the same two conditions."
        )

    @pytest.mark.parametrize("payload_for, rule", SITES)
    def test_complete_handoff_with_extra_keys_is_silent_at_both_sites(
        self, payload_for, rule, tmp_path, monkeypatch, pact_context, emit_events
    ):
        """The representative SILENT arm, covering both shapes at once.

        VALID_HANDOFF already carries two present-but-empty fields
        (uncertainty, open_questions), so adding a legitimate extra key gives
        one fixture that is simultaneously the extra-keys shape and the
        present-but-empty shape -- the two the census puts at 7.1% and ~50%
        of real traffic. Firing on either would make the advisory noise.

        Its job here is not to re-test the validator but to prove the call is
        wired and neither site adds a check of its own. `memory_saved` is the
        real secretary field, the single most common legitimate extra key.
        """
        handoff = dict(VALID_HANDOFF, memory_saved=["m1"])
        rules = _rules(payload_for(handoff), tmp_path, monkeypatch, pact_context)
        assert rule not in rules, (
            f"{rule} fired on a complete handoff carrying an extra key and two "
            f"legitimately empty fields. Presence-only validation must be "
            f"silent here; a site adding its own emptiness or extra-key check "
            f"would fire on most real handoffs."
        )
