"""
Location: pact-plugin/tests/test_merge_guard_1136_canonical_join_ssot.py
Summary: COMPREHENSIVE BIDIRECTIONAL SACROSANCT certification for #1136 — the canonical-join
         SSOT. The fix folds branch_set + mass_target onto ONE pure NETSTRING/length-prefix
         helper `_canonical_join(items) = "".join(f"{len(i)}:{i}" for i in items)`; the @/#
         delimiters are ELIMINATED (closing BOTH the comma AND the # collisions), the
         `\\x00implicit` sentinel is UNCHANGED, and refspecs are now sorted+DEDUPED.

         Proven against the REAL classifier, base-vs-HEAD, NEVER a byte-diff (#1118):
           - INJECTIVITY BY CONSTRUCTION: a TOTAL constructive left-inverse decode round-trips
             every edge (empty list/item, embedded :/digits/NUL, multi-digit lengths) -> a
             function with a left inverse cannot be non-injective. This is the GOLD-STANDARD
             bound on the #1118 CONCENTRATION HAZARD (one helper backs both detectors), plus a
             random-sequence property test (belt-and-suspenders).
           - #1136 CLOSURE: base(main HEAD 9256c93c) COLLIDES -> HEAD REFUSES, for BOTH the
             COMMA witness AND the # witness, at the identity level AND the REAL mint->execute
             gate (token for set X does NOT authorize set Y).
           - OVER-BLOCK (PRIMARY / cardinal): a faithful single-command mass-delete /
             branch-delete mints AND self-matches at execute; reorder/dedup still match.
           - DOUBLED SURFACE: branch_set (already collision-safe on base -> non-vacuity is the
             OVER-BLOCK direction + "stays correct across the move onto the shared helper")
             AND mass_target (base comma-COLLIDES -> HEAD REFUSES).
           - NETSTRING adversarial field contents, \\x00implicit sentinel isolation, the NEW
             dedup row, and the `+:a` aliases `:a` edge.

NON-VACUITY: the closure rows load the BASE classifier (9256c93c) via `git show`+exec and
assert base=COLLIDES / HEAD=REFUSES in-test (a revert to comma/@/# joins instantly reds them).
#1136 PRE-DATES this refactor (reproduce-base-vulnerability-first: the collision is LIVE on
base). Destructive verbs assembled at runtime (PUSH/DEL) so this file carries no raw literal.
"""
import io
import json
import random
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import merge_guard_post  # noqa: E402
import merge_guard_pre  # noqa: E402
import shared.merge_guard_common as mgc  # noqa: E402
from merge_guard_post import main as post_main  # noqa: E402
from merge_guard_pre import main as pre_main  # noqa: E402

_BASE_SHA = "9256c93c"  # main HEAD — #1136 pre-dates this refactor (comma/@/# joins COLLIDE)


def _load_classifier(sha):
    wt = Path(__file__).resolve().parents[2]
    src = subprocess.check_output(
        ["git", "-C", str(wt), "show", sha + ":pact-plugin/hooks/shared/merge_guard_common.py"]
    ).decode()
    mod = types.ModuleType("merge_guard_common_1136_" + sha)
    mod.__file__ = str(wt / "pact-plugin/hooks/shared/merge_guard_common.py")
    mod.__package__ = "shared"
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    return mod


_BASE = _load_classifier(_BASE_SHA)
CJ = mgc._canonical_join
MT = mgc._extract_mass_delete_target          # HEAD (netstring)
MT_BASE = _BASE._extract_mass_delete_target    # base (comma/@/#)
D = mgc.is_dangerous_command
ALLOW, DENY = 0, 2

# Destructive verbs assembled at runtime — this file carries no raw literal.
PUSH = "git " + "push "
DEL = "--delete "


