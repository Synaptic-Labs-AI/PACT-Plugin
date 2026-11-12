# Canary Verification Checklist

This checklist defines a 3-tier verification process for PRs that touch `pact-plugin/`. Each tier adds a layer of confidence that protocol changes have not introduced regressions.

**When to apply**: Any PR that modifies files under `pact-plugin/` (protocols, commands, agents, skills, hooks).

---

## Tier 1: Automated Verification

Run all verification scripts from the repository root. Every PR touching `pact-plugin/` must pass all four. The pytest suite is a fifth obligation of this tier. It has its own row in the checklist below, and its own pass condition.

```bash
bash scripts/verify-protocol-extracts.sh
bash scripts/verify-scope-integrity.sh
bash scripts/verify-task-hierarchy.sh
bash scripts/verify-worktree-protocol.sh
```

**Expected output**: Each script prints one result line for each check, prefixed with `✓` or `✗`, then a summary block. `verify-protocol-extracts.sh` also prints a `VERIFY-CALL: <file>` line before each extract it compares. That line has no prefix and is not a result line. The other three scripts do not print it.

```
=== Summary ===
Passed: N
Failed: 0

VERIFICATION PASSED
```

Any script exiting with `VERIFICATION FAILED` (exit code 1) means the PR has broken a protocol invariant and must not be merged.

### Checklist

**How to read these counts.** Each number is the `Passed: N` value of a run that ends with `Failed: 0`. It is the number of checks the script runs. Three of the four counts do not change. The `verify-scope-integrity.sh` count moves with the number of files in `pact-plugin/agents/`, and its row gives the rule that makes it checkable. The four count rows follow, and the suite-run row follows them:

- [ ] `verify-protocol-extracts.sh` passes (19 checks) -- each SSOT extract agrees with its section, anchored by H2 heading text. FIXED count: one check for each `verify` call in the script. `pact-plugin/tests/test_canary_checklist_count.py` reads this number and compares it against a run, so an edit to this row alone reddens that test.
- [ ] `verify-scope-integrity.sh` passes (86 checks) -- cross-references, naming conventions, nesting limits, worktree integration, memory hooks, executor interface, agent persistent memory. THIS COUNT MOVES WITH THE AGENT DIRECTORY. It is 64 fixed checks, plus two loops across `pact-plugin/agents/`. The nesting-limit loop skips the 4 agents named in the `case` statement of the script and checks each remaining file. The `memory: user` loop checks each file. Today that directory holds 13 files, which gives 64 + 9 + 13 = 86. If you see a different number, count the agent files before you report a defect. `pact-plugin/tests/test_canary_checklist_count.py` reads the fixed-check count and the skip count from this row, predicts the total from a real count of the agent directory, and compares it against a run. Adding an agent file keeps that test green. Changing the fixed checks or the skip set without correcting this row reddens it.
- [ ] `verify-task-hierarchy.sh` passes (28 checks) -- task lifecycle patterns in all command files. FIXED count: no check comes from the contents of a directory. `pact-plugin/tests/test_canary_checklist_count.py` reads this number and compares it against a run, so an edit to this row alone reddens that test. Adding a command file does not move the count.
- [ ] `verify-worktree-protocol.sh` passes (20 checks) -- worktree skill existence, command references, path propagation. FIXED count: no check comes from the contents of a directory. `pact-plugin/tests/test_canary_checklist_count.py` reads this number and compares it against a run, so an edit to this row alone reddens that test. Adding a skill directory does not move the count.

