# Plan 5 — rule corrections, approval-fatigue cuts, cwd resolution

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `skyguard` rules decide on **what a command actually executes and where its
operands actually resolve**, not on which words appear anywhere in its text. In practice:

1. port the install's ReSaver fix (`4a429bd`) first;
2. close the real gaps: PowerShell forms, MO2 roots, DevBench, shell writes to plugins (E21), relative
   paths resolved from the payload's `cwd`, and the PowerShell tool left unguarded (E12);
3. cut the noise. Every ask and advise fires only on the program actually run, in at most 2 plain
   lines, and never echoes the command. The user, 2026-10-02: *"All this serves to do is give me
   approval fatigue."*

**Every behaviour change is priced against this machine's real tool-call history before it lands.**

**Architecture:** There are three new pure modules:

- `tools/skyguard/shellparse.py` turns a command string into the commands it executes, for bash,
  PowerShell and `cmd`.
- `tools/skyguard/targets.py` resolves each operand against the payload `cwd` and classifies it into a
  protection zone.
- `tools/skyguard/rules.py` is the registry of rule ids, decisions and message templates.

`shell_rules.py` and `write_rules.py` become thin rule tables over those modules. The corpus gains a
hand-authored `intended.jsonl` beside the frozen bash-recorded `expected.jsonl`. A new pricing tool
replays real history through the **old and the new engine in-process** and reports newly-denied,
newly-asked and newly-advised items (and the no-longer ones) **per rule**.

**Tech Stack:** Python ≥ 3.8 syntax, stdlib only (engine floor pinned by `test_engine_parses_on_python_3_8`);
bash; pytest 8; Node only for the JS↔Python precedence parity test.

**Spec:** `docs/superpowers/specs/2026-09-30-universal-agent-support-design.md` §5 (new rules), §7.1
(corpus), §10 phase 6; evidence E3, E12, E13, E16, E21. Roadmap row 5:
`C:\Users\Moona\.claude\plans\looks-good-lovely-fern.md`. The inputs are:

- `docs/superpowers/measurements/2026-10-02-ask-and-analysis-failure-audit.md` (the "audit")
- `docs/superpowers/measurements/2026-10-02-plan2-equivalence.md` (the "equivalence replay")
- Codex handoff `docs/superpowers/handoffs/codex-harness-repairs.md`, on branch
  `codex/harness-repairs-fd71271`, which must be merged before Task 0 (see the preconditions)
- House style: Plan 2 (`docs/superpowers/plans/2026-10-01-plan2-skyguard-engine.md`)

---

## Facts this plan rests on (re-measure before relying on any of them)

| # | Fact | Tier | Source |
|---|---|---|---|
| P1 | The toolkit has ONE ask rule (`ASK_RESAVER`, `shell_rules.py:166-170`). The replay saw 3 asks in 49,343 calls, **all from embedded text, 0 with ReSaver as the executed program**. | MEASURED | audit |
| P2 | Advise hits over the 49,343 replayed calls: ADV_PLUGIN 1,627 (3.3%), ADV_EXTERNAL 220, ADV_LOADORDER 160, ADV_MOVE 132, PAPYRUS 72, ADV_REDIRECT 21, the others ≤ 11. **ADV_DELETE: 0.** | MEASURED | audit |
| P3 | DENY_INSTALL would fire 371 times on replay. 14 of the 29 real denies were followed within 3 calls by a similar call that was allowed (the "split the command" workaround). The false-positive share has **not** been measured. | MEASURED / HEURISTIC | audit |
| P4 | Relative deletes are missed today: `rm -rf Data` (no trailing separator), `Remove-Item Data\x`, `find Data -delete`, `git clean -fdx`. Relative `mv`/`cp`/redirect/`sed -i` into `Data/` are allowed. | MEASURED | audit |
| P5 | Claude `PreToolUse` payloads carry a top-level `cwd` for `Bash`, `PowerShell`, `Edit` and `Write` (scrubbed to `<path>` in the fixtures). **Not yet known: does it track the Bash tool's persistent `cd`?** That is measurement M5a. | READ | `tests/fixtures/payloads/claude/*.json` |
| P6 | Every transcript line in `~/.claude/projects/**/*.jsonl` carries `cwd`, so replay items can be priced with the cwd they ran in. | MEASURED (one file) | this plan's prep |
| P7 | The history holds 1,575 raw PowerShell tool calls. They were not replayed, because the toolkit's matcher is `Bash` only. | MEASURED | equivalence replay |
| P8 | `replay.py` `make_install` copies the **working tree's** `tools/skyguard` into BOTH installs (`record.py:63-65`). It therefore cannot compare two engine versions; it compares only bash-reference vs engine. | READ | `scripts/guard-corpus/record.py` |
| P9 | `paths.read_env` lets the FILE override the environment. The Codex `active-plugins.js` lets the ENVIRONMENT win (`PLUGINS_TXT` > `SKYRIM_LOADORDER_DIR` env > paths file > probe). The two precedences disagree. | READ | `paths.py:285-304`; codex branch `tools/xelib/active-plugins.js` |
| P10 | `setup.sh` already computes `MO2_MODS` and `MO2_OVERWRITE` (lines 278-279) but writes only 3 keys to `skyrim-paths.env` (lines 369-372). | READ | `setup.sh` |
| P11 | The only console command with a MEASURED crash is a full `smp reset` (708-plugin VR order, reproduced by hand). | MEASURED (install) | `KNOWLEDGEBASE.md:1505`; install memory `kb-devbench-live-game-api` |
| P12 | No MCP payload has been captured for either agent. | READ | `tests/fixtures/payloads/README.md` "Not captured yet" |
| P13 | `esp-verify-wrapper.sh verify` prints PASS when both inventories are empty, which is a false PASS on an absent inventory. | READ | `tools/esp-verify-wrapper.sh:57-79,140-165` |
| P14 | Install `4a429bd` drops **every** heredoc body before matching. That includes a body fed to `bash`/`sh`, which IS executed. So `bash <<'EOF'` + `resaver-cli.sh reset-havok x.ess --apply` + `EOF` does not ask in the install either. | INFERRED from the diff (reproduce in Task 4) | install `git show 4a429bd` |
| P15 | The toolkit master working tree currently has **uncommitted** changes to the five hook wrappers, `agent/guards/adapters/claude/*.sh`, `setup.sh`, `tests/bin/*`, `tests/test_hooks.py`, `test_repo_invariants.py` and `test_setup_paths.py`. This plan did not make them, and does not know who owns them. | MEASURED 2026-10-02 | `git status` |

---

## Preconditions (Task 0 checks every one; any failure stops the plan)

- Plan 2's final review is closed, and its replay certified equivalence (`2095ecd`: 0 unexplained).
- The Codex branch `codex/harness-repairs-fd71271` (head `3c68ef4` or later) is reviewed and merged into
  master: `git merge-base --is-ancestor 3c68ef4 master`.
- The P15 working-tree changes are committed or discarded **by their owner**. Plan 5 never stashes,
  commits or reverts them.
- No latency measurement (`scripts/guard-latency.py`), history replay or mutation run is in progress.
  Never edit the tree while one runs.

## Dependencies on Plan 4 (runs in parallel; Plan 4 owns the Codex adapter and the verdict interface)

Plan 5 **does not edit** `tools/skyguard/adapters/codex*`, `tools/skyguard/verdict.py` or any `.codex/`
file. Handshake:

| Need | Plan 5 does | Plan 4 provides |
|---|---|---|
| `cwd`, shell dialect | Rule functions take keyword args `cwd: str = ""` and `shell: str = ""` (`"bash"`, `"powershell"`, `"cmd"`, or `""` for unknown). The defaults keep every current caller valid. The **Claude** adapter's `extract()` gains `cwd` and maps `tool_name` to `shell`. | The Codex adapter passes `shell="powershell"` (E3) and its `cwd`. |
| MCP | `mcp_rules.decide(tool_name, args, roots, audit)` | Codex MCP mapping, if any. |
| `ask` on Codex | Nothing. An `ask` verdict stays an ask. | Ask → deny ("needs your confirmation — run it yourself"), and `.rules` `decision="prompt"` for the prefix-matchable asks (ReSaver `--apply`, the DevBench console `curl`). **Declared dependency D-P4-1:** Plan 4's `.rules` generator reads the prefix forms from `rules.RULES[...]["prefixes"]` (added in Task 1). |
| Evaluation status | The opaque-command advisory (Task 12) and the parse-failure fallback (Task 7) need to say "partial". **Declared dependency D-P4-2:** Plan 4's `Verdict` gains `status` (`evaluated`/`partial`/`failed`) and `reason_code`. Until it lands, Plan 5 records partial evaluation in the audit trail only (`audit.fired`, Task 1). Once it lands, a one-line follow-up in Plan 5's last task sets `status="partial"` from that trace. **Plan 5 does not add the field.** |
| `run.py` | Passes `cwd`/`shell` from the Claude adapter, and adds the `mcp` role. These are small edits. Conflicts with Plan 4's adapter registration are resolved at merge. | Adapter registry. |

## Branching

Plan 4 is mid-flight on master, so Plan 5 runs in worktree `../skyrimvr-toolkit-plan5` on branch
`plan5`, cut from master after the preconditions. It merges to master after Plan 5's final review (the
roadmap's parallel rule). Commit every finished task on `plan5`. **Never push.** Any tag or release
goes through `release-review`.

## Global Constraints

- **Corpus rows first** (RED) for every behaviour change, then the code (GREEN). New expectations are
  **authored by hand** in `tests/guard_corpus/intended.jsonl` from this plan's message table. They are
  never recorded from the new engine (see "How new expectations are recorded and reviewed").
