#!/bin/bash
# scripts/verify-spec-fixtures.sh
# Validates every fixture instance under spec/schemas/fixtures/ against its
# schema, and every schema's embedded examples against that schema. Uses a
# python3 STDLIB-ONLY subset validator for JSON Schema draft 2020-12 — no
# third-party validator is a repository dependency, so this script carries
# its own implementation of exactly the constructs the four schemas use.
#
# Rules:
# - Fixture naming contract: fixtures/{kind}.{valid|invalid}-N.json is
#   validated against {kind}.schema.json. A "valid" fixture producing any
#   error, or an "invalid" fixture producing none, is a failure.
# - Embedded examples: each schema's top-level "examples" instances must
#   validate against the schema itself.
# - CONSTRUCT GUARD (fail-loud vacuity protection): before validating, every
#   schema is swept for validation keywords outside the supported set below.
#   An unsupported construct (e.g. $ref, unevaluatedProperties) fails the run
#   loudly instead of validating vacuously — the known failure mode of a
#   subset validator meeting a schema that outgrew it.
# - Supported constructs: type, required, properties, items, enum,
#   const (DEEP compare — array- and object-valued consts compare by
#   structure, not identity), pattern (unanchored re.search), anyOf, oneOf
#   (exactly-one), if/then/else, additionalProperties (boolean or subschema),
#   minItems, minLength, minimum. "format" is annotation-only per the
#   draft 2020-12 default and is deliberately not asserted.
# - A missing schemas dir or empty fixtures dir is a skip, not a failure
#   (authoring is incremental).
#
# Run from the repository root. Exit codes: 0 = pass/skip, 1 = failure.
# --self-test seeds known-bad fixture/schema combinations and asserts each
# goes RED (a green never observed failing is indistinguishable from broken).
#
# Internal env override (self-test plumbing): VERIFY_SCHEMAS_DIR.

set -u

python3 - "${1:-}" <<'PYEOF'
import json
import os
import re
import sys

SCHEMAS_DIR = os.environ.get("VERIFY_SCHEMAS_DIR", "spec/schemas")

ANNOTATION_KEYWORDS = {
    "$schema", "$id", "$comment", "title", "description", "examples", "default",
}
SUPPORTED_KEYWORDS = {
    "type", "required", "properties", "items", "enum", "const", "pattern",
    "anyOf", "oneOf", "if", "then", "else", "additionalProperties",
    "minItems", "minLength", "minimum", "format",
}

TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def sweep_unsupported(schema, path="$"):
    """Yield (json_path, keyword) for every validation keyword outside the
    supported set. Walks only schema positions — property NAMES under
    'properties' are data, not keywords."""
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key in ANNOTATION_KEYWORDS:
                continue
            if key == "properties" and isinstance(value, dict):
                for prop_name, subschema in value.items():
                    yield from sweep_unsupported(subschema, f"{path}.properties.{prop_name}")
                continue
            if key in ("anyOf", "oneOf") and isinstance(value, list):
                for i, sub in enumerate(value):
                    yield from sweep_unsupported(sub, f"{path}.{key}[{i}]")
                continue
            if key in ("items", "if", "then", "else", "additionalProperties"):
                yield from sweep_unsupported(value, f"{path}.{key}")
                if key not in SUPPORTED_KEYWORDS:
                    yield (f"{path}.{key}", key)
                continue
            if key not in SUPPORTED_KEYWORDS:
                yield (f"{path}.{key}", key)


