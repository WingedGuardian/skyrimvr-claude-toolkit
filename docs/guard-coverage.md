# Guard coverage: every route by which an agent can change files or state

Plan 5 Task 14. This is the inventory the guards are judged against: for each route an agent can use to
mutate the install or the machine, which hook sees it and what that hook does not catch. A hook that "sees" a
route is not a hook that stops everything on it; the last column says what slips through.

`tests/test_guard_coverage.py` parses this file and the shipped `agent/targets/claude/settings.json` and fails
if the two disagree (a route marked guarded that no matcher fires on, a route marked uncovered that a
matcher now fires on, a matcher naming a tool this table lacks, or a summary count that is wrong).

**Status key.**
- **guarded**: a hook fires on every call to the route and the rules judge its content. Known blind spots are
  listed and are not a reason to downgrade the row (they belong to the rule, not the route).
- **partial**: a hook sees the route (or only some calls on it), but judges only part of what it can do.
- **uncovered**: no toolkit hook fires on the route, or one fires and applies no rule. The agent can mutate here
  unobserved by the guards.
- **absent**: the tool does not exist in the measured Claude Code build. The row exists so a future release
  that adds it is noticed.
- **out-of-scope**: not an agent route (the user typing, the harness itself), or it cannot change local state.

**Evidence tier** (every row): MEASURED (a probe was run; the count is given), READ (a file says so),
INFERRED (reasoned, not run).

**Measuring setup for the Claude rows (MEASURED 2026-10-04, Claude Code 2.1.289, `claude -p --permission-mode
bypassPermissions --model haiku`):** a scratch project whose settings hold a `*` capture hook beside the
shipped-style matchers, so the same call shows which matchers fired. Findings are quoted per row.

## Claude Code PreToolUse matchers (from `agent/targets/claude/settings.json`)

Matchers are compared with a full match against the tool name (measured below: `Edit\|Write` did not fire for
`NotebookEdit`, so this is not a substring search).

| Matcher | Hooks |
|---|---|
| `Bash\|PowerShell\|Monitor` | protect-bash, snapshot-before-tool |
| `Edit\|Write\|NotebookEdit` | protect-files, backup-before-edit |
| `mcp__devbench.*` | protect-mcp |

## Routes

