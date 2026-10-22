"""
Location: pact-plugin/tests/test_wrap_up_record.py
Summary: The wrap-up record CLI (hooks/shared/wrap_up_record.py), run as a real
         subprocess the way wrap-up.md step 5 runs it, with a stub `gh` on
         PATH for each answer gh can give.
Used by: pytest.

Each arm checks the five printed lines, the exit code, and the journal line
the CLI wrote, and then asks the reader (session_journal.wrap_up_closed)
what that record means.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from clock_shift.clock_shift_env import carry_clock_shift
from shared.session_journal import wrap_up_closed

CLI = Path(__file__).resolve().parents[1] / "hooks" / "shared" / "wrap_up_record.py"

PR_JSON = json.dumps({
    "headRefName": "feat/x",
    "headRepository": {"id": "R_1", "name": "repo-x", "nameWithOwner": "own/repo-x"},
    "headRepositoryOwner": {"id": "O_1", "login": "own"},
    "number": 61,
    "state": "{state}",
})


def _stub_gh(bindir: Path, *, stdout: str = "", stderr: str = "", rc: int = 0) -> None:
    """A gh that records its argv and answers with fixed output."""
    bindir.mkdir(parents=True, exist_ok=True)
    (bindir / "out.txt").write_text(stdout)
    (bindir / "err.txt").write_text(stderr)
    gh = bindir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{bindir}/argv.txt"\n'
        f'cat "{bindir}/out.txt"\n'
        f'cat "{bindir}/err.txt" >&2\n'
        f"exit {rc}\n"
    )
    gh.chmod(0o755)


def _run(tmp_path: Path, session_dir: str | None = None) -> subprocess.CompletedProcess:
    sd = session_dir if session_dir is not None else str(tmp_path / "session")
    if session_dir is None:
        Path(sd).mkdir(parents=True, exist_ok=True)
    env = carry_clock_shift({
        "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "CLAUDE_CONFIG_DIR": str(tmp_path),
    })
    return subprocess.run(
        [sys.executable, str(CLI), "--session-dir", sd],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
        timeout=60, check=False,
    )


def _lines(result: subprocess.CompletedProcess) -> dict:
    lines = result.stdout.splitlines()
    assert [line.split("=", 1)[0] for line in lines] == [
        "pr_state", "pr_number", "branch", "head_owner", "head_repo", "pr_source"
    ], result.stdout
    return dict(line.split("=", 1) for line in lines)


def _journal(tmp_path: Path) -> list[dict]:
    path = tmp_path / "session" / "session-journal.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize("state", ["MERGED", "OPEN", "CLOSED"])
def test_a_pr_state_is_recorded_with_its_number(tmp_path, state):
    _stub_gh(tmp_path / "bin", stdout=PR_JSON.replace("{state}", state))

    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert _lines(result) == {
        "pr_state": state, "pr_number": "61", "branch": "feat/x",
        "head_owner": "own", "head_repo": "repo-x", "pr_source": "cwd",
    }
    (event,) = _journal(tmp_path)
    assert {k: event[k] for k in ("type", "source", "pr_state", "pr_number", "pr_source")} == {
        "type": "session_end", "source": "wrap-up", "pr_state": state, "pr_number": 61,
        "pr_source": "cwd",
    }
    assert wrap_up_closed([event], 61, None) is (state == "MERGED")
    argv = (tmp_path / "bin" / "argv.txt").read_text().splitlines()
    assert argv == ["pr view --json number,state,headRefName,headRepository,headRepositoryOwner"]


def test_no_pull_request_records_none_without_a_number(tmp_path):
    _stub_gh(tmp_path / "bin", rc=1,
             stderr='no pull requests found for branch "feat/x"\n')

    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert _lines(result) == {
        "pr_state": "NONE", "pr_number": "", "branch": "", "head_owner": "", "head_repo": "",
        "pr_source": "cwd",
    }
    (event,) = _journal(tmp_path)
    assert event["pr_state"] == "NONE" and "pr_number" not in event


@pytest.mark.parametrize("stub", [
    pytest.param(dict(rc=1, stderr="error connecting to api.github.com\n"), id="network"),
    pytest.param(dict(rc=4, stderr="gh auth login required\n"), id="auth"),
    pytest.param(dict(rc=0, stdout="not json"), id="unreadable-json"),
    pytest.param(dict(rc=0, stdout=PR_JSON.replace("{state}", "DRAFT")), id="unknown-state"),
    pytest.param(dict(rc=0, stdout=PR_JSON.replace("{state}", "MERGED").replace('61', '"61"')),
                 id="string-number"),
])
def test_any_other_answer_records_unknown(tmp_path, stub):
    _stub_gh(tmp_path / "bin", **stub)

    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert _lines(result)["pr_state"] == "UNKNOWN"
    (event,) = _journal(tmp_path)
    assert event["pr_state"] == "UNKNOWN" and "pr_number" not in event


def test_gh_missing_records_unknown(tmp_path):
    (tmp_path / "bin").mkdir()

    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert _lines(result)["pr_state"] == "UNKNOWN"


@pytest.mark.parametrize("session_dir", ["{session_dir}", "relative/dir", "/does/not/exist"])
def test_a_bad_session_dir_writes_nothing_and_exits_2(tmp_path, session_dir):
    _stub_gh(tmp_path / "bin", stdout=PR_JSON.replace("{state}", "MERGED"))

    result = _run(tmp_path, session_dir=session_dir)

    assert result.returncode == 2
    assert result.stdout == ""
    assert not (tmp_path / "bin" / "argv.txt").exists()


def test_a_failed_write_exits_1_and_still_prints(tmp_path):
    """The session dir is a file's child: the journal cannot be created."""
    _stub_gh(tmp_path / "bin", stdout=PR_JSON.replace("{state}", "MERGED"))
    sd = tmp_path / "session"
    sd.mkdir()
    (sd / "session-journal.jsonl").mkdir()  # a directory where the journal file goes

    result = _run(tmp_path, session_dir=str(sd))

    assert result.returncode == 1
    assert _lines(result)["pr_state"] == "MERGED"
    assert "wrap_up_record:" in result.stderr


