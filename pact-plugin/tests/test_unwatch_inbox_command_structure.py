"""
Structural invariants for pact-plugin/commands/unwatch-inbox.md (Teardown role).

File-parsing assertions only — no command execution. Pin section
presence, F6 'ignoring not-found errors' literal, best-effort framing,
Teardown ordering load-bearing, missing_ok=True usage, and the
cross-link to watch-inbox.

Cycle 4 audit allocation per Task #52:
- F6 (TaskStop tolerates not-found) → unwatch-inbox
- best-effort framing → unwatch-inbox
- 'wake mechanism is opportunistic' brief mention → unwatch-inbox
"""

from pathlib import Path

import pytest

CMD_PATH = (
    Path(__file__).resolve().parent.parent
    / "commands"
    / "unwatch-inbox.md"
)


@pytest.fixture(scope="module")
def cmd_text() -> str:
    return CMD_PATH.read_text(encoding="utf-8")


def _section_body(text: str, header: str) -> str:
    """Return the body between `header` and the next same-or-higher level header."""
    lines = text.splitlines()
    level = len(header) - len(header.lstrip("#"))
    start = None
    for i, line in enumerate(lines):
        if line.strip() == header:
            start = i + 1
            break
    if start is None:
        return ""
    end = len(lines)
    for j in range(start, len(lines)):
        line = lines[j].strip()
        if line.startswith("#"):
            this_level = len(line) - len(line.lstrip("#"))
            if this_level <= level:
                end = j
                break
    return "\n".join(lines[start:end])


# ---------- Frontmatter / file-level invariants ----------

def test_command_file_exists():
    assert CMD_PATH.exists()


def test_frontmatter_has_description(cmd_text):
    """Slash-commands use filename as identity (no `name:` field).
    Frontmatter must carry at least a description."""
    assert cmd_text.startswith("---\n")
    head, _, _ = cmd_text[4:].partition("\n---\n")
    assert "description:" in head
    assert "inbox-watch" in head or "unwatch-inbox" in head or "Tear down" in head


def test_command_body_under_compaction_budget(cmd_text):
    # ~50L target per Task #52; 100-line ceiling for headroom.
    line_count = len(cmd_text.splitlines())
    assert line_count <= 100, (
        f"unwatch-inbox.md has {line_count} lines, exceeds 100 cap"
    )


# ---------- Section-presence invariants ----------

REQUIRED_SECTIONS = [
    "## Overview",
    "## When to Invoke",
    "## Operation",
    "## Teardown Block",
    "## Failure Modes",
    "## Verification",
    "## References",
]


@pytest.mark.parametrize("section", REQUIRED_SECTIONS)
def test_required_section_present(cmd_text, section):
    assert any(line.strip() == section for line in cmd_text.splitlines()), (
        f"missing required section header: {section}"
    )


def test_no_arm_or_recovery_subsection(cmd_text):
    """Cycle 4: command IS the operation, no Arm/Recovery sub-sections.
    unwatch-inbox is the Teardown command; Arm logic lives in
    watch-inbox.md."""
    body = _section_body(cmd_text, "## Operation")
    assert "### Arm" not in body
    assert "### Recovery" not in body


# ---------- F6: TaskStop tolerance literal ----------

def test_f6_teardown_block_contains_ignoring_not_found(cmd_text):
    """F6 invariant: TaskStop tolerates not-found errors. The literal
    phrase 'ignoring not-found errors' (or the close substitute
    'tolerate not-found') must appear in the Teardown Block."""
    teardown = _section_body(cmd_text, "## Teardown Block")
    assert (
        "ignoring not-found" in teardown
        or "tolerate not-found" in teardown
    ), (
        "Teardown Block must carry the F6 tolerance phrase. Without it, "
        "an editing LLM 'tightening up error handling' will silently "
        "restore crash-on-stale-ID."
    )


# ---------- Best-effort framing ----------

def test_best_effort_framing_in_overview_or_teardown(cmd_text):
    """The 'best-effort' framing prevents an editing LLM from converting
    Teardown into a strict-required-success operation."""
    full = cmd_text.lower()
    assert "best-effort" in full


def test_opportunistic_wake_rationale_present(cmd_text):
    """Brief 'wake mechanism is opportunistic' rationale anchors why
    Teardown failures are tolerable. Per Task #52: brief mention here;
    main treatment lives in watch-inbox."""
    full = cmd_text.lower()
    assert (
        "opportunistic" in full
        or "no harm done" in full
        or "tolerable" in full
    )


