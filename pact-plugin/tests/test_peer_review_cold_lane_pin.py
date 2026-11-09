"""Structural pins for the cold reviewer lane in commands/peer-review.md.

The cold lane is a prose-only feature: an anonymous `general-purpose`
subagent dispatched with an information diet instead of a narrative. Its
independence lives in WHAT the dispatch text says, so the pins are token
and phrase pins bound to the clause or paragraph that must carry them,
never file-wide; some pins span a whole sentence, where the rule is the
whole sentence or needs its closing words (an exemption list that must
name nothing more, say). Each
region ends at the next region of its kind, so a token living in a sibling
clause cannot satisfy an arm, and a rewording that keeps the load-bearing
phrases survives.

COVERED, and this list is the scope:
- the section anchor, its closing rule, and the warm-dispatch paragraph
  that follows it ('for each warm reviewer');
- the cold prompt: exactly one fenced block sent verbatim, its ordered
  clause labels, no backticks inside it, and every placeholder it uses
  defined in the placeholder list (and every defined placeholder used);
  the {base} and {head} definitions (one base for every round);
  the bound on what a re-check appends;
- each prompt clause's content, bound to its own label line: ARTIFACT's
  diff instruction and its bar on PR and commit-message surfaces; AVOID's
  named paths and stores; CHANNELS' messaging, task-tool and pact-memory
  prohibitions and its HALT instruction; DISREGARD's arrival surfaces,
  its operative verb, the advisory carve-out, the safety-rules exception
  and its precedence rule; READ-ONLY's worktree bar, cache exception,
  scratch-copy route, quoted-heredoc rule and guarded-command bar;
  OPERATIONAL FACTS' placeholder and use instruction, and the
  placeholder's own definition; QUESTION's phrase and replica clause;
  REPORT's diff-derived opening, severity enum, nothing-found lines,
  merge-block sentence, open questions, coverage boundary and blocker
  report;
- the spawn sentence: one inline Agent(subagent_type="general-purpose",
  description="Cold diff-only review", ...) reference carrying no name=,
  no team_name= and no role prelude,
  plus the prose that requires that shape (NO name=/team_name=/prelude,
  with every review, same parallel block, no teammate conversion, no
  tasks);
- the lifecycle: the per-round ordering lead-in, the ten step labels in
  order, a once guard that holds each listed rule's operative phrase to a
  single occurrence in its home (the lane, or step 5) and in the whole
  file (a paraphrased second copy evades it), and an absence check keeping the dropped machinery's terms out of
  the lane; each step's operative clauses, bound to its own step
  paragraph; the resume paragraph; the quoted re-check message; and the
  division of labour;
- the copy-paste guard: no warm dispatch-machinery token occurs in the
  section, with task-tool names allowed only inside CHANNELS;
- satellites: the always-included bullet, the example-tree [Subagent]
  label, the Reuse-table cold row, the warm-only completion check, and the
  clause-satisfaction paragraph after REVIEWER-MEMORY-CHANNEL;
- outside the section, in both files: each warm-scoping or cold-line
  phrase (overview steps 4-9, reviewer counts, journal cold line, dispatch
  template, warm spawn line, synthesis rows, the merge gate's cold
  conditions, persona waits, resume route, HALT and blocker exceptions),
  each asserted to occur once;
- the persona: the exception at the Agent-Teams checkpoint and in the
  dispatch-mandatory block, the cold row of its Reuse table, the HANDOFF
  exception and its reviewer-list bullet, each bound to its own region;
- three class guards (see TestClassGuards for what each cannot catch):
  every placeholder in the section defined and no angle-bracket slots;
  each validity list naming every element its contract requires; and a
  census of reviewer paragraphs naming warm machinery, each qualified or
  inventoried against the sentence that scopes it.

ENUMERATION NOTE for the copy-paste guard: the warm template hands no
path literals — plan and contract paths are runtime-filled into Task B
descriptions. The enumerated set is the warm machinery's actual spawn and
task tokens, read off peer-review.md as written, plus warm-only words a
paraphrased warm brief would still carry. The cold section's own
legitimate avoid-list paths sit inside AVOID, which is why they are
pinned PRESENT there.

WHAT THIS DOES NOT CATCH: obedience, and paraphrase. Whether a dispatched
cold reviewer actually refrains from reading the avoid-list paths, reports
the required shape, or arrives without contaminated context is runtime
behavior no fixture observes — the first live peer-review dispatch under
this prose is the probe. A warm brief rewritten in words that avoid every
enumerated token passes the guard. The evasion ceiling is prose reworded into
uselessness while every pinned phrase survives. A second, distinct cost is
maintenance: the rows are exact substrings, so a punctuation-only edit inside a
pinned span (a semicolon replaced by a period, say) reds a row even though the
meaning is unchanged, and the row must be updated with the prose.
"""
import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
PEER_REVIEW = PLUGIN_ROOT / "commands" / "peer-review.md"
PERSONA = PLUGIN_ROOT / "agents" / "pact-orchestrator.md"

SECTION_HEADING = "### Cold Reviewer Lane"
# The warm dispatch paragraph that follows the section. Its scoping word
# "warm" keeps the per-reviewer Teachback-Gated Dispatch steps off the
# cold lane, so it is both the section's end anchor and a pinned clause.
DISPATCH_REVIEWERS_LEAD = "**Dispatch reviewers**"
WARM_SCOPING = "for each warm reviewer"
PROMPT_FENCE_OPEN = "```text\n"
PROMPT_FENCE_CLOSE = "\n```"
# A prompt clause's label line. Every clause uses this one syntax, so the
# next match is the next clause whatever its name.
CLAUSE_LABEL_LINE = re.compile(r"^- \*\*(?P<label>[A-Z][^*\n]*)\*\*:", re.MULTILINE)
PROMPT_LABELS = (
    "ARTIFACT",
    "AVOID by path",
    "CHANNELS",
    "DISREGARD",
    "READ-ONLY",
    "OPERATIONAL FACTS",
    "QUESTION",
    "REPORT",
)
PLACEHOLDER = re.compile(r"\{[a-z_]+\}")
# `name=` as a parameter of its own: `team_name=` must not match it.
NAME_PARAM = re.compile(r"(?<![\w])name=")
# The persona names the exception at two sites whose lead-ins differ, so
# each site is bound by its own REGION: an occurrence of the fragment
# elsewhere in the persona (a reviewer-list bullet, the HANDOFF sentence)
# must not stand in for a dropped site.
PERSONA_EXCEPTION = "the peer-review cold reviewer"
PERSONA_NOT_SPECIALIST = "not a specialist dispatch"
PERSONA_CHECKPOINT_MARKER = (
    "Reaching for `Agent(subagent_type=...)` without `team_name`"
)
PERSONA_NEVER_MARKER = "for specialist agents"


def _strip_ticks(s: str) -> str:
    """Drop backticks so a vocabulary pin matches ticked and unticked forms."""
    return s.replace("`", "")


def _region(text: str, marker: str, what: str) -> str:
    """The paragraph containing `marker`, from its start to the next blank line.

    A blockquote block spans its `>` continuation lines, which are not blank,
    so a blockquote is one region.
    """
    hit = text.find(marker)
    assert hit != -1, f"{what} lost its {marker!r} marker"
    assert text.find(marker, hit + 1) == -1, (
        f"{what}: the {marker!r} marker now occurs more than once, so the "
        f"region it anchors is ambiguous"
    )
    start = text.rfind("\n\n", 0, hit)
    start = 0 if start == -1 else start + 2
    end = text.find("\n\n", hit)
    return text[start:end if end != -1 else None]


def _peer_review_text() -> str:
    return PEER_REVIEW.read_text(encoding="utf-8")


def _persona_text() -> str:
    return PERSONA.read_text(encoding="utf-8")


def _cold_section() -> str:
    """The Cold Reviewer Lane section alone: heading to the closing rule.

    The end is anchored on the warm dispatch paragraph that follows the
    section, and the closing rule must sit directly before it: if the rule
    is deleted or moved, the failure names the boundary instead of letting
    the section swallow the warm machinery and fail on its tokens.
    """
    text = _peer_review_text()
    start = text.find(SECTION_HEADING)
    assert start != -1, f"peer-review.md lost the {SECTION_HEADING!r} section"
    follower = text.find(DISPATCH_REVIEWERS_LEAD, start)
    assert follower != -1, (
        f"the cold lane section is no longer followed by the "
        f"{DISPATCH_REVIEWERS_LEAD!r} paragraph — the section boundary moved"
    )
    body = text[start:follower].rstrip()
    assert body.endswith("\n---"), (
        "the cold lane section no longer closes with a '---' rule directly "
        "before the warm dispatch paragraph — the section boundary moved"
    )
    return body[: -len("\n---")]


def _split_section() -> tuple[str, str, str]:
    """(text before the prompt fence, the fenced prompt, text after it)."""
    section = _cold_section()
    assert section.count(PROMPT_FENCE_OPEN) == 1, (
        f"expected exactly 1 {PROMPT_FENCE_OPEN.strip()!r} prompt fence in the "
        f"cold lane section, found {section.count(PROMPT_FENCE_OPEN)}"
    )
    open_at = section.find(PROMPT_FENCE_OPEN)
    body_at = open_at + len(PROMPT_FENCE_OPEN)
    close_at = section.find(PROMPT_FENCE_CLOSE, body_at - 1)
    assert close_at != -1, "the cold prompt fence is never closed"
    return section[:open_at], section[body_at:close_at + 1], section[close_at + len(PROMPT_FENCE_CLOSE):]


def _prompt() -> str:
    return _split_section()[1]


def _lead_prose() -> str:
    """The section's lead-facing prose: everything outside the prompt fence."""
    before, _, after = _split_section()
    return before + "\n\n" + after


def _clause(label: str) -> str:
    """One prompt clause: its label line up to the next label line.

    The end is the NEXT label line of any name, never a hardcoded
    neighbour, so inserting or reordering clauses cannot make one clause
    absorb another and let a sibling's tokens satisfy its arms.
    """
    prompt = _prompt()
    starts = [m for m in CLAUSE_LABEL_LINE.finditer(prompt) if m.group("label") == label]
    assert len(starts) == 1, (
        f"expected exactly 1 {label!r} clause line in the cold prompt, "
        f"found {len(starts)}"
    )
    nxt = CLAUSE_LABEL_LINE.search(prompt, starts[0].end())
    return prompt[starts[0].start():nxt.start() if nxt else None]


