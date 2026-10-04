# Plan 5 pricing record

- **Base sha (`git rev-parse --short HEAD` at Task 0 start, branch `plan5`):** `0704791`
- **Preconditions (verified 2026-10-02):** Plan 2 complete on master (`docs/superpowers/plans/2026-10-01-plan2-closeout.md`:
  "completed 2026-10-02; full suite on a87ce27: 1052 passed, 27 skipped"); Codex harness branch merged
  (`a87ce27 Merge branch 'codex/harness-repairs-fd71271'`, `tests/run_harness_tests.py` present);
  `tools/payload-capture/` present (`capture-hook.sh`, `scrub.py`).
- **Claude Code:** 2.1.288.

## Baseline (Task 2, `scripts/guard-corpus/price.py`)

Run 2026-10-02: `--base c3f32ef` (P5-BASE) against the same tree exported from `c3f32ef` (engine vs itself).
Raw items stay outside the repo; counts only here.

- **N = 58,558** unique items, deduplicated on (tool, command|file_path|mcp-input, cwd). Per tool:
  Bash 52,385 / Write 3,166 / Edit 1,450 / PowerShell 1,552 / mcp__devbench* 5. (MultiEdit is folded into the
  file-tool figures; none was seen separately.)
- **Raw tool calls** (before dedup): Bash 54,457; Write 4,114; Edit 8,300; PowerShell 1,600;
  mcp__devbench-vr__capture 6, __console 1, __game 1.
- **Decision matrix (old>new):** allow>allow 55,766; advise>advise 2,340; deny>deny 445; ask>ask 7.
  Every `newly-*` and `no-longer-*` direction is 0, `reason_moved` 0, parse failures 0 (old and new).
- Note: MCP items price as allow until `skyguard.mcp_rules` exists. The engine has no MCP or PowerShell-specific rules at P5-BASE, so MCP items price as allow and
  PowerShell commands go through the shell rules.

## Task 4: S-RESAVER-APPLY (port of install 4a429bd + 1540b99)

Run 2026-10-02: `price.py --base c3f32ef --only-rule S-RESAVER-APPLY` against the Task 4 tree. Raw items stay
outside the repo; counts only here.

- **N = 58,832.** Decision matrix (old>new): allow>allow 56,035; advise>advise 2,341; deny>deny 447;
  **ask>allow 5; ask>advise 4**. Parse failures 0 (old and new), engine errors 0.
- **S-RESAVER-APPLY: newly-ask 0, no-longer-ask 9.** Every other direction is 0.
- **All 9 no-longer-ask items were read by hand. None was a ReSaver execution.** Each is a command that
  merely CONTAINED the rule's words as text: 6 are `python - <<'EOF'` / `cat > x.py <<'EOF'` heredocs
  (analysis and test-authoring scripts, the P1 class), 1 is a shell test harness that passes
  `'bash tools/resaver-cli.sh reset-havok ... --apply'` as a quoted argument to a function, 1 is a heredoc that
  edits `KNOWLEDGEBASE.md`, 1 is a script-generation heredoc that writes this task's own rows. The brief
  expected 3 (the P1 asks seen when it was written); the other 6 are this arc's own sessions, which talk about
  the rule while building it. The history holds **zero real `--apply` runs of ReSaver**, so nothing a person
  would have wanted asked about stopped asking.
- The 4 `ask>advise` items are not a second change: the old ask discarded the advice that accumulated before
  it (rule order), so once the false ask is gone, `S-PLUGIN-REF` / `S-LOADORDER-REF` advice on the same
  command surfaces. Read: all 4 are python heredocs that mention a plugin or `plugins.txt`.
- `reason_moved` = 21: the earlier rename `S-WRITE-INSTALL` -> `S-REDIRECT-INSTALL` (a rule id, same decision),
  committed after `c3f32ef`. Not a Task 4 effect; no verdict moved.
- **The install already has R7.** `1540b99` back-ported the shell-heredoc keep, so P14 no longer reproduces
  there: the install's 13/13 cases and this task's 18 rows agree on 16. The two rows where they differ are the
  deliberate extensions: `t04-java` (`java -jar ...ReSaver.jar --apply`: install allows, intended ask) and
  `t04-herestring` (`cat <<< x` followed by a ReSaver write: the install's awk takes `<<<` for a heredoc and
  swallows the next command; install allows, intended ask).

### Task 4, fix round 1 (widened rule)

Re-priced 2026-10-02 (`--base c3f32ef --only-rule S-RESAVER-APPLY`), N = 58,947. Matrix: allow>allow 56,138;
advise>advise 2,341; deny>deny 447; **allow>ask 5; ask>advise 5; ask>allow 11**. S-RESAVER-APPLY:
**newly-ask 5, no-longer-ask 16**, all else 0; 0 errors, 0 parse failures.

- **newly-ask 5: all read, all REAL saves written.** Each is `bash tools/resaver-cli.sh clean "$IN" "$OUT"
  --undefined --unattached --apply` against a real `Save*.ess` under `Documents/My Games/Skyrim VR/Saves`.
  The old text rule never asked about `clean` (not in its op list), so these writes went through unasked; the
  ask is correct. (The message names "the save" because the path is a shell variable.)
