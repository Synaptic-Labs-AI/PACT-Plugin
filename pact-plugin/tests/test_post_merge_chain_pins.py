"""
Pins on the workflow chain's instruction text: orchestrate or comPACT ->
peer-review -> merge -> wrap-up, with pause and refresh as interruptions.

Every pin reads the LLM-loaded markdown directly. Each docstring names the
mutation that reddens it.
"""
import re
from pathlib import Path

PLUGIN = Path(__file__).parent.parent
COMMANDS_DIR = PLUGIN / "commands"
PERSONA = PLUGIN / "agents" / "pact-orchestrator.md"

_PACT_COMMAND_RE = re.compile(r"/PACT:[A-Za-z][A-Za-z-]*")
_EXPLICIT_RETURN_RE = re.compile(r"(?:return|go) to step \d+|stay in this workflow", re.IGNORECASE)
_OPTION_BULLET_RE = re.compile(r"^\s*- \*\*\"[^\"]+\"\*\*.*?On selection:(.*)$", re.MULTILINE)
_TOP_LEVEL_STEP_RE = re.compile(r"^(\d+)\. \*\*(.+?)\*\*", re.MULTILINE)


_STEP_7_ANCHOR = "7. ⚠️ **Merge Authorization Checkpoint**"
_STEP_8_HEADING = "Check the PR state after the merge decision."
_STEP_8_ANCHOR = f"8. **{_STEP_8_HEADING}**"
_STEP_8_END = "\n\n> ⚠️ **Do NOT shut down reviewers here.**"


def _step_8_bullets():
    """{first token of each step-8 bullet: bullet text}, read only inside
    peer-review step 8."""
    section = _section(_text("peer-review.md"), _STEP_8_ANCHOR, _STEP_8_END)
    bullets = {}
    for line in section.splitlines()[1:]:
        stripped = line.strip()
        if stripped.startswith("- "):
            head = stripped[2:].split("→", 1)[0].strip()
            bullets[head] = stripped
    return section, bullets


def _text(name):
    """A bare file name is a command; a path is relative to the plugin root."""
    base = PLUGIN if "/" in name else COMMANDS_DIR
    return (base / name).read_text(encoding="utf-8")


def _section(text, anchor, end):
    """The text from the single occurrence of `anchor` to the first `end`
    after it. An anchor that is missing or repeated fails, so a renamed or
    duplicated section cannot silently shrink the checked set."""
    count = text.count(anchor)
    assert count == 1, f"anchor {anchor!r} occurs {count} times, expected 1"
    start = text.index(anchor)
    stop = text.find(end, start + len(anchor))
    assert stop != -1, f"end marker {end!r} not found after {anchor!r}"
    return text[start:stop]


def _next_top_level_step(text, offset):
    """The bold heading of the first top-level numbered step after `offset`."""
    m = _TOP_LEVEL_STEP_RE.search(text, offset)
    return m.group(2) if m else None


# ---------------------------------------------------------------------------
# End-of-flow checkpoints
# ---------------------------------------------------------------------------

def _option_clauses(section):
    return [m.group(1).strip() for m in _OPTION_BULLET_RE.finditer(section)]


def _arrow_clauses(section):
    return [
        line.split("→", 1)[1].strip()
        for line in section.splitlines()
        if line.lstrip().startswith("- ") and "→" in line
    ]


def _table_action_cells(section):
    rows = [ln for ln in section.splitlines() if ln.startswith("| **")]
    return [ln.strip().strip("|").split("|")[-1].strip() for ln in rows]


def _fenced_lines(section):
    m = re.search(r"```\n(.*?)\n```", section, re.DOTALL)
    return [m.group(1).strip()] if m else []


def _last_bullet(section):
    bullets = [ln.strip()[2:] for ln in section.splitlines() if ln.lstrip().startswith("- ")]
    return bullets[-1:]


