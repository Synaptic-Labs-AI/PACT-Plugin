"""
Location: pact-plugin/tests/test_live_probe_gate_dogfood.py
Summary: C2 dogfood — NON-MOCKED integration coverage for live_probe_gate (the
locus-b advisory). The gate is its OWN first probe subject. Two layers here:

  WARN-PATH (real temp git repo, no mock): a `gh pr merge` on a branch that
  touches hooks/ with NO satisfied RUNBOOK row for the current plugin version
  emits the non-blocking WARN; a satisfied both-mode PASS row -> silent. Drives
  main() end-to-end through the REAL git diff + REAL plugin.json + REAL RUNBOOK
  reads (the freshness seam), asserting the WARN surfaces via the stdout
  hookSpecificOutput.additionalContext channel + exit 0.

  FRESHNESS-SPEC (real temp files): _has_satisfied_row must accept a GENUINE
  both-mode PASS row and REJECT (a) a substring-`pass`-but-not-genuine token
  (`bypass`, `non-genuine-pass`) and (b) an UNFILLED pending "PASS/FAIL"
  template row. Both false-satisfy cases would silence the gate before any real
  probe = a "checked & clear" false signal, recursively the inert class the gate
  exists to prevent.

The REAL both-mode (tmux + in-process) platform firing of the 4.4.13 probe is
POST-MERGE by nature (the hook fires at `gh pr merge`); the prepped procedure is
tests/runbooks/924-locus-b-dogfood-probe.md. This pytest layer certifies the
DECISION LOGIC checks ACTUAL coverage (a real hooks/ diff with no row WARNs),
never claimed coverage.

NOTE (commit-sequencing): the FRESHNESS-SPEC substring/pending cases are RED
against an as-built `"pass" in low` substring matcher and GREEN once the
_has_satisfied_row token-precise fix lands. They encode the SPEC, not the bug.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import live_probe_gate as g  # noqa: E402

VERSION = "4.4.13"


def _make_root(tmp: Path, runbook_body: str, version: str = VERSION) -> Path:
    """Build a real PACT-plugin-shaped root: plugin.json marker + RUNBOOK."""
    pj = tmp / "pact-plugin" / ".claude-plugin"
    pj.mkdir(parents=True, exist_ok=True)
    (pj / "plugin.json").write_text(
        json.dumps({"name": "pact-plugin", "version": version}), encoding="utf-8")
    rb = tmp / "pact-plugin" / "tests" / "runbooks"
    rb.mkdir(parents=True, exist_ok=True)
    (rb / "RUNBOOK_RUN_DATES.md").write_text(runbook_body, encoding="utf-8")
    return tmp


# ── FRESHNESS-SPEC: _has_satisfied_row against real RUNBOOK files ──

# Canonical GENUINE satisfied row = the PRODUCTION #924 per-mode template: the
# verdict cell (cells[3]) carries the per-mode counted PASS. (Pre-#66 this put
# PASS in the Notes cell; #66's F-B verdict-cell-scope correctly stopped honoring
# a Notes-only PASS, so the genuine row must carry PASS in the verdict cell.)
GENUINE_PASS_ROW = (
    "| header |\n"
    f"| 2026-06-08 | michael-wojcik | {VERSION} | tmux PASS 2/2 · in-process PASS 2/2 | "
    "sids observed | both modes PASS. journal ev ts recorded. |\n"
)
BYPASS_ROW = f"| 2026-06-08 | op | {VERSION} | tmux | in-process | bypass |\n"
NONGENUINE_ROW = f"| 2026-06-08 | op | {VERSION} | tmux | in-process | non-genuine-pass |\n"
PENDING_TEMPLATE_ROW = (
    f"| _pending_ | | {VERSION} | /2 | tmux + in-process | "
    "arm (§a) PASS/FAIL · forensic (§b) PASS/FAIL |\n"
)
FAIL_ROW = f"| 2026-06-08 | op | {VERSION} | tmux 0/2 | in-process | FAIL |\n"


class TestFreshnessRowSpec:
    def test_genuine_both_mode_pass_row_satisfies(self, tmp_path):
        root = _make_root(tmp_path, "| h |\n" + GENUINE_PASS_ROW)
        assert g._has_satisfied_row(root, VERSION, waiver_ok=False) is True

    def test_fail_row_does_not_satisfy(self, tmp_path):
        root = _make_root(tmp_path, "| h |\n" + FAIL_ROW)
        assert g._has_satisfied_row(root, VERSION, waiver_ok=False) is False

    def test_no_row_does_not_satisfy(self, tmp_path):
        root = _make_root(tmp_path, "| h |\n")
        assert g._has_satisfied_row(root, VERSION, waiver_ok=False) is False

    # SPEC-FIRST (RED until the token-precise _has_satisfied_row fix lands):
    def test_substring_bypass_must_not_false_satisfy(self, tmp_path):
        root = _make_root(tmp_path, "| h |\n" + BYPASS_ROW)
        assert g._has_satisfied_row(root, VERSION, waiver_ok=False) is False, (
            "'bypass' contains the substring 'pass' but is NOT a genuine PASS "
            "verdict — must not satisfy the freshness gate"
        )

    def test_substring_nongenuine_pass_must_not_false_satisfy(self, tmp_path):
        root = _make_root(tmp_path, "| h |\n" + NONGENUINE_ROW)
        assert g._has_satisfied_row(root, VERSION, waiver_ok=False) is False

    def test_unfilled_pending_template_must_not_false_satisfy(self, tmp_path):
        # The worst case: an unfilled "PASS/FAIL" placeholder row would silence
        # the gate BEFORE any real probe runs.
        root = _make_root(tmp_path, "| h |\n" + PENDING_TEMPLATE_ROW)
        assert g._has_satisfied_row(root, VERSION, waiver_ok=False) is False, (
            "an unfilled PASS/FAIL pending template row must NOT satisfy the "
            "gate — that is a 'checked & clear' false signal before any probe"
        )

    def test_waiver_row_satisfies_only_when_waiver_ok(self, tmp_path):
        waiver = f"| 2026-06-08 | op | {VERSION} | WAIVED | n/a | hooks/-only, no seam change |\n"
        root = _make_root(tmp_path, "| h |\n" + waiver)
        assert g._has_satisfied_row(root, VERSION, waiver_ok=True) is True
        assert g._has_satisfied_row(root, VERSION, waiver_ok=False) is False

    # FIX (review cycle 1, finding #2 hardening) — COLUMN-ANCHOR:
    def test_different_version_row_mentioning_v_in_notes_not_satisfied(self, tmp_path):
        # A DIFFERENT-version both-mode PASS row whose NOTES cell merely MENTIONS
        # the current version V must NOT satisfy — the version match is anchored
        # to the "Plugin version" cell (3rd column), not anywhere-in-line. (Pre-
        # hardening this false-satisfied via the version mention in prose.)
        row = ("| 2026-06-08 | op | 4.1.3 | tmux 2/2, in-process 2/2 | sids | "
               f"both modes PASS — backport note referencing {VERSION} |\n")
        root = _make_root(tmp_path, "| h |\n" + row)
        assert g._has_satisfied_row(root, VERSION, waiver_ok=False) is False, (
            "a row with a DIFFERENT version in the Plugin-version column must NOT "
            "satisfy just because it mentions V in its Notes prose (column-anchor)"
        )

    # FIX (review cycle 1, finding #2 hardening) — EXACT-PASS verdict.
    # NOTE: devops's committed regex `(?<![A-Za-z])PASS(?:ED)?(?![A-Za-z/])`
    # DELIBERATELY accepts the genuine verdicts "PASS" AND "PASSED" (operators
    # write either), while rejecting the non-genuine / case-variant / placeholder
    # forms below. (The lead's #2 spec listed "PASSED" as a non-satisfier; that
    # conflicts with devops's shipped `(?:ED)?` — surfaced to the lead/devops for
    # a ruling. This test pins the UNAMBIGUOUS rejections that hold under EITHER
    # interpretation so it is correct against the shipped code today.)
    def test_non_genuine_pass_verdicts_do_not_satisfy(self, tmp_path):
        for verdict in ("both modes Passed",        # mixed-case (not exact)
                        "both modes passed",         # lowercase (not exact)
                        "tmux bypass, in-process x",  # substring 'pass' in 'bypass'
                        "both modes BYPASSED",        # lookbehind: 'PASS' preceded by 'Y'
                        "both modes non-genuine-pass",
                        "arm (§a) PASS/FAIL · forensic PASS/FAIL"):  # unfilled template
            row = (f"| 2026-06-08 | op | {VERSION} | tmux 2/2, in-process 2/2 | "
                   f"sids | {verdict} |\n")
            root = _make_root(tmp_path, "| h |\n" + row)
            assert g._has_satisfied_row(root, VERSION, waiver_ok=False) is False, (
                f"verdict {verdict!r} must NOT satisfy the gate"
            )

    def test_genuine_pass_and_passed_verdicts_satisfy(self, tmp_path):
        # Both "PASS" and "PASSED" (counted, per-mode, in the VERDICT cell) are
        # genuine verdicts -> satisfy. The PASS(?:ED)? tolerance is settled.
        for verdict_cell in ("tmux PASS 2/2 · in-process PASS 2/2",
                             "tmux PASSED 2/2 · in-process PASSED 2/2"):
            row = f"| 2026-06-08 | op | {VERSION} | {verdict_cell} | sids | both modes |\n"
            root = _make_root(tmp_path, "| h |\n" + row)
            assert g._has_satisfied_row(root, VERSION, waiver_ok=False) is True, (
                f"genuine verdict cell {verdict_cell!r} must satisfy the gate"
            )


def _per_mode_row(verdict_cell: str, version: str = VERSION) -> str:
    """A #924-template per-mode row: the VERDICT cell is the 4th column (cells[3])
    and reads 'tmux PASS|FAIL N/N · in-process PASS|FAIL N/N'."""
    return f"| 2026-06-08 | op | {version} | {verdict_cell} | sids observed | per-mode probe |\n"


class TestPerModePassParsing:
    """FINDING #1 (#57, pairs with devops #56): the freshness check parses the
    per-mode verdict cell and satisfies IFF BOTH tmux AND in-process are a genuine
    PASS — closing the false-satisfy where a per-mode FAIL rode alongside a PASS.
    Contract per devops #56: per-mode shape detected by `(?:tmux|in-process)\\s+
    (?:PASS|FAIL)` in cells[3]; both `tmux PASS(?:ED)?` and `in-process PASS(?:ED)?`
    must hit. Aggregate/older shape (no per-mode token in cells[3]) keeps the
    legacy token-presence path."""

    @pytest.mark.parametrize("verdict, satisfies", [
        # --- real per-mode verdicts (satisfy IFF both modes a counted PASS) ---
        ("tmux PASS 2/2 · in-process PASS 2/2", True),
        ("tmux PASSED 2/2 · in-process PASSED 2/2", True),     # PASSED variant
        # --- round-2 partial-mode guard (one mode FAIL → not satisfy) ---
        ("tmux FAIL 0/2 · in-process PASS 2/2", False),        # THE round-2 FIX — dangerous direction
        ("tmux PASS 2/2 · in-process FAIL 0/2", False),
        ("tmux PASS 2/2 · in-process FAIL 1/2", False),        # in-process FAIL w/ count
        ("tmux FAIL 0/2 · in-process FAIL 0/2", False),
        # --- SEPARATOR-PLACEHOLDER class (#62 trailing-count hardening): a
        # verdict with NO count after PASS is an unfilled/template placeholder,
        # never a real probe → must NOT satisfy. The count requirement
        # (`PASS(?:ED)?\s+\d`) is what closes the architect-found separator edges.
        ("tmux PASS/FAIL · in-process PASS/FAIL", False),      # slash placeholder
        ("tmux PASS, FAIL · in-process PASS, FAIL", False),    # COMMA — architect edge
        ("tmux PASS FAIL · in-process PASS FAIL", False),      # SPACE separator
        ("tmux PASS|FAIL · in-process PASS|FAIL", False),      # PIPE (count req + markdown cell-split)
        ("tmux PASS · in-process PASS", False),                # bare — NO count at all
        # --- F-A (#66 count-VALUE): a counted PASS satisfies IFF num==denom>0
        # (a complete run). An incomplete/zero/vacuous count is not a real probe.
        ("tmux PASS 4/4 · in-process PASS 4/4", True),         # complete, larger count
        ("tmux PASS 0/2 · in-process PASS 0/2", False),        # zero numerator
        ("tmux PASS 1/2 · in-process PASS 1/2", False),        # partial (num<denom)
        ("tmux PASS 0/0 · in-process PASS 0/0", False),        # vacuous zero denominator
        ("tmux PASS 2/2 · in-process PASS 1/2", False),        # one mode incomplete
        # --- F-A residual count-bypass (#70 re-verify of the count parser):
        # malformed/incomplete counts reject; a complete N/N (any magnitude,
        # leading zeros normalized) accepts.
        ("tmux PASS 3/2 · in-process PASS 3/2", False),        # num>denom (impossible)
        ("tmux PASS 2/2/2 · in-process PASS 2/2/2", False),    # triple-segment count
        ("tmux PASS 02/2 · in-process PASS 02/2", True),       # leading zero, 02==2 complete
        ("tmux PASS 999999/999999 · in-process PASS 999999/999999", True),  # huge complete
    ])
    def test_per_mode_verdict(self, tmp_path, verdict, satisfies):
        root = _make_root(tmp_path, "| h |\n" + _per_mode_row(verdict))
        assert g._has_satisfied_row(root, VERSION, waiver_ok=False) is satisfies, (
            f"per-mode verdict {verdict!r} expected satisfies={satisfies}"
        )

    # NOTE: the legacy single-mode-row "still satisfies" case moved to
    # TestCrossRowAggregation — under #70 (Option-A) a LONE single-mode row no
    # longer satisfies on its own (the other mode is ABSENT). The legacy
    # verdict-cell shape ("tmux 6/6 — PASS") now participates in CROSS-ROW
    # aggregation (the 923 two-row case there), not per-row.

    def test_per_mode_column_anchor_preserved(self, tmp_path):
        # A DIFFERENT-version per-mode both-PASS row that mentions V in its Notes
        # cell must NOT satisfy (column-anchor on cells[2] still holds post-#56).
        row = (f"| 2026-06-08 | op | 4.1.3 | tmux PASS 2/2 · in-process PASS 2/2 | "
               f"sids | per-mode for {VERSION} backport |\n")
        root = _make_root(tmp_path, "| h |\n" + row)
        assert g._has_satisfied_row(root, VERSION, waiver_ok=False) is False, (
            "per-mode row with a DIFFERENT version in cells[2] must not satisfy "
            "just because Notes mention V (column-anchor preserved)"
        )

    def test_per_mode_waived_still_gated_on_waiver_ok(self, tmp_path):
        waiver = f"| 2026-06-08 | op | {VERSION} | WAIVED | n/a | hooks/-only, no seam change |\n"
        root = _make_root(tmp_path, "| h |\n" + waiver)
        assert g._has_satisfied_row(root, VERSION, waiver_ok=True) is True
        assert g._has_satisfied_row(root, VERSION, waiver_ok=False) is False


class TestCrossRowAggregation:
    """FINDING F-C (#71, pairs devops #70 Option-A): a version satisfies by
    CROSS-ROW AGGREGATION — scan ALL rows for version V, build a per-mode status
    (tmux, in-process) from each row's VERDICT CELL (cells[3]) merging worst-wins
    (FAIL > PASS > DEFERRED > absent), and SATISFY iff BOTH modes ∈ {PASS,
    DEFERRED} AND ≥1 is a genuine PASS AND neither is FAIL. This closes F-C: a
    LONE single-mode row (e.g. only tmux) no longer satisfies on its own — the
    other mode is ABSENT. Preserves the real 923 (two separate single-mode rows)
    and 926 (in-process PASS + tmux _deferred) shapes. DEFERRED requires the
    verdict cell == 'n/a' AND the row LABEL (cells[0]) naming THAT mode as
    '_deferred — <mode> …' (FAIL ≠ deferred; ABSENT ≠ deferred). Uses the REAL
    merged-in 923/926 RUNBOOK row shapes as live cases."""

    @staticmethod
    def _row(version, verdict_cell, notes="x", date="2026-06-09"):
        return f"| {date} | michael-wojcik | {version} | {verdict_cell} | n/a | {notes} |\n"

    @staticmethod
    def _deferred(version, mode):
        # 926-style deferral row: the LABEL cell (cells[0]) names the deferred
        # mode; the verdict cell (cells[3]) is "n/a".
        return (f"| _deferred — {mode} mode under non-default CLAUDE_CONFIG_DIR | | "
                f"{version} | n/a | n/a | the {mode} live-probe is deferred |\n")

    # ---- SATISFY via aggregation ----
    def test_923_two_separate_rows_both_pass_aggregate_satisfies(self, tmp_path):
        # Real 923 @4.4.12 (L48 + L49): tmux PASS row + in-process PASS row, as
        # two SEPARATE single-mode rows. Aggregate: both modes PASS → satisfy.
        body = ("| h |\n"
                + self._row("4.4.12", "tmux 6/6 — PASS (real platform surface confirmed)")
                + self._row("4.4.12", "in-process 6/6 — PASS (real hook, real resolver)"))
        root = _make_root(tmp_path, body, version="4.4.12")
        assert g._has_satisfied_row(root, "4.4.12", waiver_ok=False) is True, (
            "923 two separate single-mode PASS rows must AGGREGATE to satisfy"
        )

    def test_926_in_process_pass_plus_tmux_deferred_aggregate_satisfies(self, tmp_path):
        # Real 926 @4.4.13 (L131 + L132): in-process PASS row + tmux _deferred
        # row. Aggregate: in-process PASS + tmux DEFERRED → both ∈{PASS,DEFERRED},
        # ≥1 PASS, no FAIL → satisfy.
        body = ("| h |\n"
                + self._row("4.4.13", "in-process 4/4 — PASS")
                + self._deferred("4.4.13", "tmux"))
        root = _make_root(tmp_path, body, version="4.4.13")
        assert g._has_satisfied_row(root, "4.4.13", waiver_ok=False) is True, (
            "926 in-process PASS + tmux DEFERRED must aggregate to satisfy"
        )

    # ---- F-C: lone single-mode row does NOT satisfy (the key new close) ----
    def test_lone_tmux_pass_no_in_process_row_does_not_satisfy(self, tmp_path):
        # THE F-C CLOSE: a lone "tmux … — PASS" row with NO in-process row →
        # in-process ABSENT → NOT satisfy. (Pre-#70 this satisfied per-row.)
        root = _make_root(
            tmp_path,
            "| h |\n" + self._row("4.4.12", "tmux 6/6 — PASS (real platform surface confirmed)"),
            version="4.4.12")
        assert g._has_satisfied_row(root, "4.4.12", waiver_ok=False) is False, (
            "a lone tmux PASS row (in-process ABSENT) must NOT satisfy under "
            "cross-row aggregation — the F-C close"
        )

    def test_lone_in_process_pass_no_tmux_row_does_not_satisfy(self, tmp_path):
        # Symmetric F-C: lone in-process PASS, NO tmux row (and no tmux deferral).
        root = _make_root(
            tmp_path, "| h |\n" + self._row("4.4.13", "in-process 4/4 — PASS"),
            version="4.4.13")
        assert g._has_satisfied_row(root, "4.4.13", waiver_ok=False) is False, (
            "a lone in-process PASS row (tmux ABSENT) must NOT satisfy"
        )

    # ---- aggregate rejects ----
    def test_one_mode_fail_aggregate_does_not_satisfy(self, tmp_path):
        # tmux FAIL row + in-process PASS row → a FAIL anywhere → NOT satisfy.
        body = ("| h |\n"
                + self._row("4.4.13", "tmux 0/2 — FAIL")
                + self._row("4.4.13", "in-process 4/4 — PASS"))
        root = _make_root(tmp_path, body, version="4.4.13")
        assert g._has_satisfied_row(root, "4.4.13", waiver_ok=False) is False, (
            "no mode may be FAIL — tmux FAIL + in-process PASS must NOT satisfy"
        )

    def test_deferred_alone_no_pass_does_not_satisfy(self, tmp_path):
        # tmux DEFERRED with NO in-process PASS row → no genuine PASS present →
        # NOT satisfy (≥1 PASS required). Also the F-B point: the deferred row's
        # Notes mention "4/4 PASS" but Notes are NOT scanned.
        body = "| h |\n" + self._deferred("4.4.13", "tmux")
        root = _make_root(tmp_path, body, version="4.4.13")
        assert g._has_satisfied_row(root, "4.4.13", waiver_ok=False) is False, (
            "a lone DEFERRED mode with no genuine PASS must NOT satisfy "
            "(≥1 PASS required; Notes-PASS is ignored)"
        )

    def test_fail_verdict_cell_with_pass_in_notes_does_not_satisfy(self, tmp_path):
        # F-B stays closed: the verdict cell is a FAIL; PASS appears only in Notes.
        body = "| h |\n" + self._row(
            "4.4.13", "in-process 4/4 — FAIL", "rerun later; an earlier 4/4 PASS was noted")
        root = _make_root(tmp_path, body, version="4.4.13")
        assert g._has_satisfied_row(root, "4.4.13", waiver_ok=False) is False, (
            "a FAIL verdict cell must NOT satisfy even when PASS appears in Notes"
        )


# ── WARN-PATH: drive main() end-to-end in a real temp git repo (no mock) ──

def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def _make_git_repo(tmp: Path, runbook_body: str, touch_hooks: bool) -> Path:
    """Real git repo: main branch with the plugin shape, then a feature branch
    that (optionally) modifies a hooks/ file. Returns the worktree root."""
    _make_root(tmp, runbook_body)
    _git(tmp, "init", "-q")
    _git(tmp, "config", "user.email", "t@t.t")
    _git(tmp, "config", "user.name", "t")
    _git(tmp, "checkout", "-q", "-b", "main")
    (tmp / "pact-plugin" / "hooks").mkdir(parents=True, exist_ok=True)
    (tmp / "pact-plugin" / "hooks" / "some_hook.py").write_text("x = 1\n")
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-q", "-m", "base")
    _git(tmp, "checkout", "-q", "-b", "feature")
    if touch_hooks:
        (tmp / "pact-plugin" / "hooks" / "some_hook.py").write_text("x = 2\n")
    else:
        (tmp / "README.md").write_text("docs only\n")
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-q", "-m", "change")
    return tmp


def _run_main(root: Path, command: str, monkeypatch, capsys) -> tuple[int, str]:
    import io
    monkeypatch.chdir(root)
    # Pin CLAUDE_PROJECT_DIR to THIS test's temp root so _resolve_repo_root is
    # deterministic and IMMUNE to a leaked value from an upstream test (some
    # tests set os.environ["CLAUDE_PROJECT_DIR"] without cleanup). A bare delenv
    # is NOT leak-immune: if the var is re-set after our delenv, or if we relied
    # on the git-rev-parse fallback, an upstream leak could redirect repo-root
    # resolution away from our temp repo and silently suppress the WARN.
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.setattr(sys, "stdin",
                        io.StringIO(json.dumps({"tool_input": {"command": command}})))
    code = 0
    try:
        g.main()
    except SystemExit as e:
        code = int(e.code or 0)
    # The WARN advisory surfaces via STDOUT hookSpecificOutput.additionalContext
    # (not stderr — a PreToolUse exit-0 stderr line is not fed to the agent). The
    # silent path prints only {"suppressOutput": true} to stdout, which carries
    # no "live-probe-gate" marker, so a marker-substring check stays correct in
    # both directions.
    out = capsys.readouterr().out
    return code, out


class TestDogfoodWarnPath:
    def test_warns_on_hooks_diff_with_no_satisfied_row(self, tmp_path, monkeypatch, capsys):
        root = _make_git_repo(tmp_path, "| h |\n", touch_hooks=True)  # no row
        code, out = _run_main(root, "gh pr merge 999 --squash", monkeypatch, capsys)
        assert code == 0, "advisory must always exit 0 (WARN-not-BLOCK)"
        assert "live-probe-gate" in out and VERSION in out, (
            "a hooks/-touching merge with no satisfied row must WARN; got "
            f"stdout={out!r}"
        )

    def test_silent_when_genuine_pass_row_exists(self, tmp_path, monkeypatch, capsys):
        root = _make_git_repo(tmp_path, "| h |\n" + GENUINE_PASS_ROW, touch_hooks=True)
        code, out = _run_main(root, "gh pr merge 999 --squash", monkeypatch, capsys)
        assert code == 0
        assert "live-probe-gate" not in out, "a fresh both-mode PASS row -> silent"

    def test_silent_on_non_hook_diff(self, tmp_path, monkeypatch, capsys):
        root = _make_git_repo(tmp_path, "| h |\n", touch_hooks=False)  # docs only
        code, out = _run_main(root, "gh pr merge 999 --squash", monkeypatch, capsys)
        assert code == 0
        assert "live-probe-gate" not in out, "a non-hooks PR must not WARN"

    def test_silent_on_non_merge_command(self, tmp_path, monkeypatch, capsys):
        root = _make_git_repo(tmp_path, "| h |\n", touch_hooks=True)
        code, out = _run_main(root, "git status", monkeypatch, capsys)
        assert code == 0
        assert "live-probe-gate" not in out, "non merge/close command -> not our concern"

    def test_checks_actual_not_claimed_coverage(self, tmp_path, monkeypatch, capsys):
        # The dogfood gate keys on the ACTUAL hooks/ diff + ACTUAL RUNBOOK row,
        # never a claimed/asserted coverage flag: a real hooks/ change with no
        # row WARNs even though the suite is green. (Green tests != probed.)
        root = _make_git_repo(tmp_path, "| h |\n", touch_hooks=True)
        _, out = _run_main(root, "gh pr close 999", monkeypatch, capsys)
        assert "live-probe-gate" in out

    def test_config_dir_independence_forged_runbook_does_not_false_satisfy(
            self, tmp_path, monkeypatch, capsys):
        # #57 / security #55 regression guard: the gate keys off CLAUDE_PROJECT_DIR
        # (the dev-repo root, via _resolve_repo_root) and IGNORES CLAUDE_CONFIG_DIR
        # (~/.claude relocation — the #926 surface). Forge a SATISFIED RUNBOOK +
        # fake plugin.json under a CLAUDE_CONFIG_DIR temp dir; the REAL repo
        # (CLAUDE_PROJECT_DIR) has a hooks/-touching diff + NO satisfied row. The
        # gate must STILL WARN — a forged satisfied RUNBOOK under a non-default
        # CLAUDE_CONFIG_DIR must NOT false-satisfy. If a future change makes the
        # gate read CLAUDE_CONFIG_DIR, the forged row would silence it -> FAIL here.
        root = _make_git_repo(tmp_path / "repo", "| h |\n", touch_hooks=True)  # no satisfied row
        # Forge a row that satisfies via the STRICT PER-MODE (production #924
        # template) path — "tmux PASS 2/2 · in-process PASS 2/2" — NOT the legacy
        # token-presence path (security #61 alignment: exercise the real
        # production satisfy-path so the guard reflects how a real satisfied
        # RUNBOOK is recognized).
        forged = _make_root(
            tmp_path / "forged_config",
            "| h |\n" + _per_mode_row("tmux PASS 2/2 · in-process PASS 2/2"),
        )
        # NON-VACUITY precondition: the forged RUNBOOK GENUINELY satisfies the
        # (strict per-mode) parser, so the gate ignoring it (below) is the real
        # assertion — not the forged row happening to be empty/unsatisfied.
        assert g._has_satisfied_row(forged, VERSION, waiver_ok=False) is True
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(forged))
        code, out = _run_main(root, "gh pr merge 1 --squash", monkeypatch, capsys)
        assert code == 0, "advisory must always exit 0 (WARN-not-BLOCK)"
        assert "live-probe-gate" in out, (
            "the gate must key off CLAUDE_PROJECT_DIR (repo root) and IGNORE "
            "CLAUDE_CONFIG_DIR — a forged satisfied RUNBOOK under a non-default "
            "CLAUDE_CONFIG_DIR must NOT false-satisfy the gate (security #55 regression)"
        )

    def test_waiver_row_silences_end_to_end_on_non_seam_hooks_diff(
            self, tmp_path, monkeypatch, capsys):
        # WAIVER PATH end-to-end through main(): a hooks/-only diff that touches
        # NO seam hook classifies waiver_required=True, so main() calls
        # _has_satisfied_row(..., waiver_ok=True) and an auditable WAIVED row
        # satisfies -> silent. Previously the waiver_ok=True branch was covered
        # only at the _has_satisfied_row UNIT level (TestFreshnessRowSpec /
        # TestPerModePassParsing), never driven through main(). `some_hook.py`
        # (what _make_git_repo touches) is NOT a seam hook -> waiver_required=True.
        waived = f"| 2026-06-08 | op | {VERSION} | WAIVED | n/a | hooks/-only, no seam change |\n"
        root = _make_git_repo(tmp_path, "| h |\n" + waived, touch_hooks=True)

        # Sanity-pin the precondition this test depends on: the touched diff is a
        # non-seam hooks/ change (waiver_required=True). If a future fixture makes
        # some_hook.py a seam hook, waiver_ok would be False and this test would
        # silently change meaning -> assert it explicitly.
        from shared.hook_infra_classifier import classify_diff  # noqa: E402
        assert classify_diff(["pact-plugin/hooks/some_hook.py"]).waiver_required is True, (
            "precondition: the touched hooks/ file must be a NON-seam change "
            "(waiver_required=True) — else this does not exercise the waiver path"
        )

        # NON-VACUITY: spy that main() reached the freshness check AND passed
        # waiver_ok=True (the waiver branch), not waiver_ok=False. A silent exit
        # alone would not prove the WAIVED row is what silenced the gate.
        real = g._has_satisfied_row
        seen: list[bool] = []

        def _spy(root_arg, version, waiver_ok):
            seen.append(waiver_ok)
            return real(root_arg, version, waiver_ok=waiver_ok)

        monkeypatch.setattr(g, "_has_satisfied_row", _spy)
        code, out = _run_main(root, "gh pr merge 999 --squash", monkeypatch, capsys)
        assert code == 0
        assert seen == [True], (
            "main() must reach _has_satisfied_row with waiver_ok=True for a "
            f"non-seam hooks/ diff (the waiver branch); got calls={seen!r}"
        )
        assert "live-probe-gate" not in out, (
            "an auditable WAIVED row must satisfy the gate on a non-seam hooks/ "
            f"diff (waiver_ok=True) -> silent; got {out!r}"
        )
        # CONTRAST (attributes the silence to the waiver branch): the SAME WAIVED
        # row does NOT satisfy when waiver_ok=False — so the end-to-end silence
        # above is the waiver path, not a row that would satisfy regardless.
        assert real(root, VERSION, waiver_ok=False) is False, (
            "the WAIVED row must NOT satisfy under waiver_ok=False — proving the "
            "e2e silence is specifically the waiver_required=True branch"
        )


# ── FINDING A: _resolve_repo_root must validate the marker at CLAUDE_PROJECT_DIR ──

class TestResolveRepoRootMarkerGuard:
    """The CLAUDE_PROJECT_DIR resolver matrix. A teammate's CLAUDE_PROJECT_DIR is
    its cwd = the `pact-plugin` SUBDIR, where the marker would double to
    `.../pact-plugin/pact-plugin/.claude-plugin/plugin.json` and be absent. The
    resolver must trust CLAUDE_PROJECT_DIR ONLY when the marker resolves at that
    root, else fall through to the git-common-dir-parent path (the true root)."""

    def _git_repo_root(self, tmp: Path) -> Path:
        """A real git repo whose root carries the plugin marker, so the
        git-common-dir-parent fallback resolves to it."""
        root = _make_root(tmp, "| h |\n")
        _git(root, "init", "-q")
        _git(root, "config", "user.email", "t@t.t")
        _git(root, "config", "user.name", "t")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "base")
        return root

    def test_subdir_project_dir_falls_through_to_repo_root(
            self, tmp_path, monkeypatch):
        # CLAUDE_PROJECT_DIR = the pact-plugin SUBDIR (marker absent there) ->
        # the resolver must NOT trust it; it falls through to the git-common-dir
        # parent = the repo root (marker FOUND). This is the Finding-A regression.
        root = self._git_repo_root(tmp_path)
        subdir = root / "pact-plugin"
        assert g._plugin_marker(subdir) is None, (
            "precondition: the marker must NOT resolve at the subdir (the bug's "
            "double-pact-plugin path) — else this test is vacuous"
        )
        monkeypatch.chdir(root)  # so git-common-dir resolves to this repo
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(subdir))
        resolved = g._resolve_repo_root()
        assert resolved is not None and g._plugin_marker(resolved) is not None, (
            "a subdir CLAUDE_PROJECT_DIR must fall through to a root where the "
            f"marker resolves; got {resolved!r} (marker "
            f"{g._plugin_marker(resolved) if resolved else None!r})"
        )
        assert resolved.resolve() == root.resolve(), (
            "the fall-through must land on the true repo root"
        )

    def test_repo_root_project_dir_is_trusted_unchanged(
            self, tmp_path, monkeypatch):
        # CLAUDE_PROJECT_DIR = the repo root (marker FOUND) -> trusted unchanged
        # (the canonical lead-driven-merge case must NOT regress).
        root = _make_root(tmp_path, "| h |\n")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
        resolved = g._resolve_repo_root()
        assert resolved == Path(str(root)), (
            "a repo-root CLAUDE_PROJECT_DIR (marker FOUND) must be returned as-is"
        )

    def test_unset_project_dir_uses_git_common_dir_parent(
            self, tmp_path, monkeypatch):
        # CLAUDE_PROJECT_DIR unset -> the guarded branch is skipped, behavior
        # unchanged: git-common-dir parent = the repo root.
        root = self._git_repo_root(tmp_path)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.chdir(root)
        resolved = g._resolve_repo_root()
        assert resolved is not None and resolved.resolve() == root.resolve(), (
            "unset CLAUDE_PROJECT_DIR must resolve to the git-common-dir parent "
            f"(repo root); got {resolved!r}"
        )


# ── FINDING B: _emit_warn surfaces via stdout hookSpecificOutput.additionalContext ──

class TestEmitWarnSurfacingShape:
    """The advisory must surface on the channel an agent operator actually sees:
    a PreToolUse exit-0 stdout `hookSpecificOutput.additionalContext` (mirrors
    the verified sibling task_claim_gate.py). exit-0 stderr is NOT fed to the
    agent, so the prior stderr advisory was invisible (#924 dogfood Finding B)."""

    def _run_emit(self, capsys, version="4.4.13",
                  seam=frozenset({"session_init.py"})):
        code = 0
        try:
            g._emit_warn(version, seam)
        except SystemExit as e:
            code = int(e.code or 0)
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    def test_emits_hookspecificoutput_additionalcontext_on_stdout(self, capsys):
        code, out, err = self._run_emit(capsys)
        assert code == 0, "advisory must exit 0 (WARN-not-BLOCK)"
        payload = json.loads(out)  # must be valid JSON on stdout
        hso = payload["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse", (
            "hookEventName MUST be the literal 'PreToolUse' (platform invariant)"
        )
        ctx = hso["additionalContext"]
        assert "live-probe-gate" in ctx and "4.4.13" in ctx, (
            "the advisory prose (marker + version) must ride additionalContext"
        )
        assert "session_init.py" in ctx, "seam-hooks must appear in the advisory"

    def test_no_suppressoutput_and_nothing_on_stderr(self, capsys):
        # suppressOutput would hide stdout and defeat additionalContext surfacing;
        # it must NOT be co-emitted. And the advisory must no longer go to stderr
        # (the channel that is invisible to the agent on exit 0).
        _, out, err = self._run_emit(capsys)
        payload = json.loads(out)
        assert "suppressOutput" not in payload, (
            "suppressOutput must NOT be co-emitted — it suppresses the stdout "
            "additionalContext the agent needs to see"
        )
        assert "live-probe-gate" not in err, (
            "the advisory must surface via stdout, not stderr (exit-0 stderr is "
            "not fed to the agent)"
        )

    def test_unknown_version_renders_placeholder(self, capsys):
        # Defensive: an empty version must not crash and must render the
        # '<unknown>' placeholder in the surfaced advisory.
        _, out, _ = self._run_emit(capsys, version="")
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert "<unknown>" in ctx

    def test_advisory_prose_is_byte_unchanged_by_the_channel_swap(self, capsys):
        # GREEN-TREE DRIFT-DETECTOR (not a correctness-derivation, and not a
        # counter-test-by-revert assertion): on the fixed tree this pins the EXACT
        # surfaced additionalContext text against a hand-typed literal of the
        # pre-swap f-string, so a FUTURE edit that mangles the operator-facing
        # wording (while keeping the channel) fails here. NOTE on its revert
        # behavior: under a source-only revert of the fix the advisory goes to
        # stderr and stdout carries no hookSpecificOutput, so this test flips RED
        # at the `json.loads(out)["hookSpecificOutput"]` EXTRACTION (KeyError) —
        # BEFORE the `ctx == expected` byte-compare ever runs. So its non-vacuity-
        # under-revert proves only that the CHANNEL moved; the byte-equality check
        # itself is a standing guard exercised on the green tree, where it catches
        # prose drift. The expected string is hand-typed (NOT re-derived from the
        # SUT's f-string at runtime), so it is not tautological.
        _, out, _ = self._run_emit(
            capsys, version="4.4.13", seam=frozenset({"session_init.py"}))
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        expected = (
            "[live-probe-gate] This branch touches the hooks/ tree but no fresh "
            "both-modes live-probe row exists in RUNBOOK_RUN_DATES.md for version "
            "4.4.13. Per the hook-infra gate, log a live-probe "
            "(tmux mandatory; in-process real-or-faithful-synthetic) — or an "
            "auditable WAIVED row for a non-seam (hooks/-only) change — before "
            "closing the originating issue. Seam hooks touched: session_init.py. "
            "(Advisory — WARN only.)"
        )
        assert ctx == expected, (
            "the additionalContext prose must be BYTE-IDENTICAL to the pre-swap "
            "stderr advisory (Finding B moved only the channel, never the text); "
            f"got {ctx!r}"
        )

    def test_multiple_seam_hooks_are_sorted_and_joined(self, capsys):
        # The seam list is `", ".join(sorted(...))` — verify the deterministic
        # sorted rendering (not set-iteration order) so the advisory is stable.
        _, out, _ = self._run_emit(
            capsys, seam=frozenset({"session_init.py", "task_claim_gate.py"}))
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert "Seam hooks touched: session_init.py, task_claim_gate.py." in ctx, (
            "seam hooks must render sorted + comma-joined (deterministic order)"
        )

    def test_primary_only_renders_none_placeholder(self, capsys):
        # An empty seam set (PRIMARY-only change, no seam hook touched) renders
        # the "(none — PRIMARY only)" placeholder, not an empty fragment.
        _, out, _ = self._run_emit(capsys, seam=frozenset())
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert "Seam hooks touched: (none — PRIMARY only)." in ctx


class TestSilentPathSuppressOutputNegativeControl:
    """NEGATIVE CONTROL for Finding B: the WARN path drops suppressOutput so its
    additionalContext surfaces — but the SILENT path (the fail-safe / satisfied
    exit) MUST still emit `{"suppressOutput": true}`. If a future edit dropped
    suppressOutput from the silent path too, the gate would leak an empty
    {} stdout on every non-firing command (the dominant case) — noise the
    suppressOutput contract exists to prevent. This asserts the asymmetry that
    Finding B deliberately introduced: suppressOutput on silent, NOT on WARN."""

    def test_silent_allow_emits_suppressoutput_true(self, capsys):
        code = 0
        try:
            g._silent_allow()
        except SystemExit as e:
            code = int(e.code or 0)
        out = capsys.readouterr().out
        assert code == 0, "silent-allow must exit 0"
        payload = json.loads(out)
        assert payload == {"suppressOutput": True}, (
            "the silent path must emit exactly {'suppressOutput': true} (the "
            f"non-firing-command contract); got {payload!r}"
        )

    def test_warn_and_silent_suppressoutput_asymmetry(self, capsys):
        # The load-bearing asymmetry in ONE assertion: WARN must NOT carry
        # suppressOutput (it would hide additionalContext); SILENT MUST carry it.
        try:
            g._silent_allow()
        except SystemExit:
            pass
        silent = json.loads(capsys.readouterr().out)
        try:
            g._emit_warn("4.4.13", frozenset({"session_init.py"}))
        except SystemExit:
            pass
        warn = json.loads(capsys.readouterr().out)
        assert silent.get("suppressOutput") is True and "suppressOutput" not in warn, (
            "asymmetry violated: silent must suppress stdout, WARN must NOT "
            f"(silent={silent!r}, warn-keys={list(warn)!r})"
        )


# ── FINDING A: resolver-matrix EDGE cases (beyond the devops 3-case matrix) ──

class TestResolveRepoRootMatrixEdges:
    """Edge cases on the _resolve_repo_root marker-guard matrix that the devops
    verification set (subdir-falls-through / repo-root-trusted / unset) does NOT
    cover: (1) a SUBDIR whose marker IS present must be TRUSTED (the guard must
    not over-trigger and reject a legitimately-marker-bearing CLAUDE_PROJECT_DIR);
    (2) the THIRD-tier cwd fallback when CLAUDE_PROJECT_DIR's marker is absent AND
    git-common-dir is also unresolvable. Together with the devops 3 these pin all
    four reachable resolver outcomes."""

    def test_marker_bearing_project_dir_is_trusted_even_if_named_like_a_subdir(
            self, tmp_path, monkeypatch):
        # The guard trusts CLAUDE_PROJECT_DIR IFF the marker resolves there — it
        # keys on marker PRESENCE, not on the path's name. A directory that
        # happens to sit one level down but DOES carry the plugin marker must be
        # returned as-is (no spurious fall-through). Builds the marker AT a nested
        # dir and points CLAUDE_PROJECT_DIR at it.
        nested = tmp_path / "checkouts" / "a"
        _make_root(nested, "| h |\n")  # plants the marker AT `nested`
        assert g._plugin_marker(nested) is not None, (
            "precondition: the marker must resolve at the nested dir — else this "
            "test does not exercise the trust-when-present branch"
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(nested))
        # cwd is elsewhere (no git): if the guard wrongly fell through, it would
        # NOT land on `nested`. Trusting the marker-bearing CPD is the assertion.
        monkeypatch.chdir(tmp_path)
        resolved = g._resolve_repo_root()
        assert resolved == Path(str(nested)), (
            "a marker-bearing CLAUDE_PROJECT_DIR must be trusted as-is regardless "
            f"of its path depth; got {resolved!r}"
        )

    def test_cwd_fallback_when_marker_absent_and_git_unresolvable(
            self, tmp_path, monkeypatch):
        # THIRD-TIER fallback: CLAUDE_PROJECT_DIR set to a marker-LESS subdir
        # (guard skips it) AND git-common-dir unresolvable -> Path.cwd(). Force
        # the git failure DETERMINISTICALLY by monkeypatching subprocess.run to
        # raise FileNotFoundError (git-not-found), rather than relying on ambient
        # filesystem git state (a no-.git tmp dir can still discover an ambient
        # parent .git and is flaky across environments). The cwd is a marker-less
        # temp dir, so the resolver returns it (the documented last-resort tier).
        subdir = tmp_path / "pact-plugin"  # marker would double here -> absent
        subdir.mkdir(parents=True, exist_ok=True)
        assert g._plugin_marker(subdir) is None, (
            "precondition: marker absent at the subdir -> guard must skip it"
        )

        def _git_unavailable(*args, **kwargs):
            raise FileNotFoundError("git not found (simulated)")

        monkeypatch.setattr(g.subprocess, "run", _git_unavailable)
        cwd = tmp_path / "elsewhere"
        cwd.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(cwd)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(subdir))
        resolved = g._resolve_repo_root()
        assert resolved == Path(cwd), (
            "marker-absent CPD + git unresolvable must fall to Path.cwd() (the "
            f"third resolver tier); got {resolved!r}"
        )
        # And the gate fail-safes silent at this cwd (no marker there) — the
        # resolver's last tier feeds main()'s marker check, which silent-allows.
        assert g._plugin_marker(resolved) is None, (
            "the cwd fallback lands on a marker-less dir here, so main() will "
            "silent-allow — the documented fail-safe, not a WARN"
        )


# ── FINDING A: END-TO-END — main() reconnects under a SUBDIR CLAUDE_PROJECT_DIR ──

class TestFindingASubdirEndToEnd:
    """The HIGHEST-VALUE Finding-A test: drive main() END-TO-END with
    CLAUDE_PROJECT_DIR = the `pact-plugin` SUBDIR (the exact teammate topology
    #932 documents) and assert the gate FIRES (WARNs) instead of silently
    self-disabling. The resolver unit tests prove the address math; THIS proves
    the gate actually reconnects to the live RUNBOOK/diff seam through the fixed
    resolver — the regression #932 reported was a SILENT self-disable visible
    only at the main() level, not at the resolver in isolation.

    NON-VACUITY: under the pre-fix unconditional `return Path(project_dir)`, the
    subdir CPD resolves to the (marker-less) subdir, main()'s marker check is
    None -> silent-allow -> NO WARN. So these tests FLIP red on the pre-fix tree
    (counter-test-by-revert of 402c5e3f confirms the cardinality)."""

    def _run_main_with_project_dir(self, root, project_dir, command,
                                   monkeypatch, capsys):
        """Like _run_main but pins CLAUDE_PROJECT_DIR to an ARBITRARY dir (here
        the subdir) rather than the repo root, so we exercise the Finding-A
        fall-through. cwd is the repo root so git-common-dir resolves to it."""
        import io
        monkeypatch.chdir(root)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
        monkeypatch.setattr(sys, "stdin", io.StringIO(
            json.dumps({"tool_input": {"command": command}})))
        code = 0
        try:
            g.main()
        except SystemExit as e:
            code = int(e.code or 0)
        return code, capsys.readouterr().out

    def test_subdir_project_dir_main_still_warns(
            self, tmp_path, monkeypatch, capsys):
        # The Finding-A regression repro: a hooks/-touching merge from a process
        # whose CLAUDE_PROJECT_DIR is the pact-plugin subdir, with NO satisfied
        # row, MUST still WARN (the gate must reconnect via git-common-dir).
        root = _make_git_repo(tmp_path, "| h |\n", touch_hooks=True)  # no row
        subdir = root / "pact-plugin"
        assert g._plugin_marker(subdir) is None, (
            "precondition: marker absent at the subdir CPD — else the fall-"
            "through that THIS test exercises never engages (vacuous)"
        )
        code, out = self._run_main_with_project_dir(
            root, subdir, "gh pr merge 999 --squash", monkeypatch, capsys)
        assert code == 0, "advisory must always exit 0 (WARN-not-BLOCK)"
        assert "live-probe-gate" in out and VERSION in out, (
            "a hooks/-touching merge under a SUBDIR CLAUDE_PROJECT_DIR must WARN "
            "(Finding A: the gate reconnects via git-common-dir instead of "
            f"silently self-disabling); got stdout={out!r}"
        )

    def test_subdir_project_dir_silent_via_satisfied_row_at_resolved_root(
            self, tmp_path, monkeypatch, capsys):
        # ISOLATES the fall-through (not merely the no-WARN output): under a
        # subdir CPD with a GENUINE satisfied row, the gate must be silenced
        # BECAUSE the fall-through reached the REAL repo-root RUNBOOK and read
        # the row there — NOT because the gate fail-safed silent at the marker
        # check. A bare "live-probe-gate not in out" assertion CANNOT tell those
        # apart (both produce no WARN); pre-fix the subdir CPD resolves to the
        # marker-less subdir, main() silent-allows at the marker==None check, and
        # _has_satisfied_row is NEVER reached. So we SPY _has_satisfied_row and
        # assert (a) it WAS called (the gate reached the freshness branch = the
        # fall-through resolved a marker-bearing root and passed marker/diff/
        # primary) and (b) it was called with the resolved REPO ROOT, not the
        # subdir. The spy makes this NON-VACUOUS: pre-fix the call never happens,
        # so assertion (a) flips RED on the unfixed tree.
        root = _make_git_repo(
            tmp_path, "| h |\n" + GENUINE_PASS_ROW, touch_hooks=True)
        subdir = root / "pact-plugin"

        real_has_satisfied_row = g._has_satisfied_row
        seen_roots: list[Path] = []

        def _spy(root_arg, version, waiver_ok):
            seen_roots.append(root_arg)
            return real_has_satisfied_row(root_arg, version, waiver_ok=waiver_ok)

        monkeypatch.setattr(g, "_has_satisfied_row", _spy)
        code, out = self._run_main_with_project_dir(
            root, subdir, "gh pr merge 999 --squash", monkeypatch, capsys)
        assert code == 0
        # (a) the freshness check was actually reached (fall-through engaged) —
        # this is the non-vacuous part: pre-fix the gate silent-allows before it.
        assert seen_roots, (
            "the fall-through must reach _has_satisfied_row (pre-fix it never "
            "does — the subdir resolves marker-less and the gate silent-allows "
            "at the marker check); an empty call list means the gate self-"
            "disabled, not that the row silenced it"
        )
        # (b) it was called with the resolved REPO ROOT (where the RUNBOOK lives),
        # not the subdir CPD — proving the fall-through landed on the true root.
        assert seen_roots[0].resolve() == root.resolve(), (
            "_has_satisfied_row must be called with the resolved repo ROOT, not "
            f"the subdir; got {seen_roots[0]!r} (subdir was {subdir!r})"
        )
        # and the genuine row at that root silences the gate (no over-fire).
        assert "live-probe-gate" not in out, (
            "a fresh both-mode PASS row at the resolved repo root must silence "
            f"the gate; got {out!r}"
        )