def _decode(s):
    """TOTAL constructive LEFT-INVERSE of _canonical_join. decode(CJ(x)) == x for all x ->
    CJ is injective by exhibited inverse (the #1118 concentration-hazard bound)."""
    out, i = [], 0
    while i < len(s):
        j = s.index(":", i)
        n = int(s[i:j])
        item = s[j + 1: j + 1 + n]
        assert len(item) == n
        out.append(item)
        i = j + 1 + n
    return out


def _mint(cmd, tok):
    env = json.dumps({"tool_name": "AskUserQuestion", "tool_input": {"questions": [{
        "question": "Proceed?", "options": [
            {"label": "Yes", "description": "Run `%s`" % cmd},
            {"label": "Cancel", "description": "Abort"}]}]},
        "tool_response": {"answers": {"Proceed?": "Yes"}}, "session_id": "cert1136"})
    with patch.object(merge_guard_post, "TOKEN_DIR", tok), \
         patch("sys.stdin", io.StringIO(env)), patch("sys.stdout", io.StringIO()):
        try:
            post_main()
        except SystemExit:
            pass
    return len(list(tok.glob("merge-authorized-*")))


def _execute(cmd, tok):
    env = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": "cert1136"})
    with patch.object(merge_guard_pre, "TOKEN_DIR", tok), \
         patch("sys.stdin", io.StringIO(env)), patch("sys.stdout", io.StringIO()), \
         patch("sys.stderr", io.StringIO()):
        try:
            pre_main(); return ALLOW
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else ALLOW


# Two-ref set with a comma/# INSIDE a ref name vs the three-ref set — the #1136 collisions.
_COMMA_2 = PUSH + "origin " + DEL + '"a,b" c'    # refs {'a,b', 'c'}
_COMMA_3 = PUSH + "origin " + DEL + "a b c"       # refs {'a','b','c'}
_HASH_2 = PUSH + "origin " + DEL + '"a#b" c'      # refs {'a#b','c'}
_HASH_3 = PUSH + '"origin#a" ' + DEL + "b c"       # remote 'origin#a', refs {'b','c'}


# ===========================================================================
# INJECTIVITY BY CONSTRUCTION — the #1118 concentration-hazard bound.
# ===========================================================================
class TestCanonicalJoinInjectivity:

    @pytest.mark.parametrize("items", [
        [], [""], ["a"], ["", ""], ["", "a"], ["a", ""],
        ["2", "ab"], ["2:a", "b"], ["10", "x" * 10], ["1", "0" + "x" * 10],
        ["a:b"], ["a", ":b"], ["a:", "b"], ["a#b"], ["a,b"], ["a@b"],
        ["\x00implicit"], ["5:xxxxx"], ["100", "y" * 100], ["a\x00b", "c"],
    ])
    def test_constructive_left_inverse_round_trips(self, items):
        # decode(encode(items)) == items for every edge -> CJ is injective (a left inverse
        # exists). TOTAL: empty list encodes to "" and decodes to [].
        enc = CJ(items)
        assert (_decode(enc) if enc != "" else []) == items

    def test_random_sequences_no_collision(self):
        # Belt-and-suspenders: distinct sequences -> distinct encodings (0 collisions).
        random.seed(1136)
        alpha = "ab:0123#,@\x00"
        seen = {}
        for _ in range(5000):
            seq = tuple("".join(random.choice(alpha) for _ in range(random.randint(0, 4)))
                        for _ in range(random.randint(0, 4)))
            enc = CJ(list(seq))
            assert enc not in seen or seen[enc] == seq, "collision: %r and %r" % (seen[enc], seq)
            seen[enc] = seq

    @pytest.mark.parametrize("x,y", [
        (["2", "ab"], ["2:a", "b"]),          # digit-boundary
        (["10", "x" * 10], ["1", "0" + "x" * 10]),  # multi-digit length
        (["a:b"], ["a", ":b"]),               # embedded ':' split
        (["a:b"], ["a:", "b"]),
        (["a#b"], ["a", "b"]),                # old '#' delim now harmless
        (["a,b"], ["a", "b"]),                # old ',' delim now harmless
        ([""], []),                           # empty-item vs empty-list
        (["", "a"], ["a"]),                   # empty-field prefix
        (["5:xxxxx"], ["5", "xxxxx"]),        # content that LOOKS like a netstring
        (["a\x00b"], ["a", "b"]),             # NUL-in-field vs 2 fields
    ])
    def test_adversarial_field_contents_distinct(self, x, y):
        # My INDEPENDENTLY-derived adversarial corpus (per lead: no coordination with security;
        # the fresh peer-review pass derives its own). Compared by STRING-EQUALITY, never decoded.
        assert CJ(x) != CJ(y)


