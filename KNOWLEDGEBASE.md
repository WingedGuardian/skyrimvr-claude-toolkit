# Skyrim Modding Knowledgebase (SE / AE / VR)

A living document of quirks, gotchas, and hard-won lessons about Skyrim modding (SE / AE / VR). Everything here was either discovered through debugging or verified via web research. VR-specific behavior is called out as such. Always consult this before making changes.

---

## Papyrus Scripting

### Log Noise (Not Bugs)

**"VM is freezing..." / "VM is frozen"** — These lines appear in **every** Papyrus log on normal game exit and save. They are not a crash, not a script error, and have no diagnostic value. Ignore them completely when troubleshooting.

### Script Lifecycle

**OnInit vs OnLoad:**
- `OnInit` fires only once when the script first initializes — never again on subsequent game reloads
- For Quest/Alias scripts: `OnInit` fires at game startup and whenever the quest starts (due to reset)
- For other objects (Topic Infos, Perks, persistent refs): `OnInit` fires at first load into the session
- All properties and variables are reset to default values before `OnInit` fires
- There is NO `OnLoad` event — use `RegisterForSingleUpdate`/`OnUpdate` or `OnGameReload` for reload detection
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
- **Exception**: `Skyrim.esm` and the game's core master files — always safe to reference by FormID since they're always loaded.

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
- **`DisablePlayerControls(True, True, False, False, False, True, True, False, 0)` + `Actor.SetDontMove(True)`** together is the correct approach. `SetDontMove` is the key — `DisablePlayerControls` alone or `SetRestrained` alone do not stop VR movement. Source: decompiled `EndMagicEndScript.pex` from `JudgementCutEnd.esp`, confirmed working in VR.
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

- See CLAUDE.md for the full list of quirks (UCS-2 strings, void Init/Close, uint16 WordBool, GetResultString pattern).
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
| ESL | Shares FE slot | xx000800-xx000FFF | 4096 | Only 2048 usable FormIDs |

**ESL Critical Rules:**
- All FormIDs must fall within `xx000800` to `xx000FFF` — exceeding causes crashes or data corruption
- xEdit's "Compact FormIDs for ESL" starts at `FE000xxx`; CK starts at `FE001xxx` (discrepancy)
- ESP→ESL conversion requires compacting FormIDs AND setting ESL flag

### Load Order & Override Resolution

- Last-loaded plugin version of a record is the "conflict winner" — overrides all lower plugins
- "Copy as override" uses the highest override visible to the target file (based on its masters)
- ModGroups hide non-winning overrides from conflict detection (prevent false positives)

### ESP Removal and Existing Saves (CRITICAL)

**NEVER remove/disable an ESP from the load order on an existing save.** Removing an ESP shifts all downstream ESPs' load order positions. Every ESP after the removed one gets a new load order index, which resets its quest stages, script state, and alias references in the save — as if freshly installed. With 700+ plugins, this is catastrophic.

