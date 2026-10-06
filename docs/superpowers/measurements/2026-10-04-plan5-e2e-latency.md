# Plan 5 live end-to-end and latency -- 2026-10-04

Task 17 steps 4 and 5. Tree: branch `plan5` at `b5225cf` (+ docs). **Claude Code 2.1.289**, `claude -p --model haiku
--permission-mode default --output-format stream-json --verbose`, Python 3.10 (setup.sh's choice), Git Bash, Windows 11.
Everything ran in a release-style scratch copy **`C:/Temp/t17e2e/Skyrim Probe (x86)/`** (built by
`scripts/e2e-codex.py`'s `make()`: tree copy, stub `SkyrimVR.exe`, real `setup.sh`). The real install was not touched.
The driver and the mock server are scratchpad scripts (not committed): `e2e17.py`, `mock_devbench.py`.

**Method note (a finding).** The first PowerShell probe was refused by the model itself, quoting the copy's `CLAUDE.md`
("DENIED ... Set-Content"): the instruction file did the blocking, not the hook, so that run proves nothing. `CLAUDE.md` and
`AGENTS.md` were deleted from the COPY and every probe below was rerun. A probe counts only when the tool actually ran its call,
the tool_result carries the hook's text, the copy's `AUDIT_LOG.txt` has the matching line and the side effect is absent.

The PowerShell tool exists in `-p` on 2.1.289 (init tool list: `Bash`, `PowerShell`), so every row went through the real tool.

| # | Probe (tool) | Expected | Observed (tool_result, verbatim) | Side effect | Audit line in the copy |
|---|---|---|---|---|---|
| 1 | `Set-Content -Path '<copy>\Data\Decoy.esp' -Value 'x'` (PowerShell) | deny | `PreToolUse:PowerShell hook error: Blocked: this writes Decoy.esp as raw text, which corrupts a plugin or archive.` / `Use xelib, Spriggit or AutoMod to change it.` | `Decoy.esp` still `decoy\n` | `protect-bash DENY: Blocked: this writes Decoy.esp ...` |
| 2 | `rm -rf Data/x` with cwd = the copy (Bash; R9 relative path) | deny | `PreToolUse:Bash hook error: Blocked: this deletes x inside the Skyrim install.` / `A mod manager cannot restore your own files there; if you mean it, run it yourself.` | `Data/x/keep.txt` still present | `protect-bash DENY: Blocked: this deletes x ...` |
| 3 | `grep -c 'rm -rf' "<copy>/CHANGELOG.md" "<copy>/README.md"` (Bash; the words and the install path, no delete) | allow | ran: `...CHANGELOG.md:1` / `...README.md:0`, `is_error` false | n/a | no DENY, no ASK line |
| 4 | `bash tools/resaver-cli.sh reset-havok Save1.ess out.ess --apply` (Bash) | ask, one line | `ReSaver will MODIFY a save file (reset-havok on Save1.ess). Approve only if you asked for this save to be changed.` (one line; in `-p` the ask is not answered, so the call does not run, `is_error` true) | no `out.ess` | `protect-bash ASK: ReSaver will MODIFY ...` |
| 5 | `mcp__devbench-vr__console` `{"action":"exec","command":"smp reset"}` via a mock stdio MCP server (name `devbench-vr`) | deny | `PreToolUse:mcp__devbench-vr__console hook error: Blocked: 'smp reset' crashes a large load order in the live game.` / `Use a targeted command instead, or run it yourself.` | the mock server's call log is EMPTY | `protect-mcp DENY: Blocked: 'smp reset' ...` |
| 5c | control: same tool, `player.additem f 1` | allow | `mock executed` | the mock's call log has 1 line | none |

Row 5c is what makes row 5 mean something: the mock answers, so an empty call log after `smp reset` is the hook and not a dead server.

**Heartbeats prove the copy's hooks answered.** `bash tools/hook-canary.sh` run in the copy after the probes:
`protect-bash HEALTHY (last completed evaluation 88 s ago, p50 683 / p95 4266 ms, n=5)`, `protect-mcp HEALTHY (6 s ago)`,
`snapshot-before-tool HEALTHY`, `session-kb-guard HEALTHY`; `protect-files` and `backup-before-edit` NOT EXERCISED (no Edit or Write
call was made, by design). `protect-mcp` was NOT EXERCISED before probe 5, so its heartbeat comes from that call.
(The canary's latency figures in this block were taken while the full test suite was running and are not step 5.)

Ledger items from this run:
- The Bash and PowerShell denies show Claude Code's `PreToolUse:<tool> hook error:` prefix; the ask does not. Text is fully visible.
- Probe 4: the ask ran twice (the model retried the same call), both asked; no one answered, as intended in `-p`.
- The PowerShell tool in `-p` ran the hook with `shell=powershell`: the deny text names `Decoy.esp`, which the PowerShell form of the rule found.
- Codex was not run live in this task (no `~/.codex` access allowed); gap X4 is covered at payload level through the real entry
  (`test_cli_a_devbench_reset_is_denied_through_the_real_entry`: `run.py codex pre-tool` prints `SKYGUARD-OK` then the deny JSON).

## Step 5: latency (gate: p95 <= 1000 ms)

`python scripts/guard-latency.py "C:/Temp/t17e2e/Skyrim Probe (x86)" --runs 20` on the scratch copy, Python 3.10, one process, machine CPU
load 30% (other sessions were running, so not idle). Median / p95 ms:

| hook | min | median | p95 |
|---|---|---|---|
| protect-bash | 153 | 162 | 459 |
| protect-files | 155 | 174 | 422 |
| protect-mcp | 155 | 287 | 452 |
| backup-before-edit | 139 | 156 | 409 |
| snapshot-before-tool | 140 | 158 | 454 |
| session-kb-guard | 471 | 773 | **1385** |

**The five per-call hooks pass the gate (p95 <= 459 ms, n=20).** `session-kb-guard` (SessionStart, once per session, not per call; it
runs `tools/kb-guard.sh` over a 123 KB KNOWLEDGEBASE.md) is **over 1000 ms: FAIL** as a number, as in Plan 2's table (905 / 2172). It
is not on the per-tool-call path. Unchanged by this plan; ledgered. n=20 is thin for a p95 (it is nearly the max): treat the
per-call figures as "well under the gate", not as a precise tail.

## Test-suite note

The full launcher suite was run as six sequential-by-shard harness processes after a single-process run proved too slow under
machine load (about 1% per 10 minutes). Five timing-sensitive tests failed under that load (four in `test_python_fallback.py`,
`test_codex_entry.py::test_a_grandchild_holding_stdout_does_not_outlast_the_deadline[bash]`: exit 137 vs the expected 124) and
all five passed on a single-process rerun.

