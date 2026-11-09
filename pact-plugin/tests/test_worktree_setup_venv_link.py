"""Step 4's venv link: the state on disk, not the words in the skill.

`skills/worktree-setup/SKILL.md` Step 4 is executed, not read. A phrase pin
over that fence disarms silently when the prose is correctly rewritten, so the
arms here extract the one bash fence from Step 4 and RUN it, unmodified apart
from the `{branch}` substitution the step itself specifies, against a
throwaway `git init` repo with a real worktree in it.

WHY THREE POSITIVE ASSERTIONS AND NOT ONE. Each is blind to what the others
catch:

- `test -L .venv` proves the link EXISTS. A clean `git status --porcelain` is
  produced by "linked and ignored" AND by "never linked" alike, so the check
  that exists to catch a dropped link step passes in exactly the regression it
  is meant to catch unless existence is asserted first.
- `git status --porcelain` proves the link would not be swept into a commit.
- `git ls-files --error-unmatch .venv` proves the link is not already TRACKED.
  Porcelain is blind to a tracked link, while `git check-ignore` is index-aware
  and reports a tracked path as NOT ignored even when the rule matches. So
  neither of the other two can see that state.

THE MUTATION META-TEST PINS THE DISCRIMINATION CLAIM. The paragraphs above say
which arm catches which regression; `TestMutationDiscrimination` mutates the
extracted fence in memory and asserts the exact matrix. A docstring claim
without that test is an assertion about tests, not a measurement of them.

`git check-ignore` is diagnostic here: its output names the matching rule and
is printed on failure, but it is never the primary assertion. The skill's own
runtime `check-ignore -q` is a different use — there, rc 1 is the intended loud
warning, including for an already-tracked link.

THE WRITE-FAILURE ARM USES A DIRECTORY, NOT A MISSING WORKTREE. A nonexistent
worktree is preempted by the fence's own path guard and, worse, guarantees no
link could exist there — so a fence that warned and then linked anyway would
still pass. Making `<common-git-dir>/info/exclude` a directory keeps the
worktree real: both write legs fail, and a stray `ln` would otherwise succeed
and be caught by the no-link assertion. No chmod is used anywhere, because CI
may run as root, where permission bits do not bite and the arm would pass
vacuously.

BINARY AND ENVIRONMENT, stated because it bounds what these arms prove: the
fence runs under `/bin/bash` with `PATH` pinned to `/usr/bin:/bin`, matching
the shipped-command house pattern. Git's global and system config is isolated
(fake HOME, fake XDG_CONFIG_HOME, `GIT_CONFIG_NOSYSTEM=1`,
`GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` unset) because a developer's global
excludesfile can change what this fixture measures — this host's ignores
`.gitignore` itself, so an unisolated run would test a different tree locally
than on CI. No remote and no worktree of the real repo is ever touched.

THE CONFIG ARM REQUIRES A SOURCE CHECKOUT, and says so with a POSITIVE marker
(`.claude-plugin/marketplace.json`), following `test_local_lint_parity.py`.
From an INSTALLED PLUGIN CACHE the repo-root `pyrightconfig.json` does not
exist by design, so that arm SKIPS with a "SKIPPED, NOT PASSED" message rather
than failing on a file the cache never ships. The fence arms need only
`SKILL.md`, which the cache does ship, so they run everywhere.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

SKILL_FILE = (
    Path(__file__).resolve().parents[1] / "skills" / "worktree-setup" / "SKILL.md"
)
BASH = Path("/bin/bash")
SAFE_PATH = "/usr/bin:/bin"

_FENCE = re.compile(r"```bash\n(.*?)```", re.DOTALL)
BRANCH = "feature/test-worktree"
GIT_ID = ("-c", "user.email=test@example.com", "-c", "user.name=Test")

pytestmark = pytest.mark.skipif(not BASH.exists(), reason="no /bin/bash")


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _section(skill_text, start_heading, end_heading):
    start = skill_text.find(start_heading)
    assert start != -1, f"no {start_heading!r} heading in worktree-setup SKILL.md"
    block = skill_text[start:]
    end = block.find(end_heading)
    assert end != -1, (
        f"{start_heading!r} does not run into {end_heading!r} — the file changed shape"
    )
    return block[:end]


def _fence_in(skill_text, start_heading, end_heading, branch):
    """The single bash fence between two headings, with `{branch}` substituted.

    Fails closed: zero fences means the step lost its command block, more than
    one means an assertion could measure a command nobody meant, and no
    `{branch}` placeholder means the test would silently address the literal
    placeholder path.
    """
    block = _section(skill_text, start_heading, end_heading)
    fences = _FENCE.findall(block)
    assert len(fences) == 1, (
        f"expected exactly one bash fence between {start_heading!r} and "
        f"{end_heading!r}; found {len(fences)}"
    )
    fence = fences[0]
    assert "{branch}" in fence, (
        f"the fence after {start_heading!r} no longer carries a {{branch}} "
        "placeholder, so this test cannot address the worktree the skill creates"
    )
    return fence.replace("{branch}", branch)


def fence_for(skill_text, branch=BRANCH):
    """Step 4's fence — the venv-link step."""
    return _fence_in(skill_text, "### Step 4:", "### Step 5:", branch)


def fence_for_step_3(skill_text, branch=BRANCH):
    """Step 3's fence — the worktree-creating half of the same injection surface."""
    return _fence_in(skill_text, "### Step 3:", "### Step 4:", branch)


def shipped_delimiter(skill_text):
    """The ONE quoted heredoc delimiter the fence reads the branch with."""
    delimiters = re.findall(r"<<\s*'([^']+)'", fence_for(skill_text))
    assert len(delimiters) == 1, (
        f"expected exactly one quoted heredoc delimiter; found {delimiters}"
    )
    return delimiters[0]


def _source_repo_root():
    """Repo root by a POSITIVE marker, or None when this is not a checkout.

    Deliberately not "did I find pyrightconfig.json?" — "I cannot find it" and
    "it is wrong" must never produce the same outcome, or a config deleted
    inside a checkout would skip instead of failing. Same marker and rationale
    as `test_local_lint_parity.py::_source_repo_root`.
    """
    for base in Path(__file__).resolve().parents:
        if (base / ".claude-plugin" / "marketplace.json").exists():
            return base
    return None


