# Skyrim modding — agent instructions

Guidance for AI coding agents working in this Skyrim install. Agent-specific notes are at the end of this file.

## What This Is

A modded Skyrim installation. Adapt paths and version-specific notes to your Skyrim version: SE, AE, VR, or LE. VR-specific behavior is flagged throughout — never assume SSE behavior equals VR behavior.

`setup.sh` detects your mod manager and fills in the Key Paths below accordingly. **If it detected Mod Organizer 2, read the MO2 note in Key Paths before reading or writing any mod file** — MO2 has no real `Data/` folder, so the obvious path is the wrong one. See "Mod Manager Layout" in `KNOWLEDGEBASE.md`.

## Key Paths

- **Game root**: `{{GAME_ROOT}}/`
- **User INI configs**: `{{CONFIG_DIR}}/` (Skyrim.ini, SkyrimVR.ini, SkyrimPrefs.ini)
- **Load order**: `{{LOADORDER_DIR}}/loadorder.txt` and `plugins.txt`
- **SKSE plugins**: `Data/SKSE/Plugins/`
- **Mod data**: `Data/` (ESPs, BSAs, meshes, textures, scripts)
{{MOD_MANAGER_PATHS}}

## Installed Modding Tools

All under `tools/`:

| Tool | Purpose | Usage |
|------|---------|-------|
| **Champollion** | Decompile Papyrus `.pex` → `.psc` | `tools/Champollion/Champollion.exe input.pex` |
| **Caprica** | Compile Papyrus `.psc` → `.pex` | `tools/Caprica/Caprica.exe` from `Data/Scripts/Source` with a BARE filename and a second `-i` for the base-game sources: exact command in skill `skyrim-context` |
| **XEditLib.dll** | Programmatic ESP/ESM reading via FFI | Load with koffi in Node.js (skill `xelib`) |
| **Spriggit** | ESP ↔ YAML/JSON conversion (.NET) | `spriggit serialize ...` |
| **AutoMod CLI** | NIF meshes, BSA archives, audio, MCM, ESP one-liners | `bash tools/automod-cli.sh <module> <command> --json` |
| **PyFFI** | NIF geometry edit — **NiTriShape (LE-format) ONLY** (any modern Python with setuptools) | PyFFI / PyNifly below; skill `skyrim-nif` |
| **PyNifly** | NIF read/write incl. **BSTriShape (SSE)** + **animation/controller authoring** (Python, prebuilt DLL) | PyFFI / PyNifly below; skill `skyrim-nif` |
| **ReSaver CLI** | Headless `.ess` save parse / query / cross-reference / clean | `bash tools/resaver-cli.sh <op> <save.ess>` — op list and rules: skill `skyrim-save` (write ops are dry-run unless `--apply`, always a NEW file). Needs JDK 17+ + ReSaver's jar (see `docs/installing-tools.md`). |
| **cosave-info** | READ-ONLY structural survey of an SKSE `.skse` co-save → JSON (which mods stashed co-save data + how much) | `bash tools/cosave-cli.sh <cosave.skse>` (Python 3; the cosave sits next to its `.ess`) |
| **skyrim-winner** | **"Which plugin actually wins this record?"** Loads the FULL active load order and asks xEdit. No index, no cache -- xEdit is the authority. | `bash tools/skyrim-winner.sh <winner\|chain> <formid> [plugin]` · `... conflicts <plugin>` -- needs **xeditlib**; skill `triage-and-conflicts` |
| **papyrus-triage** | Turns `Papyrus.N.log` into bucketed, attributed counts. Rows **sum to the entry count**; the not-shown row prints even at zero. | `python tools/papyrus-triage.py [logfile] [--top N] [--all]` -- bundled, Python 3, no install |
| **crash-triage** | Reduces CrashLogger dumps (~5.5k lines each) to ranked signatures and labels the ones your knowledgebase already ruled on. | `python tools/crash-triage.py [dir\|file] [--frames N] [--all]` -- bundled, Python 3, no install |
| **DevBench** | **LIVE in-game** inspect / console / Papyrus / scenario via a localhost REST+MCP server — only while the game is actually running | `bash tools/devbench-cli.sh <alive\|health\|ping\|state\|inspect\|exec\|call\|describe\|notify\|tool>` — skill `devbench` |

