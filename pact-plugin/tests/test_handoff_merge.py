"""
Location: pact-plugin/tests/test_handoff_merge.py
Summary: Contract tests for the reader-side helpers in
         hooks/shared/handoff_merge.py that the pact-handoff-harvest skill
         calls by route. Each arm is paired with the one-line mutation it
         exists to kill, named in its docstring.
Used by: pytest. The older union, identity and old-format arms live in
         test_handoff_merge_identity.py.
"""
from shared.handoff_merge import union_preferring_task_file

_REQUIRED_ALL = {
    "produced": "p",
    "decisions": ["d"],
    "reasoning_chain": "r",
    "uncertainty": ["u"],
    "integration": "i",
    "open_questions": ["o"],
}


class TestUnionJournalSideMembership:
    """State A decides the JOURNAL side by membership in
    (None, '', [], {}), the same test as the task-file side.
    Kills: `journal_value not in _EMPTY_VALUES` -> `journal_value`."""

    def test_journal_zero_beside_empty_disk_field_is_decided_and_reported(self):
        journal = dict(_REQUIRED_ALL, open_questions=0)
        disk = dict(_REQUIRED_ALL, open_questions=[])
        merged, reports = union_preferring_task_file(journal, disk, "7")
        # covering key sets -> the empty task-file value is kept, AND reported
        assert merged["open_questions"] == []
        assert reports == [
            "handoff field kept empty for task 7: field open_questions "
            "present and empty; the task-file required key set covers the "
            "journal copy"
        ]

    def test_journal_false_is_restored_when_disk_is_a_proper_subset(self):
        journal = dict(_REQUIRED_ALL, open_questions=False)
        disk = {k: v for k, v in _REQUIRED_ALL.items() if k != "produced"}
        disk["open_questions"] = ""
        merged, reports = union_preferring_task_file(journal, disk, "7")
        assert merged["open_questions"] is False
        assert any(
            "restored from the journal" in r and "open_questions" in r
            for r in reports
        )

    def test_journal_empty_values_still_do_not_trigger_state_a(self):
        for empty in (None, "", [], {}):
            journal = dict(_REQUIRED_ALL, open_questions=empty)
            disk = dict(_REQUIRED_ALL, open_questions=[])
            merged, reports = union_preferring_task_file(journal, disk, "7")
            assert merged["open_questions"] == []
            assert reports == []


# =============================================================================
# Journal selection and arc grouping
# =============================================================================
from shared.handoff_merge import (  # noqa: E402
    arc_of,
    current_arc,
    disk_handoff,
    disk_identity,
    owning_group,
    group_events,
    latest_by_ts_then_journal_order,
    ledger_token,
    parse_ledger_tokens,
    positioned,
    safe_from_teams,
    store_epochs,
    task_metadata,
    undivided_contents,
    unit_token,
)


def _created(task_id, team="t", ts="2026-11-01T10:00:00Z"):
    return {"type": "task_created", "task_id": task_id, "team": team, "ts": ts}


def _handoff(task_id, tag, team="t", ts="2026-11-01T11:00:00Z",
             agent="coder", subject="coder: implement fix"):
    event = {"type": "agent_handoff", "task_id": task_id, "agent": agent,
             "task_subject": subject, "ts": ts, "handoff": {"produced": tag}}
    if team is not None:
        event["team"] = team
    return event


def _units(journal, task_id="3"):
    """Positioned journal -> {key: [handoff tags]} for one task's handoffs."""
    pj = positioned(journal)
    epochs = store_epochs(pj)
    events = [e for e in pj
              if e["type"] == "agent_handoff" and e["task_id"] == task_id]
    groups = group_events(events, pj, epochs)
    return {k: [e["handoff"]["produced"] for e in v] for k, v in groups.items()}


