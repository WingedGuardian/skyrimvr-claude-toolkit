# ADAPTING.md: you are an agent this toolkit does not support yet

This file is written to you, the coding agent. If you are Claude Code or the Codex CLI, stop: the toolkit
is already wired for you (see `README.md`). Otherwise you can wire it into yourself. Be honest about how
far you get: the toolkit gives you Layers 0 and 1 without any hook (Layer 2 is not yet released), and Layer 3
only if you can *prove* a hook of yours blocks.

`python` in this file means a Python 3.10 or newer interpreter. On Linux and macOS that is usually `python3`; on
Windows, `py -3` or `python`. Use whichever one starts on this machine.

## 0. The only status words you may use

| Say | When |
|---|---|
| *supported* | never about yourself: only the Claude Code CLI and the Codex CLI are, as `README.md` lists them |
| **partially supported: Layers 0-2** | the default. You read the instructions and skills and use the tools; **nothing stops a destructive command**. Layer 2, a read-only lock below the agent, is not yet released |
| **locally adapted, self-verified** | only after section 7. It describes *this install on this machine*. It is not a claim by the toolkit |

## 1. Rules

1. **Never edit the engine, the guard corpus, a rule module or a test to make your adapter pass.** An
   adapter translates; the engine decides. If a rule looks wrong, report it (section 9).
2. **Find out how your agent fails.** A hook that errors, times out or prints garbage must not allow. Some agents
   treat all of those as "allow" (section 5). Measure yours (section 3) before trusting any wrapper.
3. **Only a mechanism your agent honours as a hard block counts.** Prove it with a harmless action.
4. **Do not install, upgrade, repair or remove host software** (Python, Git, Node, anything) unless the user's
   task says so in so many words. Use the `toolkit-setup` skill.
5. **Report every gap** in the capability report. A gap you did not write down will be assumed absent.

## 2. Layers 0-2: what you get without any hook

The layers, as this kit numbers them: **Layer 0**, the command-line tools in `tools/`; **Layer 1**, the plain-text
instructions, skills and knowledgebase; **Layer 2**, protection below the agent: a read-only lock on the vanilla
masters and archives, **not yet released**, so it protects nothing today; **Layer 3**, an in-loop hook, which is
what an adapter adds. These are not the numbered defenses of `docs/safety-philosophy.md`, which describe the two
supported agents.

1. Read `AGENTS.md` completely. It is the toolkit's instruction file. If your instruction limit is below 32 KiB
   it may be cut silently: say so in the report (section 3) and read the file yourself in pieces.
2. Skills are plain `SKILL.md` files in `.agents/skills/<name>/`. Read one *before* the work it names.
3. Use `tools/` (the wrappers, `tools/skyguard`, `tools/hook-canary.sh`) through your shell tool.
4. Read the `## My preferences` section of `KNOWLEDGEBASE.local.md` every session; named agents get it injected
   by a hook, you must read it.

## 3. Self-assessment: produce the capability report

Copy `tools/adapting/capability-report.template.json` to `capability-report.json` and fill **every** leaf as
`{"value": ..., "tier": "MEASURED|READ|INFERRED|ASSUMED", "evidence": "..."}`. The evidence must name a file or
a command; the validator reports an empty one as a problem, and a gating leaf (below) with no evidence is not
evidence at all. Measure, do not recall:

- **Payload capture.** Install a hook that only writes its stdin to a file and exits 0 (or your agent's
  allow). Run one shell command, one file write and, if you have it, one MCP call. Keep the files: they are
  your conformance cases (`payload_capture.files`).
- **Can the hook block?** Hook: create `probe-ran.flag`, then emit your candidate block. Action: a command that
  creates `probe-action.flag`. If `probe-action.flag` exists, that block shape does **not** stop your agent.
- **Does a failed hook fail open?** Same, but the hook exits 1; then prints garbage; then sleeps past its
  timeout; then points at a script that does not exist. Record each. Any row where the action ran means
  that *the wrapper, not the agent*, must carry the failure. Record the agent's hook timeout, in seconds, as
  `pre_tool_hook.hook_timeout_s`.
