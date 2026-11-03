"""Execute the harvest skill's Step 3 pseudocode against the real helpers.

The block in `skills/pact-handoff-harvest/SKILL.md` is the instruction the
harvesting agent follows. Pins on its text can say that a clause is present;
they cannot say that a branch routes correctly. This module extracts the ONE
`python` fence after "Pseudocode for the read." and runs it, with the shared
`handoff_merge` helpers and in-memory task stores, so a routing or recording
change that keeps every pinned word still reddens a behavioural assertion.

Each arm names the behaviour it protects and the one-line mutant it must
catch; the mutant evidence is recorded with the change that added the arm.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from shared import handoff_merge
from shared.handoff_merge import handoff_content_key

_PLUGIN = Path(__file__).resolve().parents[1]

SKILL_TEXT = (
    _PLUGIN / "skills" / "pact-handoff-harvest" / "SKILL.md"
).read_text(encoding="utf-8")
_MARKER = "Pseudocode for the read."
_FENCE = "```python\n"
BLOCK = SKILL_TEXT.split(_MARKER, 1)[1].split(_FENCE, 1)[1].split("\n```", 1)[0]
# Compiled at module level: the block's loop uses a top-level `continue`.
CODE = compile(BLOCK, "SKILL.md#step3", "exec")

# The names the block may call. An explicit tuple, never dir(): a helper the
# block starts calling must be added here on purpose.
HELPERS = (
    "PARTITION_THIS", "PARTITION_FROM", "PARTITION_FOREIGN",
    "PARTITION_OLD_FORMAT", "OLD_FORMAT_DEST_MIXED", "OLD_FORMAT_DEST_FROM",
    "OLD_FORMAT_DEST_THIS", "partition_destination", "old_format_destination",
    "mixed_old_format_event", "union_preferring_task_file", "disk_identity",
    "report_safe", "latest_by_ts_then_journal_order", "positioned",
    "store_epochs", "group_events", "current_arc", "owning_group",
    "undivided_contents", "task_metadata", "disk_handoff", "safe_from_teams",
    "unit_token",
)

THIS, FROM, OTHER = "session-new", "session-old", "session-other"
T0, T1, T2, T3 = (f"2026-11-01T00:00:0{i}Z" for i in range(4))


def _handoff(produced="p", **extra):
    h = {"produced": produced, "decisions": "d", "uncertainty": "u",
         "integration": "i", "open_questions": "q"}
    h.update(extra)
    return h


H = _handoff()


def key(handoff):
    return handoff_content_key(handoff)


def ev(task_id, team=None, *, agent="coder", subject="s", handoff=None, ts=T1):
    e = {"type": "agent_handoff", "task_id": task_id, "agent": agent,
         "task_subject": subject, "handoff": dict(handoff or H), "ts": ts}
    if team is not None:
        e["team"] = team
    return e


def created(task_id, team, *, ts=T0):
    return {"type": "task_created", "task_id": task_id, "team": team, "ts": ts}


def task(handoff=None, *, owner="coder", subject="s", **siblings):
    meta = {"handoff": dict(handoff or H)}
    meta.update(siblings)
    return {"owner": owner, "subject": subject, "metadata": meta}


class Run:
    """One execution of the block: what it processed, reported and read."""

    def __init__(self, journal, stores, *, this=THIS, realign=(FROM,),
                 tasklist=(), census=None, processed=(), forced=(), patch=None):
        self.processed, self.reports, self.reads = [], [], []
        ns = {name: getattr(handoff_merge, name) for name in HELPERS}
        ns.update(
            Path=Path, datetime=datetime, timezone=timezone,
            task_list_dir=f"/cfg/tasks/{this}" if this else "",
            realign_events=[{"type": "team_realigned", "from": t} for t in realign],
            journal=list(journal),
            tasklist_ids=list(tasklist),
            census={team: set(ids) for team, ids in (census or {}).items()},
            processed_tokens=set(processed),
            forced_ids=set(forced),
            report=self._report,
            read_task_json=self._reader(stores),
            process=lambda handoff, metadata=None, section=None:
                self.processed.append((section, handoff, metadata)),
        )
        if patch:
            ns.update(patch)
        exec(CODE, ns)
        self.recorded = ns["recorded_tokens"]

    def _report(self, line):
        """Strict: a report is a non-empty str, so a `report(None)` mutant
        raises here instead of hiding behind a fragment count."""
        assert isinstance(line, str) and line, f"report() got {line!r}"
        self.reports.append(line)

    def _reader(self, stores):
        def read_task_json(task_id, team):
            self.reads.append((team, task_id))
            return stores.get(team, {}).get(task_id, {})
        return read_task_json

    def sections(self):
        return [section for section, _, _ in self.processed]

    def count(self, fragment):
        return sum(fragment in line for line in self.reports)


def test_the_skill_carries_exactly_one_python_fence():
    """A second fence is a local helper definition the routes replaced."""
    assert SKILL_TEXT.count(_FENCE) == 1
    assert SKILL_TEXT.count(_MARKER) == 1


# 1 -- mutant: route THIS -> FOREIGN
def test_this_team_event_is_processed_with_this_stores_metadata():
    stores = {THIS: {"7": task(audit_summary="a")}}
    run = Run([ev("7", THIS)], stores)
    assert run.sections() == [None]
    _, handoff, metadata = run.processed[0]
    assert handoff == H
    assert metadata == stores[THIS]["7"]["metadata"]
    assert run.count("identity mismatch") == 0


# 2 -- mutant: the from section reads THIS team's store
def test_from_team_unit_unions_with_its_own_store():
    stores = {FROM: {"5": task(_handoff(reasoning_chain="from disk"), sib="F")},
              THIS: {"5": task(_handoff("stranger"), owner="other", sib="T")}}
    run = Run([ev("5", FROM)], stores)
    assert run.sections() == [f"team={FROM}"]
    _, handoff, metadata = run.processed[0]
    assert handoff.get("reasoning_chain") == "from disk"
    assert metadata["sib"] == "F"


# 3 -- mutant: a FROM event also lands in this section
def test_from_team_event_never_lands_in_this_section():
    run = Run([ev("5", FROM)], {FROM: {"5": task()}}, tasklist=["5"])
    assert run.sections() == [f"team={FROM}"]
    assert run.count("handoff gap") == 0


# 4 -- mutant: the foreign-only discovery loop is dropped
def test_foreign_only_id_is_reported_once_and_never_processed():
    run = Run([ev("9", OTHER)], {OTHER: {"9": task()}})
    assert run.processed == []
    assert run.count(f"journal event names team {OTHER}") == 1


# 5 -- mutant: the unknown-label branch appends to this_events
def test_unknown_label_fails_closed():
    run = Run([ev("7", THIS)], {}, tasklist=["7"],
              patch={"partition_destination": lambda *a: "bogus"})
    assert run.processed == []
    assert run.count("identity mismatch for task 7") == 1


# 6 -- mutant: a MIXED old-format event is kept as this team's
def test_mixed_old_format_event_is_discarded_and_reported():
    stores = {FROM: {"4": task(owner="someone-else")}}
    run = Run([ev("4", agent="coder")], stores)
    assert [s for s, _, _ in run.processed if s is None] == []
    assert run.count("journal content equals team") == 1


# 7 -- mutant: DEST_FROM routes into this section
def test_old_format_event_owned_by_a_from_team_is_processed_there():
    run = Run([ev("4")], {FROM: {"4": task()}})
    assert run.sections() == [f"team={FROM}"]


# 8 -- mutant: discovery adds a FROM event as (this team, id)
def test_journal_only_from_unit_is_processed_despite_a_same_numbered_token():
    from_handoff = _handoff("from work")
    stores = {THIS: {"5": task(_handoff("this team's 5"), owner="other")}}
    run = Run([ev("5", FROM, handoff=from_handoff)], stores,
              processed={f"5#{key(from_handoff)}"})
    assert run.sections() == [f"team={FROM}"]
    assert run.recorded == [f"{FROM}/5#{key(from_handoff)}"]


# 9 -- mutant: the token check is dropped
def test_second_pass_with_the_recorded_tokens_processes_nothing():
    journal = [ev("7", THIS), ev("5", FROM, handoff=_handoff("f"))]
    stores = {THIS: {"7": task()}, FROM: {"5": task(_handoff("f"))}}
    first = Run(journal, stores)
    assert len(first.processed) == 2
    second = Run(journal, stores, processed=first.recorded)
    assert second.processed == []
    assert second.recorded == []


# 10 -- mutant: the token carries no content key
def test_reused_number_with_new_content_is_harvested():
    old, new = _handoff("old arc"), _handoff("new arc")
    run = Run([ev("3", THIS, handoff=new)], {},
              processed={"3", f"3#{key(old)}"})
    assert [h for _, h, _ in run.processed] == [new]
    assert run.recorded == [f"3#{key(new)}"]


# 11 -- mutant: the group key drops the arc
def test_two_recorded_creations_make_two_units():
    a, b = _handoff("arc a"), _handoff("arc b")
    journal = [created("3", THIS, ts=T0), ev("3", THIS, handoff=a, ts=T1),
               created("3", THIS, ts=T2), ev("3", THIS, handoff=b, ts=T3)]
    run = Run(journal, {})
    assert sorted(h["produced"] for _, h, _ in run.processed) == ["arc a", "arc b"]
    without = Run([ev("3", THIS, handoff=a, ts=T1),
                   ev("3", THIS, handoff=b, ts=T3)], {})
    assert [h for _, h, _ in without.processed] == [b]


# 11b -- mutant: the arc drops the store epoch
def test_recreated_store_separates_an_instance_with_no_creation_event():
    old, new = _handoff("old store"), _handoff("new store")
    journal = [ev("3", THIS, handoff=old, ts=T0), ev("7", THIS, ts=T0),
               created("1", THIS, ts=T1), ev("3", THIS, handoff=new, ts=T2)]
    run = Run(journal, {})
    produced = sorted(h["produced"] for _, h, _ in run.processed)
    assert produced == ["new store", "old store", "p"]


# 11c -- mutant: the no-boundary report is dropped
def test_undivided_contents_are_reported_once_and_the_latest_processed():
    a, b = _handoff("first"), _handoff("second")
    run = Run([ev("3", THIS, handoff=a, ts=T1), ev("3", THIS, handoff=b, ts=T2)], {})
    assert [h for _, h, _ in run.processed] == [b]
    assert run.count("holds 2 distinct contents with no creation boundary") == 1


# 11d -- mutant: arcs are assigned by ts rather than journal position
def test_same_second_recreation_keeps_the_old_emit_in_its_own_arc():
    a, b = _handoff("old arc"), _handoff("new arc")
    journal = [created("3", THIS, ts=T0), ev("3", THIS, handoff=a, ts=T1),
               created("3", THIS, ts=T1), ev("3", THIS, handoff=b, ts=T2)]
    run = Run(journal, {})
    assert sorted(h["produced"] for _, h, _ in run.processed) == ["new arc", "old arc"]


# 12 -- mutant: every group gets the task file's metadata
def test_only_the_owning_group_gets_the_task_files_metadata():
    stores = {THIS: {"6": task(_handoff("b"), owner="b", sib="b's")}}
    run = Run([ev("6", THIS, agent="a", handoff=_handoff("a")),
               ev("6", THIS, agent="b", handoff=_handoff("b"))], stores)
    by_produced = {h["produced"]: metadata for _, h, metadata in run.processed}
    assert by_produced["a"] is None
    assert by_produced["b"]["sib"] == "b's"


# 13 -- mutant: admission per id instead of per store
def test_census_admission_is_per_store():
    stores = {FROM: {"5": task(sib="A")}, OTHER: {"5": task(sib="B")}}
    run = Run([], stores, realign=(FROM, OTHER), census={FROM: {"5"}})
    assert run.sections() == [f"team={FROM}"]


# 14 -- mutant: safe_from_teams keeps this team's own name
def test_a_realign_from_this_team_processes_each_unit_once():
    run = Run([ev("7", THIS)], {THIS: {"7": task()}}, realign=(THIS,),
              census={THIS: {"7"}})
    assert run.sections() == [None]


# 15 -- mutant: safe_from_teams drops its path-component predicate
def test_an_unsafe_realign_source_is_reported_and_never_read():
    run = Run([], {}, realign=("../x",), census={"../x": {"5"}})
    assert run.count("handoff realign source invalid") == 1
    assert all(team != "../x" for team, _ in run.reads)
    assert run.processed == []


# 16 -- mutant: the tie-break keeps the EARLIER line on an equal instant
def test_equal_instant_takes_the_later_journal_line():
    a, b = _handoff("earlier line"), _handoff("later line")
    run = Run([ev("3", THIS, handoff=a, ts=T1), ev("3", THIS, handoff=b, ts=T1)], {})
    assert [h["produced"] for _, h, _ in run.processed] == ["later line"]


# 17 -- mutant: the block reads metadata with a bare .get
def test_a_non_mapping_metadata_is_processed_as_empty():
    stores = {THIS: {"8": {"owner": "coder", "subject": "s", "metadata": []}}}
    run = Run([], stores, census={THIS: {"8"}})
    assert run.processed == [(None, {}, {})]


# 18 -- mutant: the gap ignores a handled from partition
def test_gap_is_reported_only_when_no_source_resolves():
    gap = Run([], {}, tasklist=["9"])
    assert gap.count("handoff gap for task 9") == 1
    handled = Run([ev("9", FROM)], {FROM: {"9": task()}}, tasklist=["9"])
    assert handled.count("handoff gap") == 0


# 19 -- mutant: the FORCED clause is dropped
def test_a_forced_task_is_processed_even_with_its_token_recorded():
    run = Run([ev("7", THIS)], {}, processed={f"7#{key(H)}"}, forced={"7"})
    assert run.sections() == [None]


# 20 -- mutant: the owner key is the dated (identity, current arc) pair
def test_a_teamless_group_owns_a_present_task_file():
    stores = {THIS: {"5": task(_handoff(reasoning_chain="disk"), sib="T")}}
    run = Run([ev("5", handoff=_handoff(reasoning_chain="disk"))], stores)
    assert run.sections() == [None]
    _, handoff, metadata = run.processed[0]
    assert metadata["sib"] == "T"
    assert run.count("identity mismatch") == 0


# 21 -- mutant: owning_group prefers the undated group
def test_a_dated_current_group_owns_the_file_over_a_teamless_one():
    old, new = _handoff("team-less copy"), _handoff("dated copy")
    stores = {THIS: {"5": task(_handoff("on disk"), sib="T")}}
    journal = [ev("5", handoff=old, ts=T0), created("5", THIS, ts=T1),
               ev("5", THIS, handoff=new, ts=T2)]
    run = Run(journal, stores)
    by_produced = {h["produced"]: metadata for _, h, metadata in run.processed}
    assert by_produced["team-less copy"] is None
    assert by_produced["on disk"]["sib"] == "T"
    assert run.count("identity mismatch") == 0


# 22 -- mutant: a non-owning group of the same identity gets the file's metadata
def test_an_older_instance_of_the_same_occupant_never_gets_the_owners_siblings():
    old, new = _handoff("old instance"), _handoff("current instance")
    stores = {THIS: {"5": task(new, sib="current instance's")}}
    journal = [created("5", THIS, ts=T0), ev("5", THIS, handoff=old, ts=T1),
               created("5", THIS, ts=T2), ev("5", THIS, handoff=new, ts=T3)]
    run = Run(journal, stores)
    by_produced = {h["produced"]: metadata for _, h, metadata in run.processed}
    assert by_produced["old instance"] is None
    assert by_produced["current instance"]["sib"] == "current instance's"


# 23 -- mutant: the non-str task-id guard is removed
@pytest.mark.parametrize("bad_id", [7, None, ""])
def test_a_non_string_task_id_is_skipped_and_reported_once(bad_id):
    bad = ev("8", THIS)
    bad["task_id"] = bad_id
    run = Run([bad, ev("8", THIS)], {})
    assert run.sections() == [None]
    assert run.count("handoff event skipped: task id") == 1


# 24 -- mutant: a disk-only unit keys on its whole metadata
def test_a_disk_only_unit_and_its_later_journal_copy_share_one_token():
    store = {THIS: {"5": task(H)}}
    first = Run([], store, census={THIS: {"5"}})
    assert len(first.processed) == 1
    second = Run([ev("5", THIS)], store, census={THIS: {"5"}},
                 processed=first.recorded)
    assert second.processed == []


# 25 -- mutant: a disk-only unit keys on its whole metadata
def test_a_sibling_added_beside_a_handoff_does_not_reharvest_the_unit():
    first = Run([], {THIS: {"5": task(H)}}, census={THIS: {"5"}})
    second = Run([], {THIS: {"5": task(H, post_completion_verification="ok")}},
                 census={THIS: {"5"}}, processed=first.recorded)
    assert second.processed == []


# 26 -- mutant: a disk-only unit keys on its handoff alone
def test_a_changed_sibling_only_task_is_harvested_again():
    def sibling_only(value):
        return {"owner": "coder", "subject": "s", "metadata": {"audit_summary": value}}
    first = Run([], {THIS: {"5": sibling_only("a")}}, census={THIS: {"5"}})
    assert len(first.processed) == 1
    second = Run([], {THIS: {"5": sibling_only("b")}}, census={THIS: {"5"}},
                 processed=first.recorded)
    assert len(second.processed) == 1


# ---- A1-A8: every report and every from-team route ------------------------

class _FixedClock:
    """`datetime` stand-in: answers only for UTC, so a clock read without
    the UTC zone raises instead of returning local time."""

    @staticmethod
    def now(tz=None):
        assert tz is timezone.utc, tz
        return datetime(2026, 11, 1, 0, 0, 9, tzinfo=timezone.utc)


# A1 -- mutant: `matches and not this_team` -> `or`
def test_a_census_only_id_with_no_team_reports_nothing_underivable():
    run = Run([], {}, this="", tasklist=["8"])
    assert run.count("underivable") == 0


# A2 -- mutants: the from section's group_events/current_arc arguments -> None
def test_from_team_instances_split_by_creation_and_the_file_joins_the_current_one():
    old, new = _handoff("from old instance"), _handoff("from current instance")
    stores = {FROM: {"5": task(dict(new, reasoning_chain="from disk"), sib="F")}}
    journal = [created("5", FROM, ts=T0), ev("5", FROM, handoff=old, ts=T1),
               created("5", FROM, ts=T2), ev("5", FROM, handoff=new, ts=T3)]
    run = Run(journal, stores)
    by = {h["produced"]: (s, h, m) for s, h, m in run.processed}
    assert set(by) == {"from old instance", "from current instance"}
    assert all(s == f"team={FROM}" for s, _, _ in by.values())
    _, current, current_meta = by["from current instance"]
    assert current.get("reasoning_chain") == "from disk" and current_meta["sib"] == "F"
    _, older, older_meta = by["from old instance"]
    assert "reasoning_chain" not in older and older_meta is None


# A3 -- mutant: union_preferring_task_file(handoff -> None), both sections
@pytest.mark.parametrize("team", [THIS, FROM])
def test_a_journal_only_field_survives_the_union(team):
    journal_copy = _handoff(note="journal only")
    run = Run([ev("5", team, handoff=journal_copy)], {team: {"5": task(H)}})
    assert [h.get("note") for _, h, _ in run.processed] == ["journal only"]


# A4 -- mutant: the from owner's selection key -> None
def test_a_from_owner_is_skipped_on_its_token_and_harvested_on_new_content():
    stores = {FROM: {"5": task(H)}}
    first = Run([ev("5", FROM)], stores)
    assert len(first.processed) == 1
    assert Run([ev("5", FROM)], stores, processed=first.recorded).processed == []
    revised = _handoff("revised")
    later = Run([ev("5", FROM, handoff=revised)], {FROM: {"5": task(revised)}},
                processed=first.recorded)
    assert [h["produced"] for _, h, _ in later.processed] == ["revised"]


# A5 -- mutant: a from non-owner's handoff -> None, and select's task id -> None
def test_a_from_non_owner_is_processed_from_its_journal_copy():
    mine, theirs = _handoff("owner's"), _handoff("other occupant's")
    stores = {FROM: {"5": task(mine, owner="a")}}
    run = Run([ev("5", FROM, agent="a", handoff=mine),
               ev("5", FROM, agent="b", handoff=theirs)], stores)
    by = {h["produced"]: m for _, h, m in run.processed}
    assert by["other occupant's"] is None
    assert [h for _, h, _ in run.processed if h["produced"] == "other occupant's"] == [theirs]


def test_a_from_group_with_undivided_contents_reports_its_task():
    run = Run([ev("5", FROM, handoff=_handoff("a"), ts=T1),
               ev("5", FROM, handoff=_handoff("b"), ts=T2)], {FROM: {"5": task(_handoff("b"))}})
    assert run.reports.count(
        "handoff group for task 5 holds 2 distinct contents with no creation "
        "boundary; the latest was processed; the earlier contents remain in the "
        "journal") == 1


# A6 -- mutants: every from disk-only handle() argument, and `disk or meta`
def test_a_from_disk_only_unit_keys_on_its_handoff_and_reharvests_on_siblings():
    stores = {FROM: {"5": task(H, sib="F")}}
    first = Run([], stores, census={FROM: {"5"}})
    assert first.processed == [(f"team={FROM}", H, stores[FROM]["5"]["metadata"])]
    second = Run([ev("5", FROM)], stores, census={FROM: {"5"}}, processed=first.recorded)
    assert second.processed == []

    def sibling_only(value):
        return {"owner": "coder", "subject": "s", "metadata": {"audit_summary": value}}
    before = Run([], {FROM: {"5": sibling_only("a")}}, census={FROM: {"5"}})
    after = Run([], {FROM: {"5": sibling_only("b")}}, census={FROM: {"5"}},
                processed=before.recorded)
    assert len(after.processed) == 1


# A7 -- mutant: the from section's `owner is None` negated, and its report sites
def test_from_store_divergence_reports_once_each():
    absent = Run([ev("5", FROM)], {})
    assert absent.reports.count(
        "handoff divergence check unavailable for task 5: task file absent") == 1
    stranger = Run([ev("5", FROM, agent="a")], {FROM: {"5": task(owner="b")}})
    assert stranger.reports.count(
        "handoff identity mismatch for task 5: task file present, no event group "
        "carries its identity") == 1


# A8 -- every report( site in the block, its EXACT line, with real values
REPORT_SCENARIOS = [
    ("realign source rejected", lambda: Run([], {}, realign=("../x",)),
     "handoff realign source invalid: team value '../x' is not a valid team name; store not read"),
    ("gap", lambda: Run([], {}, tasklist=["9"], patch={"datetime": _FixedClock}),
     "handoff gap for task 9: no source resolved; agent unknown; recorded 2026-11-01T00:00:09Z"),
    ("event skipped", lambda: Run([dict(ev("8", THIS), task_id=7)], {}),
     "handoff event skipped: task id '7' is not a non-empty string"),
    ("token unavailable", lambda: Run([], {THIS: {"a b": task()}}, census={THIS: {"a b"}}),
     "handoff ledger token unavailable for task a b: unit processed, not recorded"),
    ("undivided contents", lambda: Run([ev("3", THIS, handoff=_handoff("x"), ts=T1),
                                        ev("3", THIS, handoff=_handoff("y"), ts=T2)], {}),
     "handoff group for task 3 holds 2 distinct contents with no creation boundary; "
     "the latest was processed; the earlier contents remain in the journal"),
    ("underivable", lambda: Run([ev("7", THIS)], {}, this=""),
     "handoff team identity underivable for task 7: no Task list: directory in context"),
    ("this file absent", lambda: Run([ev("7", THIS)], {}),
     "handoff divergence check unavailable for task 7: task file absent"),
    ("foreign-only at discovery", lambda: Run([ev("9", OTHER)], {}),
     f"handoff identity mismatch for task 9: journal event names team {OTHER}; this store is team {THIS}"),
    ("non-str team", lambda: Run([dict(ev("7"), team=5)], {}, tasklist=["7"]),
     "handoff team value invalid for task 7: event team value '5' is not a valid team name; event not processed"),
    ("foreign in the loop", lambda: Run([ev("7", OTHER)], {}, tasklist=["7"]),
     f"handoff identity mismatch for task 7: journal event names team {OTHER}; this store is team {THIS}"),
    ("this mismatch", lambda: Run([ev("7", THIS, agent="a")], {THIS: {"7": task(owner="b")}}),
     "handoff identity mismatch for task 7: task file present, no event group carries its identity"),
    ("old-format mixed", lambda: Run([ev("4", agent="coder")], {FROM: {"4": task(owner="someone-else")}}),
     f"handoff identity mismatch for task 4: journal content equals team {FROM} task 4, whose identity it does not carry"),
    ("old-format unmatched", lambda: Run([ev("4")], {}),
     "handoff cross-team check unavailable for task 4: no other named store carries the identity"),
    ("from no group, file absent", lambda: Run([], {}, census={FROM: {"5"}}),
     "handoff divergence check unavailable for task 5: task file absent"),
    ("from groups, file absent", lambda: Run([ev("5", FROM)], {}),
     "handoff divergence check unavailable for task 5: task file absent"),
    ("from mismatch", lambda: Run([ev("5", FROM, agent="a")], {FROM: {"5": task(owner="b")}}),
     "handoff identity mismatch for task 5: task file present, no event group carries its identity"),
    ("this union line", lambda: Run([ev("5", THIS, handoff=_handoff(note="j"))], {THIS: {"5": task(H)}}),
     "handoff field kept from the journal for task 5: field note absent from the task file "
     "and not a required field; the required key sets do not decide it"),
    ("from union line", lambda: Run([ev("5", FROM, handoff=_handoff(note="j"))], {FROM: {"5": task(H)}}),
     "handoff field kept from the journal for task 5: field note absent from the task file "
     "and not a required field; the required key sets do not decide it"),
]


def _report_sites():
    import ast
    return [n for n in ast.walk(ast.parse(BLOCK)) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name) and n.func.id == "report"]


def test_every_report_site_has_a_scenario():
    """A new report( site in the block needs a scenario here."""
    assert len(REPORT_SCENARIOS) == len(_report_sites()) > 0


@pytest.mark.parametrize("label", [label for label, _, _ in REPORT_SCENARIOS])
def test_every_report_template_is_exact(label):
    _, scenario, line = next(s for s in REPORT_SCENARIOS if s[0] == label)
    assert scenario().reports.count(line) == 1


@pytest.mark.parametrize("this", ["", None])
def test_an_underivable_team_routes_every_event_old_format(this):
    """Control for the guard: no `Task list:` makes every event OLD-FORMAT
    and reports once per task with a journal match."""
    run = Run([ev("7", THIS), ev("8", OTHER)], {}, this=this)
    assert run.count("team identity underivable for task 7") == 1
    assert run.count("team identity underivable for task 8") == 1
    assert run.count("journal event names team") == 0
