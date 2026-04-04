# Skyrim Claude Code Modding Toolkit

An AI-assisted Skyrim modding environment for power users. Claude Code handles the mechanical work — porting mods across versions, inspecting and editing ESPs, debugging scripts, and building simple mods from scratch — with safety hooks, pre-loaded engine knowledge, and every tool pre-configured.

Built from hundreds of hours of hands-on Skyrim VR mod development. **[Though this env was built with VR in mind, it can be just as powerful in any Skyrim version. Claude Code is the brain.]**

> **This isn't just a guide -- it's a complete environment with all the setup prework already done.** You just need to install, and begin building the mod of your dreams! (Or ironing out all the bugs in your existing setup 😛)

---

## What Is Claude Code?

[Claude Code](https://claude.ai/code) is an AI assistant by Anthropic that runs directly on your computer. Unlike ChatGPT or regular Claude chat, it can **read your files, run commands, edit configs, execute scripts -- and do the mechanical work of modding for you** -- all with your permission. Think of it as having a modding expert sitting next to you who can actually touch your files.

Most people who try to use Claude Code for modding spend days figuring out tool integrations, fighting weird Delphi DLL quirks, learning what Claude needs to know to be useful, and building safety guardrails so it doesn't break anything. This toolkit ships with all of that already solved. You get a working environment on day one.

It's not perfect, and it will require some trial and error — especially for complex mods from scratch. But for the tedious parts of modding, it's significantly faster than doing it yourself.

---

## What You Get

This isn't a guide that tells you what to install and configure yourself. It's a **complete, ready-to-run modding environment** -- every tool pre-calibrated, every known Skyrim quirk already documented, every footgun already identified and protected against. Extract it into your game folder, paste one prompt, and you're working.

### A Toolset Built and Tested for Skyrim

Skyrim modding is full of undocumented engine quirks, version-specific differences, and tools that silently fail. This toolkit was built and tested on a live **Skyrim VR** install, but the knowledge, hooks, and scripts work across all versions — SE, AE, VR, and even LE where applicable. The knowledgebase includes version-specific sections so Claude knows what differs and what doesn't.

The clearest example: **xeditlib**. XEditLib.dll is the engine inside SSEEdit/xEdit -- the most powerful ESP editing tool in the Skyrim modding ecosystem. Getting it working from Node.js (so Claude Code could actually read and write ESP files) required cracking open the Delphi FFI layer and fixing a cascade of subtle bugs: strings encoded as UCS-2 instead of UTF-8, `InitXEdit()` silently corrupting the call stack when declared wrong, booleans that are actually 2-byte integers, a non-obvious two-step string-return pattern. None of this is documented anywhere. We debugged it, fixed it, and published the working wrapper as [xeditlib](https://github.com/WingedGuardian/xeditlib) on npm so you never have to deal with any of it. Claude Code can now read any ESP file and write new ones -- something that wouldn't work at all before this toolkit.

### Everything Included

- **600+ lines of Skyrim modding knowledge** -- Papyrus quirks, version-specific differences, xEdit pitfalls, engine bugs, and more (including VR-specific sections). Loaded into every Claude session automatically.
- **Safety hooks** -- Claude asks permission before editing any game file, won't touch ESP/ESM files directly, and automatically backs up everything it modifies with a full audit trail.
- **Confidence system** -- Claude rates its confidence (0-100%) and lists its assumptions before proposing any change. No guessing, no "this should work."
- **ESP editing via Spriggit** -- Serialize any ESP to human-readable YAML, edit it directly, deserialize back. Claude's native file editing works on YAML out of the box — no FFI layer, no scripting, and changes diff cleanly in git.
- **ESP analysis via xeditlib** -- Programmatic inspection, diffing, and bulk queries across records. The hard Delphi FFI work is already done. ([xeditlib on GitHub](https://github.com/WingedGuardian/xeditlib))
- **NIF mesh tools** -- Inspect, retexture, scale, fix eye-ghosting, and verify mesh files. Detect VR-breaking skeleton nodes.
- **BSA archive tools** -- Full read/write/merge/diff on BSA archives. Extract individual files, create new archives, update contents.
- **Audio processing** -- Extract, convert, and create Skyrim voice files (FUZ/XWM/WAV).
- **MCM menu generation** -- Programmatically create SkyUI mod configuration menus with toggles, sliders, and pages.
- **Save file analysis** -- Decompress and binary-scan .ess saves. Search for orphaned scripts, count effect accumulation, check mod footprint, detect save bloat.
- **Dry-run workflow** -- All ESP and asset changes go through a preview pass first. Claude shows you exactly what it will do before touching anything.
- **Claude Code skills** -- Slash commands like `/inspect-esp MyMod.esp`, `/port-to-vr`, and `/create-mod` that trigger guided workflows. Auto-loading context that injects critical Skyrim gotchas when Claude works with game files.
- **Auto-setup** -- One prompt installs prerequisites, configures paths, sets up hooks, and optionally installs modding tools (Champollion, Caprica, Spriggit, AutoMod CLI). No manual configuration.

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

Copy this entire line and paste it into Claude Code:

```
I just installed the Skyrim Claude Code Modding Toolkit into this folder. Run "bash setup.sh" to set
  everything up. Install any missing prerequisites (jq, Node.js) for me. After setup, ask me which optional modding
  tools I'd like (xeditlib, Champollion, Caprica, Spriggit, AutoMod CLI) and install the ones I pick. AutoMod CLI adds
  NIF mesh editing, BSA archive tools, audio processing, and MCM menu generation. Be sure to tailor the environment
  specifically to my Skyrim version and install (may or may not be VR). Explain everything in plain English and ask me any
  questions you may need to.
```

Claude handles the rest. It will configure paths, install dependencies, set up the safety hooks, and walk you through optional tool installation. Just answer any questions it asks.

**That's it. You're done.**

---

## Using It

From now on, whenever you open Claude Code in your Skyrim folder, the full environment loads automatically -- knowledge base, safety hooks, tool integrations, everything. No setup required each session. Just start talking.

This toolkit fits best as a **power user tool** — particularly strong for investigating, porting, and debugging existing mods, making targeted record edits, and scripting assistance. For simpler mods (spells, powers, item records, short scripts), Claude can build these from scratch. For complex systems, expect some iteration.

---

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

If it involves Skyrim, Papyrus, ESPs, INI files, scripts, or mod files of any kind, just ask. Claude has the full context of how the engine works and will figure out the path forward. It's significantly faster than doing it yourself — especially for the tedious parts.

---

## What's in the Knowledgebase?

Other AI modding setups make you feed Claude information manually or re-explain the same quirks every session. This toolkit ships with a pre-loaded `KNOWLEDGEBASE.md` -- 600+ lines of documented knowledge that Claude reads automatically at the start of every session:

| Topic | What's Covered |
|-------|---------------|
| **Papyrus Scripting** | Script lifecycle, threading, RemoveSpell vs DispelSpell, Wait() reliability, magic effects, performance pitfalls |
| **Version-Specific Differences** | SKSE versions, skeleton issues, camera, physics, UI, input, mod framework compatibility (with VR-specific sections) |
| **xEdit / ESP Editing** | VMAD fragility, plugin types (ESM/ESP/ESL), load order, BSA priority, navmesh, cleaning caveats |
| **Engine Quirks** | Ability spells, vanilla bugs, SKSE plugin compatibility warnings |
| **VR Controller Input** | SKSE Input API limitations in VR, VRIK API as the correct method, code examples (VR section) |
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

---

## FAQ

**Q: Does this work with flat Skyrim SE (non-VR)?**
A: Yes! The knowledgebase covers both. VR-specific sections only apply to VR. Safety hooks and workflow work for either. Just tell Claude Code to adapt your enviornment to your specific Skyrim version. 

**Q: Can Claude break my mods or save files?**
A: The safety hooks prevent this. Claude can't edit ESP/ESM files directly, must ask permission for any edit, and backs everything up. But always keep your own backups too.

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
- [Spooky's AutoMod Toolkit](https://github.com/SpookyPirate/spookys-automod-toolkit) -- Inspiration for expanded CLI capabilities
- [Claude Code](https://claude.ai/code) by Anthropic