class TestLatestByTsThenJournalOrder:
    """Kills: `>=` -> `>` (tie keeps the earlier line); a string compare
    (fractional later instant loses); an unparseable ts winning."""

    def test_equal_ts_keeps_the_later_line(self):
        a = {"ts": "2026-11-03T07:00:00Z", "n": 1}
        b = {"ts": "2026-11-03T07:00:00Z", "n": 2}
        assert latest_by_ts_then_journal_order([a, b])["n"] == 2

    def test_fractional_later_instant_wins(self):
        a = {"ts": "2026-11-03T07:00:00Z", "n": 1}
        b = {"ts": "2026-11-03T07:00:00.5Z", "n": 2}
        assert latest_by_ts_then_journal_order([a, b])["n"] == 2

    def test_unparseable_ts_never_beats_a_valid_one(self):
        a = {"ts": "2026-11-03T07:00:00Z", "n": 1}
        for bad in (1793700000, None, "not-a-time"):
            assert latest_by_ts_then_journal_order([a, {"ts": bad, "n": 2}])["n"] == 1

    def test_empty_group_is_none(self):
        assert latest_by_ts_then_journal_order([]) is None


class TestPositioned:
    def test_positions_are_journal_indexes_and_inputs_untouched(self):
        journal = [{"a": 1}, "junk", {"b": 2}]
        out = positioned(journal)
        assert [e["_pos"] for e in out] == [0, 2]
        assert "_pos" not in journal[0]


class TestStoreEpochs:
    """Kills: dropping the `<=` boundary test; one maximum for every team."""

    def test_reissued_number_opens_one_boundary(self):
        journal = [_created("7"), _handoff("7", "x"), _created("1")]
        assert store_epochs(positioned(journal)) == {"t": [2]}

    def test_increasing_ids_open_none(self):
        journal = [_created("1"), _created("2"), _handoff("2", "x"), _created("3")]
        assert store_epochs(positioned(journal)) == {}

    def test_non_numeric_ids_open_none(self):
        journal = [_created("7"), _created("abc"), _created(" 1"), _created(1)]
        assert store_epochs(positioned(journal)) == {}

    def test_teams_are_independent(self):
        journal = [_created("7", team="a"), _created("1", team="b")]
        assert store_epochs(positioned(journal)) == {}

    def test_maximum_resets_at_the_boundary(self):
        journal = [_created("7"), _created("1"), _created("2"), _created("1")]
        assert store_epochs(positioned(journal)) == {"t": [1, 3]}


class TestArcOf:
    """Kills: dropping the team check; assigning arcs by ts, not position."""

    def test_two_creations_pick_the_latest_at_or_before_by_position(self):
        pj = positioned([_created("3"), _handoff("3", "A1"),
                         _created("3"), _handoff("3", "A2")])
        epochs = store_epochs(pj)
        assert arc_of(pj[1], pj, epochs)[1] == 0
        assert arc_of(pj[3], pj, epochs)[1] == 2

    def test_same_ts_recreation_after_the_emit_leaves_it_in_the_old_arc(self):
        ts = "2026-11-01T10:00:00Z"
        pj = positioned([_created("3", ts=ts), _handoff("3", "A1", ts=ts),
                         _created("3", ts=ts)])
        assert arc_of(pj[1], pj, store_epochs(pj))[1] == 0

    def test_teamless_event_has_no_arc(self):
        pj = positioned([_created("3"), _handoff("3", "A", team=None)])
        assert arc_of(pj[1], pj, store_epochs(pj)) is None

    def test_another_teams_creation_is_ignored(self):
        pj = positioned([_created("3", team="other"), _handoff("3", "A")])
        assert arc_of(pj[1], pj, store_epochs(pj)) == (0, None)

    def test_creation_after_the_event_is_ignored(self):
        pj = positioned([_handoff("3", "A"), _created("3")])
        assert arc_of(pj[0], pj, store_epochs(pj)) == (0, None)

    def test_creation_from_an_earlier_epoch_does_not_cross_the_boundary(self):
        pj = positioned([_created("3"), _created("7"), _created("1"),
                         _handoff("3", "new instance, no creation event")])
        assert arc_of(pj[3], pj, store_epochs(pj)) == (1, None)

    def test_unpositioned_event_has_no_arc(self):
        assert arc_of(_handoff("3", "A"), [], {}) is None


