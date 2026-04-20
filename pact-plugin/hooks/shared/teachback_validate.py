"""
Location: pact-plugin/hooks/shared/teachback_validate.py
Summary: Content-shape validation rules for teachback_submit and
         teachback_approved per CONTENT-SCHEMAS.md §Validation Rules.
         Implements generation-shaped rubber-stamp-resistance checks:
         citation-shape regex (strict vs flexible per Q1),
         substring-inequality, token-sharing with required_scope_items,
         template-blocklist 50% density, evidence-substring grounding,
         addressed-item membership.
Used by: hooks/teachback_gate.py (#401 Commit #7 follow-up — closes
         auditor YELLOW Y2 deferral).

Rationale: Phase 1 validation MUST exercise the full rule surface so
Phase 1 observability (teachback_gate_advisory events + the Phase 2
readiness diagnostic at scripts/check_teachback_phase2_readiness.py)
produces a meaningful false-positive count. Shipping Phase 1 with only
field-presence + min-length checks would be selection-shaped
enforcement — the exact failure mode tightening plan §Generation-shaped
content tightening is designed to close.

Public API:
    validate_submit(submit, metadata, protocol_level, agent_name) -> list[FieldError]
    validate_approved(approved, submit, metadata, protocol_level, agent_name) -> list[FieldError]
    FieldError (NamedTuple): field, error, actual_value

Error shape — `FieldError`:
    field: dotted-path string ("teachback_submit.most_likely_wrong.assumption")
    error: human-readable error message
    actual_value: truncated actual value for the deny-reason template
                  (500 char cap to avoid blasting deny_reason with
                  huge strings)

All validators return [] on pass; non-empty list on fail. Teachback_gate
uses the FIRST error to populate the deny-reason template context.

SACROSANCT fail-open: every validator catches Exception and returns []
(ie. treats as pass) so a validator bug never blocks legitimate work.
That's consistent with teachback_gate.main()'s outer try/except
envelope, but belt-and-suspenders here because content validation
touches regex engines + unicode tokenization which have their own
failure surfaces.
"""

from __future__ import annotations

import re
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Universal rules
# ---------------------------------------------------------------------------

# CONTENT-SCHEMAS.md §Universal rules #1 — 10 phrases, case-insensitive
# density check via _template_density_fails.
_TEMPLATE_BLOCKLIST: tuple[str, ...] = (
    "looks good",
    "as expected",
    "no issues",
    "all clear",
    "approved",
    "proceed",
    "understood",
    "sounds good",
    "makes sense",
    "noted",
)

# CONTENT-SCHEMAS.md §Universal rules #3 — citation shape regex with
# three alternates. Strict mode (CODE/TEST phase or coder agents) passes
# only alternates 1 + 2; flexible mode passes all 3.
_CITATION_SHAPE_STRICT = re.compile(
    r"^(?:"
    r"\w[\w/.\-]*?\.\w+:\d+"                   # file.ext:linenum
    r"|"
    r"\w+(?:\.\w+)?\([^)]*\)"                  # function() or Module.function()
    r")$"
)
_CITATION_SHAPE_FLEXIBLE = re.compile(
    r"^(?:"
    r"\w[\w/.\-]*?\.\w+:\d+"                   # file.ext:linenum
    r"|"
    r"\w+(?:\.\w+)?\([^)]*\)"                  # function() or Module.function()
    r"|"
    r"(?:\w+\s){2,}\w+"                        # named-operation-with-identifiers (3+ words)
    r")$"
)

# CONTENT-SCHEMAS.md §Token-sharing check — stopwords list.
_STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "of", "to", "in", "on", "at", "by", "for", "with", "as", "from",
    "that", "this", "these", "those", "it", "its", "they", "them",
    "and", "or", "but", "not", "no", "yes", "if", "then", "else",
    # PACT-specific noise
    "task", "agent", "teammate", "lead", "orchestrator", "pact",
})

# Grounding-shape recognizer for teachback_approved.response_to_*.grounding
# (CONTENT-SCHEMAS.md row 22 + row 24). Contains `§` OR `line N` OR
# `section` OR `:N` line-number shape.
_GROUNDING_SHAPE = re.compile(r"§|line\s+\d+|section|:\d+", re.IGNORECASE)

