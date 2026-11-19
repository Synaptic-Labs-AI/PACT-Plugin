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
from test_peer_review_cold_lane_pin import FORWARD_POINTER

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

    Only the helper's empty-population shape is a layout omission. Its
    fail-closed refusals (unavailable git, an unsafe root) fail in every
    layout rather than skipping as though the artifact were absent.
    """
    try:
        return _shared_tracked_files(REPO_ROOT, relative_dir, pattern)
    except AssertionError as exc:
        if "empty tracked population" not in str(exc):
            raise
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

    def test_layout_guard_classifies_by_failure_shape(
            self, tmp_path, monkeypatch):
        """Outside the dev checkout, only an EMPTY population is a layout gap.

        The helper's fail-closed refusals are failures in every layout; a
        blanket catch would relabel broken git as an installed layout that
        ships no skills. The skip must name the layout omission, and the
        refusal must surface with its own message.
        """
        install = tmp_path / "PACT" / "4.8.34"
        skills = install / "skills"
        skills.mkdir(parents=True)
        (skills / "untracked").mkdir()
        (skills / "untracked" / "SKILL.md").write_text(
            "untracked\n", encoding="utf-8")
        self._git(tmp_path, "init", "-q")
        monkeypatch.setitem(globals(), "REPO_ROOT", tmp_path)
        monkeypatch.setitem(globals(), "PLUGIN_ROOT", install)

        with pytest.raises(pytest.skip.Exception, match="installed layout"):
            _collection_population(f"{install.name}/skills", "*/SKILL.md")

        real_run = subprocess.run

        def fake_run(args, **kwargs):
            if args[:2] == ["git", "ls-files"]:
                return subprocess.CompletedProcess(
                    args, 128, stdout="", stderr="fatal: synthetic index failure"
                )
            if args[:2] == ["git", "rev-parse"]:
                return subprocess.CompletedProcess(
                    args, 0, stdout="true\n", stderr="")
            return real_run(args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        try:
            _collection_population(f"{install.name}/skills", "*/SKILL.md")
        except AssertionError as exc:
            assert "Refusing to widen" in str(exc), exc
        except pytest.skip.Exception as exc:
            # A skip here means the guard swallowed the refusal as a layout
            # omission; that is the defect this arm exists to catch.
            raise AssertionError(
                f"the layout guard swallowed a refusal as a skip: {exc}"
            ) from exc
        else:
            raise AssertionError("the refusal did not surface at all")

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


def _instruction_surface_population():
    """Every tracked markdown file across the four LLM-loaded surfaces.

    Built from the collection-time populations so an untracked scratch
    file stays invisible and the installed-layout skip behavior matches
    the rest of the module.
    """
    files = []
    for surface in ("skills", "agents", "commands", "protocols"):
        files.extend(_collection_population(f"{PLUGIN_ROOT.name}/{surface}", "**"))
    return sorted(p for p in files if p.suffix == ".md")


class TestMemorySavedRetiredFromInstructionSurfaces:
    """The memory_saved flag was retired in v4.8.36: zero code readers,
    self-reported, unenforced — the "Confirm Memory Saved" checklist step
    in pact-agent-teams/SKILL.md and the emitter docstring caveat naming
    the flag were both deleted. Nothing else asserted that retirement, so
    a future reintroduction would land unpinned.

    Scoped to the four LLM-loaded instruction surfaces (skills/, agents/,
    commands/, protocols/ — every tracked markdown file): a token an LLM
    loads as instruction re-teaches the retired flag wherever it appears.
    The repo-root CHANGELOG's retirement note legitimately names the flag
    and stays out of scope. A bare substring check is deliberate — it
    catches backtick-wrapped, fenced, and prose forms alike. A tracked
    file that is not valid UTF-8 is named by the pin rather than raising
    a bare decode error.
    """

    FORBIDDEN_TOKEN = "memory_saved"

    def test_no_memory_saved_in_any_llm_loaded_surface(self):
        offenders = []
        undecodable = []
        for path in _instruction_surface_population():
            try:
                text = path.read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                undecodable.append(path)
                continue
            if self.FORBIDDEN_TOKEN in text:
                offenders.append(path)
        assert not offenders, (
            f"{[str(p.relative_to(PLUGIN_ROOT.parent)) for p in offenders]} "
            f"re-teach the retired memory_saved flag. The flag was removed "
            f"in v4.8.36 (zero code readers, unenforced — a task could "
            f"attest a save no mechanism verified); reintroducing the token "
            f"in an LLM-loaded surface revives a write-only signal. Update "
            f"this pin deliberately if the flag ever returns with an "
            f"enforcer."
        )
        assert not undecodable, (
            f"{[str(p.relative_to(PLUGIN_ROOT.parent)) for p in undecodable]} "
            f"is not valid UTF-8, so this pin cannot read it to check for "
            f"the retired memory_saved flag; instruction surfaces must "
            f"stay UTF-8."
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




@pytest.mark.parametrize("text, banned", [
    ("in the copy described below", True),
    ("the clone defined next", True),
    ("the recipe given later", True),
    ("a copy built below", True),
    ("Shown later in this bullet", True),
    ("see below for the copy", True),
    ("the failing-test rule below", False),
    ("the pre-existing and failure branches below", False),
    ("the pre-existing-failure rule at the end of this bullet", False),
])
def test_forward_pointer_scope(text, banned):
    # The pattern bans the deferring forms its comment lists and nothing else.
    assert bool(FORWARD_POINTER.search(text)) is banned, (
        f"FORWARD_POINTER {'missed' if banned else 'banned'} {text!r}; "
        "update the comment's list with the pattern"
    )


def test_forward_pointer_has_one_definition():
    # The pattern is defined once, in the cold-lane pin, so the two receipts
    # cannot drift apart; this module keeps no copy of its own.
    import test_peer_review_cold_lane_pin as cold
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    local = [node for node in ast.walk(tree)
             if isinstance(node, (ast.Assign, ast.AnnAssign))
             and any(isinstance(target, ast.Name) and target.id == "FORWARD_POINTER"
                     for target in (node.targets if isinstance(node, ast.Assign) else [node.target]))]
    assert not local, "test_skills_structure.py defines its own FORWARD_POINTER"
    assert FORWARD_POINTER is cold.FORWARD_POINTER, (
        "test_skills_structure.py's FORWARD_POINTER is not the cold-lane pin's"
    )


class TestRunReceiptAttribution:
    """The pact-agent-teams Step 0 run receipt, branch by branch.

    - Before each run, HEAD and the status are recorded and the staged change
      is saved to a file outside the worktree, scoped to the explicit paths
      the teammate stages by, so a peer's staged work on other paths never
      enters the change copy; a path shared with a peer is named in the
      HANDOFF, and a failure only a peer's hunk there explains is not the
      teammate's.
    - The whole change, new files included, is staged by explicit path before
      each reported run; a run that starts with an unstaged or untracked path
      of the change does not count and is run again after staging.
    - The isolated copy that holds the change is defined in its own
      sub-bullet before every run that uses it, with no forward pointer:
      a remote-less clone of the worktree's local repository (never a remote
      URL, which lacks unpushed commits) at the pre-run HEAD with the saved
      staged change applied.
    - When the worktree status before a run shows any path outside the
      teammate's own paths, the run goes to that copy instead of the
      worktree, so a peer's edit cannot mask the teammate's own break.
    - A moved HEAD is re-run once before the HANDOFF, in that copy, never
      in the worktree. That copy's run is the
      result; its own staged files are never read as written by the run.
    - If the copy cannot be built or the command cannot be run in it, on any
      run that needs it (its own sub-bullet after both branches), the
      teammate cannot tell whether a failure is its own, so it stops and
      sends the lead a BLOCKER naming the command, what moved and why; it
      never hands off past it. That exit
      never takes paths the change copy reproduces: on any run, those follow
      the pre-existing and failure branches.
    - A status-only change is re-run with the same interpreter, by absolute
      path, in the same copy, never the live index. That copy's run is the
      result, stated once per branch.
    - One baseline clone, defined in its own sub-bullet before every
      branch, serves both the reproduced-path check and the failing-test
      rule: a remote-less clone of the worktree's local repository at the
      HEAD recorded before the run, with no change applied. The teammate's subject is its own change, so a failure
      an earlier commit on the branch caused is pre-existing to it; the
      receipt names no merge base and no fetch.
    - One reproduced-path rule, its own sub-bullet after both branches and
      the unbuildable exit, serves every run in the copy: every path that differs
      between the copy's status before and after its run is reproduced,
      whatever the worktree status did, and a worktree path the copy does
      not change came from outside the run. With no reproduced path the
      copy's run stands; with any, the command is re-run in the baseline
      clone. Each reproduced path is decided on its own by the baseline
      clone's own run: one that also
      changes between that clone's status before and after its run is
      pre-existing and reported in the HANDOFF; one that does not is a
      failure of the teammate's own change whatever the other paths are, to
      fix, never re-run away. When every reproduced path is pre-existing,
      that copy's run decides: 0 is a pass, and its failing tests follow the
      general rule below.
    - For every reported run, with or without a status change, a failing
      test is a failure of the change only when the change copy fails it and
      the baseline clone passes it. A worktree run with failing tests is
      re-run once in the change copy with the same command, selection and
      flags, so an order dependence the change causes still shows; a test
      that passes there came from outside the change, and one the baseline clone also fails is pre-existing. Only
      then may the teammate hand off, naming those tests and the
      precondition unmet through them; any other failing test blocks it. The
      exit-code precondition names that one exception and no other, so an
      unbuildable copy never waives it.
    - No git archive export is named as the copy.

    Each arm is bound to the Step 0 receipt bullet, read through its
    indented continuation and sub-bullet lines up to the next sibling bullet
    or blank line, so the phrase cannot be satisfied from elsewhere in the
    skill and the rule may be laid out as sub-bullets.

    WHAT THIS CANNOT CATCH: a contradicting instruction added outside the
    bullet, and whether a teammate actually re-runs in an isolated copy or
    runs the baseline control.
    """

    REL_PATH = "pact-agent-teams/SKILL.md"
    BULLET_START = "- For each of those runs, record `git rev-parse HEAD`"
    REQUIRED_PHRASES = (
        ("Stage your whole change by explicit path, new files included, before each run you report. If an unstaged or untracked path of your change exists when a run starts, stage it and run again; that run does not count.",
         "the whole change staged before each reported run, so the saved diff is the change"),
        ("and immediately before it also save your staged change with `git diff --cached --binary -- <your paths>` to a file outside the worktree, such as one in your session scratch directory, where `<your paths>` are the explicit paths you stage your change by and list in your HANDOFF, new files included.",
         "the record step: only your own staged paths saved before the run, outside the worktree"),
        ("A peer's staged change to any other path is not part of your change and stays out of the saved file.",
         "a peer's staged change to another path stays out of the change copy"),
        ("A path you share with a peer carries the peer's staged hunks in that file too: name each such path in your HANDOFF, and a failure that a peer's hunk alone explains is reported, with the path, and is not a failure of your change.",
         "a shared path is named, and a failure a peer's hunk alone explains is not the teammate's"),
        ("If `HEAD` differs, the worktree moved during the run: re-run it once before you write the HANDOFF, in the isolated copy that holds your change, not in the worktree, and never report the moved run as a pass or a failure of your change.",
         "the moved-HEAD branch: one re-run, in the change copy, never the worktree"),
        ("never report the moved run as a pass or a failure of your change. That copy's run is your result.",
         "the moved-HEAD copy run as the result"),
        ("If the copy that holds your change cannot be built or the command cannot be run in it, on any run that needs the copy, you cannot tell whether a failure is yours: stop, do not hand off, and send the lead a BLOCKER as in [On Blocker](#on-blocker), naming the command, what moved (`HEAD` or the paths) and why the copy cannot be built or run.",
         "the stop: an unbuildable copy is a blocker, never a waiver of the exit-code precondition"),
        ("This exit never applies to paths the copy that holds your change reproduces; on any run, those follow the pre-existing and failure branches below.",
         "a reproduced write never reaches the blocker exit"),
        ("If, before a run you report, `git status --porcelain --untracked-files=all` shows any path that is not one of your paths, run the command in the isolated copy that holds your change instead of the worktree, with the same interpreter, by absolute path, and report that copy's run in place of a worktree run, judged by the rules below for a run in that copy.",
         "a worktree carrying any path outside the teammate's own paths sends the run to the change copy"),
        ("If only the status differs, re-run the same command with the same interpreter, by absolute path (for example the worktree's venv `python`), in the isolated copy that holds your change. Compare its status before and after. That copy's run is your result.",
         "the isolated-copy re-run, with the same interpreter by absolute path"),
        ("Every run below that leaves the worktree uses one isolated copy that holds your change: a clone of this worktree's local repository, made from its path and never from a remote URL, with its remote removed, checked out at the `HEAD` you recorded before the run, with the staged change you saved before the run applied by running `git apply --index` on that file in the clone.",
         "the recipe, defined once before every re-run: the pre-run HEAD plus the staged change saved before the run"),
        ("The reproduced-path check and the failing-test rule below both use one baseline clone: a clone of this worktree's local repository, made from its path and never from a remote URL, with its remote removed, checked out at the `HEAD` you recorded before the run, with no change applied.",
         "the one baseline clone both rules use: the recorded pre-run HEAD with no change applied"),
        ("After any run in the copy that holds your change, every path that differs between the status of that copy before and after its run is a reproduced path, and a path that changed in the worktree but does not change in that copy came from outside the run and is not a failure of your change.",
         "one reproduced-path rule for both re-runs: every path the copy's run changes"),
        ("If there is no reproduced path, that copy's run stands as your result. If there is any, run the same command the same way in the baseline clone.",
         "the reproduced-path check names the baseline clone, and no reproduced path leaves the copy's run standing"),
        ("Decide each reproduced path on its own. A reproduced path that also changes between the baseline clone's status before and after its run is a pre-existing defect: report it in your HANDOFF, with the path and the command that wrote it, and do not treat it as a failure of your change.",
         "a base-reproduced write reported as pre-existing"),
        ("When every reproduced path is pre-existing, exit code 0 counts as a pass, and failing tests follow the pre-existing-failure rule at the end of this bullet.",
         "the all-pre-existing branch defers failing tests to the general rule"),
        ("For any run whose result you report, with or without a status change, a failing test is a failure of your change only when it fails in the copy that holds your change and passes, with the same command and interpreter, in the baseline clone.",
         "a failing test counts only when the change copy fails it and the baseline clone passes it, for every reported run"),
        ("When the run you report is the worktree's and a test fails, re-run the same command, with the same test selection and flags, once in that copy, and no more: a test that fails in the worktree but passes in that copy's run came from outside your change, so report it with the command, and it does not block.",
         "a worktree failure is re-run once in the change copy, and one that passes there is reported, not blocking"),
        ("A failing test that also fails in the baseline clone is pre-existing.",
         "a failing test the baseline clone also fails is pre-existing"),
        ("When every failing test is pre-existing or from outside your change, you may hand off: name each such test, with the command, in your HANDOFF, and state there that the exit-code precondition above is unmet only through those tests.",
         "the pre-existing-failure hand-off, naming the tests and the unmet precondition"),
        ("Any other failing test is a failure of your change and blocks the HANDOFF.",
         "any other failing test still blocks the HANDOFF"),
        ("A reproduced path that does not change in the baseline clone's run was written by the run itself: treat it as a failure of your change, whatever the other paths are, fix it, and say so in your HANDOFF; never send it as a blocker or re-run it away.",
         "the change-only write treated as a failure of the change"),
    )

    def _bullet(self) -> str:
        text = (SKILLS_DIR / self.REL_PATH).read_text(encoding="utf-8")
        assert text.count(self.BULLET_START) == 1, (
            f"{self.REL_PATH} must carry exactly one Step 0 run-receipt bullet "
            f"starting {self.BULLET_START!r}"
        )
        # The bullet runs from its first line through every following
        # indented line (continuation and sub-bullet lines), and stops at the
        # first blank line or the first line at column 0 (the next sibling).
        lines = text[text.index(self.BULLET_START):].split("\n")
        extent = [lines[0]]
        for line in lines[1:]:
            if not line.strip() or not line[:1].isspace():
                break
            extent.append(line)
        return "\n".join(extent)

    @pytest.mark.parametrize("phrase, lost", REQUIRED_PHRASES, ids=lambda v: v[:32] if isinstance(v, str) else v)
    def test_receipt_branch_present(self, phrase, lost):
        assert phrase in self._bullet(), (
            f"the {self.REL_PATH} Step 0 run-receipt bullet lost {lost} ({phrase!r})"
        )

    def test_precondition_names_its_one_receipt_exception(self):
        text = (SKILLS_DIR / self.REL_PATH).read_text(encoding="utf-8")
        line = (
            "- All relevant tests have been run with exit code 0, except failing tests "
            "that the next bullet finds pre-existing or from outside your change."
        )
        assert text.count(line) == 1, (
            f"the {self.REL_PATH} exit-code precondition must name its one "
            "exception, failing tests the run-receipt bullet finds pre-existing or "
            "from outside your change, so it does not contradict that bullet"
        )
        assert "unattributable" not in text, (
            f"the {self.REL_PATH} still names an unattributable run, which waived "
            "the exit-code precondition when the copy could not be built"
        )
        # "The next bullet" must be the receipt bullet: an inserted bullet reds.
        lines = text.split("\n")
        at = next(i for i, ln in enumerate(lines) if ln == line or ln.startswith(line))
        after = next(ln for ln in lines[at + 1:] if ln.startswith("- "))
        assert after.startswith(self.BULLET_START), (
            f"the {self.REL_PATH} exit-code precondition says 'the next bullet', "
            f"but the next bullet is not the run-receipt bullet: {after[:80]!r}"
        )

    def test_each_branch_states_its_result_once(self):
        # Once in the moved-HEAD branch and once in the status-only branch.
        count = self._bullet().count("copy's run is your result")
        assert count == 2, (
            f"the {self.REL_PATH} run-receipt bullet must state the copy's run as "
            f"the result once per branch (moved HEAD, status-only), found {count}"
        )

    def test_baseline_clone_is_defined_before_every_branch(self):
        # The definition must be its own sub-bullet ahead of the branches, so
        # neither rule that names "the baseline clone" reads it as conditional.
        bullet = self._bullet()
        define = bullet.find("both use one baseline clone")
        first_branch = bullet.find("  - If `HEAD` differs")
        assert define != -1 and first_branch != -1 and define < first_branch, (
            f"the {self.REL_PATH} run-receipt bullet must define the baseline "
            "clone in its own sub-bullet before the first branch"
        )
        assert bullet.count("the baseline clone") >= 3, (
            f"the {self.REL_PATH} reproduced-path check, per-path decision and "
            "failing-test rule must each name the baseline clone"
        )

    def test_baseline_is_the_recorded_head_without_the_change(self):
        # The teammate's subject is its own staged change, so its baseline is
        # the commit it ran on without that change; a merge base with the
        # target branch would charge it with failures earlier branch commits
        # caused, and a fetch would only serve that merge base.
        bullet = self._bullet()
        start = bullet.find("  - The reproduced-path check and the failing-test rule below both use one baseline clone")
        assert start != -1, f"the {self.REL_PATH} baseline definition sub-bullet is missing"
        end = bullet.find("\n", start)
        sub = bullet[start:end if end != -1 else None]
        assert "checked out at the `HEAD` you recorded before the run, with no change applied" in sub, (
            f"the {self.REL_PATH} baseline clone must sit at the recorded "
            f"pre-run HEAD with no change applied: {sub!r}"
        )
        lowered = bullet.lower()
        for stray in ("merge base", "merge-base", "fetch_head", "git fetch"):
            assert stray not in lowered, (
                f"the {self.REL_PATH} run receipt still names {stray!r}; the "
                "teammate's baseline is its recorded HEAD, not a merge base"
            )

    def test_unbuildable_copy_exit_follows_both_branches(self):
        # The exit sits in its own sub-bullet after the status-only branch, so
        # the moved-HEAD and status-only re-runs both reach it.
        bullet = self._bullet()
        moved = bullet.find("  - If `HEAD` differs")
        status_only = bullet.find("  - If only the status differs")
        exit_ = bullet.find("  - If the copy that holds your change cannot be built")
        assert -1 not in (moved, status_only, exit_) and moved < status_only < exit_, (
            f"the {self.REL_PATH} blocker exit must be its own sub-bullet "
            "after both the moved-HEAD and the status-only branches "
            f"(positions moved={moved}, status-only={status_only}, exit={exit_})"
        )
        moved_sub = bullet[moved:status_only]
        assert "cannot be built" not in moved_sub, (
            f"the {self.REL_PATH} blocker exit sits inside the moved-HEAD "
            "branch, where the status-only re-run does not reach it"
        )

    def test_one_reproduced_path_rule_follows_both_branches(self):
        # Both re-runs share one rule for which paths are reproduced, placed
        # after both branches and the exit, so neither branch carries its own
        # rule that the other can override.
        bullet = self._bullet()
        moved = bullet.find("  - If `HEAD` differs")
        exit_ = bullet.find("  - If the copy that holds your change cannot be built")
        shared = bullet.find("  - After any run in the copy that holds your change, every path that differs")
        decide = bullet.find("  - Decide each reproduced path on its own")
        assert -1 not in (moved, exit_, shared, decide) and moved < exit_ < shared < decide, (
            f"the {self.REL_PATH} reproduced-path rule must be one sub-bullet "
            "after both branches and the blocker exit, before the "
            f"per-path decision (positions moved={moved}, exit={exit_}, "
            f"shared={shared}, decide={decide})"
        )
        branches = bullet[moved:exit_]
        for stray in ("reproduced path", "outside the run", "also change there"):
            assert stray not in branches, (
                f"the {self.REL_PATH} re-run branches carry their own "
                f"reproduced-path wording ({stray!r}); only the shared rule may"
            )
        assert bullet.count("came from outside the run") == 1, (
            f"the {self.REL_PATH} receipt must say once, in the shared rule, "
            "which paths came from outside the run"
        )

    def test_pre_existing_decision_reads_the_baseline_run(self):
        # "Appears in the baseline clone" reads as "exists there", and every
        # tracked file exists in that checkout; only a change during that
        # clone's own run makes a reproduced path pre-existing.
        bullet = self._bullet()
        decide = bullet.find("  - Decide each reproduced path on its own")
        assert decide != -1, f"the {self.REL_PATH} per-path decision sub-bullet is missing"
        end = bullet.find("\n  - ", decide + 1)
        decision = bullet[decide:end if end != -1 else None]
        assert "appear" not in decision.lower(), (
            f"the {self.REL_PATH} per-path decision says a path 'appears' in the "
            "baseline clone; it must say the path changes during that clone's run"
        )
        for phrase in ("also changes between the baseline clone's status before and after its run",
                       "does not change in the baseline clone's run"):
            assert phrase in decision, (
                f"the {self.REL_PATH} per-path decision lost {phrase!r}"
            )

    def test_change_copy_holds_only_your_own_paths(self):
        # A shared index holds every teammate's staged work; a whole-index
        # diff would charge a peer's staged change to the teammate whose copy
        # it lands in.
        bullet = self._bullet()
        assert "`git diff --cached --binary -- <your paths>`" in bullet, (
            f"the {self.REL_PATH} record step must save the staged change scoped "
            "to the teammate's own paths"
        )
        assert "`git diff --cached --binary`" not in bullet, (
            f"the {self.REL_PATH} record step saves the whole shared index, so a "
            "peer's staged work lands in the change copy"
        )

    def test_a_worktree_failure_is_decided_in_the_change_copy(self):
        # A worktree run carries every peer's edits, so its failing test is
        # the teammate's only when the copy holding just the teammate's change
        # fails it too, after one bounded re-run of the same command there; a
        # re-run of the failing tests alone would excuse a failure the change
        # causes through test order.
        bullet = self._bullet()
        rule = bullet.find("  - For any run whose result you report")
        assert rule != -1, f"the {self.REL_PATH} failing-test rule is missing"
        end = bullet.find("\n  - ", rule + 1)
        text = bullet[rule:end if end != -1 else None]
        for phrase in ("fails in the copy that holds your change and passes",
                       "re-run the same command, with the same test selection and flags, once in that copy, and no more",
                       "came from outside your change"):
            assert phrase in text, (
                f"the {self.REL_PATH} failing-test rule lost {phrase!r}"
            )
        assert "re-run its failing tests" not in text, (
            f"the {self.REL_PATH} failing-test rule re-runs only the failing "
            "tests, which excuses a failure the change causes through test order"
        )
        assert "is pre-existing only when" not in text, (
            f"the {self.REL_PATH} failing-test rule still decides a failure by the "
            "baseline clone alone, charging a peer's worktree edits to the teammate"
        )

    def test_change_copy_defined_before_first_use(self):
        # An agent reads the receipt top-down: the copy's recipe must come
        # before the first branch that re-runs in it, and no sentence may
        # point forward to an artifact defined later.
        bullet = self._bullet()
        define = bullet.find("  - Every run below that leaves the worktree uses one isolated copy that holds your change: a clone of this worktree's local repository")
        baseline = bullet.find("  - The reproduced-path check and the failing-test rule below both use one baseline clone")
        first_use = bullet.find("  - If `HEAD` differs")
        assert -1 not in (define, baseline, first_use) and baseline < first_use and define < first_use, (
            f"the {self.REL_PATH} run receipt must define the baseline clone and "
            "the copy that holds your change before the moved-HEAD branch, the "
            f"first that uses them (positions baseline={baseline}, copy={define}, "
            f"first use={first_use})"
        )
        assert bullet.find("`git diff --cached --binary -- <your paths>`") < define, (
            f"the {self.REL_PATH} run receipt must save the staged change before "
            "the copy that applies it is defined"
        )
        pointer = FORWARD_POINTER.search(bullet)
        assert pointer is None, (
            f"the {self.REL_PATH} run receipt points forward to an artifact it "
            f"has not defined yet: {pointer.group(0)!r}"
        )

    def test_peer_paths_in_the_worktree_send_the_run_to_the_copy(self):
        # A peer's edit in the shared worktree can mask the teammate's own
        # break, so a worktree carrying any path outside the teammate's paths
        # is never the run that is reported.
        bullet = self._bullet()
        define = bullet.find("  - Every run below that leaves the worktree uses one isolated copy")
        trigger = bullet.find("  - If, before a run you report, `git status --porcelain --untracked-files=all` shows any path that is not one of your paths")
        moved = bullet.find("  - If `HEAD` differs")
        assert -1 not in (define, trigger, moved) and define < trigger < moved, (
            f"the {self.REL_PATH} peer-path trigger must follow the copy's "
            "definition and precede the moved-HEAD branch "
            f"(positions copy={define}, trigger={trigger}, moved={moved})"
        )

    def test_clones_are_made_from_the_local_repository(self):
        # A clone of the remote lacks the teammate's unpushed HEAD, so it
        # cannot be built; both clones name the worktree's local repository.
        bullet = self._bullet()
        source = "a clone of this worktree's local repository, made from its path and never from a remote URL"
        assert bullet.count(source) == 2, (
            f"the {self.REL_PATH} baseline clone and change copy must both be "
            f"made from the worktree's local repository ({source!r})"
        )
        assert "a clone of the repository with its remote removed" not in bullet, (
            f"the {self.REL_PATH} run receipt names a clone without its source"
        )

    def test_an_unbuildable_copy_is_a_blocker(self):
        bullet = self._bullet()
        exit_ = bullet.find("  - If the copy that holds your change cannot be built")
        end = bullet.find("\n  - ", exit_ + 1)
        text = bullet[exit_:end if end != -1 else None]
        for phrase in ("stop, do not hand off, and send the lead a BLOCKER",
                       "[On Blocker](#on-blocker)"):
            assert phrase in text, (
                f"the {self.REL_PATH} unbuildable-copy exit lost {phrase!r}"
            )
        assert "exit-code precondition" not in text, (
            f"the {self.REL_PATH} unbuildable-copy exit still speaks of the "
            "exit-code precondition, which it must never waive"
        )

    def test_receipt_names_no_archive_export(self):
        # An archive export holds neither the staged change nor .git, so the
        # run-made path may not reappear there; only a clone is named.
        assert "git archive" not in self._bullet(), (
            f"the {self.REL_PATH} Step 0 run-receipt bullet names a git archive "
            "export again; the isolated copy must be a remote-less clone with "
            "the staged change applied"
        )
