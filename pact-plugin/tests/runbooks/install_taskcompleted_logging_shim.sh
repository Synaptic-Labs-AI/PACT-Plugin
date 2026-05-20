#!/bin/bash
# Install a logging-shim wrapper around BOTH TaskCompleted hooks
# (agent_handoff_emitter.py + teardown_request_emitter.py) to capture raw
# TaskCompleted stdin from the next session's hook fires. Wrapping both
# hooks lets a single fire produce two captures (sibling-emission cross-
# check); if one hook short-circuits at its own gate, the other still
# captures — robust to mid-pipeline early-exits.
#
# Capture target (per-hook subdir disambiguation):
#   /tmp/pact-hook-stdin-captures/taskcompleted/agent_handoff_emitter/{ISO_timestamp}Z-pid{N}.json
#   /tmp/pact-hook-stdin-captures/taskcompleted/teardown_request_emitter/{ISO_timestamp}Z-pid{N}.json
#
# The shim is a side-effect tee: reads stdin into a buffer, atomically
# writes the buffer to disk (`.tmp` sibling + os.rename for POSIX-atomic
# rename within the same filesystem), then replays the buffer via
# io.StringIO so the original main() consumes identical bytes. Production
# behavior is preserved; any shim error falls through to the unmodified
# hook (try/except: pass wrapper).
#
# Distinct from the PostToolUse shim: this installer uses the marker
# "PACT-PREPARER-LOGGING-SHIM-INSTALLED-TASKCOMPLETED" (not the PostToolUse
# marker), so the two installers' idempotency checks do not collide.
#
# Activates on the NEXT session start. Idempotent — re-running is a no-op
# against an already-installed shim. Uninstall by restoring each
# .preshim.bak sibling backup.
#
# Refs: #612 (logging-shim methodology), #781 (actor-discriminator capture
# campaign), #638 (atomic-write discipline lesson).
#
# Recommended workflow for the capture session:
#   1. Run this installer.
#   2. Start a fresh session.
#   3. Trigger a teammate TaskCompleted event (e.g., dispatch a teammate
#      task, wait for completion). Each TaskCompleted fire produces two
#      captures — one per wrapped hook.
#   4. Inspect /tmp/pact-hook-stdin-captures/taskcompleted/ for captures.
#   5. Confirm agent_id presence on teammate-frame fires; absence on
#      lead-frame fires.
#   6. Promote captures to fixtures under pact-plugin/tests/fixtures/wake_lifecycle/
#      (taskcompleted_lead_context_shape.json + taskcompleted_teammate_context_shape.json)
#      with hand-added _meta block (capture_method="logging-shim").
#   7. Uninstall by restoring each .preshim.bak.

set -euo pipefail

# ─── Dynamic plugin-root resolution ─────────────────────────────────────
# Same resolution method as install_logging_shim.sh (sibling). Inline
# duplication of the ~15-line block is preferred over a sourced helper to
# avoid source-path-fragility (the helper file path would depend on the
# installer's invocation CWD). Both scripts are run via absolute path from
# the orchestrator, so the duplication cost is bounded.
#
# Note on the glob pattern: the canonical on-disk dir name is `pact-plugin/PACT/`
# (uppercase PACT) per project convention. APFS on macOS is case-insensitive
# by default, so the uppercase glob matches the actual lowercase directory
# transparently.

PACT_CACHE_BASE="$HOME/.claude/plugins/cache/pact-plugin/PACT"
PACT_ROOT=""

if [[ -d "$PACT_CACHE_BASE" ]]; then
  PACT_VERSION="$(ls -1 "$PACT_CACHE_BASE" 2>/dev/null | sort -V | tail -1)"
  if [[ -n "$PACT_VERSION" && -d "$PACT_CACHE_BASE/$PACT_VERSION" ]]; then
    PACT_ROOT="$PACT_CACHE_BASE/$PACT_VERSION"
  fi
fi

if [[ -z "$PACT_ROOT" ]]; then
  echo "Unable to resolve live PACT plugin version. Aborting." >&2
  exit 1
fi

echo "Resolved plugin root: $PACT_ROOT"

