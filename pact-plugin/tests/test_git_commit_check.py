"""
Tests for git_commit_check.py — PreToolUse hook that validates git commits
for PACT protocol compliance (SECURITY hook).

Tests cover:
1. check_security: .env file detection, risky logging patterns
2. check_frontend_credentials: VITE_, REACT_APP_, NEXT_PUBLIC_ credential exposure
3. check_direct_api_calls: direct external API call detection in frontend code
4. check_env_file_in_gitignore: .gitignore validation for .env entries
5. check_hardcoded_secrets: API key patterns, Stripe keys, GitHub tokens, etc.
6. main: stdin JSON parsing, exit codes, error/warning routing
"""
import io
import json
import os
import shutil
import subprocess
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# check_security
# ---------------------------------------------------------------------------

class TestCheckSecurity:
    """Tests for check_security() — .env file detection and risky log patterns."""

    def test_detects_env_file(self):
        from git_commit_check import check_security
        errors = check_security([".env"])
        assert len(errors) == 1
        assert "environment file" in errors[0].lower()

    def test_detects_nested_env_file(self):
        from git_commit_check import check_security
        errors = check_security(["config/.env"])
        assert len(errors) == 1

    def test_detects_env_prefixed_file(self):
        from git_commit_check import check_security
        errors = check_security([".env.local"])
        assert len(errors) == 1

    def test_allows_non_env_files(self):
        from git_commit_check import check_security
        errors = check_security(["src/app.py", "README.md"])
        assert errors == []

    def test_detects_console_log_process_env(self):
        from git_commit_check import check_security
        with patch(
            "git_commit_check.get_staged_file_content",
            return_value=(
                "console.log("
                "process.env.SECRET)"
            ),
        ):
            errors = check_security(["src/app.js"])
        assert len(errors) >= 1
        assert any("pattern" in e.lower() for e in errors)

    def test_detects_print_os_environ(self):
        from git_commit_check import check_security
        with patch(
            "git_commit_check.get_staged_file_content",
            return_value=(
                "print(os."
                "environ['PASSWORD'])"
            ),
        ):
            errors = check_security(["main.py"])
        assert len(errors) >= 1

    def test_detects_console_log_password(self):
        from git_commit_check import check_security
        with patch(
            "git_commit_check.get_staged_file_content",
            return_value=(
                "console.log("
                "password)"
            ),
        ):
            errors = check_security(["src/login.ts"])
        assert len(errors) >= 1

    def test_detects_print_api_key(self):
        from git_commit_check import check_security
        with patch(
            "git_commit_check.get_staged_file_content",
            return_value=(
                "print("
                "api_key)"
            ),
        ):
            errors = check_security(["util.py"])
        assert len(errors) >= 1

    def test_detects_console_log_token(self):
        from git_commit_check import check_security
        with patch(
            "git_commit_check.get_staged_file_content",
            return_value=(
                "console.log("
                "token)"
            ),
        ):
            errors = check_security(["src/auth.js"])
        assert len(errors) >= 1

    def test_skips_non_code_files(self):
        from git_commit_check import check_security
        with patch("git_commit_check.get_staged_file_content") as mock_get:
            errors = check_security(["image.png", "data.csv"])
        mock_get.assert_not_called()
        assert errors == []

    def test_case_insensitive_matching(self):
        from git_commit_check import check_security
        with patch(
            "git_commit_check.get_staged_file_content",
            return_value=(
                "Console.Log("
                "PASSWORD)"
            ),
        ):
            errors = check_security(["src/app.js"])
        assert len(errors) >= 1

    def test_multiple_env_files(self):
        from git_commit_check import check_security
        errors = check_security([".env", "config/.env", ".env.production"])
        assert len(errors) == 3


# ---------------------------------------------------------------------------
# check_frontend_credentials
# ---------------------------------------------------------------------------