- **Priced before it lands.** Every task that changes a verdict runs `scripts/guard-corpus/price.py`
  with old engine = the previous task's commit and new engine = the working tree. It records the per-rule
  counts in `docs/superpowers/measurements/2026-10-XX-plan5-pricing.md`, **before** the commit that
  changes behaviour. Read by hand:
  - **every** newly-denied and newly-asked item;
  - **every** no-longer-denied and no-longer-asked item (the loosening side);
  - for advise: all items if ≤ 100, otherwise a seeded random 100, with the FP rate given with its
    denominator.
  - **Cap:** if a task produces more than 200 newly-denied plus newly-asked items, STOP. The rule is too
    broad; narrow it and re-price. Do not hand-wave a large count.
- **False positives are as serious as misses.** Each FP class found is fixed (rule narrowed + an intended
  row) or accepted by a `Ruling:` that states its count.
- **Mutation entries** in `tests/test_mutations.py` for every new rule and branch: **one twin per clause**
  of each compound condition. The ids are `rule-<RULE-ID>-<clause>`. `test_every_rule_has_a_mutation`
  (Task 1) enforces at least one per registry id.
- **Message contract** (Task 1 test, made strict in Task 13). Applies to every engine `ask`, `advise`
  and `deny` message:
  - ≤ 2 lines, each ≤ 200 characters after substitution;
  - no `{c}`/command placeholder in any template;
  - a file is named by its **basename** only (`and N more` for several);
  - it never contains a ≥ 24-character run of the command that is not a basename;
  - plain words: an ask says what changes, which file, and what Yes does; a deny says what was blocked
    and what to do instead.
  - The wrapper-level `GUARD INERT` texts in `agent/guards/adapters/claude/*.sh` are exempt (Ruling R13).
- Engine floor Python 3.8; stdlib only; pure functions in `shellparse`/`targets` (no filesystem access,
  except the DevBench save-exists check in Task 11).
- Test command, from Task 15 onward: `python -B tests/run_harness_tests.py tests/ -q -rs`. Before
  Task 15: `python -m pytest tests/ -q -p no:cacheprovider`.
- Latency: re-run `scripts/guard-latency.py` after Task 7 and in Task 17. Gate: p95 ≤ 1 s (spec M4).
  Report the delta against Plan 2's figure. Never run it concurrently with a replay, pricing or mutation run.

## How new expectations are recorded and reviewed

The bash reference in `tests/guard_corpus/reference/` defines **the old behaviour only**.

1. **`expected.jsonl` is frozen.** It is never re-recorded. After Task 1 it is checked by **decision and
   rule id**, not by text, because every message is rewritten. `tests/guard_corpus/legacy_rules.json`
   maps each legacy text template, frozen from the reference hooks, to its rule id. An advise row with
   joined texts (` | `) maps to the set of rule ids.
2. **`intended.jsonl` is the spec for new behaviour.** Each row is authored by hand:

   ```json
   {"id": "t07-rel-rm-data", "layout": "declared", "hook": "protect-bash.sh",
    "payload": {"tool_name": "Bash", "cwd": "<install>", "tool_input": {"command": "rm -rf Data/meshes/x"}},
    "payload_note": "install-relative", "decision": "deny", "rules": ["S-DEL-INSTALL"],
    "text": "Blocked: this deletes x inside the Skyrim install.\nA mod manager cannot restore your own files there; if you mean it, run it yourself.",
    "supersedes": null, "task": "T7", "why": "relative delete into Data/ resolved from cwd (P4)"}
   ```

   - `text` is exact. It is written from the message table below, never pasted from engine output.
   - `supersedes` names a legacy row whose verdict this row deliberately changes. The payload must be
     identical, so the row really re-specifies that case.
3. **Tests** (`tests/test_guard_corpus.py`, Task 3):
   - each legacy row that is not superseded reproduces its decision and rule-id set;
   - each intended row reproduces decision + rule ids + exact text through the real wrapper (`record.fire`);
   - every supersession points at an existing legacy row with an identical payload;
   - every supersession is **live**: the new engine differs from the legacy verdict. A stale
     supersession fails, so it cannot hide a later regression;
   - every intended row has a non-empty `why` and a `task`.
4. **Review.** `scripts/guard-corpus/intent_diff.py <rev-range>` prints every added or changed intended row
   as a table: id, payload shape, old decision (legacy or previous intended), new decision, rules, `why`.
   Each task's reviewer reads that table **before** the code diff. Each row's decision is checked against
   this plan's rule table, and each `supersedes` against the pricing section for that task.
5. **Debugging aid:** `record.py --check-intended [--id X]` fires intended rows and prints actual vs
   intended. It never writes the file.

## Rule and message table (the target state; ids are stable)

`{file}` = a basename; `{op}` = the subcommand or console command; `{save}` = a save basename.

**Shell guard (`protect-bash`; Bash and PowerShell tools)**

| Rule | Fires when (executed program + resolved operand) | Decision | Message |
|---|---|---|---|
| S-INERT-NOCMD | no command, whitespace-only, or a non-string command | deny | `GUARD INERT: the shell guard received no usable command, so it checked nothing.` / `Investigate the hook payload before retrying.` |
| S-INERT-NOROOT | the install root cannot be derived | deny | `GUARD INERT: the shell guard could not work out where the Skyrim install is, so it checked nothing.` / `Re-run setup.sh from your Skyrim folder.` |
| S-INERT-CONFIG | a protection key in `skyrim-paths.env` is malformed | deny | `GUARD INERT: {file} has an unreadable value for {op}, so a protected folder is unknown.` / `Re-run setup.sh to rewrite it.` |
| S-DEL-INSTALL | a delete primitive's operand resolves inside the install (minus the scratch allowlist, R8) | deny | `Blocked: this deletes {file} inside the Skyrim install.` / `A mod manager cannot restore your own files there; if you mean it, run it yourself.` |
| S-DEL-CONFIG | … resolves inside the config dir | deny | `Blocked: this deletes {file} in the Skyrim settings folder.` / `Your INIs and controlmap there cannot be restored by a mod manager.` |
| S-DEL-MO2 | … resolves inside MO2 `mods/` or `overwrite/` | deny | `Blocked: this deletes {file} inside Mod Organizer's mods or overwrite folder.` / `If you mean it, remove the mod in MO2 or run it yourself.` |
| S-DEL-UNRESOLVED | a delete primitive has an unresolvable operand (variable, glob root, pipeline input) **and** the same statement names a protected root | deny | `Blocked: this deletes a path the guard cannot work out, in a command that names a protected Skyrim folder.` / `Write the full path, or run it yourself.` |
| S-DEL-FALLBACK | parse failed **and** the legacy co-occurrence test hits (R4) | deny | `Blocked: the guard could not read this command, and it contains a deletion and a protected Skyrim folder.` / `Split it into simpler commands, or run it yourself.` |
| S-REGISTRY | `reg delete` / `Remove-Item(Property)` on a Bethesda key | deny | `Blocked: this deletes Bethesda registry keys, which the game and tools use to find the install.` |
| S-PSC-OUT | a decompiler/compiler's `-o`/`--output`/`-OutputPath` operand is a `.psc` | deny | `Blocked: this tool would write its output straight over {file}.` / `Send output to a separate folder and copy back what you want.` |
| S-CHAMPOLLION | Champollion is executed with a `.pex` operand resolving into the install's `Data/Scripts` | deny | `Blocked: Champollion on {file} inside Data/Scripts has destroyed .psc sources before.` / `Copy the .pex to a temp folder and run it there.` |
| S-WRITE-PLUGIN | `>`/`>>`, `tee`, `Set-Content`/`Add-Content`/`Out-File`/`Clear-Content`, `[IO.File]::Write*`/`AppendAll*`, `sed -i`, `dd of=` into `*.esp/esm/esl/bsa/ba2` (any location, like the Write tool) | deny | `Blocked: this writes {file} as raw text, which corrupts a plugin or archive.` / `Use xelib, Spriggit or AutoMod to change it.` |
| S-COPY-PLUGIN | `cp`/`mv`/`Copy-Item`/`Move-Item`/`copy`/`move` whose **destination** is a plugin/archive in the install or MO2 | ask | `This replaces {file} with another file.` / `Yes overwrites it in place; the current copy is not kept.` |
| S-RESAVER-APPLY | the ReSaver CLI (`resaver-cli(.sh)`, `ResaverCLI`, `java -jar *resaver*.jar`) is executed with `--apply` and `reset-havok`/`cleanse-formlists`/`remove-created` | ask | `ReSaver will MODIFY a save file ({op} on {save}). Approve only if you asked for this save to be changed.` (install text, R17) |
| S-DEVBENCH-CRASH | the DevBench console (curl to `:8920`/`:8921` `/api/tool/console`, `devbench-cli.sh exec`) runs a denied command (R14) | deny | `Blocked: '{op}' crashes a large load order in the live game.` / `Use a targeted command instead (see the devbench skill).` |
| S-DEVBENCH-SAVE | DevBench `game` save names an existing save (or the save folder is unresolvable) | ask | `DevBench will save over the existing save {save}.` / `Yes replaces that save; the old one is not kept.` |
| S-WRITE-INSTALL | a write primitive into a non-plugin file in the install | advise | `Note: this overwrites {file} in the Skyrim install (a redirect empties it first).` |
| S-WRITE-LOADORDER | a write primitive into `plugins.txt`/`loadorder.txt` | advise | `Note: this writes {file}; your mod manager owns it and may undo the change.` |
| S-WRITE-INI | a write primitive into `Skyrim*.ini` | advise | `Note: this writes {file}; a later INI in the load chain can override it.` |
| S-WRITE-SKSE-INI | a write primitive into `Data/SKSE/Plugins/*.ini` | advise | `Note: this writes {file}; no snapshot covers SKSE configs, so copy it first.` |
| S-MOVE-INSTALL | copy/move whose destination resolves into the install (non-plugin) | advise | `Note: this copies or moves onto {file} in the Skyrim install; an overwrite there is silent.` |
| S-SED-WINPATH | `sed -i` on an install/config file **and** a backslash path in the replacement | advise | `Note: sed's replacement contains a backslash path, which sed mangles; use forward slashes.` |
| S-AUTOMOD-ESP | automod-cli is executed with an ESP write subcommand, without `--dry-run` | advise | `Note: AutoMod is writing {file} without --dry-run; the rule is dry-run first.` |
| S-AUTOMOD-NIF | automod-cli `nif` write subcommand executed | advise | `Note: cross-read {file} with an independent parser before testing it in game.` |
| S-AUTOMOD-ARCHIVE | automod-cli `archive` write subcommand executed | advise | `Note: loose files override {file}; check for loose copies before trusting it.` |
| S-EXTERNAL-OUT | an executed Papyrus/ESP tool's output flag resolves into the install/config | advise | `Note: {op} writes its output into the Skyrim install; check it landed where you meant.` |
| S-RELATIVE-UNKNOWN | a relative delete naming `Data` while `cwd` is unknown or untrusted (R9) | advise | `Note: this deletes {file} by a relative path, and the guard was not told the working folder.` |
| S-OPAQUE | **only if Task 12's pricing keeps it (R15):** an opaque program (`bash x.sh`, `python x.py`, `node x.js`, `eval`, `Invoke-Expression`) is given a protected path or plugin as an argument | advise | `Note: the guard cannot see what {op} does with {file}.` |

