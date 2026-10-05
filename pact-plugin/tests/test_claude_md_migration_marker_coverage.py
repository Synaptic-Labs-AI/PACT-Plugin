"""
Location: pact-plugin/tests/test_claude_md_migration_marker_coverage.py
Summary: Every block-marker literal the shipped code spells is checked by the
         migration's read-back, or excluded from it with a reason.
Used by: pytest.

`_plan_migration` refuses a rebuilt file unless the managed, memory and
session blocks read back as they must, and every other marker it carries
reads the same as in the input. A marker literal added later and named in
none of those checks would slip past the read-back with nothing red, so this
pin scans the shipped sources and fails on any marker literal it cannot place.
"""

import ast
import collections
import functools
import re

from shared.claude_md_manager import (
    _MIGRATION_CARRIED_MARKERS,
    _ROUTING_END_MARKER,
    _ROUTING_START_PREFIX,
    MANAGED_END_MARKER,
    MANAGED_START_MARKER,
    MEMORY_END_MARKER,
    MEMORY_START_MARKER,
    RETRIEVED_CONTEXT_COMMENT,
    SESSION_END_MARKER,
    SESSION_START_MARKER,
    WORKING_MEMORY_COMMENT,
)
from test_claude_md_locator_census import LINE_DATA, _shipped_sources

# A comment opener with content, at the start of a line of a string literal.
_MARKER_LINE = re.compile(r"(?m)^ {0,3}(<!--[ \t]*\S[^\n]*)")

READ_BACK = frozenset({
    MANAGED_START_MARKER, MANAGED_END_MARKER,    # the managed block reads FOUND
    MEMORY_START_MARKER, MEMORY_END_MARKER,      # the memory block reads FOUND
    SESSION_START_MARKER, SESSION_END_MARKER,    # the session block reads as extracted
    *_MIGRATION_CARRIED_MARKERS,                 # each reads as in the input
    _ROUTING_START_PREFIX, _ROUTING_END_MARKER,  # the routing block reads as in the input
})

_SECTION_COMMENT = "a section comment: the rebuild adds it when a section lacks one"
EXCLUDED = {
    RETRIEVED_CONTEXT_COMMENT: _SECTION_COMMENT,
    WORKING_MEMORY_COMMENT: _SECTION_COMMENT,
    "<!-- Auto-managed by session_init hook. Overwritten each session. -->": _SECTION_COMMENT,
}


def marker_literals(sources):
    """Each marker literal in `sources` ({path: text}), with where it is spelled.

    A marker literal is a line of a string literal (docstrings aside) that
    opens a comment with content. The per-pin line data (the STALE mark, the
    budget warning and the pin date comment) is left out: readers locate it by
    position inside a pin, never as a block marker.
    """
    found = collections.defaultdict(list)
    for rel, source in sources.items():
        tree = ast.parse(source)
        docstrings = {
            id(node.body[0].value) for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docstrings):
                for match in _MARKER_LINE.finditer(node.value):
                    if not LINE_DATA.search(match.group(1)):
                        found[match.group(1)].append(f"{rel}:{node.lineno}")
    return found


@functools.lru_cache(maxsize=1)
def _shipped_literals():
    return dict(marker_literals(_shipped_sources()))


def _unplaced(found):
    return {literal: where for literal, where in found.items()
            if literal not in READ_BACK and literal not in EXCLUDED}


def test_every_marker_literal_is_read_back_or_excluded():
    unplaced = _unplaced(_shipped_literals())
    assert not unplaced, (
        "marker literals the migration's read-back does not check: "
        f"{unplaced}. Add each to the read-back in _plan_migration, or to "
        "EXCLUDED here with the reason it may change on an honest migration.")


def test_the_scan_finds_every_literal_this_file_places():
    missing = (READ_BACK | EXCLUDED.keys()) - _shipped_literals().keys()
    assert not missing, f"placed here but spelled nowhere in the shipped code: {missing}"


def test_a_new_marker_literal_is_reported():
    # A marker in a plain literal or on a template line is reported; one in a
    # docstring, and per-pin line data, are not.
    sources = {
        "hooks/fake.py": (
            'NEW = "<!-- PACT_NEW_BLOCK_START -->"\n'
            'TEMPLATE = f"""{NEW}\n<!-- SESSION_NEW_FIELD -->\n"""\n'
            'def f():\n    """Mentions a marker.\n\n<!-- PACT_IN_A_DOCSTRING -->\n"""\n'
            'STALE = "<!-- STALE: Last relevant "\n'
        ),
    }
    assert set(_unplaced(marker_literals(sources))) == {
        "<!-- PACT_NEW_BLOCK_START -->", "<!-- SESSION_NEW_FIELD -->"}
