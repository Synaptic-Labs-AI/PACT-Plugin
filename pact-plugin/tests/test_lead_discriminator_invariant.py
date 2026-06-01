"""HYBRID structural invariant for the lead/teammate role discriminator.

This is the drift backstop for the is_lead migration. It pins TWO
complementary shapes into executable form so a future regression fails at
CI time rather than silently under a live tmux session.

THE MIGRATION (context):
    The role decision used to be a NEGATIVE heuristic — ``resolve_agent_name(
    input_data) == ""`` meant "lead". Under tmux that is wrong in BOTH
    directions: resolve_agent_name's Step-4 strips the ``pact-`` prefix and
    returns a NON-empty string for both lead spellings, so the v4.4.0 lead
    took the teammate-bypass branch in every Class-B gate (the three DENY
    gates were silently dead for the lead). The fix is a single POSITIVE
    predicate ``is_lead(input_data)`` keyed on the harness-set top-level
    ``agent_type`` field.

THE TWO LEGS:

  POSITIVE leg (``test_migrated_hooks_route_role_decision_through_is_lead``):
    every hook that makes a lead/teammate role decision routes it through
    ``pact_context.is_lead`` — proven by asserting each migrated hook's
    source contains an ``is_lead(...)`` call. This leg DEPENDS on the
    enumerated migrated-set being complete; it catches a migrated hook that
    REGRESSES away from is_lead.

  NEGATIVE leg (``test_no_hook_reintroduces_a_banned_role_signal``):
    an AST denylist that scans ALL top-level hook files and flags any hook
    that reintroduces a banned role signal —
      (a) an env-var read used as a role discriminator,
      (b) an ``agent_id``-presence role branch, or
      (c) ``resolve_agent_name()`` used as a lead proxy
          (``if resolve_agent_name(...)`` / ``... == "" `` / ``!= ""``).
    This leg does NOT depend on the migrated-set being complete — it is the
    future-drift + incomplete-migration backstop (a brand-new hook that
    keys role off a banned signal is caught even though it is not in any
    enumerated list).

  NAMED-SITE ALLOWLIST (``_SANCTIONED_RESOLVE_AGENT_NAME_SITES``):
    ``resolve_agent_name`` has THREE sanctioned non-role uses (human-readable
    edit attribution / labels), which the negative leg must NOT flag. The
    allowlist is keyed by (file, enclosing-function) and is deliberately
    EXPLICIT rather than a structural discriminator: distinguishing
    "resolve_agent_name as a lead proxy" from "resolve_agent_name for a
    label" is genuinely hard to express structurally, and an auditable named
    list makes every sanctioned site a deliberate, documented decision.
    Adding a new sanctioned site MUST require a deliberate allowlist + test
    update — that maintenance cost is a FEATURE (same philosophy as the
    "new field must update this test" phantom-green guard in
    test_first_observable_write_misfire_invariant.py).

PHANTOM-GREEN PROTECTION:
    The negative leg's value is only real if the detector actually FIRES on
    a banned signal. ``test_negative_leg_detector_fires_on_*`` feed synthetic
    source containing each banned shape and assert the detector flags it —
    proving the leg is coupled to the regression, not a vacuous pass. A
    non-vacuity guard (``test_negative_leg_scans_a_nonempty_hook_set`` +
    ``test_allowlist_sites_are_all_real``) ensures the scan never silently
    degrades to "found nothing because it looked at nothing".

Modeled on tests/test_first_observable_write_misfire_invariant.py.
"""

import ast
from pathlib import Path

import pytest


HOOKS_DIR = Path(__file__).parent.parent / "hooks"


# ---------------------------------------------------------------------------
# POSITIVE leg: the migrated hooks and the role-decision symbol they route to.
# ---------------------------------------------------------------------------

