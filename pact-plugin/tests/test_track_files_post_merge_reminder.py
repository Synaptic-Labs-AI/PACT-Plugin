"""
Location: pact-plugin/tests/test_track_files_post_merge_reminder.py
Summary: The lead's post-merge reminder in track_files.py, driven through the
         real hook as a subprocess with stdin JSON. Neither `is_lead` nor the
         merge classifier is mocked.
Used by: pytest. The non-mocked seam arms for the reminder (captured lead frame,
         teammate frame, failure event) live in
         test_track_files_background_integration.py.

Frame shapes are the measured ones: a successful `gh pr merge` prints nothing,
so the firing arms carry an empty stdout, and the incident command is a merge
piped to `tail` and followed by `gh pr view`. A fresh config root holds no
team, no task store and no merge-guard token, so every firing arm here also
proves the reminder fires with no resolvable team.

The isolation arms inject a fault into one job through a wrapper that patches
a module attribute and then runs the hook file as __main__: the job under test
imports its helper inside the function, so the patch reaches it.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from clock_shift.clock_shift_env import carry_clock_shift
from shared.pact_context import LEAD_AGENT_TYPES
from track_files import _POST_MERGE_REMINDER

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
HOOK = HOOKS / "track_files.py"
PEER_REVIEW = Path(__file__).resolve().parents[1] / "commands" / "peer-review.md"

LEAD = "PACT:pact-orchestrator"
MERGE = "gh pr merge 1659 --squash"
INCIDENT = "gh pr merge 1659 --squash 2>&1 | tail -3; gh pr view 1659 --json state,mergedAt"


def _env(root: Path, **extra) -> dict:
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(root),
        "CLAUDE_CONFIG_DIR": str(root),
        "CLAUDE_PROJECT_DIR": "/reminder/project",
    }
    env.update(extra)
    return carry_clock_shift(env)


def _frame(command=MERGE, *, agent_type: str | None = LEAD, event="PostToolUse", response=None, **over):
    frame = {
        "hook_event_name": event,
        "session_id": "reminder-sid",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": (
            {"stdout": "", "stderr": "", "interrupted": False, "isImage": False}
            if response is None else response
        ),
    }
    if agent_type is not None:
        frame["agent_type"] = agent_type
    frame.update(over)
    return frame


def _run(root: Path, frame: dict, *, prelude: str | None = None) -> subprocess.CompletedProcess:
    """Run the real hook. With a prelude, run it in a wrapper that executes the
    prelude first and then the hook file as __main__."""
    if prelude is None:
        argv = [sys.executable, str(HOOK)]
        env = _env(root)
    else:
        code = f"{prelude}\nimport runpy\nrunpy.run_path({str(HOOK)!r}, run_name='__main__')\n"
        argv = [sys.executable, "-c", code]
        env = _env(root, PYTHONPATH=str(HOOKS))
    return subprocess.run(
        argv, input=json.dumps(frame), capture_output=True, text=True, env=env,
        timeout=30, check=False,
    )


def _context(result: subprocess.CompletedProcess) -> str | None:
    """The single additionalContext, or None for suppressOutput. Asserts the
    output is exactly one JSON object and never a decision (N1)."""
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)  # raises on a second object
    # Decision KEYS, not the word: step 8's heading says "merge decision".
    hook_out = out.get("hookSpecificOutput", {})
    for key in ("decision", "permissionDecision"):
        assert key not in out and key not in hook_out, out
    assert out.get("continue") is not False, out
    if out == {"suppressOutput": True}:
        return None
    return out["hookSpecificOutput"]["additionalContext"]


class TestReminderFires:
    @pytest.mark.parametrize("command", [
        pytest.param(MERGE, id="F1-bare-merge-empty-stdout"),
        pytest.param(INCIDENT, id="F2-incident-compound"),
        pytest.param("gh pr merge 1659 --squash --admin", id="F3-admin"),
        pytest.param("gh pr merge 1659 --auto --squash", id="F3-auto"),
    ])
    def test_a_lead_merge_gets_the_reminder(self, tmp_path, command):
        """F1-F4: no team, no task store, no merge-guard token on disk."""
        assert _context(_run(tmp_path, _frame(command))) == _POST_MERGE_REMINDER

    @pytest.mark.parametrize("spelling", sorted(LEAD_AGENT_TYPES))
    def test_every_lead_spelling_fires(self, tmp_path, spelling):
        """F5."""
        assert _context(_run(tmp_path, _frame(agent_type=spelling))) == _POST_MERGE_REMINDER

    def test_the_reminder_names_wrap_up_and_carries_no_issue_number(self):
        assert "`/PACT:wrap-up`" in _POST_MERGE_REMINDER
        assert "#" not in _POST_MERGE_REMINDER


class TestReminderSilent:
    @pytest.mark.parametrize("frame", [
        pytest.param(_frame(agent_type="pact-backend-coder", agent_id="abc"), id="S1-teammate"),
        pytest.param(_frame(agent_type=None), id="S2-no-agent-type"),
        pytest.param(
            _frame(response={"stdout": "", "stderr": "", "interrupted": True}),
            id="S3-interrupted",
        ),
        pytest.param(_frame(response="merged"), id="S4-non-dict-response"),
        pytest.param(_frame(event="PostToolUseFailure"), id="S6-failure-event"),
    ])
    def test_no_reminder(self, tmp_path, frame):
        assert _context(_run(tmp_path, frame)) is None

    @pytest.mark.parametrize("command", [
        "gh pr view 1659", "gh pr list", "gh pr close 1659",
        "git push --force origin feature", "git merge main",
    ])
    def test_a_lead_non_merge_command_gets_no_reminder(self, tmp_path, command):
        """S5."""
        assert _context(_run(tmp_path, _frame(command))) is None

    @pytest.mark.parametrize("command", [
        pytest.param("git commit -m 'docs: explain the gh pr merge 1659 reminder'", id="S9-commit-message"),
        pytest.param("echo 'next: gh pr merge 1659 --squash'", id="S9-echo"),
        pytest.param("grep -n 'gh pr merge' hooks/track_files.py", id="S9-grep"),
    ])
    def test_a_command_that_only_mentions_a_merge_gets_no_reminder(self, tmp_path, command):
        """S9: the operation classifier reads raw text and calls each of these a
        merge; the reminder also requires the guard's danger scan, which reads the
        quoted merge text as inert."""
        assert _context(_run(tmp_path, _frame(command))) is None

    @pytest.mark.parametrize("tool_input", [
        pytest.param({}, id="S7-no-command"),
        pytest.param({"command": ["gh", "pr", "merge", "1659"]}, id="S7-command-not-a-string"),
        pytest.param({"command": ""}, id="S7-empty-command"),
        pytest.param("gh pr merge 1659", id="S7-tool-input-not-a-dict"),
    ])
    def test_a_malformed_tool_input_is_silent(self, tmp_path, tool_input):
        """S7: a lead Bash frame whose command cannot be read stays silent
        and exits 0."""
        assert _context(_run(tmp_path, _frame(tool_input=tool_input))) is None

    def test_an_edit_carrying_merge_text_is_silent(self, tmp_path):
        """S8: only a Bash call is a merge; an Edit whose payload names one
        is file tracking."""
        frame = _frame(tool_name="Edit", tool_input={
            "file_path": str(tmp_path / "notes.md"),
            "old_string": "", "new_string": "gh pr merge 1659 --squash",
            "command": "gh pr merge 1659 --squash",
        }, tool_response={"filePath": str(tmp_path / "notes.md")})
        assert _context(_run(tmp_path, frame)) is None


class TestReminderWording:
    def test_a_merge_that_failed_inside_an_exit_0_command_still_gets_conditional_text(self, tmp_path):
        """F6: the incident shape when the merge leg failed. The whole command
        exits 0 (tail and gh pr view succeed), so PostToolUse fires and the
        reminder fires too. Its first words make it safe to read after a failed
        merge."""
        frame = _frame(INCIDENT, response={
            "stdout": "X Pull request #1659 is not mergeable: the base branch policy "
                      "prohibits the merge.\nOPEN", "stderr": "", "interrupted": False})
        text = _context(_run(tmp_path, frame))
        assert text == _POST_MERGE_REMINDER
        assert text.startswith("If the merge succeeded:"), text

    def test_the_reminder_routes_a_peer_review_lead_to_its_pr_state_check(self):
        """Inside /PACT:peer-review the lead continues at that command's PR-state
        check (which invokes wrap-up on MERGED), not a bare wrap-up that skips it.
        The other branch is named by place, never "otherwise", which reads as the
        else of "If the merge succeeded" and so as wrap-up after a failed merge,
        and it waits for the merge, because a queued `--auto` merge exits 0."""
        assert "inside /PACT:peer-review, continue at its step 8" in _POST_MERGE_REMINDER
        assert (
            "outside /PACT:peer-review, invoke `/PACT:wrap-up` once the PR has merged"
            in _POST_MERGE_REMINDER
        )
        assert "otherwise" not in _POST_MERGE_REMINDER

    def test_the_reminder_names_peer_review_step_8_by_its_heading(self):
        """The reminder sends the lead to peer-review's step 8 by name, so the
        name must be that step's heading as the command file spells it."""
        text = PEER_REVIEW.read_text(encoding="utf-8")
        headings = re.findall(r"(?m)^8\. \*\*(.+?)\*\*", text)
        assert len(headings) == 1, headings
        assert f'step 8, "{headings[0].rstrip(".")}"' in _POST_MERGE_REMINDER

    def test_an_api_merge_is_a_merge(self, tmp_path):
        """F7: the classifier reads the REST merge endpoint as a merge, so the
        reminder follows it as it follows `gh pr merge`."""
        frame = _frame("gh api -X PUT repos/o/r/pulls/1659/merge")
        assert _context(_run(tmp_path, frame)) == _POST_MERGE_REMINDER


