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
