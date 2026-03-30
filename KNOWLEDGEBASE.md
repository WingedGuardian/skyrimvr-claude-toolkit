# Skyrim VR Modding Knowledgebase

A living document of quirks, gotchas, and hard-won lessons about Skyrim VR modding. Everything here was either discovered through debugging or verified via web research. Always consult this before making changes.

---

## Papyrus Scripting

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
- **Weapon.SetReach(float)** — the value IS respected by the game independently of mesh (confirmed with whip mod). But **only takes effect on game restart**, not during gameplay.
- **Weapon.GetReach() / SetReach()** — SKSE native functions, no documented upper cap

### What DOES NOT WORK at Runtime
- **Weapon.SetModelPath(string)** — changes the internal path but does **NOT update the visual** of an already-equipped weapon. Even unequip→SetModelPath→re-equip cycle just shows disappear/reappear with the same model.
- **SetReach at runtime** — value changes but hit detection range doesn't update until game restart
- **Effect Shader via GetFormFromFile** — `Game.GetFormFromFile(0x00010DDD, "Skyrim.esm") as EffectShader` returned None. FormID may be wrong or form type mismatch.

### Not Yet Tested
- `Weapon.SetEquippedModel(Static model)` — different SKSE function from SetModelPath, takes a Static form. May work where SetModelPath failed.
- `VisualEffect.Play()` on player/weapon — overlay VFX
- Art Object attachment via PO3_SKSEFunctions

### Reach Formula (Vanilla, without Precision)
- `fCombatDistance(141) × NPCScale × WeaponReach`
- SetReach(10.0) ≈ 1410 units ≈ 21m
- SetReach(30.0) ≈ 4230 units ≈ 63m
- SetReach(50.0) ≈ 7050 units ≈ 106m
- **Precision does not exist for VR** — weapon collision is handled by HIGGS/VRIK

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

## Hook Candidates

A living list of potential safety hooks identified during work. Evaluated but not necessarily implemented.

| Candidate | Trigger | What it would do | Priority | Status |
|-----------|---------|------------------|----------|--------|
| *None yet* | — | — | — | — |

**When to add entries:** After any near-miss, unexpected outcome, or pattern of risk that current hooks don't cover. Include why the gap was noticed and whether the overhead is justified.

---

*Last updated: 2026-03-28*
*Add new entries as they're discovered. Prefer verified facts over speculation.*
