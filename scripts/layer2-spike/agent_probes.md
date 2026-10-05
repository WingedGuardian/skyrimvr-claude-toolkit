# Layer 2 spike — agent probes (Plan 6 Task 2)

Driver: `agent_probes.py` (Python rather than the plan's `.sh`: the verdict needs hashes, ACL
re-reads and JSON transcripts, and the driver reuses `lockprobe.py`/`sandbox.py` in-process).

```
python agent_probes.py claude <M-A|M-B|M-C|M-D> --out r.json
python agent_probes.py codex  <mech> [--sandbox workspace-write|danger-full-access] --out r.json
python agent_probes.py codex-unlock M-A --sandbox <mode> --out r.json   # unlock in-session, all 4 mechs
```

Rules the driver enforces:

- Every file lives under `C:/Temp/skyguard-ml2/agent-<agent>/…`; every lock call is sandbox-asserted.
- Each locked target has an unlocked control given the same instruction. A control that did not change
  makes the row UNMEASURED.
- Claude runs `--safe-mode` (no plugins, no hooks) with `--tools` limited to the tools under test.
- Codex gets its prompt on stdin (the npm `.cmd` shim cuts multi-line arguments).
- After each run every file is unlocked from OUTSIDE the agent and verified clean (`stuck` lists any
  that are not; a non-empty `stuck` is a stop condition).
- `~/.codex/config.toml` is copied before the first Codex run and diffed after; it is never edited.
