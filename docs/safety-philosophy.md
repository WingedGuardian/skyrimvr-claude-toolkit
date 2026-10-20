# Safety Philosophy

## Why Safety Matters in Skyrim Modding

A modded Skyrim installation with hundreds of plugins is a complex, interdependent system. Small changes can have cascading effects:

- A bad ESP edit can corrupt save files permanently
- An incorrect INI setting can cause crashes with no obvious error
- Overwriting a critical file with no backup means hours of reinstallation
- VR-specific quirks mean "works in SSE" doesn't mean "works in VR"

This toolkit was built after experiencing all of these. Every safety feature exists because something went wrong without it.

## Defense in Depth

The toolkit uses several defenses, one behind the other. (They are numbered as defenses, not layers: the
"Layers 0-2" of `ADAPTING.md` are a different list, about what an agent the toolkit does not wire itself gets.)

### Defense 1: Knowledge (KNOWLEDGEBASE.md)
Before making any change, Claude is instructed to check the knowledgebase for known pitfalls. Many Skyrim modding errors are well-documented but easy to forget.

### Defense 2: Confidence Levels
Claude must explicitly rate its confidence (0-100%) before proposing any change and list assumptions. This forces investigation before action.

#### Confidence scale
| Range | Meaning | Action |
|-------|---------|--------|
| 95-100% | Verified via testing, docs, or authoritative source | Proceed with user confirmation |
| 80-94% | Strong evidence but not fully verified | Proceed with caveats noted |
| 60-79% | Reasonable assumption, some unknowns | Research more before proceeding |
| < 60% | Speculative | Do NOT proceed -- investigate first |

### Defense 3: Hook Guards
Six thin wrappers over the Python engine `tools/skyguard/` intercept Claude's tool calls:

- **protect-bash.sh** -- Blocks destructive commands; **advises** on file operations in the game directory (a note to Claude, not a prompt to you)
- **protect-files.sh** -- Blocks writes to plugin/archive binaries through the Edit/Write tools; **advises** on all other edits in the install
- **protect-mcp.sh** -- Guards the DevBench MCP tools (and the curl / `devbench-cli.sh` route to the same tools): denies the one console command measured to crash a large load order (`smp reset`). Recognized changes to one or two saves require verified permanent save-pair backups before proceeding; bulk access needs the bound human approval described below.
- **backup-before-edit.sh** -- Copies a file before Claude modifies it **through the Edit/Write/NotebookEdit tools**. ⚠ Anything written by a script run through Bash is invisible to it -- that is a property of hooks, not a bug here. MEASURED: zero backups of a 177,459-byte `KNOWLEDGEBASE.md` across seven months, because it is written by a script. `session-kb-guard.sh` below covers that channel.
- **snapshot-before-tool.sh** -- Snapshots Papyrus source/compiled scripts before shell commands
- **session-kb-guard.sh** -- At SessionStart, copies the files nothing can rebuild
  (`KNOWLEDGEBASE.md`, `KNOWLEDGEBASE.local.md`, `CLAUDE.md`, `AGENTS.md`) and raises an alarm if
  one shrank. It exists because the first five all key on a *tool call*, and the
  knowledgebase is usually written by a script run through Bash -- invisible to an
  Edit/Write hook. Measured on the author's install: zero knowledgebase backups in
  seven months, with the backup hook working correctly throughout.

### Defense 4: Dry-Run Convention
ESP modifications via xelib always use a two-pass workflow: read-only preview, then write only after human approval.

### Defense 5: Audit Trail
Every change made through the edit tools (and Codex `apply_patch`) is logged with timestamp, tool name, and backup location, and so is every refusal and note. A file a script writes through the shell is not logged as a change. If something goes wrong, you can trace what the edit tools changed and when.

## Design Principles

### 1. No Silent Modifications
Destructive changes are blocked outright. Everything else that is consequential but legitimate is **advised**: a note is injected into Claude's context and the call proceeds -- you are not prompted. Five rules in the toolkit ask you to decide: a git command that would throw away uncommitted changes in a protected folder, a `lock disable` that turns the vanilla-file lock off for good, an in-place restore (`snapshot restore --in-place` or `restore-from-backup.sh --restore`), which writes over live files, and a program the guard cannot name (a variable such as `$R`) given one of your saves -- it may write, move or delete the save, or nothing, and the guard cannot count that change to back it up first -- and a command the guard cannot read that names the install, since it cannot tell what that command does there.