def validate(instance, schema, path="$"):
    """Return a list of error strings for instance against schema (subset
    of draft 2020-12; see the header for the supported construct set)."""
    errors = []
    if schema is True or schema == {}:
        return errors
    if schema is False:
        return [f"{path}: schema 'false' admits nothing"]

    stype = schema.get("type")
    if stype is not None:
        allowed = stype if isinstance(stype, list) else [stype]
        if not any(TYPE_CHECKS[t](instance) for t in allowed if t in TYPE_CHECKS):
            errors.append(f"{path}: type is not {stype}")
            return errors  # further keyword checks assume the type matched

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value {instance!r} not in enum {schema['enum']}")
    if "const" in schema and instance != schema["const"]:
        # Deep structural compare: == on parsed JSON compares arrays and
        # objects element-wise, which is what an array-valued const needs.
        errors.append(f"{path}: value {instance!r} != const {schema['const']!r}")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: length {len(instance)} < minLength {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: {instance!r} does not match pattern {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} < minimum {schema['minimum']}")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: {len(instance)} item(s) < minItems {schema['minItems']}")
        if "items" in schema:
            for i, item in enumerate(instance):
                errors.extend(validate(item, schema["items"], f"{path}[{i}]"))

    if isinstance(instance, dict):
        for field in schema.get("required", []):
            if field not in instance:
                errors.append(f"{path}: required property {field!r} missing")
        props = schema.get("properties", {})
        for key, value in instance.items():
            if key in props:
                errors.extend(validate(value, props[key], f"{path}.{key}"))
        ap = schema.get("additionalProperties")
        if ap is False:
            extras = sorted(set(instance) - set(props))
            if extras:
                errors.append(f"{path}: additional properties not allowed: {extras}")
        elif isinstance(ap, dict):
            for key, value in instance.items():
                if key not in props:
                    errors.extend(validate(value, ap, f"{path}.{key}"))

    if "anyOf" in schema:
        branches = [validate(instance, sub, path) for sub in schema["anyOf"]]
        if not any(not b for b in branches):
            errors.append(f"{path}: no anyOf branch validates")
    if "oneOf" in schema:
        valid_count = sum(1 for sub in schema["oneOf"] if not validate(instance, sub, path))
        if valid_count != 1:
            errors.append(f"{path}: {valid_count} oneOf branch(es) validate (exactly 1 required)")
    if "if" in schema:
        if not validate(instance, schema["if"], path):
            if "then" in schema:
                errors.extend(validate(instance, schema["then"], path))
        elif "else" in schema:
            errors.extend(validate(instance, schema["else"], path))

    return errors