def test_a_record_that_cannot_be_read_back_exits_1(tmp_path):
    """The write succeeds but the journal cannot be read: only the read-back
    check sees it."""
    _stub_gh(tmp_path / "bin", stdout=PR_JSON.replace("{state}", "MERGED"))
    sd = tmp_path / "session"
    sd.mkdir()
    journal = sd / "session-journal.jsonl"
    journal.write_text("")
    journal.chmod(0o200)  # write-only
    try:
        result = _run(tmp_path, session_dir=str(sd))
    finally:
        journal.chmod(0o600)

    assert result.returncode == 1
    assert "read back" in result.stderr


def test_a_failed_write_is_not_masked_by_an_earlier_identical_record(tmp_path):
    """The journal already holds the same record and refuses the new write:
    the read-back alone would pass on the old line, so the write's own
    result must decide."""
    _stub_gh(tmp_path / "bin", stdout=PR_JSON.replace("{state}", "MERGED"))
    sd = tmp_path / "session"
    sd.mkdir()
    journal = sd / "session-journal.jsonl"
    journal.write_text(json.dumps({"v": 1, "type": "session_end", "source": "wrap-up",
                                   "pr_state": "MERGED", "pr_number": 61,
                                   "ts": "2026-01-01T00:00:00Z"}) + "\n")
    journal.chmod(0o400)  # read-only
    try:
        result = _run(tmp_path, session_dir=str(sd))
    finally:
        journal.chmod(0o600)

    assert result.returncode == 1
    assert "write failed" in result.stderr


# ---------------------------------------------------------------------------
# Fallback: the cwd has no PR (wrap-up run outside the feature worktree), so
# the script reads the PR of the latest review_dispatch in the session journal.
# ---------------------------------------------------------------------------

NO_PR_STDERR = 'no pull requests found for branch "main"\n'


def _stub_gh_two_reads(bindir: Path, *, numbered_stdout: str = "", numbered_stderr: str = "",
                       numbered_rc: int = 0) -> None:
    """A gh whose cwd read (`gh pr view --json ...`) finds no PR and whose
    numbered read (`gh pr view <N> ...`) answers as given. Records argv."""
    bindir.mkdir(parents=True, exist_ok=True)
    (bindir / "num_out.txt").write_text(numbered_stdout)
    (bindir / "num_err.txt").write_text(numbered_stderr)
    gh = bindir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{bindir}/argv.txt"\n'
        'if [ "$3" = "--json" ]; then\n'
        f"  printf '%s' '{NO_PR_STDERR}' >&2; exit 1\n"
        "fi\n"
        f'cat "{bindir}/num_out.txt"\n'
        f'cat "{bindir}/num_err.txt" >&2\n'
        f"exit {numbered_rc}\n"
    )
    gh.chmod(0o755)


