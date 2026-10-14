"""
The post-edit document both CLAUDE.md gates judge, `shared.edit_simulation`.

Unit rows pin `simulate` on its own: a Write, the Edit tool's empty
`old_string` (create or fill a blank file, else refused), the literal replace,
and the curly-quote fold that matches what the tool matches. The gate rows
drive the shipped `pin_caps_gate.py` as a subprocess on a real PreToolUse
frame against files in tmp_path, so each shape is decided as the tool will
apply it. The staleness gate's twin rows live in test_pin_staleness_gate.py.
"""

import pytest

from shared.edit_simulation import is_blank, simulate
from test_pin_caps_gate import _frame, _outcome, _pins, _run_hook  # noqa: E402 — sibling harness reuse

NEW_PIN = "\n\n<!-- pinned: 2026-04-21 -->\n### New\nbody"


def _edit(old, new, replace_all=False):
    return {"old_string": old, "new_string": new, "replace_all": replace_all}


def _gate(project, target, tool, tool_input):
    return _outcome(_run_hook(project, _frame(target, tool, tool_input)))


# ---------------------------------------------------------------------------
# simulate
# ---------------------------------------------------------------------------

def test_a_write_is_its_content():
    assert simulate("old text\n", "Write", {"content": "new text\n"}) == "new text\n"


@pytest.mark.parametrize("before, tool_name, tool_input", [
    ("x", "Write", "not a dict"),
    ("x", "NotebookEdit", {"content": "y"}),
    ("x", "Write", {"content": None}),
    ("x", "Write", {}),
    ("x", "Edit", {"old_string": None, "new_string": "y"}),
    ("x", "Edit", {"old_string": "x", "new_string": 3}),
    (None, "Edit", _edit("x", "y")),
], ids=["tool_input not a dict", "another tool", "content None", "content missing",
        "old_string None", "new_string not str", "before not str"])
def test_a_payload_that_is_not_an_edit_or_write_is_none(before, tool_name, tool_input):
    assert simulate(before, tool_name, tool_input) is None


@pytest.mark.parametrize("text", ["", "\n  \t\n", "\ufeff", "\ufeff\n", "\u00a0", "\u3000\u2028"],
                         ids=["no text", "whitespace only", "byte-order mark", "byte-order mark and newline",
                              "no-break space", "ideographic space and line separator"])
def test_the_tool_reads_these_files_as_blank(text):
    assert is_blank(text)


@pytest.mark.parametrize("text", ["\x85", "\x1c", "x", "\ufeffx"],
                         ids=["next line", "file separator", "text", "byte-order mark and text"])
def test_the_tool_reads_these_files_as_holding_text(text):
    assert not is_blank(text)


@pytest.mark.parametrize("before", ["", "\n  \t\n", "\ufeff", "\ufeff\n"],
                         ids=["no text", "whitespace only", "byte-order mark", "byte-order mark and newline"])
def test_an_empty_old_string_creates_or_fills_a_blank_file(before):
    assert simulate(before, "Edit", _edit("", "### Pin\nbody\n")) == "### Pin\nbody\n"


def test_an_empty_old_string_on_a_next_line_only_file_changes_nothing():
    assert simulate("\x85", "Edit", _edit("", "### Pin\nbody\n")) == "\x85"


def test_an_empty_old_string_on_a_file_with_text_changes_nothing():
    before = "# Project\n\n## Pinned Context\n"
    assert simulate(before, "Edit", _edit("", "### Pin\nbody\n")) == before


def test_replace_all_selects_every_site_or_the_first():
    before = "MARK\nMARK\n"
    assert simulate(before, "Edit", _edit("MARK", "DONE", replace_all=True)) == "DONE\nDONE\n"
    assert simulate(before, "Edit", _edit("MARK", "DONE")) == "DONE\nMARK\n"


def test_a_straight_old_string_matches_curly_text_in_the_file():
    before = "He said “hi” and ‘bye’.\n"
    after = simulate(before, "Edit", _edit("said \"hi\" and 'bye'", "said nothing"))
    assert after == "He said nothing.\n"


