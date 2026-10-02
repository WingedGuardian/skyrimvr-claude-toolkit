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
- Note: the engine has no MCP or PowerShell-specific rules at P5-BASE, so MCP items price as allow and
  PowerShell commands go through the shell rules.
