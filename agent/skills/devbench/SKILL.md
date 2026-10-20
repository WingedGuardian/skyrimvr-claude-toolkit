---
name: devbench
description: Drive the RUNNING game through DevBench (a localhost REST/MCP SKSE plugin) with tools/devbench-cli.sh -- liveness, console exec with output, Papyrus calls, HUD notes, scenarios. Use for any in-game test, any live-state question, and any "it doesn't work in game" problem while the game is running.
---

# DevBench — the live in-game test channel

**The single biggest time sink in modding is the in-game test loop:** change something, ask the user to
launch the game, have them trigger it, wait, hear "it didn't work", guess again. DevBench breaks that
loop. It is a dev-only SKSE plugin (alandtse, Nexus SE **181326** — same author as Engine Fixes VR) that
runs a **localhost REST + MCP server inside the running game**, so the agent can inspect live state, run
console commands *and read their output*, call Papyrus functions and get the return value, dismiss
modals, and drive scripted scenarios — directly, while the user just keeps playing.

**NOT bundled** (GPL-3.0-or-later; the toolkit ships only the wrapper). It changes no gameplay and
writes no save data.

**It is a mod, not one of the `tools/` dev utilities — so do NOT install it for the user.** Every
other optional tool lives in `tools/` and never touches the game; DevBench is an SKSE plugin that ends
up in `Data/SKSE/Plugins/`. Hand-copying a DLL there bypasses Vortex/MO2, leaves the file untracked,
and on a managed install a later deploy or purge can clobber it. If the user wants DevBench, they
install it through their own mod manager like any other SKSE plugin; the wrapper below works the
moment it's present.

```bash
bash tools/devbench-cli.sh alive                       # is the GAME running, paused, hung, or not loaded?
bash tools/devbench-cli.sh health                      # raw off-thread liveness + instance identity
bash tools/devbench-cli.sh ping                        # server self-test only -- NOT proof of liveness
bash tools/devbench-cli.sh state                       # {plugin,version,vr,playerLoaded,frame,pid,port,exe}
bash tools/devbench-cli.sh inspect vm                  # Papyrus VM health — freeze diagnosis
bash tools/devbench-cli.sh exec "player.getav health"  # console exec + capture + read output
bash tools/devbench-cli.sh call Actor IsInCombat '[]' '{"form":"0x14"}'
bash tools/devbench-cli.sh notify "Test 3 of 5: swing now"   # HUD narration
bash tools/devbench-cli.sh tool menu '{"action":"accept"}'   # raw: any tool, any JSON
```

**Hard requirement: the game must be running.** The server only exists while the game is genuinely
running with a save loaded. In VR that normally means the headset is on (Skyrim VR errors at VR-init
without one) -- or headless mode, below. When the game is closed the port is dead — fall back to the
offline tools (xelib, Spriggit, ReSaver, PyNifly).

