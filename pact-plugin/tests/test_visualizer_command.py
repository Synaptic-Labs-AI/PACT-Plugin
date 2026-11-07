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
        ["--demo", "--project", "--project-dir", "--session", "--stop", "--open"],
        ids=["demo", "project", "project-dir", "session", "stop", "open"],
    )
    def test_body_covers_each_argument_variant(self, flag):
        assert flag in _body(), (
            f"visualize.md argument contract advertises a variant that the "
            f"body never tells the model to pass: missing {flag!r}."
        )

    def _row(self, label):
        rows = [
            line for line in _body().splitlines()
            if line.strip().startswith(f"| {label}")
        ]
        assert len(rows) == 1, (
            f"visualize.md must carry exactly one {label!r} launch row; "
            f"found {len(rows)}"
        )
        return rows[0]

    def test_live_row_passes_the_project_root_not_a_basename(self):
        """The empty/live row hands the PROJECT ROOT to the launcher, which
        derives the journal slug with the hooks' own rule. A shell basename
        of $PWD is the worktree or subdirectory name, which matches no
        journal slug, and a basename of an unresolved symlink differs from
        the hooks' resolved slug."""
        row = self._row("empty / `live`")
        assert '--project-dir "$CLAUDE_PROJECT_DIR"' in row, (
            "the empty/live row must pass --project-dir \"$CLAUDE_PROJECT_DIR\""
        )
        assert "$PWD" not in row and "basename" not in row, (
            "the empty/live row must not derive the project from $PWD or a "
            "shell basename"
        )

    def test_live_row_handles_an_unset_project_dir(self):
        body = _body()
        assert "`$CLAUDE_PROJECT_DIR` is empty" in body, (
            "visualize.md must say what to do when $CLAUDE_PROJECT_DIR is empty"
        )

    def test_live_row_offers_the_session_id(self):
        assert "--session '<session id>'" in _body(), (
            "visualize.md must tell the model to pass this session's id when "
            "it is known, so a peer session in the same project is not opened"
        )

    def test_project_argument_is_validated_before_use(self):
        """$ARGUMENTS is free text substituted into a shell command, so the
        label must be checked against the allowlist before anything runs."""
        body = _body()
        assert "`^[A-Za-z0-9._-]+$`" in body, (
            "visualize.md must tell the model to accept a <project> argument "
            "only when it fully matches ^[A-Za-z0-9._-]+$"
        )
        assert "do not launch anything" in body.lower(), (
            "visualize.md must tell the model to launch nothing for an invalid label"
        )

    def test_project_row_single_quotes_the_label(self):
        row = self._row("`<project>`")
        assert "--project '<label>'" in row, (
            "the <project> row must single-quote the label: inside double "
            "quotes $( ) and backticks still expand"
        )
        assert '"<label>"' not in row

    def test_stop_scope_is_the_config_root(self):
        body = _body()
        assert "recorded for this config root" in body, (
            "the stop bullet must say it stops the server recorded for this "
            "config root, which another session or a shell may have started"
        )
        assert "this session launched" not in body

    def test_refusal_is_reported_not_acted_on(self):
        lines = [
            line for line in _body().splitlines()
            if "do not stop the server yourself" in line.lower()
        ]
        assert len(lines) == 1, (
            "visualize.md must tell the model to report a stop-it-first "
            "refusal and leave the running server alone"
        )
        assert "`Stop:`" in lines[0] and "`pact-viz:`" in lines[0], (
            "the refusal instruction must say to report the pact-viz: line "
            "together with the Stop: command"
        )

    def test_url_is_reported_with_its_key(self):
        body = _body()
        assert "including everything after `#`" in body, (
            "visualize.md must tell the model to report the URL with its "
            "fragment: the fragment carries the page's access key"
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
        lowered = body.lower()
        assert "equal path, not a fallback" in lowered, (
            "visualize.md must present the direct launch as an EQUAL path, not "
            "a fallback; the two launch paths drive the same server and the "
            "direct one costs no tokens."
        )

    def test_direct_launch_gives_the_user_an_absolute_path(self):
        """${CLAUDE_PLUGIN_ROOT} is set for the command's own Bash call, not
        in the user's shell, so the direct-launch advice must hand over the
        absolute launcher path from the Stop: line."""
        body = _body()
        assert "absolute path" in body and "`Stop:` line" in body, (
            "visualize.md must tell the model to give the user the launcher's "
            "absolute path from the Stop: line"
        )
        assert "`${CLAUDE_PLUGIN_ROOT}` is not set in their shell" in body

    def test_open_is_conditional_on_a_local_gui(self):
        """--open is appended only at a local GUI session. A body-wide word
        check passes on any mention of headless; this pins the condition in
        the same sentence as the flag, and keeps --open out of every row."""
        body = _body()
        sentences = [
            line for line in body.splitlines()
            if "--open" in line and "local GUI" in line
        ]
        assert sentences, (
            "visualize.md must say, in the line that names --open, that it is "
            "appended only at a local GUI session"
        )
        assert "SSH" in body and "headless" in body
        for label in ("empty / `live`", "`demo`", "`<project>`", "`stop`"):
            assert "--open" not in self._row(label), (
                f"the {label} row must not hard-code --open"
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
