"""
Location: pact-plugin/tests/test_non_tty_w1.py
Summary: W1 observable contracts for non-TTY harness support (#1658/#1309):
the resolver-pair keying matrix (aligned-first one-directional, both-DISAGREE
fail-closed), iter/reader compatibility through the containment seam, the
harness-stamp truth table (transcript Writer 1 + substrate Writer 2, monotonic,
misstamp guard), and the additive journal diagnostics.
Used by: the full-suite merge gate (`cd pact-plugin && python3 -m pytest -q`,
no path argument). Companion anchors live in test_team_name_detect_align.py,
test_missed_wake_scan_integration.py and test_stop_background_gate.py — the
three historical iter call anchors this file does NOT duplicate.
"""

import json

# No path setup here: tests/conftest.py owns sys.path for tests/ and hooks/
# (the path-setup pin enforces exactly that ownership).

import shared.pact_context as pact_context
import bootstrap_marker_writer as bmw
from shared.session_journal import _validate_event_schema, make_event
from shared.task_utils import (
    iter_team_task_jsons,
    read_task_json,
    resolve_task_dir,
    resolve_team_dir,
    task_dir_for,
)

SHORT_NAME = "session-1a2b3c4d"
FULL_UUID = "9f8e7d6c-5b4a-3f2e-1d0c-9b8a7f6e5d4c"


def _seed_candidates(monkeypatch, aligned, persisted, session_id):
    """Seed the resolver's context candidates the way an initialised hook
    frame would present them (aligned via the identity-match resolver,
    persisted via the session-context cache). Patches the pact_context
    MODULE attributes — the resolver reads them late-bound through the
    module, so task_utils-level from-import patches would not reach it."""
    monkeypatch.setattr(pact_context, "get_team_name", lambda: aligned)
    monkeypatch.setattr(
        pact_context,
        "get_pact_context",
        lambda: {"team_name": persisted, "session_id": session_id},
    )
    monkeypatch.setattr(pact_context, "get_session_id", lambda: session_id)


def _make_store(tmp_path, *names, with_config=False):
    base = tmp_path / "tasks"
    for name in names:
        (base / name).mkdir(parents=True, exist_ok=True)
        (base / name / "1.json").write_text(
            json.dumps({"id": "1", "owner": "x"}), encoding="utf-8"
        )
    if with_config:
        teams = tmp_path / "teams"
        for name in names:
            (teams / name).mkdir(parents=True, exist_ok=True)
            (teams / name / "config.json").write_text("{}", encoding="utf-8")
    return base


