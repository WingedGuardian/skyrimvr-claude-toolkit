# Changelog

## v4.0.0 — 2026-10-08

### Added

- **`mod-release-review` skill:** a review before publishing a Skyrim mod, scoped to what changed since the last published version; it changes nothing while reviewing and brings every finding to the author before any fix.
- **In-place sed guard (S-SED-CRLF):** `sed -i` without `-b` on a file in the install, config or MO2 folder (or a game INI or load-order file by name) is refused, because Git for Windows' sed rewrites every CRLF line ending to LF; use `sed -i -b`. It fires only when sed is the program that runs, not on text that mentions it.
- **Skyrim AI Assistant Toolkit:** for coding agents (Claude Code, Codex, …), not an AI NPC mod (SkyrimNet, Mantella, CHIM).
- **Codex CLI support on Windows:** generated instructions, skills, hook definitions and an entry script.
- **Codex hook approval:** run `/hooks` after setup.
- **Codex start folder:** start Codex in the game folder itself.
- **Agent selection:** `setup.sh --agent claude|codex|all|auto`.
- **Default agent selection:** `auto` is the default.
- **Agent alias:** `both` aliases `all`.
- **Setup diagnostics:** report agents that remain NOT CONFIGURED.
- **Approval reminders:** setup lists the hook approval steps still needed.
- **Shared guard engine:** Claude and Codex wrappers use the Python rules under `tools/skyguard/`.
- **Optional Windows file lock:** Layer 2 locks existing vanilla masters and archives; it is off by default.
- **Lock at setup:** `setup.sh --lock` opts into Layer 2.
- **Session-bound lock lifts:** `skyguard lock unlock <file>` records a lift that lasts one hour by default.
- **Lock status:** `lock status` reports the lock state.
- **Lock disable:** `lock disable` turns the lock off.
- **Lock enable:** `lock enable` turns the lock back on.
- **Emergency unlock:** `tools/skyguard-unlock.cmd` works from the user's terminal without Python.
- **Shadow mode:** `guards off` allows eligible refusals and asks with a warning and audit record.
- **Guard self-protection:** recognized writes and deletes targeting `.skyguard/` are refused.
- **Self-protection for printed deletes:** delete text that an interpreter prints into a shell or interpreter (a pipe, a substitution or a script the same command runs) is refused, including under `guards off`.
- **Self-protection for Recycle Bin deletes through a variable:** a Recycle-Bin delete of guard state is refused when its path comes from a variable, an environment variable, a recognized format token (`{p}`, `{}`, `%s`, `%(p)s`, `%.80s`, `${p}`) or an empty literal joined to it, including under `guards off`.
- **Self-protection and parser inspection:** a Python command that only prints or parses delete examples, with output that nothing in the command runs, is not refused as a guard-state delete when every other part of the command is a recognized text tool or shell structure (`if`, loops, `case`, `{ }`, assignments).
- **Self-protection and outside examples:** quoted Recycle Bin examples outside the install are not read as guard-state operands.
- **Guard integrity banner:** session start reports guard files that differ from `GUARD-FILES.sha256`.
- **Layer diagnostics:** `bash tools/skydoctor` reports live, not live or unknown for each protection layer.
- **Backup manifest:** backups record the original path, size and SHA-256 in `.claude/backups/MANIFEST.jsonl`.
- **Manifest restore:** `scripts/restore-from-backup.sh` checks the recorded hash before restoring.
- **Nested script snapshots:** content-addressed snapshots cover `.psc` and `.pex` files under both script source layouts.
- **Recovery snapshots:** recognized shell changes trigger snapshots of load-order and Skyrim INI files.
- **ReSaver save snapshots:** recognized ReSaver writes snapshot the named save and co-save.
- **DevBench save snapshots:** recognized DevBench saves snapshot the named save and co-save.
- **Snapshot list:** `snapshot list` shows the recorded snapshots.
- **Snapshot restore:** `snapshot restore <id>` restores into a new folder by default.
- **In-place restore:** `--in-place` is limited and requires confirmation.
- **3.x migration:** `tools/migrate-3x.py` carries user edits into local notes and a settings backup.
- **Pre-update snapshot:** `tools/pre-update-snapshot.sh` saves files an extract would replace before the update.
- **Adapter guide:** `ADAPTING.md` explains integration for other coding agents.
- **Adapter templates:** under `tools/adapting/`.
- **Adapter capability reports:** under `tools/adapting/`.
- **Adapter worked examples:** under `tools/adapting/`.
- **Adapter conformance:** fault injection checks that an adapter refuses when the engine cannot decide.
- **Neutral entry:** `run.py neutral pre-tool` answers allow, ask or deny for an agent-neutral request.
- **Runtime inventory:** `toolkit-setup` checks Python, Git and Node by launching them.
- **Universal setup prompt:** host-software installs require an explicit exact-id and exact-version authorization.
- **Preference injection:** session start reads at most 1 MiB of local notes and injects at most 4,096 bytes of the preferences section.
- **DevBench skill:** DevBench guidance moves out of the core instructions.
- **Conflict triage skill:** conflict triage guidance moves out of the core instructions.
- **xelib skill:** xelib guidance moves out of the core instructions.
- **Hook stage telemetry:** the canary reports invocation stages.
- **Hook latency telemetry:** the canary reports measured call latency.
- **Interpreter fallback:** configured hooks can use an available fallback Python and report DEGRADED.
- **Archive-write advice:** recognized archive creation into the install gets its own rule message.
- **DevBench protection:** recognized `smp reset` calls are refused.
- **Third-party tool audit:** checks pinned tools.
- **Installation instruction audit:** checks installation instructions.
- **Pinned downloads table:** records version, file, SHA-256 and source for optional downloads.
- **Optional Python dependencies:** tool requirements have a separate hash lock.
- **Payload fixtures:** real hook shapes were rechecked on Claude Code 2.1.291 and Codex CLI 0.160.0; no shape change was found.
- **Generated agent files:** contributors edit `agent/` and regenerate.
- **Generated file sync check:** CI checks that generated agent files are in sync.
- **Committed release builds:** the archive builder reads git objects.
- **Clean release trees:** the archive builder refuses dirty trees.
- **Build record:** the archive builder records BUILD-INFO.
- **Payload verification:** one checker validates archive shape, hooks and adapter files.
- **Scrubbed verification:** the local verifier checks the archive and generated install for identity and credential leaks.
- **Maintainer secret:** release CI requires `LEAK_TOKENS` and refuses to publish without a valid value.
- **CI matrix:** Windows and Linux Python 3.10–3.13 legs.
- **CI tripwires:** the legs include collection and skip tripwires.
- **Setup smoke matrix:** CI exercises the agent-selection values.
- **Merge-marker gate:** contributor tests reject committed conflict markers.

