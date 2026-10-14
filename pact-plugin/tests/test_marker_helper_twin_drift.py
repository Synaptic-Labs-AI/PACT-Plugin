"""One definition of where a PACT marker is: no shipped module keeps its own.

Every reader and writer of a PACT marker in a CLAUDE.md locates it through
hooks/shared/claude_md_markers.py. Two locator helpers used to be copied from
hooks/shared/pin_markers.py into skills/pact-memory/scripts/working_memory.py,
because the production entry `cli.py` puts only the skill root on sys.path and
the copy could not import the original. working_memory now imports the finder
after pact_session, whose sys.path bootstrap adds hooks/, so the copies are
gone.

This gate keeps them gone: no shipped Python file defines a function under one
of the retired locator names, and working_memory's parser IS the finder's, not
a second definition of it. A comparison gate held the two copies equal; this
one holds that there is only one.

The CLI-shaped import (skill root alone on sys.path) is pinned in
tests/test_claude_md_pin_markers.py.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
SHIPPED_TOPS = ("hooks", "skills", "scripts")

# The helpers that each held a line-scanning reading of `the marker occupies a
# line` or `the memory region inside the managed block`, plus working_memory's
# copies of the managed-region extractor and of the section terminator scanner.
RETIRED = ("marker_line_span", "marker_line_offset", "marker_line_present",
           "_narrow_to_memory_region", "_is_already_marked", "_is_end_marked")
RETIRED_IN_WORKING_MEMORY = RETIRED + ("extract_managed_region", "_find_terminator_offset")
WORKING_MEMORY = PLUGIN / "skills" / "pact-memory" / "scripts" / "working_memory.py"


def _shipped_files():
    for top in SHIPPED_TOPS:
        for path in sorted((PLUGIN / top).rglob("*.py")):
            if "__pycache__" not in path.parts and not path.name.startswith("test_"):
                yield path


def _defined_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_the_scan_sees_the_shipped_tree():
    """Non-vacuity: the scan reaches both former homes of the copies and
    reads their definitions."""
    files = {path.relative_to(PLUGIN).as_posix(): path for path in _shipped_files()}
    assert "hooks/shared/pin_markers.py" in files
    assert "skills/pact-memory/scripts/working_memory.py" in files
    assert "plan_insertion" in _defined_functions(files["hooks/shared/pin_markers.py"])
    assert "_resolve_write_scope" in _defined_functions(WORKING_MEMORY)


@pytest.mark.parametrize("name", RETIRED)
def test_no_shipped_module_defines_a_retired_locator(name):
    owners = [path.relative_to(PLUGIN).as_posix() for path in _shipped_files()
              if name in _defined_functions(path)]
    assert not owners, (
        f"{name} is defined again in {owners}. Locate markers through "
        "hooks/shared/claude_md_markers.py instead of a second line scanner."
    )


def test_working_memory_keeps_no_managed_region_copy():
    copies = _defined_functions(WORKING_MEMORY) & set(RETIRED_IN_WORKING_MEMORY)
    assert not copies, f"working_memory.py defines {sorted(copies)} again"


def test_working_memory_parses_with_the_finder_itself():
    import shared.claude_md_markers as finder
    from scripts import working_memory

    assert working_memory.parse is finder.parse
    assert working_memory.State is finder.State