def _require_checkout():
    root = _source_repo_root()
    if root is None:
        pytest.skip(
            "SKIPPED, NOT PASSED — no .claude-plugin/marketplace.json above "
            f"{Path(__file__).resolve()}, so this is not a PACT source checkout. "
            "The expected case is an INSTALLED PLUGIN CACHE, which ships no "
            "repo-root pyrightconfig.json by design. This skip is NO "
            "INFORMATION about the config arm — never read it as a pass."
        )
    return root


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_git_config(tmp_path, monkeypatch):
    """Keep the developer's real global and system git config out of the fixture.

    This host's global excludesfile ignores `.gitignore` itself, but the hazard
    is general: any `core.excludesFile` can change whether the fixture's repo is
    clean, and the fixture would then measure the operator's machine rather than
    the shipped commands. `GIT_CONFIG_NOSYSTEM` closes the system-file channel.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.delenv("GIT_CONFIG_SYSTEM", raising=False)
    return fake_home


def git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    )


def make_repo(
    tmp_path,
    gitignore=".worktrees/\n",
    with_venv=True,
    tracked_venv_link=False,
    branch=BRANCH,
):
    """A committed repo, optionally with a `.venv`, plus a real worktree.

    The default `.venv` is created AFTER the commit on purpose: the point of the
    positive arms is an untracked link. `tracked_venv_link` builds the opposite
    state — a committed symlink that the worktree checks out — which is the
    state `check-ignore` reports as not ignored.
    """
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    (repo / ".gitignore").write_text(gitignore, encoding="utf-8")
    if tracked_venv_link:
        # A committed symlink to a committed directory: `-d` is true in the
        # main checkout, and the worktree checks the link out as a TRACKED path.
        (repo / "realvenv").mkdir()
        (repo / "realvenv" / "marker").write_text("real venv\n", encoding="utf-8")
        (repo / ".venv").symlink_to("realvenv", target_is_directory=True)
    git(repo, "add", "-A")
    git(repo, *GIT_ID, "commit", "-qm", "init")
    if with_venv and not tracked_venv_link:
        (repo / ".venv").mkdir()
        (repo / ".venv" / "marker").write_text("real venv\n", encoding="utf-8")
    worktree = repo / ".worktrees" / branch
    git(repo, "worktree", "add", "--quiet", str(worktree), "-b", branch)
    return repo, worktree


def make_empty_repo(tmp_path):
    """A repo with one commit and NO worktree — Step 3 creates the worktree."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, *GIT_ID, "commit", "-qm", "init")
    return repo


def make_submodule_checkout(tmp_path):
    """A superproject with a submodule that has its own `.venv` at its root."""
    superproject = tmp_path / "super"
    superproject.mkdir()
    subprocess.run(["git", "init", "--quiet", str(superproject)], check=True)
    (superproject / "README.md").write_text("superproject\n", encoding="utf-8")
    git(superproject, "add", "-A")
    git(superproject, *GIT_ID, "commit", "-qm", "init")

    upstream = tmp_path / "sub-upstream"
    upstream.mkdir()
    subprocess.run(["git", "init", "--quiet", str(upstream)], check=True)
    (upstream / "README.md").write_text("submodule\n", encoding="utf-8")
    git(upstream, "add", "-A")
    git(upstream, *GIT_ID, "commit", "-qm", "init")

    subprocess.run(
        [
            "git", "-C", str(superproject), "-c", "protocol.file.allow=always",
            "submodule", "add", "--quiet", str(upstream), "sub",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    git(superproject, *GIT_ID, "commit", "-qm", "add submodule")

    submodule = superproject / "sub"
    (submodule / ".venv").mkdir()
    (submodule / ".venv" / "marker").write_text("venv\n", encoding="utf-8")
    return superproject, submodule


def make_separate_git_dir_checkout(tmp_path):
    """A checkout whose git dir lives OUTSIDE its working tree."""
    checkout = tmp_path / "checkout"
    git_dir = tmp_path / "elsewhere" / "gitdir"
    git_dir.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "--quiet", "--separate-git-dir", str(git_dir), str(checkout)],
        check=True,
        capture_output=True,
        text=True,
    )
    (checkout / "README.md").write_text("checkout\n", encoding="utf-8")
    git(checkout, "add", "-A")
    git(checkout, *GIT_ID, "commit", "-qm", "init")
    (checkout / ".venv").mkdir()
    (checkout / ".venv" / "marker").write_text("venv\n", encoding="utf-8")
    return checkout, git_dir


def run_fence(fence, repo, pwd=None):
    """Run the fence with `repo` as cwd.

    `pwd` is passed only by the symlinked-root arm: the fence computes its root
    from `pwd`, and a shell reached through a symlink carries the LOGICAL path
    in `PWD`. Without it bash resolves to the physical path and the arm would
    not exercise the symlink at all.
    """
    env = {
        "PATH": SAFE_PATH,
        "HOME": os.environ["HOME"],
        "XDG_CONFIG_HOME": os.environ.get("XDG_CONFIG_HOME", ""),
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    if pwd is not None:
        env["PWD"] = str(pwd)
    return subprocess.run(
        [str(BASH), "-c", fence],
        cwd=str(repo),
        capture_output=True,
        text=True,
        env=env,
    )


def exclude_path(worktree):
    out = Path(git(worktree, "rev-parse", "--git-path", "info/exclude").stdout.strip())
    return out if out.is_absolute() else worktree / out


def exclude_venv_lines(worktree):
    path = exclude_path(worktree)
    if not path.is_file():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln == "/.venv"]


def porcelain(worktree):
    return git(worktree, "status", "--porcelain").stdout.strip()


def is_tracked(worktree):
    return (
        git(worktree, "ls-files", "--error-unmatch", ".venv", check=False).returncode
        == 0
    )


def check_ignore_path(worktree, path):
    """Diagnostic only: `--no-index` so a tracked path still names the rule."""
    return git(worktree, "check-ignore", "-v", "--no-index", path, check=False)


