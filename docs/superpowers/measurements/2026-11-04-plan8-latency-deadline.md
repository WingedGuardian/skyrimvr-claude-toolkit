# Plan 8 T14b: hook latency and deadline matrix, final candidate

Date 2026-11-04. Tree: `0a3767e`, frozen worktree; the release candidate `afdaf16` changes only tests and the CHANGELOG after it. Windows 11, i7-14700F, 28 logical CPUs, Git 2.55.0.5, Python 3.10.10, PowerShell 5.1 and 7.6. Idle priority, 4 pinned cores. Other sessions were running on the machine throughout, so the load is at least what is stated.

Tests: `tests/test_hook_deadline_load.py`, switched on with `SKYGUARD_LOAD_TEST`, `_MATRIX`, `_PROBE` and `_AB`. MEASURED throughout.

## Matrix: 9 entries, 5 paths, 3 load levels

| Load | Burners | Cells | Runs | Worst | Failed open | Late | Wrong answer |
|---|---|---|---|---|---|---|---|
| 0x | 0 | 45 | 225 | 42.4 s | 0 | 0 | 0 |
| 1x | 4 | 45 | 230 | 43.2 s | 0 | 0 | 1 |
| 2x | 8 | 45 | 450 | 41.4 s | 0 | 0 | 0 |

Limits: 60 s definition timeout, 120 s for `session-kb-guard`; a run is late at 10 s before its limit.

The first matrix run was stopped at 81 cells by the runner's disk floor, and the rest ran afterwards. Three 1x cells had skipped themselves in that run because other sessions had the machine busy, and all three ran in the second. The one wrong answer: `claude:session-kb-guard`, `hang-pre-watchdog`, 1x, run 1 exited silently with code 0x80000004 in 31.9 s at about 74% machine load, where the expected answer is its NOT RUN notice. The rerun of that cell was correct every time. Across both runs of the cell that is 1 wrong answer. The entry blocks nothing, and the cause is unknown. Known limit in the CHANGELOG.

## Hung-hook runs at 16 burners

35 runs across 7 entry and scenario pairs, refused: 35. Worst 42.6 s.

## Kill latency at 16 burners

A bound of 3 s on a native sleeper, 10 runs per shell, all timed out and returned. Worst inner time:

- bash: 4.14 s
- powershell: 4.40 s
- pwsh: 4.50 s

## Hot path against v3.9.1

Healthy path, unpinned, no burners, 40 interleaved runs per entry. The bar is p95 at most 1000 ms on v4.0. The machine was not idle: about 38% busy in the median. v3.9.1 has no `protect-mcp` here and no Codex entries.

| Entry | v4.0 median | v4.0 p95 | v3.9.1 median | v3.9.1 p95 |
|---|---|---|---|---|
| claude:backup-before-edit | 266.0 ms | 346.0 ms | 294.0 ms | 327.0 ms |
| claude:protect-bash | 358.0 ms | 443.0 ms | 278.0 ms | 314.0 ms |
| claude:protect-files | 341.0 ms | 443.0 ms | 172.0 ms | 218.0 ms |
| claude:protect-mcp | 339.0 ms | 408.0 ms | - | - |
| claude:session-kb-guard | 653.0 ms | 776.0 ms | 130.0 ms | 164.0 ms |
| claude:snapshot-before-tool | 269.0 ms | 320.0 ms | 197.0 ms | 226.0 ms |
| codex:bash | 310.0 ms | 369.0 ms | - | - |
| codex:powershell | 538.0 ms | 625.0 ms | - | - |
| codex:pwsh | 574.0 ms | 637.0 ms | - | - |

All 9 entries meet the bar. v4.0 is slower on most hot paths, and `session-kb-guard` runs once per session.
