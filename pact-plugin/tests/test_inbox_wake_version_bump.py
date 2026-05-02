"""
Version-bump consistency invariants for the current release.

The plugin version is tracked in 4 files; all four must carry the same
version literal, with zero stale references to the prior version.
"""

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TARGET_VERSION = "3.21.1"
PRIOR_VERSION = "3.21.0"


# ---------- 4-file version invariants ----------

def test_plugin_json_version():
    p = REPO_ROOT / "pact-plugin" / ".claude-plugin" / "plugin.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data.get("version") == TARGET_VERSION


def test_marketplace_json_version():
    p = REPO_ROOT / ".claude-plugin" / "marketplace.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    plugins = data.get("plugins", [])
    assert plugins, "marketplace.json missing plugins array"
    versions = {plugin.get("version") for plugin in plugins}
    assert TARGET_VERSION in versions, (
        f"marketplace.json: no plugin entry with version {TARGET_VERSION}; "
        f"saw {versions}"
    )


def test_root_readme_version():
    p = REPO_ROOT / "README.md"
    text = p.read_text(encoding="utf-8")
    assert TARGET_VERSION in text, (
        f"root README.md missing target version literal {TARGET_VERSION}"
    )


def test_pact_plugin_readme_version():
    p = REPO_ROOT / "pact-plugin" / "README.md"
    text = p.read_text(encoding="utf-8")
    assert TARGET_VERSION in text, (
        f"pact-plugin/README.md missing version literal {TARGET_VERSION}"
    )


# ---------- Stale-version sweep ----------

@pytest.mark.parametrize("path", [
    Path("pact-plugin") / ".claude-plugin" / "plugin.json",
    Path(".claude-plugin") / "marketplace.json",
    Path("README.md"),
    Path("pact-plugin") / "README.md",
])
def test_no_stale_prior_version_token(path):
    """No file in the 4-file set may carry the immediate-prior version
    string. Catches half-applied bumps."""
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    assert PRIOR_VERSION not in text, (
        f"{path}: stale prior version {PRIOR_VERSION!r} still present"
    )
