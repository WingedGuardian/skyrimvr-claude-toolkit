# Plan 2 equivalence replay — 2026-10-02

Spec §7.2. Every unique Bash `command` and Edit/Write/MultiEdit `file_path` from this machine's real
Claude Code history (`~/.claude/projects`, including nested subagent transcripts) was fired at the
frozen bash hooks (`tests/guard_corpus/reference/`) and at the new Python-engine wrappers
(`.claude/hooks/`), in a `declared` install, by `scripts/guard-corpus/replay.py`. Raw commands were
kept outside the repository and are not committed.

## Result

**N = 53,445 unique items. Agree: 53,441. Disagree: 4. Unexplained: 0.**
**No item the old hooks denied or asked about is allowed by the new ones.**

Raw tool calls seen: Bash 52,384 · Edit 8,076 · Write 3,917 · PowerShell 1,575 (not replayed: the
toolkit's hooks are not wired to the PowerShell tool — Plan 5).

Only the two blocking guards are compared. The side-effect hooks (backup, snapshot) emit no decision.

| Hook | Old | New | Count |
|---|---|---|---|
| protect-bash | allow | allow | 47,503 |
| protect-bash | advise | advise | 2,025 |
| protect-bash | deny | deny | 409 |
| protect-bash | ask | ask | 3 |
| protect-bash | allow | **advise** | 2 |
| protect-files | allow | allow | 3,398 |
| protect-files | advise | advise | 92 |
| protect-files | deny | deny | 13 |

## Disagreement classes

Each was read by hand.

1. **Message text only, same decision (2 items).** The old hook passes the command through `jq.exe`,
   and Git Bash's MSYS argument conversion rewrites path-like text on the way (for example `/c/x`
   becomes `C:/x`). The new engine echoes the command exactly. Scrubbed shape: a command containing a
   POSIX-style drive path, e.g. `ls /c/<dir> && ...`. Resolution: the new text is the correct one; no
   change. (Plan 2 ledger ruling, Task 8.)
2. **New hook stricter: allow → advise (2 items).** Both are a heredoc writing a script that contains
   a plugin filename immediately followed by a non-ASCII letter (shape: `cat My.esp<é>`). GNU grep's
   `\b` treats the non-ASCII byte inconsistently; the engine matches with `re.ASCII`, so it sees a word
   boundary and advises. Resolution: accepted, covered by the Task 3 ruling (Python is stricter in every
   measured case; the cost is an extra advisory on exotic Unicode spellings).

**Where the OLD hook was stricter: none.**

An earlier partial run showed one engine timeout under heavy machine load. It agreed on retry and
does not appear in the final result.

## Method notes

- Resumable: the item list is frozen on first run and finished results are kept; errors and timeouts
  are retried. The run took three sittings because of a 2-hour background limit.
- Machine was shared with other sessions; timings from this run are not latency measurements
  (see the Task 9 latency measurement instead).
