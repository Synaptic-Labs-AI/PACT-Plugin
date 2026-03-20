"""
Location: pact-plugin/hooks/shared/handoff_example.py
Summary: Shared handoff example template used by handoff_gate.py and
         teammate_completion_gate.py to provide agents with a concrete,
         copy-paste-ready TaskUpdate example for storing HANDOFF metadata.
Used by: hooks/handoff_gate.py, hooks/teammate_completion_gate.py
"""

# Template uses str.format() instead of f-strings to avoid brace-escaping
# hell with nested JSON braces. The {task_id} placeholder is the only
# substitution needed.
_HANDOFF_EXAMPLE_TEMPLATE = (
    'Example — copy, fill in, and run:\n'
    'TaskUpdate(taskId="{task_id}", metadata={{"handoff": {{\n'
    '  "produced": ["file1.py"], "decisions": ["chose X because Y"],\n'
    '  "uncertainty": [{{"LOW": "untested edge case"}}],\n'
    '  "integration": ["touches module Z"], "open_questions": ["none"]\n'
    '}}}})\n\n'
    'Then call: TaskUpdate(taskId="{task_id}", status="completed")'
)


def format_handoff_example(task_id: str = "YOUR_ID") -> str:
    """
    Generate a concrete, copy-paste-ready handoff example for agents.

    The example uses realistic but clearly templated values so agents
    can pattern-match and fill in their own data rather than trying to
    parse a schema description.

    Args:
        task_id: Task ID to use in the example. Defaults to "YOUR_ID"
                 (handoff_gate uses this). Pass the actual task ID when
                 available (teammate_completion_gate uses this).

    Returns:
        Multi-line string with the example and two-step instruction.
    """
    return _HANDOFF_EXAMPLE_TEMPLATE.format(task_id=task_id)
