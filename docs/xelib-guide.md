# xelib Guide for Skyrim Modding

## What is xelib?

[xeditlib](https://github.com/WingedGuardian/xeditlib) is a Node.js wrapper around `XEditLib.dll`, the same engine that powers xEdit/TES5Edit/SSEEdit. It gives you programmatic access to all 163 xEdit functions from JavaScript.

This means you can read, inspect, diff, and modify ESP/ESM files with code instead of clicking through the xEdit GUI.

## Installation

```bash
npm install xeditlib
```

This installs:
- `xelib.js` -- The Node.js wrapper (all 163 functions with correct FFI signatures)
- `XEditLib.dll` -- The Delphi-compiled binary
- `koffi` -- The FFI library for calling native DLL functions from Node.js

## Critical Setup: Registry Key

XEditLib reads your game path from the Windows registry. You need the **SSE registry key** (even for VR):

```
HKLM\SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition
  Installed Path = C:\path\to\your\Skyrim VR\
```

If this key doesn't exist, create it:
```bash
reg add "HKLM\SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition" /v "Installed Path" /t REG_SZ /d "C:\path\to\your\Skyrim VR\\" /f
```

> **Why SSE, not VR?** XEditLib uses game mode `gmSSE=4` for both SSE and VR. There is no VR-specific mode. The DLL looks up the game path using the SSE registry key regardless.

## Delphi FFI Gotchas

XEditLib.dll is Delphi-compiled. These quirks caused hours of debugging and are now handled by the xelib wrapper, but understanding them helps when debugging:

1. **All strings are UCS-2/UTF-16LE** (Delphi `PWideChar`), never UTF-8
2. **`InitXEdit()` and `CloseXEdit()` are VOID** -- declaring them as bool corrupts the call stack
3. **`WordBool` = `uint16`** (2 bytes), not bool/uint8
4. **String return pattern**: Functions write a length to a `PInteger`, then you call `GetResultString(buffer, len)` to get the value
5. **Game mode enum**: gmFNV=0, gmFO3=1, gmTES4=2, gmTES5=3, gmSSE=4 (use this for VR), gmFO4=5

## Basic Usage Pattern

```js
const xelib = require('xeditlib');

// Initialize
xelib.init();
xelib.setGameMode(xelib.GM_SSE); // Always GM_SSE for VR

// Load plugins
xelib.loadPlugins('Skyrim.esm', true, false);
await xelib.waitForLoader(60000);

// Work with records
const fileH = xelib.fileByName('Skyrim.esm');
const weapons = xelib.getRecords(fileH, 'WEAP');

for (const w of weapons.slice(0, 5)) {
    console.log(xelib.displayName(w));
    xelib.release(w);  // Always release handles!
}

// Cleanup
xelib.release(fileH);
xelib.close();
```

## The Dry-Run Convention

**Never call `SaveFile()` on the first run.** Always follow this pattern:

### Pass 1: Read-Only Preview
```js
// Load ESP, inspect records, log proposed changes
// Do NOT call xelib.saveFile()
console.log('Would modify: SPEL record 0x000800');
console.log('Would add: MGEF record with EditorID "MyEffect"');
```

### Pass 2: User Reviews
The user reads the console output and decides whether to proceed.

### Pass 3: Write (with --commit flag)
```js
if (process.argv.includes('--commit')) {
    xelib.saveFile(fileH);
    console.log('Saved!');
} else {
    console.log('DRY RUN -- use --commit to save');
}
```

See `examples/create-esp.js` for a complete implementation of this pattern.

## Element Path Navigation

xelib uses backslash-delimited paths to navigate record structures:

```js
// Get a value from a nested path
const castType = xelib.getValue(spellH, 'SPIT\\Cast Type');

// For deeply nested paths, use two-step navigation
const data = xelib.getElement(mgefH, 'Magic Effect Data\\DATA');
const archetype = xelib.getValue(data, 'Archetype');
```

**Quirk**: `getValue(record, 'PARENT\\Child')` can fail with nested paths when the target doesn't exist. Use two-step navigation instead:
```js
const outer = xelib.getElement(recH, 'DATA');
const inner = xelib.getElement(outer, 'Radius');
const v = xelib.getValue(inner, '');  // empty path on element handle
```

## Handle Management

XEditLib uses integer handles (like file descriptors). **You must release handles when done**:

```js
const h = xelib.getElement(fileH, 'WEAP');
// ... work with h ...
xelib.release(h);  // MUST release!
```

Forgetting to release handles causes memory leaks and eventually crashes.

## Common Record Types

| Signature | Type | Description |
|-----------|------|-------------|
| SPEL | Spell | Spells, abilities, powers |
| MGEF | Magic Effect | Individual magic effects |
| GLOB | Global Variable | Script-accessible global values |
| KYWD | Keyword | Tags for filtering and conditions |
| WEAP | Weapon | Weapon definitions |
| ARMO | Armor | Armor and clothing |
| NPC_ | NPC | Non-player characters |
| QUST | Quest | Quest definitions |
| FLST | FormList | Lists of forms |
| PERK | Perk | Perk tree entries |

## Further Reading

- [xeditlib GitHub](https://github.com/WingedGuardian/xeditlib) -- Full API documentation
- [xEdit documentation](https://tes5edit.github.io/docs/) -- Record structure reference
- [UESP Mod File Format](https://en.uesp.net/wiki/Skyrim_Mod:Mod_File_Format) -- Low-level file format