| ID | Agent | Route | Tool names | Status | Guard | Blind spots, evidence |
|---|---|---|---|---|---|---|
| C1 | claude | Bash tool | `Bash` | guarded | protect-bash (skyguard shell rules) + snapshot-before-tool | READ: known limits in `shell_rules.py`, `writes.py`, `deletes.py` (relative paths in `python -c`, `perl -i`, `install`, `ln -sf`, `rsync --delete`, `git reset --hard`, variables built from variables). Relative `rm` is ADVISE-only, since a hook never sees the shell's cwd (MEASURED in Plan 4 E2E: deleted `Data/Decoy.esp`) |
| C2 | claude | Bash inside a subagent, a skill, or `run_in_background` | `Bash` | guarded | same as C1 | MEASURED 2026-10-04: a subagent's `echo sub > sub.txt` fired the Bash hook (payload `agent_id` set); skills and background shells are the same tool |
| C3 | claude | PowerShell tool | `PowerShell` | guarded | protect-bash (PowerShell dialect) + snapshot-before-tool | READ: Plan 5 added the matcher; master had none (Plan 6 M2 side finding). Limits as C1, plus piped / location-relative PowerShell and `reg import` (Task 9 docstring) |
| C4 | claude | Edit tool | `Edit` | guarded | protect-files (W- rules) + backup-before-edit | READ: plugin/archive deny, notes for INI, load order, Papyrus, install. MEASURED: `Edit\|Write` fired for `Edit` (2 of 2 calls) |
| C5 | claude | Write tool | `Write` | guarded | protect-files + backup-before-edit | MEASURED: `Edit\|Write` fired for `Write` (1 of 1) |
| C6 | claude | MultiEdit tool | `MultiEdit` | absent | none needed today | MEASURED: the model reported `MultiEdit` does not exist in 2.1.289 (asked for it under `--tools Read,Edit,Write,MultiEdit,NotebookEdit`; it used Edit). The shipped matcher would not fire on it if it came back |
| C7 | claude | NotebookEdit tool (rewrites `.ipynb` cells, any path) | `NotebookEdit` | guarded | protect-files (same W- rules, read from `notebook_path`) + backup-before-edit | MEASURED 2026-10-04, Claude Code 2.1.289 (Plan 5 Task 13b capture): payload `tool_input` is `{notebook_path, cell_id, new_source, edit_mode}`, no `file_path`; the adapter reads `notebook_path`, and a missing or non-string one is refused (W-INERT-NOPATH). Before 13b: the `*` hook fired and `Edit\|Write` did not, the notebook changed unguarded. Blind spots as C4/C5 |
| C8 | claude | Monitor tool (runs a shell command and streams it) | `Monitor` | guarded | protect-bash + snapshot-before-tool, judged under the unknown dialect (bash and PowerShell) | MEASURED 2026-10-04, Claude Code 2.1.289 (Plan 5 Task 13b capture): payload `tool_input` is `{command, description, timeout_ms}`, no dialect field, so the engine judges both readings. Before 13b: the `*` hook fired and `Bash\|PowerShell` did not, and `echo mon > mon.txt` created the file. Limits as C1. The WebSocket form (`ws` object, no `command`; INFERRED from the documented Monitor schema and a report from another install, not captured here) runs no command and is allowed; `ws` beside any `command` key, `ws` that is not an object, or neither key is denied |
| C9 | claude | EnterWorktree / ExitWorktree (create and remove a git worktree, then move the session into it) | `EnterWorktree`, `ExitWorktree` | uncovered | none | MEASURED (Enter, 1 call): created `.claude/worktrees/probewt`, no matcher fired. ExitWorktree removal is INFERRED from the tool's purpose. In a git install this makes a second copy of the install tree and moves the session's cwd into it (INFERRED: hook root resolves from the hook file location, so rules still bind the real install) |
| C10 | claude | DevBench MCP (live game: `console`, `game`, `papyrus`, `inspect`, ...) | `mcp__devbench-vr__console`, `mcp__devbench-vr__game`, `mcp__devbench-vr__papyrus` | partial | protect-mcp (S-DEVBENCH-CRASH deny, S-DEVBENCH-SAVE ask) | MEASURED (Plan 5 M5b): payload `mcp__devbench-vr__console`. READ (`mcp_rules.py`): only bare `smp reset` and a save over an existing name are judged. The `papyrus` tool and console `bat` files can do either thing without being named; an unreadable body (`-d @file`) is allowed. Also reached by curl and `devbench-cli.sh` through C1 (same judge) |
| C11 | claude | Any other MCP server's state-changing tools (Drive, Docs, Playwright navigation, ...) | `mcp__claude_ai_Google_Drive__authenticate`, `mcp__claude_ai_Claude_Docs__update` | uncovered | none (no matcher; stated by design) | READ: the settings carry no matcher for them. Mostly remote state, not the install |
| C12 | claude | MCP code-execution tools (run arbitrary JS or Node in a server) | `mcp__plugin_playwright_playwright__browser_run_code_unsafe`, `mcp__plugin_playwright_playwright__browser_evaluate` | uncovered | none | MEASURED in Plan 6 Phase A (`2026-10-04-plan6-m2.md`, on master): a Playwright code-execution call wrote a state file with Node `fs` while `--tools` restricted the built-in tools. **`--tools` does not restrict MCP tools.** Only a `*` matcher would see it. Whatever the machine allows an MCP server process to do, the agent can do |
| C13 | claude | MCP tools that write a file by design | `mcp__plugin_playwright_playwright__browser_take_screenshot`, `mcp__plugin_playwright_playwright__browser_file_upload` | uncovered | none | READ: screenshot takes a `filename`; INFERRED that the path is not checked by any toolkit guard |
| C14 | claude | Artifact tool, `read` with `path`/`out_dir` (saves a published file to local disk) | `Artifact` | uncovered | none | READ (tool description): default folder is the scratchpad; any other `out_dir` asks the user. The save is the harness's own approval, not a toolkit guard |
| C15 | claude | Reading and fetching tools | `Read`, `Glob`, `Grep`, `LSP`, `WebFetch`, `WebSearch`, `ToolSearch`, `Skill` | out-of-scope | n/a | READ: no local mutation. (A fetch can leak, which is not this table's question) |
| C16 | claude | `!` shell escape typed by the user in the TUI; the harness's own writes (transcripts, settings, memory) | `-` | out-of-scope | n/a | READ: user-initiated or the harness itself, not an agent route. Claude's own memory writes use the Write tool and are C5 |
| C17 | claude | Session launched with hooks off or untrusted (`--safe-mode`, `disableAllHooks`, project not trusted) | `-` | uncovered | none (hook-canary reports it after the fact) | READ: `--safe-mode` drops plugins and hooks (Plan 6 ML2 harness note). A launch-time choice by whoever starts the agent |
| C18 | claude | Self-modification of the guards (`.claude/settings.json`, hook files, `tools/skyguard/`) through Edit or Write | `Edit`, `Write` | partial | backup-before-edit only | INFERRED from READ of `write_rules.py`: the W- rules judge the install zone, not the toolkit's own files. Plan 6 adds self-protection |
| X1 | codex | Shell command (`exec_command`, PowerShell or bash) | `-` | guarded | `.codex/hooks/skyguard-entry` pre-tool, matcher `.*`, the same skyguard shell rules | READ (master `agent/targets/codex/hooks.json`). MEASURED Plan 4 E2E A4, A5: `Remove-Item` blocked, ReSaver apply refused. Codex cannot ask, so every ASK becomes a refusal. Blind spots as C1 |
| X2 | codex | `apply_patch` (Add, Update, Delete, Move) | `-` | guarded | pre-tool entry parses the patch; protect-files rules; backup of changed files | MEASURED Plan 4 (5 of 5 patch payloads; E2E A1 plugin deny, A2 advise plus backup) |
| X3 | codex | `apply_patch` run through the shell (`@'...'@ \| apply_patch`) | `-` | guarded | refused as unattributable (fail-by-deny) | MEASURED Plan 4 E2E A3: blocked, `unreadable patch`; not a path verdict |
| X4 | codex | MCP tools, DevBench included | `-` | uncovered | `.*` fires and the entry logs it; no rule judges it | MEASURED Plan 4 (toy server: PreToolUse fired with `mcp__probe__echo`). READ `agent/instructions/codex.md`: "Not checked: ... MCP tools (DevBench included)". The Claude C10 judge is not wired for Codex |
| X5 | codex | `!` user-shell commands in the Codex TUI | `-` | out-of-scope | n/a | MEASURED Plan 4 derisk: no PreToolUse fired. User-typed, not an agent path |
| X6 | codex | Codex launched where the hooks do not run: hooks not approved with `/hooks`; started in a subfolder of a non-git install | `-` | uncovered | `.codex/rules/skyrim.rules` prefix rules only (registry deletes) | MEASURED Plan 4 E2E: B2 (`Data/New.esp` created, hooks unapproved), R-subdir (2 of 2 runs, no hook line) and Plan 6 F6, F7 (written, no heartbeat). Unapproved hooks are skipped silently |
| O1 | any | Interpreter or opaque-script writes launched from a shell (`python -c`, `node -e`, `perl -i`, a script that writes a plugin) | `Bash`, `PowerShell` | partial | the shell guard sees the command text only | READ (R11, `writes.py` docstring): a plugin written by `open('X.esp','w')` gets no message; Plan 6's read-only lock is the backstop. Priced in Task 12: an opaque-script rule hit 41 of 65,694 items, 1 useful, dropped |
| O2 | any | Processes the agent starts that outlive the call (`Start-Process`, `nohup`, `&`, scheduled tasks) | `Bash`, `PowerShell` | partial | the launch is seen; the later writes are not | INFERRED: a hook runs per tool call and cannot see a detached child's later writes |
| O3 | any | Processes started outside the agent: the user's terminal, Vortex, the game, tools on a schedule | `-` | uncovered | none until Layer 2 (Plan 6, the read-only lock) | READ: `docs/superpowers/plans/2026-10-04-plan6-layer2-lock.md` on master. Not agent routes; listed because a lock, not a hook, is what stops them touching a protected file |

## Summary

Counts by status for the routes above (the test recomputes these and fails on a mismatch):

- guarded: 10
- partial: 4
- uncovered: 9
- absent: 1
- out-of-scope: 3

Ranked by what an agent can do through the route, the uncovered routes to close first:

1. **C12 MCP code execution** and **C11 other MCP**: only a `*` matcher sees them; the cheapest safe step is a
   `mcp__.*` matcher that logs, then an allow-list of servers the user trusts. Until then the MCP surface is the
   widest hole, and the Layer 2 lock is the only backstop.
2. **X6 Codex without active hooks** and **X4 Codex MCP**: no rule runs.
3. **C9 worktree tools**, **C13** and **C17**: lower impact; record and watch.
