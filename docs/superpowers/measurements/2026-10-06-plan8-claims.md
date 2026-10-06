# Plan 8 T4: the docs-claim audit (2026-10-06)

Every sentence in the README, the `agent/` instruction sources (core, Claude addendum, Codex addendum),
`docs/*.md`, `ADAPTING.md` and the CHANGELOG's Unreleased section that says what the guards do, checked
against a **probe**: a real request through the real entry of a scratch install.

## Method

- **Tree:** branch `p8-claims` at `63c29aa` (master + all of Plan 6). Claim line numbers below are at `63c29aa`.
  Doc fixes: commit `27495bd`.
- **Install:** a copy of the tree (no `.git`, no `tests/`) at `<scratchpad>/p8-claims/g/Skyrim VR/` with a stub
  `SkyrimVR.exe`, stub `Data/` files (`x.esp`, `Skyrim.esm`, `Skyrim - Textures0.bsa`, `Scripts/a.pex`,
  `Scripts/Source/a.psc`, `SKSE/Plugins/foo.ini`, `notes.txt`), configured by the real `setup.sh` (exit 0). Its
  `.claude/skyrim-paths.env` was then pointed at fake settings, load-order and MO2 folders beside it
  (`MyGames/Skyrim VR` with `Skyrim.ini` and `Saves/Existing.ess`, `LocalAppData/Skyrim VR/plugins.txt`,
  `MO2/mods`, `MO2/overwrite`), so no probe named the real install or the real config folder.
- **Entries (one process at a time, through the resource-budget tool, `--cpu 3`):**
  - **Claude:** `bash .claude/hooks/protect-bash.sh | protect-files.sh | protect-mcp.sh | session-kb-guard.sh |
    backup-before-edit.sh | snapshot-before-tool.sh`, payload shaped like `tests/fixtures/payloads/claude/*`
    (`Bash`, `PowerShell`, `Monitor`, `Write`, `Edit`, `NotebookEdit`, `mcp__devbench-vr__*`, `SessionStart`).
  - **Codex:** `pwsh -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .codex/hooks/skyguard-entry.ps1
    pre-tool|session-start` (the `commandWindows` form of the rendered `.codex/hooks.json`), payloads shaped like
    `tests/fixtures/payloads/codex/0.160.0/*` (`Bash`, `apply_patch`, `exec_command`, `write_stdin`, `mcp__*`).
  - **Neutral:** `python tools/skyguard/run.py neutral pre-tool --root R --root-win R --project R` (ADAPTING.md §4).
  - The CLIs as a user would run them: `tools/skyguard.sh lock|guards|snapshot`, `tools/skydoctor`,
    `tools/hook-canary.sh`, `tools/devbench-cli.sh` (against `tests/mock_devbench.py`), `codex execpolicy check`
    against the installed `.codex/rules/skyrim.rules` (codex-cli 0.160.0).
- **Scripts and raw results** (scratchpad, not committed): `mkgame.py`, `probe.py` (the runner),
  `probes_def.py` / `probes2_def.py` / `probes3_def.py` (122 + 47 + 5 probe definitions), `stateful.py`,
  `envprobe.py`, `lockprobe.py`, `backupprobe.py`, `regcheck.sh`, `dbprobe.sh`; results `out1.json` ... `out3.json`,
  `stateful-*.json`, `envprobe.json`, `lockprobe.json`.
- **Counts:** 174 probe definitions, **475 judged calls** (Claude 158, Codex 165, neutral 152) plus the stateful and CLI runs of
  sections 6-8. Probe strings lived in files, never on a command line.
