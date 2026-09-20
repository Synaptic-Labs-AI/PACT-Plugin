"""Pin the two allowed-field lists inside one rejection envelope to each other.

A rejected save/update produces ONE error object carrying TWO descriptions of
the same allowed set: the prose ``message`` built by
``scripts.database._reject_unknown_columns`` and the machine-readable
``allowed_fields`` array ``scripts.cli`` attaches. They disagreed: the sentence
named the server-owned fields (``id``, ``created_at``, ``updated_at``) that the
array omitted, so a caller reading the sentence and a caller parsing the array
got different answers about the same call.

Why this survived: the CLI-layer tests for this envelope inject a FABRICATED
``ValueError`` (``side_effect=ValueError("... Allowed fields: context, goal")``)
rather than provoking a real rejection, so no test ever compared the real prose
against the real array. That is the gap these cases close -- every assertion
below runs the REAL rejection path.

The fix derives the prose list from ``allowed - _STRIPPED_ON_INGRESS``, the same
expression that defines ``CALLER_FACING_*``. Derivation is what makes the two
unable to drift, so the pin here is against a REGRESSION to a hand-maintained
list, not against the arithmetic.

Validation is deliberately NOT narrowed -- ``create`` still tolerates ``id`` and
``created_at``. Only what is ADVERTISED changed. ``test_stripped_fields_are_still_accepted``
is the arm that fails if someone "simplifies" the fix by narrowing the allowlist
itself, which would be a behaviour change wearing a cosmetic fix's clothes.
"""
import re

import pytest

from scripts.database import (
    ALLOWED_CREATE_COLUMNS,
    ALLOWED_UPDATE_COLUMNS,
    CALLER_FACING_CREATE_FIELDS,
    CALLER_FACING_UPDATE_FIELDS,
    _STRIPPED_ON_INGRESS,
    _reject_unknown_columns,
)

_ALLOWED_RE = re.compile(r"Allowed fields: (.+?)(?:\.|$)")


def _prose_fields(operation, allowed):
    """Provoke a REAL rejection and return the field set its sentence names."""
    with pytest.raises(ValueError) as excinfo:
        _reject_unknown_columns(
            {"definitely_not_a_column": "x"}, allowed, operation=operation
        )
    match = _ALLOWED_RE.search(str(excinfo.value))
    assert match, f"no 'Allowed fields:' clause in message: {excinfo.value}"
    return {f.strip() for f in match.group(1).split(",") if f.strip()}


@pytest.mark.parametrize(
    "operation,allowed,caller_facing",
    [
        ("save", ALLOWED_CREATE_COLUMNS, CALLER_FACING_CREATE_FIELDS),
        ("update", ALLOWED_UPDATE_COLUMNS, CALLER_FACING_UPDATE_FIELDS),
    ],
)
def test_prose_list_equals_the_machine_list(operation, allowed, caller_facing):
    """The sentence and the array describe the SAME set."""
    prose = _prose_fields(operation, allowed)
    assert prose == set(caller_facing), (
        "the error's prose field list has drifted from the caller-facing set "
        "cli.py puts in `allowed_fields`.\n"
        f"  in the sentence only: {sorted(prose - set(caller_facing))}\n"
        f"  in the array only:    {sorted(set(caller_facing) - prose)}"
    )


@pytest.mark.parametrize(
    "operation,allowed",
    [("save", ALLOWED_CREATE_COLUMNS), ("update", ALLOWED_UPDATE_COLUMNS)],
)
def test_server_owned_fields_are_not_advertised(operation, allowed):
    """No server-owned field is offered to the caller as settable.

    Guards the direction the original defect ran in: `updated_at` appeared in
    the sentence while being unsettable and unexplained by the parenthetical.
    """
    prose = _prose_fields(operation, allowed)
    leaked = prose & _STRIPPED_ON_INGRESS
    assert not leaked, f"{operation}: server-owned field(s) advertised: {sorted(leaked)}"


def test_prose_list_is_not_empty():
    """Non-vacuity guard for the two comparisons above.

    Both tests are set comparisons against `_prose_fields`. If the message
    format changed so the regex captured nothing, the helper's own assert fires
    -- but a capture that silently matched a SHORTER list would weaken them
    without failing. Pin that a well-known field survives the derivation.
    """
    prose = _prose_fields("save", ALLOWED_CREATE_COLUMNS)
    assert len(prose) >= 10, f"only {len(prose)} fields parsed: {sorted(prose)}"
    assert {"context", "goal", "lessons_learned"} <= prose


