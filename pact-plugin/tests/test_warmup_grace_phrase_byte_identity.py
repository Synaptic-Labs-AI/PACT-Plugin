"""Byte-identity structural pin: the load-bearing prefix
"CronList-presence IS the armed-state bit" must appear byte-identically
in BOTH start-pending-scan.md §Warmup-State File AND
scan-pending-tasks.md §Warmup-Grace-Skip Procedure (+ 6th-guardrail audit).

The prefix is load-bearing for the two-source-of-truth-failure-mode
prevention: it pins the contract that `pending-scan-armed-at.json` is
NOT armed-state replication; the cron's CronList-presence remains the
sole armed-state primitive.

The architect spec §10.1 / §10.2 / §10.3 pin the phrase in each file
INDEPENDENTLY. This cross-file pin asserts byte-identical PREFIX
symmetry across both files — a different property: it catches the
drift-between-sites failure mode where one site's phrase silently
softens (e.g., "CronList-presence IS the armed-state primitive") while
the per-site presence assertions still pass.

Per CLAUDE.md coupling-via-substring-count pin: uses Python str.count()
substring semantics, NOT grep -c (which would line-count and mismatch
when the phrase appears multiple times on one line or across split
lines).
"""
from pathlib import Path


COMMANDS_DIR = Path(__file__).parent.parent / "commands"
START = COMMANDS_DIR / "start-pending-scan.md"
SCAN = COMMANDS_DIR / "scan-pending-tasks.md"

LOAD_BEARING_PREFIX = "CronList-presence IS the armed-state bit"


def test_phrase_present_in_start_pending_scan():
    """The load-bearing prefix appears at least once in
    start-pending-scan.md (§Warmup-State File audit anchor)."""
    text = START.read_text()
    count = text.count(LOAD_BEARING_PREFIX)
    assert count >= 1, (
        f"start-pending-scan.md missing load-bearing prefix "
        f"'{LOAD_BEARING_PREFIX}'. Found {count} occurrences. "
        f"This prefix is byte-identity-pinned across start-pending-scan.md "
        f"§Warmup-State File and scan-pending-tasks.md §Warmup-Grace-Skip; "
        f"drift between sites silently softens the two-source-of-truth "
        f"contract."
    )


def test_phrase_present_in_scan_pending_tasks():
    """The load-bearing prefix appears at least once in
    scan-pending-tasks.md (§Warmup-Grace-Skip Procedure + 6th-guardrail
    audit block both reference it)."""
    text = SCAN.read_text()
    count = text.count(LOAD_BEARING_PREFIX)
    assert count >= 1, (
        f"scan-pending-tasks.md missing load-bearing prefix "
        f"'{LOAD_BEARING_PREFIX}'. Found {count} occurrences."
    )


def test_phrase_exact_multiplicity_across_files():
    """Exact-multiplicity pin: the load-bearing prefix appears exactly
    once in start-pending-scan.md (§Warmup-State File audit anchor)
    and exactly twice in scan-pending-tasks.md (§Warmup-Grace-Skip
    Procedure audit + 6th-guardrail audit block).

    A `>= 1` assertion on both files is phantom-green on asymmetric
    single-site drift: when one site has multiple occurrences (scan
    has 2), mutating ONE of them to a paraphrase still leaves
    `count >= 1` True. Pinning the exact multiplicities catches
    single-occurrence drift in either file immediately — a
    mismatched count surfaces as test RED with the expected-vs-
    actual diff in the error message.

    If the canonical multiplicities change (e.g., the architect
    decides scan should have 3 occurrences), update the expected
    counts in lockstep with the source edit per the
    coupling-via-substring-count discipline."""
    start_count = START.read_text().count(LOAD_BEARING_PREFIX)
    scan_count = SCAN.read_text().count(LOAD_BEARING_PREFIX)

    EXPECTED_START = 1
    EXPECTED_SCAN = 2

    assert start_count == EXPECTED_START and scan_count == EXPECTED_SCAN, (
        f"Byte-identity multiplicity violated: prefix "
        f"'{LOAD_BEARING_PREFIX}' occurs {start_count}x in "
        f"start-pending-scan.md (expected {EXPECTED_START}) and "
        f"{scan_count}x in scan-pending-tasks.md (expected "
        f"{EXPECTED_SCAN}). A count mismatch indicates either a "
        f"drift in one site without the paired edit, or a "
        f"deliberate multiplicity change that requires updating "
        f"the expected counts in lockstep."
    )
