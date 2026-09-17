"""The REAL CLAUDE.md resolution path for archive_pin — driven, not mocked.

THIS FILE EXISTS BECAUSE NOTHING ELSE EXERCISES THE ACTUAL RESOLVER.
`get_project_claude_md_path` is monkeypatched in both places it appears
(test_check_pin_caps.py's `patched_claude_md`, test_archive_pin.py's
`claude_md` fixture), so every existing test hands the code a path it already
decided on. That is precisely why a resolver defect survived PREPARE,
ARCHITECT, CODE, TEST and three reviews: no test could see it, because no
test ever asked the resolver a question.

So nothing here patches `get_project_claude_md_path`, `resolve_claude_md`, or
`_resolve_project_claude_md_with_base`. The tests set `CLAUDE_PROJECT_DIR`
and the working directory — the two real inputs — and let resolution run.

THE DEFECT UNDER TEST. Resolution order is CLAUDE_PROJECT_DIR -> git
common-dir parent -> CWD, and a miss at any step falls through SILENTLY. A
CLAUDE_PROJECT_DIR naming a directory with no CLAUDE.md therefore does not
fail; it resolves to a DIFFERENT project's file and the archival reports a
confident success for a pin the invocation never named. The command's Step 3
heading cross-check is structurally blind to it, because check_pin_caps.py
uses the SAME resolver — the listing step and the archival step agree on the
same wrong file and every heading matches. That check catches an index shift
WITHIN a file; nothing catches a wrong FILE.

WHY THE REFUSAL IS NARROW, and why that matters more than making it strict.
PACT's own primary workflow sets CLAUDE_PROJECT_DIR to a WORKTREE, where
CLAUDE.md is gitignored and therefore absent, and depends on the git
fall-through to reach the main checkout's file. A blanket "env dir has no
CLAUDE.md -> refuse" rule would break that on every single invocation — a
cardinal over-block. `test_same_repository_fallthrough_is_allowed` is the
guard on that, and it is the more important half of this file.

`db_path` IS REQUIRED AND KEYWORD-ONLY on `build_verdict`. Every bare
`build_verdict` call in this file passes `db_path=None`, and every one of
them is provably inert: the enclosing test either stubs `_run_memory_cli` /
`subprocess.run`, or short-circuits before the spawn (a bad index, an
unresolvable CLAUDE.md, a missing `_MEMORY_CLI`, a patched extractor). `None`
still MEANS the live store — what makes it safe is that nothing
consumes it. Delete the stub or the short-circuit above such a call and the
claim silently becomes false.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

import archive_pin  # noqa: E402
from shared.project_scope import same_repository  # noqa: E402


PINNED = (
    "# Project\n\n## Pinned Context\n\n"
    "<!-- pinned: 2026-01-01 -->\n"
    "### {title}\n"
    "body of {title}\n"
)


def _make_project(root: Path, title, layout="dot_claude"):
    """Create a project dir; `title=None` means NO CLAUDE.md at all."""
    root.mkdir(parents=True, exist_ok=True)
    if title is None:
        return root
    if layout == "dot_claude":
        target = root / ".claude" / "CLAUDE.md"
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        target = root / "CLAUDE.md"
    target.write_text(PINNED.format(title=title), encoding="utf-8")
    return root


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """A tmpdir guaranteed to be OUTSIDE any git repository.

    Without this the git fall-through could reach the PACT repo itself and
    quietly supply a real CLAUDE.md, which would make these tests measure the
    developer's checkout instead of the fixture. Asserted, not assumed.
    """
    probe = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "--git-common-dir"],
        capture_output=True, text=True, timeout=10,
    )
    assert probe.returncode != 0, (
        f"tmp_path is inside a git repo ({probe.stdout.strip()}) — the git "
        "fall-through would reach a real CLAUDE.md and these tests would "
        "measure the checkout, not the fixture"
    )
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return tmp_path


class TestResolutionIsDriven:
    """Controls first: the resolver must behave correctly when it is right,
    or a later refusal proves nothing about discrimination."""

    def test_env_dir_with_claude_md_is_used(self, isolated, monkeypatch):
        a = _make_project(isolated / "project_a", "PIN A")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(a))
        monkeypatch.chdir(a)
        path, base = archive_pin.resolve_claude_md()
        assert path == a / ".claude" / "CLAUDE.md"
        assert "### PIN A" in path.read_text(encoding="utf-8")
        assert Path(base) == a

    def test_legacy_layout_is_used(self, isolated, monkeypatch):
        a = _make_project(isolated / "legacy", "PIN L", layout="legacy")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(a))
        monkeypatch.chdir(a)
        path, _ = archive_pin.resolve_claude_md()
        assert path == a / "CLAUDE.md"

    def test_no_claude_md_anywhere_is_unevaluable(self, isolated, monkeypatch):
        empty = _make_project(isolated / "empty", None)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(empty))
        monkeypatch.chdir(empty)
        with pytest.raises(archive_pin._Unevaluable) as exc:
            archive_pin.resolve_claude_md()
        assert "not found" in exc.value.reason


class TestCrossProjectFallthroughIsRefused:
    """THE F-B GUARD. Fails on the pre-fix code, which returned project_a's
    file for an invocation that named project_b."""

    def test_cross_project_fallthrough_raises(self, isolated, monkeypatch):
        a = _make_project(isolated / "project_a", "PIN A")
        b = _make_project(isolated / "project_b", None)
        # Precondition, asserted so the test cannot pass for the wrong reason:
        # b must genuinely have no CLAUDE.md, or there is no fall-through.
        assert not (b / "CLAUDE.md").exists()
        assert not (b / ".claude" / "CLAUDE.md").exists()

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(b))
        monkeypatch.chdir(a)

        with pytest.raises(archive_pin._Unevaluable) as exc:
            archive_pin.resolve_claude_md()
        reason = exc.value.reason
        assert str(b) in reason, "refusal must name the directory that was set"
        assert "different project" in reason
        assert "project_a" in reason, (
            "refusal must name the file it would otherwise have used, or the "
            "curator cannot tell what was about to happen"
        )

    def test_the_fallthrough_target_really_was_reachable(
        self, isolated, monkeypatch
    ):
        """NON-VACUITY CONTROL for the test above.

        The refusal is only meaningful if resolution WOULD have succeeded on
        the wrong file. Drive the underlying resolver directly — the one
        `resolve_claude_md` wraps — and confirm it happily returns project_a
        while the environment names project_b. If this ever stops returning a
        path, the test above would pass because nothing resolved at all,
        which is a different and much safer world.
        """
        a = _make_project(isolated / "project_a", "PIN A")
        b = _make_project(isolated / "project_b", None)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(b))
        monkeypatch.chdir(a)

        path, _base = archive_pin._resolve_project_claude_md_with_base()
        assert path is not None, "no fall-through occurred — refusal is moot"
        assert "PIN A" in path.read_text(encoding="utf-8"), (
            "the unguarded resolver did NOT return the other project's file, "
            "so this suite is no longer testing the defect it was written for"
        )


class TestSameRepositoryFallthroughIsAllowed:
    """THE OVER-BLOCK GUARD, and the more important half of this file.

    PACT sets CLAUDE_PROJECT_DIR to a worktree where CLAUDE.md is gitignored
    and absent, and relies on the git fall-through to reach the main
    checkout. If the refusal above were written as "env dir has no CLAUDE.md
    -> refuse", it would fire on every PACT invocation. This proves it does
    not.
    """

    def test_same_repository_fallthrough_is_allowed(
        self, isolated, monkeypatch
    ):
        repo = isolated / "repo"
        _make_project(repo, "PIN ROOT")
        init = subprocess.run(["git", "init", "-q", str(repo)],
                              capture_output=True, text=True, timeout=30)
        assert init.returncode == 0, f"git init failed: {init.stderr}"

        # A subdirectory of the SAME repo, with no CLAUDE.md of its own --
        # structurally the worktree case: env names it, resolution falls
        # through to the repo root, and the root is the same project.
        sub = repo / "subproject"
        sub.mkdir()
        assert not (sub / "CLAUDE.md").exists()

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(sub))
        monkeypatch.chdir(sub)

        path, base = archive_pin.resolve_claude_md()   # must NOT raise
        assert "PIN ROOT" in path.read_text(encoding="utf-8")
        assert Path(base).resolve() == repo.resolve()

    def test_same_repository_predicate_is_true_for_a_subdir(
        self, isolated
    ):
        """The discriminator itself, tested directly."""
        repo = isolated / "repo2"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)],
                       capture_output=True, timeout=30)
        sub = repo / "nested"
        sub.mkdir()
        assert same_repository(sub, repo) is True

    def test_same_repository_predicate_is_false_across_repos(self, isolated):
        one = isolated / "one"; one.mkdir()
        two = isolated / "two"; two.mkdir()
        subprocess.run(["git", "init", "-q", str(one)],
                       capture_output=True, timeout=30)
        subprocess.run(["git", "init", "-q", str(two)],
                       capture_output=True, timeout=30)
        assert same_repository(one, two) is False

    def test_same_repository_predicate_fails_safe_outside_git(self, isolated):
        """A non-repo directory returns False, routing to a refusal. On a
        destructive path, declining to guess is the safe direction."""
        plain = isolated / "plain"; plain.mkdir()
        assert same_repository(plain, plain) is False


class TestSymlinkedClaudeMdAttribution:
    """FOLDED IN from the implementation review's symlink Minor — same defect
    family as F-B: a resolver producing a plausible wrong answer with no
    signal.

    The archive's project is now taken from the resolver's own `base` (the
    directory it found the file under, captured BEFORE descending into
    `.claude`) rather than re-derived from the returned path with `.resolve()`,
    which followed a leaf symlink and attributed the archive to the link
    target's parent.
    """

    def test_symlinked_claude_md_attributes_to_the_project_not_the_target(
        self, isolated, monkeypatch
    ):
        """Asserts on the CWD `archive_pin` actually hands the memory CLI,
        NOT on the resolver's base.

        The first version of this test checked `resolve_claude_md()[1]` and
        passed against the pre-fix code — the resolver's base was always
        right; what was wrong was `archive_pin` re-deriving the project from
        the leaf path instead of using that base. Testing the resolver
        measured the correct property on the wrong object, which is the same
        mistake this whole feature exists to catch. The mutation sweep caught
        it: reverting the attribution left the suite green.

        The memory layer detects `project_id` from CLAUDE_PROJECT_DIR, else
        git, else by walking up from the CWD — so the cwd handed to the
        subprocess IS the archive's project attribution.
        """
        proj = isolated / "myproject"; proj.mkdir()
        store = isolated / "elsewhere"; store.mkdir()
        real = store / "CLAUDE.md"
        real.write_text(PINNED.format(title="PIN S"), encoding="utf-8")
        os.symlink(real, proj / "CLAUDE.md")

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
        monkeypatch.chdir(proj)

        seen = {}

        def _spy(args, db_path=None, stdin_data=None, cwd=None):
            seen["cwd"] = cwd
            if args[0] == "save":
                return 0, json.dumps(
                    {"ok": True, "result": {"memory_id": "a" * 32}}
                ), ""
            return 0, json.dumps(
                {"ok": True, "result": {"context": stdin_data or ""}}
            ), ""

        monkeypatch.setattr(archive_pin, "_run_memory_cli", _spy)
        archive_pin.build_verdict(0, db_path=None)

        assert seen, "the memory CLI was never invoked — nothing was measured"
        assert Path(seen["cwd"]).name == "myproject", (
            f"archive filed under {Path(seen['cwd']).name!r} — the symlink "
            f"TARGET's directory — rather than the project that owns the pin"
        )


class TestVerdictCarriesTheResolvedPath:
    """`claude_md_path` in every verdict, so a wrong file is visible.

    A boolean 'resolved ok' would reproduce the defect exactly: correct-
    looking and silent about WHICH file. The curator has to be able to read
    the literal path and recognise it.
    """

    def test_unevaluable_from_bad_index_still_names_the_file(
        self, isolated, monkeypatch
    ):
        a = _make_project(isolated / "project_a", "PIN A")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(a))
        monkeypatch.chdir(a)
        verdict = archive_pin.build_verdict(99, db_path=None)
        assert verdict["outcome"] == "UNEVALUABLE"
        assert verdict["claude_md_path"] is not None
        assert "project_a" in verdict["claude_md_path"]

    def test_unresolvable_reports_null_path_not_a_guess(
        self, isolated, monkeypatch
    ):
        empty = _make_project(isolated / "empty", None)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(empty))
        monkeypatch.chdir(empty)
        verdict = archive_pin.build_verdict(0, db_path=None)
        assert verdict["outcome"] == "UNEVALUABLE"
        assert verdict["claude_md_path"] is None, (
            "null means no file was resolved; inventing a path here would be "
            "worse than reporting none"
        )

    def test_archived_verdict_names_the_file(self, isolated, monkeypatch,
                                             tmp_path, memory_store):
        a = _make_project(isolated / "project_a", "PIN A")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(a))
        monkeypatch.chdir(a)
        verdict = archive_pin.build_verdict(
            0, db_path=str(memory_store("mem.db"))
        )
        assert verdict["outcome"] == "ARCHIVED", verdict
        assert verdict["claude_md_path"] == str(
            (a / ".claude" / "CLAUDE.md")
        ), verdict["claude_md_path"]

    def test_cli_emits_the_path_as_json(self, isolated, monkeypatch, capsys):
        a = _make_project(isolated / "project_a", "PIN A")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(a))
        monkeypatch.chdir(a)
        rc = archive_pin.main(["--index", "99"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert "claude_md_path" in payload


class TestSyncSuppressionAndDeleteString:
    """The archival save must not write the block back, and must hand the
    caller a content handle for the removal.

    THE SUPPRESSION IS MEASURED BY BYTE-IDENTITY, not by a block count. A
    count check is conditioned on the hypothesis it tests — it only detects a
    writer that duplicates THIS block. Byte-identity fails if anything at all
    writes, including a writer nobody has thought of. That distinction is not
    academic here: if some other path also writes the block back, uniqueness
    never holds and the emit-time check redirects EVERY eviction to manual
    removal — a de-facto disabling of the automated path, arriving through
    the fix rather than the defect.
    """

    def test_archival_save_leaves_claude_md_byte_identical(
        self, isolated, monkeypatch, tmp_path, memory_store
    ):
        a = _make_project(isolated / "project_a", "PIN A")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(a))
        monkeypatch.chdir(a)
        claude_md = a / ".claude" / "CLAUDE.md"

        before = claude_md.read_bytes()
        verdict = archive_pin.build_verdict(0, db_path=str(memory_store("m.db")))

        assert verdict["outcome"] == "ARCHIVED", verdict
        assert verdict["memory_id"], (
            "the archive must have PERSISTED — a crashed save also leaves the "
            "file untouched, and the two are indistinguishable without this"
        )
        assert claude_md.read_bytes() == before, (
            "the archival save wrote to CLAUDE.md; the Working Memory "
            "projection is putting back the bytes the archive exists to remove"
        )

    def test_delete_string_is_the_verbatim_span_and_unique(
        self, isolated, monkeypatch, tmp_path, memory_store
    ):
        a = _make_project(isolated / "project_a", "PIN A")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(a))
        monkeypatch.chdir(a)
        verdict = archive_pin.build_verdict(0, db_path=str(memory_store("m.db")))

        content = (a / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        handle = verdict["delete_string"]
        assert handle in content, "delete_string must be verbatim in the file"
        assert content.count(handle) == 1, (
            "a non-unique handle makes the curator's Edit ambiguous"
        )
        assert "### PIN A" in handle
        assert verdict["occurrences"] == 1

    def test_delete_string_present_on_a_located_pin_unevaluable(
        self, isolated, monkeypatch
    ):
        """It is a property of WHICH PIN, not of whether the archive worked —
        so the escape-hatch path gets a mechanical boundary too and no
        consumer has to re-derive one."""
        a = _make_project(isolated / "project_a", "PIN A")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(a))
        monkeypatch.chdir(a)

        def _stub(args, **kwargs):
            if args[0] == "save":
                return 0, json.dumps(
                    {"ok": True, "result": {"memory_id": "e" * 32}}
                ), ""
            return 1, "", json.dumps({"ok": False, "error": "NOT_FOUND",
                                      "message": "gone"})

        monkeypatch.setattr(archive_pin, "_run_memory_cli", _stub)
        verdict = archive_pin.build_verdict(0, db_path=None)
        assert verdict["outcome"] == "UNEVALUABLE"
        assert verdict["delete_string"] is not None
        assert "### PIN A" in verdict["delete_string"]

    def test_unresolvable_pin_has_null_delete_string(
        self, isolated, monkeypatch
    ):
        a = _make_project(isolated / "project_a", "PIN A")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(a))
        monkeypatch.chdir(a)
        verdict = archive_pin.build_verdict(99, db_path=None)
        assert verdict["outcome"] == "UNEVALUABLE"
        assert verdict["delete_string"] is None


class TestArchivedDeleteUnsafe:
    """The FOURTH outcome: archive succeeded, removal unsafe.

    Not UNEVALUABLE — that means *cannot tell*, and a duplicated block is a
    KNOWN-BAD precondition. Not NOT_ARCHIVED — that asserts the archive
    failed, which is false, and putting a falsehood in the one report whose
    job is truthful measurement is the defect this feature exists to remove.

    It is a distinct outcome rather than a reason on an existing one because
    THE OUTCOME NAME MUST DETERMINE THE DISPOSITION. One outcome with two
    dispositions forces a reason table, and a reason table is how a
    permission gets inherited by a condition it was never designed for.
    """

    def _duplicated_project(self, root):
        """A CLAUDE.md where the pin's whole span appears twice."""
        root.mkdir(parents=True, exist_ok=True)
        (root / ".claude").mkdir(parents=True, exist_ok=True)
        # The duplicate must match the SPAN, not just the block: the span
        # runs to the next pin's start and so carries the trailing blank
        # line. A copy without it is a different string and the uniqueness
        # check would correctly pass -- the fixture, not the code, would be
        # wrong. The precondition assertion in the test catches that.
        block = "<!-- pinned: 2026-01-01 -->\n### DUP\nbody of DUP\n"
        (root / ".claude" / "CLAUDE.md").write_text(
            "# P\n\n## Pinned Context\n\n" + block + "\n"
            "## Working Memory\n\n" + block + "\n",
            encoding="utf-8",
        )
        return root

    def test_duplicate_block_yields_archived_delete_unsafe(
        self, isolated, monkeypatch, tmp_path, memory_store
    ):
        a = self._duplicated_project(isolated / "dup_project")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(a))
        monkeypatch.chdir(a)

        content = (a / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        verdict = archive_pin.build_verdict(0, db_path=str(memory_store("m.db")))

        # Precondition: the fixture really does duplicate the span, or this
        # test passes for the wrong reason.
        assert content.count(verdict["delete_string"]) > 1, (
            "fixture did not actually duplicate the block — the unsafe "
            "verdict below would be measuring something else"
        )
        assert verdict["outcome"] == "ARCHIVED_DELETE_UNSAFE", verdict
        assert verdict["memory_id"], (
            "the archive SUCCEEDED — the verdict must carry its id, because "
            "the content being safe is what makes this a redirect and not a trap"
        )
        assert verdict["occurrences"] > 1
        assert len(verdict["locations"]) == verdict["occurrences"]
        assert verdict["delete_string"] is not None

    def test_unsafe_is_not_mislabelled_as_a_failed_archive(
        self, isolated, monkeypatch, tmp_path, memory_store
    ):
        """Reusing NOT_ARCHIVED here would assert the archive failed when it
        succeeded — and downstream that maps to the `archive_refused` journal
        row, whose stated purpose is detecting a BROKEN ARCHIVER. A run of
        them would read as a failing archiver while every archive worked."""
        a = self._duplicated_project(isolated / "dup_project2")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(a))
        monkeypatch.chdir(a)
        verdict = archive_pin.build_verdict(0, db_path=str(memory_store("m.db")))
        assert verdict["outcome"] not in ("NOT_ARCHIVED", "UNEVALUABLE")
        assert verdict["contained"] is True

    def test_zero_occurrences_is_described_as_absence_not_ambiguity(self):
        """`occurrences != 1` catches BOTH directions, and they are different
        conditions with different curator responses.

        Zero means the block was read from the file moments earlier and is
        gone from the post-save re-read — concurrent modification, a live
        hazard on a file the curator may have open. A curator told
        "ambiguous" goes hunting for a second copy that does not exist.
        The disposition must be readable from what the verdict SAYS.
        """
        zero = archive_pin._unsafe_reason(0, "/p/CLAUDE.md", "a" * 32)
        many = archive_pin._unsafe_reason(3, "/p/CLAUDE.md", "a" * 32)

        assert "ambiguous" not in zero, (
            "zero occurrences is ABSENCE, not ambiguity — there is no second "
            "copy for the curator to disambiguate against"
        )
        assert "NO LONGER PRESENT" in zero
        assert "concurrently" in zero
        assert "ambiguous" in many, "the >1 case genuinely IS ambiguous"
        assert "occurs 3 times" in many
        # Both must still report the archive succeeded — that is what makes
        # either a redirect rather than a trap at the cap.
        for reason in (zero, many):
            assert "archive SUCCEEDED" in reason
            assert "a" * 32 in reason

    def test_both_unsafe_directions_share_the_outcome(self, isolated,
                                                      monkeypatch, tmp_path,
                                                      memory_store):
        """Same outcome name, different explanation — the split is in the
        reason, never in the disposition."""
        a = self._duplicated_project(isolated / "dup_project3")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(a))
        monkeypatch.chdir(a)
        verdict = archive_pin.build_verdict(0, db_path=str(memory_store("m.db")))
        assert verdict["outcome"] == "ARCHIVED_DELETE_UNSAFE"
        assert "ambiguous" in verdict["reason"]
        assert verdict["occurrences"] == 2


