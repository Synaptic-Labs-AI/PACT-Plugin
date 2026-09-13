"""The `covers_since` rule where agents read and copy it, and what the lead is
told about a wait that lacks the field.

Location: pact-plugin/tests/test_covers_since_instruction_pins.py
Summary: pins that every wait SET writes `covers_since` (equal to `since` on a
         SET that starts a wait, carried forward unchanged on a re-SET) at each
         site an agent reads or copies it from, and pins that the lead-facing
         surface for a missing anchor names every route to it and asserts none.
Used by: the suite. No module-level sys.path.insert — path setup is
         conftest-owned; see tests/test_path_setup_pin.py.

EVERY SITE, AND COUNTS WHERE A FILE HAS SEVERAL. An agent copies the wait from
whichever template it is reading. A template that drops the field produces
waits with no anchor however correct the rule table is, so each template is
pinned in its own file, and a file carrying a template at several sites is
pinned at that many: a presence check stays green with one of them reverted.

PRESENCE PINS, NORMALISED. Backticks are stripped and whitespace runs collapsed
on both sides, so a re-wrap or an inline-code change does not fail a pin and a
re-word does.

A CORRECT FIRST SET HAS NOTHING TO REPORT. A wait written as instructed carries
an anchor, so the lead-side scan stays silent about it. A wait without one can
come from routes the scan cannot tell apart, so its surface names them all.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import missed_wake_scan as mw
from fixtures.role_frames import captured_lead_userpromptsubmit_qualified

PLUGIN = Path(__file__).resolve().parents[1]
AGENT_TEAMS = "skills/pact-agent-teams/SKILL.md"
TEACHBACK = "skills/pact-teachback/SKILL.md"
ORCHESTRATOR = "agents/pact-orchestrator.md"
COMPACT = "commands/comPACT.md"
ORCHESTRATE = "commands/orchestrate.md"
COMPLETION = "protocols/pact-completion-authority.md"
PROTOCOLS = "protocols/pact-protocols.md"

TEMPLATE_JSON = '"covers_since": "<the same value as since>"'
TEMPLATE_INLINE = "covers_since=<the same value as since>"
REVISION_LOOP = "re-SETs `intentional_wait` with a fresh `since` and a `covers_since` equal to it."


def _phrase(text: str) -> str:
    return " ".join(text.replace("`", "").split())


# (file, phrase, minimum occurrences). A minimum above 1 is a file that carries
# the same template at that many sites.
PINS = [
    # The rule table row.
    (AGENT_TEAMS, "| `covers_since` | on every SET |", 1),
    (AGENT_TEAMS, "(the first SET, or any SET after a CLEAR), write the same value as `since`", 1),
    (AGENT_TEAMS, "write `covers_since` again with its existing value in the same `TaskUpdate`", 1),
    (AGENT_TEAMS, "leaving the field out deletes it", 1),
    (AGENT_TEAMS, "If it is already missing, write the value `since` held BEFORE you overwrite it.", 1),
    # The staleness paragraph.
    (AGENT_TEAMS, "give it a fresh `since` and carry `covers_since` forward unchanged in the same write.", 1),
    (AGENT_TEAMS, "a write that omits the field deletes it.", 1),
    (AGENT_TEAMS, "A wait you SET after a CLEAR is a new wait: write `covers_since` equal to its new `since`.", 1),
    # Templates an agent copies.
    (AGENT_TEAMS, TEMPLATE_JSON, 1),
    (AGENT_TEAMS, TEMPLATE_INLINE, 2),
    (AGENT_TEAMS, "expected_resolver, since, covers_since}` per the SET subsection", 1),
    (AGENT_TEAMS, '"covers_since": now,', 1),
    (TEACHBACK, TEMPLATE_JSON, 2),
    (COMPACT, TEMPLATE_INLINE, 1),
    (ORCHESTRATE, TEMPLATE_INLINE, 1),
    # The revision loop.
    (COMPLETION, REVISION_LOOP, 1),
    (PROTOCOLS, REVISION_LOOP, 1),
    # What the lead is told about a teammate's wait.
    (ORCHESTRATOR, "Fields: `reason`, `expected_resolver`, `since`, `covers_since`.", 1),
    (ORCHESTRATOR, "re-SET with a fresh `since` and `covers_since` carried forward unchanged", 1),
    (ORCHESTRATOR, "A re-SET that moves or drops `covers_since` has widened what the wait covers", 1),
]


@pytest.mark.parametrize(
    "rel, phrase, minimum", PINS,
    ids=[f"{i:02d}:{Path(r).parent.name if r.endswith('SKILL.md') else Path(r).stem}:{_phrase(p)[:36]}"
         for i, (r, p, _) in enumerate(PINS)],
)
def test_the_every_SET_rule_is_stated_where_agents_read_it(rel, phrase, minimum):
    found = _phrase((PLUGIN / rel).read_text(encoding="utf-8")).count(_phrase(phrase))
    assert found >= minimum, (
        f"{rel}: {phrase!r} appears {found} time(s), expected at least {minimum}. "
        "An agent copies its wait from whichever site it is reading, so a site "
        "that states the older rule or drops the field produces waits with no "
        "anchor. If the wording changed on purpose, update this pin with it."
    )


LEAD_TEAM = "covers-since-team"


def _ago(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


class TestTheMissingAnchorSurfaceAssertsNoRoute:
    """A wait with no anchor may predate the field, may come from an older
    instruction or a template that omits it, or may have lost the field on a
    re-SET. The scan sees the same absence in every case, so the lead is told
    all of them and none is asserted."""

    def test_the_ABSENT_explanation_names_every_route_and_asserts_none(self):
        task = {"id": "7", "owner": "alice", "status": "in_progress",
                "metadata": {"intentional_wait": {"reason": "awaiting_blocker_resolution",
                                                  "expected_resolver": "peer",
                                                  "since": _ago(90)}}}
        surface = _phrase(mw.build_unanchored_surface([(task, "absent")]) or "")
        for route in ("predates the field",
                      "was written under the earlier instruction or from a template that omits it",
                      "an agent dropped it on a re-SET"):
            assert route in surface, (
                f"the missing-anchor surface no longer names the route {route!r}; "
                f"a lead told fewer routes than exist will act on the wrong one: {surface!r}"
            )
        assert "the scan cannot tell which" in surface, surface
        assert "no teammate is at fault" not in surface, (
            "the surface asserts that no teammate is at fault. A wait missing its "
            "anchor can be an agent dropping the field on a re-SET, so that claim "
            f"is one the scan cannot make: {surface!r}"
        )


@pytest.fixture
def lead_scan(tmp_path, monkeypatch):
    """Run `run_surface` as a lead over one wait, with a real registry record
    launched 100 minutes ago that the wait, SET 90 minutes ago, covers."""
    import importlib

    from shared import pact_context

    # background_work copies pact_context.get_team_name when it is first
    # imported. Load it before the patch below, so a copy taken while the
    # patch is active cannot outlive this test.
    importlib.import_module("shared.background_work")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    team_dir = tmp_path / "teams" / LEAD_TEAM
    team_dir.mkdir(parents=True)
    (team_dir / "background_work.json").write_text(json.dumps({"records": [{
        "agent_name": "alice", "session_id": "s", "task_ids": ["7"],
        "registered_at": _ago(100)}]}), encoding="utf-8")
    monkeypatch.setattr(pact_context, "get_team_name", lambda: LEAD_TEAM)
    monkeypatch.setattr(mw, "read_events", lambda et: [])
    monkeypatch.setattr(mw, "append_event", lambda e: True)
    monkeypatch.setattr(mw, "get_journal_path", lambda: str(tmp_path / "journal.jsonl"))

    def run(anchored: bool) -> str:
        since = _ago(90)
        wait = {"reason": "awaiting_blocker_resolution", "expected_resolver": "peer",
                "since": since}
        if anchored:
            wait["covers_since"] = since
        task = {"id": "7", "owner": "alice", "subject": "s", "status": "in_progress",
                "metadata": {"intentional_wait": wait}}
        monkeypatch.setattr(mw, "get_task_list", lambda: [task])
        return mw.run_surface(captured_lead_userpromptsubmit_qualified()) or ""

    return run


class TestAFirstSETWrittenAsInstructedIsNotReported:

    def test_a_first_SET_carrying_covers_since_equal_to_since_raises_no_missing_anchor_surface(
        self, lead_scan
    ):
        out = lead_scan(anchored=True)
        assert "FALLBACK ANCHOR" not in out, (
            "a wait SET exactly as instructed, with `covers_since` equal to `since`, "
            f"was reported to the lead as missing its anchor: {out!r}"
        )

    def test_the_same_wait_WITHOUT_covers_since_is_reported(self, lead_scan):
        """The control for the arm above: the same setup reaches the surface, so
        that arm's silence comes from the anchor and not from a setup that
        could never report anything."""
        out = lead_scan(anchored=False)
        assert "FALLBACK ANCHOR" in out, (
            f"a covered wait with no `covers_since` did not reach the lead: {out!r}"
        )