class TestCurrentArc:
    def test_is_the_arc_at_the_end_of_the_journal(self):
        # Re-creating id 3 reissues a number, so it also opens epoch 1.
        pj = positioned([_created("3"), _handoff("3", "A1"), _created("3")])
        assert current_arc("t", "3", pj, store_epochs(pj)) == (1, 2)
        assert arc_of(pj[1], pj, store_epochs(pj)) == (0, 0)

    def test_teamless_is_none(self):
        assert current_arc("", "3", [], {}) is None


class TestGroupEvents:
    """Kills: a key without the arc; a key without the epoch; raw
    occupant_hash instead of disk_identity."""

    def test_two_arcs_same_occupant_are_two_groups(self):
        units = _units([_created("3"), _handoff("3", "A1"),
                        _created("3"), _handoff("3", "A2a"),
                        _handoff("3", "A2b")])
        assert sorted(units.values()) == [["A1"], ["A2a", "A2b"]]

    def test_no_creations_is_one_group(self):
        units = _units([_handoff("3", "A1"), _handoff("3", "A2")])
        assert list(units.values()) == [["A1", "A2"]]

    def test_epoch_separates_a_new_instance_with_no_creation_event(self):
        units = _units([_created("3"), _handoff("3", "old"),
                        _created("7"), _created("1"),
                        _handoff("3", "new")])
        assert sorted(units.values()) == [["new"], ["old"]]

    def test_empty_subject_and_sentinel_share_a_group(self):
        units = _units([_handoff("3", "A", subject=""),
                        _handoff("3", "B", subject="(no subject)")])
        assert list(units.values()) == [["A", "B"]]
        (identity, _), = units.keys()
        assert identity == disk_identity("coder", "")


class TestUndividedContents:
    def test_counts_distinct_contents(self):
        same = [_handoff("3", "A"), _handoff("3", "A")]
        assert undivided_contents(same) == 1
        assert undivided_contents(same + [_handoff("3", "B")]) == 2


class TestTotalAccessors:
    """Kills: `.get("metadata", {})`, which returns a non-dict as-is."""

    def test_non_dict_metadata_is_empty(self):
        for bad in ([], "x", None, 3):
            assert task_metadata({"metadata": bad}) == {}
            assert disk_handoff({"metadata": bad}) == {}

    def test_non_dict_task_is_empty(self):
        assert task_metadata([]) == {} and disk_handoff(None) == {}

    def test_handoff_must_be_a_non_empty_dict(self):
        assert disk_handoff({"metadata": {"handoff": []}}) == {}
        assert disk_handoff({"metadata": {"handoff": {}}}) == {}
        assert disk_handoff({"metadata": {"handoff": {"a": 1}}}) == {"a": 1}


class TestSafeFromTeams:
    """Kills: dropping the predicate; dropping `!= this_team`; dropping the
    dedup."""

    def test_filters_reports_and_dedups(self):
        events = [{"from": v} for v in ["a/b", "", 3, "T", "x", "x"]]
        teams, reports = safe_from_teams(events, "T")
        assert teams == ["x"]
        assert reports == [
            "handoff realign source invalid: team value 'a/b' is not a valid "
            "team name; store not read",
            "handoff realign source invalid: team value '3' is not a valid "
            "team name; store not read",
        ]

    def test_non_dict_events_are_skipped(self):
        assert safe_from_teams(["x", None], "T") == ([], [])