class TestPostResolutionFailuresKeepPinContext:
    """A failure AFTER the pin resolved must not report LESS than one before.

    Every failure `_run_memory_cli` can raise happens once CLAUDE.md has been
    read, the pins parsed and the block sliced — so the verdict can still
    name the file, the heading and the delete handle. It previously reported
    none of them, which made the LATER a failure occurred, the LESS context
    survived. Backwards, and not something anyone would choose: the
    pre-resolution control below reported MORE than a post-resolution CLI
    timeout did.

    It bit hardest exactly where it mattered most. A CLI timeout and a
    missing CLI are the canonical CANNOT-TELL cases — they are precisely when
    the escape hatch runs — so the hatch had no mechanical delete boundary in
    its own primary use case. Found by `coder-prose`, who blocked a deletion
    that depended on the contract rather than trusting it.

    Patched at the SUBPROCESS seam, not at `_run_memory_cli`: stubbing the
    function under test would bypass the branch under test.
    """

    def _project(self, isolated, monkeypatch, name):
        a = _make_project(isolated / name, "CTX PIN")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(a))
        monkeypatch.chdir(a)
        return a

    def _assert_full_context(self, verdict, label, reason_marker):
        """`reason_marker` is what stops a FALSE CLEAR.

        Asserting only the outcome and the fields lets a test pass by
        exercising a DIFFERENT path that happens to produce the same shape —
        which is exactly how `coder-prose`'s second probe reported CLEARED
        while verifying one path twice under two labels. Two green rows that
        were secretly the same row. So each test also proves the trigger it
        intended actually reached the verdict.
        """
        assert verdict["outcome"] == "UNEVALUABLE", f"{label}: {verdict}"
        assert reason_marker in verdict["reason"], (
            f"{label}: the intended trigger did not reach the verdict — "
            f"expected {reason_marker!r} in reason, got {verdict['reason']!r}. "
            f"This test would otherwise pass on any path with the same shape."
        )
        assert verdict["heading"] == "CTX PIN", (
            f"{label}: heading lost — the pin WAS resolved before this failure"
        )
        assert verdict["claude_md_path"] is not None, (
            f"{label}: claude_md_path lost — the file was read before this "
            f"failure, so the verdict can name it"
        )
        assert verdict["delete_string"] is not None, (
            f"{label}: delete_string lost — this is a CANNOT-TELL case, so it "
            f"is exactly when the escape hatch runs and needs a boundary"
        )
        assert "CTX PIN" in verdict["delete_string"]

    def test_cli_timeout_keeps_context(self, isolated, monkeypatch):
        self._project(isolated, monkeypatch, "t1")

        def _boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="save", timeout=1)

        monkeypatch.setattr(subprocess, "run", _boom)
        self._assert_full_context(archive_pin.build_verdict(0, db_path=None), "timeout",
                                  "timed out")

    def test_cli_launch_failure_keeps_context(self, isolated, monkeypatch):
        self._project(isolated, monkeypatch, "t2")

        def _boom(*a, **k):
            raise OSError("cannot launch")

        monkeypatch.setattr(subprocess, "run", _boom)
        self._assert_full_context(archive_pin.build_verdict(0, db_path=None), "launch",
                                  "could not launch")

    def test_missing_cli_keeps_context(self, isolated, monkeypatch):
        self._project(isolated, monkeypatch, "t3")
        monkeypatch.setattr(
            archive_pin, "_MEMORY_CLI", Path("/nonexistent/cli.py")
        )
        self._assert_full_context(archive_pin.build_verdict(0, db_path=None), "missing cli",
                                  "not found")

    def test_pre_resolution_failure_still_reports_less(
        self, isolated, monkeypatch
    ):
        """THE CONTROL, and the reason the above is a defect rather than a
        design choice. A genuinely pre-resolution failure — the pin never
        resolved — correctly reports no heading and no delete handle, while
        still naming the file, because resolution itself succeeded. If this
        ever started reporting a heading, the tests above would be passing
        for the wrong reason."""
        self._project(isolated, monkeypatch, "t4")
        verdict = archive_pin.build_verdict(99, db_path=None)
        assert verdict["outcome"] == "UNEVALUABLE"
        assert verdict["heading"] is None
        assert verdict["delete_string"] is None
        assert verdict["claude_md_path"] is not None


