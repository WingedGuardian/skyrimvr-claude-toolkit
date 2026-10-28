---
name: mod-release-review
description: Use before publishing a new version of a Skyrim mod you made — a plugin, script, mesh, asset or SKSE-DLL mod — whether a first release, an update, or a Nexus upload. Reviews only what changed since the last published version, changes nothing while reviewing, and brings every finding to you before any fix. Not for releasing this toolkit itself.
---

# Mod release review (author-gated)

**Why this review asks before it fixes.** A mod's correctness is judged in the game, by its author.
The agent cannot see in-game behavior. A review that runs after the author has tested in game and
then "fixes" something that only *looks* wrong on paper can publish a broken mod that was working.
Every rule below exists to prevent that.

**Words used here.** *The author* = the user publishing the mod. *Players* = people who install it.
*Author-tested* = behavior the author observed in game for this version.

## The rules

1. **Read-only review.** While reviewing, change nothing in the mod, the game install or the
   package — no fixes, cleanups or "while I'm here". Exports and decompiles for reading go to a
   scratch folder. Reviewer subagents are told not to edit.
2. **Every finding goes to the author, including definite ones.** No silent fixes. No finding is
   dropped because the agent judged it minor.
3. **What the author saw in game outranks the agent's reading of the files — for what they actually
   saw.** If the code reads as X and the author observed Y, the reading is the suspect. But testing
   covers only the behavior observed, on the author's install, with the build they ran (Phase 1). It
   does not cover packaging, dependencies, other load orders or a fresh install.
4. **Nothing is fixed without the author's individual approval.** Suspected findings are confirmed
   one at a time.
5. **A fix to an author-tested area makes that testing stale.** Say so each time, and name the re-test.

## When to run it

**Every release, before it is published.** The author chooses when:
- **before their final in-game test pass.** Approved fixes then get tested by that pass. This is
  usually the better choice: nothing changes after the author's sign-off.
- **after their testing.** Any fix approved then makes that area's testing stale (rule 5).
- **both.** A full review before the final pass, then a short re-check of only what changed since.

Recommend running it before the final pass, and ask which the author wants.

## Two kinds of finding

| Kind | Means | Examples |
|---|---|---|
| **DEFINITE** | provable from the files alone; no in-game behavior involved | a script fails to compile; a master the plugin needs is not a stated requirement; a file the installer names is missing; an asset that must not be published is inside the package |
| **SUSPECTED** | depends on how the game behaves at runtime | a timing, condition, property or record that *looks* wrong |

Use these two words, consistently, in everything shown to the author.

## Phase 0 — baseline: what players already have

Scope = **the difference between what was last published and what will be published now.** Never
the whole mod.

Baseline, in order of preference:
1. The release record of the previous version (Phase 6 writes it).
2. **The archive players actually downloaded** (the Nexus file). The published bytes are the
   baseline, not the development files or a memory of them.
3. A git tag, if the mod lives in a repo and the tag is known to match what was published.
4. **None of these** (a first release, or nothing was kept) → the whole package is in scope.

Tell the author which baseline you used and how much changed, and **get them to confirm it before
reviewing.** A wrong baseline quietly shrinks or inflates the review.

How to diff each file type:
- ESP/ESM/ESL: Spriggit YAML of both sides (`bash tools/spriggit-cli.sh`).
- `.pex`: Champollion decompile of both sides. The shipped `.pex` is what players' games run, not the `.psc`.
- Text (`.psc`, INI, JSON, FOMOD XML): diff directly.
- Binary with no text form (NIF, audio, DLL): changed = hash differs. Review that file through its tool.

## Phase 1 — ask what the author tested, before judging anything

Ask, in one short message:
- which features they tested in game for this version, and what they did not
- **which build they tested.** Is it byte-identical to what will be published? Testing a development
  plugin, or loose files that will ship packed in a BSA, does not test the published bytes
- anything they already know is rough

Record the answer as the **author-tested set**. Tag each changed area tested or untested.

## Phase 2 — read the changes

Read every changed or added file **in full**, for context. **Findings are scoped to the changed lines
and what they affect.** Do not hunt through unchanged files. Something wrong in unchanged files that
you notice anyway is reported as **PRE-EXISTING**, listed separately, and does not block the release:
players already have it, so holding the release does not fix it. **Exception:** an old defect that
this release makes players REACH — through a new feature, a new default or a new line in the
description — is treated as in-diff.

## Phase 3 — Skyrim checklist (check the rows the diff touches)

