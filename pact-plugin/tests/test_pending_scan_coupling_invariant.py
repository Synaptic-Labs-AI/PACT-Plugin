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

ISO-8601 timestamp-format coupling (added 2026-05-21, PR #819 TEST gap-fill):

The Step 0.5 self-correcting teardown check (introduced by #819) reads
`teardown_request.ts` as an ISO-8601 UTC string and converts to integer
epoch via `strptime` for comparison against integer-epoch `scan_armed.armed_at`
and `scan_disarmed.disarmed_at`. The format literal `%Y-%m-%dT%H:%M:%SZ`
must match BYTE-IDENTICAL across four sites:

  - session_journal.py make_event (line 325, upstream SSOT — stamps the ts)
  - scan-pending-tasks.md Step 0.5 bash extractor (consumer — parses the ts)
  - test_scan_pending_tasks_self_teardown.py ISO_FORMAT_LITERAL (test fixture)
  - test_scan_pending_tasks_command_structure.py structural pin (assertion)

Per-site tests pin each literal independently; this cross-site equality
test catches the silent-drift failure mode where one site is updated and
the others are not. Empirically: a future Python 3.11+ baseline bump
might tempt a switch to `fromisoformat` (Z-suffix supported only in 3.11+);
without coordinated update of the format literal across all 4 sites,
strptime in the .md would silently fail to parse and Step 0.5 would
fall through to its fail-open behavior — defeating the compliance fix.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCAN_MD = ROOT / "commands" / "scan-pending-tasks.md"
START_MD = ROOT / "commands" / "start-pending-scan.md"
SESSION_JOURNAL_PY = ROOT / "hooks" / "shared" / "session_journal.py"
TEST_SELF_TEARDOWN_PY = ROOT / "tests" / "test_scan_pending_tasks_self_teardown.py"
TEST_COMMAND_STRUCTURE_PY = ROOT / "tests" / "test_scan_pending_tasks_command_structure.py"

ISO_FORMAT_LITERAL_CANONICAL = "%Y-%m-%dT%H:%M:%SZ"


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


def test_iso_format_literal_byte_identical_across_step_0_5_coupling_sites():
    """The ISO-8601 timestamp format literal `%Y-%m-%dT%H:%M:%SZ` must be
    byte-identical across four sites that participate in the Step 0.5
    self-correcting teardown ISO→epoch conversion chain:

      1. session_journal.py make_event (upstream SSOT — stamps `ts`)
      2. scan-pending-tasks.md Step 0.5 bash extractor (consumer — parses `ts`)
      3. test_scan_pending_tasks_self_teardown.py ISO_FORMAT_LITERAL
         (test fixture that round-trips through strftime/strptime)
      4. test_scan_pending_tasks_command_structure.py structural pin
         (asserts the literal is present in the .md)

    Per-site tests pin each literal INDEPENDENTLY. None of them catch
    the silent-drift failure mode: site 1 (make_event) is updated to a
    new format string while one of sites 2-4 is left at the old format.
    The bash strptime in site 2 would then fail to parse make_event's
    new shape, the variable would yield empty string, the `-n` guard
    would close, and Step 0.5 would silently fall through to fail-open —
    defeating the compliance fix that #819 exists to deliver.

    This test asserts the literal is present and equal at all four
    sites. A coordinated update to all four passes; a partial update
    fails loudly, surfacing the coupling for the editor.

    Counter-test-by-revert discipline:
    - Mutating ONLY make_event format literal: test FAILS.
    - Mutating ONLY the .md extractor format literal: test FAILS.
    - Mutating ONLY one test file's literal: test FAILS.
    - Coordinated mutation of all four: test PASSES; the dependent
      per-site pins (structural + runtime + make_event tests) would
      also need updating, forcing atomic-commit awareness of the
      coupling.

    Why this matters specifically for #819: Option D's compliance
    latency bound (≤5 minutes) depends on the strptime parse SUCCEEDING.
    A silent strptime failure due to format drift yields the same
    user-visible behavior as the pre-Option-D state (no self-teardown,
    orchestrator must manually invoke). The runtime round-trip test
    (test_step_0_5_iso_to_epoch_conversion_round_trip) catches the
    failure when round-tripped through the test's own fixture, but
    only the cross-site equality test catches drift in make_event
    itself (site 1) — the upstream SSOT.
    """
    # The canonical SSOT is `make_event` in session_journal.py: it stamps
    # the `ts` field on every journaled event. Anchor by `make_event`'s
    # `strftime(...)` call shape rather than by line number (line-anchored
    # pins drift across unrelated edits).
    sj_text = SESSION_JOURNAL_PY.read_text(encoding="utf-8")
    make_event_match = re.search(
        r"def make_event\b.*?strftime\(\"([^\"]+)\"\)",
        sj_text, re.DOTALL,
    )
    assert make_event_match is not None, (
        "session_journal.py must define `make_event` with a `strftime(\"...\")` "
        "call stamping the `ts` field. The make_event function is the upstream "
        "SSOT for the Step 0.5 ISO→epoch coupling chain."
    )
    make_event_literal = make_event_match.group(1)
    assert make_event_literal == ISO_FORMAT_LITERAL_CANONICAL, (
        f"session_journal.py `make_event` strftime literal "
        f"({make_event_literal!r}) MUST equal the canonical ISO format "
        f"literal ({ISO_FORMAT_LITERAL_CANONICAL!r}). make_event is the "
        f"upstream SSOT — any change here cascades through all Step 0.5 "
        f"coupling sites and silently breaks the bash strptime parse."
    )

    # The bash extractor in scan-pending-tasks.md must contain the literal
    # inside the Step 0.5 fenced bash block (not merely anywhere in the
    # file — a comment containing the literal would phantom-green a real
    # extractor drift).
    scan_text = SCAN_MD.read_text(encoding="utf-8")
    op_start = scan_text.find("\n## Operation")
    step_0_5_pos = scan_text.find("\n0.5. ", op_start)
    step_1_pos = scan_text.find("\n1. ", step_0_5_pos)
    step_0_5_body = scan_text[step_0_5_pos:step_1_pos] if step_1_pos > 0 else scan_text[step_0_5_pos:]
    bash_match = re.search(r"```bash\n(.*?)```", step_0_5_body, re.DOTALL)
    assert bash_match is not None, "Step 0.5 must contain a fenced ```bash``` block"
    bash_body = bash_match.group(1)
    assert ISO_FORMAT_LITERAL_CANONICAL in bash_body, (
        f"scan-pending-tasks.md Step 0.5 bash extractor MUST contain the "
        f"canonical ISO format literal {ISO_FORMAT_LITERAL_CANONICAL!r} "
        f"inside the fenced ```bash``` block (not merely in surrounding "
        f"prose). The bash strptime call is the consumer of make_event's "
        f"stamped `ts`; drift breaks the parse and silently fail-opens "
        f"Step 0.5. Bash body:\n{bash_body!r}"
    )

    # Test fixtures must also pin the literal (the canonical fixture
    # constant + the structural assertion). These keep the two ends of
    # the chain in sync with the SSOT.
    fixture_text = TEST_SELF_TEARDOWN_PY.read_text(encoding="utf-8")
    assert ISO_FORMAT_LITERAL_CANONICAL in fixture_text, (
        f"test_scan_pending_tasks_self_teardown.py MUST pin the canonical "
        f"ISO format literal {ISO_FORMAT_LITERAL_CANONICAL!r} as its "
        f"ISO_FORMAT_LITERAL constant. The runtime round-trip test "
        f"depends on this fixture matching make_event's stamped shape."
    )

    structural_text = TEST_COMMAND_STRUCTURE_PY.read_text(encoding="utf-8")
    assert ISO_FORMAT_LITERAL_CANONICAL in structural_text, (
        f"test_scan_pending_tasks_command_structure.py MUST contain the "
        f"canonical ISO format literal {ISO_FORMAT_LITERAL_CANONICAL!r} "
        f"in its structural pin asserting Step 0.5's .md contains the "
        f"literal. This is the .md ↔ test pin half of the coupling chain."
    )


def test_step_0_5_audit_prose_forbids_set_e_and_fromisoformat():
    """Step 0.5's audit prose contains a verbatim future-editor warning
    forbidding two specific 'harmonizations' that would silently break
    the self-correcting teardown:

      1. `set -e` or `ERR` trap inside the Step 0.5 bash block:
         empty-operand `-gt` would abort the cron-fire turn (because
         bash's `-gt` with an empty string operand exits non-zero in
         `set -e` mode), breaking fail-open semantics and reintroducing
         the compliance gap Option D exists to close.

      2. Switching the extractor from explicit-format `strptime` to
         `fromisoformat` without verifying Python version baseline:
         `fromisoformat` only handles the `Z` suffix in Python 3.11+.
         A 3.10-or-earlier baseline would raise ValueError on
         `make_event`'s `%Y-%m-%dT%H:%M:%SZ` shape, the extractor
         would crash, the variable would yield empty, and Step 0.5
         would silently fail-open.

    Both warnings are load-bearing PROSE that encodes a DESIGN decision.
    The coder's HANDOFF uncertainty[0] flagged this verbatim: 'Step 0.5's
    verbatim audit prose contains a future-editor warning that forbids
    set -e AND fromisoformat switch — these are architecturally
    load-bearing for fail-open semantics + Python 3.7+ portability,
    and I have preserved them verbatim, but future reviewers should
    verify the prose has not drifted in any subsequent edit pass.'

    This is a documentation-in-code test (per the documentation-in-code-
    tests pattern): it pins the future-editor warning so any subsequent
    'cleanup' that strips the warning fails loudly. Without this test,
    a well-meaning future editor could remove the warning during a
    'simplify Step 0.5 prose' pass, then later add `set -e` for
    'consistency with orchestrate.md' — silently re-opening the
    compliance gap.

    The test pins the structural shape of the warning (both forbiddens
    must be present in the Step 0.5 audit prose), not the exact wording
    — minor copy-edits are allowed; removal of either forbidden is not.

    Counter-test-by-revert: stripping either forbidden token from the
    audit prose causes this test to fail with a specific message
    identifying which forbidden is missing. Restoring the token via
    `git checkout` makes the test pass again. Empirically validated at
    fix/819-cron-self-teardown HEAD.
    """
    scan_text = SCAN_MD.read_text(encoding="utf-8")
    op_start = scan_text.find("\n## Operation")
    assert op_start >= 0, "scan-pending-tasks.md missing §Operation"
    step_0_5_pos = scan_text.find("\n0.5. ", op_start)
    assert step_0_5_pos >= 0, "scan-pending-tasks.md §Operation missing Step 0.5"
    # Bound Step 0.5 to the next numbered step.
    step_1_pos = scan_text.find("\n1. ", step_0_5_pos)
    step_0_5_body = scan_text[step_0_5_pos:step_1_pos] if step_1_pos > 0 else scan_text[step_0_5_pos:]

    # Forbidden #1: set -e / ERR trap warning must remain in the audit prose.
    has_set_e_warning = "set -e" in step_0_5_body and "MUST NOT" in step_0_5_body
    assert has_set_e_warning, (
        "Step 0.5 audit prose must contain a future-editor warning "
        "FORBIDDING `set -e` (or ERR trap) inside the Step 0.5 bash block. "
        "Without `set -e`, an empty-operand `-gt` comparison evaluates to "
        "false (fail-open). WITH `set -e`, an empty-operand `-gt` aborts "
        "the cron-fire turn — breaking fail-open and reintroducing the "
        "compliance gap Option D exists to close. The warning prose is "
        "load-bearing for future-editor hardening; removing it permits "
        "a 'consistency with orchestrate.md' edit to silently break the "
        "fix. Restore the verbatim warning per coder HANDOFF (PR #819 "
        "commit cf5e8ac9)."
    )

    # Forbidden #2: fromisoformat switch warning must remain in the audit prose.
    has_fromisoformat_warning = "fromisoformat" in step_0_5_body
    assert has_fromisoformat_warning, (
        "Step 0.5 audit prose must contain a future-editor warning "
        "FORBIDDING the `fromisoformat` switch without Python 3.11+ "
        "baseline verification. `fromisoformat` only handles the `Z` "
        "suffix in 3.11+; an earlier baseline raises ValueError on "
        "make_event's `%Y-%m-%dT%H:%M:%SZ` shape, the extractor crashes, "
        "the variable yields empty, and Step 0.5 silently fail-opens — "
        "defeating the compliance fix. The explicit-format `strptime` is "
        "portable to Python 3.7+; the switch warning protects portability. "
        "Restore the verbatim warning per coder HANDOFF (PR #819 commit "
        "cf5e8ac9)."
    )
