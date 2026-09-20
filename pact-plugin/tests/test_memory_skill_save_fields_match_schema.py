"""Pin the pact-memory SKILL.md save-field table against the CLI's real allowlist.

skills/pact-memory/SKILL.md carries a "Memory Structure" table whose **On save**
column tells an agent which keys a ``save``/``update`` JSON payload accepts. That
column is a claim about ``scripts.database.CALLER_FACING_CREATE_FIELDS``, and
nothing compared the two: the table documented a ``files`` payload key that the
allowlist has never contained, so an agent following the documentation lost its
save to an exit-2 rejection and had to work out why.

The divergence is the defect these tests exist to prevent, in BOTH directions:

  1. A field documented as settable that the allowlist rejects (the original
     defect) -- an agent sends it and the save fails.
  2. A field the allowlist accepts that the table omits -- an agent never learns
     the field exists.

``files`` is pinned separately because it is not a documentation slip but a real
asymmetry: it IS a memory-record field (stored in a link table, re-attached on
read) and is NOT a payload key. A future edit that "fixes" the table by adding
``files`` back to the settable set, or that "fixes" the allowlist by admitting
``files``, would re-open the defect from either side -- the second would also
break, because there is no ``files`` column on the ``memories`` table.
"""
import re
from pathlib import Path

import pytest

from scripts.database import CALLER_FACING_CREATE_FIELDS

_SKILL_MD = (
    Path(__file__).resolve().parents[1]
    / "skills" / "pact-memory" / "SKILL.md"
)

# | `field` | type | on-save | description |
_ROW_RE = re.compile(r"^\|\s*`([a-z_]+)`\s*\|[^|]*\|\s*([^|]+?)\s*\|")

# The On-save values that mean "you may put this key in a save payload".
_SETTABLE = {"you supply", "optional"}
_NOT_SETTABLE = {"**never**"}


def _parse_rows():
    """Return {field_name: on_save_cell} from the Memory Structure table."""
    lines = _SKILL_MD.read_text().splitlines()
    start = next(
        i for i, line in enumerate(lines)
        if line.startswith("## Memory Structure")
    )
    rows = {}
    for line in lines[start:]:
        # Stop at the next top-level section so a later table cannot leak in.
        if line.startswith("## ") and not line.startswith("## Memory Structure"):
            break
        match = _ROW_RE.match(line)
        if match:
            rows[match.group(1)] = match.group(2).strip()
    return rows


def test_table_parses_non_vacuously():
    """Guard the parser itself.

    Every assertion below is a set comparison against this parse. If the table
    were reformatted so ``_ROW_RE`` matched nothing, those comparisons would
    reduce to ``set() == set()`` on one side and fail loudly -- but a parse that
    silently found only SOME rows would weaken them without failing. Pin the
    count and the vocabulary so a partial parse is caught here first.
    """
    rows = _parse_rows()
    assert len(rows) >= 10, f"parsed only {len(rows)} rows -- table shape changed"
    unknown = {
        field: cell for field, cell in rows.items()
        if cell not in _SETTABLE | _NOT_SETTABLE
    }
    assert not unknown, (
        f"unrecognised On-save values {unknown}. Every row must say one of "
        f"{sorted(_SETTABLE | _NOT_SETTABLE)}; a new vocabulary word needs a "
        f"deliberate decision about whether it means settable."
    )


def test_documented_settable_fields_match_the_allowlist():
    """The table's settable rows ARE the CLI's accepted payload keys."""
    rows = _parse_rows()
    documented = {f for f, cell in rows.items() if cell in _SETTABLE}
    accepted = set(CALLER_FACING_CREATE_FIELDS)

    assert documented == accepted, (
        "SKILL.md's save-field table has drifted from the CLI allowlist.\n"
        f"  documented as settable but REJECTED on save: "
        f"{sorted(documented - accepted)}\n"
        f"  accepted on save but NOT documented: "
        f"{sorted(accepted - documented)}"
    )


def test_files_is_documented_read_only_and_is_not_accepted():
    """``files`` is a record field, never a payload key -- pinned both ways."""
    rows = _parse_rows()
    assert "files" in rows, "the `files` row was removed; it is a real record field"
    assert rows["files"] in _NOT_SETTABLE, (
        f"`files` is marked {rows['files']!r}, but it is not a payload key. "
        "It is stored in a link table and re-attached on read."
    )
    assert "files" not in CALLER_FACING_CREATE_FIELDS, (
        "`files` was added to the save allowlist. There is no `files` column on "
        "the `memories` table -- it lives in `memory_files`, so admitting it "
        "here writes nothing and re-opens the documented/accepted divergence."
    )


@pytest.mark.parametrize("field", ["project_id", "session_id"])
def test_derived_unless_supplied_fields_are_marked_optional(field):
    """These are accepted in a payload AND auto-detected when omitted.

    Marking them the way purely-derived fields are marked would tell an agent it
    cannot set them, which is false -- ``MemoryAPI.save`` fills them in only
    ``if not provided``.
    """
    rows = _parse_rows()
    assert rows.get(field) == "optional", (
        f"`{field}` is marked {rows.get(field)!r}; it is accepted in a save "
        "payload and only auto-detected when absent."
    )
    assert field in CALLER_FACING_CREATE_FIELDS
