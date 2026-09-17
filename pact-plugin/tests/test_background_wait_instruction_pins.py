"""Background-wait instructions outside the orchestrator persona.

Location: pact-plugin/tests/test_background_wait_instruction_pins.py
Summary: pins the text that tells a teammate to flag a wait after backgrounding
         work, where a consultant flags it, which lead turns the missed-wake
         scan runs on, and what tmux teammate mode does not cover. The
         orchestrator's own rules are pinned section by section in
         test_wait_discipline_pins.py.
Used by: the suite. No module-level sys.path.insert — path setup is
         conftest-owned; see tests/test_path_setup_pin.py.

PRESENCE PINS, NORMALISED as in that file: backticks stripped and whitespace
collapsed on both sides, so a re-wrap passes and a re-word fails.

THE STARTUP NOTICE IS READ FROM THE EVALUATED CONSTANT. It is built from
adjacent string literals, so a phrase can be split across two of them, and no
search of the source text finds it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import session_init

PLUGIN = Path(__file__).resolve().parents[1]
TEACHBACK = "skills/pact-teachback/SKILL.md"
AGENT_TEAMS = "skills/pact-agent-teams/SKILL.md"


def _phrase(text: str) -> str:
    return " ".join(text.replace("`", "").split())


PINS = [
    (TEACHBACK, "If you background work, flag the wait before you end the turn."),
    (TEACHBACK, "or a command a shell `&` sends to the background"),
    (AGENT_TEAMS, "If you background work as a consultant, SET the wait on your most recently completed task."),
    (AGENT_TEAMS, "not on a turn opened by a teammate message"),
]


@pytest.mark.parametrize(
    "rel, phrase", PINS,
    ids=[f"{Path(r).parent.name}:{_phrase(p)[:40]}" for r, p in PINS],
)
def test_the_background_wait_instruction_is_where_the_agent_reads_it(rel, phrase):
    assert _phrase(phrase) in _phrase((PLUGIN / rel).read_text(encoding="utf-8")), (
        f"{rel}: {phrase!r} is missing. An agent acts on this instruction at the "
        "moment it backgrounds work or idles; if the wording changed on purpose, "
        "update this pin with it."
    )


def test_the_in_process_notice_says_tmux_does_not_cover_an_unwatched_background_job():
    notice = _phrase(session_init._INPROCESS_MODE_NOTICE)
    claim = ("`--teammate-mode tmux` makes teammate wake delivery reliable; it does "
             "NOT cover a background job that finishes with nobody watching")
    assert _phrase(claim) in notice, (
        "the in-process startup notice no longer says what tmux mode does not "
        f"cover, so a lead may relaunch in tmux and expect background jobs to wake it: {notice!r}"
    )
