---
name: skyrim-save
description: Read, scan and clean Skyrim save files (.ess), including ReSaver CLI writes (reset-havok, cleanse-formlists, remove-created). Use when debugging save issues, searching for orphaned scripts, checking mod footprint in saves, investigating save bloat, or cleaning or repairing a save.
paths: "**/*.ess"
---

# Save File Analysis

Use `scripts/read-save.py` to decompress and scan Skyrim save files. Requires Python with `lz4`, pinned and hash-locked (from the toolkit root: `python -m pip install --only-binary=:all: --require-hashes -r tools/requirements-tools.lock`). All output is JSON.

## Commands

**Get save info (player name, level, location, plugin count):**
```bash
python scripts/read-save.py info "<path-to-save.ess>"
```

**List all plugins in the save:**
```bash
python scripts/read-save.py plugins "<path-to-save.ess>"
```

**Search for a string (script names, EditorIDs, variable names):**
```bash
python scripts/read-save.py search "<save.ess>" --string "MyModScriptName"
```

**Search for a FormID (as hex, little-endian uint32):**
```bash
python scripts/read-save.py search "<save.ess>" --formid 0x0001ABCD
```

**Search for a raw hex byte pattern:**
```bash
python scripts/read-save.py search "<save.ess>" --hex "DEADBEEF"
```

## Common Debugging Use Cases

- **Orphaned scripts**: Search for a script name from a removed mod. If it still appears, the save has orphaned instances.
- **Effect accumulation**: Search for a spell's FormID and count occurrences. More than expected = stacking bug.
- **Mod footprint**: Search for a mod's prefix (e.g., "MyMod_") to see how many references it left in the save.
- **Save bloat**: Compare `decompressedSize` across saves to detect growth over time.

## Save File Structure

SSE/VR saves use LZ4 compression. General layout of the decompressed data:
- **0-100KB**: Header, plugin list, screenshot
- **100KB-~22MB**: Global data tables, ChangeForms (binary)
- **~22MB+**: Papyrus string table, script instances, FormID arrays

## Limitations

- **The binary scanner is read-only** — it cannot write back valid saves (to change a save, use the ReSaver CLI below)
- **Binary search only** — cannot parse structured Papyrus instance data (variable values, active effect state)
- For structured save editing, use [ReSaver](https://www.nexusmods.com/skyrimspecialedition/mods/5031) (Java GUI)

## ReSaver CLI (headless, `tools/resaver-cli.sh`)

`bash tools/resaver-cli.sh <op> <save.ess>` runs ReSaver's library without its GUI. Needs JDK 17+ and ReSaver's jar (see `docs/installing-tools.md`).

- **Read ops:** `info|dump|find|find-refs|worries|recon|changeform|extradata-scan|changeform-diff|freeze-report|globaldata|globaldata-diff`. The op list with arguments is the header comment of `tools/resaver-cli.sh`.
- **Write ops:** `set-global|set-var|clean|reset-havok|cleanse-formlists|remove-created`. Each is a dry run unless `--apply`, and always writes a NEW file.
- `verify-roundtrip` is a self-test. Every `--apply` is verify-gated: the file is re-read and must equal the model, or the output is deleted and the op fails.
- Resolve FormID to EditorID with `tools/resaver-resolve-names.js`.