def _paragraph(marker: str) -> str:
    """The lead-facing paragraph of the section that contains `marker`."""
    return _region(_lead_prose(), marker, "the cold lane's lead-facing prose")


def _placeholder_list() -> str:
    """The placeholder definitions: the bullet list just before the fence."""
    before = _split_section()[0]
    start = before.find("Change only the placeholders")
    assert start != -1, (
        "the cold lane lost its 'Change only the placeholders' list — the "
        "prompt's placeholders are then undefined for the lead"
    )
    return before[start:]


def _spawn_reference() -> str:
    """The single inline Agent(...) call reference in the lead-facing prose.

    Exactly one is required: the spawn shape is the lane's defining
    constraint, so a second Agent( form is drift even if the first is
    correct.
    """
    refs = re.findall(r"Agent\(([^)]*)\)", _lead_prose())
    assert len(refs) == 1, (
        f"expected exactly 1 inline Agent(...) reference in the cold lane "
        f"section, found {len(refs)}: {refs!r}"
    )
    return refs[0]


def _standard_combination_block() -> str:
    """The always-included reviewer list, before the conditional list."""
    text = _peer_review_text()
    start = text.find("Standard reviewer combination (always included):")
    assert start != -1, "peer-review.md lost the standard reviewer combination list"
    end = text.find("Conditional reviewers", start)
    assert end != -1, "the standard list no longer precedes the conditional list"
    return text[start:end]


def _table_row(text: str, first_cell: str, what: str) -> str:
    """The one markdown table row whose first cell begins with `first_cell`."""
    rows = [ln for ln in text.splitlines() if ln.startswith(f"| {first_cell}")]
    assert len(rows) == 1, (
        f"{what}: expected exactly 1 table row starting {first_cell!r}, "
        f"found {len(rows)}"
    )
    return rows[0]


# The lane's lifecycle steps, in order: bold lead-ins "**Label.**".
STEP_LABEL_LINE = re.compile(r"^\*\*([A-Z][^*\n]*)\.\*\* ", re.MULTILINE)
LIFECYCLE_STEPS = (
    "Dispatch",
    "Spawn result",
    "While it runs",
    "Return",
    "Validity",
    "No report or an invalid one",
    "Synthesis",
    "Remediation",
    "Re-check",
    "Failed cycle",
)
# Once guard: each lifecycle rule's operative phrase occurs exactly once in
# peer-review.md. Cross-references use the bold step label instead.
ONCE_RULES = (
    ("record the subagent's agentId", "the agentId record at spawn"),
    ("HALT` by its recorded agentId", "the HALT route"),
    ("the HALT lands at its next tool call", "the HALT timing"),
    ("Append every report verbatim to the record", "the verbatim record"),
    ("every other file of your own that would carry a finding or a disposition", "the hold's file-set extension"),
    ("is a blocker report when", "the blocker-report trigger"),
    ("is never valid", "the HALT and blocker invalidity rule"),
    ("A first-round cold report is valid only with", "the first-round validity list"),
    ("A re-check report is valid only with", "the re-check validity list"),
    ("run one fresh cold dispatch", "the no-report rule"),
    ("Each round gets that one fresh dispatch at most", "the per-round dispatch bound"),
    ("The status row carries no severity", "the did-not-run row's routing"),
    ("Send it no message other than a HALT or the re-check message", "the lead's message bar"),
    ("when the record holds no valid first-round cold report", "the first-round fallback in a re-review"),
    ("whichever report raised it", "the re-check list's scope"),
    ("ends in exactly one disposition", "the disposition rule"),
    ("verified invalid, when you find", "the verified-invalid disposition"),
    ("declined, when the user declines it at Step C", "the declined disposition"),
    ('answers "No" at Step A', "the Step A decline"),
    ("declines it at the merge-readiness question below", "the at-gate decline"),
    ("Record every disposition, resolved included", "the disposition record rule"),
    ("a finding has no disposition until that line exists", "the line-is-the-disposition rule"),
    ("List each verified-invalid blocking cold finding", "the user-visible invalidation"),
    ("ask one `AskUserQuestion` per finding", "the per-finding merge-readiness question"),
    ('"Run one more verify-only re-review round", described as', "the question's re-review option"),
    ("with every placeholder value it was dispatched with", "the full placeholder record at spawn"),
    ("in the changed code at `{head}`", "the evidence location for verified invalid"),
    ("resume its subagent once in the same round", "the same-round re-check after a first-round fallback"),
    ("even when the list is empty", "the no-skip rule"),
    ("send it instead as a fresh cold dispatch", "the fresh re-check after imPACT corrections"),
    ("or another reason, stated", "the REPORT reason set"),
    ("for any reason, or none, other than", "the blocker trigger's reason test"),
    ("routes it to a tracked GitHub issue there", "the tracked-issue decline"),
    ("The re-check message is the paragraph below", "the re-check message"),
    ("Use it in every round", "the fixed base"),
    ("still triages it", "the retry blocker's triage"),
    ("Cold findings count under the **Failed cycle** test", "the cold failed-cycle rule"),
    ("in the same parallel block as the warm reviewers", "the same-block spawn rule"),
    ("placeholder values `/PACT:imPACT` corrects", "the imPACT-correction record"),
    ("a lead line of corrected placeholder values", "the resume test on corrections"),
    ("greedy-fix exclusion summary excludes it", "the greedy-fix decline"),
    ("as a status row with no severity, which step 4 does not route", "the verified-invalid status row"),
    ("a resume other than a same-round re-check", "the retry exemption for the same-round re-check"),
    ("However many findings choose it, run one such round", "the one-round bound on re-review answers"),
    ("filling each slot in angle brackets from that finding in the record", "the question's slot-filling rule"),
    ("the valid first-round report is the round's end state instead", "the same-round re-check fallback"),
    ("Choosing the second option starts that verify-only re-review round", "the re-review option's effect"),
    ("Never use the cold reviewer as a fixer and never reassign it", "the never-a-fixer rule"),
)
# Phrases a frozen literal legitimately repeats: the per-finding question's
# text names the re-review option, and the option label names it again, so
# the phrase occurs exactly twice in the lane and the file. A third copy (a
# restated rule) still fails.
EXACT_COUNT_RULES = (
    ("run one more verify-only re-review round", 2, "the re-review option, in the question text and the option label"),
)
# Once phrases whose home is step 5 (merge readiness), outside the lane: each
# occurs exactly once in step 5's own item and never in the lane.
STEP5_ONCE_RULES = (
    ("lacks a disposition", "the merge gate on undisposed blocking findings"),
    ("Name each verified-invalid blocking cold finding", "the merge statement's invalidation list"),
)

# Class guard (a): an angle-bracket slot is a placeholder the list does not define.
ANGLE_SLOT = re.compile(r"<[a-z]+(?:-[a-z]+)+>")

# Class guard (b): the report elements each validity list must name, in
# REPORT's own nouns. Two REPORT parts have no key here: the findings
# element (the ';' list's first entry, counted as the +1) and the opening,
# which precedes the ';' list and is handled by DIFF_CLAIMS_OPENING below.
# The REPORT element count is asserted against this map.
REPORT_ELEMENTS_KEYS = (
    "nothing-found line for each severity",
    "merge-block sentence",
    "no open questions",
    "coverage boundary with its receipt",
)
# The opening sits before REPORT's ';' list, so it is outside the count and
# segment checks; it is required in the first-round list only, because the
# re-check message replaces REPORT. (REPORT phrase, validity-list phrase.)
DIFF_CLAIMS_OPENING = ("open with what the diff claims to do", "the diff-claims opening")
# Per-finding elements of the re-check, bound with their quantifier so a
# rewrite to "a status for some findings" fails. (checklist, validity list)
RECHECK_QUANTIFIED = (
    ("For each finding, give its status", "a status for every listed finding"),
    ("For each new issue, give its severity", "the severity, file:line and failure scenario of each new issue"),
)
RECHECK_ELEMENTS = (
    "failure scenario",
    "merge-block sentence",
    "no open questions",
    "coverage boundary with its receipt",
)
FIRST_ROUND_VALIDITY = "A first-round cold report is valid only with"
RECHECK_VALIDITY = "A re-check report is valid only with"
# Stems of machinery the lane deliberately dropped; they must not return to
# it. Matched against the lowercased lane, so a capitalised or inflected
# form ('Superseded', 'restarts', 'ancestors') is caught too. Bare 'next
# round' is deliberately absent: the lane legitimately says what the next
# round does.
RETIRED_LANE_STEMS = (
    "supersed",
    "restart",
    "merge-base",
    "ancestor",
    "latest round",
    "previous cold",
    "most recent cold round",
    "applies to the next round",
    "recorded skip",
    "no open cold findings",
    "skip the cold lane",
    "that no valid cold report has marked resolved",
    "alone holds merge readiness",
    # The old append-at-return timing, in full. The retained 'as soon as
    # `/PACT:imPACT` returns' is a different string and must not be matched.
    "as soon as it returns, before synthesizing",
    # The old stop valve that released on any imPACT outcome.
    "once it has handled that reviewer",
    # The release end that left a replaced reviewer holding forever.
    "calls for no replacement",
)
# Each validity list ends at its consequence clause.
VALIDITY_ENDS = ("; otherwise",)
VALIDITY_CONSEQUENCE = "; otherwise it is not valid."
# A word inside a validity list that turns a required element optional.
# Only the list span is scanned, so a negator elsewhere in the sentence
# (or a phrasing such as "is not needed" after the consequence) evades it.
VALIDITY_NEGATOR = re.compile(
    r"\b(?:without|except|excepting|optional|optionally|not|unless|may omit)\b",
    re.IGNORECASE,
)

