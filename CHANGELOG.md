# Changelog

## v3.8.2 — 2026-09-06

### Fixed

- **`crash-triage` read 1 of 7 dumps on CrashLoggerSSE 1.2x and reported them as
  "no exception".** Four format differences, reported and fixed by
  [@yurar123](https://github.com/yurar123) in #8: the exception-type pattern
  excluded `C++ Exception` (the `+` and lowercase fall outside `[A-Z_]+`), the stack
  header changed from `PROBABLE CALL STACK` to `CALL STACK ([P]robable / [S]tack
  scan):`, frame lines gained `[P]`/`[S]` markers, and every C++ throw collapsed
  onto its shared throw site instead of being distinguished by its `Info` string.
  Both formats are supported; verified against 28 real Skyrim VR dumps on
  CrashLoggerSSE v1-15-0-0 with byte-identical output.

- **`setup.sh` told you to install the .NET SDK that cannot run Spriggit.** It
  recommended SDK 8 and then printed `Found .NET SDK: 8.0.424`, which reads as
  success — but Spriggit.CLI targets `net9.0`, so `dotnet tool install Spriggit.CLI`
  succeeded and every invocation afterwards died with "You must install or update
  .NET". Because `esp-verify-wrapper.sh` drives spriggit, the cross-reference
  integrity guard was silently unusable too. Setup now checks whether the .NET 9
  **runtime** is present rather than reporting whichever SDK it found, and names
  both SDKs when neither is installed (AutoMod pins SDK 8.0.x via `global.json`
  with `rollForward: latestFeature`, which does not roll 8 → 9).

### Added

- **`crash-triage` now says when it has stopped understanding its input.** The
  accounting line cannot: every file lands in exactly one bucket by construction,
  so it balances identically and says nothing about whether the parser still works.
  Two format-agnostic guards now gate the verdict:
  - **unparsed ratio** — the share of dumps whose exception line failed. Priced
    against real data: a healthy install has 1 unparsed in 28 (4%, a crash inside
    no loaded module that can never parse); the 1.2x break was 86%. Fires when
    nothing parses at all, or when both >25% and at least 2 dumps fail.
  - **frames coverage** — a changed stack header leaves every dump "parsed" with
    zero frames and no complaint, so the unparsed count alone would have missed two
    of the three 1.2x changes. Every parsed dump yielding zero frames is reported.

### Changed

- ⚠ **`crash-triage` has a new non-zero exit.** A run that previously printed
  `RESULT: OK` and exited 0 can now print `RESULT: PARSER DEGRADED` and exit **1**
  when the guards above trip. If you gate a script on its exit code, note that 1
  now also means "the report above covers only the dumps that parsed". Exit 2
  (could not check) and the OK path are unchanged.


## v3.8.1 — 2026-08-27

### Fixed

- **`skyrim-winner` now says how to fix the one error every new user hits.**
  Without xeditlib installed it reported `cannot load xelib` followed by two
  full Node require stacks — accurate, unreadable, and silent on the one
  thing that resolves it. It now names the install command and the fact that
  it must be run from the toolkit root, and trims the require-stack noise off
  the resolver errors it still reports.

  Found by running the shipped v3.8 zip from a fresh extraction rather than
  from the dev install, where xeditlib is always present and this path never
  executes.

## v3.8 — 2026-08-27

Three tools ported in from the dev install, each of which answers a question you
would otherwise settle by squinting at a wall of text. Every accounting guard in
them is mutation-tested, and building them turned up two ways a tool can be
confidently, silently wrong about which files it is even reading.

### Added

- **`tools/skyrim-winner.sh`** — "which plugin actually wins this record?" Loads
  the **full** active load order and asks xEdit, then prints the winner
  (`winner`), the complete override chain in order (`chain`), or every record a
  plugin loses (`conflicts`). Accepts a bare FormID or a plugin-scoped local id
  (`SomeMod.esp:000871`).

  No index and no cache. xEdit already computes conflict resolution correctly
  and the load order is explicit and total, so this asks it and prints the
  answer — one source of truth, and it is not this script. A full 659-plugin
  load costs ~4 seconds, which is why there is no cache and therefore no
  freshness contract to get wrong.

  The alternative — a script that loads the handful of plugins that look
  relevant — gives an answer only as complete as the guess. A plugin outside the
  list can win, and the script never sees it. A too-short list can also load
  **nothing** and still resolve, producing a confident answer from an empty tree.

  Requires **xeditlib**. Judge success by the wrapper's exit code (0 ok / 2 error
  / 3 did-not-finish), never node's: the underlying script exits **127 on
  complete success** because Node's stdio flush fails while koffi still holds
  `XEditLib.dll`.

- **`tools/papyrus-triage.py`** — buckets `Papyrus.N.log` by normalized message
  shape and attributes each bucket to the script or plugin that produced it.
  Bundled, pure standard library, no install.

  The unit of accounting is the **entry, not the line**: a Papyrus entry is
  multi-line, and the dev install's log has 8426 lines but only 5292 timestamped
  entries. A line-based sum is ~37% wrong. Severity is also spelled **both**
  `warning:` (99) and `WARNING:` (662) in the same file; a case-sensitive match
  loses 87% of them.

- **`tools/crash-triage.py`** — reduces CrashLogger dumps (~5,500 lines each,
  ~4,600 of it raw stack) to ranked `module+offset` signatures, and labels the
  ones your knowledgebase has already ruled on, so a settled crash prints as
  `[ACCEPTED - do not re-investigate]` instead of being chased again. The `KNOWN`
  dict ships with three rulings from the dev install and is meant to be edited.

- **`tools/skyrim_paths.py`** — shared, deliberate discovery of the per-game
  folders, replacing the hardcoded dev paths the tools arrived with.

### Fixed