> **Note**: none of these are bundled. How to install and verify each one: `docs/installing-tools.md`.

## Where the rest lives

Skills are plain Markdown (`<name>/SKILL.md` in your agent's skills folder — see the end of this file);
any agent can read one directly. Read the skill BEFORE the work:

| When you are about to… | Read |
|---|---|
| use the running game (inspect, console, Papyrus calls, scenarios) | skill `devbench` |
| ask which plugin wins a record, triage Papyrus, crash or SKSE logs or a mod archive, or bulk-edit ESPs | skill `triage-and-conflicts` |
| write or run an xelib / XEditLib script | skill `xelib` |
| edit an ESP with Spriggit, or compile Papyrus | skill `skyrim-context` |
| touch a mesh (`.nif`) | skill `skyrim-nif` |
| update or add a mod | skill `mod-update-check` |
| publish a new version of your own mod | skill `mod-release-review` |
| work with archives, audio, MCM menus or saves | skills `skyrim-bsa`, `skyrim-audio`, `skyrim-mcm`, `skyrim-save` |
| install or verify an optional tool, or set up the Nexus API key | `docs/installing-tools.md` |
| use the Linux devcontainer | `docs/container-vs-windows.md` |
| understand why the guards behave as they do | `docs/safety-philosophy.md` |
| adapt an unsupported agent, or Python, Git or Node is missing or broken | `ADAPTING.md`, skill `toolkit-setup` |

## PyFFI / PyNifly (NIF geometry + animation authoring)

> **HARD LIMITS** — recipes, install notes and the validation gates: skill `skyrim-nif`.
> 1. PyFFI **cannot read BSTriShape** (any SSE-format NIF) — use PyNifly. PyFFI is for LE-format NiTriShape geometry edits.
> 2. **Never author animations with PyFFI** — its controller blocks pass its own readback and CTD the engine. Use PyNifly.
> 3. Never build from a fresh `NifFormat.Data()` (it corrupts the header string table); load an existing valid NIF and restructure it.
> 4. **Validate before any in-game test** with an independent parser (PyNifly), checking the specific crashable subsystem, and render a PNG (Blender headless) for a visual fix. Same-tool readback proves nothing.

## INI Config Hierarchy

Settings load in this order (later overrides earlier):
1. `Skyrim.ini` -- base settings
2. `SkyrimVR.ini` -- VR-specific overrides
3. `SkyrimPrefs.ini` -- user preferences (loaded last)

## Nexus Mod Research (Standing Rule)

**Always search a mod's Nexus mod page before investigating it.** Check the description, tutorials/articles, comments, and bug reports before going in blind. This saves enormous time -- most issues have been seen and documented by other users.

Nexus also has a free API (versions, changelogs, dependencies). `bash tools/nexus.sh mod <id>` wraps it; key setup and the personal-use rules are in `docs/installing-tools.md`. Never echo, log or commit the key.

## Knowledgebase

`KNOWLEDGEBASE.md` (project root) is the master reference for all discovered quirks, gotchas, and cross-version differences. **Always consult it before making changes** to avoid repeating past mistakes.

### Two files, and which one to WRITE to

| file | owner | shipped in the zip? |
|---|---|---|
| `KNOWLEDGEBASE.md` | the toolkit | **yes** — replaced on every update |
| `KNOWLEDGEBASE.local.md` | **you**: your facts AND your instructions | **never** — the release refuses to contain it |

**Standing instruction**: after every debugging session, mod investigation, or web
research, extract any new facts (engine quirks, VR vs SSE differences, API gotchas,
tool limitations) and append them to **`KNOWLEDGEBASE.local.md`**, creating it if it
does not exist. Read both files; write only to the local one.

### Your preferences: `## My preferences`, read every session

`KNOWLEDGEBASE.local.md` holds the user's facts AND their standing instructions. Its
`## My preferences` section is the user's instructions to you: **read it at the start of every
session and follow it**. On agents with session hooks the guards print it at session start; that is
the same text. Read the rest of the file before making changes. When the user states a lasting
preference ("always…", "never…", "from now on…"), add it to that section. When you create the
file, start it with an empty `## My preferences` section.

**Why the split, and it is not bookkeeping.** The upgrade path is "extract the new zip
over your install", so any file the toolkit ships is *replaced* on update. A file that
is both shipped by us and appended to by you loses everything you added, silently, at
the moment you update — and the README used to promise the opposite. A toolkit must
never ship a file it also asks you to edit.

⚠ If you already have accumulated notes inside `KNOWLEDGEBASE.md` from an older
version, move them to `KNOWLEDGEBASE.local.md` now. Anything left in the shipped file
is lost at the next update.

### The copy that covers the install you already have

At every session start the guards run `tools/kb-guard.sh`. It snapshots the knowledgebase files and
the generated instruction files into `.claude/backups/kb/<stamp>/` when they change, and alarms if one
vanishes, empties or loses more than half its bytes. No session hooks on your agent? Run
`bash tools/kb-guard.sh --verbose` yourself at the start of a session. ⚠ The snapshot is the
protection and the alarm is a convenience: an update that REPLACES a file with a bigger one raises
no alarm. Details: `docs/safety-philosophy.md`.

## Top Gotchas (Always In Context)

These are the most dangerous/common pitfalls. Consult `KNOWLEDGEBASE.md` for full details.

1. **RemoveSpell doesn't fire OnEffectFinish** -- use `DispelSpell` when cleanup logic exists (but `DispelSpell` excludes ability-type spells)
2. **All effects on a spell must have the same casting type** -- mismatches cause silent failure
3. **VMAD editing is fragile** -- use `GetFormFromFile()` on your OWN plugin's forms to minimize properties (never another mod's — see *Own Your Records*), and check the result for `None`; xEdit can't add scripts to VMAD
4. **PlayIdle fails in VR** -- VRIK overrides skeleton IK; bypass with timed Papyrus scripts
5. **Wait() unreliable under 100ms** -- merge sub-100ms gaps; use `RegisterForSingleUpdate` when possible
6. **SSE != VR** for: camera, skeleton, collision, UI, input, SKSE addresses, physics (60Hz->90Hz)
7. **ESL FormIDs must be in xx000800-xx000FFF** -- exceeding = crash or data corruption
8. **Loose files always override BSAs** -- check for loose file conflicts before assuming BSA content wins
9. **Condition OR has precedence over AND** -- `A AND B OR C` != what you'd expect
10. **Non-auto properties don't restore from master on save/load** -- they stay blank
11. **PreWEAPON/PreSHIELD skeleton nodes cause CTD in VR** -- must be removed
12. **ONAM required for ESM temp record overrides** -- missing ONAM = game silently ignores overrides
13. **SetVehicle causes HMD desync in VR** -- avoid entirely
14. **GoToState("") in OnUnload -> Self=None crash** -- move to OnLoad instead
15. **Navmesh creation is CK-only** -- xEdit can only delete, never recreate
16. **VR controller input is not on the SKSE Input API** -- use the VRIK API for trigger/grip/button detection
17. **Spell effect Area is in FEET, not game units** -- Area=60 ≈ 18m radius
18. **Papyrus Sound type maps to SOUN, not SNDR** -- passing a SNDR FormID silently returns None

