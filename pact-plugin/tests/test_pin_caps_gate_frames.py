"""L2 seam test: which frames the pin-cap gate checks, and the deny text each sees.

Location: pact-plugin/tests/test_pin_caps_gate_frames.py
Summary: runs the real pin_caps_gate.py as a subprocess against a real config
         root (team config, the lead's session context, the session registry)
         and a real project CLAUDE.md at 12 pins. The gate checks the lead, a
         PACT specialist type, and any frame whose session belongs to a PACT
         team; it checks no plain session and no non-PACT --agent session. A
         team member's count denial asks the team-lead to free a slot instead
         of naming the pin command.
Used by: hook_infra_classifier's COVERED_L2 mapping for `pin_caps_gate`.

Every subprocess gets HOME, CLAUDE_CONFIG_DIR and CLAUDE_PROJECT_DIR inside
tmp_path, so no row can read the real ~/.claude. The team read is never
monkeypatched in a subprocess row; the one in-process row makes it raise.

REVERT-CARDINALITY NON-VACUITY GATE, MEASURED. Run against the gate as it was
before frame gating (lead only), this file reports 17 failed, 14 passed. Every
row that gates a specialist, a team member or a subagent fails, and so does
every row-set row whose lead outcome is a denial or an advisory. The 14 that
pass are the lead's rows (including the lead spelling on a teammate frame),
the frames the gate must not check, the coverage row, and the six row-set rows
where every frame is allowed, which an ungated frame is too. If that revert
ever reports 0 failed, this file has stopped measuring the seam.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fixtures.role_frames import (
    captured_plain_userpromptsubmit,
    captured_pretooluse_lead_inprocess,
    captured_pretooluse_teammate_inprocess_subagent,
    captured_pretooluse_teammate_tmux,
    captured_teammate_sessionstart,
    constructed_pretooluse_teammate_inprocess,
)
from test_pin_caps_gate import REAL_HOOK_ROWS, _pins  # noqa: E402 — sibling harness reuse

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "pin_caps_gate.py"
TEAM = "session-frames"
IN_PROCESS_MEMBER = "fr-backend"
TMUX_MEMBER = "tmux-tester"
LEAD_SESSION = captured_pretooluse_lead_inprocess()["session_id"]
TMUX_SESSION = captured_pretooluse_teammate_tmux()["session_id"]
SOLO_SESSION = captured_teammate_sessionstart()["session_id"]
PLAIN_SESSION = captured_plain_userpromptsubmit()["session_id"]
OTHER_AGENT_SESSION = "c0ffee00-0000-4000-8000-000000000001"
MEMBER_TEXT = "Ask the team-lead to free a pin slot; do not prune pins yourself."
NEW_PIN = "<!-- pinned: 2026-04-21 -->\n### New\nbody\n\n## Working Memory"


@pytest.fixture
def seam(tmp_path):
    """A config root with one team whose lead session has a context file, and a
    project whose CLAUDE.md holds 12 pins."""
    assert len({LEAD_SESSION, TMUX_SESSION, SOLO_SESSION, PLAIN_SESSION, OTHER_AGENT_SESSION}) == 5
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "CLAUDE.md").write_text(_pins(12), encoding="utf-8")
    _write(tmp_path / ".claude" / "teams" / TEAM / "config.json", {
        "leadSessionId": LEAD_SESSION,
        "members": [
            {"name": IN_PROCESS_MEMBER, "agentId": f"{IN_PROCESS_MEMBER}@{TEAM}",
             "agentType": "pact-backend-coder", "backendType": "in-process"},
            {"name": TMUX_MEMBER, "agentId": f"{TMUX_MEMBER}@{TEAM}", "agentType": "pact-test-engineer"},
        ],
    })
    return tmp_path


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _project(root: Path) -> Path:
    return root / "project"


def _context_file(root: Path) -> Path:
    from shared.pact_context import project_slug

    return root / ".claude" / "pact-sessions" / project_slug(str(_project(root))) / LEAD_SESSION / "pact-session-context.json"


def _lead_context(root: Path) -> None:
    _write(_context_file(root), {"session_id": LEAD_SESSION, "project_dir": str(_project(root)), "team_name": TEAM})


def _register(root: Path, session_id: str, member: str) -> None:
    """Append one session-registry line, in the shape `session_registry.register` writes."""
    path = root / ".claude" / "pact-sessions" / ".teammate-registry.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"session_id": session_id, "value": f"{member}@{TEAM}"}) + "\n")


def _env(root: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDE_CONFIG_DIR", "CLAUDE_PROJECT_DIR", "CLAUDE_CODE_SESSION_ID")}
    env.update(HOME=str(root), CLAUDE_CONFIG_DIR=str(root / ".claude"), CLAUDE_PROJECT_DIR=str(_project(root)))
    return env


def _claude_md(root: Path) -> Path:
    return _project(root) / ".claude" / "CLAUDE.md"


def _adding_a_pin(frame: dict, root: Path) -> dict:
    """`frame` carrying a change that adds one pin to the 12-pin file."""
    frame = {k: v for k, v in frame.items() if k != "_meta"}
    target = str(_claude_md(root))
    if frame.get("tool_name") == "Write":
        content = _claude_md(root).read_text(encoding="utf-8").replace("## Working Memory", NEW_PIN, 1)
        frame["tool_input"] = {"file_path": target, "content": content}
    else:
        frame.update(tool_name="Edit", tool_input={"file_path": target, "old_string": "## Working Memory",
                                                   "new_string": NEW_PIN, "replace_all": False})
    frame["cwd"] = str(_project(root))
    return frame


def _run(root: Path, frame: dict):
    """(outcome, text): ("deny", reason), ("advisory", context) or ("allow", None)."""
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(frame), capture_output=True,
                          text=True, timeout=60, env=_env(root), cwd=_project(root))
    if proc.returncode == 2:
        out = json.loads(proc.stdout)["hookSpecificOutput"]
        return "deny", out["permissionDecisionReason"]
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
    out = json.loads(proc.stdout)
    if out == {"suppressOutput": True}:
        return "allow", None
    return "advisory", out["hookSpecificOutput"]["additionalContext"]


def _lead_denial(result):
    outcome, reason = result
    assert outcome == "deny", result
    assert reason.startswith("Pin count cap reached (13/12).") and "/PACT:prune-memory" in reason, reason
    assert MEMBER_TEXT not in reason, reason


def _member_denial(result):
    outcome, reason = result
    assert outcome == "deny", result
    assert reason == f"Pin count cap reached (13/12). {MEMBER_TEXT}", reason


def _subagent(session_id: str = LEAD_SESSION) -> dict:
    frame = captured_pretooluse_teammate_inprocess_subagent()
    frame["session_id"] = session_id
    return frame


def _plain() -> dict:
    frame = {k: v for k, v in captured_plain_userpromptsubmit().items() if k not in ("_meta", "prompt")}
    frame.update(hook_event_name="PreToolUse", tool_name="Edit", tool_use_id="toolu_<synthetic>")
    return frame


def _with(frame: dict, **fields) -> dict:
    frame = dict(frame)
    frame.update(fields)
    return frame


# ---------------------------------------------------------------------------
# Which frames are gated, and the text each sees
# ---------------------------------------------------------------------------

def test_the_lead_is_gated_with_the_lead_text(seam):
    _lead_context(seam)
    _lead_denial(_run(seam, _adding_a_pin(captured_pretooluse_lead_inprocess(), seam)))


def test_a_tmux_specialist_with_no_team_is_gated_with_the_lead_text(seam):
    """A specialist with no team has no team-lead to ask, so its denial is the
    lead's, with no member sentence: the solo branch is not the member branch."""
    assert not _context_file(seam).exists()
    result = _run(seam, _adding_a_pin(captured_pretooluse_teammate_tmux(), seam))
    _lead_denial(result)
    assert MEMBER_TEXT not in (result[1] or "")


