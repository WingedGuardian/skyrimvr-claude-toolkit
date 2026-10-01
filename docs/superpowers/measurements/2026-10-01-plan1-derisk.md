# Plan 1 de-risk measurements — 2026-10-01

Task 0 of the Plan 1 implementation plan. All runs were in throwaway scratch repos, none in a user install.
Tiers per the project's evidence rules.

## Versions

`claude --version` → `2.1.287 (Claude Code)` · `codex --version` → `codex-cli 0.159.3` (auto-updated from
0.154.0 since the 2026-09-30 spike) · Windows 11, Git Bash, Python 3.10.10.

## Results

| # | Question | Result | Tier |
|---|---|---|---|
| R1 | Do Plan 1 Tasks 1–4, applied as written to a clone of `c9ddeac`, keep the suite green? | **Yes.** Baseline 268 passed → after Tasks 1–4, 285 passed (268 + 17 new), including `test_setup_paths`, `test_repo_invariants` and the full mutation gate. | MEASURED |
| R2 | Does moving the sources to `agent/` and regenerating change anything but the header? | **No.** The `-U0` diff of `CLAUDE.md`, `AGENTS.md` and `.claude/` against `HEAD`, filtered to drop the header line, is empty; 16 files gained one line, 3 JSON/.example files are unchanged. | MEASURED |
| R3 | Does the drift gate bite? | **Yes.** Appending a byte to `.claude/hooks/protect-bash.sh` → `--check` exit 1, naming the file; restoring → exit 0. The mutation-gate entry `generated-files-cannot-be-hand-edited` is killed (harness requires pytest exit 1). | MEASURED |
| R4 | Does `git add --renormalize` change any blob? | **No:** only `.gitattributes` was staged. The blobs were already LF; the working copies stay CRLF until a fresh checkout. | MEASURED |
| R5 | Are `.example` target files LF in an autocrlf working copy? | **No, CRLF.** The generator copied them raw. Fixed: `_lf()` is now applied to every target file, and `test_crlf_target_file_generates_lf` went RED→GREEN. | MEASURED |
| R6 | Does `claude -p` fire project `PreToolUse` hooks with matcher `"*"`, including the PowerShell tool? | **Yes.** 5 payloads: `SessionStart`; `PreToolUse` for `Bash` {command, description}, `PowerShell` {command, description}, `Write` {file_path, content}, and `Edit` {file_path, old_string, new_string, replace_all}. Without `< /dev/null`, `-p` waits 3 s for stdin. | MEASURED |
| R7 | Does `codex exec` 0.159.3 fire `PreToolUse` for shell and all three `apply_patch` forms? | **Yes.** 5 payloads: `SessionStart`; `Bash` (`Get-Date`, run in pwsh); and `apply_patch` with `*** Add File:`, `*** Update File:` and `*** Delete File:`. | MEASURED |
| R8 | Does `failureMode: "block"` ([openai/codex#41979](https://github.com/openai/codex/issues/41979)) exist on 0.159.3? | **No.** The key was placed on both the matcher group and the hook entry, and the hook was made to exit 1. The hook showed as "Failed", the command ran, and the file was created. The unknown key is ignored silently (`--strict-config` does not cover `hooks.json`). `codex features list`: `hooks` stable, nothing failure-related. | MEASURED |
| R9 | Codex exit 2 failing to block | Windows-only bug [openai/codex#48183](https://github.com/openai/codex/issues/48183): hooks run via `pwsh -Command`, which turns 2 into 1. | READ (issue) |
| R10 | Codex surfaces without hooks | [openai/codex#24453](https://github.com/openai/codex/issues/24453) reports Windows command executions emitting no `PreToolUse` even with matcher `"*"`. | READ (issue) |

## Not measured (deferred, with owner)

- **Interactive Codex TUI.** Needs a real terminal; this harness cannot drive one. → Plan 4, before any
  parity claim for the TUI (D14).
- **Codex desktop app / IDE extensions.** → Plan 4 or later, per D14.
- **MCP tool payloads** (both agents) and Codex `SessionStart` `compact`. → Plans 4–5.