# ---------- Teardown ordering load-bearing ----------

def test_teardown_block_orders_taskstop_before_unlink(cmd_text):
    """Order is load-bearing: TaskStop must precede STATE_FILE unlink.
    Inverse ordering would leave a brief window where a STATE_FILE-less
    Monitor still runs but Arm sees no STATE_FILE and re-arms — orphan."""
    teardown = _section_body(cmd_text, "## Teardown Block")
    stop_idx = teardown.find("TaskStop")
    # Match either "Unlink STATE_FILE" prose or the canonical
    # `Path.unlink(STATE_FILE, missing_ok=True)` form.
    unlink_idx = teardown.find("Path.unlink")
    if unlink_idx == -1:
        unlink_idx = teardown.find("Unlink STATE_FILE")
    assert stop_idx >= 0, "TaskStop reference missing from §Teardown Block"
    assert unlink_idx >= 0, "STATE_FILE unlink reference missing from §Teardown Block"
    assert stop_idx < unlink_idx, (
        "Teardown ordering inverted — TaskStop must precede unlink"
    )


def test_teardown_uses_missing_ok(cmd_text):
    """Path.unlink missing_ok=True is the load-bearing flag — without
    it, the unlink call raises FileNotFoundError when STATE_FILE was
    already removed by an earlier Teardown."""
    teardown = _section_body(cmd_text, "## Teardown Block")
    assert "missing_ok=True" in teardown


# ---------- Failure Modes coverage ----------

def test_failure_modes_mentions_monitor_died_silently(cmd_text):
    """The Monitor-died-mid-session concept must appear in §Failure Modes
    with the specific phrasing — `"Monitor"` alone is too weak (passes on
    any prose mentioning Monitor); the F6-anchored phrase pins the
    failure-mode entry concretely."""
    fm = _section_body(cmd_text, "## Failure Modes")
    assert "Monitor died silently" in fm


# ---------- Cross-link to watch-inbox ----------

def test_references_section_links_to_watch_inbox(cmd_text):
    refs = _section_body(cmd_text, "## References")
    assert "watch-inbox" in refs


# ---------- Lead-Session Guard (arch-F1) ----------

def test_lead_session_guard_section_has_body(cmd_text):
    """Teardown must refuse to execute from a teammate session AND the
    section must have a non-empty body (not just inline cross-ref).
    Phantom-green guard: the prior `'## Lead-Session Guard' in cmd_text`
    form would pass on inline references like 'see `## Lead-Session
    Guard` below'; this stricter form requires the actual H2 section to
    exist and contain content."""
    body = _section_body(cmd_text, "## Lead-Session Guard")
    assert body.strip(), (
        "Lead-Session Guard section is missing or empty — phantom-green "
        "guard: only an inline cross-ref to the section, not the section "
        "itself."
    )


def test_lead_session_guard_compares_session_id_to_team_config_lead(cmd_text):
    """Same as arm command: signal source MUST be `session_id` against
    `team_config.leadSessionId`. Two-source-of-truth defense."""
    guard = _section_body(cmd_text, "## Lead-Session Guard")
    assert "leadSessionId" in guard
    assert "session_id" in guard
    assert "refuse" in guard.lower()


# ---------- task_id allowlist validation (sec-M1) ----------

def test_teardown_validates_monitor_task_id_against_allowlist_regex(cmd_text):
    """The Teardown sequence MUST validate STATE_FILE.monitor_task_id
    against an allowlist regex BEFORE calling TaskStop. A poisoned
    STATE_FILE could otherwise inject arbitrary strings into a tool-call
    argument. The allowlist `^[a-z0-9]{6,}\\Z` matches Claude Code's
    task-id format and refuses anything else.

    Anchor must be `\\Z` (absolute end-of-string), NOT `$`. Python
    regex's `$` matches before a trailing newline by default, which
    means a planted task_id like `bu4hxc2bh\\n; rm -rf ~` would pass
    `re.match(r"^[a-z0-9]{6,}$", task_id)` despite the embedded
    newline. `\\Z` matches only the absolute end of the string."""
    teardown = _section_body(cmd_text, "## Teardown Block")
    assert "^[a-z0-9]{6,}\\Z" in teardown