def test_a_registered_tmux_teammate_is_gated_and_asks_the_team_lead(seam):
    _register(seam, TMUX_SESSION, TMUX_MEMBER)
    _member_denial(_run(seam, _adding_a_pin(captured_pretooluse_teammate_tmux(), seam)))


def test_an_in_process_subagent_of_the_lead_is_gated_and_asks_the_team_lead(seam):
    _lead_context(seam)
    _member_denial(_run(seam, _adding_a_pin(_subagent(), seam)))


def test_an_in_process_subagent_with_no_team_is_not_gated(seam):
    assert _run(seam, _adding_a_pin(_subagent(), seam)) == ("allow", None)


@pytest.mark.parametrize("tool_name", ["Edit", "Write"])
def test_an_in_process_teammate_is_gated_and_asks_the_team_lead(seam, tool_name):
    _lead_context(seam)
    _member_denial(_run(seam, _adding_a_pin(constructed_pretooluse_teammate_inprocess(tool_name), seam)))


def test_an_in_process_teammate_without_an_agent_id_is_gated(seam):
    _lead_context(seam)
    frame = constructed_pretooluse_teammate_inprocess()
    del frame["agent_id"]
    _member_denial(_run(seam, _adding_a_pin(frame, seam)))


@pytest.mark.parametrize("with_context", [True, False])
def test_a_frame_with_the_lead_spelling_is_gated_with_the_lead_text(seam, with_context):
    if with_context:
        _lead_context(seam)
    frame = _with(constructed_pretooluse_teammate_inprocess(), agent_type="PACT:pact-orchestrator")
    _lead_denial(_run(seam, _adding_a_pin(frame, seam)))


