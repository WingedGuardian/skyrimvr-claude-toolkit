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

This applies to ordinary cleanup and both archive tiers. Older plans do not bypass the apply-time checks. A backup vault is optional and disabled by default; its permanent pruning always needs a separate exact-plan approval.

### Classes

| Class | What | Rule |
|---|---|---|
| **Forever** | Saves and every copy of them; backup stores and their manifests (anything under a folder named `*backup*`); knowledgebase snapshots; known-good baselines; audit logs; folders a person labelled by hand | Never removed by a tool. A person decides. |
| **Evidence** | Findings, READMEs, receipts (`.md`, `.json`, `.jsonl`), small logs; every file a project doc links to | Kept. They are small, and docs point at them. |
| **Recreatable** | A byte-identical copy of a file that is still live (a fixture copy of a game master) | Removed as soon as it is idle: the live file is the copy. |
| **Build output** | Compiler intermediates inside build folders | Review candidates only. Removal requires an explicit content-bound obsolete decision. Build products and active work remain protected. |
| **Finished work** | A task folder that is done: no write for 7 days, no read for 48 hours, not named in the instruction file's Live state block, not pinned with `.housekeep-keep`, no save, no git repository. A session can mark it done early with `.housekeep-closed`. | Zipped to the archive folder, the zip read back and checked, a short `ARCHIVED.md` (with a restore command) left behind, then the bulk recycled. The archive is local by default; another drive or a NAS is optional, never assumed. Archive age triggers review only; unique archives stay until explicitly declared obsolete. |
| **Duplicate** | The same bytes at several agent-output paths | Stored once: hard links to one read-only copy. Every path keeps working. |
| **Rolling** | Per-launch logs, snapshot folders, named scratch beds | Compression requires a verified lossless replacement. Snapshots require an identical retained generation. Unique logs and scratch need explicit content-bound obsolete decisions; age alone never deletes them. |
| **Toolkit temp** | Leftover workspaces explicitly declared disposable by their producer when newly created | Name, ownership and age alone are insufficient. Require a producer disposition receipt or explicit content-bound obsolete decision, then the ordinary protection, inactivity and in-use checks. Legacy unmarked workspaces stay. |

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
3. **Leave active work alone.** A task folder written to -- or read from -- in the last 48 hours belongs to a
   session that is probably still running. A read leaves no write time, so "nothing wrote here" alone never
   means done: a build linking a library only reads it. Where the drive does not record reads, only folders a
   session marked `.housekeep-closed` are archived.
4. **Never touch a git-tracked file**, and never hard-link to a live game file.
5. **Accounting must sum.** Every scanned byte lands in exactly one row; the row of bytes left alone prints even
   at zero.
6. **A changed rule acts only after a person has seen its output.** The daily run applies only when the cleanup
   code matches the version the user approved; after any change it plans, writes a review file, and the session
   says so.
7. **Never unattended, never a blindside.** Cleanup runs only from an open agent session, which announces it --
   what, which approved version, where the log is -- before it starts. No background service or scheduled task, no
   window or focus change, and nothing that makes the system ask "delete permanently?": an item the bin cannot
   take stays where it is and is reported.

### Where agent output goes

One folder per task. Findings and receipts at the top level; bulk (copied game files, archives, build trees, raw
log copies) in a `payload/` subfolder. Docs link to the top level, so the bulk can be trimmed without breaking a
link.

Cleanup is the second line of defence; writing less is the first:

- **Reference, don't copy.** Evidence records a commit, a hash and a diff, never a full copy of a tree, bundle,
  database or game file per run. Stage unchanged inputs as hard links, not copies.
- **One build folder per task.** Regenerated outputs overwrite, or keep the newest two.
- **Close the task.** Keep the README and receipts; retire bulk only after replacement or obsolescence proof, and write
  `.housekeep-closed`. A multi-GB folder that is genuinely needed later gets `.housekeep-keep` and one README line
  saying why; the daily note names every pinned folder over 1 GiB.
- **Budget.** Agent output has a size budget (`budget_gib`); over it, finished work is archived oldest first, and
  growth of more than 5 GiB in a day is named in the session-start note.

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
  each other; the 4.x content-addressed store does not.

## Running it

Optional preservation of older backup generations on secondary storage:
[cold backup archive](BACKUP-ARCHIVE.md). It is opt-in, verifies every archived
byte, keeps uncertain usefulness indefinitely, and requires a separate exact,
evidence-backed approval for permanent pruning.

```bash
cp .claude/housekeep.json.example .claude/housekeep.json   # then list your own folders in it
python tools/housekeep.py                                  # report: changes nothing
python tools/housekeep.py plan -o plan.json                # every action, with the hash it was planned on
python tools/housekeep.py apply plan.json [--only link,gzip]
```

`python tools/housekeep.py sweep-temp` runs the temp sweep by hand. The session-start hook runs
`housekeep.py session .claude/diag/housekeep-session.txt` detached and shows that file's line at the next start.

`apply` runs the classes in a fixed order (recreatable, build, link, gzip, launch, snapshot, scratch, archive,
expire) and prints every refusal with its reason. A refusal is not an error to work around: re-run `plan`.

### The daily run: only with a session open, always announced

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
compress several times over) and `archive_retention_days` (90; 0 disables review) sets the review threshold. Age never proves an archive obsolete. Point
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