# Hooks that make a lead/teammate role decision and MUST route it through
# is_lead. Each entry is a top-level hook filename relative to hooks/. This
# set IS the migrated Class-A (session_init, postcompact_archive) + Class-B
# (the 5 teammate-bypass gates) surface. A migrated hook dropping its is_lead
# call (regressing to the old heuristic) is caught here.
MIGRATED_HOOKS: tuple[str, ...] = (
    "session_init.py",          # Class-A: 4 lead-only writes
    "postcompact_archive.py",   # Class-A2 (#881): compact-summary write
    "bootstrap_gate.py",        # Class-B DENY
    "pin_staleness_gate.py",    # Class-B DENY
    "pin_caps_gate.py",         # Class-B DENY
    "bootstrap_prompt_gate.py", # Class-B inject
    "bootstrap_marker_writer.py",  # Class-B marker write
)


# ---------------------------------------------------------------------------
# NEGATIVE leg: named-site allowlist for sanctioned resolve_agent_name uses.
# ---------------------------------------------------------------------------

# resolve_agent_name's sanctioned non-role uses, keyed (file, function). These
# are human-readable edit-attribution / label uses — NOT lead-proxy role
# decisions — so the negative leg must NOT flag them. EXPLICIT by design: a
# new sanctioned site requires a deliberate edit here + a documented reason.
#
# Why each site is sanctioned:
#   file_tracker.py / main:
#       resolve_agent_name supplies the human-readable LABEL for the editor in
#       the file-edit tracking record. The ROLE/uniqueness decision in
#       file_tracker uses the composite (agent_name, session_id) editor KEY,
#       NOT a lead/teammate verdict — resolve_agent_name here is attribution,
#       never a lead proxy. (#878 NEW-1 KEPT-for-label.)
#   file_tracker.py / get_environment_delta:
#       reads the stored ``agent`` LABEL off tracking entries to attribute
#       drift to a named editor — attribution, not a role gate. (Listed for
#       auditability even though it reads a stored label rather than calling
#       resolve_agent_name directly, so a future refactor that switches it to
#       a live resolve_agent_name call stays pre-sanctioned and documented.)
#   shared/pact_context.py / resolve_agent_name:
#       the function's OWN definition + its internal agent_id name-resolution
#       (Step 1-3) — this is the name-resolver itself, not a consumer keying
#       role off it.
_SANCTIONED_RESOLVE_AGENT_NAME_SITES: frozenset[tuple[str, str]] = frozenset({
    ("file_tracker.py", "main"),
    ("file_tracker.py", "get_environment_delta"),
    ("shared/pact_context.py", "resolve_agent_name"),
})


def _hook_files() -> list[Path]:
    """All top-level hook .py files (excludes __init__, shared/, tests)."""
    return sorted(
        p for p in HOOKS_DIR.glob("*.py")
        if p.name != "__init__.py"
    )


def _enclosing_function(tree: ast.AST, target: ast.AST) -> str:
    """Return the name of the FunctionDef enclosing ``target``, or "" if the
    node sits at module scope. Walks the tree once building a child->name map.
    """
    enclosing = ""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                if child is target:
                    enclosing = node.name
                    # keep the INNERMOST function: a later (nested) match
                    # overwrites an outer one because ast.walk yields the
                    # outer FunctionDef first.
    return enclosing


# ---------------------------------------------------------------------------
# Banned-signal detectors (operate on an AST tree + a file label).
# ---------------------------------------------------------------------------

def _find_resolve_agent_name_role_uses(
    tree: ast.AST, file_label: str
) -> list[tuple[str, int]]:
    """Flag resolve_agent_name() calls used as a LEAD PROXY (banned), skipping
    the named-site allowlist.

    A "lead proxy" is any resolve_agent_name() call whose result feeds a role
    branch. We treat EVERY call site as a candidate and rely on the named-site
    allowlist to exempt the sanctioned attribution/label uses — this is
    deliberately conservative (a new resolve_agent_name call in a non-
    allowlisted site is flagged regardless of whether it textually looks like
    a role branch), so that drift toward "resolve_agent_name == lead" cannot
    sneak in through a site the structural matcher fails to recognize.

    Returns (function_name, lineno) for each NON-allowlisted call.
    """
    offending: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None
        )
        if name != "resolve_agent_name":
            continue
        fn = _enclosing_function(tree, node)
        if (file_label, fn) in _SANCTIONED_RESOLVE_AGENT_NAME_SITES:
            continue
        offending.append((fn, node.lineno))
    return offending


