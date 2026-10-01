"""What an unknown frame keeps doing, and what it must not emit in a PACT project.

Sibling of ``test_session_init_unknown_frame_is_inert.py``, which pins an
unknown frame's output by EXACT EQUALITY in an EMPTY sandbox. An empty sandbox
triggers none of the state-gated emissions a lead gets (the additionalDirectories
tip needs a settings.json; the pin advisories need a project CLAUDE.md holding
pins; task resumption needs tasks in the session's store; the last-session
snapshot needs a previous session's journal; the backlog block needs a backlog
record), so a leak of one of them into ``_unknown_frame_output`` passes there.
The arm below pins the same exact output in a sandbox seeded to trigger each of
them, and lead controls on identical copies prove every seed fires: one with a
session id for the team-scoped seeds, and one with none, whose resumption reads
the CLAUDE_CODE_TASK_LIST_ID store the unknown frame would read.

It also pins the one kept upkeep item that module leaves unobserved: orphan
merge-authorization tokens are reaped for an unknown frame too.
"""
import io
import json
import os
import re
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import session_init  # noqa: E402
from session_init import _UNKNOWN_FRAME_CONTEXT, _UNKNOWN_ROLE_NOTICE  # noqa: E402
from shared import pact_context  # noqa: E402
from shared.merge_guard_common import ORPHAN_TOKEN_MAX_AGE_SECONDS, TOKEN_PREFIX  # noqa: E402
from shared.paths import get_claude_config_dir  # noqa: E402

LEAD = {"agent_type": "PACT:pact-orchestrator"}
# Distinct ids: a lead's persisted context under the same project and session
# id would recover the unknown frame as a resumed lead.
_TEMPLATE_SID = "00000000-aaaa-bbbb-cccc-000000000000"
_LEAD_SID = "11111111-aaaa-bbbb-cccc-000000000000"
_PLAIN_SID = "22222222-aaaa-bbbb-cccc-000000000000"
_TASK_LIST_ID = "plain-persistent-list"

# Three pins dated well past PINNED_STALENESS_DAYS. Two already carry the STALE
# marker, which is what the stale-block directive counts, so it can fire without
# the staleness writer running first; the unmarked one is what the writer marks.
_STALE_MARKER = "<!-- STALE: Last relevant"
_STALE_PINS = (
    "<!-- pinned: 2025-01-01 -->\n### Old pin A 2025-01-01\n"
    f"{_STALE_MARKER} 2025-01-01 -->\nBody A.\n\n"
    "<!-- pinned: 2025-01-02 -->\n### Old pin B 2025-01-02\n"
    f"{_STALE_MARKER} 2025-01-02 -->\nBody B.\n\n"
    "<!-- pinned: 2025-01-03 -->\n### Old pin C 2025-01-03\nBody C.\n\n"
)

# Each (channel, marker) a lead emits on the seeded layout and an unknown frame
# must not.
SEEDED = [
    ("systemMessage", "to `additionalDirectories`"),
    ("systemMessage", "in-process teammate mode"),
    ("additionalContext", "stale pin(s) detected"),
    ("additionalContext", "Pin slots:"),
    ("additionalContext", "/PACT:prune-memory"),
    ("additionalContext", "PACT plugin:"),
    ("additionalContext", "PACT Runtime Config"),
    # The lead's own team store. The task-list store the plain arm would read
    # is proven by its own control below.
    ("additionalContext", "Resumption context: Features: Ship the feature"),
    ("additionalContext", "Previous session summary"),
    ("additionalContext", "SEEDED BACKLOG ITEM"),
]

_IN_PROGRESS_TASK = {"id": "1", "status": "in_progress"}
_BACKLOG_ITEM = {
    "id": "a1b2", "title": "SEEDED BACKLOG ITEM", "status": "active", "rank": 1,
    "blocked_by": [], "batch_with": [], "ref": None, "plan": None, "memory": [],
    "note": "", "added": "2026-09-01", "touched": "2026-09-01",
}


def _run(monkeypatch, project, session_id, source="startup", **frame):
    """Drive ``session_init.main()`` with nothing stubbed and return the output.

    Each start gets the fresh session-context state a real hook process has,
    so one start's cached team or session cannot leak into the next.
    """
    pact_context.reset_for_tests()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.chdir(project)
    stdin_data = json.dumps({"session_id": session_id, "source": source, **frame})
    with patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc:
            session_init.main()
    assert exc.value.code == 0
    return json.loads(mock_stdout.getvalue())


def _channel(output, channel):
    if channel == "systemMessage":
        return output.get("systemMessage", "")
    return output["hookSpecificOutput"].get("additionalContext", "")


