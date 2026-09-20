"""
Location: pact-plugin/tests/test_working_memory_fidelity.py
Summary: The Working Memory section is a derived view of the pact-memory
         store. These arms pin the property that makes the derived view
         safe to rely on: N quiet saves followed by ONE `sync` leave the same
         section that N full saves would have left; the store's edits
         (delete, a second project's records) are what the next `sync`
         shows; a file with no section gets one, a managed region with no
         memory markers is declined; the envelope names its project on the
         outcomes that write nothing; and the prose that tells agents how
         many records the section holds, and the templates that render the
         project, agree with what the code does.

         Every write in this file lands under `tmp_path`: every child sets
         `CLAUDE_PROJECT_DIR` to a tmp project and every argv that can write
         carries `--claude-md-root`; in-process calls pass `target=`.
Used by: pytest.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.working_memory as wm  # noqa: E402
from scripts.working_memory import (  # noqa: E402
    MAX_WORKING_MEMORIES,
    SyncResult,
    project_memories_to_claude_md,
)
from helpers import create_test_schema  # noqa: E402
from test_memory_cli import _backdate  # noqa: E402
from fixtures.hf_cache import hf_cache_env

_PLUGIN = Path(__file__).resolve().parent.parent
_CLI = _PLUGIN / "skills" / "pact-memory" / "scripts" / "cli.py"

_SCAFFOLD = (
    "# Probe\n\n"
    "## Working Memory\n"
    f"{wm.WORKING_MEMORY_COMMENT}\n\n"
    "## Pinned Context\n\nkeep me\n"
)

_HEADER_LINE = re.compile(r"^### \d{4}-\d{2}-\d{2} \d{2}:\d{2}$", re.MULTILINE)
_ID_LINE = re.compile(r"^\*\*Memory ID\*\*: [0-9a-f]{32}$", re.MULTILINE)


def _run(env, cwd, *args):
    # Bound at the choke point: every child this module spawns comes through
    # here, so one binding covers all of them and no later call site can be
    # added without it. Measured: this module's children attempted 57 model
    # loads without it, each one a cold-cache download on a machine with no
    # warm cache (i.e. CI).
    env = {**env, **hf_cache_env()}
    proc = subprocess.run(
        [sys.executable, str(_CLI), *args],
        env=env, cwd=str(cwd), capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, f"rc={proc.returncode}\n{proc.stderr[:600]}"
    payload = json.loads(proc.stdout)
    assert payload.get("ok") is True, payload
    return payload["result"]


def _envelope(project, db, *extra):
    """The `sync` envelope from a child, with no argument added on its behalf.

    The refusal arms below must NOT carry `--claude-md-root`; passing it is
    what would stop the guard firing, so the caller names every argument.
    """
    return _run(_env(project), project, "sync", "--db-path", str(db), *extra)


def _project(tmp_path, name, body=_SCAFFOLD):
    root = tmp_path / name
    root.mkdir()
    (root / "CLAUDE.md").write_text(body, encoding="utf-8")
    return root


def _env(project):
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(project)
    return env


def _store(tmp_path, name):
    import sqlite3
    db = tmp_path / name
    conn = sqlite3.connect(str(db))
    create_test_schema(conn)
    conn.close()
    return db


def _section(text):
    """The Working Memory section body, from its heading to the next `## `."""
    return text.split("## Working Memory\n", 1)[1].split("\n## ", 1)[0]


def _headers(text):
    return [m.group(0)[4:] for m in _HEADER_LINE.finditer(_section(text))]


def _record(i, dense=False):
    """A record exercising every field the formatter renders."""
    pad = (" a" * 100) if dense else ""
    return {
        "context": f"context {i}{pad}",
        "goal": f"goal {i}{pad}",
        "decisions": [f"decision {i}a{pad}", f"decision {i}b"],
        "lessons_learned": [f"lesson {i}{pad}"],
        "reasoning_chains": [f"chain {i}{pad}"],
        "agreements_reached": [f"agreed {i}{pad}"],
        "disagreements_resolved": [f"settled {i}{pad}"],
    }


class TestQuietSavesThenOneSyncMatchFullSaves:
    """FIDELITY: the block a `sync` rebuilds is the block the saves would have
    written. Two tmp projects receive the same N records; A through full
    saves, B through `--no-sync` saves and one `sync`.

    WHAT THE EQUALITY DOES NOT BOUND. The two routes share the formatter, the
    resolver, the splice, the budget and the containment check, so this
    compares the DELTA between them and says nothing about what they share: a
    formatter regression moves both sides alike and passes here. The rendering
    itself is pinned tree-wide by the `_format_memory_entry` arms in
    `test_token_budget.py`. Do not read a green here as cover for it.

    NORMALISATION, and why each is legitimate. (1) Entry HEADERS are replaced
    by a placeholder on both sides. A's headers are the save-time clock; B's
    records are backdated to fixed dates years from A's, so B's headers cannot
    equal A's by construction. THE BACKDATING IS NOT BREAKING A TIE: the writer
    stamps `created_at` to microsecond precision, so saves do not tie even
    several inside one second, and a tie arises only from a stamp set by direct
    SQL -- which is what `_backdate` itself does. That a projected header IS the
    record's `created_at` is pinned elsewhere
    (`test_sync_projects_the_records_under_their_own_dates`).
    (2) `**Memory ID**` lines are replaced by a placeholder: each store mints
    its own ids, so the two projects hold different records by construction;
    the claim is about the RENDERING of a record, not its identity, and the
    id line's presence is asserted on both sides. (3) No `**Files**`
    normalisation is applied: the CLI `save` links no files, so neither side
    renders the line, and an arm asserts that so the omission is measured
    rather than assumed.
    """

    N = MAX_WORKING_MEMORIES + 2

    def _full_saves(self, tmp_path, dense):
        a = _project(tmp_path, "project-a")
        db = _store(tmp_path, "a.db")
        for i in range(self.N):
            result = _run(_env(a), tmp_path, "save", json.dumps(_record(i, dense)),
                          "--claude-md-root", str(a), "--db-path", str(db))
            assert result["sync_status"] == "wrote", result
        return (a / "CLAUDE.md").read_text(encoding="utf-8")

    def _quiet_saves_then_sync(self, tmp_path, dense):
        b = _project(tmp_path, "project-b")
        db = _store(tmp_path, "b.db")
        for i in range(self.N):
            result = _run(_env(b), tmp_path, "save", json.dumps(_record(i, dense)),
                          "--no-sync", "--db-path", str(db))
            assert result["sync_status"] == "suppressed", result
            _backdate(db, result["memory_id"], f"2026-01-{i + 1:02d} 00:00:00")
        result = _run(_env(b), tmp_path, "sync", "--claude-md-root", str(b),
                      "--db-path", str(db))
        assert result["sync_status"] == "wrote" and result["projected"] == MAX_WORKING_MEMORIES
        return (b / "CLAUDE.md").read_text(encoding="utf-8")

    @pytest.mark.parametrize("dense", [False, True], ids=["under-budget", "dense"])
    def test_the_two_sections_agree_apart_from_their_headers(self, tmp_path, dense):
        full = self._full_saves(tmp_path, dense)
        rebuilt = self._quiet_saves_then_sync(tmp_path, dense)

        # Non-vacuity: both hold the cap, the newest N-cap records and not the
        # oldest, and the header normalisation below has something to do.
        #
        # THE HEADER GUARD IS CLOCK-DEPENDENT, so read it as partial. Under a
        # formatter that ignored the record's own `created_at` both sides would
        # render "now" and compare EQUAL, so the guard fires and this arm
        # reddens. But a header carries only minutes: when A's surviving saves
        # straddle a minute boundary the two lists differ anyway and that mutant
        # passes here. It is killed off fixed dates, without a clock, by
        # `test_a_plain_file_gains_a_section_at_its_end`,
        # `test_one_projected_entry_leaves_no_hand_written_entry` and
        # `test_deleting_the_newest_record_promotes_the_one_past_the_cap`.
        for text in (full, rebuilt):
            assert len(_headers(text)) == MAX_WORKING_MEMORIES
            assert len(_ID_LINE.findall(_section(text))) == MAX_WORKING_MEMORIES
            assert f"context {self.N - 1}" in text and "context 0" not in text
            assert "**Files**" not in text
        assert _headers(full) != _headers(rebuilt)
        assert _ID_LINE.findall(_section(full)) != _ID_LINE.findall(_section(rebuilt))

        def normalise(text):
            section = _HEADER_LINE.sub("### <stamp>", _section(text))
            return _ID_LINE.sub("**Memory ID**: <id>", section)

        assert normalise(full) == normalise(rebuilt)

    def test_the_rest_of_the_file_is_the_same_on_both_routes(self, tmp_path):
        full = self._full_saves(tmp_path, False)
        rebuilt = self._quiet_saves_then_sync(tmp_path, False)
        strip = lambda t: t.replace(_section(t), "")  # noqa: E731
        assert strip(full) == strip(rebuilt) == _SCAFFOLD.replace(
            _section(_SCAFFOLD), "")


class TestTheStoreIsWhatTheNextSyncShows:
    """Edits to the store, then `sync`, are the only way the section changes."""

    @pytest.fixture
    def project(self, tmp_path):
        return _project(tmp_path, "project")

    @pytest.fixture
    def db(self, tmp_path):
        return _store(tmp_path, "store.db")

    def _save(self, project, db, context, stamp):
        result = _run(_env(project), project, "save", json.dumps({"context": context}),
                      "--no-sync", "--db-path", str(db))
        _backdate(db, result["memory_id"], stamp)
        return result["memory_id"]

    def _sync(self, project, db):
        return _run(_env(project), project, "sync", "--claude-md-root", str(project),
                    "--db-path", str(db))

    def test_deleting_the_newest_record_promotes_the_one_past_the_cap(self, project, db):
        ids = [self._save(project, db, f"save {i}", f"2026-02-0{i} 00:00:00")
               for i in range(1, MAX_WORKING_MEMORIES + 2)]
        first = self._sync(project, db)
        assert first["memory_ids"] == ids[:0:-1]
        text = (project / "CLAUDE.md").read_text(encoding="utf-8")
        assert "save 1" not in text and f"save {len(ids)}" in text

        _run(_env(project), project, "delete", ids[-1], "--db-path", str(db))
        second = self._sync(project, db)

        assert second["memory_ids"] == ids[-2::-1]
        text = (project / "CLAUDE.md").read_text(encoding="utf-8")
        assert "save 1" in text and f"save {len(ids)}" not in text
        assert _headers(text)[-1] == "2026-02-01 00:00"

    def test_records_of_another_project_in_the_same_store_are_not_projected(
        self, tmp_path, db
    ):
        mine = _project(tmp_path, "project-mine")
        other = _project(tmp_path, "project-other")
        self._save(mine, db, "my record", "2026-03-01 00:00:00")
        self._save(other, db, "other record", "2026-03-02 00:00:00")
        other_before = (other / "CLAUDE.md").read_bytes()

        result = self._sync(mine, db)

        status = _run(_env(mine), mine, "status", "--db-path", str(db))
        assert result["project_id"] == status["project_id"]
        assert result["projected"] == 1
        text = (mine / "CLAUDE.md").read_text(encoding="utf-8")
        assert "my record" in text and "other record" not in text
        assert (other / "CLAUDE.md").read_bytes() == other_before

    def test_records_saved_in_the_same_second_project_newest_save_first(self, project, db):
        """Full saves prepend in save order, and a `sync` over records sharing
        a `created_at` must keep that order, or the rebuilt block disagrees
        with the block the saves would have written.

        THE TIE IS MANUFACTURED HERE, NOT OBSERVED. The writer stamps to
        microsecond precision, so saves do not tie in production; `_backdate`
        sets these stamps to one value by direct SQL, which is one of the two
        ways a real tie can arise (the other is the schema default). The
        ordering contract is worth pinning for those two, not for saves.

        MEASURED DETECTION SET, so nobody has to re-run the ablation. Both
        DELETING `, rowid DESC` from `list_memories` and flipping it to `rowid
        ASC` redden this arm, and they redden it IDENTICALLY: with no tiebreak
        the plan sorts through a temp b-tree that emits tied rows in ASCENDING
        rowid, so deletion and flip return the same order. That is the opposite
        of `search_memories_by_text`, where deletion is undetectable, so its
        result does not transfer here and this one does not transfer back. One
        asymmetry to keep: the FLIP is detected under any plan, because ASC
        reverses DESC whenever two rows tie, while the DELETION is detected
        only because today's plan emits ties ascending. A future index or plan
        change that emitted them descending would make deletion invisible
        HERE. It would not go unnoticed: TestTheTiebreakReachesTheEngine in
        tests/test_memory_database.py asserts the clause in the statement the
        engine is handed and never looks at rows, so it reddens on a deletion
        under any plan. This arm's job is the narrower one it can actually do
        -- showing the ordering DOES something today, which a check on the
        statement text cannot show."""
        ids = [self._save(project, db, f"tied {i}", "2026-04-01 12:00:00")
               for i in range(MAX_WORKING_MEMORIES)]
        result = self._sync(project, db)
        assert result["memory_ids"] == ids[::-1]


class TestAFileWithoutASection:
    """What `sync` does when there is no Working Memory section to replace."""

    def test_a_plain_file_gains_a_section_at_its_end(self, tmp_path):
        target = _project(tmp_path, "plain", "# Notes\n\nsome prose\n") / "CLAUDE.md"
        result = project_memories_to_claude_md(
            [{"id": "a" * 32, "context": "fresh", "created_at": "2026-05-01 00:00:00"}],
            target=target,
        )
        assert result.reason == SyncResult.WROTE
        text = target.read_text(encoding="utf-8")
        assert text.startswith("# Notes\n\nsome prose\n")
        assert "## Working Memory\n" in text and "fresh" in text
        assert _headers(text) == ["2026-05-01 00:00"]

    def test_a_managed_region_without_memory_markers_is_declined(self, tmp_path):
        body = (
            "# Notes\n\n"
            f"{wm._MANAGED_START_MARKER}\n"
            "## Current Session\n- Resume: x\n"
            f"{wm._MANAGED_END_MARKER}\n"
        )
        target = _project(tmp_path, "managed", body) / "CLAUDE.md"
        before = target.read_bytes()
        result = project_memories_to_claude_md(
            [{"id": "a" * 32, "context": "fresh", "created_at": "2026-05-01 00:00:00"}],
            target=target,
        )
        assert result.reason == SyncResult.NO_WINDOW
        assert target.read_bytes() == before


class TestAShortProjectionStillReplaces:
    """A projection of FEWER entries than the cap removes every entry the
    section held. The cap alone would hide a prepend when the projection is
    full, so this is the arm that tells replace from prepend."""

    def test_one_projected_entry_leaves_no_hand_written_entry(self, tmp_path):
        target = _project(tmp_path, "held", _SCAFFOLD.replace(
            "## Pinned Context",
            "### 2025-01-01 01:00\n**Context**: hand one\n\n"
            "### 2024-01-01 01:00\n**Context**: hand two\n\n"
            "## Pinned Context",
        )) / "CLAUDE.md"
        assert len(_headers(target.read_text(encoding="utf-8"))) == 2

        result = project_memories_to_claude_md(
            [{"id": "a" * 32, "context": "only", "created_at": "2026-06-01 00:00:00"}],
            target=target,
        )

        assert result.reason == SyncResult.WROTE
        text = target.read_text(encoding="utf-8")
        assert _headers(text) == ["2026-06-01 00:00"]
        assert "hand one" not in text and "hand two" not in text


class TestTheEnvelopeNamesItsProjectOnEveryOutcome:
    """`project_id` rides the envelope on the outcomes that write nothing.

    `wrote` and `empty` are pinned elsewhere. These are the other two, and
    they are the ones a reader needs most: an outcome that touched no file is
    exactly when the caller has to check which project was resolved. Each arm
    asserts the WHOLE envelope, so a dropped key and an added one both fail.
    """

    @pytest.fixture
    def stocked(self, tmp_path):
        """A project holding one record of its own, so no arm below can reach
        its outcome through the `empty` return, which precedes the guards."""
        project = _project(tmp_path, "stocked")
        db = _store(tmp_path, "stocked.db")
        _run(_env(project), project, "save", json.dumps({"context": "a record"}),
             "--no-sync", "--db-path", str(db))
        return project, db

    def _project_id(self, project, db):
        return _run(_env(project), project, "status", "--db-path", str(db))["project_id"]

    def test_a_refused_sync_still_names_the_project(self, stocked):
        project, db = stocked
        before = (project / "CLAUDE.md").read_bytes()

        # No `--claude-md-root`: the child inherits PYTEST_CURRENT_TEST with no
        # anchor declared, which is the guard's refusal shape.
        envelope = _envelope(project, db)

        assert envelope == {
            "sync_status": "refused", "projected": 0, "memory_ids": [],
            "project_id": self._project_id(project, db),
        }
        assert (project / "CLAUDE.md").read_bytes() == before

    def test_a_failed_sync_still_names_the_project(self, stocked):
        project, db = stocked
        # A directory where the file belongs: the write raises, and the raise
        # is what `failed` reports. Nothing about the store is disturbed.
        (project / "CLAUDE.md").unlink()
        (project / "CLAUDE.md").mkdir()

        envelope = _envelope(project, db, "--claude-md-root", str(project))

        assert envelope == {
            "sync_status": "failed", "projected": 0, "memory_ids": [],
            "project_id": self._project_id(project, db),
        }

    def test_the_same_project_writes_when_the_obstruction_is_gone(self, stocked):
        """Control for both arms above: the refusal and the failure are the
        conditions, not the fixture. The same project and store, anchored and
        unobstructed, reach `wrote` with the same `project_id`."""
        project, db = stocked

        envelope = _envelope(project, db, "--claude-md-root", str(project))

        assert envelope["sync_status"] == "wrote" and envelope["projected"] == 1
        assert envelope["project_id"] == self._project_id(project, db)


# ---------------------------------------------------------------------------
# The prose that names the count agrees with the constant
# ---------------------------------------------------------------------------

_NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
                 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}

_HARVEST_SKILL = _PLUGIN / "skills" / "pact-handoff-harvest" / "SKILL.md"
_SECRETARY = _PLUGIN / "agents" / "pact-secretary.md"
_MEMORY_SKILL = _PLUGIN / "skills" / "pact-memory" / "SKILL.md"


def _slice(path, start, stop_pattern):
    text = path.read_text(encoding="utf-8")
    assert start in text, f"{path.name} lost its anchor {start!r}"
    rest = text.split(start, 1)[1]
    m = re.search(stop_pattern, rest, re.MULTILINE)
    return start + (rest[:m.start()] if m else rest)


def _prose_sites():
    """Every instruction passage that tells an agent how many records the
    section holds, keyed by where it lives."""
    return {
        "harvest Step 7.5": _slice(_HARVEST_SKILL, "### Step 7.5:", r"^### "),
        "harvest Consolidation Step 4": _slice(
            _HARVEST_SKILL, "### Step 4: Reconcile Working Memory", r"^### "),
        "secretary spawn step": _slice(
            _SECRETARY, "1. **Rebuild Working Memory from the store**", r"^\d+\. "),
        "secretary WORKING MEMORY SYNC": _slice(
            _SECRETARY, "# WORKING MEMORY SYNC", r"^# "),
        "pact-memory sync row": _slice(_MEMORY_SKILL, "| `sync` |", r"^\|"),
        "pact-memory Sync Command": _slice(_MEMORY_SKILL, "### Sync Command", r"^### "),
    }


_NUMBER = "|".join(_NUMBER_WORDS.values()) + r"|\d+"

# BOTH WORD ORDERS. "the newest three records" and "the three newest records"
# say the same thing, and pinning only the first reddens on a reword that
# changes no meaning -- a false alarm is what teaches an editor to delete a
# pin. The number still carries the check, so drift in the constant is caught
# either way.
_COUNT_MENTION = re.compile(rf"(?:newest ({_NUMBER})\b|\b({_NUMBER}) newest\b)")


def _sites_disagreeing_with(count):
    accepted = {_NUMBER_WORDS[count], str(count)}
    disagreeing = []
    for name, passage in _prose_sites().items():
        # One alternative matches per mention, so exactly one group is set.
        mentions = [before or after
                    for before, after in _COUNT_MENTION.findall(passage)]
        if not mentions or any(m not in accepted for m in mentions):
            disagreeing.append((name, mentions))
    return disagreeing


_REPORT_SLOT = "{sync_status | empty, project {project_id}}"

# Where each report template renders the slot, and how many times.
_SLOT_SITES = {_HARVEST_SKILL: 2, _SECRETARY: 1}


class TestTheReportTemplatesRenderTheProject:
    """The instruction to record `project_id` and the template that renders it
    are one change. Dropping `{project_id}` from a slot leaves the instruction
    asking for a value with nowhere to put it, which no other arm would see."""

    def test_every_template_slot_carries_the_project(self):
        # The comparison below is built from `_SLOT_SITES` itself, so it agrees
        # with a census that lost a FILE. The per-file counts are pinned there;
        # the number of files is not, so pin it here.
        assert len(_SLOT_SITES) == 2, sorted(p.name for p in _SLOT_SITES)
        counts = {path.name: path.read_text(encoding="utf-8").count(_REPORT_SLOT)
                  for path in _SLOT_SITES}
        assert counts == {path.name: n for path, n in _SLOT_SITES.items()}

    def test_no_slot_renders_the_status_without_the_project(self):
        """Control: the count above cannot tell a dropped slot from a slot
        degraded back to the status alone, so name that shape and forbid it."""
        degraded = "{sync_status | empty}"
        for path in _SLOT_SITES:
            assert degraded not in path.read_text(encoding="utf-8"), path.name


class TestTheProseCountIsTheConstant:
    def test_the_census_names_every_site(self):
        """Cardinality, because the two arms below cannot supply it. Both
        compare against `_prose_sites()` itself, so a census that lost a site
        agrees with itself and stays green while that site goes unchecked.

        No subset check against a name constant, unlike the propagating-command
        census this copies: those stems come from a glob and can gain a member,
        these are authored in the dict itself, and `_slice` already fails loudly
        on an anchor that no longer matches."""
        sites = _prose_sites()
        assert sites, "the prose census extracted nothing; report this as an " \
                      "extraction failure, not as agreement"
        assert len(sites) == 6, sorted(sites)

    def test_every_site_names_the_cap(self):
        assert _sites_disagreeing_with(MAX_WORKING_MEMORIES) == []

    def test_every_site_would_disagree_with_a_different_cap(self):
        """Control: the check is live at every site, not vacuously green."""
        disagreeing = _sites_disagreeing_with(MAX_WORKING_MEMORIES + 1)
        assert sorted(name for name, _ in disagreeing) == sorted(_prose_sites())
