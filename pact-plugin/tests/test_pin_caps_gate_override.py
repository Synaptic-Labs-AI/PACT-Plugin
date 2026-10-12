"""
Which size overrides the pin-cap gate checks: only an override row the change
adds or edits.

An override row is the comment the parser attributes to a pin. The gate looks
its rationale, whitespace collapsed, up among the override comments on every
row of the file before the change, and checks only a rationale it does not
find there. So an old invalid override stays untouched through edits elsewhere
in its pin, a new date on its row, renames, moves, line-ending rewrites and
fence-closing Writes, and a change that adds an invalid override, or edits a
rationale and leaves it invalid, is refused. Where either row carries a
reconfirmation after its override field, the rationales are compared with
their trailing non-word characters removed, since the reader cuts those with
the reconfirmation; so adding, removing, moving or re-dating one leaves an old
override untouched.

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


@pytest.mark.parametrize("separator", ["\u2028", "\x85"], ids=["line separator", "next line"])
def test_a_rationale_holding_a_line_break_is_no_override_and_is_size_checked(separator):
    small = _new_pin(override=f"a{separator}b")
    assert gate_decision(SMALL, *small).verdict != "DENY"
    big = {**small[1], "new_string": small[1]["new_string"].replace("### Pin 9\nb\n", "### Pin 9\n" + "y" * 1600 + "\n")}
    decision = gate_decision(SMALL, "Edit", big)
    assert (decision.verdict, decision.cause) == ("DENY", "size"), decision


def test_a_valid_override_on_a_new_pin_is_allowed():
    assert gate_decision(SMALL, *_new_pin(override="verbatim form")).verdict != "DENY"


# Reconfirming an overridden pin as prune-memory.md says, in either placement
# a curator writes: the override survives and its rationale is read alone.
RATIONALE = "verbatim dispatch form is load-bearing for LLM routing"
RECONFIRM = "reconfirmed: 2026-10-06 because the routing table still cites it in every dispatch"
OVERRIDDEN = _doc("\n".join([_pin(1, body=_words("alpha", 1700), override=RATIONALE, date="2026-05-26")]
                            + _rest()))
OVERRIDDEN_ROW = f"<!-- pinned: 2026-05-26, pin-size-override: {RATIONALE} -->"
RECONFIRM_WITH_COMMA = "reconfirmed: 2026-10-06 because the table, and the router, still cite it"
# The separators a curator writes before a reconfirmation; the age reader
# honours each, so the override reader must too.
SEPARATORS = [(", ", "a comma"), ("; ", "a semicolon"), (" (", "a parenthesis"), (" - ", "a dash"),
              (" ", "a space")]
RECONFIRMED = (
    [(f"after the rationale, {label}", f"<!-- pinned: 2026-05-26, pin-size-override: {RATIONALE}{sep}{RECONFIRM} -->")
     for sep, label in SEPARATORS]
    + [(f"before the override, {label}", f"<!-- pinned: 2026-05-26{sep}{RECONFIRM}, pin-size-override: {RATIONALE} -->")
       for sep, label in SEPARATORS]
    + [(f"before the override, {label}, a comma in the reason",
        f"<!-- pinned: 2026-05-26{sep}{RECONFIRM_WITH_COMMA}, pin-size-override: {RATIONALE} -->")
       for sep, label in SEPARATORS]
)


def _first_pin_after(call):
    """The first pin of OVERRIDDEN after `call`, read from its Pinned body."""
    from fixtures.pin_helpers import parse_pins
    from shared.edit_simulation import simulate

    after = simulate(OVERRIDDEN, *call)
    assert after is not None
    return parse_pins(after[after.index("## Pinned Context\n") + len("## Pinned Context\n"):after.index(WM)])[0]


@pytest.mark.parametrize("name, row", RECONFIRMED, ids=[row[0] for row in RECONFIRMED])
def test_reconfirming_an_overridden_pin_keeps_its_override(name, row):
    call = _edit(OVERRIDDEN_ROW, row)
    decision = gate_decision(OVERRIDDEN, *call)
    assert decision.verdict == "ALLOW", (name, decision)
    pin = _first_pin_after(call)
    assert (pin.override_rationale, pin.body_chars > 1500) == (RATIONALE, True), name


# A rationale with no word character keeps it: only the separator before the
# reconfirmation is dropped.
@pytest.mark.parametrize("rationale, separator", [("\U0001f512", ", "), ("?!", "; ")],
                         ids=["a lock emoji", "punctuation"])
def test_a_symbol_rationale_before_a_reconfirmation_keeps_its_override(rationale, separator):
    call = _edit(OVERRIDDEN_ROW, f"<!-- pinned: 2026-05-26, pin-size-override: {rationale}{separator}{RECONFIRM} -->")
    decision = gate_decision(OVERRIDDEN, *call)
    assert decision.verdict == "ALLOW", (rationale, decision)
    assert _first_pin_after(call).override_rationale == rationale


# A rationale at the cap stays at the cap whatever separates a reconfirmation
# from it: the run of non-word characters before the reconfirmation is dropped.
AT_CAP = "x" * 120
CAP_SEPARATORS = [(" / ", "a slash"), (": ", "a colon"), (" | ", "a bar"), (" \u2014 ", "an em dash"),
                  ("\t", "a tab"), (". ", "a full stop"), (" > ", "a greater-than sign")]


@pytest.mark.parametrize("separator", [sep for sep, _ in CAP_SEPARATORS], ids=[label for _, label in CAP_SEPARATORS])
def test_a_rationale_at_the_cap_before_a_reconfirmation_is_allowed(separator):
    call = _edit(OVERRIDDEN_ROW, f"<!-- pinned: 2026-05-26, pin-size-override: {AT_CAP}{separator}{RECONFIRM} -->")
    decision = gate_decision(OVERRIDDEN, *call)
    assert decision.verdict == "ALLOW", (separator, decision)
    assert _first_pin_after(call).override_rationale == AT_CAP


INVALID_RECONFIRMED = [
    ("after the rationale, rationale over the limit",
     f"<!-- pinned: 2026-05-26, pin-size-override: {LONG}, {RECONFIRM} -->",
     "Override rationale is 130 chars (max: 120). Shorten it."),
    ("before the override, rationale over the limit",
     f"<!-- pinned: 2026-05-26, {RECONFIRM}, pin-size-override: {LONG} -->",
     "Override rationale is 130 chars (max: 120). Shorten it."),
    ("before the override, empty rationale",
     f"<!-- pinned: 2026-05-26, {RECONFIRM}, pin-size-override:  -->",
     "Override rationale is empty — provide a non-empty reason."),
    ("after a semicolon, rationale over the limit",
     f"<!-- pinned: 2026-05-26, pin-size-override: {LONG}; {RECONFIRM} -->",
     "Override rationale is 130 chars (max: 120). Shorten it."),
    ("before the override after a dash, a comma in the reason, rationale over the limit",
     f"<!-- pinned: 2026-05-26 - {RECONFIRM_WITH_COMMA}, pin-size-override: {LONG} -->",
     "Override rationale is 130 chars (max: 120). Shorten it."),
    ("only a separator before the reconfirmation",
     f"<!-- pinned: 2026-05-26, pin-size-override: ; {RECONFIRM} -->",
     "Override rationale is empty — provide a non-empty reason."),
    ("only a slash before the reconfirmation",
     f"<!-- pinned: 2026-05-26, pin-size-override: / {RECONFIRM} -->",
     "Override rationale is empty — provide a non-empty reason."),
    ("one character over the cap, before a slash and a reconfirmation",
     f"<!-- pinned: 2026-05-26, pin-size-override: {'x' * 121} / {RECONFIRM} -->",
     "Override rationale is 121 chars (max: 120). Shorten it."),
]


@pytest.mark.parametrize("name, row, reason", INVALID_RECONFIRMED, ids=[row[0] for row in INVALID_RECONFIRMED])
def test_reconfirming_with_an_invalid_rationale_is_refused(name, row, reason):
    decision = gate_decision(OVERRIDDEN, *_edit(OVERRIDDEN_ROW, row))
    assert (decision.verdict, decision.cause) == ("DENY", "override"), (name, decision)
    assert decision.reason == f"Pin cap violation (invalid override): {reason}"


# An old override over the cap that a curator reconfirms, unreconfirms or
# re-dates without touching its rationale. The reader cuts a reconfirmation
# written after the rationale together with the rationale's own trailing
# punctuation, so either placement reads the same rationale.
OLD = "y" * 124
REC = "reconfirmed: 2026-10-10 because the dispatch template still cites it"
ENDINGS = [(".", "a full stop"), (")", "a closing parenthesis"), ("!", "an exclamation mark"),
           ("…", "an ellipsis"), ('"', "a quotation mark"), ('."', "a full stop and a quotation mark"),
           (":", "a colon"), ("y", "a word character")]


def _old(rationale):
    return _doc("\n".join([_pin(1, body="short body", override=rationale, date="2026-05-26")] + _rest()))


@pytest.mark.parametrize("ending", [e for e, _ in ENDINGS], ids=[label for _, label in ENDINGS])
def test_a_reconfirmation_appended_after_an_old_invalid_override_is_allowed(ending):
    rationale = OLD + ending
    call = _edit(f"{rationale} -->", f"{rationale}, {REC} -->")
    assert gate_decision(_old(rationale), *call).verdict == "ALLOW", ending


@pytest.mark.parametrize("ending", [e for e, _ in ENDINGS], ids=[label for _, label in ENDINGS])
def test_a_reconfirmation_removed_from_an_old_invalid_override_is_allowed(ending):
    rationale = OLD + ending
    call = _edit(f"{rationale}, {REC} -->", f"{rationale} -->")
    assert gate_decision(_old(f"{rationale}, {REC}"), *call).verdict == "ALLOW", ending


def test_a_reconfirmation_appended_by_a_whole_file_write_is_allowed():
    before = _old(OLD + ".")
    after = before.replace(f"{OLD}. -->", f"{OLD}., {REC} -->")
    assert gate_decision(before, *_write(after)).verdict == "ALLOW"


RECONFIRMED_OLD = _old(f"{OLD}., {REC}")
UNTOUCHED = [
    ("a reconfirmation placed before the override", _old(OLD + "."),
     _edit("pinned: 2026-05-26, pin-size-override:", f"pinned: 2026-05-26, {REC}, pin-size-override:")),
    ("the prune-memory rewrite moving the reconfirmation before the override", RECONFIRMED_OLD,
     _edit(f"pinned: 2026-05-26, pin-size-override: {OLD}., {REC} -->",
           f"pinned: 2026-05-26, reconfirmed: 2026-11-10 because the template still cites it, "
           f"pin-size-override: {OLD}. -->")),
    ("a reconfirmation re-dated", RECONFIRMED_OLD, _edit("2026-10-10", "2026-11-10")),
    # A new pin above the old one whose rationale is, or reads as, the old one
    # less its full stop. The old row matches its exact copy first, so the new
    # pin's rationale is checked on its own, and it is valid.
    ("a new pin above with the old rationale less its full stop", _old("x" * 120 + "."),
     _edit("<!-- pinned: 2026-05-26,", _pin(8, body="b", override="x" * 120) + "\n<!-- pinned: 2026-05-26,")),
    ("a new reconfirmed pin above reading the old rationale less its full stop", _old("x" * 120 + "."),
     _edit("<!-- pinned: 2026-05-26,",
           _pin(8, body="b", override=f"{'x' * 120}, {REC}") + "\n<!-- pinned: 2026-05-26,")),
]


@pytest.mark.parametrize("name, before, call", UNTOUCHED, ids=[row[0] for row in UNTOUCHED])
def test_an_old_invalid_override_is_untouched_by_where_its_reconfirmation_sits(name, before, call):
    assert gate_decision(before, *call).verdict == "ALLOW", name


# Where neither row carries a reconfirmation after its override field, the
# comparison stays exact: punctuation edited alone is an edit.
PUNCTUATION_EDITS = [
    ("a valid rationale at the cap given a full stop", _old("x" * 120),
     _edit("x" * 120 + " -->", "x" * 120 + ". -->"), 121),
    ("a valid rationale at the cap given a full stop and a reconfirmation before the override",
     _old("x" * 120),
     _edit(f"pinned: 2026-05-26, pin-size-override: {'x' * 120} -->",
           f"pinned: 2026-05-26, {REC}, pin-size-override: {'x' * 120}. -->"), 121),
    ("an old invalid rationale's full stop removed", _old(OLD + "."), _edit(f"{OLD}. -->", f"{OLD} -->"), 124),
    ("an old invalid rationale's words changed as a reconfirmation is appended", _old(OLD + "."),
     _edit(f"{OLD}. -->", f"w{OLD[1:]}., {REC} -->"), 124),
    # Removing a reconfirmation reveals only what was written before it; a
    # full stop added in the same edit is new text.
    ("a reconfirmation removed and a full stop added to a valid rationale at the cap",
     _old(f"{'x' * 120}, {REC}"), _edit(f"{'x' * 120}, {REC} -->", f"{'x' * 120}. -->"), 121),
]


@pytest.mark.parametrize("name, before, call, length", PUNCTUATION_EDITS, ids=[row[0] for row in PUNCTUATION_EDITS])
def test_an_edited_rationale_is_still_checked(name, before, call, length):
    decision = gate_decision(before, *call)
    assert (decision.verdict, decision.cause) == ("DENY", "override"), (name, decision)
    assert decision.reason == (f"Pin cap violation (invalid override): Override rationale is {length} chars "
                               f"(max: 120). Shorten it.")


# Near-duplicate old overrides on two pins, 'R.' and 'R'. The rows are matched
# as a whole, so neither pin takes the old row its neighbour needs, in either
# order, in a batch reconfirm or in the second one that rewrites both rows.
TWIN = "y" * 124
BIG = _words("alpha", 1700)


def _twins(row_a, row_b, body, swap=False):
    pins = [f"{row_a}\n### Pin A\n{body}\n", f"{row_b}\n### Pin B\n{body}\n"]
    return _doc("\n".join((pins[::-1] if swap else pins) + _rest(3)))


def _comment(rationale, place="none", date="2026-10-10"):
    reconfirm = f"reconfirmed: {date} because still cited"
    if place == "after":
        return f"<!-- pinned: 2026-05-26, pin-size-override: {rationale}, {reconfirm} -->"
    if place == "date":
        return f"<!-- pinned: 2026-05-26, {reconfirm}, pin-size-override: {rationale} -->"
    return f"<!-- pinned: 2026-05-26, pin-size-override: {rationale} -->"


@pytest.mark.parametrize("swap", [False, True], ids=["'R.' first", "'R' first"])
def test_reconfirming_one_of_two_near_duplicate_overrides_is_allowed(swap):
    before = _twins(_comment(TWIN + "."), _comment(TWIN), "short body", swap)
    call = _edit(f"{TWIN}. -->", f"{TWIN}., reconfirmed: 2026-10-10 because still cited -->")
    assert gate_decision(before, *call).verdict == "ALLOW"


BATCHES = [
    ("one appended and one in template order", ("none", "none"), ("after", "date")),
    ("both appended, then both rewritten in template order", ("after", "after"), ("date", "date")),
]


@pytest.mark.parametrize("body", [BIG, "short body"], ids=["over the cap", "small"])
@pytest.mark.parametrize("name, places_before, places_after", BATCHES, ids=[row[0] for row in BATCHES])
def test_a_batch_reconfirm_of_near_duplicate_overrides_is_allowed(name, places_before, places_after, body):
    before = _twins(_comment(TWIN + ".", places_before[0], "2026-07-01"),
                    _comment(TWIN, places_before[1], "2026-07-01"), body)
    after = _twins(_comment(TWIN + ".", places_after[0]), _comment(TWIN, places_after[1]), body)
    assert gate_decision(before, *_write(after)).verdict == "ALLOW", name


def test_an_old_row_two_new_rows_could_take_goes_to_the_invalid_one():
    """The old reconfirmed row reads X. One edit adds a pin whose rationale is
    X and removes the old pin's reconfirmation, so it reads 'X...' at 122.
    Only the invalid one needs the old row; the new X is valid on its own."""
    x = "x" * 119
    before = _old(f"{x}..., {REC}")
    call = _edit(f"<!-- pinned: 2026-05-26, pin-size-override: {x}..., {REC} -->",
                 _pin(8, body="b", override=x) + f"\n<!-- pinned: 2026-05-26, pin-size-override: {x}... -->")
    assert gate_decision(before, *call).verdict == "ALLOW"


# An over-cap pin whose override was written 121 characters, so it reads 120
# and exempts the pin only while a reconfirmation follows it. A
# reconfirmation that moves leaves the override untouched, and the pin counts
# as having no valid override on both sides: it may keep its size, as an
# over-cap pin without one may, and may not grow.
@pytest.mark.parametrize("ending", [".", ":"], ids=["a full stop", "a colon"])
@pytest.mark.parametrize("move", ["rewritten in template order", "removed"])
@pytest.mark.parametrize("grows", [False, True], ids=["same size", "grows"])
def test_a_moved_reconfirmation_neither_grants_nor_keeps_an_exemption(ending, move, grows):
    written = "x" * 120 + ending
    row = f"<!-- pinned: 2026-05-26, pin-size-override: {written}, {REC} -->"
    moved = (f"<!-- pinned: 2026-05-26, {REC}, pin-size-override: {written} -->" if move != "removed"
             else f"<!-- pinned: 2026-05-26, pin-size-override: {written} -->")
    before = _doc("\n".join([f"{row}\n### Pin 1\n{BIG}\n"] + _rest()))
    after = before.replace(row, moved)
    if grows:
        after = after.replace(BIG, BIG + " more" * 24)
    decision = gate_decision(before, *_write(after))
    if grows:
        assert (decision.verdict, decision.cause) == ("DENY", "size"), decision
    else:
        assert decision.verdict == "ALLOW", decision


@pytest.mark.parametrize("grows", [False, True], ids=["same size", "grows"])
def test_an_appended_reconfirmation_grants_no_exemption(grows):
    """The override, written 121 characters, reads 120 once a reconfirmation
    follows it; the pin had none before, so it gains none."""
    written = "x" * 120 + "."
    row = f"<!-- pinned: 2026-05-26, pin-size-override: {written} -->"
    before = _doc("\n".join([f"{row}\n### Pin 1\n{BIG}\n"] + _rest()))
    after = before.replace(row, f"<!-- pinned: 2026-05-26, pin-size-override: {written}, {REC} -->")
    if grows:
        after = after.replace(BIG, BIG + " more" * 24)
    decision = gate_decision(before, *_write(after))
    if grows:
        assert (decision.verdict, decision.cause) == ("DENY", "size"), decision
    else:
        assert decision.verdict == "ALLOW", decision



def test_an_override_edited_as_a_reconfirmation_is_appended_reads_on_its_own():
    """The full stop is removed as the reconfirmation is appended, so the
    rationale is edited: it reads 120 and exempts the pin, which may grow."""
    written = "x" * 120 + "."
    row = f"<!-- pinned: 2026-05-26, pin-size-override: {written} -->"
    before = _doc("\n".join([f"{row}\n### Pin 1\n{BIG}\n"] + _rest()))
    after = before.replace(row, f"<!-- pinned: 2026-05-26, pin-size-override: {'x' * 120}, {REC} -->")
    after = after.replace(BIG, BIG + " more" * 24)
    assert gate_decision(before, *_write(after)).verdict == "ALLOW"

def test_pin_spans_and_section_pins_list_the_same_pins():
    """The gate names a pin by its index among `pin_spans`; the size check
    reads `section_pins`. Both must list the same pins in the same order."""
    from pin_caps import section_pins
    from shared.claude_md_markers import State, parse
    from shared.pin_growth import locate_pinned, pin_spans

    texts = [OVERSIZE, SMALL, VALID, EMPTY, UNCLOSED_ABOVE, FENCED_EXAMPLE, OVERRIDDEN, RECONFIRMED_OLD,
             _twins(_comment(TWIN + ".", "after"), _comment(TWIN, "date"), BIG),
             _doc("\n".join([_pin(1, body="text\n\n\n"), "### Pin with no comment\nbody\n"] + _rest()))]
    checked = 0
    for text in texts:
        doc = parse(text)
        located = locate_pinned(doc)
        if located.state is not State.FOUND:
            continue
        heading, last = located.spans[0]
        spans = pin_spans(doc, (heading + 1, last))
        pins = section_pins(doc, located)
        assert len(spans) == len(pins)
        for (first, _end), pin in zip(spans, pins):
            rows = [doc.lines[r].content for r in range(first, _end + 1)]
            assert pin.heading in rows
            if pin.date_comment is not None:
                assert rows[0].strip() == pin.date_comment
        checked += 1
    assert checked >= 9


# The override check's cost. Near-twins ('R' and 'R.' interleaved, every
# 'R.' reconfirmed by appending) once sent the matching down long alternating
# chains; it now spends a few steps of the pin cap's step budget per row, and
# a check that passes the budget or the timer allows, never refuses.
def _near_twins(n):
    rows = [_comment(TWIN), _comment(TWIN + ".")] * (n // 2)
    before = _doc("\n".join(f"{row}\n### Pin {i}\nbody {i}\n" for i, row in enumerate(rows)))
    after = before.replace(f"{TWIN}. -->", f"{TWIN}., reconfirmed: 2026-10-10 because still cited -->")
    return before, after


def test_the_near_twin_shape_is_matched_in_steps_linear_in_its_rows(monkeypatch):
    import pin_caps_gate
    from shared import pin_growth

    made = []

    class Spy(pin_growth._Budget):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setattr(pin_growth, "_Budget", Spy)
    rows = 1000
    reason, _revoked = pin_caps_gate._invalid_override(*_near_twins(rows))
    assert reason is None
    assert len(made) == 3  # the pin mapping's, the contest's and the matching's
    assert all(budget.spent <= 4 * rows for budget in made), [budget.spent for budget in made]


def _spent_by_part(monkeypatch):
    """A spy on every budget made: {part: steps spent}, summed per part."""
    from collections import Counter

    from shared import pin_growth

    made = []

    class Spy(pin_growth._Budget):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setattr(pin_growth, "_Budget", Spy)

    def spent():
        out = Counter()
        for budget in made:
            out[budget.part] += budget.spent
        return out
    return spent


def _rewritten_beside_copies(n):
    """Each pin, with its own override, rewritten entirely with its row
    padded, beside a new pin carrying an exact copy of the row."""
    ratios = [f"rationale number {k} for the verbatim form" for k in range(n)]
    before = _doc("\n".join(f"{_plain(r)}\n### Pin {k}\n{' '.join(_tokens(f'o{k}x', 0, 4))}\n"
                            for k, r in enumerate(ratios)))
    after = _doc("\n".join(f"{_plain(r.replace(' ', '   ', 1))}\n### Pin {k}\n{' '.join(_tokens(f'n{k}x', 0, 4))}\n"
                           f"\n{_plain(r)}\n### Copy {k}\ncopy {k}\n" for k, r in enumerate(ratios)))
    return before, after


def _split_padded_part_keeping_under_half(n):
    """Each pin, with its own override, split: the part under its heading
    keeps 10 of its 19 word pairs and no override, the other keeps 9 with the
    row padded."""
    ratios = [f"rationale number {k} for the verbatim form" for k in range(n)]
    words = [_tokens(f"t{k}x", 0, 20) for k in range(n)]
    before = _doc("\n".join(f"{_plain(r)}\n### Pin {k}\n{' '.join(w)}\n" for k, (r, w) in enumerate(zip(ratios, words))))
    after = _doc("\n".join(f"<!-- pinned: 2026-05-26 -->\n### Pin {k}\n{' '.join(w[:11])}\n"
                           f"\n{_plain(r.replace(' ', '   ', 1))}\n### Part {k}\n{' '.join(w[10:])}\n"
                           for k, (r, w) in enumerate(zip(ratios, words))))
    return before, after


@pytest.mark.parametrize("shape", [_rewritten_beside_copies, _split_padded_part_keeping_under_half],
                         ids=["rewritten beside an exact copy", "split, the padded part keeping under half"])
def test_the_contest_searches_only_an_old_rows_group_for_rows_standing_for_it(monkeypatch, shape):
    """Every pin carries a different override, so each group holds one pin's
    rows: the contest's search for every row standing for an old row, or for
    a second one, reads only that group, and its steps are linear in the
    rows. Every padded row stands for its pin's old row, so all are allowed."""
    import pin_caps_gate

    spent = _spent_by_part(monkeypatch)
    rows = 300
    reason, _revoked = pin_caps_gate._invalid_override(*shape(rows))
    assert reason is None
    from shared import pin_growth

    assert spent()[pin_growth.CONTEST] <= 100 * rows, spent()  # 15 and 66 a row; 365 and more a row scanning every row


