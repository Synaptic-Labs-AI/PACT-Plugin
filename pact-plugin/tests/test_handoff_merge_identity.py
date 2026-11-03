"""
Home for the harvest-side reconciliation primitives in
shared/agent_handoff_marker.py: the emit-side subject sentinel, the disk
identity, and the task-file-preferred HANDOFF union.

The rule is AUTHORED in skills/pact-handoff-harvest/SKILL.md Step 3; these
tests pin the executable copy so the two cannot drift. The union arms are the
REQUIRED-key-set gate: fixtures A-F from the preparation census, the three
journal-only arms, and the alias-ordering case. The earlier extraction arms
that pinned the superseded rule are deliberately replaced here — commit 4's
whole point is that their expectations changed.

Not covered elsewhere: no existing test reaches the union — it lived only as
pseudocode before this module.
"""
import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

from shared.agent_handoff_marker import (
    OLD_FORMAT_MIXED,
    OLD_FORMAT_OWNED,
    OLD_FORMAT_UNMATCHED,
    OldFormatOutcome,
    disk_identity,
    emit_side_subject,
    mixed_old_format_event,
    occupant_hash,
    old_format_destination,
    report_safe,
    union_preferring_task_file,
)

SKILL_TEXT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "pact-handoff-harvest"
    / "SKILL.md"
).read_text(encoding="utf-8")


class TestEmitSideSubject:
    def test_missing_empty_and_whitespace_get_the_sentinel(self):
        for value in (None, "", "   ", " \t\n"):
            assert emit_side_subject(value) == "(no subject)"

    def test_present_subject_is_returned_unchanged(self):
        """The value is NOT str-coerced or trimmed — occupant_hash does the
        stringifying, exactly as it does at both emit paths."""
        assert emit_side_subject("design X") == "design X"
        assert emit_side_subject("  padded  ") == "  padded  "


class TestDiskIdentity:
    def test_equals_occupant_hash_with_the_substituted_subject(self):
        assert disk_identity("review-backend", None) == occupant_hash(
            "review-backend", "(no subject)"
        )
        assert disk_identity("review-backend", "   ") == occupant_hash(
            "review-backend", "(no subject)"
        )

    def test_present_subject_hashes_as_itself(self):
        assert disk_identity("coder-a", "land the arms") == occupant_hash(
            "coder-a", "land the arms"
        )


class TestReportSafe:
    """The sanitiser itself: every line boundary a consumer may split on,
    not only the ASCII ones. `str.splitlines()` — Python's own, and many log
    consumers — recognises all of these."""

    @pytest.mark.parametrize(
        "sep", ["\n", "\r", "\r\n", "\x85", "\u2028", "\u2029"]
    )
    def test_every_line_boundary_is_stripped(self, sep):
        assert report_safe("before" + sep + "after") == "beforeafter"

    def test_non_line_controls_are_still_stripped(self):
        assert report_safe("a\x00b\x07c\x7fd") == "abcd"

    def test_c1_controls_are_stripped_even_when_not_line_boundaries(self):
        """\x9b is C1 but not a line boundary. An enumerated class widened
        only to NEL would leave it; the category filter does not."""
        assert report_safe("before\x9bafter") == "beforeafter"

    def test_visible_text_survives_and_the_value_is_capped(self):
        assert report_safe("x" * 500, limit=10) == "xxxxxxxxxx"


