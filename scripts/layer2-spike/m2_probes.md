# Layer 2 spike — M2 probes (Plan 6 Task 4)

`m2_probes.py` drives the write routes F1–F8 against `.skyguard/state.json` in a scratch toolkit
install built from **master** (`git archive master` minus `tests/` and `.github/`, a stub
`SkyrimVR.exe`, `bash setup.sh </dev/null`), at `C:/Temp/skyguard-ml2/m2/SkyrimProbe/`.

- A scratch-only observer hook, `m2_dump_hook.py`, is registered FIRST under `PreToolUse` with
  matcher `*` in that copy's `.claude/settings.json`. It records each payload and the hook process's
  `SKYGUARD_GUARDS`, and decides nothing. "Hook-visible" for Claude means the observer saw a payload
  naming the file; for Codex it means the toolkit's own `pre-tool` heartbeat FILES were written after
  the run started (a directory's mtime is not enough — that mistake was made and fixed).
- Every route has a control: the same instruction on `.skyguard/control.json`.
- Claude runs `--model haiku --permission-mode bypassPermissions --strict-mcp-config --tools <tool>`.
  `--tools` does NOT remove MCP tools: without `--strict-mcp-config` the model reached the file through
  a Playwright MCP `browser_run_code` call (Node `fs`). The marker text is `PROBE-Fn`: with `FORGED-Fn`
  the model declined to act at all, which made every control fail.
- Codex runs `codex exec -s workspace-write`, prompt on stdin; `codex-trusted` adds
  `--dangerously-bypass-hook-trust` (the automatable equivalent of approved hooks, as in Plan 4's E2E).
- `m2_dump_hook.py systemMessage` is the Table V probe hook (returns a `systemMessage` and an
  `additionalContext` with distinct markers).
