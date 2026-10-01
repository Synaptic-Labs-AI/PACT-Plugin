"""
Location: pact-plugin/tests/test_claude_md_not_utf8.py
Summary: A CLAUDE.md holding a byte that is not valid UTF-8 neither breaks a
         session nor gets rewritten.
Used by: pytest.

One stray byte (a latin-1 'é' from a wrong-editor save, say) used to raise
UnicodeDecodeError in the strict reads of the project CLAUDE.md, and session_init
fell into its safety net for a lead and a teammate alike; one in the global
~/.claude/CLAUDE.md did the same for every session. The rule now: a read that
only reads decodes with replacement, and a read that feeds a rewrite decodes
strictly and, when it cannot, leaves the file untouched. It reports the skip
only when the rewrite was due: with nothing to do, a bad file gets what a valid
one gets. Writing replacement characters back would corrupt the user's file.

Every arm runs a control on a valid copy of the same layout, so a skip is
shown on a layout that provably reaches the rewrite. The session-start arms
drive the real hooks in a fresh interpreter under a sandbox HOME.
"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parents[1] / "hooks"
_PLUGIN_ROOT = _HOOKS.parent
LEAD = "PACT:pact-orchestrator"
BAD = b"caf\xe9 latin-1 byte\n"
SAFETY_NET = "PACT hook warning (session_init)"
NOT_UTF8 = "not valid UTF-8"
USER_MD = "# My project\n\nBuild with make.\n"


def _sandbox(tmp_path):
    home = tmp_path / "home"
    proj = home / "proj"
    proj.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    env.update(HOME=str(home), CLAUDE_PROJECT_DIR=str(proj),
               CLAUDE_PLUGIN_ROOT=str(_PLUGIN_ROOT))
    return home, proj, env


def _run(hook, frame, home, env):
    proc = subprocess.run(
        [sys.executable, str(_HOOKS / hook)], input=json.dumps(frame),
        capture_output=True, text=True, env=env, cwd=str(home), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _start(sid, agent_type, home, env, source="startup"):
    frame = {"hook_event_name": "SessionStart", "session_id": sid, "source": source}
    if agent_type is not None:
        frame["agent_type"] = agent_type
    return _run("session_init.py", frame, home, env)


def _managed_md(tmp_path):
    """The managed project CLAUDE.md a lead start writes, with a stale pin."""
    home, proj, env = _sandbox(tmp_path / "template")
    _start(str(uuid.uuid4()), LEAD, home, env)
    text = (proj / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "## Pinned Context\n" in text
    return text.replace(
        "## Pinned Context\n",
        "## Pinned Context\n\n### Old decision 2020-01-01\n\nPinned long ago.\n",
        1,
    )


class TestSessionStart:

    @pytest.mark.parametrize("layout, notice", [
        ("managed", "Pinned staleness skipped"),
        ("unmanaged", "Migration skipped"),
    ])
    def test_a_lead_start_skips_the_rewrites_and_keeps_its_ladder(
        self, tmp_path, layout, notice
    ):
        """A managed file reaches the session block and stale-pin rewrites; an
        unmanaged one reaches the migration."""
        text = _managed_md(tmp_path) if layout == "managed" else USER_MD
        rel = Path(".claude/CLAUDE.md") if layout == "managed" else Path("CLAUDE.md")

        home, proj, env = _sandbox(tmp_path / "control")
        (proj / rel).parent.mkdir(parents=True, exist_ok=True)
        (proj / rel).write_text(text, encoding="utf-8")
        _start(str(uuid.uuid4()), LEAD, home, env)
        assert (proj / rel).read_text(encoding="utf-8") != text, (
            "control: a lead start on the valid file did not rewrite it"
        )

        home, proj, env = _sandbox(tmp_path / "subject")
        (proj / rel).parent.mkdir(parents=True, exist_ok=True)
        before = text.encode("utf-8") + BAD
        (proj / rel).write_bytes(before)
        out = _start(str(uuid.uuid4()), LEAD, home, env)

        assert (proj / rel).read_bytes() == before
        assert SAFETY_NET not in out.get("systemMessage", "")
        assert notice in out.get("systemMessage", "")
        assert "Session info skipped" in out.get("systemMessage", "")
        assert out["hookSpecificOutput"]["additionalContext"].startswith(
            "YOUR PACT ROLE: orchestrator.")

    def test_a_teammate_start_completes_and_writes_nothing(self, tmp_path):
        home, proj, env = _sandbox(tmp_path)
        (proj / ".claude").mkdir()
        before = _managed_md(tmp_path).encode("utf-8") + BAD
        (proj / ".claude" / "CLAUDE.md").write_bytes(before)

        out = _start(str(uuid.uuid4()), "pact-backend-coder", home, env)

        assert SAFETY_NET not in out.get("systemMessage", "")
        assert "YOUR PACT ROLE: teammate." not in out["hookSpecificOutput"][
            "additionalContext"], "the teammate fell into the safety net"
        assert (proj / ".claude" / "CLAUDE.md").read_bytes() == before

    def test_a_bad_byte_in_the_global_file_does_not_break_any_session(self, tmp_path):
        kernel = "# global\n\n<!-- PACT_START: old kernel -->\nold\n<!-- PACT_END -->\n"

        home, proj, env = _sandbox(tmp_path / "control")
        (home / ".claude").mkdir(parents=True, exist_ok=True)
        (home / ".claude" / "CLAUDE.md").write_text(kernel, encoding="utf-8")
        _start(str(uuid.uuid4()), None, home, env)
        assert "PACT_START" not in (home / ".claude" / "CLAUDE.md").read_text(), (
            "control: the kernel block was not stripped from a valid file"
        )

        home, proj, env = _sandbox(tmp_path / "subject")
        (home / ".claude").mkdir(parents=True, exist_ok=True)
        before = kernel.encode("utf-8") + BAD
        (home / ".claude" / "CLAUDE.md").write_bytes(before)
        out = _start(str(uuid.uuid4()), None, home, env)

        assert (home / ".claude" / "CLAUDE.md").read_bytes() == before
        assert SAFETY_NET not in out.get("systemMessage", "")
        assert "Kernel block strip skipped" in out.get("systemMessage", "")


class TestNothingToDo:
    """With nothing for a rewriter to do, a bad file gets the result a valid one
    gets: no skip notice. Each arm's would-change twin is above or below."""

    def test_a_global_file_without_a_kernel_block_gives_no_notice(self, tmp_path):
        text = b"# global\n\nMy own instructions.\n"
        outs = []
        for name, data in (("control", text), ("subject", text + BAD)):
            home, proj, env = _sandbox(tmp_path / name)
            (home / ".claude").mkdir(parents=True, exist_ok=True)
            (home / ".claude" / "CLAUDE.md").write_bytes(data)
            outs.append(_start(str(uuid.uuid4()), None, home, env))
            assert (home / ".claude" / "CLAUDE.md").read_bytes() == data

        assert NOT_UTF8 not in outs[1].get("systemMessage", "")
        assert outs[1].get("systemMessage") == outs[0].get("systemMessage")

    def test_a_managed_file_with_no_pin_gives_no_migration_or_staleness_notice(
        self, tmp_path
    ):
        home, proj, env = _sandbox(tmp_path)
        _start(str(uuid.uuid4()), LEAD, home, env)
        md = proj / ".claude" / "CLAUDE.md"
        before = md.read_bytes() + BAD
        md.write_bytes(before)

        message = _start(str(uuid.uuid4()), LEAD, home, env).get("systemMessage", "")

        assert md.read_bytes() == before
        assert "Migration skipped" not in message
        assert "Pinned staleness skipped" not in message
        assert "Session info skipped" in message, (
            "a new session's block is a rewrite that was due"
        )

    def test_a_compaction_reports_no_session_info_skip(self, tmp_path):
        sid = str(uuid.uuid4())
        home, proj, env = _sandbox(tmp_path)
        _start(sid, LEAD, home, env)
        md = proj / ".claude" / "CLAUDE.md"
        valid = md.read_bytes()
        _start(sid, LEAD, home, env, source="compact")
        assert md.read_bytes() == valid, "control: a compaction rewrote the block"

        md.write_bytes(valid + BAD)
        message = _start(sid, LEAD, home, env, source="compact").get(
            "systemMessage", "")

        assert md.read_bytes() == valid + BAD
        assert "Session info skipped" not in message

    def test_the_pin_marker_writer_reports_its_own_no_op(self, tmp_path, monkeypatch):
        import pin_marker_writer

        project = tmp_path / "p"
        project.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        (project / "CLAUDE.md").write_text(USER_MD, encoding="utf-8")
        assert pin_marker_writer._plan_and_write() == "noop_not_migrated", (
            "control: the valid file was not a no-op"
        )

        before = USER_MD.encode("utf-8") + BAD
        (project / "CLAUDE.md").write_bytes(before)
        assert pin_marker_writer._plan_and_write() == "noop_not_migrated"
        assert (project / "CLAUDE.md").read_bytes() == before


