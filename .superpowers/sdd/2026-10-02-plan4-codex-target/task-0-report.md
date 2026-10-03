# Task 0 report — Codex re-measurement (Plan 4)

**Status:** DONE except Step 6 (the TUI probe), which needs the user's terminal; instructions are written
and the scratch repo is prepared. Plan 4's stop condition (PreToolUse fires and a deny blocks in the TUI)
is therefore still OPEN — Tasks that depend on TUI parity must not be claimed until it is run.

**Codex:** `codex-cli 0.160.0` (model `gpt-6.1-sol`; user config `approval: never`). Claude Code 2.1.288,
pwsh 7.6.6, Windows PowerShell 5.1.26100.9549. BASE `39d2c1c`.

**Full results:** `docs/superpowers/measurements/2026-10-02-plan4-codex-derisk.md` (predictions committed
first, in `7f8a514`, before any probe ran).

## Measured items

| Item | Result | Tier |
|---|---|---|
| JSON deny | Blocked, command did not run (4/4 across launch forms) | MEASURED |
| ask / bad JSON / timeout (sleep 20 s, timeout 5) | Failed, command ran (1/1 each) | MEASURED |
| exit 1 / exit 2, pwsh only | Failed, command ran (1/1 each) | MEASURED |
| exit 1 / exit 2 through the R2 `\|\|` form | **Completed**, command ran: `\|\|` reran the entry under 5.1 with EMPTY stdin, which exited 0 (3/3) | MEASURED — plan amended (R2, Task 7 fault row) |
| R6 `failureMode: "block"` | ignored (exit 1 still Failed, command ran, 1/1); tolerated (hook loads); not hashed; absent from binary | MEASURED + READ — R6: left out |
| R7 `additionalContextLimit` | honoured and hashed; unit tokens (default 2,500); above it the MIDDLE is cut and full output spilled to `%TEMP%\hook_outputs\`; 6 KB whole without it, 16 KB cut without / whole with 8000 | MEASURED 4 runs — R7: left out, adapter caps at 8,000 B |
| R2 (a) leading quote | Failed, hook never ran | MEASURED 1/1 |
| R2 (b) `bash` with system32 first | Failed, script never ran (Git-bash-first control Blocked) | MEASURED 1/1 + 1/1 |
| R2 (c) missing first program → `powershell` | 5.1 ran, Blocked | MEASURED 2/2 |
| Shapes | apply_patch Add/Update/Move/two-file/Delete as `apply_patch` + `{command}`; shell heredoc patch arrives as `Bash` only and applies; `cd sub &&` verbatim; `-C sub/deep` → payload cwd = subdir; MCP → `mcp__probe__echo` with args | MEASURED |
| Rules, hooks unreviewed | `reg delete` rejected by the forbidden prefix rule; hooks silently skipped; `execpolicy`: bare `reg` forbidden, `reg.exe` no match | MEASURED |
| Latency (R2 bare entry → python, p95; 3 interleaved runs of 30) | 1.58 s (machine-contaminated: Python alone 0.93) / 0.76 / 0.66 s; PowerShell start ≈ 0.3–0.45 s p50 over Python; every rung passed in the 2 clean runs, so the ladder did not stop; gate unchanged | MEASURED, CPU 24–45 % whole-machine |
| Trust hash | X4 scheme reproduces 11/11 app-server `currentHash` vectors (`tests/fixtures/codex/0.160.0/trust_vectors.json`) | MEASURED |
| Transcript | `session_meta` originator `codex_exec`/source `exec`; tools run in code mode (`custom_tool_call` `exec` + JS); typed `item_completed` items CommandExecution/FileChange/McpToolCall; no hook records; `claude -p` entrypoint `sdk-cli` | MEASURED — plan amended (Task 13) |

## Files

- `docs/superpowers/measurements/2026-10-02-plan4-codex-derisk.md`
- `docs/superpowers/measurements/2026-10-02-plan4-tui-probe-instructions.md` (user step)
- `tests/fixtures/payloads/codex/0.160.0/*.json` (12), `tests/fixtures/payloads/README.md`
- `tests/fixtures/codex/0.160.0/trust_vectors.json`, `rollout-exec*.jsonl` (4)
- `tests/fixtures/claude/transcript-p.jsonl`
- Plan amended: Assumed base, R2, R6, R7, Risk 2, Task 7 fault matrix, Task 8 Step 1, Task 13.

## Concerns

1. Step 6 open — the D14 stop condition is unverified until the user runs it.
2. Latency headroom is thin (~0.25–0.35 s p95 for the engine); Task 7's real-chain timing decides.
3. The `\|\|` fallback empty-stdin rerun is a new fail-open path (only reachable on a pwsh crash/kill).
4. Hook output over the token threshold is written to `%TEMP%` by Codex (privacy for preferences).
5. `test_payload_fixtures.py` does not glob version subfolders (leak check covered by hand).
6. The Claude-side `protect-bash` refused an offline `codex execpolicy check` and a doc heredoc that only
   mentioned a registry delete on a non-Bethesda key (false positive, twice) — a Plan 5 guard-policy item.
7. Codex wrote one trust entry to `~/.codex/config.toml` (scratch `p1`, listed in the TUI instructions for
   pruning); the TUI step will add one projects table and two hooks.state tables.