def test_the_carry_compares_the_merged_pins_texts_in_the_matchings_budget(monkeypatch):
    """The merged pin's lone row reads valid where Orig's old one did not, and
    no pin continues Orig: whether the merged pin keeps some of Orig's text
    is read from both pins' word pairs, each spending a step of the override
    matching's budget."""
    import pin_caps_gate
    from shared import pin_growth

    spent = _spent_by_part(monkeypatch)
    before, after = _merge(BARE, (NO_OVERRIDE, CUT), True, False)
    pin_caps_gate._invalid_override(before, after)
    orig, merged = 19, 19 + 1 + 19  # Second's 19 pairs, the pair across the join, Orig's 19
    assert spent()[pin_growth.MATCHING] >= orig + merged, spent()


@pytest.mark.parametrize("key", ["plain", "plain.", "plain.,!? ", "x_", "x_.", "é—", "…", "", "a\u0301.", "九.", "a1!", "🔒🔒"])
def test_the_trimmed_read_drops_exactly_the_trailing_run_the_reader_drops(key):
    """`_trimmed` ends the read at its last character `pin_caps._WORD_CHAR`
    matches, as the reader's cut does."""
    from pin_caps import _WORD_CHAR
    from pin_caps_gate import _trimmed

    end = len(key)
    while end and not _WORD_CHAR.match(key, end - 1):
        end -= 1
    assert _trimmed(key) == key[:end]


