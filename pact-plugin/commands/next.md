---
description: Report the cross-session backlog reconciled against git, the tracker and pact-memory, then steer or record what the user decides
argument-hint: "[optional: e.g., add <title>, that one is done, do those two together]"
---

## What this command is for

The backlog holds **what the user intends to do next**, in order. It is not a
view of the tracker, not a filter over it, and nothing here syncs the two. The
tracker holds everything that is wrong or wanted; an item enters the backlog by
a deliberate act.

You are the **sole writer**. The user never hand-edits the file, so their only
view of it is what you report. Reconcile before you steer.

## Mode

- **Without arguments** (`/PACT:next`): report the reconciled backlog and
  recommend what to do next.
- **With arguments**: apply what the user asked for, then report.

## Step 1 — Update what you already know

**This is the main job, not a preamble.** You have been at the work. Statuses you
WITNESSED changing are facts, and you write them without asking:

```bash
python3 "{plugin_root}/hooks/shared/backlog.py" set <item-id> --status <status>
```

Run the command rather than editing the file: it loads through `load_or_create`,
which is what stashes the compare-and-swap baseline. A document built any other way
is written with NO lost-update protection and nothing reports that.

Two writes need no permission, and ONLY these two:

- **A `status` transition you WITNESSED** — you saw the PR merge, you started the
  work, you raised or cleared the blocker.
- **Anything you are TRANSCRIBING from the user.** Recording their words is not
  deciding for them. This covers `add`, and it covers `status` → `dropped`: the
  user says they are not doing something, and you write it down.

`dropped` is transcription and NEVER inference. You never WITNESS a decision not to
do something — only the user can supply it. "We're not doing the rate-limit work
after all" is a `dropped` you apply unasked. An item nobody has touched for a
fortnight is NOT dropped: that is the staleness flag, it is your inference, and it
ASKS.

Everything else asks. Report every write you made — silent correctness is
indistinguishable from silent breakage.

## Step 2 — Reconcile and report

Run:

```bash
python3 "{plugin_root}/hooks/shared/backlog.py" show
```

This resolves every drift class in one pass: refs against the tracker in a
single batched query (falling back to bounded per-ref REST calls where GraphQL
is refused), `plan` paths against the project root, `memory` ids
against the store, staleness, and dangling relational ids.

`show` OMITS `done` AND `dropped` ITEMS BY DEFAULT and says how many it hid, on
its own line after the last item and before the flags:

```
3 done and 1 dropped hidden (--all to show)
```

Each category is named separately rather than summed — four done items is a
working project and four dropped items is a project that keeps abandoning
things, and a reader needs to tell those apart. Singular per category, and a
category absent from the line entirely at zero. Pass `--all` to see them. The
line names its own remedy, so the flag is discoverable from the output without
this file. When the archive holds settled items, a second line — `N archived
(--archived to show)` — names the count and the flag that shows them, in the
default and `--all` views alike.

Read the output and report to the user in plain language:

1. What is `active` right now.
2. The next two or three `planned` items by rank.
3. Every flag, with what you think it means.

Exit code 3 means the file could not be USED, and the message says why.
BRANCH ON ONE PHRASE:

- `could not be read` — the bytes never arrived (a permission bit, a directory
  at the path). `repair` REFUSES this. Do NOT go to Step 5: report the access
  problem the message names and stop.
- ANY OTHER exit-3 message — the bytes were read and are not a usable backlog
  (`unparseable`, `top level is <type>, expected an object`, or wording not
  listed here). All of these are repairable, so go to Step 5.

Keying on the ONE refusing case rather than listing the repairable ones is
deliberate: a message nobody anticipated then defaults to Step 5, where
`repair` declines harmlessly if it turns out not to apply — rather than to a
stop, which would leave a fixable backlog unfixed.

Exit code 65 is a REFUSAL, not a corruption: the file is readable and nothing was
written. Report what the message names and fix that. Do NOT go to Step 5 — a
readable file must never be repaired.

Exit code 64 is a USAGE error: the command line was malformed, so the tool never
ran. Nothing was read and nothing was written, so the backlog says nothing about
this. Fix the invocation the message names and run it again — a global flag such
as `--backlog-dir` must come BEFORE the subcommand. Do NOT go to Step 5, and do
NOT report it as a refusal.

