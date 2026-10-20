> Evidence from the toolkit's development. Not instructions for your agent.

# Plan 8 T14b: hook latency and deadline matrix

Measured 2026-10-14 and 2026-10-15 on one machine (a desktop CPU with 28 logical CPUs; Git 2.55.0.windows.5,
bash 5.3.15 (cygwin), harness Python 3.10.10, Windows PowerShell 5.1.26100.9549, pwsh 7.6.6).
Every number below is MEASURED by `tests/test_hook_deadline_load.py` (opt-in, `SKYGUARD_LOAD_TEST=1`) and copied from its run rows; the rows
and logs stay outside the repository (they hold machine paths). Tiers: MEASURED unless a sentence says INFERRED.

## What was run

- **Hooks (9):** the six Claude wrappers (protect-bash, protect-files, protect-mcp, backup-before-edit, snapshot-before-tool, session-kb-guard),
  the Codex bash entry, the Codex Windows PowerShell 5.1 entry and the pwsh entry.
- **Scenarios (5):** healthy; fallback (configured Python missing); engine failure; hung engine before `run.py` arms its watchdog; hung engine after.
  Every failure scenario is a mutation of a **scratch install**, never a switch in the product.
- **Load:** CPU burners pinned by affinity to 4 of the 28 logical CPUs (`SKYGUARD_LOAD_CORES`), at **normal** priority (user-approved for this
  run); 1x = 1 burner per pinned core, 2x = 2, 4x = 4. Burners are stopped by their own handles. The machine as a whole lost at most 4 CPUs.
  Runs per scenario: 5 at idle, 1x and 4x; 10 at 2x. A run is timed from spawn to the later of the entry's exit and its stdout closing.
- **Pass:** a scenario's worst run is under the definition timeout minus 10 s (50 s for PreToolUse hooks, 110 s for SessionStart); no gate hook
  may allow or answer after its timeout where a refusal was expected (a late answer counts as a failure). The entries' own outer deadline is the
  timeout minus 15 s, so 45-50 s is a margin warning.
- **Red first:** the same matrix against the pre-fix tree `544efd4` (a scratch clone). **Green:** the candidate tree. A stage interrupted by the game
  starting was discarded and rerun from the start (the harness stops its own process tree by PID when `SkyrimVR.exe` appears).

## Result

| | Runs | Scenarios | Late runs | Runs that failed open | Worst run |
|---|---|---|---|---|---|
| **Pre-fix `544efd4`, idle** | 213 | 45 | 18 | 9 | 140.0 s (capped) |
| **Pre-fix, 1x** | 213 | 45 | 18 | 9 | 140.0 s (capped) |
| **Pre-fix, 2x** | 408 | 45 | 18 | 9 | 140.0 s (capped) |
| **Candidate, idle** | 225 | 45 | 0 | 0 | 40.8 s |
| **Candidate, 1x** | 225 | 45 | 0 | 0 | 43.5 s |
| **Candidate, 2x** | 450 | 45 | 0 | 0 | 42.2 s |
| **Candidate, 4x** (original run, before the clock fix; see the Codex PowerShell note) | 230 | 45 | 1 | 0 | 51.3 s |

### Red: the pre-fix tree fails open when a hook hangs before its own timer starts

Six of 45 scenarios were red, **the same six at idle, 1x and 2x**: `hang-pre-watchdog` on all six Claude wrappers. No run ended before the 80 s
harness cap (140 s for session-kb-guard, whose definition timeout is 120 s). For the three gate hooks (protect-bash, protect-files, protect-mcp)
the agent had already given up at the 60 s timeout, so the command ran unchecked: **9 of 9 runs failed open at every load level**. In 8 of those 9 at
2x the hook printed nothing at all; in the ninth it printed a refusal after the agent had moved on. The two side hooks (backup-before-edit, snapshot-before-tool) and
session-kb-guard were late but are not gates, so no command was affected. Because it reproduces with the machine idle, this is a plain defect, not a load effect.
None of the Codex entries went red on the pre-fix tree.

### Green: the candidate passes everywhere except one Codex PowerShell cell, now fixed

At idle, 1x and 2x all 45 scenarios passed with no late run (worst 40.8 s, 43.5 s, 42.2 s). At 4x all six Claude hooks passed (worst 39.5 s) and
Codex bash and pwsh passed. **One cell failed at 4x: `codex:powershell` `hang-pre-watchdog`, worst 51.3 s against the 50 s line.** The answer was
still a refusal (never an allow), but it arrived inside the margin. Cause (READ and reproduced): the entry took its deadline clock at the script's first
line, and under load Windows PowerShell 5.1 spent seconds starting before that line. Fix `a43abcc`: the clock starts at the process's own start time
(never later than now, and ignored if the read fails or the value is implausible). The new test failed first (the clock started 8.3 s after the spawn)
and its mutant is killed. After the fix: 20 runs, worst 48.4 s, late 0, failed open 0 (both PowerShell entries, `hang-pre-watchdog`, 4x).
Full 4x rerun of both PowerShell entries on the merged tree (all five scenarios): 50 runs, 10 scenarios, worst 48.9 s, late 0, failed open 0.

