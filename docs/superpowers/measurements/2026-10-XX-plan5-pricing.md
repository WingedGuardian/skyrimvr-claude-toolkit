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