def check_ignore(worktree):
    return check_ignore_path(worktree, ".venv")


def link_state(fence, repo):
    """The positive properties plus the unlink warning, for the mutation matrix."""
    done = run_fence(fence, repo)
    worktree = repo / ".worktrees" / BRANCH
    return {
        "exists": (worktree / ".venv").is_symlink(),
        "clean": porcelain(worktree) == "",
        "not_tracked": not is_tracked(worktree),
        "unlinked": "the link was removed" in done.stdout,
    }


# ---------------------------------------------------------------------------
# The contract of the shipped fence
# ---------------------------------------------------------------------------


class TestFenceContract:
    def test_step_4_has_exactly_one_bash_fence(self):
        fence_for(SKILL_FILE.read_text(encoding="utf-8"))

    def test_the_branch_arrives_as_data_through_one_quoted_heredoc(self):
        """The branch is DATA. Exactly one QUOTED heredoc carries it, and it
        reaches no expansion — the point is not that `$BRANCH` appears."""
        text = SKILL_FILE.read_text(encoding="utf-8")
        fence = fence_for(text)
        assert "{branch}" not in fence
        delimiter = shipped_delimiter(text)
        assert (
            f"IFS= read -r BRANCH <<'{delimiter}'\n{BRANCH}\n{delimiter}" in fence
        ), "the branch is not read through the quoted heredoc"
        assert fence.count(BRANCH) == 1, (
            f"the branch text appears {fence.count(BRANCH)} times; it must "
            "appear only as the heredoc body, never inside an expansion"
        )


# ---------------------------------------------------------------------------
# The positive arm: the fence creates a link that is ignored and untracked
# ---------------------------------------------------------------------------


class TestFenceCreatesAnIgnoredLink:
    def test_link_exists(self, isolated_git_config, tmp_path):
        repo, worktree = make_repo(tmp_path)
        done = run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), repo)
        link = worktree / ".venv"
        assert link.is_symlink(), (
            "the fence did not create the .venv symlink. A clean porcelain tree "
            "is also produced when the link was never created, so this assertion "
            "must come before the cleanliness arms."
        )
        # Compare RESOLVED paths: a behaviour-preserving reword that changes the
        # spelling of the target must stay green.
        assert link.resolve() == (repo / ".venv").resolve()
        assert "Venv link: .venv -> " in done.stdout

    def test_link_is_not_swept_into_a_commit(self, isolated_git_config, tmp_path):
        repo, worktree = make_repo(tmp_path)
        run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), repo)
        report = check_ignore(worktree)
        assert porcelain(worktree) == "", (
            f"the worktree is dirty after the fence; it must contain no .venv "
            f"entry. check-ignore said: {report.stdout.strip() or report.stderr.strip()}"
        )

    def test_link_is_not_already_tracked(self, isolated_git_config, tmp_path):
        repo, worktree = make_repo(tmp_path)
        run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), repo)
        assert not is_tracked(worktree), (
            ".venv is tracked. Porcelain is blind to a tracked link and "
            "check-ignore reports it as not ignored, so this state must be "
            "surfaced rather than read as clean."
        )


class TestBranchIsInertText:
    """The branch name is DATA; a quoted heredoc keeps `$(...)` from running.

    The previous fence substituted the branch into a double-quoted assignment,
    where bash performs command substitution AS IT ASSIGNS. The marker file is
    the observable: if it exists, the name was evaluated as shell code. The
    benign arm runs beside it so this class cannot pass by breaking the link.
    """

    def test_ifs_payload_is_literal_data_and_never_executes(
        self, isolated_git_config, tmp_path
    ):
        """`$IFS` avoids the space that `check-ref-format` would reject.

        That matters: with a space the name is rejected for the wrong reason
        and the arm never measures inertness. This payload PASSES name
        validation, so the fence carries it through as a literal directory
        name — and the marker must still be absent.
        """
        repo, _ = make_repo(tmp_path)
        marker = tmp_path / "PWNED"
        branch = f"wt$(touch$IFS{marker})"
        done = run_fence(
            fence_for(SKILL_FILE.read_text(encoding="utf-8"), branch=branch), repo
        )
        assert not marker.exists(), (
            "the branch name was evaluated as shell code — a command "
            f"substitution in it created {marker}"
        )
        assert done.returncode == 0, f"the fence failed: {done.stderr.strip()}"
        assert branch in done.stdout, (
            "the branch text did not survive as literal data in the report; "
            f"stdout={done.stdout.strip()!r}"
        )
        assert not os.path.lexists(repo / ".worktrees" / branch / ".venv")

    def test_an_invalid_branch_is_rejected_before_a_path_is_used(
        self, isolated_git_config, tmp_path
    ):
        repo, _ = make_repo(tmp_path)
        branch = "wt$(touch /tmp/rejected)"
        done = run_fence(
            fence_for(SKILL_FILE.read_text(encoding="utf-8"), branch=branch), repo
        )
        assert "Venv link: skipped (invalid branch name)" in done.stdout, (
            f"the invalid name was not rejected: {done.stdout.strip()!r}"
        )
        assert not os.path.lexists(repo / ".worktrees" / branch / ".venv")

    def test_a_benign_branch_still_gets_its_link(self, isolated_git_config, tmp_path):
        repo, worktree = make_repo(tmp_path)
        done = run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), repo)
        assert "Venv link: .venv -> " in done.stdout
        assert (worktree / ".venv").is_symlink()