**Scope of the fix verification:** the idle, 1x, 2x and the other 4x cells above ran on `0871924` (master plus the test file), which does not contain
`a43abcc`. The clock change affects only the Codex PowerShell entry, so only the two PowerShell entries were re-measured on the fixed tree.

## Where the red run does not reproduce the investigation

The investigation behind this gate reported a hung Codex bash engine at 120-162 s and a coreutils bound failing to kill a native `python.exe`
under load in 18-227 s. Neither reproduced with this method: on the pre-fix tree every Codex scenario passed, and the kill-latency probe below ended in
3.4-10.6 s at 2x, 4x and 8x. The Git 2.55 / MSYS update since the investigation may be why (INFERRED, not tested). The gate does prove the defect class
above (a hang before the timer arms) and does stay green on the candidate; it does not claim to reproduce those two specific numbers.

## Kill-latency probe (a 3 s bound on a native sleeper, must end in under 6 s)

| Tree | Load | bash | PowerShell 5.1 | pwsh |
|---|---|---|---|---|
| Pre-fix | 2x | 3.4-4.2 s (n=10) | 3.7-4.6 s (n=10) | 3.9-5.4 s (n=10) |
| Pre-fix | 4x | 3.8-6.5 s (n=10) | 3.7-6.2 s (n=10) | 4.2-8.4 s (n=10) |
| Pre-fix | 8x | 3.6-10.6 s (n=10) | 4.2-8.5 s (n=10) | 5.0-8.8 s (n=10) |
| Candidate | 4x | 3.8-7.6 s (n=10) | 4.5-7.4 s (n=10) | 4.8-9.3 s (n=10) |

The probe is **not a discriminator**: the pre-fix tree passes it at 2x, and both trees exceed 6 s at 4x, because the time is dominated by process start-up
under load rather than by the bound. (The candidate was probed at 4x only.) It is kept as a record, not as a gate. The scenarios above are the gate.

## Idle speed, candidate against v3.9.1 (interleaved, 40 runs per hook and side, 560 timed runs, machine at most 19% busy)

| Hook | Candidate median | Candidate p95 | Candidate max | v3.9.1 p95 |
|---|---|---|---|---|
| claude:protect-bash | 165 ms | 241 ms | 642 ms | 334 ms |
| claude:protect-files | 172 ms | 223 ms | 231 ms | 200 ms |
| claude:protect-mcp | 169 ms | 233 ms | 240 ms | no baseline |
| claude:backup-before-edit | 181 ms | 221 ms | 249 ms | 335 ms |
| claude:snapshot-before-tool | 173 ms | 222 ms | 252 ms | 224 ms |
| claude:session-kb-guard | 418 ms | 482 ms | 490 ms | 450 ms |
| codex:bash | 178 ms | 241 ms | 588 ms | no baseline |
| codex:powershell | 372 ms | 409 ms | 433 ms | no baseline |
| codex:pwsh | 402 ms | 443 ms | 469 ms | no baseline |

Every p95 is under the 1 s bar. v3.9.1 shipped no Codex hooks and no protect-mcp, so those rows have no baseline. **session-kb-guard p95 at idle is
the number Plan 6 owed** (its earlier 9,221 ms figure was measured under load and was not repeated here).

## Full matrices (worst run per scenario, seconds; `+` = ended at the harness cap; `**OPEN**` = failed open; `late` = past timeout minus 10 s)

### Pre-fix `544efd4`