# Coder-agent prefixes for _citation_strictness fallback.
_CODER_PREFIXES = (
    "backend-coder",
    "frontend-coder",
    "database-engineer",
    "devops-engineer",
    "n8n",
    "test-engineer",
)

# Cap actual_value in FieldError so the deny_reason template doesn't
# blast with multi-KB strings.
_ACTUAL_VALUE_CAP = 500

# Role-marker / line-terminator strip set. Matches
# `peer_inject._sanitize_agent_name` (inline re.sub) and
# `session_state._RENDER_STRIP_RE` verbatim — C0 control chars
# (0x00-0x1F), DEL (0x7F), NEL (U+0085), LINE SEPARATOR (U+2028),
# PARAGRAPH SEPARATOR (U+2029). Any deny-reason placeholder whose
# value is drawn from teammate- or lead-authored task metadata is
# passed through this filter BEFORE truncation and BEFORE str.format()
# interpolation so crafted content cannot inject a `YOUR PACT ROLE:`
# line into the teammate-visible systemMessage. Drift test in
# test_teachback_validate asserts pattern equivalence with the
# peer_inject canonical form.
_ROLE_MARKER_STRIP_RE = re.compile(r"[\x00-\x1f\x7f\u0085\u2028\u2029]")


def _strip_control_chars(value: str) -> str:
    """Remove C0 / DEL / Unicode line-terminator characters from ``value``.

    Replacement is the empty string (mirrors
    ``session_state._sanitize_member_name`` — render-context precedent,
    where stripped chars collapse without merging identifiers). This is
    the filter applied to every teammate/lead-authored string reaching
    the deny-reason rendering pathway: placeholder values in
    ``teachback_example.format_deny_reason`` and the truncated preview
    in ``FieldError.actual_value``.

    Non-string inputs pass through unchanged — callers decide whether
    to coerce to str first.
    """
    if not isinstance(value, str):
        return value
    return _ROLE_MARKER_STRIP_RE.sub("", value)


class FieldError(NamedTuple):
    """Per-field validation error surfaced to the deny_reason template."""
    field: str
    error: str
    actual_value: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Lowercase + whitespace-collapse normalization for substring and
    membership comparisons."""
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", text.strip().lower())


def _tokenize(text: str) -> list[str]:
    """Tokenize text for the token-sharing check. Splits on whitespace +
    punctuation; lowercased."""
    if not isinstance(text, str):
        return []
    # Split on any non-alphanumeric-underscore run
    raw = re.findall(r"[a-zA-Z0-9_]+", text.lower())
    return raw


def _flatten_strs(obj) -> list[str]:
    """Recursively collect all string values from a nested dict/list
    structure. Used by _evidence_grounded to build the submit text blob."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        out: list[str] = []
        for v in obj.values():
            out.extend(_flatten_strs(v))
        return out
    if isinstance(obj, list):
        out: list[str] = []
        for item in obj:
            out.extend(_flatten_strs(item))
        return out
    return []


def _template_density_fails(text: str) -> bool:
    """CONTENT-SCHEMAS.md §Universal rules #1 — non-template check.
    Returns True iff >= 50% of the case-insensitive character count is
    covered by blocklist phrases."""
    if not isinstance(text, str) or not text.strip():
        return False
    lower = text.lower()
    total = len(lower)
    if total == 0:
        return False
    blocklist_chars = 0
    for phrase in _TEMPLATE_BLOCKLIST:
        # Count non-overlapping occurrences; multiply by phrase length.
        idx = 0
        while True:
            found = lower.find(phrase, idx)
            if found < 0:
                break
            blocklist_chars += len(phrase)
            idx = found + len(phrase)
    return (blocklist_chars / total) >= 0.5


def _citation_strictness(metadata: dict, agent_name: str) -> str:
    """Return 'strict' | 'flexible' per CONTENT-SCHEMAS.md §Q1.
    Phase override first; agent-type prefix fallback second."""
    phase = metadata.get("phase", "") if isinstance(metadata, dict) else ""
    if isinstance(phase, str) and phase in ("CODE", "TEST"):
        return "strict"
    if isinstance(agent_name, str):
        lower = agent_name.lower()
        for prefix in _CODER_PREFIXES:
            if lower.startswith(prefix):
                return "strict"
    return "flexible"


