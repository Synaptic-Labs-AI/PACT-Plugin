#!/bin/bash
# scripts/verify-spec-nouns.sh
# Scans the spec's committed prose surface (spec/pact-protocol.md +
# spec/README.md + spec/schemas/*.json) for substrate mechanism nouns that
# must not appear there. Patterns live in scripts/spec-noun-denylist.txt
# (case-sensitive fixed strings, one per line).
#
# Rules:
# - spec/pact-protocol.md is scanned in FULL — informative sections included.
# - spec/README.md is scanned the same way (it carries the framework-agnostic
#   claim, so substrate nouns must not accumulate there either).
# - Schema JSON files are scanned against a projection with every "$comment"
#   string value blanked first: $comment values are annotation, and the schema
#   extraction-pin convention requires them to cite source-code filenames that
#   are themselves denylisted. Only $comment values are exempt; a denylisted
#   noun anywhere else in a schema (title, description, property text) fails.
# - Gray-zone terms are NOT machine-deniable; they are printed as a reminder
#   footer for the manual attestation pass and never fail this script.
# - Targets that do not exist yet are skipped with a clear message.
#
# Run from the repository root. Exit codes: 0 = pass (or nothing to scan yet),
# 1 = denylist hit(s) found, 2 = usage/environment error.
#
# --self-test: seeds known-good and known-bad fixtures in a temp dir and
# asserts the check goes RED on the bad ones (counter-test: a green never
# observed failing is indistinguishable from broken).
#
# Internal env overrides (used by --self-test; not part of the manual-run
# interface): VERIFY_SPEC_MD, VERIFY_SPEC_README, VERIFY_SCHEMAS_DIR.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DENYLIST="$SCRIPT_DIR/spec-noun-denylist.txt"
SPEC_MD="${VERIFY_SPEC_MD:-spec/pact-protocol.md}"
SPEC_README="${VERIFY_SPEC_README:-spec/README.md}"
SCHEMAS_DIR="${VERIFY_SCHEMAS_DIR:-spec/schemas}"

HITS=0
SCANNED=0

# Blank every "$comment" string value in a JSON document (recursively) and
# print the projection. Parse failure is fatal: schemas must be valid JSON.
project_without_comments() {
    python3 - "$1" <<'PYEOF'
import json
import sys

path = sys.argv[1]
try:
    with open(path) as f:
        doc = json.load(f)
except (OSError, ValueError) as exc:
    print(f"UNPARSEABLE JSON: {path}: {exc}", file=sys.stderr)
    sys.exit(3)


def blank_comments(node):
    if isinstance(node, dict):
        return {
            key: ("" if key == "$comment" else blank_comments(value))
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [blank_comments(item) for item in node]
    return node


print(json.dumps(blank_comments(doc), indent=2))
PYEOF
}

# Scan one text file against every denylist pattern.
# Args: label_for_output path_to_text
scan_text() {
    local label="$1"
    local text_path="$2"
    local pattern
    local matches

    while IFS= read -r pattern; do
        case "$pattern" in
            ''|'#'*) continue ;;
        esac
        matches=$(grep -nF -- "$pattern" "$text_path" || true)
        if [ -n "$matches" ]; then
            echo "✗ $label: denylisted noun '$pattern'"
            echo "$matches" | head -5 | sed 's/^/    /'
            HITS=$((HITS + 1))
        fi
    done < "$DENYLIST"
}

print_grayzone_footer() {
    echo ""
    echo "--- Gray-zone terms (manual attestation pass; NOT failures here) ---"
    cat <<'FOOTER'
idle / wake / inbox        - not in normative sentences; informative hazard text OK
hook                       - generic English OK; deny when naming a platform mechanism
git / commit / branch / merge / PR / worktree
                           - normative core says "version control" / "change-integration gate"
middleware / node / graph / reducer / thread
                           - generic CS uses OK; check no substrate mechanism is smuggled
orchestrator               - spec actor vocabulary is "Lead"; deniable once terminology lands
teammate / team-lead       - platform-flavored spellings of Specialist/Lead; gray until terminology lands
secretary / task / journal / metadata / PACT-family production names
                           - allowed by design; confirm deliberate use
FOOTER
    echo ""
}

