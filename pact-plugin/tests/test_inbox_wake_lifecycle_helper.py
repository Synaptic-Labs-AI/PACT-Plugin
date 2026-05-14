"""
Behavioral invariants for pact-plugin/hooks/shared/wake_lifecycle.py.

Direct-import tests of count_active_tasks() and _lifecycle_relevant().
Pin the carve-out semantics (signal-tasks, team-config exempt agentTypes)
to the shared helper _is_wake_excluded_agent_type from
shared.intentional_wait (no duplicate literal). Pure-never-raises
property pins the contract so the redundant try/except in
session_init.py:728-730 can be removed in future cleanup.

POST-EMPTY-CARVE-OUT NOTE: WAKE_EXCLUDED_AGENT_TYPES is now an empty
frozenset (decoupled from SELF_COMPLETE_EXEMPT_AGENT_TYPES which still
contains pact-secretary). Secretary tasks ARE counted toward the wake-
mechanism active tally; the count gate (count > 0 prevents Teardown
emit) handles the Bug A secretary-window scenario at the count layer
rather than the per-owner carve-out layer. Tests below that previously
asserted "secretary excluded from wake count" have been inverted to
assert the post-empty behavior (secretary counts).
"""

import json
import sys
from pathlib import Path

import pytest

# Hooks dir is added to sys.path by conftest.
import shared.wake_lifecycle as wl
from shared.intentional_wait import (
    SELF_COMPLETE_EXEMPT_AGENT_TYPES,
    WAKE_EXCLUDED_AGENT_TYPES,
)


def _write_team_config(tmp_path, team_name, members):
    """Write a team config under tmp_path/.claude/teams/<team_name>/config.json,
    mirroring the harness path that _iter_members reads when teams_dir
    override is omitted (Path.home() resolves to tmp_path via monkeypatch).
    """
    team_dir = tmp_path / ".claude" / "teams" / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "config.json").write_text(
        json.dumps({"team_name": team_name, "members": members}),
        encoding="utf-8",
    )


# ---------- Source-level structural invariants ----------

def test_helper_imports_shared_helper_from_intentional_wait():
    """No duplicate carve-out logic — the helper must reuse the canonical
    wake-side carve-out helper from shared.intentional_wait. Pin the
    DECOUPLED-CONSTANT discipline: the wake-side import must be
    _is_wake_excluded_agent_type (consulting WAKE_EXCLUDED_AGENT_TYPES),
    NOT _is_exempt_agent_type (the self-completion-side helper consulting
    SELF_COMPLETE_EXEMPT_AGENT_TYPES). The two sets are currently
    identical at {pact-secretary} but the import names are decoupled so
    a future divergence (e.g., wake-side reduction without changing
    self-completion authority) does not require touching this file."""
    src = (
        Path(__file__).resolve().parent.parent
        / "hooks" / "shared" / "wake_lifecycle.py"
    ).read_text(encoding="utf-8")
    assert "from shared.intentional_wait import _is_wake_excluded_agent_type" in src
    # Active anti-recouple guard: wake_lifecycle MUST NOT IMPORT the
    # self-completion-side helper. Re-introducing the import would
    # silently re-couple the two policies. Pinned via line-anchored
    # import-statement match (rather than bare substring) so the
    # DECOUPLED-CONSTANT DISCIPLINE comment in wake_lifecycle.py — which
    # legitimately mentions _is_exempt_agent_type as the warning target —
    # does not trigger this assertion.
    has_recouple_import = any(
        line.strip() == "from shared.intentional_wait import _is_exempt_agent_type"
        or line.strip().startswith(
            "from shared.intentional_wait import "
        ) and "_is_exempt_agent_type" in line.split("import", 1)[1]
        for line in src.splitlines()
    )
    assert not has_recouple_import, (
        "wake_lifecycle.py must NOT import _is_exempt_agent_type "
        "(the self-completion-side helper). Use _is_wake_excluded_agent_type "
        "instead — see DECOUPLED-CONSTANT DISCIPLINE comment in "
        "wake_lifecycle.py."
    )
    # No re-declaration: a literal exempt set in the helper would diverge
    # from intentional_wait. Belt-and-suspenders: stale post-#682 import.
    assert "SELF_COMPLETE_EXEMPT_AGENTS" not in src
    assert "frozenset({\"pact-secretary\"" not in src
    assert "frozenset({'pact-secretary'" not in src