def _find_agent_id_role_uses(
    tree: ast.AST, file_label: str
) -> list[tuple[str, int]]:
    """Flag reads of the ``agent_id`` stdin field used as a role discriminator.

    Detects ``input_data.get("agent_id")`` / ``input_data["agent_id"]`` style
    reads. The role decision must key on ``agent_type`` (via is_lead), never on
    ``agent_id``-presence — agent_id is absent under tmux and its presence/
    absence is exactly the #812 drift hazard.

    validate_handoff.py is EXEMPT: its agent_id read is a role-CLASS check
    (``is_pact_agent``), explicitly deferred to the #812 follow-up per the
    plan's scope decision — NOT a lead/teammate discriminator. Listed by file
    so the exemption is auditable.
    """
    if file_label == "validate_handoff.py":
        return []
    offending: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        # input_data.get("agent_id", ...)
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == "get"
                    and node.args):
                arg0 = node.args[0]
                if isinstance(arg0, ast.Constant) and arg0.value == "agent_id":
                    offending.append((_enclosing_function(tree, node), node.lineno))
        # input_data["agent_id"]
        if isinstance(node, ast.Subscript):
            sl = node.slice
            if isinstance(sl, ast.Constant) and sl.value == "agent_id":
                offending.append((_enclosing_function(tree, node), node.lineno))
    return offending


def _find_env_var_role_reads(
    tree: ast.AST, file_label: str
) -> list[tuple[str, int]]:
    """Flag os.environ reads of a ROLE-bearing env var.

    The role decision must derive from the harness-set ``agent_type`` stdin
    field, never from an environment variable (the old in-process model used
    env vars; under tmux they are unreliable role signals). Matches
    os.environ.get("...") / os.getenv("...") / os.environ["..."] whose key
    name contains a role token (AGENT / TEAMMATE / LEAD / ORCHESTRAT / ROLE),
    EXCLUDING the benign infra vars (CLAUDE_PROJECT_DIR, CLAUDE_PLUGIN_ROOT,
    CLAUDE_ENV_FILE — paths, not roles).
    """
    role_tokens = ("AGENT", "TEAMMATE", "LEAD", "ORCHESTRAT", "ROLE")
    benign = ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT", "CLAUDE_ENV_FILE")
    offending: list[tuple[str, int]] = []

    def _key_is_role(key: str) -> bool:
        up = key.upper()
        if up in benign:
            return False
        return any(tok in up for tok in role_tokens)

    def _receiver_is_environ(attr_node: ast.Attribute) -> bool:
        """True iff ``attr_node`` is ``<...>.environ.<attr>`` — so we only
        match an os.environ ``.get`` and never ``input_data.get(...)`` /
        ``tool_input.get(...)`` (those carry stdin role FIELDS, not env vars,
        and are handled by the dedicated agent_id / is_lead detectors)."""
        recv = attr_node.value
        return isinstance(recv, ast.Attribute) and recv.attr == "environ"

    for node in ast.walk(tree):
        # os.environ.get("KEY") / os.getenv("KEY")
        if isinstance(node, ast.Call):
            func = node.func
            if not (isinstance(func, ast.Attribute) and node.args):
                continue
            # `.get` must be on an os.environ receiver; `getenv` is always env.
            is_env_get = (
                (func.attr == "get" and _receiver_is_environ(func))
                or func.attr == "getenv"
            )
            if is_env_get:
                arg0 = node.args[0]
                if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    if _key_is_role(arg0.value):
                        offending.append((_enclosing_function(tree, node), node.lineno))
        # os.environ["KEY"]
        if isinstance(node, ast.Subscript):
            value = node.value
            is_environ = (
                isinstance(value, ast.Attribute) and value.attr == "environ"
            )
            sl = node.slice
            if is_environ and isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                if _key_is_role(sl.value):
                    offending.append((_enclosing_function(tree, node), node.lineno))
    return offending


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


# ===========================================================================
# POSITIVE leg
# ===========================================================================

