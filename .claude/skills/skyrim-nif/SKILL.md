---
name: skyrim-nif
description: Inspect and modify NIF mesh files using AutoMod CLI. Use when working with meshes, textures, skeleton nodes, or fixing VR mesh issues.
paths: "**/*.nif,Data/meshes/**"
---

# NIF Mesh Operations

Use the AutoMod CLI for all mesh work. Always use `--json` for output.

```bash
bash tools/automod-cli.sh nif <command> [args] --json
```

## Read-Only Commands

- `nif info <path>` — file version, size, structure
- `nif list-textures <path>` — all texture references (supports recursive folder)
- `nif list-strings <path>` — all string entries / node names
- `nif shader-info <path>` — shader property details
- `nif verify <path>` — integrity check

## Write Commands (always confirm with user first)

- `nif replace-textures <path> --old <str> --new <str> [--dry-run] [--backup]` — batch retexture
- `nif rename-strings <path> --old <str> --new <str> [--dry-run] [--backup]` — rename nodes
- `nif fix-eyes <path> [--dry-run] [--backup]` — fix FaceGen eye ghosting
- `nif scale <path> <factor> [--output <path>]` — resize mesh
- `nif restore <path>` — restore from .nif.bak backup

## VR-Critical Mesh Issues

- **PreWEAPON and PreSHIELD skeleton nodes cause CTD in VR** — use `nif list-strings` to check for these, then `nif rename-strings` to remove the "Pre" prefix or delete the nodes
- VR does a text-contains search for WEAPON/SHIELD nodes and gets confused by Pre* prefixes
- XP32 First Person Skeleton CTD Bugfix is critical for custom skeleton users in VR