ANY OTHER exit code — 0, 3, 64 and 65 are the only ones the tool chooses, so
anything else came from something that is not the tool. The usual cause is that
the tool never started: a wrong path or a wrong working directory makes the
interpreter exit 2 before any of our code runs, and stderr then holds a Python
traceback rather than one of our messages. Check the invocation and the
directory. Do NOT go to Step 5, and do NOT report it as a refusal — nothing read
the backlog, so the backlog says nothing about this. Distrust any diagnosis of
the backlog's contents drawn from such a run, including your own.

A file that parses but breaks a schema rule reports as `schema:` flags and
still renders. Fix the field the flag names.

Each line ends with `[id=xxxx]`. Use it as the `<item-id>` argument to Step 3's
`set` command — this output is where you get it.

**Never cite an item's `id` to the user.** Ids exist so the relational fields
survive a retitling; they mean nothing to a person. Refer to items by title.

## Step 3 — Apply the facts, ask about the intent

**This step governs INFERENCES ONLY** — what the reconciliation found, not what you
witnessed in Step 1. A flag is something the tool worked out, so almost all of it
asks. Each row carries its own verdict; do not read one from the heading:

| Flag | Verdict | What you do |
|---|---|---|
| ref is closed as COMPLETED | ASK | propose marking it `done` |
| ref was closed WITHOUT the work being done | ASK | propose `dropped`, NEVER `done`. A tracker closing a ticket as not-planned IS a decision against the work, arriving from outside — which is what `dropped` records. `done` would record the opposite of what happened, and proposing nothing leaves the item ranked forever |
| ref is closed with NO stated reason | ASK | ask which happened; propose neither `done` nor `dropped` until they say |
| ref is unverifiable | ASK | check the ref by hand, or clear it |
| `blocked_by` names a SETTLED item (`done` or `dropped`) | ASK | propose unblocking it — the flag names the blocker's status, and neither will clear on its own |
| a relational field holds non-id entries | ASK | the entries are not ids at all; ask what was meant before removing them |
| a relational field names an unknown id | ASK | the id WAS the record, so nothing is recoverable, and a failed lookup cannot tell a mistype from a deletion |
| two exclusive items are both `active` | ASK | propose pausing one |
| active with no branch or worktree | ASK | confirm the work is still live |
| active and untouched past the cutoff | ASK | confirm it is still live |
| planned and unranked, untouched past the cutoff | ASK | rank it, or ask whether the work is still intended |
| `plan` does not resolve | APPLY at exactly ONE candidate | at zero or two-plus, ASK |
| a `memory` id no longer resolves | ASK | same reason as the relational id — the id was the record |
| a `memory` id is unverifiable | NEITHER | say the store could not be opened; change nothing |
| a `memory` record changed after linking | ASK | re-read it before relying on it |

An automatic fix is you overwriting the user's recorded intent on the strength
of an inference. Put the proposal to the user with `AskUserQuestion` and let
them decide.

## Step 4 — Write what the user decides

Add an item:

```bash
python3 "{plugin_root}/hooks/shared/backlog.py" add "<title>" \
  --rank <n> --ref "<opaque tracker reference>" \
  --note "<one line, your voice>"
```

Change one:

```bash
python3 "{plugin_root}/hooks/shared/backlog.py" set <item-id> \
  --status done
```

Archive settled items:

```bash
python3 "{plugin_root}/hooks/shared/backlog.py" archive <item-id> [<item-id>...]
```

The item is moved out of the live list, not removed: its id still resolves,
and `show --archived` lists it. Only `done` or `dropped` items archive — a
live id is refused and nothing is written.

Add and set both accept `--status`, `--rank`, `--ref`, `--plan`, `--note`, and
the repeatable `--memory`, `--blocked-by`, `--batch-with`, `--exclusive-with`.
`--ref none` clears a ref. The three list fields clear the same way:
`--blocked-by none`. Passing `none` alongside an id is refused.

Field rules the writer enforces, and a violation is REFUSED with nothing
written rather than quietly adjusted:

- `note` is capped at 500 characters. Anything longer goes in the tracker issue
  or in pact-memory, with the note pointing at it.
- `note` is written in YOUR voice, never in the user's first person. A relay in
  the user's first person gains an authority it never had and no later reader
  can strip it off. Where the distinction carries weight, mark it: `user
  ruled:` or `inferred:`.
- `plan` is repo-relative.
- `memory` holds at most 5 record ids.
- An item with no `ref` is fully supported and is never second-class.

### Where the report happens, and which files carry it