def test_operation_section_validates_monitor_task_id_before_taskstop(cmd_text):
    """Independent pin in the Operation section — the validation must be
    in the load-bearing procedure list, not just the supplementary
    Teardown Block. An editing LLM removing the regex from Operation
    would leave the supplementary block as the only mention; this test
    pins both surfaces."""
    operation = _section_body(cmd_text, "## Operation")
    assert "^[a-z0-9]{6,}\\Z" in operation


def test_task_id_regex_uses_absolute_end_anchor_not_dollar(cmd_text):
    """Pin the `\\Z` (absolute-end) anchor across both regex surfaces.
    `$` matches before a trailing newline in Python's default re mode,
    creating a smuggling vector: `bu4hxc2bh\\n; rm -rf` passes
    `^[a-z0-9]{6,}$` because `$` accepts the prefix-then-newline
    shape. `\\Z` rejects any string with a trailing newline.

    Restricts the search to the procedure-step lines (not audit prose
    that may quote the old `$` form as a negative example). The
    procedure step is the load-bearing surface — the audit prose can
    legitimately reference both forms in a 'use this not that'
    explanation."""
    for section_name in ("## Operation", "## Teardown Block"):
        body = _section_body(cmd_text, section_name)
        # Filter to only the procedure-step lines: lines that begin with
        # a digit + dot (numbered procedure step) AND mention the regex.
        proc_lines = [
            line for line in body.splitlines()
            if line.strip()
            and (line.lstrip()[:1].isdigit() or line.lstrip().startswith("- "))
            and "[a-z0-9]" in line
        ]
        assert proc_lines, (
            f"§{section_name} should contain a procedure step that names "
            f"the task-id regex"
        )
        for line in proc_lines:
            assert "\\Z" in line, (
                f"§{section_name} procedure step uses unanchored `$` "
                f"(newline-smuggling vector). Line: {line!r}"
            )


# ---------- TOCTOU audit comment (sec-M2) ----------

def test_teardown_documents_toctou_window_audit(cmd_text):
    """The Teardown Block must document the TOCTOU window between
    resolve() and unlink() so an editing LLM understands why the window
    is acceptable (same-user-trust assumption) and does not over-engineer
    a defense that would not improve the security posture."""
    teardown = _section_body(cmd_text, "## Teardown Block")
    assert "TOCTOU" in teardown
    assert "same-user" in teardown.lower()


# ---------- armed_by_session_id integrity validation (sec-M1) ----------

def test_unwatch_inbox_validates_armed_by_session_id_before_taskstop(cmd_text):
    """The Teardown sequence must validate STATE_FILE.armed_by_session_id
    against the current session_id BEFORE calling TaskStop. Without this
    check, a planted/cross-session STATE_FILE would let a different
    same-user session weaponize TaskStop against the lead's active
    Monitor or other tasks. The check fail-opens to unlink so the
    planted file gets cleaned without invoking TaskStop on it."""
    teardown = _section_body(cmd_text, "## Teardown Block")
    assert "armed_by_session_id" in teardown


def test_operation_validates_armed_by_session_id_before_taskstop(cmd_text):
    """Independent pin in the Operation section — the validation must
    appear in the load-bearing procedure list, not just the
    supplementary Teardown Block."""
    operation = _section_body(cmd_text, "## Operation")
    assert "armed_by_session_id" in operation


def test_teardown_audit_explains_cross_session_taskstop_threat(cmd_text):
    """Audit anchor must name the threat model so an editing LLM
    'tightening' the procedure does not silently strip the integrity
    check by reasoning 'the regex already validates'. The audit must
    explicitly name the cross-session TaskStop weaponization shape."""
    teardown = _section_body(cmd_text, "## Teardown Block")
    assert "cross-session" in teardown.lower()


# ---------- arch2-M1: terminal-status doc wording ----------

def test_when_to_invoke_uses_terminal_status_wording(cmd_text):
    """The When-to-Invoke trigger row must reflect that BOTH `completed`
    AND `deleted` terminal statuses fire the Teardown — not just
    `completed`. After be-F2 (cycle 6), status=deleted is also a
    terminal transition; the doc must match the implementation's
    behavior or an editing LLM reading the doc will silently
    misrepresent the contract."""
    when = _section_body(cmd_text, "## When to Invoke")
    assert "terminal status" in when
    assert "completed" in when
    assert "deleted" in when
