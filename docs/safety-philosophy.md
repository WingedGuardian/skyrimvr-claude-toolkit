# Safety Philosophy

## Why Safety Matters in Skyrim Modding

A modded Skyrim installation with hundreds of plugins is a complex, interdependent system. Small changes can have cascading effects:

- A bad ESP edit can corrupt save files permanently
- An incorrect INI setting can cause crashes with no obvious error
- Overwriting a critical file with no backup means hours of reinstallation
- VR-specific quirks mean "works in SSE" doesn't mean "works in VR"

This toolkit was built after experiencing all of these. Every safety feature exists because something went wrong without it.

## Defense in Depth

The toolkit uses multiple layers of protection:

### Layer 1: Knowledge (KNOWLEDGEBASE.md)
Before making any change, Claude is instructed to check the knowledgebase for known pitfalls. Many Skyrim modding errors are well-documented but easy to forget.

### Layer 2: Confidence Levels
Claude must explicitly rate its confidence (0-100%) before proposing any change and list assumptions. This forces investigation before action.

#### Confidence scale
| Range | Meaning | Action |
|-------|---------|--------|
| 95-100% | Verified via testing, docs, or authoritative source | Proceed with user confirmation |
| 80-94% | Strong evidence but not fully verified | Proceed with caveats noted |
| 60-79% | Reasonable assumption, some unknowns | Research more before proceeding |
| < 60% | Speculative | Do NOT proceed -- investigate first |

### Layer 3: Hook Guards
Six thin wrappers over the Python engine `tools/skyguard/` intercept Claude's tool calls:

- **protect-bash.sh** -- Blocks destructive commands; **advises** on file operations in the game directory (a note to Claude, not a prompt to you)
- **protect-files.sh** -- Blocks writes to plugin/archive binaries through the Edit/Write tools; **advises** on all other edits in the install
- **protect-mcp.sh** -- Guards the DevBench MCP tools (and the curl / `devbench-cli.sh` route to the same tools): denies the one console command measured to crash a large load order (`smp reset`), and asks before a save would overwrite an existing save
- **backup-before-edit.sh** -- Copies a file before Claude modifies it **through the Edit/Write tools**. ⚠ Anything written by a script run through Bash is invisible to it -- that is a property of hooks, not a bug here. MEASURED: zero backups of a 177,459-byte `KNOWLEDGEBASE.md` across seven months, because it is written by a script. `session-kb-guard.sh` below covers that channel.
- **snapshot-before-tool.sh** -- Snapshots active Papyrus source/compiled scripts before any Bash command
- **session-kb-guard.sh** -- At SessionStart, copies the files nothing can rebuild
  (`KNOWLEDGEBASE.md`, `KNOWLEDGEBASE.local.md`, `CLAUDE.md`, `AGENTS.md`) and raises an alarm if
  one shrank. It exists because the first five all key on a *tool call*, and the
  knowledgebase is usually written by a script run through Bash -- invisible to an
  Edit/Write hook. Measured on the author's install: zero knowledgebase backups in
  seven months, with the backup hook working correctly throughout.

### Layer 4: Dry-Run Convention
ESP modifications via xelib always use a two-pass workflow: read-only preview, then write only after human approval.

### Layer 5: Audit Trail
Every file modification is logged with timestamp, tool name, and backup location. If something goes wrong, you can trace exactly what changed and when.

## Design Principles

### 1. No Silent Modifications
Destructive changes are blocked outright. Everything else that is consequential but legitimate is **advised**: a note is injected into Claude's context and the call proceeds -- you are not prompted. Exactly one rule in the toolkit asks you to decide (a ReSaver command that mutates a save).

⚠ This inverted in v3.8.3 and this page said the opposite until v3.9. A guard that prompts on routine work gets approved by reflex and then protects nothing; the X4 toolkit measured 40 such prompts across 13,282 commands, every one of them noise. So the honest summary is: **few prompts, a short deny list, and a lot of advice** -- not "nothing happens without your approval".

### 2. Reversibility
Every edit has a timestamped backup. The `restore-from-backup.sh` script makes recovery straightforward.

### 3. Investigation First
The confidence level system and investigation checklist ensure research happens before action. This prevents the most common class of errors: acting on incorrect assumptions about how Skyrim works.

### 4. Binary Files Are Sacred
ESP, ESM, ESL, BSA, and BA2 files cannot be written directly. They must go through proper tooling (xelib, Spriggit, Creation Kit). This prevents accidental corruption of binary formats.

### 5. Continuous Improvement
The "safety improvement loop" instruction in CLAUDE.md asks Claude to evaluate whether new hooks or protections are needed after every session. The `Hook Candidates` section in the knowledgebase tracks proposed improvements.

## Customizing Safety

The hook scripts are designed to be customized -- but ⚠ **they are shipped files, so extracting an update replaces them and your edits are lost.** Keep a copy of any hook you change, or put your rules in a separate hook and register it in `.claude/settings.json` (also shipped -- copy it too). To customise:

- **Whitelist paths** you want Claude to edit freely (e.g., a working directory for scripts)
- **Add new patterns** to the bash guard for commands specific to your workflow
- **Adjust the confirmation threshold** -- some users may want less friction for frequently-edited files

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
`setup.sh` recorded in `.claude/skyrim-paths.env`. The previous rule matched *any*
path containing "Skyrim", which meant the session scratchpad (its path contains
`C--GOG-Games-...-Skyrim-VR`), a checkout of this toolkit, `C:/Temp/skyrim-notes.txt`
and a GitHub URL were all refused as "deleting the game". MEASURED: 71 of 443
refusals over 7,641 real commands were that false positive, and none of the 71 named
the install.

⚠ **If no root can be resolved, the guard DENIES.** A guard that evaluated nothing
must not answer "fine" -- that conflation is what left every hook here inert for five
weeks. ⚠ A broken `skyrim-paths.env` does NOT cost you refusals: the self-derived root always resolves, so the guard silently narrows to the install folder plus the generic config literal, and an MO2 profile directory stops being guarded with nothing reported. The fail-closed branch fires only when NO root resolves at all.

⚠ **A relative path cannot be resolved** -- the hook is never told the caller's
working directory -- so `rm Data/x.nif` gets an advisory, not a refusal. Prefer
absolute paths when deleting.

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
  that hook fires on Edit/Write tool calls, and the knowledgebase is usually written
  through a script run via Bash, which an Edit/Write hook structurally cannot see.
  MEASURED on this install 2026-09-08: **zero** `KNOWLEDGEBASE.md` backups across
  seven months, with the backup hook working correctly the whole time.
