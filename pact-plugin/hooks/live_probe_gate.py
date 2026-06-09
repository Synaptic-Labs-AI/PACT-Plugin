#!/usr/bin/env python3
"""
Live-Probe Gate (locus-b advisory)

Location: pact-plugin/hooks/live_probe_gate.py

Summary: PreToolUse(Bash) ADVISORY hook. When a `gh pr merge` / `gh pr close`
command runs in the PACT-plugin DEV repo on a branch that touches the hooks/
tree but has no fresh both-modes live-probe row logged in RUNBOOK_RUN_DATES.md
for the current plugin version, it emits a NON-BLOCKING WARN reminding the
operator to run (and log) the live-probe before closing the originating issue.

Coverage split (honest about what this hook does NOT do): the runtime WARN
fires ONLY at MERGE-time (`gh pr merge` / `gh pr close`), where the branch diff
(`git diff base...HEAD`) is non-empty and classifies correctly. Issue-close is
NOT a runtime arm of this hook: on `main` post-merge `base...HEAD` is EMPTY, so
a `gh issue close` classification would silently no-op -> a false
"checked & clear" signal, the exact registered-but-inert class this gate exists
to prevent (recursively, inside the gate). Issue-close is therefore enforced
PROCEDURALLY by the project CLAUDE.md pin + the no-Closes-#N sub-rule (close the
originating issue manually after the probe row is logged), NOT by this hook.

Posture (the two load-bearing invariants):
  - WARN-not-BLOCK: this hook ALWAYS exits 0; it never returns a
    permissionDecision=deny. BLOCK is deferred behind a measured WARN->BLOCK
    ladder (the #897 cry-wolf lesson).
  - FAIL-SAFE to silent-allow: EVERY resolution failure (not the plugin dev
    repo, not a matched command, unreadable version, git/classifier error, an
    unexpected exception) emits {"suppressOutput": true} and exits 0. This is
    the INVERSE of merge_guard's fail-CLOSED posture, because this hook is
    ADVISORY, not a security boundary: it must NEVER block a consumer and NEVER
    fail closed.

Used by: hooks.json PreToolUse hook (matcher: Bash), registered after
merge_guard_pre.py.
Input: JSON from stdin with tool_input.command.
Output: {"suppressOutput": true} + exit 0 (silent), or a stderr WARN advisory
        + {"suppressOutput": true} + exit 0.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NoReturn, Optional

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Critical imports: the classifier SSOT + the merge_guard PR command regexes.
# A failure here means we cannot classify -> disable the advisory (silent-allow),
# never block. INVERSE of merge_guard's fail-closed module-load guard.
#
# INERT config-dir coupling (do NOT assume zero coupling): importing
# merge_guard_common transitively resolves the Claude config-dir at import time
# (merge_guard_common derives a module-level TOKEN_DIR from
# shared.paths.get_claude_config_dir). This gate uses ONLY the two command
# regexes from that module — never TOKEN_DIR — so the config-dir coupling is
# INERT here: the gate's WARN-vs-silent decision is driven by CLAUDE_PROJECT_DIR
# (via _resolve_repo_root) + the RUNBOOK row scan, NOT by the config-dir. The
# coupling is import-transitive only; a future editor should not treat this hook
# as config-dir-free, but also need not route its decision through the config-dir.
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from shared.hook_infra_classifier import classify_diff
    from shared.merge_guard_common import (
        _GH_PR_MERGE_RE,
        _GH_PR_CLOSE_RE,
    )
except Exception:
    print(_SUPPRESS_OUTPUT)
    sys.exit(0)

# Matched commands: `gh pr merge` / `gh pr close` ONLY. `gh issue close` is
# DELIBERATELY absent: a runtime issue-close arm would classify via
# `git diff base...HEAD`, which is EMPTY on `main` post-merge, and would
# silently no-op -> a false "checked & clear" signal = the registered-but-inert
# class this gate exists to prevent. Issue-close is enforced PROCEDURALLY (the
# project pin + the no-Closes-#N sub-rule), not at runtime. Do NOT re-add an
# issue-close matcher without first solving the empty-diff classification
# problem (see the module docstring's "Coverage split").
_GATE_COMMAND_RES = (_GH_PR_MERGE_RE, _GH_PR_CLOSE_RE)

# Genuine-PASS verdict token for the RUNBOOK_RUN_DATES freshness scan. The
# `(?:ED)?` accepts BOTH "PASS" and "PASSED" (so a genuine probe row written
# either way satisfies the gate — kills the over-warn WITHOUT relying on
# operators typing exactly "PASS"). Case-SENSITIVE + bounded so it STILL
# rejects: the unfilled "PASS/FAIL" template placeholder (trailing "/"
# excluded), "bypass"/"BYPASS"/"BYPASSED" (preceding letter excluded by the
# lookbehind), and "non-genuine-pass" (lowercase). A substring `"pass" in low`
# match would false-satisfy on all of those -> a non-genuine/pending row could
# silently disarm the gate before any real probe (the recursive inert class
# this gate exists to prevent). This single regex threads both review findings:
# the over-warn (a genuine "PASSED" row was rejected) AND the disarm (a
# placeholder/bypass row was accepted).
# ── Verdict-cell parsing for the cross-row freshness aggregation ──────────────
# A version is satisfied iff, aggregated ACROSS all its rows, BOTH modes are
# {PASS or DEFERRED}, at least one is PASS, and neither is FAIL. Each row is
# classified per mode from its verdict cell (cells[3]). This unifies the two row
# shapes: the #924 per-mode template ("tmux PASS 2/2 · in-process PASS 2/2",
# both modes in one cell, verdict-then-count) AND the older single-mode legacy
# rows (923 "tmux 6/6 — PASS" / 926 "in-process 4/4 — PASS", count-then-verdict)
# plus the 926 deferral row (verdict "n/a", labelled "_deferred — tmux …").
_PASS_TOKEN_RE = re.compile(r"(?<![A-Za-z])PASS(?:ED)?(?![A-Za-z/])")
_FAIL_TOKEN_RE = re.compile(r"(?<![A-Za-z])FAIL(?![A-Za-z])")
# A COMPLETE count token "N/N". The `(?<![\d/])`/`(?![\d/])` boundaries reject a
# multi-slash forge like "2/2/2" (every candidate sub-pair is fenced by a '/').
_COUNT_RE = re.compile(r"(?<![\d/])(\d+)/(\d+)(?![\d/])")
# Per-mode segment separator in the #924 template verdict cell ("· ").
_MODE_SEP_RE = re.compile(r"·")


def _segment_for_mode(verdict_cell: str, mode: str) -> str:
    """The verdict-cell segment pertaining to `mode` — the #924 template splits
    modes with '·'; legacy single-mode rows are one segment. '' if `mode` is
    absent from the verdict cell."""
    for segment in _MODE_SEP_RE.split(verdict_cell):
        if mode in segment:
            return segment
    return ""


def _classify_segment(segment: str):
    """Classify one mode's verdict segment -> 'PASS' | 'FAIL' | None.
    PASS iff a genuine PASS token AND a COMPLETE count (numerator>0 AND
    numerator==denominator) AND no FAIL token. FAIL iff a FAIL token. None for a
    malformed/placeholder/incomplete verdict: 'PASS 0/2' (zero), 'PASS 1/2'
    (partial), 'PASS 3/2' (num>denom), 'PASS 2/2/2' (multi-slash), bare 'PASS',
    'PASS/FAIL' (no count + slash-fenced PASS). Count is interpreted numerically
    so leading zeros are tolerated ('02/2' == 2/2); huge counts are arbitrary-
    precision via int()."""
    count = _COUNT_RE.search(segment)
    complete = bool(count) and int(count.group(1)) > 0 and int(count.group(1)) == int(count.group(2))
    if _PASS_TOKEN_RE.search(segment) and complete and not _FAIL_TOKEN_RE.search(segment):
        return "PASS"
    if _FAIL_TOKEN_RE.search(segment):
        return "FAIL"
    return None


def _classify_mode(cells: list, mode: str):
    """Classify a single RUNBOOK row's record for `mode`:
    'PASS' | 'FAIL' | 'DEFERRED' | None.
    DEFERRED requires an EXPLICIT deferral: the verdict cell is 'n/a' (or says
    'deferred') AND the row's label cell (1st column) names THIS mode (the 926
    shape '_deferred — tmux …'). A FAIL is NEVER deferred; an ABSENT mode (no
    verdict segment, not labelled deferred) is NEVER deferred — that is the F-C
    close (a lone single-mode PASS does not certify the unmentioned mode)."""
    verdict_cell = cells[3] if len(cells) > 3 else ""
    status = _classify_segment(_segment_for_mode(verdict_cell, mode))
    if status is not None:
        return status
    label_low = cells[0].lower() if cells else ""
    verdict_low = verdict_cell.strip().lower()
    if ("deferred" in label_low
            and (verdict_low == "n/a" or "deferred" in verdict_low)
            and mode in label_low):
        return "DEFERRED"
    return None


# Aggregation precedence (worst-wins for safety): a recorded FAIL for a mode
# dominates a later PASS — re-runs after a fail must bump the version, never
# silently override a logged failure.
_STATUS_RANK = {None: 0, "DEFERRED": 1, "PASS": 2, "FAIL": 3}


def _merge_status(current, incoming):
    """Aggregate a mode's status across rows, keeping the worst (highest-rank)."""
    return current if _STATUS_RANK[current] >= _STATUS_RANK[incoming] else incoming