**Removed:** ADV_DELETE (R10), ADV_RELATIVE (→ cwd resolution / S-RELATIVE-UNKNOWN), ADV_PLUGIN (R11),
ADV_LOADORDER-on-read and ADV_REDIRECT (→ the S-WRITE-* rules).

**Write guard (`protect-files`; Edit/Write)**

| Rule | Fires when | Decision | Message |
|---|---|---|---|
| W-INERT-NOPATH | no / empty `file_path` | deny | `GUARD INERT: the file guard received no file path, so it checked nothing.` / `Investigate the hook payload before retrying.` |
| W-PLUGIN | path ends in a plugin/archive extension (unchanged condition) | deny | `Blocked: writing {file} directly corrupts a plugin or archive.` / `Use xelib, Spriggit or AutoMod to change it.` |
| W-INI | `Skyrim*.ini` | advise | `Note: a later INI in the load chain can override {file}; diff before and after.` |
| W-SKSE-INI | `Data/SKSE/Plugins/*.ini` | advise | `Note: no snapshot covers {file}; copy it before changing it.` |
| W-LOADORDER | `plugins.txt`/`loadorder.txt` | advise | `Note: your mod manager owns {file} and may undo a direct edit.` |
| W-PAPYRUS | `.psc`/`.pex` **resolving into the install's `Data/Scripts` or MO2 `mods/`** (narrowed, priced in Task 9) | advise | `Note: the game loads {file} at startup only; snapshot it before experimenting.` |
| W-INSTALL | the catch-all, **resolving into the install/config/MO2** (narrowed from the text match on `Data/`) | advise | `Note: editing {file} inside the live Skyrim install.` |

**MCP guard (`mcp__devbench.*`, new role `protect-mcp`):** S-DEVBENCH-CRASH and S-DEVBENCH-SAVE, applied to
the MCP `console` and `game` tools.

---

## Rulings

Each is stated as Ruling, then why, then the cost if wrong.

- **R1** — Ruling: `expected.jsonl` stays frozen. New behaviour lives in a hand-authored `intended.jsonl`
  and is never recorded from the new engine. — Why: the bash reference no longer defines behaviour, and
  recording from the engine would make the corpus agree with whatever was written. — Cost if wrong: a
  hand-authored row can encode a wrong expectation. The safety nets are `intent_diff.py` review plus
  pricing.
- **R2** — Ruling: pricing evaluates engines **in-process** (a driver imports each engine tree), not
  through the bash wrappers. — Why: minutes instead of hours; Plan 5 does not change the wrapper's
  decision path. — Cost if wrong: a wrapper-level difference (payload extraction for the new
  PowerShell/`cwd`/MCP fields) goes unpriced. Covered by the corpus rows firing through real wrappers,
  and by the Task 17 live E2E.
- **R3** — Ruling: guard protections take the **union** of the env and file values for every root. Tool
  lookup keeps the Codex precedence (`PLUGINS_TXT` > `SKYRIM_LOADORDER_DIR` env > paths file > probe). —
  Why: a guard must never lose a root because another source shadows it (P9). — Cost if wrong: a stale
  env var adds a protected root, which causes FPs. It shows up in pricing.
- **R4** — Ruling: if the command cannot be parsed, the legacy co-occurrence rules apply (S-DEL-FALLBACK,
  and the legacy advise checks). It never falls through to allow. — Why: fail closed (spec §5.1 spirit).
  — Cost if wrong: commands the parser cannot read keep today's FPs. Their count is reported in every
  pricing run and must stay < 0.5% of items; above that, the parser is fixed first.
- **R5** — Ruling: an unknown dialect (`shell=""`) is parsed as bash **and** PowerShell, and the
  effects are unioned. — Why: E3. Text alone cannot tell them apart reliably. — Cost if wrong: extra
  denies on ambiguous text, which are priced.
- **R6** — Ruling: an **unquoted** backslash in a bash token is evaluated under both readings (bash
  escape-stripped, and literal Windows path). The stricter reading wins. — Why: `rm C:\GOG\x` in Git
  Bash really runs on `C:GOGx`, but the intent, and the next attempt, is the Windows path. — Cost if
  wrong: rare FPs.
- **R7** — Ruling: a heredoc body is (a) parsed as commands when its consumer is a shell (`bash`, `sh`,
  `pwsh`, `powershell`); (b) checked with the legacy text rules (for example the rmtree regex) when the
  consumer is another interpreter (`python`, `node`); (c) treated as data otherwise (`cat > f <<EOF`
  is a write to `f`). — Why: P14. Dropping every body, as install `4a429bd` does, misses an executed
  ReSaver run. — Cost if wrong: some extra asks on `bash <<EOF` scripts. **This deviates from the
  install**, so it is listed under "needs the user".
- **R8** — Ruling: delete protection covers the install root and everything under it, **except a
  scratch allowlist** kept as data (`paths.SCRATCH_GLOBS`). The allowlist is seeded with
  `**/__pycache__/**`, `**/node_modules/**` and `**/.pytest_cache/**`, and **extended only from
  hand-read replay FPs**, each extension with its own count. `.claude/` stays protected (hooks and
  backups are the safety net). — Why: P3. The user's sessions run *inside* the install, so the toolkit's
  own files live under the protected root. — Cost if wrong: an allowlisted path really mattered. Each
  extension is priced in the loosening direction.
- **R9** — Ruling: the payload `cwd` resolves relative operands **only if M5a proves it tracks the Bash
  and PowerShell tools' persistent working directory**. An in-command `cd <absolute>` / `Set-Location`
  is always tracked. Otherwise relative deletes naming `Data` keep advising (S-RELATIVE-UNKNOWN) and are
  never denied. — Why: a wrong cwd turns relative resolution into both FPs and misses. — Cost if wrong:
  the relative-delete gap (P4) stays open, as advice only.
- **R10** — Ruling: ADV_DELETE is deleted. — Why: 0 hits in 49,343 replayed calls, and by construction
  (any delete into the install already denies). — Cost if wrong: none measurable. A corpus row proves its
  removal changes no verdict.
- **R11** — Ruling: ADV_PLUGIN is deleted. Reading a plugin is silent; writing one by a shell primitive is
  S-WRITE-PLUGIN (deny); copying onto one is S-COPY-PLUGIN (ask). — Why: 3.3% of all calls (P2), nearly
  all reads. — Cost if wrong: an opaque program writing a plugin gets no note. The Edit/Write deny
  remains, and Plan 6's read-only lock is the backstop.
- **R12** — Ruling: an ask still drops the advice gathered before it. The dropped advice is recorded in
  the audit log. — Why: the 2-line contract. — Cost if wrong: a note is lost on a rare ask.
- **R13** — Ruling: the wrapper-level fail-closed texts (`agent/guards/adapters/claude/*.sh`) are exempt
  from the message contract and are not edited by Plan 5. — Why: they report infrastructure failures, and
  those files are dirty in the tree (P15) and shared with Plan 4. — Cost if wrong: they stay long, but
  they appear only on failures.
- **R14** — Ruling: the DevBench deny-list is `smp reset` with no argument, the only MEASURED crash (P11).
  Every further entry needs evidence of the same kind. A save-overwrite asks only when the named save
  exists or the save folder cannot be resolved. — Why: an unmeasured deny-list is guesswork, and a
  blanket save-ask is fatigue. — Cost if wrong: another crashing command is not blocked. The skill text
  still warns.
- **R15** — Ruling: S-OPAQUE ships only if pricing shows it on ≤ 0.2% of replay items **and** ≥ 50% of
  its hand-read sample is useful. Otherwise it is dropped and the drop is ledgered. — Why: the audit's
  own warning that deny would block routine work, and the fatigue directive. — Cost if wrong: the
  opaque-script gap stays silent. The numbers are defaults the user may change.
