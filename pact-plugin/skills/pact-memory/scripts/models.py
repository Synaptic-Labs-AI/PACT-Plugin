"""
PACT Memory Data Models

Location: pact-plugin/skills/pact-memory/scripts/models.py

Rich memory object dataclasses for the PACT Memory skill.
Provides structured representations of memory data with serialization
and deserialization capabilities.

Used by:
- memory_api.py: High-level API wraps raw dicts as MemoryObject instances
- search.py: Search results returned as MemoryObject instances
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Union


@dataclass
class TaskItem:
    """
    Represents a task within a memory.

    Attributes:
        task: Description of the task.
        status: Current status ('pending', 'in_progress', 'completed').
        priority: Optional priority level ('low', 'medium', 'high').
    """
    task: str
    status: str = "pending"
    priority: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Union[Dict[str, Any], str]) -> "TaskItem":
        """Create TaskItem from dict or string."""
        if isinstance(data, str):
            return cls(task=data)
        return cls(
            task=data.get("task", ""),
            status=data.get("status", "pending"),
            priority=data.get("priority")
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = {"task": self.task, "status": self.status}
        if self.priority:
            result["priority"] = self.priority
        return result


@dataclass
class Decision:
    """
    Represents a decision made during development.

    Attributes:
        decision: The decision that was made.
        rationale: Why this decision was made.
        alternatives: Alternative options that were considered.
    """
    decision: str
    rationale: Optional[str] = None
    alternatives: Optional[List[str]] = None

    @classmethod
    def from_dict(cls, data: Union[Dict[str, Any], str]) -> "Decision":
        """Create Decision from dict or string."""
        if isinstance(data, str):
            return cls(decision=data)
        return cls(
            decision=data.get("decision", ""),
            rationale=data.get("rationale"),
            alternatives=data.get("alternatives")
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = {"decision": self.decision}
        if self.rationale:
            result["rationale"] = self.rationale
        if self.alternatives:
            result["alternatives"] = self.alternatives
        return result


@dataclass
class Entity:
    """
    Represents an entity referenced in a memory (component, service, etc.).

    Attributes:
        name: Name of the entity.
        type: Type of entity ('component', 'service', 'module', 'function', etc.).
        notes: Additional notes about the entity.
    """
    name: str
    type: Optional[str] = None
    notes: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Union[Dict[str, Any], str]) -> "Entity":
        """Create Entity from dict or string."""
        if isinstance(data, str):
            return cls(name=data)
        return cls(
            name=data.get("name", ""),
            type=data.get("type"),
            notes=data.get("notes")
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = {"name": self.name}
        if self.type:
            result["type"] = self.type
        if self.notes:
            result["notes"] = self.notes
        return result


def _parse_string_list(raw: Any) -> List[str]:
    """
    Parse a field that should be a list of strings, handling JSON strings and None.

    Handles three cases:
    - None/empty: returns []
    - list: filters None values and converts items to str
    - str: attempts JSON parse, falls back to single-element list

    Args:
        raw: The raw field value from database or dict.

    Returns:
        List of strings.
    """
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw if item is not None]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return [str(item) for item in parsed if item is not None] if isinstance(parsed, list) else [raw]
        except json.JSONDecodeError:
            return [raw] if raw else []
    return []


@dataclass
class MemoryObject:
    """
    Rich memory object representing a saved context.

    This is the primary data structure for memories in the PACT Memory system.
    It combines context, goals, tasks, lessons, decisions, entity references,
    and conversation-theory fields into a single cohesive structure.

    Attributes:
        id: Unique identifier for the memory.
        context: Current working context description.
        goal: What we're trying to achieve.
        active_tasks: List of tasks with status and priority.
        lessons_learned: What worked or didn't work.
        decisions: Decisions made with rationale and alternatives.
        entities: Referenced components, services, or modules.
        reasoning_chains: How key decisions connect — "X because Y, which required Z".
        agreements_reached: What was verified via teachback or agreement check.
        disagreements_resolved: Where agents disagreed and how it was settled.
        files: List of file paths associated with this memory.
        project_id: Project identifier for scoping.
        session_id: Session identifier for grouping.
        created_at: When the memory was created.
        updated_at: When the memory was last updated.
    """
    id: str
    context: Optional[str] = None
    goal: Optional[str] = None
    active_tasks: List[TaskItem] = field(default_factory=list)
    lessons_learned: List[str] = field(default_factory=list)
    decisions: List[Decision] = field(default_factory=list)
    entities: List[Entity] = field(default_factory=list)
    reasoning_chains: List[str] = field(default_factory=list)
    agreements_reached: List[str] = field(default_factory=list)
    disagreements_resolved: List[str] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    project_id: Optional[str] = None
    session_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryObject":
        """
        Create a MemoryObject from a dictionary.

        Handles conversion of nested structures (tasks, decisions, entities)
        and datetime parsing. Also handles edge cases like string values
        for list fields and None values in lists.

        Args:
            data: Dictionary with memory fields.

        Returns:
            MemoryObject instance.
        """
        # Parse active_tasks
        raw_tasks = data.get("active_tasks") or []
        if isinstance(raw_tasks, str):
            # Handle JSON string or plain string
            try:
                parsed = json.loads(raw_tasks)
                raw_tasks = parsed if isinstance(parsed, list) else [{"task": raw_tasks}]
            except json.JSONDecodeError:
                # Plain string - treat as a single task
                raw_tasks = [{"task": raw_tasks}] if raw_tasks else []
        # Filter out None values in the list
        active_tasks = [TaskItem.from_dict(t) for t in raw_tasks if t is not None]

        # Parse lessons_learned (simple string list — uses shared helper)
        lessons_learned = _parse_string_list(data.get("lessons_learned"))

        # Parse decisions
        raw_decisions = data.get("decisions") or []
        if isinstance(raw_decisions, str):
            try:
                parsed = json.loads(raw_decisions)
                raw_decisions = parsed if isinstance(parsed, list) else [{"decision": raw_decisions}]
            except json.JSONDecodeError:
                # Plain string - treat as a single decision
                raw_decisions = [{"decision": raw_decisions}] if raw_decisions else []
        # Filter out None values
        decisions = [Decision.from_dict(d) for d in raw_decisions if d is not None]

        # Parse entities
        raw_entities = data.get("entities") or []
        if isinstance(raw_entities, str):
            try:
                parsed = json.loads(raw_entities)
                raw_entities = parsed if isinstance(parsed, list) else [{"name": raw_entities}]
            except json.JSONDecodeError:
                # Plain string - treat as a single entity
                raw_entities = [{"name": raw_entities}] if raw_entities else []
        # Filter out None values
        entities = [Entity.from_dict(e) for e in raw_entities if e is not None]

        # Parse CT fields (simple string lists, same pattern as lessons_learned)
        reasoning_chains = _parse_string_list(data.get("reasoning_chains"))
        agreements_reached = _parse_string_list(data.get("agreements_reached"))
        disagreements_resolved = _parse_string_list(data.get("disagreements_resolved"))

        # Parse files (simple string list)
        files = data.get("files") or []
        if isinstance(files, str):
            try:
                files = json.loads(files)
            except json.JSONDecodeError:
                files = [files] if files else []

        # Parse datetimes
        created_at = _parse_datetime(data.get("created_at"))
        updated_at = _parse_datetime(data.get("updated_at"))

        return cls(
            id=data.get("id", ""),
            context=data.get("context"),
            goal=data.get("goal"),
            active_tasks=active_tasks,
            lessons_learned=lessons_learned,
            decisions=decisions,
            entities=entities,
            reasoning_chains=reasoning_chains,
            agreements_reached=agreements_reached,
            disagreements_resolved=disagreements_resolved,
            files=files,
            project_id=data.get("project_id"),
            session_id=data.get("session_id"),
            created_at=created_at,
            updated_at=updated_at
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary suitable for database storage.

        Nested structures are converted to lists of dicts.
        Datetimes are formatted as ISO strings.

        Returns:
            Dictionary representation of the memory.
        """
        return {
            "id": self.id,
            "context": self.context,
            "goal": self.goal,
            "active_tasks": [t.to_dict() for t in self.active_tasks],
            "lessons_learned": self.lessons_learned,
            "decisions": [d.to_dict() for d in self.decisions],
            "entities": [e.to_dict() for e in self.entities],
            "reasoning_chains": self.reasoning_chains,
            "agreements_reached": self.agreements_reached,
            "disagreements_resolved": self.disagreements_resolved,
            "files": self.files,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }

    def to_storage_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary for database storage.

        This excludes fields that are auto-populated or managed separately
        (like 'files' which are stored in the graph).

        Returns:
            Dictionary suitable for create_memory/update_memory.
        """
        return {
            "id": self.id,
            "context": self.context,
            "goal": self.goal,
            "active_tasks": [t.to_dict() for t in self.active_tasks],
            "lessons_learned": self.lessons_learned,
            "decisions": [d.to_dict() for d in self.decisions],
            "entities": [e.to_dict() for e in self.entities],
            "reasoning_chains": self.reasoning_chains,
            "agreements_reached": self.agreements_reached,
            "disagreements_resolved": self.disagreements_resolved,
            "project_id": self.project_id,
            "session_id": self.session_id
        }

    def get_searchable_text(self) -> str:
        """
        Generate text representation for embedding/search.

        Combines key fields into a single text block for
        semantic search embedding generation.

        Returns:
            Concatenated text from context, goal, lessons, and decisions.
        """
        parts = []

        if self.context:
            parts.append(f"Context: {self.context}")

        if self.goal:
            parts.append(f"Goal: {self.goal}")

        if self.active_tasks:
            task_text = "; ".join(t.task for t in self.active_tasks)
            parts.append(f"Tasks: {task_text}")

        if self.lessons_learned:
            lessons_text = "; ".join(self.lessons_learned)
            parts.append(f"Lessons: {lessons_text}")

        if self.decisions:
            decision_texts = []
            for d in self.decisions:
                text = d.decision
                if d.rationale:
                    text += f" ({d.rationale})"
                decision_texts.append(text)
            parts.append(f"Decisions: {'; '.join(decision_texts)}")

        if self.entities:
            entity_text = ", ".join(
                f"{e.name} ({e.type})" if e.type else e.name
                for e in self.entities
            )
            parts.append(f"Entities: {entity_text}")

        if self.reasoning_chains:
            chains_text = "; ".join(self.reasoning_chains)
            parts.append(f"Reasoning: {chains_text}")

        if self.agreements_reached:
            agreements_text = "; ".join(self.agreements_reached)
            parts.append(f"Agreements: {agreements_text}")

        if self.disagreements_resolved:
            disagreements_text = "; ".join(self.disagreements_resolved)
            parts.append(f"Disagreements resolved: {disagreements_text}")

        return "\n".join(parts)

    def __repr__(self) -> str:
        """Concise string representation."""
        context_preview = (
            self.context[:50] + "..." if self.context and len(self.context) > 50
            else self.context
        )
        return f"MemoryObject(id={self.id!r}, context={context_preview!r})"


def _parse_datetime(value: Any) -> Optional[datetime]:
    """
    Parse a datetime from various formats.

    Args:
        value: String, datetime, or None.

    Returns:
        Parsed datetime or None.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        # Try ISO format
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
        # Try common formats
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None


def memory_from_db_row(
    row: Dict[str, Any],
    files: Optional[List[str]] = None
) -> MemoryObject:
    """
    Create a MemoryObject from a database row with optional files.

    Convenience function for combining memory data with graph-derived files.

    Args:
        row: Dictionary from database query.
        files: Optional list of file paths from graph query.

    Returns:
        MemoryObject instance.
    """
    data = dict(row)
    if files is not None:
        data["files"] = files
    return MemoryObject.from_dict(data)
