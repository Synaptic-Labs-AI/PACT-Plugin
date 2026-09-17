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


def postcompact_frame(agent_type, compact_summary="post-compaction summary text",
                      session_id=None):
    """A synthesized PostCompact hook-stdin frame for the gate/suppression tests.

    PostCompact frames carry ``compact_summary``; ``agent_type`` carries the
    role discriminator the is_lead gate keys on; ``session_id`` is optional —
    None omits the field, which is the DEGRADATION shape the session-scoped
    writer falls back on (#1504). The CAPTURED sibling of this builder is
    ``postcompact_lead_manual``; this one stays SYNTHESIZED. Pass
    ``agent_type=None`` for a plain frame (the field is omitted).
    """
    extra = {}
    if session_id is not None:
        extra["session_id"] = session_id
    return _frame(agent_type, hook_event_name="PostCompact",
                  compact_summary=compact_summary, **extra)


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
  "posttooluse_teammate_inprocess_bash_background": {
    "_meta": {
      "capture_method": "live in-process Agent-Teams TEAMMATE PostToolUse, captured 2026-09-11 by a temporary keys-only diagnostic in an authorized, hash-verified, immediately-reverted patch of the INSTALLED track_files.py. KEY SET IS REAL AND VERBATIM. EVERY VALUE IS SYNTHETIC.",
      "authority": "THE KEY SET AND THE VALUE TYPES. Every VALUE is synthetic — the captures were keys-and-types only by design, because the live frame carries command text, absolute paths and session identifiers, none of which may enter the repository. Do not read any value below as observed.",
      "provenance_is_split_and_it_matters": "THE TOP-LEVEL KEY SET is from my own capture (background-work-coder, 2026-09-11). THE tool_response KEY SET AND TYPES are from a SECOND capture taken by another teammate through a different instrument — my own emission recorded tool_response as a BARE BOOLEAN (present: true) and therefore could not answer the question that field later turned out to decide. That is the lesson this entry now carries twice: A BOOLEAN CANNOT BE RE-INTERROGATED LATER. When a capture is expensive and non-repeatable, record SHAPE for everything on the frame, not PRESENCE.",
      "corrected_after_commit": "This entry first shipped with tool_response as the STRING '<synthetic>' and effort likewise, where the measured tool_response is a DICT. A fixture whose purpose is to give future readers ground truth misrepresented the type of the one field that mattered, and any test built on it would have modelled tool_response as a string. Fixed. `effort`'s dict shape is INHERITED from the sibling lead capture in this same file (2026-06-06, CC 2.1.167) and was NOT measured on this frame — it is the least certain thing in this entry.",
      "tool_response_KEYS_VARY_BETWEEN_FRAMES": "The key set shown is one observed shape, NOT a fixed schema. `backgroundTaskId` appears on a run_in_background frame; a non-background frame carried `bashEditDiff` instead; `timedOutAfterMs` and `returnCodeInterpretation` each appeared on some frames and not others. ANY CODE READING tool_response MUST TREAT EVERY KEY AS OPTIONAL — `.get()` with a default, never an index and never a required-key assumption. A fixture showing one fixed key set invites exactly the wrong inference, which is why this is stated rather than left to the shape.",
      "why_it_matters": "FIRST capture of an in-process Agent-Teams TEAMMATE frame on any event in this repository. Every prior teammate fixture was synthesized-from-matrix, and the one in-process PostToolUse capture (pretooluse_teammate_inprocess_subagent) is an Agent-TOOL subagent, a different spawn mechanism.",
      "negative_result": "IDENTITY IS NOT BINDABLE ON THIS FRAME by any route that refuses a type-strip. 'agent_name' is ABSENT (no such key). 'agent_id' is PRESENT and contains NO '@'. So resolve_agent_name Steps 1 and 2 both MISS, Step 3 is structurally dead, Step 3.5 has no registry row in-process, and the value is produced by STEP 4.",
      "the_load_bearing_fact": "'agent_type' ON THIS FRAME CARRIED THE TEAMMATE'S OWN name, NOT the agentType recorded for that member in the team config (which was a 'pact-'-prefixed type). Frame agent_type and config agentType ARE DIFFERENT VALUES. Any argument that excludes a Step-4 type-strip by pointing at distinct names across same-agentType members is therefore invalid: Step 4 strips the FRAME's field, which already differs per member.",
      "quick_summary": {
        "agent_name_key_present": false,
        "agent_id_contains_at": false,
        "agent_type_is_member_name": true,
        "hook_event_name": "PostToolUse",
        "run_in_background": true,
        "tool_name": "Bash",
        "tool_response_present": true
      }
    },
    "agent_id": "0123456789abcdef",
    "agent_type": "probe-work-coder",
    "cwd": "<cwd>",
    "duration_ms": 0,
    "effort": {
      "level": "<synthetic>"
    },
    "hook_event_name": "PostToolUse",
    "permission_mode": "<synthetic>",
    "prompt_id": "<synthetic>",
    "scratchpad_dir": "<scratchpad_dir>",
    "session_id": "<session_id>",
    "tool_input": {
      "command": "<command>",
      "description": "<description>",
      "run_in_background": true
    },
    "tool_name": "Bash",
    "tool_response": {
      "backgroundTaskId": "<synthetic>",
      "interrupted": false,
      "isImage": false,
      "noOutputExpected": false,
      "stderr": "<synthetic>",
      "stdout": "<synthetic>"
    },
    "tool_use_id": "<synthetic>",
    "transcript_path": "<transcript_path>"
  },
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
  "pretooluse_teammate_tmux": {
    "_meta": {
      "capture_method": "live-session additive-settings PreToolUse dumper (Claude Code 2.1.177, cycle-2 spike)",
      "note": "tmux teammate PreToolUse: agent_type PRESENT (pact-test-engineer, a non-lead spelling -> is_lead False); session_id DISTINCT from the lead's (tmux topology); NO top-level agent_id."
    },
    "agent_type": "pact-test-engineer",
    "cwd": "<cwd>",
    "effort": {
      "level": "xhigh"
    },
    "hook_event_name": "PreToolUse",
    "permission_mode": "bypassPermissions",
    "session_id": "6f9d8c47-f03d-4422-ab9c-87fdc8408103",
    "tool_input": {
      "content": "<content>",
      "file_path": "<file_path>"
    },
    "tool_name": "Write",
    "tool_use_id": "toolu_01GfyP2GZYrdWApmbcctBmZs",
    "transcript_path": "<transcript_path>"
  },
  "pretooluse_lead_inprocess": {
    "_meta": {
      "capture_method": "live-session additive-settings PreToolUse dumper (Claude Code 2.1.177, cycle-2 spike)",
      "note": "lead PreToolUse: agent_type PRESENT (PACT:pact-orchestrator, qualified lead spelling -> is_lead True); session_id is the leadSessionId shared by the in-process subagent below (the collapse); NO top-level agent_id."
    },
    "agent_type": "PACT:pact-orchestrator",
    "cwd": "<cwd>",
    "effort": {
      "level": "xhigh"
    },
    "hook_event_name": "PreToolUse",
    "permission_mode": "bypassPermissions",
    "session_id": "9d820be0-7a77-48fb-972f-a130c5a375cc",
    "tool_input": {
      "content": "<content>",
      "file_path": "<file_path>"
    },
    "tool_name": "Write",
    "tool_use_id": "toolu_01GnQLhYGGZBGoMqC6ABzGbK",
    "transcript_path": "<transcript_path>"
  },
  "pretooluse_teammate_inprocess_subagent": {
    "_meta": {
      "capture_method": "live-session additive-settings PreToolUse dumper (Claude Code 2.1.177, cycle-2 spike)",
      "note": "in-process subagent PreToolUse: agent_type PRESENT (general-purpose); session_id EQUALS the lead's (the session_id==leadSessionId in-process collapse, previously M0-INFERRED, now CAPTURED); agent_id PRESENT (the corroborating discriminator, absent on the tmux + lead frames)."
    },
    "agent_id": "a41556261a05e62df",
    "agent_type": "general-purpose",
    "cwd": "<cwd>",
    "effort": {
      "level": "xhigh"
    },
    "hook_event_name": "PreToolUse",
    "permission_mode": "bypassPermissions",
    "session_id": "9d820be0-7a77-48fb-972f-a130c5a375cc",
    "tool_input": {
      "content": "<content>",
      "file_path": "<file_path>"
    },
    "tool_name": "Write",
    "tool_use_id": "toolu_01JUFbVry6HqG3YsKnbVhFYh",
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
  },
  "postcompact_lead_manual": {
    "_meta": {
      "capture_method": "live-session-append-only-hook-dump (PACT 4.6.44 dogfood, in-process lead session, 2026-08-26)",
      "is_1504_step0": true,
      "note": "#1504 step 0: FIRST captured PostCompact frame — a lead (PACT:pact-orchestrator) manual /compact in an in-process session. session_id IS present (closes the dagger the session-scoped writer resolution rests on); compact_summary PRESENT and non-empty; trigger and prompt_id are real fields the synthesized-from-matrix builder never carried.",
      "quick_summary": {
        "AGENT_TYPE_top_level": "PACT:pact-orchestrator",
        "compact_summary_present": true,
        "hook_event_name": "PostCompact",
        "prompt_id_present": true,
        "session_id_present": true,
        "trigger": "manual"
      }
    },
    "agent_type": "PACT:pact-orchestrator",
    "compact_summary": "<elided for fixture - 21801 chars of session summary at capture; non-empty truthy string is the load-bearing shape>",
    "cwd": "<cwd>",
    "hook_event_name": "PostCompact",
    "prompt_id": "e4aa375e-3347-4abc-8d19-0e4e2c19f3b9",
    "session_id": "f5897740-e23f-4868-8d76-cc85c7e893f7",
    "transcript_path": "<transcript_path>",
    "trigger": "manual"
  },
  "compaction_teammate_precompact": {"_meta": {"capture_method": "live hook heartbeat, capture lead started with --agent PACT:pact-orchestrator, in-process teammates, 2026-09-14", "note": "An in-process haiku teammate auto-compacted. Its frames are lead-shaped: the lead agent_type, session_id and transcript_path, and no agent_id, agent_name or agent_transcript_path. Paths and prompt_id are placeholders; compact_summary is a synthetic stand-in of the same shape, not the captured text."}, "agent_type": "PACT:pact-orchestrator", "cwd": "<cwd>", "prompt_id": "<prompt_id>", "scratchpad_dir": "<scratchpad_dir>", "session_id": "4ec31948-bbe5-4ef4-841c-631d1ef31e61", "transcript_path": "<transcript_path>", "custom_instructions": null, "hook_event_name": "PreCompact", "trigger": "auto"},
  "compaction_teammate_sessionstart": {"_meta": {"capture_method": "live hook heartbeat, capture lead started with --agent PACT:pact-orchestrator, in-process teammates, 2026-09-14", "note": "An in-process haiku teammate auto-compacted. Its frames are lead-shaped: the lead agent_type, session_id and transcript_path, and no agent_id, agent_name or agent_transcript_path. Paths and prompt_id are placeholders; compact_summary is a synthetic stand-in of the same shape, not the captured text."}, "agent_type": "PACT:pact-orchestrator", "cwd": "<cwd>", "prompt_id": "<prompt_id>", "scratchpad_dir": "<scratchpad_dir>", "session_id": "4ec31948-bbe5-4ef4-841c-631d1ef31e61", "transcript_path": "<transcript_path>", "hook_event_name": "SessionStart", "model": "claude-haiku-4-5-20251001", "source": "compact"},
  "compaction_teammate_postcompact": {"_meta": {"capture_method": "live hook heartbeat, capture lead started with --agent PACT:pact-orchestrator, in-process teammates, 2026-09-14", "note": "An in-process haiku teammate auto-compacted. Its frames are lead-shaped: the lead agent_type, session_id and transcript_path, and no agent_id, agent_name or agent_transcript_path. Paths and prompt_id are placeholders; compact_summary is a synthetic stand-in of the same shape, not the captured text."}, "agent_type": "PACT:pact-orchestrator", "cwd": "<cwd>", "prompt_id": "<prompt_id>", "scratchpad_dir": "<scratchpad_dir>", "session_id": "4ec31948-bbe5-4ef4-841c-631d1ef31e61", "transcript_path": "<transcript_path>", "compact_summary": "<analysis>\nThe conversation so far: a file-reading task.\n</analysis>\n\n<summary>\n1. Primary Request and Intent: the teammate was asked to read three short text files in a scratch project and report their line counts to the team-lead.\n2. Key Technical Concepts: reading files, counting lines, reporting through SendMessage.\n3. Current Work: the three counts were gathered and sent.\n4. Pending Tasks: none.\n</summary>", "hook_event_name": "PostCompact", "trigger": "auto"},
  "compaction_lead_precompact": {"_meta": {"capture_method": "live hook heartbeat, capture lead started with --agent PACT:pact-orchestrator, in-process teammates, 2026-09-14", "note": "The capture lead ran /compact. Its frames carry the same key set as the teammate compaction frames. Paths and prompt_id are placeholders; compact_summary is a synthetic stand-in of the same shape, not the captured text."}, "agent_type": "PACT:pact-orchestrator", "cwd": "<cwd>", "prompt_id": "<prompt_id>", "scratchpad_dir": "<scratchpad_dir>", "session_id": "4ec31948-bbe5-4ef4-841c-631d1ef31e61", "transcript_path": "<transcript_path>", "custom_instructions": null, "hook_event_name": "PreCompact", "trigger": "manual"},
  "compaction_lead_sessionstart": {"_meta": {"capture_method": "live hook heartbeat, capture lead started with --agent PACT:pact-orchestrator, in-process teammates, 2026-09-14", "note": "The capture lead ran /compact. Its frames carry the same key set as the teammate compaction frames. Paths and prompt_id are placeholders; compact_summary is a synthetic stand-in of the same shape, not the captured text."}, "agent_type": "PACT:pact-orchestrator", "cwd": "<cwd>", "prompt_id": "<prompt_id>", "scratchpad_dir": "<scratchpad_dir>", "session_id": "4ec31948-bbe5-4ef4-841c-631d1ef31e61", "transcript_path": "<transcript_path>", "hook_event_name": "SessionStart", "model": "claude-opus-5[1m]", "source": "compact"},
  "compaction_lead_postcompact": {"_meta": {"capture_method": "live hook heartbeat, capture lead started with --agent PACT:pact-orchestrator, in-process teammates, 2026-09-14", "note": "The capture lead ran /compact. Its frames carry the same key set as the teammate compaction frames. Paths and prompt_id are placeholders; compact_summary is a synthetic stand-in of the same shape, not the captured text."}, "agent_type": "PACT:pact-orchestrator", "cwd": "<cwd>", "prompt_id": "<prompt_id>", "scratchpad_dir": "<scratchpad_dir>", "session_id": "4ec31948-bbe5-4ef4-841c-631d1ef31e61", "transcript_path": "<transcript_path>", "compact_summary": "<analysis>\nThe conversation so far: coordinating one teammate.\n</analysis>\n\n<summary>\n1. Primary Request and Intent: the orchestrator spawned one teammate to read three short text files in a scratch project and waited for its report.\n2. Key Technical Concepts: teammate dispatch, task tracking, compaction.\n3. Current Work: the report arrived and the task was completed.\n4. Pending Tasks: none.\n</summary>", "hook_event_name": "PostCompact", "trigger": "manual"}
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


