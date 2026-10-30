"""The prose<->journal-registry seam for /PACT:prune-memory telemetry.

THIS TEST DELIBERATELY SPANS TWO OWNERS, and that is the reason it exists
rather than an awkwardness to apologise for.

`commands/prune-memory.md` (command prose) EMITS journal events.
`hooks/shared/session_journal.py` (Python) VALIDATES them. Neither file's
own test suite can see the other: the suites reading the prose carry no
`session_journal` import, and the suites exercising the registry never read
the prose. So the contract between them was correct only BY INSPECTION --
a rename, a field change or a typo on either side would drift silently,
with both suites green.

That is the same shape as two defects already found in this feature's CODE
phase: a contract spanning two agents' file boundaries, invisible from
inside either scope by construction. A test that belongs to neither owner
is the only kind that can hold it.

WHAT THIS DOES *NOT* COVER, stated so a later reader does not over-trust it:
the `outcome` VALUE set (`cancelled` / `no_candidates` / `unknown_state` /
`archive_refused` / `unverified_eviction`) has NO single source of truth.
`_REQUIRED_FIELDS_BY_TYPE` types `outcome` as `str`, not as a value set, and
the writer is not enum-gated -- so `outcome="banana"` validates green today.
Pinning those five strings HERE would mint a THIRD source of truth (prose,
this test, and nowhere authoritative) rather than connect two, so it is
deliberately not done. Tracked as a named residual; the fix is to constrain
the value set at the registry, which is where a value-set contract belongs.
"""
import importlib.util
import json
import re
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, _PLUGIN_ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def journal():
    return _load("session_journal", "hooks/shared/session_journal.py")


@pytest.fixture(scope="module")
def prune_md():
    return (_PLUGIN_ROOT / "commands" / "prune-memory.md").read_text(
        encoding="utf-8"
    )


@pytest.fixture(scope="module")
def pin_event_types(journal):
    """The registry's pin-related event types, read from the REGISTRY.

    Derived from the Python side rather than from a literal list here, so a
    newly registered `pin_*` type is picked up automatically and must then
    justify its absence from the prose.
    """
    return {
        name: fields
        for name, fields in journal._REQUIRED_FIELDS_BY_TYPE.items()
        if name.startswith("pin_")
    }


def test_registry_pin_types_are_the_expected_two(pin_event_types):
    """NON-VACUITY GATE for every test below that iterates this selection.

    A `pin_` prefix typo, a rename, or a registry restructure yields an
    EMPTY selection -- and an empty selection makes every loop below pass
    over nothing. That is the vacuous-loop green: a test reporting success
    while measuring nothing, which is the exact defect this whole feature
    exists to remove. Asserting the cardinality BEFORE anything iterates is
    what stops this suite shipping that defect inside the test written to
    close a seam.
    """
    assert set(pin_event_types) == {"pin_prune_skipped", "pin_pruned"}, (
        f"expected exactly the two pin telemetry types, got "
        f"{sorted(pin_event_types)}"
    )


def test_every_registered_pin_type_appears_in_the_prose(
    pin_event_types, prune_md
):
    """Driven FROM the registry, which catches drift in BOTH directions.

    Rename the registry key and the new name is absent from the prose ->
    fails. Rename it in the prose and the registered key is absent -> fails.
    A prose-driven check would only catch the first.
    """
    for event_type in pin_event_types:
        assert event_type in prune_md, (
            f"journal type {event_type!r} is registered in "
            f"_REQUIRED_FIELDS_BY_TYPE but never named in prune-memory.md — "
            "the emitting prose and the validating schema have drifted"
        )


