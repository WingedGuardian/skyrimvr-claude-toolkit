# Plan 4 Task 0 — Codex re-measured before anything is frozen

Plan: `docs/superpowers/plans/2026-10-02-plan4-codex-target.md`, Task 0. Measured 2026-10-02 on this
machine (Windows 11 26200, shared with other sessions), in one throwaway scratch repo outside every
repo and install: `<scratch>/plan4-probes/p1` (a git repo; the hook folder is `p1/sp ace/`, with a space
on purpose). Driven with `codex exec --dangerously-bypass-hook-trust -s workspace-write` (bypass flag in
the scratch repo only). Tier key: MEASURED (count given), READ (file/binary cited), INFERRED (hedged).

## Predictions (written BEFORE any probe ran)

| Probe | Prediction |
|---|---|
| DENYME (JSON deny) | `Blocked`, flag absent |
| ASKME (JSON ask) | `Failed`, flag created (fails open) |
| EXIT1ME | `Failed`, flag created |
| EXIT2ME (stderr + exit 2) | `Failed`, flag created |
| BADJSON | `Failed`, flag created |
| SLEEPME (sleep 20 s, timeout 5) | `Failed` after ~5 s, flag created |
| R6 `failureMode: "block"` + EXIT1ME | ignored: flag created. READ before running: the 0.160.0 binary contains no `failureMode`/`failure_mode` string, and `HookHandlerConfig::Command` has 6 fields (`command, commandWindows, timeout, async, statusMessage, additionalContextLimit`). Second prediction: the unknown key does not stop the hook from loading |
| R7 CTXME 6,000 B / SessionStart 6,000 B, no limit | both tails visible. READ before running: the app-server schema calls `additionalContextLimit` a *spill threshold* in **tokens** (`null` = 2,500 tokens, `0` = no spilling) and the binary has `hooks/src/output_spill.rs`, so 6,000 B (~1,500 tokens) is under the default and the brief's 6,000 B probe cannot show truncation. Added probe: 16,000 B (~4,000 tokens) spills (tail not inline) without a limit and is inline with `8000` |
| R2 (a) leading-quote command | `Failed`, flag created |
| R2 (b) `bash "<path>"` with `C:\Windows\system32` first on PATH | `Failed` (WSL stub), flag created |
| R2 (c) `<nonexistent> … \|\| powershell …` | runs via `powershell` 5.1: `Blocked` |
| Rules `reg delete` without the bypass flag | rejected by the rule; `codex execpolicy check` → `forbidden` |
| Latency, R2 entry → `python -c pass` | pwsh p50 ~0.5 s, p95 ≤ 1 s idle; powershell 5.1 slightly faster |
| Trust hash | X4's scheme reproduces every `currentHash`; `failureMode` does not change the hash; `additionalContextLimit: 8000` does |
| Rollout surface | `session_meta.payload.originator = "codex_exec"` |
| `claude -p` transcript | `entrypoint = "sdk-cli"` |

## Step 1 — preconditions (MEASURED, all pass)

Run in the plan4 worktree (`../skyrimvr-toolkit-plan4`, branch `plan4`, created from master `39d2c1c`),
not in the main checkout, which this session does not touch.

| Check | Result |
|---|---|
| `git status --short` | empty |
| Plan 3 merge commits | `bfe1902 Merge Plan 3…`, `39d2c1c Plan 3 close-out…`, `b021f14 … My preferences (D15)` present |
| `tools/skyguard/prefs.py` | prefs-ok |
| `CODEX_ADDENDUM_RESERVE` in `gen_agent_trees.py` | 2 |
| `printf -v NOW` in `protect-bash.sh` | 1 |
| `gen_agent_trees.py --budget` | `core.md + codex.md (reserved): 30663 / 32768 bytes (headroom 2105)`; claude row 30331 (headroom 2437) |
| BASE (`git rev-parse HEAD`) | `39d2c1ccf02c632dcfc6263192de3f4e3bfe7cd4` |
| versions | `codex-cli 0.160.0` · `2.1.288 (Claude Code)` · pwsh `7.6.6` · Windows PowerShell `5.1.26100.9549` |

