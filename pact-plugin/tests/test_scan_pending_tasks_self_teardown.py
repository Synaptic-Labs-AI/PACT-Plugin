"""
End-to-end timing test for the self-correcting teardown check in
scan-pending-tasks.md Step 0.5. Extracts the bash block verbatim from
the .md file (drift-proof SSOT) and exercises it against synthetic
journal files.

What is verified:
- When the most recent `teardown_request.ts` (ISO-8601 UTC, parsed via
  strptime to epoch) is AFTER the most recent `scan_armed.ts` AND no
  `scan_disarmed.ts` after the teardown_request, Step 0.5 exits 0
  (fire — LLM-side invokes Skill("PACT:stop-pending-scan")).
- When a `scan_disarmed` event has already been written after the
  teardown_request, Step 0.5 falls through (already serviced).
- When no `teardown_request` event exists, Step 0.5 falls through
  (no pending teardown).
- Re-arm cycle (scan_armed → teardown_request → scan_disarmed →
  scan_armed → teardown_request): the LATEST triple drives the
  decision; Step 0.5 fires on the latest teardown_request.
- Fail-open contract: empty session dir / no scan_armed event causes
  empty extractor output and gate falls through.
- ISO→epoch conversion round-trips correctly across the strptime
  literal `%Y-%m-%dT%H:%M:%SZ` (coupling pair with
  session_journal.py:325 make_event format) — uniform across all
  three event types (teardown_request.ts, scan_armed.ts,
  scan_disarmed.ts).

The bash block is extracted from `commands/scan-pending-tasks.md`
Step 0.5 via a section-bounded search (`\\n0.5. ` to `\\n1. `) and
then the FIRST fenced ```bash``` block within that section. The shell
snippet uses `exit 0` to short-circuit on fire; we discriminate
between fire and fall-through by appending a sentinel `echo` after
the snippet — sentinel present in stdout means the snippet fell
through (no fire); sentinel absent means the snippet exited early
(fire path taken — LLM-side action would follow).
"""

from __future__ import annotations

import datetime
import json
import re
import subprocess
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SCAN_MD = ROOT / "commands" / "scan-pending-tasks.md"
PLUGIN_ROOT = ROOT  # `pact-plugin/`
SJ_PATH = PLUGIN_ROOT / "hooks" / "shared" / "session_journal.py"


# Coupling pair partner: this literal MUST equal session_journal.py:325
# `make_event` ts format. Any drift between the two silently breaks the
# ISO→epoch conversion in Step 0.5 (the read returns a string the bash
# integer comparison cannot parse).
ISO_FORMAT_LITERAL = "%Y-%m-%dT%H:%M:%SZ"


def _extract_step_0_5_bash_block(scan_md_text: str) -> str:
    """Extract the fenced ```bash``` block from §Operation Step 0.5.

    Returns the bash content with the markdown fence stripped. The
    indentation prefix of each line (3 spaces, since the bash block is
    nested under the numbered Step 0.5 list item) is also stripped.
    """
    op_start = scan_md_text.find("\n## Operation")
    assert op_start >= 0, "scan-pending-tasks.md missing §Operation section"
    step_0_5_pos = scan_md_text.find("\n0.5. ", op_start)
    assert step_0_5_pos >= 0, (
        "scan-pending-tasks.md §Operation missing Step 0.5 (`0.5. ` marker)"
    )
    # Bound Step 0.5 to next numbered step (Step 1).
    step_1_pos = scan_md_text.find("\n1. ", step_0_5_pos)
    step_0_5_body = (
        scan_md_text[step_0_5_pos:step_1_pos] if step_1_pos > 0
        else scan_md_text[step_0_5_pos:]
    )
    match = re.search(r"```bash\n(.*?)```", step_0_5_body, re.DOTALL)
    assert match is not None, (
        "Step 0.5 must contain a fenced ```bash``` block — the extractor "
        "is the SSOT and drift-proof contract between Step 0.5's prose "
        "and this test."
    )
    raw = match.group(1)
    lines = raw.splitlines()
    dedented = [ln[3:] if ln.startswith("   ") else ln for ln in lines]
    return "\n".join(dedented)


@pytest.fixture(scope="module")
def step_0_5_bash_template() -> str:
    """The Step 0.5 bash block, with `{plugin_root}` and `{session_dir}`
    template tokens preserved verbatim."""
    return _extract_step_0_5_bash_block(SCAN_MD.read_text(encoding="utf-8"))


