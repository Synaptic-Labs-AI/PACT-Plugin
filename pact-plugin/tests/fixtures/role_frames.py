"""Hook-stdin role frames for the lead/teammate discriminator.

This module serves TWO frame sets, kept deliberately separate by provenance:

1. SYNTHESIZED builders (``_frame`` / ``lead_frame_*`` / ``teammate_frame`` /
   ``plain_frame`` / ``postcompact_frame``) - parametric frames matched to the
   documented capture matrix (teammate hook stdin carries ``agent_type`` on
   every event; ``agent_id`` / ``agent_name`` are ABSENT under tmux;
   ``teammate_name`` appears only on TaskCompleted/TeammateIdle). Each carries
   ``_meta.capture_method = "synthesized-from-matrix ..."`` so no reader ever
   mistakes them for recovered captures. Use these when a test needs an
   arbitrary event/role shape on demand.

2. CAPTURED (real) frames (``captured_frame`` + the ``captured_*`` accessors) -
   verbatim platform stdin captured during the #812 empirical discriminator
   audit on Claude Code 2.1.167 (2026-06-06), via two additive, plugin-unmodified
   dumpers (a headless ``--settings`` dumper for SessionStart/UserPromptSubmit
   per role, and a live ``settings.local.json`` dumper for the #917 PostToolUse /
   TaskCompleted frames). Each frame carries its own ``_meta.capture_method``
   provenance. Absolute paths (``cwd`` / ``transcript_path``) are sanitized to
   ``<cwd>`` / ``<transcript_path>`` placeholders and the verbose
   ``task_description`` value is elided (it is not read by the discriminator or
   emit paths); ``task_subject`` is preserved verbatim (it is a load-bearing
   input to the emit-path ``occupant_hash``). The role-discriminator shapes
   (``agent_type`` / ``session_id`` / ``team_name`` / ``teammate_name``) are
   preserved verbatim because they are the point.

These captured frames are the committed source of ground truth for the
discriminator tests and the #917 marker-poisoning regression - the raw capture
JSONL lives under the (gitignored) ``docs/`` tree, so promoting the frames here
is what makes them available to the suite. See
``pact-plugin/hooks/shared/HOOK_STDIN_DISCRIMINATORS.md`` for the per-event
truth table these frames substantiate.

Consumed by test_is_lead.py (predicate truth-table), the per-hook suppression
tests (session_init / postcompact_archive gate behavior), and the agent_handoff
emit-path regression tests.
"""

import copy
import json


_CAPTURE_METHOD = "synthesized-from-matrix (v4.4.0 / CC 2.1.158); not a captured frame"


def _frame(agent_type, **extra):
    """Build a synthesized hook-stdin frame with the role-discriminator field.

    ``agent_type`` is the only field is_lead/classify_session_role read; the
    optional ``extra`` kwargs let a caller add event-specific fields
    (``hook_event_name``, ``session_id``, ``compact_summary``, ...) for the
    per-hook suppression tests without re-stamping provenance each time.

    Pass ``agent_type=None`` for the "unknown" / plain-frame role (the field
    is omitted entirely, matching a no-``--agent`` primary frame).
    """
    frame = {"_meta": {"capture_method": _CAPTURE_METHOD}}
    if agent_type is not None:
        frame["agent_type"] = agent_type
    frame.update(extra)
    return frame


def lead_frame_qualified(**extra):
    """Lead launched as ``--agent PACT:pact-orchestrator`` (qualified)."""
    return _frame("PACT:pact-orchestrator", **extra)


def lead_frame_unqualified(**extra):
    """Lead launched as ``--agent pact-orchestrator`` (unqualified)."""
    return _frame("pact-orchestrator", **extra)


def teammate_frame(agent_type="pact-backend-coder", **extra):
    """A PACT specialist teammate frame (agent_type present, not a lead)."""
    return _frame(agent_type, **extra)


def plain_frame(**extra):
    """A non-PACT / no-``--agent`` primary frame (agent_type absent)."""
    return _frame(None, **extra)


def postcompact_frame(agent_type, compact_summary="post-compaction summary text"):
    """A synthesized PostCompact hook-stdin frame for the #881 gate tests.

    PostCompact frames carry ``compact_summary``; ``agent_type`` carries the
    role discriminator the is_lead gate keys on. Pass ``agent_type=None`` for a
    plain frame (the field is omitted).
    """
    return _frame(agent_type, hook_event_name="PostCompact",
                  compact_summary=compact_summary)


# =============================================================================
# CAPTURED (real) frames - promoted from the #812 empirical discriminator audit
# (Claude Code 2.1.167, 2026-06-06). Provenance in each frame's _meta.
# Absolute paths sanitized to <cwd> / <transcript_path>; task_description elided
# (unread by the discriminator/emit paths); task_subject preserved (load-bearing
# occupant_hash input); role-discriminator shapes preserved verbatim. Parsed
# from a verbatim JSON blob (not hand-transcribed Python literals) so each frame
# stays byte-faithful to its capture and is trivially diffable.
# =============================================================================