Saves follow their own rule (user ruling 2026-10-13), because a save is the one thing a mod manager cannot give back. A command that changes one or two saves -- writes, copies onto, edits, moves, renames or deletes them, a DevBench save over an existing one, ReSaver `--apply` -- runs WITHOUT a prompt, but only after the deciding hook has copied both files of each pair (the `.ess` and its `.skse` co-save count as one save), read each copy back to its SHA-256 and recorded it; if that backup fails the command is refused. More than two saves, or a wildcard, is refused until you type the `SAVE_ACCESS_APPROVAL` line the refusal shows -- read only from what you typed, never from anything the agent read. Those backups are never pruned. Reading, hashing, listing and copying saves out are free; deleting or moving the whole `Saves` folder stays refused.

New save transactions live in `.modding-agent-harness/backups/saves/`, independent of the coding agent.
Version 1 approvals bind canonical full paths and session/operation; wildcards also bind the command hash
and logical save count. Legacy basename-only grants do not qualify. The neutral configuration file can
select another backup destination and reserve; the store cannot include a save root. Both older Skyrim
save formats remain readable, and legacy recovery leaves their evidence intact. Restore defaults to a
new directory, expands a member selection to its pair, verifies chosen blobs and journals publication.

⚠ This inverted in v3.8.3 and this page said the opposite until v3.9. A guard that prompts on routine work gets approved by reflex and then protects nothing; the X4 toolkit measured 40 such prompts across 13,282 commands, every one of them noise. So the honest summary is: **few prompts, a short deny list, and a lot of advice** -- not "nothing happens without your approval".

### 2. Reversibility
Every edit made through the edit tools (and Codex `apply_patch`) has a timestamped backup; a file a script writes does not. The `restore-from-backup.sh` script makes recovery straightforward.

### 3. Investigation First
The confidence level system and investigation checklist ensure research happens before action. This prevents the most common class of errors: acting on incorrect assumptions about how Skyrim works.

### 4. Binary Files Are Sacred
ESP, ESM, ESL, BSA, and BA2 files cannot be written directly through the edit tools or the shell writes the guard can read (a script run from a file, or a name built at runtime, is not seen). They must go through proper tooling (xelib, Spriggit, Creation Kit). This prevents accidental corruption of binary formats.

### 5. Continuous Improvement
The "safety improvement loop" instruction in CLAUDE.md asks Claude to evaluate whether new hooks or protections are needed after every session. The `Hook Candidates` section in the knowledgebase tracks proposed improvements.

## Customizing Safety

The guards can be customized, but their shipped files can be replaced during an update. Keep personal rules in separate local files. Before an update, run `tools/pre-update-snapshot.sh` as README.md's FAQ ("How do I update the toolkit?") says; extracting over the installation overwrites those files. If you added your own pre-tool handlers, use the reviewed upgrade in `docs/upgrading-native-hooks.md` instead, and keep the old handlers active until their replacement is qualified. To customise:

- **Whitelist paths** you want Claude to edit freely (e.g., a working directory for scripts)
- **Add new patterns** to the bash guard for commands specific to your workflow
- **Change a rule's decision** (`deny`, `ask` or `advise`) in `tools/skyguard/rules.py` -- for example to make a
  refusal you hit often into a note

The rules live in Python, in `tools/skyguard/`: the shell guard's patterns and messages in
`shell_rules.py`, the file guard's in `write_rules.py`, and which directories count as "the install"
in `paths.py`. The scripts in `.claude/hooks/` are thin adapters -- they read Claude Code's payload,
record a heartbeat and hand the verdict back -- and need no editing to change a rule. (Contributing a
change back? Rules: edit `tools/skyguard/`. Adapters: edit `agent/guards/adapters/claude/` and
regenerate; the `.claude/hooks/` copies in the repository are generated.) The hooks need Python 3 and
refuse every command if it is missing, so a broken engine can never read as "allowed".

## How the guards resolve "the install"

**"The install" is RESOLVED, not guessed from the word "Skyrim".** The guards derive
their root from their own location -- for the Claude Code hooks, two directories above
`.claude/hooks/` -- and add whatever
`setup.sh` recorded in `skyrim-paths.env` at the toolkit root (a 3.x install's
`.claude/skyrim-paths.env` is also read; its protected folders are added to the root file's folders,
with a session-start notice to re-run `setup.sh` when the legacy file remains). The previous rule matched *any*
path containing "Skyrim", which meant the session scratchpad (its path contains
`C--Games-Skyrim-VR`), a checkout of this toolkit, `C:/Temp/skyrim-notes.txt`
and a GitHub URL were all refused as "deleting the game". MEASURED: 71 of 443
refusals over 7,641 real commands were that false positive, and none of the 71 named
the install.