# Class guard (c): reviewer paragraphs that name warm machinery without
# saying warm or cold, each scoped by a qualified sentence in its container.
# Key: the paragraph's first content line, first 60 characters.
REVIEWER_WORD = re.compile(r"reviewer", re.IGNORECASE)
WARM_MACHINERY = re.compile(
    r"TaskCreate|TaskUpdate|TaskList|TaskGet|\bTask [AB]\b|team_name|HANDOFF|open_questions|teachback|task_id|\breuse",
    re.IGNORECASE,
)
QUALIFIER = re.compile(r"\bwarm\b|\bcold\b", re.IGNORECASE)
_TGD_SCOPE = ("pr", "Each warm reviewer dispatch creates **two tasks**")
_DISPATCH_SCOPE = ("pr", WARM_SCOPING + ", follow the steps")
WARM_SCOPED_INVENTORY = {
    "> **Worktree scope reminder**: When reusing a reviewer as a ": (
        "pr", "When a warm reviewer's findings need fixing"),
    '- **Task A** — TEACHBACK gate. `subject = "{reviewer-type}: ': _TGD_SCOPE,
    "Both are created BEFORE the `Agent(...)` spawn call. The rev": _TGD_SCOPE,
    "# A-FIRST ORDERING (required): create Task A (the teachback ": _TGD_SCOPE,
    '1. `TaskCreate(subject="{reviewer-type}: TEACHBACK for revie': _DISPATCH_SCOPE,
    "6. **Journal event**: write one `variety_assessed` event per": _DISPATCH_SCOPE,
    "7. Spawn the reviewer with the canonical dispatch form. The ": _DISPATCH_SCOPE,
    "Agent(": _DISPATCH_SCOPE,
    "**HANDOFF review** (after every reviewer has reported — PRIM": (
        "pr", "The cold reviewer writes no HANDOFF"),
    "This is the **primary memory trigger** — fires unconditional": (
        "pr", "The cold reviewer writes no HANDOFF"),
    "**Mechanism**: The reviewer's understanding of the PR intent": (
        "pr", "Each warm reviewer should state"),
    "Monitor for blocker/algedonic signals via:": (
        "pr", "**`SendMessage`**: Teammates send blockers and algedonic signals directly to the team-lead"),
    "- After CODE phase specialists have reported → Standard Harv": (
        "persona", "Every agent except the peer-review cold reviewer delivers a structured HANDOFF"),
}


def _validity_sentence(marker: str) -> str:
    """One validity list: its marker up to its consequence clause."""
    region = _paragraph(marker)
    start = region.find(marker)
    ends = [i for i in (region.find(e, start) for e in VALIDITY_ENDS) if i != -1]
    assert ends, f"the {marker!r} list lost its consequence clause"
    return region[start:min(ends)]


def _checklist_quote() -> str:
    """The quoted re-check paragraph the lead sends, quotes stripped."""
    region = _paragraph('"RE-REVIEW CHECKLIST:').strip()
    assert region.startswith('"') and region.endswith('"'), (
        "the re-check paragraph is no longer one quoted paragraph"
    )
    return region[1:-1]


def _step5_item() -> str:
    """Step 5 (merge readiness) of peer-review.md: its numbered line alone."""
    lines = [ln for ln in _peer_review_text().splitlines() if ln.startswith("5. State merge readiness")]
    assert len(lines) == 1, "peer-review.md lost its step-5 merge-readiness line"
    return lines[0]


def _step(label: str) -> str:
    """The lifecycle step paragraph that opens with **label.**"""
    return _strip_ticks(_paragraph(f"**{label}.**"))


def _unqualified_reviewer_paragraphs() -> list[str]:
    """Anchors of reviewer paragraphs naming warm machinery with no warm/cold word.

    The cold section itself is excluded: it is the cold rules.
    """
    pr = _peer_review_text()
    start = pr.find(SECTION_HEADING)
    end = pr.find(DISPATCH_REVIEWERS_LEAD, start)
    texts = [pr[:start] + pr[end:], _persona_text()]
    anchors = []
    for text in texts:
        for para in re.split(r"\n[ \t]*\n", text):
            lines = [ln.strip() for ln in para.splitlines()
                     if ln.strip() and not ln.strip().startswith("```")]
            if not lines or all(re.match(r"#{2,6} ", ln) for ln in lines):
                continue
            if (REVIEWER_WORD.search(para) and WARM_MACHINERY.search(para)
                    and not QUALIFIER.search(para)):
                anchors.append(lines[0][:60])
    return anchors


class TestSectionAnchor:
    def test_section_heading_present(self):
        assert SECTION_HEADING in _peer_review_text(), (
            "peer-review.md lost the Cold Reviewer Lane section heading"
        )

    def test_section_closes_before_warm_dispatch(self):
        assert len(_cold_section()) > 400, (
            "the cold lane section is too short to carry the dispatch "
            "contract — an edit truncated it"
        )


class TestWarmDispatchScoping:
    """The per-reviewer dispatch steps that follow the section are scoped to
    WARM reviewers. Without the scoping word, 'for each reviewer' sends the
    always-included cold lane through Task A + Task B and a named teammate
    spawn — the conversion the section exists to prevent."""

    def test_dispatch_steps_scoped_to_warm_reviewers(self):
        text = _peer_review_text()
        start = text.find(DISPATCH_REVIEWERS_LEAD)
        assert start != -1, (
            f"peer-review.md lost the {DISPATCH_REVIEWERS_LEAD!r} paragraph"
        )
        line = text[start:text.find("\n", start)]
        assert WARM_SCOPING in line, (
            f"the dispatch-reviewers lead-in no longer says {WARM_SCOPING!r} "
            f"— the Teachback-Gated Dispatch steps now read as applying to "
            f"the cold reviewer too: {line!r}"
        )

    def test_completion_check_skips_the_cold_reviewer(self):
        region = _region(
            _peer_review_text(), "**Verify agent task completion**",
            "peer-review.md",
        )
        assert "The cold reviewer owns no task" in region, (
            "the task-completion check no longer exempts the cold reviewer "
            "— a lead following it looks for a task the lane never had"
        )


class TestPromptShape:
    """The cold prompt is one fenced block sent verbatim. An ad-hoc prompt
    composed from bullets is unpinned by construction: the text actually
    sent would be whatever the lead wrote that day."""

    def test_prompt_is_sent_verbatim(self):
        before = _split_section()[0]
        assert "Send the cold prompt below verbatim" in before, (
            "the cold lane no longer tells the lead to send the prompt "
            "verbatim — the fence becomes an example the lead paraphrases"
        )

    def test_clause_labels_in_order(self):
        labels = tuple(m.group("label") for m in CLAUSE_LABEL_LINE.finditer(_prompt()))
        assert labels == PROMPT_LABELS, (
            f"the cold prompt's clause labels changed: expected "
            f"{PROMPT_LABELS!r}, found {labels!r}"
        )

    def test_re_check_append_is_bounded(self):
        before = _split_section()[0]
        assert "a re-check also appends the re-check message (see **Re-check**), and nothing else" in before, (
            "the cold lane no longer bounds what a re-check dispatch adds to "
            "the verbatim prompt"
        )

    def test_prompt_has_no_backticks(self):
        assert "`" not in _prompt(), (
            "the cold prompt carries a backtick — the fence is pasted "
            "verbatim, and a backtick span in it reaches the subagent as "
            "markup or, in a shell, as command substitution"
        )

    def test_every_used_placeholder_is_defined(self):
        used = set(PLACEHOLDER.findall(_prompt()))
        defined = set(PLACEHOLDER.findall(_placeholder_list()))
        assert used, "the cold prompt carries no placeholders — the fence lost its slots"
        assert used == defined, (
            f"the cold prompt's placeholders and the lead's placeholder list "
            f"disagree: used only {sorted(used - defined)!r}, defined only "
            f"{sorted(defined - used)!r}"
        )


class TestArtifactClause:
    """The diff is the only artifact. A subagent with no team and no tasks
    can still reach the author's account through the PR surfaces and the
    commit-message bodies, so the bar is in the contract."""

    @pytest.mark.parametrize("token, lost", [
        ("git diff {base}...{head}", "the instruction to run the diff itself"),
        ("gh pr view", "the bar on reading the PR body"),
        ("gh api", "the bar on reading the PR comments"),
        ("commit-message bodies", "the bar on reading commit-message bodies"),
        ("commit-message subjects", ("the bar on commit subjects, which "
         "reach the reviewer through a one-line log")),
        ("any git log message text are barred, because", ("the operative "
         "verb with its left and right context — 'are not barred' fails, "
         "and without the verb the surfaces read as a list")),
    ])
    def test_artifact_token_present(self, token, lost):
        assert token in _clause("ARTIFACT"), (
            f"the cold prompt's ARTIFACT clause lost {lost} ({token!r})"
        )


class TestAvoidList:
    """The information diet, named by PATH (and store), not by concept:
    'do not read the author's plan' is easy to rationalise past when the
    path is handed over."""

    @pytest.mark.parametrize("token, lost", [
        ("docs/plans/", "the plan store"),
        ("docs/architecture/", "the architecture contract store"),
        ("docs/review/", "the review-doc store"),
        ("CLAUDE.md on disk", "the managed-block file — the whole file is off-list"),
        ("team task directory", ("the task store, where dispatches and "
         "HANDOFFs carry the narrative")),
        ("handoff files", "upstream narrative in file form"),
        ("pact-memory stores", "the shared per-project memory store"),
    ])
    def test_avoid_token_present(self, token, lost):
        assert token in _clause("AVOID by path"), (
            f"the cold prompt's AVOID clause no longer names {lost} "
            f"({token!r}) by its literal path/store"
        )


class TestChannelsClause:
    """The channel prohibitions. The subagent shape closes the team inbox
    by construction, but task tools and messaging remain loadable for a
    plain subagent — the bar is load-bearing because the construction does
    not reach them."""

    @pytest.mark.parametrize("token, lost", [
        ("do not use SendMessage", ("the bar on messaging reviewers, "
         "secretary, or lead about the work")),
        ("Do not load or use task tools", "the bar on the task tools"),
        ("TaskCreate", "the task write tools — creating a task joins the store"),
        ("TaskUpdate", "the task write tools — ditto"),
        ("TaskList", "the task read tools — the task store carries the narrative"),
        ("TaskGet", "the task read tools — ditto"),
        ("Do not query pact-memory", "the bar on pact-memory queries"),
        ("If the lead sends you HALT, stop at once and return your report so far, with its coverage boundary", (
         "the HALT instruction the lead's HALT route relies on")),
    ])
    def test_channels_token_present(self, token, lost):
        assert token in _clause("CHANNELS"), (
            f"the cold prompt's CHANNELS clause lost {lost} ({token!r})"
        )


