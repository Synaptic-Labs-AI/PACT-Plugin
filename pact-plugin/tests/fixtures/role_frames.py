"""Synthesized hook-stdin role frames for the lead/teammate discriminator.

These are SYNTHESIZED-from-matrix frames, NOT verbatim captured stdin: the
raw platform frames were not persisted in this session. The shape matches the
documented v4.4.0 / CC 2.1.158 capture matrix (teammate hook stdin carries
``agent_type`` on every event; ``agent_id`` / ``agent_name`` are ABSENT under
tmux; ``teammate_name`` appears only on TeammateIdle). Every frame carries a
``_meta.capture_method`` stamp so no reader mistakes them for recovered
captures. If byte-exact frames are ever needed, re-capture freshly per the
capture spec in docs/plans/hook-lead-discriminator-fix-plan.md.

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
