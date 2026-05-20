#!/bin/bash
# DEVELOPMENT USE ONLY — NOT FOR PRODUCTION INSTALLATION.
# This installer wraps a live plugin-cache hook so the shim captures raw
# stdin from EVERY hook fire across ALL Claude Code sessions sharing the
# plugin cache (verified empirically — see #814). Captures land under
# /tmp/pact-hook-stdin-captures/ and persist until manually removed.
# Inspect captures for sensitive data + clean /tmp before sharing any
# captures externally. See #814 for safety-hardening tracking.
#
# Install a logging-shim wrapper around session_init.py (SessionStart hook)
# to capture raw SessionStart stdin from the next session's hook fires.
# The capture target is
# /tmp/pact-hook-stdin-captures/sessionstart/session_init/{ISO_timestamp}Z-pid{N}.json.
#
# The shim is a side-effect tee: reads stdin into a buffer, atomically
# writes the buffer to disk (`.tmp` sibling + os.rename for POSIX-atomic
# rename within the same filesystem), then replays the buffer via
# io.StringIO so the original main() consumes identical bytes. Production
# behavior is preserved; any shim error falls through to the unmodified
# hook (try/except: pass wrapper).
#
# Distinct from the PostToolUse + TaskCompleted shims: this installer
# uses the marker "PACT-PREPARER-LOGGING-SHIM-INSTALLED-SESSIONSTART"
# (independent idempotency namespace; the three installers' checks do
# not collide).
#
# Activates on the NEXT session start (Claude Code loads hooks at
# session-start, not on file change). Idempotent — re-running is a no-op
# against an already-installed shim. Uninstall by restoring the
# .preshim.bak sibling backup.
#
# Refs: #781 (actor-discriminator capture campaign parent PR),
# #812 (SessionStart empirical schema audit),
# #638 (atomic-write discipline lesson).
#
# Recommended workflow for the capture session:
#   1. Run this installer.
#   2. Start a fresh session (and/or spawn one-shot Explore agents to
#      produce teammate-context SessionStart fires).
#   3. Inspect /tmp/pact-hook-stdin-captures/sessionstart/session_init/
#      for captures.
#   4. Audit the captured payload for the actual discriminator field —
#      current `is_lead_at_session_start` assumes `agent_type` presence
#      on teammate-frame SessionStart fires; this campaign empirically
#      verifies whether that assumption holds (and whether SessionStart
#      even has a teammate-fire path).
#   5. Promote captures to fixtures under
#      pact-plugin/tests/fixtures/wake_lifecycle/ with hand-added _meta
#      block (capture_method="logging-shim").
#   6. Uninstall by restoring the .preshim.bak.
#   7. Before sharing any captures externally:
#      rm -rf /tmp/pact-hook-stdin-captures/

set -euo pipefail

# ─── Dynamic plugin-root resolution ─────────────────────────────────────
# Resolve the live plugin version once at install time. The running Claude
# Code session loads hooks from a versioned subdir under the plugin cache;
# the installer must target that exact version, not a hardcoded one.
#
# Resolution method (single source-of-truth, three usage sites: HOOK= shell
# variable, embedded-Python via env-var injection, and verification grep):
#   1. Glob the per-version subdirs under the plugin cache.
#   2. Pick the highest semver via `sort -V`.
#   3. Fail loudly if no version resolves.
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

HOOK="$PACT_ROOT/hooks/session_init.py"
BACKUP="${HOOK}.preshim.bak"
CAPTURE_DIR="/tmp/pact-hook-stdin-captures/sessionstart/session_init"

if [[ ! -f "$HOOK" ]]; then
  echo "Resolved hook file not found: $HOOK" >&2
  exit 1
fi

mkdir -p "$CAPTURE_DIR"
chmod 0700 "$CAPTURE_DIR" || true

