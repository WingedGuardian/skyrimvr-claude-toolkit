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
Three bash scripts intercept Claude's tool calls:

- **protect-bash.sh** -- Blocks destructive commands, confirms file operations
- **protect-files.sh** -- Blocks binary file writes, confirms all other edits
- **backup-before-edit.sh** -- Copies every file before modification

### Layer 4: Dry-Run Convention
ESP modifications via xelib always use a two-pass workflow: read-only preview, then write only after human approval.

### Layer 5: Audit Trail
Every file modification is logged with timestamp, tool name, and backup location. If something goes wrong, you can trace exactly what changed and when.

## Design Principles

### 1. No Silent Modifications
Every file change triggers a confirmation prompt or is blocked outright. There are no "auto-approved" edits to game files.

### 2. Reversibility
Every edit has a timestamped backup. The `restore-from-backup.sh` script makes recovery straightforward.

### 3. Investigation First
The confidence level system and investigation checklist ensure research happens before action. This prevents the most common class of errors: acting on incorrect assumptions about how Skyrim works.

### 4. Binary Files Are Sacred
ESP, ESM, ESL, BSA, and BA2 files cannot be written directly. They must go through proper tooling (xelib, Spriggit, Creation Kit). This prevents accidental corruption of binary formats.

### 5. Continuous Improvement
The "safety improvement loop" instruction in CLAUDE.md asks Claude to evaluate whether new hooks or protections are needed after every session. The `Hook Candidates` section in the knowledgebase tracks proposed improvements.

## Customizing Safety

The hook scripts are designed to be customized:

- **Whitelist paths** you want Claude to edit freely (e.g., a working directory for scripts)
- **Add new patterns** to the bash guard for commands specific to your workflow
- **Adjust the confirmation threshold** -- some users may want less friction for frequently-edited files

Edit the scripts in `.claude/hooks/` to match your workflow.