class TestStep3BranchIsInertText:
    """Step 3 is the OTHER site of the same injection, and the older one.

    It creates the worktree, so the branch reaches `git worktree add` — the
    command that pre-existed the Step 4 link. A `$()` payload that passes
    ref-format must land as a literal directory and branch name, with no
    execution; the benign arm proves the feature still works.
    """

    def test_payload_does_not_execute_and_lands_literally(
        self, isolated_git_config, tmp_path
    ):
        repo = make_empty_repo(tmp_path)
        marker = tmp_path / "PWNED"
        branch = f"wt$(touch$IFS{marker})"
        done = run_fence(
            fence_for_step_3(SKILL_FILE.read_text(encoding="utf-8"), branch=branch),
            repo,
        )
        assert not marker.exists(), (
            "Step 3 evaluated the branch as shell code — the substitution "
            f"created {marker}"
        )
        assert done.returncode == 0, f"Step 3 failed: {done.stderr.strip()}"
        worktree = repo / ".worktrees" / branch
        assert (worktree / ".git").is_file(), (
            "the literal branch name did not create a worktree"
        )
        assert (
            git(repo, "rev-parse", "--verify", f"refs/heads/{branch}", check=False).returncode
            == 0
        ), "the branch was not created under its literal name"

    def test_benign_branch_creates_the_worktree_and_branch(
        self, isolated_git_config, tmp_path
    ):
        repo = make_empty_repo(tmp_path)
        done = run_fence(
            fence_for_step_3(SKILL_FILE.read_text(encoding="utf-8")), repo
        )
        assert done.returncode == 0, f"Step 3 failed: {done.stderr.strip()}"
        assert (repo / ".worktrees" / BRANCH / ".git").is_file()
        assert (
            git(repo, "rev-parse", "--verify", f"refs/heads/{BRANCH}", check=False).returncode
            == 0
        )


class TestDelimiterCannotBeForged:
    """The delimiter must be a string NO legal branch may equal.

    Pinning a hardcoded spelling (`PACT_BRANCH`) would stay green if a
    maintainer "tidied" the odd space delimiter to a hyphen form — and then a
    legitimate branch named `PACT-BRANCH` would terminate the heredoc and be
    falsely rejected. The property below reds on any such tidy, and the
    behavioural arm pins the identifier-shaped cousin end to end.
    """

    def test_the_shipped_delimiter_is_not_a_legal_branch_name(self, tmp_path):
        delimiter = shipped_delimiter(SKILL_FILE.read_text(encoding="utf-8"))
        done = subprocess.run(
            ["git", "check-ref-format", "--branch", delimiter],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert done.returncode != 0, (
            f"the heredoc delimiter {delimiter!r} is a LEGAL branch name — a "
            "ref with that exact value would terminate the heredoc and be "
            "falsely rejected. The delimiter must be unforgeable by any valid "
            "ref."
        )
        assert "not a valid branch name" in done.stderr, (
            f"the delimiter was rejected for the wrong reason: {done.stderr!r}"
        )

    def test_a_ref_shaped_like_an_identifier_cousin_links_normally(
        self, isolated_git_config, tmp_path
    ):
        repo, worktree = make_repo(tmp_path, branch="PACT-BRANCH")
        done = run_fence(
            fence_for(SKILL_FILE.read_text(encoding="utf-8"), branch="PACT-BRANCH"),
            repo,
        )
        assert "invalid branch name" not in done.stdout, (
            f"a valid ref named PACT-BRANCH was rejected: {done.stdout.strip()!r}"
        )
        assert "Venv link: .venv -> " in done.stdout
        assert (worktree / ".venv").is_symlink()


class TestMultiLineValueIsTheInstructionsJob:
    """The heredoc CANNOT defend against a multi-line value.

    Measured: substituting `evil\\nPACT BRANCH\\ntouch <marker>` terminates the
    heredoc at the injected delimiter line, `read` takes `evil`, and the
    leftover `touch` line runs as a top-level command — the marker IS created.
    The defence is the skill's single-line rule, applied by the agent BEFORE
    substituting. The two arms below pin each fact separately so neither is
    mistaken for the other.
    """

    def test_the_single_line_rule_is_stated_in_both_steps(self):
        text = SKILL_FILE.read_text(encoding="utf-8")
        for start, end in (
            ("### Step 3:", "### Step 4:"),
            ("### Step 4:", "### Step 5:"),
        ):
            block = _section(text, start, end)
            assert "must be a single line" in block, (
                f"{start} lost the single-line rule. The heredoc alone cannot "
                "stop a multi-line value; this sentence is the only defence."
            )

    def test_the_fence_alone_does_not_stop_a_multiline_value(
        self, isolated_git_config, tmp_path
    ):
        """CHARACTERIZATION, not a safety claim: this passes while the marker
        IS created. If the fence ever validates single-line input, replace it
        with a positive 'no marker' assertion."""
        repo, _ = make_repo(tmp_path)
        marker = tmp_path / "PWNED"
        value = f"evil\nPACT BRANCH\ntouch {marker}"
        run_fence(
            fence_for(SKILL_FILE.read_text(encoding="utf-8"), branch=value), repo
        )
        assert marker.exists(), (
            "the fence unexpectedly stopped a multi-line value — if it now "
            "validates single-line input, this characterization should become "
            "a positive assertion"
        )


# ---------------------------------------------------------------------------
# Counter-arms: the assertions can fail, and the fence's rule is what works
# ---------------------------------------------------------------------------


class TestCounterArms:
    def test_trailing_slash_rule_does_not_ignore_a_symlink(
        self, isolated_git_config, tmp_path
    ):
        """`.venv/` matches directories only, so the same link is visible."""
        repo, worktree = make_repo(tmp_path, gitignore=".worktrees/\n.venv/\n")
        (worktree / ".venv").symlink_to(repo / ".venv", target_is_directory=True)
        assert porcelain(worktree) == "?? .venv", (
            "the counter-arm did not show the symlink as untracked, so the "
            "positive arms cannot be shown to discriminate"
        )
        report = check_ignore(worktree)  # diagnostic: which rule (not) matched
        assert report.returncode == 1, (
            "a trailing-slash `.venv/` rule unexpectedly matched a symlink — "
            f"the whole premise of this fix is wrong. check-ignore: {report.stdout}"
        )

    def test_removing_the_fence_rule_makes_the_link_visible(
        self, isolated_git_config, tmp_path
    ):
        """The rule the fence writes is the thing doing the work."""
        repo, worktree = make_repo(tmp_path)
        run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), repo)
        path = exclude_path(worktree)
        kept = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln != "/.venv"]
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        assert porcelain(worktree) == "?? .venv", (
            "removing the fence's info/exclude line left the tree clean, so "
            "something other than that line is ignoring the link and the "
            "positive arms prove less than they claim"
        )


