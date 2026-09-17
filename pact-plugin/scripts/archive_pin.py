#!/usr/bin/env python3
"""
Archive a CLAUDE.md pin to pact-memory and report whether its bytes arrived.

Location: pact-plugin/scripts/archive_pin.py

Summary: Single-responsibility archival step for /PACT:prune-memory. Given
the index of a pin in the `evictable_pins` list that `check_pin_caps.py
--status` emits, this script extracts that pin's block VERBATIM from
CLAUDE.md, saves it to pact-memory via the memory CLI, re-fetches the saved
record by the returned `memory_id`, and asserts that the pin block is
present in the fetched record. It emits a three-outcome verdict as JSON on
stdout and ALWAYS exits 0.

**It never deletes anything.** The eviction Edit stays with the command, so
the destructive act remains visible to the curator and to the pin_caps_gate
PreToolUse hook. This script measures; it does not decide.

    The SCRIPT never refuses -- it measures and reports.
    The COMMAND refuses, in prose, on the reported verdict.

That split is why a fail-open here does not become a fail-open DECISION. A
hook fail-open would allow the destructive Edit; a script fail-open only
produces an UNEVALUABLE that the command must handle.

Usage:
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/archive_pin.py" --index N
  # tests only, and the store must be PRESENT first: the memory CLI refuses a
  # `--db-path` naming an absent store for each command other than `setup`.
  python3 .../pact-memory/scripts/cli.py setup --db-path /tmp/test.db
  python3 archive_pin.py --index 0 --db-path /tmp/test.db

Output (stdout, JSON, ALWAYS exit 0). `outcome`, `heading`, `claude_md_path`
and `delete_string` are present as KEYS in every verdict; when a value is null
that is governed by the invariant below, not by a per-failure enumeration.
`heading` is NEVER derived from the requested index:

  {"outcome": "ARCHIVED",               "heading": str, "claude_md_path": str,
   "delete_string": str, "memory_id": str, "chars": int, "contained": true,
   "occurrences": 1}
  {"outcome": "ARCHIVED_DELETE_UNSAFE", "heading": str, "claude_md_path": str,
   "delete_string": str, "memory_id": str, "occurrences": int,
   "locations": [int], "reason": str,
   "sync_status": str,        # ONLY on the sync-breach arm
   "sync_scope": str}         # ONLY when that arm ATTEMPTED a write
  {"outcome": "NOT_ARCHIVED",           "heading": str, "claude_md_path": str,
   "delete_string": str, "memory_id": str|null, "reason": str}
  {"outcome": "UNEVALUABLE",            "heading": str|null,
   "claude_md_path": str|null, "delete_string": str|null, "reason": str}

FOUR outcomes. Only ARCHIVED permits the removal Edit to proceed.

`delete_string` is the verbatim block, a CONTENT handle: the removal is
`Edit(old_string=delete_string, new_string="")`, which matches on content, so
a file that moved under the caller makes the Edit FAIL LOUDLY rather than
delete the wrong bytes. It is a property of WHICH PIN, not of whether the
archive worked, so it is present on NOT_ARCHIVED and on a located-pin
UNEVALUABLE too. Uniqueness is verified after the save. Presence is governed
by the invariant below.

ARCHIVED_DELETE_UNSAFE means the archive SUCCEEDED and the removal must not
proceed automatically. TWO CONDITIONS REACH IT, and they share ONE disposition
-- content safe, no automatic Edit, remove by hand, no escape hatch -- which is
why they share one outcome NAME rather than forking into a fifth:

  NOT UNIQUE       the block is not unique, so an Edit keyed on it would be
                   ambiguous.
  SYNC NOT SUPPRESSED  the save reported a `sync_status` other than
                   `suppressed`, so the CLAUDE.md projection that `--no-sync`
                   exists to prevent was attempted or performed. That arm adds
                   `sync_status`, and adds `sync_scope` when the save ATTEMPTED
                   a write (`wrote` or `failed`) -- because `occurrences` and
                   `locations` describe the TARGET file, while a stray
                   projection may sit in a DIFFERENT CLAUDE.md. `sync_scope`
                   is EXISTENCE-INDEPENDENT: it says where a copy must be IF
                   one exists, never that one does, which is why `failed` may
                   carry it. The statuses that never reach the write --
                   `refused`, `unresolved`, `missing` -- omit it because the
                   bound there is true but VACUOUS, not because it is false,
                   and because the key's ABSENCE is itself the signal that no
                   write was attempted. See `_WRITE_ATTEMPTED_STATUSES` for
                   why a uniform key would destroy that signal.

Two conditions under one outcome is NOT the reason-table hazard named below:
that hazard is one outcome carrying two DISPOSITIONS, distinguished only by
prose. These two carry the same disposition, and `_unsafe_reason` already
varies its prose across two conditions for the same reason. It is a
distinct outcome rather than a reason on another one because THE OUTCOME NAME
MUST DETERMINE THE DISPOSITION -- one outcome with two dispositions forces a
reason table, which is how a permission gets inherited by a condition it was
never designed for. It offers no escape hatch, and does not trap the curator
at the cap: the content is archived, so manual removal is a redirect.

THE CONTRACT, stated as the INVARIANT THE CODE ENFORCES rather than as a
list of which failures null which fields:

    EACH FIELD IS PRESENT IFF THE FACT IT NAMES WAS ACTUALLY ESTABLISHED.

  `claude_md_path`  the file this run read.  Established once resolution
                    succeeds -- so it survives EVERY later failure.
  `heading`         which pin.  Established once the pin is located.
  `delete_string`   a USABLE handle for the removal Edit.  Established once
                    the block is sliced AND is non-empty AND is verbatim in
                    the source.

An enumeration ("null only when X") is a claim about observed data and is
falsified by the first path nobody thought of -- which is exactly how the
previous version of this contract came to be false the day it was written.
The invariant above cannot be falsified by adding a fourth failure path,
because it says what the code guarantees rather than what it was seen doing.

TWO CONSEQUENCES a reader is likely to misread as bugs, so they are stated:

1. A LATER failure never reports LESS than an earlier one.  Context is
   attached at the boundary where it becomes known and is never dropped
   afterwards, so a verdict's context grows monotonically with how far the
   run got.  A post-resolution CLI failure therefore reports MORE than a
   pre-resolution bad index, not less.  (It once reported less; that was the
   bug this contract now pins.)

2. Two post-resolution paths DELIBERATELY omit `delete_string`, and adding
   it there would be a regression rather than a consistency fix.  An empty
   block and a block that fails the source-side verbatim tripwire are both
   cases where a handle was computed but is NOT USABLE -- an Edit keyed on
   either would fail to match.  Emitting a handle known to be bad is worse
   than emitting none, because the caller's whole reason to trust it is that
   it was checked.  `heading` and `claude_md_path` are still present on both,
   because those facts WERE established.

Used by:
  - commands/prune-memory.md: invoked before the removal Edit; the verdict
    gates whether the eviction proceeds
  - tests/test_archive_pin.py

Related:
  - scripts/check_pin_caps.py -- supplies the `--index` coordinate system
  - hooks/pin_caps.py -- parse_pins / Pin, the parser this reuses
  - skills/pact-memory/scripts/cli.py -- the save/get surface, reached by
    SUBPROCESS rather than import (keeps the process boundary the rest of
    the codebase keeps, and the CLI is the tested public surface)
<!-- PACT_STORE_BAR_BEGIN -->
**STORE ACCESS.** A memory operation (save, search, get, list, update or
delete a record) goes through the pact-memory CLI. YOU DO NOT SELECT A
STORE. Do not name a store by `--db-path`, by an environment variable, or by
one more route somebody adds later. Let the CLI resolve it. A store you
select is not the store the memory of the team lives in, so a save there is
lost rather than shared. STORE INSPECTION is different: a row count, a
column audit, or a schema check on the file. To inspect, do not run a CLI
verb, do not import a module below `skills/pact-memory/scripts/`, and do not
open the store read-write. In ONE command, against ONE resolved path, check
that `memory.db-wal` and `memory.db-shm` are both absent by their full
names, then open with `mode=ro` and `immutable=1`. Without `immutable=1` the
open fails. If a sidecar is present, stop and report. The read does not load
the vector extension, so it cannot answer a question about `vec_memories`.
Stop and report rather than take a barred route.
<!-- PACT_STORE_BAR_END -->
The `pact-memory` skill carries the full rule.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Load pin_caps and staleness from hooks/ by explicit file path. Identical
# rationale to check_pin_caps.py: `sys.path.insert(0, hooks_dir)` would
# PREPEND, so a future hooks/types.py or hooks/json.py would shadow the
# stdlib. Spec-loading binds by path, not by name resolution; hooks_dir is
# APPENDED (not prepended) so the `shared` package resolves -- staleness's
# `from shared.claude_md_manager` and this module's `shared.project_scope` --
# with the stdlib retaining priority on any name collision.
_HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.append(str(_HOOKS_DIR))

# The memory CLI. Reached by subprocess, never imported: skills/ is a
# separate surface from scripts/, and cli.py is the tested public contract.
_MEMORY_CLI = (
    Path(__file__).resolve().parent.parent
    / "skills" / "pact-memory" / "scripts" / "cli.py"
)

# Wall-clock ceiling for a single memory-CLI call. `save` spins the embedding
# backend; a hang must surface as UNEVALUABLE (refuse, pin survives) rather
# than blocking the curator's session indefinitely.
_CLI_TIMEOUT_SECONDS = 120

# Structured archive marker, written into `entities[].type`.
#
# THIS IS A NAMED CONSTANT ON PURPOSE, and any code that SCANS for archives
# must read THIS constant rather than re-typing the literal. `entities[].type`
# is unconstrained free text -- thousands of distinct values across the store,
# with nothing validating any of them -- so the field is structured by POSITION,
# not by SCHEMA. A typo or a second copy that drifts therefore ships SILENTLY:
# the scanner returns a smaller set and raises no error. A confident undercount
# is worse than a loud failure, which is why the value is pinned by a test.
#
# The value matches the one prior archive in the store that carries a type at
# all. Adopting it rather than inventing a second value avoids forking the
# convention -- which is the whole problem this marker exists to end, and
# forking it inside the fix would be the same defect one level up.
#
# SCOPE OF WHAT THIS BUYS -- discoverability GOING FORWARD, never enumeration.
# Archives written before this ships carry no marker at all, including ones
# that were folded into an existing record rather than saved standalone. Any
# count derived by scanning for archives is a FLOOR, not a total. Do not let a
# docstring, test name, or comment claim this marker identifies pin archives
# generally; it identifies the ones written after it ships.
ARCHIVE_ENTITY_TYPE = "pact_memory_archive"


def is_archive_record(entities) -> bool:
    """True if `entities` carries ARCHIVE_ENTITY_TYPE as an entity TYPE.

    THE PREDICATE IN CODE, so an audit does not have to reconstruct it from
    prose. Every prior use of this marker hand-rolled the match at the call
    site, and the hand-rolled version people reach for first is a SUBSTRING
    test over the serialised blob -- which also matches a record that merely
    MENTIONS the marker in a name or a note. Measured on a live store: the
    substring form returned 19 where this returns 18, and the extra row was a
    memory ABOUT the marker. A document describing the audit joined the
    population the audit was counting.

    Accepts the stored JSON string or an already-decoded list.

    TOTAL BY DESIGN: anything unparseable, or shaped unexpectedly, is False
    rather than an exception. An audit that dies on one malformed row reports
    nothing; one that skips it reports a floor -- and a floor is what this
    marker yields anyway, per the scope note above.
    """
    if isinstance(entities, (str, bytes)):
        try:
            entities = json.loads(entities)
        except (ValueError, TypeError):
            return False
    if not isinstance(entities, list):
        return False
    return any(
        isinstance(item, dict) and item.get("type") == ARCHIVE_ENTITY_TYPE
        for item in entities
    )


# The same predicate for callers querying the store directly. Bind
# ARCHIVE_ENTITY_TYPE as the single parameter.
#
# ⚠️ `j.type = 'object'` IS LOAD-BEARING AND THE OBVIOUS GUARD DOES NOT WORK.
# Entity members are not uniformly objects -- a live store held 63 JSON strings
# among 14092 objects -- and `json_extract` on a bare string raises
# "malformed JSON", failing the whole query rather than skipping the row.
#
# The natural defence, `json_type(j.value) = 'object' AND json_extract(...)`,
# RAISES IDENTICALLY: `json_type` RE-PARSES the value, so the guard itself
# throws before the AND can short-circuit. `j.type` is the column `json_each`
# already computed while walking the array, so it costs no second parse and is
# the only spelling that filters rather than raising. Verified against a real
# store on sqlite 3.54.0.
ARCHIVE_RECORD_COUNT_SQL = (
    "SELECT COUNT(DISTINCT m.id) FROM memories m, json_each(m.entities) j "
    "WHERE j.type = 'object' AND json_extract(j.value, '$.type') = ?"
)

# The memory CLI subcommand archival is permitted to use. See the STANDALONE
# invariant on `_build_record` / `archive_pin`: an `update` would create no new
# record and run no marker code, silently voiding the audit property.
_ARCHIVE_SUBCOMMAND = "save"

# Memory-CLI subcommands whose handler projects into CLAUDE.md, and which
# therefore accept `--no-sync`. `_run_memory_cli` suppresses the projection on
# these when the caller named no project (no `cwd`), because there is then no
# target to project INTO and inheriting an ambient one would be a guess.
#
# NOT a general "commands that touch memory" list, and deliberately narrow:
# `--no-sync` is declared on the `save` subparser ONLY, so passing it to a
# subcommand that does not declare it is an argparse error -- the fix would
# manufacture an over-block on `get`. `search` suppresses its own sync inside
# its handler and takes no flag, so it does not belong here either.
#
# Kept honest by a test that reads the CLI's real parser and asserts this set
# equals the subparsers actually DECLARING `--no-sync`. Note what that does and
# does not catch: a subcommand that declares the flag and is missing from this
# set fails the test, but a subcommand that GAINS A SYNC WITHOUT DECLARING THE
# FLAG is invisible to it -- the detector compares against declarers, so a
# non-declarer is outside the population it examines. Closing that would need a
# different predicate (which handlers project into CLAUDE.md), which the parser
# does not expose.
_SYNC_CAPABLE_SUBCOMMANDS = frozenset({"save"})

# `SyncResult` statuses on which the save ATTEMPTED a CLAUDE.md projection, and
# which therefore have a bound worth reporting as `sync_scope`.
#
# THE MEMBERSHIP TEST IS "WAS A WRITE ATTEMPTED", NOT "DID ONE LAND". `failed`
# is a member because the `except` producing it wraps the atomic rename, with a
# lock release and a log call running after that rename inside the same `try` --
# so a durable write can still report `failed`. Excluding it would drop the
# scope from the status where a stray copy is MOST plausible.
#
# `refused`, `unresolved` and `missing` are absent because all three return or
# raise BEFORE the write is attempted, so there is no projection from this save
# to bound. Their bound would be TRUE but VACUOUS -- they are excluded for
# vacuity, never because the scope would be false.
#
# ⚠️ AND THE VACUITY ARGUMENT ALONE DOES NOT DEFEND THIS SET. THIS IS THE
# CANONICAL STATEMENT OF WHY; the other sites point here.
#
# Vacuity says the key would be POINTLESS on those three. It gives no ground
# to refuse the edit this set actually has to survive -- adding the key
# everywhere "for schema uniformity", which is not pointless, it is TIDY. A
# maintainer can accept every word above and still make that change.
#
# THE REASON THAT REFUSES IT IS ABOUT SIGNAL, NOT WASTE: under a
# present-iff-attempted rule, THE ABSENCE OF THE KEY IS ITSELF INFORMATION --
# it says no write was attempted, and a consumer can branch on that. Add the
# key to all five and it stops distinguishing anything: a meaningful signal is
# traded for a uniform one, which is the exact trade this whole consumer
# exists to reverse. So the uniform version is not merely wasteful, it is
# DESTRUCTIVE, and that is what makes this set defensible rather than
# arbitrary.
#
# Keep both reasons. Vacuity explains why nobody wanted the key there; signal
# is what stops someone adding it anyway.
#
# NOT DERIVED FROM `SyncResult`, deliberately, AND THE SAME PAIR APPLIES. The
# classification reason is that this names a property of the ARCHIVE's route
# rather than of the enum, so a seventh reason must be classified by whoever
# adds it instead of being defaulted in by a filter. That is true and it is
# also defeasible on its own -- it argues about CORRECTNESS OF CLASSIFICATION
# and says nothing against a uniform set. The signal argument above is what
# refuses that edit here too; both guards defend the same change, so they are
# stated together rather than left to defend it separately and fail.
_WRITE_ATTEMPTED_STATUSES = frozenset({"wrote", "failed"})


def _load_hook_module(name: str):
    """Load a module from hooks/ by explicit file path.

    Registers the loaded module in sys.modules under `name` before executing
    so modules loaded via this same helper resolve `from {name} import ...`
    against the already-loaded object (staleness does `from pin_caps import`).
    """
    module_path = _HOOKS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, str(module_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_pin_caps = _load_hook_module("pin_caps")
_staleness = _load_hook_module("staleness")

parse_pins = _pin_caps.parse_pins
_PIN_HEADING_RE = _pin_caps._PIN_HEADING_RE
_parse_pinned_section = _staleness._parse_pinned_section
get_project_claude_md_path = _staleness.get_project_claude_md_path
# The (path, base) form. `base` is the directory the resolver ACTUALLY found
# the file under, captured before descending into `.claude` -- a trusted
# pre-resolve anchor rather than a re-derivation from the returned path.
_resolve_project_claude_md_with_base = (
    _staleness._resolve_project_claude_md_with_base
)
_find_existing_claude_md = _staleness._find_existing_claude_md
# Lexical inverse of the resolver's base/CLAUDE.md construction. Purely
# lexical (pathlib .parent never follows symlinks), and already locked to
# the resolver's own shape by TestStalenessLexicalBaseParity -- so it is
# the right primitive for project attribution, not a second derivation.
_lexical_base_of = _staleness._lexical_base_of


class _Unevaluable(Exception):
    """Raised when the pin's state cannot be established at all.

    Distinct from a definite archival failure. Carries an optional heading
    for the cases where the pin WAS resolved before the failure occurred.
    """

    def __init__(self, reason: str, heading: str | None = None,
                 claude_md_path: str | None = None,
                 delete_string: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.heading = heading
        # The block to remove, when the pin RESOLVED before the failure. It is
        # a property of WHICH PIN, not of whether the archive worked, so the
        # escape-hatch path gets a mechanical boundary too and no consumer has
        # to re-derive one.
        self.delete_string = delete_string
        # The file this run read, when one was resolved before the failure.
        # None only when resolution itself failed -- there is genuinely no
        # path to name, and inventing one would be worse than null.
        self.claude_md_path = claude_md_path


def _span_start(pinned_content: str, heading_start: int, date_comment) -> int:
    """Offset where a pin's span begins: its date-comment LINE, else its heading.

    Extracted so the START of pin N and the END of pin N-1 are computed by the
    SAME rule. Deriving the end from the next HEADING instead would make the
    two asymmetric and let each block swallow the following pin's date comment.
    """
    if not date_comment:
        return heading_start
    preceding = pinned_content[:heading_start]
    # rfind takes the NEAREST preceding occurrence, so an identical comment
    # string inside an earlier pin's body cannot capture the span.
    comment_at = preceding.rfind(date_comment)
    if comment_at == -1:
        return heading_start
    # Back up to that LINE's first character, so indentation is inside the span.
    return preceding.rfind("\n", 0, comment_at) + 1


def extract_pin_block(pinned_content: str, index: int, pins) -> str:
    """Return the pin's block as a VERBATIM SLICE of `pinned_content`.

    THIS IS A SLICE, NOT A RECONSTRUCTION, and the distinction is the whole
    point. Rebuilding the block as `date_comment + "\\n" + heading + "\\n" +
    body` looks equivalent but is not: `parse_pins` walks BACKWARD over blank
    lines to find the date comment and stores it `.strip()`ed, so a rebuilt
    block silently drops any blank line between comment and heading and any
    trailing whitespace on the comment line. Measured across five plausible
    hand-written pin formats, a rebuild is verbatim in only 2 of 5.

    That matters because the archival success criterion asserts containment
    of THIS string in the re-fetched record. If this string is already a
    lossy rendering of the pin, all three conjuncts still hold and the
    verdict certifies a variant -- a check reporting success while measuring
    nothing, which is the exact defect this subsystem exists to remove. A
    slice makes the criterion meaningful by making the block verbatim BY
    CONSTRUCTION, so the containment test measures the only thing it ever
    could: whether the storage round-trip preserved the bytes.

    Span rule:
      - `end`   = the NEXT PIN'S SPAN START (its date-comment line, else its
                  heading), or end of section for the last pin. NOT the next
                  `### ` heading -- see the boundary note below; an earlier
                  revision of this docstring said heading-start and a reader
                  acted on it, which is why the rule is stated once, here.
      - `start` = walk backward from the heading start over blank lines; if the
                  first non-blank preceding line matches the date-comment
                  pattern, `start` is the offset of THAT LINE'S FIRST CHARACTER
                  (so leading indentation is inside the slice); otherwise
                  `start` is the heading start.
      - Take `source[start:end]` EXACTLY. Do not strip, rejoin, or normalize.

    The no-strip rule means the block carries the blank line(s) separating it
    from the next pin. That is safe and was measured rather than assumed:
    `context` is a scalar, so it escapes the string-list normalization, and a
    value with one trailing newline, two trailing newlines, or a trailing
    newline plus spaces all round-trip BYTE-EXACT through the real CLI. Had
    any of those been stripped in transit, containment would have returned
    false for every archive -- an over-block on the whole feature.

    THE END BOUNDARY IS THE NEXT PIN'S SPAN START, NOT THE NEXT HEADING.
    Running to the next `### ` would put the FOLLOWING pin's date comment
    inside this pin's block, because a span starts at the comment line, which
    sits BEFORE the heading. The archived record would then carry another
    pin's pinned-date -- still a verbatim substring, so the containment check
    could never notice, and still "correct" by every conjunct in the criterion.
    Computing both edges with `_span_start` makes the spans partition the
    section instead of overlapping.

    Args:
        pinned_content: The Pinned Context section body (what parse_pins ate).
        index: Position of the pin within that section.
        pins: The full parsed Pin list -- the NEXT pin's date_comment is needed
            to place this pin's end boundary.

    Raises:
        _Unevaluable: if the section's headings no longer agree with `index`.
    """
    starts = [m.start() for m in _PIN_HEADING_RE.finditer(pinned_content)]
    if index < 0 or index >= len(starts) or index >= len(pins):
        raise _Unevaluable(
            f"pin index {index} out of range (section has {len(starts)} pins)"
        )

    block_start = _span_start(pinned_content, starts[index], pins[index].date_comment)
    if index + 1 < len(starts) and index + 1 < len(pins):
        block_end = _span_start(
            pinned_content, starts[index + 1], pins[index + 1].date_comment
        )
    else:
        block_end = len(pinned_content)

    return pinned_content[block_start:block_end]


# The project-identity predicate, and the reader for the session's worktree
# record it takes, live in hooks/shared/project_scope.py, where the
# working-memory projection uses the same ones, so the two refusals cannot
# drift apart. Imported through the `shared` PACKAGE, which the hooks directory
# appended to sys.path above makes importable, so this module and every other
# importer hold ONE module object -- and skills/ stays reached by subprocess
# only. Re-exported, never wrapped: a wrapper could acquire behaviour and
# become a second definition.
from shared.project_scope import (  # noqa: E402
    get_worktree_identity_from_session_record as _get_worktree_identity_from_session_record,
    stays_in_declared_project as _stays_in_declared_project,
)


def resolve_claude_md():
    """Resolve the CLAUDE.md to operate on, refusing a cross-project fallback.

    Returns (path, base). RAISES `_Unevaluable` rather than silently operating
    on a file the invocation never named.

    THE DEFECT THIS CLOSES. Resolution order is CLAUDE_PROJECT_DIR -> git
    common-dir parent -> CWD, and a miss at any step falls through SILENTLY.
    So CLAUDE_PROJECT_DIR naming a directory with no CLAUDE.md does not fail --
    it resolves to a DIFFERENT project's file and reports a confident success.
    Reproduced deliberately: with the env var naming project_b and the CWD in
    project_a, the resolver returns project_a's CLAUDE.md.

    Step 3's heading cross-check cannot see this. `check_pin_caps.py` uses the
    SAME resolver, so the listing step and the archival step agree on the same
    wrong file and every heading matches. That check catches an index shift
    WITHIN a file; nothing catches a wrong FILE.

    THE REFUSAL IS NARROW BY CONSTRUCTION. It fires only when
    CLAUDE_PROJECT_DIR is explicitly set, names a directory with no CLAUDE.md,
    AND the resolver landed outside that directory's own repository. The
    worktree fall-through -- the case PACT itself depends on -- stays inside
    the same repository and is allowed through.
    """
    # Resolve through `get_project_claude_md_path` -- the seam the rest of the
    # suite already patches -- and derive the base LEXICALLY from the result,
    # rather than taking the with-base form directly. Same answer in
    # production (the with-base call is what this wraps), but it keeps one
    # resolution seam for the whole codebase instead of introducing a second
    # that fixtures would have to know about separately.
    env_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    path = get_project_claude_md_path()
    if path is None:
        raise _Unevaluable("CLAUDE.md not found")
    base = _lexical_base_of(path)

    if env_dir:
        env_path = Path(env_dir)
        if (_find_existing_claude_md(env_path) is None
                and not _stays_in_declared_project(
                    env_path, base, path,
                    worktree_identity=_get_worktree_identity_from_session_record(),
                )):
            raise _Unevaluable(
                f"CLAUDE_PROJECT_DIR={env_dir} contains no CLAUDE.md, and "
                f"resolution fell through to {path} in a different project. "
                f"Refusing rather than archiving a pin the invocation never "
                f"named. Set CLAUDE_PROJECT_DIR to the project that owns the "
                f"pin, or unset it to resolve from the working directory."
            )
    return path, base


def project_dir_for(claude_md: Path) -> Path:
    """Return the project directory that owns `claude_md`.

    CLAUDE.md lives at either `<project>/.claude/CLAUDE.md` (preferred) or
    `<project>/CLAUDE.md` (legacy), so the project root is one or two levels
    up depending on which form resolved.

    This is the INVERSE of `shared.claude_md_manager.resolve_project_claude_md_path`
    (project dir -> CLAUDE.md); no existing helper goes this direction, which
    is why it is defined here rather than reused. The two-layout knowledge is
    owned by that module's `_DOT_CLAUDE_RELATIVE` / `_LEGACY_RELATIVE`; if a
    THIRD location is ever supported, this function must be swept too. It is
    deliberately NOT a sixth CLAUDE.md resolver -- it never probes the
    filesystem for CLAUDE.md, it only maps a path already resolved by one of
    the five, so `test_claude_md_resolver_parity.py` does not cover it.

    This is load-bearing for WHERE the archived pin is filed. The memory
    layer detects `project_id` from CLAUDE_PROJECT_DIR, else from git, else
    by walking UP from the CWD to the nearest project marker -- and `.claude`
    IS such a marker. So running the memory CLI from the plugin's own
    directory files a consumer's archived pin under project `.claude`
    (measured), because the installed plugin lives beneath `~/.claude/`.
    Deriving the CWD from the CLAUDE.md we actually read keeps the pin and
    its archive agreeing on the project by construction.
    """
    parent = claude_md.resolve().parent
    return parent.parent if parent.name == ".claude" else parent


def _run_memory_cli(args, db_path=None, stdin_data=None, cwd=None):
    """Invoke the memory CLI and return (returncode, stdout, stderr).

    STREAMS ARE KEPT SEPARATE AND MUST STAY THAT WAY. `save` writes an
    embedding progress bar (`Fetching 10 files: ...`) to STDERR on SUCCESS
    -- measured at ~130 bytes -- so merging the streams would splice that
    bar into the JSON envelope and corrupt every parse. Never `2>&1` here.

    Errors also go to stderr (cli.py's `_error`), which is why a failed call
    presents as EMPTY STDOUT: NOT_FOUND, PREFIX_TOO_SHORT and an outright
    crash are indistinguishable on stdout alone. The caller must classify
    from the stderr envelope, whose key is `error` (NOT `error_type`).

    Raises:
        _Unevaluable: if the CLI is missing, hangs, or cannot be launched.
    """
    if not _MEMORY_CLI.exists():
        raise _Unevaluable(f"memory CLI not found at {_MEMORY_CLI}")

    # FALSY-BUT-PRESENT, rejected under pytest. The line below tests db_path
    # for TRUTHINESS, so `db_path=""` takes the same branch as an omitted one
    # and routes to the live store -- a required-parameter fix defeated without
    # removing the parameter. A caller that names db_path and passes an empty
    # value has stated an intention the truthiness test then discards.
    #
    # The predicate is `is not None and not db_path`, NOT plain falsiness.
    # None is the "I am not scoping this call" sentinel and is legitimate on
    # the paths that never spawn -- six tests reach here with db_path=None
    # while stubbing subprocess.run or _MEMORY_CLI, and none of them can touch
    # a store. Rejecting plain falsiness would redden all six for a hazard
    # they do not have. A real spawn carrying None is caught at the process
    # boundary instead, in the child, where the decision actually lands.
    # THE IN-PROCESS HALF IS `sys.modules`, AND IT IS THE HALF A CLEARED
    # ENVIRONMENT CANNOT REMOVE. `PYTEST_CURRENT_TEST` is caller-controlled:
    # `env -i` strips it, and so does a selective unset, so an environment-only
    # predicate goes blind for the careful caller who isolates a probe. This
    # function runs IN THE PARENT, which for a test IS the pytest interpreter,
    # so `pytest` is in `sys.modules` and no environment edit can hide it.
    #
    # THE TWO HALVES COVER DIFFERENT POPULATIONS AND NEITHER IS REDUNDANT.
    # `sys.modules` covers an in-process caller and cannot see a fresh
    # interpreter. The environment variable covers a spawned child that
    # inherited it and is the only signal that crosses that boundary.
    #
    # RESIDUAL, STATED RATHER THAN IMPLIED: a spawned child that runs with the
    # environment cleared is covered by NEITHER half here. That case is the
    # child-side guard's to catch, and the child-side guard keys on the same
    # environment variable, so the two layers share one predicate there.
    if (
        "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST")
    ) and db_path is not None and not db_path:
        raise _Unevaluable(
            "db_path was given as an empty value under pytest; it is falsy, "
            "so the memory CLI would fall back to the LIVE database. "
            "Pass a real temp path, or None if this call cannot reach a store."
        )

    argv = [sys.executable, str(_MEMORY_CLI), *args]
    if db_path:
        argv += ["--db-path", db_path]

    # AUTOMATIC ANCHOR SUPPLY, for the one verb that can sync.
    #
    # `cwd` is the caller's statement of which project owns this invocation --
    # the same value that pins CLAUDE_PROJECT_DIR below -- so it is also the
    # boundary a CLAUDE.md write must stay inside. Supplying it here is what
    # makes the anchor DECLARED rather than derived: the child does not compute
    # it, it is told.
    #
    # THIS IS A SUPPLY, NOT A CAPABILITY. `--claude-md-root` is a CLI flag that
    # any subprocess route can pass for itself. This wrapper adds it
    # automatically only because it alone knows the declared directory; what is
    # limited to this route is the automation, never the availability.
    if cwd and args and args[0] == "save":
        argv += ["--claude-md-root", str(cwd)]

    # Pin the project for the child process. CLAUDE_PROJECT_DIR is the memory
    # layer's PRIMARY detection strategy and is deterministic, unlike the git
    # and CWD-walk fallbacks.
    #
    # An explicit `cwd` is the CALLER'S statement of which project owns this
    # invocation, so it OVERWRITES any ambient value rather than deferring to
    # it. This was `setdefault`, which is a no-op when the variable is already
    # set -- so the ambient value won and the more specific answer lost to the
    # more general one. The fail direction was inverted.
    #
    # That is a PRODUCTION defect, not a test concern. `project_dir_for` exists
    # so the archive's project derives from the CLAUDE.md actually read, and
    # `resolve_claude_md` deliberately PERMITS a worktree fall-through: the env
    # dir is a worktree carrying no CLAUDE.md, so resolution lands on the main
    # repo's file. Under `setdefault` that filed the archive under the WORKTREE
    # while the pin lived in the MAIN repo -- exactly the pin/archive
    # disagreement `project_dir_for` is there to prevent.
    #
    # THE ENV IS NOW ALWAYS BUILT, and the `cwd is None` case is the reason.
    # It used to leave `env` as None, and `subprocess.run(env=None)` hands the
    # child the parent environment VERBATIM -- so the child resolved a
    # CLAUDE.md from whatever ambient CLAUDE_PROJECT_DIR, git anchor or working
    # directory happened to be in scope. Measured: with the variable unset and
    # `cwd` omitted, the child wrote to the invoking repository's real
    # CLAUDE.md. Every configuration of that branch reached outside the
    # intended target; only the destination varied.
    #
    # NO TARGET IS NOT A LICENCE TO GUESS ONE. A caller that omits `cwd` has
    # stated no project, so the ambient value is not a weaker answer to the
    # same question -- it is an answer to a different one. The variable is
    # therefore REMOVED rather than inherited, and the projection that would
    # have consumed it is SUPPRESSED. An absent target is a SKIP, never a
    # CREATE: nothing here invents a path, and nothing writes a CLAUDE.md that
    # did not already exist.
    #
    # `--no-sync` is appended only for subcommands that accept it. It is
    # declared on the `save` subparser alone, so adding it to a `get` would be
    # an argparse error -- an over-block manufactured by the fix. The set is a
    # named constant pinned against the CLI's real parser by a test; see
    # `_SYNC_CAPABLE_SUBCOMMANDS` for what that test does and does not catch.
    env = dict(os.environ)
    if cwd is not None:
        env["CLAUDE_PROJECT_DIR"] = str(cwd)
    else:
        env.pop("CLAUDE_PROJECT_DIR", None)
        if args and args[0] in _SYNC_CAPABLE_SUBCOMMANDS and "--no-sync" not in args:
            argv.append("--no-sync")

    try:
        proc = subprocess.run(
            argv,
            input=stdin_data,
            capture_output=True,   # separate pipes -- NOT merged
            text=True,
            timeout=_CLI_TIMEOUT_SECONDS,
            # CWD is the PROJECT that owns the pin, never the plugin's own
            # directory -- see project_dir_for(). #935 residual: an unpinned
            # CWD silently files the archive under the wrong project.
            cwd=str(cwd) if cwd is not None else None,
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise _Unevaluable(
            f"memory CLI timed out after {_CLI_TIMEOUT_SECONDS}s"
        )
    except OSError as exc:
        raise _Unevaluable(f"could not launch memory CLI: {type(exc).__name__}")

    return proc.returncode, proc.stdout, proc.stderr


def _parse_envelope(stdout: str):
    """Parse a success envelope from stdout, or return None.

    Returns the `result` payload on a well-formed `{"ok": true, ...}`
    envelope. Returns None for empty stdout, malformed JSON, or ok=false --
    all of which the caller must treat as "the call did not succeed",
    classifying the reason from stderr rather than from this absence.
    """
    if not stdout.strip():
        return None
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(envelope, dict) or not envelope.get("ok"):
        return None
    return envelope.get("result")


def _classify_cli_error(stderr: str) -> str:
    """Render a short reason from the memory CLI's stderr error envelope.

    The envelope key is `error` (NOT `error_type`). Falls back to a trimmed
    raw stderr when the payload is not a parseable envelope -- a crash
    traceback still tells the curator more than a bare "failed" would.
    """
    text = stderr.strip()
    if not text:
        return "memory CLI failed with no diagnostic output"
    try:
        payload = json.loads(text)
        if isinstance(payload, dict) and payload.get("error"):
            message = payload.get("message", "")
            return f"{payload['error']}: {message}".strip().rstrip(":")
    except json.JSONDecodeError:
        pass
    return text.splitlines()[-1][:300]


def _unsafe_reason(occurrences: int, claude_md_path: str, memory_id: str) -> str:
    """Explain WHY the removal is unsafe -- the two causes are different.

    `occurrences != 1` catches both directions, but they are not the same
    condition and a curator acts differently on each:

      > 1   the block appears more than once, so an Edit keyed on it cannot be
            targeted. DUPLICATION.
      0     the block is not there at all. It was present when this run read
            the file and is gone from the post-save re-read, which means
            something modified CLAUDE.md concurrently -- a live hazard on a
            file the curator may have open in an editor. ABSENCE, not ambiguity.

    Telling a curator "ambiguous" when the block has VANISHED sends them
    hunting for a second copy that does not exist. The disposition must be
    readable from what the verdict says rather than reconstructed, and a
    reason string that misdescribes its own condition is that failure in
    miniature.
    """
    archived = (
        f"The archive SUCCEEDED (memory_id {memory_id}) -- the content is "
        f"safe. Remove the pin manually."
    )
    if occurrences == 0:
        return (
            f"the pin block is NO LONGER PRESENT in {claude_md_path}. It was "
            f"read from that file moments ago, so something modified the file "
            f"concurrently -- check for an editor or another process holding "
            f"it. This is absence, not ambiguity: there is no second copy to "
            f"find. {archived}"
        )
    return (
        f"the pin block occurs {occurrences} times in {claude_md_path}; a "
        f"removal Edit keyed on it would be ambiguous. {archived}"
    )


def _suppression_breach_reason(
    sync_status: str, claude_md_path: str, sync_scope: str, memory_id: str
) -> str:
    """Explain a save whose CLAUDE.md projection was not suppressed.

    ONE PREDICATE FOR THE DECISION, THREE SHAPES FOR THE MESSAGE. The gate is
    `!= suppressed`: the archival save passes `--no-sync` explicitly, so
    `suppressed` is the ONLY status this route ever asks for, and keying on
    the REQUESTED value rather than enumerating the unwanted ones covers a
    SEVENTH `SyncResult` reason the moment it is added, fail-safe, with no
    edit here. `suppressed` is the PRODUCTION-NORMAL value and nothing may
    fire on it -- that would be a cardinal over-block on the curator's own
    routine path.

    BUT THE GATE'S ONE FACT IS NOT THE MESSAGE'S ONE FACT, AND THE MESSAGE
    SPLITS THREE WAYS RATHER THAN TWO. The distinction is not "did a copy
    land" -- it is WAS A WRITE EVEN ATTEMPTED, because that is what decides
    whether there is anything to bound. Measured against the exits in
    `sync_to_claude_md`:

      wrote        A PROJECTION LANDED. Name where it can be.

      failed       A WRITE WAS ATTEMPTED AND MAY HAVE COMPLETED. The
                   `except` that produces this status wraps the atomic
                   rename, and a lock release and a log call run after that
                   rename inside the same `try` -- so a durable write can
                   still report `failed`. A copy MAY exist, and the bound
                   still holds, so name it. This is the status where a stray
                   copy is MOST plausible and the bound is SOUNDEST.

      refused      NO WRITE WAS EVER ATTEMPTED -- all three exit before the
      unresolved   `try` block opens. The bound is TRUE here but VACUOUS, and
      missing      a scope that is unconditionally true names nothing worth
                   searching. Omitted for that reason, NOT because it would
                   be false.

    THIS SITE GOVERNS THE SENTENCE, NOT THE KEY, and the reason is deliberately
    vacuity ALONE. The `sync_scope` KEY carries a second argument -- that its
    absence is machine-readable signal -- which does NOT apply here: no
    consumer reads this prose and branches on whether a bound sentence appears.
    Importing that argument to a string would be stating a reason that does not
    describe what it is attached to. If you are sweeping the signal reason
    through this file, STOP at the two key sites; this asymmetry is deliberate.

    THE BOUND IS SOUND ON `failed`, WHICH IS THE ONLY WAY THIS COULD MISLEAD.
    The save leg passes `--claude-md-root`, and a write OUTSIDE the anchor is
    refused by `_atomic_write_text` -- which raises, and so yields `failed`
    with no write at all. So on `failed` there are exactly two possibilities:
    no write, or a write INSIDE `sync_scope`. Never outside. The scope can
    therefore never point a curator at the wrong directory.

    NO ARM CLAIMS A COPY EXISTS, AND NONE CLAIMS ONE DOES NOT. "No copy
    exists" would be as false on `failed` as "a copy exists" -- both assert
    knowledge this path does not have.

    WHY THIS DOES NOT DUPLICATE THE OCCURRENCE CHECK. A projection into the
    SAME file is already caught downstream by `occurrences != 1`, and that
    check is BETTER evidence because it measures the artifact rather than a
    reported status. Two things are left over:

      SAME FILE   the occurrence check fires, but `_unsafe_reason` then tells
                  the curator to "check for an editor or another process" --
                  when THIS RUN wrote it. Right outcome, wrong cause.

      OTHER FILE  the occurrence check reads ONLY the target, so a projection
                  into a DIFFERENT CLAUDE.md leaves the target at exactly one
                  occurrence and the archive reports CLEAN. Nothing else on
                  this path can see it.

    A bounded scope is not a located file. The `wrote` arm says WHERE a stray
    copy must be if one exists; it does not claim one does.
    """
    archived = (
        f"The archive SUCCEEDED (memory_id {memory_id}) -- the content is "
        f"safe. Remove the pin manually."
    )
    common = (
        f"`occurrences` and `locations` below describe {claude_md_path} ONLY. "
    )
    # THE BOUND, WORDED IDENTICALLY FOR BOTH ARMS THAT CARRY IT. It is
    # EXISTENCE-INDEPENDENT: it says where a copy must be IF one exists, and
    # asserts nothing about whether one does. That is what lets `failed` carry
    # it without claiming a write landed.
    bound = (
        f"They do NOT measure where a projection would have landed: "
        f"resolution is ambient, so IF a copy of the pin exists it is in some "
        f"CLAUDE.md under {sync_scope} -- the declared anchor bounds it there "
        f"and no further. Check that directory before removing anything. "
    )
    if sync_status == "wrote":
        return (
            f"the archival save WROTE a CLAUDE.md working-memory projection, "
            f"which `--no-sync` exists to prevent. THIS RUN made that write; "
            f"it is not a concurrent editor. {common}{bound}{archived}"
        )
    if sync_status == "failed":
        return (
            f"the archival save ATTEMPTED a CLAUDE.md projection that "
            f"`--no-sync` should have suppressed, and the attempt FAILED. "
            f"The failure can be raised AFTER the write completed, so this "
            f"verdict cannot tell whether a projection landed -- it claims "
            f"neither that a copy exists nor that none does. {common}{bound}"
            f"{archived}"
        )
    # refused / unresolved / missing. NO `bound` HERE, AND THE REASON IS
    # VACUITY RATHER THAN FALSEHOOD: these three exit before the write is
    # attempted, so there is no projection from this save to bound and a scope
    # would name nothing worth searching.
    #
    # VACUITY IS THE WHOLE REASON AT THIS SITE, unlike the `sync_scope` KEY,
    # which also rests on its absence being machine-readable signal. That
    # second argument is about a consumer branching on a key and has no
    # purchase on a string, so it is deliberately NOT repeated here. The
    # asymmetry between this comment and the one at
    # `_WRITE_ATTEMPTED_STATUSES` is intended, not an unfinished sweep.
    #
    # This arm says NO PROJECTION WAS ATTEMPTED, which is stronger than the
    # `failed` arm's "cannot tell" and is warranted here -- it is a fact about
    # THIS SAVE, established by which exit ran. It deliberately says nothing
    # about the file's contents generally, which this path never measured.
    return (
        f"the archival save reported '{sync_status}', so the suppression "
        f"`--no-sync` requested did not take effect and this run and the "
        f"memory CLI disagree about whether this save projects. NO PROJECTION "
        f"WAS ATTEMPTED on this status, so this save left no copy of the pin "
        f"anywhere -- there is no scope to search. {common}{archived}"
    )


def _occurrence_offsets(text: str, needle: str) -> list:
    """Character offsets of every occurrence of `needle`, for the unsafe verdict.

    Offsets are DIAGNOSTIC only -- they tell a curator where the copies are.
    They are deliberately NOT the delete handle: a positional handle computed
    now and consumed later is silently wrong if anything touches the file in
    between, whereas the content handle makes a stale Edit fail loudly.
    """
    offsets, start = [], 0
    while True:
        at = text.find(needle, start)
        if at == -1:
            return offsets
        offsets.append(at)
        start = at + 1


def _build_record(block: str, heading: str) -> dict:
    """Build the pact-memory record for an archived pin.

    THE PIN BODY GOES IN `context`, AND NEVER IN A LIST FIELD. This is a
    prohibition, not a preference. String-list fields (lessons_learned,
    reasoning_chains, ...) `.strip()` and NFC-normalize their items, so a
    pin stored there comes back ALTERED and the containment check returns
    FALSE for a perfectly good archive -- an OVER-BLOCK that refuses a
    legitimate eviction, which is the cardinal error direction here.
    Scalars escape that normalization and round-trip byte-exact.

    This is not hypothetical: the one pin-archive record already in the
    store asserts "Content preserved verbatim" while holding its content in
    `lessons_learned`, a field that cannot deliver that guarantee.

    `entities` carries the curation tags; `goal` carries the orienting
    label. Curation and verbatim occupy DIFFERENT columns, so a queryable
    archive and byte-exact preservation coexist with no trade-off.

    STANDALONE ONLY. This record is always CREATED, never merged into an
    existing one. The invariant is load-bearing rather than incidental: an
    additive `update` creates no new record and runs no marker code, so a
    folded archive is invisible to a marker scan -- which has already happened
    to prior demotions. A future maintainer adding a fold path for a good
    reason (dedup, retrieval quality) would void the audit property WITHOUT
    TOUCHING THIS FUNCTION, and nothing would fail at runtime; the symptom is a
    missing record in somebody's later audit. If a fold path is ever genuinely
    wanted, write the marker on the update path too, or withdraw the
    auditability claim in the same change.
    """
    archived_on = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {
        "context": block,
        "goal": f"Archived pin: {heading}",
        "entities": [{
            "name": heading,
            "type": ARCHIVE_ENTITY_TYPE,
            "notes": (
                f"demoted from CLAUDE.md Pinned Context on {archived_on}"
            ),
        }],
    }


def archive_pin(index: int, db_path=None) -> dict:
    """Archive the pin at `index` and return a verdict dict.

    Sequence: resolve CLAUDE.md -> parse pins -> slice the block verbatim ->
    save -> re-fetch by the returned memory_id -> assert containment.

    Raises:
        _Unevaluable: whenever the pin's state cannot be established.
    """
    # Refuses a cross-project fall-through rather than silently operating on
    # a file the invocation never named. `resolved_base` is the resolver's own
    # trusted anchor, used for the archive's project attribution instead of
    # re-deriving it from the leaf path (which followed symlinks).
    claude_md, resolved_base = resolve_claude_md()
    # Every verdict from here on names the file actually read, so a wrong-file
    # resolution is visible rather than silent.
    claude_md_path = str(claude_md)

    try:
        content = claude_md.read_text(encoding="utf-8")
    except (IOError, OSError, UnicodeDecodeError) as exc:
        raise _Unevaluable(f"CLAUDE.md unreadable ({type(exc).__name__})",
                           claude_md_path=claude_md_path)

    parsed = _parse_pinned_section(content)
    if parsed is None:
        raise _Unevaluable("no Pinned Context section",
                           claude_md_path=claude_md_path)

    _, _, pinned_content = parsed
    try:
        pins = parse_pins(pinned_content)
    except Exception as exc:  # noqa: BLE001 -- parse fault is unevaluable
        raise _Unevaluable(f"pin parse failed ({type(exc).__name__})",
                           claude_md_path=claude_md_path)

    if index < 0 or index >= len(pins):
        raise _Unevaluable(
            f"pin index {index} out of range ({len(pins)} pins parsed)",
            claude_md_path=claude_md_path,
        )

    pin = pins[index]
    # The pin's ACTUAL heading, read from the parse. Never echoed back from
    # the requested index: the caller cross-checks this value against the
    # curator's selection to catch an index shift between listing and
    # archival, and an echo would make that check compare the index against
    # itself and pass unconditionally.
    heading = pin.heading[4:] if pin.heading.startswith("### ") else pin.heading

    block = extract_pin_block(pinned_content, index, pins)
    if not block.strip():
        raise _Unevaluable("pin block is empty", heading=heading,
                           claude_md_path=claude_md_path)

    # Source-side tripwire. The slice makes this true BY CONSTRUCTION, so it
    # costs nothing in the normal case -- it exists to fail loudly if the
    # extractor ever regresses into a lossy rebuild. Checked BEFORE the save,
    # so a broken extractor writes nothing.
    if block not in content:
        raise _Unevaluable(
            "extracted block is not verbatim in CLAUDE.md (extractor bug)",
            heading=heading, claude_md_path=claude_md_path,
        )

    def _cli(args, *, _heading=heading, _block=block, _md=claude_md_path,
             **kwargs):
        """Invoke the memory CLI, enriching any UNEVALUABLE with pin context.

        `_run_memory_cli` raises bare -- correctly, since it is a generic
        helper with no idea which pin is being archived. But EVERY failure it
        can raise happens AFTER the pin was resolved: CLAUDE.md read, pins
        parsed, block already sliced. So the verdict can and must still name
        the file, the heading and the delete handle.

        Without this the LATER a failure occurs the LESS context survives,
        which is backwards and is not something anyone would choose: a
        pre-resolution bad index reported `claude_md_path` while a
        post-resolution CLI timeout reported nothing at all.

        It bit hardest exactly where it mattered most. A CLI timeout and a
        missing CLI are the canonical CANNOT-TELL cases -- they are when the
        escape hatch runs -- so the hatch would have had no mechanical
        delete boundary in its own primary use case.

        THE THREE DEFAULT ARGUMENTS ARE THE ENFORCEMENT, NOT DECORATION.
        `_heading`, `_block` and `_md` capture their enclosing values at `def`
        TIME instead of closing over the names. The ordering dependency between
        this one definition site and its three binding sites is then checked by
        the interpreter on every run: moving this `def` above any of them
        raises NameError AT THE `def`. A closure defers that failure into the
        `except` path below -- which runs only once something else has ALREADY
        failed -- so the diagnostic would collapse to `internal error:
        NameError` at precisely the moment the curator needs the delete
        boundary. Keyword-only, and underscore-prefixed, so no caller supplies
        them by accident and `**kwargs` still forwards the real arguments
        untouched.

        The handler reads the PARAMETERS, never the enclosing names. That is
        load-bearing rather than stylistic: defaults nothing reads would still
        satisfy the `def` while leaving the handler closing over the originals,
        which restores the silent failure this shape exists to prevent.
        """
        try:
            return _run_memory_cli(args, **kwargs)
        except _Unevaluable as exc:
            raise _Unevaluable(
                exc.reason,
                heading=_heading,
                claude_md_path=_md,
                delete_string=_block,
            ) from exc

    # --- conjunct 1: save returned a memory_id -----------------------------
    # Passed on STDIN rather than argv: the record embeds arbitrary curator
    # text, and an argv-borne payload would be bounded by ARG_MAX and would
    # surface the pin body in the process table.
    project_dir = resolved_base
    payload = json.dumps(_build_record(block, heading))
    # STANDALONE: always `save` (create), never `update` (fold). See
    # _build_record -- a fold runs no marker code and is invisible to an
    # archive scan.
    # --no-sync: the Working Memory projection would write the record's
    # `context` -- the block, verbatim -- back into the same CLAUDE.md this
    # archive exists to remove it from. The pin SLOT would be freed while the
    # file was not, and the duplicate would make the curator's removal Edit
    # ambiguous. Measured: without it the block occurs twice and the file
    # grows; with it CLAUDE.md is byte-identical across the save.
    _, stdout, stderr = _cli(
        [_ARCHIVE_SUBCOMMAND, "--stdin", "--no-sync"], db_path=db_path,
        stdin_data=payload, cwd=project_dir,
    )
    result = _parse_envelope(stdout)
    memory_id = result.get("memory_id") if isinstance(result, dict) else None
    if not memory_id:
        # A definite failure of the save itself: the store was reachable and
        # said no. NOT_ARCHIVED, not UNEVALUABLE.
        #
        # ONE STATED EXCEPTION TO THAT CRITERION. The child-side guard in
        # cli.py refuses BEFORE opening any store, so "reachable and said no"
        # does not describe it -- yet it lands here, deliberately. UNEVALUABLE
        # buys the escape hatch (print the pin, permit manual removal), which
        # exists so a curator with a broken CLI is not TRAPPED; this refusal
        # has a one-line fix, so offering hand-deletion would be more
        # destructive than the refusal it replaced. The routing follows the
        # DISPOSITION, not the label.
        #
        # ⚠️ So this criterion is narrower than it reads. Any future logic that
        # keys on it PROGRAMMATICALLY -- "NOT_ARCHIVED implies the store
        # answered" -- is wrong for this one cause. Read the reason string.
        return {
            "outcome": "NOT_ARCHIVED",
            "heading": heading,
            "claude_md_path": claude_md_path,
            "delete_string": block,
            "memory_id": None,
            "reason": f"save returned no memory_id -- {_classify_cli_error(stderr)}",
        }

    # --- conjunct 2: the record is retrievable by that id ------------------
    _, stdout, stderr = _cli(
        ["get", memory_id], db_path=db_path, cwd=project_dir
    )
    fetched = _parse_envelope(stdout)
    if not isinstance(fetched, dict):
        # The id came back but the record will not re-read. We cannot tell
        # whether the bytes are safe, so we must not claim they are -- and we
        # must not claim they are lost either.
        raise _Unevaluable(
            f"saved as {memory_id} but re-fetch failed -- "
            f"{_classify_cli_error(stderr)}",
            heading=heading, claude_md_path=claude_md_path,
            delete_string=block,
        )

    # --- conjunct 3: the pin block is present AT THE DESTINATION -----------
    # SUBSTRING, never equality. `context` may legitimately carry more than
    # the block (a provenance preamble, say), and an equality assertion would
    # return false for a correct archive -- an over-block in the one control
    # whose entire purpose is not destroying content.
    #
    # This is the conjunct the old criterion lacked. A returned memory_id
    # proves a record EXISTS (persistence); only this proves the record
    # carries the PIN (fidelity). The founding data loss satisfied the former
    # at the moment it occurred.
    context = fetched.get("context")
    if not isinstance(context, str) or block not in context:
        return {
            "outcome": "NOT_ARCHIVED",
            "heading": heading,
            "claude_md_path": claude_md_path,
            "delete_string": block,
            "memory_id": memory_id,
            "reason": (
                "saved record does not contain the pin block verbatim "
                "(fidelity check failed)"
            ),
        }

    # --- delete_string uniqueness, checked AFTER the save --------------
    # AFTER, because that is the state the curator's destructive Edit will
    # actually meet -- checking before would validate a precondition on a file
    # something may since have written to, which is the assumption under test.
    # The before == after assertion turns that judgement call into a
    # measurement: with the sync suppressed the two must agree, and a
    # divergence surfaces loudly instead of being absorbed by whichever side
    # was picked.
    try:
        post = claude_md.read_text(encoding="utf-8")
    except (IOError, OSError, UnicodeDecodeError) as exc:
        raise _Unevaluable(
            f"CLAUDE.md unreadable after save ({type(exc).__name__})",
            heading=heading, claude_md_path=claude_md_path,
            delete_string=block,
        )

    occurrences = post.count(block)

    # --- the projection this save was told NOT to make --------------------
    #
    # READS `sync_status` OFF THE SAVE ENVELOPE ALREADY IN HAND. The field
    # crosses the process boundary on the `save` route and, before this,
    # nothing read it -- a channel with no consumer.
    #
    # PRESENT-VALUE-ONLY, AND ABSENCE IS NOT SUCCESS. An absent field is NO
    # EVIDENCE, so nothing fires on it. That is not the same as reading absence
    # as a clean sync, which `cli.py` explicitly forbids: the check below acts
    # on a value that IS there and disagrees with what this call requested.
    # Absence is safe to pass here for a reason particular to this call site --
    # `_MEMORY_CLI` is a sibling path of this file, so parent and child are the
    # same tree and cannot disagree about the envelope's shape. A real-CLI test
    # pins that premise rather than leaving it assumed.
    #
    # CHECKED BEFORE `occurrences != 1`, DELIBERATELY. When both conditions
    # hold, the breached suppression is the CAUSE and the duplicate block is
    # the SYMPTOM, so the curator is told the cause. Nothing is lost by the
    # ordering: this verdict carries `occurrences` and `locations` too.
    #
    # THIS IS A REGRESSION GUARD, NOT A FIX FOR A LIVE DEFECT. While
    # `--no-sync` works the status is `suppressed` and this never fires. It
    # converts "we pass the flag and assume it worked" into "we verify it
    # worked" -- on a path whose own comment records that the suppression is
    # load-bearing and was measured.
    # AN ABSENT `sync_status` LEAVES THIS GUARD INERT, AND NOBODY IS TOLD.
    # RECORDED RATHER THAN ANSWERED, because neither available answer is
    # defensible: failing closed would refuse every archive against a CLI that
    # predates the field, which is an over-block on the curator's own path;
    # and treating absence as a clean sync is the inference `cli.py` forbids.
    # So absence is NO EVIDENCE and nothing fires -- with the limitation
    # written here rather than left for a reader to discover. What makes that
    # tolerable at THIS call site, and nowhere else, is that `_MEMORY_CLI` is
    # a sibling path of this file: parent and child are the same tree and
    # cannot disagree about the envelope's shape. A real-CLI test pins that.
    sync_status = result.get("sync_status") if isinstance(result, dict) else None
    if sync_status is not None and sync_status != "suppressed":
        # THE DECISION IS ONE PREDICATE; THE PAYLOAD IS TWO SHAPES, SPLIT ON
        # WHETHER A WRITE WAS ATTEMPTED -- not on whether one landed.
        #
        # `sync_scope` IS EXISTENCE-INDEPENDENT. It says where a copy MUST BE
        # IF ONE EXISTS; it never claims one does. So the question it answers
        # is not "did a projection land" but "is there a projection from this
        # save to bound at all", and that is decided by which exit ran:
        #
        #   wrote, failed              a write was ATTEMPTED. `failed` is
        #                              produced by an `except` wrapping the
        #                              atomic rename, with a lock release and
        #                              a log call after it inside the same
        #                              `try`, so a completed write can still
        #                              report `failed`. Both carry the scope.
        #
        #   refused, unresolved,       all exit BEFORE the write is attempted.
        #   missing                    The bound is true but VACUOUS, so it is
        #                              omitted -- for vacuity, never falsehood.
        #                              AND NOT ONLY FOR VACUITY: absence of the
        #                              key is ITSELF the signal that no write
        #                              was attempted, so adding it "for schema
        #                              uniformity" would destroy a distinction
        #                              rather than merely add noise. Full
        #                              argument at `_WRITE_ATTEMPTED_STATUSES`.
        #
        # This still satisfies the module's invariant at the top of the file,
        # EACH FIELD IS PRESENT IFF THE FACT IT NAMES WAS ACTUALLY ESTABLISHED,
        # because the fact `sync_scope` names is THE BOUND ON ANY PROJECTION
        # FROM THIS SAVE -- established the moment a write is attempted under a
        # checked anchor, not only once one is known to have landed.
        #
        # DO NOT NARROW THIS BACK TO `wrote` ALONE. That reading was tried and
        # retired: it drops the scope from `failed`, which is precisely the
        # status where a stray copy is most plausible and the bound is
        # soundest, and it contradicts this function's own docstring.
        verdict = {
            "outcome": "ARCHIVED_DELETE_UNSAFE",
            "heading": heading,
            "claude_md_path": claude_md_path,
            "delete_string": block,
            "memory_id": memory_id,
            "chars": len(block),
            "contained": True,
            "occurrences": occurrences,
            "locations": _occurrence_offsets(post, block),
            "sync_status": sync_status,
            "reason": _suppression_breach_reason(
                sync_status, claude_md_path, str(project_dir), memory_id
            ),
        }
        if sync_status in _WRITE_ATTEMPTED_STATUSES:
            verdict["sync_scope"] = str(project_dir)
        return verdict

    if occurrences != 1:
        # ARCHIVE SUCCEEDED, REMOVAL IS UNSAFE -- a distinct outcome, not a
        # variant of another. Not UNEVALUABLE: that means "cannot tell", and
        # this is a KNOWN-BAD precondition for the delete. Not NOT_ARCHIVED:
        # that asserts the archive failed, which is false here and would put a
        # falsehood in the one report whose job is truthful measurement.
        # No escape hatch -- the curator removes the pin manually WITH the
        # content already safely archived, so this redirects rather than traps
        # them at the cap. The outcome NAME determines the disposition; a
        # reason string would not, and a reason table is how a permission gets
        # inherited by a condition it was never designed for.
        return {
            "outcome": "ARCHIVED_DELETE_UNSAFE",
            "heading": heading,
            "claude_md_path": claude_md_path,
            "delete_string": block,
            "memory_id": memory_id,
            "chars": len(block),
            "contained": True,
            "occurrences": occurrences,
            "locations": _occurrence_offsets(post, block),
            "reason": _unsafe_reason(occurrences, claude_md_path, memory_id),
        }

    return {
        "outcome": "ARCHIVED",
        "heading": heading,
        "claude_md_path": claude_md_path,
        "delete_string": block,
        "memory_id": memory_id,
        "chars": len(block),
        "contained": True,
        "occurrences": 1,
    }


def build_verdict(index: int, *, db_path) -> dict:
    """Run the archival and return a verdict dict — never raises.

    The single place where an unevaluable state becomes an UNEVALUABLE
    verdict, so `main` only has to serialize. Keeping the mapping here
    (rather than inline in `main`) is what lets tests exercise the
    degradation paths without going through argv and stdout.

    `db_path` IS REQUIRED AND KEYWORD-ONLY. It used to default to None,
    and None means the real store -- so the seam that exists to let tests
    reach the degradation paths was also the seam that skipped the db-path
    guard. The decision that made testing easy removed the isolation, and
    it was silent: a caller that simply said nothing got the live store.

    Required makes every caller state an answer; keyword-only makes them
    state it BY NAME, so the answer is legible at the call site rather than
    being a bare second positional. What it does NOT do is make the answer
    correct -- `db_path=None` still satisfies the signature and still means
    the real store. The mechanical protection is the guard in
    `_run_memory_cli`; this parameter is what makes the choice visible.
    """
    try:
        return archive_pin(index, db_path=db_path)
    except _Unevaluable as exc:
        return {
            "outcome": "UNEVALUABLE",
            "heading": exc.heading,
            "claude_md_path": exc.claude_md_path,
            "delete_string": exc.delete_string,
            "reason": exc.reason,
        }
    except Exception as exc:  # noqa: BLE001 -- never crash the curator's flow
        return {
            "outcome": "UNEVALUABLE",
            "heading": None,
            "claude_md_path": None,
            "delete_string": None,
            "reason": f"internal error: {type(exc).__name__}",
        }


def main(argv=None) -> int:
    """Entry point. ALWAYS returns 0; the verdict is carried in-band."""
    parser = argparse.ArgumentParser(
        prog="archive_pin",
        description=(
            "Archive one CLAUDE.md pin to pact-memory and report, "
            "mechanically, whether its bytes arrived."
        ),
    )
    parser.add_argument(
        "--index",
        type=int,
        required=True,
        help="Position of the pin in check_pin_caps --status evictable_pins",
    )
    parser.add_argument("--db-path", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    verdict = build_verdict(args.index, db_path=args.db_path)
    sys.stdout.write(json.dumps(verdict) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