def _render_step_0_5(template: str, plugin_root: Path, session_dir: Path) -> str:
    """Render the Step 0.5 bash by substituting `{plugin_root}` and
    `{session_dir}`. These tokens are platform-rendered at fire time;
    we mimic that substitution in the test harness."""
    return template.replace("{plugin_root}", str(plugin_root)).replace(
        "{session_dir}", str(session_dir)
    )


def _iso_ts(epoch_seconds: int) -> str:
    """Render an epoch as ISO-8601 UTC matching make_event's format.

    The format literal is byte-coupled to session_journal.py:325 and to
    the strptime literal in scan-pending-tasks.md Step 0.5. See
    ISO_FORMAT_LITERAL above.
    """
    return datetime.datetime.fromtimestamp(
        epoch_seconds, tz=datetime.timezone.utc
    ).strftime(ISO_FORMAT_LITERAL)


def _write_journal(session_dir: Path, event_type: str, payload: dict) -> Path:
    """Append a single JSONL event to the session journal. Uses a
    correctly-formatted `%Y-%m-%dT%H:%M:%SZ` `ts` matching make_event;
    callers may override `ts` in `payload` for ISO-timestamp tests.

    This helper is intentionally independent from the one in
    test_scan_pending_tasks_warmup_grace.py — its `ts` value is
    deliberately distinct (matches make_event format vs. the older
    test's `+00:00` shape) so the Step 0.5 ISO→epoch conversion is
    exercised against the canonical on-disk shape.
    """
    session_dir.mkdir(parents=True, exist_ok=True)
    journal = session_dir / "session-journal.jsonl"
    record = {
        "v": 1,
        "type": event_type,
        "ts": _iso_ts(int(time.time())),
    }
    record.update(payload)
    with journal.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return journal


def _write_teardown_request(session_dir: Path, epoch: int) -> Path:
    """Convenience: write a `teardown_request` event with `ts` stamped
    to the supplied epoch in canonical ISO-8601 UTC format. This
    matches the on-disk shape produced by
    `teardown_request_emitter.py` and `wake_inbox_drain.py` (both rely
    on `session_journal.make_event` for the `ts` field)."""
    return _write_journal(session_dir, "teardown_request", {
        "task_id": "fake-1",
        "team_name": "fake-team",
        "ts": _iso_ts(epoch),
    })


def _run_step_0_5(bash_body: str) -> subprocess.CompletedProcess:
    """Run the Step 0.5 bash block with a sentinel echo appended.

    Discriminator: if the snippet `exit 0`-fires, stdout will NOT
    contain SENTINEL. If the snippet falls through, stdout WILL
    contain SENTINEL.
    """
    sentinel = "STEP_0_5_FELL_THROUGH"
    script = bash_body + f'\necho "{sentinel}"\n'
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
    )


SENTINEL = "STEP_0_5_FELL_THROUGH"


def test_step_0_5_fires_when_teardown_request_after_arm(
    tmp_path, step_0_5_bash_template
):
    """Pending-teardown happy path: a `teardown_request` was written
    AFTER the latest `scan_armed.ts` and BEFORE any `scan_disarmed`.
    Step 0.5 must fire (sentinel absent).
    """
    session_dir = tmp_path / "session"
    now = int(time.time())
    _write_journal(session_dir, "scan_armed", {"ts": _iso_ts(now - 200)})
    _write_teardown_request(session_dir, now - 100)

    bash_body = _render_step_0_5(step_0_5_bash_template, PLUGIN_ROOT, session_dir)
    result = _run_step_0_5(bash_body)

    assert result.returncode == 0, (
        f"Step 0.5 exit code expected 0, got {result.returncode}. "
        f"stderr={result.stderr!r}"
    )
    assert SENTINEL not in result.stdout, (
        f"Step 0.5 should have FIRED on teardown_request(now-100) > "
        f"scan_armed(now-200) with no scan_disarmed. Sentinel "
        f"{SENTINEL!r} present in stdout indicates the gate fell "
        f"through. stdout={result.stdout!r}"
    )


