# Plan 5 cumulative pricing: P5-BASE to the merged tree -- 2026-10-04

Task 17 step 1. Every unique Bash, PowerShell, Edit, Write and MCP tool call in this machine's real Claude Code
history (`~/.claude/projects`, nested subagent transcripts included) was fired at the OLD engine (`tools/skyguard` at
**P5-BASE `0704791`**, the tree Plan 5 branched from) and at the NEW engine (branch `plan5` at `544efd4`, master merged in:
Plans 3 and 4 included), by `scripts/guard-corpus/price.py --base 0704791`, each in its own subprocess. Raw items stay
outside the repository (they hold secrets and private paths); only counts and hand-read shapes are recorded here.

## Result

**N = 66,773 unique items.** Bash 59,537 / Write 3,843 / Edit 1,726 / PowerShell 1,662 / MCP (`mcp__devbench-vr__*`) 5.
Raw tool calls before de-duplication: Bash 61,708; Edit 9,197; Write 4,807; PowerShell 1,714; DevBench MCP 8.
Engine errors: **0 old, 0 new** (a crash in the new engine cannot read as clean: `newly-error` is its own direction and is 0).
Parse failures (new engine): **63 of 61,199 shell items = 0.103%** (Bash 61 of 59,537 = 0.102%; PowerShell 2 of 1,662 =
0.120%); gate is < 0.5%. The old engine has no parser, so 0 by construction.

### The old x new decision matrix

| old > new | Count | Reading |
|---|---|---|
| allow > allow | 63,794 | |
| advise > advise | 208 | the advice that survived, see below |
| advise > allow | 2,220 | noise removed |
| allow > advise | 3 | three real writes into the install the old text rule missed (read) |
| advise > deny | 8 | executed raw-text writes of a plugin (E21), all probes or tests (read) |
| advise > ask | 1 | a real ReSaver `--apply` (read) |
| allow > deny | 24 | 20 Champollion on `Data/Scripts` `.pex`, 3 relative in-install deletes, 1 `smp reset` (read) |
| allow > ask | 9 | real ReSaver `--apply` runs (read) |
| ask > ask | 1 | a test harness feeding the command to the hook as data: a KNOWN false positive (read) |
| ask > allow | 25 | text that mentioned ReSaver; none was an execution (read) |
| deny > deny | 274 | |
| deny > allow | 205 | E16 and the registry/config/Champollion text matches: all false positives (read) |
| deny > ask | 1 | the same command as one of the 9 above (read) |
| **total** | **66,773** | |

### The four totals the brief asks for

- **Total asks: 12** (was 26) = 0.018% of items. Target "about 0, being only executed ReSaver `--apply` and real save
  overwrites" is met: **11 of the 12 are an executed ReSaver `--apply`** (9 allow>ask, 1 advise>ask, 1 deny>ask: `clean`,
  `set-var`, `set-global` or `reset-havok` on a real `Save*.ess` or a copy of one); **1 is a false positive** (the
  hook-test harness below). There is no other ask in the history: S-DEVBENCH-SAVE asked 0 times in 66,773 items.
- **Total advise: 211** against the P2 baseline of **2,028**: **10.4% of the baseline count**, a **89.6% cut**. The history grew
  from 53,445 (Plan 2) to 66,773 items, so as a RATE the advice now fires on 0.316% of items where P2 measured 3.79%
  (2,028 / 53,445): **8.3% of the baseline rate**. Old engine at P5-BASE on this same history: 2,437 (Bash 2,253 + PowerShell
  26 + Edit 93 + Write 65). New: Bash 63, Edit 83, Write 65, PowerShell 0.
  Shell advice alone: 63 of 61,199 shell items = **0.103%** (old 2,279 = 3.72%).
- **Total denies: 306** (old 480). With the E16 class removed -- the 205 false-positive denies, every one read -- the old
  engine would have denied **274**, and the new engine denies those same 274 plus **32 new, correct** ones
  (20 Champollion on a `Data/Scripts` `.pex`, 8 executed raw writes of a `.esp` = E21, 3 relative deletes inside the
  install, 1 `smp reset`), minus 0: **no deny the old engine had and should have kept is gone** (see the reading of the 205).
  By tool: Bash 282, Write 14, PowerShell 8, Edit 2.
- **Every item the old engine denied or asked about and the new one allows is a text match, not an action** (205 + 25 read
  below). Zero real deletes of the install, zero real registry deletes of a Bethesda key, zero real ReSaver executions lost.

## Per rule, all six directions