def test_a_plain_session_is_not_gated(seam):
    _lead_context(seam)  # another session's context changes nothing
    assert _run(seam, _adding_a_pin(_plain(), seam)) == ("allow", None)


def test_a_non_pact_agent_session_and_its_subagent_are_not_gated(seam):
    _lead_context(seam)
    primary = _with(captured_pretooluse_teammate_tmux(), agent_type="claude-security:claude-security",
                    session_id=OTHER_AGENT_SESSION)
    assert _run(seam, _adding_a_pin(primary, seam)) == ("allow", None)
    assert _run(seam, _adding_a_pin(_subagent(OTHER_AGENT_SESSION), seam)) == ("allow", None)


def test_a_solo_pact_specialist_session_is_gated_with_the_lead_text(seam):
    frame = _with(captured_pretooluse_teammate_tmux(), agent_type="pact-preparer", session_id=SOLO_SESSION)
    _lead_denial(_run(seam, _adding_a_pin(frame, seam)))


def test_a_subagent_of_a_registered_tmux_teammate_is_gated(seam):
    _register(seam, TMUX_SESSION, TMUX_MEMBER)
    _member_denial(_run(seam, _adding_a_pin(_subagent(TMUX_SESSION), seam)))


def test_a_corrupt_context_file_gates_nothing_and_raises_nothing(seam):
    path = _context_file(seam)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert _run(seam, _adding_a_pin(constructed_pretooluse_teammate_inprocess(), seam)) == ("allow", None)


def test_a_team_read_that_raises_gates_nothing(monkeypatch):
    import shared.background_work as background_work
    from shared.claude_md_manager import gate_frame

    def _raises(_frame):
        raise RuntimeError("team read failed")

    monkeypatch.setattr(background_work, "frame_team_and_name", _raises)
    frame = _adding_a_pin(constructed_pretooluse_teammate_inprocess(), Path("/nonexistent"))
    assert gate_frame(frame) is None
    # A specialist type is still gated, with the lead's text, when the team read fails.
    assert gate_frame(_with(frame, agent_type="pact-backend-coder")) == "specialist"


@pytest.mark.parametrize("frame", [None, "text", ["list"], {"agent_type": ["not", "a", "string"]}])
def test_gate_frame_never_raises_on_a_malformed_frame(frame):
    from shared.claude_md_manager import gate_frame

    assert gate_frame(frame) is None


