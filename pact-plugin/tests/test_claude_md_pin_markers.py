"""
Location: pact-plugin/tests/test_claude_md_pin_markers.py

Summary: The pin-marker planner and writer, and working_memory's write window
and Retrieved Context anchor, locate every marker and section through the
fence-aware parser (hooks/shared/claude_md_markers.py).

Each row drives a whole document. A fenced, commented-out or indented copy of
a heading or marker is an example: it is never the anchor, and its bytes come
out unchanged. A lookup the parser cannot answer with certainty refuses, names
the line, and leaves the file byte-identical.

SAFETY: every row that touches disk pins CLAUDE_PROJECT_DIR (and HOME for the
subprocess) to tmp_path, so no resolver can reach a real CLAUDE.md.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from shared.claude_md_manager import (
    MANAGED_START_MARKER,
    MEMORY_END_MARKER,
    MEMORY_START_MARKER,
    PINNED_END_MARKER,
    PINNED_START_MARKER,
)
from shared.claude_md_markers import Cause, State
from shared.pin_markers import (
    END_LINE,
    START_LINE,
    Insertion,
    Refusal,
    SkipReason,
    apply_insertion,
    certify_expel_nothing,
    plan_insertion,
)
from tests import test_pin_marker_writer as _writer_tests

build_claude_md = _writer_tests.build_claude_md
production_head_and_tail = _writer_tests.production_head_and_tail

SKILL_ROOT = Path(__file__).resolve().parent.parent / "skills" / "pact-memory"
FENCED_HEADING_EXAMPLE = "```markdown\n## Pinned Context\n\n### An example pin\n```\n"
FENCED_MARKER_EXAMPLE = f"```\n{PINNED_START_MARKER}\n## Pinned Context\n{PINNED_END_MARKER}\n```\n"


def _marked(doc: str) -> str:
    planned = plan_insertion(doc)
    assert isinstance(planned, Insertion), f"expected an insertion, got {planned!r}"
    new = apply_insertion(doc, planned)
    assert certify_expel_nothing(doc, new, planned)
    return new


def _refusal(doc: str, state: State, cause: Cause | None = None) -> Refusal:
    planned = plan_insertion(doc)
    assert isinstance(planned, Refusal), f"expected a refusal, got {planned!r}"
    assert planned.located.state is state
    if cause is not None:
        assert planned.located.cause is cause
    assert re.search(r"\blines? \d", planned.located.reason), planned.located.reason
    assert planned.value.startswith(f"refused_{state.value.lower()}: ")
    return planned


def _real_heading(new: str) -> int:
    """Offset of the `## Pinned Context` heading line that is not inside a fence."""
    return new.index("\n## Pinned Context\n", new.index("<!-- PACT_MEMORY_START -->")) + 1


# --------------------------------------------------------------------------
# Fenced examples stay examples
# --------------------------------------------------------------------------

class TestFencedExamplesAreNeverAnchors:

    def test_a_fenced_heading_above_the_real_section_is_left_byte_identical(self):
        doc = build_claude_md(retrieved=f"\n### 2026-01-01\nAn entry quoting:\n{FENCED_HEADING_EXAMPLE}\n")
        new = _marked(doc)
        assert new.count(FENCED_HEADING_EXAMPLE) == 1
        start = new.index(START_LINE)
        assert start > new.index(FENCED_HEADING_EXAMPLE) + len(FENCED_HEADING_EXAMPLE)
        assert new[start + len(START_LINE):].startswith("## Pinned Context\n\n### A pin\n")

    def test_a_fenced_marker_example_in_a_pin_body_neither_blocks_nor_anchors(self):
        doc = build_claude_md(pinned_body=f"### A pin\nThe markers look like:\n{FENCED_MARKER_EXAMPLE}\n")
        new = _marked(doc)
        assert new.count(FENCED_MARKER_EXAMPLE) == 1
        start, end = new.index(START_LINE), new.index(END_LINE + "## Working Memory")
        assert start < new.index(FENCED_MARKER_EXAMPLE) < end
        assert plan_insertion(new) is SkipReason.ALREADY_MARKED

    @pytest.mark.parametrize("fenced_line", ["## Not a terminator", "### Not a pin", "# Nor an H1"])
    def test_a_fenced_heading_line_in_a_pin_body_does_not_end_the_section(self, fenced_line):
        body = f"### A pin\n```text\n{fenced_line}\n```\nStill the same pin.\n\n"
        new = _marked(build_claude_md(pinned_body=body))
        end = new.index(END_LINE)
        assert new[end + len(END_LINE):].startswith("## Working Memory\n")
        assert new.index("Still the same pin.") < end

    @pytest.mark.parametrize("body, cause", [
        ("### A pin\n```\nnever closed\n", Cause.UNCLOSED_FENCE),
        ("### A pin\n- ```bash\nls\n```\n", Cause.CONTAINER_FENCE),
        ("### A pin\n> ```\nquoted\n", Cause.CONTAINER_FENCE),
    ])
    def test_an_uncertain_fence_in_a_pin_body_refuses(self, body, cause):
        _refusal(build_claude_md(pinned_body=body), State.UNKNOWN, cause)

    def test_a_block_above_an_unclosed_fence_at_end_of_file_is_still_written(self):
        doc = build_claude_md(user_suffix="\nUser notes:\n```\nnever closed\n")
        assert new_has_one_pair(_marked(doc))


def new_has_one_pair(text: str) -> bool:
    return text.count(START_LINE) == 1 and text.count(END_LINE) == 1


# --------------------------------------------------------------------------
# Hidden, duplicate and stray shapes
# --------------------------------------------------------------------------

class TestUncertainShapesRefuse:

    def test_a_heading_only_inside_a_comment_refuses_and_names_it(self):
        head, tail = production_head_and_tail()
        doc = head + "<!--\n## Pinned Context\n\n### An old pin\n-->\n## Working Memory\n" + tail
        refused = _refusal(doc, State.UNKNOWN, Cause.COMMENTED)
        assert "inside an HTML block" in refused.located.reason

    def test_a_commented_out_old_heading_above_the_real_one_is_ignored(self):
        head, tail = production_head_and_tail()
        doc = (head + "<!--\n## Pinned Context\n\n### An old pin\n-->\n"
               "## Pinned Context\n\n### A pin\n\n## Working Memory\n" + tail)
        new = _marked(doc)
        start = new.index(START_LINE)
        assert start > new.index("### An old pin\n-->\n")
        assert new[start + len(START_LINE):].startswith("## Pinned Context\n\n### A pin\n")

    def test_two_memory_blocks_refuse_as_duplicate(self):
        head, tail = production_head_and_tail()
        second = f"{MEMORY_START_MARKER}\n## Pinned Context\n\n### Again\n{MEMORY_END_MARKER}\n"
        doc = head + "## Pinned Context\n\n### A pin\n\n" + tail.replace(
            MEMORY_END_MARKER + "\n", MEMORY_END_MARKER + "\n" + second, 1)
        _refusal(doc, State.DUPLICATE, Cause.DUPLICATE)

    def test_two_marker_pairs_in_the_memory_block_refuse_as_duplicate(self):
        body = (f"### A pin\n\n{PINNED_START_MARKER}\n{PINNED_END_MARKER}\n"
                f"{PINNED_START_MARKER}\n{PINNED_END_MARKER}\n")
        _refusal(build_claude_md(pinned_body=body), State.DUPLICATE, Cause.DUPLICATE)

    def test_a_mid_line_marker_mention_refuses_as_stray(self):
        """Intended change: the old writer let a mid-line copy through; the
        parser's stray rule reads marker text off a marker line as MALFORMED."""
        body = f"### A pin\nSee {PINNED_START_MARKER} in the docs.\n\n"
        _refusal(build_claude_md(pinned_body=body), State.MALFORMED, Cause.STRAY)

    def test_a_backticked_marker_mention_does_not_refuse(self):
        body = f"### A pin\nSee `{PINNED_START_MARKER}` in the docs.\n\n"
        assert new_has_one_pair(_marked(build_claude_md(pinned_body=body)))

    def test_a_marker_indented_four_spaces_refuses_as_stray(self):
        """Intended change: the old stripped comparison accepted any indent;
        a marker line takes at most 3 spaces, so 4 is stray text."""
        body = f"### A pin\n\n    {PINNED_END_MARKER}\n"
        _refusal(build_claude_md(pinned_body=body), State.MALFORMED, Cause.STRAY)

    def test_a_marker_line_outside_the_memory_block_is_ignored(self):
        """Intended change: the old writer searched the whole file and called an
        own-line copy anywhere a collision. The pair lives in the memory block,
        where every pin reader looks, so a copy in the user's own prose is left
        alone and the real pair still goes in."""
        prefix = f"# My notes\n\n{PINNED_START_MARKER}\n\n"
        new = _marked(build_claude_md(user_prefix=prefix))
        assert new.startswith(prefix)
        assert new.count(START_LINE) == 2 and new.count(END_LINE) == 1


