# Plan 4 Task 14 -- the Codex TUI end-to-end (user step, about 10 minutes)

This is the one Task 14 probe that needs you at a real terminal: `codex exec` cannot show what the
interactive TUI does with the toolkit's hooks. Everything else (the `codex exec` phases and the Claude
regression) is already run. **Nothing here touches your Skyrim install or this repo**: the folder below is a
throwaway release-style copy of the toolkit with fake game files (empty `SkyrimVR.exe`, `Data/Existing.esp`,
and so on), already set up with `setup.sh`.

The scratch folder:

```text
C:\Users\Moona\AppData\Local\Temp\claude\C--GOG-Games-The-Elder-Scrolls-V-Skyrim-VR\a58938c5-68de-4c34-bf34-f1988738f9f1\scratchpad\plan4-e2e\tui
```

It holds the install, `Skyrim Probe (x86)`, and the collector, `collect.py`. Open `codex` inside the
`Skyrim Probe (x86)` folder, **not** inside `Data\` or any other subfolder (see "What this decides", last row).

## 1. Run it (copy-paste)

Open a **new** Windows Terminal tab (PowerShell) and paste:

```powershell
cd "C:\Users\Moona\AppData\Local\Temp\claude\C--GOG-Games-The-Elder-Scrolls-V-Skyrim-VR\a58938c5-68de-4c34-bf34-f1988738f9f1\scratchpad\plan4-e2e\tui\Skyrim Probe (x86)"
codex
```

If Codex asks whether to trust this folder, say **yes** (project hooks only load in a trusted folder; that
trust entry is one of the three things listed under "Cleanup" below).

## 2. In Codex, in this order

1. Type `/hooks`. It should list **two** hooks for this folder, labelled **"Skyrim toolkit: session checks"**
   (SessionStart) and **"Skyrim toolkit: checking"** (PreToolUse). **Approve both.** Note whether `/hooks`
   shows anything else (a warning, a "modified" label, a count other than 2). Close the `/hooks` screen.
2. Type, as a normal message:
   `Create Data/TuiProbe.esp containing the word probe, using apply_patch.`
   If Codex asks you to approve something, approve it. **Look for:** a message saying the call was
   **blocked by the PreToolUse hook**, quoting "Cannot directly write to plugin/archive files". If instead
   Codex says the file was created, note that (it is the failure we are testing for).
3. Type: `Run Get-Date.`
   **Look for:** it runs and prints a date. (Approve the command if asked.)
4. Type: `The SessionStart hook injected additional context at the start of this session. Without reading any file or running any command, reply with the first line of that hook context and the preference sentinel found in it.`
   **Look for:** a first line starting `SKYGUARD: Codex hooks live`, and a sentinel `PREF-SENTINEL-6611`.
   (If Codex declines to quote it, that is the model, not the toolkit: just note what it answered.)
5. Type `/quit`.

## 3. Collect (copy-paste, same tab -- run it YOURSELF)

**Run this in PowerShell yourself. Do not ask Codex to run it**: it must capture what you and Codex really
did in the session, not a second session's.

```powershell
cd ..
python collect.py
```

(`cd ..` goes from `Skyrim Probe (x86)` up to `tui`, where `collect.py` lives. Needs Python 3.8+; `python`
is on your PATH from earlier probes.)

It only **reads**: the scratch install, the Codex rollout files for this folder, and, read-only,
`~\.codex\config.toml`. It runs the scratch install's own hook canary. It writes nothing and edits no config.

**Paste its whole output back**, together with one line each for:

- did step 2 say *blocked*? (yes/no; paste the line if you can)
- did anything ask you to approve something before the block? (yes/no)
- what did step 4 answer?
- anything odd in `/hooks` (step 1)?

Then say **"TUI done"**. The orchestrating session then runs Phase C (`codex exec` with your approved
hooks, no bypass flag, plus the definition-change check) in this same folder.

## What this decides

| What we read | Decides |
|---|---|
| `TuiProbe.esp` absent, the hook's `protect-files DENY` line in the audit log, the "blocked by PreToolUse hook" text in the rollout | D14/D2 must-have: the toolkit's guard holds in the TUI, for the right reason |
| `Get-Date` ran | the guard does not block ordinary work in the TUI |
| the step-4 answer, and no read of `KNOWLEDGEBASE.local.md` in the rollout | D15 through the real TUI entry point |
| rollout `originator` = `codex-tui` | the canary's surface split (Task 13) labels the TUI correctly |
| the `[hooks.state.'...']` hashes in section 4 equal the toolkit's computed hashes (section 5 prints `stored=True`) | `codex_trust.expected_entries()` still matches what Codex itself stores (re-measured on our own definitions) |
| the canary's COVERAGE block shows the TUI session HEALTHY | the canary can see the TUI surface. Known risk: see the concern in the Task 14 report -- rollouts with a `Reasoning` item currently read UNMEASURABLE |
| Phase C afterwards: approved hooks run under `codex exec`; changing `"timeout": 60` to `61` disarms them (canary says `modified`); restoring re-arms | E18 re-measured on our own definitions |

## Cleanup you may want afterwards (we never edit `~/.codex/config.toml`)

Codex writes these to `~/.codex/config.toml` by itself; you can delete them by hand once Plan 4 is done.

- Already there from the `codex exec` runs (2026-10-04), each `trust_level = "trusted"`:
  - `[projects.'c:\users\moona\appdata\local\temp\claude\c--gog-games-the-elder-scrolls-v-skyrim-vr\a58938c5-68de-4c34-bf34-f1988738f9f1\scratchpad\plan4-e2e\a\skyrim probe (x86)']`
  - `[projects.'...\plan4-e2e\g\skyrim probe (x86)']` (same prefix)
  - `[projects.'...\plan4-e2e\b\skyrim probe (x86)']` (same prefix)
- From Task 0 (2026-10-02): `[projects.'...\scratchpad\plan4-probes\p1']`, and from its TUI probe a
  `[projects.'...\plan4-probes\p-tui']` table plus two `[hooks.state.'...\p-tui\.codex\hooks.json:...']`
  tables, if you ran it.
- From this TUI run (will appear): a `[projects.'...\plan4-e2e\tui\skyrim probe (x86)']` table and two
  `[hooks.state.'...\tui\skyrim probe (x86)\.codex\hooks.json:...']` tables (one per approved hook).
  Section 4 of the collector's output prints their exact names.
