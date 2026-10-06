"""Migration helpers for tests that state a migration's input and output as text.

Shipped code migrates through `claude_md_manager._plan_migration`, which also
says why it refuses. These helpers wrap the same functions so a test can state
the result in one call.
"""

from shared.claude_md_manager import _drop_spans, _legacy_line_spans, _plan_migration
from shared.claude_md_markers import parse


def _build_migrated_content(content: str) -> str:
    """`content` migrated as `_plan_migration` plans it, or `content`
    unchanged when it is already migrated or the planner refuses it."""
    return _plan_migration(content)[0] or content


def _strip_legacy_lines(content: str) -> str:
    """`content` without the stale orchestrator-loader lines the parser reads
    as visible prose. A fenced, code or uncertain row that quotes one, or a row
    inside an HTML block that hides it, stays byte for byte."""
    return _drop_spans(content, _legacy_line_spans(parse(content)))