@pytest.mark.parametrize("part", ["MAPPING", "CONTEST", "MATCHING"])
def test_an_exhausted_step_budget_allows_with_a_stop_early_note_naming_its_part(monkeypatch, part):
    """The split pin's second copy of an invalid override is refused with the
    budget (test_one_old_row_vouches_for_one_copy); with none left in one part
    of the override check, the check stops there and the change is allowed
    with the pin cap's stop-early note, naming that part."""
    from shared import pin_growth

    name = getattr(pin_growth, part)
    monkeypatch.setitem(pin_growth.LIMITS, name, 0)
    halves = _pin(1, body="first half", override=LONG) + "\n" + _pin(7, body="second half", override=LONG)
    decision = gate_decision(SMALL, *_write(SMALL.replace(_pin(1, body="short body", override=LONG), halves)))
    assert (decision.verdict, decision.cause) == ("ALLOW_ADVISORY", "size_bound"), decision
    assert decision.reason == ("The pin cap check stopped early and allowed this change: "
                               f"{name} passed its step budget of 0.")


def test_texts_too_large_for_the_check_stop_it_before_the_override_check_reads_them(monkeypatch):
    import pin_caps_gate
    from shared import pin_growth

    def unread(before, after):
        raise AssertionError("the override check read texts too large for the check")

    monkeypatch.setattr(pin_caps_gate, "_invalid_override", unread)
    half = pin_growth.MAX_CHARS // 2
    before = SMALL.replace("short body", "x" * half)
    decision = gate_decision(before, *_write(before.replace("x" * half, "y" * half)))
    assert (decision.verdict, decision.cause) == ("ALLOW_ADVISORY", "size_bound"), decision
    assert (decision.reason or "").startswith("The pin cap check stopped early and allowed this change: "
                                              "the file is too large for the pin cap check"), decision


