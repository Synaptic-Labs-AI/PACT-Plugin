"""Fail when the harvest skill's prose names something its code does not have.

The region from `### Step 1:` to `### Step 3.5` is the instruction the
harvesting agent follows, and the Step 3 block plus the shared helpers are
what it calls. Two mechanical contradictions are caught here:

- a backticked identifier in the prose that names nothing in the code, the
  journal schema, the handoff schema, the platform tools or a short literal
  vocabulary;
- a report line quoted in the prose that no block or helper f-string emits.
"""

import ast
import inspect
import re
from pathlib import Path

import test_harvest_step3_exec as step3

from shared import handoff_merge, handoff_schema, session_journal

_HOOKS = Path(__file__).resolve().parents[1] / "hooks"

REGION = re.sub(
    r"```.*?```", "",
    step3.SKILL_TEXT.split("### Step 1:", 1)[1].split("### Step 3.5", 1)[0],
    flags=re.S,
)

# Names the prose may use that no code defines: the union's set symbols,
# literal values, census and sibling key names, shell tools and plain words.
VOCAB = frozenset({
    "D", "J", "EMPTY", "None", "False", "null", "awk", "read", "context",
    "final", "state", "unknown", "written_at_utc", "_dropped_keys",
    "audit_summary", "audit_summary_authored", "handoff_addendum",
    "handoff_amendment", "post_completion_verification", "teachback_submit",
})
PLATFORM_TOOLS = frozenset({"TaskList", "TaskGet", "TaskUpdate", "TaskCompleted"})


def _block_names():
    names = set()
    for node in ast.walk(ast.parse(step3.BLOCK)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.FunctionDef):
            names.add(node.name)
            names.update(a.arg for a in node.args.args)
    return names


def _route_names():
    names = set()
    for match in re.finditer(r"from shared\.\w+ import ([\w, ]+);", step3.SKILL_TEXT):
        names.update(n.strip() for n in match.group(1).split(","))
    return names


def _helper_names():
    """Exported helper names and their parameter names."""
    names = set(handoff_merge.__all__)
    for name in handoff_merge.__all__:
        obj = getattr(handoff_merge, name)
        if callable(obj) and not isinstance(obj, type):
            try:
                names.update(inspect.signature(obj).parameters)
            except (TypeError, ValueError):
                pass
    names.update(n for n in dir(handoff_schema) if not n.startswith("_"))
    return names


def _schema_names():
    names = set()
    for table in (session_journal._REQUIRED_FIELDS_BY_TYPE,
                  session_journal._OPTIONAL_FIELDS_BY_TYPE):
        for event_type, fields in table.items():
            names.add(event_type)
            names.update(fields)
    names.update(handoff_schema.HANDOFF_REQUIRED_FIELDS)
    names.update(handoff_schema.HANDOFF_RECOMMENDED_FIELDS)
    names.update(handoff_schema.HANDOFF_LEGACY_ALIASES)
    return names


def _known():
    return (_block_names() | _route_names() | _helper_names() | _schema_names()
            | PLATFORM_TOOLS | VOCAB)


def _prose_identifiers():
    return set(re.findall(r"`([A-Za-z_]\w*)`", REGION))


def test_every_backticked_identifier_in_the_prose_exists():
    """G-B3: the prose names nothing the code does not have."""
    found = _prose_identifiers()
    assert len(found) > 20, "the identifier parse found too little to check"
    assert not found - _known(), sorted(found - _known())


def test_every_vocabulary_entry_still_occurs():
    """The allowlist cannot rot: an entry the prose no longer uses goes."""
    assert not VOCAB - _prose_identifiers(), sorted(VOCAB - _prose_identifiers())


def _template(node: ast.JoinedStr) -> str:
    parts = []
    for value in node.values:
        if isinstance(value, ast.Constant):
            parts.append(re.escape(value.value))
        else:
            parts.append(".+")
    return "".join(parts)


def _emitted_templates():
    """Every `handoff ...` f-string the block or a shared helper emits."""
    sources = [step3.BLOCK, (_HOOKS / "shared" / "handoff_merge.py").read_text(encoding="utf-8")]
    templates = set()
    for source in sources:
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.JoinedStr):
                head = node.values[0] if node.values else None
                if isinstance(head, ast.Constant) and str(head.value).startswith("handoff "):
                    templates.add(_template(node))
    return templates


def _prose_reports():
    """Backticked `handoff ...` report lines quoted in the prose, as regexes."""
    out = []
    for quoted in re.findall(r"`(handoff [^`]+)`", REGION):
        pattern = re.escape(quoted)
        pattern = re.sub(r"\\<[^>]*\\>|<[^>]*>", ".+", pattern)
        pattern = re.sub(r"\b[NK]\b", ".+", pattern)
        out.append((quoted, pattern))
    return out


def _matches(prose_pattern: str, template: str) -> bool:
    """A prose line and a code template describe the same line when each
    accepts a sample of the other's literal text."""
    sample = re.sub(r"\.\+", "X", template).replace("\\", "")
    probe = re.sub(r"\.\+", "X", prose_pattern).replace("\\", "")
    return bool(re.fullmatch(prose_pattern, sample) or re.fullmatch(template, probe))


def test_every_report_quoted_in_the_prose_is_one_the_code_emits():
    """G-B4: a report line the prose promises is a line some code writes."""
    reports = _prose_reports()
    templates = _emitted_templates()
    assert len(reports) >= 10 and len(templates) >= 10, (len(reports), len(templates))
    orphans = [q for q, p in reports if not any(_matches(p, t) for t in templates)]
    assert not orphans, orphans
