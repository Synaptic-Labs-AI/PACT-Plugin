"""
Location: pact-plugin/tests/test_pin_caps_gate_credit.py

A pin moved by two Edits, through the shipped hooks: `pin_caps_gate.py` decides
each Edit at PreToolUse and records the agent's chain credit after an ALLOW;
`track_files.py` confirms the write at PostToolUse and moves the baseline for
a credited move; `missed_wake_scan.py` reports growth at the next prompt. Each
row runs the hooks as subprocesses in the drift tests' world (a tmp_path config
root, a git repository holding the project CLAUDE.md and the lead's session
context); the test applies each allowed Edit to the file itself, between the
two hook calls, as the tool would.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from clock_shift.clock_shift_env import carry_clock_shift
from test_claude_md_drift import HOOKS, LEAD, SID, TRACK, _doc, _hook, _pins, _prompt, world  # noqa: F401 — sibling harness reuse; world is a fixture

GATE = HOOKS / "pin_caps_gate.py"
MEMBER = {"agent_type": "probe-coder", "agent_id": "aprobe-coder-0123456789abcdef"}
OTHER_MEMBER = {"agent_type": "probe-coder", "agent_id": "aprobe-coder-fedcba9876543210"}
PIN_3 = "<!-- pinned: 2026-04-21 -->\n### Pin 3\nsecret body 3\n\n"
PIN_12 = "<!-- pinned: 2026-04-21 -->\n### Pin 12\nsecret body 12\n\n"
REMOVE_3 = {"old_string": PIN_3, "new_string": ""}
READD_3 = {"old_string": PIN_12, "new_string": PIN_12 + PIN_3}


def _frame(w, tool_input, event, frame):
    return {"hook_event_name": event, "tool_name": "Edit", "session_id": SID, "agent_type": LEAD,
            "tool_input": {"file_path": str(w.claude_md), "replace_all": False, **tool_input}, **frame}


def _gate(w, tool_input, **frame):
    """The gate's verdict on an Edit of the world's CLAUDE.md."""
    result = subprocess.run([sys.executable, str(GATE)], input=json.dumps(_frame(w, tool_input, "PreToolUse", frame)),
                            capture_output=True, text=True, env=carry_clock_shift(dict(w.env)), cwd=w.repo,
                            timeout=600)
    if result.returncode == 2:
        return "deny"
    assert result.returncode == 0, result.stderr
    return "allow" if json.loads(result.stdout) == {"suppressOutput": True} else "advisory"


def _edit(w, tool_input, **frame):
    """An Edit as the tool runs it: the gate decides; when it allows, the
    file changes and track_files.py's PostToolUse leg runs. The verdict."""
    verdict = _gate(w, tool_input, **frame)
    if verdict != "deny":
        text = w.claude_md.read_text(encoding="utf-8")
        assert text.count(tool_input["old_string"]) == 1
        w.claude_md.write_text(text.replace(tool_input["old_string"], tool_input["new_string"]), encoding="utf-8")
        _hook(w, TRACK, _frame(w, tool_input, "PostToolUse", frame))
    return verdict


@pytest.fixture
def at_13(world):
    """The world's CLAUDE.md at 13 pins, with the baseline recorded."""
    world.claude_md.write_text(_doc(_pins(13)), encoding="utf-8")
    _prompt(world)
    return world


def test_a_pin_moved_by_two_edits_at_13_is_allowed_and_not_reported(at_13):
    assert _edit(at_13, REMOVE_3) == "allow"
    assert _edit(at_13, READD_3) == "allow"
    assert at_13.claude_md.read_text(encoding="utf-8").count("### Pin") == 13
    assert _prompt(at_13) is None


def test_the_re_add_without_the_removal_is_refused(at_13):
    at_13.claude_md.write_text(_doc(_pins(13)).replace(PIN_3, ""), encoding="utf-8")
    assert _gate(at_13, READD_3) == "deny"