class TestCheckFrontendCredentials:
    """Tests for check_frontend_credentials() — frontend env var exposure."""

    def test_detects_vite_secret_key(self):
        from git_commit_check import check_frontend_credentials
        with patch("git_commit_check.get_staged_file_content",
                   return_value='const key = import.meta.env.VITE_API_SECRET'):
            errors = check_frontend_credentials(["src/App.tsx"])
        assert len(errors) >= 1
        assert "frontend credential" in errors[0].lower()

    def test_detects_react_app_token(self):
        from git_commit_check import check_frontend_credentials
        with patch("git_commit_check.get_staged_file_content",
                   return_value='process.env.REACT_APP_AUTH_TOKEN'):
            errors = check_frontend_credentials(["src/api.jsx"])
        assert len(errors) >= 1

    def test_detects_next_public_password(self):
        from git_commit_check import check_frontend_credentials
        with patch("git_commit_check.get_staged_file_content",
                   return_value='NEXT_PUBLIC_DB_PASSWORD = "abc"'):
            errors = check_frontend_credentials(["components/db.tsx"])
        assert len(errors) >= 1

    def test_detects_nuxt_public_credential(self):
        from git_commit_check import check_frontend_credentials
        with patch("git_commit_check.get_staged_file_content",
                   return_value='NUXT_PUBLIC_API_CREDENTIAL = "abc"'):
            errors = check_frontend_credentials(["pages/index.vue"])
        assert len(errors) >= 1

    def test_allows_non_credential_env_vars(self):
        from git_commit_check import check_frontend_credentials
        with patch("git_commit_check.get_staged_file_content",
                   return_value='const url = import.meta.env.VITE_API_URL'):
            errors = check_frontend_credentials(["src/App.tsx"])
        assert errors == []

    def test_skips_backend_files(self):
        """Non-frontend files should be skipped."""
        from git_commit_check import check_frontend_credentials
        with patch("git_commit_check.get_staged_file_content") as mock_get:
            errors = check_frontend_credentials(["server/api.py"])
        mock_get.assert_not_called()
        assert errors == []

    def test_checks_js_in_frontend_dir(self):
        """JS/TS files in frontend dirs should be checked."""
        from git_commit_check import check_frontend_credentials
        with patch("git_commit_check.get_staged_file_content",
                   return_value='VITE_API_KEY = "secret"'):
            errors = check_frontend_credentials(["src/config.js"])
        assert len(errors) >= 1

    def test_skips_js_in_non_frontend_dir(self):
        """JS/TS files NOT in frontend dirs should be skipped."""
        from git_commit_check import check_frontend_credentials
        with patch("git_commit_check.get_staged_file_content") as mock_get:
            errors = check_frontend_credentials(["scripts/build.js"])
        mock_get.assert_not_called()
        assert errors == []

    def test_svelte_file_detected(self):
        from git_commit_check import check_frontend_credentials
        with patch("git_commit_check.get_staged_file_content",
                   return_value='VITE_SECRET_KEY = "abc"'):
            errors = check_frontend_credentials(["src/App.svelte"])
        assert len(errors) >= 1


# ---------------------------------------------------------------------------
# check_direct_api_calls
# ---------------------------------------------------------------------------

class TestCheckDirectApiCalls:
    """Tests for check_direct_api_calls() — direct external API call warnings."""

    def test_detects_fetch_to_external_api(self):
        from git_commit_check import check_direct_api_calls
        with patch("git_commit_check.get_staged_file_content",
                   return_value='fetch("https://api.example.com/data")'):
            warnings = check_direct_api_calls(["src/service.tsx"])
        assert len(warnings) == 1
        assert "external API" in warnings[0]

    def test_detects_axios_to_external_api(self):
        from git_commit_check import check_direct_api_calls
        with patch("git_commit_check.get_staged_file_content",
                   return_value='axios.get("https://api.example.com/data")'):
            warnings = check_direct_api_calls(["src/service.jsx"])
        assert len(warnings) == 1

    def test_detects_stripe_api_call(self):
        from git_commit_check import check_direct_api_calls
        with patch("git_commit_check.get_staged_file_content",
                   return_value='fetch("https://api.stripe.com/v1/charges")'):
            warnings = check_direct_api_calls(["src/payment.tsx"])
        assert len(warnings) == 1
        assert "API" in warnings[0]

    def test_detects_openai_api_call(self):
        from git_commit_check import check_direct_api_calls
        with patch("git_commit_check.get_staged_file_content",
                   return_value='fetch("https://api.openai.com/v1/chat")'):
            warnings = check_direct_api_calls(["src/ai.jsx"])
        assert len(warnings) == 1

    def test_skips_backend_dirs(self):
        """Backend directories should be excluded even with frontend extensions."""
        from git_commit_check import check_direct_api_calls
        with patch("git_commit_check.get_staged_file_content") as mock_get:
            warnings = check_direct_api_calls(["server/api.ts"])
        mock_get.assert_not_called()
        assert warnings == []

    def test_one_warning_per_file(self):
        """Multiple patterns in one file should produce only one warning."""
        from git_commit_check import check_direct_api_calls
        content = (
            'fetch("https://api.stripe.com/v1/charges")\n'
            'fetch("https://api.openai.com/v1/chat")\n'
        )
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            warnings = check_direct_api_calls(["src/service.tsx"])
        assert len(warnings) == 1

    def test_allows_relative_api_calls(self):
        """Relative API calls (to own backend) should not trigger warnings."""
        from git_commit_check import check_direct_api_calls
        with patch("git_commit_check.get_staged_file_content",
                   return_value='fetch("/api/data")'):
            warnings = check_direct_api_calls(["src/service.tsx"])
        assert warnings == []

    def test_requires_frontend_dir_and_ext(self):
        """File must be in frontend dir AND have frontend extension."""
        from git_commit_check import check_direct_api_calls
        with patch("git_commit_check.get_staged_file_content") as mock_get:
            # Non-frontend ext in frontend dir
            warnings = check_direct_api_calls(["src/config.json"])
        mock_get.assert_not_called()
        assert warnings == []


# ---------------------------------------------------------------------------
# check_env_file_in_gitignore
# ---------------------------------------------------------------------------

IGNORE_CHANNELS = ["repo_gitignore", "parent_gitignore", "per_repo_exclude", "global_excludesfile"]


def _isolated_git_env(tmp_path, monkeypatch):
    """Apply the HOME/XDG/GIT_CONFIG_* overrides that isolate user-level git config.

    Prevents the developer's real `~/.config/git/ignore` or `core.excludesFile`
    from affecting test outcomes (false positives) and prevents tests from
    writing into real config (side-effect leakage). Critical for the
    `global_excludesfile` channel; cheap defense-in-depth for the others.

    Returns the `fake_home` path so the caller can place a global ignore file
    under `$fake_home/.config/git/ignore` when needed.
    """
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.delenv("GIT_CONFIG_SYSTEM", raising=False)
    return fake_home


