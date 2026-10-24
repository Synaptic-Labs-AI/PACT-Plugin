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

Used by: the suite-wide CLAUDE.md redirect in tests/conftest.py
(``_isolate_claude_md_target``) and the test modules that seed a syncable
CLAUDE.md by hand — test_working_memory_concurrency.py,
test_working_memory_concurrency_comprehensive.py,
test_working_memory_resolver_and_lock_release.py,
test_project_dir_resolution.py, test_memory_cli.py.

Per the thin-conftest rule this is a direct-import helper, not a pytest
fixture; nothing here is injected.
"""

from pathlib import Path


def seed_claude_md(
    home: Path, *, with_retrieved: bool = False, preferred: bool = True
) -> Path:
    """Create a minimal project CLAUDE.md.

    The Working Memory section is always present. When ``with_retrieved`` is set
    a Retrieved Context section is seeded too — exercising the section-replace
    branch rather than the section-create branch at the second site.

    ``preferred`` picks the dual-location slot. ``True`` (the default; every
    hand-seeding call site) writes ``home/.claude/CLAUDE.md`` — the
    resolver's preferred probe. ``False`` writes ``home/CLAUDE.md`` — the
    LEGACY slot — and creates ``home/.claude`` NOT AT ALL: the legacy slot
    is the lower-precedence probe, so the suite-wide conftest redirect
    seeds legacy-only and a test that builds either location itself wins
    its own world back (preferred by probe order, legacy by same-path
    overwrite). The fixture's file then exists only as the fallback for
    tests that build nothing.
    """
    if preferred:
        claude_dir = home / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        claude_md = claude_dir / "CLAUDE.md"
    else:
        claude_md = home / "CLAUDE.md"
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
