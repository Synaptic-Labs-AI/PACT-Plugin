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

_PACT_COMMAND_RE = re.compile(r"/PACT:[a-z][a-z-]*")
_EXPLICIT_RETURN_RE = re.compile(r"return to step \d+|stay in this workflow", re.IGNORECASE)
_OPTION_BULLET_RE = re.compile(r"^\s*- \*\*\"[^\"]+\"\*\*.*?On selection:(.*)$", re.MULTILINE)
_TOP_LEVEL_STEP_RE = re.compile(r"^(\d+)\. \*\*(.+?)\*\*", re.MULTILINE)


def _text(name):
    return (COMMANDS_DIR / name).read_text(encoding="utf-8")


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
        checked = {c[0] for c in CHECKPOINTS}
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
