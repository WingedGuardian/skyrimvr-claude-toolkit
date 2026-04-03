# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A modded Skyrim installation managed by Vortex. (Adapt paths and version-specific notes to your Skyrim version: SE, AE, VR, or LE.)

## Key Paths

- **Game root**: `{{GAME_ROOT}}/`
- **User INI configs**: `{{DOCUMENTS_DIR}}/My Games/Skyrim VR/` (Skyrim.ini, SkyrimVR.ini, SkyrimPrefs.ini)
- **Load order**: `C:/Users/{{USERNAME}}/AppData/Local/Skyrim VR/loadorder.txt` and `plugins.txt`
- **SKSE plugins**: `Data/SKSE/Plugins/`
- **Mod data**: `Data/` (ESPs, BSAs, meshes, textures, scripts)

## Installed Modding Tools

All under `tools/`:

| Tool | Purpose | Usage |
|------|---------|-------|
| **Champollion** | Decompile Papyrus `.pex` → `.psc` | `tools/Champollion/Champollion.exe input.pex` |
| **Caprica** | Compile Papyrus `.psc` → `.pex` | `tools/Caprica/Caprica.exe --game skyrim --import "Data/Scripts/Source" input.psc` |
| **XEditLib.dll** | Programmatic ESP/ESM reading via FFI | Load with koffi in Node.js (see below) |
| **Spriggit** | ESP ↔ YAML/JSON conversion (.NET) | `dotnet tool run spriggit serialize ...` |
| **AutoMod CLI** | NIF meshes, BSA archives, audio, MCM, ESP one-liners | `bash tools/automod-cli.sh <module> <command> --json` |

