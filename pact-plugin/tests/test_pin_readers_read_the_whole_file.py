"""
Location: pact-plugin/tests/test_pin_readers_read_the_whole_file.py

Every reader of the Pinned section's pins reads rows of the whole-file parse,
never a parse of the section's text on its own: the staleness readers (the
entries they mark, the stale-pin block signal), archive_pin's index read and
block extraction, and check_pin_caps. A parse that starts mid-file starts in a
state the whole file does not have, so the two can disagree; the readers must
agree with `section_pins` on the whole file.

The population row runs over the fence corpus and the fence oracle's document
generator; wherever the whole file and the section's text both find the
section they read the same pins, so the row's equality carries the guard. The
fixed rows pin what the readers say on a file whose section the whole file
cannot read, and tie the block signal to `section_pins`.
"""

import glob
import random
from pathlib import Path

import archive_pin
import check_pin_caps
import staleness
from pin_caps import PIN_STALE_BLOCK_THRESHOLD, _PIN_HEADING_ROW, section_pins
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


# A `<!--` opened above the Pinned section, never closed, and covering a fence
# in pin A: the file reads as uncertain from the comment down, so no reader can
# tell which of its rows are pins.
_OPEN_COMMENT_FILE = (
    "# Notes\n<!-- a note that never closes\n\n## Pinned Context\n\n"
    "### A\nbody from 2020-01-01\n```md\n### fenced example 2020-01-01\n```\n\n"
    "### B\nbody b\n"
)


def test_every_reader_declines_where_the_whole_file_cannot_read_the_section(
        tmp_path, monkeypatch):
    """The staleness markings write nothing and raise no block signal,
    check_pin_caps reports the section unreadable, and the archive's index read
    finds no section."""
    text = _OPEN_COMMENT_FILE
    assert _found(text) is None
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(text, encoding="utf-8")
    assert staleness.check_pinned_staleness(claude_md) is None
    assert staleness.check_pinned_block_signal(claude_md) is None
    assert claude_md.read_text(encoding="utf-8") == text
    monkeypatch.setattr(check_pin_caps, "get_project_claude_md_path", lambda: claude_md)
    assert check_pin_caps._resolve_pins() == ([], (
        "pinned section unreadable: line 2 starts an uncertain region: "
        "an HTML block is never closed"))
    monkeypatch.setattr(archive_pin, "resolve_claude_md", lambda: (claude_md, tmp_path))

    def _stop(*_args, **_kwargs):
        raise archive_pin._Unevaluable("stopped before the save")

    monkeypatch.setattr(archive_pin, "_run_memory_cli", _stop)
    verdict = archive_pin.build_verdict(1, db_path=None)
    assert (verdict["outcome"], verdict["reason"]) == ("UNEVALUABLE", "no Pinned Context section")


def test_the_block_signal_counts_the_pins_section_pins_reads(tmp_path, monkeypatch):
    """Wherever the whole file and the section's text both find the section
    they read the same pins, so no file separates the block signal's two
    readings. The signal is tied to its source instead: it counts the pins
    `section_pins` reads from the whole-file parse."""
    text = "# Notes\n\n## Pinned Context\n\n### A\nbody a\n\n### B\nbody b\n"
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(text, encoding="utf-8")
    seen = []

    def _all_stale(doc, located):
        seen.append(doc.text)
        return [pin._replace(is_stale=True) for pin in section_pins(doc, located)]

    monkeypatch.setattr(staleness, "section_pins", _all_stale)
    assert staleness.check_pinned_block_signal(claude_md) is not None
    assert seen == [text]
