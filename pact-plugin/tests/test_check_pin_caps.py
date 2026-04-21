"""
Tests for scripts/check_pin_caps.py — CLI JSON-contract enforcement,
fail-open behavior, and exit-code semantics.

Risk tier: CRITICAL (enforcement CLI; /PACT:pin-memory depends on JSON
contract).
"""

import io
import json
import sys
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent))

from helpers import make_claude_md_with_pins, make_pin_entry  # noqa: E402


@pytest.fixture
def patched_claude_md(tmp_path, monkeypatch):
    """Write a CLAUDE.md and patch get_project_claude_md_path resolution."""
    def _write(content):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(content, encoding="utf-8")
        # check_pin_caps.py binds the symbol at import — patch there.
        # Only the consumer-side patch is load-bearing; prior staleness-side
        # patch was decorative and risked dead-patch status if staleness.py
        # refactors its resolution callsite.
        import check_pin_caps
        monkeypatch.setattr(
            check_pin_caps, "get_project_claude_md_path", lambda: claude_md
        )
        return claude_md
    return _write


def _run_cli(argv, stdin_text=None):
    """Invoke check_pin_caps.main and capture stdout + return code.

    When `stdin_text` is provided, sys.stdin is replaced with an
    `io.StringIO` carrying that text for the duration of the call.
    This exercises the `--body-from-stdin` ingestion path without
    requiring a real pipe: `sys.stdin.read()` returns the full text
    verbatim, matching the shell-pipe contract the CLI promises.
    """
    import check_pin_caps
    buf = io.StringIO()
    stdin_patch = patch.object(sys, "stdin", io.StringIO(stdin_text)) \
        if stdin_text is not None else None
    with patch.object(sys, "stdout", buf):
        if stdin_patch is not None:
            with stdin_patch:
                rc = check_pin_caps.main(argv)
        else:
            rc = check_pin_caps.main(argv)
    out = buf.getvalue().strip()
    payload = json.loads(out) if out else None
    return rc, payload


def _make_pinned_content(n_pins=0, pin_body_chars=100, stale_indices=()):
    """Thin wrapper around helpers.py factories preserving legacy signature."""
    entries = [
        make_pin_entry(
            title=f"Pin {i}",
            body_chars=pin_body_chars,
            stale_date="2026-01-01" if i in stale_indices else None,
        )
        for i in range(n_pins)
    ]
    return make_claude_md_with_pins(entries)


class TestCheckPinCapsCli_StatusQuery:
    """--status emits current slot state without checking any add."""

    def test_status_zero_pins(self, patched_claude_md):
        """Empty Pinned Context body routes through fail-open in _resolve_pins
        (parsed is None → reason='no pinned section'). Back-M1: --status
        uniformly surfaces the fail-open signal rather than a fake 0/12
        slot state.
        """
        patched_claude_md(_make_pinned_content(0))
        rc, payload = _run_cli(["--status"])
        assert rc == 0
        assert payload["allowed"] is True
        assert payload["violation"] is None
        assert "unknown" in payload["slot_status"]
        assert "proceeding" in payload["slot_status"]
        assert payload["evictable_pins"] == []

    def test_status_with_pins(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(3, pin_body_chars=200))
        rc, payload = _run_cli(["--status"])
        assert rc == 0
        assert payload["allowed"] is True
        assert "3/12" in payload["slot_status"]
        assert len(payload["evictable_pins"]) == 3
        # evictable_pins shape
        p0 = payload["evictable_pins"][0]
        assert p0["index"] == 0
        assert p0["heading"] == "Pin 0"  # "### " stripped
        assert p0["chars"] == 200
        assert p0["stale"] is False
        assert p0["override"] is False


class TestCheckPinCapsCli_AddAllowed:
    """Happy-path: --new-body fits under both caps → exit 0 + allowed=true."""

    def test_new_body_under_caps_allowed(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(3))
        rc, payload = _run_cli(["--new-body", "short new pin"])
        assert rc == 0
        assert payload["allowed"] is True
        assert payload["violation"] is None


