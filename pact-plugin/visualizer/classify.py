"""Location: pact-plugin/visualizer/classify.py
Summary: The journal-event classifier: the single table that maps a raw PACT
         journal event type to a visual category, icon and severity, then the
         per-type title/detail renderer and the bounded raw-payload trimmer.
         Also owns the canonical six-field HANDOFF projection with its legacy
         read-side aliases.
Used by: visualizer.model (event list, handoffs, findings/alerts extraction),
         visualizer.demo (scripted events are classified by the same table).

The classifier is deliberately total: an unknown event type returns None (it
is counted by the model and omitted from the response) and no renderer may
raise on a partially formed event. Plain text only — no HTML is ever produced
here, because the page renders these strings with textContent.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .journal import RawEvent
from .settings import (
    MAX_DETAIL_CHARS,
    MAX_FIELD_CHARS,
    MAX_TITLE_CHARS,
    normalize_variety,
)

# Canonical handoff fields, in the order the PACT templates teach them. Kept
# in sync by hand with hooks/shared/handoff_schema.HANDOFF_CANONICAL_FIELDS:
# the visualizer is a standalone stdlib package and must not import the hooks
# package (it is launched as `python -m visualizer` with only the plugin root
# on sys.path).
HANDOFF_CANONICAL_FIELDS: tuple[str, ...] = (
    "produced",
    "decisions",
    "reasoning_chain",
    "uncertainty",
    "integration",
    "open_questions",
)

# Mirrors hooks/shared/handoff_schema.HANDOFF_RECOMMENDED_FIELDS. The PACT
# template surfaces mark reasoning_chain recommended, not required, so a
# five-key handoff is well-formed. Never fold it into the required set: real
# handoffs omit it and the debrief badge would then report a valid record as
# incomplete.
HANDOFF_RECOMMENDED_FIELDS: tuple[str, ...] = ("reasoning_chain",)

# Derived, like the SSOT's HANDOFF_REQUIRED_FIELDS: canonical minus recommended.
HANDOFF_REQUIRED_FIELDS: tuple[str, ...] = tuple(
    field for field in HANDOFF_CANONICAL_FIELDS
    if field not in HANDOFF_RECOMMENDED_FIELDS
)

# The read-side legacy spellings the PACT protocol docs once taught. Mirrors
# hooks/shared/handoff_schema.HANDOFF_LEGACY_ALIASES; never written, only read.
HANDOFF_LEGACY_ALIASES: dict[str, str] = {
    "key_decisions": "decisions",
    "areas_of_uncertainty": "uncertainty",
    "integration_points": "integration",
}

# --- Redaction --------------------------------------------------------------

REDACTED = "[redacted]"

# Credential-shaped values. Each match is replaced whole by REDACTED.
_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"gh[opusr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}"),
    re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
    ),
)

# ``name = value`` / ``name: value`` assignments: the name stays, the value goes.
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization|access[_-]?token"
    r"|client[_-]?secret)\b(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
)

# Mapping keys whose whole value is withheld, whatever its type.
_SECRET_KEY = re.compile(
    r"(?i)token|secret|password|passwd|authorization|api[_-]?key|credential|cookie"
)


def redact(text: str) -> str:
    """Replace credential-shaped substrings of ``text`` with ``[redacted]``.

    Location: classify.redact.
    Summary: Applied to every payload string before it is bounded, so a
             truncation can never cut a secret into a shape the patterns no
             longer match. Value patterns are replaced whole; assignments
             keep the name and lose the value. Pure; never raises.
    Used by: as_text, trim_raw (every string leaf, at any depth), model
             (session-list project_dir), roster loaders through as_text.
    """
    if not isinstance(text, str) or not text:
        return text
    for pattern in _SECRET_VALUE_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return _SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)


def is_secret_key(key: object) -> bool:
    """True when a mapping key names a credential, so its value is withheld."""
    return isinstance(key, str) and bool(_SECRET_KEY.search(key))


SEVERITIES = frozenset({"info", "success", "warning", "danger", "muted"})

# type -> (category, icon, default severity). Types missing here are
# "unrecognized": counted in meta.event_count but omitted from the event feed.
EVENT_TYPE_INFO: dict[str, tuple[str, str, str]] = {
    "session_start": ("session", "session", "success"),
    "session_end": ("pr", "pr", "info"),
    "phase_transition": ("phase", "phase", "info"),
    "agent_dispatch": ("dispatch", "dispatch", "info"),
    "dispatch_decision": ("dispatch", "dispatch", "info"),
    "dispatch_site": ("dispatch", "dispatch", "info"),
    "dispatch_variety": ("variety", "variety", "info"),
    "variety_assessed": ("variety", "variety", "info"),
    "agent_handoff": ("handoff", "handoff", "success"),
    # The durable trace that the b1 mixed-store guard withheld both the
    # agent_handoff event and its snapshot. A loss is a warning, not silence.
    "handoff_emit_suppressed": ("handoff", "handoff", "warning"),
    "teachback_ack": ("teachback", "teachback", "success"),
    # The creation leg: the only journal evidence of a task once the task
    # store has been reaped, so the board survives a GC'd store.
    "task_created": ("task", "task", "info"),
    "task_metadata_snapshot": ("task", "task", "info"),
    "checkpoint": ("checkpoint", "save", "success"),
    "commit": ("commit", "commit", "success"),
    "review_dispatch": ("review", "review", "info"),
    "review_finding": ("finding", "bug", "warning"),
    "remediation": ("remediation", "bug", "warning"),
    "pr_ready": ("pr", "pr", "success"),
    "algedonic_signal": ("alert", "alert", "danger"),
    "blocker_raised": ("alert", "alert", "danger"),
    "compaction_attributed": ("compact", "compact", "muted"),
    "compaction_summary_dropped": ("compact", "compact", "warning"),
    "lifecycle_decision": ("gate", "gate", "info"),
    "gate_health": ("gate", "gate", "danger"),
    "journal_emit_skipped": ("gate", "gate", "warning"),
    "handoff_refusal_degraded": ("handoff", "handoff", "warning"),
    "team_realigned": ("session", "session", "warning"),
    "session_paused": ("session", "session", "info"),
    "session_refreshed": ("session", "session", "info"),
    "session_consolidated": ("session", "session", "muted"),
    "session_resumption_surfaced": ("session", "session", "info"),
    "session_refresh_consumed": ("session", "session", "muted"),
    "session_pause_consumed": ("session", "session", "muted"),
    "cleanup_summary": ("session", "session", "muted"),
    "s2_state_seeded": ("session", "session", "info"),
    "artifact_paths": ("task", "task", "info"),
    "missed_wake": ("wake", "wake", "warning"),
    "unflagged_background_wait": ("wake", "wake", "warning"),
    "background_stop_gate": ("gate", "gate", "muted"),
    "pin_pruned": ("memory", "raw", "info"),
    "pin_prune_skipped": ("memory", "raw", "warning"),
}

VISUAL_TYPES: frozenset[str] = frozenset(EVENT_TYPE_INFO)


def is_visual_type(event_type: object) -> bool:
    """True when the classifier knows how to render this event type.

    Location: classify.is_visual_type.
    Summary: The visibility gate: only known types become feed rows; unknown
             types are counted by the model and omitted.
    Used by: model, tests.
    """
    return isinstance(event_type, str) and event_type in VISUAL_TYPES


def event_type_of(data: Mapping[str, Any]) -> str:
    """Return the raw event's type string, or "" when absent/malformed."""
    value = data.get("type")
    return value if isinstance(value, str) else ""


