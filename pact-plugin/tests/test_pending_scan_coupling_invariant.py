"""
Coupling-invariant cross-file test for the warmup-grace ↔ cron-interval
pair pinned by commands/scan-pending-tasks.md Step 0 and
commands/start-pending-scan.md §CronCreate Block.

The architect's spec preamble states:

    Coupling invariant — WARMUP_GRACE_SECONDS = cron interval.
    The first cron fire post-arm always falls in [0, cron_interval);
    setting WARMUP_GRACE_SECONDS = cron_interval guarantees 100%
    first-fire coverage of the false-fire window.

The existing structural tests pin each side INDEPENDENTLY:
- test_warmup_grace_step_0_present_in_operation pins the literal `300`
  in scan-pending-tasks.md Step 0 (via substring containment).
- test_cron_create_call_shape_cron_create_block_has_four_fields pins the
  literal `cron="*/5 * * * *"` in start-pending-scan.md §CronCreate Block.

What neither pins: the MATHEMATICAL coupling — that the grace-seconds equal
the cron-minutes * 60. A future PR that lockstep-bumps both numbers to a
NON-equal pair (e.g., `*/7` cron + 300s grace, re-opening the false-fire
window for the [300, 420) range) would pass both existing tests.

This test parses both numerics structurally and asserts equality, surviving
any lockstep cadence retuning while catching asymmetric drift in EITHER
direction.

Counter-test-by-revert validation (documented in test docstrings):
- Mutating ONLY scan-pending-tasks.md Step 0 bash `-lt N` literal: test FAILS.
- Mutating ONLY start-pending-scan.md cron literal: test FAILS.
- Mutating BOTH in lockstep to a new equal pair: test PASSES (invariant
  preserved); pinned per-side tests would also need updating.
- Mutating BOTH but to a NON-equal pair (asymmetric drift): test FAILS.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCAN_MD = ROOT / "commands" / "scan-pending-tasks.md"
START_MD = ROOT / "commands" / "start-pending-scan.md"


def _extract_grace_seconds_from_step_0(scan_md_text: str) -> int:
    """Parse the warmup-grace integer from scan-pending-tasks.md Step 0
    bash block. The bash gate's load-bearing literal is the `-lt N`
    comparison: this is the runtime decision boundary, not a prose
    mention. Prose mentions of `300s` are documentation, not enforcement.
    """
    op_start = scan_md_text.find("\n## Operation")
    assert op_start >= 0, "scan-pending-tasks.md missing §Operation"
    step_0_pos = scan_md_text.find("\n0. ", op_start)
    assert step_0_pos >= 0, "scan-pending-tasks.md missing Step 0"
    step_1_pos = scan_md_text.find("\n1. ", step_0_pos)
    step_0_body = scan_md_text[step_0_pos:step_1_pos] if step_1_pos > 0 else scan_md_text[step_0_pos:]
    bash_match = re.search(r"```bash\n(.*?)```", step_0_body, re.DOTALL)
    assert bash_match is not None, "scan-pending-tasks.md Step 0 missing fenced ```bash``` block"
    bash_body = bash_match.group(1)
    # Match the canonical `-lt <int>` shape used by the gate.
    lt_match = re.search(r"-lt\s+(\d+)\b", bash_body)
    assert lt_match is not None, (
        f"scan-pending-tasks.md Step 0 bash must contain `-lt <integer>` "
        f"comparison; got body:\n{bash_body!r}"
    )
    return int(lt_match.group(1))


def _extract_cron_minutes_from_croncreate_block(start_md_text: str) -> int:
    """Parse the cron-interval minute-count from start-pending-scan.md
    §CronCreate Block. The §CronCreate Block section is the source-of-
    truth for the cron arming literal; Step 4 of §Operation also contains
    the literal but the §CronCreate Block is the authoritative call shape.
    """
    block_start = start_md_text.find("\n## CronCreate Block")
    assert block_start >= 0, "start-pending-scan.md missing §CronCreate Block"
    # Bound to next section.
    next_section = start_md_text.find("\n## ", block_start + 1)
    block_section = start_md_text[block_start:next_section] if next_section > 0 else start_md_text[block_start:]
    cron_match = re.search(r'cron="\*/(\d+)\s+\*\s+\*\s+\*\s+\*"', block_section)
    assert cron_match is not None, (
        f"start-pending-scan.md §CronCreate Block must contain a "
        f'`cron="*/N * * * *"` literal; got section:\n{block_section!r}'
    )
    return int(cron_match.group(1))


def test_warmup_grace_equals_cron_interval():
    """The warmup-grace seconds in scan-pending-tasks.md Step 0 MUST
    equal the cron interval (in seconds) in start-pending-scan.md
    §CronCreate Block. Architect's spec preamble: 'WARMUP_GRACE_SECONDS
    = cron interval. The first cron fire post-arm always falls in
    [0, cron_interval); setting WARMUP_GRACE_SECONDS = cron_interval
    guarantees 100% first-fire coverage of the false-fire window.'

    This test catches asymmetric drift that the per-side structural
    pins miss. Per-side pins fix the LITERAL value at each surface;
    this pin fixes the RELATIONSHIP between them. Lockstep cadence
    retuning (both surfaces moving to a new equal pair) preserves
    this invariant while breaking the per-side pins — by design, so
    that the human/agent doing the retune is forced to acknowledge
    the coupling.

    Counter-test discipline (documented at file head): mutating
    EITHER surface independently fails this test; mutating both to
    a non-equal pair (e.g., `*/7` cron + 300s grace) also fails this
    test; mutating both to an equal pair (e.g., `*/7` cron + 420s
    grace) preserves this test BUT breaks the per-side pins,
    forcing atomic-commit awareness.
    """
    scan_text = SCAN_MD.read_text(encoding="utf-8")
    start_text = START_MD.read_text(encoding="utf-8")

    grace_seconds = _extract_grace_seconds_from_step_0(scan_text)
    cron_minutes = _extract_cron_minutes_from_croncreate_block(start_text)
    cron_seconds = cron_minutes * 60

    assert grace_seconds == cron_seconds, (
        f"Coupling-invariant violation: warmup-grace ({grace_seconds}s) "
        f"!= cron interval ({cron_minutes}min = {cron_seconds}s). "
        f"The first cron fire post-arm always falls in [0, cron_interval). "
        f"Setting grace < interval re-opens the false-fire window in the "
        f"interval [grace, interval). Setting grace > interval delays the "
        f"first legitimate scan beyond the cron's first-fire boundary "
        f"(every fire in [0, grace - interval) is unnecessarily skipped). "
        f"Tune both surfaces in lockstep: scan-pending-tasks.md Step 0 "
        f"bash `-lt N` AND start-pending-scan.md §CronCreate Block "
        f'`cron="*/M * * * *"` with N == M * 60.'
    )


def test_warmup_grace_is_positive_integer():
    """The warmup-grace seconds must be a positive integer. Zero or
    negative would short-circuit the warmup-grace skip entirely
    (every fire would fall through), defeating the false-fire window
    bound. The literal source MUST be a positive integer; the regex
    in _extract_grace_seconds_from_step_0 enforces digit-shape, this
    test pins the positivity.
    """
    scan_text = SCAN_MD.read_text(encoding="utf-8")
    grace_seconds = _extract_grace_seconds_from_step_0(scan_text)
    assert grace_seconds > 0, (
        f"warmup-grace must be > 0 seconds; got {grace_seconds}. "
        f"A zero or negative grace defeats the false-fire window bound."
    )


def test_cron_interval_is_positive_integer():
    """The cron interval must be a positive integer. `*/0` is invalid
    cron syntax; `*/1` is the minimum useful value (every minute).
    """
    start_text = START_MD.read_text(encoding="utf-8")
    cron_minutes = _extract_cron_minutes_from_croncreate_block(start_text)
    assert cron_minutes > 0, (
        f"cron interval must be > 0 minutes; got {cron_minutes}. "
        f"`*/0` is invalid cron syntax."
    )
