"""
Location: pact-plugin/tests/test_pr_state_rest_fallback.py
Summary: Every hook reader of PR state, run against a fake `gh` that refuses
         GraphQL (`gh pr view` and `gh api graphql` fail) but answers REST
         (`gh api repos/...`), the way sessions that block GraphQL behave.
Used by: pytest.

Each reader must read the same values over REST as over `gh pr view`, and keep
its unreadable answer when REST fails too. The readers: gh_helpers.check_pr_state
(session_end.check_unpaused_pr, session_resume's paused-PR check, and
merge_guard_post's token retirement) and wrap_up_record's two reads.

The fake receives REST paths exactly as the hooks pass them, `{owner}/{repo}`
placeholders included: real gh fills those from the cwd's remotes with the
same repo `gh pr view` picks, which the fake does not model.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from merge_guard_post import write_token
from shared import gh_helpers
from shared.merge_guard_common import extract_command_context

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
RECORD_CLI = HOOKS / "shared" / "wrap_up_record.py"
GUARD_POST = HOOKS / "merge_guard_post.py"
REFUSED = "HTTP 403: GitHub GraphQL is not available from this session\n"
_MERGE = "gh pr " + "merge"

FAKE_GH = """\
import json, os, sys
args = sys.argv[1:]
root = os.environ["FAKE_GH_DIR"]
with open(os.path.join(root, "argv.log"), "a") as log:
    log.write(" ".join(args) + "\\n")
with open(os.path.join(root, "cwd.log"), "a") as log:
    log.write(os.getcwd() + "\\n")
with open(os.path.join(root, "routes.json")) as handle:
    routes = json.load(handle)
if args[:2] == ["pr", "view"]:
    fast = routes.get("numbered" if "--repo" in args else "fast")
    if fast is None:
        sys.stderr.write(routes["refused"])
        sys.exit(1)
    sys.stdout.write(fast.get("stdout", ""))
    sys.stderr.write(fast.get("stderr", ""))
    sys.exit(fast.get("rc", 0))
if args[:2] == ["api", "graphql"]:
    print(json.dumps({"message": "GitHub GraphQL is not available"}))
    sys.exit(1)
if args[:1] == ["api"] and len(args) == 2 and args[1] in routes.get("rest", {}):
    print(json.dumps(routes["rest"][args[1]]))
    sys.exit(0)
