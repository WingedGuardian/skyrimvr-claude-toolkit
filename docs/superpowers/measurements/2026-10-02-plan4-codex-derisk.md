# Plan 4 Task 0 — Codex re-measured before anything is frozen

Plan: `docs/superpowers/plans/2026-10-02-plan4-codex-target.md`, Task 0. Measured 2026-10-02 on this
machine (Windows 11 26200, shared with other sessions), in one throwaway scratch repo outside every
repo and install: `<scratch>/plan4-probes/p1` (a git repo; the hook folder is `p1/sp ace/`, with a space
on purpose). Driven with `codex exec --dangerously-bypass-hook-trust -s workspace-write` (bypass flag in
the scratch repo only). Tier key: MEASURED (count given), READ (file/binary cited), INFERRED (hedged).

## Predictions (written BEFORE any probe ran)

| Probe | Prediction |
|---|---|
| DENYME (JSON deny) | `Blocked`, flag absent |
| ASKME (JSON ask) | `Failed`, flag created (fails open) |
| EXIT1ME | `Failed`, flag created |
| EXIT2ME (stderr + exit 2) | `Failed`, flag created |
| BADJSON | `Failed`, flag created |
| SLEEPME (sleep 20 s, timeout 5) | `Failed` after ~5 s, flag created |
| R6 `failureMode: "block"` + EXIT1ME | ignored: flag created. READ before running: the 0.160.0 binary contains no `failureMode`/`failure_mode` string, and `HookHandlerConfig::Command` has 6 fields (`command, commandWindows, timeout, async, statusMessage, additionalContextLimit`). Second prediction: the unknown key does not stop the hook from loading |
| R7 CTXME 6,000 B / SessionStart 6,000 B, no limit | both tails visible. READ before running: the app-server schema calls `additionalContextLimit` a *spill threshold* in **tokens** (`null` = 2,500 tokens, `0` = no spilling) and the binary has `hooks/src/output_spill.rs`, so 6,000 B (~1,500 tokens) is under the default and the brief's 6,000 B probe cannot show truncation. Added probe: 16,000 B (~4,000 tokens) spills (tail not inline) without a limit and is inline with `8000` |
| R2 (a) leading-quote command | `Failed`, flag created |
| R2 (b) `bash "<path>"` with `C:\Windows\system32` first on PATH | `Failed` (WSL stub), flag created |
| R2 (c) `<nonexistent> … \|\| powershell …` | runs via `powershell` 5.1: `Blocked` |
| Rules `reg delete` without the bypass flag | rejected by the rule; `codex execpolicy check` → `forbidden` |
| Latency, R2 entry → `python -c pass` | pwsh p50 ~0.5 s, p95 ≤ 1 s idle; powershell 5.1 slightly faster |
| Trust hash | X4's scheme reproduces every `currentHash`; `failureMode` does not change the hash; `additionalContextLimit: 8000` does |
| Rollout surface | `session_meta.payload.originator = "codex_exec"` |
| `claude -p` transcript | `entrypoint = "sdk-cli"` |
