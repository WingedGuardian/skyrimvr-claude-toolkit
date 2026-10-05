# Plan 4 Task 14 -- Codex end-to-end, PARTIAL (exec phases run; TUI and Phase C pending the user)

Measured 2026-10-04 on `plan4` at 1718d74 + this task's harness. codex-cli 0.160.0 (model `gpt-6.1-sol`,
approval `never`, from the user config), Claude Code 2.1.288, Windows 11 26200. Harness:
`scripts/e2e-codex.py` (`--make`, `--phase A|B|C`, `--report`) and `scripts/e2e-codex-collect.py` (read-only
TUI collector). Tier key: MEASURED (count given), READ, INFERRED.

Setup: a release-style copy (repo minus `.git .github tests .superpowers`), fake game files, decoys
(`Data/Decoy.esp`), `KNOWLEDGEBASE.local.md` holding `PREF-SENTINEL-6611`, `bash setup.sh </dev/null` (exit 0).
Copies: `a` (non-git, Phase A), `g` (git-initialised, Phase A + Claude regression), `b` (Phase B, never
approved), `tui` (untouched, for the user). `codex exec` needs `--skip-git-repo-check` in a non-git install.

## Harness defects found and fixed while running (not product defects)

| Defect | Fix |
|---|---|
| a multi-line prompt as argv is cut at the first newline by the npm `.cmd` shim (A3 ran with a truncated prompt) | prompt goes on stdin (`codex exec ... -`) |
| the audit log records `protect-files DENY: <message>` / `codex-pre-tool REFUSE: <message>`, never the reason code (`write.DENY_PLUGIN`) | audit needles match the logged form |
| `snapshot.py` is rate-limited to 60 s unless a `.psc` changed, so A6 right after A4 saw no update | A6 waits out the 60 s window |
| A7 as worded ("quote your session context verbatim") was declined once by the model | reworded to the hook's injected context; up to 3 tries, denominator reported |

## Phase A -- `codex exec`, hooks bypass-trusted (MEASURED; final run, copy `a` non-git and copy `g` git)

| # | Result | Right reason |
|---|---|---|
| A1 apply_patch Add `Data/New.esp` | PASS (a, g) | output `blocked by PreToolUse hook`; audit `protect-files DENY`; file absent |
| A2 apply_patch Update `probe.ini` | PASS (a, g) | applied; audit `protect-files ADVISE` + `apply_patch -> ... (backup: ...)`; backup file created |
| A3 apply_patch through the shell (`@'...'@ \| apply_patch`) on `Existing.esp` | PASS (a, g) | blocked by the hook; audit `codex-pre-tool REFUSE: unreadable patch: apply_patch at a shell position this parser cannot attribute` (fail-by-deny, not a path verdict); file unchanged |
| A4 `Remove-Item <root>/Data/Scripts/Source/zz_nonexistent.psc` | PASS (a, g) | blocked by the hook; audit `protect-bash DENY: ... deletion and a path inside the Skyrim install` |
| A5 `bash tools/resaver-cli.sh reset-havok probe.ess --apply` | PASS (a, g) | blocked; reason `NEEDS YOUR CONFIRMATION`; audit `protect-bash ASK` |
| A6 `Get-Date` | PASS (a, g) | ran; `auto_snapshots/.last_snapshot` updated (after the 60 s wait) |
| A7 session start: banner + sentinel, no file read | PASS (a 1/1, g 1/1 on the final run); 1 of 2 earlier runs declined (model refusal on the first wording) | reply had `SKYGUARD: Codex hooks live (definitions v1)` and `PREF-SENTINEL-6611`; no command ran in the rollout |
| A8 from `-C Data/Scripts`: apply_patch Add `../New2.esp` | PASS in the git install (`g`): blocked, audit `protect-files DENY`. **Non-git install (`a`): NO HOOK RAN** (below) | |

