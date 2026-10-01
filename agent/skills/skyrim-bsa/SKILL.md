---
name: skyrim-bsa
description: Read, extract, create, and modify BSA/BA2 archives using AutoMod CLI.
paths: "**/*.bsa,**/*.ba2"
---

# BSA/BA2 Archive Operations

Use the AutoMod CLI for archive work. Always use `--json` for output.

```bash
bash tools/automod-cli.sh archive <command> [args] --json
```

## Read-Only Commands

- `archive info <archive>` — metadata and stats
- `archive list <archive> [--filter <pattern>] [--limit <n>]` — list contents
- `archive validate <archive>` — integrity check
- `archive diff <archive1> <archive2>` — compare two archives
- `archive status` — verify BSArch tool availability

## Write Commands (always confirm with user first)

- `archive extract <archive> --output <dir>` — extract all files
- `archive extract-file <archive> <file> --output <dir>` — extract single file
- `archive create <dir> --output <archive> [--game Skyrim]` — create new BSA
- `archive add-files <archive> <dir> [--base-dir <path>]` — add files to existing archive
- `archive remove-files <archive> --filter <pattern>` — remove files by pattern
- `archive update-file <archive> <file-path> <archive-path>` — replace single file
- `archive replace-files <archive> <source-dir>` — bulk replace
- `archive merge <archive1> <archive2> [...] --output <path>` — merge archives
- `archive optimize <archive> [--output <path>]` — optimize compression

## Key Reminder

**Loose files always override BSAs.** Check for loose file conflicts in `Data/` before assuming BSA content is what the game loads.