⚠ **If no root can be resolved, the guard DENIES.** A guard that evaluated nothing
must not answer "fine" -- that conflation is what left every hook here inert for five
weeks. ⚠ An unreadable or malformed root `skyrim-paths.env` DENIES every call (`GUARD INERT`) until it is repaired. A broken legacy file also denies when no valid root file exists; beside a valid root file it is reported at session start, and readable, well-formed legacy protection entries still join the union. An emptied file, or a missing key, does NOT cost you refusals: the self-derived root still resolves and valid legacy protection keys remain in the union. With neither file recording the folder, protection narrows to the install plus the generic config literal, and an MO2 folder stops being guarded with nothing reported.

⚠ **A relative path resolves against the working directory the agent reports** in the
hook payload, so `rm Data/x.nif` run from the install is refused. Only when the payload
carries no working directory does it get an advisory (Claude) or a refusal (Codex
refuses such a payload). A relative path inside interpreter code (`python -c`) is not
resolved. Prefer absolute paths when deleting.

## Why so few prompts

**Why so few prompts.** A guard that prompts on routine work gets approved by reflex
and then protects nothing -- the X4 toolkit measured 40 such prompts across 13,282
commands, every one of them noise. Deny is likewise a short list: "the mod manager
owns it" or "this is risky" is a reason to advise, not to refuse, because a guard
that blocks legitimate work is a guard that gets deleted.

## Is the guard actually running?

**Verify the hooks are actually alive: `bash tools/hook-canary.sh`.** Every hook here
was INERT for months -- fired, read zero bytes, exited 0, which is byte-identical to
approving. `/dev/stdin` fails when stdin is a Win32 pipe from a non-MSYS parent,
which is how Claude Code spawns a hook; the suite reproduces that on Windows but not
on Linux, and neither tells you about YOUR install. The canary reads heartbeats
written from inside real invocations, which does.

Each invocation leaves four records in `.claude/backups/.hook-heartbeat/<agent>/`: the hook **ran**, it
**received** a payload, the engine **evaluated** it, and the wrapper **emitted** the verdict. The canary reports
each hook as HEALTHY, STALE, DEGRADED, NOT EXERCISED or UNMEASURABLE (exit 1 if any is DEGRADED, 2 if any is
unmeasurable). A failure is cleared only by a *newer completed evaluation* -- a later payload proves nothing --
and a hook that was invoked but never finished (killed by the agent's timeout) is DEGRADED too. The old 3.x
heartbeat files are ignored.

## The knowledgebase snapshot at session start

The knowledgebase split (`KNOWLEDGEBASE.md` is shipped and replaced on update; `KNOWLEDGEBASE.local.md`
is yours) protects what has not been written yet. An install whose notes are
*already* in the shipped file is covered by one thing only: a copy taken before the
extract. The Claude Code hook `.claude/hooks/session-kb-guard.sh` takes that copy at **SessionStart**,
delegating to `tools/kb-guard.sh`, which snapshots `KNOWLEDGEBASE.md`,
`KNOWLEDGEBASE.local.md`, `CLAUDE.md` and `AGENTS.md` into `.claude/backups/kb/<stamp>/` whenever
the content changed, and raises an alarm when one of them vanishes, empties, or loses
more than half its bytes. Run it by hand any time: `bash tools/kb-guard.sh --verbose`.

- ⚠ **The snapshot is the protection; the alarm is a convenience.** An update that
  replaces a 100 KB knowledgebase with a 140 KB shipped one is a total loss that
  shrinks nothing and raises no alarm. Recovery is always the stored copy.
- ⚠ **Rotation never prunes the oldest, the newest, or the largest copy of a watched
  file.** Every session after an unnoticed loss snapshots the damaged file, so a plain
  oldest-first rotation would walk the good copy off the end — a store that destroys
  what it exists to protect while still looking like a store.
- **Why a separate mechanism at all**, when `backup-before-edit.sh` already exists:
  that hook fires on Edit/Write/NotebookEdit tool calls, and the knowledgebase is usually written
  through a script run via Bash, which an Edit/Write hook structurally cannot see.
  MEASURED on this install 2026-09-08: **zero** `KNOWLEDGEBASE.md` backups across
  seven months, with the backup hook working correctly the whole time.