# ─── Hooks to wrap ──────────────────────────────────────────────────────
# Both TaskCompleted hooks per hooks.json registration. The shim install
# loops over both; each hook gets its own .preshim.bak and per-hook capture
# subdir.

HOOKS=(
  "$PACT_ROOT/hooks/agent_handoff_emitter.py"
  "$PACT_ROOT/hooks/teardown_request_emitter.py"
)
CAPTURE_BASE="/tmp/pact-hook-stdin-captures/taskcompleted"

# Pre-flight: every targeted hook must exist on disk.
for hook in "${HOOKS[@]}"; do
  if [[ ! -f "$hook" ]]; then
    echo "Resolved hook file not found: $hook" >&2
    exit 1
  fi
done

# ─── Per-hook install loop ──────────────────────────────────────────────

ALREADY_INSTALLED_COUNT=0
NEWLY_INSTALLED_COUNT=0
# Track which hooks were newly installed this run. Post-install verification
# below runs ONLY against newly-installed hooks; already-installed-skipped
# hooks are not re-verified (their on-disk shim was verified when originally
# installed, and re-verification against a pre-existing stub-marker shim
# would false-fail because the stub may not carry the full shim block).
NEWLY_INSTALLED_HOOKS=()

for hook in "${HOOKS[@]}"; do
  hook_basename="$(basename "$hook" .py)"
  capture_dir="$CAPTURE_BASE/$hook_basename"
  backup="${hook}.preshim.bak"

  mkdir -p "$capture_dir"
  chmod 0700 "$capture_dir" || true

  # Idempotency check against the distinct TaskCompleted marker.
  if grep -q "PACT-PREPARER-LOGGING-SHIM-INSTALLED-TASKCOMPLETED" "$hook"; then
    echo "Shim already installed in $hook_basename; no-op for this hook."
    ALREADY_INSTALLED_COUNT=$((ALREADY_INSTALLED_COUNT + 1))
    continue
  fi

  cp "$hook" "$backup"

  # Insert the shim via env-var-injected Python heredoc (quoted 'PY' keeps
  # the Python body's `$` and quoted strings intact; the hook path is
  # passed in via PACT_HOOK_PATH env var).
  PACT_HOOK_PATH="$hook" PACT_CAPTURE_DIR="$capture_dir" python3 <<'PY'
import os
from pathlib import Path

hook = Path(os.environ["PACT_HOOK_PATH"])
capture_dir = os.environ["PACT_CAPTURE_DIR"]
src = hook.read_text()

shim_template = '''# PACT-PREPARER-LOGGING-SHIM-INSTALLED-TASKCOMPLETED — capture stdin to
# disk for the TaskCompleted capture campaign. Removable: restore from the
# .preshim.bak sibling backup when no longer needed. Captures every
# TaskCompleted stdin invocation to __CAPTURE_DIR_LITERAL__ WITHOUT modifying
# behavior — the original main() runs against the same stdin. The capture
# write is atomic-rename style (.tmp sibling + os.rename) to avoid
# half-written files in the capture dir.
import datetime as _shim_datetime, os as _shim_os, sys as _shim_sys
from pathlib import Path as _ShimPath
_SHIM_CAPTURE_DIR = _ShimPath(__CAPTURE_DIR_REPR__)
_SHIM_CAPTURE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
try:
    _shim_buffer = _shim_sys.stdin.read()
    _shim_ts = _shim_datetime.datetime.now(_shim_datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    _shim_final = _SHIM_CAPTURE_DIR / f"{_shim_ts}-pid{_shim_os.getpid()}.json"
    _shim_tmp = _shim_final.with_suffix(_shim_final.suffix + ".tmp")
    # Atomic-write discipline: write to .tmp sibling, then rename. POSIX
    # rename within the same filesystem is atomic; a half-written .tmp
    # leftover (writer crashed) is named so the test harness can ignore it.
    _shim_tmp.write_text(_shim_buffer)
    _shim_os.rename(str(_shim_tmp), str(_shim_final))
    # Replay the captured stdin so the original main() consumes the same payload.
    import io as _shim_io
    _shim_sys.stdin = _shim_io.StringIO(_shim_buffer)
except Exception:
    # Shim must never break the hook — fall through on any error.
    pass
# ───────── ORIGINAL HOOK BODY STARTS BELOW ─────────
'''