class TestAnchoredRule:
    """The shipped rule is `/.venv`: the repo-root entry and nothing deeper.

    An unanchored rule also matches `sub/.venv`, where the fence never creates
    a link; the nesting behaviour is the property, not the spelling.
    """

    def test_root_level_venv_is_ignored(self, isolated_git_config, tmp_path):
        repo, worktree = make_repo(tmp_path)
        run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), repo)
        assert check_ignore_path(worktree, ".venv").returncode == 0
        assert porcelain(worktree) == ""

    def test_nested_venv_is_not_matched_by_the_rule(
        self, isolated_git_config, tmp_path
    ):
        repo, worktree = make_repo(tmp_path)
        run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), repo)
        sub = worktree / "sub"
        sub.mkdir()
        (sub / ".venv").symlink_to(repo / ".venv", target_is_directory=True)
        report = check_ignore_path(worktree, "sub/.venv")
        assert report.returncode == 1, (
            "the rule matched a nested .venv, so it is not anchored to the "
            f"repo root. check-ignore: {report.stdout.strip()}"
        )
        # Git collapses an untracked directory to `?? sub/`; either spelling
        # proves the nested entry is visible to git rather than ignored.
        assert "?? sub" in porcelain(worktree)


# ---------------------------------------------------------------------------
# Idempotency and the exclude-file newline guard
# ---------------------------------------------------------------------------


class TestRerunIsIdempotent:
    def test_second_run_leaves_one_rule_and_the_same_link(
        self, isolated_git_config, tmp_path
    ):
        repo, worktree = make_repo(tmp_path)
        fence = fence_for(SKILL_FILE.read_text(encoding="utf-8"))
        run_fence(fence, repo)
        first_target = os.readlink(worktree / ".venv")
        second = run_fence(fence, repo)
        assert "Venv link: .venv already exists in the worktree" in second.stdout, (
            f"the second run did not report the existing link: {second.stdout!r}"
        )
        assert len(exclude_venv_lines(worktree)) == 1, (
            f"expected exactly one .venv rule after two runs; found "
            f"{exclude_venv_lines(worktree)}"
        )
        assert os.readlink(worktree / ".venv") == first_target
        assert not (worktree / ".venv" / ".venv").exists(), (
            "a second `ln` nested a link inside the existing one — the fence "
            "must guard the existing path, not use `ln -sfn`"
        )


class TestExcludeNewlineGuard:
    def test_an_existing_final_line_without_a_newline_is_preserved(
        self, isolated_git_config, tmp_path
    ):
        """Appending must not fuse the new rule onto the previous last line."""
        repo, worktree = make_repo(tmp_path)
        path = exclude_path(worktree)
        path.write_text("keep-me", encoding="utf-8")  # deliberately no newline
        run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), repo)
        lines = path.read_text(encoding="utf-8").splitlines()
        assert "keep-me" in lines, (
            f"the existing final line was destroyed by the append: {lines!r}"
        )
        assert lines[-1] == "/.venv", f"the rule is not on its own line: {lines!r}"


# ---------------------------------------------------------------------------
# Edge cases and guards the skill promises
# ---------------------------------------------------------------------------


class TestPathGuard:
    def test_skips_when_the_computed_path_is_not_a_git_worktree(
        self, isolated_git_config, tmp_path
    ):
        repo, _ = make_repo(tmp_path)
        missing = repo / ".worktrees" / "missing" / "branch"
        assert not os.path.lexists(missing)

        done = run_fence(
            fence_for(SKILL_FILE.read_text(encoding="utf-8"), branch="missing/branch"),
            repo,
        )
        assert done.returncode == 0, (
            f"the fence must skip and continue, not fail: {done.stderr.strip()}"
        )
        assert "Venv link: skipped (no git worktree at " in done.stdout, (
            f"expected the path-guard skip; got {done.stdout.strip()!r}"
        )
        assert not os.path.lexists(missing), (
            "something was created at a path that is not a git worktree"
        )
        assert exclude_venv_lines(repo) == []


class TestPlainDirectoryIsNotAWorktree:
    """A directory under `.worktrees/` is inside the repo's work tree.

    `git -C <dir> rev-parse --is-inside-work-tree` therefore reports TRUE for a
    plain directory, so that predicate links into a directory that is not a
    worktree. The `.git`-FILE predicate is the one that distinguishes a real
    worktree. The skip must also be silent on stderr: shell noise on the normal
    guard path is indistinguishable from a failure to a reader.
    """

    def test_skips_with_no_link_no_rule_and_no_stderr(
        self, isolated_git_config, tmp_path
    ):
        repo, _ = make_repo(tmp_path)
        plain = repo / ".worktrees" / "feature" / "plain"
        plain.mkdir(parents=True)
        (plain / "obstruction").write_text("x\n", encoding="utf-8")

        done = run_fence(
            fence_for(SKILL_FILE.read_text(encoding="utf-8"), branch="feature/plain"),
            repo,
        )
        assert done.returncode == 0, f"the guard must not fail: {done.stderr.strip()}"
        assert "Venv link: skipped (no git worktree at " in done.stdout, (
            f"a plain directory was not rejected; stdout={done.stdout.strip()!r}"
        )
        assert not os.path.lexists(plain / ".venv")
        assert exclude_venv_lines(repo) == [], (
            "the rule was written even though the guard rejected the path"
        )
        assert done.stderr == "", (
            f"the skip path must be silent on stderr; got {done.stderr!r}"
        )