# NOTE: this hook deliberately does NOT init shared.pact_context. Earlier it
# called pact_context.init() "for parity" with sibling Bash hooks, but it never
# READS the context — so the init was avoidable IO on EVERY Bash invocation (it
# ran before the command-match guard). Dropped entirely: the advisory needs only
# tool_input.command from stdin. Do not re-add it without a real consumer.


def _silent_allow() -> NoReturn:
    """Fail-safe exit: emit nothing blocking, exit 0."""
    print(_SUPPRESS_OUTPUT)
    sys.exit(0)


# ─── Component A — project-identity guard (self-disable fail-safe) ───────────

def _resolve_repo_root() -> Optional[Path]:
    """Resolve the repo ROOT for IDENTITY, mirroring staleness.py's resolution
    order: CLAUDE_PROJECT_DIR -> `git rev-parse --git-common-dir` parent
    (worktree-safe; the main root where the git-TRACKED marker lives in every
    checkout) -> cwd. Distinct from the diff, which is computed in the current
    worktree (Component C)."""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        return Path(project_dir)
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip()).resolve().parent
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return Path.cwd()


def _plugin_marker(root: Path) -> Optional[dict]:
    """Return the parsed plugin.json marker iff `root` is the PACT-plugin dev
    repo, else None. The marker `pact-plugin/.claude-plugin/plugin.json` is
    git-TRACKED -> present in the main root AND every worktree, absent from any
    consumer's own tree (where the plugin is installed, not sourced)."""
    marker = root / "pact-plugin" / ".claude-plugin" / "plugin.json"
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError, UnicodeDecodeError):
        # ValueError/UnicodeDecodeError are explicit fail-safe intent (JSON/
        # decode errors are ValueError subclasses; the outer net also catches).
        return None
    if isinstance(data, dict) and data.get("name") and data.get("version"):
        return data
    return None


