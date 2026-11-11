"""
Tests for skills/ directory structural validation.

Tests cover:
1. All expected skill directories exist
2. Each skill has a SKILL.md file
3. SKILL.md has valid YAML frontmatter with required fields
4. Skill names and descriptions are present
5. SKILL.md body contains substantive content
"""
import ast
import subprocess
from pathlib import Path

import pytest

from fixtures.tracked_files import tracked_files as _shared_tracked_files
from helpers import parse_frontmatter

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PLUGIN_ROOT / "skills"
REPO_ROOT = PLUGIN_ROOT.parent


def _tracked_files(repo_root, relative_dir, pattern=None):
    """Keep deleted index entries visible to the structural skill-directory oracle.

    Content consumers use ``_collection_population`` (keep_deleted false) so a
    deleted SKILL.md is reported once by name, not raised through every read.
    """
    return _shared_tracked_files(
        repo_root, relative_dir, pattern, keep_deleted=True
    )


def _collection_population(relative_dir, pattern):
    """A collection-time population, derived from the plugin directory's own name.

    A CONTENT consumer: deleted index entries are dropped (``keep_deleted``
    false), because collection-time readers open each file. Keeping a deleted
    entry here turns a local deletion into FileNotFoundError from every
    parametrized case instead of the named structural failure.

    The name is read rather than hardcoded because an installed plugin cache
    places the plugin under a version directory (``.../PACT/4.8.34``), where a
    literal ``pact-plugin/`` prefix matches nothing. A slim install that omits
    the artifact skips this module by name instead of aborting collection; a
    dev checkout whose population is empty still fails loudly.
    """
    try:
        return _shared_tracked_files(REPO_ROOT, relative_dir, pattern)
    except AssertionError:
        if PLUGIN_ROOT.name == "pact-plugin":
            raise
        pytest.skip(
            f"installed layout {PLUGIN_ROOT} ships no {relative_dir!r}; "
            "the structural claims are about the dev checkout",
            allow_module_level=True,
        )


def _tracked_skill_dirs(plugin_root):
    """Return every tracked top-level skill directory, even without SKILL.md."""
    plugin_root = Path(plugin_root)
    files = _tracked_files(plugin_root.parent, f"{plugin_root.name}/skills")
    skills_dir = plugin_root / "skills"
    known = skills_dir / "pact-team-registration" / "SKILL.md"
    assert known in files
    directories = set()
    for path in files:
        relative = path.relative_to(skills_dir)
        if len(relative.parts) > 1:
            directories.add(skills_dir / relative.parts[0])
    return sorted(directories)


EXPECTED_SKILLS = {
    "pact-agent-teams",
    "pact-architecture-patterns",
    "pact-teachback",
    "pact-coding-standards",
    "pact-handoff-harvest",
    "pact-memory",
    "pact-prepare-research",
    "pact-security-patterns",
    "pact-team-registration",
    "pact-testing-strategies",
    "request-more-context",
    "worktree-cleanup",
    "worktree-setup",
}

# n8n skills are optional but expected
N8N_SKILLS = {
    "n8n-code-javascript",
    "n8n-code-python",
    "n8n-expression-syntax",
    "n8n-mcp-tools-expert",
    "n8n-node-configuration",
    "n8n-validation-expert",
    "n8n-workflow-patterns",
}


@pytest.fixture
def skill_dirs():
    """Get every tracked skill directory, including incomplete skills."""
    return _tracked_skill_dirs(PLUGIN_ROOT)


class TestSkillDirectoriesExist:
    def test_skills_directory_exists(self):
        assert SKILLS_DIR.is_dir()

    def test_core_skills_present(self, skill_dirs):
        names = {d.name for d in skill_dirs}
        for expected in EXPECTED_SKILLS:
            assert expected in names, f"Missing skill: {expected}"