def test_a_curly_old_string_matches_straight_text_in_the_file():
    before = "He said \"hi\".\n"
    assert simulate(before, "Edit", _edit("said “hi”", "waved")) == "He waved.\n"


def test_a_literal_match_is_taken_before_the_fold():
    before = "“a” then \"a\"\n"
    assert simulate(before, "Edit", _edit("\"a\"", "B", replace_all=True)) == "“a” then B\n"


def test_replace_all_through_the_fold_replaces_the_text_matched_first():
    # The first folded match is the curly pair; every copy of that exact text is
    # replaced, and a copy with its quotes the other way round is not.
    before = "“a” x “a” y ”a“\n"
    assert simulate(before, "Edit", _edit("\"a\"", "B", replace_all=True)) == "B x B y ”a“\n"
    assert simulate(before, "Edit", _edit("\"a\"", "B")) == "B x “a” y ”a“\n"


def test_an_old_string_that_matches_nowhere_changes_nothing():
    before = "He said “hi”.\n"
    assert simulate(before, "Edit", _edit("said \"bye\"", "x")) == before


# ---------------------------------------------------------------------------
# The cap gate decides the edit the tool makes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pins, expected", [(13, "deny"), (12, "allow")])
def test_an_edit_that_creates_the_file_is_counted_by_its_own_pins(tmp_path, pins, expected):
    target = tmp_path / ".claude" / "CLAUDE.md"
    assert not target.exists()
    assert _gate(tmp_path, target, "Edit", _edit("", _pins(pins))) == expected


@pytest.mark.parametrize("blank", ["\n   \n", "\ufeff", "\ufeff\n"],
                         ids=["whitespace only", "byte-order mark", "byte-order mark and newline"])
def test_an_edit_that_fills_a_blank_file_is_counted_by_its_own_pins(tmp_path, blank):
    target = tmp_path / ".claude" / "CLAUDE.md"
    target.parent.mkdir()
    target.write_text(blank, encoding="utf-8")
    assert _gate(tmp_path, target, "Edit", _edit("", _pins(13))) == "deny"


def test_an_empty_old_string_on_a_file_with_text_is_allowed(tmp_path):
    target = tmp_path / ".claude" / "CLAUDE.md"
    target.parent.mkdir()
    target.write_text(_pins(12), encoding="utf-8")
    assert _gate(tmp_path, target, "Edit", _edit("", NEW_PIN)) == "allow"


@pytest.mark.parametrize("old_string", ["say \"hi\"", "say “hi”"], ids=["straight", "exact curly"])
def test_a_13th_pin_anchored_on_curly_text_is_denied(tmp_path, old_string):
    target = tmp_path / ".claude" / "CLAUDE.md"
    target.parent.mkdir()
    target.write_text(_pins(12, {3: "say “hi”"}), encoding="utf-8")
    assert _gate(tmp_path, target, "Edit", _edit(old_string, old_string + NEW_PIN)) == "deny"


def test_a_13th_pin_anchored_with_curly_quotes_on_straight_text_is_denied(tmp_path):
    target = tmp_path / ".claude" / "CLAUDE.md"
    target.parent.mkdir()
    target.write_text(_pins(12, {3: "say \"hi\""}), encoding="utf-8")
    assert _gate(tmp_path, target, "Edit", _edit("say “hi”", "say \"hi\"" + NEW_PIN)) == "deny"


@pytest.mark.parametrize("replace_all, expected", [(True, "deny"), (False, "allow")])
def test_replace_all_through_the_fold_adds_a_pin_at_every_site(tmp_path, replace_all, expected):
    # 11 pins, two of them holding the same curly text: every site adds a pin
    # (13, over the cap), the first site alone adds one (12, at the cap).
    target = tmp_path / ".claude" / "CLAUDE.md"
    target.parent.mkdir()
    target.write_text(_pins(11, {3: "note “x”", 5: "note “x”"}), encoding="utf-8")
    call = _edit("note \"x\"", "note \"x\"" + NEW_PIN, replace_all=replace_all)
    assert _gate(tmp_path, target, "Edit", call) == expected
