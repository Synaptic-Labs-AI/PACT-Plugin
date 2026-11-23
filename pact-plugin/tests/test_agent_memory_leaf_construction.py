"""No shipped, LLM-loaded file may CONSTRUCT an agent-memory leaf.

THE RULE THIS PINS. The platform hands each agent the absolute path to its own
memory directory. The leaf is GIVEN, never derived from the agent's type name —
so a shipped instruction that spells `~/.claude/agent-memory/<some-type>/` tells
the agent to build a path the platform did not give it, and the two forms are
not always the same directory.

WHY A PIN AND NOT A SWEEP. Seven sites across three files carried a constructed
leaf. Two were found by the work that introduced the rule; the other five only
because somebody swept afterwards. A sweep is something a person has to remember;
this is a red suite.

WHAT COUNTS AS THE DEFECT — and the distinction is the whole design. The GENERIC
form `~/.claude/agent-memory/` naming the directory is CORRECT and must pass: a
shipped plugin cannot name an absolute path, so describing the location is the
only thing it can do. Only a NAMED LEAF after the slash is the defect. A pin that
forbade the generic form would refuse the correct spelling and be worse than no
pin at all.

WHAT THIS DOES NOT COVER, stated here because a bound that lives only in a
handoff is lost at the next reading:
  * a path SPLIT ACROSS LINES — the scan is per-occurrence over the file text,
    so `agent-memory/` at the end of one line and the leaf on the next is not
    seen;
  * a leaf built by PROSE CONCATENATION — "the agent-memory directory, plus your
    agent type" constructs the same path with no slash-adjacent leaf to match;
  * a RENAMED directory — if `agent-memory` itself is spelled differently the
    scan finds nothing, which is why the liveness control below asserts the term
    still appears at all;
  * BACKSLASH separators — Windows-style paths are not matched.
The first two are the realistic gaps. Both are reachable by an author who is
paraphrasing rather than copying, which is exactly how the original seven arose.
"""

import re
from pathlib import Path

import pytest

# Surfaces that are loaded into an LLM's context as shipped instructions,
# including the plugin-root files an agent opens on demand one hop from its
# instructions. The enumeration follows the surface census in
# tests/test_test_run_scope_pins.py:176-180,191 (templates/*.md included,
# known member templates/environment-model.md).
_LLM_LOADED_DIRS = (
    "agents", "skills", "commands", "protocols", "reference", "templates"
)

_PLUGIN_ROOT = Path(__file__).parent.parent

# `agent-memory/` followed immediately by a path-segment character. Deliberately
# keyed on the DIRECTORY NAME rather than on the `~/.claude/` prefix, so an
# absolute path, a `$HOME` form or a bare relative one are all caught. The
# lookahead class covers a literal name, a `<placeholder>`, a `{template}` and a
# `$VAR` — the four spellings a future construction plausibly takes.
_CONSTRUCTED_LEAF = re.compile(r"agent-memory/(?=[A-Za-z0-9_{<$*])")

# Any mention at all, used only as a liveness control.
_ANY_MENTION = re.compile(r"agent-memory")


def _shipped_md_files():
    for d in _LLM_LOADED_DIRS:
        root = _PLUGIN_ROOT / d
        if root.is_dir():
            yield from sorted(root.rglob("*.md"))


def constructed_leaf_count(text):
    """Occurrences of a constructed agent-memory leaf in ``text``."""
    return len(_CONSTRUCTED_LEAF.findall(text))


