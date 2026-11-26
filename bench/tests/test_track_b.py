from __future__ import annotations

from pathlib import Path

from runner import track_b


def test_discover_tasks_returns_all_six() -> None:
    tasks = track_b.discover_tasks(track_b.TASKS)
    assert {task.id for task in tasks} == {
        "notes-api-crud",
        "invoice-rounding-bug",
        "jwt-auth",
        "discount-strategy-refactor",
        "oauth-pkce-flow",
        "job-queue-retries",
    }


def test_build_prompt_includes_absolute_sentinel(tmp_path: Path) -> None:
    task = track_b.load_task(track_b.TASKS / "notes-api-crud")
    sentinel = tmp_path / "DONE"
    prompt = track_b.build_prompt(task, sentinel)
    assert str(sentinel) in prompt
    assert "exactly DONE" in prompt
    assert "\n" not in prompt


def test_aggregate_scores_by_task_and_condition() -> None:
    results = [
        {"task": "a", "condition": "pact", "status": "success", "elapsed_seconds": 10, "tokens": 100, "journal": {"handoffs": 2}, "subagents": {"task_tools": ["TaskUpdate"]}},
        {"task": "a", "condition": "pact", "status": "hidden_fail", "elapsed_seconds": 20, "tokens": 200, "journal": {"handoffs": 0}, "subagents": {"task_tools": ["TaskList"]}},
        {"task": "a", "condition": "solo", "status": "success", "elapsed_seconds": 5, "tokens": 50, "journal": {"handoffs": 0}, "subagents": {"task_tools": []}},
    ]
    agg = track_b.aggregate(results)
    rows = {(row["task"], row["condition"]): row for row in agg["rows"]}
    assert rows[("a", "pact")]["n"] == 2
    assert rows[("a", "pact")]["pass_rate"] == 0.5
    assert rows[("a", "solo")]["pass_rate"] == 1.0
