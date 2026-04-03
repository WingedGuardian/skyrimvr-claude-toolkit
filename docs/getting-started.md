# Getting Started -- Detailed Guide

This is the extended version of the setup instructions. If the README was enough, you don't need this. This guide is for people who want more detail at each step.

---

## What You're Setting Up

You're giving Claude Code (an AI assistant) a "brain upgrade" for Skyrim modding. After setup, you can talk to it in plain English and it will:

- Know 600+ Skyrim quirks, pitfalls, and workarounds (including VR-specific sections)
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

**Option A: Desktop App (if you've never used a terminal before)**

1. Go to [claude.ai/code](https://claude.ai/code)
2. Click the Windows download button
3. Run the installer (just click Next through everything)
4. Open "Claude Code" from your Start menu
5. Sign in with your Claude account

**Option B: Command Line Install**

1. Install Node.js first:
   - Go to [nodejs.org](https://nodejs.org)
   - Click the big green **LTS** button
   - Run the installer, click Next through everything
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

The toolkit files blend in alongside your existing game files. Nothing is overwritten -- the toolkit only adds new files (CLAUDE.md, KNOWLEDGEBASE.md, setup.sh, and the .claude/ folder).

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

Copy this entire block and paste it into Claude Code:

```
I just installed the Skyrim Claude Code Modding Toolkit into this folder. Run "bash setup.sh" to set everything up. Install any missing prerequisites (jq, Node.js) for me. After setup, ask me which optional modding tools I'd like (xeditlib, Champollion, Caprica, Spriggit, AutoMod CLI) and install the ones I pick. Be sure to tailor the environment specifically to my Skyrim version and install (may or may not be VR). Explain everything in plain English and ask me any questions you may need to.
```

Press Enter. Claude will:

1. **Run the setup script** -- configures all the safety hooks and paths
2. **Install jq** if you don't have it (a small tool the hooks need)
3. **Offer optional tools** and explain what each does:
   - **xeditlib** -- lets Claude read/create ESP mod files with code
   - **Champollion** -- decompiles Papyrus scripts so you can read them
   - **Caprica** -- compiles Papyrus scripts you write
   - **Spriggit** -- converts ESP files to readable text
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

## Troubleshooting

**"Claude Code won't start"**
- Make sure you have an active Claude Pro or Max subscription
- Try reinstalling: `npm install -g @anthropic-ai/claude-code`

**"jq not found" or hooks aren't working**
- Open Windows Terminal and run: `winget install jqlang.jq`
- Close and reopen Claude Code

**"setup.sh not found"**
- Make sure you extracted the toolkit into your Skyrim folder (Step 3)
- Make sure Claude Code is running in that folder (Step 4)

**Something else?**
- Just ask Claude: *"Something went wrong with my toolkit setup. Can you help me fix it?"*