# (file, anchor, end, extractor, terminal phrases accepted at this site).
# Terminal phrases are accepted only where the chain ends: wrap-up's session
# decision hands the session back to the user or ends it.
CHECKPOINTS = (
    ("peer-review.md", "**Merge Authorization Checkpoint**",
     "> Do not act on bare text", _option_clauses, ()),
    ("peer-review.md", _STEP_8_ANCHOR,
     "\n\n", _arrow_clauses, ()),
    ("comPACT.md", 'ask: "Work committed. What next?"',
     "**If blocker reported**", _table_action_cells, ()),
    ("wrap-up.md", "## 8. Session Decision",
     "### Teammate shutdown", _option_clauses,
     ('Report "Ready for next task."', "then report.")),
    ("pause.md", "### 7. Report", "If Telegram", _fenced_lines, ()),
    ("refresh.md", "### 6. STANDBY", "Until bootstrap runs", _fenced_lines, ()),
    ("protocols/pact-workflows.md",
     '**Next steps** — After commit, use `AskUserQuestion` to ask: "Work committed. What next?"',
     "**If blocker reported**", _arrow_clauses, ()),
    ("plan-mode.md", "4. Once all decision-requiring questions are resolved:",
     "**Do NOT exit plan mode**", _last_bullet, ()),
)

# Checkpoints whose clause is text the USER runs ("Resume with `/PACT:x`"),
# not a hand-off the lead performs. The hand-off verb pin skips them.
USER_FACING = {"pause.md", "refresh.md", "plan-mode.md"}

# Every other command, with the reason it has no end-of-flow checkpoint.
EXEMPT = {
    "bootstrap.md": "session-start ritual; hands control to the user's first request",
    "imPACT.md": "runs inside another workflow and returns to it",
    "next.md": "backlog report; the user's decision is recorded, no workflow follows",
    "orchestrate.md": "ends by invoking `/PACT:peer-review`, not at a user checkpoint",
    "pin-memory.md": "utility; edits pinned memory and returns",
    "prune-memory.md": "utility; prunes pins and returns",
    "rePACT.md": "nested cycle; returns to the parent workflow",
    "telegram-setup.md": "utility; configures notifications and returns",
}

# Option bullets across commands/*.md on the reference tree. A floor, never
# the definition: the checked set is CHECKPOINTS.
OPTION_BULLET_FLOOR = 10


class TestEndOfFlowCheckpoints:
    def test_every_command_is_classified(self):
        """Reddens when a command file is added without being listed in
        CHECKPOINTS or EXEMPT, or when one is listed in both."""
        on_disk = {p.name for p in COMMANDS_DIR.glob("*.md")}
        checked = {c[0] for c in CHECKPOINTS if "/" not in c[0]}
        assert not checked & set(EXEMPT), sorted(checked & set(EXEMPT))
        assert on_disk == checked | set(EXEMPT), (
            sorted(on_disk - checked - set(EXEMPT)),
            sorted(checked | set(EXEMPT) - on_disk),
        )

    def test_option_bullet_count_is_at_least_the_floor(self):
        """Non-vacuity control: reddens when the option-bullet regex stops
        matching the command files."""
        total = sum(
            len(_OPTION_BULLET_RE.findall(p.read_text(encoding="utf-8")))
            for p in COMMANDS_DIR.glob("*.md")
        )
        assert total >= OPTION_BULLET_FLOOR, total

    def test_every_checkpoint_names_a_successor_or_a_return(self):
        """Reddens when an end-of-flow option says only "do nothing", or when
        any checkpoint section loses its anchor. Mutation: restore peer-review's
        "Continue reviewing" clause to "do nothing — let the user continue
        their review"."""
        bad = []
        for name, anchor, end, extract, terminal in CHECKPOINTS:
            text = _text(name)
            if text.count(anchor) != 1:
                bad.append((name, anchor, f"anchor occurs {text.count(anchor)} times"))
                continue
            section = _section(text, anchor, end)
            clauses = extract(section)
            if not clauses:
                bad.append((name, anchor, "no clauses extracted"))
                continue
            next_step = _next_top_level_step(text, text.index(anchor) + len(section))
            for clause in clauses:
                if _PACT_COMMAND_RE.search(clause) or _EXPLICIT_RETURN_RE.search(clause):
                    continue
                if any(t in clause for t in terminal):
                    continue
                if ("gh pr merge" in clause and next_step == _STEP_8_HEADING
                        and "invoke `/PACT:wrap-up`" in _step_8_bullets()[1].get("`MERGED`", "")):
                    continue
                bad.append((name, anchor, clause))
        assert not bad, bad


# ---------------------------------------------------------------------------
# Merge -> wrap-up
# ---------------------------------------------------------------------------