def _pact_project_claude_md(monkeypatch, tmp_path):
    """The managed CLAUDE.md a real lead start writes, with stale pins added.

    Its Current Session block names the template start's session dir, which
    every copy reads as the previous session, so a completed phase is appended
    to that session's journal for the snapshot to summarise.
    """
    template = tmp_path / "template"
    template.mkdir()
    _run(monkeypatch, template, _TEMPLATE_SID, **LEAD)
    text = (template / ".claude" / "CLAUDE.md").read_text()
    assert "## Pinned Context\n" in text, "the managed template has no Pinned Context section"
    previous = Path(re.search(r"- Session dir:\s*`([^`]+)`", text).group(1))
    with (previous / "session-journal.jsonl").open("a", encoding="utf-8") as journal:
        journal.write(json.dumps({
            "v": 1, "type": "phase_transition", "phase": "CODE",
            "status": "completed", "ts": "2026-09-01T00:00:00Z",
        }) + "\n")
    return text.replace("## Pinned Context\n", "## Pinned Context\n\n" + _STALE_PINS, 1)


def _seed_tasks(directory, subject):
    directory.mkdir(parents=True)
    (directory / "1.json").write_text(json.dumps({**_IN_PROGRESS_TASK, "subject": subject}))


def _project(tmp_path, name, claude_md):
    project = tmp_path / name
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "CLAUDE.md").write_text(claude_md)
    return project


class TestUnknownFrameOutputIsExactInAPactProject:

    @pytest.mark.parametrize("source", ["startup", "resume"])
    def test_no_seeded_emission_reaches_an_unknown_frame(self, source, monkeypatch, tmp_path):
        config = get_claude_config_dir()
        config.mkdir(parents=True, exist_ok=True)
        (config / "settings.json").write_text(
            json.dumps({"permissions": {"additionalDirectories": []}})
        )
        claude_md = _pact_project_claude_md(monkeypatch, tmp_path)
        # A lead reads its team's store. A session with no team context reads
        # the store CLAUDE_CODE_TASK_LIST_ID names, which is how a plain session
        # keeps a persistent task list.
        _seed_tasks(config / "tasks" / f"session-{_LEAD_SID[:8]}", "Ship the feature")
        monkeypatch.setenv("CLAUDE_CODE_TASK_LIST_ID", _TASK_LIST_ID)
        _seed_tasks(config / "tasks" / _TASK_LIST_ID, "Keep the list")
        control = _project(tmp_path, "control", claude_md)
        plain = _project(tmp_path, "plain", claude_md)
        backlog = Path.home() / ".claude" / "pact-backlog"
        backlog.mkdir(parents=True, exist_ok=True)
        (backlog / "demo.json").write_text(json.dumps({
            "version": 1, "project": "demo", "project_path": str(control),
            "roots": [str(control), str(plain)], "updated": "2026-09-01T00:00:00Z",
            "items": [_BACKLOG_ITEM],
        }))

        lead_output = _run(monkeypatch, control, _LEAD_SID, source, **LEAD)
        for channel, marker in SEEDED:
            assert marker in _channel(lead_output, channel), (
                f"control: {marker!r} did not reach a lead's {channel}, so this "
                "layout does not seed that emission"
            )
        assert (control / ".claude" / "CLAUDE.md").read_text().count(_STALE_MARKER) == 3, (
            "control: a lead start did not mark the unmarked stale pin, so this "
            "layout never reaches the staleness writer"
        )
        # With no session id no team context is built, so task resumption
        # falls back to the CLAUDE_CODE_TASK_LIST_ID store
        # (task_utils.get_task_list), the store a leak into the unknown frame
        # would read.
        no_team = _project(tmp_path, "control-no-team", claude_md)
        no_team_output = _run(monkeypatch, no_team, None, source, **LEAD)
        assert "Resumption context: Features: Keep the list" in _channel(
            no_team_output, "additionalContext"
        ), (
            "control: resumption with no team context did not read the "
            "task-list store, so that seed proves nothing"
        )

        output = _run(monkeypatch, plain, _PLAIN_SID, source)
        assert output["hookSpecificOutput"] == {
            "hookEventName": "SessionStart",
            "additionalContext": _UNKNOWN_FRAME_CONTEXT,
        }
        assert output.get("systemMessage") == _UNKNOWN_ROLE_NOTICE
        assert (plain / ".claude" / "CLAUDE.md").read_text() == claude_md, (
            "an unknown frame rewrote a PACT project's CLAUDE.md"
        )


class TestUnknownFrameReapsOrphanTokens:

    def test_an_expired_token_is_reaped_and_a_fresh_one_kept(self, monkeypatch, tmp_path):
        # TOKEN_DIR is bound to the config dir at import, so point it here.
        token_dir = tmp_path / "tokens"
        token_dir.mkdir()
        monkeypatch.setattr(session_init, "TOKEN_DIR", token_dir)
        expired = token_dir / f"{TOKEN_PREFIX}expired"
        fresh = token_dir / f"{TOKEN_PREFIX}fresh"
        expired.write_text("")
        fresh.write_text("")
        old = time.time() - ORPHAN_TOKEN_MAX_AGE_SECONDS - 60
        os.utime(expired, (old, old))

        plain = tmp_path / "plain"
        plain.mkdir()
        output = _run(monkeypatch, plain, _PLAIN_SID)

        assert output["hookSpecificOutput"]["additionalContext"] == _UNKNOWN_FRAME_CONTEXT
        assert not expired.exists(), "an unknown frame left an expired merge token on disk"
        assert fresh.exists(), "an unknown frame reaped a merge token that has not expired"