def as_text(value: Any, limit: int = MAX_TITLE_CHARS) -> str:
    """Coerce a scalar JSON value to bounded plain text.

    Location: classify.as_text.
    Summary: Strings pass through (bounded); ints/floats/bools become their
             str form; containers become "" because they are not display text.
             Newlines and control characters are collapsed to spaces so a
             single event can never inject extra feed rows. Credential-shaped
             text is redacted first, before the bound is applied.
    Used by: classify renderers, model, roster, demo.
    """
    if isinstance(value, str):
        text = redact(value)
    elif isinstance(value, (int, float, bool)):
        text = str(value)
    else:
        return ""
    text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    return text[:limit]


def as_int(value: Any, default: int | None = None) -> int | None:
    """Best-effort integer coercion that rejects bools and non-numeric text."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def as_str_list(value: Any, limit: int = 40) -> list[str]:
    """Coerce a JSON array of scalars to a bounded list of plain strings."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value[:limit]:
        text = as_text(item, MAX_FIELD_CHARS)
        if text:
            out.append(text)
    return out


def trim_text(value: str, limit: int) -> str:
    """Bound a text field to exactly ``limit`` chars or fewer.

    Location: classify.trim_text.
    Summary: When truncation is needed the ellipsis is included INSIDE the
             cap (limit-1 chars + one ellipsis) so the contract's 240/500
             bounds are upper bounds on the rendered string. The renderer
             inputs are already bounded by as_text, so the ellipsis branch is
             DEFENSIVE: it fires only for a future caller that hands trim_text
             an unbounded string, not on today's classify_event path.
    Used by: classify_event.
    """
    if len(value) <= limit:
        return value
    if limit <= 1:
        return value[:limit]
    return value[: limit - 1] + "…"