- **Verdicts:** `PASS` (the doc says what the probe shows); `FIXED` (the doc claimed more, or something else, than the
  probe shows; reworded in `27495bd`); `LIMIT` (the doc states a limit and the probe shows that limit);
  `RE-PROBE <lane>` (another Plan 8 lane changes this behaviour or text; today's behaviour recorded);
  `CARRIED` (needs a live agent session; the earlier measurement is cited and the manual batch re-checks it);
  `PENDING` (waits for the batch by plan).
- Decisions are written `claude / codex / neutral`. `adv` = allowed with a note to the agent.

**Gate result: 0 rows where a doc claims more than its probe shows** after `27495bd`, excluding the RE-PROBE rows.
Before the fix: 15 rows (all marked FIXED below) where the doc said something the probe did not show.

## 1. Deletes (core.md 188-201, Codex addendum 22-24, README 424, CHANGELOG 113)

| # | Claim | Probe | Result | Verdict |
|---|---|---|---|---|
| D1 | "deleting in the game or config folders is refused" (Codex addendum 23-24); deletes in the install refused, "relative paths resolve against the working directory the agent reports" (core 188-190) | `rm Data/x.esp` cwd = install; `rm x.esp` cwd = `Data/`; `rm Data/x.esp` cwd = unrelated folder; absolute `rm "<install>/Data/x.esp"` from the unrelated folder | deny/deny/deny (S-DEL-INSTALL) ; deny ×3 ; **allow ×3** (resolves outside, correct) ; deny ×3 | PASS |
| D2 | same, PowerShell dialect | `Remove-Item .\Data\x.esp` from install / `.\x.esp` from `Data/` / from unrelated; absolute `Remove-Item -Recurse -Force "<install>\Data\meshes"`; `ri .\x.esp` (Data); `del .\Data\x.esp` | deny ×3 ; deny ×3 ; allow ×3 ; deny ×3 ; deny ×3 ; deny ×3 | PASS |
| D3 | `rd /s`, `del` through cmd | `cmd /c rd /s /q Data` (install); `cmd /c del a.pex` (Data/Scripts) | deny ×3 ; deny ×3 | PASS |
| D4 | the program list: `find -delete`, `git clean`, `shutil.rmtree`, a Recycle-Bin delete (core 192-193; README 424; core 260 "judges a Recycle-Bin delete like any delete") | `find "<install>/Data" -name "*.esp" -delete`; `git clean -fdx` (install); `python -c "shutil.rmtree(r'<install>\Data\meshes')"`; `[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile(...,'SendToRecycleBin')` | deny / **allow** / deny ; deny ×3 ; deny ×3 ; deny ×3 | PASS (Codex `find`: see D5) |
| D5 | (follow-up) Codex judges its shell as PowerShell, where `find` is `find.exe` (a text search) | the same `find` wrapped as `bash -c '...'` on Codex | deny ×3 | PASS; Known-limit candidate K6 |
| D6 | config folder and MO2 `mods/` / `overwrite/` (core 192; README 424; CHANGELOG 113) | `rm "<config>/Skyrim.ini"`; `rm -rf <MO2>/mods/SomeMod`; `rm -rf <MO2>/overwrite/x` | deny ×3 (S-DEL-CONFIG) ; deny ×3 (S-DEL-MO2) ; deny ×3 | PASS |
| D7 | "a delete whose target the guard cannot work out in a command naming a protected folder", "`rm -rf *` after an unknown `cd`" (core 194-196) | `rm -rf "$TARGET" && ls "<install>/Data"`; `cd "$SOMEWHERE" && rm -rf *` | deny ×3 (S-DEL-UNRESOLVED) ; deny ×3 (S-DEL-UNKNOWN-DIR) | PASS |
| D8 | "unparseable while it contains a deletion and names a protected folder -- it refuses" (core 214-215) | `rm -rf "<install>/Data` (unclosed quote) | deny ×3 (S-DEL-FALLBACK, status partial) | PASS |
| D9 | "Scratch folders (`/tmp`, temp, the scratchpad) are not protected" (core 196) | `rm -rf /tmp/...`; `rm -rf "$TEMP/..."`; `rm -rf <unrelated>/x` from the install | allow ×3 each | PASS |
| D10 | "resolved ... never guessed from the word Skyrim" (core 200; safety-philosophy 92-99) | `rm -rf C:/Temp/skyrim-notes.txt`; `rm -rf "<scratch>/Unrelated/Skyrim VR copy"` | allow ×3 each | PASS |
| D11 | Known limit by ruling: an unresolved operand deleted from inside the install is allowed silently (guard-coverage C1) | `rm -rf "$X"` cwd = install | allow ×3 | LIMIT |
| D12 | "moving files out of them" (core 194) | `mv "<install>/Data/x.esp" /tmp/x.esp` | deny ×3 (S-DEL-INSTALL) | PASS |
| D13 | "Moving or renaming the install, `Data`, the settings folder or `Saves` away" (core 203) | `mv <install>/Data <install>/Data.old`; `mv <config>/Saves ...`; `mv <install> ...` | deny ×3 (S-MOVE-ROOT) each | PASS |
| D14 | "Deleting Bethesda registry keys (`reg delete` / `Remove-Item` on a Bethesda key or one of its parents)" (core 204) | `reg delete "HKLM\...\Bethesda Softworks\Skyrim VR" /f`; `reg.exe delete ...`; `Remove-Item -Recurse 'HKLM:\...\Bethesda Softworks'`; parent `reg delete HKLM\SOFTWARE\WOW6432Node /f` | deny ×3 (DENY_REGISTRY) each | PASS |
| D15 | Codex `.rules` refuses the registry deletes "spelled as the rules file lists it", even before approval (README 652-656) | `codex execpolicy check --rules .codex/rules/skyrim.rules -- reg delete "HKLM\SOFTWARE\Bethesda Softworks" /f`; `reg.exe ...`; a subkey `...\Bethesda Softworks\Skyrim VR`; lower case; `reg query` | forbidden ; forbidden ; **no match** ; **no match** ; no match | PASS (the README restricts the claim to the listed spellings; the subkey and case gaps are K5) |
| D16 | E16 control: "`grep -rn "rm -rf" <install>` is allowed" (core 188-190; CHANGELOG 116) | `grep -rn "rm -rf" "<install>/Data"`; `Select-String -Pattern "Remove-Item" -Path <install>\Data\notes.txt` | allow ×3 each, no note | PASS |
| D17 | "Pattern matching on parsed commands, not a sandbox (a relative path inside `python -c` code is not resolved)" (core 197) | `python -c "os.remove('Data/x.esp')"` from the install; same with an absolute path | **allow ×3** ; deny ×3 | LIMIT |
| D18 | interpreter code that launches a delete, command-running wrappers (CHANGELOG 115-118) | `python -c "os.system('rd /s /q <install>\Data')"`; `Start-Process cmd -ArgumentList "/c rd /s /q <install>\Data"`; `schtasks /create ... /tr "cmd /c rd /s /q <install>\Data"` | deny ×3 ; deny ×3 (S-DEL-FALLBACK) ; deny ×3 | PASS |
| D19 | `rm Data/x.nif` "gets an advisory, not a refusal" because the hook "is never told the caller's working directory" (safety-philosophy 105-107) | D1, plus `rm Data/x.esp` with **no** cwd in the payload | with cwd: deny; with none: adv (S-RELATIVE-UNKNOWN) / deny (Codex: "payload has no cwd") / adv | **FIXED** (the sentence predated Plan 5's cwd resolution) |

## 2. Plugin and archive writes (core 205-209; README 424-425; safety-philosophy 37, 68; CHANGELOG 107-110)

| # | Claim | Probe | Result | Verdict |
|---|---|---|---|---|
| W1 | edit tools refuse a plugin/archive write | Claude `Write Data/New.esp`, `Edit Data/x.esp`, `NotebookEdit` on `.esp`, `Write` on `Skyrim - Textures0.bsa`; Codex `apply_patch` Add / Update; neutral `write` | deny on every route (W-PLUGIN / write.DENY_PLUGIN) | PASS |
| W2 | "anywhere" (core 205) | `Write <unrelated>/Outside.esp`; `echo x > /tmp/t4-anywhere.esp` | deny ×3 each | PASS |
| W3 | shell list: `>` `>>` `tee` `Set-Content` `Out-File` `sed -i` `perl -i` `dd of=` `[IO.File]::Write*` (core 206) and `Add-Content` (draft row) | each one, relative from the install or absolute from the unrelated folder | deny ×3 for all 10 (DENY_WRITE_PLUGIN) | PASS |
| W4 | downloaders (core 206; CHANGELOG 113) | `curl -o`, `wget -O`, `Invoke-WebRequest -OutFile` onto `Data/x.esp` | deny ×3 each | PASS |
| W5 | "a name written in `python -c` / `node -e` code" (core 207) | `python -c "open('Data/New.esp','w')..."`, `node -e "...writeFileSync('Data/New.esp',...)"` | deny ×3 each | PASS |
| W6 | heredoc and NTFS stream (CHANGELOG 118) | `cat > Data/New.esp <<'EOF'`; `echo x > Data/x.esp:stream` | deny ×3 each | PASS |
| W7 | "Not covered: a name built at runtime, a script run from a file (`python fix.py`)" (core 208-209) | `python fix.py` | allow ×3 | LIMIT |
| W8 | "Copying a finished plugin onto `Data/` is advised, not refused" (core 208; CHANGELOG 110). The draft's guess "ask on Claude, deny on Codex" is not what any doc says | `cp built.esp <install>/Data/x.esp`; `Copy-Item ...`; `mv built.esp ...` | adv ×3 (S-COPY-PLUGIN) each | PASS |
| W9 | Codex `apply_patch` Delete / Move-away of a plugin is a delete (guard-coverage X2) | `*** Delete File: Data/x.esp`; `*** Move to: ../x.esp`; Delete `SkyrimVR.exe`; Delete `<config>/Skyrim.ini` | deny (S-DEL-INSTALL) ×3 ; deny (S-DEL-CONFIG) | PASS |
| W10 | apply_patch through the shell; unattributable patch refused (guard-coverage X3) | `apply_patch <<'PATCH' ... Add File: Data/New.esp`; `a=apply_patch; $a <<'PATCH' ...` | deny (W-PLUGIN) ; deny `GUARD INERT ... unreadable patch` | PASS |
| W11 | "Binary Files Are Sacred ... cannot be written directly" (safety-philosophy 68) | W1-W7 | true for the edit tools and the readable shell writes; W7 shows a script file is not seen | **FIXED** (scoped) |
| W12 | "File guard ... Advises on INIs, load-order files and scripts; it does not prompt" (README 425) | `Write <config>/Saves/Existing.ess` | ask (Claude) / deny NEEDS YOUR CONFIRMATION (Codex) / ask (W-SAVE) | **FIXED** (its one prompt is a save) |

## 3. Notes (ADVISED: core 217-227; README 424-425; safety-philosophy 36-37)

| # | Claim | Probe | Result | Verdict |
|---|---|---|---|---|
| A1 | Skyrim INIs | `Edit <config>/Skyrim.ini`; `echo x >> <config>/Skyrim.ini` | adv ×3 each | PASS |
| A2 | SKSE plugin configs; Papyrus `.psc`/`.pex`; load-order files | `Edit Data/SKSE/Plugins/foo.ini`; `Edit a.psc`; `Write a.pex`; `Edit <loadorder>/plugins.txt` | adv ×3 each | PASS |
| A3 | redirects into `Data/` advised; "a `cp`/`mv` there is silent" (core 222-223) | `echo x > Data/notes.txt`; `cp a.txt <install>/Data/notes.txt` | adv ×3 ; allow ×3, no note | PASS |
| A4 | "A decompiler writing its output into the install" | `Champollion.exe <unrelated>/b.pex -p <install>/Data/Scripts/Source` | adv ×3 (ADV_EXTERNAL) | PASS |
| A5 | "`sed -i` with a backslash path" (core 224) | in the install: `sed -i 's|old|C:\Games\x|' Data/notes.txt`; outside: same on `<unrelated>/notes.txt` | adv ×3 ; **allow ×3, no note** | **FIXED** (scoped to a file in the install) |
| A6 | AutoMod write without `--dry-run` | `automod-cli.sh esp add-weapon ... --json`; same with `--dry-run` | adv ×3 ; allow ×3, no note | PASS |
| A7 | "Naming a plugin or INI in a read-only command is silent" (core 227) | `head -c 4 Data/x.esp` | allow ×3, no note | PASS |
| A8 | Edits to other install files advised (safety-philosophy 37; Codex addendum 24) | `Edit Data/notes.txt`; Codex Update `<config>/Skyrim.ini` | adv ×3 ; adv | PASS |
| A9 | "Tool output aimed straight at an existing `.psc`" is DENIED (core 210) | `decomp.exe in.pex -o Data/Scripts/Source/a.psc`; `decomp.exe in.pex > Data/Scripts/Source/a.psc`; `some-tool > a.psc` | deny ×3 (S-PSC-OUT) ; **adv ×3** (S-WRITE-INSTALL) ; adv ×3 | **FIXED** (the deny is a program's output flag; a redirect gets a note) |
| A10 | Champollion against a `.pex` in `Data/Scripts/` denied (core 210-211) | `Champollion.exe Data/Scripts/a.pex` | deny ×3 | PASS |

## 4. The seven asks (core 229-236; safety-philosophy 57; README 649-651; Codex addendum 19-21)

| # | Claim | Probe | Result | Verdict |
|---|---|---|---|---|
| Q1 | ReSaver write with `--apply` asks; on Codex it is refused with `NEEDS YOUR CONFIRMATION` | `resaver-cli.sh clean "<config>/Saves/Existing.ess" --apply`; control without `--apply` | ask / deny NEEDS / ask ; allow ×3 | PASS |
| Q2 | DevBench save over an existing save (`game` tool, console `save <name>`) | MCP `game {save Existing}`; `console {exec save Existing}`; papyrus `Game.SaveGame Existing`; scenario step `game save Existing`; control `game save BrandNew` | ask / deny NEEDS / ask each ; allow ×3 | PASS |
| Q3 | shell write, copy or move onto a save; edit-tool write to one (two rules) | `cp a.ess <config>/Saves/Existing.ess`; `Copy-Item ...`; `Write <config>/Saves/Existing.ess`; Codex Update of that save | ask / deny / ask (S-WRITE-SAVE) ; same ; ask / deny / ask (W-SAVE) ; deny NEEDS | PASS |
| Q4 | git that discards uncommitted changes | `git checkout -- Data/x.esp`; `git reset --hard` (cwd install) | ask / deny / ask each (S-GIT-DISCARD) | PASS |
| Q5 | `lock disable` asks on Claude, refused on Codex | `skyguard.sh lock disable` | ask / deny NEEDS / ask (S-LIFT-PERMANENT) | PASS |
| Q6 | `snapshot restore --in-place` and `restore-from-backup.sh --restore` ask (core 235-236); safety-philosophy 57 named only the first | both; control `snapshot restore abc123` (new folder) | ask / deny / ask each (S-RESTORE-INPLACE) ; allow ×3 | PASS (safety-philosophy FIXED to name both) |
| Q7 | exactly seven ask rules (core 229, safety-philosophy 57) | the rule registry (`rules.RULES`, `decision == "ask"`) | 7: S-RESAVER-APPLY, S-WRITE-SAVE, S-GIT-DISCARD, S-DEVBENCH-SAVE, S-LIFT-PERMANENT, S-RESTORE-INPLACE, W-SAVE | PASS |
| Q8 | Codex never asks: every ask is a refusal (README 649-651; ADAPTING §5 "The Codex adapter turns every ask into a deny") | all 165 Codex calls | 0 asks; every Claude ask above is a Codex deny that starts `NEEDS YOUR CONFIRMATION` | PASS |
| Q9 | "Every message is at most two plain lines and never echoes the command" (CHANGELOG 122) | all 382 non-empty messages from the 475 calls | 0 over two lines (Codex: after its one-line prefix); 0 contain the command text; they name a file's basename | PASS |

## 5. DevBench (core 212; README 424, 662-664; safety-philosophy 38; guard-coverage C10, X4)

| # | Claim | Probe | Result | Verdict |
|---|---|---|---|---|
| B1 | `smp reset` refused by MCP, curl and `devbench-cli.sh` | MCP `console {exec smp reset}`; curl to `:8921` and `:8920`; `wget` to `:8921`; curl to the `/mcp` JSON-RPC endpoint; `devbench-cli.sh exec "smp reset"`; `devbench-cli.sh tool console '{...}'` | deny ×3 each (S-DEVBENCH-CRASH); control `console {help}` allow ×3 | PASS |
| B2 | the line is found anywhere: scenario steps, papyrus `ExecuteCommand` (C10) | scenario step with `smp reset`; papyrus `Debug.ExecuteCommand("smp reset")` | deny ×3 each | PASS |
| B3 | `replay` contents are not judged (C10, a limit; ADAPTING §9) | MCP `replay {play rec1}` | allow ×3 | LIMIT |
| B4 | the judge recognises any server name containing `devbench` in any case (ADAPTING §9; X4) | `mcp__skyrim-devbench__console` and `mcp__DevBench-vr__console` with `smp reset`, sent to each entry | deny ×3 each | PASS |
| B5 | Claude's matcher `mcp__devbench.*` misses other names (C10, claude addendum) | READ `agent/targets/claude/settings.json` (B4 sends the payload to `protect-mcp.sh` directly, so it does not test the matcher) | matcher unchanged at 63c29aa | RE-PROBE T6 (the DevBench matcher changes in p8-deferred) |
| B6 | Codex: other MCP servers logged, not guarded (README 660-662; CHANGELOG 140-146) | `mcp__probe__echo {"text":"smp reset"}` | allow, one line in `.claude/backups/codex-unhooked-tools.log` | PASS |
| B7 | Codex MCP is deny-only: the save ask is a refusal; arguments are judged as sent (X4) | Q2 on Codex; `tool_input` as the bare string `smp reset` | deny NEEDS ; deny | PASS |
| B8 | "arguments the engine cannot read are an INERT refusal" (X4) | neutral `mcp_args: "{not json"`; Codex `tool_input: "{not json"` | deny GUARD INERT ; allow (Codex args are always re-encoded as JSON text, so they are always readable; the string has no refused line) | PASS (the INERT branch is reachable only where args arrive as text) |
| B9 | DevBench 1.11.0+ for `alive`; "Older DevBench still works" (devbench skill 65-80; CHANGELOG 1097) | `devbench-cli.sh alive` against `mock_devbench.py running` and `legacy` (no `/api/health`) | RUNNING rc 0 ; NOTE "predates /api/health (1.11.0+)", falls back, RUNNING rc 0 | PASS |
| B10 | "Update the VR Address Library first" (devbench skill 68-72; setup.sh) | advice, not a guard claim; the wording in `setup.sh` | not probeable | PASS (skill: stated as advice); setup.sh text RE-PROBE T7 |

## 6. Decoded text, unreadable input, broken configuration

| # | Claim | Probe | Result | Verdict |
|---|---|---|---|---|
| U1 | decoded text run as commands refused (core 213; CHANGELOG 115) | `echo ... | base64 -d | bash`; PowerShell `iex` of a decoded string | deny ×3 each (S-SHELL-UNREAD, status partial) | PASS |
| U2 | an empty command is GUARD INERT (core 214) | empty `command` | deny ×3 (`GUARD INERT: ... received no command`) | PASS |
| U3 | Codex refuses a payload with no cwd or a non-object `tool_input` (X1) | no `cwd`; `tool_input: "rm -rf Data"` | deny GUARD INERT each | PASS |
| U4 | Codex `cmd` / `chars` keys get the shell rules (X1) | `exec_command {cmd: "Remove-Item .\Data\x.esp"}`; `write_stdin {chars: "rm Data/x.esp\n"}` | deny each | PASS |
| U5 | NotebookEdit with no `notebook_path` refused (C7) | `{new_source, cell_id}` only | deny (W-INERT-NOPATH) | PASS |
| U6 | Monitor judged under both dialects; `ws` form allowed; "`ws` beside any `command` key, `ws` that is not an object, or neither key is denied" (C8) | Monitor `Remove-Item .\Data\x.esp`; `ws {url}` only; `ws` + `echo hi`; `ws` + `rm Data/x.esp`; `ws` as a string; neither key | deny ; allow ; **allow** ; deny ; deny ; deny | **FIXED** (`ws` beside a `command` is judged as that command) |
| U7 | "If no root resolves, or a path in `skyrim-paths.env` is unreadable, the guard DENIES" (core 201) | env variants (`envprobe.py`, Claude and Codex, three commands each): binary garbage; a malformed `SKYRIM_MO2_MODS` (unclosed quote); a relative `SKYRIM_CONFIG_DIR` | deny on every call, even `echo hi` (GUARD INERT / S-INERT-CONFIG) | PASS; the file's location RE-PROBE T8 |
| U8 | "A broken `skyrim-paths.env` does NOT cost you refusals ... the guard silently narrows" (safety-philosophy 103; coordinator row from the T8 lane) | as U7, plus an EMPTY file, plus a malformed `SKYRIM_LOADORDER_DIR` (not a protection key) | malformed protection key / unreadable: deny everything. Empty file: install still denied, **MO2 delete allowed**, nothing reported. Load-order key: verdicts unchanged | **FIXED** (malformed or unreadable denies; only an empty file or a missing key narrows silently). File location RE-PROBE T8 |

## 7. Self-protection, lifts, shadow mode (Plan 6: README 431-578; core 242-248; CHANGELOG 5-36)

| # | Claim | Probe | Result | Verdict |
|---|---|---|---|---|
| S1 | writes to `.skyguard/` refused on the shell, the file tools, `apply_patch`, moves and deletes (README 559-561; core 248) | `Write .skyguard/state.json`; `echo {} > ...`; `tee`; `Copy-Item`; `git checkout -- .skyguard`; `rm`; Codex Add File `.skyguard/x.json` | deny on every route (S-/W-SELF-STATE) | PASS |
| S2 | known gaps MEASURED as allowed: interpreter writes, `install`, archive unpacking (README 561-564) | `python -c "open('.skyguard/state.json','w')"`; `install -m 644 x .skyguard/state.json`; `tar -xf a.tar -C .skyguard` | allow ×3 each | LIMIT |
| S3 | the heartbeats and audit log get a note, not a prompt (README 570-572; C18). The plan row "self-protection deny on the heartbeat directory" is not what any doc says | `Write` / `echo >` into `.claude/backups/.hook-heartbeat/claude/` | adv ×3 (W-/S-SELF-GUARD) ; a recursive delete of that folder: deny (S-DEL-INSTALL, so `guards off` would shadow it) | PASS (docs match; the deny on a delete comes from the install rule) |
| S4 | guard code and config get a note (README 570-572) | `Edit .claude/hooks/protect-bash.sh`, `.claude/settings.json`, `.claude/skyrim-paths.env` | adv ×3 each | PASS |
| S5 | not guarded at all: `GUARD-FILES.sha256`, the launchers, `MANIFEST.jsonl` (README 566-569) | `Edit GUARD-FILES.sha256`; `Write .claude/backups/MANIFEST.jsonl`; `echo x >> tools/skyguard.sh` | allow ×3 each, no note | LIMIT |
| S6 | `lock unlock` and `guards off` allowed with a note (README 520-521; CHANGELOG 21) | both, with `--session` | adv ×3 each (S-LIFT-NOTE) | PASS |
| S7 | hand-clearing a lock target and running the unlock `.cmd` refused (README 521-524) | `attrib -R`; `icacls ... /remove:d`; `Set-ItemProperty ... IsReadOnly`; `.\tools\skyguard-unlock.cmd` | deny ×3 each (S-LIFT-RAW, S-LIFT-UNLOCKCMD) | PASS |
| S8 | lock off by default (README 431, 445; CHANGELOG 7) | fresh install: `lock status`; `skydoctor` | "not locked" ; Layer 2 `not live -- never applied here (off by default)`, exit 1 | PASS |
| S9 | "A default install, with the lock never applied, exits 1 on purpose" (README 626-627) | `skydoctor` on the fresh install | exit 1 | PASS |
| S10 | skydoctor reports layer by layer, `live` / `not live` / `unknown` with a reason, AGENTS.md within 32,768 bytes (README 620-625) | the same run | every line carries a state and a reason; `AGENTS.md live present (29,973 bytes)`; `doctor.py` `AGENTS_MD_MAX = 32768` | PASS |
| S11 | a lift lasts an hour; "the next session start relocks anything still open" (README 503-507; core 244-246) | on the scratch install (`lockprobe.py`): `lock lock` (2 of 2 locked; a write to `Skyrim.esm` raises PermissionError, `x.esp` writes) -> SessionStart S5 -> `lock unlock Data/Skyrim.esm --session t4-S5` ("for 60 min") -> write succeeds -> SessionStart S6 | S6's banner: `1 vanilla files were left unlocked; relocked.`; status 2 of 2; the write is refused again | PASS |
| S12 | an agent without a session id cannot lift (core 246-247) | `lock unlock` / `guards off` with no `--session` and no TTY | exit 2, "No session id ..." | PASS |
| S13 | `guards off`: every rule still runs, "a refusal becomes an allow plus a warning", `SHADOW` per call, for an hour (README 541-545) | SessionStart S1 -> `guards off --session t4-S1` -> 13 probes in S1 | install delete, plugin writes, S-DEL-FALLBACK, S-DEL-UNRESOLVED, smp reset: allow + `Guards OFF: this would have been denied (<code>)`; **the ReSaver ask also became an allow**; 13 `SHADOW` audit lines; an allow in a shadowed session logs `SHADOW: allow` | **FIXED** (a confirmation prompt is shadowed too) |
| S14 | never shadowed: GUARD INERT, the lift refusals, `lock disable`, `.skyguard/` writes (README 545-547) | in S1: `GUARD INERT` empty command; `attrib -R`; the unlock `.cmd`; `lock disable`; `.skyguard` write (tool and shell) | deny / deny / deny / **ask** / deny / deny, each logged `SHADOW <code> kept` | PASS |
| S15 | "honoured only inside that session" (README 552) | the first two probes with session `t4-S2-other` | deny each | PASS |
| S16 | the lift ends at the next session start (README 534-535 "reported and cleared at the next session start"; S-LIFT-NOTE) vs "for an hour" (README 543) | SessionStart S3 while S1's lift is open, then S1's install delete | banner `Guards were left OFF by an earlier session ...; they are back ON.`; S1's delete denied | **FIXED** (README 543: "for an hour or until the next session starts") |
| S17 | Codex `.rules` never shadowed | Codex's own execpolicy, outside the engine (D15) | not reachable by `guards off` by construction | PASS (READ) |
| S18 | "every lift is shown to you" | README 527-534 already claims only "reported to the agent, recorded in the state file, and shown by `skydoctor`" | lift notes arrive as model context (S6, S11) | PENDING (Table V, the batch, T19) |
| S19 | the neutral entry and `guards off` | S1's 13 probes through the neutral entry with `session_id: t4-S1` | none shadowed | no doc claim either way; noted (K4) |

## 8. Session start, banners, backups, canary (README 52-53, 579-616, 640-643; core 101-122, 269; Codex addendum 8-13)

| # | Claim | Probe | Result | Verdict |
|---|---|---|---|---|
| N1 | Codex session context starts with `SKYGUARD: Codex hooks live` (Codex addendum 9; README 643) | Codex session-start entry | first line `SKYGUARD: Codex hooks live (definitions v1).` | PASS |
| N2 | the session hook prints `## My preferences` (core 101; CHANGELOG 213) | `KNOWLEDGEBASE.local.md` with one preference; Claude and Codex session starts | both print `USER PREFERENCES ... - T4PREF: ...` | PASS |
| N3 | kb-guard snapshots the knowledgebase and instruction files at session start (core 118-120) | first Claude session start | `KB GUARD: snapshot ... KNOWLEDGEBASE.md ... KNOWLEDGEBASE.local.md ... CLAUDE.md ... AGENTS.md` | PASS |
| N4 | "approve the 2 Skyrim toolkit hooks" (README 641-642; setup.sh) | rendered `.codex/hooks.json` | 2 hooks (SessionStart, PreToolUse) | PASS |
| N5 | frozen definitions: a changed definition needs re-approval, and the CHANGELOG says so (Codex addendum 11-12; README 647-648) | sha256 of `agent/targets/codex/hooks.json` vs `FROZEN.sha256` | `3cc0c07e...` = the v1 ledger line; `tests/test_codex_definitions.py` holds the CHANGELOG rule | PASS (READ + hash) |
| N6 | `AGENTS.md` within Codex's 32 KiB (README 621; CHANGELOG 189) | installed `AGENTS.md` | 29,973 bytes | PASS |
| N7 | start Codex in the game folder: from a subfolder no hook runs (Codex addendum 14-16; README 645; CHANGELOG 126) | needs a live Codex session | MEASURED Plan 4 (R-subdir, 2 of 2) | CARRIED (manual batch M1 step 7) |
| N8 | hook-canary states (README 52-53; CHANGELOG 184) | `health.STATES`; a canary run | `HEALTHY, STALE, DEGRADED, NOT EXERCISED, UNMEASURABLE, NEVER INVOKED`; the docs listed five | **FIXED** (NEVER INVOKED added) |
| N9 | auto-backup "Copies every file ... before modification" (README 426); "Every edit has a timestamped backup", "Every file modification is logged" (safety-philosophy 52, 62) | `backup-before-edit.sh` on an `Edit` of `foo.ini`; `snapshot-before-tool.sh` on `echo y > Data/SKSE/Plugins/foo.ini` | Edit: one manifest line and one `AUDIT_LOG` line with the backup name; the shell write: no backup | **FIXED** (scoped to the edit tools and `apply_patch`) |
| N10 | Codex: files an `apply_patch` changes or deletes are backed up first (Codex addendum 24-25) | the Codex Update probes on `Skyrim.ini` | `AUDIT_LOG` `apply_patch -> ...Skyrim.ini (backup: ...)` | PASS |
| N11 | recovery snapshots of `plugins.txt` / `Skyrim*.ini` before a shell command, and the one save before a ReSaver `--apply` or DevBench save (README 606-608) | `skyguard.sh snapshot list` after the probes | `20261006_151530-config  2 changed: plugins.txt, Skyrim.ini`; two `-save` snapshots of `Existing.ess` (ReSaver, DevBench) | PASS |
| N12 | Claude addendum: "`Bash` -> protect-bash ...; `Edit\|Write` -> ...; The PowerShell tool is not matched yet" (claude.md 11-15) | `agent/targets/claude/settings.json`; the PowerShell and Monitor probes above reach `protect-bash.sh` | matchers `Bash\|PowerShell\|Monitor`, `Edit\|Write\|NotebookEdit`, `mcp__devbench.*` | **FIXED** (DevBench matcher name left out: RE-PROBE T6) |
| N13 | "Other agents ... Layer 2 ... is not yet released" (README 695; ADAPTING 5, 16, 34; CHANGELOG 86) | S8, S11: the lock ships and works, off by default | shipped, opt-in | **FIXED** (and `test_adapting_claims.py` updated) |
| N14 | "apply plus remove over the 26 names of the full VR list" (README 444) | `lock._names("vr")` | 33 names; the 26 were the spike's R1 set (`2026-10-04-plan6-ml2.md` C10) | **FIXED** |
| N15 | the core instructions say "`snapshot-before-tool` ... before any Bash command" (safety-philosophy 40) | not probed for read-only skipping | reworded to "before shell commands" | **FIXED** (weakened to what is shown) |

## 9. ADAPTING.md (Plan 7)

| # | Claim | Probe | Result | Verdict |
|---|---|---|---|---|
| P1 | neutral entry refuses unknown fields and a repeated key, answers with `SKYGUARD-OK` (§9) | `{..., "zzz": 1}`; `"command"` twice | `SKYGUARD-OK` + deny GUARD INERT (`unknown field 'zzz'`, `duplicate field 'command'`) | PASS |
| P2 | refuses `agent: claude` / `codex`; `v` must be the integer 1; not-JSON refused (§4, §9) | `agent: "claude"`; `v: true`; `not json at all` | deny GUARD INERT each | PASS |
| P3 | `other` allowed, labelled not judged, status partial (§4, §9) | `kind: other` | allow, `NOT JUDGED: this tool kind is not guarded`, `partial` | PASS |
| P4 | `mcp` to the DevBench rules; another server allowed, `NOT JUDGED: only a DevBench MCP server is guarded`, `partial` (§9) | B1, B6 through the neutral entry | deny ; allow + that context, `partial` | PASS |
| P5 | a write that names no file is refused (§9) | `kind: write, paths: []` | deny `engine.NO_PATHS` | PASS |
| P6 | the Claude adapter passes `ask` and `deny` through (§5) | §4 rows | Claude asks are `ask` | PASS |
| P7 | "the toy test does NOT prove hook wiring" (§7) | a limit statement; only live canaries prove it | consistent | PASS |
| P8 | the claims gate of ADAPTING: prose facts carry `[src:]` anchors (`tests/test_adapting_claims.py`) | ran the test file | green (with N13's test update) | PASS |

## 10. Rows recorded for other lanes (today's behaviour; not edited here)

| # | Claim | Today | Lane |
|---|---|---|---|
| R1 | `setup.sh` closing text: "Claude Code will: Ask permission before editing any game file" | false: edits in the game get a note, not a prompt (A1-A8) | RE-PROBE T7 |
| R2 | `setup.sh`: Spriggit `--version 0.40.0`, ".NET 9 SDK", "do NOT install unpinned: 0.41+ targets net10.0"; PyNifly "verified with 29.1.0" | README, CHANGELOG, `docs/installing-tools.md`, skills and `.config/dotnet-tools.json` all say 0.41.0 / .NET 10 / PyNifly 27.2.0 | RE-PROBE T7 |
| R3 | `setup.sh`: DevBench 1.11.0+, Address Library first | consistent with the skill (B9, B10) | RE-PROBE T7 |
| R4 | where `skyrim-paths.env` lives (core 200-201; safety-philosophy 92-103; README; engine messages name `.claude/skyrim-paths.env`) | `.claude/skyrim-paths.env` | RE-PROBE T8 |
| R5 | Claude's DevBench matcher name gap (C10; claude addendum) | `mcp__devbench.*` | RE-PROBE T6 |
| R6 | the setup prompt's authorization semantics (winget allow-list; CHANGELOG 91) | `tools/inventory-runtimes.py parse_authorization`: fail-closed, the known ids only | RE-PROBE T6 (`SETUP_PROMPT.txt` authorization form) |
| R7 | Spriggit and PyNifly paths, after T6b | docs agree with each other (R2) | RE-PROBE T7 (setup text only) |

## 11. Stale telemetry codes

`shell.DENY_INSTALL` and `shell.ASK_RESAVER` appear in no current doc. The only mentions are dated history
(`docs/superpowers/plans/2026-10-02-plan4-codex-target.md`, `2026-10-02-plan5-rules-and-noise.md`,
`docs/superpowers/measurements/2026-10-02-ask-and-analysis-failure-audit.md`) and two test comments that name the
replacement (`shell.S-DEL-INSTALL`, `shell.S-RESAVER-APPLY`). The live audit log codes are the registry ids
(`shell.S-DEL-INSTALL`, `shell.S-RESAVER-APPLY`, `write.W-SAVE`, ...). Dated records are not rewritten. PASS.

## Known-limit candidates (for T12's CHANGELOG; not fixed here)

- **K1** The shadow-mode warning says "this would have been denied" for a shadowed ask, and `guards off` prints
  "a refusal becomes an allow" (`engine.SHADOW_HEAD`, `cli.py` 125): engine wording, the docs now say asks are shadowed too.
- **K2** A Monitor call in the PowerShell dialect is judged correctly but its message names the file through the bash
  reading (`deletes .Datax.esp`): message text only.
- **K3** An emptied `skyrim-paths.env` silently drops the MO2 and settings-folder protection (U8); the canary and
  `skydoctor` do not report it.
- **K4** The neutral entry does not honour a `guards off` lift (S19). No doc claims either way.
- **K5** Codex `.rules` matches exact tokens: a Bethesda subkey or a lower-case spelling is not forbidden before the
  hooks are approved (D15). The README already limits the claim to the spellings the file lists.
- **K6** Codex shell commands are judged as PowerShell, where `find` is `find.exe`; on a machine whose PATH puts Git's
  `usr/bin` before `System32`, `find ... -delete` would be GNU find and would delete unjudged (INFERRED, not measured).
