# Plan 6 — Layer 2: the vanilla-file lock, lifts with expiry, shadow mode, `skydoctor`

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every agent a protection that holds **below** the agent: a read-only lock on the game's
own files (the vanilla masters and archives, never the user's mods). It must hold even when an
agent's hooks fail open: Codex with unapproved hooks, an AllSigned policy, WSL, or an unknown agent.
It ships **only if gate M-L2 passes** (D5). The plan also delivers:

- the D6 lift flow: the agent may unlock the files or switch the guards to shadow mode. Every lift is
  visible and expires at the next session start;
- the M2 answer on forging the state file;
- `skydoctor`;
- the items deferred to this plan: backup manifests with checksums and atomic destinations, nested
  PSC/PEX snapshots with no 7-day exclusion, and agent self-protection.

**Architecture:**

- `tools/skyguard/lock.py` applies and removes the lock. It uses whichever mechanism Task 5 picks
  from the measurements.
- `tools/skyguard/state.py` owns `.skyguard/state.json`, which holds the lifts. The file is
  session-bound, checksummed and expiring.
- `tools/skyguard/doctor.py` composes the existing health, trust and lock readers into one report.
  It writes nothing.
- The engine gains:
  - shadow mode: rules still run, and nothing is blocked;
  - the lift and self-protection rules;
  - session-start expiry.
- `tools/skyguard-unlock.cmd` is the Python-free escape hatch.

The measurement scripts live in `scripts/layer2-spike/`. They run against a sandboxed scratch tree
only.

**Tech Stack:** Python ≥ 3.8 syntax, stdlib only (engine floor). bash, PowerShell 5.1/7, `cmd.exe`,
`attrib`, `icacls`. pytest 8. Node only for the xelib probes.

**Spec:** `docs/superpowers/specs/2026-09-30-universal-agent-support-design.md`. The parts this plan
uses:

- D5 (Layer 2 only if unlocking works and xEdit and Wrye Bash are unaffected) and D6 (lifts);
- §3 Layer 2; §5.2 lifting protections; §5.3 Layer 2 details;
- §7.5 (the `guards off` → restart → relock cycle); §9 M2 and M-L2;
- evidence E2, E14, E17, E20, E22.

Roadmap row 6 is in `C:/Users/Moona/.claude/plans/looks-good-lovely-fern.md`, together with its
**FINAL MANUAL BATCH** block. House style follows Plan 2
(`docs/superpowers/plans/2026-10-01-plan2-skyguard-engine.md`) and Plan 5 (branch `plan5`): corpus rows
first, priced against real history, one mutation twin per clause, and measured rather than assumed.

---

## Facts this plan rests on (re-measure before relying on any of them)

| # | Fact | Tier | Source |
|---|---|---|---|
| L1 | In the author's VR install these 6 files are all **single-link** (`st_nlink == 1`), with attributes `0x20` (ARCHIVE, **not** read-only): `Skyrim.esm`, `Update.esm`, `SkyrimVR.esm`, `Dawnguard.esm`, `Skyrim_VR - Main.bsa` and `Skyrim - Voices_ru0.bsa`. Sizes: `Skyrim.esm` is 249,753,412 bytes, `Skyrim_VR - Main.bsa` 438 MB, `Skyrim - Voices_ru0.bsa` 2.0 GB. | MEASURED (6 files, read-only `os.stat`, 2026-10-04) | this plan's prep |
| L2 | `Data/` also holds names that **look vanilla and may not be**: `Dawnguard Sentries SE.bsa`, `Skyrim Extended Cut - Saints and Seducers.bsa`, `Skyrim Project Optimization - Full ESL Version.esm` and `Skyrim - Voices_ru0.bsa`. Whether the last is a mod or a localized vanilla archive is not known. **A glob target list (`Skyrim - *.bsa`, `Dawnguard*`) would lock mod files.** | READ (directory listing) | this plan's prep |
| L3 | The X4 toolkit measured the read-only attribute on Windows 11. It blocks **11 of 14** write primitives. It does **not** block `rm -f` (Git Bash), `Remove-Item -Force` or `Copy-Item -Force`: `-Force` clears the attribute. That measurement was first wrong because the `bash` it ran was the WSL stub; **a control on an unlocked file is what exposed it.** | MEASURED by the X4 session (evidence, not truth: re-measured in Task 1) | `x4-claude-toolkit/scripts/x4lock.py` docstring |
| L4 | X4's first ACL lock `icacls /deny <user>:(W,D,WDAC,WO)` **locked the user out**. `W` includes SYNCHRONIZE, so reads were denied too. `WDAC` made the deny unremovable unelevated on files owned by Administrators. X4 now uses **only** `(OI)(CI)(DE,DC,WD,AD)` (mask 65606) on a *directory*, and measured reads 12/12 working, writes 20/20 blocked, and removal by the same user 47/47. | MEASURED by the X4 session | `x4-claude-toolkit/scripts/x4refguard.py` |
| L5 | A `DE` deny on a **file alone** did not stop deletes: the parent's FILE_DELETE_CHILD grants it. An inherited deny-delete on the directory did. | MEASURED (X4) | spec E22 |
| L6 | Spec §5.3 rejected an **inherited** deny on `Data/`, because Vortex's purge deletes its hardlinks there. A **file-level, non-inherited** deny (`WD,AD`, no `DE`) has **not** been considered or measured. | READ | spec §5.3 |
| L7 | `backup.py` writes to `backups/<YYYYmmdd_HHMMSS>__<path with / \ : → _>` with `shutil.copyfile`, directly to the final name. Two edits of one file in the same second therefore **overwrite** the first backup, and a failed copy leaves a partial file under the final name. | READ | `tools/skyguard/backup.py` (plan5 worktree) |
| L8 | `scripts/restore-from-backup.sh` rebuilds the original path with `sed 's/_/\//g'`. **Every underscore becomes a slash**, so `Skyrim_VR - Main.bsa` or `My_Mod.esp` is restored to the wrong path. There is no manifest. | READ | `scripts/restore-from-backup.sh:37` |
| L9 | `snapshot.py` copies only **top-level** `Data/Scripts/Source/*.psc` and `Data/Scripts/*.pex`, only files **< 7 days old**, rate-limited to 60 s. The SSE layout `Data/Source/Scripts/` is not looked at. | READ | `tools/skyguard/snapshot.py` (plan5 worktree) |
| L10 | Plan 5 R16 moved the backup manifests and nested snapshots to this plan. Plan 5 **Task 16 measures their inputs**: counts, bytes, the share older than 7 days, and per-call snapshot time. **It had not run on 2026-10-04.** | READ | plan5 doc, Task 16; no "Inputs for the side-effects plan" section in the pricing doc |
| L11 | Codex fails open on everything except a JSON deny (E1, E17). Unapproved project hooks are skipped silently (E2). A changed definition disarms silently (E18). `.rules` `forbidden` holds without hooks (E19). An in-session env change has no effect on the hooks (E20). | MEASURED | spec §2 |
| L12 | On this machine, `wsl -l -q` lists only `docker-desktop`, so there is no general-purpose distro. | MEASURED 2026-10-04 | prep |
| L13 | A depth-≤ 3 search found no Wrye Bash and no MO2. Vortex is installed (`C:/Program Files/Black Tree Gaming Ltd/Vortex`). xEdit executables are in the author's `tools/xedit/` (`xTESEdit64.exe`, …). **The search finding nothing is a lead, not a fact.** | MEASURED (search) / READ | prep |
| L14 | xelib exposes `SetGamePath` and `SaveFile(Cardinal, PWChar)`, and the toolkit loader honours `$PLUGINS_TXT`. A scratch game folder can therefore be loaded headlessly, never through the registry's real path. | READ | `tools/zedit/.../xelib/src/cpp/lib.h:30,99,129`; toolkit `tools/xelib/active-plugins.js` |
| L15 | Plan 4's health telemetry (`health.py`, `hook-canary`) and Codex trust reader (`codex_trust.py`) exist on branch `plan4`. Neither is merged. Plan 4's Codex adapter turns any `ask` into deny (Plan 4 R4). Plan 4's `.rules` carry registry deletes only (Plan 4 R5). | READ | plan4 worktree |
| L16 | Claude Code hook JSON has a `systemMessage` field documented as shown **to the user**. Whether Codex shows any hook-provided text to the user, apart from the static `statusMessage`, is **not measured**. | READ-DOCS (Claude) / UNKNOWN (Codex) | |

---

## Preconditions

**Phase A (Tasks 0–5, measurement only) may start now.** It adds new files only: spike scripts,
measurement docs and spec amendments. It edits no engine file.

**Phase B (Tasks 6–19) starts only when every one of these holds; Task 6 checks them:**

- Plans 4 and 5 are reviewed and merged into master (`git merge-base --is-ancestor plan4 master`, the
  same for `plan5`). Plan 6 edits `run.py`/`engine.py`, the rule registry, `test_mutations.py` and the
  health readers. All four are mid-flight in those branches.
- Plan 5's `scripts/guard-corpus/price.py` and `intended.jsonl` exist on master.
- Plan 5 Task 16 has produced its "Inputs for the side-effects plan" numbers. If it has not, Task 6
  takes them, read-only, with the same method.
- Task 5 is committed, which means the mechanism decision is recorded.
- No replay, pricing, mutation, latency run or suite is running. Never edit the tree while one runs.

## Branching

Phase A commits **directly on master**. It adds only new files under `scripts/layer2-spike/`,
`docs/superpowers/measurements/` and the spec, so it cannot conflict with the Plan 4/5 merges.
Phase B runs in worktree `../skyrimvr-toolkit-plan6`, on branch `plan6`, cut from master after the
preconditions hold. It merges after the Phase B final review. **Never push. Never tag.** A release goes
through `release-review`.

## Global Constraints

- **Scratch only, never the real install.** Every lock, unlock or write probe targets
  `C:/Temp/skyguard-ml2/` (the shallow path avoids xEdit/MO2 long-path failures). Every mutating call,
  whether `attrib`, `icacls`, a write probe or `skyguard lock` itself, first asserts that its target
  resolves strictly under `$SKYGUARD_LOCK_SANDBOX`. If not, it raises `SandboxViolation`, which derives
  from `BaseException`, deliberately not from `Exception`, so nothing swallows it (X4 pattern). The test
  suites set the variable to their tmp dir. **Scratch copies are real copies.** Before any lock, check
  that the copy's `st_ino` differs from its source's and that `st_nlink == 1` on both. An attribute or
  an ACL belongs to the file object, so locking a hardlinked "copy" locks the user's real file.
- **The real install is read, never written.** Task 0 records the sha256 and attributes of every real
  file it copies. Task 5 and Task 19 re-read them, and any difference stops the plan.
- **ACL masks are whitelisted.** A probe may deny only from `{WD, AD, WA, WEA}`. It never uses
  `W`/`M`/`F`, `S` (SYNCHRONIZE), `WDAC`, `WO`, `D` or `DE` (L4, L5). Every apply is followed by a
  re-read, and every remove by a check that 0 deny entries remain, as the same unelevated user.
- **Every probe has a control.** The same primitive is run against an unlocked twin file, and it must
  change that file. A probe whose control fails is **UNMEASURED**, never "blocked". Git Bash is called
  by absolute path (`C:/Program Files/Git/bin/bash.exe`), never as `bash` from PATH (L3: the WSL stub).
- **Pre-committed decisions.** The decision tables in this doc are copied into the measurement doc and
  committed **before** the first result is recorded. A result never edits its own table. A table found
  wrong is replaced by a new dated table that states why, and the old one stays.
- **Corpus rows first, priced before landing** (Plan 5 rules). Every new rule gets hand-authored
  `intended.jsonl` rows, then `price.py` against the previous commit. Every newly denied or newly asked
  item is read by hand; for advise, all hits up to 100, else a seeded random 100. **Cap:** more than
  200 newly denied plus newly asked items → STOP and narrow the rule.
- **Mutation twins**, one per clause, ids `rule-<RULE-ID>-<clause>` and `l2-<area>-<clause>`.
- **Message contract** (Plan 5): ≤ 2 lines of ≤ 200 characters, files named by basename, never echoing
  the command.
- **No user step mid-plan.** Every step that needs a person (a GUI, a TUI, Steam) is written into
  Task 19's batch document and appended to the roadmap's FINAL MANUAL BATCH. Each such step has a
  pre-committed outcome table, and until the batch result is in, the code ships in its **safe default**
  (see R3).
- Test command (Plan 5 Task 15 onward): `python -B tests/run_harness_tests.py tests/ -q -rs`. Latency
  gate: `scripts/guard-latency.py`, p95 ≤ 1 s, never concurrent with another heavy run.
- Commit each finished task. Never push.

---

## Rulings

Each is given as the Ruling, then why, then the cost if wrong.

- **R1** — Ruling: the lock covers an **explicit per-edition name list** intersected with "exists" and
  "single-link". It never uses a glob:
  - VR: `Skyrim.esm`, `Update.esm`, `Dawnguard.esm`, `HearthFires.esm`, `Dragonborn.esm`,
    `SkyrimVR.esm`, `Skyrim_VR - Main.bsa`, and the base `Skyrim - <Animations|Interface|Meshes0|Meshes1|Misc|Patch|Shaders|Sounds|Textures0–8|Voices_<lang>0>.bsa`
    names;
  - SE/AE: the same masters and archives minus the two VR files, plus `_ResourcePack.esl/.bsa`, plus
    the Creation Club plugins **as listed in the game's own `Skyrim.ccc`** with their same-basename
    BSAs.
  
  The official localized voice/interface names are included. — Why: L2 (a glob locks mods), and
  `Skyrim.ccc` is the game's own manifest, not a guess. — Cost if wrong: a mod shipped under a vanilla
  name and installed by hand (not by a manager, since a manager's copy is multi-link) gets locked. That
  is harmless: it still loads, and `status` lists it with its size so the user can unlock it.
- **R2** — Ruling: the candidate mechanisms are **M-A** read-only attribute; **M-B** file-level
  non-inherited deny `(WD,AD)` for the current user's SID; **M-C** = A+B; **M-D** = A + deny
  `(WD,AD,WA,WEA)`, where denying write-attributes stops `-Force` from clearing the bit. A directory
  ACL is not a candidate. — Why: L3–L6. The spec rejected only the inherited directory deny, and M-D
  is the only cheap candidate that may stop `-Force`. — Cost if wrong: an untested fifth mechanism
  would have been better; the measurement doc lists what was not tried.
- **R3** — Ruling: the lock ships with **`setup.sh` not applying it** (`layer2.default = "off"` in
  `skyguard/lock.py`) until the Final Manual Batch results are in. A one-line, separately committed
  flip to `"on"` follows **only** if Task 5's automated rows **and** the batch rows (Wrye Bash, Steam
  verify) meet Table B. — Why: two of D5/M-L2's ship conditions can only be measured by the user, after
  the code exists. A default chosen by momentum is the failure this prevents. — Cost if wrong: v4.0.0
  could release with the lock opt-in, if the batch slips past the release.
- **R4** — Ruling: **the filesystem is the truth for "is it locked"; the manifest is a record.**
  `status` re-reads attributes and ACLs. Session start re-locks the **derived target set** (R1), not
  whatever `lock.json` lists, unless a persistent `lock disable` is recorded. — Why: forging or
  deleting the manifest must not be able to shrink the protected set silently (M2). — Cost if wrong:
  a user who unlocked one file by hand with `attrib` sees it relocked at the next session, with a
  banner saying so.
- **R5** — Ruling: unlock never clears protection the toolkit did not set. `lock.json` records each
  file's **prior** attribute and prior explicit ACEs. A file that was already read-only is recorded as
  `preexisting_ro`, and unlock leaves it read-only. — Why: some users set the bit themselves. — Cost if
  wrong: a lost manifest leaves `unlock --scan` unable to tell the two cases apart; it then clears only
  the bit and prints which files it could not attribute.
- **R6** — Ruling: **forgery cannot buy silence or persistence; it cannot be prevented.** Same-user
  code can rewrite any file and compute any checksum, and DPAPI is open to the same user. So:
  - a lift is honoured only in the **session that made it** (`session_id` in the state equals the
    request's), and never after `expires_at` (at most 8 h);
  - every honoured lift produces, **on every call**, a `SHADOW` audit line plus model context;
  - every session start reports and clears every lift;
  - a state the engine cannot verify (bad checksum, unknown version, unparseable, oversized, not a
    regular file) reads as **guards on + lock on**, with a log line.
  
  The checksum catches naive and accidental edits only, and the docs say exactly that. — Why: D6 lets
  the agent lift anyway, so the only extra a forger could gain is silence or persistence. — Cost if
  wrong: M2 Task 4 shows a route that stays silent → Table M decides.
- **R7** — Ruling: a persistent off (`lock disable`) is **ask on Claude and deny on Codex** (via Plan 4
  R4) when an agent runs it. It is shown in a banner **at every session start for as long as it
  lasts**. `lock unlock` and `guards off` are D6 lifts: allowed with an advisory, and expiring. — Why:
  D6 grants the agent temporary lifts. A permanent one is the user's to make, and a banner that never
  stops is the visibility that cannot be forged away. — Cost if wrong: a user who wants the lock gone
  for good sees the banner every session until they `lock enable` or uninstall. That is intended.
- **R8** — Ruling: `guards off` is **engine shadow mode**. Rules evaluate; `deny`/`ask` become `allow` +
  context `Guards OFF: this would have been denied …`; side effects (backups, snapshots) **still run**;
  **`GUARD INERT` (a broken engine) still denies**, and so do Codex `.rules` `forbidden` (outside the
  engine, L11) and the Layer 2 lock (a separate lift). — Why: spec §5.2. A broken engine cannot read
  the state it would need to honour. Backups are the net that makes shadow mode safe. — Cost if wrong:
  a user who switched guards off *because* the engine is broken is still blocked. The documented last
  resort is the agent's own hook switch.
- **R9** — Ruling: expiry runs at SessionStart for every `source` except `compact`
  (`startup`/`resume`/`clear`). With no session start, as with an unknown agent, a lift is cleared by
  `expires_at` the next time anything reads it, or by the next `skydoctor` run. **Nothing schedules a
  background relock.** — Why: no scheduled tasks, no service, nothing running outside the agent. —
  Cost if wrong: on an agent with no hooks, an unlock can stay in place until someone runs `skydoctor`
  or a named-agent session starts. The README states this.
- **R10** — Ruling: the Vortex compatibility rows are measured by **replaying Vortex's own file
  operations** (hardlink deploy, purge, game-file collision backup), confirmed by READING Vortex's
  installed bundle, on scratch. Vortex is **not** pointed at a scratch game. — Why: managing a
  different game folder would rewrite the user's Vortex state, which is outside scratch. — Cost if
  wrong: a Vortex operation that was not replayed breaks on a locked file. The operation then fails
  with "access denied" on a vanilla name; the README entry says to unlock, and the release E2E has a
  row for it.
- **R11** — Ruling: POSIX (Proton/Linux) `lock` exits 3 `unsupported` in v4.0.0. — Why: X4 measured
  the weaker POSIX guarantee (9/14, unlink is authorized by the directory), and there is no device to
  test on. — Cost if wrong: Proton users get no Layer 2. The README says so.
- **R12** — Ruling: self-protection of the guard's own files is **deny for `.skyguard/**`** (only the
  `tools/skyguard` CLI writes it). For the guard code and config, the decision is **priced, ask or
  advise**:
  - `.claude/hooks/**`, `.claude/settings*.json`, `.codex/**`, `tools/skyguard/**`, `agent/guards/**`;
  - the user-level `~/.claude/settings*.json` (`disableAllHooks`) and `~/.codex/config.toml`.
  
  Separately, **at every session start** a hash check of the guard files against the shipped
  `GUARD-FILES.sha256` produces a banner. — Why: the author's history is full of hook maintenance the
  user asked for, so a blanket ask there is approval fatigue. Tamper-evidence costs nothing per call. —
  Cost if wrong: on Codex an ask is a deny (Plan 4 R4), so a priced ask there blocks legitimate
  maintenance. The deny message says "run it yourself".
- **R13** — Ruling: **a write the lock refuses cannot carry a message.** The OS says only "access
  denied". The spec's line "a refused write names the unlock command" is corrected:
  - Layer 3 (S-WRITE-PLUGIN, already a deny) names the command for named agents;
  - the core instructions tell every agent: "access denied on a vanilla master or archive = the
    toolkit's lock; `tools/skyguard lock unlock <file>`";
  - `skydoctor` lists the locked set.
  
  — Why: the attribute and the ACL have no text channel. — Cost if wrong: an unknown agent that never
  read the core text retries or works around it. The lock still holds.
- **R14** — Ruling: Phase A on master, Phase B in worktree `plan6`. — Why: Phase A adds new files
  only; Phase B edits files Plans 4/5 own until they merge. — Cost if wrong: one rebase.
- **R15** — Ruling: third-party GUI tools that M-L2 needs and that are absent (Wrye Bash standalone,
  MO2 portable) are fetched as **portable archives into `C:/Temp/skyguard-ml2/tools/`** from their
  official GitHub releases, with the release's published checksum verified. No installer, nothing in
  Program Files or the registry, nothing in any game `Data/`. — Why: they are measurement tools, not
  mods (the Vortex-only rule covers mods), and the user ruled out mid-plan questions. — Cost if wrong:
  the user did not want them downloaded. They are listed in the batch cleanup and deleted there.

---

## Pre-committed decision tables (copied verbatim into the measurement doc in Task 0, before any result)

### Table A — mechanism (decided in Task 5 from Tasks 1–3)

Rows are probe ids from Tasks 1–3.

- **MIN-BAR**: P1 `>`, P3 `cp`, P9 `Set-Content`, P19 python `open('wb')`, P22 `shutil.copyfile`, P29
  Codex `apply_patch` Update, P30 Claude `Write`. These are the accident shapes that actually happened
  (L3: all 4 X4 incidents were in this set).
- **REQUIRED**: every overwrite row P1–P33.
- **COMPAT**: C1–C10. **READS**: R1–R3. **UNLOCK**: U1–U5.

| Outcome for a mechanism | Verdict for that mechanism |
|---|---|
| Any READS row fails | ineligible |
| Any UNLOCK row fails, including Python-free, manifest-missing, or ≥ 1 residual deny ACE | ineligible |
| Any COMPAT row fails that is a **routine** operation: C1 xEdit/xelib load, C2 save own plugin, C5 Vortex deploy/purge/collision, C6 MO2 VFS | ineligible |
| A COMPAT row fails that is a **deliberate master edit** (C3 is *supposed* to fail; C4 clean-after-unlock must pass) | C4 failing → ineligible |
| Apply + remove over the full VR target set > 2 s total (needed for session-start relock) | ineligible |
| Eligible but misses any MIN-BAR row | ineligible (a lock that misses the common accidents is false assurance) |

**Choice:** among the eligible mechanisms, take the one that blocks the most REQUIRED rows. On a tie,
the one with fewer parts wins (M-A before M-B before M-C before M-D). **No eligible mechanism → no lock
(D5 option B):** Layer 2 is recovery only, Tasks 8–11's lock parts are skipped (state, shadow mode,
`skydoctor`, self-protection and recovery still ship), and `skyguard lock` prints
`not available: M-L2 failed (<row>)`. The deletion rows D1–D7 are recorded but **never decisive**: a
deleted master is loud, since the game won't start and Steam verify restores it, while an overwritten
one is silent.

