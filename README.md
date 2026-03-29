# Skyrim VR Claude Code Modding Toolkit

Turn your Skyrim VR install into an AI-assisted modding workshop. Claude Code becomes an automated mod expert who can build you nearly any mod you want! It naturally understands Papyrus scripting, VR-specific quirks, ESP editing, and protects your game files from accidental damage.

Built from hundreds of hours of hands-on Skyrim VR mod development.

> **This isn't just a guide -- it's a complete environment with all the setup prework already done.** You just need to install, and begin building the mod of your dreams!

---

## What Is Claude Code?

[Claude Code](https://claude.ai/code) is an AI assistant by Anthropic that runs directly on your computer. Unlike ChatGPT or regular Claude chat, it can **read your files, run commands, edit configs, execute scripts--and literally build mods FOR you** -- all with your permission. Think of it as having a modding expert sitting next to you who can actually touch your files.

Most people who try to use Claude Code for modding spend days figuring out tool integrations, fighting weird Delphi DLL quirks, learning what Claude needs to know to be useful, and building safety guardrails so it doesn't break anything. This toolkit ships with all of that already solved. You get a working environment on day one.

---

## What You Get

This isn't a guide that tells you what to install and configure yourself. It's a **complete, ready-to-run modding environment** -- every tool pre-calibrated, every known Skyrim VR quirk already documented, every footgun already identified and protected against. Extract it into your game folder, paste one prompt, and you're working.

### A Toolset Built and Tested for Skyrim VR

Standard modding tools don't just work for VR out of the box. Mods crash. Scripts behave differently. SKSE addresses don't match. The physics run at the wrong framerate. Tools written for SSE silently fail. This toolkit is calibrated specifically for **Skyrim VR** -- every piece of knowledge, every hook, every script was built and verified against a live VR install.

The clearest example: **xeditlib**. XEditLib.dll is the engine inside SSEEdit/xEdit -- the most powerful ESP editing tool in the Skyrim modding ecosystem. Getting it working from Node.js (so Claude Code could actually read and write ESP files) required cracking open the Delphi FFI layer and fixing a cascade of subtle bugs: strings encoded as UCS-2 instead of UTF-8, `InitXEdit()` silently corrupting the call stack when declared wrong, booleans that are actually 2-byte integers, a non-obvious two-step string-return pattern. None of this is documented anywhere. We debugged it, fixed it, and published the working wrapper as [xeditlib](https://github.com/WingedGuardian/xeditlib) on npm so you never have to deal with any of it. Claude Code can now read any ESP file and write new ones -- something that wouldn't work at all before this toolkit.

### Everything Included

- **600+ lines of Skyrim VR modding knowledge** -- Papyrus quirks, VR vs SSE differences, xEdit pitfalls, engine bugs, VRIK controller input, and more. Loaded into every Claude session automatically.
- **Safety hooks** -- Claude asks permission before editing any game file, can't touch ESP/ESM files directly, and automatically backs up everything it modifies with a full audit trail.
- **Confidence system** -- Claude rates its confidence (0-100%) and lists its assumptions before proposing any change. No guessing, no "this should work."
- **ESP scripting via xeditlib** -- Programmatic ESP inspection, diffing, and creation. The hard FFI work is already done. ([xeditlib on GitHub](https://github.com/WingedGuardian/xeditlib))
- **Dry-run workflow** -- All ESP changes go through a read-only preview pass first. Claude shows you exactly what it will do before touching anything.
- **Auto-setup** -- One prompt installs prerequisites, configures paths, sets up hooks, and optionally installs modding tools (Champollion, Caprica, Spriggit). No manual configuration.

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

### Step 2: Extract This Toolkit Into Your Skyrim VR Folder

1. Download this mod from Nexus (Manual Download)
2. Find your Skyrim VR folder:
   - Open **Steam** > **Library** > right-click **Skyrim VR** > **Properties** > **Installed Files** > **Browse**
   - A folder opens -- this is your Skyrim VR folder
3. **Extract the zip directly into that folder**
   - Right-click the downloaded zip > Extract All > paste your Skyrim VR folder path > Extract
   - The files blend in alongside your existing game files (nothing is overwritten)

### Step 3: Open Claude Code in Your Skyrim VR Folder

**Desktop App:** Open Claude Code. Click the folder/path area and navigate to your Skyrim VR folder. Or type this (replace with YOUR path):
```
cd "C:\Steam\steamapps\common\SkyrimVR"
```

**Command Line:** Open Windows Terminal and type:
```
cd "C:\Steam\steamapps\common\SkyrimVR"
claude
```

> **Tip:** In the Steam browse window from Step 2, click the address bar and copy the path. Paste it after `cd `.

### Step 4: Paste This Prompt

Copy this entire line and paste it into Claude Code:

```
I just installed the Skyrim VR Claude Code Modding Toolkit into this folder. Run "bash setup.sh" to set everything up. Install any missing prerequisites (jq, Node.js) for me. After setup, ask me which optional modding tools I'd like (xeditlib, Champollion, Caprica, Spriggit) and install the ones I pick. Explain everything in plain English -- I may be new to command-line tools.
```

Claude handles the rest. It will configure paths, install dependencies, set up the safety hooks, and walk you through optional tool installation. Just answer any questions it asks.

**That's it. You're done.**

---

## Using It

From now on, whenever you open Claude Code in your Skyrim VR folder, the full environment loads automatically -- knowledge base, safety hooks, tool integrations, everything. No setup required each session. Just start talking:

**Investigating mods:**
- *"What does the mod at nexusmods.com/skyrimspecialedition/mods/12345 do?"*
- *"Decompile Data/Scripts/MyScript.pex and explain what it does"*
- *"Inspect the records in Data/MyMod.esp"*

**Troubleshooting:**
- *"I'm getting a CTD when I equip this weapon in VR. What could cause that?"*
- *"Check my SkyrimVR.ini for settings that might cause problems"*
- *"NPC dialogue stopped showing up after I installed a mod. Help me debug it."*

**Making changes:**
- *"Set fUpdateBudgetMS to 2.0 in SkyrimVR.ini"*
- *"Help me create an ESP that adds a new spell"*
- *"Port this SSE Papyrus script to work in VR"*

**Learning:**
- *"What's the difference between RemoveSpell and DispelSpell?"*
- *"How does the Papyrus threading model work?"*
- *"What mods are known to break in VR?"*

---

## What's in the Knowledgebase?

Other AI modding setups make you feed Claude information manually or re-explain the same quirks every session. This toolkit ships with a pre-loaded `KNOWLEDGEBASE.md` -- 600+ lines of documented knowledge that Claude reads automatically at the start of every session:

| Topic | What's Covered |
|-------|---------------|
| **Papyrus Scripting** | Script lifecycle, threading, RemoveSpell vs DispelSpell, Wait() reliability, magic effects, performance pitfalls |
| **VR vs SSE Differences** | SKSE versions, skeleton CTDs, camera, physics (60Hz vs 90Hz), UI, input, mod compatibility matrix |
| **xEdit / ESP Editing** | VMAD fragility, plugin types (ESM/ESP/ESL), load order, BSA priority, navmesh, cleaning caveats |
| **Engine Quirks** | Ability spells, vanilla bugs, SKSE plugin compatibility warnings |
| **VR Controllers** | Why SKSE Input API fails in VR, VRIK API as the correct method, code examples |
| **Debugging** | Debug.Notification limitations, Debug.Trace patterns, concurrent script handling |

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
A: Yes! The knowledgebase covers both. VR-specific sections only apply to VR. Safety hooks and workflow work for either.

**Q: Can Claude break my mods or save files?**
A: The safety hooks prevent this. Claude can't edit ESP/ESM files directly, must ask permission for any edit, and backs everything up. But always keep your own backups too.

**Q: I'm getting "jq not found"**
A: Open Windows Terminal and run: `winget install jqlang.jq` -- then restart Claude Code.

**Q: How do I update the toolkit?**
A: Download the new version from Nexus and extract over the old one. Your knowledgebase additions are preserved.

---

## Contributing

Found a new Skyrim VR quirk? PRs welcome on [GitHub](https://github.com/WingedGuardian/skyrimvr-claude-toolkit) -- especially additions to `KNOWLEDGEBASE.md`.

## License

MIT -- see [LICENSE](LICENSE).

## Credits

- [xeditlib](https://github.com/WingedGuardian/xeditlib) -- Node.js wrapper for XEditLib.dll
- [zEdit](https://github.com/z-edit/zedit) -- Source of XEditLib.dll
- [Claude Code](https://claude.ai/code) by Anthropic
