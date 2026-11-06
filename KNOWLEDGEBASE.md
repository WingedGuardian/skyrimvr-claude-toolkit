# Skyrim Modding Knowledgebase (SE / AE / VR)

A living document of quirks, gotchas, and hard-won lessons about Skyrim modding (SE / AE / VR). Everything here was either discovered through debugging or verified via web research. VR-specific behavior is called out as such. Always consult this before making changes.

---

## Papyrus Scripting

### Log Noise (Not Bugs)

**"VM is freezing..." / "VM is frozen"** — These lines appear in **every** Papyrus log on normal game exit and save. They are not a crash, not a script error, and have no diagnostic value. Ignore them completely when troubleshooting.

**"Unable to bind script X to alias Y on quest Z because their base types do not match"** (measured 2026-11-04) — the plugin attaches script `X` to a ReferenceAlias, but `X.psc` does not extend `ReferenceAlias` (for example `Scriptname X Hidden`, a holder of global native functions that extends nothing). The script never runs on that alias; if the mod calls its functions as globals and keeps state elsewhere (SKSE co-save), the line is cosmetic. Fixing it means editing the alias's VMAD in the third-party plugin.

**"Script X does not exist" for a script the plugin references** — check whether that mod's own BSA ships it. When the mod's other scripts are present and this one is not, it is an author-side packaging miss, not a load-order problem.

### Script Lifecycle

**OnInit vs OnLoad:**
- `OnInit` fires only once when the script first initializes — never again on subsequent game reloads
- For Quest/Alias scripts: `OnInit` fires at game startup and whenever the quest starts (due to reset)
- For other objects (Topic Infos, Perks, persistent refs): `OnInit` fires at first load into the session
- All properties and variables are reset to default values before `OnInit` fires
- A quest script has no `OnLoad` event, and `OnLoad` on an ObjectReference means its 3D loaded, not that a save did — use `RegisterForSingleUpdate`/`OnUpdate` or `OnGameReload` for reload detection
- SKSE's `ActiveMagicEffect.psc` declares the Actor events, including `OnRaceSwitchComplete`, so an ability's effect script can react to vampire turn, cure and Vampire Lord transform (all race swaps) without a ReferenceAlias script
- `OnInit` does NOT fire again after save/load (quest must be reset)

**Persistence and Save/Load:**
- Auto properties receive their default values after save/load
- **Non-auto properties do NOT receive master file values after save/load** — they remain blank
- If a variable references a Form from a removed mod, it becomes "missing" and stays missing even if the mod is restored
- FormList entries added by scripts persist through save/load
- ObjectReferences are temporary by default — only the base Form is stored in saves. Use `PlaceAtMe` with `persist=true` or ReferenceAlias for persistence

**Orphaned Scripts:**
- Occur when objects are removed before script threads finish, or from uninstalled mods
- Don't cause serious save bloat but indicate cleanup issues
- Removing a mod leaves its `RegisterForUpdate` handlers registered, causing periodic errors