def captured_pretooluse_teammate_tmux():
    """Real tmux-teammate PreToolUse: agent_type PRESENT (pact-test-engineer);
    agent_id ABSENT; session_id DISTINCT from the lead's (tmux topology)."""
    return captured_frame("pretooluse_teammate_tmux")


def captured_pretooluse_lead_inprocess():
    """Real lead PreToolUse: qualified lead spelling; agent_id ABSENT; session_id
    is the shared leadSessionId (the in-process anchor)."""
    return captured_frame("pretooluse_lead_inprocess")


def captured_pretooluse_teammate_inprocess_subagent():
    """Real in-process subagent PreToolUse: agent_type PRESENT (general-purpose);
    agent_id PRESENT; session_id EQUALS the lead's = the session_id==leadSessionId
    collapse (was M0-inferred, now captured)."""
    return captured_frame("pretooluse_teammate_inprocess_subagent")


def captured_postcompact_lead_manual():
    """#1504 step-0 frame: FIRST captured PostCompact (lead manual /compact).

    session_id PRESENT (the session-scoped writer's resolution input — closes
    the dagger); compact_summary PRESENT and non-empty; trigger and prompt_id
    are real fields the synthesized-from-matrix builder never carried.
    """
    return captured_frame("postcompact_lead_manual")


