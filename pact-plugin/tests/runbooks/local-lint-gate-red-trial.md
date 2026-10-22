# Runbook: Local Lint Gate Red-Trial (issue #1634)

Certifies the RED direction of `scripts/verify-local-lint-gate.sh` — that a
tree CI's lint step would reject also reddens the local gate, that a missing
linter refuses rather than passes, and that the script lints its own tree no
matter where it is invoked from. The green direction (both legs pass on a
clean tree) is covered by the merge gate itself every run; what only this
runbook can show is the gate failing when it must.

Run this before merging any change to the gate script, the workflow's lint
legs, or `commands/peer-review.md`'s lint-read step, and after any ruff major
bump on the dev machine.

## 1. The rig — a detached copy, never the worktree

```bash
WT=/Users/mj/Sites/collab/PACT-prompt/.worktrees/fix/1634-local-lint-gate   # adjust to the checkout under test
SCRATCH=$(mktemp -d /tmp/lintgate-red-trial.XXXXXX)
cp -R "$WT/." "$SCRATCH/"
rm -f "$SCRATCH/.git"          # a detached copy: no git operation may reach the real repo
```

Probes are planted as SEPARATE files at the top level of the copy's
`pact-plugin/` — the same location class as the stray conftest that escaped
every local gate on PR #1632 while CI caught it.

- F401 probe (`pact-plugin/_f401_probe_local_lint_red_trial.py`): one line, `import sys`
- F821 probe (`pact-plugin/_f821_probe_local_lint_red_trial.py`): one line, `print(undefined_name_for_f821_probe)`

Every arm below records its PREDICTION before the run and its observed
outcome after; an arm whose outcome matches its prediction for an unexamined
reason is recorded as such, not as a pass. Exit codes are captured unpiped
(`cmd > out; rc=$?`), never through a pipeline.

## 2. Baseline (clean copy, no probes)

**Predict**: exit 0; stdout stamps `LINT-GATE-ROOT <scratch>`,
`LINT-GATE-RUFF <version>`, `LINT-GATE-GREEN both legs`.

```bash
bash "$SCRATCH/scripts/verify-local-lint-gate.sh" > "$SCRATCH/out-baseline.txt" 2>&1; echo "rc=$?"
cat "$SCRATCH/out-baseline.txt"
```

## 3. Arm A — F401 probe (leg 1 load-bearing)