def _review(session: Path, pr_number, pr_url="https://github.com/own/repo-x/pull/61",
            ts="2026-04-17T12:00:00Z") -> None:
    session.mkdir(parents=True, exist_ok=True)
    with (session / "session-journal.jsonl").open("a") as journal:
        journal.write(json.dumps({"v": 1, "type": "review_dispatch", "pr_number": pr_number,
                                  "pr_url": pr_url, "reviewers": ["x"], "ts": ts}) + "\n")


def _argv(tmp_path: Path) -> list[str]:
    return (tmp_path / "bin" / "argv.txt").read_text().splitlines()


def _session_ends(tmp_path: Path) -> list[dict]:
    return [e for e in _journal(tmp_path) if e["type"] == "session_end"]


def test_no_pr_in_the_cwd_falls_back_to_the_reviewed_pr(tmp_path):
    _stub_gh_two_reads(tmp_path / "bin", numbered_stdout=PR_JSON.replace("{state}", "MERGED"))
    _review(tmp_path / "session", 61)

    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert _lines(result) == {
        "pr_state": "MERGED", "pr_number": "61", "branch": "feat/x",
        "head_owner": "own", "head_repo": "repo-x", "pr_source": "review",
    }
    (event,) = _session_ends(tmp_path)
    assert (event["pr_state"], event["pr_number"], event["pr_source"]) == ("MERGED", 61, "review")
    assert _argv(tmp_path) == [
        "pr view --json number,state,headRefName,headRepository,headRepositoryOwner",
        "pr view 61 --repo own/repo-x --json number,state,headRefName,headRepository,headRepositoryOwner",
    ]


def test_the_latest_review_dispatch_is_the_one_read(tmp_path):
    _stub_gh_two_reads(tmp_path / "bin", numbered_stdout=PR_JSON.replace("{state}", "MERGED"))
    _review(tmp_path / "session", 50, pr_url="https://github.com/own/repo-x/pull/50",
            ts="2026-04-17T10:00:00Z")
    _review(tmp_path / "session", 61)

    _run(tmp_path)

    assert _argv(tmp_path)[1].startswith("pr view 61 ")


@pytest.mark.parametrize("pr_url", [
    "not a url", "https://gitlab.com/own/repo-x/pull/61", "https://github.com/-own/repo-x/pull/61", None,
])
def test_an_unparseable_pr_url_does_not_fall_back(tmp_path, pr_url):
    """With no repo to name, a bare `gh pr view 61` would read PR 61 of
    whatever repo the cwd is in: a different PR. The cwd result stands, and
    a stub that would answer MERGED for 61 is never asked."""
    _stub_gh_two_reads(tmp_path / "bin", numbered_stdout=PR_JSON.replace("{state}", "MERGED"))
    _review(tmp_path / "session", 61, pr_url=pr_url)

    result = _run(tmp_path)

    assert _lines(result)["pr_state"] == "NONE"
    assert len(_argv(tmp_path)) == 1


def test_outside_any_repo_an_unparseable_pr_url_stays_unknown(tmp_path):
    _stub_gh_outside_a_repo(tmp_path / "bin", numbered_stdout=PR_JSON.replace("{state}", "MERGED"))
    _review(tmp_path / "session", 61, pr_url="not a url")

    result = _run(tmp_path)

    assert _lines(result)["pr_state"] == "UNKNOWN"
    assert len(_argv(tmp_path)) == 1


def test_no_review_dispatch_keeps_none_with_one_gh_call(tmp_path):
    _stub_gh_two_reads(tmp_path / "bin", numbered_stdout=PR_JSON.replace("{state}", "MERGED"))

    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert _lines(result)["pr_state"] == "NONE"
    assert len(_argv(tmp_path)) == 1


