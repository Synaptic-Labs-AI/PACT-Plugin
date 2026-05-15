"""
End-to-end timing test for the warmup-grace skip in scan-pending-tasks.md
Step 0. Extracts the bash block verbatim from the .md file (drift-proof
SSOT) and exercises it against synthetic journal files.

What is verified:
- When the most recent `scan_armed` event's `armed_at` is within the
  warmup-grace window (now - 30s), the bash block exits 0 and short-
  circuits — no further stdout.
- When the most recent `scan_armed` event is outside the grace window
  (now - 300s), the bash block falls through Step 0 — the gate exits
  0 without short-circuiting.
- When no journal exists / no scan_armed event, the gate falls through
  (fail-open).

The bash block is extracted from `commands/scan-pending-tasks.md` Step 0
via regex against the fenced ```bash``` block. The shell snippet uses
`exit 0` to short-circuit on skip; we discriminate between skip and
fall-through by appending a sentinel `echo` after the snippet — sentinel
present in stdout means the snippet fell through (no skip); sentinel
absent means the snippet exited early (skip path taken).

Counter-test-by-revert: reverting the literal `180` in scan-pending-
tasks.md Step 0 to `120` (or any value < 30) flips the skip-window test
to fall-through. Reverting the `python3 -c` extraction to `jq` (or other
extractor) doesn't break these tests as long as the new extractor yields
an empty string on null input — but breaks T3's structural pin first.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SCAN_MD = ROOT / "commands" / "scan-pending-tasks.md"
PLUGIN_ROOT = ROOT  # `pact-plugin/`
SJ_PATH = PLUGIN_ROOT / "hooks" / "shared" / "session_journal.py"


# Single source of truth — bumping this constant should be the ONLY
# code edit required if the warmup-grace literal in Step 0 is re-tuned.
# Coupling pair partner: the cron interval in start-pending-scan.md
# §CronCreate Block must be re-tuned in lockstep.
WARMUP_GRACE_SECONDS = 180


def _extract_step_0_bash_block(scan_md_text: str) -> str:
    """Extract the fenced ```bash``` block from §Operation Step 0.

    Returns the bash content with the markdown fence stripped. The
    indentation prefix of each line (4 spaces, since the bash block is
    nested under the numbered Step 0 list item) is also stripped.
    """
    op_start = scan_md_text.find("\n## Operation")
    assert op_start >= 0, "scan-pending-tasks.md missing §Operation section"
    # Find Step 0 numbered marker.
    step_0_pos = scan_md_text.find("\n0. ", op_start)
    assert step_0_pos >= 0, "scan-pending-tasks.md §Operation missing Step 0"
    # Bound Step 0 to next numbered step (Step 1) or section.
    step_1_pos = scan_md_text.find("\n1. ", step_0_pos)
    step_0_body = scan_md_text[step_0_pos:step_1_pos] if step_1_pos > 0 else scan_md_text[step_0_pos:]
    # Extract the first ```bash ... ``` fenced block in Step 0.
    match = re.search(r"```bash\n(.*?)```", step_0_body, re.DOTALL)
    assert match is not None, (
        "Step 0 must contain a fenced ```bash``` block — the extractor "
        "is the SSOT and drift-proof contract between Step 0's prose "
        "and this test."
    )
    raw = match.group(1)
    # Strip the 4-space indent prefix (Step 0 list-item nesting).
    lines = raw.splitlines()
    dedented = [ln[3:] if ln.startswith("   ") else ln for ln in lines]
    return "\n".join(dedented)


@pytest.fixture(scope="module")
def step_0_bash_template() -> str:
    """The Step 0 bash block, with `{plugin_root}` and `{session_dir}`
    template tokens preserved verbatim."""
    return _extract_step_0_bash_block(SCAN_MD.read_text(encoding="utf-8"))


def _render_step_0(template: str, plugin_root: Path, session_dir: Path) -> str:
    """Render the Step 0 bash by substituting `{plugin_root}` and
    `{session_dir}`. These tokens are platform-rendered at fire time;
    we mimic that substitution in the test harness."""
    return template.replace("{plugin_root}", str(plugin_root)).replace(
        "{session_dir}", str(session_dir)
    )


def _write_journal(session_dir: Path, event_type: str, payload: dict) -> Path:
    """Append a single JSONL event to the session journal, matching
    the on-disk shape session_journal.py produces."""
    session_dir.mkdir(parents=True, exist_ok=True)
    journal = session_dir / "session-journal.jsonl"
    record = {
        "v": 1,
        "type": event_type,
        "ts": "2026-05-15T00:00:00+00:00",
    }
    record.update(payload)
    with journal.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return journal


def _run_step_0(bash_body: str) -> subprocess.CompletedProcess:
    """Run the Step 0 bash block with a sentinel echo appended.

    Discriminator: if the snippet `exit 0`-skips, stdout will NOT
    contain SENTINEL. If the snippet falls through, stdout WILL
    contain SENTINEL.
    """
    sentinel = "STEP0_FELL_THROUGH"
    script = bash_body + f'\necho "{sentinel}"\n'
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
    )


SENTINEL = "STEP0_FELL_THROUGH"


def test_step_0_skips_within_grace_window(tmp_path, step_0_bash_template):
    """When `armed_at = now - 30s` (well inside the WARMUP_GRACE_SECONDS
    window), Step 0 must exit 0 without falling through. Discriminator:
    sentinel NOT in stdout.

    Counter-test-by-revert: reverting the literal `180` in Step 0 to
    `0` or any value < 30 flips this test (Step 0 would no longer skip
    on now-30 because elapsed >= grace).
    """
    session_dir = tmp_path / "session"
    armed_at = int(time.time()) - 30
    assert 30 < WARMUP_GRACE_SECONDS, (
        f"Test fixture invariant: 30 < WARMUP_GRACE_SECONDS={WARMUP_GRACE_SECONDS}"
    )
    _write_journal(session_dir, "scan_armed", {"armed_at": armed_at})

    bash_body = _render_step_0(step_0_bash_template, PLUGIN_ROOT, session_dir)
    result = _run_step_0(bash_body)

    assert result.returncode == 0, (
        f"Step 0 exit code expected 0, got {result.returncode}. "
        f"stderr={result.stderr!r}"
    )
    assert SENTINEL not in result.stdout, (
        f"Step 0 should have SHORT-CIRCUITED on armed_at=now-30 "
        f"(elapsed=30 < WARMUP_GRACE_SECONDS={WARMUP_GRACE_SECONDS}). "
        f"Sentinel {SENTINEL!r} present in stdout indicates Step 0 fell "
        f"through. stdout={result.stdout!r}"
    )


def test_step_0_falls_through_outside_grace_window(tmp_path, step_0_bash_template):
    """When `armed_at = now - 300s` (well outside the WARMUP_GRACE_SECONDS
    window), Step 0 must fall through. Discriminator: sentinel IN stdout.

    Counter-test-by-revert: reverting the literal `180` in Step 0 to
    `>= 300` flips this test (Step 0 would short-circuit because elapsed
    < grace).
    """
    session_dir = tmp_path / "session"
    armed_at = int(time.time()) - 300
    assert 300 > WARMUP_GRACE_SECONDS, (
        f"Test fixture invariant: 300 > WARMUP_GRACE_SECONDS={WARMUP_GRACE_SECONDS}"
    )
    _write_journal(session_dir, "scan_armed", {"armed_at": armed_at})

    bash_body = _render_step_0(step_0_bash_template, PLUGIN_ROOT, session_dir)
    result = _run_step_0(bash_body)

    assert result.returncode == 0, (
        f"Step 0 exit code expected 0, got {result.returncode}. "
        f"stderr={result.stderr!r}"
    )
    assert SENTINEL in result.stdout, (
        f"Step 0 should have FALLEN THROUGH on armed_at=now-300 "
        f"(elapsed=300 > WARMUP_GRACE_SECONDS={WARMUP_GRACE_SECONDS}). "
        f"Sentinel {SENTINEL!r} absent in stdout indicates Step 0 "
        f"short-circuited. stdout={result.stdout!r}"
    )


def test_step_0_falls_through_when_no_scan_armed_event(tmp_path, step_0_bash_template):
    """When the journal exists but contains no `scan_armed` event, the
    `read-last --type scan_armed` invocation returns `null`; the
    extraction yields empty string; the gate falls through. This is
    the fail-open contract: no scan_armed event => normal scan body
    proceeds.
    """
    session_dir = tmp_path / "session"
    # Journal exists but has a different event type only.
    _write_journal(session_dir, "session_start", {
        "session_id": "fake-id", "project_dir": "/tmp"
    })

    bash_body = _render_step_0(step_0_bash_template, PLUGIN_ROOT, session_dir)
    result = _run_step_0(bash_body)

    assert result.returncode == 0, (
        f"Step 0 exit code expected 0, got {result.returncode}. "
        f"stderr={result.stderr!r}"
    )
    assert SENTINEL in result.stdout, (
        f"Step 0 should fall through on missing scan_armed event "
        f"(fail-open contract). stdout={result.stdout!r}"
    )


def test_step_0_falls_through_when_no_journal(tmp_path, step_0_bash_template):
    """When the session directory has no journal file at all, the
    read-last invocation returns `null`; the gate falls through.
    """
    session_dir = tmp_path / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    # No journal written.

    bash_body = _render_step_0(step_0_bash_template, PLUGIN_ROOT, session_dir)
    result = _run_step_0(bash_body)

    assert result.returncode == 0, (
        f"Step 0 exit code expected 0, got {result.returncode}. "
        f"stderr={result.stderr!r}"
    )
    assert SENTINEL in result.stdout, (
        f"Step 0 should fall through on missing journal file "
        f"(fail-open contract). stdout={result.stdout!r}"
    )