def test_the_timer_stopping_the_override_check_allows_with_the_stop_early_note(monkeypatch):
    import pin_caps_gate
    from shared.pin_growth import SizeBound

    def stopped(before, after):
        raise SizeBound("the pin cap check ran past 30 s")

    monkeypatch.setattr(pin_caps_gate, "_invalid_override", stopped)
    decision = gate_decision(SMALL, *_new_pin(override=LONG + "y"))
    assert (decision.verdict, decision.cause) == ("ALLOW_ADVISORY", "size_bound"), decision
    assert decision.reason == "The pin cap check stopped early and allowed this change: the pin cap check ran past 30 s."


def _stands_for(new, old):
    """The edge rule `_Edges` builds, written pair by pair."""
    from pin_caps_gate import _trimmed

    if new.key == old.key:
        return True
    if _trimmed(new.key) != _trimmed(old.key):
        return False
    return new.written is not None or (old.written is not None and old.written.startswith(new.key))


def test_the_matching_covers_as_many_invalid_rows_as_any_matching_can():
    """Over random small sets of near-duplicate rows: every pair `_match`
    makes obeys the edge rule, each new row identical to an old row left for
    it takes one, and among the rest it leaves no more invalid rows unmatched
    than a matching of the invalid rows alone, found by brute force."""
    import itertools
    import random

    from pin_caps_gate import _match, _Row, _trimmed

    base = "y" * 119
    reads = [base, base + ".", base + "..", base + "!", base + "x"]
    writtens = [None, ".,", "..,", ",", "!,"]
    rng = random.Random(7)

    def row(pin):
        key = rng.choice(reads)
        tail = rng.choice(writtens)
        written = None if tail is None else key + tail
        return _Row(key, key, written, pin, _trimmed(key))

    for _ in range(400):
        new = [row(i) for i in range(rng.randint(1, 5))]
        old = [row(None) for _ in range(rng.randint(0, 5))]
        matched = _match(new, old)
        assert len(set(matched.values())) == len(matched)
        assert all(_stands_for(new[i], old[j]) for i, j in matched.items())
        identical, used = set(), set()
        for i, r in enumerate(new):
            j = next((j for j in range(len(old)) if j not in used
                      and (old[j].rationale, old[j].written) == (r.rationale, r.written)), None)
            if j is not None:
                used.add(j)
                identical.add(i)
                assert (old[matched[i]].rationale, old[matched[i]].written) == (r.rationale, r.written)
        invalid = [i for i, r in enumerate(new) if not r.valid]
        rest = [i for i in invalid if i not in identical]
        free = [j for j in range(len(old)) if j not in used]
        best = max((k for k in range(len(rest) + 1)
                    for chosen in itertools.combinations(rest, k)
                    for olds in itertools.permutations(free, k)
                    if all(_stands_for(new[i], old[j]) for i, j in zip(chosen, olds))), default=0)
        covered = sum(1 for i in invalid if i in matched)
        assert covered == best + len(identical & set(invalid)), (new, old, matched)


# An old override's own unchanged row is that row; a copy of it, even one a
# whitespace change makes read the same, claims an old row only if one is
# left for it.
SPACED = "a " * 59 + "b"  # 119 characters, valid


def _spaced_doc(*rationales):
    return _doc("\n".join([_pin(i, body="short body", override=r) for i, r in enumerate(rationales, 1)]
                          + _rest(len(rationales) + 1)))


def test_a_new_pin_copying_an_old_override_with_extra_spaces_is_refused():
    """The copy reads the same once whitespace is collapsed, but it is 121
    characters; the untouched original keeps the old row, so the copy is
    checked on its own."""
    padded = SPACED.replace("a b", "a   b", 1)
    assert len(padded) == 121 and " ".join(padded.split()) == SPACED
    decision = gate_decision(_spaced_doc(SPACED), *_new_pin(override=padded))
    assert (decision.verdict, decision.cause) == ("DENY", "override"), decision
    assert decision.reason == "Pin cap violation (invalid override): Override rationale is 121 chars (max: 120). Shorten it."


def test_a_whitespace_edit_to_the_only_copy_of_an_override_is_not_rechecked():
    padded = SPACED.replace("a b", "a   b", 1)
    decision = gate_decision(_spaced_doc(SPACED), *_edit(f"pin-size-override: {SPACED} -->",
                                                        f"pin-size-override: {padded} -->"))
    assert decision.verdict != "DENY", decision


def test_a_whitespace_edit_on_the_original_pin_beside_a_valid_copy_is_allowed():
    """The edit leaves the padded text on the original pin (its heading and
    body) and puts the reconfirmed original text on a new pin above it. The
    original pin's own row reads the same once whitespace is collapsed, so it
    stands for its old row; the new pin's copy is checked on its own and is
    valid."""
    padded = SPACED.replace("a b", "a   b", 1)
    call = _edit(f"pin-size-override: {SPACED} -->",
                 f"pin-size-override: {SPACED}, {REC} -->\n### Pin copy\nb\n<!-- pinned: 2026-10-01, "
                 f"pin-size-override: {padded} -->")
    before = _spaced_doc(SPACED)
    from shared.edit_simulation import simulate
    after = simulate(before, *call)
    assert after.count("pin-size-override:") == 2
    assert gate_decision(before, *call).verdict == "ALLOW"



# Each pin's override stands for its own pin's old row first: the pin it
# continues is the old pin it matches best, by heading and content together,
# then content (half of the old pin's word pairs kept), then heading, the
# larger share first in each, wherever either pin sits. A copy on another pin,
# however its heading or body reads, takes the original's row only if the
# original's own row cannot.
ORIG_BODY = "the dispatch template cites this verbatim form in every routing decision"
NEW_BODY = "an unrelated note about something else entirely with other words"


def _named(*pins):
    return _doc("\n".join(f"{row}\n### {heading}\n{body}\n" for row, heading, body in pins) + "\n" + "\n".join(_rest(6, 9)))


def _plain(rationale, reconfirmed=False):
    tail = f", {REC}" if reconfirmed else ""
    return f"<!-- pinned: 2026-05-26, pin-size-override: {rationale}{tail} -->"


PADDED = SPACED.replace("a b", "a   b", 1)


def _tokens(tag, start, stop):
    return [f"{tag}{k}" for k in range(start, stop)]