def _matches_citation(text: str, strictness: str) -> bool:
    """Return True iff text matches the citation-shape regex."""
    if not isinstance(text, str):
        return False
    pattern = (
        _CITATION_SHAPE_STRICT if strictness == "strict"
        else _CITATION_SHAPE_FLEXIBLE
    )
    return bool(pattern.match(text))


def _shares_non_stopword_token(text: str, required_scope_items: list) -> bool:
    """CONTENT-SCHEMAS.md §Token-sharing check. Returns True iff `text`
    shares >= 1 non-stopword token (length >= 3) with any
    required_scope_items entry."""
    text_tokens = {t for t in _tokenize(text) if len(t) >= 3} - _STOPWORDS
    if not text_tokens:
        return False
    for item in (required_scope_items or []):
        if not isinstance(item, str):
            continue
        item_tokens = {t for t in _tokenize(item) if len(t) >= 3} - _STOPWORDS
        if text_tokens & item_tokens:
            return True
    return False


def _scanned_candidate_distinct(candidate: str, submit_assumption: str) -> bool:
    """CONTENT-SCHEMAS.md §Substring-inequality check. Returns True iff
    candidate is NOT substring-equal to submit's most_likely_wrong.assumption
    (normalized compare; either direction disqualifies)."""
    a = _normalize(candidate)
    b = _normalize(submit_assumption)
    if not a or not b:
        # Empty values don't trigger the copy-paste guard; handled by
        # min-length check instead.
        return True
    return a not in b and b not in a


def _evidence_grounded(evidence: str, submit: dict) -> bool:
    """CONTENT-SCHEMAS.md §Evidence-substring check. Returns True iff
    normalized(evidence) is a substring of the normalized concatenation
    of ALL string values in submit."""
    if not isinstance(evidence, str) or not evidence.strip():
        return True  # empty evidence handled elsewhere via min-length
    if not isinstance(submit, dict):
        return False
    blob = _normalize(" ".join(_flatten_strs(submit)))
    e = _normalize(evidence)
    if not e:
        return True
    return e in blob


def _all_addressed_valid(addressed, required_scope_items) -> list[str]:
    """CONTENT-SCHEMAS.md §addressed membership check. Returns list of
    addressed items NOT found in required_scope_items (normalized
    compare). Empty list means all valid."""
    if not isinstance(addressed, list):
        return []
    required = required_scope_items if isinstance(required_scope_items, list) else []
    required_normalized = {_normalize(i) for i in required if isinstance(i, str)}
    invalid: list[str] = []
    for item in addressed:
        if not isinstance(item, str):
            continue
        if _normalize(item) not in required_normalized:
            invalid.append(item)
    return invalid


def _truncate(value) -> str:
    """Return a truncated str representation suitable for
    FieldError.actual_value. Caps at _ACTUAL_VALUE_CAP chars.

    Strips role-marker / line-terminator characters BEFORE the length
    cap so stripped chars do not consume the truncation budget. The
    ``actual_value`` field is rendered back into a teammate-visible
    systemMessage via ``teachback_example._INVALID_SUBMIT_TEMPLATE``;
    an un-stripped newline from teammate-authored content could inject
    a fake ``YOUR PACT ROLE:`` line into that rendered output.
    """
    s = str(value) if value is not None else ""
    s = _strip_control_chars(s)
    if len(s) > _ACTUAL_VALUE_CAP:
        return s[: _ACTUAL_VALUE_CAP - 3] + "..."
    return s


# ---------------------------------------------------------------------------
# validate_submit
# ---------------------------------------------------------------------------

def _check_min_length(
    value, field: str, min_len: int, errors: list[FieldError]
) -> bool:
    """Emit a FieldError if value is not a string of >= min_len chars
    (whitespace-only strings always fail; whitespace counts toward length
    otherwise per CONTENT-SCHEMAS.md §Universal rules #2). Returns True
    iff the check passed."""
    if not isinstance(value, str):
        errors.append(FieldError(field, f"must be a string (got {type(value).__name__})",
                                  _truncate(value)))
        return False
    if not value.strip():
        errors.append(FieldError(field, "must not be empty / whitespace-only",
                                  _truncate(value)))
        return False
    if len(value) < min_len:
        errors.append(FieldError(
            field, f"min {min_len} chars (got {len(value)})",
            _truncate(value),
        ))
        return False
    return True


