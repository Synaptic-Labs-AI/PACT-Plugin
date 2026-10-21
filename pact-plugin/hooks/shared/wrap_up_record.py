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
import sys

if __name__ == "__main__":
    # Run as a file: put hooks/ (this file's grandparent) first, so `shared`
    # is this plugin's package.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Module imports, not names: a test that patches a function on its source
# module (session_journal.append_event, gh_helpers.run_gh) must reach this too.
from shared import gh_helpers, session_journal  # noqa: E402

GH_ARGS = ["pr", "view", "--json", "number,state,headRefName,headRepository,headRepositoryOwner"]
GH_TIMEOUT_S = 15
NO_PR_MARKER = "no pull requests found"
PR_STATES = ("MERGED", "OPEN", "CLOSED")
FIELDS = ("pr_state", "pr_number", "branch", "head_owner", "head_repo")


def read_pr(cwd: str | None = None) -> dict:
    """The PR for the current branch, mapped: pr_state is MERGED, OPEN or
    CLOSED from gh; NONE when gh says no pull request exists; UNKNOWN on any
    other failure or an answer this cannot read. pr_number is an int, present
    only for the three gh states."""
    result = gh_helpers.run_gh(GH_ARGS, timeout=GH_TIMEOUT_S, cwd=cwd)
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
    pr = read_pr()
    failure = record(session_dir, pr)
    for key in FIELDS:
        print(f"{key}={pr.get(key, '')}")
    if failure:
        print(f"wrap_up_record: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
