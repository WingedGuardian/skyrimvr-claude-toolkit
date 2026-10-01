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

### Layer 3: Hook Guards
Five bash scripts intercept Claude's tool calls:

- **protect-bash.sh** -- Blocks destructive commands; **advises** on file operations in the game directory (a note to Claude, not a prompt to you)
- **protect-files.sh** -- Blocks writes to plugin/archive binaries through the Edit/Write tools; **advises** on all other edits in the install
- **backup-before-edit.sh** -- Copies a file before Claude modifies it **through the Edit/Write tools**. ⚠ Anything written by a script run through Bash is invisible to it -- that is a property of hooks, not a bug here. MEASURED: zero backups of a 177,459-byte `KNOWLEDGEBASE.md` across seven months, because it is written by a script. `session-kb-guard.sh` below covers that channel.
- **snapshot-before-tool.sh** -- Snapshots active Papyrus source/compiled scripts before any Bash command
- **session-kb-guard.sh** -- At SessionStart, copies the files nothing can rebuild
  (`KNOWLEDGEBASE.md`, `KNOWLEDGEBASE.local.md`, `CLAUDE.md`) and raises an alarm if
  one shrank. It exists because the first four all key on a *tool call*, and the
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

Edit the scripts in `.claude/hooks/` to match your workflow. (Contributing a change back? Edit
`agent/guards/legacy-bash/` instead and regenerate. The `.claude/hooks/` copies in the repository are
generated.)
