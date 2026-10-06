# Installing the optional tools

None of the modding tools are bundled — install only the ones you need. Per tool: what it is, how to acquire it, and a quick verify.

Install them into the `tools/` folder in your game directory; the setup prompt walks through this. See the [xeditlib](https://github.com/WingedGuardian/xeditlib) repo for XEditLib setup. NifSkope and Blender (used for NIF render-verification and mesh repair) are large external GUI apps installed separately, not bundled.

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
  - Acquire, **pinned to 0.41.0** (the version every serialize/deserialize command here was tested against; needs the **.NET 10 SDK**, the toolkit's single .NET requirement): `dotnet tool restore` from the toolkit root (installs exactly the version in `.config/dotnet-tools.json`, also 0.41.0; run it as `dotnet tool run spriggit`) or `dotnet tool install --global Spriggit.CLI --version 0.41.0`. **Never install it unpinned**: you get whatever is newest, not what was tested. 0.41.0 ships only a `net10.0` tool, so on an SDK-9 machine it starts, prints a version, and cannot serialize. `bash tools/toolchain-check.sh` reports DEGRADED when the installed version differs from the pin (older or newer) or its target SDK is missing.
  - A round trip is **not byte-stable** on 0.41.0: deserializing unchanged YAML gives an ESP of the same size whose records can come out in a different order. To prove a round trip lossless, re-serialize and compare the YAML.
  - Still pass the version `spriggit --version` prints as `--PackageVersion`, never a hardcoded one.
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
  - The `time.clock = time.perf_counter` monkey-patch (recipe: the `skyrim-nif` skill) is still required (`time.clock` was removed in Python 3.8 — the only OTHER Python-version issue, also the only one ever reported upstream, still open/unfixed as [GitHub issue #80](https://github.com/niftools/pyffi/issues/80)).
  - Verify with that same interpreter: `python -c "import pyffi; print(pyffi.__version__)"`.
- **PyNifly** — SSE BSTriShape read/write + animation/controller authoring + the independent parse gate.
  - Acquire: download `io_scene_nifly.zip` from the latest release at https://github.com/BadDogSkyrim/PyNifly/releases and extract it into `tools/pynifly/` so the prebuilt DLL lands at `tools/pynifly/io_scene_nifly/NiflyDLL.dll` (PyNifly 29.x, the version this was verified with: 29.1.0; releases before 29.x put it at `tools/pynifly/io_scene_nifly/pyn/NiflyDLL.dll`). No build step. (A `git clone` of the repo does NOT contain the compiled DLL — it only ships in the release zip.)
  - Verify: `python -c "import sys; sys.path.insert(0,'tools/pynifly/io_scene_nifly'); from pyn import pynifly"` (29.x loads the DLL on import; older releases need `pynifly.NifFile.Load(<path to pyn/NiflyDLL.dll>)` per the skyrim-nif skill).
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
- **`tools/nexus.sh`** — built-in Nexus API helper (no install). Needs your Nexus key (see *Nexus API key* below).
  - Verify: `bash tools/nexus.sh mod <id>` prints a mod's name/version.

## Why some tools have wrappers

1. **AutoMod** — `tools/automod-cli.sh` invokes the **prebuilt `spookys-automod.dll`** directly rather than `dotnet run`. This fixes the per-call recompile / MSB1025 failures that the old `dotnet run` form produced; rebuild once with `--rebuild` after changing AutoMod source.
2. **Spriggit** — deep/nested output paths throw `UnauthorizedAccessException`. Use `tools/spriggit-cli.sh`, which runs in a shallow workspace and copies the result back. It **preserves the exact ESP basename** (= the Spriggit ModKey) so FormKey master references aren't corrupted.

The xelib loader hazard (fix 3) is a usage rule; it lives in the `xelib` skill.

## Nexus API key

Nexus also offers a free REST+GraphQL API ([api-docs.nexusmods.com](https://api-docs.nexusmods.com/)) for mod **version, update dates, changelogs, file info, and dependencies** — useful for update detection and migration/triage. Supply your own free **Personal API Key** (nexusmods.com → Site preferences → API Access) and send it raw as the `apikey` request header. Skyrim SE/VR domain = `skyrimspecialedition`; endpoints take `game_domain_name` + `mod_id`. A personal key is for personal/local use only — never commit or log it, and a shared tool must have each user supply their own. In this toolkit the key is resolved **file-first, then env**: `tools/.nexus_api_key` (one line) if present, else `$NEXUS_API_KEY`. The key file is gitignored — never echo, log, or commit it. `tools/nexus.sh` wraps this (e.g. `bash tools/nexus.sh mod <id>`).