@pytest.fixture
def git_repo_with_env_ignored(tmp_path, monkeypatch, request):
    """
    Create a tmp_path git repo where `.env` is ignored via ONE of four channels.
    Parametrize with `IGNORE_CHANNELS` to cover all four.

    Isolates user-level git config via HOME / XDG_CONFIG_HOME / GIT_CONFIG_GLOBAL /
    GIT_CONFIG_SYSTEM overrides so tests neither read from nor write to the
    developer's real global git config. chdirs into the appropriate subdirectory
    for each channel. Returns the cwd path.
    """
    if shutil.which("git") is None:
        pytest.skip("git not available on PATH")

    channel = request.param

    fake_home = _isolated_git_env(tmp_path, monkeypatch)

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)

    if channel == "repo_gitignore":
        (repo / ".gitignore").write_text(".env\n")
        cwd = repo
    elif channel == "parent_gitignore":
        # .env ignored by PARENT's .gitignore; commit happens in subdir.
        (repo / ".gitignore").write_text(".env\n")
        subdir = repo / "subpkg"
        subdir.mkdir()
        cwd = subdir
    elif channel == "per_repo_exclude":
        (repo / ".git" / "info" / "exclude").write_text(".env\n")
        cwd = repo
    elif channel == "global_excludesfile":
        global_ignore = fake_home / ".config" / "git" / "ignore"
        global_ignore.parent.mkdir(parents=True)
        global_ignore.write_text(".env\n")
        # XDG default location; git check-ignore picks it up automatically
        # when XDG_CONFIG_HOME is set.
        cwd = repo
    else:
        raise ValueError(f"unknown channel: {channel}")

    monkeypatch.chdir(cwd)
    return cwd