class TestResolverKeyingMatrix:
    """The four keying cells + the ambiguity cell, over resolve_task_dir
    (resolve_team_dir shares _resolve_live_team_dir and is spot-checked)."""

    def test_short_name_present_cli_shape(self, tmp_path, monkeypatch):
        """CLI shape: aligned/persisted agree on the short name, its dir
        exists -> resolves to it (CLI-unchanged cell)."""
        _seed_candidates(monkeypatch, SHORT_NAME, SHORT_NAME, FULL_UUID)
        base = _make_store(tmp_path, SHORT_NAME)
        got = resolve_task_dir(tasks_base_dir=str(base))
        assert got is not None and got.name == SHORT_NAME

    def test_uuid_only_config_less_shape(self, tmp_path, monkeypatch):
        """Non-TTY shape: aligned adopts the full UUID (branch-2), persisted
        still says the short name, ONLY the UUID store exists -> UUID wins
        (the #1020 fix cell: the raw persisted read used to miss)."""
        _seed_candidates(monkeypatch, FULL_UUID, SHORT_NAME, FULL_UUID)
        base = _make_store(tmp_path, FULL_UUID)
        got = resolve_task_dir(tasks_base_dir=str(base))
        assert got is not None and got.name == FULL_UUID

    def test_both_agree(self, tmp_path, monkeypatch):
        """Aligned == persisted, dir exists -> trivially that dir."""
        _seed_candidates(monkeypatch, SHORT_NAME, SHORT_NAME, FULL_UUID)
        base = _make_store(tmp_path, SHORT_NAME)
        assert resolve_task_dir(tasks_base_dir=str(base)).name == SHORT_NAME

    def test_both_disagree_fails_closed(self, tmp_path, monkeypatch):
        """BOTH-DISAGREE: aligned and persisted differ AND both name EXISTING
        dirs -> None (ambiguous substrate; consumers suppress) — never a
        silent pick of either. Mirrors bootstrap_marker_writer's ambiguity
        suppression."""
        _seed_candidates(monkeypatch, FULL_UUID, SHORT_NAME, FULL_UUID)
        base = _make_store(tmp_path, FULL_UUID, SHORT_NAME)
        assert resolve_task_dir(tasks_base_dir=str(base)) is None

    def test_neither_exists_none(self, tmp_path, monkeypatch):
        """No candidate dir exists -> None (absence is the existing no-team
        signal, not an error)."""
        _seed_candidates(monkeypatch, SHORT_NAME, SHORT_NAME, FULL_UUID)
        base = _make_store(tmp_path)  # empty tasks root
        assert resolve_task_dir(tasks_base_dir=str(base)) is None

    def test_session_id_dir_fallback(self, tmp_path, monkeypatch):
        """Both name candidates empty (uninit'd cache), but the session-id
        dir exists (config-less UUID store) -> session-id dir wins."""
        _seed_candidates(monkeypatch, "", "", FULL_UUID)
        base = _make_store(tmp_path, FULL_UUID)
        got = resolve_task_dir(tasks_base_dir=str(base))
        assert got is not None and got.name == FULL_UUID

    def test_resolution_is_one_directional_upgrade(self, tmp_path, monkeypatch):
        """The persisted name's dir exists but the ALIGNED name's dir exists
        too -> aligned wins (upgrade only; never the stale persisted pick)."""
        _seed_candidates(monkeypatch, FULL_UUID, SHORT_NAME, FULL_UUID)
        base = _make_store(tmp_path, FULL_UUID, SHORT_NAME)
        # both exist -> ambiguity cell fires first, so prove the order on a
        # substrate where ONLY the aligned dir exists alongside a persisted
        # name whose dir is absent: aligned still wins over session-id.
        base2 = tmp_path / "tasks2"
        base2.mkdir()
        (base2 / FULL_UUID).mkdir()
        assert resolve_task_dir(tasks_base_dir=str(base2)).name == FULL_UUID

    def test_aligned_first_upgrade_is_observable(self, tmp_path, monkeypatch):
        """The CLI-unchanged pin arm's DISCRIMINATING cell: three fully
        disjoint candidates (aligned/persisted/session-id), only the
        ALIGNED dir exists -> resolves the aligned dir. Seeding aligned ==
        persisted (as the plain CLI cells do) cannot distinguish the
        aligned-first walk from a persisted-first one, and a session-id
        equal to the aligned name masks the fallback arm — so the pin needs
        this disjoint cell to be measurable at all."""
        third = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        assert third not in (SHORT_NAME, FULL_UUID)
        _seed_candidates(monkeypatch, SHORT_NAME, FULL_UUID, third)
        base = _make_store(tmp_path, SHORT_NAME)  # ONLY the aligned store
        got = resolve_task_dir(tasks_base_dir=str(base))
        assert got is not None and got.name == SHORT_NAME

    def test_resolve_team_dir_shared_core(self, tmp_path, monkeypatch):
        """resolve_team_dir resolves the teams/ root through the same core
        (same order, same ambiguity cell)."""
        _seed_candidates(monkeypatch, FULL_UUID, SHORT_NAME, FULL_UUID)
        teams = tmp_path / "teams"
        (teams / FULL_UUID).mkdir(parents=True)
        got = resolve_team_dir(teams_dir=str(teams))
        assert got is not None and got.name == FULL_UUID

    def test_never_raises_on_bad_seams(self, monkeypatch):
        """Hostile seam inputs degrade to None (never raises)."""
        monkeypatch.setattr(
            pact_context,
            "get_pact_context",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr(pact_context, "get_team_name", lambda: "")
        monkeypatch.setattr(pact_context, "get_session_id", lambda: "")
        assert resolve_task_dir(tasks_base_dir="/nonexistent-root-xyz") is None


class TestContainmentSeam:
    """task_dir_for owns validation + the symlink-escape anchor for every
    caller; iter and read_task_json route through it."""

    def test_task_dir_for_rejects_traversal(self):
        assert task_dir_for("../escape") is None
        assert task_dir_for("a/b") is None
        assert task_dir_for("") is None
        assert task_dir_for(None) is None
        assert task_dir_for("ok-name").name == "ok-name"

    def test_task_dir_for_symlink_escape_rejected(self, tmp_path):
        """A SYMLINK at tasks/{name} pointing outside the base -> None."""
        base = tmp_path / "tasks"
        base.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (base / "evil").symlink_to(outside, target_is_directory=True)
        assert task_dir_for("evil", tasks_base_dir=str(base)) is None

    def test_iter_routes_through_seam_and_keeps_shape(self, tmp_path):
        """iter's signature/behavior is unchanged through the re-expression:
        explicit name, no candidate fallthrough, dotfile-skip, yield dicts."""
        base = _make_store(tmp_path, SHORT_NAME)
        (base / SHORT_NAME / ".hidden.json").write_text("{}", encoding="utf-8")
        got = list(iter_team_task_jsons(SHORT_NAME, tasks_base_dir=str(base)))
        assert len(got) == 1 and got[0]["id"] == "1"

    def test_iter_unsafe_name_yields_nothing(self, tmp_path):
        base = _make_store(tmp_path)
        assert list(iter_team_task_jsons("../x", tasks_base_dir=str(base))) == []

    def test_read_task_json_routes_through_seam(self, tmp_path):
        """The reader finds the task under the team dir and keeps its
        bare-base fallback; unsafe names fall to the bare base (fail-open)."""
        base = _make_store(tmp_path, SHORT_NAME)
        assert read_task_json("1", SHORT_NAME, tasks_base_dir=str(base))["id"] == "1"
        assert read_task_json("1", "../evil", tasks_base_dir=str(base)) == {}


class TestSetHarnessStamp:
    """pact_context.set_harness_stamp: single legal value, monotonic,
    surgical (other fields preserved), fail-open."""

    def _stamp_target(self, tmp_path, recorded=None):
        target = tmp_path / "pact-session-context.json"
        target.write_text(json.dumps(recorded or {
            "team_name": SHORT_NAME, "session_id": FULL_UUID,
            "project_dir": "/p", "plugin_root": "/r",
            "started_at": "2026-10-21T00:00:00+00:00", "process_key": "",
            "harness": "",
        }), encoding="utf-8")
        return target

    def test_sets_non_tty_and_preserves_other_fields(self, tmp_path, monkeypatch):
        target = self._stamp_target(tmp_path)
        monkeypatch.setattr(pact_context, "_context_path", target)
        monkeypatch.setattr(pact_context, "_cache", {"harness": ""})
        assert pact_context.set_harness_stamp("non_tty", {"agent_type": "PACT:pact-orchestrator"}) is True
        data = json.loads(target.read_text())
        assert data["harness"] == "non_tty"
        assert data["team_name"] == SHORT_NAME  # surgical: nothing else rewritten
        assert pact_context._cache["harness"] == "non_tty"

    def test_monotonic_never_changes_a_stamped_value(self, tmp_path, monkeypatch):
        target = self._stamp_target(tmp_path, recorded={
            "team_name": SHORT_NAME, "harness": "non_tty",
        })
        monkeypatch.setattr(pact_context, "_context_path", target)
        assert pact_context.set_harness_stamp("non_tty", {"agent_type": "PACT:pact-orchestrator"}) is False
        assert json.loads(target.read_text())["harness"] == "non_tty"

    def test_illegal_value_refused(self, tmp_path, monkeypatch):
        target = self._stamp_target(tmp_path)
        monkeypatch.setattr(pact_context, "_context_path", target)
        assert pact_context.set_harness_stamp("desktop", {"agent_type": "PACT:pact-orchestrator"}) is False
        assert json.loads(target.read_text())["harness"] == ""

    def test_fail_open_without_context_path(self, monkeypatch):
        monkeypatch.setattr(pact_context, "_context_path", None)
        assert pact_context.set_harness_stamp("non_tty", {"agent_type": "PACT:pact-orchestrator"}) is False


class TestWriterOneTranscript:
    """session_init Writer 1: bounded transcript entrypoint read, fail-open."""

    def _transcript(self, tmp_path, lines):
        path = tmp_path / "session.jsonl"
        path.write_text("\n".join(json.dumps(x) for x in lines) + "\n",
                        encoding="utf-8")
        return str(path)

    def test_reads_entrypoint_from_early_lines(self, tmp_path):
        from session_init import _read_transcript_entrypoint
        lines = [
            {"leafUuid": "a", "type": "summary"},
            {"type": "user", "entrypoint": "remote"},
            {"type": "assistant", "entrypoint": "remote"},
        ]
        assert _read_transcript_entrypoint(self._transcript(tmp_path, lines)) == "remote"

    def test_desktop_entrypoint_value(self, tmp_path):
        from session_init import _read_transcript_entrypoint
        lines = [{"type": "attachment", "entrypoint": "claude-desktop"}]
        assert _read_transcript_entrypoint(
            self._transcript(tmp_path, lines)) == "claude-desktop"

    def test_cli_entrypoint_is_not_stamped_by_mapping(self):
        """The mapping table maps only remote/claude-desktop onto non_tty."""
        from session_init import _NON_TTY_ENTRYPOINTS
        assert _NON_TTY_ENTRYPOINTS == frozenset({"remote", "claude-desktop"})
        assert "cli" not in _NON_TTY_ENTRYPOINTS

    def test_fail_open_absent_path_and_missing_field(self, tmp_path):
        from session_init import _read_transcript_entrypoint
        assert _read_transcript_entrypoint(None) == ""
        assert _read_transcript_entrypoint("") == ""
        assert _read_transcript_entrypoint(str(tmp_path / "nope.jsonl")) == ""
        lines = [{"type": "user"}, {"type": "assistant", "entrypoint": 42}]
        assert _read_transcript_entrypoint(
            self._transcript(tmp_path, lines)) == ""  # non-str ignored

    def test_saved_context_carries_harness_forward(self, tmp_path, monkeypatch):
        """_saved_context keeps a recorded harness (the monotonic
        carry-forward into build_context_cache)."""
        from session_init import _saved_context
        session_dir = tmp_path / "sess"
        session_dir.mkdir()
        monkeypatch.setattr(
            "session_init.build_session_path", lambda slug, sid: session_dir
        )
        (session_dir / "pact-session-context.json").write_text(json.dumps({
            "team_name": SHORT_NAME,
            "started_at": "2026-10-21T00:00:00+00:00",
            "harness": "non_tty",
        }), encoding="utf-8")
        saved = _saved_context(FULL_UUID, "/proj")
        assert saved.get("harness") == "non_tty"

    def test_empty_context_schema_has_harness_key(self):
        assert pact_context._EMPTY_CONTEXT["harness"] == ""

    def test_build_context_cache_threads_harness(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pact_context, "_context_path", None)
        result = pact_context.build_context_cache(
            SHORT_NAME, FULL_UUID, "/proj", "/root", harness="non_tty",
        )
        assert result is not None
        assert result[1]["harness"] == "non_tty"
        assert pact_context._cache["harness"] == "non_tty"


class TestWriterTwoSubstrate:
    """bootstrap_marker_writer._refine_harness_stamp: the substrate legs,
    the spawn-witness gating, and the misstamp guard."""

    def _refine(self, monkeypatch, *, aligned, session_id, team_dir,
                has_secretary):
        stamps = []
        monkeypatch.setattr(bmw.pact_context, "get_team_name", lambda: aligned)
        monkeypatch.setattr(
            bmw.pact_context, "get_session_id", lambda: session_id)
        monkeypatch.setattr(
            bmw.pact_context, "set_harness_stamp",
            lambda v, frame=None: stamps.append(v) or True,
        )
        monkeypatch.setattr(bmw, "resolve_team_dir", lambda: team_dir)
        monkeypatch.setattr(bmw, "_team_has_secretary", lambda t: has_secretary)
        bmw._refine_harness_stamp({})
        return stamps

    def test_leg_one_uuid_keyed_store(self, tmp_path, monkeypatch):
        """Aligned name == full session UUID -> stamp (config-less substrate
        adopted the UUID-anchored team)."""
        teams = tmp_path / "teams"
        (teams / FULL_UUID).mkdir(parents=True)
        team_dir = teams / FULL_UUID
        stamps = self._refine(
            monkeypatch, aligned=FULL_UUID, session_id=FULL_UUID,
            team_dir=team_dir, has_secretary=False,
        )
        assert stamps == ["non_tty"]

    def test_leg_two_spawn_witnessed_config_absent(self, tmp_path, monkeypatch):
        """Team dir live WITHOUT config.json + secretary spawn witnessed ->
        stamp (absence AFTER a spawn is evidence)."""
        teams = tmp_path / "teams"
        (teams / SHORT_NAME).mkdir(parents=True)
        stamps = self._refine(
            monkeypatch, aligned=SHORT_NAME, session_id=FULL_UUID,
            team_dir=teams / SHORT_NAME, has_secretary=True,
        )
        assert stamps == ["non_tty"]

    def test_no_spawn_witness_no_stamp(self, tmp_path, monkeypatch):
        """Absence BEFORE any spawn is not evidence -> no stamp (only the
        transcript leg may stamp pre-spawn)."""
        teams = tmp_path / "teams"
        (teams / SHORT_NAME).mkdir(parents=True)
        stamps = self._refine(
            monkeypatch, aligned=SHORT_NAME, session_id=FULL_UUID,
            team_dir=teams / SHORT_NAME, has_secretary=False,
        )
        assert stamps == []

    def test_misstamp_guard_config_present(self, tmp_path, monkeypatch):
        """THE misstamp-guard cell: config.json present + aligned name is the
        short name (not the UUID) -> NOT non_tty, even with a spawn."""
        teams = tmp_path / "teams"
        (teams / SHORT_NAME).mkdir(parents=True)
        (teams / SHORT_NAME / "config.json").write_text("{}", encoding="utf-8")
        stamps = self._refine(
            monkeypatch, aligned=SHORT_NAME, session_id=FULL_UUID,
            team_dir=teams / SHORT_NAME, has_secretary=True,
        )
        assert stamps == []

    def test_no_live_team_dir_no_stamp(self, tmp_path, monkeypatch):
        stamps = self._refine(
            monkeypatch, aligned=SHORT_NAME, session_id=FULL_UUID,
            team_dir=None, has_secretary=True,
        )
        assert stamps == []

    def test_empty_aligned_no_stamp(self, tmp_path, monkeypatch):
        stamps = self._refine(
            monkeypatch, aligned="", session_id=FULL_UUID,
            team_dir=None, has_secretary=False,
        )
        assert stamps == []


class TestJournalDiagnostics:
    """Additive, non-gating: session_start.entrypoint (optional, type-checked)
    and dispatch_decision.resolved_team (unregistered-type passthrough)."""

    def test_session_start_entrypoint_str_passes(self):
        event = make_event(
            "session_start",
            team=SHORT_NAME,
            session_id=FULL_UUID,
            project_dir="/proj",
            source="startup",
            entrypoint="remote",
        )
        ok, why = _validate_event_schema(event)
        assert ok, why

    def test_session_start_entrypoint_wrong_type_rejected(self):
        event = make_event(
            "session_start",
            team=SHORT_NAME,
            session_id=FULL_UUID,
            project_dir="/proj",
            source="startup",
            entrypoint=42,
        )
        ok, _ = _validate_event_schema(event)
        assert not ok

    def test_session_start_entrypoint_absent_still_passes(self):
        event = make_event(
            "session_start",
            team=SHORT_NAME,
            session_id=FULL_UUID,
            project_dir="/proj",
            source="startup",
        )
        ok, why = _validate_event_schema(event)
        assert ok, why

    def test_dispatch_decision_resolved_team_passthrough(self):
        """dispatch_decision is not a registered type (unknown-type opt-in
        pass-through), so the additive resolved_team rides the event."""
        event = make_event(
            "dispatch_decision",
            decision="allow",
            team_name=SHORT_NAME,
            resolved_team=FULL_UUID,
        )
        ok, why = _validate_event_schema(event)
        assert ok, why
        assert event["resolved_team"] == FULL_UUID


# COUNTER-TEST-BY-REVERT CARDINALITY (spec §5; MEASURED on this worktree,
# 2026-10-21, `python3 -m pytest tests/test_non_tty_w1.py -q` after each
# single-line mutation of _resolve_live_team_dir's candidate walk):
#
#   baseline (unmutated):            36 passed, 0 failed.
#   ALWAYS-FALLBACK (walk drops the  1 failed, 35 passed — exactly
#   aligned candidate):                test_aligned_first_upgrade_is_observable.
#   NEVER-FALLBACK (walk keeps the   1 failed, 35 passed — exactly
#   aligned candidate only):           test_session_id_dir_fallback.
#
# Two design notes the measurements forced:
# (1) The plain CLI cells (aligned == persisted) CANNOT distinguish an
#     aligned-first walk from a persisted-first one — the first ALWAYS-FALLBACK
#     measurement reddened NOTHING until test_aligned_first_upgrade_is_
#     observable added a fully disjoint-candidates cell (aligned/persisted/
#     session-id all different; only the aligned store exists). A session-id
#     equal to the aligned name likewise masks the fallback arm, so the cell
#     uses three distinct names.
# (2) The UUID-only cell does NOT redden under NEVER-FALLBACK, by design of
#     the cell: it seeds the POST-ALIGNMENT truth (aligned == UUID), so the
#     aligned candidate alone already resolves it — that is the fix's claim,
#     that alignment (not fallback) carries the UUID store. The fallback
#     arm's observable is the session-id cell (both name candidates empty).
#     This deviates from spec §5's letter ("never-fallback reddens exactly
#     the UUID-only tests") for a measured reason; flagged in the W1 HANDOFF.
