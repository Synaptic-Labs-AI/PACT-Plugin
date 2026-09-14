"""Non-mocked L2 seam test for staged compaction attribution.

Runs `python3 hooks/session_init.py`, `hooks/postcompact_archive.py` and
`hooks/bootstrap_gate.py` as child processes over a temporary config root laid
out like the platform's: projects/<slug>/<sid>.jsonl for the lead, and
<sid>/subagents/agent-*.jsonl for an in-process teammate. The order is the
platform's: the compaction hooks run and return first, and only then does the
compacting agent's summary record land in its transcript. The next Read of the
summary file settles it through bootstrap_gate. Nothing is patched.

REVERT CARDINALITY, measured on this file alone: with the settle call removed
from bootstrap_gate's main, both arms fail (2 failed); with postcompact_archive
writing compact-summary.txt directly instead of staging, both arms fail (2
failed); with build_context_cache recording now on a compaction, the teammate
arm fails and the lead arm passes (1 failed).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from fixtures.role_frames import (
    captured_compaction_lead_postcompact,
    captured_compaction_lead_sessionstart,
    captured_compaction_teammate_postcompact,
    captured_compaction_teammate_sessionstart,
)
from shared.compaction_owner import _summary_bodies
from shared.pact_context import project_slug

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
SID = "4ec31948-bbe5-4ef4-841c-631d1ef31e61"
STARTED_AT = "2026-01-01T00:00:00+00:00"


class Session:
    """A temporary home with one lead session, its transcripts and its lead files."""

    def __init__(self, tmp_path):
        self.home = tmp_path
        self.config = tmp_path / ".claude"
        self.project = tmp_path / "cmp-lead"
        self.project.mkdir()
        transcripts = self.config / "projects" / "-cmp-lead"
        subagents = transcripts / SID / "subagents"
        subagents.mkdir(parents=True)
        self.lead = transcripts / f"{SID}.jsonl"
        self.teammate = subagents / "agent-acmp-probe-0123456789abcdef.jsonl"
        for path in (self.lead, self.teammate):
            self._append(path, {"type": "user", "message": {"role": "user", "content": "earlier turn"}})
        self.session_dir = self.config / "pact-sessions" / project_slug(str(self.project)) / SID
        self.session_dir.mkdir(parents=True)
        self.canonical = self.session_dir / "compact-summary.txt"
        self.canonical.write_text("THE LEAD'S OWN SUMMARY", encoding="utf-8")
        self.context = self.session_dir / "pact-session-context.json"
        self.context.write_text(json.dumps({
            "team_name": "session-4ec31948", "session_id": SID, "project_dir": str(self.project),
            "plugin_root": str(HOOKS.parent), "started_at": STARTED_AT,
        }), encoding="utf-8")

    @staticmethod
    def _append(path, record):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def summary_lands(self, path, compact_summary):
        self._append(path, {"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted"})
        self._append(path, {"type": "user", "isCompactSummary": True,
                            "message": {"role": "user", "content": "This session is being continued.\n\nSummary:\n"
                                        + _summary_bodies(compact_summary)[0]}})

    def run(self, hook, frame):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
        env.update(HOME=str(self.home), CLAUDE_CONFIG_DIR=str(self.config),
                   CLAUDE_PROJECT_DIR=str(self.project), CLAUDE_PLUGIN_ROOT=str(HOOKS.parent))
        frame = {**frame, "transcript_path": str(self.lead), "cwd": str(self.project)}
        proc = subprocess.run([sys.executable, str(HOOKS / hook)], input=json.dumps(frame),
                              capture_output=True, text=True, env=env, timeout=60)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    def compact(self, sessionstart, postcompact):
        """The chain in the platform's order: SessionStart(compact), then PostCompact."""
        started = self.run("session_init.py", sessionstart)
        assert self.run("postcompact_archive.py", postcompact) == {"suppressOutput": True}
        return started["hookSpecificOutput"]["additionalContext"]

    def read_summary(self):
        frame = {"hook_event_name": "PreToolUse", "agent_type": "PACT:pact-orchestrator", "session_id": SID,
                 "tool_name": "Read", "tool_input": {"file_path": str(self.canonical)}}
        output = self.run("bootstrap_gate.py", frame)
        assert "permissionDecision" not in json.dumps(output), output

    def staged(self):
        return sorted(self.session_dir.glob("compact-summary.pending-*.json"))

    def attributions(self):
        journal = self.session_dir / "session-journal.jsonl"
        events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
        return [(e["verdict"], e["basis"]) for e in events if e.get("type") == "compaction_attributed"]


def test_a_teammate_compaction_never_replaces_the_lead_summary(tmp_path):
    session = Session(tmp_path)
    post = captured_compaction_teammate_postcompact()

    session.compact(captured_compaction_teammate_sessionstart(), post)

    assert json.loads(session.context.read_text(encoding="utf-8"))["started_at"] == STARTED_AT
    assert session.canonical.read_text(encoding="utf-8") == "THE LEAD'S OWN SUMMARY"
    [pending] = session.staged()
    assert json.loads(pending.read_text(encoding="utf-8"))["summary"] == post["compact_summary"]

    session.summary_lands(session.teammate, post["compact_summary"])
    session.read_summary()

    assert session.canonical.read_text(encoding="utf-8") == "THE LEAD'S OWN SUMMARY"
    [kept] = session.session_dir.glob("compact-summary.teammate-*.txt")
    assert kept.read_text(encoding="utf-8") == post["compact_summary"]
    assert session.staged() == []
    assert session.attributions() == [("teammate", "content")]


def test_a_lead_compaction_reaches_the_summary_file_before_its_read(tmp_path):
    session = Session(tmp_path)
    post = captured_compaction_lead_postcompact()

    session.compact(captured_compaction_lead_sessionstart(), post)
    assert session.canonical.read_text(encoding="utf-8") == "THE LEAD'S OWN SUMMARY"

    session.summary_lands(session.lead, post["compact_summary"])
    session.read_summary()

    assert session.canonical.read_text(encoding="utf-8") == post["compact_summary"]
    assert session.staged() == []
    assert session.attributions() == [("lead", "content")]