class TestFirstPrompt:

    def test_an_unrecorded_lead_leaves_the_block_and_reports_the_skip(self, tmp_path):
        """bootstrap_prompt_gate replaces the block of a lead session_init did
        not record; a file it cannot decode is left alone."""
        def arm(root, bad):
            home, proj, env = _sandbox(root)
            _start(str(uuid.uuid4()), LEAD, home, env)
            md = proj / ".claude" / "CLAUDE.md"
            data = md.read_bytes() + (BAD if bad else b"")
            md.write_bytes(data)
            fork = str(uuid.uuid4())
            _start(fork, None, home, env, source="fork")
            out = _run("bootstrap_prompt_gate.py", {
                "hook_event_name": "UserPromptSubmit", "session_id": fork,
                "agent_type": LEAD, "prompt": "hi"}, home, env)
            return fork, md, data, out["hookSpecificOutput"]["additionalContext"]

        fork, md, _, context = arm(tmp_path / "control", bad=False)
        assert f"--resume {fork}" in md.read_text(encoding="utf-8"), (
            "control: the valid block was not replaced"
        )

        fork, md, data, context = arm(tmp_path / "subject", bad=True)
        assert md.read_bytes() == data
        assert NOT_UTF8 in context
        assert "Session placeholder variables" in context