def trim_raw(data: Mapping[str, Any]) -> dict:
    """Bounded copy of a raw journal event for the model's ``raw`` field.

    Location: classify.trim_raw.
    Summary: Caps strings at settings.MAX_FIELD_CHARS, containers at 40
             entries, and recursion at depth 5 so a hostile or huge event can
             never balloon the payload. Keys are preserved in insertion order.
             Every string leaf is redacted before it is capped, and the value
             of a credential-named key becomes ``[redacted]`` whatever it is.
    Used by: classify_event, model.
    """
    return _trim_mapping(data, depth=0)


def _trim_value(value: Any, depth: int) -> Any:
    if depth >= 5 and isinstance(value, (Mapping, list, tuple)):
        return "[truncated]"
    if isinstance(value, str):
        return redact(value)[:MAX_FIELD_CHARS]
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, Mapping):
        return _trim_mapping(value, depth + 1)
    if isinstance(value, (list, tuple)):
        return [_trim_value(item, depth + 1) for item in list(value)[:40]]
    return redact(str(value))[:MAX_FIELD_CHARS]


def _trim_mapping(data: Mapping[str, Any], depth: int) -> dict:
    out: dict = {}
    for index, (key, value) in enumerate(data.items()):
        if index >= 40:
            out["_truncated_keys"] = True
            break
        out[str(key)[:200]] = REDACTED if is_secret_key(key) else _trim_value(value, depth)
    return out


def _handoff_field_text(value: Any) -> str:
    """Render one handoff field value as bounded text.

    Location: classify._handoff_field_text.
    Summary: The handoff schema is presence-only, so a field may legitimately
             carry a LIST (open_questions is commonly list-shaped); as_text
             alone would coerce it to "" and a schema-valid handoff would read
             as incomplete. Lists are therefore joined with a middle dot, each
             item bounded like any other field value. An explicitly empty list
             renders "(none)": the key IS present under the presence-only
             rule, so it must count for schema completeness.
    Used by: extract_handoff_fields.
    """
    if isinstance(value, list):
        joined = " · ".join(part for part in as_str_list(value, 40) if part)
        return joined or "(none)"
    return as_text(value, MAX_FIELD_CHARS)


