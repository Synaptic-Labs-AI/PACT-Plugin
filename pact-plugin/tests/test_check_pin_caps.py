"""
Tests for scripts/check_pin_caps.py — ADVISORY CLI (cycle-8 demotion).

Risk tier: CRITICAL on the advisory-shape contract (/PACT:prune-memory
depends on the JSON shape and on the fail-open exit-code invariant).
The CLI no longer enforces caps — enforcement lives in
hooks/pin_caps_gate.py. Tests here cover the retained surface:
  * --status and --list-evictable emit the same JSON payload shape
  * evictable_pins serialization (stale / override / heading strip)
  * fail-open paths never exit non-zero
  * SACROSANCT: never exit 2 under any resolved-state or fail-open path

Retired-flag tests (--new-body, --body-from-stdin, --has-override,
--override-rationale, stdin-empty refusal, in-band override rationale
validation) were deleted in the cycle-8 CLI-demote commit. Hook-side
enforcement is exercised by tests/test_pin_caps_gate.py (smoke) plus
Phase E exhaustive matrix coverage.

Historical reference: pre-cycle-8 test file carried ~600 LOC of
add-path / stdin / override tests. Those tests intentionally exercised
the CLI's enforcement responsibilities, which no longer exist. Keeping
them would produce phantom-green coverage against a CLI path no
curator ever reaches post-demotion (hook denies first).
"""

import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from helpers import make_claude_md_with_pins, make_pin_entry  # noqa: E402
from tests.test_unreadable_location_carriers import (  # noqa: E402
    _NEEDS_NON_ROOT,
    _preferred_unsearchable,
    lock,  # noqa: F401 -- a fixture, requested by name below
)


@pytest.fixture
def patched_claude_md(tmp_path, monkeypatch):
    """Write a CLAUDE.md and patch get_project_claude_md_path resolution."""
    def _write(content):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(content, encoding="utf-8")
        import check_pin_caps
        monkeypatch.setattr(
            check_pin_caps, "get_project_claude_md_path", lambda errors=None: claude_md
        )
        return claude_md
    return _write


def _run_cli(argv) -> tuple[int, dict]:
    """Invoke check_pin_caps.main and capture stdout + return code.

    Contract: the demoted CLI always emits a JSON payload (advisory
    or fail-open both write to stdout) — an empty stdout would be a
    CLI bug. We assert on that invariant so downstream tests can
    subscript the payload dict without Optional-narrowing noise.
    """
    import check_pin_caps
    buf = io.StringIO()
    with patch.object(sys, "stdout", buf):
        rc = check_pin_caps.main(argv)
    out = buf.getvalue().strip()
    assert out, "CLI produced no stdout — advisory payload is always required"
    payload = json.loads(out)
    assert isinstance(payload, dict), (
        f"CLI payload must be a JSON object, got {type(payload).__name__}"
    )
    return rc, payload


def _make_pinned_content(n_pins=0, pin_body_chars=100, stale_indices=()):
    """Thin wrapper around helpers.py factories preserving legacy signature."""
    entries = [
        make_pin_entry(
            title=f"Pin {i}",
            body_chars=pin_body_chars,
            stale_date="2026-01-01" if i in stale_indices else None,
        )
        for i in range(n_pins)
    ]
    return make_claude_md_with_pins(entries)


