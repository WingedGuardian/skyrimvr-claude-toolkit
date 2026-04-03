---
name: skyrim-save
description: Read and scan Skyrim save files (.ess). Use when debugging save issues, searching for orphaned scripts, checking mod footprint in saves, or investigating save bloat.
paths: "**/*.ess"
---

# Save File Analysis

Use `scripts/read-save.py` to decompress and scan Skyrim save files. Requires Python with `lz4` package (`pip install lz4`). All output is JSON.

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

- **Read-only** — cannot edit or write back valid saves
- **Binary search only** — cannot parse structured Papyrus instance data (variable values, active effect state)
- For structured save editing, use [ReSaver](https://www.nexusmods.com/skyrimspecialedition/mods/5031) (Java GUI)