# Substitute the per-hook capture-dir literal into the shim block. Use
# explicit `.replace()` with sentinel placeholders rather than `.format()`
# because the shim body contains Python f-strings (`f"{_shim_ts}-..."`) and
# `.format()` would mis-parse those f-string braces as format placeholders.
# `repr()` quotes the capture_dir string safely for Python embedding.
shim = (
    shim_template
    .replace("__CAPTURE_DIR_LITERAL__", capture_dir)
    .replace("__CAPTURE_DIR_REPR__", repr(capture_dir))
)

# Insert the shim AFTER the module docstring and BEFORE the first import.
lines = src.splitlines(keepends=True)
insert_idx = 0
in_docstring = False
docstring_quote = None
for i, line in enumerate(lines):
    stripped = line.strip()
    if i == 0 and stripped.startswith("#!"):
        continue
    if not in_docstring and (stripped.startswith('"""') or stripped.startswith("'''")):
        docstring_quote = stripped[:3]
        if stripped.count(docstring_quote) >= 2 and len(stripped) > 3:
            insert_idx = i + 1
            break
        in_docstring = True
        continue
    if in_docstring:
        if docstring_quote in stripped:
            insert_idx = i + 1
            in_docstring = False
            break
        continue
    if stripped.startswith("import ") or stripped.startswith("from "):
        insert_idx = i
        break

modified = "".join(lines[:insert_idx]) + "\n" + shim + "\n" + "".join(lines[insert_idx:])
hook.write_text(modified)
print(f"Shim inserted into {hook.name} at line {insert_idx + 1}; capture dir: {capture_dir}/")
PY

  NEWLY_INSTALLED_COUNT=$((NEWLY_INSTALLED_COUNT + 1))
  NEWLY_INSTALLED_HOOKS+=("$hook")
done

# ─── Idempotency early-exit ─────────────────────────────────────────────
# When NO hooks were newly installed (all were already-installed-skipped),
# exit 0 without running post-install verification. The on-disk shim block
# was verified at original install time; re-running this script must be a
# clean no-op per the idempotency contract.

if [[ "$NEWLY_INSTALLED_COUNT" -eq 0 ]]; then
  echo "All shims already installed; no-op."
  exit 0
fi

# ─── Post-install verification ──────────────────────────────────────────
# Three assertions per newly-installed hook (so up to 6 total across both
# hooks when both are newly installed):
#   1. Marker count == 1 in the hook file.
#   2. Capture-dir literal count >= 1 in the hook file.
#   3. Per-hook capture subdir exists on disk.
# Any failure exits non-zero with descriptive stderr.

for hook in "${NEWLY_INSTALLED_HOOKS[@]}"; do
  hook_basename="$(basename "$hook" .py)"
  capture_dir="$CAPTURE_BASE/$hook_basename"

  marker_count="$(grep -c "PACT-PREPARER-LOGGING-SHIM-INSTALLED-TASKCOMPLETED" "$hook" || true)"
  if [[ "$marker_count" -ne 1 ]]; then
    echo "Verification FAILED: expected exactly 1 PACT-PREPARER-LOGGING-SHIM-INSTALLED-TASKCOMPLETED marker in $hook; got $marker_count." >&2
    exit 1
  fi

  capture_dir_count="$(grep -c "$capture_dir" "$hook" || true)"
  if [[ "$capture_dir_count" -lt 1 ]]; then
    echo "Verification FAILED: expected per-hook capture-dir literal in $hook; got $capture_dir_count occurrences." >&2
    exit 1
  fi

  if [[ ! -d "$capture_dir" ]]; then
    echo "Verification FAILED: per-hook capture subdir not created: $capture_dir" >&2
    exit 1
  fi

  echo "Verification OK ($hook_basename): marker=$marker_count, capture_dir_refs=$capture_dir_count."
done

echo "Done. Newly installed shims: $NEWLY_INSTALLED_COUNT; already-installed (skipped): $ALREADY_INSTALLED_COUNT."
echo "Activates on next session start. To uninstall each hook:"
for hook in "${HOOKS[@]}"; do
  echo "  cp ${hook}.preshim.bak $hook"
done