class TestCheckPinCaps_Advisory_StatusQuery:
    """--status emits current slot state. Post-demotion, this is also the
    default (no-flag) behavior — the CLI has no add-time flags to select."""

    def test_status_zero_pins(self, patched_claude_md):
        """Empty Pinned Context body routes through fail-open in _resolve_pins
        (parsed is None → reason='no pinned section'). --status surfaces
        the fail-open signal rather than a fake 0/12 slot state."""
        patched_claude_md(_make_pinned_content(0))
        rc, payload = _run_cli(["--status"])
        assert rc == 0
        assert payload["allowed"] is True
        assert payload["violation"] is None
        assert "unknown" in payload["slot_status"]
        assert "proceeding" in payload["slot_status"]
        assert payload["evictable_pins"] == []

    def test_status_with_pins(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(3, pin_body_chars=200))
        rc, payload = _run_cli(["--status"])
        assert rc == 0
        assert payload["allowed"] is True
        assert "3/12" in payload["slot_status"]
        assert len(payload["evictable_pins"]) == 3
        p0 = payload["evictable_pins"][0]
        assert p0["index"] == 0
        assert p0["heading"] == "Pin 0"  # "### " stripped
        assert p0["chars"] == 200
        assert p0["stale"] is False
        assert p0["override"] is False

    def test_default_argv_equivalent_to_status(self, patched_claude_md):
        """No flags → same payload as --status. Post-demotion the CLI has
        no argv-selected behaviors; advisory is the only mode."""
        patched_claude_md(_make_pinned_content(3))
        rc_default, payload_default = _run_cli([])
        rc_status, payload_status = _run_cli(["--status"])
        assert rc_default == rc_status == 0
        assert payload_default == payload_status

    def test_list_evictable_equivalent_to_status(self, patched_claude_md):
        """--list-evictable is an alias for --status; same payload shape."""
        patched_claude_md(_make_pinned_content(3))
        rc_list, payload_list = _run_cli(["--list-evictable"])
        rc_status, payload_status = _run_cli(["--status"])
        assert rc_list == rc_status == 0
        assert payload_list == payload_status


class TestCheckPinCaps_Advisory_FailOpen:
    """Resolution / read / parse failures yield allowed=true + exit 0.

    Post-demotion every code path is advisory; fail-open is the universal
    shape. Degradation reason surfaces in slot_status so consumers see
    'unknown (...)' instead of a fake zero-state.
    """

    def test_no_claude_md_fails_open(self, monkeypatch):
        import staleness
        import check_pin_caps
        monkeypatch.setattr(
            staleness, "get_project_claude_md_path", lambda errors=None: None
        )
        monkeypatch.setattr(
            check_pin_caps, "get_project_claude_md_path", lambda errors=None: None
        )
        rc, payload = _run_cli(["--status"])
        assert rc == 0
        assert payload["allowed"] is True
        assert "unknown" in payload["slot_status"]
        assert "claude.md not found" in payload["slot_status"]

    def test_unreadable_file_fails_open(self, patched_claude_md, monkeypatch):
        patched_claude_md(_make_pinned_content(3))

        def _raise(*a, **k):
            raise IOError("simulated read failure")

        monkeypatch.setattr(Path, "read_text", _raise)
        rc, payload = _run_cli(["--status"])
        assert rc == 0
        assert payload["allowed"] is True
        assert "unknown" in payload["slot_status"]

    def test_no_pinned_section_fails_open(self, patched_claude_md):
        """CLAUDE.md exists but has no ## Pinned Context."""
        patched_claude_md("# Project\n\n## Working Memory\n\n")
        rc, payload = _run_cli(["--status"])
        assert rc == 0
        assert payload["allowed"] is True
        assert "no pinned section" in payload["slot_status"]

    def test_parse_exception_fails_open(self, patched_claude_md, monkeypatch):
        patched_claude_md(_make_pinned_content(3))
        import check_pin_caps

        def _boom(_pinned_content):
            raise RuntimeError("parse blew up")

        monkeypatch.setattr(check_pin_caps, "parse_pins", _boom)
        rc, payload = _run_cli(["--status"])
        assert rc == 0
        assert payload["allowed"] is True
        assert "parse error" in payload["slot_status"]


