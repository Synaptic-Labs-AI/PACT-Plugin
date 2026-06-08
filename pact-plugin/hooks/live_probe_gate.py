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

# Genuine-PASS token for the RUNBOOK_RUN_DATES freshness scan. Case-SENSITIVE
# and bounded so it matches a real verdict ("PASS.", "2/2 ... PASS",
# "in-process = PASS (real)") but NOT: an unfilled "PASS/FAIL" template
# placeholder (trailing "/" excluded), "bypass" / "BYPASS" (preceding letter
# excluded), or "non-genuine-pass" (lowercase). A substring `"pass" in low`
# match would false-satisfy on all three -> a non-genuine/pending row could
# silently disarm the gate before any real probe (the recursive inert class
# this gate exists to prevent).
_PASS_TOKEN_RE = re.compile(r"(?<![A-Za-z])PASS(?![A-Za-z/])")

# pact_context is optional parity with sibling Bash hooks (warms the context
# cache). Best-effort: the advisory does not depend on it, so an import failure
# must NOT disable the hook.
try:
    import shared.pact_context as pact_context  # type: ignore
except Exception:
    pact_context = None  # type: ignore


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
    except (OSError, json.JSONDecodeError):
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
    row (tmux + in-process + PASS) OR — when `waiver_ok` — a logged WAIVED row.

    Deliberately ROBUST to the exact column layout (the per-mode row schema is
    authored concurrently in the docs stream): scans markdown table rows that
    mention the version and looks for the mode/PASS tokens. A read failure
    returns False (cannot confirm a probe -> WARN), which is the safe advisory
    direction."""
    runbook = root / "pact-plugin" / "tests" / "runbooks" / "RUNBOOK_RUN_DATES.md"
    try:
        text = runbook.read_text(encoding="utf-8")
    except OSError:
        return False
    version_re = re.compile(r"(?<![\d.])" + re.escape(version) + r"(?![\d.])")
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        if not version_re.search(line):
            continue
        low = line.lower()
        # Skip pending/template rows (belt-and-suspenders): a `_pending`
        # placeholder row must NEVER count as a satisfied probe.
        if "_pending" in low:
            continue
        if waiver_ok and "waived" in low:
            return True
        # A genuine both-mode PASS row. The PASS verdict is matched
        # case-SENSITIVELY on the ORIGINAL `line` via _PASS_TOKEN_RE so an
        # unfilled "PASS/FAIL" placeholder, "bypass", or "non-genuine-pass"
        # never false-satisfies. tmux/in-process stay substring on `low` —
        # those tokens aren't the bug.
        if "tmux" in low and "in-process" in low and _PASS_TOKEN_RE.search(line):
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

        if pact_context is not None:
            try:
                pact_context.init(input_data)
            except Exception:
                pass  # context cache is best-effort

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
