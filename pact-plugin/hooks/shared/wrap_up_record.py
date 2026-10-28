#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/shared/wrap_up_record.py
Summary: Wrap-up's PR read and its session_end record. Runs ONE
         `gh pr view` from the current directory, maps the result to a PR
         state, writes wrap-up's `session_end` event (source "wrap-up",
         pr_state, and pr_number when there is a PR), reads it back, and
         prints what step 6 of /PACT:wrap-up needs to choose its branch.
Used by: commands/wrap-up.md step 5, from inside the feature worktree:
         python3 "{plugin_root}/hooks/shared/wrap_up_record.py" --session-dir '{session_dir}'

When the current directory gives no PR (NONE) or no answer (UNKNOWN, as
outside any git repo), it reads the PR of the latest `review_dispatch` in the
journal by number instead, naming the repo from that event's pr_url; with no
parseable pr_url there is no fallback. On NONE it also skips a PR whose
wrap-up already recorded it MERGED from the current directory after that
review: the session has moved on to a feature with no PR. A record read from
the review does not skip it, since step 6 reruns this command after one. A
fallback that cannot confirm that PR records UNKNOWN, never NONE. When the
current directory is that PR's worktree (its checked-out branch is the PR's
head branch, and one of its remotes is the PR's repo), the PR read is the
current directory's.

Each read retries once over REST (gh_helpers.rest_json) when `gh pr view`
reaches gh and fails for any reason other than "no pull requests found":
some sessions refuse GraphQL, which `gh pr view` needs. The current
directory's branch is looked up the way `gh pr view` looks it up (see
_read_branch_over_rest). A REST answer that does not read leaves the read
UNKNOWN, as before.

Run by file path, not `python3 -m`: the current directory must stay the
worktree (gh resolves the PR from it), and `-m` would put that directory
first on sys.path, where a consumer repo's own `shared/` would shadow this
package.

Output, one `key=value` line each, always in this order (a value is empty
when unknown):
    pr_state=<MERGED|OPEN|CLOSED|NONE|UNKNOWN>
    pr_number=<int>
    branch=<headRefName>
    head_owner=<headRepositoryOwner.login>
    head_repo=<headRepository.name>
    pr_source=<cwd|review>

pr_source is review when the fallback ran and its result replaced the
current directory's read (also when the fallback itself ended UNKNOWN), unless
the current directory is that PR's worktree, and cwd otherwise. The
session_end event records it too.

Exit codes: 0 the event was written and read back; 1 the write or the
read-back failed (the lines are still printed, the reason is on stderr);
2 a usage error (a --session-dir that is not an existing absolute directory).

The readers are session_journal.wrap_up_closed (session_end.py and
session_resume.py).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from urllib.parse import quote

if __name__ == "__main__":
    # Run as a file: put hooks/ (this file's grandparent) first, so `shared`
    # is this plugin's package.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Module imports, not names: a test that patches a function on its source
# module (session_journal.append_event, gh_helpers.run_gh) must reach this too.
from shared import backlog, gh_helpers, git_helpers, session_journal  # noqa: E402

