"""
Location: pact-plugin/tests/test_claude_md_corpus_writers.py
Summary: Every corpus file through the readers and writers, not only the
         finder: the Current Session block's planner and reader, the pin-marker
         planner, the legacy kernel strip's planner, the migration's planner and
         the one Pinned locator. One arm runs every CLAUDE.md writer over the
         corpus and a sweep of open HTML blocks, and requires that none makes
         the file less readable.
Used by: pytest.

The expected outcome of each call is read from the corpus's hand-written block
states (tests/fixtures/claude_md_corpus/expected.json), never computed by the
parser: a block the table calls UNKNOWN, DUPLICATE or MALFORMED is refused with
the line named, and a reader returns nothing from it. A new Current Session
block that would land inside an HTML block the file never closes is refused
with that block's line named. Every write is checked after the fact: the
target block reads back FOUND once, only that block and blank separator rows
changed, every other row keeps its bytes, its kind and its hidden flag, and a
second plan writes nothing.
"""

import collections
import functools
import itertools
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from unittest import mock

import pytest

import archive_pin
from fixtures.hf_cache import hf_cache_env
from pin_caps import section_pins
from scripts import working_memory
from shared.claude_md_manager import (
    MANAGED_END_MARKER,
    MANAGED_START_MARKER,
    MEMORY_END_MARKER,
    MEMORY_START_MARKER,
    SESSION_END_MARKER,
    SESSION_START_MARKER,
    _plan_kernel_strip,
    _plan_migration,
)
from shared.claude_md_markers import State, parse, uncertainty_added
from shared.pin_markers import Insertion, Refusal, SkipReason, apply_insertion, plan_insertion
from shared.session_resume import _plan_session_block, _session_block_text
from shared.stale_session import recorded_session_id
from staleness import check_pinned_staleness, locate_pinned

_CORPUS = Path(__file__).parent / "fixtures" / "claude_md_corpus"
_EXPECTED = json.loads((_CORPUS / "expected.json").read_text(encoding="utf-8"))
_CASES = sorted(_EXPECTED)
_UNCERTAIN = {"UNKNOWN", "DUPLICATE", "MALFORMED"}
_NAMES_A_LINE = re.compile(r"\blines? \d")
# Certain files with no Current Session block whose new block is appended
# inside an HTML block the file opens and never closes. Its start marker would
# end that block, so the read-back refuses, naming the line that opens it and
# the cause.
_APPENDED_INSIDE_AN_OPEN_BLOCK = {
    "html_comment_unclosed_above_pinned_no_structure":
        (2, "an HTML block is ended only by a line that starts a comment"),
}
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
    if case in _APPENDED_INSIDE_AN_OPEN_BLOCK:
        assert state == "ABSENT"
        line, cause = _APPENDED_INSIDE_AN_OPEN_BLOCK[case]
        refusal = ("did not read back as one block where it was written: "
                   f"line {line} starts an uncertain region: {cause}")
        assert new is None and status and refusal in status, status
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
    (tmp_path / "CLAUDE.md").write_bytes(text.encode("utf-8"))
    got = _extract_session_started(str(tmp_path))
    if _state(case, "SESSION") != "FOUND":
        assert got is None
        return
    doc = parse(text)
    first, last = doc.find_block(SESSION_START_MARKER, SESSION_END_MARKER).spans[0]
    alone = tmp_path / "alone"
    alone.mkdir()
    (alone / "CLAUDE.md").write_bytes(
        "".join(text[line.start:line.end] for line in doc.lines[first:last + 1]).encode("utf-8"))
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


# --- the migration into the managed structure -------------------------------

_PACT_MARKERS = {MANAGED_START_MARKER, MANAGED_END_MARKER, MEMORY_START_MARKER, MEMORY_END_MARKER,
                 SESSION_START_MARKER, SESSION_END_MARKER}
_PACT_HEADINGS = {"## Retrieved Context", "## Pinned Context", "## Working Memory"}


def _is_pact_line(line):
    """A marker line (up to 3 spaces before the marker) or a memory heading at
    column 0: the lines the migration rebuilds. An indented heading is the
    user's."""
    text = line.rstrip()
    body = text.lstrip(" ")
    return (body in _PACT_MARKERS and len(text) - len(body) <= 3) or text in _PACT_HEADINGS


