#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/bootstrap_marker_writer.py
Summary: UserPromptSubmit hook that writes the bootstrap-complete marker
         once the bootstrap ritual's pre-conditions are observable on disk
         (team config exists AND `secretary` is in members[]). Verify-and-
         refuse semantic: hook does NOT create pre-conditions; LLM ritual
         (commands/bootstrap.md Steps 1-2) still owns TeamCreate + secretary
         spawn.
Used by: hooks.json UserPromptSubmit hook (no matcher — fires every prompt)

Layer in the four-layer bootstrap gate enforcement (#401, #664). On each
user message, checks:
  - Marker already exists (valid stamp) → suppressOutput (idempotent fast path)
  - No session dir → suppressOutput (non-PACT session)
  - Team config absent OR secretary not in members[] → suppressOutput
    (verify-and-refuse: pre-conditions unmet, sibling bootstrap_prompt_gate
    owns the user-visible advisory)
  - Pre-conditions met, marker absent → atomic-write marker, suppressOutput

Pre-#664, the marker was produced by an LLM-executed Bash heredoc at the
end of commands/bootstrap.md Step 5. That coupled marker creation to the
LLM correctly executing a multi-line shell snippet — five LLM-mediated
failure modes (mistype, missing substitution, stale session-dir, skip,
rationalization-skip). Moving the writer into a hook lifts the gate
contract from "marker presence == ritual presumed" to "marker presence ==
ritual demonstrably completed" because the producer can verify the
ritual's load-bearing artifacts (team config + secretary member entry)
exist on disk before stamping.

Atomic write: tempfile.mkstemp(dir=session_dir) + os.fdopen.write +
os.replace (modeled on shared.pact_context.write_context but using
os.replace for cross-platform atomicity per modern stdlib idiom).
File mode 0o600, directory mode 0o700.

SACROSANCT: module-load failures emit an advisory `additionalContext` at
exit 0 (mirrors bootstrap_prompt_gate._emit_load_failure_advisory).
Runtime exceptions in writer logic suppressOutput at exit 0 — the gate
(bootstrap_gate.py PreToolUse) is the user-visible failure surface; if
this hook silently fails, the next prompt retries.

Input: JSON from stdin with hook_event_name, session_id, prompt, etc.
Output: JSON with hookSpecificOutput.additionalContext (load-failure case)
        or {"suppressOutput": true} (every other path)
"""

# ─── stdlib first (used by _emit_load_failure_advisory BEFORE wrapped imports) ─
import json
import sys
from typing import NoReturn


def _emit_load_failure_advisory(stage: str, error: BaseException) -> NoReturn:
    """Emit fail-closed advisory for module-load failure.

    UserPromptSubmit cannot DENY the prompt; the strongest available signal
    is `additionalContext` injection. Uses ONLY stdlib (json, sys) so it
    remains functional even when every wrapped import below fails. Audit
    anchor: hookEventName must be present in any structured output.
    """
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                f"PACT bootstrap_marker_writer {stage} failure — the hook "
                f"could not write the bootstrap marker. {type(error).__name__}: "
                f"{error}. The companion bootstrap_gate PreToolUse will "
                f"continue to deny code-editing/agent-dispatch tools "
                f"fail-closed until the marker exists."
            ),
        }
    }))
    print(
        f"Hook load error (bootstrap_marker_writer / {stage}): {error}",
        file=sys.stderr,
    )
    sys.exit(0)


# ─── fail-closed wrapper around cross-package imports ───────────────────────
try:
    import os
    import tempfile
    from pathlib import Path

    import shared.pact_context as pact_context
    from bootstrap_gate import is_marker_set
    from shared import BOOTSTRAP_MARKER_NAME
    from shared.marker_schema import (
        MARKER_MAX_BYTES,
        MARKER_SCHEMA_VERSION,
        expected_marker_signature,
    )
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_advisory("module imports", _module_load_error)


_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

_SECRETARY_NAME = "secretary"


def _team_has_secretary(team_name: str) -> bool:
    """Return True iff ~/.claude/teams/{team_name}/config.json exists and
    contains a member with name == "secretary".

    Pre-condition for marker write. Returns False on any I/O error,
    malformed JSON, or missing-secretary case (silent — the sibling
    bootstrap_prompt_gate owns the user-visible advisory).

    Distinct from shared.pact_context._lookup_agent_in_team_config: that
    helper is id-keyed (looks up by member["id"]); this one is name-keyed
    (scans for a member whose name matches the canonical secretary
    literal).
    """
    if not team_name:
        return False
    config_path = (
        Path.home() / ".claude" / "teams" / team_name / "config.json"
    )
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    members = data.get("members")
    if not isinstance(members, list):
        return False
    for member in members:
        if isinstance(member, dict) and member.get("name") == _SECRETARY_NAME:
            return True
    return False


def _read_plugin_version(plugin_root: str) -> str:
    """Return the plugin version from plugin.json, or '' on any I/O / parse
    error. Mirrors the read at bootstrap_gate.py:is_marker_set so producer
    and verifier compute the digest over the same plugin_version string.
    """
    if not plugin_root:
        return ""
    plugin_json_path = Path(plugin_root) / ".claude-plugin" / "plugin.json"
    try:
        return json.loads(
            plugin_json_path.read_text(encoding="utf-8")
        ).get("version", "")
    except (OSError, ValueError):
        return ""


def _write_marker(session_dir: Path, session_id: str, plugin_root: str,
                  plugin_version: str) -> None:
    """Atomically write the bootstrap-complete marker.

    Caller MUST have verified pre-conditions before calling. This function
    is unconditionally writing.

    Atomicity: tempfile.mkstemp same-directory + os.fdopen.write +
    os.replace. Same-directory is required for cross-FS atomicity
    (cross-FS replaces degrade to copy+unlink). File mode 0o600 (user-only
    read/write); directory mode 0o700.

    Defensive size assertion: refuse to write if the JSON payload exceeds
    MARKER_MAX_BYTES. The current schema produces ~150 bytes so the
    assertion is a no-op in practice; it documents the invariant against
    future schema growth that outpaces the verifier's size cap.
    """
    sig = expected_marker_signature(
        session_id, plugin_root, plugin_version, MARKER_SCHEMA_VERSION
    )
    payload = {"v": MARKER_SCHEMA_VERSION, "sid": session_id, "sig": sig}
    body = json.dumps(payload).encode("utf-8")

    if len(body) > MARKER_MAX_BYTES:
        raise ValueError(
            f"marker payload {len(body)} bytes exceeds "
            f"MARKER_MAX_BYTES={MARKER_MAX_BYTES}"
        )

    target = session_dir / BOOTSTRAP_MARKER_NAME
    session_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(session_dir),
        prefix=".bootstrap-complete-",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, str(target))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _try_write_marker(input_data: dict) -> None:
    """Verify pre-conditions and write marker if all are met.

    Silent on every refuse path — the sibling bootstrap_prompt_gate owns
    the user-visible "bootstrap required" advisory.
    """
    pact_context.init(input_data)

    session_dir_str = pact_context.get_session_dir()
    if not session_dir_str:
        return

    session_dir = Path(session_dir_str)

    # Idempotent fast path: marker already valid → no-op. Uses the same
    # is_marker_set predicate the gate uses, so producer and verifier
    # observe identical content invariants.
    if is_marker_set(session_dir):
        return

    # Teammate sessions don't drive bootstrap (their lead does). Skip.
    if pact_context.resolve_agent_name(input_data):
        return

    # Pre-condition: team config + secretary member exist on disk.
    team_name = pact_context.get_team_name()
    if not _team_has_secretary(team_name):
        return

    plugin_root = pact_context.get_plugin_root()
    plugin_version = _read_plugin_version(plugin_root)
    if not plugin_root or not plugin_version:
        return

    session_id = session_dir.name
    _write_marker(session_dir, session_id, plugin_root, plugin_version)


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # Malformed stdin → fail-OPEN (input-side failure is harness's domain).
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    try:
        _try_write_marker(input_data)
    except Exception:
        # Runtime fail-OPEN: the gate's deny path is the user-visible
        # failure surface. If this hook silently fails, the next prompt
        # retries. Loud advisory on producer-side bug would mislead a
        # healthy session into rebooting. Module-load failures are handled
        # separately (advisory) by the module-load wrapper above.
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    print(_SUPPRESS_OUTPUT)
    sys.exit(0)


if __name__ == "__main__":
    main()