class TestStepEightStateGate:
    def test_step_8_reads_the_pr_state_first(self):
        """Reddens when step 8 stops reading the PR state (and the auto-merge
        request) before it routes. Mutation: delete the `gh pr view` sentence."""
        section, _ = _step_8_bullets()
        first = section.split("\n", 1)[0]
        assert "Run `gh pr view <N> --json state,autoMergeRequest`." in first, first

    def test_only_merged_invokes_wrap_up(self):
        """Reddens when a non-MERGED bullet invokes `/PACT:wrap-up`, or when the
        MERGED bullet stops creating the wrap-up task and invoking wrap-up.
        Mutation: change "`MERGED` →" to "Always →"."""
        _, bullets = _step_8_bullets()
        assert set(bullets) == {"`MERGED`", "`OPEN` with `autoMergeRequest` set",
                                "Any other state"}, sorted(bullets)
        merged = bullets["`MERGED`"]
        assert '`TaskCreate` the "Invoke /PACT:wrap-up" task' in merged, merged
        assert "invoke `/PACT:wrap-up`" in merged, merged
        for head, text in bullets.items():
            if head != "`MERGED`":
                assert not _EDGE_RE.search(text), (head, text)

    def test_a_queued_merge_is_not_reapproved(self):
        """Reddens when a pending auto-merge loops back to the approval step.
        Mutation: change the auto-merge bullet's "return to step 8" to "return
        to step 7"."""
        _, bullets = _step_8_bullets()
        queued = bullets["`OPEN` with `autoMergeRequest` set"]
        assert "Do not ask for approval again." in queued, queued
        assert "return to step 8" in queued and "step 7" not in queued, queued

    def test_any_other_state_returns_to_the_checkpoint(self):
        """Reddens when a refused or unmerged PR stops returning to step 7.
        Mutation: delete the "Any other state" bullet."""
        _, bullets = _step_8_bullets()
        assert bullets.get("Any other state", "").endswith("return to step 7."), bullets

    def test_continue_reviewing_reaches_the_state_check(self):
        """Reddens when "Continue reviewing" skips step 8, so a PR merged on
        GitHub meanwhile never reaches `/PACT:wrap-up`. Mutation: change "go to
        step 8" back to "return to step 7"."""
        step_7 = _section(_text("peer-review.md"), _STEP_7_ANCHOR, _STEP_8_ANCHOR)
        lines = [ln for ln in step_7.splitlines() if '**"Continue reviewing"**' in ln]
        assert len(lines) == 1, lines
        assert lines[0].rstrip().endswith("go to step 8"), lines[0]