class TestUnionPreferringTaskFile:
    # ---- fixtures A-F: the required-key-set gate ----------------------
    # Canonical required set: produced, decisions, uncertainty, integration,
    # open_questions. reasoning_chain is recommended, and a legal revision may
    # omit it — that is why the comparison is scoped to the required set.
    JOURNAL = {
        "produced": "p",
        "decisions": ["d"],
        "reasoning_chain": ["r"],
        "uncertainty": ["u"],
        "integration": "i",
        "open_questions": ["q"],
    }

    def test_fixture_a_accidental_partial_restores_the_cleared_field(self):
        """D = {produced, open_questions}, a proper subset of J's five -> the
        present-and-empty field is restored from the journal, and reported."""
        disk = {"produced": "p2", "open_questions": []}
        merged, reports = union_preferring_task_file(
            dict(self.JOURNAL), disk, "61"
        )
        assert merged["open_questions"] == ["q"]
        assert merged["produced"] == "p2"
        assert any(
            "restored from the journal" in line and "open_questions" in line
            for line in reports
        )

    def test_fixture_b_complete_revision_keeps_the_cleared_field(self):
        disk = {
            "produced": "p2",
            "decisions": ["d2"],
            "reasoning_chain": ["r2"],
            "uncertainty": ["u2"],
            "integration": "i2",
            "open_questions": [],
        }
        merged, reports = union_preferring_task_file(
            dict(self.JOURNAL), disk, "61"
        )
        assert merged["open_questions"] == []
        assert any("kept empty" in line and "covers" in line for line in reports)

    def test_fixture_c_journal_extras_do_not_make_a_complete_revision_partial(self):
        """DISCRIMINATING against a FULL-KEY comparison: the journal's extra
        key would make a full-key task file look like a proper subset."""
        journal = {**self.JOURNAL, "notes": "an extra"}
        disk = {
            "produced": "p2",
            "decisions": ["d2"],
            "uncertainty": ["u2"],
            "integration": "i2",
            "open_questions": [],
        }
        merged, reports = union_preferring_task_file(journal, disk, "61")
        assert merged["open_questions"] == []
        assert any("kept empty" in line for line in reports)
        assert merged["notes"] == "an extra"
        assert any(
            "not a required field" in line and "notes" in line
            for line in reports
        )

    def test_fixture_d_revision_omitting_reasoning_chain_keeps_the_field(self):
        """DISCRIMINATING against a FULL-KEY comparison: a legal revision may
        omit the recommended field."""
        journal = dict(self.JOURNAL)
        disk = {
            "produced": "p2",
            "decisions": ["d2"],
            "uncertainty": ["u2"],
            "integration": "i2",
            "open_questions": [],
        }
        merged, reports = union_preferring_task_file(journal, disk, "61")
        assert merged["open_questions"] == []
        assert any("kept empty" in line for line in reports)
        assert merged["reasoning_chain"] == ["r"]
        assert any(
            "not a required field" in line and "reasoning_chain" in line
            for line in reports
        )

    def test_fixture_e_partial_plus_unknown_key_restores(self):
        """DISCRIMINATING against a FULL-KEY comparison: the unknown key makes
        the sets incomparable there, so the restore never fires."""
        disk = {"produced": "p2", "open_questions": [], "weird_key": 1}
        merged, reports = union_preferring_task_file(
            dict(self.JOURNAL), disk, "61"
        )
        assert merged["open_questions"] == ["q"]
        assert merged["weird_key"] == 1
        assert any("restored from the journal" in line for line in reports)

    def test_fixture_f_rename_and_add_keeps_the_field_after_normalization(self):
        """DISCRIMINATING FOR THE ORDERING. Normalization runs BEFORE the set
        math. Reversed, `key_decisions` is not a required key, so D's required
        set is 4 against J's 5 -> proper subset -> restore, and this arm
        fails with open_questions == ["q"]."""
        disk = {
            "produced": "p2",
            "key_decisions": ["d2"],
            "uncertainty": ["u2"],
            "integration": "i2",
            "open_questions": [],
            "notes": "n",
        }
        merged, _ = union_preferring_task_file(dict(self.JOURNAL), disk, "61")
        assert merged["open_questions"] == []
        assert merged["key_decisions"] == ["d2"]
        assert "decisions" not in merged

    # ---- P10: journal-only fields, three arms --------------------------
    def test_p10_required_journal_only_under_a_proper_subset_is_kept(self):
        """THE #61 RESIDUAL'S ARM. This is the same proper-subset branch the
        residual describes: a DELIBERATE complete removal of a required key
        and an accidental partial rewrite both leave D a proper subset, so the
        branch restores the journal value and reports that it did. Key sets
        cannot separate the two, and the rule text says so."""
        journal = {"produced": "p", "decisions": ["d"], "uncertainty": ["u"]}
        disk = {"produced": "p2", "decisions": ["d2"]}
        merged, reports = union_preferring_task_file(journal, disk, "61")
        assert merged["uncertainty"] == ["u"]
        assert any(
            "restored from the journal" in line and "uncertainty" in line
            for line in reports
        )

    def test_p10_required_journal_only_under_incomparable_sets_is_dropped(self):
        journal = {"produced": "p", "decisions": ["d"]}
        disk = {"produced": "p2", "uncertainty": ["u2"]}
        merged, reports = union_preferring_task_file(journal, disk, "61")
        assert "decisions" not in merged
        assert any(
            "undecidable" in line and "decisions" in line for line in reports
        )

    def test_p10_non_required_journal_only_alongside_an_equal_required_set_is_kept(self):
        """The keep-positive is written as a journal-only OPTIONAL field with
        EQUAL required sets. 'A covering set keeps a journal-only field' is
        vacuous: a required journal-only key cannot exist under covering."""
        journal = {"produced": "p", "decisions": ["d"], "reasoning_chain": ["r"]}
        disk = {"produced": "p2", "decisions": ["d2"]}
        merged, reports = union_preferring_task_file(journal, disk, "61")
        assert merged["reasoning_chain"] == ["r"]
        assert any(
            "not a required field" in line and "reasoning_chain" in line
            for line in reports
        )

    def test_p10b_legacy_spelling_is_not_swallowed_by_the_incomparable_branch(self):
        """`key_decisions` normalizes to `decisions`, so the sets are EQUAL,
        not incomparable, and nothing is dropped."""
        journal = {"produced": "p", "key_decisions": ["d-journal"]}
        disk = {"produced": "p2", "decisions": ["d-disk"]}
        merged, reports = union_preferring_task_file(journal, disk, "61")
        assert merged == {"produced": "p2", "decisions": ["d-disk"]}
        assert reports == []

    # ---- retained behaviour pins --------------------------------------
    def test_present_and_non_empty_disk_value_wins(self):
        merged, _ = union_preferring_task_file(
            {"decisions": ["old"]}, {"decisions": ["new"]}, "61"
        )
        assert merged == {"decisions": ["new"]}

    @pytest.mark.parametrize("empty", [None, "", [], {}])
    def test_each_empty_spelling_yields_to_a_covering_task_file(self, empty):
        """Emptiness is EQUALITY MEMBERSHIP. Under a covering (here equal)
        required set the empty value stands."""
        merged, _ = union_preferring_task_file(
            {"decisions": ["journal"]}, {"decisions": empty}, "61"
        )
        assert merged == {"decisions": empty}

    # THE FOUR FALSY ARMS BELOW ARE RETAINED-BEHAVIOUR PINS, NOT GUARDS. TEST
    # measured all four green under BOTH auditor mutants: at EQUAL required key
    # sets the membership and truthiness readings route identically. They are
    # kept because the 0/False-as-content property is what this branch depends
    # on; the two PROPER-SUBSET probes after them are the only regime that
    # separates the readings and are the arms that fail under the mutants.
    def test_zero_is_content_and_wins(self):
        """RETAINED-BEHAVIOUR PIN, NOT A DISCRIMINATOR (both mutants green):
        documents that 0 is content and wins the disk side. The discriminating
        proper-subset probe is `test_p10_proper_subset_disk_zero_is_content`."""
        merged, _ = union_preferring_task_file(
            {"decisions": ["journal"]}, {"decisions": 0}, "61"
        )
        assert merged == {"decisions": 0}

    def test_false_is_content_and_wins(self):
        """RETAINED-BEHAVIOUR PIN, NOT A DISCRIMINATOR (both mutants green):
        documents that False is content, like 0."""
        merged, _ = union_preferring_task_file(
            {"decisions": ["journal"]}, {"decisions": False}, "61"
        )
        assert merged == {"decisions": False}

    def test_empty_string_journal_value_does_not_trigger_the_rule(self):
        """RETAINED-BEHAVIOUR PIN, NOT A DISCRIMINATOR (both mutants green):
        documents the journal-truthiness reading; "" is both falsy and a set
        member, so every variant agrees at equal key sets."""
        merged, _ = union_preferring_task_file(
            {"decisions": ""}, {"decisions": []}, "61"
        )
        assert merged == {"decisions": []}

    def test_falsy_journal_values_let_the_task_file_win(self):
        """RETAINED-BEHAVIOUR PIN, NOT A DISCRIMINATOR (both mutants green):
        documents that a falsy journal value does not itself trigger the
        present-and-empty state. The proper-subset probes below are the
        guards."""
        for falsy in (0, False, [], {}):
            merged, _ = union_preferring_task_file(
                {"decisions": falsy}, {"decisions": []}, "61"
            )
            assert merged == {"decisions": []}

    def test_none_is_empty_under_a_proper_subset(self):
        """KEEP-None RATIONALE, pinned. The empty-value arm above uses EQUAL
        key sets, where removing None from _EMPTY_VALUES changes nothing.
        Here the task file is a PROPER SUBSET, so dropping None makes the
        disk's null win — the erasure the narrowing exists to prevent."""
        journal = {"decisions": ["j"], "uncertainty": ["u"]}
        disk = {"decisions": None}
        merged, reports = union_preferring_task_file(journal, disk, "61")
        assert merged["decisions"] == ["j"]
        assert any(
            "restored from the journal" in line and "decisions" in line
            for line in reports
        )

    def test_p10_proper_subset_disk_zero_is_content(self):
        """DISCRIMINATING (proper subset): D = {decisions} is a proper subset
        of J = {decisions, uncertainty}, so 0 is content on the disk side and
        wins. Mutant `not disk_value` restores ["j"] and reddens this arm."""
        journal = {"decisions": ["j"], "uncertainty": ["u"]}
        disk = {"decisions": 0}
        merged, _ = union_preferring_task_file(journal, disk, "61")
        assert merged["decisions"] == 0

    def test_p10_proper_subset_empty_list_beats_journal_zero(self):
        """DISCRIMINATING (proper subset): the journal's 0 is falsy but NOT a
        member of (None, '', [], {}), so the task file's [] wins. Mutant
        `journal_value not in _EMPTY_VALUES` restores 0 and reddens this arm."""
        journal = {"decisions": 0, "uncertainty": ["u"]}
        disk = {"decisions": []}
        merged, _ = union_preferring_task_file(journal, disk, "61")
        assert merged["decisions"] == []

    def test_present_and_empty_with_incomparable_sets_keeps_the_empty_value(self):
        """THE INCOMPARABLE / PRESENT-AND-EMPTY BRANCH, which had no arm at
        all. J = {produced, decisions, open_questions}, D = {produced,
        uncertainty, open_questions}: neither covers the other, so the empty
        value stands and the state is reported as undecidable. The old rule
        (always keep the journal on present-and-empty) returns ["q"] here."""
        journal = {
            "produced": "p",
            "decisions": ["d"],
            "open_questions": ["q"],
        }
        disk = {
            "produced": "p2",
            "uncertainty": ["u"],
            "open_questions": [],
        }
        merged, reports = union_preferring_task_file(journal, disk, "61")
        assert merged["open_questions"] == []
        assert any(
            "undecidable" in line
            and "open_questions" in line
            and "incomparable" in line
            for line in reports
        )

    def test_inputs_are_not_mutated(self):
        journal = {"decisions": ["old"], "open_questions": ["q"]}
        disk = {"decisions": ["new"], "open_questions": []}
        union_preferring_task_file(journal, disk, "61")
        assert journal == {"decisions": ["old"], "open_questions": ["q"]}
        assert disk == {"decisions": ["new"], "open_questions": []}

    def test_task_id_is_accepted_and_empty_inputs_are_safe(self):
        assert union_preferring_task_file({}, {}, "61") == ({}, [])

    # ---- security: report injection (CWE-117) and duplicate spellings --
    def test_report_task_id_strips_control_characters(self):
        """A newline in the task id must not forge a second report line; the
        reports are read by an agent that may act on them."""
        _, reports = union_preferring_task_file(
            {"decisions": ["d"]}, {"decisions": []}, "61\nFORGED: approved"
        )
        assert reports
        assert all("\n" not in line and "\r" not in line for line in reports)
        assert "61FORGED: approved" in reports[0]

    def test_report_field_name_strips_control_characters(self):
        """Field names come from the task file and the journal, so a key can
        carry a newline too."""
        _, reports = union_preferring_task_file(
            {"produced": "p", "weird\nkey": "x"}, {"produced": "p2"}, "61"
        )
        assert any("weirdkey" in line for line in reports)
        assert all("\n" not in line for line in reports)

    @pytest.mark.parametrize("sep", ["\x85", "\u2028"])
    def test_report_field_name_strips_unicode_line_separators(self, sep):
        """DISCRIMINATING for the ASCII-only class: NEL and LINE SEPARATOR are
        not C0, so the old sanitiser left them and a `splitlines()` renderer
        would see a forged line."""
        _, reports = union_preferring_task_file(
            {"produced": "p", ("weird" + sep + "key"): "x"},
            {"produced": "p2"},
            "61",
        )
        assert any("weirdkey" in line for line in reports)
        assert sep not in "\n".join(reports)

    def test_report_field_name_is_length_capped(self):
        long_key = "k" * 500
        _, reports = union_preferring_task_file(
            {"produced": "p", long_key: "x"}, {"produced": "p2"}, "61"
        )
        assert reports
        assert "k" * 129 not in reports[0]
        assert len(reports[0]) < 400

    def test_duplicate_spellings_leave_no_stale_journal_copy(self):
        """DISCRIMINATING: `merged` starts from the RAW journal mapping while
        `_field_map` collapses both spellings, so dropping only the selected
        spelling would leave the alias in the output."""
        journal = {"decisions": ["old"], "key_decisions": ["old alias"]}
        disk = {"decisions": ["new"]}
        merged, _ = union_preferring_task_file(journal, disk, "61")
        assert merged == {"decisions": ["new"]}

    def test_dual_spelling_disk_copy_keeps_the_alias_value(self):
        """DISCRIMINATING (alignment with resolve_handoff_field): a FALSY
        canonical spelling on the DISK side must fall through to a truthy
        alias. Before the alignment the map selected the empty canonical, so
        the alias value was ignored AND the journal content was dropped."""
        journal = {"produced": "p", "decisions": ["journal"]}
        disk = {
            "produced": "p2",
            "decisions": "",
            "key_decisions": ["disk alias"],
        }
        merged, _ = union_preferring_task_file(journal, disk, "61")
        assert merged["key_decisions"] == ["disk alias"]
        assert "decisions" not in merged

    # ---- input totality: a malformed copy is ABSENT, not a crash ----------
    def test_non_dict_disk_copy_is_treated_as_absent(self):
        """B-F4, RED before the coercion: a `metadata.handoff` holding a
        string reaches the union through the harvest's read loop. Raising
        here makes the whole loop hostage to one bad field; the module's
        convention is that a malformed copy is absent."""
        merged, reports = union_preferring_task_file(
            {"key_decisions": ["x"]}, "not-a-dict", "61"
        )
        assert isinstance(merged, dict)
        assert isinstance(reports, list)
        assert merged["key_decisions"] == ["x"]

    def test_non_dict_journal_copy_is_treated_as_absent(self):
        merged, reports = union_preferring_task_file(
            [], {"produced": "p"}, "61"
        )
        assert isinstance(merged, dict)
        assert isinstance(reports, list)
        assert merged["produced"] == "p"

    def test_drop_removes_every_spelling_of_the_dropped_identity(self):
        journal = {"produced": "p", "decisions": ["d"], "key_decisions": ["d2"]}
        disk = {"produced": "p2", "uncertainty": ["u"]}
        merged, reports = union_preferring_task_file(journal, disk, "61")
        assert "decisions" not in merged
        assert "key_decisions" not in merged
        assert any(
            "undecidable" in line and "decisions" in line for line in reports
        )