# ─── Idempotency check ──────────────────────────────────────────────────
# Detect an already-installed shim and exit cleanly. The marker
# "PACT-PREPARER-LOGGING-SHIM-INSTALLED-SESSIONSTART" is the in-file
# sentinel — distinct from the PostToolUse + TaskCompleted markers.

if grep -q "PACT-PREPARER-LOGGING-SHIM-INSTALLED-SESSIONSTART" "$HOOK"; then
  echo "Shim already installed; no-op."
  exit 0
fi

# ─── Back up the unmodified hook ────────────────────────────────────────

cp "$HOOK" "$BACKUP"

# ─── Insert the shim ────────────────────────────────────────────────────
# Use env-var injection (PACT_ROOT="$PACT_ROOT" + os.environ['PACT_ROOT']
# inside the quoted heredoc) rather than shell-expansion inside the heredoc,
# so the 'PY' quoting keeps the Python body's `$` characters and string
# literals intact.

PACT_ROOT="$PACT_ROOT" python3 <<'PY'
import os
from pathlib import Path

pact_root = os.environ["PACT_ROOT"]
hook = Path(pact_root) / "hooks" / "session_init.py"
src = hook.read_text()

shim = '''# PACT-PREPARER-LOGGING-SHIM-INSTALLED-SESSIONSTART — capture stdin to
# disk for the SessionStart empirical schema audit. Removable: restore
# from the .preshim.bak sibling backup when no longer needed. Captures
# every SessionStart stdin invocation to
# /tmp/pact-hook-stdin-captures/sessionstart/session_init/ WITHOUT
# modifying behavior — the original main() runs against the same stdin.
# The capture write is atomic-rename style (.tmp sibling + os.rename) to
# avoid half-written files in the capture dir.
import datetime as _shim_datetime, os as _shim_os, sys as _shim_sys
from pathlib import Path as _ShimPath
_SHIM_CAPTURE_DIR = _ShimPath("/tmp/pact-hook-stdin-captures/sessionstart/session_init")
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

# Insert the shim AFTER the module docstring and BEFORE the first import line.
# This positions the shim such that hook stdin is captured before any of the
# hook's own imports run, while keeping the original docstring intact at the
# top of the file.
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
            # One-line docstring.
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
print(f"Shim inserted at line {insert_idx + 1}; capture dir: /tmp/pact-hook-stdin-captures/sessionstart/session_init/")
PY

# ─── Post-install verification ──────────────────────────────────────────
# Three assertions confirm the shim landed in the LIVE hook file (not a
# stale or guessed path). Any failure exits non-zero with descriptive
# stderr — the silent-zero-capture failure mode the previous installer
# enabled is the exact class this verification eliminates.

MARKER_COUNT="$(grep -c "PACT-PREPARER-LOGGING-SHIM-INSTALLED-SESSIONSTART" "$HOOK" || true)"
if [[ "$MARKER_COUNT" -ne 1 ]]; then
  echo "Verification FAILED: expected exactly 1 PACT-PREPARER-LOGGING-SHIM-INSTALLED-SESSIONSTART marker in $HOOK; got $MARKER_COUNT." >&2
  exit 1
fi

CAPTURE_DIR_COUNT="$(grep -c "/tmp/pact-hook-stdin-captures/sessionstart/session_init" "$HOOK" || true)"
if [[ "$CAPTURE_DIR_COUNT" -lt 1 ]]; then
  echo "Verification FAILED: expected capture-dir literal in $HOOK; got $CAPTURE_DIR_COUNT occurrences." >&2
  exit 1
fi

if [[ ! -d "$CAPTURE_DIR" ]]; then
  echo "Verification FAILED: capture subdir not created: $CAPTURE_DIR" >&2
  exit 1
fi

echo "Verification OK: marker=$MARKER_COUNT, capture_dir_refs=$CAPTURE_DIR_COUNT."
echo "Done. Activates on next session start. To uninstall:"
echo "  cp $BACKUP $HOOK"