> **Note**: Install tools you need into a `tools/` folder in your game directory. See the [xeditlib](https://github.com/WingedGuardian/xeditlib) repo for XEditLib setup. AutoMod CLI is installed via the setup prompt (clones and builds [SpookyPirate's AutoMod Toolkit](https://github.com/SpookyPirate/spookys-automod-toolkit)).

## AutoMod CLI

A .NET CLI at `tools/automod/` for NIF meshes, BSA archives, audio, MCM menus, and quick ESP record creation. Call via wrapper:
```bash
bash tools/automod-cli.sh <module> <command> [args] --json
```

**Always use `--json`** for parseable output. **Always use `--dry-run` first** for any write command.

| Module | Key Commands | Use For |
|--------|-------------|---------|
| **nif** | `info`, `list-textures`, `replace-textures`, `fix-eyes`, `scale`, `verify` | Mesh inspection and editing |
| **archive** | `info`, `list`, `extract`, `create`, `add-files`, `diff`, `merge` | BSA/BA2 full CRUD |
| **audio** | `info`, `extract-fuz`, `create-fuz`, `wav-to-xwm` | Voice files (FUZ/XWM/WAV) |
| **mcm** | `create`, `add-toggle`, `add-slider`, `validate` | SkyUI MCM menus |
| **esp** | `add-weapon`, `add-spell`, `add-armor`, `add-npc`, `add-quest`, etc. | Quick one-liner record creation |

### When to Use Which Tool
- **AutoMod `esp`**: Quick one-liner additions. Best for simple records.
- **Spriggit**: Complex multi-field editing via YAML. Best for detailed work.
- **xeditlib**: Programmatic traversal and diffing. Best for analysis.
- **AutoMod `nif`/`archive`/`audio`/`mcm`**: Only tools available for these operations.

## XEditLib.dll API (Critical Notes)

The DLL is Delphi-compiled. These quirks caused hours of debugging:

1. **All strings are UCS-2/UTF-16LE** (Delphi `PWideChar`), never UTF-8:
   ```js
   function wcb(s) { const b = Buffer.alloc((s.length+1)*2,0); b.write(s,0,'ucs2'); return b; }
   ```

2. **`InitXEdit()` and `CloseXEdit()` are VOID**, not bool. Declaring them as bool corrupts the call stack.

3. **`WordBool` = `uint16`** (2 bytes), not bool/uint8.

4. **String return pattern**: Functions don't return strings directly. They write a length to a `PInteger` param, then you call `GetResultString(buffer, len)` to retrieve the actual value:
   ```js
   function getString(fn) {
       const lenBuf = Buffer.alloc(4, 0);
       fn(lenBuf);
       const len = lenBuf.readInt32LE(0);
       if (len < 1) return '';
       const strBuf = Buffer.alloc(len * 2, 0);
       GetResultString(strBuf, len);
       return strBuf.toString('utf16le', 0, len * 2);
   }
   ```

5. **Game mode enum**: gmFNV=0, gmFO3=1, gmTES4=2, gmTES5=3, **gmSSE=4** (use this for Skyrim VR), gmFO4=5

6. **Registry requirement**: XEditLib reads game path from `HKLM\SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition` (the SSE key, not the VR key, because game mode 4 = SSE).

7. **xelib.js wrapper**: See [xeditlib on GitHub](https://github.com/WingedGuardian/xeditlib) for the full wrapper with all 163 functions.

## INI Config Hierarchy

Settings load in this order (later overrides earlier):
1. `Skyrim.ini` -- base settings
2. `SkyrimVR.ini` -- VR-specific overrides
3. `SkyrimPrefs.ini` -- user preferences (loaded last)

## Nexus Mod Research (Standing Rule)

**Always search a mod's Nexus mod page before investigating it.** Check the description, tutorials/articles, comments, and bug reports before going in blind. This saves enormous time -- most issues have been seen and documented by other users.

## Knowledgebase

`KNOWLEDGEBASE.md` (project root) is the master reference for all discovered quirks, gotchas, and cross-version differences. **Always consult it before making changes** to avoid repeating past mistakes.

**Standing instruction**: After every debugging session, mod investigation, or web research, extract any new facts (engine quirks, VR vs SSE differences, API gotchas, tool limitations) and add them to KNOWLEDGEBASE.md. We learn from everything we come into contact with.

## Top Gotchas (Always In Context)

These are the most dangerous/common pitfalls. Consult `KNOWLEDGEBASE.md` for full details.

1. **RemoveSpell doesn't fire OnEffectFinish** -- use `DispelSpell` when cleanup logic exists
2. **All effects on a spell must have the same casting type** -- mismatches cause silent failure
3. **VMAD editing is fragile** -- use `GetFormFromFile()` to minimize properties; xEdit can't add scripts to VMAD
4. **PlayIdle fails in VR** -- VRIK overrides skeleton IK; bypass with timed Papyrus scripts
5. **Wait() unreliable under 100ms** -- merge sub-100ms gaps; use `RegisterForSingleUpdate` when possible
6. **SSE != VR** for: camera, skeleton, collision, UI, input, SKSE addresses, physics (60Hz->90Hz)
7. **ESL FormIDs must be in xx000800-xx000FFF** -- exceeding = crash or data corruption
8. **Loose files always override BSAs** -- check for loose file conflicts before assuming BSA content wins
9. **Condition OR has precedence over AND** -- `A AND B OR C` != what you'd expect
10. **Non-auto properties don't restore from master on save/load** -- they stay blank
11. **PreWEAPON/PreSHIELD skeleton nodes cause CTD in VR** -- must be removed
12. **ONAM required for ESM temp record overrides** -- missing ONAM = game silently ignores overrides
13. **SetVehicle causes HMD desync in VR** -- avoid entirely
14. **GoToState("") in OnUnload -> Self=None crash** -- move to OnLoad instead
15. **Navmesh creation is CK-only** -- xEdit can only delete, never recreate

## xelib Dry-Run Convention

All ESP modifications via xelib scripts must follow this two-pass workflow:
1. **Read-only pass**: load the ESP, log what would change (records added/modified/removed), print to console -- do NOT call `SaveFile()`
2. **User reviews** the proposed changes
3. **Write pass**: only after user approval, run again with `SaveFile()` enabled

This prevents accidental ESP corruption. The hook system blocks direct ESP writes, but xelib operates through Bash and can write via `SaveFile()`.

## Spriggit ESP Workflow (Preferred for Editing)

For **creating or editing ESP records**, prefer Spriggit over xelib. Spriggit serializes ESP files to human-readable YAML that Claude can edit directly with its native Edit tool — no FFI, no scripting layer, and the YAML diffs cleanly in git.

### Workflow
1. **Serialize**: `spriggit serialize --InputPath "Data/MyMod.esp" --OutputPath "/tmp/mymod-yaml" --GameRelease SkyrimSE --PackageName Spriggit.Yaml --PackageVersion "0.40.0"`
2. **Edit**: Read and modify the YAML files directly (Claude's Edit tool works natively on these)
3. **Review**: User reviews the YAML changes (human-readable diffs)
4. **Deserialize**: `spriggit deserialize --InputPath "/tmp/mymod-yaml" --OutputPath "Data/MyMod.esp"`

### When to Use Which
- **Spriggit**: Creating new ESPs, editing existing records, any task where you're modifying specific fields. Simpler, no setup beyond `dotnet tool install`.
- **xeditlib**: Bulk inspection, programmatic traversal (e.g., "find all spells with mismatched casting types"), diffing two ESPs, analysis scripts. Better for read-heavy operations across many records.

### Spriggit Notes
- `spriggit-meta.json` is required in the YAML root for deserialization
- ESP header version must be 1.7 for SSE/VR (not 1.0)
- `--GameRelease` is only for serialize, NOT deserialize

## Safety Rules

Hooks in `.claude/settings.json` enforce these automatically:

### Hard blocked (cannot proceed)
- Deleting the game installation directory or config directory
- Deleting Bethesda registry keys
- Directly writing to ESP/ESM/ESL/BSA/BA2 files (use xelib or modding tools)

### Requires user confirmation
- **Any edit to ANY file** in the game directory or config directory (catch-all)
- Papyrus scripts (`.psc`, `.pex`)
- Skyrim INI files (Skyrim.ini, SkyrimVR.ini, SkyrimPrefs.ini)
- SKSE plugin configs (`Data/SKSE/Plugins/*.ini`)
- Load order files (loadorder.txt, plugins.txt)
- Any `rm`, `mv`, `cp`, redirect, or `sed -i` touching game/config directories
- Any bash command referencing plugin/archive files

### General rules
- **Always review changes before applying** -- this is a delicate install
- Never modify ESP/ESM files directly -- use xelib programmatically or Spriggit
- Vortex manages load order -- direct edits to loadorder.txt/plugins.txt may be overwritten by Vortex
- User is knowledgeable about Skyrim modding and INI settings

### Safety improvement loop
After every session, near-miss, or unexpected outcome, evaluate whether a new hook, expanded protection, or knowledgebase entry could have prevented or caught the issue. Propose new hooks when a pattern of risk emerges -- not reactively after damage, but proactively when you notice a gap. Document proposed hooks in the "Hook Candidates" section of `KNOWLEDGEBASE.md` even if not immediately implemented.

### Audit trail
- Every file edit is auto-backed up to `.claude/backups/` with timestamp
- An audit log at `.claude/backups/AUDIT_LOG.txt` records every file touched, when, and by which tool

## Confidence Levels (Mandatory)

**Before proposing ANY change** to game files, configs, scripts, or ESP records, you MUST:

1. **State a confidence level** (0-100%) for each proposed change
2. **List assumptions** that the confidence level depends on
3. **Investigate before acting**: Check the knowledgebase, read relevant source files, and web-search for Skyrim/VR-specific quirks before committing to an approach. Skyrim has many built-in bugs and version-specific differences -- things frequently do NOT work as expected.
4. **Target >= 90% confidence** before touching anything. If below 90%, document what's uncertain and what additional research would raise it.
5. **Never assume Skyrim SE behavior = Skyrim VR behavior.** Always verify VR-specific differences.

### Confidence scale
| Range | Meaning | Action |
|-------|---------|--------|
| 95-100% | Verified via testing, docs, or authoritative source | Proceed with user confirmation |
| 80-94% | Strong evidence but not fully verified | Proceed with caveats noted |
| 60-79% | Reasonable assumption, some unknowns | Research more before proceeding |
| < 60% | Speculative | Do NOT proceed -- investigate first |

### Investigation checklist (before any change)
- [ ] Consulted `KNOWLEDGEBASE.md` for known quirks
- [ ] Read the actual source files involved
- [ ] Checked if VR differs from SSE for this feature
- [ ] Web-searched for known issues with this approach
- [ ] Considered rollback path if the change breaks something
- [ ] Evaluated whether this task reveals a gap in current hook coverage