- **no-longer-ask 16** (9 before, +7 from this arc's own sessions): all 16 re-read; every one is text, a test
  script, a heredoc, a quoted harness argument or a documentation edit. Zero real executions.
- `ask>advise` 5: the old ask had swallowed advice on the same command; same reading as before.

### Task 4, fix round 2 (timing, more wrappers)

Re-priced 2026-10-02 (`--base c3f32ef --only-rule S-RESAVER-APPLY`), N = 59,079. Matrix: allow>allow 56,264;
advise>advise 2,342; deny>deny 449; allow>ask 5; ask>advise 5; ask>allow 14. S-RESAVER-APPLY: **newly-ask 5,
no-longer-ask 19**, all else 0, `reason_moved` still 21 (the id rename), 0 errors. The five newly-ask are the
same five real `clean ... --apply` writes as round 1 (re-read). The 3 extra no-longer-ask are this arc's own
text-only sessions (re-read). The bounded quantifiers, the `A.*B` split in `grep.py` and the write-rule
lookbehinds changed no other rule's verdict over 59k real calls (no other decision cell moved, `reason_moved`
unchanged).

### Task 4, fix round 3 (timing fixes, whole engine)

Re-priced 2026-10-02 WITHOUT `--only-rule` (`--base c3f32ef`), N = 59,227. Matrix: allow>allow 56,410;
advise>advise 2,342; deny>deny 451; allow>ask 5; ask>advise 5; ask>allow 14. Per rule: S-RESAVER-APPLY newly-ask 5 /
no-longer-ask 19 (unchanged from round 2); S-REDIRECT-INSTALL newly-advise 21 and S-WRITE-INSTALL no-longer-advise
21, which is the 21 `reason_moved` items of the earlier id rename (same decision). **No decision moved because of
the timing fixes** (install-path lead lookbehind, relative-Data rewrite, the registry rule split in two greps, the
`split_dotstar` whitelist): every deny/advise/allow cell is identical to round 2 apart from items added to the
history since.

## Task 5: `shellparse` (no rule uses it yet) -- the parse-failure gate (R4)

Run 2026-10-02: `price.py --base c3f32ef` (whole engine) against the Task 5 tree. The driver now asks the NEW
tree's `skyguard.shellparse` whether it can read every Bash/PowerShell item (`parse_ok`, `parse_why`;
`tests/test_price_tool.py::test_parse_failures_are_reported_per_tool_when_the_engine_has_the_parser`).

- **N = 59,785.** Matrix: allow>allow 56,966; advise>advise 2,342; deny>deny 452; ask>ask 1; allow>ask 5;
  ask>advise 5; ask>allow 14. Per rule: exactly the Task 4 figures (S-RESAVER-APPLY newly-ask 5 / no-longer-ask 19;
  the 21-item id rename). **No verdict moved** -- the parser is not wired to any rule. 0 engine errors.
- **Parse failures (new engine): 63 of 55,001 shell items = 0.115%** (gate: < 0.5%).
  Bash **61 of 53,443 = 0.114%**; PowerShell **2 of 1,558 = 0.128%**. Old engine 0 (it has no parser).
- **By reason** (all read): `unterminated " quote` 49 -- **47** are a Windows path ending in `\"`
  (`ls "C:\x\Data\"`), which bash itself reads as an escaped quote, so the real command failed too; the other 2
  are genuine. `unterminated \` substitution` 3 (a PowerShell backtick inside a bash double-quoted `pwsh -Command`
  string -- bash would run it as a substitution). `unterminated heredoc` 3 (two `<<'EOF'` on one line,
  `python3 - <<'EOF' || python - <<'EOF'`: the second body is empty-and-unterminated in bash as well).
  `unexpected (` 3, `unterminated ' quote` 2, `unbalanced )` 1 (a backtick-quoted `504)` inside a commit message).
  PowerShell 2: bash heredoc syntax sent to the PowerShell tool, and a malformed `@{...}`.
  Every failure is a command its own shell would reject or misread; none is a parser gap found so far.
