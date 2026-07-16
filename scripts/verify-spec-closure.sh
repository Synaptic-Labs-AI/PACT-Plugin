#!/bin/bash
# scripts/verify-spec-closure.sh
# Verifies dual-binding closure between the spec's requirement index and the
# two conformance documents:
#
#   spec/pact-protocol.md      — requirement index (the closure denominator)
#   spec/annex-claude-code.md  — as-built audit rows
#   spec/binding-langchain.md  — prospective binding rows
#
# Checks:
# - Requirement index: every row's key matches ^L[1-4]-[A-Z]+-[0-9]{2}$
#   exactly; status is one of {active, deprecated}; no duplicate keys.
#   Deprecated keys are EXCLUDED from the closure denominator.
# - Annex/binding keyed tables (header column 1 == "Key"): every row key
#   matches the regex exactly; any unparseable row is a FAILURE (no silent
#   row-skipping); no duplicate row keys.
# - Annex status enum is closed: satisfied | satisfied-with-deviation |
#   unsatisfied | not-applicable. Unknown value = failure.
#   unsatisfied rows must carry an issue link; not-applicable rows must cite
#   a predicate; satisfied-with-deviation rows must describe the deviation.
#   Annotation column (when present) admits only "structural" or empty.
# - Binding confidence enum is closed: clean | plausible-with-pattern | gap.
#   Every row's Evidence cell must be non-empty (citation / named pattern /
#   what-was-searched respectively).
# - BIDIRECTIONAL closure per document: every active key has exactly one row;
#   every row references an existing active key.
# - The summary includes an INFORMATIONAL count of satisfied(structural)
#   annotations and not-applicable predicate citations in the annex (audit-pass
#   convenience; never a gate).
#
# Group codes are data (the regex admits any uppercase group), so registry
# appends need no script change. Missing documents are skipped with a clear
# message; however, if a conformance document exists while the requirement
# index cannot be found, that inconsistency is a failure, not a skip.
#
# Run from the repository root. Exit codes: 0 = pass/skip, 1 = failure.
# --self-test seeds known-bad fixtures and asserts each goes RED.
#
# Internal env overrides (self-test plumbing): VERIFY_SPEC_MD,
# VERIFY_ANNEX_MD, VERIFY_BINDING_MD.

set -u

python3 - "${1:-}" <<'PYEOF'
import os
import re
import sys
import tempfile

KEY_RE = re.compile(r"^L[1-4]-[A-Z]+-[0-9]{2}$")
INDEX_STATUSES = {"active", "deprecated"}
ANNEX_STATUSES = {"satisfied", "satisfied-with-deviation", "unsatisfied", "not-applicable"}
CONFIDENCE_TIERS = {"clean", "plausible-with-pattern", "gap"}
ANNOTATIONS = {"", "structural"}

SPEC_MD = os.environ.get("VERIFY_SPEC_MD", "spec/pact-protocol.md")
ANNEX_MD = os.environ.get("VERIFY_ANNEX_MD", "spec/annex-claude-code.md")
BINDING_MD = os.environ.get("VERIFY_BINDING_MD", "spec/binding-langchain.md")


def split_row(line):
    """Split one markdown table line into stripped cells."""
    cells = line.strip().strip("|").split("|")
    return [cell.strip() for cell in cells]


def parse_tables(path):
    """Return [(header_cells, [(line_no, cells), ...]), ...] for one file."""
    tables = []
    header = None
    rows = []
    in_fence = False
    with open(path) as f:
        for line_no, raw in enumerate(f, 1):
            line = raw.rstrip("\n")
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            if line.lstrip().startswith("|"):
                cells = split_row(line)
                if header is None:
                    header = cells
                    rows = []
                elif all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                    continue  # separator row
                else:
                    rows.append((line_no, cells))
            else:
                if header is not None:
                    tables.append((header, rows))
                    header, rows = None, []
    if header is not None:
        tables.append((header, rows))
    return tables


def find_column(header, name):
    lowered = [cell.lower() for cell in header]
    return lowered.index(name) if name in lowered else None


