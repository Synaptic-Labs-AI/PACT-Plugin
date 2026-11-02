"""The instruction surfaces name the project memory file the way the code emits it.

Location: pact-plugin/tests/test_project_memory_line_instructions.py

The SessionStart hook names this session's project memory file on one line
(`session_resume.format_project_memory_line`), and `cli.py project-file`
prints the same path when that line is missing from the context. Every
instruction site that reads the project memory file tells the agent to read
that line, to run that command when the line is gone, and to stop when
either source says the file is unavailable. These pins hold the two sides
together:

  - each site names the `Project memory file:` line, the exact
    `cli.py" project-file` invocation in its own plugin-root spelling, and
    the unavailable stop;
  - `$CLAUDE_PROJECT_DIR/.claude/CLAUDE.md` appears only after "fails to run";
  - the sites that compare the target with a checkout path compare resolved
    paths (`realpath`);
  - the label they quote is the emitted line's actual prefix, the subcommand
    they run is one the CLI's parser accepts, and the invocation is the
    store-access bar's NON_ROUTE_INVOCATIONS entry.

So renaming the emitted line, the subcommand or the carve-out without the
instruction text (or the reverse) turns one of these red. Each site's text is
read with its whitespace flattened, because the files hard-wrap prose.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
LINE_LABEL = "Project memory file:"
INVOCATION_TAIL = '/skills/pact-memory/scripts/cli.py" project-file'

# Each site and the plugin-root spelling its own text uses.
SITES = {
    "commands/pin-memory.md": "{plugin_root}",
    "commands/bootstrap.md": "{plugin_root}",
    "commands/wrap-up.md": "{plugin_root}",
    "commands/prune-memory.md": "${CLAUDE_PLUGIN_ROOT}",
    "agents/pact-orchestrator.md": "{plugin_root}",
}
RESOLVED_PATH_SITES = ["commands/pin-memory.md", "commands/prune-memory.md"]


def _flat(relative: str) -> str:
    return " ".join((PLUGIN / relative).read_text(encoding="utf-8").split())


@pytest.mark.parametrize("site", SITES)
def test_each_site_names_the_project_memory_line(site):
    assert f"`{LINE_LABEL}`" in _flat(site), site


@pytest.mark.parametrize("site", SITES)
def test_each_site_names_the_cli_fallback(site):
    assert f'python3 "{SITES[site]}{INVOCATION_TAIL}' in _flat(site), site


@pytest.mark.parametrize("site", SITES)
def test_each_site_stops_when_the_file_is_unavailable(site):
    """The CLI prints `unavailable: <cause>` and exits 3 on an unknown root.
    That is not a failure to run, so a site without this clause leaves the
    agent no branch for it."""
    assert "says the file is unavailable" in _flat(site), site


@pytest.mark.parametrize("site", SITES)
def test_the_environment_path_appears_only_after_the_command_fails(site):
    """`$CLAUDE_PROJECT_DIR/.claude/CLAUDE.md` is the last resort: every
    mention follows "fails to run" in the same paragraph."""
    text = (PLUGIN / site).read_text(encoding="utf-8")
    for paragraph in text.split("\n\n"):
        flat = " ".join(paragraph.split())
        for match in re.finditer(r"\$CLAUDE_PROJECT_DIR/\.claude/CLAUDE\.md", flat):
            assert "fails to run" in flat[: match.start()], (
                site, flat[max(0, match.start() - 200): match.start()]
            )


@pytest.mark.parametrize("site", RESOLVED_PATH_SITES)
def test_a_path_comparison_uses_resolved_paths(site):
    """A project reached through a symlink names the symlinked path while git
    prints the real one, so a textual comparison misreads an in-checkout file
    as outside it."""
    assert "realpath" in _flat(site), site


def test_the_quoted_label_is_the_emitted_lines_prefix(tmp_path):
    from shared.claude_md_manager import ManagedDocument
    from shared.project_scope import SessionRoot
    from shared.session_resume import format_project_memory_line

    doc = ManagedDocument(tmp_path / ".claude" / "CLAUDE.md", tmp_path, True)

    line = format_project_memory_line(SessionRoot(tmp_path, True, tmp_path, None), doc)

    assert line.startswith(f"{LINE_LABEL} `{doc.path}`"), line


def test_the_invoked_subcommand_is_one_the_cli_accepts():
    from scripts.cli import build_parser

    subcommand = INVOCATION_TAIL.rsplit(" ", 1)[1]

    assert build_parser().parse_args([subcommand]).command == subcommand


def test_the_invocation_is_the_store_access_carve_out():
    from test_store_access_bar_presence import NON_ROUTE_INVOCATIONS

    assert INVOCATION_TAIL.lstrip("/") in NON_ROUTE_INVOCATIONS, sorted(
        NON_ROUTE_INVOCATIONS
    )
