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
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
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
