# Plan 5 live probes (M5a, M5b)

Date 2026-10-02, Windows 11, `claude --version` = 2.1.288 (Claude Code), `claude -p --permission-mode bypassPermissions --model haiku`.
Scratch repos outside this repo; the capture hook (`tools/payload-capture/capture-hook.sh`) wired as PreToolUse, matcher `"*"`.

## M5a - does payload `cwd` track the shell's real cwd? (gates R9)

Prompt: Bash `mkdir sub`; Bash `cd sub`; Bash `pwd`; PowerShell `Set-Location sub`; PowerShell `(Get-Location).Path`.
S = the scratch repo root (`.../plan5-probes/m5a`).

| # | Tool | Command | payload `cwd` | shell's real cwd (printed by the same session) |
|---|---|---|---|---|
| 1 | Bash | `mkdir sub` | S | - |
| 2 | Bash | `cd sub` | S | - |
| 3 | Bash | `pwd` | S | `pwd` printed S (MSYS spelling `/tmp/claude/.../m5a`) |
| 4 | PowerShell | `Set-Location sub` | S | - |
| 5 | PowerShell | `(Get-Location).Path` | S\sub | printed S\sub |

Three runs gave the same payload-cwd column. Findings:
- PowerShell: payload `cwd` followed `Set-Location` (S -> S\sub) and equals the real cwd.
- Bash: payload `cwd` equals what `pwd` prints in the following call (S). A `cd` in one Bash call did **not**
  persist to the next call's `pwd` in this headless `-p` session, and the payload `cwd` agreed with that.
  So payload `cwd` == the shell's real cwd for both tools in every observed case.

**Decision (pre-committed, applied verbatim): branch 1 applies** - `cwd` equals the shell's real cwd after the `cd`
for both tools, so **relative resolution trusts the payload `cwd`**. R9's fallback does NOT apply; Task 7's relative-deny
rows are not re-authored as S-RELATIVE-UNKNOWN advise.

Caveat (INFERRED, not tested): a `cd` and its use chained inside ONE command (`cd sub && rm x`) is not covered by
payload `cwd`; that is a within-command matter for the parser, not for this measurement.

## M5b - Claude MCP PreToolUse payload (gates Task 11)

Throwaway stdio MCP server (Python JSON-RPC; initialize, tools/list with `console` and `game`, tools/call -> "ok"),
registered as `devbench-vr` in the scratch repo's `.mcp.json`, loaded with `--mcp-config`.

- `tool_name`: `mcp__devbench-vr__console` and `mcp__devbench-vr__game` (expected shape confirmed).
- `tool_input`: the tool's arguments verbatim, e.g. `{"command": "help"}` / `{"action": "list"}`.
- Extra top-level key: `mcp_server: {"name": "devbench-vr", "source": "dynamic"}` (`source` is `dynamic` because the
  server came from `--mcp-config`; a `.mcp.json` auto-load may report another value - not measured).
- Fixtures (scrubbed with `tools/payload-capture/scrub.py`, no hand edits):
  `tests/fixtures/payloads/claude/claude-PreToolUse-mcp__devbench-vr__console-1.json`, `...__game-1.json`.
- Note: the model first called `ToolSearch` (deferred MCP tool schema), a PreToolUse payload with `tool_name` `ToolSearch`; not committed.
- **Matcher `mcp__devbench.*`:** wired as a second PreToolUse hook beside the `*` one. It fired exactly twice,
  for the two MCP calls (`console`, `game`), and not for `ToolSearch`. **It matches.**