**Headless Skyrim VR (no headset): `bash tools/vr-headless.sh on`.** It points the game at SteamVR's
null driver (replacing an OpenComposite `openvr_api.dll` with SteamVR's own), so the game boots and
DevBench is drivable with nobody in the headset. Close SteamVR first; then start SteamVR, let it
settle (if room setup opens, wait for it to close: launching while it runs gets the game evicted),
and launch the game. `bash tools/vr-headless.sh off` restores both files byte-exact and verifies
them; it is safe to repeat and after a crash, so run it before the user plays again. **Valid only for
pose-independent tests** -- scripts, records, console, frame rate. Anything that depends on where the
head and hands are (HIGGS, VRIK, PLANCK, hand collision) needs the real headset. `emu on|off` swaps in
a pose-emulating driver (its hands track) once the user has built it (`tools/vr-emu-driver/README.md`:
fetched from a pinned upstream commit, never shipped). Skyrim VR only.

**Port is deterministic per runtime: SE/AE `8920`, VR `8921`** (DevBench iterates if the port is busy
and writes the bound port to `Data/SKSE/Plugins/devbench/runtime.json`, which the wrapper reads).
Override with `DEVBENCH_PORT`. MCP clients can point at `http://127.0.0.1:<port>/mcp`; the REST path
the wrapper uses works any time the server is up, with no reconnect.

**Tools:** `ping` · `console` (exec/read) · `inspect` (`state|health|vm|scene|mods|player|inventory|
quests|effects|refs`) · `papyrus` (list/describe/**call**) · `menu` (list/describe/**accept**/open/close) ·
`game` (save/load/list) · `scenario` (timed steps with `waitFor`/`waitUntil`, not guessed sleeps) ·
`camera` · `record`/`replay`.

**Newer tools (from the DevBench 1.16–1.28 changelog, README and tool descriptions; this toolkit has not
exercised them against a live game).** `game` gained `loadLast` (fire-and-forget; reload the latest save), `advanceTime`
(1.17), `setTimeScale` / `getTimeScale` (1.22; scale 0.1..3.0, leased, refused with 409 while a
recording runs) · `wait` and `sleep` (1.16; step pacing, incl. in-game hours) · `input` (1.18-1.24;
`capabilities` / `observe` / `keyboard` / `vrTrackedSet`: **synthetic input, VR-specific and mutating**
-- treat like `record`/`replay`) · `camera orbit` (third person only; 501 on VR) · `inspect kind=extensions`
· `menu invoke`. **`scenario` now FAILS when an inner tool errors (since 1.16, #72)**; before that it
reported success, so a scenario that used to "pass" may now fail honestly -- read the step it names.
`devbench-bridge.exe` (stdio, shipped with DevBench) is the recommended MCP route for a session that must
survive game restarts; **register it under a server name containing `devbench`** (any case; `dev-bench`
works too), because the toolkit's MCP guard matches a server name containing devbench and would not see a
bridge named anything else.

**Versions.** The toolkit's wrapper needs **1.11.0+** for `alive` (off-thread health since 1.12.0; older
builds fall back to a frame diff). Everything it documents (ports, `POST /api/tool/<tool>`,
`/api/health`, `/api/tools`, console exec/capture/read, 400/504 meanings) is unchanged in 1.28.0 per the
changelog and a diff of the tool descriptions against 1.24.0; 1.28.0 (2026-10-11) is the newest release this was
read against; the one behaviour that did change is the Papyrus `call` rule below. **Keep the VR Address Library
current before updating DevBench:** DevBench 1.19+ is built on CommonLibSSE-NG 8.3.0 and needs a current
Address Library. The newest VR Address Library seen when this was written is **0.276.1** (2026-10-04);
a stale Address Library is the usual reason an SKSE plugin fails to load.
**Console capture:** 1.19 fixed capture when the output buffer is empty (#96) and 1.24 changed how
console output is captured (#117). 1.28 (#130) makes `console exec` with `capture:true` return the
output itself as `lines` (plus `completed` and `lossPossible`); a following `read` returns the same output
again, so the wrapper's exec+sleep+read pair still works unchanged. `count:0` was common on older builds
and is no longer assumed normal -- many commands still print nothing, so verify by re-reading state.
1.24 also warns on unknown request keys; the wrapper sends only documented keys.

**Papyrus `call` changed in 1.25 (#118) -- omitted optional arguments.** Through 1.24 a short argument
list was padded with neutral values (`None`/`0`/`false`/`""`), which silently turned reference ops such as
`MoveTo`/`Disable`/`Kill` into no-ops. Since 1.25 an omitted trailing argument is filled from a table of
known vanilla/SKSE defaults (`PlaceAtMe` `aiCount=1`, `MoveTo` `abMatchRotation=true`, ...) and **any other
omitted argument refuses the call with 400 and nothing runs**. Pass every argument explicitly, or add
`"fillNeutral":true` (raw `tool papyrus`) to accept the old neutral padding; the answer lists every fill
under `filledArgs` and warns about any neutral guess. 1.28 also accepts `null` for `None` (object and array
parameters only; 400 otherwise, #129), resolves forms behind custom scripts such as a quest script (#128),
and a 504 now says whether the call was already dispatched (it may still run).
**Other changes since 1.24 that nothing here depends on:** `game setTimeScale` returns a terminal snapshot
with a `generation` and `state` and waits up to `waitMs` (default 2000); a request still ramping is an
answer, not a 504, unless `requireReached:true` (1.26+); `inspect` gained `lights`, per-hand `hands` data
and, on actor refs, alive/3D-loaded/attack-state/stamina (1.25-1.28); `camera` gained `frame` and
`camAngles` (1.25); synthetic keys carry an empty user event so menus receive them (1.28, #127).

**Check `alive` first, and believe it over `ping`.** Every tool call above runs *on the game's main
thread* and 504s after 5s if that thread is stuck — so tool calls fail in exactly the situation you
most need diagnosed. `GET /api/health` (DevBench **1.11.0+**, answered off-thread since **1.12.0**) is
the one endpoint that always replies; `alive` samples it twice and separates the four states that a
raw `ping` flattens into one: **running** · **paused/loading** (frame frozen, task queue draining —
not a hang) · **hung** (frame frozen *and* `pendingTasks` piling up while `lastTaskFrame` stalls) ·
**not in game** (`frame < 0`, no save loaded). It also prints the answering instance's
`pid`/`exe`/`vr`, so if you have two Skyrims open you catch a wrong-port misattach in one call instead
of chasing phantom results. On older DevBench the wrapper falls back to the legacy frame diff and says
so. MCP clients get the same signal as `inspect kind=health`.

**Standing practice — automate the test, don't delegate it.** On any in-game problem, ask "how do I
test this myself instead of asking the user to?" Build a *parameterized* harness (a global Papyrus
function callable via `call`, logging to a file you read off disk) rather than a one-shot, so sweeping
a value or swapping a mechanism is another DevBench call — not another edit → recompile → user reload.
Reserve manual testing for the final confirmation gate. See KNOWLEDGEBASE.md for the hazards
(`game save` deadlock, paused-state semantics, heavy console commands) — read them before your first
live session.

## bench-vr — frame rate before and after a change

```bash
python tools/bench-vr.py run --label before --coc RiverwoodSleepingGiantInn --coc WhiterunOrigin
# make the change, restart the game, return to the same point, then:
python tools/bench-vr.py run --label after  --coc RiverwoodSleepingGiantInn --coc WhiterunOrigin
python tools/bench-vr.py compare <before.json> <after.json>
```

It pins timescale 0, one game hour, one forced weather and god mode, travels to each location, and
derives fps from the off-thread `/api/health` frame counter (a stall shows as ~0 fps, not a gap).
**Window 1 at each location is discarded**; windows 2+ are pooled. God mode, timescale and game hour
are put back even when the run fails (anything it could not restore is printed with its command);
the forced weather is not, so measure where reverting is easy. `compare` refuses two runs taken under
different conditions. Results go to `.claude/backups/bench-vr/`. Default location: one vanilla
`coc WhiterunOrigin`. Trust a number only when nothing caps it: a refresh cap (a headset's frame
synthesis or a fixed-rate mode) makes every location read the same value, and then any change
"reproduces" it -- an interior reading above the exteriors is what shows nothing is capping.

**Cautions, each one measured on a live game:**
- **Never `coc` into the cell the player is already in.** Re-entering it re-fires its Story Manager
  checks, which crashed the game. bench-vr skips that location's travel and measures it in place.
- **Toggling shader-framework features live is not free.** Turning Light Limit Fix OFF at runtime hung
  the GPU (driver reset, game aborted). Disabling many features at once collapsed rendering to ~0.5 fps
  until they were re-enabled. One change per launch, measured before and after, is the safe way.
