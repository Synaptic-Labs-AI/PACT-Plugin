"""
Version-bump consistency invariants for the current release.

The plugin version is tracked in 4 files; all four must carry the same
version literal. TARGET_VERSION is read from plugin.json at test time so
the suite tracks every future bump without manual edits.

PRIOR_VERSION stale-sweep is disabled: with TARGET_VERSION sourced
dynamically there is no canonical prior to enumerate, and explicit prior-
version stale-sweeps belong to release-engineering checklists rather than
suite-level invariants.
"""

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN_JSON_PATH = (
    REPO_ROOT / "pact-plugin" / ".claude-plugin" / "plugin.json"
)
TARGET_VERSION = json.loads(PLUGIN_JSON_PATH.read_text(encoding="utf-8"))[
    "version"
]


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


