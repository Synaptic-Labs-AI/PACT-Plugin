"""Regression gate: LLM-facing prose must not hardcode the Claude config root.

Agents follow prose literally, not the Python resolver, so a `~/.claude/...`
literal in an instruction surface makes a non-default-root install read, write,
and in one case execute against the WRONG install's state tree.

Three arms, two prose assertions, one allowlist, and a control per direction.
Implemented in pathlib rather than shell grep as a PORTABILITY requirement:
ARM 1's lookahead is PCRE and BSD `/usr/bin/grep` rejects `-P`. Where `grep -P`
appears to work, an interactive shell function may be rewriting `grep` to
`ugrep` — that function does not exist in CI.
"""

import re
from pathlib import Path

import pytest

# claude_md_ambient (module-wide): the prose-root refusal is this module's
# subject and is orthogonal to the CLAUDE.md resolution redirect; marked so
# the redirect is explicitly not applied here. See tests/conftest.py.
pytestmark = pytest.mark.claude_md_ambient

PLUGIN_ROOT = Path(__file__).parent.parent

# The census population. NOT the nine dirs the model gate walks, and no
# top-level iterdir pass: that scope yields 357 out-of-population hits across
# 86 files (tests 227, hooks 100, README 21, telegram 6, reference 3).
CENSUS_DIRS = ("agents", "commands", "skills", "protocols")

# Suffix ALLOWLIST, and it is load-bearing rather than defence-in-depth: it is
# the only mechanism that excludes `__pycache__/*.pyc`. No (path, literal,
# count) triple can key a pycache path — the count is a property of the
# reader's installed interpreters and moved 0 -> 13 -> 0 -> 13 -> 17 during
# development while nobody edited anything. Filter by suffix, never by
# directory name; suffix fails closed.
# Measured by reviewer-impl: 17 of 17 .pyc raise UnicodeDecodeError under a
# strict utf-8 read, and an errors="replace" read recovers 2 banned literals
# from them. So WITHOUT this filter ARM 1 HARD-REDS on allowlist-absent keys;
# it does not merely drift. `.sh` was added after measuring that exactly one
# .sh file sits in the population and carries zero hits - it was invisible to
# all three arms, which is a fail-OPEN on detection.
SCANNED_SUFFIXES = (".py", ".md", ".json", ".txt", ".yml", ".yaml",
                    ".toml", ".sh")

# This file necessarily contains the banned literals in its own allowlist.
SELF_EXCLUDED_FILES = frozenset({"tests/test_prose_root_gate.py"})

# ⚠️ THE `(?=/)` LOOKAHEAD IS LOAD-BEARING — DO NOT "FIX" IT.
# It is the only reason this arm stays quiet on the migration's OWN output.
# The prescribed shell form is `"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/..."`,
# where `$HOME/.claude` is followed by `}` and not `/`. Broadening to
# `(?=[/}])`, or dropping the lookahead, REDS ON CORRECTLY MIGRATED CODE — but
# NOT on the same text, and the distinction is the point:
#   BROADENING to `(?=[/}])` reds the SHELL form `"${CLAUDE_CONFIG_DIR:-$HOME/
#     .claude}/..."`, because `$HOME/.claude` there is followed by `}`. It
#     leaves the `{config_dir}` definition sentence QUIET.
#   DROPPING the lookahead reds BOTH the shell form AND every file carrying the
#     definition sentence, which writes a trailing backticked `~/.claude`.
# Spec site 1, the CRITICAL registration site, migrates to the shell form, so
# BOTH edits break it. Measured. See the control
# `test_arm1_stays_quiet_on_the_migrations_own_output` below.
ARM1 = re.compile(r"(?:~|\$HOME|\$\{HOME\})/\.claude(?=/)")

# Same firing behaviour as ARM1, extended to capture the path tail so an
# allowlist entry keys on WHICH literal is allowed. Without the tail, every
# entry would key on the bare root prefix and a file could swap an allowed
# `~/.claude/pact-memory/memory.db` for a forbidden `~/.claude/tasks/` while
# holding its count. Occurrence granularity, not line granularity.
ARM1_KEY = re.compile(r"(?:~|\$HOME|\$\{HOME\})/\.claude/[^\s`\"'),*]*")

