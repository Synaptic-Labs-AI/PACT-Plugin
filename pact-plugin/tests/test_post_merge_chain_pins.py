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
_EXPLICIT_RETURN_RE = re.compile(r"return to step \d+|stay in this workflow", re.IGNORECASE)
_OPTION_BULLET_RE = re.compile(r"^\s*- \*\*\"[^\"]+\"\*\*.*?On selection:(.*)$", re.MULTILINE)
_TOP_LEVEL_STEP_RE = re.compile(r"^(\d+)\. \*\*(.+?)\*\*", re.MULTILINE)


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


# (file, anchor, end, extractor, terminal phrases accepted at this site).
# Terminal phrases are accepted only where the chain ends: wrap-up's session
# decision hands the session back to the user or ends it.
CHECKPOINTS = (
    ("peer-review.md", "**Merge Authorization Checkpoint**",
     "> Do not act on bare text", _option_clauses, ()),
    ("peer-review.md", "8. **Invoke `/PACT:wrap-up` after a merge.**",
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
)

# Every other command, with the reason it has no end-of-flow checkpoint.
EXEMPT = {
    "bootstrap.md": "session-start ritual; hands control to the user's first request",
    "imPACT.md": "runs inside another workflow and returns to it",
    "next.md": "backlog report; the user's decision is recorded, no workflow follows",
    "orchestrate.md": "ends by invoking `/PACT:peer-review`, not at a user checkpoint",
    "pin-memory.md": "utility; edits pinned memory and returns",
    "plan-mode.md": "produces a plan; the user starts a workflow from it",
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
                if "gh pr merge" in clause and next_step and "/PACT:wrap-up" in next_step:
                    continue
                bad.append((name, anchor, clause))
        assert not bad, bad


# ---------------------------------------------------------------------------
# Merge -> wrap-up
# ---------------------------------------------------------------------------

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
                heading = _next_top_level_step(text, m.end())
                assert heading and "/PACT:wrap-up" in heading, (path.name, heading)
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
        """Reddens when the persona's state-recovery next-action line stops
        routing a merged PR to `/PACT:wrap-up`, or routes "all complete" to
        peer-review first. Mutation: delete the merged-PR clause."""
        lines = [ln for ln in PERSONA.read_text(encoding="utf-8").splitlines()
                 if ln.startswith("5. Next action:")]
        assert len(lines) == 1, lines
        line = lines[0]
        assert "merged PR" in line and "`/PACT:wrap-up`" in line, line
        assert line.index("`/PACT:wrap-up`") < line.index("all complete"), line


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
