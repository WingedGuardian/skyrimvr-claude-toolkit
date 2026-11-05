# Retention: what agent work keeps, and for how long

Backups and evidence are the point of this toolkit, and left alone they eat the disk. Measured on one Skyrim VR
install after six weeks: 44 GiB of agent output, of which about 20 GiB were exact copies of bytes already on disk --
fixture copies of `Skyrim.esm`, build intermediates, one log copied into twenty evidence folders, a snapshot hook
re-copying unchanged scripts thousands of times. Very little of it was *old*; it was *repeated*. This page is the
policy; `tools/housekeep.py` implements the parts a tool can.

<!-- shared-policy:begin -->
## Shared policy (identical in the Skyrim and X4 toolkits -- change both)

### Certainty before removal

Age, size, an idle timer, a filename and a completed-task marker trigger review; none proves that unique bytes are obsolete. Housekeep retires a payload only when it re-proves an identical retained copy or a verified lossless replacement, or the operator has explicitly declared those exact bytes obsolete with a reason and evidence. Unknown usefulness is kept indefinitely. Failed or stale proof leaves the source in place.

This applies to ordinary cleanup and both archive tiers. Older plans do not bypass the apply-time checks. A backup vault is optional and disabled by default; archive retirement needs a separate exact-plan approval and a working recycle route.

### Destination follows creation-time disposition and current proof

Permanent deletion is eligible only when the producer declared the item disposable
at creation and rebuildability is checked now. A receipt alone is not current
proof; a current rebuild test alone is not a creation-time disposition. Never
backfill receipts onto legacy material. Incomplete proof demotes an otherwise
authorized retirement to the Recycle Bin; unknown value remains kept.

Uncommitted work, unique logs/evidence, long-run outputs and handwritten material
can be the only copy and belong in the Recycle Bin when retirement is justified.
Worktree proof includes a clean tree plus a branch committed and pushed; this is
not permission to push just to make cleanup eligible. Same-run scratch may still
be deleted by its creator at exit. An item Windows cannot recycle needs the
user's exact-item decision, never a silent permanent fallback.

Housekeep currently uses recycling for cross-run retirements. It does not infer
permanent eligibility from a folder name, ownership marker or file extension.
NAS backup payloads also use the recycler after exact-plan approval; without a
working recycle route they stay in place. No automatic Bin purge is provided.

The Windows session-start report warns at `recycle_bin_warn_gib` (default 10 GiB;
0 disables the warning). It reads aggregate contents only and reports unavailable
queries separately. The size is logical contents, not guaranteed reclaimable
physical allocation. Report permanent deletions, recycling and failures separately.

### Classes

| Class | What | Rule |
|---|---|---|
| **Forever** | Saves and every copy of them; backup stores and their manifests (except explicitly configured rolling tool stores below); knowledgebase snapshots; known-good baselines; audit logs; folders a person labelled by hand | Never removed by a tool. A person decides. |
| **Evidence** | Findings, READMEs, receipts (`.md`, `.json`, `.jsonl`), small logs; every file a project doc links to | Kept. They are small, and docs point at them. |
| **Recreatable** | A byte-identical copy of a file that is still live (a fixture copy of a game master) | Removed as soon as it is idle: the live file is the copy. |
| **Build output** | Compiler intermediates inside build folders | Review candidates only. Removal requires an explicit content-bound obsolete decision. Build products and active work remain protected. |
| **Finished work** | A task folder that is done: no write for 14 days (`archive_after_days`; 7, `min_idle_days`, once agent output is over `budget_gib`), no read for 48 hours, not named in the instruction file's Live state block, not pinned with `.housekeep-keep`, no save or git repository/worktree, no ownership receipt without qualified release proof. A session can mark it done early with `.housekeep-closed`; independent holds still apply. | Zipped to the archive folder, the zip read back and checked, a short `ARCHIVED.md` (with a restore command) left behind, then the bulk recycled. The archive is local by default; another drive or a NAS is optional, never assumed. Archive age triggers review only; unique archives stay until explicitly declared obsolete. |
| **Duplicate** | The same bytes at several agent-output paths | Stored once: hard links to one read-only copy. Every path keeps working. |
| **Rolling** | Per-launch logs, snapshot folders, named scratch beds | Compression requires a verified lossless replacement. Snapshots require an identical retained generation. Unique logs and scratch need explicit content-bound obsolete decisions; age alone never deletes them. |
| **Rolling tool store** | Configured rolling snapshots, including stores under backup folders | Retire only a byte-identical repeated generation after re-proving the retained copy. Newest and distinct generations stay. |
| **Toolkit temp** | Leftover workspaces explicitly declared disposable by their producer when newly created | Name, ownership and age alone are insufficient. Require a producer disposition receipt or explicit content-bound obsolete decision, then the ordinary protection, inactivity and in-use checks. Legacy unmarked workspaces stay. |

### The toolkit cleans up after itself