class TestCheckPinCapsCli_CountRefusal:
    """At 12 pins, any add is refused with exit 1 and kind=count."""

    def test_full_slots_count_refused(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(12, pin_body_chars=50))
        rc, payload = _run_cli(["--new-body", "small"])
        assert rc == 1
        assert payload["allowed"] is False
        assert payload["violation"]["kind"] == "count"
        assert payload["violation"]["current_count"] == 12
        assert "(FULL)" in payload["slot_status"]

    def test_full_slots_with_override_still_count_refused(self, patched_claude_md):
        """Override is size-only bypass; does not relax count cap."""
        patched_claude_md(_make_pinned_content(12, pin_body_chars=50))
        rc, payload = _run_cli(["--new-body", "small", "--has-override"])
        assert rc == 1
        assert payload["violation"]["kind"] == "count"


class TestCheckPinCapsCli_SizeRefusal:
    """Oversize bodies refused unless --has-override."""

    def test_oversize_refused_without_override(self, patched_claude_md):
        # Need at least one existing pin so the Pinned Context section is
        # non-empty; empty sections fail-open (unknown state).
        patched_claude_md(_make_pinned_content(1, pin_body_chars=50))
        body = "x" * 1600
        rc, payload = _run_cli(["--new-body", body])
        assert rc == 1
        assert payload["violation"]["kind"] == "size"
        assert payload["violation"]["offending_pin_chars"] == 1600

    def test_oversize_allowed_with_override(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(1, pin_body_chars=50))
        body = "x" * 1600
        rc, payload = _run_cli(["--new-body", body, "--has-override"])
        assert rc == 0
        assert payload["allowed"] is True

    def test_exactly_at_cap_without_override_allowed(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(1, pin_body_chars=50))
        body = "x" * 1500  # exactly at cap → > 1500 predicate is false
        rc, payload = _run_cli(["--new-body", body])
        assert rc == 0
        assert payload["allowed"] is True


class TestCheckPinCapsCli_FailOpen:
    """Resolution / read / parse failures yield allowed=true + exit 0."""

    def test_no_claude_md_fails_open_on_add(self, monkeypatch):
        import staleness
        import check_pin_caps
        monkeypatch.setattr(staleness, "get_project_claude_md_path", lambda: None)
        monkeypatch.setattr(
            check_pin_caps, "get_project_claude_md_path", lambda: None
        )
        rc, payload = _run_cli(["--new-body", "anything"])
        assert rc == 0
        assert payload["allowed"] is True
        assert "unknown" in payload["slot_status"]

    def test_unreadable_file_fails_open_on_add(
        self, patched_claude_md, monkeypatch
    ):
        claude_md = patched_claude_md(_make_pinned_content(3))
        import check_pin_caps

        def _raise(*a, **k):
            raise IOError("simulated read failure")

        monkeypatch.setattr(Path, "read_text", _raise)
        rc, payload = _run_cli(["--new-body", "anything"])
        assert rc == 0
        assert payload["allowed"] is True

    def test_no_pinned_section_fails_open_on_add(self, patched_claude_md):
        """CLAUDE.md exists but has no ## Pinned Context — allow."""
        patched_claude_md("# Project\n\n## Working Memory\n\n")
        rc, payload = _run_cli(["--new-body", "anything"])
        assert rc == 0
        assert payload["allowed"] is True

    def test_empty_pinned_section_fails_open_on_add(self, patched_claude_md):
        """## Pinned Context heading present but body empty — treated
        as unknown state; fail-open per _resolve_pins contract."""
        patched_claude_md(_make_pinned_content(0))  # heading + blank
        rc, payload = _run_cli(["--new-body", "x" * 2000])
        assert rc == 0
        assert payload["allowed"] is True
        assert "unknown" in payload["slot_status"]

    def test_parse_exception_fails_open_on_add(
        self, patched_claude_md, monkeypatch
    ):
        patched_claude_md(_make_pinned_content(3))
        import check_pin_caps

        def _boom(_pinned_content):
            raise RuntimeError("parse blew up")

        monkeypatch.setattr(check_pin_caps, "parse_pins", _boom)
        rc, payload = _run_cli(["--new-body", "anything"])
        assert rc == 0
        assert payload["allowed"] is True

    def test_status_with_no_claude_md_still_emits(self, monkeypatch):
        import staleness
        import check_pin_caps
        monkeypatch.setattr(staleness, "get_project_claude_md_path", lambda: None)
        monkeypatch.setattr(
            check_pin_caps, "get_project_claude_md_path", lambda: None
        )
        # --status bypasses the fail-open short-circuit; emits empty-pin view.
        rc, payload = _run_cli(["--status"])
        assert rc == 0
        assert payload["allowed"] is True


