# Plan 7 preflight: the base, the interfaces, the three unknowns

Measured 2026-10-04 in the `plan7` worktree. Tiers: **MEASURED** = I ran it; **READ** = the source says so.

## 1. Base

| | |
|---|---|
| worktree HEAD | `d7f8047` (Plan 7 fix round 3), on top of `722d790` "Merge plan5 (Plans 1-5) into plan7" |
| `plan4` head | `c30fa02` -- **ancestor of HEAD: yes** (MEASURED, `git merge-base --is-ancestor`) |
| `plan5` head | `f2d03a6` -- **ancestor of HEAD: NO** (MEASURED). Merge base is `544efd4`; the three commits not in plan7 are `7227988`, `b5225cf`, `f2d03a6` (Plan 5 Task 17: pricing, `verdict status=partial from the parse`, DevBench-on-Codex judgement, docs). |

The plan's base condition is therefore met for Plans 1-5 **except Plan 5 Task 17**, which is still landing on
another branch and will be merged into `plan7` later (orchestrator's ruling). Consequence for Task 1: build only
against the engine's public entry (`Request`, `Verdict`, `engine.pre_tool`, `codes.classify`), never against rule
internals, so Task 17's engine changes merge without touching `neutral.py`. Task 17 commit `b5225cf` touches
the verdict status path; re-run `tests/test_skyguard_neutral.py` after that merge.

## 2. Pinned interfaces (READ, at `d7f8047`)

- `tools/skyguard/request.py` -- `class Request(NamedTuple)`, fields in order, all defaulted:
  `v: int = 1`, `agent: str = ""`, `event: str = ""` (pre_tool | session_start | other),
  `kind: str = ""` (shell | write | mcp | other | none), `shell: Optional[str] = None` (bash | powershell | None),
  `command: str = ""`, `paths: Tuple[str, ...] = ()`, `ops: Tuple[str, ...] = ()`
  (add|update|delete|move_from|move_to|write|edit), `mcp_tool: str = ""`, `mcp_args: str = ""`,
  `cwd: str = ""` (RAW, never normalized), `tool_name: str = ""`, `session_id: str = ""`,
  `transcript_path: str = ""`, `source: str = ""`, `empty: bool = False`, `failure: str = ""`.
  The plan's names match; nothing renamed.
- `tools/skyguard/verdict.py` -- `DECISIONS = ("allow", "ask", "deny")`, `STATUSES = ("evaluated", "partial",
  "failed")`; `class Verdict(NamedTuple)`: `decision="allow"`, `reason=""`, `context=""`, `status="evaluated"`,
  `code=""`. `Advice` (add/joined).
- `tools/skyguard/engine.py` -- `pre_tool(req, project, roots_fn, audit) -> Verdict` (req.failure -> deny
  `codex.TRANSLATE`; event != pre_tool -> deny `codex.TRANSLATE`; else `_rules` then `side_effects`).
  `class RootsUnavailable(Exception)` (message = refusal text). `NO_PATHS` = "GUARD INERT: a write request named
  no file, so the write rules checked NOTHING. Allowing silently would be indistinguishable from deciding this is
  fine." (code `engine.NO_PATHS`, status failed). Also `INERT_TRANSLATE`, `_BACKUP_OPS = ("update", "delete",
  "move_from")`, `UNHOOKED_LOG = "codex-unhooked-tools.log"`, `evaluate_shell/evaluate_write/evaluate_mcp/combine`.
  - kind `write`: `evaluate_writes` with soft roots (an unreadable env costs advice, never a refusal).
  - kind `shell`: roots required when the command is non-empty (`RootsUnavailable` -> deny `run.ENV_UNREADABLE`
    with the exception's text); if `paths` are also given they are judged as writes and combined.
  - kind `mcp` and `other`: **`Verdict()` (allow)**; the DevBench judge is wired for Claude only (engine comment
    cites docs/guard-coverage.md X4). Side effects log them to `.claude/backups/codex-unhooked-tools.log`.
- `tools/skyguard/run.py` -- `ADAPTERS = {"claude": claude, "codex": codex}`, `CODEX_ROLES = ("pre-tool",
  "session-start")`; argparse: `agent` choices `sorted(ADAPTERS)`, `role` choices `["protect-bash",
  "protect-files", "backup-before-edit", "snapshot-before-tool", "session-kb-guard", "protect-mcp"] +
  list(CODEX_ROLES)`; options `--root --root-win --project(required) --bash --invocation --telemetry`.
  Check `if (a.agent == "codex") != (a.role in CODEX_ROLES): return 1` runs BEFORE stdin is read.
  `_codex(a, raw) -> int`, `_declared_env(a)` (None when the env file exists but cannot be read),
  `_telemetry(a, v)` (writes `<role>.evaluated` + trims latency, only when both `--invocation` and
  `--telemetry`), `_emit(text)`, `_session_line(...)` (indexes `health.COUNTING_HOOKS[a.agent]` -- swallowed on
  KeyError, but a neutral role must not call it). `ENV_UNREADABLE` in run.py is the Claude wording
  ("protect-bash.sh found ..."); the Codex wording is `adapters.codex.ENV_UNREADABLE` ("the Codex hook found
  ..."). Neither is agent-neutral, so Task 1 defines its own neutral wording (see section 6).
- `tools/skyguard/codes.py` -- `classify(v: Verdict) -> Verdict`: keeps an explicit code; else codes the reason
  (deny/ask, whole match) or context (allow, first-item prefix); no text -> `"allow"`; no match -> `"unknown"`;
  an INERT code forces status failed.
- `tools/skyguard/health.py` -- `record(dirpath, hook, stage, inv, detail="")` overwrites `<hook>.<stage>`;
  `COUNTING_HOOKS = {"claude": ("protect-bash", "protect-files", "protect-mcp"), "codex": ("pre-tool",)}`
  (no `neutral` key: ruling R4 stands).
- `write_rules.DENY_PLUGIN = rules.RULES["W-PLUGIN"]["template"]`; the text, as returned through the engine:
  "Blocked: plugin and archive files (.esp .esm .esl .bsa .ba2) cannot be written directly.\nUse xelib,
  Spriggit or AutoMod to change them instead." Code `write.DENY_PLUGIN`.
- `shell_rules.decide(command, roots, audit, cwd="", shell="", env=None)`; `engine.evaluate_shell` passes
  `cwd=req.cwd or ""`, `shell=req.shell or ""`. `""` = unknown dialect = parsed as bash AND PowerShell (R5).

## 3. Unknown (a): `Request.shell=None` and `Request.cwd` -- YES, both honoured

READ: `engine.evaluate_shell` maps `shell=None` to `""`, which `shell_rules.decide` documents as "parsed as bash
AND PowerShell". MEASURED (`engine.pre_tool` called directly, throwaway install, `paths.roots(root, root, {})`):

| command | shell | cwd | decision |
|---|---|---|---|
| `Set-Content -Path Data/Decoy.esp -Value x` | None | install root | deny (raw-text plugin write) |
| same | bash | install root | deny |
| same | powershell | install root | deny |
| `rm -rf Data` | None | install root | deny ("deletes Data inside the Skyrim install") |
| `rm -rf Data` | None | `<tmp>/elsewhere` | allow |

The last two rows differ only in `cwd`, so the relative path is resolved against the request's cwd.

## 4. Unknown (b): the decoy plugin write -- DENIED both ways (MEASURED)

Throwaway install in the session scratchpad (`tools/skyguard` copied), `python run.py codex pre-tool --root R
--root-win R --project R`, Codex fixtures `codex-PreToolUse-Bash-1.json` / `codex-PreToolUse-apply_patch-1.json`
with `tool_input.command` replaced (the `tests/test_codex_entry.py::_plugin_write` pattern). Every run: exit 0,
stdout `SKYGUARD-OK` + body.

| request | deny text (verbatim, `\n` = newline in the reason) |
|---|---|
| shell `echo x > "<install>/Data/Decoy.esp"` | `Blocked: this writes Decoy.esp as raw text, which corrupts a plugin or archive.\nUse xelib, Spriggit or AutoMod to change it.` |
| shell `echo x > Data/Decoy.esp`, cwd = install | same text |
| shell `echo x > <outside>/elsewhere/Decoy.esp` | same text |
| apply_patch `*** Add File: <install>/Data/Decoy.esp` | `Blocked: plugin and archive files (.esp .esm .esl .bsa .ba2) cannot be written directly.\nUse xelib, Spriggit or AutoMod to change them instead.` |
| apply_patch `*** Add File: <outside>/elsewhere/Decoy.esp` | same text |

Checker note (my own probe bug, recorded per CLAUDE.md "Checker bugs"): the first run left the install path
unquoted. The install directory is `Skyrim VR` (a space), so the redirect target was `.../Skyrim` and the engine
correctly **allowed** it. The allow was the probe's fault, not the engine's; quoted, it denies. Task 5's
decoy rows must quote paths containing spaces.

So no change to Task 1 or Task 5 is needed: the shell decoy row stays.

## 5. Unknown (c): generator and release surface

- `python scripts/gen_agent_trees.py --check` -> `in sync: 42 generated files (all)`, rc 0 (MEASURED), and the
  same with a throwaway `tools/adapting/probe.py` present (MEASURED; removed afterwards, `git status` clean).
  `tools/` is not generator input.
- Release bundle: `.github/workflows/release.yml` builds the zip **by rule**: `rsync -a --exclude '.git'
  --exclude '.github' --exclude 'tests' --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' ./
  "/tmp/$NAME/"` (READ, lines 48-50). `tools/adapting/` and a root `ADAPTING.md` therefore ship with no list
  edit. The workflow and `tools/verify-release.sh` only add **presence assertions** for a few named files
  (`release.yml` lines 97, 101, 104: `tools/hook-canary.sh`, `tools/kb-guard.sh`, `tools/skyguard/run.py`;
  `verify-release.sh` lines 95-97 the same three). Task 10 may optionally add a matching `test -f` for
  `tools/skyguard/neutral.py` / `ADAPTING.md` beside line 104 / line 97; nothing is required for them to ship.

## 6. What Task 1 uses (the pinned names)

`Request`, `Verdict`, `DECISIONS`, `STATUSES`, `engine.pre_tool`, `engine.RootsUnavailable`, `codes.classify`,
`paths.roots`, `run._declared_env`, `run._telemetry`, `run._emit` -- all as named in the plan. The one change:
`run.ENV_UNREADABLE` is Claude wording, so the neutral role raises `RootsUnavailable` with its own
agent-neutral text (`neutral.ENV_UNREADABLE`), which the engine returns as `run.ENV_UNREADABLE`-coded deny.

## 7. Runbook preconditions (Step 3)

`grep -n "winget install\|choco\|apt-get\|brew install" setup.sh tools/*.sh` (MEASURED), every hit:

```
setup.sh:68:    echo "    winget install OpenJS.NodeJS.LTS   (or download from nodejs.org)"
setup.sh:86:            echo "  Install a current Python (python.org, or 'winget install Python.Python.3.12') and re-run."
setup.sh:93:    echo "  Install it from python.org (or 'winget install Python.Python.3.12') and re-run -- ..."
setup.sh:142:    echo "  Install Python 3 from python.org (or 'winget install Python.Python.3.12'), then: bash setup.sh"
setup.sh:177:        echo "     Install:  winget install Microsoft.DotNet.SDK.9"
setup.sh:183:    echo "    Spriggit needs SDK 9:   winget install Microsoft.DotNet.SDK.9"
setup.sh:184:    echo "    AutoMod ALSO needs 8:   winget install Microsoft.DotNet.SDK.8"
setup.sh:197:    echo "    Install when you want it:  winget install Microsoft.OpenJDK.21"
tools/toolchain-check.sh:90:    printf '  %-22s               winget install Microsoft.DotNet.SDK.9)\n' ""
```

All are printed advice. `grep -n winget setup.sh` minus echo/printf lines leaves only a comment (line 60:
"... blocks winget/npm/pip for the same reason"). **The Node auto-install is gone** on this base (setup.sh lines
62-69 report a missing Node and say "Setup does not install it"), so deferred item D-2 is **not needed**.

winget (MEASURED, read-only `winget install --help`): version `v1.29.380`. Present: `-e,--exact`,
`-v,--version`, `--no-upgrade`, `--accept-package-agreements`, `--accept-source-agreements`, and
**`-s,--source` ("Find package using the specified source") -- confirmed**, so Task 7 may print `--source
winget`. (Task 7 is already done; this only records that its premise holds on this machine.)

## 8. Prompt and skill plumbing (Step 4)

- `tests/test_repo_invariants.py:15: PROMPT_START = "I just installed the Skyrim modding toolkit"` (MEASURED).
- `README.md:313: Copy this entire prompt and paste it into your coding agent (Claude Code, Codex, ...)`;
  `docs/getting-started.md` has no such line (Task 9 already done on this base).
- `agent/skills`: create-mod, devbench, inspect-esp, port-to-vr, skyrim-audio, skyrim-bsa, skyrim-context,
  skyrim-mcm, skyrim-nif, skyrim-save, toolkit-setup, triage-and-conflicts, xelib (13; `toolkit-setup` is Task 8).
- `gen_agent_trees.py --check` green (section 5). `--budget`: `core.md + claude.md: 30332 / 32768 bytes
  (headroom 2436)`, `core.md + codex.md: 30893 / 32768 bytes (headroom 1875)` -- the largest addendum is
  `codex.md`, so Task 10's one table row has **1875 bytes** of headroom.
