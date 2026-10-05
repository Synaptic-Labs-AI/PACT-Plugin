"""
Location: pact-plugin/tests/test_claude_md_corpus_writers.py
Summary: Every corpus file through the readers and writers, not only the
         finder: the Current Session block's planner and reader, the pin-marker
         planner, the legacy kernel strip's planner and the one Pinned locator.
Used by: pytest.

The expected outcome of each call is read from the corpus's hand-written block
states (tests/fixtures/claude_md_corpus/expected.json), never computed by the
parser: a block the table calls UNKNOWN, DUPLICATE or MALFORMED is refused with
the line named, and a reader returns nothing from it. Every write is checked
after the fact: the target block reads back FOUND once, only that block and
blank separator rows changed, every other row keeps its bytes, its kind and its
hidden flag, and a second plan writes nothing.
"""

import json
import re
from pathlib import Path

import pytest

from shared.claude_md_manager import SESSION_END_MARKER, SESSION_START_MARKER, _plan_kernel_strip
from shared.claude_md_markers import State, parse
from shared.pin_markers import Refusal, SkipReason, plan_insertion
from shared.session_resume import _plan_session_block, _session_block_text
from shared.stale_session import recorded_session_id
from staleness import locate_pinned

_CORPUS = Path(__file__).parent / "fixtures" / "claude_md_corpus"
_EXPECTED = json.loads((_CORPUS / "expected.json").read_text(encoding="utf-8"))
_CASES = sorted(_EXPECTED)
_UNCERTAIN = {"UNKNOWN", "DUPLICATE", "MALFORMED"}
_NAMES_A_LINE = re.compile(r"\blines? \d")
KERNEL_START, KERNEL_END = "<!-- PACT_START:", "<!-- PACT_END -->"
SID = "0123abcd-0000-4000-8000-00000000000a"
STARTED = "2026-01-02 03:04:05 UTC"


def _text(case):
    errors = "replace" if _EXPECTED[case].get("decode") == "replace" else "strict"
    return (_CORPUS / f"{case}.md").read_bytes().decode("utf-8", errors=errors)


def _state(case, block):
    return _EXPECTED[case]["blocks"][block]["state"]


def _plan(text):
    return _plan_session_block(text, SID, "session-0123abcd", None, None, STARTED)


def _changed_rows(before, after):
    """(before rows, after rows) that differ, as half-open ranges after the
    common prefix and suffix of raw rows, and the rows of either side whose
    kind or hidden flag changed inside that prefix and suffix."""
    b, a = parse(before), parse(after)
    braw = [before[line.start:line.end] for line in b.lines]
    araw = [after[line.start:line.end] for line in a.lines]
    p = 0
    while p < min(len(braw), len(araw)) and braw[p] == araw[p]:
        p += 1
    s = 0
    while s < min(len(braw), len(araw)) - p and braw[-1 - s] == araw[-1 - s]:
        s += 1
    flags = lambda line: (line.kind, line.in_html)  # noqa: E731
    moved = [i for i in range(p) if flags(b.lines[i]) != flags(a.lines[i])]
    moved += [-1 - i for i in range(s) if flags(b.lines[-1 - i]) != flags(a.lines[-1 - i])]
    return (b, range(p, len(braw) - s)), (a, range(p, len(araw) - s)), moved


def _only_block_and_blank_rows(doc, rows, span):
    first, last = span if span else (0, -1)
    return all(first <= row <= last or not doc.lines[row].content.strip() for row in rows)


def test_every_corpus_file_is_a_case():
    assert len(_CASES) >= 100
    assert {p.stem for p in _CORPUS.glob("*.md")} == set(_CASES)


# --- the Current Session block ----------------------------------------------

@pytest.mark.parametrize("case", _CASES)
def test_the_session_block_writer(case):
    text, state = _text(case), _state(case, "SESSION")
    new, status = _plan(text)
    if state in _UNCERTAIN:
        assert new is None and status and _NAMES_A_LINE.search(status), status
        return
    assert new is not None, status  # FOUND is rewritten with new values, ABSENT gets a block
    (b, brows), (a, arows), moved = _changed_rows(text, new)
    after = a.find_block(SESSION_START_MARKER, SESSION_END_MARKER)
    assert after.state is State.FOUND
    before = b.find_block(SESSION_START_MARKER, SESSION_END_MARKER)
    assert _only_block_and_blank_rows(a, arows, after.spans[0])
    assert _only_block_and_blank_rows(b, brows, before.spans[0] if state == "FOUND" else None)
    assert moved == []
    assert _plan(new) == (None, None)