class TestCheckPinCaps_UnreadableIsNotMissing:
    """The resolver runs for real here: CLAUDE_PROJECT_DIR names a project
    whose `.claude/` cannot be searched, beside a readable legacy CLAUDE.md,
    and the cwd is an empty directory outside any repository. Resolution stops
    at the declared project, and the slot status says the file could not be
    read, names the cause, and carries no path."""

    @_NEEDS_NON_ROOT
    def test_an_unsearchable_claude_dir_is_reported_unreadable(
        self, tmp_path, lock, monkeypatch
    ):
        proj, _legacy = _preferred_unsearchable(tmp_path, lock)
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.chdir(empty)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))

        rc, payload = _run_cli(["--status"])

        status = payload["slot_status"]
        assert rc == 0 and payload["allowed"] is True
        assert "claude.md could not be read: PermissionError (EACCES)" in status, status
        assert "not found" not in status and "/" not in status, status

    def test_a_git_call_that_did_not_answer_is_reported_as_git(self, tmp_path, monkeypatch):
        """The declared project holds no CLAUDE.md, so resolution reaches the
        git rung, and git fails to run. The status names git, not CLAUDE.md's
        permissions."""
        import subprocess

        proj = tmp_path / "proj"
        proj.mkdir()
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.chdir(empty)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))

        def git_denied(*args, **kwargs):
            raise PermissionError(13, "Permission denied", "git")

        monkeypatch.setattr(subprocess, "run", git_denied)
        rc, payload = _run_cli(["--status"])

        status = payload["slot_status"]
        assert rc == 0
        assert "claude.md could not be located: git PermissionError (EACCES)" in status, status
        assert "could not be read" not in status and "/" not in status, status

    def test_a_project_with_no_claude_md_is_still_not_found(self, tmp_path, monkeypatch):
        """The control: the same call with a readable, empty project."""
        proj = tmp_path / "proj"
        proj.mkdir()
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.chdir(empty)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))

        rc, payload = _run_cli(["--status"])

        assert rc == 0
        assert "claude.md not found" in payload["slot_status"], payload["slot_status"]


class TestCheckPinCaps_Advisory_EvictablePins:
    """evictable_pins surface — ordering, stale/override flags, heading strip."""

    def test_evictable_includes_stale_flag(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(3, stale_indices={1}))
        rc, payload = _run_cli(["--status"])
        flags = [p["stale"] for p in payload["evictable_pins"]]
        assert flags == [False, True, False]

    def test_evictable_includes_override_flag(self, patched_claude_md):
        content = make_claude_md_with_pins([
            make_pin_entry(
                title="Override Pin",
                body_chars=4,
                date="2026-04-11",
                override_rationale="load-bearing verbatim form",
            ),
            make_pin_entry(title="Plain Pin", body_chars=4),
        ])
        patched_claude_md(content)
        rc, payload = _run_cli(["--status"])
        overrides = [p["override"] for p in payload["evictable_pins"]]
        assert overrides == [True, False]

    def test_evictable_heading_has_prefix_stripped(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(2))
        rc, payload = _run_cli(["--status"])
        for entry in payload["evictable_pins"]:
            assert not entry["heading"].startswith("### ")


def _days_ago(n):
    """A YYYY-MM-DD string exactly `n` days before now (UTC).

    CLI-level age tests derive their dates RELATIVE to now rather than
    hard-coding a calendar date. A hard-coded date drifts: a fixture pinned
    at a literal `2026-04-20` is 5 days old the week it is written and 400
    days old a year later, so any `overdue is False` assertion built on one
    is a date bomb that passes until it silently does not. Relative dates
    are time-invariant by construction — this pin is 60 days old whenever
    the suite runs.

    Unit-level tests inject an explicit clock instead (see
    TestPinAgeDays_Unit), which is stronger; this helper exists for the
    end-to-end path where the clock is not reachable from outside.
    """
    return (
        datetime.now(timezone.utc) - timedelta(days=n)
    ).strftime("%Y-%m-%d")