class TestPositiveLeg:
    """Every migrated hook routes its role decision through is_lead."""

    @pytest.mark.parametrize("hook_name", MIGRATED_HOOKS)
    def test_migrated_hook_calls_is_lead(self, hook_name):
        """Each migrated hook's source contains an is_lead(...) call.

        A migrated hook that REGRESSES away from is_lead (back to the old
        resolve_agent_name heuristic, or to any other discriminator) drops
        the call and fails here. Pinned by AST so a mention of ``is_lead`` in
        a comment/docstring does NOT satisfy it — only a real Call node.
        """
        path = HOOKS_DIR / hook_name
        assert path.exists(), f"Migrated hook {hook_name} not found at {path}"
        tree = _parse(path)
        calls_is_lead = any(
            isinstance(n, ast.Call)
            and (
                (isinstance(n.func, ast.Name) and n.func.id == "is_lead")
                or (isinstance(n.func, ast.Attribute) and n.func.attr == "is_lead")
            )
            for n in ast.walk(tree)
        )
        assert calls_is_lead, (
            f"{hook_name} makes a lead/teammate role decision but does NOT "
            f"call is_lead(). If this hook regressed to the old "
            f"resolve_agent_name heuristic (or any non-is_lead discriminator), "
            f"restore the is_lead routing. If the hook legitimately no longer "
            f"makes a role decision, remove it from MIGRATED_HOOKS with a "
            f"documented reason."
        )

    def test_migrated_set_is_the_full_class_a_and_class_b_surface(self):
        """Documentation-in-code: the migrated set is exactly the 7 hooks the
        plan enumerates (2 Class-A + 5 Class-B). Pinning the cardinality means
        a future edit that drops a hook from the positive leg is a deliberate,
        reviewed change — not a silent omission that would weaken the leg.
        """
        assert len(MIGRATED_HOOKS) == 7, (
            f"MIGRATED_HOOKS has {len(MIGRATED_HOOKS)} entries; expected 7 "
            f"(session_init + postcompact_archive + 5 Class-B gates). If the "
            f"migrated surface genuinely changed, update this count with a "
            f"documented reason."
        )


# ===========================================================================
# NEGATIVE leg
# ===========================================================================

class TestNegativeLeg:
    """No hook reintroduces a banned role signal (the drift backstop)."""

    def test_no_hook_uses_resolve_agent_name_as_lead_proxy(self):
        """AST denylist across ALL hooks: no non-allowlisted resolve_agent_name
        call. Independent of the migrated-set — a brand-new hook keying role
        off resolve_agent_name is caught even though it is in no enumerated
        list.
        """
        offending: list[str] = []
        for path in _hook_files():
            label = path.name
            for fn, lineno in _find_resolve_agent_name_role_uses(_parse(path), label):
                offending.append(f"{label}:{lineno} (in {fn or '<module>'})")
        assert offending == [], (
            f"resolve_agent_name() called outside the sanctioned named-site "
            f"allowlist: {offending}. resolve_agent_name as a role/lead proxy "
            f"is the migrated-away-from anti-pattern. If this is a NEW "
            f"sanctioned attribution/label use, add (file, function) to "
            f"_SANCTIONED_RESOLVE_AGENT_NAME_SITES with a documented reason."
        )

    def test_no_hook_branches_role_on_agent_id_presence(self):
        """AST denylist: no hook reads the agent_id stdin field for a role
        decision (validate_handoff exempt — #812 role-class, deferred).
        agent_id-presence is the #812 drift hazard — absent under tmux.
        """
        offending: list[str] = []
        for path in _hook_files():
            label = path.name
            for fn, lineno in _find_agent_id_role_uses(_parse(path), label):
                offending.append(f"{label}:{lineno} (in {fn or '<module>'})")
        assert offending == [], (
            f"agent_id read for a role decision: {offending}. The role "
            f"discriminator must key on agent_type (via is_lead), never on "
            f"agent_id-presence (absent under tmux; the #812 drift hazard). "
            f"If this is a sanctioned non-role agent_id use, exempt the file "
            f"in _find_agent_id_role_uses with a documented reason."
        )

    def test_no_hook_reads_a_role_env_var(self):
        """AST denylist: no hook derives role from a role-bearing env var.
        The role signal is harness-set agent_type stdin, not the environment.
        """
        offending: list[str] = []
        for path in _hook_files():
            label = path.name
            for fn, lineno in _find_env_var_role_reads(_parse(path), label):
                offending.append(f"{label}:{lineno} (in {fn or '<module>'})")
        assert offending == [], (
            f"role-bearing env-var read: {offending}. Role must derive from "
            f"the harness-set agent_type stdin field, never an env var (the "
            f"old in-process model's unreliable-under-tmux signal)."
        )


