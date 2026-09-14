"""Non-mocked L2 seam test for teammate-compaction attribution.

Runs `python3 hooks/session_init.py` and `python3 hooks/postcompact_archive.py`
as child processes, started together as the platform starts them, over a
temporary config root laid out like the platform's: projects/<slug>/<sid>.jsonl
for the lead, and <sid>/subagents/agent-*.jsonl for an in-process teammate.
Nothing is patched. The boundary and summary records are stamped with the real
clock, and the teammate case waits the real lead guard.

REVERT PROOF, measured: with postcompact_archive.py restored to its base, both
arms fail, the teammate arm on the overwritten summary and the lead arm on the
missing compaction_attributed event (2 failed). With session_init.py restored
to its base, the teammate arm fails on the orchestrator output and the
rewritten lead files (1 failed).
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from fixtures.role_frames import (
    captured_compaction_lead_postcompact,
    captured_compaction_lead_sessionstart,
    captured_compaction_teammate_postcompact,
    captured_compaction_teammate_sessionstart,
)
from shared.compaction_owner import LEAD_GUARD_S, _summary_bodies
from shared.pact_context import project_slug

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
SID = "4ec31948-bbe5-4ef4-841c-631d1ef31e61"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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
            self._append(path, {"type": "user", "timestamp": "2026-01-01T00:00:00.000Z",
                                "message": {"role": "user", "content": "earlier turn"}})
        self.session_dir = self.config / "pact-sessions" / project_slug(str(self.project)) / SID
        self.session_dir.mkdir(parents=True)
        (self.session_dir / "compact-summary.txt").write_text("THE LEAD'S OWN SUMMARY", encoding="utf-8")
        (self.session_dir / "pact-session-context.json").write_text(json.dumps({
            "team_name": "session-4ec31948", "session_id": SID, "project_dir": str(self.project),
            "plugin_root": str(HOOKS.parent), "started_at": "2026-01-01T00:00:00Z",
        }), encoding="utf-8")
        (self.project / "CLAUDE.md").write_text("# Project\n", encoding="utf-8")

    @staticmethod
    def _append(path, record):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def compacted(self, path, compact_summary):
        stamp = _now()
        self._append(path, {"type": "system", "subtype": "compact_boundary",
                            "content": "Conversation compacted", "timestamp": stamp})
        self._append(path, {"type": "user", "isCompactSummary": True, "timestamp": stamp,
                            "message": {"role": "user", "content": "This session is being continued.\n"
                                        + _summary_bodies(compact_summary)[0]}})

    def run_both(self, sessionstart, postcompact):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
        env.update(HOME=str(self.home), CLAUDE_CONFIG_DIR=str(self.config),
                   CLAUDE_PROJECT_DIR=str(self.project), CLAUDE_PLUGIN_ROOT=str(HOOKS.parent))
        started = {}
        procs = {}
        for hook, frame in (("session_init.py", sessionstart), ("postcompact_archive.py", postcompact)):
            frame = {**frame, "transcript_path": str(self.lead), "cwd": str(self.project)}
            started[hook] = time.monotonic()
            procs[hook] = subprocess.Popen([sys.executable, str(HOOKS / hook)], stdin=subprocess.PIPE,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
            procs[hook].stdin.write(json.dumps(frame))
            procs[hook].stdin.close()
        results = {}
        for hook, proc in procs.items():
            out = proc.stdout.read()
            err = proc.stderr.read()
            code = proc.wait(timeout=60)
            results[hook] = (code, out, err, time.monotonic() - started[hook])
        return results

    def snapshot(self):
        files = [p for root in (self.config, self.project) for p in root.rglob("*")
                 if p.is_file() and "projects" not in p.relative_to(self.config if root == self.config else self.project).parts]
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}

    def journal(self):
        return self.session_dir / "session-journal.jsonl"

    def attributions(self):
        if not self.journal().exists():
            return []
        events = [json.loads(line) for line in self.journal().read_text(encoding="utf-8").splitlines()]
        return [(e["verdict"], e["basis"]) for e in events if e.get("type") == "compaction_attributed"]


def test_a_teammate_compaction_leaves_every_lead_file_alone(tmp_path):
    session = Session(tmp_path)
    post = captured_compaction_teammate_postcompact()
    session.compacted(session.teammate, post["compact_summary"])
    before = session.snapshot()

    results = session.run_both(captured_compaction_teammate_sessionstart(), post)

    code, out, err, elapsed = results["session_init.py"]
    assert code == 0, err
    assert json.loads(out) == {"suppressOutput": True}, out[:300]
    assert elapsed >= LEAD_GUARD_S - 0.5, elapsed
    code, out, err, _ = results["postcompact_archive.py"]
    assert code == 0, err
    assert json.loads(out) == {"suppressOutput": True}
    after = session.snapshot()
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    assert changed == [str(session.journal())], changed
    assert session.attributions() == [("teammate", "content")]


def test_a_lead_compaction_writes_and_directs_as_before(tmp_path):
    session = Session(tmp_path)
    post = captured_compaction_lead_postcompact()
    session.compacted(session.lead, post["compact_summary"])

    results = session.run_both(captured_compaction_lead_sessionstart(), post)

    code, out, err, _ = results["postcompact_archive.py"]
    assert code == 0, err
    assert (session.session_dir / "compact-summary.txt").read_text(encoding="utf-8") == post["compact_summary"]
    assert session.attributions() == [("lead", "content")]
    code, out, err, _ = results["session_init.py"]
    assert code == 0, err
    assert "YOUR PACT ROLE: orchestrator" in json.loads(out)["hookSpecificOutput"]["additionalContext"]
