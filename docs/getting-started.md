# Getting Started -- Detailed Guide

This is the extended version of the setup instructions. If the README was enough, you don't need this. This guide is for people who want more detail at each step.

---

## What You're Setting Up

You're giving Claude Code (an AI assistant) a "brain upgrade" for Skyrim modding. After setup, you can talk to it in plain English and it will:

- Know 1,300+ Skyrim quirks, pitfalls, and workarounds (including VR-specific sections)
- Protect your game files from accidental damage
- Decompile and analyze Papyrus scripts
- Inspect ESP/ESM mod files
- Help you create new mods from scratch
- Research Nexus Mods pages for known issues
- Edit INI settings safely with automatic backups

---

## Step 1: Get Claude Code

### What is it?

Claude Code is made by Anthropic (the company behind Claude AI). It's like ChatGPT, but instead of just talking, it can actually read and edit files on your computer. It runs in a terminal window -- think of it like a very smart command prompt.

### Sign up and subscribe

1. Go to [claude.ai](https://claude.ai)
2. Create an account (email or Google)
3. Subscribe to **Claude Pro** ($20/month) or **Claude Max** ($100/month)
   - Pro is plenty for modding work
   - You can cancel anytime

### Install it

The toolkit's safety hooks are tested on the Claude Code **command line** (Option B). The desktop app is not a
surface the toolkit claims (`ADAPTING.md`, section 8), so prefer Option B if you can.

**Option A: Desktop App (if you've never used a terminal before; not covered by the toolkit's hook testing)**

1. Go to [claude.ai/code](https://claude.ai/code)
2. Click the Windows download button
3. Run the installer (just click Next through everything)
4. Open "Claude Code" from your Start menu
5. Sign in with your Claude account

**Option B: Command Line Install**

1. Install Node.js first:
   - For you to run: open Windows Terminal (step 2 shows how) and run `winget install --id OpenJS.NodeJS.LTS --exact --version 24.19.0 --no-upgrade`
     (Node.js 24.19.0 is the version this toolkit was tested with; winget checks the installer's hash)
2. Open **Windows Terminal**:
   - Press the **Windows key** on your keyboard
   - Type `terminal`
   - Click **Terminal** in the search results
3. In the terminal window, type this and press Enter:
   ```
   npm install -g @anthropic-ai/claude-code
   ```
4. Wait for it to finish (takes about a minute)

---

## Step 2: Find Your Skyrim Folder

1. Open **Steam**
2. Click **Library** (at the top)
3. Find **Skyrim VR** (or **Skyrim Special Edition**) in your game list
4. Right-click it > **Properties**
5. Click **Installed Files** (on the left side)
6. Click **Browse...**
7. A Windows Explorer window opens -- **this is your Skyrim folder**
8. Click the **address bar** at the top of that window (where it shows the folder path)
9. The path turns into selectable text -- **copy it** (Ctrl+C)

Write this path down or keep the window open. You'll need it in the next steps.

Common paths look like:
- `C:\Steam\steamapps\common\SkyrimVR`
- `D:\SteamLibrary\steamapps\common\SkyrimVR`
- `C:\Program Files (x86)\Steam\steamapps\common\SkyrimVR`

---

## Step 3: Extract the Toolkit

1. Download the toolkit from Nexus Mods (click **Manual Download**)
2. Find the downloaded `.zip` file (usually in your Downloads folder)
3. Right-click the zip file
4. Click **Extract All...**
5. In the "Extract to" box, **paste your Skyrim folder path** from Step 2
6. Click **Extract**

The toolkit files blend in alongside your existing game files. ⚠ Files the toolkit ships ARE replaced on a later update -- `CLAUDE.md`, `KNOWLEDGEBASE.md`, `.claude/hooks/`. Your own notes belong in `KNOWLEDGEBASE.local.md`, which the toolkit never ships. On a FIRST install it only adds new files (CLAUDE.md, KNOWLEDGEBASE.md, setup.sh, and the .claude/ folder).

---

## Step 4: Open Claude Code in Your Skyrim Folder

### Desktop App:

1. Open Claude Code
2. You'll see a text input at the bottom
3. Type this (paste YOUR path from Step 2 between the quotes):
   ```
   cd "C:\Steam\steamapps\common\SkyrimVR"
   ```
4. Press Enter

### Command Line:

1. Open Windows Terminal
2. Type this (paste YOUR path):
   ```
   cd "C:\Steam\steamapps\common\SkyrimVR"
   ```
3. Press Enter
4. Type `claude` and press Enter

You should see Claude Code's interface -- a text area where you can type messages.

---

## Step 5: Paste the Setup Prompt

Copy this entire block and paste it into your coding agent (Claude Code, Codex, ...):

```
I just installed the Skyrim modding toolkit into this folder. Read AGENTS.md (Claude Code reads CLAUDE.md, which says the same), then read the skill toolkit-setup (.agents/skills/toolkit-setup/SKILL.md; Claude Code: .claude/skills/toolkit-setup/SKILL.md) and follow it: it inventories Python, Git and Node by launching them, then you run "bash setup.sh". Host-software installs: NOT AUTHORIZED. That means: do not install, upgrade, repair or remove Python, Git, Node or any other system software (a JDK, .NET, Blender) on my computer; if something is missing or broken, tell me the exact manual steps and wait for me. (To authorize installing Python, Git or Node I will change that sentence myself, to this form: Host-software installs: AUTHORIZED python=<winget id>==<exact version>. The only winget ids it can name are Python.Python.3.<minor> (3.10 or newer) for python, Git.Git for git, and OpenJS.NodeJS or OpenJS.NodeJS.LTS for node, each at the exact version I write, installed with --no-upgrade. That sentence never covers a JDK, .NET or Blender: for those, and for anything else, you only give me manual steps.) If you are an agent other than Claude Code or Codex, read ADAPTING.md after setup and tell me honestly which protection layers you can provide and which you cannot. After setup, ask me which optional modding tools I'd like (xeditlib, Champollion, Caprica, Spriggit, AutoMod CLI, PyFFI, PyNifly, Blender, NifSkope, ReSaver CLI) and set up the ones I pick, only inside this folder (git clones or downloads into tools/), each at exactly the version docs/installing-tools.md names, checking every download's SHA-256 against its Pinned downloads table -- never a newer or older version, and stop and tell me if a hash does not match. AutoMod CLI adds NIF mesh editing, BSA archive tools, audio processing, and MCM menu generation -- it is a real tool you install by cloning https://github.com/SpookyPirate/spookys-automod-toolkit at tag v1.11.2 (commit e1c34ea, the version this toolkit tested) into tools/automod and then running bash tools/automod-cli.sh --rebuild, which checks out that commit, retargets it to the .NET 10 SDK and builds only the Cli project (never build it with a bare dotnet build; the first build needs internet), after which it runs via tools/automod-cli.sh. PyFFI + PyNifly add NIF geometry and animation/controller authoring; Blender (headless) + NifSkope add mesh repair and render-verification of meshes before in-game testing. ReSaver CLI adds headless .ess save parsing, cross-referencing, cleaning, and changeform-level diagnostics (ReSaver 6.0.643 from Nexus mod 5031 into tools/resaver-cli/, its ReSaver.jar and lib/ checked against Pinned downloads with bash tools/resaver-cli.sh verify; needs JDK 17+ (JDK 21 LTS recommended; a JDK is system software: never install it yourself, give me the manual steps)). Separately, TELL me about DevBench but do NOT install it: it is an optional dev-only SKSE plugin (Nexus mod 181326 by alandtse, no gameplay change and no save data) that runs a localhost server inside the running game, letting you inspect live state, run console commands and read their output, and call Papyrus functions -- so you can test your own fixes while I keep playing, instead of asking me to launch, trigger, and report back. Unlike everything else in that list it is a mod, not a dev tool, so if I want it I install it through my own mod manager like any other SKSE plugin -- never copy files into Data/ yourself. The wrapper tools/devbench-cli.sh is already bundled and works as soon as it's installed. The bundled cosave-info tool (tools/cosave-cli.sh, Python 3, no download) gives a read-only structural survey of an SKSE .skse co-save. Be sure to tailor the environment specifically to my Skyrim version and install (may or may not be VR). Also ask me whether I want to enable the optional Nexus API integration (mod update-detection / version & changelog checks); if yes, explain how to get a free Personal API Key and save it to tools/.nexus_api_key (which is gitignored). Explain everything in plain English and ask me any questions you may need to.
```

Press Enter. Your agent will:

1. **Check Python 3, Git and Node by launching them** -- the safety hooks need Python (jq is optional). It does
   not install any of them: if one is missing or broken it tells you the manual steps and waits
2. **Run the setup script** -- configures all the safety hooks and paths
3. **Offer optional tools** and explain what each does:
   - **xeditlib** -- lets Claude read/create ESP mod files with code
   - **Champollion / Caprica** -- decompile / compile Papyrus scripts
   - **Spriggit** -- converts ESP files to readable text (and back)
   - **AutoMod CLI** -- NIF / BSA / audio / MCM / ESP operations
   - **PyFFI / PyNifly** -- NIF geometry + animation authoring (the v3 animation/render features)
   - **Blender / NifSkope** -- mesh repair + render-verification before in-game testing
   - **ReSaver CLI** -- headless save (.ess) parsing, cross-referencing, and cleaning
   - **DevBench** -- lets Claude inspect and drive the *running* game, so it can test its own
     fixes while you play instead of asking you to launch and report back
4. **Verify everything works**
5. **Show you what you can do**

Just answer its questions as they come up. If something fails, it will explain the problem and walk you through fixing it.

---

## Step 6: You're Done!

From now on, to use the toolkit:
1. Open Claude Code
2. Navigate to your Skyrim folder (`cd "your path"`)
3. Start talking

The toolkit loads automatically every time. No re-setup needed.

---

## What's in `tools/`

These ship with the toolkit and are ready to run. You don't need to memorise them — Claude knows what
each one is for — but it helps to know what's there.

| Script | What it does |
|---|---|
| `devbench-cli.sh` | Drive the **running** game: inspect live state, run console commands and read their output, call Papyrus functions. Needs DevBench installed. |
| `esp-verify-wrapper.sh` | Snapshot a plugin's cross-references before a risky bulk edit, then verify afterwards — fails loudly if a reference was silently re-mastered or dropped. |
| `spriggit-cli.sh` | Spriggit wrapper that works around its failure on deeply-nested output paths. Use instead of calling Spriggit directly. |
| `automod-cli.sh` | AutoMod CLI wrapper (NIF / BSA / audio / MCM / ESP one-liners). Runs the prebuilt Release DLL. |
| `resaver-cli.sh` | Headless `.ess` save parsing, querying, cross-referencing, cleaning, and changeform diagnostics. |
| `resaver-resolve-names.js` | Resolve a FormID to its EditorID and record signature. |
| `cosave-cli.sh` | Read-only structural survey of an SKSE `.skse` co-save. |
| `pyffi-geometry-split.py` | Split one combined NIF mesh into separate shapes so each can take its own shader (e.g. a glowing blade with a non-glowing hilt). |
| `blender-nif-validate.py` | Headless Blender mesh validation. |
| `blender-nif-render.py` | Headless Blender render of a NIF to PNG, so a mesh fix is confirmed before you load the game. |
| `nexus.sh` | Nexus API wrapper for mod version / changelog lookups. Never prints your key. |
| `xelib/` | The xelib helpers, including the load-order loader. |

## Optional: a reproducible dev container

If you'd rather not install Python/Node/.NET/JDK onto your own machine, `bash devshell-docker.sh`
(Docker Desktop only) builds a container with all of them pre-wired and drops you into a shell — for
the tools that don't need Windows or an active MO2 session (ESP inspection/diffing, FOMOD generation,
unit tests, ReSaver CLI). `setup.sh` already pointed its mounts at your real mod files when you ran
it. See `docs/container-vs-windows.md` for what belongs there versus what still needs Windows.

## Telling the agent your preferences

Your standing instructions go in `KNOWLEDGEBASE.local.md` (in your Skyrim folder; create it if it is
missing), under a `## My preferences` heading. The agent is told to read that section at the start of
every session and follow it, and to add to it when you state a lasting preference. Updates never
replace this file. For example:

```markdown
## My preferences
- Always show me a dry run before writing to any plugin.
- I play in VR; never suggest SSE-only mods.
- Keep answers short; I know my way around xEdit.
```

## Troubleshooting

**"Claude Code won't start"**
- Make sure you have an active Claude Pro or Max subscription
- Try reinstalling: `npm install -g @anthropic-ai/claude-code`

**"GUARD INERT ... cannot run Python" or hooks aren't working**
- Install Python 3 (for you to run, in Windows Terminal: `winget install --id Python.Python.3.12 --exact --version 3.12.10 --no-upgrade`: inside the tested 3.10-3.13 range and the Python the optional tools were verified on), then run `bash setup.sh` again
- Close and reopen Claude Code

**"setup.sh not found"**
- Make sure you extracted the toolkit into your Skyrim folder (Step 3)
- Make sure Claude Code is running in that folder (Step 4)

**Something else?**
- Just ask Claude: *"Something went wrong with my toolkit setup. Can you help me fix it?"*
