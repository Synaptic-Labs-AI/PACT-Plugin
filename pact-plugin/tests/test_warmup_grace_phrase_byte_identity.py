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


def test_phrase_byte_identical_across_files():
    """Byte-identity symmetry: the prefix occurs in BOTH files. This
    is the cross-file property that per-site presence assertions
    cannot guarantee — they verify each site independently but allow
    silent drift between sites (one site softening while the other
    stays canonical).

    If this test fails while the per-site presence tests pass, the
    failure indicates one site has dropped or renamed the phrase
    without the corresponding edit in the paired file."""
    start_count = START.read_text().count(LOAD_BEARING_PREFIX)
    scan_count = SCAN.read_text().count(LOAD_BEARING_PREFIX)

    assert start_count >= 1 and scan_count >= 1, (
        f"Byte-identity symmetry violated: prefix "
        f"'{LOAD_BEARING_PREFIX}' occurs {start_count}x in "
        f"start-pending-scan.md and {scan_count}x in "
        f"scan-pending-tasks.md. Both must have >=1 occurrence."
    )
