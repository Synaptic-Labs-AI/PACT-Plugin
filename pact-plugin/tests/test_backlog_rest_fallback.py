"""The backlog's tracker layer: the REST fallback when GraphQL is refused (#1667).

Sibling of tests/test_backlog.py. That file covers the backlog as a whole;
this one covers how `show` checks refs against the tracker — the repo-slug
chain, the one-ref classifier, the batched GraphQL fast path, the per-ref REST
fallback with its shared deadline, cap and thread pool, and the write-time
ref normalisation that depends on the same slug. The arms here came in three
waves (the #1667 CODE and TEST phases, then the PR #1674 review cycle, R1 for
production and R2 for these tests) and are grouped by subject, not by wave.

The fixture builders (`_backlog`, `_item`, `_cli_store`, `_backlog_file`,
`_repo`, `_OLD`, `BACKLOG_CLI`) are imported from tests/test_backlog.py, so
there is one copy of each. The tracker guard both files use is
tests/_backlog_tracker_guard.py.

ONE FAKE FAMILY. Every in-process tracker double is `_tracker()`: GraphQL
answers whatever the arm passes (the recorded cloud refusal by default) and
REST answers from a dict of bodies keyed by issue number, with an optional
per-call hook for the arms that hang, fail or answer late. Each call is
recorded as `(argv, kwargs)` under a lock, because REST calls run in a thread
pool. `_rest_calls` / `_rest_paths` read the REST half of that record.

TIMING. The pool and the deadline run on the real clock (`concurrent.futures`
waits on a real condition variable), so the arms that need time use real
waits. Each such arm is built so that LOAD CAN ONLY WIDEN THE MARGIN THE ARM
DEPENDS ON: a "lands after the deadline" condition is produced by a sleep,
whose duration is a lower bound, and every "lands before" window is at least
0.25 s. Where an argument can be read instead of a race won (the wait's
timeout, the pool's worker count), the arm reads the argument.

Mutation record
---------------
Conventions are those of tests/test_backlog.py's record (faithful mutations,
exception kills only where the row's property IS that no exception escapes,
mutate a COPY and run pytest FROM it). Every row below was run against a
scratch copy, one mutation at a time, with the mutant's verdict predicted
first and checked to be the opposite of what the arm asserts.

  #1667 rows (moved from tests/test_backlog.py):
  in-process `gh` guard removed         test_the_tracker_guard_trips_on_an_unstubbed_gh_call
  REST `merged_at` stops meaning MERGED test_the_outcome_table_lands_every_row_in_the_same_bucket_on_both_paths
  REST PR node loses its `merged` key   test_the_outcome_table_lands_every_row_in_the_same_bucket_on_both_paths
  `duplicate` leaves the abandoned set  test_the_outcome_table_lands_every_row_in_the_same_bucket_on_both_paths
  a no-reason close reads as done       test_no_record_reads_done_unless_it_says_completed_or_merged
  the REST pool waits for its workers   test_a_rest_call_hanging_mid_flight_does_not_hold_resolve_refs
  a REST call runs with no timeout      test_the_show_process_exits_within_budget_while_gh_hangs
  bad REST JSON escapes as exception    test_one_failing_rest_call_does_not_take_its_neighbours_with_it
  a worker exception is re-raised       test_one_failing_rest_call_does_not_take_its_neighbours_with_it
  no-stdout REST reads not-resolved     test_one_failing_rest_call_does_not_take_its_neighbours_with_it
  the trigger matches on wording        test_a_refusal_shaped_error_body_falls_back_and_still_ends_unverifiable
  unparseable GraphQL goes to REST      test_unparseable_graphql_output_never_falls_back
  `reconcile` drops project_path        test_reconcile_anchors_the_slug_lookup_on_the_stored_project_path
  urlsplit ValueError escapes           test_slug_and_ref_parsers_refuse_what_they_cannot_read
  `_classify_ref` non-str guard gone    test_slug_and_ref_parsers_refuse_what_they_cannot_read

  R2 rows (this file's own arms, and arms R2 made discriminating again):
  the write path reads the CWD origin   test_the_write_path_reads_the_backlogs_own_origin_not_the_cwd
  a relative project_path reaches git   test_local_repo_slug_reads_origin_of_project_path_with_real_git
  the REST cut is lexicographic         test_rest_fallback_caps_refs_and_reports_the_overflow
  a late call gets only what is left    test_a_call_started_late_still_gets_the_floor_as_its_timeout
  a queued call starts past deadline    test_a_queued_lookup_never_starts_after_the_deadline
  the pool takes one worker per lookup  test_the_pool_never_runs_more_than_the_worker_cap
  the wait ends at the bare deadline    test_r1_an_answer_inside_the_last_call_floor_is_used
  the REST clock restarts after GraphQL test_a_slow_graphql_refusal_leaves_rest_only_the_remaining_budget
  a MERGED node stops reading done      test_no_record_reads_done_unless_it_says_completed_or_merged
  the done flag line is dropped         test_rest_verdicts_render_as_done_and_abandoned_flags
  the abandoned flag reads as done      test_rest_verdicts_render_as_done_and_abandoned_flags
  a foreign slug skips `_valid_slug`    test_hostile_foreign_slugs_never_reach_a_rest_argv
  an undecodable origin parses a slug   test_a_non_utf8_origin_url_yields_no_slug_and_no_crash
  the fake drops worker-thread calls    test_rest_fake_records_from_every_worker_thread

  R3 rows:
  gh repo view host check removed       test_r3_gh_repo_view_is_accepted_only_for_a_github_com_url
  gh repo view stops requesting url     test_r3_gh_repo_view_is_accepted_only_for_a_github_com_url
  host check accepts any *github.com    test_r3_gh_repo_view_is_accepted_only_for_a_github_com_url
  no-slug invalid link reads foreign    test_r3_a_foreign_link_is_checked_even_with_no_local_slug
  no slug short-circuits foreign links  test_r3_a_foreign_link_is_checked_even_with_no_local_slug
                                        test_r3_an_unusable_project_path_still_checks_a_foreign_link
  `_same_repo` turns case-sensitive     test_r3_same_repo_is_the_one_comparison_and_issue_number_is_gone
  `primary` None crashes the cut key    test_r3_a_foreign_link_is_checked_even_with_no_local_slug

  R4 rows:
  `_git_anchor` accepts a broken path   test_r4_a_broken_absolute_project_path_yields_no_slug_and_no_cwd_read
  gh repo view drops its cwd            test_r4_gh_repo_view_runs_in_the_project_not_the_process_cwd
  `_is_github_url` allows userinfo      test_r4_gh_repo_view_url_with_userinfo_or_backslash_is_refused
  `_run_capture` drops cwd              test_r4_gh_repo_view_runs_in_the_project_not_the_process_cwd

The R1 arms (`test_r1_*`) carry the mutations batch R1 ran against them; they
are not re-listed here except where R2 changed the arm. Deliberately NOT a
row: the guard's GH_HOST removal. Deleting it leaves this file green even
with GH_HOST set in the launching shell, because `_run_capture` scrubs GH_HOST
from every child itself (pinned by test_r1_every_gh_api_call_names_github_and_gh_host_is_scrubbed).
The guard line is a second layer, not a pinned property.

Two #1667 rows were dropped rather than moved, because R1 changed the
contract they pinned: "a None REST deadline reads as spent" (the deadline is
now a REQUIRED keyword; the arm pins the TypeError instead) and "an empty
REST batch starts a pool" (the empty batch now returns before the pool, and
the arm still pins no call). The show-process arm had gone vacuous under R1:
its 0.3 s child budget is below the 0.5 s call floor, so no REST call started
and the no-timeout mutant passed. R2 raised the child budget and added a
marker the fake `gh` writes on every REST call, which the arm asserts. The
slow-refusal arm had gone vacuous the same way (0.2 s budget, 0.5 s floor)
and now shrinks the floor below its budget.
"""
import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import threading
import time

import pytest

from shared import backlog

from _backlog_tracker_guard import _gh_tripwire_dir, _no_real_tracker  # noqa: F401  # fixture re-export: the autouse tracker guard applies to this module only by being imported here
from _backlog_tracker_guard import hermetic_git  # noqa: F401  # fixture re-export: requested by name by the real-git arms
from tests.test_backlog import (
    BACKLOG_CLI,
    _OLD,
    _backlog,
    _backlog_file,
    _cli_store,
    _item,
    _repo,
)


# ---------------------------------------------------------------------------
# The one fake family
# ---------------------------------------------------------------------------

# Recorded in a Claude Code cloud session (gh 2.89.0), trimmed to the read
# fields plus one irrelevant field.
_GRAPHQL_REFUSED_STDOUT = json.dumps({
    "message": "GitHub GraphQL is not available from Claude Code sessions; "
               "use the REST API (gh api repos/{owner}/{repo}/...).",
    "documentation_url": "https://docs.anthropic.com/",
})
_REST_BODIES = {
    1666: {"number": 1666, "state": "closed", "state_reason": "completed"},
    1004: {"number": 1004, "state": "closed", "state_reason": "not_planned"},
    886: {"number": 886, "state": "closed", "state_reason": "duplicate"},
    1667: {"number": 1667, "state": "open", "state_reason": None},
    1672: {"number": 1672, "state": "closed", "state_reason": None,
           "pull_request": {"merged_at": "2026-10-02T16:32:00Z"}},
    1626: {"number": 1626, "state": "closed", "state_reason": None,
           "pull_request": {"merged_at": None}},
    1661: {"number": 1661, "state": "open", "state_reason": None,
           "pull_request": {"merged_at": None}},
}
_REST_NOT_FOUND = {"message": "Not Found", "status": "404"}
_INCOMPLETE = {"state": "unverifiable", "reason": backlog._REASON_CALL_INCOMPLETE}


def _is_graphql(argv):
    return list(argv[:3]) == ["gh", "api", "graphql"]


def _rest_number(argv):
    """The issue number of a REST argv: `repos/{o}/{n}/issues/{N}`."""
    return int(argv[2].rsplit("/", 1)[-1])


def _tracker(calls, graphql=_GRAPHQL_REFUSED_STDOUT, bodies=_REST_BODIES,
             rest_hook=None):
    """A `subprocess.run` fake for `gh api`.

    GraphQL answers `graphql` (default: the recorded refusal). REST answers
    the body for its issue number from `bodies`, or the 404 body. `rest_hook`,
    when given, is called as `rest_hook(argv, number, kwargs)` before the
    answer: it may sleep, block or raise, and a non-None return replaces the
    answer. Every call is recorded as `(argv, kwargs)` under a lock.
    """
    lock = threading.Lock()

    def fake_run(command, **kwargs):
        with lock:
            calls.append((list(command), kwargs))
        if _is_graphql(command):
            return subprocess.CompletedProcess(command, 1, stdout=graphql,
                                               stderr="gh: (HTTP 403)")
        number = _rest_number(command)
        if rest_hook is not None:
            replaced = rest_hook(command, number, kwargs)
            if replaced is not None:
                return replaced
        body = bodies.get(number, _REST_NOT_FOUND)
        return subprocess.CompletedProcess(
            command, 0 if number in bodies else 1, stdout=json.dumps(body), stderr="")

    return fake_run


