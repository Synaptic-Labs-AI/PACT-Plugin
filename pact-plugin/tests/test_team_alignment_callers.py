"""
The ambiguity notice and dispatch_gate's deny detail both read the one
shared describer, shared.pact_context.describe_team_alignment.

* bootstrap_marker_writer's ambiguity notice is the describer's text when it
  finds two or more matching teams, and nothing in any other state.
* dispatch_gate's rule-8 deny adds the describer's text when the session
  team's config is absent, a bare statement that the config is absent when
  the describer has nothing to explain, and nothing when the config exists.

The describer is replaced by a stub returning each state, so these cells pin
how the callers consume it; the describer's own decisions are pinned by the
diagnostics tests.
"""

from fixtures.team_split import (
    NEW_TEAM,
    OLD_SID,
    OLD_TEAM,
    SECOND_TEAM,
    reset_context_module,
    split_world,
)

import bootstrap_marker_writer
import dispatch_gate
import shared.pact_context as ctx_module
from shared.pact_context import TeamAlignment

AMBIGUOUS = TeamAlignment(
    "ambiguous", ((NEW_TEAM, True), (SECOND_TEAM, True)),
    "ambiguous text: PACT cannot tell which team is this session's.",
)
UNRESOLVED = TeamAlignment("unresolved", ((NEW_TEAM, False),), "unresolved text")
NO_CANDIDATES = TeamAlignment("no_candidates", (), "no-candidates text")
RESOLVED = TeamAlignment("resolved", (), "")


def _stub(monkeypatch, result):
    monkeypatch.setattr(ctx_module, "describe_team_alignment", lambda: result)


def test_the_ambiguity_notice_is_the_describers_text(monkeypatch):
    _stub(monkeypatch, AMBIGUOUS)
    assert bootstrap_marker_writer._ambiguity_notice() == AMBIGUOUS.text


def test_no_other_state_gives_an_ambiguity_notice(monkeypatch):
    for result in (UNRESOLVED, NO_CANDIDATES, RESOLVED):
        _stub(monkeypatch, result)
        assert bootstrap_marker_writer._ambiguity_notice() is None, result.state


def _gate_world(tmp_path, monkeypatch):
    split_world(tmp_path, monkeypatch, saved_team=OLD_TEAM)
    reset_context_module()
    ctx_module.init({"session_id": OLD_SID})


def test_the_deny_detail_is_the_describers_text(tmp_path, monkeypatch):
    _gate_world(tmp_path, monkeypatch)
    monkeypatch.setattr(ctx_module, "get_team_name", lambda: OLD_TEAM)
    _stub(monkeypatch, UNRESOLVED)
    assert dispatch_gate._team_alignment_diagnosis() == "\n\n" + UNRESOLVED.text


def test_the_deny_detail_falls_back_when_nothing_is_explained(
        tmp_path, monkeypatch):
    _gate_world(tmp_path, monkeypatch)
    monkeypatch.setattr(ctx_module, "get_team_name", lambda: OLD_TEAM)
    _stub(monkeypatch, RESOLVED)
    assert dispatch_gate._team_alignment_diagnosis() == (
        f"\n\n`teams/{OLD_TEAM}/config.json` is absent.")


def test_no_deny_detail_while_the_config_exists(tmp_path, monkeypatch):
    """Control: the session team has a config, so even an ambiguous
    describer adds nothing."""
    _gate_world(tmp_path, monkeypatch)
    monkeypatch.setattr(ctx_module, "get_team_name", lambda: NEW_TEAM)
    _stub(monkeypatch, AMBIGUOUS)
    assert dispatch_gate._team_alignment_diagnosis() == ""
