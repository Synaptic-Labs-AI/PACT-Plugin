"""
Durable test suite for the pact_harvest CLI extraction (#1034/#927).

Covers the harvest-domain CLI (hooks/shared/pact_harvest.py) + the pure
resolve_latest_artifacts helper (hooks/shared/session_journal.py), which replace
the previously-untestable inline-Python glue the pact-handoff-harvest skill ran.
(This closes the #927 review's MINOR-2: the supersede was prose only.)

THREAT MODEL → TEST MAPPING (why each block exists):
  - The secretary runs the harvest OFF-LEAD, where pact_context.get_session_dir()
    / read_events() false-return '' → 0 events silently. The CLI exists to give
    the skill explicit-path, masked-read-safe entry points. So the load-bearing
    tests are: (a) the B1-CLASS DRIFT PARITY — resolve-session-dir on a
    special-char project_dir/session_id reconstructs the SANITIZED path the
    writer actually wrote to (NO drift); this is the exact bug class clean-
    basename masking hid in #927 and only cross-lane review caught. (b) The
    EXIT-CODE contract (0=proceed-incl-empty, 2=stop, never 1) — the skill keys
    its "report the gap and STOP, do not fall back to a path-less read" branch on
    the exit code, never on parsing stdout. (c) The IMPORT-SEAM — the direct-
    script sys.path bootstrap that makes `from shared.pact_context` resolve;
    without it the CLI is dead in script mode (pact_context has package-relative
    imports). (d) The ARRAY-PARSE contract the rewritten skill Steps 1/10 rely on.

FIDELITY SPLIT (lead-confirmed): TRUE direct-script subprocess for the CLI
contract / exit-code / empty-stdout / import-seam tests (those properties only
exist in script mode); in-process direct calls for the pure helper units.

B1-ORACLE DISCIPLINE (lead-confirmed, avoids the #927 wrong-oracle near-miss):
the expected sanitized path is DERIVED from the SSOT — reconstruct_session_dir
called directly — NOT hand-built. A hand-built path that under-sanitizes would
agree with a buggy CLI (vacuous green). A companion assertion proves a raw
un-sanitized join DIFFERS, so the special-char input actually triggers
sanitization (defeating clean-basename masking).

NON-VACUITY: documented per-test in the HANDOFF; spot-proven via source mutation
(flip an exit code / break supersede / remove the bootstrap → the matching test
goes RED). The B1 parity test's raw-join-differs companion is its in-suite
non-vacuity guard.

Run on the 3.13.7 interpreter (default python3 has no pytest):
    /Users/mj/.pyenv/versions/3.13.7/bin/python3 -m pytest \
        pact-plugin/tests/test_pact_harvest_cli.py -rA
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = Path(__file__).parent.parent / "hooks"
sys.path.insert(0, str(_HOOKS_DIR))

import shared.pact_context as pact_context  # noqa: E402
from shared.pact_harvest import _resolve_session_dir  # noqa: E402
from shared.session_journal import (  # noqa: E402
    append_event,
    make_event,
    resolve_latest_artifacts,
)

_CLI = _HOOKS_DIR / "shared" / "pact_harvest.py"
_PY = "/Users/mj/.pyenv/versions/3.13.7/bin/python3"

# Exit-code contract (must match pact_harvest.py).
_EXIT_OK = 0
_EXIT_INTERNAL_ERROR = 1
_EXIT_UNRESOLVED = 2


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    """Invoke the CLI as a TRUE direct-script subprocess (script mode — the
    only mode where the sys.path bootstrap, exit codes, and stdout contract
    actually exist). Uses the same interpreter the suite runs under."""
    return subprocess.run(
        [sys.executable, str(_CLI), *args],
        capture_output=True,
        text=True,
    )


def _art_event(workflow, feature, paths, ts, task_id=None):
    fields = {"workflow": workflow, "feature": feature, "paths": paths}
    if task_id is not None:
        fields["task_id"] = task_id
    ev = make_event("artifact_paths", **fields)
    ev["ts"] = ts
    return ev


# =============================================================================
# (1) PURE UNIT — resolve_latest_artifacts (in-process; no subprocess needed).
# =============================================================================
class TestResolveLatestArtifacts:
    FEATURE = "feat-x"

    def test_supersede_latest_ts_wins_per_workflow(self):
        events = [
            _art_event("prepare", self.FEATURE, ["/OLD.md"], "2026-06-25T01:00:00Z"),
            _art_event("prepare", self.FEATURE, ["/NEW.md"], "2026-06-25T03:00:00Z"),
            _art_event("prepare", self.FEATURE, ["/MID.md"], "2026-06-25T02:00:00Z"),
        ]
        assert resolve_latest_artifacts(events, self.FEATURE) == {"prepare": ["/NEW.md"]}

    def test_complete_path_list_never_merged_across_events(self):
        """Each event carries the COMPLETE list; supersede REPLACES, never
        unions. The latest event's 1-path list wins whole over a prior 2-path
        list — a merge bug would yield 3 paths."""
        events = [
            _art_event("prepare", self.FEATURE, ["/a.md", "/b.md"], "2026-06-25T01:00:00Z"),
            _art_event("prepare", self.FEATURE, ["/c.md"], "2026-06-25T02:00:00Z"),
        ]
        assert resolve_latest_artifacts(events, self.FEATURE) == {"prepare": ["/c.md"]}

    def test_distinct_workflows_both_kept(self):
        events = [
            _art_event("prepare", self.FEATURE, ["/p.md"], "2026-06-25T01:00:00Z"),
            _art_event("architect", self.FEATURE, ["/a.md"], "2026-06-25T01:00:00Z"),
        ]
        assert resolve_latest_artifacts(events, self.FEATURE) == {
            "prepare": ["/p.md"], "architect": ["/a.md"]}

    def test_wrong_feature_excluded(self):
        events = [
            _art_event("prepare", self.FEATURE, ["/mine.md"], "2026-06-25T01:00:00Z"),
            _art_event("prepare", "other-feat", ["/other.md"], "2026-06-25T02:00:00Z"),
        ]
        assert resolve_latest_artifacts(events, self.FEATURE) == {"prepare": ["/mine.md"]}

    def test_empty_events_returns_empty_dict(self):
        assert resolve_latest_artifacts([], self.FEATURE) == {}

    @pytest.mark.parametrize("bad", [
        [1, 2, 3], "a string", None, 42,
        {"feature": FEATURE, "paths": ["/x.md"], "ts": "2026-06-25T01:00:00Z"},  # no workflow
        {"workflow": "prepare", "feature": FEATURE, "ts": "2026-06-25T01:00:00Z"},  # no paths
        {"workflow": "prepare", "feature": FEATURE, "paths": "/x.md", "ts": "2026-06-25T01:00:00Z"},  # paths not list
    ])
    def test_malformed_events_skipped_defensively(self, bad):
        """Non-dict entries and dicts missing workflow/paths (or paths-not-list)
        are skipped — parity with the _read_events_at isinstance(dict) guard."""
        good = _art_event("prepare", self.FEATURE, ["/good.md"], "2026-06-25T05:00:00Z")
        assert resolve_latest_artifacts([bad, good], self.FEATURE) == {"prepare": ["/good.md"]}

    def test_bad_ts_never_supersedes_good(self):
        """A missing/unparseable ts is treated as older than any parseable ts,
        so a malformed later event cannot mask a well-formed one — regardless of
        insertion order (both orderings asserted)."""
        good = _art_event("prepare", self.FEATURE, ["/good.md"], "2026-06-25T02:00:00Z")
        bad = {"type": "artifact_paths", "workflow": "prepare",
               "feature": self.FEATURE, "paths": ["/bad.md"], "ts": "not-a-ts"}
        assert resolve_latest_artifacts([good, bad], self.FEATURE) == {"prepare": ["/good.md"]}
        assert resolve_latest_artifacts([bad, good], self.FEATURE) == {"prepare": ["/good.md"]}

    def test_missing_ts_never_supersedes_good(self):
        good = _art_event("prepare", self.FEATURE, ["/good.md"], "2026-06-25T02:00:00Z")
        no_ts = {"type": "artifact_paths", "workflow": "prepare",
                 "feature": self.FEATURE, "paths": ["/nots.md"]}
        assert resolve_latest_artifacts([good, no_ts], self.FEATURE) == {"prepare": ["/good.md"]}
        assert resolve_latest_artifacts([no_ts, good], self.FEATURE) == {"prepare": ["/good.md"]}

    def test_equal_ts_tie_break_is_last_wins(self):
        """Same-second double-emit: on a byte-IDENTICAL ts in the same
        (workflow, feature) group, the LAST-written event (iterated later in
        journal order) supersedes — `make_event` stamps ts at second
        granularity, so two emits of the same phase doc can collide and the
        authoritative one is the later complete snapshot. Both orderings are
        asserted so the result tracks journal order, not insertion luck: a
        first-wins (`>` instead of `>=`) regression flips BOTH and goes RED."""
        first = _art_event("prepare", self.FEATURE, ["/first.md"], "2026-06-25T01:00:00Z")
        second = _art_event("prepare", self.FEATURE, ["/second.md"], "2026-06-25T01:00:00Z")
        assert resolve_latest_artifacts([first, second], self.FEATURE) == {"prepare": ["/second.md"]}
        assert resolve_latest_artifacts([second, first], self.FEATURE) == {"prepare": ["/first.md"]}

    def test_parseable_naive_vs_aware_ts_does_not_crash(self):
        """A parseable-but-tz-NAIVE ts (date-only) alongside the normal aware
        ...Z ts must NOT crash the resolution. Both parse via _parse_ts but are
        incomparable (offset-naive vs offset-aware), which a strict
        `candidate > incumbent` OUTSIDE the parse-guard would let raise
        TypeError, escaping resolve_latest_artifacts. The fail-open guard keeps
        the incumbent instead. Regression-proof: reverting the comparison-guard
        makes this raise TypeError -> RED. Trigger requires a corrupted journal
        (make_event always stamps aware-Z); this pins graceful degradation."""
        aware = _art_event("prepare", self.FEATURE, ["/aware.md"], "2026-06-25T03:00:00Z")
        naive = {"type": "artifact_paths", "workflow": "prepare",
                 "feature": self.FEATURE, "paths": ["/naive.md"], "ts": "2026-06-25"}
        # Must not raise in either order; result is fail-open (incumbent kept).
        assert resolve_latest_artifacts([aware, naive], self.FEATURE) == {"prepare": ["/aware.md"]}
        assert resolve_latest_artifacts([naive, aware], self.FEATURE) == {"prepare": ["/naive.md"]}


# =============================================================================
# (2) SUBPROCESS — resolve-session-dir contract + THE B1-CLASS DRIFT PARITY.
# =============================================================================
class TestResolveSessionDirSubprocess:
    def _write_ctx(self, tmp_path, project_dir, session_id):
        ctx = tmp_path / "pact-session-context.json"
        ctx.write_text(json.dumps(
            {"project_dir": project_dir, "session_id": session_id}), encoding="utf-8")
        return ctx

    def test_valid_context_exit0_absolute_dir(self, tmp_path):
        ctx = self._write_ctx(tmp_path, "/clean/project", "sess-abcd")
        r = _run_cli("resolve-session-dir", "--context-file", str(ctx))
        assert r.returncode == _EXIT_OK
        out = r.stdout.strip()
        assert Path(out).is_absolute()
        # Oracle = the SSOT helper itself (NOT a hand-built path).
        assert out == pact_context.reconstruct_session_dir("/clean/project", "sess-abcd")

    def test_b1_class_drift_both_axes_sanitized_no_drift(self, tmp_path):
        """THE critical durability test (the exact bug class cross-lane review
        caught in #927). A project basename with a DOT and a session_id with a
        non-[A-Za-z0-9_-] char must reconstruct the SANITIZED path the WRITER
        wrote to — NO drift.

        ORACLE = reconstruct_session_dir called directly (the SSOT). Non-vacuity
        companion: a RAW un-sanitized join DIFFERS — proving the special-char
        input actually triggers sanitization on BOTH axes (slug + session_id),
        which is what clean-basename masking hid."""
        project_dir = "/Users/x/my.project dir"   # dot AND space in the basename
        session_id = "abc.def 123"                  # dot AND space (non-allowlist)
        ctx = self._write_ctx(tmp_path, project_dir, session_id)
        r = _run_cli("resolve-session-dir", "--context-file", str(ctx))
        assert r.returncode == _EXIT_OK
        out = r.stdout.strip()

        # (a) parity with the SSOT writer-derivation (the load-bearing assertion).
        expected = pact_context.reconstruct_session_dir(project_dir, session_id)
        assert out == expected, f"CLI {out!r} drifted from SSOT {expected!r}"

        # (b) NON-VACUITY: a raw un-sanitized join would land elsewhere — so the
        # special-char input genuinely exercises sanitization (not a no-op).
        from shared.paths import get_claude_config_dir
        raw = str(get_claude_config_dir() / "pact-sessions"
                  / Path(project_dir).name / session_id)
        assert out != raw, (
            "special-char input must be sanitized away from the raw join — if "
            "equal, the drift case is vacuous (no sanitization exercised)"
        )
        # And concretely: the dot/space are gone from BOTH path segments.
        assert "my_project_dir" in out and "abc_def_123" in out

    def test_nul_byte_path_is_bad_input_not_crash(self, capsys):
        """A context-file path with an embedded NUL byte raises ValueError from
        Path.read_text ('embedded null byte') — it must be handled as bad-input
        (exit 2 + empty stdout + stderr diagnostic), NOT an uncaught exit-1
        crash. Exercised IN-PROCESS by calling the handler directly: the CLI
        subprocess can't carry a NUL in argv (execve strips it), so the guard is
        unreachable via _run_cli; this is the only way to drive the path.
        Regression-proof: dropping ValueError from the read's except clause lets
        the ValueError escape the handler -> this raises instead of returning 2,
        going RED."""
        rc = _resolve_session_dir("/tmp/ctx\x00.json")
        assert rc == _EXIT_UNRESOLVED, "NUL path must be bad-input (exit 2), not a crash"
        captured = capsys.readouterr()
        assert captured.out == "", "exit-2 path must emit EMPTY stdout"
        assert "embedded null byte" in captured.err

    @pytest.mark.parametrize("scenario", ["missing_file", "bad_json", "not_object",
                                          "falsy_project", "falsy_session"])
    def test_unresolvable_exit2_empty_stdout(self, tmp_path, scenario):
        """Every bad-input path → exit 2 + EMPTY stdout (the skill's stop gate
        keys on the exit code; stdout must carry no data to mis-parse)."""
        if scenario == "missing_file":
            target = str(tmp_path / "does-not-exist.json")
        elif scenario == "bad_json":
            f = tmp_path / "bad.json"; f.write_text("{not json", encoding="utf-8")
            target = str(f)
        elif scenario == "not_object":
            f = tmp_path / "arr.json"; f.write_text("[1,2,3]", encoding="utf-8")
            target = str(f)
        elif scenario == "falsy_project":
            target = str(self._write_ctx(tmp_path, "", "sess-abcd"))
        else:  # falsy_session
            target = str(self._write_ctx(tmp_path, "/clean/project", ""))
        r = _run_cli("resolve-session-dir", "--context-file", target)
        assert r.returncode == _EXIT_UNRESOLVED, f"{scenario}: expected exit 2"
        assert r.stdout == "", f"{scenario}: stdout must be EMPTY on exit 2"

    def test_exit_code_is_2_never_1_on_bad_input(self, tmp_path):
        """Bad input is EXACTLY 2, never 1 (1 is reserved for internal errors).
        Pins the documented divergence from session_journal's exit-1 CLI."""
        r = _run_cli("resolve-session-dir", "--context-file",
                     str(tmp_path / "nope.json"))
        assert r.returncode == _EXIT_UNRESOLVED
        assert r.returncode != _EXIT_INTERNAL_ERROR


# =============================================================================
# (3) SUBPROCESS — resolve-artifacts contract.
# =============================================================================
class TestResolveArtifactsSubprocess:
    FEATURE = "feat-y"

    @pytest.fixture
    def session_with_events(self, tmp_path, monkeypatch, pact_context):
        """Build a REAL on-disk journal at a resolvable session dir so the CLI's
        read_events_from resolves it (non-mocked seam)."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name="t", session_id="sid-harvest", project_dir="/proj")
        slug = Path("/proj").name
        session_dir = tmp_path / ".claude" / "pact-sessions" / slug / "sid-harvest"
        return str(session_dir)

    def test_supersede_and_shape_one_line_json(self, session_with_events):
        sd = session_with_events
        append_event(_art_event("prepare", self.FEATURE, ["/OLD.md"], "2026-06-25T01:00:00Z"))
        append_event(_art_event("prepare", self.FEATURE, ["/NEW.md"], "2026-06-25T02:00:00Z"))
        append_event(_art_event("architect", self.FEATURE, ["/arch.md"], "2026-06-25T01:00:00Z"))
        r = _run_cli("resolve-artifacts", "--session-dir", sd, "--feature", self.FEATURE)
        assert r.returncode == _EXIT_OK
        # One-line compact JSON object.
        assert "\n" not in r.stdout.strip()
        assert json.loads(r.stdout) == {"prepare": ["/NEW.md"], "architect": ["/arch.md"]}

    def test_wrong_feature_excluded(self, session_with_events):
        sd = session_with_events
        append_event(_art_event("prepare", self.FEATURE, ["/mine.md"], "2026-06-25T01:00:00Z"))
        append_event(_art_event("prepare", "other", ["/other.md"], "2026-06-25T02:00:00Z"))
        r = _run_cli("resolve-artifacts", "--session-dir", sd, "--feature", self.FEATURE)
        assert r.returncode == _EXIT_OK
        assert json.loads(r.stdout) == {"prepare": ["/mine.md"]}

    def test_empty_result_is_empty_object_exit0(self, session_with_events):
        """A legitimately-empty result is {} at exit 0 (NOT a stop trigger)."""
        sd = session_with_events
        r = _run_cli("resolve-artifacts", "--session-dir", sd, "--feature", "no-such-feat")
        assert r.returncode == _EXIT_OK
        assert json.loads(r.stdout) == {}

    @pytest.mark.parametrize("bad_dir", ["", "relative/dir"])
    def test_bad_session_dir_exit2_empty_stdout(self, bad_dir):
        r = _run_cli("resolve-artifacts", "--session-dir", bad_dir, "--feature", self.FEATURE)
        assert r.returncode == _EXIT_UNRESOLVED
        assert r.stdout == ""


# =============================================================================
# (4) IMPORT-SEAM — the direct-script sys.path bootstrap resolves
#     shared.pact_context (the non-vacuous seam the bootstrap fix addresses).
# =============================================================================
class TestImportSeamBootstrap:
    def test_direct_script_resolves_pact_context_package_chain(self, tmp_path):
        """The CLI run as a direct script (cwd OUTSIDE the repo) must resolve
        `from shared.pact_context import reconstruct_session_dir` AND
        pact_context's own package-relative imports (from .session_state, etc).
        Proven end-to-end: resolve-session-dir produces a correct abs path, which
        is only possible if the bootstrap made `shared` a real package. If a
        future edit removes the sys.path bootstrap, this goes RED (ModuleNotFound
        or a non-zero exit), failing loudly."""
        ctx = tmp_path / "pact-session-context.json"
        ctx.write_text(json.dumps(
            {"project_dir": "/p", "session_id": "s"}), encoding="utf-8")
        # Run from an arbitrary cwd (tmp_path) to exercise script-mode sys.path.
        r = subprocess.run(
            [sys.executable, str(_CLI), "resolve-session-dir",
             "--context-file", str(ctx)],
            capture_output=True, text=True, cwd=str(tmp_path),
        )
        assert r.returncode == _EXIT_OK, (
            f"direct-script import seam broke (rc={r.returncode}); "
            f"stderr={r.stderr!r}"
        )
        assert r.stdout.strip() == pact_context.reconstruct_session_dir("/p", "s")
        assert "ModuleNotFoundError" not in r.stderr
        assert "ImportError" not in r.stderr


# =============================================================================
# (5) ARRAY-PARSE CONTRACT — session_journal's `read` CLI emits a JSON ARRAY
#     (the contract SKILL.md Steps 1/10 now parse). A future switch back to
#     JSONL would break the skill's reused read — this guards it.
# =============================================================================
class TestReadEmitsJsonArrayContract:
    def test_session_journal_read_cli_emits_json_array(self, tmp_path, monkeypatch, pact_context):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name="t", session_id="sid-arr", project_dir="/proj")
        slug = Path("/proj").name
        session_dir = str(tmp_path / ".claude" / "pact-sessions" / slug / "sid-arr")
        append_event(make_event(
            "agent_handoff", agent="devops-engineer", task_id="1",
            task_subject="x", handoff={"produced": "p", "decisions": "d",
            "uncertainty": "n", "integration": "n", "reasoning_chain": "r",
            "open_questions": "n"}))
        sj = str(_HOOKS_DIR / "shared" / "session_journal.py")
        r = subprocess.run(
            [sys.executable, sj, "read", "--session-dir", session_dir,
             "--type", "agent_handoff"],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, f"read failed: {r.stderr!r}"
        parsed = json.loads(r.stdout)
        assert isinstance(parsed, list), (
            "session_journal read MUST emit a JSON ARRAY (the contract Steps "
            "1/10 parse); a regression to JSONL would break the reused read"
        )
        assert len(parsed) == 1 and parsed[0]["type"] == "agent_handoff"


# =============================================================================
# (6) ARGPARSE CONTRACT — a missing or unknown subcommand must exit NON-ZERO
#     (argparse's 2), so the skill's `if ! out=$(...); then stop; fi` gate
#     still STOPS rather than falling through to a path-less read. `sub.required
#     = True` is what enforces this; a regression to required=False would let a
#     no-arg invocation exit 0 (return _EXIT_INTERNAL_ERROR is unreachable today
#     but a silent contract change) — this pins the stop-gate-compatible code.
# =============================================================================
class TestSubcommandArgparseContract:
    def test_no_subcommand_exits_nonzero_stop_gate(self):
        """No subcommand -> argparse error -> exit 2 (non-zero). The skill keys
        its stop branch on the exit code, so any non-zero is a stop; assert it
        is NOT 0 and concretely the argparse 2."""
        r = _run_cli()
        assert r.returncode != _EXIT_OK, "no-subcommand must not exit 0"
        assert r.returncode == 2, f"expected argparse exit 2, got {r.returncode}"

    def test_unknown_subcommand_exits_nonzero_stop_gate(self):
        """An invalid subcommand choice -> argparse error -> exit 2."""
        r = _run_cli("definitely-not-a-subcommand")
        assert r.returncode != _EXIT_OK, "unknown-subcommand must not exit 0"
        assert r.returncode == 2, f"expected argparse exit 2, got {r.returncode}"


# =============================================================================
# (7) TRAVERSAL DEFENSE + GRACEFUL-TS — through the real CLI.
#     (a) A traversal session_id ('../../etc/passwd') must be SANITIZED away,
#         leaving no '..' segment that could escape the pact-sessions tree.
#     (b) A corrupted journal with a parseable-but-naive ts must NOT crash
#         resolve-artifacts (exit 1 + traceback); it degrades gracefully.
# =============================================================================
class TestTraversalAndGracefulTs:
    def test_traversal_session_id_is_sanitized_no_escape(self, tmp_path):
        """A '../../etc/passwd' session_id reconstructs INSIDE pact-sessions
        with the traversal characters collapsed (no '..' segment, stays under
        the sessions root). Oracle = the SSOT reconstruct_session_dir; the
        independent non-vacuity check is the structural 'no ..' / 'under root'
        assertion (a sanitization regression would leave a '..' segment)."""
        ctx = tmp_path / "pact-session-context.json"
        ctx.write_text(json.dumps(
            {"project_dir": "/clean/project", "session_id": "../../etc/passwd"}),
            encoding="utf-8")
        r = _run_cli("resolve-session-dir", "--context-file", str(ctx))
        assert r.returncode == _EXIT_OK
        out = r.stdout.strip()
        # Parity with the SSOT writer-derivation (load-bearing).
        assert out == pact_context.reconstruct_session_dir(
            "/clean/project", "../../etc/passwd")
        # Non-vacuity: the traversal is neutralized — no '..' path segment, and
        # the resolved dir stays under the pact-sessions tree.
        parts = Path(out).parts
        assert ".." not in parts, f"traversal not sanitized: {out!r}"
        assert "pact-sessions" in parts
        sessions_idx = parts.index("pact-sessions")
        # Everything after pact-sessions is the sanitized slug + session_id —
        # no segment escapes upward.
        assert all(p != ".." for p in parts[sessions_idx:])

    def test_resolve_artifacts_naive_ts_does_not_crash_via_cli(
        self, tmp_path, monkeypatch, pact_context):
        """End-to-end: a corrupted journal carrying one aware-Z and one
        parseable-but-naive ts for the same (workflow, feature) must NOT crash
        the CLI. Before the comparison-guard fix this exited 1 with a TypeError
        traceback; now it exits 0 and emits a valid JSON object (the incumbent
        survives, fail-open). Regression-proof: reverting the guard makes this
        exit 1 -> RED."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name="t", session_id="sid-naive", project_dir="/proj")
        slug = Path("/proj").name
        session_dir = tmp_path / ".claude" / "pact-sessions" / slug / "sid-naive"
        feature = "feat-naive"
        append_event(_art_event("prepare", feature, ["/aware.md"], "2026-06-25T03:00:00Z"))
        # A hand-corrupted naive (date-only) ts — only reachable via a mangled
        # journal, since make_event always stamps aware-Z.
        append_event({"v": 1, "type": "artifact_paths", "workflow": "prepare",
                      "feature": feature, "paths": ["/naive.md"], "ts": "2026-06-25"})
        r = _run_cli("resolve-artifacts", "--session-dir", str(session_dir),
                     "--feature", feature)
        assert r.returncode == _EXIT_OK, (
            f"naive-vs-aware ts must degrade gracefully, not crash; "
            f"rc={r.returncode} stderr={r.stderr!r}"
        )
        assert "TypeError" not in r.stderr
        # A valid JSON object is emitted; the well-formed aware event survives.
        assert json.loads(r.stdout) == {"prepare": ["/aware.md"]}