**THREE FILES carry a backlog report**: `bootstrap.md`, `wrap-up.md` and
`next.md`. Naming the files rather than the occasions is deliberate — a file is
checkable, an occasion is not.

A report belongs where the user is choosing WHAT TO DO NEXT: at session start,
before the session decision, and in this command. It does not belong where an
already-chosen item is being executed, or where one artifact is being judged —
those are occasions to write a witnessed transition, not to re-read the list.

Two of the three report without being asked, so they are the session's bookends;
this command's report happens because the user invoked it.

### When the writes happen, and which they are

**FOUR FILES carry boundary writes**: `orchestrate.md`, `comPACT.md`,
`imPACT.md` and `wrap-up.md`. Naming the files rather than the occasions is
deliberate — a file is checkable, an occasion is not.

**BOUNDARY WRITES COVER WITNESSED TRANSITIONS; RECONCILIATION COVERS UNWITNESSED
ONES.** That is what lets you classify a site this table omits. An out-of-band
merge — a PR merged in the web UI with no PACT command run — is unwitnessed by
definition, so it correctly gets no site, and it is picked up and asked about at
the next reconciliation instead.

| # | Write | Where | Asked first? |
|---|---|---|---|
| 1 | `status` → `active` | `orchestrate.md`, end of pre-flight | no — witnessed |
| 2 | `status` → `done` | `orchestrate.md`, between the completion steps | no — witnessed |
| 3 | `status` → `active` | `comPACT.md`, pre-invocation | no — witnessed |
| 4 | `status` → `done` | `comPACT.md`, after task-completion verification | no — witnessed |
| 5 | `status` → `blocked` | `imPACT.md`, when the blocker is raised | no — witnessed |
| 6 | `status` → `active` | `imPACT.md`, when the blocker is cleared | no — witnessed |
| 7 | `status` → `done` | `wrap-up.md`, inside worktree cleanup, BEFORE the worktree is removed | no — witnessed |
| 8 | `touched` | rides every row above | not yours to write — `set` and `add` stamp it themselves and there is no flag for it |
| 9 | `add` | wherever the user says it | no — but ONLY when transcribing their words |
| 10 | `status` → `dropped` | wherever the user says it | no — transcription only. You never WITNESS a decision not to do something |
| 11 | `rank` | — | ALWAYS asks; the ordering IS the intent |
| 12 | `ref` | — | ALWAYS asks |
| 13 | `plan` | — | ALWAYS asks at a boundary |
| 14 | `blocked_by`, `batch_with`, `exclusive_with` | — | ALWAYS ask |
| 15 | `title`, `note`, `memory` | — | ALWAYS ask |
| 16 | removing an item | — | NEVER — and not a gap. The record is kept and the state is expressed with `dropped` instead |
| 17 | `archive` a settled item | wherever the user says it | no — but ONLY when transcribing their words |

No site writes two status rows. Rows 11-17 are here because a permissions table
that lists only what is allowed reads as though the omissions are allowed too.

Not on every small action: the file's value is its stability.

## Step 5 — When the file is corrupt

The read path reports a corrupt backlog and changes nothing. Repair is a write,
and it is yours:

```bash
python3 "{plugin_root}/hooks/shared/backlog.py" repair
```

This RENAMES the corrupt file aside and reports where it went. It never
overwrites and never deletes, so a wrong rebuild loses nothing. Tell the user
the moved-aside path, then rebuild the items from what you can recover.

`repair` REFUSES a file it can read, and says so. That refusal means the file
is not corrupt — nothing was moved and the backlog is intact. Report to the
user what the flags say is wrong with it and stop there.

It also refuses a file it CANNOT read at all — a permission bit, a directory
at the path, an IO error. That refusal names the error and means corruption is
unknown, not established. Report the error to the user; do not move the file.

`--force` renames the file aside anyway — readable or unreadable — and reports
where it went. The file is KEPT, not deleted, so nothing is lost. Forcing an
unreadable file does NOT make it readable: whatever stopped the read applies to
the moved-aside copy too. Use it ONLY when the user has asked for that file to
be set aside. Never reach for it to get past a refusal.

## Store location

The file lives under the directory `get_backlog_dir()` resolves in
`hooks/shared/paths.py`, one JSON file per project. Ask that function rather
than writing the path out.

The session-start block that surfaces this backlog automatically is a pure file
read. It reports a corrupt or unresolvable store loudly and never writes.
