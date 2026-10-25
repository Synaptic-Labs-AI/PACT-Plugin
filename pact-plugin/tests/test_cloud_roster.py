"""
Location: pact-plugin/tests/test_cloud_roster.py
Summary: Unit tests for shared/cloud_roster.py: the `applies` switch, record
         validation, append limits and file safety, the read caps, and every
         fold rule (design docs/architecture/cloud-roster.md §3.1, §9).
Used by: the pact-plugin test suite.
"""

from __future__ import annotations

import json
import os

import pytest

from fixtures.actor_frames import use_config, write_team_config
from fixtures.roster_frames import (
    agent_id,
    bind,
    confirm,
    intent,
    make_cloud_team,
    read_roster,
    roster_path,
    tool_use_id,
    write_roster,
)
from shared import cloud_roster

TEAM = "cloud-team"
TU1, TU2 = tool_use_id(1), tool_use_id(2)
A1, A2 = agent_id(1), agent_id(2)


@pytest.fixture
def config(tmp_path, monkeypatch):
    return use_config(monkeypatch, tmp_path / "cfg")


# ─── applies ────────────────────────────────────────────────────────────────


def test_applies_for_team_dir_without_config(config):
    make_cloud_team(config, TEAM)
    assert cloud_roster.applies(TEAM) is True


def test_does_not_apply_when_config_exists(config):
    write_team_config(config, TEAM, [])
    assert cloud_roster.applies(TEAM) is False


def test_does_not_apply_when_config_is_a_symlink(config, tmp_path):
    team_dir = make_cloud_team(config, TEAM)
    os.symlink(tmp_path / "nowhere.json", team_dir / "config.json")
    assert cloud_roster.applies(TEAM) is False


def test_does_not_apply_without_team_dir(config):
    assert cloud_roster.applies(TEAM) is False


@pytest.mark.parametrize("bad", ["", None, 3, "../x", "a/b", "x" * 200])
def test_does_not_apply_for_unsafe_team_names(config, bad):
    assert cloud_roster.applies(bad) is False


def test_teams_dir_override(tmp_path):
    (tmp_path / "teams" / TEAM).mkdir(parents=True)
    assert cloud_roster.applies(TEAM, str(tmp_path / "teams")) is True


# ─── append ─────────────────────────────────────────────────────────────────


def test_append_writes_one_compact_line_with_version_and_ts(config):
    make_cloud_team(config, TEAM)
    assert cloud_roster.append_record(
        TEAM, {"k": "intent", "tu": TU1, "name": "coder", "type": "pact-backend-coder"}
    )
    raw = roster_path(config, TEAM).read_text()
    assert raw.count("\n") == 1 and ", " not in raw
    (record,) = read_roster(config, TEAM)
    assert record["v"] == 1 and record["k"] == "intent" and "ts" in record


def test_append_creates_file_mode_0600(config):
    make_cloud_team(config, TEAM)
    cloud_roster.append_record(TEAM, {"k": "intent", "tu": TU1, "name": "c", "type": "t"})
    assert (roster_path(config, TEAM).stat().st_mode & 0o777) == 0o600


def test_append_refused_locally(config):
    write_team_config(config, TEAM, [])
    assert not cloud_roster.append_record(
        TEAM, {"k": "intent", "tu": TU1, "name": "c", "type": "t"}
    )
    assert not roster_path(config, TEAM).exists()


def test_append_never_creates_the_team_dir(config):
    assert not cloud_roster.append_record(
        TEAM, {"k": "intent", "tu": TU1, "name": "c", "type": "t"}
    )
    assert not (config / "teams" / TEAM).exists()