class TestDisregardClause:
    """What arrives anyway. The avoid list governs what the reviewer reads;
    this clause governs what the harness places in its context regardless,
    and it must keep the safety rules that arrive by the same route."""

    @pytest.mark.parametrize("token, lost", [
        ("CLAUDE.md sections", "the managed-block arrival surface"),
        ("auto-memory index", "the harness-placed memory index"),
        ("hook advisories", "the hook advisories (claim-a-task advice)"),
        ("harness notices", "the harness notices (the team roster)"),
        ("inadmissible", ("the operative verb — context may arrive, but "
         "relying on it must stay forbidden")),
        ("Advisories about tasks or teams do not apply to you", (
         "the carve-out that stops a task or roster advisory pulling the "
         "subagent into the team")),
        ("Safety and tool-use rules in those sections still apply", (
         "the safety exception — without it the disregard strips the "
         "shell-safety rules from a write-capable subagent")),
        ("where one conflicts with this prompt, this prompt wins", (
         "the precedence rule — without it a kept rule such as 'query the "
         "secretary' overrides the prompt's channel bars")),
        ("a listing of recent commits", ("the harness's recent-commits "
         "listing, which carries commit subjects")),
    ])
    def test_disregard_token_present(self, token, lost):
        assert token in _clause("DISREGARD"), (
            f"the cold prompt's DISREGARD clause lost {lost} ({token!r})"
        )

    def test_no_dispatch_only_guidance_rule(self):
        assert "admissible only from this dispatch" not in _cold_section(), (
            "the cold lane again says operational guidance is admissible "
            "only from the dispatch — that strips the safety rules the "
            "DISREGARD clause keeps"
        )


class TestReadOnlyClause:
    """The cold subagent can write and run shell commands in a checkout
    whose remote is real, and it disregards the instruction files that
    would otherwise carry the safety rules — so they are restated here."""

    @pytest.mark.parametrize("token, lost", [
        ("do not edit, stage, commit", "the bar on changing the worktree"),
        ("{scratch_dir}", "the scratch-copy route for mutation probes"),
        ("files that a test or lint run writes and git ignores", (
         "the cache exception's scope — without it the required test and "
         "lint runs violate the read-only rule")),
        (".ruff_cache, .hypothesis) are excepted", ("the exception's "
         "operative verb with its lint cache — 'are not excepted' fails")),
        ("quoted heredoc", "the quoted-heredoc rule for prose and command text"),
        ("never with an unquoted heredoc", "the unquoted-heredoc prohibition"),
        ("Never run a push, merge, close or branch-delete command", (
         "the guarded-command bar")),
    ])
    def test_read_only_token_present(self, token, lost):
        assert token in _clause("READ-ONLY"), (
            f"the cold prompt's READ-ONLY clause lost {lost} ({token!r})"
        )


class TestOperationalFactsClause:
    """A cold reviewer told to avoid the plan, review docs and CLAUDE.md
    cannot find the project's test and lint instructions there, so the
    prompt must carry them; without them its evidence shrinks to static
    tracing."""

    @pytest.mark.parametrize("token, lost", [
        ("{operational_facts}", "the slot the lead fills"),
        ("run the tests and the lint gate", "the instruction to use the facts"),
        ("Do not look for run instructions in the avoided paths", (
         "the bar on recovering run instructions from the avoided paths")),
        ("untracked docs/", "the path-convention fact (untracked docs/ is normal)"),
    ])
    def test_operational_facts_token_present(self, token, lost):
        assert token in _clause("OPERATIONAL FACTS"), (
            f"the cold prompt's OPERATIONAL FACTS clause lost {lost} ({token!r})"
        )

    @pytest.mark.parametrize("token, lost", [
        ("run the project's tests", "how to run the project's tests"),
        ("venv", "the interpreter or venv form"),
        ("lint gate command", "the lint gate command"),
        ("path convention", "the path conventions"),
    ])
    def test_operational_facts_placeholder_defined(self, token, lost):
        lines = [ln for ln in _placeholder_list().splitlines() if "{operational_facts}" in ln]
        assert len(lines) == 1, "the {operational_facts} placeholder lost its definition line"
        assert token in lines[0], (
            f"the {{operational_facts}} definition lost {lost} ({token!r}) — "
            f"an under-equipped lane produces static-tracing evidence only"
        )


class TestQuestionClause:
    """The operative question. 'Does this diff implement the plan' is the
    warm lanes' question; the cold lane's value depends on asking the
    other one."""

    def test_question_phrase_present(self):
        assert '"what is wrong with this diff"' in _clause("QUESTION"), (
            "the cold prompt's QUESTION no longer asks 'what is wrong with "
            "this diff' — a drift toward the plan-conformance question "
            "would erase the frame break the lane exists to buy"
        )

    def test_question_names_replica_tests(self):
        assert "replica" in _clause("QUESTION"), (
            "the cold prompt's QUESTION lost its replica clause — tests that "
            "exercise a replica of production logic are where warm lanes "
            "agree with the story"
        )


class TestReportClause:
    """The REPORT shape that makes a null result legible."""

    @pytest.mark.parametrize("token, lost", [
        ("derived from the diff alone", ("the report's opening claim must "
         "come from the diff, never the PR description")),
        ("a nothing-found line for each severity", ("the per-severity explicit "
         "no-findings lines — absent findings and an absent review look "
         "identical without them")),
        ("a merge-block sentence, saying what would block a merge", ("the global merge judgement, required "
         "even when nothing would block")),
        ('or the line "no open questions"', ("the explicit none-form — "
         "without it an omitted element reads as 'none' and the merge gate "
         "passes vacuously")),
        ("the open questions you could not settle", ("the questions the diff could not settle — they "
         "get a disposition row like a HANDOFF's open_questions")),
        ("and each changed path whose diff hunk you did not read, with the reason: you could not read it, you judged it not review-relevant, or another reason, stated", ("the blocker report the "
         "persona's blocker exception relies on")),
        ("coverage boundary with its receipt", ("the required boundary — a partial review "
         "must be visible as partial")),
    ])
    def test_report_token_present(self, token, lost):
        assert token in _clause("REPORT"), (
            f"the cold prompt's REPORT clause lost {lost} ({token!r})"
        )

    @pytest.mark.parametrize("token", ["blocking", "suggestion", "nitpick"])
    def test_severity_enum_token_present(self, token):
        assert token in _clause("REPORT"), (
            f"the cold prompt's severity vocabulary lost {token!r} — the "
            f"review_finding journal event consumes this enum"
        )


class TestSpawnShape:
    """The spawn reference carries the anonymous-subagent shape, and the
    sentence around it requires that shape. A cold dispatch written by
    copying the warm template inherits name=/team_name=/the role prelude —
    a teammate spawn that joins the team and defeats the lane while every
    census stays green."""

    def test_subagent_type_is_general_purpose(self):
        ref = _spawn_reference()
        assert 'subagent_type="general-purpose"' in ref, (
            f"the cold spawn reference is not a general-purpose subagent: {ref!r}"
        )

    def test_spawn_description_is_the_fixed_literal(self):
        ref = _spawn_reference()
        assert 'description="Cold diff-only review"' in ref, (
            f"the cold spawn reference lost its fixed description literal — "
            f"the lead then invents one, and a warm-style description names "
            f"the reviewer as a specialist: {ref!r}"
        )

    def test_spawn_carries_description_and_prompt(self):
        ref = _spawn_reference()
        assert "description=" in ref and "prompt=" in ref, (
            f"the cold spawn reference lost its description/prompt params: {ref!r}"
        )

    def test_no_name_param(self):
        assert not NAME_PARAM.search(_spawn_reference()), (
            "the cold spawn reference carries name= — an anonymous "
            "subagent has none; naming it makes it addressable as a teammate"
        )

    def test_no_team_name_param(self):
        assert "team_name" not in _spawn_reference(), (
            "the cold spawn reference carries team_name= — that single "
            "param is what joins an agent to the team's task store and inbox"
        )

    def test_no_role_prelude(self):
        assert "YOUR PACT ROLE" not in _spawn_reference(), (
            "the cold spawn reference carries the teammate role prelude — "
            "the marker that routes a spawn into the teammate protocol"
        )

    @pytest.mark.parametrize("token, lost", [
        ("NO name=, NO team_name=, and NO teammate role prelude", (
         "the prohibition itself — the call literal alone is an example, "
         "not an instruction")),
        ("dispatched with every review, in the same parallel block as the warm reviewers", (
         "the always-dispatched, same-block rule — pinned with its left "
         "context so a negated 'not in the same parallel block' fails; "
         "dispatched after the warm reports, the lane inherits their "
         "findings as narrative")),
        ("Do not convert it to a teammate spawn", "the no-conversion rule"),
        ("do not create tasks for it", "the no-tasks rule"),
    ])
    def test_spawn_prose_present(self, token, lost):
        region = _strip_ticks(_paragraph("The cold reviewer is dispatched with"))
        assert token in region, (
            f"the cold spawn paragraph lost {lost} ({token!r})"
        )


