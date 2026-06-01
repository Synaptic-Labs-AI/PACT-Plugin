"""Synthesized hook-stdin role frames for the lead/teammate discriminator.

PROVENANCE — these are SYNTHESIZED-from-matrix frames, NOT verbatim captured
stdin: the raw platform frames were not persisted in this session. The shape
matches the documented v4.4.0 / CC 2.1.158 capture matrix (teammate hook stdin
carries ``agent_type`` on every event; ``agent_id`` / ``agent_name`` are ABSENT
under tmux; ``teammate_name`` appears only on TeammateIdle). Every frame carries
a ``_meta.capture_method`` stamp so no reader ever mistakes them for recovered
captures.

RE-CAPTURE PROCEDURE (and why it is GATED on adopting tmux):
  The actual real-frame re-capture CANNOT be done from the current in-process
  teammateMode — in-process teammates do not fire their own separate hook
  lifecycle with the tmux frame shape, so there is no genuine teammate stdin to
  capture here. Re-capture therefore stays GATED on actually adopting tmux
  teammateMode for an unattended run. When that happens, the procedure is:
    1. On a scratch branch, add a passive additive hook (PostToolUse /
       SubagentStart / PreToolUse-TaskUpdate) that appends raw ``sys.stdin`` to
       a session-id-filtered capture file — over those 3 events, to settle the
       per-event ``agent_name`` / ``agent_id`` presence under tmux.
    2. Run a real tmux PACT session (lead + ≥1 specialist teammate) so each
       process fires its own hook lifecycle and real frames land in the file.
    3. Replace these synthesized builders with the captured frames, updating
       ``_meta.capture_method`` to record the capture date + CC build.
  Until tmux is adopted, the synthesized shape (matched to the documented
  matrix) is the correct and only available source — re-capture is a follow-up,
  not a gap in this PR. See the capture spec in
  docs/plans/hook-lead-discriminator-fix-plan.md.

Consumed by test_is_lead.py (predicate truth-table) and the per-hook
suppression tests (session_init / postcompact_archive gate behavior).
"""

_CAPTURE_METHOD = "synthesized-from-matrix (v4.4.0 / CC 2.1.158); not a captured frame"


def _frame(agent_type, **extra):
    """Build a synthesized hook-stdin frame with the role-discriminator field.

    ``agent_type`` is the only field is_lead/classify_session_role read; the
    optional ``extra`` kwargs let a caller add event-specific fields
    (``hook_event_name``, ``session_id``, ``compact_summary``, …) for the
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