class TestMergeHandsOffToWrapUp:
    def test_merge_option_ends_at_the_merge_and_the_next_step_names_wrap_up(self):
        """Reddens when an option that runs `gh pr merge` also names a
        `/PACT:` command, or when the next top-level step's heading does not
        name `/PACT:wrap-up`. Mutation: append ", then invoke `/PACT:wrap-up`"
        to the merge option, or delete peer-review step 8."""
        found = 0
        for path in sorted(COMMANDS_DIR.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            for m in _OPTION_BULLET_RE.finditer(text):
                clause = m.group(1)
                if "gh pr merge" not in clause:
                    continue
                found += 1
                assert not _PACT_COMMAND_RE.search(clause), (path.name, clause)
                assert path.name == "peer-review.md", path.name
                step_7 = _section(text, _STEP_7_ANCHOR, _STEP_8_ANCHOR)
                assert m.group(0) in step_7, "the merge option left step 7"
                heading = _next_top_level_step(text, m.end())
                assert heading == _STEP_8_HEADING, heading
                merged = _step_8_bullets()[1].get("`MERGED`", "")
                assert "invoke `/PACT:wrap-up`" in merged, merged
        assert found, "no option runs `gh pr merge`; the extraction stopped matching"

    def test_review_hierarchy_creates_the_wrap_up_task_before_completing_review(self):
        """Reddens when peer-review's Task Hierarchy has no `TaskCreate` naming
        `/PACT:wrap-up`, or places it after the Review task completes.
        Mutation: delete hierarchy item 19."""
        text = _text("peer-review.md")
        block = _section(text, "## Task Hierarchy", "\n## ")
        lines = block.splitlines()
        create = [i for i, ln in enumerate(lines)
                  if "`TaskCreate`" in ln and "/PACT:wrap-up" in ln]
        complete = [i for i, ln in enumerate(lines)
                    if 'Review task status = "completed"' in ln]
        assert len(create) == 1, create
        assert complete, "hierarchy no longer completes the Review task"
        assert create[0] < complete[-1], (create, complete)

    def test_persona_recovery_routes_a_merged_pr_to_wrap_up(self):
        """Reddens when the persona's state-recovery next-action step stops
        routing a merged PR to `/PACT:wrap-up`, routes "all complete" to
        peer-review first, or moves out of the State Recovery section.
        Mutation: delete the merged-PR clause."""
        section = _section(PERSONA.read_text(encoding="utf-8"),
                           "## 6. State Recovery", "\n## ")
        lines = [ln for ln in section.splitlines() if ln.startswith("5. Next action:")]
        assert len(lines) == 1, lines
        line = lines[0]
        assert "merged PR" in line and "`/PACT:wrap-up`" in line, line
        assert line.index("`/PACT:wrap-up`") < line.index("all complete"), line

    def test_persona_keys_wrap_up_on_the_wrap_up_session_end(self):
        """Reddens when the persona decides "wrap-up has not run" from any
        signal other than a `session_end` with source wrap-up after the latest
        `review_dispatch`. Mutation: restore "no `session_consolidated` event
        in the journal"."""
        section = _section(PERSONA.read_text(encoding="utf-8"),
                           "## 6. State Recovery", "\n## ")
        line = next(ln for ln in section.splitlines() if ln.startswith("5. Next action:"))
        assert ('no `session_end` event with `"source": "wrap-up"` after the latest '
                '`review_dispatch` event') in line, line
        assert "session_consolidated" not in line, line


# ---------------------------------------------------------------------------
# Teammate shutdown has one owner
# ---------------------------------------------------------------------------

_LLM_LOADED_GLOBS = ("commands/*.md", "agents/*.md", "protocols/*.md", "skills/*/SKILL.md")

# A merge and `/PACT:wrap-up` joined by "or", in either order: the two offered
# as alternative end points ("until after PR merge or `/PACT:wrap-up`").
_MERGE_OR_WRAP_UP_RE = re.compile(
    r"\bmerge[a-z]*`?,?\s+or\s+(?:after\s+|until\s+|via\s+)?`?/PACT:wrap-up"
    r"|/PACT:wrap-up`?,?\s+or\s+(?:after\s+|until\s+)?(?:the\s+|a\s+|PR\s+|user\s+)*merge",
    re.IGNORECASE,
)


class TestShutdownOwner:
    def test_the_pattern_matches_the_alternative_form(self):
        """Non-vacuity control: reddens when the pattern stops matching the
        shape it exists to catch."""
        assert _MERGE_OR_WRAP_UP_RE.search("persist until after PR merge or `/PACT:wrap-up`.")
        assert _MERGE_OR_WRAP_UP_RE.search("run `/PACT:wrap-up` or merge the PR")

    def test_no_llm_loaded_sentence_offers_merge_or_wrap_up(self):
        """Reddens when any LLM-loaded file offers a merge and `/PACT:wrap-up`
        as alternatives. Mutation: restore the persona's "Teammates persist
        until after PR merge or `/PACT:wrap-up`."."""
        files = [p for g in _LLM_LOADED_GLOBS for p in sorted(PLUGIN.glob(g))]
        assert files, "no LLM-loaded files found"
        hits = []
        for path in files:
            text = path.read_text(encoding="utf-8")
            for m in _MERGE_OR_WRAP_UP_RE.finditer(text):
                hits.append((str(path.relative_to(PLUGIN)), text[:m.start()].count("\n") + 1, m.group(0)))
        assert not hits, hits


# A teammate-lifecycle verb whose end point is the merge alone: "Teammates
# persist until after user-authorized merge", "Shut down after ... user merge
# decision". Scanned per sentence and per table cell. "merge-ready" is a task
# state, not an end point.
_LIFECYCLE_KEYED_ON_MERGE_RE = re.compile(
    r"\b(?:shut(?:s|ting)?\s+(?:\w+\s+){0,3}?down|shutdown|persist(?:s|ed)?|"
    r"(?:keep|kept|stay|stays)\s+alive)\b"
    r"[^.|\n]*?\b(?:after|until|following)\b[^.|\n]*?\bmerge\b(?!-)",
    re.IGNORECASE,
)


def _sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+|\|", text) if s.strip()]


class TestTeammateLifecycleNotKeyedOnMerge:
    def test_the_pattern_matches_both_retired_shapes(self):
        """Non-vacuity control: the two sentences this pin retired, verbatim
        from the reference tree, must each match; the persist-state and
        merge-ready phrasings on the live tree must not."""
        for retired in (
            "Teammates persist until after user-authorized merge.",
            ("Shut down after all remediation complete + user merge decision "
             "(via `AskUserQuestion`)."),
        ):
            assert _LIFECYCLE_KEYED_ON_MERGE_RE.search(retired), retired
        for kept in (
            "Review task: in_progress (persists until merge-ready)",
            "Consolidate memory, persist state, shut down teammates",
        ):
            assert not _LIFECYCLE_KEYED_ON_MERGE_RE.search(kept), kept

    def test_no_llm_loaded_sentence_ends_teammates_at_the_merge(self):
        """Reddens when any LLM-loaded sentence or table cell shuts down, keeps
        alive or persists teammates until a merge without naming
        `/PACT:wrap-up` as the owner. Mutation: restore peer-review's "Teammates
        persist until after user-authorized merge."."""
        files = [p for g in _LLM_LOADED_GLOBS for p in sorted(PLUGIN.glob(g))]
        assert files, "no LLM-loaded files found"
        hits = []
        for path in files:
            for sentence in _sentences(path.read_text(encoding="utf-8")):
                if "/PACT:wrap-up" in sentence:
                    continue
                m = _LIFECYCLE_KEYED_ON_MERGE_RE.search(sentence)
                if m:
                    hits.append((str(path.relative_to(PLUGIN)), m.group(0)))
        assert not hits, hits


# ---------------------------------------------------------------------------
# The workflow chain
# ---------------------------------------------------------------------------

# An imperative hand-off: "invoke `/PACT:x`" or "**Invoke `/PACT:x`**". A
# mention ("happens during `/PACT:wrap-up`") or a "Run `/PACT:x`" is not an edge.
_EDGE_RE = re.compile(r"\b[Ii]nvoke\*{0,2}\s+`/PACT:([A-Za-z][A-Za-z-]*)`")
_WORKFLOWS = PLUGIN / "protocols" / "pact-workflows.md"
_CHAIN_RE = re.compile(r"^\*\*Chain\*\*: (.+?)\.(?: |$)", re.MULTILINE)


def _successor_graph():
    """{command stem: set of command stems it imperatively invokes}. A target
    that is a skill rather than a command (worktree-setup) is not a node."""
    stems = {p.stem for p in COMMANDS_DIR.glob("*.md")}
    graph = {}
    for path in COMMANDS_DIR.glob("*.md"):
        targets = set(_EDGE_RE.findall(path.read_text(encoding="utf-8")))
        graph[path.stem] = targets & stems
    return graph


def _reaches(graph, start, goal, avoiding=frozenset()):
    seen, todo = set(), [start]
    while todo:
        node = todo.pop()
        if node == goal:
            return True
        if node in seen or node in avoiding:
            continue
        seen.add(node)
        todo.extend(graph.get(node, ()))
    return False


class TestWorkflowChain:
    def test_every_named_successor_exists(self):
        """Reddens when a command invokes `/PACT:x` and x is neither a command
        nor a skill. Mutation: rename a target to `/PACT:wrapup`."""
        stems = {p.stem for p in COMMANDS_DIR.glob("*.md")}
        skills = {p.name for p in (PLUGIN / "skills").iterdir() if p.is_dir()}
        missing = []
        for path in COMMANDS_DIR.glob("*.md"):
            for target in _EDGE_RE.findall(path.read_text(encoding="utf-8")):
                if target not in stems | skills:
                    missing.append((path.name, target))
        assert not missing, missing

    def test_work_workflows_reach_wrap_up_only_through_peer_review(self):
        """Reddens when orchestrate or comPACT stops invoking peer-review,
        invokes wrap-up directly, or when peer-review stops invoking wrap-up.
        Mutation: change orchestrate's "**Invoke `/PACT:peer-review`**" to
        "**Run `/PACT:peer-review`**"."""
        graph = _successor_graph()
        assert "wrap-up" in graph["peer-review"], graph["peer-review"]
        for start in ("orchestrate", "comPACT"):
            assert "peer-review" in graph[start], (start, graph[start])
            assert _reaches(graph, start, "wrap-up"), (start, graph)
            assert not _reaches(graph, start, "wrap-up", avoiding={"peer-review"}), (
                start, graph)

    def test_the_chain_line_states_the_derived_chain(self):
        """Reddens when pact-workflows has no **Chain** line, or when the line's
        `/PACT:` sequence disagrees with the successor graph. Mutation: delete
        the Chain line, or swap peer-review and wrap-up in it."""
        matches = _CHAIN_RE.findall(_WORKFLOWS.read_text(encoding="utf-8"))
        assert len(matches) == 1, matches
        stages = [s.strip() for s in matches[0].split("→")]
        named = [_PACT_COMMAND_RE.findall(s) for s in stages]
        assert [n for n in named if n] == [
            ["/PACT:orchestrate", "/PACT:comPACT"], ["/PACT:peer-review"], ["/PACT:wrap-up"],
        ], named
        graph = _successor_graph()
        for earlier, later in zip([n for n in named if n], [n for n in named if n][1:]):
            for a in earlier:
                for b in later:
                    assert b.split(":")[1] in graph[a.split(":")[1]], (a, b, graph)


# A verb other than "invoke" placed directly before a `/PACT:` command: the
# successor graph above counts only "invoke", so a hand-off worded this way
# drops its edge without reddening anything.
_OTHER_HANDOFF_VERB_RE = re.compile(
    r"\b(?:run|use|execute|call|start|launch|trigger|begin|switch\s+to|go\s+to|"
    r"proceed\s+(?:to|with)|continue\s+with|hand\s+off\s+to)\*{0,2}\s+`?/PACT:",
    re.IGNORECASE,
)

# The lead's own end-of-flow hand-offs outside CHECKPOINTS: orchestrate ends by
# invoking peer-review.
_LEAD_HANDOFF_STEPS = (
    ("orchestrate.md", "6. **Invoke `/PACT:peer-review`**", "\n"),
)


def _lead_handoff_clauses():
    out = []
    for name, anchor, end, extract, _terminal in CHECKPOINTS:
        if name in USER_FACING:
            continue
        for clause in extract(_section(_text(name), anchor, end)):
            out.append((name, clause))
    for name, anchor, end in _LEAD_HANDOFF_STEPS:
        out.append((name, _section(_text(name), anchor, end)))
    return out


class TestHandOffsUseInvoke:
    def test_the_pattern_matches_the_dropped_shapes(self):
        """Non-vacuity control: the shapes that would drop an edge match; the
        counted shape and a plain mention do not."""
        for dropped in ("Run `/PACT:peer-review`", "**Run `/PACT:peer-review`**",
                        "use `/PACT:pause`", "proceed to `/PACT:wrap-up`"):
            assert _OTHER_HANDOFF_VERB_RE.search(dropped), dropped
        for kept in ("Invoke `/PACT:peer-review`", "more work may follow via `/PACT:comPACT`"):
            assert not _OTHER_HANDOFF_VERB_RE.search(kept), kept

    def test_every_lead_handoff_clause_is_checked(self):
        """Non-vacuity control: reddens when the lead-facing set shrinks below
        one clause per lead-facing checkpoint plus orchestrate's final step."""
        names = {n for n, _ in _lead_handoff_clauses()}
        expected = {c[0] for c in CHECKPOINTS if c[0] not in USER_FACING} | {
            s[0] for s in _LEAD_HANDOFF_STEPS}
        assert names == expected, sorted(names ^ expected)

    def test_end_of_flow_handoffs_are_introduced_with_invoke(self):
        """Reddens when a lead-facing end-of-flow hand-off names its successor
        with a verb other than "invoke", which the successor graph would not
        count. Mutation: change peer-review's "invoke `/PACT:pause`" option to
        "run `/PACT:pause`"."""
        bad = [(n, c) for n, c in _lead_handoff_clauses() if _OTHER_HANDOFF_VERB_RE.search(c)]
        assert not bad, bad
        # Every lead-facing clause that names a successor at all names one edge.
        no_edge = [(n, c) for n, c in _lead_handoff_clauses()
                   if _PACT_COMMAND_RE.search(c) and not _EDGE_RE.search(c)
                   and not _EXPLICIT_RETURN_RE.search(c)]
        assert not no_edge, no_edge


# ---------------------------------------------------------------------------
# imPACT returns to its caller
# ---------------------------------------------------------------------------

class TestImpactReturnsToCaller:
    def test_every_outcome_resumes_the_invoking_workflow(self):
        """Reddens when the paragraph after imPACT's Outcomes table stops
        resuming the blocked workflow, or allows starting a new one. Mutation:
        delete the **Return to the caller** paragraph."""
        text = _text("imPACT.md")
        after_table = _section(text, "## Outcomes", "**When to consider rePACT**")
        paras = [p for p in after_table.split("\n\n") if p.startswith("**Return to the caller**")]
        assert len(paras) == 1, paras
        rule = paras[0]
        assert "After the chosen outcome" in rule, rule
        assert "resume the workflow whose task the blocker blocked" in rule, rule
        assert "Never start a new workflow from imPACT" in rule, rule

    def test_retry_continues_the_invoking_workflow(self):
        """Reddens when the retry step stops sending the lead back into the
        invoking workflow. Mutation: drop the appended clause."""
        lines = [ln for ln in _text("imPACT.md").splitlines()
                 if "**Retry the current phase**" in ln]
        assert len(lines) == 1, lines
        assert lines[0].rstrip().endswith("then continue the invoking workflow from that phase"), lines[0]


# ---------------------------------------------------------------------------
# A reopened finding class fails the cycle
# ---------------------------------------------------------------------------

class TestFailedCycleRule:
    def test_failed_cycle_counts_a_reopened_class(self):
        """Reddens when peer-review's failed-cycle rule stops counting a finding
        in an already-closed class, or stops defining a class. Mutation: delete
        the clause "OR raises a finding in a class an earlier cycle already
        closed"."""
        paras = [p for p in _text("peer-review.md").split("\n\n")
                 if p.startswith("**Failed cycle**:")]
        assert len(paras) == 1, paras
        rule = paras[0]
        assert "leaves a blocking finding open" in rule, rule
        assert "raises a finding in a class an earlier cycle already closed" in rule, rule
        assert "A finding's class is one sentence naming the behaviour that fails" in rule, rule

    def test_termination_reads_through_the_definition(self):
        """Reddens when the remediation termination rule counts something other
        than failed cycles. Mutation: restore "If blocking items persist after 2
        fix-verify cycles"."""
        lines = [ln for ln in _text("peer-review.md").splitlines()
                 if "**Termination**:" in ln]
        assert len(lines) == 1, lines
        assert "After 2 failed cycles (see **Failed cycle** above)" in lines[0], lines[0]


# ---------------------------------------------------------------------------
# A caller check returns only to the review of the current branch
# ---------------------------------------------------------------------------

_CALLER_CHECK_SITES = (
    "commands/orchestrate.md",
    "commands/comPACT.md",
    "protocols/pact-workflows.md",
    "protocols/pact-protocols.md",
)
_CURRENT_REVIEW = (
    "an `in_progress` Review task whose subject ends with `({branch})`, where "
    "{branch} is the output of `git branch --show-current`"
)


def _caller_checks(text):
    return [ln for ln in text.splitlines()
            if "Review task" in ln and "fixing review findings" in ln]


class TestCallerCheckNamesTheCurrentReview:
    def test_review_subject_carries_the_branch(self):
        """Reddens when peer-review's Review task subject stops naming the
        branch the caller checks match on. Mutation: restore the subject
        "Review: {feature}"."""
        block = _section(_text("peer-review.md"), "## Task Hierarchy", "\n## ")
        items = [ln for ln in block.splitlines() if "`TaskCreate`: Review task" in ln]
        assert len(items) == 1, items
        assert '"Review: {feature} ({branch})"' in items[0], items[0]
        assert "git branch --show-current" in items[0], items[0]

    def test_every_caller_check_names_the_current_branch_review(self):
        """Reddens when a site that returns to a calling review accepts any
        `in_progress` Review task, or when a site's check disappears.
        Mutation: replace "whose subject ends with `({branch})`" with "whose
        subject names the branch" at any one site."""
        bad = []
        for rel in _CALLER_CHECK_SITES:
            checks = _caller_checks(_text(rel))
            if len(checks) != 1:
                bad.append((rel, f"{len(checks)} caller checks"))
            elif _CURRENT_REVIEW not in checks[0]:
                bad.append((rel, checks[0]))
        assert not bad, bad

    def test_no_other_file_carries_a_caller_check(self):
        """Reddens when a caller check appears outside the listed sites, so
        the per-site arm above cannot miss a new one."""
        found = set()
        for g in _LLM_LOADED_GLOBS:
            for path in PLUGIN.glob(g):
                if _caller_checks(path.read_text(encoding="utf-8")):
                    found.add(str(path.relative_to(PLUGIN)))
        assert found == set(_CALLER_CHECK_SITES), sorted(found ^ set(_CALLER_CHECK_SITES))
