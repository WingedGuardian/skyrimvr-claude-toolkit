---
name: port-to-vr
description: Guided checklist for porting an SSE-only mod to Skyrim VR
disable-model-invocation: true
argument-hint: "[mod name or path]"
---

# Port SSE Mod to VR

Systematically check the mod specified by `$ARGUMENTS` against every known VR incompatibility. Consult `KNOWLEDGEBASE.md` for full details on each item.

## Step 1: SKSE Dependency Check

- Does the mod require SKSE64? → Needs SKSEVR equivalent
- Check for address library dependencies → VR address library is entirely different
- Look for DLL plugins in `Data/SKSE/Plugins/` → must be compiled against SKSEVR
- Check if CommonLibSSE-NG versions exist (supports SE + AE + VR in one build)

## Step 2: Papyrus Script Audit

Decompile all `.pex` files and check for:
- [ ] `PlayIdle()` on player → fails in VR (VRIK overrides skeleton IK). Replace with timed Papyrus scripts.
- [ ] `DisablePlayerControls()` alone → doesn't stop VR thumbstick movement. Add `SetDontMove(true)`.
- [ ] `SetVehicle()` → causes HMD desync in VR. Must be removed entirely.
- [ ] `ForceThirdPerson()` / `ForceFirstPerson()` → can't control VR camera.
- [ ] `Input.GetMappedKey()` → returns -1 for VR controllers. Use VRIK API.
- [ ] `Game.ShakeCamera()` → mostly inert in VR, leave in but document.
- [ ] `SetAngle()` on player → doesn't control HMD orientation.

## Step 3: Skeleton Compatibility

- [ ] Check for custom skeleton nodes → **PreWEAPON and PreSHIELD nodes cause CTD in VR**
- [ ] Verify XP32 compatibility → need "XP32 First Person Skeleton CTD Bugfix" for VR
- [ ] Any `.hkx` behavior files → Havok behaviors work but test with VRIK IK overlay

## Step 4: Physics and Combat

- [ ] Havok physics tuned for 60Hz? → VR runs at 90Hz, needs `fMaxTime` INI adjustment
- [ ] Player collision assumptions → VR uses capsule vs ragdoll (not capsule vs capsule)
- [ ] Melee hit detection → Precision doesn't exist for VR; HIGGS/VRIK handle this
- [ ] Weapon reach values → formula changes without Precision

## Step 5: UI and Menus

- [ ] SkyUI dependency → needs SkyUI VR fork specifically
- [ ] Custom menus/widgets → VR UI is completely reworked for controllers
- [ ] MCM menus → must use SkyUI VR MCM
- [ ] Book content → treated as 2D HUD layer in VR

## Step 6: Animation System

- [ ] OAR/DAR animations → OAR has native VR support; DAR needs VR fix or OAR conversion
- [ ] Animation Motion Revolution → VR-compatible (CTD was fixed in update)
- [ ] SCAR → dedicated VR port exists (BFCO)
- [ ] Animation priorities → must be higher than MCO/ADXP for proper playback

## Step 7: ESP Record Check

Serialize with Spriggit and check:
- [ ] Casting type consistency on all spells (all effects must match)
- [ ] ONAM subrecords if ESM-flagged
- [ ] ESL FormID range (xx000800-xx000FFF)
- [ ] VMAD script properties → prefer `GetFormFromFile()` to minimize properties

## Step 8: Create VR Patch

Based on findings:
1. Create a VR compatibility patch ESP if record changes are needed
2. Rewrite incompatible Papyrus scripts
3. Document what was changed and why
4. Test in VR with VRIK, HIGGS, and OpenComposite active

## Output

Provide a clear report:
- **Compatible as-is**: items that work without changes
- **Needs patching**: items that need script rewrites or ESP patches
- **Incompatible**: items that fundamentally can't work in VR (and workarounds if any)
- **Recommended testing**: specific in-game scenarios to verify
