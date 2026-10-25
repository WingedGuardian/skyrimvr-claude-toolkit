# Skyrim AI Assistant Toolkit

For coding agents (Claude Code, Codex, …), not an AI NPC mod (SkyrimNet, Mantella, CHIM).

A modding workspace with engine knowledge, coding-agent instructions, safety hooks and wrappers for optional tools. Use it to inspect plugins, debug scripts, port mods and author meshes. Skyrim VR informed the workflow; check the version-specific guidance for SE, AE and LE.

Optional modding tools are installed separately. The toolkit configures paths and hooks through `setup.sh`; check the canary after real agent calls to confirm that the hooks ran.

[House Carl integration](docs/housecarl.md) adds reusable record queries, SkyPatcher/config diagnostics
and ordinary plugin authoring through a shared CLI/MCP gateway. Wrye Bash order and mod deployment
are configured separately. Existing-plugin edits use verified backups, isolated staging and independent
xEdit validation. The upstream server is an optional, separately installed dependency.

| Agent | Integration |
|---|---|
| Claude Code | Generated instructions, skills and hooks |
| Codex CLI on Windows | Generated instructions, skills and hooks; approval in `/hooks` required |
| Other coding agents | Instructions and tools; [ADAPTING.md](ADAPTING.md) describes how to add hooks |

The optional Windows file lock is off by default. Its limits and the hook coverage are described below. Earlier release announcements are in [CHANGELOG.md](CHANGELOG.md).

## New in v4: Codex support, one guard engine, and backed-up game saves

- **Codex CLI on Windows**, alongside Claude Code: the same instructions, skills and guards. Approve the hooks with
  `/hooks` (see *Using with Codex* below). Other agents can wire the guards into themselves with [ADAPTING.md](ADAPTING.md).
- **One guard engine in Python** (`tools/skyguard/`) behind both agents' hooks. A guard that cannot run refuses
  rather than allows; `bash tools/hook-canary.sh` and `bash tools/skydoctor` show what is live.
- **Game saves are backed up, not asked about:** a change to one or two of them first copies both files of each,
  verified and kept forever; more than two need the approval line you type.
- **An optional file lock** for the vanilla masters and archives (Windows, off by default), described below.
- **New tools:** `mod-install` and `archive-triage` for manual installs, `housekeep` for agent output, headless
  VR and `bench-vr` for DevBench, and the optional House Carl gateway (`docs/housecarl.md`).

Everything else, and how to upgrade from 3.x: [CHANGELOG.md](CHANGELOG.md).

## Added in v3: Create Custom Animated VFX — and See Them Before You Load the Game

v2 could read, edit, and build ESPs. **v3 adds the ability to author 3D mesh content and animation, and to verify it visually before it ever touches your headset.** This is the big leap, so it goes first:

- **Author animated NIFs from scratch.** Using PyNifly (the library behind BodySlide/Outfit Studio), Claude can write the animation-controller blocks that make a mesh *move on its own* — a self-spinning effect that auto-loops the instant it's placed in the world (a `SpecialIdle` animation sequence on a placed Activator, **zero Papyrus scripting required**), telescoping/extending geometry, or transform-keyframed motion. Hand-rolling these controller blocks is exactly the kind of thing that crashes the game when done wrong; the toolkit knows the correct way to write them.
- **Headless render-verification loop.** Claude can render a NIF straight to a PNG with headless Blender and **show you the result right in chat** — so a mesh or VFX fix is confirmed *before* you spend a game-load on it. NifSkope acts as an independent render gate and PyNifly as an independent parse gate, so a bad file gets caught in tooling, not in your headset. Author → validate → render-proof, all without a launch per iteration.
- **NIF geometry surgery.** Mesh splitting and subdivision, vertex and bounds edits, partial-mesh glow maps (a blade glows while the hilt doesn't), collision/Havok edits, and detection of VR-breaking skeleton nodes.
- **Full audio pipeline.** Extract, convert, and create Skyrim sound files (FUZ/XWM/WAV), and wire up the SNDR/SOUN record pair correctly.
- **Everything from v2.** ESP read/write/diff, Spriggit YAML editing, BSA archive CRUD, MCM menu generation, and save-file scanning are all still here.

### Things people have actually built with this

These are real, shipped results — proof the pipeline works end to end:

- An extending / telescoping blade effect (length-only, no fat-blade scaling)
- A self-spinning cone attack that loops on its own with no script driving it
- A partial-blade glow (emissive blade, non-glowing hilt) via a slot-2 glow map
- A custom weapon with a glow, a matching inventory icon, and a correct in-hand render in VR
- Diagnosing and fixing a broken combat-music setup at the record level
- Porting an SSE-only combat mod to VR end to end

### Things you *could* build — feasible, just not tried by us yet

Everything below is achievable with the exact toolchain that ships here. We haven't personally built each one, so treat them as starting points rather than guarantees — but the mechanism for each already exists in the box:

- Spinning runes, floating sigils, or orbiting ward effects (same self-looping recipe as the spinning cone)
- Summon or portal meshes with built-in motion; pulsing or "breathing" props; rotating gears and machinery for a dungeon
- Telescoping or morphing weapons — whips, extending spears, transforming blades
- Custom spell-effect meshes that animate themselves, with no per-tick Papyrus positioning
- Spinning loot beacons, animated banners, or other ambient set-dressing driven by transform controllers
- Re-skinning and glow-mapping an existing mesh set into a themed weapon/armor look
- Bespoke boss-fight VFX — formation rings, shockwave meshes — authored and render-checked headless before testing
- Bulk retexture or icon generation across all of a mod's assets

**If you can describe the motion, Claude can try to author the controller and show you a render.** That's the workflow.

---

## What You Get

This isn't a guide that tells you what to install and configure yourself. It's a **complete, ready-to-run modding environment** -- every tool pre-calibrated, every known Skyrim quirk already documented, every footgun already identified and protected against. Extract it into your game folder, paste one prompt, and you're working.

### A Toolset Built and Tested for Skyrim

Skyrim modding is full of undocumented engine quirks, version-specific differences, and tools that silently fail. This toolkit was built and tested on a live **Skyrim VR** install, but the knowledge, hooks, and scripts work across all versions — SE, AE, VR, and even LE where applicable. The knowledgebase includes version-specific sections so Claude knows what differs and what doesn't.

The clearest example: **xeditlib**. XEditLib.dll is the engine inside SSEEdit/xEdit -- the most powerful ESP editing tool in the Skyrim modding ecosystem. Getting it working from Node.js (so Claude Code could actually read and write ESP files) required cracking open the Delphi FFI layer and fixing a cascade of subtle bugs: strings encoded as UCS-2 instead of UTF-8, `InitXEdit()` silently corrupting the call stack when declared wrong, booleans that are actually 2-byte integers, a non-obvious two-step string-return pattern. None of this is documented anywhere. We debugged it, fixed it, and open-sourced the working wrapper as [xeditlib](https://github.com/WingedGuardian/xeditlib) so you never have to deal with any of it. Claude Code can now read any ESP file and write new ones -- something that wouldn't work at all before this toolkit.

### Everything Included

- **1,300+ lines of Skyrim modding knowledge** -- Papyrus quirks, version-specific differences, xEdit pitfalls, engine bugs, NIF/animation authoring, VR hit-detection, and more (including VR-specific sections). Loaded into every Claude session automatically.
- **NIF authoring & animation** -- Author self-animating meshes (self-spinning, telescoping, keyframed motion) with PyNifly, edit LE geometry with PyFFI, and verify everything with a headless render before you load the game.
- **Render-verification loop** -- Headless Blender renders any NIF to a PNG so fixes are confirmed in chat; NifSkope and PyNifly act as independent render/parse gates so bad files are caught in tooling.
- **Safety hooks** -- Claude is refused outright on the things that are never correct (writing a plugin or archive as raw text -- through the edit tools **and** through the shell -- deleting inside the game, config or Mod Organizer folders or moving files out of them, moving the install, `Data`, the settings folder or `Saves` away, deleting Bethesda registry keys, running decoded text as commands, DevBench's `smp reset`), and is warned -- without interrupting you -- on the things that are consequential but legitimate. Files it edits are backed up first, with a full audit trail. Only five rules ask *you* to decide: a git command that would throw away uncommitted changes in a protected folder, a `lock disable` that turns the vanilla-file lock off for good, an in-place snapshot restore (`snapshot restore --in-place`), which writes over live files, a program the guard cannot name (a variable such as `$R`) given one of your saves, and such a program -- or an `eval` of code a variable holds -- naming another protected path (the install, `Data`, the settings or MO2 folders, a plugin or archive). Any other change to your saves does not ask: one or two saves (a save and its co-save count as one) are backed up first, verified, and kept forever; more than two, or a wildcard, is refused until you type the `SAVE_ACCESS_APPROVAL` line that `python tools/agent-harness.py check --preview` prints for that exact command (the refusal points there). Run `bash tools/hook-canary.sh` any time to confirm the hooks are alive and actually receiving their input -- they once were not, and nothing looked wrong.
- **Confidence system** -- Claude rates its confidence (0-100%) and lists its assumptions before proposing any change. No guessing, no "this should work."
- **ESP editing via Spriggit** -- Serialize any ESP to human-readable YAML, edit it directly, deserialize back. Claude's native file editing works on YAML out of the box — no FFI layer, no scripting, and changes diff cleanly in git.
- **ESP analysis via xeditlib** -- Programmatic inspection, diffing, and bulk queries across records. The hard Delphi FFI work is already done. ([xeditlib on GitHub](https://github.com/WingedGuardian/xeditlib))
- **Cross-reference integrity guard** -- A tool-agnostic wrapper snapshots an ESP's references before a risky bulk edit and loudly fails afterward if any reference was silently re-mastered or dropped.
- **NIF mesh tools** -- Inspect, retexture, scale, fix eye-ghosting, split/subdivide geometry, apply glow maps, and verify mesh files. Detect VR-breaking skeleton nodes.
- **BSA archive tools** -- Full read/write/merge/diff on BSA archives. Extract individual files, create new archives, update contents.
- **Audio processing** -- Extract, convert, and create Skyrim voice and sound files (FUZ/XWM/WAV).
- **MCM menu generation** -- Programmatically create SkyUI mod configuration menus with toggles, sliders, and pages.
- **Save file analysis** -- Decompress and binary-scan .ess saves. Search for orphaned scripts, count effect accumulation, check mod footprint, detect save bloat.
- **Conflict resolution answered by xEdit, not guessed** -- `skyrim-winner` loads your **full** active load order and asks xEdit which plugin actually wins a record, prints the whole override chain, or lists every record a plugin loses. No index and no cache, so there is nothing to go stale.
- **Log triage that cannot quietly drop rows** -- `papyrus-triage` buckets and attributes a Papyrus log; `crash-triage` reduces ~5,500-line CrashLogger dumps to ranked signatures and labels the ones your knowledgebase already ruled on. Both print the denominator first, sum their rows against independently counted totals, and print the not-shown row even when it is zero -- and both are mutation-tested, so a check that stopped biting fails CI.
- **Dry-run workflow** -- All ESP and asset changes go through a preview pass first. Claude shows you exactly what it will do before touching anything.
- **Claude Code skills** -- Slash commands like `/inspect-esp MyMod.esp`, `/port-to-vr`, and `/create-mod` that trigger guided workflows. Auto-loading context that injects critical Skyrim gotchas when Claude works with game files.
- **Requirements** -- **Python 3.10+ is required** for the safety hooks and offline shared harness (tested on 3.10–3.13; without a suitable Python they refuse commands until it is installed and `setup.sh` is re-run). `jq` is optional. xeditlib needs Node.js; the OpenCode JavaScript transport has separate [setup and qualification notes](.opencode/SKYRIM-OPENCODE.md), and its standalone transport tests use Node.
- **Auto-setup** -- One prompt installs prerequisites, configures paths, sets up hooks, and optionally installs modding tools. No manual configuration.

### Optional tools (install as needed)

None of these are bundled; setup walks you through any you pick.

- **xeditlib** -- programmatic ESP read/write (pinned to v1.0.2 = commit `06f73ea` with koffi 2.15.1 in `package-lock.json`, integrity-checked; from the toolkit root: `npm ci --ignore-scripts --no-audit --no-fund`)
- **Champollion / Caprica** -- Papyrus decompile / compile (Champollion v1.3.2 / Caprica v0.3.0 from their GitHub releases; check each SHA-256 against docs/installing-tools.md *Pinned downloads*)
- **Spriggit** -- ESP ↔ YAML editing (`dotnet tool install Spriggit.CLI --version 0.41.0` -- **always pinned** to the tested version, not older and not newer; `tools/toolchain-check.sh` flags a version that differs from the pin); **requires the .NET 10 SDK**, not just the runtime (the SDK it was tested with; a manual step, for you to run -- an agent may not install this: `winget install --id Microsoft.DotNet.SDK.10 --exact --version 10.0.401 --no-upgrade`). Spriggit and AutoMod both run on that SDK. The optional House Carl gateway additionally needs .NET Runtime 9 and ASP.NET Core Runtime 9 on Windows; see [its prerequisites](docs/housecarl.md). Spriggit fetches its serializer at runtime as a dotnet tool, and only a matching SDK can install one — with the runtime alone it starts, prints a version, and still cannot serialize. That also disables the cross-reference integrity guard, which drives it.
- **AutoMod CLI** -- NIF / BSA / audio / MCM / ESP. **Must be cloned and built**: clone https://github.com/SpookyPirate/spookys-automod-toolkit at tag `v1.11.2` into `tools/automod`, then `bash tools/automod-cli.sh --rebuild` (retargets it to .NET 10 and builds the Cli project only; the first build needs internet) — then run via `tools/automod-cli.sh`. Same .NET 10 SDK as Spriggit; no .NET 8.
- **PyFFI** -- LE-format NIF geometry (PyFFI 2.2.3 + setuptools 84.0.0, hash-locked: `python -m pip install --only-binary=:all: --require-hashes -r tools/requirements-tools.lock`)
- **PyNifly** -- SSE BSTriShape + animation authoring (download `io_scene_nifly.zip` of release **V27.2.0** and check its SHA-256 against docs/installing-tools.md *Pinned downloads*; a release zip, not a repo clone — the compiled DLL ships only in the release zip; no build)
- **Blender (headless) / NifSkope** -- NIF mesh repair + render verification (large external apps; Blender 4.2.9 LTS and NifSkope v2.0.dev9, hashes in docs/installing-tools.md *Pinned downloads*)
- **ReSaver CLI** -- headless `.ess` save parse / cross-reference / clean / changeform-level diagnostics (ReSaver **6.0.643** from **Nexus mod 5031** / FallrimTools into `tools/resaver-cli/`, `ReSaver.jar` and `lib/` checked against docs/installing-tools.md *Pinned downloads* by `bash tools/resaver-cli.sh verify`; requires **JDK 17+**, JDK 21 LTS recommended). Optional on Windows: **ReSaver Plus**, a native backend for the same operations that `python tools/resaver-plus/build.py` builds locally from a pinned upstream commit plus the toolkit's patches (no binary ships; needs Visual Studio 2022 Build Tools); Java stays the default, opt in with `RESAVER_BACKEND=native` (`tools/resaver-plus/README.md`)
- **DevBench** -- LIVE in-game inspect / console / Papyrus while the game runs (**Nexus mod 181326** / alandtse; GPL-3.0, dev-only, no gameplay change and no save data). Unlike everything else here it's a **mod, not a `tools/` utility** -- install it with your own mod manager like any other SKSE plugin, rather than letting Claude drop the DLL into `Data/`. The toolkit bundles the wrapper `tools/devbench-cli.sh`, which works as soon as DevBench is installed.
- **papyrus-triage / crash-triage** -- Papyrus log and CrashLogger dump triage. No install beyond Python 3; bundled (`tools/papyrus-triage.py`, `tools/crash-triage.py`).
- **logging-registry** -- Read-only discovery of logging/debug controls, configured values and explicit coverage gaps. Search and compare saved inventories; [guide](docs/logging-registry.md), skill `logging-registry`.
- **skse-load-status** -- what SKSE did with each plugin DLL at the last launch (loaded, incompatible, not an SKSE plugin) and the version each reports about itself, from `sksevr.log` / `skse64.log`. No install beyond Python 3; bundled (`tools/skse-load-status.py`).
- **archive-triage** -- what a mod archive would change before you install it: each file NEW / SAME / CHANGED against what is installed, every destination shown and any outside `Data/` flagged; read-only. Bundled (`tools/archive-triage.py`); needs **7-Zip 26.00** (pinned in docs/installing-tools.md).
- **mirror-launch-logs** -- one-shot, append-only copy of the SKSE, crash and Papyrus logs into `.claude/backups/log-mirror/`, so the next launch cannot rotate the evidence away; a replaced log keeps its previous copy. No install beyond Python 3; bundled (`tools/mirror-launch-logs.py`).
- **housekeep** -- disk retention for agent output (`docs/RETENTION.md`): report, plan, then apply exactly the plan. Copies of live game files and build intermediates go to the Recycle Bin, duplicates become read-only hard links, launch logs are compressed then aged out; saves, backup folders, git-tracked files, doc-linked evidence and active task folders are never touched. Session start also sweeps the toolkit's own stale temp dirs and the guard's settled `.claude/retired/` batches to the Recycle Bin in the background (never pytest's shared folder; `SKYGUARD_HOUSEKEEP_SWEEP=off` turns it off) and says when disk needs your attention. No install beyond Python 3; bundled (`tools/housekeep.py`).
- **mod-install** -- a manual mod install that is a dry run unless `--apply`: every destination shown, every replaced file backed up, a SHA-256 manifest to undo it, existing configs kept with the new copy beside them as `.new`, hardlink-safe writes, plugins never activated; installs into a new mod folder under Mod Organizer 2 (`--update` replaces an existing one after keeping the old folder whole). Bundled (`tools/mod-install.py`); needs **7-Zip 26.00**.
- **nexus.sh `find` / `page` / `contents`** -- the desk review before an update (skill `mod-update-check`): name search, the page with author-declared requirements and every changelog since your installed version (sorted by version, not as text), and file contents from Nexus previews where Nexus serves them to the tool. Failures print `RESULT: COULD-NOT-CHECK` (exit 3), never a blank answer. Bundled (`tools/nexus.sh`, `tools/nexus-render.py`); needs your Nexus key.
- **bench-vr** -- frame rate at fixed locations through DevBench, before and after a change, with `compare`; it pins timescale, hour, weather and god mode, discards each location's first window, and puts back god mode, timescale and hour even on failure. The forced weather and the player's location are left as they are, and every run says so (`LEFT AS IS (by design)`, with the start and end cell): reload your save to return. Read-only otherwise. Bundled (`tools/bench-vr.py`); needs DevBench and a running game.
- **vr-headless** (Skyrim VR only) -- boots the game on SteamVR's null driver with no headset, so DevBench tests that do not depend on head or hand position can run unattended; `off` restores `openvr_api.dll` and `steamvr.vrsettings` byte-exact and verifies them; a file that changed after `on` is first copied, verified, into the run's `changed-after-on-<stamp>/` folder, and the state file is kept as `state-closed-<stamp>.json`. `run --desktop background|current` owns a whole session and restores in `finally`. An optional pose-emulating driver (the official SkyrimVR Devkit v2.0.1) is downloaded on your machine from its pinned release commit and hash-verified (`tools/vr-emu-driver/README.md`); the toolkit ships no copy. Bundled (`tools/vr-headless.sh`).
- **skyrim-winner** -- which plugin wins a record, per the full load order. Bundled (`tools/skyrim-winner.sh`) but needs **xeditlib** installed to do anything.
- **cosave-info** -- read-only structural survey of an SKSE `.skse` co-save → JSON (which mods stashed co-save data + how much). No install beyond Python 3; bundled (`tools/cosave-cli.sh`).

---

## Setup (4 Steps)

### Step 1: Install a supported coding agent

Follow the official installation guide for [Claude Code](https://code.claude.com/docs/en/setup) or [Codex CLI](https://developers.openai.com/codex/cli/). Codex hook support here is for the Windows CLI; the desktop app and IDE extensions are not claimed.

The toolkit's tested Python range is **3.10–3.13**. The hooks run under Git Bash, tested with Git for Windows **2.55.0.5** (Git 2.55 needs this toolkit version or later: see [Installing tools](docs/installing-tools.md#git-for-windows)). Optional xelib tools use **Node 24.19.0**; Spriggit and AutoMod use the **.NET 10 SDK**. [Installing tools](docs/installing-tools.md) lists the exact tested versions and verification steps. The setup prompt below does not authorize host-software installation.

   Install Node.js **24.19.0**, the version this toolkit was tested with. For you to run, in Windows Terminal:
   `winget install --id OpenJS.NodeJS.LTS --exact --version 24.19.0 --no-upgrade`

DevBench is an optional SKSE mod installed through your mod manager. Its documented minimum is **1.11.0**;
the newest release the docs were read against is **1.28.0** (2026-10-11, the recorded API/docs reference).
It has been read against the upstream changelog and tool descriptions, not runtime-tested here. One behaviour
changed in 1.25.0: a Papyrus `call` with an omitted optional argument is refused (400) unless the argument is passed or
`fillNeutral:true` is sent. The VR Address Library metadata reference is
**newest seen: 0.276.1** (2026-10-08). These sightings do not change pins or recommend an upgrade;
follow [installing-tools.md](docs/installing-tools.md) for verification and installation guidance.

### Step 2: Extract This Toolkit Into Your Skyrim Folder

1. Download this mod from Nexus (Manual Download)
2. Find your Skyrim folder:
   - Open **Steam** > **Library** > right-click **Skyrim VR** (or **Skyrim SE**) > **Properties** > **Installed Files** > **Browse**
   - A folder opens -- this is your Skyrim folder
3. **Extract the zip directly into that folder**
   - Right-click the downloaded zip > Extract All > paste your Skyrim folder path > Extract
   - The files blend in alongside your existing game files. ⚠ **Files the toolkit ships ARE replaced** -- on a fresh install that costs you nothing, but on an UPDATE it replaces `CLAUDE.md`, `KNOWLEDGEBASE.md` and the hooks. Keep your own notes in `KNOWLEDGEBASE.local.md`, which is never shipped.

### Step 3: Open your agent in the Skyrim folder

Open a terminal in the folder containing your game's executable, then start `claude` or `codex`. For Codex, follow the startup and live-hook checks in **Using with Codex** below.

Setup accepts `bash setup.sh --agent claude|codex|all|auto`. Choose one value; `auto` is the default and detects agents on PATH, and `both` aliases `all`. An unselected agent keeps its files but is reported NOT CONFIGURED. The optional file lock is off unless you select `--lock`.

After setup, Codex needs you to run `/hooks` and approve the toolkit's two hooks. Make real tool calls, then run `bash tools/hook-canary.sh` to check their invocation records. Unrendered templates in a source checkout are NOT CONFIGURED; approval alone does not render them.

### Step 4: Paste This Prompt

Copy this entire prompt and paste it into your coding agent (Claude Code, Codex, ...) (it's also saved as `SETUP_PROMPT.txt` in your Skyrim folder):

```
I just installed the Skyrim modding toolkit into this folder. Read AGENTS.md (Claude Code reads CLAUDE.md, which says the same), then read the skill toolkit-setup (.agents/skills/toolkit-setup/SKILL.md; Claude Code: .claude/skills/toolkit-setup/SKILL.md) and follow it: it inventories Python, Git and Node by launching them, then you run "bash setup.sh". Host-software installs: NOT AUTHORIZED. That means: do not install, upgrade, repair or remove Python, Git, Node or any other system software (a JDK, .NET, Blender) on my computer; if something is missing or broken, tell me the exact manual steps and wait for me. (To authorize installing Python, Git or Node I will change that sentence myself, to this form: Host-software installs: AUTHORIZED python=<winget id>==<exact version>. The only winget ids it can name are Python.Python.3.<minor> (3.10 or newer) for python, Git.Git for git, and OpenJS.NodeJS or OpenJS.NodeJS.LTS for node, each at the exact version I write, installed with --no-upgrade. That sentence never covers a JDK, .NET or Blender: for those, and for anything else, you only give me manual steps.) If you are an agent other than Claude Code or Codex, read ADAPTING.md after setup and tell me honestly which protection layers you can provide and which you cannot. After setup, ask me which optional modding tools I'd like (xeditlib, Champollion, Caprica, Spriggit, AutoMod CLI, PyFFI, PyNifly, Blender, NifSkope, ReSaver CLI, ReSaver Plus) and set up the ones I pick, only inside this folder (git clones or downloads into tools/), each at exactly the version docs/installing-tools.md names, checking every download's SHA-256 against its Pinned downloads table -- never a newer or older version, and stop and tell me if a hash does not match. AutoMod CLI adds NIF mesh editing, BSA archive tools, audio processing, and MCM menu generation -- it is a real tool you install by cloning https://github.com/SpookyPirate/spookys-automod-toolkit at tag v1.11.2 (commit e1c34ea, the version this toolkit tested) into tools/automod and then running bash tools/automod-cli.sh --rebuild, which checks out that commit, retargets it to the .NET 10 SDK and builds only the Cli project (never build it with a bare dotnet build; the first build needs internet), after which it runs via tools/automod-cli.sh. PyFFI + PyNifly add NIF geometry and animation/controller authoring; Blender (headless) + NifSkope add mesh repair and render-verification of meshes before in-game testing. ReSaver CLI adds headless .ess save parsing, cross-referencing, cleaning, and changeform-level diagnostics (ReSaver 6.0.643 from Nexus mod 5031 into tools/resaver-cli/, its ReSaver.jar and lib/ checked against Pinned downloads with bash tools/resaver-cli.sh verify; needs JDK 17+ (JDK 21 LTS recommended; a JDK is system software: never install it yourself, give me the manual steps)). ReSaver Plus is an optional second, native ReSaver backend for Windows: python tools/resaver-plus/build.py builds it here from the pinned upstream commit plus the shipped patches and installs it only if its six tests pass; it needs Visual Studio 2022 Build Tools with Desktop development with C++ (system software: never install it yourself, give me the manual steps); the Java ReSaver CLI stays the default and I opt in with RESAVER_BACKEND=native. Separately, TELL me about DevBench but do NOT install it: it is an optional dev-only SKSE plugin (Nexus mod 181326 by alandtse, no gameplay change and no save data) that runs a localhost server inside the running game, letting you inspect live state, run console commands and read their output, and call Papyrus functions -- so you can test your own fixes while I keep playing, instead of asking me to launch, trigger, and report back. Unlike everything else in that list it is a mod, not a dev tool, so if I want it I install it through my own mod manager like any other SKSE plugin -- never copy files into Data/ yourself. The wrapper tools/devbench-cli.sh is already bundled and works as soon as it's installed. The bundled cosave-info tool (tools/cosave-cli.sh, Python 3, no download) gives a read-only structural survey of an SKSE .skse co-save. Be sure to tailor the environment specifically to my Skyrim version and install (may or may not be VR). Also ask me whether I want to enable the optional Nexus API integration (mod update-detection / version & changelog checks); if yes, explain how to get a free Personal API Key and save it to tools/.nexus_api_key (which is gitignored). Explain everything in plain English and ask me any questions you may need to.
```

Your agent handles the rest. It checks which of Python, Git and Node actually start, configures paths, sets up the safety hooks, and walks you through the optional tools (installed only inside this folder). It does not install Python, Git, Node or any other system software: if one is missing it tells you the manual steps, unless you change the authorization sentence in the prompt yourself. Just answer any questions it asks.

> **Note on the NIF render loop:** The headless render-verification step uses Blender (and, optionally, NifSkope for an independent visual check). These are large external GUI apps, so — like Champollion/Caprica — they aren't bundled in the zip; the setup will point you to install them and wire up the addon. PyFFI and PyNifly themselves are lightweight and handled by setup.

Check the canary before relying on the guards. Install optional tools only as needed.

---

## Optional: Nexus API Integration

The toolkit can use the free **Nexus Mods API** so Claude can check mod **versions, update dates, changelogs, file info, and dependencies** programmatically — handy for *"did any of my mods update?"*, migration triage, and pre-investigation research. It's entirely optional; everything else works without it.

**To enable it:**
1. Get a free **Personal API Key**: nexusmods.com → **Site preferences → API Access** ([direct link](https://www.nexusmods.com/users/myaccount?tab=api)) → copy your Personal API Key.
2. Save it (one line, nothing else) to **`tools/.nexus_api_key`**, or set the **`NEXUS_API_KEY`** environment variable. (Or just paste it to Claude during setup and it'll write the file for you.)
3. Done — `tools/nexus.sh` and Claude pick it up automatically (file first, then the env var). Quick check: `bash tools/nexus.sh mod 176043` prints that mod's name/version/last-updated.

> **Security:** your key is **personal/local use only**. The file is **gitignored by default** so it's never committed — never share it, paste it publicly, or log it. If it's ever exposed, revoke/regenerate it on the same API Access page.

---

## Using It

Start your configured agent in the Skyrim folder. It reads the toolkit instructions and knowledgebase; approved, configured hooks provide the safety checks described below. Optional tools must be installed before their wrappers can run.

This toolkit fits best as a **power user tool** — particularly strong for investigating, porting, and debugging existing mods, making targeted record edits, scripting assistance, and now authoring custom meshes and animated VFX. For simpler mods (spells, powers, item records, short scripts), Claude can build these from scratch. For complex systems, expect some iteration.

---

**Creating custom meshes and animated VFX (new in v3):**
- *"Author a NIF for a rune that slowly spins on its own when I place it in the world, and show me a render"*
- *"Take this sword mesh and make the blade telescope out to twice its length, length-only, no fat-blade scaling"*
- *"Give this blade an emissive glow on the steel but not the hilt, then render it so I can check"*
- *"Build a custom weapon with a glowing mesh, a matching inventory icon, and a correct in-hand render in VR"*
- *"Render this NIF to a PNG so I can see if your last edit actually fixed the hole in the mesh"*

**Porting mods across Skyrim versions (SSE to VR, Oldrim to SSE, SSE to AE, etc.):**
- *"This mod was made for SSE. Examine every VR incompatibility and fix each one"*
- *"Decompile this script and tell me what breaks in VR and how to fix it"*
- *"This mod uses PlayIdle() on the player -- that doesn't work in VR. Rewrite it to use timed Papyrus instead"*
- *"Port this SSE combat script to VR -- check the knowledgebase for anything that behaves differently"*

**Debugging and troubleshooting:**
- *"I'm getting a CTD when I equip this weapon in VR. Can you fix it?"*
- *"NPC dialogue stopped showing up after I installed a mod. Help me debug it."*
- *"Check my SkyrimVR.ini for settings that might cause problems"*
- *"Decompile Data/Scripts/MyScript.pex and explain how it works"*
- *"These two mods both touch the same magic effect -- which one wins?"*

**Investigating and understanding mods:**
- *"Inspect all the records in Data/MyMod.esp and explain what this mod actually does under the hood"*
- *"What does the mod at nexusmods.com/skyrimspecialedition/mods/12345 do? Are there any known VR issues?"*
- *"Compare the original and my patched version of this ESP and show me exactly what changed"*

**Building new mods from scratch (simpler ones work best):**
- *"Build me a power that lets me slow time for 10 seconds with a 60-second cooldown"*
- *"Create an ESP that adds a new two-handed katana with custom reach and a fire enchantment"*
- *"Write a Papyrus script that tracks how many enemies I've killed and shows a notification every 10 kills"*
- *"Make a spell that blinds nearby enemies for 5 seconds using a custom magic effect"*
- *"I want a Lesser Power that equips my best sword and shield automatically when I enter combat"*

**Whatever else you want:**
- *"Add a FOMOD installer to my mod"*
- *"Write a MCM menu config for my mod using SkyUI VR"*
- *"Scan my load order for mods known to break in VR"*
- *"My mod works in SSE but crashes in VR on startup -- let's figure out why"*

If it involves Skyrim, Papyrus, ESPs, INI files, scripts, meshes, or mod files of any kind, just ask. Claude has the full context of how the engine works and will figure out the path forward. It's significantly faster than doing it yourself — especially for the tedious parts.

---

## What's in the Knowledgebase?

Other AI modding setups make you feed Claude information manually or re-explain the same quirks every session. This toolkit ships with a pre-loaded `KNOWLEDGEBASE.md` -- 1,300+ lines of documented knowledge that Claude reads automatically at the start of every session:

| Topic | What's Covered |
|-------|---------------|
| **Papyrus Scripting** | Script lifecycle, threading, RemoveSpell vs DispelSpell, Wait() reliability, magic effects, performance pitfalls |
| **Version-Specific Differences** | SKSE versions, skeleton issues, camera, physics, UI, input, mod framework compatibility (with VR-specific sections) |
| **xEdit / ESP Editing** | VMAD fragility, plugin types (ESM/ESP/ESL), load order, BSA priority, navmesh, cleaning caveats, Spriggit pitfalls |
| **Engine Quirks** | Ability spells, vanilla bugs, SKSE plugin compatibility warnings |
| **VR Controller Input** | SKSE Input API limitations in VR, VRIK API as the correct method, code examples (VR section) |
| **NIF & Animation Authoring** | PyFFI vs PyNifly limits, authoring self-animating meshes without CTDs, render/parse validation gates, geometry split/subdivide, glow maps |
| **VR Hit Detection** | The HIGGS / WeaponCollision / PLANCK / vanilla stack, the engine melee-range cap, Havok game-unit conversions |
| **Weapon & VFX at Runtime** | What can and can't be changed on an equipped weapon, instant scaling, projectile/collision routing |
| **Audio** | SOUN vs SNDR record pairing, the WAV→XWM pipeline, OutputModel/Category gotchas |
| **Debugging** | Debug.Notification limitations, Debug.Trace patterns, concurrent script handling |
| **Save File Analysis** | .ess format (LZ4 decompression), binary search for FormIDs/strings, plugin list extraction, orphaned script detection |

All of this is pre-loaded and ready to go. And it grows over time -- Claude adds new discoveries as you work together, so the environment gets smarter the more you use it.

---

## Safety Features

After setup and any required hook approval, the supported agents run these checks. They are pattern-based protections against accidents; verify invocation with the canary.

| Protection | What It Does |
|-----------|-------------|
| **Command guard** | Blocks deleting inside the install, the config folder or Mod Organizer's `mods`/`overwrite` (a Recycle-Bin delete included), deleting everything in a folder it cannot place, moving the install, `Data`, the settings folder or `Saves` away, running decoded text (`base64 -d` piped to a shell), deleting Bethesda registry keys, raw-text shell writes to ESP/ESM/ESL/BSA/BA2 (`>`, `tee`, `Set-Content`, `Out-File`, ...), `sed -i` without `-b` on a file in the install, config or MO2 folder (it rewrites CRLF line endings to LF; `sed -i -b` is fine), and DevBench `smp reset`. Runs on the Bash and PowerShell tools. **Advises** on other file operations in game directories -- a note to Claude, not a prompt to you. |
| **File guard** | Blocks direct writes to ESP/ESM/ESL/BSA/BA2 files through the edit tools. **Advises** on INIs, load-order files and scripts. A write onto one or two saves in `Saves/` is backed up first, then runs; more than two need your typed approval. |
| **Auto-backup** | Copies a file to `.claude/backups/` before the edit tools (or Codex `apply_patch`) change it, with full audit log. A file a script writes through the shell is not backed up. |
| **Confidence system** | Claude must rate confidence 0-100% and list assumptions before any change. |
| **Investigation-first** | Claude checks the knowledgebase and web-searches before touching anything. |
| **Validate-before-test** | Authored NIFs are cross-checked with an independent parser and a headless render before they're handed off for in-game testing -- crashes get caught in tooling, not your headset. |

### Optional: lock the vanilla files (Layer 2, off by default, Windows)

The hooks only work while the agent runs them: Codex skips unapproved hooks silently, a group policy or WSL can
stop them starting, and an unknown agent may have none. Layer 2 is a protection that sits **below** the agent, in
the file system, so it holds in those cases too. It covers the game's own files only (the vanilla masters and
archives), never your mods.

**Mechanism.** `bash tools/skyguard.sh lock lock` sets the read-only attribute on each target **and** adds a
file-level deny entry for your own account (write data, append data, write attributes, write extended attributes;
nothing else, and never delete or read). The deny on write-attributes is what stops `-Force` and `cp -f` from
clearing the attribute. The targets are an **explicit per-edition list of names** (a pattern would also lock
mod files that look vanilla), and only files that exist, are regular and have a single hard link: a copy a mod
manager hard-linked is reported and skipped, never locked. It is applied in-process (no process per file) and
needs no administrator rights (apply plus remove over the 26 names of the spike's full VR set, which the shipped list has grown to 33, took at most 0.021 s, over
5 runs, MEASURED on the Phase A spike's in-process code `fastlock.py`, not on the shipped `lock.py`;
`docs/evidence/2026-10-04-plan6-ml2.md`, C10). **It is off by default**: nothing is locked until you
run it.

**What it stops, MEASURED** (2026-10-04, on scratch copies of real masters under `C:/Temp`, one Windows 11
machine, one unelevated user; every row ran against the locked file **and** an unlocked twin that had to change,
or the row was discarded as unmeasured):

| Route | Result with the lock |
|---|---|
| Overwrite primitives, 38 rows: Git Bash (`>`, `>>`, `cp`, `cp -f`, `tee`, `dd`, `sed -i`, `mv` over: 8), PowerShell 7 and Windows PowerShell 5.1 (`Set-Content`, `Out-File`, `Add-Content`, `Copy-Item`, `Move-Item`, .NET `WriteAllBytes`, each with and without `-Force`: 18), Python (8), `cmd` (2), Node (2) | **38 of 38 blocked** |
| The agents' own tools: Claude `Write` and `Edit`, Codex `apply_patch` Update File and Delete File | **4 of 4 blocked** (Codex Add File of a *new* name beside the locked file still works) |
| Deleting: `rm -f`, `Remove-Item` (with and without `-Force`), `os.remove`, `del /f`, in 7 of the 8 delete rows | **7 of 8 blocked**; renaming the file away (`mv f f.bak`) is not |
| Setting a timestamp (`os.utime`) | blocked (a tool that stamps times on a master will fail) |
| Reading: Python, PowerShell (both), a 1 MiB memory-mapped read, attribute read | **5 of 5 work** |

Across the 42 overwrite rows the pre-committed table counts as required (the 38 above plus `apply_patch`, `Write`,
`Edit` and an xEdit/xelib save), **41 of 42 are blocked**. Under the read-only attribute alone, 27 of the 38 shell
rows were blocked (`-Force`, `cp -f`, `sed -i` and `mv` over a file got through); that is why the deny entry is there.

**What it does not stop** (read these before relying on it):

- **An xEdit or xelib save of a locked master is not stopped** (MEASURED for xelib on 4 of 4 mechanisms; the xEdit
  program itself is INFERRED to behave the same, its GUI run is pending). The save renames the locked file into
  `Data/zEdit Backups/<name>.<stamp>.bak` and writes a new file under the vanilla name. The original bytes survive
  as the `.bak`, and the lock travels with it, but the new file is unlocked.
- **Renaming a locked file away** (`mv Skyrim.esm Skyrim.esm.bak`) is not stopped: it needs only the delete right,
  which the lock does not deny (a delete deny on one file would not hold anyway, because the folder grants it).
- **Linux, Proton and WSL.** `lock` exits 3 `unsupported` on Linux/POSIX in v4.0.0 (the weaker guarantee measured
  for the X4 toolkit there, and no device to test on). WSL was never measured.
- **Not measured at all:** the game reading locked masters (INFERRED: it opens them read-only); a symlink-based
  mod-manager deploy (needs Developer Mode, not available on the test machine); the ownership of a file by another
  principal.
- **Pending the manual batch, so the lock stays off by default.** Wrye Bash scanning and building a Bashed Patch with
  the masters locked, Steam "Verify integrity" on a locked file, a **Steam game update**, an Anniversary Edition
  in-game **Creation download** (`cc*.esm` / `.bsa`, which are on the lock list), the xEdit program and Mod
  Organizer 2's virtual file system have **not** been run. A Steam update or a Creation download writes into the same
  locked files, so it is INFERRED (not measured) to fail with a write error while they are locked: unlock first. Vortex was checked by replaying its own file operations (hard-link deploy,
  purge, a game-file collision backup and restore) on a copy: every replayed step passed (the symlink-deploy step
  could not be run), and a collided file that became multi-link is reported rather than relocked. Under the pre-committed decision table, "not run" means **opt-in on
  every edition**; the default flips to on only after those results are in.
  Ruling 2026-10-14: v4.0.0 ships the lock **off by default**, and Steam "Verify integrity" on a locked file is deliberately
  left untested. Treat it as unsupported: unlock first.
- **`guards off` does not cancel the lock**, and the lock cannot say why it refused: the OS reports only "access
  denied" (see below).

**Unlock in one command.** `bash tools/skyguard.sh lock unlock <file>` (from `cmd` or PowerShell:
`tools\skyguard.cmd lock unlock <file>`) lifts one file; with no file it lifts every target. Typed at your own
terminal it needs nothing else. An agent's shell must pass `--session <id>` (or have `SKYGUARD_SESSION_ID` set); the
lift is then recorded and bound to that session. **A lift lasts one hour by default: the first tool call after it
expires locks the file again**, and the next session start relocks anything still open (the backstop). Without hooks
neither happens (see below). `bash tools/skyguard.sh lock status` shows each target as locked, unlocked, missing,
half-locked (`drift`) or read-only but not locked by the toolkit, plus the files it skipped; it does not show lifts (a lifted file reads
`unlocked`): `bash tools/skydoctor` shows each lift and when it ends. `lock disable` turns the lock off for good (until
`lock enable`) and a banner repeats it at every session start. To lock again now: `lock lock`.

- **Without Python:** double-click `tools\skyguard-unlock.cmd`. It unlocks every target (no lift is recorded), re-checks
  every file, prints `NOT unlocked` and exits non-zero if one is still locked; `lock lock`, or the next session start,
  locks them again. MEASURED on the shipped script with Python off `PATH` (only `C:\Windows\System32` on it): 6 of 6
  masters unlocked, no toolkit deny entry left (`docs/evidence/2026-10-05-plan6-e2e.md`, row 9).
- **Last resort, by hand,** for one file `F`, in this order: `icacls "F" /remove:d *<your SID>` and then
  `attrib -R "F"`. Your SID is the `S-1-5-21-...` value `whoami /user` prints (call `System32\whoami.exe`, not Git's).
  `/remove:d` removes every explicit deny entry for your account on that file. Afterwards `icacls "F"` should list no
  deny entry.
- **What an agent may do.** `lock unlock` and `guards off` are allowed with a note ("tell the user why, restore it when
  done"). An agent is **refused** (never softened by shadow mode) when it clears the attribute or the ACL by hand on a
  lock target (`attrib -R`, `icacls ... /remove`, `Set-ItemProperty`, interpreter code that does the same) or runs
  `tools\skyguard-unlock.cmd`; `lock disable` is a prompt on Claude and a refusal on Codex. Hand-clearing would skip the
  log and the expiry. This is a Layer 1 rule, so it only holds where the hooks run.
- **On Codex, and from Claude's PowerShell tool, the agent cannot lift the lock, by design.** A lift needs the
  session id, and only Claude's Bash tool receives it (as `SKYGUARD_SESSION_ID`); Codex and the PowerShell tool get
  none, and the `.cmd` and hand-clearing are refused to agents (above). There, **you** run `tools\skyguard-unlock.cmd`
  in your own terminal when the agent needs a master written (and `lock lock` afterwards, or let the next session
  start relock). Codex's default `workspace-write` sandbox could not clear the lock by hand either (MEASURED once,
  codex-cli 0.160.0: `icacls` changes nothing there, and `attrib -R` is refused by the deny entry).
- **An agent that never read this** sees only "access denied". The core instructions tell every agent what that means.

**If no hooks run, nothing relocks a lift.** The relock after an hour happens on the next tool call the hooks see, and
the backstop at session start; there is no scheduled task and no service, by design. On an agent with no hooks, an
expired lift is no longer honoured, `bash tools/skydoctor` reports the files as `unlocked (lift until ...)` or
`unlocked (outside skyguard)` and drops the expired record, but the files stay unlocked until a hooked session runs or
you run `lock lock`. An unlock there can therefore outlast its hour.

**Who is told about a lift (Table V; the interactive screens are still pending).** Every shadow-mode call is written
to `.claude/backups/AUDIT_LOG.txt` and put in the model's context, the lift notes and session-start banners go to the
model's context, and `bash tools/skydoctor` shows every lift. None of them is sent through a channel that is shown to
you: the toolkit's hooks emit model context (`additionalContext`) only (MEASURED in headless Claude, `claude -p`:
every note arrived as `additionalContext`; `docs/evidence/2026-10-05-plan6-e2e.md`). The interactive
Claude screen has not been checked, and for Codex it is unmeasured, so the toolkit claims only that **every lift is
reported to the agent, recorded in the state file, and shown by `skydoctor`**, not that it is shown to you.

**A forged state file cannot be prevented, only made loud.** Code running as you can rewrite any file, so a lift is
checksummed (catches accidents and naive edits only), bound to the session that made it, expires (an hour by
default), is logged on every call it is honoured, and is reported and cleared at the next session start. A state
file the engine cannot verify is read as guards on and the lock on, with a `STATE-REJECTED` banner. Writes to
`.skyguard/` are refused on the routes listed below, not on every route.

### Shadow mode: `guards off`

`bash tools/skyguard.sh guards off --session <id> [--reason "why"]` (`tools\skyguard.cmd ...` from cmd or PowerShell) switches the
guards to **shadow mode** for that one session, for an hour or until the next session starts, whichever is first: every rule still runs, but a refusal or a confirmation prompt becomes an allow plus a warning (`Guards OFF: this would have been
denied (<rule>).`, or for a confirmation prompt `Guards OFF: this would have asked you first (<rule>); it ran without asking.`), and every call is logged as `SHADOW` in `.claude/backups/AUDIT_LOG.txt`; a call it lets through is one line, `skyguard SHADOW: <rule> (deny) not enforced: ...`, carrying the rule's own message, never a separate DENY or ASK line. `guards on` ends it;
`guards status` shows it. The reason (one line, at most 120 characters; `guards off` when none is given) is what `skydoctor` and the next session-start banner show, as untrusted data. Backups and snapshots still run. **Never shadowed:** a broken guard (`GUARD INERT`),
Codex's `.rules` file, the Layer 2 lock, the refusals of hand-unlocking a lock target and of `lock disable`, and the
refusals of writes into `.skyguard/`.

- **It also lets through the deletions the guard could not read.** The "could not read this command" and "path the
  guard cannot work out" deletes (`S-DEL-FALLBACK`, `S-DEL-UNRESOLVED`) are ordinary refusals, so shadow mode allows them.
- **Deletes inside the install have no backup.** The toolkit does not copy files before a shell delete, so with
  guards off an install delete (`S-DEL-INSTALL`) is neither blocked nor recoverable from the toolkit.
- The lift is recorded only if the session's start is visible to the hooks, and is honoured only inside that session.
- If you need the guards off because the engine itself is broken, shadow mode cannot help (a broken guard still
  refuses); use your agent's own switch for hooks instead.

### The guards protect themselves, and tell you when they change

- **Writes to `.skyguard/` (the on/off and lift state) are refused** on the shell (redirects, `cp`, `tee`, `sed -i`,
  `Set-Content`, `Copy-Item`, `git checkout -- .skyguard`, downloads and more), the file tools, Codex `apply_patch`,
  moves and deletes; only `tools/skyguard` should write it. **Known gaps, MEASURED as allowed** (final review, lane
  A): an interpreter writing it from code (`python -c "open('.skyguard/state.json','w')..."`; recognized interpreter
  deletes are caught, writes are not), and unpacking an archive into it (`tar -xf a.tar
  -C .skyguard`, `7z x a.7z -o.skyguard`). Such a forged state still needs a valid checksum and the session id, and
  is named by the next session-start banner.
  Ordinary `install -m 644 x .skyguard/state.json` is now refused; computed destinations and uncommon option forms
  remain limited (for example, a glued `install -tDIR` destination).
- **Not guarded at all, so an edit gets no note:** `GUARD-FILES.sha256` itself, the launchers `tools/skyguard.sh`,
  `tools/skydoctor` and `tools/skyguard-unlock.cmd`, and `.claude/backups/MANIFEST.jsonl` (which a restore trusts
  for the original path; MEASURED by an engine probe: a `Write` or `echo >` to each is allowed with no note). An edit
  to a hook plus a matching edit to `GUARD-FILES.sha256` hides that hook from the session-start banner (the per-call
  note on the hook edit itself remains).
- **The guard code and config get a note, not a prompt:** `.claude/hooks/`, `.claude/settings*.json`, `.codex/`,
  `tools/skyguard/`, `agent/guards/`, `skyrim-paths.env` (at the toolkit root, and the 3.x `.claude/skyrim-paths.env`),
  the hook heartbeats and audit log, and your user-level
  `~/.claude/settings*.json` and `~/.codex/config.toml`. A prompt was priced against the author's own history and
  rejected: 24 of 24 would-be prompts were hook maintenance the user had asked for.
- **A session-start banner** compares the guard files against the shipped `GUARD-FILES.sha256` and names the files
  that differ from the release. This is tamper evidence, not prevention: an identical edit to all eight hook
  interpreter lines is not seen by the banner (the per-call note still is).

The shell guard remains a pattern-based accident barrier. Some encoded program names in Bash ANSI-C quotes
are not classified; this gap also exists in v3.9.1. The guard does not provide a sandbox.

### Backups and snapshots you can restore from

- **Every backup now has a manifest line** (`.claude/backups/MANIFEST.jsonl`: the exact original path, size and
  sha256, tool, agent, session). A backup is written to a temporary name, checksummed while it is copied, flushed,
  and given its final name without ever replacing another backup, so two edits of one file in the same second keep
  two backups and a failed copy leaves no half-file.
- **`bash scripts/restore-from-backup.sh`** reads the manifest, shows the exact original path, checks the sha256
  before it restores, and backs up the file it replaces. `--restore` never writes into a plugin or archive or the
  guard's own folders (the same refusal `snapshot restore --in-place` applies), and an agent that runs it is asked
  to confirm first. The old script rebuilt paths by turning every underscore into a slash, so
  `Skyrim_VR - Main.bsa` or `My_Mod.esp` was restored to the wrong place; a backup with no manifest line is still
  offered, labelled `(legacy: path guessed)`.
- **Script snapshots reach every script.** `.psc` and `.pex` are now snapshotted wherever they sit under
  `Data/Scripts/` (nested folders included) and `Data/Source/Scripts/`, with no 7-day age limit, into a
  content-addressed store. The old hook saw only the top level of one layout and only files younger than 7 days: on
  the author's install **3,428 of 11,898 scripts (29%) were never seen** (MEASURED read-only). Folders that are
  junctions are not followed. Added latency, MEASURED on the release code on synthetic trees with the install's file
  counts, replayed over the author's real call pattern (16,672 hook calls that walk, out of 23,282; how often scripts
  change is ASSUMED from history): **p95 129.9 ms** over the walking calls with 3,049 scripts hard-linked as on the
  live install, **137.7 ms** with all 11,898 hard-linked, against a gate of 150 ms; the slowest single call shape is
  148.5 / 150.7 ms at p95. Charging the recovery snapshot's once-a-minute re-read (13.8 ms) to every walking call
  outside the rate window, a conservative sensitivity check rather than the gate, gives 143.7 ms and **151.5 ms, 1.5 ms
  over the gate** on the fully linked tree (`docs/evidence/2026-10-04-plan6-pricing.md`, Task 18 step
  3). The first session builds its baseline over about 100 s of work spread across the session start and the first
  calls.
- **Recovery snapshots** also copy, before a shell command that may change them, your `plugins.txt`, `loadorder.txt`
  and `Skyrim*.ini` from the configured folders, and, before a ReSaver `--apply` or a DevBench save, the **one save
  it will overwrite and its `.skse` co-save** (for ReSaver that is the output save; never the whole save folder).
- **Restoring:** `bash tools/skyguard.sh snapshot list` shows every snapshot with its id.
  `bash tools/skyguard.sh snapshot restore <id>` writes the files to a **new folder**
  (`.claude/backups/restored/<id>__<time>/`) and touches nothing live. `--to DIR` chooses the folder (it refuses if
  a target exists); `--in-place` puts files back where they came from, only the files that kind of snapshot is allowed to write
  (for load order, INIs and saves: `plugins.txt`, `loadorder.txt`, `Skyrim*.ini`, `.ess`, `.skse`) and only inside the
  configured roots, and **backs up each file it replaces first**. A file that already matches the snapshot is left
  alone and reported `unchanged`, so a mod manager's hard link to it survives. A full restore of a load-order/INI
  snapshot skips, and names, a file whose folder is no longer configured, and restores the rest. Before it replaces a
  save it keeps 2 GiB free on the backups drive, or restores nothing. An agent that runs an in-place restore is asked
  to confirm first. A damaged stored copy refuses the restore and names the file.

### `skydoctor`, and how it differs from `skyguard doctor`

- **`bash tools/skydoctor`** answers "what is actually live on this install?" in one report, layer by layer, one line
  per item as `live`, `not live` or `unknown` with a reason (never blank): the tools start (Layer 0), the instruction
  files are present and `AGENTS.md` fits its 32,768-byte limit (Layer 1), each lock target is locked, lifted, skipped
  or missing plus the known gaps above (Layer 2), the hooks of each agent are wired and being exercised, with one coverage row per recorded Claude Code or Codex session, as `hook-canary` reads it (Layer 3), whether the
  guards are on, and whether the guard files match the release. Exit 0 = everything live, 1 = something not live,
  2 = something unknown. It writes nothing except dropping expired lifts (and logging that it did). **A default
  install, with the lock never applied, exits 1 on purpose.**
- **`bash tools/skyguard.sh doctor`** is the older, narrower command: only the **hook health** report (are the hooks
  firing and receiving their payload). `skydoctor` includes that reading as one part. Use `skydoctor` for the whole
  picture; use `skyguard doctor` only if you want the hook report alone.

---

## Using with Codex

The same install works with OpenAI's Codex CLI on Windows. `setup.sh` configures it alongside Claude Code:
`AGENTS.md` gets your paths, the skills are in `.agents/skills/`, and the safety hooks in `.codex/`.

**One step only you can do: approve the hooks.** Codex runs a project's hooks only after you approve them,
and it skips unapproved ones **silently** -- no error, no warning, nothing checked. Open `codex` in your
Skyrim folder, type `/hooks`, and approve the **2 Skyrim toolkit hooks**. Then check with
`bash tools/hook-canary.sh`. When they run, each Codex session starts with `SKYGUARD: Codex hooks live`.

Start Codex in the game folder. Subfolder loading is version-dependent: it worked in an isolated 0.162.1 probe.
Verify the live banner and canary before relying on hooks. The older evidence: from a subfolder of a non-git
install no hook ran on codex-cli 0.160.0, which is why it depends on the version you run.

Approve again when:

- a toolkit update's CHANGELOG says **"re-approve your Codex hooks"** (Codex stops running an approved hook
  the moment its definition changes, again silently);
- you move the toolkit to another folder. The approval covers the definition as rendered, install path
  included (inferred from how Codex hashes it, not measured). `setup.sh` warns when the definitions point
  at another folder: re-extract the zip there, run `bash setup.sh`, approve again.

**What is checked:** every shell command and every `apply_patch` the agent runs, including a patch run
through the shell -- the same rules as Claude Code. **A confirmation becomes a refusal on Codex**: a hook
there cannot ask you, so anything that would need your OK is refused with `NEEDS YOUR CONFIRMATION`, and
you run it yourself if you agree. Deleting the Bethesda registry keys (`reg delete` / `reg.exe delete` of
`HKLM\SOFTWARE\Bethesda Softworks` and the other spellings it lists) is also refused by
`.codex/rules/skyrim.rules`, which holds even before you approve the hooks.

**What is not checked:**

- **Anything, until you approve the hooks** (above). Deleting a Bethesda registry key, spelled as the rules file
  lists it, is the one exception.
- **Commands you type with `!`** in the Codex prompt -- they run in your own shell and bypass hooks
  (measured on codex-cli 0.160.0).
- **Input sent to a program that is already running** (Codex's `write_stdin`): the keystrokes are judged as a
  shell command when the hook sees them, but whether Codex 0.160.0 fires a hook for `write_stdin` at all is
  **unmeasured**, and keystrokes that only finish a command typed earlier are not put back together. Treat it
  as a possible gap.
- **MCP tools other than DevBench's**: logged in `.claude/backups/codex-unhooked-tools.log`, not guarded. DevBench's own tools
  (any server whose name contains `devbench`) get the same judge as on Claude: `smp reset` is refused, and a save over an
  existing save is backed up first, then runs (more than two saves at once need your typed approval).
- **A shell call's own working directory** (`workdir`): read when the hook payload carries one, so relative
  paths land there; whether Codex puts `workdir` in the payload is **unmeasured**. Use absolute paths for
  anything in the game folder.
- **A hook that cannot start lets the command run.** That is how Codex treats a failed hook, so the toolkit's
  entry scripts never fail on their own -- but a hook that never starts cannot help. Two setups where it
  never starts are **not supported**: a Windows group policy that forces the PowerShell execution policy to
  `AllSigned` (the entry script is unsigned, and `-ExecutionPolicy Bypass` cannot override a group policy),
  and **Codex running inside WSL** (it runs the Linux command, not the Windows one this install is set up
  for). `setup.sh` also refuses an install folder whose path contains `%`, `!`, `$`, `` ` `` or `"`, which
  would cut the hook's own path; Claude Code still works there.

**Surfaces.** Claimed: the Codex **CLI** -- the interactive TUI and `codex exec`. The Codex desktop app and
IDE extensions are not claimed: some Windows Codex surfaces emit no hook calls at all for some commands
([openai/codex#24453](https://github.com/openai/codex/issues/24453)). There, treat the install as
"Layers 0-2 only": just the protections that need no hook (the instructions and
`.codex/rules/skyrim.rules`). `bash tools/hook-canary.sh` shows which hooks actually ran. It also lists Codex's trust state and, per surface, how many tool calls a session's transcript shows
against how many the hooks recorded. That coverage covers **only sessions in which a hook fired**: a surface
whose hooks never fire leaves no record at all, so it cannot appear as a gap. That is why only the surfaces
probed live (`codex exec` and the TUI) are claimed, and why the desktop app and IDE extensions are not.

## Other agents

Any agent that reads files gets the toolkit's instructions, skills and tools, with no hook (Layers 0 and 1, plus Layer 2 if the user turns it on: a read-only lock on the vanilla files below the agent, off by default, Windows only). [`ADAPTING.md`](ADAPTING.md) is written for such an agent: it lets the agent wire up the rest itself and describes how far it can prove it got. The toolkit does not call such an agent supported; only Claude Code and Codex are listed as supported here.

---

## FAQ

**Q: Does this work with flat Skyrim SE (non-VR)?**
A: Yes! The knowledgebase covers both. VR-specific sections only apply to VR. Safety hooks and workflow work for either. Just tell Claude Code to adapt your environment to your specific Skyrim version.

**Q: Can Claude break my mods or save files?**
A: The safety hooks refuse the destructive cases outright rather than asking you to confirm them: writing a plugin or archive (through the edit tools or a shell write such as `>`, `tee`, `Set-Content`), deleting inside the game, config or Mod Organizer folders, and deleting Bethesda's registry keys are all denied. The delete rule is anchored on the *program that runs* (`rm`, `rmdir`, `del`, `Remove-Item`, `shutil.rmtree`, `find -delete`, in Bash and PowerShell) and on where its operand **resolves**, relative paths against the working directory Claude reports, in either slash direction and either path dialect -- an earlier version keyed on one spelling of `rm` and eleven others walked past it, and matched the words of a `grep` as if they were a delete. The install is *resolved*, from the hooks' own location plus the paths `setup.sh` records in `skyrim-paths.env` at the toolkit root (a 3.x install's `.claude/skyrim-paths.env` is still read); it is not "any path containing the word Skyrim", which used to match a temp folder, a checkout of this toolkit, and even a git URL, refusing ordinary work as though it were deleting your game. If the hooks cannot work out where the install is, they **refuse rather than allow** -- a guard that checked nothing must not answer "fine". **It is still pattern matching, not a sandbox**, so treat it as a guard against accidents rather than a guarantee against everything. Ordinary edits proceed with a warning in Claude's context instead of a prompt at you, and files it edits through the Edit/Write tools are copied to `.claude/backups/` first with an audit-log entry -- work done by external tools through Bash is covered by the `.psc`/`.pex` snapshot instead. Verify the hooks are actually live with `bash tools/hook-canary.sh`, and keep your own backups regardless.

**Q: Do I need Blender or NifSkope?**
A: Only for the optional headless render-verification loop. The core NIF authoring/editing (PyFFI, PyNifly) works without them. If you want Claude to render a mesh to a PNG and show it to you in chat, install Blender; NifSkope adds an independent visual gate. Setup will walk you through it.

**Q: Every command is refused with "GUARD INERT ... cannot run Python"**
A: The safety hooks run on Python 3 and refuse everything when they cannot start it (that is deliberate: a guard that cannot run must not allow). Install Python 3 (for you to run: `winget install --id Python.Python.3.12 --exact --version 3.12.10 --no-upgrade`: inside the tested 3.10-3.13 range and the Python the optional tools were verified on; winget checks the installer's hash), then run `bash setup.sh` again so it records the interpreter's path in the hooks. `jq` is no longer needed by the hooks; it is optional and only prettifies `tools/devbench-cli.sh` and `tools/nexus.sh` output.

**Q: How do I update the toolkit?**
A: (1) Unpack the new zip into a separate folder. The zip holds one versioned folder, `skyrim-ai-assistant-toolkit-<version>/`: that inner folder is `<new folder>` below, the one with `setup.sh` and `tools/` directly inside it. (2) From your Skyrim folder, run `bash "<new folder>/tools/pre-update-snapshot.sh" .` to keep a copy of what the update replaces. (3) Copy the new folder's contents over your Skyrim folder; shipped files are replaced. (4) Run `bash setup.sh --agent auto` (or `claude`, `codex`, `all`); on a 3.x install it runs `tools/migrate-3x.py`, which carries your own edits to CLAUDE.md, the hook settings and KNOWLEDGEBASE.md forward and deletes nothing. (5) Complete the hook review (Codex: `/hooks`) and check `bash tools/hook-canary.sh` after real tool calls. If you wrote your own pre-tool hook handlers or a personal save policy, use the reviewed upgrade in [docs/upgrading-native-hooks.md](docs/upgrading-native-hooks.md) instead of step 3.

**Extracting directly over an installation replaces shipped files, including customized instructions and hook settings.** Step 2's snapshot and setup's migration keep a copy of each; the reviewed upgrader instead preserves collisions for explicit choices. Your own notes belong in **`KNOWLEDGEBASE.local.md`**, which the toolkit never ships and the release build refuses to contain; the agent reads both knowledge files and writes only to the local one. Its `## My preferences` section holds your standing instructions. Existing session hooks and the OpenCode system transform reinject that section through the same bounded parser. Local notes, path configuration and permanent recovery stores remain separate from shipped defaults.

Since v3.9 a SessionStart hook also copies `KNOWLEDGEBASE.md`, `KNOWLEDGEBASE.local.md`, `CLAUDE.md` and `AGENTS.md` into `.claude/backups/kb/` whenever they change, and says so loudly at the start of the next session if one of them vanished, emptied, or lost more than half its bytes — which is what extracting an update over your install looks like from the inside. Run it yourself any time with `bash tools/kb-guard.sh --verbose`. Note the honest limit: an update that replaces your 100 KB knowledgebase with a 140 KB shipped one shrinks nothing and raises no alarm. The stored copy is the protection; the alarm is a convenience on top of it.

---

## Releasing (maintainer)

The GitHub release is built by `.github/workflows/release.yml` from the tag. The **Nexus upload
pack** is built by `scripts/build-nexus-pack.sh <tag> <changelog.txt> <description.txt>`, which
downloads the published release asset rather than rebuilding it, records its sha256 and size, and
**refuses to report success on an incomplete pack**. `tests/test_nexus_pack.py` asserts the shape.

Before any tag, run `bash scripts/build-release-zip.sh HEAD dist` and then
`bash tools/verify-release.sh --local dist/<zip> --scrubbed` on your own machine: it greps the zip and
everything a scrubbed install generates for your user name, home folder and real game folder (read from your
environment, never written into the repo), for the values of your API keys, and for key-shaped strings. That
local run is the authoritative identity-leak gate. The release workflow repeats the grep, but a CI runner
does not know who you are: set the repository secret **`LEAK_TOKENS`** (Settings -> Secrets and variables ->
Actions) to your user name and your home and game folders, one per line. Write each folder ONCE, as a
Windows path (`C:\Users\<you>`, `D:\Games\Skyrim VR`): the checker derives the other spellings itself
(forward slashes, JSON-escaped, `/c/...`, URL-encoded). A name needs at least 4 characters and a folder at
least two named levels, or the token is rejected (by line number; the release log never prints a token or a
matched spelling). If the secret is missing, empty or rejected, the release workflow refuses to publish.

The changelog and mod-page description are passed in, not generated: extracting a version block
from `CHANGELOG.md` yields raw markdown, while the Nexus box renders a flat list and wants one
issue per line. Nexus has no write API for files or the description, so the upload itself is always
manual.

## Contributing

Found a new Skyrim quirk? PRs welcome on [GitHub](https://github.com/WingedGuardian/skyrimvr-claude-toolkit) -- especially additions to `KNOWLEDGEBASE.md`.

### Editing CLAUDE.md, skills, hooks or settings

These are **generated**. Edit the source under `agent/` (see `agent/README.md` for the map; the safety rules themselves live in `tools/skyguard/*_rules.py`), then run
`python scripts/gen_agent_trees.py` and commit both. CI runs `--check` and rejects a hand edit of a
generated file. `--check` also enforces the **instruction budget**: the installed instruction file
(header + `agent/instructions/core.md` + an addendum, plus room for `setup.sh`'s path expansion) must
stay at or under 32,768 bytes, because Codex silently truncates past that. Check it with
`python scripts/gen_agent_trees.py --budget`; when over, move bulk into a skill or `docs/`. The core
and the `devbench`, `mod-release-review`, `mod-update-check`, `toolkit-setup`, `triage-and-conflicts` and `xelib` skills must also stay **agent-neutral**
(no Claude-only tool names, variables, slash commands or config paths; Claude specifics go in
`agent/instructions/claude.md`), enforced by `tests/test_instruction_neutrality.py`.

### Running the tests

The suite is dev-only and is not in the release zip. From a clone:

```bash
python -m venv .venv
# Windows:            .venv/Scripts/python
# Linux / macOS:      .venv/bin/python
.venv/Scripts/python -m pip install --only-binary=:all: --require-hashes -r requirements.lock
.venv/Scripts/python -B tests/run_harness_tests.py tests/ -v  # contained behavioral suite
node --test                                                # Node unit tests
```

The suite runs on both Windows and Linux in CI, so it should pass on either.

Use the contained launcher above for local tests: bare pytest inherits the host environment, and with
`TOOLKIT_REQUIRE_HARNESS=1` a pytest session without the launcher's barriers errors instead of going green.
See [integration safety and dependency locks](docs/harness-integrations.md) for Spriggit
publication, read-only container mounts, MO2 load-order selection and explicit unavailable checks.

`tests/` runs the **real** `setup.sh` against generated MO2 and stock layouts and
asserts on the paths it *wrote* -- not on what it printed. `tools/devbench-cli.sh`
is exercised against a mock DevBench server covering every liveness state.

**`tests/test_mutations.py` is the one to understand before adding a test.** A
regression test written after its bug was fixed passes on the first run, which
proves nothing. The mutation gate reverts each fix on a throwaway copy and
asserts the guarding test *fails*. If you add a regression test, add its
mutation too -- otherwise you have coverage you cannot trust. It has already
caught two of its own guards asserting nothing.

## License

MIT -- see [LICENSE](LICENSE).

## Credits

- [xeditlib](https://github.com/WingedGuardian/xeditlib) -- Node.js wrapper for XEditLib.dll
- [zEdit](https://github.com/z-edit/zedit) -- Source of XEditLib.dll
- [Spriggit](https://github.com/Mutagen-Modding/Spriggit) -- ESP to YAML serialization by Mutagen
- [Spooky's AutoMod Toolkit](https://github.com/SpookyPirate/spookys-automod-toolkit) -- the CLI backend for the toolkit's NIF/BSA/audio/MCM/ESP operations
- [PyNifly](https://github.com/BadDogSkyrim/PyNifly) -- NIF read/write and animation authoring (wraps [nifly](https://github.com/ousnius/nifly))
- [PyFFI](https://github.com/niftools/pyffi) -- LE-format NIF geometry editing
- [Blender](https://www.blender.org/) and [NifSkope](https://github.com/hexabits/nifskope) (v2.0.dev9, the maintained hexabits fork) -- mesh repair and independent render verification
- [FallrimTools / ReSaver](https://www.nexusmods.com/skyrimspecialedition/mods/5031) -- save-file (.ess) parsing library driven headlessly by the ReSaver CLI
- [ReSaver++ / ReSaver Plus](https://github.com/Tonycubed2/ResaverPP) by Tonycubed2 (Apache-2.0) -- the C++ port of Mark Fairchild's ReSaver behind the opt-in native ReSaver backend; built locally from a pinned commit plus the toolkit's patches (`tools/resaver-plus/NOTICE.md`), with LZ4 (Yann Collet) and miniz (Rich Geldreich) linked into its CLI
- [DevBench](https://www.nexusmods.com/skyrimspecialedition/mods/181326) by alandtse ([source](https://github.com/alandtse/devbench)) -- the live in-game REST/MCP channel the `devbench-cli.sh` wrapper drives
- [@aaronputty](https://github.com/aaronputty) ([fork](https://github.com/aaronputty/putty-skyrim-claude-toolkit)) -- the MO2 path-detection insight that shaped v3.4, and the devcontainer + `docs/container-vs-windows.md` foundation for v3.5
- [Claude Code](https://claude.ai/code) by Anthropic