def test_step_0_5_does_not_fire_when_disarm_after_teardown_request(
    tmp_path, step_0_5_bash_template
):
    """The teardown has already been serviced: `scan_disarmed.ts` >
    `teardown_request.ts`. Step 0.5 must fall through (sentinel
    present) — stop-pending-scan has already run.
    """
    session_dir = tmp_path / "session"
    now = int(time.time())
    _write_journal(session_dir, "scan_armed", {"ts": _iso_ts(now - 300)})
    _write_teardown_request(session_dir, now - 200)
    _write_journal(session_dir, "scan_disarmed", {"ts": _iso_ts(now - 100)})

    bash_body = _render_step_0_5(step_0_5_bash_template, PLUGIN_ROOT, session_dir)
    result = _run_step_0_5(bash_body)

    assert result.returncode == 0, (
        f"Step 0.5 exit code expected 0, got {result.returncode}. "
        f"stderr={result.stderr!r}"
    )
    assert SENTINEL in result.stdout, (
        f"Step 0.5 should have FALLEN THROUGH (already serviced) — "
        f"scan_disarmed(now-100) > teardown_request(now-200). Sentinel "
        f"{SENTINEL!r} absent in stdout indicates Step 0.5 fired "
        f"spuriously. stdout={result.stdout!r}"
    )


def test_step_0_5_does_not_fire_when_no_teardown_request(
    tmp_path, step_0_5_bash_template
):
    """No teardown_request event in journal: Step 0.5 must fall through
    (sentinel present). `[ -n "$LATEST_TEARDOWN_REQUEST" ]` guard
    closes the gate when the extractor yields empty string.
    """
    session_dir = tmp_path / "session"
    now = int(time.time())
    _write_journal(session_dir, "scan_armed", {"ts": _iso_ts(now - 100)})

    bash_body = _render_step_0_5(step_0_5_bash_template, PLUGIN_ROOT, session_dir)
    result = _run_step_0_5(bash_body)

    assert result.returncode == 0, (
        f"Step 0.5 exit code expected 0, got {result.returncode}. "
        f"stderr={result.stderr!r}"
    )
    assert SENTINEL in result.stdout, (
        f"Step 0.5 should have FALLEN THROUGH (no teardown_request) — "
        f"empty LATEST_TEARDOWN_REQUEST should close the `-n` guard. "
        f"Sentinel {SENTINEL!r} absent in stdout indicates the gate "
        f"fired spuriously. stdout={result.stdout!r}"
    )


def test_step_0_5_fires_on_re_arm_cycle(tmp_path, step_0_5_bash_template):
    """Re-arm cycle: an older (scan_armed, teardown_request,
    scan_disarmed) triple was serviced; then a new scan_armed
    landed; then a new teardown_request. Step 0.5 must fire on the
    LATEST teardown_request (latest-vs-latest semantics in the
    `read-last` reader contract).

    Sequence:
      now-1000: scan_armed (old)
      now-900:  teardown_request (old)
      now-800:  scan_disarmed (old — serviced)
      now-700:  scan_armed (new)
      now-100:  teardown_request (new — unserviced)
    """
    session_dir = tmp_path / "session"
    now = int(time.time())
    _write_journal(session_dir, "scan_armed", {"ts": _iso_ts(now - 1000)})
    _write_teardown_request(session_dir, now - 900)
    _write_journal(session_dir, "scan_disarmed", {"ts": _iso_ts(now - 800)})
    _write_journal(session_dir, "scan_armed", {"ts": _iso_ts(now - 700)})
    _write_teardown_request(session_dir, now - 100)

    bash_body = _render_step_0_5(step_0_5_bash_template, PLUGIN_ROOT, session_dir)
    result = _run_step_0_5(bash_body)

    assert result.returncode == 0, (
        f"Step 0.5 exit code expected 0, got {result.returncode}. "
        f"stderr={result.stderr!r}"
    )
    assert SENTINEL not in result.stdout, (
        f"Step 0.5 should have FIRED on the LATEST triple: "
        f"teardown_request(now-100) > scan_armed(now-700) > "
        f"scan_disarmed(now-800). Sentinel {SENTINEL!r} present in "
        f"stdout indicates the gate is reading stale events — the "
        f"reader is no longer returning the most-recent event. "
        f"stdout={result.stdout!r}"
    )