class TestPairStates:

    def test_a_lone_marker_is_unpaired(self):
        body = f"### A pin\n\n{PINNED_END_MARKER}\n"
        doc = build_claude_md(pinned_body=body)
        assert plan_insertion(doc) is SkipReason.UNPAIRED

    def test_an_end_above_the_start_is_inverted(self):
        head, tail = production_head_and_tail()
        doc = (head + f"{PINNED_END_MARKER}\n## Pinned Context\n\n### A pin\n\n"
               f"{PINNED_START_MARKER}\n## Working Memory\n" + tail)
        assert plan_insertion(doc) is SkipReason.INVERTED_PAIR

    def test_a_pair_off_the_writers_rows_is_a_collision(self):
        body = f"### A pin\n{PINNED_START_MARKER}\n{PINNED_END_MARKER}\n\n"
        assert plan_insertion(build_claude_md(pinned_body=body)) is SkipReason.MARKER_COLLISION

    def test_four_passes_yield_exactly_one_pair(self):
        doc = build_claude_md(retrieved=f"\n### 2026-01-01\n{FENCED_HEADING_EXAMPLE}\n")
        for _ in range(4):
            planned = plan_insertion(doc)
            if isinstance(planned, Insertion):
                doc = apply_insertion(doc, planned)
        assert new_has_one_pair(doc)
        assert plan_insertion(doc) is SkipReason.ALREADY_MARKED