def test_no_shipped_file_constructs_an_agent_memory_leaf():
    """The pin. Every LLM-loaded surface, every constructed leaf."""
    files = list(_shipped_md_files())

    # Liveness, both halves. A scan over no files, or over files that no longer
    # mention the directory at all, reports zero for the wrong reason — and a
    # zero for the wrong reason is what this whole class of defect is made of.
    assert files, (
        f"scanned no files under {_LLM_LOADED_DIRS} — the pin is measuring "
        f"nothing. Re-point it at the shipped instruction surfaces."
    )
    mentions = sum(len(_ANY_MENTION.findall(p.read_text(encoding="utf-8")))
                   for p in files)
    assert mentions, (
        "no shipped file mentions `agent-memory` at all. Either the directory "
        "was renamed — in which case this pin now guards a term nobody uses and "
        "must be re-pointed — or the scan broke. Do not read this as clean."
    )

    offenders = []
    for p in files:
        n = constructed_leaf_count(p.read_text(encoding="utf-8"))
        if n:
            offenders.append((str(p.relative_to(_PLUGIN_ROOT)), n))

    assert not offenders, (
        f"{len(offenders)} shipped file(s) construct an agent-memory leaf: "
        f"{offenders}. The platform gives each agent the absolute path to its "
        f"own memory directory; the leaf is GIVEN, never derived from the type "
        f"name. Describe the location generically (`~/.claude/agent-memory/`) "
        f"and tell the agent to use the path it was handed. NOTE the bounds in "
        f"this module's docstring: a leaf split across lines, or built by prose "
        f"concatenation, is NOT caught here."
    )


# --- Predicate certification. Both directions, over the SAME function the pin
# --- above calls, so this table certifies the shipped predicate and not a copy.

_MUST_FLAG = (
    ("literal type name", "see `~/.claude/agent-memory/pact-test-engineer/` for notes"),
    ("no trailing slash", "your dir is ~/.claude/agent-memory/pact-secretary"),
    ("angle placeholder", "write to `~/.claude/agent-memory/<agent-name>/`"),
    ("brace template", "path: ~/.claude/agent-memory/{agent_type}/MEMORY.md"),
    ("env-var leaf", "cd .claude/agent-memory/$AGENT_TYPE/"),
    ("HOME prefix", "$HOME/.claude/agent-memory/pact-auditor/index.md"),
    ("absolute prefix", "/Users/x/.claude/agent-memory/pact-architect/"),
    ("bare relative", "agent-memory/pact-preparer/MEMORY.md"),
)

_MUST_PASS = (
    ("generic, backtick close", "under `~/.claude/agent-memory/` — use the path you are given"),
    ("generic, mid-sentence", "the path contains `.claude/agent-memory/` and nothing more"),
    ("generic, sentence end", "notes live under ~/.claude/agent-memory/."),
    ("generic, space after", "stored in ~/.claude/agent-memory/ by the platform"),
    ("no slash at all", "the agent-memory directory the platform gave you"),
    ("prose only", "check your agent memory for relevant patterns"),
    ("generic, end of line", "the directory is ~/.claude/agent-memory/\nand the schema follows"),
)


@pytest.mark.parametrize("label,text", _MUST_FLAG)
def test_predicate_flags_a_constructed_leaf(label, text):
    """Non-vacuity: the predicate must FIRE on every construction spelling.

    A pin that has never fired has demonstrated nothing. These are the shapes a
    future construction plausibly takes — a literal name, a placeholder, a
    template, an environment variable, and three prefixes other than `~/`.
    """
    assert constructed_leaf_count(text) >= 1, (
        f"the {label} spelling was NOT flagged. The predicate pins the exact "
        f"spellings it happens to match rather than the class, so a "
        f"construction written this way would ship unnoticed."
    )


@pytest.mark.parametrize("label,text", _MUST_PASS)
def test_predicate_accepts_the_generic_form(label, text):
    """The over-block guard, and the constraint that decides the pin's worth.

    Naming the directory generically is the CORRECT thing for a shipped plugin
    to do — it cannot know the absolute path. Refusing this form would push
    authors toward spelling out a leaf, which is the defect. Reddening here
    means the pin has become worse than no pin.
    """
    assert constructed_leaf_count(text) == 0, (
        f"the {label} form is CORRECT and was flagged as a defect. This is an "
        f"over-block: it refuses the spelling the rule asks authors to use. "
        f"Narrow the predicate; do not change the shipped text to satisfy it."
    )