## xelib Dry-Run Convention

All ESP modifications via xelib scripts must follow this two-pass workflow:
1. **Read-only pass**: load the ESP, log what would change (records added/modified/removed), print to console -- do NOT call `SaveFile()`
2. **User reviews** the proposed changes
3. **Write pass**: only after user approval, run again with `SaveFile()` enabled

This prevents accidental ESP corruption. The guards block direct ESP writes, but xelib runs as an ordinary script and can write through `SaveFile()`, which no guard sees.

## Spriggit ESP Workflow (Preferred for Editing)

For **creating or editing ESP records**, prefer Spriggit over xelib. Spriggit serializes ESP files to human-readable YAML that you can edit directly as text — no FFI, no scripting layer, and the YAML diffs cleanly in git.

**Serialize → edit the YAML → the user reviews it → deserialize**, always through `bash tools/spriggit-cli.sh` (raw
`spriggit` fails on nested paths). Spriggit **silently drops unknown YAML fields**: serialize with `-u`, round-trip
verify every new field, pass `--PackageVersion` as `spriggit --version` prints it. Commands and notes: skill `skyrim-context`.

### When to Use Which
- **Spriggit**: Creating new ESPs, editing existing records, any task where you're modifying specific fields.
- **AutoMod `esp`**: one-liner additions of simple records. AutoMod's `nif`/`archive`/`audio`/`mcm` modules are often the only tools for those formats (their skills: see *Where the rest lives*).
- **xeditlib**: Bulk inspection, programmatic traversal, diffing two ESPs, analysis scripts.