Measured on one machine on 2026-10-18: one day of local test runs left about 25 GiB of copies in pytest's
temp dir, and 14,018 small directories from earlier runs had built up since August. None of it was ever read
again. So:

1. **Code and tests remove what they create, in the same run.** A temp dir is made with a cleanup registered at
   the moment it is created, never "deleted at the end" where a crash skips it. Same-run scratch may bypass
   the Recycle Bin; cross-run permanent deletion needs the separate receipt and current-proof gates. A test enforces cleanup,
   and every prefix the code creates must be one the sweep knows.
2. **Session start sweeps the leftovers** of earlier runs (a crash, a killed job) to the Recycle Bin, in the
   background, at most every 6 hours.
3. **Session start says what it cannot fix**: free space near the floor, an agent's session store past its
   threshold, a pile of git worktrees. One line, only when there is something to say.
4. **Full test suites run in CI, not on the user's machine.** Locally: the tests for the files that changed.

### Rules every removal follows

1. **Report, plan, apply -- never one step.** The plan records each file's hash; apply re-checks it and refuses
   anything that changed.
2. **Recycle Bin (or Trash) by default.** Permanent eligibility requires both gates above, except same-run scratch.
   An item too big for the Bin, a network item without a recycling route, or unknown/disabled Bin capacity stays
   in place and is reported in-session. Any exact-item permanent-deletion decision belongs to the user; cleanup
   never silently falls back to it. Recycling frees no disk space until the Bin is deliberately emptied.
3. **Leave active work alone.** Task archival checks whole-task write/read activity, pins, Live state,
   resource-job ownership, ownership receipts and repository/save protections. Recent reads independently veto
   archival. If read recording cannot be proven with same-run owned scratch on the same volume, only an explicitly
   closed folder can qualify. A closure bypasses write-age only; it does not bypass the independent hold gates.
4. **Never touch a git-tracked file**, and never hard-link to a live game file.
5. **Accounting must sum.** Every scanned byte lands in exactly one row; the row of bytes left alone prints even
   at zero.
6. **A changed rule acts only after a person has seen its output.** Archive/retirement actions require the
   cleanup version the user approved. An updated version first produces a review; it does not approve itself.
   The optional session daily runner applies only its approved version. X4 task archiving is opt-in and manual.
7. **Never unattended, never a blindside.** Cleanup runs only from an open agent session, which announces it --
   what, which approved version, where the log is -- before it starts. No background service or scheduled task, no
   window or focus change, and no unattended permanent-deletion prompt: an item the bin cannot
   take stays where it is and is reported.

### Where agent output goes

One folder per task. Findings and receipts at the top level; bulk (copied game files, archives, build trees, raw
log copies) in a `payload/` subfolder. Docs link to the top level, so the bulk can be trimmed without breaking a
link.

Cleanup is the second line of defence; writing less is the first:

- **Reference, don't copy.** Evidence records a commit, a hash and a diff, never a full copy of a tree, bundle,
  database or game file per run. Stage unchanged inputs by reference where the tool allows, otherwise
  copy them; never hard-link them (a hard link is the same file, so a write to the staged copy changes
  the source).
- **One build folder per task.** Regenerated outputs overwrite, or keep the newest two.
- **Close the task.** Keep the README and receipts; retire bulk only after replacement or obsolescence proof, and write
  `.housekeep-closed`. A multi-GB folder that is genuinely needed later gets `.housekeep-keep` and one README line
  saying why; the daily note names every pinned folder over 1 GiB.
- **Budget.** Agent output has a size budget (`budget_gib`); over it, finished work is archived oldest first, and
  the optional daily runner reports measured logical growth separately from physical drive usage.

### Ordinary archive review

Ordinary task archives and the optional cold-backup vault are separate inventories. Both are reviewed; one is
not coverage for the other. `archive_review_days` defaults to 30, with `archive_review_seconds` 15 per ordinary
archive review, from an open session only. A failed/partial review retries sooner and offers no retirement.
`archive_retention_days` is accepted for compatibility; age is never authority to remove an archive, and setting
it to zero does not disable certainty-based review. Unknown usefulness stays indefinitely.

Only an explicit `obsolete_items` entry bound to the current archive bytes, with
`basis: operator-confirmed-obsolete`, a reason and evidence can produce an ordinary archive retirement candidate.
Its `content_hash` is the current BLAKE2b digest with 16-byte output. Apply checks those bytes again, plus the
configured namespace and independent protections. Source group names such as `backups` do not hide tracked,
linked, save, baseline or custom protections. NAS retirement still needs a working recycler; failure leaves it.

The source check `check-housekeep-parity.py PEER_SOURCE_ROOT` compares both actual shared policies, their pins,
and both shared archive helpers. The local pin tests alone do not prove that the sibling repository agrees.

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

### The shared classes in this toolkit

How three shared classes read here. These rows add this toolkit's own stores and rulings to the shared policy above;
they loosen none of its gates for `housekeep`.

