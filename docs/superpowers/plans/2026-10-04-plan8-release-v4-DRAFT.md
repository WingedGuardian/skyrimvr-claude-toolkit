# Plan 8 (DRAFT): the v4.0.0 release, "Skyrim AI Assistant Toolkit", and the final manual batch

**Status:** DRAFT, 2026-10-04. Not approved, not started. Written from: the spec
(`docs/superpowers/specs/2026-09-30-universal-agent-support-design.md`, §8, §10 phase 9, D1, D3, D13), the roadmap
(`C:/Users/user/.claude/plans/looks-good-lovely-fern.md`, row 8 and the FINAL MANUAL BATCH block), the Plan 2/3
close-outs, the plan4 ledger, the plan5 ledger (in flight), and the release machinery as it stands on master
(`.github/workflows/release.yml`, `tools/verify-release.sh`, `setup.sh`, `README.md`, `CHANGELOG.md`).

**Hard rules for the whole plan (user's global rules):**
- **Never push, tag, publish, or rename the GitHub repo without the user's explicit go-ahead.** Every step that
  needs the user is marked **[USER]**. Everything else is local and reversible.
- A release needs the `release-review` skill (a FULL review of everything changed since `v3.9.1`), the
  cold-clone check of the archive, and the E2E. A clean review is necessary, not sufficient.
- CHANGELOG: one issue per line, never combined.
- Commit small and often on `master` (after the merges). Do not edit the tree while a review, a mutation run or a
  suite is executing.
- Codex work is Windows-only (setup says so); POSIX legs cover the shared engine and the Claude target.

**Inputs not yet landed (this plan cannot start before them):** Plan 4 (branch `plan4`, final review done, two
Important items open), Plan 5 (branch `plan5`, mid-flight at Task 7b), Plan 6 (Layer 2, `skydoctor`, M-L2 spike;
not yet written), Plan 7 (`ADAPTING.md`, toy agent, universal setup prompt; not yet written). Anything below that
names Plan 6/7 content is a contract to be re-read against their close-outs, not a fact.

## Order of work (the spine)

```
A  merge plan4 -> master                      (T1-T2)
B  merge plan5 -> master                      (T3)
C  re-verify every docs claim against the MERGED engine   (T4)
D  Plan 6 merge, Plan 7 merge                 (T5)           [USER: only if M-L2 needs them in-session]
E  release code (setup --agent, paths env, migration, rename-in-tree, release machinery, docs)   (T6-T12)
F  release-review of v3.9.1..HEAD             (T13)
G  merged-revision gates, archive from committed objects, cold-clone + scrubbed-env check, automated E2E   (T14-T16)
H  FINAL MANUAL BATCH prepared (Claude) -> run (USER) -> Phase C + analysis (Claude)   (T17-T19)
I  readiness report -> USER GO-AHEAD -> rename, push, tag (USER-gated) -> verify published   (T20-T22)
```

A fix found at any later stage sends the work back to the earliest stage whose evidence it invalidates (a code
change after F re-opens F for the delta and re-runs G; see T19).

---

## Phase A/B: merges

### T1 Pre-flight inventory (Claude, read-only)
- Record `git rev-parse` of `master` (39aee7e at drafting), `plan4` (106d9e7), `plan5` (058b0e2 and moving).
  **plan4 branched from master's tip (39d2c1c); plan5 branched from an older master (0704791)**, so plan5 does not
  contain Plan 3's merge or the harness repairs. The merge order is therefore plan4 first, then plan5, and plan5's
  conflicts are with plan4's files, not with master's.
- Confirm both worktrees (`../skyrimvr-toolkit-plan4`, `../skyrimvr-toolkit-plan5`) are clean, the plan5 ledger says
  all its tasks are complete and its final review is done, and no other process is editing either tree.
- Copy both ledgers' deferred lists into the table at the end of this file (re-read; the table below was built from
  them at drafting time and plan5's ledger was still growing).
- **Gate:** plan5 final review closed. If not, Plan 8 waits; nothing in this plan fixes plan5.

### T2 Merge plan4 into master (local only)
- `git merge plan4` on master (a merge commit, history kept; the repo's convention, see `bfe1902`, `a87ce27`).
  Expected conflicts: `CHANGELOG.md` (both have `## Unreleased (v4.0.0 in progress)`), `README.md`, `setup.sh`,
  `.github/workflows/*`, generated files (`AGENTS.md`, `CLAUDE.md`, `.codex/**`, `.agents/**`). **Resolve sources
  (`agent/**`, `CHANGELOG.md`, `README.md`), then regenerate** (`python scripts/gen_agent_trees.py`) and let
  `--check` prove the generated files; never hand-merge a generated file.
- Re-verify the four plan4 **merge-risk** items from its ledger (they concern plan5, so they are re-checked in T3,
  but record here that the plan4 half is in place): the `DENY_WRITE_PLUGIN` classification row, the R5 subset
  (`.rules` prefix `reg delete` vs the hook deny), `engine.evaluate_write` taking `req.cwd`, the one-code-per-row
  check for changed `DENY_PSC`/`DENY_CHAMPOLLION` text.
- Focused checks only at this step (generator drift, codex freeze gate vs the last tag, conformance, the
  instruction budget). The full merged-revision run is T14.
- Commit: `Merge plan4 (Codex target) into master`.

### T3 Merge plan5 into master (local only)
- `git merge plan5`. Expected conflicts: `tools/skyguard/*` (both rewrite `shell_rules.py`, `write_rules.py`,
  `engine.py`), `tests/guard_corpus/*` (expected verdicts differ by design: plan5 changes behaviour after Plan 2's
  replay certified equivalence), `.claude/settings.json` + its generated source (matcher `Bash|PowerShell`),
  `.codex/rules/skyrim.rules` and its generator, `CHANGELOG.md`.
- **Resolve the plan4 merge-risk list explicitly, each with evidence:**
  1. `DENY_WRITE_PLUGIN` has a classification row (plan4's "every shell verdict classified" test) and the
     `codes` one-code-per-row check passes with plan5's changed `DENY_PSC`/`DENY_CHAMPOLLION` text.
  2. **R5:** plan5 narrows `DENY_REGISTRY` to Bethesda keys. The `.rules` file may only hold prefix rules that are
     strict subsets of a hook deny; a `reg delete` prefix rule broader than the narrowed deny must be narrowed or
     dropped, and the subset test must go red on a deliberately broadened rule (mutation).
  3. `write_rules.decide(roots, cwd, env)` is reached from `engine.evaluate_write` with `req.cwd` on **both** the
     Claude and Codex paths (one call site; a test that a relative delete resolves from the payload cwd).
- plan5's `fa4d0e1` has a `wip` commit message; **do not rewrite history**. The release review reads it as is.
- The plan5 timing guard (quadratic regexes, Plan 2 regression) must be present and green on the merged tree.
- Commit: `Merge plan5 (rules and noise) into master`.

---

## Phase C: documentation claims against the merged engine

### T4 Claim audit (the "docs claim" gate)
Every sentence in README, `AGENTS.md`/`CLAUDE.md` (generated; fix the `agent/` source), `docs/*.md`, the codex
addendum and the CHANGELOG that states what the guards do is turned into a **probe**: a real request through the
real entry point (`tools/skyguard/run.py` via the shipped hook wrapper, with a Claude and a Codex payload), with
the claim, the probe, the observed verdict and the verdict-per-agent recorded in
`docs/superpowers/measurements/2026-10-XX-plan8-claims.md` (use the real date). The point of the table is that each
claim is checked against the engine that ships, not against the engine that existed when the claim was written.

Required rows (not exhaustive; enumerate the whole class, per the project's "samples, not the spec" rule):
- **`codex.md` / `AGENTS.md` ~l.353-357 "deleting in the game or config folders is refused"** (`plan4:AGENTS.md`).
  The plan4 final review found this false for relative and PowerShell forms; it becomes true only with plan5's
  cwd resolution (Task 7). Probe: `rm Data/x.esp`, `Remove-Item .\Data\x`, `ri`, `del`, `rd /s`, from a payload
  cwd of the game root, of `Data/`, and of an unrelated folder; absolute and relative; Bash and PowerShell
  dialects; Claude and Codex payloads. Rewrite the sentence to exactly what the table shows. Keep the E22 caveat
  (Codex's built-in policy may reject a delete before any hook runs) out of the pass criterion.
- "Writing a plugin or archive directly is refused": Edit/Write tools, `apply_patch` add/update/delete/move,
  `> file.esp`, `tee`, `Set-Content`, `Add-Content`, `Out-File`, `[IO.File]::Write*` (D13/E21: the README must stop
  saying "hard blocked" for anything the table shows as advise or ask).
- Copy/move onto a plugin is **ask** on Claude and **deny** on Codex ("run it yourself if intended").
- MO2 `mods/` and `overwrite/` deletes (E13), DevBench console deny-list through MCP and `curl ...:8920|:8921`.
- E16 control: a read-only `grep` whose pattern contains deletion words and the install path is allowed.
- "ReSaver `--apply` asks": prompt wording is at most two plain lines and does not echo the command (user rule
  2026-10-02), on every ask and advise message; on Codex it becomes deny.
- **The Codex startup limit found in plan4 Task 14 (undocumented until now):** in a **non-git** install, starting
  Codex from a subfolder (`Data\Scripts`) loads **no hooks** and fails open; the canary reports NEVER INVOKED. The
  docs must say "start Codex in the game folder". (Verify the claim again on the merged build in the manual batch,
  M1 step 7.)
- The hook-count/"live" banner text (`SKYGUARD: Codex hooks live`), the 32 KiB limit, the frozen-definition
  "re-approve" rule, the `/hooks` approval step, the heartbeat/canary state names.
- A claim that cannot be probed is reworded as a statement of intent ("is designed to") or removed; it is never
  left standing.
- **Gate:** zero rows where a doc claims more than the probe shows. Any engine gap found here is not patched in this
  plan unless it is a one-line rule fix with a corpus row; larger gaps become a documented limit plus a follow-up
  in the CHANGELOG's "Known limits" (see deferred table).
- Commit: the doc fixes and the claims table, in separate commits.

---

## Phase D: Plans 6 and 7

### T5 Merge Plan 6 and Plan 7 (local only)
Plan 6 (Layer 2 lock, M2, M-L2, `guards off`, `skydoctor`) merges first, then Plan 7 (`ADAPTING.md`, toy agent,
universal setup prompt). Per plan, in this order:
1. Read its close-out and its ledger's deferred list; add them to the deferred table.
2. Merge; regenerate; re-run its claim rows through T4's method (Layer 2 and `guards off` claims are especially
   prone to overstate).
3. **Decision gates that belong to the user [USER]:**
   - **M-L2 verdict.** If (a)-(e) in spec §9 all passed, Layer 2 ships as D5 describes. If it failed, Layer 2 is
     "recovery only" (D5 option B) and README/CHANGELOG say so; the plan does not ship a lock that did not pass.
     The Wrye Bash and Steam-verify parts of M-L2 are user-run and are collected in the FINAL MANUAL BATCH (M2).
     The verdict is therefore made **after** the batch; until then Plan 6's lock is merged but described as
     "pending M-L2" and T12 has a branch for each outcome.
   - If Plan 6 or 7 is not ready when everything else is, whether to release without it is the user's call (spec
     D1/D5 allow "Layer 2 = recovery only"; D7's "partially supported" language covers an unfinished ADAPTING).
     Plan 8 does not decide this.
4. The Plan 6 deferral "agent self-protection (`.codex/**`, `tools/skyguard/**` writable by the agent)" is closed by
   Plan 6 or becomes a documented limit (deferred table).
- Commit: two merge commits.

---

## Phase E: release code

### T6 Deferred items assigned "fix in Plan 8"
Do the items in the deferred table (below) marked **Plan 8 fix**, each with its test and (for guards) a mutation
that turns the named test red. Grouped by where they land so the commits are coherent:
- prefs injection hardening (`prefs.py`): unclosed code fence, `## My Preferences (...)` variant heading,
  bounded read (about 1 MB cap before the 4,096-byte section cap);
- `read_env`/`run.py --root ""` (moves with T8);
- the test-infra pair: env scrub keeps `SystemDrive`/`SystemRoot` (kills the stray `%SystemDrive%/ProgramData/...`
  folder; this is also the same scrub T15 relies on), and the catastrophic-regex test self-terminates in at most 5 s
  or kills its process tree (no orphan spinning child on timeout);
- docs/CI: the two plan docs that break the "Relative links in Markdown resolve" step (red at base c740d97 per the
  Plan 3 close-out; re-check whether it is still red, fix the docs, not the check);
- a `setup.sh` warning when the installed `CLAUDE.md`/`AGENTS.md` would exceed its budget at the user's root length
  (Plan 3: ~8 B per root character, ~34 chars of headroom on a long MO2 root).

### T7 `setup.sh --agent claude|codex|all` (+ `auto`)
**Spec/roadmap say `claude|codex|both|auto`; this plan's brief says `claude|codex|all`.** Implement the values
`claude`, `codex`, `all` and `auto` (every agent found on PATH), keep `both` as an accepted alias of `all`, and
update the spec line in the release commit so the three documents agree. Default with no flag: `auto`; if nothing
is found on PATH, `all` (an installed-but-unlaunched agent must not be left unprotected).
- `setup.sh` remains the only installer, one script, no second code path per agent.
- Per-agent work is conditional: `claude` = `.claude/` hooks + `settings.json` placeholders + `CLAUDE.md`;
  `codex` = `.codex/hooks.json` render, Codex entries' Python path, `AGENTS.md`, `.agents/skills`; `all` = both.
  **Not-selected agent files are not deleted** (the zip ships both trees); setup says plainly that the other agent
  has no filled-in paths here and its hooks are not configured, so it will deny/refuse rather than run unguarded.
  The "unconfigured Codex = no hooks, runs unguarded" hazard (an unrendered `{{CODEX_ROOT}}` template) must fail
  closed or be loudly flagged by `skydoctor`/canary; test it.
- Codex support is Windows-only: `--agent codex` on a non-Windows root exits non-zero with the reason.
- It **never marks a Codex project trusted**; it prints the `/hooks` approval step (existing plan4 behaviour,
  kept) and that the user must start Codex **in the game folder**.
- `--agent` unknown value -> usage error, exit 2, nothing written. Re-running is idempotent (existing test class;
  extend it per value). Refuse `%`, `!`, `$`, backtick, `"` in the install root (plan4 behaviour, kept).
- Tests: setup-smoke for each value (CI job `setup-smoke` grows a matrix), idempotence, partial-state repair,
  mutation per branch.

### T8 `skyrim-paths.env` moves to the toolkit root
- New location `<root>/skyrim-paths.env`; carries `SKYRIM_MO2_MODS`, `SKYRIM_MO2_OVERWRITE`, `SKYGUARD_PYTHON` in
  addition to today's keys (spec §4/§8). **Single reader, `tools/skyrim_paths.py` and the engine's `paths.read_env`,
  both prefer the new path, fall back to `.claude/skyrim-paths.env` with a deprecation notice** (printed by
  setup and the session-start hook, once per session, not per call).
- About 31 references on plan4 (`git grep -n "skyrim-paths.env"`): enumerate them all, including `protect-bash.sh`
  (resolves roots), `kb-guard`, the canary, the two entry wrappers, `tools/*.sh`, README, the claude/codex addenda,
  `.gitignore`/release excludes (machine-local: not tracked, not shipped; the release job must assert the file
  is **not** in the payload, like `KNOWLEDGEBASE.local.md`).
- **Both present, disagreeing:** new wins, setup says so. **Neither present:** the existing no-root-resolvable ->
  DENY rule stands. `run.py --root ""` no longer reads a cwd-relative env (deferred item).
- Update `read_env`'s comment about shell-sourcing differences (deferred Plan 2 item).
- Tests: root resolution from each location, deprecation notice once, the claim-audit rows for the new path,
  mutation (a reader that ignores the new location).

### T9 3.x -> 4.0 migration for existing users
Existing 3.x users update by **extracting the zip over their install** (the documented path). That overwrites
`CLAUDE.md`, `.claude/settings.json`, hooks, skills and `KNOWLEDGEBASE.md`, so migration is about what the user
may have customised in files the release now owns. Build it as `tools/migrate-3x.py` (or inside setup) with tests
on **fixtures built from every shipped tag** (`v3.8.3` ... `v3.9.1` and the 4.0 candidate):
- **Hash list:** a table of the SHA-256 of every shipped `CLAUDE.md` (post-setup rendering is path-dependent, so
  hash the *template* and compare against the user's file with their root placeholder-normalised; document the
  method). An installed `CLAUDE.md` matching no known version is copied **verbatim** into a marked block at the
  end of `KNOWLEDGEBASE.local.md` (created if absent) with sorting instructions ("move lasting preferences up
  into `## My preferences`"). **Never deleted, never guessed at.** Do the same for a user-edited `AGENTS.md`.
- **Untouched by migration:** `.claude/backups/**`, `.claude/settings.local.json`, `KNOWLEDGEBASE.local.md`
  content outside the marked block, KB files.
- **`settings.json` customisation:** a 3.x `settings.json` with user hooks or permissions the release overwrites:
  detect (not equal to any shipped `settings.json` template) and save a copy to `.claude/settings.json.3x-backup`
  before the extract's version is used; say so. (Verify what the extract-over path actually does to this file; if
  extraction already overwrote it by the time setup runs, the only recoverable copy is the one in the user's own
  backups, so the **README update steps must say "run `bash tools/pre-update-snapshot.sh` first"** or equivalent:
  make this a tiny script that copies the user-owned files before they extract.)
- **Idempotent:** running setup twice does not duplicate the marked block (key it by the content hash).
- **Frozen Codex definitions:** a 3.x user has none, so there is nothing to re-approve at 4.0; the release's
  `hooks.json` defines v1. Record this in the CHANGELOG ("Codex hooks v1: approve them in /hooks") and in the
  freeze gate's baseline (T11).
- **Upgrade-from-3.9.1 test** (spec §7.7): extract the 4.0 archive over a real 3.9.1 install fixture containing a
  customised `CLAUDE.md`, a user `settings.local.json`, backups and a populated `KNOWLEDGEBASE.local.md`; assert
  the table above byte for byte. This test runs in the scrubbed environment (T15) too.

### T10 Release machinery: built from committed git objects, verified in a scrubbed environment, CI on both platforms
Audit items from the roadmap (Codex audit) turned into concrete changes:
1. **One build script, `scripts/build-release-zip.sh <rev> <outdir>`**, used by `release.yml` AND by the local
   pre-release build. It builds from **committed objects** (`git archive <rev>` or `git ls-files -z | tar`), never
   from a working-tree `rsync`: untracked files, ignored files and uncommitted edits can then never reach the zip.
   Replaces the current `rsync -a --exclude` of the checkout. Because the old comment notes `.gitattributes
   export-ignore` was inert under rsync, either use it deliberately under `git archive` or keep explicit excludes;
   pick one and add the test that `tests/`, `.github/`, `.superpowers/`, `.skyguard/`, any
   `skyrim-paths.env`, `KNOWLEDGEBASE.local.md` and `.claude/backups` are absent. The zip root directory is
   renamed (T11). Add a **dirty-tree tripwire**: the script refuses (exit non-zero) when `git status --porcelain`
   is non-empty, or when `<rev>` is not an ancestor-or-equal of HEAD, unless `--allow-dirty` (CI never passes it).
   Record the commit SHA inside the zip (`BUILD-INFO` at the root, shipped) so the verifier can prove which
   commit the artifact came from.
2. **Payload assertions move out of workflow YAML into `tools/check-release-payload.py`** (plan4 already added
   `tools/check-codex-payload.py` and calls it from release.yml and verify-release.sh; fold the existing bash
   asserts in release.yml: hook floor, bare `cat` stdin line, heartbeat, canary, kb-guard, skyguard `run.py`,
   KB local absent, tests absent, the new paths-env absent). Both the workflow and `verify-release.sh` run the
   same checker. Positive assertions, not blocklists (the file's own history says why). **Update the hook
   floor** (5 shell hooks today; recount from the merged tree), and make sure the Codex entries (`.codex/hooks/*`)
   and `.codex/hooks.json` are counted/asserted as well as `.claude/hooks`.
3. **Scrubbed-environment archive verification** (`tools/verify-release.sh` gains `--local <zip>`, and
   `--scrubbed`): extract into a fresh directory with a fake `SkyrimVR.exe` and run, with the environment
   cleared (`env -i` on POSIX; an explicit minimal env on Windows that **keeps `SystemRoot`, `SystemDrive`,
   `ComSpec`, `PATHEXT`** so the harness itself does not create stray folders, see deferred T4 item) and
   `HOME`/`USERPROFILE`/`APPDATA` pointed at an empty temp directory: no author machine paths, no
   `X4_NEXUS_KEY`, no `PYTHONPATH`, no inherited `CLAUDE_*`/`CODEX_*` vars, no repo checkout on `PATH`:
   `setup.sh --agent all`, `skydoctor`/`hook-canary`, the guard corpus smoke, `gen_agent_trees --check` from the
   extracted tree (installed-copy path), `tools/skyguard` selftest, a payload through each entry wrapper, the
   instruction-size check on the installed files. **Tripwire for the scrub itself:** a canary variable
   (`PLAN8_LEAK_CANARY=1`) is set in the parent and the verifier asserts it is invisible inside; and the
   verifier greps the extracted tree and every generated artifact for the author's username/home/`C:\GOG Games`
   strings and for the machine's real Skyrim path.
4. **CI runs Windows + POSIX suites, with tripwires.** Today's `behavior` matrix (`ci.yml` job
   `behavior`, `matrix.os`) runs two legs, and the Codex harness-barrier modules run separately. Required here:
   (a) the **same** merged-revision suite on `windows-latest` and `ubuntu-latest`; (b) a **skip ceiling**
   (a recorded number of allowed skips; a leg that skips more than the ceiling fails; existing
   `test_skip_ceiling` pattern); (c) a **tripwire per leg**: each leg first runs a test that fails if the
   platform-specific tests collected for it are fewer than expected (so a leg that silently runs nothing goes
   red), and the harness-contained modules run on both legs (the ledger notes some modules are skipped under the
   main run and run separately, 33 pass; wire them as a required step, not optional); (d) `fetch-depth: 0`
   on every job that runs the freeze gate (needs the last tag); (e) the plan4/plan5 timing guard and the
   catastrophic-regex test run with a CI-level timeout so a hang is a red job, not a stall; (f) the generated-
   files drift gate, the `AGENTS.md` byte budget, the neutrality gate, `setup.sh --agent` matrix (T7), cold install,
   upgrade-from-3.9.1 (T9), and the toy agent from Plan 7.
   CI cannot prove the live-agent parts; those stay local (T16) and in the manual batch.
5. **Freeze gate baseline.** The spec's frozen `.codex/hooks.json` gate compares against "the last tagged release".
   `v3.9.1` has none. The first tag with Codex hooks is `v4.0.0`, so the gate's baseline for that release is the
   `FROZEN.sha256` in `agent/targets/codex/`, with the "tagless release" check plan4 added. After the tag exists,
   the next release compares against `v4.0.0`. Test both branches.
6. **Workflow safety:** `release.yml` keeps `ref: ${{ inputs.tag || github.ref }}`; add a check that the tag's
   commit is on `master`, that `CHANGELOG.md` has a section for the version, and that `BUILD-INFO` equals the
   tag's SHA. The workflow never runs on a tag the user did not push.

### T11 The rename, in the tree only
(The GitHub repo rename is T22, gated by the user.) Rename inside the repo, in one commit:
- Product name "Skyrim AI Assistant Toolkit" and the plain statement, in README line 1-3, the generated
  instruction files' headers, `package.json`, `setup.sh` banner, `SETUP_PROMPT.txt`, the Nexus pack script, the
  zip/root name `skyrim-ai-assistant-toolkit-<version>`: **"for coding agents (Claude Code, Codex, ...), not an AI
  NPC mod (SkyrimNet, Mantella, CHIM)"** (D3). `git grep -n -i "skyrimvr-claude-toolkit"` finds 19 files at
  drafting (`tests/test_hooks.py`, `scripts/build-nexus-pack.sh`, `tools/verify-release.sh`, the guard-corpus
  reference, `package-lock.json`, ...); classify each as rename / keep (a frozen reference or history).
- `TOOLKIT_REPO` default in `verify-release.sh` and every link: GitHub redirects old URLs after a rename, but a
  workflow/`gh` call made **before** the rename must still use the old slug and **after** it the new one.
  Make the slug a single variable with the new name as default **only in the release commit that is tagged**; the
  rename itself (T22) happens first, then the push, so the tagged tree never refers to a repo that does not yet
  exist under that name. Test: no file in the built zip contains the old slug except in the CHANGELOG/history
  lines marked as such.
- `SETUP_PROMPT.txt` becomes the **universal setup prompt** from Plan 7 (agent-neutral; "install into this folder,
  run `bash setup.sh --agent auto`, ..."). Do not write a second one; this task wires the file and the README
  snippet to Plan 7's text and adds the test that README's copy equals the file.
- Anything that embeds the old product name in the author's personal install is out of scope (spec non-goal).
- Frozen things must not move: **the frozen Codex hook definitions do not include the product name or repo name**
  (the freeze gate must stay green after the rename; add a test that renders the definitions and compares).
- Record the Nexus title/description text (a draft file in `scripts/build-nexus-pack.sh`'s output): the first line
  states "for coding agents, not an AI NPC mod". The Nexus upload is the user's manual step (T22).

### T12 README and CHANGELOG
- **CHANGELOG:** collapse the several `Unreleased (v4.0.0 in progress)` sections (master has one; plan4 and plan5
  each add theirs) into a dated `## v4.0.0` section, in the repo's existing order, **one issue per line**. Required
  entries, each its own line:
  - security fixes to the 3.x guards named as such (D13): the **PowerShell matcher** (E12: the shipped matcher
    was `Bash` only) and the **shell-write-to-plugins rules** (E21); the 3.x "hard blocked" wording corrected;
  - the release is the first to include: the Codex target; the renamed product; `setup.sh --agent`; the moved paths
    env (with the deprecation); the instruction split and the three new skills; the preference injection; Layer 2
    (per the M-L2 outcome, one branch per outcome); `skydoctor`; `ADAPTING.md`; hook latency measured; the
    quadratic-regex fix (a bug that existed only in the unreleased engine: **say so**, do not list it as a 3.x fix);
  - "**re-approve your Codex hooks**" does not appear in 4.0.0 (nothing to re-approve from 3.x); the line for
    4.0.0 is the first-approval step instead;
  - the migration behaviour (T9) and the pre-update snapshot step;
  - **Known limits**, one per line (see the table's "document as known limit" rows, plus: Codex hooks fail open on
    timeout/crash/missing script; `ask` does not exist on Codex; non-git install + subfolder start; the agent can
    edit `.codex/**` and `tools/skyguard/**`; other surfaces (desktop/IDE) are Layers 0-2 only unless probed, D14).
  - Numbers in the CHANGELOG and README are re-derived at write time from the merged tree (byte sizes, hook counts,
    corpus rows, test counts). **Never quote a count that was not summed from data this session.**
- **README:** retitle, tagline, an agent support table (Claude Code: full; Codex CLI on Windows: full with the
  approval step; other agents: Layers 0-2, plus `ADAPTING.md`), the "start Codex in the game folder" note, a
  "what an unknown agent lacks" paragraph (spec §3), the update procedure (pre-update snapshot, extract over,
  re-run `setup.sh`), the migration notes, a section on Layer 2 that matches the M-L2 outcome, the known limits,
  and the surfaces policy. Re-run T4's claim table over the final text.
- Docs inside `docs/` that name `.claude/skyrim-paths.env` or the old repo are updated.

---

## Phase F: release review

### T13 `release-review` of everything since v3.9.1
**Invoke the `release-review` skill first** (scope rules, the in-arc vs pre-arc blame test, the coverage checklist);
the user's global rule is a FULL review of everything the release **changes**, every commit since `v3.9.1`, every
file the range adds or modifies **read in full**. Not scoped to the risky part. Not scoped to what ships beyond
what changes.
- Compute the range: `git diff --stat v3.9.1..HEAD` and `git log --oneline v3.9.1..HEAD` on the merged tree;
  write the file list and per-file line counts to the review ledger. The range is large (Plans 1-7 plus this
  plan); partition by area and dispatch reviewers (model `opus`, correctness-critical per the user's routing
  table), with each partition's reviewer **required to list the files it read in full** so coverage is checkable
  against the ledger. **No file is dropped for size**; generated files are reviewed by proving the generator drift
  gate and reading the generator and sources, plus a byte-level diff of generated vs regenerated.
- Partitions (adjust to the real file list): (1) `tools/skyguard/**` engine and rules; (2) adapters, entry
  wrappers, frozen definitions, `.rules`, setup.sh and migration; (3) Plan 6 (lock, `skydoctor`, `guards off`) and
  Plan 7 (`ADAPTING.md`, toy agent); (4) instructions, skills, README, CHANGELOG, docs (read as text a user acts
  on; the claims table from T4 is an input, not a substitute); (5) tests, corpus, fixtures, mutation gates, CI,
  release machinery; (6) the pre-arc blame test for anything that touches a pre-existing defect (a defect the
  release did not introduce is a known limit, not a hold).
- Fixtures: captured payload fixtures were scrubbed of user paths; the review re-runs the leak check across every
  spelling (JSON `\\`, forward-slash, MSYS `/c/Users/...`) over the committed objects, not the working tree.
- Mutation gate: the plan4 ledger notes the full ~247-mutant gate was killed at about 37% by a 2 h cap and must be
  run **in chunks** at the final review; plan5 adds 401 mutations on its own. Run both full sets chunked, baseline
  green first, record killed/survived per chunk with denominators; a survivor is a finding.
- Classify findings Critical / Important / Minor. Critical and Important are fixed before the release; Minor is
  fixed or lands in the deferred table with an assignment. Every fix after this point re-opens the review for the
  delta (T19).
- **A clean review is necessary, not sufficient:** it does not replace T14-T16, or the manual batch.
- Output: `docs/superpowers/plans/2026-10-XX-plan8-release-review.md` (real date), listing coverage and findings.

---

## Phase G: gates on the merged revision

### T14 Merged-revision suites, both platforms
- Full pytest suite on the merged, **committed** revision, in a fresh local clone (the suite takes more than 30
  minutes here; per the Plan 3 ruling run in `scratchpad/suite-<sha>`, never against a tree being edited),
  **Windows and POSIX**: Windows locally (Git Bash + PowerShell) and POSIX through the CI `ubuntu-latest` leg
  after the user lets it run (a push is needed for that, so before the user's go-ahead the POSIX leg runs in
  the repo's Docker devshell, `devshell-docker.sh`, and CI gets its turn at T21's push to a branch only **if the
  user okays a branch push**, not before). Record counts and skip counts with denominators.
- Harness-contained modules (Codex harness repairs) as a separate required step.
- Baseline from `v3.9.1` (same method, same machine) so a **regression** is visible as a number, not a feeling.
- Test-count claims in README/CHANGELOG come from this run.
- Instruction budgets: `gen_agent_trees.py --budget` on a long-MO2-root fixture, for both `CLAUDE.md` and
  `AGENTS.md`.
- Latency (M4): the latency gate from the guard-latency script on the merged build, both agents, p95 <= 1 s,
  interleaved A/B against v3.9.1's hooks; record the load level (the earlier runs at 73-79% load were noisy).

### T15 Archive from committed objects, cold clone, scrubbed environment
- Build the candidate with `scripts/build-release-zip.sh <HEAD sha>` (T10.1) on a clean tree.
- `bash tools/verify-release.sh --local <zip> --scrubbed` (T10.3): payload shape, hook inertness checks,
  Codex payload check (`tools/check-codex-payload.py`), BUILD-INFO SHA, leak greps, setup in a scrubbed env for
  `--agent claude|codex|all`, idempotent re-run, upgrade-from-3.9.1 (T9), `skydoctor`/canary, size gates.
- **Cold clone:** also `git clone` the repo at HEAD into a fresh directory (not a worktree), build from the clone,
  and prove the zip is **byte-identical in file set and content** to the one built from the working repo
  (hash every member). A difference means the build depends on something outside git.
- Mutation on the checker itself: inject (a) a stray untracked file into the tree, (b) a missing hook, (c) a
  leaked home path, (d) `.codex/hooks.json` with an unrendered placeholder, (e) a `tests/` entry; the named check
  must go red for each (and the baseline must be green first).
- Output: a verification record in `docs/superpowers/measurements/2026-10-XX-plan8-archive-verification.md`.

### T16 Automated live E2E (Claude-driven, no user)
Run from the **candidate archive** extracted into a scratch install (not the dev tree), per spec §7.5:
- `claude -p` and `codex exec`, in Git Bash and PowerShell, against the scratch install: a decoy `.esp` write, a
  delete in the install, a delete in MO2 `mods/`, a DevBench `smp reset`; plus the PowerShell-tool and `apply_patch`
  forms; **non-delete probes wherever possible** (Codex's built-in policy can reject a delete before the hook runs,
  E22).
- Backups and snapshots happen; session-start checks appear; `skydoctor` reports untrusted Codex hooks; **the hooks
  unreviewed case** (probe with unapproved hooks: the `.rules` forbidden rules still hold, E19); `guards off` ->
  action -> session restart relocks (Plan 6).
- Re-measure E1/E2 fail-open behaviour on the **current** Codex version (fail-open may change in either direction,
  spec §12); record the Codex version; if it changed behaviour, the docs claims (T4) are re-checked.
- The existing scripts (`scripts/e2e-codex.py`, `scripts/e2e-codex-collect.py`) are the harness; extend, do not
  rewrite. Hook latency from the hook logs (M4).
- **Phase C of plan4's Task 14** (Codex exec with *approved* hooks, no bypass flag, plus the definition-change
  re-approval check) **needs the user's approved hook state in `~/.codex/config.toml`**, so it is part of the
  manual batch (T19), not here.
- Output: `docs/superpowers/measurements/2026-10-XX-plan8-e2e.md`, with every probe's observed output.

---

## Phase H: the FINAL MANUAL BATCH

### T17 Prepare the batch (Claude; the user does nothing)
Everything that can be prepared is prepared first so the user's session is one sitting and read-only for the
collectors:
- **Rebuild the scratch fixtures from the CANDIDATE archive.** Plan4's prepared scratch folder is from an older
  build (it tests plan4's payload, not the merged release); it is discarded and recreated from the archive built
  in T15. Same layout (a throwaway release-style install with fake game files: empty `SkyrimVR.exe`,
  `Data/Existing.esp`, a sentinel `KNOWLEDGEBASE.local.md` with `PREF-SENTINEL-6611`), already set up by
  `bash setup.sh --agent all`.
- One consolidated instruction file `docs/superpowers/measurements/2026-10-XX-plan8-manual-batch.md`, with the
  exact copy-paste steps below, expected outcomes (what "pass" looks like **and** what would have made it red), and
  where to paste the output. Re-use the plan4 TUI instructions' text where still correct; update every path.
- Collectors (**read-only**: they read the scratch install, rollout/transcript files for that folder and
  `~/.codex/config.toml`/`~/.claude`; they write nothing, edit no config, print one `RESULT:` line and scrub the
  user's home from the output so it can be pasted): `scripts/e2e-codex-collect.py` (existing, copied into the
  scratch folder as `collect.py`), a new `collect-claude.py` for the interactive Claude session, and Plan 6's
  M-L2 collector (hash + `attrib` + `os.stat` only). Each collector is **dry-run against a recorded fixture** (and
  mutation-checked: a rollout with the block missing must make the collector red) before the user sees it.
- Prerequisites checklist at the top of the instruction file (Codex version, Python on PATH, Git Bash, Wrye Bash
  installed, Steam running); a "stop and tell me" list (anything that touches the real install is out of scope).
- **The batch never touches the author's real install** except M2 step 3 (Steam verify), which is the one place the
  real install is involved; it is marked and optional-with-consequence (below).

### T18 [USER] Run the FINAL MANUAL BATCH (one session, about 45-60 minutes)

> **FINAL MANUAL BATCH, as agreed (user ruling 2026-10-04): every user-run test happens here, in one session at the
> very end. Collect all of them here. No user step is asked for mid-stream.**

**M1. Codex TUI run (plan4 Task 14, carried here; about 10 minutes).** *Folder: the scratch install built from the
candidate archive; the exact path is written into the instruction file by T17.*
1. Open a new Windows Terminal PowerShell tab.
   `cd "<scratch>\Skyrim Probe (x86)"` then `codex`. If Codex asks to trust the folder, say yes (the trust entry is
   one of the things the cleanup section lists). Do **not** start Codex in `Data\` or another subfolder (that
   is step 7).
2. `/hooks`. Expect **two** hooks, "Skyrim toolkit: session checks" (SessionStart) and "Skyrim toolkit: checking"
   (PreToolUse). **Approve both.** Note anything else (a warning, a "modified" label, a count other than 2). Close.
3. Say: `Create Data/TuiProbe.esp containing the word probe, using apply_patch.` Approve if asked. **Look for:**
   "blocked by the PreToolUse hook" quoting "Cannot directly write to plugin/archive files". If Codex says it
   created the file, that is the failure under test; note it.
4. Say: `Run Get-Date.` **Look for:** it runs and prints a date (the guard does not block ordinary work).
5. Say: `Run Set-Content -Path Data\TuiProbe2.esp -Value probe in PowerShell.` **Look for:** a block with the
   shell-write-to-plugin reason (E21, closed in this release; the 3.x guard only advised). If Codex's own policy
   rejects it instead, note which message you saw (a different refusal does not prove the hook).
6. Say: `The SessionStart hook injected additional context at the start of this session. Without reading any file
   or running any command, reply with the first line of that hook context and the preference sentinel found in it.`
   **Look for:** a line starting `SKYGUARD: Codex hooks live`, and `PREF-SENTINEL-6611`. (If Codex declines to
   quote it, that is the model, not the toolkit: just note the answer.) Type `/quit`.
7. **Subfolder start (the plan4 finding, 2 minutes):** `cd "Data\Scripts"` (create it if absent), run `codex`,
   type `/hooks`, note what it lists (expect none for this folder), then run `Get-Date` and `/quit`. Then in the
   original folder: `cd ..\..` and continue to step 8. Expected: the collector later shows NEVER INVOKED for that
   session; the README says "start Codex in the game folder".
8. **Collect, yourself, in the same tab (never ask Codex to run this):**
   `cd ..` (to the folder holding `collect.py`), then `python collect.py`. It only reads. Paste its whole output
   back, plus one line each: did step 3 say *blocked* (paste the line if you can)? did anything ask you to approve
   before the block? did step 5 block, and with whose message? what did step 6 answer? anything odd in `/hooks`?
   Then say "TUI done".
9. *(Claude runs "Phase C" afterwards, in the same scratch folder, using your approved hooks: `codex exec` without
   a bypass flag, and the definition-change re-approval check, which edits only the scratch install's
   `hooks.json`. Nothing for you to do; do not close the approvals.)*

**M2. Wrye Bash and Steam verify (Plan 6, M-L2 items (c) and (e); about 20 minutes; steps come from Plan 6).**
*These two checks exist only if Plan 6 shipped a Layer 2 lock; if it did not (or M-L2 already failed earlier), M2
is skipped and the CHANGELOG says "Layer 2 = recovery only".* The exact steps are copied here from Plan 6's spike
document at T17 (a gate: **T17 is not complete while this block still says "TBD"**). The skeleton, pending Plan 6's
wording:
1. On the scratch copy of the vanilla files that Plan 6 built (never the real `Data/`): run the lock
   (`tools/skyguard lock`), then run the Plan 6 pre-check collector (read-only: lists each target's `attrib` and
   `st_nlink` and SHA-256).
2. **Wrye Bash (c):** launch Wrye Bash against the scratch Data, let it scan, build a Bashed Patch, ghost then
   unghost one plugin, and exit. Then run the collector again; it diffs hashes and attributes, and reports what
   Wrye changed or refused.
3. **Steam verify (e):** Steam -> Skyrim Special Edition / VR -> Properties -> Installed Files -> Verify integrity of
   game files, **on the install the lock was applied to** (Plan 6 decides whether that is the scratch library or a
   Steam library copy; verify never runs on a non-Steam copy). Run the collector a third time; it reports whether
   Steam restored, skipped, or failed on locked files, from file hashes plus Steam's own log
   (`Steam\logs\content_log.txt`, read only). **Rollback is recorded before this step:** `tools/skyguard unlock`
   and the Steam verify itself (it repairs vanilla files by definition).
4. Paste the three collector outputs and what Wrye Bash and Steam displayed. Say "M-L2 done".

**M3. Interactive Claude Code session on the candidate (about 8 minutes).** The earlier E2E used `claude -p`; the
interactive TUI is the surface users use (D14).
1. `cd "<scratch>\Skyrim Probe (x86)"`, run `claude`. Approve the project hooks if prompted.
2. `/hooks` -> expect the project's PreToolUse and SessionStart entries listed. Note anything else.
3. Say: `Create Data/ClaudeProbe.esp containing the word probe.` **Look for:** the Write tool refused with the
   plugin/archive message. Say: `Run: echo probe > Data/ClaudeProbe2.esp` **Look for:** a refusal (3.x only
   advised, E21). Say: `Run Get-Date in PowerShell.` **Look for:** it runs.
4. Say: `What preferences does your session start say I have?` **Look for:** the `PREF-SENTINEL-6611` line.
5. `/exit`. Run `python collect-claude.py` from the folder above (read-only; reads the project's transcript and the
   hook heartbeats; runs the scratch install's canary). Paste its output and the observations.

**M4. Headset / in-game: none.** v4.0.0 changes no mod, plugin, mesh or script content. Plans 6-7 add none either
as of drafting; if either adds a headset step it is appended here at T5, never asked mid-stream.

**M5. Other surfaces (optional; default is not claimed).** The Codex desktop app and IDE extensions, and Claude's
IDE extensions, are claimed only if a live probe proves hooks fire (D14). Skipped by default; the README then says
"Layers 0-2 only" for them. If the user wants a surface claimed, the probe is: open the scratch folder in that
surface, approve hooks, run step 3 of M1, run the collector; "blocked" -> claimed, "created" -> documented as not
covered.

**What the user is asked for afterwards:** paste the collector outputs and the one-line observations. No other
action.

### T19 Claude: Phase C, analysis, and the fix loop
- Run Phase C against the scratch folder (approved hooks, `codex exec`, no bypass; definition-change check on the
  scratch copy only). Read every collector output; reconcile each observation with its expected result.
- Decide per observation: pass; documented limit (add to README/CHANGELOG known limits and re-run T4 for that
  claim); defect.
- **A defect found here is fixed, then the evidence is invalidated back to the earliest stage the fix touches:**
  code change -> T13 for the delta (every changed file read in full again) -> T14 -> T15 (rebuild the archive;
  a new SHA means the previous verification record no longer applies) -> T16; and M1-M3 are re-run **only for the
  steps whose behaviour the fix changes**, as a short second user session, announced as such. A docs-only fix
  re-runs T4 and T15 only.
- M-L2 verdict (if Plan 6 present) is recorded here: ships / recovery-only; T12's branch is selected and the
  README/CHANGELOG text is finalised.

---

## Phase I: readiness, go-ahead, publish

### T20 Readiness report (Claude)
One page: the commit to be tagged (SHA), the archive's SHA-256 and `BUILD-INFO`, test/skip counts on both platforms,
the review's coverage and findings with their status, the mutation gate counts, the claims table result, the E2E
and manual-batch results, every known limit that ships, every open question. Includes the **rollback story** (what
happens if the release is bad: yank the GitHub release, point users at v3.9.1; the migration never deletes user
files, so a downgrade is extract-over of v3.9.1 plus the saved `.3x-backup` files).

### T21 [USER] Go-ahead
The user reads the report and gives (or withholds) an explicit go-ahead for each of: **the GitHub repo rename**, the
push, the tag, the release publication. A go for one is not a go for the others. Optional here: a branch push so CI
can run the POSIX leg before the real push (the user decides; default no).

### T22 [USER-gated] Rename, push, tag, verify, Nexus
Only after T21, and each step only if the user said go:
1. Rename the GitHub repo to `skyrim-ai-assistant-toolkit` (Settings -> General, or `gh repo rename`); confirm the
   old URL redirects; update the local remote (`git remote set-url origin ...`). Check the name is still free right
   before (it was free on 2026-09-30).
2. Push `master` (no tag yet); watch CI green on both legs.
3. Bump the version string, tag `v4.0.0` **at the exact SHA verified in T15** (if HEAD moved since, T15 is void),
   push the tag. `release.yml` builds from the committed objects with the shared script.
4. `bash tools/verify-release.sh v4.0.0` against the **published** artifact (cold clone, scrubbed), and compare
   the downloaded zip's hash with the T15 candidate's: they must be identical in content.
5. Prepare the Nexus pack (`scripts/build-nexus-pack.sh`: title, first line "for coding agents, not an AI NPC
   mod", CHANGELOG-derived description, one issue per line); **the upload is the user's manual step.**
6. Post-release: a short note into `KNOWLEDGEBASE.md` and the memory index (what the release taught: the gates that
   went red, the surprises), and the follow-ups (OpenCode, deferred "later" items) into the roadmap. Adopting v4 in
   the author's own install is a separate decision (spec non-goal).

---

## Deferred-items assignment table

"Plan 8 fix" = done in T6-T12 (or the task named). "Known limit" = written into README/CHANGELOG "Known limits"
(one per line) and kept out of the release's promises. "Later" = tracked in the roadmap's follow-ups, not in 4.0.0.
"Verify at merge" = reported resolved on a branch; T2/T3/T4 must show it with evidence, else it becomes a Plan 8 fix.

| # | Source | Item | Assignment | Where |
|---|---|---|---|---|
| 1 | Plan 2 T1 | test and recorder share `classify()`/`fire()`; corpus test adds ~94 s | Later | CI-time tuning |
| 2 | Plan 2 T1 | corpus extractor `ev()` swallows exceptions (dropped payloads equal themselves) | Later (note in known-limit list for contributors only) | test infra |
| 3 | Plan 2 T2 | `read_env` catches only `OSError` | Already resolved (c2affe7) | verify at merge |
| 4 | Plan 2 T2 | `read_env` is scoped to setup's `K='...'` form; comment missing | Plan 8 fix | T8 |
| 5 | Plan 2 T4 | `shell-cr-line-end` records allow (weak guard) | Later | |
| 6 | Plan 2 T5 | snapshot `_age_days`/`rl.stat()` unguarded against a vanishing file | Verify at merge (plan5 rewrites snapshot nesting); else Plan 8 fix | T3 |
| 7 | Plan 2 T5 | garbled `.last_snapshot` treated as 0 | Known limit (same outcome as bash); no entry needed beyond the table | |
| 8 | Plan 2 T6 | jq number formatting, `\u007f`, BOM/concatenated JSON, lone surrogate | Known limit (fails closed or stricter) | |
| 9 | Plan 2 T6 | `run.py --root ""` reads cwd-relative env | Plan 8 fix | T8 |
| 10 | Plan 2 T6 | `tool_name` 0/0.0 -> "unknown" (audit text only) | Later | |
| 11 | Plan 2 T7 | commit `d3ffd6a` bisectability (`{{PYTHON_PATH}}` vs `{{JQ_PATH}}`) | Won't fix (history) | |
| 12 | Plan 2 T7 | Store-alias python pinned | Already resolved (7375946) | verify at merge |
| 13 | Plan 2 T7 | `_SELF` mutants for backup/snapshot wrappers only covered by launch tests | Later; added in T13's mutation chunks if cheap | |
| 14 | Plan 2 final | `setup.sh` accepts any Python 3.x | Resolved on plan4 (`9f558ac`, requires 3.8+) | verify at merge |
| 15 | Plan 2 final | PY written in double quotes | Resolved on plan4 (single-quoted) | verify at merge |
| 16 | Plan 2 final | hook-timeout class: engine hang >60 s drops the answer | Partly resolved (plan4 in-process watchdog `485f3c1`; plan5 regex fix); remainder Known limit | T10.4(e), README |
| 17 | Plan 2 found-after | quadratic regexes (20 KB: 10.5 s, 2.7 s) | Fixed on plan5 (`fa4d0e1`, `80a2a37`) | verify at merge: timing guard present and green |
| 18 | Plan 3 final | `prefs.py` unclosed code fence swallows later sections | Plan 8 fix | T6 |
| 19 | Plan 3 final | `## My Preferences (...)` variant heading injects nothing, no audit line | Plan 8 fix | T6 |
| 20 | Plan 3 final | `prefs` `read_bytes` unbounded | Plan 8 fix | T6 |
| 21 | Plan 3 final | installed `CLAUDE.md` headroom on long MO2 roots ~34 chars | Plan 8 fix: setup warns (and `AGENTS.md` measured too); the limit in Known limits | T6, T14 |
| 22 | Plan 3 T3 | CI "Relative links in Markdown resolve" red at base on two plan docs | Plan 8 fix (re-check; fix the docs) | T6 |
| 23 | Plan 4 T1 | assert new verdict fields on more decision paths | Later | |
| 24 | Plan 4 T4 | stray `%SystemDrive%/ProgramData/...` folder from env scrub without `SystemDrive` | Plan 8 fix | T6, T10.3 |
| 25 | Plan 4 T4 | seq read-then-write repeats under simultaneous calls (DEGRADED one call longer) | Known limit (conservative) | |
| 26 | Plan 4 T5 | catastrophic-regex test mutant can orphan a spinning child | Plan 8 fix | T6, T10.4(e) |
| 27 | Plan 4 carry T8/10 | installer refuses `%` in root; PS quoting `''`; group-policy AllSigned / missing PowerShell end non-zero (fail open) -> canary/doc flags; `*.ps1` `eol=lf` | Resolved on plan4 per CHANGELOG for the first two; the AllSigned/missing-PowerShell doc + canary flag and the `eol=lf` gate: verify at merge, else Plan 8 fix | T2, T4 |
| 28 | Plan 4 final | agent self-protection: `.codex/**` and `tools/skyguard/**` writable by the agent | Plan 6 (Layer 2) or Known limit | T5 |
| 29 | Plan 4 final Important | subfolder-start loads no hooks, undocumented | Plan 8 fix (docs) + live recheck | T4, M1 step 7 |
| 30 | Plan 4 final Important | `codex.md` "deleting in game/config folders is refused" false for relative/PowerShell | Plan 8 fix: re-verify against merged engine (needs plan5 Task 7) | T4 |
| 31 | Plan 4 final | canary counts refused calls as hooked (can hide an unguarded call) | Verify fixed in plan4's final round; else Plan 8 fix | T2 |
| 32 | Plan 4 final | argv-list command -> allowed | Verify fixed in plan4's final round; else Plan 8 fix | T2 |
| 33 | Plan 4 merge-risk | `DENY_WRITE_PLUGIN` classification row | Plan 8 merge task | T3 |
| 34 | Plan 4 merge-risk | R5 `.rules` `reg delete` broader than narrowed hook deny | Plan 8 merge task | T3 |
| 35 | Plan 4 merge-risk | thread `cwd` into `evaluate_write` | Plan 8 merge task | T3 |
| 36 | Plan 4 merge-risk | one-code-per-row after changed DENY_PSC/DENY_CHAMPOLLION text | Plan 8 merge task | T3 |
| 37 | Plan 4 Task 14 | TUI run + Phase C deferred to the final batch | FINAL MANUAL BATCH M1 + T19 | |
| 38 | Plan 4 Task 0 | spec D16 `failureMode:"block"` not available on 0.159.3 (E25); plan4 re-measured on 0.160.0 | Re-measure on the current Codex in T16; adopt only with live proof and a "re-approve" CHANGELOG line (which would be 4.0.0's first approval anyway) | T16 |
| 39 | Plan 5 T0 | measurement docs named `2026-10-XX-*` | Plan 5 owns (its Task 17); verify at merge that no `XX` file names remain | T3 |
| 40 | Plan 5 T2 | totals double-count a same-rule decision change | Plan 5 owns; verify at merge or label | T3 |
| 41 | Plan 5 T3 | `legacy_rule_ids` short-literal chunks could match unrelated text (latent) | Later (not user-visible; note for release review) | T13 |
| 42 | Plan 5 T4 | `fa4d0e1` has a `wip` message | Won't fix: no history rewrite | |
| 43 | Plan 5 T5 | printf with no `%` could stay literal instead of `?` | Later | |
| 44 | Plan 5 T5/T6 carry | opaque `?` exec must still scan text for protected paths; middle-component globs against roots | Plan 5 owns (Task 7/7b); verified by claims rows | T3, T4 |
| 45 | plan5 Task 7b | resolver misresolutions (22 found); broad poisoning ruling | Plan 5 owns; T13 reads it in full | T13 |
| 46 | Plan 1 / roadmap | (a) Codex harness review fixes; (b) harness barriers wired into CI, Node auto-install dropped; (c) live Docker sentinel + Spriggit round-trip smoke; (d) MO2 helper precedence into the common path interface; (e) ESP-verify false PASS on absent inventory; (f) merged-revision Windows+POSIX run and cold archive check | (a) merged on master (`a87ce27`), (b) wired by Plan 5's test-infra task: verify at merge; (c) a user-host check, needs Docker Desktop: run in T14 if Docker is up, else Known limit; (d) Plans 4/5: verify at merge; (e) Plan 5: verify at merge; (f) **this plan, T14-T15** | T14, T15 |
| 47 | Spec D8 | OpenCode | Later (follow-up phase 10) | |
| 48 | Spec D5 / §9 | M-L2 outcome decides what the README may claim | Plan 6 -> user decision after M2 | T5, T19 |

## Task count

**22 tasks** (T1-T22): merges 3 (T1-T3), claim audit 1 (T4), Plan 6/7 merge 1 (T5), release code 7 (T6-T12),
release review 1 (T13), gates 3 (T14-T16), manual batch 3 (T17-T19), publish 3 (T20-T22). User-needed: T18 (the
batch), T21 (go-ahead), T22 (every outward action), and T5 only if M-L2's verdict or an unfinished Plan 6/7 needs a
ruling.

## Open questions (only the ones that are genuinely the user's)

1. **`--agent all` versus the spec's `both`:** adopted `claude|codex|all|auto` with `both` as an alias; the spec
   line is updated in the release commit. Say so if you want `both` to be primary.
2. **If Plan 6 or Plan 7 is not finished when everything else is:** whether v4.0.0 ships without them (spec
   allows Layer 2 = recovery only and "partially supported" for unknown agents) or waits. Not decided here.
3. **Steam verify (M2 step 3) runs against a real Steam library install** (it cannot verify a non-Steam copy).
   Plan 6 chooses the target; this is flagged so that the user knows it is the one batch step that touches a real
   install, and may prefer to skip it and ship Layer 2 as "pending Steam verify" or recovery-only.
4. **Branch push for a CI POSIX leg before the real push** (T14/T21): default is "no push at all until the
   go-ahead", with POSIX covered by the Docker devshell; the user may allow a branch push.

## Controller rulings on the draft's open questions (2026-10-04)

- **Q1 `--agent` names:** `claude|codex|all|auto`, `both` kept as an alias; spec line updated in the release commit.
- **Q4 branch push for CI:** no push before the user's go-ahead; POSIX covered by the Docker devshell.
- **Q2 (ship without an unfinished Plan 6/7?) and Q3 (Steam verify on a real install):** the user decides these at the readiness report (T20), together with the final manual batch, not before.