def test_lifecycle_relevant_preserves_fail_conservative_audit_anchor():
    """The fail-CONSERVATIVE asymmetry between this call site (count on
    config-read failure) and the sibling predicates in
    intentional_wait.py (return False on config-read failure) is
    load-bearing for the wake mechanism. Pin the audit-anchor phrases
    INSIDE the step-4 ``elif team_name:`` block specifically — a free
    file-wide substring check would pass vacuously if a future
    contributor introduced the phrases anywhere else in the module
    (e.g. a helper docstring, an unrelated comment). The tightened
    anchor requires both halves of the rationale to live in the
    executable elif-body's comment block, so a body-only revert that
    deletes the elif block deletes the anchor with it.
    """
    src_path = (
        Path(__file__).resolve().parent.parent
        / "hooks" / "shared" / "wake_lifecycle.py"
    )
    src_lines = src_path.read_text(encoding="utf-8").splitlines()

    # Anchor: the EXECUTABLE statement `    elif team_name:` (4-space
    # indent + trailing colon). Docstring mentions like `elif team_name:`
    # wrapped in backticks do not match because the indentation differs
    # and they appear inside triple-quoted strings, not at function-body
    # indent. We require exactly one such anchor line — duplicates would
    # indicate either a refactor that split the predicate or a spurious
    # paste, both of which warrant review.
    anchor_indices = [
        i for i, ln in enumerate(src_lines) if ln == "    elif team_name:"
    ]
    assert len(anchor_indices) == 1, (
        "Expected exactly one `    elif team_name:` executable line in "
        "wake_lifecycle.py (the step-4 fail-CONSERVATIVE branch). Found "
        f"{len(anchor_indices)} at line numbers "
        f"{[i + 1 for i in anchor_indices]!r}."
    )
    anchor_idx = anchor_indices[0]

    # Window: lines following the anchor that belong to the elif body.
    # The elif body in this branch is comment-only narration plus the
    # final `_warn_empty_team_config_once(team_name)` call. Collect
    # lines until we hit either:
    #   (a) a `# Step N:` outer-step marker (the next outer-step comment
    #       at the SAME 4-space indent), which closes the elif body, or
    #   (b) any non-comment, non-blank statement that is NOT the expected
    #       trailing `_warn_empty_team_config_once(team_name)` call.
    # The collected window is the executable elif body's comment block.
    window_lines = []
    for ln in src_lines[anchor_idx + 1:]:
        stripped = ln.strip()
        # Outer-step marker closes the window before consuming.
        if stripped.startswith("# Step ") and ln.startswith("    # Step "):
            break
        window_lines.append(ln)
        # Stop after the trailing _warn_empty_team_config_once call — the
        # last executable line of the elif body.
        if stripped == "_warn_empty_team_config_once(team_name)":
            break
    window_text = "\n".join(window_lines)

    # The three audit-anchor phrases MUST live inside the executable
    # elif body's window. A future revert that deletes the elif body
    # makes window_lines empty (or near-empty), flipping these
    # assertions to FAIL — the intended counter-signal under body-only
    # revert.
    assert "Fail-CONSERVATIVE" in window_text, (
        f"`Fail-CONSERVATIVE` audit anchor must live inside the step-4 "
        f"`elif team_name:` body (executable line {anchor_idx + 1}). "
        f"Window starts at line {anchor_idx + 2}; collected window:\n"
        f"{window_text!r}"
    )
    assert "under-arm" in window_text, (
        f"`under-arm` rationale must live inside the step-4 `elif "
        f"team_name:` body (executable line {anchor_idx + 1}). Window:\n"
        f"{window_text!r}"
    )
    assert "unrecoverable" in window_text, (
        f"`unrecoverable` rationale must live inside the step-4 `elif "
        f"team_name:` body (executable line {anchor_idx + 1}). Window:\n"
        f"{window_text!r}"
    )

    # Pin additionally that no OTHER executable site in the module
    # contains these phrases. Docstring/comment occurrences elsewhere
    # would create a phantom-green path where a future contributor
    # could delete the elif body's comment block, leave the phrases in
    # an unrelated docstring, and the assertions above would still
    # pass. Forbid the phrases anywhere in src EXCEPT inside the
    # window we just validated.
    src_outside_window = "\n".join(
        ln
        for i, ln in enumerate(src_lines)
        if not (anchor_idx + 1 <= i <= anchor_idx + len(window_lines))
    )
    for phrase in ("Fail-CONSERVATIVE", "under-arm", "unrecoverable"):
        assert phrase not in src_outside_window, (
            f"Phrase {phrase!r} must appear ONLY inside the step-4 "
            f"`elif team_name:` body's comment block (lines "
            f"{anchor_idx + 2}..{anchor_idx + 1 + len(window_lines)}). "
            f"Found outside the window — a future contributor could "
            f"delete the elif body's rationale and this anchor would "
            f"still pass vacuously."
        )