**Lobotomize instead:** Keep the ESP in the load order (maintains position stability) but strip its functionality:
1. Remove compiled scripts (.pex files from `Data/Scripts/`)
2. Disable SKSE plugins (.dll → .dll.disabled)
3. Disable SPID distribution files (.ini → .ini.disabled) if applicable
4. ReSaver clean to remove orphaned script instances (with .pex gone, they won't re-create)

The ESP remains as a hollow shell. Only suggest removing ESPs when the user is starting a new game.

*Source: direct experience — the lobotomy approach is confirmed working.*

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

### Engine Fixes Available

- **Engine Fixes VR**: tree LOD visibility, BSFadeNode offset corrections, volume settings persistence
- **Poached Bugs VR**: ports Scrambled Bugs fixes to VR (spellcasting, texture, weapon issues)
- **Papyrus Tweaks NG**: script execution fixes, VR playroom script pausing — **use 2023 version, NOT v4.1.1** (see above)

---

## ESP Creation via Spriggit

### Header Version
- **ESP header version must be 1.7** for SSE/VR plugins. Version 1.0 is flagged as Oldrim/wrong game by tools like Rybash.
- In Spriggit YAML: `ModHeader: Stats: Version: 1.7`
- A `spriggit-meta.json` file is required in the root of the YAML directory for deserialization. Format:
  ```json
  {"PackageName": "Spriggit.Yaml.Skyrim", "Version": "0.40.0", "Release": "SkyrimSE", "ModKey": "YourMod.esp"}
  ```

### Spriggit CLI
- Serialize: `spriggit serialize --InputPath "Data/Mod.esp" --OutputPath "/tmp/output" --GameRelease SkyrimSE --PackageName Spriggit.Yaml --PackageVersion "0.40.0"`
- Deserialize: `spriggit deserialize --InputPath "/tmp/yaml_dir" --OutputPath "Data/Mod.esp"` (reads meta from spriggit-meta.json)
- `--GameRelease` is only for serialize, NOT deserialize
- **Use `-u` / `--ErrorOnUnknown` on serialize.** It makes Spriggit throw on unknown
  fields/records instead of dropping them silently — directly mitigates the silent-drop
  issue below. Make it the default for our workflow.

### ✅ Spriggit round-trip is byte-stable — verified 2026-05-18
- A controlled no-op round-trip (`serialize → deserialize unchanged → serialize`) of the
  a test ESP produced a **byte-for-byte identical file**; an unmodified ESP
  round-trip to identical YAML with `:Skyrim.esm` references intact. Spriggit serializes
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
- To freeze the player while preserving weapon drawn state, use: `DisablePlayerControls(true, false, false, false, false, false, true, false, 0)` — disables movement and menu, but NOT fighting. Combined with `SetDontMove(true)` for VR thumbstick freeze.


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

- Parse structured Papyrus instance data (variable values, active effect state)
- Edit and write back a valid save (requires full format understanding)
- Browse by record type (use ReSaver for that — Java GUI)

### Common Debugging Use Cases

- **Orphaned scripts**: Search for a removed mod's script names. If still present, the save has orphaned instances.
- **Effect accumulation**: Search for a spell's FormID and count matches. More than expected = stacking bug.
- **Mod footprint**: Search for a mod's EditorID prefix to see how many references it left.
- **Save bloat tracking**: Compare decompressed sizes across saves.

*Tool: `scripts/read-save.py` — requires Python + `lz4` package (`pip install lz4`)*

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

**Write safety:** every write op is **dry-run by default** and only applies with `--apply`. An applied write always goes to a **NEW file** — it never overwrites the input save. Every `--apply` is additionally **verify-gated**: the output is re-read and compared to the written model, and on any unintended divergence the file is deleted and the op fails (fail-to-write beats silent corruption). As always, **loading the result in-game is the final validation gate.**

**Performance:** a full structured parse of a ~45MB save runs in about **2.4s**, including building the cross-reference index — fast enough to use interactively during debugging.

**Name resolution:** ReSaver works in FormIDs. Resolve FormID → EditorID on demand with `tools/resaver-resolve-names.js` (xeditlib-backed) so dumps and reference reports are human-readable.

**Requirements:** a JDK plus ReSaver's `ReSaver.jar` + its `lib/` folder in `tools/resaver-cli/`. The wrapper auto-compiles its small driver on first run.

## Co-save (.skse) Structural Survey (cosave-info)

The `.ess` is only half the save state — SKSE mods stash their own data in a sibling **co-save**
(`SaveN_....skse`, same basename as the `.ess`). `bash tools/cosave-cli.sh <cosave.skse>` emits a
**read-only** JSON survey: the header fields plus a per-opcode chunk inventory (count + total bytes),
so you can see **which** plugins stashed co-save data (StorageUtil/PapyrusUtil, JContainers, per-mod
blobs) and **how much** — the mod-state landscape the `.ess` never exposes. It surveys chunk
*structure* (the `{u32 type, u32 version, u32 length}` walk, validated on real VR cosaves) and
resyncs on a desync, reporting coverage — it does **not** decode each chunk's plugin-internal format
(those are undocumented per-plugin). No install beyond Python 3; it never writes the cosave.

### Node/koffi helper exit codes are unreliable on Windows

Every xelib / koffi-based Node helper (resaver-resolve-names.js, the xelib scripts, etc.) can exit **non-zero (e.g. 127) even on full success** on Windows / Git-Bash — Node's stdio-flush-on-exit races against the resident native DLL's teardown. **Judge success by parsing the JSON the script prints, never by the process exit code.** Wrappers that gate on `$?` will report phantom failures.

---

## Hook Candidates

A living list of potential safety hooks identified during work. Evaluated but not necessarily implemented.

| Candidate | Trigger | What it would do | Priority | Status |
|-----------|---------|------------------|----------|--------|
| *None yet* | — | — | — | — |

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
EquipmentType: 013F45:Skyrim.esm
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

**Common EquipmentType FormIDs:**
- `013F45:Skyrim.esm` — RightHand (one-handed weapons)
- `013F42:Skyrim.esm` — BothHands (two-handed weapons)

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

**No native `IsBlocking()` function exists** in Papyrus, SKSE, PO3, or DbSKSE. **Workaround:** `target.GetActorValue("IsBlocking")` returns 1.0 if actor is in blocking stance, 0.0 otherwise. This is an internal AV, not documented but functional.

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

Throws `Error: Actor value "IsBlocking" does not exist` on every call. This error has non-trivial VM overhead, especially in hot paths. **Use `Actor.GetAnimationVariableBool("IsBlocking")` instead** — this reads the animation graph variable and works correctly.

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

### po3 Papyrus Extender (VR) exports a subset
`po3_papyrusextender_vr.dll` omits ~24 functions present in the SSE build (e.g. `Apply3DHavokImpulse`,
`GetAllActiveEffectsOnActor`, `InstantKill`, `RemoveChildNode`). Mods calling them fail silently
("Function X could not be found"). Check the VR fork's exports before assuming a po3 function exists.

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

AutoMod `audio wav-to-xwm` needs `xWMAEncode.exe` (present at `src/xWMAEncode.exe`); it requires an
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
- **Spriggit is on PATH directly as `spriggit`** here (NOT `dotnet tool run spriggit`).
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
stdlib in 3.12 (PEP 632) — `pip install pyffi setuptools` fixes it, since setuptools ships its own
vendored `distutils` plus an import shim (the official PEP 632 migration path). Verified end-to-end
on Python 3.14. The separate `time.clock` monkey-patch (removed in 3.8, [still open upstream](https://github.com/niftools/pyffi/issues/80)) is unrelated and still required regardless of interpreter version.

**PyNifly is the fix** (installed `tools/pynifly/io_scene_nifly/pyn/`, prebuilt DLL, no Blender;
`from pyn import pynifly` after putting `io_scene_nifly` on `sys.path`). Wraps ousnius/nifly (the
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
  the deadlock persists on current versions is **untested here** — treat `game save` as suspect and
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

### Don't wreck the live session you're testing in
- **Heavy/global console commands can CTD a big load order.** A full `smp reset` reliably crashed a
  700+ plugin VR load order — reproduced by typing it manually, so this is the command's cost, not
  DevBench's fault. Keep live commands lightweight and targeted; get the user's OK and a save before
  any state change.
- **Don't poke the player's real ongoing activity to "test".** Firing state-mutating commands at live
  systems pollutes engine state (e.g. the music instance stack, producing phantom double-tracks).
  **Spawn dedicated test actors and act on those** (`player.placeatme <hostileBase> N`), and observe
  the player's natural activity rather than perturbing it.
- Many commands produce **no console output at all** (`addmusic`/`removemusic`, etc.). `count:0` from
  a capture is normal — judge the result by re-reading state, not by captured lines.

### Papyrus `call` gotchas
- Globals/native functions: **omit `self`**. Member functions: pass it —
  `{"form":"0x14"}` (or an EditorID), or `"selected"` for the console/crosshair ref (set via `prid`).
- Unlike console `cgf`, **the return value comes back** (`{called, returned, returnedType}`) — this is
  what makes read-modify-read loops possible without a play-session round trip.
- **Omitted trailing optional params are padded with neutral defaults** (`None`/`0`/`0.0`/`false`/`""`).
  For reference ops (`MoveTo`, `Disable`, `Kill`) a short arg list therefore **silently no-ops** —
  pass every optional whose real default isn't neutral (e.g. `StringUtil.Substring`'s `-1`).
- Array returns may not serialize (an array-returning function can come back as `none`) — probe with a
  scalar-returning function instead of concluding the state is absent.

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

### Never hand-install a mod into `Data/` on a managed install

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

## Crash logs: CrashLogger writes `.LOG`, not `.txt`

Recent CrashLogger versions write crash logs as **`.LOG`** into `Documents/My Games/<game>/SKSE/` and
open the newest one in Notepad. If you are looking for `crash-*.txt` and finding nothing, that is why.
Sort that folder by modified time after a CTD.

---

## AutoMod BSA extract/repack needs `bsarch.exe`

AutoMod's `archive` module reads a BSA index natively for `list`, but `extract`, `extract-file`, and
`repack` shell out to **BSArch.exe** — without it they fail with `BSArch not found`. Place `bsarch.exe`
at `<build-output>/tools/bsarch/bsarch.exe`, i.e. under the same `bin/Release/net8.0/` folder the
wrapper runs the DLL from. **It lives under `bin/`, so any `dotnet build` or `clean` of AutoMod wipes
it** — keep a canonical copy elsewhere and re-place it after a rebuild.

Similarly, `audio wav-to-xwm` needs `xWMAEncode.exe`, which is not bundled. It is often simpler to call
`xWMAEncode` directly than to go through AutoMod.

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
