# Retention: what agent work keeps, and for how long

Backups and evidence are the point of this toolkit, and left alone they eat the disk. Measured on one Skyrim VR
install after six weeks: 44 GiB of agent output, of which about 20 GiB were exact copies of bytes already on disk --
fixture copies of `Skyrim.esm`, build intermediates, one log copied into twenty evidence folders, a snapshot hook
re-copying unchanged scripts thousands of times. Very little of it was *old*; it was *repeated*. This page is the
policy; `tools/housekeep.py` implements the parts a tool can.

<!-- shared-policy:begin -->
## Shared policy (identical in the Skyrim and X4 toolkits -- change both)

### Classes

| Class | What | Rule |
|---|---|---|
| **Forever** | Saves and every copy of them; backup stores and their manifests (anything under a folder named `*backup*`); knowledgebase snapshots; known-good baselines; audit logs; folders a person labelled by hand | Never removed by a tool. A person decides. |
| **Evidence** | Findings, READMEs, receipts (`.md`, `.json`, `.jsonl`), small logs; every file a project doc links to | Kept. They are small, and docs point at them. |
| **Recreatable** | A byte-identical copy of a file that is still live (a fixture copy of a game master) | Removed as soon as it is idle: the live file is the copy. |
| **Build output** | Compiler intermediates inside build folders (`.obj .pch .lib .ilk .iobj .ipdb .tlog .idb`) | Removed once idle. Binaries and debug symbols (`.dll .exe .pdb`) are kept. |
| **Duplicate** | The same bytes at several agent-output paths | Stored once: hard links to one read-only copy. Every path keeps working. |
| **Rolling** | Per-launch log captures, snapshot folders, scratch test beds | Compressed after 7 days, removed after 90; a snapshot that repeats the one before is removed; a scratch bed is removed by name when its task is done. |
| **Toolkit temp** | Directories the toolkit's own code and tests make in the system temp dir, and pytest's old runs | Recycled by the session-start sweep once nothing in them changed for 24 hours and no process holds a file open inside. |

### The toolkit cleans up after itself

Measured on one machine on 2026-10-18: one day of local test runs left about 25 GiB of copies in pytest's
temp dir, and 14,018 small directories from earlier runs had built up since August. None of it was ever read
again. So:

1. **Code and tests remove what they create, in the same run.** A temp dir is made with a cleanup registered at
   the moment it is created, never "deleted at the end" where a crash skips it. This is the only deletion that
   bypasses the Recycle Bin: a process removing scratch it made itself during that run. A test enforces it,
   and every prefix the code creates must be one the sweep knows.
2. **Session start sweeps the leftovers** of earlier runs (a crash, a killed job) to the Recycle Bin, in the
   background, at most every 6 hours.
3. **Session start says what it cannot fix**: free space near the floor, an agent's session store past its
   threshold, a pile of git worktrees. One line, only when there is something to say.
4. **Full test suites run in CI, not on the user's machine.** Locally: the tests for the files that changed.

### Rules every removal follows

1. **Report, plan, apply -- never one step.** The plan records each file's hash; apply re-checks it and refuses
   anything that changed.
2. **Recycle Bin (or Trash), never a permanent delete** -- except same-run scratch (above). An item too big for
   the bin gets the system's own "delete permanently?" prompt, and stays where it is unless the user says yes.
   The bin frees nothing until the user empties it; that is their decision.
3. **Leave active work alone.** A task folder written to in the last 48 hours belongs to a session that is
   probably still running.
4. **Never touch a git-tracked file**, and never hard-link to a live game file.
5. **Accounting must sum.** Every scanned byte lands in exactly one row; the row of bytes left alone prints even
   at zero.

### Where agent output goes

One folder per task. Findings and receipts at the top level; bulk (copied game files, archives, build trees, raw
log copies) in a `payload/` subfolder. Docs link to the top level, so the bulk can be trimmed without breaking a
link.

### Agent transcripts