The old engine records no rule ids (Task 1 added `Audit.rule`), so `newly-*` comes from the engine's own trace and
`no-longer-*` is attributed by the OLD engine's message class. A rule that fires on both sides at one decision is in neither
column.

| Rule (new engine) | newly-deny | newly-ask | newly-advise | no-longer-deny | no-longer-ask | no-longer-advise |
|---|---|---|---|---|---|---|
| S-DEL-INSTALL | 144 | | | | | |
| S-DEL-UNRESOLVED | 100 | | | | | |
| S-DEL-FALLBACK | 2 | | | | | |
| S-DEL-CONFIG | 1 | | | | | |
| old delete-text rules (E16 class) | | | | 169 + 15 + 1 (to ask) | | |
| S-REGISTRY | 5 | | | 20 | | |
| S-PSC-OUT | 4 | | | | | |
| S-CHAMPOLLION | 23 | | | 1 | | |
| S-WRITE-PLUGIN (shell, E21) | 10 | | | | | |
| W-PLUGIN (Edit/Write) | 16 | | | | | |
| S-DEVBENCH-CRASH | 1 | | | | | |
| S-RESAVER-APPLY | | 12 | | | 25 | |
| S-COPY-PLUGIN | | | 36 | | | |
| S-WRITE-INSTALL / S-WRITE-INI / S-WRITE-SKSE-INI / S-WRITE-LOADORDER | | | 6 / 4 / 5 / 5 | | | |
| S-AUTOMOD-ARCHIVE / -ESP / -NIF | | | 3 / 2 / 7 | | | |
| W-PAPYRUS / W-INSTALL / W-SKSE-INI / W-INI | | | 124 / 13 / 10 / 1 | | | |
| old "references plugin/archive files" | | | | | | 1,743 |
| old "external tool writing into the install" | | | | | | 186 |
| old "moving/copying inside the install" (S-MOVE-INSTALL, removed by ruling) | | | | | | 134 |
| old "references load order" | | | | | | 111 |
| old "relative delete" note (S-DEL-UNRESOLVED-CWD, removed by ruling) | | | | | | 18 |
| old redirect / PAPYRUS SCRIPT / AutoMod / `sed -i` notes | | | | | | 20 / 10 / 3 / 4 |

(`newly-deny` sums to 306 = the 24 + 8 + the 274 that the old engine ALSO denied for another reason: its message class differs,
so the per-rule count includes denies that moved rules, which is why the column exceeds the matrix's 32. The matrix is the
authority for decisions; this table is for attribution. `reason_moved` = 483 items kept their decision and changed rule.)

## Disagreement classes, each read

Every cell where the decision moved was printed with its command and read. The 205 + 25 + 9 + 8 + 24 + 3 + 1 + 1 + 1 +
1 individual items are all accounted for; the 2,220 lifted advisories were classified by old message and sampled (45 read).

**1. deny > allow (205): all false positives, none a real action.**
- **169 "names both a deletion and a path inside the install" (E16) and the 15 "config directory" ones (184 together).**
  Each was checked by an independent regex over its `rm`/`del`/`Remove-Item` operands (not the engine under test): **134 have
  no delete operand outside the scratchpad, `/tmp` or `AppData/Local/Temp`**; the other **50 were hand-read** from the printed
  command: 18 delete a sibling of the install or a file elsewhere (`C:/Games/spriggit-tmp` 9, `C:/GOG modding tools/_*` 5,
  `C:/Games/zgin` ffmpeg temporaries 3, `~/.plans` 1), 13 delete a variable that holds a scratch folder (the assignment is in
  the same command), 13 are text only (a python heredoc that writes a script, a hook-test harness piping `rm -rf` into the hook as
  data, a `grep` for the word), 3 are `git rm --cached` (the index, not the file), 2 are `rm -f tools/_before_check.py` run inside
  another repository, 1 is `rm -rf tools/__pycache__` from the install (allowed by the deliberate `__pycache__` scratch ruling,
  Task 7). **No real delete of the install is lost.**
- **20 "Cannot delete Bethesda registry keys":** 17 are text (`grep`, heredocs that edit docs or tests, an `ls`), 3 are
  `reg delete 'HKCU\Software\X4ToolkitTests...'`, a non-Bethesda key outside S-REGISTRY's scope by ruling (Task 9).
- **The 15 "config directory" ones are in the 184 above** (all scratch or `/tmp` deletes; the `tools/_before_check.py` pair is
  in another repository). **1 "Champollion against a PEX":** the `.pex` is copied to a scratch folder first.

**2. ask > allow (25): all text.** Python and bash heredocs that author tests and documentation for this very rule
(`tests/test_skyguard_shell.py` edits, pricing-doc appends, probe scripts). **Zero real ReSaver executions stopped asking.**