**Predict**: gate exit 1, stderr marker `LINT-GATE-LEG-1-FAILED exit 1`, ruff
output names `_f401_probe_local_lint_red_trial.py` with `F401`; leg 2 never
runs (fail-fast mirrors CI's `bash -e` block). DIRECT leg-2 run on the same
tree: exit 0 — the demonstrated miss proving leg 1's `--select F401` is
load-bearing (an unused import is invisible to an F821-only select).
Under the exit taxonomy of remediation cycle 1, FINDINGS keep exit 1; a
ruff runtime exit above 1 here would instead be a leg ERROR mapping to
exit 3 (arm F) — exit 1 from this arm is what proves the findings path.

```bash
printf 'import sys\n' > "$SCRATCH/pact-plugin/_f401_probe_local_lint_red_trial.py"
bash "$SCRATCH/scripts/verify-local-lint-gate.sh" > "$SCRATCH/out-f401.txt" 2>&1; echo "gate rc=$?"
cd "$SCRATCH" && ruff check --select F821 --target-version py39 pact-plugin/ > "$SCRATCH/out-f401-leg2direct.txt" 2>&1; echo "leg2-direct rc=$?"
rm "$SCRATCH/pact-plugin/_f401_probe_local_lint_red_trial.py"
```

## 4. Arm B — F821 probe (both legs kill)

**Predict**: gate exit 1 at leg 1 (`F821` is in leg 1's select), finding
names `_f821_probe_local_lint_red_trial.py`; DIRECT leg-2 run: exit 1 — leg 2
kills it too, which is the deliberate redundancy that keeps F821 coverage
independent of leg 1's path list. The gate's own fail-fast means only the
direct run exercises leg 2 here. Exit 1 on both runs is the FINDINGS path
under the cycle-1 taxonomy (a ruff crash would map to exit 3 instead).

```bash
printf 'print(undefined_name_for_f821_probe)\n' > "$SCRATCH/pact-plugin/_f821_probe_local_lint_red_trial.py"
bash "$SCRATCH/scripts/verify-local-lint-gate.sh" > "$SCRATCH/out-f821.txt" 2>&1; echo "gate rc=$?"
cd "$SCRATCH" && ruff check --select F821 --target-version py39 pact-plugin/ > "$SCRATCH/out-f821-leg2direct.txt" 2>&1; echo "leg2-direct rc=$?"
rm "$SCRATCH/pact-plugin/_f821_probe_local_lint_red_trial.py"
```

## 5. Arm C — negative control (probes removed)

**Predict**: exit 0, `LINT-GATE-GREEN both legs`. Proves the reds above came
from the probes, not from the rig.

```bash
bash "$SCRATCH/scripts/verify-local-lint-gate.sh" > "$SCRATCH/out-control.txt" 2>&1; echo "rc=$?"
```

## 6. Arm D — ruff absent (fail-closed refusal)

**Predict**: exit 2, stderr `GATE REFUSED: ruff is not available` plus install
advice, and NO `LINT-GATE-RUFF` stamp (refusal precedes the stamps). ruff on
this machine lives in `~/.local/bin`, which `PATH='/usr/bin:/bin'` strips.

```bash
env PATH='/usr/bin:/bin' bash "$SCRATCH/scripts/verify-local-lint-gate.sh" > "$SCRATCH/out-nopath.txt" 2>&1; echo "rc=$?"
```

## 7. Arm E — invoked from elsewhere (root pinned internally)

**Predict**: invoked by absolute path with CWD `/`: exit 0, and
`LINT-GATE-ROOT` names the SCRATCH root, not `/` — the script lints the tree
it lives in; the caller's directory cannot re-narrow the sweep.

```bash
cd / && bash "$SCRATCH/scripts/verify-local-lint-gate.sh" > "$SCRATCH/out-fromroot.txt" 2>&1; echo "rc=$?"; head -1 "$SCRATCH/out-fromroot.txt"
```

## 8. Arm F — ruff runtime crash (exit-3 path, added with the exit taxonomy)

**Predict**: a `ruff.toml` the linter cannot load, planted at the scratch
copy's root, makes ruff exit with a runtime code above 1 on leg 1; the gate
maps that to exit 3 with a `LINT-GATE-LEG-1-ERROR exit <observed>` marker —
disjoint from both findings (exit 1) and refusal (exit 2). The observed ruff
exit rides in the marker line, because that exit is the crash's diagnosis.

```bash
printf 'select = 5\n' > "$SCRATCH/ruff.toml"   # wrong type: config-load failure
bash "$SCRATCH/scripts/verify-local-lint-gate.sh" > "$SCRATCH/out-crash.txt" 2>&1; echo "gate rc=$?"
rm "$SCRATCH/ruff.toml"
```

This arm also exercises the crash route a real config regression would take
(the rig's own `ruff.toml` probe is the same file class §9's narrowing note
names), and it cannot contaminate the repo: the config lands only in the
detached copy.

## 9. Outcomes — run of 2026-10-22 (test-engineer, independent TEST-phase repetition)

| Arm | Prediction | Observed | Verdict |
|-----|------------|----------|---------|
| Baseline | exit 0, three stamps | rc=0; LINT-GATE-ROOT scratch; LINT-GATE-RUFF `ruff 0.16.5`; LINT-GATE-GREEN both legs | PASS |
| A F401 | gate rc 1, LEG-1 marker, file named; leg2-direct rc 0 | rc=1; F401 finding names `_f401_probe_local_lint_red_trial.py:1:8` + `LINT-GATE-LEG-1-FAILED exit 1`; leg 2 never ran; leg2-direct rc=0 `All checks passed!` | PASS |
| B F821 | gate rc 1, file named; leg2-direct rc 1 | rc=1 at leg 1; F821 finding names `_f821_probe_local_lint_red_trial.py:1:7`; leg2-direct rc=1, same kill | PASS |
| C control | exit 0, LINT-GATE-GREEN | rc=0, `LINT-GATE-GREEN both legs` | PASS |
| D no-ruff | exit 2, GATE REFUSED, no RUFF stamp | rc=2 under `env PATH='/usr/bin:/bin'` (ruff lives in `~/.local/bin`, excluded — and the refusal itself proves the strip took: a leaked ruff would have linted instead); observed stderr, verbatim: `GATE REFUSED: ruff is not available, so the lint legs cannot run.` / `A missing linter must not read as a pass. Install or upgrade ruff:` / `python3 -m pip install --upgrade ruff` (full text in `out-nopath.txt`); LINT-GATE-RUFF count 0 | PASS |
| E from / | rc 0, LINT-GATE-ROOT = scratch | rc=0; first line `LINT-GATE-ROOT /private/tmp/lintgate-red-trial.<suffix>` | PASS |

6/6 arms matched their predictions. Scratch copy: `/tmp/lintgate-red-trial.ro9vIP`
(source: worktree HEAD `26a7b46a`, clean tree).

THE OUTCOMES TABLE ABOVE IS THE DURABLE RECORD OF THIS RUN. Every observed
value that a later reader needs — exit codes, marker lines, verbatim refusal
text — is quoted IN the table rows; the `out-*.txt` capture files beside the
scratch copy are the run's working artifacts, and the scratch copy itself is
disposable. Do not point a reader at `/tmp` for evidence: a reboot or the
tmp cleaner removes it, and what the table does not carry is then gone.

Operator: test-engineer. Copy source: worktree HEAD `26a7b46a` (2026-10-22).
Resolved ruff: `ruff 0.16.5` (baseline output `LINT-GATE-RUFF`).
Devops-engineer's CODE-phase certification (Task #18) ran the same rig once;
this run is the independent TEST-phase repetition this row records.

## 10. Outcomes — re-run of 2026-10-23 (devops-engineer, remediation cycle 1: exit taxonomy)

Re-certified arms under the new exit taxonomy (findings stay exit 1; ruff
runtime exits above 1 map to exit 3; refusals stay exit 2). Predictions were
written BEFORE execution, per this runbook's rule; this table is the durable
record of the run.

| Arm | Prediction | Observed | Verdict |
|-----|------------|----------|---------|
| Baseline | rc=0, `LINT-GATE-GREEN both legs` — the taxonomy change must not disturb the green path | rc=0; final line `LINT-GATE-GREEN both legs` | PASS |
| A F401 | gate rc=1, marker `LINT-GATE-LEG-1-FAILED exit 1`, finding names the probe | rc=1; marker present verbatim; finding names `_f401_probe_local_lint_red_trial.py` | PASS |
| B F821 | gate rc=1 at leg 1 (FAILED marker), leg2-direct rc=1 | rc=1 at leg 1; leg2-direct rc=1 | PASS |
| F crash | gate rc=3, marker `LINT-GATE-LEG-1-ERROR exit 2` carrying the observed ruff exit | rc=3; marker `LINT-GATE-LEG-1-ERROR exit 2` present; ruff's own message names the scratch `ruff.toml` as the cause | PASS |

4/4 arms matched their predictions. Arms C (control), D (no-ruff), and E
(from-/) are unchanged by the taxonomy — their 2026-10-22 rows stand.
Scratch copy: `/tmp/lintgate-remed.8qQlDd` (source: the remediation-cycle
working tree — script carrying the exit-3 mapping; disposable).

Operator: devops-engineer (reviewer-reuse fixer). Resolved ruff: `ruff 0.16.5`.

## 11. Skew note

The gate certifies a LOCAL green under the stamped local ruff. CI installs
ruff unpinned, and CI's lint step now echoes its resolved version
(`ruff --version`, commit 417d76b4). A local green that disagrees with a CI
red needs BOTH stamps before it can be attributed; version verdict-parity
beyond that is unverifiable locally and accepted as residual by the plan
(docs/plans/local-lint-gate-plan.md, Risk table).

A second narrowing the byte-parity comparison does not cover: the mirror and
the parity test compare INVOCATION LINES on the assumption that no ruff
CONFIGURATION file exists anywhere in the repo (no `ruff.toml`, no
`.ruff.toml`, no `[tool.ruff]` in a `pyproject.toml` — verified absent on
this branch). A config file would change the EFFECTIVE rule surface — its
own select, its own exclusions — without changing a single compared byte, so
parity would stay green over a different lint than either side's invocation
line suggests. If a ruff config ever lands in this repo, extend the parity
guard to compare the config surface too before trusting the mirror.

