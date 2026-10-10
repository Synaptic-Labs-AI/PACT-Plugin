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


@pytest.mark.parametrize("separator", [" ", "\x85"], ids=["line separator", "next line"])
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