class TestLifecycleOrder:
    """The lane runs as one ordered list of steps. The order arm holds the
    step labels' sequence; the once arm holds each listed rule's operative
    phrase to a single occurrence, case-insensitively, both in its home
    (the lane, or step 5's line) and in the whole file. A
    paraphrased second copy of a rule, or a rule absent from both lists,
    is not caught."""

    def test_lead_in_states_the_order_rule(self):
        assert "Run the cold lane once in each review round — the initial review, any later full review round, and each verify-only re-review — in the order below; each rule applies where it is written." in _lead_prose(), (
            "the lane lost its ordering lead-in"
        )

    def test_steps_in_order(self):
        labels = tuple(STEP_LABEL_LINE.findall(_lead_prose()))
        assert labels == LIFECYCLE_STEPS, (
            f"the cold lane's lifecycle steps changed: expected "
            f"{LIFECYCLE_STEPS!r}, found {labels!r}"
        )

    @pytest.mark.parametrize("term", RETIRED_LANE_STEMS)
    def test_retired_term_absent_from_lane(self, term):
        assert term not in _cold_section().lower(), (
            f"the cold lane again uses {term!r} — machinery the lane dropped "
            f"(restart, supersession, a moving re-check base, round "
            f"bookkeeping, the re-check skip, the old append-at-return "
            f"timing, and the stop valve that released on any imPACT outcome); "
            f"every round now reviews the whole PR with one retry, and a cold "
            f"report is held until every warm reviewer of its round is accepted "
            f"or stopped with TaskStop"
        )

    def test_no_third_failure_term(self):
        assert "review-did-not-happen" not in _peer_review_text(), (
            "peer-review.md again uses 'review-did-not-happen' — a failure "
            "outcome nothing downstream reads; an incomplete report is 'not "
            "valid' and the lane's one failure outcome is 'did not run'"
        )

    @pytest.mark.parametrize("phrase, rule", ONCE_RULES)
    def test_rule_stated_once(self, phrase, rule):
        # Case-insensitive, so a copy that starts a sentence ('Ask one ...'
        # beside 'ask one ...') still counts. Once in the lane AND once in
        # the whole file: a verbatim copy pasted anywhere else (step 4, say)
        # is a second statement of the rule.
        in_lane = _cold_section().lower().count(phrase.lower())
        in_file = _peer_review_text().lower().count(phrase.lower())
        assert in_lane == 1 and in_file == 1, (
            f"{rule} must be stated once, in the cold lane ({phrase!r}): "
            f"the lane has {in_lane}, the file has {in_file} — a second copy "
            f"drifts from the first"
        )

    @pytest.mark.parametrize("phrase, expected, rule", EXACT_COUNT_RULES)
    def test_rule_stated_exact_count(self, phrase, expected, rule):
        in_lane = _cold_section().lower().count(phrase.lower())
        in_file = _peer_review_text().lower().count(phrase.lower())
        assert in_lane == expected and in_file == expected, (
            f"{rule} must occur exactly {expected} times, all in the cold "
            f"lane ({phrase!r}): the lane has {in_lane}, the file has {in_file}"
        )

    @pytest.mark.parametrize("phrase, rule", STEP5_ONCE_RULES)
    def test_step5_rule_stated_once_at_home(self, phrase, rule):
        home = _step5_item().lower().count(phrase.lower())
        in_file = _peer_review_text().lower().count(phrase.lower())
        assert home == 1 and in_file == 1, (
            f"{rule} must be stated once, in step 5 ({phrase!r}): step 5 "
            f"has {home}, the file has {in_file}"
        )