PIN_REPORT = (
    "import shared.claude_md_drift as d\n"
    "d.report_after_bash = lambda frame: 'PIN REPORT'\n"
)
PIN_RAISES = (
    "import shared.claude_md_drift as d\n"
    "def boom(frame):\n    raise RuntimeError('pin job fault')\n"
    "d.report_after_bash = boom\n"
)
REMINDER_RAISES = (
    "import shared.merge_guard_common as m\n"
    "def boom(command):\n    raise RuntimeError('reminder fault')\n"
    "m.detect_command_operation_type = boom\n"
)


class TestReminderJoinsAndIsolates:
    def test_both_reports_join_into_one_additional_context(self, tmp_path):
        """Constraint (a): one JSON object, the reminder after the pin report."""
        result = _run(tmp_path, _frame(), prelude=PIN_REPORT)
        assert _context(result) == "PIN REPORT\n\n" + _POST_MERGE_REMINDER

    def test_a_reminder_fault_keeps_the_pin_report(self, tmp_path):
        """N2."""
        result = _run(tmp_path, _frame(), prelude=PIN_REPORT + REMINDER_RAISES)
        assert _context(result) == "PIN REPORT"

    def test_a_pin_report_fault_keeps_the_reminder(self, tmp_path):
        result = _run(tmp_path, _frame(), prelude=PIN_RAISES)
        assert _context(result) == _POST_MERGE_REMINDER
