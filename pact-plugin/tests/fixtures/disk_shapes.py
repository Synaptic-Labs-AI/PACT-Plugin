"""
Location: pact-plugin/tests/fixtures/disk_shapes.py
Summary: Fixture surface for the OPERATIONAL-LULL regression test
         suite. Re-exports the canonical UMBRELLA_SUBJECT_PREFIXES tuple
         from shared/wake_lifecycle.py (the production home) AND
         defines minimal on-disk task / team-config dict-shape helpers
         used by fixtures.
Used by: pact-plugin/tests/test_shared_wake_lifecycle.py (unit tests on
         has_in_progress_umbrella_orchestration import the tuple +
         shape helpers),
         pact-plugin/tests/test_teardown_request_emitter_phase_lull.py
         (Tier-1 phase-lull regression fixtures),
         pact-plugin/tests/test_wake_lifecycle_emitter_phase_lull.py
         (Tier-2 mirror coverage), and the promoted
         tests/regression/test_842_phase_lull_regression.py harness.

SSOT direction: UMBRELLA_SUBJECT_PREFIXES is DEFINED at
shared/wake_lifecycle.py (production); this module RE-EXPORTS it. Hook
subprocesses do not have tests/ on sys.path at runtime, so the
production helper cannot import from this fixture module — the
dependency points outward from production to tests, not inward. Tests
get drift-resistance via Python import semantics: the re-exported
tuple object is identity-equal to the production constant. Per
devops's empirical finding at the Phase A diagnostic, umbrella tasks
have `owner: null` on disk (created by /PACT:orchestrate /
/PACT:comPACT / /PACT:peer-review and phase-task TaskCreates), so the
detection discriminator is signature-based (subject prefix), NOT
owner-based.

Scope discipline: this module pins only what the production helper or
its direct unit tests consume. Test-engineer's broader fixture surface
may add helpers here on its own SendMessage coordination — the SSOT
principle is "single source of truth for the load-bearing contract,"
not "all-encompassing module."
"""

# Re-export UMBRELLA_SUBJECT_PREFIXES from its production home so test
# fixtures import a single symbol that is provably identical (by Python
# import semantics) to the constant the production helper consults.
# Adding a new prefix requires updating ONLY shared/wake_lifecycle.py —
# both the production helper and every regression fixture re-derive at
# import time.
from shared.wake_lifecycle import UMBRELLA_SUBJECT_PREFIXES  # noqa: F401


def make_umbrella_task(
    task_id: str,
    subject_prefix: str = "Feature: ",
    subject_suffix: str = "test umbrella",
    status: str = "in_progress",
) -> dict:
    """Return a minimal on-disk task dict shaped like an umbrella-
    orchestration task. Used by unit tests on
    has_in_progress_umbrella_orchestration and by phase-lull regression
    fixtures.

    Mirrors the empirically-captured umbrella shape: no `owner` field
    (or `owner: null`), `subject` starts with one of
    UMBRELLA_SUBJECT_PREFIXES, lifecycle status defaults to
    `in_progress` (the state Gate 6 is designed to detect).

    `subject_prefix` MUST be a member of UMBRELLA_SUBJECT_PREFIXES for
    the resulting task to match the predicate; pass a non-canonical
    prefix to fixture a negative case.
    """
    return {
        "id": task_id,
        "subject": subject_prefix + subject_suffix,
        "status": status,
        "blocks": [],
        "blockedBy": [],
        "metadata": {},
    }


def make_specialist_task(
    task_id: str,
    owner: str,
    subject: str = "specialist work item",
    status: str = "in_progress",
) -> dict:
    """Return a minimal on-disk task dict shaped like a specialist
    (teammate-owned) task. Used by phase-lull fixtures to assert Gate 6
    short-circuits BEFORE the count_active_tasks iteration when an
    umbrella is present even though specialist work has wound down."""
    return {
        "id": task_id,
        "subject": subject,
        "status": status,
        "owner": owner,
        "blocks": [],
        "blockedBy": [],
        "metadata": {},
    }


def make_team_config(
    team_name: str,
    members: list[dict] | None = None,
    lead_agent_id: str = "a-lead",
) -> dict:
    """Return a minimal team-config dict matching the on-disk shape at
    ~/.claude/teams/{team_name}/config.json. The `members` list is the
    field consumed by _classify_owner in shared/wake_lifecycle.py; the
    `leadAgentId` field is consumed by _read_team_lead_agent_id. The
    predicate under test (has_in_progress_umbrella_orchestration) does
    NOT read team config — it operates purely on iter_team_task_jsons
    output — but its unit tests still write a config file because
    test_classify_owner-style call patterns depend on it for symmetry
    with the broader wake-lifecycle test surface."""
    return {
        "team_name": team_name,
        "leadAgentId": lead_agent_id,
        "members": members if members is not None else [],
    }