- **Which tools write or run?** List every tool of yours that writes or edits a file and every tool that runs a
  command, by the names your agent uses. They become the profile's `write_tools` and `shell_tools` (section 6).
- **Which surface?** Terminal, desktop app and IDE extension are different surfaces. Probe the one you are
  running in; do not generalise.
- **Can it ask?** Only if a block shape pauses and shows the user a prompt. Default: no.

Validate with `python tools/adapter_conformance.py report capability-report.json`. The tool *derives* your
status and prints it first; it never reads one from you. A `claimed_status` stronger than the derived one is an
error. Four leaves gate the stronger status, and each must be tier MEASURED with evidence:
`pre_tool_hook.fires_on_this_surface` (true), `pre_tool_hook.can_block` (true),
`pre_tool_hook.failed_hook_behaviour` (`allows` or `blocks`) and
`pre_tool_hook.every_write_and_shell_tool_mapped` (true: each such tool is in your adapter's map and in the
profile's lists, section 6). When a failed hook `allows`, `pre_tool_hook.hook_timeout_s` gates too: MEASURED, and
at least 30 seconds (the adapter's own 25 s deadline plus a 5 s margin), because an agent that gives up on the hook
first runs the call. Then `proof` (section 7) must be filled, both live canaries included. An INFERRED or ASSUMED
gating leaf keeps you at **partially supported: Layers 0-2**, and the tool says which.

## 4. The contract

Request (JSON on stdin of `python tools/skyguard/run.py neutral pre-tool --root R --root-win R --project R`;
`R` is the install folder):

| Field | Meaning |
|---|---|
| `v` | must be the integer `1` (not `true`, not `1.0`, not a string) |
| `event` | must be `"pre_tool"` |
| `kind` | `shell` (a command string), `write` (a file write or edit, with `paths`), `mcp` (judged by the DevBench rules, below) or `other` |
| `shell` | `"bash"`, `"powershell"` or `null` (both dialects are tried); only with `kind: "shell"` |
| `command` | the command text, for `shell` |
| `paths` | absolute paths, for `write` (and for a shell call you can already see writes files) |
| `ops` | parallel to `paths`, each one of `add`, `update`, `delete`, `move_from`, `move_to`, `write`, `edit`; default `update`. The ops that take a backup: `update`, `delete`, `move_from`. The rest take none |
| `cwd` | the call's working directory, **raw** (do not normalise) |
| `mcp_tool`, `mcp_args` | for `mcp`: the tool's full name and its arguments as JSON object text (`""` means none) |
| `tool_name`, `session_id`, `agent` | optional strings; `agent` may not be `claude` or `codex` (those agents have their own entry) |

Unknown fields and a repeated key are refused, at every nesting level. A request that cannot be understood is
answered with a deny whose reason starts `GUARD INERT`: that is a refusal, not a decision. A tool call you cannot
classify is `other`, **except** that any call carrying a command string must be `shell`. A write whose paths you
cannot extract must not be sent as an empty `write`: the engine denies it, and so should you.

What each kind means to the engine:

- `shell`, `write`: judged by the shell and write rules, and backed up (above) whatever the decision.
- `mcp`: judged by the same DevBench rules Claude's MCP hook uses. A call that would crash the game is denied; a
  call that saves over an existing save returns **`ask`**, which falls under the ask rule below. Any other
  call is allowed. Name the tool as the three parts `mcp`, `<server>`, `<tool>` joined by a double underscore:
  only a server whose name starts `devbench` is judged. A call to any other server is allowed with `status`
  `partial` and the context `NOT JUDGED: only a DevBench MCP server is guarded`; treat it as you treat `other`.
- `other`: **allowed, and never judged.** The answer carries `status` `partial` and the context
  `NOT JUDGED: this tool kind is not guarded`. Show or log that context; do not report the call as checked.

Answer (stdout): the line `SKYGUARD-OK`, then one line of JSON:
`{"v":1,"decision":"allow|ask|deny","reason":...,"context":...,"status":"evaluated|partial|failed","code":...}`.
Exit 0 whenever the engine decided. **The engine broke** when its exit code is not 0, **or** when the first line
of its stdout is not exactly `SKYGUARD-OK`: render your agent's hard block for that, with a reason that says the
guard checked nothing. Only after both checks pass may you read the verdict line, and a missing or unknown
`decision` is still a deny. A verdict is yours to render: `deny` -> your hard block; `ask` -> a deny that tells the
user to run the command themselves, unless you proved your agent pauses and shows a prompt (then fill `render_ask`);
`allow` -> let it run, passing `context` as advice if you can.

## 5. What is known about the two supported agents

Copy their *mechanism*, not their wording. Every row is cited.

<!-- facts:begin -->
| Fact | Tier | Source |
|---|---|---|
| Codex blocks a tool call on exactly one thing: a parsed JSON `permissionDecision: "deny"`. `ask`, exit 2, exit 1, malformed JSON, a timeout and a missing script all let the command run | MEASURED | [spec: E17] [measured: docs/superpowers/measurements/2026-10-02-plan4-codex-derisk.md#EXIT2ME] |
| Codex skips a project's unapproved hooks silently | MEASURED | [spec: E2] |
| Codex shell commands arrive as tool `Bash` although they run under PowerShell | MEASURED | [spec: E3] |
| Codex `apply_patch` carries paths only inside the patch text | MEASURED | [spec: E4] [src: tools/skyguard/patch_paths.py::def parse_patch] |
| A user-typed `!` command in the Codex prompt fires no pre-tool hook | MEASURED | [src: tools/skyguard/adapters/codex.py::fires no PreToolUse] |
| The Codex adapter turns every `ask` into a deny that says the user must run it themselves | READ | [src: tools/skyguard/adapters/codex.py::def render] |
| The Claude adapter passes `ask` and `deny` through | READ | [src: tools/skyguard/adapters/claude.py::def render] |
| A deny that arrives after the agent's own hook timeout is discarded, so every wrapper bounds its own work below that timeout | READ | [src: agent/guards/adapters/codex/skyguard-entry.sh::DISCARDS an answer that arrives after its hook timeout] |
| `AGENTS.md` past 32 KiB is silently truncated by Codex | MEASURED | [spec: E7] |
| A skill with Claude-only frontmatter loads and runs in Codex | MEASURED | [spec: E6] |
| The adapter template adds one Python start to every call; measured median and p95 are recorded | MEASURED | [measured: docs/superpowers/measurements/2026-10-05-plan7-adapter-latency.md#adapter median] |
<!-- facts:end -->

## 6. Wire the adapter

1. **Instructions.** Make your agent read `AGENTS.md` at the start of every session (or copy it to the file name
   your agent reads; do not edit the copy).
2. **Skills.** Point your agent's skills folder at `.agents/skills/` or copy the folders.
3. **Adapter folder.** `mkdir tools/adapters/<name>` and copy `tools/adapting/adapter_template.py` to
   `tools/adapters/<name>/adapter.py`. Edit **only** the block between `# SLOTS-BEGIN` and `# SLOTS-END`:
   `AGENT_NAME`, `STATIC_BLOCK` (a literal in your agent's measured block shape), `map_payload`,
   `render_block`, `render_allow`, and `render_ask` only if you proved your agent asks. The worked examples are
   `tools/adapting/examples/json_deny.slots.py` (a JSON-deny agent, the Codex mechanism) and
   `tools/adapting/examples/exit2.slots.py` (an exit-code agent). Each maps only the tools named in its
   `WRITE_TOOLS` and `SHELL_TOOLS`; anything else falls through to `other`, which the engine allows unjudged
   (**UNMAPPED TOOLS ARE NOT GUARDED**). So list every tool of yours that writes or runs there, not only the
   first one you captured. The Claude and Codex adapters in
   `tools/skyguard/adapters/` are the reference for payload mapping; read them, do not import them.
   An unfilled adapter refuses every call. The template does the rest: it runs the engine, checks the answer as
   section 4 says, and on any failure renders your block. It writes UTF-8 bytes, so a reason the console's
   code page cannot encode still arrives, and if emitting your block fails it falls back to `STATIC_BLOCK`.
   Your slots are trusted: a slot that writes to `sys.__stdout__` or to file descriptor 1 directly bypasses the
   template's capture of ordinary output, and conformance can catch it only through the block-shape rows.
4. **Register the hook** in your agent's own configuration: run `python tools/adapters/<name>/adapter.py` before every
   tool call that runs a command or writes a file, matching **all** such tools. Use the absolute interpreter path.
   Also route the MCP calls you can see (section 4), and map each to the three-part name.
5. **Timeouts.** The adapter's own deadline is 25 s (override: `SKYGUARD_ADAPTER_TIMEOUT_S`). Your agent's hook
   timeout **must be longer**: if it is shorter, a hung engine makes the agent give up and *run the call*. The
   toolkit's repository tests show it happening (`test_hook_timeout_shorter_than_the_adapter_fails_open`). The
   report validator holds you to it: unless a failed hook blocks, a `hook_timeout_s` under 30 s keeps the status
   partial (section 3).
6. **Conformance.** Write `cases.json` and `profile.json` from your captured payloads, then run
   `python tools/adapter_conformance.py run --adapter-dir tools/adapters/<name> --profile profile.json --cases cases.json`.
   It runs against a scratch copy and must print `CONFORMANCE PASS`.

   `profile.json` says what a block and an allow look like to *your* agent:

   ```json
   {"block": {"exit": 0, "stdout_regex": "\\{\"decision\": \"block\".*", "stderr_regex": null},
    "allow": {"exit": 0},
    "write_tools": ["write_file", "edit_file", "apply_patch"],
    "shell_tools": ["run_shell"]}
   ```

   A regex is a full match over the whole output stream (leading and trailing whitespace removed, CRLF read as LF),
   so write it to describe the *decision*, not a word an allow's free text could contain; junk in front of a block
   shape is not a block. `write_tools` lists every tool that writes or edits a file (at least one) and
   `shell_tools` every tool that runs a command, by your agent's own names; a tool may not be in both. A non-empty
   `shell_tools` makes the shell pair mandatory. Leave it empty only if your agent has no shell tool: the run then
   ends `CONFORMANCE PASS` followed by `SHELL UNGUARDED`, and you must report that gap. Every run also prints
   `UNMAPPED TOOLS ARE NOT GUARDED` with the lists it probed: a tool missing from them was never tested.

   `cases.json` is `{"cases": [ {"id": ..., "expect": "block|allow", "payload": ...}, ... ]}` with the ids
   `decoy-plugin-write`, `benign-write`, `empty-payload`, `garbage-payload` and, when `shell_tools` is not empty,
   `decoy-plugin-shell` and `benign-shell`. Each decoy case also names its tool (`"tool": "write_file"`), and
   **every** listed tool needs a decoy case of its own: name the extra ones `decoy-plugin-write:<tool>` or
   `decoy-plugin-shell:<tool>`. Ids must be unique. A `payload` is your agent's own captured JSON (or text); the
   placeholder `{ROOT}` stands for the scratch install, and `payload_hex` gives raw bytes. Quote a shell path that
   may contain a space. The decoy is always a file that does not exist, in `Data/`.

   Beyond your cases the tool replaces the engine with ten broken ones (`engine-crash`, `engine-silent`,
   `engine-garbage`, `engine-marker-garbage`, `engine-wrong-version`, `engine-allow-but-exit-1`,
   `engine-no-marker`, `engine-unknown-decision`, `engine-hang`, `engine-missing`) and requires a block whose reason is visible, then with two valid ones: `engine-deny-nonce`
   (a deny for a harmless payload: the adapter must block and the engine's own reason must reach the agent) and
   `engine-allow-valid` (an allow for the decoy: the adapter must obey the engine, not its own opinion of the
   payload).

   If your agent's adapter is not Python, pass `--port` (and `--command` to say what to run: a JSON list of argv
   strings such as `["node", "{DIR}/adapter.js"]`, with `{PY}` and `{DIR}` as placeholders). A command string is
   refused, never split, so a path with a space stays one argument. `--command` is refused without `--port`. The
   structure check is then skipped and the tool says so (`UNVERIFIED STRUCTURE`), and your port must honour
   `SKYGUARD_ADAPTER_TIMEOUT_S`.

## 7. Required proof before any claim

1. `CONFORMANCE PASS` from section 6.
2. **The live write canary.** In a *real* session of your agent, on the surface you will use, ask yourself to
   write `Data/Canary.esp` (a decoy; never an existing plugin) with your write tool, and again with each other
   tool in `write_tools`. Each call must be refused with the reason visible, and the file must not exist
   afterwards. Record it as `proof.live_canary_blocked`.
3. **The live shell canary.** In the same session, run `echo x > Data/Canary.esp` through your shell tool (each
   tool in `shell_tools`). It must be refused the same way, and the file must not exist. Record it as
   `proof.live_shell_canary_blocked`. Conformance only sends your adapter synthetic shell payloads; this is the
   only proof that your agent's hook really runs before its shell tool.
4. **The broken-hook canary.** Breaking a file that the live hook does not run proves nothing, so change what the
   hook runs. Copy the whole install to a scratch folder. In the copy, replace `tools/skyguard/run.py` with a
   script that only sleeps for 120 seconds. Register your hook for that scratch folder so that it runs the
   **copy's** `tools/adapters/<name>/adapter.py` (if your agent's hook settings are global, point them at the copy
   for this test only, then back). Start a *fresh* session in the scratch folder and repeat the write canary there.
   After about 25 s it must be refused with `GUARD INERT` in the reason, and the file must not exist. If the call
   ran instead, your agent gave up on the hook first: record that in `failed_hook_behaviour` and `hook_timeout_s`.
   Delete the scratch folder afterwards, and never break the real install.
5. Fill `proof` in the capability report and run its validator. Only its derived status may be quoted.

What conformance does **not** prove. The toolkit's own toy-agent test shows that the template, the conformance tool
and the engine work together for two invented agents. It proves nothing about your agent's hook: whether the agent
calls your adapter, on your surface, for every tool, and honours your block shape is proved only by the live
canaries (`proof.live_canary_blocked` and `proof.live_shell_canary_blocked` in the report). A report without both
stays **partially supported: Layers 0-2**, and so does an agent with no shell tool.

No toolkit command certifies an adapted agent yet: `tools/hook-canary.sh` and the engine's health report know
only the two named agents. Your adapter writes heartbeats to
`.claude/backups/.hook-heartbeat/adapted/<name>/` so that a later toolkit release, or the user, can; the
engine's own record for your calls is `neutral-pre-tool`, kept apart from the Codex hook's.
Until section 7 passes, say **partially supported: Layers 0-2**.

## 8. Surfaces

A surface (terminal, desktop app, IDE extension) is claimed only after a live probe shows its hooks fire.

<!-- facts:begin -->
| Surface | Status | Source |
|---|---|---|
| Codex CLI (interactive and `codex exec`) | claimed | [src: README.md::Surfaces] [spec: D14] |
| Codex desktop app and IDE extensions | not claimed: some Windows Codex executions emit no hook calls | [spec: E24] [src: README.md::Layers 0-2 only] |
| Claude Code CLI | claimed | [spec: E27] |
| Any other agent or surface | not measured; shipped only once a live test passes | [spec: D8] [spec: D14] |
<!-- facts:end -->

## 9. Gaps and upstreaming

List every gap in the report. These are the ones the toolkit already knows about:

<!-- facts:begin -->
- The engine refuses a write request that names no file, rather than allowing it | [src: tools/skyguard/engine.py::NO_PATHS]
- The neutral entry refuses unknown fields and a repeated key, and answers with the `SKYGUARD-OK` marker | [src: tools/skyguard/neutral.py::unknown field] [src: tools/skyguard/neutral.py::duplicate field]
- The neutral entry refuses `agent` values `claude` and `codex` | [src: tools/skyguard/neutral.py::must not be claude or codex]
- The neutral entry allows kind `other` and labels it not judged | [src: tools/skyguard/neutral.py::NOT JUDGED]
- The neutral entry sends kind `mcp` to the DevBench rules | [src: tools/skyguard/neutral.py::engine.evaluate_mcp]
- The neutral entry allows an MCP call to any other server and labels it not judged, status `partial` | [src: tools/skyguard/neutral.py::UNJUDGED_MCP = "NOT JUDGED: only a DevBench MCP server is guarded"]
- Only an MCP server whose name contains `devbench`, ignoring case and `-` `_` `.` (`DevBench-vr`, `dev-bench`, `skyrim-devbench`), is judged, so a DevBench server registered under any other name is allowed unjudged; an adapter must map such a server to such a name itself. | [src: tools/skyguard/mcp_rules.py::"devbench" in re.sub(]
- The MCP judge reads every string in a DevBench call, whatever the tool, for a refused console command (`smp reset`); the save-over-a-save confirmation reads only the `console`, `game` and `papyrus` tools and scenario steps (`steps`), so a save made by a `replay` of a recording is not confirmed | [src: tools/skyguard/mcp_rules.py::for s in _strings(args):] [src: tools/skyguard/mcp_rules.py::elif tool == "game"]
- Ops that take a backup are exactly `update`, `delete` and `move_from` | [src: tools/skyguard/engine.py::_BACKUP_OPS = ("update", "delete", "move_from")]
- The engine's own telemetry role for the neutral entry is `neutral-pre-tool` | [src: tools/skyguard/neutral.py::TELEMETRY_ROLE = "neutral-pre-tool"]
- The engine's own health tooling knows only the agents `claude` and `codex`; it does not certify an adapted agent | [src: tools/skyguard/health.py::COUNTING_HOOKS]
- The conformance tool refuses `--command` without `--port`, because a custom command is not the file whose identity it hashes | [src: tools/adapter_conformance.py::--command needs --port]
- A report's status is derived by the validator and never read from the report | [src: tools/adapter_conformance.py::The ONLY place a status string is produced]
- Unless a failed hook blocks, the validator keeps the status partial while the agent's hook timeout is below the adapter's deadline plus a margin | [src: tools/adapter_conformance.py::HOOK_TIMEOUT_MARGIN_S = 5] [src: tools/adapting/adapter_template.py::ADAPTER_TIMEOUT_S = 25]
- Conformance requires a decoy case for every tool the profile lists, and probes no other tool | [src: tools/adapter_conformance.py::needs its own] [src: tools/adapter_conformance.py::UNMAPPED_NOTE = "UNMAPPED TOOLS ARE NOT GUARDED"]
- Conformance runs the adapter from an argv list and never splits a command string | [src: tools/adapter_conformance.py::a command string is never split]
- The template emits UTF-8 bytes and falls back to its static block if emitting fails | [src: tools/adapting/adapter_template.py::falls back to STATIC_BLOCK]
<!-- facts:end -->

Two more gaps are not code facts but limits of this method: a slot that writes straight to the agent's output
bypasses the template's capture (section 6), and a decision the engine has not been taught to judge, such as an
`other` tool, stays unguarded however well the adapter works.

To offer your adapter upstream, open an issue with: the agent's name and version, the surface, the capability
report, the conformance output, the canary evidence and the slots block. Never ask the toolkit to call you
supported on the strength of your own report.
