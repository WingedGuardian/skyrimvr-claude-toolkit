# Installing the optional tools

None of the modding tools are bundled — install only the ones you need. Per tool: what it is, how to acquire it, and a quick verify.

**House Carl 2.0.5** is an optional record/query backend with a shared Claude/Codex gateway, Wrye-managed
or MO2-profile inputs, and verified staged plugin edits. Setup, pinned archive hash, prerequisites and
capability limits: [House Carl integration](housecarl.md). Start with `python tools/housecarl.py --help`.

**Every install here is ONE exact version — the one this toolkit was tested with — and every one can be verified** (user ruling, v4: not older, not newer). Packages install from a lock or an exact pin; downloads are checked against the SHA-256 in [Pinned downloads](#pinned-downloads) before you unpack them. If a hash does not match, stop: you have a different file than the one that was tested.

Install them into the `tools/` folder in your game directory; the setup prompt walks through this. See the [xeditlib](https://github.com/WingedGuardian/xeditlib) repo for XEditLib setup. NifSkope and Blender (used for NIF render-verification and mesh repair) are large external GUI apps installed separately, not bundled.

- **xeditlib** — Node.js wrapper around XEditLib.dll for programmatic ESP/ESM read/write.
  - Acquire, **pinned and integrity-checked**: run `npm ci --ignore-scripts --no-audit --no-fund` **from the toolkit root**. The shipped `package.json` lists xeditlib v1.0.2 = commit `06f73eac215970c427ede049a9198c8002c43c65` (as the GitHub tarball of that commit) and koffi 2.15.1, the version tested with it (xeditlib itself asks for koffi `^2.15.1`, which would otherwise float), as optional dependencies; `package-lock.json` holds a sha512 integrity hash for each, and npm checks downloads against those locked hashes. A successful npm exit alone does not prove an optional backend was installed: run the verification below. No GitHub account or SSH key is needed. `--ignore-scripts` is safe because koffi ships prebuilt binaries. A later `npm ci` keeps them (they are in the lock), and so does a later plain install at the root. MEASURED 2026-10-17: in a private copy of the toolkit root with an empty npm cache and empty npm user/global configuration, `npm ci --ignore-scripts` fetched the public commit archive and installed both locked packages. The installed diagnostic tests and pinned native DLL negative paths passed without loading plugins; all 36 consumer Node checks passed, and a following plain install kept both packages and their verified archive bytes. This does not qualify plugin edits or game loading. They are optional because xeditlib is Windows-only: on Linux (the devcontainer, CI) npm skips it. Installing at the root puts `node_modules/xeditlib` where Node's upward lookup finds it from every bundled script (`tools/xelib/*.js`, `tools/resaver-resolve-names.js`, `examples/*.js` all `require('xeditlib')`). The bundled `XEditLib.dll` + `*.Hardcoded.dat` load relative to the package folder, so the scripts are cwd-independent once installed.
  - **Registry requirement**: XEditLib loads in `gmSSE` mode (game mode 4) even on VR, so it reads the game path from the **SSE** registry key. If `HKLM\SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition` is missing (common on a VR-only install), ESP loads fail. Create it from an **admin** terminal: `reg add "HKLM\SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition" /v "Installed Path" /t REG_SZ /d "<GAME_DIR>\" /f`.
  - Verify: from the toolkit root, `npm ls xeditlib koffi` shows `xeditlib@1.0.2` (from `.../tar.gz/06f73eac215970c427ede049a9198c8002c43c65`) and `koffi@2.15.1`, and `node -e "require('xeditlib')"` exits clean.
- **Champollion** — decompiles Papyrus `.pex` → `.psc`.
  - Acquire: download **v1.3.2** (`Champollion.v1.3.2.zip`, see [Pinned downloads](#pinned-downloads)), check its SHA-256, and unpack into `tools/Champollion/`.
  - Verify: `tools/Champollion/Champollion.exe --help`.
- **Caprica** — compiles Papyrus `.psc` → `.pex`.
  - Acquire: download **v0.3.0** (`Caprica.v0.3.0.7z`, see [Pinned downloads](#pinned-downloads)), check its SHA-256, and unpack into `tools/Caprica/`.
  - Verify: `tools/Caprica/Caprica.exe --help`.
- **Spriggit** — ESP ↔ YAML/JSON serialization.
  - Acquire, **pinned to 0.41.0** (the version every serialize/deserialize command here was tested against; needs the **.NET 10 SDK**, the single .NET requirement of the core tools; the optional House Carl gateway also needs the .NET 9 runtimes, see `docs/housecarl.md`): `dotnet tool restore` from the toolkit root (installs exactly the version in `.config/dotnet-tools.json`, also 0.41.0, as a local tool you run as `dotnet tool run spriggit`) or `dotnet tool install --global Spriggit.CLI --version 0.41.0`. Either works with everything here: `tools/spriggit-cli.sh`, the ESP guard `tools/esp-verify-wrapper.sh` and `tools/toolchain-check.sh` use a global `spriggit` when one is on PATH and fall back to the local tool otherwise. **Never install it unpinned**: you get whatever is newest, not what was tested. 0.41.0 ships only a `net10.0` tool, so on an SDK-9 machine it starts, prints a version, and cannot serialize. `bash tools/toolchain-check.sh` reports DEGRADED when the installed version differs from the pin (older or newer) or its target SDK is missing.
  - The first round trip of an ESP another tool wrote is **not byte-stable** (on 0.40 and 0.41 alike): deserializing unchanged YAML gives an ESP of the same size whose records can come out in a different order; after that the output is a fixed point. To prove a round trip lossless, re-serialize and compare the YAML.
  - Still pass the version `spriggit --version` prints as `--PackageVersion`, never a hardcoded one.
  - For deeply-nested output paths that trip `UnauthorizedAccessException`, call `tools/spriggit-cli.sh` (same args; it runs in a shallow workspace and copies the result back).
  - Verify: `spriggit --help`.
- **AutoMod CLI** — NIF / BSA / audio / MCM / ESP one-liners.
  - Acquire, **pinned to the tested tag**: `git clone --branch v1.11.2 https://github.com/SpookyPirate/spookys-automod-toolkit tools/automod` (v1.11.2 = commit `e1c34ea34cae4b9a1a547ea6a90c8a91412d689e`).
  - Build: `bash tools/automod-cli.sh --rebuild`. Needs only the **.NET 10 SDK** (the single .NET requirement of the core tools) and, the first time, internet for the NuGet restore. The wrapper does four things, so you don't have to:
    0. puts the clone on the tested commit `e1c34ea`: a clean clone at any other commit (e.g. one an older toolkit told you to clone unpinned) is checked out to it, fetching the tag if needed; a clone with **your own changes is never touched** -- the wrapper stops and tells you to commit or stash them first. The same goes for a detached commit of your own that no branch or tag holds (it tells you how to give it a branch). Its own one-line patch (step 2) is undone before moving, so it never blocks the move;
    1. writes `tools/automod/global.json` selecting SDK 10 (`{ "sdk": { "version": "10.0.100", "rollForward": "latestFeature" } }`), replacing the `8.0.100` pin older toolkits told you to create;
    2. retargets the clone from net8.0 to net10.0 — a one-line **local patch** of upstream's `Directory.Build.props` (`git -C tools/automod diff` shows it). Retargeting, not roll-forward: a net8.0 build does not start on a machine with only .NET 10 unless `DOTNET_ROLL_FORWARD=Major` is set on every launch;
    3. builds the **Cli project only** (the WPF `Setup` project targets `net8.0-windows` — never build it headless). The built artifact is `src/SpookysAutomod.Cli/bin/Release/net10.0/spookys-automod.dll`, and `toolkit-built-from.txt` beside it records the commit it was built from. `bash tools/automod-cli.sh --built` (and `tools/toolchain-check.sh`) compares that with the pin, and reads DEGRADED for a build made with local changes or a DLL replaced after its last `--rebuild`.
  - Upgrading from a toolkit that built AutoMod for net8.0: the wrapper refuses the old build and tells you to run `--rebuild`; no clean is needed. Move a placed `bsarch.exe` from `bin/Release/net8.0/tools/bsarch/` to `bin/Release/net10.0/tools/bsarch/` (`archive create`/`extract` need it).
  - Run via the wrapper: `bash tools/automod-cli.sh <module> <command> --json`. Never `dotnet build` it yourself: that skips the commit check and the retarget.
  - Verify: `bash tools/automod-cli.sh esp --help --json`.
- **PyFFI** — LE-format NiTriShape geometry edits.
  - **Works on any modern Python (verified through 3.14) as long as `setuptools` is installed in that same environment** — no dedicated Python 3.10 needed. PyFFI 2.2.3's real (and only) Python-version blocker is a single unconditional `from distutils.cmd import Command` in `pyffi/utils/__init__.py` (used only by an unused, `# pragma: no cover` doc-building helper class) — `distutils` was removed from the stdlib in Python 3.12 (PEP 632). Installing setuptools beside it fixes it: setuptools ships its own vendored `distutils` plus a compatibility shim that transparently satisfies that import, which is the officially-sanctioned PEP 632 migration path, not a fragile workaround. Confirmed end-to-end on Python 3.14.6: imported cleanly, read a real 735-block Skyrim skeleton NIF spanning ~15 block types (NiNode, several bhk\* physics types, controllers, etc.), modified it, wrote it back out, and re-read the result — all clean. The upstream [niftools/pyffi](https://github.com/niftools/pyffi) repo itself has had no real commits since **January 2020** (only a stray dependabot branch since), so this is the practical path forward rather than expecting an upstream fix there.
  - Acquire, **pinned and hash-locked** (PyFFI 2.2.3 + setuptools 84.0.0, plus lz4 4.4.5 for `scripts/read-save.py`), with the Python you will run it from, from the toolkit root: `python -m pip install --only-binary=:all: --require-hashes -r tools/requirements-tools.lock`. pip refuses any file whose hash is not in the lock. MEASURED 2026-10-05: installs into fresh venvs on Python 3.10.10 and 3.12.10, and on both PyFFI reads a real NIF and lz4 decompresses a real save.
  - The `time.clock = time.perf_counter` monkey-patch (recipe: the `skyrim-nif` skill) is still required (`time.clock` was removed in Python 3.8 — the only OTHER Python-version issue, also the only one ever reported upstream, still open/unfixed as [GitHub issue #80](https://github.com/niftools/pyffi/issues/80)).
  - Verify with that same interpreter: `python -c "import pyffi; print(pyffi.__version__)"`.
- **PyNifly** — SSE BSTriShape read/write + animation/controller authoring + the independent parse gate.
  - Acquire: download `io_scene_nifly.zip` of release **V27.2.0** (see [Pinned downloads](#pinned-downloads)), check its SHA-256, and extract it into `tools/pynifly/` so the prebuilt DLL lands at `tools/pynifly/io_scene_nifly/NiflyDLL.dll` (the version this was verified with: 27.2.0; the zip also contains `io_scene_nifly/pyn/NiflyDLL.dll`, and that copy is never loaded). One PyNifly version for the library and the Blender add-on: 29.1.0 failed re-validation (2026-10-06) because its Blender add-on strips effect shaders on a round trip when the texture is not beside the NIF. No build step. (A `git clone` of the repo does NOT contain the compiled DLL — it only ships in the release zip.)
  - Verify: `python -c "import sys; sys.path.insert(0,'tools/pynifly/io_scene_nifly'); from pyn import pynifly"` (it loads the DLL on import (27.2.0 and later; `NifFile.Load` is a no-op)).
- **Blender (headless)** — NIF mesh repair + render-to-PNG verification.
  - Acquire: Blender **4.2.9 LTS** (`blender-4.2.9-windows-x64.zip`, see [Pinned downloads](#pinned-downloads)) — check its SHA-256, then install the PyNifly Blender add-on: the **same** 27.2.0 `io_scene_nifly.zip` as PyNifly above.
  - Verify: `blender --background --version`.
- **NifSkope** — independent visual NIF render gate (GUI).
  - Acquire: **NifSkope v2.0.dev9** from the maintained hexabits fork (`NifSkope_2_0_2023-09-29-x64.7z`, see [Pinned downloads](#pinned-downloads)); check its SHA-256. The original niftools repo stopped at v2.0.dev7 (2017); dev9 is the build this toolkit was tested with.
  - Verify: launch it and open any NIF.
- **ReSaver CLI** — headless `.ess` save parse / cross-reference / clean / changeform-level diagnostics.
  - Acquire: ReSaver **6.0.643** (FallrimTools, Nexus mod 5031, file `Resaver 6.0.643-5031-6-0-643-1694115572.7z`; see [Pinned downloads](#pinned-downloads)): drop `ReSaver.jar` plus its `lib/` folder into `tools/resaver-cli/` and compare `ReSaver.jar`'s SHA-256 with the table. Needs JDK 17+ (JDK 21 LTS recommended, the same JDK 21.0.12 the devcontainer uses; a manual step, for you to run -- an agent may not install this: `winget install --id Microsoft.OpenJDK.21 --exact --version 21.0.12.8 --no-upgrade`). The wrapper auto-compiles its small driver on first run.
  - The read/diagnostic ops layer a small **analysis overlay** (modified ReSaver source, Apache-2.0 — see `tools/resaver-cli/analysis-overlay/NOTICE.md`) in front of your jar for extra changeform parse coverage; write ops always run the STOCK jar (corruption safety), and if the overlay can't compile against your ReSaver version the wrapper falls back to stock parsing automatically.
  - Verify: `bash tools/resaver-cli.sh info <save.ess>` prints JSON.
- **cosave-info** — READ-ONLY structural survey of an SKSE `.skse` co-save (which mods stashed co-save data + how much — the mod-state landscape the `.ess` never exposes). No install beyond Python 3.
  - Verify: `bash tools/cosave-cli.sh <SaveN_...>.skse` prints JSON (the `.skse` sits next to its `.ess`).
- **7-Zip** — reads mod archives (`.7z`, `.zip`, `.rar`) for `tools/archive-triage.py`.
  - Acquire: **7-Zip 26.00** (see [Pinned downloads](#pinned-downloads)) and check its SHA-256 before running it. Windows: the x64 installer `7z2600-x64.exe` (the default install puts `7z.exe` in `C:\Program Files\7-Zip\`). Linux: `7z2600-linux-x64.tar.xz`, whose console program is `7zz`; put it on `PATH`. 7-Zip is a program you install yourself -- an agent may not install it.
  - The tools look for it in this order: `$SEVENZIP` (a full path; when it is set and wrong, nothing else is tried), then `7z`, `7zz` or `7za` on `PATH`, then `7-Zip\7z.exe` under Program Files.
  - Verify: `7z` (or `7zz`) with no arguments prints `7-Zip 26.00` on its first line.
- **`tools/nexus.sh`** — built-in Nexus API helper (no install). Needs your Nexus key (see *Nexus API key* below).
  - Verify: `bash tools/nexus.sh mod <id>` prints a mod's name/version.
  - Also: `find "<name>"`, `page <id> --since <version>`, `contents <id>` (skill `mod-update-check`). Any failure prints `RESULT: COULD-NOT-CHECK` and exits 3.

## Pinned downloads

The exact files this toolkit was tested with. **Check the SHA-256 before you unpack anything**: in Git Bash
`sha256sum <file>`, in PowerShell `Get-FileHash <file> -Algorithm SHA256` (it prints upper case; compare
ignoring case). A different hash means a different file than the one tested — do not use it.

| Tool | Version | File | SHA-256 | From |
|---|---|---|---|---|
| House Carl | 2.0.5 | `houseCARL-2.0.5.zip` | `c3d3ca7e124d92c65f7a1b935b4a598e8d69f2f1fe900224bab5d1b352f0df19` | <https://github.com/Avick3110/houseCARL/releases/download/v2.0.5/houseCARL-2.0.5.zip> |
| Champollion | v1.3.2 | `Champollion.v1.3.2.zip` | `ea53054276ac8006ccd3b323286bfbc6e34a454fa419d08da9bd440cbd31b383` | <https://github.com/Orvid/Champollion/releases/download/v1.3.2/Champollion.v1.3.2.zip> |
| Caprica | v0.3.0 | `Caprica.v0.3.0.7z` | `0037e54cf3a1021e976278d9a637e8ae541aaf9ebeb8d274b6c2cfe32f4ee559` | <https://github.com/Orvid/Caprica/releases/download/v0.3.0/Caprica.v0.3.0.7z> |
| PyNifly | 27.2.0 | `io_scene_nifly.zip` | `920a17c8c4001c4f2a12eff85aad8e54fd19ebcf7e828624733f1fb2bba972da` | <https://github.com/BadDogSkyrim/PyNifly/releases/download/V27.2.0/io_scene_nifly.zip> |
| PyNifly Blender add-on | 27.2.0 | `io_scene_nifly.zip` | `920a17c8c4001c4f2a12eff85aad8e54fd19ebcf7e828624733f1fb2bba972da` | <https://github.com/BadDogSkyrim/PyNifly/releases/download/V27.2.0/io_scene_nifly.zip> |
| Blender | 4.2.9 | `blender-4.2.9-windows-x64.zip` | `223cd22afc5cab3f8cb43006574f1c962ff9bb9e07418c3f4f2542b7c34899cb` | <https://download.blender.org/release/Blender4.2/blender-4.2.9-windows-x64.zip> |
| NifSkope | v2.0.dev9 | `NifSkope_2_0_2023-09-29-x64.7z` | `8d608e558b2cdd32edb7e09acc26f491f8ec2faa11fac984682570df55330e0d` | <https://github.com/hexabits/nifskope/releases/download/v2.0.dev9/NifSkope_2_0_2023-09-29-x64.7z> |
| 7-Zip | 26.00 | `7z2600-x64.exe` | `6fe18d5b3080e39678cabfa6cef12cfb25086377389b803a36a3c43236a8a82c` | <https://github.com/ip7z/7zip/releases/download/26.00/7z2600-x64.exe> |
| 7-Zip (Linux) | 26.00 | `7z2600-linux-x64.tar.xz` | `c74dc4a48492cde43f5fec10d53fb2a66f520e4a62a69d630c44cb22c477edc6` | <https://github.com/ip7z/7zip/releases/download/26.00/7z2600-linux-x64.tar.xz> |
| ReSaver | 6.0.643 | `ReSaver.jar` inside `Resaver 6.0.643-5031-6-0-643-1694115572.7z` | `af6f632d561de9d2f8b97f860d5f1c997a79fb77d821d31a3e0111fabb22c1d3` | Nexus mod 5031 (manual download) |
| BSArch | 1.0 | `bsarch.exe` inside `BSArch-64745-1-0-1769253446.zip` | `8ca75290b2ec4c821802a3975f10e1d2ddb4e94297357340b1a9b9979e947911` | Nexus (Fallout New Vegas) mod 64745, *Old files* (manual download) |

How each hash was obtained (MEASURED 2026-10-05): Champollion, Caprica and NifSkope were downloaded and hashed,
and the unpacked `Champollion.exe`, `Caprica.exe` and `NifSkope.exe` are byte-identical to the copies this toolkit
was tested with; both PyNifly rows equal the SHA-256 GitHub publishes for that release asset (27.2.0 fetched and hashed
2026-10-06; one file serves both rows); Blender's row is the hash blender.org publishes in `blender-4.2.9.sha256`, and the
tested Blender reports `4.2.9 LTS`. 7-Zip (MEASURED 2026-10-13): 7-zip.org's download page links both files from the ip7z/7zip GitHub release `26.00` and publishes no hash; both were downloaded and hashed and equal the SHA-256 GitHub publishes for those release assets, and the `7z.exe` and `7z.dll` inside the x64 installer are byte-identical to the 7-Zip 26.00 that `archive-triage` was tested with (on Windows; the Linux `7zz` was hashed, not run). `python scripts/thirdparty-audit.py` re-checks each published hash.

**Nexus gives no direct download link and no published hash**, so the two Nexus rows cannot be checked before
you unpack: get exactly the named file (the mod's *Files* tab; BSArch 1.0 is under *Old files*), unpack it, and
compare the hash of the file named in the *File* column — `ReSaver.jar` (its manifest says
`Implementation-Build: 643`) and `bsarch.exe` (it prints `BSArch v1.0`). Those two hashes are of the tested files
themselves.

**ReSaver's `lib/`.** The same archive's `target/lib/` becomes `tools/resaver-cli/lib/`, and every jar there is on
the driver's classpath, so each file is pinned too. *Required* files are the ones the driver was tested with; the
others (the GUI's JavaFX, picocli, the build's JUnit) may be left out, but a file that is present must be the
pinned one, and a jar not in this table must not be there. `bash tools/resaver-cli.sh verify` checks
`ReSaver.jar` and `lib/` against these hashes (it needs no JDK). MEASURED 2026-10-13: hashed from an extraction of
`Resaver 6.0.643-5031-6-0-643-1694115572.7z` whose `ReSaver.jar` matches the row above; the four required jars are
byte-identical to the tested install's.

| `lib/` file | Required | SHA-256 |
|---|---|---|
| `annotations-16.0.2.jar` | yes | `245abad9a39eab1266ac9a8796980f462577e708ef3f6d43be2e008e4b72b9b4` |
| `j2html-1.5.0.jar` | yes | `de40093737108d75f5bf832cce596308e8f90c36d36383b7aec40dff224d1e64` |
| `juniversalchardet-1.0.3.jar` | yes | `757bfe906193b8b651e79dc26cd67d6b55d0770a2cdfb0381591504f779d4a76` |
| `lz4-pure-java-1.7.0.jar` | yes | `452da15ae42910d6475a0b9d73a404cbfd37d8ebe29558ad54439fe4b0d4e089` |
| `apiguardian-api-1.1.2.jar` | no | `b509448ac506d607319f182537f0b35d71007582ec741832a1f111e5b5b70b38` |
| `javafx-base-11-win.jar` | no | `901e382f96b53c91a949f91b07201ec5433bf3c864186cb50446a6b0951aa778` |
| `javafx-base-11.jar` | no | `df7ef2e994be4a86153be0aaf0dcafa697ba4be0db55caefb8bda85e02d66ead` |
| `javafx-controls-11-win.jar` | no | `af625a0b813f051bcb5bd1f00a35af61648264d7b792aabcff032f57dfbfa9a4` |
| `javafx-controls-11.jar` | no | `dec3f34ca06ca9be71ce8c72646a31eb751b1df5a2b354dcd10fb64df842da4f` |
| `javafx-graphics-11-win.jar` | no | `5a278411a777a1215b1b6c0c6c2192c94857fe0253a6de805c67a9c9d29d29d1` |
| `javafx-graphics-11.jar` | no | `6b846071b1fdae103f1b0e80c988d9d0b9cae37391b00f8ea3dc11e1e8e021b0` |
| `javafx-swing-11-win.jar` | no | `585ec5d61cbbe53df15e2713527b79364ac281de4fbc11d862a04c52f8ecc578` |
| `javafx-swing-11.jar` | no | `ba599dc31f2f8f098346198ce0125c2aee5358f89e13e7f59cdb290ab6807f59` |
| `junit-jupiter-5.8.2.module` | no | `d9df47b3c7431883ab1f1f1d820c2980ebcbd753edc4c82b99bf5ec2c533ad2e` |
| `junit-jupiter-api-5.8.2.jar` | no | `1808ee87e0f718cd6e25f3b75afc17956ac8a3edc48c7e9bab9f19f9a79e3801` |
| `junit-jupiter-engine-5.8.2.jar` | no | `753b7726cdd158bb34cedb94c161e2291896f47832a1e9eda53d970020a8184e` |
| `junit-jupiter-params-5.8.2.jar` | no | `d1c22d6fe5483568c08c8913f34abd2303490c3480ce6c18a2ea31c65e44102a` |
| `junit-platform-commons-1.8.2.jar` | no | `d2e015fca7130e79af2f4608dc54415e4b10b592d77333decb4b1a274c185050` |
| `junit-platform-engine-1.8.2.jar` | no | `0b7d000f8c3e8e5f7d6b819649936e7b9938314e87c8f983805218ea57567e59` |
| `opentest4j-1.2.0.jar` | no | `58812de60898d976fb81ef3b62da05c6604c18fd4a249f5044282479fc286af2` |
| `picocli-4.6.3.jar` | no | `b0a5159e926de8084ff066025142270443533656bc599b8bb31d14d11fd138a4` |

## Git for Windows

The hooks and `setup.sh` run under Git Bash, which comes with Git for Windows. This toolkit is tested with Git for Windows 2.55.0.5 (release `v2.55.0.windows.5`). Git is system software: an agent installs it only when your setup prompt authorizes exactly `git=Git.Git==2.55.0.5`, so normally you install it yourself. For you to run, in Windows Terminal:
`winget install --id Git.Git --exact --version 2.55.0.5 --no-upgrade`
or download `Git-2.55.0.5-64-bit.exe` from <https://github.com/git-for-windows/git/releases/tag/v2.55.0.windows.5> and check that its SHA-256 is `d065a4e23c3d9a6b5073d609b5be0830227ec3ca053c083ba385061ddfaf94c6` before running it. MEASURED 2026-10-13: downloaded and hashed, equal to the digest GitHub publishes for that release asset and to the installer hash in winget's `Git.Git` 2.55.0.5 manifest; the installer's Authenticode signature is valid.

**Git for Windows 2.55 changed `$OSTYPE`.** From 2.55 (bash 5.3 on the MSYS2 3.6 runtime) Git Bash reports `$OSTYPE` as `cygwin`; 2.54 and earlier report `msys`. `uname -s` still starts with `MINGW`. The hooks support both from toolkit commit `d908198` (2026-10-11, first released in v4.0.0): they treat `cygwin` as Git Bash when the MSYS runtime (`/usr/bin/msys-2.0.dll`) is present. A v4 development checkout from `d6b1ca1` up to, not including, `d908198` fails closed under Git 2.55 -- nothing is let through, but every hook call reports GUARD INERT -- so **update the toolkit before you update Git**. v3.9.1's hooks do not read `$OSTYPE` (READ from its source; not run under Git 2.55).

## Why some tools have wrappers

1. **AutoMod** — `tools/automod-cli.sh` invokes the **prebuilt `spookys-automod.dll`** directly rather than `dotnet run`. This fixes the per-call recompile / MSB1025 failures that the old `dotnet run` form produced; rebuild once with `--rebuild` after changing AutoMod source.
2. **Spriggit** — deep/nested output paths throw `UnauthorizedAccessException`. Use `tools/spriggit-cli.sh`, which runs in a shallow workspace and copies the result back. It **preserves the exact ESP basename** (= the Spriggit ModKey) so FormKey master references aren't corrupted.

The xelib loader hazard (fix 3) is a usage rule; it lives in the `xelib` skill.

## Nexus API key

Nexus also offers a free REST+GraphQL API ([api-docs.nexusmods.com](https://api-docs.nexusmods.com/)) for mod **version, update dates, changelogs, file info, and dependencies** — useful for update detection and migration/triage. Supply your own free **Personal API Key** (nexusmods.com → Site preferences → API Access) and send it raw as the `apikey` request header. Skyrim SE/VR domain = `skyrimspecialedition`; endpoints take `game_domain_name` + `mod_id`. A personal key is for personal/local use only — never commit or log it, and a shared tool must have each user supply their own. In this toolkit the key is resolved **file-first, then env**: `tools/.nexus_api_key` (one line) if present, else `$NEXUS_API_KEY`. The key file is gitignored — never echo, log, or commit it. `tools/nexus.sh` wraps this (e.g. `bash tools/nexus.sh mod <id>`).
