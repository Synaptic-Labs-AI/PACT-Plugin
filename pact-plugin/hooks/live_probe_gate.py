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
_PASS_TOKEN_RE = re.compile(r"(?<![A-Za-z])PASS(?:ED)?(?![A-Za-z/])")

# Per-mode verdict parsing for the #924 template-instance row shape, whose
# verdict cell reads "tmux PASS|FAIL N/N · in-process PASS|FAIL N/N".
# _PER_MODE_VERDICT_RE detects that shape (a mode token immediately followed by
# a PASS/FAIL verdict); satisfaction then requires BOTH modes to be a COMPLETE
# pass (see _mode_complete_pass). Each per-mode PASS regex CAPTURES the count
# `(\d+)/(\d+)` after PASS(?:ED)?; _mode_complete_pass then requires the count to
# be numerator>0 AND numerator==denominator (all sections passed). This:
#   - rejects the WHOLE separator-placeholder class in one stroke — "PASS/FAIL",
#     "PASS|FAIL", "PASS, FAIL", "PASS FAIL", bare "PASS" (no count) all lack the
#     "N/N" form so the regex never matches;
#   - rejects an INCOMPLETE/vacuous count — "PASS 0/2" (zero), "PASS 1/2"
#     (partial), "PASS 0/0" (vacuous) — which a digit-only `\s+\d` had let through;
#   - keeps "tmux FAIL N/N · in-process PASS N/N" rejected (per-mode, both must pass).
_PER_MODE_VERDICT_RE = re.compile(r"(?:tmux|in-process)\s+(?:PASS|FAIL)")
_TMUX_PASS_RE = re.compile(r"tmux\s+PASS(?:ED)?\s+(\d+)/(\d+)")
_INPROC_PASS_RE = re.compile(r"in-process\s+PASS(?:ED)?\s+(\d+)/(\d+)")


def _mode_complete_pass(verdict_cell: str, mode_re: "re.Pattern[str]") -> bool:
    """A per-mode PASS is genuine ONLY with a COMPLETE count: PASS N/N where
    numerator > 0 AND numerator == denominator (every section passed). Rejects
    "0/2" (zero passed), "1/2" (partial), "0/0" (vacuous), and — because the
    regex requires the "N/N" shape — every separator-placeholder form."""
    match = mode_re.search(verdict_cell)
    if match is None:
        return False
    numerator, denominator = int(match.group(1)), int(match.group(2))
    return numerator > 0 and numerator == denominator

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
    """True iff RUNBOOK_RUN_DATES.md has, for `version`, EITHER a both-mode PASS
    row OR — when `waiver_ok` — a logged WAIVED row.

    Version matching is COLUMN-ANCHORED: `version` must appear in the row's
    "Plugin version" cell (the 3rd markdown column across every table in this
    ledger), NOT anywhere-in-line. Anchoring kills the false-satisfy where a
    DIFFERENT-version row merely MENTIONS this version in its prose Notes cell.

    Verdict matching is PER-MODE for the #924 template-instance row shape (the
    verdict cell reads "tmux PASS|FAIL N/N · in-process PASS|FAIL N/N"): BOTH the
    tmux verdict AND the in-process verdict must be a COMPLETE pass — PASS/PASSED
    with a count "N/N" where N>0 and numerator==denominator. This closes (a) the
    mixed FAIL/PASS false-satisfy ("tmux FAIL · in-process PASS"), (b) every
    separator placeholder (no "N/N" form: "PASS/FAIL", "PASS|FAIL", "PASS, FAIL",
    "PASS FAIL", bare "PASS"), and (c) an incomplete/vacuous count ("PASS 0/2",
    "PASS 1/2", "PASS 0/0").

    For the older single-mode "Sections passed" sections (923-missed-wake,
    926-config-dir, which predate the per-mode cell) the PASS token AND the mode
    presence are BOTH scoped to the VERDICT CELL (not the whole line): a
    target-version row whose verdict is FAIL/n-a but whose Notes prose merely
    mentions "PASS" does NOT satisfy. These sections are single-mode-per-row, so
    the legacy check requires AT LEAST ONE mode in the verdict cell plus a
    case-sensitive PASS there (requiring BOTH modes would wrongly reject the real
    single-mode PASS rows, e.g. 926's in-process-only row with tmux `_deferred`).

    A read failure returns False (cannot confirm a probe -> WARN) — the safe
    advisory direction."""
    runbook = root / "pact-plugin" / "tests" / "runbooks" / "RUNBOOK_RUN_DATES.md"
    try:
        text = runbook.read_text(encoding="utf-8")
    except (OSError, ValueError, UnicodeDecodeError):
        # ValueError/UnicodeDecodeError = explicit fail-safe intent (the outer
        # net also catches); a read failure must never satisfy the gate.
        return False
    version_re = re.compile(r"(?<![\d.])" + re.escape(version) + r"(?![\d.])")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        # Markdown row -> cells: "| a | b | c |" -> ["a", "b", "c"]. The
        # "Plugin version" column is the 3rd cell in every ledger table
        # (| Run date | Operator | Plugin version | ... |). COLUMN-ANCHOR the
        # version match THERE, not anywhere-in-line, so a different-version row
        # that mentions this version in its Notes prose cannot false-satisfy.
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 3 or not version_re.search(cells[2]):
            continue
        low = line.lower()
        # Skip pending/template rows (belt-and-suspenders): a `_pending`
        # placeholder row must NEVER count as a satisfied probe.
        if "_pending" in low:
            continue
        if waiver_ok and "waived" in low:
            return True
        verdict_cell = cells[3] if len(cells) > 3 else ""
        # PER-MODE verdict shape (the #924 template-instance): the verdict cell
        # carries an explicit per-mode verdict ("tmux PASS|FAIL N/N · in-process
        # PASS|FAIL N/N"). HARDEN: require BOTH modes to be a COMPLETE pass
        # (PASS N/N with N>0 and num==denom). This rejects "tmux FAIL N/N ·
        # in-process PASS N/N" (a mode FAILed), every separator placeholder, AND
        # an incomplete/vacuous count ("PASS 0/2", "PASS 1/2", "PASS 0/0").
        if _PER_MODE_VERDICT_RE.search(verdict_cell):
            if (_mode_complete_pass(verdict_cell, _TMUX_PASS_RE)
                    and _mode_complete_pass(verdict_cell, _INPROC_PASS_RE)):
                return True
            # A mode FAILed, is a placeholder, or has an incomplete count ->
            # this row does NOT satisfy; keep scanning (do not fall through).
            continue
        # Older / aggregate "Sections passed" shape (923-missed-wake,
        # 926-config-dir): single-mode-per-row sections that predate the per-mode
        # cell. The PASS token is scoped to the VERDICT CELL (not the whole
        # line) so a target-version row whose verdict is FAIL/n-a but whose Notes
        # prose merely mentions "PASS" does NOT satisfy (the F-B exploit). Mode
        # presence is ALSO scoped to the verdict cell, requiring AT LEAST ONE
        # mode: these sections are single-mode-per-row (923 has separate tmux /
        # in-process PASS rows; 926 has an in-process PASS row + a `_deferred`
        # tmux n/a row), so requiring BOTH modes in one row would wrongly reject
        # the real single-mode PASS rows. _PASS_TOKEN_RE stays case-sensitive so
        # a "PASS/FAIL" placeholder / "bypass" / lowercase never matches.
        verdict_low = verdict_cell.lower()
        if (("tmux" in verdict_low or "in-process" in verdict_low)
                and _PASS_TOKEN_RE.search(verdict_cell)):
            return True
    return False


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