main_scan() {
    echo "=== Spec Noun Audit ==="
    echo ""

    local md_target
    for md_target in "$SPEC_MD" "$SPEC_README"; do
        if [ -f "$md_target" ]; then
            scan_text "$md_target" "$md_target"
            SCANNED=$((SCANNED + 1))
        else
            echo "SKIP: $md_target not found (not yet authored)"
        fi
    done

    if [ -d "$SCHEMAS_DIR" ]; then
        local found_schema=0
        local schema projection
        for schema in "$SCHEMAS_DIR"/*.json; do
            [ -e "$schema" ] || continue
            found_schema=1
            SCANNED=$((SCANNED + 1))
            projection=$(mktemp)
            if project_without_comments "$schema" > "$projection"; then
                scan_text "$schema (\$comment values exempt; line numbers refer to a normalized projection)" "$projection"
            else
                echo "✗ $schema: invalid JSON — cannot scan"
                HITS=$((HITS + 1))
            fi
            rm -f "$projection"
        done
        if [ "$found_schema" -eq 0 ]; then
            echo "SKIP: no *.json files in $SCHEMAS_DIR yet"
        fi
    else
        echo "SKIP: $SCHEMAS_DIR/ not found (not yet authored)"
    fi

    print_grayzone_footer

    echo "=== Summary ==="
    if [ "$SCANNED" -eq 0 ]; then
        echo "Nothing to scan yet (no targets exist)."
        exit 0
    fi
    if [ "$HITS" -gt 0 ]; then
        echo "Denylist hits: $HITS"
        echo "NOUN AUDIT FAILED"
        exit 1
    fi
    echo "Denylist hits: 0"
    echo "NOUN AUDIT PASSED"
    exit 0
}

self_test() {
    local tmpdir fails
    tmpdir=$(mktemp -d)
    fails=0

    echo "=== Self-Test (known-bad fixtures must go RED) ==="

    printf 'The Lead calls SendMessage to notify the Specialist.\n' > "$tmpdir/spec-bad.md"
    printf 'The Lead emits a wake signal after the durable write.\n' > "$tmpdir/spec-clean.md"
    printf 'This specification is framework-agnostic and built on LangGraph.\n' > "$tmpdir/readme-bad.md"
    printf 'This specification is framework-agnostic.\n' > "$tmpdir/readme-clean.md"
    mkdir -p "$tmpdir/schemas-comment-only" "$tmpdir/schemas-bad" "$tmpdir/schemas-empty"
    printf '{"$comment": "extracted from teachback_schema.py", "title": "payload"}\n' \
        > "$tmpdir/schemas-comment-only/a.schema.json"
    printf '{"$comment": "clean anchor", "description": "written via TaskUpdate"}\n' \
        > "$tmpdir/schemas-bad/b.schema.json"

    run_case() {
        local name="$1" spec="$2" readme="$3" schemas="$4" expected="$5" rc
        VERIFY_SPEC_MD="$spec" VERIFY_SPEC_README="$readme" VERIFY_SCHEMAS_DIR="$schemas" \
            "$0" > /dev/null 2>&1
        rc=$?
        if [ "$rc" -eq "$expected" ]; then
            echo "✓ $name (exit $rc)"
        else
            echo "✗ $name: expected exit $expected, got $rc"
            fails=$((fails + 1))
        fi
    }

    run_case "denylisted noun in spec markdown goes RED" \
        "$tmpdir/spec-bad.md" "$tmpdir/readme-clean.md" "$tmpdir/schemas-empty" 1
    run_case "denylisted noun in README goes RED" \
        "$tmpdir/spec-clean.md" "$tmpdir/readme-bad.md" "$tmpdir/schemas-empty" 1
    run_case "clean spec + noun only inside \$comment stays GREEN" \
        "$tmpdir/spec-clean.md" "$tmpdir/readme-clean.md" "$tmpdir/schemas-comment-only" 0
    run_case "noun OUTSIDE \$comment in a schema goes RED" \
        "$tmpdir/spec-clean.md" "$tmpdir/readme-clean.md" "$tmpdir/schemas-bad" 1
    run_case "clean control stays GREEN" \
        "$tmpdir/spec-clean.md" "$tmpdir/readme-clean.md" "$tmpdir/schemas-empty" 0

    rm -rf "$tmpdir"
    if [ "$fails" -gt 0 ]; then
        echo "SELF-TEST FAILED ($fails case(s))"
        exit 1
    fi
    echo "SELF-TEST PASSED"
    exit 0
}

if [ ! -f "$DENYLIST" ]; then
    echo "ERROR: denylist not found: $DENYLIST"
    exit 2
fi

if [ "${1:-}" = "--self-test" ]; then
    self_test
else
    main_scan
fi