class TestLedgerTokens:
    """Kills: accepting bare ids; accepting an unsafe team or id."""

    def test_round_trip_both_shapes(self):
        this = ledger_token("3", "0123abcd")
        other = ledger_token(5, "unserializable", "session-x")
        assert this == "3#0123abcd"
        assert other == "session-x/5#unserializable"
        text = ("## team=t\n"
                f"Processed task IDs (this team): 1, {this}\n"
                f"Processed task IDs (this team): {other}\n"
                "Last processed: 2026-11-03\n")
        assert parse_ledger_tokens(text) == {this, other}

    def test_bare_ids_are_dropped(self):
        assert parse_ledger_tokens("Processed task IDs (this team): 3, 4") == set()

    def test_unsafe_parts_give_none(self):
        assert ledger_token("3", "0123abcd", "../x") is None
        assert ledger_token("3/4", "0123abcd") is None
        assert ledger_token(None, "0123abcd") is None
        assert ledger_token(True, "0123abcd") is None
        assert ledger_token("3", "0123ABCD") is None
        assert ledger_token("3", "0123abc") is None

    def test_unit_token_keys_on_content(self):
        a = unit_token("3", {"produced": "A"})
        b = unit_token("3", {"produced": "B"})
        assert a != b and a.startswith("3#") and len(a) == 10

    def test_non_str_section_is_empty(self):
        assert parse_ledger_tokens(None) == set()


class TestOwningGroup:
    """One owner per task file: the dated current-instance group, else the
    undatable (team-less) group, else none. Kills: swapping the precedence;
    dropping either branch."""

    DATED = (1, 4)

    def test_dated_current_group_owns(self):
        groups = {("id", self.DATED): [], ("other", self.DATED): []}
        assert owning_group(groups, "id", self.DATED) == ("id", self.DATED)

    def test_teamless_group_owns_when_no_dated_group_does(self):
        groups = {("id", None): [], ("id", (0, 1)): []}
        assert owning_group(groups, "id", self.DATED) == ("id", None)

    def test_dated_beats_teamless_with_the_same_identity(self):
        groups = {("id", None): [], ("id", self.DATED): []}
        assert owning_group(groups, "id", self.DATED) == ("id", self.DATED)

    def test_no_matching_identity_owns_nothing(self):
        groups = {("other", None): [], ("other", self.DATED): []}
        assert owning_group(groups, "id", self.DATED) is None

    def test_no_task_file_owns_nothing(self):
        assert owning_group({("id", None): []}, None, self.DATED) is None

    def test_malformed_inputs_never_raise(self):
        assert owning_group(None, "id", self.DATED) is None
        assert owning_group({("id", None): []}, "id", [1, 2]) == ("id", None)


class TestParseLedgerTokensWholeText:
    """Tokens are found anywhere in the section text, and only whole tokens.
    Kills: reading label-start lines only; dropping either boundary guard;
    dropping the `{from}/` prefix."""

    def test_mid_line_token_in_narrative_is_found(self):
        text = "Note: re-read 5#0123abcd after the drain; see above."
        assert parse_ledger_tokens(text) == {"5#0123abcd"}

    def test_from_token_is_found_whole(self):
        text = "- session-x/7#unserializable was recorded late"
        assert parse_ledger_tokens(text) == {"session-x/7#unserializable"}

    def test_non_tokens_are_rejected(self):
        for text in ("3", "#123", "abc#XYZ", "5#deadbeefx", "5#0123ABCD"):
            assert parse_ledger_tokens(text) == set(), text

    def test_a_token_cannot_start_inside_a_path(self):
        assert parse_ledger_tokens("a/b/5#0123abcd") == set()