*Sources: [CK Wiki: OnInit](https://ck.uesp.net/wiki/OnInit), [CK Wiki: Save Files Notes](https://ck.uesp.net/wiki/Save_Files_Notes_(Papyrus))*

### Threading Model

- Papyrus is **single-threaded per script instance**. Concurrent calls queue (FIFO).
- Default `fUpdateBudgetMS` is 1.2ms per frame for all scripts combined.
- The VM is limited to ~100 operations per task.
- If budget is exceeded, execution pauses until next frame.
- `iMaxAllocatedMemoryBytes`: total memory limit for all stack frames. If exceeded, VM waits rather than allocating.
- A script stack dump occurs after 5000ms continuous overload — incoming events are dropped (not `Wait()` continuations).
- PayloadInterpreter bypasses Papyrus entirely — can fire simultaneously with running scripts.

**Race Conditions:**
- Only one thread can operate on a script at a time, creating queues
- Deadlocks can occur when locking multiple locks — define a convention for lock order
- **Out-of-order event delivery**: `OnTriggerLeave` can arrive before `OnTriggerEnter` — use counters, not booleans
- Multiple scripts calling the same function create queue delays — popular scripts become progressively slower

**VM Freezing:**
- VM freezes when game is paused (menu, console)
- Toggling scripts with console pauses all scripts; they resume on save action or stack dump

*Sources: [CK Wiki: Threading Notes](https://ck.uesp.net/wiki/Threading_Notes_(Papyrus)), [Papyrus INI Settings Analysis](https://thallassathoughts.wordpress.com/2016/09/16/myths-and-legends-papyrus-ini-settings/)*

### RemoveSpell vs DispelSpell

- `RemoveSpell()` removes a spell from the actor's spell list but does **NOT** fire `OnEffectFinish` on the magic effect script.
- `DispelSpell()` cancels the active magic effect and **DOES** fire `OnEffectFinish`.
- **CRITICAL: `DispelSpell()` explicitly EXCLUDES ability-type spells.** It only works on non-ability active effects (spells, enchantments, potions). For abilities, you MUST use `RemoveSpell()`.
- If a magic effect script has cleanup logic in `OnEffectFinish` (restoring weather, re-enabling AI, deleting placed objects), you **must** use `DispelSpell()`, not `RemoveSpell()` — but only for non-ability spells.
- For ability-type spells (Type=Ability, Cast=Constant Effect): use `AddSpell()` to apply, `RemoveSpell()` to remove. `DispelSpell()` will silently do nothing.
- `AddSpell()` on an ability the actor already has is a safe no-op — no stacking, no error.

### Utility.Wait() Reliability

- `Wait()` is frame-bounded — minimum resolution is one frame (~11ms at 90fps).
- Sub-100ms waits (e.g. `Wait(0.05)`) are unreliable under script load. They can fire 2-3x late.
- Under extreme script load (700+ plugins), jitter can reach 200-500ms on rare occasions.
- `Wait()` is never skipped — the engine defers but always resumes.
- **Prefer `RegisterForSingleUpdate`/`OnUpdate`** over `Wait()` — safer, avoids stack issues with long waits.
- For FX sequencing, merge any sub-100ms gaps into the adjacent step.
- For combat-critical timing (18-20ms precision), Papyrus is inadequate — use native C++ (PayloadInterpreter, SKSE plugin).

### Spell.Cast() vs PayloadInterpreter @CAST

- `Spell.Cast()` goes through the Papyrus VM scheduler. Subject to per-frame script budget (1.2ms default).
- PayloadInterpreter `@CAST` is native C++ — fires in the same engine tick as the animation event, zero Papyrus overhead.
- `@APPLYSPELL` bypasses all casting mechanics (no resource costs, no casting animation). Equivalent to `AddSpell(spell, false)` for abilities.
- `@UNAPPLYSPELL` removes the applied effect. Equivalent to `RemoveSpell()` — NOT `DispelSpell()`, since PI primarily uses this for ability-type spells which DispelSpell excludes.
- `Spell.Cast()` CAN stack effects if called multiple times. `AddSpell()` for abilities CANNOT stack.

### PlayIdle() and Animations in VR

- `PlayIdle()` on the player is unreliable in VR. VRIK overrides the upper-body skeleton via IK from controllers.
- Even if the idle fires, VRIK fights the pose on head and arms in real-time.
- `PlayIdle()` makes condition checks; `SendAnimationEvent()` bypasses some but is still state-dependent.
- Can't play idle if in "Weapon Drawn" state; can't use `attackStart` if in Idle state.
- Conditions in behavior files prevent certain animations from firing.
- If a mod depends on animation annotation events (e.g. PayloadInterpreter payloads), and those events come from a `PlayIdle()` animation, the entire chain fails in VR.
- **Workaround**: bypass animation dependency entirely with timed Papyrus scripts.

### GetFormFromFile()

- `Game.GetFormFromFile(formID, "plugin.esp")` looks up forms at runtime without needing script properties.
- Avoids VMAD property complexity when you'd otherwise need 20+ spell properties.
- The formID parameter is the **local** FormID (without the load-order prefix).
- Returns `None` if the plugin isn't loaded — **always check before casting**.
- Form ID must include `0x` prefix (e.g., `0x0001ABCD`).

### ESP Dependency Rule: Own Your Records

**Never use `GetFormFromFile()` to borrow records from another mod.** Soft runtime dependencies (`GetFormFromFile("SomeMod.esp")`) silently break when that mod is disabled, uninstalled, or load-order shifted — the call returns `None` with no error until the feature simply doesn't work.

**Rule**: If you need a record (SNDR, SPEL, MGEF, etc.) from another mod, copy it into the ESP you're actively developing. Create your own version of the record in the ESP you're actively developing and reference that instead.

- Hard master dependencies (listed in MAST records) can't always be avoided — they're acceptable when you're overriding or extending a specific mod's content.
- Soft `GetFormFromFile()` dependencies on other mods are almost always avoidable: copy the WAV/record into your own ESP.
- **Not an exception**: `Skyrim.esm` and the core masters are always loaded, but another plugin can still override their records. A bare FormID is fine for a marker or anchor; copy any vanilla record whose behaviour your mod relies on.

### Magic Effect Lifecycle

**OnEffectStart / OnEffectFinish:**
- `OnEffectStart` fires when effect activates on target
- `OnEffectFinish` fires when effect ends
- **CRITICAL**: When `OnEffectFinish` fires, the effect may already be deleted — calling methods on it can fail

**Casting Types (all effects on a spell MUST match):**
- Constant Effect (0): always active like an ability
- Fire-and-Forget (1): charge then fire on release
- Concentration (2): sustained while held, drains magicka continuously

**Delivery Types:**
- Self: applies to caster
- Touch: applies on contact
- Aimed/Projectile: applies to target hit

**Spell vs Ability vs Power:**
- Spell: cast from menu, costs magicka
- Ability: passive, always active (Constant Effect, no duration)
- Power: equips to shout key; Greater Power = 1/day, Lesser Power = unlimited or custom cooldown
- Each behaves fundamentally differently for cooldowns, stacking, and activation

### RegisterForSingleUpdate vs RegisterForUpdate

- `RegisterForUpdate`: fires continuously at specified interval (creates repeating loop)
- `RegisterForSingleUpdate`: fires exactly once, must be called again to repeat
- If mod is removed, `RegisterForUpdate` handlers still fire → periodic errors in log
- **Safer to chain `RegisterForSingleUpdate` calls** rather than relying on `RegisterForUpdate`

### State System Quirks

- `OnBeginState` does NOT fire if already in that state
- Calling `GoToState("")` inside `OnUnload` triggers `OnEndState` but `Self` is None/Null → crash
- **Workaround**: move `GoToState("")` call to `OnLoad` instead

### Actor Value Functions

- `SetActorValue`: sets **BASE** value (maximum), not current value
- `RestoreActorValue`: adds to current value up to base limit, doesn't change base
- `ModifyActorValue`: changes **BOTH** current AND base value simultaneously
- `DamageActorValue`/`RestoreActorValue` for current-only changes
- `SetActorValue` should only be used intentionally for base value changes

### Equipment Functions

- `EquipItem`: if actor doesn't have item, gives it first (better to `AddItem` first)
- Enchanted weapon charges reset to full if actor not in loaded cell
- `abPreventEquip` flag doesn't work on NPCs (only Ammo)
- `QueueNiNodeUpdate` (SKSE) needed after `EquipItem` to force visual update

### SetVehicle Issues

- `SetVehicle(None)` does NOT reliably dismount — players may remain stuck
- Setting as vehicle for custom creatures crashes without matching `.hkx` animation files
- No `IsRidingHorse` function — workaround: check `bIsRiding` animation value
- In VR: causes HMD desync (game moves player position while HMD anchor stays fixed)

### Performance Pitfalls

**Most expensive operations (in order):**
1. Native function calls (biggest bottleneck)
2. Accessing properties on different scripts (involves function call)
3. String operations
4. Complex conditional logic

**Optimizations:**
- Cache property values in local variables
- Minimize native calls
- Use auto properties when possible
- Default arrays limited to 128 items (use JContainers/PapyrusUtil for more)
- Arrays are pass-by-reference — modifying elements affects all references to that array

### Null Reference Patterns

- "Cannot call X() on a None object" — attempting operation on null value
- Always check for `None` before operations
- `IsHostileToActor` crashes to desktop with NONE object (vanilla bug)

### Condition Function Gotchas

- **OR has precedence over AND** — `A AND B OR C AND D` evaluates as `(A AND (B OR C) AND D)`, NOT `(A AND B) OR (C AND D)`
- More than 3 AND-pairs causes performance hits on high-demand items (spells, effects)
- Some condition functions lack Papyrus equivalents (e.g., `IsPlayerInRegion`)
- `GetIsRace` condition doesn't always recognize race properly — use `GetRace()` script function instead

*Sources: [CK Wiki: Condition Functions](https://ck.uesp.net/wiki/Condition_Functions), [Beyond Skyrim: Scripting Best Practices](https://wiki.beyondskyrim.org/wiki/Arcane_University:Scripting_Best_Practices)*

### `Math.Acos` / `Math.Asin` and NaN: neither a clamp nor an inclusive range check catches it

`Math.Acos(x)` errors ("Argument for acos out of range") for any x outside [-1, 1], **including NaN**. A
zero-length vector normalised and fed into a dot product produces NaN. The usual clamp lets it through,
because `NaN > 1.0` and `NaN < -1.0` are both false. MEASURED on one Skyrim VR install: the Papyrus VM also
returned **true** for the inclusive test `x >= -1.0 && x <= 1.0` with x = NaN, so an inclusive membership
check is not a finite-value check either; strict comparisons rejected it. Validate with strict
comparisons, handle the exact endpoints explicitly, and then call `Acos`/`Asin`. In a polling `OnUpdate`
an unguarded call errors on every tick, and that error storm can degrade the VM until the game freezes.

---

## VR vs SSE Differences

### Engine Foundation

- Skyrim VR is functionally based on SSE but is a **separate engine build** — not compatible with SSE or Oldrim
- Game mode in XEditLib: `gmSSE=4` (use this for VR — there is no VR-specific mode)
- Registry key: `HKLM\SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition` (SSE key, not VR)

### SKSE: SE vs VR

- SKSEVR is a **completely separate build** from SKSE64 (SE)
- Address libraries are entirely different — thousands of addresses differ between SE and VR
- Manual offset updates required when porting SE plugins to VR
- CommonLibSSE NG supports SE, AE, and VR in a single multi-runtime build
- **#1 mod breakage cause**: mods requiring SKSE64 (not SKSEVR) won't load

### Animation System

- VR uses the same Havok Behavior graph as SSE — `.hkx` files and Nemesis patches work
- The critical difference: VR's "first person" is the HMD's physical orientation, not the game camera
- `SetAngle`, `ForceThirdPerson`, `ForceFirstPerson` do not control the HMD view
- `SetVehicle` causes HMD desync — game moves player position while HMD anchor stays physically fixed

**Skeleton:**
- VR is extremely particular about skeleton structure
- **Remove PreWEAPON and PreSHIELD nodes** (keep vanilla skeleton) or CTDs occur
- VR appears to do text-contains search for WEAPON/SHIELD nodes, gets confused by Pre* prefixes
- XP32 First Person Skeleton CTD Bugfix critical for custom skeleton users
- VRIK maps head and arms via IK rig, displays virtual body matching player movements

**Animation Replacers:**
- OAR (Open Animation Replacer): native VR support
- DAR animations auto-converted to OAR format on load
- Animation priorities must be higher than MCO/ADXP for proper playback

### Camera System

- VR uses headset tracking for primary camera (HMD position/rotation)
- `PlayerCamera` exists but is **subordinate to VR camera**
- Standard SE `PlayerCamera` interactions may not function identically
- `Game.ShakeCamera()` adds noise to the third-person camera node — in VR this is mostly inert (minor artifacts at best, nauseating at worst). Generally safe to leave in.

### Player Control Functions in VR

**CORRECTED + VERIFIED from reference VR mod (`JudgementCutEnd.esp`):**

- `DisablePlayerControls()` does **NOT** prevent VR controller thumbstick movement. VR controller input (thumbstick/wand movement) is a separate engine-level input system that bypasses the traditional control disable flags. Calling it during a scripted sequence does nothing to stop the player from walking around.
- **`DisablePlayerControls(True, True, False, False, False, True, True, False, 0)` + `Actor.SetDontMove(True)`** together is the correct approach (the second argument, abFighting, sheathes a drawn weapon: see VR Weapon Draw State). `SetDontMove` is the key — `DisablePlayerControls` alone or `SetRestrained` alone do not stop VR movement. Source: decompiled `EndMagicEndScript.pex` from `JudgementCutEnd.esp`, confirmed working in VR.
- **`Actor.SetRestrained(true)`** was tried but also NOT effective for VR movement. It operates at the actor level (not the input level), intercepting movement before the VR input reaches the character. Per CK wiki: "disables player movement, camera switching and camera rotating." Confidence: 85% (tested in VR context).
- `Game.SetPlayerAIDriven(true)` can be combined for belt-and-suspenders — overrides player control with AI behavior.
- Over-encumbrance (adding heavy items / reducing carry weight) only slows to walk speed — does NOT prevent movement.
- Paralysis effects work but trigger ragdoll physics — avoid for cinematic sequences unless using a "no-ragdoll paralysis" mod.
- `ForceActorValue("SpeedMult", 0)` prevents movement but feels unresponsive (controller input still registered, character just doesn't move).
- **VR-native solutions** (VRIK Actions, Disable Input VR SKSE plugin) work best but add dependencies.
- `EnablePlayerControls()` still needed to restore menu/combat/other input; pair with `SetRestrained(false)`.

*Source: in-game testing; CK wiki; web research on VR mod implementations*

### UI System

- UI completely reworked for VR controllers/motion input
- Menu processing limited to up/down/right/left controller signals
- SkyUI incompatible — SkyUI VR fork needed
- Book content treated as 2D HUD layer; text scaled if UI scaling applied

### ImageSpace (Post-Processing)

- Off: fixes some UI problems but creates visible darker/lighter seam between eyes
- On: fixes visual seam but breaks UI in some configurations
- Core tension between VR visual requirements and UI rendering
- Community Shaders has known broken UI issues in VR

### Weather

- `Weather.ForceActive(True)` snaps instantly — no transition. Jarring in a headset but functional.
- `Weather.SetActive()` transitions over ~2 seconds (gradual). Smoother for VR.
- Weather renders in full stereo in the headset.

### Physics & Havok

- Havok locked to **60Hz by default** — not updated for VR 90Hz requirement
- INI tweak raises rate to 90Hz for VR
- **Player collision changed**: player capsule vs enemy capsule (SE) → player capsule vs **enemy ragdoll** (VR)
- Player capsule narrower in VR than base game
- These physics differences affect melee combat significantly

### Input & Controllers

- VR controllers are primary input — mods assuming keyboard/mouse fail
- Locomotion: teleport vs smooth (VR-specific)
- Hand-based item interaction (picking up, holding, throwing via HIGGS)
- Motion archery and melee (hand gestures trigger attacks)

### VR Playroom

- Scripts running in VR playroom cause incompatibilities
- Papyrus Tweaks NG pauses non-VR-playroom scripts until player exits

### OpenComposite / OpenXR

- Skyrim VR ships with OpenVR (SteamVR) support
- OpenComposite bypasses SteamVR → directly to OpenXR
- Significant performance gains, reduced CPU/GPU load
- Especially beneficial for Quest 3 via Virtual Desktop

### Mod Framework VR Status

| Framework | VR Status |
|-----------|-----------|
| SKSEVR | Supported (separate build from SKSE64) |
| Nemesis | Works (behavior patches are version-agnostic HKX) |
| OAR (replacing DAR) | Native VR support |
| DAR (original) | VR build has `IsInCombat` offset bug → use DAR VR Fix or switch to OAR |
| Animation Motion Revolution | VR-compatible (CTD fixed in update) |
| PayloadInterpreter | VR support since v1.1.0 (Nexus) via VR Address Library |
| IFrame Generator RE | VR-compatible (CommonLibSSE-NG) |
| SCAR | Dedicated VR port exists (BFCO) |
| MCO/ADXP | Works at animation level in VR |
| True Directional Movement | No VR port, non-functional in VR |
| VRIK | VR-only — overrides skeleton IK from controllers |
| Precision | Works in VR via OAR versions |
| HIGGS | VR-only — hand interaction, gravity gloves |
| Papyrus Extender | VR version maintained by community (original author has no headset) |

### Common Mod Breakage Categories

1. **SKSE dependency without SKSEVR port** — #1 cause of breakage
2. **UI interaction assumptions** — mods assuming 2D menus/mouse input
3. **Address/function offset mismatches** — SE addresses don't map to VR
4. **Skeleton structure issues** — PreWEAPON/PreSHIELD nodes cause CTD
5. **Controller input paradigm** — keyboard/mouse mods don't work
6. **Framerate constraints** — physics/logic tuned for 60Hz fails at VR 90Hz
7. **ImageSpace/visual conflicts** — post-processing vs UI rendering trade-offs
8. **Physics collision differences** — player ragdoll vs capsule collision

### Which INI Skyrim VR actually reads, and VR shadow settings

MEASURED on one Skyrim VR install (2026-10-11/12, `getini` through DevBench after a relaunch); check yours the
same way before relying on it:
- **Skyrim VR did not read `Documents/My Games/Skyrim VR/Skyrim.ini` for `[Display]` sun-shadow settings.**
  Values written there were absent from the live `getini` answer after a relaunch. Put user overrides in
  `SkyrimVR.ini` (or `SkyrimPrefs.ini`) and confirm each one live with `getini`. The generic "Skyrim.ini,
  then SkyrimVR.ini, then SkyrimPrefs.ini" order does not by itself prove a key is read from a given file.
- **Shadows that step "bit by bit" while the player stands still, but move smoothly while walking** come from
  `fSunStaticTimeUpdateScale:Display` (live VR default 0.10). `1.0` made the steps larger; `0` made them smooth.
  With `bDisableShadowJumps=1` the `fSunShadowUpdateTime`/`fSunUpdateThreshold` pair had no visible effect.
- **An FPS-driven shadow plugin can differ between headset and desktop view.** ShadowBoost scales shadow
  distance between a minimum and maximum to hold a target FPS. With a 60 FPS target and VR running below it,
  the distance sat at the minimum, so distant shadows were missing in the headset but present in the desktop
  mirror. Setting minimum = maximum stopped the resizing. Symptom "VR and desktop view differ": look for
  plugins that react to frame rate.
- **Community Shaders (CSX 3.19.2) cloud shadows were invisible** until Dynamic Cubemaps' `CubemapResolution`
  went from 128 to 256 (the cloud-shadow texture is sized from that resolution).

### A mod's MCM Helper settings file can override its SKSE INI

MEASURED on one install: a mod configured through MCM Helper kept `Data/MCM/Settings/<mod>.ini` beside its
`Data/SKSE/Plugins/<mod>.ini`. Changing a key only in the SKSE INI left the mod's startup log reporting the old
behaviour until the same key was changed in the MCM settings file too. When a mod has both, edit both with the
game closed, and confirm the change from the mod's own log line, not from the file you edited.

---

## xEdit / xelib / ESP Editing

### VMAD (Virtual Machine Adapter) Editing

- xEdit/xelib VMAD construction from scratch is fragile — known bugs with complex property structures.
- **VMAD must be read sequentially** — no lengths/offsets provided, making selective editing impossible without corrupting adjacent data.
- **xEdit cannot add new scripts to VMAD** (only remove properties). Adding script fragments requires correct `fragmentCount` or quest aliases corrupt.
- xEdit cannot parse VMAD records with illegal struct definitions (structs within structs, arrays in structs).
- For 25+ properties, the recommended approach is: create a template in Creation Kit, then copy the VMAD subrecord.
- For simple cases (1-3 properties), xelib can handle it.
- Alternative: use `GetFormFromFile()` in the script to avoid needing properties at all.
- Mutagen (C#) has robust, strongly-typed VMAD support for programmatic use.

*Sources: [UESP: VMAD Format](https://en.uesp.net/wiki/Skyrim_Mod:Mod_File_Format/VMAD_Field), [TES5Edit Issues](https://github.com/TES5Edit/TES5Edit/issues/544)*

### XEditLib.dll (Delphi FFI)

- See the `xelib` skill for the full list of quirks (UCS-2 strings, void Init/Close, uint16 WordBool, GetResultString pattern).
- Game mode: always use `GM_SSE` (4) for both Skyrim SE and Skyrim VR.
- Registry: reads from `Skyrim Special Edition` key, not `Skyrim VR`.

**xelib element path navigation quirk**: `getValue(record, 'PARENT\Child')` with nested paths fails ("Expected 2 arguments, got 1" from Koffi FFI) when the target doesn't exist at the first level. Use two-step navigation instead:
```js
const outer = xelib.getElement(recH, 'PARENT');  // e.g. 'DATA'
const inner = xelib.getElement(outer, 'Child');    // e.g. 'Radius'
const v = xelib.getValue(inner, '');               // empty path on element handle
xelib.setFloatValue(inner, '', newValue);
```
This applies to `getValue`, `setValue`, `setFloatValue` — all path-based functions work on element handles with empty paths, not on record handles with nested paths.

### Record Structure Gotchas

**ONAM Requirement:**
- Any ESM-flagged module containing overrides of temporary CELL children must include ONAM subrecords listing all overridden records
- Without proper ONAM, the game engine ignores overrides missing from it
- ESM files cleaned with Quick Auto Clean must be resaved to fix incorrect ONAM data

**ESP Special Behavior:**
- All references in ESP files are treated as permanent regardless of their Temporary flag
- Only ESL-flagged plugins properly support temporary references

**Record Flags:**
- Drag-and-drop in xEdit doesn't always copy record flags correctly
- Partial Form Flag removes all subrecords except EditorID

### Plugin Types (ESM vs ESP vs ESL)

| Type | Load Position | FormID Range | Limit | Notes |
|------|--------------|--------------|-------|-------|
| ESM | Top of load order | Full range | 254 total ESM+ESP | Supports temp refs properly |
| ESP | After ESMs | Full range | 254 total ESM+ESP | Treats ALL refs as permanent |
| ESL | Shares FE slot | xx000800-xx000FFF (header 1.70) | 4096 | 2048 usable FormIDs at header 1.70 |

**ESL rules:**
- **Recommended: keep new records at object IDs `xx000800` to `xx000FFF`** with a header-1.70 plugin. That range
  is the one every ESL-capable runtime accepts, and IDs above `0xFFF` do not fit a light plugin at all.
- **Below `0x800` is runtime-dependent, not a rule either way.** Header 1.71 lets SE 1.6.1130 and later use object
  IDs from `0x001`; modern SE tools generate light plugins that way. Skyrim VR has no native ESL support, so
  there it depends on the ESL-support plugin you run. Observed on one VR install (MEASURED there, not
  generalised): a header-1.71 light plugin with most of its records at `0x002`-`0x7FF` loaded and played
  normally, and the ESL-support plugin's log said it emulated the old header version. On the same install a
  different header-1.71 light plugin with every record below `0x800` was followed by a heap-corruption crash at
  save load, whose cause was never established. So neither "always crashes" nor "safe" is proven: before you
  enable such a plugin, check its new-record ID range, install it on its own, and verify it on your runtime.
- xEdit's "Compact FormIDs for ESL" starts at `FE000xxx`; CK starts at `FE001xxx` (discrepancy)
- ESP→ESL conversion requires compacting FormIDs AND setting ESL flag

### Load Order & Override Resolution

- Last-loaded plugin version of a record is the "conflict winner" — overrides all lower plugins
- "Copy as override" uses the highest override visible to the target file (based on its masters)
- ModGroups hide non-winning overrides from conflict detection (prevent false positives)

### ESP Removal and Existing Saves (CRITICAL)

**NEVER remove/disable an ESP from the load order on an existing save.** The rule stands; the
reason originally written here was WRONG (corrected 2026-10-05, ledger-style note below).

- ~~Removing an ESP shifts downstream load-order indices, which resets their quest/script state.~~
  **Refuted.** The `.ess` carries a plugin-NAME table (`pluginInfo`, plus a separate
  `lightPluginInfo` list for ESL/FE plugins) and a `formIDArray` of save-time FormIDs; on load the
  game remaps save-time IDs to the current order — SKSE's `ResolveFormId` documents exactly that
  contract for co-save data ("takes a formID as it was when the save was made and writes the formID
  as it is when the save is loaded"). READ: UESP *Skyrim Mod:Save File Format*; CommonLibSSE
  *Serialization* wiki. "Remapped by plugin name" is INFERRED from that format, not quoted from a
  page — but no source supports a reset, and inserting/reordering is routine on long saves.
- **The real dangers of removal:** every form from the removed plugin is dropped from the save,
  while its script instances stay baked in (`Cannot open store for class "X", missing file?` in
  the Papyrus log); references, aliases, inventory items and other mods' scripts that pointed at
  its forms dangle. "Removing mods and continuing to use the same save game is not supported by
  the game" (CK *Save File Notes (Papyrus)*). That is why we lobotomize instead.

**Lobotomize instead:** Keep the ESP in the load order (its forms stay resolvable) but strip its functionality:
1. Remove compiled scripts (.pex files from `Data/Scripts/`)
2. Disable SKSE plugins (.dll → .dll.disabled)
3. Disable SPID distribution files (.ini → .ini.disabled) if applicable
4. ReSaver clean to remove orphaned script instances (with .pex gone, they won't re-create)

The ESP remains as a hollow shell. Only suggest removing ESPs when the user is starting a new game.

*Source: direct experience — the lobotomy approach is confirmed working.*

### Updating or Adding Mods on an Existing Save: Safety Rubric

**Classify every update by what it CHANGES, never by what the mod "is".** A "DLL mod" that also
ships `.pex` files is a script update; a "texture mod" that ships an ESP is a plugin update. Open
the archive and sort its files into the rows below, and the riskiest row present sets the verdict.

**Verdicts:** `SAFE` · `SAFE-WITH-STEPS` · `RISKY` (one at a time, checkpoint save between) ·
`DON'T` · **`UNCLEAR`, which means "not proven safe", never "probably fine".** It needs the archive,
a diff, or a DevBench test before it can move. For a mod not yet installed: `ADD-OK` ·
`ADD-WITH-STEPS` · `NEW-GAME-ONLY`.

#### 1. What the update changes, from no save footprint to deep

| Changes | Save footprint | Default verdict | Why / what to check |
|---|---|---|---|
| Textures, meshes, sounds, voice, strings, INI/JSON/TOML config | none | SAFE | Nothing is stored in the save. VR caveats still apply: PreWEAPON/PreSHIELD skeleton nodes CTD (gotcha #11), and a flat-screen `.swf` can replace a VR one. |
| Animations / behavior (`.hkx`, OAR configs) | none | SAFE-WITH-STEPS | No save data, but behavior output may need regenerating (Nemesis/Pandora). An OAR config change needs no regen. |
| SKSE DLL that writes **no co-save records** and registers no Papyrus natives | none | SAFE *if VR-loadable* | See §2. Revert = put the old DLL back, even on the same save. |
| SKSE DLL **that writes co-save records** | `.skse` co-save | SAFE-WITH-STEPS | SKSE hands each plugin its record `version` and leaves the format to the author. A DLL that ignores its own version field after a format change is UNSOURCED territory, so read the changelog for "save format" or "reset". Back up the `.skse` together with the `.ess`. |
| SKSE DLL **+ its `.pex`/`.psc` native declarations** | depends | SAFE-WITH-STEPS | The pair must move together. A mismatch logs `does not match existing signature` and the native goes unbound. |
| ESP record **field edits, FormIDs unchanged** | partial | SAFE-WITH-STEPS | Base-record edits apply on load, except for fields the save already changed (change forms). Visited cells, opened containers and met NPCs keep their baked state until a reset, so the "fix" may be inert on an existing save. That is lost value, not damage. |
| ESP: **records removed** that the save may reference | dangling refs | RISKY | Same as removal: dropped forms and orphaned instances. Needs a diff. |
| ESP: **FormIDs renumbered / compacted** | broken refs | **DON'T** | The save looks for the old IDs. ESLify: "do not compact... mods that have generated facegendata, scripts, or .seq files." |
| ESP: **ESL flag added**, IDs already within 0x800–0xFFF | slot move | RISKY | The plugin moves from a full slot to the FE light list. Remap should cover it (INFERRED), but community advice is new games only. |
| ESP: **ESL flag removed** | slot move | RISKY | UNSOURCED. Treat like the row above. |
| ESP: **master added** | none, if the master is present | SAFE-WITH-STEPS | The new master must already be installed and load earlier. Once in a save, masters can't be dropped. |
| ESP: **running quest** changed (stages, aliases, fragments) | baked | RISKY | QUST is a change form, so alias fills and stage state persist. Changes land only when the quest restarts (INFERRED). |
| `.pex`: **function bodies only** | none | SAFE | "Adding functions: no issues occur." A frame already on the stack finishes on the OLD code, and later calls use the new code. |
| `.pex`: **variable/property removed or renamed** | warning | SAFE-WITH-STEPS | The saved value is discarded with a log warning, and the warning stops after the next save. A rename counts as delete + add. |
| `.pex`: **property ADDED** | unresolved | RISKY / UNCLEAR | **Sources conflict:** the CK page says a new auto property takes the plugin's value, while a Skyrim LE report says instances already in the save stay `None`. Non-auto properties stay blank (gotcha #10). Settle it with DevBench on a save copy before trusting an update that depends on it. |
| `.pex`: **existing property's VALUE changed** in the ESP | baked | inert on an existing save | "Changing the value will not overwrite the value it has in the save game" (unless the property is `const`). The update's fix may never reach the save. |
| `.pex`: **script, state or quest removed** | orphans | RISKY | `Cannot open store for class`. ReSaver can clean unattached instances afterwards. |
| Long-running loop or registration in a changed script | persistent | RISKY | `RegisterForUpdate` and event registrations persist until unregistered. A loop on the stack keeps running old code until it exits. |

**Things that LOWER script risk:** the mod has a version-migration path, such as an SkyUI MCM
`OnVersionUpdate`, a maintenance quest on `OnPlayerLoadGame`, or an "update/reset" MCM button.
Read the decompiled source (Champollion) to confirm it actually exists.

#### 2. SKSE DLLs: the VR gate comes before save safety

"DLL-only = update whenever" is true for save safety only. A DLL can still be wrong for VR:
- **It must be a VR build**, or a CommonLibSSE-NG / CommonLibVR multi-runtime build that declares
  VR. An SE/AE-only DLL is at best refused by SKSE, which disables the feature silently, and at
  worst crashes. NG itself warns: "fundamental ABI incompatibilities between SE/AE and VR".
- **Check the VR-incompatible ledger first**, if you keep one: some releases of otherwise-VR-capable
  plugins (a Papyrus fix-up plugin, a crash logger) are known to break VR or a particular runtime.
- **Fork or rename migrations leave the old DLL behind, and SKSE loads BOTH.** A renamed fork's old
  DLL beside the new one is the classic case. After any update, check for an old-named sibling.
- **The proof is the SKSE log, not the install:** every DLL gets one result line in
  `Documents/My Games/Skyrim VR/SKSE/sksevr.log`: `loaded correctly`, `reported as incompatible
  during query|load`, or `does not appear to be an SKSE plugin`. Diff that file before and after
  each update.
- **Find stateful DLLs from the save itself:** `bash tools/cosave-cli.sh <save>.skse` lists the
  co-save records by opcode. JContainers (`JSTR`), PapyrusUtil (`STFV`/`STRL`/...), Papyrus Extender
  (`P3PE`), SPID and DbSkseFunctions are common writers; many opcodes will be unnamed.

- **A newer Address Library can be a hard prerequisite even when the mod's stated minimum is met.**
  MEASURED on one VR install: Engine Fixes VR 7.11.0 (stated minimum VR Address Library 0.235.0) stopped the
  game before SKSE finished starting, missing Address Library ID 40291, with 0.276.1 installed; with 0.279.0
  installed it loaded. Update the Address Library first when a DLL update fails early, and treat a
  stated minimum as a claim to check against the log. Engine Fixes also reads a separate
  `EngineFixesCustom.toml` for overrides, beside the `EngineFixes.toml` it ships.
- **A VR mod's Nexus MAIN file can be the SE/AE build.** One VR mod's MAIN download was SE/AE-only; the VR DLL
  came from the author's GitHub release (MEASURED, checked against GitHub's published digest). An `openvr`
  string inside a DLL is not proof of VR support: the SE/AE DLL contained one too. Judge by the plugin's
  declared runtime support and runtime guards (or by `sksevr.log` after a test start), not by a string search.

#### 3. Adding a mod not yet installed

- **Appending, or inserting mid-order, does not corrupt other mods' saved state** (see the
  correction above). **It CAN change which plugin wins records other mods depend on.** Run
  `bash tools/skyrim-winner.sh conflicts <new.esp>`, plus a check of what the new plugin newly
  WINS, before activating.
- **Adding a scripted mod is a one-way door.** Removal is unsupported (above), so the only exit is a
  lobotomy. Weigh that before adding, not after.
- **Start-Game-Enabled quests.** Community sources (AutoSEQ README) say SGE quests still start on an
  existing save, but their dialogue, scenes and some fragments need a matching `Data/SEQ/<plugin>.seq`
  plus a save/reload. **One measurement disagrees**: a single SGE quest did NOT auto-start across
  multiple loads and needed `Quest.Start()` plus a save (MEASURED once, 2026-07-09; one quest, one
  load order, not a general rule). So: check that the SEQ file ships, then confirm `Quest.IsRunning`
  via DevBench after the first load. Don't assume it started.
- **Plugin slots:** count from the VR `plugins.txt` at decision time (full slots, light slots, and
  whether a VR ESL-support plugin is loaded); don't carry a number over from an earlier session.
- **The author's "requires a new game" is an author ruling, not an engine rule.** Respect it unless
  a diff proves otherwise.
- **Distribution mods (SPID/KID/SkyPatcher)** apply at runtime on every load, so they can be added
  without a save footprint, except for what they hand out (spells, perks, items), which actors
  then carry.

#### 4. The routine for every update session

1. **Baseline before touching anything:** copy the current `.ess` **and its `.skse`**, `plugins.txt`
   and `loadorder.txt`. Snapshot `sksevr.log`, and run `papyrus-triage` and `crash-triage` so
   "after" has a "before".
2. **One RISKY change at a time,** with a checkpoint save between, so a break can be bisected.
   SAFE items can be batched.
3. **Install through your mod manager (or a dry-run installer that backs up overwritten files and
   writes a manifest)**, so every file stays attributable.
4. **Verify from the outside after each batch:** the `sksevr.log` result line for every touched DLL,
   a DevBench load (`/api/health`, then `game loadLast`), and a Papyrus log diff for new
   `Cannot open store`, `does not match existing signature` or `not found` lines.
5. **If something breaks:** asset or DLL → restore the backup, on the same save. ESP or script →
   roll back to the pre-change save. Never "fix forward" a save that loaded a bad script state.

**Sources:** UESP *Skyrim Mod:Save File Format* and *Skyrim:Form ID*; CommonLibSSE *Serialization*
wiki; CommonLibSSE-NG *Runtime Targeting*; CK *Save File Notes (Papyrus)* and *Persistence (Papyrus)*
(read via the fallout.wiki mirror, because the Skyrim pages 403; VM rules are shared, but not
re-confirmed on the Skyrim page); Nexus forum 9922438 (new properties `None` on existing saves) and
13476332 (orphan log lines); ESLify mod page (SE 21618); AutoSEQ README; Nexus article *Mods,
Stability, and Save games* (Skyrim 801). Rows marked INFERRED or UNSOURCED are claims to test,
not facts.

### BSA/BA2 Archive Load Order

**Priority (highest to lowest):**
1. Loose files in Data folder (**always win** over everything)
2. Plugin-associated BSAs (in plugin load order)
3. Vanilla/engine-loaded BSAs (INI-specified)

- SSE/VR supports dual archives per plugin: `MyMod.bsa` + `MyMod - Textures.bsa`
- BSAs override each other based on position in archive load order; later-loading wins

*Source: [Modding Wiki: Asset Load Order](https://modding.wiki/en/skyrim/users/asset-load-order)*

### Casting Type Rule (Critical)

**All effects on a spell or enchantment must have the same casting type.** Mismatched casting types cause unpredictable behavior or spell failure.

### Navmesh Editing

- Navmesh **creation** is Creation Kit only — xEdit can only delete, not recreate
- Navmesh data is zlib-compressed; tools like TESVSnip can lose data during processing
- Adding too many objects to a cell with ESP-stored navmesh causes crash-on-exit (ESM navmesh is fine)
- Never fix deleted navmeshes from official .esm files — they are intentionally present

### ITM/UDR Cleaning Caveats

- **Not all ITMs are errors** — intentional ITMs exist for compatibility (keyword injections, compatibility patches)
- Deleted references (UDR) are a primary cause of crashes — should be undeleted + disabled
- But if a mod's purpose IS deleting something, UDR-cleaning breaks the mod
- Always review before saving after cleaning

### String Localization

- **STRINGS**: item names, actor names, race names, location names, quest objectives
- **ILSTRINGS**: NPC dialogue subtitles and voice-acted dialogue
- **DLSTRINGS**: book body text and quest descriptions
- Missing string files = blank text in-game
- xEdit does not validate or auto-generate string files

### Creation Kit Known Bugs

- **ESM flag bug**: CK does not set the 0x8 ESM flag when converting files to master type — requires manual correction
- CK can appear to save successfully but the .esp file disappears or becomes corrupted
- CK automatically removes null records on load — potentially silently corrupting data
- xEdit allows ESP files as masters; CK rejects and deletes ESP master references, leaving orphaned records
- Porting LE→SSE loses data (e.g., critical hit data on weapons) due to form version 43→44 changes

### Papyrus Fragment Pitfalls

- Fragments are stored inline in VMAD subrecords — fragile to corruption
- Fragment code must include all necessary function/event declarations
- Declaring properties in fragments can cause compiler errors blocking all further edits
- **Workaround**: add empty fragment, close quest window, reopen, edit code, then assign properties via Properties button
- CK validates fragments on save; xEdit does not — fragments edited in xEdit must be resaved in CK

### Master File Dependencies

- Removing a master file that is actively used leaves broken FormIDs with no recovery
- xEdit's "Clean Masters" detects unused master references and removes them
- Manually removing masters requires updating all referencing FormIDs — extremely error-prone

---

## Game Engine Quirks

### Ability-Type Spells

- Abilities are "always on" spells — Constant Effect, no duration.
- `AddSpell` for an already-present ability = safe no-op (no stacking).
- Abilities persist through save/load. Remove them explicitly when done.

### Papyrus fUpdateBudgetMS

- Default: 1.2ms per frame for all scripts combined.
- With 700+ plugins, this budget is frequently exceeded.
- Papyrus Tweaks NG can increase this via `fMainThreadTaskletBudget`.
- Affects `Wait()` reliability and event dispatch timing.

### Known Vanilla Bugs

- `IsHostileToActor` crashes to desktop with NONE object
- `abPreventEquip` flag in `UnequipItem` doesn't work on NPCs
- `ForceThirdPerson`/`ForceFirstPerson` can't determine current camera mode
- Forcing view change causes immediate revert
- Bashing costs no stamina in vanilla Skyrim VR (infinite stagger exploit)

### SKSE Plugin Version Compatibility

**PapyrusTweaks NG v4.1.1 (Oct 2025) breaks NPC dialogue in VR.** The newer version's `ValidationSignaturesHook` and `AttemptFunctionCallHook` cause NPC dialogue options to never appear — NPC speaks their greeting but the player choice menu doesn't show. **Use the stable 2023 version instead.** Symptom: NPC speaks but dialogue menu never opens; Papyrus log shows many `Unbound native function` and `does not match existing signature` errors at load time.

**po3_PapyrusExtender updates can break backward compatibility.** The March 2025 update changed function signatures for `PO3_SKSEFunctions`, causing mods compiled against older versions to fail with "does not match existing signature" errors. Functions like `GetSkinColor`, `GetHairColor`, `GetAllSpellsInMod`, `ToggleChildNode`, `ResetActor3D` all affected.

**The DLL on disk is the source of truth for "what version is actually installed" — not the mod manager.** For any SKSE plugin, read the embedded File/Product version of `Data/SKSE/Plugins/*.dll` directly (e.g. the stable PapyrusTweaks build reports `4.1.0.x`). A manually-installed or hand-reverted plugin is invisible to Vortex/MO2, so the manager's reported version can disagree with what the game actually loads. Because these are DLLs with no save data, reverting one is safe mid-playthrough — the problem disappears on an existing save. Keep a personal "do-not-update-past" list; a plugin that is silently broken in VR while fine on SE/AE is easy to misattribute and easy to reinstall months later.

### Engine Fixes Available

- **Engine Fixes VR**: tree LOD visibility, BSFadeNode offset corrections, volume settings persistence
- **Poached Bugs VR**: ports Scrambled Bugs fixes to VR (spellcasting, texture, weapon issues)
- **Papyrus Tweaks NG**: script execution fixes, VR playroom script pausing — **use 2023 version, NOT v4.1.1** (see above)

---

## ESP Creation via Spriggit

### A round trip reorders VMAD properties
MEASURED on one plugin: a Spriggit serialize/deserialize round trip rewrote the order of a script's VMAD
properties and nothing else in that record. A byte comparison of the plugin then reports a difference that is
not a change. Verify a round trip by parsing the properties (names, types, values), not by comparing bytes.

### Header Version
- **ESP header version must be 1.7** for SSE/VR plugins. Version 1.0 is flagged as Oldrim/wrong game by tools like Rybash.
- In Spriggit YAML: `ModHeader: Stats: Version: 1.7`
- A `spriggit-meta.json` file is required in the root of the YAML directory for deserialization. Format:
  ```json
  {"PackageName": "Spriggit.Yaml.Skyrim", "Version": "0.41.0", "Release": "SkyrimSE", "ModKey": "YourMod.esp"}
  ```

### Spriggit CLI
- Serialize: `spriggit serialize --InputPath "Data/Mod.esp" --OutputPath "/tmp/output" --GameRelease SkyrimSE --PackageName Spriggit.Yaml --PackageVersion "0.41.0"` (pass what `spriggit --version` prints)
- Deserialize: `spriggit deserialize --InputPath "/tmp/yaml_dir" --OutputPath "Data/Mod.esp"` (reads meta from spriggit-meta.json)
- `--GameRelease` is only for serialize, NOT deserialize
- **Install pinned: `dotnet tool install Spriggit.CLI --version 0.41.0`** (v4, 2026-10-05; was
  0.40.0). 0.41.0 (2026-07-02) ships only `tools/net10.0`, so it needs the **.NET 10 SDK**; per
  the drift audit a `dotnet tool update` on an SDK 6/8/9 machine failed with
  "DotnetToolSettings.xml was not found". 0.40.0 targeted net9.0. `tools/toolchain-check.sh`
  compares `spriggit --version` with the pin in `.config/dotnet-tools.json` (older or newer is
  a MISMATCH) and requires exactly SDK major 10 for 0.41+, SDK 9 before that.
- **`spriggit --version` exits 1 even when it works** (MEASURED 2026-10-05, 0.41.0, global tool and
  `dotnet tool run spriggit` alike): it prints `Spriggit version 0.41.0+...`, a stray `--version`, and
  `Spriggit.CLI 0.41.0+...`, then returns 1. Probe it by its OUTPUT, never its exit status, and not with
  `| grep -q` under `pipefail` (grep closes the pipe early and the SIGPIPE fails the pipeline). A fake that
  printed one line and exited 0 kept two new tests green while the real tool was never detected.
- **Two installs, both supported:** the global tool (`spriggit` on PATH) and `dotnet tool restore`'s local
  tool (`dotnet tool run spriggit`, answers only from inside the toolkit). `tools/spriggit-cli.sh`,
  `tools/esp-verify-wrapper.sh` and `tools/toolchain-check.sh` use the global one first, then the local one.
- **Use `-u` / `--ErrorOnUnknown` on serialize.** It makes Spriggit throw on unknown
  fields/records instead of dropping them silently — directly mitigates the silent-drop
  issue below. Make it the default for our workflow.

### ✅ Spriggit round-trip is lossless — compare YAML, not ESP bytes (re-measured 2026-10-05)
- A controlled no-op round-trip (`serialize → deserialize unchanged → serialize`) of
  a test ESP produced a **byte-for-byte identical file** (2026-05-18); an unmodified ESP
  round-trip to identical YAML with `:Skyrim.esm` references intact.
- ⚠ **The bytes are only stable once Spriggit has written the file.** MEASURED 2026-10-05 on a
  1549-byte ESP another tool wrote (RallysHandcart.esp, 8 STAT records): one
  serialize → deserialize gave the same size but **1247 of 1549 bytes different** (records come
  out in a different order), with **0.40.0 and 0.41.0 equally** (their two outputs are
  byte-identical, so this is not a 0.41 regression). Re-serializing gave YAML identical to the
  first serialize (10 of 10 files), and a second deserialize reproduced the first output byte for
  byte, so the first pass canonicalises the order and the result is a fixed point. Same result
  on SDK 10.0.401 with nothing else installed. **Prove a round trip by re-serializing and
  diffing the YAML; an ESP byte-compare fails on correct output.** Spriggit serializes
  exactly what the YAML says — it does NOT invent master-attribution corruption.
- The earlier belief that "Spriggit corrupts master prefixes on round-trip" was a
  **misdiagnosis**. The real culprit for re-mastered references (`:Skyrim.esm`
  → `:YourMod.esp`) can be an **over-broad ModKey remap during a manual mod split** —
  a blanket plugin-name replace that sweeps up legitimate field references.
- **Rule**: trust Spriggit round-trips. The real risk is *bulk ModKey/master remap*
  operations (mod splits, find-replace on YAML). Inventory cross-references (FormID +
  target master, per field) before and after any such operation and diff them.

### Tool: `tools/esp-verify-wrapper.sh` — cross-reference integrity guard
Catches the silent re-mastering / dropped-reference corruption class. Tool-agnostic
(guards Spriggit, xEditLib, CK, manual YAML edits). Snapshots are stored outside the
game dir (`~/.esp-verify-snapshots`), so it never trips the edit hooks.
- `bash tools/esp-verify-wrapper.sh snapshot Data/Mod.esp [...]` — before a risky op
- `bash tools/esp-verify-wrapper.sh verify   Data/Mod.esp [...]` — after; exit 1 +
  loud report on any reference whose target master changed or that vanished
- `bash tools/esp-verify-wrapper.sh guard Data/Mod.esp [...] -- <command...>` —
  snapshot → run command → verify, one shot
**Standing practice**: run `snapshot` before, `verify` after, ANY bulk ESP operation
(mod split, plugin rename, master-list edit, YAML find-replace). Verified 2026-05-18
to catch re-mastered / dropped references. If a change is intended, re-run
`snapshot` to accept it as the new baseline.

### ⚠️ Spriggit silently drops unknown YAML fields (CRITICAL)
- **Behavior**: If a YAML field name doesn't match a known Mutagen property, Spriggit deserialize drops the data with **zero error, zero warning, zero log line**. Deserialize returns success and the resulting ESP simply lacks that field.
- **Failure mode**: You can edit YAML, deserialize, deploy, load in-game — everything appears fine — and only notice when in-game behavior tied to that field doesn't manifest. Easily hours of mis-diagnosis.
- **Mandatory verification workflow when writing a NEW YAML field**:
  1. Edit YAML and deserialize to a temp ESP
  2. Re-serialize that temp ESP back to YAML
  3. Grep the re-serialized YAML for the field you just wrote
  4. If absent → field name is wrong (or the field doesn't exist for that record type). Try alternates.
- **Finding the correct Mutagen property name**: Inspect the DLL strings in your Spriggit install's `Mutagen.Bethesda.Skyrim.dll` (under the dotnet tools store, e.g. `~/.dotnet/tools/.store/spriggit.cli/<ver>/.../Mutagen.Bethesda.Skyrim.dll`). Use `grep -aoE "(get|set)_[A-Z][a-zA-Z]+" <dll> | sort -u | grep -i <pattern>` to enumerate property accessors.
- **Known name traps**:
  - MGEF Impact Data Set link → `ImpactData` (NOT `ImpactDataSet`, despite the linked record being IPDS)
  - xEdit's display name often diverges from Mutagen's property name — always verify against Mutagen, not xEdit
- **Discovered** during an MGEF clone for a custom-effect fix.

### Caprica Compilation
- Must run from `Data/Scripts/Source/` directory and use relative paths
- Requires `--flags "TESV_Papyrus_Flags.flg"` or compilation fails with "No user flags defined"
- `state` is a reserved word in Papyrus — cannot be used as a variable name
- To import VRIK API: copy `Data/Scripts/VRIK.psc` to `Data/Scripts/Source/VRIK.psc`
- Command: `../../../tools/Caprica/Caprica.exe --game skyrim --import "." --output "../" --flags "TESV_Papyrus_Flags.flg" "Script.psc"`

---

## VR Controller Input Detection

### SKSE Input API — DOES NOT WORK for VR Controllers
- `Input.GetMappedKey("Right Attack/Block")` returns **-1** in VR — VR controllers are not mapped through DirectInput keycodes
- `Input.GetNumKeysPressed()` / `Input.GetNthKeyPressed()` never see VR controller input
- `Input.IsKeyPressed()` is useless for VR trigger/grip/button detection
- Only `GetMappedKey("Left Attack/Block") = 507` returned a value; right-hand controls returned -1
- **Do NOT use the SKSE Input API for VR controller detection**

### VRIK API — THE Correct Method for VR Input
All functions are native globals on the `VRIK` scriptname (`Data/Scripts/VRIK.psc`).

**Button Press Detection (all confirmed working on Quest 3 via Virtual Desktop + OpenComposite):**
- `VRIK.VrikIsTriggerPressed(Bool onLeft)` — **WORKS** clean press/release, zero false positives
- `VRIK.VrikIsGripPressed(Bool onLeft)` — **WORKS**
- `VRIK.VrikIsThumbstickPressed(Bool onLeft)` — **WORKS**
- `VRIK.VrikIsButtonAPressed(Bool onLeft)` — **WORKS**
- `VRIK.VrikIsButtonBPressed(Bool onLeft)` — **WORKS**
- Pass `false` for right hand, `true` for left hand

**Touch Detection (capacitive):**
- `VRIK.VrikIsTriggerTouched(false)` returns **TRUE at rest** on Quest 3 — finger resting on capacitive trigger. Use `Pressed` not `Touched` for intentional input.

**Position Tracking (confirmed working):**
- `VRIK.VrikGetHandX/Y/Z(Bool onLeft)` — world-space hand position, updates in real-time
- `VRIK.VrikGetHmdX/Y/Z()` — world-space HMD position
- Positions are real tracking data; change when player moves

**Controller Info:**
- `VRIK.VrikGetControllerType()` — Quest 3 reports as **0 (Rift/Oculus)**

**Axis Values:**
- `VRIK.VrikGetAxisX/Y(Bool onLeft)` — thumbstick X/Y positions

**Known Issues:**
- `VrikGetFingerPos` has a **signature mismatch** with current VRIK VR build — function will not be bound. All other VRIK functions work fine.
- Duration functions (`VrikTriggerPressDuration`, etc.) — not tested but likely work

**VRIK "Adjust Held Weapon Positions" causes concentration beam angle offset.** In VRIK Options, the setting "Adjust Held Weapon Positions" subtly rotates held weapons to fit better in the hand. For normal melee weapons this is fine. For a staff firing a concentration beam, it deflects the projectile origin ~15 degrees off-center, making the beam fire noticeably to one side instead of straight. **Fix: disable "Adjust Held Weapon Positions" in VRIK Options.** The beam then fires exactly along the controller direction via MISVR. This is a per-save MCM setting, not stored in vrik.ini.

**Usage Pattern (from POS3/POS4 mods):**
```papyrus
; In a polling loop:
if VRIK.VrikIsTriggerPressed(false)
    ; Right trigger is held — do something
endif
```

### Direction from VR Hand Position
To compute a direction vector from the player toward where the sword is pointing:
```papyrus
float dx = VRIK.VrikGetHandX(false) - player.GetPositionX()
float dy = VRIK.VrikGetHandY(false) - player.GetPositionY()
float dist = Math.sqrt(dx*dx + dy*dy)
float sinAng = dx / dist
float cosAng = dy / dist
```
This gives the horizontal direction from body center to sword hand — accurate enough for extension/projectile aiming in VR.

---

## Weapon Manipulation at Runtime

### What WORKS
- **Direct weapon swap**: `Actor.UnequipItem(weaponA, false, true)` → `Actor.EquipItem(weaponB, false, true)` — confirmed working in VR, weapon visually changes in hand
- **Weapon swap UX caveat**: Force-equipping/unequipping during powers causes a visible game stutter (brief pause). **Bad modding practice** — never force swap the player's equipped weapon. Instead, check if the required weapon is equipped and refuse activation if not.
- **Weapon.SetReach(float)** — the value IS respected by the game independently of mesh (confirmed with whip mod). But **only takes effect on game restart**, not during gameplay.
- **Weapon.GetReach() / SetReach()** — SKSE native functions, no documented upper cap
- **NetImmerse.SetNodeScale(Caster, "WEAPON", scale, false)** — scales the WEAPON skeleton bone and all children (including equipped weapon mesh). Engine renders at render framerate. One Papyrus call to extend, one to retract. Zero per-tick visual work. **Confirmed working in VR** for instant weapon scaling. Requires a visible weapon to be equipped (scaling invisible = still invisible).
- **`Caster.QueueNiNodeUpdate()`** — call after SetNodeScale to flush the scale change to the render pipeline.

### What DOES NOT WORK at Runtime
- **Weapon.SetModelPath(string)** — changes the internal path but does **NOT update the visual** of an already-equipped weapon. Even unequip→SetModelPath→re-equip cycle just shows disappear/reappear with the same model.
- **Weapon.SetEquippedModel(Static)** — native SKSE function (Weapon.psc line 55). **CONFIRMED DESTRUCTIVE in VR.** Called correctly with valid Static forms — no visual change, but actively corrupts the weapon's equipped model reference. Combined with `QueueNiNodeUpdate()`, causes the weapon to **disappear from the player's hand entirely**. The weapon shows as equipped in inventory but is invisible/gone in-world. Only fixable by manually unequipping and re-equipping. **NEVER USE THIS FUNCTION.**
- **SetReach at runtime** — value changes but hit detection range doesn't update until game restart
- **Effect Shader via GetFormFromFile** — `Game.GetFormFromFile(0x00010DDD, "Skyrim.esm") as EffectShader` returned None. FormID may be wrong or form type mismatch.
- **`ModAV("SpeedMult", -70)` for VR locomotion slowdown** — has NO effect on VR thumbstick movement. User confirmed no slowdown felt at all. VR locomotion bypasses SpeedMult.
- **`ModAV("CarryWeight", -99999)` for VR locomotion slowdown** — WORKS. Over-encumbrance (carry weight exceeded) does slow VR thumbstick movement. Use this instead of SpeedMult.

### VR Weapon Collision / Reach
- **WEAP Reach field does NOT control VR weapon collision distance.** HIGGS/VRIK use the NIF's Havok physics collision geometry to determine hit detection range, not the Reach value.
- **To extend weapon collision in VR**: the weapon NIF needs extended physics collision geometry — a longer `bhkConvexVerticesShape` or a chain of physics bones with constraints (e.g., Fettered Fragments' 17-bone `Fragment_0-16` chain). Standard weapons use `bhkBoxShape` matching the visual mesh.
- **The `_HDTSw` dummy armor trick** does NOT extend reach on its own. The primary reach extension comes from the weapon NIF's physics bone chain.
- **Creating extended-reach VR weapons requires NifSkope/Blender** to modify the NIF's physics structure.

### OL_WEAPON Projectile Routing — Damage Through Extended Weapon Collision

**Root cause**: Any projectile (spell or arrow) that physically contacts a Havok collision shape with layer `OL_WEAPON` (layer 5) has its damage routed to the weapon's owner-actor. This is an engine-level routing step that occurs before SpellAbsorption, MagicResist, block, and all Papyrus event hooks — none of those mechanisms can intercept it.

**Consequence for extended custom weapons**: A weapon NIF with a long `bhkCapsuleShape` layered `OL_WEAPON` (e.g., a 71.43 Havok unit / ~5000 game unit beam) makes the player take full spell damage from any projectile contacting any part of the capsule, regardless of distance from the hand.

**Approaches that do NOT fix it** (confirmed wrong path):
- `ModAV("SpellAbsorption", …)` — "SpellAbsorption" is not a valid Papyrus AV name; generates log errors on every call. Even if it were valid, SpellAbsorption gates the *spell-cast-at-actor* pathway, not the *projectile-hits-OL_WEAPON-shape* routing.
- `MagicResist` buff — same wrong path; applies to direct spell effects, not collision-routed damage.
- Absorb MGEF, Atronach Stone perk — operate on the spell-cast path, not the OL_WEAPON path.
- Po3 `OnProjectileHit` event — **not available in VR**. Po3 VR only exports projectile property accessors (GetProjectileSpeed, etc.), no event handlers.

**WeaponCollision.dll (ijwzac/WeaponCollisionVR) cannot help**: The plugin uses hardcoded per-weapon-type reach values (sword=90, greatsword=100, axe=70 game units, scaled by `WeaponRangeMultiply` INI setting). It reads neither the NIF collision geometry nor the weapon record's Reach field. Visual debug (`DebugShowPlayerWeaponRange=true`) confirms the parry line is always ~90 units regardless of equipped weapon. A custom NIF capsule of 5000 units is completely invisible to it.

**Clean fix path (bookmarked, not yet implemented)**: Fork `github.com/ijwzac/WeaponCollisionVR` (Apache 2.0, explicitly fork-permitted). Add ~10 lines: INI flag `bUseWeaponLengthOverride = true` + a custom keyword on the weapon form → when that keyword is present, replace the hardcoded sword line length with a configurable value (e.g., 5000 units). Build requires Visual Studio + CommonLibSSE-NG + CMake 3.21+ + C++23. Confidence: 70% (risks: VR-specific CommonLibSSE-NG quirks, SKSE update breakage, maintenance burden).

**Accepted workaround**: Frame spell vulnerability as a deliberate design tradeoff — extending the blade exposes the player; retracting (releasing trigger) restores normal hit detection.

### VR Weapon Draw State
- **`DisablePlayerControls(abFighting=true)` causes weapon to sheathe** when `EnablePlayerControls` is called afterward. The engine resets weapon drawn state when the fighting control flag is toggled.
- To freeze the player while preserving weapon drawn state, use: `DisablePlayerControls(true, false, false, false, false, false, true, false, 0)` — disables movement and activation, but NOT fighting. Combined with `SetDontMove(true)` for VR thumbstick freeze.


### Reach Formula (Vanilla, without Precision)
- `fCombatDistance(141) × NPCScale × WeaponReach`
- SetReach(10.0) ≈ 1410 units ≈ 21m
- SetReach(30.0) ≈ 4230 units ≈ 63m
- SetReach(50.0) ≈ 7050 units ≈ 106m
- **Precision does not exist for VR** — weapon collision is handled by HIGGS/VRIK

### PLANCK — VR melee hit detection + runtime settings API

PLANCK replaces vanilla/Precision melee hit detection in VR. Config: `Data/SKSE/Plugins/activeragdoll.ini`.

**Papyrus API** — `planck.psc` (`ScriptName PLANCK Hidden`; source at `Data/Source/Scripts/` — a DIFFERENT dir from `Data/Scripts/Source`, so add it to Caprica `--import` to compile against it):
- `PLANCK.GetSetting(String name)` / `PLANCK.SetSetting(String name, float value)` — global native; read/modify any numeric `activeragdoll.ini` setting at runtime. `GetSetting` returns exactly `-2.71828` if the name doesn't exist (typo check).
- Also `AddIgnoredActor`, `GetLastHitNodeName` (call inside `OnHit`), aggression-topic setters.

**Hit-speed gates** (confirmed 2026-05-17 to be read **per-hit** — `SetSetting` changes take effect live, no restart/cell-load):
- `hitSwingSpeedThreshold` (def 5.0) — min impact-point velocity for a swing to register
- `hitStabSpeedThreshold` (def 2.5) — same for stabs
- `hitRequiredHandSpeedRoomspace` (def 1.5) — the hand must be moving this fast; "walk the weapon into an enemy without moving your hand" won't register otherwise

Setting all three to **0** → any contact registers a melee hit regardless of swing speed. The setting is global (all the player's melee); toggle to 0 for a weapon-state and restore on exit to scope it per-weapon. Useful for an extended-blade weapon that should damage on any contact (toggle the gates from the weapon's script and restore on exit). This is the engine-native "weapon damages on any touch" fix — PLANCK collision runs at engine framerate, no script-sampling gap.

---

## Papyrus Debugging in VR

### Debug.Notification() Limitations
- Skyrim displays **one notification at a time** with ~5 second delay before the next in queue
- Notifications from rapid script execution pile up and display minutes after they fired
- **Do NOT rely on notifications for real-time feedback or timing-sensitive tests**
- `Debug.MessageBox()` is **non-blocking** in Papyrus — fires and script continues immediately, does NOT wait for OK

### Debug.Trace() — The Correct Logging Method
- Writes directly to `Documents/My Games/Skyrim VR/Logs/Script/Papyrus.0.log`
- No queue delay — logged at time of execution
- Use `[TAG]` prefixes for easy grepping: `Debug.Trace("[MYMOD] message")`
- Use `Utility.GetCurrentRealTime()` for timing measurements

### Concurrent Script Instances
- Activating a Lesser Power multiple times spawns multiple ActiveMagicEffect instances
- Each runs its own script thread — polling loops from multiple activations overlap
- **Always use a GlobalVariable lock** to prevent re-entrance:
  ```papyrus
  if MyLock.GetValue() != 0
      return
  endif
  MyLock.SetValue(1)
  ; ... do work ...
  MyLock.SetValue(0)
  ```

---

## Save File Analysis

### .ess Save Format

SSE, AE, and VR saves all use the same `.ess` format with LZ4 compression. The compressed block is preceded by a `(decompressedSize, compressedSize)` uint32 pair. Typical decompressed sizes are 20-60MB.

**Decompressed structure:**
- **Bytes 0-5**: Preamble
- **Byte 5**: Regular plugin count (uint8), followed by length-prefixed UTF-8 plugin names
- **After regular plugins**: Light plugin count (uint16), followed by length-prefixed names
- **~100KB-22MB**: Global data tables, ChangeForms (binary, mostly non-ASCII)
- **~22MB+**: Papyrus string table (script names, variable names, type annotations)
- **End**: FormID reference tables (dense FormID arrays)

### What Can Be Done

- **Binary search** for any FormID (as LE uint32), string, or byte pattern
- **Count occurrences** to detect accumulation or duplication bugs
- **Read context** around any match (hex + ASCII dump)
- **Extract plugin lists** (both regular and light/ESL plugins)
- **String search** for script names, variable names, EditorIDs

### What Cannot Be Done (Without Full Parser)

These are limits of raw byte-scanning, not of the ReSaver CLI below. Use its
structured parse and reference index for Papyrus data, record browsing and edits;
byte-pattern matches alone do not establish parsed references or orphan counts.

- Parse structured Papyrus instance data (variable values, active effect state)
- Edit and write back a valid save (requires full format understanding)
- Browse by record type (use the structured ReSaver CLI or its Java GUI)

### Common Debugging Use Cases

- **Orphaned scripts**: Search for a removed mod's script names. If still present, the save has orphaned instances.
- **Effect accumulation**: Search for a spell's FormID and count matches. More than expected = stacking bug.
- **Mod footprint**: Search for a mod's EditorID prefix to see how many references it left.
- **Save bloat tracking**: Compare decompressed sizes across saves.

*Tool: `scripts/read-save.py` — requires Python + `lz4` (pinned, hash-locked: `python -m pip install --only-binary=:all: --require-hashes -r tools/requirements-tools.lock`)*

---

## Headless Save Editing (ReSaver CLI)

ReSaver (the save inspector from FallrimTools) ships a Java **library** that can be driven headlessly — no GUI — to parse, cross-reference, and clean `.ess` saves **deterministically**. This supersedes raw binary byte-scanning for any structured work: instead of guessing at offsets you get parsed ChangeForms, the Papyrus instance/string tables, and a real reference index.

**Operations** (`bash tools/resaver-cli.sh <op> <save.ess> [args]`):
- `info` — high-level summary (plugin lists, counts, save metadata)
- `dump` — structured dump of parsed contents
- `find` — locate elements (forms, scripts, strings) by id/name
- `find-refs` — cross-reference: what points at a given form/instance
- `worries` — surface likely problems (orphans, unattached instances, suspicious accumulation)
- `recon` — sync-aware parse-coverage scan of ALL changeform body types (per-type ok/fail, failure
  categories, unknown extra-data types with predecessor histograms — the "phantom test")
- `changeform` — parse ONE changeform body (`--raw` adds changeflags + hex/base64; `--depth N`)
- `extradata-scan` — tally unknown extra-data types (`--type REFR` is the trustworthy subset)
- `changeform-diff` — structured + raw-tail diff of a changeform across two saves (noise-labeled)
- `globaldata` / `globaldata-diff` — global-data table survey / cross-save diff
- `set-global` / `set-var` — targeted value edits
- `clean` — remove orphaned/unattached script data
- `reset-havok` / `cleanse-formlists` / `remove-created` — ReSaver-delegated bulk cleanups
- `verify-roundtrip` — safety self-test: write + re-read + compare, report identical (temp discarded)

**Analysis overlay (read ops only):** the read/diagnostic ops layer a small overlay of *modified
ReSaver source* (Apache-2.0; `tools/resaver-cli/analysis-overlay/`) in front of the jar for extra
changeform parse coverage (extra-data types 16/135, QUST QuestInstances, REFR extra-data). **Write
ops always run the STOCK jar** so an authored parse fix can never reach `ChangeForm.write()` —
corruption safety. If the overlay can't compile against your ReSaver version, the wrapper falls back
to stock parsing automatically (basic ops unaffected).

**Write safety:** every write op is **dry-run by default** and only applies with `--apply`. An applied write always goes to a **NEW file** — it never overwrites the input save. Every `--apply` is additionally **verify-gated**: a private candidate is re-read and compared to the written model. Failed candidates are quarantined in their owned staging directory with recovery evidence; existing destinations stay intact and no successful publication is reported. As always, **loading the result in-game is the final validation gate.**

**Performance:** a full structured parse of a ~45MB save runs in about **2.4s**, including building the cross-reference index — fast enough to use interactively during debugging.

**Name resolution:** ReSaver works in FormIDs. Resolve FormID → EditorID on demand with `tools/resaver-resolve-names.js` (xeditlib-backed) so dumps and reference reports are human-readable.

**Requirements:** a JDK plus ReSaver's `ReSaver.jar` + its `lib/` folder in `tools/resaver-cli/`; `bash tools/resaver-cli.sh verify` checks both against the pinned SHA-256s. The wrapper auto-compiles its small driver on first run.

## Co-save (.skse) Structural Survey (cosave-info)

The `.ess` is only half the save state — SKSE mods stash their own data in a sibling **co-save**
(`SaveN_....skse`, same basename as the `.ess`). `bash tools/cosave-cli.sh <cosave.skse>` emits a
**read-only** JSON survey: the header fields plus a per-opcode chunk inventory (count + total bytes),
so you can see **which** plugins stashed co-save data (StorageUtil/PapyrusUtil, JContainers, per-mod
blobs) and **how much** — the mod-state landscape the `.ess` never exposes. It surveys chunk
*structure* (the `{u32 type, u32 version, u32 length}` walk, validated on real VR cosaves) and
resyncs on a desync, reporting coverage — it does **not** decode each chunk's plugin-internal format
(those are undocumented per-plugin). No install beyond Python 3; it never writes the cosave.

### Decoding co-save chunks: `PLGN` and 8-byte counts

The SKSE core `PLGN` chunk is the load order at save time: a u16 count, then per entry a u8 index (`0xFE`
followed by a u16 light index for a light plugin), a u16 name length and the name. It is what resolves the
FormIDs stored elsewhere in the co-save. Inside Papyrus Extender's (`P3PE`) chunks, MEASURED on PE 5.10
co-saves (chunk version 8), counts are 8-byte `size_t` values, not 4-byte. A decoder that reads 4-byte
counts desynchronises silently after the first one.

### Node/koffi helper exit codes are unreliable on Windows

Every xelib / koffi-based Node helper (resaver-resolve-names.js, the xelib scripts, etc.) can exit **non-zero (e.g. 127) even on full success** on Windows / Git-Bash — Node's stdio-flush-on-exit races against the resident native DLL's teardown. **Judge success by parsing the JSON the script prints, never by the process exit code.** Wrappers that gate on `$?` will report phantom failures.

---

## Hook Candidates

A living list of potential safety hooks identified during work. Evaluated but not necessarily implemented.

| Candidate | Trigger | What it would do | Priority | Status |
|-----------|---------|------------------|----------|--------|
| Missing-master preflight | Before a game launch | Read `plugins.txt` and the MAST records of every active plugin; refuse or warn when a master is absent or disabled. A CTD on 2026-11-02 looked like a plugin bug and was a missing master (see *Crash logs*) | High | Not implemented |
| Write guard for the Nexus downloads folder | Bash/Edit/Write into the mod manager's download folder | Refuse like the install and `mods/` guards do. Needs the folder in `skyrim-paths.env` first; check `docs/guard-coverage.md` | Medium | Not implemented |

**When to add entries:** After any near-miss, unexpected outcome, or pattern of risk that current hooks don't cover. Include why the gap was noticed and whether the overhead is justified.

---

---

## VR Papyrus — Confirmed Findings

### Player Movement
- **`SetPosition()` on the player triggers a cell reload** in Skyrim VR, causing a loading screen even for short distances. Never use it for player teleportation.
- **`TranslateTo()` at extreme speed (100,000+) is effectively instant** and does NOT trigger a cell reload. Works well for flash-step/teleportation in VR. The original camera smoothly follows but at 100K speed it's imperceptible.
- **`MoveTo()` on the player** also triggers cell reload behavior similar to SetPosition. Avoid for short-distance movement.
- **`Game.FadeOutGame()` + `SetPosition`** is a terrible VR teleport method — the cell reload causes a multi-second black screen followed by landscape flickering as assets reload.

### Papyrus Tick Rate with Heavy Plugin Load
- **`Wait(0.1)` rounds to ~0.5-1 second actual** with 700+ plugins. The Papyrus VM is heavily throttled.
- **All Papyrus-positioned objects are limited to ~1Hz visual update rate.** Neither `SetPosition()` per-tick nor `StopTranslation()` + `TranslateTo()` per-tick can achieve smooth real-time tracking.
- **`TranslateTo()` at moderate speed (e.g. 8000 u/s) gives engine-interpolated smoothness** between Papyrus ticks, but objects still visibly "trail" behind the intended position by ~0.5s.
- **`SetPosition()` gives jerky teleporting** — objects snap to new positions with visible jumps.
- **For smooth real-time visual tracking, use engine-level approaches** like `NetImmerse.SetNodeScale()` (one call, engine handles rendering) rather than per-tick Papyrus object positioning.
- **`PlaceAtMe()` creates objects VISIBLE at caster's feet** by default. With large `SetScale()` values, a huge object flickers visibly at the player's feet. Fix: use `PlaceAtMe(form, 1, false, true)` — 4th parameter `abInitiallyDisabled=true` creates the object disabled. Then `SetPosition()` to correct location while invisible, then `EnableNoWait()`.
- **Remove `Debug.Notification()` from tight polling loops** — notification queue is slow and adds overhead per-tick.

### PapyrusUtil in VR
- **`miscutil.ScanCellNPCs()` can crash** PapyrusUtil.dll in VR with an access violation in `TESObjectREFR::SetParentCell`. Crash occurs when processing actors with invalid/missing cell references (e.g., a Goat with `ParentCell: ---`). Replace with `FindRandomActorFromRef()` loops for safer target finding.
- **`papyrusutil.ObjRefArray()`** works fine in VR for dynamic arrays exceeding 128 elements (tested up to 270).

### Targeting (TDM Replacement)
- **`truedirectionalmovement` does not exist for VR.** All calls to `truedirectionalmovement.GetCurrentTarget()` must be replaced. The script name won't resolve at all — Papyrus logs "Cannot open store for class 'truedirectionalmovement', missing file?"
- **`GetCombatTarget()`** only returns an actor when the player is in active combat (has attacked or been attacked). Returns None for passive/nearby enemies.
- **`FindRandomActorFromRef()`** is truly random — it can return any actor in range regardless of direction, proximity, or hostility. To approximate "target what you're looking at," sample 30+ times and filter by `GetHeadingAngle()` (±60°), keeping the closest match.
- **`GetActorBase().GetName()` returns empty** for leveled actors (trolls, bandits, etc.). Use `GetDisplayName()` instead for notification text.

### Spell/Effect Architecture
- **`Self.dispel()` on an ActiveMagicEffect only dispels THAT specific effect**, not the entire spell. If a spell has multiple effects (e.g., a spell with 8 effects from 8 MGEFs), dispelling one leaves the other 7 running with their summoned objects still visible. Use `actor.DispelSpell(spellForm)` to end all effects from a spell.
- **GlobalVariable signals for cross-script communication** work reliably between ActiveMagicEffects, but globals are STICKY — if a signal is set when no handler is running, it persists until cleared. Always clear signal globals in `OnEffectStart` to prevent stale signals from previous casts triggering immediately.
- **`RemoveSpell` does NOT fire `OnEffectFinish`** — use `DispelSpell` when cleanup logic exists in OnEffectFinish. (Previously known, reconfirmed.)

### Object Spawning
- **`PlaceAtMe()` + immediate `SetMotionType()` fails in VR** because 3D assets load slower than flatscreen SSE. The object's 3D isn't loaded yet when SetMotionType is called, producing "has no 3d, and so cannot have its motion type changed." Fix: wait loop checking `Is3DLoaded()` before calling `SetMotionType()`.
- **Spawning 270 weapon objects** via PlaceAtMe in a single OnEffectStart works without crashing in VR, but takes several seconds. OnUpdate won't start until OnEffectStart completes.

### Formation Math (object rings)
- **`While i < 180` with `sin(i)/cos(i)` only creates a semicircle** (0°-180° covers right→bottom→left). For a full 360° ring of objects, use `While i < 360`.
- **Radius 1800 units (~25m) exceeds typical object render distance** in VR — swords at the far side won't be visible. Keep formation radii under ~1300 units for reliable visibility.

### Decompilation (Champollion)
- **Champollion cannot decompile all bytecode.** Some code blocks decompile as empty if/elseif branches with only debug line number comments. The assembly output (`-a` flag) shows these as genuine `jmp` instructions with no code between them — the blocks may have been empty in the original compiled source (code commented out before compilation), not a decompiler bug.
- **Champollion produces variable shadowing** that Caprica rejects. Inner scope `Float cosi` conflicts with outer scope `Float cosI` (Papyrus is case-insensitive). Remove the inner redeclaration.
- **`OnPlayerFastTravelEnd` on ActiveMagicEffect** compiles in the original compiler but Caprica rejects it as "Non-native scripts cannot define new events." Safe to remove — OnEffectFinish handles cleanup.
- **Compilation stubs** are needed for type references (e.g., `zzz_getClosetTgt`) when compiling scripts that use those types as properties. Create minimal stubs with function signatures, compile, then delete the stubs.

### Skyrim Sound System: SOUN vs SNDR (CRITICAL — do not confuse)

**Every playable sound requires TWO records, not one:**
- **SNDR** (Sound Descriptor) — stores WAV file paths, OutputModel, Category. Cannot be played directly from scripts.
- **SOUN** (Sound Marker) — a thin wrapper that references one SNDR. This is what the Papyrus `Sound` type maps to.

**The Papyrus `Sound` type = SOUN records only.** `GetFormFromFile(id, "plugin.esp") as Sound` silently returns `None` if `id` points to a SNDR instead of a SOUN. No error in Papyrus log. Total silence, no indication of failure.

**Correct wiring pattern** (confirmed from a working mod's Spriggit serialization):
```
SNDR record (e.g. FormKey 49D076) ← holds WAV paths, category, output model
SOUN record (e.g. FormKey 49D077) ← SoundDescriptor: 49D076:YourMod.esp
Script: GetFormFromFile(0x49D077, "YourMod.esp") as Sound  ← uses SOUN FormID
```

**In Spriggit YAML**, SOUN records go in a `SoundMarkers/` folder:
```yaml
FormKey: 00085E:YourMod.esp
EditorID: MySoundMarker
SoundDescriptor: 000857:YourMod.esp
```

**Correct OutputModel for scripted action SFX**: `000EC7:Skyrim.esm` (`SOMStereoRad09000_verbSHOUTS`). This is the right output model for scripted action SFX. Do NOT use `0FD93D:Skyrim.esm` (`SOMStereoRad03000_verb`) — that's for ambient environmental emitters.

**Correct Category for action SFX**: `0172A1:Skyrim.esm` (`AudioCategorySFX`). Do NOT use `01A0BD:Skyrim.esm` (`AudioCategoryMAG`) — that's specifically for magic effects audio.

**WAV format**: 44100 Hz, 16-bit PCM. Sounds played positionally on a game object (`Sound.Play(objectRef)`) should be **mono**; stereo may not position correctly in 3D space.


### Spriggit WEAP YAML Format (CRITICAL — invisible weapons if wrong)

**`BasicStats` and `Data` sections have DIFFERENT fields.** Getting them swapped = invisible weapons (no mesh, no icon, record exists but renders nothing).

Correct mapping (confirmed against working weapon records):
- **`BasicStats:`** = `Value`, `Weight`, `Damage` (the "stat card" numbers)
- **`Data:`** = `AnimationType`, `Speed`, `Reach`, `Flags`, `NumProjectiles`, `AnimationAttackMult`, `Skill`, `Stagger`, `Unused`, `Unknown2`, `Unknown3`

**Required `Data:` subfields** (omitting any causes invisible/broken weapons):
```yaml
Data:
  AnimationType: OneHandSword  # or TwoHandSword, etc.
  Unused: 0x000000
  Speed: 1.0
  Reach: 1.0
  Flags: []                    # or [CantDrop], [NonPlayable], etc.
  NumProjectiles: 1
  AnimationAttackMult: 1
  Unknown2: 1061997773         # standard value from vanilla records
  Unknown3: 0x000000000000000000000000
  Skill: OneHanded             # or TwoHanded
  Stagger: 0.5
```

**Other important fields for visible weapons:**
- `Model.Data: 0x020000000000000000000000` — MODT subrecord, present in all working records
- `ImpactDataSet: 013CAC:Skyrim.esm` — standard weapon impact (without this, no hit sounds/effects)
- `DetectionSoundLevel: Normal` — affects sneak detection
- `Critical:` section — include even with minimal values (`Damage: 1, PercentMult: 10, Flags: [], Unused2: 0x000000`)
- `ObjectBounds:` — safe default: `First: -32768, -32768, -32768` / `Second: 32767, 32767, 32767`

**Working minimal WEAP YAML template** (based on working weapon records):
```yaml
FormKey: XXXXXX:Mod.esp
EditorID: MyWeapon
ObjectBounds:
  First: -32768, -32768, -32768
  Second: 32767, 32767, 32767
Name:
  TargetLanguage: English
  Value: My Weapon
Model:
  File: weapons\path\to\mesh.nif
  Data: 0x020000000000000000000000
EquipmentType: 013F42:Skyrim.esm
Keywords:
- 01E711:Skyrim.esm
Description:
  TargetLanguage: English
  Value: ''
ImpactDataSet: 013CAC:Skyrim.esm
BasicStats:
  Value: 100
  Weight: 5.0
  Damage: 12
Data:
  AnimationType: OneHandSword
  Unused: 0x000000
  Speed: 1.0
  Reach: 1.0
  Flags: []
  NumProjectiles: 1
  AnimationAttackMult: 1
  Unknown2: 1061997773
  Unknown3: 0x000000000000000000000000
  Skill: OneHanded
  Stagger: 0.5
Critical:
  Damage: 1
  PercentMult: 10
  Flags: []
  Unused2: 0x000000
DetectionSoundLevel: Normal
```

**Vanilla EquipmentType FormIDs** (MEASURED: EditorIDs read from the `EQUP` group of `Skyrim.esm`):
- `013F42:Skyrim.esm` — RightHand
- `013F43:Skyrim.esm` — LeftHand
- `013F44:Skyrim.esm` — EitherHand
- `013F45:Skyrim.esm` — BothHands
- `0141E8:Skyrim.esm` — Shield; `025BEE` — Voice; `035698` — Potion

Which one a weapon uses (MEASURED over every `WEAP` in `Skyrim.esm`): one-handed swords, daggers, axes and maces
use RightHand (1,213 of 1,258 records; 42 EitherHand, 3 BothHands), greatswords, battleaxes and bows use BothHands (1,137 of 1,137).
`IronSword` is RightHand, `IronGreatsword` BothHands. Copy the EquipmentType of a vanilla weapon of the same kind.
(Earlier versions of this file had RightHand and BothHands swapped.)

**Previous error**: Using `Critical.Flags: [OnDeath]` caused Spriggit parse error. Use `Flags: []` instead. `OnDeath` is valid in some records but may not be valid for Mutagen's CriticalData.Flag enum.

### Spell Effect Area is in FEET (CRITICAL)
- **Spell effect Area values are measured in FEET, not game units.** 1 foot ≈ 21.3 game units ≈ 30.5 cm.
- Area=60 = 60 feet = ~18 meter RADIUS (36m diameter) — NOT 60 game units (~85cm).
- Area=200 = ~61 meter radius. Area=3 = ~0.9m radius. Area=1 = ~0.3m radius.
- For sword-width damage: Area=2-3 (about 60-90cm radius).
- For wider effects (stagger aura, splash): Area=10-15 (~3-5m radius).
- This affects ALL spell effects: damage, healing, cloak, area denial, etc.

### ESP/Spriggit
- **Spriggit requires `--PackageVersion` parameter** when `--PackageName` and `--GameRelease` are set, or it throws "PackageVersion needs to be set."
- **A plugin's own records use a mod index equal to its master count** for `GetFormFromFile()` (an ESP with 5 masters uses index `0x05`; with 2 masters, `0x02`). Match the prefix to the master count or the lookup silently returns None.

### ImageSpaceModifier
- **`FadeToBlackHoldImod`** (Skyrim.esm FormID `0x0F756E`) fades the screen to black and holds. Can be applied at partial strength (e.g., 0.6) for dimming rather than full blackout.
- **`ApplyCrossFade(strength)`** transitions gradually to the IMOD effect, vs `Apply(strength)` which snaps to it instantly.
- **`FadeToBlackImod`** = `0x0F756D`, **`FadeToBlackBackImod`** = `0x0F756F` (fade back from black).

---

### Staff AnimationType, WNAM, and VR In-Hand Render

**`AnimationType: Staff` causes visual blade inversion when using a sword NIF.** The staff skeleton orients the weapon bone differently than the sword skeleton. Result: blade appears held backwards. **Fix: apply a Y-axis rotation matrix to all child NiTriShape geometry nodes in the NIF.** This successfully corrects the inversion without breaking beam direction.

**VRIK uses the 3rd-person weapon NIF *only when WNAM is null***. In Skyrim VR with VRIK active, the engine attaches the 3rd-person model (`MODL`) to the avatar hand bone — UNLESS the WEAP record has a `WNAM` (FirstPersonModel) field set to a STAT that wraps a different NIF. **Setting `WNAM = SomeStat` redirects the in-hand render to the STAT's NIF.** Confirmed: pointing `WNAM` at a STAT that wraps a vanilla 1st-person NIF causes the in-hand mesh to render that vanilla blade instead of the edited 3rd-person NIF. An earlier diagnostic (swapping the custom 1st-person NIF for an iron dagger had zero visual effect) was true *because WNAM was null at the time* — the auto-fallback 1st-person NIF lookup was indeed unused, but an *explicit* WNAM redirect is honored. **Practical rule: if you want VR in-hand to show NIF X, either edit the 3rd-person Model AND leave WNAM null, OR set WNAM to a STAT wrapping NIF X.** Both work; mixing them produces the override case where WNAM wins.

**Inventory icon UI also follows WNAM.** When WNAM is null, the inventory icon viewport renders blank (or nothing reasonable). Setting WNAM to a valid STAT activates the icon. The rendered icon is the STAT's NIF (same one used for in-hand). To get a glowing inventory icon, point WNAM at a STAT wrapping a glowing NIF.

**Skyrim does NOT auto-derive 1stperson NIF paths for custom directories.** The `1stperson<name>.nif` convention only works within recognized vanilla paths (e.g., `weapons/akaviri/`). Custom paths like `mymod/weapon.nif` → `mymod/1stpersonweapon.nif` do not resolve. Confirmed by diagnostic.

**`AnimationType: Staff` IS required for hold-to-fire.** With `AnimationType: OneHandSword` (even with `WeapTypeStaff` keyword + `EnchantType: StaffEnchantment`), the engine does not trigger hold-attack-to-cast. AnimationType: Staff is the gate.

**DAC-NG does NOT support concentration spells.** `_DynamicAnimationCasting/template.toml` explicitly states concentration spells "not working well right now" and provides `IgnoreConcentrationSpell = true` as a filter. Not viable for continuous beam.

---

### Staff vs Sword Skeleton: Blade Inversion + Grip Offset
A sword NIF set to `AnimationType: Staff` renders the blade **inverted** (held backwards), because the
staff skeleton orients the weapon bone differently than the sword skeleton.

**Fix**: rotate every child NiTriShape geometry node 180 deg around **Y** (negates X and Z, reversing
the forward-facing axis). Do this with PyFFI on an LE-format NIF, not by hand-patching bytes. Z- or
X-rotation only spins the blade about its own axis and does not flip the tip direction.

**Grip offset** comes from the skeleton bone positions:

| Bone | X | Y | Z |
|------|---|---|---|
| WeaponSword | -11.89 | +1.92 | +6.67 |
| WeaponStaff | 0.00 | -10.08 | +2.47 |

The ~12-unit Y difference makes a staff grip feel "further back"; the ~12-unit X difference makes it
feel "more centered." A root NIF translation to compensate has little effect in VR — VRIK's own grip
system controls hand-to-weapon placement and is only adjustable via VRIK's in-game per-weapon-type
calibration (not via `vrik.ini`, which has only a global `weaponAdjustment`).

### Cell.GetNthRef is UNRELIABLE for Real-Time Actor Detection

**`Cell.GetNumRefs(0)` + `Cell.GetNthRef(i, 0)` does NOT reliably return all loaded actors.** Confirmed via Papyrus logging: a Frost Troll being continuously hit by an engine beam (dozens of BEAM CONTACT events per second) appeared in only 2 out of 24 consecutive `GetNthRef` scans (~1s each). The ref count jumped from 17→18→19 when the troll appeared, suggesting dynamic load/unload of the ref list.

**Fix: Use `PO3_SKSEFunctions.GetActorsByProcessingLevel(0)` instead.** Returns an `Actor[]` array directly — no ObjectReference casting needed, far more reliable. Processing level 0 = high (nearby, combat-active actors). Confirmed working: 14 actors found per tick in the same area where GetNthRef found 1-2.

### Concentration Beam OnEffectStart Behavior

**For Script archetype MGEFs on concentration spells:** `OnEffectStart` fires at engine framerate (dozens per second, not once). Each call is a separate script instance. This means:
- Any `Spell.Cast()` or `RegisterForSingleUpdate()` inside `OnEffectStart` will spam
- Use a script-level `bool` gate to prevent spam (set true on first fire, reset in `OnEffectFinish`)
- `HasMagicEffect()` check races with Cast — the effect hasn't registered by the time the next `OnEffectStart` fires

**For ValueModifier archetype MGEFs on concentration spells:** Continuous HP drain at engine speed. Magnitude is per-second.

### Staff AnimationType Melee Auto-Fires Enchantment

**When a Staff weapon physically contacts an enemy in melee, the engine auto-fires the staff's concentration enchantment.** This happens WITHOUT the player pressing the VR trigger. The beam visual appears and the beam's damage script fires.

**Fix 1 (Papyrus — has 1-frame flash):** Check `VRIK.VrikIsTriggerPressed(false)` in OnEffectStart. If trigger not pressed, call `self.Dispel()`.

**Fix 2 (Engine-level — prevents beam entirely):** Strip the enchantment from the weapon via `Weapon.SetEnchantment(None)` when trigger is released, restore with `Weapon.SetEnchantment(beamEnch)` when trigger is pressed. Modifies the base Weapon form. Confirmed working in VR — enchantment toggle propagates to equipped instance.

### Useful Vanilla MGEFs / Reference FormIDs

| Record | FormID | Purpose |
|--------|--------|---------|
| PerkBleedingDamage | 0C367A:Skyrim.esm | Vanilla sword bleed effect (Bladesman perk). ValueModifier/Health, HideInUI, FireAndForget/Touch. |

### PushActorAway — Caller is the Source, Parameter is the One Pushed

`Actor.PushActorAway(Actor akActorToPush, float afKnockbackForce)` — the **calling actor** is the source of the push; **akActorToPush** is the one who gets knocked back, away from the caller.

- `Caster.PushActorAway(Target, 15.0)` → enemy flies away from player ✓
- `Target.PushActorAway(Caster, 15.0)` → **player** flies away from enemy ✗ (common mistake)

### Block Detection in Papyrus

Vanilla Papyrus and SKSE have no `IsBlocking()` function. **Do not use `GetActorValue("IsBlocking")`** — it is not an actor value and errors on every call (see the correction below; an earlier version of this file recommended it). Use `Actor.GetAnimationVariableBool("IsBlocking")` on actors whose behavior graph supplies the variable. DbSKSE's source declares a native `IsActorBlocking(Actor)` (READ: `DbSKSEFunctions.psc`); a declaration does not prove it works on your runtime, so verify it before relying on it.

### DbSKSEFunctions — Projectile Collision Tracking

`DbSKSEFunctions.psc` provides powerful projectile inspection:
- `GetProjectileHitRefs(ObjectReference projectileRef)` — all refs a projectile hit
- `GetProjectileCollidedLayerNames(ObjectReference projectileRef)` — collision layer names
- `SetProjectileBaseCollisionRadius(Projectile, float)` — change beam width at runtime
- `GetProjectileShooter(ObjectReference projectileRef)` — trace who fired

Useful for diagnostics and potentially for detecting beam pass-through. Requires the projectile ObjectReference, not just the Projectile base form.

### Core Modding Philosophy: Prefer Native Engine Solutions

**Always prefer what the engine already knows how to do over Papyrus-based workarounds.** "Simple" in modding means simple from the engine's perspective — not simple as code.

Examples of native vs. wrong approach:

| Problem | Wrong (script hack) | Right (native) |
|---------|---------------------|----------------|
| Stagger immunity | `ForceActorValue("Stagger", 0)` every tick | AddPerk with stagger resist entry point |
| Keeping Magicka up | `RestoreAV("Magicka", 9999)` in loop | Enchantment with 0 Magicka cost |
| Preventing melee auto-fire | Dispel in OnEffectStart | Strip enchantment from weapon form (engine-level) |
| Hit detection | Papyrus raycast | Engine collision (projectile/weapon) |
| Movement restriction | Script-forced position | SetRestrained or Paralysis MGEF |

**The question to always ask first:** "How does the game already handle this scenario?" Find that mechanism and use it. Only fall back to Papyrus scripting when no native mechanism exists — and even then, treat it as a supplement to engine behavior, not a replacement.

This is the same principle as engine-primary hit detection: the engine is always doing more work more reliably than Papyrus can. Work with it, not around it.

*Last updated: 2026-04-08*
*Add new entries as they're discovered. Prefer verified facts over speculation.*

---

## Stagger & Bleed — Vanilla Alignment

Vanilla-aligned patterns versus invented ones (the *Vanilla Game as Frame of Reference* principle in the
agent instructions):

| Mechanic | Wrong (invented) | Vanilla-aligned |
|----------|-----------------|-----------------|
| Bleed on hit | ValueModifier MGEF on a Concentration enchantment | FireAndForget bleed spell cast per-swing (mirrors the Bleeding Strikes perk's per-hit application) |
| Script stagger | Stagger-archetype FireAndForget spell via `Spell.Cast()` (no visible animation) | `PushActorAway(target, 1-3)` — physical stumble, proven engine behavior |
| Stagger immunity | `ForceActorValue("Stagger", 0)` polling | AddPerk with a ModIncomingStagger ×0 entry |
| Melee auto-fire prevention | Dispel in OnEffectStart | Strip the enchantment from the weapon form |

### Stagger-archetype spell cast from Papyrus → no visible animation

`Spell.Cast(source, target)` where the spell wraps a `Stagger` archetype MGEF produces NO visible stagger animation on the target. Vanilla Skyrim never delivers stagger this way — vanilla staggers come from perk entry points on weapon hit, shout impact data, or spell impact data on projectile collision. None of those paths are accessible from a Papyrus `Spell.Cast()` call. Do not pursue this approach.

**Practical substitute:** `Caster.PushActorAway(target, 1.0–3.0)` produces a physical stumble that reads visually as stagger. It fires reliably from script, scales with force, and matches the user's expectation of "hit = stumble."

### Stagger MGEF on Concentration enchantment = stagger lock

A `Stagger` archetype MGEF placed directly on a Concentration enchantment effect re-fires at ~90Hz in VR. The stagger animation restarts every frame before the previous one completes → infinite locked stumble. **Never put Stagger MGEFs on Concentration enchantments.** Deliver stagger per-swing via script (PushActorAway or a FireAndForget spell cast on dirChanged).

### Vanilla bleed pattern

Vanilla bleed (e.g. Bleeding Strikes perk, `0C367A:Skyrim.esm`):
- Archetype: ValueModifier Health
- CastType: FireAndForget
- Duration > 0 (persists after application, ticks over time)
- Delivered via perk entry point triggered on each weapon swing hit

**Script analog for a beam/sweep weapon:** cast a FireAndForget bleed spell from your per-swing scan when `dirChanged == true` — one cast per swing per actor on the line. This maps directly to "each swing applies bleed," matching vanilla bleed semantics precisely.

### `GetActorValue("IsBlocking")` is NOT a valid actor value

Throws `Error: Actor value "IsBlocking" does not exist` on every call. This error has non-trivial VM overhead, especially in hot paths. **Use `Actor.GetAnimationVariableBool("IsBlocking")` instead** — it reads the animation graph variable. Animal and creature graphs may not supply it: there the call logs a missing-variable error and returns false, so skip actors whose race has no such graph rather than probing them every hit.

The **`IsBlocking` condition function** (on a magic effect or spell condition) evaluates its subject natively. If that subject can be a non-actor reference — a cone or area effect hitting a door or other scenery — guard it with `IsActor == 1` first. INFERRED from one VR crash log (a native null read inside condition evaluation with a door as the subject); the guard is cheap either way.

### dirChanged early-return is the correct pattern for cheap polling loops

When a 10Hz polling loop has swing-event work (tier knockback, bleed cast, form lookup), gate ALL of it behind `dirChanged` and exit early on `!dirChanged`. Most ticks during normal beam use will be `!dirChanged` (player holding the beam still). This collapses the per-tick cost to a direction dot product + return — effectively free.

---


## Papyrus Log — Generalizable Cleanup Lessons

### "Cannot open store for class X" is benign orphan noise
Lobotomized or removed mods leave `Cannot open store for class "..."` lines (orphaned script
instances baked into save aliases). They are harmless. `bDisableMissingScriptError = true` in
PapyrusTweaks.ini suppresses them at the log level; ReSaver can clean the underlying orphans.

### GetTargetActor() == None is NOT a valid unbound-Self guard
When an ActiveMagicEffect script's `Self` has no native binding (e.g. phantom AME instances created
by an equip/ability re-broadcast), calling ANY native function on it logs `Unable to call X - no
native object bound` BUT does NOT return None — it returns a non-None garbage value. So
`If GetTargetActor() == None : Return` never triggers, and actually ADDS an error instead of
suppressing one. This contradicts common Papyrus advice — trust the empirical log. There is no
in-script way to make such a phantom instance short-circuit; fix it at the source (reduce the
broadcast, detach the script at ESP level, or filter via an SKSE plugin), not in the script body.

### po3 Papyrus Extender on VR: the old 2019 port is a trap (corrected 2026-10-05)
The earlier note here said `po3_papyrusextender_vr.dll` "omits ~24 functions present in the SSE build"
(`Apply3DHavokImpulse`, `GetAllActiveEffectsOnActor`, `InstantKill`, `RemoveChildNode`). That described
the **old 2019 port** (log banner `po3_PapyrusExtenderVR 2.3`), not the modern VR build.
- **Correct VR setup:** Nexus SE **22854**'s scripts (the original Papyrus Extender) **plus** Nexus SE
  **58296**'s DLL (alandtse's CommonLibVR build, `po3_PapyrusExtender.dll`, same filename, meant to
  overwrite the original's DLL). 58296's page says it requires the original for the scripts, and its
  archive ships only that one DLL. A mod manager may file the VR DLL under 22854 because it was
  dropped over the original's in staging; that is the documented install, not an error.
- **The trap:** a 2019 `po3_papyrusextender_vr.dll` left beside the modern DLL is a second Papyrus
  Extender. It loads after it and **replaces about 90 of the modern DLL's natives** (measured on
  one install: 90 of 90 shared names), and some replacements change signature, so scripts compiled
  against the modern `.psc` call them with the wrong shape. The function-gap list above was the old
  port's; the modern VR build supplies most of it.
- **Fix:** retire the old DLL (rename to `.disabled`; a DLL change is save-safe), then confirm the
  `Replacing native function` lines disappear from the next Papyrus log.
- **General lesson:** `grep "Replacing native function"` in the Papyrus log to find **stale duplicate
  SKSE plugins**. Two plugins registering the same script's natives is the signature, whatever the
  plugin (see the rubric's "old-named sibling" rule).

### PapyrusTweaks.ini on heavy modlists
Useful keys: `iMaxOpsPerFrame` (raise from vanilla 100); `bDisableMissingScriptError` and
`bDisableNoPropertyOnScriptErrorLogs` (suppress cosmetic version-skew noise); `bSpeedUpNativeCalls`
(desync native calls from framerate — a real perf win); `iStackDumpTimeoutMS` (lower = stack dumps
fire before a freeze times out, so you actually capture the offending stack). On a very heavy modlist
the inflated Papyrus memory settings are load-bearing — see the VM page-policy note below.


## Music System

### MUSC vs MUST — Critical Distinction

**MUSC = Music Type container.** What the music manager tracks on its priority stack. Has FNAM flags, PNAM (priority + ducking dB), WNAM fade duration, TNAM (track list). The Papyrus `MusicType` form type maps to MUSC.

**MUST = Music Track Type.** Individual audio file entries. Referenced from MUSC.TNAM. They are NOT on the priority stack.

`DbSKSEFunctions.GetMusicTypeStatus(form)` always returns `-1` if passed a MUST FormID — the manager doesn't track individual tracks. Always pass the MUSC container.

Key MUSC FormIDs (Skyrim.esm): MUSCombat `0x0003418E`, MUSCombatBoss `0x000D777A`, MUSExploreMountain `0x000B9DCE`.

### FNAM Flags on MUSC Records

Bit 0 (0x01): Plays One Selection. **Bit 1 (0x02): Abrupt Transition** — switches immediately on trigger without waiting for current track's natural end. Bit 2 (0x04): Cycle Tracks. Bit 3 (0x08): Maintain Track Order. Bit 5 (0x20): DucksCurrentTrack (Mutagen naming; non-standard flag).

Vanilla MUSCombat FNAM = `0x09` (PlaysOneSelection + MaintainTrackOrder). Without Abrupt Transition, the engine queues combat music but waits for the current exploration track to finish before switching. PM exploration tracks have no cue points, so this wait is 60-120+ seconds — meaning short fights end before combat music ever fires.

**Correct Spriggit YAML flag names** for MUSC FNAM: `PlaysOneSelection`, `AbruptTransition`, `CycleTracks`, `MaintainTrackOrder`, `DucksCurrentTrack` (=0x20). Use these in Flags list. Do NOT try to set via xelib `setValue()` with a binary string — xelib writes the wrong value silently (verified: produced `0x34` instead of intended `0x0B`). Always use Spriggit YAML for FNAM edits on MUSC records.

**Personalized Music - Remade.esp** overrides MUSCombat FNAM to `0x24` = CycleTracks + DucksCurrentTrack. This is intentional — PM's 88 combat tracks require CycleTracks to cycle through the pool. Removing CycleTracks causes the engine to fail to play any track and leaves the music manager in a deadlocked state (MUSCombat never becomes Playing, exploration music is cut by AbruptTransition, total silence results). Always preserve PM's flags when patching MUSCombat.

Target FNAM for working MUSCombat with Abrupt Transition on top of PM's override: `0x26` = AbruptTransition + CycleTracks + DucksCurrentTrack. In Spriggit YAML:
```yaml
Flags:
- AbruptTransition
- CycleTracks
- DucksCurrentTrack
```

### Papyrus MusicType.Add() Bypasses Engine Ducking (Critical)

Calling `MusicType.Add()` from Papyrus and the engine's native combat trigger go through **different code paths**:

- **Engine-native trigger**: Applies PNAM ducking → lower-priority types become inaudible (MUSCombat has PNAM ducking=100dB, effectively muting exploration music)
- **Papyrus `Add()`**: Bypasses ducking entirely → exploration music stays audibly layered under combat music

This means any Papyrus-based "force-start combat music on combat entry" workaround will produce the "multiple tracks playing at once" symptom. Always rely on the engine's native combat trigger, not scripted Add().

### BossMusic.esm Stacking Pattern

BossMusic.esm has 13 MUSC records all at priority 2 (same as MUSCombat). The script `_A_BossMusic.pex` is attached to actors via VMAD. Each tagged actor independently calls `BossMusic.Add()` on combat entry (then `GoToState("PlaySong")`). `BossMusic.Remove()` fires only on `OnDying`.

Consequence: multiple boss-tagged actors in the same fight → multiple `Add()` calls stack at the same priority. Escaped/despawned actors leak entries (Remove() never fires). Stack accumulates over the session, eventually corrupting the music manager so MUSCombat won't re-fire on subsequent encounters.

Diagnostic: `DbSKSEFunctions.GetMusicTypeStatus(BossXxx)` going to `1` (Playing) simultaneously with `MUSCombat=1` confirms stacking.

### CombatMusicFixNG.dll — Incompatible with Multi-Enemy Fights

Despite its name, CombatMusicFixNG.dll **must not be used** here. It hooks actor-death events and calls `Game.GetPlayer().IsInCombat()`. In a multi-enemy fight, there's a brief window after killing one enemy where `IsInCombat()` returns false before the player acquires the next target. The plugin force-terminates combat music in that window even though combat is ongoing. Result: combat music plays 3-4 seconds then stops mid-fight. Keep disabled.

## Orphaned Extended Skeletons -> Havok Contact-Solver CTD
A partially-uninstalled adult-creature mod (the More-Nasty-Critters / Creature-Framework family) can
leave orphaned extended `skeleton.nif` files in `Data/meshes/actors/<creature>/character assets/`
after its ESP, physics configs, and Creature Framework are gone. The extended ragdoll/collision
(`bhkRagdollConstraint` / `bhkRigidBody` / `bhkCapsuleShape`) can fault the Havok contact solver:
`EXCEPTION_ACCESS_VIOLATION` in the physics path (`hkpConstraintInstance` / `hkpRigidBody` /
`hkpSimulationIsland`) while the creature contacts world geometry, independent of combat. Fix: restore
the vanilla `skeleton.nif` for that creature (extract from `Skyrim - Meshes0.bsa`).

**Tooling note**: nif-tool.exe / the AutoMod `nif` module CANNOT inspect Havok `bhk*` collision
(textures/strings/shaders only). Use NifSkope (GUI) for collision-block inspection.


## Audio & Tooling Lessons

### Freeze/Timestop scripts that kill enemies → vanished corpses (the JCE "disappear" fix)

**Symptom**: an enemy hit by a timestop/freeze effect (AI disabled + paralyzed) is killed *while
frozen*, then **vanishes** instead of leaving a corpse — specifically *"when the effect finishes."*

**Root cause**: the freeze MGEF's `OnEffectFinish` calls `EnableAI()` / clears `Paralysis` on the
target **without checking `IsDead()`**. Re-animating a corpse at freeze-end despawns it. (`OnEffectStart`
is usually guarded against dead targets; `OnEffectFinish` often is not.)

**Fix (general, reusable)** — guard the finish handler so it never touches a dead actor:
```papyrus
Event OnEffectFinish(Actor akTarget, Actor akCaster)
    if akTarget.IsDead()
        return   ; don't re-enable AI / clear paralysis on a corpse — that despawns it
    endif
    ; ...existing un-freeze cleanup...
EndEvent
```
The despawn happens at *finish*, not start (commenting out a ragdoll-removal call at OnEffectStart is
the wrong fix). The fix is the `IsDead` early-return at the top of `OnEffectFinish` (confirmed working).

### `PushActorAway` argument direction (caused a self-knockback bug)

Signature: `ObjectReference.PushActorAway(Actor akActorToPush, Float aiKnockbackForce)` — **"pushes the
passed-in actor away from THIS object."** So `X.PushActorAway(Y, f)` pushes **Y** away from **X**.
- To knock an **enemy** away from the player: `Caster.PushActorAway(enemy, f)` ✓
- The reversed form `enemy.PushActorAway(Caster, f)` pushes the **PLAYER** ✗ (enemy takes damage but no
  knockback; the player gets launched — the exact symptom that flagged this).

### AutoMod CLI cannot read MGEF / SNDR internals

`automod esp view-record` returns **empty `Properties`** for MGEF and SoundDescriptor (SNDR) records
(it surfaces EditorID/RecordType only). To read their fields (archetype, delivery, area, scripts,
sound file paths) use **xelib** or **Spriggit serialize**. Gotcha: the MGEF archetype field is literally
**misspelled `Archtype`** in the record — xelib path `Magic Effect Data\DATA - Data\Archtype`. A scan
using `Archetype` silently matches nothing.

### WAV → XWM audio pipeline (for SNDR sound files)

AutoMod `audio wav-to-xwm` needs `xWMAEncode.exe` (not bundled); it requires an
`-o` output path. xWMAEncode only accepts **1, 2, or 6** channels and PCM input — multichannel sources
(e.g. a 7.1.4 WAV) fail with `XWMA_E_UNSUPPORTED_CHANNELS`. Normalize first with ffmpeg, then encode:
```bash
ffmpeg -y -i "in.wav" -acodec pcm_s16le -ar 44100 -ac 2 "_pcm.wav"
src/xWMAEncode.exe "_pcm.wav" "Data/Sound/FX/.../out.xwm"
```
A SNDR pointing at a **missing** `.xwm` just plays silence (no crash) — files can be dropped in later.

### Adding new SNDR/SOUN records via Spriggit + the Write-tool `/tmp` gotcha

Spriggit lays records out as per-record YAML in typed folders: SNDR → `SoundDescriptors/`, SOUN →
`SoundMarkers/`, named `<EditorID> - <FormID>_<Plugin>.yaml`. To add a sound: serialize, clone an
existing SNDR (set `SoundFiles: [FX\...\file.xwm]`) + SOUN (set `SoundDescriptor:` to the new SNDR),
deserialize.
- **The global tool answers as `spriggit`; the local one as `dotnet tool run spriggit`** (both are supported, above).
- **CRITICAL gotcha**: the Write tool's `/tmp` and Bash's `/tmp` resolve to **different** locations on
  this Windows setup — files Written to `/tmp/...` are invisible to a Bash/Spriggit `/tmp/...` read, so
  the new records silently never deserialize. Use an **explicit absolute path** (e.g. a dedicated `spriggit-tmp/` dir OUTSIDE `/tmp`) for the serialize OutputPath *and* the Write paths so both tools agree.


## Papyrus VM Page-Policy CTD on Heavy Modlists
Signature (CrashLoggerSSE): `EXCEPTION_ACCESS_VIOLATION` in `SkyrimScript::HandlePolicy::Func4`,
through `BSScript VirtualMachine -> NativeFunctionBase::Call`, with `SimpleAllocMemoryPagePolicy`
(and often PapyrusTweaks) in the stack. This is the VM's internal script-memory page-policy faulting
— NOT machine RAM exhaustion. The last script line is incidental (the policy serves all scripts).

**Tuning lesson**: on a very heavy modlist, do NOT shrink the Papyrus memory page sizes. Inflating
the pool (`iMaxAllocatedMemoryBytes`) while shrinking `iMaxMemoryPageSize` / `iMinMemoryPageSize`
produces hundreds of thousands of tiny pages — pathological, and causes an instant CTD on game load.
Big pages compensate for a big pool; the two are coupled. If the crash frequency is intolerable, the
only low-risk lever is PapyrusTweaks `bIgnoreMemoryLimit` true->false (re-imposes the ceiling so the
VM freezes/dumps instead of growing into the fault) — without touching page geometry.


## nifly/PyNifly saves can change what you did not touch

MEASURED on one install (2026-10-14): saving with nifly's default optimisation changed a particle light's
bounds in a mesh whose collision was being replaced; a strict before/after visual comparison caught it.
Preserving the original took an explicit `SetBounds` with the original values and `Save(optimize=false,
sortBlocks=false)`.

**Collision triangle count can cause a local stutter.** On that install a replacement brazier mesh whose
collision had 2,120 triangles stuttered the frame rate near it; a loose override with a 76-triangle collision
aligned to the original bounds fixed it (user-confirmed A/B, not a measured FPS delta). Hitch Hunter's report
log names the thread blocking each hitch, which separates a main-thread stall from runtime waits. A high
triangle count alone does not prove a mesh causes stutter.

## NIF Tooling — PyFFI Limits, PyNifly, and CTD Prevention

**PyFFI 2.2.3 has two HARD limits (both cost us real debugging):**
1. **Cannot read BSTriShape at all** (`Unknown block type 'BSTriShape'`) → can't touch any SSE-format
   (user_version_2=100) NIF.
2. **Authored animation controllers CTD the engine.** PyFFI can construct
   NiControllerManager/NiControllerSequence blocks and re-read them fine, but the written NIF
   **crashes-to-desktop the instant it's placed** — PyFFI omits the header string-table registrations
   the engine requires. (Confirmed in-game 2026-06-15: a PyFFI-authored `SpecialIdle` spinner CTD'd on
   `placeatme`, despite a clean PyFFI readback.) Also: building from a **fresh `NifFormat.Data()`** corrupts
   the header on write — always load an existing valid NIF and restructure it.
   → PyFFI stays useful ONLY for LE-format **NiTriShape geometry edits** (vertex shifts, bounds, the
   blade split/subdivision).

**Not a real limit, despite what it looks like: PyFFI does NOT need a dedicated Python 3.10.** The
only actual Python-version blocker is one unconditional `from distutils.cmd import Command` in
`pyffi/utils/__init__.py` (an unused doc-building helper), and `distutils` was removed from the
stdlib in 3.12 (PEP 632) — installing setuptools beside it (hash-locked: `python -m pip install --only-binary=:all: --require-hashes -r tools/requirements-tools.lock`) fixes it, since setuptools ships its own
vendored `distutils` plus an import shim (the official PEP 632 migration path). Verified end-to-end
on Python 3.14. The separate `time.clock` monkey-patch (removed in 3.8, [still open upstream](https://github.com/niftools/pyffi/issues/80)) is unrelated and still required regardless of interpreter version.

**PyNifly is the fix** (installed `tools/pynifly/io_scene_nifly/`, prebuilt DLL, no Blender; verified on PyNifly 27.2.0,
the toolkit's one pinned version (library and Blender add-on), where the DLL actually loaded is `io_scene_nifly/NiflyDLL.dll` (the zip's
`io_scene_nifly/pyn/NiflyDLL.dll` is never loaded) and `from pyn import pynifly`, after putting `io_scene_nifly` on `sys.path`, loads it itself;
the animation-authoring round-trip was re-validated 2026-10-06: 27.2.0 and 29.1.0 author byte-identical controller stacks. 29.1.0 is NOT pinned: its Blender add-on strips BSEffectShaderProperty on an import/export round trip when the diffuse texture is not on disk, and a Blender round trip drops animation controllers on both versions). Wraps ousnius/nifly (the
BodySlide/Outfit Studio engine). **Reads/writes SSE incl. BSTriShape** AND has correct `.New()`
controller-authoring factories. Use it for any BSTriShape NIF and ALL animation authoring. See
the `SpecialIdle`-named NiControllerSequence auto-loops on a placed Activator with zero scripting
(a self-spinning mesh effect with no Papyrus).

**CTD-PREVENTION PRINCIPLE (general, not just NIFs):** a crash-to-desktop must be caught in tooling,
not the user's headset. The miss that taught this: I "verified" a PyFFI NIF by reading it back **with
PyFFI** — same buggy tool, so self-consistent ≠ engine-valid. Standing gates before any in-game test:
(1) author with **valid-by-construction** tools (PyNifly, not hand-rolled PyFFI); (2) **cross-validate
with an INDEPENDENT, stricter parser** (PyNifly for NIFs — and check the SPECIFIC crashable subsystem,
e.g. the controller, since a geometry-only read passes even when the animation stack is malformed);
(3) known-good structure diff. **Cathedral Assets Optimizer was NOT adopted** — it's a Nexus-only
*optimizer* that would mutate LE-format meshes (toward BSTriShape); PyNifly is the read-only gate.

**STRETCHING A BLADE MESH (geometry lessons):** matrix-stretching a sparse low-poly blade by a large
factor tears it into invisible slivers, and the worst offenders are the thin cutting-EDGE triangles
(they taper to ~0 area). Two fixes: (a) **subdivide** the blade first (PyFFI midpoint subdivision,
crack-free via a shared edge-midpoint dict) so the stretch spreads across many short segments instead
of a few long ones; (b) **keep the split threshold on a UV/material boundary** — a threshold that pulls
geometry whose UVs map to a different texture region (e.g. a gold guard vs a steel blade) into the
stretch makes that region smear and tear. Thicken the THIN dimension (~x2) to give the edge bevel some
body.

---

## Havok Units & VR Hit-Detection

### Havok game units
1 Havok unit ≈ 70 game units. This applies to `bhk*` collision blocks ONLY (bhkBoxShape dimensions,
bhkConvexTransformShape translations, capsule lengths). A ~5000-game-unit collision shape is ~71 Havok
units. Get this wrong and the collision geometry lands far from the visual mesh.

### VR melee hit-detection stack
VR melee hit detection is layered: vanilla engine + HIGGS (hand interaction) + WeaponCollision.dll +
PLANCK (active-ragdoll melee). They stack — a hit can come from any layer.
- WeaponCollision.dll's `EnemyDetectionRange` (default 600) is often the real range bottleneck — raise
  it before blaming the weapon.
- The engine's melee hit range is effectively capped (~1500 game units) and does NOT scale with a
  longer Havok capsule. Reach beyond the cap must come from a Papyrus script (per-swing scan +
  spell/damage cast), not from enlarging the collision capsule.

---

## Spawned-Actor Havok CTD (null physics body)
Calling `PushActorAway` or a lethal `DamageActorValue` on a freshly `PlaceAtMe`'d / just-spawned actor
whose 3D/Havok body hasn't loaded yet = `EXCEPTION_ACCESS_VIOLATION` CTD. Guard any physics op behind
an `Is3DLoaded()` wait-loop before touching a spawned actor.

---

## No Papyrus Raycast
There is no raycast available to Papyrus. Scripts cannot find a beam's or projectile's terrain-contact
point. Continuous terrain-impact effects keyed to where a beam hits the world are not achievable from
script alone — use engine projectile collision (impact data sets, explosions) instead.

---

## Immobilizing the Player / NPCs in VR (aggro + VRIK)
- `DisablePlayerControls(movement)` makes the player effectively un-attackable by NPCs (they stop
  aggroing). To immobilize while KEEPING aggro, use `Actor.SetDontMove(true)` ALONE — it freezes VR
  thumbstick locomotion but does NOT freeze the VRIK weapon-bone aim, so the player can still fight.
- `SetRestrained` stops movement but does NOT stop attacks.
- To fully freeze an NPC victim (no movement, no attacks), use `EnableAI(false)` — and restore AI
  before any kill so the corpse doesn't despawn (see the `OnEffectFinish` IsDead guard above).

---

## NIF Validation & Render Trichotomy
Three independent gates for an authored/edited mesh — run them in order before any in-game test:
- **PyFFI** — LE-format NiTriShape geometry edits (vertex shifts, bounds, mesh split/subdivision).
  Cannot read BSTriShape; never author animation controllers with it (CTD).
- **PyNifly** — reads/writes SSE BSTriShape, authors animation/controller blocks correctly, AND is the
  independent PARSE gate (a battle-tested second parser that catches malformed files a same-tool
  readback misses). Check the specific crashable subsystem (e.g. the controller) — a geometry-only
  read passes even when the animation stack is malformed.
- **NifSkope (GUI) + headless Blender** — the RENDER gate. NifSkope is the independent visual
  validator; Blender (headless, PyNifly addon) repairs meshes and renders a NIF to PNG so a fix is
  confirmed in chat before loading the game. Blender shares the nifly library, so it is NOT an
  independent parser — use it for repair/render, not parse-validation.


---

## DevBench — Live In-Game Testing (hazards + confirmed patterns)

DevBench (alandtse, Nexus SE 181326) exposes the **running** game over a localhost REST/MCP server.
Everything below was learned during live sessions on a heavy (700+ plugin) VR load order. Read this
before your first live session — several of these cost a force-killed game to discover.

### Versions and what changed in 1.16-1.28 (read from the changelog/README, 2026-10-05; 1.25-1.28 added 2026-10-14; not run live)
- Latest seen: **1.28.0** (2026-10-11; the recorded API/docs reference moved from 1.24.0 on 2026-10-14).
  Toolkit floor stays **1.11.0** (`alive`; off-thread since 1.12.0).
  No removals or renames in 1.16-1.28: ports, `POST /api/tool/<tool>`, `/api/health` fields, `/api/tools`,
  console exec/capture/read and the 400/504 meanings are all unchanged (1.25-1.28 checked by diffing the
  README and `bridge/src/tools-fallback.json` at v1.24.0 against v1.28.0; no new tool, no removed argument).
  **One behaviour change: Papyrus `call` omitted optionals (1.25, #118)** -- see *Papyrus `call` gotchas*.
  1.25-1.28 otherwise add: `camera frame`/`camAngles` (1.25); `inspect` actor alive/loaded3D (1.25), `lights`
  and per-hand `hands` (1.26-1.27), attack state/stamina (1.28); `game setTimeScale` returns a
  `generation`/`state` snapshot and waits `waitMs` instead of 504-ing while the engine ramps, unless
  `requireReached:true` (1.26); `console exec capture:true` returns `lines` itself (1.28, #130); papyrus `null`
  for `None` (#129) and forms behind custom scripts (#128) (1.28); injected keys carry an empty user event (1.28, #127).
  CommonLibSSE-NG was not bumped in 1.25-1.28 (the Address Library note below still holds).
- **Update the VR Address Library first.** DevBench 1.19+ is built on CommonLibSSE-NG 8.3.0. Newest VR
  Address Library seen: **0.279.0**.
- New: `game loadLast` / `advanceTime` (1.17) / `setTimeScale` / `getTimeScale` (1.22, 0.1..3.0, leased, 409
  while recording); `wait` and `sleep` (1.16); `input` (1.18-1.24, synthetic keyboard/VR tracked-pose, mutating);
  `camera orbit` (third person only, 501 on VR); `inspect kind=extensions`; `menu invoke`; `devbench-bridge`
  stdio exe. `inspect state` also returns `pid`, `port`, `exe`.
- **`scenario` fails on an inner tool error since 1.16 (#72)**; it used to report success.
- Console capture: 1.19 fixed empty-buffer capture (#96), 1.24 "capture console output" (#117), and 1.24
  warns on unknown request keys (#116). The old "count:0 is normal" is partly a pre-1.19 artefact.
- **CrashLogger:** 1.24.0 added `__fastfail` crash dumps (a crash class older loggers never wrote).
  `tools/crash-triage.py` already matches `crash-*.txt`/`.log` case-insensitively; no real `__fastfail`
  dump has been run through it yet -- when one exists, confirm the unparsed counter stays 0.

### The server is not the game (liveness ≠ ping)
- The HTTP server runs on a **separate thread from the game**. A deadlocked, hung, or paused game
  still answers `ping` with `{"ok":true}`. **`ping` is not proof the game is alive.**
- Worse, you cannot diagnose a hang with an ordinary tool call: every tool (`inspect`, `console`,
  `papyrus`, …) is dispatched onto the **main thread** and throws **504 after 5 s** if that thread is
  stuck. The probe fails in exactly the case it exists to detect.
- **Use `GET /api/health`** (DevBench **1.11.0+**; answered off the main thread since **1.12.0**, and
  `inspect kind=health` is the MCP-reachable twin). It is the only endpoint that keeps replying
  through a stall, and returns liveness *and* identity in one shot:
  `{ ok, lastLifecycle, frame, lastTaskFrame, pendingTasks, pid, port, exe, vr }`.
- Read it as **four** states, not two — `tools/devbench-cli.sh alive` samples it twice and does this:

  | Signal | State | Exit |
  |---|---|---|
  | `frame` advancing | running | 0 |
  | `frame` frozen, `pendingTasks` 0 | **paused / console open / loading — NOT a hang** | 2 |
  | `frame` frozen, `pendingTasks` > 0, `lastTaskFrame` frozen | main-thread queue starved = **hung** | 2 |
  | `frame` < 0 | server up, no save loaded (main menu) | 3 |

  A frozen frame **alone is not proof of a hang** — the old two-read frame diff cried wolf every time
  the user opened a menu. The task queue is the discriminator.
- `pid`/`port`/`exe`/`vr` identify **which instance answered**. If you ever run two Skyrims (SE on
  8920, VR on 8921), a client pinned to the wrong port silently returns real-looking results from the
  wrong game; this catches it in one call.
- Status codes are meaningful — don't treat any JSON body as success: **504** = main thread busy or
  stuck (server fine), **400** = bad argument type (since 1.11.0; it used to be a 500), **connection
  refused** = game closed. Collapsing these into "it failed" sends you debugging the wrong layer.

### ⚠ `game save` can deadlock the game (observed on v1.8.2)
- Driving `game {action:"save"}` mid-session hung the game's main thread: a 0-byte `<name>.ess.tmp`
  that never finalized, `inspect` timing out, frame counter stopped. Recovery was a force-kill of the
  game process. `ping` kept answering the whole time.
- **To reload a script for testing, have the USER do an in-game load (F9 / load menu)** — that path
  never hung; only DevBench's own save did.
- DevBench 1.9.1 changed this area ("console: reroute save/load to game tool path") and mutating
  `game` actions are documented as fire-and-forget with `lifecycle` events for completion. Whether
  the deadlock persists on current versions is **untested here** (1.22.0's changelog says named saves are
  now queued through SKSE, #104 -- reported fixed in 1.22 per the changelog, unverified) — treat `game save` as suspect and
  prefer the user-driven load until you have verified otherwise on your version.
- If you do try it on a newer build, watch it with `alive` / `GET /api/health` (see above) rather than
  `inspect` — a tool call is dispatched onto the very main thread the deadlock freezes, so it 504s
  and tells you nothing, while `health` keeps answering and shows `pendingTasks` piling up.

### Paused-game semantics (in a menu)
- **READ tools work while paused** — `inspect state/vm/scene/player/effects/refs` are fine, which
  makes pause useful for a leisurely inspection.
- **WRITE/act tools do NOT process while paused.** `console exec` only *queues* (commands run on the
  game thread, landing a frame later); Papyrus `OnUpdate` does not fire. Do not mistake a paused game
  for a logic failure.
- Shader-state probes report **false negatives while paused** (playback is frozen), so a
  "shader not playing" reading taken in a menu is meaningless. Check unpaused.
- **A game-modal message box pauses the simulation while every read still succeeds.** MEASURED on one
  install: after a load, a mod's message box was up; Papyrus calls and reference inspection returned
  normally while nothing in the world moved. Before taking a baseline or timing anything, check for an open
  menu or message box, deal with it deliberately (read its buttons before choosing one), and confirm with
  `GET /api/health` that `frame` advances. A successful call proves the call ran, not that the game is running.

### Don't wreck the live session you're testing in
- **Heavy/global console commands can CTD a big load order.** A full `smp reset` reliably crashed a
  700+ plugin VR load order — reproduced by typing it manually, so this is the command's cost, not
  DevBench's fault. Keep live commands lightweight and targeted; get the user's OK and a save before
  any state change.
- **Don't poke the player's real ongoing activity to "test".** Firing state-mutating commands at live
  systems pollutes engine state (e.g. the music instance stack, producing phantom double-tracks).
  **Spawn dedicated test actors and act on those** (`player.placeatme <hostileBase> N`), and observe
  the player's natural activity rather than perturbing it.
- **Headless: never use the player as an attacker, target or hit recipient.** With no headset the emulated
  player has no usable physical body (on one install the headset pose lay on the floor). Spawn the NPCs a test
  needs, record their identities, and verify their roles and positions; the player only keeps the area loaded.
- **A main-menu start is enough to check that an SKSE plugin loads.** Starting the game to the main menu
  (health `lastLifecycle` `dataLoaded`, no save loaded) plus the plugin's line in the SKSE log qualifies
  "the DLL loads and starts", without touching any save. It says nothing about behaviour in a loaded world.
- **Turning autosaves off does not stop every save writer.** On one install, with the game's own autosaves
  off, a save-manager mod's rotation still wrote saves during a test and overwrote an existing one (no save
  was requested). Before a test that loads a save, back up the saves it could reach, or keep the test's save
  outside the folder the game writes to. `sLocalSavePath` did not move DevBench's save folder there.
- Many commands produce **no console output at all** (`addmusic`/`removemusic`, etc.). `count:0` from
  a capture can be genuine (on DevBench < 1.19 it was also what an empty capture buffer returned; fixed in
  1.19, reworked in 1.24) — judge the result by re-reading state, not by captured lines.

### Papyrus `call` gotchas
- Globals/native functions: **omit `self`**. Member functions: pass it —
  `{"form":"0x14"}` (or an EditorID), or `"selected"` for the console/crosshair ref (set via `prid`).
- Unlike console `cgf`, **the return value comes back** (`{called, returned, returnedType}`) — this is
  what makes read-modify-read loops possible without a play-session round trip.
- **Omitted trailing optional params: DevBench <= 1.24 padded them with neutral defaults**
  (`None`/`0`/`0.0`/`false`/`""`), so for reference ops (`MoveTo`, `Disable`, `Kill`) a short arg list
  **silently no-opped**. **Since 1.25 (#118, read from the changelog/README, not run live)** an omitted
  param is filled only from a table of known vanilla/SKSE defaults (`PlaceAtMe` `aiCount=1`, `MoveTo`
  `abMatchRotation=true`); any other omitted param **refuses the call with 400 and nothing runs**. Pass every
  param explicitly, or send `fillNeutral:true` for the old padding. The answer lists fills under `filledArgs`
  (`source: table|neutral`) and a `warning` for neutral guesses. Still pass explicit values for non-neutral
  defaults (e.g. `StringUtil.Substring`'s `-1`). Do not assume which build is installed: a 1.11-1.24
  install still pads silently.
- Array returns may not serialize (an array-returning function can come back as `none`) — probe with a
  scalar-returning function instead of concluding the state is absent.
- **Send Papyrus float parameters as JSON floats.** MEASURED: `ModActorValue("health", 1000000)` returned
  `called` with no effect on health, while `1000000.0` changed it. A void call's success says nothing about its
  effect: read the state back.

### Protocol for tests the user must physically observe (VR)
- **Narrate on the HUD.** The user cannot see chat while in the headset. Drive every instruction and
  countdown through `Debug.Notification` (`devbench-cli.sh notify "..."`) so the sequence explains
  itself in-game, and only ask them to remove the headset when the HUD says it's done.
- **Burst, never single-shot.** A one-off effect is a "shooting star" the user will miss. Fire
  10–15 times over ~10s so one burst is one unmissable observation.
- **Prefer explicit player facing/angles over weapon-node geometry** when the test doesn't need the
  weapon — it removes pose dependency.
- Log results to a file and read them yourself. Don't rely on the user's eyes, and don't rely on
  console capture where a Papyrus channel or an on-disk log will do.

---

## Vortex: a purge puts `.vortex_backup` files back, and can leave orphans

MEASURED on one install (2026-10-05 and 2026-10-13):
- When Vortex deploys a mod over files that already exist unmanaged, it renames them `*.vortex_backup`. When that
  mod is disabled or purged, Vortex renames the backups back, so an old manual copy returns. "Disabled in
  Vortex" does not mean "gone from `Data/`". Before installing a replacement for a framework, inventory its
  folders by date and by Vortex ownership (`vortex.deployment.json`).
- It can also leave a `*.vortex_backup` with no original beside it. After one mod that had overwritten meshes
  was disabled, two NIFs existed only as `.nif.vortex_backup`, and the item was invisible in game. A whole-`Data/`
  scan found 748 backups, 30 of them without an original. After any purge, scan for orphans.

## Mod Manager Layout — MO2's Virtual Filesystem (read this before trusting any path)

**Mod Organizer 2 has no real merged `Data/` folder.** It builds a *virtual* one at launch by
overlaying, in priority order: the stock game's `Data/`, then each enabled mod's own folder, then
`overwrite/` (highest). That virtual view exists **only while MO2 is running a program through it**.

Vortex and manual installs are the opposite — they deploy mod files directly into the game's `Data/`,
so `Data/` really is the merged view. Everything else in this knowledgebase assumes that layout.

**What this changes for MO2 users:**

| Thing | Stock / Vortex | MO2 |
|---|---|---|
| A mod's actual files | `Data/...` | `<instance>/mods/<mod-name>/...` |
| INIs | `Documents/My Games/<game>/` | `<instance>/profiles/<profile>/` (when profile-specific INIs are on — a per-profile toggle) |
| `loadorder.txt` / `plugins.txt` | `%LOCALAPPDATA%/<game>/` | `<instance>/profiles/<profile>/` |
| Files a tool writes into `Data/` at runtime | land in `Data/` | land in `<instance>/overwrite/` |

**Finding the instance:** global instances live in `%LOCALAPPDATA%/ModOrganizer/<InstanceName>/`;
a portable instance keeps everything in MO2's own install folder. Either way the settings are in
`ModOrganizer.ini` — `[General]` holds `gamePath`, `gameName`, `selected_profile`; `[Settings]` holds
the optional `base_directory`, `mod_directory`, `profiles_directory`, `overwrite_directory` overrides,
which may contain the literal token `%BASE_DIR%`. Values are QSettings-escaped (backslashes doubled).
Match an instance to a game folder by comparing its `gamePath`. `setup.sh` does all of this
automatically and writes the correct paths into CLAUDE.md.

### The silent-wrong-answer trap: load-order tooling outside MO2

**xelib/XEditLib resolves plugins from the game path.** Launched *outside* MO2, it sees only the
plugins physically present in the stock `Data/` — which on an MO2 setup is the base game and little
else. It does not error. It returns a **wrong but entirely plausible** answer for anything involving
the override chain, conflict resolution, or the full load order.

- **Needs the MO2 VFS** (run the script through MO2's executables list): override-chain resolution,
  "what wins this conflict", anything reading the full load order, load-order-dependent edits.
- **Works fine outside MO2**: reading or editing a single plugin by path (Spriggit serialize /
  deserialize), because those never consult the load order at all. This is the main reason to prefer
  Spriggit for single-plugin work on an MO2 install.

The same logic applies to any tool that resolves the game path from the registry rather than from an
explicit file path.

### Never bare-copy a mod into `Data/` on a managed install

A dev tool and a mod are different things, and the line matters here. Dev tools (Spriggit, xelib,
Champollion, ReSaver, …) live outside the game and can be installed freely. **A mod — anything that
ends up under `Data/`, including SKSE plugin DLLs — belongs to the mod manager**, and copying files
in by hand is the wrong move on any Vortex or MO2 install:

- **Vortex** tracks deployed files; a hand-placed file is untracked, and a later deploy or purge can
  clobber or orphan it.
- **MO2** builds `Data/` virtually at launch. A file written to the real `Data/` isn't part of any
  mod, doesn't appear in the conflict view, and won't behave like the rest of the load order.
- Either way it's invisible to the user's own record of what's installed — which is exactly the state
  that makes a broken install impossible to debug later.

So: install it through the mod manager, or tell the user to. "It's just one DLL" is how an install
becomes unreproducible.

When the user installs by hand anyway, use `tools/mod-install.py`, never a bare copy: its dry run
shows every destination (anything outside `Data/` flagged), and `--apply` backs up what it replaces
and writes a SHA-256 manifest, which is the record a bare copy lacks. Under MO2 it installs a new mod
folder in MO2's `mods/` (enable it in MO2) instead of writing to the real `Data/`. Read the
destinations before `--apply`, and check the result after it (skill `triage-and-conflicts`).

### `modlist.txt` is written in reverse

`<instance>/profiles/<profile>/modlist.txt` (CRLF) lists the **highest** priority first, and a separator line (`-<name>_separator`) comes **after** its members: a separator's members are the lines above it, up to the previous separator. `+` = enabled, `-` = disabled. "Insert above X" therefore means "higher priority than X". Write a sorted copy elsewhere and let the user back up the original and swap the copy in with MO2 closed; assert the result has the same line count and the same multiset of lines as the input.

### usvfs 0.5.7.1 corrupts .NET directory listings (measured 2026-10-29)

`hooks/file_information_utils.h` in usvfs 0.5.7.1 maps `FileFullDirectoryInformation` (class 2, the one .NET uses) to `FILE_ID_FULL_DIR_INFORMATION`, so the name is read and written at offset 80 instead of 68. Reads break duplicate skipping (each entry listed twice); writes land 12 bytes late (names like `1hm_be1hm_be`, `0_mast0_`, `info.iin`). Any .NET tool listing a virtual folder is hit; Pandora Behaviour Engine+ showed it as Nemesis mods partly ignored, `Event/variable table mismatch` and `DirectoryNotFoundException`. Not Wine-specific; fixed in 0.5.7.2 (commit de356fb2). **Check the `FileVersion` of `usvfs_x64.dll` itself**: one install had the x86 DLL and both proxies at 0.5.7.2 and the x64 DLL still at 0.5.7.1.

---

## Papyrus: a recompiled `.pex` only loads at game STARTUP

A `.pex` is read when the game launches. **A mid-session save/load never re-reads it from disk** — not
even loading a save that has never seen your mod. If you recompile a script attached to an active magic
effect or ability and then quickload, the game keeps running the **old bytecode**: Skyrim bakes the
script instance into the save and restores that instance on load.

**The only reliable refresh:** quit to desktop, relaunch, and load a **pre-activation** save (one made
before that effect was ever active). A post-activation save restores its baked instance even after a
full restart.

**Verify which build is live rather than assuming.** Plant a distinctive log line in each build and
check for it in the on-disk log — that is the definitive marker. Separately, grep a newly added string
into the `.pex` on disk first, which distinguishes "it did not compile" from "it did not load".

**Design implication — this is the real fix.** Because loading new code is expensive, make anything you
intend to *iterate* a runtime parameter (a `GlobalVariable` the script reads each run) instead of a
constant. Tuning then never changes the code: one load to get the parameter-driven build in, and zero
reloads for the entire sweep, flipping values live via the console or DevBench. Put the *mechanism*
behind a parameter too, not just the magnitude, so you can swap approaches without recompiling.

---

## Indexing records by FormKey can hide a duplicate

MEASURED on one plugin: a checker that built a dictionary keyed by FormKey silently collapsed two records that
shared object ID `0x860` (a weapon and an unused explosion), so its record count disagreed with xEdit's for a
reason it could not see. xEdit's CheckForErrors reported no error for the duplicate. Before indexing by FormKey,
assert the keys are unique, and keep the record type and EditorID with each entry.

## xelib `setFormID`: the master-count high-byte trap

The "local" FormID xelib reports for a record a plugin **owns** is **master-count-prefixed**, not the
bare lower three bytes. A plugin with 4 masters owns records whose local form looks like `0x040008XX`.

Passing a bare `0x8B3` to `setFormID(rec, 0x8B3, true)` sets the high byte to `0x00`, which the engine
reads as **an override of `Skyrim.esm` form `0x0008B3`** — a silently corrupt record, and two plugins
doing it will collide. `GetFormFromFile` matches on the lower three bytes, so it can *appear* to
half-work, which is what makes this expensive to track down.

```js
const n = xelib.getMasterNames(file).length;   // e.g. 4
const localFid = (n << 24) | 0x0008B3;         // 0x040008B3
const copy = xelib.copyElement(srcRecord, file, true);   // asNew = standalone
xelib.setFormID(copy, localFid, true, false);            // local = true, fixRefs = false
```

**Verify any ESP edit with a full before/after Spriggit YAML diff** — stronger than a reference-only
check, because it also catches silently dropped fields. Serialize a backup and the live file, then
`diff -r`; only the intended delta should appear. (`--PackageVersion` is required whenever
`--GameRelease` / `--PackageName` are set.)

---

## Reused vanilla records can carry gating Conditions

If a reused vanilla magic effect or spell fires on **some** targets but not others — "hit or miss" —
suspect **Conditions on the MGEF** before anything else. Vanilla effects are often built for a specific
quest context and carry checks (`HasKeyword`, `GetRestrained`, and similar) that made sense there and
quietly gate your reuse. Conditions are evaluated when the effect applies; targets that fail simply get
nothing, with no error anywhere.

Checklist when reusing a vanilla magic effect that must fire reliably:

1. **Own the record first** (see the Own Your Records rule) so another mod's override cannot
   reintroduce the gate, then **strip the Conditions** on your copy.
2. **Check delivery.** A Constant-Effect / Self ability applied via `AddSpell` lands instantly and
   cannot miss; an Aimed projectile cast at a target can whiff entirely.
3. **Check Resist Value** — a separate axis from Conditions. Set it to `None` for unresistable.

Serializing your copy with Spriggit is the fastest way to actually *see* inherited fields you never set.

---

## Two winners: records by plugin order, files by mod priority (measured 2026-11-01..04, MO2)

What the player sees often comes from two independent conflict winners:

- **NPC look.** The NPC_ record is won by plugin order; its loose FaceGen (`meshes/actors/character/FaceGenData/FaceGeom/<plugin>/<FormID>.nif` and the FaceTint DDS) is won by MO2 mod priority. When they come from different mods the head is missing or wrong (a headless NPC: record from mod A names A's head part, FaceGeom from mod B uses B's). Rule: the mod whose plugin wins the record must also win its FaceGen. To survey, list loose FaceGeom per enabled mod, find paths shipped by more than one, then find the record winner by scanning plugins for `NPC_` headers with that FormID.
  A FaceGen patch that tells users "let mod X win all loose-file conflicts against mod Y" must patch EVERY NPC whose FaceGen both mods ship, not only the ones split in your own setup; measure the shared file set of the two mod folders first. With several mod pairs, every pair must be flipped; diagnose by listing, per patched FormID, the winning FaceGeom/FaceTint mod next to the installed plugin's record source.
- **Scripts.** A loose `.pex` beats a `.pex` inside any BSA regardless of MO2 priority. Two overhauls that replace the same vanilla scripts, one loose and one in its BSA, give the loose scripts while the later plugin wins the records: a mismatched script/record pair. Pick one mod.
- **Interface SWFs.** Resource mods sometimes ship stock `interface/*.swf` (Beyond Skyrim Assets: `hudmenu.swf`, `map.swf`, `SkyUI/mapMarkerArt.swf`); placed above a UI reskin they silently revert it. After any sort, diff the file winner of every file the moved mods ship, before vs after.

**A patch must master every plugin that overrides its records, not only today's winner.** With two plugins overriding the same record and neither mastering the other, LOOT's order between them is not guaranteed; a patch mastering both is the only order-proof fix. A FaceGen patch built against the winner alone was beaten when LOOT sorted another overrider after it, and 10 NPCs stayed headless. Build from the live `plugins.txt` (scan every active plugin's headers for the FormIDs), not a remembered load order, and check in game with the console's "Base last changed by". Merge only the fields you mean to change onto the current winner; copying a donor record whole reverts later fixes (USSEP, AI Overhaul).

**A plugin that swaps `.esp` for `.esl` across a major version does not keep its FormIDs.** Changing a dependent patch's master header from the old name to the new one leaves dangling references to records the new version deleted (Tamrielic Distribution 1.x -> 2.0 dropped its `DES_Proxy*` records). Re-target the references to records that still exist.

**Overhauls reuse vanilla FormIDs under new EditorIDs** (Scion, Sacrosanct). Porting a feature from one onto another means copying those records to new FormIDs, never sharing the IDs.

---

## Crash logs: CrashLogger writes `.LOG`, not `.txt`

Recent CrashLogger versions write crash logs as **`.LOG`** into `Documents/My Games/<game>/SKSE/` and
open the newest one in Notepad. If you are looking for `crash-*.txt` and finding nothing, that is why.
Sort that folder by modified time after a CTD.

**The dangerous case is not finding nothing — it is finding some.** Both extensions coexist in
that one folder, so a `crash-*.txt` glob does not fail, it reports a confident total built from
whichever half it matched. Measured 2026-08-27 on the dev install: **8 `.txt` (all June 2025)
beside 20 `.log` (2026)**, and the first version of `crash-triage.py` cheerfully reported
"8 log(s)" — a full year out of date, with an accounting check that balanced perfectly, because
a denominator can only account for what it was given. Match `crash-*.(txt|log)`
case-insensitively, and keep the `crash-` prefix so the plugin's own `CrashLogger.log` is not
counted as a crash.

**Trainwreck** (Nexus 106440) names its dumps by timestamp only (`2026-10-22-22-14-42.log`, no `crash-` prefix) and writes them to `SKSE/Crashlogs/`. `crash-triage.py` reads that subfolder and that name shape; a `crash-*` glob finds none of them.

**First moves on a new CTD (measured 2026-11-02, AE 1.7.104).** Before porting or patching the plugin that changed last: (1) compare the crash signature with and without the suspect, and (2) check every active plugin's MAST records against `plugins.txt`. A CTD right after the main menu at `SkyrimSE.exe+1DCB60` (`mov rax,[rcx+0x30]`, called from `+2DF22B`, `rcx = [PlayerCharacter+0x40] + 0x30`, i.e. the player's base object is null) was identical with and without the blamed DLL; the cause was an enabled patch plugin whose master had been deleted. MO2 deleted it: installing an archive into an existing mod folder with **Replace** removes that folder's files first.

---

## AutoMod `archive list` shows 100 files unless told otherwise

`archive list` defaults to `--limit 100` (READ: AutoMod v1.11.0 source, `ArchiveCommands.cs`; `--limit 0` lists
all). The JSON carries `"showing"` and `"totalInArchive"`, but a reader who looks only at the file list sees a
100-file archive. MEASURED on one install: a 1,571-file texture BSA read as holding 100 files and produced a
confident wrong conclusion ("648 of 709 textures missing everywhere"). Pass `--limit 0` and compare `showing`
with `totalInArchive` before concluding anything from a listing. With `--filter`, `totalInArchive` is still the
whole archive's count, so a filtered listing is expected to show fewer.

## AutoMod BSA extract/repack needs `bsarch.exe`

AutoMod's `archive` module reads a BSA index natively for `list`, but `create`, `extract`,
`extract-file`, and `repack` shell out to **BSArch.exe** — without it they fail with `BSArch not found`
(`create` MEASURED 2026-10-05). Place `bsarch.exe` (BSArch 1.0, the tested build; hash in docs/installing-tools.md *Pinned downloads*) at `<build-output>/tools/bsarch/bsarch.exe`, i.e.
under the same `bin/Release/net10.0/` folder the wrapper runs the DLL from (`net8.0/` before v4: the
v4 retarget builds into a NEW folder, so move an existing copy across). BSArch also refuses an
`extract --output` folder that does not exist yet (`EInvalidArguments: Folder does not exist`);
create it first. **It lives under `bin/`, so any `dotnet build` or `clean` of AutoMod wipes
it** — keep a canonical copy elsewhere and re-place it after a rebuild.

---

## Reading a BSA or plugin header without AutoMod or xEdit (measured 2026-11-04, macOS)

When neither AutoMod's DLL nor `bsarch.exe` is available, an SSE BSA v105 with archive flags 3 (directory and file names, no default compression) reads in about 30 lines of Python: header `<4s8I`; folder records `<QIIQ`; then per folder a bzstring name plus `<QII` file records; then the file-name block (header field 7 is its length). Bit `0x40000000` of a file size toggles compression for that file, and a compressed block starts with its uncompressed size (4 bytes), so asset versions compare by size without extracting. Used to pull two `.pex` out of a 40,643-file BSA and to compare 44k archive entries across a load order.

Header-level plugin parsing is equally small: TES4 MAST subrecords, a GRUP walk, and zlib for records with flag `0x40000`. That was enough for overlap counts, full override chains (767 plugins in a few minutes) and LVLO diffs. It does not replace xEdit's field-level conflict view, and it reads files, not the MO2 VFS.

---

## FOMOD Installers: Authoring and Mod Manager Behaviour (measured 2026-11-01, MO2)

**Archive layout.** `fomod/info.xml` + `fomod/ModuleConfig.xml` + one folder per installable option. Each `<folder source="X" destination=""/>` must name a folder that exists in the archive; a build script should assert the set of `source=` values equals the folders it packs. Exclude OS litter (`.DS_Store`, `Thumbs.db`).

**Schema validation.** `xsi:noNamespaceSchemaLocation="http://qconsulting.ca/fo3/ModConfig5.0.xsd"` is a thin file that `<xs:redefine>`s `http://qconsulting.ca/gemm/ModConfig5.0.xsd`. The gemm schema has `type=" xs:string"` (leading space) on one attribute, which libxml2 refuses to compile. To validate offline: download both with `curl -L` (the plain URL returns a redirect page), strip the space, point the redefine at the local copy, then `xmllint --noout --nonet --schema ModConfig5.0.xsd ModuleConfig.xml`. Well-formed XML is not enough: the schema catches misplaced elements that a mod manager may silently ignore.

**One plugin per setup (variants).** When a patch must master a different set of optional plugins depending on what the user has (patch A, patch B, both; optional addon or not), build one plugin per combination, all with the same file name, each in its own option folder, and let the installer pick. Install exactly one: two copies of the same overrides confuse load-order sorting and double-apply. Tell the user to reinstall when they enable or disable one of the detected plugins: the old variant is then a missing master or loses to a plugin it no longer masters.

**Optional support for another mod: an add-on plugin, not more variants.** When a supported mod is optional and only adds fields to a few records the patch overrides, ship a small separate plugin that masters both the main patch and that mod and copies just those fields on top of the main patch's records. One add-on fits every variant if the main records it touches are identical across variants (assert it in the build). In the FOMOD put it in a `SelectAny` group: `defaultType NotUsable` (greyed out, it would be a missing master) and one pattern `Recommended` (checked) when the mod's plugin is `Active`. In `SelectAny`, MO2 disables only that option.

**Auto-detecting active plugins: the layout that works in MO2.**
- One visible step, one `SelectExactlyOne` group, one option per variant. Do not hide the choice entirely in `conditionalFileInstalls`: the user then cannot see which variant is installed, and the archive's identical plugin names in several folders read as "installs all of them".
- Make **exactly one** option `Recommended` for any setup; every other option `Optional`. Never rely on option order to pick among several Recommended ones: MO2's source checks only the first Recommended, yet a real MO2 build preselected the LAST of several.
- MO2 uses the **first matching pattern** of an option's `dependencyType` (`getPluginDependencyType`). So per option: pattern 1 `Optional` when any detected plugin the option does NOT need is `Active` (an `Or` of `fileDependency state="Active"`), pattern 2 `Recommended` when all plugins it needs are `Active`, default `Optional`. Only positive `Active` checks; no `Missing` / `Inactive` logic needed.
- **Do not use `Required` for auto-selection** in a `SelectExactlyOne` or `SelectAtMostOne` group: MO2 then disables EVERY option of the group by design, and the user cannot change or even clearly see the selection. `NotUsable` defaults have the same effect on the other options.
- **Do not gate a patch FOMOD with `moduleDependencies`.** It is a hard gate: one required plugin missing or inactive (no USSEP, Vortex not yet deployed, a merged or renamed plugin) and the installer refuses to run; the user sees only "problem with the fomod". A failing nested `<dependencies operator="Or">` gives MO2's "installation as fomod Failed No conditions were successful in an 'OR' clause" (user report 2026-11-02, both target plugins installed but not active). Let the plugin's masters and the game's missing-master warning do the gating.
- MO2 answers `fileDependency` for `.esp/.esm/.esl` from its live plugin list: a plugin present but disabled is `Inactive`, so "enable first, then install" belongs in the instructions.

**Two independent choices without duplicating the auto-detect group** (a user pick x an auto-detected setup; XSD-valid and simulated for every setup, not yet manager-tested): options install no files and only set `conditionFlags`; `conditionalFileInstalls` (after `</installSteps>`, ModConfig 5.0 order) maps each `flagDependency` pair to one folder. Options keep their `typeDescriptor` patterns, so preselection is unchanged and the choices stay visible. A FOMOD cannot read mod file priority, so a choice that depends on which mod wins loose files must be a user pick with the priority rule in its description.

**Self-check before handing it to a user.** Write a small evaluator over `ModuleConfig.xml` (And/Or over `fileDependency`, first-match patterns, defaultType) and run it over every Active/Inactive/Missing combination of the detected plugins: assert the install is refused exactly when it should be and that exactly one option is Recommended, the right one. XSD validation plus this simulation still do not prove a manager's behaviour; one real install test per manager does.

**Mod name shown by MO2.** The installer window title is MO2's guess from the archive file name, set before `info.xml` is read. For a file name without a Nexus `-<modid>-` part, MO2 keeps only letters, `_` and spaces up to the first other character (`Foo - Bar Patch.zip` becomes `Foo`). In a real MO2 build the Name box also kept that guess although `info.xml` had `<Name>`. For a Nexus download MO2 uses the `.meta` mod name (the page title), so users are unaffected; for local testing, type the name. Fill `<Author>`, `<Version>` and `<Website>`: MO2 shows them on the installer page.

**Vortex:** not measured. Its installer evaluates the same XML, but preselection and `Required` behaviour may differ; test once before release.

---

## Releasing: MO2 `meta.ini` and the Nexus API

- (Source-read 2026-11-03, not verified with a real install.) MO2 keeps a `meta.ini` bundled at the archive root, then overwrites only installationFile, repository, author, uploader, uploaderUrl, nexusFileStatus, plus gameName/modid/version when the download's `.meta` supplies them. A manually added zip has a blank `.meta` (`version=0.0.0.0`), so the bundled version is lost. `url` + `hasCustomURL=true` set "Visit web page". Quote values containing commas (QSettings reads them as lists).
- (Measured 2026-11-01.) Nexus API v1 `/games/<domain>/mods/<id>.json` returns the full description (BBCode) but **not** the Permissions tab; fetching the page itself gets a Cloudflare 403. Permission terms differ between sibling pages by the same author: read each page, never copy one page's terms to another.
- (Source-read 2026-11-03, not yet run live.) Nexus v3 (`https://api.nexusmods.com/v3`, `apikey` header) can only add a new version to an **existing** file (`POST /mod-files/{file_id}/versions`); the mod page and its first file are made on the website. Upload: `POST /uploads/multipart` -> PUT parts to the presigned URLs (no key) -> POST the completion XML -> `POST /uploads/{id}/finalise` -> poll `GET /uploads/{id}` until `available`. Changelog: `POST /mods/{id}/changelogs` (appends). Source: `Nexus-Mods/upload-action` commit 98532f7.

---

## CommonLibSSE-NG & SKSE Plugin Rebuild Traps

Lessons from rebuilding SE/AE plugins on AE 1.7.104 (2026-10-27 to 2026-11-04); not exercised on VR:

- **Rebuild the DLL the enabled mod ships.** Hash the DLL in the folder that has the `+` line in `modlist.txt` and match its PDB source list to a commit before rebuilding. Rebuilding from an older or different Nexus version's source can crash on launch if forms or INI configurations drifted.
- **Two mods shipping the same DLL name.** Tell them apart by the `SKSEPlugin_Version` struct bytes (`+0x304` `versionIndependenceEx`, `+0x308` `versionIndependence`) and the version field in `skse64.log` / `sksevr.log`. A `no version data` line means the DLL exports `SKSEPlugin_Query` but not `SKSEPlugin_Version` (an older 1.5.97 build): often another mod bundles an outdated copy (e.g. Simply Knock SE shipping a pre-AE `PapyrusUtil.dll` overriding a newer one by priority). Delete the bundled copy in the mod manager. Triage: `grep "no version data" skse64.log`, find every copy in `mods/`, compare exports with `llvm-readobj --coff-exports`.
- **Manual CommonLibSSE-NG install (no vcpkg port).** `cmake --install` omits `cmake/CommonLibSSE.cmake` (defines `add_commonlibsse_plugin`) and the generated config lacks `find_dependency(directxtk)`: copy the `.cmake` into `lib/cmake/CommonLibSSE/` and add `find_dependency(directxtk CONFIG)` before the targets include. Configure with `-DENABLE_SKYRIM_VR=OFF -DBUILD_TESTS=OFF` (`extern/openvr` is an empty submodule); a VR build was not tried.
- **14.x/15.x API breaks.** No `UNICODE` (use `GetModuleHandleW` for wide literals); `NOMINMAX` must precede `RE/Skyrim.h`; `ActorValueOwner::RestoreActorValue(modifier, av, v)` is removed (the 2-argument versions force the sign; use virtual `ModActorValue(modifier, av, v)`); `RE::DebugNotification` is removed (`RE::SendHUDMessage::ShowHUDMessage`).
- **`SKSE::AllocTrampoline(n)` runs once** (`std::once_flag`) and fixes the total size. A helper that calls `AllocTrampoline(14)` before each `write_call<5>` leaves a 14-byte trampoline; the second hook fails with `SKSE/Trampoline.cpp(164): Failed to handle allocation request`. Call it once after `SKSE::Init` with 14 x the number of call hooks plus margin.
- **`add_commonlibsse_plugin(... STRUCT_DEPENDENT)`** emits `versionIndependenceEx = 0x2` and `versionIndependence = 0x1` (no `StructsPost629`), which tells SKSE the plugin uses pre-1.6.629 structs. Plugins that read version-shifted structs through `RUNTIME_DATA_ACCESSOR`s pick the layout at runtime and should keep the default Independent (`0x3` / `0x1`).
- **A post-build `copy_if_different` to `$SKYRIM_MODS_FOLDER` / `$SKYRIM_FOLDER`** in a mod's `CMakeLists.txt` writes straight into the live install. Configure with those variables unset.
- **The 1.6.1170 `SkyrimSE.exe` is SteamStub-encrypted** (`.text` is noise), so code cannot be diffed across builds without an unpacked copy. For hardcoded-RVA plugins, map Format 2 (1.6.1170) offset -> ID -> Format 5 offset; Format 5 is `uint32 rva[id]` from file offset 96.

Similarly, `audio wav-to-xwm` needs `xWMAEncode.exe`, which is not bundled. It is often simpler to call
`xWMAEncode` directly than to go through AutoMod.

**AutoMod on .NET 10 (v4, MEASURED 2026-10-05, v1.11.2 = e1c34ea, SDK 10.0.401 with no other SDK or
runtime installed).** Upstream targets net8.0; `bash tools/automod-cli.sh --rebuild` pins SDK 10 in
`tools/automod/global.json` and retargets `Directory.Build.props` to net10.0 (one line, a local
patch), then builds the Cli project. It built with 0 errors (2 harmless NU1510 warnings) and on 10.0.12
alone ran: `esp create`, `add-weapon` (dry-run and real), `add-spell/-armor/-global/-quest/-npc/-book/
-perk/-faction`, `info`, `view-record`, `list-masters`; `nif info`; `archive list/create/extract`
(byte-exact); `mcm create/add-toggle/validate`; `papyrus status/generate/validate/download/compile/
decompile`; `audio info/create-fuz/extract-fuz` (byte-exact). Spriggit 0.41 read the AutoMod-written
ESP and round-tripped it byte-identical. Upstream's unit tests: 68 of 69 pass on net10.0 — and the
same 68 of 69 on net8.0: `ResultTests.ToJson_FailedResult_ContainsSuccessFalse` fails on both (a
failed result's JSON omits `"success": false`), so it is upstream's, not .NET 10's. Rebuilding over an
old net8.0 build needs no clean. A net8.0 build does NOT start on a 10-only machine without
`DOTNET_ROLL_FORWARD=Major`; the wrapper refuses it and says to `--rebuild`.

---

## Explosion knockback needs a Knock Down flag, not more Force

An `EXPL` record's `DATA\Force` **only moves actors if a Knock Down flag is set** in `DATA\Flags`.
Without `Knock Down - Always` or `Knock Down - By Formula`, raising Force does nothing at all — you can
go 3, then 50, then 1000, and see zero knockback. This is a classic dead end: the parameter looks like
the knob, and it is not.

- `Knock Down - Always` ragdolls every affected actor regardless of mass; `Knock Down - By Formula` is
  mass-weighted and gentler.
- Force pushes **radially away from the explosion centre**, so a self-centred explosion flings distant
  actors outward. For a directional fling the centre must be offset from the target.
- For simple visible knockback, the engine-native `PushActorAway(target, magnitude)` (roughly 1-5 =
  stagger through ragdoll) is usually a better tool than an explosion at all.

## Windows scripting traps that look like success

MEASURED on Windows 11 (2026-10-25 unless noted):
- **`subprocess.run(['bash', ...])` from Python starts the WSL `bash.exe` in `System32`, not Git Bash**,
  because Windows searches the system folder before `PATH`. With no WSL distribution it prints only
  `execvpe(/bin/bash) failed` on stderr and exits 1. A hook harness that reads empty stdout as "the hook allowed
  it" then reports allows the hook never made. Call Git Bash by absolute path, and treat a non-zero exit as an
  error, never as a decision.
- **Python 3.10's `datetime.fromisoformat` rejects a 7-digit fraction** (`...00.1234567`, the precision
  PowerShell and .NET print); 3.12 accepts it. Trim to 6 digits before parsing.
- **Process start times differ by source:** on one install, Get-Process and CIM (`Win32_Process`) gave the same
  process start time with a different last fractional digit (MEASURED 2026-10-23). Compare identities from one
  source only.
- **PyYAML (YAML 1.1) turns bare `off`, `on`, `yes`, `no` into booleans:** `a: off` loads as `False`. Quote
  such values in a YAML file that a tool reads as text.

## Windows paths in `sed` replacement text get eaten

Feeding a backslash-separated Windows path straight into `sed`'s *replacement*
half silently destroys it. GNU sed reads backslash sequences there as escapes:
`\U` switches on upper-casing, `\L` on lower-casing, `\t` becomes a literal tab,
and every other backslash is simply dropped. Measured on this toolkit's own
`setup.sh`:

```
supplied  C:\Users\testuser\AppData\Local\Microsoft\WinGet\Links\jq.exe
produced  C:SERS<TAB>ESTUSERAPPDATAocalmicrosoftwingetinksjq.exe
```

There is no error and no non-zero exit -- the substitution "succeeds" and writes
garbage. When the value is a path to a tool, whatever consumes it fails open.

**Rule: normalize before substituting.** `path=$(printf '%s' "$path" | tr '\134' '/')`
Skyrim tooling accepts forward slashes everywhere, so this costs nothing.

**Normalize at the point where the branches converge, not at each assignment.**
This toolkit shipped the bug twice for that reason: the v3.2.1 fix normalized
two variables and missed a third, and that third had *three* assignment sites
(a `which` result, a known-locations loop, and a post-install re-detect), so a
fix attached to any one of them would still have missed the others.

The same trap exists in Python -- `"C:\Users\..."` in a non-raw string is a
`\UXXXXXXXX` escape error, and `\t` in a double-quoted string is a tab. Use raw
strings, or build the separator with `chr(92)`.

---

## `setGamePath` needs a trailing separator — and only tells you later, in `setGameMode`

Measured 2026-08-27. XEditLib wants the game path terminated with a separator. Give it
`C:/Games/The Elder Scrolls V Skyrim VR` and the **next** call fails:

```
SetGameMode failed
```

The error names the wrong function. `setGamePath` returns success; the path is only validated
when the mode is set, so the failure surfaces one call later with a message pointing at a call
that is fine. Worse, the trap is **call-order dependent**: it only bites when `setGamePath`
runs *before* `setGameMode`. Scripts that set the mode first never see it, which is why the
same bad path can look harmless in one script and fatal in another.

**Rule: terminate the game path before handing it to xelib.** Deriving the path (rather than
hardcoding it) makes this easy to get wrong, because `path.resolve()` never returns a trailing
separator:

```js
const last = dir.slice(-1);
return (last === path.posix.sep || last === path.win32.sep) ? dir : dir + path.sep;
```

Check **both** separator styles, not `path.sep` alone — game paths are Windows paths even when
the CI runner testing them is Linux, where `path.sep === '/'` and a backslash-terminated path
would be "fixed" by appending a second separator.

---

## More than one `plugins.txt` (and more than one `My Games` folder) — the wrong one reads perfectly

A machine that has ever had more than one Skyrim installed carries several copies of the same
per-game state, and **nothing errors when the wrong one is read**:

- `%LOCALAPPDATA%\Skyrim VR\plugins.txt`, `...\Skyrim Special Edition\plugins.txt`,
  `...\Skyrim\plugins.txt` — the active load order.
- `Documents\My Games\Skyrim VR\`, `...\Skyrim Special Edition\`, `...\Skyrim\` — INIs,
  `Logs\Script\Papyrus.*.log`, and the `SKSE\` folder with the crash dumps.

Measured on the dev machine (a VR-only install): the **SSE** `plugins.txt` exists, holds 659
plugins, and is **18 days stale**. It disagrees with the VR file about which plugins are
active. A stale-but-valid file does not fail a loader — it produces a complete, plausible,
**wrong** load order, and every conflict answer computed from it is confidently wrong.

Note this contradicts the older advice that the SSE `plugins.txt` "does not exist on a VR
install". It may well exist. Don't assume its absence.

**Rule: pick deliberately, report the rivals, and offer an override.** The bundled tools probe
`Skyrim VR → Skyrim Special Edition → Skyrim`, print every candidate they ignored to stderr,
and honour `$PLUGINS_TXT`, `$PAPYRUS_LOG_DIR`, `$SKSE_CRASH_DIR`. Silently picking the first
hit is the same class of bug as reading the wrong half of the crash logs (above): a confident
answer about an install you are not modding.

## `paths:` in SKILL.md gates LISTING, not loading

MEASURED 2026-09-09. Reading a `.psc` under `Data/` produced *"The following skills are available
for use with the Skill tool: skyrim-context"* — the skill became **invocable**; its body did **not**
enter context. A description that says "Auto-loads when working with X" is describing an intention,
not the mechanism.

**Consequence for any `CLAUDE.md` → skill migration:** the root file must keep an explicit *invoke*
pointer. Without one, a future session sees no mention of the knowledge and concludes it does not
exist — the migration silently deletes the guidance instead of deferring it. Never migrate a
"never do X" rule this way at all.

## A guard at the wrong depth resolves a wrong root and silently allows

MEASURED 2026-09-09, same file, two locations, same payload. `protect-bash.sh` derives its install
root from its own location (`$(dirname "$_SELF")/../..`). Run from its real location it correctly
DENIES an `rm -rf` of the game install. A **byte-identical copy** run from a temp directory returns
**zero bytes** — and zero bytes is read as ALLOW.

The documented invariant is *"no root resolvable → DENY"*. It does not cover **root resolvable but
wrong**, which is the more dangerous case because nothing errors. `hook-canary.sh` reports such a
hook ALIVE, correctly: it *is* running and it *is* receiving its payload.

This matters here because users extract the bundle themselves, and a wrong depth is silent. Not yet
fixed: the obvious check ("deny unless the root looks like a Skyrim install") would break this
repository, which ships these hooks and is not a game install.

## Hook timeout economics — measure before optimising the guard

MEASURED 2026-09-09 with zero-fork in-hook markers (`EPOCHREALTIME` + `printf`, both bash builtins,
so the instrument does not perturb what it measures):

| hook | stdin read | body median | body max |
|---|---|---|---|
| `protect-bash.sh` | 13.0 ms | 372.9 ms | 465.2 ms |
| `snapshot-before-tool.sh` | 13.1 ms | 531.5 ms | 557.2 ms |
| `protect-files.sh` | 14.1 ms | 209.1 ms | 215.9 ms |
| `backup-before-edit.sh` | 13.8 ms | 113.7 ms | 123.0 ms |

Two conclusions. **The `INPUT=$(cat)` stdin read is ~3% of the cost** — bounding it saves nothing on
the normal path. And **fork reduction cannot be justified against a 60s budget**: the whole of
`protect-bash` is 375 ms.

⚠ **The observed timeouts do not live on this distribution.** Successful runs cap at ~8.2 s across
250 transcripts while every timeout is ≥10 s, with nothing in between. Either the process is not
starting at all (see [claude-code#77078](https://github.com/anthropics/claude-code/issues/77078) —
hook processes left suspended, `UserProcessorTime = 00:00:00`, OPEN with no known root cause), or
`durationMs` is measured differently for a cancellation than for a success. **That question is
unresolved** — do not treat the fork count as the explanation.

## SUPERSEDED: verify-release.sh's historical hook-floor derivation defect

The current verifier no longer reads that missing extracted-tree workflow or
falls back to five hooks. Both release paths call `tools/check-release-payload.py`.
Its explicit Claude/Codex floors are checked against the actual shipped tree by
`tests/test_release_payload.py`; this is a shared checked inventory, not a claim
that a workflow floor is derived at runtime. The finding below records the old
implementation and is retained as history.

Found by the v3.9.1 release review, 2026-09-09. **Deferred deliberately — fix this before
`verify-release.sh` certifies another release.**

`tools/verify-release.sh:73-74` reads the floor from
`"$ROOT/../.github/workflows/release.yml"`. But `ROOT="$WORK/skyrimvr-claude-toolkit-$VER"`
where `WORK=$(mktemp -d)` holds only the zip and the extracted tree — and **`.github` is
excluded from the release payload**, so that path never exists. `sed` fails, `floor` comes back
empty, and `case "$floor" in ''|*[!0-9]*) floor=5 ;;` substitutes the hardcoded 5.

So the check passes for the wrong reason. Two claims in the file are false as written:

- the inline comment: *"The FLOOR is derived from the workflow that built this zip, not retyped
  here"* — it is retyped, in the fallback.
- the header: *"the hook floor and the gate list are read from the working tree"* —
  `$ROOT/../` is the temp directory, not the working tree.

⚠ **It is invisible because the fallback is currently CORRECT.** `release.yml` asserts `-ge 5`
and the fallback says 5, so the assertion agrees with reality today and will keep agreeing until
someone adds a sixth hook — at which point the tool reports a stale floor with a comment
insisting it cannot be stale. That is the exact defect this line was written to repair: a
hardcoded `-ge 4` that was one release stale the moment it shipped.

**Fix shape:** derive from the checkout, not from the extracted payload — the working tree is
where `.github` actually lives, and the header already says that is the intent. Then prove it by
mutation: change `release.yml`'s floor and require the named check to go red. A derivation that
cannot be shown to follow its source is a hardcoded value with extra steps.

## Deadline clocks start at the spawn, not at the script's first line (T14b, 2026-10-15)

MEASURED at 4x CPU load (16 burners pinned to 4 of 28 cores): the Codex Windows PowerShell 5.1 entry refused a hung engine **51.3 s after the spawn**
against a 50 s margin (definition timeout 60 s). Its own clock started at the script's first line, after seconds of PowerShell start-up. Codex's
timeout runs from the spawn, so any wrapper must date its deadline from its own **process start time** (`Process.StartTime`, guarded: never later
than now, ignored if the read fails or is implausible). Test it by delaying inside the same process before the script runs, not by sleeping in the test.

## A hang before the watchdog fails open, and it does not need load (T14b, 2026-10-15)

MEASURED on the pre-fix tree `544efd4`: a hang before `run.py` arms its own timer ran to the harness cap (80 s; 140 s for session-kb-guard) on all six
Claude wrappers at idle, 1x and 2x alike, and for the three gate hooks all 9 runs at every level failed open (the agent gave up at 60 s and the command
ran). A load gate that sweeps only load levels, or only the failure paths that load makes worse, would not have shown it. Run every failure path at
idle too.

## A "kill latency" probe measures process start-up, not the bound (T14b, 2026-10-15)

MEASURED: a 3 s bound on a native sleeper ended in 3.4-5.4 s at 2x on the pre-fix tree and in 3.7-10.6 s at 4x and 8x on both trees. The extra time is
process start under load. It does not separate a working bound from a broken one; the scenario cells do. The investigation's 18-227 s kill times did not
reproduce here (INFERRED: the Git 2.55 / MSYS update may have changed it; not tested).

## Mutation anchors that quote a pinned value go stale when the pin moves (2026-10-14)

The Git pin move changed an installer SHA-256 and added its own mutation twin, but an older mutation still named the previous checksum. The branch's
targeted test run was green; the merged full suite failed `test_every_mutation_anchor_occurs_exactly_once[t6d-manual-steps-lose-their-hash]`. After any
pin bump, grep `tests/` for the old value and run the anchor test (about 6 s for 2,521 anchors).

## Long unattended work: use a detached process, not the agent's background shell (2026-10-14)

OBSERVED: Claude Code stopped a background `until ...; sleep` wait when the machine ran low on memory (a game was running); the detached
`pwsh -File` queue it was watching was unaffected. Launch overnight queues as detached processes that write their own log, and keep the agent's
background shell for short waits.

---

## Vanilla Game Mechanics & Reference (baseline that gameplay mods edit)

Purpose: when **sorting a modlist** or judging whether two gameplay mods conflict, an agent needs
the vanilla baseline those mods retune — the combat/crafting math, the records that get overridden,
and the bugs that make a mod a "fix" (which must load after what it patches). This is the vanilla
game data, not tooling. Sourced from **UESP** (`en.uesp.net`), the same primary source the rest of
this file uses; the fandom wiki `elderscrolls.fandom.com` 403s automated fetches and is not used.

Conventions that apply throughout:
- **FormIDs are written as 6-digit local IDs under the `00` (Skyrim.esm) prefix**, the way UESP/CK
  write them. The leading master byte is load-order dependent — see *Vanilla Record Reference* for
  how to read it and why the same local ID under a different prefix is a different master.
- Exact IDs must be confirmed in xEdit at edit time; this section names which records collide, not
  an exhaustive dump.
- **Verified on flat Skyrim SE/AE only.** VR was not checked for any of this; treat the formulas and FormIDs as unverified for VR.
- Older related entries: *Useful Vanilla MGEFs / Reference FormIDs* (in the VR Papyrus findings
  above) and *Reused vanilla records can carry gating Conditions*.

### Combat & Defense Formulas

**Weapon damage**

```
displayed damage = Round[ (base damage + smithing increase)
                          * (1 + skill/200)
                          * (1 + perk effects)
                          * (1 + item effects)
                          * (1 + potion effect)
                          * (1 + Seeker of Might bonus) ]
```
- Skill: each point of One-handed / Two-handed / Archery = +0.5% damage for the player, **+2% for
  NPCs** (NPCs scale ~4x faster off skill).
- Perk effects = `0.2 * Armsman/Barbarian/Overdraw rank` (rank 5 = `+1.0`, i.e. x2). Multiplicative
  with skill and smithing, so all three multiply together.
- Blessing of Auriel adds into `item effects`; Blessing of Boethiah adds into `potion effect`.
- Smithing increase must be the unrounded value before the Round step.
- Attack speed: `attacks per second = speed / 0.867` (base one-hand attack = 13/15 s).
  Representative speeds: dagger 1.3 (1.50 APS), sword 1.0 (1.154), war axe 0.9, mace 0.8 (0.923);
  greatsword 0.7, battleaxe 0.7, warhammer 0.6.
- Dual-wield off-hand attacks take 50% longer, so dual-wield is ~+33% DPS, not +100%. The off-hand
  weapon's speed governs the whole dual power attack.

Power attack damage (both hands summed):

```
damage = SUM_hands[ displayed * (1 + PAB) * (1 + PAP) * (1 + DPAP) * DPAM ]
PAB = 1 if power attacking            (=> x2 base)
PAP = 0.25 with Savage Strike / Devastating Blow
DPAP = 0.5 with Dual Savagery
DPAM = 1.5 (right) / 3 (left) on a dual power attack, else 1
```

| Attack | Damage | Stagger | Stamina |
|---|---|---|---|
| Single power attack | x2 | x1 | x1 |
| Dual-wield power (3 hits) | x1.5/hit | x1 | x0.5/hit |
| Bash | x0.5 | x0.5 | x1 |
| Power bash | x1.5 | x1.5 | x1 |
| Unarmed power | x2 | x0.5 | x1 |

- Power attack stamina = `(40 + weapon weight * 2) * attack cost multiplier * (1 - perk effect)`;
  Fighting Stance = -25%. Only 1 Stamina is required to power attack (stamina does not scale damage).
- Representative base damage: Iron Sword 9, Steel Sword 10, Daedric Sword 16, Dragonbone Sword 19;
  Iron Dagger 2, Daedric Dagger 6; Iron Greatsword 16, Dragonbone Greatsword 27; Iron Warhammer 24,
  Daedric Warhammer 31, Dragonbone Warhammer 33. Silver weapons: +20 flat vs undead/werewolves
  (not scaled by skill, but scaled by perks/enchantments).
- Difficulty multipliers (multiplied onto everything): Novice NPC x2 / player x0.5; Apprentice
  x1.5 / x0.75; Adept x1 / x1; Expert x0.75 / x1.5; Master x0.5 / x2; Legendary x0.25 / x3. Applies
  to followers and summons too.
- NPC-only `Extra Damage` perks multiply NPC damage independent of skill: x1.12, 1.5, 2, 2.5, 3,
  3.5, 4, 4.5, 5, 6 (2H column usually ~1 lower). Enemy-overhaul mods frequently edit these perk
  records.

*Source: https://en.uesp.net/wiki/Skyrim:Weapons , https://en.uesp.net/wiki/Skyrim:Damage ,
https://en.uesp.net/wiki/Skyrim:One-handed*

**Critical hits**

- Crit extra damage = typically `floor(base damage * 0.5)`, stored per-weapon in the weapon record;
  it **bypasses armor entirely**.
- Crit damage is NOT modified by tempering, weapon skill, Armsman/Barbarian/Overdraw, Fortify skill
  effects, sneak attacks, power attacks, ammo type, or draw power. It scales only with base damage —
  so perks like Bladesman are near-worthless on smithed high-base weapons (~3% expected gain at top
  tier).
- Deep Wounds / Bladesman / Critical Shot: rank 1 = 10% chance, rank 2 = 15% chance and
  `floor(base*0.5)*1.25`, rank 3 = 20% chance and `floor(base*0.5)*1.5`.
- Critical Charge / Great Critical Charge (sprinting power attack) = double crit, 100% chance =
  `2 * floor(base * 0.5)`; stacks on top of the x2 power attack. With Bladesman/Deep Wounds taken,
  the double crit only fires 10/15/20% of the time instead (net lower average sprinting-PA damage).
- Sneak attacks and crits are computed separately and stack.

*Source: https://en.uesp.net/wiki/Skyrim:Damage*

**Armor rating and damage reduction**

```
item rating  = CEILING[ (base rating + item quality) * (1 + 0.4 * (skill + skill effect)/100) ]
                * (1 + Custom Fit / Well Fitted) * (1 + Matching Set) * (1 + Juggernaut / Agile Defender)
shield rating = same but WITHOUT the armor-perk term
displayed rating = SUM(item ratings) + shield rating + armor effects (flesh spells, Lord Stone, ...)
DR% = displayed rating * 0.12 + 3.00 * (number of armor pieces worn, incl. shield)
```
- Each armor point = 0.12% reduction. Hidden bonus = **+25 per piece worn** (4 pieces + shield =
  +125 total), not shown in the UI; the `+3.00 * pieces` term is that hidden bonus as a percentage
  (25 * 0.12 = 3). Clothing/robes get no hidden bonus.
- **Hard cap: 80% physical DR** (x5 effective health). Total rating needed = 667 (= 567 displayed +
  100 hidden for 4 pieces; = 542 displayed + 125 hidden with a shield). The famous **567 displayed**
  (no shield) / **542 displayed** (with shield) cap values.
- Armor reduces **physical damage only**; magic is unaffected.
- Durability scales as `100 / (100 - DR%)` — hyperbolic, so late points of rating are worth more
  until the cap.
- NPC armor formula uses skill coefficient **1.5 instead of 0.4**, and 1.5625 instead of 1.25 for
  the unison perk.
- Smithing: Legendary quality adds +10 per piece, +20 for chest (total +50 for a matched set). With
  100 skill/perks, any tempered heavy set + shield caps; any tempered light set from Elven up +
  shield caps.
- `Dragonhide` = 80% flat physical ignore (armor-independent). Mage Armor perk multiplies flesh
  spells x2 / x2.5 / x3 **only if no ArmorLight/ArmorHeavy keyword item is worn** (shields don't
  count against it); Mage Armor boosts are "armor effects", not "skill effects" — they enter after
  the CEILING term.

*Source: https://en.uesp.net/wiki/Skyrim:Armor , https://en.uesp.net/wiki/Skyrim:Alteration*

**Armor penetration**

Effective rating = `rating * (1 - penetration)`; i.e. to still hit 80% DR you need
`total rating = 667 / (1 - penetration)`.

| Source | Penetration |
|---|---|
| Bone Breaker (mace, 1h) rank 1/2/3 | 25% / 50% / 75% |
| Skullcrusher (warhammer, 2h) rank 1/2/3 | 25% / 50% / 75% |
| Enhanced Crossbow (Dawnguard) | 50% of armor ignored |

- UESP's exact stated figures: vs 75% penetration you need **2,668 total rating**
  (`667 / (1-0.75)`) = **2,268 displayed** (`567 / (1-0.75)`).
- INFERRED (same arithmetic, UESP does not print these): vs 50% (crossbows) → 1,334 total /
  **1,234 displayed with 4 pieces** (the commonly quoted 1234) / 1,209 with shield; vs 25% → 889
  total / 789 displayed. The often-quoted "~860" figure does **not** appear on the UESP Armor page —
  treat it as UNSOURCED/community; UESP's only printed penetration-adjusted numbers are 2,668 and
  2,268.
- Penetration is multiplicative against rating, so it counters the hidden +25/piece too.
  Bone Breaker/Skullcrusher do nothing vs unarmored targets (dragons, animals, Dwemer constructs have
  0 armor rating). Critical hits ignore armor outright (above) — a separate layer from penetration.

*Source: https://en.uesp.net/wiki/Skyrim:Armor , https://en.uesp.net/wiki/Skyrim:One-handed*

**Blocking**

```
Blocked% = (base term) * (1 + Shield Wall perk) * (1 + sum of 4 Fortify Block enchants)
           * (1 + Fortify Block potion) * (fBlockPowerAttackMult if power attack)
shield base term = 45 + 0.2 * BASE (untempered) shield armor rating * (1 + BlockSkill * 1.5 / 100)
weapon base term = 30 + 0.2 * ENEMY weapon base damage * (1 + BlockSkill * 1.5 / 100)
fBlockPowerAttackMult = 0.66
```
- **Block cap 85%**; stacks multiplicatively with armor's 80% → up to **97% total physical
  mitigation**.
- Shield Wall actual values are 10/20/30/40/50% per rank (in-game text is wrong for ranks 1,2,4,5;
  USSEP corrects it).
- Weapon blocking vs creatures/unarmed enemies is flat (enemy weapon base damage = 0): 30% base,
  45% with 5/5 Shield Wall — Block skill does nothing in that matchup. Only the *enemy's* weapon
  base damage matters, not yours (warhammers are "easier to block" than daggers).
- Only base shield armor rating counts for the block formula (tempering still counts for the armor
  formula). Any shield with base rating ≥ 23.33 hits 85% vs normal attacks at 100 skill + 5/5
  Shield Wall. Power attacks need an extra ~52% Fortify Block (from 4 enchants/potions) to still
  reach 85%, because of the x0.66.
- **Elemental Protection** (Block tree): blocking with a shield reduces incoming fire/frost/shock
  **health** damage by 50%, from any direction; stacks with other spell resistances; does not reduce
  magicka/frost-stamina drain.
- Bashing: `weapon bash = base damage * 0.5 * [0.1 + (BlockSkill/100) * (0.5-0.1)] * 3 (power bash)
  * 5 (Deadly Bash)`; `shield bash = base armor rating * 0.5 * [0.1 + (BlockSkill/100) *
  (0.25-0.1)] * 3 * 5`. Enemy variants use 0.25/0.05 caps (lower). Stamina: block ~small, bash 35,
  power bash 55. Fortify Block does NOT increase bash damage.
- Block hitbox = 35° cone (`fCombatHitConeAngle = 35`).
- Bugs worth knowing when patch state matters: vanilla Deflect Arrows forces 85% block vs everything
  (fixed by Official Patch 1.5); NPC `fCombatBlockTimeMax` ≈ 10,000 s (fixed by USSEP 4.3.4 → 16 s).
- Block skill XP scales with damage actually mitigated; shield block = 2x weapon-block XP, shield
  bash = 5x weapon-bash XP.

*Source: https://en.uesp.net/wiki/Skyrim:Block*

**Magic / elemental resistance stacking**

- `Magic Resistance` reduces offensive spell damage by the displayed %; **stacks additively across
  sources but hard-capped at 85%** (for the player; NPCs/followers with 100% are truly immune).
- Sources: Alteration perk Magic Resistance 10/20/30% (ranks at 30/50/70 skill), plus Resist Magic
  enchantments/potions/racial effects — all funnel into the same 85% cap.
- Elemental resists (Resist Fire / Frost / Shock) are each **capped at 85% separately** and are
  applied **multiplicatively after** Resist Magic: UESP's worked example — 100-pt fire spell with
  Resist Magic 85% → 15 pts, then Resist Fire 85% → 2.25 pts = **97.75% total**. So magic resist and
  elemental resist are two distinct layers, not one pool.
- **Spell Absorption is rolled before Magic Resistance** and is a chance to nullify the whole spell
  and refund its magicka (Atronach Stone/perk paths to 100% absorption). Absorption, Resistance and
  Absorb Magicka do not stack as one mechanic — absorption just wins first when it procs.
- Weakness to Magic (alchemy only, no disenchantable enchantment) amplifies damage from magical and
  elemental attacks; weakness-from-poison is not re-amplified by an existing weakness on the target,
  but potion-side-effect and enchantment sources can amplify themselves and each other; Weakness to
  Poison amplifies Weakness to Magic.
- **MOD SORT: any mod that raises or rewrites the 85% caps, reorders resistance application, or edits
  the Magic Resistance perk (00053128-2a) conflicts with every other mod touching those —
  load-order-last wins. Elemental-resist enchantment overhauls and enemy "immune to X" overhauls
  fight the same records.**

*Source: https://en.uesp.net/wiki/Skyrim:Magic_Overview , https://en.uesp.net/wiki/Skyrim:Resist_Fire ,
https://en.uesp.net/wiki/Skyrim:Weakness_to_Magic , https://en.uesp.net/wiki/Skyrim:Alteration*

**Spell cost**

```
Cost = base cost * skill multiplier * perk multiplier * equipment multiplier * dual cast multiplier
skill multiplier = 1 - (skill / 400)^0.65        (= ~0.5939 at skill 100, i.e. -41%)
equipment multiplier = 1 - (sum of Fortify <school> enchantment reductions)   // 4x25% = 0 cost
dual cast = x2.8 cost for x2.2 effect (vs two single casts: 2.0 cost for 2.0 effect)
```
- Perk multipliers: Novice/Apprentice/Adept/Expert/Master tier perks halve cost (x0.5), stacking
  with the skill term. Dual-cast Destruction = +10% damage for +40% magicka; Healing = +10% heal
  rate for +40% magicka.

*Source: https://en.uesp.net/wiki/Skyrim:Magic_Overview*

**Stagger**

- Stagger chance = base chance x weapon stagger multiplier x attack-type multiplier. Weapon stagger
  values: dagger 0, sword 0.75, war axe 0.85, mace 1.0, greatsword 1.1, battleaxe 1.15, warhammer
  1.25 (Stalhrim non-dagger melee +0.1), bows 0 (Power Shot: 50% chance of 0.25), crossbows 0.75.
- Attack-type multipliers (from the power attack table): normal/power single x1, bash x0.5, power
  bash x1.5, unarmed power x0.5.
- Perk/shout stagger sources: Impact (Destruction dual cast), Power Shot (Archery), Power Bash,
  Force Without Effort (improves overall stagger %), Unrelenting Force word 3 / Cyclone / Staff of
  Hasedoki → ragdoll instead of stagger. Stagger interrupts the current action; ragdoll adds a
  knockdown + recovery delay.
- UNSOURCED: the numeric *base* stagger chance per attack is not stated on these UESP pages; only
  the multipliers above are sourced. The vanilla `stagger` column on weapon records is the
  per-weapon multiplier.
- **MOD SORT: stagger overhauls typically edit the weapon records' stagger values, the Power
  Bash/Impact perks, and/or the base-chance game settings — two stagger mods stacked will
  double-apply or overwrite; last in load order wins.**

*Source: https://en.uesp.net/wiki/Skyrim:Combat , https://en.uesp.net/wiki/Skyrim:One-handed ,
https://en.uesp.net/wiki/Skyrim:Weapons*

**Where these numbers live (combat-specific; perk/MGEF FormIDs: see Vanilla Record Reference)**

- Weapon damage: weapon records (DNAM base damage, stagger, speed), skill/perk records (Armsman,
  Barbarian, Overdraw, Bone Breaker, Skullcrusher), `crExtraDamage*` NPC perks, difficulty
  multipliers. Two damage overhauls editing the same perk record = last plugin wins; a
  global-formula mod (e.g. one that changes `skill/200`) usually stacks multiplicatively with
  record-level mods (different layers), but two record-level mods on the same weapon/perk conflict.
- Armor: ARMR item ratings, the effect magnitude chain (skill coefficient 0.4/1.5, hidden +25/piece,
  0.12%/point, 80% cap), flesh spells + Mage Armor perk, Dragonhide. Smithing-cap mods and
  armor-rating overhauls both edit these; a "raises the armor cap" mod must edit the cap itself,
  conflicting with any other cap mod.
- Block: `fBlockPowerAttackMult` (0.66), `fShieldBaseFactor` (45), `fBlockWeaponBase` (30),
  `fWeaponBash*`/`fShieldBash*`/`fStamina*Bash*` game settings, Shield Wall perk, Deflect Arrows
  bug state (Official Patch 1.5 / USSEP dependent).
- Resistances: 85% magic cap, 85% per-element cap, application order (absorb → magic resist →
  elemental resist). Enchantment overhauls raising resist caps conflict with each other and with any
  mod enforcing vanilla caps.
- Criticals: crit damage is per-weapon record data and armor-bypassing; a mod adding "crit scales
  with X" changes the engine-side crit path and conflicts with weapon-record crit edits.
- General rule: game *settings* (gameSetting records) and *perk records* are single-owner — if two
  gameplay mods touch the same one, the later plugin in load order wins the whole record, not a
  merge; formula mods and record mods stack multiplicatively only when they touch different layers.

### Crafting & Leveling Formulas

Reference for judging whether crafting mods stack, overwrite, or fight. Smithing (cross-checked
against UESP Leveling: `XP = quantity * (25 + 3*value^0.65)`; tempering
`XP = 3.8 * Δvalue^0.5 * Δquality^0.5`) is consistent with the combat section above.

**General skill-XP machinery (all skills):**

```
skill XP to next level  = SkillImproveMult * (currentlevel-1)^1.95 + SkillImproveOffset
skill XP from one use   = SkillUseMult * baseXP + SkillUseOffset
character XP per skillup = new skill level * fXPPerSkillRank (1.0)
```

| Skill | UseMult | ImproveMult | ImproveOffset | baseXP per craft |
|---|---|---|---|---|
| Alchemy | 0.75 | 1.6 | 65 | 1 per gold value created + 1 flat per successful potion |
| Enchanting | 900 | 1 | 170 | 1 per item enchanted (flat); disenchant 1 per 400 gold of enchantment value; recharge 0.05/0.1/0.2/0.4/0.6 (Petty→Grand) |
| Smithing | 1 | 0.25 | 300 | 25 + 3*value^0.65 per item |

- Total XP 15→100: Smithing ~91,601; Enchanting ~278,852; Alchemy ~428,568. Enchanting XP is
  completely independent of item value, enchantment, or soul size — flat per craft; Fortify
  Enchanting potions do NOT increase Enchanting XP. Alchemy XP is dominated by the gold-cost term
  (flat part is +1, vs Smithing's flat +25 — this is why pre-1.5 iron-dagger spam worked for
  Smithing but Alchemy always rewarded high-value potions). XP multipliers (Mage/Thief/Warrior Stone,
  Well Rested, etc.) stack multiplicatively on these.

*Source: https://en.uesp.net/wiki/Skyrim:Leveling , https://en.uesp.net/wiki/Skyrim:Alchemy ,
https://en.uesp.net/wiki/Skyrim:Enchanting*

**Alchemy — magnitude:**

```
magnitude = round(Base_Mag * 4 * (1 + AlchemySkill/200)
                  * (1 + FortifyAlchemy%/100) * (1 + AlchemistPerk%/100)
                  * (1 + PhysicianPerk%/100) * (1 + Benefactor%orPoisoner%/100)
                  * (1 + SeekerOfShadows%/100))
```
- `4` = game setting `fAlchemyIngredientInitMult`; the skill term is
  `1 + (fAlchemySkillFactor-1)*skill/100` with `fAlchemySkillFactor=1.5` (+0.5%/level, +50% at
  100). Only ONE of Benefactor (+25, potions with beneficial effects) / Poisoner (+25, poisons) is
  ever active; Physician (+25) only on Restore Health/Magicka/Stamina. Alchemist perk is
  0/20/40/60/80/100. Duration-scaled effects (Invisibility, Paralysis, Slow, Waterbreathing only)
  apply the same multiplier to `Base_Dur`; all other effects have fixed duration.
- `Base_Mag`/`Base_Dur` come from the EFIT field of the **highest-priority ingredient** (priority =
  effect cost in CK data), not averaged. Non-standard ingredients override: River Betty 2.5x Damage
  Health magnitude, Crimson Nirnroot 3x, Deathbell 1.5x, Jarrin Root 100x mag / 158.5x value,
  Nirnroot 12.6x gold value only.

**Alchemy — gold value (= XP driver):**

```
gold_cost = floor(Base_Cost * max(Magnitude^1.1, 1) * (Duration>0 ? (Duration/10)^1.1 : 1))
```
- Multi-effect potions: cost computed per effect, total = sum. Highest-cost effect decides name and
  potion-vs-poison classification — decided BEFORE perks are applied. Purity perk zeroes
  opposing-sign effects, which *reduces* gold value. Hidden internal magnitudes/durations still feed
  the cost formula (e.g. Cure Disease value rises with "magnitude" that does nothing in-game). Known
  engine bug: many Damage Health ingredients use a 1s duration in the cost calc instead of 0s.

*Source: https://en.uesp.net/wiki/Skyrim:Alchemy , https://en.uesp.net/wiki/Skyrim:Alchemy_Effects*

**Enchanting — apparel magnitude:**

```
vanilla:        net = base * soulMult * skillMult(effective skill) * (1+EnchanterPerk) * (1+specificPerk) * (1+SeekerOfSorcery)
with USKP:      net = base * soulMult * skillMult * (1+FortifyEnchPotion) * (1+EnchanterPerk) * (1+specificPerk) * (1+SeekerOfSorcery)

skillMult = 1 + (skill/100) * (skill/100 - 0.14) / 3.4     // quadratic; +25.29% at skill 100
effective skill (vanilla) = base skill + FortifyEnchanting potion magnitude + Ahzidal's Genius(10) + Haunting Gift(10 each)
```
- Soul multiplier: Grand 1, Greater 2/3, Common 1/3, Lesser 1/6, Petty 1/12. Soul charge capacity:
  3000/2000/1000/500/250. Base magnitude = value at Grand soul, 0 skill, no perks.
- The vanilla/USKP split is real and behavioral: vanilla adds the potion into *skill* (subject to the
  quadratic and the 200 asymptote); USKP applies it as a separate multiplier and stops
  Necromage/Fortify-Restoration from touching enchanting. A USKP-style patch and a gameplay overhaul
  that edits the same Fortify-X enchantment records conflict directly — patch must load after.
- Enchanter perk +20/40/60/80/100%; Fire/Frost/Storm Enchanter and Insightful (skill enchants)/Corpus
  (Health/Magicka/Stamina + regen) each +25%; these ADD together and also stack with Destruction
  Augmented perks on weapons (up to x2.25 for two elements). Stalhrim: Frost/Resist Frost/Chaos +25%.
- Weapon max magnitude uses the same formula (soul size excluded unless it would drop charges below
  1), then x(1+Destruction perk); charges per use
  `= 3 * (baseCost * mag/maxMag)^1.1 * (1 - sqrt(skill/200))`; net uses = soul capacity / charges per
  use. Skill 200 = infinite charges (asymptote); 110 (Ahzidal's Genius) is the vanilla cap; Fortify
  Enchanting is deliberately NOT skill for this reason.
- Caps/ceilings: max natural enchant multiplier ~3.132x (100 skill, all perks, Grand soul);
  ~4.21875x with a Fortify Enchanting potion without exploits. Enchanted-item value = base item
  value + enchantment value (additive) — and a documented bug makes the *monetary value* of player
  enchantments DECREASE as Enchanting skill rises (charges are recalculated at use time, not craft
  time: a weapon enchanted under a huge potion drains fast when later used without it).
- Disenchant: destroys item, teaches effect only (magnitude irrelevant), one-time per effect;
  XP = enchantment gold value/400. Fortify Enchanting potion window is 30s; the item-rename screen
  pauses the timer (standard speedrun micro-optimization).

*Source: https://en.uesp.net/wiki/Skyrim:Enchanting , https://en.uesp.net/wiki/Skyrim:Enchanting_Effects*

**Alchemy <-> Enchanting feedback loop:**

```
Fortify Alchemy gear -> stronger Fortify Enchanting potion -> stronger Fortify Alchemy enchantments -> ...
```
- Amplifiers in the loop: 5 gear slots possible only via the Falmer-helmet+circlet bug (otherwise 4);
  Seeker of Shadows (+10% alchemy) / Seeker of Sorcery (+10% enchants) are mutually exclusive but
  freely swappable at the Sallow Regent Black Book; Ahzidal's Genius +10 Enchanting skill; vampire
  Necromage +25% to all self enchantments/potions (bug; USKP removes it). Fortify Restoration potions
  re-amplify active Fortify Skill potions on re-equip — that is the famous unbounded exploit;
  multiple same-type Fortify Skill potions do NOT stack, but Restoration boosts any of them.
- Community/UESP stated ceilings (page carries verification-needed flags — treat as INFERRED): max
  Fortify Enchanting potion +40% legit vanilla, +61% with falmer+circlet and Necromage; one tester
  reached 122% after ~8 loop rounds without Necromage; USKP world: 44% max potion, 114% with Rare
  Curios ingredients, 127% with Haunting Gift, strongest Fortify Alchemy enchant +68%, +272% total
  across four pieces.
- Loop has diminishing returns because each multiplier is (1+x) applied to a base that itself grew;
  there is no hard engine cap on potion magnitude or enchant magnitude — balance is entirely a data
  decision, which is exactly what crafting overhauls change.

*Source: https://en.uesp.net/wiki/Skyrim:Enchanting ("Maximizing Enchantment Power") ,
https://en.uesp.net/wiki/Skyrim:Leveling (exploit section) ,
https://en.uesp.net/wiki/Skyrim:Alchemy_Effects (stacking rules)*

**MOD-SORTING implications (crafting):**

- Every formula above is data-driven, not code: `fAlchemyIngredientInitMult`, `fAlchemySkillFactor`,
  per-effect MGEF `BaseCost`/`BaseMag`/flags (`PowerAffectsMagnitude`, `NoMagnitude`,
  `NoDuration`), per-ingredient INGR `EFIT` entries, ENCH base magnitudes/costs, and the perk records.
  Two crafting overhauls that edit the same MGEF/INGR/ENCH/perk records do NOT merge — plain record
  conflict, last plugin in load order wins, silently. There is no "stacking" of two magnitude
  retunes.
- The pivot records gameplay mods fight over: the **Fortify Alchemy / Fortify Enchanting / Fortify
  Smithing / Fortify Restoration MGEFs and their enchantment entries** (vanilla has no obtainable
  Fortify Enchanting enchantment — only the potion MGEF; mods adding the enchantment edit
  MGEF+ENCH+keyword sets). A mod that retunes these changes the entire loop's balance point; load
  order between it and USKP-style patches (which altered the vanilla Necromage/Fortify-Restoration
  enchanting interactions) is decisive — patches after overhauls, always.
- Enchantment slot legality is keyword-driven (which body slots accept which effect). Mods that add
  enchantable slots or new Fortify-Skill enchantments edit KYWD+ENCH; conflicts show up as
  "enchantment missing from station" rather than stat fights.
- Ingredient strength multipliers (Giant's Toe 5.9x Fortify Health cost, Salmon Roe, Rare Curios
  doubling ingredients) live on INGR records — an alchemy overhaul overriding INGRs and a mod adding
  new ingredients both touch the same records if they edit the same ingredients; USSEP already
  overrides many INGR/MGEF records, so any overhaul must load after it or inherit its fixes/bugs
  unpredictably.
- XP-rate changes (mods making crafting level slower) edit the Actor Value skill-use/improve
  multipliers and/or game settings — global ActorValue data, so *any* two mods touching them conflict
  even if they target different skills, depending on how they bundle (raw AV edits vs scripts).

*Source: https://en.uesp.net/wiki/Skyrim:Alchemy_Effects ,
https://en.uesp.net/wiki/Skyrim:Enchanting_Effects , https://en.uesp.net/wiki/Skyrim:Leveling
(record-level mapping INFERRED from formula-to-CK-field correspondence)*

Skipped: full per-effect Base_Cost/Base_Mag tables (389-line UESP tables — fetch
`Skyrim:Alchemy_Effects` when exact numbers are needed). Risk: several loop-ceiling numbers (+61%,
122%) sit on UESP pages flagged "verification needed" — treat as community-computed.

### Vanilla Record Reference (FormIDs & EditorIDs)

Recognize which vanilla records a gameplay mod overrides or depends on, and predict who wins.

**How to read a FormID**

- A FormID is **8 hex digits**: the **first 2 digits = master index byte**, the last **6 digits =
  local ID** inside the file that owns the record. Treat it as a *local ID + load-order-dependent
  prefix*, never an absolute address.
- The master byte is the index of the owning master **in the referencing plugin's own master list**
  (for Skyrim.esm records, in the game's load order). `000cb40d` and `010cb40d` are two different
  records — the same local ID under a different prefix means a different master file.
- Conventional prefixes (vanilla SSE/AE order): `00` = Skyrim.esm (never changes), `01` = Update.esm,
  `02` = Dawnguard.esm, `03` = Hearthfires.esm, `04` = Dragonborn.esm. Load any plugin between them
  and the DLC prefixes shift; always read the prefix from the actual load order.
- `ff` = runtime/dynamically allocated (summons, script-created items) — never document, never patch.
- `FE` = light/ESL-flagged plugins (incl. Creation Club): the master byte becomes `FE`, followed by a
  **3-hex-digit load-order slot** (`000`–`FFF`) and a 3-hex local ID (`FE000ABC`). The slot is
  assigned by plugin order (CC content orders alphabetically among itself), so it **varies per
  install** — treat any `FExxxxxx` ID as "per-install, confirm with `help` or xEdit".
- **EditorIDs are stable across load orders; FormID prefixes are not.** When diagnosing which record
  a mod touched, match on EDID (`Enchanter00`, `CraftingSmithingForge`).
- Conflict rule for everything below: records are **overwritten whole; field-by-field merging does
  not exist**. The last plugin in load order that overrides a record wins *all* of it; two mods
  touching the same record need a conflict-resolution patch (forward both changes).
- IDs below are 6-digit local IDs under the `00` Skyrim.esm prefix. Perk IDs cross-checked against
  Skyrim.esm (EDID → FormID); keyword/MGEF IDs likewise.

*Source: https://en.uesp.net/wiki/Skyrim:Form_ID*

**Key gameplay perks (PERK records)**

Most-overridden vanilla perk records. FormID = **rank 1**.

| Perk (EDID) | FormID | Who typically overrides |
|---|---|---|
| SteelSmithing | 000cb40d | USSEP, crafting overhauls (CCO/CCOR), armor-set mods |
| ArcaneBlacksmith | 0005218e | crafting overhauls, magical-item tempering patches |
| DragonArmor | 00052190 | crafting overhauls, DLC-item add-ons |
| Armsman00 (One-handed) | 000babe4 | combat overhauls (Valhalla etc.), perk overhauls |
| Bladesman / BoneBreaker / HackAndSlash | 0005f56f / 0005f592 / 0003fffa | weapon-specialization rebalances, perk overhauls |
| DualFlurry / DualSavagery | 00106256 / 00106258 | combat overhauls, dual-wield fixes |
| SavageStrike / CriticalCharge / FightingStance | 0003af81 / 000cb406 / 00052d50 | combat overhauls |
| Barbarian / Champion'sStance / DevastatingBlow / GreatCriticalCharge | 000babe8 / 00052d51 / 00052d52 / 000cb407 | combat overhauls |
| DeepWounds / Sweep / Warmaster / Skullcrusher | 0003af83 / 0003af9e / 0003afa7 / 0003af84 | combat overhauls, bleed-mod patches |
| ShieldWall / DeflectArrows / ElementalProtection / BlockRunner | 000bccae / 00058f68 / 00058f69 / 00106253 | combat overhauls, shield mods |
| PowerBash / DeadlyBash / DisarmingBash / ShieldCharge / QuickReflexes | 00058f67 / 0005f594 / 00058f66 / 00058f6a / 000d8c33 | combat overhauls, VR mods (timed-block rewrites) |
| Stealth00 / Backstab / DeadlyAim / AssassinsBlade | 000be126 / 00058210 / 001036f0 / 00058211 | stealth overhauls, sneak-damage rebalances |
| MuffledMovement / SilentRoll / ShadowWarrior | 00058213 / 00105f24 / 00058214 | stealth overhauls |
| Overdraw00 / CriticalShot / PowerShot / SteadyHand / Bullseye | 000babed / 00105f1c / 00058f62 / 00103ada / 00058f64 | archery overhauls, combat overhauls |
| AugmentedFlames / AugmentedFrost / AugmentedShock | 000581e7 / 000581ea / 00058200 | magic/damage rebalance mods, perk overhauls |
| IntenseFlames / DeepFreeze / Disintegrate | 000f392e / 000f3933 / 000f3f0e | elemental-balance mods |
| Impact / DestructionDualCasting | 000153d2 / 000153cf | spell-cost/stagger rebalances |
| Novice–Master tier perks (Destruction) | 000f2ca8, 000c44bf, 000c44c0, 000c44c1, 000c44c2 | spell-cost overhauls (analogous rows per school) |
| Regeneration (Restoration) | 000581f8 | heal-rate rebalances, vampire mods, perk overhauls |
| Necromage / Recovery / Respite / WardAbsorb | 000581e4 / 000581f4 / 000581f9 / 00068bcc | magic overhauls, undead/vampire mods |
| Enchanter00 / InsightfulEnchanter / CorpusEnchanter / ExtraEffect | 000bee97 / 00058f7e / 00058f7d / 00058f7f | enchanting overhauls, exploit-fix patches |
| Alchemist00 / Physician / Benefactor / Poisoner / Experimenter / Purity | 000be127 / 00058215 / 00058216 / 00058217 / 00058218 / 0005821d | alchemy overhauls (condition on KYWDs — see keyword cluster) |
| MagicResistance / Atronach / Stability / MageArmor30 | 00053128 / 000581f7 / 000581fc / 000d7999 | magic overhauls, standing-stone mods (Atronach stone shares this perk) |
| Juggernaut00 / WellFitted / Conditioning / MatchingSet(Heavy) / ReflectBlows | 000bcd2a / 00058f6f / 00058f6d / 00107832 / 00105f33 | armor-rating rebalances, perk overhauls |
| AgileDefender00 / CustomFit / MatchingSet(Light) / Unhindered / WindWalker | 000be123 / 00051b1b / 00051b17 / 00051b1c / 00105f22 | armor-rating rebalances, perk overhauls |
| Haggling00 / Merchant / Fence / Investor / MasterTrader | 000be128 / 00058f7a / 00058f79 / 00058f7b / 001090a5 | economy overhauls, merchant mods |
| MysticBinding / OblivionBinding / Summoner / Atromancy / TwinSoulS | 000640b3 / 000d799c / 00105f30 / 000cb419 / 000d5f1c | conjuration rebalances, bound-weapon mods |
| Unbreakable / TreasureHunter / ExtraPockets / Poisoned / PerfectTouch | 00058209 / 00105f26 / 00096590 / 00105f28 / 00058205 | thief-tree overhauls, lockpick mods |

**Rank note (critical for perk overhauls):** every rank of a multi-rank perk is a **separate PERK
record with its own FormID**, EDID = base + skill-level suffix. UESP tables list rank 1 only.
Verified examples: `Armsman00 000babe4`, `Armsman20 00079343`, `Armsman40 00079342`,
`Armsman60 00079344`, `Armsman80 00079345`; `Alchemist00 000be127` → `Alchemist80 000c07cd`
(20/40/60 = 000c07ca/cb/cc); `MageArmor30/50/70 = 000d7999 / 000d799a / 000d799b`; same pattern for
`Overdraw`, `Juggernaut`, `Stealth`, `ShieldWall`, `Barbarian`, `AgileDefender`, `Enchanter`. A mod
that "changes Armsman" but not its 4 higher-rank records silently keeps vanilla values on ranks 2–5 —
search by EDID prefix in xEdit, never by one FormID.

**Load-order consequence:** perk records are whole-record conflicts. A perk overhaul (Ordinator,
Vokrii, similar) overrides the vanilla PERK records it touches and must load **after** USSEP and
after any balance patch that edits the same perk — or, if the balance patch must win, the overhaul's
version of that whole perk record must be forwarded into a patch. A patch ordered *before* the
overhaul silently loses every field. Condition-list edits are the usual casualty.

*Source: https://en.uesp.net/wiki/Skyrim:Smithing , https://en.uesp.net/wiki/Skyrim:One-handed ,
https://en.uesp.net/wiki/Skyrim:Block , https://en.uesp.net/wiki/Skyrim:Sneak ,
https://en.uesp.net/wiki/Skyrim:Archery , https://en.uesp.net/wiki/Skyrim:Destruction ,
https://en.uesp.net/wiki/Skyrim:Restoration , https://en.uesp.net/wiki/Skyrim:Enchanting ,
https://en.uesp.net/wiki/Skyrim:Alchemy , https://en.uesp.net/wiki/Skyrim:Alteration ,
https://en.uesp.net/wiki/Skyrim:Heavy_Armor , https://en.uesp.net/wiki/Skyrim:Light_Armor
(rank records cross-checked against Skyrim.esm)*

**Key magic effects (MGEF records)**

**The single most important fact:** vanilla keeps **three parallel MGEF records per effect** — one
channel for spells/abilities, one prefixed `Alch` for potions/poisons, one prefixed `Ench` for gear
enchantments. Editing one channel changes only that channel. A "potions are too strong" fix belongs
in `Alch*`; a "this enchantment is too strong" fix in `Ench*`; they are different records and do
**not** conflict with each other.

| Effect family | Alchemy channel (potion/poison) | Enchantment channel (gear) | Spell / ability channel |
|---|---|---|---|
| Damage Health | AlchDamageHealth 0003eb42 | (damage-on-contact handled per weapon ENCH) | DamageHealthConcSelf 000f4995 |
| Damage Magicka / Stamina | AlchDamageMagicka 0003a2b6 / AlchDamageStamina 0003a2c6 | EnchMagickaDamageFFContact 0005b44f / stamina-damage variant 0005b450 | — |
| Damage Magicka/Stamina Regen (poisons) | AlchDamageMagickaRate 00073f2b / AlchDamageStaminaRate 00073f2c | AbDamageStaminaRateVisible 0001729d / AbDamageHealRateVisible 0001729b | AbDamageHealRate 000c1e8c, AbDamageStaminaRate 000c1e8d, AbDamageMagickaRate 000a1a45 |
| Restore Health / Magicka / Stamina | AlchRestoreHealth 0003eb15, AlchRestoreStamina 0003eb16, AlchRestoreMagicka 0003eb17 | (heal-on-armor via EnchFortifyHealRate…) | RestoreHealthFFSelf 0001cea6, RestoreMagickaFFSelf 000f4996, RestoreStaminaFFSelf 0002f3ba |
| **Regenerate** (Fortify * Rate) | AlchFortifyHealRate 0003eb06, AlchFortifyMagickaRate 0003eb07, AlchFortifyStaminaRate 0003eb08 | EnchFortifyHealRateConstantSelf 0007a0f8, …MagickaRate 0007a0fd, …StaminaRate 0007a105 | AbFortifyHealRate 00057b49, AbFortifyMagickaRate 0005312b (standing stones, race/ability) |
| **Resist** Fire/Frost/Shock/Magic/Poison/Disease | AlchResistFire 0003eaea, AlchResistFrost 0003eaeb, AlchResistShock 0003eaec, AlchResistMagic 00039e51, AlchResistPoison 00090041 | EnchResistFireConstantSelf 00048c8b, …Frost 00048f45, …Shock 00049295 (EDID typo `ResistShockt`), …Magic 000b7a35, …Disease 00100e60, …Poison 000ff15e | AbResistFire 00024314, AbResistFrost 00024315, AbResistShock 00024318, AbResistMagic 00053124, AbResistPoison 000aa024, AbResistDisease 000e40d3, AbResistDamage 000cab90 |
| **Absorb** Health/Magicka/Stamina | — (no vanilla absorb potions) | EnchAbsorbHealthFFContact 000aa155, …Magicka 000aa156, …Stamina 000aa157 | AbsorbHealthConcAimed 0008d5be, AbsorbMagickaConcAimed 000f1cfe, AbsorbStaminaConcAimed 000f1d01 |
| Elemental damage (Fire/Frost/Shock) | — | EnchFireDamageFFContact 0004605a, EnchFrostDamageFFContact 0004605b, EnchShockDamageFFContact 0004605c | FireDamageFFAimed 00012f03, FrostDamageFFAimed 0001cea2, ShockDamageFFAimed 0001cea8; concentration: 00013ca9 / 00013caa / 00013cab |
| Paralysis | AlchParalysis 00073f30 | EnchParalysisFFContact 000acbb6 | ParalysisFFContact 0006f702 |
| Cure Disease / Poison | AlchCureDisease 000ae722, AlchCurePoison 00109add | — | CureDiseaseEffect 000fbff5 |
| **Fortify skill** block | `AlchFortify*` **0003eb18–0003eb29** (Alchemy 0003eb18, OneHanded 0003eb19, TwoHanded 0003eb1a, Archery 0003eb1b, Block 0003eb1c, Smithing 0003eb1d, HeavyArmor 0003eb1e, LightArmor 0003eb1f, Pickpocket 0003eb20, Lockpicking 0003eb21, Sneak 0003eb22, Barter 0003eb23, Alteration 0003eb24, Conjuration 0003eb25, Destruction 0003eb26, Illusion 0003eb27, Restoration 0003eb28, Enchanting 0003eb29) | `EnchFortify*ConstantSelf` **0007a0f2–0007a106** (Block 0007a0f3, Carry 0007a0f4, Conjuration 0007a0f5, Destruction 0007a0f6, HeavyArmor 0007a0f9, LightArmor 0007a0fb, Lockpicking 0007a0fc, OneHanded 0007a0ff, Pickpocket 0007a100, Restoration 0007a101, Smithing 0007a102, Sneak 0007a103, Speechcraft 0007a104, StaminaRate 0007a105, TwoHanded 0007a106) + Alchemy 0008b65c, Health 000493aa, Magicka 00049504, Stamina 00049507, ArmorRating 00017120 | ability/race forms (e.g. AbFortify*) |

**Who edits which channel, and the load-order consequence:**
- *Alchemy/poison balance mods, ingredient overhauls* → `Alch*` MGEF (magnitude, duration, BaseCost
  drives potion gold value).
- *Enchanting overhauls, item enchant rebalances, Summermyst-style additions* → `Ench*` MGEF and the
  ENCH records that reference them.
- *Combat overhauls, magic overhauls, spell mods* → the spell-channel MGEF (and separately the SPEL
  records — a mod can change a spell without touching its MGEF).
- *Standing stone / race / vampire / werewolf mods* → the `Ab*` (ability) MGEF, e.g. Atronach stone
  and `AbFortifyHealRate`.
- *USSEP* overrides scattered MGEF across all channels.
- Same-channel edits collide: last plugin wins the entire MGEF (magnitude, cost, flags, keyword list
  KWDA). Different channels never collide. Because a single MGEF's KWDA drives alchemy perk logic, a
  mod that rewrites `KWDA` on `Alch*` effects can break Physician/Benefactor/Poisoner even if
  magnitudes look fine — check KWDA in patches.

*Source: https://en.uesp.net/wiki/Skyrim:Damage_Health ,
https://en.uesp.net/wiki/Skyrim:Fortify_One-handed , https://en.uesp.net/wiki/Skyrim:Regenerate_Health ,
https://en.uesp.net/wiki/Skyrim:Resist_Fire , https://en.uesp.net/wiki/Skyrim:Absorb_Health ,
https://en.uesp.net/wiki/Skyrim:Alchemy_Effects , https://en.uesp.net/wiki/Skyrim:Magical_Effects
(channel IDs cross-checked against Skyrim.esm: UESP's `alchid` = MGEF with `Alch` prefix,
`enchid` = MGEF with `Ench` prefix, `id` = spell-channel MGEF)*

**Key keywords (KYWD records)**

No UESP page lists KYWD FormIDs — IDs below verified against Skyrim.esm (EDID → FormID). Keywords are
referenced, not overridden, by most mods: they appear in KWDA lists on items, MGEFs, perks and
conditions.

| Keyword (EDID) | FormID | Who injects / retargets it |
|---|---|---|
| CraftingSmithingForge | 00088105 | crafting overhauls; forge-menu condition on COBJ recipes |
| CraftingSmithingSharpeningWheel | 00088108 | tempering distribution mods |
| CraftingSmithingArmorTable | 000adb78 | tempering distribution mods |
| CraftingSmithingSkyforge | 000f46ce | Skyrim content mods (Skyforge exclusives) |
| CraftingSmelter / CraftingTanningRack / CraftingCookpot | 000a5cce / 0007866a / 000a5cb3 | station-relocation and Hearthfire-style mods |
| WICraftingSmithing / WICraftingSmithingTempering / WICraftingAlchemy / WICraftingEnchanting | 0004f680 / 0004f6fd / 0004f6e6 / 0004f6dd | crafting-menu/requisition mods |
| WeapTypeSword / WarAxe / Dagger / Mace / Bow / Staff | 0001e711 / 0001e712 / 0001e713 / 0001e714 / 0001e715 / 0001e716 | weapon mods (adding a weapon to a skill perk's condition), perk overhauls changing weapon-type conditions |
| WeapTypeGreatsword / Battleaxe / Warhammer / BoundArrow | 0006d931 / 0006d932 / 0006d930 / 0010d501 | as above |
| WeapMaterialWood / Iron / Steel / Dwarven / Elven / Glass / Orcish / Ebony / Daedric | 0001e717 / 0001e718 / 0001e719 / 0001e71a / 0001e71b / 0001e71c / 0001e71d / 0001e71e / 0001e71f | material-rank mods, tempering/leveled-list mods, perk conditions |
| WeapMaterialImperial / Draugr / DraugrHoned / Falmer / FalmerHoned / Silver | 000c5c00 / 000c5c01 / 000c5c02 / 000c5c03 / 000c5c04 / 0010aa1a | as above |
| ArmorHeavy / ArmorLight | 0006bbd2 / 0006bbd3 | **the** armor-type keywords (vanilla has no `ArmorTypeHeavy`); armor mods and perks (Matching Set, Conditioning) match on these |
| ArmorCuirass / Boots / Helmet / Gauntlets / Shield / Clothing / Jewelry | 0006c0ec / 0006c0ed / 0006c0ee / 0006c0ef / 000965b2 / 0006bbe8 / 0006bbe9 | slot-assignment mods, enchant-slot restrictions |
| ArmorMaterial* block **0006bbd4–0006bbe7** (contiguous: Daedric 0006bbd4, Dragonplate 0006bbd5, Dragonscale 0006bbd6, Dwarven 0006bbd7, Ebony 0006bbd8, Elven 0006bbd9, ElvenGilded 0006bbda, Leather 0006bbdb, Glass 0006bbdc, Hide 0006bbdd, Scaled 0006bbde, Studded 0006bbdf, ImperialLight 0006bbe0, ImperialStudded 0006bbe1, ImperialHeavy 0006bbe2, Iron 0006bbe3, IronBanded 0006bbe4, Orcish 0006bbe5, Steel 0006bbe6, SteelPlate 0006bbe7) | 0006bbd4–0006bbe7 | armorsmith mods, per-material perk conditions, craft-recipe gating |
| VendorItemWeapon / VendorItemArmor / VendorItemJewelry / VendorItemArrow | 0008f958 / 0008f959 / 0008f95a / 000917e7 | merchant/loot-list mods |
| MagicDisallowEnchanting | 000c27bd | mods that block/allow disenchanting or enchanting unique items |
| MagicAlchRestoreHealth / MagicAlchRestoreMagicka / MagicAlchHarmful / MagicAlchBeneficial | 00042503 / 00042508 / 00042509 / 000f8a4e | referenced by the Physician/Benefactor/Poisoner perk conditions; alchemy overhauls edit them (UESP's published script lists 00042503 twice for restore-health and restore-stamina — treat the stamina entry as suspect, verify in xEdit) |
| MagicEnchFortifySmithing / MagicAlchFortifySmithing (and siblings) | 00074f6e / 00065a22 | effect-tagging keywords used by perk and enchant conditions |

**Load-order consequence:** a keyword lives inside its owner record's KWDA array. Two mods adding
*different* keywords to the same vanilla item/MGEF still conflict — the later override's entire KWDA
wins, so the earlier mod's keyword silently vanishes. Conflicts are common between (a) crafting
overhauls and (b) armor/weapon packs both tagging the same vanilla items, and between merchant mods
using `VendorItem*`. Resolution = a patch that unions both KWDA lists. Keyword records themselves
(the KYWD) are rarely overridden; it is their *presence on other records* that is contested. A mod
that adds a keyword must load before any patch that forwards record overrides containing it,
otherwise the reference dangles.

*Source: no UESP page publishes KYWD FormIDs; IDs verified from Skyrim.esm (EDID → FormID).
Keyword semantics: https://en.uesp.net/wiki/Skyrim:Smithing and MGEF KWDA usage documented in
https://en.uesp.net/wiki/Skyrim:Alchemy_Effects*

### Vanilla Bug Catalogue (mod-sorting relevant)

Mod-category key: **[USSEP]** = Unofficial Skyrim Special Edition Patch (record-overriding ESP;
loads right after masters, conventionally first among mods; any later plugin overriding the same
records clobbers its fix unless it forwards it). **[Official]** = fixed in a Bethesda game update
(already present on any current SE/AE install — a mod claiming to fix one is redundant or targets
LE 1.x). **[Fix-mod]** = dedicated community fix mod (ESP and/or SKSE plugin). **[Overhaul]** =
gameplay overhaul that may also incidentally fix or break the bug.

Load-order doctrine (applies throughout): a USSEP-style fix must load before record-touching gameplay
mods only if those mods forward its fixes; in practice USSEP goes first and overhauls/patches that
touch the same records load after it and must be checked with xEdit for the specific records. Two
mods fixing the same bug via the same record = conflict; keep one (usually load the more specific
fix after the general patch).

#### Crafting / Smithing

| Bug | Vanilla symptom | Fixed by | Load-order note |
|---|---|---|---|
| Notched Pickaxe enchantment inert | Text claims +5 Smithing; effect never applies (a flat skill bonus vanilla never grants) | [USSEP] (USKP 2.0.0) | USSEP override on the weapon record; any mod touching Notched Pickaxe loads after USSEP or forwards the fix |
| Ancient Knowledge perk wrong | +25% armor applies to ALL armor (not just Dwarven); +15% applied as tempering boost instead of smithing XP | Altered (not fixed) by [Official] 1.9.26; truly fixed by [USSEP] 1.3.1 | USSEP's version changes Dwarven temper behavior vs official patch — armor-balance mods assuming one behavior conflict; check which the user has |
| Steel Smithing ignores Imperial weapons | Imperial weapons (steel-made) not improvable/forgeable under the perk, though Imperial armor is | [USSEP] 2.0.7 | Record override (keyword on Imperial weapons); conflicts with mods reworking Imperial gear unless patched |
| Ebony 1H war axe/mace value error | Bad base values: unpatched game has Daedric = Ebony (lighter) for 1H axes/maces | [USSEP] corrects the order; [Official] SE 1.6.1130 separately fixed bad values on Glass Mace/Warhammer, Honed Draugr War Axe, Ebony Mace/War Axe, most daggers | Any weapon/leveling mod reworking these records must load after Update.esm and USSEP, forwarding intended values |
| Ebony dagger speed too slow | Wrong (slow) speed stat | [Official] 1.5.24 | Redundant on SE — ignore fix mods claiming this |
| Arcane Blacksmith lost after Legendary reset | Making Smithing Legendary, re-leveling and re-taking Arcane Blacksmith can permanently break tempering of enchanted items | Unfixed (no fix cited on UESP) | Gameplay note; temper-fix mods are [Fix-mod]/[Overhaul], verify individually |
| Mannequin duplication | Taking armor back from a mannequin without leaving the cell duplicates it; mannequins also "walk" | Duplication: [USSEP] 1.2.4; walking: [Official] SE 1.6.629 (MannequinRace immobile) | Mannequin-placement mods (player homes) edit mannequin refs — load after USSEP; residual dupe in CC Saints & Seducers gear |
| Double head-slot gear | Falmer Helmet or Ancient Shrouded Cowl lets you equip a second headpiece; Gauldur Amulet a second necklace | Circlet/Cowl: [USSEP] 1.2; Gauldur: [Official] 1.8 | Record fixes; USSEP's Falmer Helm fix is widely forwarded/undone by exploit-retaining mods — an intentional conflict, user choice |
| Matching Set / Custom Fit fail on certain pieces | Set perks not applied with some armor pieces | [Official] 1.9.26 | Armor mods must set correct armor keywords; keyword errors reintroduce the bug inside the mod's own gear (author responsibility, not load order) |
| Weightless-stack armor exploit | Equipping one piece from a stack makes the whole stack weightless under Conditioning-like perks | Unfixed | Engine behavior; relevant to carry-weight overhauls |

*Source: https://en.uesp.net/wiki/Skyrim:Smithing , https://en.uesp.net/wiki/Skyrim:Glitches ,
https://en.uesp.net/wiki/Skyrim:Patch , https://en.uesp.net/wiki/Skyrim:Special_Edition_Patch*

#### Enchanting / Alchemy

| Bug | Vanilla symptom | Fixed by | Load-order note |
|---|---|---|---|
| Fortify Restoration loop | Fortify Restoration potions buff Fortify Alchemy gear retroactively → exponential crafting; game-breaking enchantments | [USSEP] 1.3.0 (also recomputes max Fortify Enchanting: 44% base, 114% with Rare Curios; disables Necromage/Fortify Restoration boost to enchanting) | Fundamental balance divergence: overhauls built on USSEP assume the loop is dead; two ecosystems, not load-order-solvable |
| Necromage-vampire self-boost | Player flagged undead: all self-targeted spells/perks/potions/enchantments +25% magnitude / +50% duration | [USSEP] 1.2.3 | Vampire overhauls (Sacrosanct et al.) deliberately keep/reshape this — they load after USSEP and re-implement it; a "Necromage fix" mod conflicts with them |
| Recovery perk magnitude frozen wrong | Both Recovery ranks permanently scaled by any Fortify Restoration effect active when the perk is taken | [USSEP] 2.1.3 (recent — older USSEP lacks it) | Version-sensitive: late USSEP fixes don't apply retroactively to taken perks without respec |
| Corpus Enchanter miscalculation | Fortify Health/Magicka/Stamina magnitudes miscalculated | [Official] 1.1 | Redundant on SE |
| Fortify Sneak / Lockpick miscalculate | Bonus to current skill miscalculated | [Official] 1.1 | Redundant on SE |
| Enchanted-item value drops as skill rises | Player-enchanted items' gold value decreases with higher Enchanting (charge-consumption compensation) | Unfixed | Affects economy mods' assumptions; no standard fix |
| Bond of Matrimony / Linwe's set disenchantable into dead effects | Effects learned but cannot be applied | [USSEP] 1.0 | Record change; enchanting-progression overhauls may load after and restore/rework |
| Necromancer Robes wrong disenchantment | Yields Fortify Magicka, not Fortify Magicka Regen | Unfixed per UESP | — |
| Stuck at Arcane Enchanter | Exit soft-locks player | [Official] 1.4.26 | Redundant on SE |
| Renamed same-effect weapons collapse to one name | Equipping/unequipping renamed variants renames them all | [Official] 1.2 | Redundant on SE |
| Myrwatch Staff Enchanter 20x XP | Enchanting XP multiplier missing (hidden 95% reduction perk not applied) | [Official] SE 1.6.318 | Redundant on current SE |

*Source: https://en.uesp.net/wiki/Skyrim:Glitches , https://en.uesp.net/wiki/Skyrim:Enchanting ,
https://en.uesp.net/wiki/Skyrim:Restoration , https://en.uesp.net/wiki/Skyrim:Special_Edition_Patch*

#### Magic / Spells / Perks

| Bug | Vanilla symptom | Fixed by | Load-order note |
|---|---|---|---|
| Grand Healing heals enemies' stamina | Healing spells restore stamina to targets that shouldn't get it (undead/daedra/automata) | [USSEP] 2.0.5 | Spell-record overrides; magic overhauls must forward |
| Respite stamina not applied (Grand Healing, Necromantic Healing, Heal Undead) | Wrong actor/fatigue checks | [USSEP] 4.1.5 | Very recent USSEP version — old installs lack it |
| Circle of Protection hazard visual desyncs | Visible effect disperses before spell expires | [USSEP] 2.0.8 | Minor; same doctrine |
| Circle of Protection + Atronach = free magicka | Standing in own CoP with Atronach perk/stone recharges magicka | Unfixed | Relevant to magic-balance mods |
| Blessing of Talos shows 0% | Active-effects display shows 0% instead of 20% | [Official] SE 1.6.1130 | Redundant on current SE |
| Marked For Death stacks with itself | Shout stacks improperly (permanent drain) | [Official] SE 1.6.629 (undocumented) | Redundant on current SE; older fix mods obsolete |
| Twin Souls ignores vampirism | Dead Thrall second-reanimate slot miscounts for vampires | [Official] SE 1.6.629 (undocumented) | Redundant |
| Magic resistance miscalculation | Resistances not calculated after patch 1.2 | [Official] 1.3.7 (LE-era) | Historical — only unpatched LE 1.2; SE never affected |
| Falling damage miscalculated on high difficulty | Excessive/incorrect fall damage at high difficulties | [Official] 1.9.26 | Redundant on SE |
| Ebony Blade perk miscategorization | Improved by wrong-handed perks | [Official] 1.9.26 | Redundant; relevant when evaluating weapon-record mods on LE |
| Deflect Arrows perk miscalculation | Perk doesn't calculate properly | [Official] 1.5.24 | Redundant |
| Dragon Aspect blocks enchanting | "Insufficient charge for enchantment" while shout active | Unfixed | — |

*Source: https://en.uesp.net/wiki/Skyrim:Restoration , https://en.uesp.net/wiki/Skyrim:Patch ,
https://en.uesp.net/wiki/Skyrim:Special_Edition_Patch , https://en.uesp.net/wiki/Skyrim:Enchanting*

#### Combat / Equipment / UI

| Bug | Vanilla symptom | Fixed by | Load-order note |
|---|---|---|---|
| Bow damage calculated incorrectly | Wrong bow damage values | [Official] 1.6.87 | Redundant on SE |
| Limbsplitter rank 3 / Headsman's Axe / Barbarian perk gaps | Battle axes (rank 3) and Headsman's Axe don't get expected perk bonuses | [Official] 1.5.24 / 1.4.15 | Redundant on SE |
| Hotkey unbind for non-dual-wieldable weapons | Daggers/swords/greatswords un-hotkey when >1 in inventory | Unfixed | Engine/UI; hotkey-manager mods are [Fix-mod] SKSE plugins, no load-order position |
| 3–10s weapon-switch delay in combat | Especially bow→spell or bow→dual wield | Unfixed | Engine; commonly mitigated by animation/behavior overhauls ([Overhaul], behavior-level, order vs ESP fixes irrelevant) |
| Arrows/ice spikes stuck in bodies | Projectile meshes never removed | Arrow-in-world cleanup: [Official] 1.5.24; stuck-on-body variant unfixed | — |
| Projectiles stuck in world never cleaned | Floating arrows accumulate | [Official] 1.5.24 | Redundant |
| Kill-cam vs projectile mismatch | Cinematic kill plays but projectile misses/misfires | Partially addressed across [Official] 1.5.24/1.6.87 killcam fixes | Killcam-adjust mods are [Fix-mod] SKSE (INFERRED, not UESP-sourced) |
| Essential NPCs stuck in kneel | Wounded-state essential NPCs never recover | Unfixed by patches | Stagger/behavior mods incidentally help; no load-order implication |
| Craft-station activation dead click | Station click does nothing, third-person blip | Unfixed | — |
| Container menu scroll jump | Inventory selection jumps a line while moving items | Unfixed; no vanilla-UI-preserving fix | Relevant to UI mods (SkyUI replaces the container menu wholesale — different mechanism) |

*Source: https://en.uesp.net/wiki/Skyrim:Glitches , https://en.uesp.net/wiki/Skyrim:Patch*

#### Quests

| Bug | Vanilla symptom | Fixed by | Load-order note |
|---|---|---|---|
| Blood on the Ice trigger failures | Quest fails to start; Viola/Calixto deaths block progression | Progressive fixes in [Official] 1.4.15 and 1.5.24; residual issues persist in community reports | Dedicated quest-fix mods ([Fix-mod]) override the same quest record USSEP also fixes — load the dedicated fix after USSEP or pick one, never assume both apply. INFERRED on residual specifics |
| Quest scripts not shutting down | Orphaned running quest scripts accumulate in save | [Official] 1.9.26 | Save-bloat class — see below; script-cleaner mods are [Fix-mod] tools run on saves, not plugins |
| Oghma Infinium bookshelf/chest reuse | Book reusable via bookshelf placement or container exploit | Container: [Official] 1.3.7; bookshelf: [Official] 1.9.26 | Redundant on SE |
| Generic items permanently flagged quest items | Item stacks become unusable/undroppable/unsellable | Console workaround vanilla; fixed piecemeal by [USSEP] | USSEP fixes instances, not the class — recurrence after USSEP suggests a mod reintroduced a quest-item flag |
| Quest-start precondition breaks (class) | Clearing a location/holding an item before a radiant quest starts breaks it (White Phial, Driftshade, Knifepoint Ridge, Forbidden Legend, etc.) | Mass-fixed by [Official] 1.4.15; SE includes all | Quest-alteration mods (The Choice Is Yours, timing/delay mods) can re-introduce precondition conflicts; they load after USSEP and are prime suspects when a radiant quest won't start |
| Companion/follower state bugs (class) | Followers over-encumbered after rehiring; Companions not equipping better gear; protected companions killable by poison | [Official] 1.9.26 / 1.4.15 | Follower overhauls ([Overhaul]) re-own these records entirely |

*Source: https://en.uesp.net/wiki/Skyrim:Patch , https://en.uesp.net/wiki/Skyrim:Glitches*

#### Engine / Save / Performance / World-state

| Bug | Vanilla symptom | Fixed by | Load-order note |
|---|---|---|---|
| Save bloat from non-shutting-down scripts | Saves grow, hitches, eventual corruption risk | [Official] 1.9.26 (quest scripts); [USSEP] (record/scripted-asset corrections incl. removing buggy scripts) | Overhauls adding scripts shift bloat risk; not order-fixable — diagnosis category |
| Ash piles never clean up | Resurrected-actor ash piles accumulate in cells | [Official] 1.9.26 | Redundant |
| Cured diseases reappear on reload | Diseases "cured" return after load | [Official] 1.9.29 | Redundant |
| Sabre cat / fox / snow fox spawns break spawn points | Spawning at a respawn point permanently breaks it | [USSEP] 2.0.0 | Leveled-list/encounter-zone mods must forward or conflict |
| Harvestable plants revert on reload | Plants reset after 10 days but revert to prior harvested state on cell save/reload | Unfixed | Green Thumb / plant-harvest mods interact; no standard fix |
| Quicksave CTD after long sessions without a proper save | Quicksave crashes; fixed by making a manual save first | Unfixed (engine) | Relevant to auto-save-tuning mods; SE memory fixes are SKSE-plugin territory (SSE Engine Fixes, Scrambled Bugs, Bug Fixes SSE — [Fix-mod] SKSE DLLs, **no load-order position**, version-pinned; UNSOURCED/community) |
| Load-order-of-operations on cell load | Load completes before ground/key elements exist; scenery pops; interactables dead | Unfixed | Crash/hitch diagnosis class, not order-fixable |
| Werewolf-revert / water-shader / dragon-landing / vampiric-grip crashes | Various CTDs | [Official] 1.6.87, 1.7.7, SE 1.2 | All redundant on current SE (1.6.x) |
| Memory/stability baseline | LE 2GB limit; 4GB LAA in [Official] 1.3.10; SE is 64-bit with its own 1.6.x memory fixes | [Official] | Keep SE fully updated; on LE, LAA + SSME/SKSE memory patch were mandatory (UNSOURCED community memory, LAA mechanism page-cited) |
| Mannequin/duplicate-reference instability; duplicate NPCs | Duplicate NPCs after load; crash on dark-brotherhood-sanctuary entry | [Official] 1.8.145, 1.9.26 | Redundant |
| ESL record range (infrastructure, not a bug) | 1.6.1130 doubled ESL range to 4096 records | [Official] | Matters when sorting: an ESL-flagged mod's compatibility with older game versions depends on this; ESPL limits are load-order-capacity-relevant |

*Source: https://en.uesp.net/wiki/Skyrim:Glitches , https://en.uesp.net/wiki/Skyrim:Patch ,
https://en.uesp.net/wiki/Skyrim:Special_Edition_Patch ,
https://en.uesp.net/wiki/Skyrim_Mod:Unofficial_Skyrim_Patch*

**Sorting heuristics distilled**

- **USSEP is the anchor**: loads immediately after masters; every record-override mod after it
  implicitly decides per-record whether USSEP's fix survives (forwarded) or is replaced (overwritten).
  Two mods "fixing" the same record = per-record winner by order, not coexistence.
- **A mod that fixes a bug USSEP also fixes** is a conflict candidate only for the shared records —
  check with xEdit `conflicts` rather than forbidding the mod wholesale.
- **Overhauls are not fix mods** even when they incidentally fix one bug: they own whole record sets;
  they load after USSEP and their fixes are side effects.
- **SKSE-plugin fix mods (SSE Engine Fixes, Scrambled Bugs, Bug Fixes SSE)** never enter the ESP load
  order; compatibility is game-version pinning, not position. (Community knowledge, not UESP-sourced.)
- **[Official]-patch fixes are not a mod category at all on SE/AE**: any mod advertising them either
  targets LE 1.x or pads its description — flag as redundant.
- Version-sensitivity trap: USSEP fixes tagged with recent versions (2.1.3 Recovery, 4.1.5 Respite) are
  absent from old USSEP copies; diagnosing "USSEP installed but bug present" requires checking the
  USSEP version, not just its presence.
