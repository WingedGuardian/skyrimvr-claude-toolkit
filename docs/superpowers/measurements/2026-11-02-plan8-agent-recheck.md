# Agent recheck on current versions, 2026-11-02

Development evidence, not release acceptance. Candidate: `bc246b8`, packaged as
`skyrim-ai-assistant-toolkit-4.0.0.zip` (371 members; byte-identical from a cold clone and from a second worktree; `verify-release.sh --local --scrubbed` OK). Windows 11, fake install with an empty `SkyrimVR.exe`, set up with `setup.sh --agent all`. Codex ran with an isolated `CODEX_HOME`.

## Payload shapes

| Agent | Version | Result |
|---|---|---|
| Claude Code | 2.1.316 | 7 payloads with a committed twin, all the same keys and value types. No fixture replaced. |
| Codex CLI | 0.165.0 | 5 payloads (SessionStart, Bash, 3 `apply_patch`), all the same keys and value types. No fixture replaced. |

Scrubbed output held neither the user's name nor home folder.

## Guards live, on the candidate archive

| Agent | Command | Result |
|---|---|---|
| Claude Code, Bash | `rm -rf <abs>/Data` | Blocked: "this deletes Data inside the Skyrim install". `Data` survived. |
| Claude Code, PowerShell | `Remove-Item -Recurse -Force <abs>/Data` | Blocked, same message. `Data` survived. |
| Claude Code, Bash | `ls`; `echo hi > <abs>/notes2.txt` | Ran. |
| Codex, shell | `Remove-Item -Recurse -Force <abs>/Data` | Blocked, same message. `Data` survived. |
| Codex, shell | `Remove-Item -Recurse -Force Data` (relative) | Blocked as an unresolved target ("GUARD INERT"): Codex supplies no execution workdir. `Data` survived. |
| Codex, shell | `Get-ChildItem -Name`; `Set-Content <abs>/notes.txt` | Ran. |

Claude ran with `--permission-mode bypassPermissions`; Codex with `--dangerously-bypass-hook-trust -s danger-full-access`, inside throwaway folders only.

## Codex 0.165.0 `-s workspace-write` on Windows

MEASURED in one isolated home with no `config.toml`: the session recorded `sandbox_policy: read-only` despite the flag, shell calls were rejected ("blocked by policy"), `apply_patch` was rejected, and neither SessionStart nor PreToolUse hooks ran (0 payloads, with a hook that worked under full access). Nothing could change, so this is not a fail-open, but a guard check under this mode proves nothing. `.codex/hooks.json` hooks also need `commandWindows` on Windows; a `command`-only capture hook recorded nothing.

Not measured here: the interactive TUI, a trusted project with persisted hook trust, and the sandbox an ordinary user's configuration selects. Those stay in the manual batch.

## Not run

OpenCode live: version 1.18.34 is installed but has no credentials, so it needs the user.
