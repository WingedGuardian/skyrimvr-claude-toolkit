# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A modded Skyrim installation. Adapt paths and version-specific notes to your Skyrim version: SE, AE, VR, or LE. VR-specific behavior is flagged throughout — never assume SSE behavior equals VR behavior.

`setup.sh` detects your mod manager and fills in the Key Paths below accordingly. **If it detected Mod Organizer 2, read the MO2 note in Key Paths before reading or writing any mod file** — MO2 has no real `Data/` folder, so the obvious path is the wrong one. See "Mod Manager Layout" in `KNOWLEDGEBASE.md`.

## Key Paths

- **Game root**: `{{GAME_ROOT}}/`
- **User INI configs**: `{{CONFIG_DIR}}/` (Skyrim.ini, SkyrimVR.ini, SkyrimPrefs.ini)
- **Load order**: `{{LOADORDER_DIR}}/loadorder.txt` and `plugins.txt`
- **SKSE plugins**: `Data/SKSE/Plugins/`
- **Mod data**: `Data/` (ESPs, BSAs, meshes, textures, scripts)
{{MOD_MANAGER_PATHS}}

## Installed Modding Tools

All under `tools/`:

| Tool | Purpose | Usage |
|------|---------|-------|
| **Champollion** | Decompile Papyrus `.pex` → `.psc` | `tools/Champollion/Champollion.exe input.pex` |
| **Caprica** | Compile Papyrus `.psc` → `.pex` | `tools/Caprica/Caprica.exe --game skyrim --import "Data/Scripts/Source" input.psc` |
| **XEditLib.dll** | Programmatic ESP/ESM reading via FFI | Load with koffi in Node.js (see below) |
| **Spriggit** | ESP ↔ YAML/JSON conversion (.NET) | `spriggit serialize ...` |
| **AutoMod CLI** | NIF meshes, BSA archives, audio, MCM, ESP one-liners | `bash tools/automod-cli.sh <module> <command> --json` |
| **PyFFI** | NIF geometry edit — **NiTriShape (LE-format) ONLY** (any modern Python with setuptools) | See PyFFI section below |
| **PyNifly** | NIF read/write incl. **BSTriShape (SSE)** + **animation/controller authoring** (Python, prebuilt DLL) | See PyNifly section below |
| **ReSaver CLI** | Headless `.ess` save parse / query / cross-reference / clean / changeform-level diagnostics | `bash tools/resaver-cli.sh <op> <save.ess>` — read ops: `info\|dump\|find\|find-refs\|worries\|recon\|changeform\|extradata-scan\|changeform-diff\|freeze-report\|globaldata\|globaldata-diff`; write ops (dry-run unless `--apply`, always a NEW file): `set-global\|set-var\|clean\|reset-havok\|cleanse-formlists\|remove-created`; `verify-roundtrip` self-test. Every `--apply` is verify-gated (re-read==model or delete+fail). Resolve FormID→EditorID via `tools/resaver-resolve-names.js`. Needs JDK 17+ + ReSaver's jar (see install section). |
| **cosave-info** | READ-ONLY structural survey of an SKSE `.skse` co-save → JSON (which mods stashed co-save data + how much) | `bash tools/cosave-cli.sh <cosave.skse>` (Python 3; the cosave sits next to its `.ess`) |
| **skyrim-winner** | **"Which plugin actually wins this record?"** Loads the FULL active load order and asks xEdit. No index, no cache -- xEdit is the authority. | `bash tools/skyrim-winner.sh <winner\|chain> <formid> [plugin]` · `... conflicts <plugin>` -- needs **xeditlib**; see below |
| **papyrus-triage** | Turns `Papyrus.N.log` into bucketed, attributed counts. Rows **sum to the entry count**; the not-shown row prints even at zero. | `python tools/papyrus-triage.py [logfile] [--top N] [--all]` -- bundled, Python 3, no install |
| **crash-triage** | Reduces CrashLogger dumps (~5.5k lines each) to ranked signatures and labels the ones your knowledgebase already ruled on. | `python tools/crash-triage.py [dir\|file] [--frames N] [--all]` -- bundled, Python 3, no install |
| **DevBench** | **LIVE in-game** inspect / console / Papyrus / scenario via a localhost REST+MCP server — only while the game is actually running | `bash tools/devbench-cli.sh <alive\|health\|ping\|state\|inspect\|exec\|call\|describe\|notify\|tool>` — see DevBench section below |