class TestDualSpellingMembershipParity:
    """_field_map and resolve_handoff_field select a dual-spelled field by
    MEMBERSHIP in (None, '', [], {}), in both insertion orders, and agree.
    Kills a truthiness test on either spelling in either function."""

    CASES = [
        # (canonical value, alias value, expected)
        (None, 0, 0),
        (0, "x", 0),
        (False, "x", False),
        ("", False, False),
        ([], 0, 0),
        (None, "", None),
        ("", [], ""),
        ([], "legacy", "legacy"),
    ]

    def test_both_functions_agree_in_both_orders(self):
        from shared.handoff_merge import _field_map
        from shared.handoff_schema import resolve_handoff_field

        for canonical, alias, expected in self.CASES:
            for pairs in (
                [("decisions", canonical), ("key_decisions", alias)],
                [("key_decisions", alias), ("decisions", canonical)],
            ):
                handoff = dict(pairs)
                read = resolve_handoff_field(handoff, "decisions")
                _, mapped = _field_map(handoff)["decisions"]
                assert read == expected and type(read) is type(expected), pairs
                assert mapped == expected and type(mapped) is type(expected), pairs

    def test_empty_values_have_one_definition(self):
        import shared.handoff_merge as merge
        import shared.handoff_schema as schema

        assert merge._EMPTY_VALUES is schema._EMPTY_VALUES


class TestSafeFromTeamsCaseFold:
    """safe_from_teams compares team names by the fold read_task_json
    applies. Kills: comparing raw names when excluding this_team; raw names
    when deduplicating."""

    def test_case_variant_of_this_team_is_excluded_with_a_report(self):
        teams, reports = safe_from_teams([{"from": "Session-A"}], "session-a")
        assert teams == []
        assert reports == [
            "handoff realign source invalid: team value 'Session-A' names "
            "this team's store under a different case; store not read"
        ]

    def test_exact_this_team_stays_silent(self):
        assert safe_from_teams([{"from": "session-a"}], "session-a") == ([], [])

    def test_case_variants_of_one_team_dedup_to_the_first(self):
        events = [{"from": "Team-X"}, {"from": "team-x"}, {"from": "TEAM-X"}]
        assert safe_from_teams(events, "session-a") == (["Team-X"], [])

    def test_exclusion_agrees_with_what_read_task_json_would_read(self, tmp_path):
        """The two folds agree: every kept team reads a store OTHER than this
        team's, and every case variant of this team is not kept."""
        import json
        from shared.task_utils import read_task_json

        base = tmp_path / "tasks"
        (base / "session-a").mkdir(parents=True)
        (base / "session-a" / "5.json").write_text(json.dumps({"id": "OWN"}))
        (base / "other").mkdir()
        (base / "other" / "5.json").write_text(json.dumps({"id": "OTHER"}))
        names = ["Session-A", "SESSION-A", "session-a", "Other", "other"]
        kept, _ = safe_from_teams([{"from": n} for n in names], "session-a")
        for name in names:
            reads_own = read_task_json(
                "5", name, tasks_base_dir=str(base), base_fallback=False
            ).get("id") == "OWN"
            assert (name in kept) is False if reads_own else True, name
        assert kept == ["Other"]


class TestDiskIdentityOwnerCoercion:
    """A non-str owner is the empty owner, so `.get("owner")` returning None
    and `.get("owner", "")` give one identity. Kills: removing the coercion
    (None then hashes as the string 'None')."""

    def test_non_str_owner_equals_the_empty_owner(self):
        for owner in (None, 7, ["x"]):
            assert disk_identity(owner, "s") == disk_identity("", "s"), owner

    def test_str_owner_is_untouched(self):
        from shared.agent_handoff_marker import occupant_hash

        assert disk_identity("coder", "s") == occupant_hash("coder", "s")
        assert disk_identity("  ", "s") == occupant_hash("  ", "s")
        assert disk_identity("None", "s") != disk_identity(None, "s")


class TestJournalEventTypesExport:
    """The types the journal-taking helpers read, exported for the skill
    guard. Kills: dropping the export or the __all__ entry."""

    def test_export_is_the_set_the_helpers_walk(self):
        import shared.handoff_merge as merge

        assert "JOURNAL_EVENT_TYPES" in merge.__all__
        assert merge.JOURNAL_EVENT_TYPES == frozenset(
            {"agent_handoff", "task_created", "task_metadata_snapshot"}
        )
        assert merge.JOURNAL_EVENT_TYPES is merge._EPOCH_EVENT_TYPES
