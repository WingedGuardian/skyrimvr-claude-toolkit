# AGENTS.md

Instructions for AI coding agents working in this Skyrim modding environment.

**Read `CLAUDE.md` first — it is the full project instruction set**, and it is agent-neutral apart
from the parts noted below. It contains the configured paths for this install, the tool inventory and
how to invoke each one, the ESP-editing workflows, and the core working principles. `KNOWLEDGEBASE.md`
is the reference for engine quirks and version differences; consult it before making changes rather
than after something breaks.

This file exists so agents that look for `AGENTS.md` by convention find their way in. It deliberately
does not duplicate `CLAUDE.md`, so the two cannot drift apart.

## What is portable, and what is not

**Portable to any agent** — this is most of the toolkit by volume:

- `KNOWLEDGEBASE.md` — engine behaviour, VR/SE/AE differences, tool gotchas. Plain reference.
- `CLAUDE.md` — paths, tool usage, workflows, principles.
- Everything under `tools/`, `examples/`, and `scripts/` — plain bash, Node, and Python. No agent
  runtime is involved; they work from any shell.

**Claude Code specific — you do not get these:**

- `.claude/settings.json` and `.claude/hooks/` — the safety layer. These hooks are what block direct
  writes to ESP/ESM/BSA files, require confirmation before editing anything in the game or config
  directories, and auto-back-up every file before it is modified, with an audit trail.
- `.claude/skills/` — packaged workflows.

**Read that second list carefully.** On another agent the guardrails are simply absent. Nothing will
stop a bad `rm`, a direct binary write into a plugin, or an unreviewed edit to a live INI. The
knowledge and the tools carry over; the seatbelts do not.

If you are not running under Claude Code, compensate deliberately:

1. **Back up before you touch anything.** Copy the file first — the toolkit's own convention is
   `.claude/backups/<descriptive-name>/`. Assume nothing is doing this for you.
2. **Never write directly to `.esp` / `.esm` / `.esl` / `.bsa` / `.ba2`.** Use Spriggit (serialize to
   YAML, edit the YAML, deserialize) or xelib. A hand-edited plugin binary is a corrupted plugin.
3. **Show the user the change before applying it**, especially for INIs, load order files, and
   anything under the game directory. The hooks normally force this pause; without them it is on you.
4. **Snapshot `.psc` sources before experimenting.** They ARE covered by `snapshot-before-tool.sh` (before every Bash command) and by `backup-before-edit.sh` (Edit/Write, no extension filter) -- an earlier version of this line said neither covered them, which was wrong. Snapshot anyway for a named rollback point, because an automatic copy you cannot identify later is not a rollback plan.
   under Claude Code, and reconstructing a working script from memory is miserable.

## Ground rules that apply regardless of agent

- **State a confidence level before proposing a change**, and investigate until it is high. Skyrim is
  full of undocumented quirks and version differences; things frequently do not work the way they
  read.
- **Never assume Skyrim SE behaviour equals Skyrim VR behaviour.** Verify per feature.
- **Use the engine's own mechanisms before writing a Papyrus workaround**, and model any new mechanic
  on how vanilla Skyrim does the closest equivalent.
- **Consult `KNOWLEDGEBASE.md` before acting**, and write what you learn into **`KNOWLEDGEBASE.local.md`** -- never back into `KNOWLEDGEBASE.md`. The toolkit ships that file, so the next update replaces it and your notes are gone.
