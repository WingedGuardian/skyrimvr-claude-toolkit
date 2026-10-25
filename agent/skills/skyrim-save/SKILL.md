---
name: skyrim-save
description: Read, scan and clean Skyrim save files (.ess), including ReSaver CLI writes (reset-havok, cleanse-formlists, remove-created). Use when debugging save issues, searching for orphaned scripts, checking mod footprint in saves, investigating save bloat, or cleaning or repairing a save; and before deleting, moving or overwriting saves (the guard backs them up; more than two needs the user's SAVE_ACCESS_APPROVAL line).
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

- **Orphaned scripts**: A raw script-name hit is an investigation lead. Use structured ReSaver instance/reference data to determine whether instances are orphaned.
- **Effect accumulation**: Raw FormID byte counts do not establish active effects or stacking. Check the structured records and runtime state.
- **Mod footprint**: Search for a mod's prefix (e.g., "MyMod_") to locate candidate bytes; these are not semantic reference counts.
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
- `verify-roundtrip` is a self-test. Every `--apply` checks its operation's permitted changes, writes in a private staging directory, and re-reads the complete serialized Papyrus/global data and co-save before publishing a new pair. Failed candidates remain in the named staging directory; existing destinations are preserved. A partial publication is a failure and names its journal.
- The pinned ReSaver script-instance setter rejects variable index zero. A preview lists the library's indexes; do not bypass its setter with reflection or direct file edits. Other supported primitive indexes use the same verified pipeline.
- Resolve FormID to EditorID with `tools/resaver-resolve-names.js`.

### Native backend: ReSaver Plus (opt-in, Windows)

`bash tools/resaver-cli.sh backend` says which backend runs. **Java is the default.** The native backend is ReSaver Plus
(Tonycubed2's C++ port of ReSaver), built on the user's machine by `python tools/resaver-plus/build.py` from a pinned
commit plus the toolkit's patches; nothing binary ships. The user opts in with `RESAVER_BACKEND=native` or the word
`native` in `tools/resaver-plus/backend`. Do not switch backends on your own to get past an error: **the selected
backend failing is an error, and neither falls back to the other.** Building it needs Visual Studio 2022 Build Tools,
which is system software: give the user the manual steps (`tools/resaver-plus/README.md`), never install it.

- **Check it first:** `bash tools/resaver-cli.sh verify-native` (its `BUILD-INFO.json` and the executable's SHA-256).
- **Ops:** the same 19 as Java plus `writer-scan` (pending save-writer frames; advisory, a heuristic, not proof of an
  imminent write). Reads and previews never write; `verify-roundtrip` runs in memory.
- **Writes:** `--apply` needs ONE explicit new output pair in an existing folder. An existing `.ess` OR `.skse` of that
  pair is refused, so is the input and the permanent save store. Run the preview (no `--apply`) first and show it.
  Before writing, the adapter backs up the input pair and the output pair into the permanent save store; the applied
  receipt must equal the preview, be verified on reopen, and carry the input's co-save over unchanged. A failed
  candidate stays on disk for inspection: report it, never delete it.
- **JSON:** the native schema, close to but NOT identical to Java's (native report wording, warnings and changeform
  trees). Never diff native output against Java output and call the difference a save change.
- **Read-only reports:** `python tools/resaver-plus.py report|compare|decompile|disassemble`.
- **Never** run `resaverpp-cli.exe` directly with `-o`, `--batch` or `--apply`, or the GUI `ReSaverPP.exe`: the guard
  denies them (S-RESAVER-NATIVE-DIRECT) because they skip every check above.
- **Limits:** one real edited save was load-tested in game on one machine; for saves unlike that one confidence is
  moderate, so the user's own in-game load of the new output is the final check. Crash atomicity is unqualified.
  Windows only: elsewhere use Java.

## Changing a save: what the guard does

A save is a `.ess` with its `.skse` co-save (same folder, same name): ONE save, whichever file a command names.
The guard judges every save in the settings folder's `Saves` (and the save ReSaver `--apply` writes, and the save a
DevBench save overwrites) that a call CHANGES: written or edited, copied onto, moved or renamed, deleted.

- **One or two saves:** no prompt. Before the tool runs, the hook copies BOTH files of each save into
  `.modding-agent-harness/backups/saves/`, reads each copy back to its SHA-256, records it, and only then allows the
  call (a note says so). A missing co-save is recorded as missing. If the backup fails (no room, unreadable, a copy
  that does not verify, the store busy), the call is refused and nothing changes. The backups are never pruned.
- **More than two, or any wildcard:** refused until the USER types one line in their own message (it is never read
  from a tool result, a file or your own text, so you cannot supply it). The refusal does not print that line; it points
  to `python tools/agent-harness.py check --preview`. Pipe that the same call as Action v1 JSON on stdin -- for a
  shell command `{"api_version":1,"host":"<your host>","tool":"<its shell tool>","surface":"shell","shell":"bash" or "powershell",
  "cwd":"<working dir>","session_id":"<this session>","command":"<the exact command>"}` (example: `ADAPTING.md`) --
  and `result.reason` in its JSON output holds the complete line(s) to show the user (an install with a personal
  policy refuses the preview: use plain `check`, which never runs the command). In that line,
  `op` is `write` (a write, copy onto, edit, ReSaver `--apply`, DevBench save), `move` (a move or rename) or
  `delete`. The version 1 grant binds the current `session_id`, operation and canonical full `paths`, including
  both members of every pair. Wildcards also bind the exact `command_sha256` and logical save `count`.
  Show the complete line the preview printed; do not construct or edit an approval yourself. Old basename-only
  and pattern/count grants require a new canonical grant. A matching grant can be reused in that session;
  changed membership or command scope invalidates it. Every approved save is still backed up first.
- **Free, any number:** reading, hashing, listing, and copying saves OUT of `Saves`.
- **Still refused:** deleting or moving the `Saves` folder itself; a loop over saves (`for f in ...; do rm "$f"`),
  which cannot be counted -- name the files, or use a wildcard the guard can list; a save it cannot place (a bare
  ReSaver name with no working directory, a DevBench save with no plain name).
- Codex: the same, but only the interactive TUI's typed messages approve (`codex exec` never does).
- Not seen: a save written by a script run from a file (`python fix.py`) or by a path built at run time.

**Restore** a save pair (both files, each hash-checked before it is written):

```bash
bash tools/skyguard.sh snapshot list --kind save                    # <stamp>-save  Save1.ess + Save1.skse ...
bash tools/skyguard.sh snapshot restore <id> --to <folder>          # a copy, live files untouched
bash tools/skyguard.sh snapshot restore <id> --in-place             # over the live save (the user approves this)
```

New IDs are `core-<uuid>`. The listing also reads the older recovery-snapshot store and installed
`save-backups` records (`local-<id>`); recovery verifies imported bytes and preserves both legacy stores.
Selecting either member selects its complete pair. Restore copies are published without clobbering concurrent
destinations; in-place recovery first preserves the complete current pair and journals each publication.
The default destination and 2 GiB reserve can be configured in the workspace's
`.modding-agent-harness/config.json` with `v: 1`, `save_store` and `save_reserve_bytes`.
The store must stay separate from save roots. Store files and ancestor moves/deletes are protected;
there is no automatic save-backup pruning. Runtime save/load restrictions still apply independently.
