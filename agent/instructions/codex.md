
---

## Codex specifics

Generated into `AGENTS.md` only; everything above is shared. Other agents: this section is not about you.

- **The guards run only after the user approves them.** Codex skips unapproved project hooks silently.
  When they run, your session context starts with `SKYGUARD: Codex hooks live`. **If that line is
  missing, the guards are NOT running:** tell the user to run `/hooks` in Codex, approve the Skyrim
  toolkit's two hooks, and check with `bash tools/hook-canary.sh`. Approval is needed again after a
  toolkit update whose CHANGELOG says "re-approve your Codex hooks", and (inferred, not measured) after the
  install folder moves: the approval covers the definition as rendered, install path included.
- **Run the toolkit's scripts as `bash tools/<name>.sh`.** Do not assume your shell is bash: on Windows
  it may be PowerShell, so write commands that work there.
- **A refusal is final.** The guards cannot ask on Codex, so anything that needs the user's confirmation
  is refused with `NEEDS YOUR CONFIRMATION`. Stop and explain what you wanted to run and why; if the user
  agrees, they run it themselves. Never retry it in another form.
- **Checked:** every shell command and every `apply_patch`, including one run through the shell. Writing
  a plugin or archive (`.esp/.esm/.esl/.bsa/.ba2`) directly is refused; deleting in the game or config
  folders is refused; edits inside the game get advice. Files an `apply_patch` changes or deletes are
  backed up to `.claude/backups/` first, and recent `.psc`/`.pex` are snapshotted before shell commands.
  A file written by a script you run is not backed up.
- **Not checked:** commands the user types with `!` in the Codex TUI (they bypass hooks), and MCP tools
  (DevBench included).
- **Skills** are in `.agents/skills/<name>/SKILL.md`: read the file when `core.md` points at it by name.
- `bash tools/hook-canary.sh` shows whether each hook ran, saw its input and finished, per surface.