def test_stripped_fields_are_still_accepted():
    """Advertising changed; ACCEPTANCE did not.

    Fails if anyone narrows the allowlist itself instead of narrowing the
    message -- that would turn a documentation correction into a behaviour
    change, breaking callers that legitimately round-trip a full memory dict.

    The membership ASSERTION below is load-bearing and must come first. An
    earlier version parametrized over ``_STRIPPED_ON_INGRESS & ALLOWED_CREATE_COLUMNS``
    and a mutant that narrowed ``ALLOWED_CREATE_COLUMNS`` SURVIVED it: emptying
    that intersection generated zero cases, so the guard evaporated at
    collection time and reported "skipped" rather than failing. A guard whose
    population is derived from the constant it is guarding cannot catch a
    mutation of that constant -- the knob that aims it also empties it. Naming
    the expected members directly is what makes the mutant fail loudly.
    """
    expected = {"id", "created_at", "updated_at"}
    assert expected == set(_STRIPPED_ON_INGRESS), (
        "the server-owned set changed; update this test deliberately rather "
        f"than letting it track the constant: {sorted(_STRIPPED_ON_INGRESS)}"
    )
    missing = expected - set(ALLOWED_CREATE_COLUMNS)
    assert not missing, (
        f"create no longer tolerates {sorted(missing)}. Narrowing the ALLOWLIST "
        "is a behaviour change; only the advertised list was meant to narrow."
    )
    for field in sorted(expected):
        _reject_unknown_columns({field: "x"}, ALLOWED_CREATE_COLUMNS, operation="save")


# --- the guarantee is STRUCTURAL, not call-site-dependent --------------------

def _envelope_from_a_real_rejection(argv):
    """Drive a REAL rejection through the CLI and return its parsed envelope.

    NOT A MOCK, and that is the whole point of this arm. Every existing
    envelope test injects a bare ``ValueError`` from a stubbed PACTMemory,
    which carries no field list -- so all of them exercise cli.py's FALLBACK
    and none of them can see whether the two halves of a real error object
    agree. This drives `_reject_unknown_columns` for real.
    """
    import json
    import io
    import contextlib
    from scripts.cli import build_parser, cmd_save, cmd_update

    args = build_parser().parse_args(argv)
    handler = cmd_save if argv[0] == "save" else cmd_update
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        try:
            handler(args)
        except SystemExit:
            pass
    return json.loads(err.getvalue())


def _a_real_memory_id():
    """Save a record and return its id, for the update arm.

    The update path checks EXISTENCE BEFORE FIELDS -- measured: a bogus id
    returns a NOT_FOUND envelope and never reaches `_reject_unknown_columns`,
    so an arm built on a fabricated id tests the wrong branch and reports a
    parse failure that looks like a product defect. It needs a real record.
    """
    import json
    import io
    import contextlib
    from scripts.cli import build_parser, cmd_save

    args = build_parser().parse_args(["save", '{"context": "envelope probe"}'])
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            cmd_save(args)
        except SystemExit:
            pass
    return json.loads(out.getvalue())["result"]["memory_id"]


@pytest.mark.parametrize("operation", ["save", "update"], ids=["save", "update"])
def test_the_envelopes_two_halves_agree_on_a_real_rejection(operation):
    """THE SENTENCE AND THE ARRAY, in one envelope, from one call.

    The sibling arms above compare the prose against the CALLER_FACING_*
    constant. That pins the derivation, and it CANNOT see the defect this
    arm exists for: cli.py chose between two constants by hand, so the two
    halves agreed only while every call site passed the `allowed` set
    matching the constant cli.py picked for that operation. A third
    operation, or the two constants transposed, would have split them again
    while every assertion above still passed.

    This reads both halves off the SAME envelope, so it is indifferent to
    which constant cli.py would have chosen and fails if they ever describe
    different sets.
    """
    if operation == "save":
        argv = ["save", '{"context": "x", "bogus_field": 1}']
    else:
        argv = ["update", _a_real_memory_id(), '{"bogus_field": 1}']
    envelope = _envelope_from_a_real_rejection(argv)

    assert envelope["ok"] is False, f"expected a rejection, got {envelope}"
    # cli.py APPENDS a parenthetical note to the message it renders -- e.g.
    # "(Note: 'id' and 'created_at' are accepted on save ...)". `_ALLOWED_RE`
    # runs to the first period, and that note has none until its own end, so
    # an unstripped parse swallows it and reports a field named after half a
    # sentence. The sibling arms never meet this because they read the RAW
    # exception message, which carries no note. Cut it first.
    message = envelope["message"].split(" (Note:")[0]
    match = _ALLOWED_RE.search(message)
    assert match, (
        f"no 'Allowed fields:' clause in the envelope's own message, so this "
        f"arm has nothing to compare: {envelope}"
    )
    from_sentence = {f.strip() for f in match.group(1).split(",") if f.strip()}
    from_array = set(envelope["allowed_fields"])

    assert from_sentence, "the sentence named no fields -- the parse is empty"
    assert from_sentence == from_array, (
        "the two halves of ONE error envelope describe different field sets.\n"
        f"  in the sentence only: {sorted(from_sentence - from_array)}\n"
        f"  in the array only:    {sorted(from_array - from_sentence)}"
    )
