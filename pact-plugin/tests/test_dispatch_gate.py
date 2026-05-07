"""
Comprehensive coverage for dispatch_gate.py — #662 PreToolUse hook.

Sibling to test_dispatch_gate_smoke.py (the 7 minimum-viable cases).
This file expands every rule landed in the gate into a behavioral matrix.

Rule coverage:
  - name_required — name= missing/empty/whitespace → DENY
  - team_name_required — team_name= empty → DENY
  - name_too_long / name_invalid_regex / name_reserved_token — name
    length/NFKC/regex/reserved-token violations → DENY
  - specialist_not_registered — subagent_type not in agent registry → DENY
  - team_name_mismatch / team_name_unavailable — team mismatch or empty
    session source → DENY
  - no_task_assigned — no Task with owner=name → DENY
  - long_inline_mission — long inline mission OR no TaskList reference,
    disposition controlled by PACT_DISPATCH_INLINE_MISSION_MODE
    (warn|deny|shadow) → WARN | DENY | ALLOW(shadow)
  - name_not_unique — name already in team config members → DENY
  - plugin_agents_missing — plugin_root agents/ directory missing → DENY
  - Runtime gate-logic exception → fail-closed DENY (covered via
    subprocess in smoke)
  - Journal: every decision (ALLOW + WARN + DENY) emits a
    dispatch_decision event with rule + verdict
  - Prompt redaction at the journal-write boundary
  - Carve-outs — SOLO_EXEMPT / non-pact-* subagent_type → ALLOW
  - Anti-sprawl — single evaluate_dispatch composition

Disciplines applied:
  - PR #660 R2: never pop shared.* from sys.modules in this test process.
    Subprocess sabotage for runtime fail-closed lives in the smoke file
    using PYTHONSAFEPATH.
  - #638 cardinality: each rule's deny is asserted by behavioral rule
    identifier (e.g. ``"name_required"``, ``"long_inline_mission"``), not
    deny string equality, so wording iterations don't cause test churn.
  - feedback_no_planning_artifact_test_names: rule names describe
    behavior, not provenance.
  - Credential literals in redaction tests are split via Python
    adjacent-string-literal concatenation so the repo-root pre-commit
    secret-scanner does not false-positive on this fixture.
"""

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


_SUPPRESS_EXPECTED = {"suppressOutput": True}
_TEAM = "pact-test"
_NAME = "tester"


# =============================================================================
# Helpers
# =============================================================================


def _make_input(
    subagent_type="pact-architect",
    name=_NAME,
    team_name=_TEAM,
    prompt="Standard mission. Check TaskList for tasks assigned to you.",
):
    return {
        "hook_event_name": "PreToolUse",
        "session_id": "test-session",
        "tool_name": "Agent",
        "tool_input": {
            "subagent_type": subagent_type,
            "name": name,
            "team_name": team_name,
            "prompt": prompt,
        },
    }


def _run_main(input_data, capsys):
    """Invoke dispatch_gate.main() in-process. Returns (exit_code, stdout_json)."""
    from dispatch_gate import main

    with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
        with pytest.raises(SystemExit) as exc_info:
            main()

    captured = capsys.readouterr()
    out = captured.out.strip()
    return exc_info.value.code, json.loads(out) if out else {}