_CAPTURED_FRAMES_JSON = r'''
{
  "lead_posttooluse_taskupdate_completed": {
    "_meta": {
      "capture_method": "live-session-additive-settings.local.json-dumper (Claude Code 2.1.167, 2026-06-06)",
      "is_917_diagnostic": true,
      "note": "#917 b2 side: lead PostToolUse(TaskUpdate,status=completed); tool_response has NO 'task' key so the gate disk-fallback is always taken; is_lead True",
      "quick_summary": {
        "AGENT_TYPE_top_level": "PACT:pact-orchestrator",
        "agent_id_top_level": null,
        "hook_event_name": "PostToolUse",
        "taskupdate_status": "completed",
        "team_name_top_level": null,
        "teammate_name_top_level": null,
        "tool_name": "TaskUpdate",
        "tool_response_has_task": false
      }
    },
    "agent_type": "PACT:pact-orchestrator",
    "cwd": "<cwd>",
    "duration_ms": 173,
    "effort": {
      "level": "xhigh"
    },
    "hook_event_name": "PostToolUse",
    "permission_mode": "bypassPermissions",
    "session_id": "e5e2be7d-84fb-4eb8-a932-1ca4557b4a43",
    "tool_input": {
      "status": "completed",
      "taskId": "9"
    },
    "tool_name": "TaskUpdate",
    "tool_response": {
      "statusChange": {
        "from": "pending",
        "to": "completed"
      },
      "success": true,
      "taskId": "9",
      "updatedFields": [
        "status"
      ]
    },
    "tool_use_id": "toolu_012VtRb2bERWcUFzqdTX9KEA",
    "transcript_path": "<transcript_path>"
  },
  "lead_sessionstart_qualified": {
    "_meta": {
      "capture_method": "headless-subprocess-additive-settings-dumper (Claude Code 2.1.167, 2026-06-06)",
      "note": "--agent PACT:pact-orchestrator (qualified lead spelling) -> is_lead True"
    },
    "agent_type": "PACT:pact-orchestrator",
    "cwd": "<cwd>",
    "hook_event_name": "SessionStart",
    "session_id": "fa92f1e3-756a-4c46-9704-26ae90fecda0",
    "source": "startup",
    "transcript_path": "<transcript_path>"
  },
  "lead_sessionstart_unqualified": {
    "_meta": {
      "capture_method": "headless-subprocess-additive-settings-dumper (Claude Code 2.1.167, 2026-06-06)",
      "note": "--agent pact-orchestrator (unqualified lead spelling) -> is_lead True"
    },
    "agent_type": "pact-orchestrator",
    "cwd": "<cwd>",
    "hook_event_name": "SessionStart",
    "session_id": "d09437a6-08e7-44df-9e69-5b3beb14c075",
    "source": "startup",
    "transcript_path": "<transcript_path>"
  },
  "lead_taskcompleted": {
    "_meta": {
      "capture_method": "live-session-additive-settings.local.json-dumper (Claude Code 2.1.167, 2026-06-06)",
      "is_917_diagnostic": true,
      "note": "#917 lead side: lead TaskCompleted has NO stdin team_name (cannot claim the marker from this frame; the lead's b2 PostToolUse path is the writable emitter)",
      "quick_summary": {
        "AGENT_TYPE_top_level": "PACT:pact-orchestrator",
        "agent_id_top_level": null,
        "hook_event_name": "TaskCompleted",
        "taskupdate_status": null,
        "team_name_top_level": null,
        "teammate_name_top_level": null,
        "tool_name": null,
        "tool_response_has_task": false
      }
    },
    "agent_type": "PACT:pact-orchestrator",
    "cwd": "<cwd>",
    "hook_event_name": "TaskCompleted",
    "session_id": "e5e2be7d-84fb-4eb8-a932-1ca4557b4a43",
    "task_description": "<elided for fixture - not read by the discriminator or emit paths>",
    "task_id": "10",
    "task_subject": "architect: design #917 emit-path fix + #812 AC closures",
    "transcript_path": "<transcript_path>"
  },
  "lead_userpromptsubmit_qualified": {
    "_meta": {
      "capture_method": "headless-subprocess-additive-settings-dumper (Claude Code 2.1.167, 2026-06-06)",
      "note": "qualified lead UserPromptSubmit; no 'source' field on UserPromptSubmit"
    },
    "agent_type": "PACT:pact-orchestrator",
    "cwd": "<cwd>",
    "hook_event_name": "UserPromptSubmit",
    "permission_mode": "acceptEdits",
    "prompt": "Reply with the single word: ok",
    "session_id": "fa92f1e3-756a-4c46-9704-26ae90fecda0",
    "transcript_path": "<transcript_path>"
  },
  "plain_sessionstart": {
    "_meta": {
      "capture_method": "headless-subprocess-additive-settings-dumper (Claude Code 2.1.167, 2026-06-06)",
      "note": "no --agent: agent_type ABSENT -> is_lead False / classify_session_role 'unknown'"
    },
    "cwd": "<cwd>",
    "hook_event_name": "SessionStart",
    "session_id": "b0f9c52e-8c38-44f0-a876-501ea3a27c7e",
    "source": "startup",
    "transcript_path": "<transcript_path>"
  },
  "plain_userpromptsubmit": {
    "_meta": {
      "capture_method": "headless-subprocess-additive-settings-dumper (Claude Code 2.1.167, 2026-06-06)",
      "note": "no --agent: agent_type ABSENT. NOTE real UserPromptSubmit frames carry NO 'source' field (source is SessionStart-only)"
    },
    "cwd": "<cwd>",
    "hook_event_name": "UserPromptSubmit",
    "permission_mode": "acceptEdits",
    "prompt": "Reply with the single word: ok",
    "session_id": "b0f9c52e-8c38-44f0-a876-501ea3a27c7e",
    "transcript_path": "<transcript_path>"
  },
  "teammate_sessionstart": {
    "_meta": {
      "capture_method": "headless-subprocess-additive-settings-dumper (Claude Code 2.1.167, 2026-06-06)",
      "note": "--agent pact-preparer, a headless PRIMARY (NOT an Agent-spawned team teammate): agent_type present, not a lead spelling -> is_lead False"
    },
    "agent_type": "pact-preparer",
    "cwd": "<cwd>",
    "hook_event_name": "SessionStart",
    "session_id": "6a0c8345-ff41-42da-941d-c0a2473177db",
    "source": "startup",
    "transcript_path": "<transcript_path>"
  },
  "teammate_taskcompleted": {
    "_meta": {
      "capture_method": "live-session-additive-settings.local.json-dumper (Claude Code 2.1.167, 2026-06-06)",
      "is_917_diagnostic": true,
      "note": "#917 poison side: teammate TaskCompleted carries a stdin team_name (b1 can claim the marker dir) but its session_id is foreign to the team (unpersisted teammate context) so the journal path is unwritable",
      "quick_summary": {
        "AGENT_TYPE_top_level": "pact-architect",
        "agent_id_top_level": null,
        "hook_event_name": "TaskCompleted",
        "taskupdate_status": null,
        "team_name_top_level": "pact-e5e2be7d",
        "teammate_name_top_level": "architect",
        "tool_name": null,
        "tool_response_has_task": false
      }
    },
    "agent_type": "pact-architect",
    "cwd": "<cwd>",
    "hook_event_name": "TaskCompleted",
    "permission_mode": "bypassPermissions",
    "session_id": "ce2de714-7b5c-48b5-a202-d6275bd5de47",
    "task_description": "<elided for fixture - not read by the discriminator or emit paths>",
    "task_id": "10",
    "task_subject": "architect: design #917 emit-path fix + #812 AC closures",
    "team_name": "pact-e5e2be7d",
    "teammate_name": "architect",
    "transcript_path": "<transcript_path>"
  }
}
'''