@pytest.mark.parametrize("case", _CASES)
def test_the_migration(case):
    """An unmigrated file is rebuilt into one managed block holding one memory
    block, keeps the session block it had, and keeps every line of the user's
    byte for byte; PACT's own marker and section-heading lines are rebuilt. A
    block the table calls uncertain is refused with the line named."""
    text = _text(case)
    new, refusal = _plan_migration(text)
    states = {name: _state(case, name) for name in ("MANAGED", "MEMORY", "SESSION")}
    if states["MANAGED"] == "FOUND":
        assert (new, refusal) == (None, None)
        return
    if _UNCERTAIN & set(states.values()):
        assert new is None and refusal and _NAMES_A_LINE.search(refusal), refusal
        return
    if new is None:
        assert refusal and _NAMES_A_LINE.search(refusal), refusal
        return
    doc = parse(new)
    assert doc.find_block(MANAGED_START_MARKER, MANAGED_END_MARKER).state is State.FOUND
    assert doc.find_block(MEMORY_START_MARKER, MEMORY_END_MARKER).state is State.FOUND
    assert doc.find_block(SESSION_START_MARKER, SESSION_END_MARKER).state.value == states["SESSION"]
    users = collections.Counter(line for line in text.lstrip("\ufeff").splitlines()
                                if line.strip() and not _is_pact_line(line))
    assert not users - collections.Counter(new.splitlines()), users - collections.Counter(new.splitlines())
    assert _plan_migration(new) == (None, None)


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
            (project / "CLAUDE.md").write_bytes(_text(case).encode("utf-8"))
            read += _extract_session_started(str(project)) is not None
    assert read >= 30


def test_the_row_check_sees_a_kind_change_outside_the_changed_rows():
    # A fence opened between two kept rows turns the row after it into code
    # or an uncertain row: the check must report it.
    assert _changed_rows("a\nb\n", "a\n```\nb\n")[2] == [-1]
    assert _changed_rows("a\nb\n", "a\n\nb\n")[2] == []


# --- no writer makes the file less readable ----------------------------------
#
# Every CLAUDE.md writer leaves no more rows the parser cannot read than it
# found, wherever it changes the text or licenses a removal. Each writer is
# driven where its check sits: the session, migration and kernel planners as
# pure functions, the pin-marker planner (its check is inside it) then
# `apply_insertion`, staleness and the two syncs on a real file, and the
# archive through its verdict for every pin, whose removal is the Edit an
# ARCHIVED verdict licenses. The population is the corpus and a sweep: into
# three base files, a user's HTML-block opener before row i and its closer
# (mid-line, at a row edge, or none) before row j >= i, for seven block types,
# each file with and without a fence at its end. Every 7th file of each base
# runs under CI and every 13th locally; an odd stride keeps both files of each
# plain and fenced pair.

_STRIDE = 7 if os.environ.get("CI") else 13

_STALE_PIN = "### Fix the gate (PR #12, merged 2020-01-01)\nBody of the stale pin.\n"
_SWEEP_BASES = {
    "migrated": (
        f"{MANAGED_START_MARKER}\n# PACT\n\n{SESSION_START_MARKER}\n## Current Session\n- Resume: x\n"
        f"{SESSION_END_MARKER}\n\n{MEMORY_START_MARKER}\n## Retrieved Context\nrc entry\n"
        f"## Pinned Context\n\n### Pin A\nBody A.\n\n{_STALE_PIN}\n### Pin C\nBody C.\n\n"
        f"## Working Memory\nwm entry\n{MEMORY_END_MARKER}\n\n{MANAGED_END_MARKER}\n\n## Notes\nnotes\n"
    ),
    "unmigrated": (
        f"# Project\nintro\n\n## Pinned Context\n\n### Pin A\nBody A.\n\n{_STALE_PIN}\n"
        "### Pin C\nBody C.\n\n## Notes\nnotes\n"
    ),
    "kernel": (
        f"# Me\nintro\n\n{KERNEL_START} v3 -->\nkernel one\nkernel two\n{KERNEL_END}\n\nnotes\nmore\n"
    ),
}
_SWEEP_BLOCKS = {  # opener, a line closing it mid-line (None: no such line), a row-edge closer
    "type 1": ("<pre>", "a </pre> b", "</pre>"),
    "type 2": ("<!-- note", "a --> b", "-->"),
    "type 3": ("<?php", "a ?> b", "?>"),
    "type 4": ("<!NOTE", "a > b", ">"),
    "type 5": ("<![CDATA[", "a ]]> b", "]]>"),
    "type 6": ("<div>", None, ""),
    "type 7": ("<custom-x>", None, ""),
}
_SWEEP_FENCE = "\n## F\n```\nc\n```\n"


