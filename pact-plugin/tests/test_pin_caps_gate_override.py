"""
Which size overrides the pin-cap gate checks: only an override row the change
adds or edits.

An override row is the comment the parser attributes to a pin. The gate looks
its rationale, whitespace collapsed, up among the override comments on every
row of the file before the change, and checks only a rationale it does not
find there. So an old invalid override stays untouched through edits elsewhere
in its pin, a new date on its row, renames, moves, line-ending rewrites and
fence-closing Writes, and a change that adds an invalid override, or edits a
rationale and leaves it invalid, is refused.

Rows drive `pin_caps_gate.gate_decision` on whole documents, the gate's own
decision with no file I/O.
"""

import pytest

from pin_caps_gate import gate_decision

MANAGED = (
    "<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->\n"
    "# PACT Framework and Managed Project Memory\n\n"
)
WM = "## Working Memory"
LONG = "x" * 130


def _pin(n, body=None, override=None, date="2026-10-01"):
    head = (f"<!-- pinned: {date}, pin-size-override: {override} -->" if override is not None
            else f"<!-- pinned: {date} -->")
    return f"{head}\n### Pin {n}\n{body if body is not None else f'Body of pin {n}.'}\n"


def _doc(pins_text):
    return (f"{MANAGED}<!-- PACT_MEMORY_START -->\n## Retrieved Context\n\n## Pinned Context\n\n"
            f"{pins_text}\n{WM}\n<!-- PACT_MEMORY_END -->\n\n<!-- PACT_MANAGED_END -->\n")