def _check_non_template(
    value: str, field: str, errors: list[FieldError]
) -> None:
    """Append FieldError if value exceeds 50% template-blocklist density."""
    if _template_density_fails(value):
        errors.append(FieldError(
            field,
            "template-phrase density >= 50% (rubber-stamp blocker per "
            "CONTENT-SCHEMAS.md §Universal rules #1). Rewrite with "
            "task-specific content.",
            _truncate(value),
        ))


def _check_citation(
    value, field: str, strictness: str, errors: list[FieldError]
) -> None:
    """Append FieldError if value doesn't match the citation-shape regex."""
    if not isinstance(value, str) or not _matches_citation(value, strictness):
        errors.append(FieldError(
            field,
            f"must match {strictness}-mode citation shape "
            f"(file.ext:linenum or function()"
            + (" or 3+-word named operation" if strictness == "flexible" else "")
            + ")",
            _truncate(value),
        ))


def validate_submit(
    submit,
    metadata: dict,
    protocol_level: str,
    agent_name: str = "",
) -> list[FieldError]:
    """Validate metadata.teachback_submit against CONTENT-SCHEMAS.md §A
    + §Field-level rules.

    Args:
        submit: the metadata.teachback_submit dict.
        metadata: full task metadata (for phase inference + required_scope_items).
        protocol_level: "simplified" | "full" (from
            shared.teachback_scan._protocol_level).
        agent_name: teammate name (for citation-strictness fallback).

    Returns:
        List of FieldError (empty iff all rules pass). Teachback_gate
        uses the first entry to populate deny_reason context.
    """
    errors: list[FieldError] = []
    try:
        if not isinstance(submit, dict):
            errors.append(FieldError(
                "teachback_submit",
                "must be a dict with the protocol-required fields",
                _truncate(submit),
            ))
            return errors

        required_scope_items = metadata.get("required_scope_items") if isinstance(metadata, dict) else None
        strictness = _citation_strictness(metadata or {}, agent_name)

        # Universal: understanding (both simplified + full)
        understanding = submit.get("understanding")
        if _check_min_length(understanding, "teachback_submit.understanding",
                              100, errors):
            _check_non_template(understanding, "teachback_submit.understanding",
                                  errors)

        # Universal: first_action (both simplified + full)
        first_action = submit.get("first_action")
        if not isinstance(first_action, dict):
            errors.append(FieldError(
                "teachback_submit.first_action",
                "must be a dict with 'action' and 'expected_signal' fields",
                _truncate(first_action),
            ))
        else:
            _check_citation(first_action.get("action"),
                              "teachback_submit.first_action.action",
                              strictness, errors)
            expected = first_action.get("expected_signal")
            if _check_min_length(expected,
                                  "teachback_submit.first_action.expected_signal",
                                  30, errors):
                _check_non_template(
                    expected,
                    "teachback_submit.first_action.expected_signal",
                    errors,
                )

        if protocol_level != "full":
            # Simplified protocol: stop here. Extra fields permitted
            # but not validated (per CONTENT-SCHEMAS.md §Simplified note).
            return errors

        # Full protocol: most_likely_wrong + least_confident_item

        mlw = submit.get("most_likely_wrong")
        if not isinstance(mlw, dict):
            errors.append(FieldError(
                "teachback_submit.most_likely_wrong",
                "must be a dict with 'assumption' and 'consequence' fields",
                _truncate(mlw),
            ))
        else:
            assumption = mlw.get("assumption")
            if _check_min_length(
                assumption,
                "teachback_submit.most_likely_wrong.assumption",
                40, errors,
            ):
                _check_non_template(
                    assumption,
                    "teachback_submit.most_likely_wrong.assumption",
                    errors,
                )
                # Token-sharing check
                if not _shares_non_stopword_token(assumption, required_scope_items or []):
                    errors.append(FieldError(
                        "teachback_submit.most_likely_wrong.assumption",
                        "must share >= 1 non-stopword token (length >= 3) "
                        "with one of the required_scope_items; ground your "
                        "assumption in the dispatch scope",
                        _truncate(assumption),
                    ))
            consequence = mlw.get("consequence")
            if _check_min_length(
                consequence,
                "teachback_submit.most_likely_wrong.consequence",
                40, errors,
            ):
                _check_non_template(
                    consequence,
                    "teachback_submit.most_likely_wrong.consequence",
                    errors,
                )

        lci = submit.get("least_confident_item")
        if not isinstance(lci, dict):
            errors.append(FieldError(
                "teachback_submit.least_confident_item",
                "must be a dict with 'item', 'current_plan', "
                "and 'failure_mode' fields",
                _truncate(lci),
            ))
        else:
            for sub in ("item", "current_plan", "failure_mode"):
                val = lci.get(sub)
                if _check_min_length(
                    val,
                    f"teachback_submit.least_confident_item.{sub}",
                    30, errors,
                ):
                    _check_non_template(
                        val,
                        f"teachback_submit.least_confident_item.{sub}",
                        errors,
                    )

        return errors
    except Exception:
        # Fail-open on any validator-internal exception — return the
        # errors accumulated so far (likely empty) to let the gate allow.
        return errors


