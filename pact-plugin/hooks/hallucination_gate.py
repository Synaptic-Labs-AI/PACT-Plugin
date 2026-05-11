#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/hallucination_gate.py
Summary: PreToolUse hook matching Bash — intercepts destructive Bash calls
         and verifies the authorizing user message exists as a genuine
         `type=user` entry in the session transcript, distinguishing
         genuine user input from orchestrator-hallucinated `Human:` turns.
Used by: hooks.json PreToolUse hook (matcher: Bash) — registered FIRST
         in the Bash chain, before git_commit_check and merge_guard_pre.

Defense layer 5 of the umbrella anti-hallucination defense set. Walks
the transcript JSONL backward over a bounded scan window, tracking the
most recent assistant text block containing the literal `Human:` and the
most recent genuine-shaped `type=user` entry (after envelope-exclusion
filter rejects platform-injected wrappers). Decision tree:

  - No `Human:` emission in scan window → ALLOW
  - Emission present AND no genuine user entry in scan window → DENY
  - Emission line-index > latest genuine user entry line-index → DENY
  - User entry more recent; substring tiers match → ALLOW
  - User entry more recent; all substring tiers miss → WARN (advisory
    additionalContext; temporal-anchor is the primary discriminator)

SACROSANCT failure semantics (mirrors merge_guard_pre / bootstrap_gate):
  - Module-load failure → DENY (fail-CLOSED)
  - Pattern-compile failure → DENY (fail-CLOSED)
  - Runtime gate-logic exception → DENY (fail-CLOSED)
  - Malformed stdin → ALLOW (fail-OPEN; harness contract failure)
  - Missing/unreadable transcript_path → ALLOW (cannot evaluate)

Audit anchor: every DENY output carries `hookEventName: "PreToolUse"`;
the harness silently fails open without it.

Known v1 limitations (see module-load docstring expansion in later
commit + hook docstring referenced from issue tracker):
  - Skill is non-hookable under PreToolUse; gate covers Bash only.
  - Wrapper-class hallucination (fake <system-reminder> emitted as
    assistant text) is out of scope; cryptographic-sentinel defense
    handles that class.
  - Recursive hallucination (same text emitted twice across a genuine
    user message landing in between) is out of scope.
  - AskUserQuestion answer descent into tool_result.content arrays
    deferred to v2; covered for Bash ops via merge_guard token system.

Input: JSON from stdin with tool_name, tool_input, transcript_path, cwd.
Output: JSON with `suppressOutput` (allow), `permissionDecision` (deny),
        or `additionalContext` (warn).