@pytest.mark.parametrize("case", _CASES)
def test_the_started_reader_reads_only_a_found_block(case, tmp_path):
    """The Started reader, from the file on disk: nothing from a block the
    table does not call FOUND, and from a FOUND block exactly what the block
    alone gives. Most corpus files hold a column-0 Started line, inside or
    outside the real block."""
    from session_init import _extract_session_started

    text = _text(case)
    (tmp_path / "CLAUDE.md").write_text(text, encoding="utf-8", newline="")
    got = _extract_session_started(str(tmp_path))
    if _state(case, "SESSION") != "FOUND":
        assert got is None
        return
    doc = parse(text)
    first, last = doc.find_block(SESSION_START_MARKER, SESSION_END_MARKER).spans[0]
    alone = tmp_path / "alone"
    alone.mkdir()
    (alone / "CLAUDE.md").write_text(
        "".join(text[line.start:line.end] for line in doc.lines[first:last + 1]),
        encoding="utf-8", newline="")
    assert got == _extract_session_started(str(alone))


def test_a_written_block_is_read_back_by_the_reader():
    for case in _CASES:
        new, _ = _plan(_text(case))
        if new is not None:
            assert recorded_session_id(new) == SID, case


# --- the pin-marker writer --------------------------------------------------

@pytest.mark.parametrize("case", _CASES)
def test_the_pin_marker_planner_follows_the_managed_block(case):
    plan = plan_insertion(_text(case))
    state = _state(case, "MANAGED")
    if state == "ABSENT":
        assert plan is SkipReason.NOT_MIGRATED
    elif state in _UNCERTAIN:
        assert isinstance(plan, Refusal) and _NAMES_A_LINE.search(plan.located.reason)


# --- the legacy kernel strip ------------------------------------------------

@pytest.mark.parametrize("case", _CASES)
def test_the_kernel_strip(case):
    text = _text(case)
    notice, new = _plan_kernel_strip(text, Path("/nonexistent/CLAUDE.md"))
    blocks = _EXPECTED[case]["blocks"]
    if "KERNEL" not in blocks:
        assert "PACT_START" not in text and (notice, new) == (None, None)
        return
    state = blocks["KERNEL"]["state"]
    if state == "ABSENT":
        assert (notice, new) == (None, None)
    elif state == "FOUND":
        assert notice is None and new is not None
        (b, brows), (a, arows), moved = _changed_rows(text, new)
        assert a.find_block(KERNEL_START, KERNEL_END).state is State.ABSENT
        assert list(arows) == [] or _only_block_and_blank_rows(a, arows, None)
        assert _only_block_and_blank_rows(b, brows, b.find_block(KERNEL_START, KERNEL_END).spans[0])
        assert moved == []
        assert _plan_kernel_strip(new, Path("/nonexistent/CLAUDE.md")) == (None, None)
    else:
        assert new is None and notice and _NAMES_A_LINE.search(notice), notice


# --- the one Pinned locator -------------------------------------------------

@pytest.mark.parametrize("case", _CASES)
def test_the_pinned_locator_returns_an_uncertain_memory_block_as_it_is(case):
    state = _state(case, "MEMORY")
    if state in _UNCERTAIN:
        for unique in (False, True):
            assert locate_pinned(parse(_text(case)), unique=unique).state.value == state


def test_the_block_text_the_writer_inserts_is_the_one_it_builds():
    # The writer's invariants above hold for this exact text; a change to the
    # builder that opened a fence or an HTML block would move rows after it.
    block = _session_block_text(SID, "session-0123abcd", None, None, STARTED)
    doc = parse(block + "\n")
    assert doc.boundary is None
    assert not any(line.in_html for line in doc.lines)


def test_the_started_reader_reads_most_found_blocks(tmp_path):
    """The FOUND arm above compares two reads; this keeps it from comparing
    None with None."""
    from session_init import _extract_session_started

    read = 0
    for case in _CASES:
        if _state(case, "SESSION") == "FOUND":
            project = tmp_path / case
            project.mkdir()
            (project / "CLAUDE.md").write_text(_text(case), encoding="utf-8", newline="")
            read += _extract_session_started(str(project)) is not None
    assert read >= 30


def test_the_row_check_sees_a_kind_change_outside_the_changed_rows():
    # A fence opened between two kept rows turns the row after it into code
    # or an uncertain row: the check must report it.
    assert _changed_rows("a\nb\n", "a\n```\nb\n")[2] == [-1]
    assert _changed_rows("a\nb\n", "a\n\nb\n")[2] == []