class TestRewritersSkip:

    def test_the_pin_marker_writer_skips(self, tmp_path, monkeypatch):
        import pin_marker_writer

        text = _managed_md(tmp_path)
        project = tmp_path / "p"
        project.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        (project / "CLAUDE.md").write_text(text, encoding="utf-8")
        assert pin_marker_writer._plan_and_write() == "written", (
            "control: the valid file was not marked"
        )

        before = text.encode("utf-8") + BAD
        (project / "CLAUDE.md").write_bytes(before)
        assert pin_marker_writer._plan_and_write() == "skipped_not_utf8"
        assert (project / "CLAUDE.md").read_bytes() == before

    def test_the_working_memory_sync_skips(self, tmp_path, monkeypatch):
        from scripts import working_memory as wm

        text = ("# Project\n\n## Working Memory\n"
                "<!-- Auto-managed by pact-memory skill. -->\n\n")
        (tmp_path / ".claude").mkdir()
        md = tmp_path / ".claude" / "CLAUDE.md"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        md.write_text(text, encoding="utf-8")
        assert wm.sync_to_claude_md({"context": "c", "goal": "g"}, None, "id").reason \
            == wm.SyncResult.WROTE, "control: the valid file was not written"

        before = text.encode("utf-8") + BAD
        md.write_bytes(before)
        result = wm.sync_to_claude_md({"context": "c2", "goal": "g"}, None, "id2")
        assert result.reason == wm.SyncResult.FAILED
        assert md.read_bytes() == before

    def test_the_pin_archive_refuses_before_any_write(self, tmp_path, monkeypatch):
        import archive_pin

        md = tmp_path / "CLAUDE.md"
        monkeypatch.setattr(archive_pin, "resolve_claude_md", lambda: (md, tmp_path))

        md.write_text("# Project\n", encoding="utf-8")
        with pytest.raises(archive_pin._Unevaluable) as control:
            archive_pin.archive_pin(1)
        assert control.value.reason == "no Pinned Context section", (
            "control: the valid file was not read past the decode"
        )

        before = b"# Project\n" + BAD
        md.write_bytes(before)
        with pytest.raises(archive_pin._Unevaluable) as subject:
            archive_pin.archive_pin(1)
        assert subject.value.reason == "CLAUDE.md unreadable (UnicodeDecodeError)"
        assert md.read_bytes() == before