def _rest_calls(calls):
    return [(argv, kwargs) for argv, kwargs in calls if not _is_graphql(argv)]


def _rest_paths(calls):
    return sorted(argv[2] for argv, _ in _rest_calls(calls))


def _this_repo(monkeypatch, slug=("owner", "repo")):
    monkeypatch.setattr(backlog, "_repo_slug", lambda *a, **k: slug)


def _init_repo_with_origin(path, origin):
    """A real repository holding only an `origin` remote (no commit)."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", origin], check=True)
    return path


# ---------------------------------------------------------------------------
# The repo-slug chain: GH_REPO, the origin remote, `gh repo view`
# ---------------------------------------------------------------------------

def test_slug_from_remote_url_accepts_github_forms_and_refuses_the_rest():
    """RED WHEN an accepted github.com form stops parsing, or a non-github
    host, a dot-only name or a malformed path starts parsing."""
    accepted = [
        "https://github.com/o/r",
        "https://github.com/o/r.git",
        "https://github.com/o/r/",
        "https://user@github.com/o/r",
        "https://user:tok@GitHub.com/o/r.git",
        "http://github.com/o/r",
        "ssh://git@github.com/o/r.git",
        "ssh://git@github.com:22/o/r",
        "git@github.com:o/r.git",
        "git://github.com/o/r",
        "  https://github.com/o/r\n",
        "me@github.com:o/r",  # B-F14: any non-empty scp user
    ]
    for url in accepted:
        assert backlog._slug_from_remote_url(url) == ("o", "r"), url

    refused = [
        None, "", "not a url", '{"message": "x"}',
        "https://gitlab.com/o/r", "https://github.example.com/o/r",
        "http://127.0.0.1:8080/git/o/r", "ftp://github.com/o/r",
        "https://github.com/o", "https://github.com/o/r/extra",
        "https://github.com/o/..", "https://github.com/./r",
        'https://github.com/o/r"x', "https://github.com/o/r x",
    ]
    for url in refused:
        assert backlog._slug_from_remote_url(url) is None, url


def test_r1_remote_forms_added_and_ambiguous_ones_refused():
    """B-F14 / S-F6: GitHub's SSH-over-443 host, `git+ssh://` and any scp user
    are github.com; a backslash or a second `@` makes urlsplit and git
    disagree on the host, so such a URL is refused.

    RED WHEN an added form stops parsing or an ambiguous one parses.
    """
    for url in ("ssh://git@ssh.github.com:443/o/r.git", "git@ssh.github.com:o/r",
                "git+ssh://git@github.com/o/r", "org-123@github.com:o/r.git"):
        assert backlog._slug_from_remote_url(url) == ("o", "r"), url
    for url in ("https://evil.com\\@github.com/o/r", "https://a@b@github.com/o/r",
                "git@github.com:o\\r", "https://ssh.example.com/o/r"):
        assert backlog._slug_from_remote_url(url) is None, url


def test_gh_repo_env_is_github_only_and_validated(monkeypatch, tmp_path):
    """RED WHEN GH_REPO stops taking precedence, or a non-github host or an
    invalid slug is accepted from it."""
    assert backlog._slug_from_gh_repo("o/r") == ("o", "r")
    assert backlog._slug_from_gh_repo("github.com/o/r") == ("o", "r")
    assert backlog._slug_from_gh_repo("ghe.example.com/o/r") is None
    assert backlog._slug_from_gh_repo("o") is None
    assert backlog._slug_from_gh_repo("o/..") is None
    assert backlog._slug_from_gh_repo("") is None

    monkeypatch.setenv("GH_REPO", "env-owner/env-repo")
    monkeypatch.setattr(backlog, "_run_capture", lambda *a, **k: pytest.fail(
        "a subprocess ran although GH_REPO settled the slug"))
    # A real directory: since S-R3-1 a project_path that does not exist is
    # unusable and yields no slug before GH_REPO is read.
    assert backlog._repo_slug(str(tmp_path)) == ("env-owner", "env-repo")


def test_local_repo_slug_reads_origin_of_project_path_with_real_git(
    tmp_path, monkeypatch, hermetic_git,
):
    """The real seam: `git -C project_path config --get remote.origin.url`,
    not the CWD. Hermetic: `hermetic_git` removes every config source but the
    repository's own, so a developer's insteadOf rule cannot reach the read.

    THE RELATIVE-PATH REFUSAL IS ASSERTED ON A PATH THAT RESOLVES. `proj`,
    relative to a CWD of `tmp_path`, IS the repository with an origin — so
    only the refusal itself can make the answer None. A relative path that
    does not exist would read None with the refusal deleted too, because git
    fails on it either way.

    RED WHEN the origin read is dropped, unanchored, the credential in the URL
    leaks into the returned slug, or a relative project_path reaches git.
    """
    repo = _init_repo_with_origin(
        tmp_path / "proj", "https://user:secret-token@github.com/Real-Owner/real-repo.git")

    assert backlog._local_repo_slug(str(repo)) == ("Real-Owner", "real-repo")
    monkeypatch.chdir(tmp_path)
    assert (tmp_path / "proj").resolve() == repo.resolve()  # 'proj' resolves here
    assert backlog._local_repo_slug("proj") is None, "a relative project_path reached git"
    # No project_path: git runs in the process CWD, as `gh repo view` did.
    monkeypatch.chdir(repo)
    assert backlog._local_repo_slug(None) == ("Real-Owner", "real-repo")


def test_r1_origin_is_read_raw_not_through_insteadof(tmp_path, hermetic_git):
    """S-F5: `git remote get-url` applies `url.<base>.insteadOf`, so a rule in
    the project's config could point the slug at another repository. The raw
    `remote.origin.url` is read instead. Real git, hermetic config.

    RED WHEN the origin read goes back through insteadOf.
    """
    repo = _repo(tmp_path / "proj")
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True)
    git("remote", "add", "origin", "https://github.com/real/repo.git")
    git("config", "url.https://github.com/attacker/.insteadOf", "https://github.com/real/")

    rewritten = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"],
                               capture_output=True, text=True).stdout
    assert "attacker" in rewritten, "control: the rewrite rule is live"
    assert backlog._origin_repo_slug(str(repo)) == ("real", "repo")


def test_a_non_utf8_origin_url_yields_no_slug_and_no_crash(tmp_path, hermetic_git):
    """A hand-edited `.git/config` can hold bytes that are not UTF-8. The
    origin read decodes with replacement, and the replacement character is
    not a slug character, so the answer is None — never a crash, and never a
    slug carrying the replacement.

    RED WHEN the decoded URL's name is accepted without `_valid_slug`, or the
    decode error escapes `_origin_repo_slug`.
    """
    repo = _init_repo_with_origin(tmp_path / "proj", "https://github.com/o/r")
    config = repo / ".git" / "config"
    raw = config.read_bytes()
    assert b"https://github.com/o/r" in raw, "control: the origin line is where expected"
    config.write_bytes(raw.replace(b"https://github.com/o/r", b"https://github.com/o/r\xff"))

    assert backlog._origin_repo_slug(str(repo)) is None
    assert backlog._local_repo_slug(str(repo)) is None


def test_r1_an_unusable_project_path_yields_no_slug_at_all(monkeypatch):
    """B-F7: a given but unusable `project_path` (relative, empty, not a str)
    refuses the origin rung because it would read the CWD — and `gh repo
    view`, which runs in the CWD, must not be reached either.

    RED WHEN `_repo_slug` falls through to `gh repo view` for such a path.
    """
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.setattr(backlog, "_gh_repo_view_slug", lambda: pytest.fail(
        "an unusable project_path fell through to gh repo view"))
    for bad in ("relative/path", "", 42):
        assert backlog._repo_slug(bad) is None, bad
        assert backlog._local_repo_slug(bad) is None, bad


def test_r3_gh_repo_view_is_accepted_only_for_a_github_com_url(monkeypatch):
    """R3-GHE / BB-3: `gh repo view` resolves from the git remotes, which may
    be a GitHub Enterprise host, while every tracker call pins github.com. Its
    slug is accepted only when the `url` it reports is on github.com.

    RED WHEN the host check is dropped (a GHE slug then queries an unrelated
    github.com repository), or `url` is no longer requested.
    """
    seen = []

    def answer(payload):
        def fake(command, **kwargs):
            seen.append(list(command))
            return json.dumps(payload)
        monkeypatch.setattr(backlog, "_run_capture", fake)
        return backlog._gh_repo_view_slug()

    slug = {"nameWithOwner": "owner/repo"}
    assert answer({**slug, "url": "https://github.com/owner/repo"}) == ("owner", "repo")
    assert seen[-1] == ["gh", "repo", "view", "--json", "nameWithOwner,url"]
    for url in ("https://ghe.example.com/owner/repo", "https://gist.github.com/owner/repo",
                "https://github.com.evil.com/owner/repo", "ssh://github.com/owner/repo",
                "https://[::1/owner/repo", None, 5):
        assert answer({**slug, "url": url}) is None, url
    assert answer(slug) is None, "no url at all must not be trusted"


def test_r4_a_broken_absolute_project_path_yields_no_slug_and_no_cwd_read(
    monkeypatch, tmp_path,
):
    """S-R3-1: an absolute project_path that no longer exists, or holds a NUL
    byte, is unusable: no slug, and `gh repo view` never runs — it would have
    read whatever repository the process CWD sits in.

    RED WHEN `_git_anchor` accepts such a path again, so the chain falls
    through to a CWD-anchored `gh repo view`.
    """
    monkeypatch.setattr(backlog, "_gh_repo_view_slug", lambda *a, **k: pytest.fail(
        "a broken project_path fell through to gh repo view"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail(
        "a broken project_path reached a subprocess"))
    for bad in (str(tmp_path / "moved-away"), str(tmp_path) + "/a\x00b"):
        assert backlog._git_anchor(bad) is None, bad
        assert backlog._repo_slug(bad) is None, bad
        outcome = backlog.resolve_refs(["#1"], project_path=bad)
        assert outcome["#1"]["reason"] == backlog._REASON_NO_TRACKER, bad
    assert backlog._git_anchor(str(tmp_path)) == ["-C", str(tmp_path)]


def test_r4_gh_repo_view_runs_in_the_project_not_the_process_cwd(monkeypatch, tmp_path):
    """S-R3-1: for an existing directory whose origin cannot be read (not a
    repository), `gh repo view` runs with `cwd=project_path`, so it fails
    there instead of answering for the CWD's repository. With no
    project_path it runs in the CWD, as it always did.

    RED WHEN the cwd is dropped from the gh repo view call.
    """
    plain = tmp_path / "plain"
    plain.mkdir()
    seen = []
    answer = {"stdout": ""}

    def fake_run(command, **kwargs):
        seen.append((list(command), kwargs.get("cwd")))
        if command[0] == "git":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 1, stdout=answer["stdout"], stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert backlog._repo_slug(str(plain)) is None
    gh = [cwd for argv, cwd in seen if argv[:3] == ["gh", "repo", "view"]]
    assert gh == [str(plain)], seen

    seen.clear()
    answer["stdout"] = json.dumps({"nameWithOwner": "o/r", "url": "https://github.com/o/r"})
    assert backlog._repo_slug(None) == ("o", "r")
    assert [cwd for argv, cwd in seen if argv[:3] == ["gh", "repo", "view"]] == [None]


def test_r4_gh_repo_view_url_with_userinfo_or_backslash_is_refused(monkeypatch):
    """S-R3-2: `urlsplit` reads `https://evil.com@github.com/...` (and its
    backslash variant) as github.com. GitHub's web URL never carries
    userinfo, so any `@` or backslash is refused, as for remotes.

    RED WHEN `_is_github_url` trusts urlsplit's host for such a URL.
    """
    for url in ("https://evil.com@github.com/o/r", "https://evil.com\\@github.com/o/r",
                "https://user:pw@github.com/o/r", "https://github.com\\o/r"):
        assert not backlog._is_github_url(url), url
        monkeypatch.setattr(backlog, "_run_capture", lambda *a, _u=url, **k: json.dumps(
            {"nameWithOwner": "o/r", "url": _u}))
        assert backlog._gh_repo_view_slug() is None, url
    assert backlog._is_github_url("https://github.com/o/r")


def test_reconcile_anchors_the_slug_lookup_on_the_stored_project_path(
    monkeypatch, tmp_path,
):
    """`reconcile` -> `_ref_flags` -> `resolve_refs` -> `_repo_slug` carries the
    backlog's own `project_path`, so `origin` is read from THIS project and not
    from whatever repository the process CWD sits in.

    RED WHEN any link in that chain drops `project_path` (the slug lookup then
    receives None and falls back to the CWD).
    """
    seen = []

    def recording_slug(project_path=None):
        seen.append(project_path)
        return None

    monkeypatch.setattr(backlog, "_repo_slug", recording_slug)
    data = _backlog(tmp_path, items=[_item(ref="#7")])
    backlog.reconcile(data)

    assert seen == [str(tmp_path)], seen


def test_slug_and_ref_parsers_refuse_what_they_cannot_read(monkeypatch):
    """The defensive returns the tables above do not reach.

    RED WHEN a malformed remote URL raises out of `_slug_from_remote_url`
    (urlsplit raises ValueError on an unclosed IPv6 bracket), a non-string
    ref reaches `.strip()`, or a failed or non-JSON `gh repo view` yields
    anything but None.
    """
    assert backlog._slug_from_remote_url("https://[::1/o/r") is None
    assert backlog._slug_from_gh_repo("github.com/o/r/extra") is None
    assert backlog._local_repo_slug(42) is None
    for not_a_ref in (None, 12, ["#1"]):
        assert backlog._classify_ref(not_a_ref).number is None, not_a_ref

    for stdout in (None, "not json"):
        monkeypatch.setattr(backlog, "_run_capture", lambda *a, _s=stdout, **k: _s)
        assert backlog._gh_repo_view_slug() is None, stdout


# ---------------------------------------------------------------------------
# Reading one ref: the classifier, foreign links, hostile input
# ---------------------------------------------------------------------------

def test_r1_classify_ref_is_the_one_reading_of_a_ref():
    """A-F7 / B-F1 / B-F13 / S-F3 / B-F12: every ref kind, in one table.

    RED WHEN a kind moves: www.github.com or a PR tab link stops being a
    github link, an out-of-range number is looked up, `v1.2/12` reads as a
    link, or a Linear key stops being opaque.
    """
    gh = "https://github.com/o/r"
    table = [
        ("#12", ("bare", 12, "", "")),
        (" 12 ", ("bare", 12, "", "")),
        ("#2147483647", ("bare", 2**31 - 1, "", "")),
        ("#2147483648", ("out_of_range", None, "", "")),
        ("#12345678901", ("out_of_range", None, "", "")),
        (f"{gh}/issues/7", ("github", 7, "o", "r")),
        ("https://www.github.com/o/r/issues/7", ("github", 7, "o", "r")),
        ("WWW.GITHUB.COM/O/R/pull/7", ("github", 7, "O", "R")),
        (f"{gh}/pull/7/files", ("github", 7, "o", "r")),
        (f"{gh}/pull/7/commits?x=1", ("github", 7, "o", "r")),
        (f"{gh}/pull/7/checks#y", ("github", 7, "o", "r")),
        (f"{gh}/issues/99999999999", ("out_of_range", None, "", "")),
        ("https://jira.example.com/browse/12", ("link", None, "", "")),
        ("jira.example.com/browse/12", ("link", None, "", "")),
        (f"{gh}/discussions/12", ("link", None, "", "")),
        ("v1.2/12", ("not_a_ref", None, "", "")),
        ("12/", ("not_a_ref", None, "", "")),
        ("foo/12", ("not_a_ref", None, "", "")),
        ("x" * 600 + "/12", ("not_a_ref", None, "", "")),
        ("ENG-123", ("opaque", None, "", "")),
        ("owner/repo#12", ("opaque", None, "", "")),
        ("²", ("opaque", None, "", "")),
        (None, ("opaque", None, "", "")),
    ]
    for ref, expected in table:
        assert tuple(backlog._classify_ref(ref)) == expected, ref


def test_r1_an_over_long_or_huge_ref_never_crashes_or_stalls(monkeypatch):
    """S-F2 / S-F3 / B-F2: a 5000-digit ref used to raise ValueError from
    int() (CPython's 4300-digit limit) in every `show`, and a long dotted
    first segment backtracked quadratically in the old link regex.

    The time bound is 2 s for work measured at ~0 ms; the quadratic regex took
    many seconds on the 40000-dot ref, so the margin absorbs any CI load and
    still separates the two.

    RED WHEN either reaches a regex or int() again: this arm then raises or
    takes seconds instead of milliseconds.
    """
    hostile = ["#" + "9" * 5000, "foo/" + "9" * 5000, "." * 40000 + " /1",
               "a." * 20000 + "/1"]
    started = time.monotonic()
    for ref in hostile:
        assert backlog._classify_ref(ref).kind == "not_a_ref", ref[:20]
        assert backlog._classify_ref(ref).number is None
    assert time.monotonic() - started < 2.0

    _this_repo(monkeypatch)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail(
        "a non-addressable ref reached the tracker"))
    started = time.monotonic()
    outcome = backlog.resolve_refs(hostile)
    assert time.monotonic() - started < 2.0, "resolving the hostile refs stalled"
    assert all(state == {"state": "unverifiable", "reason": backlog._REASON_NOT_A_REF}
               for state in outcome.values()), outcome


def test_hostile_foreign_slugs_never_reach_a_rest_argv(monkeypatch):
    """A foreign github link's owner and name become a REST URL path, so each
    must pass `_valid_slug` before it is used. Traversal (`..`, `%2e%2e`,
    `b%2F..%2F..`), a template placeholder (`{owner}`) and a query tail
    carrying a gh flag (`--method=DELETE`) must never appear in any argv; an
    owner with a leading `-` is a valid GitHub login character but must stay
    INSIDE the path element, never become an argv element of its own.

    Reproduces the security verify's probe (task #42) as an arm.

    RED WHEN a foreign slug skips `_valid_slug`, or any part of a ref other
    than its owner, name and number reaches the argv.
    """
    calls = []
    _this_repo(monkeypatch, ("o", "n"))
    monkeypatch.setattr(subprocess, "run", _tracker(calls))

    refused = ["https://github.com/../x/issues/1", "https://github.com/%2e%2e/x/issues/2",
               "https://github.com/{owner}/x/issues/3", "https://github.com/a/../issues/5",
               "https://github.com/a/b%2F..%2F..%2Fuser/issues/6",
               "https://github.com/.../x/issues/8"]
    looked_up = {"https://github.com/-X/y/issues/4": "repos/-X/y/issues/4",
                 "https://github.com/a/b/issues/12?per_page=100&x=--method=DELETE":
                     "repos/a/b/issues/12"}
    outcome = backlog.resolve_refs(refused + list(looked_up))

    for ref in refused:
        assert outcome[ref] == {"state": "unverifiable",
                                "reason": backlog._REASON_FOREIGN_REF}, ref
    assert _rest_paths(calls) == sorted(looked_up.values())
    for argv, _ in _rest_calls(calls):
        assert argv[:2] == ["gh", "api"] and argv[2].startswith("repos/"), argv
        assert argv[3:] == ["--hostname", "github.com"], argv
        for part in argv:
            for needle in ("..", "%", "{", "}", "--method", "?", "#"):
                assert needle not in part, (needle, argv)


def test_a_non_decimal_digit_ref_is_stored_verbatim_and_reads_unverifiable(
    monkeypatch,
):
    """'²' passes str.isdigit() but int() rejects it. Stored verbatim on write,
    and the read path reports it unverifiable instead of raising ValueError.

    RED WHEN `_classify_ref` goes back to isdigit(), which crashes `show`.
    """
    assert backlog._field_updates(argparse.Namespace(ref="²"))["ref"] == "²"
    assert backlog._field_updates(argparse.Namespace(ref="#1²"))["ref"] == "#1²"
    assert backlog._classify_ref("²").number is None
    monkeypatch.setattr(backlog, "_repo_slug", lambda *_: pytest.fail(
        "a non-addressable ref reached the tracker"))
    outcome = backlog.resolve_refs(["²", "https://github.com/o/r/issues/²"])
    assert all(state["state"] == "unverifiable" for state in outcome.values()), outcome


def test_a_non_link_ref_and_a_foreign_link_carry_different_reasons(monkeypatch):
    """`12/` is not a link at all; `https://jira.../12` is a link elsewhere.

    RED WHEN the two collapse into one reason, or either is looked up.
    """
    _this_repo(monkeypatch)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail(
        "an unaddressable ref reached the tracker"))

    not_links = ["12/", "foo/12"]
    # A-F9: a github.com link to another repository with a VALID slug is now
    # looked up there, so the github.com arm here uses an invalid slug.
    links = ["https://jira.example.com/browse/12", "jira.example.com/browse/12",
             "github.com/other%20x/repo/issues/12"]
    outcome = backlog.resolve_refs(not_links + links)
    for ref in not_links:
        assert outcome[ref] == {"state": "unverifiable",
                                "reason": backlog._REASON_NOT_A_REF}, ref
    for ref in links:
        assert outcome[ref] == {"state": "unverifiable",
                                "reason": backlog._REASON_FOREIGN_REF}, ref


def test_refs_pointing_outside_this_repository_are_never_looked_up(monkeypatch):
    """RED WHEN another repository's URL, or another tracker's URL ending in
    digits, is queried as this repository's issue of the same number.

    A-F9 (pin flipped by design): a github.com link to ANOTHER repository is
    looked up in THAT repository, never in this one; only a link no tracker
    here can address (another host) keeps the outside-this-repository reason.
    """
    calls = []
    _this_repo(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _tracker(calls))

    other_repo = "https://github.com/other/repo/issues/1666"
    other_host = "https://jira.example.com/browse/1666"
    own = ["https://github.com/OWNER/repo/pull/1672", "1666"]
    outcome = backlog.resolve_refs([other_repo, other_host] + own)

    assert outcome[other_host] == {"state": "unverifiable",
                                   "reason": backlog._REASON_FOREIGN_REF}
    assert outcome[other_repo] == {"state": "done"}
    assert outcome[own[0]] == {"state": "done"}
    assert outcome[own[1]] == {"state": "done"}
    assert _rest_paths(calls) == ["repos/other/repo/issues/1666",
                                  "repos/owner/repo/issues/1666",
                                  "repos/owner/repo/issues/1672"]


def test_r1_a_link_to_another_github_repository_is_checked_there(monkeypatch):
    """A-F9: a github.com link to another repository is looked up in THAT
    repository over REST — even when the batched GraphQL query succeeds for
    this repository's refs — and never enters the GraphQL query. A link whose
    owner or name is not a valid slug is not looked up at all.

    RED WHEN a foreign link is queried in this repository, is folded into the
    GraphQL batch, or an invalid slug reaches a REST path.
    """
    calls = []
    answer = json.dumps({"data": {"repository": {
        "r0": {"state": "OPEN", "stateReason": None}}}})
    _this_repo(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _tracker(calls, graphql=answer))

    foreign = "https://github.com/upstream/thing/issues/1004"
    invalid = "https://github.com/up%20stream/thing/issues/1004"
    outcome = backlog.resolve_refs(["#1667", foreign, invalid])

    assert outcome["#1667"] == {"state": "open"}
    assert outcome[foreign] == {"state": "abandoned", "reason": "not_planned"}
    assert outcome[invalid] == {"state": "unverifiable",
                                "reason": backlog._REASON_FOREIGN_REF}
    graphql = [argv for argv, _ in calls if _is_graphql(argv)]
    assert len(graphql) == 1 and "upstream" not in " ".join(graphql[0])
    assert _rest_paths(calls) == ["repos/upstream/thing/issues/1004"]


def test_r3_a_foreign_link_is_checked_even_with_no_local_slug(monkeypatch):
    """BB-2: a github.com link names its own repository, so it is checked
    there even when this project has no slug. A bare number needs this
    repository's slug, and so does every non-link kind: they read `no
    tracker configured`, and no GraphQL call is made.

    RED WHEN a missing slug short-circuits every ref again, or a bare number
    or an invalid-slug link is looked up with no slug.
    """
    calls = []
    monkeypatch.setattr(backlog, "_repo_slug", lambda *a, **k: None)
    monkeypatch.setattr(subprocess, "run", _tracker(calls))

    foreign = "https://github.com/upstream/thing/pull/1672/files"
    no_tracker = ["#1666", "https://github.com/up%20stream/thing/issues/1",
                  "https://jira.example.com/browse/12", "12/", "#99999999999"]
    outcome = backlog.resolve_refs([foreign, "ENG-1"] + no_tracker)

    assert outcome[foreign] == {"state": "done"}
    for ref in no_tracker:
        assert outcome[ref] == {"state": "unverifiable",
                                "reason": backlog._REASON_NO_TRACKER}, ref
    assert outcome["ENG-1"]["reason"] == backlog._REASON_NO_RESOLUTION
    assert [argv for argv, _ in calls if _is_graphql(argv)] == []
    assert _rest_paths(calls) == ["repos/upstream/thing/issues/1672"]


def test_r3_an_unusable_project_path_still_checks_a_foreign_link(monkeypatch):
    """BB-2 with B-F7: an unusable `project_path` gives no slug (and never
    reaches `gh repo view`), but a foreign link is still checked where it
    points.

    RED WHEN the unusable path suppresses the foreign lookup, or falls
    through to `gh repo view`.
    """
    calls = []
    monkeypatch.setattr(backlog, "_gh_repo_view_slug", lambda: pytest.fail(
        "an unusable project_path fell through to gh repo view"))
    monkeypatch.setattr(subprocess, "run", _tracker(calls))
    foreign = "https://github.com/upstream/thing/issues/1004"
    outcome = backlog.resolve_refs([foreign, "#1"], project_path="relative/path")
    assert outcome[foreign] == {"state": "abandoned", "reason": "not_planned"}
    assert outcome["#1"]["reason"] == backlog._REASON_NO_TRACKER
    assert _rest_paths(calls) == ["repos/upstream/thing/issues/1004"]


def test_issue_comment_and_query_links_address_the_path_number(monkeypatch):
    """A this-repo link with `#fragment` or `?query` is the issue in its PATH.

    RED WHEN such a link reads foreign or unaddressable, when its number is
    taken from the fragment, or when the write path stops collapsing it.
    """
    comment = "https://github.com/owner/repo/issues/1666#issuecomment-1004"
    query = "https://github.com/owner/repo/pull/1672?tab=files"
    assert backlog._classify_ref(comment).number == 1666
    assert backlog._classify_ref("https://github.com/o/r/issues/12#34").number == 12
    assert backlog._ref_targets_repo(comment, "owner", "repo")
    assert not backlog._ref_targets_repo(comment, "other", "repo")

    monkeypatch.setattr(backlog, "_origin_repo_slug", lambda *_: ("owner", "repo"))
    for ref, expected in ((comment, "1666"), (query, "1672")):
        assert backlog._field_updates(argparse.Namespace(ref=ref))["ref"] == expected
    other = "https://github.com/other/repo/issues/1666#issuecomment-1"
    assert backlog._field_updates(argparse.Namespace(ref=other))["ref"] == other

    calls = []
    _this_repo(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _tracker(calls))
    outcome = backlog.resolve_refs([comment, query, other])
    assert outcome[comment] == {"state": "done"}
    assert outcome[query] == {"state": "done"}
    # A-F9 (pin flipped by design): looked up in its own repository.
    assert outcome[other] == {"state": "done"}
    assert _rest_paths(calls) == ["repos/other/repo/issues/1666",
                                  "repos/owner/repo/issues/1666",
                                  "repos/owner/repo/issues/1672"]


def test_r1_an_out_of_range_number_never_enters_the_batched_query(monkeypatch):
    """S-F8 / B-F2b: a number past GraphQL's Int would fail the WHOLE batched
    document, leaving every other ref unresolved. It is refused up front with
    its own reason, and the query carries only in-range numbers.

    RED WHEN the bound is lost and the number is interpolated into the query.
    """
    calls = []
    answer = json.dumps({"data": {"repository": {
        "r0": {"state": "CLOSED", "stateReason": "COMPLETED"}}}})
    _this_repo(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _tracker(calls, graphql=answer))

    outcome = backlog.resolve_refs(["#1666", "#99999999999"])
    assert outcome["#1666"] == {"state": "done"}
    assert outcome["#99999999999"] == {"state": "unverifiable",
                                       "reason": backlog._REASON_NUMBER_RANGE}
    (argv, _), = calls
    assert "99999999999" not in " ".join(argv)


# ---------------------------------------------------------------------------
# Write-time ref normalisation
# ---------------------------------------------------------------------------

def test_r1_a_ref_over_the_length_cap_is_refused_on_write(tmp_path, monkeypatch, capsys):
    """S-F2: `add`/`set` refuse a ref past `_REF_MAX_LENGTH` with the refusal
    exit code and a clear message; nothing is written, nothing truncated.

    RED WHEN an over-long ref is stored (truncated or whole), or a ref at the
    limit is refused.
    """
    path = _backlog_file(tmp_path, monkeypatch, [])
    too_long = "x" * (backlog._REF_MAX_LENGTH + 1)

    assert backlog.main(["add", "Thing", "--ref", too_long]) == backlog._EXIT_REFUSED
    err = capsys.readouterr().err
    assert "refused" in err and str(backlog._REF_MAX_LENGTH) in err
    assert not path.exists() or json.loads(path.read_text(encoding="utf-8"))["items"] == []

    at_limit = "x" * backlog._REF_MAX_LENGTH
    assert backlog.main(["add", "Thing", "--ref", at_limit]) == backlog._EXIT_OK
    item = json.loads(path.read_text(encoding="utf-8"))["items"][0]
    assert item["ref"] == at_limit

    assert backlog.main(["set", item["id"], "--ref", too_long]) == backlog._EXIT_REFUSED
    assert json.loads(path.read_text(encoding="utf-8"))["items"][0]["ref"] == at_limit


def test_r1_write_time_collapse_ignores_gh_repo(monkeypatch):
    """A-F3: a stored ref outlives GH_REPO, so a link collapses to a bare
    number only when it matches the `origin` slug. With GH_REPO pointing
    upstream and origin at a fork, an upstream link is stored verbatim.

    RED WHEN the write path consults GH_REPO again.
    """
    monkeypatch.setenv("GH_REPO", "upstream/repo")
    monkeypatch.setattr(backlog, "_origin_repo_slug", lambda *_: ("fork", "repo"))

    def stored(ref):
        return backlog._field_updates(argparse.Namespace(ref=ref))["ref"]

    upstream = "https://github.com/upstream/repo/issues/5"
    assert stored(upstream) == upstream
    assert stored("https://github.com/fork/repo/pull/5/files") == "5"
    assert stored("#99999999999") == "#99999999999", "out of range is not collapsed"


def test_the_write_path_reads_the_backlogs_own_origin_not_the_cwd(
    tmp_path, tmp_path_factory, monkeypatch, hermetic_git,
):
    """`add` and `set` collapse a this-repo link using the origin of the
    BACKLOG'S project_path, not of the process CWD. End to end through
    `main()` and real git: the backlog's project has origin `o/r`; the CWD is
    a different repository whose origin is `x/y`.

    The CWD repository sits OUTSIDE the project tree on purpose: under it,
    git would walk up to the project's own `.git` and read `o/r` anyway, and
    a CWD-anchored write path would pass.

    RED WHEN `_handle_write_or_show` stops passing the stored project_path to
    the ref normalisation (the link then reads as foreign and is stored
    verbatim instead of as its number).
    """
    _init_repo_with_origin(tmp_path, "https://github.com/o/r.git")
    path = _backlog_file(tmp_path, monkeypatch, [])
    elsewhere = _init_repo_with_origin(
        tmp_path_factory.mktemp("elsewhere") / "cwd", "https://github.com/x/y.git")
    monkeypatch.chdir(elsewhere)
    assert backlog._origin_repo_slug(None) == ("x", "y"), "control: the CWD is x/y"

    assert backlog.main(["add", "Thing", "--ref",
                         "https://github.com/o/r/issues/5"]) == backlog._EXIT_OK
    item = json.loads(path.read_text(encoding="utf-8"))["items"][0]
    assert item["ref"] == "5", "add read the CWD's origin, not the project's"

    assert backlog.main(["set", item["id"], "--ref",
                         "https://github.com/o/r/pull/6/files"]) == backlog._EXIT_OK
    assert json.loads(path.read_text(encoding="utf-8"))["items"][0]["ref"] == "6", (
        "set read the CWD's origin, not the project's")


# ---------------------------------------------------------------------------
# The GraphQL fast path and the fallback trigger
# ---------------------------------------------------------------------------

def test_graphql_request_failed_is_shape_only():
    """RED WHEN a success, a partial success or an empty body triggers the
    fallback, or a refusal body does not."""
    assert backlog._graphql_request_failed(json.loads(_GRAPHQL_REFUSED_STDOUT))
    assert backlog._graphql_request_failed({"message": "Bad credentials"})
    for not_refused in (
        {}, {"data": None}, {"data": {}, "errors": [{"message": "x"}]},
        {"data": {"repository": {}}, "message": "x"}, {"message": 5}, [1], "x",
    ):
        assert not backlog._graphql_request_failed(not_refused), not_refused


def test_graphql_success_never_calls_rest(monkeypatch):
    """RED WHEN a body carrying `data` triggers per-ref calls."""
    calls = []
    answer = json.dumps(
        {"data": {"repository": {"r0": {"state": "OPEN", "stateReason": None}}}})
    _this_repo(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _tracker(calls, graphql=answer))
    assert backlog.resolve_refs(["#1"]) == {"#1": {"state": "open"}}
    assert len(calls) == 1 and _is_graphql(calls[0][0])


def test_refused_graphql_falls_back_to_rest_per_ref(monkeypatch):
    """End to end through `resolve_refs` with the recorded refusal body.

    RED WHEN the fallback does not fire, a REST call lacks a bounded timeout,
    or a 404 reads as anything but unverifiable.
    """
    calls = []
    _this_repo(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _tracker(calls))

    refs = ["#1666", "#886", "#1672", "#1626", "#1667", "#999999"]
    outcome = backlog.resolve_refs(refs)

    assert outcome["#1666"] == {"state": "done"}
    assert outcome["#886"] == {"state": "abandoned", "reason": "duplicate"}
    assert outcome["#1672"] == {"state": "done"}
    assert outcome["#1626"] == {"state": "abandoned", "reason": "closed unmerged"}
    assert outcome["#1667"] == {"state": "open"}
    assert outcome["#999999"]["state"] == "unverifiable"

    rest = _rest_calls(calls)
    assert len(rest) == len(refs), "expected one REST call per ref"
    for argv, kwargs in rest:
        assert argv[:2] == ["gh", "api"] and argv[2].startswith("repos/owner/repo/issues/")
        timeout = kwargs.get("timeout")
        assert timeout is not None and 0 < timeout <= backlog._TRACKER_TIMEOUT_SECONDS


def test_a_refusal_shaped_error_body_falls_back_and_still_ends_unverifiable(
    monkeypatch,
):
    """SHAPE-ONLY trigger, its accepted false positive, end to end.

    A rate-limit or bad-credentials body has the refusal's shape (`message`,
    no `data`), so it DOES fall back. REST then fails the same way, and every
    ref must end `did not resolve` — never `open`, never an exception — with
    exactly one REST call per ref, so the false positive costs one bounded
    window and nothing else.

    RED WHEN an error body reaching `_node_from_rest` maps to a bucket, or the
    fallback stops firing on a refusal-shaped body.
    """
    calls = []
    limited = json.dumps({"message": "API rate limit exceeded for user ID 1.",
                          "documentation_url": "https://docs.github.com/"})

    def rate_limited(argv, number, kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout=limited, stderr="gh: (HTTP 403)")

    _this_repo(monkeypatch)
    monkeypatch.setattr(subprocess, "run",
                        _tracker(calls, graphql=limited, rest_hook=rate_limited))

    refs = ["#1", "#2", "#3"]
    outcome = backlog.resolve_refs(refs)

    assert all(outcome[ref] == {"state": "unverifiable",
                                "reason": backlog._REASON_NOT_RESOLVED}
               for ref in refs), outcome
    assert _rest_paths(calls) == [
        "repos/owner/repo/issues/1", "repos/owner/repo/issues/2",
        "repos/owner/repo/issues/3"]


def test_unparseable_graphql_output_never_falls_back(monkeypatch):
    """Output that is not JSON at all is not a refusal: no REST call, and the
    refs keep the default unverifiable reason.

    RED WHEN unparseable output is routed into the REST fallback or raises.
    """
    calls = []
    _this_repo(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _tracker(calls, graphql="<html>502</html>"))

    outcome = backlog.resolve_refs(["#1"])
    assert outcome["#1"]["state"] == "unverifiable"
    assert outcome["#1"]["reason"] == "no tracker resolution for this ref"
    assert len(calls) == 1 and _is_graphql(calls[0][0])


def test_r1_deeply_nested_json_is_unparseable_not_a_crash(monkeypatch):
    """S-F7: json.loads raises RecursionError, not ValueError, on deeply
    nested input. Treated like any unparseable body.

    RED WHEN RecursionError escapes `resolve_refs` or `_gh_repo_view_slug`.
    """
    nested = "[" * 200000
    assert backlog._parse_json(nested) is backlog._UNPARSEABLE
    calls = []
    _this_repo(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _tracker(calls, graphql=nested))
    outcome = backlog.resolve_refs(["#1666"])
    assert outcome["#1666"] == {"state": "unverifiable",
                                "reason": backlog._REASON_NO_RESOLUTION}
    monkeypatch.setattr(backlog, "_run_capture", lambda *a, **k: nested)
    assert backlog._gh_repo_view_slug() is None


def test_r1_refusal_returns_none_and_resolve_refs_owns_the_fallback(monkeypatch):
    """A-F2 / A-F4: `_resolve_refs_graphql` only speaks GraphQL — a refusal
    is None — and every reason `show` prints is a `_REASON_*` constant.

    RED WHEN the GraphQL function dispatches to REST itself again, or a
    reason drifts back to an inline literal.
    """
    calls = []
    monkeypatch.setattr(subprocess, "run", _tracker(calls))
    assert backlog._resolve_refs_graphql("owner", "repo", {"#1666": 1666}) is None
    assert _rest_paths(calls) == []

    monkeypatch.setattr(backlog, "_repo_slug", lambda *_: None)
    outcome = backlog.resolve_refs(["#1", "ENG-1"])
    assert outcome == {
        "#1": {"state": "unverifiable", "reason": backlog._REASON_NO_TRACKER},
        "ENG-1": {"state": "unverifiable", "reason": backlog._REASON_NO_RESOLUTION},
    }
    source = open(backlog.__file__, encoding="utf-8").read()
    body = source.split("def resolve_refs(", 1)[1].split("\ndef _resolve_refs_rest(", 1)[0]
    assert '"reason": "' not in body, "an inline reason literal is back"


def test_r1_every_gh_api_call_names_github_and_gh_host_is_scrubbed(monkeypatch):
    """S-F1: `gh api` takes its host from GH_HOST, not from the slug, so every
    call carries `--hostname github.com`, and GH_HOST never reaches a child.

    RED WHEN either guard is dropped and GH_HOST could redirect the verdicts.
    """
    calls = []
    monkeypatch.setenv("GH_HOST", "example.invalid")
    _this_repo(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _tracker(calls))

    backlog.resolve_refs(["#1666", "https://github.com/up/stream/issues/886"])
    assert len(calls) == 3
    for argv, kwargs in calls:
        assert argv[-2:] == ["--hostname", "github.com"], argv
        assert "GH_HOST" not in kwargs["env"], argv


def test_r1_run_capture_survives_nul_bytes_and_undecodable_output():
    """B-F3 / S-F4: a NUL byte in an argv (a hand-edited `project_path`)
    raises ValueError from subprocess, and non-UTF-8 output raised
    UnicodeDecodeError — both crashed `show`. Real subprocesses, no fakes.

    RED WHEN `_run_capture` lets either escape.
    """
    assert backlog._run_capture(["git", "-C", "/no\x00such", "status"]) is None
    script = "import sys; sys.stdout.buffer.write(b'ok \\xff')"
    assert backlog._run_capture([sys.executable, "-c", script]) == "ok �"
    assert backlog._origin_repo_slug("/no\x00such") is None


# ---------------------------------------------------------------------------
# Outcomes: one table through both paths, and the closed-is-not-done invariant
# ---------------------------------------------------------------------------

def test_node_from_rest_maps_every_bucket_through_ref_outcome():
    """RED WHEN a REST body lands in a different bucket than the GraphQL node
    for the same ref would, or an error body reads as anything but None."""
    expected = {
        1666: {"state": "done"},
        1004: {"state": "abandoned", "reason": "not_planned"},
        886: {"state": "abandoned", "reason": "duplicate"},
        1667: {"state": "open"},
        1672: {"state": "done"},
        1626: {"state": "abandoned", "reason": "closed unmerged"},
        1661: {"state": "open"},
    }
    for number, body in _REST_BODIES.items():
        node = backlog._node_from_rest(body)
        assert node is not None, number
        if "pull_request" in body:
            assert "merged" in node, f"PR #{number} node lost its `merged` key"
        assert backlog._ref_outcome(node) == expected[number], number

    assert backlog._ref_outcome(backlog._node_from_rest(
        {"state": "closed", "state_reason": None})) == {"state": "closed"}
    for bad in (_REST_NOT_FOUND, {"message": "not enabled for this session"},
                [1], None, {"state": 5}, {"state": ""}):
        assert backlog._node_from_rest(bad) is None, bad


# ONE TABLE, TWO PATHS. Each row is a single tracker fact stated twice: as the
# GraphQL node the batched query returns and as the REST body
# `gh api repos/{o}/{n}/issues/{N}` returns for the SAME issue or PR. Both
# must land in the SAME bucket — that is the closed-is-not-done authority
# staying single. The issue numbers are synthetic and exist only to key rows.
_OUTCOME_ROWS = [
    # (label, graphql node, REST body, expected outcome)
    ("open issue",
     {"state": "OPEN", "stateReason": None},
     {"state": "open", "state_reason": None},
     {"state": "open"}),
    ("reopened issue",
     {"state": "OPEN", "stateReason": "REOPENED"},
     {"state": "open", "state_reason": "reopened"},
     {"state": "open"}),
    ("closed completed",
     {"state": "CLOSED", "stateReason": "COMPLETED"},
     {"state": "closed", "state_reason": "completed"},
     {"state": "done"}),
    ("closed not_planned",
     {"state": "CLOSED", "stateReason": "NOT_PLANNED"},
     {"state": "closed", "state_reason": "not_planned"},
     {"state": "abandoned", "reason": "not_planned"}),
    ("closed duplicate",
     {"state": "CLOSED", "stateReason": "DUPLICATE"},
     {"state": "closed", "state_reason": "duplicate"},
     {"state": "abandoned", "reason": "duplicate"}),
    ("closed issue, no reason",
     {"state": "CLOSED", "stateReason": None},
     {"state": "closed", "state_reason": None},
     {"state": "closed"}),
    ("closed issue, unknown reason",
     {"state": "CLOSED", "stateReason": "SOMETHING_NEW"},
     {"state": "closed", "state_reason": "something_new"},
     {"state": "closed"}),
    ("merged PR",
     {"state": "MERGED", "merged": True},
     {"state": "closed", "state_reason": None,
      "pull_request": {"merged_at": "2026-10-26T04:37:12Z"}},
     {"state": "done"}),
    ("closed unmerged PR",
     {"state": "CLOSED", "merged": False},
     {"state": "closed", "state_reason": None,
      "pull_request": {"merged_at": None}},
     {"state": "abandoned", "reason": "closed unmerged"}),
    ("open PR",
     {"state": "OPEN", "merged": False},
     {"state": "open", "state_reason": None,
      "pull_request": {"merged_at": None}},
     {"state": "open"}),
]
_ROW_BY_NUMBER = {index + 100: row for index, row in enumerate(_OUTCOME_ROWS)}


def _graphql_answers_from_table(calls):
    """A `subprocess.run` fake: the batched query answered from the table.

    Reads each alias's issue NUMBER back out of the query the module built, so
    it follows whatever alias scheme the code uses — and runs unchanged
    against the pre-#1667 module, whose query has the same shape.
    """
    def fake_run(command, **kwargs):
        calls.append((list(command), kwargs))
        assert _is_graphql(command), f"unexpected call {command}"
        query = next(arg for arg in command if arg.startswith("query="))
        repository = {
            alias: _ROW_BY_NUMBER[int(number)][1]
            for alias, number in re.findall(
                r"\b(r\d+): issueOrPullRequest\(number: (\d+)\)", query)
        }
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps({"data": {"repository": repository}}),
            stderr="")
    return fake_run


def _table_outcomes_via_graphql(module, monkeypatch):
    calls = []
    monkeypatch.setattr(module, "_repo_slug", lambda *a, **k: ("owner", "repo"))
    monkeypatch.setattr(subprocess, "run", _graphql_answers_from_table(calls))
    refs = [f"#{number}" for number in _ROW_BY_NUMBER]
    outcome = module.resolve_refs(refs)
    assert len(calls) == 1, f"the fast path made {len(calls)} calls, not one"
    return {int(ref[1:]): state for ref, state in outcome.items()}


def _table_outcomes_via_rest(monkeypatch):
    calls = []
    _this_repo(monkeypatch)
    bodies = {number: row[2] for number, row in _ROW_BY_NUMBER.items()}
    monkeypatch.setattr(subprocess, "run", _tracker(calls, bodies=bodies))
    refs = [f"#{number}" for number in _ROW_BY_NUMBER]
    outcome = backlog.resolve_refs(refs)
    rest = _rest_calls(calls)
    assert len(rest) == len(refs), f"expected one REST call per ref, got {len(rest)}"
    return {int(ref[1:]): state for ref, state in outcome.items()}


def test_the_outcome_table_lands_every_row_in_the_same_bucket_on_both_paths(
    monkeypatch,
):
    """Every row, end to end through `resolve_refs`, once per path.

    The GraphQL half pins `_ref_outcome`'s mapping as it stood BEFORE #1667:
    its rows were run against the pre-change module (8e027e5~1) loaded as a
    copy, and passed there unchanged. The REST half pins that the adapter
    reproduces that mapping exactly.

    RED WHEN any bucket moves on either path: a `merged_at` that stops making
    a PR `done`, a closed-unmerged PR losing its `merged` key and reading
    `closed`, `duplicate` dropping out of the abandoned set, or `completed`
    stopping being the only reason that means done.
    """
    expected = {number: row[3] for number, row in _ROW_BY_NUMBER.items()}
    via_graphql = _table_outcomes_via_graphql(backlog, monkeypatch)
    via_rest = _table_outcomes_via_rest(monkeypatch)

    for number, row in _ROW_BY_NUMBER.items():
        label = row[0]
        assert via_graphql[number] == expected[number], f"GraphQL, {label}"
        assert via_rest[number] == expected[number], f"REST, {label}"


def _evidence_of_done(node_or_body, rest):
    """What a tracker record must SAY for `done` to be the honest verdict."""
    if rest:
        pull = node_or_body.get("pull_request")
        merged = isinstance(pull, dict) and bool(pull.get("merged_at"))
        reason = str(node_or_body.get("state_reason") or "").lower()
        state = str(node_or_body.get("state") or "").lower()
    else:
        merged = str(node_or_body.get("state") or "").lower() == "merged"
        reason = str(node_or_body.get("stateReason") or "").lower()
        state = str(node_or_body.get("state") or "").lower()
    return merged or (state == "closed" and reason == "completed")


def test_no_record_reads_done_unless_it_says_completed_or_merged():
    """The closed-is-not-done invariant over EVERY combination, both shapes,
    in BOTH directions: no combination reads `done` without the evidence, and
    every combination WITH the evidence reads `done`.

    A table pins the rows someone thought of; this loop pins the property over
    the cross product of states, reasons and PR markers, including impossible
    ones (an open issue claiming `completed`, a PR with `merged_at` and state
    `open`), because an invariant that only holds on plausible input is not
    the invariant. The census at the end is derived from the sequences the
    loops walk, so adding a case cannot leave a stale literal behind.

    RED WHEN any combination reads `done` without a `completed` close or a
    merge — e.g. `closed` alone mapped to done, or a falsy `merged` key read
    as merged — or when a MERGED node or a completed close stops reading done.
    """
    reasons = [None, "completed", "not_planned", "duplicate", "reopened", "other", ""]
    rest_states = ("open", "closed")
    pulls = (None, {"merged_at": None}, {"merged_at": "2026-01-01T00:00:00Z"}, {}, "junk")
    graphql_states = ("OPEN", "CLOSED", "MERGED", "")
    merged_flags = (None, True, False)

    checked = 0
    for state in rest_states:
        for reason in reasons:
            for pull in pulls:
                body = {"state": state, "state_reason": reason}
                if pull is not None:
                    body["pull_request"] = pull
                verdict = backlog._ref_outcome(backlog._node_from_rest(body))
                checked += 1
                if verdict["state"] == "done":
                    assert _evidence_of_done(body, rest=True), body
                elif _evidence_of_done(body, rest=True):
                    pytest.fail(f"evidence of done did not read done: {body} -> {verdict}")

    for state in graphql_states:
        for reason in reasons:
            for merged in merged_flags:
                node = {"state": state, "stateReason": reason.upper() if reason else reason}
                if merged is not None:
                    node["merged"] = merged
                verdict = backlog._ref_outcome(node)
                checked += 1
                if verdict["state"] == "done":
                    assert _evidence_of_done(node, rest=False), node
                elif _evidence_of_done(node, rest=False):
                    pytest.fail(f"evidence of done did not read done: {node} -> {verdict}")

    census = (len(rest_states) * len(reasons) * len(pulls)
              + len(graphql_states) * len(reasons) * len(merged_flags))
    assert checked == census, "the cross product shrank"


def test_rest_verdicts_render_as_done_and_abandoned_flags(monkeypatch, tmp_path):
    """`show`'s two strongest sentences, reached through the REST path: a ref
    closed as completed tells a live item it is probably done; a ref closed as
    not planned tells it the work was NOT done. An open ref says nothing.

    These lines predate #1667 but had no arm; the REST fallback is now what
    produces `done` and `abandoned` in cloud sessions, so they are pinned
    through it, end to end from `reconcile`.

    RED WHEN either flag line is dropped, the two are swapped, or an open ref
    starts flagging.
    """
    calls = []
    _this_repo(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _tracker(calls))
    items = [_item(item_id="aaaa", title="Done one", status="active", ref="#1666"),
             _item(item_id="bbbb", title="Dropped one", status="planned", ref="#1004"),
             _item(item_id="cccc", title="Open one", status="active", ref="#1667")]

    flags = backlog._ref_flags(items, project_path=str(tmp_path))

    done = [flag for flag in flags if "ref #1666" in flag]
    abandoned = [flag for flag in flags if "ref #1004" in flag]
    assert len(done) == 1 and "closed as completed" in done[0] \
        and "probably done" in done[0], flags
    assert len(abandoned) == 1 and "WITHOUT the work being done (not_planned)" in abandoned[0] \
        and "probably abandoned, NOT done" in abandoned[0], flags
    assert not [flag for flag in flags if "ref #1667" in flag], flags
    assert _rest_paths(calls) == ["repos/owner/repo/issues/1004",
                                  "repos/owner/repo/issues/1666",
                                  "repos/owner/repo/issues/1667"]


# ---------------------------------------------------------------------------
# The REST pool: cap, order, workers, deadline, floor, isolation
# ---------------------------------------------------------------------------

def test_rest_fallback_caps_refs_and_reports_the_overflow(monkeypatch):
    """At most `_REST_MAX_REFS` calls, and the cut is NUMERIC: with refs #1
    to #N the ones past the cap are exactly the highest numbers. In string
    order (`#10` < `#2`) the cut would land elsewhere. The overflow count is
    derived from the ref list, not restated.

    RED WHEN more than `_REST_MAX_REFS` calls are made, an overflow ref is
    dropped silently instead of carrying the cap reason, or the cut order is
    not numeric.
    """
    calls = []
    _this_repo(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _tracker(calls))

    cap = backlog._REST_MAX_REFS
    refs = [f"#{n}" for n in range(1, cap + 3)]
    outcome = backlog.resolve_refs(refs)

    assert len(_rest_calls(calls)) == cap
    capped = {ref for ref, state in outcome.items()
              if state.get("reason") == backlog._REASON_REST_CAP}
    assert capped == set(refs[cap:]), sorted(capped)
    assert len(capped) == len(refs) - cap
    assert str(cap) in backlog._REASON_REST_CAP


def test_r1_rest_calls_are_one_per_issue_in_numeric_order_this_repo_first(monkeypatch):
    """B-F6: refs naming the same issue share ONE call and its answer; the
    cap cuts in numeric order (not string order, where '#10' < '#2'), with
    this repository's issues before another repository's.

    RED WHEN duplicates each take a call and a cap slot, or the cut is
    lexicographic or puts a foreign repository first.
    """
    calls = []
    _this_repo(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _tracker(calls))
    monkeypatch.setattr(backlog, "_REST_MAX_REFS", 3)

    same = ["#1666", "1666", "https://github.com/OWNER/repo/issues/1666"]
    refs = same + ["#886", "#1004", "https://github.com/aaa/first/issues/1",
                   "#1667"]
    outcome = backlog.resolve_refs(refs)

    for ref in same:
        assert outcome[ref] == {"state": "done"}, ref
    assert _rest_paths(calls) == ["repos/owner/repo/issues/1004",
                                  "repos/owner/repo/issues/1666",
                                  "repos/owner/repo/issues/886"]
    for ref in ("#1667", "https://github.com/aaa/first/issues/1"):
        assert outcome[ref] == {"state": "unverifiable",
                                "reason": backlog._REASON_REST_CAP}, ref
    outcome[same[0]]["state"] = "mutated"
    assert outcome[same[1]] == {"state": "done"}, "fanned-out entries share one dict"


def test_the_pool_never_runs_more_than_the_worker_cap(monkeypatch):
    """The pool is sized `min(_REST_MAX_WORKERS, lookups)`: never a thread per
    ref at the cap, never more threads than lookups for a few. Read from the
    executor's own argument rather than inferred from timing.

    RED WHEN the pool takes one worker per lookup (or ignores the cap).
    """
    sizes = []

    class RecordingExecutor(concurrent.futures.ThreadPoolExecutor):
        def __init__(self, max_workers=None, *args, **kwargs):
            sizes.append(max_workers)
            super().__init__(max_workers, *args, **kwargs)

    calls = []
    _this_repo(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _tracker(calls))
    monkeypatch.setattr(backlog, "ThreadPoolExecutor", RecordingExecutor)

    backlog.resolve_refs([f"#{n}" for n in range(1, backlog._REST_MAX_REFS + 1)])
    backlog.resolve_refs(["#1", "#2", "#3"])
    assert sizes == [backlog._REST_MAX_WORKERS, 3], sizes
    assert backlog._REST_MAX_WORKERS < backlog._REST_MAX_REFS, (
        "control: the cap must bind for the first call to discriminate")


def test_rest_fallback_never_starts_a_call_past_the_deadline(monkeypatch):
    """A spent deadline makes every ref `did not complete`, with no REST call
    and no hang. RED WHEN a call starts after the deadline."""
    calls = []
    _this_repo(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _tracker(calls))
    monkeypatch.setattr(backlog, "_TRACKER_TIMEOUT_SECONDS", 0)

    outcome = backlog.resolve_refs(["#1666", "#886"])

    assert all(state == _INCOMPLETE for state in outcome.values()), outcome
    assert _rest_calls(calls) == []


def test_r1_no_rest_call_starts_with_less_than_one_floor_left(monkeypatch):
    """A-F5: with less than `_REST_MIN_CALL_TIMEOUT` left of the budget, no
    call is started — such a call could not change the outcome, and the
    interpreter would still wait for it at exit. Deterministic: the budget
    only shrinks while the arm runs.

    RED WHEN the pool starts calls on a spent budget.
    """
    calls = []
    monkeypatch.setattr(subprocess, "run", _tracker(calls))
    deadline = time.monotonic() + backlog._REST_MIN_CALL_TIMEOUT / 2
    outcome = backlog._resolve_refs_rest(
        {"#1666": ("owner", "repo", 1666)}, deadline=deadline, primary=("owner", "repo"))
    assert calls == []
    assert outcome == {"#1666": _INCOMPLETE}


def _one_worker_with_a_slow_first_call(monkeypatch, calls, first, seconds):
    """One worker, so lookups run in order; the call for `first` sleeps
    `seconds` before answering. A sleep is a LOWER bound, so "the next lookup
    starts at least `seconds` after the deadline was set" holds under any
    load."""
    def slow_first(argv, number, kwargs):
        if number == first:
            time.sleep(seconds)
        return None

    monkeypatch.setattr(backlog, "_REST_MAX_WORKERS", 1)
    monkeypatch.setattr(subprocess, "run", _tracker(calls, rest_hook=slow_first))


def test_a_call_started_late_still_gets_the_floor_as_its_timeout(monkeypatch):
    """A lookup that starts with less than `_REST_MIN_CALL_TIMEOUT` left of
    the budget (but more than nothing) is given the FLOOR as its subprocess
    timeout, not the sliver that is left — so a call that does start has time
    to answer.

    One worker: #886 runs first and takes 0.55 s; #1666 starts after it, with
    at most 1.0 - 0.55 = 0.45 s left, below the 0.5 s floor. It must start
    before the 1.0 s deadline, which leaves 0.45 s of slack for load.

    RED WHEN a late call's timeout is `remaining` (the floor is dropped).
    """
    calls = []
    floor = backlog._REST_MIN_CALL_TIMEOUT
    _one_worker_with_a_slow_first_call(monkeypatch, calls, first=886, seconds=0.55)

    outcome = backlog._resolve_refs_rest(
        {"#886": ("owner", "repo", 886), "#1666": ("owner", "repo", 1666)},
        deadline=time.monotonic() + 1.0, primary=("owner", "repo"))

    assert outcome == {"#886": {"state": "abandoned", "reason": "duplicate"},
                       "#1666": {"state": "done"}}
    timeouts = {_rest_number(argv): kwargs["timeout"] for argv, kwargs in _rest_calls(calls)}
    assert timeouts[1666] == floor, timeouts


def test_a_queued_lookup_never_starts_after_the_deadline(monkeypatch):
    """A lookup still QUEUED when the deadline passes is never started, even
    though the coordinator is still waiting (for up to one floor) on the call
    ahead of it.

    One worker: #886 runs first and sleeps 0.8 s, past the 0.7 s deadline;
    #1666 is dequeued at >= 0.8 s, after the deadline, while the wait still
    runs to ~1.2 s. It must read incomplete with no call made.

    RED WHEN the worker's own deadline check is removed (the queued lookup
    then makes its call and reads `done`).
    """
    calls = []
    _one_worker_with_a_slow_first_call(monkeypatch, calls, first=886, seconds=0.8)

    outcome = backlog._resolve_refs_rest(
        {"#886": ("owner", "repo", 886), "#1666": ("owner", "repo", 1666)},
        deadline=time.monotonic() + 0.7, primary=("owner", "repo"))

    assert _rest_paths(calls) == ["repos/owner/repo/issues/886"], _rest_paths(calls)
    assert outcome["#1666"] == _INCOMPLETE
    assert outcome["#886"] == {"state": "abandoned", "reason": "duplicate"}


def test_r1_an_answer_inside_the_last_call_floor_is_used(monkeypatch):
    """B-F4: the wait runs to the deadline PLUS one call floor — the latest a
    started call can finish — so an answer that lands just past the deadline
    is used instead of discarded (the interpreter waits for it anyway).

    Two checks. The wait's own timeout is read (deterministic): it must
    exceed what is left of the budget. And the behaviour: the answer sleeps
    1.0 s against a 0.9 s deadline — past it by construction, since a sleep is
    a lower bound — and the wait runs to ~0.9 + 0.6 s, leaving 0.5 s of slack.

    RED WHEN the wait ends at the bare deadline again.
    """
    waited = []
    real_wait = backlog.wait

    def recording_wait(futures, timeout=None, **kwargs):
        waited.append(timeout)
        return real_wait(futures, timeout=timeout, **kwargs)

    calls = []
    monkeypatch.setattr(backlog, "_REST_MIN_CALL_TIMEOUT", 0.6)
    monkeypatch.setattr(backlog, "wait", recording_wait)
    monkeypatch.setattr(subprocess, "run", _tracker(
        calls, rest_hook=lambda argv, number, kwargs: time.sleep(1.0)))

    budget = 0.9
    outcome = backlog._resolve_refs_rest(
        {"#1666": ("owner", "repo", 1666)},
        deadline=time.monotonic() + budget, primary=("owner", "repo"))

    assert len(waited) == 1 and waited[0] > budget, waited
    assert outcome == {"#1666": {"state": "done"}}


def test_r1_a_thread_that_cannot_start_leaves_its_refs_incomplete(monkeypatch):
    """B-F5: `submit` raising RuntimeError (a container's thread limit) must
    not escape `show`. What was submitted resolves; the rest read incomplete.

    RED WHEN the RuntimeError propagates out of `resolve_refs`.
    """
    calls = []
    submitted = []

    class OneThreadOnly(concurrent.futures.ThreadPoolExecutor):
        def submit(self, *args, **kwargs):
            if submitted:
                raise RuntimeError("can't start new thread")
            submitted.append(args)
            return super().submit(*args, **kwargs)

    monkeypatch.setattr(backlog, "ThreadPoolExecutor", OneThreadOnly)
    _this_repo(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _tracker(calls))

    outcome = backlog.resolve_refs(["#886", "#1666"])
    assert outcome["#886"] == {"state": "abandoned", "reason": "duplicate"}
    assert outcome["#1666"] == _INCOMPLETE


def test_a_slow_graphql_refusal_leaves_rest_only_the_remaining_budget(monkeypatch):
    """ONE budget for the tracker check: a refusal that arrives after the
    budget is spent leaves the REST phase nothing, so no REST call starts.

    The floor is shrunk below the budget (0.1 s against 0.2 s) so that a
    restarted clock WOULD start calls: with the default 0.5 s floor no call
    can start on a 0.2 s budget either way, and the arm could not tell the
    two apart. The 0.25 s refusal outlasts the budget by construction.

    RED WHEN the REST phase restarts the clock after GraphQL returns, which
    let a slow refusal stretch `show` to two budgets.
    """
    calls = []
    refused_then_rest = _tracker(calls)

    def slow_refusal(command, **kwargs):
        if _is_graphql(command):
            time.sleep(0.25)
        return refused_then_rest(command, **kwargs)

    _this_repo(monkeypatch)
    monkeypatch.setattr(subprocess, "run", slow_refusal)
    monkeypatch.setattr(backlog, "_TRACKER_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(backlog, "_REST_MIN_CALL_TIMEOUT", 0.1)

    outcome = backlog.resolve_refs(["#1666", "#886"])

    assert _rest_calls(calls) == []
    assert all(state == _INCOMPLETE for state in outcome.values()), outcome


def test_a_rest_call_hanging_mid_flight_does_not_hold_resolve_refs(monkeypatch):
    """A call that STARTED and then hangs is abandoned at the shared deadline.

    The deadline arms above cover calls that never start; this one starts
    them. `resolve_refs` must return at about the deadline plus one floor
    (0.4 + 0.1 s), the hung ref must read `did not complete`, and the refs
    that answered must keep their verdicts. The hung call blocks for up to
    3 s, so a coordinator that waits for it takes >= 3 s against a 2 s bound;
    the answering refs have the whole 0.5 s window.

    RED WHEN the coordinator waits for its workers (`shutdown(wait=True)`, or
    the pool used as a context manager), or `wait()` loses its timeout.
    """
    calls = []
    release = threading.Event()

    def hang_1666(argv, number, kwargs):
        if number == 1666:
            release.wait(timeout=3.0)
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
        return None

    _this_repo(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _tracker(calls, rest_hook=hang_1666))
    monkeypatch.setattr(backlog, "_TRACKER_TIMEOUT_SECONDS", 0.4)
    monkeypatch.setattr(backlog, "_REST_MIN_CALL_TIMEOUT", 0.1)
    try:
        started = time.monotonic()
        outcome = backlog.resolve_refs(["#1666", "#886", "#1672"])
        elapsed = time.monotonic() - started
    finally:
        release.set()

    assert elapsed < 2.0, f"resolve_refs waited {elapsed:.2f}s on a hung call"
    assert outcome["#1666"] == _INCOMPLETE
    assert outcome["#886"] == {"state": "abandoned", "reason": "duplicate"}
    assert outcome["#1672"] == {"state": "done"}


def test_one_failing_rest_call_does_not_take_its_neighbours_with_it(monkeypatch):
    """Per-ref isolation inside the pool, one row per way a call can fail.

    - TimeoutExpired / empty stdout -> `did not complete` (`_run_capture` None)
    - unparseable stdout            -> `did not resolve` (the ValueError arm)
    - an exception `_run_capture` does not catch -> `did not complete`
      (the `future.exception()` arm), never an exception out of `show`

    RED WHEN any of those branches is removed or merged into another, or a
    single bad ref turns its neighbours unverifiable.
    """
    def failing(argv, number, kwargs):
        if number == 1:
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
        if number == 2:
            return subprocess.CompletedProcess(argv, 0, stdout="   ", stderr="")
        if number == 3:
            return subprocess.CompletedProcess(argv, 0, stdout="{not json", stderr="")
        if number == 4:
            raise RuntimeError("a failure _run_capture does not catch")
        return None

    calls = []
    _this_repo(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _tracker(calls, rest_hook=failing))

    outcome = backlog.resolve_refs(["#1", "#2", "#3", "#4", "#1666", "#1626"])

    assert outcome["#1"] == _INCOMPLETE
    assert outcome["#2"] == _INCOMPLETE
    assert outcome["#3"] == {"state": "unverifiable",
                             "reason": backlog._REASON_NOT_RESOLVED}
    assert outcome["#4"] == _INCOMPLETE
    assert outcome["#1666"] == {"state": "done"}
    assert outcome["#1626"] == {"state": "abandoned", "reason": "closed unmerged"}


def test_rest_resolution_called_directly_keeps_its_contract(monkeypatch):
    """`_resolve_refs_rest` takes a REQUIRED deadline (A-F6: one budget,
    anchored in `resolve_refs` only), and with nothing to check makes no call
    and starts no pool. Every `gh api` call names github.com (S-F1).

    RED WHEN an empty batch submits to an executor, the deadline gains a
    default again, or `--hostname github.com` is dropped.
    """
    calls = []
    monkeypatch.setattr(subprocess, "run", _tracker(calls))
    deadline = time.monotonic() + backlog._TRACKER_TIMEOUT_SECONDS
    primary = ("owner", "repo")

    assert backlog._resolve_refs_rest({}, deadline=deadline, primary=primary) == {}
    assert calls == []
    with pytest.raises(TypeError):
        backlog._resolve_refs_rest({}, primary=primary)

    outcome = backlog._resolve_refs_rest(
        {"#1666": ("owner", "repo", 1666)}, deadline=deadline, primary=primary)
    assert outcome == {"#1666": {"state": "done"}}
    (argv, kwargs), = calls
    assert argv == ["gh", "api", "repos/owner/repo/issues/1666",
                    "--hostname", "github.com"]
    assert backlog._REST_MIN_CALL_TIMEOUT <= kwargs["timeout"] <= backlog._TRACKER_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# Hermeticity, and the real CLI process against a hanging `gh`
# ---------------------------------------------------------------------------

def test_the_tracker_guard_trips_on_an_unstubbed_gh_call():
    """The guard is armed: an unstubbed `gh` call fails, it is not silently run.

    RED WHEN the in-process guard is removed — the call would then reach the
    tripwire `gh` (exit 1) and `_run_capture` would return None quietly.
    """
    with pytest.raises(pytest.fail.Exception, match="unstubbed `gh`"):
        backlog._run_capture(["gh", "api", "graphql"])


_HANGING_GH = """#!/bin/sh
case "$*" in
  "api graphql"*)
    printf '%s' '{GRAPHQL}'
    exit 1 ;;
  "api repos/"*)
    echo "$*" >> '{MARKER}'
    exec sleep 30 ;;
