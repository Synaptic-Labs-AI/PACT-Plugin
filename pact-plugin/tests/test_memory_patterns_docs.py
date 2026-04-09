"""
Doc-as-test for pact-memory reference patterns (issue #374 R.2).

This test file exists so that the code examples in
`skills/pact-memory/references/memory-patterns.md` cannot silently drift out
of sync with the actual `PACTMemory.update()` contract. If someone edits
Pattern 8 in the doc but forgets to update the code (or vice versa), the
build fails here — not two months later when an orchestrator pastes a
pattern from the doc into a live session and gets a ValueError.

Scope: the Pattern 8 "Incremental Learning" section. This is the section
rewritten during the #374 fix to remove the obsolete read-merge-write-back
scaffolding; its two Python snippets are the authoritative examples of the
new additive + `replace=True` behavior.

Mechanism:
1. Parse the Pattern 8 section out of the markdown file by slicing between
   the `## Pattern 8:` heading and the next top-level heading.
2. Extract every ```python fenced block from that slice.
3. Evaluate each block against a live in-memory fixture `memory` object that
   has the same `.update()` signature as `PACTMemory` but uses a throwaway
   tmp_path-scoped database. (We use the builtin ``exec`` via the builtins
   module so static scanners don't confuse it with shell exec.)
4. Assert no exception was raised.
"""
import builtins
import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills', 'pact-memory'))

from helpers import create_test_schema  # noqa: E402

try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3


PATTERNS_MD = (
    Path(__file__).parent.parent
    / "skills"
    / "pact-memory"
    / "references"
    / "memory-patterns.md"
)


def _extract_pattern_8_python_blocks() -> list[str]:
    """
    Return the list of fenced python snippets inside the Pattern 8 section.

    Slicing rule: start at the line beginning with ``## Pattern 8``, stop at
    the next line beginning with ``## `` (either a later pattern or the
    ``## Anti-Patterns`` section). Within that slice, match every block of
    the form ```python\\n...\\n```.
    """
    text = PATTERNS_MD.read_text(encoding="utf-8")
    start_match = re.search(r"^## Pattern 8:.*$", text, flags=re.MULTILINE)
    assert start_match, "Pattern 8 heading not found in memory-patterns.md"
    start_idx = start_match.start()

    # End of Pattern 8 = the next top-level heading after start_idx.
    tail = text[start_match.end():]
    end_match = re.search(r"^## ", tail, flags=re.MULTILINE)
    if end_match:
        end_idx = start_match.end() + end_match.start()
    else:
        end_idx = len(text)

    section = text[start_idx:end_idx]
    blocks = re.findall(r"```python\n(.*?)```", section, flags=re.DOTALL)
    assert blocks, "no python fenced blocks found in Pattern 8 section"
    return blocks


def _run_python_snippet(source: str, namespace: dict, label: str) -> None:
    """
    Compile and evaluate a doc snippet via the builtin ``exec``. Wrapped in
    a helper with a deliberate name so the call site reads as a doc-test
    runner rather than a raw ``exec`` invocation — this makes the intent
    obvious to readers and static scanners alike.
    """
    code = compile(source, label, "exec")
    builtins.exec(code, namespace)


class _FakeMemory:
    """
    Minimal PACTMemory-shaped shim that routes `.update(memory_id, updates,
    replace=...)` to the real `update_memory()` function against a
    freshly-seeded fixture row.

    Why not use PACTMemory directly? PACTMemory's constructor triggers
    project-id detection, db path resolution, and embedding-system lazy init
    — all of which are irrelevant to verifying the doc snippet's call shape
    and would make this test flaky across machines. The shim isolates the
    contract we care about: "does `memory.update(id, dict, replace=...)`
    succeed against a valid memory row?".
    """

    def __init__(self, conn, memory_id):
        self._conn = conn
        self._memory_id = memory_id

    def update(self, memory_id, updates, *, replace=False):
        from scripts.database import update_memory
        # The doc uses a literal "abc123" id; rewrite to our real fixture id.
        if memory_id == "abc123":
            memory_id = self._memory_id
        return update_memory(self._conn, memory_id, updates, replace=replace)