GH_JSON = ["--json", "number,state,headRefName,headRepository,headRepositoryOwner"]
GH_ARGS = ["pr", "view", *GH_JSON]
# owner/repo from a review_dispatch pr_url; each part starts alphanumeric.
PR_URL_RE = re.compile(
    r"^https://github\.com/([A-Za-z0-9][A-Za-z0-9_.-]*)/([A-Za-z0-9][A-Za-z0-9_.-]*)/pull/\d+/?$"
)
# owner/repo at the end of a git remote URL (scp-form, https or ssh), .git optional.
REMOTE_URL_RE = re.compile(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$")
GH_TIMEOUT_S = 15
NO_PR_MARKER = "no pull requests found"
PULL_HEAD_REF_RE = re.compile(r"^refs/pull/(\d+)/head$")
PR_STATES = ("MERGED", "OPEN", "CLOSED")
FIELDS = ("pr_state", "pr_number", "branch", "head_owner", "head_repo", "pr_source")


def read_pr(cwd: str | None = None) -> dict:
    """The PR for the current branch, mapped: pr_state is MERGED, OPEN or
    CLOSED from gh; NONE when gh says no pull request exists; UNKNOWN on any
    other failure or an answer this cannot read. pr_number is an int, present
    only for the three gh states."""
    result = gh_helpers.run_gh(GH_ARGS, timeout=GH_TIMEOUT_S, cwd=cwd)
    if not _rest_retry(result):
        return _map(result)
    return _read_branch_over_rest(cwd)


def read_reviewed_pr(session_dir: str, *, skip_if_wrapped: bool = False) -> dict | None:
    """The PR of the latest review_dispatch in the journal, read by number
    and repo: the fallback when the current directory gives no PR or no
    answer (wrap-up run outside the feature worktree, or outside any repo).

    None, so the current-directory result stands, when there is no
    review_dispatch with an int pr_number, or when its pr_url does not parse
    to owner/repo. Never read the number without --repo: run from another
    repo, a bare read would find that repo's PR with the same number, and a
    MERGED answer there would send wrap-up's cleanup to the wrong branch.

    With `skip_if_wrapped`, also None when a wrap-up already recorded that PR
    MERGED from the current directory at or after its review
    (session_journal.wrap_up_closed): the current directory's NONE is then the
    truth about a new, PR-less feature. A record read from the review (this
    run's own, before step 6 reruns it from the worktree) does not count.

    Anything short of a clear answer about THAT PR is UNKNOWN, never NONE:
    the PR was reviewed, so it exists."""
    reviews = session_journal.read_events_from(session_dir, "review_dispatch")
    number = reviews[-1].get("pr_number") if reviews else None
    if not isinstance(number, int) or isinstance(number, bool):
        return None
    url = reviews[-1].get("pr_url")
    match = PR_URL_RE.match(url) if isinstance(url, str) else None
    if not match:
        return None
    if skip_if_wrapped:
        ends = [e for e in session_journal.read_events_from(session_dir, "session_end")
                if e.get("pr_source") != "review"]
        if session_journal.wrap_up_closed(ends, number, reviews[-1].get("ts")):
            return None
    args = ["pr", "view", str(number), "--repo", f"{match.group(1)}/{match.group(2)}"]
    result = gh_helpers.run_gh([*args, *GH_JSON], timeout=GH_TIMEOUT_S)
    pr = _map(result)
    if _rest_retry(result):
        path = f"repos/{match.group(1)}/{match.group(2)}/pulls/{number}"
        pr = _map_rest(gh_helpers.rest_json(path, timeout=GH_TIMEOUT_S))
    if pr["pr_state"] == "NONE" or pr.get("pr_number") != number:
        return {"pr_state": "UNKNOWN"}
    pr["base_repo"] = (match.group(1), match.group(2))
    return pr


def in_pr_worktree(pr: dict) -> bool:
    """Whether the current directory is this PR's worktree: its checked-out
    branch is exactly the PR's head branch, and one of its remotes names
    exactly the PR's base or head owner/repo."""
    branch = git_helpers.run_git(["symbolic-ref", "--short", "-q", "HEAD"])
    if branch is None or branch.returncode != 0 or not pr.get("branch"):
        return False
    if branch.stdout.strip() != pr["branch"]:
        return False
    urls = git_helpers.run_git(["config", "--get-regexp", r"^remote\..*\.url$"])
    if urls is None or urls.returncode != 0:
        return False
    repos = {pr.get("base_repo"), (pr.get("head_owner"), pr.get("head_repo"))}
    for line in urls.stdout.splitlines():
        found = REMOTE_URL_RE.search(line.split(" ", 1)[-1].strip())
        if found and (found.group(1), found.group(2)) in repos:
            return True
    return False


def _map(result) -> dict:
    """Map one `gh pr view --json ...` result (or None) to the PR fields."""
    pr: dict = {"pr_state": "UNKNOWN"}
    if result is None:
        return pr
    if result.returncode != 0:
        if NO_PR_MARKER in (result.stderr or "").lower():
            pr["pr_state"] = "NONE"
        return pr
    try:
        data = json.loads(result.stdout)
    except (TypeError, ValueError):
        return pr
    if not isinstance(data, dict):
        return pr
    state, number = data.get("state"), data.get("number")
    if state not in PR_STATES or not isinstance(number, int) or isinstance(number, bool):
        return pr
    owner = data.get("headRepositoryOwner")
    repo = data.get("headRepository")
    branch = data.get("headRefName")
    pr.update(
        pr_state=state,
        pr_number=number,
        branch=branch if isinstance(branch, str) else "",
        head_owner=owner.get("login", "") if isinstance(owner, dict) else "",
        head_repo=repo.get("name", "") if isinstance(repo, dict) else "",
    )
    return pr


def _git(cwd: str | None, *args: str) -> str | None:
    """One git command's trimmed stdout in `cwd`, or None when it fails (an
    unset config key exits 1)."""
    anchor = ["-C", cwd] if cwd else []
    out = git_helpers.run_git([*anchor, *args])
    return out.stdout.strip() if out is not None and out.returncode == 0 else None


def _obj(value) -> dict:
    return value if isinstance(value, dict) else {}


def _remote_value(cwd: str | None, value: str | None, *, name_only: bool = False):
    """A remote config value, read as gh reads it. Returns False when it is
    unset, else the github.com (owner, repo) it resolves to, or None when it
    resolves to none (decline).

    For branch.<b>.remote and pushRemote: a value holding ":" is a URL, and
    "." or a path starting "./" or "/" counts as unset. Anything else (and
    every remote.pushDefault value, which gh reads only as a name) names a
    remote, whose URL `git remote get-url` reads with insteadOf applied, as
    gh's remote list has it. The URL is parsed, never printed."""
    if not value:
        return False
    if not name_only and (value == "." or value.startswith(("./", "/"))):
        return False
    url = value if ":" in value and not name_only else _git(cwd, "remote", "get-url", value)
    return backlog._slug_from_remote_url(url)


def _push_target(cwd: str | None, branch: str, merge: str) -> tuple[str, tuple[str, str] | None] | None:
    """Where gh expects `branch` to be pushed, as gh's
    tryDetermineDefaultPushTarget decides it: the remote branch name and the
    head repo, or None to decline. The repo is None when no remote is set,
    which gh reads as the base repo.

    1. `<branch>@{push}` when it resolves to refs/remotes/<remote>/<branch>.
    2. Otherwise the branch name is the local one, or the merge ref's branch
       when push.default is upstream or tracking; and the remote is the first
       set of branch.<b>.pushRemote, remote.pushDefault, branch.<b>.remote."""
    pushed = _git(cwd, "rev-parse", "--symbolic-full-name", f"{branch}@{{push}}") or ""
    parts = pushed.splitlines()[0].split("/", 3) if pushed else []
    if len(parts) == 4 and parts[:2] == ["refs", "remotes"] and parts[2] and parts[3]:
        repo = _remote_value(cwd, parts[2], name_only=True)
        return (parts[3], repo) if repo else None
    remote_branch = branch
    if _git(cwd, "config", "--get", "push.default") in ("upstream", "tracking"):
        remote_branch = merge.removeprefix("refs/heads/") or branch
    for key in (f"branch.{branch}.pushremote", "remote.pushdefault", f"branch.{branch}.remote"):
        repo = _remote_value(cwd, _git(cwd, "config", "--get", key), name_only=key == "remote.pushdefault")
        if repo is not False:
            return (remote_branch, repo) if repo else None
    return (remote_branch, None)


def _read_branch_over_rest(cwd: str | None) -> dict:
    """The current branch's PR over REST, found as `gh pr view` (v2.103.3,
    pkg/cmd/pr/shared/finder.go and find_refs_resolution.go) finds it.

    A merge ref refs/pull/<N>/head names PR N. Otherwise the head is
    `<owner>:<remote branch>` for a head repo other than the base, and the
    bare remote branch for the base repo or no remote. Of the PRs on the base
    repo with that head branch (30, newest first), the OPEN ones go first
    (a stable sort), and the first whose head label matches is taken, unless
    it is closed or merged and its head is the base's default branch. None
    matching is NONE. A remote gh could not resolve to a github.com repo
    declines (UNKNOWN), and so does no branch."""
    unknown = {"pr_state": "UNKNOWN"}
    branch = _git(cwd, "symbolic-ref", "--short", "-q", "HEAD")
    if not branch:
        return unknown
    merge = _git(cwd, "config", "--get", f"branch.{branch}.merge") or ""
    numbered = PULL_HEAD_REF_RE.match(merge)
    if numbered:
        return _map_rest(gh_helpers.rest_json(
            f"repos/{{owner}}/{{repo}}/pulls/{numbered.group(1)}", timeout=GH_TIMEOUT_S, cwd=cwd))
    target = _push_target(cwd, branch, merge)
    if target is None:
        return unknown
    remote_branch, head = target
    owner = head[0] if head else "{owner}"
    pulls = gh_helpers.rest_json(
        f"repos/{{owner}}/{{repo}}/pulls?head={owner}:{quote(remote_branch, safe='')}"
        "&state=all&per_page=30", timeout=GH_TIMEOUT_S, cwd=cwd)
    if not isinstance(pulls, list) or not all(isinstance(p, dict) for p in pulls):
        return unknown
    for pull in sorted(pulls, key=lambda p: p.get("state") != "open"):
        head_part, base_part = _obj(pull.get("head")), _obj(pull.get("base"))
        base_repo = str(_obj(base_part.get("repo")).get("full_name", "")).lower()
        wanted = remote_branch
        if head and f"{head[0]}/{head[1]}".lower() != base_repo:
            wanted = f"{head[0]}:{remote_branch}"
        cross = str(_obj(head_part.get("repo")).get("full_name", "")).lower() != base_repo
        label = head_part.get("label") if cross else head_part.get("ref")
        if label != wanted:
            continue
        if pull.get("state") != "open" and _obj(base_part.get("repo")).get("default_branch") == wanted:
            continue
        return _map_rest(pull)
    return {"pr_state": "NONE"}


def _rest_retry(result) -> bool:
    """Whether a `gh pr view` result is retried over REST: it reached gh,
    exited non-zero, and did not say that no pull request exists."""
    return (result is not None and result.returncode != 0
            and NO_PR_MARKER not in (result.stderr or "").lower())


def _map_rest(body) -> dict:
    """Map a REST pull body, or a list of them newest first (the first is
    taken, and an empty list is NONE), to the PR fields. Anything that does
    not read is UNKNOWN, which is also what the retried fast read mapped to."""
    if isinstance(body, list):
        if not body:
            return {"pr_state": "NONE"}
        body = body[0]
    state = gh_helpers.rest_pr_state(body)
    number = body.get("number") if isinstance(body, dict) else None
    if not state or not isinstance(number, int) or isinstance(number, bool):
        return {"pr_state": "UNKNOWN"}
    head = body.get("head")
    head = head if isinstance(head, dict) else {}
    repo = head.get("repo")
    repo = repo if isinstance(repo, dict) else {}
    owner = repo.get("owner")
    branch, name = head.get("ref"), repo.get("name")
    login = owner.get("login") if isinstance(owner, dict) else None
    return {
        "pr_state": state,
        "pr_number": number,
        "branch": branch if isinstance(branch, str) else "",
        "head_owner": login if isinstance(login, str) else "",
        "head_repo": name if isinstance(name, str) else "",
    }


def record(session_dir: str, pr: dict) -> str | None:
    """Write wrap-up's session_end and read it back. Returns None when the
    journal's last session_end carries exactly these fields, else the reason."""
    fields = {"source": "wrap-up", "pr_state": pr["pr_state"]}
    if "pr_number" in pr:
        fields["pr_number"] = pr["pr_number"]
    fields["pr_source"] = pr["pr_source"]
    event = session_journal.make_event("session_end", **fields)
    if not session_journal.append_event(event, session_dir=session_dir):
        return "the session_end write failed (schema or I/O)"
    ends = session_journal.read_events_from(session_dir, "session_end")
    last = ends[-1] if ends else {}
    if any(last.get(k) != v for k, v in fields.items()):
        return "the session_end read back does not match what was written"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record wrap-up's PR state in the session journal.")
    parser.add_argument("--session-dir", required=True)
    args = parser.parse_args(argv)
    session_dir = args.session_dir
    if not os.path.isabs(session_dir) or not os.path.isdir(session_dir):
        print(f"wrap_up_record: --session-dir is not an existing absolute directory: {session_dir!r}",
              file=sys.stderr)
        return 2
    pr = {**read_pr(), "pr_source": "cwd"}
    if pr["pr_state"] in ("NONE", "UNKNOWN"):
        # On NONE, a PR whose wrap-up already ran is not reread: the session
        # has moved on to a feature with no PR.
        reviewed = read_reviewed_pr(session_dir, skip_if_wrapped=pr["pr_state"] == "NONE")
        if reviewed is not None:
            source = "cwd" if in_pr_worktree(reviewed) else "review"
            pr = {**reviewed, "pr_source": source}
    failure = record(session_dir, pr)
    for key in FIELDS:
        print(f"{key}={pr.get(key, '')}")
    if failure:
        print(f"wrap_up_record: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
