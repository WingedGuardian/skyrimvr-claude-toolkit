# Skyrim Claude Code Modding Toolkit

An AI-assisted Skyrim modding environment for power users. Claude Code handles the mechanical work — porting mods across versions, inspecting and editing ESPs, debugging scripts, building mods from scratch, **and now authoring custom animated meshes and VFX you can verify without ever launching the game** — with safety hooks, pre-loaded engine knowledge, and every tool pre-configured.

Built from hundreds of hours of hands-on Skyrim VR mod development. **[Though this env was built with VR in mind, it can be just as powerful in any Skyrim version. Claude Code is the brain.]**

> **This isn't just a guide -- it's a complete environment with all the setup prework already done.** You just need to install, and begin building the mod of your dreams! (Or ironing out all the bugs in your existing setup 😛)

---

## What Is Claude Code?

[Claude Code](https://claude.ai/code) is an AI assistant by Anthropic that runs directly on your computer. Unlike ChatGPT or regular Claude chat, it can **read your files, run commands, edit configs, execute scripts -- and do the mechanical work of modding for you** -- all with your permission. Think of it as having a modding expert sitting next to you who can actually touch your files.

Most people who try to use Claude Code for modding spend days figuring out tool integrations, fighting weird Delphi DLL quirks, learning what Claude needs to know to be useful, and building safety guardrails so it doesn't break anything. This toolkit ships with all of that already solved. You get a working environment on day one.

It's not perfect, and it will require some trial and error — especially for complex mods from scratch. But for the tedious parts of modding, it's significantly faster than doing it yourself.

---

## New in v3.5: A Reproducible Dev Container

Every install so far has meant "run `setup.sh`, install prerequisites onto this machine, hope."
v3.5 adds an alternative: a Docker container with Python, Node, .NET, and a JDK already wired up —
built and verified end-to-end (Spriggit really does serialize a real mod's ESP inside it) — for the
parts of the workflow that don't need Windows or an active MO2 session: ESP inspection and diffing,
FOMOD/JSON generation, unit-testing mod logic, ReSaver CLI save analysis.

`bash devshell-docker.sh` builds the image and drops you into a shell — Docker Desktop is the only
prerequisite. `setup.sh` wires its mounts to your real mod files automatically: on MO2, straight from
your detected instance; on stock/Vortex, from `Data/`. `docs/container-vs-windows.md` is the routing
guide for what belongs in the container versus what still needs Windows (xelib, load-order-dependent
edits, Papyrus compilation, NIF authoring, live in-game testing).

Credit: the devcontainer and its MO2-mount design come from
[@aaronputty](https://github.com/aaronputty)'s fork of this toolkit.

---

## New in v3.4: Mod Organizer 2 Support

If you use MO2, previous versions quietly pointed Claude at the wrong folders. **MO2 has no real
merged `Data/` folder** — it builds a virtual one at launch, so the game's `Data/` holds the stock
game and almost none of your mods, and your INIs and load order live in the profile rather than in
Documents. The toolkit assumed the Vortex/stock layout throughout.

Setup now detects your MO2 instance (global or portable), matches it to this game folder, resolves
your active profile and the real mods/overwrite/profile paths, and writes those into `CLAUDE.md`
instead of the wrong ones. It also warns Claude about the trap that costs MO2 users the most time:
**load-order-aware tooling launched outside MO2 doesn't error — it silently returns a wrong but
plausible answer**, because it only sees the plugins physically present in the stock `Data/`. Non-MO2
setups are unaffected and get a stock-layout note instead.

Also new: an **`AGENTS.md`** so agents that follow that convention can pick the toolkit up. The
knowledge and every tool are agent-neutral already; the safety hooks are the Claude-specific part, and
`AGENTS.md` is explicit that other agents don't get them.

---

## New in v3.3: Claude Can Test Its Own Fixes in the Running Game

Every other feature here shortens the time it takes Claude to *make* a change. This one attacks the
part that actually eats your evening: the test loop. Normally it goes change → you launch the game →
you trigger the thing → "nope, still broken" → guess again. Ten minutes a cycle, and Claude is
working blind off your description.

**With [DevBench](https://www.nexusmods.com/skyrimspecialedition/mods/181326) installed, Claude drives
the running game itself.** DevBench is a dev-only SKSE plugin (by alandtse, of Engine Fixes VR) that
runs a small localhost server inside Skyrim — it changes no gameplay and writes nothing to your saves.
Claude can then:

- **Read live state** — the Papyrus VM's health (the actual diagnosis for script freezes), active
  magic effects, your equipment, quests, the loaded reference grid.
- **Run console commands and read their output** — not fire-and-forget like `cgf`; it sees the result.
- **Call Papyrus functions and get the return value back**, so it can do read → change → re-read loops
  without a single game restart.
- **Narrate tests on your HUD** while you're in the headset, and dismiss modal popups for you.
- **Run scripted scenarios** with real event waits instead of guessed sleeps.

In practice, tuning a value stops being "edit, recompile, reload, ask you to try it" and becomes
another call into the running game. You just keep playing. The toolkit ships the wrapper
(`tools/devbench-cli.sh`) and the hard-won hazard list — DevBench itself you install from Nexus.

---

## New in v3: Create Custom Animated VFX — and See Them Before You Load the Game

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
- **Safety hooks** -- Claude asks permission before editing any game file, won't touch ESP/ESM files directly, and automatically backs up everything it modifies with a full audit trail.
- **Confidence system** -- Claude rates its confidence (0-100%) and lists its assumptions before proposing any change. No guessing, no "this should work."
- **ESP editing via Spriggit** -- Serialize any ESP to human-readable YAML, edit it directly, deserialize back. Claude's native file editing works on YAML out of the box — no FFI layer, no scripting, and changes diff cleanly in git.
- **ESP analysis via xeditlib** -- Programmatic inspection, diffing, and bulk queries across records. The hard Delphi FFI work is already done. ([xeditlib on GitHub](https://github.com/WingedGuardian/xeditlib))
- **Cross-reference integrity guard** -- A tool-agnostic wrapper snapshots an ESP's references before a risky bulk edit and loudly fails afterward if any reference was silently re-mastered or dropped.
- **NIF mesh tools** -- Inspect, retexture, scale, fix eye-ghosting, split/subdivide geometry, apply glow maps, and verify mesh files. Detect VR-breaking skeleton nodes.
- **BSA archive tools** -- Full read/write/merge/diff on BSA archives. Extract individual files, create new archives, update contents.
- **Audio processing** -- Extract, convert, and create Skyrim voice and sound files (FUZ/XWM/WAV).
- **MCM menu generation** -- Programmatically create SkyUI mod configuration menus with toggles, sliders, and pages.
- **Save file analysis** -- Decompress and binary-scan .ess saves. Search for orphaned scripts, count effect accumulation, check mod footprint, detect save bloat.
- **Dry-run workflow** -- All ESP and asset changes go through a preview pass first. Claude shows you exactly what it will do before touching anything.
- **Claude Code skills** -- Slash commands like `/inspect-esp MyMod.esp`, `/port-to-vr`, and `/create-mod` that trigger guided workflows. Auto-loading context that injects critical Skyrim gotchas when Claude works with game files.
- **Auto-setup** -- One prompt installs prerequisites, configures paths, sets up hooks, and optionally installs modding tools. No manual configuration.

### Optional tools (install as needed)

None of these are bundled; setup walks you through any you pick.

- **xeditlib** -- programmatic ESP read/write (`npm install github:WingedGuardian/xeditlib`)
- **Champollion / Caprica** -- Papyrus decompile / compile (GitHub releases)
- **Spriggit** -- ESP ↔ YAML editing (`dotnet tool install Spriggit.CLI`)
- **AutoMod CLI** -- NIF / BSA / audio / MCM / ESP. **Must be cloned and built**: clone https://github.com/SpookyPirate/spookys-automod-toolkit into `tools/automod` and build the Cli project (not the WPF Setup project) — then run via `tools/automod-cli.sh`.
- **PyFFI** -- LE-format NIF geometry (Python 3.10, `pip install pyffi`)
- **PyNifly** -- SSE BSTriShape + animation authoring (download `io_scene_nifly.zip` from the GitHub **releases**, not a git clone — the compiled DLL ships only in the release zip; no build)
- **Blender (headless) / NifSkope** -- NIF mesh repair + render verification (large external apps)
- **ReSaver CLI** -- headless `.ess` save parse / cross-reference / clean / changeform-level diagnostics (download ReSaver from **Nexus mod 5031** / FallrimTools into `tools/resaver-cli/`; requires **JDK 17+**, JDK 21 LTS recommended)
- **DevBench** -- LIVE in-game inspect / console / Papyrus while the game runs (download from **Nexus mod 181326** / alandtse into `Data/SKSE/Plugins/devbench.dll`; GPL-3.0, dev-only, no gameplay change and no save data). The toolkit bundles the wrapper `tools/devbench-cli.sh`.
- **cosave-info** -- read-only structural survey of an SKSE `.skse` co-save → JSON (which mods stashed co-save data + how much). No install beyond Python 3; bundled (`tools/cosave-cli.sh`).

---

## Setup (4 Steps)

Setup is this short because the environment is already built. There's no configuration to figure out, no tools to manually wire up, no documentation to read before you start. Everything is pre-configured -- you just point it at your game folder and go.

### Step 1: Install Claude Code

1. **Sign up** at [claude.ai](https://claude.ai) if you don't have an account
2. **Subscribe** to Claude Pro ($20/month) or Max ($100/month) -- required for Claude Code
3. **Install Claude Code** -- pick one:

   **Desktop App (easiest):**
   Download from [claude.ai/code](https://claude.ai/code), install, open it.

   **Command Line:**
   Install [Node.js](https://nodejs.org/) (click the big green LTS button, install it).
   Then open **Windows Terminal** (search "Terminal" in Start menu) and run:
   ```
   npm install -g @anthropic-ai/claude-code
   ```

### Step 2: Extract This Toolkit Into Your Skyrim Folder

1. Download this mod from Nexus (Manual Download)
2. Find your Skyrim folder:
   - Open **Steam** > **Library** > right-click **Skyrim VR** (or **Skyrim SE**) > **Properties** > **Installed Files** > **Browse**
   - A folder opens -- this is your Skyrim folder
3. **Extract the zip directly into that folder**
   - Right-click the downloaded zip > Extract All > paste your Skyrim folder path > Extract
   - The files blend in alongside your existing game files (nothing is overwritten)

### Step 3: Open Claude Code in Your Skyrim Folder

**Desktop App:** Open Claude Code. Click the folder/path area and navigate to your Skyrim folder. Or type this (replace with YOUR path):
```
cd "C:\Steam\steamapps\common\SkyrimVR"
```

**Command Line:** Open Windows Terminal and type:
```
cd "C:\Steam\steamapps\common\SkyrimVR"
claude
```

> (Use `SkyrimVR`, `Skyrim Special Edition`, or whatever your folder is actually named.)

> **Tip:** In the Steam browse window from Step 2, click the address bar and copy the path. Paste it after `cd `.

### Step 4: Paste This Prompt

Copy this entire prompt and paste it into Claude Code (it's also saved as `SETUP_PROMPT.txt` in your Skyrim folder):

```
I just installed the Skyrim Claude Code Modding Toolkit into this folder. Run "bash setup.sh" to set everything up. Install any missing prerequisites (jq, Node.js) for me. After setup, ask me which optional modding tools I'd like (xeditlib, Champollion, Caprica, Spriggit, AutoMod CLI, PyFFI, PyNifly, Blender, NifSkope, ReSaver CLI) and install the ones I pick. AutoMod CLI adds NIF mesh editing, BSA archive tools, audio processing, and MCM menu generation -- it is a real tool you install by cloning https://github.com/SpookyPirate/spookys-automod-toolkit into tools/automod and building the Cli project (dotnet build tools/automod/src/SpookysAutomod.Cli -c Release; do not build the WPF Setup project headless), after which it runs via tools/automod-cli.sh. PyFFI + PyNifly add NIF geometry and animation/controller authoring; Blender (headless) + NifSkope add mesh repair and render-verification of meshes before in-game testing. ReSaver CLI adds headless .ess save parsing, cross-referencing, cleaning, and changeform-level diagnostics (download ReSaver.jar from Nexus mod 5031 into tools/resaver-cli/; needs JDK 17+ (JDK 21 LTS recommended; e.g. `winget install Microsoft.OpenJDK.21`)). The bundled cosave-info tool (tools/cosave-cli.sh, Python 3, no download) gives a read-only structural survey of an SKSE .skse co-save. Be sure to tailor the environment specifically to my Skyrim version and install (may or may not be VR). Also ask me whether I want to enable the optional Nexus API integration (mod update-detection / version & changelog checks); if yes, explain how to get a free Personal API Key and save it to tools/.nexus_api_key (which is gitignored). Explain everything in plain English and ask me any questions you may need to.
```

Claude handles the rest. It will configure paths, install dependencies, set up the safety hooks, and walk you through optional tool installation. Just answer any questions it asks.

> **Note on the NIF render loop:** The headless render-verification step uses Blender (and, optionally, NifSkope for an independent visual check). These are large external GUI apps, so — like Champollion/Caprica — they aren't bundled in the zip; the setup will point you to install them and wire up the addon. PyFFI and PyNifly themselves are lightweight and handled by setup.

**That's it. You're done.**

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

From now on, whenever you open Claude Code in your Skyrim folder, the full environment loads automatically -- knowledge base, safety hooks, tool integrations, everything. No setup required each session. Just start talking.

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

These aren't things you configure -- they're already wired in. Every session, before Claude touches anything, these run automatically:

| Protection | What It Does |
|-----------|-------------|
| **Command guard** | Blocks deleting game files or registry keys. Confirms all file operations in game directories. |
| **File guard** | Blocks direct writes to ESP/ESM/BSA files. Confirms all other game file edits. |
| **Auto-backup** | Copies every file to `.claude/backups/` before modification, with full audit log. |
| **Confidence system** | Claude must rate confidence 0-100% and list assumptions before any change. |
| **Investigation-first** | Claude checks the knowledgebase and web-searches before touching anything. |
| **Validate-before-test** | Authored NIFs are cross-checked with an independent parser and a headless render before they're handed off for in-game testing -- crashes get caught in tooling, not your headset. |

---

## FAQ

**Q: Does this work with flat Skyrim SE (non-VR)?**
A: Yes! The knowledgebase covers both. VR-specific sections only apply to VR. Safety hooks and workflow work for either. Just tell Claude Code to adapt your environment to your specific Skyrim version.

**Q: Can Claude break my mods or save files?**
A: The safety hooks prevent this. Claude can't edit ESP/ESM files directly, must ask permission for any edit, and backs everything up. But always keep your own backups too.

**Q: Do I need Blender or NifSkope?**
A: Only for the optional headless render-verification loop. The core NIF authoring/editing (PyFFI, PyNifly) works without them. If you want Claude to render a mesh to a PNG and show it to you in chat, install Blender; NifSkope adds an independent visual gate. Setup will walk you through it.

**Q: I'm getting "jq not found"**
A: Open Windows Terminal and run: `winget install jqlang.jq` -- then restart Claude Code.

**Q: How do I update the toolkit?**
A: Download the new version from Nexus and extract over the old one. Your knowledgebase additions are preserved.

---

## Contributing

Found a new Skyrim quirk? PRs welcome on [GitHub](https://github.com/WingedGuardian/skyrimvr-claude-toolkit) -- especially additions to `KNOWLEDGEBASE.md`.

## License

MIT -- see [LICENSE](LICENSE).

## Credits

- [xeditlib](https://github.com/WingedGuardian/xeditlib) -- Node.js wrapper for XEditLib.dll
- [zEdit](https://github.com/z-edit/zedit) -- Source of XEditLib.dll
- [Spriggit](https://github.com/Mutagen-Modding/Spriggit) -- ESP to YAML serialization by Mutagen
- [Spooky's AutoMod Toolkit](https://github.com/SpookyPirate/spookys-automod-toolkit) -- the CLI backend for the toolkit's NIF/BSA/audio/MCM/ESP operations
- [PyNifly](https://github.com/BadDogSkyrim/PyNifly) -- NIF read/write and animation authoring (wraps [nifly](https://github.com/ousnius/nifly))
- [PyFFI](https://github.com/niftools/pyffi) -- LE-format NIF geometry editing
- [Blender](https://www.blender.org/) and [NifSkope](https://github.com/niftools/nifskope) -- mesh repair and independent render verification
- [FallrimTools / ReSaver](https://www.nexusmods.com/skyrimspecialedition/mods/5031) -- save-file (.ess) parsing library driven headlessly by the ReSaver CLI
- [DevBench](https://www.nexusmods.com/skyrimspecialedition/mods/181326) by alandtse ([source](https://github.com/alandtse/devbench)) -- the live in-game REST/MCP channel the `devbench-cli.sh` wrapper drives
- [@aaronputty](https://github.com/aaronputty) ([fork](https://github.com/aaronputty/putty-skyrim-claude-toolkit)) -- the MO2 path-detection insight that shaped v3.4, and the devcontainer + `docs/container-vs-windows.md` foundation for v3.5
- [Claude Code](https://claude.ai/code) by Anthropic