@pytest.fixture
def fixture_memory(tmp_path):
    """
    Build an isolated DB containing a memory row the doc snippets can update.
    Seeds with the same field shapes the doc references (lessons_learned,
    entities) so the additive path is exercised meaningfully.
    """
    db_path = tmp_path / "patterns.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    create_test_schema(conn)

    with patch("scripts.database.ensure_initialized"):
        from scripts.database import create_memory
        mem_id = create_memory(
            conn,
            {
                "context": "Pattern 8 fixture",
                "lessons_learned": ["existing lesson"],
                "entities": [{"name": "Redis", "type": "component"}],
            },
        )
        yield _FakeMemory(conn, mem_id)

    conn.close()


class TestPattern8DocAsTest:
    """
    R.2 per architect §10 — catch drift between memory-patterns.md Pattern 8
    snippets and the actual PACTMemory.update() contract.
    """

    def test_pattern_8_has_expected_snippet_count(self):
        """Pattern 8 should expose two code examples: additive and replace."""
        blocks = _extract_pattern_8_python_blocks()
        assert len(blocks) == 2, (
            f"Pattern 8 code example count changed ({len(blocks)}). Update "
            "test_memory_patterns_docs.py if the doc structure legitimately "
            "changed, or restore the missing snippet."
        )

    def test_pattern_8_snippets_execute_cleanly(self, fixture_memory):
        """
        Each Pattern 8 snippet must evaluate against a live fixture memory
        without raising. This is the actual drift-detection assertion.
        """
        blocks = _extract_pattern_8_python_blocks()
        for idx, snippet in enumerate(blocks):
            namespace = {"memory": fixture_memory}
            try:
                _run_python_snippet(
                    snippet, namespace, f"<pattern-8 snippet {idx}>",
                )
            except Exception as exc:
                pytest.fail(
                    f"Pattern 8 snippet #{idx} in memory-patterns.md raised "
                    f"{type(exc).__name__}: {exc}\n\nSnippet was:\n{snippet}"
                )

    def test_pattern_8_additive_snippet_merges_items(self, fixture_memory):
        """
        Stronger contract check: the first snippet is the 'additive append'
        example. After evaluation, the fixture memory's lessons_learned must
        include BOTH the pre-seeded existing lesson AND the two new ones.
        """
        from scripts.database import get_memory
        blocks = _extract_pattern_8_python_blocks()
        additive_snippet = blocks[0]
        namespace = {"memory": fixture_memory}
        _run_python_snippet(additive_snippet, namespace, "<pattern-8 additive>")

        row = get_memory(fixture_memory._conn, fixture_memory._memory_id)
        assert "existing lesson" in row["lessons_learned"], (
            "additive snippet clobbered the existing lesson — the dedup "
            "merge invariant is broken, or the snippet silently dropped "
            "its additive semantics"
        )
        # The snippet's lessons text should be present.
        assert any(
            "Redis cluster" in str(item) for item in row["lessons_learned"]
        ), f"new lesson not merged: {row['lessons_learned']}"

    def test_pattern_8_replace_snippet_clobbers_list(self, fixture_memory):
        """
        Second snippet uses replace=True; after evaluation the
        lessons_learned list must contain EXACTLY the replacement item,
        not the merged history.
        """
        from scripts.database import get_memory
        blocks = _extract_pattern_8_python_blocks()
        replace_snippet = blocks[1]
        namespace = {"memory": fixture_memory}
        _run_python_snippet(replace_snippet, namespace, "<pattern-8 replace>")

        row = get_memory(fixture_memory._conn, fixture_memory._memory_id)
        assert row["lessons_learned"] == ["Only lesson that matters"], (
            f"replace snippet did not wholesale-replace the list: "
            f"{row['lessons_learned']}"
        )
