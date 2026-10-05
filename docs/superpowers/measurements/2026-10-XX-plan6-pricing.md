# Plan 6 pricing and baseline record

- **Base sha:** `544efd4` (branch `plan6`, cut from `plan5`; HEAD at Task 6 start). Worktree `../skyrimvr-toolkit-plan6`.
- **Claude Code / machine:** shared machine, other sessions active (sampled CPU 45% during the latency run).

## Preconditions (checked 2026-10-04)

| Precondition | State |
|---|---|
| Plan 4 in master | yes (`git merge-base --is-ancestor plan4 master`) |
| Plan 5 in master | **NO** -- `plan5` is not yet an ancestor of `master`. plan6 was cut from `plan5` (544efd4) by ledger ruling (saves the Task 17 wait); it must merge after plan5, with clean ancestry. |
| `price.py` and `intended.jsonl` | `scripts/guard-corpus/price.py` and `tests/guard_corpus/intended.jsonl` both present. |
| Plan 5 Task 16 numbers | exist (commit `9391c58`, section "Inputs for the side-effects plan" in `2026-10-XX-plan5-pricing.md`); cited below, not re-measured |
| Task 5 committed (mechanism decision) | yes (`8284208`; user ruling in the ledger: M-D, default OFF, R3) |
| No heavy run in progress | the two runs below were sequential; other agents' sessions were active |

## Side-effect inputs (cited from Plan 5 Task 16, `9391c58`; MEASURED there 2026-10-04, read-only on the real install)

- Scripts, both source layouts, top-level vs nested: **11,898 files / 36,726,229 bytes**. `Data/Scripts` top-level `.pex` 6,336
  (17.6 MB, 98.9% older than 7 d); top-level `.psc` 7; nested `.pex` 17; `Data/Scripts/Source` top-level `.psc` 2,134
  (8.9 MB, 100% older), nested `.psc` 82; `Data/Source/Scripts` top-level `.psc` 3,322 (9.65 MB, 98.3% older), nested 0.
- The snapshot hook sees 8,470 files and copies only those younger than 7 days (0 `.psc`, ~68 `.pex` per snapshot); **3,428 files
  (29%) are never seen**.
- Per-call snapshot time (in-process, scratch copy): 313-320 ms (cold first 690 ms) when copying; **72-74 ms** inside the 60 s
  rate limit; worst case all-fresh 3.1-3.2 s (48.6 s cold).
- Backups: **863** per-edit backups; **1 of 863** has no recoverable original path (name lossy for all: `/ \ :` -> `_`, L8); 14
  `FAILED` + 3 `BLIND` audit lines; `auto_snapshots/` 1,109 dirs, 72,360 files, 2.82 GB, of which 89 distinct contents = 2.76 MB.

Not re-measured: Task 6's brief asks for exactly these figures and Task 16 provides all of them.

## Baseline pricing (`price.py --base 544efd4`, engine vs itself, run 2026-10-04)

Raw items stay outside the repo (`C:/Temp/plan6-t6-price`); counts only here.

- **N = 66,791** unique items (Bash 59,552; Write 3,846; Edit 1,726; PowerShell 1,662; mcp 5). Raw calls: Bash 61,723; Write 4,810;
  Edit 9,197; PowerShell 1,714; mcp devbench 8.
- Decision matrix (old>new): allow>allow 66,261; advise>advise 211; deny>deny 307; ask>ask 12.
- **Every `newly-*` / `no-longer-*` direction = 0 (zero diff against itself), `reason_moved` 0, engine errors 0.**
  Parse failures 63 old / 63 new (Bash 61 of 59,552 = 0.102%; PowerShell 2 of 1,662 = 0.120%).
- N grew from Plan 5's 58,558 because the history grew; the advise cell (211 vs 2,340 at Plan 5's base) moved because the
  rules changed between the two bases, not because of this run.

## Baseline latency (`scripts/guard-latency.py`, 50 runs per wrapper, 2026-10-04)

Configured copy: `git archive HEAD` of this worktree into `C:/Temp/plan6-lat`, a zero-byte `SkyrimVR.exe`, `setup.sh` run. Machine
shared, sampled CPU load 45% (other agents running): **not an idle-machine run**, and the gate is stated against an idle one.

| Wrapper | min | median | p95 | max (ms) |
|---|---|---|---|---|
| protect-bash | 154 | 171 | 416 | 647 |
| protect-files | 154 | 346 | 482 | 516 |
| protect-mcp | 154 | 389 | 480 | 699 |
| backup-before-edit | 141 | 171 | 437 | 625 |
| snapshot-before-tool | 138 | 157 | 391 | 401 |
| session-kb-guard | 528 | 1,421 | **9,221** | 12,846 |

Gate (every wrapper p95 <= 1 s): FAIL, on `session-kb-guard` alone; the five per-call wrappers are within the gate. Plan 5's record
shows the same wrapper failing under load (838 / 1,011 / 2,113 ms) and a run here is ~4x worse at 45% load, so this is a baseline
for Plan 6's later comparison, not a verdict. `session-kb-guard` runs once per session, not per tool call. The idle re-run is owed
at Task 18. Task 10 adds work to session start, so this wrapper is the one to watch.
