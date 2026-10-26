"""
Location: pact-plugin/tests/test_cloud_roster_gap_pins.py
Summary: Pins for guards that the #1670 final test review (task #109) found
had no failing arm under mutation:
  - MA1: state_file.append_line never crosses its cap, even when a second
    writer lands between the size check and the write (flock).
  - MA4: subagent_metadata reads meta.json only from under
    <config>/projects (mutant: containment root "/" stayed green).
  - MA5: cloud_roster.applies refuses an unsafe team name (mutant: the
    SAFE_ID check removed stayed green), and SAFE_ID refuses a trailing
    newline, which `^...$` with .match used to accept.
Used by: the full-suite merge gate (`cd pact-plugin && python3 -m pytest -q`).
"""

from __future__ import annotations

import json
import os

from fixtures.actor_frames import SUBAGENT_ID, cloud_meta
from shared import cloud_roster, state_file, subagent_meta
from shared.paths import get_claude_config_dir


# ─── MA1: the append cap holds against a concurrent writer ───────────────────


def test_concurrent_appends_never_cross_the_cap(tmp_path, monkeypatch):
    """A second writer tries to append between the first one's size check
    and its write (simulated from inside os.write). Without serialization
    both fit the check and the file ends at 109 bytes against a 100-byte cap,
    which makes the roster unloadable. With the append lock the second
    writer is refused while the first holds it, and the file ends exactly at
    the cap."""
    # raising=False: without the lock (the regression) the constant does not
    # exist, and the test must fail on the size assert, not on the setup.
    monkeypatch.setattr(state_file, "_APPEND_LOCK_TIMEOUT_SECONDS", 0.05,
                        raising=False)
    path = tmp_path / "r.jsonl"
    path.write_bytes(b"x" * 90 + b"\n")
    record = b"y" * 8 + b"\n"   # 91 + 9 == 100: either append alone fits
    real_write, inner = os.write, []

    def racing_write(fd, data):
        if not inner:
            try:
                inner.append(state_file.append_line(
                    path, record, tmp_path, max_file_bytes=100))
            except OSError as error:   # TimeoutError: the lock is held
                inner.append(error)
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", racing_write)
    outer = state_file.append_line(path, record, tmp_path, max_file_bytes=100)
    monkeypatch.setattr(os, "write", real_write)
    assert inner, "the racing writer never ran"
    assert isinstance(inner[0], OSError), f"inner writer was not refused: {inner[0]!r}"
    assert outer is True
    assert path.stat().st_size == 100


# ─── MA4: meta.json is read only from under <config>/projects ────────────────


def test_meta_outside_projects_root_is_not_read(tmp_path, pact_context):
    pact_context(team_name="pact-test", session_id="test-session")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / f"agent-{SUBAGENT_ID}.meta.json").write_text(
        json.dumps(cloud_meta("secretary", "pact-secretary")), encoding="utf-8")
    frame = {"agent_id": SUBAGENT_ID,
             "agent_transcript_path": str(outside / "agent.jsonl")}
    assert subagent_meta.subagent_metadata(frame) == {}


def test_meta_inside_projects_root_is_read(tmp_path, pact_context):
    """Control for the test above: the same file under <config>/projects
    is read, so the refusal above is the containment check, not a miss."""
    pact_context(team_name="pact-test", session_id="test-session")
    inside = get_claude_config_dir() / "projects" / "-proj" / "x"
    inside.mkdir(parents=True)
    (inside / f"agent-{SUBAGENT_ID}.meta.json").write_text(
        json.dumps(cloud_meta("secretary", "pact-secretary")), encoding="utf-8")
    frame = {"agent_id": SUBAGENT_ID,
             "agent_transcript_path": str(inside / "agent.jsonl")}
    assert subagent_meta.subagent_metadata(frame)["name"] == "secretary"


# ─── MA5: safe ids and team names ────────────────────────────────────────────


def test_agent_id_with_a_trailing_newline_reads_no_meta(pact_context):
    """`^...$` with .match accepts "id\\n" ($ matches before a final
    newline). Checked by behaviour, so it holds however the id check is
    written: a meta file whose name carries the newline is still not read."""
    pact_context(team_name="pact-test", session_id="test-session")
    inside = get_claude_config_dir() / "projects" / "-proj" / "x"
    inside.mkdir(parents=True)
    bad_id = SUBAGENT_ID + "\n"
    (inside / f"agent-{bad_id}.meta.json").write_text(
        json.dumps(cloud_meta("secretary", "pact-secretary")), encoding="utf-8")
    frame = {"agent_id": bad_id,
             "agent_transcript_path": str(inside / "agent.jsonl")}
    assert subagent_meta.subagent_metadata(frame) == {}


def test_applies_refuses_unsafe_team_names(pact_context):
    pact_context(team_name="pact-test", session_id="test-session")
    config = get_claude_config_dir()
    (config / "teams" / "ok").mkdir(parents=True, exist_ok=True)
    (config / "x").mkdir(exist_ok=True)     # teams/../x exists, no config.json
    (config / "teams" / "ok\n").mkdir(exist_ok=True)
    assert cloud_roster.applies("ok")
    assert not cloud_roster.applies("../x")
    assert not cloud_roster.applies("ok/")
    assert not cloud_roster.applies("ok\n")