class TestPinAgeDays_Unit:
    """_pin_age_days — the D7 age computation, with an INJECTED clock.

    Every case here pins an exact expected integer against a frozen `now`,
    so these assertions are deterministic forever and cannot rot into a
    date bomb.
    """

    NOW = datetime(2026, 7, 25, tzinfo=timezone.utc)

    def test_canonical_date_comment_yields_age(self):
        import check_pin_caps
        age = check_pin_caps._pin_age_days(
            "<!-- pinned: 2026-05-26 -->", now=self.NOW
        )
        assert age == 60

    def test_reconfirmed_date_resets_the_clock(self):
        """The behavioural point of the re-confirmation grammar.

        Without the reset, re-confirming a pin would change nothing
        observable — the curator would re-attest a pin and it would stay
        flagged overdue, which makes the whole grammar decorative. The
        original `pinned:` date here is ~2.5 years old; the reconfirmation
        is 5 days old, and 5 is what must surface.
        """
        import check_pin_caps
        age = check_pin_caps._pin_age_days(
            "<!-- pinned: 2024-01-01, reconfirmed: 2026-07-20 because the "
            "parser-scoping answer is still the one people reach for -->",
            now=self.NOW,
        )
        assert age == 5

    def test_reconfirmed_reason_containing_a_date_does_not_confuse_it(self):
        """A free-text reason may itself mention a date; the reconfirmation
        date is the one adjacent to the `reconfirmed:` key, never the first
        date-shaped run in the comment."""
        import check_pin_caps
        age = check_pin_caps._pin_age_days(
            "<!-- pinned: 2024-01-01, reconfirmed: 2026-07-20 because it "
            "supersedes the 2020-01-01 rule -->",
            now=self.NOW,
        )
        assert age == 5

    def test_size_override_comment_still_yields_age(self):
        """The override form carries a trailing clause too; age must still
        parse out of it, or every oversized pin would report unknown."""
        import check_pin_caps
        age = check_pin_caps._pin_age_days(
            "<!-- pinned: 2026-05-26, pin-size-override: verbatim dispatch "
            "form is load-bearing -->",
            now=self.NOW,
        )
        assert age == 60

    @pytest.mark.parametrize("comment", [
        None,                              # no date comment at all
        "<!-- pinned: 2026-13-45 -->",     # well-formed shape, invalid date
        "<!-- pinned: sometime -->",       # no date-shaped run
        "",                                # empty
    ])
    def test_undatable_pin_yields_none_not_zero(self, comment):
        """Unknown is None, never 0. A 0 would render as a brand-new pin —
        the most-protected state — for a pin whose provenance we cannot
        establish at all."""
        import check_pin_caps
        assert check_pin_caps._pin_age_days(comment, now=self.NOW) is None

    def test_future_date_yields_negative_not_clamped(self):
        """A future-dated pin is a data anomaly and stays visible as one.
        Clamping to 0 would silently render it as freshly pinned."""
        import check_pin_caps
        age = check_pin_caps._pin_age_days(
            "<!-- pinned: 2026-08-25 -->", now=self.NOW
        )
        assert age == -31