> **Note**: Install the tools you need into a `tools/` folder in your game directory; the setup prompt walks through this. See the [xeditlib](https://github.com/WingedGuardian/xeditlib) repo for XEditLib setup. NifSkope and Blender (used for NIF render-verification and mesh repair — see below) are large external GUI apps installed separately, not bundled.

## Installing the Optional Tools

None of the modding tools are bundled — install only the ones you need. Per tool: what it is, how to acquire it, and a quick verify.

- **xeditlib** — Node.js wrapper around XEditLib.dll for programmatic ESP/ESM read/write.
  - Acquire: run `npm install github:WingedGuardian/xeditlib` **from the toolkit root** (installs from GitHub; the bare `npm install xeditlib` works only once it's published to the npm registry). Installing at the root puts `node_modules/xeditlib` where Node's upward lookup finds it from every bundled script (`tools/xelib/*.js`, `tools/resaver-resolve-names.js`, `examples/*.js` all `require('xeditlib')`). The bundled `XEditLib.dll` + `*.Hardcoded.dat` load relative to the package folder, so the scripts are cwd-independent once installed.
  - **Registry requirement**: XEditLib loads in `gmSSE` mode (game mode 4) even on VR, so it reads the game path from the **SSE** registry key. If `HKLM\SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition` is missing (common on a VR-only install), ESP loads fail. Create it from an **admin** terminal: `reg add "HKLM\SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition" /v "Installed Path" /t REG_SZ /d "<GAME_DIR>\" /f`.
  - Verify: from the toolkit root, `node -e "require('xeditlib')"` exits clean.
- **Champollion** — decompiles Papyrus `.pex` → `.psc`.
  - Acquire: download a release from github.com/Orvid/Champollion/releases and unpack into `tools/Champollion/`.
  - Verify: `tools/Champollion/Champollion.exe --help`.
- **Caprica** — compiles Papyrus `.psc` → `.pex`.
  - Acquire: download a release from github.com/Orvid/Caprica/releases and unpack into `tools/Caprica/`.
  - Verify: `tools/Caprica/Caprica.exe --help`.
- **Spriggit** — ESP ↔ YAML/JSON serialization.
  - Acquire: `dotnet tool install Spriggit.CLI` (or `dotnet tool install --global Spriggit.CLI`).
  - For deeply-nested output paths that trip `UnauthorizedAccessException`, call `tools/spriggit-cli.sh` (same args; it runs in a shallow workspace and copies the result back).
  - Verify: `spriggit --help`.
- **AutoMod CLI** — NIF / BSA / audio / MCM / ESP one-liners.
  - Acquire: `git clone https://github.com/SpookyPirate/spookys-automod-toolkit` into `tools/automod`.
  - Pin the SDK: create `tools/automod/global.json` selecting SDK `8.0.x` with `"rollForward": "latestFeature"`:
    ```json
    { "sdk": { "version": "8.0.100", "rollForward": "latestFeature" } }
    ```
  - Build the **Cli project only**: `dotnet build tools/automod/src/SpookysAutomod.Cli -c Release`. The WPF `Setup` project targets `net8.0-windows` — **never build it headless** (it will fail). The built artifact is `spookys-automod.dll`.
  - Run via the wrapper: `bash tools/automod-cli.sh <module> <command> --json`; pass `--rebuild` to rebuild the DLL.
  - Verify: `bash tools/automod-cli.sh esp --help --json`.
- **PyFFI** — LE-format NiTriShape geometry edits.
  - **Works on any modern Python (verified through 3.14) as long as `setuptools` is installed in that same environment** — no dedicated Python 3.10 needed. PyFFI 2.2.3's real (and only) Python-version blocker is a single unconditional `from distutils.cmd import Command` in `pyffi/utils/__init__.py` (used only by an unused, `# pragma: no cover` doc-building helper class) — `distutils` was removed from the stdlib in Python 3.12 (PEP 632). `pip install pyffi setuptools` fixes it: setuptools ships its own vendored `distutils` plus a compatibility shim that transparently satisfies that import, which is the officially-sanctioned PEP 632 migration path, not a fragile workaround. Confirmed end-to-end on Python 3.14.6: imported cleanly, read a real 735-block Skyrim skeleton NIF spanning ~15 block types (NiNode, several bhk\* physics types, controllers, etc.), modified it, wrote it back out, and re-read the result — all clean. The upstream [niftools/pyffi](https://github.com/niftools/pyffi) repo itself has had no real commits since **January 2020** (only a stray dependabot branch since), so this is the practical path forward rather than expecting an upstream fix there.
  - The `time.clock = time.perf_counter` monkey-patch below is still required (`time.clock` was removed in Python 3.8 — the only OTHER Python-version issue, also the only one ever reported upstream, still open/unfixed as [GitHub issue #80](https://github.com/niftools/pyffi/issues/80)).
  - Verify with that same interpreter: `python -c "import pyffi; print(pyffi.__version__)"`.
- **PyNifly** — SSE BSTriShape read/write + animation/controller authoring + the independent parse gate.
  - Acquire: download `io_scene_nifly.zip` from the latest release at https://github.com/BadDogSkyrim/PyNifly/releases and extract it into `tools/pynifly/` so the prebuilt DLL lands at `tools/pynifly/io_scene_nifly/pyn/NiflyDLL.dll`. No build step. (A `git clone` of the repo does NOT contain the compiled DLL — it only ships in the release zip.)
  - Verify: load the DLL per the PyNifly section below.
- **Blender (headless)** — NIF mesh repair + render-to-PNG verification.
  - Acquire: download from blender.org; install the PyNifly Blender addon.
  - Verify: `blender --background --version`.
- **NifSkope** — independent visual NIF render gate (GUI).
  - Acquire: download a release from github.com/niftools/nifskope/releases.
  - Verify: launch it and open any NIF.
- **ReSaver CLI** — headless `.ess` save parse / cross-reference / clean / changeform-level diagnostics.
  - Acquire: download ReSaver from the FallrimTools page (Nexus mod 5031) and drop `ReSaver.jar` plus its `lib/` folder into `tools/resaver-cli/`; needs JDK 17+ (JDK 21 LTS recommended; e.g. `winget install Microsoft.OpenJDK.21`). The wrapper auto-compiles its small driver on first run.
  - The read/diagnostic ops layer a small **analysis overlay** (modified ReSaver source, Apache-2.0 — see `tools/resaver-cli/analysis-overlay/NOTICE.md`) in front of your jar for extra changeform parse coverage; write ops always run the STOCK jar (corruption safety), and if the overlay can't compile against your ReSaver version the wrapper falls back to stock parsing automatically.
  - Verify: `bash tools/resaver-cli.sh info <save.ess>` prints JSON.
- **cosave-info** — READ-ONLY structural survey of an SKSE `.skse` co-save (which mods stashed co-save data + how much — the mod-state landscape the `.ess` never exposes). No install beyond Python 3.
  - Verify: `bash tools/cosave-cli.sh <SaveN_...>.skse` prints JSON (the `.skse` sits next to its `.ess`).
- **`tools/nexus.sh`** — built-in Nexus API helper (no install). Needs your Nexus key per the Nexus section below.
  - Verify: `bash tools/nexus.sh mod <id>` prints a mod's name/version.

### Reliability fixes (why these wrappers exist)

1. **AutoMod** — `tools/automod-cli.sh` invokes the **prebuilt `spookys-automod.dll`** directly rather than `dotnet run`. This fixes the per-call recompile / MSB1025 failures that the old `dotnet run` form produced; rebuild once with `--rebuild` after changing AutoMod source.
2. **Spriggit** — deep/nested output paths throw `UnauthorizedAccessException`. Use `tools/spriggit-cli.sh`, which runs in a shallow workspace and copies the result back. It **preserves the exact ESP basename** (= the Spriggit ModKey) so FormKey master references aren't corrupted.
3. **xelib** — in `GM_SSE` mode the loader reads the **SSE** `plugins.txt`, which may be **absent** on a VR install → the load fails silently. It may also be **present and stale**, which is worse: measured on the dev machine (VR-only), the SSE file holds 659 plugins, is 18 days old, and disagrees with the VR file about which plugins are active — a complete, plausible, wrong load order that nothing rejects. `loadActive()` prefers the VR file, names every rival it ignored, and honours `$PLUGINS_TXT`. Use `tools/xelib/active-plugins.js` `loadActive()` to read and load the real active order explicitly, and always run xelib via a `.js` **file** (`node script.js`), never `node -e '...'` (inline eval deterministically breaks `SetGameMode`).

## AutoMod CLI

A .NET CLI at `tools/automod/` for NIF meshes, BSA archives, audio, MCM menus, and quick ESP record creation. Call via wrapper:
```bash
bash tools/automod-cli.sh <module> <command> [args] --json
```

**Always use `--json`** for parseable output. **Always use `--dry-run` first** for any write command.

| Module | Key Commands | Use For |
|--------|-------------|---------|
| **nif** | `info`, `list-textures`, `replace-textures`, `fix-eyes`, `scale`, `verify` | Mesh inspection and editing. **VR: check for PreWEAPON/PreSHIELD nodes.** |
| **archive** | `info`, `list`, `extract`, `create`, `add-files`, `diff`, `merge` | BSA/BA2 full CRUD |
| **audio** | `info`, `extract-fuz`, `create-fuz`, `wav-to-xwm` | Voice files (FUZ/XWM/WAV) |
| **mcm** | `create`, `add-toggle`, `add-slider`, `validate` | SkyUI MCM menus. **VR: requires SkyUI VR fork.** |
| **esp** | `add-weapon`, `add-spell`, `add-armor`, `add-npc`, `add-quest`, `attach-script`, `set-property`, etc. | Quick one-liner record creation |

### When to Use Which Tool
- **AutoMod `esp`**: Quick one-liner additions. Best for simple records.
- **Spriggit**: Complex multi-field editing via YAML. Best for detailed work.
- **xeditlib**: Programmatic traversal and diffing. Best for analysis/read-heavy operations.
- **AutoMod `nif`/`archive`/`audio`/`mcm`**: Often the only tools available for these operations.
- **PyFFI / PyNifly**: NIF geometry and animation authoring (see below).
- **AutoMod `nif` cannot inspect Havok collision (`bhk*`) blocks** — it handles textures/strings/shaders only. Use NifSkope or a purpose-built parser for collision.

## PyFFI (NIF geometry edits — LE-format NiTriShape only)

**Works on any modern Python (verified through 3.14) as long as `setuptools` is installed alongside it** — see the Installing section above for why. Still needs the `time.clock = time.perf_counter` monkey-patch (a separate, still-open upstream issue, unrelated to the distutils/3.12 one).

> **HARD LIMITS:**
> 1. PyFFI **cannot read BSTriShape at all** (`Unknown block type 'BSTriShape'`) — i.e. any SSE-format (user_version_2=100) NIF. Use PyNifly.
> 2. PyFFI can construct controller blocks, but an **authored NiControllerManager/NiControllerSequence CTDs the engine** despite passing PyFFI's own readback — it omits header string-table registrations the engine requires. **Never author animations with PyFFI — use PyNifly.**
> 3. Building from a fresh `NifFormat.Data()` corrupts the header string table on write. Always **load an existing valid NIF and restructure it** instead.
>
> PyFFI remains the right tool for LE-format NiTriShape geometry edits (blade split/subdivision, bound spheres, vertex shifts).

```python
# run with any modern Python that has setuptools installed alongside pyffi
import time; time.clock = time.perf_counter
from pyffi.formats.nif import NifFormat

with open('path/to/input.nif', 'rb') as f:
    data = NifFormat.Data(); data.read(f)

# ... modify blocks ...

with open('path/to/output.nif', 'wb') as f:
    data.write(f)
```

### Why PyFFI over binary patching / NifSkope re-saves
- Preserves exact NIF format (BSStreamVersion, block types, shader flags, texture slots).
- Handles version differences (83 vs 100) correctly — no format corruption.
- NifSkope converts NIFs to BSStreamVersion 100 (SSE format) on save, which can strip texture slots and change BSLightingShaderProperty structure → crashes with Community Shaders / TruePBR in VR. Scripted PyFFI edits avoid this.

### Key operations
- **Collision shape editing**: `block.dimensions.y = new_value` on `bhkBoxShape`
- **Transform editing**: `block.transform.m_42 = new_value` on `bhkConvexTransformShape`
- **Block iteration**: `for block in data.blocks:` + `type(block).__name__` to find block types

## PyNifly (modern NIF lib — BSTriShape + animation authoring)

Installed at `tools/pynifly/io_scene_nifly/pyn/` (BadDogSkyrim PyNifly; prebuilt `NiflyDLL.dll` ships with it — no Blender, no compile; plain Python 3.10/3.12 x64). It wraps ousnius/nifly, the library behind BodySlide/Outfit Studio.

```python
import sys, os
sys.path.insert(0, "tools/pynifly/io_scene_nifly")
from pyn import pynifly      # import as the package, NOT `import pynifly`
pynifly.NifFile.Load(os.path.abspath("tools/pynifly/io_scene_nifly/pyn/NiflyDLL.dll"))
nf = pynifly.NifFile(path)               # reads SSE BSTriShape AND LE NiTriShape
[s.name for s in nf.shapes]; list(nf.nodes.keys())
```

**Use PyNifly for** the things PyFFI can't: any **BSTriShape (SSE)** NIF, and **all animation/controller authoring** — it has `.New()` factories for `NiControllerManager / NiControllerSequence / NiMultiTargetTransformController / NiTransformData / NiTransformInterpolator / NiDefaultAVObjectPalette / BSXFlags / NiTextKeyExtraData` that register header strings correctly (the exact thing PyFFI botches → CTD). This is what makes **self-spinning / telescoping / keyframe-animated effect meshes** possible (e.g. a `SpecialIdle`-named `NiControllerSequence` auto-loops on a placed Activator with zero scripting).

**ALSO the validation gate:** after authoring/editing any NIF, cross-read it with PyNifly (an independent, battle-tested parser) before in-game testing — a clean PyNifly read catches malformed files that PyFFI's same-tool readback misses. Check the *specific* crashable subsystem (e.g. the controller), since a geometry-only read can pass even when the animation stack is malformed.

## NIF Validation & Render Verification

A crash-to-desktop must be caught in tooling, not in the headset. Use the right tool for each role:

| Tool | Role |
|------|------|
| **PyFFI** | LE-format NiTriShape geometry edits |
| **PyNifly** | BSTriShape (SSE) + animation/controller authoring + **independent parse-validation gate** |
| **NifSkope** | Independent **render gate** (GUI) — visual confirmation a NIF renders |
| **Blender (headless)** | Mesh **repair** + **render-to-PNG** verification (uses the same nifly lib as PyNifly — good for repair, NOT an independent parser) |

**Gates before any in-game test:** (1) author with valid-by-construction tools (PyNifly, not hand-rolled controller blocks); (2) cross-validate with an independent, stricter parser; (3) diff against a known-good structure. Render a PNG (Blender headless) so a mesh/VFX fix is confirmed in chat before a game launch.

## skyrim-winner — which plugin actually wins a record

```bash
bash tools/skyrim-winner.sh winner    0x0003418E            # who wins
bash tools/skyrim-winner.sh chain     0x0003418E            # full override chain, in order
bash tools/skyrim-winner.sh winner    "SomeMod.esp:000871"  # scoped: local id inside a plugin
bash tools/skyrim-winner.sh conflicts "SomeMod.esp"         # records this plugin LOSES
```

Requires **xeditlib** (see the optional-tools section). The game root is derived from the
script's own location (`tools/` sits in the game folder); override with `$GAME_ROOT`.

**It deliberately has no index and no cache.** xEdit already computes conflict resolution
correctly, and the load order is explicit and total, so this asks xEdit through xelib and
prints the answer — one source of truth, and it is not this script. A full 659-plugin load
costs **~4 seconds** on the dev install, which is why a cache — and therefore a freshness
contract to get wrong — is unnecessary.

**Prefer it over hand-rolling another override script.** The common shortcut is to load a
hand-picked subset of plugins "that could plausibly be involved". A who-wins answer computed
against a guessed subset is only as complete as the guess: a plugin outside the list can win
and the script never sees it. Worse, a short list can silently load **nothing** and still
resolve, producing a confident answer from an empty tree.

**Judge by the wrapper's exit code, not node's.** `node tools/skyrim-winner.js` exits **127
on complete success** (koffi/stdio shutdown, see the XEditLib notes) — `skyrim-winner.sh`
parses the `RESULT:` line and gives a real exit code (0 ok / 2 error / 3 did-not-finish).

⚠ **A `refs` subcommand is deliberately absent.** `getReferencedBy` returns 0 without
`buildRefs`, and `buildRefs: true` makes the loader fail outright on a real 659-plugin order
(reproduced twice, empty exception message). A subcommand that always answers "nothing
references this" is worse than no subcommand.

## papyrus-triage — what the engine actually complained about

```bash
python tools/papyrus-triage.py                    # newest Papyrus log
python tools/papyrus-triage.py <logfile> --all
```

A hand-rolled `grep | sort | uniq -c` has no way to notice it dropped a shape. Two properties
are asserted here, not assumed: **rows sum to the input**, and **the not-shown row prints even
when it is zero**.

**The unit of accounting is the ENTRY, not the line.** A Papyrus entry is multi-line — the dev
install's `Papyrus.1.log` has 8426 lines but only 5292 timestamped entries, the rest being
`stack:` continuations. A line-based sum is ~37% wrong.

**Severity is spelled two ways.** The same log contains both `warning:` (99) and `WARNING:`
(662); a case-sensitive grep silently loses 87% of them.

Known-benign vanilla shapes are **labelled, never dropped** — ranking attention is the job;
deciding what the reader may see is not.

## crash-triage — which crashes are new, and which are already answered

```bash
python tools/crash-triage.py                 # every crash dump in the SKSE folder
python tools/crash-triage.py <one.log>
```

A CrashLogger dump is ~5,500 lines, ~4,600 of which are the raw `STACK` block. The identifying
part is the exception line plus the top of `PROBABLE CALL STACK`. This groups dumps by
`module+offset` and cross-references a `KNOWN` dict of signatures you have already ruled on —
so a settled crash prints as `[ACCEPTED - do not re-investigate]` instead of being chased a
fourth time. **Add your own rulings to that dict** as you make them; it ships with the three
from the dev install and is meant to be edited.

**It matches `crash-*.txt` AND `crash-*.log`, case-insensitively**, because CrashLoggerSSE
changed extension. Matching one of them does not fail — it prints a confident, complete-looking
total built from whichever half it happened to catch. Measured on the dev install: 8 `.txt`
(all June 2025) sitting beside 20 `.log` (2026).

Same invariants as `papyrus-triage`: denominator first, rows summing to independently counted
totals, and the not-shown row printing at zero. Both are mutation-tested — dropping logs during
parsing produces `ACCOUNTING MISMATCH` and exit 1.

## Where these tools look, and what happens when there is more than one install

`papyrus-triage` and `crash-triage` discover their folders through `tools/skyrim_paths.py`
rather than hardcoding a path. A machine can easily carry `Documents/My Games/Skyrim`,
`.../Skyrim Special Edition` **and** `.../Skyrim VR` at once, and the wrong one **reads
perfectly** — you get a complete report about an install you are not modding.

So the picker prefers **Skyrim VR → Skyrim Special Edition → Skyrim**, and whenever more than
one candidate exists it prints every rival it ignored, to stderr, naming the override:

```
[skyrim_paths] more than one candidate exists; using:
  C:\Users\you\Documents\My Games\Skyrim VR\SKSE
  ignoring: C:\Users\you\Documents\My Games\Skyrim Special Edition\SKSE
  ...  If this picked wrong, set SKSE_CRASH_DIR.
```

Overrides: `$PAPYRUS_LOG_DIR`, `$SKSE_CRASH_DIR`, and `$PLUGINS_TXT` (for the xelib load-order
reader, which faces the same hazard under `%LOCALAPPDATA%`).

## DevBench — the live in-game test channel (`tools/devbench-cli.sh`)

**The single biggest time sink in modding is the in-game test loop:** change something, ask the user to
launch the game, have them trigger it, wait, hear "it didn't work", guess again. DevBench breaks that
loop. It is a dev-only SKSE plugin (alandtse, Nexus SE **181326** — same author as Engine Fixes VR) that
runs a **localhost REST + MCP server inside the running game**, so Claude can inspect live state, run
console commands *and read their output*, call Papyrus functions and get the return value, dismiss
modals, and drive scripted scenarios — directly, while the user just keeps playing.

**NOT bundled** (GPL-3.0-or-later; the toolkit ships only the wrapper). It changes no gameplay and
writes no save data.

**It is a mod, not one of the `tools/` dev utilities — so do NOT install it for the user.** Every
other optional tool lives in `tools/` and never touches the game; DevBench is an SKSE plugin that ends
up in `Data/SKSE/Plugins/`. Hand-copying a DLL there bypasses Vortex/MO2, leaves the file untracked,
and on a managed install a later deploy or purge can clobber it. If the user wants DevBench, they
install it through their own mod manager like any other SKSE plugin; the wrapper below works the
moment it's present.

```bash
bash tools/devbench-cli.sh alive                       # is the GAME running, paused, hung, or not loaded?
bash tools/devbench-cli.sh health                      # raw off-thread liveness + instance identity
bash tools/devbench-cli.sh ping                        # server self-test only -- NOT proof of liveness
bash tools/devbench-cli.sh state                       # {plugin,version,vr,playerLoaded,frame}
bash tools/devbench-cli.sh inspect vm                  # Papyrus VM health — freeze diagnosis
bash tools/devbench-cli.sh exec "player.getav health"  # console exec + capture + read output
bash tools/devbench-cli.sh call Actor IsInCombat '[]' '{"form":"0x14"}'
bash tools/devbench-cli.sh notify "Test 3 of 5: swing now"   # HUD narration
bash tools/devbench-cli.sh tool menu '{"action":"accept"}'   # raw: any tool, any JSON
```

**Hard requirement — this is NOT headless.** The server only exists while the game is genuinely
running with a save loaded. In VR that means the headset is on (Skyrim VR errors at VR-init without
it). When the game is closed the port is dead — fall back to the offline tools (xelib, Spriggit,
ReSaver, PyNifly).

**Port is deterministic per runtime: SE/AE `8920`, VR `8921`** (DevBench iterates if the port is busy
and writes the bound port to `Data/SKSE/Plugins/devbench/runtime.json`, which the wrapper reads).
Override with `DEVBENCH_PORT`. MCP clients can point at `http://127.0.0.1:<port>/mcp`; the REST path
the wrapper uses works any time the server is up, with no reconnect.

**Tools:** `ping` · `console` (exec/read) · `inspect` (`state|health|vm|scene|mods|player|inventory|
quests|effects|refs`) · `papyrus` (list/describe/**call**) · `menu` (list/describe/**accept**/open/close) ·
`game` (save/load/list) · `scenario` (timed steps with `waitFor`/`waitUntil`, not guessed sleeps) ·
`camera` · `record`/`replay`.

**Check `alive` first, and believe it over `ping`.** Every tool call above runs *on the game's main
thread* and 504s after 5s if that thread is stuck — so tool calls fail in exactly the situation you
most need diagnosed. `GET /api/health` (DevBench **1.11.0+**, answered off-thread since **1.12.0**) is
the one endpoint that always replies; `alive` samples it twice and separates the four states that a
raw `ping` flattens into one: **running** · **paused/loading** (frame frozen, task queue draining —
not a hang) · **hung** (frame frozen *and* `pendingTasks` piling up while `lastTaskFrame` stalls) ·
**not in game** (`frame < 0`, no save loaded). It also prints the answering instance's
`pid`/`exe`/`vr`, so if you have two Skyrims open you catch a wrong-port misattach in one call instead
of chasing phantom results. On older DevBench the wrapper falls back to the legacy frame diff and says
so. MCP clients get the same signal as `inspect kind=health`.

**Standing practice — automate the test, don't delegate it.** On any in-game problem, ask "how do I
test this myself instead of asking the user to?" Build a *parameterized* harness (a global Papyrus
function callable via `call`, logging to a file you read off disk) rather than a one-shot, so sweeping
a value or swapping a mechanism is another DevBench call — not another edit → recompile → user reload.
Reserve manual testing for the final confirmation gate. See KNOWLEDGEBASE.md for the hazards
(`game save` deadlock, paused-state semantics, heavy console commands) — read them before your first
live session.

## Optional Devcontainer (`.devcontainer/`, `devshell.sh`, `devshell-docker.sh`)

Credit: this devcontainer and its MO2-mount design originated in
[@aaronputty](https://github.com/aaronputty)'s fork of this toolkit.

A reproducible Linux container (Python 3.11, Node 20, .NET 9, JDK 17) for the tools that don't need
Windows or an active MO2 session: Spriggit ESP inspection/diffing, FOMOD/JSON generation, unit-testing
mod logic, ReSaver CLI. See `docs/container-vs-windows.md` for the full routing guide — xelib and
anything load-order-dependent still needs Windows (and MO2's executables list, on an MO2 install).

- **`bash devshell-docker.sh`** — builds the image on first run and drops you into a shell. Only
  needs Docker Desktop. Reads its mount sources straight out of `.devcontainer/devcontainer.json`
  (which `setup.sh` fills in with your real mod paths), so it never hardcodes them.
- **`bash devshell.sh`** — same thing via the `@devcontainers/cli` (`npm install -g
  @devcontainers/cli`), for editor integration (VS Code's Dev Containers extension uses the same
  `devcontainer.json`).
- `setup.sh` fills in `.devcontainer/devcontainer.json`'s mount paths automatically: on an MO2
  install, from the detected instance's mods/profile/overwrite folders; on stock/Vortex, from `Data/`
  and the INI config folder.
- Confirmed working end-to-end: a real third-party ESP with records serializes and inspects
  correctly via `examples/inspect-esp.py`. **One confirmed limitation:** a plugin using localized
  strings (the `Localized` flag) fails to serialize in the container — see
  `docs/container-vs-windows.md`.
- The SKSE-plugin cross-compile toolchain (LLVM/xwin/xmake) from the source fork is **not** part of
  this default image — it roughly doubles the build for a capability that's still a single
  unvalidated experiment. See `docs/skse-cross-compile.md` if you want to add it yourself.

## ESP Cross-Reference Integrity (`tools/esp-verify-wrapper.sh`)

Guards against the silent re-mastering / dropped-reference corruption class during bulk ESP operations (mod splits, plugin renames, master-list edits, YAML find-replace). Tool-agnostic; snapshots stored outside the game dir so it never trips the edit hooks.
- `bash tools/esp-verify-wrapper.sh snapshot Data/Mod.esp [...]` — before a risky op
- `bash tools/esp-verify-wrapper.sh verify   Data/Mod.esp [...]` — after; exits 1 + loud report on any reference whose target master changed or vanished
- `bash tools/esp-verify-wrapper.sh guard Data/Mod.esp [...] -- <command...>` — snapshot → run → verify, one shot

**Standing practice**: snapshot before, verify after, ANY bulk ESP operation. If a change is intended, re-run `snapshot` to accept it as the new baseline.

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

Nexus also offers a free REST+GraphQL API ([api-docs.nexusmods.com](https://api-docs.nexusmods.com/)) for mod **version, update dates, changelogs, file info, and dependencies** — useful for update detection and migration/triage. Supply your own free **Personal API Key** (nexusmods.com → Site preferences → API Access) and send it raw as the `apikey` request header. Skyrim SE/VR domain = `skyrimspecialedition`; endpoints take `game_domain_name` + `mod_id`. A personal key is for personal/local use only — never commit or log it, and a shared tool must have each user supply their own. In this toolkit the key is resolved **file-first, then env**: `tools/.nexus_api_key` (one line) if present, else `$NEXUS_API_KEY`. The key file is gitignored — never echo, log, or commit it. `tools/nexus.sh` wraps this (e.g. `bash tools/nexus.sh mod <id>`).

## Knowledgebase

`KNOWLEDGEBASE.md` (project root) is the master reference for all discovered quirks, gotchas, and cross-version differences. **Always consult it before making changes** to avoid repeating past mistakes.

**Standing instruction**: After every debugging session, mod investigation, or web research, extract any new facts (engine quirks, VR vs SSE differences, API gotchas, tool limitations) and add them to KNOWLEDGEBASE.md. We learn from everything we come into contact with.

## Top Gotchas (Always In Context)

These are the most dangerous/common pitfalls. Consult `KNOWLEDGEBASE.md` for full details.

1. **RemoveSpell doesn't fire OnEffectFinish** -- use `DispelSpell` when cleanup logic exists (but `DispelSpell` excludes ability-type spells)
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
16. **VR controller input is not on the SKSE Input API** -- use the VRIK API for trigger/grip/button detection
17. **Spell effect Area is in FEET, not game units** -- Area=60 ≈ 18m radius
18. **Papyrus Sound type maps to SOUN, not SNDR** -- passing a SNDR FormID silently returns None

## xelib Dry-Run Convention

All ESP modifications via xelib scripts must follow this two-pass workflow:
1. **Read-only pass**: load the ESP, log what would change (records added/modified/removed), print to console -- do NOT call `SaveFile()`
2. **User reviews** the proposed changes
3. **Write pass**: only after user approval, run again with `SaveFile()` enabled

This prevents accidental ESP corruption. The hook system blocks direct ESP writes, but xelib operates through Bash and can write via `SaveFile()`.

## Spriggit ESP Workflow (Preferred for Editing)

For **creating or editing ESP records**, prefer Spriggit over xelib. Spriggit serializes ESP files to human-readable YAML that Claude can edit directly with its native Edit tool — no FFI, no scripting layer, and the YAML diffs cleanly in git.

### Workflow
1. **Serialize**: `bash tools/spriggit-cli.sh serialize --InputPath "Data/MyMod.esp" --OutputPath "<output-yaml-dir>" --GameRelease SkyrimSE --PackageName Spriggit.Yaml --PackageVersion "<installed-version>"` (use the wrapper — scratch/output paths are usually deeply nested and raw `spriggit` throws `UnauthorizedAccessException` on them; see Fix note below for `<installed-version>`)
2. **Edit**: Read and modify the YAML files directly
3. **Review**: User reviews the YAML changes (human-readable diffs)
4. **Deserialize**: `bash tools/spriggit-cli.sh deserialize --InputPath "<output-yaml-dir>" --OutputPath "Data/MyMod.esp"`

### When to Use Which
- **Spriggit**: Creating new ESPs, editing existing records, any task where you're modifying specific fields.
- **xeditlib**: Bulk inspection, programmatic traversal, diffing two ESPs, analysis scripts.

### Spriggit Notes
- `spriggit-meta.json` is required in the YAML root for deserialization
- ESP header version must be 1.7 for SSE/VR (not 1.0)
- `--GameRelease` is only for serialize, NOT deserialize; `--PackageVersion` is REQUIRED when `--PackageName` is set
- **Don't hardcode `--PackageVersion`** (e.g. `0.40.0` goes stale the moment `dotnet tool install` pulls a newer Spriggit). Discover the installed version with `spriggit --version` and pass exactly that.
- Use `-u` / `--ErrorOnUnknown` on serialize — Spriggit **silently drops unknown YAML fields** otherwise (no error, no warning). Always round-trip verify a new field: deserialize → re-serialize → grep for the field.

## ESP Dependency Rule: Own Your Records

**Never use `GetFormFromFile()` to borrow records from another mod.** Soft runtime dependencies silently break when that mod is disabled — the call returns `None` with zero error output and the feature simply doesn't work. This is nearly impossible to debug without reading the Papyrus log.

**Rule**: Any record your mod needs must live in the ESP you're building. Copy the record (SNDR, SPEL, MGEF, WEAP, etc.) into your own plugin rather than referencing another mod's.
- **Hard master dependencies** (MAST records): acceptable when overriding or extending a specific mod's content.
- **Soft `GetFormFromFile()` dependencies on other mods**: almost always avoidable. Copy the asset, create your own record.
- **Safe exception**: `Skyrim.esm` and the base game masters are always loaded — referencing their FormIDs is fine.

## Safety Rules

Hooks in `.claude/hooks/`, wired up by `.claude/settings.json`, enforce these
automatically. **A hook exists to stop ME, not to interrupt the user** -- so the
rules are graded, and only the first list actually refuses.

### DENIED -- refused outright, cannot proceed
- Deleting the game installation directory or the config directory -- anchored on any
  destroyer (`rm`, `rmdir`, `del`, `Remove-Item`, `shutil.rmtree`, `find -delete`)
  named alongside a qualified path inside the install, in either slash direction.
  It is pattern matching, not a sandbox; treat it as a guard against accidents.
- Deleting Bethesda registry keys
- Directly writing to ESP/ESM/ESL/BSA/BA2 files (use xelib, Spriggit or AutoMod)
- Tool output aimed straight at an existing `.psc`, and Champollion against a `.pex`
  inside `Data/Scripts/` -- both have destroyed irreplaceable sources here

### ADVISED -- the call proceeds, I get a note, the user is never prompted
- Skyrim INI files (Skyrim.ini, SkyrimVR.ini, SkyrimPrefs.ini) -- last file wins
- SKSE plugin configs (`Data/SKSE/Plugins/*.ini`) -- not captured by any snapshot
- Papyrus scripts (`.psc`, `.pex`) -- a `.pex` loads at game startup ONLY
- Load order files -- the mod manager owns them and may overwrite a direct edit
- `rm`, `mv`, `cp`, redirects and `sed -i` touching game/config directories
- Any bash command referencing plugin/archive files
- AutoMod write commands without `--dry-run`

### ASKS THE USER -- exactly one rule
- A ReSaver `reset-havok` / `cleanse-formlists` / `remove-created` with `--apply`,
  which mutates a `.ess` save through ReSaver's write path

**Why so few prompts.** A guard that prompts on routine work gets approved by reflex
and then protects nothing -- the X4 toolkit measured 40 such prompts across 13,282
commands, every one of them noise. Deny is likewise a short list: "the mod manager
owns it" or "this is risky" is a reason to advise, not to refuse, because a guard
that blocks legitimate work is a guard that gets deleted.

**Verify the hooks are actually alive: `bash tools/hook-canary.sh`.** Every hook here
was INERT for months -- fired, read zero bytes, exited 0, which is byte-identical to
approving. `/dev/stdin` fails when stdin is a Win32 pipe from a non-MSYS parent,
which is how Claude Code spawns a hook; the suite reproduces that on Windows but not
on Linux, and neither tells you about YOUR install. The canary reads heartbeats
written from inside real invocations, which does.

### General rules
- **Always review changes before applying** -- modded installs are delicate
- Never modify ESP/ESM files directly -- use xelib programmatically or Spriggit
- Vortex manages load order -- direct edits to loadorder.txt/plugins.txt may be overwritten
- **Never hand-install a third-party mod into `Data/` -- that is the mod manager's job.** Downloading
  someone's mod and copying its files in bypasses Vortex/MO2: the files are untracked, a later deploy
  or purge can clobber or orphan them, and under MO2 they don't appear in the virtual filesystem's
  conflict view at all. They're also invisible in the user's own record of what's installed, which is
  the state that makes a broken install undebuggable later. **This includes SKSE plugin DLLs** --
  DevBench among them. If a mod is needed, tell the user and let them install it with their manager.
  (Writing the user's **own** in-development mod files into `Data/` is a different thing and is fine.)

### Safety improvement loop
After every session, near-miss, or unexpected outcome, evaluate whether a new hook, expanded protection, or knowledgebase entry could have prevented or caught the issue. Propose new hooks when a pattern of risk emerges -- proactively when you notice a gap. Document proposed hooks in the "Hook Candidates" section of `KNOWLEDGEBASE.md`.

### Audit trail
- Every file edit is auto-backed up to `.claude/backups/` with timestamp
- An audit log at `.claude/backups/AUDIT_LOG.txt` records every file touched, when, and by which tool

### Iteration snapshots (standing process — do not skip)
Mod development requires many experimental iterations. Without snapshots, reverting to a known-good state means reconstructing code from memory or entangled transcript turns.
1. **Before any experimental change**, copy the current `.psc` source files to `.claude/backups/<descriptive-name>/` (e.g. `known-good-pre-stagger/`). This is the rollback point.
2. **The auto-backup hook covers INIs only** — it does NOT capture `.psc`/`.pex`. Spriggit auto-backups capture the ESP on every deserialize, but not scripts.
3. **After confirming a state works in-game**, snapshot both scripts and a Spriggit export to a dated `known-good/` folder, named descriptively (what works, not just the date).
4. **"Restore from transcript" is a trap** — past-turn code is entangled with the bugs being fixed that same turn. Read it for reference; don't paste it forward blindly.

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

---

## Core Modding Principle: Vanilla Game as Frame of Reference

**Before implementing any mechanic — even a novel one — identify how vanilla Skyrim handles the closest equivalent and model the solution on that pattern.**

Approaches disconnected from how the engine actually works lead to silent failures: Concentration stagger locks, script-cast stagger spells with no visible animation, ValueModifier Touch spells cast from static markers that don't deliver damage. The vanilla game is proof-of-concept. If vanilla doesn't do it that way, ask *why* before choosing your approach.

**Practical process:**
1. Identify the vanilla analogue (e.g. "bleed on weapon swing" → Bleeding Strikes / Bladesman perk)
2. Find the MGEF/spell/mechanism vanilla uses
3. Model your solution as closely as possible on that pattern
4. Only diverge when the vanilla pattern genuinely cannot be adapted

**Examples:**

| Mechanic | Wrong (invented) | Vanilla-aligned |
|----------|-----------------|-----------------|
| Bleed on hit | ValueModifier MGEF on a Concentration enchantment | FireAndForget bleed spell cast per-swing (mirrors the Bleeding Strikes perk's per-hit application) |
| Script stagger | Stagger-archetype FireAndForget spell via `Spell.Cast()` (no visible animation) | `PushActorAway(target, 1-3)` — physical stumble, proven engine behavior |
| Stagger immunity | `ForceActorValue("Stagger", 0)` polling | AddPerk with a ModIncomingStagger ×0 entry |
| Melee auto-fire prevention | Dispel in OnEffectStart | Strip the enchantment from the weapon form |

---

## Core Modding Principle: Native Engine Solutions First

**Before writing any Papyrus workaround, ask: "How does the game already handle this?"**

"Simple" in Skyrim modding means simple from the engine's perspective — not simple as code. A 3-line Papyrus hack that polls state every tick is NOT simpler than a 1-line `AddPerk()` call, because the perk is what the engine natively understands. Script-based polling fights the engine; native mechanisms work with it.

**Rule:** Use the engine's own systems first. Papyrus is a supplement, not a replacement.

| Problem | Script hack (wrong) | Native solution (right) |
|---------|---------------------|-------------------------|
| Stagger immunity | ForceActorValue polling | AddPerk with stagger resist entry |
| Keeping magicka up | RestoreAV in a loop | Enchantment with 0 magicka cost |
| Melee auto-fire prevention | Dispel in OnEffectStart | Strip enchantment from weapon form |
| Hit detection | Papyrus distance checks | Engine collision (projectile/melee) |
| Movement lock | Script position forcing | SetRestrained / Paralysis MGEF (note VR caveats — see KB) |

---

## Core Principle: Do Your Homework (Due Diligence Before Acting)

**Do the amount of due diligence you need such that the user has to do as little trial-and-error and manual verification as possible.**

- This does **NOT** mean cut corners, and does **NOT** mean skip steps where the user is genuinely needed (e.g. in-game VR testing only they can do).
- It means: verify formats, read the actual files, research the established technique (web + Nexus), confirm tool capabilities, and de-risk uncertain steps with a cheap spike — *then* make the change.
- **Catch CTD-class failures in tooling, not the headset** — cross-validate authored assets with an independent parser; don't trust same-tool readback.

Every in-game test cycle costs the user real time. Burn your own tokens on verification so theirs aren't wasted on avoidable trial-and-error.

---

## Core Working Principle: Cognitive Co-Pilot, Not Order-Taker

On every task, ask: **"what else is wrong here that nobody asked about?"** — and surface it. Don't just execute the stated request: find related issues, challenge assumptions, and suggest what the user hasn't thought of. The value here is **anticipation, not compliance**.

Treat the user's examples as a **sample, not the spec.** When they name a few instances, enumerate and probe the broader *class* yourself:
- Two failing FormIDs named → audit the whole record set, not just those two.
- One timing bug found → check every analogous call for the same defect.
- "X doesn't work" after a parameter tweak → question whether the **mechanism itself is wrong**, not just the number (e.g. a knockback that needs a *Knock Down* flag, not more Force).