# Bodies of distinct words, so each share is exact: the original holds 19
# word pairs, of which the rewrite keeps 5, the weaker pin 1, the fragment 4
# and the grown body all 19, among 60 new words.
TOKENS = " ".join(_tokens("o", 0, 20))
REWRITTEN = " ".join(_tokens("o", 0, 6) + _tokens("a", 0, 14))
WEAKER = " ".join(_tokens("o", 10, 12) + _tokens("b", 0, 18))
FRAGMENT = " ".join(_tokens("o", 10, 15))
GROWN = " ".join(_tokens("o", 0, 20) + _tokens("n", 0, 60))
COPIES = [
    ("a new pin above with a padded copy, the original reconfirmed",
     [(_plain(SPACED), "Orig", ORIG_BODY)],
     [(_plain(PADDED), "Newcomer", NEW_BODY), (_plain(SPACED, True), "Orig", ORIG_BODY)]),
    ("a new pin below with a padded copy, the original reconfirmed",
     [(_plain(SPACED), "Orig", ORIG_BODY)],
     [(_plain(SPACED, True), "Orig", ORIG_BODY), (_plain(PADDED), "Newcomer", NEW_BODY)]),
    ("the new pin takes the original's heading, the original renamed and reconfirmed",
     [(_plain(SPACED), "Orig", ORIG_BODY)],
     [(_plain(PADDED), "Orig", NEW_BODY), (_plain(SPACED, True), "Orig two", ORIG_BODY)]),
    ("both pins headed like the original",
     [(_plain(SPACED), "Orig", ORIG_BODY)],
     [(_plain(PADDED), "Orig", NEW_BODY), (_plain(SPACED, True), "Orig", ORIG_BODY)]),
    ("positional headings: the new pin is 'Pin 1', the original becomes 'Pin 2'",
     [(_plain(SPACED), "Pin 1", ORIG_BODY)],
     [(_plain(PADDED), "Pin 1", NEW_BODY), (_plain(SPACED, True), "Pin 2", ORIG_BODY)]),
    ("a new pin above repeating the original's body, the original reconfirmed",
     [(_plain(SPACED), "Orig", ORIG_BODY)],
     [(_plain(PADDED), "Echo", ORIG_BODY), (_plain(SPACED, True), "Orig", ORIG_BODY)]),
    ("a new pin above holding the first two thirds of the original's body",
     [(_plain(SPACED), "Orig", ORIG_BODY)],
     [(_plain(PADDED), "Echo", " ".join(ORIG_BODY.split()[:8])), (_plain(SPACED, True), "Orig", ORIG_BODY)]),
    ("a new pin below repeating the original's body, the original reconfirmed",
     [(_plain(SPACED), "Orig", ORIG_BODY)],
     [(_plain(SPACED, True), "Orig", ORIG_BODY), (_plain(PADDED), "Echo", ORIG_BODY)]),
    ("a new pin above repeating an over-cap original's body",
     [(_plain(SPACED), "Orig", BIG)],
     [(_plain(PADDED), "Echo", BIG), (_plain(SPACED, True), "Orig", BIG)]),
    ("a new pin above holding part of the original's body, the original extended",
     [(_plain(SPACED), "Orig", ORIG_BODY)],
     [(_plain(PADDED), "Echo", " ".join(ORIG_BODY.split()[:8])),
      (_plain(SPACED, True), "Orig", ORIG_BODY + " and six more words added here")]),
    ("the original rewritten under its heading, a new pin above under it keeping less of the old text",
     [(_plain(SPACED), "Orig", TOKENS)],
     [(_plain(PADDED), "Orig", WEAKER), (_plain(SPACED, True), "Orig", REWRITTEN)]),
    ("the original rewritten under its heading, a new pin below under it keeping less of the old text",
     [(_plain(SPACED), "Orig", TOKENS)],
     [(_plain(SPACED, True), "Orig", REWRITTEN), (_plain(PADDED), "Orig", WEAKER)]),
    ("a new pin above under the original's heading holding a fragment of its body",
     [(_plain(SPACED), "Orig", TOKENS)],
     [(_plain(PADDED), "Orig", FRAGMENT), (_plain(SPACED, True), "Orig", REWRITTEN)]),
    ("a new pin below under the original's heading holding a fragment of its body",
     [(_plain(SPACED), "Orig", TOKENS)],
     [(_plain(SPACED, True), "Orig", REWRITTEN), (_plain(PADDED), "Orig", FRAGMENT)]),
    ("a new pin above under another heading holding a fragment of the original's body",
     [(_plain(SPACED), "Orig", TOKENS)],
     [(_plain(PADDED), "Echo", FRAGMENT), (_plain(SPACED, True), "Orig", REWRITTEN)]),
    ("the original grown under its heading, a new pin above under it holding a fragment of its body",
     [(_plain(SPACED), "Orig", TOKENS)],
     [(_plain(PADDED), "Orig", FRAGMENT), (_plain(SPACED, True), "Orig", GROWN)]),
    ("the original grown and renamed, a new pin above under its old heading holding a fragment of its body",
     [(_plain(SPACED), "Orig", TOKENS)],
     [(_plain(PADDED), "Orig", FRAGMENT), (_plain(SPACED, True), "Renamed", GROWN)]),
]


@pytest.mark.parametrize("name, before, after", COPIES, ids=[row[0] for row in COPIES])
def test_an_invalid_copy_beside_an_edited_original_is_refused(name, before, after):
    decision = gate_decision(_named(*before), *_write(_named(*after)))
    assert (decision.verdict, decision.cause) == ("DENY", "override"), (name, decision)
    assert decision.reason == "Pin cap violation (invalid override): Override rationale is 121 chars (max: 120). Shorten it."


# Faithful edits around an old invalid override (124 characters) on two pins:
# each still stands for its old row, through its own pin or, where the
# mapping cannot place the pin, through any old row it may stand for.
INVALID = "y" * 124
OTHER = "z" * 124
SECOND_BODY = "a second pin about a different matter with its own words"
START = [(_plain(INVALID), "Alpha pin", ORIG_BODY), (_plain(OTHER), "Beta pin", SECOND_BODY)]
FAITHFUL_PINS = [
    ("the body rewritten, the override row untouched",
     [(_plain(INVALID), "Alpha pin", ORIG_BODY[::-1]), START[1]]),
    ("the pin replaced by different text, its override row kept",
     [(_plain(INVALID), "Gamma pin", NEW_BODY), START[1]]),
    ("the two pins swapped",
     [START[1], START[0]]),
    ("the pin renamed",
     [(_plain(INVALID), "Alpha pin renamed", ORIG_BODY), START[1]]),
    ("the pin renamed, rewritten and moved below the other",
     [START[1], (_plain(INVALID), "Completely new title", ORIG_BODY[::-1])]),
    ("a pure split with the override on the first half",
     [(_plain(INVALID), "Alpha pin", ORIG_BODY[:30]), ("<!-- pinned: 2026-05-26 -->", "Alpha part two", ORIG_BODY[30:]),
      START[1]]),
    ("a reconfirmation appended on a renamed and rewritten pin",
     [(_plain(INVALID, True), "New name", ORIG_BODY[::-1]), START[1]]),
    ("the override comment moved to the other pin",
     [("<!-- pinned: 2026-05-26 -->", "Alpha pin", ORIG_BODY), (_plain(INVALID), "Beta pin", SECOND_BODY)]),
    ("the pin replaced by different text, its override reconfirmed",
     [(_plain(INVALID, True), "Gamma pin", NEW_BODY), START[1]]),
    ("the pin renamed, rewritten and moved, its override reconfirmed",
     [START[1], (_plain(INVALID, True), "Completely new title", ORIG_BODY[::-1])]),
]


@pytest.mark.parametrize("name, after", FAITHFUL_PINS, ids=[row[0] for row in FAITHFUL_PINS])
def test_an_old_invalid_override_is_untouched_wherever_its_pin_goes(name, after):
    decision = gate_decision(_named(*START), *_write(_named(*after)))
    assert decision.verdict == "ALLOW", (name, decision)


def test_an_override_shared_by_two_pins_moved_from_one_to_a_new_pin_stands_for_its_old_row():
    """The untouched pin keeps its own old row; the moved copy takes the one
    left, not the untouched pin's."""
    before = _named((_plain(INVALID), "Alpha pin", ORIG_BODY), (_plain(INVALID), "Beta pin", SECOND_BODY))
    after = _named((_plain(INVALID), "Gamma pin", NEW_BODY), (_plain(INVALID), "Alpha pin", ORIG_BODY),
                   ("<!-- pinned: 2026-05-26 -->", "Beta pin", SECOND_BODY))
    assert gate_decision(before, *_write(after)).verdict == "ALLOW"


@pytest.mark.parametrize("copy_above", [True, False], ids=["copy above", "copy below"])
def test_a_copy_alike_in_heading_and_body_continues_the_original_no_better_than_it(copy_above):
    """Two pins alike in heading and body continue the original equally, so
    neither is given its pin: the padded row may stand for the old one, as
    on a whitespace edit of the original, and the reconfirmed row is valid on
    its own."""
    pins = [(_plain(PADDED), "Orig", ORIG_BODY), (_plain(SPACED, True), "Orig", ORIG_BODY)]
    after = _named(*(pins if copy_above else pins[::-1]))
    decision = gate_decision(_named((_plain(SPACED), "Orig", ORIG_BODY)), *_write(after))
    assert decision.verdict == "ALLOW", decision