| Check | How |
|---|---|
| Shipped `.pex` matches the `.psc` it claims to be | Champollion-decompile the shipped `.pex` and compare with the source |
| Scripts compile | Caprica, with the command in the `skyrim-context` skill |
| **Did the author's testing exercise THIS mod's record?** | `bash tools/skyrim-winner.sh winner <formid>` on the author's load order. If another plugin wins it, their test ran the other plugin's version |
| Dependencies another install may lack | the plugin's masters; `GetFormFromFile` on another mod's forms (returns `None` silently when that mod is absent); SKSE plugins and frameworks at a VR-compatible build and minimum version. **The author's own install has all of these, so their testing cannot catch this class** |
| Properties | non-auto properties stay blank after load; added, removed or renamed properties against the plugin's VMAD |
| Save compatibility for players mid-playthrough | new masters; removed scripts, forms or quests; changed property sets → verdict SAFE / RISKY / DON'T |
| Plugin structure | ESL FormID range, FormID reuse, ONAM for ESM overrides |
| Package contents | file list and hashes against baseline + diff; loose files vs BSA; FOMOD paths resolve to the intended `Data/` layout |
| Content that must never be published | the author's private or development plugins, and assets they have no right to redistribute. Ask the author for their list once and record it |
| Leftover development scaffolding | diagnostic logging, disabled test functions, unused globals |
| Meshes | cross-read with PyNifly (never PyFFI alone); no PreWEAPON/PreSHIELD nodes (VR) |
| SKSE DLL | VR build; built from the source that was reviewed; Address Library dependency stated |
| Claims | version string, changelog and the Nexus description match the files; every number re-derived |
| Permissions and credits | every asset from another author: permission status and credit line |

Label each finding's evidence **MEASURED / READ / INFERRED / ASSUMED**. Any live check in the running
game (the `devbench` skill) happens only if the author offers a session.

## Phase 4 — bring findings to the author

Write the complete findings list first, with nothing applied. Then present it:

**DEFINITE findings** may be shown together as one list. Each item still gets its own answer from the
author. Nothing is fixed because "they were all obvious".

**SUSPECTED findings: one at a time.** One question per finding, never combined. Each one states:
- what and where (file and line, or FormID), in-diff or pre-existing, evidence tier
- whether it falls in the author-tested set, and the direct question: **"Have you tested this in
  game? Can you vouch for it?"**
- what the agent thinks happens, **that it is not certain**, and the cheapest way to settle it (a
  log line, a decompile, a short in-game check)
- **what could break if it is changed**
- options: *Leave it (I vouch for it)* · *Fix it* · *I'll test it first* · *Investigate more*

When a suspected finding is in an author-tested area and the evidence is INFERRED or ASSUMED, the
recommendation is to leave it, and the agent says so.

Every answer is recorded. A finding the author leaves stays left. It comes back only if a later
release changes that code.

## Phase 5 — fixes (only the ones the author approved, one by one)

- Back up the affected files first.
- Smallest change that does it. Rebuild (compile, deserialize). Diff against the backup and
  **confirm that only the intended lines moved.**
- **The fix is itself new, unreviewed change.** Review it before it ships.
- **Verify the fix yourself first, and save the author's time for what you cannot check.** In order:
  offline checks (compile, decompile, `skyrim-winner`, Spriggit diff, PyNifly), then a live check
  through the `devbench` skill if the game is running and the author allows it, within any limits
  the author has set on live game automation.
- Whatever only the author can confirm in game goes on **one re-test list, run as a single session
  at the end**, not one launch per fix. Tell the author which tested area each fix touched.

## Phase 6 — publish gate and the next baseline

Nothing is published until **every finding has the author's recorded answer** and the author gives
the final go. Then:

1. Build the package and hash its actual contents. Those hashes must match the reviewed files.
2. Write the release record in a folder **outside `Data/` and outside the package**, the same place
   every release (ask the author once). It holds:
   - `SHIPPED-MANIFEST.json`: every packaged file with sha256 and size, status `shipped`. Once the
     package is handed over, **treat it as published**: record it as the current version and the next
     baseline without waiting for the author to confirm the upload. The next Phase 0 checks it
   - the reviewable sources: `.psc`, Spriggit YAML per plugin, INI/JSON, FOMOD XML
   - `REVIEW-RECORD.md`: the baseline used, the author-tested set, every finding with its answer
3. A clean review is necessary, not sufficient. The upload itself is the author's step.

Skipping step 2 makes the next release's review start from a download or a full read.

## Red flags

- About to edit anything during the review → rule 1.
- "It's definitely broken, I'll just fix it" → present it and wait for the answer.
- Dropping a finding because the author "probably tested that" → present it, marked author-tested.
- Two suspected findings in one question.
- Treating the author's testing as cover for a dependency, packaging or save-compatibility finding.
  Their install is not a player's install.
- Reviewing the whole mod without telling the author there was no baseline.
- Calling something wrong on a code read when the author has seen it work in game.
