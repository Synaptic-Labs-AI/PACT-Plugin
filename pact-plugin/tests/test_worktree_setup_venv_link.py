"""Step 4's venv link: the state on disk, not the words in the skill.

`skills/worktree-setup/SKILL.md` Step 4 is executed, not read. A phrase pin
over that fence disarms silently when the prose is correctly rewritten, so the
arms here extract the one bash fence from Step 4 and RUN it, unmodified apart
from the `{branch}` substitution the step itself specifies, against a
throwaway `git init` repo with a real worktree in it.

WHY THREE ASSERTIONS AND NOT ONE. Each is blind to what the others catch:

- `test -L .venv` proves the link EXISTS. A clean `git status --porcelain` is
  produced by "linked and ignored" AND by "never linked" alike, so the check
  that exists to catch a dropped link step passes in exactly the regression it
  is meant to catch unless existence is asserted first.
- `git status --porcelain` proves the link would not be swept into a commit.
- `git ls-files --error-unmatch .venv` proves the link is not already TRACKED.
  Porcelain is blind to a tracked link, while `git check-ignore` is index-aware
  and reports a tracked path as NOT ignored even when the rule matches. So
  neither of the other two can see that state.

`git check-ignore` is diagnostic here: its output names the matching rule and
is printed on failure, but it is never the primary assertion. The skill's own
runtime `check-ignore -q` is a different use — there, rc 1 is the intended loud
warning, including for an already-tracked link.

THE COUNTER-ARM IS THE DISCRIMINATION PROOF. A tracked `.venv/` rule (trailing
slash) does not match a symlink, so the same link shows as `?? .venv`. Without
that arm a green suite would say nothing about whether the assertions can fail
at all.

BINARY AND ENVIRONMENT, stated because it bounds what these arms prove: the
fence runs under `/bin/bash` with `PATH` pinned to `/usr/bin:/bin`, matching
the shipped-command house pattern. Git's global config is isolated (fake HOME,
fake XDG_CONFIG_HOME, `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` unset) because a
developer's global excludesfile can change what this fixture measures — this
host's ignores `.gitignore` itself, so an unisolated run would test a different
tree locally than on CI. No remote and no worktree of the real repo is ever
touched.
"""

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


def step_4(skill_text):
    start = skill_text.find("### Step 4:")
    assert start != -1, "no '### Step 4:' heading in worktree-setup SKILL.md"
    block = skill_text[start:]
    end = block.find("### Step 5:")
    assert end != -1, "Step 4 does not run into Step 5 — the file changed shape"
    return block[:end]


def fence_for(skill_text, branch=BRANCH):
    """Step 4's single bash fence with `{branch}` substituted.

    Fails closed: zero fences means the step lost its command block, more than
    one means an assertion could measure a command nobody meant, and no
    `{branch}` placeholder means the test would silently address the literal
    placeholder path.
    """
    block = step_4(skill_text)
    fences = _FENCE.findall(block)
    assert len(fences) == 1, (
        f"expected exactly one bash fence in a {len(block)}-char Step 4; "
        f"found {len(fences)}"
    )
    fence = fences[0]
    assert "{branch}" in fence, (
        "Step 4's fence no longer carries a {branch} placeholder, so this "
        "test cannot address the worktree the skill creates"
    )
    return fence.replace("{branch}", branch)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_git_config(tmp_path, monkeypatch):
    """Keep the developer's real global git config out of the fixture.

    This host's global excludesfile ignores `.gitignore` itself, but the
    hazard is general: any `core.excludesFile` can change whether the fixture's
    repo is clean, and the fixture would then measure the operator's machine
    rather than the shipped commands.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.delenv("GIT_CONFIG_SYSTEM", raising=False)
    return fake_home


def git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    )


def make_repo(tmp_path, gitignore=".worktrees/\n", with_venv=True):
    """A committed repo, optionally with a real `.venv`, plus a real worktree.

    `.venv` is created AFTER the commit on purpose: the point of the arms is an
    untracked link, and a tracked one is a separate state the test surfaces.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    (repo / ".gitignore").write_text(gitignore, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, *GIT_ID, "commit", "-qm", "init")
    if with_venv:
        (repo / ".venv").mkdir()
        (repo / ".venv" / "marker").write_text("real venv\n", encoding="utf-8")
    worktree = repo / ".worktrees" / BRANCH
    git(repo, "worktree", "add", "--quiet", str(worktree), "-b", BRANCH)
    return repo, worktree