# ---------------------------------------------------------------------------
# validate_approved
# ---------------------------------------------------------------------------

def validate_approved(
    approved,
    submit,
    metadata: dict,
    protocol_level: str,
    agent_name: str = "",
) -> list[FieldError]:
    """Validate metadata.teachback_approved against CONTENT-SCHEMAS.md §B
    + §Field-level rules. Cross-references `submit` for substring-
    inequality and evidence-substring checks."""
    errors: list[FieldError] = []
    try:
        if not isinstance(approved, dict):
            errors.append(FieldError(
                "teachback_approved",
                "must be a dict with the protocol-required fields",
                _truncate(approved),
            ))
            return errors

        required_scope_items = metadata.get("required_scope_items") if isinstance(metadata, dict) else None
        strictness = _citation_strictness(metadata or {}, agent_name)

        # Universal: scanned_candidate + conditions_met

        sc = approved.get("scanned_candidate")
        if not isinstance(sc, dict):
            errors.append(FieldError(
                "teachback_approved.scanned_candidate",
                "must be a dict with 'candidate' and 'evidence_against' fields",
                _truncate(sc),
            ))
        else:
            candidate = sc.get("candidate")
            if _check_min_length(
                candidate,
                "teachback_approved.scanned_candidate.candidate",
                40, errors,
            ):
                _check_non_template(
                    candidate,
                    "teachback_approved.scanned_candidate.candidate",
                    errors,
                )
                # Full protocol only: substring-inequality against submit
                # (simplified has no most_likely_wrong)
                if protocol_level == "full" and isinstance(submit, dict):
                    submit_mlw = submit.get("most_likely_wrong") or {}
                    submit_assumption = submit_mlw.get("assumption", "") if isinstance(submit_mlw, dict) else ""
                    if not _scanned_candidate_distinct(candidate, submit_assumption):
                        errors.append(FieldError(
                            "teachback_approved.scanned_candidate.candidate",
                            "must NOT be substring-equal to "
                            "teachback_submit.most_likely_wrong.assumption "
                            "(case-insensitive; rubber-stamp blocker per "
                            "CONTENT-SCHEMAS.md §Substring-inequality check). "
                            "Generate a DIFFERENT candidate misunderstanding.",
                            _truncate(candidate),
                        ))

            evidence = sc.get("evidence_against")
            if not isinstance(evidence, str) or not evidence.strip():
                errors.append(FieldError(
                    "teachback_approved.scanned_candidate.evidence_against",
                    "must not be empty",
                    _truncate(evidence),
                ))
            elif len(evidence) > 300:
                errors.append(FieldError(
                    "teachback_approved.scanned_candidate.evidence_against",
                    f"max 300 chars (got {len(evidence)})",
                    _truncate(evidence),
                ))
            elif not _evidence_grounded(evidence, submit if isinstance(submit, dict) else {}):
                errors.append(FieldError(
                    "teachback_approved.scanned_candidate.evidence_against",
                    "must be a case-insensitive substring of the concatenated "
                    "teachback_submit text (quote the teammate's own words)",
                    _truncate(evidence),
                ))

        cm = approved.get("conditions_met")
        if not isinstance(cm, dict):
            errors.append(FieldError(
                "teachback_approved.conditions_met",
                "must be a dict with 'addressed' and 'unaddressed' list fields",
                _truncate(cm),
            ))
        else:
            addressed = cm.get("addressed")
            if not isinstance(addressed, list):
                errors.append(FieldError(
                    "teachback_approved.conditions_met.addressed",
                    "must be a list",
                    _truncate(addressed),
                ))
            else:
                invalid = _all_addressed_valid(addressed, required_scope_items)
                if invalid:
                    errors.append(FieldError(
                        "teachback_approved.conditions_met.addressed",
                        f"item(s) not in required_scope_items: "
                        f"{', '.join(invalid[:5])}"
                        + ("..." if len(invalid) > 5 else ""),
                        _truncate(addressed),
                    ))
            unaddressed = cm.get("unaddressed")
            if not isinstance(unaddressed, list):
                errors.append(FieldError(
                    "teachback_approved.conditions_met.unaddressed",
                    "must be a list (empty list means all items addressed)",
                    _truncate(unaddressed),
                ))
            # Non-empty unaddressed triggers auto-downgrade at the gate
            # state classifier, NOT here — teachback_scan._classify_task_state
            # handles the T5 transition.

        if protocol_level != "full":
            return errors

        # Full protocol: response_to_assumption + response_to_least_confident +
        # first_action_check
        for field_name in ("response_to_assumption", "response_to_least_confident"):
            resp = approved.get(field_name)
            if not isinstance(resp, dict):
                errors.append(FieldError(
                    f"teachback_approved.{field_name}",
                    "must be a dict with 'verdict' and 'grounding' fields",
                    _truncate(resp),
                ))
                continue
            verdict = resp.get("verdict")
            if verdict not in ("confirm", "correct"):
                errors.append(FieldError(
                    f"teachback_approved.{field_name}.verdict",
                    f"must be one of {{'confirm', 'correct'}} (got "
                    f"{verdict!r})",
                    _truncate(verdict),
                ))
            grounding = resp.get("grounding")
            if _check_min_length(
                grounding,
                f"teachback_approved.{field_name}.grounding",
                20, errors,
            ):
                _check_non_template(
                    grounding,
                    f"teachback_approved.{field_name}.grounding",
                    errors,
                )
                if not _GROUNDING_SHAPE.search(grounding):
                    errors.append(FieldError(
                        f"teachback_approved.{field_name}.grounding",
                        "must contain '§' OR 'line N' OR 'section' OR "
                        "':N' line-number shape (reference the dispatch)",
                        _truncate(grounding),
                    ))

        fac = approved.get("first_action_check")
        if not isinstance(fac, dict):
            errors.append(FieldError(
                "teachback_approved.first_action_check",
                "must be a dict with 'my_derivation', 'match', "
                "and 'if_mismatch_resolution' fields",
                _truncate(fac),
            ))
        else:
            _check_citation(
                fac.get("my_derivation"),
                "teachback_approved.first_action_check.my_derivation",
                strictness, errors,
            )
            match = fac.get("match")
            if match not in ("match", "mismatch"):
                errors.append(FieldError(
                    "teachback_approved.first_action_check.match",
                    f"must be one of {{'match', 'mismatch'}} (got "
                    f"{match!r})",
                    _truncate(match),
                ))
            resolution = fac.get("if_mismatch_resolution")
            if match == "match":
                if resolution is not None:
                    errors.append(FieldError(
                        "teachback_approved.first_action_check.if_mismatch_resolution",
                        "must be null when match == 'match'",
                        _truncate(resolution),
                    ))
            elif match == "mismatch":
                if _check_min_length(
                    resolution,
                    "teachback_approved.first_action_check.if_mismatch_resolution",
                    20, errors,
                ):
                    _check_non_template(
                        resolution,
                        "teachback_approved.first_action_check.if_mismatch_resolution",
                        errors,
                    )

        return errors
    except Exception:
        return errors