# ARM 1 stops at `.claude/` and is therefore blind to a BARE config root with
# no path after it - `cd ~/.claude`, `CONFIG=~/.claude`. ARM 3 closes that.
# The exclusion set is the whole design and each character earns its place:
#   /  ARM 1's job, and firing here too would double-count
#   `  the `{config_dir}` definition sentence writes a trailing backticked
#      `~/.claude`; without this, every carrier of that sentence needs a triple
#   }  the migrated shell form ends `$HOME/.claude}` - WITHOUT THIS, ARM 3 REDS
#      ON CORRECTLY MIGRATED CODE, the same trap as ARM 1's lookahead
#   \w and -  `.claude-zai`, `.claudex` are OTHER installs, not this root
# Measured on the migrated tree: 0 occurrences, so this costs no allowlist
# entries. It can acquire them if someone writes a bare root deliberately.
ARM3 = re.compile(r"(?:~|\$HOME|\$\{HOME\})/\.claude(?![/`\w}-])")

# Targeted pin on known sites — NOT a class gate. Misses single-quoted
# '.claude', Path.home().joinpath(".claude"), os.path.expanduser("~/.claude"),
# and Path("~/.claude").expanduser(). Do not read it as covering the class.
ARM2 = re.compile(r"Path\.home\(\)\s*/\s*\"\.claude\"")

_DB_PIN = (
    "pact-memory DB, default-root-pinned by design at "
    "skills/pact-memory/scripts/config.py:186 (#1410)"
)
_DB_PIN_MIRRORED = (
    _DB_PIN + " — MIRRORED PAIR: this is the one allowlist entry with a SECOND "
    "mechanical detector. scripts/verify-protocol-extracts.sh reds if a future "
    "editor migrates one half without the other, so the count is not the sole "
    "guard here."
)
_TELEGRAM = (
    "a default-root-pinned code counterpart exists at telegram/config.py:35 "
    "(CONFIG_DIR = Path.home() / '.claude' / 'pact-telegram'). Migrating these "
    "would write the .env where the server never reads it."
)

# Premise pin for _TELEGRAM, which licenses 8 of the 15 allowlisted
# occurrences - the majority - and is the only justification here without one.
# _DB_PIN's premise is pinned by ARM2_ALLOWLIST; _DB_PIN_MIRRORED names a second
# detector; this had neither, so a "migrate telegram too" pass would silently
# invalidate both entries while this file stayed green.
#
# THIS READS telegram/config.py, WHICH CENSUS_DIRS DELIBERATELY EXCLUDES, AND
# THAT IS CORRECT. CENSUS_DIRS is the population of the VIOLATION SCAN. A
# premise pin is a different operation on a different file, and it reads outside
# that population BECAUSE THAT IS WHY THE ENTRY NEEDS A PIN AT ALL - the premise
# lives where the scan does not look. Do not "tidy" this into CENSUS_DIRS.
_TELEGRAM_PREMISE = ("telegram/config.py", 'Path.home() / ".claude" / "pact-telegram"')

# (path, literal, count, justification). The count closes the hole in BOTH
# directions: a new site pushes it above declared, a removed site drops it
# below, so a stale entry reds instead of outliving its reason.
ARM1_ALLOWLIST = [
    ("commands/telegram-setup.md", "~/.claude/pact-telegram", 2, _TELEGRAM),
    ("commands/telegram-setup.md", "~/.claude/pact-telegram/.env", 6, _TELEGRAM),
    ("protocols/pact-protocols.md", "~/.claude/pact-memory/memory.db", 1, _DB_PIN_MIRRORED),
    ("protocols/pact-state-recovery.md", "~/.claude/pact-memory/memory.db", 1, _DB_PIN_MIRRORED),
    ("skills/pact-memory/SKILL.md", "~/.claude/pact-memory/memory.db", 2, _DB_PIN),
    ("skills/pact-memory/scripts/cli.py", "~/.claude/pact-memory/...", 1,
     "illustrative, no consumer — a stderr-scrubbing comment. NOT 'pact-memory, "
     "by design': that reason is false here and would license the next bare "
     "pact-memory tail that DOES have a consumer."),
    ("skills/pact-memory/scripts/config.py", "~/.claude/pact-memory", 1, _DB_PIN),
    ("skills/pact-memory/scripts/database.py", "~/.claude/pact-memory/memory.db", 1, _DB_PIN),
]

ARM2_ALLOWLIST = [
    ("skills/pact-memory/scripts/config.py", 186, _DB_PIN),
]