@pytest.mark.parametrize("pr_number", ["61", True, None])
def test_a_review_dispatch_without_an_int_number_keeps_none(tmp_path, pr_number):
    _stub_gh_two_reads(tmp_path / "bin", numbered_stdout=PR_JSON.replace("{state}", "MERGED"))
    _review(tmp_path / "session", pr_number)

    result = _run(tmp_path)

    assert _lines(result)["pr_state"] == "NONE"
    assert len(_argv(tmp_path)) == 1


@pytest.mark.parametrize("stub", [
    pytest.param(dict(numbered_rc=1, numbered_stderr="error connecting to api.github.com\n"),
                 id="network"),
    pytest.param(dict(numbered_rc=1, numbered_stderr=NO_PR_STDERR), id="numbered-says-no-pr"),
    pytest.param(dict(numbered_stdout="not json"), id="unreadable-json"),
    pytest.param(dict(numbered_stdout=PR_JSON.replace("{state}", "MERGED").replace("61", "62")),
                 id="another-number"),
])
def test_a_failed_fallback_records_unknown_never_none(tmp_path, stub):
    """A fallback that cannot confirm the reviewed PR must not leave NONE:
    NONE would send wrap-up down its no-PR branch for a PR that exists."""
    _stub_gh_two_reads(tmp_path / "bin", **stub)
    _review(tmp_path / "session", 61)

    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert _lines(result)["pr_state"] == "UNKNOWN"
    (event,) = _session_ends(tmp_path)
    assert event["pr_state"] == "UNKNOWN" and "pr_number" not in event


def test_a_pr_found_in_the_cwd_makes_no_second_read(tmp_path):
    _stub_gh(tmp_path / "bin", stdout=PR_JSON.replace("{state}", "OPEN"))
    _review(tmp_path / "session", 99)

    result = _run(tmp_path)

    assert _lines(result)["pr_number"] == "61"
    assert len(_argv(tmp_path)) == 1


NOT_A_REPO = "failed to run git: fatal: not a git repository (or any of the parent directories): .git\n"


def _stub_gh_outside_a_repo(bindir: Path, *, numbered_stdout: str = "", numbered_rc: int = 0,
                            numbered_stderr: str = "") -> None:
    """A gh whose cwd read fails as it does outside any repo, and whose
    numbered `--repo` read answers as given."""
    _stub_gh_two_reads(bindir, numbered_stdout=numbered_stdout, numbered_rc=numbered_rc,
                       numbered_stderr=numbered_stderr)
    gh = bindir / "gh"
    gh.write_text(gh.read_text().replace(NO_PR_STDERR, NOT_A_REPO))


def test_outside_any_repo_the_reviewed_pr_is_read_with_repo(tmp_path):
    """The cwd read fails as UNKNOWN ("not a git repository"); the fallback's
    --repo read resolves the reviewed PR anyway."""
    _stub_gh_outside_a_repo(tmp_path / "bin", numbered_stdout=PR_JSON.replace("{state}", "MERGED"))
    _review(tmp_path / "session", 61)

    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert _lines(result)["pr_state"] == "MERGED"
    assert _argv(tmp_path)[1].startswith("pr view 61 --repo own/repo-x ")


def test_outside_any_repo_a_failed_fallback_stays_unknown(tmp_path):
    _stub_gh_outside_a_repo(tmp_path / "bin", numbered_rc=1,
                            numbered_stderr="error connecting to api.github.com\n")
    _review(tmp_path / "session", 61)

    result = _run(tmp_path)

    assert _lines(result)["pr_state"] == "UNKNOWN"
    assert len(_argv(tmp_path)) == 2


def test_outside_any_repo_with_no_review_stays_unknown_with_one_call(tmp_path):
    _stub_gh_outside_a_repo(tmp_path / "bin", numbered_stdout=PR_JSON.replace("{state}", "MERGED"))

    result = _run(tmp_path)

    assert _lines(result)["pr_state"] == "UNKNOWN"
    assert len(_argv(tmp_path)) == 1


# ---------------------------------------------------------------------------
# A cwd NONE falls back only to a reviewed PR that wrap-up has not closed yet.
# ---------------------------------------------------------------------------