# ─── Component C — diff + freshness ─────────────────────────────────────────

def _changed_paths() -> Optional[list[str]]:
    """Repo-relative changed paths of the current worktree branch vs its base
    (origin/main if present, else main), via merge-base (three-dot). Runs git in
    the process CWD (the worktree at `gh pr merge` time). Returns None on any
    git failure -> the caller fail-safes to silent-allow."""
    base = "main"
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", "origin/main"],
            capture_output=True, text=True, timeout=5,
        )
        if probe.returncode == 0:
            base = "origin/main"
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _has_satisfied_row(root: Path, version: str, waiver_ok: bool) -> bool:
    """True iff RUNBOOK_RUN_DATES.md certifies a complete both-modes probe for
    `version` — OR, when `waiver_ok`, a logged WAIVED row.

    Version matching is COLUMN-ANCHORED to the "Plugin version" cell (3rd column),
    never anywhere-in-line, so a different-version row that merely mentions this
    version in prose cannot false-satisfy.

    Satisfaction is decided by CROSS-ROW AGGREGATION: across ALL rows at
    `version`, each row contributes a per-mode status (PASS / FAIL / DEFERRED /
    None) read from its verdict cell (cells[3]). The version satisfies iff BOTH
    modes end up {PASS or DEFERRED} AND at least one is PASS AND neither is FAIL.
    This unifies:
      - the #924 per-mode template ("tmux PASS 2/2 · in-process PASS 2/2", both
        modes in one cell) — one row supplies both modes;
      - the legacy single-mode rows (923 "tmux 6/6 — PASS" + "in-process 6/6 —
        PASS"; 926 "in-process 4/4 — PASS") — each supplies one mode, aggregated
        across rows;
      - explicit deferral (926 "_deferred — tmux … | n/a") — a mode that cannot
        run (e.g. tmux under a custom CLAUDE_CONFIG_DIR) counts as covered.
    A PASS requires a COMPLETE count (num>0, num==denom), closing the incomplete/
    placeholder/multi-slash forge classes (F-A). A lone single-mode PASS whose
    other mode is neither PASS nor DEFERRED does NOT satisfy (F-C). PASS that
    appears only in Notes (verdict cell FAIL/n-a) does NOT satisfy (F-B,
    verdict-cell-scoped). A read failure returns False (cannot confirm a probe
    -> WARN) — the safe advisory direction."""
    runbook = root / "pact-plugin" / "tests" / "runbooks" / "RUNBOOK_RUN_DATES.md"
    try:
        text = runbook.read_text(encoding="utf-8")
    except (OSError, ValueError, UnicodeDecodeError):
        # ValueError/UnicodeDecodeError = explicit fail-safe intent (the outer
        # net also catches); a read failure must never satisfy the gate.
        return False
    version_re = re.compile(r"(?<![\d.])" + re.escape(version) + r"(?![\d.])")
    tmux_status = None
    inproc_status = None
    waived = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        # Markdown row -> cells. COLUMN-ANCHOR the version match to the 3rd cell
        # ("Plugin version"), never anywhere-in-line, so a different-version row
        # that mentions this version in its Notes prose cannot false-satisfy.
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 3 or not version_re.search(cells[2]):
            continue
        low = line.lower()
        # A `_pending` placeholder row never counts as a probe.
        if "_pending" in low:
            continue
        # A WAIVED row satisfies only a PRIMARY-not-SECONDARY change (waiver_ok);
        # it contributes nothing to per-mode status.
        if "waived" in low:
            waived = True
            continue
        # Aggregate this row's per-mode verdict into the running status.
        tmux_status = _merge_status(tmux_status, _classify_mode(cells, "tmux"))
        inproc_status = _merge_status(inproc_status, _classify_mode(cells, "in-process"))
    if waiver_ok and waived:
        return True
    # Both modes covered (PASS or DEFERRED), at least one actually PASSed, and
    # neither FAILed.
    return (
        tmux_status in ("PASS", "DEFERRED")
        and inproc_status in ("PASS", "DEFERRED")
        and "PASS" in (tmux_status, inproc_status)
    )


