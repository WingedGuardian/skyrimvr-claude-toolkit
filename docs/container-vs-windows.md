# Container vs. Windows — Tool Routing Guide

Credit: this file's core insight — and the devcontainer this toolkit ships — originated in
[@aaronputty](https://github.com/aaronputty)'s fork,
[putty-skyrim-claude-toolkit](https://github.com/aaronputty/putty-skyrim-claude-toolkit).

Default to the container for any task. Only cross to Windows when the task genuinely requires the
Windows DLL or an active MO2 virtual filesystem.

## Decision Table

| Task | Where | Tool |
|---|---|---|
| Validate plugin metadata (masters, FormIDs, overlap) | Container | Spriggit YAML + Python |
| Inspect ESP records | Container | `spriggit serialize` → YAML |
| Diff two ESPs | Container | `spriggit serialize` both → `diff -r` |
| Author new ESP records | Container | Spriggit YAML → `spriggit deserialize` |
| Generate FOMOD XML | Container | Node.js (`fast-xml-parser`) |
| Validate JSON schemas | Container | Node.js (`ajv`, `zod`) |
| Unit test mod logic | Container | `pytest` |
| Parse a `.ess` save | Container | ReSaver CLI (JDK 17 is in the image) |
| Read INI / load order files | Container | `SKYRIM_PROFILE_DIR` mount |
| Read an installed mod's files | Container | `SKYRIM_MODS_DIR` mount |
| Load-order-dependent ESP edits | Windows (MO2) | xelib (`examples/inspect-esp.js`, etc.) |
| Override chain resolution across the full load order | Windows (MO2) | xelib |
| Decompile Papyrus `.pex` → `.psc` | Windows | Champollion |
| Compile Papyrus `.psc` → `.pex` | Windows | Caprica |
| NIF authoring / animation / render-verify | Windows | PyFFI, PyNifly, Blender, NifSkope |
| Live in-game testing | Windows (game running) | DevBench |

## Why xelib is Windows-only

`XEditLib.dll` is a Delphi-compiled Windows DLL — it cannot run in a Linux container at all.

On an **MO2** install there's a second reason: xelib resolves plugins from the game path, and MO2's
merged load order only exists as a *virtual* filesystem while MO2 is actively running something
through it. Launched outside MO2, xelib sees only the plugins physically present in the stock
`Data/` folder — and **it does not error**. It silently returns a wrong-but-plausible answer for
anything involving the override chain or the full load order (see "Mod Manager Layout" in
`KNOWLEDGEBASE.md`). Run those scripts through MO2's executables list.

On a **stock/Vortex** install this second problem doesn't apply — `Data/` really is the merged
view — but xelib is still Windows-only for the DLL reason above.

## Why the container works anyway

The container's mounts (`SKYRIM_MODS_DIR`, `SKYRIM_PROFILE_DIR`, `SKYRIM_OVERWRITE_DIR`) give
read-only access to the real, per-mod files regardless of manager:

- **MO2**: mounted straight from `<instance>/mods/`, `<instance>/profiles/<profile>/`, and
  `<instance>/overwrite/` — no MO2 process needs to be running for the container to read them.
- **Stock/Vortex**: `Data/` already is the merged view, so `SKYRIM_MODS_DIR` and
  `SKYRIM_OVERWRITE_DIR` both point at it, and `SKYRIM_PROFILE_DIR` points at the INI folder.

`setup.sh` resolves which case applies and fills in `.devcontainer/devcontainer.json` accordingly —
see the "Mod Manager Layout" section of `KNOWLEDGEBASE.md` for the detection details.

Spriggit and ReSaver read files by path, so they work identically either way: they never need the
MO2 VFS or a running game, only a path to the file.

### One confirmed Spriggit-in-container limitation

Spriggit works in the container for the overwhelming majority of mod plugins — verified against a
real third-party ESP with actual records. But **a plugin using localized strings** (a `TranslatedString`
field, the `Localized` flag — common in vanilla ESMs and some total-overhaul mods) **fails to serialize
in the container** with a Mutagen `ArgumentNullException` deep in its BSA/load-order resolution path
(`PluginListingsPathProvider.Get` has no default plugins.txt location on Linux, and passing
`--DataFolder` doesn't change this). If Spriggit throws that way on a plugin that should be simple,
check whether it has the Localized flag — if so, serialize it on Windows instead.

## Spriggit vs. xelib

| Capability | Spriggit | xelib |
|---|---|---|
| Read records | Yes (YAML) | Yes |
| Write records | Yes (YAML round-trip) | Yes |
| Resolve override chain | No — single file only | Yes (needs the MO2 VFS if on MO2) |
| Runs in the container | Yes | No (Windows DLL) |