def test_helper_documented_pure_never_raises():
    """Pin the docstring contract — pure functions, never raise. This is
    the structural anchor that lets future cleanup remove the redundant
    try/except wrapping count_active_tasks at session_init.py:728-730."""
    docs = (wl.count_active_tasks.__doc__ or "") + (wl.__doc__ or "")
    assert "never raise" in docs.lower() or "never raises" in docs.lower()


# ---------- _lifecycle_relevant predicate ----------

@pytest.mark.parametrize("task,expected", [
    ({"status": "in_progress", "owner": "x"}, True),
    ({"status": "pending", "owner": "x"}, True),
    ({"status": "completed", "owner": "x"}, False),
    ({"status": "deleted", "owner": "x"}, False),
    ({"status": "blocked", "owner": "x"}, False),
    ({"status": "in_progress"}, True),  # missing owner is fine
    ({}, False),  # missing status fails the active-status gate
])
def test_lifecycle_relevant_status_gate(task, expected):
    assert wl._lifecycle_relevant(task) is expected


@pytest.mark.parametrize("agent_type", sorted(WAKE_EXCLUDED_AGENT_TYPES))
def test_lifecycle_relevant_excludes_wake_excluded_agenttypes(agent_type, tmp_path, monkeypatch):
    """Wake-excluded agentTypes resolved via team-config lookup do not
    count toward the active-work tally. The owner name is arbitrary —
    the team-config agentType is what matters (#682).

    POST-EMPTY-CARVE-OUT: WAKE_EXCLUDED_AGENT_TYPES is now an empty
    frozenset, so this parametrize produces ZERO cells (vacuously true,
    no test bodies execute). The test is preserved as a structural
    placeholder: if a future PR re-populates WAKE_EXCLUDED_AGENT_TYPES
    (e.g., adding back pact-secretary or adding a new exempt agentType),
    the parametrize automatically re-activates and pins the carve-out
    behavior. Distinct from the self-completion side which still has
    pact-secretary in SELF_COMPLETE_EXEMPT_AGENT_TYPES."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-exempt"
    _write_team_config(tmp_path, team, [
        {"name": "session-secretary", "agentType": agent_type},
    ])
    task = {"status": "in_progress", "owner": "session-secretary"}
    assert wl._lifecycle_relevant(task, team) is False


def test_lifecycle_relevant_secretary_owner_now_counts_post_empty_carve_out(tmp_path, monkeypatch):
    """POST-EMPTY-CARVE-OUT: a secretary-owned task is now lifecycle-
    relevant (counts toward the active tally) because
    WAKE_EXCLUDED_AGENT_TYPES is empty. Pre-empty, the spawn-name-
    freedom test asserted the inverse (any name reaches the exempt
    agentType carve-out, returns False); post-empty the carve-out has
    no agentType members to match against, so the carve-out is a no-op
    and secretary tasks count like any other teammate task.

    The Bug A secretary-window scenario (eager Teardown when secretary
    completes its first teachback) is now fixed at the count gate:
    count_active_tasks > 0 prevents Teardown emit before the
    has_same_teammate_continuation predicate is even consulted. The
    SELF_COMPLETE_EXEMPT_AGENT_TYPES side still contains pact-secretary
    (self-completion authority preserved); the wake-side carve-out is
    decoupled and now empty."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-arbitrary-name"
    _write_team_config(tmp_path, team, [
        {"name": "secretary-from-mars", "agentType": "pact-secretary"},
    ])
    task = {"status": "in_progress", "owner": "secretary-from-mars"}
    assert wl._lifecycle_relevant(task, team) is True, (
        "Post-empty WAKE_EXCLUDED_AGENT_TYPES: secretary tasks must count "
        "toward the wake-mechanism active tally. If False, the wake-side "
        "carve-out has been re-populated and this test must be inverted "
        "in lockstep."
    )


