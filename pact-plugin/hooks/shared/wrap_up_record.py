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

if __name__ == "__main__":
    # Run as a file: put hooks/ (this file's grandparent) first, so `shared`
    # is this plugin's package.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Module imports, not names: a test that patches a function on its source
# module (session_journal.append_event, gh_helpers.run_gh) must reach this too.
from shared import gh_helpers, git_helpers, session_journal  # noqa: E402

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
PR_STATES = ("MERGED", "OPEN", "CLOSED")
FIELDS = ("pr_state", "pr_number", "branch", "head_owner", "head_repo", "pr_source")


def read_pr(cwd: str | None = None) -> dict:
    """The PR for the current branch, mapped: pr_state is MERGED, OPEN or
    CLOSED from gh; NONE when gh says no pull request exists; UNKNOWN on any
    other failure or an answer this cannot read. pr_number is an int, present
    only for the three gh states."""
    return _map(gh_helpers.run_gh(GH_ARGS, timeout=GH_TIMEOUT_S, cwd=cwd))


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
    pr = _map(gh_helpers.run_gh([*args, *GH_JSON], timeout=GH_TIMEOUT_S))
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
