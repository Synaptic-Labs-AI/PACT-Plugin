"""
Location: pact-plugin/tests/test_pin_readers_read_the_whole_file.py

Every reader of the Pinned section's pins reads rows of the whole-file parse,
never a parse of the section's text on its own: the staleness readers (the
entries they mark, the stale-pin block signal), archive_pin's index read and
block extraction, and check_pin_caps. A parse that starts mid-file starts in a
state the whole file does not have, so the two can disagree; the readers must
agree with `section_pins` on the whole file.

The population rows run over the fence corpus and the fence oracle's document
generator. The fixed rows use a file on which the whole file and the section's
text read a different number of pins.
"""

import glob
import random
from pathlib import Path

import archive_pin
import check_pin_caps
import staleness
from pin_caps import PIN_STALE_BLOCK_THRESHOLD, _PIN_HEADING_ROW, parse_pins, section_pins
from shared.claude_md_markers import State, parse
from test_claude_md_fence_oracle import _document

CORPUS = Path(__file__).resolve().parent / "fixtures" / "claude_md_corpus"


def _population():
    texts = {Path(f).name: Path(f).read_text(encoding="utf-8", errors="replace")
             for f in sorted(glob.glob(str(CORPUS / "*.md")))}
    for seed in (1, 2):
        rnd = random.Random(seed)
        for i in range(1000):
            texts[f"generated-{seed}-{i}"] = _document(rnd)
    return texts


def _found(text):
    """(doc, located, body rows) for a text whose Pinned section is FOUND and
    not empty, else None."""
    doc = parse(text)
    located = staleness.locate_pinned(doc)
    if located.state is not State.FOUND:
        return None
    body = staleness._pinned_body(doc, located)
    return None if body is None else (doc, located, body)


def _section_text(text):
    """The Pinned section's text, cut out of the file, for a parse of its own."""
    parsed = staleness._parse_pinned_section(text)
    assert parsed is not None
    return parsed[2]


def _reader_views(text, tmp_path, monkeypatch):
    """What each reader sees in `text`: pin headings, entry headings, block
    count and the block signal's verdict."""
    found = _found(text)
    assert found is not None
    doc, located, (first, last) = found
    pins = section_pins(doc, located)
    entries = staleness._entry_rows(doc.find_lines(_PIN_HEADING_ROW, (first, last)), last)
    blocks = [archive_pin.extract_pin_block(doc, first, last, i, pins) for i in range(len(pins))]
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(text, encoding="utf-8")
    monkeypatch.setattr(check_pin_caps, "get_project_claude_md_path", lambda: claude_md)
    caps_pins, _reason = check_pin_caps._resolve_pins()
    return {
        "pins": [p.heading for p in pins],
        "entries": [doc.lines[heading].content for heading, _ in entries],
        "blocks": blocks,
        "check_pin_caps": [p.heading for p in caps_pins],
        "signal": staleness.check_pinned_block_signal(claude_md) is not None,
        "stale": sum(p.is_stale for p in pins),
    }


def test_every_reader_reads_the_pins_section_pins_reads(tmp_path, monkeypatch):
    checked = 0
    for name, text in _population().items():
        if _found(text) is None:
            continue
        views = _reader_views(text, tmp_path, monkeypatch)
        if not views["pins"]:
            continue
        checked += 1
        assert views["entries"] == views["pins"], name
        assert views["check_pin_caps"] == views["pins"], name
        assert views["signal"] == (views["stale"] >= PIN_STALE_BLOCK_THRESHOLD), name
        # The archive blocks tile the body from the first pin's span to its
        # end, each holding its own pin's heading.
        found = _found(text)
        assert found is not None
        doc, _located, (first, last) = found
        start = archive_pin._span_start(doc, first, doc.find_lines(_PIN_HEADING_ROW, (first, last))[0])
        assert "".join(views["blocks"]) == text[start:doc.lines[last].end], name
        for block, heading in zip(views["blocks"], views["pins"]):
            assert heading in block, name
    assert checked >= 40, f"only {checked} documents with a FOUND Pinned section and pins"


# A `<!--` opened above the Pinned section and never closed. The whole file
# reads every later row as prose, so the fenced `### ` line in pin A is a pin of
# its own, and its date makes it a stale entry. The section's text parsed on its
# own has no comment above it and reads the fence as code: two pins, nothing
# stale.
_OPEN_COMMENT_FILE = (
    "# Notes\n<!-- a note that never closes\n\n## Pinned Context\n\n"
    "### A\nbody from 2020-01-01\n```md\n### fenced example 2020-01-01\n```\n\n"
    "### B\nbody b\n"
)


def test_the_readers_read_the_whole_file_where_the_section_text_reads_differently(
        tmp_path, monkeypatch):
    """Every reader reads the whole file's three pins, and the staleness
    markings find its one stale entry, where the section's text parsed on its
    own reads two pins and nothing stale."""
    text = _OPEN_COMMENT_FILE
    found = _found(text)
    assert found is not None, "the shape no longer reads as a FOUND Pinned section"
    doc, _located, (first, last) = found
    views = _reader_views(text, tmp_path, monkeypatch)
    assert views["pins"] == ["### A", "### fenced example 2020-01-01", "### B"]
    assert views["entries"] == views["pins"] == views["check_pin_caps"]
    assert len(views["blocks"]) == 3
    stale = staleness.detect_stale_entries(doc, first, last)
    assert [heading for _, _, heading in stale] == ["### fenced example 2020-01-01"]
    _, stale_count, modified, _ = staleness.apply_staleness_markings(text, doc, first, last)
    assert (stale_count, modified) == (1, True)
    section = _section_text(text)
    sliced = parse(section)
    assert [p.heading for p in parse_pins(section)] == ["### A", "### B"], (
        "the shape no longer separates the whole file from its section text")
    assert staleness.detect_stale_entries(sliced, 0, len(sliced.lines) - 1) == []


def test_the_block_signal_counts_the_pins_section_pins_reads(tmp_path, monkeypatch):
    """No file separates the block signal's two readings: the pins only the
    whole file reads sit below a never-closed comment, and a STALE marker on
    one of them would end that comment and leave the section unreadable. So
    the signal is tied to its source: it counts the pins `section_pins` reads
    from the whole-file parse."""
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(_OPEN_COMMENT_FILE, encoding="utf-8")
    seen = []

    def _all_stale(doc, located):
        seen.append(doc.text)
        return [pin._replace(is_stale=True) for pin in section_pins(doc, located)]

    monkeypatch.setattr(staleness, "section_pins", _all_stale)
    assert staleness.check_pinned_block_signal(claude_md) is not None
    assert seen == [_OPEN_COMMENT_FILE]


def test_the_archive_index_read_takes_the_whole_files_pins(tmp_path, monkeypatch):
    """archive_pin's verdict names the whole file's pin at each index: index 1
    is the fenced example there, where the section's text read alone has
    `### B`."""
    text = _OPEN_COMMENT_FILE
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(text, encoding="utf-8")
    monkeypatch.setattr(archive_pin, "resolve_claude_md", lambda: (claude_md, tmp_path))

    def _stop(*_args, **_kwargs):
        raise archive_pin._Unevaluable("stopped before the save")

    monkeypatch.setattr(archive_pin, "_run_memory_cli", _stop)
    verdict = archive_pin.build_verdict(1, db_path=None)
    assert verdict["heading"] == "fenced example 2020-01-01"
    assert verdict["delete_string"] == "### fenced example 2020-01-01\n```\n\n"
    assert parse_pins(_section_text(text))[1].heading == "### B", (
        "the shape no longer shifts the slice's pin at index 1")
