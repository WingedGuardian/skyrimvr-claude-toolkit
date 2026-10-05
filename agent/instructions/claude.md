
---

## Claude Code specifics

Generated into `CLAUDE.md` only. Everything above is shared with every agent.

- **Skills** live in `.claude/skills/<name>/SKILL.md`. Load one with the Skill tool (`Skill: skyrim-nif`).
  A path-scoped skill (`paths:` in its frontmatter) is *listed* automatically when you touch a matching
  file (`skyrim-nif` is scoped to `**/*.nif,Data/meshes/**`). Listing is not loading: invoke it.
- **The guards** are `.claude/hooks/*.sh`, thin adapters over `tools/skyguard/`, wired up in
  `.claude/settings.json`: `Bash` → `protect-bash` + `snapshot-before-tool`; `Edit|Write` →
  `protect-files` + `backup-before-edit`; SessionStart → `session-kb-guard` (knowledgebase snapshot, then your `## My preferences`). `setup.sh` records the
  Python path in each hook. The user lists or disables them with `/hooks`. The PowerShell tool is not
  matched yet.
- **What the backup hook cannot see.** `backup-before-edit.sh` fires on Edit/Write tool calls only, with no
  extension filter (`.psc` included). A file written by a script run through the Bash tool is invisible to
  it. That is how the knowledgebase is usually written, and it is why `session-kb-guard.sh` snapshots the
  knowledgebase files at SessionStart. `snapshot-before-tool.sh` copies recent `.psc`/`.pex` before each
  non-read-only Bash command.
- **Why `hook-canary.sh` matters here.** Claude Code spawns a hook with a Win32 pipe on stdin, on which
  `cat /dev/stdin` returns zero bytes. Every hook here was inert that way for months while its suite
  passed. The canary reads heartbeats written from inside real invocations.
- **A refused command the user really wants**: they can run it themselves with the `!` prefix.