### Changed

- **Node 24 LTS:** xelib tooling requires Node 24; the tested pin is 24.19.0.
- **.NET 10 SDK:** Spriggit and AutoMod use .NET 10; the tested SDK pin is 10.0.401.
- **Spriggit 0.41.0:** replaces the 0.40.0 pin.
- **Serializer version:** `--PackageVersion` follows the installed `spriggit --version`.
- **AutoMod rebuild:** `bash tools/automod-cli.sh --rebuild` moves a clean clone to the pinned commit and builds the CLI for net10.0.
- **AutoMod local edits:** the wrapper leaves a modified clone alone and gives instructions.
- **AutoMod old builds:** the wrapper refuses an old net8.0 build.
- **bsarch location:** an AutoMod-local executable belongs under `bin/Release/net10.0/tools/bsarch/`.
- **PyNifly 27.2.0:** both the library and Blender add-on use this pin.
- **Exact Node dependencies:** package versions replace range pins.
- **Pinned xeditlib:** installs use a fixed commit.
- **Pinned koffi:** xeditlib installs use koffi 2.15.1.
- **No install scripts:** xeditlib installs use `--ignore-scripts`.
- **Container runtimes:** the image and runtime downloads are pinned.
- **Container JDK:** the JDK moves to 21.
- **Tested Python range:** 3.10–3.13.
- **Python 3.8/3.9 syntax:** guard syntax remains compatible with 3.8/3.9, which are untested.
- **jq optional:** the guards no longer need jq.
- **Root paths file:** `skyrim-paths.env` moves to the toolkit root.
- **Legacy protection union:** the 3.x `.claude/skyrim-paths.env` remains readable and its protection folders are combined with the root file.
- **Legacy retirement:** setup retires the old paths file only when it contributes no additional protection folder.
- **Private paths:** the root paths file is ignored and never shipped.
- **Empty explicit root:** `run.py --root ""` reads no paths file from the current directory.
- **Install hints:** setup prints exact commands for the user; it does not silently install host runtimes.
- **Instruction budget:** setup warns when expanded instructions exceed Codex's 32,768-byte limit.
- **Idempotent setup:** agent selection repairs partially configured files.
- **All guard interpreters:** setup renders Python paths in guard files regardless of the selected agent.
- **Migration before rendering:** setup reports a migration failure and continues configuration.
- **Smaller core instructions:** detailed tool guidance moves into skills and documentation.
- **Bounded engine runtime:** the default engine timeout is 20 seconds and expiry produces a GUARD INERT refusal.
- **Hook timeout handling:** wrappers terminate their own timed-out child instead of relying on a late host refusal.
- **Claude shell matcher:** covers Bash, PowerShell and Monitor.
- **Claude edit matcher:** covers Edit, Write and NotebookEdit.
- **DevBench matching:** guard parsing recognizes server names containing devbench.
- **DevBench scenarios:** recognized scenario steps are judged like direct calls.
- **GNU find on Codex:** recognized delete forms are judged across reported shell dialects.
- **Shadow ask wording:** explicitly says the call ran without asking.
- **Codex script fields:** script fields in command payloads are judged.
- **Codex argv lists:** argv lists in command payloads are judged.
- **Ambiguous Codex commands:** ambiguous command fields refuse.
- **Relative delete operands:** relative paths use the reported working directory.
- **MO2 delete protection:** protection includes MO2 mods and overwrite.
- **Read-only mentions:** searches for delete syntax are distinguished from commands that delete.
- **Wrapper dependency health:** toolchain checks report dependency failures.
- **Spriggit version drift:** toolchain checks report Spriggit version drift.
- **AutoMod provenance:** toolchain checks show the build commit.
- **Knowledgebase snapshots:** include AGENTS.md.
- **Lift expiry:** the next hooked tool call after expiry relocks the affected files.
- **Archive identity:** the root is `skyrim-ai-assistant-toolkit-<version>`, using the package name as the shared slug.
- **GitHub links:** retain the current repository path; no remote rename is part of this change.
- **Shipped identity scrub:** examples and dated reports use neutral paths.
- **Corpus fixture bytes:** non-shipped corpus fixtures preserve historical input bytes.
- **Freeze baseline:** a tag on HEAD cannot serve as its own comparison baseline.

### Fixed