- Parser gaps found and FIXED while measuring (before the gate): `name(){ ... }` with no space (was 500 of the
  first run's 557 failures), and `time ( ... )`.

### Task 5, fix round 1 (adversarial review: deletes returned ok=True but not as executed)

Re-priced 2026-10-03 (`--base c3f32ef`), N = 60,355. **No verdict moved** (same per-rule figures as above:
S-RESAVER-APPLY 5/19, the 21-item id rename); 0 engine errors.
- **Parse failures: 63 of 55,467 shell items = 0.114%** (Bash 61 of 53,905 = 0.113%; PowerShell 2 of 1,562 =
  0.128%). Before the round: 63 of 55,001 = 0.115%. **Delta: no new failure.**
- An intermediate run of this round read 68 (0.123%). The 5 extra were MY regressions, both fixed and pinned by
  tests: `wsl` re-joined its words into a string (a `)` inside a quoted vhdx path became syntax; `--import` was read
  as a command) -- 2 PowerShell + 1 Bash; and a `$(( ))` holding a `$( )` was fed whole to the command parser
  (`$(( ($(date +%s%N)-s)/1000000 ))`) -- 2 Bash. Only the substitutions inside a skipped span are parsed now.

### Task 5, fix round 3 (casts, Start-Process arrays, stdin into shells/interpreters, env clusters, input cap)

Re-priced 2026-10-03 (`--base c3f32ef`), N = 60,804. No verdict moved (S-RESAVER-APPLY 5/19, the 21-item rename);
0 engine errors. **Parse failures: 63 of 55,879 = 0.113%** (Bash 61 of 54,298 = 0.112%; PowerShell 2 of 1,581 =
0.127%). Compared item by item with fix round 1: **no command newly fails, none stopped failing.** The new input
cap (256 KB) touches nothing real: the largest of the real shell commands is 50,452 characters.

## Task 6: targets + config as data + cwd plumbing; S-RESAVER-APPLY re-based on shellparse

Run 2026-10-03. Two runs over the same history: `--base 4f13eef` (Task 5 head: isolates this task) and
`--base c3f32ef` (cumulative). New engine = a frozen copy of the working tree's `tools/skyguard`.

**Task 6 alone (`--base 4f13eef`), N = 60,962.** 0 engine errors. Parse failures unchanged: 63 old / 63 new,
0 reasons moved (Bash 61 of 54,437 = 0.112%; PowerShell 2 of 1,581 = 0.127%).
Matrix: allow>allow 58,142; advise>advise 2,353; deny>deny 455; ask>ask 5; **allow>ask 5; advise>ask 1;
ask>allow 1.** Only S-RESAVER-APPLY moved: **newly-ask 6, no-longer-ask 1.** No other rule moved (deny, advise:
0 in every direction), as expected: no rule reads `cwd` or the new directory roots yet.

All 7 read by hand:
- newly-ask, **5 true positives** (PowerShell tool, k=16094, 16113, 16118, 16119, 16120): a script defines
  `function J($a){ & java ... -cp $cp $cli @a }` with `$cp` = `...\ReSaver.jar;...` and `$cli` = `ResaverCLI.java`,
  then calls `J @("clean"|"set-global"|"set-var", $in, ..., "--apply")`. Each one really runs a ReSaver write
  (on a save copy in a scratchpad). The parser does not expand functions (a Task 5 known limit), so these are caught
  by the R4/opaque text fallback (the script also holds an opaque `& $jq ...` program). The old regex rule did not
  read PowerShell at all. The prompt names the first `.ess` in the text (`*.ess`, from `-Filter *.ess`).
- newly-ask, **1 false positive** (Bash, k=10700): a hand-run hook test harness,
  `t(){ ... printf '{"tool_input":{"command":%s}}' "$(printf '%s' "$2" | "$JQ" -R .)" | bash "$H" ...; }` then
  `t "..." 'bash tools/resaver-cli.sh reset-havok save.ess out.ess --apply'`. The ReSaver command is DATA piped into
  the hook; the opaque `"$JQ"` program elsewhere in the command triggers the text fallback.
- no-longer-ask, **1 false positive removed** (Bash, k=9139): `python -c "...for c in ['bash tools/resaver-cli.sh
  reset-havok \"x.ess\" --apply', ...]: parse(c)"` -- Python code that only mentions the command.

**Ruling (Task 6):** the opaque/parse-failure fallback scans the WHOLE command text (resaver + `--apply` + a
whole-word operation), not only the opaque exec's own statement. Why: statement scope would lose all 5 true
positives above (their opaque exec is a different statement from the `J @(... "--apply")` call) to remove 1 false
positive. Cost if wrong: an over-ask on a command that both mentions a ReSaver write and runs an unrelated opaque
program -- 1 in 60,962 items here.

**Cumulative (`--base c3f32ef`), N = 60,965.** S-RESAVER-APPLY newly-ask 10, no-longer-ask 23; the 21-item
S-REDIRECT-INSTALL/S-WRITE-INSTALL id rename (Task 1). Nothing else. 0 engine errors.

**S-INERT-CONFIG over this machine's real paths files (Step 3).** The strict parser was run over every
`skyrim-paths.env` found (install `.claude/skyrim-paths.env`; no toolkit checkout has one, only the
`.example` files, which also parse): **0 malformed of 1 real file** (and 0 of the 6 `.example` copies). The
replay above cannot see this rule (the driver passes no paths file), so this is its whole pricing.

**Re-based rows.** All 63 `t04-*` rows pass unchanged on the parser. 12 `t06-*` rows added; against the Task 5
engine 4 of them differ (RED): `cmd /c`, the `--ap""ply` quote split, the variable-held program, and the malformed
paths file. The `pwsh -Command` and PowerShell `&` rows already asked under the regex rule and are pins.

### Task 6, fix round 1 (adversarial review: covers_protected, 8.3 names, admin shares, BOM, scratch ruling)

Re-priced 2026-10-03, new engine = a frozen copy of the fix-round tree.
- `--base 4f13eef`, **N = 61,149**. 0 engine errors. Parse failures 63 → 63, 0 reasons moved.
  - The verdict changes are the same as before the round, item for item (compared by command): S-RESAVER-APPLY
    **newly-ask 6** (the same 5 TP + 1 FP) and **no-longer-ask 1** (the same FP removed).
  - Nothing else moved. `covers_protected`, the 8.3/admin-share/stream handling and the narrowed scratch list have
    no rule caller until Task 7, so this round cannot move a verdict; Task 7 prices them.
- `--base c3f32ef` (cumulative), **N = 61,152**: S-RESAVER-APPLY newly-ask 10, no-longer-ask 25, plus the
  21-item rename.
  - The 2 extra no-longer-asks (25 vs 23) are new history items: this session's own commands that write corpus
    rows or docs CONTAINING a ReSaver `--apply` line as data. Both were read, and both are correct allows.
- **S-INERT-CONFIG with the BOM fix and the declare forms:** 0 malformed in the install's real `skyrim-paths.env`
  (1 real file), and 0 in the 3 checked `.example` copies (plan5, master, plan4).

## Task 7: delete rules on the parser (E16, relative deletes, PowerShell forms, MO2)

Run 2026-10-03. New engine = a frozen copy of the working tree. Raw items stay in the session scratchpad.

**Task 7 alone (`--base a07c164`), N = 61,683** (Bash 55,071; PowerShell 1,598; Write 3,445; Edit 1,564; MCP 5).
0 engine errors. **Parse failures 63 → 63** (Bash 61 of 55,071 = 0.111%; PowerShell 2 of 1,598 = 0.125%; R4 gate
< 0.5%). 0 shell items carry an empty `cwd` (the R9 empty-cwd path costs nothing on this history).

Decision matrix (old > new): allow>allow 58,853; advise>advise 2,351; ask>ask 11; deny>deny 343;
**allow>deny 2; deny>advise 90; deny>allow 23; advise>allow 10.** Per rule (a rule "newly fires" when its id is
new on that item, so a deny that only changed rule counts on both sides):

| rule | newly-deny | no-longer-deny | other |
|---|---|---|---|
| S-DEL-INSTALL | 3 | 278 | |
| S-DEL-CONFIG | 0 | 28 | |
| S-DEL-UNRESOLVED | 190 | 0 | |
| S-DEL-FALLBACK | 2 | 0 | |
| S-REGISTRY | 1 | 1 | |
| S-RELATIVE-UNKNOWN | | | no-longer-advise 12 |
| S-PLUGIN-REF / S-EXTERNAL-OUT / S-REDIRECT / S-MOVE / S-LOADORDER | | | newly-advise 87 / 3 / 2 / 1 / 1 (advice a removed deny used to pre-empt) |

Old-rule → new-rule movement: S-DEL-INSTALL→S-DEL-UNRESOLVED 166, S-DEL-CONFIG→S-DEL-UNRESOLVED 24,
S-DEL-INSTALL→advise 88, S-DEL-INSTALL→allow 21, S-DEL-CONFIG→advise/allow 3, S-DEL-CONFIG→S-DEL-INSTALL 1,
S-DEL-INSTALL→S-DEL-FALLBACK 1, S-DEL-INSTALL→S-REGISTRY 1, S-REGISTRY→S-DEL-FALLBACK 1, S-RELATIVE-UNKNOWN→none 12,
allow→S-DEL-INSTALL 2.

**Newly denied items (decision moved to deny): 2. Cap (200) not approached.** Both read:
- `cd .claude/backups/.hook-heartbeat && rm -f protect-bash.blind ...` (cwd = install): deletes hook heartbeat
  markers under `.claude/`. True positive BY R8 (`.claude/` stays protected); cost 1 item.
- `cd _CLAUDE_OUTPUT/nexus-v3.9 && rm -f <two stale notes>` (cwd = install): deletes the user's own deliverables
  folder inside the install. **Ruling:** not added to `SCRATCH_GLOBS` -- it holds files the user reads, so
  deleting there is the user's call; cost 1 item.

**No-longer-denied items (deny → advise/allow): 113, every one read** (operands resolved and listed per item):
- 96 items whose delete operands (136 operands in all) ALL resolve OUTSIDE every protected root: `/tmp/*`,
  the user's Temp/scratchpad, `C:/Temp/*`, `C:/GOG modding tools/_*`, `C:/GOG Games/spriggit-tmp/*` and
  `C:/GOG Games/zgin/*` (siblings of the install), `~/.plans`, a toolkit checkout. Each was denied only because
  the same command also named the install (`cd <install> && ...`). **FP removed.**
- 17 with no executed delete at all: grep / git log / git commit messages, python heredocs that edit a document or
  write a script TEXT containing `rm`, hook test harnesses that pass a delete command as data, and 3 `git rm
  --cached` (index only). **FP removed.**
- **True positives lost: 0.** (A first pass found one class -- `git rm` without `--cached` deletes working files
  and was caught only by the text rule -- and `git rm` was added as a delete primitive before this run.)
- advise → allow (S-RELATIVE-UNKNOWN, 12): absolute operands outside the install, variables holding scratch paths,
  echo/commit text. All correct (the cwd is known on every item, so relative words resolve).

**Rule moves that stay deny (read by class, not changing a decision):**
- S-DEL-UNRESOLVED 190 = deny→deny. A variable operand (`rm -rf "$SCRATCH/x"`) in a command that also names the
  install. Classified by substituting each command's own literal assignments (analysis only): **20 true positives**
  (the variable holds a path in the install: `.claude/backups/...`, `_CLAUDE_OUTPUT/...`, `Data/...`), **100 false
  positives** (scratchpad / Temp / `/tmp`), **70 unknown** (loop variables, values built from other variables,
  `mktemp`). These were denied before too (by S-DEL-INSTALL's text test); the new message says what to do ("Write
  the full path"). Not narrowed here: tracking same-command assignments is a parser feature with its own misread
  risk (a silent allow) -- carried as a follow-up with this count.
- S-DEL-FALLBACK 2 = deny→deny: hook test harnesses whose single exec carries a delete command and the install
  path as data next to an opaque `"$JQ"` / `bash .claude/hooks/$1`. Retained FPs.
- **Ruling (opaque clause):** an opaque program triggers the fallback only when ONE exec's own words carry both a
  delete word and a protected root. A first version applied the legacy whole-command test to any command with an
  opaque exec: it re-denied 13 commands of the shape `SP=<spriggit.exe>; rm -rf /tmp/x; "$SP" serialize
  <install>/Data/...` (13 of 13 read: false positives) and newly denied 1 more (an `echo '{"command":"rm -rf
  /tmp/x"}' | "$JQ"` probe beside a `cd <install>`). Per exec, both are gone and `printf 'rm -rf <install>' | sh`
  still denies.

**Cumulative (`--base c3f32ef`), N = 61,683:** the Task 7 figures above, plus S-RESAVER-APPLY newly-ask 10 /
no-longer-ask 25 (Tasks 4-6) and the 21-item S-REDIRECT-INSTALL/S-WRITE-INSTALL rename (Task 1). 0 errors.

**Rulings ledgered in this task** (each pinned by a t07 row and a mutation):
- Delete primitives beyond the brief: `git rm` (not `--cached`, not `-n`), `rimraf` (direct and via
  npx/pnpx/bunx/`pnpm|yarn|bun dlx`), `robocopy` with `/MIR` or `/PURGE` (deletes in the destination) or `/MOV`
  / `/MOVE` (deletes the source), `[IO.File]::Delete` / `[IO.Directory]::Delete`. A `git -c alias.x=!...` is
  opaque. Interpreter code (R7(b)) is checked with the legacy delete regex PLUS a short list of delete APIs
  (`os.remove/unlink/rmdir/removedirs`, `.unlink(`, `.rmdir(`, `.rm(Sync)(`, `remove_tree(`, perl `unlink`): 0 newly
  denied items from it.
- `git clean -n` / `--dry-run` deletes nothing; a PowerShell `-WhatIf` is NOT honoured (stricter; 0 items seen).
- Empty cwd (branch 1 of R9 with no cwd in the payload): a relative operand is unresolved, so it denies under
  clause (b) when the command names a protected root, and otherwise advises (S-RELATIVE-UNKNOWN) when it names
  `Data`. 0 history items have an empty cwd.
- Unknown dialect (R5) with no cwd: PowerShell's reading of `/tmp/x` is the current drive's root, which cannot be
  placed, so `rm -rf /tmp/x && ls <install>` denies under `shell=""`. Pinned by a test; Claude always sends the
  dialect.
- A QUOTED word whose literal reading is a protected path is that path (an install named `Skyrim (VR) [x]$`).
- Tightening: `rm ".../My Games/S<KELVIN SIGN>yrim VR/a.ini"` now denies (Python's lower() folds the Kelvin sign;
  NTFS would not): two legacy allow rows superseded as deny.

**Latency (Step 4).** `scripts/guard-latency.py` on a setup.sh-configured copy, 50 runs, three runs under 4-16 %
sampled CPU load with other sessions active. protect-bash p95 905 / 471 / 372 ms (Plan 2: 358 ms under load).
The gate (every wrapper p95 <= 1 s) FAILED on each run on wrappers this task does not touch (protect-files p95
1,105 / 1,667 / 360 ms; session-kb-guard 838 / 1,011 / 2,113 ms) -- the load-dominated tail Plan 2's E2E
measurement documented. Engine alone, in-process over all 56,669 shell items of the history: median 0.094 → 0.173
ms, p95 0.663 → 0.675 ms, p99 1.278 → 1.109 ms, max 13.3 → 10.7 ms (old = `a07c164`). The delete rules add no
measurable tail; an idle-machine gate run is still owed (Task 17).

### Task 7, fix round 1 (review follow-ups)

`--base 4777bd3`, **N = 61,935** (Bash 55,285; PowerShell 1,600). 0 engine errors; parse failures 63 → 63.
Matrix: allow>advise 66, deny>advise 1, every other cell unchanged.
- **S-DEL-UNRESOLVED-CWD newly-advise 75 = 0.132% of 56,885 shell items** (under the 0.2% noise budget, so it ships).
  All 75 read: an unresolved delete operand (`$SP/...`, `$S/...`, `$SCRATCH/...`, `(Join-Path $base ...)`, pipe
  input) run with the cwd inside the install. Substituting each command's own literal assignments (analysis only):
  **57 resolve outside every root, 18 are built from other variables -- all 18 read, every one a Temp/scratchpad or
  `C:/Temp` path. 0 of 75 would have deleted inside the install.** On this history the advice is noise; it stays
  (one line, no echo) because it is the only signal for the silent-allow gap the review named.
  A first version also advised when the cwd merely CONTAINED a root (`C:/`); the cwd test is now "inside" (zone).
- **S-DEL-INSTALL no-longer-deny 1** (read): `rm -rf tools/__pycache__` from the install -- the R8 refinement
  (Python regenerates it). With the deny gone, an S-PLUGIN-REF note on the same command surfaces (newly-advise 1).
- **Controller ruling: S-DEL-UNRESOLVED-CWD removed** (75 advisories, 0 real -- pure noise; the standing directive
  is against advice fatigue). It is now a KNOWN LIMIT: an unresolved delete operand run from inside a protected
  folder is not flagged. Re-priced `--base 4777bd3`, **N = 62,018**: newly-advise 1 (S-PLUGIN-REF) and
  no-longer-deny 1 (S-DEL-INSTALL) -- both the same item, `rm -rf tools/__pycache__` (item 2). Nothing else moved;
  0 errors; parse failures 63 → 63.

## Task 8: shell writes to plugins (E21), the write-target notes, ADV_PLUGIN removed

Run 2026-10-03. New engine = a frozen copy of the working tree. Raw items stay in the session scratchpad.

**Task 8 alone (`--base 99bfc4b`), N = 62,181** (Bash 55,505; PowerShell 1,601; Write 3,491; Edit 1,579; MCP 5;
**shell calls 57,106**). 0 engine errors. Parse failures 63 → 63 (Bash 0.110%, PowerShell 0.125%).

Decision matrix (old > new): allow>allow 59,375; advise>advise 604; ask>ask 11; deny>deny 347;
**advise>allow 1,834; advise>deny 7; allow>advise 3.**

| rule | direction | items | % of shell calls | hand-read verdict |
|---|---|---|---|---|
| S-WRITE-PLUGIN | newly-deny | 7 | 0.012% | all 7 read: every one really writes a file named `.esp` by a redirect (`echo v>victim.esp`, `printf 'canary' > probe-abs.esp`, `> _CLAUDE_OUTPUT/zzz_relock2.esp`, ...). All 7 are probes from the sessions that MEASURED E21 itself. True positives by the rule (any location, like W-PLUGIN). |
| S-COPY-PLUGIN | newly-advise | 36 | 0.063% | all 36 read: every one copies or moves a file onto a live plugin directly in `Data/` (spriggit-built ESP deployed over `Data/Shinso.esp`, `.esp.save` sidecars promoted, reverts from `.claude/backups`, `ZZZGin*` spike plugins). 36 of 36 accurate; all are the user's own intended deploys. |
| S-WRITE-INSTALL | newly-advise | 5 | 0.009% | all read: README written into `Data/Sound/FX/...`, `sed > *.psc` and `sed -i` on `Data/Scripts/Source/*.psc`, a stub `.psc`. 5 of 5 real writes in `Data/`. |
| S-WRITE-LOADORDER | newly-advise | 4 | 0.007% | all read: 3 `printf > .../loadorder.txt` building MO2/Nolvus test fixtures in a scratchpad; 1 `cp` of the VR plugins.txt over the **SSE** plugins.txt (the gotcha-#10 file). 4 of 4 real writes. |
| S-WRITE-INI | newly-advise | 3 | 0.005% | the same 3 fixture scripts (`printf > .../Skyrim.ini`). Real writes; to scratch fixtures. |
| S-WRITE-SKSE-INI | newly-advise | 1 | 0.002% | `cp ... "$GAME/Data/SKSE/Plugins/SkyPatcher/npc/IchimaruGinVoice.ini"`: real. |
| S-PLUGIN-REF (ADV_PLUGIN) | no-longer-advise | 1,851 | 3.24% | see below |
| S-LOADORDER-REF | no-longer-advise | 176 | 0.31% | see below |
| S-REDIRECT-INSTALL | no-longer-advise | 23 | | all 23 read (below) |
| S-MOVE-INSTALL | no-longer-advise | 11 | | all 11 read: the same command now carries S-COPY-PLUGIN, and the legacy text note is not added on top (by design). |

**Advise noise.** New notes: 49 items = **0.086% of shell calls**; every rule is under the 0.2% budget (largest:
S-COPY-PLUGIN, 0.063%). Removed: 2,061 rule firings, net **1,834 advise items become silent** (3.21% of shell
calls), against 3 allow → advise.

**No-longer ADV_PLUGIN (1,851): seeded sample of 100 (seed 8) read by hand: 0 shell-primitive writes to a plugin
missed.** The sample is reads (cat, automod `view-record`/`info`/`archive list`, spriggit `serialize`, xelib
scripts, grep/strings), copies FROM `Data/*.esp` into `.claude/backups` or staging (correctly silent), and ~10
`spriggit deserialize --OutputPath Data/X.esp` (the sanctioned writer; S-EXTERNAL-OUT is Task 9's). Two copies onto
`Data/` plugins in the sample now carry S-COPY-PLUGIN.
Beyond the sample, **every one of the 1,851** was screened for a write word and a plugin extension in one statement
segment (245 hits) and each hit read:
- 147 "redirect/other" segments: redirects of a READ's output into a log/json, `cat > ..._X.esp.yaml` (Spriggit
  YAML, not a plugin), `sed` on scripts and YAML, `--OutputPath` of spriggit, python/js test DATA strings. The only
  real redirects onto `.esp` names are the 7 now denied.
- 1 python `open(...,"w")`: a test history file.
- 163 copy segments: 31 now noted (S-COPY-PLUGIN). Of the 132 silent: copies FROM Data into backups/staging, and
  **17 deploys whose destination is a variable** (`cp "$SCRATCH/build/X.esp" "$GAME/Data/X.esp"`, `mv -f
  "$GAME/Data/X.esp.save" "$GAME/Data/X.esp"`). That was a miss in the first draft; fixed (an unresolved
  destination whose own words put the plugin directly in a `Data` folder notes) and re-priced: S-COPY-PLUGIN
  26 → 36 items (the 10 new ones read: all real deploys onto `$GAME/Data/*.esp`). Remaining silent: 6
  `cp "$S/X.esp" "$D/X.esp"` (SkyrimNet manual deploy; `$D` names no Data folder) -- KNOWN LIMIT.

**No-longer S-LOADORDER-REF (176): screened all for a write word next to `plugins.txt`/`loadorder.txt` (11 hits),
each read.** One real miss in the first draft -- `cp ".../Skyrim VR/plugins.txt" "$SSEDIR/plugins.txt"` (a COPY
onto a load-order file) -- fixed (a copy onto a classified file notes as a write of it) and re-priced: S-WRITE-
LOADORDER newly-advise 3 → 4. Remaining: 1 `fs.writeFileSync(..., 'plugins.txt')` in node code (interpreter code,
KNOWN LIMIT, R11) and test-data strings. The other 165 are reads.

**No-longer S-REDIRECT-INSTALL (23), all read:** 1 moved to S-WRITE-INSTALL (`cat > Data/Sound/.../README.txt`),
2 moved to S-WRITE-PLUGIN (probe.esp), and 20 are writes to the install OUTSIDE `Data/`: 16 xelib/tool scripts
`cat > tools/xelib/*.js`, `tools/snapshot-stack.sh`, an append to `KNOWLEDGEBASE.md`, 2 `_CLAUDE_OUTPUT/*.txt`
notes. **Ruling:** S-WRITE-INSTALL covers `Data/`, the config folder and MO2 only -- the rest of the install is the
user's own working tree (scripts, notes, docs); a note there is noise (0 of 20 a game file). Cost if wrong: an
overwrite of a non-game install file (KNOWLEDGEBASE.md) is silent; the session KB guard snapshots that file.

**First draft vs final.** The first draft (run 1) newly denied 9: the 7 above plus 2 hook-test harnesses that pass
`printf 'x' > "$SP/Data/Test.esp"` as DATA to a `run()` function beside an opaque `bash "$H"`. The opaque fallback
now checks only the opaque program's own words and the nearest upstream program feeding its pipe; both FPs are
gone and `printf 'echo x > a.esp' | sh` still denies (pinned: `t08-harness-data-esp`, `t08-opaque-pipe-esp`).

**Ask stop rule.** Not triggered: S-COPY-PLUGIN ships as ADVISE (controller ruling). All 36 items are the user's
own deploys -- as an ask that would be 36 prompts (> 20, the stop line), and none is a miss an ask would have
prevented, so there is no priced case for upgrading it.

**Cumulative (`--base c3f32ef`), N = 62,218:** the Task 7 figures plus Task 8's: S-WRITE-PLUGIN newly-deny 7,
S-COPY-PLUGIN newly-advise 36, S-PLUGIN-REF no-longer-advise 1,763 (Task 7 had already exposed 88 of the 1,851),
S-LOADORDER-REF 176, S-WRITE-INSTALL newly 4 / no-longer 20 (the c3f32ef id of the legacy redirect note), S-WRITE-
LOADORDER 4, S-WRITE-INI 3, S-WRITE-SKSE-INI 1. 0 errors; parse failures 0 → 63 (the old engine has no parser).

**Timing.** 16 new shapes x 2 dialects (`test_write_shell_rules_stay_fast`), all linear; worst at 80 KB 109 ms CPU
(`fallback-write-words`, the parse-failure segment scan), next 36 ms.

## Task 9: every advice anchored to the executed program; 2-line messages; write-guard notes by zone

### Task 9, item 0 (Task 8 review minors)

`--base dffda1a`, N = 62,406 (Bash 55,697, PowerShell 1,604, Write 3,514, Edit 1,586, MCP 5); shell calls 57,301.
0 errors; parse failures 63 → 63.

| change | newly-deny | newly-ask | newly-advise | no-longer-deny | no-longer-ask | no-longer-advise |
|---|---|---|---|---|---|---|
| fallback: one target word after `>` (not the rest of the segment); `=>`/`->` are arrows | 0 | 0 | 0 | 0 | 0 | 0 |
| a copy's destination classified by its RESOLVED path too | 0 | 0 | 0 | 0 | 0 | 0 |
| `cp /dev/null X.esp` (a copy from a null device) is a write | 0 | 0 | 0 | 0 | 0 | 0 |

Decision matrix unchanged (advise 607, allow 61,433, ask 11, deny 355). The three shapes do not occur in this
machine's history; each is pinned by a `t09-*` row and a mutation. tee keeps ALL its operands as targets (not just
the first word): `tee a.log X.esp` really writes X.esp, so "first word only" would be a miss, not a fix.

### Task 9, main change (every advice anchored; registry/.psc/Champollion denies anchored; write-guard notes by zone)

`--base dffda1a`, N = 62,551 (Bash 55,819, PowerShell 1,604, Write 3,531, Edit 1,592, MCP 5); shell calls 57,423,
file-tool calls 5,123. 0 errors; parse failures 63 → 63 (0.109% Bash, 0.125% PowerShell). Matrix: allow>allow
61,494; advise>advise 245; advise>allow 363; allow>advise 61; allow>deny 20; deny>allow 16; deny>deny 341; ask>ask 11.

| rule | direction | n | hand-read | verdict |
|---|---|---|---|---|
| S-CHAMPOLLION | newly-deny | 20 | **all 20:** every one RUNS `tools/Champollion/Champollion.exe "Data/Scripts/X.pex"` after `cd` into the install (a relative operand; 1 is `"Data/Scripts/$s.pex"` in a for loop) -- the exact shape the rule exists for (Champollion writes .psc into Data/Scripts/Source whatever the flags; two items then read the generated `Data/Scripts/Source/X.psc`). The text rule missed them because it needed the absolute path. | TP by rule (the plan's tightening) |
| S-CHAMPOLLION | no-longer-deny | 1 | read: Champollion on `"$SCR/HiggsVR.pex"` after a `cp` of the install's .pex to a scratch folder -- the remedy the deny prescribes; the text rule saw the install path in the `cp`. | FP removed |
| S-REGISTRY | no-longer-deny | 15 | **all 15:** 12 TEXT -- heredocs/python edits whose code mentions the words (this plan's own edits, twice), `codex execpolicy check ... <reg-words>` (reg is an ARGUMENT there), a printf to progress.md, a grep, a test-row file; 3 REAL deletes of `HKCU\Software\X4ToolkitTests\...` keys from X4 sessions -- not a Bethesda key (outside the rule's scope; the text rule denied ANY such delete). 0 Bethesda-key deletes lost. | 12 FP removed; 3 out of scope (Ruling below) |
| S-EXTERNAL-OUT | no-longer-advise | 248 | categorised all 248: 197 Caprica compiling into `Data/Scripts` (`-o ..`, `--output "Data/Scripts"`, absolute), 51 Spriggit `deserialize --OutputPath Data/X.esp`; 0 decompiles. | noise removed (Ruling below) |
| S-MOVE-INSTALL | no-longer-advise | 103 | categorised all 103: 60 backups INTO `.claude/backups`, 7 backups to `~/.claude/backups`, 30 to an unresolved `$VAR` destination (each read: scratch, snapshot, stash and diag folders; 0 into Data/), 5 syncs into the install's `tools/`, 1 text in a printf. The install was the SOURCE, or the destination was outside Data/, in every one. | noise removed (destination only) |
| S-MOVE-INSTALL | newly-advise | 66 | **all 66:** real `cp`/`mv` onto files in `Data/` given RELATIVE (after `cd`): mesh/script/audio deploys and reverts from `.claude/backups`, `.fuz`/`.xwm` placement, music-folder moves, 2 `.bak` copies inside `Data/`. 0 FP by the rule's definition (each really overwrites in Data/); each is the user's deliberate deploy. | TP by rule; see the report's concern |
| S-AUTOMOD-NIF | no-longer-advise | 5 | all 5: a plan-file heredoc, a `--dry-run` rename-strings, a 7z extract of the AutoMod zip, a grep loop, one of this session's python one-liners. 0 executed NIF writes. | FP removed |
| S-AUTOMOD-ESP | no-longer-advise | 1 | read: a grep over `tools/automod` sources for command names. | FP removed |
| S-SED-WINPATH | no-longer-advise | 3 | all 3: `sed -i` on scratch files outside the install with no backslash in the replacement; one is a `curl | python` with no sed write at all. | FP removed |
| W-PAPYRUS | no-longer-advise | 10 | all 10: Edits of decompiled `.psc` in `%TEMP%\pex_decompile\`. | FP removed |
| S-EXTERNAL-OUT, W-INSTALL, S-SED-WINPATH, S-AUTOMOD-ARCHIVE | newly-* | 0 | -- | -- |

newly-ask 0, no-longer-ask 0. Newly-deny 20 (cap 200).

**Advise noise.** Shell calls carrying any advice: **451 → 159 (0.79% → 0.28%)**; file-tool calls: 157 → 147
(3.06% → 2.87%). Per rule, % of shell calls (old → new): S-EXTERNAL-OUT 0.432% → 0; S-MOVE-INSTALL 0.251% → 0.186%;
S-AUTOMOD-NIF 0.021% → 0.012%; S-AUTOMOD-ESP 0.005% → 0.003%; S-SED-WINPATH 0.005% → 0; S-AUTOMOD-ARCHIVE 0.005%
unchanged. Every shell advise rule is now ≤ 0.2%. Write guard (% of file-tool calls): W-PAPYRUS 2.62% → 2.42% (124
hits = 0.20% of ALL calls; all 124 are Edits/Writes of `.psc` in the install's `Data/Scripts/Source`, 65 distinct
files -- the snapshot-before-experimenting case the note exists for); W-INSTALL 0.23% unchanged; W-SKSE-INI 0.20%.

**Rulings.**
- **S-EXTERNAL-OUT covers decompilers only** (Champollion -p/-a/-o; AutoMod `papyrus decompile`). Of the 501 hits
  the plan-table rule (any Papyrus/ESP tool's output flag into the install, cwd-resolved) produced in the first
  draft, 369 were Caprica compiling into Data/Scripts and 127 Spriggit deserializing the user's plugin into Data/ --
  the tool writing its product where it belongs, every time; 0 were decompiles. That is the "always noise" class
  the user's directive removes. Cost if wrong: a compiler or Spriggit writing to an unintended install folder is
  silent (the legacy text rule's 248 hits held no such case either). Supersessions `t09-ext-tool-out(-config)`.
- **S-REGISTRY covers Bethesda keys and their ancestors** (the plan table: "on a Bethesda key"), plus an
  unresolved key when the command names Bethesda. The 3 real X4 test-key deletes above become silent. Cost if
  wrong: a delete of an unrelated key is not this guard's to stop.
- **On a parse failure the advise rules are not attempted** (as S-WRITE-INSTALL in Task 8: a deviation from R4 for
  advice only). The deny rules (registry, .psc output, Champollion) keep the legacy text fallback.
- **S-MOVE-INSTALL covers the install's Data/, the config folder and MO2** (the S-WRITE-INSTALL area): 65 of the 103
  removed hits were copies into `.claude/backups` and `tools/`.

**Cumulative (`--base c3f32ef`), N = 62,566:** 0 errors; parse failures 0 → 63 (the old engine has no parser).
Matrix: allow>allow 59,618; advise>allow 2,120; deny>allow 125; allow>deny 22; ask>allow 25; allow>ask 9;
allow>advise 58; advise>advise 244; advise>deny 6; deny>advise 4; advise>ask 1; ask>ask 1. Task 9's rows as above
(S-EXTERNAL-OUT no-longer 245, S-MOVE-INSTALL newly 66 / no-longer 113 -- 10 of them taken over by S-COPY-PLUGIN in
Task 8), Tasks 4-8 unchanged.

**Timing.** 18 anchored-rule shapes x 2 dialects (`test_anchored_rules_stay_fast`) and the write guard's 8 path
shapes with roots, all linear; a mutation removing the sed path regex's word-start lookbehind is caught.