def run_fence(fence, repo):
    return subprocess.run(
        [str(BASH), "-c", fence],
        cwd=str(repo),
        capture_output=True,
        text=True,
        env={
            "PATH": SAFE_PATH,
            "HOME": os.environ["HOME"],
            "XDG_CONFIG_HOME": os.environ.get("XDG_CONFIG_HOME", ""),
        },
    )


def exclude_path(worktree):
    out = Path(git(worktree, "rev-parse", "--git-path", "info/exclude").stdout.strip())
    return out if out.is_absolute() else worktree / out


def exclude_venv_lines(worktree):
    path = exclude_path(worktree)
    if not path.is_file():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln == ".venv"]


def porcelain(worktree):
    return git(worktree, "status", "--porcelain").stdout.strip()


def is_tracked(worktree):
    return (
        git(worktree, "ls-files", "--error-unmatch", ".venv", check=False).returncode
        == 0
    )


def check_ignore(worktree):
    """Diagnostic only: `--no-index` so a tracked link still names the rule."""
    return git(worktree, "check-ignore", "-v", "--no-index", ".venv", check=False)


# ---------------------------------------------------------------------------
# The contract of the shipped fence
# ---------------------------------------------------------------------------


class TestFenceContract:
    def test_step_4_has_exactly_one_bash_fence(self):
        fence_for(SKILL_FILE.read_text(encoding="utf-8"))

    def test_substitution_addresses_the_branch_worktree(self):
        fence = fence_for(SKILL_FILE.read_text(encoding="utf-8"))
        assert f".worktrees/{BRANCH}" in fence
        assert "{branch}" not in fence


# ---------------------------------------------------------------------------
# The positive arm: the fence creates a link that is ignored and untracked
# ---------------------------------------------------------------------------


class TestFenceCreatesAnIgnoredLink:
    def test_link_exists(self, isolated_git_config, tmp_path):
        repo, worktree = make_repo(tmp_path)
        run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), repo)
        link = worktree / ".venv"
        assert link.is_symlink(), (
            "the fence did not create the .venv symlink. A clean porcelain tree "
            "is also produced when the link was never created, so this assertion "
            "must come before the cleanliness arms."
        )
        assert os.readlink(link) == str(repo / ".venv")

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
        kept = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln != ".venv"]
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        assert porcelain(worktree) == "?? .venv", (
            "removing the fence's info/exclude line left the tree clean, so "
            "something other than that line is ignoring the link and the "
            "positive arms prove less than they claim"
        )


# ---------------------------------------------------------------------------
# Idempotency: running the fence twice is how a reused worktree is handled
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
        assert "left untouched" in second.stdout, (
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


# ---------------------------------------------------------------------------
# Edge cases the skill promises
# ---------------------------------------------------------------------------


class TestNoVenvInMainCheckout:
    def test_skips_without_a_link_or_a_rule(self, isolated_git_config, tmp_path):
        repo, worktree = make_repo(tmp_path, with_venv=False)
        done = run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), repo)
        assert "skipped (no .venv in the main checkout)" in done.stdout
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
        assert "already exists" in done.stdout and "left untouched" in done.stdout
        assert real.is_dir() and not real.is_symlink()
        assert (real / "marker").read_text(encoding="utf-8") == "local venv\n"
        # The fence writes the ignore rule BEFORE the guard, so the rule is
        # present even though the path was left alone. Asserting its absence
        # would encode a behaviour the skill deliberately does not have.
        assert len(exclude_venv_lines(worktree)) == 1


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
    """The write-failure branch, forced portably.

    No chmod: CI may run as root, where permission bits do not bite and the arm
    would pass vacuously. Instead the failure is forced through what the fence
    itself reads — a `{branch}` with no worktree behind it makes
    `git -C <path> rev-parse` fail, so the exclude path comes back empty and
    both write legs fail. The fence must warn and create no link.
    """

    def test_warning_and_no_link_when_the_exclude_path_is_unresolvable(
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
            f"the fence must warn and continue, not fail: {done.stderr.strip()}"
        )
        assert "could not write" in done.stdout and ".venv link skipped" in done.stdout, (
            f"expected the exclude-write warning; got {done.stdout.strip()!r}"
        )
        assert not os.path.lexists(missing), (
            "a link was created even though the ignore rule could not be written"
        )
        assert exclude_venv_lines(repo) == []