- **R16** — Ruling: backup manifests and the nested PSC/PEX snapshot are **measured in Task 16 and
  deferred** to a separate side-effects plan. — Why: they are non-blocking hooks with a latency and disk
  budget, not rules. Snapshotting every nested `.pex` of a several-hundred-plugin order on each command
  is unbounded until the counts are known. — Cost if wrong: the roadmap row is not fully delivered by
  Plan 5. This is listed under "needs the user".
- **R17** — Ruling: the ReSaver ask text is ported **verbatim** from install `4a429bd`. — Why: the install
  and the toolkit should prompt identically. — Cost if wrong: none.
- **R18** — Ruling: work happens on branch `plan5` in a worktree, not on master. — Why: Plan 4 runs in
  parallel on master (the roadmap's parallel rule overrides the "commit on master" default for that
  reason). — Cost if wrong: merge conflicts in `run.py` and the Claude adapter, which are small.

---

## Review Focus

1. **A misparse is now a silent allow.** Today's text match over-blocks. A parser that drops a segment
   under-blocks. → `test_shellparse_*` (Task 5) and the parse-failure count in every pricing run. Every
   no-longer-denied item is read by hand. Mutation twins delete each parser branch.
2. **Heredocs, `$(…)`, backticks, `bash -c`, `pwsh -Command`, `cmd /c`, `xargs`, `find -exec/-delete`,
   pipelines into `Remove-Item`.** Each is an executed-code path that is easy to drop. → intended rows
   `t05-*`/`t07-*`.
3. **cwd.** Windows vs MSYS spellings, `..`, `~`, drive-relative `C:foo`, UNC paths, and a `cd` inside the
   command. → `test_targets_*` and M5a.
4. **The loosening direction.** E16, ADV_PLUGIN removal and the scratch allowlist each stop catching
   something. The per-rule no-longer lists are the evidence.
5. **No echo, ≤ 2 lines**, enforced by the test (not by reading templates), including messages built
   from long basenames.

---

### Task 0: Preconditions, baselines, and the two live measurements

**Files:** Create `docs/superpowers/measurements/2026-10-XX-plan5-pricing.md` (header and baselines only),
`docs/superpowers/measurements/2026-10-XX-plan5-live-probes.md`; add captured MCP fixtures under
`tests/fixtures/payloads/claude/` via `tools/payload-capture/scrub.py` only.

- [ ] **Step 1:** Check every precondition. Create worktree `../skyrimvr-toolkit-plan5`, branch
  `plan5`. Record the base sha in the pricing doc.
- [ ] **Step 2 — M5a (gates R9):** in a scratch repo wired to `tools/payload-capture/capture-hook.sh`,
  capture these with `claude -p --permission-mode bypassPermissions`:
  - Bash tool: `mkdir sub`; then `cd sub`; then `pwd`;
  - PowerShell tool: `Set-Location sub`; then `Get-Location`.
  
  Record each call's payload `cwd` against the shell's real cwd. **Decision, pre-committed:** if `cwd`
  equals the shell's real cwd after the `cd` for both tools, relative resolution trusts `cwd`. Otherwise
  R9's fallback applies, and Task 7's relative-deny rows are re-authored as S-RELATIVE-UNKNOWN advise.
- [ ] **Step 3 — M5b (gates Task 11):** capture a Claude MCP `PreToolUse` payload. Use a throwaway stdio
  MCP server in the scratch repo with tools `console` and `game`, registered as `devbench-vr`. Record
  `tool_name` (expected `mcp__devbench-vr__console`) and the `tool_input` shape. Scrub the result and
  commit it as a fixture, never hand-written (README rule). Also check whether matcher `mcp__devbench.*`
  matches the captured tool name, by wiring it and observing it fire.
- [ ] **Step 4:** Commit: `"Plan 5 Task 0: live probes (payload cwd tracking, MCP payload) and base"`.

### Task 1: Rule registry, rule trace, message-contract test (zero behaviour change)

**Files:** Create `tools/skyguard/rules.py`, `tests/test_rule_messages.py`, `tests/guard_corpus/legacy_rules.json`;
modify `shell_rules.py`, `write_rules.py`, `audit.py`, `tests/test_mutations.py`.

**Interfaces:**

```python
# tools/skyguard/rules.py
RULES = {
  # id: {"decision": "deny|ask|advise", "template": str, "legacy": bool, "prefixes": [..] (asks/denies
  #      Plan 4 may emit to .codex/rules), "hook": "protect-bash|protect-files|protect-mcp"}
}
def message(rule_id: str, **fields) -> str   # fields restricted to {"file","op","save","n"}; basename-only
```

`Audit` gains an in-memory `fired: List[Tuple[str, str]]` (rule id, decision), appended by a new
`audit.rule(hook, rule_id, decision, msg)`. The **log line format is unchanged**: hook-canary and
kb-guard read `AUDIT_LOG.txt`.

- [ ] **Step 1:** Move every existing template into `RULES` under its new id, keeping the **old text**
  with `"legacy": True`. Every `advise()`/`deny()`/ask call records its rule id.
- [ ] **Step 2:** Write `tests/test_rule_messages.py`:
  - `test_no_template_echoes_the_command`: no template contains `{c}`, `{p}` or `{command}`. Legacy
    templates are listed as **expected failures**, by id, in an explicit `LEGACY_ALLOWED` set.
  - `test_message_shape`: every template rendered with a 60-character basename stays at ≤ 2 lines and
    ≤ 200 characters per line (legacy ids excepted).
  - `test_legacy_set_only_shrinks`: `LEGACY_ALLOWED` ⊆ the ids still marked legacy. This prevents a new
    rule from joining the exemption.
  - `test_every_rule_has_a_mutation`: every non-legacy id appears in some `MUTATIONS` param id
    (`rule-<ID>-…`).
- [ ] **Step 3:** Generate `legacy_rules.json` (legacy text template → rule id) with a one-off script
  from the reference hook texts. Commit the JSON, not the script.
- [ ] **Step 4:** Run the corpus test and `test_skyguard_*`. They must pass with **no row changed**. This
  commit becomes the pricing base `P5-BASE`.
- [ ] **Mutations:** `rule-trace-records-id` (drop the `audit.rule` call in `deny()` → `test_skyguard_shell` trace test red).
- [ ] Commit: `"skyguard: rule registry and rule trace (no verdict change)"`.

### Task 2: The pricing tool — engine vs engine over real history

**Files:** Create `scripts/guard-corpus/price.py`, `scripts/guard-corpus/engine_eval.py`,
`tests/test_price_tool.py`; modify `replay.py` (extraction shared via an `extract()` that now also takes
`PowerShell`, `mcp__devbench*`, and the per-line `cwd`).

**Interfaces:**
- `price.py --base <rev> [--new <dir>] --history ~/.claude/projects --out-dir <outside repo> [--only-rule ID]`
  - exports `<rev>:tools/skyguard` by `git archive` into a temp dir;
  - runs `engine_eval.py` once per engine as a subprocess with `PYTHONPATH` = that tree, streaming
    items as JSONL;
  - the driver calls the engine's `decide` with whichever kwargs its signature accepts
    (`inspect.signature`), using a recording `Audit` that writes nothing to disk;
  - the driver returns `{decision, text, fired:[ids], parse_ok}` per item.
- Items are deduplicated on `(tool_name, command|file_path|mcp-input, cwd)`. Raw items go to `--out-dir`
  only (refused inside the repo, as `replay.py` already does).
- Output: per rule × direction (`newly-deny`, `newly-ask`, `newly-advise`, `no-longer-deny`,
  `no-longer-ask`, `no-longer-advise`), plus the old×new decision matrix, the parse-failure count, and N
  per tool. One JSONL file per (rule, direction) goes to `--out-dir` for hand reading. **Only counts** go
  into the repo.
- A rule "newly fires" for an item when its id is in `new.fired` and not in `old.fired`. Decision
  movement is counted separately, so an item whose decision did not change but whose reason moved to a
  different rule is visible.

- [ ] **Step 1:** RED tests (`tests/test_price_tool.py`, tiny synthetic history in `tmp_path`):
  - identical engines → every direction is 0;
  - a scratch engine copy with one broadened rule → that rule's `newly-*` > 0 **and every other rule 0**
    (proves the tool can go red, and attributes per rule);
  - an item whose transcript line carries `cwd` → the driver receives it;
  - an out-dir inside the repo → refused.
- [ ] **Step 2:** Implement. **Step 3:** Baseline run `--base P5-BASE --new P5-BASE`. It must be all
  zeros; record N per tool (expect Bash ≈ 52k raw, PowerShell 1,575 raw, plus MCP) in the pricing doc.
- [ ] **Mutations:** `price-attribution-per-rule` (attribute every newly-fired id to the first rule →
  synthetic test red).
- [ ] Commit: `"guard-corpus: price.py — per-rule old-vs-new engine pricing over real history"`.

### Task 3: Corpus v2 — `intended.jsonl`, supersessions, decision+rule legacy check

**Files:** Create `tests/guard_corpus/intended.jsonl` (empty), `scripts/guard-corpus/intent_diff.py`;
modify `tests/test_guard_corpus.py`, `scripts/guard-corpus/record.py` (`--check-intended`; `cwd`
templating `<install>` in `payload.cwd`), `tests/guard_corpus/README.md`.

- [ ] **Step 1:** Write the five tests listed under "How new expectations are recorded and reviewed",
  plus the `install-relative` templating for `payload.cwd`.
- [ ] **Step 2:** Switch the legacy check to decision + rule id (via `legacy_rules.json` and the engine
  trace). It must be green with zero supersessions. Then a twin: inject one fake stale supersession
  → `test_supersessions_are_live` red.
- [ ] **Step 3:** README: the two files, the authoring rule, the review rule, and "never record intended
  rows from the engine".