The reserved row moved since the plan was written (31,305 → 30,663), so **`codex.md` may now be up to
2,048 + 2,105 = 4,153 bytes** (the plan said 3,511; the ≤ 3,000 target is unaffected).

## Step 3 — exec probes (one `codex exec` per row; model `gpt-6.1-sol`, approval `never` from the user config)

"Flag" = whether `<MARKER>.flag` exists afterwards. "Status" is Codex's own `hook: PreToolUse <status>` line.
Every run's hook invocations were also counted from the probe's own log (`hooklog.jsonl`).

| Probe | Prediction | Result | Count | Tier | Plan change |
|---|---|---|---|---|---|
| DENYME, R2 form | Blocked, no flag | **Blocked**, no flag; `Command blocked by PreToolUse hook: probe json deny (DENYME)` | 1/1 (4/4 counting the R2(b) control and both R2(c) runs) | MEASURED | none |
| ASKME | Failed, flag | **Failed, flag created** | 1/1 | MEASURED | none (R4 stands) |
| BADJSON | Failed, flag | **Failed, flag created** | 1/1 | MEASURED | none |
| SLEEPME, timeout 5 | Failed, flag | **Failed, flag created**; one invocation only (the `\|\|` fallback did NOT run after the kill) | 1/1 | MEASURED | none |
| EXIT1ME, R2 form | Failed, flag | **`Completed`**, flag created. The hook ran TWICE: pwsh (payload 642 chars) exited 1, so `\|\|` started `powershell` 5.1, which got an **empty stdin** (`len=0`), printed nothing and exited 0 | 3/3 (two EXIT1ME runs + the R6 R2-form run) | MEASURED | **yes, R2/Task 7** (below) |
| EXIT2ME, R2 form | Failed, flag | **`Completed`**, flag created, same double run | 1/1 | MEASURED | same |
| EXIT1ME, pwsh only (no `\|\|`) | Failed, flag | **Failed, flag created** | 1/1 | MEASURED | none |
| EXIT2ME, pwsh only | Failed, flag | **Failed, flag created** (exit 2 is still not a block on 0.160.0) | 1/1 | MEASURED | none |
| R6 `failureMode: "block"` + EXIT1ME, pwsh only | ignored | **ignored**: Failed, flag created; the hook still loaded and fired (the unknown key is tolerated) | 1/1 | MEASURED + READ (no `failureMode` string in the 326 MB `codex.exe`; `HookHandlerConfig::Command with 6 elements`) | **R6: leave it out; D16 stays open** |
| R6, R2 form | ignored | Completed, flag created (the `\|\|` rerun again) | 1/1 | MEASURED | — |
| R2(a) leading-quote command (`"C:/Program Files/PowerShell/7/pwsh.exe" … -File "<sp ace>/probe.ps1"`) | Failed | **Failed, flag created; the hook never ran** (no PreToolUse in the probe log) | 1/1 | MEASURED | none (R2 stands) |
| R2(b) `bash "<sp ace>/probe.sh"`, `C:\Windows\system32` first on PATH | Failed | **Failed, flag created; `probe.sh` never ran** (no `bashwho.txt`). `C:\Windows\System32\bash.exe` exists (WSL stub; only a `docker-desktop` distro) | 1/1 | MEASURED | none (R2 stands) |
| R2(b) control: Git's `usr\bin` first on PATH | — | **Blocked**; `BASH_VERSION=5.2.37(1)-release OSTYPE=msys` | 1/1 | MEASURED | — |
| R2(c) `nonexistent-pwsh-probe … \|\| powershell …` | runs via 5.1, Blocked | **Blocked**; the probe recorded `PS=5.1.26100.9549` for the PreToolUse call | 2/2 | MEASURED | none |