KEEPING_ALL = [
    ("a new pin above under the heading holding all the old text and more, the original rewritten",
     [(_plain(PADDED), "Orig", GROWN), (_plain(SPACED, True), "Orig", REWRITTEN)]),
    ("a new pin above under another heading holding all the old text and more, the original rewritten",
     [(_plain(PADDED), "Echo", GROWN), (_plain(SPACED, True), "Orig", REWRITTEN)]),
    ("a new pin above under the heading holding all the old text and more, the original unchanged",
     [(_plain(PADDED), "Orig", GROWN), (_plain(SPACED, True), "Orig", TOKENS)]),
    ("a new pin below under the heading holding all the old text and more, the original unchanged",
     [(_plain(SPACED, True), "Orig", TOKENS), (_plain(PADDED), "Orig", GROWN)]),
]


@pytest.mark.parametrize("name, after", KEEPING_ALL, ids=[row[0] for row in KEEPING_ALL])
def test_a_pin_keeping_all_of_the_old_text_continues_it(name, after):
    """The pin keeping the larger share of the old text continues it, however
    much text it adds, and a whitespace pad on its override reads as the old
    row; where both pins keep all of it they tie and the rows match as any
    rows may."""
    decision = gate_decision(_named((_plain(SPACED), "Orig", TOKENS)), *_write(_named(*after)))
    assert decision.verdict == "ALLOW", (name, decision)


EQUAL = [
    ("the copy above", True, True, "ALLOW"),
    ("the copy below", False, True, "ALLOW"),
    ("the copy above, the original's override untouched", True, False, "DENY"),
]


@pytest.mark.parametrize("name, copy_above, reconfirmed, verdict", EQUAL, ids=[row[0] for row in EQUAL])
def test_pins_under_one_heading_keeping_none_of_the_old_text_continue_it_equally(name, copy_above, reconfirmed, verdict):
    """The original is rewritten entirely and a new pin under its heading has
    new text too: both keep none of the old text, so neither is given its
    pin and the padded row may stand for the old one. An untouched original
    override is that old row, so the copy is then checked on its own."""
    pins = [(_plain(PADDED), "Orig", " ".join(_tokens("b", 0, 20))),
            (_plain(SPACED, reconfirmed), "Orig", " ".join(_tokens("a", 0, 20)))]
    after = _named(*(pins if copy_above else pins[::-1]))
    decision = gate_decision(_named((_plain(SPACED), "Orig", TOKENS)), *_write(after))
    assert decision.verdict == verdict, (name, decision)


@pytest.mark.parametrize("move", ["rewritten in template order", "removed"])
def test_one_pin_continuing_two_alike_old_pins_continues_neither(move):
    """Of two over-cap pins alike in heading and body, the first is removed
    and the second's reconfirmation moved. The pin left continues both
    equally, so neither is its own: its row matches the second's old row as
    any row may, and it keeps its size with no valid override on both sides."""
    written = "x" * 120 + "."
    moved = (f"<!-- pinned: 2026-05-26, {REC}, pin-size-override: {written} -->" if move != "removed"
             else _plain(written))
    before = _named((_plain(SPACED), "Orig", BIG), (_plain(written, True), "Orig", BIG))
    assert gate_decision(before, *_write(_named((moved, "Orig", BIG)))).verdict == "ALLOW"


# Two pins carrying the same override, written 121 characters so it reads 120
# only while a reconfirmation follows it. Each pin's row takes its own pin's
# old row first, so an untouched neighbour cannot take the edited pin's.
WRITTEN = "x" * 120 + "."
ALPHA, OMEGA = _words("alpha", 1700), _words("omega", 1700)
CUT = _plain(WRITTEN, True)
BARE = _plain(WRITTEN)
TEMPLATE = f"<!-- pinned: 2026-05-26, {REC}, pin-size-override: {WRITTEN} -->"
SHARED = [
    ("the earlier pin rewritten into template order", (CUT, CUT), (TEMPLATE, ALPHA, CUT, OMEGA), None),
    ("the earlier pin's reconfirmation removed", (CUT, CUT), (BARE, ALPHA, CUT, OMEGA), None),
    ("the earlier pin's reconfirmation removed, the later pin grows", (CUT, CUT),
     (BARE, ALPHA, CUT, OMEGA + " more" * 24), None),
    ("the later pin's reconfirmation removed, the earlier pin grows", (CUT, CUT),
     (CUT, ALPHA + " more" * 24, BARE, OMEGA), None),
    ("the earlier pin's reconfirmation removed and it grows", (CUT, CUT),
     (BARE, ALPHA + " more" * 24, CUT, OMEGA), "size"),
    ("neither reconfirmed, the earlier pin reconfirmed by appending", (BARE, BARE), (CUT, ALPHA, BARE, OMEGA), None),
    ("neither reconfirmed, the earlier pin reconfirmed by appending and grown", (BARE, BARE),
     (CUT, ALPHA + " more" * 24, BARE, OMEGA), "size"),
]


@pytest.mark.parametrize("name, rows, after, cause", SHARED, ids=[row[0] for row in SHARED])
def test_each_pin_keeps_its_own_old_row_beside_a_pin_with_the_same_override(name, rows, after, cause):
    before = _named((rows[0], "Alpha pin", ALPHA), (rows[1], "Omega pin", OMEGA))
    decision = gate_decision(before, *_write(_named((after[0], "Alpha pin", after[1]),
                                                    (after[2], "Omega pin", after[3]))))
    if cause:
        assert (decision.verdict, decision.cause) == ("DENY", cause), (name, decision)
    else:
        assert decision.verdict == "ALLOW", (name, decision)


# A read with no word character trims to nothing: it stands only for an old
# row with the same read. The reader keeps a symbol-only rationale before a
# reconfirmation, but reads separator text alone there as empty.
LOCK = "\U0001f512"
SYMBOLS = [
    ("a lock emoji rewritten as 200 with a reconfirmation", LOCK, f"{LOCK * 200}, {REC}", "DENY",
     "Override rationale is 200 chars (max: 120). Shorten it."),
    ("a separator-only rationale given a reconfirmation", "--", f"--, {REC}", "DENY",
     "Override rationale is empty \u2014 provide a non-empty reason."),
    ("a lock emoji given a reconfirmation", LOCK, f"{LOCK}, {REC}", "ALLOW", None),
]


@pytest.mark.parametrize("name, old, new, verdict, reason", SYMBOLS, ids=[row[0] for row in SYMBOLS])
def test_a_symbol_only_rationale_stands_only_for_the_same_read(name, old, new, verdict, reason):
    decision = gate_decision(_spaced_doc(old), *_edit(f"pin-size-override: {old} -->", f"pin-size-override: {new} -->"))
    assert decision.verdict == verdict, (name, decision)
    if reason:
        assert decision.reason == f"Pin cap violation (invalid override): {reason}"


# The pin the mapping names as the old pin's continuation keeps its own
# edited row's old row ahead of an exact copy on a pin keeping no more of the
# old text, with a body unlike its own, and the copy is checked on its own.
# A copy on a pin keeping more, or with the same body, takes the old row, as
# it would with no continuation named; so do copies where the mapping names
# none (equal shares under one heading).
WEAKER2 = " ".join(_tokens("o", 14, 16) + _tokens("c", 0, 18))
HALF, OTHER_HALF = " ".join(_tokens("o", 0, 11)), " ".join(_tokens("o", 9, 20))  # 10 of 19 old pairs each
STRONGER = " ".join(_tokens("o", 4, 18) + _tokens("s", 0, 6))  # 13 of 19
AHEAD = [
    ("the original rewritten, its own row padded, an exact copy above",
     [(_plain(SPACED), "Orig", WEAKER), (_plain(PADDED), "Orig", REWRITTEN)], "ALLOW"),
    ("the original rewritten, its own row padded, an exact copy below",
     [(_plain(PADDED), "Orig", REWRITTEN), (_plain(SPACED), "Orig", WEAKER)], "ALLOW"),
    ("the original keeping all its text, its own row padded, an exact copy above",
     [(_plain(SPACED), "Orig", WEAKER), (_plain(PADDED), "Orig", TOKENS)], "ALLOW"),
    ("two weaker pins each with an exact copy, the original's own row padded",
     [(_plain(SPACED), "Orig", WEAKER), (_plain(SPACED), "Orig", WEAKER2), (_plain(PADDED), "Orig", REWRITTEN)], "ALLOW"),
    ("the original's own row padded, no copy",
     [(_plain(PADDED), "Orig", REWRITTEN)], "ALLOW"),
    ("the copy's pin keeping as much of the old text as the original, the original's own row padded",
     [(_plain(SPACED), "Orig", TOKENS), (_plain(PADDED), "Orig", TOKENS)], "DENY"),
    ("one copy on a weaker pin, another under another heading keeping as much, the original's own row padded",
     [(_plain(SPACED), "Orig", WEAKER), (_plain(SPACED), "Echo", " ".join(_tokens("o", 12, 18) + _tokens("e", 0, 14))),
      (_plain(PADDED), "Orig", REWRITTEN)], "ALLOW"),
    ("copies on a weaker pin and an equal one under another heading above, the original's half padded",
     [(_plain(SPACED), "Weak", WEAKER), (_plain(SPACED), "Equal", OTHER_HALF), (_plain(PADDED), "Orig", HALF)], "ALLOW"),
    ("the same, the copies below",
     [(_plain(PADDED), "Orig", HALF), (_plain(SPACED), "Weak", WEAKER), (_plain(SPACED), "Equal", OTHER_HALF)], "ALLOW"),
    ("the other half under another heading with an exact copy below, the original's half padded",
     [(_plain(PADDED), "Orig", HALF), (_plain(SPACED), "Orig part two", OTHER_HALF)], "ALLOW"),
    ("the other half under another heading with an exact copy above, the original's half padded",
     [(_plain(SPACED), "Orig part two", OTHER_HALF), (_plain(PADDED), "Orig", HALF)], "ALLOW"),
    ("both halves under the original's heading, the exact copy above",
     [(_plain(SPACED), "Orig", OTHER_HALF), (_plain(PADDED), "Orig", HALF)], "DENY"),
    ("both halves under the original's heading, the exact copy below",
     [(_plain(PADDED), "Orig", HALF), (_plain(SPACED), "Orig", OTHER_HALF)], "DENY"),
    ("the original rewritten entirely and padded, a copy under another heading keeping none either",
     [(_plain(SPACED), "Other", " ".join(_tokens("b", 0, 20))), (_plain(PADDED), "Orig", " ".join(_tokens("a", 0, 20)))],
     "ALLOW"),
    ("copies on a weaker pin and one under another heading keeping more, the original's half padded",
     [(_plain(SPACED), "Weak", WEAKER), (_plain(SPACED), "Strong", STRONGER), (_plain(PADDED), "Orig", HALF)], "DENY"),
]