### Table B — ship-default flip (decided after the Final Manual Batch; R3)

| Batch result | Default |
|---|---|
| B1 Wrye Bash scans, builds a Bashed Patch, and ghosts/unghosts an inactive plugin with the masters locked; at most a warning | required for "on" |
| B1 fails on scan or Bashed Patch | **no lock in v4.0.0** (D5); `lock` prints `not available` |
| B1 fails only on an operation on a vanilla master (masters are never ghosted while active) | "on"; the README names the operation and the unlock |
| B2 Steam verify repairs the corrupted locked file; the attribute ends cleared or kept | "on"; session start reports drift and relocks |
| B2 Steam verify errors or loops on a locked file | "on" **only for VR** (no Steam updates since release; INFERRED, re-checked against `appmanifest_611670.acf` build history at flip time); SE/AE opt-in, with "unlock before Verify/Update" |
| B2 Steam reports success but leaves the corrupted locked file in place | opt-in on every edition, with a warning (the lock would hide corruption) |
| B2 not run | opt-in on every edition |

### Table M — state-file forgery (decided in Task 4)

| Outcome | Design |
|---|---|
| S1–S6 (below) behave as R6 says, **and** every write route F1–F8 that is not blocked still yields per-call SHADOW lines + context + a session-start banner + expiry | spec §5.2 + R6 are sufficient; build Task 7 as written |
| A route makes an off state honoured **without** a per-call SHADOW line (silence) | move the SHADOW emission to the decision path itself (`engine.pre_tool`, after combine), never optional; re-measure |
| A forged state outlives its session (persistence) | the session-binding check moves into the state reader (no caller can skip it); re-measure |
| Either cannot be closed | `guards off` becomes **launch-environment only** (`SKYGUARD_GUARDS=off`, unforgeable in-session per E20, re-measured as F8). D6's "agent may switch guards off" is reported as unmet for guards (lock lifts unaffected) in the close-out |

