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
