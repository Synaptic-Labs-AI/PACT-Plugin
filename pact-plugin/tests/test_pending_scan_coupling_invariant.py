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

import ast
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

    USE-based AST Call-node pin (per secretary memory `fa044ba5`,
    promoted to form-(c) in commit-l). Earlier iterations of this pin
    walked a regression ladder:

      - Original presence-based pin: `count("strptime") >= 2` inside
        the span. Phantom-greens any decoy `_ = datetime.strptime(...)`
        because the decoy preserves the call count while the
        comparator falls back to direct lex compare.

      - form-(b) result-binding regex (commit-i/j, superseded):
        `\\w+_epoch\\s*=\\s*int\\s*\\(\\s*\\n?\\s*datetime\\.strptime\\([^)]*_TS_FMT`
        against the span. Closed the bare-decoy vector but phantom-
        greens any Row-4 mutant of the shape:

            armed_epoch = int(datetime.strptime(armed_ts, _TS_FMT)
                              .replace(tzinfo=timezone.utc).timestamp())
            armed_epoch = armed_ts   # OVERWRITE — value discarded
            ...
            if armed_epoch > disarmed_epoch:   # silent lex compare

        The regex matches the decoy result-binding line, satisfies
        `>= 2`, and never inspects subsequent re-assignment of the
        same `_epoch` Name. Empirically verified phantom-green
        against the producer-side span via /tmp probe during the
        commit-l fold (form-(b) regex returned 2 matches on a Row-4
        mutant where the comparator does string compare).

      - form-(c) AST Call-node pin (commit-l, current): parse the
        whole module via `ast.parse`, locate the FunctionDef whose
        body contains a `read_last_event(<event_name>)` Call to
        BOTH `scan_armed` and `scan_disarmed`, find the Try node
        whose body wraps both reads, then collect EVERY Assign /
        AnnAssign within that Try whose target Name matches
        `\\w+_epoch`. For each such assignment, classify the RHS:

          GOOD: `int(<chain ending in datetime.strptime(<ts>, _TS_FMT)>)`
                — walks chained `.replace(...).timestamp()` attribute
                Calls back to the strptime Call, asserts the function
                is `datetime.strptime` and one arg is the Name
                `_TS_FMT`.

          BAD : anything else, EXCEPT the fail-conservative literal
                `<name>_epoch = None` reset (allowed; the comparator
                gates on `is not None` so a None reset is correct).

        The test PASSES iff:
          (a) BOTH `armed_epoch` and `disarmed_epoch` have at least
              one GOOD assignment inside the span.
          (b) NO assignment to ANY `\\w+_epoch` target inside the
              span has a BAD RHS that is not literal None.

        Condition (b) is what closes Row-4: a decoy GOOD assignment
        followed by `armed_epoch = armed_ts` (Name RHS, not None
        and not int(strptime(...))) triggers the BAD-overwrite
        rejection. The hostile mutant cannot smuggle a string
        comparator behind a decoy without leaving a BAD assignment
        the AST walk catches.

        Strictly more general than the rejected hybrid regex
        alternative (form-(b) + forbid `\\w+_epoch\\s*=\\s*\\w+_ts\\b`),
        which only catches overwrites whose RHS Name has a `_ts`
        suffix. Empirically falsified against a Row-4 mutant whose
        overwrite used the arbitrary Name `armed_raw` (phantom-green
        under hybrid, FAIL under form-(c)).

    Span localization (architect §10.3): the AST walk restricts to
    assignments inside the Try whose body Calls `read_last_event` for
    BOTH `scan_armed` and `scan_disarmed`. Docstrings / comments /
    other code at module scope cannot satisfy the pin even if they
    quote a result-binding shape verbatim (this docstring contains
    `armed_epoch = int(datetime.strptime(...))` as illustrative
    example — it is module-level prose, not inside the producer Try
    AST, so does not contribute to the count).

    Counter-test-by-revert ladder (CUMULATIVE strip, each row
    incorporates the prior; empirical verification recipe in
    commit-l). Cardinality per row references what form-(c) detects.

      Row 1 (replace strptime CALLS with direct lex compare, no
        decoys — naive future-editor 'simplification'): FAIL
        because no GOOD `_epoch` assignments exist inside the span;
        condition (a) violated.

      Row 2 (CUMULATIVE: lex compare AND add decoy
        `_ = datetime.strptime(...,_TS_FMT)` references): FAIL
        because `_` does not match `\\w+_epoch` — the decoy does not
        even register with the AST walk. Condition (a) still
        violated; presence-based pin would PHANTOM-GREEN here.

      Row 3 (CUMULATIVE: rename decoy LHS to `_epoch =
        datetime.strptime(...,_TS_FMT)` WITHOUT the `int(...)`
        wrapper): FAIL because the RHS is a bare strptime Call, not
        the required `int(...)` outer Call; classified BAD, condition
        (b) violated.

      Row 4 (CUMULATIVE: full GOOD decoy
        `_epoch = int(datetime.strptime(...,_TS_FMT).timestamp())`
        followed by overwrite `_epoch = <name>_ts` and lex compare):
        FAIL under form-(c) because the overwrite is classified BAD
        (RHS is a Name, not None and not int(strptime(...))) and
        condition (b) catches it. PHANTOM-GREEN under the prior
        form-(b) regex — closing this is the commit-l contribution.

      Row 5 (residual, NOT closed by form-(c)): a mutant that
        produces a GOOD assignment AND uses the resulting `_epoch`
        name in the comparator, BUT the strptime call's first arg
        is a SHADOW variable rebound to a different string before
        the call:

            armed_ts = "1970-01-01T00:00:00Z"   # shadow
            armed_epoch = int(datetime.strptime(armed_ts, _TS_FMT)
                              .replace(tzinfo=timezone.utc).timestamp())

        The AST walk classifies this GOOD because it does not
        data-flow the arg back to `armed.get("ts")`. Closing Row 5
        requires full data-flow analysis (taint from `armed.get("ts")`
        through the strptime arg) — out of scope for a structural
        AST pin. F4-future-extension candidate if a Row-5-shape
        mutant is observed in the wild; deferred as a residual.

    What this test pins: the COMPOSITE invariant that EVERY `_epoch`
    assignment inside the producer-side Try produces the load-bearing
    int+strptime+_TS_FMT shape (or the fail-conservative None reset)
    — the comparator pipeline cannot be silently rerouted around the
    binding without leaving an AST violation.

    This test REPLACES the prior 4-site byte-identity coupling pin
    (`test_iso_format_literal_byte_identical_across_step_0_5_coupling_sites`,
    retired per architect §3.4 Q4 when #821's ts-unification reduced
    the 4-site chain to 2 sites). Per-site presence pins
    (`test_step_0_5_audit_prose_forbids_set_e_and_fromisoformat` below
    and the structural pin in test_scan_pending_tasks_command_structure.py)
    cover the format-literal presence side. The Python consumer code-
    shape invariant — that's this test's contribution.
    """
    src = (ROOT / "hooks" / "wake_inbox_drain.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    # Span localization via AST: find the FunctionDef containing a
    # `read_last_event("scan_armed")` Call, then the Try inside it whose
    # body Calls `read_last_event` for both `scan_armed` and `scan_disarmed`.
    # AST inspection (not source-string match) is robust against quote
    # style, formatting, and unrelated future try/except blocks.
    def _try_reads(node):
        names = set()
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id == "read_last_event"
                and len(sub.args) == 1
                and isinstance(sub.args[0], ast.Constant)):
                names.add(sub.args[0].value)
        return names

    fn_node = None
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and "scan_armed" in _try_reads(fn):
            fn_node = fn
            break
    assert fn_node is not None, (
        "wake_inbox_drain.py must contain a function whose body Calls "
        "`read_last_event(\"scan_armed\")` — the producer-side idempotency "
        "check anchor. The form-(c) AST pin cannot locate the span without it."
    )

    try_node = None
    for node in ast.walk(fn_node):
        if isinstance(node, ast.Try) and {"scan_armed", "scan_disarmed"}.issubset(_try_reads(node)):
            if try_node is None or node.lineno < try_node.lineno:
                try_node = node
    assert try_node is not None, (
        "wake_inbox_drain.py producer-side function must wrap the "
        "`read_last_event(\"scan_armed\")` / `read_last_event(\"scan_disarmed\")` "
        "calls in a single Try block — the outer fail-conservative catch is "
        "the load-bearing producer-side guard, and the form-(c) AST pin uses "
        "this Try as the span."
    )

    # Classify the RHS of an `<name>_epoch = ...` assignment. GOOD iff the
    # RHS is `int(<chain ending in datetime.strptime(<ts>, _TS_FMT)>)`. The
    # walk descends through `.replace(...).timestamp()` attribute-Call chains
    # back to the strptime Call.
    def _is_good_epoch_rhs(value):
        if not (isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "int"
                and len(value.args) == 1):
            return False
        cur = value.args[0]
        while isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute):
            if (cur.func.attr == "strptime"
                and isinstance(cur.func.value, ast.Name)
                and cur.func.value.id == "datetime"):
                return any(isinstance(a, ast.Name) and a.id == "_TS_FMT"
                           for a in cur.args)
            cur = cur.func.value
        return False

    # Collect every (Ann)Assign in the Try whose target is `\w+_epoch`.
    epoch_name_re = re.compile(r"^\w*_epoch$")
    epoch_assigns = []  # list of (lineno, target_id, is_good, is_none_reset)
    for node in ast.walk(try_node):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and epoch_name_re.match(tgt.id):
                    is_none = isinstance(node.value, ast.Constant) and node.value.value is None
                    epoch_assigns.append((node.lineno, tgt.id, _is_good_epoch_rhs(node.value), is_none))
        elif isinstance(node, ast.AnnAssign):
            if (isinstance(node.target, ast.Name)
                and epoch_name_re.match(node.target.id)
                and node.value is not None):
                is_none = isinstance(node.value, ast.Constant) and node.value.value is None
                epoch_assigns.append((node.lineno, node.target.id, _is_good_epoch_rhs(node.value), is_none))

    # Condition (b): no BAD non-None RHS in the span. Catches Row-4 mutants
    # where a GOOD decoy is followed by `<name>_epoch = <name>_ts` overwrite.
    violations = [(ln, nm) for ln, nm, good, none_reset in epoch_assigns
                  if not good and not none_reset]
    assert not violations, (
        "wake_inbox_drain.py producer-side Try (the block wrapping "
        "`read_last_event(\"scan_armed\")` / `read_last_event(\"scan_disarmed\")`) "
        "contains `<name>_epoch = ...` assignments whose RHS is neither "
        "`int(datetime.strptime(<ts>, _TS_FMT)...)` nor the fail-conservative "
        "literal `None` reset. This is the Row-4 phantom-green vector: a "
        "decoy result-binding shape can satisfy a regex pin while the actual "
        "comparator falls back to direct lex compare on the raw ts strings — "
        "silently broken under any future ts format drift. Violations "
        f"(lineno, target): {violations!r}. Restore the int(strptime(...)) "
        "shape, or use literal `None` for the fail-conservative path."
    )

    # Condition (a): both armed_epoch and disarmed_epoch must have at least
    # one GOOD assignment in the span (both sides of the comparator wired
    # through strptime).
    good_names = {nm for _, nm, good, _ in epoch_assigns if good}
    missing = {"armed_epoch", "disarmed_epoch"} - good_names
    assert not missing, (
        "wake_inbox_drain.py producer-side Try must contain at least one "
        "`<name>_epoch = int(datetime.strptime(<ts>, _TS_FMT)...)` "
        "assignment for EACH of `armed_epoch` and `disarmed_epoch` — both "
        "sides of the `armed_epoch > disarmed_epoch` comparator must be "
        f"wired through strptime. Missing GOOD bindings for: {sorted(missing)}. "
        f"Found GOOD bindings for: {sorted(good_names)}."
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
