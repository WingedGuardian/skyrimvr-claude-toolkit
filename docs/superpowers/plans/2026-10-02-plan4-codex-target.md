# Plan 4 — Codex target (adapter, frozen hooks, rules, conformance, live E2E)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Guard/engine work: a fresh implementer **and** a reviewer per task (roadmap cadence).

**Goal:** Make the toolkit's blocking guards, backups, snapshots and session-start checks work under
OpenAI Codex CLI — on both CLI surfaces (interactive TUI and `codex exec`) — with **strict, live-tested
parity** for blocking (D2, D2a). Codex can only be stopped by one thing, a parsed JSON
`permissionDecision:"deny"`; every other outcome (ask, exit 2, exit 1, crash, malformed output, timeout,
missing script, untrusted or modified hook) **runs the command**. So everything in this plan is built
around one property: *every path out of a Codex hook is either the engine's verdict or a deny.*

**Architecture:** The engine (`tools/skyguard/`) gains three agent-neutral things, each in its own task so
Plan 5 can consume them without touching Codex code: a verdict **evaluation status + reason code**, a
**request contract** with one dispatch module (`engine.py`), and **health telemetry**
(invocation → receipt → evaluation → emission, recovery only by a newer completed evaluation). A Codex
adapter (`adapters/codex.py`) maps Codex payloads (`Bash` = PowerShell text, `apply_patch` patch text, a
patch run through the shell, unknown/MCP tools, `SessionStart`) to requests, through one shared
apply_patch path parser, and renders only deny-JSON or `additionalContext`. Two entry wrappers
(`skyguard-entry.ps1` for Windows, `skyguard-entry.sh` for POSIX) are the fail-closed boundary. They are
called by **two frozen hook definitions** in `.codex/hooks.json`. A generated `.codex/rules/skyrim.rules`
is the second line, the one that holds while the hooks are unapproved. The generator gains a Codex target
(`AGENTS.md` = core + `codex.md`, `.agents/skills/`, `.codex/**`), `setup.sh` configures it, and the canary
reports Codex trust and per-surface hook coverage (D14).

**Tech Stack:** Python ≥ 3.8 syntax, stdlib only (3.10 local, 3.11 CI); PowerShell 5.1 + 7 (ASCII-only
script); bash; pytest 8; `codex-cli` (version recorded in Task 0); GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-30-universal-agent-support-design.md` — §5 adapters and contract,
§5.1 fail-by-deny, §7.3–7.5 conformance/mutants/live E2E, §8 trust step, D2/D2a/D9/D14/D15/D16, E1–E7,
E17–E19, E23–E26. Roadmap row 4 + its *(Codex)* audit items:
`C:/Users/Moona/.claude/plans/looks-good-lovely-fern.md`. Measured facts:
`memory/kb-codex-hooks-measured.md`, `docs/superpowers/measurements/2026-10-01-plan1-derisk.md`,
`docs/superpowers/measurements/2026-10-02-ask-and-analysis-failure-audit.md`. House style:
`docs/superpowers/plans/2026-10-01-plan2-skyguard-engine.md`.

**Prior art (evidence, not truth — re-measured in Task 0):** the X4 toolkit's lane-B Codex work in
`C:/Users/Moona/Projects/x4-p2-b` — `docs/superpowers/measurements/2026-10-02-codex-0160.md` (Codex
0.160.0) and `agent/guards/adapters/{codex.py,codex-entry.ps1,codex_trust.py,patch_paths.py}`. Its
findings that change this plan are folded in below and each is re-measured on our own definitions before
anything depends on it: `bash` in a hook command resolves by the launcher's PATH and can hit the WSL stub
(silent fail-open); a hook command **starting with `"` fails**; `apply_patch` run **through the shell**
arrives as `Bash` and is applied; payload `cwd` is the session's subdirectory, not the project root;
the trust hash is reproducible from `codex app-server` `hooks/list`; `additionalContextLimit` (default
2500) is a hashed definition field.

## Assumed base (verify in Task 0 Step 1; stop if any is false)

Plan 4 starts on `master` **after all of**:
1. **Plan 2's final review and fix pass is committed.** This includes the work that is *uncommitted* in the
   tree as of 2026-10-02 (the fork-free wrapper rewrite in `agent/guards/adapters/claude/*.sh`, the
   `setup.sh` regular-install-over-Store-alias interpreter choice, `tests/bin/{py,python,python3}`), and
   the latency measurement (`scripts/guard-latency.py`) recorded in `2026-10-02-plan2-e2e.md`.
2. The Codex harness-repairs branch (`codex/harness-repairs-fd71271`) is reviewed and merged (roadmap).
3. **Branch `plan3` is merged, including its Task 8** (`tools/skyguard/prefs.py`, preferences injected by
   `session.run`), so `gen_agent_trees.py` has `INSTRUCTION_BUDGET`, `CODEX_ADDENDUM_RESERVE`,
   `instruction_sizes()`, and the measured headroom for `codex.md` is known (Plan 3 measurements: the
   reserved row is 31,305 / 32,768, so **`codex.md` may be at most 2,048 + 1,463 = 3,511 bytes**;
   this plan targets ≤ 3,000).

Plan 5 runs in parallel (own worktree). Plan 4 **does not edit** `tools/skyguard/shell_rules.py`,
`write_rules.py`, `backup.py`, `snapshot.py`, `session.py`, `paths.py`, or `agent/targets/claude/settings.json`.
Where Plan 4 needs their behaviour it calls their existing functions; the interfaces Plan 5 consumes are
declared below.

## Rulings (each confirmable at plan review)

- **R1. Two hook definitions, no more:** `SessionStart` (no matcher) and `PreToolUse` with matcher `".*"`.
  No `PostToolUse`. — *Why:* every definition is a PowerShell start on every matching call and one more
  frozen thing users must approve (E18). `".*"` already routes MCP and unknown tools to the engine, so
  Plan 5's DevBench MCP rules need no definition change. — *Cost if wrong:* a later `PostToolUse` need
  costs one "re-approve your Codex hooks" release; batch it with D16 if that ever lands.
- **R2. Windows entry = `pwsh -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "<root>/.codex/hooks/skyguard-entry.ps1" <role> || powershell <same>`**,
  in `commandWindows`; POSIX entry = `bash "<root>/.codex/hooks/skyguard-entry.sh" <role>` in `command`.
  Program first, quoted path after; never a leading `"`; never `bash` on Windows. — *Why:* X4 measured a
  leading quote failing 3/3 and `bash` resolving to the WSL stub when Codex is launched from PowerShell;
  both are silent fail-opens. Task 0 re-measures both. — *Cost if wrong:* PowerShell cold start is paid on
  every tool call (Risk 2, latency).
- **R3. The Python path lives in the entry script, never in a definition.** `setup.sh` fills
  `{{PYTHON_PATH}}` in `skyguard-entry.{ps1,sh}`. — *Why:* a definition change silently disarms the
  approved hook (E18); a Python upgrade must not do that. — *Cost if wrong:* none known.
- **R4. `ask` becomes deny in the Codex adapter, generically**, with a one-line prefix that tells the agent
  to stop and that the user can run it themselves. No `.rules` `prompt` for ReSaver. — *Why:* an ask fails
  open on Codex (E1). A `prompt` prefix rule on `resaver-cli.sh reset-havok` would also prompt every
  dry-run (approval fatigue, user 2026-10-02) and could not see `--apply` anywhere but the end. ReSaver
  writes a new file and is verify-gated, so "the user runs it" is the escape. Whether Codex's own `!`
  user-shell escape bypasses PreToolUse is **measured in the TUI step** and only then named in the text.
  — *Cost if wrong:* a user who wanted an in-session approval gets a refusal and runs it by hand.
- **R5. `.rules` carries only prefix rules that are a strict subset of an existing hook deny** — today
  `reg delete …` and `reg.exe delete …` (the hook denies *every* `reg delete`). Every shell-rule verdict
  constant is classified `forbidden | prompt | hook_only` with a reason, and a test fails on any
  unclassified constant. — *Why:* a broader prefix is a new false positive with no hook to explain it, and
  `.rules` is the only layer that holds while the hooks are unapproved (E19). — *Cost if wrong:* the
  unapproved-hooks state protects only registry deletes; Layer 2 (Plan 6) and the canary carry the rest.
- **R6. `failureMode: "block"` goes into the v1 definitions only if Task 0 proves it on the then-current
  Codex** (a hook exiting 1 must block). Otherwise it is left out and D16 stays open. — *Why:* no release
  has shipped Codex definitions yet, so adopting it now costs no re-approval; later it costs one. —
  *Cost if wrong:* nil either way; the wrapper is fail-closed regardless.
- **R7. `additionalContextLimit` is decided in Task 0, before the freeze.** If the field is honoured and
  the default (2,500) truncates a realistic SessionStart (kb-guard output + up to 4,096 bytes of
  preferences, Plan 3 R5), the definition sets it to 8,000; otherwise the adapter caps its own output with
  a visible "truncated — read KNOWLEDGEBASE.local.md" notice. — *Why:* it is a hashed definition field.
  — *Cost if wrong:* preferences silently cut on Codex.
- **R8. Reason codes are derived from constant names** (`shell.DENY_INSTALL`, `write.DENY_PLUGIN`, …) by
  matching the reason/context text against the rule modules' own template constants. No rule module is
  edited. Plan 5 may pass `code=` explicitly; derivation is the fallback. — *Why:* Plan 5 owns those
  files. — *Cost if wrong:* an ambiguous template pair; a test proves every corpus row maps to exactly one
  code.
- **R9. Evaluation status means:** `evaluated` = every rule and side effect ran; `partial` = the verdict is
  valid but a side effect (backup, snapshot, telemetry) failed; `failed` = no trustworthy verdict (blocking
  roles then deny). It is **separate from the decision**: an INERT deny is `decision=deny, status=failed`.
- **R10. Telemetry for every agent lives under `.claude/backups/.hook-heartbeat/<agent>/`** (Plan 3 R2
  allows `.claude/backups/` in neutral text; Plan 8 may move it). The new canary **ignores the 3.x
  heartbeat files** (`.hook-heartbeat/<hook>`, `<hook>.blind`) so a stale 3.x BLIND cannot pin it.
- **R11. The Python fallback chain applies to the Claude wrappers too**, engaged only when the configured
  interpreter fails (hot path unchanged), and a fallback is recorded as DEGRADED.
- **R12. `AGENTS.md` = core + `codex.md`** for Codex *and* every unknown agent. `agents-pointer.md` is
  retired. The Claude target stops rendering `AGENTS.md`; a test forbids two targets owning one path.
- **R13. `setup.sh` configures the Codex files unconditionally** (Python placeholder, hooks.json root,
  `AGENTS.md` paths) and prints the approval step. `--agent` is Plan 8.
- **R14. The hook-definition freeze is a committed hash ledger** (`agent/targets/codex/FROZEN.sha256`) plus
  a CHANGELOG rule, **and** a tag comparison once a tag carries Codex definitions. — *Why:* no tag carries
  them yet, so a tag comparison alone would pass vacuously.
- **R15. Surfaces claimed after this plan: Codex `exec` and the interactive TUI**, each only after its live
  E2E passes. The desktop app and IDE extensions are **not claimed** (D14); the canary's per-session
  coverage shows what they do.
- **R16. Harness-repairs item (d)** (MO2 helper precedence into the common path interface, keeping
  `PLUGINS_TXT`) **goes to Plan 5**, which owns the path interface. Plan 4 takes none of it.

## Global Constraints

- **Every PreToolUse path out of a Codex wrapper is a validated engine verdict or a JSON deny.** Exit 0
  always; never exit 2; never `"ask"` or `"allow"` as a `permissionDecision`; never more than one JSON
  object; never a leading `"` in a hook command. Mutants prove each (Task 12).
- **Claude behaviour is byte-identical.** `python -m pytest tests/test_guard_corpus.py -q` passes with zero
  edits to `tests/guard_corpus/expected.jsonl` after every task. The only Claude-visible changes are the
  telemetry layout (Task 4), the fallback chain (Task 5) and `AGENTS.md` content (Task 10).
- **Hook definitions are frozen once Task 8 lands.** All behaviour lives in the scripts they call.
- **Engine floor Python 3.8** (`test_engine_parses_on_python_3_8` covers every new `tools/skyguard/*.py`).
- **PowerShell scripts are ASCII-only** (Windows PowerShell 5.1 reads BOM-less files as ANSI) and run on
  5.1 and 7. **Never log the environment** (hooks inherit all of it, secrets included; X4 MEASURED).
- **Generated files:** `agent/` is the source; regenerate, never hand-edit. LF everywhere (`.gitattributes`).
- **Live Codex runs happen only in scratch copies**, never in a user install. `codex exec` writes
  `trust_level = "trusted"` entries for every folder it runs in to `~/.codex/config.toml` (X4 MEASURED);
  **we never edit that file** — list the scratch paths for the user at the end instead.
- Commit each finished task on `master`; **never push.** Test command:
  `python -m pytest tests/ -q -p no:cacheprovider`. **Never edit the tree while the suite, a mutation run
  or a live E2E is running.**

## Review Focus

1. **A fail-open path nobody exercised.** pwsh missing, `powershell` 5.1 only, a path with spaces, a
   `(x86)` path, the engine hanging past the wrapper's own deadline, the engine printing two lines or a
   BOM, an exception before the first byte is written. → `test_codex_entry.py` fault matrix (Task 7); each
   case asserts the *deny JSON*, not merely "not allow".
2. **Definition drift that disarms silently.** Any byte of a definition that varies by machine or run
   (path case, slashes, a trailing space, setup.sh re-run, a moved install). → Task 8 freeze gate +
   `test_setup_renders_definitions_deterministically` (Task 11) + canary trust report (Task 13).
3. **A patch path the guard never sees:** `*** Move to:`, a multi-file patch, a header-like line inside a
   hunk (`+*** Delete File: x`), a patch run through the shell (`apply_patch <<'PATCH'`), a `cd sub &&`
   prefix, a relative path from a subdirectory `cwd`, a path with `..`. → `test_patch_paths.py` (Task 3),
   adapter tests (Task 6), a mutant that drops Delete headers (Task 12).
