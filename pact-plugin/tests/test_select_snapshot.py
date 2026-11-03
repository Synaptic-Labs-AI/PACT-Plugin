"""Contract of `handoff_merge.select_snapshot`, the Step 3.1 snapshot choice.

A work unit takes sibling keys only from a snapshot of ITS OWN instance: the
same task id and occupant, no other team, and an arc equal to the unit's
group arc. A team-less snapshot has no arc, so it never reaches a dated unit.
"""

import pytest
from shared.handoff_merge import disk_identity, select_snapshot

TEAM, OTHER = "session-snap", "session-elsewhere"
OCC = disk_identity("coder", "s")
T1, T2, T3 = (f"2026-11-01T00:00:0{i}Z" for i in (1, 2, 3))


def snap(label, *, team=None, task_id="5", occupant=OCC, ts=T1):
    e = {"type": "task_metadata_snapshot", "task_id": task_id, "occupant": occupant,
         "subject": "s", "metadata": {"label": label}, "ts": ts}
    if team is not None:
        e["team"] = team
    return e


def created(task_id="5", team=TEAM, ts=T1):
    return {"type": "task_created", "task_id": task_id, "team": team, "ts": ts}


def label(result):
    chosen, _ = result
    return chosen["metadata"]["label"] if chosen else None


# Dated unit: the arc of an instance created at position 0.
DATED = (0, 0)


def test_a_dated_unit_takes_its_own_team_and_arc():
    journal = [created(), snap("own", team=TEAM)]
    assert label(select_snapshot(journal, "5", OCC, TEAM, DATED)) == "own"


def test_a_dated_unit_never_takes_a_teamless_snapshot():
    journal = [created(), snap("legacy")]
    assert label(select_snapshot(journal, "5", OCC, TEAM, DATED)) is None


def test_a_teamless_unit_takes_a_teamless_snapshot():
    assert label(select_snapshot([snap("legacy")], "5", OCC, TEAM, None)) == "legacy"


def test_a_teamless_unit_never_takes_a_dated_snapshot():
    journal = [created(), snap("dated", team=TEAM)]
    assert label(select_snapshot(journal, "5", OCC, TEAM, None)) is None


def test_another_instances_snapshot_is_dropped_both_ways():
    journal = [created(ts=T1), snap("old instance", team=TEAM, ts=T1),
               created(ts=T2), snap("new instance", team=TEAM, ts=T3)]
    old_arc, new_arc = (0, 0), (1, 2)
    assert label(select_snapshot(journal, "5", OCC, TEAM, old_arc)) == "old instance"
    assert label(select_snapshot(journal, "5", OCC, TEAM, new_arc)) == "new instance"


def test_a_snapshot_naming_another_team_is_dropped_and_reported_once():
    journal = [created(team=OTHER), snap("foreign\nFORGED", team=OTHER)]
    chosen, reports = select_snapshot(journal, "5", OCC, TEAM, (0, 0))
    assert chosen is None
    assert len(reports) == 1
    assert "snapshot names team" in reports[0] and "\n" not in reports[0]


def test_occupant_and_task_id_must_match():
    journal = [created(), snap("wrong occupant", team=TEAM, occupant="x"),
               snap("wrong id", team=TEAM, task_id="6")]
    assert label(select_snapshot(journal, "5", OCC, TEAM, DATED)) is None


def test_the_latest_instant_wins_and_a_tie_keeps_the_later_line():
    journal = [created(), snap("first", team=TEAM, ts=T2),
               snap("second", team=TEAM, ts=T2), snap("earlier", team=TEAM, ts=T1)]
    assert label(select_snapshot(journal, "5", OCC, TEAM, DATED)) == "second"


def test_an_arc_given_as_a_json_list_is_the_same_arc():
    journal = [created(), snap("own", team=TEAM)]
    assert label(select_snapshot(journal, "5", OCC, TEAM, [0, 0])) == "own"


def test_the_returned_snapshot_carries_no_position_field():
    chosen, _ = select_snapshot([created(), snap("own", team=TEAM)], "5", OCC, TEAM, DATED)
    assert "_pos" not in chosen


@pytest.mark.parametrize("journal", [None, "x", [None, 3, "e"], [{"type": "task_metadata_snapshot"}]])
def test_malformed_input_never_raises(journal):
    assert select_snapshot(journal, "5", OCC, TEAM, None) == (None, [])


@pytest.mark.parametrize("arc", ["not an arc", 0, [], [0], [0, 1, 2], ["0", 0], [True, 0],
                                 [0, "1"], {"epoch": 0}, (0.5, None)])
def test_a_malformed_arc_selects_nothing_and_is_reported_once(arc):
    """A malformed arc is NOT the team-less arc: read as None it would take a
    team-less snapshot that belongs to another instance."""
    journal = [snap("legacy"), created(), snap("dated", team=TEAM)]
    chosen, reports = select_snapshot(journal, "5", OCC, TEAM, arc)
    assert chosen is None
    assert len(reports) == 1 and "not a valid arc" in reports[0]


@pytest.mark.parametrize("arc", [(0, None), [1, 3], (2, 0)])
def test_a_well_formed_arc_is_not_reported(arc):
    assert select_snapshot([created()], "5", OCC, TEAM, arc)[1] == []