_CAPTURED_FRAMES = json.loads(_CAPTURED_FRAMES_JSON)


def captured_frame(label):
    """Return a deep copy of the captured frame stored under ``label``.

    Deep-copied so a caller mutating the returned dict cannot corrupt the
    shared module-level capture. Raises ``KeyError`` on an unknown label
    (fail-loud: a typo'd fixture name should not silently pass).
    """
    return copy.deepcopy(_CAPTURED_FRAMES[label])


def captured_plain_sessionstart():
    """Real plain SessionStart (no --agent; agent_type ABSENT)."""
    return captured_frame("plain_sessionstart")


def captured_plain_userpromptsubmit():
    """Real plain UserPromptSubmit (agent_type ABSENT; carries no 'source')."""
    return captured_frame("plain_userpromptsubmit")


def captured_teammate_sessionstart():
    """Real --agent pact-preparer SessionStart (headless primary, not a team teammate)."""
    return captured_frame("teammate_sessionstart")


def captured_lead_sessionstart_unqualified():
    """Real --agent pact-orchestrator SessionStart (unqualified lead spelling)."""
    return captured_frame("lead_sessionstart_unqualified")


def captured_lead_sessionstart_qualified():
    """Real --agent PACT:pact-orchestrator SessionStart (qualified lead spelling)."""
    return captured_frame("lead_sessionstart_qualified")


def captured_lead_userpromptsubmit_qualified():
    """Real qualified-lead UserPromptSubmit frame."""
    return captured_frame("lead_userpromptsubmit_qualified")


def captured_lead_posttooluse_taskupdate_completed():
    """#917 b2-side frame: lead PostToolUse(TaskUpdate, status=completed).

    tool_response has NO 'task' key, so the gate's disk-fallback task read is
    always taken; agent_type is the qualified lead spelling (is_lead True).
    """
    return captured_frame("lead_posttooluse_taskupdate_completed")


def captured_teammate_taskcompleted():
    """#917 poison-side frame: teammate TaskCompleted with team_name PRESENT.

    Carries a stdin team_name (b1 can claim the marker dir) while its
    session_id is foreign to the team (unpersisted teammate context) - the
    exact asymmetry that lets a non-writable b1 fire poison the marker.
    """
    return captured_frame("teammate_taskcompleted")


def captured_lead_taskcompleted():
    """#917 lead-side frame: lead TaskCompleted with NO stdin team_name."""
    return captured_frame("lead_taskcompleted")