class TestLeadFacingRules:
    """Each lifecycle step's operative clauses, bound to its own step."""

    @pytest.mark.parametrize("step, token, lost", [
        ("Dispatch", "This lane skips the Task A + Task B sequence, the variety stamp, and the per-reviewer journal block", (
         "the skip sentence as one phrase — the per-reviewer procedure "
         "otherwise re-absorbs the cold lane")),
        ("Dispatch", 'reviewers array as the string "cold-reviewer"', (
         "the named review_dispatch string")),
        ("Spawn result", "record the subagent's agentId at once in the review doc's \"Cold report (verbatim)\" section", (
         "the agentId record at spawn — resume and HALT need it")),
        ("Spawn result", "with every placeholder value it was dispatched with", (
         "the full placeholder record — a fresh dispatch must reproduce every value")),
        ("Spawn result", "create the review doc with that section if it does not exist yet", (
         "the record's creation rule")),
        ("Spawn result", "keep it in a sibling file the review doc names; every mention of the record below means that file", (
         "the sibling-file rule")),
        ("While it runs", "SendMessage it HALT by its recorded agentId", "the HALT route"),
        ("While it runs", "the HALT lands at its next tool call, and it returns its report so far", (
         "the HALT timing and what comes back")),
        ("Return", "writes no HANDOFF and messages no one; its final report is its return", (
         "the return path")),
        ("Return", "Append every report verbatim to the record under its agentId", "the verbatim record"),
        ("Return", "while a warm reviewer of the report's round holds the release", (
         "the hold's trigger — a warm reviewer of the report's round holds the release")),
        ("Return", "hold the report in your own context and write it to no file", (
         "the hold and its write bar")),
        ("Return", "the review doc, a sibling file and a scratch file are the same leak", (
         "why no file is safe — a sibling or scratch file leaks like the review doc")),
        ("Return", "because a file this session writes reaches a live teammate's context when it changes", (
         "the causal clause — any file this session writes reaches a live teammate")),
        ("Return", "is one you dispatched in that round or reused in it by SendMessage", (
         "the holder set — dispatched in the round or reused in it by SendMessage")),
        ("Return", "until you accept a HANDOFF it delivered for that round", (
         "release end 1 — an accepted HANDOFF for that round")),
        # Closed by its semicolon: a condition appended after TaskStop (a
        # replacement's acceptance, say) would bring back the never-release.
        ("Return", "or until it is stopped with TaskStop;", (
         "release end 2 — any TaskStop, with nothing appended")),
        ("Return", "a replacement that /PACT:imPACT calls for holds the release from that moment", (
         "the replacement holds from the moment it is called for")),
        ("Return", "before you stop its predecessor or spawn it", (
         "the handover — the replacement holds before its predecessor's stop")),
        ("Return", "a TaskStop that reports the agent not found counts as stopped", (
         "a not-found TaskStop counts as stopped")),
        ("Return", "You accept a HANDOFF when you validate it, mark its task completed and send the acceptance SendMessage", (
         "the acceptance definition — validate, mark completed, send the acceptance")),
        ("Return", "a reviewer's task status is not a delivery", (
         "task status is not a delivery")),
        ("Return", "a task marked completed by termination, stall recovery or a redo is not an acceptance", (
         "a termination, stall or redo completion is not an acceptance")),
        ("Return", "Every other /PACT:imPACT outcome leaves the reviewer holding the release", (
         "every other imPACT outcome keeps the hold")),
        ("Return", "told to continue, augmented, escalated, sent to a redo or to /PACT:rePACT, or re-invoked", (
         "the enumerated imPACT outcomes that keep the hold")),
        ("Return", "and so does stall recovery that marks its task completed and spawns a new teammate without TaskStop", (
         "stall recovery without TaskStop keeps the hold")),
        ("Return", "until you stop the old teammate with TaskStop, which you do once the new teammate is spawned", (
         "the stall-recovery old teammate holds until stopped, once its successor is spawned")),
        ("Return", "that new teammate holds the release too", (
         "the stall-recovery teammate holds the release too")),
        ("Return", "The hold delays writes and messages only", (
         "the hold's scope — writes and messages")),
        ("Return", "does not delay triage", (
         "the write/decide boundary — triage is not delayed")),
        ("Return", "every other file of your own that would carry a finding or a disposition", (
         "the hold's file-set extension")),
        ("Return", "the synthesis included", (
         "the synthesis named in the hold's file set")),
        ("Return", "every review_finding journal write", (
         "the hold's journal-write extension")),
        ("Return", "It also covers what a reviewer that holds the release can read", (
         "the hold covers what a holder can read")),
        ("Return", "put no held cold finding in a SendMessage to that reviewer or to the secretary", (
         "the message bar — no held finding to a holder or the secretary")),
        ("Return", "in the subject, description or metadata of a task assigned to that reviewer or named in its task", (
         "the task-content bar — a holder's task and tasks named in it")),
        ("Return", "or in the subject, description or metadata of any task you create", (
         "the created-task bar — subject, description and metadata of any task you create")),
        # Closed by its period: the bare phrase is a prefix of the old
        # "every task's subject" reason and would survive its return.
        ("Return", "because every teammate can read every task.", (
         "why every task you create is barred — every teammate can read every task")),
        ("Return", "The hold does not cover the spawn-time agentId and placeholder record, which **Spawn result** requires at once.", (
         "the hold's one write exemption — the spawn record, and nothing else")),
        ("Return", "If you can no longer reproduce a held report verbatim", (
         "the unreproducible-report fallback trigger")),
        ("Return", "follow **No report or an invalid one** for a report other than a same-round re-check, treating it as no report", (
         "the non-re-check fallback route")),
        ("Return", 'bound may already be spent, in which case the round ends with the "Cold lane did not run" row and line', (
         "the spent-bound outcome — the round ends with the did-not-run row and line")),
        ("Return", "follow **Re-check** for a lost same-round re-check report, whose fallback makes the valid first-round report the round's end state, with no fresh dispatch", (
         "the lost same-round re-check fallback and its no-fresh-dispatch outcome")),
        ("Return", "Append the held report and write the held files", (
         "the release action — append the held report and write the held files")),
        ("Return", "when no warm reviewer of the report's round still holds the release", (
         "the release condition — no warm reviewer of the round still holds the release")),
        ("Return", "in every case before you write the synthesis", (
         "the append-before-synthesis-write ordering")),
        ("Return", "is making no progress counts as a stop", (
         "the progress state — a reviewer still working is not a stop")),
        ("Return", "has delivered no HANDOFF", (
         "the first clause — no HANDOFF delivered")),
        ("Return", "you rejected its HANDOFF and it has drawn no revised HANDOFF", (
         "the second clause's delivery half — no revised HANDOFF after a rejection")),
        ("Return", "run /PACT:imPACT on it", (
         "the stopped-reviewer action — run imPACT on the stop")),
        ("Return", "the stop itself releases nothing", (
         "the stop is not a release")),
        ("Return", "whose HANDOFF is delivered and awaiting your decision", (
         "the exclusion's subject — a delivered HANDOFF awaiting decision")),
        ("Return", "is not a stop, and it holds the release", (
         "the exclusion — a delivered HANDOFF awaiting decision is not a stop")),
        ("Return", "A reviewer that becomes live again in the round", (
         "the re-hold subject — a reviewer live again in the round")),
        ("Return", "its HANDOFF rejected and under revision", (
         "the re-hold trigger — a rejected HANDOFF under revision")),
        ("Return", "re-dispatched by /PACT:imPACT", (
         "the re-hold trigger — an imPACT re-dispatch")),
        ("Return", "sent new work by SendMessage", (
         "the re-hold trigger — new work by SendMessage")),
        ("Return", "or resumed after a TaskStop by any SendMessage to its name", (
         "the re-hold trigger — any message resumes a stopped reviewer")),
        # Closed by its period, for the same reason as release end 2.
        ("Return", "holds the release again until you accept its new HANDOFF or it is stopped with TaskStop.", (
         "the re-hold and its two ends, with nothing appended")),
        ("Return", "A report not cut short by a HALT is a blocker report when its coverage boundary names a changed path whose diff hunk it did not read for any reason, or none, other than that the path is not review-relevant", (
         "the blocker trigger with its HALT carve-out, matching REPORT's reasons")),
        ("Return", "or a path it judged not review-relevant that you judge clearly review-relevant", (
         "the lead's reclassification and its bound ('clearly')")),
        ("Return", "a skipped path that is not review-relevant (a version bump, a lockfile, a generated file) and a path read but whose tests did not run are coverage notes", (
         "the trigger's boundary — untested or irrelevant is not a blocker")),
        ("Return", "Record any placeholder values /PACT:imPACT corrects as a labelled lead line in the record, appended immediately after the report it corrects", (
         "the record of imPACT's corrections and its append position")),
        ("Return", "as soon as /PACT:imPACT returns when that report is already in the record", (
         "the correction line's in-record branch — append it when imPACT returns")),
        ("Return", "while that report is held, hold the line with it and append it immediately after the report at the release", (
         "the correction line's held branch — held with the report, appended after it at the release")),
        ("Return", "Run /PACT:imPACT on a blocker report, and do not synthesize the lane as clean", (
         "the blocker route and the bar on reading a blocked lane as clean")),
        ("Validity", "A report cut short by a HALT, or a blocker report, is never valid", (
         "the HALT and blocker invalidity rule")),
        ("Validity", "a severity, file:line and failure scenario for each finding", (
         "the per-finding fields, bound with their quantifier")),
        ("Validity", "; otherwise it is not valid", (
         "the validity consequence — an incomplete report is not valid, and the next step handles it")),
        ("No report or an invalid one", "When a cold dispatch, or a resume other than a same-round re-check (as **Re-check** says), returns no report or a report that is not valid, or such a resume is not possible, run one fresh cold dispatch", (
         "the re-dispatch trigger, all three causes")),
        ("No report or an invalid one", "after any HALT and any /PACT:imPACT are resolved", (
         "the re-dispatch timing")),
        ("No report or an invalid one", "with the placeholders as /PACT:imPACT corrected them and, for a re-check, the re-check message", (
         "the re-dispatch's placeholders and message")),
        ("No report or an invalid one", "Each round gets that one fresh dispatch at most", (
         "the per-round bound — no retry loop")),
        ("No report or an invalid one", "If it also returns no valid report, or /PACT:imPACT does not resolve the blocker, the cold lane did not run for this round", (
         "the did-not-run outcome, both causes")),
        ("No report or an invalid one", 'add the status row "Cold lane did not run" to the recommendations table', (
         "the did-not-run table row")),
        ("No report or an invalid one", 'the line "Cold lane did not run." to the merge-readiness statement, each naming the blocker when there was one', (
         "the did-not-run merge line")),
        ("No report or an invalid one", "When that dispatch returned a blocker report, /PACT:imPACT still triages it, and its corrections apply when the next round fills the placeholders", (
         "the retry blocker's triage and its carry into the next round")),
        ("No report or an invalid one", "The status row carries no severity, so step 4 does not route it", (
         "the row's routing — the lead invents no severity")),
        ("No report or an invalid one", "Do not treat completion of the spawn as the review", (
         "the completion-is-not-review rule")),
        ("Synthesis", 'Wait for the round\'s end state — a valid cold report or the "Cold lane did not run" line — before synthesizing', (
         "the wait rule, naming both end states and requiring a valid report")),
        ("Synthesis", "While a warm reviewer of this round still holds the release", (
         "the synthesis hold's trigger — a warm reviewer of this round still holds the release")),
        ("Synthesis", "hold the synthesis in your own context", (
         "the synthesis hold while warm reviewers are live")),
        ("Synthesis", "write no file that carries a finding or a disposition", (
         "the synthesis hold's write bar — a finding or a disposition")),
        ("Synthesis", "the release **Return** gives", (
         "the synthesis hold's release — the one **Return** gives")),
        ("Synthesis", "Every cold finding in the record ends in exactly one disposition", (
         "the disposition rule — every finding closes, exactly once")),
        ("Synthesis", "resolved, when a valid cold report marks it so", "the resolved disposition"),
        ("Synthesis", "verified invalid, when you find file:line evidence in the changed code at {head} that the finding is wrong", (
         "the verified-invalid disposition and its evidence bar — changed code, not a plan or review doc")),
        ("Synthesis", 'or declined, when the user declines it at Step C, routes it to a tracked GitHub issue there, answers "No" at Step A, which declines every cold minor and future finding, or declines it at the merge-readiness question below, or, for a cold minor or future finding, when the greedy-fix exclusion summary excludes it from fixing', (
         "the declined disposition and its five routes, the greedy route scoped to minors and futures")),
        ("Synthesis", "Record every disposition, resolved included, as its own line in the record, labelled as yours, with the evidence for a verified-invalid finding, and never edit the verbatim reports", (
         "the record discipline — every disposition, resolved included, is a lead line")),
        ("Synthesis", "a finding has no disposition until that line exists, and the re-check list and the merge gate read these lines only", (
         "the line-is-the-disposition rule and its readers")),
        ("Synthesis", "List each verified-invalid blocking cold finding, with its evidence, in the recommendations table as a status row with no severity, which step 4 does not route", (
         "the user-visible invalidation, kept out of step 4's fixer routing")),
        ("Synthesis", "When only blocking cold findings with no disposition hold merge readiness, ask one AskUserQuestion per finding", (
         "the per-finding merge-readiness question and its trigger")),
        ("Synthesis", "filling each slot in angle brackets from that finding in the record", (
         "the question's slot-filling rule")),
        ("Synthesis", 'the question "Cold finding [<severity>] <file:line>: <finding text>. This blocking finding has no disposition. Decline it, or run one more verify-only re-review round?"', (
         "the question's literal text")),
        ("Synthesis", '"Decline it", described as "Record it as declined by you; it no longer holds merge readiness."', (
         "the decline option's description")),
        ("Synthesis", '"Run one more verify-only re-review round", described as "Run one verify-only re-review round in which the cold re-check checks it."', (
         "the re-review option's description")),
        ("Synthesis", "However many findings choose it, run one such round; it runs the cold re-check only, which lists every finding still without a disposition", (
         "the one-round bound on re-review answers and its scope")),
        ("Synthesis", 'with the literal options "Decline it"', "the question's decline option"),
        ("Synthesis", '"Run one more verify-only re-review round"', "the question's re-review option"),
        ("Synthesis", "marking the recommended one with the (recommended) suffix and a one-line rationale as in C.2", (
         "the question's recommendation convention")),
        ("Synthesis", "Choosing the second option starts that verify-only re-review round, in which the cold re-check can mark the finding", (
         "what the re-review option does")),
        ("Synthesis", 'Append the synthesis to the review doc and leave the "Cold report (verbatim)" record intact', (
         "the bar on rewriting the verbatim record")),
        ("Synthesis", "Journal each cold finding per the review_finding journal step", (
         "the journal landing")),
        ("Synthesis", "Give each open question in a first-round or re-check cold report a row", (
         "the open-question rows")),
        ("Remediation", "Never use the cold reviewer as a fixer and never reassign it", "the never-a-fixer rule"),
        ("Remediation", "route its findings to warm fixers", "where its findings go"),
        ("Remediation", "Send it no message other than a HALT or the re-check message", (
         "the bar on any other message")),
        ("Re-check", "when the record holds no valid first-round cold report, dispatch the cold prompt as a first-round review", (
         "the first-round fallback in a re-review")),
        ("Re-check", "when that review is valid and the record holds earlier findings with no disposition, resume its subagent once in the same round with the re-check message listing them, and that re-check's report is the round's end state", (
         "the same-round re-check of earlier findings after the fallback")),
        ("Re-check", "if that re-check is not valid or not possible, the valid first-round report is the round's end state instead, with no fresh dispatch, and the earlier findings keep no disposition until the next round's re-check", (
         "the same-round re-check's fallback — the valid first round stands, no retry")),
        ("Re-check", "Otherwise send the re-check message listing every cold finding in the record with no disposition, whichever report raised it, even when the list is empty", (
         "the re-check list and the no-skip rule")),
        ("Re-check", "The re-check message is the paragraph below followed by the listed findings", (
         "the message composition — checklist plus findings")),
        ("Re-check", "copied verbatim from the record, never from synthesized rows", (
         "the findings' source")),
        ("Re-check", "Do not describe the fixes", "the bar on narrating the fixes"),
        ("Failed cycle", "Cold findings count under the **Failed cycle** test exactly like any reviewer's", (
         "the link to the cycle test")),
    ])
    def test_step_clause_present(self, step, token, lost):
        assert token in _step(step), f"the {step!r} step lost {lost} ({token!r})"

    @pytest.mark.parametrize("token, lost", [
        ("SendMessage to the agentId of the cold subagent that returned the most recent valid report", (
         "the resume target")),
        ("with the re-check message as the whole message", "the bar on adding anything to the resume"),
        ("When the record holds a lead line of corrected placeholder values appended after that report, send it instead as a fresh cold dispatch: the cold prompt with the corrected placeholders, followed by the re-check message", (
         "the fresh re-check after imPACT corrections")),
    ])
    def test_resume_paragraph_present(self, token, lost):
        region = _strip_ticks(_paragraph("Send the re-check message by resume"))
        assert token in region, f"the resume paragraph lost {lost} ({token!r})"

    @pytest.mark.parametrize("token, lost", [
        ("RE-REVIEW CHECKLIST:", "the checklist's literal lead-in"),
        ("This replaces REPORT for this review", "the re-check report shape"),
        ("git diff {base}...{head}", "the diff the re-check runs, in defined placeholders"),
        ("For each new issue, give its severity (blocking, suggestion or nitpick), file:line and a concrete failure scenario", (
         "the new-issue fields the Failed cycle test needs")),
    ])
    def test_re_check_message_present(self, token, lost):
        assert token in _checklist_quote(), f"the re-check message lost {lost} ({token!r})"

    @pytest.mark.parametrize("token, lost", [
        ("policy files", "the warm lanes' reading of project policy"),
        ("the architect lane owns project-policy conformance", "the named owner of policy conformance"),
        ("against itself", "the cold lane's frame — the code's own claims"),
    ])
    def test_division_token_present(self, token, lost):
        region = _paragraph("Division of labour")
        assert token in region, f"the division-of-labour sentence lost {lost} ({token!r})"


