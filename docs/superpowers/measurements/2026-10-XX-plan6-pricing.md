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

## Task 12: agent self-protection (S/W-SELF-STATE, S/W-SELF-GUARD) and the Task 11 review fixes

`price.py --base 7c034b9` (Task 11's commit), run 2026-10-05, one process, machine shared. Raw items stay outside the
repo (`C:/Temp/p6t12-price-{a,b,c}`); counts only here.

**Part A (Task 11 review fixes + Rulings: per-statement construct targets, upstream-only pipeline peers, Data
listings, cc* globs, `--help`, cacls/takeown, interpreter code, variable-only cmdlet paths).** N = 70,313. Every
`newly-*` / `no-longer-*` direction = 0; errors 0; parse failures 64 / 64.

**Part B, priced as ASK first (the pre-committed rule needs the ask count).** N = 70,342: newly-deny 1 (S-SELF-STATE),
newly-ask 24 (S-SELF-GUARD 11, W-SELF-GUARD 13), nothing else moved; errors 0; parse failures 64 / 64.

Every hit read by hand (MEASURED: the command; INFERRED: who asked for it, from the nearest preceding user message
in the same transcript, found by matching the exact tool input):

- **S-SELF-STATE, 1 newly-deny.** A Plan 6 Phase A measurement agent building a scratch install
  (`R=C:/Temp/skyguard-ml2/...; cd $R && ... echo '{}' > .skyguard/state.json`). The parser marks the cwd after
  `cd $R` unknowable, so the relative `.skyguard/state.json` is unresolved and fails closed. A false positive on scratch
  work; writing the path absolute avoids it. 1 <= 20, every one read: **S-SELF-STATE ships as deny.**
- **S-SELF-GUARD, 11 newly-ask.** All hook maintenance of the live install: staging `protect-*.sh.new` copies inside
  `.claude/hooks`, swapping them in with `mv -f`, copying staged hooks from the toolkit checkout. Preceding user
  messages: "ok proceed now", "ok phase 1 is ready", the user's own complaint about hook prompts, and task
  notifications inside sessions the user opened for that hook work. 11 of 11 user-requested maintenance.
- **W-SELF-GUARD, 13 newly-ask.** 9 Edit/Write on `.claude/hooks/protect-*.sh(.new)`, 1 Write of a
  `_diag-capture.sh` diagnostic hook, 1 Edit of `.claude/settings.json` (after "ok do it"), and 2 Edits of the
  user-level `~/.claude/settings.json` from an X4 session (after "Yes, set to 365 days"). 13 of 13 user-requested.

**Ruling: S-SELF-GUARD / W-SELF-GUARD ship as ADVISE.** Pre-committed rule: ask only if the hand-read sample shows
<= 25 % user-requested maintenance AND <= 50 newly asked. Measured: 24 newly asked (passes the count) but **24 of 24 =
100 %** user-requested maintenance (fails the share), so advise. Cost if wrong: an agent that edits a hook on its own
gets a note, not a prompt; the session-start `GUARD-FILES.sha256` banner (shipped either way) names the changed file
in the next session. (Corrected in Task 12 fix round 1: as first shipped, the manifest held the hashes of the files with
setup.sh's placeholders still in them, so every configured install bannered 8 files every session and an edit hid
among them. The hash is now taken on the repo form -- the interpreter slot lines setup.sh writes are put back to their
tokens, exact line shape only, and the interpreter values compared across the files -- so a configured install is
silent and only a real edit is named, MEASURED with the real setup.sh on a scratch install. Residue: one identical
edit to all eight interpreter lines is not seen by the banner; the per-call note still is.) The ask text stays recorded beside the registry entry.

**Part B as shipped (advise).** Run on the committed engine (`b721468`; the history grew between runs): N = 70,752 (matrix: allow>allow 70,177; advise>advise 214; allow>advise 24; deny>deny 323; ask>ask 13; allow>deny 1): newly-deny 1 (the scratch item above), newly-advise 24 (the same 24 items,
checked identical as a set), newly-ask 0, no-longer-* 0, errors 0, parse failures 64 / 64.

## Task 14: snapshot latency gate (nested .psc/.pex, content-addressed store)

**Rule (pre-committed, with the controller's reading from fix round 1).** Gate: the snapshot hook's added per-call p95.
- At or below 150 ms, the store ships with a 60 s rate limit.
- From 150 to 400 ms, the rate limit becomes 300 s and the walk is skipped while folder mtimes are unchanged.
- Above 400 ms, the per-call walk is disabled.

The controller reads "per call" as the p95 over a realistic call stream, derived from real history. The cost driver that
crosses 150 ms is the out-of-window comparison. Branch 2 does not make that comparison cheaper; it only makes it
rarer, and it gives up detecting an in-place `.pex` overwrite inside the window.

**Shape costs.** Measured in-process with `snapshot.take` on 2026-10-05, n = 200 per shape; CPU load was 5–16%.
- The synthetic tree uses the Task 6 counts: 11,898 files with 3,086-byte bodies, 36.7 MB in total, the size measured
  on the install.
- Hard links come from a staging folder. The live install was checked read-only (stat only): **3,049 of its 11,899 scripts
  are multi-link**.
- The trees were about 77 MB each. They were built in the scratch folder and sent to the Recycle Bin afterwards.

| Tree | in window p95 | outside, unchanged p95 | outside, one `.psc` changed p95 | cold baseline |
|---|---|---|---|---|
| 3,049 hard-linked (as the live install) | 33.0 | 127.5 | **167.7** | 100.2 s |
| all 11,898 hard-linked (re-stat capped at 75 ms per comparison) | 27.2 | 124.5 | **160.0** | 111.0 s |

- **Re-stat cost.** Without the cap, re-stat'ing every hard link costs 186 ms warm and 442 ms cold for 11,898 `os.stat`
  calls, and 58 ms serially for 2,975 paths.
- **What the cap does.** It resumes the pass at the next comparison, and each stopped pass writes a `RESTAT-PARTIAL`
  audit line. That happened 401 times in 600 calls on the fully linked tree, and 147 times on the 3,049-link tree.
- **Earlier run, as an upper bound.** Before the cap, with n = 200 on a 2,975-link tree, the one-changed p95 was
  180.6 ms.
- **Earlier run, not comparable.** Round 0 used 30-byte files and no links: worst p95 74.0 ms, baseline 72.4 s.

**Call stream.** The source is this install's transcripts: 463 files (main sessions plus nested subagents), read-only,
spanning 2026-03-20 to 2026-10-05.
- 37,166 `tool_use` events, de-duplicated by id.
- 21,596 calls to the snapshot hook (Bash, PowerShell or Monitor).
  - 6,458 match the skip list and cost about 0.
  - 15,138 walk.
- All sessions are merged into one timeline, because the rate limit applies per install.
- Gaps between walking calls: p10 2.2 s, median 13.3 s, p90 135.5 s. **82.5% are under 60 s.**
- Script-change events come from the same history: 2,578. A change event is either an Edit or Write of a `.psc`/`.pex`,
  or a shell command that runs Caprica, Champollion or the AutoMod papyrus command, or copies or moves a `.psc`/`.pex`.
  - **Stated assumption:** that heuristic is the change rate.
  - A change makes the next walking call a full comparison that finds the change.
- Replaying the 60 s rate limit gives these classes: in window 10,101; outside and unchanged 3,959; outside and
  changed 1,078.
- Each call's cost is drawn from the measured samples for its class. The result is the mean of 50 resamples.

| Tree | stream p95, every hook call | stream p95, walking calls only | worst single-call shape p95 |
|---|---|---|---|
| 3,049 hard-linked | 126.2 | **131.1** | 167.7 |
| all hard-linked, capped | 125.5 | **137.3** | 160.0 |

**Verdict.** The stream p95 is at or below 150 ms on the conservative reading (walking calls only), so **branch 1 holds:
the 60 s rate limit stands**. The margin is 13–19 ms.
- One call in ten that walks can take 160–168 ms at p95.
- The first session's cold baseline costs about 100 s in total. It is spread across the session-start box and per-call
  boxes of 0.25 s.
- To re-run: `scratchpad/p6t14/measure3.py` and `stream.py`.

## Task 15: recovery snapshots and `snapshot restore`

**Hook ordering (INFERRED from Claude Code's documentation, not measured).** The save copy is correct only if it
finishes before the command runs. Claude Code runs every matching PreToolUse hook in parallel, and runs the tool only
after all of them return or time out (60 s in the shipped settings). The recovery deadline is the engine start plus
17 s (the 20 s engine budget minus a 3 s margin), so the copy finishes well inside that.
- The tests show the copy exists when the hook returns. They cannot show Claude Code's scheduling.
- Residual: a hook that Claude Code kills at its timeout lets the tool run with no copy. The heartbeat then records the
  run as killed.
- On Codex the copy happens in the pre-tool side effects, in the same process that returns the verdict.

**Latency (MEASURED, in process, files sized like a 700-plugin install).** Added per call on the non-read-only shell
path:

| Shape | p95 |
|---|---|
| Common call: inside the 60 s window, nothing changed | 0.62 ms |
| Full re-read, once a minute (mostly the index fsync) | 13.8 ms |
| `plugins.txt` changed | 15.4 ms |

- A 41 MB save copy takes about 57 ms warm.
- The single-link nlink sweep (Task 14 re-review N3) adds 7.7 ms p95 to a comparison call. It is capped at 256 paths
  and 6 ms on perf_counter, because `time.monotonic` ticks every ~15.6 ms on Windows: a 10 ms monotonic box MEASURED
  15.5 ms.

**Pricing S-RESTORE-INPLACE (ask)** with `price.py --base a1f24df` over 74,803 real tool calls: **0 changes in any
direction.** No call in the history runs `skyguard snapshot restore --in-place`. The ask therefore costs nothing
historically, and it covers a write into the install that no other write rule sees.

- **Fix round 2** (the rule now matches any spelling of the verb and the flag, and the CLI parsers set
  `allow_abbrev=False`): over 75,670 calls, **1 newly-ask, 0 other changes**. The one hit is the re-reviewer's own
  probe of an abbreviated in-place restore (`--in`) in a scratch install, so it is a true positive.