print(json.dumps({"message": "Not Found", "status": "404"}))
sys.exit(1)
"""


def _pull(number=61, state="closed", merged=True, ref="feat/x", owner="own", repo="repo-x",
          base="own/repo-x", default_branch="main"):
    """A REST pull body: the single-PR endpoint carries `merged`, a list entry
    only `merged_at`. A head repo other than `base` is a fork (cross-repo)."""
    return {
        "number": number, "state": state, "merged": merged,
        "merged_at": "2026-10-27T11:42:55Z" if merged else None,
        "head": {"ref": ref, "label": f"{owner}:{ref}",
                 "repo": {"name": repo, "full_name": f"{owner}/{repo}", "owner": {"login": owner}}},
        "base": {"repo": {"full_name": base, "default_branch": default_branch}},
    }


def _gql(number=61, state="MERGED", ref="feat/x", owner="own", repo="repo-x"):
    """The same PR as `gh pr view --json number,state,headRefName,...` prints it."""
    return json.dumps({
        "number": number, "state": state, "headRefName": ref,
        "headRepository": {"name": repo}, "headRepositoryOwner": {"login": owner},
    })


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    """A fake gh first on PATH, for in-process readers and subprocesses alike.
    `set(fast=..., numbered=..., rest=...)` writes its answers: `fast` for
    `gh pr view` without --repo, `numbered` with it; None refuses GraphQL."""
    root = tmp_path / "fakegh"
    root.mkdir()
    gh = root / "gh"
    gh.write_text(f"#!{sys.executable}\n{FAKE_GH}")
    gh.chmod(0o755)

    def set_routes(fast=None, numbered=None, rest=None):
        (root / "routes.json").write_text(json.dumps(
            {"fast": fast, "numbered": numbered, "rest": rest or {}, "refused": REFUSED}))

    def calls():
        log = root / "argv.log"
        return log.read_text().splitlines() if log.exists() else []

    set_routes()
    monkeypatch.setenv("PATH", f"{root}{os.pathsep}/usr/bin{os.pathsep}/bin")
    monkeypatch.setenv("FAKE_GH_DIR", str(root))
    return {"root": root, "set": set_routes, "calls": calls}


# ---------------------------------------------------------------------------
# gh_helpers.check_pr_state
# ---------------------------------------------------------------------------

STATES = [
    pytest.param("MERGED", _pull(state="closed", merged=True), id="merged"),
    pytest.param("CLOSED", _pull(state="closed", merged=False), id="closed"),
    pytest.param("OPEN", _pull(state="open", merged=False), id="open"),
]


@pytest.mark.parametrize("state,pull", STATES)
def test_check_pr_state_reads_the_same_state_over_rest(fake_gh, state, pull):
    fake_gh["set"](fast={"stdout": state + "\n"})
    fast = gh_helpers.check_pr_state(61)
    fake_gh["set"](rest={"repos/{owner}/{repo}/pulls/61": pull})
    rest = gh_helpers.check_pr_state(61)

    assert fast == rest == state
    assert fake_gh["calls"]()[-1] == "api repos/{owner}/{repo}/pulls/61"


def test_check_pr_state_reads_rest_in_the_callers_directory(fake_gh, tmp_path):
    """gh fills {owner}/{repo} from the directory it runs in, so the REST read
    must run where the fast read ran (the merge guard passes the command's)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    fake_gh["set"](rest={"repos/{owner}/{repo}/pulls/61": _pull()})
    assert gh_helpers.check_pr_state(61, cwd=str(repo)) == "MERGED"
    cwds = (fake_gh["root"] / "cwd.log").read_text().splitlines()
    assert cwds == [str(repo.resolve())] * 2


def test_check_pr_state_stays_unreadable_when_rest_fails_too(fake_gh):
    assert gh_helpers.check_pr_state(61) == ""
    assert fake_gh["calls"]() == ["pr view 61 --json state --jq .state",
                                  "api repos/{owner}/{repo}/pulls/61"]


def test_check_pr_state_does_not_retry_a_fast_path_that_answered(fake_gh):
    fake_gh["set"](fast={"stdout": "OPEN\n"}, rest={"repos/{owner}/{repo}/pulls/61": _pull()})
    assert gh_helpers.check_pr_state(61) == "OPEN"
    assert len(fake_gh["calls"]()) == 1


def test_check_pr_state_does_not_retry_when_gh_never_ran(fake_gh, monkeypatch):
    """A fast read that never reached gh (a timeout) is not retried, even
    though REST would answer: a second wait would double the hook's."""
    real_run_gh = gh_helpers.run_gh
    fake_gh["set"](rest={"repos/{owner}/{repo}/pulls/61": _pull()})
    monkeypatch.setattr(gh_helpers, "run_gh",
                        lambda args, **kw: None if args[0] == "pr" else real_run_gh(args, **kw))
    assert gh_helpers.check_pr_state(61) == ""
    assert fake_gh["calls"]() == []


@pytest.mark.parametrize("number", ["61/../1", "6 1", "", "٦١"])
def test_check_pr_state_retries_only_an_all_digit_number(fake_gh, number):
    fake_gh["set"](rest={f"repos/{{owner}}/{{repo}}/pulls/{number}": _pull()})
    assert gh_helpers.check_pr_state(number) == ""
    assert not any(call.startswith("api") for call in fake_gh["calls"]())


@pytest.mark.parametrize("body", [
    {"number": 61, "state": "draft"}, {"number": 61}, ["closed"], "MERGED", None,
])
def test_rest_pr_state_reads_nothing_from_an_unknown_shape(body):
    assert gh_helpers.rest_pr_state(body) == ""


def test_rest_json_reads_nothing_from_a_failed_call(fake_gh):
    """A REST error body is JSON too (a 404 prints one); a non-zero exit is
    never read as an answer."""
    assert gh_helpers.rest_json("repos/own/repo-x/pulls/404") is None
    assert fake_gh["calls"]() == ["api repos/own/repo-x/pulls/404"]


def test_a_merge_is_read_from_merged_at_alone():
    """A list entry carries merged_at and no `merged` field."""
    body = {"number": 61, "state": "closed", "merged_at": "2026-10-27T11:42:55Z"}
    assert gh_helpers.rest_pr_state(body) == "MERGED"


# ---------------------------------------------------------------------------
# The callers of check_pr_state
# ---------------------------------------------------------------------------

def test_session_end_warns_on_a_merged_pr_read_over_rest(fake_gh, monkeypatch):
    import session_end

    monkeypatch.setattr(session_end, "read_events", lambda *_a, **_k: [])
    tasks = [{"id": "1", "subject": "x", "status": "completed", "metadata": {"pr_number": 61}}]

    unreadable = session_end.check_unpaused_pr(tasks=tasks, project_slug="proj")
    fake_gh["set"](rest={"repos/{owner}/{repo}/pulls/61": _pull()})
    merged = session_end.check_unpaused_pr(tasks=tasks, project_slug="proj")

    assert unreadable is not None and "pause-mode was not run" in unreadable
    assert merged is not None and "was merged but /PACT:wrap-up did not run" in merged


@pytest.mark.parametrize("merged,expected", [(True, "has been merged"), (False, "has been closed")])
def test_session_resume_reads_a_paused_prs_state_over_rest(fake_gh, merged, expected):
    from shared.session_resume import _interpret_paused_event

    event = {"pr_number": 61, "branch": "feat/x",
             "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    fake_gh["set"](rest={"repos/{owner}/{repo}/pulls/61": _pull(merged=merged)})

    prompt = _interpret_paused_event(event)

    assert prompt is not None and expected in prompt


def _guard_post(tmp_path, command, cwd: Path) -> None:
    payload = {
        "hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": str(cwd),
        "tool_input": {"command": command},
        "tool_response": {"stdout": "", "stderr": "", "interrupted": False},
    }
    env = {**os.environ, "HOME": str(tmp_path), "CLAUDE_CONFIG_DIR": str(tmp_path / ".claude")}
    proc = subprocess.run([sys.executable, str(GUARD_POST)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, cwd=str(cwd),
                          timeout=30, check=False)
    assert proc.returncode == 0, proc.stderr


def _mint(tmp_path, command) -> Path:
    config = tmp_path / ".claude"
    config.mkdir(exist_ok=True)
    assert write_token(extract_command_context(command), token_dir=config)
    return config


@pytest.mark.parametrize("pull,retired", [
    pytest.param(_pull(state="closed", merged=True), True, id="merged-retires"),
    pytest.param(_pull(state="open", merged=False), False, id="queued-open-keeps"),
])
def test_merge_guard_reads_the_state_over_rest(tmp_path, fake_gh, pull, retired):
    """Readable state stays authoritative when it is read over REST: a merge
    the PR shows MERGED retires its token, and one still OPEN (queued) keeps it."""
    command = f"{_MERGE} 61 --squash"
    config = _mint(tmp_path, command)
    fake_gh["set"](rest={"repos/{owner}/{repo}/pulls/61": pull})

    _guard_post(tmp_path, command, cwd=tmp_path)

    assert "api repos/{owner}/{repo}/pulls/61" in fake_gh["calls"]()
    assert bool(list(config.glob("merge-authorized-*.consumed"))) is retired


def test_merge_guard_with_rest_unreadable_ends_as_with_no_gh(tmp_path, fake_gh, monkeypatch):
    """A checkout whose repo REST cannot read (a fork or a second remote that
    gh resolves to a repo without this PR: 404) ends exactly as an unreadable
    state did before the REST path existed."""
    command = f"{_MERGE} 61 --squash"
    refused = tmp_path / "refused"
    refused.mkdir()
    config = _mint(refused, command)
    _guard_post(refused, command, cwd=tmp_path)

    missing = tmp_path / "missing"
    missing.mkdir()
    baseline = _mint(missing, command)
    monkeypatch.setenv("PATH", f"/usr/bin{os.pathsep}/bin")
    _guard_post(missing, command, cwd=tmp_path)

    assert (bool(list(config.glob("merge-authorized-*.consumed")))
            == bool(list(baseline.glob("merge-authorized-*.consumed"))))


# ---------------------------------------------------------------------------
# wrap_up_record
# ---------------------------------------------------------------------------

def _record(tmp_path, cwd: Path) -> dict:
    session = tmp_path / "session"
    session.mkdir(exist_ok=True)
    proc = subprocess.run([sys.executable, str(RECORD_CLI), "--session-dir", str(session)],
                          capture_output=True, text=True, env=dict(os.environ),
                          cwd=str(cwd), timeout=60, check=False)
    return dict(line.split("=", 1) for line in proc.stdout.splitlines())


def _checkout(path: Path, branch: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for args in (["init", "-q"], ["checkout", "-q", "-b", branch]):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
    return path


def _review(tmp_path, number=61, url="https://github.com/own/repo-x/pull/61") -> None:
    session = tmp_path / "session"
    session.mkdir(exist_ok=True)
    with (session / "session-journal.jsonl").open("a") as journal:
        journal.write(json.dumps({"v": 1, "type": "review_dispatch", "pr_number": number,
                                  "pr_url": url, "reviewers": ["x"],
                                  "ts": "2026-04-17T12:00:00Z"}) + "\n")


def _branch_path(head_owner="{owner}", branch="feat%2Fx"):
    return f"repos/{{owner}}/{{repo}}/pulls?head={head_owner}:{branch}&state=all&per_page=30"


BRANCH_PATH = _branch_path()
EXPECTED = {"pr_state": "MERGED", "pr_number": "61", "branch": "feat/x",
            "head_owner": "own", "head_repo": "repo-x", "pr_source": "cwd"}


def test_the_cwd_read_gives_the_same_lines_over_rest(tmp_path, fake_gh):
    worktree = _checkout(tmp_path / "wt", "feat/x")
    fake_gh["set"](fast={"stdout": _gql()})
    fast = _record(tmp_path, worktree)
    fake_gh["set"](rest={BRANCH_PATH: [_pull(), _pull(number=60)]})
    rest = _record(tmp_path, worktree)

    assert fast == rest == EXPECTED
    assert fake_gh["calls"]()[-1] == f"api {BRANCH_PATH}"


def test_the_cwd_read_takes_the_newest_pr_for_the_branch(tmp_path, fake_gh):
    """REST lists a branch's PRs newest first, and `gh pr view` picks the
    newest (measured: a reused branch name with two merged PRs)."""
    worktree = _checkout(tmp_path / "wt", "feat/x")
    fake_gh["set"](rest={BRANCH_PATH: [_pull(number=76), _pull(number=74)]})
    assert _record(tmp_path, worktree)["pr_number"] == "76"


def test_the_cwd_read_with_no_pr_for_the_branch_is_none(tmp_path, fake_gh):
    worktree = _checkout(tmp_path / "wt", "feat/x")
    fake_gh["set"](rest={BRANCH_PATH: []})
    assert _record(tmp_path, worktree)["pr_state"] == "NONE"


def test_a_branch_name_is_quoted_into_the_rest_path(tmp_path, fake_gh):
    worktree = _checkout(tmp_path / "wt", "fix/a&b{owner}")
    _record(tmp_path, worktree)
    assert f"api {_branch_path(branch='fix%2Fa%26b%7Bowner%7D')}" in fake_gh["calls"]()


def test_the_numbered_read_gives_the_same_lines_over_rest(tmp_path, fake_gh):
    _review(tmp_path)
    fake_gh["set"](fast={"rc": 1, "stderr": 'no pull requests found for branch "main"\n'},
                   numbered={"stdout": _gql()})
    fast = _record(tmp_path, tmp_path)
    fake_gh["set"](rest={"repos/own/repo-x/pulls/61": _pull()})
    rest = _record(tmp_path, tmp_path)

    expected = {**EXPECTED, "pr_source": "review"}
    assert fast == rest == expected
    assert fake_gh["calls"]()[-1] == "api repos/own/repo-x/pulls/61"


def test_both_reads_stay_unknown_when_rest_fails_too(tmp_path, fake_gh):
    _review(tmp_path)
    worktree = _checkout(tmp_path / "wt", "feat/x")
    lines = _record(tmp_path, worktree)
    assert (lines["pr_state"], lines["pr_source"]) == ("UNKNOWN", "review")
    assert [c for c in fake_gh["calls"]() if c.startswith("api")] == [
        f"api {BRANCH_PATH}", "api repos/own/repo-x/pulls/61"]


def test_no_pr_found_is_not_retried(tmp_path, fake_gh):
    worktree = _checkout(tmp_path / "wt", "feat/x")
    fake_gh["set"](fast={"rc": 1, "stderr": 'no pull requests found for branch "feat/x"\n'},
                   rest={BRANCH_PATH: [_pull()]})
    assert _record(tmp_path, worktree)["pr_state"] == "NONE"
    assert not any(c.startswith("api") for c in fake_gh["calls"]())


def test_a_wrap_up_read_that_never_reached_gh_is_not_retried():
    """run_gh's None (gh missing, or a timeout) is not retried: a second
    15-second wait would double wrap-up's."""
    from shared import wrap_up_record

    assert wrap_up_record._rest_retry(None) is False
    assert wrap_up_record._rest_retry(
        subprocess.CompletedProcess(["gh"], 1, "", REFUSED)) is True


# ---------------------------------------------------------------------------
# The branch lookup mirrors gh v2.103.3 (pkg/cmd/pr/shared/finder.go
# findForRefs, find_refs_resolution.go tryDetermineDefaultPushTarget)
# ---------------------------------------------------------------------------

def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)


def _number(tmp_path, worktree) -> str:
    return _record(tmp_path, worktree)["pr_number"]


def test_an_open_pr_goes_before_a_newer_closed_one(tmp_path, fake_gh):
    worktree = _checkout(tmp_path / "wt", "feat/x")
    fake_gh["set"](rest={BRANCH_PATH: [_pull(number=76), _pull(number=74, state="open", merged=False),
                                       _pull(number=72, state="open", merged=False)]})
    assert _number(tmp_path, worktree) == "74"


@pytest.mark.parametrize("pulls,expected", [
    pytest.param([_pull(number=70, ref="main")], "NONE", id="merged-pr-from-the-default-branch"),
    pytest.param([_pull(number=70, ref="main"), _pull(number=71, ref="main", state="open", merged=False)],
                 "71", id="an-open-one-still-counts"),
])
def test_a_closed_pr_whose_head_is_the_default_branch_is_skipped(tmp_path, fake_gh, pulls, expected):
    worktree = _checkout(tmp_path / "wt", "main")
    fake_gh["set"](rest={_branch_path(branch="main"): pulls})
    lines = _record(tmp_path, worktree)
    assert (lines["pr_number"] or lines["pr_state"]) == expected


def test_with_no_push_remote_only_a_same_repo_pr_matches(tmp_path, fake_gh):
    worktree = _checkout(tmp_path / "wt", "feat/x")
    fake_gh["set"](rest={BRANCH_PATH: [_pull(number=80, owner="own", repo="fork-of-x"),
                                       _pull(number=60)]})
    assert _number(tmp_path, worktree) == "60"


def _fork_checkout(tmp_path) -> Path:
    worktree = _checkout(tmp_path / "wt", "feat/x")
    _git(worktree, "remote", "add", "origin", "https://github.com/own/repo-x.git")
    _git(worktree, "remote", "add", "fork", "git@github.com:forker/repo-x.git")
    _git(worktree, "remote", "add", "other", "https://github.com/other/repo-x.git")
    return worktree


FORK_PULLS = [_pull(number=60), _pull(number=81, owner="forker")]


@pytest.mark.parametrize("config,owner", [
    pytest.param({"branch.feat/x.pushRemote": "fork", "remote.pushDefault": "other",
                  "branch.feat/x.remote": "other"}, "forker", id="pushRemote-first"),
    pytest.param({"remote.pushDefault": "fork", "branch.feat/x.remote": "other"}, "forker",
                 id="then-remote.pushDefault"),
    pytest.param({"branch.feat/x.remote": "fork"}, "forker", id="then-branch.remote"),
    pytest.param({"branch.feat/x.remote": "https://github.com/forker/repo-x.git"}, "forker",
                 id="a-url-value"),
    pytest.param({"branch.feat/x.pushRemote": ".", "branch.feat/x.remote": "fork"}, "forker",
                 id="a-dot-value-is-unset"),
    pytest.param({"branch.feat/x.remote": "origin"}, "own", id="the-base-repo-remote"),
])
def test_the_head_owner_follows_gh_push_target_order(tmp_path, fake_gh, config, owner):
    worktree = _fork_checkout(tmp_path)
    for key, value in config.items():
        _git(worktree, "config", key, value)
    fake_gh["set"](rest={_branch_path(head_owner=owner): FORK_PULLS})

    lines = _record(tmp_path, worktree)

    assert f"api {_branch_path(head_owner=owner)}" in fake_gh["calls"]()
    assert lines["pr_number"] == ("81" if owner == "forker" else "60")


def test_push_default_upstream_takes_the_merge_refs_branch(tmp_path, fake_gh):
    worktree = _fork_checkout(tmp_path)
    _git(worktree, "config", "push.default", "upstream")
    _git(worktree, "config", "branch.feat/x.remote", "fork")
    _git(worktree, "config", "branch.feat/x.merge", "refs/heads/up-x")
    path = _branch_path(head_owner="forker", branch="up-x")
    fake_gh["set"](rest={path: [_pull(number=82, owner="forker", ref="up-x")]})
    assert _number(tmp_path, worktree) == "82"


def test_a_resolving_push_ref_wins(tmp_path, fake_gh):
    """`<branch>@{push}` names the remote and branch git would push to; a
    push refspec maps local feat/x to the fork's pushed-x."""
    worktree = _fork_checkout(tmp_path)
    _git(worktree, "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-q", "--allow-empty", "-m", "x")
    _git(worktree, "config", "branch.feat/x.pushRemote", "fork")
    _git(worktree, "config", "remote.fork.push", "refs/heads/feat/x:refs/heads/pushed-x")
    _git(worktree, "update-ref", "refs/remotes/fork/pushed-x", "HEAD")
    path = _branch_path(head_owner="forker", branch="pushed-x")
    fake_gh["set"](rest={path: [_pull(number=83, owner="forker", ref="pushed-x")]})
    assert _number(tmp_path, worktree) == "83"


def test_a_pull_head_merge_ref_reads_that_pr_by_number(tmp_path, fake_gh):
    worktree = _checkout(tmp_path / "wt", "pr-7")
    _git(worktree, "config", "branch.pr-7.merge", "refs/pull/7/head")
    fake_gh["set"](rest={"repos/{owner}/{repo}/pulls/7": _pull(number=7)})
    assert _number(tmp_path, worktree) == "7"


@pytest.mark.parametrize("key,value", [
    pytest.param("branch.feat/x.pushRemote", "gone", id="a-remote-that-does-not-exist"),
    pytest.param("branch.feat/x.remote", "https://gitlab.com/forker/repo-x.git", id="a-non-github-url"),
    pytest.param("remote.pushDefault", "https://github.com/forker/repo-x.git",
                 id="remote.pushDefault-is-only-a-name"),
])
def test_a_push_target_gh_could_not_resolve_declines(tmp_path, fake_gh, key, value):
    worktree = _fork_checkout(tmp_path)
    _git(worktree, "config", key, value)
    fake_gh["set"](rest={BRANCH_PATH: [_pull()], _branch_path(head_owner="forker"): FORK_PULLS})
    assert _record(tmp_path, worktree)["pr_state"] == "UNKNOWN"
    assert not any(c.startswith("api") for c in fake_gh["calls"]())