**3. The 12 asks.** 11 real `resaver-cli.sh|ResaverCLI.java clean|set-var|set-global|reset-havok ... --apply` (allow>ask 9: 5 Bash,
4 PowerShell; advise>ask 1: PowerShell; deny>ask 1: a ReSaver run whose scripted cleanup also named the config directory, which the
old text rule denied). **1 known false positive (ask>ask):** a
hand-run harness that pipes `'bash tools/resaver-cli.sh reset-havok save.ess out.ess --apply'` into `protect-bash.sh` as DATA
while an opaque `"$JQ"` program elsewhere in the command triggers the R4 text fallback. Recorded in Task 6; the cost of
removing it is leaving a real opaque ReSaver run unasked, so it stays.

**4. allow > deny (24) and advise > deny (8): all true positives by design.**
- **20 Champollion** on `Data/Scripts/<x>.pex`, the install's own rule ("has destroyed .psc sources before"), ported at Task 9.
  Note: the deny fires even with `-p /tmp/...`; the rule reads the operand, not the output flag. The history ran these before
  the rule existed.
- **3 relative deletes inside the install** (`cd tools; rm -rf pynifly.old-tmp`; `rm -f protect-bash.blind` and
  `rm -f NEXUS-CHANGELOG-v3.9.txt` after `cd` into an install subfolder): R9 cwd resolution. The old rule could not see them.
  Two are the user's own scratch (`_CLAUDE_OUTPUT`, heartbeat markers); the rule has no exemption for them, by design.
- **1 `smp reset` through curl** (`/api/tool/console`): S-DEVBENCH-CRASH, the measured crash.
- **8 E21 raw writes of a `.esp`** (`echo v>victim.esp`, `printf decoy > Data/Decoy.esp`, `printf TES4 > Mod.esp`, `> zzz_*.esp`
  probes, one `echo` to a path ending `Mod.esp.yaml` that sits in a fake-spriggit heredoc beside `printf TES4 > Mod.esp`): every
  one is a file a session wrote on purpose to test or fake a plugin. E21 denies the write wherever it lands, by design.

**5. allow > advise (3).** `sed -i` and `mv` into `Data/Scripts/Source` by relative path: real writes into the install the old text
rule missed (S-WRITE-INSTALL). The advice wording for `sed -i` still says "a > redirect empties it first", which is wrong for
`sed -i` (a copy-edit-rename). Minor, ledgered.

**6. advise > allow (2,220).** By old message: "references plugin/archive files" 1,735, "external Papyrus/ESP tool writing into the
install" 186 (narrowed to decompilers by ruling), "moving/copying inside the install" 134 (removed by ruling), "references load
order" 111, `sed -i`/AutoMod/redirect/relative-delete notes 54, PAPYRUS SCRIPT 10. A random 45 were read: every one is a read or a
sanctioned tool (`grep`/`cat`/`ls`/`automod-cli esp info`/`spriggit serialize` of a plugin, `skyrim-winner`, a node script that
`require`s xelib, a Caprica compile, a `cp` of a plugin into a backup folder, `spriggit deserialize` of the user's own ESP back into
`Data/`). **0 of the 45 was a write the user would have wanted a note about.** The 3 AutoMod and 4 `sed -i` notes lost were also read
(all 7): 4 `sed -i` edit a scratchpad file, 2 AutoMod notes sat on commands that only read (a `grep` of its source, a python read of a price file), and 1 is a
`7z e ... -o tools/nif-tool` extraction into the install, a real write the old text rule happened to match and no rule covers now
(one instance in 66,773; ledgered, not a rule). The executed forms stay covered (S-AUTOMOD-*: 12 advise remain; S-WRITE-*: 20).

## Noise cut, in one line

Advice per item: **3.79% (P2: 2,028 / 53,445) to 0.316% (211 / 66,773)**. Asks: **26 to 12, 11 of them real**. Denies: **480 to 306,
of which 205 were removed false positives and 32 are new true positives**.

## Method notes

- Scrubbed raw items, per-direction files and `summary.json` are in the session scratchpad (outside the repo).
- The matrix's `ask>ask` and the table's S-RESAVER-APPLY `newly-ask 12` differ by design: 12 asks in total = 11 new + 1
  unchanged.
- `price.py`'s `moves()` counts a rule at a changed decision as a movement; with an old trace of zero rules, `no-longer-*` is
  zero in `summary.json` by construction, which is why the table above attributes by message class instead.
- The old engine at P5-BASE has 2 PowerShell rules (none for the tool): its 26 PowerShell advisories are text matches.