class TestCheckEnvFileInGitignore:
    """Tests for check_env_file_in_gitignore() — delegates to `git check-ignore`.

    Shape per docs/architecture/fix-511-git-check-ignore.md §1:
      - 4 E2E positive (parametrized across ignore channels) — real git repo,
        `.env` ignored via each channel, assert `(True, None)`.
      - 1 E2E negative — real git repo, no ignore anywhere, assert VIOLATION
        with all three destinations named.
      - 5 mocked — branch matrix (exit 128, other non-zero, TimeoutExpired,
        FileNotFoundError, wrapper-args invariant).
      - 2 adversarial — `!.env` negation pattern (the false-positive the fix
        closes per arch §309) and Unicode-path cwd (cheap regression insurance
        for a SACROSANCT-surface hook).

    Assertion token contract (arch §0): the `VIOLATION` / `WARNING` substrings
    are the block/allow signal that `main()` triages on. Tests assert on these
    tokens, not on exit codes or blocking behavior at the function level.
    """

    # -------- E2E positive: parametrized per-channel --------

    @pytest.mark.parametrize(
        "git_repo_with_env_ignored", IGNORE_CHANNELS, indirect=True
    )
    def test_detects_env_ignored_across_all_channels(self, git_repo_with_env_ignored):
        from git_commit_check import check_env_file_in_gitignore
        is_protected, error = check_env_file_in_gitignore()
        assert is_protected is True
        assert error is None

    # -------- E2E negative --------

    def test_returns_violation_when_env_not_ignored_anywhere(
        self, tmp_path, monkeypatch
    ):
        if shutil.which("git") is None:
            pytest.skip("git not available on PATH")
        from git_commit_check import check_env_file_in_gitignore

        _isolated_git_env(tmp_path, monkeypatch)

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
        monkeypatch.chdir(repo)

        is_protected, error = check_env_file_in_gitignore()
        assert is_protected is False
        assert error is not None
        assert "VIOLATION" in error
        # All three destinations named per arch §5.
        assert ".gitignore" in error
        assert "~/.config/git/ignore" in error
        assert ".git/info/exclude" in error

    # -------- Mocked branch matrix --------

    def test_returns_warning_on_exit_128(self):
        from git_commit_check import check_env_file_in_gitignore
        mock_result = MagicMock()
        mock_result.returncode = 128
        with patch("shared.git_helpers.subprocess.run", return_value=mock_result):
            is_protected, error = check_env_file_in_gitignore()
        assert is_protected is False
        assert error is not None
        assert "WARNING" in error
        assert "VIOLATION" not in error
        assert "exit 128" in error
        # Shared log-grep invariant across every WARNING string (arch §5).
        assert "cannot verify .env protection" in error

    def test_returns_warning_on_unexpected_exit(self):
        from git_commit_check import check_env_file_in_gitignore
        mock_result = MagicMock()
        mock_result.returncode = 2
        with patch("shared.git_helpers.subprocess.run", return_value=mock_result):
            is_protected, error = check_env_file_in_gitignore()
        assert is_protected is False
        assert error is not None
        assert "WARNING" in error
        assert "VIOLATION" not in error
        assert "exited 2" in error
        # Shared log-grep invariant across every WARNING string (arch §5).
        assert "cannot verify .env protection" in error

    def test_handles_run_git_failure(self):
        """Merged: TimeoutExpired and FileNotFoundError collapse into one
        "could not be invoked" WARNING after run_git extraction (arch §8)."""
        from git_commit_check import check_env_file_in_gitignore
        # TimeoutExpired path
        with patch(
            "shared.git_helpers.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=5),
        ):
            is_protected, error = check_env_file_in_gitignore()
        assert is_protected is False
        assert error is not None
        assert "WARNING" in error
        assert "VIOLATION" not in error
        assert "could not be invoked" in error
        assert "cannot verify .env protection" in error

        # FileNotFoundError path
        with patch(
            "shared.git_helpers.subprocess.run",
            side_effect=FileNotFoundError("git"),
        ):
            is_protected, error = check_env_file_in_gitignore()
        assert is_protected is False
        assert error is not None
        assert "WARNING" in error
        assert "VIOLATION" not in error
        assert "could not be invoked" in error
        assert "cannot verify .env protection" in error

    def test_invokes_git_check_ignore_with_correct_args(self):
        """Wrapper-shape invariant: argv + capture_output + timeout are frozen.

        After F4 extraction, the subprocess.run call lives in shared.git_helpers.run_git;
        the caller's intent (no check=True, since exit 1 is the VIOLATION branch) is
        preserved structurally by run_git's signature (it does not pass check=True).
        """
        from git_commit_check import check_env_file_in_gitignore
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch(
            "shared.git_helpers.subprocess.run", return_value=mock_result
        ) as mock_run:
            check_env_file_in_gitignore()
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == ["git", "check-ignore", "-q", ".env"]
        assert kwargs.get("capture_output") is True
        assert kwargs.get("timeout") == 5
        # check=True would raise on exit 1, swallowing the VIOLATION return.
        assert kwargs.get("check", False) is False

    # -------- Adversarial --------

    def test_negation_pattern_unignores_env_and_returns_violation(
        self, tmp_path, monkeypatch
    ):
        """`!.env` in a higher-priority source unignores `.env` from a lower one.

        Arch §309 names this as one of the false-positives the fix closes: the
        old substring read would see `.env` in some ignore file and approve
        the commit, missing a later `!.env` re-inclusion. Per gitignore(5),
        priority order is: command-line patterns > per-directory `.gitignore`
        (nearest wins) > `.git/info/exclude` > `core.excludesFile` (global).
        So `.env` in global ignore + `!.env` in repo `.gitignore` = not ignored.

        `git check-ignore -q` returns exit 1 when the final resolution is
        "not ignored," even via a negation pattern — the hook must surface
        that as a VIOLATION.
        """
        if shutil.which("git") is None:
            pytest.skip("git not available on PATH")
        from git_commit_check import check_env_file_in_gitignore

        fake_home = _isolated_git_env(tmp_path, monkeypatch)

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
        # Global ignore says "ignore .env"; repo-local .gitignore overrides.
        global_ignore = fake_home / ".config" / "git" / "ignore"
        global_ignore.parent.mkdir(parents=True)
        global_ignore.write_text(".env\n")
        (repo / ".gitignore").write_text("!.env\n")
        monkeypatch.chdir(repo)

        is_protected, error = check_env_file_in_gitignore()
        assert is_protected is False
        assert error is not None
        assert "VIOLATION" in error

    def test_unicode_and_space_in_cwd_path_does_not_break_invocation(
        self, tmp_path, monkeypatch
    ):
        """cwd containing Unicode/space characters must not break subprocess call.

        The subprocess argv passes `.env` literally (no path interpolation), so
        this test is currently a no-op regression guard. If a future refactor
        adds a path argument, this catches the breakage for a SACROSANCT-surface
        hook where subtle invocation bugs are silent-failure-prone.
        """
        if shutil.which("git") is None:
            pytest.skip("git not available on PATH")
        from git_commit_check import check_env_file_in_gitignore

        _isolated_git_env(tmp_path, monkeypatch)

        # Unicode + space in directory name — the ancestor cwd that
        # git check-ignore resolves `.env` relative to.
        repo = tmp_path / "rép o"
        repo.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
        (repo / ".gitignore").write_text(".env\n")
        monkeypatch.chdir(repo)

        is_protected, error = check_env_file_in_gitignore()
        assert is_protected is True
        assert error is None


# ---------------------------------------------------------------------------
# Security residual E2E (arch §8 — git rm --cached .env must not block)
# ---------------------------------------------------------------------------