def run_checks(schemas_dir):
    failures = []
    checked = 0
    if not os.path.isdir(schemas_dir):
        print(f"SKIP: {schemas_dir}/ not found (not yet authored)")
        return failures, checked
    schemas = {}
    for fname in sorted(os.listdir(schemas_dir)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(schemas_dir, fname)
        try:
            with open(path) as f:
                doc = json.load(f)
        except (OSError, ValueError) as exc:
            failures.append(f"{fname}: unparseable JSON: {exc}")
            continue
        kind = fname.split(".")[0]
        schemas[kind] = (fname, doc)
        unsupported = list(sweep_unsupported(doc))
        for json_path, keyword in unsupported:
            failures.append(
                f"{fname}: UNSUPPORTED construct {keyword!r} at {json_path} — "
                f"extend the subset validator before this schema can be gated (vacuity guard)"
            )
    if not schemas:
        print(f"SKIP: no *.json schemas in {schemas_dir} yet")
        return failures, checked

    # Embedded examples validate against their own schema.
    for kind, (fname, doc) in sorted(schemas.items()):
        for i, example in enumerate(doc.get("examples", [])):
            checked += 1
            errs = validate(example, doc)
            for e in errs:
                failures.append(f"{fname} examples[{i}]: {e}")

    fixtures_dir = os.path.join(schemas_dir, "fixtures")
    if not os.path.isdir(fixtures_dir):
        print(f"SKIP: {fixtures_dir}/ not found (not yet authored)")
        return failures, checked
    fixture_names = sorted(f for f in os.listdir(fixtures_dir) if f.endswith(".json"))
    if not fixture_names:
        print(f"SKIP: no fixtures in {fixtures_dir} yet")
        return failures, checked

    for fname in fixture_names:
        match = re.fullmatch(r"([a-z]+)\.(valid|invalid)-[0-9]+\.json", fname)
        if not match:
            failures.append(f"fixtures/{fname}: name does not match {{kind}}.{{valid|invalid}}-N.json")
            continue
        kind, expectation = match.group(1), match.group(2)
        if kind not in schemas:
            failures.append(f"fixtures/{fname}: no schema {kind}.schema.json to validate against")
            continue
        try:
            with open(os.path.join(fixtures_dir, fname)) as f:
                instance = json.load(f)
        except (OSError, ValueError) as exc:
            failures.append(f"fixtures/{fname}: unparseable JSON: {exc}")
            continue
        checked += 1
        errs = validate(instance, schemas[kind][1])
        if expectation == "valid" and errs:
            for e in errs[:4]:
                failures.append(f"fixtures/{fname}: expected VALID but: {e}")
        elif expectation == "invalid" and not errs:
            failures.append(
                f"fixtures/{fname}: expected INVALID but validates cleanly — "
                f"the twin no longer exercises its named constraint"
            )
    return failures, checked


def self_test():
    import contextlib
    import io
    import tempfile

    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:pact:spec:0.0.0:probe.schema.json",
        "$comment": "self-test probe schema",
        "type": "object",
        "required": ["name", "tags"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "tags": {"const": ["a", "b"]},
        },
        "examples": [{"name": "ok", "tags": ["a", "b"]}],
    }

    def seed(tmpdir, schema_doc, fixtures):
        os.makedirs(os.path.join(tmpdir, "fixtures"), exist_ok=True)
        with open(os.path.join(tmpdir, "probe.schema.json"), "w") as f:
            json.dump(schema_doc, f)
        for fname, instance in fixtures.items():
            with open(os.path.join(tmpdir, "fixtures", fname), "w") as f:
                json.dump(instance, f)

    good_fixtures = {
        "probe.valid-1.json": {"name": "ok", "tags": ["a", "b"]},
        "probe.invalid-1.json": {"name": "", "tags": ["a", "b"]},
    }

    cases = [
        ("clean control stays GREEN", schema, dict(good_fixtures), 0, None),
        ("valid fixture that violates goes RED", schema,
         {**good_fixtures, "probe.valid-2.json": {"tags": ["a", "b"]}}, 1, "expected VALID"),
        ("invalid fixture that validates goes RED", schema,
         {**good_fixtures, "probe.invalid-2.json": {"name": "fine", "tags": ["a", "b"]}}, 1, "expected INVALID"),
        ("array-valued const mismatch is caught (deep compare)", schema,
         {**good_fixtures, "probe.valid-3.json": {"name": "ok", "tags": ["a", "b", "c"]}}, 1, "!= const"),
        ("unsupported construct ($ref) goes RED", {**schema, "$ref": "#/nowhere"},
         dict(good_fixtures), 1, "UNSUPPORTED construct"),
        ("failing embedded example goes RED",
         {**schema, "examples": [{"name": "", "tags": ["a", "b"]}]},
         dict(good_fixtures), 1, "examples[0]"),
    ]

    print("=== Self-Test (known-bad fixtures must go RED) ===")
    fails = 0
    for name, schema_doc, fixtures, expected_rc, expected_msg in cases:
        with tempfile.TemporaryDirectory() as tmpdir:
            seed(tmpdir, schema_doc, fixtures)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                failures, _ = run_checks(tmpdir)
            rc = 1 if failures else 0
            msg_ok = expected_msg is None or any(expected_msg in e for e in failures)
            if rc == expected_rc and msg_ok:
                print(f"✓ {name}")
            else:
                print(f"✗ {name}: rc={rc} (expected {expected_rc}); failures={failures}")
                fails += 1
    if fails:
        print(f"SELF-TEST FAILED ({fails} case(s))")
        sys.exit(1)
    print("SELF-TEST PASSED")
    sys.exit(0)


if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
    self_test()

print("=== Spec Fixture Validation ===")
print("")
failures, checked = run_checks(SCHEMAS_DIR)
print("")
print("=== Summary ===")
if failures:
    for failure in failures:
        print(f"✗ {failure}")
    print(f"Instances checked: {checked}; failures: {len(failures)}")
    print("FIXTURE VALIDATION FAILED")
    sys.exit(1)
print(f"Instances checked: {checked}; failures: 0")
print("FIXTURE VALIDATION PASSED")
sys.exit(0)
PYEOF