class TestPresentAndEmptyControl:
    """P4 control: the field must be GENUINELY present-and-empty and the
    journal must hold content. Under the required-key-set gate the restore
    branch serves the ACCIDENTAL PARTIAL rewrite (the task file is a proper
    subset), so the control is built in that shape — not in the covering
    shape, which correctly keeps the empty value."""

    def test_present_and_empty_with_a_partial_task_file_restores(self):
        journal = {
            "open_questions": ["was the question resolved?"],
            "produced": "x",
            "decisions": ["d"],
        }
        disk = {"open_questions": [], "produced": "x"}
        assert disk["open_questions"] == [] and "open_questions" in disk
        assert "decisions" not in disk  # proper subset: the partial-rewrite shape
        merged, reports = union_preferring_task_file(journal, disk, "61")
        assert merged["open_questions"] == ["was the question resolved?"]
        assert any("restored from the journal" in line for line in reports)


# =============================================================================
# OLD-FORMAT cross-check (C3)
# =============================================================================
class TestMixedOldFormatEvent:
    EVENT_HANDOFF = {
        "produced": "x",
        "decisions": ["old decision"],
        "uncertainty": ["old uncertainty"],
        "integration": "i",
        "open_questions": [],
    }

    def _task(self, owner, subject, handoff=None, task_id="57"):
        return {
            "id": task_id,
            "owner": owner,
            "subject": subject,
            "metadata": {"handoff": handoff or dict(self.EVENT_HANDOFF)},
        }

    def _classify(self, agent, subject, candidates):
        return mixed_old_format_event(
            "57", agent, subject, dict(self.EVENT_HANDOFF), candidates
        )

    def test_content_match_with_a_different_identity_is_mixed(self):
        """DISCRIMINATING: the event's content is another store's, and its
        identity is not that file's. This is the #57 shape."""
        outcome = self._classify(
            "review-backend",
            "NEW subject",
            [{"team": "session-old", "task": self._task("review-backend", "OLD subject")}],
        )
        assert outcome.state == OLD_FORMAT_MIXED
        assert outcome.team == "session-old"
        assert outcome.report == (
            "handoff identity mismatch for task 57: journal content equals "
            "team session-old task 57, whose identity it does not carry"
        )

    def test_content_and_identity_both_matching_is_owned_by_that_store(self):
        outcome = self._classify(
            "review-backend",
            "NEW subject",
            [{"team": "session-old", "task": self._task("review-backend", "NEW subject")}],
        )
        assert outcome.state == OLD_FORMAT_OWNED
        assert outcome.team == "session-old"
        assert outcome.report is None

    def test_content_matching_no_named_store_is_unmatched_and_reports(self):
        """The UNMATCHED state is the COMMON old-format path, and it carries
        the unavailable-check line ON THE OUTCOME so the caller cannot choose
        silently. An outcome without a report here would be the silent-branch
        defect the arc exists to fix."""
        other = {**self.EVENT_HANDOFF, "produced": "something else"}
        outcome = self._classify(
            "review-backend",
            "NEW subject",
            [{"team": "session-old", "task": self._task("someone", "OLD", handoff=other)}],
        )
        assert outcome.state == OLD_FORMAT_UNMATCHED
        assert outcome.team is None
        assert outcome.report == (
            "handoff cross-team check unavailable for task 57: no other "
            "named store carries the identity"
        )

    def test_an_identity_match_wins_over_an_earlier_content_match(self):
        """The two-pass scan: the CONTENT match comes FIRST, so a classifier
        that short-circuits at the first content match returns MIXED here.
        The correct classifier checks every candidate and finds the identity
        match in the second one. (The order was the reverse before: with the
        identity match first, the short-circuit mutant passed every arm.)"""
        foreign = self._task("review-backend", "OLD subject")
        same = self._task("review-backend", "NEW subject")
        outcome = self._classify(
            "review-backend",
            "NEW subject",
            [
                {"team": "session-old", "task": foreign},
                {"team": "session-this", "task": same},
            ],
        )
        assert outcome.state == OLD_FORMAT_OWNED
        assert outcome.team == "session-this"

    def test_subject_sentinel_is_applied_to_the_file_identity(self):
        outcome = self._classify(
            "review-backend",
            "(no subject)",
            [{"team": "session-old", "task": self._task("review-backend", "")}],
        )
        assert outcome.state == OLD_FORMAT_OWNED
        assert outcome.team == "session-old"

    @pytest.mark.parametrize("owner", [None, "   "])
    def test_empty_owner_candidate_is_not_mixed_and_is_kept(self, owner):
        """DISCRIMINATING (F2): the emit substitutes the platform teammate
        name for an empty owner, and the record cannot reproduce it. A content
        match against such a file must NOT be read as a foreign identity —
        before the fix this returned MIXED and the event was discarded."""
        task = {
            "id": "57",
            "subject": "NEW subject",
            "metadata": {"handoff": dict(self.EVENT_HANDOFF)},
        }
        if owner is not None:
            task["owner"] = owner
        outcome = self._classify(
            "review-backend", "NEW subject",
            [{"team": "session-old", "task": task}],
        )
        assert outcome.state == OLD_FORMAT_UNMATCHED
        assert old_format_destination(outcome, "session-this") == "this"

    def test_mixed_report_is_sanitised(self):
        """SITE 2 of report_safe: the classifier's own report interpolates the
        task id and the team. Without the sanitiser a line boundary in either
        forges a report line. This is the report site the one-sided count pin
        did not reach."""
        outcome = mixed_old_format_event(
            "57\nFORGED",
            "review-backend",
            "NEW subject",
            dict(self.EVENT_HANDOFF),
            [
                {
                    "team": "session-old\nFORGED",
                    "task": self._task("review-backend", "OLD subject"),
                }
            ],
        )
        assert outcome.state == OLD_FORMAT_MIXED
        assert "\n" not in outcome.report
        assert "57FORGED" in outcome.report
        assert "session-oldFORGED" in outcome.report

    def test_non_dict_event_handoff_is_unmatched(self):
        outcome = mixed_old_format_event(
            "57",
            "review-backend",
            "NEW subject",
            "not a mapping",
            [{"team": "session-old", "task": self._task("review-backend", "OLD")}],
        )
        assert outcome.state == OLD_FORMAT_UNMATCHED
        assert "cross-team check unavailable" in outcome.report

    def test_no_candidates_is_unmatched(self):
        assert (
            self._classify("review-backend", "NEW subject", []).state
            == OLD_FORMAT_UNMATCHED
        )