def _emit_warn(version: str, seam_hooks: frozenset[str]) -> NoReturn:
    """Emit a NON-BLOCKING advisory (exit 0) via the merge_guard `[security]`
    stderr precedent, then silent-allow on stdout.

    The exact platform surfacing of a PreToolUse stderr-on-exit-0 line is
    verified at runtime in the TEST-phase dogfood live-probe (this hook is its
    own first probe subject). If stderr-on-exit-0 proves not surfaced, the
    fallback is a non-deny `hookSpecificOutput` informational field — isolated
    in THIS function so the swap is a one-function change, not a main() rewrite.
    """
    seam = ", ".join(sorted(seam_hooks)) if seam_hooks else "(none — PRIMARY only)"
    print(
        f"[live-probe-gate] This branch touches the hooks/ tree but no fresh "
        f"both-modes live-probe row exists in RUNBOOK_RUN_DATES.md for version "
        f"{version or '<unknown>'}. Per the hook-infra gate, log a live-probe "
        f"(tmux mandatory; in-process real-or-faithful-synthetic) — or an "
        f"auditable WAIVED row for a non-seam (hooks/-only) change — before "
        f"closing the originating issue. Seam hooks touched: {seam}. "
        f"(Advisory — WARN only.)",
        file=sys.stderr,
    )
    print(_SUPPRESS_OUTPUT)
    sys.exit(0)


def main() -> None:
    # Outer guard catches Exception only (NOT BaseException) so the SystemExit
    # raised by _silent_allow()/_emit_warn() propagates cleanly instead of being
    # re-caught (which would double-print). Any genuine unexpected error ->
    # silent-allow (advisory must never block, never fail closed).
    try:
        try:
            input_data = json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            _silent_allow()

        if not isinstance(input_data, dict):
            _silent_allow()

        command = (input_data.get("tool_input") or {}).get("command", "")
        if not command or not any(rx.search(command) for rx in _GATE_COMMAND_RES):
            _silent_allow()  # not a merge/close command -> not our concern

        root = _resolve_repo_root()
        marker = _plugin_marker(root) if root is not None else None
        if marker is None:
            _silent_allow()  # not the PACT-plugin dev repo (consumer/hook-less)

        changed = _changed_paths()
        if not changed:
            _silent_allow()  # cannot compute diff (or empty) -> fail-safe, no WARN

        result = classify_diff(changed)
        if not result.primary:
            _silent_allow()  # PR does not touch the hooks/ tree

        version = str(marker.get("version", ""))
        if version and _has_satisfied_row(root, version, waiver_ok=result.waiver_required):
            _silent_allow()  # a fresh probe row (or logged waiver) exists for this version

        _emit_warn(version, result.seam_hooks)
    except Exception:
        _silent_allow()


if __name__ == "__main__":
    main()
