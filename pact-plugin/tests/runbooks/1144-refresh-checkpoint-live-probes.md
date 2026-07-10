# Runbook: /PACT:refresh Mid-Workstream Checkpoint Live Probes

**Purpose:** the post-merge runtime-confirmation gate for the /PACT:refresh
command (#1144). The CI layers ship in the PR: schema round-trips
(`test_session_journal.py`), the fail-safe resolver + spent-check + arbitration
(`test_session_resume.py`), the real-`main()` surfacing matrix
(`test_session_init.py::TestRefreshSurfacingMatrix`), the reaper guard truth
table (`test_session_end.py`), and the command structural pins
(`test_commands_structure.py`). What CI CANNOT prove is compaction-boundary
behavior: whether the surfaced prompt survives an actual `/compact` render,
whether tmux-mode TaskStop matches the in-process shutdown semantics the
design encoded, and whether the full fire-once cycle behaves end-to-end in a
real session. Those are runtime-only — this runbook is that gate.

Manual execution is REQUIRED before the minor release tag (see
RUNBOOK_RUN_DATES.md).

## Probes

| # | Item | Steps | Go criterion |
|---|------|-------|--------------|
| R1 | tmux-mode shutdown probe | In a `--teammate-mode tmux` session: spawn a scratch teammate (haiku), confirm a real pane exists (`tmux list-panes`) and `backendType: "tmux"` in the team config; send `shutdown_request` → verify the pane is gone, record whether the roster entry remains; spawn a second scratch teammate, `TaskStop(name)` directly → verify pane termination, config-file survival, tasks-dir survival; `SendMessage` to the stopped name → record error vs transcript-resume. Also record whether pre-stop stragglers deliver late. | TaskStop terminates the pane AND config + tasks stay intact (mirrors in-process). No-go ⇒ SHUTDOWN step redesign before release. |
| R2 | Manual `/compact` render | In a session holding a live `session_refreshed` event, run manual `/compact`; inspect the first post-compact turn context. | The refresh prompt text (incl. `refresh_ts=`) appears in the first post-compact turn context. |
| R3 | Full fire-once cycle | refresh → `/compact` → `/PACT:bootstrap` → confirm resumption; then start a NEW session. | `session_refresh_consumed` present in the journal with the matching `refresh_ts`; the new session's startup does NOT re-surface the refresh prompt. |

## Verifier note — journal dir is cwd-slug-keyed

The session journal directory is keyed by the slug of the session's cwd. A
refresh performed from a WORKTREE cwd journals under the WORKTREE's slug
directory, not the main repo's. When verifying R2/R3, grep the slug dir
matching the refresh-time cwd — do not assume the repo slug.

## Recording

Append a dated entry per run to RUNBOOK_RUN_DATES.md under a
`## 1144-refresh-checkpoint-live-probes.md` section: probe id, session id,
outcome (go/no-go), and any observed deviation (e.g. straggler deliveries,
roster-entry behavior differences between shutdown_request and TaskStop).
