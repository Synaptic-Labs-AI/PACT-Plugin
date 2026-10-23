"""
The canonical minimal project CLAUDE.md seeder.

Location: pact-plugin/tests/fixtures/claude_md_seed.py

Summary: creates ``<home>/.claude/CLAUDE.md`` containing the minimal section
skeleton the working-memory sync sites operate on — a ``# Project`` title and
a Working Memory section, with an optional Retrieved Context section for the
section-replace branch. Consolidated here from the module-local
``_seed_claude_md`` definitions that previously lived (verbatim or as a
byte-identical default arm) in the consuming modules; the public name follows
the fixtures/ direct-import convention (``hf_cache_env``, ``emitter.py``).

Used by: the test modules that seed a syncable CLAUDE.md by hand —
test_working_memory_concurrency.py,
test_working_memory_concurrency_comprehensive.py,
test_working_memory_resolver_and_lock_release.py,
test_project_dir_resolution.py, test_memory_cli.py.

Per the thin-conftest rule this is a direct-import helper, not a pytest
fixture; nothing here is injected.
"""

from pathlib import Path


def seed_claude_md(home: Path, *, with_retrieved: bool = False) -> Path:
    """Create a minimal project CLAUDE.md.

    The Working Memory section is always present. When ``with_retrieved`` is set
    a Retrieved Context section is seeded too — exercising the section-replace
    branch rather than the section-create branch at the second site.
    """
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    claude_md = claude_dir / "CLAUDE.md"
    parts = ["# Project\n\n"]
    if with_retrieved:
        parts.append(
            "## Retrieved Context\n"
            "<!-- Auto-managed by pact-memory skill. -->\n\n"
        )
    parts.append(
        "## Working Memory\n"
        "<!-- Auto-managed by pact-memory skill. -->\n\n"
    )
    claude_md.write_text("".join(parts), encoding="utf-8")
    return claude_md
