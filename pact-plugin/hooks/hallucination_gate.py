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

Known v1 limitations:

  1. Skill is non-hookable under PreToolUse. The gate covers Bash only.
     Skill calls (e.g., voluntary self-refusal preludes on destructive
     skills) require a separate enforcement path. Mitigation: the
     SACROSANCT AskUserQuestion-discipline framework can be extended
     to require explicit AUQ before each destructive Skill invocation.

  2. Wrapper-class hallucination. The orchestrator can emit a fake
     `<system-reminder>` as an assistant text block. This gate inspects
     for the literal `Human:` substring in assistant text but does NOT
     detect spoofed platform-injected wrappers. Cryptographic sentinel
     defense is the right mechanism for that class.

  3. Recursive hallucination. If the orchestrator hallucinates the SAME
     text twice (once at gate-fire time, blocked; then again after a
     genuine user message containing the same text lands), the second
     evaluation passes the temporal anchor. The gate cannot distinguish
     re-emission from genuine authorization. Low realism in the current
     corpus; FN cost > FP cost favors the temporal-anchor approach
     despite the gap.

  4. AskUserQuestion answer descent. AUQ answers arrive as `type=user`
     entries with array content (not string) embedded in tool_result
     blocks. The gate skips array-content user entries. For Bash
     destructive ops, the merge_guard token system absorbs AUQ-class
     authorizations differently (token file, not transcript-grep).

  5. Tier-3 paraphrase matching deferred. The empirical corpus is
     verbatim echoes; no paraphrasing calibration data. Tier 1 (exact
     substring) + Tier 2 (case-fold + whitespace-collapse) only in v1.
     Tier-miss with passing temporal anchor produces WARN (advisory),
     not DENY — paraphrase-class FP is preferred over silent allow.

  6. Scan window bound. 500-line backward scan may miss far-back
     genuine authorizations ("go ahead with all merges for the rest of
     the session"). The no-matching-user-turn case will DENY in this
     scenario; the user must re-authorize via AskUserQuestion (which
     flows through the merge_guard token path).

  7. Assistant explanatory mentions of past Human: content. If the
     assistant emits prose quoting a prior `Human:` directive (e.g.,
     "the user wrote 'Human: yes' earlier") AFTER the genuine user turn,
     the gate latches onto the quoted suffix as the hallucinated text.
     Substring tier-1/2 typically miss (the quote has extra surrounding
     text), producing WARN. This is acceptable v1 behavior — the
     destructive op is not blocked, just flagged.

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
        # Shapes covered:
        #   `git push --tags`               — bulk tag push, --tags first
        #   `git push origin --tags`        — bulk tag push, --tags after remote
        #   `git push <remote> refs/tags/<x>`  — explicit tag refspec
        #   `git push <remote> v1.2.3`      — semver-shaped tag positional
        #   `git push <remote> v1` / `2`    — dotless single-token version ref
        #
        # The semver-tag regex uses `(?:\.\d+)*` (zero-or-more decimal
        # groups) to admit dotless forms like `v1`, `2`, `1024`. The
        # trailing `(?![\w-])` post-anchor (mirrors merge_guard_pre's
        # _GH_PR_NUMBER_RE convention) rejects branch-suffix forms like
        # `2-branch`, `1024-stuff`, `v1-branch` that a plain `\b` would
        # incorrectly admit (digit→hyphen IS a word-boundary under \b).
        # The --tags flag-walk uses the same `(?:\S+\s+)*` idiom as the
        # other destructive patterns so --tags is caught wherever it
        # appears after `push`.
        # Branch-shaped positionals (`feature-x`, `release-2026`) are
        # NOT flagged; the main-branch push form is handled by
        # merge_guard_pre.
        re.compile(_GIT_PREFIX + r"push\s+(?:\S+\s+)*--tags\b"),
        re.compile(
            _GIT_PREFIX
            + r"push\s+(?:-\S+\s+)*\S+\s+refs/tags/\S+"
        ),
        re.compile(
            _GIT_PREFIX
            + r"push\s+(?:-\S+\s+)*\S+\s+v?\d+(?:\.\d+)*(?![\w-])"
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


# ─── Temporal-anchor algorithm ─────────────────────────────────────────

# Decision sentinel values returned by `evaluate_transcript`. Stable
# strings — tests pin against them and the main() output layer maps them
# to suppressOutput / permissionDecision / additionalContext envelopes.
DECISION_ALLOW = "allow"
DECISION_DENY = "deny"
DECISION_WARN = "warn"


def evaluate_transcript(
    transcript_lines: list[str],
) -> tuple[str, str]:
    """Return (decision, reason) for the destructive-Bash gate.

    Decision is one of DECISION_ALLOW / DECISION_DENY / DECISION_WARN.
    Pure function — no I/O, no env reads. The caller (main()) handles
    transcript_path read + scan-window trim and passes the trimmed
    list with newest entry at the end.

    Algorithm (walks BACKWARD, newest line first):
      1. Track most recent `type=user` entry whose string content
         passes envelope-exclusion.
      2. Track most recent `type=assistant` entry whose content blocks
         include a text block containing the literal `Human:`.
      3. Early-break once both anchors found (backward walk yields
         newest-first → first hit is the latest occurrence).

    Decision tree:
      - No Human-emission found → ALLOW (no priming signal).
      - Emission present AND no genuine user entry → DENY.
      - Emission line-index > user-entry line-index → DENY
        (the assistant emitted Human: more recently than the latest
        genuine user keystroke; treat as hallucinated authorization).
      - User-entry more recent; hallucinated text below length floor
        → ALLOW (too short to discriminate meaningfully).
      - Tier 1 exact substring (hallucinated ⊂ user_text) → ALLOW.
      - Tier 2 normalized substring (case-fold + ws-collapse) → ALLOW.
      - All tiers miss → WARN (advisory; temporal anchor already
        passed, so this is paraphrase-class FP territory).
    """
    last_user_directive = None
    last_user_directive_idx = None
    last_assistant_human = None
    last_assistant_human_idx = None

    # Backward walk: highest-index lines are newest in JSONL format.
    # First hit on each anchor is therefore the latest occurrence;
    # early-break once both anchors found is safe.
    for idx in range(len(transcript_lines) - 1, -1, -1):
        try:
            entry = json.loads(transcript_lines[idx])
        except (json.JSONDecodeError, ValueError):
            continue  # malformed lines silently skipped, like the refresh parser

        if not isinstance(entry, dict):
            continue

        entry_type = entry.get("type")
        message = entry.get("message")
        if not isinstance(message, dict):
            continue

        if entry_type == "user" and last_user_directive is None:
            content = message.get("content")
            # Only string-content user entries are considered.
            # AskUserQuestion answers arrive as array content embedded in
            # tool_result blocks; descent deferred to v2 (the
            # merge_guard token system absorbs AUQ-class authorizations
            # for Bash ops).
            if isinstance(content, str) and passes_envelope_exclusion(content):
                last_user_directive = content
                last_user_directive_idx = idx

        elif entry_type == "assistant" and last_assistant_human is None:
            blocks = message.get("content", [])
            if isinstance(blocks, list):
                for block in blocks:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "text"
                        and isinstance(block.get("text"), str)
                        and "Human:" in block["text"]
                    ):
                        extracted = extract_after_human(block["text"])
                        if extracted:
                            last_assistant_human = extracted
                            last_assistant_human_idx = idx
                            break

        if last_user_directive is not None and last_assistant_human is not None:
            break

    if last_assistant_human is None:
        return (DECISION_ALLOW, "no_human_emission")

    if last_user_directive_idx is None:
        return (DECISION_DENY, "no_matching_user_message_in_scan_window")

    if last_assistant_human_idx > last_user_directive_idx:
        return (DECISION_DENY, "human_emission_more_recent_than_user_turn")

    # Genuine user entry precedes the assistant Human emission. Apply
    # substring tiers in order: length-floor short-circuit → exact →
    # normalized.
    hallucinated = last_assistant_human.strip()
    user_text = last_user_directive

    if len(hallucinated) < SUBSTRING_LENGTH_FLOOR_CHARS:
        return (DECISION_ALLOW, "tier0_below_length_floor_with_user_precedence")

    if hallucinated in user_text:
        return (DECISION_ALLOW, "tier1_exact_substring")

    if normalize(hallucinated) in normalize(user_text):
        return (DECISION_ALLOW, "tier2_normalized_substring")

    return (DECISION_WARN, "human_emission_text_not_found_in_recent_user_turns")


# ─── Transcript I/O ────────────────────────────────────────────────────

# Inlined local copy of refresh.transcript_parser.read_last_n_lines.
# Inlining keeps the SACROSANCT gate decoupled from the refresh-subsystem
# evolution (the gate fail-CLOSES on any module-load failure of an
# imported helper). Extract to shared/transcript_tail.py if a second
# consumer materializes; until then, ~25 lines of seek-from-end
# boilerplate is cheaper than the coupling.
_LARGE_FILE_THRESHOLD_BYTES = 10 * 1024 * 1024


def _read_last_n_lines(path: Path, n: int) -> list[str]:
    """Return the last N lines of `path` (oldest first within the slice,
    newest at end) or [] on any I/O error.

    Small files (< 10MB) read+slice; large files reverse-seek in chunks.
    All exceptions are swallowed and surface as []; the caller treats
    empty as `cannot evaluate` → fail-OPEN ALLOW.
    """
    try:
        file_size = path.stat().st_size

        if file_size < _LARGE_FILE_THRESHOLD_BYTES:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            return lines[-n:] if len(lines) > n else lines

        chunk_size = 8192
        collected: list[str] = []
        with open(path, "rb") as f:
            f.seek(0, 2)
            remaining = f.tell()
            buffer = b""
            while remaining > 0 and len(collected) < n:
                read_size = min(chunk_size, remaining)
                remaining -= read_size
                f.seek(remaining)
                chunk = f.read(read_size)
                buffer = chunk + buffer
                parts = buffer.split(b"\n")
                if remaining > 0:
                    buffer = parts[0]
                    new_parts = parts[1:]
                else:
                    new_parts = parts
                    buffer = b""
                collected = [
                    p.decode("utf-8", errors="replace") + "\n"
                    for p in new_parts if p
                ] + collected
        return collected[-n:] if len(collected) > n else collected
    except (OSError, ValueError):
        return []


# ─── Output envelope ───────────────────────────────────────────────────


def _emit_deny(reason: str, triggering_text: str = "") -> NoReturn:
    """Emit canonical DENY envelope with hookEventName audit anchor."""
    snippet = (triggering_text or "")[:60]
    msg = (
        f"PACT hallucination_gate denied destructive Bash op — reason: "
        f"{reason}."
    )
    if snippet:
        msg += f" Triggering text snippet: {snippet!r}."
    msg += (
        " The most recent `Human:` directive appears to be an "
        "orchestrator-emitted hallucination rather than a genuine user "
        "keystroke. Use AskUserQuestion to confirm with the user before "
        "proceeding."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": msg,
        }
    }))
    sys.exit(2)