### Table V — user-visible channel per agent (decided in the batch, B3/B4)

| Outcome | Claim allowed in README |
|---|---|
| The agent shows the hook's `systemMessage` (or equivalent) to the user on a lift and at session start | "every lift is shown to you" for that agent |
| Shown to the model only | "every lift is reported to the agent and logged; `skydoctor` shows it", never "shown to you" |

---

## Review Focus

1. **Scratch leaking into the real install.** A hardlinked copy, a junction, a path resolved through
   `..`, or a sandbox check that compares strings instead of resolved paths. → the sandbox test twins;
   the Task 0 and Task 5 real-install hash/attribute diff.
2. **An ACL that cannot be removed.** WDAC/WO/S slipped into a mask; a file owned by another principal.
   → whitelist test; U-rows; Task 3 C9.
3. **A green probe for the wrong reason.** The WSL `bash`, Codex's sandbox refusing the write before the
   lock is reached, Codex's built-in policy rejecting `Remove-Item -Force` before any hook (E22), the
   probe writing to a different file than it thinks. → controls on every row.
4. **The lift that never expires.** Session-start ordering: relock must run **before** anything that
   reports "locked". A lift written after its session's start record. Clock skew in `expires_at`.
5. **Shadow mode as a silent allow.** A deny path that bypasses the shadow conversion and so blocks, or
   one that converts without logging.