def _wrapped(session: Path, pr_number: int, ts: str) -> None:
    """An earlier wrap-up's record: this PR MERGED, at `ts`."""
    session.mkdir(parents=True, exist_ok=True)
    with (session / "session-journal.jsonl").open("a") as journal:
        journal.write(json.dumps({"v": 1, "type": "session_end", "source": "wrap-up",
                                  "pr_state": "MERGED", "pr_number": pr_number,
                                  "pr_source": "cwd", "ts": ts}) + "\n")


def test_a_reviewed_pr_already_wrapped_keeps_none(tmp_path):
    """A session merged and wrapped PR 61, then wraps a new feature with no
    PR: the cwd's NONE is the truth, and PR 61 must not be recorded again."""
    _stub_gh_two_reads(tmp_path / "bin", numbered_stdout=PR_JSON.replace("{state}", "MERGED"))
    _review(tmp_path / "session", 61, ts="2026-04-17T12:00:00Z")
    _wrapped(tmp_path / "session", 61, ts="2026-04-17T13:00:00Z")

    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert _lines(result)["pr_state"] == "NONE"
    assert _lines(result)["pr_source"] == "cwd"
    assert len(_argv(tmp_path)) == 1
    assert _session_ends(tmp_path)[-1]["pr_source"] == "cwd"


def test_a_wrap_up_before_the_latest_review_does_not_close_it(tmp_path):
    """The earlier wrap-up predates the latest review of the same PR (a second
    review round): that review is not wrapped yet, so the fallback runs."""
    _stub_gh_two_reads(tmp_path / "bin", numbered_stdout=PR_JSON.replace("{state}", "MERGED"))
    _wrapped(tmp_path / "session", 61, ts="2026-04-17T11:00:00Z")
    _review(tmp_path / "session", 61, ts="2026-04-17T12:00:00Z")

    result = _run(tmp_path)

    assert _lines(result)["pr_state"] == "MERGED"
    assert _lines(result)["pr_source"] == "review"


def test_another_prs_wrap_up_does_not_close_the_reviewed_one(tmp_path):
    _stub_gh_two_reads(tmp_path / "bin", numbered_stdout=PR_JSON.replace("{state}", "MERGED"))
    _review(tmp_path / "session", 61, ts="2026-04-17T12:00:00Z")
    _wrapped(tmp_path / "session", 50, ts="2026-04-17T13:00:00Z")

    result = _run(tmp_path)

    assert _lines(result)["pr_source"] == "review"


def test_an_unknown_cwd_read_falls_back_even_for_a_wrapped_pr(tmp_path):
    _stub_gh_outside_a_repo(tmp_path / "bin", numbered_stdout=PR_JSON.replace("{state}", "MERGED"))
    _review(tmp_path / "session", 61, ts="2026-04-17T12:00:00Z")
    _wrapped(tmp_path / "session", 61, ts="2026-04-17T13:00:00Z")

    result = _run(tmp_path)

    assert _lines(result)["pr_state"] == "MERGED"
    assert _lines(result)["pr_source"] == "review"
    assert len(_argv(tmp_path)) == 2


def test_a_failed_fallback_is_review_sourced(tmp_path):
    """The fallback ran and replaced the cwd read, so the source is review,
    even though it could only say UNKNOWN."""
    _stub_gh_two_reads(tmp_path / "bin", numbered_rc=1,
                       numbered_stderr="error connecting to api.github.com\n")
    _review(tmp_path / "session", 61)

    result = _run(tmp_path)

    assert _lines(result)["pr_state"] == "UNKNOWN"
    assert _lines(result)["pr_source"] == "review"
    assert _session_ends(tmp_path)[-1]["pr_source"] == "review"


def test_a_cwd_unknown_with_no_review_is_cwd_sourced(tmp_path):
    _stub_gh_outside_a_repo(tmp_path / "bin")

    result = _run(tmp_path)

    assert _lines(result)["pr_state"] == "UNKNOWN"
    assert _lines(result)["pr_source"] == "cwd"


def test_the_schema_types_pr_source(tmp_path):
    from shared.session_journal import _validate_event_schema, make_event

    ok = make_event("session_end", source="wrap-up", pr_state="NONE", pr_source="cwd")
    assert _validate_event_schema(ok) == (True, "ok")
    valid, reason = _validate_event_schema(
        make_event("session_end", source="wrap-up", pr_state="NONE", pr_source=1))
    assert valid is False and "pr_source" in reason