# ===========================================================================
# PHANTOM-GREEN PROTECTION — prove each detector actually fires
# ===========================================================================

class TestNegativeLegDetectorsFire:
    """Synthetic-source proofs: each banned-signal detector FIRES on the
    shape it is meant to catch. Without these, a detector that silently
    matches nothing would make the negative leg a vacuous always-pass.
    """

    def test_resolve_agent_name_detector_fires_on_non_allowlisted_call(self):
        src = (
            "def some_gate(input_data):\n"
            "    if resolve_agent_name(input_data):\n"
            "        return None  # teammate bypass — the banned old heuristic\n"
        )
        tree = ast.parse(src)
        hits = _find_resolve_agent_name_role_uses(tree, "some_new_gate.py")
        assert hits, (
            "resolve_agent_name detector did NOT fire on a non-allowlisted "
            "call — the negative leg would be vacuous."
        )

    def test_resolve_agent_name_detector_respects_allowlist(self):
        """The same call shape inside an ALLOWLISTED (file, function) is NOT
        flagged — proving the allowlist actually exempts, so the detector
        isn't trivially flagging everything.
        """
        src = (
            "def main(input_data):\n"
            "    agent_name = resolve_agent_name(input_data)  # label use\n"
            "    return agent_name\n"
        )
        tree = ast.parse(src)
        hits = _find_resolve_agent_name_role_uses(tree, "file_tracker.py")
        assert hits == [], (
            "Allowlisted (file_tracker.py, main) resolve_agent_name use was "
            "flagged — the named-site allowlist is not being honored."
        )

    def test_agent_id_detector_fires_on_get(self):
        src = (
            "def gate(input_data):\n"
            "    if input_data.get('agent_id'):\n"
            "        return None\n"
        )
        tree = ast.parse(src)
        hits = _find_agent_id_role_uses(tree, "some_new_gate.py")
        assert hits, "agent_id .get detector did NOT fire."

    def test_agent_id_detector_fires_on_subscript(self):
        src = (
            "def gate(input_data):\n"
            "    aid = input_data['agent_id']\n"
            "    return aid\n"
        )
        tree = ast.parse(src)
        hits = _find_agent_id_role_uses(tree, "some_new_gate.py")
        assert hits, "agent_id subscript detector did NOT fire."

    def test_agent_id_detector_exempts_validate_handoff(self):
        """validate_handoff's agent_id read is the deferred #812 role-class
        check — must NOT be flagged.
        """
        src = (
            "def main(input_data):\n"
            "    agent_id = input_data.get('agent_id', '')\n"
            "    return is_pact_agent(agent_id)\n"
        )
        tree = ast.parse(src)
        hits = _find_agent_id_role_uses(tree, "validate_handoff.py")
        assert hits == [], (
            "validate_handoff.py agent_id read was flagged — the #812 "
            "role-class exemption is not being honored."
        )

    def test_env_var_detector_fires_on_role_token(self):
        src = (
            "import os\n"
            "def gate(input_data):\n"
            "    if os.environ.get('CLAUDE_AGENT_TYPE') == 'lead':\n"
            "        return None\n"
        )
        tree = ast.parse(src)
        hits = _find_env_var_role_reads(tree, "some_new_gate.py")
        assert hits, "env-var role detector did NOT fire on a role token."

    def test_env_var_detector_ignores_benign_infra_vars(self):
        """CLAUDE_PROJECT_DIR / CLAUDE_PLUGIN_ROOT are paths, not roles — must
        NOT be flagged (else the detector would scream on every hook).
        """
        src = (
            "import os\n"
            "def main():\n"
            "    pd = os.environ.get('CLAUDE_PROJECT_DIR', '.')\n"
            "    pr = os.environ.get('CLAUDE_PLUGIN_ROOT', '')\n"
            "    return pd, pr\n"
        )
        tree = ast.parse(src)
        hits = _find_env_var_role_reads(tree, "session_init.py")
        assert hits == [], (
            f"Benign infra env vars were flagged as role reads: {hits}."
        )

    def test_env_var_detector_ignores_stdin_field_gets(self):
        """A ``.get("agent_type")`` on a STDIN dict (input_data / tool_input)
        is NOT an env-var read — it is a legitimate stdin role-field access
        (is_lead / classify_session_role do exactly this). The env-var
        detector must require an ``os.environ`` receiver, else it would
        false-positive on every is_lead call site. This pins the receiver
        check that the first pass got wrong.
        """
        src = (
            "def is_lead(input_data):\n"
            "    return input_data.get('agent_type') in LEAD_AGENT_TYPES\n"
            "def gate(input_data):\n"
            "    aid = input_data.get('agent_id', '')\n"
            "    role = input_data['tool_input'].get('agent_role')\n"
            "    return aid, role\n"
        )
        tree = ast.parse(src)
        hits = _find_env_var_role_reads(tree, "session_init.py")
        assert hits == [], (
            f"Stdin-field .get() calls were misflagged as env-var reads: "
            f"{hits}. The detector must require an os.environ receiver."
        )