- [ ] **Mutations:** `corpus-supersession-must-be-live`; `corpus-intended-text-exact` (compare
  decisions only → an intended row with a deliberately wrong `text` in the mutation copy stays green →
  the mutation must make `test_intended_rows_reproduce` red).
- [ ] Commit: `"guard corpus v2: hand-authored intended rows, supersessions, rule-id legacy check"`.

### Task 4: FIRST ITEM — port install `4a429bd` (ReSaver asks only when ReSaver runs; one line)

**Corpus first** (`intended.jsonl`, `task: "T4"`). These reconstruct the commit's 10 cases (4 asks, 6
non-asks) plus the audit's gaps:

| id | command shape | decision |
|---|---|---|
| t04-apply | `bash tools/resaver-cli.sh reset-havok "x.ess" --apply` | ask (text names `reset-havok on x.ess`) |
| t04-apply-env | `FOO=1 bash tools/resaver-cli.sh cleanse-formlists s.ess --apply` | ask |
| t04-apply-chain | `cd tools && ./resaver-cli.sh remove-created s.ess --apply` | ask |
| t04-apply-subst | `out=$(resaver-cli.sh reset-havok s.ess --apply)` | ask |
| t04-dry | `bash tools/resaver-cli.sh reset-havok s.ess` | allow |
| t04-heredoc-py | `python - <<'EOF'` … `# resaver-cli reset-havok --apply` … `EOF` (40 lines) | allow (**supersedes** the legacy-shaped ask) |
| t04-grep | `grep -n "reset-havok.*--apply" tools/resaver-cli.sh` | allow |
| t04-echo | `echo "run resaver-cli.sh reset-havok s.ess --apply later"` | allow |
| t04-apply-other-cmd | `bash tools/resaver-cli.sh info s.ess; echo --apply` | allow (`--apply` not in the ReSaver command) |
| t04-read | `bash tools/resaver-cli.sh dump s.ess` | allow |
| t04-java | `java -jar tools/resaver-cli/ReSaver.jar --apply reset-havok s.ess` | ask (audit gap) |
| t04-bash-heredoc | `bash <<'EOF'` / `tools/resaver-cli.sh reset-havok s.ess --apply` / `EOF` | ask (R7; the install misses this, P14) |

- [ ] **Step 1:** Add the rows (RED). Reproduce P14 against the install's `protect-bash.sh` and record the
  result in the pricing doc.
- [ ] **Step 2:** Implement in `shell_rules.py` **as the install did** (heredoc-body drop + command-position
  regex). The minimum extensions are: `java -jar`, a shell-consumed heredoc kept (R7), and `--apply`
  searched within the same executed command. The message is the install text (R17), via `rules.message`.
  Task 6 later re-bases this on `shellparse` with no row change.
- [ ] **Step 3: Price** `--only-rule S-RESAVER-APPLY`. Expected: no-longer-ask 3 (the P1 embedded-text
  asks), newly-ask ≈ 0. Read all of them.
- [ ] **Mutations (one per clause):**
  - `rule-S-RESAVER-APPLY-anchor` (anchor regex → bare text match: t04-heredoc-py red);
  - `-apply-same-cmd` (search `--apply` in the whole command: t04-apply-other-cmd red);
  - `-heredoc-drop` (keep all heredoc bodies: t04-heredoc-py red);
  - `-shell-heredoc-kept` (drop all bodies: t04-bash-heredoc red);
  - `-java` (remove the java form: t04-java red);
  - `-op-list` (drop `remove-created`: t04-apply-chain red).
- [ ] Commit: `"skyguard: ReSaver ask only when ReSaver is executed; one-line prompt (port of install 4a429bd)"`.

### Task 5: `shellparse` — what a command executes (bash, PowerShell, cmd)

**Files:** Create `tools/skyguard/shellparse.py`, `tests/test_shellparse.py`. No rule uses it yet, so
the corpus must be unchanged.

**Interface:**

```python
class Exec(NamedTuple):
    program: str          # normalised: basename, lower-case, .exe/.cmd/.bat/.ps1/.sh stripped; aliases mapped
    argv: Tuple[str, ...] # unquoted words (the quoting is remembered in `quoted`)
    quoted: Tuple[bool, ...]
    redirects: Tuple[Tuple[str, str], ...]   # (">"|">>"|"<"|"2>"..., target word)
    dialect: str          # "bash" | "powershell" | "cmd"
    cd_before: str        # absolute/relative dir from an earlier `cd`/`Set-Location` in the same statement chain, "" if none, "?" if unresolvable
    stdin_from: str       # "pipe" | "heredoc:<consumer-kind>" | ""
    segment: str          # the source text of this simple command (for the legacy fallback only)
class Parsed(NamedTuple):
    execs: Tuple[Exec, ...]
    ok: bool              # False -> rules use the legacy fallback (R4)
    why: str
def parse(command: str, shell: str = "") -> Parsed   # shell "" -> both dialects, execs unioned (R5)
```

**Required behaviour** (each item is a parametrized test in `tests/test_shellparse.py`):
- **bash:**
  - separators `; && || | & \n`; `( … )` and `{ …; }`;
  - `$( … )` and backticks parsed recursively, as are `<( … )`;
  - `VAR=x` prefixes skipped;
  - unwrapped: `sudo`, `env [-i] [VAR=x]`, `command`, `builtin`, `exec`, `nohup`, `time`, `nice`,
    `timeout N`, `xargs [opts]` (with `stdin_from="pipe"`);
  - `bash|sh -c STR`, `pwsh|powershell -Command STR` / `-c STR`, and `cmd /c STR` re-parsed in their own
    dialect;
  - `find P… -delete` and `find P… -exec rm …` → a delete Exec on the start paths;
  - heredocs per R7;
  - quotes: `'…'`, `"…"` with `\` escapes, `$'…'`; unquoted backslashes per R6;
  - comments (`#`) dropped outside quotes.
- **PowerShell:**
  - separators `; | && || \n`; `&` call operator; `.` dot-source;
  - quotes `'…'` (doubled `''`) and `"…"` with backtick escape;
  - aliases → canonical: `ri rm del erase rd rmdir` → `remove-item`; `mi mv move` → `move-item`;
    `cpi cp copy` → `copy-item`; `sc` → `set-content`; `ac` → `add-content`; `clc` → `clear-content`;
    `ni` → `new-item`; `sl cd chdir` → `set-location`; `iex` → `invoke-expression`;
  - named parameters `-Path -LiteralPath -Destination -FilePath -Value` (prefix-abbreviated, as PowerShell allows);
  - script blocks `{ … }` parsed recursively;
  - `[IO.File]::Write*/AppendAll*/Delete/Move/Copy('path', …)` → a synthetic Exec (`program="io.file.write"`, etc.);
  - `> >> 2>` redirects.
- **cmd:** `& && || |`; `del erase rd rmdir copy move`; `/s /q` flags.
- **Failure:** unterminated quote, unterminated heredoc or unbalanced `$(`, or more than 2,000 tokens or
  nesting > 8 → `ok=False`, with `why`. It **never raises**: a property test feeds 10,000 seeded random
  strings, plus every corpus command.

- [ ] **Step 1:** Write the tests (RED). **Step 2:** Implement. **Step 3:** Run
  `price.py --base P5-BASE` in parse-only mode (the driver reports `parse_ok`). Record the parse-failure
  count per tool. **Gate:** < 0.5% (R4); otherwise fix the parser before Task 7.
- [ ] **Mutations:**
  - `shellparse-subst-recursion` (do not descend into `$(…)` → test red);
  - `-heredoc-consumer` (treat every body as data → t04-bash-heredoc red, once Task 6 re-bases);
  - `-pwsh-alias` (drop `ri` → test red);
  - `-find-delete`;
  - `-bash-c`;
  - `-pwsh-command`;
  - `-never-raises` (make an unterminated quote raise → the property test red).
- [ ] Commit: `"skyguard: shellparse — executed commands for bash, PowerShell and cmd"`.

### Task 6: `targets` + config as data + cwd plumbing; ReSaver re-based on the parser

**Files:** Create `tools/skyguard/targets.py`, `tests/test_targets.py`. Modify:
- `paths.py`: strict parse; the `SKYRIM_MO2_MODS`/`SKYRIM_MO2_OVERWRITE` keys; R3 union; `SCRATCH_GLOBS`;
- `adapters/claude.py`: `extract` adds `cwd`, `shell` and `mcp_input`; a non-string command →
  `command_type`;
- `run.py`: passes `cwd`/`shell`;
- `setup.sh`: writes the 2 MO2 keys. Coordinate with P15's owner first. The file location move is
  Plan 8/9, not this plan;
- `agent/targets/claude/skyrim-paths.env.example`.

**Interface:**

```python
def resolve(word: str, cwd: str, dialect: str, env: Mapping[str, str]) -> Optional[str]
    # -> normalised absolute "c:/x/y" (lower-cased drive, forward slashes, ntpath-normalised ".."),
    #    or None if unresolvable ($VAR other than HOME/USERPROFILE family, globs in a dir component,
    #    "~user", "-", empty cwd for a relative word)
def zone(abs_path: str, roots: paths.Roots) -> str
    # "install" | "config" | "mo2" | "scratch" | "outside"; most specific wins; case-insensitive
```

`Roots` gains `install_dirs`, `config_dirs`, `mo2_dirs` (normalised absolute paths) **beside** the
existing regex fields. The regex fields stay for S-DEL-FALLBACK and the legacy advise checks.