class TestOldFormatDestination:
    """The ROUTING arms. The previous routing sent every non-mixed event to
    this team, so `test_a_from_store_owned_event_routes_to_that_team` is the
    arm that fails under it: an old-format HANDOFF owned by a realigned FROM
    store must not be processed under this team."""

    def test_mixed_is_discarded(self):
        outcome = OldFormatOutcome(OLD_FORMAT_MIXED, "session-old", "report me")
        assert old_format_destination(outcome, "session-this") == "mixed"

    def test_owned_by_this_team_stays_here(self):
        outcome = OldFormatOutcome(OLD_FORMAT_OWNED, "session-this")
        assert old_format_destination(outcome, "session-this") == "this"

    def test_owned_by_a_realigned_from_team_routes_to_that_team(self):
        outcome = OldFormatOutcome(OLD_FORMAT_OWNED, "session-old")
        assert old_format_destination(outcome, "session-this") == "from"

    def test_unmatched_stays_here(self):
        outcome = OldFormatOutcome(OLD_FORMAT_UNMATCHED)
        assert old_format_destination(outcome, "session-this") == "this"

    def test_end_to_end_owned_by_from_store_routes_there(self):
        """The lead's arm: classify an event whose content AND identity both
        match a from store, then route it. It lands under that team's
        section, never in this team's merged output."""
        handoff = {"produced": "old team work", "decisions": ["d"]}
        task = {
            "id": "57",
            "owner": "coder-a",
            "subject": "old team subject",
            "metadata": {"handoff": dict(handoff)},
        }
        outcome = mixed_old_format_event(
            "57", "coder-a", "old team subject", handoff,
            [{"team": "session-old", "task": task}],
        )
        assert outcome.state == OLD_FORMAT_OWNED
        assert old_format_destination(outcome, "session-this") == "from"
        assert outcome.team == "session-old"


# =============================================================================
# session_resume renderer — the coupling (P12)
# =============================================================================
class TestResumeGroupingKey:
    def _event(self, task_id, team, decision, ts):
        return {
            "type": "agent_handoff",
            "task_id": task_id,
            "team": team,
            "agent": "coder",
            "task_subject": "same subject",
            "ts": ts,
            "handoff": {"decisions": [decision]},
        }

    def _render(self, events):
        with patch(
            "shared.session_resume.read_events_from", return_value=events
        ):
            from shared.session_resume import _build_journal_resume_inner

            return _build_journal_resume_inner("/tmp/not-a-real-session")

    def test_two_teams_same_numbered_task_render_as_two_entries(self):
        """DISCRIMINATING: with the old (agent, subject) key the later team
        overwrites the earlier and only one entry renders."""
        rendered = self._render(
            [
                self._event("1", "session-a", "team a work", "2026-01-01T00:00:01Z"),
                self._event("1", "session-b", "team b work", "2026-01-01T00:00:02Z"),
            ]
        )
        assert "team a work" in rendered
        assert "team b work" in rendered
        assert rendered.count("- coder: same subject") == 2

    def test_two_task_ids_sharing_an_occupant_render_as_two_entries(self):
        rendered = self._render(
            [
                self._event("1", "session-a", "task one", "2026-01-01T00:00:01Z"),
                self._event("2", "session-a", "task two", "2026-01-01T00:00:02Z"),
            ]
        )
        assert "task one" in rendered and "task two" in rendered
        assert rendered.count("- coder: same subject") == 2