"""

import json
import sys
from typing import NoReturn


def _emit_load_failure_deny(stage: str, error: BaseException) -> NoReturn:
    """Emit fail-CLOSED deny for module-load or runtime gate-logic failure.

    Mirrors merge_guard_pre._emit_load_failure_deny and
    bootstrap_gate._emit_load_failure_deny. Uses ONLY stdlib (json, sys)
    so it remains functional even when every wrapped import below has
    failed. Audit anchor: hookEventName must be present in any deny
    output — the harness silently fails open without it.
    """
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"PACT hallucination_gate {stage} failure — blocking for "
                f"safety. {type(error).__name__}: {error}. Check hook "
                f"installation and shared module availability."
            ),
        }
    }))
    print(
        f"Hook load error (hallucination_gate, {stage}): {error}",
        file=sys.stderr,
    )
    sys.exit(2)


# Fail-CLOSED wrapper around all module-level risky work (cross-package
# imports + regex compilations). If ANY of this load fails (broken
# Python install, missing shared.pact_context, syntax error in
# merge_guard_pre, malformed regex), the harness sees a structured deny
# BEFORE the process exits — instead of an empty stdout that would fail
# open. Handler depends ONLY on stdlib (json, sys) imported above this
# block, so it remains functional even if every cross-package import
# below fails.
try:
    import re
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    import shared.pact_context as pact_context  # noqa: E402
    from shared.pact_context import resolve_agent_name  # noqa: E402

    # _strip_non_executable_content lives in merge_guard_pre.py module-
    # level (not yet extracted to shared/merge_guard_common.py as of
    # this CODE-phase check). Import directly from the sibling hook
    # module rather than duplicating the ~170-line strip pipeline.
    from merge_guard_pre import _strip_non_executable_content  # noqa: E402
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_deny("module load", _module_load_error)


# Pre-serialized JSON for allow-path output: tells Claude Code UI to
# suppress the hook display instead of showing "hook error (No output)".
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


# ─── Tunable constants ─────────────────────────────────────────────────

# Backward scan window for transcript walk. Covers the 3-instance
# corpus gap distribution (22 / 196 / no-counterpart) with headroom.
# ~5-15 lines per turn → 500 lines ≈ 30-100 turns of context.
TRANSCRIPT_SCAN_WINDOW_LINES = 500

# Minimum characters in the post-`Human:` substring for tier-1/2 matching.
# Hallucinated text below the floor (~4 words) is treated as
# discrimination-insufficient: if temporal anchor passes (user entry more
# recent than emission), ALLOW. Avoids tripping on `Human: ok`.
SUBSTRING_LENGTH_FLOOR_CHARS = 20

# Maximum chars extracted after `Human:` for substring comparison.
# Bounds the compare cost; the orchestrator hallucination shape observed
# in the corpus puts the directive on the same or next line.
EXTRACTED_HUMAN_MAX_CHARS = 200


# ─── Envelope-exclusion ────────────────────────────────────────────────

# Prefixes that mark a `type=user` content string as PLATFORM-INJECTED
# rather than a genuine user keystroke. Any user-entry whose content
# (after lstrip) starts with one of these is filtered out of the
# temporal-anchor walk — it cannot serve as the "authorizing user turn"
# for a destructive op.
#
# `startswith` is more conservative than `in`: a user who literally
# types `<teammate-message` at message start is vanishingly rare;
# substring-anywhere would over-exclude legitimate quotations.
ENVELOPE_PREFIXES = (
    "<teammate-message",
    "<task-notification>",
    "<command-message>",
    "<system-reminder>",
    "[Request interrupted",
)


def passes_envelope_exclusion(content: str) -> bool:
    """Return True iff `content` is shaped like a genuine user keystroke.

    Pure function. False on any of the 5 platform-injected envelopes;
    True on bare text. lstrip handles leading whitespace from soft
    indentation.
    """
    if not isinstance(content, str):
        return False
    s = content.lstrip()
    return not any(s.startswith(p) for p in ENVELOPE_PREFIXES)


def extract_after_human(text: str) -> str:
    """Extract the substring after the LAST `Human:` occurrence.

    Returns up to EXTRACTED_HUMAN_MAX_CHARS from the first line after
    the marker, stripped of leading whitespace. Returns "" if `Human:`
    is absent.

    The orchestrator emission shape observed in the corpus is a
    multi-line block where `Human:` is followed by the hallucinated
    directive on the same or next line; bounding the extract keeps the
    downstream substring compare cheap.
    """
    if not isinstance(text, str):
        return ""
    idx = text.rfind("Human:")
    if idx == -1:
        return ""
    tail = text[idx + len("Human:"):].lstrip()
    if not tail:
        return ""
    first_line = tail.splitlines()[0] if tail else ""
    return first_line[:EXTRACTED_HUMAN_MAX_CHARS]


def normalize(s: str) -> str:
    """Tier-2 normalization: case-fold + whitespace-collapse.

    `"  Yes\\tplease   MERGE  it.\\n"` → `"yes please merge it."`.
    Pure function. Used for tier-2 substring comparison between the
    hallucinated `Human:` text and the latest genuine user turn.
    """
    if not isinstance(s, str):
        return ""
    return " ".join(s.lower().split())


# ─── Destructive-Bash pattern set ──────────────────────────────────────

# Composed prefixes mirror merge_guard_pre conventions for DRY usage.
# `(?:\S+\s+)*` matches zero or more global-flag+value tokens (e.g.,
# `--repo owner/repo`) between the tool name and the subcommand.
try:
    _GH_GLOBAL_FLAGS = r"(?:\S+\s+)*"
    _GIT_GLOBAL_FLAGS = r"(?:\S+\s+)*"
    _GH_PREFIX = r"\bgh\s+" + _GH_GLOBAL_FLAGS
    _GIT_PREFIX = r"\bgit\s+" + _GIT_GLOBAL_FLAGS

    # Compound pattern: merge_guard_pre overlap (layered defense) +
    # rm -rf variants + artifact-creation/destruction + history-rewrite +
    # tag-publication. Layered Option B per architect: hallucination_gate
    # runs FIRST under the shared matcher=Bash entry; on DENY the chain
    # halts and merge_guard_pre does not run.
    DESTRUCTIVE_PATTERNS = [
        # ─── merge_guard_pre overlap (layered defense-in-depth) ───
        re.compile(_GH_PREFIX + r"pr\s+merge\b"),
        re.compile(_GH_PREFIX + r"pr\s+close\b(?=.*--delete-branch)"),
        re.compile(r"--delete-branch.*" + _GH_PREFIX + r"pr\s+close\b"),
        re.compile(_GIT_PREFIX + r"push\s+.*--force(?!-with-lease)\b"),
        re.compile(_GIT_PREFIX + r"push\s+.*-f\b"),
        re.compile(_GIT_PREFIX + r"push\s+-[a-zA-Z]*f"),
        re.compile(_GIT_PREFIX + r"branch\s+.*-D\b"),
        re.compile(_GIT_PREFIX + r"branch\s+.*--delete\s+--force\b"),
        re.compile(_GIT_PREFIX + r"branch\s+--force\s+--delete\b"),

        # ─── rm -rf and combined-flag variants ───
        # Conservative coverage: order-agnostic -r/-f combos + glued
        # -rf/-fr/-Rf, including extra letters like -rfv.
        re.compile(r"\brm\s+(?:\S+\s+)*-rf\b"),
        re.compile(r"\brm\s+(?:\S+\s+)*-fr\b"),
        re.compile(r"\brm\s+(?:\S+\s+)*-r\s+(?:\S+\s+)*-f\b"),
        re.compile(r"\brm\s+(?:\S+\s+)*-f\s+(?:\S+\s+)*-r\b"),
        re.compile(r"\brm\s+(?:\S+\s+)*-[a-zA-Z]*r[a-zA-Z]*f"),
        re.compile(r"\brm\s+(?:\S+\s+)*-[a-zA-Z]*f[a-zA-Z]*r"),

        # ─── Artifact creation / deletion via gh ───
        # gh issue create surfaced by the cross-session #684 case
        # (hallucinated 'Human:' instance that escaped existing
        # defenses). Release create/delete rewrites public artifacts.
        re.compile(_GH_PREFIX + r"issue\s+create\b"),
        re.compile(_GH_PREFIX + r"release\s+create\b"),
        re.compile(_GH_PREFIX + r"release\s+delete\b"),

        # ─── Tag publication (rewrites public history) ───
        # Narrowed from the broader sketch flagged in teachback:
        #   `git push --tags`            — explicit tag-bulk push
        #   `git push <remote> refs/tags/<x>`  — explicit tag refspec
        #   `git push <remote> v1.2.3`   — semver-shaped tag positional
        # Branch-shaped positionals are NOT flagged by this set; the
        # main-branch push form is handled by merge_guard_pre.
        re.compile(_GIT_PREFIX + r"push\s+--tags\b"),
        re.compile(
            _GIT_PREFIX
            + r"push\s+(?:-\S+\s+)*\S+\s+refs/tags/\S+"
        ),
        re.compile(
            _GIT_PREFIX
            + r"push\s+(?:-\S+\s+)*\S+\s+v?\d+(?:\.\d+)+\b"
        ),

        # ─── History-rewriting ops ───
        re.compile(_GIT_PREFIX + r"reset\s+(?:\S+\s+)*--hard\b"),
        # rebase (any form): interactive, --onto, plain — all rewrite
        # history; orchestrator-authorized rebase from hallucination
        # is categorically destructive.
        re.compile(_GIT_PREFIX + r"rebase\b"),
        re.compile(_GIT_PREFIX + r"tag\s+-d\b"),
        # Remote ref deletion: `git push <remote> :refs/heads/<x>` or
        # `git push <remote> --delete <ref>`.
        re.compile(_GIT_PREFIX + r"push\s+(?:\S+\s+)*\S+\s+:refs/"),
        re.compile(_GIT_PREFIX + r"push\s+(?:\S+\s+)*--delete\s+\S+"),
    ]
except BaseException as _pattern_compile_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_deny("pattern compilation", _pattern_compile_error)


def is_destructive_command(command: str) -> bool:
    """Return True iff `command` matches any DESTRUCTIVE_PATTERNS entry
    AFTER stripping non-executable content (heredocs, comments, quoted
    echo/printf/git-commit-message args, variable assignment values,
    here-strings).

    Pure function. Reuses merge_guard_pre._strip_non_executable_content
    for false-positive suppression — same canonical strip pipeline
    applied by the layered companion gate.
    """
    if not isinstance(command, str) or not command:
        return False
    # Normalize bash line continuations before stripping (without this,
    # patterns split across lines bypass all regex detection).
    normalized = command.replace("\\\n", " ")
    stripped = _strip_non_executable_content(normalized)
    return any(pat.search(stripped) for pat in DESTRUCTIVE_PATTERNS)


def main() -> NoReturn:
    """Hook entry point. Scaffolding only in this commit — main logic
    lands in a subsequent commit. Current behavior: fail-OPEN ALLOW on
    every invocation so the hook is registration-safe pre-logic.
    """
    try:
        try:
            json.load(sys.stdin)
        except json.JSONDecodeError:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    except (SystemExit, KeyboardInterrupt):
        # SystemExit is the success-path exit (sys.exit(0) above);
        # KeyboardInterrupt is operator-initiated. Neither is a
        # gate-logic failure — re-raise so the process exits normally.
        raise
    except Exception as runtime_error:  # noqa: BLE001 — SACROSANCT fail-closed
        _emit_load_failure_deny("runtime", runtime_error)


if __name__ == "__main__":
    main()