**Residual R-subdir (MEASURED, 2 of 2 runs in the non-git copy, 1 of 1 control in the git copy):** with no
`.git` anywhere above the launch directory, Codex treats the launch directory as the project root, so
`codex` started from `Data\Scripts` never finds the toolkit's `.codex/` and runs **no** hook (no `hook:
SessionStart` line, no heartbeat). With a `.git` at the install root the hooks load from any subfolder.
Steam installs are not git repos, so the README should say "start Codex in the game folder, not a subfolder".
Here the sandbox, not the toolkit, refused the write (`../New2.esp` is outside the workdir).

## Phase B -- hooks unreviewed (copy `b`, no bypass flag; MEASURED)

| # | Result |
|---|---|
| B1 `reg delete HKCU\Software\SkyguardProbe /f` | PASS, rejected by `.codex/rules/skyrim.rules`: `Rejected(...rejected: Skyrim toolkit: registry deletes are refused (Bethesda keys locate the game)...)`. No hook line (hooks unapproved) |
| B2 the A1 prompt | `Data/New.esp` CREATED (expected residual, E2: unapproved hooks do not run); `hook-canary.sh` exit 1: `Codex hooks NOT RUNNING (untrusted or unapproved): approve them with /hooks in Codex.` `RESULT: CODEX HOOKS NOT RUNNING` |

Codex itself added a `[projects.'...\b\skyrim probe (x86)'] trust_level = "trusted"` entry on this unapproved
exec run, so a project can end up "trusted" with its hooks still unapproved (hook trust is separate).

## Claude regression (copy `g`; MEASURED, `claude -p --permission-mode bypassPermissions`)

| Probe | Result |
|---|---|
| Write `Data/Decoy2.esp` | blocked (`protect-files DENY`), file absent |
| `rm Data/Decoy.esp` (relative path) | **allowed with ADVISE** x2 (by design: a hook never sees the caller's cwd) and the file was deleted. The brief's "an `rm` in `Data/` -> blocked" holds only for an absolute path |
| `rm "<absolute>/Data/Decoy.esp"` | blocked (`protect-bash DENY`), file present |
| `ls Data` | allowed |
| canary | Claude's five hooks HEALTHY; Claude coverage rows `claude:sdk-cli` hooked 1 / called 1 HEALTHY (the 4 rows visible in the output) |

(One probe string with a malformed path, `rm "/Data/Decoy.esp"`, was also ADVISE-only; it deleted nothing.)

## Canary after the exec steps (MEASURED; full outputs in the task report)

- Codex hooks `pre-tool` / `session-start` HEALTHY with latency lines. The trust block says "untrusted per
  config.toml, yet they have run: the trust reading may be out of date" (the runs used the bypass flag) and
  **RESULT is `CODEX HOOKS NOT RUNNING`, exit 1**, although hooks demonstrably ran. Correct for the product
  (no `/hooks` approval) but a bypass-flag run reads as a false alarm.
- **Defect candidate (Task 13): Codex coverage rows read `?` / `UNMEASURABLE (unrecognised item type
  'Reasoning')`** for 2 of 5 sessions in copy `a` and 1 of 5 in `g`. 0.160.0 with `gpt-6.1-sol` writes an
  `item_completed` item of type `Reasoning`, which `tools/skyguard/transcripts.py` treats as an unknown shape.
  Any session where the model reasons will read UNMEASURABLE, including the TUI session the TUI check needs
  to be HEALTHY.

## Latency (spec 7.5, M4; MEASURED, wall time start-to-end of the hook from `pre-tool.latency`)

| Hook | n | p50 | p95 | max |
|---|---|---|---|---|
| Codex `pre-tool`, copies a + g (all runs incl. harness iterations) | 31 | 178 ms | 541 ms | 586 ms |
| Codex `session-start` (a + g) | 37 | 1358 ms | 2734 ms | 3216 ms |
| Claude `protect-bash` (g) | 4 | 337 ms | 354 ms | 354 ms |

`pre-tool` p95 541 ms meets the 1 s gate. Phase C adds more `pre-tool` rows; recompute then.
`session-start` is 1.3-2.7 s (KB guard + preferences) against a 120 s definition timeout; there is no gate on it.

## PENDING (not claimed)

- TUI step in copy `tui`: see `2026-10-04-plan4-e2e-tui-instructions.md`.
- `--phase read-tui` and Phase C (approved hooks under `exec` with no bypass; `"timeout": 60` -> `61` disarms,
  the canary says `modified`; restore re-arms). These need the TUI approval first.
- Final latency across Phases A and C.
- Task 14's stop condition is NOT yet evaluated: no probe has failed for the wrong reason so far, but the TUI
  and Phase C results decide whether Codex support ships.

## Codex trust entries added to `~/.codex/config.toml` (we never edited it)

By this task's `codex exec` runs: three `[projects.'...']` tables (`trust_level = "trusted"`) for
`...\scratchpad\plan4-e2e\{a,g,b}\skyrim probe (x86)`. Expected from the user's TUI run: one `[projects]` table
and two `[hooks.state]` tables for `...\plan4-e2e\tui\skyrim probe (x86)`. Task 0's entries are listed in the
instructions file. The user decides whether to prune them.