| Hook | Scenario | idle | 1x | 2x |
|---|---|---|---|---|
| claude:protect-bash | healthy | 0.6 | 1.7 | 5.9 |
| claude:protect-bash | fallback | 1.2 | 6.9 | 10.1 |
| claude:protect-bash | engine-fail | 0.9 | 3.8 | 9.7 |
| claude:protect-bash | hang-pre-watchdog | 80.0+ **OPEN** | 80.0+ **OPEN** | 80.0+ **OPEN** |
| claude:protect-bash | hang-post-watchdog | 20.6 | 21.7 | 24.1 |
| claude:protect-files | healthy | 0.5 | 1.1 | 1.9 |
| claude:protect-files | fallback | 1.2 | 4.2 | 3.2 |
| claude:protect-files | engine-fail | 0.8 | 3.0 | 3.6 |
| claude:protect-files | hang-pre-watchdog | 80.0+ **OPEN** | 80.0+ **OPEN** | 80.0+ **OPEN** |
| claude:protect-files | hang-post-watchdog | 20.6 | 21.9 | 22.1 |
| claude:protect-mcp | healthy | 0.6 | 3.0 | 1.6 |
| claude:protect-mcp | fallback | 1.2 | 4.9 | 3.0 |
| claude:protect-mcp | engine-fail | 0.9 | 5.0 | 2.7 |
| claude:protect-mcp | hang-pre-watchdog | 80.0+ **OPEN** | 80.0+ **OPEN** | 80.0+ **OPEN** |
| claude:protect-mcp | hang-post-watchdog | 20.7 | 22.6 | 23.8 |
| claude:backup-before-edit | healthy | 0.5 | 1.4 | 2.4 |
| claude:backup-before-edit | fallback | 1.3 | 6.6 | 3.3 |
| claude:backup-before-edit | engine-fail | 0.9 | 2.3 | 4.3 |
| claude:backup-before-edit | hang-pre-watchdog | 80.0+ late | 80.0+ late | 80.0+ late |
| claude:backup-before-edit | hang-post-watchdog | 20.6 | 22.3 | 22.2 |
| claude:snapshot-before-tool | healthy | 0.6 | 2.0 | 2.3 |
| claude:snapshot-before-tool | fallback | 1.2 | 4.6 | 3.3 |
| claude:snapshot-before-tool | engine-fail | 0.8 | 2.8 | 3.0 |
| claude:snapshot-before-tool | hang-pre-watchdog | 80.0+ late | 80.0+ late | 80.0+ late |
| claude:snapshot-before-tool | hang-post-watchdog | 20.7 | 22.8 | 22.9 |
| claude:session-kb-guard | healthy | 1.1 | 4.0 | 4.0 |
| claude:session-kb-guard | fallback | 1.7 | 7.9 | 4.0 |
| claude:session-kb-guard | engine-fail | 0.8 | 2.8 | 3.9 |
| claude:session-kb-guard | hang-pre-watchdog | 140.0+ late | 140.0+ late | 140.0+ late |
| claude:session-kb-guard | hang-post-watchdog | 20.6 | 22.4 | 22.0 |
| codex:bash | healthy | 0.7 | 2.5 | 2.0 |
| codex:bash | fallback | 1.3 | 6.0 | 3.6 |
| codex:bash | engine-fail | 0.8 | 4.5 | 3.9 |
| codex:bash | hang-pre-watchdog | 40.4 | 42.6 | 40.9 |
| codex:bash | hang-post-watchdog | 20.6 | 23.7 | 23.4 |
| codex:powershell | healthy | 1.0 | 2.3 | 2.4 |
| codex:powershell | fallback | 1.5 | 9.9 | 3.1 |
| codex:powershell | engine-fail | 1.1 | 5.7 | 3.3 |
| codex:powershell | hang-pre-watchdog | 40.7 | 47.9 | 41.6 |
| codex:powershell | hang-post-watchdog | 21.0 | 23.1 | 22.6 |
| codex:pwsh | healthy | 1.0 | 5.4 | 2.7 |
| codex:pwsh | fallback | 1.7 | 8.6 | 3.7 |
| codex:pwsh | engine-fail | 1.2 | 4.2 | 3.6 |
| codex:pwsh | hang-pre-watchdog | 40.7 | 42.9 | 42.1 |
| codex:pwsh | hang-post-watchdog | 21.1 | 23.3 | 22.7 |


### Candidate (4x for the two PowerShell entries is the fixed entry, the rest is `0871924`)