- **`crash-triage` matched only half the crash logs.** CrashLoggerSSE writes
  `crash-*.log`; older builds write `crash-*.txt`. Both extensions coexist in the
  same folder, so globbing one of them does not fail — it reports a confident,
  complete-looking total built from whichever half it caught. Measured on the dev
  install: **8 `.txt` (all June 2025) beside 20 `.log` (2026)**, and the tool
  reported "8 log(s)" with its accounting check balancing perfectly, because a
  denominator can only account for what it was handed. Now matches
  `crash-*.(txt|log)` case-insensitively, while still excluding the plugin's own
  `CrashLogger.log`.

- **The tools no longer guess which Skyrim install they are reading.** A machine
  that has ever had more than one Skyrim carries several copies of the same state
  — `%LOCALAPPDATA%\Skyrim VR|Skyrim Special Edition|Skyrim\plugins.txt`, and
  `Documents\My Games\<game>\` with its INIs, Papyrus logs and SKSE crash dumps.
  The wrong one **reads perfectly**. Measured on the dev machine, a VR-only
  install: the SSE `plugins.txt` exists, holds 659 plugins, is 18 days stale, and
  disagrees with the VR file about which plugins are active — a complete,
  plausible, wrong load order that no loader would reject. The tools now probe
  `Skyrim VR → Skyrim Special Edition → Skyrim`, print every candidate they
  ignored to stderr, and honour `$PLUGINS_TXT`, `$PAPYRUS_LOG_DIR`,
  `$SKSE_CRASH_DIR`.

  This also corrects the toolkit's own long-standing note that the SSE
  `plugins.txt` "does not exist on a VR install". It may well exist.

- **`skyrim-winner` resolved a plugin-scoped FormID against the wrong space.**
  `SomeMod.esp:000871` passed a **local** id where xelib's `getRecord()` wants a
  full FormID, so real records came back as "no record found". It now matches on
  the low three bytes within the named plugin.

- **A malformed FormID is rejected, not sanitised.** Input like `000871!` or
  `Shinso.esp/000843` previously had its stray characters stripped and was
  looked up anyway — the one failure mode worse than an error, because it
  answers about a **different, real** record.

- **`skyrim-winner` and `resaver-resolve-names` terminate the game path.**
  XEditLib requires a trailing separator, but only reports it one call later as
  `SetGameMode failed`, and only when `setGamePath` runs before `setGameMode`.
  Both scripts now derive the game root from their own location — no hardcoded
  path, `$GAME_ROOT` to override.

### Testing

- 16 behavioural tests for the two triage tools, against hand-authored fixture
  logs (real logs carry usernames and are not committed), and 10 for
  `skyrim-winner`'s FormID parsing and path handling.
- 4 new mutations, bringing the gate to **22**. Each reverts one shipped fix on a
  throwaway copy of the repo and asserts the guarding test goes red — including
  the two new crash-glob guards.
- The mutation gate caught one of its own anchors going stale during this
  release, which is the whole point of it.
- **The Node tests now run on Windows too.** They cover path separators and
  file discovery, and the `node` job runs on Linux only — where `path.sep` is
  `/` and a Windows-separator bug cannot surface. They are now part of the
  behavioural job, which blocks on both legs.
- Job names no longer claim a file count (`Shell (16 scripts)`), which was
  already wrong and would go wrong again on every file added.

## v3.7 — 2026-08-26

v3.6 added CI that checks the toolkit's *structure*. This adds the layer that
checks its *behavior* — and closes two gates that were reporting success while
examining nothing.

### Fixed

- **`setup.sh` could silently corrupt every safety hook.** `JQ_PATH` was
  substituted into all four hooks via `sed`, and GNU sed reads backslash
  sequences in replacement text as escapes. A Windows-style jq path was not
  merely wrong, it was destroyed — measured:

  ```
  supplied  C:\Users\testuser\AppData\Local\Microsoft\WinGet\Links\jq.exe
  produced  JQ="C:SERS<TAB>ESTUSERAPPDATAocalmicrosoftwingetinksjq.exe"
  ```

  `\U` switched on upper-casing, `\t` became a literal tab, `\L` switched on
  lower-casing, and every backslash was eaten. The hooks then point at a jq that
  does not exist and fail open, silently, on a toolkit whose whole purpose is
  safety rails.

  This is the same defect fixed for `LOCALAPPDATA` and the Documents path in
  v3.2.1. That fix normalized two variables; an audit of every variable reaching
  `sed` found `JQ_PATH` as the one it missed. Normalized after all three of its
  assignment branches converge, so the `which` result, the known-locations loop
  and the post-`winget` re-detect are all covered.

  **Reachability is unconfirmed** — every fallback path in `setup.sh` is already
  forward-slashed, so `which jq` returning a Windows path is the only trigger and
  it has not been observed in the wild. This is a defensive fix against a
  proven-catastrophic outcome, not a confirmed live bug.

- **`npm test` was a gate that could not fail.** It runs `node --test`, which
  with zero test files printed nothing and exited 0 — so the CI Node job reported
  success having examined nothing at all. There are now six real Node tests
  covering the pure-JS half of `active-plugins.js`, plus a guard asserting the
  run actually reports passing tests.

- **`setup-smoke` asserted almost nothing.** It ran `setup.sh` and then only
  checked that `settings.json` parsed — every path bug this toolkit has shipped
  would have passed it. It now also checks that no `{{PLACEHOLDER}}` survived,
  that the Game root and Mod manager lines were written, and that the jq
  placeholder was substituted in every hook.

- **Extensionless test shims checked out as CRLF.** `.gitattributes` pinned
  `*.sh` to LF but not `tests/bin/*`, so a fresh clone produced a shim with CRLF
  line endings, which dies on Linux with `bad interpreter: /bin/bash^M`.

### Added

- **A behavioral test suite** (`tests/`, pytest) that runs the *real* `setup.sh`
  against generated MO2 and stock layouts and asserts on the paths it **wrote**,
  not on what it printed. Every test is anchored to a bug this toolkit actually
  shipped: the v3.2.1 `sed` corruption and MSYS game root, and the v3.5.4
  portable-MO2 detection, `@ByteArray` unwrapping and `..` normalization.

- **`tools/devbench-cli.sh` coverage** against a mock DevBench server: all four
  liveness states (running / paused / hung / no-save-loaded), the pre-1.11.0
  fallback, a dead port, 504 and 400 — asserting **exit codes**, which are the
  contract. The whole matrix runs again with `jq` masked off `PATH`, because
  `jget`'s no-jq branch is hand-rolled `sed` and the least-exercised code there.

- **A mutation gate** (`tests/test_mutations.py`, modelled on the X4 toolkit's
  `mutation_probe`). A regression test written after its bug was fixed passes on
  the first run, which proves nothing; the gate reverts each fix on a throwaway
  copy and asserts the guarding test *fails*. Nine mutations, and it earned its
  place immediately by catching two guards that asserted nothing:

  - `test_byte_array_wrapping_is_unwrapped` passed with the unwrap reverted.
    Without it, detection falls through to the stock branch, so no MO2 block is
    written (nothing containing `@ByteArray` to find) and the profile name still
    appears because it is part of the game directory path. Both assertions were
    true for entirely the wrong reason.
  - The `npm test` vacuity guard passed against its own mutation. Asserting a
    non-zero pass count is not enough: a file that registers no tests is itself
    counted as one passing test, so `pass 1` looks healthy.

- **Windows CI coverage** (`behavior` job, ubuntu + windows matrix, **both
  blocking**). All four historical path bugs were Windows-specific, so an
  ubuntu-only run would have caught none of them.

  The Windows leg earned its place on its first real run. It exposed that `bash`
  on a GitHub Windows runner resolves to `C:\Windows\System32\bash.exe` — the WSL
  launcher, which exits 1 with no distro installed — and that a workflow's
  `shell: bash` does not change what Python's `subprocess` finds. Every test that
  spawned a shell failed there with a bare exit 1 and no message. The suite now
  probes for a working `bash` rather than trusting `PATH`.

### Notes

- `tests/` is excluded from the release zip, via an **rsync exclude** rather than
  `.gitattributes export-ignore` — the zip is built with `rsync`+`zip`, not
  `git archive`, so `export-ignore` would have been silently inert.
- The Node test ships, because it sits next to the module it tests in `tools/`.
  Excluding it would recreate the vacuous `npm test` for anyone installing from
  the zip, which is the exact thing this release fixes.
- `KNOWLEDGEBASE.md` gains an entry on the `sed` backslash trap, including the
  rule that cost this toolkit two releases: normalize where the branches
  converge, not at each assignment.

## v3.6 — 2026-08-23

### Added

- **Continuous integration (GitHub Actions).** Every push and pull request now runs five jobs:
  shell (`bash -n`, shebangs, `shellcheck`), Python (`py_compile`, `ruff`), Node (`node --check`,
  `npm test`), Claude Code config validation, and a `setup.sh` smoke test that installs the toolkit
  into a stubbed game directory and runs it twice.

  Two of those checks exist because their failure modes are **completely silent at runtime**, which
  is the worst possible bug in a toolkit whose entire value is safety rails:

  - Every hook `command` in `.claude/settings.json` must point at a file that exists. A hook with a
    wrong path does not error — it simply never fires, and the protections you think you have are
    gone.
  - Every `SKILL.md` must carry `name` and `description` frontmatter, with `name` matching its
    directory. A malformed skill is ignored by Claude Code with no message.

  Relative Markdown links, `devcontainer.json` (parsed as JSONC — it has comments), and the
  settings/`package.json`/`dotnet-tools.json` files are validated too. `ruff` gates only on
  genuine-bug rules so pre-existing style findings do not redden the build.

- **Release automation.** Tagging `v*` builds the install zip (asserting `.claude/` actually made it
  into the payload), extracts that version's section from this changelog, and publishes a GitHub
  Release with the zip attached.

- **`.gitattributes`** pinning scripts to LF. Preventive: all tracked scripts are stored correctly
  today, but the repo ships a Linux devcontainer, and a Windows author with `core.autocrlf=true`
  committing CRLF into a shell script would break it at exec time with `bad interpreter: /bin/bash^M`.

### Knowledgebase

- **The DLL on disk is the source of truth for an SKSE plugin's installed version, not the mod
  manager.** Read the embedded File/Product version of `Data/SKSE/Plugins/*.dll` directly. A
  manually-installed or hand-reverted plugin is invisible to Vortex/MO2, so the manager's reported
  version can disagree with what the game actually loads.

## v3.5.4 — 2026-08-07

Two community-reported fixes, both verified independently here before merging.

### Fixed

- **Portable MO2 instances (Nolvus / Wabbajack layouts) were never detected.** Reported and fixed by
  [@Leit-motif](https://github.com/Leit-motif) (#5). On a portable instance, detection fell through
  to the stock/Vortex branch and wrote `Documents\My Games\...` plus `%LOCALAPPDATA%\...` for the INI
  and load-order paths — paths that exist but that no MO2 profile uses. Confidently wrong, which is
  precisely the failure MO2 support was added to prevent. Three independent causes, each of which
  alone was enough to break it:
  - `ini_get` didn't unwrap QSettings' `@ByteArray(...)` form, which MO2 writes routinely for
    `gamePath` and `selected_profile`. **This also broke the `MO2_INSTANCE_INI` escape hatch that
    v3.4 documented for portable instances** — so the documented workaround didn't work either.
  - Only global instances under `%LOCALAPPDATA%\ModOrganizer\` were probed. Nolvus/Wabbajack park the
    portable instance beside the game folder, so the game root's siblings are now probed too.
  - The sibling probe resolves through `..`, which would have written an unreadable
    `STOCK GAME/../MO2` instance path into `CLAUDE.md`. Normalized with `pwd -W`.

  Verified by rebuilding the reported layout and running the real `setup.sh` against it: portable
  instance detected with correct profile/mods/overwrite paths; a decoy sibling instance pointing at a
  *different* game correctly ignored (the `gamePath` equality gate holds); global instances with
  plain non-`@ByteArray` values still detected; non-MO2 installs still take the stock branch.

- **PyFFI does not need a dedicated Python 3.10 install.** Reported and fixed by
  [@awesmdiver](https://github.com/awesmdiver) (#4). Every mention of PyFFI told you to install a
  separate 3.10 to avoid breaking on 3.12+. That extra install is unnecessary: PyFFI 2.2.3's only
  version blocker is a single `from distutils.cmd import Command` in `pyffi/utils/__init__.py`, used
  by an unused doc-building helper, and `distutils` left the stdlib in 3.12 (PEP 632).
  `pip install pyffi setuptools` resolves it — setuptools vendors its own `distutils` plus an import
  shim, the sanctioned PEP 632 migration path.

  Verified here on 3.12 (the reporter verified 3.14.6, which between them brackets the range that
  matters): reproduced the failure on a bare venv, confirmed the shim resolves through
  `setuptools/_distutils`, then read → mutated → wrote → re-read a real game NIF successfully. The
  separate `time.clock` monkey-patch is unrelated (removed in 3.8) and still required.

  Worth knowing *why* this became common: **since Python 3.12, `venv` no longer installs setuptools
  by default** — a fresh 3.12 venv ships pip only. The breakage is a newly-missing dependency, not a
  newly-broken library.

## v3.5.3 — 2026-07-31

### Changed

- **Promoted the "don't hand-install a mod into `Data/`" rule from `KNOWLEDGEBASE.md` into
  `CLAUDE.md`'s Safety Rules.** v3.5.2 removed the bad instruction from the setup prompt and wrote up
  the reasoning in the knowledgebase — but the knowledgebase is *consulted*, while `CLAUDE.md` is
  *always loaded*. A rule whose whole job is to stop an action at the moment you're about to take it
  is useless in a file you have to remember to open. The KB entry stays as the long-form explanation;
  the Safety Rules now carry the rule itself.
- Scoped precisely: it covers installing **someone else's** packaged mod (SKSE plugin DLLs included).
  Writing your own in-development mod's files into `Data/` is normal work and explicitly unaffected.

## v3.5.2 — 2026-07-31

### Changed

- **The setup prompt no longer offers to install DevBench.** It listed DevBench alongside the
  `tools/` utilities and instructed Claude to "install it to `Data/SKSE/Plugins/devbench.dll`" — but
  DevBench is the one optional item that isn't a dev tool. Everything else installs under `tools/`
  and never touches the game; DevBench is an **SKSE plugin**, i.e. a mod. Hand-copying a DLL into
  `Data/` bypasses Vortex/MO2, leaves the file untracked, and on a managed install a later deploy or
  purge can clobber it — the opposite of what the rest of this toolkit's safety design stands for.
  The prompt now *tells* the user DevBench exists and what it unlocks, and says explicitly that it's
  a mod they install through their own mod manager, and that Claude must not copy files into `Data/`
  itself. The bundled `tools/devbench-cli.sh` wrapper works the moment DevBench is present.
- The same correction applied to `README.md`, `CLAUDE.md`, and `setup.sh`'s closing tool summary,
  which all carried the "download it into `Data/SKSE/Plugins/devbench.dll`" phrasing.
- All three copies of the setup prompt (`SETUP_PROMPT.txt`, `README.md`, `docs/getting-started.md`)
  remain byte-identical, now verified programmatically rather than by eye.

### Docs

- `KNOWLEDGEBASE.md`: new "Never hand-install a mod into `Data/` on a managed install" note under Mod
  Manager Layout — why a dev tool and a mod aren't the same thing, and what hand-placing a file
  actually breaks under Vortex vs MO2.

## v3.5.1 — 2026-07-31

Tracks DevBench **1.12.0** upstream. The liveness check the toolkit shipped in v3.3 was the best
available at the time; DevBench has since added a purpose-built endpoint that does the job properly,
and the old approach has a hole worth naming.

### Fixed

- **`devbench-cli.sh alive` could not detect the thing it existed to detect.** It diffed the `frame`
  counter across two `inspect {kind:state}` calls — but *every* DevBench tool call is dispatched onto
  the game's **main thread** and throws 504 after 5 s if that thread is stuck. So on a genuine hang
  the probe itself hung: you got a timeout indistinguishable from a closed game, precisely when you
  needed a diagnosis. It now uses **`GET /api/health`** (DevBench 1.11.0+, answered *off* the main
  thread since 1.12.0), the one endpoint that keeps replying through a stall.
- **A frozen frame was reported as "paused or hung" — one verdict for two very different problems.**
  `alive` now separates four states using `pendingTasks`/`lastTaskFrame` as the discriminator:
  running (0) · paused/console-open/loading, queue draining normally (2) · genuinely **hung**, tasks
  queued but not completing (2) · server up but **no save loaded**, `frame < 0` (3). A frozen frame
  alone is not evidence of a hang; the old check cried wolf every time you opened a menu.
- **HTTP status was ignored — any JSON body counted as success.** A `504` (main thread busy) or a
  `400` (bad argument type; 1.11.0 reclassified these from 500) was printed as if the call had
  worked. Each now reports what actually went wrong and returns a distinct exit code, so a busy game,
  a malformed call, and a closed game stop looking identical.
- `jget`'s jq path used `// empty`, which swallowed a legitimate `false` — `vr` on an SE install read
  as missing rather than false.

### New

- **`devbench-cli.sh health`** — the raw off-thread probe: `{ ok, lastLifecycle, frame,
  lastTaskFrame, pendingTasks, pid, port, exe, vr }`.
- **Instance identity in `alive` output** (`pid`/`exe`/`vr`/`port`). If you have both SE and VR open,
  a client pinned to the wrong port returns real-looking results from the wrong game; this surfaces
  the misattach in one call. MCP clients get the same signal via `inspect kind=health`.

### Compatibility

- **Older DevBench still works.** On a build without `/api/health` (pre-1.11.0) the wrapper detects
  the 404, falls back to the legacy frame diff, and says so on stderr rather than failing.
- Every path above was exercised against a mock server reproducing each scenario — running, paused,
  hung, not-in-game, legacy-404, 504, 400, and a dead port — with and without `jq` on PATH.

### Docs

- `KNOWLEDGEBASE.md`'s "liveness ≠ ping" entry rewrote its now-outdated advice (the two-read frame
  diff) into the four-state table, plus the status-code semantics.
- The `game save` deadlock entry notes that `health` — not `inspect` — is how you watch for it.
- **`README.md`'s copy of the setup prompt was stale** — it never mentioned DevBench, so anyone who
  pasted the prompt from the README (the most visible copy, and the one the Nexus page points at)
  was never offered DevBench during setup. It is now byte-identical to the canonical
  `SETUP_PROMPT.txt`, which `docs/getting-started.md` already matched.

## v3.5 — 2026-07-31

### New Capabilities

- **Optional devcontainer** for the tools that don't need Windows or an active MO2 session:
  Spriggit ESP inspection/diffing, FOMOD/JSON generation, unit-testing mod logic, ReSaver CLI.
  Credit: this originated in [@aaronputty](https://github.com/aaronputty)'s fork of this toolkit
  ([putty-skyrim-claude-toolkit](https://github.com/aaronputty/putty-skyrim-claude-toolkit)), who
  gave the go-ahead to bring it upstream after they weren't able to get back to their own 9-commits-
  ahead branch. Not copied verbatim — rebuilt and re-verified for this toolkit's shape, credited
  throughout.

  - `.devcontainer/Dockerfile`: Python 3.11 + Node 20 + .NET 9 + JDK 17 (ReSaver's floor), on Debian
    **bookworm** rather than the source fork's bullseye (bullseye's LTS window ends 2026-08-31;
    bookworm also means JDK installs as a plain `apt-get` instead of a manual fetch). Build and every
    toolchain component verified inside a real container: Python 3.11.15, Node 20.20.2, JDK 17.0.20,
    .NET SDK 9.0.316, and `dotnet tool restore` successfully restoring `spriggit.cli`.
  - `devshell-docker.sh` / `devshell.sh`: build-and-shell wrappers (Docker-only, or via the
    `@devcontainers/cli`). `devshell-docker.sh` reads its mount sources straight out of
    `.devcontainer/devcontainer.json` rather than hardcoding them, so it can't drift from what
    `setup.sh` resolved.
  - **`setup.sh` now also fills in `.devcontainer/devcontainer.json`'s mount paths** — from the
    detected MO2 instance's mods/profile/overwrite folders on an MO2 install, or from `Data/` and the
    INI config folder on stock/Vortex. Verified end-to-end: the real `devshell-docker.sh` (not a
    manual reconstruction) building the image, mounting a real game `Data/` folder read-only,
    restoring `dotnet` tools, and — checked directly via `docker inspect` and a live write
    attempt — enforcing that read-only mount (`touch` on the mounted path fails with "Read-only file
    system").
  - **`examples/inspect-esp.py`** verified against a real third-party mod plugin with actual records
    (correctly listed its MagicEffects/Quests/Spells groups). The source fork's version imported a
    Python `esplugin` package that doesn't exist on PyPI (esplugin is a Rust crate) and would have
    failed on line one — rewritten to use only the Spriggit path, which is what actually works, and
    corrected to this toolkit's own Spriggit convention (`Spriggit.Yaml` + a required
    `--PackageVersion`, not `Spriggit.Yaml.Skyrim` with no version).
  - **One confirmed limitation, found while verifying:** a plugin using localized strings (the
    `Localized` flag — common in vanilla ESMs) fails to serialize via Spriggit inside the container
    with a Mutagen exception, because its BSA/load-order resolution has no default path on Linux.
    Documented in `docs/container-vs-windows.md` rather than silently shipped as if it worked
    universally.
  - Toolchain pins: `package.json`, `requirements.txt` (container-side Python deps),
    `requirements-windows.txt` (Windows-only, e.g. `pywin32` — doesn't build on Linux),
    `.config/dotnet-tools.json` (Spriggit CLI), `.node-version`, `.python-version`.
  - **A real bug caught and fixed during verification, not shipped:** `devshell-docker.sh`'s `jq`
    calls and its `docker run` mount targets all reference bare `/skyrim/...`-style paths — under Git
    Bash on Windows, any such bare-slash argument gets silently rewritten to a Windows path
    (`/skyrim/mods` → `C:/Program Files/Git/skyrim/mods`) before reaching `jq` or `docker.exe`. Fixed
    with `MSYS_NO_PATHCONV=1` and resolving `SCRIPT_DIR` via `pwd -W`. Also fixed: `jq` can't parse
    the JSONC `//` comments that VS Code and the devcontainer CLI both allow in `devcontainer.json` —
    `devshell-docker.sh` now strips whole-line comments before parsing.

- **`docs/container-vs-windows.md`** — the tool-routing decision table (container vs. Windows vs.
  MO2's executables list), adapted from the source fork and cross-referenced with this toolkit's own
  MO2 documentation.

- **`docs/skse-cross-compile.md`** — the source fork's SKSE-plugin cross-compilation recipe
  (LLVM/xwin/xmake), documented as an opt-in addendum rather than built into the default image. It
  roughly doubles the container (LLVM 17 + a ~700MB Windows SDK/CRT splat) for a capability the
  source author themselves called unvalidated beyond one experiment (a pre-pivot build of the SKSE
  plugin Mora) — kept out of the default so that cost doesn't land on every user's container build.

---

## v3.4 — 2026-07-27

### New Capabilities

- **Mod Organizer 2 support.** Previous versions assumed the Vortex/stock layout, where mods deploy
  into the game's `Data/`. **MO2 has no real merged `Data/` folder** — it builds a virtual one at
  launch by overlaying the stock game, each enabled mod's own folder, and `overwrite/`. So on an MO2
  setup the toolkit was pointing Claude at a real-but-nearly-empty `Data/`, and at Documents and
  `%LOCALAPPDATA%` for INIs and load order that actually live in the MO2 profile.

  `setup.sh` now detects MO2 (global instances under `%LOCALAPPDATA%/ModOrganizer/<name>/`, or a
  portable instance via `MO2_INSTANCE_INI`), matches an instance to this game folder by its
  `gamePath`, resolves `selected_profile` and the real mods / overwrite / profiles directories
  (including `base_directory` overrides and the `%BASE_DIR%` token), and writes those paths into
  `CLAUDE.md` — moving the INI and load-order paths to the profile when the files are genuinely
  there. Non-MO2 installs are unaffected and get a short stock-layout note instead.

- **The MO2 silent-wrong-answer trap is now documented** in `KNOWLEDGEBASE.md` and injected into
  CLAUDE.md for MO2 users: xelib/XEditLib resolves plugins from the game path, so launched *outside*
  MO2 it sees only the plugins physically in the stock `Data/`. It doesn't error — it returns a wrong
  but plausible answer for anything involving the override chain or full load order. Run those through
  MO2's executables list; single-plugin work (Spriggit by path) is fine outside MO2 because it never
  consults the load order.

- **`AGENTS.md`** — the toolkit now ships the cross-agent convention file, so agents that look for it
  find their way in. It points at `CLAUDE.md` rather than duplicating it (so they can't drift), and is
  explicit about what is portable (the knowledgebase and every tool — plain bash/Node/Python) versus
  what is Claude Code specific (the safety hooks and skills), with concrete compensating practices for
  agents that don't get the guardrails.

### Knowledgebase

Six additions, kept deliberately to things any Skyrim modder hits regardless of what they're building:

- **Mod manager layout** — the MO2 virtual filesystem, where each thing really lives, and the
  load-order tooling trap.
- **A recompiled `.pex` only loads at game startup** — a mid-session save/load never re-reads it, not
  even from a save that has never seen your mod. The only reliable refresh is a full restart onto a
  *pre-activation* save. Includes the design implication: make anything you intend to tune a runtime
  parameter, so iterating never touches the code.
- **xelib `setFormID` master-count high-byte trap** — a bare local FormID sets the high byte to `0x00`,
  which the engine reads as an override of a `Skyrim.esm` record. Silently corrupt, and it half-works
  often enough to be expensive to find.
- **Reused vanilla records can carry gating Conditions** — the usual cause of a vanilla effect that
  fires on some targets but not others.
- **CrashLogger writes `.LOG`, not `.txt`** — why your crash logs appear to be missing.
- **AutoMod BSA extract/repack needs `bsarch.exe`**, and it lives under `bin/`, so any rebuild wipes it.
- **Explosion knockback needs a Knock Down flag** — `DATA\Force` moves nobody without it, no matter how
  high you raise it.

### Docs

- `docs/getting-started.md` gains a "What's in `tools/`" reference table naming every bundled script.

---

## v3.3 — 2026-07-27

### New Capabilities

- **DevBench — the live in-game test channel.** The toolkit's tools all shortened the time to *make*
  a change; this one attacks the loop that actually costs you evenings — change, launch, trigger,
  "still broken", guess again. With [DevBench](https://www.nexusmods.com/skyrimspecialedition/mods/181326)
  (alandtse) installed, Claude drives the **running** game itself: reads live state (Papyrus VM health,
  active effects, inventory, quests, the loaded ref grid), runs console commands **and reads their
  output**, calls Papyrus functions **and gets the return value back**, narrates tests on your HUD while
  you're in the headset, dismisses modals, and runs scripted scenarios with real event waits instead of
  guessed sleeps. Tuning a value stops being an edit→recompile→reload→ask-you-to-try cycle and becomes
  another call into the live game.

  **DevBench is NOT bundled** (GPL-3.0-or-later) — install it from Nexus mod 181326 into
  `Data/SKSE/Plugins/devbench.dll`. It is dev-only: no gameplay change, no save data. The toolkit ships
  the wrapper and the knowledge:
  - **`tools/devbench-cli.sh`** — resolves the port automatically (reads DevBench's `runtime.json`,
    else the per-runtime default: VR `8921`, SE/AE `8920`; override with `DEVBENCH_PORT`), and wraps
    the common operations: `ping`, `alive`, `state`, `inspect <kind>`, `exec "<console cmd>"` (handles
    the two-step capture/read fence), `call <Script> <Function> [args] [self]`, `describe`, `notify`
    (HUD narration), plus a raw `tool` escape hatch for any tool and any JSON. Fails fast with a clear
    message when the game isn't running.
  - **`alive`** encodes the single most important hazard: DevBench's HTTP server runs on a **separate
    thread from the game**, so a hung or deadlocked game still answers `ping`. Real liveness is whether
    the `frame` counter advances between two reads — `alive` checks exactly that and exits 2 on a stuck
    frame.
  - **A new KNOWLEDGEBASE section** covering the hazards learned the hard way on a 700+ plugin VR load
    order: the `game save` deadlock, what does and doesn't work while the game is paused (reads yes,
    writes no, shader probes give false negatives), why heavy console commands like `smp reset` can CTD
    a big load order, why you spawn test actors instead of poking the player's live state, the Papyrus
    `call` gotchas (omitted trailing optionals are padded to neutral defaults, silently no-opping
    `MoveTo`/`Disable`/`Kill`), and the protocol for tests a VR user must physically observe.

### Docs

- README, CLAUDE.md, `setup.sh`, `SETUP_PROMPT.txt`, and `docs/getting-started.md` all cover DevBench
  as an optional tool, and it's credited to alandtse.

---

## v3.2.1 — 2026-07-26

Hotfix release. `setup.sh` only — no tool or knowledgebase changes. If you installed v3.1 or v3.2,
re-run `bash setup.sh` against a fresh copy of `CLAUDE.md` (or fix the two Key Paths lines by hand);
the paths it wrote for you were wrong.

### Fixes
- **The Load Order path written into CLAUDE.md was corrupted on every Windows install.**
  `$LOCALAPPDATA` is backslash-delimited, and it was fed straight into `sed`'s replacement text,
  where GNU sed treats `\U`, `\a` etc. as escapes — `C:\Users\You\AppData\Local` came out as
  `C:SERSYOUAPPDATAocal`. Both `$LOCALAPPDATA` and the Documents path are now normalized to forward
  slashes before substitution. Affects v3.1 and v3.2. (Fix by @awesmdiver.)
- **SE installs with a redirected Documents folder were detected as VR.** `DOCUMENTS_DIR` was
  hardcoded to `C:/Users/<you>/Documents`, so a Documents folder moved by OneDrive "Back up your
  folders", a manual Properties → Location move, or a GPO redirect matched neither `My Games`
  probe and fell through to the `Skyrim VR` default. Now resolved via
  `[Environment]::GetFolderPath('MyDocuments')`, with the old hardcoded path as fallback.
  (Fix by @awesmdiver.)
- **The game root written into CLAUDE.md was an MSYS path, not a Windows path.** `pwd` under Git
  Bash returns `/c/Games/Skyrim` — a form Claude's file tools and PowerShell can't open. CLAUDE.md
  now gets the `C:/Games/Skyrim` form (`pwd -W`); the script's own filesystem work is unchanged.
- **SE/VR detection ignored the one unambiguous signal.** A fresh install that had never been
  launched has no `My Games/<variant>/` folder yet, so detection fell through to `Skyrim VR` even
  when only `SkyrimSE.exe` was present. The game folder's `.exe` is now the primary signal, with
  the config-folder probe as the tiebreaker.
- Removed a dead `{{USERNAME}}` substitution — no such placeholder exists in the CLAUDE.md template.

---

## v3.2 — 2026-07-09

### Fixes
- **xelib scripts couldn't find the wrapper on a fresh install.** `tools/xelib/loader_diag.js`,
  `tools/xelib/active-plugins.js`, and `tools/resaver-resolve-names.js` used `require('./xelib')`,
  which resolves to a local file that only exists in a dev layout — on a clean install they threw
  `MODULE_NOT_FOUND`. All now `require('xeditlib')` (the real package name). Install xeditlib **from
  the toolkit root** (`npm install github:WingedGuardian/xeditlib`) so Node's upward module lookup
  finds it from `tools/` and `examples/` alike; the bundled `XEditLib.dll` + `*.Hardcoded.dat` load
  relative to the package, so the scripts are cwd-independent. Docs/setup updated to say so.
  (Thanks to @awesmdiver for reporting the broken require paths.)

### New Capabilities
- **ReSaver CLI — changeform-level diagnostics.** New read ops `recon` (sync-aware parse-coverage
  scan of all changeform body types), `changeform` (parse one changeform body), `extradata-scan`,
  `changeform-diff`, `globaldata`/`globaldata-diff`, `freeze-report`; new verify-gated write ops
  `reset-havok`, `cleanse-formlists`, `remove-created`, plus a `verify-roundtrip` self-test. Every
  `--apply` is verify-gated (the output is re-read and compared to the written model; on any
  unintended divergence the file is deleted and the op fails). Read/diagnostic ops layer a small
  **analysis overlay** (modified ReSaver source, Apache-2.0 — see
  `tools/resaver-cli/analysis-overlay/NOTICE.md`) in front of your jar for extra parse coverage;
  write ops always run the STOCK jar; if the overlay can't compile against your ReSaver version the
  wrapper falls back to stock parsing automatically. JVM flags are now JDK-version-gated so the tool
  starts on JDK 17–22 (not just 23+).
- **cosave-info** (`tools/cosave-cli.sh` + `tools/cosave-info.py`) — read-only structural survey of
  an SKSE `.skse` co-save → JSON: which mods stashed co-save data (StorageUtil/PapyrusUtil/
  JContainers/per-mod blobs) and how much — the mod-state landscape the `.ess` itself never exposes.

---

## v3.1 — 2026-06-27

### New Capabilities
- **ReSaver CLI** (`tools/resaver-cli.sh`) — headless `.ess` save parsing, querying, cross-referencing,
  and cleaning, driving ReSaver's (FallrimTools) Java library. Ops: `info` / `dump` / `find` /
  `find-refs` / `worries` / `set-global` / `set-var` / `clean`. Writes are dry-run unless `--apply` and
  always go to a NEW file (never overwriting the input); FormID→EditorID resolution via
  `tools/resaver-resolve-names.js`. Supersedes raw binary byte-scanning for structured save work.

### Reliability Fixes
- **AutoMod** — `tools/automod-cli.sh` now invokes the **prebuilt `spookys-automod.dll`** instead of
  `dotnet run`, eliminating the per-call recompile / MSB1025 failures.
- **Spriggit** — `tools/spriggit-cli.sh` runs deep/nested output paths in a shallow workspace,
  fixing the `UnauthorizedAccessException` on deeply-nested paths (preserves the exact basename = ModKey).
- **xelib** — `tools/xelib/active-plugins.js` `loadActive()` handles the case where the SSE `plugins.txt`
  the GM_SSE loader expects is absent on a VR install (which otherwise fails silently).

### Setup Instructions Overhaul
- Every optional tool now has explicit acquisition/build instructions in CLAUDE.md, setup.sh,
  SETUP_PROMPT.txt, and README.md — including the AutoMod clone + Cli-project build (fixes Claude
  treating the AutoMod CLI as "fictional" when it wasn't already present).

---

## v3.0 — 2026-06-23

### New Capabilities
- **Author animated NIFs from scratch (PyNifly)** — self-spinning meshes (a `SpecialIdle`
  NiControllerSequence that auto-loops on a placed Activator with zero scripting), telescoping/
  extending geometry, and transform-keyframed effects. PyNifly writes the controller blocks correctly
  (hand-rolled PyFFI authoring CTDs the engine). It also reads/writes SSE **BSTriShape** meshes, which
  PyFFI cannot.
- **Headless render-verify loop** — `tools/blender-nif-validate.py` (independent PyNifly parse gate) +
  `tools/blender-nif-render.py` (render a NIF to PNG) confirm a mesh/VFX fix in chat before a game
  launch. NifSkope serves as the independent visual gate. "Author → validate → render-proof."
- **NIF geometry surgery** — `tools/pyffi-geometry-split.py` (split one shape into two for independent
  shaders / partial-mesh glow), plus the glow-map / mesh-split / stretch techniques documented in the
  knowledgebase.
- **AutoMod CLI** (`tools/automod-cli.sh`) — NIF / BSA / audio / MCM / ESP modules surfaced as a
  first-class tool.
- **ESP cross-reference integrity guard** — `tools/esp-verify-wrapper.sh` snapshots and diffs every
  record's cross-references (FormID + target master) to catch silent re-mastering / dropped-reference
  corruption from bulk remaps.
- **Snapshot-before-edit hook** — `.claude/hooks/snapshot-before-tool.sh` auto-snapshots active
  `.psc`/`.pex` files before every Bash command (external tools bypass the Edit/Write backup hook),
  with rate limiting and auto-pruning.

### Knowledgebase
- Grown and **fully scrubbed** to ~1,381 lines of generalizable, project-agnostic knowledge.
- New engine sections: Havok game units (≈70:1), the VR melee hit-detection stack + engine melee-range
  cap, spawned-actor Havok CTD (`Is3DLoaded()` guard), no-Papyrus-raycast limit, immobilizing the
  player/NPCs in VR (SetDontMove vs DisablePlayerControls vs EnableAI, with aggro/VRIK interactions),
  the NIF validation/render trichotomy, PyFFI limits & PyNifly authoring, the Music System (MUSC vs
  MUST, ducking-bypass, FNAM flags), SOUN-vs-SNDR wiring, the WAV→XWM pipeline, the Papyrus VM
  page-policy CTD on heavy modlists, and more.

### CLAUDE.md
- New principle sections: Vanilla Game as Frame of Reference, Native Engine Solutions First, Do Your
  Homework (due diligence), and Cognitive Co-Pilot (anticipate, don't just comply).
- New tool docs: PyFFI, PyNifly, AutoMod CLI, the NIF validation/render trichotomy, and the
  esp-verify integrity guard — all version-agnostic.

---

## v2.0

### New Capabilities
- **ESP editing via Spriggit** — Serialize any ESP to human-readable YAML, edit directly, deserialize back. Now the primary recommended workflow for record editing.
- **AutoMod CLI integration** — NIF mesh inspection and editing, BSA archive CRUD, audio file processing (FUZ/XWM/WAV), and MCM menu generation via SpookyPirate's AutoMod Toolkit.
- **Save file analysis** — New `scripts/read-save.py` + `skyrim-save` skill. Decompress .ess saves, extract the full plugin list, search for orphaned scripts, detect effect accumulation, check mod footprint, and monitor save bloat over time.
- **8 Claude Code skills** — Auto-loading slash commands: `/inspect-esp`, `/port-to-vr`, `/create-mod`. Auto-context for NIFs, BSAs, audio files, save files, and general Skyrim modding context.

### Changes
- Version-agnostic: fully supports SE, AE, VR, and LE. Not VR-exclusive despite VR origins.
- Framing updated to reflect actual strengths: power user tool for porting, debugging, and editing — complex mods from scratch require iteration.
- Setup prompt updated to include AutoMod CLI as an optional install.
- Knowledgebase expanded with save file format documentation.
- README reordered: porting and debugging examples now lead; new-mod-from-scratch examples follow with honest caveats.

---

## v1.4

- Added `scripts/read-save.py` (LZ4 decompression, plugin list parsing, binary search)
- Added `skyrim-save` skill
- Save File Analysis section added to knowledgebase

## v1.3

- SpookyPirate AutoMod CLI integrated (NIF, BSA, audio, MCM modules)
- AutoMod CLI safety hooks added to `protect-bash.sh`
- `automod-cli.sh` wrapper script added

## v1.2

- Spriggit added as primary ESP editing workflow
- `inspect-esp`, `port-to-vr`, `create-mod` skills added
- `skyrim-nif`, `skyrim-bsa`, `skyrim-audio`, `skyrim-mcm` skills added
- CLAUDE.md template generalized with `{{GAME_ROOT}}` / `{{USERNAME}}` placeholders

## v1.1

- Knowledgebase generalized from VR-specific to version-agnostic (SE/AE/VR/LE)
- VR-specific content moved to labeled subsections
- setup.sh detects both `Skyrim VR` and `Skyrim Special Edition` document paths

## v1.0

- Initial release
- xeditlib integration (Delphi FFI fixes open-sourced on GitHub)
- Safety hooks: command guard, file guard, auto-backup with audit log
- Confidence system and investigation-first workflow
- 600+ line Skyrim knowledgebase
- `skyrim-context` skill (auto-loads for .psc, .pex, Data/, .ini files)