## ESP Dependency Rule: Own Your Records

**Never use `GetFormFromFile()` to borrow records from another mod.** Soft runtime dependencies silently break when that mod is disabled — the call returns `None` with zero error output and the feature simply doesn't work. This is nearly impossible to debug without reading the Papyrus log.

**Rule**: Any record your mod needs must live in the ESP you're building. Copy the record (SNDR, SPEL, MGEF, WEAP, etc.) into your own plugin rather than referencing another mod's.
- **Hard master dependencies** (MAST records): acceptable when overriding or extending a specific mod's content.
- **Soft `GetFormFromFile()` dependencies on other mods**: almost always avoidable. Copy the asset, create your own record.
- **Even vanilla records can be overridden by other plugins.** `Skyrim.esm` is always loaded, but any plugin later in the load order may change a vanilla record your mod references, silently changing your mod's behaviour. Copy any vanilla record whose specific behaviour your mod relies on (a magic effect's archetype, a sound descriptor, a spell) into your own plugin and reference your copy. Bare vanilla FormID references are fine only for markers/anchors (e.g. `XMarker`) and keywords used purely for matching.

## Safety Rules

**The guards** enforce these automatically on agents with hook support (your agent's notes at the end say
how). The rules live in the Python engine `tools/skyguard/`, which needs Python 3; a guard that cannot
start it REFUSES rather than allows. **A guard exists to stop the agent, not to interrupt the user**, so
the rules are graded and only the first list refuses.

### DENIED -- refused outright, cannot proceed

Each is judged on the **program that runs** and the file it targets, in Bash and PowerShell, not on words
that merely appear in a command (`grep -rn "rm -rf" <install>` is allowed). Relative paths resolve against
the working directory the agent reports.