class TestSecurityResidualE2E:
    """End-to-end verification that untracking a previously-tracked .env is
    not flagged as a violation.

    The argv-level pin `test_get_staged_files_excludes_deletions` asserts the
    `--diff-filter=d` flag is on the argv; this class closes the semantic
    loop by driving `main()` end-to-end through a real git repo in the
    remediation state a user would be in (`.env` historically tracked, then
    `git rm --cached .env` staged alongside `.gitignore` updates). Asserts
    exit 0 with no VIOLATION on stderr.
    """

    def test_git_rm_cached_env_does_not_block_commit(
        self, tmp_path, monkeypatch, capsys
    ):
        if shutil.which("git") is None:
            pytest.skip("git not available on PATH")
        from git_commit_check import main

        _isolated_git_env(tmp_path, monkeypatch)

        repo = tmp_path / "repo"
        repo.mkdir()
        # Set a committer identity so the initial commit succeeds under our
        # isolated HOME (no user.name/user.email in the fake global config).
        env_cmd_args = {"cwd": repo, "check": True}
        subprocess.run(["git", "init", "--quiet"], **env_cmd_args)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], **env_cmd_args)
        subprocess.run(["git", "config", "user.name", "Test"], **env_cmd_args)

        # Stage 1: track .env historically (pre-remediation state).
        (repo / ".env").write_text("SECRET=sentinel\n")
        subprocess.run(["git", "add", ".env"], **env_cmd_args)
        subprocess.run(["git", "commit", "-m", "initial", "--quiet"], **env_cmd_args)

        # Stage 2: the user's remediation — ignore + untrack .env.
        (repo / ".gitignore").write_text(".env\n")
        subprocess.run(["git", "add", ".gitignore"], **env_cmd_args)
        subprocess.run(["git", "rm", "--cached", ".env", "--quiet"], **env_cmd_args)

        monkeypatch.chdir(repo)

        # Drive main() with a plausible `git commit` stdin payload.
        input_data = {"tool_input": {"command": "git commit -m 'remove .env'"}}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit) as exc:
                main()

        # Exit 0 = commit allowed. Exit 2 would mean VIOLATION fired.
        assert exc.value.code == 0, (
            "git rm --cached .env staged alongside a new .gitignore must not "
            "block the commit — the deletion has no staged content and .env is "
            "now ignored."
        )
        # Defense in depth: even if exit 0, confirm no VIOLATION string surfaced.
        captured = capsys.readouterr()
        assert "VIOLATION" not in captured.err
        # Belt-and-suspenders: guard the false-pass where main()'s outer
        # try/except swallows an unexpected error and exits 0 with
        # "Hook Error" on stderr. Without this, a regression that broke
        # main() would silently pass this test on exit-code alone.
        assert "Hook Error" not in captured.err


# ---------------------------------------------------------------------------
# check_hardcoded_secrets
# ---------------------------------------------------------------------------

class TestCheckHardcodedSecrets:
    """Tests for check_hardcoded_secrets() — API key and secret detection."""

    def test_detects_openai_key(self):
        from git_commit_check import check_hardcoded_secrets
        content = ('api_key = "sk-' 'FAKE00000000000000000000000000"')
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["config.py"])
        assert len(errors) >= 1

    def test_detects_stripe_live_key(self):
        from git_commit_check import check_hardcoded_secrets
        content = ('key = "sk_live_' 'FAKE0000000000000000"')
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["payment.py"])
        assert len(errors) >= 1
        assert any("stripe" in e.lower() for e in errors)

    def test_detects_stripe_test_key(self):
        from git_commit_check import check_hardcoded_secrets
        content = ('key = "sk_test_' 'FAKE0000000000000000"')
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["payment.py"])
        assert len(errors) >= 1

    def test_detects_github_pat(self):
        from git_commit_check import check_hardcoded_secrets
        content = ('token = "ghp_' 'FAKE00000000000000000000000000000000"')
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["deploy.py"])
        assert len(errors) >= 1
        assert any("github" in e.lower() for e in errors)

    def test_detects_github_oauth_token(self):
        from git_commit_check import check_hardcoded_secrets
        content = ('token = "gho_' 'FAKE00000000000000000000000000000000"')
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["auth.py"])
        assert len(errors) >= 1

    def test_detects_slack_token(self):
        from git_commit_check import check_hardcoded_secrets
        content = ('token = "xoxb-' '1234567890-abcdefgh"')
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["slack.py"])
        assert len(errors) >= 1
        assert any("slack" in e.lower() for e in errors)

    def test_detects_generic_api_key_assignment(self):
        from git_commit_check import check_hardcoded_secrets
        content = ('api_key = "' 'abcdefghijklmnopqrstuvwxyz1234"')
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["config.py"])
        assert len(errors) >= 1

    def test_detects_hardcoded_password(self):
        from git_commit_check import check_hardcoded_secrets
        content = ('password = "' 'my_super_secret_' 'password"')
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["db.py"])
        assert len(errors) >= 1

    def test_detects_aws_access_key_id(self):
        from git_commit_check import check_hardcoded_secrets
        content = ('aws_key = "AKIA' 'IOSFODNN7EXAMPLE"')
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["config.py"])
        assert len(errors) >= 1
        assert any("aws" in e.lower() for e in errors)

    def test_ignores_non_akia_aws_prefix(self):
        """Only AKIA prefix indicates long-term AWS keys; ASIA is temporary."""
        from git_commit_check import check_hardcoded_secrets
        content = 'key = "NOTAKIA0000000000000"'
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["config.py"])
        assert not any("aws" in e.lower() for e in errors)

    def test_detects_rsa_private_key(self):
        from git_commit_check import check_hardcoded_secrets
        content = ('-----BEGIN ' 'RSA PRIVATE KEY-----' '\nMIIE...')
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["deploy.py"])
        assert len(errors) >= 1
        assert any("private key" in e.lower() for e in errors)

    def test_detects_generic_private_key(self):
        from git_commit_check import check_hardcoded_secrets
        content = ('-----BEGIN '
                   'PRIVATE KEY-----'
                   '\nMIIE...')
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["certs.py"])
        assert len(errors) >= 1
        assert any("private key" in e.lower() for e in errors)

    def test_detects_ec_private_key(self):
        from git_commit_check import check_hardcoded_secrets
        content = ('-----BEGIN EC '
                   'PRIVATE KEY-----'
                   '\nMHQC...')
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["crypto.py"])
        assert len(errors) >= 1
        assert any("private key" in e.lower() for e in errors)

    def test_detects_openssh_private_key(self):
        from git_commit_check import check_hardcoded_secrets
        content = ('-----BEGIN OPENSSH '
                   'PRIVATE KEY-----'
                   '\nb3Blbn...')
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["ssh.py"])
        assert len(errors) >= 1
        assert any("private key" in e.lower() for e in errors)

    def test_ignores_public_key_header(self):
        """Public keys are not secrets and should not trigger."""
        from git_commit_check import check_hardcoded_secrets
        content = '-----BEGIN PUBLIC KEY-----\nMIIB...'
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["keys.py"])
        assert not any("private key" in e.lower() for e in errors)

    def test_detects_jwt_token(self):
        from git_commit_check import check_hardcoded_secrets
        # Realistic JWT structure: header.payload.signature
        content = ('token = "eyJ'
                   'hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc123def456_signature"')
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["auth.py"])
        assert len(errors) >= 1
        assert any("jwt" in e.lower() for e in errors)

    def test_ignores_short_eyj_string(self):
        """Short 'eyJ' strings that aren't full JWTs should not trigger."""
        from git_commit_check import check_hardcoded_secrets
        content = 'x = "eyJhbGci"'  # Too short, missing dot-separated segments
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["config.py"])
        assert not any("jwt" in e.lower() for e in errors)

    def test_allows_short_values(self):
        """Short values (< 8 chars for password, < 20 for keys) should not trigger."""
        from git_commit_check import check_hardcoded_secrets
        content = 'password = "short"'
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["config.py"])
        assert errors == []

    def test_skips_non_code_files(self):
        from git_commit_check import check_hardcoded_secrets
        with patch("git_commit_check.get_staged_file_content") as mock_get:
            errors = check_hardcoded_secrets(["image.png", "data.csv"])
        mock_get.assert_not_called()
        assert errors == []

    def test_truncates_long_match_preview(self):
        """Match preview should be truncated at 30 chars."""
        from git_commit_check import check_hardcoded_secrets
        long_key = "a" * 50
        content = f'api_key = "{long_key}"'
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["config.py"])
        assert len(errors) >= 1
        assert "..." in errors[0]


