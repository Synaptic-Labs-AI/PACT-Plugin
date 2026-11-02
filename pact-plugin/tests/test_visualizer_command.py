"""
Location: pact-plugin/tests/test_visualizer_command.py
Summary: Structural guard for the /PACT:visualize slash command
         (commands/visualize.md) and its plugin.json registration. Pins the
         frontmatter the slash-command UI reads, the launch surface the body
         instructs (the detached pact-viz launch with its demo / project /
         stop variants), the equal token-free direct-launch option, the
         reporting contract, and freedom from planning artifacts and the
         repo's globally pinned prose phrases on this LLM-loaded surface.
Used by: pact-plugin test suite (standing merge gate).

The command is a thin launcher for PACT Mission Control; the viewer itself is
covered by test_visualizer_assets.py, the backend by test_visualizer_data.py /
test_visualizer_server.py. This file gates the surface a future edit is most
likely to break silently: the argument-hint the UI advertises, the exact
binary and flags the body tells the model to run, and the launch path that
must stay presented as an equal option rather than a fallback.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers import parse_frontmatter
from test_planning_artifact_scrub import PATTERN_CATALOG

PLUGIN_ROOT = Path(__file__).parent.parent
COMMAND_PATH = PLUGIN_ROOT / "commands" / "visualize.md"
PLUGIN_JSON_PATH = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"

ARGUMENT_HINT = "[demo|live|<project>|stop]"

# The literal the body must instruct, quoted so the plugin-root variable
# expands inside a Claude Code session.
LAUNCH_LITERAL = '"${CLAUDE_PLUGIN_ROOT}/bin/pact-viz"'

# Globally pinned prose phrases from other surface gates. They are load-bearing
# wording in the files that own them; a copy on an unrelated command surface
# would satisfy a pin's occupancy check for a site that no longer carries it.
FOREIGN_PINNED_PHRASES = (
    "Wait for coders to produce initial output before observing",
    "observe the staged diff",
    "See metadata.teachback_rejection",
    "See metadata.handoff_rejection",
)


def _text() -> str:
    return COMMAND_PATH.read_text(encoding="utf-8")


def _body() -> str:
    text = _text()
    _, _, rest = text.partition("---")
    _, _, body = rest.partition("---")
    return body


def _planning_artifact_hits(text: str) -> list[tuple[str, int, str]]:
    """Planning-artifact matches in `text`, using the repo's scrub catalog.

    Reusing the catalog rather than restating it keeps this arm and
    test_planning_artifact_scrub.py from drifting. A line carrying an
    exemption marker is skipped, matching the scrub's own semantics, so a
    deliberately exempted example does not turn this arm into a false red.
    """
    hits = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if "planning-artifact-exempt" in line:
            continue
        for spec in PATTERN_CATALOG:
            for match in spec.pattern.finditer(line):
                hits.append((spec.name, lineno, match.group(0)))
    return hits


class TestCommandSurface:
    def test_command_file_exists(self):
        assert COMMAND_PATH.is_file(), (
            f"{COMMAND_PATH} is missing. /PACT:visualize is registered in "
            f"plugin.json, so an absent file resolves the slash command to "
            f"nothing."
        )

    def test_frontmatter_parses(self):
        fm = parse_frontmatter(_text())
        assert fm is not None, "visualize.md has invalid YAML frontmatter"

    def test_description_is_present_and_substantive(self):
        fm = parse_frontmatter(_text())
        description = fm.get("description", "").strip()
        assert description, "visualize.md frontmatter has no description"
        assert len(description) >= 20, (
            f"visualize.md description is too terse for the slash-command UI: "
            f"{description!r}"
        )

    def test_argument_hint_matches_the_documented_contract(self):
        fm = parse_frontmatter(_text())
        assert fm.get("argument-hint") == ARGUMENT_HINT, (
            f"visualize.md argument-hint must be {ARGUMENT_HINT!r} so the "
            f"command's accepted arguments stay discoverable; got "
            f"{fm.get('argument-hint')!r}"
        )

    def test_body_is_kept_small(self):
        """The command loads on every invocation; it stays a launcher rather
        than a second copy of the viewer's README. The ceiling is loose on
        purpose — it catches a documentation dump, not a reworded paragraph."""
        body_lines = len(_body().splitlines())
        assert 15 <= body_lines <= 80, (
            f"visualize.md body is {body_lines} lines; keep the launcher small "
            f"(roughly 30-50 lines). Viewer detail belongs in "
            f"visualizer/README.md, which this command can point at."
        )


class TestPluginRegistration:
    def test_plugin_json_parses(self):
        json.loads(PLUGIN_JSON_PATH.read_text(encoding="utf-8"))

    def test_command_is_registered(self):
        plugin = json.loads(PLUGIN_JSON_PATH.read_text(encoding="utf-8"))
        assert "./commands/visualize.md" in plugin.get("commands", []), (
            "plugin.json must register ./commands/visualize.md in `commands`; "
            "otherwise /PACT:visualize is an installed-but-unreachable command."
        )

    def test_every_registered_command_file_exists(self):
        """Registration-to-file control: a registration that names no file is
        invisible to the presence arm above, which only reads the one entry."""
        plugin = json.loads(PLUGIN_JSON_PATH.read_text(encoding="utf-8"))
        missing = [
            entry
            for entry in plugin.get("commands", [])
            if not (PLUGIN_ROOT / entry).is_file()
        ]
        assert not missing, f"plugin.json registers missing command files: {missing}"


class TestLaunchInstructions:
    def test_body_launches_the_bundled_binary_detached(self):
        body = _body()
        assert LAUNCH_LITERAL in body, (
            f"visualize.md must launch {LAUNCH_LITERAL} so the command works "
            f"from the installed plugin root regardless of the user's cwd."
        )
        assert "--detach" in body, (
            "visualize.md must launch detached; a foreground server would "
            "block the Claude turn it was launched from."
        )

    @pytest.mark.parametrize(
        "flag",
        ["--demo", "--project", "--stop", "--open"],
        ids=["demo", "project", "stop", "open"],
    )
    def test_body_covers_each_argument_variant(self, flag):
        assert flag in _body(), (
            f"visualize.md argument contract advertises a variant that the "
            f"body never tells the model to pass: missing {flag!r}."
        )

    def test_body_reports_url_and_stop_lines(self):
        body = _body()
        assert "URL:" in body, (
            "visualize.md must tell the model to report the tool's `URL:` line "
            "verbatim — that is the only way a headless session can surface the "
            "viewer."
        )
        assert "Stop:" in body, (
            "visualize.md must tell the model to report the tool's `Stop:` line "
            "when stopping, so the user knows which instance ended."
        )

    def test_direct_launch_is_presented_as_an_equal_option(self):
        body = _body()
        assert f"{LAUNCH_LITERAL} --open" in body, (
            "visualize.md must name the direct launch "
            f"`{LAUNCH_LITERAL} --open`; it is the token-free path and must "
            "not be omitted."
        )
        assert "equal" in body.lower(), (
            "visualize.md must present the direct launch as an EQUAL path, not "
            "a fallback — the two launch paths drive the same server and the "
            "direct one costs no tokens."
        )

    def test_body_does_not_open_a_browser_unconditionally(self):
        """--open is conditional on a local GUI session; the body must not
        instruct an unconditional browser open on headless machines."""
        body = _body()
        lowered = body.lower()
        assert "headless" in lowered or "ssh" in lowered, (
            "visualize.md must mention the headless/SSH condition under which "
            "--open is omitted; otherwise a headless session runs a browser "
            "open that cannot succeed and drops the URL report."
        )


class TestSurfaceHygiene:
    def test_no_planning_artifacts(self):
        hits = _planning_artifact_hits(_text())
        assert not hits, (
            "visualize.md is LLM-loaded and must carry no planning artifacts "
            f"(PR refs, SHAs, version literals, task ids, letter labels): "
            f"{hits}. Strip them or, for a deliberate example, add the repo's "
            "`planning-artifact-exempt` marker."
        )

    @pytest.mark.parametrize("phrase", FOREIGN_PINNED_PHRASES)
    def test_foreign_pinned_phrase_absent(self, phrase):
        assert phrase not in _text(), (
            f"visualize.md copied {phrase!r} from the surface that pins it. "
            "Those phrases are load-bearing occupancy anchors for other files; "
            "a copy here can satisfy a pin whose real site was deleted. Use "
            "behavioural wording local to this command instead."
        )
