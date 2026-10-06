# Container vs. Windows — Tool Routing Guide

Credit: this file's core insight — and the devcontainer this toolkit ships — originated in
[@aaronputty](https://github.com/aaronputty)'s fork,
[putty-skyrim-claude-toolkit](https://github.com/aaronputty/putty-skyrim-claude-toolkit).

Default to the container for any task. Only cross to Windows when the task genuinely requires the
Windows DLL or an active MO2 virtual filesystem.

The workspace and all game/profile bind mounts are read-only, including workspace aliases.
Write generated output in container-local scratch and publish deliberately through Windows.
Dependency failure stops startup. See [integration safety](harness-integrations.md) for locks,
mount contracts, disposable sentinel checks and the COULD NOT CHECK status.

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

## Writing from inside the container

The workspace is a read-only bind, so every workflow that writes uses a container-local path.
Measured 2026-10-02 on a fresh clone (no `node_modules/`, nothing pre-created):

| Workflow | Where it writes | Notes |
|---|---|---|
| Node dependencies | `/opt/skyrim-node/node_modules` | `npm ci` from the lockfile; `NODE_PATH` serves `require()`, not ES-module `import` |
| Spriggit serialize/deserialize | e.g. `/tmp/<name>` | `bash tools/spriggit-cli.sh` (uses the restored local tool); a `/workspace` destination is refused up front |
| pytest | `/tmp/skyrim-pytest-cache` | set through `PYTEST_ADDOPTS` |
| `tests/run_harness_tests.py` | `/tmp/skyrim-harness-evidence/` | automatic when the checkout is not writable; override with `HARNESS_EVIDENCE_ROOT` |

To bring a result back to the host, publish it deliberately from Windows (or copy it out with
`docker cp`); the container cannot modify the host workspace or the game/profile mounts.

## Spriggit vs. xelib

| Capability | Spriggit | xelib |
|---|---|---|
| Read records | Yes (YAML) | Yes |
| Write records | Yes (YAML round-trip) | Yes |
| Resolve override chain | No — single file only | Yes (needs the MO2 VFS if on MO2) |
| Runs in the container | Yes | No (Windows DLL) |

## Using the devcontainer

Files: `.devcontainer/`, `devshell.sh`, `devshell-docker.sh`.

A reproducible Linux container (Python 3.11, Node 24 LTS, .NET 9, JDK 17) for the tools that don't need
Windows or an active MO2 session: Spriggit ESP inspection/diffing, FOMOD/JSON generation, unit-testing
mod logic, ReSaver CLI. xelib and
anything load-order-dependent still needs Windows (and MO2's executables list, on an MO2 install).
The checkout is mounted read-only; where each workflow writes instead is in
*Writing from inside the container* above.

- **`bash devshell-docker.sh`** — builds the image on first run and drops you into a shell. Needs
  Docker Desktop and `jq` on the host (it refuses to start without it). Reads its mount sources
  straight out of `.devcontainer/devcontainer.json` (which `setup.sh` fills in with your real mod
  paths), so it never hardcodes them.
- **`bash devshell.sh`** — same thing via the `@devcontainers/cli` (`npm install -g
  @devcontainers/cli`), for editor integration (VS Code's Dev Containers extension uses the same
  `devcontainer.json`).
- `setup.sh` fills in `.devcontainer/devcontainer.json`'s mount paths automatically: on an MO2
  install, from the detected instance's mods/profile/overwrite folders; on stock/Vortex, from `Data/`
  and the INI config folder.
- Confirmed working end-to-end: a real third-party ESP with records serializes and inspects
  correctly via `examples/inspect-esp.py`. **One confirmed limitation:** a plugin using localized
  strings (the `Localized` flag) fails to serialize in the container — see
  *One confirmed Spriggit-in-container limitation* above.
- The SKSE-plugin cross-compile toolchain (LLVM/xwin/xmake) from the source fork is **not** part of
  this default image — it roughly doubles the build for a capability that's still a single
  unvalidated experiment. See [skse-cross-compile.md](skse-cross-compile.md) if you want to add it yourself.
