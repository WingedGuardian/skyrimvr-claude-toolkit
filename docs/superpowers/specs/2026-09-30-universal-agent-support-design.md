# Universal agent support — design

**Date:** 2026-09-30 · **Status:** DRAFT, awaiting user review · **Target release:** v4.0.0 (from v3.9.1)

The X4 toolkit's spec of the same date was used as a worked example. None of its decisions was carried
over unconfirmed: every decision below was re-derived for Skyrim and approved by the user.

## 1. Goal

Make the toolkit as functional under OpenAI Codex CLI as under Claude Code, **in the public release**.
Keep it portable to agents not known in advance, including by letting an unsupported agent adapt the
toolkit to itself.

### Decisions (user, 2026-09-30)

| # | Decision |
|---|---|
| D1 | Scope is the **public release**: installer, README, CI, Nexus. |
| D2 | **Blocking guards need strict, live-tested parity** on every named agent. If parity cannot be proven for an agent, that agent's support does not ship. Ask and advisory features may be weaker, with every gap documented in the README. On an agent that cannot ask, ask becomes deny with "run it yourself if intended". |
| D2a | **Backups before edits are in the strict tier** (same patch-path parser as blocking). |
| D3 | Renamed in the same release to **"Skyrim AI Assistant Toolkit"**, repo `skyrim-ai-assistant-toolkit`. The repo name was free on GitHub when checked on 2026-09-30. The tagline, the first Nexus line and the README state plainly that it is **for coding agents (Claude Code, Codex, …), not an AI NPC mod** (SkyrimNet, Mantella, CHIM). |
| D4 | **One neutral source, with per-agent files generated from it and committed.** Not Claude-as-source, and not hand-kept twins. |
| D5 | **Layered model** (§3). For Skyrim, Layer 2 locks **only the vanilla masters and archives**; everything else is made recoverable. The lock ships only if gate M-L2 (§9) passes. |
| D6 | **The agent may lift both protections**: unlock the vanilla files, and switch the guards off. Every lift is visible: advisory hook note, log, banner, doctor. Lifts expire at the next session start. |
| D7 | **Self-adaptation kit**: `ADAPTING.md`, a conformance test, a live canary, a CI toy-agent test, and one universal setup prompt. An agent is "partially supported: Layers 0–2" until it passes both conformance and the canary. |
| D8 | **OpenCode**: the interface is designed for it now; it ships once a live test passes. |
| D9 | **Instruction split** to ≤ 32,768 bytes, plus `project_doc_max_bytes = 65536` in the Codex project config as a margin only. CI still fails at 32,768. |
| D10 | **`KNOWLEDGEBASE.local.md` holds the user's facts AND instructions**: a fixed `## My preferences` section is read every session, and the rest is consulted before changes. |
| D11 | **The full testing set** (§7), including the live end-to-end release gate. |
| D12 | **Python engine (`skyguard`)**, confirmed by the latency spike (§2, E10). |
| D14 | *(plan review, 2026-10-01)* **Surface policy for every agent, not just Codex.** The CLI is the must-have (Codex: interactive TUI + `codex exec`). Every other surface (desktop app, IDE extensions, other agents' GUIs) is claimed **only once a live probe proves its hooks fire**; more surfaces are better. A surface whose hooks don't fire is "Layers 0–2 only", and `skydoctor`/canary detect that per session from heartbeats rather than assuming it (E24). |
| D15 | *(plan review)* **Preferences are injected.** Named agents get `KNOWLEDGEBASE.local.md` → `## My preferences` from the SessionStart hook (E5). The "read it" instruction stays as the fallback for unknown agents. |
| D17 | *(Plan 2 review, 2026-10-01)* **Python is REQUIRED for the safety hooks; `jq` no longer is.** Without Python the blocking hooks fail closed (deny every command), as today's do without `jq`. `setup.sh` records a concrete interpreter path through a `{{PYTHON_PATH}}` placeholder, warns loudly when Python is missing, and never auto-installs it. |
| D16 | *(plan review)* **Codex `failureMode: "block"`** ([openai/codex#41979](https://github.com/openai/codex/issues/41979)) is adopted once a live test proves a crashing hook blocks. That is a one-time change to the frozen definitions, with a CHANGELOG "re-approve" entry. **Not available on 0.159.3 (E25).** |

### Non-goals

- **An MCP server as a safety layer.** It cannot stop an agent bypassing it through its own shell. DevBench
  (an MCP server inside the game) is a surface the guards *cover* (§5), not a layer.
- **The author's personal install** (`C:/Games/...`). It is a test bed; adopting v4 there is a separate
  decision.

## 2. Evidence this design rests on

| # | Fact | Tier | Source |
|---|---|---|---|
| E1 | Codex hooks: a JSON `permissionDecision:"deny"` blocks. **`"ask"` and exit 2 + stderr both FAIL OPEN** (hook "Failed", command ran). | MEASURED, codex-cli 0.154.0 on Windows | scratch spike, run2 |
| E2 | **Untrusted Codex project hooks are skipped silently**: no warning in the output. | MEASURED | run1 |
| E3 | Codex shell commands run in **PowerShell 7** (`pwsh -Command`) but arrive as `tool_name:"Bash"`. Hook commands of the form `bash "<abs>"` run under Git Bash. | MEASURED | run2 hook log |
| E4 | Codex `apply_patch`: `tool_input.command` = patch text, with paths in `*** Add/Update/Delete File:` lines and no `file_path` field. | MEASURED | run2 hook log |
| E5 | Codex `additionalContext` and SessionStart stdout reach the model. | MEASURED | run2 |
| E6 | A skill with Claude-only frontmatter (`paths`, `user-invocable`, `argument-hint`) in `.agents/skills/` loads and runs in Codex. | MEASURED | run3 |
| E7 | `AGENTS.md` past 32 KiB is **silently truncated**. A project `.codex/config.toml` `project_doc_max_bytes` fixed it, in a trusted directory. | MEASURED, A/B | run4/run5 |
| E8 | Today's guards **fail by denying** (`GUARD INERT…`), not by asking. | READ | `protect-bash.sh:43,59,216`; `protect-files.sh:65,84` |
| E9 | Shipped `CLAUDE.md` = **53,772 bytes**; per-section sizes in §6. | MEASURED | |
| E10 | Latency, 50 runs per row, through `bash <wrapper>`. Python 3.10: 95th percentile **60–64 ms** idle and under CPU load. Today's bash guard: 297–670 ms. Python 3.12 (Store alias): 81–85 ms. **Caveat:** the CPU load did not slow even the bash baseline, so the earlier 4,620 ms measurement was **not reproduced**. Latency is therefore re-measured in live sessions (§7.5). | MEASURED | scratch spike |
| E11 | **Today's bash guard given a Codex `.esp` patch only ADVISES.** Wiring it to Codex unchanged would let a direct plugin write through. | MEASURED | spike correctness pass |
| E12 | The toolkit's Claude matcher is `Bash` only, so **the PowerShell tool is unguarded** for toolkit users. The author's install uses `Bash\|PowerShell`. | READ | `.claude/settings.json` in both repos |
| E13 | Delete rules cover the game root and config folders only; **MO2 `mods/` and `overwrite/` are unguarded**. `setup.sh` detects MO2 but `skyrim-paths.env` carries 3 keys. | READ | `setup.sh:202-290` |
| E14 | **Vortex hardlinks:** in the author's install, 140 of 911 plugins and archives in `Data/` are multi-link. The vanilla masters and archives are all single-link and writable. | MEASURED | `os.stat().st_nlink` |
| E15 | OpenCode: reads `AGENTS.md`, skills from `.agents/skills` and `.claude/skills`, has path-glob permissions, and blocks through plugins by throwing. Open risks: subagent hook coverage, the after-hook firing, WSL advised on Windows. **Not installed here.** | READ-DOCS (X4 research, 2026-09-30) | X4 spec §2 |
| E16 | Today's guard false-positived on a read-only `grep` whose *pattern* contained deletion words while naming the install path. | MEASURED | this session |
| E17 | Confirmed on **codex-cli 0.159.2** by the X4 session. Beyond `ask` and exit 2, these ALSO fail open: **exit 1, malformed JSON, a hook timeout, and a missing hook script**. Only a JSON deny blocks. | MEASURED (X4 session) | x4-claude-toolkit `docs/superpowers/measurements/2026-09-30-codex-spike.md` |
| E18 | Codex hook approval is stored in the user's `~/.codex/config.toml` as `[hooks.state.'<abs hooks.json>:<event>:<i>:<j>'] trusted_hash`, and **the hash covers the hook DEFINITION only**. Editing the called script kept the hook active; changing a definition field (timeout 5→6) made it **silently inert**; restoring the definition byte-for-byte reactivated it. | MEASURED (X4 session) | same |
| E19 | Codex `.rules`: `prefix_rule decision="forbidden"` **blocked even while the hooks were unreviewed**, matching inside the `pwsh -Command` wrapper. `decision="prompt"` asked the user in the TUI and was **rejected** under `codex exec` with approval=never. That makes it **fail-closed both ways: the only real "ask" on Codex**, and prefix-only. | MEASURED (X4 session) | same |
| E20 | A launch-time env switch (`X4_GUARD=off`) **cannot be flipped from inside the session**: an agent setting it in-session left every hook still seeing the launch value, under both Codex and `claude -p`. | MEASURED (X4 session) | same |
| E21 | **Today's guard does NOT block shell writes to plugins/archives; it only advises**: `echo x > Foo.esp`, `Set-Content Foo.esp` (as `Bash` or `PowerShell`), and `Copy-Item … Foo.esp -Force`. `Out-File` into an SKSE `.ini` gives no message at all. The "hard blocked" claim in README/CLAUDE.md holds only for the Edit/Write tools, **under Claude Code as well**. | MEASURED | spike harness, toolkit `protect-bash.sh` @ `aff1857` |
| E22 | An inherited `icacls <root> /deny <user>:(OI)(CI)(DE,DC)` blocked 8/8 delete primitives and left writes working; a DE deny on the file alone did not hold. Codex's built-in policy rejected `Remove-Item -Force` under approval=never before any hook ran. | MEASURED (X4 session) | same |
| E23 | Codex exit 2 failing to block is the **Windows-only** bug [openai/codex#48183](https://github.com/openai/codex/issues/48183): hooks run under `pwsh -Command`, which turns 2 into 1. JSON deny remains the only rule. | READ (issue) | |
| E24 | [openai/codex#24453](https://github.com/openai/codex/issues/24453): some Windows Codex command executions emit **no PreToolUse at all**, even with matcher `"*"`. This is why D14 exists. | READ (issue) | |
| E25 | A hook `failureMode: "block"` is **ignored silently on codex-cli 0.159.3**: a hook exiting 1 showed "Failed" and the command ran. | MEASURED | `docs/superpowers/measurements/2026-10-01-plan1-derisk.md` R8 |
| E26 | `codex exec` 0.159.3 fires `PreToolUse` for shell (pwsh) and for all three `apply_patch` forms, plus `SessionStart`. | MEASURED | same, R7 |
| E27 | `claude -p` (Claude Code 2.1.287) fires project `PreToolUse` hooks with matcher `"*"` for `Bash`, `PowerShell`, `Write` and `Edit`, plus `SessionStart`. | MEASURED | same, R6 |
| E28 | Layer 2 primitive matrix, 38 overwrite cells (Git Bash, pwsh 7, PS 5.1, Python, cmd, Node), each with an unlocked control. Blocked: **M-A** read-only bit 27/38; **M-B** file deny `(WD,AD)` 31/38; **M-C** = A+B 33/38; **M-D** = A + deny `(WD,AD,WA,WEA)` **38/38**. Rename-over and unlink-then-create (`mv`, `cp -f`, `sed -i`, `os.replace`, `Move-Item -Force`) pass anything that has no read-only bit the actor cannot clear; `-Force` clears the bit unless `WA` is denied. Reads (incl. `mmap`) work under all four. | MEASURED | `docs/superpowers/measurements/2026-10-04-plan6-ml2.md` Task 1 |
| E29 | **Rename-away is open under every file-level mechanism** (`mv f f.bak`): it needs only DELETE on the file. **xelib's `SaveFile` of a locked master uses exactly that**: the locked original moves to `Data/zEdit Backups/<name>.<stamp>.bak` and a new file takes its name. No candidate stops a deliberate xEdit/xelib save. | MEASURED | same, Task 3 C3/P32 |
| E30 | Claude Code's `Write`/`Edit` write a temp file and rename it over the target (`EPERM … rename '…tmp.<pid>.<hex>' -> '…'` under a lock). So they behave like rename-over: open under M-B, blocked under M-A/C/D. Codex `apply_patch` Update is blocked by all four; Delete only by M-D. | MEASURED | same, Task 2 |
| E31 | **Inside Codex's `workspace-write` sandbox, `icacls` cannot change any DACL** (Access denied, even on a file Codex created); `attrib` works. So M-B/C/D cannot be lifted in a sandboxed Codex session (M-D not even its bit). Under `danger-full-access`, and under Claude, all four lock and unlock in-session with no elevation. | MEASURED | same, Task 2 C8 |
| E32 | Vortex (READ from `app.asar`): game-file collision = rename the original to `.vortex_backup`, link the mod's copy, purge = unlink + rename back; on EPERM it offers an elevated **allow** ACE. Replayed through Node `fs` on a locked bed: all steps succeed under all four mechanisms; the lock travels with the renamed file and comes back on purge. | READ + MEASURED | same, Task 3 C5 |
| E33 | Applying/removing any mechanism over the 26 VR target names: **≤ 25 ms in-process** (ctypes `SetFileAttributesW` + `SetEntriesInAclW`/`SetNamedSecurityInfoW`), 1.3–10.7 s with one `attrib`/`icacls` process per file under load. | MEASURED | same, Task 3 C10 |
| E34 | Without a manifest, the read-only bit the toolkit set and one the user set are indistinguishable (M-A fails U3). ACE mechanisms can carry the distinction in the filesystem itself via a whitelisted mask variant. A Python-free unlock (`cmd` + `attrib` + `icacls`, `PATH=System32`) works for all four. | MEASURED | same, Task 3 U2/U3 |
| E35 | In `claude -p` stream-json, a **PreToolUse** hook's `systemMessage` arrives as a user-facing `system/informational` notice and is **not** in the model's context; a **SessionStart** `systemMessage` produced no such notice. `additionalContext` reaches the model on both. An in-session `export SKYGUARD_GUARDS=off` never reached a hook (re-confirms E20 for this toolkit). | MEASURED | `2026-10-04-plan6-m2.md` |

## 3. The layered model

| Layer | Reaches | Content |
|---|---|---|
| **0 — CLIs** | any agent that runs commands | existing `tools/` wrappers, plus `tools/skyguard` (engine CLI, lock/guards commands) and `tools/skydoctor` (which layers are live) |
| **1 — Plain text** | any agent that reads files | generated `AGENTS.md`/`CLAUDE.md`, skills (open `SKILL.md` standard), `KNOWLEDGEBASE.md`, `KNOWLEDGEBASE.local.md`, `ADAPTING.md` |
| **2 — Below the agent** | every agent, known or not | read-only lock on the single-link vanilla masters and archives (if M-L2 passes), plus snapshots and the restore script for everything else |
| **3 — In-loop hooks** | named agents | per-agent adapters around `skyguard`: pre-tool block/advise, backups, snapshots, session-start checks |

**What an unknown agent lacks (README):** blocking of destructive commands outside the locked vanilla
files, plugin/archive write blocking, backups before edits, DevBench guards, and session-start checks.

## 4. Source layout and generation

```
agent/
  instructions/core.md        shared; ≤ 32,768 bytes including the largest addendum (CI)
  instructions/claude.md      Claude addendum: hook names, `!` escape, /hooks
  instructions/codex.md       Codex addendum: trust step, $skill, PowerShell note, ask→deny
  skills/<name>/SKILL.md      9 existing + 3 new (devbench, triage-and-conflicts, xelib)
  guards/skyguard/            Python engine
  guards/adapters/{claude,codex}/
scripts/gen-agent-trees.py    writes every per-agent file
```

| | Claude Code | Codex | Unknown |
|---|---|---|---|
| Instructions | `CLAUDE.md` = core + claude | `AGENTS.md` = core + codex | `AGENTS.md` |
| Skills | `.claude/skills/` | `.agents/skills/` | `.agents/skills/` |
| Guards | `.claude/settings.json`, matcher **`Bash\|PowerShell`** and `Edit\|Write` and `mcp__devbench.*` | `.codex/hooks.json` + `.codex/config.toml` | Layer 2 only |

- **Generated files are committed** with a `GENERATED — edit agent/...` header. A CI check regenerates them and
  fails on any difference.
- **`setup.sh` stops editing files in place.** It writes `skyrim-paths.env` (moved to the toolkit root,
  adding `SKYRIM_MO2_MODS`, `SKYRIM_MO2_OVERWRITE` and `SKYGUARD_PYTHON`) and runs the generator. The paths in
  the instructions come from that file.
- **Skills keep their Claude frontmatter** (E6: Codex ignores unknown keys). Bodies stop relying on `$ARGUMENTS`
  alone: "the mod the user named (`$ARGUMENTS` in Claude Code)".
- **The interpreter is resolved once at install time** and stored as `SKYGUARD_PYTHON`. The Store alias
  (`WindowsApps\python3`) is accepted only if nothing else is found. No interpreter means the wrapper denies.

## 5. Guard engine and adapter contract

**One engine.** `skyguard` holds every verdict and side effect, ported from today's five hooks: the
`protect-bash` and `protect-files` rules, `backup-before-edit`, `snapshot-before-tool`, and
`session-kb-guard`. **New rules:**

- **PowerShell forms of each destructive command** (`Remove-Item`/`ri`/`del`/`rd`/`erase`, `Move-Item`,
  `Clear-Content`, and `Set-Content`/`Out-File`/`>` into a protected path). Shell is detected from the text,
  because of E3.
- **MO2 `mods/` and `overwrite/`** as protected roots (closes E13).
- **DevBench:** a deny-list of console commands known to crash or corrupt (`smp reset`, …) on the MCP tool
  *and* on `curl … :8920|:8921/api/tool/console`. Save-overwrite via `game save` is ask (deny on Codex).
- **The E16 false positive:** a deletion word inside a *quoted argument of a read-only command*
  (`grep`, `rg`, `Select-String`) does not count as a deletion.
- **Shell writes to plugins/archives (closes E21, for Claude as well).** Corrupting write primitives into
  `*.esp|esm|esl|bsa|ba2` are **deny**: `>`/`>>`, `tee`, `Set-Content`, `Add-Content`, `Out-File`,
  `[IO.File]::Write*`. Copy/move *onto* such a file (`cp`, `mv`, `Copy-Item`, `Move-Item`) is **ask**,
  because deploying our own mod's freshly built ESP into `Data/` is a legitimate workflow. Tools that write
  plugins through a proper library (`node … xelib`, `spriggit-cli.sh`, `automod-cli.sh`) are not matched.
  The README and core-instruction claim is corrected to match whatever the engine actually enforces.
  This is an **intentional strengthening**: the equivalence gate (§7.2) lists it as an expected
  difference, row by row.

**Contract** (versioned; JSON on stdin, JSON on stdout):
```
request: {v:1, agent, event: pre_tool|post_tool|session_start,
          kind: shell|write|mcp, shell: bash|powershell|null,
          command, paths[], mcp_tool, mcp_args, cwd}
verdict: {v:1, decision: allow|ask|deny, reason, context, log[]}
```

**Adapter duties, and nothing else:**
- Map the agent's own payload to a request. Codex patch paths are extracted by **one shared engine helper**.
- Map the verdict back. Claude passes through. **Codex receives only deny-JSON or `additionalContext`.**
  `ask` becomes deny with "needs your confirmation — run it yourself if intended". **Never exit 2, never `ask`**
  (E1, E17).
- **Codex second line: generated `.codex/rules/skyrim.rules`.** Every rule that can be expressed as a command
  prefix is also emitted there: denies as `decision="forbidden"`, asks as `decision="prompt"`. This gives
  Codex a real, fail-closed ask for prefix-matchable cases (ReSaver `--apply` modes, DevBench console
  `curl`), and **a block that holds even while the hooks are unreviewed** (E19, mitigating E2). Path-based
  rules cannot be expressed as prefixes and stay hook-only.
- **The hook definitions in `.codex/hooks.json` are frozen across releases** (E18). A definition change
  silently disarms every user's approved hook. All behaviour lives in the scripts the definitions call. A CI
  gate fails if a definition differs from the last tagged release, unless the CHANGELOG carries a
  "re-approve your Codex hooks" entry.
- **A timeout fails open on Codex (E17)**, and the wrapper cannot catch it. The Codex hook timeout is 60 s,
  and the latency gate (§7.5) is the protection.
- Declare the agent's context limit.
- Write a heartbeat per invocation (feeds `hook-canary` and `skydoctor`).
- The wrapper **denies by itself** if the engine is missing, crashes, or prints nothing parseable.

### 5.1 Failure handling (today's fail-by-denying behaviour, E8, preserved)

Any of these gives **deny** with the failure named (`GUARD INERT: …`), and never a silent allow: no input,
no interpreter, an unparseable payload or command, an install root that can't be resolved, or an engine crash.

### 5.2 Lifting protections (D6)

| Command (`tools/skyguard`) | Effect | Visibility |
|---|---|---|
| `lock status\|unlock\|lock` | removes/restores the Layer 2 read-only lock | advisory hook note ("tell the user why; relock when done"), audit log, `skydoctor` |
| `guards off [reason]` / `guards on` | **shadow mode**: rules still evaluate; nothing is blocked; each would-be deny returns as advice ("guards OFF — this would have been denied: …") and is logged | the same, plus a **GUARDS OFF** banner at every session start while off |

- **Lifts expire at the next session start:** relock and guards on, with a banner if anything was left lifted.
- **State lives in `.skyguard/state.json`.** It is written only by these commands, with a checksum. The engine
  treats any state it did not write as **guards on** and logs it (M2 confirms this is sufficient per agent).
  - *Amended 2026-10-04 (Plan 6 R6, M2):* same-user code can always recompute the checksum, so the checksum
    catches naive and accidental edits only. What forgery cannot buy is **silence or persistence**: a lift
    is honoured only in the **session that made it** (`session_id` equal, created after that session's
    start record) and never past `expires_at` (clamped to 8 h); every honoured lift emits a `SHADOW` audit
    line and model context **on every call**, emitted on the decision path itself; every session start
    reports and clears all lifts. M2 measured that every write route on which a hook runs is visible to
    that hook; the routes no hook sees (Codex with unapproved hooks) enforce nothing either (E2).
- **Last resort, documented:** each agent's own hook switch (`/hooks`; Codex `[features] hooks = false`).
  `skydoctor` reports that state as "Layer 3 absent".
- **False positive:** the user runs the command themselves (e.g. `!` in Claude Code); the case joins the
  corpus.

### 5.3 Layer 2 details

- **Targets**, chosen by the installer per game version: `Skyrim.esm`, `Update.esm`, `Dawnguard.esm`,
  `HearthFires.esm`, `Dragonborn.esm`, plus `SkyrimVR.esm` (VR), the AE `_ResourcePack` and CC masters (AE),
  and the vanilla `Skyrim - *.bsa`.
- **A file is locked only if it is single-link** (`st_nlink == 1`), checked per file, so a mod manager's
  hardlinked copy is never locked (E14). MO2's `mods/` is never locked.
- **Mechanism:** the read-only attribute (`attrib +R`). It needs no admin and one command reverses it. A
  refused write names the unlock command.
  - *Amended 2026-10-04 (Plan 6 Task 5, M-L2 measurements E28–E34):* pre-committed Table A selects
    **M-D: the read-only bit plus a file-level, non-inherited deny `(WD,AD,WA,WEA)` for the user's own
    SID** (bit first, then the ACE; removal in reverse). It blocks 38/38 overwrite primitives plus the
    agents' own tools; M-A (bit only) misses 11 including every `-Force`. Implementation constraints
    measured: apply in-process, never one process per file (E33); a pre-existing read-only target is
    tagged with the mask variant `(WD,AD,WA)` so a manifest-less unlock leaves the user's bit alone (E34).
    **Not stopped by M-D or any candidate:** a deliberate xEdit/xelib save (rename-to-backup, E29), and
    rename-away generally. **Not liftable from Codex's sandbox** (E31). Spec gate M-L2 is therefore **not
    passed as written** — see §9; whether the lock ships is a user ruling (D5).
  - *Correction (Plan 6 R13):* **a refused write cannot name the unlock command.** The bit and the ACE have
    no text channel; the OS says only "access denied". Instead: Layer 3 (`S-WRITE-PLUGIN`) names it for
    named agents; the core instructions say "access denied on a vanilla master or archive = the toolkit's
    lock; `tools/skyguard lock unlock <file>`"; `skydoctor` lists the locked set.
  - *Considered and rejected for `Data/`:* X4's inherited deny-delete ACL (E22). It stops deletes but not
    overwrites (the vanilla risk), and an inherited deny on `Data/` would break Vortex's purge, which
    deletes its hardlinks.
- **Recovery for everything else:** rate-limited snapshots of `plugins.txt`/`loadorder.txt`, the INIs and the
  save folder before risky commands (the same mechanism as today's `.psc` snapshot), plus
  `scripts/restore-from-backup.sh`.

## 6. Instructions

Shipped `CLAUDE.md` is 53,772 bytes. These sections move out; none is deleted:

| Section | Bytes | Destination |
|---|---|---|
| Installing the Optional Tools | 8,184 | `docs/installing-tools.md` |
| DevBench | 4,854 | skill `devbench` |
| skyrim-winner, papyrus-triage, crash-triage, "where these tools look" | 5,351 | skill `triage-and-conflicts` |
| Optional Devcontainer | 1,947 | `docs/` |
| AutoMod CLI (duplicates the skills) | 1,653 | existing skills |
| XEditLib API | 1,439 | skill `xelib` |
| **Total** | **≈23.4 KB** | core ≈ 30.4 KB before addenda |

- *Safety Rules* (6.9 KB) stays, rewritten agent-neutrally ("the guards", `tools/skyguard`). If core plus the
  largest addendum exceeds 32,768, *Safety Rules* gives up its hook-internals subsection to `docs/safety-philosophy.md`.
- **Gates:** generated `AGENTS.md` ≤ 32,768 bytes, and a **neutrality gate** fails if the core names a
  Claude-only tool or variable (`Edit`, `Write`, `CLAUDE_PROJECT_DIR`, `/hooks`, …).
- **Preferences reach the agent two ways (D10, D15):** for named agents the SessionStart hook injects
  `KNOWLEDGEBASE.local.md` → `## My preferences`. As the fallback for unknown agents, the core also tells
  every agent to read that section every session, and the rest of the file before changes. This is plain
  text, not `@import` (Codex does not expand imports).
- The prompt-audit fixes from 2026-09-30 that apply to the toolkit go into the core and skills during the split:
  the Caprica invocation, the Precision line in `port-to-vr`, the `skyrim-nif` "all mesh work" line, the
  `GetFormFromFile` own-plugin scoping, the AMR/SCAR lines, and the `inspect-esp` script path. Each is
  verified against the toolkit tree first; they are not copied from the install.

## 7. Testing and CI

1. **Shared guard corpus**: requests independent of any agent, paired with expected verdicts. Seeded from
   `test_hooks.py`, `test_mutations.py`, `test_hook_canary.py` and `test_kb_guard.py`. Additions:
   PowerShell forms, MO2/Vortex-staging paths, DevBench through MCP and through curl, E11, and E16. Every
   future miss or false positive becomes a case.
2. **Equivalence gate** (one-time, Claude-only, before any Codex work): today's bash hooks against `skyguard`
   plus the Claude adapter, over the corpus and real recorded payloads, diffed **per item**. Every row where
   the old hook was stricter is read by hand. Zero unexplained differences.
3. **Conformance per adapter**, from **payloads captured in real sessions** by a logging hook, never
   hand-written from docs. The 2026-09-30 Codex captures are the first fixtures. The Claude `PowerShell` tool
   payload is captured as well.
4. **Adapter mutants**, each of which must turn conformance red: drop paths; deny→allow; swallow engine
   errors; emit `ask` to Codex; emit exit 2 to Codex; emit malformed JSON; change a frozen `hooks.json`
   definition field (E18).
5. **Live end-to-end release gate** (run locally, not in CI): `claude -p` and `codex exec` against a scratch
   install copy, in Git Bash and PowerShell. It proves that these are blocked with the reason visible: a
   decoy `.esp` write, a delete in the install, a delete in MO2 `mods/`, and a DevBench `smp reset`. It
   proves backups and snapshots happen, session-start checks appear, untrusted Codex hooks are reported by
   `skydoctor`, and that a `guards off` → action → session-restart cycle relocks.
   **Hook latency is taken from the hook logs** (95th percentile ≤ 1 s).
   Probes use **non-delete writes** wherever possible, because Codex's built-in policy can reject a
   delete before the hook runs (E22), which would make the test pass for the wrong reason. Probes
   also run once with the hooks unreviewed, to prove that the `.rules` forbidden rules still hold (E19).
6. **Layer 2** gate M-L2 (§9).
7. **CI** (existing workflows): generator drift, conformance for all adapters, the `AGENTS.md` byte budget, the
   neutrality gate, `setup.sh --agent` for each value, cold install, upgrade from 3.9.1, the toy agent (D7),
   and existing gates updated for the new layout (`release.yml` hook count, `verify-release.sh`).
8. **Release** through the `release-review` and `releasing` skills.

## 8. Installer, rename, migration

- **`setup.sh --agent claude|codex|both|auto`** (`auto` = every agent found on PATH). `setup.sh` remains
  the only installer.
- **It never marks a Codex project trusted.** It prints the `/hooks` approval step. `skydoctor` and
  `hook-canary` report "Codex hooks NOT RUNNING (untrusted or unapproved)" until the first heartbeat (E2).
- **`skyrim-paths.env` moves to the toolkit root.** `.claude/skyrim-paths.env` is still read, with a
  deprecation notice.
- **Upgrade from 3.x:** an installed `CLAUDE.md` whose hash matches no shipped version (a hash list built from
  every tag) is copied into a marked block at the end of `KNOWLEDGEBASE.local.md`, with sorting instructions.
  It is never deleted and never guessed at. Backups, `settings.local.json` and KB files are untouched.
- **Rename** (D3): GitHub repo rename (old links redirect), zip name, README, `SETUP_PROMPT.txt` → universal
  setup prompt. The Nexus title and description change is the user's manual upload. **The GitHub rename is an
  outward action done at release time with the user's go-ahead.**

## 9. Measurements that gate a dependent phase

| # | Question | Blocks | If it fails |
|---|---|---|---|
| M1 | Capture real payloads: Claude `Bash`, `PowerShell`, `Edit`, `Write`, MCP; Codex Bash (pwsh), `apply_patch` add/update/delete, MCP, SessionStart startup/compact | 4, 6 | adapter built only from captures |
| M2 | Can an agent forge `.skyguard/state.json` past the checksum rule, per agent? (The env-var half is answered by E20: launch env cannot be changed in-session.) | 7 | §5.2 state-file integrity rule redesigned |
| **M-L2** | On a **scratch copy** of the vanilla files: (a) `skyguard lock`/`unlock` from inside a Claude **and** a Codex session, no admin; (b) xEdit loads the full order with locked masters, warning at most; clean-after-unlock and relock work; (c) Wrye Bash launches, scans, builds a Bashed Patch, and ghosting works against locked masters; (d) Git Bash `rm -f`, `Remove-Item -Force` and an xEdit save are stopped; (e) Steam "verify files" with the lock present | 7 | Layer 2 = recovery only (D5 option B) |
| M4 | Hook latency in live sessions (E10 caveat) | release | investigate before release |

*M-L2 status, 2026-10-04 (Plan 6 Phase A; `2026-10-04-plan6-ml2.md`):* (a) Claude: pass for all
mechanisms; Codex `workspace-write` sandbox: **fail** for M-B/C/D, pass for M-A (E31). (b) xelib loads
the order with masters locked, own-plugin saves work, unlock → save → relock works (C1, C2, C4); the
xEdit exe headless run stops on modal dialogs → batch B5. (c) Wrye Bash → batch B1. (d) `rm -f` and
`Remove-Item -Force`: stopped by M-D; **an xEdit/xelib save: not stopped by any candidate (E29)**.
(e) Steam verify → batch B2; whether the lock defaults on is decided by Plan 6 Table B after the batch,
never by momentum (R3). **Gate result as written: not passed ((a)-Codex, (d)); user ruling required
before the lock is built (Plan 6 Task 8).**

## 10. Phases

1. Measurements M1, M2.
2. Neutral source + generator, **Claude target only**: generated `.claude/` byte-identical to today's apart
   from headers.
3. `skyguard` + Claude adapter + **equivalence gate** (§7.2).
4. Instruction split, 3 new skills, `KNOWLEDGEBASE.local.md` preferences, toolkit prompt-audit fixes.
5. Codex adapter + generated Codex files, conformance, mutants, first live E2E.
6. New rules: PowerShell, MO2, DevBench, E16 fix (each with corpus cases first).
7. M-L2 → Layer 2 lock, lift commands, shadow mode, `skydoctor`.
8. `ADAPTING.md`, toy-agent CI test, universal setup prompt.
9. `setup.sh --agent`, migration, rename, README/CHANGELOG, final live E2E, **v4.0.0** via `release-review` +
   `releasing`. No push or tag without the user.
10. *(Follow-up)* OpenCode once a live test passes (D8).

Each phase lands as small commits on `master` with its gates green before the next starts.

## 11. `ADAPTING.md` (D7)

Written to the agent: *"you are an agent this toolkit does not support yet."*
1. A self-assessment checklist that produces a fixed-format capability report: instruction file + limit,
   skills dir, pre-tool hook (can it block? can it ask? does a failed hook fail open?), session start,
   payload capture.
2. The §5 contract, plus the rule **"find out how your agent fails: a hook that errors must not allow."**
3. The Claude and Codex adapters as commented worked examples, to copy rather than compose.
4. Where the adapter goes, and how to register a generator target.
5. **Required proof:** conformance passes, the canary (a harmless forbidden action on a decoy plugin) is
   blocked, and `skydoctor` shows Layer 3 live. Until then: "partially supported: Layers 0–2".
6. Rules: never edit the corpus or the engine to pass; adapters only translate; report every gap.
7. An upstream submission template.

## 12. Risks

- **The guard refactor is the riskiest step.** Mitigation: it is done alone, Claude-only, behind the per-item
  equivalence gate.
- **Codex changes weekly.** Mitigation: captured fixtures turn CI red on a protocol change, and the live E2E
  runs every release. E1/E2 are re-measured each release, since fail-open behaviour may change in either
  direction.
- **A self-ported adapter may be wrong about its own agent.** Mitigation: it cannot claim protection without
  conformance + canary, and Layer 2 holds regardless.
- **Users skip the Codex trust step.** Mitigation: a loud `skydoctor` and canary report every session, plus the
  README.
- **The read-only lock surprises a modder** cleaning masters. Mitigation: the error message names the unlock
  command, and the D6 lift flow makes it one step.

## 13. Resolved during review

- **D13 (user, 2026-09-30): no v3.9.2 patch.** The E12 matcher fix and the E21 shell-write rules ship in
  v4.0.0. The v4.0.0 CHANGELOG names both as security fixes to the 3.x guards.
