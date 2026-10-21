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
        "pr_state", "pr_number", "branch", "head_owner", "head_repo"
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
        "head_owner": "own", "head_repo": "repo-x",
    }
    (event,) = _journal(tmp_path)
    assert {k: event[k] for k in ("type", "source", "pr_state", "pr_number")} == {
        "type": "session_end", "source": "wrap-up", "pr_state": state, "pr_number": 61,
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