@pytest.mark.parametrize("record", [
    {"k": "intent", "tu": "not-a-toolu", "name": "c", "type": "t"},
    {"k": "intent", "tu": TU1, "name": "", "type": "t"},
    {"k": "intent", "tu": TU1, "name": "bad\nname", "type": "t"},
    {"k": "intent", "tu": TU1, "name": "c", "type": "has space"},
    {"k": "bind", "tu": TU1, "aid": "agent-1", "type": "t", "via": "type",
     "meta_present": False},
    {"k": "bind", "tu": TU1, "aid": A1, "type": "t", "via": "guess",
     "meta_present": False},
    {"k": "bind", "tu": TU1, "aid": A1, "type": "t", "via": "type",
     "meta_present": "no"},
    {"k": "confirm", "tu": TU1, "aid": A1, "type": "bad type"},
    {"k": "unknown", "tu": TU1},
    {"k": "intent", "tu": TU1, "name": "c", "type": "t", "ts": "yesterday"},
])
def test_append_refuses_invalid_records(config, record):
    make_cloud_team(config, TEAM)
    assert not cloud_roster.append_record(TEAM, record)
    assert read_roster(config, TEAM) == []


def test_append_refuses_record_over_1024_bytes(config):
    make_cloud_team(config, TEAM)
    record = {"k": "intent", "tu": TU1, "name": "n" * 128, "type": "t" * 128,
              "pad": "x" * 900}
    assert not cloud_roster.append_record(TEAM, record)


def test_append_stops_at_the_file_cap(config, monkeypatch):
    make_cloud_team(config, TEAM)
    record = {"k": "intent", "tu": TU1, "name": "coder", "type": "pact-backend-coder"}
    assert cloud_roster.append_record(TEAM, record)
    size = roster_path(config, TEAM).stat().st_size
    monkeypatch.setattr(cloud_roster, "MAX_ROSTER_BYTES", size)  # file now AT the cap
    assert cloud_roster.append_record(TEAM, dict(record, tu=TU2)) is False
    assert roster_path(config, TEAM).stat().st_size == size


def test_append_refuses_a_symlinked_roster(config, tmp_path):
    team_dir = make_cloud_team(config, TEAM)
    target = tmp_path / "elsewhere.jsonl"
    target.write_text("")
    os.symlink(target, team_dir / cloud_roster.ROSTER_FILENAME)
    assert not cloud_roster.append_record(
        TEAM, {"k": "intent", "tu": TU1, "name": "c", "type": "t"}
    )
    assert target.read_text() == ""