class TestSkillMdFiles:
    def test_each_skill_has_skill_md(self, skill_dirs):
        for d in skill_dirs:
            skill_md = d / "SKILL.md"
            assert skill_md.is_file(), f"{d.name}/ missing SKILL.md"

    def test_skill_md_has_frontmatter(self, skill_dirs):
        for d in skill_dirs:
            skill_md = d / "SKILL.md"
            if not skill_md.is_file():
                continue
            text = skill_md.read_text(encoding="utf-8")
            assert text.startswith("---"), f"{d.name}/SKILL.md missing frontmatter"

    def test_frontmatter_has_name(self, skill_dirs):
        for d in skill_dirs:
            skill_md = d / "SKILL.md"
            if not skill_md.is_file():
                continue
            text = skill_md.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)
            if fm is None:
                continue
            assert "name" in fm, f"{d.name}/SKILL.md missing name"

    def test_frontmatter_has_description(self, skill_dirs):
        for d in skill_dirs:
            skill_md = d / "SKILL.md"
            if not skill_md.is_file():
                continue
            text = skill_md.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)
            if fm is None:
                continue
            assert "description" in fm, f"{d.name}/SKILL.md missing description"

    def test_skill_md_has_substantive_body(self, skill_dirs):
        for d in skill_dirs:
            skill_md = d / "SKILL.md"
            if not skill_md.is_file():
                continue
            text = skill_md.read_text(encoding="utf-8")
            _, _, body = text.partition("---")
            _, _, body = body.partition("---")
            assert len(body.strip()) > 100, f"{d.name}/SKILL.md body too short"