class TestCheckPinCaps_AgeSignal_D7:
    """age_days / overdue on the evictable_pins surface (D7).

    Additive: `stale` and the marker machinery are untouched. These tests
    pin BOTH halves of that claim — that the new fields are correct, and
    that the old field's meaning did not move.
    """

    def test_threshold_boundary_is_inclusive(self, patched_claude_md):
        """overdue is `age >= PINNED_STALENESS_DAYS`. Pinning both sides of
        the boundary is what makes this non-vacuous: an off-by-one in
        either direction fails exactly one of these two assertions."""
        import check_pin_caps
        threshold = check_pin_caps.PINNED_STALENESS_DAYS
        patched_claude_md(make_claude_md_with_pins([
            make_pin_entry(title="At threshold", body_chars=10,
                           date=_days_ago(threshold)),
            make_pin_entry(title="One day under", body_chars=10,
                           date=_days_ago(threshold - 1)),
        ]))
        rc, payload = _run_cli(["--status"])
        assert rc == 0
        at, under = payload["evictable_pins"]
        assert at["age_days"] == threshold
        assert at["overdue"] is True
        assert under["age_days"] == threshold - 1
        assert under["overdue"] is False

    def test_undatable_pin_reports_unknown_not_not_overdue(
        self, patched_claude_md
    ):
        """THE load-bearing assertion of D7's three-state contract.

        A pin whose date cannot be established reports `overdue: null`, not
        `overdue: false`. False would silently EXEMPT the pin from review —
        it would be treated as fresh — and the pins we cannot date are
        exactly the ones most likely to be stale, because an undated pin is
        one nobody has maintained. Collapsing unknown into a clean verdict
        is the defect this whole feature exists to remove; here it would be
        shipped inside the signal built to fix it.
        """
        patched_claude_md(make_claude_md_with_pins([
            make_pin_entry(title="Undatable", body_chars=10,
                           date="not-a-date"),
        ]))
        rc, payload = _run_cli(["--status"])
        assert rc == 0
        entry = payload["evictable_pins"][0]
        assert entry["age_days"] is None
        assert entry["overdue"] is None
        assert entry["overdue"] is not False, (
            "unknown age must not render as not-overdue"
        )

    def test_old_pin_is_overdue_while_stale_stays_false(
        self, patched_claude_md
    ):
        """The two signals are independent, and this is D7's whole reason
        to exist.

        `stale` is MARKER-based: true only once staleness.py has written a
        `<!-- STALE: ... -->` marker into the body. `overdue` is AGE-based.
        A 60-day-old pin carrying no marker must report overdue=True and
        stale=False. If a future change ever made `overdue` merely mirror
        `stale`, this test goes red — which is the point, because a mirror
        would reintroduce the conflation the separate naming prevents.
        """
        patched_claude_md(make_claude_md_with_pins([
            make_pin_entry(title="Old but unmarked", body_chars=10,
                           date=_days_ago(60)),
        ]))
        rc, payload = _run_cli(["--status"])
        entry = payload["evictable_pins"][0]
        assert entry["age_days"] == 60
        assert entry["overdue"] is True
        assert entry["stale"] is False, (
            "stale must remain marker-based and independent of age"
        )

    def test_stale_marker_does_not_imply_overdue(self, patched_claude_md):
        """The converse independence: a RECENTLY-pinned entry carrying a
        stale marker reports stale=True but overdue=False. Together with
        the previous test this pins both diagonals, so neither field can
        collapse into the other without a red."""
        patched_claude_md(make_claude_md_with_pins([
            make_pin_entry(title="Marked but fresh", body_chars=10,
                           date=_days_ago(1), stale_date="2020-01-01"),
        ]))
        rc, payload = _run_cli(["--status"])
        entry = payload["evictable_pins"][0]
        assert entry["stale"] is True
        assert entry["overdue"] is False

    def test_reconfirmation_clears_overdue_end_to_end(
        self, patched_claude_md
    ):
        """The re-confirmation grammar, exercised through the real parser
        and the real CLI rather than only at unit level — an old pin that
        has been re-confirmed recently is no longer overdue."""
        content = make_claude_md_with_pins([
            make_pin_entry(title="Reconfirmed", body_chars=10,
                           date=_days_ago(400)),
        ]).replace(
            f"<!-- pinned: {_days_ago(400)} -->",
            f"<!-- pinned: {_days_ago(400)}, reconfirmed: {_days_ago(2)} "
            f"because still the first thing a new session needs -->",
        )
        patched_claude_md(content)
        rc, payload = _run_cli(["--status"])
        entry = payload["evictable_pins"][0]
        assert entry["age_days"] == 2, (
            "re-confirmation must reset the clock end-to-end, not just in "
            "the unit-level helper"
        )
        assert entry["overdue"] is False

    def test_age_fields_are_additive_not_replacing(self, patched_claude_md):
        """D7 adds columns; it removes and renames nothing. A consumer
        reading the pre-D7 shape must still find every field it had."""
        patched_claude_md(_make_pinned_content(2, pin_body_chars=50))
        rc, payload = _run_cli(["--status"])
        for entry in payload["evictable_pins"]:
            assert set(entry) == {
                "index", "heading", "chars", "stale", "override",
                "age_days", "overdue",
            }

    def test_threshold_is_not_a_second_constant(self):
        """PINNED_STALENESS_DAYS has ONE definition. This module re-exports
        staleness's value rather than declaring its own, so `overdue` and
        the SessionStart stale-block directive cannot drift to different
        thresholds. Pinning identity (not equality to a literal 30) is what
        makes this catch a divergence rather than a value change."""
        import check_pin_caps
        import staleness
        assert (
            check_pin_caps.PINNED_STALENESS_DAYS
            is staleness.PINNED_STALENESS_DAYS
        )


