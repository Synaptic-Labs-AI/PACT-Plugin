#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/shared/pact_config.py
Summary: Single source of truth for PACT_* runtime configuration options.
         Resolves each option from os.environ at CALL TIME with a typed parse
         (bool exact-membership / enum allow-list), a registry-declared default,
         and validation. Fail-open by construction -- every public function is
         total (never raises); any resolution failure returns the registry
         default. The module does ZERO work at import: no module-level env
         reads, no caching. "module-load-safe" means importing this module has
         no side effects, NOT that values are cached at import. Each call reads
         os.environ LIVE, so a consumer that resolves once at its OWN module
         load (the dispatch/handoff gates) sees exactly the value a direct
         os.environ.get(...).strip().lower() would have produced, and tests can
         monkeypatch os.environ without reloading the module.
Used by: dispatch_gate.py + handoff_ordering_gate.py (get_enum -- the two
         *_MODE reads); session_init.py (llm_options -- the SessionStart
         injection of LLM-consumed options). Any future PACT_* consumer reuses
         this resolver by adding a _REGISTRY row.

Config source model -- os.environ-BLIND: the resolver reads ONLY os.environ. It
does NOT parse settings.json or model cross-tier precedence, because Claude Code
merges every settings tier (managed > CLI > local > project > user) into the
process environment BEFORE the hook subprocess starts -- the hook sees one
already-merged os.environ. Persisting a PACT_* option via settings.json's `env`
block is therefore transparent to this resolver. (Contrast teammate_mode.py,
which DOES read settings files, because `teammateMode` is a Claude-Code-native
setting with no env-var projection.)
"""
from __future__ import annotations

import os
import sys
from typing import Dict


# ─── Registry: the single source of truth for every PACT option ────────────
# Each row declares an option's type, default, and consumer:
#   type "bool":  parsed by EXACT MEMBERSHIP (see _BOOL_TRUE) -- NOT Python
#                 truthiness, under which bool("0") is True (a fail-UNSAFE slip).
#   type "enum":  validated against "allowed"; an unrecognized value falls back
#                 to "default" AND emits a one-line stderr warning (the tell).
#   consumer "llm":  surfaced to the orchestrator LLM via llm_options() +
#                    session_init's SessionStart injection (markdown flows
#                    cannot read env vars).
#   consumer "hook": read directly by a Python hook (the dispatch/handoff gates).
# Adding an option = adding a row here (Open/Closed); no other edit to this file.
_REGISTRY: Dict[str, dict] = {
    "PACT_PR_GREEDY_FIX": {
        "type": "bool", "default": False, "consumer": "llm",
    },
    "PACT_AUTONOMOUS_SCOPE_DETECTION": {
        "type": "bool", "default": False, "consumer": "llm",
    },
    "PACT_DISPATCH_INLINE_MISSION_MODE": {
        "type": "enum", "default": "warn",
        "allowed": ("warn", "deny", "shadow"), "consumer": "hook",
    },
    "PACT_DISPATCH_VARIETY_MODE": {
        "type": "enum", "default": "warn",
        "allowed": ("warn", "deny", "shadow"), "consumer": "hook",
    },
}

# Canonical TRUE tokens for the bool parse (case-insensitive, post-strip).
# EXACT MEMBERSHIP: any token NOT in this set -- including "0", "2", "maybe",
# "" and an unset var -- resolves to False. This is the fail-SAFE direction (a
# garbled greedy / autonomous flag stays OFF) and deliberately rejects Python
# truthiness, under which bool("0") would be True (security F2).
_BOOL_TRUE = frozenset({"1", "true", "yes", "on"})


def _normalize(raw: str) -> str:
    """Strip + lowercase a raw env value.

    This is the SAME normalization dispatch_gate.py / handoff_ordering_gate.py
    apply to their *_MODE reads before the membership check. Replicated here so
    routing those reads through get_enum stays byte-behavior-identical to the
    pre-resolver ``os.environ.get(name, default).strip().lower()``.
    """
    return raw.strip().lower()


def get_bool(name: str) -> bool:
    """Resolve a bool-typed PACT option from os.environ (live, at call time).

    EXACT-MEMBERSHIP parse: returns True iff the stripped, lowercased value is
    one of _BOOL_TRUE ("1"/"true"/"yes"/"on"). Every other value -- and an
    unset var -- returns the registry default (False for both current bool
    options). Total: never raises; any failure returns the registry default.
    """
    entry = _REGISTRY.get(name, {})
    default = bool(entry.get("default", False))
    try:
        raw = os.environ.get(name)
        if raw is None:
            return default
        return _normalize(raw) in _BOOL_TRUE
    except Exception:  # noqa: BLE001 -- total contract (fail-safe -> default)
        return default


def get_enum(name: str) -> str:
    """Resolve an enum-typed PACT option from os.environ (live, at call time).

    Applies the gates' existing ``.strip().lower()`` normalization, then
    validates against the registry's "allowed" tuple. An UNSET var returns the
    registry default silently (the expected steady state, not a
    misconfiguration). A SET-but-unrecognized value returns the default AND
    emits a one-line stderr warning -- the non-vacuity tell that the
    invalid-value branch is live. Total: never raises; any failure returns the
    registry default.
    """
    entry = _REGISTRY.get(name, {})
    default = str(entry.get("default", ""))
    allowed = entry.get("allowed", ())
    try:
        raw = os.environ.get(name)
        if raw is None:
            return default  # unset -> silent default (not a misconfiguration)
        value = _normalize(raw)
        if value in allowed:
            return value
        # Recognized option, unrecognized value: fall back to default + WARN.
        # The warning never disables the gate (default is the safe mode) and is
        # additive observability -- it does not change the resolved value, so
        # the gate's behavior is identical to the pre-resolver coercion.
        print(
            f"pact_config: {name}={raw!r} is not one of {tuple(allowed)}; "
            f"using default {default!r}",
            file=sys.stderr,
        )
        return default
    except Exception:  # noqa: BLE001 -- total contract (fail-safe -> default)
        return default


def llm_options() -> Dict[str, object]:
    """Return {option_name: resolved_typed_value} for every consumer=="llm"
    option -- the payload session_init injects into the orchestrator's context.

    Values are typed (bool for the two current LLM options), resolved LIVE from
    os.environ via get_bool / get_enum. Total: never raises; a per-option
    failure falls back to that option's registry default inside the getter.
    """
    options: Dict[str, object] = {}
    for name, entry in _REGISTRY.items():
        if entry.get("consumer") != "llm":
            continue
        opt_type = entry.get("type")
        if opt_type == "bool":
            options[name] = get_bool(name)
        elif opt_type == "enum":
            options[name] = get_enum(name)
    return options