class TestSymlinkedRootIsAValidWorktree:
    """A worktree reached through a symlinked root is still a valid worktree.

    `git rev-parse --show-toplevel` reports the PHYSICAL path, so a predicate
    that compares toplevel to the computed path rejects this state. Under the
    hybrid the fence resolves its root to the PHYSICAL path; `PWD` is still
    passed so the invocation carries the logical spelling a real shell would
    have, proving the physical resolution accepts it. The assertions below are
    unchanged and must keep passing.
    """

    def test_link_is_created_through_the_symlinked_root(
        self, isolated_git_config, tmp_path
    ):
        repo, worktree = make_repo(tmp_path)
        linked = tmp_path / "linked-root"
        linked.symlink_to(repo, target_is_directory=True)

        done = run_fence(
            fence_for(SKILL_FILE.read_text(encoding="utf-8")), linked, pwd=linked
        )
        assert "skipped" not in done.stdout, (
            f"a valid worktree behind a symlinked root was rejected: {done.stdout.strip()!r}"
        )
        assert "Venv link: .venv -> " in done.stdout
        assert (linked / ".worktrees" / BRANCH / ".venv").is_symlink(), (
            "the link was not created through the symlinked root"
        )
        assert (worktree / ".venv").is_symlink(), (
            "the symlinked-root link does not reach the real worktree"
        )


class TestContainment:
    """A worktree path that resolves outside `.worktrees/` is not ours.

    A planted symlink there can point at another repository's real worktree
    (its `.git` is a FILE, so the worktree guard passes). Resolution is what
    catches it: the physical path must still sit under `$REPO_ROOT/.worktrees`.
    The branch name is check-ref-format-valid, so this measures containment
    rather than name validation.
    """

    def test_a_branch_resolving_outside_worktrees_is_skipped(
        self, isolated_git_config, tmp_path
    ):
        repo, _ = make_repo(tmp_path)
        other = tmp_path / "other"
        other.mkdir()
        subprocess.run(["git", "init", "--quiet", str(other)], check=True)
        (other / "README.md").write_text("other\n", encoding="utf-8")
        git(other, "add", "-A")
        git(other, *GIT_ID, "commit", "-qm", "init")
        other_wt = tmp_path / "other-wt"
        git(other, "worktree", "add", "--quiet", str(other_wt), "-b", "other-branch")

        evil = repo / ".worktrees" / "evil"
        evil.symlink_to(other_wt, target_is_directory=True)
        assert (evil / ".git").is_file(), "the planted path is not a real worktree"

        done = run_fence(
            fence_for(SKILL_FILE.read_text(encoding="utf-8"), branch="evil"), repo
        )
        assert done.returncode == 0, f"the fence failed: {done.stderr.strip()}"
        assert "Venv link: skipped (worktree path is not under .worktrees/)" in done.stdout, (
            f"containment did not fire; stdout={done.stdout.strip()!r}"
        )
        assert not os.path.lexists(other_wt / ".venv"), (
            "a link was created in another repository's worktree"
        )


class TestRootResolutionOutsideASimpleCheckout:
    """The root is the CHECKOUT, not the git dir's parent.

    In a submodule `--git-common-dir` is `<super>/.git/modules/<name>`; with
    `--separate-git-dir` it is an unrelated directory. Taking the git dir's
    parent therefore writes `.worktrees/` INSIDE `<super>/.git/modules` or
    beside the separate git dir, and the real checkout gets nothing.
    """

    @staticmethod
    def _assert_locus(skill_text, root, old_path):
        step3 = run_fence(fence_for_step_3(skill_text), root)
        assert step3.returncode == 0, f"Step 3 failed: {step3.stderr.strip()}"
        assert (root / ".worktrees" / BRANCH / ".git").is_file(), (
            "the worktree was not created under the checkout root"
        )
        assert not os.path.lexists(old_path), (
            f"the pre-fix locus {old_path} was written to"
        )

        step4 = run_fence(fence_for(skill_text), root)
        assert step4.returncode == 0, f"Step 4 failed: {step4.stderr.strip()}"
        assert "Venv link: .venv -> " in step4.stdout, (
            f"the link was not created; stdout={step4.stdout.strip()!r}"
        )
        assert (root / ".worktrees" / BRANCH / ".venv").is_symlink()

    def test_submodule_root_is_the_submodule(self, isolated_git_config, tmp_path):
        superproject, submodule = make_submodule_checkout(tmp_path)
        self._assert_locus(
            SKILL_FILE.read_text(encoding="utf-8"),
            submodule,
            superproject / ".git" / "modules" / ".worktrees",
        )

    def test_separate_git_dir_root_is_the_checkout(
        self, isolated_git_config, tmp_path
    ):
        checkout, git_dir = make_separate_git_dir_checkout(tmp_path)
        self._assert_locus(
            SKILL_FILE.read_text(encoding="utf-8"),
            checkout,
            git_dir.parent / ".worktrees",
        )


class TestInvocationLocus:
    """A linked worktree invocation still targets the MAIN checkout.

    The hybrid must not replace the common-dir locus with `--show-toplevel`
    everywhere: run from inside a linked worktree, `--show-toplevel` is the
    WORKTREE, and treating it as the root would nest `.worktrees/` inside the
    worktree and link the wrong `.venv`.
    """

    def test_running_from_inside_a_linked_worktree_targets_the_main_root(
        self, isolated_git_config, tmp_path
    ):
        repo, worktree = make_repo(tmp_path)
        done = run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), worktree)
        assert done.returncode == 0, f"the fence failed: {done.stderr.strip()}"
        assert "Venv link: .venv -> " in done.stdout, (
            f"the link was not created; stdout={done.stdout.strip()!r}"
        )
        assert (worktree / ".venv").is_symlink()
        assert not (worktree / ".worktrees").exists(), (
            "the fence treated the linked worktree as the root and nested "
            ".worktrees/ inside it"
        )


class TestNoVenvInMainCheckout:
    def test_skips_without_a_link_or_a_rule(self, isolated_git_config, tmp_path):
        repo, worktree = make_repo(tmp_path, with_venv=False)
        done = run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), repo)
        assert "Venv link: skipped (no .venv at " in done.stdout
        # lexists, not exists: a dangling symlink would read as absent to
        # `exists()` and this arm would then pass over the bug it names.
        assert not os.path.lexists(worktree / ".venv")
        assert exclude_venv_lines(worktree) == []