def test_append_refuses_a_team_dir_symlinked_outside_teams(config, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (config / "teams").mkdir(parents=True)
    os.symlink(outside, config / "teams" / TEAM)
    assert not cloud_roster.append_record(
        TEAM, {"k": "intent", "tu": TU1, "name": "c", "type": "t"}
    )
    assert list(outside.iterdir()) == []


# ─── load ───────────────────────────────────────────────────────────────────


def test_load_none_when_missing_or_local(config):
    make_cloud_team(config, TEAM)
    assert cloud_roster.load_roster(TEAM) is None
    write_team_config(config, "local", [])
    assert cloud_roster.load_roster("local") is None


def test_load_none_over_cap(config, monkeypatch):
    write_roster(config, TEAM, [intent(TU1, "c", "t")])
    monkeypatch.setattr(cloud_roster, "MAX_ROSTER_BYTES", 10)
    assert cloud_roster.load_roster(TEAM) is None


def test_load_none_for_symlinked_roster(config, tmp_path):
    team_dir = make_cloud_team(config, TEAM)
    target = tmp_path / "r.jsonl"
    target.write_text(json.dumps(intent(TU1, "c", "t")) + "\n")
    os.symlink(target, team_dir / cloud_roster.ROSTER_FILENAME)
    assert cloud_roster.load_roster(TEAM) is None


def test_load_skips_malformed_lines_and_bad_fields(config):
    write_roster(
        config, TEAM,
        [intent(TU1, "coder", "pact-backend-coder"),
         {"v": 2, "k": "intent", "tu": TU2, "name": "x", "type": "t"},
         {"v": 1, "k": "intent", "tu": TU2, "name": "bad\x00", "type": "t"}],
        raw_lines=["{not json", "[]", '"str"', '{"v":1,"k":"intent"'],
    )
    roster = cloud_roster.load_roster(TEAM)
    assert set(roster.intents) == {TU1}


# ─── fold ───────────────────────────────────────────────────────────────────


def _fold(*records):
    return cloud_roster.fold(list(records))


def test_first_intent_wins():
    roster = _fold(intent(TU1, "real", "pact-backend-coder"),
                   intent(TU1, "forged", "pact-secretary"))
    assert roster.intent(TU1).name == "real"
    assert roster.types_for_name("forged") == frozenset()


def test_bind_without_intent_ignored():
    assert _fold(bind(TU1, A1, "t")).binding_for(A1) is None


def test_bind_type_must_match_intent_type():
    roster = _fold(intent(TU1, "c", "pact-backend-coder"), bind(TU1, A1, "pact-secretary"))
    assert roster.binding_for(A1) is None


def test_bind_type_matches_across_pact_prefix():
    roster = _fold(intent(TU1, "c", "pact-backend-coder"),
                   bind(TU1, A1, "PACT:pact-backend-coder"))
    binding = roster.binding_for(A1)
    assert binding.name == "c" and binding.strength == cloud_roster.STRENGTH_BOUND


def test_confirm_without_type_binds():
    roster = _fold(intent(TU1, "c", "general-purpose"), confirm(TU1, A1))
    assert roster.binding_for(A1).strength == cloud_roster.STRENGTH_CONFIRMED


def test_confirm_with_wrong_type_ignored():
    roster = _fold(intent(TU1, "c", "general-purpose"), confirm(TU1, A1, "Explore"))
    assert roster.binding_for(A1) is None


def test_confirm_beats_conflicting_bind():
    roster = _fold(intent(TU1, "c", "t"), bind(TU1, A1, "t"), confirm(TU1, A2, "t"))
    assert roster.binding_for(A1) is None
    assert roster.binding_for(A2).tool_use_id == TU1


def test_agent_bound_to_two_tool_use_ids_is_unbound():
    roster = _fold(intent(TU1, "a", "t"), intent(TU2, "b", "t"),
                   bind(TU1, A1, "t"), bind(TU2, A1, "t"))
    assert roster.binding_for(A1) is None


def test_confirmed_attachment_beats_bind_only_for_same_agent():
    roster = _fold(intent(TU1, "a", "t"), intent(TU2, "b", "t"),
                   bind(TU1, A1, "t"), confirm(TU2, A1))
    assert roster.binding_for(A1).name == "b"


def test_two_confirms_for_one_agent_unbind():
    roster = _fold(intent(TU1, "a", "t"), intent(TU2, "b", "t"),
                   confirm(TU1, A1), confirm(TU2, A1))
    assert roster.binding_for(A1) is None


def test_repeated_identical_records_are_idempotent():
    roster = _fold(intent(TU1, "c", "t"), bind(TU1, A1, "t"), bind(TU1, A1, "t"),
                   confirm(TU1, A1), confirm(TU1, A1))
    assert roster.binding_for(A1).strength == cloud_roster.STRENGTH_CONFIRMED


def test_types_for_name_covers_every_intent_bound_or_not():
    roster = _fold(intent(TU1, "x", "PACT:pact-secretary"), intent(TU2, "x", "pact-auditor"))
    assert roster.types_for_name("x") == frozenset({"pact-secretary", "pact-auditor"})


def test_rebinding_six_secretaries_keeps_each_id():
    records = []
    for n in range(6):
        records += [intent(tool_use_id(n), "secretary", "pact-secretary"),
                    confirm(tool_use_id(n), agent_id(n), "pact-secretary")]
    roster = _fold(*records)
    assert roster.types_for_name("secretary") == frozenset({"pact-secretary"})
    assert all(roster.binding_for(agent_id(n)).name == "secretary" for n in range(6))


def test_unattached_intents_excludes_bound_and_confirmed():
    roster = _fold(intent(TU1, "a", "t"), intent(TU2, "b", "t"),
                   intent(tool_use_id(3), "c", "t"),
                   bind(TU1, A1, "t"), confirm(TU2, A2))
    assert [i.name for i in roster.unattached_intents()] == ["c"]


def test_round_trip_through_append_and_load(config):
    make_cloud_team(config, TEAM)
    assert cloud_roster.append_record(TEAM, {"k": "intent", "tu": TU1, "name": "c",
                                             "type": "pact-backend-coder"})
    assert cloud_roster.append_record(TEAM, {"k": "bind", "tu": TU1, "aid": A1,
                                             "type": "pact-backend-coder", "via": "type",
                                             "meta_present": False})
    assert cloud_roster.load_roster(TEAM).binding_for(A1).name == "c"