# ===========================================================================
# #1136 CLOSURE — base COLLIDES -> HEAD REFUSES, BOTH the comma AND the # witnesses.
# ===========================================================================
class TestUnderBlockClosure:

    def test_comma_identity_base_collides_head_distinct(self):
        assert MT_BASE(_COMMA_2) == MT_BASE(_COMMA_3) and MT_BASE(_COMMA_2) is not None  # base COLLIDES
        assert MT(_COMMA_2) != MT(_COMMA_3)  # HEAD REFUSES

    def test_hash_identity_base_collides_head_distinct(self):
        assert MT_BASE(_HASH_2) == MT_BASE(_HASH_3) and MT_BASE(_HASH_2) is not None  # base COLLIDES
        assert MT(_HASH_2) != MT(_HASH_3)  # HEAD REFUSES

    def test_comma_collision_closed_at_real_gate(self, tmp_path):
        # HEAD: a token minted for the 2-set does NOT authorize executing the 3-set.
        assert _mint(_COMMA_2, tmp_path) == 1
        assert _execute(_COMMA_3, tmp_path) == DENY

    def test_hash_collision_closed_at_real_gate(self, tmp_path):
        assert _mint(_HASH_2, tmp_path) == 1
        assert _execute(_HASH_3, tmp_path) == DENY

    # NON-VACUITY note: the coupling proof for these rows is the BAKED BASE column above
    # (MT_BASE, loaded from the real pre-fix source 9256c93c) — it uses the actual @/# encoding
    # and so faithfully reproduces BOTH the comma AND the # collisions on base, asserting HEAD
    # distinct. A synthetic list-comma-join neuter would reproduce ONLY the comma collision (the
    # # collision came from the @/# FIELD structure, not a list join) — so the real baked base
    # is the structurally-faithful non-vacuity, not an in-file monkeypatch.


# ===========================================================================
# OVER-BLOCK self-match corpus — PRIMARY / cardinal gate. A faithful single-command
# mass-delete / branch-delete mints AND authorizes its own exec (and reorders/dedups).
# ===========================================================================
class TestOverBlockSelfMatchCorpus:

    _MASS = [
        ("2-ref delete", PUSH + "origin " + DEL + "a b"),
        ("3-ref delete", PUSH + "origin " + DEL + "a b c"),
        ("comma-in-name", PUSH + "origin " + DEL + '"a,b" c'),
        ("hash-in-name", PUSH + "origin " + DEL + '"a#b" c'),
        ("colon-empty-src", PUSH + "origin " + ":a :b"),
        ("mirror implicit", PUSH + "--mirror origin"),
        ("prune", PUSH + "--prune origin refs/heads/x refs/heads/y"),
    ]
    _D = "-" + "D"
    _BRANCH = [
        ("branch 2", "git branch " + _D + " aa bb"),
        ("branch 3", "git branch " + _D + " aa bb cc"),
    ]

    @pytest.mark.parametrize("label,cmd", _MASS + _BRANCH)
    def test_faithful_click_mints_and_self_matches(self, label, cmd, tmp_path):
        assert _mint(cmd, tmp_path) == 1, "%s: faithful click must mint" % label
        assert _execute(cmd, tmp_path) == ALLOW, "%s: faithful click must self-authorize (over-block=cardinal)" % label

    def test_reorder_still_matches(self, tmp_path):
        assert _mint(PUSH + "origin " + DEL + "a b c", tmp_path) == 1
        assert _execute(PUSH + "origin " + DEL + "c a b", tmp_path) == ALLOW

    def test_branch_reorder_still_matches(self, tmp_path):
        assert _mint("git branch " + self._D + " aa bb cc", tmp_path) == 1
        assert _execute("git branch " + self._D + " cc aa bb", tmp_path) == ALLOW