def test_an_outside_change_between_the_edits_refuses_the_re_add(at_13):
    assert _edit(at_13, REMOVE_3) == "allow"
    text = at_13.claude_md.read_text(encoding="utf-8")
    at_13.claude_md.write_text(text.replace("secret body 7", "secret body 7, edited in an editor"),
                               encoding="utf-8")
    assert _gate(at_13, READD_3) == "deny"


def test_an_in_process_teammate_keeps_its_own_chain(at_13):
    assert _edit(at_13, REMOVE_3, **MEMBER) == "allow"
    assert _gate(at_13, READD_3) == "deny"  # the lead holds no credit
    assert _gate(at_13, READD_3, **OTHER_MEMBER) == "deny"  # nor does another teammate
    assert _edit(at_13, READD_3, **MEMBER) == "allow"


def test_a_chain_from_another_session_gives_no_credit(at_13):
    assert _edit(at_13, REMOVE_3) == "allow"
    assert _gate(at_13, READD_3, agent_type="pact-backend-coder", session_id="teammate-session") == "deny"


@pytest.mark.parametrize("damage", ["unreadable", "corrupt", "missing"])
def test_a_damaged_or_missing_record_gives_todays_verdict(at_13, damage):
    assert _edit(at_13, REMOVE_3) == "allow"
    record = at_13.session_dir / "claude-md-pin-credit" / "lead.json"
    assert record.exists()
    if damage == "unreadable":
        record.chmod(0o000)
    elif damage == "corrupt":
        record.write_text("{not json", encoding="utf-8")
    else:
        record.unlink()
    try:
        assert _gate(at_13, READD_3) == "deny"
        assert _gate(at_13, {"old_string": "secret body 5", "new_string": "secret body five"}) == "allow"
    finally:
        if record.exists():
            record.chmod(0o600)


TWELVE_INDENTED_LINE = "  ### Sub heading\n"


@pytest.fixture
def at_12(world):
    _prompt(world)
    return world


def test_the_agents_own_indented_heading_un_indented_at_12_is_refused(at_12):
    assert _edit(at_12, {"old_string": "secret body 4\n", "new_string": "secret body 4\n" + TWELVE_INDENTED_LINE}) == "allow"
    assert _gate(at_12, {"old_string": TWELVE_INDENTED_LINE, "new_string": "### Sub heading\n"}) == "deny"


def test_the_users_own_indented_heading_un_indented_at_12_is_allowed(at_12):
    text = at_12.claude_md.read_text(encoding="utf-8")
    at_12.claude_md.write_text(text.replace("secret body 4\n", "secret body 4\n" + TWELVE_INDENTED_LINE),
                               encoding="utf-8")
    assert _gate(at_12, {"old_string": TWELVE_INDENTED_LINE, "new_string": "### Sub heading\n"}) == "allow"


def test_two_teammates_moving_pins_at_once_keep_separate_chains(at_13):
    pin_5 = "<!-- pinned: 2026-04-21 -->\n### Pin 5\nsecret body 5\n\n"
    at_13.claude_md.write_text(_doc(_pins(14)), encoding="utf-8")
    assert _edit(at_13, REMOVE_3, **MEMBER) == "allow"
    assert _edit(at_13, {"old_string": pin_5, "new_string": ""}, **OTHER_MEMBER) == "allow"
    # The other teammate's removal changed the file, so the first chain is broken.
    assert _gate(at_13, READD_3, **MEMBER) == "deny"
    assert _edit(at_13, {"old_string": PIN_12, "new_string": PIN_12 + pin_5}, **OTHER_MEMBER) == "allow"
    records = sorted(p.name for p in (at_13.session_dir / "claude-md-pin-credit").glob("*.json"))
    assert records == [f"{MEMBER['agent_id']}.json", f"{OTHER_MEMBER['agent_id']}.json"]


def test_the_lead_advisory_still_reports_growth_the_gate_never_saw(at_13):
    """Control for the first row: growth past the cap from outside any tool is
    still reported at the next prompt."""
    text = at_13.claude_md.read_text(encoding="utf-8")
    at_13.claude_md.write_text(text.replace(PIN_12, PIN_12 + PIN_3.replace("Pin 3", "Pin 30")), encoding="utf-8")
    assert "14 pins" in (_prompt(at_13) or "")