class Checker:
    def __init__(self):
        self.errors = []
        self.notes = []

    def fail(self, msg):
        self.errors.append(msg)

    def parse_index(self, path):
        """Parse the requirement index; return {key: status} or None if the
        index heading/table is absent."""
        heading_re = re.compile(r"^#+\s+.*requirement index", re.IGNORECASE)
        with open(path) as f:
            lines = f.read().splitlines()
        start = None
        for i, line in enumerate(lines):
            if heading_re.match(line):
                start = i
                break
        if start is None:
            return None
        # First table after the heading.
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as tf:
            tf.write("\n".join(lines[start:]))
            tail_path = tf.name
        try:
            tables = parse_tables(tail_path)
        finally:
            os.unlink(tail_path)
        if not tables:
            return None
        header, rows = tables[0]
        status_col = find_column(header, "status")
        if status_col is None:
            self.fail(f"{path}: requirement index table has no Status column (header: {header})")
            return {}
        index = {}
        for line_no, cells in rows:
            if len(cells) != len(header):
                self.fail(f"{path}: UNPARSEABLE index row (cell count {len(cells)} != header {len(header)}): {cells}")
                continue
            key, status = cells[0], cells[status_col].lower()
            if not KEY_RE.fullmatch(key):
                self.fail(f"{path}: UNPARSEABLE index row — key '{key}' does not match the key grammar")
                continue
            if status not in INDEX_STATUSES:
                self.fail(f"{path}: key {key} has unknown index status '{cells[status_col]}' (allowed: {sorted(INDEX_STATUSES)})")
                continue
            if key in index:
                self.fail(f"{path}: duplicate key {key} in requirement index")
                continue
            index[key] = status
        return index

    def check_keyed_doc(self, path, doc_name, active_keys, deprecated_keys, kind):
        """Validate one conformance document (annex or binding) and its closure."""
        tables = parse_tables(path)
        keyed_tables = [t for t in tables if t[0] and t[0][0].lower() == "key"]
        if not keyed_tables:
            self.fail(f"{path}: no keyed tables found (expected tables whose first header cell is 'Key')")
            return
        seen = {}
        structural_count = 0
        na_predicate_count = 0
        for header, rows in keyed_tables:
            status_col = find_column(header, "status")
            annotation_col = find_column(header, "annotation")
            confidence_col = find_column(header, "confidence")
            evidence_col = find_column(header, "evidence")
            if kind == "annex" and status_col is None:
                self.fail(f"{path}: keyed table missing Status column (header: {header})")
                continue
            if kind == "binding" and (confidence_col is None or evidence_col is None):
                self.fail(f"{path}: keyed table missing Confidence/Evidence column (header: {header})")
                continue
            for line_no, cells in rows:
                if len(cells) != len(header):
                    self.fail(f"{path}:{line_no}: UNPARSEABLE row (cell count {len(cells)} != header {len(header)}): {cells}")
                    continue
                key = cells[0]
                if not KEY_RE.fullmatch(key):
                    self.fail(f"{path}:{line_no}: UNPARSEABLE row — key column '{key}' does not match the key grammar")
                    continue
                if key in seen:
                    self.fail(f"{path}:{line_no}: duplicate row for key {key} (first at line {seen[key]})")
                    continue
                seen[key] = line_no
                if key in deprecated_keys:
                    self.fail(f"{path}:{line_no}: row for DEPRECATED key {key} — one row per ACTIVE key only")
                elif key not in active_keys:
                    self.fail(f"{path}:{line_no}: row references key {key} absent from the requirement index")
                last_cell = cells[-1]
                if kind == "annex":
                    status = cells[status_col]
                    if status not in ANNEX_STATUSES:
                        self.fail(f"{path}:{line_no}: {key} has unknown status '{status}' (allowed: {sorted(ANNEX_STATUSES)})")
                    elif status == "unsatisfied" and "http" not in last_cell:
                        self.fail(f"{path}:{line_no}: {key} is unsatisfied but carries no issue link")
                    elif status == "not-applicable" and not last_cell:
                        self.fail(f"{path}:{line_no}: {key} is not-applicable but cites no predicate")
                    elif status == "satisfied-with-deviation" and not last_cell:
                        self.fail(f"{path}:{line_no}: {key} is satisfied-with-deviation but names no deviation")
                    if status == "not-applicable" and last_cell:
                        na_predicate_count += 1
                    if annotation_col is not None and cells[annotation_col] not in ANNOTATIONS:
                        self.fail(f"{path}:{line_no}: {key} has unknown annotation '{cells[annotation_col]}' (allowed: structural or empty)")
                    elif annotation_col is not None and cells[annotation_col] == "structural":
                        structural_count += 1
                else:
                    confidence = cells[confidence_col]
                    if confidence not in CONFIDENCE_TIERS:
                        self.fail(f"{path}:{line_no}: {key} has unknown confidence '{confidence}' (allowed: {sorted(CONFIDENCE_TIERS)})")
                    if not cells[evidence_col]:
                        self.fail(f"{path}:{line_no}: {key} has an empty Evidence cell")
        missing = sorted(active_keys - set(seen))
        for key in missing:
            self.fail(f"{path}: CLOSURE GAP — active key {key} has no row in {doc_name}")
        if len(seen) != len(active_keys) and not missing:
            self.fail(f"{path}: row count {len(seen)} != active key count {len(active_keys)}")
        self.notes.append(f"{doc_name}: {len(seen)} rows vs {len(active_keys)} active keys")
        if kind == "annex":
            self.notes.append(
                f"{doc_name} (informational): {structural_count} satisfied(structural) annotation(s), "
                f"{na_predicate_count} not-applicable predicate citation(s)"
            )