- **Adapted agents in the health reports:** `hook-canary` and `skydoctor` now report each agent adapted through ADAPTING.md (one row per `tools/adapters/<name>/adapter.py`, named by its `AGENT_NAME` slot) from the heartbeats its adapter and the engine write; they knew only Claude Code and Codex.
- **Hook health under simultaneous calls:** two calls of one hook at the same moment can take the same invocation number (measured: 5 of 12 calls fired four at a time); `hook-canary` and `skydoctor` now order such calls by their timestamps, so a completion after a failure clears it at once instead of one call later. A call still never clears its own failure.
- **Shadow-mode audit record:** a call `guards off` lets through is logged as one line, `skyguard SHADOW: <code> (deny) not enforced: ...` with the rule's message folded in; it was logged as the rule's DENY or ASK line followed by a separate SHADOW line, so the first line claimed a refusal for a call that ran. A refusal that stands still writes its rule line as before.
- **Guards-off reason:** `guards off --reason "why"` records the reason `skydoctor` and the session-start banner show (one line, at most 120 characters, still shown as untrusted data); without it the reason stays "guards off". A reason could not be given before.
- **Write targets held in a variable:** a write, copy, move or tee whose target comes from a literal assigned earlier in the same command (`P=Data/X.esp; echo x > "$P"`, `$p = '...'; Set-Content $p`) gets the same verdict as the literal form, for plugins, saves, INIs, load order, SKSE INIs, install files, guard state, Papyrus output and git discards. These were allowed.
- **Name-filtered deletes started above a protected folder:** `find <folder above the install> -name X -delete` (also `-iname`, `-exec rm`, `-execdir`, `-exec mv`), `fd ... -x/-X rm` and PowerShell `Get-ChildItem ... | ForEach-Object { Remove-Item $_ }` are judged as reaching the whole subtree. They were allowed; a `-maxdepth` that provably cannot reach the folder still is.
- **`sed -bi`:** recognised as an in-place edit; the write scan did not see it as a write.
- **`toolkit-setup` neutrality:** the skill is now checked by the instruction-neutrality test the README said covered it.
- **Artifact bytecode:** payload verification avoids creating machine-specific bytecode in the extracted archive before scanning it for leaks.
- **Virtual environment verification:** scrubbed checks recognize the measured interpreter directory alongside its base Python installation.
- **Leak-scan allowance floor:** an allowance that is the home folder, a folder above it or a drive root is never applied.
- **Cross-product verification:** `verify-release.sh` takes the expected root from the zip's own product name and names an artifact of another product, with the remedy.
- **Bash interpreter probes:** health checks preserve failed engine results so session checks and backups cannot falsely record completion.
- **3.x PowerShell coverage:** the Bash-only matcher left PowerShell calls unguarded.
- **3.x Monitor coverage:** shell commands through Monitor now reach the command guard.
- **3.x NotebookEdit coverage:** the old Edit|Write matcher excluded NotebookEdit.
- **3.x raw plugin writes:** recognized shell writes to ESP, ESM, ESL, BSA and BA2 now refuse.
- **Prefs unclosed fences:** an unclosed fence no longer silently hides preferences.
- **Prefs annotated headings:** an annotated preference heading no longer silently hides preferences.
- **Prefs complexity:** fence lookup avoids repeated rescanning of the remaining file.
- **Prefs truncation:** capped or uninjected sections are reported.
- **Canary verdicts:** a refused Codex call is not counted as a successfully guarded call.
- **Setup folder casing:** equivalent case spellings do not imply that the install moved.
- **Setup Python check:** requires a working interpreter.
- **Unsupported Python versions:** setup reports them.
- **Codex unsafe install paths:** setup refuses characters its hook launchers cannot quote safely.
- **Non-ASCII configuration paths:** setup emits a paths file readable by the guard.
- **Ampersand install paths:** setup re-runs without breaking shell quoting.
- **Backup restore names:** manifest paths preserve underscores in plugin and archive names.
- **Backup collisions:** repeated edits within a second do not overwrite the previous backup.
- **Concurrent manifests:** locked appends retain each backup record.
- **Identical in-place restores:** skip unchanged files to preserve mod-manager hard links.
- **Windows launcher backslashes:** backslash invocation works.
- **Windows launcher spaces:** Python paths containing spaces work.
- **Snapshot reparse folders:** snapshot containment checks reparse folders.
- **Snapshot hard links:** snapshot containment checks changed hard links.
- **Snapshot time box:** snapshot containment checks time-box expiry.
- **Lock inheritance:** lock and unlock leave ACL inheritance unchanged.
- **Save reserve:** save snapshots require 2 GiB to remain free on the destination drive.
- **Local Spriggit:** verification wrappers can use a repo-local restored tool.
- **Spriggit comparison:** round trips of external ESPs may reorder records; compare re-serialized YAML.
- **Caprica command:** compile from the source folder using a bare script filename.
- **PyNifly guidance:** identify the loaded NiflyDLL.dll correctly.
- **Animated NIF guidance:** Blender round trips can drop controllers; do not use them for animated assets.
- **Precision port guidance:** corrected VR port notes.
- **AMR VR port guidance:** corrected VR port notes.
- **SCAR port guidance:** corrected notes for SCAR-related VR ports.
- **Owned record guidance:** GetFormFromFile references should use the mod's own forms.
- **None-check guidance:** GetFormFromFile results should be checked for None.
- **Shell advice:** messages distinguish ordinary file writes from raw plugin writes.
- **Telemetry vocabulary:** documentation uses current registry rule identifiers.
- **Broken documentation links:** corrected relative links.
- **Documentation link check:** the suite checks relative links.
- **Link-check scope:** dependency website Markdown does not count as toolkit filesystem links.
- **Codex E2E verdicts:** include checks added after the live probe returns, so a late failure cannot leave PASS cached.
- **Corpus path derivation:** preserves the historical mixed-slash spelling while removing it from shipped helpers.
- **Test tripwire isolation:** intentional setup denials do not contaminate another test's barrier log.
- **MSYS path regression:** explicitly mounts the test fixture instead of skipping on a mismatched temporary directory.
- **Migration regression:** verifies that the preferences heading precedes carried instruction blocks.
- **Ruff gate:** excludes adapter slot fragments that are not executable Python.
- **Test environment:** preserves SystemDrive.
- **Regex test cleanup:** the timeout reaper cannot leave its own spinning test child behind.
- **CI output parser:** reads the launcher-produced pytest format.
- **Release identity checks:** derived spellings are checked without printing matched values.
- **Committed archive contents:** git attributes cannot substitute unexpected working-tree content.
- **Session state race:** clearing lifts preserves the latest persistent lock decision under the writer lock.
- **DevBench recovery aliases:** every judged server spelling receives the same save/co-save snapshot handling.
- **Codex E2E setup:** a failed setup stops preparation before git initialization.
- **Codex E2E agent selection:** preparation selects Codex explicitly instead of relying on PATH detection.
- **Migration fence kind:** another fence kind cannot close the user's open fence.
- **Migration fence length:** a shorter run cannot close the user's open fence.
- **Migration fence trailing text:** a closer with trailing text cannot close the user's open fence.
- **Archive product slug:** archive names come from the selected commit's validated package name.
- **CI build log:** tee writes outside the checkout so it cannot trip the clean-tree gate.
- **CMD Python apostrophes:** recorded interpreter paths retain embedded apostrophes.
- **CMD delayed expansion:** literal exclamation marks in interpreter paths survive callers that enable expansion.
- **Partial load startup:** already-started test burners are stopped when a later launch fails.
- **POSIX test executables:** temporary interpreter and tool stubs carry executable permissions.
- **Auto setup regression:** the test recognizes platform-neutral AGENTS rendering when all agents are selected.
- **Backup coverage documentation:** matches the current implementation.
- **Command-payload coverage documentation:** matches the current implementation.
- **Paths-union coverage documentation:** matches the current implementation.
- **Historical identity examples:** derived scratch-path spellings are scrubbed consistently.

### Removed

