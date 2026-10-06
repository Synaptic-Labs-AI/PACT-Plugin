"""
Location: pact-plugin/tests/test_claude_md_session_block.py
Summary: The Current Session block's readers and writers on the fence-aware
         parser: update_session_info (and its pure planner), the field readers
         (stale-block detector, previous session dir, Started), and the
         bootstrap gate's recording branch.
Used by: pytest.

Every CLAUDE.md here is written under tmp_path. Rows cover: fenced text left
byte-identical; refusals that leave the file alone and name the line;
idempotence and the read-back of every write; a found block above an uncertain
region; the non-UTF-8 skip path planning on the replace-decoded copy; and the
exact bytes of the five ordinary write shapes, so a normal file's output is
unchanged.
"""

import pathlib
import subprocess
import sys
import uuid

import pytest

from shared import session_resume
from shared.claude_md_manager import (
    MANAGED_END_MARKER,
    MANAGED_START_MARKER,
    MANAGED_TITLE,
    MEMORY_END_MARKER,
    MEMORY_START_MARKER,
    RETRIEVED_CONTEXT_COMMENT,
    SESSION_END_MARKER,
    SESSION_START_MARKER,
    WORKING_MEMORY_COMMENT,
)
from shared.claude_md_markers import State, parse
from shared.session_resume import _session_block_text, update_session_info
from shared.stale_session import detect_stale_session_block, recorded_session_id

SID = "0123abcd-0000-4000-8000-00000000000a"
OLD_SID = "fedc4321-1111-4000-8000-00000000000b"
TEAM = "session-0123abcd"
STARTED = "2026-01-02 03:04:05 UTC"
S, E = SESSION_START_MARKER, SESSION_END_MARKER


def _block(sid=SID):
    return _session_block_text(sid, TEAM, None, None, STARTED)


OLD_BLOCK = _block(OLD_SID)
FENCED_EXAMPLE = "```markdown\n" + OLD_BLOCK + "\n```\n"