4. **Telemetry that reads green wrongly.** Recovery by a newer *receipt* instead of a completed
   evaluation; equal timestamps; an unparseable record; a 3.x leftover; an invocation that never finished
   (the agent's timeout killed it). → `test_skyguard_health.py` (Task 4), each with a twin.
5. **Interfaces Plan 5 depends on changing under it** (`Verdict` field order, `engine.evaluate_shell`
   signature, the classification test). → `test_plan5_interface.py` pins them (Task 2).

## Risks (ranked)

1. **The fail-open root cannot be closed from inside the hook.** If PowerShell cannot start, the hooks are
   unapproved, or a definition byte drifted, Codex runs the command and says nothing (E2, E17, E18). The
   wrapper covers everything after it starts; before that, only `.rules` (registry deletes, R5), the
   session banner (model compliance ASSUMED), the canary's trust and coverage report, and Layer 2 (Plan 6)
   remain. Mitigated, not closed; the README says so.
2. **Latency.** Every Codex tool call pays a PowerShell cold start plus Python. X4 measured its full chain
   at p50 1.1–2.1 s, p95 1.3–5.8 s on this machine; the spec gate is p95 ≤ 1 s. Task 0 measures the bare
   entry first, with a pre-committed fallback ladder and a user decision if all rungs fail.
3. **Hashed-definition choices made before evidence is complete.** `failureMode`, `additionalContextLimit`,
   the matcher, timeouts and the command text are frozen in Task 8; a wrong choice costs every user a
   re-approval later. Task 0 measures each before Task 8 commits the template.
4. **Transcript formats** (Codex is migrating rollouts to "paginated thread history" — `codex
   migrate-rollouts`) make the D14 coverage check fragile. The parser recognises measured shapes only and
   reports UNMEASURABLE otherwise, never a guess.
5. **Cross-plan churn with Plan 5** in `run.py`/`engine.py` and the classification test; pinned by
   `test_plan5_interface.py` and the declared merge rule below.

## Interfaces handed to Plan 5 (declared here, pinned by `tests/test_plan5_interface.py`)

- `skyguard.verdict.Verdict(decision="allow", reason="", context="", status="evaluated", code="")` — the
  first three fields keep their order, so every existing `Verdict("deny", X)` is unchanged. Plan 5 may
  pass `code=` / `status=`; if it does not, `codes.classify()` derives the code from the constant name.
- `skyguard.codes.classify(v: Verdict) -> Verdict` — fills `code` (and `status="failed"` for `NO_*`
  constants). **A new `DENY_*`/`ASK_*`/`ADV_*`/`NO_*` string constant in `shell_rules.py` or
  `write_rules.py` is coded automatically by its name.**
- `skyguard.request.Request` — the spec §5 contract (`v, agent, event, kind, shell, command, paths, ops,
  mcp_tool, mcp_args, cwd`, plus `tool_name, session_id, transcript_path, source, empty, failure`).
  `shell` is `"powershell"` for Codex on Windows, `"bash"` for Codex on POSIX and Claude `Bash`.
  **`cwd` is filled from the payload; Plan 5's relative-path resolution consumes it.**
- `skyguard.engine.evaluate_shell(req, roots, audit) -> Verdict` is the **only** call site of
  `shell_rules.decide`, and `engine.evaluate_writes(req, audit)` the only Codex call site of
  `write_rules.decide`. When Plan 5 adds `cwd=`/`shell=` parameters to `decide`, it edits those two lines.
- `skyguard.codex_rules.CLASSIFICATION` — every `DENY_*`/`ASK_*`/`NO_*` constant of `shell_rules.py` must
  have a row (`forbidden|prompt|hook_only`, reason). `test_every_shell_verdict_is_classified` goes red when
  Plan 5 adds one; Plan 5 adds the row (and, if `forbidden`/`prompt`, the prefix rule).
- **Codex needs nothing from Plan 5's ask triage:** any `ask` reaches Codex as a deny (R4).
- Merge order: whichever of Plans 4/5 lands second rebases onto the other; the conflict surface is
  `tools/skyguard/run.py` (Plan 4 moves the Claude dispatch into `engine.py`) and
  `tests/test_mutations.py` (append-only on both sides).

---

### Task 0: Preconditions, then re-measure Codex on the current version (de-risk; user needed for ~10 min)

Nothing here lands in the repo except a measurements doc and recaptured fixtures. **If a result
contradicts this plan, stop and amend the plan before Task 1** (the pre-committed fallback for each probe
is listed with it).

**Files:**
- Create: `docs/superpowers/measurements/2026-10-XX-plan4-codex-derisk.md`
- Create: `tests/fixtures/payloads/codex/<version>/*.json` (scrubbed captures) and update
  `tests/fixtures/payloads/README.md`
- Create: `tests/fixtures/codex/<version>/trust_vectors.json`, `tests/fixtures/codex/<version>/rollout-*.jsonl`
  (scrubbed transcript excerpts for Task 13)

- [ ] **Step 1: Preconditions.**

```bash
cd /c/Users/Moona/Projects/skyrimvr-claude-toolkit
git status --short                                   # Expected: empty
git log --oneline -40 | grep -i "plan 3\|prefs\|preferences" # Expected: Plan 3 merge commits present
test -f tools/skyguard/prefs.py && echo prefs-ok    # Expected: prefs-ok
grep -c "CODEX_ADDENDUM_RESERVE" scripts/gen_agent_trees.py   # Expected: >= 2
grep -c 'printf -v NOW' agent/guards/adapters/claude/protect-bash.sh  # Expected: 1 (Plan 2 fork-free wrapper committed)
python scripts/gen_agent_trees.py --budget           # Expected: the "codex.md (reserved)" row; note its headroom
git rev-parse HEAD                                   # record as BASE in the measurements doc
codex --version; claude --version; pwsh -v; powershell -NoProfile -Command '$PSVersionTable.PSVersion.ToString()'
```
Any miss → stop and report which precondition failed.

- [ ] **Step 2: Build the scratch probe repo** (outside every repo and install; one throwaway folder).
  Write `probe-hook.py` (stdlib): it appends the raw payload to `raw/<stamp>.json` (as
  `tools/payload-capture/capture-hook.sh` does) and then answers by marker in the payload text:
  `DENYME` → JSON deny; `ASKME` → JSON `"ask"`; `EXIT1ME` → exit 1; `EXIT2ME` → stderr + exit 2;
  `BADJSON` → prints `{"hookSpecificOutput":`; `SLEEPME` → sleeps 20 s with the definition timeout at 5;
  `CTXME` → `additionalContext` of 6,000 bytes whose last 20 bytes are `TAILSENTINEL-4471`; otherwise
  prints nothing. A `SessionStart` prints 6,000 bytes ending `SESSIONTAIL-8812`. Write
  `.codex/hooks.json` using the **R2 form** (`commandWindows` = `pwsh … -File "<scratch>/sp ace/probe.ps1" || powershell …`,
  where `probe.ps1` pipes stdin to `python probe-hook.py`; the folder name contains a space on purpose).

- [ ] **Step 3: Run the exec probes** (each `codex exec --dangerously-bypass-hook-trust -s workspace-write "<prompt>" < /dev/null`,
  one marker per run, the prompt asks for `New-Item -ItemType File <MARKER>.flag`). Record, per marker:
  the status line (`Blocked`/`Failed`/none), whether `<MARKER>.flag` exists. Predictions (write them in
  the doc BEFORE running): DENYME blocks; ASKME, EXIT1ME, EXIT2ME, BADJSON, SLEEPME run (fail open).
  - **R6 probe:** add `"failureMode": "block"` to the hook entry and rerun EXIT1ME. Prediction: ignored
    (E25). If it **blocks**, record it; R6 then puts it in the v1 template.
  - **R7 probe:** CTXME and a SessionStart: ask the model to repeat the tail sentinel. Then add
    `"additionalContextLimit": 8000` and repeat. Record whether each sentinel is visible with and without.
  - **R2 probes:** (a) the same hook with a leading-quote command; (b) `bash "<path>/probe.sh"` launched
    from a `pwsh -NoProfile` terminal whose PATH puts `C:\Windows\system32` first; (c) the `||` fallback
    with a nonexistent first program. Predictions: (a) Failed, (b) Failed, (c) runs via `powershell`.
  - **Shapes:** one run each for: `apply_patch` Add, Update, Delete, Update+`Move to`, a two-file patch,
    `apply_patch <<'PATCH'` through the shell, a `cd sub && …` shell call, and a session started with
    `-C sub/deep` (record payload `cwd`). If a toy stdio MCP server is quick to stand up
    (`python -m` a 40-line stdlib JSON-RPC echo server registered with `codex mcp add`), capture one MCP
    tool payload; otherwise record "MCP: not captured" (Plan 5 owns DevBench MCP).
  - **Rules:** a project `.codex/rules/probe.rules` with
    `prefix_rule(pattern = ["reg", "delete"], decision = "forbidden", justification = "probe")`, then ask
    for `reg delete HKCU\Software\SkyguardProbe /f` **without** `--dangerously-bypass-hook-trust` (hooks
    unreviewed). Prediction: rejected by the rule (E19). Also `codex execpolicy check --rules
    .codex/rules/probe.rules reg delete HKCU\Software\SkyguardProbe /f` → forbidden.
  - **Latency:** time 30 runs of the R2 entry form (pwsh → `python -c pass`) and of `powershell` 5.1 alone,
    by feeding a fixture payload to the exact `commandWindows` string through `cmd /c`. Record p50/p95.
- [ ] **Step 4: Trust-hash oracle.** Using `codex app-server` (`hooks/list`, as in X4's lane-B method)
  with the **two Skyrim v1 definitions** (Task 8 Step 1's template, with and without the R6/R7 fields) passed as `-c hooks.<Event>=[...]` session flags, rendered for the
  neutral root `C:/SkyguardVectors/Skyrim Probe (x86)` (no personal path; the folder need not exist),
  record each `currentHash` to `tests/fixtures/codex/<version>/trust_vectors.json`, plus three neutral
  variants (timeout 60 vs 61; matcher `.*` vs `^Bash$`; with/without `statusMessage`). Task 8's
  `codex_trust.hook_hash` must reproduce all of them.
- [ ] **Step 5: Transcript shape (for Task 13).** From one exec run, read the rollout file at the payload's
  `transcript_path`: record (a) which line/field names the surface (prediction: `session_meta.payload`
  `originator`/`source`, e.g. `codex_exec` vs the TUI's value), (b) the item types and names for a shell
  call, an `apply_patch` call and an MCP call. Commit a scrubbed excerpt (ids → `<id>`, home → `<HOME>`,
  message text → `<text>`) as `rollout-exec.jsonl`. Do the same for one `claude -p` transcript
  (`entrypoint` prediction: `sdk-cli`) as `tests/fixtures/claude/transcript-p.jsonl`.
- [ ] **Step 6: USER STEP — TUI probe (~10 min; needs a real terminal).** Give the user exactly this,
  with `<S>` replaced by the scratch path:

  ```text
  Open a NEW Windows Terminal tab (PowerShell), then:
    cd "<S>"
    codex
  In Codex:
    /hooks            -> approve BOTH hooks listed for this folder (SessionStart, PreToolUse)
    Type:  Run New-Item -ItemType File DENYME.flag in the shell, then run Get-Date.
    Type:  !echo skyguard-bang-probe
    Type:  /compact
    Type:  What is the last word of your session-start context? Reply with that word only.
    /quit
  Then tell me "TUI done".
  ```
  Then read `raw/` and `~/.codex/config.toml` (read-only) and record: (a) PreToolUse fired in the TUI and
  DENYME was **Blocked** (the D14 must-have); (b) whether `!echo skyguard-bang-probe` produced a
  PreToolUse (decides R4's escape text); (c) a `SessionStart` with `source: "compact"` (captured as a
  fixture); (d) the TUI rollout's surface value; (e) the `[hooks.state.'<…>']` keys and `trusted_hash`
  values Codex wrote for the scratch hooks — Task 8's `expected_entries()` must reproduce them.
  **Stop condition:** if PreToolUse does not fire in the TUI, or a JSON deny does not block there, stop:
  D2 says Codex support does not ship without CLI parity. Report to the user with the evidence.
- [ ] **Step 7: Recapture fixtures.** Scrub `raw/` with `tools/payload-capture/scrub.py --agent codex` into
  `tests/fixtures/payloads/codex/<version>/`, check no `Moona` survives
  (`grep -rl Moona tests/fixtures && echo LEAK`), update the fixtures README (version, prompts, what each
  file is, which were not captured).
- [ ] **Step 8: Write the measurements doc** (one table row per probe: prediction, result, count, tier,
  plan change) and **amend this plan** where a result changed a ruling (R2/R6/R7/R4 text, latency risk).
  Commit:
  `git add docs/superpowers/measurements tests/fixtures && git commit -m "Plan 4 Task 0: Codex <version> re-measured (fail-open matrix, failureMode, context limit, TUI, trust hash, transcripts)"`

**Pre-committed fallbacks:** latency p95 of the bare entry > 1 s → try `-NoLogo`, then PowerShell 5.1
first, then measure `cmd /c <short .cmd>`; if all exceed 1 s, **ask the user** whether the Codex latency
gate becomes the measured value (README states it) or Codex waits. Rollout format not parseable → Task 13
reports per-session coverage as UNMEASURABLE (never guessed). `hooks/list` gives no hashes → Task 8
derives vectors from the TUI-approved `trusted_hash` values of Step 6 instead.

---
### Task 1: Verdict evaluation status + reason code (shared engine interface, no rule-module edits)

**Files:**
- Modify: `tools/skyguard/verdict.py`
- Create: `tools/skyguard/codes.py`
- Modify: `tools/skyguard/run.py` (classify before render; no output change)
- Test: `tests/test_skyguard_codes.py` (create)
- Modify: `tests/test_mutations.py` (one entry)

**Interfaces:** Produces `Verdict(decision, reason, context, status="evaluated", code="")`,
`verdict.DECISIONS`, `verdict.STATUSES`, `codes.TEMPLATES`, `codes.matches(text, whole) -> list[str]`,
`codes.classify(v) -> Verdict`. Consumed by Tasks 2, 4, 6 and by Plan 5 (see *Interfaces handed to Plan 5*).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_skyguard_codes.py
"""Verdicts carry a stable reason code and an evaluation status, separate from the decision (Plan 4 R8/R9)."""
import json
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import codes, shell_rules, write_rules  # noqa: E402
from skyguard.verdict import STATUSES, Verdict  # noqa: E402

CORPUS = REPO / "tests" / "guard_corpus" / "expected.jsonl"


def test_positional_construction_is_unchanged():
    assert Verdict("deny", "x") == ("deny", "x", "", "evaluated", "")
    assert Verdict()._fields[:3] == ("decision", "reason", "context")
    assert STATUSES == ("evaluated", "partial", "failed")


def _constants():
    for ns, mod in (("shell", shell_rules), ("write", write_rules)):
        for name, value in sorted(vars(mod).items()):
            if isinstance(value, str) and name.startswith(("DENY_", "ASK_", "ADV_", "NO_")) and not name.endswith("_LOG"):
                yield "{}.{}".format(ns, name), value


@pytest.mark.parametrize("code,template", list(_constants()), ids=[c for c, _ in _constants()])
def test_every_rule_constant_codes_to_itself(code, template):
    text = template.replace("{c}", "rm -rf X").replace("{p}", "C:/X/Data/a.ini")
    whole = code.split(".", 1)[1].startswith(("DENY_", "ASK_", "NO_"))
    assert codes.matches(text, whole) == [code]


def test_every_corpus_row_maps_to_exactly_one_code():
    """Runs over every recorded verdict text; an ambiguous template pair would show up here."""
    seen = 0
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["decision"] == "allow" or not row["text"]:
            continue
        found = codes.matches(row["text"], row["decision"] in ("deny", "ask"))
        assert len(found) == 1, (row["id"], found)
        seen += 1
    assert seen >= 50, "corpus suspiciously small: {} coded rows".format(seen)


def test_inert_refusals_are_failed_not_evaluated():
    v = codes.classify(Verdict("deny", shell_rules.NO_COMMAND_DENY))
    assert (v.decision, v.status, v.code) == ("deny", "failed", "shell.NO_COMMAND_DENY")


def test_a_rule_deny_is_evaluated():
    v = codes.classify(Verdict("deny", write_rules.DENY_PLUGIN))
    assert (v.status, v.code) == ("evaluated", "write.DENY_PLUGIN")


def test_advice_is_coded_by_its_first_item():
    ctx = write_rules.ADV_PAPYRUS.format(p="a.psc") + " | " + write_rules.ADV_INSTALL.format(p="x")
    assert codes.classify(Verdict("allow", "", ctx)).code == "write.ADV_PAPYRUS"


def test_plain_allow_is_coded_allow():
    assert codes.classify(Verdict()).code == "allow"


def test_an_explicit_code_is_kept():
    assert codes.classify(Verdict("deny", "anything", code="plan5.X")).code == "plan5.X"


def test_unknown_text_is_coded_unknown_never_guessed():
    assert codes.classify(Verdict("deny", "some text no rule wrote")).code == "unknown"
```

- [ ] **Step 2: Run them** → `python -m pytest tests/test_skyguard_codes.py -q` → FAIL (`ImportError: codes`,
  `STATUSES`).

- [ ] **Step 3: Extend the verdict**

```python
# tools/skyguard/verdict.py
"""Verdict and advice for the guard engine.

A verdict carries two independent facts (Plan 4 R9):
  decision -- what the agent is told: allow | ask | deny
  status   -- how far the engine got: evaluated | partial | failed
and a stable reason CODE naming the rule that decided (codes.py). An INERT refusal is decision=deny,
status=failed: the agent is stopped AND the telemetry records that nothing was checked. The first three
fields keep their order, so every Verdict("deny", reason) written before Plan 4 is unchanged.
"""
from __future__ import annotations

from typing import List, NamedTuple

DECISIONS = ("allow", "ask", "deny")
STATUSES = ("evaluated", "partial", "failed")


class Verdict(NamedTuple):
    decision: str = "allow"        # allow | ask | deny
    reason: str = ""
    context: str = ""              # advice text for the model
    status: str = "evaluated"      # evaluated | partial | failed
    code: str = ""                 # e.g. "write.DENY_PLUGIN"; "" until codes.classify() fills it


class Advice:
    def __init__(self) -> None:
        self.items: List[str] = []

    def add(self, msg: str) -> None:
        self.items.append(msg)

    def joined(self) -> str:
        return " | ".join(self.items)
```

- [ ] **Step 4: Write the code derivation**

```python
# tools/skyguard/codes.py
"""Stable reason codes, derived from the rule modules' OWN text constants (Plan 4 R8).

The rule modules (shell_rules.py, write_rules.py) are owned by Plan 5 and are not edited here. Each
DENY_/ASK_/ADV_/NO_ string constant is a template whose {c}/{p} fields are filled at runtime, so a reason
text identifies its constant: the literal parts must match in order. A new constant is coded by its name
automatically. A rule that passes code= explicitly keeps it.

Decisive texts (deny/ask) must match a template WHOLE; advice is coded by its FIRST item (a prefix match),
since advice items are joined with " | " and a command may itself contain " | ".
"""
from __future__ import annotations

import re
from typing import List, Pattern, Tuple

from . import shell_rules, write_rules
from .verdict import Verdict

_PREFIXES = ("DENY_", "ASK_", "ADV_", "NO_")
_FIELD = re.compile(r"\{[a-z]\}")
# The Claude wrappers' own refusal (Python missing, engine failed) never reaches the engine, but it is in
# the corpus; code it so the corpus check is total.
_WRAPPER = ("wrapper.INERT", re.compile(r"GUARD INERT: \S+ .*evaluated NO rules.*", re.S))


def _templates() -> List[Tuple[str, Pattern]]:
    out = []
    for ns, mod in (("shell", shell_rules), ("write", write_rules)):
        for name, value in sorted(vars(mod).items()):
            if not isinstance(value, str) or not name.startswith(_PREFIXES) or name.endswith("_LOG"):
                continue
            rx = ".*".join(re.escape(part) for part in _FIELD.split(value))
            out.append(("{}.{}".format(ns, name), re.compile(rx, re.S)))
    out.append(_WRAPPER)
    return out


TEMPLATES = _templates()


def matches(text: str, whole: bool) -> List[str]:
    return [code for code, rx in TEMPLATES if (rx.fullmatch(text) if whole else rx.match(text))]


def classify(v: Verdict) -> Verdict:
    if v.code:
        return v
    whole = v.decision in ("deny", "ask")
    text = v.reason if whole else v.context
    if not text:
        return v._replace(code="allow")
    found = matches(text, whole)
    if not found:
        return v._replace(code="unknown")
    code = found[0]                    # more than one is a test failure (corpus check), never decided here
    status = "failed" if ".NO_" in code or code == "wrapper.INERT" else v.status
    return v._replace(code=code, status=status)
```

- [ ] **Step 5: Call it in `run.py`** — replace the last two lines of `main()`
  (`_emit(adapter.render(v))` / `return 0`) with:

```python
    v = codes.classify(v)
    _emit(adapter.render(v))
    return 0
```
  and add `codes` to the `from skyguard import …` line. The Claude adapter renders only
  `decision/reason/context`, so its output is byte-identical.

- [ ] **Step 6: Run** → `python -m pytest tests/test_skyguard_codes.py tests/test_guard_corpus.py tests/test_skyguard_claude_adapter.py -q`
  → all PASS, `expected.jsonl` untouched (`git diff --stat tests/guard_corpus` → empty). If a corpus row
  maps to 0 or 2 codes, read it: a 0 is a text no constant produced (fix `codes.py`, never the corpus); a
  2 is an ambiguous template pair (make the earlier template's literal longer in `codes.py` by anchoring,
  never edit the rule module).

- [ ] **Step 7: Mutation entry** (append to `MUTATIONS` in `tests/test_mutations.py`):

```python
    # An INERT refusal must be recorded as "nothing was checked", not as a rule decision. Fold the
    # status back into the decision and the telemetry would read a blind guard as a working one.
    pytest.param(
        "tools/skyguard/codes.py",
        'status = "failed" if ".NO_" in code or code == "wrapper.INERT" else v.status',
        "status = v.status",
        "tests/test_skyguard_codes.py::test_inert_refusals_are_failed_not_evaluated",
        id="inert-refusal-is-status-failed",
    ),
```
  Run: `python -m pytest "tests/test_mutations.py::test_guard_actually_bites[inert-refusal-is-status-failed]" -q` → PASS (killed).

- [ ] **Step 8: Full suite, then commit**

```bash
python -m pytest tests/ -q -p no:cacheprovider
git add tools/skyguard/verdict.py tools/skyguard/codes.py tools/skyguard/run.py tests/test_skyguard_codes.py tests/test_mutations.py
git commit -m "skyguard: verdicts carry an evaluation status and a reason code, separate from the decision"
```

---

### Task 2: The request contract and one dispatch module (Claude output unchanged)

**Files:**
- Create: `tools/skyguard/request.py`, `tools/skyguard/engine.py`
- Modify: `tools/skyguard/run.py`, `tools/skyguard/adapters/claude.py` (add `to_request`)
- Test: `tests/test_skyguard_engine.py`, `tests/test_plan5_interface.py` (create)

**Interfaces:**
- `request.Request` (NamedTuple; fields in *Interfaces handed to Plan 5*).
- `engine.evaluate_shell(req, roots, audit) -> Verdict` (classified).
- `engine.evaluate_write(path, audit) -> Verdict`; `engine.evaluate_writes(req, audit) -> Verdict`.
- `engine.combine(verdicts) -> Verdict`: first deny wins; else first ask; else advice items in order,
  de-duplicated; the worst status (`failed` > `partial` > `evaluated`).
- `claude.to_request(p: dict, kind: str) -> Request` (`p` = `extract()`'s dict).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_skyguard_engine.py
"""engine.py: the one place requests meet rules."""
import sys

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import engine, write_rules  # noqa: E402
from skyguard.request import Request  # noqa: E402
from skyguard.verdict import Verdict  # noqa: E402


class _Audit:
    def __init__(self):
        self.lines = []

    def log(self, hook, level, msg):
        self.lines.append((hook, level, msg))

    def raw(self, line):
        self.lines.append(line)

    @staticmethod
    def stamp():
        return "20261002_000000"


def test_first_deny_wins_and_keeps_its_reason():
    v = engine.combine([Verdict("allow", "", "a"), Verdict("deny", "first"), Verdict("deny", "second")])
    assert (v.decision, v.reason) == ("deny", "first")


def test_ask_beats_advice_but_not_deny():
    assert engine.combine([Verdict("allow", "", "a"), Verdict("ask", "q")]).decision == "ask"
    assert engine.combine([Verdict("ask", "q"), Verdict("deny", "d")]).decision == "deny"


def test_advice_accumulates_in_order_without_duplicates():
    v = engine.combine([Verdict("allow", "", "a"), Verdict("allow", "", "b"), Verdict("allow", "", "a")])
    assert v.context == "a | b"


def test_worst_status_survives():
    v = engine.combine([Verdict(status="evaluated"), Verdict(status="partial"), Verdict(status="evaluated")])
    assert v.status == "partial"


def test_a_multi_path_write_is_denied_by_any_plugin_path():
    req = Request(kind="write", paths=("C:/G/Data/a.ini", "C:/G/Data/b.esp"), ops=("update", "add"))
    v = engine.evaluate_writes(req, _Audit())
    assert (v.decision, v.code) == ("deny", "write.DENY_PLUGIN")


def test_a_write_request_with_no_paths_refuses():
    v = engine.evaluate_writes(Request(kind="write"), _Audit())
    assert (v.decision, v.status) == ("deny", "failed")
```

```python
# tests/test_plan5_interface.py
"""The engine interfaces Plan 5 builds on. Changing one is a cross-plan change: update both plans."""
import inspect
import re
import sys

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import engine  # noqa: E402
from skyguard.request import Request  # noqa: E402
from skyguard.verdict import Verdict  # noqa: E402

SPEC_FIELDS = ("v", "agent", "event", "kind", "shell", "command", "paths", "mcp_tool", "mcp_args", "cwd")


def test_verdict_shape():
    assert Verdict._fields == ("decision", "reason", "context", "status", "code")


def test_request_carries_every_spec_field():
    assert set(SPEC_FIELDS) <= set(Request._fields)
    assert Request().v == 1


def test_evaluate_shell_signature():
    assert list(inspect.signature(engine.evaluate_shell).parameters) == ["req", "roots", "audit"]


def test_shell_rules_decide_has_exactly_one_call_site():
    hits = []
    for f in sorted((REPO / "tools" / "skyguard").rglob("*.py")):
        n = len(re.findall(r"shell_rules\.decide\(", f.read_text(encoding="utf-8")))
        if n:
            hits.append((f.name, n))
    assert hits == [("engine.py", 1)], hits
```

- [ ] **Step 2: Run** → FAIL (`ModuleNotFoundError: skyguard.request`).

- [ ] **Step 3: Write `request.py` and `engine.py`**

```python
# tools/skyguard/request.py
"""The agent-neutral request (spec section 5 contract), built by an adapter from its agent's payload.

The spec's fields come first; the rest are what adapters already know and the engine needs (telemetry,
per-session coverage, the patch op per path). `failure` set = the adapter could not translate the payload;
a blocking role must then refuse (spec 5.1) -- it is never read as an empty, harmless request.
"""
from __future__ import annotations

from typing import NamedTuple, Optional, Tuple


class Request(NamedTuple):
    v: int = 1
    agent: str = ""
    event: str = ""                   # pre_tool | session_start | other
    kind: str = ""                    # shell | write | mcp | other | none
    shell: Optional[str] = None       # bash | powershell | None
    command: str = ""
    paths: Tuple[str, ...] = ()
    ops: Tuple[str, ...] = ()         # parallel to paths: add|update|delete|move_from|move_to|write|edit
    mcp_tool: str = ""
    mcp_args: str = ""
    cwd: str = ""
    tool_name: str = ""
    session_id: str = ""
    transcript_path: str = ""
    source: str = ""
    empty: bool = False
    failure: str = ""
```

```python
# tools/skyguard/engine.py
"""The one dispatch point between requests and rules.

Every agent's verdicts go through here, so a rule-module signature change (Plan 5: cwd=, shell=) is made
in one place. Rule modules are called, never edited, from this file.
"""
from __future__ import annotations

from typing import Iterable

from . import codes, shell_rules, write_rules
from .verdict import Advice, Verdict

_RANK = {"evaluated": 0, "partial": 1, "failed": 2}
NO_PATHS = ("GUARD INERT: a write request named no file, so the write rules checked NOTHING. Allowing "
            "silently would be indistinguishable from deciding this is fine.")


def evaluate_shell(req, roots, audit) -> Verdict:
    return codes.classify(shell_rules.decide(req.command, roots, audit))


def evaluate_write(path: str, audit) -> Verdict:
    return codes.classify(write_rules.decide(path, audit))


def combine(verdicts: Iterable[Verdict]) -> Verdict:
    vs = list(verdicts)
    worst = max((v.status for v in vs), key=lambda s: _RANK.get(s, 2), default="evaluated")
    for want in ("deny", "ask"):
        for v in vs:
            if v.decision == want:
                return v._replace(status=worst)
    advice, seen = Advice(), set()
    for v in vs:
        for item in [v.context] if v.context else []:
            if item not in seen:
                seen.add(item)
                advice.add(item)
    codes_seen = [v.code for v in vs if v.context]
    return Verdict("allow", "", advice.joined(), worst, codes_seen[0] if codes_seen else "allow")


def evaluate_writes(req, audit) -> Verdict:
    if not req.paths:
        audit.log("protect-files", "REFUSE", "write request without paths")
        return Verdict("deny", NO_PATHS, status="failed", code="engine.NO_PATHS")
    return combine(evaluate_write(p, audit) for p in req.paths)
```
  (De-duplication is per whole verdict context; one write verdict's context is already one joined string.)

- [ ] **Step 4: Route the Claude roles through `engine`** in `run.py` (verdicts unchanged):
  - `protect-bash`: build `req = claude.to_request(p, "shell")` and call
    `engine.evaluate_shell(req, roots, audit)` in both branches where `shell_rules.decide` was called
    (the empty-command branch keeps `paths.Roots("", "")`).
  - `protect-files`: `v = engine.evaluate_write(p["file_path"], audit)`.
  - Drop the now-unused `shell_rules`/`write_rules` imports from `run.py`; the Task 1 `codes.classify`
    call stays (idempotent on an already-coded verdict).
  - `claude.to_request`:

```python
def to_request(p: dict, kind: str):
    from ..request import Request
    return Request(agent="claude", event="pre_tool", kind=kind, shell="bash" if kind == "shell" else None,
                   command=p["command"], paths=(p["file_path"],) if p["file_path"] else (),
                   tool_name=p["tool_name"], empty=p["empty"])
```

- [ ] **Step 5: Run** → `python -m pytest tests/test_skyguard_engine.py tests/test_plan5_interface.py tests/test_guard_corpus.py tests/test_hooks.py -q`
  → all PASS; `git diff --stat tests/guard_corpus` → empty.

- [ ] **Step 6: Commit**

```bash
python -m pytest tests/ -q -p no:cacheprovider
git add tools/skyguard/request.py tools/skyguard/engine.py tools/skyguard/run.py tools/skyguard/adapters/claude.py tests/test_skyguard_engine.py tests/test_plan5_interface.py
git commit -m "skyguard: request contract and one dispatch module; Claude verdicts unchanged"
```

---

### Task 3: The shared `apply_patch` path parser (D2a: blocking and backups read the same paths)

**Files:**
- Create: `tools/skyguard/patch_paths.py` (ported from X4's `agent/guards/adapters/patch_paths.py`, made
  3.8-compatible; credit in the docstring)
- Test: `tests/test_patch_paths.py` (create)

**Interfaces:** `parse_patch(text) -> list[tuple[op, path]]` with `op` in
`add|update|delete|move_from|move_to`; `shell_patch(cmd) -> (patch_text, cd_dir) | None`;
`PatchParseError`. Paths are returned verbatim (stripped); resolving them is the adapter's job (Task 6).

- [ ] **Step 1: Write the failing tests** (the first four read the REAL captured fixtures)

```python
# tests/test_patch_paths.py
"""One apply_patch grammar for every caller. Anything it does not know is refused, never skipped."""
import json
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard.patch_paths import PatchParseError, parse_patch, shell_patch  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "payloads" / "codex"


def _captured(verb):
    for f in sorted(FIX.glob("*apply_patch*.json")):     # the 0.159.3 captures (notes/a.txt); not rglob
        cmd = json.loads(f.read_text(encoding="utf-8"))["tool_input"]["command"]
        if verb in cmd:
            return cmd
    pytest.fail("no captured fixture with " + verb)


@pytest.mark.parametrize("verb,op", [("*** Add File:", "add"), ("*** Update File:", "update"),
                                     ("*** Delete File:", "delete")])
def test_captured_shapes_parse(verb, op):
    assert parse_patch(_captured(verb)) == [(op, "notes/a.txt")]


def test_move_relabels_the_source_and_adds_the_destination():
    p = "*** Begin Patch\n*** Update File: a.txt\n*** Move to: b.esp\n@@\n-x\n+y\n*** End Patch"
    assert parse_patch(p) == [("move_from", "a.txt"), ("move_to", "b.esp")]


def test_multi_file_patch_lists_every_file():
    p = ("*** Begin Patch\n*** Add File: Data/x.esp\n+a\n*** Update File: Data/y.ini\n@@\n-a\n+b\n"
         "*** Delete File: Data/z.bsa\n*** End Patch")
    assert [o for o, _ in parse_patch(p)] == ["add", "update", "delete"]


def test_a_header_inside_a_hunk_is_content():
    p = "*** Begin Patch\n*** Add File: a.txt\n+*** Delete File: Data/Skyrim.esm\n*** End Patch"
    assert parse_patch(p) == [("add", "a.txt")]


def test_crlf_and_end_of_file_marker():
    p = "*** Begin Patch\r\n*** Update File: a.txt\r\n@@\r\n-a\r\n+b\r\n*** End of File\r\n*** End Patch\r\n"
    assert parse_patch(p) == [("update", "a.txt")]


@pytest.mark.parametrize("bad", [
    "*** Add File: a.txt\n+x\n*** End Patch",                          # no Begin
    "*** Begin Patch\n*** Add File: a.txt\n+x",                        # no End
    "*** Begin Patch\n*** Frobnicate File: a.txt\n*** End Patch",      # unknown header
    "*** Begin Patch\n*** Add File:   \n*** End Patch",                # empty path
    "*** Begin Patch\n*** Add File: a\n*** Move to: b\n*** End Patch",  # Move after Add
    "*** Begin Patch\n*** Update File: a\n@@\n-x\n+y\n*** Move to: b\n*** End Patch",  # Move not directly after Update
    "*** Begin Patch\n*** End Patch",                                  # touches nothing
])
def test_unknown_grammar_is_refused(bad):
    with pytest.raises(PatchParseError):
        parse_patch(bad)


def test_shell_heredoc_form():
    cmd = "apply_patch <<'PATCH'\n*** Begin Patch\n*** Add File: Data/x.esp\n+a\n*** End Patch\nPATCH"
    body, cd = shell_patch(cmd)
    assert parse_patch(body) == [("add", "Data/x.esp")] and cd is None


def test_shell_cd_prefix_is_returned():
    cmd = 'cd "Data/Scripts" && apply_patch <<EOF\n*** Begin Patch\n*** Delete File: a.psc\n*** End Patch\nEOF'
    body, cd = shell_patch(cmd)
    assert cd == "Data/Scripts" and parse_patch(body) == [("delete", "a.psc")]


def test_shell_quoted_argument_form():
    cmd = "apply_patch '*** Begin Patch\n*** Add File: x.esp\n+a\n*** End Patch'"
    assert parse_patch(shell_patch(cmd)[0]) == [("add", "x.esp")]


def test_an_ordinary_command_is_not_a_patch():
    assert shell_patch("Get-Date") is None
    assert shell_patch("rg -n apply_patch tools/") is None


def test_a_patch_it_cannot_attribute_is_refused():
    with pytest.raises(PatchParseError):
        shell_patch("Get-Date; apply_patch < p.txt")
```

- [ ] **Step 2: Run** → FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write the parser**

```python
# tools/skyguard/patch_paths.py
"""The ONE apply_patch path parser (spec section 5 adapter duties; D2a).

Blocking and backups both ask this module which files a patch touches, so the two can never disagree.
Any grammar it does not know raises PatchParseError -- a missing Begin/End, an unknown `*** ` header, an
empty path, a Move not directly after an Update, a patch that touches nothing. The caller must refuse:
a partial path list is an allow for the paths it missed.

Grammar: Codex apply_patch, confirmed on captured payloads (Add/Update/Delete on 0.159.3; Move and
multi-file on 0.160.0 per the X4 toolkit's lane-B measurements, re-captured in Plan 4 Task 0). Inside a
hunk every content line starts with `+`, `-`, ` ` or `@@`, so `+*** Delete File: x` is CONTENT.
Ported from the X4 toolkit (agent/guards/adapters/patch_paths.py); Python 3.8 syntax here.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

BEGIN, END = "*** Begin Patch", "*** End Patch"
_HEADERS = (("*** Add File:", "add"), ("*** Update File:", "update"), ("*** Delete File:", "delete"))
_MOVE = "*** Move to:"
_EOF = "*** End of File"
# apply_patch at the start of a shell command, optionally after `cd <dir> &&` (or `;`). Codex applies a
# patch run through the shell and reports it as a Bash call (X4 MEASURED, 0.160.0).
_SHELL_FORM = re.compile(
    r"""^\s*(?:cd\s+(?P<dir>"[^"]*"|'[^']*'|[^\s;&|]+)\s*(?:&&|;)\s*)?(?:apply_patch|applypatch)(?=\s|$|<)""")
# The same word at ANY command position: used to refuse what _SHELL_FORM cannot attribute.
_ANY_POSITION = re.compile(r"(?:^|[;&|\n]|\$\()\s*(?:apply_patch|applypatch)(?=\s|$|<)")
_HEREDOC = re.compile(
    r"<<-?\s*(['\"]?)(?P<tag>[A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n(?P<body>.*?)\n[ \t]*(?P=tag)[ \t]*(?:\n|$)", re.S)
_QUOTED = re.compile(r"\s*(['\"])(?P<body>\*\*\* Begin Patch.*\*\*\* End Patch)\1\s*$", re.S)


class PatchParseError(ValueError):
    pass


def parse_patch(text: str) -> List[Tuple[str, str]]:
    lines = text.replace("\r\n", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines or lines[0].strip() != BEGIN:
        raise PatchParseError("no '*** Begin Patch' line")
    if lines[-1].strip() != END:
        raise PatchParseError("no '*** End Patch' line")
    out: List[Tuple[str, str]] = []
    prev_was_update_header = False
    for line in lines[1:-1]:
        if not line.startswith("*** "):
            prev_was_update_header = False
            continue
        for head, op in _HEADERS:
            if line.startswith(head):
                path = line[len(head):].strip()
                if not path:
                    raise PatchParseError("empty path after " + head)
                out.append((op, path))
                prev_was_update_header = op == "update"
                break
        else:
            if line.startswith(_MOVE):
                path = line[len(_MOVE):].strip()
                if not prev_was_update_header or not path:
                    raise PatchParseError("'*** Move to:' must directly follow an Update header")
                out[-1] = ("move_from", out[-1][1])
                out.append(("move_to", path))
                prev_was_update_header = False
            elif line.strip() == _EOF:
                prev_was_update_header = False
            else:
                raise PatchParseError("unknown patch header: " + line[:60])
    if not out:
        raise PatchParseError("the patch touches no file")
    return out


def shell_patch(cmd: str) -> Optional[Tuple[str, Optional[str]]]:
    m = _SHELL_FORM.match(cmd)
    if not m:
        if _ANY_POSITION.search(cmd):
            raise PatchParseError("apply_patch at a shell position this parser cannot attribute")
        return None
    rest = cmd[m.end():]
    h = _HEREDOC.search(rest)
    if h:
        body = h.group("body")
    else:
        q = _QUOTED.match(rest)
        if not q:
            raise PatchParseError("apply_patch through the shell without a heredoc or a quoted patch")
        body = q.group("body")
    d = m.group("dir")
    if d and d[0] in "\"'":
        d = d[1:-1]
    return body, d
```

- [ ] **Step 4: Run** → `python -m pytest tests/test_patch_paths.py tests/test_skyguard_core.py -q` → PASS
  (including `test_engine_parses_on_python_3_8`).

- [ ] **Step 5: Commit**

```bash
git add tools/skyguard/patch_paths.py tests/test_patch_paths.py
git commit -m "skyguard: one apply_patch path parser (Add/Update/Delete/Move, shell form), refuses unknown grammar"
```

---
### Task 4: Health telemetry redesign (invocation → receipt → evaluation → emission) and the canary on top of it

**Why:** today a hook writes one heartbeat (`<hook>` = "a payload arrived") or `<hook>.blind`. That
proves receipt, not that a verdict was reached and delivered, and on Codex the silent failures are exactly
the ones *after* receipt: an engine crash, a wrapper killed by the agent's timeout (which lets the command
run), an output the agent could not parse. The canary must see each stage, and a failure must clear only
when a **newer completed evaluation** proves recovery.

**Files:**
- Create: `tools/skyguard/health.py`
- Modify: `tools/skyguard/run.py` (args `--invocation`, `--telemetry`; write the `evaluated` record)
- Modify: `agent/guards/adapters/claude/*.sh` (five wrappers: stage records), then regenerate
- Modify: `tools/hook-canary.sh` (thin front for `health.py report`)
- Rewrite: `tests/test_hook_canary.py`; create `tests/test_skyguard_health.py`
- Modify: `tests/test_hooks.py::test_every_hook_writes_a_liveness_heartbeat` (new layout),
  `tests/test_mutations.py` (re-anchor the canary entries; add two)

**Record format** (one line per file, overwritten; `<t>` = Unix seconds with a fractional part, `.` decimal;
`<inv>` = invocation id, `<pid>-<random>`), in `<project>/.claude/backups/.hook-heartbeat/<agent>/`:

| File | Writer | Content |
|---|---|---|
| `<hook>.invoked` | wrapper, first thing | `<t> <inv>` |
| `<hook>.received` | wrapper | `<t> <inv> bytes=<n>` (n > 0) |
| `<hook>.evaluated` | engine (`run.py`) | `<t> <inv> <status> <decision> <code>` |
| `<hook>.emitted` | wrapper, after it validated the engine's output | `<t> <inv> ok` |
| `<hook>.failed` | wrapper or engine | `<t> <inv> <stage> <text>` (stage: receipt, python, engine, emission) |
| `<hook>.latency` | wrapper, **appended** | `<inv> <t_invoked> <t_emitted>`; the engine trims it to its last 2,000 lines when it passes 256 KB |

**States** (`health.hook_state(dir, hook, now, stale_s=7200, kill_s=150)`), checked in this order:
1. no record at all → `NOT EXERCISED`;
2. any record that does not parse (timestamp not a float, too few fields, unknown status) → `UNMEASURABLE`;
3. a **completed evaluation** = `evaluated` with status `evaluated` **and** an `emitted` with the same
   `<inv>`. A `failed` record is current unless a completed evaluation is **strictly newer**
   (equal timestamps prove no order) → `DEGRADED` (detail = the failure);
4. `invoked` newer than the last emission by more than `kill_s` with no emission for that `<inv>` →
   `DEGRADED` ("invoked at T and never finished -- killed by the agent's timeout? On Codex that lets the
   command RUN");
5. the newest `evaluated` has status `partial` → `DEGRADED` ("partial: <code>");
6. the last completed evaluation is older than `stale_s` → `STALE`;
7. otherwise `HEALTHY`, with "recovered; was DEGRADED at T" when an older `failed` exists (keeps the
   2026-09-09 RECOVERED lesson visible, exit 0).

Report exit: **1** if any hook is DEGRADED; else **2** if any is UNMEASURABLE or the directory is unreadable;
else 0. The 3.x files (`.hook-heartbeat/<hook>`, `<hook>.blind`) are ignored, with one line saying so (R10).

- [ ] **Step 1: Write the failing state tests** (`tests/test_skyguard_health.py`). One test per rule above
  **and a twin** for each recovery rule (Review Focus 4). Key ones, in full:

```python
# tests/test_skyguard_health.py
"""Hook health from staged telemetry: a failure clears only on a NEWER COMPLETED evaluation."""
import sys

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import health  # noqa: E402

NOW = 1_800_000_000.0


def _w(d, hook, stage, line):
    d.mkdir(parents=True, exist_ok=True)
    (d / "{}.{}".format(hook, stage)).write_text(line + "\n", encoding="utf-8")


def _complete(d, t, inv="1-1", status="evaluated"):
    _w(d, "h", "invoked", "{} {}".format(t - 0.2, inv))
    _w(d, "h", "received", "{} {} bytes=40".format(t - 0.1, inv))
    _w(d, "h", "evaluated", "{} {} {} allow allow".format(t, inv, status))
    _w(d, "h", "emitted", "{} {} ok".format(t + 0.01, inv))


def test_nothing_is_not_exercised(tmp_path):
    assert health.hook_state(tmp_path, "h", NOW)[0] == "NOT EXERCISED"


def test_a_completed_evaluation_is_healthy(tmp_path):
    _complete(tmp_path, NOW - 5)
    assert health.hook_state(tmp_path, "h", NOW)[0] == "HEALTHY"


def test_a_failure_after_the_last_completion_is_degraded(tmp_path):
    _complete(tmp_path, NOW - 50)
    _w(tmp_path, "h", "failed", "{} 2-2 engine exit 1".format(NOW - 10))
    assert health.hook_state(tmp_path, "h", NOW)[0] == "DEGRADED"


def test_a_newer_receipt_does_not_clear_a_failure(tmp_path):
    """Twin of the recovery rule: payload arrival is not proof that a verdict was reached."""
    _w(tmp_path, "h", "failed", "{} 2-2 engine exit 1".format(NOW - 10))
    _w(tmp_path, "h", "invoked", "{} 3-3".format(NOW - 5))
    _w(tmp_path, "h", "received", "{} 3-3 bytes=40".format(NOW - 5))
    assert health.hook_state(tmp_path, "h", NOW)[0] == "DEGRADED"


def test_a_newer_completed_evaluation_clears_it_and_says_so(tmp_path):
    _w(tmp_path, "h", "failed", "{} 2-2 engine exit 1".format(NOW - 10))
    _complete(tmp_path, NOW - 5, inv="3-3")
    state, detail = health.hook_state(tmp_path, "h", NOW)
    assert state == "HEALTHY" and "recovered" in detail


def test_equal_timestamps_prove_no_recovery(tmp_path):
    _complete(tmp_path, NOW - 5, inv="3-3")
    _w(tmp_path, "h", "failed", "{} 2-2 engine exit 1".format(NOW - 5))
    assert health.hook_state(tmp_path, "h", NOW)[0] == "DEGRADED"


def test_an_evaluation_never_emitted_is_not_a_completion(tmp_path):
    _w(tmp_path, "h", "failed", "{} 2-2 emission not JSON".format(NOW - 10))
    _w(tmp_path, "h", "evaluated", "{} 3-3 evaluated deny write.DENY_PLUGIN".format(NOW - 5))
    _w(tmp_path, "h", "emitted", "{} 2-2 ok".format(NOW - 11))
    assert health.hook_state(tmp_path, "h", NOW)[0] == "DEGRADED"


def test_an_invocation_that_never_finished_is_degraded(tmp_path):
    _complete(tmp_path, NOW - 1000, inv="1-1")
    _w(tmp_path, "h", "invoked", "{} 9-9".format(NOW - 400))
    state, detail = health.hook_state(tmp_path, "h", NOW)
    assert state == "DEGRADED" and "never finished" in detail


def test_a_partial_evaluation_is_degraded(tmp_path):
    _complete(tmp_path, NOW - 5, status="partial")
    assert health.hook_state(tmp_path, "h", NOW)[0] == "DEGRADED"


def test_an_old_completion_is_stale(tmp_path):
    _complete(tmp_path, NOW - 9000)
    assert health.hook_state(tmp_path, "h", NOW)[0] == "STALE"


def test_a_truncated_record_is_unmeasurable_never_green(tmp_path):
    _complete(tmp_path, NOW - 5)
    _w(tmp_path, "h", "failed", "20260909_15")
    assert health.hook_state(tmp_path, "h", NOW)[0] == "UNMEASURABLE"


def test_3x_heartbeat_files_are_ignored(tmp_path):
    (tmp_path / "protect-bash.blind").write_text("20260101_000000 NO PAYLOAD\n")
    assert health.hook_state(tmp_path / "claude", "protect-bash", NOW)[0] == "NOT EXERCISED"
```

- [ ] **Step 2: Run** → FAIL (`ModuleNotFoundError: skyguard.health`).

- [ ] **Step 3: Write `health.py`.** Interfaces: `STATES`, `record(dirpath, hook, stage, inv, detail)`
  (engine-side writer; never raises), `read(path) -> tuple|None` (raises `Unmeasurable`),
  `hook_state(d, hook, now, stale_s=7200, kill_s=150) -> (state, detail)`,
  `latency(d, hook, last=200) -> (n, p50, p95)`, `trim_latency(d, hook)`, and a CLI
  `python tools/skyguard/health.py report --root <install> [--stale-min N] [--json]` that walks every
  `<agent>/` directory that has a matching installed hook set (`.claude/hooks/*.sh` → claude's five;
  `.codex/hooks.json` → codex's `pre-tool`, `session-start`), prints one row per hook
  (`state`, detail, `latency p50/p95` when ≥ 5 samples) and exits per the rule above. The state function
  follows the numbered rules literally; keep it one function with the rules as commented blocks in that
  order, so a reviewer can check them against this plan line by line. A parsed record is
  `(t: float, inv: str, rest: list[str])`. The two helpers the mutation gate anchors on, verbatim:

```python
def _completed(recs):
    """Time of the newest COMPLETED evaluation: status 'evaluated' AND emitted for the same invocation."""
    ev, em = recs.get("evaluated"), recs.get("emitted")
    if ev and em and ev[2][0] == "evaluated" and em[1] == ev[1]:
        return ev[0]
    return None


def _never_finished(recs, now, kill_s):
    """Invoked, never emitted, and older than any hook timeout: the agent killed it (Codex then RAN the call)."""
    inv, em = recs.get("invoked"), recs.get("emitted")
    return bool(inv) and (not em or em[1] != inv[1]) and now - inv[0] > kill_s
```
  `hook_state` calls them as `done = _completed(recs)` and `if _never_finished(recs, now, kill_s):`.

- [ ] **Step 4: Engine side.** `run.py` gains `--invocation` (default `""`) and `--telemetry` (default `""`).
  When both are set, after the verdict is classified it calls
  `health.record(a.telemetry, a.role, "evaluated", a.invocation, "{} {} {}".format(v.status, v.decision, v.code))`
  (session role: `"evaluated session code=session"`), and `health.trim_latency(...)`. A telemetry failure
  never changes the verdict (`record` swallows `OSError`).

- [ ] **Step 5: Wrapper side (all five Claude wrappers).** Replace the heartbeat block. Builtins only (the
  hot-path rule from Plan 2's fork-free rewrite):

```bash
HB_DIR="$PROJECT_DIR/.claude/backups/.hook-heartbeat/claude"
[ -d "$HB_DIR" ] || mkdir -p "$HB_DIR" 2>/dev/null
T0="${EPOCHREALTIME:-}"; T0="${T0/,/.}"; [ -n "$T0" ] || printf -v T0 '%(%s)T' -1
INV="$$-$RANDOM"
printf '%s %s\n' "$T0" "$INV" > "$HB_DIR/$HOOK_NAME.invoked" 2>/dev/null
if [ -n "$INPUT" ]; then
    printf '%s %s bytes=%s\n' "$T0" "$INV" "${#INPUT}" > "$HB_DIR/$HOOK_NAME.received" 2>/dev/null
else
    printf '%s %s receipt NO PAYLOAD\n' "$T0" "$INV" > "$HB_DIR/$HOOK_NAME.failed" 2>/dev/null
fi
```
  pass `--invocation "$INV" --telemetry "$HB_DIR"` to `run.py`; on the success branch, before printing:

```bash
T1="${EPOCHREALTIME:-$T0}"; T1="${T1/,/.}"
printf '%s %s ok\n' "$T1" "$INV" > "$HB_DIR/$HOOK_NAME.emitted" 2>/dev/null
printf '%s %s %s\n' "$INV" "$T0" "$T1" >> "$HB_DIR/$HOOK_NAME.latency" 2>/dev/null
```
  and in `inert()` (blocking wrappers) and every FAILED branch (non-blocking wrappers) first write
  `printf '%s %s %s %s\n' "$T1" "$INV" "<stage>" "<text>" > "$HB_DIR/$HOOK_NAME.failed"`
  (`python` for "cannot run Python", `engine` for a non-zero exit, `emission` for non-JSON output). The
  literal `hook-heartbeat` stays in a non-comment line (release gate). Regenerate:
  `python scripts/gen_agent_trees.py`.

- [ ] **Step 6: `tools/hook-canary.sh` becomes a front.** It keeps its header comment (the 2026-09-06
  `/dev/stdin` history), finds an interpreter (the configured `PY=` line of `.claude/hooks/protect-bash.sh`,
  else `python3`, `python`, `py -3`), and runs `health.py report --root "$ROOT" "$@"`. With no Python it
  prints `UNMEASURABLE: no Python 3 -- and without it every blocking hook is refusing every command (run
  setup.sh)` and exits 2. Rewrite `tests/test_hook_canary.py` to drive the real script against fixture
  telemetry, keeping every scenario from its docstring (blind then recovered, equal stamps, truncated
  stamp, stale, not exercised) translated to the new records, plus the exit-code table.

- [ ] **Step 7: Mutations.** Re-anchor every existing `test_hook_canary` mutation entry to the equivalent
  construct in `health.py` (each must still be killed), and add:

```python
    # Recovery must be proven by a COMPLETED evaluation. Accept a newer receipt instead and a guard whose
    # engine crashes on every call reads HEALTHY as soon as the next payload arrives.
    pytest.param(
        "tools/skyguard/health.py",
        '    if ev and em and ev[2][0] == "evaluated" and em[1] == ev[1]:\n        return ev[0]',
        '    if recs.get("received"):\n        return recs["received"][0]',
        "tests/test_skyguard_health.py::test_a_newer_receipt_does_not_clear_a_failure",
        id="health-recovery-needs-a-completed-evaluation",
    ),
    # Forget the never-finished rule and a hook killed by the agent's timeout -- on Codex, a command
    # that RAN unchecked -- leaves no trace in the report.
    pytest.param(
        "tools/skyguard/health.py",
        "if _never_finished(",
        "if False and _never_finished(",
        "tests/test_skyguard_health.py::test_an_invocation_that_never_finished_is_degraded",
        id="health-reports-a-killed-invocation",
    ),
```
  (Both anchors are the Step 3 helpers verbatim. The first mutant is the exact defect class: recovery
  "proven" by payload arrival.)

- [ ] **Step 8: Run and commit.** `python -m pytest tests/test_skyguard_health.py tests/test_hook_canary.py tests/test_hooks.py tests/test_guard_corpus.py -q`,
  then the full suite, then the mutation gate for the canary/health entries.

```bash
git add tools/skyguard/health.py tools/skyguard/run.py agent/guards .claude/hooks tools/hook-canary.sh tests/
git commit -m "Health telemetry: invocation/receipt/evaluation/emission; recovery only by a newer completed evaluation"
```

---

### Task 5: Python fallback chain (configured → verified PATH/launcher → known locations)

**Why:** today a moved or upgraded Python makes every blocking hook refuse everything until `setup.sh` is
re-run (honest, but a full outage). The chain keeps the hooks working and **reports the degradation**
instead. The hot path is unchanged: the chain runs only when the configured interpreter fails.

**Files:**
- Modify: `agent/guards/adapters/claude/*.sh` (five), regenerate
- Test: `tests/test_python_fallback.py` (create); `tests/test_mutations.py` (one entry)

**Chain (identical order in every wrapper; Task 7 adds the Codex entries):**
1. the configured `PY` (setup.sh), used as today;
2. on failure (`command -v` fails, or the engine exits non-zero **and** a capability probe of `PY` fails),
   each of `py -3`, `python`, `python3` that resolves and is **not** a `WindowsApps` alias, capability-probed;
3. known locations: `$LOCALAPPDATA/Programs/Python/Python3*/python.exe`, `/c/Python3*/python.exe`,
   `$PROGRAMFILES/Python3*/python.exe`, `/usr/bin/python3`, `/usr/local/bin/python3`;
4. a `WindowsApps` alias, last;
5. nothing → today's `inert` deny (blocking) / FAILED line (non-blocking).

Capability probe: `"$@" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 8) else 1)'`. A
fallback that succeeds writes a `failed` record (stage `python`, "configured <PY> unusable; used <X> --
re-run setup.sh") **before** the engine runs, so the canary shows DEGRADED until a later call through the
configured interpreter completes (rule 3 of Task 4).

- [ ] **Step 1: Failing tests** (`tests/test_python_fallback.py`), each through the real wrapper with
  `tests/bin/` shims on `PATH` (Plan 2 added `tests/bin/{py,python,python3}`):
  - `test_a_missing_configured_python_falls_back_and_still_decides`: `PY` set to a nonexistent path,
    `PATH` has a working `python` → a `Write` to `Data/X.esp` is **denied by the rule**
    (`BLOCKED: Cannot directly write to plugin/archive files…`), not `GUARD INERT`; a `failed` record with
    stage `python` exists.
  - `test_a_python2_on_path_is_skipped`: a shim `python` that exits 1 on the probe, a working `python3` →
    decided by the rule.
  - `test_the_store_alias_is_used_only_last`: shims under a `WindowsApps/` directory and a regular one →
    the regular one is used (the shim records argv to a file).
  - `test_nothing_runnable_still_refuses`: no candidate → `GUARD INERT … cannot run Python`.
  - `test_every_wrapper_lists_the_same_chain`: the text between `# PY-CHAIN-BEGIN` and `# PY-CHAIN-END`
    is identical in all five Claude wrappers (Task 7 extends this to the two Codex entries, comparing the
    candidate names and order after normalising syntax).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the chain as one function between `# PY-CHAIN-BEGIN`/`# PY-CHAIN-END` in each
  wrapper (store multi-word candidates in an array; never `eval`). Replace `command -v "$PY" … || inert …`
  with `command -v "$PY" >/dev/null 2>&1 || py_fallback || inert "…"`, and on a non-zero engine exit run
  `py_ok "$PY" || { py_fallback && <run the engine once more>; }` before `inert "engine failed …"`.
  Regenerate.
- [ ] **Step 4: Run** the new tests, `tests/test_hooks.py`, `tests/test_guard_corpus.py`, and
  `python scripts/guard-latency.py <configured copy> --runs 30` → p95 within 10 % of the Plan 2 E2E number
  (the hot path must not have moved). Record both numbers in the commit body.
- [ ] **Step 5: Mutation** — make the chain try the alias first:

```python
    pytest.param(
        "agent/guards/adapters/claude/protect-bash.sh",
        '*/[Ww]indows[Aa]pps/*) [ -z "$PY_ALIAS" ] && PY_ALIAS="$c"; continue ;;',
        '*/[Ww]indows[Aa]pps/*) ;;',
        "tests/test_python_fallback.py::test_the_store_alias_is_used_only_last",
        id="python-fallback-uses-the-store-alias-last",
    ),
```
  (Write the `case` arm exactly as anchored above. The mutation targets the SOURCE wrapper and the
  test copies the generated hook, so the test must install from `agent/guards/adapters/claude/` via the
  generator's render, not from `.claude/hooks/` — use `gen_agent_trees.render("claude", REPO/"agent")`
  in its fixture.)
- [ ] **Step 6: Commit** `"Hooks: Python fallback chain, used only when the configured interpreter fails, reported as DEGRADED"`.

---

### Task 6: The Codex adapter and the engine's Codex roles

**Files:**
- Create: `tools/skyguard/adapters/codex.py`
- Modify: `tools/skyguard/engine.py` (add `pre_tool`, `side_effects`), `tools/skyguard/run.py`
  (`ADAPTERS["codex"]`, roles `pre-tool` / `session-start`, the `SKYGUARD-OK` sentinel)
- Test: `tests/test_skyguard_codex_adapter.py` (create)

**Interfaces:**
- `codex.to_request(raw: str, platform: str = sys.platform) -> Request`
- `codex.render(v: Verdict) -> str` — PreToolUse body: deny JSON, `additionalContext` JSON, or `""`.
- `codex.CONTEXT_LIMIT` (from Task 0 / R7), `codex.INSTRUCTION_LIMIT_BYTES = 32768`, `codex.ASK_PREFIX`,
  `codex.BANNER = "SKYGUARD: Codex hooks live (definitions v1)."`
- `engine.pre_tool(req, project, roots_fn, audit) -> Verdict` (all Codex PreToolUse work: rules, then
  snapshot and backups; side-effect exceptions → status `partial`; unknown/MCP tools → allow + one line in
  `.claude/backups/codex-unhooked-tools.log`).
- `run.py codex pre-tool|session-start …` prints `SKYGUARD-OK\n` + body, exits 0; any internal failure exits
  1 with nothing on stdout (unchanged contract, so the wrapper can tell "decided" from "broke").

Mapping (MEASURED shapes, Task 0 + Plan 1 fixtures):

| Codex payload | Request |
|---|---|
| `SessionStart` (`source`: startup/resume/compact) | `event=session_start` |
| `PreToolUse`, `tool_name:"Bash"`, `tool_input.command` str | `kind=shell`, `shell=powershell` on Windows else `bash`; if `patch_paths.shell_patch` finds a patch → also `paths/ops` (resolved against `cwd` and its `cd` dir) |
| `PreToolUse`, `tool_name:"apply_patch"` | `kind=write`, `paths/ops` from `parse_patch`, resolved against `cwd` |
| any other `tool_name` (MCP, `collaborationspawn_agent`, …) | `kind=mcp` (MCP naming per Task 0) or `other` — allowed, recorded |
| empty / not JSON / not an object / Bash without a string command / unreadable patch | `failure="<why>"` → deny, status `failed` |

- [ ] **Step 1: Failing tests** — drive every committed Codex fixture through `to_request` and `render`;
  the key assertions:

```python
# tests/test_skyguard_codex_adapter.py (excerpt; one test per row of the mapping table, plus these)
def test_ask_never_reaches_codex():
    for decision in ("ask", "allow", "deny", "bogus"):
        out = codex.render(Verdict(decision, "r", "c"))
        assert '"ask"' not in out and '"allow"' not in out


def test_ask_becomes_a_deny_that_says_why():
    j = json.loads(codex.render(Verdict("ask", "ReSaver --apply mutates a save")))
    h = j["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny"
    assert h["permissionDecisionReason"].startswith("NEEDS YOUR CONFIRMATION")


def test_an_unknown_decision_is_refused_not_allowed():
    h = json.loads(codex.render(Verdict("bogus", "x")))["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny"


def test_plain_allow_prints_nothing():
    assert codex.render(Verdict()) == ""


def test_long_context_is_capped_with_a_visible_note():
    out = json.loads(codex.render(Verdict("allow", "", "x" * 20000)))
    assert len(out["hookSpecificOutput"]["additionalContext"]) <= codex.CONTEXT_LIMIT
    assert "truncated" in out["hookSpecificOutput"]["additionalContext"]


def test_a_relative_patch_path_resolves_against_the_session_cwd():
    raw = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "cwd": "C:\\G\\Data\\Scripts",
                      "tool_input": {"command": "*** Begin Patch\n*** Add File: ..\\x.esp\n+a\n*** End Patch"}})
    assert codex.to_request(raw, "win32").paths == ("C:/G/Data/x.esp",)


def test_a_patch_through_the_shell_carries_its_paths():
    cmd = "cd Data && apply_patch <<'P'\n*** Begin Patch\n*** Delete File: Skyrim.esm\n*** End Patch\nP"
    raw = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": "C:/G", "tool_input": {"command": cmd}})
    r = codex.to_request(raw, "win32")
    assert (r.kind, r.paths, r.ops) == ("shell", ("C:/G/Data/Skyrim.esm",), ("delete",))


@pytest.mark.parametrize("raw", ["", "not json", "[]", '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":5}}',
                                 '{"hook_event_name":"PreToolUse","tool_name":"apply_patch","tool_input":{"command":"*** Begin Patch\\n*** End Patch"}}'])
def test_untranslatable_payloads_carry_a_failure(raw):
    assert codex.to_request(raw, "win32").failure
```
  plus engine-level tests: a `write` request for `Data/a.esp` → deny `write.DENY_PLUGIN`; an Update of an
  existing `Data/SKSE/Plugins/x.ini` → allow with the SKSE advice **and** a backup file in
  `.claude/backups/`; a Delete of an existing file → backup taken; a request with `failure` → deny status
  `failed` code `codex.TRANSLATE`; an MCP tool → allow + one line in `codex-unhooked-tools.log`; a
  `side_effects` exception → status `partial` with the decision unchanged.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement `adapters/codex.py`** (core shown; keep it translation-only):

```python
"""Codex CLI <-> skyguard. Translation only (spec section 5).

Codex blocks on exactly one thing: a parsed {"hookSpecificOutput":{"permissionDecision":"deny"}}.
"ask", exit 2, exit 1, a crash, malformed JSON and a timeout all let the command RUN (spec E1, E17;
re-measured in Plan 4 Task 0). So render() can emit only three bodies -- deny, additionalContext, or
nothing -- and an ask becomes a deny that says the user must run it themselves (R4).
"""
from __future__ import annotations

import json
import ntpath
import posixpath
import sys

from .. import patch_paths
from ..request import Request
from ..verdict import Verdict

AGENT = "codex"
INSTRUCTION_LIMIT_BYTES = 32768     # E7: AGENTS.md past this is silently truncated
CONTEXT_LIMIT = 2500                # replace with the Task 0 / R7 measured value
ASK_PREFIX = ("NEEDS YOUR CONFIRMATION -- Codex cannot ask, so the toolkit refuses instead. Stop and tell "
              "the user what you wanted to run and why; if they agree, they run it themselves. ")
BANNER = "SKYGUARD: Codex hooks live (definitions v1)."
_NOTE = " [truncated by the toolkit at {n} characters]"


def _s(v) -> str:
    return v if isinstance(v, str) else ""


def _cap(text: str) -> str:
    if len(text) <= CONTEXT_LIMIT:
        return text
    note = _NOTE.format(n=CONTEXT_LIMIT)
    return text[:CONTEXT_LIMIT - len(note)] + note


def _resolve(path: str, cwd: str, cd_dir, platform: str) -> str:
    mod = ntpath if platform.startswith("win") else posixpath
    base = cwd
    if cd_dir:
        base = cd_dir if mod.isabs(cd_dir) or not cwd else mod.join(cwd, cd_dir)
    p = path.strip()
    if not mod.isabs(p) and base:
        p = mod.join(base, p)
    return mod.normpath(p).replace("\\", "/") if (mod.isabs(p) or base) else p.replace("\\", "/")


def to_request(raw: str, platform: str = sys.platform) -> Request:
    text = raw.replace("\x00", "")
    if not text.strip():
        return Request(agent=AGENT, event="pre_tool", empty=True, failure="empty payload")
    try:
        d = json.loads(text)
    except ValueError:
        return Request(agent=AGENT, event="pre_tool", failure="payload is not JSON")
    if not isinstance(d, dict):
        return Request(agent=AGENT, event="pre_tool", failure="payload is not a JSON object")
    common = dict(agent=AGENT, cwd=_s(d.get("cwd")), session_id=_s(d.get("session_id")),
                  transcript_path=_s(d.get("transcript_path")), tool_name=_s(d.get("tool_name")))
    event = d.get("hook_event_name")
    if event == "SessionStart":
        return Request(event="session_start", kind="none", source=_s(d.get("source")), **common)
    if event != "PreToolUse":
        return Request(event="other", kind="other", **common)
    ti = d.get("tool_input") if isinstance(d.get("tool_input"), dict) else {}
    cmd = ti.get("command")
    name = common["tool_name"]
    try:
        if name == "apply_patch":
            if not isinstance(cmd, str) or not cmd:
                return Request(event="pre_tool", failure="apply_patch without patch text", **common)
            ops = patch_paths.parse_patch(cmd)
            return Request(event="pre_tool", kind="write", command=cmd,
                           paths=tuple(_resolve(p, common["cwd"], None, platform) for _, p in ops),
                           ops=tuple(o for o, _ in ops), **common)
        if name == "Bash":
            if not isinstance(cmd, str):
                return Request(event="pre_tool", failure="shell call without a command string", **common)
            shell = "powershell" if platform.startswith("win") else "bash"
            sp = patch_paths.shell_patch(cmd)
            if sp is None:
                return Request(event="pre_tool", kind="shell", shell=shell, command=cmd, **common)
            body, cd_dir = sp
            ops = patch_paths.parse_patch(body)
            return Request(event="pre_tool", kind="shell", shell=shell, command=cmd,
                           paths=tuple(_resolve(p, common["cwd"], cd_dir, platform) for _, p in ops),
                           ops=tuple(o for o, _ in ops), **common)
    except patch_paths.PatchParseError as e:
        return Request(event="pre_tool", failure="unreadable patch: {}".format(e), **common)
    kind = "mcp" if name.startswith("mcp__") else "other"     # naming per Task 0; adjust if it differs
    return Request(event="pre_tool", kind=kind, mcp_tool=name if kind == "mcp" else "",
                   mcp_args=json.dumps(ti, sort_keys=True) if kind == "mcp" else "", **common)


def render(v: Verdict) -> str:
    if v.decision == "allow":
        if not v.context:
            return ""
        body = {"hookEventName": "PreToolUse", "additionalContext": _cap(v.context)}
    else:
        reason = v.reason if v.decision == "deny" else (
            ASK_PREFIX + v.reason if v.decision == "ask" else
            "GUARD INERT: the engine returned an unknown decision ({!r}); refusing.".format(v.decision))
        body = {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": _cap(reason)}
    return json.dumps({"hookSpecificOutput": body}, ensure_ascii=False)
```
  If Task 0 measured that `!` in the TUI does **not** fire PreToolUse, append to `ASK_PREFIX`:
  `"(In the Codex prompt they can type ! followed by the command.)"`. Otherwise leave it as is.
- [ ] **Step 4: `engine.pre_tool`** — `failure` → `Verdict("deny", INERT_TRANSLATE.format(req.failure),
  status="failed", code="codex.TRANSLATE")` where `INERT_TRANSLATE = "GUARD INERT: the Codex hook could not
  read this call ({}), so it checked NOTHING. This is a refusal, not a verdict."`; `kind=shell` →
  `evaluate_shell`, combined with `evaluate_writes` when `paths` is set; `kind=write` → `evaluate_writes`;
  then `side_effects(req, project, audit, v)`: snapshot for every shell call
  (`snapshot.run(project, req.command, False, audit)`, as the Claude hook does), and a backup for every path
  whose op is `update`, `delete` or `move_from` (`backup.run(project, "apply_patch", path, False, audit)`),
  each in its own `try` → on exception `v._replace(status="partial")`. Side effects run whatever the
  decision, as on Claude (where the backup/snapshot hooks run beside the guard). Plan 5 changes to
  `backup.run`/`snapshot.run` signatures are made at these two calls.
- [ ] **Step 5: `run.py`** — `ADAPTERS = {"claude": claude, "codex": codex}`; roles add `pre-tool` and
  `session-start` (valid only with `codex`; `claude` + those roles → exit 1). Codex pre-tool:
  `req = codex.to_request(raw)`, `v = engine.pre_tool(req, a.project, lambda: paths.roots(a.root, a.root_win, env), audit)`,
  `health.record(...)`, then `_emit("SKYGUARD-OK\n" + codex.render(v))`. Codex session-start:
  `text = session.run(a.project, req.source, a.bash, audit)`, `_emit("SKYGUARD-OK\n" + codex.BANNER + ("\n" + text if text else ""))`.
  (Plan 3's `session.run` already injects preferences and handles `compact`; nothing Codex-specific.)
- [ ] **Step 6: Run** the adapter tests, `tests/test_skyguard_engine.py`, the corpus, then commit:
  `"skyguard: Codex adapter (deny-JSON or context only; ask becomes deny) and the Codex pre-tool/session roles"`.

---

### Task 7: The Codex entry wrappers — the fail-closed boundary

**Files:**
- Create: `agent/guards/adapters/codex/skyguard-entry.ps1`, `agent/guards/adapters/codex/skyguard-entry.sh`
- Test: `tests/test_codex_entry.py` (create)

**Contract (both files):** read all of stdin as bytes; write `invoked`/`received`/`failed`/`emitted`/
`latency` telemetry under `<root>/.claude/backups/.hook-heartbeat/codex/` (root = two levels above the
script); find Python by the Task 5 chain (configured `{{PYTHON_PATH}}` first); run
`run.py codex <role> --root <root> --project <root> --bash {{BASH_PATH}} --invocation <inv> --telemetry <dir>`
with the payload on stdin **under an internal deadline** (pre-tool 50 s, session-start 110 s; both below the
definition timeouts 60/120 so the wrapper, not Codex, decides); require line 1 == `SKYGUARD-OK`; for
pre-tool require the body to be empty or one JSON object whose only top-level key is `hookSpecificOutput`,
whose keys are a subset of `hookEventName, permissionDecision, permissionDecisionReason, additionalContext`,
and whose `permissionDecision`, if present, is `"deny"`. **Anything else** — unknown role, no Python,
engine missing, non-zero exit, deadline passed (kill the process tree), empty output, extra lines, bad
JSON, a non-deny decision, an exception anywhere — prints the INERT deny (pre-tool) or the
`SKYGUARD: NOT LIVE -- …` line (session-start), records `failed`, and **exits 0**. A `finally` block emits
the deny if nothing was emitted. The cause text is reduced to `[A-Za-z0-9 ._:/()=,+-]` and 300 chars
before it goes into JSON (no hand escaping). ASCII only. Never log the environment.

- [ ] **Step 1: Failing tests** (`tests/test_codex_entry.py`). Run the PowerShell entry with `pwsh`
  (skip the module if neither `pwsh` nor `powershell` exists) and the POSIX entry with Git Bash, each from a
  fixture install where the root path contains a space and `(x86)`. **Fault matrix, every row asserting the
  exact deny JSON (Review Focus 1)**, each with a replaced `run.py`:
  `raise SystemExit(3)` · `print("hi")` · prints `SKYGUARD-OK` + two JSON lines · prints `SKYGUARD-OK` +
  `{"hookSpecificOutput":{"permissionDecision":"ask"}}` · prints `SKYGUARD-OK` + `{"x":1}` · sleeps past the
  deadline (set by a test-only env `SKYGUARD_WRAPPER_TIMEOUT_S=2`) · `run.py` deleted · `{{PYTHON_PATH}}`
  unfilled and no Python on `PATH` (test-only `SKYGUARD_NO_PYTHON_FALLBACK=1`). Plus the controls: a benign
  `Get-Date` fixture → empty stdout, exit 0, `emitted` record; the captured `apply_patch` Add fixture
  rewritten to `Data/Decoy.esp` → deny `BLOCKED: Cannot directly write to plugin/archive files…`; a
  SessionStart fixture → stdout starts with `SKYGUARD: Codex hooks live`. Plus:
  `test_entry_scripts_are_ascii`, `test_fallback_chain_matches_the_claude_wrappers` (extends Task 5's).
  Task 12's mutants name these tests, so use exactly: `test_plugin_write_is_denied` (asserts the RULE's
  text, not merely "deny"), `test_engine_crash_is_a_deny`, `test_a_hung_engine_is_a_deny`,
  `test_every_path_exits_zero` (parametrized over the whole fault matrix and the controls).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Write `skyguard-entry.ps1`.** Base it on the X4 entry
  (`C:/Users/Moona/Projects/x4-p2-b/agent/guards/adapters/codex-entry.ps1`: stdin as bytes via
  `[Console]::OpenStandardInput()`, `ProcessStartInfo` with async stdout/stderr reads, `WaitForExit(limit)`
  then `taskkill /T /F`, `Test-Body`, quote only whitespace-containing arguments because `py.exe` reads its
  raw command line), and change: the role names; `SKYGUARD-OK` sentinel; the Task 5 chain
  (`'{{PYTHON_PATH}}'` first; a `$exe -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 8) else 1)"`
  probe per fallback candidate; `WindowsApps` last; `SKYGUARD_NO_PYTHON_FALLBACK=1` test knob); the
  telemetry writes (`[IO.File]::WriteAllText`, timestamps from
  `[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000` formatted with `InvariantCulture`); the deny and
  NOT-LIVE texts naming `bash tools/hook-canary.sh`.
- [ ] **Step 4: Write `skyguard-entry.sh`** for POSIX Codex: the Claude wrapper's fork-free structure, with
  `timeout <limit>` around the engine when `timeout` exists, the `SKYGUARD-OK` check, and a body check by
  pattern (starts `{"hookSpecificOutput":`, contains no `"permissionDecision":"ask"` / `"allow"`, one line).
  Best effort on POSIX (Plan 4 claims Windows; CI ubuntu runs its tests).
- [ ] **Step 5: Run** `python -m pytest tests/test_codex_entry.py -q` → PASS on Windows with both `pwsh`
  and `powershell` (parametrize the interpreter). Time 30 calls of the benign fixture through the exact
  `commandWindows` string; record p50/p95 in the commit body (compare with Task 0).
- [ ] **Step 6: Commit** `"Codex entry wrappers: every path out is the engine's verdict or a deny"`.

---
### Task 8: The frozen hook definitions, the project config, the freeze gate, and the trust check

**Files:**
- Create: `agent/targets/codex/hooks.json`, `agent/targets/codex/config.toml`,
  `agent/targets/codex/FROZEN.sha256`
- Create: `tools/skyguard/codex_trust.py` (hash scheme ported from X4's `codex_trust.py`, 3.8-compatible,
  narrow TOML reader; credit in the docstring)
- Test: `tests/test_codex_definitions.py`, `tests/test_codex_trust.py` (create)

**Interfaces:**
- `hooks.json` placeholders: `{{CODEX_ROOT}}` (install root, Windows spelling with **forward slashes**,
  e.g. `C:/Program Files (x86)/Steam/steamapps/common/Skyrim VR`) and `{{CODEX_ROOT_POSIX}}`. Filled by
  `setup.sh` (Task 11). No other machine-specific byte may appear in a definition.
- `codex_trust.hook_hash(event_key, group, handler, windows) -> "sha256:<hex>"`;
  `codex_trust.expected_entries(hooks_json, windows) -> {state_key: hash}`;
  `codex_trust.trust_report(hooks_json, codex_config, project_root) -> dict` with a per-hook status in
  `trusted | untrusted | modified | disabled | unknown` (unknown when the file cannot be read or **no**
  stored hash matches the scheme — never guessed as modified). CLI
  `python tools/skyguard/codex_trust.py report --root <install>` (exit 0 all trusted / 1 any not / 2 unknown).

- [ ] **Step 1: Write the template** (R1, R2; add `"failureMode": "block"` per R6 and
  `"additionalContextLimit": 8000` per R7 **only if Task 0 proved them**, before the first commit — after
  that, any change is a re-approval):

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"{{CODEX_ROOT_POSIX}}/.codex/hooks/skyguard-entry.sh\" session-start",
            "commandWindows": "pwsh -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"{{CODEX_ROOT}}/.codex/hooks/skyguard-entry.ps1\" session-start || powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"{{CODEX_ROOT}}/.codex/hooks/skyguard-entry.ps1\" session-start",
            "timeout": 120,
            "statusMessage": "Skyrim toolkit: session checks"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "bash \"{{CODEX_ROOT_POSIX}}/.codex/hooks/skyguard-entry.sh\" pre-tool",
            "commandWindows": "pwsh -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"{{CODEX_ROOT}}/.codex/hooks/skyguard-entry.ps1\" pre-tool || powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"{{CODEX_ROOT}}/.codex/hooks/skyguard-entry.ps1\" pre-tool",
            "timeout": 60,
            "statusMessage": "Skyrim toolkit: checking"
          }
        ]
      }
    ]
  }
}
```
  `agent/targets/codex/config.toml`:

```toml
# Codex reads at most this many bytes of AGENTS.md in a TRUSTED project (spec D9, E7). It is margin
# only: the toolkit's CI keeps AGENTS.md at or under 32,768 bytes as installed, which an untrusted
# project still reads in full.
project_doc_max_bytes = 65536
```
- [ ] **Step 2: Failing tests** (`tests/test_codex_definitions.py`):
  - `test_exactly_two_definitions` (SessionStart without matcher; PreToolUse matcher `.*`).
  - `test_no_command_starts_with_a_quote` and `test_windows_commands_never_call_bash` (R2).
  - `test_every_command_runs_the_entry_with_a_known_role`.
  - `test_definitions_carry_no_python_path_and_no_other_placeholder` (only `{{CODEX_ROOT}}`,
    `{{CODEX_ROOT_POSIX}}`; R3).
  - `test_timeouts_exceed_the_wrappers_own_deadlines` (60 > 50 + 5; 120 > 110 + 5; read the deadlines from
    the entry scripts).
  - `test_codex_hook_definitions_are_frozen`: the sha256 of the template's LF bytes equals the **last** line
    of `FROZEN.sha256` (`<sha> <label>`); if the file has more than one line, the CHANGELOG's top section
    (`## Unreleased …` or the version being released) contains `re-approve your Codex hooks`; and for the
    newest git tag whose tree has `agent/targets/codex/hooks.json` (`git tag --sort=-creatordate`), a
    different blob also requires that CHANGELOG line. With no such tag yet, that half says so in a
    `print`, and the ledger half still runs (R14).
- [ ] **Step 3: Create the ledger** — `python -c "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('agent/targets/codex/hooks.json').read_bytes().replace(b'\r\n',b'\n')).hexdigest(), 'v1')" > agent/targets/codex/FROZEN.sha256`.
- [ ] **Step 4: `codex_trust.py`.** Hash scheme (READ by X4 from `codex-rs hooks/src/engine/discovery.rs`;
  reproduced 6/6 there): `"sha256:" + sha256(compact canonical JSON, keys sorted recursively, None omitted,
  of {"event_name": <snake_case event>, "matcher"?: m, "hooks": [handler]})`, handler =
  `{"type":"command","command": <commandWindows on Windows else command>, "timeout": <given else 600>,
  "async": <bool, default false>, "statusMessage"?, "additionalContextLimit"? (omitted when unset or 2500)}`
  (+ `failureMode` if R6 adopted it and Task 0 showed it in the hash). State key:
  `<absolute hooks.json path>:<snake event>:<group index>:<handler index>` (verify the exact spelling —
  separators, drive-letter case — against the keys Task 0 Step 6 read from `~/.codex/config.toml`).
  Config reader: `tomllib` when available, else a narrow line reader for `[hooks.state.'<key>']` /
  `[hooks.state."<key>"]` tables with `trusted_hash` and `enabled`, where any other line inside such a
  table makes the answer `unknown`. **Never write the config.**
- [ ] **Step 5: Trust tests** (`tests/test_codex_trust.py`): reproduce every vector in
  `tests/fixtures/codex/<version>/trust_vectors.json` (Task 0 Step 4; computed for the neutral root
  `C:/SkyguardVectors/Skyrim Probe (x86)`, so no personal path is committed); `untrusted` with no state
  table; `modified` when one stored hash differs and another matches; `unknown` when none match;
  `disabled` when `enabled = false`; `unknown` on an unreadable config. The match against hashes Codex
  stored after a **real** `/hooks` approval is a live check (Task 0 Step 6, Task 14 Phase C), not a
  committed fixture, because those keys hold the user's path.
- [ ] **Step 6: Mutation** (the spec §7.4 "change a frozen definition field" mutant):

```python
    # Change one definition field (E18): Codex silently stops running every approved copy of this hook.
    pytest.param(
        "agent/targets/codex/hooks.json",
        '"timeout": 60,',
        '"timeout": 61,',
        "tests/test_codex_definitions.py::test_codex_hook_definitions_are_frozen",
        id="codex-definitions-are-frozen",
    ),
```
- [ ] **Step 7: Run and commit**
  `"Codex: two frozen hook definitions, project config, freeze gate, and a hook-trust reader"`.

---

### Task 9: `.codex/rules/skyrim.rules` — the line that holds while the hooks are unapproved

**Files:**
- Create: `tools/skyguard/codex_rules.py`
- Test: `tests/test_codex_rules.py` (create)

**Interfaces:** `CLASSIFICATION: dict[str, tuple[str, str]]` (code → (`forbidden|prompt|hook_only`, reason));
`PREFIX_RULES: list[dict]` (`pattern`, `decision`, `code`, `justification`, `match`, `not_match`);
`render() -> str` (Starlark text, consumed by the generator in Task 10).

- [ ] **Step 1: Failing tests:**
  - `test_every_shell_verdict_is_classified`: every `DENY_*`, `ASK_*`, `NO_*` string constant of
    `shell_rules` has a `CLASSIFICATION` row (this is the test Plan 5 trips when it adds one).
  - `test_buckets_sum`: forbidden + prompt + hook_only == number of classified codes; every
    `forbidden`/`prompt` code has at least one `PREFIX_RULES` entry and vice versa.
  - `test_every_prefix_rule_is_a_subset_of_its_hook_deny` (R5): for each rule, every `match` example joined
    with spaces is **denied by `shell_rules.decide`** with that rule's code (through `engine.evaluate_shell`
    with fixture roots), and every `not_match` example is not.
  - `test_rules_file_passes_codex_execpolicy` (skipped only when `codex` is not on PATH, with the reason
    printed): write `render()` to a temp file, then for each `match` example run
    `codex execpolicy check --rules <file> <tokens…>` → its output names the rule's decision; for each
    `not_match` example → no match.
- [ ] **Step 2: Implement.** Classification (today's `shell_rules` constants):

| Code | Bucket | Reason |
|---|---|---|
| `shell.DENY_REGISTRY` | forbidden | the hook denies every `reg delete`; `reg delete` / `reg.exe delete` prefixes are a strict subset |
| `shell.DENY_CONFIG`, `shell.DENY_INSTALL`, `shell.DENY_CHAMPOLLION` | hook_only | path-based: the protected path is an argument, not a prefix |
| `shell.DENY_PSC` | hook_only | the `-o` flag can be anywhere |
| `shell.ASK_RESAVER` | hook_only | a prefix would also prompt every dry-run; `--apply` position varies (R4) |
| `shell.NO_COMMAND_DENY`, `shell.NO_ROOT_DENY` | hook_only | analysis failures, not commands |

```python
PREFIX_RULES = [
    {"pattern": ["reg", "delete"], "decision": "forbidden", "code": "shell.DENY_REGISTRY",
     "justification": "Skyrim toolkit: registry deletes are refused (Bethesda keys locate the game). "
                      "Ask the user to run it themselves if it is really intended.",
     "match": [["reg", "delete", "HKCU\\Software\\SkyguardProbe", "/f"]],
     "not_match": [["reg", "query", "HKCU\\Software"]]},
    {"pattern": ["reg.exe", "delete"], "decision": "forbidden", "code": "shell.DENY_REGISTRY",
     "justification": "Skyrim toolkit: registry deletes are refused (Bethesda keys locate the game). "
                      "Ask the user to run it themselves if it is really intended.",
     "match": [["reg.exe", "delete", "HKCU\\Software\\SkyguardProbe", "/f"]],
     "not_match": [["reg.exe", "query", "HKCU\\Software"]]},
]
```
  `render()` emits one `prefix_rule(pattern = [...], decision = "...", justification = "...", match = [...],
  not_match = [...])` per entry, strings JSON-escaped (Starlark accepts JSON string syntax). If
  `codex execpolicy check` rejects `match`/`not_match` keywords, drop them from the rendered file (keep them
  in `PREFIX_RULES` for the tests) and record that in the commit body.
- [ ] **Step 3: Run and commit** `"Codex .rules: prefix rules that are strict subsets of hook denies; every shell verdict classified"`.

---

### Task 10: The generator's Codex target — `AGENTS.md`, `.agents/skills/`, `.codex/**`

**Files:**
- Modify: `scripts/gen_agent_trees.py`, `agent/README.md`, `.gitattributes`
- Create: `agent/instructions/codex.md`; delete `agent/instructions/agents-pointer.md` (R12)
- Modify: `agent/skills/inspect-esp/SKILL.md`, `agent/skills/port-to-vr/SKILL.md` (and any skill the
  neutrality sweep flags)
- Regenerate: `AGENTS.md`, `.agents/skills/**`, `.codex/**`
- Test: `tests/test_gen_agent_trees.py` (append), `tests/test_mutations.py` (one entry)

**Interfaces:** `render("codex", src)`; `render_all(src) -> dict` (union of targets; raises if two targets
emit the same path); CLI `--target all|claude|codex` (default `all`, which `--check` and CI use).
`OWNED_DIRS` gains `.agents/skills`, `.codex/hooks`, `.codex/rules`. `TEXT_SUFFIXES` gains `.ps1`, `.toml`,
`.rules`, which take the `#` header as their **first** line (no shebang to skip).

- [ ] **Step 1: Failing tests** (append):
  - `test_codex_target_maps_every_source` (scratch source like Plan 1's `_scratch_source`, plus
    `instructions/codex.md`, `guards/adapters/codex/skyguard-entry.ps1`, `targets/codex/{hooks.json,config.toml,FROZEN.sha256}`)
    → keys `AGENTS.md`, `.agents/skills/demo/SKILL.md`, `.agents/skills/demo/pic.png`,
    `.codex/hooks/skyguard-entry.ps1`, `.codex/hooks.json`, `.codex/config.toml`, `.codex/rules/skyrim.rules`;
    **not** `FROZEN.sha256`.
  - `test_agents_md_is_core_plus_codex_addendum` and `test_claude_target_no_longer_renders_agents_md`.
  - `test_no_path_is_owned_by_two_targets`.
  - `test_hooks_json_gets_no_header_and_ps1_gets_one_on_line_one`.
  - `test_installed_agents_md_fits_the_budget`: `instruction_sizes(REPO/"agent")["codex.md"] <= 32768`
    (Plan 3's budget, now with the real addendum; `codex.md (reserved)` is gone).
  - `test_codex_md_stays_small`: `len(codex.md LF bytes) <= 3000` (target; Plan 3 measured room for 3,511).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `_render_codex(src)`: `AGENTS.md` = `_headed("agent/instructions/core.md + codex.md", core + codex, ".md")`;
  skills → `.agents/skills/<rel>` with the same `_headed`; `guards/adapters/codex/*` →
  `.codex/hooks/<name>` headed by suffix; `targets/codex/hooks.json` → `.codex/hooks.json` (LF, no header);
  `targets/codex/config.toml` → `.codex/config.toml` (headed); `.codex/rules/skyrim.rules` =
  `SH_HEADER.format(src="tools/skyguard/codex_rules.py") + codex_rules.render()` (import by path, as tests
  import `tools/`). Remove `AGENTS.md` from `_render_claude`. Add the `.gitattributes` lines
  `.codex/** text eol=lf`, `.agents/** text eol=lf`, `*.ps1 text eol=lf`.
- [ ] **Step 4: Write `agent/instructions/codex.md`** (≤ 3,000 bytes; state only what Task 0 measured —
  drop or reword any bullet whose fact was not confirmed):

```markdown

---

## Codex specifics

Generated into `AGENTS.md` only; everything above is shared. Other agents: this section is not about you.

- **The guards run only after the user approves them.** Codex skips unapproved project hooks silently.
  When they run, your session context starts with `SKYGUARD: Codex hooks live`. **If that line is
  missing, the guards are NOT running:** tell the user to run `/hooks` in Codex, approve the Skyrim
  toolkit's two hooks, and check with `bash tools/hook-canary.sh`. Approval is needed again after a
  toolkit update whose CHANGELOG says "re-approve your Codex hooks", and after the install folder moves.
- **Your shell is PowerShell 7** on Windows, although the tool is called `Bash`. Write PowerShell; run the
  toolkit's scripts as `bash tools/<name>.sh`.
- **A refusal is final.** The guards cannot ask on Codex, so anything that needs the user's confirmation
  is refused with `NEEDS YOUR CONFIRMATION`. Stop and explain what you wanted to run and why; if the user
  agrees, they run it themselves. Never retry it in another form.
- **Checked:** every shell command and every `apply_patch`, including one run through the shell. Writing
  a plugin or archive (`.esp/.esm/.esl/.bsa/.ba2`) directly is refused; deleting in the game or config
  folders is refused; edits inside the game get advice. Files an `apply_patch` changes or deletes are
  backed up to `.claude/backups/` first, and recent `.psc`/`.pex` are snapshotted before shell commands.
  A file written by a script you run is not backed up.
- **Not checked:** text typed into an interactive program you started, and MCP tools (DevBench included).
- **Skills** are in `.agents/skills/<name>/SKILL.md`. Use one by mentioning `$<name>` (for example
  `$skyrim-nif`) or read the file. Where a skill says `$ARGUMENTS`, it means the user's request.
- `bash tools/hook-canary.sh` shows whether each hook ran, saw its input and finished, per surface.
```
- [ ] **Step 5: Skill bodies.** In `inspect-esp` and `port-to-vr`, rewrite each bare `$ARGUMENTS` to "the
  mod the user named (`$ARGUMENTS` in Claude Code)" (spec §4). Then run Plan 3's neutrality check
  (`violations()`) over **every** skill body (frontmatter excluded) and fix each hit the same way Plan 3
  fixed the core; extend the neutrality test's scope to all skills (`test_every_skill_body_is_neutral`).
  Frontmatter keeps its Claude keys (E6).
- [ ] **Step 6: Regenerate and verify.** `python scripts/gen_agent_trees.py` →
  `wrote N of M files (all)`; `--check` → in sync; `--budget` → the `codex.md` row ≤ 32,768;
  `git diff --stat .claude CLAUDE.md` → **no change** (the Claude target is untouched apart from no longer
  owning `AGENTS.md`). Update `agent/README.md`'s table (Codex rows; the pointer row goes).
- [ ] **Step 7: Mutation** — hand-edit a generated Codex file:

```python
    pytest.param(
        ".codex/config.toml",
        "project_doc_max_bytes = 65536",
        "project_doc_max_bytes = 32768",
        "tests/test_gen_agent_trees.py::test_repo_is_in_sync",
        id="generated-codex-files-cannot-be-hand-edited",
    ),
```
- [ ] **Step 8: Commit** `"Generator: Codex target (AGENTS.md = core + codex addendum, .agents/skills, .codex/**)"`.

---

### Task 11: `setup.sh` configures Codex; kb-guard watches `AGENTS.md`; release gates know Codex

**Files:**
- Modify: `setup.sh`, `tools/kb-guard.sh`, `.github/workflows/release.yml`, `tools/verify-release.sh`,
  `.github/workflows/ci.yml` (the "Every hook command points at a file that exists" step also reads
  `.codex/hooks.json`), `agent/instructions/core.md` (the sentence naming the snapshotted files), `README.md`
  (a "Using with Codex" section), `CHANGELOG.md`
- Test: `tests/test_setup_paths.py`, `tests/test_kb_guard.py`, `tests/test_repo_invariants.py` (append)

- [ ] **Step 1: Failing tests:**
  - `test_setup_configures_the_codex_entry_scripts`: after `run_setup`, both `.codex/hooks/skyguard-entry.*`
    hold the real interpreter path and the Git Bash path, no `{{`.
  - `test_setup_fills_the_codex_definitions_with_the_install_root`: `.codex/hooks.json` parses; every
    `commandWindows` contains `<root with forward slashes>/.codex/hooks/skyguard-entry.ps1`; no `{{`.
  - `test_setup_renders_definitions_deterministically`: running setup twice leaves `.codex/hooks.json`
    byte-identical, and two installs at the same path in different shells (`run_setup` with and without
    the MSYS shim) produce identical bytes (Review Focus 2).
  - `test_setup_warns_when_codex_definitions_point_elsewhere`: a configured hooks.json whose root is another
    folder → setup prints `WARNING: .codex/hooks.json points at` and the re-extract + re-approve steps,
    and does **not** rewrite it.
  - `test_setup_refuses_a_root_it_cannot_put_in_json`: a POSIX root containing `"` or `\` → the Codex
    definitions are left unconfigured with a printed reason (the Claude side still configures).
  - `test_agents_md_gets_the_same_paths_as_claude_md`: the configured `AGENTS.md` carries the same
    `Game root`/MO2 lines as `CLAUDE.md`.
  - `test_setup_prints_the_codex_approval_step`.
  - `test_kb_guard_snapshots_agents_md` (extend `test_every_watched_file_is_actually_snapshotted`).
- [ ] **Step 2: Implement `setup.sh`:**
  - The `{{PYTHON_PATH}}` loop iterates relative paths: the five `.claude/hooks/*.sh` plus
    `.codex/hooks/skyguard-entry.ps1` and `.codex/hooks/skyguard-entry.sh` (the "Updated interpreter"
    branch matches `^PY=` in `.sh` and `^\$PY = ` in `.ps1`).
  - `{{BASH_PATH}}` → `cygpath -m "$BASH"` on Git Bash, else `$BASH`.
  - `{{CODEX_ROOT}}` → `$GAME_ROOT_WIN` with `\` turned into `/`; `{{CODEX_ROOT_POSIX}}` → `$GAME_DIR`.
    Escaped for sed with the existing `PY_SED`-style escaping. Refuse (print why) a value containing `"`
    or `\` after normalisation.
  - If `.codex/hooks.json` has no placeholder and does not contain the current root → the WARNING above.
  - The CLAUDE.md placeholder block becomes `for DOC in CLAUDE.md AGENTS.md; do … done` (same seds, same
    MO2 block splice).
  - After the hooks section print:

```text
Codex users: Codex runs these hooks only after you approve them. Open `codex` in this folder, type
/hooks, and approve the 2 Skyrim toolkit hooks. Until then NOTHING is checked and Codex does not say so.
Check any time with: bash tools/hook-canary.sh
```
- [ ] **Step 3:** `tools/kb-guard.sh` `WATCHED="KNOWLEDGEBASE.md KNOWLEDGEBASE.local.md CLAUDE.md AGENTS.md"`;
  update the sentence in `agent/instructions/core.md` (and `docs/safety-philosophy.md`) that names the three
  snapshotted files to name four; regenerate; re-check `--budget`.
- [ ] **Step 4: Release gates.** In `release.yml` (payload checks) and `tools/verify-release.sh`, add:
  `.codex/hooks.json` present and parses; each command's script exists in the payload;
  `.codex/rules/skyrim.rules` present; `.agents/skills/*/SKILL.md` count equals `.claude/skills/*/SKILL.md`
  count; payload `AGENTS.md` ≤ 32,768 bytes. Each new check gets a case in the existing
  `test_verify_release` style test where the check is proven to fail on a payload missing that file.
  `git check-ignore .codex/hooks.json .agents/skills/x` → prints nothing.
- [ ] **Step 5: README + CHANGELOG.** README "Using with Codex": the approval step; what is checked and
  what is not (the Task 0–measured gaps: unapproved hooks are silent; `write_stdin`/interactive input;
  MCP until Plan 5; a hook that cannot start fails open; the shell `workdir` is invisible to hooks; moving
  the folder needs re-approval; surfaces claimed = CLI TUI + `codex exec`, others "Layers 0–2 only");
  `ask` becomes a refusal. CHANGELOG `## Unreleased`: "Codex support" entries, **one issue per line**
  (the user's changelog rule), including "First Codex hook definitions (v1): approve them in /hooks".
- [ ] **Step 6: Run and commit** (two commits): `"setup.sh configures the Codex hooks and AGENTS.md; prints the approval step"`,
  `"kb-guard watches AGENTS.md; release gates check the Codex payload; README/CHANGELOG: Codex"`.

---
### Task 12: Conformance from captured fixtures, and the adapter mutants (spec §7.3–7.4)

**Files:**
- Create: `tests/test_codex_conformance.py`
- Modify: `tests/test_mutations.py` (the spec §7.4 set not already added)

- [ ] **Step 1: Conformance.** For **every** file under `tests/fixtures/payloads/codex/` (both versions),
  feed it through the **generated, configured** entry (`.codex/hooks/skyguard-entry.ps1` via `pwsh`, and
  `powershell` when present; `.sh` via Git Bash) in a fixture install, and assert: exit 0; stdout is empty
  or exactly one JSON object of an allowed shape (no `ask`, no `allow`, no unknown key); telemetry has a
  matching `invoked`/`evaluated`/`emitted` triple; and the verdict equals the row's expectation in
  `tests/fixtures/payloads/codex/EXPECTED.json` (fixture name → `allow|advise|deny` + code).
  **Derived variants** — a captured shape with only its path replaced (`notes/a.txt` → `Data/Decoy.esp`,
  `Data/SKSE/Plugins/x.ini`, `../x.esp` from a subdirectory `cwd`) — are generated in the test from the
  captured file, never written by hand, and named `derived:<fixture>:<path>` in the test id (the shape
  comes from a real capture; only the path comes from the test, spec §7.3).
  Expectations for the derived variants: Add/Update/Delete/Move of a plugin → deny `write.DENY_PLUGIN`;
  Update of an SKSE ini → advise `write.ADV_SKSE_CONFIG` + a backup file; the shell heredoc patch onto
  `Data/Skyrim.esm` → deny; `Remove-Item` with the install path → deny `shell.DENY_INSTALL`; ReSaver
  `--apply` → deny starting `NEEDS YOUR CONFIRMATION`; MCP/unknown tool → allow + recorded.
- [ ] **Step 2: The spec §7.4 mutants** (each anchored on code written in Tasks 3, 6, 7; each must be
  killed by the named test):

| id | file | mutation | test that must fail |
|---|---|---|---|
| `codex-adapter-drops-delete-paths` | `tools/skyguard/patch_paths.py` | `("*** Delete File:", "delete")` removed from `_HEADERS` | `test_codex_conformance.py::test_conformance[derived:…apply_patch-3…:Data/Decoy.esp]` |
| `codex-deny-becomes-allow` | `tools/skyguard/adapters/codex.py` | `"permissionDecision": "deny"` → `"permissionDecision": "allow"` | `test_codex_entry.py::test_plugin_write_is_denied` (the wrapper must refuse a non-deny decision → INERT deny, so assert the **rule's** text, not just "deny") |
| `codex-wrapper-swallows-engine-errors` | `agent/guards/adapters/codex/skyguard-entry.ps1` | the non-zero-exit `Fail` line → `Emit ''; exit 0` | `test_codex_entry.py::test_engine_crash_is_a_deny` |
| `codex-adapter-emits-ask` | `tools/skyguard/adapters/codex.py` | `"permissionDecision": "deny",` → `"permissionDecision": v.decision,` | `test_skyguard_codex_adapter.py::test_ask_never_reaches_codex` |
| `codex-wrapper-exits-2-on-deny` | `skyguard-entry.ps1` | the final `exit 0` after a verdict → `exit 2` | `test_codex_entry.py::test_every_path_exits_zero` |
| `codex-adapter-malformed-json` | `tools/skyguard/adapters/codex.py` | `return json.dumps({"hookSpecificOutput": body}, ensure_ascii=False)` → the same `[:-1]` | `test_codex_entry.py::test_plugin_write_is_denied` |
| `codex-wrapper-has-no-deadline` | `skyguard-entry.ps1` | `WaitForExit($limit * 1000)` → `WaitForExit()` | `test_codex_entry.py::test_a_hung_engine_is_a_deny` |
| `codex-shell-patch-not-parsed` | `tools/skyguard/adapters/codex.py` | `sp = patch_paths.shell_patch(cmd)` → `sp = None` | `test_skyguard_codex_adapter.py::test_a_patch_through_the_shell_carries_its_paths` |
| `codex-relative-path-not-resolved` | `tools/skyguard/adapters/codex.py` | `if not mod.isabs(p) and base:` → `if False:` | `test_skyguard_codex_adapter.py::test_a_relative_patch_path_resolves_against_the_session_cwd` |
| `codex-unclassified-shell-verdict` | `tools/skyguard/codex_rules.py` | delete the `shell.DENY_REGISTRY` classification row | `test_codex_rules.py::test_every_shell_verdict_is_classified` |

  (`codex-definitions-are-frozen` landed in Task 8.) Write each as a `pytest.param` with a comment saying
  what real defect it stands for, as the existing entries do.
- [ ] **Step 3: Run the mutation gate for the new ids**, then the full suite. Commit
  `"Codex conformance from captured payloads; adapter mutants proven killed"`.

---

### Task 13: The canary reports Codex trust and per-surface hook coverage (D14)

**Why:** an unapproved, modified or disabled Codex hook is silent (E2, E18), and some Windows surfaces emit
no `PreToolUse` at all (E24). Neither can be seen from inside the hook. Both can be seen from outside:
the trust state from `~/.codex/config.toml`, and coverage by comparing a session's transcript (its
`transcript_path` arrives in every payload) with the hook's own per-session record.

**Files:**
- Modify: `tools/skyguard/health.py` (sessions + coverage), `tools/skyguard/run.py` (record a session line
  per call for both agents), `tools/skyguard/adapters/claude.py` (`extract()` also returns `session_id`,
  `transcript_path`; render unchanged)
- Create: `tools/skyguard/transcripts.py` (one parser per agent)
- Test: `tests/test_skyguard_coverage.py` (create), driven by the scrubbed transcript excerpts from Task 0

**Interfaces:**
- Engine per call: append `<t> <hook> <tool_name>` to
  `.hook-heartbeat/<agent>/sessions/<session_id>.log` (first line on SessionStart:
  `<t> start <source> <transcript_path>`). Keep the 20 newest session files per agent.
- `transcripts.count_calls(agent, path) -> (surface, {tool_name: n})`, raising `Unmeasurable` on any shape
  it does not recognise. The Codex parser reads the surface and the tool-call items **exactly as Task 0
  Step 5 recorded them** (fields and item types are constants at the top of the module, each with a comment
  citing the measurement); the Claude parser counts `tool_use` blocks and reads `entrypoint`.
- `health.coverage(project, agent, last=5) -> [(session, surface, hooked, called, state)]`, where `called`
  counts only tools the installed matchers cover (Claude: `Bash`, `Edit`, `Write` until Plan 5 adds
  `PowerShell`; Codex: every tool call, `write_stdin` excluded and listed as a known gap if Task 0
  confirmed it is never hooked). State: `HEALTHY` (hooked ≥ called), `DEGRADED` (hooked < called: calls
  ran with no hook), `NOT EXERCISED` (no calls), `UNMEASURABLE` (transcript missing or unrecognised).
- `health.py report` prints, per agent: the hook rows (Task 4), then for Codex the trust table from
  `codex_trust.trust_report` ("Codex hooks NOT RUNNING (untrusted or unapproved)" when no heartbeat and not
  trusted, spec §8), then a per-surface coverage block. Exit code folds coverage DEGRADED into 1 and an
  untrusted/modified/disabled hook into 1.

- [ ] **Step 1: Failing tests:** coverage HEALTHY on a transcript excerpt whose calls all have session
  lines; DEGRADED when one call has none (the E24 shape); UNMEASURABLE on an unknown transcript shape (a
  twin that renames the item type the parser keys on); surface grouping (exec vs TUI excerpts); the trust
  block says NOT RUNNING for an untrusted fixture config; the report's exit codes.
- [ ] **Step 2: Implement; run; commit** `"Canary: Codex hook trust and per-surface coverage from transcripts (D14)"`.

---

### Task 14: Live end-to-end on both Codex CLI surfaces (release gate for Plan 4; user needed ~15 min)

**Files:**
- Create: `scripts/e2e-codex.py` (repeatable each release; spec §12 "re-measured each release")
- Create: `docs/superpowers/measurements/2026-10-XX-plan4-codex-e2e.md`

`e2e-codex.py --make <dir>` builds a **release-style copy** (rsync of the repo minus `.git .github tests`)
into `<dir>/Skyrim Probe (x86)/` with fake game files (`SkyrimVR.exe`, `Data/Existing.esp`,
`Data/Scripts/Source/a.psc`, `Data/SKSE/Plugins/probe.ini`, `KNOWLEDGEBASE.local.md` with
`## My preferences` holding the sentinel `PREF-SENTINEL-6611`) and runs `bash setup.sh </dev/null`.
`e2e-codex.py --phase A|B|C|read-tui <copy>` runs one phase and appends JSON results; `--report` prints the
table. Every probe passes only **for the right reason**: Codex's output says
`blocked by PreToolUse hook` (or the rules rejection for B1) **and** `AUDIT_LOG.txt` has the matching
`DENY` line **and** the side effect is absent. Probes use non-delete writes where possible (E22); the one
delete probe targets a nonexistent file and requires the hook's audit line, so Codex's own policy cannot
pass it for the wrong reason.

- [ ] **Phase A — `codex exec`, hooks bypass-trusted** (`--dangerously-bypass-hook-trust -s workspace-write`,
  one action per run, `< /dev/null`):

| # | Prompt asks for | Pass |
|---|---|---|
| A1 | `apply_patch` Add `Data/New.esp` | blocked; file absent; audit `DENY` `write.DENY_PLUGIN` |
| A2 | `apply_patch` Update `Data/SKSE/Plugins/probe.ini` | applied; advice in output; a backup of the old file in `.claude/backups/` |
| A3 | `apply_patch` through the shell (heredoc) Update `Data/Existing.esp` | blocked; file unchanged |
| A4 | `Remove-Item "<root>/Data/Scripts/Source/zz_nonexistent.psc" -ErrorAction SilentlyContinue` | blocked by the **hook** (`shell.DENY_INSTALL` audit line) |
| A5 | `bash tools/resaver-cli.sh reset-havok probe.ess --apply` | blocked; reason starts `NEEDS YOUR CONFIRMATION` |
| A6 | `Get-Date` | runs; snapshot `.last_snapshot` updated |
| A7 | session start: "quote the first line of your session context and the preference sentinel, without reading any file" | reply has `SKYGUARD: Codex hooks live` and `PREF-SENTINEL-6611`; the rollout shows **no** read of `KNOWLEDGEBASE.local.md` (D15 through the real entry point) |
| A8 | from `-C "Data/Scripts"`: `apply_patch` Add `../New2.esp` | blocked (relative path from a subdirectory cwd) |

- [ ] **Phase B — hooks unreviewed** (a second copy at a new path, **no** bypass flag):
  B1 `reg delete HKCU\Software\SkyguardProbe /f` → rejected by `.codex/rules/skyrim.rules` (E19 on our
  rules); B2 the A1 prompt → **not** blocked (expected: E2) and `bash tools/hook-canary.sh` in that copy
  reports Codex hooks NOT RUNNING and exits non-zero. B2 is a measured residual, not a failure; it is
  why the README's approval step and the session banner exist.
- [ ] **USER STEP — TUI in copy A** (needs a real terminal). Give the user exactly:

  ```text
  Open a NEW Windows Terminal tab (PowerShell), then:
    cd "<copy A path>"
    codex
  In Codex:
    /hooks      -> approve the 2 "Skyrim toolkit" hooks (session checks, checking)
    Type:  Create Data/TuiProbe.esp containing the word probe, using apply_patch.
    Type:  Run Get-Date.
    Type:  What is the first line of your session-start context? Quote it exactly.
    /quit
  Then tell me "TUI done".
  ```
  Then `--phase read-tui`: TuiProbe.esp absent with the hook's audit DENY; `Get-Date` ran; the banner was
  quoted; the canary's coverage block shows the TUI surface HEALTHY; the trust table shows both hooks
  `trusted`, and `codex_trust.expected_entries()` equals the keys/hashes Codex stored (read-only).
- [ ] **Phase C — `codex exec` with the TUI-approved hooks, no bypass flag** (copy A): A1 → blocked
  (approved hooks run under exec). Then change `"timeout": 60` to `61` in copy A's installed
  `.codex/hooks.json`: A1 → **not** blocked and the canary trust table says `modified` (E18 re-measured on
  our own definitions); restore the byte-identical file: A1 → blocked again.
- [ ] **Claude regression in copy A:** `claude -p` Write `Data/Decoy.esp` → blocked; an `rm` in `Data/` →
  blocked; `ls` allowed; the canary shows Claude's five hooks HEALTHY/NOT EXERCISED, never DEGRADED.
- [ ] **Latency (spec §7.5, M4):** p50/p95 of `pre-tool` from `.hook-heartbeat/codex/pre-tool.latency`
  across Phases A and C, and of Claude's `protect-bash`. Gate: p95 ≤ 1 s, or the value the user accepted
  in Task 0's fallback.
- [ ] **Write the measurements doc** (versions, every probe with prediction/result/tier, the residuals,
  latency) and list for the user every scratch folder Codex marked trusted in `~/.codex/config.toml`
  during Tasks 0 and 14 (read-only; the user decides whether to prune them).
  **Stop condition:** any Phase A/C or TUI probe failing for the wrong reason, or not blocked → Codex
  support does not ship (D2); fix with systematic-debugging, then rerun the whole task.
  Commit `"Plan 4 E2E: Codex exec + TUI live, rules hold unapproved, definitions freeze verified"`.

---

### Task 15: Final whole-range review

- [ ] One fresh reviewer on the most capable model over `BASE..HEAD` with this plan's Review Focus and the
  Global Constraints, reading every changed file in full. Then the fix pass (each fix TDD, with a mutant
  when it guards a refusal path), the full suite, the mutation gate, `gen_agent_trees.py --check` and
  `--budget`, and `tools/verify-release.sh` against a release-style copy.
- [ ] Update the roadmap status block (`looks-good-lovely-fern.md`) and the spec's evidence table with any
  E-row Task 0/14 changed (versioned). **Never push; no tag** (Plan 8 releases).

## Verification (end of Plan 4)

1. `python -m pytest tests/ -q -p no:cacheprovider` green; the mutation gate kills every new entry
   (`inert-refusal-is-status-failed`, two `health-*`, `python-fallback-*`, `codex-definitions-are-frozen`,
   `generated-codex-files-cannot-be-hand-edited`, the ten Task 12 ids).
2. `tests/guard_corpus/expected.jsonl` unchanged since `BASE` (`git diff --stat BASE -- tests/guard_corpus` empty).
3. `python scripts/gen_agent_trees.py --check` in sync (all targets); `--budget` shows the real `codex.md`
   row ≤ 32,768.
4. Task 14's measurements doc: every probe passed for the right reason on `codex exec` and the TUI; rules
   held with hooks unapproved; the definition change disarmed and the restore re-armed; latency within the
   gate.
5. `git log --oneline BASE..HEAD` shows one or two commits per task, none pushed.

## Spec coverage of Plan 4

| Spec | Task |
|---|---|
| §5 adapter duties: map payloads, one shared patch-path helper | 3, 6 |
| §5 Codex gets only deny-JSON or `additionalContext`; ask → deny; never exit 2 | 6, 7, 12 |
| §5 `.codex/rules` second line (forbidden; prompt where prefix-expressible) | 9 |
| §5 frozen definitions + CI gate + CHANGELOG re-approve | 8 |
| §5 timeout fails open → wrapper deadline below the definition timeout; latency gate | 7, 14 |
| §5 declare the context limit | 6 (`INSTRUCTION_LIMIT_BYTES`, `CONTEXT_LIMIT`) |
| §5 heartbeat per invocation; wrapper denies by itself | 4, 7 |
| §5.1 fail-by-deny | 6, 7, 12 |
| §4 Codex column: `AGENTS.md` = core + codex, `.agents/skills/`, `.codex/hooks.json` + `config.toml`; skill bodies `$ARGUMENTS` | 10 |
| §6 D9 `project_doc_max_bytes = 65536` as margin; CI at 32,768 | 8, 10 |
| §6 D15 preferences injected on Codex | 6 (via Plan 3's `session.run`), 14 A7 |
| §7.3 conformance from captured payloads | 0, 12 |
| §7.4 adapter mutants (all seven named) | 1, 8, 12 |
| §7.5 live E2E for `codex exec` (+ TUI per D14), unreviewed-hooks run, latency from hook logs | 14 |
| §8 never mark a project trusted; print the approval step; canary reports NOT RUNNING | 11, 13 |
| D14 surfaces: TUI + exec live; others unclaimed; per-session detection from heartbeats | 0, 13, 14 |
| D16 failureMode adopted only if proven | 0, 8 (R6) |
| Roadmap *(Codex)*: verdict status + reason code | 1 |
| Roadmap *(Codex)*: Python fallback chain | 5, 7 |
| Roadmap *(Codex)*: health telemetry redesign + canary states | 4, 13 |
| Roadmap: kb-guard watches `AGENTS.md` | 11 |

**Deferred by design:** new rules (PowerShell forms, MO2 roots, DevBench incl. MCP, E16, E21, the
`Bash|PowerShell` matcher, ask triage, ReSaver executed-program anchor, cwd resolution in shell rules) →
Plan 5, which consumes the interfaces above; `guards off`, Layer 2 lock and `skydoctor` → Plan 6 (so the
spec §7.5 "guards off → restart relocks" probe runs in Plan 6/8); `ADAPTING.md` → Plan 7; `setup.sh
--agent`, the paths-file move, rename and the release → Plan 8; Codex desktop app / IDE extensions →
unclaimed until a live probe (D14), offered to the user as an optional probe after Task 14.

## Execution method

Subagent-driven (roadmap cadence for guard/engine work): a fresh implementer and a reviewer per task,
with Tasks 0 and 14 run by the orchestrating session because they need the user's terminal. Tasks 1–5 are
engine-side and can land before any Codex file exists; Tasks 6–11 depend on Task 0's measurements; 12–14
need everything before them.

## Controller rulings (2026-10-02)

- **R16 confirmed:** MO2 helper precedence (harness-repairs item d) is Plan 5's; Plan 5 rules on it.
- **Desktop app / IDE extensions:** not probed in Plan 4. Probe them after Task 14, per D14; claim nothing until a probe passes.
- **Latency:** if every rung of Task 0's ladder misses p95 <= 1 s, stop and bring the measured split to the user. Do not raise the gate.