- **pydantic:** unused, removed with its dependency pins.
- **cattrs:** unused, removed with its dependency pins.
- **mypy:** unused, removed with its dependency pins.
- **pytest-cov:** unused, removed with its dependency pins.
- **Windows-only requirements:** the pywin32 requirements files.
- **.NET 8/9 support:** optional .NET tools now use 10.
- **Node 20 support:** Node tooling now requires 24.
- **Spriggit 0.40.0 pin:** replaced by 0.41.0.
- **Old Bash rule engine:** retained only as a non-shipped test reference.
- **Separate Codex and adapter payload checkers:** folded into the shared checker.
- **Layer 2 research probes in the install zip:** the developer-only `scripts/layer2-spike/` measurement scripts stay in the repository but are no longer shipped.
- **Automatic jq installation:** removed from setup.
- **Automatic host-runtime installation:** removed from the default setup prompt.
- **Legacy paths writes:** setup writes the root paths file.
- **PyNifly 29.1 Blender add-on recommendation:** replaced by the validated 27.2.0 pin.

### Known limits

- **Hooks need configuration and approval:** unrendered source templates do not become live merely by approving them.
- **Codex scope:** Windows CLI support is claimed; WSL, desktop and IDE integration are not.
- **Codex confirmations:** the adapter refuses an ask and tells the user to run the intended action themselves.
- **Codex other MCP tools:** logged rather than guarded, except the recognized DevBench surface.
- **Codex user commands:** commands typed with `!` bypass hooks.
- **Codex subfolders:** Start Codex in the game folder itself, never a subfolder: from a subfolder no hook runs, and Codex does not tell you.
- **PowerShell policy:** AllSigned may prevent hook launch.
- **Pattern-based coverage:** the guard is an accident barrier, not a sandbox.
- **Encoded Bash program names:** some ANSI-C quoted spellings are not classified; this gap predates v4.
- **Interpreter literal writes:** a file name written by inline interpreter code (`python -c "open('X.esp','w')"`, Node `writeFileSync`) is judged by its file class alone, not by where it lands, so writing a plugin-named file outside the game is refused too; `cp` to the same place is judged by its destination and is allowed.
- **`cd` through a variable:** a `cd` whose folder comes from a same-command variable is not followed, so a name-filtered `find . -delete` after it is judged like `rm NAME` in the folder before the `cd`.
- **robocopy exclusions:** `/XF` patterns are read as copy sources, so an excluded `*.esp` can still raise the copy-onto-plugin note (a note only, never a refusal).
- **Backslashes in Bash guard messages:** an unquoted Windows path is named the way Bash reads it, backslashes removed (`Data\MyMod.esp` is shown as `DataMyMod.esp`, which is also the file Bash would write); quote Windows paths or use forward slashes.
- **Interpreter state writes:** code can write or link guard state through forms the shell scanner does not recognize.
- **Archive extraction:** unpacking into guard state is not covered.
- **Computed shell destinations:** can escape recognized self-protection.
- **Uncommon shell option forms:** can escape recognized self-protection.
- **Shell aliases:** can escape recognized self-protection.
- **Printed deletes across commands:** text one command writes to a script and a later command runs is not traced.
- **Piped delete operands:** a path one program prints and the next deletes (`| Remove-Item`, `| ForEach-Object { Remove-Item $_ }`, `Remove-Item $(...)`, `| while read f; do rm "$f"`) is not resolved for self-protection.
- **cmd for-loops:** deletes inside `cmd /c for /f ... do del` are not recognized.
- **Recycle Bin prefix concatenation:** a Recycle-Bin delete whose literal holds part of a guard-state path joined to a variable (`".skyguard/" + p`) is not refused as a guard-state delete; from inside the install only the unresolved-delete rule refuses it, which `guards off` lifts.
- **Recycle Bin placeholders:** a Recycle-Bin delete whose literal is an arbitrary placeholder replaced at run time (`"PATH".replace("PATH", p)`) is not refused as a guard-state delete; from inside the install only the install-delete rule refuses it, which `guards off` lifts.
- **Absolute-path inspection:** a read-only inspection that prints a delete of an absolute install path is refused by the install-delete rule, which `guards off` lifts.
- **Empty paths files:** can lose non-install protection folders if neither paths file retains them.
- **Broken legacy file:** beside a valid root file it is reported, but does not itself stop the guard.
- **Neutral shadow mode:** the neutral entry does not honour a guards-off lift.
- **Neutral other kind:** reported UNJUDGED.
- **DevBench replay:** its contents are not judged.
- **DevBench input:** its contents are not judged.
- **DevBench fixtures:** MCP fixtures were not re-captured during release-version verification.
- **Layer 2 opt-in:** the lock is off unless enabled.
- **Layer 2 platform:** Windows only; Linux/POSIX returns unsupported.
- **Lock rename:** a locked file can be renamed away.
- **xelib save:** can rename the locked original into zEdit Backups and write an unlocked replacement.
- **Lock with Wrye Bash:** manual measurement pending.
- **Lock with Steam update/verify:** manual measurement pending.
- **Lock with AE Creation downloads:** manual measurement pending.
- **Lock with the xEdit GUI:** manual measurement pending.
- **Lock with MO2:** manual measurement pending.
- **Lift visibility:** agent context, audit and skydoctor are covered; interactive user visibility remains unmeasured.
- **Relocking needs hooks:** no background service relocks an expired lift.
- **Agent unlock session:** Codex and Claude PowerShell do not receive the session binding used by Claude Bash; the user must unlock from their own terminal.
- **Adapted agents:** `hook-canary` and `skydoctor` read an adapted agent's heartbeats, which prove its adapter reached and delivered verdicts; coverage against that agent's transcript is not measured, so only the live canaries of ADAPTING.md prove its hook runs for every tool.
- **Raw unlock-all:** does not record a per-file lift list.
- **State replacement:** bounded PermissionError retries handle transient Windows readers; persistent sharing failures can still fail.
- **Shell delete recovery:** no general backup is made before deletion.
- **Backup scope:** script writes outside the snapshot paths are not automatically backed up.
- **Snapshot rate limit:** a garbled `.last_snapshot` reads as 0, so that call snapshots despite the 60-second rate limit; the snapshot rewrites the file.
- **Instruction expansion:** unusually long install paths may exceed the instruction budget; setup warns.
- **Migration scope:** edited hooks and skills are snapshotted rather than migrated.
- **Migration line endings:** carried notes use LF; the backup retains the original bytes.
- **Migration concurrency:** simultaneous runs can race and one may fail.
- **Animated NIFs:** Blender round trips can drop controllers.
- **Spriggit bytes:** a round trip may reorder records without changing the re-serialized content.
- **Spriggit in CI:** CI restores the pinned Spriggit with the .NET 10 SDK but never runs it; the serialize/deserialize round trip on .NET 10 was checked by hand, not by CI.
- **ReSaver lib jars:** only `ReSaver.jar` has a pinned SHA-256; the `lib/` jars from the same archive are not hashed, so take them from the archive you checked.
- **CrashLogger `__fastfail` dumps:** CrashLogger 1.24.0 added this dump class and no real one has been run through `crash-triage.py`; a dump it cannot read is counted as unparsed, not given a signature, so read that dump by hand.
- **Python 3.8/3.9:** engine compatibility is retained but these versions are outside the tested range.
- **Live release checks:** a clean suite and review do not substitute for the remaining manual and live-agent batch.
- **Container release legs:** require Docker and a separate build/round-trip check.
- **Devcontainer JDK check:** the image build checks the JDK with `java -version | grep -q` under `pipefail`, so it can fail the build even when the JDK is right (it fails closed: no image); rebuild. Docker legs only.