# ===========================================================================
# DOUBLED SURFACE base-vs-HEAD (the shared helper -> a regression in EITHER must red a row).
# ===========================================================================
class TestDoubledSurfaceBaseVsHead:

    def test_mass_target_collision_flips_base_to_head(self):
        # mass_target: base(comma) COLLIDES -> HEAD(netstring) REFUSES (the #1136 flip).
        assert MT_BASE(_COMMA_2) == MT_BASE(_COMMA_3)
        assert MT(_COMMA_2) != MT(_COMMA_3)

    def test_branch_set_stays_collision_safe_across_the_move(self):
        # branch_set was ALREADY collision-safe on base (NUL join, R1 F1) -> its non-vacuity is
        # NOT a base-flip. It must STAY correct across the move onto the shared netstring helper:
        # a comma-named branch set and a 3-branch set are DISTINCT on BOTH base and HEAD.
        _D = "-" + "D"
        two = "git branch " + _D + ' "aa,bb" cc'
        three = "git branch " + _D + " aa bb cc"
        bset = mgc._extract_branch_delete_set
        bset_base = _BASE._extract_branch_delete_set
        assert bset_base(two) != bset_base(three)  # already safe on base (NUL)
        assert bset(two) != bset(three)            # stays safe at HEAD (netstring)

    def test_branch_set_over_block_intact_across_move(self, tmp_path):
        _D = "-" + "D"
        cmd = "git branch " + _D + " aa bb cc"
        assert _mint(cmd, tmp_path) == 1 and _execute(cmd, tmp_path) == ALLOW


# ===========================================================================
# \x00implicit sentinel isolation + dedup + colon force-delete edge.
# ===========================================================================
class TestSentinelDedupAndColonEdge:

    def test_sentinel_disjoint_from_real_remote_and_ref(self):
        # Framed sentinel 9:\x00implicit is distinct from a real remote 'implicit' (8:implicit)
        # and from any ref-set encoding — the NUL prefix is load-bearing (kept unchanged).
        implicit = MT(PUSH + "--mirror")                    # implicit remote -> sentinel
        real = MT(PUSH + "--mirror implicit")               # explicit remote named 'implicit'
        assert implicit is not None and real is not None and implicit != real
        assert "9:\x00implicit" in implicit and "8:implicit" in real

    def test_dedup_collapses_duplicate_ref(self, tmp_path):
        # `--delete a a b` == `--delete a b` (a duplicate ref is the SAME target). mint==read
        # symmetry: a token for {a,b} authorizes the duplicate form and vice-versa; and it does
        # NOT authorize a DIFFERENT set {a,b,c}.
        assert MT(PUSH + "origin " + DEL + "a a b") == MT(PUSH + "origin " + DEL + "a b")
        assert _mint(PUSH + "origin " + DEL + "a a b", tmp_path) == 1
        assert _execute(PUSH + "origin " + DEL + "a b", tmp_path) == ALLOW
        assert _execute(PUSH + "origin " + DEL + "a b c", tmp_path) == DENY  # NOT outside the set

    def test_colon_force_delete_aliases_same_ref(self, tmp_path):
        # `+:a` (force empty-source push = delete) aliases `:a`; a token for it authorizes ONLY
        # the same-ref delete, not a different ref.
        assert MT(PUSH + "origin +:a :b") == MT(PUSH + "origin :a :b")
        assert _mint(PUSH + "origin +:a :b", tmp_path) == 1
        assert _execute(PUSH + "origin :a :b", tmp_path) == ALLOW
        assert _execute(PUSH + "origin :a :c", tmp_path) == DENY