class TestDeliberateDeleteStringOmissions:
    """Two post-resolution paths omit `delete_string` ON PURPOSE.

    A derived census of every `_Unevaluable` raise flagged these two as
    "post-resolution without full context" — and they are correct. That is
    the distinction the contract invariant exists to make legible:

        each field is present iff THE FACT IT NAMES was established.

    `delete_string` does not name "the block"; it names *a usable handle for
    the removal Edit*. On both paths a block was computed and is NOT usable:
    an empty block is no handle at all, and a block failing the source-side
    verbatim tripwire is one an Edit provably cannot match. **Emitting a
    handle known to be bad is worse than emitting none**, because the
    caller's entire reason to trust it is that it was checked.

    `heading` and `claude_md_path` ARE present on both, because those facts
    were established. This class exists so a future "consistency fix" that
    adds `delete_string` here goes red with the reason attached.
    """

    def test_empty_block_omits_the_handle_but_keeps_the_rest(
        self, isolated, monkeypatch
    ):
        a = _make_project(isolated / "empty_block", "EB PIN")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(a))
        monkeypatch.chdir(a)
        monkeypatch.setattr(archive_pin, "extract_pin_block",
                            lambda *args, **kw: "   \n  ")
        verdict = archive_pin.build_verdict(0, db_path=None)
        assert verdict["outcome"] == "UNEVALUABLE"
        assert "empty" in verdict["reason"]
        assert verdict["delete_string"] is None, (
            "an empty block is not a usable delete handle; emitting it would "
            "hand the caller something an Edit cannot match"
        )
        assert verdict["heading"] == "EB PIN"
        assert verdict["claude_md_path"] is not None

    def test_non_verbatim_block_omits_the_handle_but_keeps_the_rest(
        self, isolated, monkeypatch
    ):
        a = _make_project(isolated / "not_verbatim", "NV PIN")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(a))
        monkeypatch.chdir(a)
        monkeypatch.setattr(
            archive_pin, "extract_pin_block",
            lambda *args, **kw: "### Fabricated\nnot from the source",
        )
        verdict = archive_pin.build_verdict(0, db_path=None)
        assert verdict["outcome"] == "UNEVALUABLE"
        assert "not verbatim" in verdict["reason"]
        assert verdict["delete_string"] is None, (
            "a block that fails the verbatim tripwire is a handle an Edit "
            "provably cannot match — worse than none"
        )
        assert verdict["heading"] == "NV PIN"
        assert verdict["claude_md_path"] is not None