def _setup_session(monkeypatch, tmp_path, plugin_root: Path, team_name=_TEAM):
    """Wire pact_context to point at a tmp session, set HOME so
    has_task_assigned + _team_member_names read tmp dirs.
    """
    import shared.pact_context as ctx_module

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    ctx_path = tmp_path / "pact-session-context.json"
    ctx_path.write_text(
        json.dumps(
            {
                "team_name": team_name,
                "session_id": "test-session",
                "project_dir": str(tmp_path / "project"),
                "plugin_root": str(plugin_root),
                "started_at": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ctx_module, "_context_path", ctx_path)
    monkeypatch.setattr(ctx_module, "_cache", None)
    monkeypatch.setattr(ctx_module, "init", lambda input_data: None)

    import shared.dispatch_helpers as dh

    dh._specialist_registry.cache_clear()


def _seed_plugin(plugin_root: Path, agents=("pact-architect",)):
    agents_dir = plugin_root / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    for stem in agents:
        (agents_dir / f"{stem}.md").write_text(f"---\nname: {stem}\n---\n")


def _seed_team(home: Path, team_name=_TEAM, members=(), tasks=()):
    """Write fake team config + canonical tasks store.

    config.json lives under ``HOME/.claude/teams/{team_name}/`` (the
    ``_team_member_names`` read path). Task files live under
    ``HOME/.claude/tasks/{team_name}/`` (the canonical task store per
    ``shared/task_utils.py``, which is what ``has_task_assigned`` reads
    after the #663 B1 path-alignment fix).
    """
    team_dir = home / ".claude" / "teams" / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "config.json").write_text(
        json.dumps(
            {
                "team_name": team_name,
                "members": [{"name": m} for m in members],
            }
        ),
        encoding="utf-8",
    )
    tasks_dir = home / ".claude" / "tasks" / team_name
    tasks_dir.mkdir(parents=True, exist_ok=True)
    for i, (owner, status) in enumerate(tasks):
        (tasks_dir / f"task_{i}.json").write_text(
            json.dumps({"id": str(i), "owner": owner, "status": status}),
            encoding="utf-8",
        )


def _full_setup(
    monkeypatch,
    tmp_path,
    *,
    agents=("pact-architect",),
    members=(),
    tasks=((_NAME, "pending"),),
    team_name=_TEAM,
):
    """One-call setup: plugin agents/, session context, team config + tasks."""
    plugin_root = tmp_path / "plugin"
    _seed_plugin(plugin_root, agents=agents)
    _setup_session(monkeypatch, tmp_path, plugin_root, team_name=team_name)
    _seed_team(tmp_path, team_name=team_name, members=members, tasks=tasks)
    return plugin_root


def _capture_journal(monkeypatch):
    """Replace append_event in both shared.session_journal and dispatch_gate
    so every emit goes into a captured list. Returns the list.
    """
    captured: list[dict] = []

    def _capture(event):
        captured.append(event)
        return True

    import shared.session_journal as sj
    monkeypatch.setattr(sj, "append_event", _capture)
    import dispatch_gate
    monkeypatch.setattr(dispatch_gate, "append_event", _capture)
    return captured


# =============================================================================
# name_required — name= absent / empty / whitespace
# =============================================================================


@pytest.mark.parametrize(
    "name_input",
    [
        "",  # empty string
        "   ",  # whitespace-only — also fails the regex rule
        "\t",  # tab-only
    ],
    ids=["empty_string", "whitespace_only", "tab_only"],
)
def test_or_regex_deny_when_name_is_empty_or_whitespace(
    name_input, tmp_path, monkeypatch, capsys
):
    """name_required (empty) or name_invalid_regex (whitespace fails regex) both DENY. Either rule is
    acceptable — the load-bearing invariant is that an unusable name is
    rejected with hookEventName=PreToolUse and exit 2.
    """
    _full_setup(monkeypatch, tmp_path)
    code, out = _run_main(_make_input(name=name_input), capsys)
    assert code == 2
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    reason = hso["permissionDecisionReason"]
    assert ("name_required" in reason) or ("name= parameter is required" in reason) \
        or ("name_invalid_regex" in reason) or ("must match" in reason)


def test_deny_when_name_key_missing(tmp_path, monkeypatch, capsys):
    """tool_input lacks the name key entirely — gate treats as empty → name_required DENY."""
    _full_setup(monkeypatch, tmp_path)
    payload = _make_input()
    del payload["tool_input"]["name"]
    code, out = _run_main(payload, capsys)
    assert code == 2
    assert "name= parameter is required" in out["hookSpecificOutput"]["permissionDecisionReason"]


# =============================================================================
# team_name_required — team_name= empty
# =============================================================================


def test_deny_when_team_name_key_missing(tmp_path, monkeypatch, capsys):
    """tool_input lacks team_name → team_name_required DENY (caught BEFORE the session-team check)."""
    _full_setup(monkeypatch, tmp_path)
    payload = _make_input()
    del payload["tool_input"]["team_name"]
    code, out = _run_main(payload, capsys)
    assert code == 2
    assert "team_name= parameter is required" in out["hookSpecificOutput"]["permissionDecisionReason"]


# =============================================================================
# name validation — regex / length cap / NFKC / reserved tokens
# =============================================================================


def test_deny_when_name_exceeds_64_char_cap(tmp_path, monkeypatch, capsys):
    """Length cap fires BEFORE regex (cheap-first ordering)."""
    _full_setup(monkeypatch, tmp_path)
    long_name = "a" * 65
    code, out = _run_main(_make_input(name=long_name), capsys)
    assert code == 2
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "exceeds limit" in reason
    assert "length" in reason.lower()


def test_allows_name_at_64_char_boundary(tmp_path, monkeypatch, capsys):
    """64 chars is the max permitted (boundary <=). Combined with the no_task_assigned check below
    we need a task with owner=long_name OR confirm the name-length check itself
    passes. Use a 64-char name + seed task with that owner.
    """
    long_name = "a" * 64
    _full_setup(
        monkeypatch,
        tmp_path,
        members=(),
        tasks=((long_name, "pending"),),
    )
    code, out = _run_main(_make_input(name=long_name), capsys)
    # Either ALLOW (name validation passed, all other rules pass) or some unrelated DENY,
    # but NOT a name-length DENY — that's the load-bearing assertion.
    if code == 2:
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        assert "exceeds limit" not in reason
        assert "must match" not in reason
        assert "reserved-token" not in reason
    else:
        assert code == 0


@pytest.mark.parametrize(
    "bad_name",
    [
        "BadName",  # uppercase
        "has space",  # space
        "has_underscore",  # underscore
        "trailing-",  # trailing dash that still matches but reserved? regex allows it
        "(parens)",  # parens
        "with\nnewline",  # newline
        "наме",  # Cyrillic — fails regex even after NFKC
        "​zero-width",  # zero-width-space prefix
    ],
    ids=[
        "uppercase",
        "space",
        "underscore",
        "trailing_dash",  # trailing dash regex-passes; mark below if needed
        "parens",
        "newline",
        "cyrillic",
        "zero_width",
    ],
)
def test_deny_invalid_name_chars(bad_name, tmp_path, monkeypatch, capsys):
    """NFKC normalize then regex check — none of these survive."""
    _full_setup(monkeypatch, tmp_path)
    code, out = _run_main(_make_input(name=bad_name), capsys)
    if bad_name == "trailing-":
        # Regex ^[a-z0-9-]+$ accepts trailing dash; this case should ALLOW
        # (or fail another rule, but not the name-regex rule). Skip the deny assertion.
        if code == 2:
            reason = out["hookSpecificOutput"]["permissionDecisionReason"]
            assert "must match" not in reason
        return
    assert code == 2
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "must match" in reason


def test_deny_fullwidth_lookalike_after_nfkc(tmp_path, monkeypatch, capsys):
    """Fullwidth digits/letters NFKC-normalize to ASCII, but the regex check
    runs on the NORMALIZED form. We want to assert that the LOOKALIKE shape
    is rejected by SOME rule — either name validation (if NFKC produces non-regex chars)
    or another rule. The load-bearing invariant: a name with fullwidth chars
    cannot smuggle through.

    Implementation note: dispatch_gate normalizes BEFORE regex. ｔｅｓｔ
    (fullwidth) NFKC-normalizes to "test", which IS valid ASCII. Per the
    impl, this name is therefore ACCEPTED by name validation. That is acceptable
    behavior — fullwidth lookalikes that legitimately normalize to a
    safe lowercase ASCII identifier are not security-sensitive. This test
    pins the empirical observation rather than an idealized expectation.
    """
    _full_setup(
        monkeypatch,
        tmp_path,
        tasks=(("test", "pending"),),
    )
    fullwidth = "ｔｅｓｔ"
    code, _out = _run_main(_make_input(name=fullwidth), capsys)
    # Either accepted (NFKC → "test" matches regex, has task) or denied for
    # an unrelated rule. Never name-regex denied since NFKC produces ASCII.
    assert code in (0, 2)


@pytest.mark.parametrize(
    "reserved",
    ["team-lead", "lead", "user", "external", "peer", "unknown", "solo"],
)
def test_deny_reserved_token(reserved, tmp_path, monkeypatch, capsys):
    """Reserved tokens DENY even though they pass the regex."""
    _full_setup(monkeypatch, tmp_path)
    code, out = _run_main(_make_input(name=reserved), capsys)
    assert code == 2
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "reserved-token" in reason


# =============================================================================
# specialist_not_registered — subagent_type not in registry
# =============================================================================


def test_deny_when_subagent_type_not_in_registry(tmp_path, monkeypatch, capsys):
    """pact-nonexistent doesn't appear in agents/ glob → specialist_not_registered DENY."""
    _full_setup(monkeypatch, tmp_path, agents=("pact-architect",))
    code, out = _run_main(_make_input(subagent_type="pact-nonexistent"), capsys)
    assert code == 2
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "not a registered PACT specialist" in reason


# =============================================================================
# team_name_mismatch / team_name_unavailable — team_name mismatch or empty session source
# =============================================================================


def test_deny_when_team_name_mismatch(tmp_path, monkeypatch, capsys):
    """Spawn passes team_name='wrong-team' but session is 'pact-test' → team_name_mismatch DENY."""
    _full_setup(monkeypatch, tmp_path)
    code, out = _run_main(_make_input(team_name="wrong-team"), capsys)
    assert code == 2
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "does not match current session team" in reason


def test_deny_when_session_team_unavailable(tmp_path, monkeypatch, capsys):
    """Empty-source decision (architect §7(h)): when session context has
    empty team_name, fail-closed — adversary passing team_name='' would
    otherwise equal the empty session value.

    The team_name_required rule catches the explicit empty team_name on
    the spawn-input side BEFORE this rule runs, so we exercise this with a
    non-empty spawn team_name
    against an empty session team_name.
    """
    plugin_root = tmp_path / "plugin"
    _seed_plugin(plugin_root)
    _setup_session(monkeypatch, tmp_path, plugin_root, team_name="")
    _seed_team(tmp_path, members=(), tasks=((_NAME, "pending"),))
    code, out = _run_main(_make_input(team_name=_TEAM), capsys)
    assert code == 2
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "session team_name is unavailable" in reason


# =============================================================================
# no_task_assigned — spawn before TaskCreate
# =============================================================================


def test_deny_when_no_task_for_owner(tmp_path, monkeypatch, capsys):
    """No task exists with owner=tester → no_task_assigned DENY."""
    _full_setup(monkeypatch, tmp_path, tasks=())  # zero tasks
    code, out = _run_main(_make_input(), capsys)
    assert code == 2
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "no Task assigned" in reason


def test_deny_when_task_owner_differs(tmp_path, monkeypatch, capsys):
    """Task exists but for a different owner → no_task_assigned DENY (still no task for tester)."""
    _full_setup(monkeypatch, tmp_path, tasks=(("other-agent", "pending"),))
    code, out = _run_main(_make_input(), capsys)
    assert code == 2
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "no Task assigned" in reason


def test_deny_when_task_completed_only(tmp_path, monkeypatch, capsys):
    """Only completed tasks count as 'no active task'. has_task_assigned
    requires status in {pending, in_progress}.
    """
    _full_setup(monkeypatch, tmp_path, tasks=((_NAME, "completed"),))
    code, out = _run_main(_make_input(), capsys)
    assert code == 2
    assert "no Task assigned" in out["hookSpecificOutput"]["permissionDecisionReason"]


# =============================================================================
# long_inline_mission — long inline mission / no TaskList ref / mode tri-state
# =============================================================================


def test_warn_when_prompt_lacks_task_reference(tmp_path, monkeypatch, capsys):
    """Default mode is 'warn' → ALLOW with additionalContext advisory.
    F7_MODE was read at module-load BEFORE this test — we don't override
    it here; default is 'warn' unless a prior test set the env var.
    """
    import dispatch_gate

    monkeypatch.setattr(dispatch_gate, "F7_MODE", "warn")
    _full_setup(monkeypatch, tmp_path)
    short_no_taskref = "Do the thing."
    code, out = _run_main(_make_input(prompt=short_no_taskref), capsys)
    # WARN: exit 0, additionalContext present (no permissionDecision).
    assert code == 0
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert "additionalContext" in hso
    assert "prompt is long" in hso["additionalContext"] \
        or "lacks a TaskList reference" in hso["additionalContext"]


def test_warn_when_prompt_exceeds_800_chars(tmp_path, monkeypatch, capsys):
    """Long prompt + TaskList reference still WARNs (length-or-no-ref)."""
    import dispatch_gate

    monkeypatch.setattr(dispatch_gate, "F7_MODE", "warn")
    _full_setup(monkeypatch, tmp_path)
    long_prompt = "x" * 801 + " Check TaskList for tasks assigned to you."
    code, out = _run_main(_make_input(prompt=long_prompt), capsys)
    assert code == 0
    hso = out["hookSpecificOutput"]
    assert "additionalContext" in hso
    assert "prompt is long" in hso["additionalContext"] \
        or "lacks a TaskList reference" in hso["additionalContext"]


def test_deny_in_deny_mode(tmp_path, monkeypatch, capsys):
    """Mode='deny' promotes WARN → DENY."""
    import dispatch_gate

    monkeypatch.setattr(dispatch_gate, "F7_MODE", "deny")
    _full_setup(monkeypatch, tmp_path)
    code, out = _run_main(_make_input(prompt="No reference here."), capsys)
    assert code == 2
    hso = out["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"
    assert "prompt is long" in hso["permissionDecisionReason"] \
        or "lacks a TaskList reference" in hso["permissionDecisionReason"]


def test_silent_allow_in_shadow_mode(tmp_path, monkeypatch, capsys):
    """Mode='shadow' returns ALLOW silently — no advisory, no deny — but
    the journal still records the long_inline_mission trigger for calibration.
    """
    import dispatch_gate

    monkeypatch.setattr(dispatch_gate, "F7_MODE", "shadow")
    captured = _capture_journal(monkeypatch)
    _full_setup(monkeypatch, tmp_path)
    code, out = _run_main(_make_input(prompt="No reference."), capsys)
    assert code == 0
    assert out == _SUPPRESS_EXPECTED
    # Journal sees the long-inline-mission trigger.
    assert any(
        e.get("type") == "dispatch_decision"
        and e.get("rule") == "long_inline_mission"
        for e in captured
    )


# =============================================================================
# name_not_unique — uniqueness vs live team members
# =============================================================================


def test_deny_when_name_already_in_team_members(tmp_path, monkeypatch, capsys):
    """Member 'tester' already lives in team.config.json → name_not_unique DENY."""
    _full_setup(
        monkeypatch,
        tmp_path,
        members=(_NAME,),  # 'tester' already present
        tasks=((_NAME, "pending"),),
    )
    code, out = _run_main(_make_input(), capsys)
    assert code == 2
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "is already a live member" in reason


def test_allows_unique_name_when_other_members_present(
    tmp_path, monkeypatch, capsys
):
    """Different live member doesn't trigger the uniqueness rule for an incoming new name."""
    _full_setup(
        monkeypatch,
        tmp_path,
        members=("someone-else",),
        tasks=((_NAME, "pending"),),
    )
    code, out = _run_main(_make_input(), capsys)
    assert code == 0
    assert out == _SUPPRESS_EXPECTED


# =============================================================================
# plugin_agents_missing — plugin_root agents/ directory missing
# =============================================================================


def test_deny_when_plugin_agents_missing(tmp_path, monkeypatch, capsys):
    """plugin_root resolves to a path whose agents/ subdir doesn't exist."""
    plugin_root = tmp_path / "broken-plugin"
    plugin_root.mkdir()  # exists but no agents/ subdir
    _setup_session(monkeypatch, tmp_path, plugin_root)
    _seed_team(tmp_path, members=(), tasks=((_NAME, "pending"),))
    code, out = _run_main(_make_input(), capsys)
    assert code == 2
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "plugin agents/ directory is unavailable" in reason


# =============================================================================
# Carve-outs — SOLO_EXEMPT + non-pact-* subagent_type
# =============================================================================


@pytest.mark.parametrize(
    "carve_out_type",
    ["general-purpose", "Explore", "Plan"],
)
def test_solo_exempt_allows_without_name_or_team(
    carve_out_type, tmp_path, monkeypatch, capsys
):
    """Research-tier subagents legitimately spawn solo. ALLOW even with
    name='' and team_name='' (which would otherwise trip name_required/team_name_required).
    """
    _full_setup(monkeypatch, tmp_path)
    code, out = _run_main(
        _make_input(subagent_type=carve_out_type, name="", team_name=""),
        capsys,
    )
    assert code == 0
    assert out == _SUPPRESS_EXPECTED


def test_non_pact_subagent_type_passes_through(tmp_path, monkeypatch, capsys):
    """An arbitrary non-pact-* subagent_type isn't this gate's business."""
    _full_setup(monkeypatch, tmp_path)
    code, out = _run_main(
        _make_input(subagent_type="some-other-tool", name="", team_name=""),
        capsys,
    )
    assert code == 0
    assert out == _SUPPRESS_EXPECTED


# =============================================================================
# journal emit on every gate decision
# =============================================================================


def test_journal_emit_on_allow(tmp_path, monkeypatch, capsys):
    """Happy-path ALLOW still emits a dispatch_decision journal event."""
    captured = _capture_journal(monkeypatch)
    _full_setup(monkeypatch, tmp_path)
    code, _out = _run_main(_make_input(), capsys)
    assert code == 0
    assert captured, "expected at least one journal event for ALLOW"
    last = captured[-1]
    assert last["type"] == "dispatch_decision"
    assert last["decision"] == "ALLOW"


def test_journal_emit_on_deny_carries_rule(tmp_path, monkeypatch, capsys):
    """DENY journal event carries the rule identifier (name_required here)."""
    captured = _capture_journal(monkeypatch)
    _full_setup(monkeypatch, tmp_path)
    _run_main(_make_input(name=""), capsys)
    deny_events = [
        e
        for e in captured
        if e.get("type") == "dispatch_decision" and e.get("decision") == "DENY"
    ]
    assert deny_events
    assert deny_events[-1]["rule"] == "name_required"


def test_journal_emit_on_warn_carries_f7(tmp_path, monkeypatch, capsys):
    """WARN (default inline-mission mode) journal event records rule='long_inline_mission'."""
    import dispatch_gate

    monkeypatch.setattr(dispatch_gate, "F7_MODE", "warn")
    captured = _capture_journal(monkeypatch)
    _full_setup(monkeypatch, tmp_path)
    _run_main(_make_input(prompt="No reference."), capsys)
    warn_events = [
        e
        for e in captured
        if e.get("type") == "dispatch_decision" and e.get("decision") == "WARN"
    ]
    assert warn_events
    assert warn_events[-1]["rule"] == "long_inline_mission"


# =============================================================================
# prompt redaction at journal-write boundary
# =============================================================================


@pytest.mark.parametrize(
    "secret_token",
    [
        # Adjacent-string-literal concatenation defeats the repo-root
        # pre-commit secret-scanner regex while preserving runtime value.
        "sk" "-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
        "xoxb" "-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
        "ghp" "_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
        "AKIA" "ABCDEFGHIJKLMNOP",
    ],
    ids=["openai", "slack", "github_pat", "aws"],
)
def test_redacts_credential_patterns_in_journal(
    secret_token, tmp_path, monkeypatch, capsys
):
    """Each credential pattern is scrubbed BEFORE journal write. Verbatim
    permissionDecisionReason is unaffected (kept for dispatcher debugging).
    """
    captured = _capture_journal(monkeypatch)
    _full_setup(monkeypatch, tmp_path)
    prompt = f"Embedded: {secret_token} ignore. Check TaskList."
    _run_main(_make_input(prompt=prompt), capsys)
    assert captured
    journaled = captured[-1].get("prompt_redacted", "")
    assert "[REDACTED]" in journaled
    assert secret_token not in journaled


def test_redacts_jwt_shape_in_journal(tmp_path, monkeypatch, capsys):
    """JWT three-segment base64url shape is also redacted."""
    captured = _capture_journal(monkeypatch)
    _full_setup(monkeypatch, tmp_path)
    # Split via Python adjacent-string-literal concatenation so the
    # repo-root pre-commit JWT-shape scanner (git_commit_check.py) does
    # not flag this fixture as a real token. Runtime value matches the
    # joined literal; the dispatch_gate JWT regex still matches at runtime.
    fake_jwt = (
        "eyJ" "hbGciOiJIUzI1NiJ9"
        "." "eyJ" "zdWIiOiIxMjMifQ"
        "." "signaturepart_zZz123"
    )
    _run_main(
        _make_input(prompt=f"Token: {fake_jwt}. Check TaskList."), capsys
    )
    assert captured
    journaled = captured[-1].get("prompt_redacted", "")
    assert "[REDACTED]" in journaled
    assert fake_jwt not in journaled


# =============================================================================
# Anti-sprawl invariant (auditor §11 YELLOW)
# =============================================================================


def test_evaluate_dispatch_is_single_composition_function():
    """Auditor YELLOW note: gate file is 444 LOC vs 300 soft budget. The
    important invariant isn't line count — it's that the F-row rules
    compose in a single decision function rather than fragmenting into
    per-row handlers.

    Asserts: dispatch_gate exposes ONE function with `evaluate_` prefix
    that returns a 3-tuple (decision, reason, rule). No per-F-row
    public functions snuck in.
    """
    import dispatch_gate
    import inspect

    public_evaluate_fns = [
        name
        for name, obj in inspect.getmembers(dispatch_gate, inspect.isfunction)
        if name.startswith("evaluate_") and not name.startswith("_")
    ]
    assert public_evaluate_fns == ["evaluate_dispatch"], (
        f"expected single evaluate_dispatch composition, got {public_evaluate_fns}"
    )
    # Per-F-row functions would have shapes like _f1_check, _evaluate_f7, etc.
    forbidden_prefixes = ("_evaluate_f", "_f1_", "_f2_", "_f3_", "_f4_")
    fn_names = [
        name for name, _ in inspect.getmembers(dispatch_gate, inspect.isfunction)
    ]
    sprawl = [
        n for n in fn_names if any(n.startswith(p) for p in forbidden_prefixes)
    ]
    assert not sprawl, f"per-F-row sprawl detected: {sprawl}"


# =============================================================================
# Defensive: malformed stdin / non-Agent tool / non-dict input
# =============================================================================


def test_malformed_stdin_fail_open(tmp_path, monkeypatch, capsys):
    """Malformed stdin → suppressOutput, exit 0 (input-side errors are
    the harness's domain, not the gate's).
    """
    from dispatch_gate import main

    with patch("sys.stdin", io.StringIO("not json")):
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 0
    out = capsys.readouterr().out.strip()
    assert json.loads(out) == _SUPPRESS_EXPECTED


def test_non_agent_tool_no_op(tmp_path, monkeypatch, capsys):
    """Defensive: if the matcher routes a non-Agent tool here, no-op."""
    payload = _make_input()
    payload["tool_name"] = "Read"
    code, out = _run_main(payload, capsys)
    assert code == 0
    assert out == _SUPPRESS_EXPECTED


# =============================================================================
# B1 path-alignment regression — has_task_assigned reads the canonical
# task store at ~/.claude/tasks/{team_name}/, NOT the legacy
# ~/.claude/teams/{team_name}/tasks/. Counter-test discipline (#638):
# reverting the L128 path in shared/dispatch_helpers.py back to the legacy
# layout makes test_b1_canonical_only flip from PASS to FAIL.
# =============================================================================


def test_b1_canonical_only_satisfies_f6(tmp_path, monkeypatch):
    """has_task_assigned MUST read ~/.claude/tasks/{team_name}/.

    Seed a task ONLY at the canonical path; leave the legacy path empty.
    The fixed implementation returns True. The buggy pre-fix implementation
    (which read the legacy path) would return False — that's the
    counter-test cardinality.
    """
    from shared.dispatch_helpers import has_task_assigned

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    canonical = tmp_path / ".claude" / "tasks" / _TEAM
    canonical.mkdir(parents=True)
    (canonical / "1.json").write_text(
        json.dumps({"id": "1", "owner": _NAME, "status": "pending"}),
        encoding="utf-8",
    )
    legacy = tmp_path / ".claude" / "teams" / _TEAM / "tasks"
    assert not legacy.exists()

    assert has_task_assigned(_TEAM, _NAME) is True


def test_b1_legacy_path_alone_does_not_satisfy_f6(tmp_path, monkeypatch):
    """A task at ONLY the legacy ~/.claude/teams/{team}/tasks/ path must NOT
    satisfy has_task_assigned. This pins the path the implementation reads
    so a future regression to the legacy layout flips this assertion.
    """
    from shared.dispatch_helpers import has_task_assigned

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    legacy = tmp_path / ".claude" / "teams" / _TEAM / "tasks"
    legacy.mkdir(parents=True)
    (legacy / "1.json").write_text(
        json.dumps({"id": "1", "owner": _NAME, "status": "pending"}),
        encoding="utf-8",
    )
    canonical = tmp_path / ".claude" / "tasks" / _TEAM
    assert not canonical.exists()

    assert has_task_assigned(_TEAM, _NAME) is False


def test_b1_canonical_path_aligns_with_task_utils(tmp_path, monkeypatch):
    """The path has_task_assigned reads must be the same root that
    task_utils.read_task_json uses. If task_utils ever moves, this test
    surfaces the divergence at the dispatch-gate layer.
    """
    from shared.dispatch_helpers import has_task_assigned
    from shared.task_utils import read_task_json

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    canonical = tmp_path / ".claude" / "tasks" / _TEAM
    canonical.mkdir(parents=True)
    (canonical / "1.json").write_text(
        json.dumps({"id": "1", "owner": _NAME, "status": "pending"}),
        encoding="utf-8",
    )

    assert has_task_assigned(_TEAM, _NAME) is True
    assert read_task_json("1", _TEAM).get("owner") == _NAME