class TestPreResponseChannelCheckGate:
    """Both pact-agent-teams (teammate-side) and pact-orchestrator agent body
    (lead-side) carry the Pre-Response Channel Check gate (issue family:
    instruction-surface-leak).

    Under v4.0.0 the lead-side gate is in agents/pact-orchestrator.md
    (the orchestrator persona delivered via --agent flag); the teammate-side
    gate is in pact-agent-teams/SKILL.md.
    """

    AGENT_TEAMS_PATH = SKILLS_DIR / "pact-agent-teams" / "SKILL.md"
    ORCHESTRATION_PATH = (
        SKILLS_DIR.parent / "agents" / "pact-orchestrator.md"
    )

    INVARIANT_HEADER = "Pre-Response Channel Check"
    INVARIANT_USER_ADDRESSEE = "Addressee is **user**"
    INVARIANT_AGENT_ADDRESSEE = "Addressee is **team-lead or teammate**"
    INVARIANT_BOTH_ADDRESSEE = "Addressee is **both**"
    INVARIANT_FORMAT_CUE = "Format-cue hijack"
    INVARIANT_CANDOR = "Candor-question"
    INVARIANT_SENDMESSAGE_REQUIRED = "SendMessage is REQUIRED"
    INVARIANT_PLAIN_TEXT_INVISIBLE = "Plain text is invisible to other agents"
    INVARIANT_CHOOSE_BOTH_FALLBACK = "If you are unsure who the addressee is, choose **both**"
    # Teammate-side (pact-agent-teams/SKILL.md) is two levels deep; lead-side
    # (agents/pact-orchestrator.md) is one level deep — pin separate link
    # paths per surface.
    TEAMMATE_CHARTER_LINK = "../../protocols/pact-communication-charter.md#pre-send-self-check"
    LEAD_CHARTER_LINK = "../protocols/pact-communication-charter.md"
    LEAD_GRAY_AREA_PHRASE = "Lead-side gray-area trap"
    TEAMMATE_GRAY_AREA_PHRASE = "Teammate-side gray-area trap"

    @staticmethod
    def _gate_text(path):
        """Read with backticks stripped: gate phrases pin WORDS, not tool-
        name rendering (instruction-prose backtick convention)."""
        return path.read_text(encoding="utf-8").replace("`", "")

    def test_teammate_side_has_gate(self):
        text = self._gate_text(self.AGENT_TEAMS_PATH)
        for phrase in (
            self.INVARIANT_HEADER,
            self.INVARIANT_USER_ADDRESSEE,
            self.INVARIANT_AGENT_ADDRESSEE,
            self.INVARIANT_BOTH_ADDRESSEE,
            self.INVARIANT_FORMAT_CUE,
            self.INVARIANT_CANDOR,
            self.INVARIANT_SENDMESSAGE_REQUIRED,
            self.INVARIANT_PLAIN_TEXT_INVISIBLE,
            self.INVARIANT_CHOOSE_BOTH_FALLBACK,
            self.TEAMMATE_CHARTER_LINK,
        ):
            assert phrase in text, f"pact-agent-teams missing gate phrase: {phrase!r}"

    def test_lead_side_has_gate(self):
        text = self._gate_text(self.ORCHESTRATION_PATH)
        for phrase in (
            self.INVARIANT_HEADER,
            self.INVARIANT_USER_ADDRESSEE,
            self.INVARIANT_AGENT_ADDRESSEE,
            self.INVARIANT_BOTH_ADDRESSEE,
            self.INVARIANT_FORMAT_CUE,
            self.INVARIANT_CANDOR,
            self.INVARIANT_SENDMESSAGE_REQUIRED,
            self.INVARIANT_PLAIN_TEXT_INVISIBLE,
            self.INVARIANT_CHOOSE_BOTH_FALLBACK,
            self.LEAD_CHARTER_LINK,
        ):
            assert phrase in text, f"orchestrator agent body missing gate phrase: {phrase!r}"

    def test_lead_side_has_gray_area_addendum(self):
        text = self.ORCHESTRATION_PATH.read_text(encoding="utf-8")
        assert self.LEAD_GRAY_AREA_PHRASE in text, (
            "orchestration must include the lead-side gray-area trap addendum"
        )

    def test_teammate_side_does_not_have_lead_addendum(self):
        text = self.AGENT_TEAMS_PATH.read_text(encoding="utf-8")
        assert self.LEAD_GRAY_AREA_PHRASE not in text, (
            "lead-side gray-area trap belongs only in orchestration/SKILL.md"
        )

    def test_teammate_side_has_gray_area_addendum(self):
        text = self.AGENT_TEAMS_PATH.read_text(encoding="utf-8")
        assert self.TEAMMATE_GRAY_AREA_PHRASE in text, (
            "pact-agent-teams must include the teammate-side gray-area trap addendum"
        )

    def test_lead_side_does_not_have_teammate_addendum(self):
        text = self.ORCHESTRATION_PATH.read_text(encoding="utf-8")
        assert self.TEAMMATE_GRAY_AREA_PHRASE not in text, (
            "teammate-side gray-area trap belongs only in pact-agent-teams/SKILL.md"
        )

    def test_teammate_gate_appears_before_on_start(self):
        teammate = self.AGENT_TEAMS_PATH.read_text(encoding="utf-8")
        assert teammate.index("## Pre-Response Channel Check") < teammate.index("## On Start"), \
            "pact-agent-teams: gate must appear before On Start"

    def test_lead_gate_appears_before_communication(self):
        lead = self.ORCHESTRATION_PATH.read_text(encoding="utf-8")
        # Architect's §1-§13 numbered hierarchy: Pre-Response Channel Check
        # is §1; Communication is §7 (post #628 §2 Session-Start Ritual
        # insertion + §2-§12 → §3-§13 renumber). The pre-response gate
        # must precede the Communication section so the agent reads the
        # channel-check rules before reading the SendMessage discipline.
        assert lead.index("## 1. Pre-Response Channel Check") < lead.index("## 7. Communication"), \
            "pact-orchestrator agent body: Pre-Response Channel Check (§1) must appear before Communication (§7)"


def _skill_file_population():
    """The tracked SKILL.md population the collection-time constants read."""
    return _collection_population(f"{PLUGIN_ROOT.name}/skills", "*/SKILL.md")


def _pact_agent_file_population():
    """The tracked pact-*.md agent population the constants read."""
    return _collection_population(f"{PLUGIN_ROOT.name}/agents", "pact-*.md")


_ALL_SKILL_FILES = _skill_file_population()


# =============================================================================
# F20 — pact-*.md agent files must declare pact-agent-teams in skills frontmatter
# =============================================================================

# Per architect §6 / §7(b)+(f), every spawned PACT teammate must preload the
# pact-agent-teams skill so the team-protocol body is in the agent's context
# at first turn. pact-orchestrator is delivered via `claude --agent` rather
# than spawned, so its frontmatter shape differs (no `skills:` block) and is
# carved out.
PACT_AGENTS_DIR = SKILLS_DIR.parent / "agents"
F20_CARVE_OUT_FILES = frozenset({"pact-orchestrator"})
_PACT_AGENT_FILES = _pact_agent_file_population()