def extract_handoff_fields(handoff: Any) -> dict[str, str | None]:
    """Project a handoff payload onto the six canonical fields.

    Location: classify.extract_handoff_fields.
    Summary: Every canonical key is always present in the result; an absent or
             empty scalar value is None (the renderer shows a dash). The
             canonical spelling wins; a falsy canonical value falls through to
             its legacy alias so a handoff written under an old spelling still
             reads. List-valued fields (schema-valid) are joined, not dropped —
             including an explicit empty list, which renders "(none)".
    Used by: model (handoffs array), demo, tests.
    """
    result: dict[str, str | None] = {}
    source: Mapping[str, Any] = handoff if isinstance(handoff, Mapping) else {}
    for field in HANDOFF_CANONICAL_FIELDS:
        value = source.get(field)
        if isinstance(value, list):
            # Present-as-list wins over the legacy alias: the schema is
            # presence-only and the canonical key is right here.
            result[field] = _handoff_field_text(value)
            continue
        if not value:
            for alias, canonical in HANDOFF_LEGACY_ALIASES.items():
                if canonical == field:
                    value = source.get(alias)
                    break
        text = _handoff_field_text(value) if value else ""
        result[field] = text or None
    return result


def handoff_schema_complete_raw(handoff: object) -> bool:
    """True when every REQUIRED handoff key is PRESENT in the raw payload.

    Mirrors hooks/shared/handoff_schema.validate_handoff_schema exactly:
    presence only (``field in handoff``), never the value. An empty string, an
    empty list, an empty object and an explicit null are all sanctioned by the
    templates and are NOT defects; only a missing required key is. Legacy
    aliases do not count, because the SSOT validator reports an alias-only
    record as missing its canonical key.

    Input is the RAW metadata.handoff dict, never the extract_handoff_fields
    projection: the projection erases the present/absent distinction this
    predicate exists to test, and a value-based test on it was the defect that
    recurred five times. This is the ONLY completeness predicate.
    """
    if not isinstance(handoff, Mapping):
        return False
    return all(field in handoff for field in HANDOFF_REQUIRED_FIELDS)


def classify_event(
    raw: RawEvent, context: Mapping[str, Any] | None = None
) -> dict | None:
    """Classify one raw journal event into the contract's event shape.

    Location: classify.classify_event.
    Summary: Looks the type up in EVENT_TYPE_INFO (unknown -> None), renders
             title/detail from the event's own fields, derives severity, agent
             and task_id, bounds every string, and attaches a trimmed raw copy.
    Used by: model.build_model, demo, tests.
    """
    data = raw.data
    event_type = event_type_of(data)
    info = EVENT_TYPE_INFO.get(event_type)
    if info is None:
        return None
    category, icon, default_severity = info
    ctx = context or {}
    title, detail, severity = _render(event_type, data, ctx, default_severity)
    agent = _agent_name(event_type, data)
    task_id = as_text(data.get("task_id"), 200) or None
    phase = as_text(data.get("phase"), 40).upper() or None
    event = {
        "seq": raw.seq,
        "ts": as_text(data.get("ts"), 64),
        "type": event_type,
        "category": category,
        "phase": phase,
        "agent": agent,
        "task_id": task_id,
        "title": trim_text(title, MAX_TITLE_CHARS),
        "detail": trim_text(detail, MAX_DETAIL_CHARS),
        "severity": severity if severity in SEVERITIES else default_severity,
        "icon": icon,
        "raw": trim_raw(data),
    }
    if event_type == "session_end" and as_text(data.get("source"), 40) == "wrap-up":
        event["wrapup"] = True
    return event


def _agent_name(event_type: str, data: Mapping[str, Any]) -> str | None:
    """Resolve the acting agent for a raw event.

    Location: classify._agent_name.
    Summary: Most events name the actor in ``agent``; review findings name a
             ``reviewer`` and remediation names a ``fixer``; task snapshots
             name an ``owner``. All fall back through the same list so agent
             statistics do not silently under-count those streams.
    Used by: classify_event.
    """
    candidates: tuple[str, ...]
    if event_type == "review_finding":
        candidates = ("agent", "reviewer")
    elif event_type == "remediation":
        candidates = ("fixer", "agent")
    elif event_type in {"task_metadata_snapshot", "dispatch_site"}:
        candidates = ("owner", "agent")
    else:
        candidates = ("agent", "owner", "reviewer", "fixer")
    for key in candidates:
        name = as_text(data.get(key), 200)
        if name:
            return name
    return None