6. **Self-protection that blocks the toolkit's own maintenance** (the author's history) or misses an
   `apply_patch` route.

---

### Task 0: Scratch bed, safety harness, real-install baseline, decision tables (Phase A)

**Files:** Create `scripts/layer2-spike/{sandbox.py,mkbed.py,baseline.py}`,
`tests/test_layer2_spike_sandbox.py`, `docs/superpowers/measurements/2026-10-XX-plan6-ml2.md` (tables
A, B, M, V copied verbatim from this plan, plus a results skeleton).

- [ ] **Step 1 — sandbox:** `sandbox.py` provides `assert_inside(path)`. It resolves `path` (realpath,
  case-folded, `\\?\` stripped) and `$SKYGUARD_LOCK_SANDBOX`, and raises `SandboxViolation(BaseException)`
  unless the path is strictly inside. Tests:
  - inside → ok;
  - the sandbox root itself → violation;
  - `..` escape → violation;
  - a junction inside the sandbox pointing outside (`mklink /J`) → violation;
  - unset variable → violation;
  - mutation `l2-sandbox-realpath` (compare unresolved strings) must turn the junction test red.
- [ ] **Step 2 — free space:** total size of the copy set × 1.3 must be free on `C:`; else stop.
- [ ] **Step 3 — baseline:** `baseline.py record` writes `C:/Temp/skyguard-ml2/real-baseline.json`
  (outside the repo): sha256, size, `st_ino`, `st_nlink`, `st_file_attributes`, `icacls` text (read
  only) of each real file to be copied. The set: the 6 VR masters, `Skyrim - Interface.bsa` (strings),
  `Skyrim - Shaders.bsa` (the smallest vanilla BSA, for BSA rows) and `SkyrimVR.exe`.
- [ ] **Step 4 — bed:** `mkbed.py` builds `C:/Temp/skyguard-ml2/game/` (`SkyrimVR.exe`, `Data/`,
  `plugins.txt`) by `shutil.copyfile` (data only, never `copy2`, never a link). After each copy it
  asserts `st_ino` differs from the source and `st_nlink == 1` on both. It also creates
  `Data/Spike.esp` (an empty plugin made by xelib in Task 3), decoys for the write rows
  (`Data/Decoy-<n>.esm`, 64 KiB random, plus a text decoy `Data/Update.esm` in a **separate** bed
  `C:/Temp/skyguard-ml2/textbed/` for `apply_patch`, which needs text) and unlocked control twins.
- [ ] **Step 5:** Commit the scripts, the test and the measurement doc with the tables **before any
  result**: `"Plan 6 Task 0: Layer 2 spike harness and pre-committed decision tables"`.

### Task 1: M-L2a — the primitive matrix, four mechanisms × every write route (Phase A)

**Files:** `scripts/layer2-spike/{lockprobe.py,primitives.py}`; results in the ml2 doc.

- [ ] **Step 1:** `lockprobe.py apply|remove|show <mechanism> <file>`. It implements M-A…M-D (R2) with
  `attrib` and `icacls /deny *<SID>:(<mask>)` (the SID from `whoami /user`), and removes with
  `icacls /remove:d *<SID>` + `attrib -R`. After each action it re-reads the state and records
  `Get-Acl` and the attributes. It is sandbox-asserted. Whitelist test: any mask outside
  `{WD,AD,WA,WEA}` raises; mutation `l2-mask-whitelist`.
- [ ] **Step 2:** `primitives.py` runs each row against a locked decoy **and** its unlocked control, as
  a child process, and records rc, stderr, the after-hash, the attributes, and any debris (temp
  files). Rows:
  - **Overwrite (REQUIRED):**
    - Git Bash (absolute path): P1 `printf x > f`, P2 `>>`, P3 `cp`, P4 `cp -f`, P5 `tee`,
      P6 `dd of= conv=notrunc`, P7 `sed -i`, P8 `mv src f`;
    - pwsh 7 **and** Windows PowerShell 5.1: P9 `Set-Content`, P10 `Set-Content -Force`, P11
      `Out-File`, P12 `Out-File -Force`, P13 `Add-Content`, P14 `Copy-Item`, P15 `Copy-Item -Force`,
      P16 `Move-Item -Force`, P17 `[IO.File]::WriteAllBytes`;
    - Python: P19 `open('wb')`, P20 `open('r+b')`, P21 `open('ab')`, P22 `shutil.copyfile`, P23
      `shutil.copy2`, P24 `os.replace`, P25 `Path.write_bytes`, P26 `os.truncate`;
    - cmd: P27 `copy /y`, P28 `echo x> f`;
    - Node: P33 `fs.writeFileSync` and `fs.renameSync` over the file (xelib-style scripts).
    - P29–P32 are agent and xelib rows (Tasks 2, 3).
  - **Delete (recorded, never decisive):** D1 `rm -f`, D2 `Remove-Item -Force`, D3 `Remove-Item`, D4
    `os.remove`, D5 `del /f`, D7 rename away (`mv f f.bak`).
  - **Metadata:** T1 `os.utime` (mod managers and tools that set timestamps), T2 attribute read.
  - **Reads:** R1 python read, R2 `Get-Content -AsByteStream`/`-Encoding Byte`, R3 a 1 MiB
    memory-mapped read (`mmap`, the way xEdit and the engine read).
  - **WSL rows:** UNMEASURED (L12). Do not install a distro. **Never `wsl --unregister`.**
- [ ] **Step 3:** Each mechanism × row cell gets `BLOCKED` / `NOT BLOCKED` / `UNMEASURED (control
  failed)`, with counts per class and per mechanism. The L3 rows (`rm -f`, `-Force`) are compared with
  the X4 numbers, and each disagreement is written out.
- [ ] **Step 4:** Commit: `"measurements: M-L2a primitive matrix (4 mechanisms, controls)"`.

### Task 2: M-L2b — the agents' own tools and lock/unlock from inside a session (Phase A)

**Files:** `scripts/layer2-spike/agent_probes.{sh,md}`; results in the ml2 doc.

- [ ] **Step 1 — Claude:** in `C:/Temp/skyguard-ml2/agent-claude/` (a project with **no** toolkit hooks,
  so the raw tool is what meets the lock), `claude -p --permission-mode bypassPermissions`, once per
  mechanism:
  - P30 `Write` and P31 `Edit` on the locked text decoy and on its control;
  - the Bash tool and the PowerShell tool each run `lockprobe.py apply` then `remove` (C8): no
    elevation prompt, and state verified by re-read.
- [ ] **Step 2 — Codex:** in `C:/Temp/skyguard-ml2/agent-codex/`, `codex exec`, default sandbox:
  - P29 `apply_patch` Update File and D6 Delete File on the locked text decoy and its control;
  - Add File of a new name next to it (must work);
  - C8 lock/unlock through pwsh.
  
  **If the control also fails, the sandbox refused, not the lock:** record UNMEASURED, and rerun once
  with the sandbox set so the workspace is writable (record the setting). Record every folder Codex
  marks trusted in `~/.codex/config.toml` (**read only, never edited**) for batch cleanup B7.
- [ ] **Step 3:** Commit: `"measurements: M-L2b agent tools vs the lock; lock/unlock inside Claude and Codex"`.

### Task 3: M-L2c — tool compatibility, unlock, and timing (Phase A)

**Files:** `scripts/layer2-spike/{xelib_probe.js,vortex_ops.py,mo2_probe.md,unlock_probe.sh}`; results.

- [ ] **Step 1 — xelib (C1–C4, P32):**
  - `SetGamePath(C:/Temp/skyguard-ml2/game/)`, `$PLUGINS_TXT` → the bed's `plugins.txt`, run as a
    `.js` file (gotcha #11); success is judged from stdout, never `$?` (gotcha #12);
  - C1: load the full vanilla order with every target locked;
  - C2: create/modify and `SaveFile` the bed's `Spike.esp` with the masters locked, then cross-read it
    with Spriggit serialize (an independent parser);
  - C3 / P32: modify and `SaveFile` `Update.esm` while locked → must **fail**; `Update.esm` stays
    byte-identical; record any `.save` debris;
  - C4: unlock (one command) → the same save succeeds → relock → `status` locked;
  - one run per eligible mechanism.
- [ ] **Step 2 — xEdit exe (C7):**
  - copy `xTESEdit64.exe` into `C:/Temp/skyguard-ml2/tools/xedit/`, so files written next to the exe
    stay in scratch;
  - run QuickAutoClean of `Update.esm` headless:
    `xTESEdit64.exe -tes5vr -D:"C:/Temp/skyguard-ml2/game/Data" -qac -autoexit -autoload Update.esm`;
  - record `%LOCALAPPDATA%` files before and after;
  - expected: refused while locked, cleaned after unlock;
  - **timebox 30 min**; if it needs a click, C7 goes to batch item B5 with these exact steps.
- [ ] **Step 3 — Vortex (C5, R10):**
  - READ the Vortex bundle (`resources/app.asar`, read-only `strings`/asar list) for the deploy
    method, `.vortex_backup` handling and purge order, citing what was found (tier READ);
  - `vortex_ops.py` replays, with masters locked:
    - (a) hardlink deploy of a staging file into `Data/` (new name);
    - (b) purge (unlink the link);
    - (c) a game-file collision: rename the locked vanilla `Skyrim - Shaders.bsa` →
      `….vortex_backup`, link the mod's copy in, then purge restores it (rename back);
    - (d) symlink deploy if `os.symlink` is permitted (Developer Mode), else UNMEASURED;
  - then `status` after each step: a file that became multi-link is reported, not relocked.
- [ ] **Step 4 — MO2 (C6, R15):**
  - fetch MO2 portable;
  - create a portable instance by files (`portable.txt`, `ModOrganizer.ini` with the scratch game path);
  - read `ModOrganizer.exe --help` for the run syntax;
  - launch Python under the VFS: read a locked master (must work), write `overwrite/Out.esp` (must
    work), write the locked master (record);
  - **timebox 45 min**; on failure the C6 rows go to batch B5 with exact steps, and the mechanism
    decision proceeds with C6 = "pending batch". Table A then treats it as **not failed**, and the R3
    flip also requires B5.
- [ ] **Step 5 — unlock (U1–U5) and timing (C9, C10):**
  - U1: `tools/skyguard lock unlock` equivalent (spike: `lockprobe.py remove`), state verified;
  - U2: Python absent (PATH stripped) → a `cmd` script using `attrib`/`icacls` only;
  - U3: manifest deleted → `--scan` removes only the toolkit's own marks (R5: a pre-existing
    read-only decoy stays read-only);
  - U4: 0 residual deny ACEs (`Get-Acl`);
  - U5: run as the same **unelevated** user (`whoami /groups` shows no Administrators token in use);
  - C10: apply + remove time over the full VR set, 5 runs each.
- [ ] **Step 6:** Commit: `"measurements: M-L2c xelib/xEdit/Vortex/MO2 compatibility, unlock, timing"`.

### Task 4: M2 — forging the state file, and which channels the user sees (Phase A, automated part)

**Files:** `scripts/layer2-spike/m2_probes.{py,md}`; `docs/superpowers/measurements/2026-10-XX-plan6-m2.md`
(Tables M and V copied in before the results).

- [ ] **Step 1 — routes F1–F8:** use a scratch toolkit install (the Plan 4 E2E recipe), with the hooks
  **as they are on master** (no self-protection yet, so this is the baseline). Probe:
  - F1 Claude `Write` to `.skyguard/state.json`;
  - F2 Bash `echo > …`;
  - F3 PowerShell `Set-Content`;
  - F4 `python -c "open(...)"` via Bash (opaque);
  - F5 Codex `apply_patch`, hooks approved — **not runnable by `codex exec` alone if approval is
    TUI-only (Plan 4 measured trust); in that case F5 → batch B3**;
  - F6 Codex `apply_patch`, hooks unapproved;
  - F7 Codex pwsh `Set-Content`;
  - F8 an in-session `SKYGUARD_GUARDS=off` (re-measures E20 for this toolkit).
  
  Record for each: written / blocked / hook-visible.
- [ ] **Step 2 — contents S1–S8:** this tests Task 7's reader, so it is written now as **failing
  tests** in `tests/test_skyguard_state.py`, marked `xfail(strict=True)` until Task 7:
  - S1: hand-edited, wrong checksum → guards on + log;
  - S2: recomputed checksum, same session → honoured, with SHADOW per call;
  - S3: another `session_id` → ignored;
  - S4: past `expires_at` → ignored;
  - S5: garbage, a directory, a symlink or junction, > 64 KiB, or an unknown `v` → guards on, no
    exception escapes;
  - S6: deleted → guards on;
  - S7: `expires_at` beyond 8 h → clamped;
  - S8: a lift dated before the session's start record → ignored.
- [ ] **Step 3 — Claude visible channel (automated half of Table V):** a scratch hook returning
  `systemMessage`, run under `claude -p --output-format stream-json`. Record whether the message
  appears in a user-facing event, as opposed to model context. The interactive confirmation is batch
  B4.
- [ ] **Step 4:** Apply Table M; record the outcome. Commit: `"measurements: M2 state-file forgery routes and channels"`.

### Task 5: The decision, spec amendments, and the real-install integrity re-check (Phase A close)

**Files:** the ml2 and m2 docs; the spec (`§2` E28+, `§5.2`, `§5.3`, `§9`); this plan's "Decision
record" (appended).

- [ ] **Step 1:** `baseline.py verify`: every real file's sha256, attributes, `st_ino`, `st_nlink` and
  ACL text are unchanged. **Any difference: stop, report, and restore nothing by hand. The user
  decides.**
- [ ] **Step 2:** Apply Table A row by row and write the verdict per mechanism. Record the choice, or
  "no lock".
- [ ] **Step 3:** Spec amendments:
  - E-rows for the measured matrix;
  - §5.3 mechanism = the choice;
  - the R13 correction ("a refused write cannot name the command");
  - §5.2 the R6 session binding;
  - §9 M-L2 (e) moved to the batch, with Table B.
- [ ] **Step 4:** Delete `C:/Temp/skyguard-ml2/game/` locks (`lockprobe.py remove` over everything)
  and verify that no file in the bed is read-only or has a deny ACE. Keep the bed for Task 18.
- [ ] Commit: `"Plan 6 Task 5: M-L2 decision (<mechanism|no lock>), spec amended"`.

---

### Task 6: Phase B preconditions and baseline

- [ ] Check every Phase B precondition. Create worktree `../skyrimvr-toolkit-plan6` and branch `plan6`.
- [ ] If Plan 5 Task 16 has not produced its numbers, take them now, read-only, with the same method:
  - `.psc`/`.pex` counts top-level vs nested, under **both** `Data/Scripts/Source` and
    `Data/Source/Scripts`, with bytes and the share older than 7 days;
  - the current per-call snapshot time;
  - the backup count, and how many backups have no recoverable original path (L8).
- [ ] Baseline pricing (`price.py --base <master sha>` → zero diff against itself) and baseline latency.
  Record both in `docs/superpowers/measurements/2026-10-XX-plan6-pricing.md`.
- [ ] Commit: `"Plan 6 Task 6: base, side-effect inputs, baselines"`.

### Task 7: `state.py` — lifts that are session-bound, checksummed and expiring

**Files:** Create `tools/skyguard/state.py`; flip the Task 4 xfails to plain tests.

- [ ] **Step 1 — the format:** `.skyguard/state.json` holds
  `{v:1, lifts:[{kind:"guards"|"lock", files?, reason, session_id, agent, created, expires_at}],
  persistent:{lock_disabled?:{since, by_session}}, sum}`. `sum` is the sha256 of the canonical JSON
  without `sum`.
  - Writes are atomic (temp in the same dir + `os.replace`), and only through `state.write()`.
  - Reads: ≤ 64 KiB, regular file only (`lstat`, no reparse point), version known, sum matches. On
    any failure the reader returns `State.SAFE` (no lifts, not disabled) **plus a reason**, and the
    caller logs `STATE-REJECTED <reason>`.
- [ ] **Step 2 — honoured lifts:** `honoured(state, session_id, now)` returns only the lifts whose
  `session_id` equals the caller's, with `created ≥ session_start(session_id)` and
  `now < min(expires_at, created + 8h)`. The session start comes from Plan 4's `health.session_record`.
- [ ] **Step 3:** The S1–S8 tests pass. Mutations, one per clause of `honoured` and of the reader
  (`l2-state-sum`, `l2-state-session`, `l2-state-expiry`, `l2-state-clamp`, `l2-state-size`,
  `l2-state-reparse`, `l2-state-version`), each killed by a named test.
- [ ] Commit: `"skyguard: lift state -- session-bound, checksummed, expiring (M2, R6)"`.

### Task 8: `lock.py` + the `tools/skyguard` CLI + the Python-free unlock

*(If Task 5 decided "no lock": build only `status`, which prints `not available: M-L2 failed (<row>)`,
and skip to Task 9.)*

**Files:** Create `tools/skyguard/lock.py`, `tools/skyguard/cli.py` (dispatch for `lock`, `guards` and
`doctor`), `tools/skyguard-unlock.cmd`, `tests/test_skyguard_lock.py`; extend the `tools/skyguard`
entry wrapper (bash) with a `.cmd` twin.

- [ ] **Step 1 — targets (R1):** `targets(data_dir, edition)`:
  - the edition comes from the exe present (`SkyrimVR.exe` / `SkyrimSE.exe`) and `Skyrim.ccc` for AE;
  - every candidate is `lstat`ed: single-link, regular, not a reparse point;
  - the result names each **skip and why** (`multi-link (mod manager copy)`, `missing`,
    `reparse point`).
  
  Tests: an L2-style directory with lookalike names → none of them locked. Mutation
  `l2-targets-glob` swaps in a glob, and the lookalike test must go red.
- [ ] **Step 2 — `lock` / `unlock [file…]` / `status [--json]` / `disable` / `enable`:**
  - lock records the prior state (R5) in `.skyguard/lock.json` and `.skyguard/locked.txt` (plain
    paths, for the `.cmd`);
  - every action re-reads the state and exits per the X4 contract: 0 verified, 1 partial or
    disagreeing, 2 refused/unreadable, 3 unsupported (R11, POSIX);
  - `unlock` with no file means everything; `unlock <file>` is the choke point;
  - each lift goes through `state.write()` (kind `lock`, this session's id, from the hook's
    environment or `--session`);
  - `disable` records `persistent.lock_disabled`;
  - every mutating call is sandbox-asserted when `SKYGUARD_LOCK_SANDBOX` is set; the suite sets it
    always.
- [ ] **Step 3 — Python-free unlock (U2):**
  - `tools/skyguard-unlock.cmd`: a pure `cmd` script that reads `.skyguard/locked.txt` and runs
    `attrib -R` (and `icacls /remove:d *<SID>` if the mechanism has an ACE) for each line;
  - it prints what it did, and runs when double-clicked;
  - the README carries the two raw commands as a last resort;
  - test: run it with `PATH` stripped of Python.
- [ ] **Step 4:** Twins for each U-row and each exit code (`l2-lock-verify-reread`,
  `l2-lock-preexisting-ro`, `l2-lock-single-link`, `l2-lock-sandbox`).
- [ ] Commit per step.

### Task 9: `guards off|on` — shadow mode in the engine (R8)

**Files:** `tools/skyguard/engine.py` (the single decision exit), `tools/skyguard/cli.py`,
`tests/test_skyguard_shadow.py`, corpus rows.

- [ ] **Step 1 — corpus rows first:**
  - every current deny/ask row, re-run with an honoured guards lift, expects `allow` with context
    `Guards OFF: this would have been denied ({rule}).` + the rule's own first line;
  - `GUARD INERT` rows still deny;
  - backup/snapshot side effects still run (a fixture asserts the backup file exists).
- [ ] **Step 2:** Implement at the **one** place a verdict leaves the engine, after combine. Write an
  audit `SHADOW <rule>` line per converted verdict.
- [ ] **Step 3:** Mutations: `l2-shadow-inert-still-denies`, `l2-shadow-logs`,
  `l2-shadow-side-effects-run`, `l2-shadow-session-bound`.
- [ ] Commit: `"skyguard: guards off = shadow mode; INERT and side effects unaffected"`.

### Task 10: Session start — expiry, drift, banners

**Files:** `tools/skyguard/session.py`, the Claude and Codex session roles (Plan 4), tests.

- [ ] **Step 1 — order (Review Focus 4):**
  1. record the session start (Plan 4);
  2. clear every lift and relock the derived target set unless `lock_disabled` (R4);
  3. compute the banners;
  4. run kb-guard and inject preferences (existing).
- [ ] **Step 2 — banners (≤ 2 lines each):**
  - `Guards were left OFF by an earlier session ({reason}); they are back ON.`
  - `{n} vanilla files were left unlocked; relocked.`
  - `{n} vanilla files were unlocked outside skyguard ({file}…); relocked.` (drift: unlocked with no
    lift record)
  - `Vanilla-file lock DISABLED since {date}. tools/skyguard lock enable turns it back on.` (every
    session, R7)
  - `{n} guard files differ from release {ver} ({file}…).` (R12)
  - `STATE-REJECTED: the guard state file was unreadable or altered; treated as guards on.`
- [ ] **Step 3 — channel:** banners go to model context on both agents, and to `systemMessage` on
  Claude if Task 4 Step 3 showed it user-facing. Codex follows Table V once B3 is in; until then,
  model context only.
- [ ] **Step 4:** Tests and twins: relock before report; drift detected; disabled banner every session;
  `compact` does not clear (R9).
- [ ] Commit.

### Task 11: Lift rules — the advisory, the raw-unlock deny, and the permanent-disable ask (D6, R7)

**Files:** `tools/skyguard/rules.py` (registry), `shell_rules.py`, `codex_rules.CLASSIFICATION`,
`intended.jsonl`, mutations.

Rules:

| Rule | Fires when (executed program + resolved operand) | Decision | Message |
|---|---|---|---|
| S-LIFT-NOTE | `tools/skyguard` executed with `lock unlock` or `guards off` | advise | `Note: this lifts {op} until the next session starts. Tell the user why, and restore it when done.` |
| S-LIFT-RAW | `attrib -R`, `Set-ItemProperty … IsReadOnly`, `.IsReadOnly = $false`, `[IO.File]::SetAttributes`, `icacls … /remove`/`/grant`/`/reset` with an operand resolving to a **current lock target** | deny | `Blocked: this unlocks {file} outside skyguard, so the lift is not logged and never expires.` / `Use: tools/skyguard lock unlock {file}` |
| S-LIFT-PERMANENT | `tools/skyguard lock disable` executed | ask (deny on Codex, via Plan 4 R4) | `This turns the vanilla-file lock off for every future session, not only this one.` / `Yes disables it until tools/skyguard lock enable.` |

- [ ] Corpus rows first (including a `grep "attrib -R"` read → allow, the E16 class). Price (expected
  ≈ 0 hits; read each). Implement. One twin per clause. Classify the new constants for Codex `.rules`
  (`hook_only`; a `forbidden` prefix only if it is a strict subset, per Plan 4 R5).
- [ ] Commit.

### Task 12: Agent self-protection (deferred item; R12)

**Files:** `rules.py`, `write_rules.py`, `shell_rules.py`, the Codex patch-path route (Plan 4
`patch_paths`), `scripts/gen-guard-hashes.py` → `GUARD-FILES.sha256` (generated, drift-gated in CI),
corpus, mutations.

Rules:

| Rule | Fires when | Decision |
|---|---|---|
| S-SELF-STATE / W-SELF-STATE | a write primitive, `Edit`/`Write`, or an `apply_patch` Add/Update/Delete/Move resolving into `.skyguard/**`, unless the executed program is `tools/skyguard` | deny: `Blocked: {file} holds the guards' on/off state and is written only by tools/skyguard.` / `Use tools/skyguard guards off, or lock unlock.` |
| S-SELF-GUARD / W-SELF-GUARD | the same routes into the guard code or config: `.claude/hooks/**`, `.claude/settings*.json`, `.codex/**`, `tools/skyguard/**`, `agent/guards/**`, `~/.claude/settings*.json`, `~/.codex/config.toml` | **priced**: ask (`This edits {file}, part of the safety guards themselves.` / `Yes applies it; the next session start lists guard files that differ from the release.`) or advise (the first line, prefixed `Note:`) |

- [ ] **Step 1:** Corpus rows, both directions:
  - each route × each protected class;
  - reads of those files → allow;
  - `tools/skyguard guards off` → S-LIFT-NOTE, **not** S-SELF-STATE;
  - a write to `.skyguard-notes.txt` (a lookalike) → allow.
- [ ] **Step 2 — price (pre-committed):** S-SELF-STATE ships as deny if newly denied ≤ 20 over the
  full history, with every one read. For S-SELF-GUARD:
  - **ask** if the hand-read sample shows ≤ 25 % user-requested maintenance **and** ≤ 50 newly asked
    in total;
  - otherwise **advise**;
  - the session-start hash banner ships either way.
  
  Record the numbers and the choice as a `Ruling:` in the pricing doc.
- [ ] **Step 3:** Implement. Twins per clause, including `l2-self-applypatch-move` (the `*** Move to:`
  path dropped) and `l2-self-userlevel` (the `~` expansion dropped).
- [ ] Commit per rule.

### Task 13: Backup manifests with checksums and atomic destinations (deferred Codex-audit item)

**Files:** `tools/skyguard/backup.py`, `scripts/restore-from-backup.sh`, `tests/test_backup_manifest.py`.

- [ ] **Step 1 — RED:**
  - two backups of one file in the same second → today the second overwrites the first (L7);
  - a copy that fails midway → today a partial file sits under the final name;
  - restoring `Skyrim_VR - Main.bsa` → today the wrong path (L8).
- [ ] **Step 2 — backup writes:**
  - the source goes to `<dest>.part-<pid>` with a running sha256, then `fsync`, then the final name is
    created exclusively (`O_EXCL`; `-1`, `-2` … suffix on collision) via `os.link` + unlink, or
    `os.replace` onto a name proven absent;
  - then one JSON line is appended to `backups/MANIFEST.jsonl`:
    `{v:1, ts, src (verbatim), dest, size, sha256, tool, agent, session_id}`;
  - re-read check: the dest's size equals the source's, otherwise `FAILED` is logged and the part is
    removed;
  - the manifest append is one `write` of one line (atomic for small appends on NTFS; INFERRED, so a
    reader skips any line that fails to parse and counts it).
- [ ] **Step 3 — restore:** `restore-from-backup.sh` reads the manifest. It lists `src` verbatim, checks
  the backup's sha256 before restoring, and falls back to the legacy name parsing **only** for
  pre-manifest backups, labelled `(legacy: path guessed)`.
- [ ] **Step 4:** Latency: price the backup role's added p95 (sha256 of the edited file). If it is
  > 50 ms on the Task 6 file-size distribution, hash in the same read pass (already the design) and
  report.
- [ ] Twins: `l2-backup-excl`, `l2-backup-part`, `l2-backup-manifest-sha`, `l2-restore-manifest-path`.
  Commit per step.

### Task 14: Nested PSC/PEX snapshots without the 7-day exclusion (deferred; R16 of Plan 5)

**Files:** `tools/skyguard/snapshot.py`, `tests/test_snapshot_nested.py`.

- [ ] **Step 1 — RED:**
  - a `.pex` in `Data/Scripts/sub/` → today not snapshotted;
  - a `.psc` aged 8 days → today not snapshotted;
  - `Data/Source/Scripts/x.psc` → today not snapshotted.
- [ ] **Step 2 — design:** a content-addressed store `auto_snapshots/blobs/<sha256>` plus one manifest
  per snapshot (`path → sha256`).
  - Walk both source layouts and `Data/Scripts/**` recursively with `os.scandir` (Windows `DirEntry`
    stat is free).
  - Keep a `(path, size, mtime_ns)` index; hash and copy only files whose entry changed.
  - A first-ever baseline runs at SessionStart, time-boxed to 20 s. If it is unfinished, it resumes on
    the next call and the snapshot is marked `partial` in the log.
  - Pruning keeps every blob referenced by the newest snapshot and by any snapshot < 14 days old, and
    never removes the only copy of a path's latest content.
- [ ] **Step 3 — pre-committed latency gate:** measured on a synthetic tree with the Task 6 counts.
  - Added per-call p95 ≤ 150 ms → ship with the 60 s rate limit.
  - 150–400 ms → rate limit 300 s, and the walk is skipped when `Data/Scripts` directory mtimes are
    unchanged **and** the last full walk is < 300 s old (stated as a known gap: an in-place overwrite
    within that window is caught by the next walk).
  - > 400 ms → per-call walks are disabled; snapshots happen at SessionStart and before
    Caprica/Champollion/AutoMod `papyrus` executions only (an executed-program anchor, Plan 5 style),
    and this is recorded as a ruling.
- [ ] Twins per clause. Commit.

### Task 15: Recovery snapshots for load order, INIs and the target save (spec §5.3)

**Files:** `tools/skyguard/snapshot.py` (or `recovery.py`), tests.

- [ ] Before a non-read-only shell command, rate-limited and content-hash-deduplicated, snapshot
  `plugins.txt`, `loadorder.txt` and `Skyrim*.ini`, from the configured roots.
- [ ] Before S-RESAVER-APPLY and S-DEVBENCH-SAVE executions (Plan 5 rules), snapshot **the named save
  only**, plus its `.skse` co-save.
- [ ] Never the whole save folder. Manifest format as in Task 13. Restore lists them.
- [ ] Twins. Commit.

### Task 16: `skydoctor`

**Files:** `tools/skyguard/doctor.py`, `tools/skydoctor` (bash + `.cmd`), `tests/test_skydoctor.py`.

- [ ] **Step 1:** One report, every line `live | not live | unknown` with a reason, never blank:
  - **Layer 0:** the CLIs resolve (`toolchain-check` reused).
  - **Layer 1:** the instruction files present, and `AGENTS.md` ≤ 32,768 B.
  - **Layer 2:** per target, `locked` / `unlocked (lift until …)` / `unlocked (outside skyguard)` /
    `skipped: multi-link` / `missing`; the mechanism; `disabled since …`; the honest gap list (the
    rows Table A recorded NOT BLOCKED).
  - **Layer 3, per agent:** Claude wiring + Plan 4 `health.hook_state`; Codex `codex_trust` +
    `.rules` present + coverage.
  - **Guards:** on/off, who, why, expiry; `STATE-REJECTED` if so.
  - **Integrity:** guard-file hash differences.
  - It **writes nothing**, except that it clears expired lifts (R9) and logs that it did.
- [ ] **Step 2:** Exit 0 all live; 1 anything not live; 2 anything unknown. A twin per state.
- [ ] Commit.

### Task 17: Documentation and instructions

- [ ] README: the Layer 2 section has:
  - the mechanism;
  - the **measured** table of what it stops and what it does not (from Table A, with its
    denominators);
  - unlock in one command (`tools/skyguard lock unlock`, `tools\skyguard-unlock.cmd`), and the raw
    commands as a last resort;
  - the Table B outcome or "pending";
  - R9's gap for agents with no hooks;
  - R11 POSIX;
  - Table V wording per agent.
- [ ] Core instructions (agent-neutral; respect the 32,768 B gate and the neutrality gate): the R13
  sentence, the lift etiquette (say why, restore when done), and "never edit `.skyguard/`".
- [ ] `docs/guard-coverage.md`: the "processes outside the agent" row → Layer 2, with its gaps.
- [ ] CHANGELOG `Unreleased`: Layer 2 (or "recovery only"), shadow mode, `skydoctor`, backup
  manifests (restore path fix, L8), nested snapshots.
- [ ] Commit.

### Task 18: Mutation pass, cumulative pricing, live E2E, latency (automated release gate for Plan 6)

- [ ] **Step 1:** Mutation gate: every `l2-*` and `rule-*` entry is killed by its **named** test.
- [ ] **Step 2:** `price.py --base <Task 6 base>` cumulative; the per-rule table; every newly denied or
  asked item read.
- [ ] **Step 3:** Live E2E with `claude -p` (Git Bash and PowerShell tools) and `codex exec`, in a
  **scratch toolkit install** with the scratch bed as its game. Heartbeats must prove the copy's hooks
  answered. Each row:
  1. `setup.sh --lock` (the opt-in while R3 holds) → `skydoctor` shows locked;
  2. with hooks **unapproved** on Codex: a P9-style write to a locked master → refused by the OS
     (the Layer 2 backstop), with the control writable;
  3. `tools/skyguard lock unlock Update.esm` → advisory visible; write succeeds; restart → relocked +
     banner;
  4. `guards off` → a decoy `.esp` `Set-Content` is allowed with SHADOW context + a log line; restart →
     guards on + banner;
  5. a forged state (S1, S3) → guards on, `STATE-REJECTED`;
  6. an S-SELF-STATE write → denied;
  7. a raw `attrib -R` on a target → denied;
  8. a backup taken with a manifest line; a nested `.pex` snapshotted;
  9. `tools\skyguard-unlock.cmd` with Python off PATH → unlocked.
- [ ] **Step 4:** Latency rerun (p95 ≤ 1 s; report the delta against Plan 5 and the Codex headroom).
- [ ] **Step 5:** `baseline.py verify` again (the real install untouched).
- [ ] Commit the E2E measurements.

### Task 19: The Final Manual Batch hand-off, final review, merge

- [ ] **Step 1:** Write `docs/superpowers/measurements/2026-10-XX-plan6-batch-instructions.md`
  (copy-paste steps, in the Plan 4 TUI-instructions format) and
  `scripts/layer2-spike/collect_batch.py`. The collector reads the results itself: hashes,
  attributes, ACLs, logs, transcripts. It never asks the user to report a number.
- [ ] **Step 2:** Append items B1–B7 below to the roadmap's FINAL MANUAL BATCH block (item 2 says later
  plans append there). B3 is merged **into** Plan 4's TUI session (item 1), so the user opens Codex
  once.
- [ ] **Step 3:** Final whole-range review on the most capable model, using this plan's Review Focus;
  then the fix pass, the full suite through the harness, and the merge `plan6` → master. **No push.**
- [ ] **Step 4 (after the batch, by Claude):** run the collector, apply Tables B and V, commit the
  results, and, only if Table B says "on", make the one-line R3 flip as its own commit
  (`"Layer 2: lock on by default (batch B1/B2 passed)"`), with the README updated to match.

---

## Final Manual Batch items this plan adds (appended in Task 19; never asked mid-plan)

All of them run against scratch: the bed `C:/Temp/skyguard-ml2/`, a scratch toolkit install, and a
stand-in Steam app. **None of them touches `C:/GOG Games`**, except the explicitly optional B6.

- **B1 — Wrye Bash** (~10 min). Steps:
  1. With the bed locked by `tools/skyguard lock` (the scratch install), launch
     `C:/Temp/skyguard-ml2/tools/wryebash/Wrye Bash.exe -o "C:/Temp/skyguard-ml2/game"`.
  2. Let it finish its first scan.
  3. Mods tab: right-click the Bashed Patch → Rebuild, and accept the defaults.
  4. Right-click the bed's inactive `Ghost-Test.esp` → toggle Ghost, then unghost.
  5. Close it.

  The collector reads Wrye Bash's log, the BP's existence and hash, the ghost rename trail, and that
  every master is still locked and byte-identical.
- **B2 — Steam verify on a stand-in** (~10 min plus the download). Steps:
  1. Install the smallest free Steam app (a suggestion is given at Task 19 time, measured < 1 GB) in
     the default library.
  2. Run `tools/skyguard lock --sandbox-target "<app dir>" --files <3 named files>` (test mode, scoped
     to that folder only).
  3. Run `collect_batch.py corrupt-one`, which unlocks, flips one byte and relocks.
  4. In Steam: Properties → Installed Files → Verify integrity.
  5. Run `collect_batch.py steam`.
  6. Run `tools/skyguard lock unlock --sandbox-target "<app dir>"`.
  7. Uninstall the app if wanted.
- **B3 — Codex TUI** (appended to Plan 4 batch item 1, same session, +5 min):
  1. Approve the hooks.
  2. Ask Codex to run `tools/skyguard guards off "batch test"`, then a decoy `.esp` write.
  3. Note whether any lift text appears **outside** the model's reply.
  4. Quit, reopen, and look for the session-start banner.
  5. F5: ask Codex to edit `.skyguard/state.json` with `apply_patch`.

  The collector reads the transcript and the logs.
- **B4 — Claude TUI** (~3 min). Same as B3 in an interactive `claude` in the scratch install: does
  `systemMessage` show to you on the lift and at session start?
- **B5 — only if Task 3 could not automate them:** the xEdit GUI QuickAutoClean (C7) and/or the MO2 GUI
  launch (C6), with the exact steps Task 3 recorded at its timebox.
- **B6 — OPTIONAL, your call, your real install:**
  1. `tools/skyguard lock` on the real install.
  2. Launch Skyrim VR once to the main menu.
  3. Quit.
  4. `tools/skyguard lock unlock`.

  If skipped, the README keeps "the game reading locked masters: INFERRED (it opens them read-only)".
- **B7 — cleanup:**
  1. Remove the scratch folders that `codex exec`/the TUI marked trusted from `~/.codex/config.toml`
     (Task 2/4 print the exact entries; we never edit that file).
  2. Delete `C:/Temp/skyguard-ml2/`, including the downloaded Wrye Bash and MO2, **after** the
     collector prints `no lock remains on any file`.
  3. Uninstall the B2 stand-in.

---

## Risks (ranked)

1. **The scratch bed is not isolated from the real install.** An attribute or ACE belongs to the
   *file object*. A hardlinked "copy", a junction or a mis-resolved path would lock or ACL the user's
   real masters. A bad mask (`W`, `WDAC`) can make a file unreadable or unremovable without elevation
   (L4, measured by X4). → real copies with `st_ino` and `st_nlink` checks; `SandboxViolation` on
   resolved paths with a junction twin; mask whitelist; real-install baseline verified in Tasks 5
   and 18.
2. **The D5 ship conditions are measured after the code exists.** Wrye Bash and Steam verify need the
   user, so they land in the batch. Momentum could ship the lock default-on unmeasured. → R3: default
   off until Table B, with a separate flip commit. Residual: a stand-in app may not behave like Skyrim
   under Steam verify, and the real-install launch (B6) is optional.
3. **Same-user forgery cannot be prevented, only made loud and short-lived.** R6 relies on per-call
   SHADOW visibility and on SessionStart. On Codex the user-visible channel is unmeasured (L16, Table
   V). On an agent with no hooks nothing expires a lift until `skydoctor` runs (R9). → Table M's
   fallback (launch-env guards off); the README claims only what Table V allows.
4. **Latency.** Nested snapshots and backup hashing add work on every call, and Codex has ~0.25–0.35 s
   of p95 headroom (Plan 4 Risk 2). → Task 14's pre-committed tiers; latency rerun in Task 18.
5. **Self-protection versus maintenance.** The author's history is full of user-requested hook edits.
   → R12 pricing with a hand-read share; tamper-evidence by banner regardless.

## Spec coverage of Plan 6

| Item | Task |
|---|---|
| M-L2 (a) lock/unlock in Claude **and** Codex, no admin | 2 (C8), 18 |
| M-L2 (b) xEdit loads, clean-after-unlock, relock | 3 (C1–C4, C7), B5 |
| M-L2 (c) Wrye Bash scan, Bashed Patch, ghosting | B1 + Table B |
| M-L2 (d) `rm -f`, `Remove-Item -Force`, xEdit save stopped | 1 (D1, D2 recorded), 3 (C3) |
| M-L2 (e) Steam verify | B2 + Table B |
| Vortex hardlinks/symlinks, MO2 VFS | 3 (C5, C6), B5 |
| Agent tools: bash, PowerShell, Python, `apply_patch`, Claude Write/Edit | 1, 2 |
| D5 "no lock" path | Table A, Task 5, Task 8 fallback |
| M2 state-file forgery | 4, 7, Table M |
| D6 lift with advisory, expiry, auditable | 7, 8, 9, 10, 11 |
| `guards off` shadow mode | 9 |
| Lift expiry at session start | 10 |
| `skydoctor` | 16 |
| Unlock in one command, measured | 3 (U1–U5), 8, 18 |
| Backup manifests + checksums + atomic destinations | 13 |
| Nested PSC/PEX snapshots without the 7-day exclusion | 14 |
| Recovery snapshots (load order, INIs, saves) | 15 |
| Agent self-protection | 12 (+ banner in 10) |
| Every user-run step in the final batch | 19, B1–B7 |

Deferred by design: OpenCode (Plan 10), `setup.sh --agent` and the paths-env move (Plan 8), POSIX lock
(R11), and adopting v4 in the author's install (a separate decision, spec non-goal).