@pytest.fixture
def project(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    return proj


def _target(proj):
    return proj / ".claude" / "CLAUDE.md"


def _write(proj, text):
    _target(proj).write_bytes(text.encode("utf-8"))


def _read(proj):
    return _target(proj).read_bytes().decode("utf-8")


def _update():
    return update_session_info(SID, TEAM, started=STARTED)


def _status():
    """The status of a write expected to report one."""
    status = _update()
    assert status is not None
    return status


# --- the five ordinary shapes, byte for byte -------------------------------

def test_a_missing_file_is_created_with_the_full_managed_structure(project):
    assert _update() == "Session info created in new project CLAUDE.md"
    assert _read(project) == (
        f"{MANAGED_START_MARKER}\n{MANAGED_TITLE}\n\n{_block()}\n\n"
        f"{MEMORY_START_MARKER}\n## Retrieved Context\n{RETRIEVED_CONTEXT_COMMENT}\n\n"
        f"## Pinned Context\n\n## Working Memory\n{WORKING_MEMORY_COMMENT}\n"
        f"{MEMORY_END_MARKER}\n\n{MANAGED_END_MARKER}\n"
    )


def test_an_existing_block_is_replaced_in_place(project):
    _write(project, "intro\n" + OLD_BLOCK + "\nafter\n")
    assert _update() == "Session info updated in project CLAUDE.md"
    assert _read(project) == "intro\n" + _block() + "\nafter\n"


def test_a_managed_file_gets_the_block_before_the_memory_block(project):
    head = f"{MANAGED_START_MARKER}\n{MANAGED_TITLE}\n\n"
    tail = f"{MEMORY_START_MARKER}\n## Pinned Context\n{MEMORY_END_MARKER}\n{MANAGED_END_MARKER}\n"
    _write(project, head + tail)
    assert _update() == "Session info added to project CLAUDE.md"
    assert _read(project) == head + _block() + "\n\n" + tail


def test_a_legacy_file_gets_the_block_before_its_retrieved_context_heading(project):
    _write(project, "# Notes\n\n## Retrieved Context\nentries\n")
    assert _update() == "Session info added to project CLAUDE.md"
    assert _read(project) == "# Notes\n\n" + _block() + "\n\n## Retrieved Context\nentries\n"


# A memory block with no managed region: the block goes before the memory start
# row, never inside the memory block and never at end of file, wherever a
# `## Retrieved Context` heading is.
NOTES = "## Retrieved Context\nuser notes\n"
MEMORY_WITH_HEADING = (
    f"{MEMORY_START_MARKER}\n## Retrieved Context\nentries\n{MEMORY_END_MARKER}\n")
MEMORY_WITHOUT_HEADING = f"{MEMORY_START_MARKER}\n## Pinned Context\n{MEMORY_END_MARKER}\n"


@pytest.mark.parametrize("head, memory, tail", [
    ("# Notes\n\n", MEMORY_WITH_HEADING, ""),  # heading inside the memory block
    ("# Notes\n\n", MEMORY_WITHOUT_HEADING, "\nlast line\n"),  # no heading at all
    (NOTES, MEMORY_WITHOUT_HEADING, ""),  # heading only above the memory block
    ("", MEMORY_WITHOUT_HEADING, "\n" + NOTES),  # heading only below it
], ids=["heading-inside", "no-heading", "heading-above", "heading-below"])
def test_a_memory_block_with_no_managed_region_gets_the_block_before_its_start_row(
        project, head, memory, tail):
    _write(project, head + memory + tail)
    assert _update() == "Session info added to project CLAUDE.md"
    assert _read(project) == head + _block() + "\n\n" + memory + tail


def test_a_block_before_the_memory_block_is_kept_there_and_rewritten_in_place(project):
    _write(project, "# Notes\n\n" + MEMORY_WITH_HEADING)
    _update()
    first = _read(project)
    assert _update() is None
    assert _read(project) == first
    assert update_session_info(OLD_SID, TEAM, started=STARTED) == (
        "Session info updated in project CLAUDE.md")
    assert _read(project) == "# Notes\n\n" + OLD_BLOCK + "\n\n" + MEMORY_WITH_HEADING


def test_a_block_already_inside_the_memory_block_is_rewritten_in_place(project):
    # Written there by an earlier version; moving it is the migration's job.
    before = f"# Notes\n\n{MEMORY_START_MARKER}\n"
    after = f"\n\n## Retrieved Context\nentries\n{MEMORY_END_MARKER}\n"
    _write(project, before + OLD_BLOCK + after)
    assert _update() == "Session info updated in project CLAUDE.md"
    assert _read(project) == before + _block() + after


@pytest.mark.parametrize("text, base", [
    ("# Notes\nbody\n", "# Notes\nbody\n"),
    ("# Notes\nno trailing newline", "# Notes\nno trailing newline\n"),
])
def test_a_file_with_no_anchor_gets_the_block_at_its_end(project, text, base):
    _write(project, text)
    assert _update() == "Session info added to project CLAUDE.md"
    assert _read(project) == base + "\n" + _block() + "\n"


# --- fenced text stays byte-identical --------------------------------------

def test_a_fenced_example_is_not_rewritten_beside_the_real_block(project):
    _write(project, "# Notes\n" + FENCED_EXAMPLE + "\n" + OLD_BLOCK + "\n")
    _update()
    assert _read(project) == "# Notes\n" + FENCED_EXAMPLE + "\n" + _block() + "\n"


def test_a_fenced_example_alone_is_not_the_block(project):
    head = f"{MANAGED_START_MARKER}\n{FENCED_EXAMPLE}\n"
    tail = f"{MEMORY_START_MARKER}\n{MEMORY_END_MARKER}\n{MANAGED_END_MARKER}\n"
    _write(project, head + tail)
    assert _update() == "Session info added to project CLAUDE.md"
    assert _read(project) == head + _block() + "\n\n" + tail


@pytest.mark.parametrize("lookalike", [
    "```\n## Retrieved Context\n```\n",
    "see the ## Retrieved Context section\n",
    "<!--\n## Retrieved Context\n-->\n",
])
def test_a_retrieved_context_lookalike_is_not_the_anchor(project, lookalike):
    text = "# Notes\n" + lookalike
    _write(project, text)
    if lookalike.startswith("<!--"):
        status = _status()
        # The only matching heading is commented out: refuse, never duplicate.
        assert "skipped" in status and "line 3" in status
        assert _read(project) == text
    else:
        _update()
        assert _read(project) == text + "\n" + _block() + "\n"


# --- refusals leave the file alone and name the line -----------------------

@pytest.mark.parametrize("text, line", [
    (OLD_BLOCK + "\n\n" + OLD_BLOCK + "\n", "lines 1, 9"),  # duplicate blocks
    ("see " + S + " here\n" + OLD_BLOCK + "\n", "line 1"),  # a stray marker
    ("intro\n```\nnever closed\n", "line 2"),  # no block, an uncertain region
    (S + "\n## Current Session\n```\n" + E + "\n", "line 1"),  # start, then uncertain
    (f"{MANAGED_START_MARKER}\nsee {MEMORY_START_MARKER} here\n", "line 2"),  # stray anchor
    # A managed-region marker the parser cannot place, beside a real memory block.
    (f"see {MANAGED_START_MARKER} here\n{MEMORY_START_MARKER}\n{MEMORY_END_MARKER}\n", "line 1"),
    (f"{MANAGED_START_MARKER}\n{MANAGED_START_MARKER}\n{MEMORY_START_MARKER}\n{MEMORY_END_MARKER}\n",
     "lines 1, 2"),
])
def test_a_block_the_parser_cannot_place_is_refused_and_left_byte_identical(project, text, line):
    _write(project, text)
    status = _status()
    assert status.startswith("Session info skipped: ") and line in status
    assert "now stale" in status
    assert _read(project) == text


READ_BACK = ("the rewritten Current Session block did not read back as one block "
             "where it was written: ")


@pytest.mark.parametrize("written, where", [
    # two starts: the lookup's own reason names the lines
    (S + "\n" + S + "\n" + E,
     f"{S!r} on line 3 starts a block inside the block started on line 2"),
    # one well-formed block a row below where the write put it
    ("x\n" + S + "\n" + E, "it reads back at line 3, not line 2 where it was written"),
    # no markers at all
    ("no markers", "no block reads back at line 2, where it was written"),
], ids=["two starts", "a row below", "no block"])
def test_a_write_that_would_not_read_back_is_refused_naming_the_line(
        project, monkeypatch, written, where):
    _write(project, "intro\n" + OLD_BLOCK + "\n")
    monkeypatch.setattr(session_resume, "_session_block_text", lambda *args: written)
    status = _status()
    assert status.startswith("Session info skipped: " + READ_BACK + where + ". ")
    assert _read(project) == "intro\n" + OLD_BLOCK + "\n"


# --- idempotence, the read-back, an uncertain region below -----------------

def test_a_second_write_is_a_no_op_and_the_block_reads_back_once(project):
    _write(project, "# Notes\n" + FENCED_EXAMPLE + "\n## Retrieved Context\n")
    _update()
    first = _read(project)
    assert _update() is None
    assert _read(project) == first
    doc = parse(first)
    located = doc.find_block(S, E)
    assert located.state is State.FOUND
    start, end = located.spans[0]
    assert "\n".join(line.content for line in doc.lines[start:end + 1]) == _block()


def test_a_block_above_an_uncertain_region_is_written(project):
    tail = "\n```\nnever closed\n"
    _write(project, OLD_BLOCK + tail)
    assert _update() == "Session info updated in project CLAUDE.md"
    assert _read(project) == _block() + tail


# --- encodings and line endings --------------------------------------------

def test_a_leading_bom_stays_in_place(project):
    _write(project, "﻿" + OLD_BLOCK + "\nafter\n")
    _update()
    assert _read(project) == "﻿" + _block() + "\nafter\n"


def test_a_leading_bom_stays_first_when_the_block_goes_before_a_memory_start_on_row_0(project):
    """The new block goes before the memory start row. The BOM in front of that
    row stays the file's first character, so the memory start line is still a
    marker line and the memory block still reads back."""
    _write(project, "\ufeff" + MEMORY_WITH_HEADING)
    assert _update() == "Session info added to project CLAUDE.md"
    written = _read(project)
    assert written == "\ufeff" + _block() + "\n\n" + MEMORY_WITH_HEADING
    assert parse(written).find_block(MEMORY_START_MARKER, MEMORY_END_MARKER).state is State.FOUND


def test_a_crlf_file_stays_crlf_and_the_block_reads_back(project):
    _target(project).write_bytes(("intro\n" + OLD_BLOCK + "\nafter\n").replace("\n", "\r\n").encode())
    _update()
    raw = _target(project).read_bytes()
    assert raw == ("intro\n" + _block() + "\nafter\n").replace("\n", "\r\n").encode()
    assert parse(raw.decode()).find_block(S, E).state is State.FOUND


def test_the_skip_path_plans_on_the_replace_decoded_copy(project, monkeypatch):
    seen = []
    real = session_resume._plan_session_block

    def spy(content, *values):
        seen.append(content)
        return real(content, *values)

    monkeypatch.setattr(session_resume, "_plan_session_block", spy)
    _target(project).write_bytes(b"caf\xe9\n" + OLD_BLOCK.encode() + b"\n")
    status = _status()
    assert "not valid UTF-8" in status
    assert seen and seen[-1].startswith("caf�\n")
    assert _target(project).read_bytes() == b"caf\xe9\n" + OLD_BLOCK.encode() + b"\n"


def test_the_skip_path_reports_nothing_when_the_block_is_current(project):
    _target(project).write_bytes(b"caf\xe9\n" + _block().encode() + b"\n")
    assert _update() is None


def test_the_skip_path_reports_a_fenced_example_as_work_left_undone(project):
    _target(project).write_bytes(b"caf\xe9\n" + FENCED_EXAMPLE.encode())
    assert "not valid UTF-8" in _status()


# --- readers: only the found block is read ---------------------------------

def test_recorded_session_id_reads_only_inside_the_found_block():
    assert recorded_session_id(OLD_BLOCK) == OLD_SID
    assert recorded_session_id("# Notes\n" + FENCED_EXAMPLE) is None
    assert recorded_session_id(f"- Resume: `claude --resume {OLD_SID}`\n") is None
    prose_first = f"- Resume: `claude --resume {SID}`\n" + OLD_BLOCK
    assert recorded_session_id(prose_first) == OLD_SID
    assert recorded_session_id("```\n" + OLD_BLOCK) is None  # past an uncertain region


def test_the_stale_block_detector_ignores_a_fenced_resume_line(project):
    _write(project, "# Notes\n" + FENCED_EXAMPLE)
    assert detect_stale_session_block({"session_id": SID}) is None
    _write(project, OLD_BLOCK + "\n")
    assert OLD_SID in str(detect_stale_session_block({"session_id": SID}))


def test_a_parser_that_fails_to_import_gives_no_stale_block_warning(project, monkeypatch):
    # dispatch_gate loads stale_session under a fail-closed guard, so the
    # detector imports the parser inside its own catch-all: a failed import
    # means no warning, never a raise.
    _write(project, OLD_BLOCK + "\n")
    assert OLD_SID in str(detect_stale_session_block({"session_id": SID}))
    monkeypatch.setitem(sys.modules, "shared.claude_md_markers", None)
    assert detect_stale_session_block({"session_id": SID}) is None


def test_the_previous_session_dir_is_read_only_from_the_found_block(project, tmp_path):
    from shared.session_resume import _extract_prev_session_dir

    # The directory exists, so a fenced line that were read would be returned.
    session_dir = tmp_path / ".claude" / "pact-sessions" / "proj" / OLD_SID
    session_dir.mkdir(parents=True)
    block = _session_block_text(OLD_SID, TEAM, str(session_dir), None, STARTED)
    _write(project, block + "\n")
    found = _extract_prev_session_dir(str(project))
    assert found is not None and pathlib.Path(found).resolve() == session_dir.resolve()
    _write(project, "# Notes\n```\n" + block + "\n```\n")
    assert _extract_prev_session_dir(str(project)) is None


def test_the_previous_session_id_fallback_reads_only_the_found_block(project):
    from shared.session_resume import _extract_prev_session_dir

    # A block with no Session dir line falls back to its own Resume line; the
    # fenced example above it names another session.
    _write(project, "# Notes\n" + FENCED_EXAMPLE + "\n" + _block() + "\n")
    derived = _extract_prev_session_dir(str(project))
    assert derived is not None and derived.endswith(SID)


def test_the_started_value_is_read_only_from_the_found_block(project):
    from session_init import _extract_session_started

    _write(project, "# Notes\n" + FENCED_EXAMPLE)
    assert _extract_session_started(str(project)) is None
    _write(project, OLD_BLOCK + "\n")
    assert _extract_session_started(str(project)) == STARTED


# --- the bootstrap gate's recording branch ---------------------------------

def test_the_gate_refuses_an_unplaceable_block_without_writing_or_locking(tmp_path):
    from test_bootstrap_prompt_gate_unrecorded_lead import _VALUES_MARK, _gate, _sandbox, _start

    home, proj, env = _sandbox(tmp_path)
    text = "# My project\n" + OLD_BLOCK + "\n" + OLD_BLOCK + "\n"
    (proj / "CLAUDE.md").write_text(text, encoding="utf-8")
    fork = str(uuid.uuid4())
    _start(fork, "fork", None, home, env)

    context = _gate(fork, home, env)

    assert (proj / "CLAUDE.md").read_text(encoding="utf-8") == text
    assert sorted(p.name for p in proj.iterdir()) == ["CLAUDE.md"]
    assert _VALUES_MARK in context, "control: the branch ran"
    assert "Session info skipped: " in context and "lines 2, 9" in context


def test_the_gate_leaves_a_file_whose_only_block_is_fenced_alone(tmp_path):
    from test_bootstrap_prompt_gate_unrecorded_lead import _VALUES_MARK, _gate, _sandbox, _start

    home, proj, env = _sandbox(tmp_path)
    text = "# My project\n" + FENCED_EXAMPLE
    (proj / "CLAUDE.md").write_text(text, encoding="utf-8")
    fork = str(uuid.uuid4())
    _start(fork, "fork", None, home, env)

    context = _gate(fork, home, env)

    assert (proj / "CLAUDE.md").read_text(encoding="utf-8") == text
    assert _VALUES_MARK in context and "Session info skipped" not in context


# --- import cost -------------------------------------------------------------

def test_importing_the_shared_package_does_not_load_the_finder():
    # Every PACT hook imports `shared`, which imports session_resume, and
    # dispatch_gate loads its imports under a fail-closed guard. The finder
    # loads only on a path that reads or writes the session block; the second
    # value is the control that the probe can see it load.
    probe = (
        "import sys, shared, dispatch_gate\n"
        "before = 'shared.claude_md_markers' in sys.modules\n"
        "shared.session_resume._plan_session_block('', 'a', 'b', None, None, 't')\n"
        "print(before, 'shared.claude_md_markers' in sys.modules)\n"
    )
    hooks = pathlib.Path(session_resume.__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-c", probe], cwd=hooks,
                            capture_output=True, text=True, check=True)
    assert result.stdout.split() == ["False", "True"]
