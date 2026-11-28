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
        assert f"{smoke['handoffs_valid']}/{smoke['handoffs']} HANDOFFs valid" in row["Orchestration smoke"]

        track_b = data["track_b"]
        if track_b["status"] == "authored_out_of_tree":
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