| Class | What | Rule |
|---|---|---|
| **Forever** | Saves and every copy of them, save backups included; every other backup store and its manifests (anything under a folder named `*backup*`) except the rolling tool stores below; known-good baselines; audit logs; folders a person labelled by hand | Never removed by a tool. A person decides. |
| **Rolling tool store** | A tool's own rotating snapshots of the files it watches (script, config and knowledgebase snapshots), even under a `*backup*` folder. The checklist below names the stores and their numbers | The guard's own pruning keeps them by age or count, always with the newest copy, and retires the rest; `housekeep` itself retires only a byte-identical repeated generation, as the shared row says. |
| **Toolkit temp** | Directories this toolkit's own code and tests make in the system temp dir, by its own name prefixes only (never a folder every project shares, such as pytest's `pytest-of-<user>`), and the batches a rolling tool store retires into `.claude/retired/` | A temp dir needs what the shared row requires (a producer disposition receipt or an explicit obsolete decision), then 24 hours with no change and no process holding a file open inside. A retired batch goes to the Recycle Bin once nothing in it changed for 10 minutes and it is not in use. |

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

Task archive workflow: configure `agent_output`/`archive_roots` and `archive_root`, enable `archive_enabled`,
then run `python tools/housekeep.py version` and `python tools/housekeep.py plan --only archive,expire -o archive-plan.json`.
Review that version and the full proposal before recording its digest in `approved_sha256`; then an announced
in-session `apply archive-plan.json --only archive,expire` rechecks it. X4 does not start an automatic task archive
run. An unreachable NAS, stale proof, active owner or unqualified Bin route leaves local material in place.
Archive inventories may include another drive/NAS; logical totals are not C: allocation or immediate recovery.


Optional preservation of older backup generations on secondary storage:
[cold backup archive](BACKUP-ARCHIVE.md). It is opt-in, verifies every archived
byte, keeps uncertain usefulness indefinitely, and requires a separate exact,
evidence-backed approval for recycling obsolete archives.

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
superseded, archive, expire) and prints every refusal with its reason. A refusal is not an error to work around: re-run `plan`.

### The daily run: only with a session open, always announced

The daily action/refusal log is append-only. Previous review notes are retained
after approval; age or a line-count limit does not establish their obsolescence.

Cleanup never runs in the background on its own: there is no scheduled task and no service. When you start a
Claude Code session, the session-start hook checks whether the daily run is due (at most once every
`daily_every_hours`, 20; not while a process in `pause_while_running` runs, such as the game or SteamVR). If it is,
the hook prints one line into the session **before anything starts** -- what will run, which approved version, where
the log is -- and then starts `housekeep.py session` detached: low priority, no windows, time-boxed to
`daily_minutes` (10); whatever is left waits for the next run. The run's summary is the next session's start note.

It **applies only when the housekeep code matches `approved_sha256`** in your config. The first run, and the first
run after any toolkit update that changes housekeep, only plans: it writes `.claude/diag/housekeep/REVIEW-NEEDED.md`
(what it would archive or remove, by folder and file type, and the code fingerprint), and the session says so. Read
it; if it is right, put that fingerprint in `approved_sha256`. You approve each version once and are told about every
run. `"daily_enabled": false` turns the daily run off; `python tools/housekeep.py announce` shows what the hook would
print right now.

Nothing it removes ever makes Windows ask "delete permanently?": an item the Recycle Bin cannot take (a network
location, a path longer than 259 characters, something bigger than the bin's limit) is left where it is and listed
in the summary for you to decide.

### The archive

Finished folders are zipped to `archive_root` (default `.claude/archive`, on the same drive: evidence and logs
compress several times over). Anything protected in a finished folder (a Forever name, linked evidence, a nested
`.housekeep-keep`) stays where it is; the zip holds a verified copy and the rest of the folder is retired. Age never proves an archive obsolete. Point
`archive_root` at another drive or a NAS share if you have one, and add `archive_roots` entries (`{"folder":
days}`) for folders of your own whose subfolders should be archived after that many idle days. Restore a folder
with `python tools/housekeep_archive.py restore "<folder>/ARCHIVED.md"`; it refuses to overwrite anything.

### Explicit obsolete decisions

Use top-level `obsolete_items` for unique ordinary cleanup candidates. Each key is an exact absolute path:

```json
"obsolete_items": {
  "/absolute/retired-item": {
    "basis": "operator-confirmed-obsolete",
    "reason": "Explain why this specific state is no longer needed",
    "evidence": "Reviewed receipt, replacement or operator decision",
    "content_hash": "<current housekeep content hash>"
  }
}
```

Files use BLAKE2b-128; directory generations use the SHA256 manifest digest returned by `housekeep_backups.content_hash(path)`. Record the decision only after review, never generate declarations merely to clear a report. Changed content invalidates it. Saves, tracked/linked evidence, pins and active work remain protected.