def test_documented_payload_validates_against_the_shipped_schema(
    journal, prune_md, pin_event_types
):
    """The payload a curator is TOLD to emit must pass the schema that will
    validate it.

    This is stronger than comparing key names, and it is why the parse here
    is safe rather than a new drift surface: the prose carries a real
    heredoc JSON literal, so this extracts a bounded, machine-checkable
    example rather than parsing freeform prose. If either side drifts, the
    documented example stops validating.
    """
    # EACH FENCE IS PARSED AS A UNIT — the type and the payload are taken
    # from the SAME block. This replaces an earlier form that gathered two
    # flat lists over the document and `zip`ped them, which had three
    # defects, all silent:
    #   - a bare `--type` in PROSE joined the type list and SHIFTED the
    #     pairing, validating a payload against the WRONG schema — and
    #     passing;
    #   - `zip` TRUNCATED to the shorter list, so a surplus payload or type
    #     vanished and the completeness half went vacuous while green;
    #   - even at equal length the lists could be MIS-ORDERED, which a count
    #     assertion is blind to by construction.
    # Pairing within a block removes all three by construction rather than
    # detecting them. The first was found by `coder-prose` in this test,
    # which is my own.
    #
    # The ordering risk was NOT hypothetical: two emit sites exist, and a
    # swapped pairing was caught only because the two schemas happen to
    # differ enough to fail validation. That is the SCHEMAS doing the work,
    # not this test, and it stops the moment two events share a field shape.
    blocks = re.findall(r"```bash\n(.*?)```", prune_md, re.DOTALL)
    assert blocks, (
        "no ```bash fence found in prune-memory.md — this test cannot "
        "locate the emit sites it validates. Re-aim rather than deleting: "
        "an empty scan yields zero pairs, which would pass as "
        "'all payloads valid'."
    )

    pairs = []
    for block in blocks:
        types = re.findall(r"--type\s+(\S+)", block)
        payloads = re.findall(r"--stdin\s+<<'JSON'\n(.*?)\nJSON", block, re.DOTALL)
        if not types and not payloads:
            continue  # a fence that emits nothing (e.g. a plain CLI call)
        assert len(types) == 1 and len(payloads) == 1, (
            f"a bash fence carries {len(types)} `--type` invocation(s) and "
            f"{len(payloads)} JSON payload(s); each emit fence must carry "
            "exactly one of each so the pairing is unambiguous. Split the "
            "fence rather than relaxing this — a fence with two of either "
            "cannot be paired without reintroducing positional guessing."
        )
        pairs.append((types[0], payloads[0]))

    assert pairs, (
        "no bash fence contains BOTH a `--type` invocation and a JSON "
        "payload — this test cannot validate an emit it cannot locate."
    )

    for event_type, raw in pairs:
        assert event_type in pin_event_types, (
            f"prune-memory.md emits --type {event_type!r}, which is not "
            "registered in _REQUIRED_FIELDS_BY_TYPE. An unregistered type "
            "validates VACUOUSLY — the writer is not enum-gated."
        )
        event = dict(json.loads(raw), type=event_type, v=1)
        ok, message = journal._validate_event_schema(event)
        assert ok, (
            f"the payload prune-memory.md documents for {event_type!r} does "
            f"NOT validate against the shipped schema: {message}"
        )


def test_payload_selection_ignores_readback_but_not_duplicate_writes(
    journal, pin_event_types
):
    write = """python3 "{plugin_root}/hooks/shared/session_journal.py" write \
  --type pin_prune_skipped --session-dir '{session_dir}' --stdin <<'JSON'
{"outcome":"cancelled","pin_count":12,"age_distribution":{}}
JSON
"""
    readback = """python3 -c '
import json
' <<'JSON'
{"session_dir":"{session_dir}","exact":{},"nonempty":[],"absent":[]}
JSON
"""
    test_documented_payload_validates_against_the_shipped_schema(
        journal, f"```bash\n{write}{readback}```\n", pin_event_types
    )

    with pytest.raises(AssertionError, match="exactly one"):
        test_documented_payload_validates_against_the_shipped_schema(
            journal, f"```bash\n{write}{write}```\n", pin_event_types
        )


