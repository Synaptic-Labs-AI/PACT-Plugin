#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/shared/edit_simulation.py

Summary: The text a CLAUDE.md holds after an Edit or Write runs, built from
the text before and the tool payload, so a PreToolUse gate can judge the file
the tool will produce. Pure, stdlib only.

Used by: pin_caps_gate (`gate_decision`) and pin_staleness_gate
(`_simulate_post_edit_document`). Both gates call this one function, so they
read the same post-edit document for the same payload.

What the Edit tool does that a literal replace misses:
- An empty `old_string` creates the file, or fills one that is blank
  (`is_blank`), with `new_string`. On any other file the tool refuses it.
- When `old_string` does not occur as written, the tool still matches it with
  curly quotes read as straight ones (U+2018/U+2019 as ', U+201C/U+201D as ").
  Folding replaces one character with one, so an offset in the folded text is
  the same offset in the original.
"""

from __future__ import annotations

from typing import Optional

# What the tool's blank test (JavaScript's String.prototype.trim) removes. It
# is not Python's str.strip() set: trim() removes U+FEFF, which strip() keeps,
# and strip() removes U+0085 and U+001C-U+001F, which trim() keeps.
_TOOL_BLANK = (
    "\t\n\x0b\x0c\r \u00a0\u1680"
    + "".join(chr(code) for code in range(0x2000, 0x200B))
    + "\u2028\u2029\u202f\u205f\u3000\ufeff"
)

_QUOTE_FOLD = str.maketrans({
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
})


def is_blank(text: str) -> bool:
    """True when the Edit tool reads `text` as an empty file: it holds
    nothing but the characters JavaScript's trim() removes, a byte-order mark
    included."""
    return text.strip(_TOOL_BLANK) == ""


def simulate(before: str, tool_name: str, tool_input: dict) -> Optional[str]:
    """The file after `tool_name` runs with `tool_input` on a file holding
    `before` ("" when there is none), or None when the payload is not a
    well-formed Edit or Write.

    Write: its `content`. Edit: `old_string` replaced by `new_string`, at the
    first site or, with `replace_all`, at every site. When `old_string` is not
    in `before`, its curly-quote fold is looked for in the fold of `before`;
    on a hit, the original text at that first position is what gets replaced,
    as written, so `new_string` is used as given. With neither, the tool
    fails and the file is unchanged.
    """
    if not isinstance(before, str) or not isinstance(tool_input, dict):
        return None
    if tool_name == "Write":
        content = tool_input.get("content")
        return content if isinstance(content, str) else None
    if tool_name != "Edit":
        return None
    old_string = tool_input.get("old_string")
    new_string = tool_input.get("new_string")
    if not isinstance(old_string, str) or not isinstance(new_string, str):
        return None
    if old_string == "":
        return new_string if is_blank(before) else before
    if old_string not in before:
        at = before.translate(_QUOTE_FOLD).find(old_string.translate(_QUOTE_FOLD))
        if at == -1:
            return before
        old_string = before[at:at + len(old_string)]
    if tool_input.get("replace_all", False):
        return before.replace(old_string, new_string)
    return before.replace(old_string, new_string, 1)