def test_lifecycle_relevant_owner_named_secretary_without_agenttype_excluded_as_orphan(tmp_path, monkeypatch):
    """An owner='secretary' string with no matching member in the team
    config is an orphan owner — the teammate-owner check returns False
    BEFORE the wake-side agentType carve-out can promote or demote it.
    A teammate spoofing owner='secretary' to escape the wake tally hits
    the orphan-exclusion gate; the spoof neither evades nor sneaks into
    the privileged agentType set.

    Pre-orphan-exclusion behavior: this task counted toward the active
    tally because the wake-side agentType carve-out only fired when the
    owner matched a member whose recorded agentType was in
    WAKE_EXCLUDED_AGENT_TYPES — a non-member owner simply fell through.
    Post-orphan-exclusion: orphan owners are now excluded regardless of
    agentType. The teammate-owner check fail-CLOSES on no member-name
    match (members list non-empty)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-spoof"
    _write_team_config(tmp_path, team, [
        {"name": "backend-coder-1", "agentType": "pact-backend-coder"},
    ])
    task = {"status": "in_progress", "owner": "secretary"}
    # Orphan owner (not in members) → excluded from wake tally.
    assert wl._lifecycle_relevant(task, team) is False


def test_lifecycle_relevant_empty_team_name_counts_secretary(tmp_path, monkeypatch):
    """team_name="" short-circuits the agentType carve-out to fail-closed.
    A secretary task with no resolvable team_name therefore counts
    (conservative: better to over-arm wake than miss real work)."""
    task = {"status": "in_progress", "owner": "session-secretary"}
    assert wl._lifecycle_relevant(task, "") is True
    assert wl._lifecycle_relevant(task) is True  # default team_name=""


@pytest.mark.parametrize("metadata,expected", [
    ({"completion_type": "signal", "type": "blocker"}, False),
    ({"completion_type": "signal", "type": "algedonic"}, False),
    # Wrong type → not a signal carve-out, still counts.
    ({"completion_type": "signal", "type": "regular"}, True),
    # Missing completion_type → counts.
    ({"type": "blocker"}, True),
    # Empty metadata → counts.
    ({}, True),
])
def test_lifecycle_relevant_signal_task_carveout(metadata, expected):
    task = {"status": "in_progress", "owner": "x", "metadata": metadata}
    assert wl._lifecycle_relevant(task) is expected


def test_lifecycle_relevant_handles_non_dict_input():
    for bad in (None, [], 42, "string", True):
        assert wl._lifecycle_relevant(bad) is False


def test_lifecycle_relevant_counts_under_malformed_metadata():
    """Malformed metadata (non-dict) is conservative-counted: cannot
    silently exempt a real active task on a parse failure."""
    task = {"status": "in_progress", "owner": "x", "metadata": "not-a-dict"}
    assert wl._lifecycle_relevant(task) is True


@pytest.mark.parametrize("agent_type", sorted(WAKE_EXCLUDED_AGENT_TYPES))
def test_lifecycle_relevant_wake_excluded_agenttype_with_corrupted_metadata(
    agent_type, tmp_path, monkeypatch
):
    """AgentType-carve-out hoist: a wake-excluded agentType task with
    non-dict metadata must STILL be excluded — return False. Pre-hoist
    behavior was True because the metadata-shape gate short-circuited
    to conservative-count BEFORE checking the agentType carve-out, so
    corrupted metadata accidentally promoted exempt agents to lifecycle-
    relevant tasks.

    POST-EMPTY-CARVE-OUT: WAKE_EXCLUDED_AGENT_TYPES is now empty, so
    this parametrize produces ZERO cells (vacuously true, no test
    bodies execute). The test is preserved as a structural placeholder:
    if a future PR re-populates WAKE_EXCLUDED_AGENT_TYPES, the
    parametrize automatically re-activates and pins the hoist invariant
    (agentType carve-out must be checked BEFORE metadata-shape gate).

    Distinct from the sibling
    test_lifecycle_relevant_counts_under_malformed_metadata which
    pins the conservative-count behavior for non-exempt teammates with
    corrupted metadata (still True)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-corrupt-meta"
    _write_team_config(tmp_path, team, [
        {"name": "session-secretary", "agentType": agent_type},
    ])
    task = {
        "status": "in_progress",
        "owner": "session-secretary",
        "metadata": "not-a-dict",
    }
    assert wl._lifecycle_relevant(task, team) is False, (
        f"Wake-excluded agentType {agent_type!r} with corrupted metadata must remain "
        f"exempt; pre-hoist behavior was True."
    )