class TestCopyPasteGuard:
    """No warm dispatch-machinery token occurs in the section. The task-tool
    names are allowed inside CHANNELS, which bars them, and nowhere else."""

    @pytest.mark.parametrize("token, origin", [
        ("pact-{reviewer-type}", "the warm subagent_type placeholder"),
        ("{reviewer-name}", "the warm name/prompt placeholder"),
        ("{reviewer-type}", "the warm reviewer-type placeholder"),
        ("{feature}", "the warm task-subject placeholder"),
        ('team_name="{team_name}"', "the warm Agent call's team binding"),
        ("YOUR PACT ROLE", "the warm spawn literal's role prelude"),
        ("PACT:pact-team-registration", "the register first-action the warm literal mandates"),
        ("--type variety_assessed", "the per-reviewer journal write the cold lane skips"),
        ("{lead_address}", "the warm prompt's lead-routing line"),
        ("teachback", "the warm gate a paraphrased warm brief still names"),
        ("-plan.md", "a handed plan path"),
    ])
    def test_warm_token_absent_from_cold_section(self, token, origin):
        assert token not in _cold_section(), (
            f"the cold lane section contains {token!r} ({origin}) — the "
            f"copy-paste signature of a warm dispatch, which hands the "
            f"lane exactly the narrative it exists to avoid"
        )

    @pytest.mark.parametrize("token", ["TaskCreate", "TaskUpdate", "TaskList", "TaskGet"])
    def test_task_tool_named_only_in_channels(self, token):
        section = _cold_section()
        outside = section.replace(_clause("CHANNELS"), "")
        assert token not in outside, (
            f"the cold lane section names {token!r} outside the CHANNELS "
            f"clause — inside CHANNELS it is barred, anywhere else it is the "
            f"warm task machinery"
        )


class TestLanePlacement:
    """The lane's satellite surfaces in peer-review.md."""

    def test_cold_reviewer_in_always_included_list(self):
        assert "- **Cold reviewer**" in _standard_combination_block(), (
            "the cold reviewer is not in the always-included reviewer "
            "combination — the design decision is ALWAYS, not conditional"
        )

    def test_example_tree_labels_it_subagent(self):
        lines = [ln for ln in _peer_review_text().splitlines() if '"cold-reviewer' in ln and "──" in ln]
        assert len(lines) == 1, "the example tree lost its cold-reviewer row"
        assert "[Subagent]" in lines[0] and "not a task" in lines[0], (
            f"the example tree's cold row no longer reads as a [Subagent] "
            f"that is not a task: {lines[0]!r}"
        )

    def test_reuse_table_routes_cold_findings_away(self):
        row = _table_row(_peer_review_text(), "Cold reviewer", "peer-review.md Reuse table")
        assert "Never a fixer" in row, (
            f"the Reuse table's cold row no longer says the cold reviewer is "
            f"never a fixer: {row!r}"
        )

    @pytest.mark.parametrize("token, lost", [
        ("general-purpose", "the type carrying no PACT agent-memory store"),
        ("type comparison by construction", ("the scoped claim — only the type "
         "comparison is satisfied structurally")),
        ("no PACT agent-memory store", "the mechanism behind the claim"),
        ("closed by the cold prompt's DISREGARD instruction, not by construction", (
         "the honest bound — the memory index is closed by instruction")),
    ])
    def test_clause_satisfaction_paragraph(self, token, lost):
        region = _region(
            _peer_review_text(), "The cold reviewer lane satisfies",
            "the REVIEWER-MEMORY-CHANNEL clause",
        )
        assert token in region, f"the clause-satisfaction paragraph lost {lost} ({token!r})"


class TestOutsideSectionRules:
    """Rules elsewhere in the two files that scope warm-lane machinery away
    from the cold lane, or add the cold report to synthesis. Each phrase is
    specific enough to occur once, so a file-wide match is still site-bound;
    the uniqueness is asserted, not assumed."""

    @pytest.mark.parametrize("which, phrase, lost", [
        ("pr", "Each warm reviewer dispatch creates **two tasks**", (
         "the warm scoping of the two-task dispatch")),
        ("pr", "Spawn all warm reviewers in parallel (multiple `Agent` calls in one response); the cold reviewer's spawn joins that response per the Cold Reviewer Lane", (
         "the warm spawn line deferring the same-block rule to the lane")),
        ("pr", "Each warm reviewer should state", "the warm scoping of the intent statement"),
        ("pr", "takes the place of a teachback", "what stands in for the cold lane's teachback"),
        ("pr", "When a warm reviewer's findings need fixing", (
         "the warm scoping of reviewer-to-fixer reuse")),
        ("pr", "Reuse the warm reviewer who found the issues; the cold reviewer re-checks its own findings", (
         "the Verify-Only re-review routing")),
        ("pr", "and per open question in each first-round or re-check cold report,", (
         "the synthesis row for each cold open question")),
        ("pr", "every cold open question carries a disposition", (
         "the merge gate on cold open questions — without it the gate "
         "passes vacuously for the lane")),
        ("pr", 'AND the cold lane\'s round ended in a valid cold report, or the statement carries the line "Cold lane did not run."', (
         "the merge gate on the cold lane's outcome — without it a "
         "did-not-happen lane merges silently")),
        ("pr", "AND no blocking cold finding in the record lacks a disposition; when only such findings hold it, use the per-finding question in **Synthesis** of the Cold Reviewer Lane)", (
         "the merge gate on undisposed blocking cold findings — scoped to "
         "blocking so a declined minor cannot hold the merge")),
        ("pr", "| **Scope** | Warm: only files changed during remediation; cold: the whole PR, per the Cold Reviewer Lane |", (
         "the Verify-Only scope split — the cold re-check reviews the whole PR")),
        ("pr", "Name each verified-invalid blocking cold finding, with its evidence, in that statement", (
         "the merge-readiness statement's invalidation list — the user sees each closure before merging")),
        ("pr", "Handle recommendations by severity (a status row with no severity from the Cold Reviewer Lane is not routed)", (
         "step 4's skip of status rows")),
        ("pr", "This summary is the decline route for each excluded cold minor or future finding (see **Synthesis** in the Cold Reviewer Lane)", (
         "the greedy-fix decline pointer")),
        ("pr", "this is the Step A decline of the cold minor and future findings (see **Synthesis** in the Cold Reviewer Lane)", (
         "the Step A decline pointer")),
        ("pr", "Warm verify-only reviewers see a narrow remediation diff", (
         "the new-issue verification note scoped to warm reviewers")),
        ("pr", "project-policy conformance of the changed files", (
         "the architect lane's policy-conformance mission")),
        ("pr", "Warm reviewer agent tasks (architect, test-engineer, domain specialists); the cold reviewer gets no task", (
         "the overview's task-creation step scoped to warm reviewers")),
        ("pr", "Review task addBlockedBy = [warm reviewer IDs]", "the overview's blocking step"),
        ("pr", "Monitor until the warm reviewers complete and the cold lane reaches its round's end state (see **Synthesis** in the Cold Reviewer Lane)", (
         "the overview's wait on the cold report — the parent task's "
         "blockers cannot hold it")),
        ("pr", 'Warm reviewer tasks status = "completed"', "the overview's completion step"),
        ("pr", 'Warm reviewer tasks status = "in_progress"', "the overview's start step"),
        ("pr", "Dispatch the warm reviewers and the cold reviewer in parallel", (
         "the overview's dispatch step naming both lanes")),
        ("pr", "Dispatch **at least 3 warm reviewers in parallel**, plus the cold reviewer", (
         "the reviewer count scoped to warm reviewers")),
        ("pr", '"reviewers": ["{reviewer1}", "{reviewer2}", "cold-reviewer"]', (
         "the review_dispatch template's cold entry")),
        ("pr", "For a cold reviewer finding, set `reviewer` to `cold-reviewer`", (
         "the journal step's cold line")),
        ("pr", "omit `task_id` from both the write and the read-back match", (
         "the journal step's task_id rule for a cold finding")),
        ("persona", ", and project-policy conformance of the changed files", (
         "the persona's architect mission")),
        ("persona", "Invoke **at least 3 warm reviewers in parallel**, plus the cold reviewer", (
         "the persona's reviewer count scoped to warm reviewers")),
        ("persona", "After the warm reviews complete and the cold lane reaches its round's end state (see the Cold Reviewer Lane in the `peer-review` command)", (
         "the persona's wait on the cold report")),
        ("persona", "Each message lands at the teammate's next idle boundary; this timing is for teammates, not the peer-review cold reviewer", (
         "the fan-out's idle-boundary timing scoped away from the cold reviewer")),
        ("persona", "teammate `SendMessage` communication", (
         "the NEVER rule's scope — teammate messaging, not every SendMessage")),
        ("persona", "that the lead still reaches by `SendMessage` to its agentId", (
         "the persona's resume route")),
        ("persona", "A running peer-review cold reviewer owns no task, so this loop does not reach it: HALT it as the Cold Reviewer Lane in the `peer-review` command says", (
         "the HALT route for a reviewer with no task")),
        ("persona", "The peer-review cold reviewer is the exception: its prompt is sent verbatim and already says how it reports a blocker", (
         "the blocker-protocol exception")),
        ("persona", "The peer-review cold reviewer's prompt is fixed by its command and takes none of it", (
         "the prompting-structure exception")),
    ])
    def test_outside_phrase_present_once(self, which, phrase, lost):
        text = _peer_review_text() if which == "pr" else _persona_text()
        count = text.count(phrase)
        assert count == 1, (
            f"{'peer-review.md' if which == 'pr' else 'the persona'} should carry "
            f"{lost} exactly once ({phrase!r}), found {count}"
        )


class TestPlaceholderDefinitions:
    """The two re-review-sensitive placeholders say exactly which ref to
    use, so the lead never invents one."""

    @pytest.mark.parametrize("name, token, lost", [
        ("{base}", "freshly fetched remote-tracking ref", (
         "the ref kind — a stale local base widens the triple-dot diff")),
        ("{base}", "or the commit of the freshly fetched base tip where no such ref exists", (
         "the base where no remote-tracking ref exists")),
        ("{base}", "Use it in every round, so the diff is always the whole PR", (
         "the fixed base — every round reviews the whole PR")),
        ("{head}", "the head commit of the PR.", "the head value"),
    ])
    def test_placeholder_definition(self, name, token, lost):
        lines = [ln for ln in _placeholder_list().splitlines() if ln.startswith(f"- `{name}`")]
        assert len(lines) == 1, f"the placeholder list lost its {name} line"
        assert token in lines[0], f"the {name} definition lost {lost} ({token!r})"