The suite-run row carries no check count, because the wrapper it names runs pytest and does not print a `Passed: N` line. Read its pass condition in [The suite run, and which tree it measured](#the-suite-run-and-which-tree-it-measured) below.

- [ ] The merge-gate suite run passes, and its capture gives the branch you review -- run the suite through `scripts/verify-gate-tree.sh`. THIS ROW IS NOT TICKED AGAINST THE `**Expected output**` BLOCK ABOVE. Its pass condition is the eight-part list in [The suite run, and which tree it measured](#the-suite-run-and-which-tree-it-measured). READ the capture against that list. Tick this row only after the read. A tick made when the command returns is a tick for the exit code alone.

### What failures mean

| Script | Common Failure Cause | Fix |
|--------|---------------------|-----|
| `verify-protocol-extracts.sh` | An extract file does not agree with its SSOT section. Or a heading sentinel in the script does not name an H2 heading in the SSOT | Regenerate the extract from its SSOT section. Or update the H2 heading sentinels in the script |
| `verify-scope-integrity.sh` | A cross-reference was broken, a required pattern was removed, or a new agent file is missing expected content | Trace the `✗` output to the specific check and restore the expected pattern |
| `verify-task-hierarchy.sh` | A command file's Task Hierarchy section is missing a lifecycle keyword (`TaskCreate`, `in_progress`, `completed`) | Add the missing lifecycle pattern to the command's Task Hierarchy section |
| `verify-worktree-protocol.sh` | A command file lost its worktree skill reference or a skill file is missing its frontmatter | Restore the worktree reference or skill frontmatter |

### The suite run, and which tree it measured

The four scripts above do not run the pytest suite. Run the merge-gate suite through `scripts/verify-gate-tree.sh`, so that the capture file records which tree the run measured. A summary from a different checkout is correct about that checkout. It says nothing about the branch you review, and the two summaries read the same.

**Before the run**, get the head commit of the branch you review from a source that is NOT the tree you are about to measure. The PR page gives it. Record it. You compare the capture against that commit below, and a commit read from the measured tree agrees with itself and checks nothing.

```bash
cd pact-plugin
CAPTURE="/tmp/gate-$(basename "$(cd -P .. && pwd)").txt"
rm -f "$CAPTURE"
env -u PYTEST_ADDOPTS ../scripts/verify-gate-tree.sh "$(cd -P .. && pwd)" "$CAPTURE" -q
echo "wrapper exit: $?"
```

The first argument is the worktree the run must measure. The second is the capture file. Each argument after those two goes to pytest. The capture path must be outside the declared tree: an in-tree capture is refused with exit code 2 before pytest starts and writes no capture, so use an outside path such as the `/tmp` path above. The script writes `GATE-TREE`, `GATE-HEAD`, `GATE-DIRTY-PATHS`, `GATE-CWD` and `GATE-COMMAND` above the pytest output, then appends `GATE-HEAD-AFTER`, `GATE-DIRTY-PATHS-AFTER`, `GATE-TREE-ATTRIBUTION`, and finally `GATE-PYTEST-EXIT` as the last stamp. `GATE-TREE-ATTRIBUTION` is `stable` only when HEAD and the complete before/after `git status --porcelain` text are identical; equal dirty counts alone are insufficient. A nonzero pytest status is preserved. A green pytest run whose tree moved returns 6 and is `unattributable`, not a pass.

Five properties of that command are load-bearing. Keep each one:

- **No pytest argument names a path.** A path argument moves the tests pytest collects away from the tree the stamps name. The stamps stay correct about the declared tree, and the run measures a different one.
- **Keep `-q`.** With it, the summary line is bare, in the shape `2 passed in 0.00s`. Without it, pytest decorates the same line, in the shape `============ 2 passed in 0.00s ============`. A check anchored on the start of the line then finds nothing, and that reads the same as a suite that did not start.
- **Quote the capture argument.** Unquoted, a capture path that holds a space breaks into fragments. The capture goes to the first fragment, and the tail fragments join the pytest command line.
- **Resolve the declared path physically with `$(cd -P .. && pwd)`.** `$PWD/..` keeps a symlinked spelling. If a symlink sits at the `pact-plugin` component, the wrapper then refuses a run that sits in the correct tree.
- **The wrapper clears `PYTEST_ADDOPTS` before pytest.** The variable reaches pytest without touching the command line, so no stamp records it. Measured before the wrapper cleared it: it narrowed a run to 9 tests of 14662 with an exit of 0 and a clean stamp. The `env -u PYTEST_ADDOPTS` prefix is now unnecessary but harmless.

**The declared path comes from the current directory, so the wrapper compares the current directory against itself.** That comparison catches a run started outside a git worktree. IT DOES NOT CATCH A RUN IN AN INCORRECT CHECKOUT. Only the stamps catch that, and only when you read them.

#### Pass condition

Read the capture first. Tick the Tier 1 row second. All eight must hold:

1. **The wrapper exit is `0`**, read from the terminal at the moment of the run. This one cannot be read from the capture. A refusal exits `2` and writes NOTHING. pytest can also exit 2, so a 2 with a capture present is an interrupted run rather than a refusal. A capture from an earlier run at the same path then survives byte for byte, and it reads clean for a run that did not occur. The `rm -f` line and this exit code are the two guards against that.
2. **The capture file is present.** If it is absent, the run refused and wrote nothing.
3. **`GATE-TREE`** names the worktree you review.
4. **`GATE-HEAD`** is the commit you recorded from the PR page, and **`GATE-HEAD-AFTER`** is that same commit. A literal `HEAD` on either line is not a commit. A tree with no commit at HEAD is refused before pytest starts (exit 2, no capture), so a run that produced a capture names a commit; the reading stays as a backstop. The after-HEAD must equal both `GATE-HEAD` and the externally recorded PR SHA, so a commit created during the run cannot pass.
5. **`GATE-DIRTY-PATHS` and `GATE-DIRTY-PATHS-AFTER`** are both `0`, and **`GATE-TREE-ATTRIBUTION`** is `stable`. A count above zero on either line means the tree holds changes that `GATE-HEAD` does not carry, so the run did not measure that commit alone. `stable` certifies that the complete before/after `git status --porcelain` text is identical, not merely that the two dirty counts match. A failed after-run `git status` prints `unknown` for `GATE-DIRTY-PATHS-AFTER`: `unknown` is not `0` and fails this item.
6. **`GATE-PYTEST-EXIT`** is `0` and is the last stamp, after all three after-run stamps. A moved tree makes the wrapper exit 6 even when this stamp is 0, so the wrapper exit and the attribution condition above must both hold.
7. **`GATE-COMMAND`** is `python3 -m pytest -q` and nothing more. A path, a `-k` filter, a `--co` or a `--lf` narrows what pytest collected. Items 1 to 6 stay correct for a narrowed run, and a narrowed run measured a part of the suite rather than the suite. Measured: `--co` exits 0 and runs no test, and a `-k` that selects a subset exits 0.
8. **The summary line** in the capture reads `<N> passed`, with no `deselected` and no error count. `<N>` marks the SHAPE of the line. Do not fetch a number to compare it against: the count moves with every legitimate test addition, so a pinned number goes stale. This item catches a `-k` or `-m` selection and a collect-only run, and the source does not matter. IT DOES NOT CATCH A PATH, which narrows with a clean summary; the wrapper clears `PYTEST_ADDOPTS` before pytest, which closes the environment channel that could carry one. Measured before the wrapper cleared it: `PYTEST_ADDOPTS='tests/test_canary_checklist_count.py'` gave a stamp of `python3 -m pytest -q`, an exit of 0, and `9 passed` from a suite of 14662.

Do not merge a PR of which the capture fails one of the eight.

A route stays open. A `collect_ignore` entry in the pytest configuration narrows the run with a clean summary and a clean stamp, and a `testpaths` line or a path line does the same by the same mechanism. Where that line is committed, it is visible in review and it narrows CI in the same way, so it is recorded here rather than checked. Where the file is untracked, `GATE-DIRTY-PATHS` is above zero and item 5 catches it. Where the file is untracked AND an ignore rule matches it, nothing here catches it. A default marker expression is different: it deselects, so item 8 catches it. Measured: an ignored `conftest.py` with `collect_ignore` gives `GATE-DIRTY-PATHS 0`, a clean stamp and `1 passed` of 3 tests.

The after-run bracket is a cheap shared-tree guard, not decisive clean-SHA certification. An in-place edit while already dirty can leave the porcelain text unchanged, and ignored files are outside that text. Decisive local certification still requires a detached clean worktree at the named SHA.

#### A tree the wrapper refuses

The script identifies the tree with git, and it reads the working directory to do it:

- If the working directory has no git ancestor, the script refuses with exit code 2 and writes no capture.
- If the tree has no commit at HEAD (an unborn branch), the script refuses with exit code 2, names the state, and writes no capture.
- If the working directory has a git ancestor, and the declared path resolves to that same ancestor, the script proceeds and stamps that ancestor.
- If the declared path resolves elsewhere, the script refuses with exit code 2, and a symlink at the pact-plugin component is one route to that.
- The script does not test how the files arrived in the directory.

So the origin of a tree does not decide the outcome. Two things do: where the working directory sits, and where the declared path resolves. Measured: an export that carries no `.git`, unpacked in an enclosing worktree, proceeds and stamps the enclosing repository. A copy made with `cp -R` carries its own `.git`, proceeds, and stamps the copy path with the head of the source.

For a directory with no git ancestor, call pytest directly and keep the output:

```bash
cd pact-plugin
env -u PYTEST_ADDOPTS python3 -m pytest -q 2>&1 | tee /tmp/gate-no-tree.txt
```

That run writes no stamp, so it says nothing about which tree it measured. Do not tick the Tier 1 row from it.

---

## Tier 2: Structural Review

Human reviewers verify structural properties that automated scripts cannot fully cover. These checks apply to every PR touching `pact-plugin/`.

### Checklist

- [ ] **Protocol extract heading sentinels stay valid** -- If the PR modifies `pact-protocols.md`, make sure the H2 heading sentinels in `verify-protocol-extracts.sh` (in the script itself) name the headings the SSOT carries now
- [ ] **Cross-references are intact** -- Protocol files that reference other protocols (e.g., `pact-scope-contract.md` referencing `rePACT.md`) still point to correct targets
- [ ] **SSOT extracts agree with their sources** -- Extracted protocol files are verbatim copies of their SSOT sections in `pact-protocols.md` (the automated script compares the bytes, and a reviewer must check that the heading sentinels themselves are correct)
- [ ] **No orphaned references** -- Search for references to renamed or deleted files; confirm no dead links remain
- [ ] **Agent definition consistency** -- All agent `.md` files under `pact-plugin/agents/` have matching frontmatter fields (`memory: user`, nesting limit, HANDOFF format)
- [ ] **Command file structure preserved** -- Command files retain their expected section headings (Task Hierarchy, phase sections, etc.)
- [ ] **Version numbers updated if needed** -- If the PR represents a version bump, both `plugin.json` and `marketplace.json` reflect the same version

---

## Tier 3: Behavioral Validation

Run a live PACT orchestration cycle to verify that the framework functions correctly end-to-end. Apply this tier at major milestones (version bumps, multi-step implementation rounds, or any PR that changes orchestration flow).

### Approach

Use the PACT framework on itself (dogfooding): run `/PACT:orchestrate` or `/PACT:comPACT` on a real task and observe whether the full lifecycle works.

### Checklist

- [ ] **Agent spawning works** -- Specialist agents are invoked and receive their prompts (check that the orchestrator delegates rather than acting directly)
- [ ] **Handoff format is correct** -- Agent responses end with the 5-item HANDOFF structure (Produced, Key decisions, Areas of uncertainty, Integration points, Open questions)
- [ ] **Memory saves succeed** -- The `pact-memory` skill saves context without errors; saved memories are retrievable via search
- [ ] **Worktree lifecycle completes** -- `worktree-setup` creates a worktree, agents work within it, and `worktree-cleanup` removes it after PR
- [ ] **Verification obligations pass in the worktree** -- All Tier 1 obligations pass when run from the worktree working directory
- [ ] **Task tracking is consistent** -- Task statuses follow the `pending -> in_progress -> completed` lifecycle without orphaned or stuck tasks
- [ ] **Phase transitions are clean** -- Each PACT phase completes with a handoff before the next begins; no phase is skipped without documented rationale

### When to run

- Before merging a version bump PR
- After completing a multi-step implementation round (e.g., all D1-D6 steps)
- When changing orchestration logic in `orchestrate.md`, `comPACT.md`, or `rePACT.md`
- Quarterly as a general regression check