# --------------------------------------------------------------------------
# The writer, on disk
# --------------------------------------------------------------------------

def _project(tmp_path, monkeypatch, text: str) -> Path:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    target = tmp_path / ".claude" / "CLAUDE.md"
    target.parent.mkdir()
    target.write_text(text, encoding="utf-8")
    return target


class TestTheWriterOnDisk:

    def test_a_refusal_leaves_the_file_byte_identical_and_names_the_line(self, tmp_path, monkeypatch):
        from pin_marker_writer import _plan_and_write

        target = _project(tmp_path, monkeypatch, build_claude_md(pinned_body="### A pin\n```\nopen\n"))
        before = target.read_bytes()
        outcome = _plan_and_write()
        assert outcome.startswith("refused_unknown: "), outcome
        assert re.search(r"\blines? \d", outcome), outcome
        assert target.read_bytes() == before

    def test_the_writer_marks_the_real_section_and_is_idempotent(self, tmp_path, monkeypatch):
        from pin_marker_writer import _plan_and_write

        doc = build_claude_md(retrieved=f"\n### 2026-01-01\n{FENCED_HEADING_EXAMPLE}\n")
        target = _project(tmp_path, monkeypatch, doc)
        assert _plan_and_write() == "written"
        written = target.read_text(encoding="utf-8")
        assert written == _marked(doc)
        assert _plan_and_write() == SkipReason.ALREADY_MARKED.value
        assert target.read_text(encoding="utf-8") == written


# --------------------------------------------------------------------------
# working_memory: the import, the write window, the Retrieved Context anchor
# --------------------------------------------------------------------------