### Upgrading from 3.x

1. Unpack the new zip into a separate folder and, from the Skyrim folder, run `bash "<new folder>/tools/pre-update-snapshot.sh" .` before replacing files.
2. Extract the toolkit contents over the install; shipped files are replaced.
3. Run `bash setup.sh --agent auto`, or select `claude`, `codex` or `all`.
4. Review migrated local notes and the settings backup; migration deletes nothing.
5. Keep personal notes and standing preferences in `KNOWLEDGEBASE.local.md`, which is never shipped.
6. Install the pinned Node and .NET runtimes yourself if you use the optional tools that require them.
7. Update Spriggit to 0.41.0 or restore the repo-local tool.
8. Rebuild AutoMod with `bash tools/automod-cli.sh --rebuild`.
9. Move an AutoMod-local bsarch.exe into the net10.0 tools directory.
10. Review any retained legacy paths file and move missing protection folders into the root file.
11. Start Codex in the game folder, approve the toolkit hooks in `/hooks`, make real tool calls and check the canary.
12. Enable the optional lock only after reading its limits.
13. Run `bash tools/skydoctor`; a default install reports the lock as off.
14. Re-add any removed Python packages your own scripts imported.
15. User-global tools stay unchanged until you update them.

## v3.9.1 — 2026-09-09

### 🔒 Security / safety

- **`hook-canary.sh` could never go back to green: one blind event pinned it red forever.** It
  branched on the existence of a `.blind` marker and never compared it against the good heartbeat,
  so a single legitimate empty-payload invocation reported BLIND permanently — measured with good
  heartbeats 107 seconds newer while the hooks were receiving 900+ byte payloads. An instrument
  stuck red gets ignored, which is the same end state as no instrument.

  A blind marker is now CURRENT unless a **well-formed** marker is followed by a **strictly
  newer** good heartbeat. The first attempt at this fix cleared too eagerly and the release
  review caught it: EQUAL timestamps read green (same second proves no ordering), and a
  truncated marker like `20260909_15` sorts BELOW a real stamp and so read green too.

- **A recovered hook now reports `RECOVERED`, not `ALIVE`, and is counted separately.** For
  `backup-before-edit.sh` and `snapshot-before-tool.sh` -- which exit 0 silently on an empty
  payload -- this report is the only place intermittent starvation could ever surface, so
  folding "was blind, then recovered" into a green line hid it. Exit code stays 0: the hook IS
  working now, and saying otherwise is the false positive that gets an instrument ignored.

- **Known limit:** clock skew is out of reach by construction. No pair of timestamps can reveal
  a non-monotonic clock, so a heartbeat written after a clock step can still read as a recovery.

- **Hook budgets raised 30s → 60s (`PreToolUse`) and 30s → 120s (`SessionStart`).** v3.9 raised them from 5s after
  establishing that a late refusal is discarded. Two measurements bound the headroom and
  they disagree, so both are stated: in-hook instrumentation puts the slowest hook body at
  **557 ms**, while the worst `hook_success` duration across 250 transcripts is **8.2 s**.
  Against the pessimistic figure 60s is a ~7x margin — the same ratio 30s gave against the
  4,620 ms that motivated raising it — so this is headroom, not a fix. **SessionStart was
  already 120s before this arc**; only the four PreToolUse entries changed here.

### 📉 Corrections to what v3.9 claimed

- **v3.9's closing claim that "the real fix is the ~20 forking `echo | grep` pipelines" is
  withdrawn.** In-hook instrumentation measured `protect-bash.sh`'s entire body at **375 ms median**
  (max 465 ms) and its `INPUT=$(cat)` stdin read at **13 ms**. Removing ~35 forks would buy roughly
  250 ms against a 60,000 ms budget. The refactor was planned, priced against real data, and
  dropped — it would have rewritten the matching core of a 313-line delete guard for an
  unmeasurable gain.

### 🧰 Release process

- **The Nexus pack now has one home and one shape, enforced by a script and a test.**
  Across eleven releases it landed in six shapes and four places: v3.5 and v3.5.3 shipped
  EMPTY directories, `nexus-v3.8/` holds a zip named 3.8.1, and v3.9 split in two with the
  real pack on a network share while the canonical home held only `UPLOAD-NOTES.txt`. Every
  one passed review because nothing asserted the shape. `scripts/build-nexus-pack.sh`
  produces the pack and refuses to report success on an incomplete one;
  `tests/test_nexus_pack.py` asserts the shape.

- **The pack's zip is downloaded from the GitHub release, never rebuilt.** The installable
  bundle is built by CI from the tag with payload guards; a locally rebuilt zip could differ
  silently from the one the release page serves.

- **The changelog and description are passed in, not generated.** Extracting the `## <tag>`
  block from CHANGELOG.md yielded 147 lines of raw markdown where the pasteable version was
  63 — the Nexus box renders a flat list, so it is a rewrite, not an extract.

### 📚 Documentation

- **PyFFI/PyNifly recipes moved out of the always-loaded `CLAUDE.md` into the path-scoped
  `skyrim-nif` skill.** The HARD LIMITS stay in `CLAUDE.md` — a "never do X" rule must not sit
  behind a loading condition. `CLAUDE.md` drops 2,200 chars.

### ⚠ Known, not fixed

- **A hook run from the wrong directory resolves a plausible-but-wrong install root and silently
  allows.** `protect-bash.sh` derives its root from its own location; a byte-identical copy run from
  elsewhere returns zero bytes (= allow) for a command the same file at its real location correctly
  denies. `hook-canary.sh` still reports such a hook ALIVE, because it is running and receiving its
  payload. The obvious hardening ("deny unless the root looks like a Skyrim install") would break
  this repo, which carries these hooks and is not a game install.

## v3.9

### 🔒 Security / safety — read this one