class TestCheckPinCapsCli_EvictablePins:
    """evictable_pins surface — ordering, stale/override flags, heading strip."""

    def test_evictable_includes_stale_flag(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(3, stale_indices={1}))
        rc, payload = _run_cli(["--status"])
        flags = [p["stale"] for p in payload["evictable_pins"]]
        assert flags == [False, True, False]

    def test_evictable_includes_override_flag(self, patched_claude_md):
        content = make_claude_md_with_pins([
            make_pin_entry(
                title="Override Pin",
                body_chars=4,
                date="2026-04-11",
                override_rationale="load-bearing verbatim form",
            ),
            make_pin_entry(title="Plain Pin", body_chars=4),
        ])
        patched_claude_md(content)
        rc, payload = _run_cli(["--status"])
        overrides = [p["override"] for p in payload["evictable_pins"]]
        assert overrides == [True, False]

    def test_evictable_heading_has_prefix_stripped(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(2))
        rc, payload = _run_cli(["--status"])
        for entry in payload["evictable_pins"]:
            assert not entry["heading"].startswith("### ")


class TestCheckPinCapsCli_NeverExit2:
    """Exit code 2 is reserved and MUST NEVER be used by this CLI."""

    def test_exit_never_2_on_add_allowed(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(0))
        rc, _ = _run_cli(["--new-body", "short"])
        assert rc != 2

    def test_exit_never_2_on_add_refused(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(12))
        rc, _ = _run_cli(["--new-body", "any"])
        assert rc != 2

    def test_exit_never_2_on_fail_open(self, monkeypatch):
        import staleness
        import check_pin_caps
        monkeypatch.setattr(staleness, "get_project_claude_md_path", lambda: None)
        monkeypatch.setattr(
            check_pin_caps, "get_project_claude_md_path", lambda: None
        )
        rc, _ = _run_cli(["--new-body", "any"])
        assert rc != 2


