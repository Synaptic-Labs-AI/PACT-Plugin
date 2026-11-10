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

`git check-ignore` is diagnostic in the positive arms: its output names the
matching rule and is printed on failure. `TestAnchoredRule` is the exception —
there the rule's anchoring IS the property under test, so its return code is
asserted directly, with porcelain beside it. The skill's own runtime
`check-ignore -q` is a different use — there, rc 1 is the intended loud
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
import shutil
import subprocess
from collections import namedtuple
from pathlib import Path

import pytest

SKILL_FILE = (
    Path(__file__).resolve().parents[1] / "skills" / "worktree-setup" / "SKILL.md"
)
CLEANUP_SKILL_FILE = (
    Path(__file__).resolve().parents[1] / "skills" / "worktree-cleanup" / "SKILL.md"
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
    assert skill_text.count(start_heading) == 1, (
        f"{start_heading!r} appears {skill_text.count(start_heading)} times. "
        "The FIRST occurrence is extracted, so a duplicate heading earlier in "
        "the file (a quick reference, an example) makes every arm measure a "
        "section that is not the operative one while the real fence regresses "
        "silently."
    )
    block = skill_text[start:]
    end = block.find(end_heading)
    assert end != -1, (
        f"{start_heading!r} does not run into {end_heading!r} — the file changed shape"
    )
    return block[:end]


def fence_block(skill_text, start_heading, end_heading):
    """The single bash fence between two headings, unsubstituted."""
    block = _section(skill_text, start_heading, end_heading)
    fences = _FENCE.findall(block)
    assert len(fences) == 1, (
        f"expected exactly one bash fence between {start_heading!r} and "
        f"{end_heading!r}; found {len(fences)}"
    )
    return fences[0]


def _fence_in(skill_text, start_heading, end_heading, branch):
    """A fence with `{branch}` substituted.

    Fails closed: no `{branch}` placeholder means the test would silently
    address the literal placeholder path.
    """
    fence = fence_block(skill_text, start_heading, end_heading)
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


def fence_for_step_2(skill_text):
    """Step 2's fence — creates `.worktrees/` and the gitignore rule (no branch)."""
    return fence_block(skill_text, "### Step 2:", "### Step 3:")


def cleanup_remove_fence(skill_text, branch=BRANCH):
    """Cleanup Step 2's fence — the worktree-removal step."""
    return _fence_in(skill_text, "### Step 2:", "### Step 3:", branch)


def cleanup_delete_fence(skill_text, branch=BRANCH):
    """Cleanup Step 3's fence — the branch-deleting step."""
    return _fence_in(skill_text, "### Step 3:", "### Step 4:", branch)


# Every fence that reads a substituted value through a heredoc. Each must
# refuse a multi-line value and an unsubstituted placeholder before a command
# uses it. The exit code differs by site: the setup link step warns and
# continues (0), the rest stop the step (1). `cleanup-step-1.5` reads the
# worktree path rather than the branch, so it carries its own placeholder and
# its own refusal messages.
_Site = namedtuple(
    "_Site", "path start end exit_code placeholder multiline unsubstituted"
)
_SITES = {
    "setup-step-3": _Site(
        SKILL_FILE, "### Step 3:", "### Step 4:", 1, "{branch}",
        "the branch value must be a single line",
        "the branch placeholder was not substituted",
    ),
    "setup-step-4": _Site(
        SKILL_FILE, "### Step 4:", "### Step 5:", 0, "{branch}",
        "the branch value must be a single line",
        "the branch placeholder was not substituted",
    ),
    "cleanup-step-1.5": _Site(
        CLEANUP_SKILL_FILE, "### Step 1.5:", "### Step 2:", 1, "{abs_worktree}",
        "the worktree path must be a single line",
        "the worktree path placeholder was not substituted",
    ),
    "cleanup-step-2": _Site(
        CLEANUP_SKILL_FILE, "### Step 2:", "### Step 3:", 1, "{branch}",
        "the branch value must be a single line",
        "the branch placeholder was not substituted",
    ),
    "cleanup-step-3": _Site(
        CLEANUP_SKILL_FILE, "### Step 3:", "### Step 4:", 1, "{branch}",
        "the branch value must be a single line",
        "the branch placeholder was not substituted",
    ),
}

# The four fences that resolve the main checkout root. The body is duplicated
# by design (shell variables do not survive between Bash calls); only the
# guard's failure branch differs, 1 to stop and 0 to skip the link step.
_ROOT_GUARD_SITES = {
    "setup-step-2": (SKILL_FILE, "### Step 2:", "### Step 3:", 1),
    "setup-step-3": (SKILL_FILE, "### Step 3:", "### Step 4:", 1),
    "setup-step-4": (SKILL_FILE, "### Step 4:", "### Step 5:", 0),
    "cleanup-step-2": (CLEANUP_SKILL_FILE, "### Step 2:", "### Step 3:", 1),
}


def _read_site_fence(skill_text, start, end, placeholder, value):
    """A fence with its placeholder substituted; fails closed when it is gone."""
    fence = fence_block(skill_text, start, end)
    assert placeholder in fence, (
        f"the fence after {start!r} no longer carries a {placeholder} "
        "placeholder, so this test cannot address the path the skill uses"
    )
    return fence.replace(placeholder, value)


def read_site_fence(label, value=BRANCH):
    site = _SITES[label]
    return _read_site_fence(
        site.path.read_text(encoding="utf-8"),
        site.start,
        site.end,
        site.placeholder,
        value,
    )


def read_site_fence_unsubstituted(label):
    site = _SITES[label]
    return fence_block(site.path.read_text(encoding="utf-8"), site.start, site.end)


def root_guard_parts(fence):
    """The root-resolution body and the guard's failure branch.

    Split at `|| {` so the four copies can be compared modulo the one
    documented difference (exit 1 to stop, exit 0 to skip the link step).
    """
    lines = fence.splitlines()
    start = next(
        i
        for i, line in enumerate(lines)
        if line.startswith("LOCAL_GIT_DIR=$(git rev-parse --git-dir")
    )
    guard = next(
        i for i in range(start, len(lines)) if lines[i].startswith('[ -n "$REPO_ROOT" ]')
    )
    head, _, tail = lines[guard].partition("|| {")
    return lines[start:guard] + [head], tail


def shipped_delimiter(skill_text):
    """The ONE quoted heredoc delimiter the fence reads the branch with."""
    delimiters = re.findall(r"<<\s*'([^']+)'", fence_for(skill_text))
    assert len(delimiters) == 1, (
        f"expected exactly one quoted heredoc delimiter; found {delimiters}"
    )
    return delimiters[0]


def site_delimiter(label):
    """The ONE quoted heredoc delimiter the named reading site uses.

    Every substituted value reaches its commands through a quoted heredoc, so
    each site's delimiter must be a string no legal value can equal. Pinned per
    site because a tidy in one duplicated copy is invisible to the others.
    """
    site = _SITES[label]
    fence = fence_block(site.path.read_text(encoding="utf-8"), site.start, site.end)
    delimiters = re.findall(r"<<\s*'([^']+)'", fence)
    assert len(delimiters) == 1, (
        f"{label} must read its value through exactly one quoted heredoc; "
        f"found {delimiters}"
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


def make_outer_repo_linked_worktree(tmp_path):
    """A separate-git-dir checkout whose git dir sits INSIDE another repo.

    From a linked worktree of the inner checkout, the common-dir parent is
    `<outer>/area` — inside the OUTER repo's work tree. A guard that asks only
    "is the computed root inside a work tree?" accepts it, because
    `git -C <outer>/area rev-parse --show-toplevel` succeeds; the equality
    guard rejects it.
    """
    outer = tmp_path / "outer"
    outer.mkdir()
    subprocess.run(["git", "init", "--quiet", str(outer)], check=True)
    (outer / "README.md").write_text("outer\n", encoding="utf-8")
    git(outer, "add", "-A")
    git(outer, *GIT_ID, "commit", "-qm", "init")

    area = outer / "area"
    area.mkdir()
    inner = tmp_path / "inner"
    subprocess.run(
        [
            "git", "init", "--quiet", "--separate-git-dir",
            str(area / "gitdir-inner"), str(inner),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    (inner / "README.md").write_text("inner\n", encoding="utf-8")
    git(inner, "add", "-A")
    git(inner, *GIT_ID, "commit", "-qm", "init")
    linked = tmp_path / "linked"
    git(inner, "worktree", "add", "--quiet", str(linked), "-b", "wt")
    return outer, area, linked


def make_outer_root_git_dir_linked_worktree(tmp_path):
    """The separate git dir sits DIRECTLY under the outer repo's root.

    Here the computed root `<outer>` IS a worktree toplevel, so the
    toplevel-equality guard alone accepts it. Only the common-dir equality
    (`<outer>` exposes `<outer>/.git`, not the inner repo's git dir) rejects it.
    """
    outer = tmp_path / "outer"
    outer.mkdir()
    subprocess.run(["git", "init", "--quiet", str(outer)], check=True)
    (outer / "README.md").write_text("outer\n", encoding="utf-8")
    git(outer, "add", "-A")
    git(outer, *GIT_ID, "commit", "-qm", "init")

    inner = tmp_path / "inner"
    subprocess.run(
        [
            "git", "init", "--quiet", "--separate-git-dir",
            str(outer / "gitdir-inner"), str(inner),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    (inner / "README.md").write_text("inner\n", encoding="utf-8")
    git(inner, "add", "-A")
    git(inner, *GIT_ID, "commit", "-qm", "init")
    linked = tmp_path / "linked"
    git(inner, "worktree", "add", "--quiet", str(linked), "-b", "wt")
    return outer, linked


def make_same_repo_subdirectory_git_dir_linked_worktree(tmp_path):
    """A repo whose separate git dir sits under a SUBDIRECTORY of itself.

    From a linked worktree the common-dir equality PASSES — both `<repo>/sub`
    and the linked worktree resolve their common dir to `<repo>/sub/gitdir`.
    Only the toplevel clause rejects the computed root `<repo>/sub`.
    """
    repo = tmp_path / "repo"
    sub = repo / "sub"
    sub.mkdir(parents=True)
    subprocess.run(
        [
            "git", "init", "--quiet", "--separate-git-dir",
            str(sub / "gitdir"), str(repo),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    (repo / "README.md").write_text("repo\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, *GIT_ID, "commit", "-qm", "init")
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "--quiet", str(linked), "-b", "wt")
    return repo, sub, linked


def make_template_less_repo(tmp_path):
    """A repo initialised with an EMPTY template, so `.git/info/` is absent."""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "--quiet", "--template=", str(repo)], check=True)
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, *GIT_ID, "commit", "-qm", "init")
    (repo / ".venv").mkdir()
    (repo / ".venv" / "marker").write_text("venv\n", encoding="utf-8")
    worktree = repo / ".worktrees" / BRANCH
    git(repo, "worktree", "add", "--quiet", str(worktree), "-b", BRANCH)
    return repo, worktree


def make_empty_tree_repo(tmp_path, branch=BRANCH):
    """A repo whose HEAD tree is EMPTY, plus a worktree with no tracked files.

    The cleanup skill's deleted-ref row distinguishes a worktree WITH tracked
    files (git refuses as dirty) from one with none (removal succeeds). Only
    this shape reaches the second outcome.
    """
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    git(repo, *GIT_ID, "commit", "--allow-empty", "-qm", "empty")
    worktree = repo / ".worktrees" / branch
    git(repo, "worktree", "add", "--quiet", str(worktree), "-b", branch)
    return repo, worktree


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
        # Pin the locale: arms below match git's English stderr, and a
        # translated message would red them for the wrong reason.
        "LC_ALL": "C",
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


def run_fence_without_tool(fence, repo, missing):
    """Run the fence with `missing` absent from PATH, and nothing else changed.

    A shim directory links every external tool the fence uses except `missing`,
    so its invocation fails with 127 while the rest of the fence behaves
    normally. Nothing global is mutated: the caller's PATH is never touched, and
    the shim is scoped to this one subprocess.
    """
    shim = repo.parent / f"bin-without-{missing}"
    shim.mkdir(exist_ok=True)
    for name in ("git", "grep", "tail", "mkdir", "rm", "dirname"):
        if name == missing:
            continue
        target = shutil.which(name)
        assert target, f"{name} is not on PATH, so the shim cannot be built"
        link = shim / name
        if not link.exists():
            link.symlink_to(target)
    env = {
        "PATH": str(shim),
        "HOME": os.environ["HOME"],
        "XDG_CONFIG_HOME": os.environ.get("XDG_CONFIG_HOME", ""),
        "GIT_CONFIG_NOSYSTEM": "1",
        "LC_ALL": "C",
    }
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
    """The positive properties plus the unlink warning, for the mutation matrix.

    `not_tracked` has NO discriminating mutation in the matrix: no shipped
    command tracks the link, so no edit to the fence can produce the tracked
    state. The tracked state is constructed directly by
    `TestTrackedLinkIsReported`; this row is asserted here so the positive
    state stays pinned, not as a claimed kill.

    The returncode is asserted so a fence that CRASHED before the link step
    cannot produce the same matrix as the dropped-link mutation.
    """
    done = run_fence(fence, repo)
    assert done.returncode == 0, (
        f"the fence crashed (rc={done.returncode}) instead of exercising the "
        f"matrix: stdout={done.stdout.strip()!r} stderr={done.stderr.strip()!r}"
    )
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
        """The branch is DATA. Exactly one QUOTED heredoc carries it.

        The read is a brace group: the value line, then a second line that
        must be empty. The brace group is what makes a multi-line value a
        refusal instead of a silent first-line truncation, so the whole
        three-line shape is pinned as one unit.
        """
        text = SKILL_FILE.read_text(encoding="utf-8")
        unsubstituted = fence_block(text, "### Step 4:", "### Step 5:")
        assert unsubstituted.count("{branch}") == 1, (
            f"the placeholder appears {unsubstituted.count('{branch}')} times "
            "in the unsubstituted fence; it must appear exactly once"
        )
        fence = fence_for(text)
        delimiter = shipped_delimiter(text)
        group = (
            f"{{ IFS= read -r BRANCH; IFS= read -r BRANCH_EXTRA || :; }} "
            f"<<'{delimiter}'\n{BRANCH}\n{delimiter}"
        )
        assert fence.count(group) == 1, (
            "the branch is not read through the brace-group quoted heredoc: "
            f"expected exactly one {group!r}"
        )
        assert len(re.findall(r"<<\s*'", fence)) == 1, (
            "the fence has more than one quoted heredoc; the branch must arrive "
            "through exactly one"
        )
        assert fence.count(BRANCH) == 1, (
            f"the branch text appears {fence.count(BRANCH)} times; it must "
            "appear only as the heredoc body, never inside an expansion"
        )


# ---------------------------------------------------------------------------
# The positive arm: the fence creates a link that is ignored and untracked
# ---------------------------------------------------------------------------


class TestExtractionFollowsTheOperativeHeading:
    """The extractor must read the OPERATIVE section, not the first lookalike.

    `_section` binds the first literal occurrence of a heading. Measured: a
    duplicate earlier `### Step 4:`/`### Step 5:` pair carrying a byte-identical
    fence let the whole suite stay green while the REAL Step 4 link command was
    replaced by `false`. The uniqueness assertion in `_section` turns that
    silent misselection into a red; this arm is its pin.
    """

    def test_a_duplicate_earlier_heading_reds_the_extraction(self):
        text = SKILL_FILE.read_text(encoding="utf-8")
        fence_for(text)  # the shipped file still has one operative section
        block = _section(text, "### Step 4:", "### Step 5:")
        decoy = (
            "### Step 4: Quick reference (duplicate)\n"
            + block[len("### Step 4:") :]
            + "### Step 5: Quick reference end\n\n"
        )
        anchor = text.index("### Step 2:")
        mutated = text[:anchor] + decoy + text[anchor:]

        # Break the REAL fence after the decoy: if extraction read the decoy,
        # no arm would ever execute this command.
        decoy_end = mutated.index("### Step 5: Quick reference end") + len(
            "### Step 5: Quick reference end"
        )
        head, tail = mutated[:decoy_end], mutated[decoy_end:]
        link = 'ln -s "$REPO_ROOT/.venv" "$WORKTREE/.venv"'
        assert tail.count(link) == 1, (
            "the real link command moved or duplicated; this mutation no longer "
            "measures the misselection it names"
        )
        mutated = head + tail.replace(link, "false")

        with pytest.raises(AssertionError, match="appears 2 times"):
            fence_for(mutated)


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
        marker = tmp_path / "PWNED"
        branch = f"wt$(touch {marker})"
        done = run_fence(
            fence_for(SKILL_FILE.read_text(encoding="utf-8"), branch=branch), repo
        )
        assert "Venv link: skipped (invalid branch name)" in done.stdout, (
            f"the invalid name was not rejected: {done.stdout.strip()!r}"
        )
        assert not marker.exists(), (
            "the invalid-name path still evaluated the payload as shell code"
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


_DELIMITER_SITES = [pytest.param(label, id=label) for label in _SITES]


class TestDelimiterCannotBeForged:
    """Every site's delimiter must be a string NO legal branch may equal.

    Pinning a hardcoded spelling (`PACT_BRANCH`) would stay green if a
    maintainer "tidied" the odd space delimiter to a hyphen form — and then a
    legitimate branch named `PACT-BRANCH` would terminate the heredoc and be
    falsely rejected. The property below is parametrized over ALL FIVE sites
    because a tidy in one duplicated copy is invisible to the other four. The
    harvest site reads a filesystem path rather than a ref, so for it this arm
    pins the delimiter's shape only; the forgeable case there is real and is
    characterized separately.
    """

    @pytest.mark.parametrize("label", _DELIMITER_SITES)
    def test_the_shipped_delimiter_is_not_a_legal_branch_name(
        self, label, tmp_path
    ):
        delimiter = site_delimiter(label)
        done = subprocess.run(
            ["git", "check-ref-format", "--branch", delimiter],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            # The assertion below matches English stderr; pin the locale so a
            # translated message cannot red the arm for the wrong reason.
            env={**os.environ, "LC_ALL": "C"},
        )
        assert done.returncode != 0, (
            f"{label}'s heredoc delimiter {delimiter!r} is a LEGAL branch name "
            "— a ref with that exact value would terminate the heredoc and be "
            "falsely rejected. The delimiter must be unforgeable by any valid "
            "ref."
        )
        assert "not a valid branch name" in done.stderr, (
            f"{label}'s delimiter was rejected for the wrong reason: "
            f"{done.stderr!r}"
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
    """A two-line value is REFUSED; a delimiter-injecting value still executes.

    The brace-group double read catches a value that simply carries a
    newline: the first read takes line 1, the second read takes line 2, and a
    non-empty remainder stops the step before the value is used. What no
    heredoc can stop is a value that also supplies the delimiter line
    (`evil\\nPACT BRANCH\\ntouch <marker>`): the heredoc terminates at that
    line and the leftover `touch` runs as a top-level command. The defence
    there is the skill's single-line rule, applied by the agent BEFORE
    substituting. The arms below pin each fact separately so neither is
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
                "stop a delimiter-injecting value; this sentence is the only "
                "defence."
            )

        # ORDER is pinned in every step whose fence substitutes the value:
        # the sentence must reach the agent before the fence it governs.
        for start, end in (
            ("### Step 3:", "### Step 4:"),
            ("### Step 4:", "### Step 5:"),
        ):
            block = _section(text, start, end)
            assert block.index("must be a single line") < block.index("```bash"), (
                f"{start}'s single-line rule must precede the fence that "
                "substitutes the value"
            )

    def test_a_two_line_value_is_refused(self, isolated_git_config, tmp_path):
        repo, worktree = make_repo(tmp_path)
        done = run_fence(
            fence_for(SKILL_FILE.read_text(encoding="utf-8"), branch="evil\nsecond"),
            repo,
        )
        assert "the branch value must be a single line" in done.stdout, (
            f"a two-line value was not refused: {done.stdout.strip()!r}"
        )
        assert done.returncode == 0, f"the link step must skip, not fail: {done.stderr}"
        assert not os.path.lexists(worktree / ".venv")
        assert exclude_venv_lines(worktree) == []

    def test_the_fence_alone_does_not_stop_a_delimiter_injecting_value(
        self, isolated_git_config, tmp_path
    ):
        """CHARACTERIZATION, not a safety claim: this passes while the marker
        IS created. The single-line prose rule is the defence; no fence text
        can bind before its own heredoc is parsed."""
        repo, _ = make_repo(tmp_path)
        marker = tmp_path / "PWNED"
        value = f"evil\nPACT BRANCH\ntouch {marker}"
        run_fence(
            fence_for(SKILL_FILE.read_text(encoding="utf-8"), branch=value), repo
        )
        assert marker.exists(), (
            "the fence unexpectedly stopped a delimiter-injecting value — if it "
            "now validates the delimiter itself, this characterization should "
            "become a positive assertion"
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


def guard_fence(site, branch=BRANCH):
    """Dispatch to one of the two byte-identical root-guard fences."""
    if site == "setup-step-2":
        return fence_for_step_2(SKILL_FILE.read_text(encoding="utf-8"))
    assert site == "cleanup-step-2"
    return cleanup_remove_fence(CLEANUP_SKILL_FILE.read_text(encoding="utf-8"), branch)


_GUARD_COPIES = [
    pytest.param(site, id=site) for site in ("setup-step-2", "cleanup-step-2")
]


class TestOuterRepoRootIsRejected:
    """A separate git dir inside ANOTHER repo must not donate that repo as root.

    A guard that asks only "is the computed root inside a work tree?" accepts
    `<outer>/area`. The equality guard asks whether
    the computed root IS a worktree toplevel, and stops before writing. Both
    copies of the guard (setup Step 2 and cleanup Step 2) are exercised so the
    duplicated text cannot drift.
    """

    @pytest.mark.parametrize("guard_copy", _GUARD_COPIES)
    def test_step_2_stops_and_writes_nothing_to_the_outer_repo(
        self, isolated_git_config, tmp_path, guard_copy
    ):
        _outer, area, linked = make_outer_repo_linked_worktree(tmp_path)
        done = run_fence(guard_fence(guard_copy), linked)
        assert done.returncode != 0, (
            f"the {guard_copy} guard did not stop: {done.stdout.strip()!r}"
        )
        assert "Cannot determine the main checkout root (computed " in done.stdout, (
            f"the stop message is missing: {done.stdout.strip()!r}"
        )
        assert str(area) in done.stdout, (
            "the stop message does not name the computed root"
        )
        assert not os.path.lexists(area / ".worktrees"), (
            "Step 2 created .worktrees inside the outer repository"
        )
        ignore = area / ".gitignore"
        assert not ignore.exists() or ".worktrees" not in ignore.read_text(
            encoding="utf-8"
        ), "Step 2 wrote a .worktrees rule into the outer repository"

    def test_step_4_skips_with_the_documented_message(
        self, isolated_git_config, tmp_path
    ):
        _outer, area, linked = make_outer_repo_linked_worktree(tmp_path)
        done = run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), linked)
        assert done.returncode == 0, f"the fence failed: {done.stderr.strip()}"
        assert "Venv link: skipped (cannot determine the main checkout root)" in done.stdout
        assert not os.path.lexists(area / ".worktrees")

    def test_direct_under_root_placement_is_rejected(self, isolated_git_config, tmp_path):
        """Root directly under the outer repo: `<outer>` is itself a toplevel,
        so ONLY the common-dir equality can reject it. Without that condition
        the outer repo is written to; with it, both steps stop."""
        outer, linked = make_outer_root_git_dir_linked_worktree(tmp_path)
        step2 = run_fence(
            fence_for_step_2(SKILL_FILE.read_text(encoding="utf-8")), linked
        )
        assert step2.returncode != 0, (
            f"Step 2 used the outer repo as the root: {step2.stdout.strip()!r}"
        )
        assert str(outer) in step2.stdout, (
            "the stop message does not name the computed root"
        )
        assert not os.path.lexists(outer / ".worktrees"), (
            "Step 2 created .worktrees in the outer repository"
        )
        ignore = outer / ".gitignore"
        assert not ignore.exists() or ".worktrees" not in ignore.read_text(
            encoding="utf-8"
        ), "Step 2 wrote a .worktrees rule into the outer repository"

        step4 = run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), linked)
        assert "Venv link: skipped (cannot determine the main checkout root)" in step4.stdout
        assert not os.path.lexists(outer / ".worktrees")


class TestSameRepoSubdirectoryRootIsRejected:
    """The TOPLEVEL clause is load-bearing on its own reachable state.

    With the git dir at `<repo>/sub/gitdir`, the common-dir equality passes
    (both sides resolve to `<repo>/sub/gitdir`) — so the root-directly-under-
    the-outer-repo arm cannot catch a toplevel-clause removal there. Here only the toplevel clause
    rejects the computed root `<repo>/sub`; without it Step 2 writes into
    `<repo>/sub`.
    """

    @pytest.mark.parametrize("guard_copy", _GUARD_COPIES)
    def test_same_repo_subdirectory_placement_is_rejected(
        self, isolated_git_config, tmp_path, guard_copy
    ):
        _repo, sub, linked = make_same_repo_subdirectory_git_dir_linked_worktree(
            tmp_path
        )
        step2 = run_fence(guard_fence(guard_copy), linked)
        assert step2.returncode != 0, (
            f"the {guard_copy} guard used the subdirectory as the root: "
            f"{step2.stdout.strip()!r}"
        )
        assert "Cannot determine the main checkout root (computed " in step2.stdout
        assert str(sub) in step2.stdout, (
            "the stop message does not name the computed root"
        )
        assert not os.path.lexists(sub / ".worktrees"), (
            "Step 2 created .worktrees in the repo subdirectory"
        )
        ignore = sub / ".gitignore"
        assert not ignore.exists() or ".worktrees" not in ignore.read_text(
            encoding="utf-8"
        ), "Step 2 wrote a .worktrees rule into the repo subdirectory"

        step4 = run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), linked)
        assert "Venv link: skipped (cannot determine the main checkout root)" in step4.stdout
        assert not os.path.lexists(sub / ".worktrees")


class TestRootGuardCopiesAreIdentical:
    """The root guard is duplicated four times because shell state does not
    survive between Bash calls. Four copies can drift; this pin compares the
    bodies and allows only the documented difference — exit 1 to stop the
    step, exit 0 to skip the link step.
    """

    def test_all_four_sites_share_one_body_modulo_the_exit_branch(self):
        bodies, tails = {}, {}
        for label, (path, start, end, expected_exit) in _ROOT_GUARD_SITES.items():
            fence = fence_block(path.read_text(encoding="utf-8"), start, end)
            body, tail = root_guard_parts(fence)
            bodies[label] = body
            tails[label] = (tail, expected_exit)

        reference = bodies["setup-step-2"]
        for label, body in bodies.items():
            if body == reference:
                continue
            difference = [
                (expected, actual)
                for expected, actual in zip(reference, body)
                if expected != actual
            ]
            pytest.fail(
                f"{label}'s root guard diverged from setup-step-2's. "
                f"First differences (expected, actual): {difference[:3]}"
            )

        for label, (tail, expected_exit) in tails.items():
            assert f"exit {expected_exit}" in tail, (
                f"{label}'s guard must exit {expected_exit}, got {tail!r}"
            )


class TestStep2CreatesTheDirectoryAndIgnoreRule:
    """Step 2's happy path: create the directory and write one ignore rule.

    The shipped rule is anchored and slash-free (`/.worktrees`): the leading
    slash confines it to the repository root, and the missing trailing slash
    keeps it matching a symlinked `.worktrees` directory as well as a real one.
    """

    def _fence(self):
        return fence_for_step_2(SKILL_FILE.read_text(encoding="utf-8"))

    def test_directory_and_one_rule_after_two_runs(
        self, isolated_git_config, tmp_path
    ):
        repo = make_empty_repo(tmp_path)
        first = run_fence(self._fence(), repo)
        second = run_fence(self._fence(), repo)
        assert first.returncode == 0, f"first run failed: {first.stderr.strip()}"
        assert second.returncode == 0, f"second run failed: {second.stderr.strip()}"
        assert (repo / ".worktrees").is_dir(), "Step 2 did not create .worktrees/"
        assert (repo / ".gitignore").read_text(encoding="utf-8").splitlines().count(
            "/.worktrees"
        ) == 1, "the rule was not written exactly once across two runs"

    def test_final_line_without_a_newline_is_preserved(
        self, isolated_git_config, tmp_path
    ):
        repo = make_empty_repo(tmp_path)
        gitignore = repo / ".gitignore"
        gitignore.write_text("keep-me", encoding="utf-8")  # deliberately no newline
        run_fence(self._fence(), repo)
        lines = gitignore.read_text(encoding="utf-8").splitlines()
        assert lines == ["keep-me", "/.worktrees"], (
            f"the append merged into the existing final line: {lines!r}"
        )


class TestSymlinkedWorktreesIsIgnored:
    """The shipped rule must match a SYMLINKED `.worktrees`, not just a dir.

    A trailing-slash rule (`.worktrees/`) matches directories only, so a
    symlinked `.worktrees` is invisible to it and `git add -A` would stage the
    link and everything behind it. The slash-free form is what prevents that;
    the counter-arm below proves the slash is the property being pinned.
    """

    def test_a_symlinked_worktrees_entry_is_ignored_and_not_staged(
        self, isolated_git_config, tmp_path
    ):
        repo = make_empty_repo(tmp_path)
        store = tmp_path / "store"
        store.mkdir()
        (store / "keep").write_text("x\n", encoding="utf-8")
        (repo / ".worktrees").symlink_to(store, target_is_directory=True)
        assert (repo / ".worktrees").is_symlink()

        done = run_fence(
            fence_for_step_2(SKILL_FILE.read_text(encoding="utf-8")), repo
        )
        assert done.returncode == 0, f"Step 2 failed: {done.stderr.strip()}"

        ignored = git(repo, "check-ignore", "-v", ".worktrees", check=False)
        assert ignored.returncode == 0, (
            "the shipped rule does not match a symlinked .worktrees; a rule "
            "with a trailing slash matches directories only. "
            f"check-ignore: {ignored.stdout.strip() or ignored.stderr.strip()}"
        )
        assert "/.worktrees" in ignored.stdout, (
            f"the matching rule is not the shipped one: {ignored.stdout!r}"
        )

        git(repo, "add", "-A")
        staged = git(repo, "diff", "--cached", "--name-only").stdout.splitlines()
        assert not any(p.startswith(".worktrees") for p in staged), (
            f"git add -A staged the symlinked .worktrees: {staged!r}"
        )

    def test_the_trailing_slash_form_does_not_match_a_symlink(
        self, isolated_git_config, tmp_path
    ):
        """Counter-arm: the slash is the property, not 'some rule exists'."""
        repo = make_empty_repo(tmp_path)
        store = tmp_path / "store"
        store.mkdir()
        (repo / ".worktrees").symlink_to(store, target_is_directory=True)

        done = run_fence(
            fence_for_step_2(SKILL_FILE.read_text(encoding="utf-8")), repo
        )
        assert done.returncode == 0, f"Step 2 failed: {done.stderr.strip()}"

        gitignore = repo / ".gitignore"
        lines = [
            ln for ln in gitignore.read_text(encoding="utf-8").splitlines()
            if ln != "/.worktrees"
        ]
        gitignore.write_text("\n".join(lines + [".worktrees/"]) + "\n", encoding="utf-8")

        report = git(repo, "check-ignore", "-v", ".worktrees", check=False)
        assert report.returncode == 1, (
            "a trailing-slash `.worktrees/` rule unexpectedly matched a symlink "
            f"— the slash-free form would then prove nothing. {report.stdout!r}"
        )


class TestCleanupBranchIsInertText:
    """Cleanup Step 2 is the OTHER older injection site.

    It hands the branch to `git worktree remove`. A legal ref carrying a
    `$(...)` payload must be removed by its LITERAL name, with no execution —
    the worktree is gone and the marker is not.
    """

    def test_hostile_ref_is_removed_literally_without_executing(
        self, isolated_git_config, tmp_path
    ):
        marker = tmp_path / "PWNED"
        branch = f"wt$(touch$IFS{marker})"
        repo, worktree = make_repo(tmp_path, branch=branch)
        assert (worktree / ".git").is_file()

        done = run_fence(
            cleanup_remove_fence(
                CLEANUP_SKILL_FILE.read_text(encoding="utf-8"), branch=branch
            ),
            repo,
        )
        assert not marker.exists(), (
            "the cleanup fence evaluated the branch as shell code — the "
            f"substitution created {marker}"
        )
        assert done.returncode == 0, f"cleanup failed: {done.stderr.strip()}"
        assert not os.path.lexists(worktree), (
            "the literal worktree was not removed"
        )
        listed = git(repo, "worktree", "list", "--porcelain").stdout
        assert str(worktree) not in listed


class TestCleanupMultiLineValueIsTheInstructionsJob:
    """The cleanup heredocs cannot stop a multi-line value either.

    The heredoc parses BEFORE any command runs, so a value carrying the
    delimiter leaves shell lines after the terminator and those lines execute.
    The defence is the single-line rule in the prose of BOTH branch-reading
    steps, stated BEFORE the fence that substitutes the value. These arms pin
    that instruction and record the limitation; neither claims a shell
    guarantee.
    """

    def test_the_single_line_rule_precedes_the_fence_in_every_reading_step(
        self,
    ):
        text = CLEANUP_SKILL_FILE.read_text(encoding="utf-8")
        for start, end, label in (
            ("### Step 1.5:", "### Step 2:", "harvest"),
            ("### Step 2:", "### Step 3:", "remove-worktree"),
            ("### Step 3:", "### Step 4:", "delete-branch"),
        ):
            block = _section(text, start, end)
            assert "must be a single line" in block, (
                f"the {label} step lost the single-line rule. The heredoc "
                "parses before any command runs, so this sentence is the only "
                "defence against a multi-line value."
            )
            assert block.index("must be a single line") < block.index("```bash"), (
                f"the {label} single-line rule must precede the fence that "
                "substitutes the value"
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
            cleanup_remove_fence(
                CLEANUP_SKILL_FILE.read_text(encoding="utf-8"), branch=value
            ),
            repo,
        )
        assert marker.exists(), (
            "the cleanup fence unexpectedly stopped a multi-line value — if it "
            "now validates single-line input, this characterization should "
            "become a positive assertion"
        )


class TestHarvestPathDelimiterIsTheInstructionsJob:
    """The harvest site reads a PATH, so the forgeable case is reachable.

    `{abs_worktree}` is a filesystem path, not a ref: nothing stops a real path
    from containing the delimiter line. The heredoc parses before any command
    runs, so a value carrying that line leaves shell lines after the terminator
    and those lines execute. The defence is the single-line rule in the step's
    prose, stated before the fence. This arm is a CHARACTERIZATION: it passes
    while the marker IS created. If the fence ever validates its value, replace
    it with a positive 'no marker' assertion.
    """

    def test_the_fence_alone_does_not_stop_a_delimiter_injecting_path(
        self, isolated_git_config, tmp_path
    ):
        repo, _ = make_repo(tmp_path)
        marker = tmp_path / "PWNED"
        value = f"evil\nPACT WORKTREE\ntouch {marker}"
        run_fence(read_site_fence("cleanup-step-1.5", value=value), repo)
        assert marker.exists(), (
            "the harvest fence unexpectedly stopped a delimiter-injecting path "
            "— if it now validates its value, this characterization should "
            "become a positive assertion"
        )


def _site_repo(label, tmp_path):
    """The repo shape each reading site needs, with its worktree."""
    if label in ("setup-step-3", "cleanup-step-1.5"):
        return make_empty_repo(tmp_path), None
    return make_repo(tmp_path)


def _assert_site_wrote_nothing(label, repo, worktree, done):
    """The site-specific 'nothing happened' assertion for a refused value."""
    if label == "setup-step-3":
        assert not (repo / ".worktrees").exists(), (
            "a refused branch value still created .worktrees/"
        )
    elif label == "cleanup-step-1.5":
        assert not any(
            marker in done.stdout
            for marker in ("DIR_PRESENT", "DIR_ABSENT", "CANNOT_OBSERVE")
        ), f"a refused worktree path still ran the harvest probe: {done.stdout!r}"
        assert not (repo / "docs").exists(), (
            "a refused worktree path still created a docs/ directory"
        )
    elif label == "setup-step-4":
        assert not os.path.lexists(worktree / ".venv")
        assert exclude_venv_lines(worktree) == [], (
            "a refused branch value still wrote the ignore rule"
        )
    elif label == "cleanup-step-2":
        assert (worktree / ".git").is_file(), (
            "a refused branch value still removed the worktree"
        )
    elif label == "cleanup-step-3":
        assert (
            git(repo, "rev-parse", "--verify", f"refs/heads/{BRANCH}", check=False).returncode
            == 0
        ), "a refused branch value still deleted the branch"


_SITE_PARAMS = [
    pytest.param(label, _SITES[label].exit_code, id=label) for label in _SITES
]


class TestEveryReadSiteRefusesAMultiLineValue:
    """The double read refuses a value that carries a newline.

    This is the fence-level defence; it does not cover a value that also
    supplies the heredoc delimiter (pinned as a characterization above).
    """

    @pytest.mark.parametrize("label,expected_rc", _SITE_PARAMS)
    def test_a_two_line_value_is_refused_before_it_is_used(
        self, isolated_git_config, tmp_path, label, expected_rc
    ):
        repo, worktree = _site_repo(label, tmp_path)
        done = run_fence(read_site_fence(label, value="evil\nsecond"), repo)
        assert _SITES[label].multiline in done.stdout, (
            f"{label} did not refuse a two-line value: {done.stdout.strip()!r}"
        )
        assert done.returncode == expected_rc, (
            f"{label} returned {done.returncode}, expected {expected_rc}: "
            f"{done.stderr.strip()}"
        )
        _assert_site_wrote_nothing(label, repo, worktree, done)


class TestEveryReadSiteRefusesTheUnsubstitutedPlaceholder:
    """A skill whose placeholder was never substituted must stop, not act.

    The check compares against the placeholder's own spelling rather than
    pinning today's syntax, so it survives a placeholder rename in lockstep.
    """

    @pytest.mark.parametrize("label,expected_rc", _SITE_PARAMS)
    def test_the_placeholder_is_refused_before_it_is_used(
        self, isolated_git_config, tmp_path, label, expected_rc
    ):
        repo, worktree = _site_repo(label, tmp_path)
        done = run_fence(read_site_fence_unsubstituted(label), repo)
        assert _SITES[label].unsubstituted in done.stdout, (
            f"{label} acted on the literal placeholder: {done.stdout.strip()!r}"
        )
        assert done.returncode == expected_rc, (
            f"{label} returned {done.returncode}, expected {expected_rc}: "
            f"{done.stderr.strip()}"
        )
        _assert_site_wrote_nothing(label, repo, worktree, done)


class TestPreviousBranchShortcutIsRefused:
    def test_at_minus_one_is_rejected_before_a_worktree_is_added(
        self, isolated_git_config, tmp_path
    ):
        """`@{-1}` passes no validation in a repo with no checkout reflog.

        Measured scope: `git check-ref-format --branch '@{-1}'` exits 128 in a
        fresh repo, so the fence refuses at validation. A repo whose reflog
        holds a previous branch EXPANDS `@{-1}` to that name; `git worktree add
        -b` then fails because the branch exists. The spelling is not
        syntactically rejected in that state, but neither state creates a
        worktree, which is what this arm pins.
        """
        repo = make_empty_repo(tmp_path)
        done = run_fence(read_site_fence("setup-step-3", value="@{-1}"), repo)
        assert "Cannot create worktree: invalid branch name." in done.stdout, (
            f"@{{-1}} was not refused: {done.stdout.strip()!r}"
        )
        assert done.returncode == 1
        assert not (repo / ".worktrees").exists()
        assert (
            git(repo, "rev-parse", "--verify", "refs/heads/@{-1}", check=False).returncode
            != 0
        )


class TestCleanupDeletesTheBranch:
    """Cleanup Step 3's fence was the one shipped fence no test executed."""

    def test_hostile_ref_is_deleted_literally_without_executing(
        self, isolated_git_config, tmp_path
    ):
        marker = tmp_path / "PWNED"
        branch = f"wt$(touch$IFS{marker})"
        repo, _worktree = make_repo(tmp_path, branch=branch)
        text = CLEANUP_SKILL_FILE.read_text(encoding="utf-8")

        removed = run_fence(cleanup_remove_fence(text, branch=branch), repo)
        assert removed.returncode == 0, f"removal failed: {removed.stderr.strip()}"

        done = run_fence(cleanup_delete_fence(text, branch=branch), repo)
        assert not marker.exists(), (
            "cleanup Step 3 evaluated the branch as shell code — the "
            f"substitution created {marker}"
        )
        assert done.returncode == 0, f"deletion failed: {done.stderr.strip()}"
        assert (
            git(repo, "rev-parse", "--verify", f"refs/heads/{branch}", check=False).returncode
            != 0
        ), "the literal branch was not deleted"

    def test_benign_branch_is_deleted(self, isolated_git_config, tmp_path):
        repo, _ = make_repo(tmp_path)
        text = CLEANUP_SKILL_FILE.read_text(encoding="utf-8")
        removed = run_fence(cleanup_remove_fence(text), repo)
        assert removed.returncode == 0, f"removal failed: {removed.stderr.strip()}"
        done = run_fence(cleanup_delete_fence(text), repo)
        assert done.returncode == 0, f"deletion failed: {done.stderr.strip()}"
        assert (
            git(repo, "rev-parse", "--verify", f"refs/heads/{BRANCH}", check=False).returncode
            != 0
        ), "the branch was not deleted"


class TestCleanupRefusesMisidentifiedTargets:
    """Removal must target the worktree registered for the requested branch.

    A path under `.worktrees/` can resolve to a real worktree of another
    branch, a plain directory, or a tree outside the repo. Each refusal below
    is paired with the ordinary removal arm above: the guard is what stops
    them, not a broken fixture.
    """

    def _remove(self, repo, branch):
        return run_fence(
            cleanup_remove_fence(
                CLEANUP_SKILL_FILE.read_text(encoding="utf-8"), branch=branch
            ),
            repo,
        )

    def test_ordinary_removal_still_succeeds(self, isolated_git_config, tmp_path):
        repo, worktree = make_repo(tmp_path)
        done = self._remove(repo, BRANCH)
        assert done.returncode == 0, (
            f"the ordinary removal failed, so the refusal arms prove nothing: "
            f"{done.stdout.strip()!r} {done.stderr.strip()!r}"
        )
        assert not worktree.exists()

    def test_symlink_to_another_branchs_worktree_inside_worktrees(
        self, isolated_git_config, tmp_path
    ):
        repo, _ = make_repo(tmp_path)
        other = repo / ".worktrees" / "other"
        git(repo, "worktree", "add", "--quiet", str(other), "-b", "other")
        (repo / ".worktrees" / "evil").symlink_to(other, target_is_directory=True)

        done = self._remove(repo, "evil")
        assert done.returncode != 0
        assert "is not the worktree for evil" in done.stdout, (
            f"a symlink to another branch's worktree was accepted: "
            f"{done.stdout.strip()!r}"
        )
        assert (other / ".git").is_file(), "the other branch's worktree was removed"

    def test_symlink_resolving_outside_worktrees(self, isolated_git_config, tmp_path):
        repo, _ = make_repo(tmp_path)
        other = tmp_path / "other"
        other.mkdir()
        subprocess.run(["git", "init", "--quiet", str(other)], check=True)
        (other / "README.md").write_text("other\n", encoding="utf-8")
        git(other, "add", "-A")
        git(other, *GIT_ID, "commit", "-qm", "init")
        other_wt = tmp_path / "other-wt"
        git(other, "worktree", "add", "--quiet", str(other_wt), "-b", "other-branch")
        (repo / ".worktrees" / "evil").symlink_to(other_wt, target_is_directory=True)

        done = self._remove(repo, "evil")
        assert done.returncode != 0
        assert "resolves outside .worktrees/" in done.stdout, (
            f"a symlink out of .worktrees/ was accepted: {done.stdout.strip()!r}"
        )
        assert (other_wt / ".git").is_file(), "another repository's worktree was removed"

    def test_symlink_to_a_plain_directory(self, isolated_git_config, tmp_path):
        repo, _ = make_repo(tmp_path)
        plain = repo / ".worktrees" / "plain"
        plain.mkdir()
        (plain / "keep").write_text("keep\n", encoding="utf-8")
        (repo / ".worktrees" / "evil").symlink_to(plain, target_is_directory=True)

        done = self._remove(repo, "evil")
        assert done.returncode != 0
        assert "is not the worktree for evil" in done.stdout, (
            f"a plain directory was accepted as a worktree: {done.stdout.strip()!r}"
        )
        assert (plain / "keep").read_text(encoding="utf-8") == "keep\n"

    def test_detached_head_is_refused(self, isolated_git_config, tmp_path):
        repo, _ = make_repo(tmp_path)
        detached = repo / ".worktrees" / "evil"
        git(repo, "worktree", "add", "--detach", "--quiet", str(detached), "HEAD")

        done = self._remove(repo, "evil")
        assert done.returncode != 0
        assert "is not the worktree for evil" in done.stdout, (
            f"a detached worktree was removed as if it were branch evil: "
            f"{done.stdout.strip()!r}"
        )
        assert (detached / ".git").is_file(), "the detached worktree was removed"


class TestDeletedBranchRefIdentity:
    """A worktree whose branch ref is gone still identifies as that branch.

    `symbolic-ref --short -q HEAD` reads HEAD's symbolic target, which survives
    `git update-ref -d refs/heads/<branch>` — so the identity guard passes and
    the failure is git's own dirty-tree refusal, not a misidentification. The
    sequence below is the one the skill documents for this state: the unforced
    removal refuses, the documented `--force` retry removes, and Step 3 then
    finds no branch left to delete.
    """

    def test_deleted_ref_identity_passes_and_force_is_the_documented_exit(
        self, isolated_git_config, tmp_path
    ):
        repo, worktree = make_repo(tmp_path)
        text = CLEANUP_SKILL_FILE.read_text(encoding="utf-8")

        deleted = git(repo, "update-ref", "-d", f"refs/heads/{BRANCH}", check=False)
        assert deleted.returncode == 0, deleted.stderr
        assert (
            git(worktree, "symbolic-ref", "--short", "-q", "HEAD").stdout.strip()
            == BRANCH
        ), "the worktree no longer names its branch symbolically"

        refused = run_fence(cleanup_remove_fence(text), repo)
        assert refused.returncode == 128, (
            f"the unforced removal should fail as dirty, got rc={refused.returncode}: "
            f"stdout={refused.stdout!r} stderr={refused.stderr!r}"
        )
        assert "is not the worktree for" not in refused.stdout, (
            "the identity guard refused a worktree whose symbolic HEAD still "
            f"names the branch: {refused.stdout!r}"
        )
        assert "contains modified or untracked files" in refused.stderr, (
            f"the failure is not git's dirty-tree refusal: {refused.stderr!r}"
        )
        assert worktree.exists()

        shipped = cleanup_remove_fence(text)
        forced_fence = shipped.replace(
            'git worktree remove "$REPO_ROOT/.worktrees/$BRANCH"',
            'git worktree remove --force "$REPO_ROOT/.worktrees/$BRANCH"',
        )
        assert forced_fence != shipped, (
            "the shipped removal command changed, so the documented --force "
            "retry no longer applies"
        )
        forced = run_fence(forced_fence, repo)
        assert forced.returncode == 0, (
            f"the documented --force retry failed: "
            f"stdout={forced.stdout!r} stderr={forced.stderr!r}"
        )
        assert not worktree.exists()

        delete = run_fence(cleanup_delete_fence(text), repo)
        assert delete.returncode != 0
        assert f"branch '{BRANCH}' not found" in delete.stderr, (
            f"Step 3 did not report the missing branch: {delete.stderr!r}"
        )


    def test_no_tracked_files_removes_cleanly_and_step_3_reports_the_missing_branch(
        self, isolated_git_config, tmp_path
    ):
        """The other half of the deleted-ref row: no tracked files, clean removal."""
        repo, worktree = make_empty_tree_repo(tmp_path)
        text = CLEANUP_SKILL_FILE.read_text(encoding="utf-8")
        deleted = git(repo, "update-ref", "-d", f"refs/heads/{BRANCH}", check=False)
        assert deleted.returncode == 0, deleted.stderr
        assert git(worktree, "ls-files").stdout.strip() == "", (
            "the fixture's worktree is not empty, so it does not reach the "
            "no-tracked-files outcome this arm pins"
        )

        removed = run_fence(cleanup_remove_fence(text), repo)
        assert removed.returncode == 0, (
            "a worktree with no tracked files should remove cleanly after its "
            f"branch ref is deleted. stdout={removed.stdout!r} "
            f"stderr={removed.stderr!r}"
        )
        assert not worktree.exists()

        delete = run_fence(cleanup_delete_fence(text), repo)
        assert delete.returncode != 0, (
            f"Step 3 deleted a branch that does not exist: {delete.stdout!r}"
        )
        assert f"branch '{BRANCH}' not found" in delete.stderr, (
            f"Step 3 did not report the missing branch: {delete.stderr!r}"
        )

    def test_a_ref_existence_guard_reds_the_no_tracked_files_arm(
        self, isolated_git_config, tmp_path
    ):
        """Mutation: requiring the branch ref to exist changes the outcome."""
        repo, worktree = make_empty_tree_repo(tmp_path)
        text = CLEANUP_SKILL_FILE.read_text(encoding="utf-8")
        deleted = git(repo, "update-ref", "-d", f"refs/heads/{BRANCH}", check=False)
        assert deleted.returncode == 0, deleted.stderr

        fence = cleanup_remove_fence(text)
        identity = (
            '[ "$(git -C "$TARGET" symbolic-ref --short -q HEAD 2>/dev/null)" '
            '= "$BRANCH" ]'
        )
        mutated = fence.replace(
            identity,
            identity
            + ' && git -C "$TARGET" rev-parse --verify --quiet '
            '"refs/heads/$BRANCH" >/dev/null',
        )
        assert mutated != fence, (
            "the identity guard changed shape; this mutation no longer measures "
            "the regression it names"
        )
        done = run_fence(mutated, repo)
        assert done.returncode != 0, (
            "the ref-existence guard did not change the outcome, so the arm "
            "above would not discriminate"
        )
        assert "is not the worktree for" in done.stdout, (
            f"the mutation failed for another reason: {done.stdout!r}"
        )
        assert worktree.exists(), "the mutated guard removed the worktree anyway"


class TestTemplateLessRepo:
    """`git init --template=` leaves no `.git/info/`; the fence must create it.

    Without the parent directory the exclude write fails, so the fence warns
    and skips the link — a template-less checkout would never get a venv.
    """

    def test_exclude_parent_is_created_and_the_link_is_made(
        self, isolated_git_config, tmp_path
    ):
        repo, worktree = make_template_less_repo(tmp_path)
        assert not (repo / ".git" / "info").exists()

        done = run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), repo)
        assert done.returncode == 0, f"the fence failed: {done.stderr.strip()}"
        assert "Venv link: .venv -> " in done.stdout
        assert (worktree / ".venv").is_symlink()
        exclude = repo / ".git" / "info" / "exclude"
        assert exclude.is_file(), "the fence did not create .git/info/exclude"
        assert "/.venv" in exclude.read_text(encoding="utf-8")


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
        _repo, worktree = make_repo(tmp_path)
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
    TRACKED_WARNING = (
        "WARNING: .venv is NOT ignored in this worktree because it is tracked — "
        "git add -A can commit changes to it; clear it with git rm --cached .venv"
    )

    def test_tracked_link_gets_the_not_ignored_warning(
        self, isolated_git_config, tmp_path
    ):
        """A tracked link is invisible to porcelain; the skill must warn loudly.

        The whole cause-specific sentence is pinned, not the shared prefix: the
        generic untracked branch prints the same prefix, and the remedy
        (`git rm --cached .venv`) is the actionable half of this warning.
        """
        repo, worktree = make_repo(tmp_path, tracked_venv_link=True)
        assert is_tracked(worktree)
        done = run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), repo)
        assert "Venv link: .venv already exists in the worktree" in done.stdout
        assert self.TRACKED_WARNING in done.stdout, (
            "the cause-specific tracked warning did not fire; the generic branch "
            f"shares its prefix, so the remedy would vanish silently. "
            f"stdout={done.stdout.strip()!r}"
        )

    def test_removing_the_tracked_leg_reds_the_pin(
        self, isolated_git_config, tmp_path
    ):
        """Mutation: the tracked leg is what makes the pin above discriminate."""
        repo, _ = make_repo(tmp_path, tracked_venv_link=True)
        fence = fence_for(SKILL_FILE.read_text(encoding="utf-8"))
        mutation = (
            'elif git -C "$WORKTREE" ls-files --error-unmatch -- .venv '
            ">/dev/null 2>&1; then"
        )
        mutated = fence.replace(mutation, "elif false; then")
        assert mutated != fence, (
            "the tracked-warning leg changed shape; this mutation no longer "
            "measures the regression it names"
        )
        done = run_fence(mutated, repo)
        assert "WARNING: .venv is NOT ignored in this worktree" in done.stdout, (
            "the mutated fence stopped warning entirely"
        )
        assert self.TRACKED_WARNING not in done.stdout, (
            "the mutation did not remove the cause-specific warning, so the pin "
            f"above would not discriminate. stdout={done.stdout.strip()!r}"
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


class TestPreExistingUnignoredPathIsKept:
    """A pre-existing untracked `.venv` is left in place and reported.

    The fence creates nothing here, so nothing may be removed; the warning must
    still name the consequence. No fixture combined a pre-existing path with a
    negated rule before, so this warning branch was never reached.
    """

    def test_pre_existing_path_stays_and_the_unignored_warning_names_it(
        self, isolated_git_config, tmp_path
    ):
        repo, worktree = make_repo(tmp_path, gitignore=".worktrees/\n!.venv\n")
        pre = worktree / ".venv"
        pre.mkdir()
        (pre / "marker").write_text("local venv\n", encoding="utf-8")

        done = run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), repo)
        assert done.returncode == 0, f"the fence failed: {done.stderr.strip()}"
        assert "Venv link: .venv already exists in the worktree" in done.stdout, (
            f"a pre-existing path was not left alone: {done.stdout.strip()!r}"
        )
        assert (
            "an untracked unignored path also makes git worktree remove refuse "
            "without --force"
        ) in done.stdout, (
            "the pre-existing-untracked warning branch did not fire; the fence "
            f"either stayed silent or named the wrong cause. "
            f"stdout={done.stdout.strip()!r}"
        )
        assert pre.is_dir() and not pre.is_symlink(), (
            "the pre-existing path was replaced"
        )
        assert (pre / "marker").read_text(encoding="utf-8") == "local venv\n"


class TestNoVenvExistsWarning:
    """The warning branch reached when the link could not be created.

    With `ln` absent the creation fails, and a negated `!.venv` rule makes
    `check-ignore` fail too. The fence must then say that there is nothing to
    commit — not claim it removed a link that never existed, and not stay
    silent. PATH is scoped to the one subprocess; the test process's PATH is
    never mutated, so later arms are unaffected.
    """

    def test_ln_failure_with_a_negated_rule_warns_and_leaves_no_link(
        self, isolated_git_config, tmp_path
    ):
        repo, worktree = make_repo(tmp_path, gitignore=".worktrees/\n!.venv\n")
        done = run_fence_without_tool(
            fence_for(SKILL_FILE.read_text(encoding="utf-8")), repo, "ln"
        )
        assert done.returncode == 0, f"the fence failed: {done.stderr.strip()}"
        assert "WARNING: could not create the .venv link" in done.stdout, (
            f"the creation failure was not reported: {done.stdout.strip()!r}"
        )
        assert (
            "WARNING: .venv is NOT ignored in this worktree, but no .venv "
            "exists there — nothing was created to commit"
        ) in done.stdout, (
            "the no-venv warning branch did not fire; the fence either claimed "
            f"a removal or stayed silent: {done.stdout.strip()!r}"
        )
        assert not os.path.lexists(worktree / ".venv"), (
            "a link exists even though `ln` was absent"
        )


# ---------------------------------------------------------------------------
# The cleanup contract and the write-failure branch
# ---------------------------------------------------------------------------


class TestRemovalSafety:
    """The ignore rule is what makes the SHIPPED cleanup fence work unforced.

    An unignored link is an untracked file, so `git worktree remove` refuses.
    That is the failure mode this arm guards, and it also pins the safety
    property: removal unlinks the symlink rather than following it, so the
    main checkout's real `.venv` survives. The arm runs the fence the skill
    ships, not a direct `git worktree remove` — the guard and the command are
    what cleanup actually executes.
    """

    def test_removal_succeeds_without_force_and_leaves_the_target_intact(
        self, isolated_git_config, tmp_path
    ):
        repo, worktree = make_repo(tmp_path)
        run_fence(fence_for(SKILL_FILE.read_text(encoding="utf-8")), repo)
        marker = repo / ".venv" / "marker"
        assert marker.is_file()

        done = run_fence(
            cleanup_remove_fence(CLEANUP_SKILL_FILE.read_text(encoding="utf-8")), repo
        )
        assert done.returncode == 0, (
            "the shipped cleanup fence refused without --force, so the ignored "
            f"link no longer licenses cleanup. stdout={done.stdout.strip()!r} "
            f"stderr={done.stderr.strip()!r}"
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
        assert config.get("pythonVersion") == "3.12", (
            f"{rel_path}: pythonVersion must stay pinned to 3.12; an unset or "
            "drifted value silently changes which grammar pyright accepts"
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

    def test_shipped_fence_is_green_on_all_four_properties(
        self, isolated_git_config, tmp_path
    ):
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
