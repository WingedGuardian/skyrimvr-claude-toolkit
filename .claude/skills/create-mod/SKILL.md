---
name: create-mod
description: Guided workflow for creating a new Skyrim mod from scratch using AutoMod CLI. Use when the user wants to build a new mod.
argument-hint: "[mod description]"
---

# Create New Mod Workflow

Build a mod step-by-step using the AutoMod CLI. Always use `--json` on all commands. Always use `--dry-run` first on write commands.

## Step 1: Create the Plugin

```bash
bash tools/automod-cli.sh esp create "<ModName>" --output "Data" --author "<author>" --description "<desc>" --json
```
Use `--light` for ESL-flagged plugins (if under 2048 records).

## Step 2: Add Records

Use the appropriate `esp add-*` command for each record:
- Weapons: `esp add-weapon <esp> <editorId> --type <type> --model <preset> --damage <n> --dry-run --json`
- Spells: `esp add-spell <esp> <editorId> --type <type> --effect <preset> --magnitude <n> --dry-run --json`
- Armor: `esp add-armor <esp> <editorId> --type <type> --slot <slot> --model <preset> --dry-run --json`
- NPCs: `esp add-npc <esp> <editorId> --name "<name>" --level <n> --dry-run --json`
- Books: `esp add-book <esp> <editorId> --name "<name>" --text "<content>" --dry-run --json`
- Perks: `esp add-perk <esp> <editorId> --name "<name>" --effect <preset> --dry-run --json`
- Globals: `esp add-global <esp> <editorId> --type float --value <n> --dry-run --json`

**Important**: Weapons and armor REQUIRE `--model` or they'll be invisible. Spells REQUIRE `--effect` or they'll do nothing.

Always preview with `--dry-run` first, then remove it after user approves.

## Step 3: Add Scripts (if needed)

1. Write the `.psc` source file
2. Compile: `bash tools/automod-cli.sh papyrus compile <source> --output Data/Scripts --headers tools/automod/skyrim-script-headers --json`
   (Or use our existing Caprica: `tools/Caprica/Caprica.exe --game skyrim --import "Data/Scripts/Source" --flags "TESV_Papyrus_Flags.flg" --output "Data/Scripts" "script.psc"`)
3. Attach to quest: `bash tools/automod-cli.sh esp attach-script <esp> --quest <editorId> --script <name> --json`
4. Auto-fill properties: `bash tools/automod-cli.sh esp auto-fill <esp> --quest <editorId> --script <name> --script-dir Data/Scripts/Source --data-folder Data --json`

## Step 4: Generate SEQ File (if quest has start-enabled flag)

```bash
bash tools/automod-cli.sh esp generate-seq <esp> --json
```

## Step 5: Verify

```bash
bash tools/automod-cli.sh esp info <esp> --json
```

Review the record counts and verify everything was added correctly.

## Safety Reminders

- All effects on a spell must have the same casting type
- ESL FormIDs must be in xx000800-xx000FFF range
- Use `GetFormFromFile()` in scripts to avoid complex VMAD properties
- Test in-game before distributing
