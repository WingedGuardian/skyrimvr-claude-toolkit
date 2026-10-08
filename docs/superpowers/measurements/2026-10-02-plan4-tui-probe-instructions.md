# Plan 4 Task 0 Step 6 — the Codex TUI probe (user step, about 10 minutes)

This is the one Task 0 probe that needs a person at a real terminal: `codex exec` cannot show what the
interactive TUI does. Everything is already prepared. You do not need to install or edit anything, and
**nothing here touches your Skyrim install or this repo.**

The scratch folder (a throwaway git repo, Codex 0.160.0 probe hooks already wired):

```text
C:\Users\user\AppData\Local\Temp\claude\C--GOG-Games-The-Elder-Scrolls-V-Skyrim-VR\a58938c5-68de-4c34-bf34-f1988738f9f1\scratchpad\plan4-probes\p-tui
```

Its hooks are the plan's R2 launch form (`pwsh ... -File "<folder>\sp ace\probe.ps1" || powershell ...`).
The PreToolUse hook answers a JSON **deny** to any command containing `DENYME`, and lets everything else
through. The SessionStart hook injects about 6,000 bytes of context whose **last word is
`SESSIONTAIL-8812`**. Every call is logged to `hooklog.jsonl` and `raw\` in that folder.

## 1. Run it (copy-paste)

Open a **new** Windows Terminal tab (PowerShell) and paste:

```powershell
cd "C:\Users\user\AppData\Local\Temp\claude\C--GOG-Games-The-Elder-Scrolls-V-Skyrim-VR\a58938c5-68de-4c34-bf34-f1988738f9f1\scratchpad\plan4-probes\p-tui"
codex
```

If Codex asks whether to trust this folder, say **yes** (project hooks only load in a trusted folder).

## 2. In Codex, in this order

1. Type `/hooks`. It should list **two** hooks for this folder: `SessionStart` and `PreToolUse`.
   **Approve both.** (This is what a real user does; Codex then records an approval hash for each.)
   Note whether `/hooks` shows anything else (a warning, a "modified" label, a count).
2. Type, as a normal message:
   `Run New-Item -ItemType File DENYME.flag in the shell, then run Get-Date.`
   If Codex asks you to approve a command, approve it. **Look for:** the `New-Item` call should show as
   blocked by the PreToolUse hook (`probe json deny (DENYME)`), and `Get-Date` should run.
3. Type exactly: `!echo skyguard-bang-probe`
   (the `!` prefix runs it in your own shell, not through the model). Just let it print.
4. Type `/compact` and wait for it to finish.
5. Type: `What is the last word of your session-start context? Reply with that word only.`
   **Look for:** whether it answers `SESSIONTAIL-8812`.
6. Type `/quit`.

## 3. Collect (copy-paste, same tab)

```powershell
python collect.py
```

It only **reads** files (the scratch folder and, read-only, `~\.codex\config.toml`). **Paste its whole
output back**, together with one line each for:

- did step 2 say *blocked*? (yes/no; paste the line if you can)
- did step 2 ask you to approve anything before the block? (yes/no)
- what did step 5 answer?
- anything odd in `/hooks` (step 1)?

Then say **"TUI done"**.

## What this decides

| What we read | Decides |
|---|---|
| PreToolUse fired in the TUI and the DENYME call was **Blocked** | D14/D2 must-have. If it did not fire or did not block, Plan 4 stops: no Codex support without CLI parity |
| whether `!echo skyguard-bang-probe` produced a PreToolUse payload | R4's escape text ("you can run it yourself with `!`") |
| a SessionStart payload with `source: "compact"` after `/compact`, and the step-5 answer | whether the session context survives compaction; the payload becomes a fixture |
| the TUI rollout's `session_meta.originator` / `source` | Task 13's surface split (exec measured as `codex_exec` / `exec`) |
| the `[hooks.state.'<key>']` keys and `trusted_hash` values Codex wrote | Task 8's `expected_entries()` key spelling (path case, slashes) and hash must reproduce them |

## Cleanup you may want afterwards (we never edit `~/.codex/config.toml`)

Codex writes these to `~/.codex/config.toml` by itself; you can delete them by hand once Plan 4 is done:

- From the `codex exec` probes (already there, written 2026-10-02):
  `[projects.'c:\users\user\appdata\local\temp\claude\c--gog-games-the-elder-scrolls-v-skyrim-vr\a58938c5-68de-4c34-bf34-f1988738f9f1\scratchpad\plan4-probes\p1']`
  with `trust_level = "trusted"`.
- From this TUI probe (will appear): a `[projects.'…\plan4-probes\p-tui']` table, and two
  `[hooks.state.'…\plan4-probes\p-tui\.codex\hooks.json:…']` tables (one per approved hook).
  `python collect.py` prints their exact names (section 5 of its output).
