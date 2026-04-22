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
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


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
        with patch("git_commit_check.get_staged_file_content",
                   return_value="console.log(process.env.SECRET)"):
            errors = check_security(["src/app.js"])
        assert len(errors) >= 1
        assert any("pattern" in e.lower() for e in errors)

    def test_detects_print_os_environ(self):
        from git_commit_check import check_security
        with patch("git_commit_check.get_staged_file_content",
                   return_value="print(os.environ['PASSWORD'])"):
            errors = check_security(["main.py"])
        assert len(errors) >= 1

    def test_detects_console_log_password(self):
        from git_commit_check import check_security
        with patch("git_commit_check.get_staged_file_content",
                   return_value="console.log(password)"):
            errors = check_security(["src/login.ts"])
        assert len(errors) >= 1

    def test_detects_print_api_key(self):
        from git_commit_check import check_security
        with patch("git_commit_check.get_staged_file_content",
                   return_value="print(api_key)"):
            errors = check_security(["util.py"])
        assert len(errors) >= 1

    def test_detects_console_log_token(self):
        from git_commit_check import check_security
        with patch("git_commit_check.get_staged_file_content",
                   return_value="console.log(token)"):
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
        with patch("git_commit_check.get_staged_file_content",
                   return_value="Console.Log(PASSWORD)"):
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

    # Isolate user-level git config — critical for the global_excludesfile
    # channel, and cheap defense-in-depth for the others. Prevents the
    # developer's real ~/.config/git/ignore from affecting test outcomes
    # (false positives) and prevents tests from writing into real config
    # (side-effect leakage).
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.delenv("GIT_CONFIG_SYSTEM", raising=False)

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

        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
        monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
        monkeypatch.delenv("GIT_CONFIG_SYSTEM", raising=False)

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
        with patch("git_commit_check.subprocess.run", return_value=mock_result):
            is_protected, error = check_env_file_in_gitignore()
        assert is_protected is False
        assert error is not None
        assert "WARNING" in error
        assert "VIOLATION" not in error
        assert "exit 128" in error
        assert "cannot verify" in error

    def test_returns_warning_on_unexpected_exit(self):
        from git_commit_check import check_env_file_in_gitignore
        mock_result = MagicMock()
        mock_result.returncode = 2
        with patch("git_commit_check.subprocess.run", return_value=mock_result):
            is_protected, error = check_env_file_in_gitignore()
        assert is_protected is False
        assert error is not None
        assert "WARNING" in error
        assert "VIOLATION" not in error
        assert "exited 2" in error
        assert "cannot verify" in error

    def test_handles_timeout_expired(self):
        from git_commit_check import check_env_file_in_gitignore
        with patch(
            "git_commit_check.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=5),
        ):
            is_protected, error = check_env_file_in_gitignore()
        assert is_protected is False
        assert error is not None
        assert "WARNING" in error
        assert "VIOLATION" not in error
        assert "timed out" in error
        assert "cannot verify" in error

    def test_handles_git_not_installed(self):
        from git_commit_check import check_env_file_in_gitignore
        with patch(
            "git_commit_check.subprocess.run",
            side_effect=FileNotFoundError("git"),
        ):
            is_protected, error = check_env_file_in_gitignore()
        assert is_protected is False
        assert error is not None
        assert "WARNING" in error
        assert "VIOLATION" not in error
        assert "git binary not found" in error
        assert "cannot verify" in error

    def test_invokes_git_check_ignore_with_correct_args(self):
        """Wrapper-shape invariant: argv + capture_output + timeout are frozen.

        Also pins `check=True` is NOT passed — exit 1 is the VIOLATION branch,
        which would raise CalledProcessError and skip the return if check=True.
        """
        from git_commit_check import check_env_file_in_gitignore
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch(
            "git_commit_check.subprocess.run", return_value=mock_result
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

        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
        monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
        monkeypatch.delenv("GIT_CONFIG_SYSTEM", raising=False)

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

        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
        monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
        monkeypatch.delenv("GIT_CONFIG_SYSTEM", raising=False)

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
# check_hardcoded_secrets
# ---------------------------------------------------------------------------

class TestCheckHardcodedSecrets:
    """Tests for check_hardcoded_secrets() — API key and secret detection."""

    def test_detects_openai_key(self):
        from git_commit_check import check_hardcoded_secrets
        content = 'api_key = "sk-FAKE00000000000000000000000000"'
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["config.py"])
        assert len(errors) >= 1

    def test_detects_stripe_live_key(self):
        from git_commit_check import check_hardcoded_secrets
        content = 'key = "sk_live_FAKE0000000000000000"'
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["payment.py"])
        assert len(errors) >= 1
        assert any("stripe" in e.lower() for e in errors)

    def test_detects_stripe_test_key(self):
        from git_commit_check import check_hardcoded_secrets
        content = 'key = "sk_test_FAKE0000000000000000"'
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["payment.py"])
        assert len(errors) >= 1

    def test_detects_github_pat(self):
        from git_commit_check import check_hardcoded_secrets
        content = 'token = "ghp_FAKE00000000000000000000000000000000"'
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["deploy.py"])
        assert len(errors) >= 1
        assert any("github" in e.lower() for e in errors)

    def test_detects_github_oauth_token(self):
        from git_commit_check import check_hardcoded_secrets
        content = 'token = "gho_FAKE00000000000000000000000000000000"'
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["auth.py"])
        assert len(errors) >= 1

    def test_detects_slack_token(self):
        from git_commit_check import check_hardcoded_secrets
        content = 'token = "xoxb-1234567890-abcdefgh"'
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["slack.py"])
        assert len(errors) >= 1
        assert any("slack" in e.lower() for e in errors)

    def test_detects_generic_api_key_assignment(self):
        from git_commit_check import check_hardcoded_secrets
        content = 'api_key = "abcdefghijklmnopqrstuvwxyz1234"'
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["config.py"])
        assert len(errors) >= 1

    def test_detects_hardcoded_password(self):
        from git_commit_check import check_hardcoded_secrets
        content = 'password = "my_super_secret_password"'
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["db.py"])
        assert len(errors) >= 1

    def test_detects_aws_access_key_id(self):
        from git_commit_check import check_hardcoded_secrets
        content = 'aws_key = "AKIAIOSFODNN7EXAMPLE"'
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
        content = '-----BEGIN RSA PRIVATE KEY-----\nMIIE...'
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["deploy.py"])
        assert len(errors) >= 1
        assert any("private key" in e.lower() for e in errors)

    def test_detects_generic_private_key(self):
        from git_commit_check import check_hardcoded_secrets
        content = '-----BEGIN PRIVATE KEY-----\nMIIE...'
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["certs.py"])
        assert len(errors) >= 1
        assert any("private key" in e.lower() for e in errors)

    def test_detects_ec_private_key(self):
        from git_commit_check import check_hardcoded_secrets
        content = '-----BEGIN EC PRIVATE KEY-----\nMHQC...'
        with patch("git_commit_check.get_staged_file_content",
                   return_value=content):
            errors = check_hardcoded_secrets(["crypto.py"])
        assert len(errors) >= 1
        assert any("private key" in e.lower() for e in errors)

    def test_detects_openssh_private_key(self):
        from git_commit_check import check_hardcoded_secrets
        content = '-----BEGIN OPENSSH PRIVATE KEY-----\nb3Blbn...'
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
        content = 'token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc123def456_signature"'
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
        mock_result.stdout = "file1.py\nfile2.js\n"
        with patch("subprocess.run", return_value=mock_result):
            files = get_staged_files()
        assert files == ["file1.py", "file2.js"]

    def test_get_staged_files_empty(self):
        from git_commit_check import get_staged_files
        mock_result = MagicMock()
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            files = get_staged_files()
        assert files == []

    def test_get_staged_files_error(self):
        from git_commit_check import get_staged_files
        import subprocess
        with patch("subprocess.run",
                   side_effect=subprocess.CalledProcessError(1, "git")):
            files = get_staged_files()
        assert files == []

    def test_get_staged_file_content_success(self):
        from git_commit_check import get_staged_file_content
        mock_result = MagicMock()
        mock_result.stdout = "file content here"
        with patch("subprocess.run", return_value=mock_result):
            content = get_staged_file_content("test.py")
        assert content == "file content here"

    def test_get_staged_file_content_error(self):
        from git_commit_check import get_staged_file_content
        import subprocess
        with patch("subprocess.run",
                   side_effect=subprocess.CalledProcessError(1, "git")):
            content = get_staged_file_content("missing.py")
        assert content == ""


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