- **Claude Code** deletes its own transcripts, file history, plans and session scratchpads after
  `cleanupPeriodDays` (default 30). Set it in **user** settings (`~/.claude/settings.json`) only.
  **Never ship it in a project's `.claude/settings.json`:** the sweep covers every project on the machine and
  uses the merged settings of whichever session starts it, so a toolkit-shipped value would delete the user's
  other projects' transcripts at the toolkit's number (code.claude.com/docs: settings-reference
  `cleanupPeriodDays`; monitoring-usage `retention_sweep`). Organisations that need one value everywhere set it
  in managed settings.
- **Codex** keeps sessions under `~/.codex/sessions/`; this toolkit knows of no automatic pruning there.
  `housekeep report` shows its size; removing old sessions is the user's call.
<!-- shared-policy:end -->

## Skyrim checklist

- **Saves are Forever, everywhere.** `.ess` and `.skse` copies inside experiment folders included -- and
  de-duplicating them is not worth it where a save guard backs a save up before any change, because the backup
  re-copies the very bytes being freed.
- **Fixture copies of masters** (`Skyrim.esm`, `Update.esm`, DLC) are the largest recreatable class; xEdit
  shadow folders and readback views make them.
- **CommonLibSSE builds** leave ~300 MB `.lib` files and precompiled headers per build folder.
- **Launch-log captures** (a watcher copying `SkyrimNet.log`, Papyrus and SKSE logs per launch) compress about
  10:1; a folder whose name is not `launch-YYYYMMDD-HHMMSS` counts as labelled and is kept.
- **Snapshot folders** from the 3.x `snapshot-before-tool.sh` hook (`auto_snapshots/YYYYMMDD_HHMMSS/`) repeat
  each other; the 4.x content-addressed store does not. That one store is **Rolling** although it sits under
  `.claude/backups/` (user ruling 2026-10-20): a stamp repeating the one before goes to the Recycle Bin. A save, or
  a `*backup*` folder, inside a stamp keeps that stamp, and every other backup folder stays Forever.
- **The temp sweep here is narrower than the Toolkit temp row above:** it recycles only directories with this
  toolkit's own name prefixes (its test runs live under `skyrim-harness-`), never pytest's `pytest-of-<user>`,
  which every project's runs share. Session start launches it only when the hook's project is the toolkit
  install the hook belongs to, never under the test harness. `SKYGUARD_HOUSEKEEP_SWEEP=off` turns it off, and
  that process makes no real Recycle Bin call.
- **The guard's own stores are pruned by retiring, not deleting.** Script snapshots, recovery blobs and session
  logs past the 20 newest per agent are renamed into `<project>/.claude/retired/<batch>/` (a hook has no time
  for the Recycle Bin). The session sweep sends each batch to the bin once nothing in it changed for 10 minutes
  and it is not in use. Off Windows there is no bin to call: the batches wait there, and the session note says
  how many and how large. Save backups are never pruned.
- **Knowledgebase snapshots** (`.claude/backups/kb/`): `tools/kb-guard.sh` keeps at most `KB_GUARD_KEEP`
  (default 30) snapshot folders, never removing the oldest, the newest or the one holding the largest copy of a
  watched file, and sends the others to the Recycle Bin through `housekeep.py recycle`, saying so first; what
  the bin does not take stays and is reported.
- **Whole-folder classes** (launch, snapshot, scratch) keep a folder whole when any file in it is protected, and
  `apply` checks that again. Off Windows `housekeep` removes nothing: every item is reported as left in place.

## Running it

```bash
cp .claude/housekeep.json.example .claude/housekeep.json   # then list your own folders in it
python tools/housekeep.py                                  # report: changes nothing
python tools/housekeep.py plan -o plan.json                # every action, with the hash it was planned on
python tools/housekeep.py apply plan.json [--only link,gzip]
```

`python tools/housekeep.py sweep-temp` runs the temp sweep by hand; `python tools/housekeep.py recycle PATH...` sends
exactly those paths to the Recycle Bin and prints OK or FAILED for each. The session-start hook runs
`housekeep.py session .claude/diag/housekeep-session.txt` detached and shows that file's line at the next start.

`apply` runs the classes in a fixed order (recreatable, build, link, gzip, launch, snapshot, scratch) and prints
every refusal with its reason. A refusal is not an error to work around: re-run `plan`.