def _emit_warn(reason: str, triggering_text: str = "") -> NoReturn:
    """Emit advisory WARN envelope via additionalContext.

    Tier-miss with passing temporal anchor: the assistant emitted a
    `Human:` directive that does NOT appear verbatim (or via case-fold +
    whitespace-collapse) in any recent genuine user turn, yet the user
    turn precedes the emission. Could be paraphrase-class authorization
    OR a paraphrase-class hallucination; gate does not block in v1.
    """
    snippet = (triggering_text or "")[:60]
    msg = (
        f"PACT hallucination_gate advisory — reason: {reason}."
    )
    if snippet:
        msg += f" Triggering text snippet: {snippet!r}."
    msg += (
        " The most recent assistant `Human:` block was not found in the "
        "recent genuine user turns within the scan window. Temporal "
        "anchor passes, so this is not blocked, but the operator should "
        "verify the destructive op was actually user-authorized."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": msg,
        }
    }))
    sys.exit(0)


def _emit_allow() -> NoReturn:
    print(_SUPPRESS_OUTPUT)
    sys.exit(0)


# ─── main() entry point ────────────────────────────────────────────────


def main() -> NoReturn:
    """Hook entry point.

    Flow:
      1. Parse stdin (fail-OPEN on JSONDecodeError).
      2. Initialize pact_context; short-circuit ALLOW on non-PACT
         session or teammate caller.
      3. Short-circuit ALLOW on non-Bash tool or missing command.
      4. Short-circuit ALLOW on non-destructive command (regex filter).
      5. Read transcript_path last N lines (fail-OPEN on missing/
         unreadable file).
      6. Dispatch to evaluate_transcript and emit the corresponding
         envelope.

    Every uncaught exception in this body fail-CLOSES via
    _emit_load_failure_deny("runtime", error) — SACROSANCT.
    """
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            _emit_allow()

        if not isinstance(input_data, dict):
            _emit_allow()

        pact_context.init(input_data)

        # Teammate-caller short-circuit. resolve_agent_name returns ""
        # for the lead (main process / non-PACT) and a non-empty name
        # for teammates. Orchestrator-side `Human:` hallucinations are
        # a lead-side failure mode; teammates have a different message
        # envelope and don't trigger the same priming pattern.
        if resolve_agent_name(input_data):
            _emit_allow()

        tool_name = input_data.get("tool_name", "")
        if tool_name != "Bash":
            _emit_allow()

        tool_input = input_data.get("tool_input", {})
        if not isinstance(tool_input, dict):
            _emit_allow()

        command = tool_input.get("command", "")
        if not command or not isinstance(command, str):
            _emit_allow()

        if not is_destructive_command(command):
            _emit_allow()

        transcript_path = input_data.get("transcript_path", "")
        if not transcript_path or not isinstance(transcript_path, str):
            _emit_allow()

        path = Path(transcript_path)
        if not path.exists() or not path.is_file():
            _emit_allow()

        lines = _read_last_n_lines(path, TRANSCRIPT_SCAN_WINDOW_LINES)
        if not lines:
            _emit_allow()

        decision, reason = evaluate_transcript(lines)

        if decision == DECISION_ALLOW:
            _emit_allow()
        elif decision == DECISION_DENY:
            _emit_deny(reason, command)
        elif decision == DECISION_WARN:
            _emit_warn(reason, command)
        else:
            # Unknown decision string — treat as fail-OPEN ALLOW rather
            # than DENY: better to let the layered merge_guard gate
            # below decide than to invent semantics.
            _emit_allow()

    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as runtime_error:  # noqa: BLE001 — SACROSANCT fail-closed
        _emit_load_failure_deny("runtime", runtime_error)


if __name__ == "__main__":
    main()