- [ ] **Step 1 (RED), `tests/test_targets.py`:**
  - `C:\a\b`, `C:/a/b`, `/c/a/b`, `c:\A\B\..\b`, `\\server\share\x` (outside), `C:foo` (drive-relative →
    None), `~/x` (HOME), `$env:USERPROFILE\x`, `%USERPROFILE%\x` (cmd);
  - relative `Data\x` with a Windows cwd, under each dialect;
  - `cd_before` absolute and relative;
  - the scratch allowlist;
  - MO2 zone precedence over install when nested.
  - Config parse (`tests/test_skyguard_core.py`): setup's `sq` forms incl. `'\''`; a malformed value →
    `S-INERT-CONFIG` naming the key, **not** a silent skip; env + file union (R3).
  - JS/Python parity: `tests/fixtures/paths_precedence.json` is read by a new Node test beside
    `active-plugins.test.js` and by a Python test. Both must pick the same `plugins.txt`, with
    `PLUGINS_TXT` winning (Codex handoff item 4).
- [ ] **Step 2:** Implement. Re-base S-RESAVER-APPLY on `shellparse` (T4 rows unchanged → green).
- [ ] **Step 3: Price**
  - S-INERT-CONFIG: run against this machine's real `skyrim-paths.env` files (the install and the
    toolkit). Expect 0 malformed;
  - the whole engine: expect no verdict change apart from the T4 rule.
- [ ] **Mutations:**
  - `targets-cwd-used` (ignore cwd → relative tests red);
  - `targets-dotdot` (skip normpath);
  - `targets-msys`;
  - `paths-union` (file overrides env → R3 test red);
  - `paths-malformed-visible` (skip malformed → test red);
  - `setup-writes-mo2-keys`.
- [ ] Commit: `"skyguard: path resolution from payload cwd, MO2 roots, config parsed as data"`.

### Task 7: Delete rules on the parser — E16 fix, relative deletes, PowerShell forms, MO2

**Corpus first** (`t07-*`; each legacy row whose verdict changes gets `supersedes`).

**Must deny:**
- each `dialect-*` destroyer (unchanged decisions; new text);
- `rm -rf Data` and `rm -r Data/meshes` with cwd = install;
- `Remove-Item Data\x -Recurse`, `ri -r Data`, `del /s /q Data\x` (cmd);
- `find Data -delete`;
- `git clean -fdx` with cwd = install;
- `G="C:/GOG…"; rm -rf "$G/Data"` (S-DEL-UNRESOLVED);
- `Get-ChildItem "C:/GOG…/Data" | Remove-Item` (S-DEL-UNRESOLVED);
- MO2 `mods/X` and `overwrite/` deletes in all three dialects;
- `rm -rf .claude/backups` (install);
- `bash -c 'rm -rf "C:/GOG…/Data"'`;
- `python -c "import shutil; shutil.rmtree(r'C:/GOG…')"` (legacy regex on interpreter code, R7(b)).

**Must allow (read-only false-positive controls, E16):**
- `grep -rn "rm -rf" "C:/GOG…/tools"`;
- `rg "Remove-Item" "C:\GOG…"`;
- `Select-String -Pattern 'del /s' -Path "C:/GOG…/x.ps1"`;
- `git log --grep="delete Data"`;
- `echo "rm -rf C:/GOG…"`;
- `rm -rf /tmp/x && ls "C:/GOG…"` (legacy `shell-compound-tmp`, supersedes);
- `rm __pycache__/x.pyc` in the install (scratch).

**Unresolvable cwd:** with an empty `cwd`, `rm Data/x` → S-RELATIVE-UNKNOWN advise.

- [ ] **Step 1:** Rows (RED). **Step 2:** Implement:
  - delete primitives = `rm`, `rmdir`, `unlink`, `remove-item`, `del`/`erase`/`rd` (cmd), `find -delete`,
    `git clean` (target = cwd or its pathspecs), `shutil.rmtree` (interpreter code), `[IO.File]::Delete`.
  - **Clauses:**
    - (a) any operand resolves to install/config/MO2 and not scratch → S-DEL-*;
    - (b) an operand is unresolvable **and** the same statement's text names a protected root →
      S-DEL-UNRESOLVED;
    - (c) `ok=False` **and** the legacy DESTROYER+root test → S-DEL-FALLBACK.
  - ADV_DELETE and ADV_RELATIVE removed (R10).
- [ ] **Step 3: Price** (the largest task), per rule, all directions:
  - **newly-deny:** expect relative deletes from install-cwd sessions; read every one; extend
    `SCRATCH_GLOBS` only from FPs found here, each with its count (R8);
  - **no-longer-deny:** DENY_INSTALL's E16 class (read every one, and classify each as FP-removed or
    true-positive-lost; any lost TP → fix or Ruling);
  - **cap of 200** newly-deny items (Global Constraints).
- [ ] **Step 4:** Latency: `scripts/guard-latency.py`, gate p95 ≤ 1 s.
- [ ] **Mutations (twins per clause):**
  - `rule-S-DEL-INSTALL-resolved` (clause a off → relative rows red);
  - `-scratch` (empty allowlist → the `__pycache__` row red);
  - `rule-S-DEL-UNRESOLVED-needs-root-mention` (drop the mention test → a new row `rm -rf "$tmp"` (allow) red);
  - `rule-S-DEL-UNRESOLVED-needs-unresolved` (fire on any operand → the grep control red);
  - `rule-S-DEL-FALLBACK-on-parse-fail` (fallback → allow: a parse-fail row red);
  - `rule-S-DEL-CONFIG`, `rule-S-DEL-MO2` (each zone dropped);
  - `pwsh-remove-item-alias`;
  - `git-clean-cwd`;
  - `find-delete`.
- [ ] Commit (after pricing is recorded): `"skyguard: delete rules decide on executed commands and resolved paths (E16, relative, PowerShell, MO2)"`.
- **Known limits (recorded in Task 7 fix round 1; `tools/skyguard/deletes.py` docstring):**
  - interpreter code with RELATIVE paths (`python -c "shutil.rmtree('Data')"`, node/perl equivalents) is invisible
    unless the code also names a protected root;
  - `mv` out of the install with a relative source path gets no advice;
  - `shred`, `rsync --delete`, `git checkout -- .`, `git reset --hard`, `git stash -u` are not delete primitives;
  - per-install user folders such as `_CLAUDE_OUTPUT` are protected like the rest of the install; a configurable
    allowlist is a follow-up idea, not built;
  - an unresolved delete operand run from inside a protected folder is not flagged (ruling, fix round 1: measured 75 occurrences, 0 real, in 61,935 items);
  - `CMD="rm -rf <install>"; $CMD` (an assignment value is not an exec); PS5 `Remove-Item -Recurse` follows junctions.

### Task 7b (controller-added): same-command literal assignments resolve delete operands

S-DEL-UNRESOLVED denied ~190 history items whose operand is a variable in a command naming a root (~100 measured FPs:
scratch paths). The parser now records LITERAL assignments in the root command's top-level statement list and gives
each Exec `subst` (word → expanded word) for every use the assignment certainly reaches; `deletes.scan` substitutes
before clause (a), and S-REGISTRY uses the same resolver (its Task 9 regex resolver is removed). Text rules
(`tools/skyguard/assign.py`): a name written more than once anywhere (any form: reassignment, `for`/`read`/`printf
-v`/`export`/`local`/`unset`, `${N:=}`, `N++`, PowerShell scope writes, `foreach`, Set-Variable, -OutVariable,
parameters, writes inside strings/heredocs) is never resolved; values must be plain literals. Flow rules
(`shellparse.track_vars`/`lookup`): not inside `bash -c`/eval/backticks, `( )`, `$( )`, `{ }`, if/while/until/for/
select/case, a function, a pipeline, `&`, after `||`; a use resolves in a later statement only when the assignment was
first in its `&&` list, or later in the same pure `&&` chain; `source`/`.`/`eval`/dot-source/`Invoke-Expression`
poison every later use; cmd never resolves (it expands `%K%` before `set` runs).

- Corpus: 74 `t07b-*` rows (20 RED against dc8c59b, the rest pins); `t07-var-data` and `t07-legacy-var-only` now
  S-DEL-INSTALL (the latter no longer supersedes `testhooks-00bbbc607b`: it gives the legacy verdict again).
- Priced (dc8c59b, N = 63,637): S-DEL-UNRESOLVED 190 → 80; 88 lifted, all hand-read FPs, 0 TP lost; 22 deny→deny
  rule moves (21 S-DEL-INSTALL naming the file, 1 S-WRITE-PLUGIN); 1 deny→ask (S-RESAVER-APPLY, a real `--apply`).
- Tightenings: an unresolved delete run from inside the install with a variable that resolves into it now denies
  (`D=Data/meshes; rm -rf "$D"`, cwd = install); S-REGISTRY no longer resolves an assignment in a branch, a
  reassigned name, or cmd's `set K=... & reg delete "%K%"`.
- Self-review hardening (before review): names the shell sets or refuses (`PWD`, `OLDPWD`, `HOME`, `IFS`, `_`,
  PowerShell automatic variables) are never resolved; a command that writes `IFS` refuses every unquoted
  expansion; `exec {D}>f`, Tee-Object -Variable, PowerShell `set`, PSVariable count as writes; `source`/`eval`
  anywhere in a statement (also behind `builtin`/`command`) and `InvokeScript` poison. Re-priced: identical.
- Known limits: a value built from another variable (`Y="$SP/x"`) and a harmless second mention that reads like a
  write (`echo "D=$D"`) stay unresolved; the write rules (S-WRITE-PLUGIN) do not read `Exec.subst`.

### Task 8: Shell writes to plugins (E21) and the write-target advice; ADV_PLUGIN removed

**Corpus first** (`t08-*`).

**Deny (S-WRITE-PLUGIN):**
- `echo x > Foo.esp`, `>> X.bsa`;
- `tee Data/X.esm`;
- `Set-Content Foo.esp x` (as Bash **and** PowerShell tool payloads);
- `Out-File -FilePath X.esl`;
- `[IO.File]::WriteAllBytes('C:\…\X.esp', $b)`;
- `sed -i s/a/b/ X.esp`;
- `dd if=a of=X.esp`.

**Ask (S-COPY-PLUGIN):** `cp build/My.esp "C:/GOG…/Data/My.esp"`;
`Copy-Item out\My.esp Data\My.esp -Force` (cwd = install); `mv x.bsa <MO2>/mods/M/x.bsa`.

**Allow:**
- `cp Data/My.esp backup/My.esp` (source, not destination);
- `cat X.esp | xxd`;
- `node tools/xelib/x.js Data/My.esp`;
- `bash tools/spriggit-cli.sh deserialize …`;
- `python read.py Data/My.esp` (legacy `shell-plugin-ref` advise → allow, supersedes).

**Advise:**
- `Out-File` into `Data/SKSE/Plugins/x.ini` → S-WRITE-SKSE-INI (E21's "no message at all");
- `echo x >> plugins.txt` → S-WRITE-LOADORDER;
- `cat plugins.txt` → allow (supersedes `shell-loadorder-ref`).

- [ ] **Step 1:** Rows (RED). **Step 2:** Implement, sharing one target classifier with `write_rules`
  (the `targets.zone` and extension classes). ADV_PLUGIN, ADV_REDIRECT and ADV_LOADORDER are deleted.
- [ ] **Step 3: Price:**
  - newly-deny S-WRITE-PLUGIN: expect ~0; read all;
  - newly-ask S-COPY-PLUGIN: read all. **If > 20 are the user's own deploys of a freshly built ESP,
    stop and bring it to the user before landing** (the spec chose ask; fatigue may argue for advise);
  - no-longer-advise ADV_PLUGIN (~1,627): read the seeded sample of 100; any **write** among them →
    there is a missed write primitive; fix it.
- [ ] **Mutations:**
  - one per primitive class: `rule-S-WRITE-PLUGIN-redirect`, `-tee`, `-set-content`, `-out-file`,
    `-iofile`, `-sed-i`, `-dd`;
  - `rule-S-COPY-PLUGIN-destination-only` (match the source too → the backup row red);
  - `rule-S-COPY-PLUGIN-zone` (any location → an outside `cp` row red);
  - `rule-S-WRITE-SKSE-INI`;
  - `rule-S-WRITE-LOADORDER-write-only` (fire on reads → the `cat plugins.txt` row red).
- [ ] Commit: `"skyguard: shell writes to plugins are blocked (E21); write advice fires only on writes"`.
- **Rulings and known limits (recorded in Task 8; `tools/skyguard/writes.py` docstring):**
  - S-COPY-PLUGIN ships as ADVISE (controller ruling): priced 36 items, all the user's own deploys, 0 misses;
  - S-WRITE-INSTALL covers the install's `Data/`, the config folder and MO2 only (20 of 23 legacy redirect notes
    were the user's own scripts/notes elsewhere in the install);
  - the opaque-program fallback reads the opaque program's own words and the nearest upstream program feeding its
    pipe, not every exec in the command (2 hook-harness FPs removed);
  - not handled: `perl -i`, interpreter code and opaque scripts writing a plugin (R11), an unresolved copy
    destination that names no Data folder (`"$D/X.esp"`), `touch`/`truncate`;
  - S-REGISTRY is still a text match (the live printf FP, 2026-10-03): Task 9 anchors it to the executed program.

### Task 9: Anchor and shorten every remaining advise; narrow the write-guard advice

**Corpus first** (`t09-*`), each with a negative twin that only *mentions* the tool:
- S-MOVE-INSTALL: destination only;
- S-SED-WINPATH: needs a backslash path in the replacement;
- S-AUTOMOD-ESP / -NIF / -ARCHIVE: executed automod-cli only. `cat docs/automod.md | grep add-weapon` → allow;
- S-EXTERNAL-OUT: the executed tool's own output flag;
- S-PSC-OUT, S-CHAMPOLLION (now cwd-resolved: `Champollion Data/Scripts/x.pex` with cwd = install → deny, a tightening);
- S-REGISTRY: executed `reg`/`Remove-ItemProperty`;
- W-PAPYRUS: install `Data/Scripts` or MO2 only (a `.psc` in the toolkit checkout → allow, supersedes);
- W-INSTALL: resolved zone only (a `Data/x.txt` relative Write path with cwd outside → allow, supersedes `write-relative-data`).

All messages switch to the new templates; the legacy flag is cleared for each.

- [ ] **Step 1:** Rows. **Step 2:** Implement. **Step 3: Price**, per rule: the no-longer direction
  dominates (ADV_EXTERNAL 220, ADV_MOVE 132, PAPYRUS 72). Read samples. Every true positive lost → fix
  or Ruling. Newly-deny for S-CHAMPOLLION: read all.
- [ ] **Mutations:** one `rule-<ID>-anchor` per rule (revert to a text match → its negative twin red),
  plus `rule-W-PAPYRUS-zone` and `rule-W-INSTALL-zone`.
- [ ] Commit: `"skyguard: every advice fires on the executed program; 2-line messages"`.

### Task 10: Wire the PowerShell tool (E12): matcher `Bash|PowerShell`

**Files:** `agent/targets/claude/settings.json` (+ generated `.claude/settings.json` via
`scripts/gen_agent_trees.py`); `tools/skyguard/snapshot.py` (PowerShell read-only verbs added to `SKIP`:
`Get-*`, `Select-String`, `Test-Path`, `Resolve-Path`, `Measure-Object`); `tests/test_repo_invariants.py`;
`.github/workflows/ci.yml` hook-count/matcher gate; `tools/verify-release.sh` if it counts matchers.

- [ ] **Step 1:** RED tests:
  - `test_shell_matcher_covers_powershell` (the generated settings' shell matcher, compiled as a regex,
    matches `Bash` and `PowerShell` and not `Write`);
  - a corpus row firing a captured-shape PowerShell payload (`tool_name: "PowerShell"`) through the
    wrapper → `shell="powershell"`;
  - a snapshot test: `Get-ChildItem` → no snapshot.
- [ ] **Step 2:** Implement. **Step 3: Price** with `--tool PowerShell` (1,575 raw calls). Every
  PowerShell item is "new" relative to the unwired baseline. Report per rule and read every deny and ask.
- [ ] **Mutations:** `matcher-includes-powershell` (revert to `Bash` → the invariant test red);
  `snapshot-skips-pwsh-reads`.
- [ ] Commit: `"Claude hooks: guard the PowerShell tool too (Bash|PowerShell matcher, E12)"`.

### Task 11: DevBench — MCP and the curl/CLI console route

**Files:** Create `tools/skyguard/mcp_rules.py`, `tests/test_skyguard_mcp.py`. Modify:
- `run.py`: role `protect-mcp`;
- `adapters/claude.py`: `mcp_input` from the M5b fixture shape;
- `agent/targets/claude/settings.json`: matcher `mcp__devbench.*` → `protect-mcp.sh`;
- a new wrapper `agent/guards/adapters/claude/protect-mcp.sh`, generated as a copy of protect-files'
  wrapper pattern with role `protect-mcp`;
- `tests/test_repo_invariants.py`: hook count 5 → 6, and update every gate that counts hooks
  (`release.yml`, `verify-release.sh`, `hook-canary.sh` list) in the same commit.

**Corpus first** (`t11-*`):
- MCP `console {action: exec, command: "smp reset"}` → deny;
- `"smp reset <target>"`? → allow (only the global form is measured, R14);
- `"SMP  RESET"` (case, whitespace) → deny;
- `curl -s -X POST http://127.0.0.1:8921/api/tool/console -d '{"action":"exec","command":"smp reset"}'`
  → deny (JSON body parsed from `-d`/`--data`/`--data-raw`);
