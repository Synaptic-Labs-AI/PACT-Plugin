#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/shared/handoff_merge.py
Summary: Reader-side HANDOFF reconciliation for the pact-handoff-harvest
         skill: the harvest's disk identity (disk_identity), the
         task-file-preferred union (union_preferring_task_file), the
         NEW-FORMAT partition router (partition_destination), and the
         OLD-FORMAT classification and routing (mixed_old_format_event,
         old_format_destination).
Used by: skills/pact-handoff-harvest/SKILL.md (every `python3 -c` route
         imports from this module, including the re-exported emit-side names
         below) and the harvest test suites. NO HOOK IMPORTS THIS MODULE: it
         is kept out of the emit path so the emit hooks do not carry its
         handoff_schema edge.

The emit-side names the skill also calls (report_safe, handoff_content_key,
emit_side_subject, occupant_hash) live in shared/agent_handoff_marker.py and
are re-exported here, so the skill has ONE import path.
"""

from __future__ import annotations

from typing import NamedTuple

from .agent_handoff_marker import (
    emit_side_subject,
    handoff_content_key,
    occupant_hash,
    report_safe,
)
from .handoff_schema import (
    HANDOFF_LEGACY_ALIASES,
    HANDOFF_REQUIRED_FIELDS,
    resolve_handoff_field,
)

__all__ = [
    "OLD_FORMAT_DEST_FROM",
    "OLD_FORMAT_DEST_MIXED",
    "OLD_FORMAT_DEST_THIS",
    "OLD_FORMAT_MIXED",
    "OLD_FORMAT_OWNED",
    "OLD_FORMAT_UNMATCHED",
    "OldFormatOutcome",
    "PARTITION_FOREIGN",
    "PARTITION_FROM",
    "PARTITION_OLD_FORMAT",
    "PARTITION_THIS",
    "disk_identity",
    "emit_side_subject",
    "handoff_content_key",
    "mixed_old_format_event",
    "occupant_hash",
    "old_format_destination",
    "partition_destination",
    "report_safe",
    "union_preferring_task_file",
]


def disk_identity(owner: str, subject: object) -> str:
    """occupant_hash(owner, emit_side_subject(subject)) — the harvest's disk identity.

    The harvest computes a task file's group identity from the RECORD
    (owner, subject), while the emit computed it from the value it had at emit
    time. Routing both through this one function is what keeps the two in
    agreement on the sentinel substitution.
    """
    return occupant_hash(owner, emit_side_subject(subject))


# The values that count as EMPTY for the union below. Test MEMBERSHIP, not
# truthiness: `not disk_value` is also TRUE for `0` and for `False`, so a
# task-file field that deliberately holds one of those would keep the journal
# value and discard the disk value. KEEP `None` IN THIS SET. Drop it and the
# narrowing itself opens the erasure it was written to close: a field
# explicitly holding null would count as CONTENT and overwrite the journal
# copy with null.
_EMPTY_VALUES = (None, "", [], {})

_REQUIRED = frozenset(HANDOFF_REQUIRED_FIELDS)

def _identity(raw_key: str) -> str:
    """Canonical identity of one raw key.

    NORMALIZATION RUNS BEFORE ANY SET MATH. `key_decisions` is the legacy
    spelling of `decisions`; a required-set comparison over RAW keys would
    leave a renamed field outside the required set and classify a complete
    revision as a partial one, reviving the field the revision cleared.
    Measured: the rename/add fixture routes correctly only in this order.
    """
    return HANDOFF_LEGACY_ALIASES.get(raw_key, raw_key)


def _field_map(handoff: dict) -> dict:
    """canonical identity -> (raw key, value) for one copy.

    VALUE SELECTION MIRRORS `resolve_handoff_field`: the canonical spelling
    wins when it holds content; when it is FALSY, the first truthy alias
    spelling wins. Before this alignment the map chose the canonical spelling
    unconditionally, so a disk copy carrying `decisions: ''` beside
    `key_decisions: [...]` selected the empty canonical: the alias value was
    ignored AND the journal content was dropped — measured content loss on a
    dual-spelling task file.
    """
    fields: dict[str, tuple[str, object]] = {}
    for raw_key, value in handoff.items():
        identity = _identity(raw_key)
        current = fields.get(identity)
        if current is None:
            fields[identity] = (raw_key, value)
        elif raw_key == identity:
            # Canonical spelling: wins when it has content, and supplies the
            # fallback value when it is falsy and no alias did.
            if value or not current[1]:
                fields[identity] = (raw_key, value)
        elif value and not current[1]:
            # Alias spelling with CONTENT beats a falsy canonical. The
            # truthiness test on `value` is load-bearing: without it a FALSY
            # alias overwrites a falsy canonical whenever it appears later in
            # insertion order, and the map disagrees with the shipped reader
            # (`resolve_handoff_field` falls back to the canonical value when
            # no alias is truthy).
            fields[identity] = (raw_key, value)
    return fields


def _required_set(fields: dict) -> set:
    """The REQUIRED canonical fields one copy carries.

    Scoped to HANDOFF_REQUIRED_FIELDS, never to every key: `reasoning_chain`
    is recommended, so a legal revision may omit it, and journal copies carry
    non-canonical extras. A full-key comparison calls both cases partial and
    restores the cleared field.
    """
    return {identity for identity in fields if identity in _REQUIRED}


def union_preferring_task_file(
    journal_handoff: object, disk_handoff: object, task_id: str
) -> tuple[dict, list[str]]:
    """Merge the two HANDOFF copies by their REQUIRED key sets.

    The task file wins each conflict, apart from the two states below. Both
    states are decided by the two copies' REQUIRED key sets, both are
    reported with one line per field, and neither is decided by an empty or
    missing value alone. Keep each field that is in one copy alone, except a
    REQUIRED field the task file lacks in the incomparable state, which is
    dropped and reported.

    Let `J` be the journal copy's required key set and `D` the task file's.
    EQUALITY COUNTS AS COVERING: 130 of 130 observed local pairs are equal,
    and a strict-superset test misroutes every one.

    State A — field PRESENT AND EMPTY on the task file, content in the
    journal:
      - D covers J (equality included) -> keep the EMPTY value.
      - D is a proper subset of J     -> keep the JOURNAL value.
      - incomparable                  -> keep the EMPTY value.

    State B — field PRESENT IN THE JOURNAL and ABSENT from the task file:
      - REQUIRED, D a proper subset of J -> keep the journal value.
      - REQUIRED, incomparable           -> DROP it (the only branch that
                                            drops journal content).
      - NOT required                     -> keep it (union default).

    THE RESIDUAL: a deliberate complete removal of a REQUIRED key and an
    accidental partial re-write both leave a proper subset, so the
    proper-subset branch restores in both and reports that it did. Key sets
    cannot separate them; the report is what makes the restore auditable.

    BOTH INPUTS ARE TOTAL. A non-dict argument is treated as an ABSENT copy
    (`{}`), matching the convention the OLD-FORMAT classifier applies to
    event and disk handoffs: a malformed copy is missing, not a crash. A
    wrong-typed `metadata.handoff` reaches this function through the
    harvest's read loop, and raising here would make that loop hostage to
    one bad field. THE COERCION IS REPORTED, once per call: it names which
    side was not a mapping, because a silently empty merge processes the
    task and records its id as done, so the loss is never retried and
    nothing says why.

    Pure; no I/O; returns a fresh dict and one report line per divergent
    field. Both inputs are left unmutated.
    """
    reports: list[str] = []
    report_task = report_safe(task_id)
    bad_copies: list[str] = []
    if not isinstance(journal_handoff, dict):
        bad_copies.append("journal")
        journal_handoff = {}
    if not isinstance(disk_handoff, dict):
        bad_copies.append("task-file")
        disk_handoff = {}
    if bad_copies:
        reports.append(
            f"handoff copy not a mapping for task {report_task}: the "
            f"{' and '.join(bad_copies)} copy was not a mapping; treated as "
            f"absent"
        )
    merged = dict(journal_handoff)

    journal_fields = _field_map(journal_handoff)
    disk_fields = _field_map(disk_handoff)
    j_req = _required_set(journal_fields)
    d_req = _required_set(disk_fields)

    def _drop_journal_identity(identity: str) -> None:
        """Remove EVERY journal raw key whose canonical identity is `identity`.

        A journal copy may carry BOTH the canonical and an alias spelling of
        one field. `_field_map` collapses them for the set math, but `merged`
        starts as a copy of the RAW journal mapping, so dropping only the one
        spelling `_field_map` selected would leave the other in the output and
        defeat the decision below: a reader resolving aliases still finds the
        value that was just decided against.
        """
        for raw_key in journal_handoff:
            if _identity(raw_key) == identity:
                merged.pop(raw_key, None)

    def _take_disk(identity: str, disk_value: object) -> None:
        disk_key, _ = disk_fields[identity]
        _drop_journal_identity(identity)
        merged[disk_key] = disk_value

    # State A: every field the task file carries.
    for identity, (disk_key, disk_value) in disk_fields.items():
        report_field = report_safe(identity)
        journal_present = identity in journal_fields
        journal_value = (
            resolve_handoff_field(journal_handoff, identity)
            if journal_present else None
        )
        if (
            disk_value in _EMPTY_VALUES
            and journal_present
            # MEMBERSHIP, as on the disk side: a journal 0 or False is
            # content, and the State A rules below decide it.
            and journal_value not in _EMPTY_VALUES
        ):
            if d_req >= j_req:  # covering, EQUALITY INCLUDED
                reports.append(
                    f"handoff field kept empty for task {report_task}: field "
                    f"{report_field} present and empty; the task-file "
                    f"required key set covers the journal copy"
                )
                _take_disk(identity, disk_value)
            elif d_req < j_req:  # proper subset
                reports.append(
                    f"handoff field restored from the journal for task "
                    f"{report_task}: field {report_field} present and empty; "
                    f"the task-file required key set is a proper subset of "
                    f"the journal copy"
                )
                # keep the journal value: merged already holds it
            else:  # incomparable
                reports.append(
                    f"handoff field undecidable for task {report_task}: "
                    f"field {report_field} present and empty; the required "
                    f"key sets are incomparable"
                )
                _take_disk(identity, disk_value)
        else:
            _take_disk(identity, disk_value)

    # State B: fields only the journal carries.
    for identity in journal_fields:
        if identity in disk_fields:
            continue
        report_field = report_safe(identity)
        if identity in _REQUIRED:
            if d_req < j_req:
                reports.append(
                    f"handoff field restored from the journal for task "
                    f"{report_task}: required field {report_field} absent "
                    f"from the task file; the task-file required key set is "
                    f"a proper subset of the journal copy"
                )
                # keep it
            else:  # incomparable; covering is impossible here
                reports.append(
                    f"handoff field undecidable for task {report_task}: "
                    f"required field {report_field} absent from the task "
                    f"file; the required key sets are incomparable"
                )
                _drop_journal_identity(identity)
        else:
            reports.append(
                f"handoff field kept from the journal for task {report_task}: "
                f"field {report_field} absent from the task file and not a "
                f"required field; the required key sets do not decide it"
            )
            # keep it: the union default for a non-required field

    return merged, reports


OLD_FORMAT_MIXED = "mixed"          # content is another store's; discard + report
OLD_FORMAT_OWNED = "owned_by"       # content AND identity match a named store
OLD_FORMAT_UNMATCHED = "unmatched"  # no named store carries the content

# Route labels `old_format_destination` returns. EXPORTED so a caller cannot
# drift a bare literal into an else bucket: a typo there would silently route
# a realigned team's work unit into THIS team.
OLD_FORMAT_DEST_MIXED = "mixed"
OLD_FORMAT_DEST_FROM = "from"
OLD_FORMAT_DEST_THIS = "this"

# Route labels `partition_destination` returns for a team-carrying event.
# EXPORTED for the same cause as the OLD_FORMAT_DEST_* set: a bare literal
# drifted into an else bucket silently misroutes a realigned team's work
# unit into THIS team.
PARTITION_THIS = "this"
PARTITION_FROM = "from"
PARTITION_FOREIGN = "foreign"
PARTITION_OLD_FORMAT = "old-format"


class OldFormatOutcome(NamedTuple):
    """Structured result of classifying one OLD-FORMAT journal event.

    `state` is one of the three OLD_FORMAT_* constants. `team` names the store
    that owns a MIXED event's content or an OWNED event outright, and is None
    for UNMATCHED. `report` is the skill's report line for a MIXED event and
    None otherwise.
    """

    state: str
    team: str | None = None
    report: str | None = None


def old_format_destination(outcome: OldFormatOutcome, this_team: str) -> str:
    """Where an OLD-FORMAT event belongs, given its classification.

    Returns one of the EXPORTED OLD_FORMAT_DEST_* labels; callers compare
    against those constants, never against bare literals.
      - OLD_FORMAT_DEST_MIXED — discard it and report `outcome.report`.
      - OLD_FORMAT_DEST_FROM — it is `outcome.team`'s work unit: process it
        under that team's `## team={team}` section against that team's store,
        never unioned with this team's task file.
      - OLD_FORMAT_DEST_THIS — this team's work unit (OWNED_BY this team, or
        UNMATCHED). UNMATCHED carries `outcome.report`, the unavailable-check
        line the caller MUST emit; OWNED_BY(this_team) carries none.

    A caller that routes every non-MIXED outcome to this team misroutes a
    realigned earlier team's HANDOFF, which is the population the old-format
    rule exists to protect. Pure; never raises.
    """
    if outcome.state == OLD_FORMAT_MIXED:
        return OLD_FORMAT_DEST_MIXED
    if outcome.state == OLD_FORMAT_OWNED and outcome.team != this_team:
        return OLD_FORMAT_DEST_FROM
    return OLD_FORMAT_DEST_THIS


def partition_destination(
    event_team: object, this_team: str, from_teams: list[str]
) -> str:
    """Where a team-carrying `agent_handoff` event belongs.

    Returns one of the EXPORTED PARTITION_* labels; callers compare against
    those constants, never against bare literals.
      - PARTITION_OLD_FORMAT — the event carries NO team (absent or empty):
        the OLD-FORMAT rule decides it.
      - PARTITION_THIS — the event names THIS team.
      - PARTITION_FROM — the event names a team the journal realigned FROM.
      - PARTITION_FOREIGN — it names neither; do not process it.

    Callers handle an underivable `this_team` BEFORE partitioning — every
    event is OLD-FORMAT then — so this function compares the names as given.

    A NON-STR `event_team` IS NEVER A VALID TEAM NAME and returns
    PARTITION_FOREIGN, even when the caller's list happens to hold the same
    non-str value. `None` is the ABSENT case and stays OLD-FORMAT. THE
    CALLER STILL BUILDS THE DICT KEY AND MUST FILTER: this function no
    longer maps a non-str to FROM, but that does not license passing one.
    Pure; never raises.
    """
    if not isinstance(event_team, str):
        if event_team is None:
            return PARTITION_OLD_FORMAT
        return PARTITION_FOREIGN
    if not event_team:
        return PARTITION_OLD_FORMAT
    if event_team == this_team:
        return PARTITION_THIS
    if event_team in from_teams:
        return PARTITION_FROM
    return PARTITION_FOREIGN


def mixed_old_format_event(
    task_id: str,
    event_agent: object,
    event_subject: object,
    event_handoff: object,
    candidate_tasks: list[dict],
) -> OldFormatOutcome:
    """Classify an OLD-FORMAT journal event against the stores whose NAMES
    the harvest holds.

    An OLD-FORMAT event carries no `team` field (every event written before
    that field existed), so its `(agent, task_subject)` identity alone cannot
    prove it belongs to this team. `candidate_tasks` are the PARSED task files
    of the stores whose names the harvest holds — this team's store and each
    `team_realigned` `from` store — as `{"team": <store name>, "task": <dict>}`.
    `task_id` is the id being resolved; it names the task in EVERY report, so
    the caller never has to compose one.

    Three outcomes, because the caller must ROUTE the event, not merely decide
    whether to discard it:
      - OLD_FORMAT_OWNED: one store's handoff content key equals the event's
        AND that store's `(owner, subject)` identity equals the event's
        `(agent, task_subject)` identity. The event is that store's work unit;
        `team` names it, and the caller routes a non-current team to its own
        section. Checked across ALL candidates BEFORE any MIXED report, so a
        later coincidental content match cannot override an identity match.
      - OLD_FORMAT_MIXED: a store's content key matches while its identity
        does not. The content came from a store whose identity the event does
        not carry; discard it and report `report`.
      - OLD_FORMAT_UNMATCHED: NO other candidate decides the event — either
        no named store carries the content, or the only content-carrying
        candidate could not have its identity compared (see the owner
        exception below). Keep the identity match as this team's, and report
        the unavailable check — a store no realignment event names is out of
        reach, and guessing is worse than reporting the bound. The report
        travels ON THE OUTCOME because this state is the COMMON path, and a
        caller that had to invent the line would be free to choose silently
        instead. The two causes carry DIFFERENT lines.

    ONE DELIBERATE EXCEPTION TO THE MIXED RULE: a content-matching candidate
    whose own record carries no non-empty `owner` is SKIPPED, not treated as
    an identity mismatch. The emit path substitutes the platform teammate name
    for an empty owner and the record cannot reproduce it, so comparing
    identities would manufacture a MISMATCH and discard content that was
    never foreign. THE SKIP REMOVES THIS CANDIDATE FROM CONSIDERATION AND
    DOES NOT SETTLE THE EVENT: another named candidate may still return
    OWNED_BY that team or MIXED, and the event is routed or discarded
    accordingly; it is UNMATCHED only when no other candidate decides it.
    When the skip leaves the event UNMATCHED, the returned report says a
    candidate CARRIED the content but its identity could not be compared —
    distinct from the no-candidate line, because the two states call for
    different actions.

    A non-dict / empty event handoff is UNMATCHED: there is no content to
    compare. Pure; no I/O; never raises.
    """
    unmatched = OldFormatOutcome(
        OLD_FORMAT_UNMATCHED,
        report=(
            f"handoff cross-team check unavailable for task "
            f"{report_safe(task_id)}: no other named store carries the "
            f"identity"
        ),
    )
    if not isinstance(event_handoff, dict) or not event_handoff:
        return unmatched
    event_content = handoff_content_key(event_handoff)
    event_identity = disk_identity(
        event_agent if isinstance(event_agent, str) else "", event_subject
    )
    content_matches: list[tuple[str, dict]] = []
    skipped_ownerless = False
    for candidate in candidate_tasks or ():
        if not isinstance(candidate, dict):
            continue
        team = candidate.get("team")
        task = candidate.get("task")
        if not isinstance(task, dict):
            continue
        metadata = task.get("metadata")
        if not isinstance(metadata, dict):
            continue
        task_handoff = metadata.get("handoff")
        if not isinstance(task_handoff, dict) or not task_handoff:
            continue
        if handoff_content_key(task_handoff) != event_content:
            continue
        owner = task.get("owner")
        if not isinstance(owner, str) or not owner.strip():
            # The emit substitutes the PLATFORM TEAMMATE NAME for an empty
            # owner, and the task record cannot reproduce that name. Comparing
            # identities here would MANUFACTURE a mismatch and report this
            # team's own event as MIXED, discarding content that was never
            # foreign. The skip removes THIS CANDIDATE from consideration; it
            # does NOT settle the event — another candidate may still return
            # OWNED_BY or MIXED, and the event is UNMATCHED only when none
            # does. `skipped_ownerless` makes that state reportable.
            skipped_ownerless = True
            continue
        file_identity = disk_identity(owner, task.get("subject"))
        if file_identity == event_identity:
            return OldFormatOutcome(OLD_FORMAT_OWNED, team=team)
        content_matches.append((team, task))
    if not content_matches:
        if skipped_ownerless:
            # Distinguishable from "no named store carried the content": a
            # reader must be able to tell a check that found nothing from a
            # check that found the content but could not compare identity.
            return OldFormatOutcome(
                OLD_FORMAT_UNMATCHED,
                report=(
                    f"handoff cross-team check unavailable for task "
                    f"{report_safe(task_id)}: a candidate carries the "
                    f"content but its record has no owner; its identity "
                    f"could not be compared"
                ),
            )
        return unmatched
    team, _ = content_matches[0]
    report_task = report_safe(task_id)
    report_team = report_safe(team)
    return OldFormatOutcome(
        OLD_FORMAT_MIXED,
        team=team,
        report=(
            f"handoff identity mismatch for task {report_task}: journal "
            f"content equals team {report_team} task {report_task}, whose "
            f"identity it does not carry"
        ),
    )