def test_step_0_5_falls_through_when_no_journal(tmp_path, step_0_5_bash_template):
    """Fail-open contract: empty session dir (no journal file).
    `read-last` returns null on missing journal → all three
    extractors yield empty string → `-n` guards close → gate falls
    through.
    """
    session_dir = tmp_path / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    # No journal file written.

    bash_body = _render_step_0_5(step_0_5_bash_template, PLUGIN_ROOT, session_dir)
    result = _run_step_0_5(bash_body)

    assert result.returncode == 0, (
        f"Step 0.5 exit code expected 0, got {result.returncode}. "
        f"stderr={result.stderr!r}"
    )
    assert SENTINEL in result.stdout, (
        f"Step 0.5 should fall through on missing journal file "
        f"(fail-open contract). Sentinel {SENTINEL!r} absent in "
        f"stdout indicates the gate fired spuriously on empty "
        f"extractors. stdout={result.stdout!r}"
    )
    # Stderr-cleanliness invariant: the `[ -n "$VAR" ]` guards are what
    # make the empty-extractor case fall through cleanly. If a future
    # editor strips a guard, the bash `-gt` test would receive an empty
    # operand and emit `[: : integer expected` to stderr — the gate
    # would STILL fall through (because the failed `-gt` evaluates to
    # false), so the sentinel-in-stdout assertion would not catch the
    # regression. Pinning stderr cleanliness catches the guard-strip
    # case at the structural-invariant level.
    assert "integer expected" not in result.stderr, (
        f"Step 0.5 fall-through path must be stderr-clean — the "
        f"`[ -n \"$VAR\" ]` guards close BEFORE the integer `-gt` "
        f"comparison reaches empty operands. Presence of `integer "
        f"expected` in stderr indicates a guard was stripped and the "
        f"`-gt` test ran with an empty operand. stderr={result.stderr!r}"
    )


def test_step_0_5_falls_through_when_only_teardown_request_no_arm(
    tmp_path, step_0_5_bash_template
):
    """Defensive: a teardown_request exists but no scan_armed event.
    `[ -n "$LATEST_SCAN_ARMED" ]` guard closes the gate. Step 0.5
    must fall through (the cron should not have been armed without a
    corresponding scan_armed event; if it is, falling through is the
    conservative choice).
    """
    session_dir = tmp_path / "session"
    now = int(time.time())
    _write_teardown_request(session_dir, now - 100)

    bash_body = _render_step_0_5(step_0_5_bash_template, PLUGIN_ROOT, session_dir)
    result = _run_step_0_5(bash_body)

    assert result.returncode == 0, (
        f"Step 0.5 exit code expected 0, got {result.returncode}. "
        f"stderr={result.stderr!r}"
    )
    assert SENTINEL in result.stdout, (
        f"Step 0.5 should fall through on missing scan_armed event "
        f"(defensive guard against ill-formed journal state). "
        f"Sentinel {SENTINEL!r} absent in stdout indicates the gate "
        f"fired without an arm anchor. stdout={result.stdout!r}"
    )
    # Stderr-cleanliness invariant: see test_step_0_5_falls_through_
    # when_no_journal for the full rationale. This test exercises the
    # `[ -n "$LATEST_SCAN_ARMED" ]` guard specifically — stripping it
    # would let the `-gt` comparison receive empty `$LATEST_SCAN_ARMED`
    # and emit `integer expected` to stderr, without changing the
    # sentinel-in-stdout outcome.
    assert "integer expected" not in result.stderr, (
        f"Step 0.5 fall-through path must be stderr-clean — the "
        f"`[ -n \"$LATEST_SCAN_ARMED\" ]` guard closes BEFORE the "
        f"integer `-gt` comparison reaches an empty operand. Presence "
        f"of `integer expected` in stderr indicates the guard was "
        f"stripped. stderr={result.stderr!r}"
    )