class TestExistingRealDirectoryIsLeftAlone:
    def test_reports_and_does_not_replace(self, isolated_git_config, tmp_path):
        repo, worktree = make_repo(tmp_path)
        real = worktree / ".venv"
        real.mkdir()
        (real / "marker").write_text("local venv\n", encoding="utf-8")
        done = run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), repo)
        assert "Venv link: .venv already exists in the worktree" in done.stdout
        assert real.is_dir() and not real.is_symlink()
        assert (real / "marker").read_text(encoding="utf-8") == "local venv\n"
        # The fence writes the ignore rule BEFORE the guard, so the rule is
        # present even though the path was left alone. Asserting its absence
        # would encode a behaviour the skill deliberately does not have.
        assert len(exclude_venv_lines(worktree)) == 1


class TestDanglingLinkIsLeftAlone:
    def test_dangling_symlink_is_treated_as_existing(
        self, isolated_git_config, tmp_path
    ):
        """`[ -e ]` is false for a dangling link; the `-L` leg must carry it."""
        repo, worktree = make_repo(tmp_path)
        dangling = worktree / ".venv"
        dangling.symlink_to("/nonexistent/target")
        done = run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), repo)
        assert "Venv link: .venv already exists in the worktree" in done.stdout, (
            "a dangling .venv was not treated as existing, so the fence will "
            f"try to link over it. stdout={done.stdout.strip()!r}"
        )
        assert os.readlink(dangling) == "/nonexistent/target"


class TestTrackedLinkIsReported:
    def test_tracked_link_gets_the_not_ignored_warning(
        self, isolated_git_config, tmp_path
    ):
        """A tracked link is invisible to porcelain; the skill must warn loudly."""
        repo, worktree = make_repo(tmp_path, tracked_venv_link=True)
        assert is_tracked(worktree)
        done = run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), repo)
        assert "Venv link: .venv already exists in the worktree" in done.stdout
        assert "WARNING: .venv is NOT ignored in this worktree" in done.stdout, (
            "the check-ignore warning did not fire for a tracked link: deleting "
            f"that line would leave this state silently 'clean'. stdout={done.stdout.strip()!r}"
        )


class TestUnlinkWhenNotIgnored:
    """A tracked `!.venv` negation outranks `info/exclude`.

    The fence creates the link, discovers it is NOT ignored, and must remove
    the link IT created. Leaving an unignored symlink behind is the exact harm
    the ignore rule exists to prevent, so the link's absence — and a clean
    porcelain tree — are the assertions.
    """

    def test_the_created_link_is_removed_and_warned_about(
        self, isolated_git_config, tmp_path
    ):
        repo, worktree = make_repo(tmp_path, gitignore=".worktrees/\n!.venv\n")
        done = run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), repo)
        assert not os.path.lexists(worktree / ".venv"), (
            "the fence left an unignored .venv symlink behind; git add -A could "
            "commit it and worktree removal would refuse without --force"
        )
        assert "the link was removed" in done.stdout, (
            f"expected the unlink warning; got {done.stdout.strip()!r}"
        )
        assert porcelain(worktree) == ""


# ---------------------------------------------------------------------------
# The cleanup contract and the write-failure branch
# ---------------------------------------------------------------------------


class TestRemovalSafety:
    """The ignore rule is what makes cleanup work without `--force`.

    An unignored link is an untracked file, so `git worktree remove` refuses.
    That is the failure mode this arm guards, and it also pins the safety
    property: removal unlinks the symlink rather than following it, so the
    main checkout's real `.venv` survives.
    """

    def test_removal_succeeds_without_force_and_leaves_the_target_intact(
        self, isolated_git_config, tmp_path
    ):
        repo, worktree = make_repo(tmp_path)
        run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), repo)
        marker = repo / ".venv" / "marker"
        assert marker.is_file()

        done = git(repo, "worktree", "remove", str(worktree), check=False)
        assert done.returncode == 0, (
            "git worktree remove refused without --force, so the ignored link "
            "no longer licenses cleanup. stderr="
            f"{done.stderr.strip()}"
        )
        assert not worktree.exists()
        assert marker.is_file() and marker.read_text(encoding="utf-8") == "real venv\n", (
            "the main checkout's .venv did not survive worktree removal — "
            "removal followed the symlink instead of unlinking it"
        )


class TestExcludeWriteFailureSkipsTheLink:
    """The write-failure branch, forced portably and without chmod.

    `<common-git-dir>/info/exclude` is replaced by a DIRECTORY while the
    worktree stays real. `grep` and `printf` both fail, the write branch warns,
    and a stray `ln` would otherwise succeed — so the no-link assertion is what
    makes this arm discriminate. A nonexistent worktree would NOT do: the path
    guard preempts it, and no link could have existed there anyway.
    """

    def test_warning_and_no_link_but_only_after_the_real_worktree_guard(
        self, isolated_git_config, tmp_path
    ):
        repo, worktree = make_repo(tmp_path)
        exclude = exclude_path(worktree)
        exclude.unlink()
        exclude.mkdir()

        done = run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), repo)
        assert done.returncode == 0, (
            f"the fence must warn and continue, not fail: {done.stderr.strip()}"
        )
        assert "WARNING: could not write " in done.stdout, (
            f"expected the exclude-write warning; got {done.stdout.strip()!r}"
        )
        assert not os.path.lexists(worktree / ".venv"), (
            "a link was created even though the ignore rule could not be written"
        )
        assert done.stderr == "", (
            "the write-failure path must be silent on stderr after the redirect "
            f"reordering; got {done.stderr!r}"
        )