def _scanned_files():
    for subdir in CENSUS_DIRS:
        for path in sorted((PLUGIN_ROOT / subdir).rglob("*")):
            if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
                continue
            rel = path.relative_to(PLUGIN_ROOT).as_posix()
            if rel in SELF_EXCLUDED_FILES:
                continue
            yield rel, path.read_text(encoding="utf-8", errors="replace")


def _allowlist_delta(found, declared):
    """Both directions of the allowlist comparison, in one place.

    Lifted out of the live check so the tail-discrimination arm below exercises
    THE SHIPPED COMPARISON rather than a copy of it - a reimplementation could
    drift from this while the suite stayed green.
    """
    stale = {k: (found.get(k, 0), n) for k, n in declared.items() if found.get(k, 0) < n}
    over = {k: (found[k], declared.get(k, 0)) for k in found if found[k] > declared.get(k, 0)}
    return stale, over


def _arm1_counts():
    found = {}
    for rel, text in _scanned_files():
        for match in ARM1_KEY.finditer(text):
            found[(rel, match.group(0))] = found.get((rel, match.group(0)), 0) + 1
    return found


# --------------------------------------------------------------------------
# ARM 1 — the census population


def test_arm1_allowlist_is_exact_in_both_directions():
    """Over AND under in ONE test, deliberately.

    Splitting the two directions across two tests means deleting one silently
    opens that direction. ARM 2 does both in a single test and is structurally
    stronger for it; this now matches.
    """
    declared = {(p, lit): n for p, lit, n, _ in ARM1_ALLOWLIST}
    stale, over = _allowlist_delta(_arm1_counts(), declared)
    assert not stale, (
        "Allowlist entries declare more occurrences than exist. The site was "
        f"fixed or moved; delete the entry rather than leave the hole: {stale}"
    )
    assert not over, (
        "LLM-facing prose hardcodes the Claude config root. Agents follow prose "
        "literally, so each of these misdirects a non-default-root install. "
        f"Migrate to `{{config_dir}}` (narrative) or "
        '`"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/..."` (shell), or add an '
        f"allowlist entry WITH a justification. found>declared: {over}"
    )


# The file and substitution used by the tail-discrimination arm. Chosen because
# it is the entry whose justification names this exact hazard.
_SWAP_FILE = "skills/pact-memory/SKILL.md"
_SWAP_FROM = "~/.claude/pact-memory/memory.db"
_SWAP_TO = "~/.claude/tasks/"
# The counterfactual key: ARM1_KEY with the tail capture removed.
_ROOT_ONLY_KEY = re.compile(r"(?:~|\$HOME|\$\{HOME\})/\.claude/")


def _counts_for(text, key_re):
    found = {}
    for match in key_re.finditer(text):
        k = (_SWAP_FILE, match.group(0))
        found[k] = found.get(k, 0) + 1
    return found


def test_arm1_key_discriminates_the_tail_not_only_the_count():
    """ARM1_KEY captures the path tail. This proves the tail does the work.

    A same-count tail swap - one allowed `memory.db` occurrence becoming a
    forbidden `tasks/` one, total held at 2 - must be DETECTED. But detection
    alone would not show the TAIL caught it, since any count change would also
    red. So the same input is run through a tail-LESS key, and must go SILENT.
    That second arm is the proof: remove the tail capture and this exact
    substitution slips through.

    Derived in memory from the real file via the SHIPPED regex - nothing is
    written to disk, so pytest's assertion-rewrite cache (which validates on
    mtime-seconds and size, and is NOT defeated by -p no:cacheprovider or
    PYTHONDONTWRITEBYTECODE=1) cannot serve stale bytecode here.
    """
    text = (PLUGIN_ROOT / _SWAP_FILE).read_text(encoding="utf-8")
    swapped = text.replace(_SWAP_FROM, _SWAP_TO, 1)
    declared = {(p, lit): n for p, lit, n, _ in ARM1_ALLOWLIST if p == _SWAP_FILE}

    before = _counts_for(text, ARM1_KEY)
    after = _counts_for(swapped, ARM1_KEY)
    assert sum(after.values()) == sum(before.values()), (
        "the substitution changed the occurrence COUNT, so the count alone would "
        "catch it and this arm would verify nothing about the tail"
    )
    assert len(after) > len(before), "the substitution did not produce a new tail"

    stale, over = _allowlist_delta(after, declared)
    assert stale and over, (
        f"a same-count tail swap was NOT detected. stale={stale} over={over}. "
        "If this fires, ARM1_KEY's tail capture does not do what its comment "
        "claims and the allowlist design needs rethinking."
    )

    rootless_declared = {(_SWAP_FILE, "~/.claude/"): sum(declared.values())}
    r_stale, r_over = _allowlist_delta(_counts_for(swapped, _ROOT_ONLY_KEY), rootless_declared)
    assert not r_stale and not r_over, (
        "the tail-LESS counterfactual also detected the swap, so this arm does "
        f"not isolate the tail. stale={r_stale} over={r_over}"
    )

    legit = text + "\n<!-- reflowed; no literal and no count changed -->\n"
    l_stale, l_over = _allowlist_delta(_counts_for(legit, ARM1_KEY), declared)
    assert not l_stale and not l_over, (
        f"an edit touching no keyed literal and no count reddened: "
        f"stale={l_stale} over={l_over}. The arm reds on any perturbation."
    )