class TestWorkingMemoryUsesTheFinder:

    def test_working_memory_imports_the_finder_from_a_cli_shaped_path(self, tmp_path):
        """The production entry puts ONLY the skill root on sys.path. Importing
        working_memory there must reach the same finder through pact_session's
        bootstrap, with hooks/ absent from the path beforehand."""
        code = (
            "import sys\n"
            "assert not any(p.rstrip('/').endswith('/hooks') for p in sys.path), sys.path\n"
            "import scripts.working_memory as wm\n"
            "import shared.claude_md_markers as finder\n"
            "assert wm.parse is finder.parse\n"
            "print('finder:', finder.__file__)\n"
        )
        env = {key: value for key, value in os.environ.items()
               if key != "PYTHONPATH" and not key.startswith("CLAUDE_")}
        env.update(PYTHONPATH=str(SKILL_ROOT), HOME=str(tmp_path), CLAUDE_PROJECT_DIR=str(tmp_path))
        result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env,
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        expected = SKILL_ROOT.parent.parent / "hooks" / "shared" / "claude_md_markers.py"
        assert result.stdout.strip() == f"finder: {expected}"

    def test_the_write_window_ignores_a_fenced_copy_of_the_whole_block(self):
        from scripts.working_memory import _resolve_write_scope, parse

        real = build_claude_md()
        fenced_copy = "```markdown\n" + real + "```\n\n"
        doc = parse(fenced_copy + real)
        scope = _resolve_write_scope(doc)
        assert scope is not None
        first, last = scope
        real_memory_row = (fenced_copy + real[:real.index(MEMORY_START_MARKER)]).count("\n")
        assert first == real_memory_row + 1
        assert doc.lines[real_memory_row].content == MEMORY_START_MARKER
        assert doc.lines[first].content == "## Retrieved Context"
        assert doc.lines[last + 1].content == MEMORY_END_MARKER

    @pytest.mark.parametrize("label, doc", [
        ("unknown", "```\nopen fence above the block\n\n" + build_claude_md()),
        ("stray", f"Prose naming {MANAGED_START_MARKER} mid-line.\n\n" + build_claude_md()),
        ("duplicate", build_claude_md().replace(
            MEMORY_END_MARKER + "\n",
            f"{MEMORY_END_MARKER}\n{MEMORY_START_MARKER}\n{MEMORY_END_MARKER}\n", 1)),
        # Intended change: a managed start with no end used to read as "no
        # managed block" and sync over the whole file; it is now MALFORMED.
        ("unpaired", f"preamble\n{MANAGED_START_MARKER}\n## Working Memory\n"),
    ])
    def test_the_write_window_declines_an_uncertain_block(self, label, doc):
        from scripts.working_memory import _resolve_write_scope, parse

        assert _resolve_write_scope(parse(doc)) is None, label

    @pytest.mark.parametrize("doc", ["", "no markers at all\n", "# Title\n\n## Working Memory\n"])
    def test_a_file_with_no_managed_block_keeps_the_whole_file_window(self, doc):
        from scripts.working_memory import _resolve_write_scope, parse

        parsed = parse(doc)
        assert _resolve_write_scope(parsed) == (0, len(parsed.lines) - 1)
        assert "".join(line.content + "\n" for line in parsed.lines) == doc

    def test_retrieved_context_goes_above_the_real_working_memory_heading(self, tmp_path, monkeypatch):
        from scripts.working_memory import sync_retrieved_to_claude_md

        fenced = "```markdown\n## Working Memory\n```\n"
        head, tail = production_head_and_tail()
        doc = head + f"## Pinned Context\n\n### A pin\n{fenced}\n## Working Memory\n\n### 2026-01-02\nW.\n" + tail
        root = tmp_path / "project"
        root.mkdir()
        (root / "CLAUDE.md").write_text(doc, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))

        result = sync_retrieved_to_claude_md(
            [{"id": "m1", "context": "c", "goal": "g"}], "a query", None, ["m1"], claude_md_root=root)

        assert result, result
        written = (root / "CLAUDE.md").read_text(encoding="utf-8")
        assert written.count(fenced) == 1
        retrieved = written.index("## Retrieved Context\n")
        assert written.index(fenced) < retrieved < written.index("\n## Working Memory\n\n### 2026-01-02")

    def test_a_working_memory_heading_outside_the_memory_block_is_not_the_anchor(
        self, tmp_path, monkeypatch
    ):
        """The anchor is looked up in the write window, so a user's own
        `## Working Memory` heading above the managed block is never the place
        the Retrieved Context section goes."""
        from scripts.working_memory import sync_retrieved_to_claude_md

        head, tail = production_head_and_tail()
        user = "# My notes\n\n## Working Memory\n\nMy own section.\n\n"
        doc = user + head + "## Pinned Context\n\n### A pin\n\n## Working Memory\n\n### 2026-01-02\nW.\n" + tail
        root = tmp_path / "project"
        root.mkdir()
        (root / "CLAUDE.md").write_text(doc, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))

        result = sync_retrieved_to_claude_md(
            [{"id": "m1", "context": "c", "goal": "g"}], "a query", None, ["m1"], claude_md_root=root)

        assert result, result
        written = (root / "CLAUDE.md").read_text(encoding="utf-8")
        assert written.startswith(user)
        assert written.index("## Retrieved Context\n") > written.index(MEMORY_START_MARKER)

    def test_a_commented_only_working_memory_heading_refuses(self, tmp_path, monkeypatch):
        from scripts.working_memory import SyncResult, sync_retrieved_to_claude_md

        head, tail = production_head_and_tail()
        doc = head + "## Pinned Context\n\n### A pin\n\n<!--\n## Working Memory\nold\n-->\n" + tail
        root = tmp_path / "project"
        root.mkdir()
        (root / "CLAUDE.md").write_text(doc, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
        before = (root / "CLAUDE.md").read_bytes()

        result = sync_retrieved_to_claude_md(
            [{"id": "m1", "context": "c", "goal": "g"}], "a query", None, ["m1"], claude_md_root=root)

        assert result.reason == SyncResult.UNCERTAIN
        assert (root / "CLAUDE.md").read_bytes() == before