class TestUnresolvedExcludePathNamesTheCause:
    """The EMPTY-`EXCLUDE` branch: the moved-repo state.

    Every ordinary fixture yields a resolveable exclude path, so the branch
    where `git -C "$WORKTREE" rev-parse --git-path` FAILS was never reached by
    the suite. Moving the repo breaks the worktree's absolute `gitdir` pointer,
    which is exactly that state. The message must name a cause instead of
    printing a blank path (which produced a double space).
    """

    def test_moved_repo_warns_with_a_named_cause_and_keeps_the_link(
        self, isolated_git_config, tmp_path
    ):
        repo, worktree = make_repo(tmp_path)
        fence = fence_for(SKILL_FILE.read_text(encoding="utf-8"))
        run_fence(fence, repo)  # first run creates the link and the rule
        first_target = os.readlink(worktree / ".venv")

        moved = tmp_path / "moved-repo"
        repo.rename(moved)  # the repo and its .worktrees move together
        moved_worktree = moved / ".worktrees" / BRANCH

        done = run_fence(fence, moved)
        assert done.returncode == 0, f"the fence failed: {done.stderr.strip()}"
        assert "could not resolve the exclude path" in done.stdout, (
            "the warning did not name the cause; it must not print a blank "
            f"path. stdout={done.stdout.strip()!r}"
        )
        assert "  " not in done.stdout, (
            f"the warning contains a double space (blank path printed): {done.stdout!r}"
        )
        assert os.readlink(moved_worktree / ".venv") == first_target, (
            "the unresolved-exclude run changed a link it did not create"
        )
        assert done.stderr == "", (
            f"the unresolved-exclude branch must be silent on stderr; got {done.stderr!r}"
        )


# ---------------------------------------------------------------------------
# The pyright config contract
# ---------------------------------------------------------------------------

_CONFIGS = [
    pytest.param("pyrightconfig.json", ".", id="repo-root"),
    pytest.param("pact-plugin/pyrightconfig.json", "..", id="plugin"),
]


class TestPyrightConfigPointsAtTheReposVenv:
    """Both configs must address the repo-root `.venv`, not a relative guess.

    EXISTENCE IS NOT ASSERTED, deliberately: CI runs from a fresh checkout with
    no `.venv`, so requiring one here would red on every CI run while proving
    nothing about the config. The property that matters — the constructed path
    IS the repo-root `.venv`, from both config depths — is path arithmetic and
    holds on every runner. The fence arms and the recorded probe cover the
    live-link half.

    OUTSIDE A SOURCE CHECKOUT THIS ARM SKIPS. An installed plugin cache ships
    no repo-root `pyrightconfig.json`, so the marker check comes first and the
    skip says "SKIPPED, NOT PASSED".
    """

    @pytest.mark.parametrize("rel_path,venv_path", _CONFIGS)
    def test_config_addresses_the_repo_root_venv(self, rel_path, venv_path):
        root = _require_checkout()
        config_path = root / rel_path
        assert config_path.is_file(), (
            f"source checkout detected at {root} but {rel_path} is missing. "
            "This is a FAILURE, not a skip: inside a checkout both configs "
            "are required."
        )
        config = json.loads(config_path.read_text(encoding="utf-8"))
        assert config["venv"] == ".venv", (
            f"{rel_path}: venv key changed, so pyright will not look where the "
            f"skill puts the link"
        )
        assert config["venvPath"] == venv_path, (
            f"{rel_path}: venvPath must be {venv_path!r} from this config's "
            f"own directory"
        )
        anchor = (config_path.parent / config["venvPath"]).resolve()
        assert anchor.is_dir(), f"{rel_path}: venvPath anchor is not a directory"
        assert (anchor / config["venv"]).resolve() == (root / ".venv").resolve(), (
            f"{rel_path}: the constructed venv path is not the repo-root .venv"
        )


# ---------------------------------------------------------------------------
# The mutation matrix: which arm reds on which regression
# ---------------------------------------------------------------------------

_LINK_COMMAND = 'ln -s "$REPO_ROOT/.venv" "$WORKTREE/.venv"'
_RULE_LITERAL = "'/.venv'"


class TestMutationDiscrimination:
    """Mutate the EXTRACTED fence in memory; SKILL.md is never touched.

    Each mutation must apply, or the arm would pass over a reword it no longer
    matches. The expected matrices are the docstring's claim, measured.

    THE TRAILING-SLASH REVERT IS NOW SELF-HEALING: the fence creates the link,
    finds it NOT ignored, and removes it. So that mutation no longer leaves a
    dirty tree — it leaves NO link plus the unlink warning, which is how the
    matrix distinguishes it from a dropped link step.
    """

    @staticmethod
    def _mutate(fence, old, new):
        mutated = fence.replace(old, new)
        assert mutated != fence, (
            f"the mutation target {old!r} is gone from the fence — a reword "
            "means this meta-test no longer measures the regression it names"
        )
        return mutated

    def test_shipped_fence_is_green_on_all_three(self, isolated_git_config, tmp_path):
        repo, _ = make_repo(tmp_path)
        assert link_state(fence_for(SKILL_FILE.read_text(encoding="utf-8")), repo) == {
            "exists": True,
            "clean": True,
            "not_tracked": True,
            "unlinked": False,
        }

    def test_dropped_link_reds_only_the_existence_arm(
        self, isolated_git_config, tmp_path
    ):
        fence = fence_for(SKILL_FILE.read_text(encoding="utf-8"))
        repo, _ = make_repo(tmp_path)
        state = link_state(self._mutate(fence, _LINK_COMMAND, "false"), repo)
        assert state == {
            "exists": False,
            "clean": True,
            "not_tracked": True,
            "unlinked": False,
        }, (
            "a dropped link step must red the existence arm ONLY; a dirty tree "
            f"or an unlink warning here would mean the matrix is wrong: {state}"
        )

    def test_trailing_slash_revert_creates_then_removes_the_link(
        self, isolated_git_config, tmp_path
    ):
        """Under the anchored rule, `.venv/` means the link is not ignored.

        The fence creates it, discovers that, and removes it. The subject is
        the create-then-remove sequence and the removal warning — NOT a
        surviving dirty tree, which the older expectation wrongly asserted.
        """
        fence = fence_for(SKILL_FILE.read_text(encoding="utf-8"))
        repo, worktree = make_repo(tmp_path)
        done = run_fence(self._mutate(fence, _RULE_LITERAL, "'.venv/'"), repo)
        assert "Venv link: .venv -> " in done.stdout, (
            f"the link was not created before the ignore check: {done.stdout.strip()!r}"
        )
        assert "the link was removed" in done.stdout, (
            f"the unlink warning did not fire: {done.stdout.strip()!r}"
        )
        assert not os.path.lexists(worktree / ".venv"), (
            "an unignored symlink was left behind"
        )
        assert porcelain(worktree) == ""