def test_step_0_5_strptime_round_trip_uniform_across_three_sources(
    tmp_path, step_0_5_bash_template
):
    """Uniform-strptime coupling contract: all three Step 0.5 extractors
    (`teardown_request.ts`, `scan_armed.ts`, `scan_disarmed.ts`) parse
    ISO-8601 UTC strings via the same strptime literal
    `%Y-%m-%dT%H:%M:%SZ` and yield integer-comparable epochs. This
    pins the byte-identity contract between session_journal.make_event's
    format string and the Step 0.5 extractor literal — any drift in
    either makes the comparison silently fail.

    Verifies the round-trip across all three sources: arrange a journal
    with deterministic anchors where the latest `scan_disarmed` is
    strictly newer than the latest `teardown_request` (which is
    strictly newer than `scan_armed`). Step 0.5 must FALL THROUGH
    because `scan_disarmed.ts > teardown_request.ts` — i.e., the
    teardown is already serviced. This exercises strptime on all three
    extractors (all three must parse without raising for the gate
    decision to be correct).

    Counter-test-by-revert: changing the format literal in either the
    .md or session_journal.py without updating the other would cause
    strptime to raise (visible in stderr) or return wrong epoch
    (sentinel position would flip — the gate would fire instead of
    falling through).
    """
    session_dir = tmp_path / "session"
    # Use a deterministic anchor epoch to make the round-trip explicit
    # and reproducible across test runs. Three timestamps spanning a
    # 2-second window exercise all three extractors with byte-distinct
    # ts strings.
    anchor_epoch = int(datetime.datetime(
        2026, 5, 21, 8, 54, 27, tzinfo=datetime.timezone.utc
    ).timestamp())
    _write_journal(session_dir, "scan_armed", {"ts": _iso_ts(anchor_epoch)})
    _write_teardown_request(session_dir, anchor_epoch + 1)
    _write_journal(session_dir, "scan_disarmed", {"ts": _iso_ts(anchor_epoch + 2)})

    bash_body = _render_step_0_5(step_0_5_bash_template, PLUGIN_ROOT, session_dir)
    result = _run_step_0_5(bash_body)

    assert result.returncode == 0, (
        f"Step 0.5 exit code expected 0, got {result.returncode}. "
        f"stderr={result.stderr!r}"
    )
    assert SENTINEL in result.stdout, (
        f"Step 0.5 should have FALLEN THROUGH — scan_disarmed.ts "
        f"(anchor+2s) > teardown_request.ts (anchor+1s) > "
        f"scan_armed.ts (anchor). All three extractors must parse via "
        f"strptime to produce integer-comparable epochs that satisfy "
        f"the already-serviced branch. Sentinel {SENTINEL!r} absent in "
        f"stdout indicates one of the strptime conversions failed "
        f"(format literal mismatch with session_journal.make_event) "
        f"or returned a wrong epoch. stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )


def test_step_0_5_double_fire_before_disarm_is_idempotent(
    tmp_path, step_0_5_bash_template
):
    """Double-disarm idempotency contract: multiple consecutive cron-fires
    hitting Step 0.5 BEFORE `stop-pending-scan` has written `scan_disarmed`
    must each fire (exit 0) deterministically. The architecture spec's
    audit prose (scan-pending-tasks.md Step 0.5 line 62) claims:

        Multiple consecutive cron-fires hitting this branch before
        `stop-pending-scan` completes write multiple `scan_disarmed`
        events — benign; the latest dominates.

    The doc-only claim covers the post-disarm-write side (latest dominates),
    but doesn't pin the PRE-disarm side: between the LLM-side action firing
    `Skill("PACT:stop-pending-scan")` and that skill actually completing
    its `scan_disarmed` write, additional cron fires can land in the same
    (scan_armed, teardown_request) window with NO `scan_disarmed` event
    yet written. Each such fire must deterministically choose to fire
    Step 0.5 (not flap to fall-through).

    This test exercises two consecutive Step 0.5 evaluations on the
    SAME journal state (no events added between them; the bash is purely
    a function of the latest-event triple). Both must fire and produce
    byte-identical sentinel-absent output. The test would catch:

      - Any subtle non-determinism in the bash (e.g., a future edit that
        introduces a `read-last`-and-mutate pattern would diverge across
        calls).
      - Any side effect from one invocation that affects the next
        (e.g., a future edit that touches a marker file before exit).
      - Drift in the latest-event read semantics that would yield
        different results across consecutive same-input reads.

    Counter-test-by-revert: inserting a side-effect (e.g., `touch
    /tmp/step-0-5-already-fired; [ -e /tmp/step-0-5-already-fired ] &&
    exit 1`) into Step 0.5's bash would cause the second invocation to
    fail-through (sentinel present). This test catches that exact
    regression pattern.

    This is the documentation-in-code partner to the audit-prose claim;
    making the property falsifiable. Per
    feedback_documentation_in_code_tests memory.
    """
    session_dir = tmp_path / "session"
    now = int(time.time())
    _write_journal(session_dir, "scan_armed", {"ts": _iso_ts(now - 200)})
    _write_teardown_request(session_dir, now - 100)
    # NOTE: No scan_disarmed event written yet — Step 0.5 must fire each time.

    bash_body = _render_step_0_5(step_0_5_bash_template, PLUGIN_ROOT, session_dir)

    # First fire — should fire Step 0.5 (sentinel absent).
    result1 = _run_step_0_5(bash_body)
    assert result1.returncode == 0, (
        f"First Step 0.5 invocation exit code expected 0, got "
        f"{result1.returncode}. stderr={result1.stderr!r}"
    )
    assert SENTINEL not in result1.stdout, (
        f"First Step 0.5 invocation should have FIRED. "
        f"stdout={result1.stdout!r}"
    )

    # Second fire — same journal state, no scan_disarmed yet — must
    # ALSO fire Step 0.5 (sentinel absent).
    result2 = _run_step_0_5(bash_body)
    assert result2.returncode == 0, (
        f"Second Step 0.5 invocation exit code expected 0, got "
        f"{result2.returncode}. stderr={result2.stderr!r}"
    )
    assert SENTINEL not in result2.stdout, (
        f"Second Step 0.5 invocation on UNCHANGED journal state should "
        f"have FIRED again (idempotent decision on same input). Sentinel "
        f"{SENTINEL!r} present in stdout indicates Step 0.5 is no longer "
        f"a pure function of latest-event-triple state — a regression "
        f"that would break the double-disarm-is-benign architectural "
        f"claim (scan-pending-tasks.md Step 0.5 audit prose line ~62). "
        f"stdout={result2.stdout!r}"
    )

    # Determinism check: both invocations must produce byte-identical
    # stdout/stderr/returncode given identical inputs. A divergence here
    # would surface non-deterministic state-mutation introduced by a
    # future edit.
    assert result1.stdout == result2.stdout, (
        f"Step 0.5 must produce deterministic output on identical inputs. "
        f"First stdout={result1.stdout!r}; second stdout={result2.stdout!r}"
    )
    assert result1.returncode == result2.returncode, (
        f"Step 0.5 must produce deterministic exit codes on identical "
        f"inputs. First rc={result1.returncode}; second rc={result2.returncode}"
    )


def test_step_0_5_falls_through_on_same_second_teardown_request_and_scan_armed(
    tmp_path, step_0_5_bash_template
):
    """Pin the `-gt` (strict greater-than) semantic for the same-second
    edge case: when `teardown_request.ts` and `scan_armed.ts` land at
    the same epoch second, `LATEST_TEARDOWN_REQUEST -gt
    LATEST_SCAN_ARMED` must evaluate FALSE (because `N -gt N` is
    false in bash) and Step 0.5 must fall through.

    Architect rationale (§9.1): equality is conservative — a teardown
    that landed in the same wall-clock second as the arm is treated
    as part of the arm cycle rather than a pending-teardown signal;
    if the teardown is real, the next cron-fire (5+ minutes later)
    will catch it via a later `teardown_request` write.

    Counter-test-by-revert: mutating bash `-gt` to `-ge` in the Step
    0.5 block flips this test (sentinel would be absent — the gate
    would fire on the same-second case, violating the conservative
    contract). Restoring `-gt` makes the test pass again. This pins
    the operator choice against a future-editor 'consistency with
    Step 0's `[ $delta -ge 0 ]`' temptation.
    """
    session_dir = tmp_path / "session"
    # Use a deterministic anchor epoch for byte-stable reproduction
    # across test runs; the test does not depend on real wall-clock
    # time.
    anchor_epoch = int(datetime.datetime(
        2026, 5, 22, 2, 0, 0, tzinfo=datetime.timezone.utc
    ).timestamp())
    _write_journal(session_dir, "scan_armed", {"ts": _iso_ts(anchor_epoch)})
    _write_teardown_request(session_dir, anchor_epoch)  # same second

    bash_body = _render_step_0_5(step_0_5_bash_template, PLUGIN_ROOT, session_dir)
    result = _run_step_0_5(bash_body)

    assert result.returncode == 0, (
        f"Step 0.5 exit code expected 0, got {result.returncode}. "
        f"stderr={result.stderr!r}"
    )
    assert SENTINEL in result.stdout, (
        f"Step 0.5 should have FALLEN THROUGH on same-second equality "
        f"(teardown_request(anchor) == scan_armed(anchor)). `-gt` is "
        f"strict; equality must NOT fire the gate. Sentinel {SENTINEL!r} "
        f"absent in stdout indicates the operator was loosened to `-ge` "
        f"or equivalent, violating the conservative-equality contract. "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