def sweep(base):
    """(name, text) for each file the sweep builds from `base`."""
    rows = base.splitlines(keepends=True)
    for kind, (opener, mid, edge) in _SWEEP_BLOCKS.items():
        for i in range(len(rows) + 1):
            closers: "list[tuple[str, tuple[int, str] | None]]" = [("never closed", None)]
            for j in range(i, len(rows) + 1):
                if mid is not None:
                    closers.append((f"closed mid-line at {j}", (j, mid)))
                closers.append((f"closed at {j}", (j, edge)))
            for closer_name, closer in closers:
                out = list(rows)
                if closer is not None:
                    out.insert(closer[0], closer[1] + "\n")
                out.insert(i, opener + "\n")
                text = "".join(out)
                name = f"{kind}, opened at {i}, {closer_name}"
                yield name, text
                yield name + ", fence after", text + _SWEEP_FENCE


_MEMORY = {"id": "m1", "context": "a context", "goal": "a goal", "created_at": "2026-01-02T03:04:05+00:00"}
_ADDS_UNREADABLE = re.compile(r"the update would .* a region PACT cannot read: ")


def _plan_pin_markers(text):
    planned = plan_insertion(text)
    if isinstance(planned, Refusal):
        return None, bool(_ADDS_UNREADABLE.search(planned.value))
    return (apply_insertion(text, planned) if isinstance(planned, Insertion) else None), False


def _planner(plan):
    def write(raw, errors):
        text = raw.decode("utf-8", errors=errors)
        return [(text, plan(text), False)]
    return write


def _kernel_strip(raw, errors):
    text = raw.decode("utf-8", errors=errors)
    notice, new = _plan_kernel_strip(text, Path("/nonexistent/CLAUDE.md"))
    return [(text, new, bool(_ADDS_UNREADABLE.search(notice or "")))]


def _pin_markers(raw, errors):
    text = raw.decode("utf-8", errors=errors)
    new, refused = _plan_pin_markers(text)
    return [(text, new, refused)]


def _on_file(raw, run):
    """Write `raw` to a CLAUDE.md in a new directory, run a writer that writes
    the file itself, and return (the file's bytes after it, the result)."""
    with tempfile.TemporaryDirectory() as directory, \
            mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": directory}):
        path = Path(directory) / "CLAUDE.md"
        path.write_bytes(raw)
        result = run(Path(directory), path)
        return path.read_bytes(), result


def _staleness(raw, errors):
    after, status = _on_file(raw, lambda root, path: check_pinned_staleness(claude_md_path=path))
    status = status or ""
    refused = status.startswith("Pinned staleness skipped: ") and bool(_ADDS_UNREADABLE.search(status))
    return [(raw.decode("utf-8", errors=errors), after.decode("utf-8", errors=errors), refused)]


