"""
Location: pact-plugin/tests/test_lead_address_prose_pins.py
Summary: Prose pins for the lead-address delivery (#1666 item 1): every
teammate spawn literal names `{lead_address}` right after the team sentence and
stays under the dispatch prompt cap at worst-case substitution; the defining
rule section in pact-agent-teams; the Task B template lines; and the no-touch
decisions (pact-teachback carries no `{lead_address}`).
Used by: the full-suite merge gate (`cd pact-plugin && python3 -m pytest -q`).
Design: docs/architecture/session-start-lead-address.md §8.2.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dispatch_gate import PROMPT_MAX_LENGTH
from test_commands_structure import (
    EXPECTED_SPAWN_LITERAL_COUNTS,
    _spawn_prompt_literals,
)

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

SPAWN_FILES = {
    stem: (PLUGIN_ROOT / ("agents" if stem == "pact-orchestrator" else "commands")
           / f"{stem}.md")
    for stem in EXPECTED_SPAWN_LITERAL_COUNTS
}

TEAM_SENTENCE = "You are joining team {team_name}."
LEAD_SENTENCE = "Send messages for your lead to `{lead_address}`."

# Worst-case substitution values (design §8.2 item 2).
_UUID = "4dc22cd5-532e-51e8-aa6a-98c56e1fbeed"
_OTHER_PLACEHOLDER = "x" * 32


def _all_spawn_literals():
    out = []
    for stem, path in SPAWN_FILES.items():
        for literal in _spawn_prompt_literals(path.read_text(encoding="utf-8")):
            out.append((stem, literal))
    return out


# Any brace placeholder, hyphenated names included ({teammate-name},
# {scope-prefixed-name}).
_ANY_PLACEHOLDER = re.compile(r"\{[a-z][a-z_-]*\}")


def _worst_case(literal: str) -> str:
    text = literal.replace('\\"', '"')
    text = text.replace("{team_name}", _UUID).replace("{lead_address}", "team-lead")
    return _ANY_PLACEHOLDER.sub(_OTHER_PLACEHOLDER, text)


def test_census_is_thirteen():
    assert len(_all_spawn_literals()) == sum(EXPECTED_SPAWN_LITERAL_COUNTS.values()) == 13


@pytest.mark.parametrize("stem,literal", _all_spawn_literals())
def test_every_spawn_literal_names_the_lead_address(stem, literal):
    assert f"{TEAM_SENTENCE} {LEAD_SENTENCE}" in literal, (
        f"{stem}: the spawn literal must name `{{lead_address}}` directly "
        "after the team sentence"
    )


@pytest.mark.parametrize("stem,literal", _all_spawn_literals())
def test_every_spawn_literal_fits_the_prompt_cap(stem, literal):
    rendered = _worst_case(literal)
    assert not _ANY_PLACEHOLDER.search(rendered), (stem, "placeholder left unsubstituted")
    assert len(rendered) <= PROMPT_MAX_LENGTH, (stem, len(rendered))


def test_the_rule_section_exists():
    skill = (PLUGIN_ROOT / "skills" / "pact-agent-teams" / "SKILL.md").read_text(
        encoding="utf-8")
    assert "## Your Lead's Address" in skill
    section = skill.split("## Your Lead's Address", 1)[1].split("\n## ", 1)[0]
    for phrase in (
        "`team-lead` in a local session, `main` in a cloud session",
        'including each `SendMessage(to="team-lead", ...)` example',
        "if `team-lead` is unreachable, use `main`",
        "The `from` field on your lead's messages can read `team-lead`",
        "it still reads `{lead_address}`, use `team-lead`",
    ):
        assert phrase in section, phrase
    # The section must sit before the Pre-Response Channel Check.
    assert skill.index("## Your Lead's Address") < skill.index(
        "## Pre-Response Channel Check")


def test_task_b_template_lines_name_the_lead_address():
    orchestrate = SPAWN_FILES["orchestrate"].read_text(encoding="utf-8")
    assert ("(pact-agent-teams On Completion Step 2) to the team-lead at "
            "{lead_address}, ") in orchestrate
    compact = SPAWN_FILES["comPACT"].read_text(encoding="utf-8")
    assert compact.count(
        "STOP and `SendMessage` it to the team-lead at `{lead_address}`.") == 2


def test_placeholder_is_declared_with_its_meaning():
    orchestrator = SPAWN_FILES["pact-orchestrator"].read_text(encoding="utf-8")
    assert "`{lead_address}` is the lead's own `SendMessage` address" in orchestrator
    bootstrap = SPAWN_FILES["bootstrap"].read_text(encoding="utf-8")
    assert "| `{lead_address}` | — | `lead_address` |" in bootstrap
    assert "`{lead_address}` has no CLAUDE.md line" in bootstrap


def test_teachback_skill_is_not_edited_for_the_address():
    """Size-budget decision: pact-teachback has ~58 chars of headroom under
    MAX_SKILL_CHARS, so the rule lives in pact-agent-teams only."""
    teachback = (PLUGIN_ROOT / "skills" / "pact-teachback" / "SKILL.md").read_text(
        encoding="utf-8")
    assert "{lead_address}" not in teachback
    assert "If `team-lead` is unreachable, send the notify to `main`." in teachback
