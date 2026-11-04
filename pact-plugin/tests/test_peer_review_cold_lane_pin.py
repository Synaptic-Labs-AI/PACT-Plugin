"""Structural pins for the cold reviewer lane in commands/peer-review.md.

The cold lane is a prose-only feature: an anonymous `general-purpose`
subagent dispatched with an information diet instead of a narrative. Its
independence lives in WHAT the dispatch text says, so the pins are token
and short-phrase pins bound to the clause or paragraph that must carry
them — never file-wide, never whole sentences. Each region ends at the
next region of its kind, so a token living in a sibling clause cannot
satisfy an arm, and a rewording that keeps the load-bearing phrases
survives.

COVERED, and this list is the scope:
- the section anchor, its closing rule, and the warm-dispatch paragraph
  that follows it ('for each warm reviewer');
- the cold prompt: exactly one fenced block sent verbatim, its ordered
  clause labels, and every placeholder it uses defined in the placeholder
  list (and every defined placeholder used);
- each prompt clause's content, bound to its own label line: ARTIFACT's
  diff instruction and its bar on PR and commit-message surfaces; AVOID's
  named paths and stores; CHANNELS' messaging, task-tool and pact-memory
  prohibitions; DISREGARD's arrival surfaces, its operative verb, the
  advisory carve-out and the safety-rules exception; READ-ONLY's
  worktree bar, scratch-copy route, quoted-heredoc rule and guarded-command
  bar; OPERATIONAL FACTS' placeholder and use instruction, and the
  placeholder's own definition; QUESTION's phrase and replica clause;
  REPORT's diff-derived opening, severity enum, nothing-found lines,
  merge-block sentence, open questions and coverage boundary;
- the spawn sentence: one inline Agent(subagent_type="general-purpose",
  ...) reference carrying no name=, no team_name= and no role prelude,
  plus the prose that requires that shape (NO name=/team_name=/prelude,
  with every review, same parallel block, no teammate conversion, no
  tasks);
- the lead-facing paragraphs, each bound to its own paragraph: the skip
  sentence as one phrase, the report landing (review_dispatch string,
  review_finding reviewer, read-back omission), the HANDOFF-less record
  and open-question rows, the null rule (valid-only-with, the main
  review-did-not-happen clause, wait-for-the-report), the never-a-fixer
  and cold re-review rule, and the division of labour;
- the copy-paste guard: no warm dispatch-machinery token occurs in the
  section, with task-tool names allowed only inside CHANNELS;
- satellites: the always-included bullet, the example-tree [Subagent]
  label, the Reuse-table cold row, the warm-only completion check, and the
  clause-satisfaction paragraph after REVIEWER-MEMORY-CHANNEL;
- the persona: the exception at the Agent-Teams checkpoint and in the
  dispatch-mandatory block, the cold row of its Reuse table, and its
  reviewer-list bullet, each bound to its own region.

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
enumerated token passes the guard. The ceiling on these pins is prose
reworded into uselessness while every pinned phrase survives.
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
        ("barred", ("the operative verb — without it the surfaces read as a "
         "list, not a prohibition")),
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
    """The trial's own failure mode: a cold reviewer that cannot run the
    project's tests or lint because the access instructions lived in the
    documents it was told to avoid."""

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
        ("nothing-found line for each severity", ("the per-severity explicit "
         "no-findings lines — absent findings and an absent review look "
         "identical without them")),
        ("what would block a merge", ("the global merge judgement, required "
         "even when nothing would block")),
        ("open questions", ("the questions the diff could not settle — they "
         "get a disposition row like a HANDOFF's open_questions")),
        ("coverage boundary", ("the required boundary — a partial review "
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
        ("dispatched with every review", "the always-dispatched rule"),
        ("in the same parallel block as the warm reviewers", (
         "the parallel-block rule — dispatched after the warm reports, the "
         "lane inherits their findings as narrative")),
        ("Do not convert it to a teammate spawn", "the no-conversion rule"),
        ("do not create tasks for it", "the no-tasks rule"),
    ])
    def test_spawn_prose_present(self, token, lost):
        region = _strip_ticks(_paragraph("The cold reviewer is dispatched with"))
        assert token in region, (
            f"the cold spawn paragraph lost {lost} ({token!r})"
        )


class TestLeadFacingRules:
    """The lane's structural exceptions, each bound to its own paragraph:
    what the cold dispatch skips, where its report lands, how a null
    report is read, and who fixes its findings."""

    def test_skip_sentence_is_one_phrase(self):
        region = _paragraph("This lane skips")
        phrase = ("This lane skips the Task A + Task B sequence, the variety "
                  "stamp, and the per-reviewer journal block")
        assert phrase in region, (
            "the skip sentence changed — it must keep all three skipped "
            "steps in one phrase, or the surrounding per-reviewer procedure "
            "re-absorbs the cold lane"
        )

    @pytest.mark.parametrize("token, lost", [
        ('reviewers array as the string "cold-reviewer"', (
         "the named review_dispatch string — without it the lead invents one")),
        ("review_finding event with reviewer cold-reviewer", (
         "the journal landing — a subagent's report otherwise vanishes at "
         "compaction")),
        ("omit it from the read-back match too", (
         "the read-back rule — a match that keeps task_id never finds the "
         "event")),
    ])
    def test_report_landing_present(self, token, lost):
        region = _strip_ticks(_paragraph("This lane skips"))
        assert token in region, f"the report-landing line lost {lost} ({token!r})"

    @pytest.mark.parametrize("token, lost", [
        ("writes no HANDOFF", "the statement that no HANDOFF exists"),
        ("the review doc is the durable home", "where the report is recorded"),
        ("open-questions dispositions", ("the disposition row for each cold "
         "open question — the merge gate keys on it")),
    ])
    def test_record_paragraph_present(self, token, lost):
        region = _paragraph("writes no HANDOFF")
        assert token in region, f"the cold record paragraph lost {lost} ({token!r})"

    @pytest.mark.parametrize("token, lost", [
        ("valid only with", ("the operative claim — the receipt list is a "
         "requirement, not advice")),
        ("synthesize the lane as review-did-not-happen, not as clean", (
         "the main null clause — an incomplete report reads as clean")),
        ("wait for the cold report before synthesizing", (
         "the wait rule — synthesis before the report lands drops the lane")),
        ("do not treat completion of the spawn as the review", (
         "the completion-is-not-review rule")),
    ])
    def test_null_rule_present(self, token, lost):
        region = _paragraph("valid only with")
        assert token in region, f"the null rule lost {lost} ({token!r})"

    @pytest.mark.parametrize("token, lost", [
        ("Never use the cold reviewer as a fixer", "the never-a-fixer rule"),
        ("route its findings to warm fixers", "where its findings go"),
        ("only to re-check its own findings", ("the re-review scope — the "
         "cold lane re-checks, it does not re-review fresh")),
    ])
    def test_re_review_rule_present(self, token, lost):
        region = _paragraph("Never use the cold reviewer as a fixer")
        assert token in region, f"the cold re-review rule lost {lost} ({token!r})"

    @pytest.mark.parametrize("route, token, lost", [
        ("- Resume:", "same cold subagent's agentId", "the resume target"),
        ("- Resume:", "and nothing else", ("the bar on adding narrative to the "
         "resume message")),
        ("- Fresh dispatch", "when it cannot be resumed", "the fallback condition"),
        ("- Fresh dispatch", "the only text a re-review dispatch adds to the verbatim prompt", (
         "the bound on what a fresh re-review dispatch may add")),
    ])
    def test_re_review_route_present(self, route, token, lost):
        lines = [ln for ln in _lead_prose().splitlines() if ln.startswith(route)]
        assert len(lines) == 1, f"the cold re-review lost its {route!r} route"
        assert token in _strip_ticks(lines[0]), (
            f"the {route!r} re-review route lost {lost} ({token!r})"
        )

    @pytest.mark.parametrize("token, lost", [
        ("RE-REVIEW CHECKLIST:", "the checklist's literal lead-in"),
        ("This replaces REPORT for this review", "the re-review report shape"),
        ("copied verbatim from the review doc", ("the findings' source — "
         "never a summary written by the lead")),
        ("Do not describe the fixes", ("the bar on narrating the fixes — a "
         "description is the author's account")),
        ("Failed cycle", "the link from open cold findings to the cycle test"),
    ])
    def test_re_review_checklist_present(self, token, lost):
        region = _paragraph("The re-review checklist is one paragraph")
        assert token in region, f"the re-review checklist lost {lost} ({token!r})"

    @pytest.mark.parametrize("token, lost", [
        ("policy files", "the warm lanes' reading of project policy"),
        ("the architect lane owns project-policy conformance", (
         "the named owner of policy conformance")),
        ("against itself", ("the cold lane's frame — the code's own claims, "
         "not the project's rules")),
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
    """Rules elsewhere in peer-review.md that scope warm-lane machinery away
    from the cold lane, or add the cold report to synthesis. Each phrase is
    specific enough to occur once, so a file-wide match is still site-bound;
    the uniqueness is asserted, not assumed."""

    @pytest.mark.parametrize("phrase, lost", [
        ("Each warm reviewer dispatch creates **two tasks**", (
         "the warm scoping of the two-task dispatch")),
        ("Spawn all warm reviewers and the cold reviewer in parallel", (
         "the single parallel spawn block")),
        ("Each warm reviewer should state", "the warm scoping of the intent statement"),
        ("takes the place of a teachback", "what stands in for the cold lane's teachback"),
        ("When a warm reviewer's findings need fixing", (
         "the warm scoping of reviewer-to-fixer reuse")),
        ("Reuse the warm reviewer who found the issues; the cold reviewer re-checks its own findings", (
         "the Verify-Only re-review routing")),
        ("and per open question in the cold report", (
         "the synthesis row for each cold open question")),
        ("every cold-report open question carries a disposition", (
         "the merge gate on cold open questions — without it the gate "
         "passes vacuously for the lane")),
        ("project-policy conformance of the changed files", (
         "the architect lane's policy-conformance mission")),
    ])
    def test_outside_phrase_present_once(self, phrase, lost):
        count = _peer_review_text().count(phrase)
        assert count == 1, (
            f"peer-review.md should carry {lost} exactly once ({phrase!r}), "
            f"found {count}"
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
        assert "Never a fixer" in row, (
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