# ---------------------------------------------------------------------------
# get_staged_files / get_staged_file_content
# ---------------------------------------------------------------------------

class TestGitHelpers:
    """Tests for git subprocess helper functions."""

    def test_get_staged_files_success(self):
        from git_commit_check import get_staged_files
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = b"file1.py\0file2.js\0"
        with patch("shared.git_helpers.subprocess.run", return_value=mock_result):
            files = get_staged_files()
        assert files == ["file1.py", "file2.js"]

    def test_get_staged_files_empty(self):
        from git_commit_check import get_staged_files
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        with patch("shared.git_helpers.subprocess.run", return_value=mock_result):
            files = get_staged_files()
        assert files == []

    def test_get_staged_files_non_zero_returncode(self):
        """Post-F4: non-zero returncode is the fail-open path (no CalledProcessError)."""
        from git_commit_check import get_staged_files
        mock_result = MagicMock()
        mock_result.returncode = 1
        with patch("shared.git_helpers.subprocess.run", return_value=mock_result):
            files = get_staged_files()
        assert files == []

    def test_get_staged_files_timeout(self):
        from git_commit_check import get_staged_files
        import subprocess
        with patch("shared.git_helpers.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=5)):
            files = get_staged_files()
        assert files == []

    def test_get_staged_files_git_not_installed(self):
        from git_commit_check import get_staged_files
        with patch("shared.git_helpers.subprocess.run", side_effect=FileNotFoundError("git")):
            files = get_staged_files()
        assert files == []

    def _undecodable_name_repo(self, tmp_path, monkeypatch):
        """A real repo with an isolated git config, cwd inside it."""
        if shutil.which("git") is None:
            pytest.skip("git not available on PATH")
        _isolated_git_env(tmp_path, monkeypatch)
        repo = tmp_path / "repo"
        repo.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        subprocess.run(
            ["git", "init", "-q", "."], cwd=repo, env=env, check=True,
            capture_output=True, timeout=30,
        )
        monkeypatch.chdir(repo)
        return repo, env

    @staticmethod
    def _stage(repo, env, *names):
        for name in names:
            (repo / name).write_text("SECRET=1\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "-A"], cwd=repo, env=env, check=True,
            capture_output=True, timeout=30,
        )

    def test_the_staged_name_survives_gits_quoting(self, tmp_path, monkeypatch):
        """D-1 LAYER 1, the defect under the DEFAULT config: git prints 0xff
        as the ASCII text `\\377` and quotes the path, so the name arrives
        mangled. The arm fails on the name itself, before any scan runs."""
        from git_commit_check import get_staged_files

        repo, env = self._undecodable_name_repo(tmp_path, monkeypatch)
        name = os.fsdecode(b"lead-\xff.txt")
        self._stage(repo, env, name)

        staged = get_staged_files()

        assert [os.fsencode(n) for n in staged] == [os.fsencode(name)], (
            f"git's C-quoting was decoded instead of removed: {staged}"
        )

    def test_the_staged_name_survives_a_locale_decode(self, tmp_path, monkeypatch):
        """D-1 LAYER 2, the narrower case: with `core.quotePath=false` git
        prints the raw bytes, and HEAD raised UnicodeDecodeError inside the
        hook. A separate arm because layer 1's fix does not make it raise."""
        from git_commit_check import get_staged_files

        repo, env = self._undecodable_name_repo(tmp_path, monkeypatch)
        subprocess.run(
            ["git", "config", "core.quotePath", "false"], cwd=repo, env=env,
            check=True, capture_output=True, timeout=30,
        )
        name = os.fsdecode(b"lead-\xff.txt")
        self._stage(repo, env, name)

        staged = get_staged_files()

        assert [os.fsencode(n) for n in staged] == [os.fsencode(name)]

    def test_an_undecodable_env_name_is_still_flagged(
        self, tmp_path, monkeypatch
    ):
        """D-1'S SECURITY CONSEQUENCE, with its OWN assertion: under the
        default config HEAD saw `"evil-\\377.env"`, which ends in a quote, so
        `.endswith('.env')` missed it. This arm asserts the scan's verdict,
        not the name, so it fails independently of the two above."""
        from git_commit_check import check_security, get_staged_files

        repo, env = self._undecodable_name_repo(tmp_path, monkeypatch)
        name = os.fsdecode(b"evil-\xff.env")
        self._stage(repo, env, name)

        staged = get_staged_files()

        assert any(
            "SACROSANCT VIOLATION" in error for error in check_security(staged)
        ), f"the .env suffix test missed an undecodable name: {staged}"

    def test_get_staged_file_content_decodes_binary_instead_of_raising(
        self, tmp_path, monkeypatch
    ):
        """The adjacent content case: a staged BINARY file used to raise
        UnicodeDecodeError, and the hook's catch-all then allowed the commit
        with every scan skipped. The ASCII runs still reach the regexes."""
        from git_commit_check import get_staged_file_content

        if shutil.which("git") is None:
            pytest.skip("git not available on PATH")
        _isolated_git_env(tmp_path, monkeypatch)
        repo = tmp_path / "repo"
        repo.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        subprocess.run(
            ["git", "init", "-q", "."], cwd=repo, env=env, check=True,
            capture_output=True, timeout=30,
        )
        monkeypatch.chdir(repo)
        (repo / "blob.bin").write_bytes(b"\x00\xff\xfe password='hunter2'\x80\n")
        subprocess.run(
            ["git", "add", "-A"], cwd=repo, env=env, check=True,
            capture_output=True, timeout=30,
        )

        content = get_staged_file_content("blob.bin")

        assert isinstance(content, str)
        assert "password" in content

    def test_get_staged_files_invokes_with_timeout(self):
        """Wrapper-args invariant: timeout=5 must be passed to subprocess.run."""
        from git_commit_check import get_staged_files
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        with patch("shared.git_helpers.subprocess.run", return_value=mock_result) as mock_run:
            get_staged_files()
        assert mock_run.called
        _, kwargs = mock_run.call_args
        assert kwargs.get("timeout") == 5

    def test_get_staged_files_excludes_deletions(self):
        """Security residual: `git rm --cached .env` deletion must not be
        scanned as an added file. Argv passed to git MUST include
        `--diff-filter=d` so deletion-only stagings are filtered out before
        the downstream security checks run."""
        from git_commit_check import get_staged_files
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        with patch("shared.git_helpers.subprocess.run", return_value=mock_result) as mock_run:
            get_staged_files()
        args, _ = mock_run.call_args
        assert args[0] == [
            "git", "diff", "--name-only", "--cached", "--diff-filter=d", "-z"
        ]

    def test_get_staged_file_content_success(self):
        from git_commit_check import get_staged_file_content
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = b"file content here"
        with patch("shared.git_helpers.subprocess.run", return_value=mock_result):
            content = get_staged_file_content("test.py")
        assert content == "file content here"

    def test_get_staged_file_content_non_zero_returncode(self):
        """Post-F4: non-zero returncode is the fail-open path (no CalledProcessError)."""
        from git_commit_check import get_staged_file_content
        mock_result = MagicMock()
        mock_result.returncode = 128
        with patch("shared.git_helpers.subprocess.run", return_value=mock_result):
            content = get_staged_file_content("missing.py")
        assert content == ""

    def test_get_staged_file_content_timeout(self):
        from git_commit_check import get_staged_file_content
        import subprocess
        with patch("shared.git_helpers.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=5)):
            content = get_staged_file_content("missing.py")
        assert content == ""

    def test_get_staged_file_content_git_not_installed(self):
        from git_commit_check import get_staged_file_content
        with patch("shared.git_helpers.subprocess.run", side_effect=FileNotFoundError("git")):
            content = get_staged_file_content("missing.py")
        assert content == ""

    def test_get_staged_file_content_invokes_with_timeout(self):
        """Wrapper-args invariant: timeout=5 must be passed to subprocess.run."""
        from git_commit_check import get_staged_file_content
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        with patch("shared.git_helpers.subprocess.run", return_value=mock_result) as mock_run:
            get_staged_file_content("any.py")
        assert mock_run.called
        _, kwargs = mock_run.call_args
        assert kwargs.get("timeout") == 5


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

class TestMain:
    """Tests for main() entry point — stdin parsing, exit codes, integration."""

    def test_allows_non_commit_command(self, capsys):
        from git_commit_check import main
        input_data = {"tool_input": {"command": "git status"}}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"suppressOutput": True}

    def test_allows_commit_with_no_staged_files(self):
        from git_commit_check import main
        input_data = {"tool_input": {"command": "git commit -m 'test'"}}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with patch("git_commit_check.get_staged_files", return_value=[]):
                with pytest.raises(SystemExit) as exc:
                    main()
        assert exc.value.code == 0

    def test_blocks_commit_with_security_errors(self):
        from git_commit_check import main
        input_data = {"tool_input": {"command": "git commit -m 'test'"}}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with patch("git_commit_check.get_staged_files",
                       return_value=[".env"]):
                with patch("git_commit_check.check_env_file_in_gitignore",
                           return_value=(True, None)):
                    with pytest.raises(SystemExit) as exc:
                        main()
        assert exc.value.code == 2

    def test_allows_clean_commit(self):
        from git_commit_check import main
        input_data = {"tool_input": {"command": "git commit -m 'test'"}}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with patch("git_commit_check.get_staged_files",
                       return_value=["src/app.py"]):
                with patch("git_commit_check.get_staged_file_content",
                           return_value="print('hello')"):
                    with patch("git_commit_check.check_env_file_in_gitignore",
                               return_value=(True, None)):
                        with pytest.raises(SystemExit) as exc:
                            main()
        assert exc.value.code == 0

    def test_handles_invalid_json_stdin(self):
        from git_commit_check import main
        with patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0  # Errors don't block

    def test_handles_missing_tool_input(self):
        from git_commit_check import main
        input_data = {}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0

    def test_warnings_dont_block(self):
        """Direct API call warnings should not block commits."""
        from git_commit_check import main
        input_data = {"tool_input": {"command": "git commit -m 'test'"}}
        with (
            patch("sys.stdin", io.StringIO(json.dumps(input_data))),
            patch("git_commit_check.get_staged_files",
                  return_value=["src/api.tsx"]),
            patch("git_commit_check.check_security", return_value=[]),
            patch("git_commit_check.check_hardcoded_secrets", return_value=[]),
            patch("git_commit_check.check_frontend_credentials",
                  return_value=[]),
            patch("git_commit_check.check_direct_api_calls",
                  return_value=["Warning: direct API call"]),
            patch("git_commit_check.check_env_file_in_gitignore",
                  return_value=(True, None)),
        ):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0


# ---------------------------------------------------------------------------
# The commit-time scan must not block its own maintenance
# ---------------------------------------------------------------------------

class TestTheScanDoesNotBlockItself:
    """This hook carries the detection patterns and its test file carries the
    credentials those patterns are tested with, so a whole-file scan found its
    own source and denied every commit that improved it. The literals are
    split so the SOURCE no longer matches; the compiled patterns are
    unchanged. Both arms below are required: one proves a genuine secret is
    still caught, the other proves the exemption is not a tests/ hole.
    """

    def test_the_split_azure_pattern_still_matches_a_genuine_string(self):
        """The adjacent literals join at compile time, so a real connection
        string still produces the Azure SQL violation."""
        from git_commit_check import check_hardcoded_secrets

        sample = (
            "Server=tcp:"
            "srv.database.windows.net,1433;"
            "Password"
            "="
            "hunter2"
        )
        with patch(
            "git_commit_check.get_staged_file_content", return_value=sample
        ):
            errors = check_hardcoded_secrets(["src/config.py"])

        assert any("Azure SQL" in error for error in errors), errors

    def test_the_exclusion_is_exactly_the_two_paths(self):
        """The scan skips its own source and its own test file and NOTHING
        else: a real secret in a THIRD file under tests/ is still caught."""
        from git_commit_check import check_hardcoded_secrets

        secret = (
            'key = "sk_live_'
            'FAKE0123456789abcdefghij"'
        )
        with patch(
            "git_commit_check.get_staged_file_content", return_value=secret
        ):
            own_test = check_hardcoded_secrets(
                ["tests/test_git_commit_check.py"]
            )
            own_source = check_hardcoded_secrets(
                ["hooks/git_commit_check.py"]
            )
            third_file = check_hardcoded_secrets(["tests/test_other.py"])

        assert own_test == [], own_test
        assert own_source == [], own_source
        assert any("Stripe live" in error for error in third_file), (
            third_file
        )

    def test_a_foreign_file_with_the_same_basename_is_still_scanned(self):
        """The exemption is EXACT paths, not a basename suffix: a directory
        that merely carries the hook's filename is not this repository's
        file, and a secret inside it must still be caught."""
        from git_commit_check import check_hardcoded_secrets

        secret = (
            'key = "sk_live_'
            'FAKE0123456789abcdefghij"'
        )
        with patch(
            "git_commit_check.get_staged_file_content", return_value=secret
        ):
            foreign_hook = check_hardcoded_secrets(
                ["vendor/hooks/git_commit_check.py"]
            )
            foreign_test = check_hardcoded_secrets(
                ["some/other/tests/test_git_commit_check.py"]
            )

        assert any("Stripe live" in error for error in foreign_hook), (
            foreign_hook
        )
        assert any("Stripe live" in error for error in foreign_test), (
            foreign_test
        )