def run_checks(spec_md, annex_md, binding_md):
    checker = Checker()
    annex_exists = os.path.isfile(annex_md)
    binding_exists = os.path.isfile(binding_md)

    if not os.path.isfile(spec_md):
        print(f"SKIP: {spec_md} not found (not yet authored) — closure not checkable")
        if annex_exists or binding_exists:
            checker.fail("conformance document(s) exist but the spec document is missing — inconsistent state")
        return checker

    index = checker.parse_index(spec_md)
    if index is None:
        print(f"NOTE: requirement index not found in {spec_md}")
        if annex_exists or binding_exists:
            checker.fail(f"{annex_md if annex_exists else binding_md} exists but {spec_md} has no requirement index — inconsistent state")
        else:
            print("SKIP: no conformance documents yet — nothing to close against")
        return checker

    active = {k for k, s in index.items() if s == "active"}
    deprecated = {k for k, s in index.items() if s == "deprecated"}
    print(f"Requirement index: {len(active)} active, {len(deprecated)} deprecated")

    if annex_exists:
        checker.check_keyed_doc(annex_md, "annex", active, deprecated, "annex")
    else:
        print(f"SKIP: {annex_md} not found (not yet authored)")
    if binding_exists:
        checker.check_keyed_doc(binding_md, "binding", active, deprecated, "binding")
    else:
        print(f"SKIP: {binding_md} not found (not yet authored)")
    return checker


GOOD_SPEC = """# Protocol

## Appendix B — Requirement Index

| Key | Level | Group | Status | Title |
|---|---|---|---|---|
| L1-TS-01 | L1 | TS | active | Durable work records |
| L1-TS-02 | L1 | TS | deprecated | Withdrawn; successor L1-TS-03 |
| L2-VS-01 | L2 | VS | active | Variety scoring bands |
"""

GOOD_ANNEX = """# Annex

| Key | Requirement (summary) | Realizing mechanism | Status | Annotation | Deviation / predicate / issue |
|---|---|---|---|---|---|
| L1-TS-01 | Durable records | durable store files | satisfied | structural | |
| L2-VS-01 | Variety bands | scoring module | not-applicable | | pull-only-waiters |
"""

GOOD_BINDING = """# Binding

| Key | Proposed binding | Confidence | Evidence | Notes |
|---|---|---|---|---|
| L1-TS-01 | state channels | clean | vendor docs section 3 | |
| L2-VS-01 | scoring node | plausible-with-pattern | documented scoring pattern | |
"""