def test_collection_time_populations_have_known_members():
    assert SKILLS_DIR / "pact-team-registration" / "SKILL.md" in _ALL_SKILL_FILES
    assert PACT_AGENTS_DIR / "pact-devops-engineer.md" in _PACT_AGENT_FILES


def test_collection_population_builders_exclude_untracked_files(
        tmp_path, monkeypatch):
    """The BUILDERS behind the module constants must read the git index.

    Reverting either builder to ``sorted(SKILLS_DIR.glob(...))`` keeps every
    helper arm green; this arm drives the builders and reddens, because a
    valid-shaped untracked file then joins the collection-time population.
    """
    repo = tmp_path / "repo"
    plugin = repo / "pact-plugin"
    skills = plugin / "skills"
    skill = skills / "pact-team-registration" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: pact-team-registration\n---\n", encoding="utf-8")
    agent = plugin / "agents" / "pact-devops-engineer.md"
    agent.parent.mkdir(parents=True)
    agent.write_text("---\nname: pact-devops-engineer\n---\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "--", "pact-plugin"], cwd=repo, check=True)
    probe_skill = skills / "000_untracked_probe" / "SKILL.md"
    probe_skill.parent.mkdir()
    probe_skill.write_text(skill.read_text(encoding="utf-8"), encoding="utf-8")
    probe_agent = plugin / "agents" / "pact-000-probe.md"
    probe_agent.write_text(agent.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setitem(globals(), "REPO_ROOT", repo)
    monkeypatch.setitem(globals(), "PLUGIN_ROOT", plugin)
    monkeypatch.setitem(globals(), "SKILLS_DIR", skills)
    monkeypatch.setitem(globals(), "PACT_AGENTS_DIR", plugin / "agents")

    skill_files = _skill_file_population()
    assert skill in skill_files
    assert probe_skill not in skill_files, (
        f"untracked {probe_skill} joined the collection-time skill population")

    agent_files = _pact_agent_file_population()
    assert agent in agent_files
    assert probe_agent not in agent_files, (
        f"untracked {probe_agent} joined the collection-time agent population")


def test_collection_constants_are_built_from_their_builders():
    """AST pin: the constants may not bypass the builders.

    A constant rebound to ``sorted(SKILLS_DIR.glob(...))`` is invisible to any
    runtime arm in a clean checkout, because the two populations are identical
    until an untracked file exists. This pin reads the module's own assignment
    nodes, so the assertion text cannot satisfy the pattern it searches for.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    assigned = {
        target.id: node.value.func.id
        for node in tree.body if isinstance(node, ast.Assign)
        for target in node.targets if isinstance(target, ast.Name)
        if isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
    }
    assert assigned.get("_ALL_SKILL_FILES") == "_skill_file_population"
    assert assigned.get("_PACT_AGENT_FILES") == "_pact_agent_file_population"


def test_deleted_tracked_skill_md_is_named_by_the_structural_oracle(
        tmp_path, monkeypatch):
    """Deletion: content populations drop the entry, the structural one names it.

    With ``keep_deleted=True`` on the content population, deleting one tracked
    SKILL.md makes every parametrized case raise FileNotFoundError. The
    structural oracle keeps the directory and reports the one named failure.
    """
    repo = tmp_path / "repo"
    plugin = repo / "pact-plugin"
    skills = plugin / "skills"
    known = skills / "pact-team-registration" / "SKILL.md"
    known.parent.mkdir(parents=True)
    known.write_text("---\nname: pact-team-registration\n---\n", encoding="utf-8")
    doomed = skills / "pact-teachback" / "SKILL.md"
    doomed.parent.mkdir()
    doomed.write_text("---\nname: pact-teachback\n---\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "--", "pact-plugin"], cwd=repo, check=True)
    doomed.unlink()  # the index entry stays; the disk entry is gone

    monkeypatch.setitem(globals(), "REPO_ROOT", repo)
    monkeypatch.setitem(globals(), "PLUGIN_ROOT", plugin)
    monkeypatch.setitem(globals(), "SKILLS_DIR", skills)

    content = _skill_file_population()
    assert doomed not in content, (
        "the deleted tracked SKILL.md stayed in the content population; every "
        "parametrized case would raise FileNotFoundError")

    directories = _tracked_skill_dirs(plugin)
    assert doomed.parent in directories, (
        "the structural population dropped a tracked skill directory whose "
        "SKILL.md is deleted; the named missing-SKILL.md failure would vanish")
    with pytest.raises(AssertionError, match=r"pact-teachback/ missing SKILL.md"):
        TestSkillMdFiles().test_each_skill_has_skill_md(directories)


class TestTrackedSkillPopulation:
    @staticmethod
    def _git(root, *args):
        subprocess.run(
            ["git", *args], cwd=root, capture_output=True, check=True
        )

    def test_untracked_is_invisible_and_tracked_deletion_stays(self, tmp_path):
        skills = tmp_path / "pact-plugin" / "skills"
        tracked = skills / "pact-team-registration" / "SKILL.md"
        tracked.parent.mkdir(parents=True)
        tracked.write_text("tracked\n", encoding="utf-8")
        self._git(tmp_path, "init", "-q")
        self._git(tmp_path, "add", "-A")
        untracked = skills / "000_untracked_probe" / "SKILL.md"
        untracked.parent.mkdir()
        untracked.write_text("untracked\n", encoding="utf-8")

        files = _tracked_files(tmp_path, "pact-plugin/skills", "*/SKILL.md")
        assert tracked in files
        assert untracked not in files

        tracked.unlink()
        assert tracked in _tracked_files(
            tmp_path, "pact-plugin/skills", "*/SKILL.md"
        )

    def test_tracked_incomplete_skill_directory_still_fails(self, tmp_path):
        skills = tmp_path / "pact-plugin" / "skills"
        known = skills / "pact-team-registration" / "SKILL.md"
        incomplete = skills / "incomplete-skill" / "README.md"
        known.parent.mkdir(parents=True)
        incomplete.parent.mkdir(parents=True)
        known.write_text("known\n", encoding="utf-8")
        incomplete.write_text("incomplete\n", encoding="utf-8")
        self._git(tmp_path, "init", "-q")
        self._git(tmp_path, "add", "-A")

        directories = _tracked_skill_dirs(tmp_path / "pact-plugin")
        assert incomplete.parent in directories
        with pytest.raises(AssertionError, match="incomplete-skill/ missing SKILL.md"):
            TestSkillMdFiles().test_each_skill_has_skill_md(directories)

    def test_gitless_fallback_is_nonempty_or_loud(self, tmp_path):
        skills = tmp_path / "pact-plugin" / "skills"
        tracked = skills / "pact-team-registration" / "SKILL.md"
        tracked.parent.mkdir(parents=True)
        tracked.write_text("exported\n", encoding="utf-8")
        assert tracked in _tracked_files(
            tmp_path, "pact-plugin/skills", "*/SKILL.md"
        )

        with pytest.raises(AssertionError, match="empty tracked population"):
            _tracked_files(tmp_path, "pact-plugin/agents", "pact-*.md")


def test_skill_dirs_fixture_excludes_untracked_skill_directories(
        tmp_path, monkeypatch, request):
    """The FIXTURE, not only the helper, must read the git index.

    Reverting ``skill_dirs`` to ``[d for d in SKILLS_DIR.iterdir() if d.is_dir()]``
    keeps every helper arm green; this arm resolves the fixture itself and
    reddens, because a valid-shaped untracked skill directory then joins it.
    """
    repo = tmp_path / "repo"
    skills = repo / "pact-plugin" / "skills"
    known = skills / "pact-team-registration" / "SKILL.md"
    known.parent.mkdir(parents=True)
    known.write_text(
        "---\nname: pact-team-registration\ndescription: known\n---\n",
        encoding="utf-8",
    )
    probe = skills / "000-untracked-probe"
    probe.mkdir()
    (probe / "SKILL.md").write_text(known.read_text(encoding="utf-8"), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "add", "--", "pact-plugin/skills/pact-team-registration/SKILL.md"],
        cwd=repo, check=True,
    )
    monkeypatch.setitem(globals(), "PLUGIN_ROOT", repo / "pact-plugin")
    monkeypatch.setitem(globals(), "SKILLS_DIR", skills)

    directories = request.getfixturevalue("skill_dirs")

    assert known.parent in directories
    assert probe not in directories, (
        "an untracked skill directory joined the shipped population; "
        "skill_dirs is not reading the git index"
    )


@pytest.mark.parametrize(
    "agent_path",
    _PACT_AGENT_FILES,
    ids=[p.stem for p in _PACT_AGENT_FILES],
)
def test_f20_pact_agent_declares_pact_agent_teams_skill(agent_path):
    """F20 pre-merge audit: each pact-*.md teammate file must list
    `pact-agent-teams` under its skills: frontmatter block. Without the
    skill preload, spawned teammates never see TaskList / SendMessage /
    completion-authority protocol — the original #662 trigger surface.

    Carve-out: pact-orchestrator is delivered via `claude --agent` and has
    no skills: block at all. Documented in F20_CARVE_OUT_FILES.
    """
    if agent_path.stem in F20_CARVE_OUT_FILES:
        pytest.skip(
            f"{agent_path.stem} is carved out from F20 — orchestrator "
            "persona delivered via --agent flag, not spawn"
        )
    text = agent_path.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    assert fm is not None, f"{agent_path.name} has no frontmatter"
    skills_value = fm.get("skills", "")
    assert "pact-agent-teams" in skills_value, (
        f"{agent_path.name} skills frontmatter missing pact-agent-teams. "
        "Spawned teammates would lack the team-protocol body — see #662 F20."
    )


class TestNoFirstActionFossilInSkillBodies:
    """Negative-invariant fossilization guard: skill bodies must not contain
    the v3.x FIRST-ACTION + Skill("PACT:teammate-bootstrap") + peer_inject
    delivery-mechanism prose. The mechanism and the bootstrap command were
    deleted; any surviving prose tells spawned teammates to look for content
    delivered by absent machinery.

    Symmetric to TestNoFirstActionFossilInConsumerCommands in
    test_commands_structure.py — that guard scans `commands/`; this one
    scans `skills/**/SKILL.md`. Skill bodies auto-load into every teammate
    spawn via `skills:` frontmatter preload, so a stale-mechanism reference
    here misroutes every teammate.
    """

    FORBIDDEN_FOSSIL_PATTERNS = (
        "YOUR FIRST ACTION (YOU MUST DO THIS IMMEDIATELY)",
        'Skill("PACT:teammate-bootstrap")',
        "Skill('PACT:teammate-bootstrap')",
        "/PACT:teammate-bootstrap",
        "/PACT:bootstrap",
        "peer_inject",
    )

    @pytest.mark.parametrize(
        "skill_path",
        _ALL_SKILL_FILES,
        ids=[p.parent.name for p in _ALL_SKILL_FILES],
    )
    def test_skill_body_has_no_v3x_delivery_fossil(self, skill_path):
        text = skill_path.read_text(encoding="utf-8")
        offenders = [p for p in self.FORBIDDEN_FOSSIL_PATTERNS if p in text]
        assert not offenders, (
            f"{skill_path.relative_to(SKILLS_DIR.parent)} contains v3.x "
            f"delivery-mechanism fossil(s): {offenders}. The bootstrap "
            f"command, peer_inject hook, and FIRST-ACTION prelude were all "
            f"removed; surviving prose describes machinery that no longer "
            f"exists and contradicts the current `skills:` frontmatter "
            f"preload model. Replace with current-state instructions."
        )


class TestOrderingInvariantPhraseCount:
    """Count-invariant pin for the 'Ordering invariant' load-bearing concept.

    The metadata-write -> SendMessage -> intentional_wait ordering is restated
    at multiple sites across the two skills that govern teammate handoff and
    teachback. Each restatement is load-bearing for a distinct audience
    (On Start gate, On Completion HANDOFF block, Intentional Waiting lead-side
    mirror, pact-teachback skill body). Silent elision of any one of these
    sites was the exact failure mode that motivated #740/#742.

    The pin uses the loose concept-phrase 'Ordering invariant' (not the
    qualified '(audit anchor)' variant) so site-specific parenthetical
    meta-tags can evolve without false-RED, while the named concept itself
    cannot be elided without false-RED.
    """

    EXPECTED_COUNTS = {
        "pact-agent-teams/SKILL.md": 3,
        "pact-teachback/SKILL.md": 1,
    }

    @pytest.mark.parametrize(
        "rel_path,expected",
        list(EXPECTED_COUNTS.items()),
        ids=list(EXPECTED_COUNTS.keys()),
    )
    def test_ordering_invariant_phrase_count(self, rel_path, expected):
        skill_md = SKILLS_DIR / rel_path
        assert skill_md.is_file(), f"{rel_path} must exist"
        text = skill_md.read_text(encoding="utf-8")
        actual = text.count("Ordering invariant")
        assert actual == expected, (
            f"{rel_path} must contain the phrase 'Ordering invariant' exactly "
            f"{expected} time(s); found {actual}. Each restatement is load-bearing "
            f"for a distinct audience (see class docstring); silent elision of any "
            f"site was the failure mode that motivated #740/#742. If a deliberate "
            f"refactor changes the count, update EXPECTED_COUNTS with a comment "
            f"naming which site was added or removed and why."
        )


class TestHarvestPerTeamSectionDirective:
    """Content-presence pin for the agent-memory per-team-section SCOPE
    directive in the harvest skill's save step.

    The secretary's processed-tasks read-back file is shared by every concurrent
    same-named secretary instance (one per team). The load-bearing fix re-scopes
    the save step from a whole-file write to a per-``## team={team_id}``
    section write, so a secretary in team A cannot clobber team B's section.
    This pin is about WHICH section may be touched, never about how it is
    written: the write within your own section is append-only, and that is a
    separate rule the skill states at its Step 8 section semantics.
    Because every agent-memory write is LLM-driven prose (no runtime code path),
    this directive has no executable regression test of its own — a future edit
    could silently revert the save step to a whole-file write and nothing
    would fail. This pin closes that gap: it asserts the verbatim directive
    phrases survive, so the per-team scoping cannot be elided without a RED.

    Pin style mirrors the EXPECTED_COUNTS content-presence pattern above.
    """

    REL_PATH = "pact-handoff-harvest/SKILL.md"

    # Verbatim load-bearing substrings of the save-step directive. If a
    # deliberate rewording changes these, update them here with a comment
    # confirming the per-team-section scoping (not whole-file overwrite) is
    # still expressed.
    # Reworded deliberately: "Overwrite only your own team's section" became
    # "Touch only your own team's section". The per-team-section scoping is
    # STILL EXPRESSED, and by both phrases below exactly as before -- the only
    # change is that the scope rule no longer uses a MODE verb to state it.
    # "Overwrite" survived a regime change: it was written when overwriting
    # your own section was the instruction, and the write is now append-only,
    # so the verb described an operation the skill had come to forbid.
    REQUIRED_PHRASES = (
        "Touch only your own team's section",
        "never modify, overwrite, or remove another team's",
    )

    @pytest.mark.parametrize("phrase", REQUIRED_PHRASES, ids=lambda p: p[:32])
    def test_per_team_overwrite_directive_present(self, phrase):
        skill_md = SKILLS_DIR / self.REL_PATH
        assert skill_md.is_file(), f"{self.REL_PATH} must exist"
        text = skill_md.read_text(encoding="utf-8")
        assert phrase in text, (
            f"{self.REL_PATH} must contain the per-team-section overwrite "
            f"directive phrase {phrase!r}. Its absence means the save step may "
            f"have silently reverted to a whole-file overwrite, which lets a "
            f"secretary in one team clobber another team's section in the "
            f"shared processed-tasks file. If a deliberate rewording changed "
            f"the phrase, update REQUIRED_PHRASES — but confirm the per-team "
            f"scoping (not whole-file overwrite) is still expressed."
        )


class TestOrphanRecoveryCarrierGuard:
    """Content-presence pin for the carrier guard at Orphaned Handoff
    Recovery step 4 of the harvest skill.

    Step 4 used to instruct an unguarded removal of the files the recovery
    pass had read. Either of the two file classes it names can be the ONLY
    carrier of a given HANDOFF: the session journal holds the tasks that
    emitted an ``agent_handoff`` event, and the task files hold a quiescent
    completed task of which the key reached no journal. The repaired step
    removes nothing, and its carrier test gates the Step 5 report instead.

    That instruction is LLM-read prose with no runtime path, so no
    behavioural test reaches it. This pin closes that gap. Each of the three
    phrases pins a different property: the first pins the instruction, the
    second pins the processed-is-not-removable inference, and the third pins
    the carrier test. A single phrase lets two of the three go missing
    behind a green suite.

    WHAT THIS CANNOT CATCH, stated so that a green is read no wider than it
    is: a rewording that keeps a phrase and defeats it in the next
    paragraph, a contradicting instruction added elsewhere in the file, and
    a phrase moved into a section it does not govern. It also cannot
    separate a deliberate rewording from a regression.

    THIS PIN COVERS THE NON-EXECUTABLE HALF ONLY. The reaper guard in
    ``hooks/session_end.py`` is the executable half and carries its own arms
    in ``test_session_end.py``. The two gates do not cross-cover, so a green
    from one says nothing about the other.

    Pin style mirrors TestHarvestPerTeamSectionDirective above.
    """

    REL_PATH = "pact-handoff-harvest/SKILL.md"

    # Verbatim load-bearing substrings of the step 4 carrier guard. If a
    # deliberate rewording changes these, update them here with a comment
    # that states step 4 removes nothing and that the carrier test with its
    # un-evaluable row is expressed.
    REQUIRED_PHRASES = (
        "Do NOT remove the files you read them from",
        "It does not follow that you may remove them",
        "the journal is the ONLY carrier of what you recovered",
    )

    @pytest.mark.parametrize("phrase", REQUIRED_PHRASES, ids=lambda p: p[:32])
    def test_carrier_guard_phrase_present(self, phrase):
        skill_md = SKILLS_DIR / self.REL_PATH
        assert skill_md.is_file(), f"{self.REL_PATH} must exist"
        text = skill_md.read_text(encoding="utf-8")
        assert phrase in text, (
            f"{self.REL_PATH} must contain the Orphaned Handoff Recovery "
            f"carrier-guard phrase {phrase!r}. Its absence means step 4 "
            f"could have reverted to an unguarded removal of the files the "
            f"recovery pass read, and either of those file classes can be "
            f"the ONLY carrier of a recovered HANDOFF. A journal removed "
            f"once costs every HANDOFF, lifecycle event and snapshot that "
            f"only it held, and nothing regenerates them. If a deliberate "
            f"rewording changed the phrase, update REQUIRED_PHRASES, and "
            f"state in that update that step 4 removes nothing and that the "
            f"carrier test is expressed."
        )


class TestWorktreeSetupVenvLinkReportPins:
    """Content-presence pins for the non-executable half of the venv link step.

    Both strings are LLM-read or printed instructions with no runtime path, so
    no behavioural test reaches them. Measured: deleting either leaves the whole
    fence suite green. They are pinned HERE because test_skills_structure.py is
    the house content-pin module for instruction prose that no executable path
    covers (see TestOrphanRecoveryCarrierGuard).

    WHAT THIS CANNOT CATCH, stated so a green is read no wider than it is: a
    rewording that keeps a phrase and defeats it in the next sentence, a
    contradicting instruction added elsewhere in the file, and a phrase moved
    into a section it does not govern.
    """

    REL_PATH = "worktree-setup/SKILL.md"
    REQUIRED_PHRASES = (
        "(read-only, shared with the main checkout — do not install through it)",
        "<every line Step 4 printed, warnings included>",
    )

    @pytest.mark.parametrize("phrase", REQUIRED_PHRASES, ids=lambda p: p[:32])
    def test_report_phrase_present(self, phrase):
        skill_md = SKILLS_DIR / self.REL_PATH
        assert skill_md.is_file(), f"{self.REL_PATH} must exist"
        text = skill_md.read_text(encoding="utf-8")
        assert phrase in text, (
            f"{self.REL_PATH} must contain {phrase!r}. The first phrase is the "
            "printed advisory that environment-managing tools write through the "
            "shared link; without it an agent can run `pip install` or `uv sync` "
            "believing the worktree has its own environment. The second is the "
            "report instruction that carries every line Step 4 printed, "
            "warnings included; without it a skipped or failed link disappears "
            "from the setup report. If a deliberate rewording changed a phrase, "
            "update REQUIRED_PHRASES and confirm both properties are still "
            "expressed."
        )
