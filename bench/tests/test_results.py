from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "bench" / "results"
BENCHMARKS = ROOT / "BENCHMARKS.md"

REQUIRED_KEYS = (
    "schema",
    "run_id",
    "created_at",
    "assembly",
    "plugin",
    "p1",
    "environment",
    "track_a",
    "track_b",
    "limitations",
)


# The exact BENCHMARKS.md native-eval cell for each recorded status.
NATIVE_CELLS = {
    "blocked_sandbox_confinement_no_valid_case": "blocked: sandbox confinement; bridge path fixed",
}


def _summaries() -> list[Path]:
    return sorted(RESULTS.glob("*/*/summary.json"))


def _rows() -> list[dict[str, str]]:
    lines = [line for line in BENCHMARKS.read_text(encoding="utf-8").splitlines() if line.startswith("|")]
    assert len(lines) >= 2, "BENCHMARKS.md has no results table (header and separator rows)"
    header = [cell.strip() for cell in lines[0].strip("|").split("|")]
    rows = []
    for line in lines[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        assert len(cells) == len(header), f"malformed BENCHMARKS.md row: {line}"
        rows.append(dict(zip(header, cells)))
    return rows


def test_every_summary_has_required_keys_and_sits_under_its_version() -> None:
    paths = _summaries()
    assert paths, "no committed summaries found"
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        missing = [key for key in REQUIRED_KEYS if key not in data]
        assert not missing, f"{path}: missing keys {missing}"
        assert data["schema"] == "pact-bench/result/1"
        assert path.parent.name == data["run_id"]
        assert path.parent.parent.name == data["plugin"]["version"]
        assert (path.parent / "report.md").is_file(), f"{path}: no report.md beside it"


def test_benchmarks_rows_match_summaries_one_to_one() -> None:
    summaries = [json.loads(path.read_text(encoding="utf-8")) for path in _summaries()]
    rows = _rows()
    assert len(rows) == len(summaries)
    for data in summaries:
        plugin = data["plugin"]
        matches = [
            row
            for row in rows
            if row["Version"] == plugin["version"] and plugin["sha"].startswith(row["Plugin SHA"])
        ]
        assert len(matches) == 1, f"expected one BENCHMARKS.md row for {plugin['version']} {plugin['sha']}"
        row = matches[0]
        assert len(row["Plugin SHA"]) >= 7
        assert row["Date"] == data["created_at"][:10]
        assert row["P1 internal suite"] == data["p1"]["status"]

        for skill in data["track_a"]["plugin_eval"]["selected"]:
            assert f"{skill['skill']} {skill['composite']:.2f}" in row["PluginEval selected"]

        tokens = data["track_a"]["token_snapshot"]
        assert f"median {tokens['median_spawn_total']:,} (n={tokens['samples']})" == row["Spawn tokens"]

        smoke = data["track_a"]["orchestration_smoke"]
        assert row["Orchestration smoke"].startswith(smoke["status"])
        assert f"exit {smoke['exit_code']}" in row["Orchestration smoke"]
        assert f"{smoke['handoffs_valid']}/{smoke['handoffs']} HANDOFFs valid" in row["Orchestration smoke"]

        native_status = data["track_a"]["native_eval"]["status"]
        native_blocked = native_status.startswith("blocked")
        assert row["Track A native eval"].startswith("blocked:") == native_blocked
        if "sandbox_confinement" in native_status:
            assert row["Track A native eval"].startswith("blocked: sandbox confinement")
        if native_status in NATIVE_CELLS:
            assert row["Track A native eval"] == NATIVE_CELLS[native_status]

        track_b = data["track_b"]
        if track_b["status"] == "authored_out_of_tree":
            assert len(track_b["tasks"]) == track_b["tasks_authored"]
            assert row["Track B"].startswith(f"{track_b['tasks_authored']} tasks validated out of tree")


def test_reports_carry_their_summary_numbers() -> None:
    for path in _summaries():
        data = json.loads(path.read_text(encoding="utf-8"))
        report = (path.parent / "report.md").read_text(encoding="utf-8")
        assert f"Plugin SHA: {data['plugin']['sha']}" in report
        assert f"Run: {data['run_id']}" in report
        tokens = data["track_a"]["token_snapshot"]
        medians = re.findall(r"median ([0-9][0-9,]*)", report)
        assert medians, "report.md states no spawn-token median"
        assert set(medians) == {f"{tokens['median_spawn_total']:,}"}, medians
        for skill in data["track_a"]["plugin_eval"]["selected"]:
            assert f"| {skill['skill']} | {skill['composite']:.2f} |" in report, skill["skill"]
        native = data["track_a"]["native_eval"]
        native_blocked = native["status"].startswith("blocked")
        assert ("| A2 native eval | blocked:" in report) == native_blocked
        if "sandbox_confinement" in native["status"]:
            assert "| A2 native eval | blocked: sandbox confinement" in report
        for case in native.get("probe", {}).get("cases", []):
            assert f"| {case['name']} | {case['with']:.2f} | {case['without']:.2f} |" in report, case["name"]
        assert f"Plugin tree: {data['plugin']['tree_sha']}" in report
        assert f"{tokens['min']:,} and {tokens['max']:,}" in report
        whole = data["track_a"]["plugin_eval"].get("whole_plugin_exploratory")
        if whole:
            assert f"whole-plugin static score ({whole['composite']:.2f}" in report
        smoke = data["track_a"]["orchestration_smoke"]
        assert f"{smoke['tasks_created']} tasks created; {smoke['handoffs']} HANDOFFs" in report
        for task in data["track_b"].get("tasks", []):
            assert f"| {task['id']} | {task['class']} | {task['variety']} |" in report, task["id"]
        flat = " ".join(report.split())
        assert f"| P1 internal suite at evaluated SHA | {data['p1']['status']}:" in report
        assert f"provider check resolved the model ({data['environment']['model']})" in report
        assert f"| A7 orchestration smoke | {smoke['status']}: exit {smoke['exit_code']}," in report
        assert f"{smoke['tasks_created']} tasks, {smoke['handoffs_valid']} valid HANDOFFs" in report
        assert f"all {smoke['handoffs_valid']} passed" in report
        assert f"commit {smoke['commit'][:7]}" in report
        assert f"| A6 token snapshot | {tokens['samples']} spawn samples:" in report
        assert f"provider probe used {tokens['provider_probe_input_tokens']:,} input tokens" in flat
        itp = native.get("impact_triage_probe")
        if itp:
            assert f"({itp['with_score']:.1f} with and {itp['without_score']:.1f} without" in flat
        for skill in data["track_a"]["plugin_eval"]["selected"]:
            badge = "none" if skill["badge"] == "no_badge" else skill["badge"]
            assert f"| {skill['skill']} | {skill['composite']:.2f} | {badge} |" in report, skill["skill"]
        cmap = data.get("coverage_map", {}).get("version")
        if cmap:
            assert f"| P2 coverage map | version {cmap};" in report
        if data["track_b"]["status"] == "authored_out_of_tree":
            assert f"| Track B | {data['track_b']['tasks_authored']} tasks authored" in report

        plugin_eval_status = data["track_a"]["plugin_eval"]["status"]
        assert f"| A3 PluginEval | {plugin_eval_status}:" in report


def test_summaries_are_self_consistent() -> None:
    for path in _summaries():
        data = json.loads(path.read_text(encoding="utf-8"))
        smoke = data["track_a"]["orchestration_smoke"]
        assert smoke["handoffs_all_valid"] == (smoke["handoffs_valid"] == smoke["handoffs"])
        probe = data["track_a"]["native_eval"].get("probe")
        if probe:
            cases = probe["cases"]
            assert probe["cases_total"] == len(cases)
            deltas = [case["with"] - case["without"] for case in cases]
            assert abs(probe["mean_delta"] - sum(deltas) / len(deltas)) < 1e-9
        tokens = data["track_a"]["token_snapshot"]
        assert tokens["min"] <= tokens["median_spawn_total"] <= tokens["max"]


# Token shapes and host paths that must never land in a committed result file.
LEAK_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"gh[opsu]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}"),
    re.compile(r"(?<![\w.])/(?:home|Users)/[^/\s\"']+"),
)


def test_committed_results_carry_no_token_or_host_path() -> None:
    files = [BENCHMARKS, *sorted(p for p in RESULTS.rglob("*") if p.is_file())]
    assert len(files) > 1, "no committed result files found"
    for path in files:
        text = path.read_text(encoding="utf-8")
        for pattern in LEAK_PATTERNS:
            match = pattern.search(text)
            assert match is None, f"{path}: {pattern.pattern} matched {match.group(0)[:12]!r}..."