def test_lifecycle_relevant_secretary_with_corrupted_metadata_now_counts_post_empty_carve_out(
    tmp_path, monkeypatch
):
    """POST-EMPTY-CARVE-OUT companion: a secretary-owned task with
    corrupted metadata is now lifecycle-relevant (returns True) because
    WAKE_EXCLUDED_AGENT_TYPES is empty AND the metadata-shape gate's
    conservative-count behavior takes effect. Pre-empty, this scenario
    short-circuited to False via the agentType carve-out hoist; post-
    empty, the carve-out is a no-op so secretary tasks are evaluated
    under the same conservative-count rule as any other teammate.

    Counter-test-by-revert: a future re-population of
    WAKE_EXCLUDED_AGENT_TYPES = {pact-secretary} flips this back to
    False; this test must be inverted in lockstep."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-corrupt-meta-empty"
    _write_team_config(tmp_path, team, [
        {"name": "session-secretary", "agentType": "pact-secretary"},
    ])
    task = {
        "status": "in_progress",
        "owner": "session-secretary",
        "metadata": "not-a-dict",
    }
    assert wl._lifecycle_relevant(task, team) is True, (
        "Post-empty WAKE_EXCLUDED_AGENT_TYPES: secretary with corrupted "
        "metadata must count (conservative-count rule applies). If False, "
        "the wake-side carve-out has been re-populated and this test "
        "must be inverted in lockstep."
    )


# ---------- count_active_tasks ----------

def test_count_active_tasks_returns_zero_on_empty_team_name(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert wl.count_active_tasks("") == 0
    assert wl.count_active_tasks(None) == 0  # type: ignore[arg-type]


def test_count_active_tasks_returns_zero_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert wl.count_active_tasks("ghost-team") == 0


def _stage_task(tmp_path: Path, team: str, task_id: str, **fields) -> None:
    d = tmp_path / ".claude" / "tasks" / team
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{task_id}.json").write_text(
        json.dumps({"id": task_id, **fields}), encoding="utf-8"
    )


def test_count_active_tasks_counts_pending_and_in_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-counts"
    _stage_task(tmp_path, team, "1", status="pending", owner="x")
    _stage_task(tmp_path, team, "2", status="in_progress", owner="y")
    _stage_task(tmp_path, team, "3", status="completed", owner="z")
    _stage_task(tmp_path, team, "4", status="deleted", owner="w")
    assert wl.count_active_tasks(team) == 2


def test_count_active_tasks_skips_signal_and_orphans(tmp_path, monkeypatch):
    """Signal tasks remain excluded via the metadata-layer signal-task
    carve-out (independent of the wake-side agentType carve-out and the
    teammate-owner check). Orphan-owner tasks (owner string doesn't
    match any current member) are now also excluded via the teammate-
    owner check.

    Setup: team config lists only `session-secretary` as a member. Tasks
    `real` (owner=x) and `sig` (owner=y) are orphans; `sec` (owner=
    session-secretary) is a known teammate.

    Pre-orphan-exclusion: count == 2 (real + sec; sig excluded only by
    signal-task carve-out). Post-orphan-exclusion: count == 1 (sec only;
    real is orphan-excluded, sig is signal-task-excluded, sec passes).

    Counter-test-by-revert: removing the teammate-owner check from
    `_lifecycle_relevant` would restore the count to 2 by re-counting
    `real` (the orphan)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-carveouts"
    # Team config records session-secretary with the privileged agentType.
    _write_team_config(tmp_path, team, [
        {"name": "session-secretary", "agentType": "pact-secretary"},
    ])
    _stage_task(tmp_path, team, "real", status="in_progress", owner="x")
    _stage_task(
        tmp_path, team, "sig",
        status="in_progress", owner="y",
        metadata={"completion_type": "signal", "type": "blocker"},
    )
    _stage_task(tmp_path, team, "sec", status="in_progress", owner="session-secretary")
    assert wl.count_active_tasks(team) == 1, (
        "Only the known teammate `sec` counts: `real` is excluded as "
        "orphan owner (not in members), `sig` is excluded by the "
        "signal-task carve-out."
    )


