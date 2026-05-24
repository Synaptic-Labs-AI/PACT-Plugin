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

Strptime-not-string-compare coupling (post-#821 ts unification):

Under #821's ts-unification (folded into PR #820), the three pending-scan
lifecycle events (`scan_armed`, `scan_disarmed`, `teardown_request`) all
carry only the auto-stamped ISO-8601 `ts` field; the prior integer-epoch
`armed_at`/`disarmed_at` fields are deleted. All three consumer sites
(scan-pending-tasks.md Step 0, Step 0.5, and wake_inbox_drain.py:685+
producer-side idempotency check) parse `ts` uniformly via strptime to
int-epoch before comparing.

The 4-site byte-identity coupling chain collapses to 2 sites:

  - session_journal.py `make_event` (upstream SSOT — stamps `ts` via
    `strftime(\"%Y-%m-%dT%H:%M:%SZ\")`)
  - the uniform-strptime extractor pattern across Step 0 / Step 0.5 /
    wake_inbox_drain.py:685+ (consumers — parse `ts` via the same literal)

The remaining structural defense in this file is the strptime-not-string-
compare invariant: the Python consumer at wake_inbox_drain.py:685+ MUST
parse `scan_armed.ts` and `scan_disarmed.ts` via strptime — direct
lexical string comparison would coincidentally match epoch ordering under
the canonical `%Y-%m-%dT%H:%M:%SZ` format but silently break under any
future format drift (sub-second fractions, mixed TZ suffixes, or a
fromisoformat switch on a Python 3.11+ baseline). This file's pin is
counterpart to scan-pending-tasks.md:66 audit prose's "direct lexical
comparison ... breaks silently under format drift" forecloseure.
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


def test_python_consumer_parses_ts_via_strptime_not_string_compare():
    """Code-shape invariant: the wake_inbox_drain.py producer-side
    idempotency check MUST parse `scan_armed.ts` and `scan_disarmed.ts`
    via strptime AND thread the result through `int(...timestamp())`
    to produce an `_epoch` binding before comparing — direct lexical
    string comparison of the ISO-8601 `ts` strings would coincidentally
    match epoch ordering under the canonical `%Y-%m-%dT%H:%M:%SZ`
    format but silently break under any format drift (sub-second
    fractions, mixed TZ suffixes, or any future format relaxation
    including a 3.11+ `fromisoformat` switch).

    This pin guards against the future-editor 'simplification':

        armed_ts = armed.get(\"ts\")
        disarmed_ts = disarmed.get(\"ts\")
        if armed_ts > disarmed_ts:   # direct lex compare — silently broken under drift
            print(_SUPPRESS_OUTPUT)

    The architecturally-correct shape (which this test pins) is:

        armed_ts = armed.get(\"ts\")
        armed_epoch = int(datetime.strptime(armed_ts, _TS_FMT)
                          .replace(tzinfo=timezone.utc).timestamp())
        ...  symmetric for disarmed  ...
        if armed_epoch > disarmed_epoch:

    USE-based result-binding pin (F4 hardening per secretary memory
    `fa044ba5`, applied 2026-05-23 in commit-i/j): the prior
    presence-based pin asserted `count(\"strptime\") >= 2` inside the
    span, which is PHANTOM-GREEN against a decoy mutant of the form

        _ = datetime.strptime(armed_ts, _TS_FMT)
        _ = datetime.strptime(disarmed_ts, _TS_FMT)
        if armed_ts > disarmed_ts:   # silent lex compare; decoys satisfy count

    The decoy preserves the strptime CALL count (and even a
    `strptime(.*_TS_FMT.*)` paren+arg form-(a) pin), but the result is
    discarded into `_` while the comparator falls back to direct lex
    compare on the raw ts strings. The fix per `fa044ba5` is the
    result-binding pattern form-(b): require an `<name>_epoch` binding
    on the LHS of an `int(...datetime.strptime(...,_TS_FMT...))`
    assignment. Decoys assigned to `_` (or any non-`_epoch` name) no
    longer satisfy the pin. The full match shape is:

        <name>_epoch = int(
            datetime.strptime(<ts>, _TS_FMT)
            ...

    enforced via regex `\\w+_epoch\\s*=\\s*int\\s*\\(\\s*\\n?\\s*datetime\\.strptime\\([^)]*_TS_FMT`
    against the span-delimited producer-side block. The result-binding
    form is what the comparator actually consumes — pinning it pins
    the load-bearing CONTRACT rather than just the presence of an
    `strptime` token. Form-(c) AST-Call-node assertion (per `fa044ba5`)
    is strongest but adds `ast` import + ~15 LOC of node-walking;
    form-(b) is the minimum-viable upgrade that closes the empirical
    decoy-strptime vector.

    Span-delimited assertion (per architect §10.3): the result-binding
    matches MUST appear inside the producer-side idempotency-check
    region — the `try:` block enclosing `read_last_event(\"scan_armed\")`
    / `read_last_event(\"scan_disarmed\")` / `except Exception: pass`.
    A coarse whole-file regex would still phantom-green a mutant
    because the surrounding docstrings + comments may reference the
    result-binding pattern as prose (this test's own docstring above
    contains `armed_epoch = int(datetime.strptime(...))` as
    illustrative example). The span-delimited form counts only the
    production CALL sites inside the producer-side try-block.

    Counter-test-by-revert (CUMULATIVE strip, empirical, verified
    during F4 fold). Each row strips MORE of the load-bearing shape
    than the prior row — these are NOT 3 independent strips. The
    cumulative framing matches the discipline established for the
    Q2 retargeted-test docstrings in commit-g (per secretary
    `0d19dfbd`).

      Row 1 (replace strptime CALLS with direct lex compare, no
        decoys — naive future-editor 'simplification'): test FAILS
        because the result-binding matches drop from 2 to 0 inside
        the span.

      Row 2 (CUMULATIVE: replace strptime CALLS with direct lex
        compare AND add decoy `_ = datetime.strptime(...,_TS_FMT)`
        references inside the span — hostile-actor decoy bypass of
        the OLD presence-based pin): test STILL FAILS under the
        F4 result-binding pin because `_` does not match
        `\\w+_epoch`. The OLD presence-based `count(\\\"strptime\\\") >= 2`
        pin would PHANTOM-GREEN at this row; the F4 hardening is
        precisely the defense against this.

      Row 3 (CUMULATIVE: replace strptime CALLS with direct lex
        compare AND add decoys AND rename the decoy LHS to
        something matching `\\w+_epoch` like `_epoch =
        datetime.strptime(...,_TS_FMT)` — but WITHOUT the
        `int(...timestamp())` shape): test STILL FAILS because the
        result-binding regex requires the `int(\\s*\\n?\\s*datetime.strptime`
        shape; a bare `_epoch = datetime.strptime(...)` lacks
        `int(` on the RHS and does not match.

      Row 4 (CUMULATIVE: replace strptime CALLS with direct lex
        compare AND add full result-binding decoys
        `_epoch = int(datetime.strptime(...,_TS_FMT).timestamp())`
        whose values are then DISCARDED — the comparator still
        uses raw lex compare): test would PHANTOM-GREEN. This is
        the residual phantom-green vector form-(b) does not close;
        form-(c) AST-Call-node assertion would close it by
        requiring the binding result to be threaded into the
        comparator. NOT addressed in this fold; F4-future-extension
        candidate if a Row-4-shape mutant is observed in the wild.

    What this test pins: the COMPOSITE invariant that the
    producer-side block produces and consumes an `_epoch` binding
    from `int(datetime.strptime(<ts>,_TS_FMT)...)` — the
    load-bearing comparator pipeline shape. Each individual layer
    (strptime presence, result-binding shape, _epoch name pattern,
    int+timestamp threading) is partially redundant by design;
    cumulative strip of all 3 form-(b)-detectable layers is the
    minimum mutation that breaks detection.

    This test REPLACES the prior 4-site byte-identity coupling pin
    (`test_iso_format_literal_byte_identical_across_step_0_5_coupling_sites`,
    retired in this same commit per architect §3.4 Q4). The retiring
    test's rationale collapsed under #821's ts-unification: the
    4-site chain reduced to 2 sites, and per-site presence pins
    (`test_step_0_5_audit_prose_forbids_set_e_and_fromisoformat` below
    and the structural pin in test_scan_pending_tasks_command_structure.py)
    cover the format-literal presence side. What was NOT pinned by
    any prior test is the code-shape invariant in the Python consumer
    — that's this test's contribution. The F4 hardening (commit-i/j)
    upgrades the original presence-based span-delimited pin to a
    USE-based result-binding pin per `fa044ba5`.
    """
    src = (ROOT / "hooks" / "wake_inbox_drain.py").read_text(encoding="utf-8")

    # Delimit the producer-side idempotency-check span by its structural
    # markers: the `try:` line preceding the `read_last_event("scan_armed")`
    # call, and the `except Exception:` line that closes the block. Both
    # are unique within the file (only the producer-side check reads both
    # scan_armed and scan_disarmed via read_last_event). Anchoring on the
    # `read_last_event("scan_armed")` call (rather than a free-floating
    # `try:`) makes the span robust against future unrelated try/except
    # blocks.
    armed_anchor = src.find('read_last_event("scan_armed")')
    assert armed_anchor >= 0, (
        "wake_inbox_drain.py must contain a `read_last_event(\"scan_armed\")` "
        "call in the producer-side idempotency check. The span-delimited "
        "strptime pin cannot apply without this anchor."
    )
    # Walk backward from the anchor to the nearest `try:` keyword (the
    # `try:` opening the producer-side block) — this is the span start.
    span_start = src.rfind("\n    try:\n", 0, armed_anchor)
    assert span_start >= 0, (
        "wake_inbox_drain.py must wrap the `read_last_event(\"scan_armed\")` "
        "call in a `try:` block — the outer fail-conservative catch is the "
        "load-bearing producer-side guard, and the span-delimited strptime "
        "pin uses this `try:` as the span-start anchor."
    )
    # Walk forward from the anchor to the matching `except Exception:` —
    # this is the span end.
    span_end = src.find("\n    except Exception:\n", armed_anchor)
    assert span_end >= 0, (
        "wake_inbox_drain.py producer-side `try:` block must close with "
        "`except Exception:` — the wider catch is the fail-conservative "
        "contract that keeps emit-on-malformed-journal-event correct. "
        "The span-delimited strptime pin uses this `except` as the "
        "span-end anchor."
    )
    span = src[span_start:span_end]

    # F4 USE-based result-binding pin: require `<name>_epoch = int(
    # datetime.strptime(<ts>, _TS_FMT) ...)` shape inside the span.
    # Pinning the result-binding (rather than the bare strptime call)
    # closes the decoy-strptime phantom-green vector — see docstring
    # above for the empirical 4-row cumulative-strip recipe.
    result_binding_pattern = re.compile(
        r"\w+_epoch\s*=\s*int\s*\(\s*\n?\s*datetime\.strptime\([^)]*_TS_FMT"
    )
    result_binding_matches = result_binding_pattern.findall(span)
    assert len(result_binding_matches) >= 2, (
        "wake_inbox_drain.py producer-side idempotency check (span: "
        "`try:` through `except Exception:` enclosing the "
        "`read_last_event(\"scan_armed\")` / `read_last_event(\"scan_disarmed\")` "
        "calls) must produce and consume an `<name>_epoch` binding from "
        "`int(datetime.strptime(<ts>, _TS_FMT) ...)` for BOTH "
        "scan_armed.ts and scan_disarmed.ts. A match count < 2 inside "
        "the span suggests at least one side has been mutated to direct "
        "lexical string comparison (potentially with decoy strptime "
        "references that bypass a presence-based count pin) — which "
        "would silently break under any future ts format drift. Matched "
        f"{len(result_binding_matches)} result-binding occurrence(s) "
        f"inside the span: {result_binding_matches!r}."
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
