#!/bin/bash
# scripts/verify-spec-schema-drift.sh
# Drift tripwire between spec/schemas/*.json and the code files those schemas
# were extracted from. Uses python3 stdlib only; source constants are read by
# AST parse (no imports, no side effects).
#
# Checks, per schema file:
# - Valid JSON (parse failure is a failure, not a skip).
# - "$schema" declares draft 2020-12.
# - "$id" matches urn:pact:spec:<semver>:<basename>.
# - "$comment" extraction anchor present and non-empty; every file path it
#   cites must exist (a vanished source file means the pin has drifted).
#   Prose-sourced shapes (handoff, signal, rejection) are covered by this
#   anchor check — their lifecycle semantics live in prose, not here.
#
# Constant compares against source (fail-loud when the source constant is
# missing — a renamed constant is drift, not a skip):
# - teachback.schema.json: required field set, reasoning_reconstruction
#   sub-keys, and the acknowledgment enum must equal the source constants
#   (TEACHBACK_REQUIRED_FIELDS, TEACHBACK_REQUIRED_SUBKEYS,
#   TEACHBACK_VARIETY_ACK_VALID_VALUES). reasoning_reconstruction must NOT be
#   in the schema's required list (its requiredness varies by variety band —
#   lifecycle prose territory, schemas are shape-only).
# - handoff.schema.json: required field set must equal the gate's
#   _HANDOFF_REQUIRED_FIELDS (hooks/task_lifecycle_gate.py) MINUS
#   reasoning_chain — the ratified canon leaves reasoning_chain optional in
#   shape while the as-built gate checks it at advisory severity (the
#   schema's own $comment records this deviation). reasoning_chain must be
#   declared as a property and must NOT be required.
# - signal.schema.json: the top-level level enum must equal (case-
#   insensitively) the severity classes derived from SYSTEM_TASK_PREFIXES
#   (hooks/shared/constants.py) minus the "Phase:" prefix — the enum's
#   lowercase "blocker" is protocol prose convention (coordinator-triaged),
#   so the compare is on uppercased names.
# - Every enum in every schema is compared against the acknowledgment values,
#   the wait-reason vocabulary, and the resolver vocabulary: an enum that
#   overlaps one of those sets in 2+ members without being equal is drift.
# - Variety band cuts (COMPACT_MAX / ORCHESTRATE_MAX / PLAN_MODE_MAX) are
#   printed for the manual prose cross-check; band rules are prose, not
#   schema shapes, so no schema compare applies.
#
# A missing spec/schemas/ directory or an individual absent schema is skipped
# with a clear message (authoring is incremental).
#
# Run from the repository root. Exit codes: 0 = pass/skip, 1 = drift found,
# 2 = environment error (source files unreadable).
# --self-test seeds known-bad schema fixtures and asserts each goes RED.
#
# Internal env override (self-test plumbing): VERIFY_SCHEMAS_DIR.

set -u

python3 - "${1:-}" <<'PYEOF'
import ast
import json
import os
import re
import sys

SCHEMAS_DIR = os.environ.get("VERIFY_SCHEMAS_DIR", "spec/schemas")
SOURCE_DIR = "pact-plugin/hooks/shared"
TEACHBACK_SOURCE = os.path.join(SOURCE_DIR, "teachback_schema.py")
WAIT_SOURCE = os.path.join(SOURCE_DIR, "intentional_wait.py")
VARIETY_SOURCE = os.path.join(SOURCE_DIR, "variety_scorer.py")
GATE_SOURCE = "pact-plugin/hooks/task_lifecycle_gate.py"
CONSTANTS_SOURCE = os.path.join(SOURCE_DIR, "constants.py")

DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
ID_RE = re.compile(r"^urn:pact:spec:[0-9]+\.[0-9]+\.[0-9]+:(?P<name>[A-Za-z0-9_.-]+)$")
PATH_TOKEN_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:py|md)")