def test_emitted_outcomes_are_declared_in_the_exit_table(prune_md):
    """Narrow value-set pin for THIS event family only.

    WHY THIS EXISTS NOW WHEN THIS FILE'S OWN DOCSTRING DECLINED IT. The
    module header states that the `outcome` VALUE set has no SSOT and that
    pinning the values here would mint a THIRD source of truth. That was
    the right call when the value set was incidental. A design change made
    it load-bearing: the registry pins event TYPES, so a new type surfaces
    as a completeness failure, but NOTHING pins values — so reusing an
    existing outcome for a new situation passes every test we have.

    The cost is specific rather than cosmetic. `session_journal.py` calls
    `archive_refused` "the highest-value row — a run of refusals is the
    signal that the archival mechanism itself is broken." Reusing it for a
    case where the archive SUCCEEDED poisons the diagnostic that row
    exists to carry: a run of them reads as a broken archiver while every
    archive in the run worked. A false positive built into the monitoring
    channel of the feature whose purpose is a trustworthy audit trail.

    NO THIRD COPY IS MINTED. The exit table in prune-memory.md is treated
    as the SSOT and this test is only its checker — the values are read
    from the table, never restated here. That is the same shape as the
    type bridge above, one level down from type to value.

    WHAT THIS DOES NOT CATCH, stated because the bound is the point:
      - the TABLE itself declaring a wrong value — it is the source, so
        this cannot audit it;
      - SEMANTIC misuse, i.e. a payload correctly using a declared value
        for the wrong situation. That is the `archive_refused`-for-a-
        delete-unsafe case, and it is not mechanically decidable here;
      - runtime rejection. `_validate_event_schema` still accepts any
        string for `outcome`; general enum-gating of the registry is
        deliberately a separate cycle. This pins the DOCUMENT, not the
        writer.
    """
    declared = set(
        re.findall(
            r"^\|[^|]+\|\s*`pin_prune_skipped`\s*\|\s*`([a-z_]+)`\s*\|",
            prune_md,
            re.MULTILINE,
        )
    )
    assert declared, (
        "no `pin_prune_skipped` rows found in prune-memory.md's exit table "
        "— this test cannot check emitted values against a declaration it "
        "cannot locate. Re-aim the extraction rather than deleting this "
        "line: an empty declared set makes every membership check below "
        "pass vacuously."
    )

    blocks = re.findall(r"```bash\n(.*?)```", prune_md, re.DOTALL)
    emitted = set()
    for block in blocks:
        if "--type pin_prune_skipped" not in block:
            continue
        for raw in re.findall(r"<<'JSON'\n(.*?)\nJSON", block, re.DOTALL):
            value = json.loads(raw).get("outcome")
            if value is not None:
                emitted.add(value)

    assert emitted, (
        "no `pin_prune_skipped` payload carrying an `outcome` was found in "
        "a bash fence — nothing to check against the exit table."
    )

    undeclared = emitted - declared
    assert not undeclared, (
        f"payload(s) emit outcome(s) {sorted(undeclared)} that the exit "
        f"table does not declare (declared: {sorted(declared)}). An "
        "undeclared value validates green today — the schema types "
        "`outcome` as `str` — so this document check is the only thing "
        "standing between a typo and a poisoned audit trail."
    )


def test_the_validator_rejects_malformed_payloads(journal):
    """NON-VACUITY CONTROL for the test above.

    A validator that accepted everything would make the documented-payload
    check pass while measuring nothing. These prove it discriminates, so a
    green above means the example is genuinely well-formed.
    """
    base = {
        "type": "pin_prune_skipped", "v": 1, "outcome": "cancelled",
        "pin_count": 12,
        "age_distribution": {"oldest": 60, "newest": 3, "median": 33},
    }
    assert journal._validate_event_schema(base)[0], "control payload must pass"

    for description, mutation in [
        ("missing required field", {k: v for k, v in base.items()
                                    if k != "pin_count"}),
        ("wrong scalar type", dict(base, pin_count="12")),
        ("bool where int expected", dict(base, pin_count=True)),
        ("empty required string", dict(base, outcome="")),
        ("wrong container type", dict(base, age_distribution="oldest=60")),
    ]:
        ok, _ = journal._validate_event_schema(mutation)
        assert not ok, f"validator accepted a malformed payload: {description}"


def test_pin_pruned_cannot_be_emitted_for_an_unverified_eviction(journal):
    """The escape-hatch invariant, enforced STRUCTURALLY rather than by prose.

    An escape-hatch eviction has no `memory_id` — that is what makes it
    unverified. `pin_pruned` requires a non-empty one, so the success event
    is unemittable on that path by construction and such an eviction can
    only be filed under `pin_prune_skipped`. The schema enforces the
    semantic instead of trusting the prose to, which matters because prose
    rules failing is this feature's founding premise.
    """
    for value in ("", "   ", "\t"):
        ok, _ = journal._validate_event_schema({
            "type": "pin_pruned", "v": 1, "heading": "Some Pin",
            "memory_id": value, "pin_count": 12,
        })
        assert not ok, (
            f"pin_pruned validated with memory_id={value!r} — an eviction "
            "with no archive record could then be filed as a SUCCESS"
        )

    ok, _ = journal._validate_event_schema({
        "type": "pin_pruned", "v": 1, "heading": "Some Pin",
        "memory_id": "a" * 32, "pin_count": 12,
    })
    assert ok, "a well-formed pin_pruned event must still validate"