def test_telegram_allowlist_premise_still_holds():
    """8 of 15 allowlisted occurrences rest on this one line. If it moves, they
    are unlicensed and telegram-setup.md's prose becomes a real defect."""
    rel, needle = _TELEGRAM_PREMISE
    text = (PLUGIN_ROOT / rel).read_text(encoding="utf-8")
    assert needle in text, (
        f"{rel} no longer binds its config dir to the default root, so the "
        f"_TELEGRAM justification is false and its 8 allowlisted occurrences in "
        f"commands/telegram-setup.md are unlicensed. Either restore the pin or "
        f"migrate the prose AND drop those entries - see #1206."
    )


def test_every_allowlist_entry_states_a_justification():
    """A wrong-but-plausible reason is more dangerous than a missing one."""
    thin = [(p, lit) for p, lit, _, why in ARM1_ALLOWLIST if len(why.strip()) < 40]
    thin += [(p, ln) for p, ln, why in ARM2_ALLOWLIST if len(why.strip()) < 40]
    assert not thin, f"allowlist entries without a real justification: {thin}"


# --------------------------------------------------------------------------
# ARM 2 — Path.home() in code


def test_arm2_finds_no_unallowlisted_home_join():
    declared = {(p, ln) for p, ln, _ in ARM2_ALLOWLIST}
    found = set()
    for subdir in CENSUS_DIRS:
        for path in sorted((PLUGIN_ROOT / subdir).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(PLUGIN_ROOT).as_posix()
            if rel in SELF_EXCLUDED_FILES:
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if ARM2.search(line):
                    found.add((rel, i))
    assert not found - declared, f"undeclared Path.home()/'.claude' sites: {found - declared}"
    assert not declared - found, (
        f"stale ARM 2 entries — the site moved or was fixed: {declared - found}"
    )


def test_arm3_finds_no_bare_config_root():
    """A bare `~/.claude` with no path after it - `cd ~/.claude`, `X=~/.claude`.

    ARM 1 cannot see these: it requires a trailing slash. Measured at zero on
    the migrated tree, so there is no allowlist for this arm; if a deliberate
    bare root ever lands, add one here with a justification.
    """
    hits = [(rel, i) for rel, text in _scanned_files()
            for i, line in enumerate(text.splitlines(), 1) if ARM3.search(line)]
    assert not hits, (
        "bare config-root literal in LLM-facing prose. An agent told to `cd` "
        f"here lands in the wrong install on a non-default root: {hits}"
    )


# --------------------------------------------------------------------------
# Prose assertions


PLACEHOLDER_TOKENS = (
    "{team_name}", "{session_dir}", "{plugin_root}", "{lead_address}", "{config_dir}",
)
PAIR = ("commands/bootstrap.md", "agents/pact-orchestrator.md")


def test_placeholder_declaration_present_in_both_carriers():
    """Keyed on the TOKEN SET, never on a sentence.

    The two declarations have never been byte-identical — bootstrap says
    "use ... as literal brace-wrapped placeholders", the orchestrator says
    "contain literal ... strings" and is a markdown bullet. A substring pin on
    either phrasing matches the file it was written against and is VACUOUSLY
    SILENT on the file it exists to protect.
    """
    for rel in PAIR:
        text = (PLUGIN_ROOT / rel).read_text(encoding="utf-8")
        assert any(all(t in line for t in PLACEHOLDER_TOKENS) for line in text.splitlines()), (
            f"{rel} no longer declares the placeholder token set. Both carriers "
            "must name all five tokens together; substitution is manual textual "
            "replacement and an agent reading only one file must still learn it."
        )


def test_config_dir_is_defined_where_it_is_used():
    """Conditional on TOKEN PRESENCE, never on file-was-touched, and NOT on
    adjacency. Measured gaps across carriers run 0 to 4 and are all correct, so
    an adjacency predicate reds correct files; and a migrated file that carries
    no `{config_dir}` at all (pact-auditor.md) must pass, not fail.
    """
    undefined = []
    for rel, text in _scanned_files():
        if "{config_dir}" not in text or not rel.endswith(".md"):
            continue
        lines = text.splitlines()
        first_use = next(i for i, l in enumerate(lines, 1) if "{config_dir}" in l)
        defs = [i for i, l in enumerate(lines, 1) if "CLAUDE_CONFIG_DIR" in l]
        # ponytail: 20-line window, measured max is 4 — generous headroom, still
        # catches "defined in an unrelated section" and "never defined at all".
        if not defs or min(abs(d - first_use) for d in defs) > 20:
            undefined.append((rel, first_use, defs[:1]))
    assert not undefined, (
        "files use `{config_dir}` without defining it near first use. An agent "
        "reading the instruction cannot resolve the placeholder: " + str(undefined)
    )


# --------------------------------------------------------------------------
# Controls — every assertion shown able to fire, in all three directions


def test_control_arm1_fires_on_each_spelling():
    for spelling in ("~/.claude/x/", "$HOME/.claude/x/", "${HOME}/.claude/x/"):
        assert ARM1.search(spelling), f"ARM 1 blind to {spelling}"


def test_control_arm1_stays_quiet_on_a_different_root():
    for quiet in ("$CLAUDE_PROJECT_DIR/.claude/CLAUDE.md",  # project dir, not config root
                  "`.claude/` as a file category",           # narrative
                  "~/.claude-zai/tasks/",                    # a different install
                  "~/.claude"):                              # bare root, no path
        assert not ARM1.search(quiet), f"ARM 1 over-fires on {quiet}"


def test_control_arm1_stays_quiet_on_the_migrations_own_output():
    """The direction that is easiest to omit: the gate must not red on the cure.

    Quiet ONLY because of the `(?=/)` lookahead. If this test fails, someone
    broadened or removed it — restore it rather than allowlisting the fallout.
    """
    for cured in ("{config_dir}/tasks/{team}/{id}.json",
                  'python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/protocols/x.py"',
                  'python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}"/protocols/x.py'):
        assert not ARM1.search(cured), (
            f"ARM 1 reds on correctly migrated code: {cured}. The `(?=/)` "
            "lookahead is load-bearing; see the comment at its definition."
        )


def test_control_arm3_fires_on_a_bare_root():
    for bare in ("~/.claude", "cd ~/.claude", "CONFIG=$HOME/.claude", 'set "~/.claude"'):
        assert ARM3.search(bare), f"ARM 3 blind to {bare}"


def test_control_arm3_stays_quiet_on_everything_it_must_not_claim():
    for quiet in ("~/.claude/tasks/",
                  "`~/.claude`",
                  "~/.claude-zai/x", "~/.claudex",
                  'python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/x"',
                  '"${CLAUDE_CONFIG_DIR:-$HOME/.claude}"'):
        assert not ARM3.search(quiet), (
            f"ARM 3 over-fires on {quiet}. Every character in its exclusion set "
            "is load-bearing; see the comment at its definition."
        )


def test_control_arm2_fires_and_discriminates():
    assert ARM2.search('Path.home() / ".claude" / "pact-memory"')
    assert not ARM2.search("get_claude_config_dir() / 'pact-memory'")


def test_control_scan_population_is_non_empty():
    """An empty walk produces zero hits, which is byte-identical to a clean
    tree. Without this, a broken CENSUS_DIRS reads as a passing gate."""
    files = list(_scanned_files())
    assert len(files) > 50, f"scan reached only {len(files)} files — walk is broken"
    assert any(ARM1_KEY.search(t) for _, t in files), (
        "no allowlisted site was reached; the walk or the pattern is dead"
    )