def _render(
    event_type: str,
    data: Mapping[str, Any],
    context: Mapping[str, Any],
    default_severity: str,
) -> tuple[str, str, str]:
    """Render (title, detail, severity) for a known event type."""
    renderer = _RENDERERS.get(event_type)
    if renderer is None:
        return event_type.replace("_", " ").title(), "", default_severity
    result = renderer(data, context, default_severity)
    return result


def _render_session_start(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    project = as_text(context.get("project"), 120) or as_text(
        _basename(data.get("project_dir")), 120
    )
    team = as_text(context.get("team"), 120) or as_text(data.get("team"), 120)
    cloud = bool(context.get("cloud")) or as_text(data.get("lead_address"), 200) == "main"
    title = f"Run started · {project}" if project else "Run started"
    parts = []
    if team:
        parts.append(f"team {team}")
    parts.append("cloud run" if cloud else "local run")
    return title, " · ".join(parts), severity


def _render_session_end(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    state = as_text(data.get("pr_state"), 20).upper()
    pr_number = as_int(data.get("pr_number"), None)
    source = as_text(data.get("source"), 40)
    if state == "MERGED":
        number = f" PR {pr_number}" if pr_number is not None else ""
        return f"SHIPPED · wrap-up recorded{number}", f"PR {pr_number} MERGED" if pr_number is not None else "MERGED", "success"
    if state:
        detail = f"PR {pr_number} {state}" if pr_number is not None else state
        return "Session ended", detail, "warning" if state == "CLOSED" else severity
    if source:
        return "Session ended", f"source {source}", severity
    return "Session ended", "", severity


def _render_phase_transition(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    phase = as_text(data.get("phase"), 40).upper() or "phase"
    status = as_text(data.get("status"), 20).lower()
    reason = as_text(data.get("skip_reason"), MAX_DETAIL_CHARS)
    if status == "completed":
        return f"{phase} completed", "", "success"
    if status == "skipped":
        return f"{phase} skipped", reason or "earned skip", "warning"
    if status in {"started", "active"}:
        return f"{phase} started", "", "info"
    return f"{phase} transition", status, severity


def _render_dispatch(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    agent = as_text(data.get("agent"), 120)
    phase = as_text(data.get("phase"), 40).upper()
    task_id = as_text(data.get("task_id"), 60)
    scope = as_list_text(data.get("scope"))
    title = f"{agent or 'agent'} dispatched to {phase or 'work'}"
    parts = []
    if task_id:
        parts.append(f"task #{task_id}")
    if scope:
        parts.append(scope)
    return title, " · ".join(parts), severity


def _render_dispatch_decision(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    decision = as_text(data.get("decision"), 40).upper() or "DECISION"
    subject = (
        as_text(data.get("name"), 120)
        or as_text(data.get("subagent_type"), 120)
        or as_text(data.get("agent"), 120)
    )
    title = f"Dispatch gate: {decision}" + (f" {subject}" if subject else "")
    parts = [
        as_text(data.get("rule"), 160),
        as_text(data.get("reason"), MAX_DETAIL_CHARS),
    ]
    resolved = as_text(data.get("resolved_team"), 120) or as_text(data.get("team_name"), 120)
    if resolved:
        parts.append(f"resolved team {resolved}")
    detail = " · ".join(part for part in parts if part)
    dangerous = decision in {"DENY", "DENIED", "BLOCK", "BLOCKED"}
    return title, detail, "danger" if dangerous else "success"


def _render_handoff(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    agent = as_text(data.get("agent"), 120) or "agent"
    task_id = as_text(data.get("task_id"), 60)
    title = f"{agent} handed off task {task_id}" if task_id else f"{agent} handed off"
    handoff = data.get("handoff")
    fields = extract_handoff_fields(handoff)
    detail = (
        fields["produced"]
        or as_text(data.get("task_subject"), MAX_DETAIL_CHARS)
        or ""
    )
    return title, detail, severity


def _render_teachback(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    agent = as_text(data.get("agent"), 120) or "teammate"
    task_id = as_text(data.get("task_id"), 60)
    detail = as_text(data.get("concern"), MAX_DETAIL_CHARS)
    if not detail and task_id:
        detail = f"task #{task_id}"
    return f"{agent} passed teachback", detail, severity


def _render_task_snapshot(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    task_id = as_text(data.get("task_id"), 60) or "?"
    subject = as_text(data.get("subject"), 160)
    owner = as_text(data.get("owner"), 120)
    title = f"Task {task_id} metadata snapshot"
    detail = " · ".join(part for part in (owner, subject) if part)
    return title, detail, severity


def _render_checkpoint(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    phase = as_text(data.get("phase"), 40).upper()
    agents = data.get("active_agents")
    count = len(agents) if isinstance(agents, list) else 0
    title = f"Save point · {phase} complete" if phase else "Save point"
    return title, f"{count} active agents", severity


def _render_commit(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    sha = as_text(data.get("sha"), 60)
    message = as_text(data.get("message"), 200)
    if sha and message:
        title = f"Commit {sha} · {message}"
    elif sha:
        title = f"Commit {sha}"
    else:
        title = "Commit"
    return title, as_text(data.get("detail"), MAX_DETAIL_CHARS), severity


def _render_review(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    pr_number = as_int(data.get("pr_number"), None)
    reviewers = as_str_list(data.get("reviewers"))
    title = f"{len(reviewers)} reviewers dispatched to PR" if reviewers else "Review dispatched"
    parts = []
    if reviewers:
        parts.append(", ".join(reviewers))
    if pr_number is not None:
        parts.append(f"PR {pr_number}")
    return title, " · ".join(parts), severity


def _render_finding(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    finding = as_text(data.get("finding"), MAX_DETAIL_CHARS) or as_text(
        data.get("detail"), MAX_DETAIL_CHARS
    )
    raw_severity = as_text(data.get("severity"), 40).lower()
    if raw_severity == "blocking":
        label, level = "Blocking", "danger"
    elif raw_severity == "suggestion":
        label, level = "Suggestion", "warning"
    elif raw_severity == "nitpick":
        label, level = "Nitpick", "info"
    else:
        label, level = raw_severity.title() or "Finding", severity
    short = finding[:80].rstrip()
    title = f"{label} · {short}" if short else label
    return title, finding, level


def _render_remediation(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    items = as_str_list(data.get("items"))
    fixer = as_text(data.get("fixer"), 120)
    cycle = as_int(data.get("cycle"), None)
    if len(items) == 1:
        title = f"Remediation: {items[0]}"
    elif cycle is not None:
        title = f"Remediation · cycle {cycle}"
    else:
        title = "Remediation"
    return title, f"fixer {fixer}" if fixer else "", severity


def _render_pr_ready(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    commits = as_int(data.get("commits"), None)
    pr_number = as_int(data.get("pr_number"), None)
    number = str(pr_number) if pr_number is not None else ""
    title = f"PR ready · {commits} commits" if commits is not None else "PR ready"
    detail = f"PR {number}" if number else as_text(data.get("pr_url"), MAX_DETAIL_CHARS)
    return title, detail, severity


def _render_alert(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    category = as_text(data.get("category"), 60).upper()
    level = as_text(data.get("level"), 20).upper()
    summary = as_text(data.get("summary"), MAX_DETAIL_CHARS) or as_text(
        data.get("detail"), MAX_DETAIL_CHARS
    )
    event_type = event_type_of(data)
    if event_type == "blocker_raised":
        phase = as_text(data.get("phase"), 40).upper()
        title = f"Blocker raised for {phase}" if phase else "Blocker raised"
        return title, summary, severity
    title = "ALGEDONIC ALERT" + (f" · {category}" if category else "")
    if level and level != "ALERT":
        title += f" · {level}"
    return title, summary, severity


def _render_compaction(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    if event_type_of(data) == "compaction_summary_dropped":
        cause = as_text(data.get("cause"), 120)
        return "Compaction summary dropped", cause, "warning"
    verdict = as_text(data.get("verdict"), 40)
    basis = as_text(data.get("basis"), 40)
    detail = " · ".join(part for part in (f"verdict {verdict}" if verdict else "", f"basis {basis}" if basis else "") if part)
    return "Context compacted", detail, severity


def _render_lifecycle(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    verdict = as_text(data.get("verdict"), 40).lower()
    rules = as_str_list(data.get("rules"), 12)
    count = as_int(data.get("advisories_count"), None)
    if count is None:
        count = len(rules)
    if verdict == "advisory" or count:
        title = f"Gate advisory · {count} rule{'s' if count != 1 else ''} flagged"
        return title, ", ".join(rules), "warning"
    tool = as_text(data.get("tool_name"), 80)
    title = f"Gate allow · {tool}" if tool else "Gate allow"
    return title, "", "muted"


def _render_gate_health(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    hook = as_text(data.get("hook"), 120) or "gate"
    stage = as_text(data.get("stage"), 80)
    error = as_text(data.get("error"), MAX_DETAIL_CHARS)
    title = f"Gate health failure · {hook}"
    detail = " · ".join(part for part in (stage, error) if part)
    return title, detail, "danger"


def _render_emit_skipped(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    skipped = as_text(data.get("skipped_type"), 120)
    cause = as_text(data.get("cause"), 120)
    detail = " · ".join(part for part in (skipped, cause) if part)
    return "Journal emit skipped", detail, "warning"


def _render_refusal(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    agent_type = as_text(data.get("agent_type"), 120) or "teammate"
    classes = as_str_list(data.get("classes"), 8)
    detail = ", ".join(classes) or as_text(data.get("detail"), MAX_DETAIL_CHARS)
    return f"HANDOFF refusal degraded · {agent_type}", detail, "warning"


def _render_realign(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    old = as_text(data.get("from"), 120)
    new = as_text(data.get("to"), 120)
    detail = f"{old} → {new}" if old or new else ""
    return "Team dir realigned", detail, "warning"


def _render_session_lifecycle(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    event_type = event_type_of(data)
    labels = {
        "session_paused": "Session paused",
        "session_refreshed": "Session refreshed",
        "session_consolidated": "Memory consolidation complete",
        "session_resumption_surfaced": "Resumption state available",
        "session_refresh_consumed": "Refresh consumed",
        "session_pause_consumed": "Pause consumed",
        "cleanup_summary": "Cleanup summary",
        "s2_state_seeded": "State seeded",
    }
    title = labels.get(event_type, "Session event")
    parts: list[str] = []
    pr_number = as_int(data.get("pr_number"), None)
    if pr_number is not None:
        parts.append(f"PR {pr_number}")
    for key in ("branch", "worktree", "workflow"):
        value = as_text(data.get(key), 160)
        if value:
            parts.append(value)
    if event_type == "cleanup_summary":
        teams = as_int(data.get("teams_reaped"), None)
        tasks = as_int(data.get("tasks_reaped"), None)
        if teams is not None or tasks is not None:
            parts.append(f"teams {teams or 0} · tasks {tasks or 0}")
    return title, " · ".join(parts), severity


def _render_artifact_paths(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    workflow = as_text(data.get("workflow"), 80) or "artifacts"
    paths = as_str_list(data.get("paths"), 10)
    title = f"Artifacts · {workflow}"
    return title, ", ".join(paths), severity


def _render_wake(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    event_type = event_type_of(data)
    if event_type == "missed_wake":
        task_id = as_text(data.get("task_id"), 60)
        agent = as_text(data.get("agent"), 120)
        since = as_text(data.get("since"), 64)
        title = f"Missed wake · task {task_id}" if task_id else "Missed wake"
        detail = " · ".join(part for part in (agent, f"since {since}" if since else "") if part)
        return title, detail, "warning"
    agent = as_text(data.get("agent"), 120) or "teammate"
    command = as_text(data.get("command"), MAX_DETAIL_CHARS)
    return f"Unflagged background wait · {agent}", command, "warning"


def _render_background_gate(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    verdict = as_text(data.get("verdict"), 60)
    running = as_int(data.get("running"), None)
    detail = f"{running} running" if running is not None else ""
    return f"Background stop gate · {verdict or 'evaluated'}", detail, severity


def _render_pin(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    event_type = event_type_of(data)
    heading = as_text(data.get("heading"), 160)
    outcome = as_text(data.get("outcome"), 80)
    if event_type == "pin_pruned":
        title = "Memory pin pruned"
        return title, heading, severity
    title = f"Pin prune skipped · {outcome}" if outcome else "Pin prune skipped"
    return title, heading, severity


def _basename(value: Any) -> str:
    """Final path component of a string, or ""."""
    text = as_text(value, 500)
    if not text:
        return ""
    return text.rstrip("/").rsplit("/", 1)[-1]


def as_list_text(value: Any) -> str:
    """Join a list of scalars into one bounded display string."""
    return ", ".join(as_str_list(value, 10))



def _render_variety(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    variety = normalize_variety(data.get("variety"))
    task_id = as_text(data.get("task_id"), 60)
    scope = as_text(data.get("scope"), 40)
    if scope == "dispatch" or task_id:
        title = f"Dispatch variety · task {task_id}" if task_id else "Dispatch variety"
    elif variety:
        title = f"Variety assessed · {variety['total']}/16"
    else:
        title = "Variety assessed"
    detail = ""
    if variety:
        detail = (
            f"novelty {variety['novelty']} · scope {variety['scope']} · "
            f"uncertainty {variety['uncertainty']} · risk {variety['risk']}"
        )
    return title, detail, severity


def _render_dispatch_variety(
    data: Mapping[str, Any], context: Mapping[str, Any], severity: str
) -> tuple[str, str, str]:
    variety = normalize_variety(data.get("variety"))
    task_id = as_text(data.get("task_id"), 60)
    title = f"Dispatch variety · task {task_id}" if task_id else "Dispatch variety"
    detail = f"{variety['total']}/16" if variety else ""
    return title, detail, severity


_RENDERERS = {
    "session_start": _render_session_start,
    "session_end": _render_session_end,
    "phase_transition": _render_phase_transition,
    "agent_dispatch": _render_dispatch,
    "dispatch_decision": _render_dispatch_decision,
    "dispatch_site": _render_dispatch,
    "dispatch_variety": _render_dispatch_variety,
    "variety_assessed": _render_variety,
    "agent_handoff": _render_handoff,
    "teachback_ack": _render_teachback,
    "task_metadata_snapshot": _render_task_snapshot,
    "checkpoint": _render_checkpoint,
    "commit": _render_commit,
    "review_dispatch": _render_review,
    "review_finding": _render_finding,
    "remediation": _render_remediation,
    "pr_ready": _render_pr_ready,
    "algedonic_signal": _render_alert,
    "blocker_raised": _render_alert,
    "compaction_attributed": _render_compaction,
    "compaction_summary_dropped": _render_compaction,
    "lifecycle_decision": _render_lifecycle,
    "gate_health": _render_gate_health,
    "journal_emit_skipped": _render_emit_skipped,
    "handoff_refusal_degraded": _render_refusal,
    "team_realigned": _render_realign,
    "session_paused": _render_session_lifecycle,
    "session_refreshed": _render_session_lifecycle,
    "session_consolidated": _render_session_lifecycle,
    "session_resumption_surfaced": _render_session_lifecycle,
    "session_refresh_consumed": _render_session_lifecycle,
    "session_pause_consumed": _render_session_lifecycle,
    "cleanup_summary": _render_session_lifecycle,
    "s2_state_seeded": _render_session_lifecycle,
    "artifact_paths": _render_artifact_paths,
    "missed_wake": _render_wake,
    "unflagged_background_wait": _render_wake,
    "background_stop_gate": _render_background_gate,
    "pin_pruned": _render_pin,
    "pin_prune_skipped": _render_pin,
}