def self_test():
    # (name, spec, annex, binding, expected_rc, expected_error_substring,
    #  expected_note_substring) — the note check pins the informational
    #  annotation/predicate counts on the clean fixture (1 structural row,
    #  1 not-applicable row with a predicate).
    cases = [
        ("clean fixture stays GREEN", GOOD_SPEC, GOOD_ANNEX, GOOD_BINDING, 0, None,
         "1 satisfied(structural) annotation(s), 1 not-applicable predicate citation(s)"),
        ("annex closure gap goes RED", GOOD_SPEC,
         GOOD_ANNEX.replace("| L2-VS-01 | Variety bands | scoring module | not-applicable | | pull-only-waiters |\n", ""),
         GOOD_BINDING, 1, "CLOSURE GAP"),
        ("unknown annex status goes RED", GOOD_SPEC,
         GOOD_ANNEX.replace("satisfied |", "done |", 1), GOOD_BINDING, 1, "unknown status"),
        ("row for deprecated key goes RED", GOOD_SPEC,
         GOOD_ANNEX + "| L1-TS-02 | Old | legacy | satisfied | | |\n", GOOD_BINDING, 1, "DEPRECATED key"),
        ("unsatisfied without issue link goes RED", GOOD_SPEC,
         GOOD_ANNEX.replace("| satisfied | structural | |", "| unsatisfied | | needs work |"),
         GOOD_BINDING, 1, "no issue link"),
        ("unparseable row goes RED", GOOD_SPEC,
         GOOD_ANNEX + "| not-a-key | broken | x | satisfied | | |\n", GOOD_BINDING, 1, "UNPARSEABLE"),
        ("superseded 'plausible' shorthand goes RED", GOOD_SPEC, GOOD_ANNEX,
         GOOD_BINDING.replace("plausible-with-pattern", "plausible"), 1, "unknown confidence"),
        ("empty binding Evidence cell goes RED", GOOD_SPEC, GOOD_ANNEX,
         GOOD_BINDING.replace("| clean | vendor docs section 3 |", "| clean | |"), 1, "empty Evidence"),
        ("row referencing unknown key goes RED", GOOD_SPEC,
         GOOD_ANNEX + "| L3-PC-01 | Ghost | x | satisfied | | |\n", GOOD_BINDING, 1, "absent from the requirement index"),
    ]

    print("=== Self-Test (known-bad fixtures must go RED) ===")
    fails = 0
    for case in cases:
        name, spec, annex, binding, expected_rc, expected_msg = case[:6]
        expected_note = case[6] if len(case) > 6 else None
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = {}
            for fname, content in (("spec.md", spec), ("annex.md", annex), ("binding.md", binding)):
                paths[fname] = os.path.join(tmpdir, fname)
                with open(paths[fname], "w") as f:
                    f.write(content)
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                checker = run_checks(paths["spec.md"], paths["annex.md"], paths["binding.md"])
            rc = 1 if checker.errors else 0
            msg_ok = expected_msg is None or any(expected_msg in e for e in checker.errors)
            note_ok = expected_note is None or any(expected_note in n for n in checker.notes)
            if rc == expected_rc and msg_ok and note_ok:
                print(f"✓ {name}")
            else:
                print(f"✗ {name}: rc={rc} (expected {expected_rc}); errors={checker.errors}; notes={checker.notes}")
                fails += 1
    if fails:
        print(f"SELF-TEST FAILED ({fails} case(s))")
        sys.exit(1)
    print("SELF-TEST PASSED")
    sys.exit(0)


if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
    self_test()

print("=== Spec Closure Verification ===")
print("")
result = run_checks(SPEC_MD, ANNEX_MD, BINDING_MD)
print("")
print("=== Summary ===")
for note in result.notes:
    print(note)
if result.errors:
    for error in result.errors:
        print(f"✗ {error}")
    print(f"Failures: {len(result.errors)}")
    print("CLOSURE VERIFICATION FAILED")
    sys.exit(1)
print("Failures: 0")
print("CLOSURE VERIFICATION PASSED")
sys.exit(0)
PYEOF