class _Warnings(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def _sync(run):
    def write(raw, errors):
        warnings = _Warnings()
        working_memory.logger.addHandler(warnings)
        try:
            after, _ = _on_file(raw, run)
        finally:
            working_memory.logger.removeHandler(warnings)
        refused = any(_ADDS_UNREADABLE.search(message) for message in warnings.messages)
        return [(raw.decode("utf-8", errors=errors), after.decode("utf-8", errors=errors), refused)]
    return write


def _verdict(raw, index):
    """The archive's verdict on pin `index` of a CLAUDE.md holding `raw`, with
    the memory CLI faked: a save returns an id and a get returns what was saved."""
    saved = {}

    def memory_cli(args, **kwargs):
        if args[0] == "save":
            saved["context"] = json.loads(kwargs["stdin_data"])["context"]
            return 0, json.dumps({"ok": True, "result": {"memory_id": "a" * 32}}), ""
        return 0, json.dumps({"ok": True, "result": {"context": saved["context"]}}), ""

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "CLAUDE.md"
        path.write_bytes(raw)
        with mock.patch.object(archive_pin, "get_project_claude_md_path", lambda: path), \
                mock.patch.object(archive_pin, "_run_memory_cli", memory_cli):
            return archive_pin.build_verdict(index, db_path=None)


def _archive(raw, errors):
    # The text as archive_pin reads it: universal newlines.
    text = raw.decode("utf-8", errors=errors).replace("\r\n", "\n").replace("\r", "\n")
    doc = parse(text)
    located = locate_pinned(doc)
    if located.state is not State.FOUND:
        return []
    outcomes = []
    for index in range(len(section_pins(doc, located))):
        verdict = _verdict(raw, index)
        if verdict["outcome"] == "ARCHIVED":
            assert text.count(verdict["delete_string"]) == 1
            outcomes.append((text, text.replace(verdict["delete_string"], "", 1), False))
        else:
            refused = (verdict["outcome"] == "ARCHIVED_DELETE_UNSAFE"
                       and verdict["reason"].startswith("removing the pin is refused: "))
            outcomes.append((text, None, refused))
    return outcomes


_WRITERS = {
    "session block": _planner(lambda text: _plan(text)[0]),
    "migration": _planner(lambda text: _plan_migration(text)[0]),
    "kernel strip": _kernel_strip,
    "pin markers": _pin_markers,
    "staleness": _staleness,
    "working memory sync": _sync(lambda root, path: working_memory.sync_to_claude_md(
        _MEMORY, target=path, claude_md_root=root)),
    "retrieved context sync": _sync(lambda root, path: working_memory.sync_retrieved_to_claude_md(
        [_MEMORY], "a query", None, ["m1"], claude_md_root=root)),
    "archive": _archive,
}
# The writers that keep a check of their own; the others add no unreadable row
# on any case, and this arm is what says so.
_CHECKED = ("kernel strip", "pin markers", "staleness", "working memory sync",
            "retrieved context sync", "archive")


def _population():
    """{group: {case: (raw bytes, decode errors)}}: the corpus, and every
    `_STRIDE`th file of each base file's sweep."""
    groups = {"corpus": {}}
    for case in _CASES:
        errors = "replace" if _EXPECTED[case].get("decode") == "replace" else "strict"
        groups["corpus"][case] = ((_CORPUS / f"{case}.md").read_bytes(), errors)
    for base_name, base in _SWEEP_BASES.items():
        files = itertools.islice(sweep(base), 0, None, _STRIDE)
        groups[base_name] = {name: (text.encode("utf-8"), "strict") for name, text in files}
    return groups


_POPULATION = _population()


@functools.lru_cache(maxsize=None)
def _judged(writer, group):
    """(writes, refusals by the writer's own check, [(case, reason)] for each
    write that adds unreadable rows) over one group of the population."""
    writes, refusals, added = 0, 0, []
    for case, (raw, errors) in _POPULATION[group].items():
        for old, new, refused in _WRITERS[writer](raw, errors):
            refusals += refused
            if new is not None and new != old:
                writes += 1
                reason = uncertainty_added(parse(old), parse(new))
                if reason is not None:
                    added.append((case, reason))
    return writes, refusals, added


@pytest.mark.parametrize("group", list(_POPULATION))
@pytest.mark.parametrize("writer", list(_WRITERS))
def test_no_writer_makes_the_file_less_readable(writer, group):
    """Wherever a writer changes the text, or licenses a removal, the new text
    has no more rows the parser cannot read than the text it started from. A
    failure names the cases; that writer needs the check the others have."""
    _, _, added = _judged(writer, group)
    assert not added, f"{writer} adds unreadable rows on {len(added)} case(s), e.g. {added[:3]}"


def test_every_writer_writes_and_every_checked_writer_refuses():
    """The arm is evidence only if each writer writes on some case, and each
    writer with its own check refuses some case through that check."""
    writes, refusals = collections.Counter(), collections.Counter()
    for writer in _WRITERS:
        for group in _POPULATION:
            group_writes, group_refusals, _ = _judged(writer, group)
            writes[writer] += group_writes
            refusals[writer] += group_refusals
    assert all(writes[writer] for writer in _WRITERS), writes
    assert all(refusals[writer] for writer in _CHECKED), refusals


@pytest.mark.requires_embedding_backend
def test_the_archive_refuses_the_exposed_corpus_file_through_the_real_memory_cli(
    tmp_path, monkeypatch, memory_store
):
    """The arm fakes the memory CLI. This row runs the real one on the corpus
    file whose removal the archive refuses, so the faked seam is checked."""
    raw = (_CORPUS / "hidden_declaration_closed_by_prose_ending_gt.md").read_bytes()
    path = tmp_path / "CLAUDE.md"
    path.write_bytes(raw)
    monkeypatch.setattr(archive_pin, "get_project_claude_md_path", lambda: path)
    for key, value in hf_cache_env().items():
        monkeypatch.setenv(key, value)
    verdict = archive_pin.build_verdict(0, db_path=str(memory_store("archive.db")))
    assert verdict["outcome"] == "ARCHIVED_DELETE_UNSAFE"
    assert verdict["reason"] == (
        "removing the pin is refused: the update would make line 2 start a region PACT cannot "
        "read: an HTML block is ended only by a line that starts a comment"
    )
    assert path.read_bytes() == raw
