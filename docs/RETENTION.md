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
| **Forever** | Saves and every copy of them, save backups included; every other backup store and its manifests (anything under a folder named `*backup*`) except the rolling tool stores below; known-good baselines; audit logs; folders a person labelled by hand | Never removed by a tool. A person decides. |
| **Evidence** | Findings, READMEs, receipts (`.md`, `.json`, `.jsonl`), small logs; every file a project doc links to | Kept. They are small, and docs point at them. |
| **Recreatable** | A byte-identical copy of a file that is still live (a fixture copy of a game master) | Removed as soon as it is idle: the live file is the copy. |
| **Build output** | Compiler intermediates inside build folders (`.obj .pch .lib .ilk .iobj .ipdb .tlog .idb`) | Removed once idle. Binaries and debug symbols (`.dll .exe .pdb`) are kept. |
| **Duplicate** | The same bytes at several agent-output paths | Stored once: hard links to one read-only copy. Every path keeps working. |
| **Rolling** | Per-launch log captures, snapshot folders, scratch test beds | Compressed after 7 days, removed after 90; a snapshot that repeats the one before is removed; a scratch bed is removed by name when its task is done. |
| **Rolling tool store** | A tool's own rotating snapshots of the files it watches (script, config and knowledgebase snapshots), even under a `*backup*` folder. Each toolkit's checklist names its stores and their numbers | Kept by age or count, always with the newest copy; the rest goes to the Recycle Bin. |
| **Toolkit temp** | Directories the toolkit's own code and tests make in the system temp dir, by the toolkit's own name prefixes only (never a folder every project shares, such as pytest's `pytest-of-<user>`), and, where a toolkit keeps them, the batches a rolling tool store retires into `.claude/retired/` | Recycled by the session-start sweep once nothing in them changed for 24 hours (a retired batch: 10 minutes) and no process holds a file open inside. |

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

### Additional Skyrim classes

These rows extend the Skyrim policy; the shared policy above is unchanged.

| Class | What | Rule |
|---|---|---|
| **Derived** (`derived`) | `events.xml`, or an XML file directly inside an `xml-decoder` folder, in an idle agent task | Recycle only while a plain `.etl` source exists in the same folder or its parent inside the same task. ETL measurements are always kept, including inside whole-folder candidates. |
| **Superseded staging** (`superseded`) | A direct versioned child of a configured `staging_roots` folder | Recycle after `superseded_after_days` (default 30) and `active_hours`, only with a higher numeric version of the same name beside it, or its exact folder name in `_superseded`. Keep rollback sources and every folder whose protection cannot be decided. |

Activity includes the task directory and its child directories, including recent empty directories.
An unreadable activity walk or metadata read keeps the task active. Direct `recycle` inspection also
refuses the whole item on any walk or metadata error, before calling the Recycle Bin.

Staging versions use two to four numeric components after the final name separator (`My Mod-1.2.3`);
comparison is numeric, with missing trailing components treated as zero. A staging root's optional
`_superseded` is a UTF-8 text file containing one exact versioned folder name per line; blanks and `#`
comment lines are allowed. Names built from paths are refused. Staging roots may not overlap each other.
Activity includes directory timestamps, including new empty directories. A Git checkout anywhere inside
a staging candidate keeps the whole folder, even when the staging root itself is outside Git.

Rollback manifests are read beneath `<toolkit>/manual-installs/`,
`<toolkit>/.claude/backups/manual-installs/`, and `SKYRIM_MANUAL_INSTALLS` when configured. Supported formats
are mod-install's `*.files.txt` manifests (including operation manifests), and JSON objects containing
exactly `{"rollback_sources": ["absolute/path", "..."]}`. Any unknown format, relative path, read failure
or link keeps the staging folder. Source directories and files named by these manifests are kept.
The new classes never follow symlinks, junctions or reparse points.

A trace at the task root cannot borrow an ETL from the shared output root. If provenance
cannot be read, planning keeps the XML and counts its known bytes. An unreadable staging
size also keeps that candidate; the plan names it in `unreadable_paths` and labels its
byte count unknown, without aborting the remaining candidates.

Planning records the XML hash or staging-folder fingerprint; apply checks the current source,
activity, protection, supersession and manifests again. Both classes use the existing Recycle Bin
path. `--only derived,superseded` restricts the scans; no staging location is enabled by default.

### Existing Skyrim retention rules

- **Saves are Forever, everywhere.** `.ess` and `.skse` copies inside experiment folders included -- and
  de-duplicating them is not worth it where a save guard backs a save up before any change, because the backup
  re-copies the very bytes being freed.
- **Fixture copies of masters** (`Skyrim.esm`, `Update.esm`, DLC) are the largest recreatable class; xEdit
  shadow folders and readback views make them.
- **CommonLibSSE builds** leave ~300 MB `.lib` files and precompiled headers per build folder.
- **Launch-log captures** (a watcher copying `SkyrimNet.log`, Papyrus and SKSE logs per launch) compress about
  10:1; a folder whose name is not `launch-YYYYMMDD-HHMMSS` counts as labelled and is kept.
- **Snapshot folders** from the 3.x `snapshot-before-tool.sh` hook (`.claude/backups/auto_snapshots/YYYYMMDD_HHMMSS/`)
  repeat each other; the 4.x content-addressed store beside them does not. Both are a **Rolling tool store** although
  they sit under `.claude/backups/` (user rulings 2026-10-20 and 2026-10-24). Two things remove a 3.x stamp, both by
  way of the Recycle Bin: `housekeep` recycles a stamp that repeats the one before (a save or a `*backup*` folder
  inside a stamp keeps that stamp), and the guard's own pruning retires every stamp whose folder is older than 14
  days, as 3.x deleted them; that age-out does not look inside the stamp (a 3.x stamp holds only `.psc`/`.pex`
  copies). Every other backup folder stays Forever.
- **The temp sweep** recycles only directories with this toolkit's own name prefixes (its test runs live under
  `skyrim-harness-`), never pytest's `pytest-of-<user>`, which every project's runs share. Session start launches it only when the hook's project is the toolkit
  install the hook belongs to, never under the test harness. `SKYGUARD_HOUSEKEEP_SWEEP=off` turns it off, and
  that process makes no real Recycle Bin call.