| Hook | Scenario | idle | 1x | 2x | 4x |
|---|---|---|---|---|---|
| claude:protect-bash | healthy | 0.9 | 1.6 | 8.0 | 6.7 |
| claude:protect-bash | fallback | 1.5 | 2.8 | 13.5 | 17.6 |
| claude:protect-bash | engine-fail | 1.2 | 2.9 | 12.2 | 25.6 |
| claude:protect-bash | hang-pre-watchdog | 30.5 | 31.6 | 33.7 | 35.6 |
| claude:protect-bash | hang-post-watchdog | 20.8 | 22.3 | 29.0 | 30.7 |
| claude:protect-files | healthy | 0.9 | 2.1 | 11.4 | 11.8 |
| claude:protect-files | fallback | 1.4 | 2.9 | 12.3 | 26.8 |
| claude:protect-files | engine-fail | 1.1 | 2.9 | 13.4 | 20.6 |
| claude:protect-files | hang-pre-watchdog | 30.6 | 31.5 | 35.5 | 39.3 |
| claude:protect-files | hang-post-watchdog | 20.8 | 22.3 | 27.4 | 32.2 |
| claude:protect-mcp | healthy | 0.9 | 2.4 | 9.5 | 8.7 |
| claude:protect-mcp | fallback | 1.4 | 2.5 | 12.3 | 31.9 |
| claude:protect-mcp | engine-fail | 1.2 | 3.4 | 11.6 | 22.4 |
| claude:protect-mcp | hang-pre-watchdog | 30.5 | 31.3 | 34.4 | 37.5 |
| claude:protect-mcp | hang-post-watchdog | 21.0 | 21.8 | 28.9 | 38.3 |
| claude:backup-before-edit | healthy | 4.0 | 2.1 | 2.3 | 14.8 |
| claude:backup-before-edit | fallback | 1.7 | 2.8 | 2.7 | 20.8 |
| claude:backup-before-edit | engine-fail | 1.1 | 2.9 | 4.0 | 17.8 |
| claude:backup-before-edit | hang-pre-watchdog | 30.9 | 31.3 | 31.5 | 36.9 |
| claude:backup-before-edit | hang-post-watchdog | 21.0 | 22.5 | 23.3 | 31.8 |
| claude:snapshot-before-tool | healthy | 1.0 | 2.0 | 2.7 | 11.5 |
| claude:snapshot-before-tool | fallback | 1.6 | 2.7 | 3.9 | 25.5 |
| claude:snapshot-before-tool | engine-fail | 1.2 | 3.7 | 4.8 | 19.0 |
| claude:snapshot-before-tool | hang-pre-watchdog | 30.6 | 31.4 | 31.6 | 39.5 |
| claude:snapshot-before-tool | hang-post-watchdog | 20.9 | 22.7 | 24.6 | 36.6 |
| claude:session-kb-guard | healthy | 1.5 | 3.3 | 4.4 | 22.7 |
| claude:session-kb-guard | fallback | 2.1 | 3.7 | 4.5 | 13.3 |
| claude:session-kb-guard | engine-fail | 1.1 | 2.9 | 3.6 | 20.4 |
| claude:session-kb-guard | hang-pre-watchdog | 30.7 | 31.7 | 31.4 | 37.0 |
| claude:session-kb-guard | hang-post-watchdog | 20.8 | 26.2 | 22.0 | 33.5 |
| codex:bash | healthy | 0.9 | 5.3 | 1.6 | 14.1 |
| codex:bash | fallback | 1.5 | 7.1 | 2.8 | 15.0 |
| codex:bash | engine-fail | 1.2 | 11.2 | 5.3 | 17.0 |
| codex:bash | hang-pre-watchdog | 40.5 | 41.4 | 41.0 | 43.3 |
| codex:bash | hang-post-watchdog | 20.8 | 25.0 | 22.0 | 35.6 |
| codex:powershell | healthy | 1.3 | 11.2 | 3.1 | 20.2 |
| codex:powershell | fallback | 2.4 | 7.8 | 4.0 | 27.8 |
| codex:powershell | engine-fail | 1.6 | 9.0 | 4.8 | 25.9 |
| codex:powershell | hang-pre-watchdog | 40.7 | 42.9 | 41.2 | 45.4 |
| codex:powershell | hang-post-watchdog | 21.6 | 29.7 | 24.0 | 37.4 |
| codex:pwsh | healthy | 1.4 | 7.3 | 4.2 | 18.5 |
| codex:pwsh | fallback | 2.1 | 9.2 | 4.0 | 20.8 |
| codex:pwsh | engine-fail | 1.7 | 10.2 | 5.1 | 33.7 |
| codex:pwsh | hang-pre-watchdog | 40.8 | 43.5 | 42.2 | 48.9 |
| codex:pwsh | hang-post-watchdog | 21.8 | 32.6 | 23.5 | 41.2 |


## Limits

- One machine, one OS, burners pinned to 4 cores by affinity; not a population. N is 5-10 per scenario, so "worst" is the worst of N, not a true maximum.
- Load was CPU only; memory and disk pressure were not generated.
- Linux, macOS and the other Windows legs run in CI, not here.
- Tightest cell on the candidate at 4x, with the PowerShell entries on the fixed entry: codex:pwsh hang-pre-watchdog at 48.9 s. The margin line is 50 s.
- The game starting interrupted the harness several times; interrupted attempts were discarded and rerun, never merged.
- The pricing, mutation and full-suite gates are separate; see the readiness report.