class TestCheckPinCapsCli_BodyFromStdin:
    """`--body-from-stdin` ingestion path — separate argv branch from
    `--new-body`. Load-bearing because stdin is the SHELL-INJECTION-SAFE
    path promised by `commands/pin-memory.md` (the `--new-body` argv path
    is documented as legacy/backward-compat). A bug here defeats the very
    mitigation the pin-memory command was redesigned around.

    Before this class, test coverage on `--body-from-stdin` was ZERO
    (reviewer finding #493 blind round-3): every existing test in this
    file exercises the `--new-body` argv branch only. The argv-branch
    tests do not cover the `args.body_from_stdin: new_body = sys.stdin.read()`
    statement in `check_pin_caps.main`, so regressions on stdin ingestion
    were silently possible.

    Counter-test-by-revert: flipping `args.body_from_stdin:` to
    `if False:` (i.e., never consume stdin) must cause the happy-path
    test below to produce `args.new_body is None` → status-query shape
    instead of an allow/refuse decision, breaking the assertion.
    """

    def test_happy_path_stdin_under_caps_allowed(self, patched_claude_md):
        """Case (a): body piped via stdin under both caps → allowed."""
        patched_claude_md(_make_pinned_content(3))
        rc, payload = _run_cli(
            ["--body-from-stdin"], stdin_text="a short pin body"
        )
        assert rc == 0
        assert payload["allowed"] is True
        assert payload["violation"] is None

    def test_oversize_stdin_without_override_refused(self, patched_claude_md):
        """Case (b): oversize body piped via stdin → size refusal.

        Confirms the size-cap predicate fires identically whether the body
        arrives via --new-body argv or --body-from-stdin — the two branches
        must converge on the same `check_add_allowed` call.
        """
        patched_claude_md(_make_pinned_content(1, pin_body_chars=50))
        rc, payload = _run_cli(
            ["--body-from-stdin"], stdin_text="x" * 1600
        )
        assert rc == 1
        assert payload["allowed"] is False
        assert payload["violation"]["kind"] == "size"
        assert payload["violation"]["offending_pin_chars"] == 1600

    def test_shell_metacharacters_preserved_verbatim(self, patched_claude_md):
        """Case (c): `$(whoami)`, backticks, `$VAR` arrive verbatim in stdin.

        This is the LOAD-BEARING contract of the stdin path. The whole point
        of `--body-from-stdin` over `--new-body` argv is that the shell does
        NOT expand metacharacters when the caller uses a single-quoted
        heredoc. The CLI MUST preserve the literal bytes — `$(whoami)` does
        not become the user's login name, backticks do not command-substitute,
        `$VAR` does not variable-expand. The body-chars cap count is the
        observable signal: if any expansion occurred, `_extract_body_chars`
        would see a different length than the raw input.

        Counter-test: removing `new_body = sys.stdin.read()` and keeping
        `new_body = args.new_body` (None for stdin branch) would route
        this call through the status-query path, producing `allowed=True`
        with NO violation field referencing size — the test would pass
        trivially but for the wrong reason. We pair the refusal assertion
        with an offending_pin_chars cardinality pin (exactly `len(body)`)
        that defeats the trivial pass.
        """
        patched_claude_md(_make_pinned_content(1, pin_body_chars=50))
        # Shell metachars that would expand if piped through an unquoted
        # context. The CLI must treat these as opaque bytes.
        body = "$(whoami) `date` $HOME ${SHELL} \\\"escaped\\\"" + ("x" * 1600)
        rc, payload = _run_cli(["--body-from-stdin"], stdin_text=body)
        # Body is oversize → refusal, AND offending_pin_chars matches the
        # exact raw input length (stripped of any date comments — there
        # are none here). If the shell had expanded `$HOME` or `$(whoami)`,
        # the chars count would diverge from len(body).
        assert rc == 1, (
            "stdin body should have triggered size refusal; got "
            f"allowed={payload['allowed']}"
        )
        assert payload["violation"]["kind"] == "size"
        assert payload["violation"]["offending_pin_chars"] == len(body), (
            "stdin preservation guarantee broken: offending_pin_chars "
            f"({payload['violation']['offending_pin_chars']}) differs from "
            f"raw input length ({len(body)}), suggesting shell metachar "
            "expansion occurred somewhere along the ingestion path."
        )

    def test_mutually_exclusive_new_body_and_stdin(self, patched_claude_md):
        """Case (d): `--new-body` and `--body-from-stdin` together → argparse error.

        argparse's add_mutually_exclusive_group raises SystemExit(2) on a
        conflict (the `2` here is argparse's internal usage-error exit,
        NOT the reserved exit-2-from-main path). We catch SystemExit and
        assert the code — confirming the mutex group is configured, i.e.,
        no caller can accidentally send both an argv body and a stdin body
        and have one of them silently win.
        """
        patched_claude_md(_make_pinned_content(0))
        import check_pin_caps
        # argparse emits usage text to stderr on conflict; silence it.
        with patch.object(sys, "stderr", io.StringIO()):
            with pytest.raises(SystemExit) as exc_info:
                check_pin_caps.main([
                    "--new-body", "x",
                    "--body-from-stdin",
                ])
        # argparse uses exit code 2 for argument errors — this is the one
        # case where the CLI legitimately exits with 2 (pre-main argparse
        # error, before our SACROSANCT fail-open contract applies).
        assert exc_info.value.code == 2

    def test_empty_stdin_refused(self, patched_claude_md):
        """Case (e): empty stdin → refusal (backend-coder-5 Commit 5).

        Commit 5 (1ce9a2d) guards against 0-char pin adds: an empty or
        whitespace-only body arriving via `--body-from-stdin` is refused
        with `CapViolation(kind="empty", ...)`. Pre-commit, the empty
        body routed through `check_add_allowed` with 0 chars, hit neither
        the size nor count predicate, and returned `allowed=True` — a
        no-op pin that polluted the pin slot without contributing
        content.

        Counter-test-by-revert: reverting Commit 5 (deleting the
        `if not new_body.strip():` guard + the `"empty"` Literal addition)
        MUST cause this test to fail on `rc == 1` → `rc == 0`.
        """
        patched_claude_md(_make_pinned_content(3))
        rc, payload = _run_cli(["--body-from-stdin"], stdin_text="")
        assert rc == 1, (
            "empty stdin body must be refused — prevents 0-char pin "
            "add (backend-coder-5 Commit 5)"
        )
        assert payload["allowed"] is False
        assert payload["violation"]["kind"] == "empty"

    def test_whitespace_only_stdin_refused(self, patched_claude_md):
        """Case (e'): whitespace-only stdin → refusal.

        Commit 5's guard `if not new_body.strip():` fires on
        whitespace-only input the same as on `""`. Pins that collapse
        to empty under `_extract_body_chars` anyway MUST share the
        refusal path — otherwise a curator gets a 0-chars pin with a
        `<!-- pinned: -->` date comment but no body content.
        """
        patched_claude_md(_make_pinned_content(3))
        rc, payload = _run_cli(
            ["--body-from-stdin"], stdin_text="   \n\t  \n"
        )
        assert rc == 1
        assert payload["violation"]["kind"] == "empty"

    def test_embedded_pin_structure_in_stdin_refused(self, patched_claude_md):
        """Case (f): stdin body containing `### ` heading → embedded_pin refusal.

        The `check_add_allowed` embedded-pin defense fires when
        `parse_pins(new_body)` returns a non-empty list. A candidate body
        with a bare `### Smuggled` heading inside triggers this path via
        the stdin branch just as it does via the argv branch — the
        defense lives downstream of the stdin/argv fan-in, so stdin
        coverage here pins the end-to-end contract.
        """
        patched_claude_md(_make_pinned_content(3))
        body = "Legitimate text\n\n### Smuggled Pin\nMore text\n"
        rc, payload = _run_cli(["--body-from-stdin"], stdin_text=body)
        assert rc == 1
        assert payload["allowed"] is False
        assert payload["violation"]["kind"] == "embedded_pin"

    def test_stdin_exit_never_2(self, patched_claude_md):
        """SACROSANCT invariant carries over to the stdin branch.

        The uniform fail-open contract (exit != 2 from main) must hold
        regardless of which body-source branch a caller takes. A regression
        where stdin reading raises an uncaught exception must NOT produce
        exit-2 — it must route through the outer fail-open guard.
        """
        patched_claude_md(_make_pinned_content(0))
        rc, _ = _run_cli(["--body-from-stdin"], stdin_text="short body")
        assert rc != 2
        # Also refused-case
        patched_claude_md(_make_pinned_content(12, pin_body_chars=50))
        rc, _ = _run_cli(["--body-from-stdin"], stdin_text="any body")
        assert rc != 2