- **The guard's own stores are Rolling tool stores, pruned by retiring, not deleting** (user ruling 2026-10-24).
  Script snapshots (`.claude/backups/auto_snapshots`) and config recovery snapshots
  (`.claude/backups/recovery_snapshots`) keep everything younger than 14 days, the newest snapshot, and the newest
  snapshot holding each path's latest content (a kept recovery snapshot also keeps the earlier lines its state
  needs; the script store also keeps every copy its index of the live files names). Session logs keep the 20 newest
  per agent. What goes is renamed into `<project>/.claude/retired/<batch>/` (a hook has no time
  for the Recycle Bin). The session sweep sends each batch to the bin once nothing in it changed for 10 minutes
  and it is not in use. Off Windows there is no bin to call: the batches wait there, and the session note says
  how many and how large. Save snapshots in the recovery store and the save backups
  (`.modding-agent-harness/backups/saves/`) are never pruned.
- **Knowledgebase snapshots** (`.claude/backups/kb/`) are a Rolling tool store (user ruling 2026-10-24):
  `tools/kb-guard.sh` keeps `KB_GUARD_KEEP` (default 30) snapshot folders, the newest ones, never removing the
  oldest, the newest or the one holding the largest copy of a watched file, and sends the others to the Recycle
  Bin through `housekeep.py recycle`, printing a notice first; what the bin does not take stays and is reported.
- **Forever names, in code:** besides saves and `*backup*` folders, `housekeep` never plans a file under a folder (or
  a file) whose name starts with `known-good` (the dated known-good baselines) or a file named `AUDIT_LOG.txt`, and a
  whole-folder action keeps any folder holding one.
- **A protection that cannot be evaluated stops the run.** When git cannot list the tracked files of a root inside
  a work tree (no runnable git, dubious ownership, a broken index), `report`, `plan` and `apply` refuse with the
  reason instead of treating nothing as tracked. A folder outside any repository has no tracked files.
- **Hard links a mod manager made are not ours.** Vortex and MO2 can deploy by hard link, so an agent-output copy
  may share its file with a live `Data/` file, and the read-only mark a duplicate set gets would land on that file
  too. A duplicate that already has a second name is left alone; `apply` re-counts the names before it links.
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
exactly those paths to the Recycle Bin and prints OK or FAILED for each. It takes only what this policy may remove: a
path strictly inside a rolling tool store (`.claude/retired`, `.claude/backups/kb`, `.claude/backups/auto_snapshots`
of the toolkit folder or `$CLAUDE_PROJECT_DIR`), a configured `agent_output`, `launch_logs` or `legacy_snapshots`
folder, a named `scratch` folder (or that folder itself), or the system temp folder. Whatever that rule says, it
refuses (`FAILED ... refused: <why>`, nothing sent to the bin) a drive root, the home folder, the toolkit or install
folder or one above it, a link, a `Saves` folder or anything in one, a save or a folder holding one, and a Forever
name (a `*backup*` folder below the allowed folder, `known-good*`, `AUDIT_LOG.txt`). The session-start hook runs
`housekeep.py session .claude/diag/housekeep-session.txt` detached and shows that file's line at the next start.

`apply` runs the classes in a fixed order (derived, recreatable, build, link, gzip, launch, snapshot, scratch,
superseded) and prints
every refusal with its reason. A refusal is not an error to work around: re-run `plan`.