- **A safety hook that runs out of time is IGNORED, and every hook here was
  configured 5 seconds -- three of the four; the fourth was 15.** MEASURED: a `PreToolUse` hook whose refusal arrives after
  its `timeout` has that refusal **discarded** — the command runs. Verified in both
  bypass and default permission modes, with controls passing in both (a hook denying
  in time blocks; one allowing in time runs). This is undocumented behaviour.

  It matters because `protect-bash.sh` measured **4,620 ms under machine load against
  a 5,000 ms budget** — 92% consumed. A busy machine (a build, a mod deploy, the game
  running) could silently disarm the delete guard. **All five hooks now use a 30s
  timeout**, re-verified: the same over-long hook now blocks correctly.

  ⚠ `tools/hook-canary.sh` cannot detect this class. The hook is alive and *does*
  receive its payload — it simply answers too late. Every liveness check added after
  the inert-hook incident is blind to it.

- **Updating the toolkit destroyed your own knowledgebase notes, and the README said
  it did not.** The documented update path is "extract the new zip over your install",
  so every file this toolkit ships is replaced — including the 177,459-byte
  `KNOWLEDGEBASE.md` that Claude was instructed, by this toolkit, to append your
  findings to. A toolkit must never ship a file it also asks you to edit.

  Your notes now go to **`KNOWLEDGEBASE.local.md`**, which is untracked, never shipped,
  and the release build **refuses** to produce a payload containing it. Claude reads
  both files and writes only to the local one.

  ⚠ **If you have notes in `KNOWLEDGEBASE.md` from an older version, move them across
  before you next update.** This change protects future accumulation; it cannot
  recover what a past extract already overwrote.

  The README FAQ said "Your knowledgebase additions are preserved". That sentence was
  false for as long as it has existed, and it is corrected.

- **New: `session-kb-guard.sh` + `tools/kb-guard.sh` — a copy of the files nothing can
  rebuild, taken before anything can overwrite them.** The change above protects
  accumulation that has not happened yet. This covers the install you already have: at
  **SessionStart**, `KNOWLEDGEBASE.md`, `KNOWLEDGEBASE.local.md` and `CLAUDE.md` are
  copied into `.claude/backups/kb/<stamp>/` whenever their content changed, and a file
  that vanishes, empties, or loses more than half its bytes raises an alarm naming the
  stored copy to recover from.

  It is a separate mechanism because the existing backup hook could not have covered
  this. `backup-before-edit.sh` fires on Edit/Write tool calls and works correctly —
  but a knowledgebase is usually written by a script run through **Bash**, which an
  Edit/Write hook structurally cannot see. MEASURED on the author's install: **zero
  `KNOWLEDGEBASE.md` backups across seven months**, of a 120 KB file, with nothing
  anywhere reporting it. (An earlier draft of this entry blamed the five-week
  inert-hook window; that was wrong, and reading the backup hook in full disproved it.)

  ⚠ **The snapshot is the protection; the alarm is a convenience on top of it.** An
  update that replaces a 100 KB knowledgebase with a 140 KB shipped one is a total
  loss that shrinks nothing and will not alarm.

  ⚠ **The rotation never prunes the oldest, the newest, or the largest stored copy of
  a watched file.** Every session after an unnoticed loss snapshots the damaged file,
  so a plain oldest-first rotation walks the good copy off the end a few sessions
  later — worse than having no store, because it looks like protection the whole time.
  Both clauses have their own test and their own mutation; the first version of that
  test covered only one of them and the mutation gate said so.

  Run it by hand any time: `bash tools/kb-guard.sh --verbose`.

### Caught by the release review, before shipping

A full review of everything this release changes (six reviewers, 20 files, ~1,500
added lines) found defects in the release's own new code. They are listed because the
first three were introduced by the fixes above.

- **Doubled path separators defeated the delete guard.** Resolving the install root
  emitted exactly one separator per separator, so
  `shutil.rmtree('C:\\Games\\Skyrim VR')` — which is how you *write* a Windows path
  in Python or PowerShell source — produced **no hook output at all**, and no output
  means allow. The pre-v3.9 rule was separator-agnostic, so this was a regression.
  ⚠ The 7,641-command replay could not see it: no historical command spells the
  install that way. **Replay prices over-blocking well and under-blocking not at all.**
- **The knowledgebase guard was slowest exactly when it had something to say.** With a
  full snapshot store and three alarms it took 21.7 s idle and 39.3 s under load,
  against a 30 s hook budget where a late answer is discarded — so the ALARM was the
  part most likely never to arrive. One `wc` fork per file per comparison became one
  for the whole store: **21.7 s → 1.6 s**.
- **`.claude/skyrim-paths.env` was written double-quoted and is SOURCED**, and `$` and
  backtick are legal characters in a Windows folder name. A real
  `C:/Games/My$Stuff/Skyrim VR` reached the guard as `C:/Games/My/Skyrim VR` — matching
  nothing, so the install was silently unguarded — and a backtick **executed on every
  Bash call**. setup.sh's own check could not catch either, because sourcing cleanly
  proves the file parses, not that it still says what was written. Now single-quoted,
  and validated by comparing the values back.
- A file emptied in one session and **deleted** in the next reported a clean install,
  because the GONE alarm keyed on the newest stored copy rather than the largest.
- A snapshot that **failed** to write reported "checked, unchanged" and counted the
  empty directory it had just made as one kept.
- The one refusal deliberately built to survive a broken jq emitted **invalid JSON**
  when the configured jq path contained backslashes — which is what `where jq` prints,
  and what this toolkit's own setup text tells you to paste. Unparseable output is not
  a deny; it is an allow.
- `tools/verify-release.sh`, the cold-clone verifier, had drifted from the release
  gate at birth: a deliberately broken payload passed all fourteen of its checks.
