"""
Location: pact-plugin/tests/test_live_probe_gate_dogfood.py
Summary: C2 dogfood — NON-MOCKED integration coverage for live_probe_gate (the
locus-b advisory). The gate is its OWN first probe subject. Two layers here:

  WARN-PATH (real temp git repo, no mock): a `gh pr merge` on a branch that
  touches hooks/ with NO satisfied RUNBOOK row for the current plugin version
  emits the non-blocking WARN; a satisfied both-mode PASS row -> silent. Drives
  main() end-to-end through the REAL git diff + REAL plugin.json + REAL RUNBOOK
  reads (the freshness seam), asserting stderr WARN + exit 0.

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

GENUINE_PASS_ROW = (
    "| header |\n"
    f"| 2026-06-08 | michael-wojcik | {VERSION} | tmux 2/2, in-process 2/2 | "
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
        # Both "PASS" and "PASSED" are genuine verdicts -> satisfy (devops's
        # shipped behavior). If the lead rules "PASSED" must be rejected, devops
        # drops `(?:ED)?` and this flips to PASSED-not-satisfying.
        for verdict in ("both modes PASS.", "both modes PASSED"):
            row = (f"| 2026-06-08 | op | {VERSION} | tmux 2/2, in-process 2/2 | "
                   f"sids | {verdict} |\n")
            root = _make_root(tmp_path, "| h |\n" + row)
            assert g._has_satisfied_row(root, VERSION, waiver_ok=False) is True, (
                f"genuine verdict {verdict!r} must satisfy the gate"
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
        ("tmux PASS 2/2 · in-process PASS 2/2", True),
        ("tmux PASSED 2/2 · in-process PASSED 2/2", True),     # PASSED variant
        ("tmux FAIL 0/2 · in-process PASS 2/2", False),        # THE FIX — dangerous direction
        ("tmux PASS 2/2 · in-process FAIL 0/2", False),
        ("tmux FAIL 0/2 · in-process FAIL 0/2", False),
        ("tmux PASS/FAIL · in-process PASS/FAIL", False),      # unfilled placeholder
    ])
    def test_per_mode_verdict(self, tmp_path, verdict, satisfies):
        root = _make_root(tmp_path, "| h |\n" + _per_mode_row(verdict))
        assert g._has_satisfied_row(root, VERSION, waiver_ok=False) is satisfies, (
            f"per-mode verdict {verdict!r} expected satisfies={satisfies}"
        )

    def test_legacy_aggregate_pass_row_still_satisfies(self, tmp_path):
        # OLDER/aggregate shape (923/926 — no per-mode PASS/FAIL token in the
        # verdict cell): preserved on the LEGACY token-presence path (tmux +
        # in-process tokens + a genuine PASS anywhere in the row).
        legacy = (f"| 2026-06-02 | op | {VERSION} | tmux 3/3, in-process 3/3 | "
                  f"n/a | both modes PASS. |\n")
        root = _make_root(tmp_path, "| h |\n" + legacy)
        assert g._has_satisfied_row(root, VERSION, waiver_ok=False) is True, (
            "an older aggregate-PASS row (no per-mode verdict cell) must still "
            "satisfy via the preserved legacy path"
        )

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
    err = capsys.readouterr().err
    return code, err


class TestDogfoodWarnPath:
    def test_warns_on_hooks_diff_with_no_satisfied_row(self, tmp_path, monkeypatch, capsys):
        root = _make_git_repo(tmp_path, "| h |\n", touch_hooks=True)  # no row
        code, err = _run_main(root, "gh pr merge 999 --squash", monkeypatch, capsys)
        assert code == 0, "advisory must always exit 0 (WARN-not-BLOCK)"
        assert "live-probe-gate" in err and VERSION in err, (
            "a hooks/-touching merge with no satisfied row must WARN; got "
            f"stderr={err!r}"
        )

    def test_silent_when_genuine_pass_row_exists(self, tmp_path, monkeypatch, capsys):
        root = _make_git_repo(tmp_path, "| h |\n" + GENUINE_PASS_ROW, touch_hooks=True)
        code, err = _run_main(root, "gh pr merge 999 --squash", monkeypatch, capsys)
        assert code == 0
        assert "live-probe-gate" not in err, "a fresh both-mode PASS row -> silent"

    def test_silent_on_non_hook_diff(self, tmp_path, monkeypatch, capsys):
        root = _make_git_repo(tmp_path, "| h |\n", touch_hooks=False)  # docs only
        code, err = _run_main(root, "gh pr merge 999 --squash", monkeypatch, capsys)
        assert code == 0
        assert "live-probe-gate" not in err, "a non-hooks PR must not WARN"

    def test_silent_on_non_merge_command(self, tmp_path, monkeypatch, capsys):
        root = _make_git_repo(tmp_path, "| h |\n", touch_hooks=True)
        code, err = _run_main(root, "git status", monkeypatch, capsys)
        assert code == 0
        assert "live-probe-gate" not in err, "non merge/close command -> not our concern"

    def test_checks_actual_not_claimed_coverage(self, tmp_path, monkeypatch, capsys):
        # The dogfood gate keys on the ACTUAL hooks/ diff + ACTUAL RUNBOOK row,
        # never a claimed/asserted coverage flag: a real hooks/ change with no
        # row WARNs even though the suite is green. (Green tests != probed.)
        root = _make_git_repo(tmp_path, "| h |\n", touch_hooks=True)
        _, err = _run_main(root, "gh pr close 999", monkeypatch, capsys)
        assert "live-probe-gate" in err

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
        forged = _make_root(tmp_path / "forged_config", "| h |\n" + GENUINE_PASS_ROW)  # satisfied
        # NON-VACUITY precondition: the forged RUNBOOK IS genuinely satisfying, so
        # the gate ignoring it (below) is the real assertion — not the forged row
        # happening to be empty/unsatisfied.
        assert g._has_satisfied_row(forged, VERSION, waiver_ok=False) is True
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(forged))
        code, err = _run_main(root, "gh pr merge 1 --squash", monkeypatch, capsys)
        assert code == 0, "advisory must always exit 0 (WARN-not-BLOCK)"
        assert "live-probe-gate" in err, (
            "the gate must key off CLAUDE_PROJECT_DIR (repo root) and IGNORE "
            "CLAUDE_CONFIG_DIR — a forged satisfied RUNBOOK under a non-default "
            "CLAUDE_CONFIG_DIR must NOT false-satisfy the gate (security #55 regression)"
        )
