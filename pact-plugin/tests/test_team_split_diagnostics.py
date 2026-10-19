"""
PACT says why it cannot decide the session's team.

When the recorded team's config is gone and the census cannot pick one live
team, PACT used to fall back silently to the recorded name. It now names the
recorded team, says its config is absent, and lists each live candidate with
whether it matches this session: in the lead's SessionStart context, in the
dispatch gate's "no Task assigned" denial, and on every lead prompt while two
or more candidates match. The explanation is built from the census's own
candidate list, so it cannot disagree with the decision. Where the census did
decide, or nothing is wrong, nothing is said.

Real path derivations throughout (tests/fixtures/team_split.py); the hooks'
main() runs in process with nothing stubbed.

Counter-tests, measured by source-only revert of each mechanism in a scratch
copy (cardinality of this file's 11-case run): dropping session_init's
emission -> 2 failed; dropping the explanation's one-match silence, its
team-activity check or its config-present check -> 1 failed each; dropping
the dispatch gate's diagnosis arm, or its config-absent condition -> 1
failed each; dropping the ambiguity notice -> 1 failed; dropping the
ambiguity notice's team-activity check -> 1 failed.
"""

import io
import json
from unittest.mock import patch

import pytest

from fixtures.role_frames import (
    captured_compaction_lead_sessionstart,
    captured_lead_userpromptsubmit_qualified,
)
from fixtures.team_split import (
    FOREIGN_TEAM,
    NEW_TEAM,
    OLD_SID,
    OLD_TEAM,
    SECOND_TEAM,
    read_journal,
    reset_context_module,
    split_world,
)

_NO_CONFIG = "has no config"
_AMBIGUOUS = "cannot tell which team is this session's"
_OWNER = "tester"


def _run_main(monkeypatch, world, module, frame):
    monkeypatch.chdir(world.project_dir)
    reset_context_module()
    with patch("sys.stdin", io.StringIO(json.dumps(frame))), \
         patch("sys.stdout", new_callable=io.StringIO) as out:
        try:
            module.main()
        except SystemExit:
            pass
    reset_context_module()
    text = out.getvalue().strip()
    return json.loads(text) if text else {}


def _session_start_context(monkeypatch, world, source="resume"):
    import session_init
    frame = captured_compaction_lead_sessionstart()
    frame.update(session_id=OLD_SID, cwd=str(world.project_dir), source=source)
    out = _run_main(monkeypatch, world, session_init, frame)
    return out.get("hookSpecificOutput", {}).get("additionalContext", "")


def _deny_reason(monkeypatch, world):
    import dispatch_gate
    frame = {
        "hook_event_name": "PreToolUse", "session_id": OLD_SID,
        "agent_type": "PACT:pact-orchestrator", "tool_name": "Agent",
        "tool_input": {"subagent_type": "pact-architect", "name": _OWNER,
                       "prompt": "Check TaskList for tasks assigned to you."},
    }
    out = _run_main(monkeypatch, world, dispatch_gate, frame)
    return out.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


def _prompt_notice(monkeypatch, world):
    import bootstrap_marker_writer
    frame = captured_lead_userpromptsubmit_qualified()
    frame.update(session_id=OLD_SID, cwd=str(world.project_dir))
    out = _run_main(monkeypatch, world, bootstrap_marker_writer, frame)
    return out.get("hookSpecificOutput", {}).get("additionalContext", "")


# -- SessionStart -------------------------------------------------------------


def test_an_undecided_launch_names_the_recorded_team_and_each_candidate(
        tmp_path, monkeypatch):
    """Two live teams both match: the lead's SessionStart context names the
    recorded team, says its config is absent, and lists both candidates."""
    world = split_world(tmp_path, monkeypatch, second_corroborated=True,
                        claude_md=True)
    context = _session_start_context(monkeypatch, world)
    assert _NO_CONFIG in context
    assert OLD_TEAM in context
    assert NEW_TEAM in context and SECOND_TEAM in context


def test_a_launch_lists_a_foreign_candidate_as_not_matching(tmp_path,
                                                            monkeypatch):
    """No team matches and another project's session is live: it is listed,
    and marked as not matching."""
    world = split_world(tmp_path, monkeypatch, live_team=None,
                        foreign="foreign-cwd", claude_md=True)
    context = _session_start_context(monkeypatch, world)
    assert _NO_CONFIG in context
    line = next(l for l in context.splitlines() if _NO_CONFIG in l)
    assert FOREIGN_TEAM in line and "does not match" in line


