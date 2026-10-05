"""
Location: pact-plugin/tests/test_claude_md_corpus_syncs.py
Summary: Every corpus file through PACT's two memory syncs (Working Memory and
         Retrieved Context in skills/pact-memory/scripts/working_memory.py).
Used by: pytest.

As in test_claude_md_corpus_writers.py, the expected outcome is read from the
corpus's hand-written block states, never computed by the parser. The window a
sync may write is the memory block inside a found managed block, or the whole
file when there is no managed block; an uncertain managed block, or an
uncertain memory block inside a found one, is never written.
"""

import re

import pytest

from scripts.working_memory import sync_retrieved_to_claude_md, sync_to_claude_md
from shared.claude_md_manager import MEMORY_END_MARKER, MEMORY_START_MARKER
from shared.claude_md_markers import parse
from test_claude_md_corpus_writers import _CASES, _CORPUS, _UNCERTAIN, _changed_rows, _state

_MEMORY = {"id": "m1", "context": "a context", "goal": "a goal", "created_at": "2026-01-02T03:04:05+00:00"}
_SYNCS = {
    "working memory": ("## Working Memory", lambda root, path: sync_to_claude_md(
        _MEMORY, target=path, claude_md_root=root)),
    "retrieved context": ("## Retrieved Context", lambda root, path: sync_retrieved_to_claude_md(
        [_MEMORY], "a query", None, ["m1"], claude_md_root=root)),
}


@pytest.mark.parametrize("sync_name", list(_SYNCS))
@pytest.mark.parametrize("case", _CASES)
def test_the_syncs(case, sync_name, tmp_path, monkeypatch):
    """A sync writes only when its window is certain. With no managed block the
    window is the whole file; a managed block the table calls uncertain, or an
    uncertain memory block inside a found one, leaves the file byte-identical.
    A write leaves one real section heading in the window, changes no other
    row's kind or hidden flag, and a repeated Retrieved Context sync of the same
    memory changes nothing."""
    heading, sync = _SYNCS[sync_name]
    raw = (_CORPUS / f"{case}.md").read_bytes()
    path = tmp_path / "CLAUDE.md"
    path.write_bytes(raw)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    memory, managed = _state(case, "MEMORY"), _state(case, "MANAGED")
    result = sync(tmp_path, path)
    after = path.read_bytes()
    if managed in _UNCERTAIN or (managed == "FOUND" and memory in _UNCERTAIN):
        assert not result and after == raw
        return
    if not result:
        assert after == raw
        return
    text, new = raw.decode("utf-8"), after.decode("utf-8")
    doc = parse(new)
    window = None
    if managed == "FOUND":
        block = doc.find_block(MEMORY_START_MARKER, MEMORY_END_MARKER)
        window = (block.spans[0][0] + 1, block.spans[0][1] - 1)
    rows = doc.find_lines(re.compile(re.escape(heading) + r"\s*$"), window)
    assert len([row for row in rows if not doc.lines[row].in_html]) == 1  # a commented-out copy is not one
    assert _changed_rows(text, new)[2] == []
    if sync_name == "retrieved context":
        sync(tmp_path, path)
        assert path.read_bytes() == after