# ===========================================================================
# NON-VACUITY GUARDS — the scan never silently degrades to "looked at nothing"
# ===========================================================================

class TestNonVacuity:
    """Guards so a false-clean from an empty scan is impossible."""

    def test_negative_leg_scans_a_nonempty_hook_set(self):
        """The hook-file glob resolves to a substantial set. If this ever
        returns near-empty (wrong cwd, moved dir), every negative-leg
        assertion would vacuously pass — this canary fails first instead.
        """
        files = _hook_files()
        assert len(files) >= 20, (
            f"Hook-file scan found only {len(files)} files (expected >= 20). "
            f"The negative-leg AST denylist would be near-vacuous. Is "
            f"HOOKS_DIR ({HOOKS_DIR}) resolving correctly?"
        )

    def test_canary_a_known_hook_is_in_the_scan_set(self):
        """Positive canary: a hook KNOWN to call resolve_agent_name
        (file_tracker.py) is actually in the scanned set, so the scan is
        exercising real files — not an empty/false-clean list.
        """
        names = {p.name for p in _hook_files()}
        assert "file_tracker.py" in names, (
            "file_tracker.py (a known resolve_agent_name caller) is NOT in "
            "the scanned hook set — the scan is not seeing real files."
        )

    def test_allowlist_sites_are_all_real(self):
        """Every allowlisted (file, function) actually EXISTS in the source.
        A stale allowlist entry (renamed/removed function) would silently
        exempt nothing — or worse, mask a future real-site collision. Pinning
        existence keeps the allowlist honest.
        """
        missing: list[str] = []
        for rel_file, fn_name in _SANCTIONED_RESOLVE_AGENT_NAME_SITES:
            path = HOOKS_DIR / rel_file
            if not path.exists():
                missing.append(f"{rel_file} (file missing)")
                continue
            tree = _parse(path)
            fn_names = {
                n.name for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            if fn_name not in fn_names:
                missing.append(f"{rel_file}::{fn_name} (function missing)")
        assert missing == [], (
            f"Stale allowlist entries (no longer in source): {missing}. "
            f"Remove or correct them so the allowlist stays auditable."
        )

    def test_allowlist_is_nonempty(self):
        """Documentation-in-code: the allowlist MUST carry at least one site
        (resolve_agent_name has genuine sanctioned label uses). An empty
        allowlist would mean either the detector is flagging everything or
        the label uses were removed — both warrant explicit review.
        """
        assert _SANCTIONED_RESOLVE_AGENT_NAME_SITES, (
            "_SANCTIONED_RESOLVE_AGENT_NAME_SITES is empty. resolve_agent_name "
            "has sanctioned label uses (file_tracker); an empty allowlist is "
            "a signal the negative-leg semantics changed — review explicitly."
        )