esac
exit 1
"""


_CHILD_BUDGET = 3.0


def test_the_show_process_exits_within_budget_while_gh_hangs(tmp_path):
    """END TO END: the real CLI process, a real hanging `gh`, a real exit.

    Non-daemon pool threads are JOINED at interpreter exit, so returning from
    `resolve_refs` on time is not enough: the PROCESS must also end. It can
    only because each REST call carries its own subprocess timeout, which
    kills the hung `gh` at most `_REST_MIN_CALL_TIMEOUT` past the deadline.

    TIMED INSIDE THE CHILD. An `atexit` hook in the child reports the time
    from just before `main()` to the end of the interpreter's thread join
    (CPython joins non-daemon threads before it runs atexit hooks), so the
    bound excludes interpreter start-up and imports, which load inflates. The
    child's budget is _CHILD_BUDGET against the 0.5 s floor: the REST call is
    skipped only when the GraphQL leg alone eats all but the floor, so the budget
    leaves room for a loaded machine. Expected ~budget + floor; the bound adds
    2.0 s on top. The fake `gh` writes a marker on every REST call, so the arm
    proves the call it bounds was made.

    RED WHEN a REST call runs without a timeout (or with an unbounded one):
    the child then lives until the outer 15 s cap.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "rest-calls"
    gh = fake_bin / "gh"
    gh.write_text(_HANGING_GH.replace("{GRAPHQL}", _GRAPHQL_REFUSED_STDOUT)
                  .replace("{MARKER}", str(marker)), encoding="utf-8")
    gh.chmod(0o755)
    store, project = _cli_store(tmp_path, items=[_item(ref="#5")])

    # Loaded by FILE PATH: backlog.py bootstraps its own `hooks/` import root,
    # so the child needs no path setup of its own (tests/test_path_setup_pin.py
    # forbids per-file path mutation, codegen strings included).
    child = (
        "import atexit, importlib.util, sys, time\n"
        f"spec = importlib.util.spec_from_file_location('backlog', {BACKLOG_CLI!r})\n"
        "backlog = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(backlog)\n"
        f"backlog._TRACKER_TIMEOUT_SECONDS = {_CHILD_BUDGET}\n"
        "started = time.monotonic()\n"
        "atexit.register(lambda: sys.stderr.write("
        "'MAIN_TO_EXIT=%.3f\\n' % (time.monotonic() - started)))\n"
        f"sys.exit(backlog.main(['--backlog-dir', {str(store)!r}, 'show']))\n"
    )
    env = dict(os.environ, GH_REPO="owner/repo",
               PATH=f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")
    try:
        result = subprocess.run([sys.executable, "-c", child], cwd=project, env=env,
                                capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        pytest.fail("`show` did not exit within 15 s while `gh` hung")

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert marker.exists() and "api repos/owner/repo/issues/5" in marker.read_text(), (
        "no REST call reached the fake gh — the arm did not exercise the "
        "call it claims to bound:\n" + output)
    timing = re.search(r"MAIN_TO_EXIT=([0-9.]+)", result.stderr)
    assert timing, output
    bound = _CHILD_BUDGET + backlog._REST_MIN_CALL_TIMEOUT + 2.0
    assert float(timing.group(1)) < bound, f"`show` took {timing.group(1)}s to exit (bound {bound}s)"
    assert backlog._REASON_CALL_INCOMPLETE in output, output


def test_rest_fake_records_from_every_worker_thread(monkeypatch):
    """The shared fake is safe under the pool: with eight workers and 25
    lookups started together, every call is recorded exactly once.

    RED WHEN the fake's record loses or duplicates calls under concurrency
    (a fake that drops calls would make every call-count arm above vacuous).
    """
    calls = []
    barrier = threading.Barrier(backlog._REST_MAX_WORKERS, timeout=5)

    def all_workers_at_once(argv, number, kwargs):
        if number <= backlog._REST_MAX_WORKERS:
            barrier.wait()
        return None

    _this_repo(monkeypatch)
    monkeypatch.setattr(subprocess, "run", _tracker(calls, rest_hook=all_workers_at_once))
    refs = [f"#{n}" for n in range(1, backlog._REST_MAX_REFS + 1)]
    backlog.resolve_refs(refs)

    numbers = sorted(_rest_number(argv) for argv, _ in _rest_calls(calls))
    assert numbers == list(range(1, backlog._REST_MAX_REFS + 1))


def test_r1_abandoned_flags_match_digits_only_for_this_repository(monkeypatch):
    """A-F8: the branch linkage takes a ref's number only when the ref is
    THIS repository's; a foreign or Jira link keeps its full text, so its
    digits never match a local branch by coincidence.

    RED WHEN a foreign link's digits are matched against branch names again.
    """
    monkeypatch.setattr(backlog, "_branch_and_worktree_names",
                        lambda *_: ["feat/1234-thing"])
    monkeypatch.setattr(backlog, "_local_repo_slug", lambda *_: ("owner", "repo"))

    def flagged(ref):
        return backlog._abandoned_flags(
            [_item(status="active", ref=ref, touched=_OLD)], "/abs/project")

    assert flagged("#1234") == []
    assert flagged("https://github.com/owner/repo/issues/1234") == []
    assert len(flagged("https://github.com/other/repo/issues/1234")) == 1
    assert len(flagged("https://jira.example.com/browse/1234")) == 1


def test_r3_same_repo_is_the_one_comparison_and_issue_number_is_gone():
    """N2 / N3: `_same_repo` is the single ref-to-repository comparison
    (bare numbers always, github links case-insensitively on owner and
    name, nothing else), and the `_issue_number` wrapper no longer exists.

    RED WHEN the comparison turns case-sensitive, admits another kind, or
    `_issue_number` comes back as a second reading of a ref.
    """
    same = backlog._same_repo
    kind = backlog._classify_ref
    assert same(kind("#12"), "owner", "repo")
    assert same(kind("https://github.com/OWNER/Repo/issues/12"), "owner", "repo")
    assert not same(kind("https://github.com/other/repo/issues/12"), "owner", "repo")
    assert not same(kind("https://github.com/owner/repo2/issues/12"), "owner", "repo")
    for ref in ("https://jira.example.com/browse/12", "12/", "ENG-1", "#99999999999"):
        assert not same(kind(ref), "owner", "repo"), ref
    assert backlog._branch_token("https://github.com/OWNER/repo/pull/12", ("owner", "repo")) == "12"
    assert not hasattr(backlog, "_issue_number")