def _words(tag, n):
    return ((tag + " ") * (n // (len(tag) + 1))).strip()


def _rest(start=2, stop=6):
    return [_pin(i) for i in range(start, stop)]


OVERSIZE = _doc("\n".join([_pin(1, body=_words("alpha", 1700), override=LONG)] + _rest()))
SMALL = _doc("\n".join([_pin(1, body="short body", override=LONG)] + _rest()))
SMALL_ROW = f"<!-- pinned: 2026-10-01, pin-size-override: {LONG} -->"
VALID = _doc("\n".join([_pin(1, body="short body", override="verbatim form")] + _rest()))
EMPTY = _doc("\n".join([_pin(1, body="short body", override="")] + _rest()))
UNCLOSED = _doc("\n".join([_pin(1, body="short body", override=LONG),
                           _pin(2, body="Body of pin 2.\n```\nunclosed")] + _rest(3)))
# The old invalid override sits below an unclosed fence, so its row is past
# the uncertainty boundary (not prose) before the change.
UNCLOSED_ABOVE = _doc("\n".join([_pin(1, body="Body of pin 1.\n```\nunclosed"),
                                 _pin(2, body="short body", override=LONG)] + _rest(3)))
FENCED_EXAMPLE = _doc("\n".join(
    [_pin(1, body=f"```\n<!-- pinned: 2026-10-01, pin-size-override: {LONG} -->\n```")] + _rest()))


def _edit(old, new):
    return "Edit", {"old_string": old, "new_string": new}


def _write(content):
    return "Write", {"content": content}


def _new_pin(**kw):
    return _edit("\n" + WM, "\n" + _pin(9, body="b", **kw) + "\n" + WM)


# Faithful edits around an old invalid override: none adds or edits it.
FAITHFUL = [
    ("a typo in its body", OVERSIZE, _edit("alpha alpha alpha", "alpah alpha alpha")),
    ("its body shrunk", OVERSIZE, _edit(_words("alpha", 1700), _words("alpha", 1000))),
    ("a different pin edited", OVERSIZE, _edit("Body of pin 3.", "Body of pin three.")),
    ("an unchanged Write", OVERSIZE, _write(OVERSIZE)),
    ("an unchanged Write with CRLF line endings", OVERSIZE, _write(OVERSIZE.replace("\n", "\r\n"))),
    ("a trailing space on its heading", SMALL, _edit("### Pin 1\n", "### Pin 1 \n")),
    ("trailing spaces on its body", SMALL, _edit("short body\n", "short body  \n")),
    ("a blank line after it", SMALL, _edit("short body\n", "short body\n\n")),
    ("a typo in a small pin's body", SMALL, _edit("short body", "shorter body")),
    ("a trailing space on the override row", SMALL, _edit(LONG + " -->", LONG + " -->  ")),
    ("the pin moved to the end by a Write", SMALL,
     _write(_doc("\n".join(_rest() + [_pin(1, body="short body", override=LONG)])))),
    ("the pin renamed", SMALL, _edit("### Pin 1\n", "### Pin one\n")),
    ("an empty rationale's pin edited", EMPTY, _edit("short body", "shorter body")),
    ("an empty rationale's date edited", EMPTY,
     _edit("pinned: 2026-10-01, pin-size-override:", "pinned: 2026-10-05, pin-size-override:")),
    ("the pin moved to the end by a Write with a new date", SMALL,
     _write(_doc("\n".join(_rest() + [_pin(1, body="short body", override=LONG, date="2026-10-05")])))),
    ("a fence closed by a Write", UNCLOSED, _write(UNCLOSED.replace("```\nunclosed", "```\nunclosed\n```"))),
    ("a fence closed by an Edit", UNCLOSED, _edit("```\nunclosed", "```\nunclosed\n```")),
    ("a fence above it closed by a Write", UNCLOSED_ABOVE,
     _write(UNCLOSED_ABOVE.replace("```\nunclosed", "```\nunclosed\n```"))),
]

# Changes that add an invalid override, or edit one and leave it invalid.
REFUSED = [
    ("a new pin with a 131-character rationale", SMALL, _new_pin(override=LONG + "y"),
     "Override rationale is 131 chars (max: 120). Shorten it."),
    ("a new pin with an empty rationale", SMALL, _new_pin(override=""),
     "Override rationale is empty — provide a non-empty reason."),
    ("the rationale edited to another invalid text", SMALL, _edit(LONG + " -->", LONG + "z -->"),
     "Override rationale is 131 chars (max: 120). Shorten it."),
    ("a valid rationale edited to an invalid one", VALID, _edit("verbatim form", LONG),
     "Override rationale is 130 chars (max: 120). Shorten it."),
    ("the invalid rationale copied to a new pin under a new date, the old one kept", SMALL,
     _new_pin(override=LONG, date="2026-10-05"),
     "Override rationale is 130 chars (max: 120). Shorten it."),
    ("a first Write carrying an invalid override", "",
     _write(_doc("\n".join([_pin(i) for i in range(1, 4)] + [_pin(9, body="b", override=LONG)]))),
     "Override rationale is 130 chars (max: 120). Shorten it."),
    ("an invalid override added to an existing pin", _doc("\n".join(_pin(i) for i in range(1, 6))),
     _edit("<!-- pinned: 2026-10-01 -->\n### Pin 2", f"<!-- pinned: 2026-10-01, pin-size-override: {LONG} -->\n### Pin 2"),
     "Override rationale is 130 chars (max: 120). Shorten it."),
]


# A new date on an old invalid override's row: the rationale is untouched.
DATE_CHANGES = [
    ("its date edited", _edit(SMALL_ROW, SMALL_ROW.replace("2026-10-01", "2026-10-05"))),
    ("a replace_all date bump across every pin comment",
     ("Edit", {"old_string": "2026-10-01", "new_string": "2026-10-02", "replace_all": True})),
]


@pytest.mark.parametrize("name, call", DATE_CHANGES, ids=[row[0] for row in DATE_CHANGES])
def test_a_new_date_beside_an_old_invalid_override_is_allowed(name, call):
    from shared.edit_simulation import simulate

    after = simulate(SMALL, *call)
    assert SMALL_ROW not in after and f"pin-size-override: {LONG} -->" in after, name
    decision = gate_decision(SMALL, *call)
    assert decision.verdict == "ALLOW", (name, decision)


@pytest.mark.parametrize("name, before, call", FAITHFUL, ids=[row[0] for row in FAITHFUL])
def test_an_old_invalid_override_is_not_rechecked(name, before, call):
    decision = gate_decision(before, *call)
    assert decision.cause != "override", (name, decision)
    assert decision.verdict != "DENY", (name, decision)


@pytest.mark.parametrize("name, before, call, reason", REFUSED, ids=[row[0] for row in REFUSED])
def test_an_added_or_edited_invalid_override_is_refused(name, before, call, reason):
    decision = gate_decision(before, *call)
    assert (decision.verdict, decision.cause) == ("DENY", "override"), (name, decision)
    assert decision.reason == f"Pin cap violation (invalid override): {reason}"


def test_one_old_row_vouches_for_one_copy():
    """A pin split in two with its invalid override copied onto both halves:
    the old row covers the first copy, and the second copy is new."""
    halves = _pin(1, body="first half", override=LONG) + "\n" + _pin(7, body="second half", override=LONG)
    after = SMALL.replace(_pin(1, body="short body", override=LONG), halves)
    decision = gate_decision(SMALL, *_write(after))
    assert (decision.verdict, decision.cause) == ("DENY", "override"), decision


def test_an_override_copied_from_a_fenced_example_is_not_refused_as_invalid():
    """The copied row's text is a row of the file before, a fenced one, so it
    is not checked. The cap is still enforced: an invalid rationale grants no
    size exemption, so the pin is size-checked as having no override."""
    decision = gate_decision(FENCED_EXAMPLE, *_new_pin(override=LONG))
    assert decision.cause != "override", decision
    big = _new_pin(override=LONG)[1]
    big = {**big, "new_string": big["new_string"].replace("### Pin 9\nb\n", "### Pin 9\n" + "y" * 1600 + "\n")}
    decision = gate_decision(FENCED_EXAMPLE, "Edit", big)
    assert (decision.verdict, decision.cause) == ("DENY", "size"), decision


@pytest.mark.parametrize("separator", [" ", "\x85"], ids=["line separator", "next line"])
def test_a_rationale_holding_a_line_break_is_no_override_and_is_size_checked(separator):
    small = _new_pin(override=f"a{separator}b")
    assert gate_decision(SMALL, *small).verdict != "DENY"
    big = {**small[1], "new_string": small[1]["new_string"].replace("### Pin 9\nb\n", "### Pin 9\n" + "y" * 1600 + "\n")}
    decision = gate_decision(SMALL, "Edit", big)
    assert (decision.verdict, decision.cause) == ("DENY", "size"), decision


def test_a_valid_override_on_a_new_pin_is_allowed():
    assert gate_decision(SMALL, *_new_pin(override="verbatim form")).verdict != "DENY"
