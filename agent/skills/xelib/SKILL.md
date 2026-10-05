---
name: xelib
description: Write or run xelib / XEditLib.dll scripts (Node + koffi) -- UTF-16 strings, VOID init, WordBool, the string-return pattern, game mode, the SSE registry key, loading the REAL active load order, and judging success by stdout not the exit code. Use before writing, running or debugging any xelib script.
---

# XEditLib / xelib

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

## Load the real active order

3. **xelib** — in `GM_SSE` mode the loader reads the **SSE** `plugins.txt`, which may be **absent** on a VR install → the load fails silently. It may also be **present and stale**, which is worse: measured on the dev machine (VR-only), the SSE file holds 659 plugins, is 18 days old, and disagrees with the VR file about which plugins are active — a complete, plausible, wrong load order that nothing rejects. `loadActive()` prefers the VR file, names every rival it ignored, and honours `$PLUGINS_TXT`. Use `tools/xelib/active-plugins.js` `loadActive()` to read and load the real active order explicitly, and always run xelib via a `.js` **file** (`node script.js`), never `node -e '...'` (inline eval deterministically breaks `SetGameMode`).

## Judge success by stdout, never the exit code

Every xelib / koffi-based Node helper (`tools/resaver-resolve-names.js`, the `tools/xelib/` scripts, your own)
can exit **non-zero (e.g. 127) even on full success** on Windows / Git Bash: Node's stdio flush at exit races
the resident native DLL's teardown. **Parse the output the script prints to decide success, never `$?`**,
and never `&&`-chain on it; a wrapper that gates on the exit code reports phantom failures. Where a wrapper
exists it does this for you (`tools/skyrim-winner.sh` parses the `RESULT:` line and returns a real exit
code). Source: `KNOWLEDGEBASE.md` → *Node/koffi helper exit codes are unreliable on Windows*.

For who-wins questions use `bash tools/skyrim-winner.sh` (skill `triage-and-conflicts`), not a new script.