@pytest.mark.parametrize("name, after, verdict", AHEAD, ids=[row[0] for row in AHEAD])
def test_the_continuation_keeps_its_old_row_ahead_of_an_exact_copy_on_a_pin_keeping_less(name, after, verdict):
    decision = gate_decision(_named((_plain(SPACED), "Orig", TOKENS)), *_write(_named(*after)))
    assert decision.verdict == verdict, (name, decision)


INVALID_SPACED = "a " * 61 + "bc"  # 124 characters, invalid
INVERSE = [
    ("the original appends a reconfirmation to an override written 121 characters, an exact copy of it below",
     BARE, [(_plain(WRITTEN, True), "Orig", REWRITTEN), (BARE, "Orig", WEAKER)], 121),
    ("the same, the exact copy above",
     BARE, [(BARE, "Orig", WEAKER), (_plain(WRITTEN, True), "Orig", REWRITTEN)], 121),
    ("the original pads an invalid override, an exact copy of it above",
     _plain(INVALID_SPACED), [(_plain(INVALID_SPACED), "Orig", WEAKER),
                              (_plain(INVALID_SPACED.replace("a a", "a   a", 1)), "Orig", REWRITTEN)], 124),
]


@pytest.mark.parametrize("name, old_row, after, length", INVERSE, ids=[row[0] for row in INVERSE])
def test_an_exact_copy_of_an_invalid_override_on_a_pin_keeping_less_is_refused(name, old_row, after, length):
    decision = gate_decision(_named((old_row, "Orig", TOKENS)), *_write(_named(*after)))
    assert (decision.verdict, decision.cause) == ("DENY", "override"), (name, decision)
    assert decision.reason == (f"Pin cap violation (invalid override): Override rationale is {length} chars "
                               f"(max: 120). Shorten it.")


@pytest.mark.parametrize("grows", [False, True], ids=["same size", "grows"])
def test_an_over_cap_continuation_with_a_padded_row_beside_an_exact_copy_keeps_its_size(grows):
    """The padded row reads invalid and its old row valid, so the pin counts
    as having no valid override on both sides: it may keep its size, and not
    grow."""
    body = " ".join(_tokens("big", 0, 300))
    grown = body + " " + " ".join(_tokens("g", 0, 40)) if grows else body
    after = _named((_plain(SPACED), "Orig", " ".join(_tokens("big", 100, 102) + _tokens("c", 0, 18))),
                   (_plain(PADDED), "Orig", grown))
    decision = gate_decision(_named((_plain(SPACED), "Orig", body)), *_write(after))
    if grows:
        assert (decision.verdict, decision.cause) == ("DENY", "size"), decision
    else:
        assert decision.verdict == "ALLOW", decision


def _positional(*rows):
    return _doc("\n".join([f"{row}\n### Pin {i}\nshort body\n" for i, row in enumerate(rows, 1)] + _rest(len(rows) + 1)))


@pytest.mark.parametrize("old_text, new_text, verdict", [(PADDED, SPACED, "ALLOW"), (SPACED, PADDED, "DENY")],
                         ids=["an old padded override kept, a valid copy above", "a padded copy above"])
def test_a_new_pin_above_taking_the_original_heading_cannot_take_its_untouched_row(old_text, new_text, verdict):
    """A new pin inserted above takes the original's positional heading, and
    both keep all of its text; the original's untouched row is its old row."""
    decision = gate_decision(_positional(_plain(old_text)), *_write(_positional(_plain(new_text), _plain(old_text))))
    assert decision.verdict == verdict, decision


@pytest.mark.parametrize("old_text, new_text, verdict", [(PADDED, SPACED, "ALLOW"), (SPACED, PADDED, "DENY")],
                         ids=["an old padded override kept, a valid copy above", "a padded copy above"])
def test_a_new_pin_above_keeping_less_cannot_take_the_untouched_original_row(old_text, new_text, verdict):
    """The new pin above takes 'Pin 1' with all but the last word of the
    body; the untouched original, now 'Pin 2', keeps more of its text, so
    its row is its old row although the heading names the new pin."""
    near = " ".join(_tokens("o", 0, 19))
    before = _doc(f"{_plain(old_text)}\n### Pin 1\n{TOKENS}\n\n" + "\n".join(_rest(2)))
    after = _doc(f"{_plain(new_text)}\n### Pin 1\n{near}\n\n{_plain(old_text)}\n### Pin 2\n{TOKENS}\n\n" + "\n".join(_rest(3)))
    decision = gate_decision(before, *_write(after))
    assert decision.verdict == verdict, decision


# The pin keeping the most of the old text continues it whatever the mapping
# names; on an equal share the mapping's choice does, unless the texts read
# the same once whitespace is collapsed. Two sentences that start and end on
# the same words keep their word pairs when swapped, yet read differently.
SENTENCES = "Always run lint then first. Always run tests then first."
SWAPPED = "Always run tests then first. Always run lint then first."
OTHER_SWAP = "Never push tests before review. Always run lint then first."
CONTINUATIONS = [
    ("a copy with the sentences swapped above, the original's own row padded",
     SENTENCES, [(_plain(SPACED), "Orig variant", SWAPPED), (_plain(PADDED), "Orig", SENTENCES)], "ALLOW"),
    ("a copy with the sentences swapped below, the original's own row padded",
     SENTENCES, [(_plain(PADDED), "Orig", SENTENCES), (_plain(SPACED), "Orig variant", SWAPPED)], "ALLOW"),
    ("a copy reading the same above, the original's own row padded",
     SENTENCES, [(_plain(SPACED), "Orig variant", SENTENCES), (_plain(PADDED), "Orig", SENTENCES)], "DENY"),
    ("a copy with other sentences swapped above, the original's own row padded",
     "Always run lint then first. Never push tests before review.",
     [(_plain(SPACED), "Orig variant", OTHER_SWAP),
      (_plain(PADDED), "Orig", "Always run lint then first. Never push tests before review.")], "ALLOW"),
]


@pytest.mark.parametrize("name, old_body, after, verdict", CONTINUATIONS, ids=[row[0] for row in CONTINUATIONS])
def test_the_pin_keeping_most_of_the_old_text_keeps_its_row_ahead_of_an_exact_copy(name, old_body, after, verdict):
    decision = gate_decision(_named((_plain(SPACED), "Orig", old_body)), *_write(_named(*after)))
    assert decision.verdict == verdict, (name, decision)


@pytest.mark.parametrize("kept", [19, 11], ids=["all but one word", "about half"])
def test_a_new_pin_above_taking_the_heading_with_an_exact_copy_leaves_the_original_its_row(kept):
    """The new pin above takes 'Pin 1' and part of the body with an exact
    copy; the original, now 'Pin 2', keeps all of it and pads its own row,
    which keeps the old row; the copy is checked on its own."""
    part = " ".join(_tokens("o", 0, kept))
    before = _doc(f"{_plain(SPACED)}\n### Pin 1\n{TOKENS}\n\n" + "\n".join(_rest(2)))
    after = _doc(f"{_plain(SPACED)}\n### Pin 1\n{part}\n\n{_plain(PADDED)}\n### Pin 2\n{TOKENS}\n\n" + "\n".join(_rest(3)))
    assert gate_decision(before, *_write(after)).verdict == "ALLOW"


