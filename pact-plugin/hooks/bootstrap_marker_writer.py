#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/bootstrap_marker_writer.py
Summary: Dual-event hook that writes the bootstrap-complete marker once the
         bootstrap ritual's pre-conditions are observable on disk (team config
         exists AND `secretary` is in members[]). Verify-and-refuse semantic:
         hook does NOT create pre-conditions; LLM ritual (commands/bootstrap.md
         Steps 1-2) still owns team resolution + secretary spawn (the platform
         provisions the session team).
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
    from shared.claude_md_manager import resolve_project_claude_md_path
    from shared.marker_schema import (
        MARKER_MAX_BYTES,
        MARKER_SCHEMA_VERSION,
        expected_marker_signature,
    )
    from shared.session_resume import update_session_info
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
    """Return True iff the secretary has joined the team — proven by EITHER the
    config.json members[] roster OR (config-less fallback) the secretary's
    inbox file.

    Pre-condition for marker write. Returns False silently on any I/O
    error, malformed JSON, or missing-secretary case — the sibling
    bootstrap_prompt_gate owns the user-visible advisory.

    PRIMARY (CLI byte-identical): the members[] check via the shared
    ``pact_context._iter_members`` helper, so the JSON-shape adversarial-input
    semantics (missing config, malformed JSON, non-list members, non-dict
    member entries, missing keys) match those of the id-keyed
    ``pact_context._lookup_agent_in_team_config`` consumer byte-for-byte. The
    predicate stays distinct: this one filters on the member ``name`` field;
    the lookup filters on member ``id``. Tried FIRST, so under CLI (config
    present) the inbox arm is never reached.

    CONFIG-LESS FALLBACK (#1019): under the Desktop / older-CLI / print
    substrate the platform creates ``teams/<full-uuid>/`` with ``inboxes/`` but
    no ``config.json`` — so members[] is empty and the marker would deadlock.
    Accept ``teams/<team_name>/inboxes/secretary.json`` as the witness.

    The inbox witnesses the secretary was DISPATCHED, not that it has joined a
    members[] roster — and that is the right signal here. PACT's bootstrap is
    ``TaskCreate -> TaskUpdate(owner) -> Agent(secretary)`` with NO pre-spawn
    ``SendMessage`` to the secretary, so the platform writes the secretary's
    inbox file only on delivery of a message to an already-dispatched
    secretary; it cannot predate the spawn within PACT's choreography. And the
    gated tools (Edit/Write/Agent) do not hard-require a LIVE secretary at
    unblock time — the one liveness contract (memory queries) is enforced by
    awaiting a ``SendMessage`` reply, not by this marker. So a dispatch-level
    witness is sufficient and correct for the marker precondition.

    Fail-safe ``False`` on any error in either arm (the inbox ``is_file`` probe
    is wrapped so an unexpected FS error degrades to the existing silent-False
    semantic).
    """
    for member in pact_context._iter_members(team_name):
        if member.get("name") == _SECRETARY_NAME:
            return True
    # Config-less fallback (#1019): the secretary's inbox file is the witness
    # when no config.json members[] roster exists. team_name is get_team_name()
    # output (path-safe by construction); the read is wrapped so any FS error
    # preserves the fail-safe False.
    try:
        teams_dir = pact_context.get_claude_config_dir() / "teams"
        secretary_inbox = (
            teams_dir / team_name / "inboxes" / f"{_SECRETARY_NAME}.json"
        )
        if secretary_inbox.is_file():
            return True
    except Exception:
        return False
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


