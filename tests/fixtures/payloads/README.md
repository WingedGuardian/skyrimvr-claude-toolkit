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

## Codex 0.160.0 recapture (`codex/0.160.0/`, Plan 4 Task 0)

- **Date:** 2026-10-02, Windows 11, `codex --version` → `codex-cli 0.160.0` (model `gpt-6.1-sol`).
- **How:** a probe hook (`probe-hook.py`, scratch only; it saves each raw payload exactly as
  `capture-hook.sh` does, then answers by a marker in the command) behind the Plan 4 **R2** launch form
  (`pwsh … -File "<scratch>/sp ace/probe.ps1" || powershell …`), in a throwaway scratch git repo, driven by
  `codex exec --dangerously-bypass-hook-trust -s workspace-write`. One payload per shape was picked from
  the raw set and passed through `scrub.py --agent codex`. Measurements and prompts:
  `docs/superpowers/measurements/2026-10-02-plan4-codex-derisk.md`.
- **Files** (scrub.py numbers them in capture-pick order):

| File | What it is |
|---|---|
| `codex-SessionStart-none-1.json` | SessionStart, `source: "startup"`, session at the project root |
| `codex-SessionStart-none-2.json` | SessionStart from `codex exec -C sub/deep` (the raw `cwd` was the subdirectory) |
| `codex-PreToolUse-Bash-1.json` | `New-Item -ItemType File DENYME.flag` (the hook answered a JSON deny; Codex showed `Blocked`) |
| `codex-PreToolUse-Bash-2.json` | `apply_patch <<'PATCH' …` run **through the shell**: arrives as `Bash`, no `apply_patch` event, and the patch applied |
| `codex-PreToolUse-Bash-3.json` | `cd sub && Get-ChildItem` |
| `codex-PreToolUse-Bash-4.json` | `Get-Location .` from a `-C sub/deep` session (raw `cwd` = the subdirectory, not the project root) |
| `codex-PreToolUse-apply_patch-1..5.json` | Add · Update · Update + `*** Move to:` · one patch with two Add headers · Delete |
| `codex-PreToolUse-mcp__probe__echo-1.json` | an MCP tool call (toy stdio server `probe`, tool `echo`, registered with `-c mcp_servers.probe…`, never in config.toml). The call itself was then refused by Codex's approval policy; the hook fired first |

- **Prompts:** one exec run per marker or shape; the exact prompt is the first line of each run's log,
  summarised in the measurements doc.
- **Not captured here:** the interactive TUI and `SessionStart` with `source: "compact"` (Task 0 Step 6,
  a user step: `docs/superpowers/measurements/2026-10-02-plan4-tui-probe-instructions.md`), Claude MCP
  payloads (Plan 5), non-CLI Codex surfaces (D14, unclaimed).
- Transcript excerpts for Task 13 are in `tests/fixtures/codex/0.160.0/rollout-*.jsonl` and
  `tests/fixtures/claude/transcript-p.jsonl`; trust-hash vectors in
  `tests/fixtures/codex/0.160.0/trust_vectors.json`.

## Not captured yet

Claude MCP tool payloads, Codex `SessionStart` with `source: compact` and the interactive Codex TUI
(pending the Task 0 Step 6 user probe), and non-CLI surfaces. Owned by Plans 4–5 (see the spec, D14).
