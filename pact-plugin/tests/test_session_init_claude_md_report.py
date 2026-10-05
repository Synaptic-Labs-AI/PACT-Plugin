"""
Location: pact-plugin/tests/test_session_init_claude_md_report.py
Summary: The per-launch report of a project CLAUDE.md that PACT will not update:
         session_init.check_claude_md_refusals, and the SessionStart message
         that carries it.
Used by: pytest.

Every CLAUDE.md here is written under tmp_path. Rows cover: a clean file and an
absent section say nothing; each refused block or section is named, with the
earliest reason's line; an unreadable or non-UTF-8 file says nothing; and the
message reaches a lead on startup and resume, but not on a compaction and not
a teammate.
"""
import io
import json
from unittest.mock import patch

import pytest

import session_init
from session_init import check_claude_md_refusals
from shared.claude_md_manager import (
    MANAGED_END_MARKER,
    MANAGED_START_MARKER,
    MANAGED_TITLE,
    MEMORY_END_MARKER,
    MEMORY_START_MARKER,
    SESSION_END_MARKER,
    SESSION_START_MARKER,
)

SB = f"{SESSION_START_MARKER}\n## Current Session\n- Resume: `x`\n{SESSION_END_MARKER}\n"
SECTIONS = (
    "## Retrieved Context\n- r1\n\n## Pinned Context\n### pin1\nbody\n\n"
    "## Working Memory\n### 2026-01-01 a\n")
MEMORY_SECTIONS = ("the PACT memory block", "Retrieved Context", "Pinned Context", "Working Memory")


def _managed(memory_body=SECTIONS, session=SB, tail=""):
    return (f"{MANAGED_START_MARKER}\n{MANAGED_TITLE}\n\n{session}\n{MEMORY_START_MARKER}\n"
            f"{memory_body}{MEMORY_END_MARKER}\n\n{MANAGED_END_MARKER}\n{tail}")