@pytest.mark.parametrize("world_kind", ["decided", "config-present", "fresh"])
def test_a_launch_says_nothing_when_there_is_nothing_to_explain(
        tmp_path, monkeypatch, world_kind):
    """The census decided (one match beside a foreign session); or the
    recorded team's config exists; or the session never lived as a team
    (no team activity in its journal). No diagnostic line."""
    kwargs = {
        "decided": dict(foreign="foreign-cwd"),
        "config-present": dict(old_config=True, live_team=None),
        "fresh": dict(second_corroborated=True, journal=("variety_assessed",)),
    }[world_kind]
    world = split_world(tmp_path, monkeypatch, claude_md=True, **kwargs)
    assert _NO_CONFIG not in _session_start_context(monkeypatch, world)


def test_the_explanation_is_empty_while_one_team_matches(tmp_path, monkeypatch):
    """Read directly, before any writer has moved the record: the recorded
    team's config is gone but exactly one live team matches, so the census
    has decided and there is nothing to explain. Every hook caller reaches
    the explanation only after the record moved, so this is the one place
    the rule is visible."""
    import shared.pact_context as ctx_module
    split_world(tmp_path, monkeypatch, foreign="foreign-cwd")
    reset_context_module()
    ctx_module.init({"session_id": OLD_SID})
    assert ctx_module.get_pact_context()["team_name"] == OLD_TEAM
    assert ctx_module.describe_team_alignment() == ""


# -- dispatch denial ----------------------------------------------------------


def test_a_denial_for_a_team_without_config_says_so(tmp_path, monkeypatch):
    """Two live teams match, so the gate stays on the recorded team and
    denies: the denial names the absent config before the generic causes,
    while the journaled reason stays the base text."""
    world = split_world(tmp_path, monkeypatch, second_corroborated=True)
    reason = _deny_reason(monkeypatch, world)
    assert "no Task assigned" in reason
    assert _NO_CONFIG in reason or f"teams/{OLD_TEAM}/config.json` is absent" in reason
    assert reason.find(OLD_TEAM) < reason.find("KNOWN CAUSES")
    decisions = [e for e in read_journal(world.session_dir)
                 if e.get("type") == "dispatch_decision"]
    assert decisions and _NO_CONFIG not in json.dumps(decisions[-1])


def test_a_denial_for_a_team_with_config_adds_nothing(tmp_path, monkeypatch):
    """Control: the recorded team's config exists, so the denial is the
    plain one."""
    world = split_world(tmp_path, monkeypatch, old_config=True, live_team=None)
    reason = _deny_reason(monkeypatch, world)
    assert "no Task assigned" in reason
    assert _NO_CONFIG not in reason
    assert "config.json` is absent" not in reason


# -- lead prompt --------------------------------------------------------------


def test_every_lead_prompt_warns_while_two_teams_match(tmp_path, monkeypatch):
    world = split_world(tmp_path, monkeypatch, second_corroborated=True)
    notice = _prompt_notice(monkeypatch, world)
    assert _AMBIGUOUS in notice
    assert NEW_TEAM in notice and SECOND_TEAM in notice
    assert _AMBIGUOUS in _prompt_notice(monkeypatch, world)


def test_a_fresh_session_gets_no_ambiguity_warning(tmp_path, monkeypatch):
    """A session that never lived as a team (no team activity in its
    journal): its recorded config is absent and two same-project teams are
    live and match, but it is not a split, so it is never told not to
    spawn."""
    world = split_world(tmp_path, monkeypatch, second_corroborated=True,
                        journal=("variety_assessed",))
    assert _AMBIGUOUS not in _prompt_notice(monkeypatch, world)


def test_one_matching_team_gives_no_ambiguity_warning(tmp_path, monkeypatch):
    """Control: one match realigns instead; no ambiguity notice."""
    world = split_world(tmp_path, monkeypatch, foreign="foreign-cwd")
    assert _AMBIGUOUS not in _prompt_notice(monkeypatch, world)