class TestCheckPinCaps_Advisory_NeverExit2:
    """SACROSANCT: exit code 2 is reserved; advisory CLI MUST never emit it.

    argparse `--help` uses SystemExit(0); argparse validation uses
    SystemExit(2) — both are argparse-internal and re-raised. Our CLI's
    own logic MUST never introduce a new exit-2 path (no enforcement
    failures, no fail-open fault converted to 2, no argparse-error from
    a retired flag).
    """

    def test_exit_never_2_on_status(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(3))
        rc, _ = _run_cli(["--status"])
        assert rc != 2

    def test_exit_never_2_on_fail_open(self, monkeypatch):
        import staleness
        import check_pin_caps
        monkeypatch.setattr(
            staleness, "get_project_claude_md_path", lambda errors=None: None
        )
        monkeypatch.setattr(
            check_pin_caps, "get_project_claude_md_path", lambda errors=None: None
        )
        rc, _ = _run_cli(["--status"])
        assert rc != 2

    def test_retired_flags_do_not_exit_2(self, patched_claude_md):
        """Retired cycle-7 flags (--new-body, --body-from-stdin,
        --has-override, --override-rationale) must not crash the CLI with
        argparse-exit-2 now that they're removed. The demoted CLI uses
        `parse_known_args` so unknown flags are silently ignored — stale
        /PACT:pin-memory callers won't DoS themselves while the pin-memory
        command is being rewritten in a sibling commit."""
        patched_claude_md(_make_pinned_content(3))
        for retired in [
            ["--new-body", "anything"],
            ["--body-from-stdin"],
            ["--has-override"],
            ["--override-rationale", "anything"],
        ]:
            rc, payload = _run_cli(retired)
            assert rc != 2, (
                f"retired flag {retired[0]} must not crash the CLI with "
                f"argparse-exit-2 — got rc={rc}, payload={payload}"
            )


class TestParsePins_MultilineRationale:
    """Parser-side defense against multi-line override rationales.

    This test covers hooks/pin_caps.py:parse_pins (not the CLI). Pre-
    cycle-8 it lived in a CLI-focused test class; moved here post-CLI-
    demotion because the CLI no longer owns rationale validation.
    Hook-side rationale validation (identical semantics) is in
    tests/test_pin_caps_gate.py.
    """

    def test_parser_rejects_multiline_rationale_cleanly(self):
        """parse_pins on a CLAUDE.md fragment with a multi-line override:
          (a) must not raise (fail-open by construction)
          (b) must NOT capture the broken rationale as a valid override
              (silent downgrade to no-override is the documented behavior)
        """
        import pin_caps
        pinned_content = (
            "<!-- pinned: 2026-04-21, pin-size-override: split\n"
            "across-lines -->\n"
            "### Broken Pin\n"
            "body content\n"
        )
        pins = pin_caps.parse_pins(pinned_content)
        assert len(pins) == 1
        assert pins[0].override_rationale is None, (
            "Multi-line rationale was captured as a valid override — "
            "parse_pins must not accept split-line rationale forms. "
            f"Got: {pins[0].override_rationale!r}"
        )