- **Deleting inside the game install, the config directory, or Mod Organizer's `mods/` / `overwrite/`**
  (`rm`, `del`, `Remove-Item`, `find -delete`, `git clean`, `shutil.rmtree`, a Recycle-Bin delete, ...), or
  moving files out of them. Also refused: a delete whose target the guard cannot work out in a command
  naming a protected folder, and deleting everything in a folder it cannot place (`rm -rf *` after an
  unknown `cd`). Scratch folders (`/tmp`, temp, the scratchpad) are not protected.
  Pattern matching on parsed commands, **not a sandbox** (a relative path inside `python -c` code is not
  resolved): a guard against accidents.

  The install is **resolved** (from the guard's own location plus `skyrim-paths.env` at the toolkit root), never guessed
  from the word "Skyrim". ⚠ If no root resolves, or a path in `skyrim-paths.env` is unreadable, the guard DENIES.
  Why: `docs/safety-philosophy.md`.
- **Moving or renaming the install, `Data`, the settings folder or `Saves`** away.
- **Deleting Bethesda registry keys** (`reg delete` / `Remove-Item` on a Bethesda key or one of its parents).
- **Writing a plugin or archive (`.esp .esm .esl .bsa .ba2`) as raw text, anywhere** -- through the edit
  tools, and through the shell: `>` / `>>`, `tee`, `Set-Content`, `Out-File`, `sed -i`, `perl -i`, `dd of=`,
  `[IO.File]::Write*`, downloaders, and a name written in `python -c` / `node -e` code. Use xelib, Spriggit
  or AutoMod. Copying a finished plugin onto `Data/` is **advised**, not refused. Not covered: a name
  built at runtime, a script run from a file (`python fix.py`), other interpreters.
- A program's output flag (`-o`, `--output`) aimed at a `.psc`, and Champollion against a `.pex`
  inside `Data/Scripts/` -- both have destroyed sources
- DevBench `smp reset` (measured to crash a large load order), by MCP, `curl` or `devbench-cli.sh`
- Decoded text run as commands (`base64 -d | bash`), which the guard cannot read
- **More than two saves changed at once**, or saves named by a wildcard, until the user types the
  `SAVE_ACCESS_APPROVAL` line the refusal shows (skill `skyrim-save`)
- A command the guard cannot read: empty or not text ("GUARD INERT"), or unparseable while it contains
  a deletion and names a protected folder -- it refuses rather than shrugs

### ADVISED -- the call proceeds, I get a note, the user is never prompted
- Skyrim INI files (Skyrim.ini, SkyrimVR.ini, SkyrimPrefs.ini) -- last file wins
- SKSE plugin configs (`Data/SKSE/Plugins/*.ini`) -- not captured by any snapshot
- Papyrus scripts (`.psc`, `.pex`) -- a `.pex` loads at game startup ONLY
- Load order files -- the mod manager owns them and may overwrite a direct edit
- Redirects, `tee`, `sed -i` and other shell writes into `Data/`, the config folder or MO2 (a `cp`/`mv`
  there is silent), a copy onto a plugin where the game loads it, and any write, copy or move onto a
  file directly in the game folder (its exe and dlls, `openvr_api.dll`, loader INIs)
- A decompiler writing its output into the install; `sed -i` with a backslash path, on a file in the install
- AutoMod write commands without `--dry-run`
- Changing one or two saves (a save and its `.skse` are one): both are backed up, verified, first;
  a failed backup refuses

Naming a plugin or INI in a read-only command is silent: notes fire on the program that writes.

### ASKS THE USER -- three rules
- git that throws away uncommitted changes in a protected folder (`checkout -- x`, `restore`,
  `reset --hard`)
- `lock disable` (the file lock off for good), and `snapshot restore --in-place` or
  `restore-from-backup.sh --restore` (writes over live files)

Few prompts by design: a guard that prompts on routine work gets approved by reflex
(`docs/safety-philosophy.md`). **Check the guards are alive: `bash tools/hook-canary.sh`.**
Coverage and gaps: `docs/guard-coverage.md`.

### The file lock and the guards' own state
- **"Access denied" writing a vanilla master or archive** (`Skyrim.esm`, a base `.bsa`) is the optional lock, not
  a fault (the OS says no more): never retry or work around it. `bash tools/skyguard.sh lock unlock <file>` lifts
  it for an hour (the next tool call after that relocks it): **tell the user why first; relock when done**
  (`lock lock`). If it answers "No session id", you cannot lift it, by design: ask the user to run
  `tools\skyguard-unlock.cmd` in their own terminal. Never clear the read-only bit or ACL by hand.
- **Never edit `.skyguard/`** (guard on/off state): refused. `bash tools/skydoctor` shows what is live.

### General rules
- **Always review changes before applying** -- modded installs are delicate
- Never modify ESP/ESM files directly -- use xelib programmatically or Spriggit
- Vortex manages load order -- direct edits to loadorder.txt/plugins.txt may be overwritten
- **Never bare-copy a third-party mod into `Data/`**, SKSE DLLs (DevBench too) included: untracked
  files get clobbered or orphaned by a deploy or purge. Prefer the user's mod manager; a manual
  install goes through `tools/mod-install.py`: dry run first, read every destination, then `--apply`
  (backups + manifest) and check the result. (The user's **own** in-development mod files are fine.)
- **Delete to the Recycle Bin, never permanently.** Windows: `Microsoft.VisualBasic.FileIO.FileSystem`
  `DeleteFile`/`DeleteDirectory` with `SendToRecycleBin`, not `rm`/`Remove-Item`. A permanent delete
  needs the user's OK; a tool's own temp files are exempt. The guard judges a Recycle-Bin delete like any delete.
- **Never push CPU, RAM, free disk, disk I/O or GPU past 80% of the machine**, counting what already
  runs (games, other sessions). Measure first; if it won't fit, wait, shrink or ask. Stress tests: never
  one worker per core, idle priority, time-boxed.

### Safety improvement loop
After every session, near-miss or surprise, ask whether a new guard rule, protection or knowledgebase entry would have caught it -- proactively, when you notice a gap. Record proposals in a "Hook Candidates" section of `KNOWLEDGEBASE.local.md`.

### Audit trail
- On agents with hooks, files changed through the edit tools are backed up to `.claude/backups/` first. ⚠ A file a script writes is not backed up (see the knowledgebase note above).
- An audit log at `.claude/backups/AUDIT_LOG.txt` records every file touched, when, and by which tool

### Iteration snapshots (standing process — do not skip)
1. **Before any experimental change**, copy the current `.psc` files to `.claude/backups/<descriptive-name>/` (e.g. `known-good-pre-stagger/`): the rollback point.
2. **Automatic copies are not rollback points** -- you can't identify them later, so snapshot by name anyway. Spriggit backs up the ESP on deserialize, not scripts.
3. **After a state works in-game**, snapshot scripts and a Spriggit export to a `known-good/` folder named for what works.
4. **"Restore from transcript" is a trap** -- that code is entangled with the bugs fixed that turn.

## Confidence Levels (Mandatory)

**Before proposing ANY change** to game files, configs, scripts, or ESP records, you MUST:

1. **State a confidence level** (0-100%) for each proposed change
2. **List assumptions** that the confidence level depends on
3. **Investigate before acting**: Check the knowledgebase, read relevant source files, and web-search for Skyrim/VR-specific quirks before committing to an approach. Skyrim has many built-in bugs and version-specific differences -- things frequently do NOT work as expected.
4. **Target >= 90% confidence** before touching anything. If below 90%, document what's uncertain and what additional research would raise it.
5. **Never assume Skyrim SE behavior = Skyrim VR behavior.** Always verify VR-specific differences.

The confidence scale (95-100% verified, 80-94% strong, 60-79% research more, < 60% do not proceed) is in `docs/safety-philosophy.md`.

### Investigation checklist (before any change)
- [ ] Consulted `KNOWLEDGEBASE.md` for known quirks
- [ ] Read the actual source files involved
- [ ] Checked if VR differs from SSE for this feature
- [ ] Web-searched for known issues with this approach
- [ ] Considered rollback path if the change breaks something
- [ ] Evaluated whether this task reveals a gap in current guard coverage

---

## Core Modding Principle: Vanilla Game as Frame of Reference

**Before implementing any mechanic — even a novel one — identify how vanilla Skyrim handles the closest equivalent and model the solution on that pattern.**

Approaches disconnected from how the engine actually works lead to silent failures: Concentration stagger locks, script-cast stagger spells with no visible animation, ValueModifier Touch spells cast from static markers that don't deliver damage. The vanilla game is proof-of-concept. If vanilla doesn't do it that way, ask *why* before choosing your approach.

**Practical process:**
1. Identify the vanilla analogue (e.g. "bleed on weapon swing" → Bleeding Strikes / Bladesman perk)
2. Find the MGEF/spell/mechanism vanilla uses
3. Model your solution as closely as possible on that pattern
4. Only diverge when the vanilla pattern genuinely cannot be adapted

**Examples:**

| Mechanic | Wrong (invented) | Vanilla-aligned |
|----------|-----------------|-----------------|
| Bleed on hit | ValueModifier MGEF on a Concentration enchantment | FireAndForget bleed spell cast per-swing (mirrors the Bleeding Strikes perk's per-hit application) |
| Script stagger | Stagger-archetype FireAndForget spell via `Spell.Cast()` (no visible animation) | `PushActorAway(target, 1-3)` — physical stumble, proven engine behavior |
| Stagger immunity | `ForceActorValue("Stagger", 0)` polling | AddPerk with a ModIncomingStagger ×0 entry |
| Melee auto-fire prevention | Dispel in OnEffectStart | Strip the enchantment from the weapon form |

---

## Core Modding Principle: Native Engine Solutions First

**Before writing any Papyrus workaround, ask: "How does the game already handle this?"**

"Simple" in Skyrim modding means simple from the engine's perspective — not simple as code. A 3-line Papyrus hack that polls state every tick is NOT simpler than a 1-line `AddPerk()` call, because the perk is what the engine natively understands. Script-based polling fights the engine; native mechanisms work with it.

**Rule:** Use the engine's own systems first. Papyrus is a supplement, not a replacement.

| Problem | Script hack (wrong) | Native solution (right) |
|---------|---------------------|-------------------------|
| Stagger immunity | ForceActorValue polling | AddPerk with stagger resist entry |
| Keeping magicka up | RestoreAV in a loop | Enchantment with 0 magicka cost |
| Melee auto-fire prevention | Dispel in OnEffectStart | Strip enchantment from weapon form |
| Hit detection | Papyrus distance checks | Engine collision (projectile/melee) |
| Movement lock | Script position forcing | SetRestrained / Paralysis MGEF (note VR caveats — see KB) |

---

## Core Principle: Do Your Homework (Due Diligence Before Acting)

**Do the amount of due diligence you need such that the user has to do as little trial-and-error and manual verification as possible.**

- This does **NOT** mean cut corners, and does **NOT** mean skip steps where the user is genuinely needed (e.g. in-game VR testing only they can do).
- It means: verify formats, read the actual files, research the established technique (web + Nexus), confirm tool capabilities, and de-risk uncertain steps with a cheap spike — *then* make the change.
- On any in-game problem, test it yourself through DevBench before asking the user (skill `devbench`).
- **Catch CTD-class failures in tooling, not the headset** — cross-validate authored assets with an independent parser; don't trust same-tool readback.

Every in-game test cycle costs the user real time. Burn your own tokens on verification so theirs aren't wasted on avoidable trial-and-error.

---

## Core Working Principle: Cognitive Co-Pilot, Not Order-Taker

On every task, ask: **"what else is wrong here that nobody asked about?"** — and surface it. Don't just execute the stated request: find related issues, challenge assumptions, and suggest what the user hasn't thought of. The value here is **anticipation, not compliance**.

Treat the user's examples as a **sample, not the spec.** When they name a few instances, enumerate and probe the broader *class* yourself:
- Two failing FormIDs named → audit the whole record set, not just those two.
- One timing bug found → check every analogous call for the same defect.
- "X doesn't work" after a parameter tweak → question whether the **mechanism itself is wrong**, not just the number (e.g. a knockback that needs a *Knock Down* flag, not more Force).