def test_importing_claude_md_manager_does_not_load_the_team_read():
    """Every hook loads claude_md_manager through the shared package, so
    gate_frame's team read stays inside the function. The control: calling
    gate_frame does load it, so the probe can see the module."""
    probe = (
        "import sys, shared.claude_md_manager as m\n"
        "print('shared.background_work' in sys.modules, 'shared.background_launch' in sys.modules)\n"
        "m.gate_frame({'session_id': 's', 'agent_type': 'fr-backend'})\n"
        "print('shared.background_work' in sys.modules)\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONPATH"] = str(HOOK.parent)
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, env=env, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["False", "False", "True"], result.stdout


# ---------------------------------------------------------------------------
# The same decision and text under every gated frame
# ---------------------------------------------------------------------------

def _row_change(claude_md: Path, before, call, expected):
    """The change and expected outcome for one row of the gate's family rows,
    with the file before written to `claude_md`."""
    if before == "unreadable":
        claude_md.write_text(_pins(3), encoding="utf-8")
        claude_md.chmod(0o000)
    else:
        claude_md.write_text(before, encoding="utf-8")
    tool, tool_input = call
    if expected is None:  # the change is built from the file and adds one pin too
        source = _pins(3) if before == "unreadable" else before
        text = source.replace(tool_input["old_string"], tool_input["new_string"], 1) if tool == "Edit" else tool_input["content"]
        tool, tool_input = "Write", {"content": text.replace("## Working Memory", NEW_PIN, 1)}
    return tool, {"file_path": str(claude_md), **tool_input}


def _frames_to_compare(root: Path):
    return {
        "lead": captured_pretooluse_lead_inprocess(),
        "tmux specialist": captured_pretooluse_teammate_tmux(),
        "in-process subagent": _subagent(),
        "in-process teammate": constructed_pretooluse_teammate_inprocess(),
    }


def _member_form(result, cause_is_count):
    outcome, text = result
    if outcome == "deny" and cause_is_count:
        return outcome, text.split(". ", 1)[0] + ". " + MEMBER_TEXT
    return result


def test_the_row_set_covers_every_family_with_an_allowed_and_a_denied_row():
    families = {}
    for name, _before, _call, expected in REAL_HOOK_ROWS:
        families.setdefault(name.split(":")[0], set()).add("deny" if expected is None else expected)
    for family, outcomes in families.items():
        if family == "not located":
            assert outcomes == {"advisory"}
        else:
            assert {"allow", "deny"} <= outcomes, (family, outcomes)


@pytest.mark.parametrize("name, before, call, expected", REAL_HOOK_ROWS, ids=[row[0] for row in REAL_HOOK_ROWS])
def test_every_gated_frame_gets_the_leads_decision_and_text(seam, name, before, call, expected):
    if before == "unreadable" and os.geteuid() == 0:
        pytest.skip("root reads a mode-000 file, so EACCES cannot be produced")
    _lead_context(seam)
    claude_md = _claude_md(seam)
    results = {}
    try:
        for label, frame in _frames_to_compare(seam).items():
            tool, tool_input = _row_change(claude_md, before, call, expected)
            frame = {k: v for k, v in frame.items() if k != "_meta"}
            frame.update(tool_name=tool, tool_input=tool_input, cwd=str(_project(seam)))
            results[label] = _run(seam, frame)
            claude_md.chmod(0o644)
    finally:
        claude_md.chmod(0o644)
    lead = results["lead"]
    assert lead[0] == ("deny" if expected is None else expected), (name, lead)
    count_denial = lead[0] == "deny" and lead[1].startswith("Pin count cap reached")
    assert results["tmux specialist"] == lead, name  # exactly the lead's: no member sentence
    assert MEMBER_TEXT not in (results["tmux specialist"][1] or ""), name
    for label in ("in-process subagent", "in-process teammate"):
        assert results[label] == _member_form(lead, count_denial), (name, label, results[label])