- `bash tools/devbench-cli.sh exec "smp reset"` → deny;
- `curl … -d @body.json` → allow + a ledgered gap (the body is unreadable offline);
- MCP `game {action: save, name: <existing>}` with a scratch Saves dir → ask;
- the same with a new name → allow; an unresolvable saves dir → ask;
- `inspect` and `ping` → allow.

- [ ] **Step 1:** Rows (RED). **Step 2:** Implement. The deny-list is data in `rules.py`. The save-exists
  check is the only filesystem read in the rules: `<config dir>/Saves/<name>.ess`.
- [ ] **Step 3: Price:** MCP + curl items from history. Read all.
- [ ] **Mutations:** `rule-S-DEVBENCH-CRASH-mcp`, `-curl`, `-cli`, `-case-fold`;
  `rule-S-DEVBENCH-SAVE-exists` (ask on every save → the new-name row red); `-unresolvable-asks`.
- [ ] Commit: `"skyguard: DevBench guard — crash-listed console commands denied, save-overwrite asks"`.

### Task 12: Analysis failures auto-deny; opaque-command advisory (priced, may be dropped)

**Corpus first** (`t12-*`):
- `{"command": "   \n\t"}` → deny S-INERT-NOCMD;
- `{"command": ["rm","-rf"]}` → deny S-INERT-NOCMD (no longer stringified);
- `{"command": 42}` → deny;
- S-OPAQUE positives: `python fix.py "C:/GOG…/Data/X.esp"`, `bash tools/x.sh Data/` (cwd = install);
- S-OPAQUE negatives: `python -m pytest`, `bash setup.sh`, `node tools/xelib/x.js` (library writers are
  not opaque, spec §5).

