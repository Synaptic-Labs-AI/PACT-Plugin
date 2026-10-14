"""Pin readers for tests that hold a Pinned section's body as text on its own.

Shipped code reads pins from rows of the whole file's parse
(`pin_caps.pins_in_rows`, `pin_caps.section_pins`). These helpers parse a
fragment by itself with the same fence-aware parser and read it through the
same functions, so a test can state a pin in a few lines.
"""

from typing import List

from pin_caps import Pin, _charge, pins_in_rows
from shared.claude_md_markers import parse


def _extract_body_chars(body: str) -> int:
    """Count body chars excluding auto-generated markers.

    The date comment and STALE marker are plugin-managed — they MUST NOT
    count against the user's 1500-char budget. `body` is parsed on its own;
    see `pin_caps._charge` for what counts.
    """
    doc = parse(body)
    return _charge(doc, 0, len(doc.lines) - 1)


def parse_pins(pinned_content: str) -> List[Pin]:
    """Parse the Pinned Context section body into a list of Pin entries.

    The text is parsed on its own with the fence-aware parser, so a `### `
    line inside a fenced block is body text, not a pin. Never raises on str
    input.

    The pinned_content input is the body AFTER the "## Pinned Context"
    heading (what `staleness._parse_pinned_section` returns in its third
    tuple slot).
    """
    if not pinned_content:
        return []
    doc = parse(pinned_content)
    return pins_in_rows(doc, 0, len(doc.lines) - 1)