def _write_back_aligned_team_name() -> None:
    """Self-heal the PERSISTED team name to the IDENTITY-MATCHED one (#989).

    Per-prompt, lead-gated write-back. ``get_team_name()`` resolves the REAL
    platform team via identity match (``config.json['leadSessionId']``);
    ``get_pact_context()['team_name']`` is what session_init PERSISTED at
    SessionStart. In a divergent launch context these DIFFER — session_init
    wrote the computed ``session-<id8>`` while the platform named the dir with
    the full UUID. This function reconciles the persisted record (and the
    human-readable CLAUDE.md ``- Team:`` line) to the aligned value, so the
    persisted file stops being stale and the two SessionStart writers converge.

    Fires ONLY when the aligned name is non-empty AND differs from the
    persisted name (the normal no-divergence CLI case is a clean no-op — they
    match, so this returns immediately). Caller has already lead-gated.

    NEVER raises — every error is swallowed. The marker write is the load-
    bearing action; a write-back failure must not abort it or crash the hook.

    CLAUDE.md guard (HARD requirement): the target is resolved via
    ``resolve_project_claude_md_path`` and ``exists()``-guarded BEFORE calling
    ``update_session_info``. That function's Case-0 branch CREATES a brand-new
    PACT-managed CLAUDE.md when the file is absent — which in a gitignored/
    absent worktree would MATERIALIZE a file we must never create. So when the
    CLAUDE.md is absent we SKIP the CLAUDE.md write entirely (the context-file
    write-back still happens). When present, we pass the FULL correct tuple
    (session_id / aligned team_name / session_dir / plugin_root) because
    ``update_session_info`` rewrites the WHOLE managed session block.
    """
    try:
        aligned = pact_context.get_team_name()
        if not aligned:
            return
        persisted = pact_context.get_pact_context().get("team_name", "").lower()
        if aligned == persisted:
            # Clean no-op: persisted record already matches the real team
            # (the normal in-scope CLI case, and the steady state after the
            # first reconciliation).
            return

        session_id = pact_context.get_session_id()
        session_dir = pact_context.get_session_dir()
        plugin_root = pact_context.get_plugin_root()

        # Context-file write-back: rewrite the persisted team_name to the
        # aligned value (full build+cache+persist seam; atomic, 0o600). This
        # ALWAYS runs on a divergence, independent of the CLAUDE.md branch.
        #
        # ORDERING IS INTENTIONAL — context FIRST, CLAUDE.md SECOND. The context
        # file is the SSOT every team-scoped hook reads; the CLAUDE.md '- Team:'
        # line is cosmetic (human-readable) and NO reader cross-checks it against
        # the context. The two writes are not transactional, but each is a
        # whole-file atomic op, so a crash BETWEEN them leaves the load-bearing
        # record (the context file) already correct — the worst residual is a
        # stale cosmetic CLAUDE.md line, self-healed on the next prompt. So the
        # non-atomicity is safe by construction, not an accepted risk.
        pact_context.write_context(
            aligned, session_id, pact_context.get_project_dir(), plugin_root
        )

        # CLAUDE.md write-back: exists()-guard BEFORE update_session_info so we
        # never trip its Case-0 create-on-absent branch in a worktree. Resolve
        # the project CLAUDE.md path the same way update_session_info does.
        #
        # TOCTOU NOTE (security, mitigated / out-of-scope): there is a window
        # between this exists() check and update_session_info's write. To exploit
        # it an attacker would need write access to the project dir AS THE SAME OS
        # USER running the hook — at which point the box is already compromised
        # (they could edit CLAUDE.md, the context file, or the hook itself
        # directly). update_session_info itself re-resolves + rewrites the whole
        # managed block atomically under its own logic, so the only residual is a
        # benign cosmetic write. No cross-user privilege boundary is crossed here,
        # so this is out-of-scope by the same-user trust model.
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
        if not project_dir:
            return
        target_file, _source = resolve_project_claude_md_path(project_dir)
        if not target_file.exists():
            # Absent (e.g. gitignored worktree CLAUDE.md): SKIP the CLAUDE.md
            # write. The context-file write-back above already happened; the
            # human-readable line just stays absent, which is correct here.
            return
        # Present: rewrite the whole managed session block with the aligned
        # team name + the full correct tuple.
        update_session_info(session_id, aligned, session_dir, plugin_root)
    except Exception as e:
        print(
            f"bootstrap_marker_writer: team-name write-back failed: {e}",
            file=sys.stderr,
        )


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

    # Self-heal the persisted team name to the identity-matched one (#989),
    # lead-gated like the marker write below. No-op when they already match
    # (the normal CLI case). Never raises. Done BEFORE the secretary check so
    # the check (and the marker's session_id) read the aligned team.
    _write_back_aligned_team_name()

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
