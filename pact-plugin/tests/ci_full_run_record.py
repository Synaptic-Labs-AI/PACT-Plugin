"""In-band record of the certifying full-suite run.

Location: pact-plugin/tests/ci_full_run_record.py

Summary: A pytest plugin object that writes the counts, summary line and
terminal node IDs of THIS run to ``${GITHUB_WORKSPACE}/.pytest-run-record.json``
(or the repository root when GITHUB_WORKSPACE is unset), so
``scripts/verify_full_run.py`` can check the run that certifies a merge rather
than a separate collection of it.

Used by: pact-plugin/conftest.py, registered in ``pytest_configure``.

GATED ON GITHUB_ACTIONS, and written ONLY under GITHUB_WORKSPACE. There is
no repository-root fallback: a stray artifact in the working tree is worse
than a probe that sets two environment variables. To probe a local run:
``GITHUB_ACTIONS=true GITHUB_WORKSPACE=/tmp/<rig> python -m pytest ...``.
A run with GITHUB_ACTIONS unset writes nothing; a gated run with no
workspace prints a loud diagnostic and writes nothing (the verifier then
fails on the absent record, which is the fail-closed direction).

WHY THE TERMINAL SUMMARY IS THE SOURCE. ``deselected`` comes from the
terminal reporter's stats, never ``len(session.items)``: deselection shrinks
the item list before tests run, so the item list cannot see it. The summary
line is built from the same reporter's ``build_summary_stats_line`` so the
record carries the line pytest would have printed.

WRITTEN EVEN WHEN THE SESSION FAILED. A record that exists only on success
cannot evidence a failure. The write is atomic (temp + ``os.replace``) so the
verifier never reads a partial JSON document.
"""

import datetime
import json
import os
import sys
from pathlib import Path

SCHEMA_VERSION = 1
RECORD_NAME = ".pytest-run-record.json"


def _gated_repo_root():
    """The checkout root, or None when this is not a gated CI run.

    The marker is the repository itself, matching the other in-band plugins:
    a nested pytest run in a temp rig has no marker above it and writes
    nothing.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return None
    for base in Path(__file__).resolve().parents:
        if (base / ".claude-plugin" / "marketplace.json").exists():
            return base
    return None


def record_path():
    """The verifier's only path: under GITHUB_WORKSPACE, or None.

    No repository-root fallback: the record must never appear in a working
    tree by accident.
    """
    workspace = os.environ.get("GITHUB_WORKSPACE", "").strip()
    if not workspace:
        return None
    return Path(workspace) / RECORD_NAME


def _format_duration(seconds):
    """Match pytest's own summary duration rendering."""
    if seconds < 60:
        return f"{seconds:.2f}s"
    delta = datetime.timedelta(seconds=int(seconds))
    return f"{seconds:.2f}s ({delta})"


class FullRunRecord:
    """Session-start to session-end recorder of the certifying run."""

    def __init__(self):
        self._root = _gated_repo_root()
        self._terminal_node_ids = []
        self._seen_node_ids = set()

    def pytest_runtest_logreport(self, report):
        """Record every node ID that reached ANY terminal outcome.

        A skip reports at setup and an error at setup or call, so recording
        the first report per node ID includes skips without counting a
        passing test's setup/call/teardown three times.
        """
        if self._root is None:
            return
        node_id = report.nodeid
        if node_id not in self._seen_node_ids:
            self._seen_node_ids.add(node_id)
            self._terminal_node_ids.append(node_id)

    @staticmethod
    def _summary_line(terminalreporter):
        """The line pytest would print, from the terminal summary source."""
        parts, _ = terminalreporter.build_summary_stats_line()
        text = ", ".join(part for part, _ in parts)
        elapsed = terminalreporter._session_start.elapsed().seconds
        return f"{text} in {_format_duration(elapsed)}"

    def pytest_sessionfinish(self, session, exitstatus):
        if self._root is None:
            return
        path = record_path()
        if path is None:
            # Gated but no workspace: fail loudly rather than write into the
            # working tree. The verifier treats the absent record as a
            # failure, which is the fail-closed direction.
            print(
                "CI-FULL-RUN-RECORD: GITHUB_ACTIONS is set but "
                "GITHUB_WORKSPACE is empty; no run record was written. "
                "Probe with GITHUB_WORKSPACE=/tmp/<rig>.",
                file=sys.stderr,
            )
            return
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        stats = reporter.stats if reporter is not None else {}
        summary_line = None
        if reporter is not None:
            try:
                summary_line = self._summary_line(reporter)
            except Exception as exc:
                # A missing summary must surface as NO-SUMMARY in the
                # verifier, not as a crashed session. Print the reason so the
                # CI log names it.
                print(f"CI-FULL-RUN-RECORD: summary unavailable: {exc!r}",
                      file=sys.stderr)
                summary_line = None
        record = {
            "schema_version": SCHEMA_VERSION,
            "collected": int(session.testscollected),
            "passed": len(stats.get("passed", [])),
            "skipped": len(stats.get("skipped", [])),
            "failed": len(stats.get("failed", [])),
            "errors": len(stats.get("error", [])),
            "deselected": len(stats.get("deselected", [])),
            "xfailed": len(stats.get("xfailed", [])),
            "xpassed": len(stats.get("xpassed", [])),
            "exitstatus": int(exitstatus),
            "summary_line": summary_line,
            "terminal_node_ids": list(self._terminal_node_ids),
        }
        temp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(
                json.dumps(record, indent=1, sort_keys=True), encoding="utf-8")
            os.replace(temp, path)
        except OSError as exc:
            # Never crash the certifying run. The verifier fails loudly on a
            # missing record; the log carries the reason.
            print(f"CI-FULL-RUN-RECORD: cannot write {path}: {exc}",
                  file=sys.stderr)
            try:
                temp.unlink()
            except OSError:
                pass