class TestCheckPinCapsCli_OverrideRationale:
    """In-band validation of `--override-rationale <text>` (Commit 6,
    eabfdc8). When the curator passes a rationale at add time, the CLI
    validates it against `OVERRIDE_COMMENT_RE` + `OVERRIDE_RATIONALE_MAX`
    and refuses malformed / oversize values with
    `CapViolation(kind="invalid_override", ...)`.

    Two-gate defense: `parse_pins` at read time silently downgrades an
    invalid override to no-override, leaving the curator to discover the
    problem only on NEXT session's stale-block check. This in-band gate
    gives same-session feedback on the typo/injection surface.

    Counter-test-by-revert: reverting Commit 6 (deleting the
    `invalid_override_reason` block + argparse arg) MUST cause the three
    refusal tests here to fail on their `rc == 1` assertion → `rc == 0`.
    The happy-path test passes both pre- and post-commit (it asserts
    that a legit rationale is accepted, which is true when the CLI
    simply ignores the unknown arg by default — so it does NOT counter-
    test alone).
    """

    def test_override_rationale_happy_path_allowed(self, patched_claude_md):
        """Valid rationale passes the in-band check → allowed."""
        patched_claude_md(_make_pinned_content(1, pin_body_chars=50))
        body = "x" * 1600  # oversize, requires override
        rc, payload = _run_cli([
            "--new-body", body,
            "--has-override",
            "--override-rationale", "verbatim form is load-bearing for LLM",
        ])
        assert rc == 0, (
            f"valid rationale rejected: {payload}"
        )
        assert payload["allowed"] is True
        assert payload["violation"] is None

    def test_override_rationale_oversize_refused(self, patched_claude_md):
        """Rationale exceeding OVERRIDE_RATIONALE_MAX (120) → refusal.

        121 chars is exactly at the `> 120` boundary — cardinality-pin
        on the predicate: if someone flips `>` to `>=`, exactly-120 rationales
        would start getting refused too. Pair with a boundary-passing
        test (120 chars → allowed) to pin the inequality direction.
        """
        patched_claude_md(_make_pinned_content(1, pin_body_chars=50))
        body = "x" * 1600
        oversize = "r" * 121  # strictly > max
        rc, payload = _run_cli([
            "--new-body", body,
            "--has-override",
            "--override-rationale", oversize,
        ])
        assert rc == 1
        assert payload["allowed"] is False
        assert payload["violation"]["kind"] == "invalid_override"
        assert "121 chars" in payload["violation"]["detail"]
        assert "max: 120" in payload["violation"]["detail"]

    def test_override_rationale_at_boundary_allowed(self, patched_claude_md):
        """Rationale exactly at the 120-char cap → allowed (inclusive boundary).

        `OVERRIDE_RATIONALE_MAX = 120` with `> MAX` predicate means 120
        is allowed. If someone accidentally flips to `>= MAX`, this test
        fails — boundary-direction pin.
        """
        patched_claude_md(_make_pinned_content(1, pin_body_chars=50))
        body = "x" * 1600
        at_max = "r" * 120
        rc, payload = _run_cli([
            "--new-body", body,
            "--has-override",
            "--override-rationale", at_max,
        ])
        assert rc == 0, (
            f"120-char rationale (== MAX) refused: {payload}. "
            "OVERRIDE_RATIONALE_MAX is inclusive; predicate is `> MAX`."
        )

    def test_override_rationale_empty_refused(self, patched_claude_md):
        """Empty rationale → refusal (whitespace-only also refused).

        Commit 6's guard chain: `rationale = args.override_rationale.strip()`
        followed by `if not rationale:`. Empty string and whitespace-only
        both collapse to empty post-strip.
        """
        patched_claude_md(_make_pinned_content(1, pin_body_chars=50))
        body = "x" * 1600
        rc, payload = _run_cli([
            "--new-body", body,
            "--has-override",
            "--override-rationale", "",
        ])
        assert rc == 1
        assert payload["violation"]["kind"] == "invalid_override"
        assert "empty" in payload["violation"]["detail"].lower()

    def test_override_rationale_whitespace_only_refused(
        self, patched_claude_md
    ):
        """Whitespace-only rationale → refusal.

        Shares the refusal path with empty rationale after
        `rationale.strip()`.
        """
        patched_claude_md(_make_pinned_content(1, pin_body_chars=50))
        body = "x" * 1600
        rc, payload = _run_cli([
            "--new-body", body,
            "--has-override",
            "--override-rationale", "   \t\n ",
        ])
        assert rc == 1
        assert payload["violation"]["kind"] == "invalid_override"

    def test_override_rationale_html_terminator_refused(
        self, patched_claude_md
    ):
        """Rationale containing `-->` → refusal via OVERRIDE_COMMENT_RE round-trip.

        The HTML comment terminator `-->` inside a rationale would:
          (a) prematurely terminate the <!-- pinned: ... --> comment
              once written to CLAUDE.md, leaving trailing injection
              soil outside the comment boundary, AND
          (b) fail the OVERRIDE_COMMENT_RE fullmatch at read time
              (the rationale pattern `(?:[^-]|-(?!->))+` refuses any
              `-->` substring by construction).

        Commit 6 catches this at add time by synthesizing the full
        comment text and round-tripping through OVERRIDE_COMMENT_RE —
        the same check parse_pins runs at read time. Without the in-band
        gate, the curator would get a silent downgrade to no-override
        on next session's parse instead of a same-session error.

        This is the LOAD-BEARING defense against HTML-comment-boundary
        smuggling.
        """
        patched_claude_md(_make_pinned_content(1, pin_body_chars=50))
        body = "x" * 1600
        # Rationale containing HTML comment terminator
        malformed = "load-bearing --> <script>alert(1)</script>"
        rc, payload = _run_cli([
            "--new-body", body,
            "--has-override",
            "--override-rationale", malformed,
        ])
        assert rc == 1, (
            "rationale containing `-->` bypassed in-band validation; "
            "HTML-comment-boundary smuggle is the load-bearing risk "
            "Commit 6 closes."
        )
        assert payload["violation"]["kind"] == "invalid_override"
        assert (
            "disallowed characters" in payload["violation"]["detail"]
            or "-->" in payload["violation"]["detail"]
        )

    def test_override_rationale_exit_never_2(self, patched_claude_md):
        """SACROSANCT: invalid_override refusal exits with 1, never 2."""
        patched_claude_md(_make_pinned_content(1, pin_body_chars=50))
        body = "x" * 1600
        rc, _ = _run_cli([
            "--new-body", body,
            "--has-override",
            "--override-rationale", "bad --> rationale",
        ])
        assert rc != 2

    def test_override_rationale_none_ignored(self, patched_claude_md):
        """`--override-rationale` omitted → no-op (no invalid_override check).

        Backward-compat: callers not passing --override-rationale must
        continue to work. If Commit 6 accidentally made --override-rationale
        required, this test fails.
        """
        patched_claude_md(_make_pinned_content(1, pin_body_chars=50))
        body = "x" * 1600
        rc, payload = _run_cli([
            "--new-body", body,
            "--has-override",
        ])
        assert rc == 0
        assert payload["allowed"] is True

    # --- Cycle-7 Commit 2: line-terminator rejection in rationale ----------
    # Pre-Commit-2 state (verified empirically during Cycle-7 planning):
    # OVERRIDE_COMMENT_RE accepts any char except the HTML comment
    # terminator `-->`, so newline/CR/Unicode-line-separators slip through
    # the CLI's in-band validation. Parser-side `_FORBIDDEN_TERMINATOR_TABLE`
    # strips \r, U+2028, U+2029, U+0085 but NOT \n. A multi-line rationale
    # landed in CLAUDE.md is both a layout-corruption and a prompt-injection
    # surface (U+2028/2029 span logical lines in some renderers). Commit 2
    # tightens CLI refusal on ALL five line terminators up-front.
    #
    # Counter-test-by-revert: removing the `_FORBIDDEN_RATIONALE_CHARS`
    # check in check_pin_caps.py MUST cause these tests to fail on their
    # `rc == 1` assertion → `rc == 0`.

    def test_override_rationale_newline_refused(self, patched_claude_md):
        """Rationale containing LF (`\\n`) → refusal.

        A multi-line rationale in CLAUDE.md corrupts the single-line
        `<!-- pinned: ..., pin-size-override: ... -->` shape that the
        downstream parse is tuned for. parse_pins' preceding-comment
        walker `prior_lines = preceding.rstrip("\\n").split("\\n")`
        inspects the SINGLE immediately-preceding line for a full match,
        so a multi-line rationale silently downgrades to no-override at
        parse time. Commit 2 adds the CLI-side refusal so the curator
        gets same-session feedback instead of silent downgrade.
        """
        patched_claude_md(_make_pinned_content(1, pin_body_chars=50))
        body = "x" * 1600
        rc, payload = _run_cli([
            "--new-body", body,
            "--has-override",
            "--override-rationale", "line one\nline two",
        ])
        assert rc == 1
        assert payload["allowed"] is False
        assert payload["violation"]["kind"] == "invalid_override"

    def test_override_rationale_carriage_return_refused(
        self, patched_claude_md
    ):
        """Rationale containing CR (`\\r`) → refusal.

        Parser silently strips \\r via _FORBIDDEN_TERMINATOR_TABLE (line
        181 in pin_caps.py). Silent sanitization at read time + silent
        acceptance at add time = the curator's rationale mutates between
        when they type it and when it renders in CLAUDE.md. The CLI-side
        refusal makes the asymmetry observable.
        """
        patched_claude_md(_make_pinned_content(1, pin_body_chars=50))
        body = "x" * 1600
        rc, payload = _run_cli([
            "--new-body", body,
            "--has-override",
            "--override-rationale", "hello\rworld",
        ])
        assert rc == 1
        assert payload["violation"]["kind"] == "invalid_override"

    @pytest.mark.parametrize("separator,name", [
        (" ", "U+2028 LINE SEPARATOR"),
        (" ", "U+2029 PARAGRAPH SEPARATOR"),
        ("", "U+0085 NEXT LINE"),
    ])
    def test_override_rationale_unicode_line_separator_refused(
        self, patched_claude_md, separator, name
    ):
        """Rationale containing any of U+2028 / U+2029 / U+0085 → refusal.

        Parser-side _FORBIDDEN_TERMINATOR_TABLE strips these silently,
        per Sec-F5b docstring on pin_caps.py:77-81. The prompt-injection
        vector is that these code points span logical lines in some
        renderers (terminals, certain markdown parsers) — enabling
        rationale content to mimic multi-line pin-structure and confuse
        downstream consumers. Refusing at add time is the observable
        guard.
        """
        patched_claude_md(_make_pinned_content(1, pin_body_chars=50))
        body = "x" * 1600
        rationale = f"load-bearing{separator}context"
        rc, payload = _run_cli([
            "--new-body", body,
            "--has-override",
            "--override-rationale", rationale,
        ])
        assert rc == 1, (
            f"{name} rationale slipped past CLI refusal; "
            "Commit 2's line-terminator check is not catching this code point."
        )
        assert payload["violation"]["kind"] == "invalid_override"

    def test_parser_rejects_multiline_rationale_cleanly(
        self, patched_claude_md
    ):
        """Parser-side: CLAUDE.md with synthesized multi-line rationale
        round-trips cleanly (no crash, no override captured on the
        broken line).

        Construct a CLAUDE.md fragment where the rationale SPANS two
        lines (synthesized by splicing a `\\n` into the override
        comment). parse_pins must:
          (a) not raise (fail-open by construction), and
          (b) NOT capture the broken rationale as a valid override
              (silent downgrade to no-override is the documented
              pre-Commit-2 behavior; Commit 2 may tighten this, but the
              don't-raise + don't-capture-broken-form invariant holds
              under both regimes).

        This test is the parser-side pin: regardless of whether Commit 2
        tightens the parser too or only the CLI, parse_pins' handling
        of a multi-line rationale must be safe (not crash, not silently
        accept a split rationale as valid). Non-xfail because the
        invariant holds under both states.
        """
        import pin_caps
        # Synthesize a multi-line override comment directly (bypasses the
        # CLI layer entirely — exercises parse_pins in isolation).
        pinned_content = (
            "<!-- pinned: 2026-04-21, pin-size-override: split\n"
            "across-lines -->\n"
            "### Broken Pin\n"
            "body content\n"
        )
        # parse_pins must not raise.
        pins = pin_caps.parse_pins(pinned_content)
        # Exactly one pin is parsed (heading-anchored).
        assert len(pins) == 1
        # Override MUST NOT be captured as valid from the split form.
        # Parser walks the SINGLE preceding line for OVERRIDE_COMMENT_RE;
        # a split comment can't fullmatch on either line alone, so
        # override_rationale stays None and date_comment also stays None
        # (neither line is a valid date comment in isolation).
        assert pins[0].override_rationale is None, (
            "Multi-line rationale was captured as a valid override — "
            "parse_pins must not accept split-line rationale forms. "
            f"Got: {pins[0].override_rationale!r}"
        )

    def test_override_rationale_newline_exit_never_2(self, patched_claude_md):
        """SACROSANCT: newline-in-rationale refusal exits with 1, never 2.

        The uniform fail-open contract (rc != 2 from main) must hold on
        the newline-refusal path the same as on every other refusal.
        """
        patched_claude_md(_make_pinned_content(1, pin_body_chars=50))
        body = "x" * 1600
        rc, _ = _run_cli([
            "--new-body", body,
            "--has-override",
            "--override-rationale", "bad\nrationale",
        ])
        assert rc == 1, "newline rationale should be refused"
        assert rc != 2, "SACROSANCT: never exit 2"