@pytest.fixture
def project(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    monkeypatch.chdir(proj)
    return proj


def _write(project, text):
    path = project / ".claude" / "CLAUDE.md"
    path.write_text(text, encoding="utf-8")
    return path


CAP_OFF = ", and the pin cap is not checked until it is fixed."


def _names(report):
    names = report.split("Sections not updated until it is fixed: ", 1)[1]
    return names.removesuffix(CAP_OFF).rstrip(".").split(", ")


@pytest.mark.parametrize("text", [
    _managed(),
    _managed(tail="\nnotes\n```\nopen\n"),  # an unclosed fence below the managed block
    _managed(memory_body="## Working Memory\n### a\n"),  # absent sections are not refusals
    f"# Notes\n\n{SECTIONS}",  # not migrated yet, nothing uncertain
])
def test_a_file_pact_can_update_gives_no_report(project, text):
    _write(project, text)
    assert check_claude_md_refusals() is None


def test_a_memory_block_missing_only_its_end_marker_is_named(project):
    """Deleting only the memory end marker leaves its start unpaired: the report
    names that line and every section of the block."""
    text = _managed().replace(MEMORY_END_MARKER + "\n", "", 1)
    start_line = text.splitlines().index(MEMORY_START_MARKER) + 1
    path = _write(project, text)
    report = check_claude_md_refusals()
    assert report.startswith(f"PACT could not update {path}: ")
    assert f"line {start_line} has no end marker" in report
    assert _names(report) == list(MEMORY_SECTIONS)


def test_an_unclosed_fence_before_the_markers_names_every_block(project):
    path = _write(project, "# Notes\n```\nopen\n" + _managed())
    report = check_claude_md_refusals()
    assert report is not None and report.startswith(f"PACT could not update {path}: line 2 ")
    assert _names(report) == ["the PACT managed block", "Current Session", *MEMORY_SECTIONS]


def test_two_session_blocks_name_only_the_session_block(project):
    _write(project, _managed(session=SB + "\n" + SB))
    report = check_claude_md_refusals()
    assert report is not None and "lines 4, 9" in report
    assert _names(report) == ["Current Session"]


@pytest.mark.parametrize("body, name, line", [
    # the only Pinned heading is commented out
    ("## Retrieved Context\n- r1\n\n<!--\n## Pinned Context\n### pin1\n-->\n\n## Working Memory\n",
     "Pinned Context", "line 14"),
    # two Pinned headings: the cap cannot tell which section it counts
    ("## Pinned Context\n### a\n\n## Pinned Context\n### b\n", "Pinned Context", "lines 10, 13"),
    # the only Working Memory heading is commented out
    ("## Retrieved Context\n- r1\n\n<!--\n## Working Memory\n-->\n", "Working Memory", "line 14"),
])
def test_a_refused_section_inside_the_memory_block_is_named_alone(project, body, name, line):
    _write(project, _managed(memory_body=body))
    report = check_claude_md_refusals()
    assert report is not None and line in report
    assert _names(report) == [name]
    # Only two real Pinned headings turn the cap off without an advisory per edit.
    assert report.endswith(CAP_OFF) == (line == "lines 10, 13")


def test_an_unclosed_fence_inside_the_memory_block_names_the_block_and_its_sections(project):
    _write(project, _managed(memory_body="## Pinned Context\n### a\n```\nopen\n"))
    report = check_claude_md_refusals()
    assert report is not None and "line 12 starts an uncertain region" in report
    assert _names(report) == ["the PACT managed block", *MEMORY_SECTIONS]


def test_the_earliest_reason_is_reported(project):
    # Two Pinned headings on lines 5 and 8 inside a memory block placed above
    # two session blocks (lines 12 and 17): the Pinned reason comes first in
    # the file, though the session block comes first in the check order.
    pinned = "## Pinned Context\n### a\n\n## Pinned Context\n### b\n"
    _write(project, (f"{MANAGED_START_MARKER}\n{MANAGED_TITLE}\n\n{MEMORY_START_MARKER}\n{pinned}"
                     f"{MEMORY_END_MARKER}\n\n{SB}\n{SB}{MANAGED_END_MARKER}\n"))
    report = check_claude_md_refusals()
    assert report is not None and "lines 5, 8" in report and "lines 12, 17" not in report
    assert _names(report) == ["Current Session", "Pinned Context"]


def test_the_report_matches_the_sync_headings_as_the_syncs_do():
    # The report names Retrieved Context or Working Memory exactly when its
    # sync would refuse, so it must match the syncs' own heading rows.
    from scripts import working_memory

    (rc, rc_heading), (wm, wm_heading) = session_init._SYNC_HEADINGS
    assert (rc, wm) == ("Retrieved Context", "Working Memory")
    assert rc_heading.pattern == working_memory._RETRIEVED_CONTEXT_HEADING_ROW.pattern
    assert wm_heading.pattern == working_memory._WORKING_MEMORY_HEADING_ROW.pattern


def test_a_missing_unreadable_or_non_utf8_file_gives_no_report(project):
    assert check_claude_md_refusals() is None
    (project / ".claude" / "CLAUDE.md").write_bytes(b"caf\xe9\n```\nopen\n")
    assert check_claude_md_refusals() is None


# --- the SessionStart message ---------------------------------------------------

LEAD = "PACT:pact-orchestrator"


def _system_message(source, agent_type=LEAD):
    stdin = json.dumps({"session_id": "cccccccc-dddd-eeee-ffff-000000000000",
                        "source": source, "agent_type": agent_type})
    with patch("sys.stdin", io.StringIO(stdin)), \
         patch("sys.stdout", new_callable=io.StringIO) as out:
        with pytest.raises(SystemExit) as exc:
            session_init.main()
    assert exc.value.code == 0
    return json.loads(out.getvalue()).get("systemMessage", "")


@pytest.mark.parametrize("source", ["startup", "resume"])
def test_a_lead_launch_carries_the_report(project, source):
    _write(project, _managed(session=SB + "\n" + SB))
    assert "PACT could not update" in _system_message(source)


def test_a_compaction_and_a_teammate_carry_no_report(project):
    _write(project, _managed(session=SB + "\n" + SB))
    assert "PACT could not update" in _system_message("startup"), "control: the lead gets it"
    assert "PACT could not update" not in _system_message("compact")
    assert "PACT could not update" not in _system_message("startup", "pact-architect")
