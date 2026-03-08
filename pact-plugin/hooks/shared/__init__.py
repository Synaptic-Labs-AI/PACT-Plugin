"""
Location: pact-plugin/hooks/shared/__init__.py
Summary: Package for shared hook utilities.
Used by: Various PACT hooks that need common Task system integration.

This package provides shared utilities for hooks, primarily Task system
integration functions that are used across multiple hooks.
"""

from .task_utils import (
    get_task_list,
    find_feature_task,
    find_current_phase,
    find_active_agents,
    find_blockers,
)

__all__ = [
    "get_task_list",
    "find_feature_task",
    "find_current_phase",
    "find_active_agents",
    "find_blockers",
]