def _captured_compaction(role, event):
    return captured_frame(f"compaction_{role}_{event}")


def captured_compaction_teammate_precompact():
    """In-process teammate PreCompact: lead-shaped, no agent_id or agent_name."""
    return _captured_compaction("teammate", "precompact")


def captured_compaction_teammate_sessionstart():
    """In-process teammate SessionStart(source: compact): lead-shaped; model is the only teammate value."""
    return _captured_compaction("teammate", "sessionstart")


def captured_compaction_teammate_postcompact():
    """In-process teammate PostCompact: lead-shaped, with a synthetic compact_summary."""
    return _captured_compaction("teammate", "postcompact")


def captured_compaction_lead_precompact():
    """Lead PreCompact from the same capture: the same key set as the teammate's."""
    return _captured_compaction("lead", "precompact")


def captured_compaction_lead_sessionstart():
    """Lead SessionStart(source: compact) from the same capture."""
    return _captured_compaction("lead", "sessionstart")


def captured_compaction_lead_postcompact():
    """Lead PostCompact from the same capture, with a synthetic compact_summary."""
    return _captured_compaction("lead", "postcompact")


def captured_posttooluse_teammate_inprocess_bash_background():
    """FIRST real in-process Agent-Teams TEAMMATE frame captured in this repo.

    PostToolUse, tool_name Bash, run_in_background true — the exact population
    a background-launch recorder would consume.

    ITS AUTHORITY IS THE KEY SET. Values are synthetic: the capture was
    keys-only by design, because the live frame carries command text, absolute
    paths and session identifiers.

    IT RECORDS A NEGATIVE RESULT, and that is the point. `agent_name` is
    absent, `agent_id` carries no `@`, and `agent_type` carries the teammate's
    own NAME rather than the `pact-`-prefixed type its team config records. So
    identity on this frame is reachable ONLY through a Step-4 type-strip.

    `agent_type` IS POLYMORPHIC BY ROLE, NOT RANDOMLY UNRELIABLE. MEASURED:
    teammate frames carried the NAME every time — three teammates, two
    independent instruments, two operators — while lead frames carry the
    agent-type spelling (`PACT:pact-orchestrator`). It is consistently a name
    for teammates and consistently a type for the lead, so a reader must not
    take "unreliable" to mean it varies per frame: it does not, and a consumer
    that validates the value against the team config's `members[]` can rely on
    that determinism per role.

    DO NOT build a write-path expectation on this fixture. It is evidence
    about frame SHAPE, not a statement that recording should or should not
    happen. Asserting "no record is written" against it would encode an
    inert mechanism as correct behaviour, which is the defect this capture
    exists to document.
    """
    return captured_frame("posttooluse_teammate_inprocess_bash_background")
