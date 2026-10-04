# Hook payload fixtures — captured from real sessions

**Never edit these by hand, and never write one from documentation.** Each file is a payload a real
agent sent to a real hook, passed through `tools/payload-capture/scrub.py`. Adapters and conformance
tests are built against these, so a fixture written from the docs would test our reading of the docs,
not the agent. Recapture when an agent upgrades its hook protocol.

## This capture

- **Date:** 2026-10-01, Windows 11, Git Bash.
- **Agents:** `claude --version` → `2.1.287 (Claude Code)`; `codex --version` → `codex-cli 0.159.3`.
- **How:** the capture hook (`tools/payload-capture/capture-hook.sh`, sha256 prefix `dea70de90781430a`)
  was wired as a `PreToolUse` (matcher `"*"` / `".*"`) and `SessionStart` hook in two throwaway scratch
  repos, and each agent was launched with `PAYLOAD_CAPTURE_DIR` set. These captures were taken during
  the Plan 1 de-risk run (`docs/superpowers/measurements/2026-10-01-plan1-derisk.md`, R6/R7) with a
  byte-identical copy of the capture hook.
- **Claude Code prompt** (`claude -p --permission-mode bypassPermissions`): *"Do each of these with the
  named tool, one call each: Bash tool: echo hi. PowerShell tool: Get-Date. Write tool: create notes.txt
  containing a. Edit tool: change a to b in notes.txt. Then reply DONE and stop."*
- **Codex prompt** (`codex exec --dangerously-bypass-hook-trust -s workspace-write`): *"One tool call each,
  in order: shell: Get-Date. apply_patch: add file notes/a.txt with line hi. apply_patch: update
  notes/a.txt so the line is hello. apply_patch: delete notes/a.txt. Then reply DONE."*

## Monitor and NotebookEdit (captured 2026-10-04, Plan 5 Task 13b)

- **Agent:** `claude --version` -> `2.1.289 (Claude Code)`. Same capture hook, matcher `"*"`, in a scratch project under
  the session scratchpad; `claude -p --permission-mode bypassPermissions` with the prompt *"Do each with the named
  tool, one call each: Monitor tool: watch the command 'echo hi'. NotebookEdit tool: in nb.ipynb replace cell 0 source
  with 'a=2'. Then reply DONE and stop."* Only these two payloads were scrubbed in (the ToolSearch and Read calls were left out).
- **Monitor** (`claude-PreToolUse-Monitor-1.json`): the shell command is `tool_input.command`; siblings `description`,
  `timeout_ms`. No dialect field.
- **NotebookEdit** (`claude-PreToolUse-NotebookEdit-1.json`): the path is `tool_input.notebook_path` (absolute); there
  is NO `file_path`. Siblings `cell_id`, `new_source`, `edit_mode`.
- **Monitor WebSocket form** (`tool_input {ws: {url}, description, timeout_ms}`, no `command`): UNMEASURED here, no
  fixture. Evidence is the documented Monitor schema (`ws {url, protocols}`, cannot be combined with `command`; a command
  Monitor "runs in the same shell environment as Bash") plus a report from another install.

## Recapture

```bash
PAYLOAD_CAPTURE_DIR="$PWD/raw" claude -p --permission-mode bypassPermissions "<prompt>" < /dev/null
PAYLOAD_CAPTURE_DIR="$PWD/raw" codex exec --dangerously-bypass-hook-trust -s workspace-write "<prompt>" < /dev/null
python tools/payload-capture/scrub.py raw tests/fixtures/payloads/<agent> --agent <agent>
```
Run these in a scratch repo whose hooks point at `capture-hook.sh`, never in a real install.
`--dangerously-bypass-hook-trust` applies to that one scratch invocation only.

## What is scrubbed

Every `*_id` field becomes `<id>`. `transcript_path` and `cwd` become `<path>`. Paths under the session's
cwd become `<cwd>`. The user's home folder, in any spelling, becomes `<HOME>`. Everything else is kept
verbatim, including fields an agent added recently (Claude Code 2.1.287 sends `effort` and `prompt_id`).

## Not captured yet

MCP tool payloads (both agents), Codex `SessionStart` with `source: compact`, the interactive Codex TUI,
and non-CLI surfaces. Owned by Plans 4–5 (see the spec, D14).