- Coverage gaps, each proven by putting the bug back and watching the named test go
  red: the regex escaping that keeps `C:/Program Files (x86)/…` guarded, the
  configured config directory (the only thing protecting an MO2 user's INIs), the
  30 s timeouts this release leads with, and two of the three files the new guard
  watches — including `KNOWLEDGEBASE.local.md`, the file this release moves
  accumulation into. **230 → 251 tests, 8 new mutations.**

Two corrections to earlier drafts of these notes, since a permanent record has no tone
of voice: three of the four hooks were on a 5 s timeout (the fourth was 15 s), and a
`git clone` of a Skyrim-named repository was **never** a false positive — `git clone`
names no destroyer, so the old rule never reached its path test. The scratchpad, the
toolkit checkout and `C:/Temp/skyrim-notes.txt` were, and are verified.

### Fixed

- **The delete guard treated any path containing the word "Skyrim" as your game
  install.** It refused work in a temp folder, in a checkout of this toolkit, in
  and `C:/Temp/skyrim-notes.txt`. (An earlier draft also claimed a `git clone` of a repository whose *name*
  contains "Skyrim VR". MEASURED by replaying **7,641 real commands**: 443 were
  refused, and **71 of those were this false positive — none of the 71 named the
  install**. The guard now resolves the real install root, from the hooks' own
  location plus paths recorded by `setup.sh`.

  ⚠ Worth stating because it changes what a fix would have to be: **a shell parser
  would not have fixed this.** It would bind the verb to its target correctly and
  still refuse, because the *target* matched. The defect was path recognition, not
  scoping.

- **Redirects and tool output into the game directory were never advised at all** on
  any install whose path contains a space — which includes the default Steam layout.
  The rules used `[^"' ]*`, which cannot span the space in `C:/Games/…`. Fixed by
  the same resolution work; this one was under-blocking, not over-blocking.
- The Champollion refusal is now scoped to the live install, so the remedy its own
  message prescribes — copy the `.pex` to a temp directory and run it there — is no
  longer itself refused.
- Six rules decided "is this the install" independently; they now share one answer, so
  a seventh inherits it instead of hand-rolling a seventh regex.

### Added

- `setup.sh` records the resolved game, config and load-order paths to
  `.claude/skyrim-paths.env` (machine-local, never tracked, never shipped). The hooks
  read it *in addition to* the root they derive themselves, so a missing or unreadable
  file can never leave the guard with nothing to match.
- If no install root can be resolved, the delete guard **refuses** rather than
  allowing, with a `GUARD INERT` reason — and there is now a test and a mutation
  proving that branch bites. It was previously shipped, load-bearing and untested.

## v3.8.3 — 2026-09-06

### 🔒 Security / safety — read this one

- **Every safety hook in this toolkit was INERT, and is now fixed.** They fired on
  every call, read **zero bytes**, fell through their first guard and exited 0 —
  which is byte-identical to deciding "this is fine". If you have been running this
  toolkit, **none of the protections in the README have actually been in force**:
  not the ESP write block, not the game-directory delete block, not the automatic
  backups. `AUDIT_LOG.txt` recording an empty command field is the fingerprint.

  The cause is one idiom: `INPUT=$(cat /dev/stdin)` returns nothing in the Claude
  Code hook environment, while a bare `cat` returns the payload. MEASURED across two
  machines — seven consecutive probes, 0 bytes via `/dev/stdin`, 641–2840 bytes via
  bare `cat`, PreToolUse and PostToolUse alike.

  **Why it lasted: nothing ran the hooks as processes at all.** `/dev/stdin` is a
  symlink to `/proc/self/fd/0` — it resolves when stdin is a real file or an
  MSYS-shell pipe, and fails when stdin is a Win32 pipe from a non-MSYS parent,
  which is how Claude Code (a Node process) spawns a hook. A behavioural test does
  reproduce it on Windows, because Python's `subprocess` hands bash a Win32 pipe
  too; on Linux `/dev/stdin` resolves for any pipe and the same test passes with the
  defect in place. So it is now checked three ways: a behavioural suite that runs
  each hook as a process, a text-level repo invariant that holds on every platform,
  and a gate in the release workflow that inspects **the built payload** rather than
  the repo.

  **After updating, run `bash tools/hook-canary.sh`.** It reads heartbeats written
  from inside real invocations and reports ALIVE / BLIND per hook — the only evidence
  that survives the gap between a piped harness and the real hook environment.

- **Hooks no longer prompt you for routine work.** The policy is now: `deny` for what
  is never correct, `advise` (a note to the assistant, no prompt, call proceeds) for
  what is consequential but legitimate, and `ask` for what is genuinely your decision
  — used for exactly one rule, a ReSaver `--apply` that mutates a save. Previously
  ordinary modding work raised confirmation prompts, and a guard that prompts on
  routine work gets approved by reflex and then protects nothing: the X4 toolkit
  measured 40 such prompts across 13,282 commands, every one noise.

- **New: `tools/hook-canary.sh` and `tools/toolchain-check.sh`.** The canary reports
  ALIVE / BLIND / NOT-EXERCISED per hook by reading heartbeats written from inside
  real invocations — the check that answers "are my hooks working *here*", which
  neither a test suite nor a release gate can answer for your install. `toolchain-check`
  asks whether every documented tool can actually start, after Spriggit spent an
  unknown period unable to run while the docs still called it the preferred workflow.
  Both existed only in the author's install until now — the same private-fix,
  public-reference gap as the hooks themselves.

- **Path guards now match both separators.** A guard written with only forward
  slashes matched `C:/Games/Skyrim` and missed `C:\Games\Skyrim`, while `cmd.exe`
  and `powershell` are both callable — so the backslash spelling was the one that got
  through. **Ten** existing guards converted plus four new rules, verified in both
  directions with controls that must not fire.

- **A hook that cannot see its input now refuses instead of passing silently.**
  Silence *is* allow, and that silence is what hid the outage above. The two blocking
  hooks deny with `GUARD INERT` and say the rules were never evaluated; the two
  recording hooks cannot block, so they write `BLIND` to the audit log rather than
  implying a backup exists.

- **Hooks are no longer tied to one machine's paths.** They resolve the project root
  from `CLAUDE_PROJECT_DIR`, falling back to their own location, so backups, audit
  log and snapshots land in the right place on any install.

### ⚠ Breaking

- **`cosave-info` / `cosave-cli.sh` now use exit codes.** Previously *every* path
  exited **0**, including for a file that is not a co-save at all, so any script
  gating on `$?` treated garbage as success. Now: **0** parsed and trustworthy ·
  **1** parsed but degraded (no chunks read, or coverage below 90%) · **2** not a
  co-save, unreadable, or a usage error. If you gate on this tool's exit code,
  re-check what you expect.

- **`crash-triage` and `spriggit-cli.sh` gained new non-zero exits.** `crash-triage`
  now exits 1 on `RESULT: INPUT SET INCOMPLETE` (a file that looks like a dump but
  did not match the pattern) as well as the `PARSER DEGRADED` states added in v3.8.2.
  `spriggit-cli.sh` exits 1 when spriggit exits 0 having written no output, which it
  previously reported as success.

### Added

- **`crash-triage` reports its INPUT SET, not just its arithmetic.** It now prints
  how many files are in the folder, how many matched, and — the part that matters —
  how many are *near misses*: files whose name carries the `crash-` prefix but which
  the extension pattern did not match. This is the failure that produced a confident
  "8 log(s) [OK]" while twenty newer dumps sat unread in the same folder. An
  accounting check cannot see it, because a denominator can only account for what it
  was handed. Priced against a real 341-file folder: 28 matched, 0 near misses, and
  ordinary mod logs like `LeveledListCrashPrevention.log` correctly ignored.

- **`papyrus-triage` reports the age of the log it chose.** Triaging a months-old log
  as though it were the current session is an answer about a world that has moved on,
  and it reads exactly like a good answer. Logs older than 7 days are marked STALE.
  Only applies to the auto-selected newest log — naming a file explicitly is a
  deliberate act and is not second-guessed.

### Changed

- **The release workflow now inspects the built payload, not just the repo.** It
  already asserted that `tests/` had not leaked and `.claude/` was present; it now
  also refuses to publish a bundle whose hooks read stdin through `/dev/stdin` or
  have lost their liveness heartbeat. This is checked against the artifact because
  the artifact is what users run — "the repo passes" is a different claim, and the
  divergence between the two has shipped a defect here before. The check was proven
  red before being trusted: green on the real payload, red with the defect
  reinstated, red with a heartbeat removed, and no false positive on the comments
  that explain the defect.

- **The mutation gate now requires a test *failure*, not merely a non-zero exit.**
  pytest exits 1 when a test fails but 2 on a collection error, 4 on a usage error
  and 5 when nothing is collected, so a mutation naming a test that had since been
  renamed or deleted satisfied the old `!= 0` check and passed forever while proving
  nothing — the stale-anchor problem on the other side of the pair, with only the
  anchor half asserted. Two anchors went stale during this release arc, so the shape
  is demonstrated rather than hypothetical. Audited at the same time: all 38
  mutations named a collectable test, so nothing was actually rotten — the gate
  simply could not have told us if it were.

- **And it now requires each mutation anchor to match exactly once.** `str.replace`
  rewrites every occurrence, so an anchor matching two sites silently reverted two
  independent fixes while crediting the kill to one. That is the other half of the
  same pair: one check catches an anchor whose partner *test* went stale, the other
  catches an anchor that grew to match *too much*. One anchor had become ambiguous
  two commits earlier and was narrowed.

### Fixed

- **`crash-triage` said nothing about dumps it did not read.** One unparsed file out
  of two is 50% — over the ratio, under the two-dump floor — and printed a bare
  `unparsed : 1/2 (50%)` above `RESULT: OK`, exit 0, with nothing indicating those
  bytes went unread. The floor stays where it is: a single unparsed dump is not
  evidence of a format change, and lowering it re-introduces the false positive that
  fired on 5 of 28 real dumps. But *"not a format change"* is not the same claim as
  *"nothing to see"*. The count is now annotated whenever it is non-zero, in two
  tiers, while the verdict gate is untouched. A clean run still prints `0/28 (0%)`
  with no arrow — an annotation that always fires is not distinguishable from one
  that works.

- **A degraded verdict named one of two problems.** When the parser was degraded *and*
  the input set was short, only `RESULT: PARSER DEGRADED` printed. The near-miss count
  was in the header, so nothing was hidden, and the exit code was 1 either way — but
  the verdict line is the part people read, and a reader pointed at a parser problem
  has no reason to suspect the file list was also incomplete. They are fixed in
  different places. The verdict now names both when both are present.

- **"No crash dumps found" was printed for a folder that had them.** With zero files
  matching the extension pattern, the tool refused with *"No crash-\*.txt or
  crash-\*.log found"* — the near-miss failure in its purest form, telling the reader
  a folder is empty of dumps while dumps sit in it under an extension the pattern does
  not cover. Exit 2 was never the problem; the sentence was. It now names the files
  carrying the `crash-` prefix that it declined to match.

- **`crash-triage` counted a crash with no module attribution as "no exception".**
  A jump to an address inside no loaded module leaves CrashLogger nothing to
  attribute, so the line ends after the address. `MODULE+OFFSET` was mandatory, so
  such a dump could never parse — a dump we read, reported as one we did not. It now
  keys as `(no module)+ADDR`. On the dev install this took a healthy folder from 27
  of 28 parsed to 28 of 28.

- **The .NET check added in v3.8.2 asked the wrong question.** It verified the .NET 9
  **runtime**, and a runtime-only install passes that while Spriggit remains
  unusable: it starts, prints a version, and cannot serialize, because it fetches its
  serializer at runtime via `dotnet tool install` and only a matching **SDK** can
  install a tool whose assets target `net9.0`. Measured on a machine in exactly that
  state — the check said the requirement was met while `esp-verify-wrapper.sh`, which
  drives spriggit, was still dead. .NET's own error misleads here too: it reports
  `DotnetToolSettings.xml was not found in the package` when the file is present,
  under a framework the SDK will not select. `setup.sh` and the README now ask for
  the SDK, and name the separate SDK 8 that AutoMod pins via `global.json`.

- **`esp-verify-wrapper.sh --help` printed source code.** The banner renders a fixed
  line range of the script's own comment header, and the range had drifted past the
  end of it. Now bounded, with a test that reads the range out of the source rather
  than running the script — the first version of that test was vacuous on any machine
  without spriggit installed.

## v3.8.2 — 2026-09-06

### Fixed

- **`crash-triage` read 1 of 7 dumps on CrashLoggerSSE 1.2x and reported them as
  "no exception".** Four format differences, reported and fixed by
  [@yurar123](https://github.com/yurar123) in #8: the exception-type pattern
  excluded `C++ Exception` (the `+` and lowercase fall outside `[A-Z_]+`), the stack
  header changed from `PROBABLE CALL STACK` to `CALL STACK ([P]robable / [S]tack
  scan):`, frame lines gained `[P]`/`[S]` markers, and every C++ throw collapsed
  onto its shared throw site instead of being distinguished by its `Info` string.
  Both formats are supported; verified against 28 real Skyrim VR dumps spanning
  three CrashLogger versions (18x v1-15-0-0, 4x v1-22-0-0, 6x v1-24-0-0) with
  byte-identical output.

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
    of the three 1.2x changes. Every *complete* dump yielding zero frames is
    reported. Truncated dumps (no stack section written at all — 5 of 28 on the
    dev install) are excluded, since they are not evidence about frame syntax.
    Note this is a per-run signal: a folder mixing old and new formats still has
    dumps with frames, so it reports OK. It catches the changeover, not a mixture.

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
