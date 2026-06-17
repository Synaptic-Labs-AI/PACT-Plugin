#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/bootstrap_marker_writer.py
Summary: Dual-event hook that writes the bootstrap-complete marker once the
         bootstrap ritual's pre-conditions are observable on disk (team config
         exists AND `secretary` is in members[]). Verify-and-refuse semantic:
         hook does NOT create pre-conditions; LLM ritual (commands/bootstrap.md
         Steps 1-2) still owns TeamCreate + secretary spawn.
Used by: hooks.json — registered under TWO events, both running this same
         verify-and-refuse logic (#975):
           - UserPromptSubmit (no matcher — fires every prompt): steady-state
             turn-2+ self-heal; the semantically-strongest "ritual
             demonstrably completed" surface.
           - PostToolUse matched on `Agent`: stamps the marker WITHIN the
             bootstrapping turn — fires after the secretary spawn returns and
             the platform has written `secretary` into members[], before the
             next specialist dispatch is gate-checked.
         The marker CONTENT is byte-identical regardless of which event fired
         (same _write_marker + signature SSOT); the firing event affects ONLY
         the echoed hookEventName, never the digest.

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
        or {"suppressOutput": true} (every other path). Every emit path carries
        hookSpecificOutput.hookEventName echoing the ACTUAL firing event
        (resolved from the frame's `hook_event_name` via _resolve_event_name);
        a VALID safe default ("UserPromptSubmit") is used only when the frame
        is genuinely unavailable (import-time double-failure or malformed
        stdin). A missing/stale event name is a silent platform-layer rejection
        — the AUDIT-ANCHOR this dynamic resolution exists to satisfy.
"""

from __future__ import annotations

# ─── stdlib first (used by _emit_load_failure_advisory BEFORE wrapped imports) ─
import json
import sys
from typing import NoReturn

# Safe default firing event for the structured-output emit paths. A module-load
# failure (the import-time advisory path) is independent of the firing event, so
# when the frame is genuinely unavailable the advisory falls back to this VALID
# event name — the platform accepts it, never a silent schema-validation
# rejection. It is also the historical default the pins asserted.
_DEFAULT_HOOK_EVENT = "UserPromptSubmit"


def _resolve_event_name(input_data, default: str = _DEFAULT_HOOK_EVENT) -> str:
    """Return the firing event's name from the parsed frame, else `default`.

    Returns ``input_data['hook_event_name']`` when it is a non-empty ``str``;
    otherwise (``None`` / non-dict / missing key / non-str / empty) returns
    ``default``. NEVER raises — this is the AUDIT-ANCHOR safety net: every
    structured-output emit path must carry a VALID ``hookEventName``, so this
    resolver cannot itself become a failure source. stdlib-only (no wrapped
    imports) so it is callable from ``_emit_load_failure_advisory`` even when
    those imports fail at module-load time.
    """
    try:
        event = input_data.get("hook_event_name")
    except AttributeError:
        return default
    if isinstance(event, str) and event:
        return event
    return default


def _safe_error_detail(error: BaseException) -> str:
    """Return ``"<TypeName>: <message>"`` for an exception, NEVER raising.

    A hostile exception whose ``__str__`` / ``__repr__`` raises (or a type
    whose ``__name__`` access raises) must not make the load-failure advisory
    itself raise while composing its message — that would defeat the
    fail-closed advisory's whole purpose. Each part is computed behind its own
    guard with a safe placeholder. stdlib-only (no wrapped imports) so it holds
    even when the module-load failure that triggered the advisory broke every
    wrapped import.
    """
    try:
        type_name = type(error).__name__
    except BaseException:  # noqa: BLE001 — hostile __name__; never propagate
        type_name = "UnprintableError"
    try:
        message = str(error)
    except BaseException:  # noqa: BLE001 — hostile __str__; never propagate
        message = "<error message unavailable: str(error) raised>"
    return f"{type_name}: {message}"


def _emit_load_failure_advisory(stage: str, error: BaseException) -> NoReturn:
    """Emit fail-closed advisory for module-load failure.

    UserPromptSubmit cannot DENY the prompt; the strongest available signal
    is `additionalContext` injection. Uses ONLY stdlib (json, sys) so it
    remains functional even when every wrapped import below fails. Audit
    anchor: hookEventName must be present in any structured output — and must
    be the ACTUAL firing event, so this path best-effort pre-parses stdin for
    `hook_event_name` (stdlib-only, guarded), falling back to the safe default.

    stdin single-consumption: this advisory runs ONLY from the module-load
    `except` block below, which ends in sys.exit(0) → main() never executes
    and never re-reads stdin. So this one-shot json.load(sys.stdin) is the
    sole consumer in that path. The happy path never calls this function, so
    main()'s json.load at the bottom remains its sole stdin consumer there.
    """
    try:
        _frame = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 — best-effort; any failure → safe default
        _frame = None
    event_name = _resolve_event_name(_frame)
    error_detail = _safe_error_detail(error)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": (
                f"PACT bootstrap_marker_writer {stage} failure — the hook "
                f"could not write the bootstrap marker. {error_detail}. "
                f"The companion bootstrap_gate PreToolUse will "
                f"continue to deny code-editing/agent-dispatch tools "
                f"fail-closed until the marker exists."
            ),
        }
    }))
    print(
        f"Hook load error (bootstrap_marker_writer / {stage}): {error_detail}",
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


def _suppress_output(event_name: str) -> str:
    """Return the JSON suppressOutput envelope carrying
    ``hookSpecificOutput.hookEventName == event_name``.

    Replaces the former ``_SUPPRESS_OUTPUT`` module constant so the envelope
    echoes the ACTUAL firing event (UserPromptSubmit vs PostToolUse) rather
    than a hard-coded value — a static event name under a PostToolUse fire is
    a silent schema-validation rejection at the platform layer (the
    AUDIT-ANCHOR trap). Callers pass the resolved event from
    ``_resolve_event_name``.
    """
    return json.dumps({
        "suppressOutput": True,
        "hookSpecificOutput": {"hookEventName": event_name},
    })


_SECRETARY_NAME = "secretary"


def _team_has_secretary(team_name: str) -> bool:
    """Return True iff the team config contains a member with
    ``name == "secretary"``.

    Pre-condition for marker write. Returns False silently on any I/O
    error, malformed JSON, or missing-secretary case — the sibling
    bootstrap_prompt_gate owns the user-visible advisory.

    Built on the shared ``pact_context._iter_members`` helper, so the
    JSON-shape adversarial-input semantics (missing config, malformed
    JSON, non-list members, non-dict member entries, missing keys)
    match those of the id-keyed
    ``pact_context._lookup_agent_in_team_config`` consumer
    byte-for-byte. The predicate stays distinct: this one filters on
    the member ``name`` field; the lookup filters on member ``id``.
    """
    for member in pact_context._iter_members(team_name):
        if member.get("name") == _SECRETARY_NAME:
            return True
    return False


def _debug_log_if_derived_team_dir_missing(team_name: str) -> None:
    """Best-effort, fail-safe rename-detector (#979). DEBUG-level stderr only.

    Emits a diagnostic IFF the derived team dir ``teams/{team_name}/`` is
    ABSENT *and* a sibling ``session-*`` team dir exists — the symptom of a
    FUTURE platform team-naming change (the platform wrote ``teams/<other>/``
    while ``generate_team_name`` still derives ``{team_name}``). Without this,
    such a rename would manifest as a SILENT marker-never-written deadlock.

    Distinguishes the rename symptom from the NORMAL pre-bootstrap state
    (derived dir PRESENT, secretary member simply not joined yet): the cheap
    happy path is a single ``is_dir()`` check that returns early with NO
    ``teams/``-scan. The sibling scan runs ONLY when the derived dir is absent.

    NEVER raises (every error swallowed) and NEVER alters the marker write —
    the caller has already short-circuited the write before calling this. Runs
    at PostToolUse(Agent)/UserPromptSubmit time, where the platform's team
    ``config.json`` is guaranteed written, so "derived dir absent" is a real
    rename signal here, not a SessionStart startup-window artifact.
    """
    try:
        if not team_name:
            return
        teams_dir = pact_context.get_claude_config_dir() / "teams"
        # Cheap happy path: derived dir present → not a rename → no teams/-scan.
        if (teams_dir / team_name).is_dir():
            return
        # Derived dir absent → scan for a sibling "session-*" dir (rename symptom).
        for entry in teams_dir.iterdir():
            if (entry.name != team_name
                    and entry.name.startswith("session-")
                    and (entry / "config.json").is_file()):
                print(
                    "[PACT bootstrap_marker_writer DEBUG #979] derived team "
                    f"name {team_name!r} has no platform dir, but sibling "
                    f"{entry.name!r} exists — possible platform team-naming "
                    "change; team-scoped resolution may be stale (the bootstrap "
                    "marker will not write until the names reconcile).",
                    file=sys.stderr,
                )
                return
    except Exception:
        # Fail-safe: a rename-detector must NEVER raise or block the marker path.
        return


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

    # Self-heal: re-create a MISSING context file (session_init crashed at
    # SessionStart) before reading session_dir. Total/never-raises; no-op
    # unless lead frame + valid session_id + file absent. The verify-and-
    # refuse pre-conditions below still gate the marker write — a healed
    # context never forges bootstrap completion.
    pact_context.heal_context_if_missing(input_data)

    # Trust boundary: session_dir comes from pact_context.get_session_dir(),
    # which derives it via shared.build_session_path's path-traversal guard
    # (Path.parents containment check). The writer trusts that upstream
    # validation rather than re-validating here; downstream filesystem
    # operations (mkstemp/os.replace) operate within that vetted directory.
    session_dir_str = pact_context.get_session_dir()
    if not session_dir_str:
        return

    session_dir = Path(session_dir_str)

    # Idempotent fast path: marker already valid → no-op. Uses the same
    # is_marker_set predicate the gate uses, so producer and verifier
    # observe identical content invariants.
    if is_marker_set(session_dir):
        return

    # Lead-role gate (#878): only the team-lead drives bootstrap (writes the
    # marker). This is NOT a teammate discriminator: an Agent-spawned team
    # teammate has no UserPromptSubmit-fire path (it wakes via inbox/SendMessage,
    # which is not hookable), so this event never carries a teammate frame
    # (empirically confirmed by the discriminator audit). The guard ensures a
    # plain / non-PACT primary frame (agent_type absent → is_lead False) does
    # not write the marker. Migrated from the negative
    # `resolve_agent_name(...) != ""` heuristic — which returned non-empty for
    # BOTH lead spellings, so the lead itself took this non-lead bypass branch
    # under tmux — to the positive is_lead predicate keyed on the harness-set
    # agent_type.
    if not pact_context.is_lead(input_data):
        return

    # Pre-condition: team config + secretary member exist on disk.
    team_name = pact_context.get_team_name()
    if not _team_has_secretary(team_name):
        # Best-effort, fail-safe rename-detector (#979): the secretary-absent
        # refuse is exactly where a FUTURE platform team-naming change would
        # surface as a silent marker-never-written deadlock. Emit a DEBUG
        # signal IFF the derived team dir is absent AND a sibling session-* dir
        # exists. Never raises, never alters this already-decided refuse.
        _debug_log_if_derived_team_dir_missing(team_name)
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
        # No parsed frame, so the firing event is unknown → safe default. This
        # is the only emit path where the actual event is unrecoverable.
        print(_suppress_output(_resolve_event_name(None)))
        sys.exit(0)

    # Frame parsed: resolve the actual firing event ONCE and reuse it for every
    # remaining emit path, so a PostToolUse fire emits "PostToolUse" and a
    # UserPromptSubmit fire emits "UserPromptSubmit" (AUDIT-ANCHOR).
    event = _resolve_event_name(input_data)

    try:
        _try_write_marker(input_data)
    except Exception:
        # Runtime fail-OPEN: the gate's deny path is the user-visible
        # failure surface. If this hook silently fails, the next prompt
        # retries. Loud advisory on producer-side bug would mislead a
        # healthy session into rebooting. Module-load failures are handled
        # separately (advisory) by the module-load wrapper above.
        print(_suppress_output(event))
        sys.exit(0)

    print(_suppress_output(event))
    sys.exit(0)


if __name__ == "__main__":
    main()