# =============================================================================
# Prose routing pins (P1-P6, P6-absence side)
#
# The partition/old-format routing needs the journal read, the context team
# name and multi-store file reads, so it is not a pure predicate (architecture
# §5 observability). These bounded-section presence pins are its regression
# protection: a filter-shaped rewrite deletes the clauses they assert.
# =============================================================================
class TestHarvestRuleProse:
    """PROSE PINS. These protect the INSTRUCTION SURFACE, not behaviour: a
    code mutant cannot redden them (test-only changes), and a text deletion
    reddens them on their own assertion.

    A REWORD MEANS UPDATING THE PIN, NEVER DELETING IT. A pin that is deleted
    because it reddened on a harmless reword leaves the rule unpinned, which
    is the state these pins exist to prevent. Tighten or loosen the anchor and
    keep the assertion.
    """

    def test_partition_clause_present(self):
        assert (
            "THIS IS A PARTITION: A PARTITION IS NEVER DISCARDED FOR NAMING "
            "ANOTHER TEAM" in SKILL_TEXT
        )
        # M-2: the FOREIGN clause. Removing "do not union it" flips the
        # instruction to allow merging a foreign event.
        assert "do not process it, do not union it" in SKILL_TEXT

    def test_step3_selection_text_carries_no_filter_wording(self):
        """A negated rule ('not a filter') is re-derived into the positive one
        by the next reader. The rule text states the partition positively and
        the word does not appear in either selection block at all."""
        selection = SKILL_TEXT.split("**SELECTION, because")[1].split(
            "### Step 3.1"
        )[0]
        snapshot_step = SKILL_TEXT.split("### Step 3.1:")[1].split(
            "### Step 3.5"
        )[0]
        assert "filter" not in selection.lower()
        assert "filter" not in snapshot_step.lower()

    def test_from_team_route_present(self):
        """P6: a filter-shaped fix fails here — the realigned earlier team is
        routed to its own section, not dropped."""
        assert (
            "Each partition naming a team the journal realigned FROM"
            in SKILL_TEXT
        )
        assert "synthesize it under that team's `team={from}` label" in SKILL_TEXT
        # M-2: the P6 CORE guarantee. Flipping "never union it" to "union it"
        # would merge a realigned team's copy into this team's task file.
        assert "never union it with this team's task file" in SKILL_TEXT
        assert "UNION IT WITH THAT TEAM'S TASK FILE" in SKILL_TEXT

    def test_from_team_partition_unions_with_its_own_store(self):
        """Bugbot's High finding: the from-team loop must read THAT team's
        store and union that copy. The journal alone drops task-file-only
        content, and reading THIS team's store produced a false 'task file
        absent' report for a task that lives in the from store."""
        block = SKILL_TEXT.split(
            "for team in sorted(from_stores):", 1
        )[1].split("    # STEP 2", 1)[0]
        assert "read_task_json(task_id, team)" in block
        assert "union_preferring_task_file(" in block
        assert 'section=f"team={team}"' in block
        # The main section still reads THIS team's store, and the false gap
        # for an all-from-team task is guarded.
        assert "read_task_json(task_id, this_team)" in SKILL_TEXT
        assert "if not from_handled_any:" in SKILL_TEXT

    def test_old_format_cross_check_present(self):
        assert "AN OLD-FORMAT EVENT CARRIES NO `team` FIELD" in SKILL_TEXT
        assert "handoff cross-team check unavailable for task N" in SKILL_TEXT
        assert "DO NOT ENUMERATE THE TASKS ROOT" in SKILL_TEXT
        # M-2: flipping "discard" to "keep" would process a mixed event.
        assert "discard a mixed event" in SKILL_TEXT

    def test_partition_route_is_called_not_inlined(self):
        """A-F6: the NEW-FORMAT partition lives in `partition_destination`
        beside `old_format_destination`, and the skill branches on the
        exported labels. The inline comparison was the unexecutable half that
        produced a prose/pseudocode disagreement."""
        assert (
            "DECIDE EACH ROUTE WITH THE SHARED `partition_destination`"
            in SKILL_TEXT
        )
        assert "partition_destination(" in SKILL_TEXT
        for label in (
            "PARTITION_OLD_FORMAT",
            "PARTITION_THIS",
            "PARTITION_FROM",
            "PARTITION_FOREIGN",
        ):
            assert label in SKILL_TEXT
        # The inline comparison must not come back. (`for team in from_teams`
        # in the membership loop is a different construct and stays.)
        assert "elif team == this_team:" not in SKILL_TEXT
        assert "elif team in from_teams:" not in SKILL_TEXT

    def test_old_format_routing_uses_the_structured_outcome(self):
        """The pseudocode must ROUTE on the outcome, not collapse every
        non-mixed classification into this team. A from-store-owned event is
        appended to that team's partition, never to this team's."""
        assert "old_format_destination(outcome, this_team)" in SKILL_TEXT
        assert "from_events.setdefault(outcome.team, []).append(e)" in SKILL_TEXT
        assert 'if destination == OLD_FORMAT_DEST_MIXED:' in SKILL_TEXT
        assert 'elif destination == OLD_FORMAT_DEST_FROM:' in SKILL_TEXT

    def test_old_format_section_reports_branch_locally(self):
        """BRANCH-LOCAL: one report in the MIXED block, ZERO in the FROM
        block, one in the ELSE (UNMATCHED) block. A whole-file count of two
        would pass a version that reported twice in MIXED and never in the
        UNMATCHED branch, which is the silent-choice defect on the common
        old-format path."""
        after_from_label = SKILL_TEXT.split('elif destination == OLD_FORMAT_DEST_FROM:', 1)[1]
        mixed_block = SKILL_TEXT.split('if destination == OLD_FORMAT_DEST_MIXED:', 1)[1].split(
            'elif destination == OLD_FORMAT_DEST_FROM:', 1
        )[0]
        from_block = after_from_label.split("\n            else:", 1)[0]
        else_block = after_from_label.split("\n            else:", 1)[1].split(
            "    # STEP 2", 1
        )[0]
        assert mixed_block.count("report(outcome.report)") == 1
        assert from_block.count("report(outcome.report)") == 0
        assert else_block.count("report(outcome.report)") == 1
        assert "task_id, e.get(\"agent\", \"\")" in SKILL_TEXT

    def test_skill_calls_the_shared_identity_helpers(self):
        """The skill must NOT carry a local `def emit_side_subject` and must
        CALL `disk_identity` for the record side, so the identity rule has
        one executable home."""
        assert "def emit_side_subject" not in SKILL_TEXT
        assert "disk_identity(task.get(" in SKILL_TEXT
        assert "emit_side_subject" in SKILL_TEXT  # the call-route entry

    def test_divergence_report_is_decided_after_the_group_loop(self):
        """The divergence reports compare ONE task file with the WHOLE group
        set, so both partitions decide them AFTER their loop and emit each
        ONCE. Inside the loop they would fire once per work unit, and for a
        multi-arc task_id the removed per-group 'not the task file's group'
        line was false: the other group owns the file and this one has no
        copy of its own to disagree with. A DRAINED from store reports in the
        from section too, so the two partitions speak in the same two states
        and are quiet in the same one."""
        # The former per-group third message is gone, prose and pseudocode.
        assert "is not the task file's group" not in SKILL_TEXT
        assert "processed from the journal copy alone" not in SKILL_TEXT
        assert "disk_group_matches = disk_id in groups" in SKILL_TEXT

        # FROM partition: one group set per from store, one report after it,
        # and BOTH states report — a drained from store is a reportable gap
        # exactly as it is in the this-team partition.
        from_block = SKILL_TEXT.split(
            "for team in sorted(from_stores):", 1
        )[1].split("    # STEP 2", 1)[0]
        assert "from_groups = group_by_identity(events)" in from_block
        assert "if not from_task:" in from_block
        assert "elif from_disk_id not in from_groups:" in from_block
        assert from_block.count('f"group carries its identity")') == 1
        assert from_block.count(': task file absent")') == 1
        from_loop = from_block.split(
            "for identity, group in from_groups.items():", 1
        )[1].split('process(handoff, section=f"team={team}")', 1)[0]
        assert "carries its identity" not in from_loop
        assert "task file absent" not in from_loop
        assert from_block.index("if not from_task:") > from_block.index(
            'process(handoff, section=f"team={team}")'
        )

        # THIS partition: neither divergence message may sit inside the loop.
        tail = SKILL_TEXT.split(
            "disk_group_matches = disk_id in groups", 1
        )[1].split("Read all HANDOFFs", 1)[0]
        assert tail.count('f"group carries its identity")') == 1
        this_loop = tail.split(
            "for identity, events in groups.items():", 1
        )[1].split("        process(handoff)", 1)[0]
        assert "carries its identity" not in this_loop
        assert "task file absent" not in this_loop
        assert tail.index("        process(handoff)\n") < tail.index(
            "elif not disk_group_matches:"
        )

    def test_divergence_report_rule_prose_states_the_once_rule(self):
        """The rule text must carry the once-post-loop instruction and the
        from-team parity, so a reader cannot re-derive a per-group report or
        a total-mismatch message for a group that lost to another match."""
        assert (
            "decide its report AFTER the group loop and emit it ONCE per task"
            in SKILL_TEXT
        )
        assert "A from-team section applies the SAME rule" in SKILL_TEXT
        assert "both the absent-file and the no-matching-group report" in SKILL_TEXT
        assert "with the SAME wording" in SKILL_TEXT
        assert "report NOTHING" in SKILL_TEXT

    def test_merge_report_lines_land_in_the_memory_entry(self):
        """F4, architect option (c): a merge report records a DECISION, and
        the journal preserves only the raw content, so the entry's `context`
        is the decision's only durable home. Dropping a report line in the
        synthesis is a DEFECT — it reproduces the gap one layer up — not a
        discretionary omission of colour. The rule must name BOTH report
        families, or a reader can exempt the union lines or the partitions.

        THE ANCHORS ARE VOCABULARY-LEVEL ON PURPOSE. Each survives a
        grammatical reword (EVERY ONE->ALL, SYNTHESIS->SUMMARY, a dropped
        "both" or "Step 3"), so a legitimate reword does not tempt the next
        editor to delete the pin. A swap of the rule's OWN TERMS
        ("reports"->"outputs", "lands"->"is written") is SUPPOSED to red:
        that changes what the rule says and buys a pin update. Do not loosen
        these to absorb it."""
        assert "MEMORY ENTRY'S `context` MUST CARRY" in SKILL_TEXT
        assert "DROPS A REPORT LINE IS A DEFECT" in SKILL_TEXT
        assert "partitions' reports and the union's per-field lines" in SKILL_TEXT
        assert "report line for that unit lands in `context`" in SKILL_TEXT

    def test_report_lines_use_the_shared_sanitiser(self):
        """F1: the skill's hand-written report sites must interpolate through
        `report_safe`. The helper-produced strings are already sanitised; the
        asymmetry this pin closes is the four sites the skill composes
        itself."""
        assert "report_safe" in SKILL_TEXT
        assert SKILL_TEXT.count("report_safe(") >= 4

    def test_empty_membership_rule_is_stated(self):
        """The rule text must define EMPTY as membership, not falsiness: 0 and
        False being content is the property the whole branch depends on, and
        prose is its only carrier."""
        assert "`EMPTY` MEANS MEMBERSHIP IN" in SKILL_TEXT

    def test_output_spelling_rule_is_stated(self):
        """The rule text must say that no journal spelling of a task-file-won
        field survives; the code drops every alias spelling and the arms
        assert it, but without this sentence a reader cannot know."""
        assert "NO JOURNAL SPELLING" in SKILL_TEXT

    def test_old_format_candidate_ordering_is_stated(self):
        """The rule text must say an identity match in ANY candidate beats an
        earlier content-only match; otherwise a reader can short-circuit at
        the first mixed result."""
        assert "CHECK EVERY NAMED CANDIDATE" in SKILL_TEXT

    def test_old_format_candidates_are_read_only_when_needed(self):
        """Architecture performance note: new-format events take no extra
        read. An unguarded candidate build reads the stores for every task,
        then reads this team's file AGAIN for the union."""
        assert "if old_format:" in SKILL_TEXT

    def test_unreadable_task_list_branch_is_implemented(self):
        """FINDING 3: the prose and the guard name the SAME state — an absent
        `Task list:` or an underivable last path component (empty this_team).
        That makes EVERY partition OLD-FORMAT and fires the underivable
        report; without the guard, team-carrying events go to the FOREIGN
        report instead, the opposite instruction."""
        assert "if not this_team:" in SKILL_TEXT
        guard = SKILL_TEXT.split("if not this_team:", 1)[1].split(
            "\n    else:", 1
        )[0]
        assert "handoff team identity underivable for task" in guard
        assert "list(matches)" in guard
        assert "task_list_dir else" in SKILL_TEXT

    def test_snapshot_section_join_present(self):
        assert "handoff snapshot identity mismatch for task N" in SKILL_TEXT
        # M-2: the DROP is the rule; "is reported" without "DROPPED" would
        # let a foreign snapshot's metadata be used.
        assert "names another team is DROPPED and reported" in SKILL_TEXT

    def test_occupant_hash_is_not_folded_with_team(self):
        assert "DO NOT FOLD THE TEAM INTO `occupant_hash`" in SKILL_TEXT

    def test_resume_coupling_sentence_present(self):
        assert "(task_id, team, agent, task_subject)" in SKILL_TEXT

    def test_data_boundary_sentence_present(self):
        assert "THE AGENT AND THE SUBJECT MUST CROSS AS DATA" in SKILL_TEXT

    # ---- PROSE-REVIEW FIXES. One pin per finding: a prose fix with no pin
    # is how the earlier divergences survived a green suite. ----

    def test_b1_missing_required_field_is_not_a_proper_subset(self):
        """B1: 'the task file lacks a required field' does NOT imply D ⊂ J.
        The old sentence routed that state to the restore branch and told the
        reader to revive the cleared field — the defect this branch exists to
        prevent. The measured counterexample must stay in the text."""
        assert "A MISSING REQUIRED FIELD DOES NOT IMPLY A PROPER SUBSET" in SKILL_TEXT
        assert (
            "against disk `{produced, uncertainty, open_questions}`"
            in SKILL_TEXT
        )
        assert (
            "If the task file lacks any REQUIRED field the journal copy "
            "carries, `D` is a proper subset of `J`"
            not in SKILL_TEXT
        )

    def test_b2_journal_side_is_truthiness_not_membership(self):
        """B2: the old sentence gave two answers for a falsy journal value.
        The code gates State A on journal TRUTHINESS and on DISK membership;
        the text must state the two tests separately."""
        assert "THE JOURNAL SIDE IS DECIDED BY TRUTHINESS, NOT MEMBERSHIP" in SKILL_TEXT
        assert "AND THAT TEST APPLIES TO THE TASK-FILE SIDE" in SKILL_TEXT
        assert (
            "and a journal value of `0`, `False`, `[]` or `{}` does not "
            "trigger the present-and-empty state"
            not in SKILL_TEXT
        )

    def test_b3_alias_map_is_the_normaliser_not_resolve_handoff_field(self):
        """B3: `resolve_handoff_field` returns None for absent AND for
        present-None, so using it to normalise keys collapses the very
        distinction State A/B rests on. The text must name the alias map."""
        assert "`resolve_handoff_field` IS NOT A KEY NORMALISER" in SKILL_TEXT
        assert "HANDOFF_LEGACY_ALIASES` map" in SKILL_TEXT
        assert (
            "to their canonical names with the shared `resolve_handoff_field`"
            not in SKILL_TEXT
        )

    def test_b4_ownerless_candidate_is_skipped_not_mixed(self):
        """B4: a content-matching candidate whose record has no owner is
        SKIPPED, leaving the event UNMATCHED. Read literally as an identity
        mismatch, the code would discard the realigned team's own HANDOFF."""
        assert (
            "A CONTENT-MATCHING CANDIDATE WHOSE OWN RECORD CARRIES NO `owner` "
            "IS SKIPPED" in SKILL_TEXT
        )
        assert "143 of 1421 task records carry no `owner`" in SKILL_TEXT

    def test_sixth_no_stale_per_group_report_pointer(self):
        """The orphan: the selection rule still pointed at a per-group report
        message that the once-post-loop rule deleted. It must now say the
        other group gets NO report."""
        assert (
            "report each other group through the report-message rule below"
            not in SKILL_TEXT
        )
        assert "gets NO per-group report" in SKILL_TEXT

    def test_minor_from_team_has_no_nested_ledger_pass(self):
        """Architect ruling (c): a from-team work unit is PROCESSED AND
        SYNTHESIZED but NOT entered in this team's ledger. Both wrong answers
        are stated with their reasons — writing the from team's section
        contradicts Step 8's section semantics, and recording the id here
        poisons this team's dedup set because the same id names different
        tasks in different stores. The repeat guard is Step 6's save-vs-update
        dedup, so a later pass updates rather than duplicates."""
        assert "DO NOT RUN STEP 2 OR STEP 8 FOR THEM" in SKILL_TEXT
        assert (
            "name team `{from}` as the source in the entry's context"
            in SKILL_TEXT
        )
        assert (
            "The repeat guard for from-team work units is Step 6's "
            "save-vs-update dedup" in SKILL_TEXT
        )
        assert "would make a later pass skip your OWN task" in SKILL_TEXT
        assert "Step 8 forbids touching another team's section" in SKILL_TEXT
        assert (
            "run Step 2 and Step 8 for them against a `## team={from}` section"
            not in SKILL_TEXT
        )
        assert (
            "run Step 2 and Step 8 for it against its own `## team={from}` "
            "section" not in SKILL_TEXT
        )

    def test_minor_process_is_defined(self):
        """The pseudocode called `process` without ever saying what it does;
        a reader executing the pseudocode had to invent the ingestion step."""
        assert (
            "`process(handoff, metadata=None, section=None)` = run Steps 4-7"
            in SKILL_TEXT
        )

    def test_minor_task_file_only_branch_has_a_prose_rule(self):
        """A task with no journal event and a present task file is a normal
        population, not a gap; the pseudocode had the branch, the prose did
        not."""
        assert (
            "When the task carries NO `agent_handoff` event in a SECTION and "
            "that SECTION's task file EXISTS" in SKILL_TEXT
        )

    def test_from_team_disk_only_task_is_processed(self):
        """The from-team half has rule 5's disk-only path: the file is
        processed under `## team={from}`, and the store produces NEITHER the
        absent-file NOR the no-match report. GREEN on b9cd8157 — this is the
        regression guard for the path itself."""
        assert "THIS IS ONE RULE WITH TWO APPLICATIONS, NOT TWO RULES" in SKILL_TEXT
        assert (
            "it produces NEITHER the absent-file report NOR the no-match report"
            in SKILL_TEXT
        )
        # The store set is built from BOTH shapes, not from journal events
        # alone.
        assert "from_stores = set(from_events)" in SKILL_TEXT
        assert "from_stores.add(team)" in SKILL_TEXT
        assert SKILL_TEXT.count("from_stores.add(team)") == 1
        disk_only = SKILL_TEXT.split("if not from_groups:", 1)[1].split(
            "elif not from_task:", 1
        )[0]
        assert "read_task_json" not in disk_only
        block = SKILL_TEXT.split("from_stores = set(from_events)", 1)[1].split(
            "    if task_id not in unprocessed:", 1
        )[0]
        assert 'metadata=from_task.get("metadata", {})' in block
        # The disk-only branch precedes both report branches, so a file with
        # no group cannot fall through to the absent or no-match message.
        assert block.index(
            'metadata=from_task.get("metadata", {})'
        ) < block.index("handoff divergence check unavailable for task")

    def test_high_from_team_membership_is_census_provenance(self):
        """Bugbot 2, RED on the content-gated membership: the disk shape is
        admitted by PROVENANCE, not by a content test. The from census admits
        a task by ANY agent-authored key (an auditor carries `audit_summary`
        and no `handoff`), so a sibling-key-only file must enter and Step 3.1
        resolve it. An id that is ONLY this team's must never admit a
        same-numbered stranger's file."""
        prelude = SKILL_TEXT.split("from_stores = set(from_events)", 1)[1].split(
            "for team in sorted(from_stores):", 1
        )[0]
        assert "if task_id in from_unprocessed:" in prelude
        assert "if read_task_json(task_id, team):" in prelude
        assert "candidate_handoff" not in SKILL_TEXT
        # A content gate here re-narrows the census population: a task whose
        # only content is a sibling key would be dropped.
        assert 'get("handoff")' not in prelude
        assert prelude.index("if task_id in from_unprocessed:") < prelude.index(
            "from_stores.add(team)"
        )

    def test_high_disk_only_from_branch_never_sets_handled_any(self):
        """Bugbot 1, RED on b9cd8157: `from_handled_any`'s only consumer is
        THIS team's report-gap line, which runs only for a ledger-uncovered
        this-team id. A from-store file is not evidence about this team's
        task, so the disk-only branch must not set the flag — otherwise a
        drained this-team discovery vanishes with no gap report."""
        disk_only = SKILL_TEXT.split("if not from_groups:", 1)[1].split(
            "elif not from_task:", 1
        )[0]
        assert 'metadata=from_task.get("metadata", {})' in disk_only
        assert "from_handled_any = True" not in disk_only
        # The gap line the flag would suppress is still reachable.
        assert 'report_gap(task_id, "unknown", gap_ts)' in SKILL_TEXT

    def test_from_team_journal_partition_still_sets_handled_any(self):
        """REGRESSION GUARD, GREEN on b9cd8157: a journal event's `team`
        field is an explicit store claim, so the journal-partition branch
        keeps setting the flag; the all-from-team-work-units carve-out
        depends on it."""
        loop_body = SKILL_TEXT.split(
            "for identity, group in from_groups.items():", 1
        )[1].split("if not from_groups:", 1)[0]
        assert "from_handled_any = True" in loop_body
        assert "if not from_handled_any:" in SKILL_TEXT

    def test_minor_from_handled_any_carve_out_is_prose(self):
        """The all-from-team task has no file in THIS store and must not
        emit the report-gap line; this was carried only by a pseudocode
        comment."""
        assert (
            "HAS NO TASK FILE IN THIS SECTION'S STORE, AND THE ABSENCE IS NOT "
            "A GAP" in SKILL_TEXT
        )

    def test_minor_report_gap_records_the_agent_when_known(self):
        """The prose required an agent name the call site cannot supply. The
        rule now says when it is knowable and what to record otherwise, and
        the call carries the field."""
        assert (
            "record the agent as `unknown` rather than omitting the field"
            in SKILL_TEXT
        )
        assert 'report_gap(task_id, "unknown", gap_ts)' in SKILL_TEXT

    def test_minor_dual_spelling_value_selection_is_stated(self):
        """The disk value of a dual-spelled field is chosen by the same rule
        as `resolve_handoff_field`: truthy canonical, else first truthy
        alias. Without it a task file carrying `decisions: ''` beside
        `key_decisions: [...]` reads as empty."""
        assert (
            "VALUE SELECTION FOR A DUAL-SPELLED COPY IS A SEPARATE STEP"
            in SKILL_TEXT
        )
        assert "falls through to the FIRST TRUTHY alias spelling" in SKILL_TEXT

    def test_inventory_routes_every_named_helper(self):
        """M5: the inventory must COVER the helpers the step and pseudocode
        name, not claim coverage. This parses the inventory's REAL import
        lines — a pin asserting a sentence about coverage passes while the
        sentence is wrong, which is exactly how four unrouted helpers
        survived the previous pass."""
        section = SKILL_TEXT.split(
            "**HOW TO CALL THE NAMES THIS STEP USES.**", 1
        )[1].split("🔴 **THIS RULE IS NOT MIRRORED", 1)[0]
        import_lines = [
            line for line in section.splitlines() if " import " in line
        ]
        assert import_lines, "the inventory has no import lines"
        for name in (
            "occupant_hash",
            "report_safe",
            "emit_side_subject",
            "disk_identity",
            "handoff_content_key",
            "union_preferring_task_file",
            "partition_destination",
            "old_format_destination",
            "mixed_old_format_event",
            "HANDOFF_LEGACY_ALIASES",
        ):
            routed = [line for line in import_lines if name in line]
            assert routed, f"{name} has no import line in the inventory"
            assert any("python3 -c" in line for line in routed), (
                f"{name} has no RUNNABLE route"
            )
        # `latest_by_ts_then_journal_order` is DEFINED here, so it needs no
        # import route; the definition itself is the route.
        assert "def latest_by_ts_then_journal_order(events):" in section
        # The two label sets the routers return are documented with them.
        for label in (
            "PARTITION_THIS",
            "PARTITION_FROM",
            "PARTITION_FOREIGN",
            "PARTITION_OLD_FORMAT",
            "OLD_FORMAT_DEST_MIXED",
            "OLD_FORMAT_DEST_FROM",
            "OLD_FORMAT_DEST_THIS",
        ):
            assert label in section

    def test_high_from_team_population_bypasses_the_ledger_filter(self):
        """HIGH (commit 24): the from-team population must not pass through
        THIS team's ledger filter. Discovery folds both populations into one
        list today, so a from-store task whose NUMBER this team already
        recorded never reaches the from-team partition and that HANDOFF is
        silently dropped. The split is at DISCOVERY, before any ledger
        comparison, and the pseudocode carries it as a separate set."""
        assert (
            "ROUTE THESE IDS TO THE FROM-TEAM PARTITION AT DISCOVERY, BEFORE "
            "ANY LEDGER COMPARISON" in SKILL_TEXT
        )
        assert "THE FILTER APPLIES TO THIS TEAM'S IDS ONLY" in SKILL_TEXT
        assert (
            "the from-team population is routed at Standard Harvest Step 1 "
            "source 4 and is NEVER subtracted by this ledger" in SKILL_TEXT
        )
        assert "for task_id in unprocessed | from_unprocessed:" in SKILL_TEXT
        assert "if task_id not in unprocessed:" in SKILL_TEXT
        # The pre-fix single-population loop must not come back.
        assert "for task_id in unprocessed:\n" not in SKILL_TEXT

    def test_high_ledger_never_records_a_from_team_id(self):
        """HIGH (commit 24), second half: the ledger write must exclude
        from-team ids. Recording one under this team's section recreates the
        same skip on the NEXT pass, so the discovery split alone is not
        enough. The rule is stated at Step 8 (the writer) and inherited by
        the incremental writer."""
        assert "WRITE THIS TEAM'S IDS ONLY — NEVER A FROM-TEAM ID" in SKILL_TEXT
        assert (
            "recording it here would make a later pass skip your OWN task of "
            "that number" in SKILL_TEXT
        )
        assert "THIS team's ids only — never a from-team id" in SKILL_TEXT

    # ---- second prose pass: blocking items, each RED on 86f8dd86 ----
    def test_blocking_b1_how_to_call_routes_the_three_helpers(self):
        """B1: a mandated call with no route. The step and pseudocode name
        `partition_destination`, `old_format_destination`,
        `mixed_old_format_event` and `HANDOFF_LEGACY_ALIASES`; every one must
        have a runnable or precisely-described entry in HOW TO CALL."""
        section = SKILL_TEXT.split(
            "**HOW TO CALL THE NAMES THIS STEP USES.**", 1
        )[1].split("🔴 **THIS RULE IS NOT MIRRORED", 1)[0]
        assert "partition_destination(event_team, this_team, from_teams)" in section
        assert (
            "from shared.agent_handoff_marker import partition_destination"
            in section
        )
        assert "PARTITION_THIS" in section and "PARTITION_OLD_FORMAT" in section
        assert "old_format_destination(outcome, this_team)" in section
        assert "from shared.agent_handoff_marker import OldFormatOutcome" in section
        assert "OLD_FORMAT_DEST_MIXED" in section
        assert (
            "mixed_old_format_event(task_id, event_agent, event_subject, "
            "event_handoff, candidate_tasks)" in section
        )
        assert (
            "from shared.agent_handoff_marker import mixed_old_format_event"
            in section
        )
        assert "HANDOFF_LEGACY_ALIASES" in section
        assert "It is a DICT, not a call" in section

    def test_blocking_b2_union_identity_names_disk_identity(self):
        """B2: the union identity sentence named `occupant_hash`, which skips
        the `(no subject)` substitution; a subject-less task's identity then
        never matches a group and the union never runs."""
        sentence = SKILL_TEXT.split(
            "4. The task file belongs to at most one group.", 1
        )[1].split("**A group that does not carry", 1)[0]
        assert "disk_identity(owner, subject)" in sentence
        # The OLD call form is the defect; the sentence may still warn
        # against recomposing the hash by name.
        assert "occupant_hash(owner, subject)" not in sentence
        assert "never a recomposed `occupant_hash`" in sentence
        assert "SUBSTITUTED" in sentence

    def test_blocking_b3_from_team_units_are_saved(self):
        """B3: "Steps 4-6" excludes Step 7 (Save to pact-memory), so every
        from-team work unit is extracted, deduped and thrown away."""
        assert "Steps 4-6" not in SKILL_TEXT
        assert "Synthesize each of their work units under Steps 4-7" in SKILL_TEXT
        assert "Steps 4-7" in SKILL_TEXT

    def test_blocking_b4_report_gap_is_defined_and_stamped(self):
        """B4: `report_gap` was called but never defined, and the call
        omitted the timestamp the prose requires."""
        assert "def report_gap(task_id, agent, ts):" in SKILL_TEXT
        assert "report_safe(agent)" in SKILL_TEXT
        assert "report_safe(ts)" in SKILL_TEXT
        assert "gap_ts = datetime.now(timezone.utc).strftime(" in SKILL_TEXT
        assert 'report_gap(task_id, "unknown", gap_ts)' in SKILL_TEXT
        assert SKILL_TEXT.index("def report_gap(") < SKILL_TEXT.index(
            'report_gap(task_id, "unknown", gap_ts)'
        )

    def test_minor_rule5_carries_its_own_provenance_gate(self):
        """Minor 5: rule 5 alone must state that the from-team application
        admits only census ids, and admit them AGGREGATE by id (the census is
        id-keyed), so a reader does not add a per-file content test."""
        assert "ADMISSION IS AGGREGATE BY ID" in SKILL_TEXT
        assert "the census is id-keyed" in SKILL_TEXT

    def test_minor_dual_spelling_sentence_is_qualified(self):
        """Minor 6: 'the selected raw key is the one the output carries' is
        false when the journal copy wins a dual-spelled field."""
        assert (
            "The selected raw key is the one the output carries WHEN THE TASK "
            "FILE WINS" in SKILL_TEXT
        )
        assert "BOTH spellings survive" in SKILL_TEXT

    def test_minor_three_report_conditions_are_exclusive(self):
        """Minor 7: absent-file, no-match and gap must be exclusive in one
        place, not fallbacks for one another."""
        assert "THE THREE CONDITIONS ARE EXCLUSIVE" in SKILL_TEXT
        assert "The GAP line requires NO source at all" in SKILL_TEXT

    def test_minor_disk_only_paths_pass_metadata_to_process(self):
        """Minor 8: rule 5 promises Step 3.1 resolves sibling keys, so the
        disk-only branches must pass the section's task metadata rather than
        only `metadata.handoff`."""
        assert 'metadata=from_task.get("metadata", {})' in SKILL_TEXT
        assert 'process(disk_handoff, metadata=task.get("metadata", {}))' in SKILL_TEXT

    # ---- amendment items ----
    def test_minor_partition_chain_fails_closed_on_an_unknown_label(self):
        """M4: the partition chain needs a final `else` routing an unknown
        label to FOREIGN — reported, never processed. The OLD-FORMAT chain
        defaults the other way on purpose (its UNMATCHED case is this team's
        by identity), so the two default postures must be stated."""
        assert (
            "elif destination == PARTITION_FOREIGN:\n"
            "                foreign.append(e)\n"
            "            else:\n"
            "                # An UNKNOWN label fails CLOSED"
            in SKILL_TEXT
        )
        # The sentence wraps across comment lines, so the fragments are
        # asserted separately rather than as one contiguous phrase.
        assert "The OLD-FORMAT chain defaults" in SKILL_TEXT
        assert "the other way on purpose" in SKILL_TEXT
        # FOREIGN is the list the report loop reads, so an unknown label is
        # reported, not processed.
        assert "for e in foreign:" in SKILL_TEXT

    def test_m1_union_annotates_its_inputs_as_object(self):
        """M1: the annotations said `dict` while the function accepts — and
        the docstring now promises — non-dict input. The annotation is the
        contract a reader sees."""
        params = inspect.signature(union_preferring_task_file).parameters
        assert params["journal_handoff"].annotation == "object"
        assert params["disk_handoff"].annotation == "object"

    def test_m2_module_header_names_both_routers(self):
        """M2: the module summary described one router while the module now
        carries two."""
        header = (
            Path(__file__).resolve().parents[1]
            / "hooks"
            / "shared"
            / "agent_handoff_marker.py"
        ).read_text(encoding="utf-8").split('"""', 2)[1]
        assert "partition_destination" in header
        assert "old_format_destination" in header

    def test_m3_the_router_function_is_authoritative_over_the_prose(self):
        """M3: the mapping is stated in both the prose and the function; the
        text must say which one a change belongs in."""
        assert (
            "THE FUNCTION IS AUTHORITATIVE; THIS PROSE NAMES THE OUTCOMES "
            "FOR THE READER" in SKILL_TEXT
        )

    def test_m6_underivable_report_needs_a_partition(self):
        """M6: the report fired once per population id, including ids with
        no journal match and therefore no partition to be underivable
        about."""
        guard = SKILL_TEXT.split("if not this_team:", 1)[1].split(
            "\n    else:", 1
        )[0]
        assert "if matches:" in guard
        assert guard.index("if matches:") < guard.index(
            "handoff team identity underivable for task"
        )