def test_count_active_tasks_session_secretary_now_counts_post_empty_carve_out(tmp_path, monkeypatch):
    """POST-EMPTY-CARVE-OUT: a session-secretary owned task is now
    COUNTED in the active tally (was: excluded via the wake-side
    agentType carve-out). Pre-empty, the carve-out at #682 made
    secretary tasks invisible to the wake mechanism; post-empty, the
    carve-out is a no-op (WAKE_EXCLUDED_AGENT_TYPES = frozenset()) so
    secretary tasks count like any other teammate.

    The Bug A secretary-window scenario (eager Teardown when secretary
    completes its first teachback) is now fixed at the count gate
    rather than the per-owner carve-out: count_active_tasks > 0
    prevents Teardown emit before the same-teammate-continuation
    predicate is consulted.

    The team-config agentType lookup mechanism still works (#682
    semantics preserved); only the wake-side carve-out's membership
    set has been emptied. SELF_COMPLETE_EXEMPT_AGENT_TYPES on the
    self-completion side still contains pact-secretary."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-prod-shape"
    _write_team_config(tmp_path, team, [
        {"name": "session-secretary", "agentType": "pact-secretary"},
    ])
    _stage_task(tmp_path, team, "memo", status="in_progress", owner="session-secretary")
    assert wl.count_active_tasks(team) == 1, (
        "Post-empty WAKE_EXCLUDED_AGENT_TYPES: secretary tasks must "
        "count toward the active tally. If 0, the wake-side carve-out "
        "has been re-populated and this test must be inverted in "
        "lockstep."
    )


def test_count_active_tasks_secretary_owner_without_agenttype_excluded_as_orphan(tmp_path, monkeypatch):
    """Trust-boundary defense, strengthened: owner='secretary' alone is
    not enough to count toward the wake tally — the team config must
    record a member with that exact name. A teammate spoofing
    owner='secretary' without a matching member is an orphan owner and
    is excluded from the active tally.

    Pre-orphan-exclusion: count == 1 (the spoof counted because the
    agentType-only carve-out fail-closed on missing-from-config but the
    teammate-owner check did not yet exist). Post-orphan-exclusion:
    count == 0 (the orphan owner is filtered before reaching the
    metadata-shape gate)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-spoof"
    _write_team_config(tmp_path, team, [
        {"name": "backend-coder-1", "agentType": "pact-backend-coder"},
    ])
    _stage_task(tmp_path, team, "spoof", status="in_progress", owner="secretary")
    assert wl.count_active_tasks(team) == 0