class TestClassGuards:
    """Mechanical guards for three defect classes, so a new member cannot
    join silently.

    (a) Every placeholder used anywhere in the section — the prompt, the
    routes and the re-review checklist — is defined in the placeholder
    list, and no angle-bracket slot (<old-head> style) stands in for one.
    CANNOT CATCH: a value the lead must supply that is written as plain
    words ('the old commit') instead of a slot.

    (b) Each validity list carries every element its report contract
    requires. The element keys are an explicit map, asserted to occur in
    the contract itself, and the contract's element count is asserted, so
    adding an element to REPORT fails here until the map and both lists
    follow. The count splits REPORT on ';', and that premise is itself
    asserted: each mapped element must sit in its own ';' segment. Each
    validity list must also carry no negator (without, except, optional,
    not, unless, may omit) between its marker and its consequence. The
    first-round list must also require the diff-claims opening, which sits
    outside REPORT's ';' list; the re-check's per-finding elements are bound
    with their quantifier ('every listed finding', 'each new issue').
    CANNOT CATCH: an element the lists name with a noun the contract does
    not use; an element added to REPORT inside an existing ';' segment; a
    negation phrased without those words or placed outside the list span;
    an element added to the re-check checklist (no count is kept there).

    (c) Every paragraph in either file that names reviewers alongside the
    warm machinery (TaskCreate, TaskUpdate, TaskList, TaskGet, Task A,
    Task B, team_name, HANDOFF, open_questions, teachback, task_id, reuse)
    says warm or cold in its own words, or sits in the inventory
    below, whose entries are scoped by a qualified sentence in their
    container. The inventory must match exactly, so a new unqualified
    paragraph fails, and so does an entry that stopped matching. CANNOT
    CATCH: whether a qualified paragraph is right about the cold lane — the
    census detects a candidate for a human to classify, it does not judge
    one; nor a warm-only rule worded without the trigger words.
    """

    def test_every_placeholder_in_the_section_is_defined(self):
        section = _cold_section().replace(_placeholder_list(), "")
        used = set(PLACEHOLDER.findall(section))
        defined = set(PLACEHOLDER.findall(_placeholder_list()))
        assert used <= defined, (
            f"the cold lane uses placeholders the lead is never told how to "
            f"fill: {sorted(used - defined)!r}"
        )

    def test_no_angle_bracket_slots(self):
        slots = ANGLE_SLOT.findall(_cold_section())
        assert not slots, (
            f"the cold lane uses angle-bracket slots outside the placeholder "
            f"list, which the lead fills by inference: {slots!r}"
        )

    def test_report_element_count(self):
        tail = _clause("REPORT").split("Then give ", 1)
        assert len(tail) == 2, "the REPORT clause lost its 'Then give' element list"
        elements = [e for e in tail[1].split(";") if e.strip()]
        assert len(elements) == len(REPORT_ELEMENTS_KEYS) + 1, (
            f"the REPORT clause now lists {len(elements)} elements; the "
            f"validity map holds the findings plus {len(REPORT_ELEMENTS_KEYS)}. "
            f"Add the new element to REPORT_ELEMENTS_KEYS and to both validity "
            f"lists"
        )

    def test_report_elements_are_semicolon_separated(self):
        """The element count above splits on ';'. This holds that premise:
        each mapped element sits in its own ';' segment, so merging two
        elements into one comma list, or dropping the ';' between them,
        fails here instead of silently changing the count."""
        segments = _clause("REPORT").split("Then give ", 1)[-1].split(";")
        homes = {key: [i for i, s in enumerate(segments) if key in s] for key in REPORT_ELEMENTS_KEYS}
        assert all(len(h) == 1 for h in homes.values()), (
            f"a REPORT element is not in exactly one ';' segment: {homes!r}"
        )
        assert len({h[0] for h in homes.values()}) == len(REPORT_ELEMENTS_KEYS), (
            f"two REPORT elements share one ';' segment: {homes!r} — the "
            f"element count can no longer see an added element"
        )

    @pytest.mark.parametrize("marker", [FIRST_ROUND_VALIDITY, RECHECK_VALIDITY])
    def test_validity_list_ends_not_valid(self, marker):
        region = _paragraph(marker)
        span = _validity_sentence(marker)
        tail = region[region.find(span) + len(span):]
        assert tail.startswith(VALIDITY_CONSEQUENCE), (
            f"the list after {marker!r} no longer ends {VALIDITY_CONSEQUENCE!r} "
            f"— each list must carry its own consequence, or a flipped one "
            f"hides behind the other list's"
        )

    @pytest.mark.parametrize("marker", [FIRST_ROUND_VALIDITY, RECHECK_VALIDITY])
    def test_validity_list_has_no_negator(self, marker):
        span = _validity_sentence(marker)[len(marker):]
        hits = VALIDITY_NEGATOR.findall(span)
        assert not hits, (
            f"the validity list after {marker!r} carries {hits!r} — an element "
            f"named as optional or excepted still matches its key"
        )

    @pytest.mark.parametrize("key", REPORT_ELEMENTS_KEYS)
    def test_report_names_each_element(self, key):
        assert key in _clause("REPORT"), (
            f"the REPORT clause no longer names {key!r} — the validity map "
            f"is anchored on REPORT's own nouns"
        )

    def test_report_opening_required_in_first_round(self):
        report_phrase, validity_phrase = DIFF_CLAIMS_OPENING
        assert report_phrase in _clause("REPORT"), (
            "the REPORT clause lost its diff-claims opening"
        )
        assert validity_phrase in _validity_sentence(FIRST_ROUND_VALIDITY), (
            "the first-round validity list no longer requires the "
            "diff-claims opening — a report without it reads as valid"
        )

    @pytest.mark.parametrize("checklist_phrase, validity_phrase", RECHECK_QUANTIFIED)
    def test_recheck_quantified_element(self, checklist_phrase, validity_phrase):
        assert checklist_phrase in _checklist_quote(), (
            f"the re-check message lost {checklist_phrase!r} with its quantifier"
        )
        assert validity_phrase in _validity_sentence(RECHECK_VALIDITY), (
            f"the re-check validity list lost {validity_phrase!r} with its quantifier"
        )

    @pytest.mark.parametrize("key", REPORT_ELEMENTS_KEYS)
    def test_first_round_validity_names_each_element(self, key):
        assert key in _validity_sentence(FIRST_ROUND_VALIDITY), (
            f"the first-round validity list no longer requires {key!r} — a "
            f"report omitting it reads as clean"
        )

    @pytest.mark.parametrize("key", RECHECK_ELEMENTS)
    def test_checklist_names_each_element(self, key):
        assert key in _checklist_quote(), (
            f"the re-review checklist no longer asks for {key!r}"
        )

    @pytest.mark.parametrize("key", RECHECK_ELEMENTS)
    def test_recheck_validity_names_each_element(self, key):
        assert key in _validity_sentence(RECHECK_VALIDITY), (
            f"the re-check validity list no longer requires {key!r} — a "
            f"re-check omitting it counts as a review"
        )

    def test_warm_machinery_paragraphs_are_qualified_or_inventoried(self):
        found = _unqualified_reviewer_paragraphs()
        new = sorted(set(found) - set(WARM_SCOPED_INVENTORY))
        stale = sorted(set(WARM_SCOPED_INVENTORY) - set(found))
        assert not new, (
            f"paragraphs name reviewers with warm machinery but say neither "
            f"warm nor cold: {new!r}. Qualify each, or add it to "
            f"WARM_SCOPED_INVENTORY with the container sentence that scopes it"
        )
        assert not stale, (
            f"WARM_SCOPED_INVENTORY entries no longer match an unqualified "
            f"paragraph: {stale!r}. Remove them"
        )

    @pytest.mark.parametrize("anchor", sorted(WARM_SCOPED_INVENTORY))
    def test_inventory_scope_sentence_present(self, anchor):
        which, scope = WARM_SCOPED_INVENTORY[anchor]
        text = _peer_review_text() if which == "pr" else _persona_text()
        assert scope in text, (
            f"the inventoried paragraph {anchor!r} relied on {scope!r} to "
            f"scope it, and that sentence is gone"
        )


class TestPersonaSites:
    """The persona's blanket sentences (specialists are MANDATORILY
    teammates; NEVER a plain subagent spawn) contradict the cold lane
    unless the carve-out sits beside them, and nothing else reddens on the
    contradiction. Each site is bound to its own region."""

    @pytest.mark.parametrize("marker, site", [
        (PERSONA_CHECKPOINT_MARKER, "the Agent-Teams checkpoint"),
        (PERSONA_NEVER_MARKER, "the dispatch-mandatory block"),
    ])
    def test_site_names_the_exception(self, marker, site):
        region = _region(_persona_text(), marker, "the persona")
        assert PERSONA_EXCEPTION in region and PERSONA_NOT_SPECIALIST in region, (
            f"{site} no longer says {PERSONA_EXCEPTION!r} is "
            f"{PERSONA_NOT_SPECIALIST!r} — the blanket teammates-only rule "
            f"reads as unqualified where a lead would 'correct' the cold spawn"
        )

    def test_persona_reuse_row_routes_cold_findings_away(self):
        row = _table_row(_persona_text(), "Cold reviewer", "the persona Reuse table")
        assert "Never a fixer and never reassigned" in row, (
            f"the persona's Reuse row no longer says the cold reviewer is "
            f"never a fixer: {row!r}"
        )

    def test_persona_handoff_rule_excepts_the_lane(self):
        assert "Every agent except the peer-review cold reviewer delivers a structured HANDOFF" in _persona_text(), (
            "the persona's HANDOFF rule no longer excepts the cold reviewer "
            "— a lead following it waits for a HANDOFF the lane never writes"
        )

    def test_persona_reviewer_list_names_the_lane(self):
        lines = [ln for ln in _persona_text().splitlines() if ln.startswith("- **Cold reviewer**")]
        assert len(lines) == 1, (
            "the persona's reviewer list no longer names the cold reviewer "
            "lane — the dispatch enumeration and the command must agree"
        )
        assert "always" in lines[0], (
            f"the persona's cold reviewer bullet no longer says always: {lines[0]!r}"
        )