def extract_constants(path, names):
    """AST-extract named top-level constants (tuples/frozensets/ints of
    literals). Fail loud on anything missing — a renamed constant is drift."""
    with open(path) as f:
        tree = ast.parse(f.read(), filename=path)
    found = {}
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
            value = node.value
        else:
            continue
        for name in targets:
            if name not in names:
                continue
            if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                    and value.func.id == "frozenset" and len(value.args) == 1):
                found[name] = frozenset(ast.literal_eval(value.args[0]))
            else:
                found[name] = ast.literal_eval(value)
    missing = [n for n in names if n not in found]
    if missing:
        raise KeyError(f"{path}: constant(s) not found: {missing}")
    return found


def iter_enums(node, path="$"):
    """Yield (json_path, enum_list) for every 'enum' list in a schema doc."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "enum" and isinstance(value, list):
                yield path, value
            else:
                yield from iter_enums(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from iter_enums(item, f"{path}[{i}]")


def find_property_names(node, acc):
    """Collect every property name declared anywhere in the schema."""
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            acc.update(props.keys())
        for value in node.values():
            find_property_names(value, acc)
    elif isinstance(node, list):
        for item in node:
            find_property_names(item, acc)


def find_object_with_required(node, member_names):
    """Find a subschema whose 'required' list contains all member_names;
    return that required list, else None."""
    if isinstance(node, dict):
        required = node.get("required")
        if isinstance(required, list) and set(member_names) <= set(required):
            return required
        for value in node.values():
            hit = find_object_with_required(value, member_names)
            if hit is not None:
                return hit
    elif isinstance(node, list):
        for item in node:
            hit = find_object_with_required(item, member_names)
            if hit is not None:
                return hit
    return None


class Checker:
    def __init__(self, constants):
        self.errors = []
        self.constants = constants

    def fail(self, msg):
        self.errors.append(msg)

    def check_conventions(self, path, doc):
        basename = os.path.basename(path)
        if doc.get("$schema") != DRAFT_2020_12:
            self.fail(f"{basename}: $schema is {doc.get('$schema')!r}, expected {DRAFT_2020_12!r}")
        schema_id = doc.get("$id", "")
        match = ID_RE.fullmatch(schema_id) if isinstance(schema_id, str) else None
        if not match:
            self.fail(f"{basename}: $id {schema_id!r} does not match urn:pact:spec:<semver>:<name>")
        elif match.group("name") != basename:
            self.fail(f"{basename}: $id names {match.group('name')!r}, file is {basename!r}")
        comment = doc.get("$comment")
        if not isinstance(comment, str) or not comment.strip():
            self.fail(f"{basename}: missing or empty $comment extraction anchor")
            return
        for token in PATH_TOKEN_RE.findall(comment):
            # Citations may be repo-relative or plugin-relative (the schemas'
            # extraction pins cite plugin-internal paths like hooks/... or
            # protocols/...); bare filenames resolve against the source dir.
            candidates = [token, os.path.join("pact-plugin", token), os.path.join(SOURCE_DIR, token)]
            if not any(os.path.isfile(c) for c in candidates):
                self.fail(f"{basename}: $comment cites {token!r} which does not exist in the repository")

    def check_enum_drift(self, path, doc):
        basename = os.path.basename(path)
        vocabularies = {
            "acknowledgment values": set(self.constants["TEACHBACK_VARIETY_ACK_VALID_VALUES"]),
            "wait reasons": set(self.constants["KNOWN_REASONS"]),
            "wait resolvers": set(self.constants["KNOWN_RESOLVERS"]),
        }
        for json_path, enum in iter_enums(doc):
            if ".if." in json_path or json_path.endswith(".if"):
                # Conditional selectors ("if" subschemas) legitimately name a
                # SUBSET of an enum to trigger a then-branch; the canonical
                # full-set enum is enforced separately (check_teachback).
                continue
            enum_set = set(v for v in enum if isinstance(v, str))
            for vocab_name, vocab in vocabularies.items():
                overlap = enum_set & vocab
                if len(overlap) >= 2 and enum_set != vocab:
                    self.fail(
                        f"{basename}: enum at {json_path} overlaps {vocab_name} but is not equal — "
                        f"schema {sorted(enum_set)} vs source {sorted(vocab)}"
                    )

    def check_teachback(self, path, doc):
        basename = os.path.basename(path)
        required_fields = set(self.constants["TEACHBACK_REQUIRED_FIELDS"])
        subkeys = set(self.constants["TEACHBACK_REQUIRED_SUBKEYS"])
        ack_values = set(self.constants["TEACHBACK_VARIETY_ACK_VALID_VALUES"])

        top_required = doc.get("required")
        if not isinstance(top_required, list):
            self.fail(f"{basename}: no top-level required list")
        else:
            if set(top_required) != required_fields:
                self.fail(
                    f"{basename}: required field set drift — schema {sorted(top_required)} "
                    f"vs source {sorted(required_fields)}"
                )
            if "reasoning_reconstruction" in top_required:
                self.fail(f"{basename}: reasoning_reconstruction must not be required (band-dependent; prose territory)")

        declared = set()
        find_property_names(doc, declared)
        for field in sorted(required_fields | subkeys | {"reasoning_reconstruction"}):
            if field not in declared:
                self.fail(f"{basename}: property {field!r} not declared anywhere in the schema")

        if find_object_with_required(doc, subkeys) is None:
            self.fail(f"{basename}: no subschema requires the reasoning_reconstruction sub-keys {sorted(subkeys)}")

        has_ack_enum = any(set(e) == ack_values for _, e in iter_enums(doc))
        if not has_ack_enum:
            self.fail(f"{basename}: no enum equals the acknowledgment values {sorted(ack_values)}")

    def check_handoff(self, path, doc):
        basename = os.path.basename(path)
        gate_fields = set(self.constants["_HANDOFF_REQUIRED_FIELDS"])
        expected_required = gate_fields - {"reasoning_chain"}
        top_required = doc.get("required")
        if not isinstance(top_required, list):
            self.fail(f"{basename}: no top-level required list")
            return
        if set(top_required) != expected_required:
            self.fail(
                f"{basename}: required field set drift — schema {sorted(top_required)} "
                f"vs gate-derived {sorted(expected_required)} "
                f"(_HANDOFF_REQUIRED_FIELDS minus reasoning_chain)"
            )
        if "reasoning_chain" in top_required:
            self.fail(f"{basename}: reasoning_chain must not be required (optional in shape per the ratified canon)")
        declared = set()
        find_property_names(doc, declared)
        for field in sorted(gate_fields):
            if field not in declared:
                self.fail(f"{basename}: property {field!r} not declared anywhere in the schema")

    def check_signal(self, path, doc):
        basename = os.path.basename(path)
        prefixes = self.constants["SYSTEM_TASK_PREFIXES"]
        expected_levels = {p.rstrip(":").upper() for p in prefixes} - {"PHASE"}
        level_enum = (doc.get("properties", {}).get("level", {}) or {}).get("enum")
        if not isinstance(level_enum, list):
            self.fail(f"{basename}: no properties.level.enum found")
            return
        schema_levels = {str(v).upper() for v in level_enum}
        if schema_levels != expected_levels:
            self.fail(
                f"{basename}: level enum drift — schema {sorted(schema_levels)} vs "
                f"SYSTEM_TASK_PREFIXES-derived {sorted(expected_levels)} (case-insensitive compare)"
            )


def load_constants():
    constants = {}
    constants.update(extract_constants(TEACHBACK_SOURCE, [
        "TEACHBACK_REQUIRED_FIELDS",
        "TEACHBACK_REQUIRED_SUBKEYS",
        "TEACHBACK_VARIETY_ACK_VALID_VALUES",
    ]))
    constants.update(extract_constants(WAIT_SOURCE, ["KNOWN_REASONS", "KNOWN_RESOLVERS"]))
    constants.update(extract_constants(VARIETY_SOURCE, ["COMPACT_MAX", "ORCHESTRATE_MAX", "PLAN_MODE_MAX"]))
    constants.update(extract_constants(GATE_SOURCE, ["_HANDOFF_REQUIRED_FIELDS"]))
    constants.update(extract_constants(CONSTANTS_SOURCE, ["SYSTEM_TASK_PREFIXES"]))
    return constants


def run_checks(schemas_dir, constants):
    checker = Checker(constants)
    if not os.path.isdir(schemas_dir):
        print(f"SKIP: {schemas_dir}/ not found (not yet authored)")
        return checker, 0
    schema_paths = sorted(
        os.path.join(schemas_dir, f) for f in os.listdir(schemas_dir) if f.endswith(".json")
    )
    if not schema_paths:
        print(f"SKIP: no *.json files in {schemas_dir} yet")
        return checker, 0
    for path in schema_paths:
        basename = os.path.basename(path)
        try:
            with open(path) as f:
                doc = json.load(f)
        except (OSError, ValueError) as exc:
            checker.fail(f"{basename}: unparseable JSON: {exc}")
            continue
        checker.check_conventions(path, doc)
        checker.check_enum_drift(path, doc)
        if basename.startswith("teachback"):
            checker.check_teachback(path, doc)
        elif basename.startswith("handoff"):
            checker.check_handoff(path, doc)
        elif basename.startswith("signal"):
            checker.check_signal(path, doc)
    return checker, len(schema_paths)


def good_teachback_schema(constants):
    fields = list(constants["TEACHBACK_REQUIRED_FIELDS"])
    subkeys = list(constants["TEACHBACK_REQUIRED_SUBKEYS"])
    ack_values = list(constants["TEACHBACK_VARIETY_ACK_VALID_VALUES"])
    properties = {name: {"type": "string"} for name in fields}
    properties["variety_acknowledgment"] = {
        "type": "object",
        "properties": {
            "rationale_articulates_this_dispatch": {"enum": ack_values},
            "concern": {"type": "string"},
        },
        "required": ["rationale_articulates_this_dispatch"],
    }
    properties["reasoning_reconstruction"] = {
        "type": "object",
        "properties": {name: {"type": "string"} for name in subkeys},
        "required": subkeys,
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:pact:spec:0.1.0:teachback.schema.json",
        "$comment": "Normative appendix; extracted from teachback_schema.py",
        "type": "object",
        "required": fields,
        "properties": properties,
    }


def good_handoff_schema(constants):
    fields = [f for f in constants["_HANDOFF_REQUIRED_FIELDS"] if f != "reasoning_chain"]
    properties = {name: {"type": "array"} for name in fields}
    properties["reasoning_chain"] = {"type": "string"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:pact:spec:0.1.0:handoff.schema.json",
        "$comment": "Normative appendix; extracted from hooks/task_lifecycle_gate.py",
        "type": "object",
        "required": fields,
        "properties": properties,
    }


def good_signal_schema(constants):
    levels = sorted({p.rstrip(":") for p in constants["SYSTEM_TASK_PREFIXES"]} - {"Phase"})
    levels = [lv if lv != "BLOCKER" else "blocker" for lv in levels]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:pact:spec:0.1.0:signal.schema.json",
        "$comment": "Normative appendix; extracted from constants.py",
        "type": "object",
        "required": ["level"],
        "properties": {"level": {"enum": levels}},
    }


def self_test(constants):
    import contextlib
    import copy
    import io
    import tempfile

    good = good_teachback_schema(constants)

    def mutate_enum(doc):
        doc["properties"]["variety_acknowledgment"]["properties"][
            "rationale_articulates_this_dispatch"]["enum"] = ["yes", "no"]

    def mutate_comment(doc):
        del doc["$comment"]

    def mutate_ghost_source(doc):
        doc["$comment"] = "extracted from vanished_module.py"

    def mutate_id(doc):
        doc["$id"] = "https://example.com/teachback.schema.json"

    def mutate_required(doc):
        doc["required"] = [f for f in doc["required"] if f != "variety_acknowledgment"]

    def mutate_rr_required(doc):
        doc["required"] = doc["required"] + ["reasoning_reconstruction"]

    def mutate_if_subset(doc):
        # A subset enum under an "if" conditional selector is a legitimate
        # then-branch trigger, NOT drift; the full-set enum stays in place.
        doc["if"] = {
            "properties": {"rationale_articulates_this_dispatch": {"enum": ["no", "concern"]}}
        }
        doc["then"] = {"required": ["variety_acknowledgment"]}

    good_handoff = good_handoff_schema(constants)
    good_signal = good_signal_schema(constants)

    def mutate_handoff_drop_required(doc):
        doc["required"] = [f for f in doc["required"] if f != "uncertainty"]

    def mutate_handoff_require_rc(doc):
        doc["required"] = doc["required"] + ["reasoning_chain"]

    def mutate_signal_drop_level(doc):
        doc["properties"]["level"]["enum"] = [
            v for v in doc["properties"]["level"]["enum"] if v != "blocker"
        ]

    # (name, target basename, mutate, expected_rc, expected_msg)
    cases = [
        ("clean fixture stays GREEN", "teachback", None, 0, None),
        ("acknowledgment enum drift goes RED", "teachback", mutate_enum, 1, "overlaps acknowledgment values"),
        ("missing $comment anchor goes RED", "teachback", mutate_comment, 1, "$comment"),
        ("$comment citing vanished source goes RED", "teachback", mutate_ghost_source, 1, "does not exist"),
        ("non-URN $id goes RED", "teachback", mutate_id, 1, "$id"),
        ("required field set drift goes RED", "teachback", mutate_required, 1, "required field set drift"),
        ("required reasoning_reconstruction goes RED", "teachback", mutate_rr_required, 1, "must not be required"),
        ("subset enum under an 'if' selector stays GREEN", "teachback", mutate_if_subset, 0, None),
        ("handoff required-set drift goes RED", "handoff", mutate_handoff_drop_required, 1, "required field set drift"),
        ("required reasoning_chain goes RED", "handoff", mutate_handoff_require_rc, 1, "must not be required"),
        ("signal level enum drift goes RED", "signal", mutate_signal_drop_level, 1, "level enum drift"),
    ]

    print("=== Self-Test (known-bad fixtures must go RED) ===")
    fails = 0
    for name, target, mutate, expected_rc, expected_msg in cases:
        docs = {
            "teachback.schema.json": copy.deepcopy(good),
            "handoff.schema.json": copy.deepcopy(good_handoff),
            "signal.schema.json": copy.deepcopy(good_signal),
        }
        if mutate:
            mutate(docs[f"{target}.schema.json"])
        with tempfile.TemporaryDirectory() as tmpdir:
            for fname, doc in docs.items():
                with open(os.path.join(tmpdir, fname), "w") as f:
                    json.dump(doc, f, indent=2)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                checker, _ = run_checks(tmpdir, constants)
            rc = 1 if checker.errors else 0
            msg_ok = expected_msg is None or any(expected_msg in e for e in checker.errors)
            if rc == expected_rc and msg_ok:
                print(f"✓ {name}")
            else:
                print(f"✗ {name}: rc={rc} (expected {expected_rc}); errors={checker.errors}")
                fails += 1
    if fails:
        print(f"SELF-TEST FAILED ({fails} case(s))")
        sys.exit(1)
    print("SELF-TEST PASSED")
    sys.exit(0)


try:
    CONSTANTS = load_constants()
except (OSError, KeyError, SyntaxError, ValueError) as exc:
    print(f"ERROR: cannot extract source constants (run from the repository root): {exc}")
    sys.exit(2)

if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
    self_test(CONSTANTS)

print("=== Spec Schema Drift Verification ===")
print("")
result, scanned = run_checks(SCHEMAS_DIR, CONSTANTS)
print("")
print("--- Variety band cuts (manual prose cross-check; not schema shapes) ---")
print(f"COMPACT_MAX={CONSTANTS['COMPACT_MAX']}  ORCHESTRATE_MAX={CONSTANTS['ORCHESTRATE_MAX']}  PLAN_MODE_MAX={CONSTANTS['PLAN_MODE_MAX']}")
print("")
print("=== Summary ===")
if result.errors:
    for error in result.errors:
        print(f"✗ {error}")
    print(f"Failures: {len(result.errors)}")
    print("SCHEMA DRIFT VERIFICATION FAILED")
    sys.exit(1)
print(f"Schemas checked: {scanned}; failures: 0")
print("SCHEMA DRIFT VERIFICATION PASSED")
sys.exit(0)
PYEOF
