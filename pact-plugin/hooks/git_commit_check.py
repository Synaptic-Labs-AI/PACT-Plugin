#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/git_commit_check.py
Summary: PreToolUse hook that validates git commits for PACT protocol compliance.
Used by: Claude Code settings.json PreToolUse hook (matcher: Bash for git commit)

Enforces:
- SACROSANCT Rule 1: No credentials/secrets in committed files
- SACROSANCT Rule 2: No frontend credential exposure, backend proxy pattern
- .env file protection in .gitignore

Input: JSON from stdin with tool_input containing the command
Output: Exit code 2 to block, 0 to allow; errors to stderr
"""

import sys
import json
import subprocess
import re
from pathlib import Path

from shared.error_output import hook_error_json

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def get_staged_files():
    """Returns a list of staged files."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--cached"],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip().splitlines()
    except subprocess.CalledProcessError:
        return []


def get_staged_file_content(filename):
    """Returns the content of a staged file."""
    try:
        result = subprocess.run(
            ["git", "show", f":{filename}"],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout
    except subprocess.CalledProcessError:
        return ""


def check_security(staged_files):
    """
    Check for basic security violations in staged files.

    Args:
        staged_files: List of staged file paths

    Returns:
        List of error messages for any violations found
    """
    errors = []

    # 1. Check for .env files being committed
    for f in staged_files:
        if f.endswith('.env') or '/.env' in f or f.startswith('.env'):
            errors.append(f"SACROSANCT VIOLATION: Attempting to commit environment file: {f}")

    # 2. Check for sensitive data in logs
    risky_patterns = [
        r'console\.log\s*\(.*process\.env',
        r'print\s*\(.*os\.environ',
        r'console\.log\s*\(.*password',
        r'print\s*\(.*password',
        r'console\.log\s*\(.*secret',
        r'print\s*\(.*secret',
        r'console\.log\s*\(.*api[_-]?key',
        r'print\s*\(.*api[_-]?key',
        r'console\.log\s*\(.*token',
        r'print\s*\(.*token',
    ]

    code_extensions = ('.js', '.ts', '.jsx', '.tsx', '.py', '.mjs', '.cjs')

    for f in staged_files:
        if f.endswith(code_extensions):
            content = get_staged_file_content(f)
            for pattern in risky_patterns:
                if re.search(pattern, content, re.IGNORECASE):
                    errors.append(
                        f"SECURITY: Potential secret exposure in log in {f}: "
                        f"matches pattern '{pattern}'"
                    )

    return errors


def check_frontend_credentials(staged_files):
    """
    SACROSANCT Rule 2: Check for credential exposure in frontend code.

    Frontend environment variables with credential suffixes should not be used
    as they expose credentials in client-side bundles.

    Args:
        staged_files: List of staged file paths

    Returns:
        List of error messages for any violations found
    """
    errors = []

    # Patterns indicating credential usage in frontend env vars
    credential_patterns = [
        r'VITE_[A-Z_]*(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|AUTH)',
        r'REACT_APP_[A-Z_]*(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|AUTH)',
        r'NEXT_PUBLIC_[A-Z_]*(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|AUTH)',
        r'NUXT_PUBLIC_[A-Z_]*(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|AUTH)',
        r'process\.env\.(VITE_|REACT_APP_|NEXT_PUBLIC_|NUXT_PUBLIC_)[A-Z_]*(?:KEY|SECRET|TOKEN)',
        r'import\.meta\.env\.(VITE_)[A-Z_]*(?:KEY|SECRET|TOKEN)',
    ]

    # Frontend file extensions
    frontend_extensions = {'.jsx', '.tsx', '.vue', '.svelte'}
    # Also check .js and .ts if they're in frontend directories
    frontend_dirs = {'src', 'components', 'pages', 'app', 'frontend', 'client', 'ui'}

    for f in staged_files:
        is_frontend_ext = any(f.endswith(ext) for ext in frontend_extensions)
        is_frontend_dir = any(
            f'/{d}/' in f or f.startswith(f'{d}/') for d in frontend_dirs
        )

        # Check frontend-specific files or JS/TS in frontend directories
        should_check = is_frontend_ext or (
            f.endswith(('.js', '.ts')) and is_frontend_dir
        )

        if should_check:
            content = get_staged_file_content(f)
            for pattern in credential_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                if matches:
                    errors.append(
                        f"SACROSANCT VIOLATION: Frontend credential exposure in {f}. "
                        f"Found: {matches[0]}. Credentials must NEVER be in frontend code. "
                        "Use backend proxy pattern instead."
                    )

    return errors


def check_direct_api_calls(staged_files):
    """
    SACROSANCT Rule 2: Warn about potential direct API calls from frontend.

    Frontend code should call backend endpoints, not external APIs directly
    (which would require credentials in frontend).

    Args:
        staged_files: List of staged file paths

    Returns:
        List of warning messages (non-blocking)
    """
    warnings = []

    # Patterns suggesting direct external API calls
    direct_api_patterns = [
        (r'fetch\s*\(\s*[\'"`]https?://api\.', 'fetch to external API'),
        (r'axios\.[a-z]+\s*\(\s*[\'"`]https?://api\.', 'axios to external API'),
        (r'fetch\s*\(\s*[\'"`]https?://[^/]*\.stripe\.com', 'direct Stripe API call'),
        (r'fetch\s*\(\s*[\'"`]https?://[^/]*\.openai\.com', 'direct OpenAI API call'),
        (r'fetch\s*\(\s*[\'"`]https?://[^/]*\.anthropic\.com', 'direct Anthropic API call'),
        (r'fetch\s*\(\s*[\'"`]https?://[^/]*\.github\.com/(?!repos/[^/]+/[^/]+$)', 'direct GitHub API call'),
        (r'fetch\s*\(\s*[\'"`]https?://[^/]*\.googleapis\.com', 'direct Google API call'),
    ]

    # Frontend file extensions and directories
    frontend_extensions = {'.jsx', '.tsx', '.vue', '.svelte', '.js', '.ts'}
    frontend_dirs = {'src', 'components', 'pages', 'app', 'frontend', 'client', 'ui'}
    # Backend directories to exclude
    backend_dirs = {'server', 'api', 'backend', 'lib', 'services', 'handlers'}

    for f in staged_files:
        is_frontend_ext = any(f.endswith(ext) for ext in frontend_extensions)
        is_frontend_dir = any(
            f'/{d}/' in f or f.startswith(f'{d}/') for d in frontend_dirs
        )
        is_backend = any(
            f'/{d}/' in f or f.startswith(f'{d}/') for d in backend_dirs
        )

        # Only warn for frontend files, not backend
        if is_frontend_ext and is_frontend_dir and not is_backend:
            content = get_staged_file_content(f)
            for pattern, description in direct_api_patterns:
                if re.search(pattern, content, re.IGNORECASE):
                    warnings.append(
                        f"SACROSANCT Warning: Potential {description} in {f}. "
                        "Verify backend proxy pattern is used."
                    )
                    break  # One warning per file

    return warnings


def check_env_file_in_gitignore():
    """
    Verify .env is ignored by git's full ignore chain (global excludes,
    per-repo excludes, parent-dir .gitignores, repo-root .gitignore).

    Delegates to `git check-ignore -q .env` rather than reading .gitignore
    directly, which closes ignore-chain false negatives (global excludes,
    .git/info/exclude, parent .gitignores) and `!.env` false positives.

    Fail-open posture on detection-mechanism errors: returns
    (False, "SACROSANCT WARNING: ..."). The WARNING substring routes to
    main()'s warnings list (non-blocking). The complementary staged-file
    check (check_security) independently blocks .env committed files, so
    a warning here is safe.

    Returns:
        Tuple of (is_protected, error_message or None)
    """
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", ".env"],
            capture_output=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return False, (
            "SACROSANCT WARNING: 'git check-ignore' timed out; "
            "cannot verify .env protection."
        )
    except FileNotFoundError:
        return False, (
            "SACROSANCT WARNING: git binary not found on PATH; "
            "cannot verify .env protection."
        )

    if result.returncode == 0:
        return True, None
    if result.returncode == 1:
        return False, (
            "SACROSANCT VIOLATION: .env is not ignored by git. "
            "Add '.env' to .gitignore (repo), ~/.config/git/ignore (global), "
            "or .git/info/exclude (per-repo private)."
        )
    if result.returncode == 128:
        return False, (
            "SACROSANCT WARNING: 'git check-ignore' reports not in a git repo "
            "(exit 128); cannot verify .env protection."
        )
    return False, (
        f"SACROSANCT WARNING: 'git check-ignore' exited {result.returncode}; "
        "cannot verify .env protection."
    )


def check_hardcoded_secrets(staged_files):
    """
    Check for hardcoded secrets and API keys in code.

    Args:
        staged_files: List of staged file paths

    Returns:
        List of error messages for any violations found
    """
    errors = []

    # Patterns that suggest hardcoded secrets
    secret_patterns = [
        # API keys with common prefixes
        (r'["\']sk-[a-zA-Z0-9]{20,}["\']', 'OpenAI API key'),
        (r'["\']sk_live_[a-zA-Z0-9]{20,}["\']', 'Stripe live key'),
        (r'["\']sk_test_[a-zA-Z0-9]{20,}["\']', 'Stripe test key'),
        (r'["\']ghp_[a-zA-Z0-9]{36,}["\']', 'GitHub personal access token'),
        (r'["\']gho_[a-zA-Z0-9]{36,}["\']', 'GitHub OAuth token'),
        (r'["\']xox[baprs]-[a-zA-Z0-9-]{10,}["\']', 'Slack token'),
        # Anthropic API keys (start with sk-ant-api)
        (r'["\']sk-ant-api[a-zA-Z0-9_-]{20,}["\']', 'Anthropic API key'),
        # Google API keys (start with AIza)
        (r'["\']AIza[a-zA-Z0-9_-]{30,}["\']', 'Google API key'),
        # Twilio Account SID (starts with AC followed by 32 hex chars)
        (r'["\']AC[a-f0-9]{32}["\']', 'Twilio Account SID'),
        # AWS access key IDs (always start with AKIA for long-term keys)
        (r'["\']AKIA[0-9A-Z]{16}["\']', 'AWS access key ID'),
        # Private key headers (PEM format)
        (r'-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----', 'Private key'),
        # JWT tokens (three base64url segments separated by dots)
        (r'["\']eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}["\']', 'JWT token'),
        # Google Cloud service account key (JSON key file marker)
        (r'"type"\s*:\s*"service_account"', 'Google Cloud service account key'),
        # Azure connection strings
        (r'DefaultEndpointsProtocol=https?;AccountName=[^;]+;AccountKey=[^;]+', 'Azure Storage connection string'),
        (r'Server=tcp:[^;]+;.*Password=[^;]+', 'Azure SQL connection string'),
        # Generic patterns
        (r'api[_-]?key\s*[=:]\s*["\'][a-zA-Z0-9]{20,}["\']', 'API key assignment'),
        (r'secret[_-]?key\s*[=:]\s*["\'][a-zA-Z0-9]{20,}["\']', 'Secret key assignment'),
        (r'password\s*[=:]\s*["\'][^"\']{8,}["\']', 'Hardcoded password'),
    ]

    code_extensions = ('.js', '.ts', '.jsx', '.tsx', '.py', '.java', '.go', '.rs', '.rb')

    for f in staged_files:
        if f.endswith(code_extensions):
            content = get_staged_file_content(f)
            for pattern, description in secret_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                if matches:
                    # Truncate the match for display
                    match_preview = matches[0][:30] + '...' if len(matches[0]) > 30 else matches[0]
                    errors.append(
                        f"SACROSANCT VIOLATION: Potential {description} in {f}: {match_preview}"
                    )

    return errors


def main():
    try:
        # Read input from stdin
        input_data = json.load(sys.stdin)
        tool_input = input_data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Check if the command is a git commit
        if not re.search(r'\bgit\s+commit\b', command):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)  # Not a commit command, allow it

        staged_files = get_staged_files()

        # If no files are staged, let git handle the error
        if not staged_files:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # Collect all errors and warnings
        security_errors = []
        warnings = []

        # --- SACROSANCT Security Checks ---

        # Basic security check (env files, logging secrets)
        security_errors.extend(check_security(staged_files))

        # SACROSANCT Rule 1: Check for hardcoded secrets
        security_errors.extend(check_hardcoded_secrets(staged_files))

        # SACROSANCT Rule 2: Frontend credential exposure
        security_errors.extend(check_frontend_credentials(staged_files))

        # SACROSANCT Rule 2: Direct API call warnings (non-blocking)
        warnings.extend(check_direct_api_calls(staged_files))

        # Check .gitignore protection for .env files
        env_protected, env_error = check_env_file_in_gitignore()
        if env_error:
            if "VIOLATION" in env_error:
                security_errors.append(env_error)
            else:
                warnings.append(env_error)

        # --- Output Warnings (non-blocking) ---
        if warnings:
            print("PACT Security Warnings:", file=sys.stderr)
            print("-" * 30, file=sys.stderr)
            for w in warnings:
                print(f"  * {w}", file=sys.stderr)
            print("-" * 30, file=sys.stderr)
            print("Review these warnings before deployment.", file=sys.stderr)
            print("", file=sys.stderr)

        # --- Block on Security Errors ---
        if security_errors:
            print("Error: PACT Security Violation", file=sys.stderr)
            print("=" * 40, file=sys.stderr)
            for err in security_errors:
                print(f"* {err}", file=sys.stderr)
            print("=" * 40, file=sys.stderr)
            print("Please fix security issues before committing.", file=sys.stderr)
            print("See SACROSANCT rules in CLAUDE.md for guidance.", file=sys.stderr)
            sys.exit(2)  # Block the tool execution

        print(_SUPPRESS_OUTPUT)
        sys.exit(0)  # Allow the commit

    except Exception as e:
        # If something goes wrong in the hook, log it but don't block
        print(f"Hook Error (git_commit_check): {e}", file=sys.stderr)
        print(hook_error_json("git_commit_check", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