@pytest.mark.parametrize("grows", [False, True], ids=["same size", "grows"])
def test_an_over_cap_original_keeping_its_row_ahead_of_a_heading_copy_keeps_its_size(grows):
    """As above with an over-cap body: the original's padded row reads invalid
    and its old row valid, so the original counts as having no valid
    override on both sides; it may keep its size, and not grow."""
    words = [f"o{k}" + "w" * 80 for k in range(20)]
    body = " ".join(words)
    after_body = body + " " + " ".join(_tokens("g", 0, 40)) if grows else body
    before = _doc(f"{_plain(SPACED)}\n### Pin 1\n{body}\n\n" + "\n".join(_rest(2)))
    after = _doc(f"{_plain(SPACED)}\n### Pin 1\n{' '.join(words[:19])}\n\n{_plain(PADDED)}\n### Pin 2\n{after_body}\n\n"
                 + "\n".join(_rest(3)))
    decision = gate_decision(before, *_write(after))
    if grows:
        assert (decision.verdict, decision.cause) == ("DENY", "size"), decision
    else:
        assert decision.verdict == "ALLOW", decision


def test_an_exact_copy_of_an_invalid_override_losing_the_heading_tiebreak_is_refused():
    """The copy keeps as much of the old text (its sentences swapped) but the
    original keeps the heading, so the original's reconfirmed row keeps the
    old row and the copy of the invalid override is checked on its own."""
    before = _named((BARE, "Orig", SENTENCES))
    after = _named((BARE, "Orig variant", SWAPPED), (_plain(WRITTEN, True), "Orig", SENTENCES))
    decision = gate_decision(before, *_write(after))
    assert (decision.verdict, decision.cause) == ("DENY", "override"), decision
    assert decision.reason == ("Pin cap violation (invalid override): Override rationale is 121 chars "
                               "(max: 120). Shorten it.")


# With no exact copy the same rule decides: the pin keeping the most of the
# old text keeps its own edited row's old row, and on an equal share the
# mapping's choice does, unless the texts read the same.
MOVED_ALL = TOKENS + " m0 m1 m2 m3"
NO_COPY = [
    ("the heading's pin keeping half padded, another keeping all reconfirmed",
     [(_plain(PADDED), "Orig", HALF), (_plain(SPACED, True), "Moved", MOVED_ALL)], "DENY"),
    ("the heading's pin keeping half reconfirmed, another keeping all padded",
     [(_plain(SPACED, True), "Orig", HALF), (_plain(PADDED), "Moved", MOVED_ALL)], "ALLOW"),
    ("equal halves reading differently, the heading's pin padded",
     [(_plain(PADDED), "Orig", HALF), (_plain(SPACED, True), "Moved", OTHER_HALF)], "ALLOW"),
    ("equal halves reading differently, the other pin padded",
     [(_plain(SPACED, True), "Orig", HALF), (_plain(PADDED), "Moved", OTHER_HALF)], "DENY"),
    ("equal halves reading the same, the heading's pin padded",
     [(_plain(PADDED), "Orig", HALF), (_plain(SPACED, True), "Moved", HALF)], "ALLOW"),
    ("equal halves reading the same, the other pin padded",
     [(_plain(SPACED, True), "Orig", HALF), (_plain(PADDED), "Moved", HALF)], "DENY"),
]


@pytest.mark.parametrize("name, after, verdict", NO_COPY, ids=[row[0] for row in NO_COPY])
def test_with_no_exact_copy_the_pin_keeping_most_of_the_old_text_keeps_its_row(name, after, verdict):
    decision = gate_decision(_named((_plain(SPACED), "Orig", TOKENS)), *_write(_named(*after)))
    assert decision.verdict == verdict, (name, decision)


def test_a_lone_padded_copy_on_a_new_over_cap_pin_is_no_contest():
    """The original keeps its body under a different valid override, and a new
    over-cap pin carries the only row standing for the old one, padded. With
    one row standing for it there is no contest, so the new pin gains no
    carry and is refused as a new over-cap pin without a valid override."""
    body = " ".join(f"o{k}" + "w" * 80 for k in range(20))
    after = _named((_plain("another rationale entirely, still within the cap"), "Orig", body + " extra words"),
                   (_plain(PADDED), "Copy", body))
    decision = gate_decision(_named((_plain(SPACED), "Orig", body)), *_write(after))
    assert (decision.verdict, decision.cause) == ("DENY", "size"), decision


# A pin merged from two continues both. 'Orig' is merged into 'Second', which
# keeps its own body and all of Orig's and carries Orig's override in place of
# its own; Orig keeps about half of its text. Over the cap, Orig's body runs
# past it and the merged pin grows.
SECOND_OWN = "the second pin's own rationale, well within the cap"
NO_OVERRIDE = "<!-- pinned: 2026-05-26 -->"


def _merge(old_row, rows, over_cap, merged_first):
    words = [f"o{k}" + "w" * (80 if over_cap else 0) for k in range(20)]
    second = " ".join(_tokens("s", 0, 20))
    before = _named((old_row, "Orig", " ".join(words)), (_plain(SECOND_OWN), "Second", second))
    pins = [(rows[0], "Orig", " ".join(words[:11])), (rows[1], "Second", second + " " + " ".join(words))]
    return before, _named(*(pins[::-1] if merged_first else pins))


MERGES = [
    ("Orig reconfirmed, the merged pin padded", (_plain(SPACED, True), _plain(PADDED)), ("ALLOW", None), ("DENY", "size")),
    ("Orig padded, the merged pin reconfirmed", (_plain(PADDED), _plain(SPACED, True)),
     ("DENY", "override"), ("DENY", "override")),
    ("Orig keeping no override, the merged pin padded", (NO_OVERRIDE, _plain(PADDED)), ("ALLOW", None), ("DENY", "size")),
    ("Orig padded, the merged pin keeping its own override", (_plain(PADDED), _plain(SECOND_OWN)),
     ("ALLOW", None), ("ALLOW", None)),
]


@pytest.mark.parametrize("merged_first", [False, True], ids=["Orig first", "merged pin first"])
@pytest.mark.parametrize("over_cap", [False, True], ids=["small", "over cap"])
@pytest.mark.parametrize("name, rows, small, over", MERGES, ids=[row[0] for row in MERGES])
def test_a_merged_pin_continues_the_old_pin_whose_text_it_keeps_most_of(name, rows, small, over, over_cap, merged_first):
    """The merged pin keeps the most of Orig's text, so it continues Orig as
    well as Second: its edited row takes Orig's old row ahead of Orig's own,
    and a padded copy on Orig is checked on its own. A merged pin that has
    grown past the cap with no valid override is refused on size."""
    before, after = _merge(_plain(SPACED), rows, over_cap, merged_first)
    decision = gate_decision(before, *_write(after))
    verdict, cause = over if over_cap else small
    assert decision.verdict == verdict, (name, decision)
    if cause:
        assert decision.cause == cause, (name, decision)


@pytest.mark.parametrize("orig_row", [CUT, NO_OVERRIDE], ids=["Orig keeping half reconfirmed", "Orig keeping no override"])
def test_an_over_cap_merged_pin_gains_no_exemption_by_reconfirming_an_invalid_override(orig_row):
    """Orig's old override reads 121 characters, and 120 once a reconfirmation
    follows it. The merged pin carries it reconfirmed: its row reads valid
    where the old one did not, so both pins count as having no valid
    override, and the merged pin, grown past the cap, is refused."""
    before, after = _merge(BARE, (orig_row, CUT), True, False)
    decision = gate_decision(before, *_write(after))
    assert (decision.verdict, decision.cause) == ("DENY", "size"), decision


def test_an_over_cap_pin_whose_padded_override_moves_to_a_pin_keeping_none_of_its_text_loses_its_exemption():
    """Orig keeps all of its over-cap body and loses its override row, which
    moves, padded, onto Second. Second keeps none of Orig's text, so it does
    not continue Orig and takes no carry from it: Orig is refused for no
    longer having a valid override."""
    body = " ".join(f"o{k}" + "w" * 80 for k in range(20))
    second = " ".join(_tokens("s", 0, 20))
    before = _named((_plain(SPACED), "Orig", body), (_plain(SECOND_OWN), "Second", second))
    after = _named((NO_OVERRIDE, "Orig", body), (_plain(PADDED), "Second", second))
    decision = gate_decision(before, *_write(after))
    assert (decision.verdict, decision.cause) == ("DENY", "size"), decision
    assert "no longer has a valid pin-size-override" in (decision.reason or ""), decision
