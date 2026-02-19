"""
Location: pact-plugin/telegram/deps.py
Summary: Dependency checker and installer for the pact-telegram MCP server.
Used by: __main__.py (pre-flight check), telegram-setup command (installation).

Checks whether required Python packages (mcp, httpx) are importable and
optionally installs them from the bundled requirements.txt. The Stop hook
(notify.py) intentionally has zero external dependencies and is NOT checked
here -- it uses only stdlib urllib.request.

Design decisions:
- check_dependencies() is a pure check with no side effects
- install_dependencies() uses pip subprocess to install from requirements.txt
- Installation targets the user site-packages (--user) to avoid permission issues
"""

import importlib
import subprocess
import sys
from pathlib import Path

# Required packages for the MCP server (not the Stop hook)
REQUIRED_PACKAGES = [
    ("mcp", "mcp"),      # MCP SDK (FastMCP)
    ("httpx", "httpx"),   # Async HTTP client for Telegram API
]

# Path to requirements.txt relative to this file
REQUIREMENTS_FILE = Path(__file__).parent / "requirements.txt"


def check_dependencies() -> list[str]:
    """
    Check if required packages are importable.

    Returns:
        List of missing package names (pip install names). Empty list if
        all dependencies are satisfied.
    """
    missing = []
    for import_name, pip_name in REQUIRED_PACKAGES:
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append(pip_name)
    return missing


def install_dependencies(quiet: bool = False) -> tuple[bool, str]:
    """
    Install required dependencies from requirements.txt.

    Uses pip to install packages into the user's site-packages directory.
    Falls back to installing individual packages if requirements.txt is
    not found.

    Args:
        quiet: If True, suppress pip output (useful for automated setup).

    Returns:
        Tuple of (success: bool, message: str). The message contains
        pip output on failure or a success confirmation.
    """
    pip_args = [
        sys.executable, "-m", "pip", "install",
        "--user",
    ]

    if quiet:
        pip_args.append("--quiet")

    # Prefer requirements.txt for pinned versions
    if REQUIREMENTS_FILE.exists():
        pip_args.extend(["-r", str(REQUIREMENTS_FILE)])
    else:
        # Fallback: install packages directly (no version pins)
        missing = check_dependencies()
        if not missing:
            return True, "All dependencies already installed."
        pip_args.extend(missing)

    try:
        result = subprocess.run(
            pip_args,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode == 0:
            # Verify installation actually worked
            still_missing = check_dependencies()
            if still_missing:
                return False, (
                    f"pip succeeded but packages still not importable: "
                    f"{', '.join(still_missing)}. "
                    f"You may need to restart your Python environment."
                )
            return True, "Dependencies installed successfully."

        error_output = result.stderr.strip() or result.stdout.strip()
        return False, f"pip install failed:\n{error_output}"

    except subprocess.TimeoutExpired:
        return False, "pip install timed out after 120 seconds."
    except FileNotFoundError:
        return False, (
            "pip not found. Ensure Python and pip are installed and on PATH."
        )
    except Exception as e:
        return False, f"Unexpected error during installation: {e}"


def get_dependency_status() -> dict[str, bool]:
    """
    Get detailed status of each required dependency.

    Returns:
        Dict mapping package import name to whether it's importable.
        Example: {"mcp": True, "httpx": True}
    """
    status = {}
    for import_name, _ in REQUIRED_PACKAGES:
        try:
            importlib.import_module(import_name)
            status[import_name] = True
        except ImportError:
            status[import_name] = False
    return status


def get_optional_dependency_status() -> dict[str, bool]:
    """
    Get status of optional dependencies (for enhanced features).

    Currently checks:
    - openai: Required for voice note transcription via Whisper API

    Returns:
        Dict mapping feature name to whether its dependency is available.
    """
    optional = {}

    # Voice transcription requires the openai package OR direct HTTP (we use httpx)
    # Since we use httpx directly for Whisper API, this just checks if an
    # OpenAI API key is configured (not a package dependency)
    try:
        from telegram.config import load_config
        config = load_config()
        optional["voice_transcription"] = bool(config.get("openai_api_key"))
    except Exception:
        optional["voice_transcription"] = False

    return optional