def test_count_active_tasks_skips_unparseable_files(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-malformed"
    d = tmp_path / ".claude" / "tasks" / team
    d.mkdir(parents=True)
    _stage_task(tmp_path, team, "ok", status="in_progress", owner="x")
    (d / "garbage.json").write_text("not valid json {{{", encoding="utf-8")
    assert wl.count_active_tasks(team) == 1


# ---------- Pure-never-raises property ----------

@pytest.mark.parametrize("bad_input", [
    None,
    "",
    "/etc",
    "team\x00with-null",
    "../../../escape",
    42,
])
def test_count_active_tasks_never_raises_on_bad_team_name(bad_input, tmp_path, monkeypatch):
    """Pure-function contract: any input shape exits with a count, never
    raises. Pinning this lets the redundant try/except at
    session_init.py:728-730 be removed in future cleanup."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Should not raise for any bad input.
    result = wl.count_active_tasks(bad_input)  # type: ignore[arg-type]
    assert isinstance(result, int)
    assert result >= 0


def test_lifecycle_relevant_never_raises():
    for bad in [None, [], {}, {"status": None}, {"metadata": None},
                {"status": "in_progress", "owner": None},
                {"status": "in_progress", "metadata": []}]:
        try:
            result = wl._lifecycle_relevant(bad)
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"_lifecycle_relevant raised on {bad!r}: {exc}")
        assert isinstance(result, bool)


# Adversarial-shape sweep across the (status, owner, metadata) cartesian
# product. Pins pure-never-raises for the predicate against arbitrary
# task shapes — required to gate the future try/except cleanup at
# session_init.py:728-730 (the gate depends on the WHOLE call chain
# being raise-free, not just the count_active_tasks entry point).
_BAD_STATUSES = [
    None, "", "kaboom", 42, 3.14, [], {}, True, b"bytes",
]
_BAD_OWNERS = [
    None, "", 42, [], {}, ["secretary"], {"name": "x"}, True,
]
_BAD_METADATAS = [
    "string", 42, [], True,
    {"completion_type": 42},  # wrong type for completion_type
    {"completion_type": "signal", "type": []},  # wrong type for type
    {"completion_type": "signal", "type": "blocker", "extra": object()},
    {"nested": {"deep": {"very": "deep"}}},
]


@pytest.mark.parametrize("status", _BAD_STATUSES)
def test_lifecycle_relevant_never_raises_on_adversarial_status(status):
    task = {"status": status, "owner": "x", "metadata": {}}
    try:
        result = wl._lifecycle_relevant(task)
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"_lifecycle_relevant raised on status={status!r}: {exc}")
    assert isinstance(result, bool)


@pytest.mark.parametrize("owner", _BAD_OWNERS)
def test_lifecycle_relevant_never_raises_on_adversarial_owner(owner):
    task = {"status": "in_progress", "owner": owner, "metadata": {}}
    try:
        result = wl._lifecycle_relevant(task)
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"_lifecycle_relevant raised on owner={owner!r}: {exc}")
    assert isinstance(result, bool)


@pytest.mark.parametrize("metadata", _BAD_METADATAS)
def test_lifecycle_relevant_never_raises_on_adversarial_metadata(metadata):
    task = {"status": "in_progress", "owner": "x", "metadata": metadata}
    try:
        result = wl._lifecycle_relevant(task)
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"_lifecycle_relevant raised on metadata={metadata!r}: {exc}")
    assert isinstance(result, bool)


@pytest.mark.parametrize("task", [
    {"status": "in_progress", "owner": ["secretary"], "metadata": []},
    {"status": [], "owner": {}, "metadata": "string"},
    {"status": None, "owner": None, "metadata": None},
    {"status": "pending", "owner": "kaboom", "metadata": {"completion_type": []}},
    {"status": 42, "owner": 99, "metadata": {"type": []}},
])
def test_lifecycle_relevant_never_raises_on_combined_adversarial_shapes(task):
    """Cross-field adversarial combinations — catches interactions
    between the status gate, owner-membership check, and metadata
    parse paths."""
    try:
        result = wl._lifecycle_relevant(task)
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"_lifecycle_relevant raised on {task!r}: {exc}")
    assert isinstance(result, bool)


# ---------- Dotfile exclusion (te-M2) ----------

def test_count_active_tasks_excludes_dotfile_prefixed_json(tmp_path, monkeypatch):
    """Dotfile-prefixed `.fake_task.json` files planted in the team
    directory must not influence the count. (Path.glob('*.json') matches
    dotfiles on POSIX, contra a common assumption — the explicit
    `name.startswith('.')` guard in iter_team_task_jsons is what excludes
    them.) Without that guard, an attacker who can write a single
    dotfile into the team's tasks dir could inflate the active-tasks
    count and suppress Teardown emit."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-dotfile"
    d = tmp_path / ".claude" / "tasks" / team
    d.mkdir(parents=True)
    # One legitimate active task.
    _stage_task(tmp_path, team, "real", status="in_progress", owner="x")
    # Dotfile-prefixed shape that would be active if matched.
    (d / ".fake_task.json").write_text(
        json.dumps({"id": "fake", "status": "in_progress", "owner": "y"}),
        encoding="utf-8",
    )
    # Dotfile-only file (pure leading-dot).
    (d / ".hidden.json").write_text(
        json.dumps({"id": "hidden", "status": "in_progress", "owner": "z"}),
        encoding="utf-8",
    )
    assert wl.count_active_tasks(team) == 1


# ---------- Symlink-escape defense (be-B1) ----------

def test_count_active_tasks_returns_zero_when_team_dir_symlink_escapes_root(tmp_path, monkeypatch):
    """Symlink-escape defense: even if `team_name` passes the safe-path
    allowlist, a symlink at ~/.claude/tasks/{team_name} pointing outside
    tasks_root must be detected via resolve()+relative_to and counted
    as 0. Mirrors session_end.py::cleanup_wake_registry's defense."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    tasks_root = tmp_path / ".claude" / "tasks"
    tasks_root.mkdir(parents=True)
    # Outside tasks_root: a directory with a real active task.
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "real.json").write_text(
        json.dumps({"id": "real", "status": "in_progress", "owner": "x"}),
        encoding="utf-8",
    )
    # team_name is allowlist-safe, but the team_dir symlinks outside.
    team = "team-sym"
    (tasks_root / team).symlink_to(outside, target_is_directory=True)
    # Without symlink-escape defense the count would be 1; with it, 0.
    assert wl.count_active_tasks(team) == 0


# ---------- Per-file symlink defense (be-F1) ----------

def test_count_active_tasks_skips_symlinked_task_files(tmp_path, monkeypatch):
    """Per-file symlink defense (be-F1): even when the team_dir is
    legitimate, individual task-file entries that are symlinks must be
    skipped. The team_dir-level resolve()+relative_to defense catches
    a malicious team_dir, but a regular team_dir with a planted symlink
    inside (e.g., `~/.claude/tasks/team-x/task-1.json -> /etc/passwd`)
    would otherwise be read by iter_team_task_jsons. Skip silently — the
    platform task system writes only regular files.

    Counter-test-by-revert: removing the per-file is_symlink guard
    would let a planted symlink contribute to the count."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    team = "team-symfile"
    d = tmp_path / ".claude" / "tasks" / team
    d.mkdir(parents=True)
    # One legitimate active task as a regular file.
    _stage_task(tmp_path, team, "real", status="in_progress", owner="x")
    # External payload that the symlink will point at (active-shaped).
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    target = elsewhere / "planted.json"
    target.write_text(
        json.dumps({"id": "planted", "status": "in_progress", "owner": "y"}),
        encoding="utf-8",
    )
    # Symlinked task file inside the team dir — must be skipped.
    (d / "planted.json").symlink_to(target)
    assert wl.count_active_tasks(team) == 1
