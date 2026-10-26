# ReSaver Plus (native ReSaver backend): built here, never shipped

ReSaver Plus ("ReSaver++", by Tonycubed2) is a C++ port of Mark Fairchild's ReSaver. The toolkit offers it as an
**opt-in** second backend for `bash tools/resaver-cli.sh`. **Java (ReSaver 6.0.643) stays the default.** This folder
ships **no binary and no upstream source**: only `build.py`, the 27 patches and 15 additions the toolkit tested, and
`SOURCE-SHA256`, the hash of every file they change or add. `build.py` fetches one pinned upstream commit and compiles it
on your machine.

## Source and pin

| | |
|---|---|
| Upstream | [Tonycubed2/ResaverPP](https://github.com/Tonycubed2/ResaverPP), version **19.6**, commit `0d48f60cb8a69b367abfbd78d58418e64c507da5` |
| Licence | Apache-2.0 (`LICENSE`, byte for byte upstream's). `NOTICE.md` carries upstream's NOTICE, the credits and the modification notice; `THIRD-PARTY-NOTICES.md` the LZ4 and miniz notices the CLI build needs |
| Local changes | 27 patches (`patches/`, applied in order) and 15 additions (`additions/`, 7 source files and 8 tests). What each changes is in `NOTICE.md` |
| Newer upstream | 19.8 (`3958350`) and 19.9 (`03ca2b6`) were reviewed and are **not adopted**: the pin is the tested commit. A bump means re-qualifying the patch set, not editing a hash |

## What you need

- Windows x64 and git.
- Visual Studio 2022 Build Tools with **Desktop development with C++** (MSVC v143, a Windows SDK and the bundled CMake
  3.21+). This is a manual step, for you to run -- an agent may not install this:
  `winget install --id Microsoft.VisualStudio.2022.BuildTools --exact --version 17.14.39 --no-upgrade`,
  then add the workload in the Visual Studio Installer. Winget lists this tested version (checked 2026-10-25).
  It is several GB.
- Python 3.10+ (the toolkit already needs it).

Tested with VS Build Tools 17.14.39 / MSBuild 17.14.51, MSVC 19.44.35228 (toolset 14.44.35207), CMake 3.31.6-msvc6,
Windows SDK 10.0.26100.0. `setup.sh` reports what it detects against these; `build.py` records what it used.

## Build

```
python tools/resaver-plus/build.py
bash tools/resaver-cli.sh verify-native
```

`build.py` verifies the checkout's commit, applies the patches, checks every changed and added file against
`SOURCE-SHA256`, builds the command-line tool and the tests **with the GUI off** (one MSBuild job, one compiler
worker; `--jobs 2` at most), and runs CTest. **It installs nothing unless all six runnable targets pass**: `unit`,
`decoder_port`, `topic113`, `expected_result`, `structured_queries`, `analysis_contract` (the real-save `fixtures`
target is skipped by design; no real save is needed or used). The result, `build/resaverpp-cli.exe`, and
`build/BUILD-INFO.json` (pins, patch-set hash, per-file source hashes, tool versions, the executable's SHA-256 and the
CTest summary) are ignored by git. MSVC output is not byte-reproducible, so the hash identifies **your** build; the
adapter pins to it. A rebuild moves the previous build to `build/previous/`, it never deletes it.

Keep the work folder path short: MSBuild's file trackers fail past the Windows path limit, so `build.py` refuses a
`--work` path over 100 characters (the default `tools/resaver-plus/work` is fine in a normal install). Existing
source caches with local changes, a different commit or line-ending conversion are refused; use a new `--work`.
A full build took 165 s here (CPU capped at 25%).

## Use it

Opt in per command with `RESAVER_BACKEND=native`, or for the install by writing the single word `native` into
`tools/resaver-plus/backend` (not shipped, never overwritten by an update). `bash tools/resaver-cli.sh backend` shows
the choice. The chosen backend failing is an **error**; neither backend ever falls back to the other.

- The same 19 operations as Java, plus `writer-scan` (pending save-writer frames; advisory, a heuristic on SSSO-style
  writer function names, not proof of an imminent write).
- Reads and previews never write. `verify-roundtrip` runs in memory.
- `--apply` writes ONE explicit new output pair in an existing folder. An existing `.ess` or `.skse` of that pair is
  refused, so is the input, so is the permanent save store. Before the write, the input pair and the output pair are
  backed up into the toolkit's permanent save store; the write is previewed, then applied, and the applied receipt
  must equal the preview, be verified on reopen, and carry the input's co-save over unchanged. A failed candidate is
  left in place for you to inspect, never deleted.
- `python tools/resaver-plus.py report|compare|decompile|disassemble` gives the read-only upstream reports.
- The JSON is the native schema, close to but **not** identical to the Java driver's: same field names for the core
  counts, native wording for reports and warnings, native changeform trees. Do not diff it against Java output.

The guard denies `resaverpp-cli.exe` run directly with a save-writing flag (`-o`, `--batch`, `--batch-all`,
`--apply`) and any launch of the GUI (`ReSaverPP.exe`, which this build does not produce): rule
S-RESAVER-NATIVE-DIRECT.

## Limits (what is not qualified)

- **Windows only.** No Linux or macOS build was attempted; use the Java backend there.
- One real edited save was loaded in game, on one machine, from the GUI+CLI build of the same patch set; your build is
  CLI-only and made by your compiler. Confidence for saves unlike the tested ones is moderate: the user's own in-game
  load stays the final check.
- Process-crash atomicity of a write is unqualified; existing-output writes are refused rather than qualified.
- The upstream GUI and its writes, and upstream's optional `helper/` SKSE plugin, are out of scope: not built, not
  shipped, not supported.
- The synthetic save that `rs_write_port_probe ... synthetic-fixture` creates (used by CI) holds invented records only.
  **Never load it in Skyrim.**