- [ ] **Step 1:** Rows (RED). **Step 2:** Implement the denies. Implement S-OPAQUE behind a registry flag.
- [ ] **Step 3: Price** S-OPAQUE. Apply R15's thresholds. If they fail, delete S-OPAQUE and its rows, and
  record the Ruling with its numbers. **Until D-P4-2 lands**, parse-failure and opaque items are recorded
  as `partial` in `audit.fired` only.
- [ ] **Mutations:** `rule-S-INERT-NOCMD-whitespace`, `-nonstring`; if S-OPAQUE ships:
  `rule-S-OPAQUE-needs-protected-arg`, `-library-writers-excluded`.
- [ ] Commit: `"skyguard: blank or non-string commands fail closed; opaque-command note (if priced in)"`.

### Task 13: Message contract made strict; no-echo proven over every row

- [ ] **Step 1:** Empty `LEGACY_ALLOWED`. Every rule now carries a new template.
  `test_legacy_set_only_shrinks` becomes `test_no_legacy_messages`.
- [ ] **Step 2:** Add `test_no_message_echoes_its_command`. For every legacy **and** intended corpus row,
  fire it, then assert the returned text:
  - has ≤ 2 lines, each ≤ 200 characters;
  - shares no ≥ 24-character substring with the command, other than a basename.

  Add `test_message_fields_are_basenames` (long-path rows).
- [ ] **Step 3:** Update `tests/test_skyguard_shell.py`/`_write.py`, which still assert old constants.
  Update their tests only, never weakening a decision assertion.
- [ ] **Mutations:** `messages-no-echo` (put `{c}` back into one template → the no-echo test red);
  `messages-two-lines` (a 3-line template → red).
- [ ] Commit: `"skyguard: every message is ≤ 2 plain lines and never echoes the command"`.

### Task 14: Mutation-route inventory

**Files:** Create `docs/guard-coverage.md` and `tests/test_guard_coverage.py`.

- [ ] **Step 1:** Write the inventory. It is a table of every tool route that can change files or
  game state, and for each, the hook that sees it:
  - Claude `Bash`, `PowerShell`, `Edit`, `Write`, `MultiEdit`, `NotebookEdit`;
  - MCP `devbench`; any other MCP server (unguarded; stated);
  - Codex shell / `apply_patch` (Plan 4);
  - processes started outside the agent (Layer 2, Plan 6).

  For `MultiEdit` and `NotebookEdit`, **measure** whether matcher `Edit|Write` fires for them (a `claude -p`
  probe with the capture hook). Do not assume regex semantics.
- [ ] **Step 2:** The test parses the generated settings and the inventory table. It fails if a route
  marked "guarded" is not matched by any matcher, or if a matcher names a route the inventory lacks.
- [ ] Commit: `"docs+test: guard coverage inventory of every mutating route"`.

### Task 15: Absorb the Codex harness follow-ups

**Files:** `tests/conftest.py`, `.github/workflows/ci.yml`, `setup.sh`, `tools/esp-verify-wrapper.sh`,
`tests/test_esp_verify.py` (new or extended), `tests/test_setup_paths.py`.

- [ ] **Step 1 — tripwires canonical (handoff item 1):**
  - `conftest.py` exposes `harness_active()` (via `harness_isolation.active()`);
  - when `TOOLKIT_REQUIRE_HARNESS=1` and the barriers are not active, the session errors at
    `pytest_configure`;
  - CI's behavioral step becomes `python -B tests/run_harness_tests.py tests/ -v --durations=10` with
    `TOOLKIT_REQUIRE_HARNESS=1`;
  - a CI step fails if `-rs` output lists any `COULD NOT CHECK` skip other than the Docker daemon.
  
  RED twin: run with the variable set and no harness → error.
- [ ] **Step 2 — drop setup's Node auto-install:** `setup.sh:56-70`. It now prints how to install Node
  and does not run `winget`. Test: a `test_setup_paths` run with a tripwire `winget` on PATH and no
  `node` → setup succeeds, tripwire log empty.
  Mutation: `setup-no-node-autoinstall`.
- [ ] **Step 3 — ESP-verify false PASS (P13):**
  - `build_inventory` dies (exit 2) when serialize yields no YAML;
  - `verify` refuses PASS when the snapshot inventory **or** the after inventory is empty, unless
    `--allow-empty` is given (a plugin with genuinely zero cross-references), and says so in one line.

  RED test with a fake `SPRIGGIT` emitting nothing → today PASS, after: exit 2.
  Mutation: `esp-verify-empty-inventory-fails`.
- [ ] **Step 4 — MO2 helper precedence:** already done by Task 6's parity test. Here, document it in
  `docs/harness-integrations.md`.
- [ ] Commit per step (4 commits).

### Task 16: Side-effect hooks — measure only (R16)

- [ ] Count, in this install (read-only) and in a fresh toolkit install:
  - `.psc`/`.pex` files under `Data/Scripts/**` (top-level vs nested), total bytes, and the share older
    than 7 days;
  - the current per-call snapshot time.
  
  Count backups in `.claude/backups/` and how many have no recoverable original. Record it in the pricing
  doc under "Inputs for the side-effects plan", with a recommendation (manifest + incremental copy, or
  hardlink, or keep). **No behaviour change.** Commit: `"measurements: inputs for backup manifests and
  nested snapshots"`.

### Task 17: Cumulative pricing, docs, E2E, final review

- [ ] **Step 1:** `price.py --base P5-BASE` over the full history. Commit the cumulative table:
  - per rule, all six directions;
  - the old×new decision matrix;
  - **total asks** (target: ≈ 0, being only executed ReSaver `--apply` and real save overwrites);
  - **total advise**, against the P2 baseline of 2,028, as a percentage;
  - total denies, with the E16 class removed.
  
  Write the measurements doc in the Plan 2 equivalence format.
- [ ] **Step 2:** If D-P4-2 has landed, set `status="partial"` from the trace (one change and one test).
  Otherwise, ledger it as open.
- [ ] **Step 3:** Docs. Correct the "hard blocked" claims to what the engine enforces (E21; spec §5): the
  README and the Safety Rules in the agent-neutral core. **If Plan 3's instruction split has not
  merged**, defer this step and ledger it; do not edit a file Plan 3 owns mid-flight. Add a CHANGELOG
  `Unreleased` entry naming E12 and E21 as security fixes to the 3.x guards (D13), and the noise cut with
  its numbers.
- [ ] **Step 4:** Live E2E (`claude -p` in a scratch install copy, Git Bash and PowerShell tools). Each
  must be blocked or asked with the reason visible, and the heartbeats must prove the copy's hooks
  answered:
  - a `Set-Content` to a decoy `Data/Decoy.esp`;
  - a relative `rm -rf Data/x` from cwd = install (if R9 allows);
  - a `grep` containing "rm -rf" plus the install path → allowed;
  - the ReSaver `--apply` → ask, one line;
  - a DevBench `smp reset` via the mock MCP server → deny.
- [ ] **Step 5:** Latency rerun (p95 ≤ 1 s).
- [ ] **Step 6:** Final whole-range review on the most capable model, with this plan's Review Focus;
  then the fix pass, then the full suite through the harness, then merge `plan5` → master. No push.

## Spec coverage of Plan 5

| Item | Task |
|---|---|
| Install `4a429bd` port (first item) | 4 (re-based in 6) |
| Executed-program anchor + ≤ 2 lines, no echo, for every ask/advise | 1, 9, 13 |
| PowerShell forms (spec §5) | 5, 7, 8 |
| MO2 roots (E13) | 6, 7 |
| DevBench (spec §5) | 11 |
| E16 false-positive fix | 7 |
| E21 shell writes to plugins | 8 |
| `Bash\|PowerShell` matcher (E12) | 10 |
| Relative paths from payload `cwd` | 0 (M5a), 6, 7 |
| Dialect-aware parsers | 5 |
| Config parsed as data, malformed rejected visibly | 6 |
| Uncertainty advisory for opaque commands | 12 (conditional, R15) |
| Read-only false-positive controls | 7 |
| Analysis failures → auto-deny | 12 |
| Ask-rule triage | 4, 8, 11 (only ReSaver, copy-onto-plugin, save-overwrite ask) |
| MCP / mutation-route inventory | 14 |
| Codex follow-ups: tripwires, Node auto-install, ESP-verify, MO2 precedence | 15, 6 |
| Backup manifests, nested snapshot | 16 (measure), then deferred (R16) |

Deferred by design: the Codex adapter, the `.rules` generator and the verdict `status` field (Plan 4);
the read-only lock and shadow mode (Plan 6); the `skyrim-paths.env` move to the toolkit root (Plan 8/9);
backporting R7 and the message contract to the author's install (a separate decision).

## Controller rulings on the plan's open questions (2026-10-02)

- **R7 back-port:** done in the install (`protect-bash.sh`, heredoc bodies fed to `bash`/`sh` are kept). Task 4 ports the install's current rule, including this.
- **R16:** accepted. Backup manifests and nested-script snapshots are measured in Plan 5, implemented in Plan 6. The roadmap row moves with it.
- **R15, R14:** defaults accepted (opaque advisory only at ≤0.2% of calls and ≥50% useful; DevBench deny-list = `smp reset`).
- **Copy onto a plugin:** ADVISE, not ask, by default. The user's own ESP deploys into `Data/` are routine, and the user's standing directive is against approval fatigue. Upgrading it to an ask needs a priced case showing real misses.
- **Uncommitted wrapper/setup changes on master** were Plan 2's latency fix in progress. Plan 5 branches from master after that fix and Plan 2's final review land.