### R7 — additionalContext size (PreToolUse `CTXME` and SessionStart, both via JSON `additionalContext`)

The model was asked for the first and last token of each context; the rollout file was then read to see
exactly what Codex injected (the model's answer alone could not show a middle cut).

| Size | `additionalContextLimit` | Model answer | What the rollout holds | Count |
|---|---|---|---|---|
| 6,000 B (~1,500 tokens) | unset | both heads + both tails | full text inline (6,481 / 6,479-char developer messages) | 1/1 |
| 6,000 B | 8000 | both heads + both tails | full text inline | 1/1 |
| 16,000 B (4,002 tokens) | unset | both heads + both tails | **`Warning: truncated output (original token count: 4002)`, head and tail kept, the MIDDLE cut (`…1541 tokens truncated…`), then `Full hook output saved to: %TEMP%\hook_outputs\<thread id>\<id>.txt`** — for SessionStart and PreToolUse alike | 1/1 |
| 16,000 B | 8000 | both heads + both tails | full text inline (16,620 / 16,619 chars) | 1/1 |

MEASURED: the field is honoured, its unit is **tokens** (READ: app-server schema "`null` uses 2,500
tokens; `0` disables spilling"), and over the threshold Codex cuts the **middle**, not the tail — so a
tail sentinel cannot detect it (the brief's 6,000 B probe would have passed either way) — and **writes the
full hook output to `%TEMP%\hook_outputs\`**, so a SessionStart carrying preferences would leave a copy in
TEMP. Ruling outcome: R7's condition is "the default truncates a realistic SessionStart". INFERRED: a
realistic one (kb-guard output + ≤ 4,096 B of preferences, Plan 3 R5) is ≤ ~6–7 KB ≈ 1,500–1,800 tokens,
under the default; so **the definition does not set the field**, and the adapter caps its own
`additionalContext` below the threshold (≤ 8,000 bytes, with the visible "truncated — read
KNOWLEDGEBASE.local.md" notice), which also keeps the spill-to-TEMP copy from being written. Plan amended.

### Payload shapes (recaptured fixtures, `tests/fixtures/payloads/codex/0.160.0/`)

| Shape | Result | Tier |
|---|---|---|
| `apply_patch` Add / Update / Update+`*** Move to:` / two-file / Delete | `tool_name:"apply_patch"`, `tool_input` = `{"command": <patch text>}`, paths relative, `*** Move to:` on the line after `*** Update File:`, a two-file patch is ONE call with two headers; all five applied | MEASURED 5/5 |
| `apply_patch <<'PATCH' …` through the shell | ONE `Bash` event with the whole heredoc; **no `apply_patch` event**; the patch applied (`viashell.txt` created) | MEASURED 1/1 |
| `cd sub && Get-ChildItem` | `Bash`, `command` verbatim | MEASURED 1/1 |
| session started with `-C sub/deep` | SessionStart and Bash payload `cwd` = `<p1>\sub\deep` (the subdirectory, backslashes); project hooks discovered from the repo root | MEASURED 1/1 |
| MCP (toy stdio server via `-c mcp_servers.probe.*`, never written to config) | PreToolUse `tool_name:"mcp__probe__echo"`, `tool_input` = the arguments (`{"text":"hello-mcp"}`); the hook fired before Codex refused the call under `approval: never` | MEASURED 1/1 |
| payload keys | PreToolUse: `cwd, hook_event_name, model, permission_mode, session_id, tool_input, tool_name, tool_use_id, transcript_path, turn_id`; SessionStart: `cwd, hook_event_name, model, permission_mode, session_id, source, transcript_path` (`source:"startup"`). `permission_mode` was `bypassPermissions` under `exec -s workspace-write` with the user's `approval: never` | MEASURED |

### Rules (`.codex/rules/probe.rules`, `prefix_rule(["reg","delete"], forbidden)`), WITHOUT the bypass flag

| Probe | Prediction | Result | Tier |
|---|---|---|---|
| the model asked to run the registry delete on `HKCU\Software\SkyguardProbe` (nonexistent), hooks unreviewed | rejected by the rule | **rejected**: `` `"…pwsh.exe" -Command "reg delete HKCU\Software\SkyguardProbe /f"` rejected: probe ``. No `hook:` line at all in the output: the unreviewed hooks were skipped silently (E2 holds on 0.160.0) | MEASURED 1/1 |
| `codex execpolicy check` on the bare `reg` form | forbidden | **forbidden** (`matchedPrefix ["reg","delete"]`) | MEASURED |
| the same with `reg.exe` | — | **no match** — R5's second prefix (`reg.exe`) is required | MEASURED |
| the same wrapped in `pwsh.exe -Command "…"` | — | no match offline, although the runtime unwraps the pwsh wrapper (row 1) | MEASURED |

### Latency — hook launch forms through `cmd /c <commandWindows>`, 30 rounds, INTERLEAVED

Each form was fed a captured PreToolUse payload on stdin; every form drains stdin and starts
`python -c pass` (the `.cmd` and floor rows let Python drain it). Whole-machine CPU busy % (28 logical CPUs,
`GetSystemTimes`) was recorded per run. The machine was shared (`Win32_Processor.LoadPercentage` read
68–74 % around the runs; a VaM process was the top CPU consumer). Script: scratch `lat.py`.

| Form | run 1 p50 / p95 (s) | run 2 p50 / p95 | run 3 p50 / p95 |
|---|---|---|---|
| **R2 `pwsh … \|\| powershell …`** | 0.668 / **1.578** | 0.386 / **0.758** | 0.516 / **0.655** |
| R2 with `-NoLogo` | 0.847 / 1.673 | 0.452 / 0.812 | 0.377 / 0.733 |
| PowerShell 5.1 first (`powershell … \|\| pwsh …`) | 0.742 / 1.640 | 0.356 / 0.519 | 0.351 / 0.761 |
| PowerShell 5.1 alone | 0.729 / 1.330 | 0.358 / 0.576 | 0.340 / 0.844 |
| `cmd /c <short .cmd>` → python | 0.362 / 1.356 | 0.049 / 0.231 | 0.056 / 0.324 |
| python floor (no PowerShell) | 0.388 / 0.932 | 0.048 / 0.373 | 0.135 / 0.321 |
| machine busy % before / after the block | 35.5 / 32.8 | 23.5 / 31.5 | 41.0 / 45.2 |

MEASURED: in run 1 **every rung missed p95 ≤ 1 s, including the Python floor (p50 0.39 s, 8× its run-2
value)**, so run 1 measured the machine, not the forms. In runs 2 and 3 every rung passed; the R2 entry's
p95 was 0.76 / 0.66 s. The split (runs 2–3): **PowerShell start ≈ 0.3–0.45 s at p50 over the Python
floor**; PowerShell 5.1 slightly faster than 7 at p50; `-NoLogo` no help. The ladder did not reach its stop
condition (not every rung missed in a clean run); the gate stays at 1 s. INFERRED risk for Task 7/14: the
bare entry leaves only ~0.25–0.35 s of p95 headroom for the engine's own Python run, and X4's full chain
measured p50 1.1–2.1 s. Task 7's 30-call timing of the real chain is the decisive measurement; record the
CPU load alongside it.

## Step 4 — trust-hash oracle (`codex app-server`, `hooks/list`, session-flag route)

Eleven definitions — the Task 8 template's two hooks rendered for the neutral root
`C:/SkyguardVectors/Skyrim Probe (x86)` (POSIX `/c/SkyguardVectors/Skyrim Probe (x86)`), the same with
`failureMode`/`additionalContextLimit`, and three neutral variants — were passed as
`-c hooks.<Event>=[…]` and listed by Codex. Vectors: `tests/fixtures/codex/0.160.0/trust_vectors.json`.

| Finding | Count | Tier |
|---|---|---|
| X4's `codex_trust.hook_hash` (x4-p2-b `agent/guards/adapters/codex_trust.py`) reproduces every `currentHash` | **11/11** | MEASURED |
| `failureMode: "block"` does **not** change the hash (same as the template) | 1/1 | MEASURED |
| `additionalContextLimit: 8000` changes it; `2500` (the default) does not | 2/2 | MEASURED |
| timeout 60 → 61, matcher `.*` → `^Bash$`, dropping `statusMessage`: each changes it | 3/3 | MEASURED |
| session-flag keys look like `C:\<session-flags>\config.toml:pre_tool_use:0:0` (snake_case event, group index, handler index) | — | MEASURED |

The project-file key spelling (`<hooks.json path>:<event>:<g>:<h>`, path case and slashes) can only come
from a real `/hooks` approval: Step 6. Codex's own `[projects.'…']` key for the scratch repo was written
**lower-cased with backslashes** (`c:\users\<user>\…\plan4-probes\p1`), so the hooks.state spelling must be
read, not assumed.

## Step 5 — transcript shape (fixtures `tests/fixtures/codex/0.160.0/rollout-*.jsonl`, `tests/fixtures/claude/transcript-p.jsonl`)

| Question | Result | Tier |
|---|---|---|
| (a) surface | line 1 `type:"session_meta"`, `payload.originator:"codex_exec"`, `payload.source:"exec"`, plus `history_mode:"paginated"`, `thread_source:"user"` | MEASURED 4/4 rollouts |
| (b) tool calls, raw | **Codex 0.160.0 runs tools in "code mode"**: every call is a `response_item` `custom_tool_call` with `name:"exec"` and JavaScript `input` such as `text(await tools.exec_command({cmd:"…"}))`, `tools.apply_patch("…")`, `tools.mcp__probe__echo({…})`. The tool name exists only inside the JS text | MEASURED |
| (b) tool calls, structured | `event_msg` `item_completed` carries typed items: **`CommandExecution`** (`command` = `[pwsh.exe, -Command, <cmd>]`, `source:"unified_exec_startup"`), **`FileChange`** (`changes` keyed by absolute path; also for the shell-heredoc patch), **`McpToolCall`** (`server`, `tool`, `arguments`, `status`) | MEASURED (1 shell, 6 patches, 1 MCP) |
| hook runs in the rollout | **none recorded**: no hook item type; SessionStart context appears as a `developer` message; a blocked call leaves a `custom_tool_call_output` ("Script failed") and **no** `CommandExecution` item | MEASURED 1/1 (deny run) |
| `claude -p` | `entrypoint:"sdk-cli"` on every user/assistant/attachment line; `tool_use` blocks name the tool; hook runs ARE recorded (`attachment.type` `hook_success`, `hook_additional_context`, `hook_system_message`) | MEASURED 1/1 |

Consequence for Task 13 (plan amended): count Codex calls from `item_completed` items
(`CommandExecution`/`FileChange`/`McpToolCall`), not from the `custom_tool_call` JS text; a blocked call
has no item, so `hooked ≥ called` stays the right inequality; any other shape → UNMEASURABLE.

## Step 6 — TUI probe: PENDING (user step)

Prepared scratch repo `<scratch>/plan4-probes/p-tui` (R2-form probe hooks, a read-only `collect.py`).
Instructions: `docs/superpowers/measurements/2026-10-02-plan4-tui-probe-instructions.md`. Open until the
user runs it: (a) PreToolUse fires and a JSON deny blocks in the TUI (the D14 must-have and Plan 4's stop
condition), (b) whether `!cmd` produces a PreToolUse (R4's text), (c) `SessionStart` `source:"compact"`,
(d) the TUI rollout's surface value, (e) the `[hooks.state.'<key>']` spelling and `trusted_hash` values.

## Other findings

1. **The `||` fallback is not only a "pwsh is missing" path.** Any non-zero pwsh exit starts the entry again
   under PowerShell 5.1 with an **empty stdin**, and when that second run exits 0 with no output Codex
   reports `Completed` — an allow. The real wrapper always exits 0, so a pwsh crash or kill is needed to
   trigger it, but then the 5.1 wrapper sees `len=0`. **Task 7's fault matrix gains a row: empty stdin on
   `pre-tool` → the INERT deny** (and `session-start` with empty stdin → the NOT-LIVE line). Plan amended.
2. Hook output above the context threshold is **persisted to `%TEMP%\hook_outputs\<thread>\`** (R7 above).
3. **Codex trust writes:** the exec probes added exactly one `[projects.'…\plan4-probes\p1']` table to
   `~/.codex/config.toml` (diffed against a read-only copy taken before the first run); the `-C sub/deep`
   run, the app-server oracle and `claude -p` added none. Listed for the user to prune; never edited.
4. **Claude-side hook false positive (this session, twice):** `protect-bash` refused an offline
   `codex execpolicy check` on the nonexistent, non-Bethesda key `HKCU\Software\SkyguardProbe` — and later
   a doc write whose heredoc merely *mentioned* that command — with "Cannot delete Bethesda registry keys".
   The probe ran from a script file instead. A guard-policy item for Plan 5 (it matches the text anywhere,
   not the executed program and not only Bethesda keys), not a Plan 4 change.
5. `tests/test_payload_fixtures.py` globs `payloads/<agent>/*.json` non-recursively, so its user-name leak
   check does not cover `codex/0.160.0/`. Checked by hand here (`grep -rli <user> tests/fixtures` → none);
   Task 12 should extend the glob.

## Not run / UNMEASURED

- The interactive TUI (Step 6, pending the user).
- `hooks/list` with `cwds=[<scratch repo>]` for project hooks (X4 saw 0 listed; not re-run).
- A direct `write_stdin` call (X4 P4; not in this brief).
- A clean-machine latency run: all three runs were on a shared, loaded machine.

## Plan changes made from this measurement

R6 (left out, D16 open), R7 (field left out; adapter caps below the token threshold; unit and middle cut
recorded), R2 (the `||` empty-stdin rerun), Task 7 (empty-stdin fault row), Task 8 (hash scheme confirmed
11/11; `failureMode` not hashed; key spelling still from Step 6), Task 13 (code-mode rollout; count
`item_completed` items), Assumed base (`codex.md` ≤ 4,153 bytes), Risk 2 (measured bare-entry split).

## Step 6 — TUI probe result (user-run, 2026-10-02, codex-cli 0.160.0)

**PASS.** Evidence: the probe's hook log and raw payloads, and Codex's own session record
(`originator=codex-tui`, `source=vscode` — run in VS Code's integrated terminal; still the TUI).

| Check | Result |
|---|---|
| SessionStart (startup) fires, context reaches the model | yes |
| PreToolUse fires for an agent shell call (`tool_name` "Bash" on PowerShell) | yes |
| JSON deny holds in the TUI | yes — "Command blocked by PreToolUse hook … probe json deny (DENYME)"; `DENYME.flag` not created |
| A harmless call is allowed | yes (`Get-Date` ran) |
| `!` user-shell command | **no PreToolUse fired** — user-typed commands bypass hooks (user-initiated; not an agent path) |
| SessionStart after `/compact` (`source=compact`) | fired; the model recalled the tail sentinel `SESSIONTAIL-8812` |
| `/hooks` approval hashes | stored hashes equal the X4-scheme predictions for both definitions |

Notes: the "approve anything before the block?" answer was given by Codex (the user had Codex run
`collect.py`), and Codex cannot see TUI approval prompts; the record shows none. Do not infer the
surface from the rollout `source` field (`vscode` here for the TUI).