class TestTheSharedPredicateIsOneObject:
    """archive_pin holds the shared predicate itself, loaded as a package member.

    MUTANT that reddens this arm: load project_scope by putting hooks/shared on
    sys.path and importing it by its bare name. The process then holds a
    top-level `project_scope` beside `shared.project_scope`, so archive_pin's
    predicate is a different object from the working-memory guard's, and a
    patch applied to one misses the other.
    """

    def test_archive_pin_re_exports_the_shared_predicate_object(self):
        import sys

        from shared import project_scope

        assert archive_pin._stays_in_declared_project is project_scope.stays_in_declared_project, (
            "archive_pin's predicate is not the shared.project_scope object"
        )
        assert "project_scope" not in sys.modules, (
            "project_scope is registered as a TOP-LEVEL module, so the process "
            "holds two copies of it"
        )

    def test_archive_pin_and_pact_session_hold_the_shared_record_reader(self):
        """MUTANT that reddens this arm: give pact_session its own copy of the
        reader instead of re-exporting the shared one, or load it into
        archive_pin from skills/ by file path. The two callers of the scope
        check then read the record through different objects."""
        from scripts import pact_session
        from shared import project_scope

        reader = project_scope.get_worktree_identity_from_session_record
        assert archive_pin._get_worktree_identity_from_session_record is reader, (
            "archive_pin's record reader is not the shared.project_scope object"
        )
        assert pact_session.get_worktree_identity_from_session_record is reader, (
            "pact_session's record reader is not the shared.project_scope object"
        )
