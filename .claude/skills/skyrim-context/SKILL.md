---
name: skyrim-context
description: Skyrim modding context and VR-specific gotchas. Auto-loads when working with Papyrus scripts, ESP-related files, or Data/ contents.
user-invocable: false
paths: "**/*.psc,**/*.pex,Data/**,**/*.ini"
---

# Skyrim Modding Context

You are working in a modded Skyrim installation with a full modding toolkit. Consult `KNOWLEDGEBASE.md` in the project root for the complete reference. Below are the most critical gotchas that cause silent failures or crashes.

## Top 15 Gotchas (Memorize These)

1. **RemoveSpell doesn't fire OnEffectFinish** — use `DispelSpell` when cleanup logic exists (but DispelSpell excludes abilities)
2. **All effects on a spell must have the same casting type** — mismatches cause silent failure
3. **VMAD editing is fragile** — use `GetFormFromFile()` to minimize properties; xEdit can't add scripts to VMAD
4. **PlayIdle fails in VR** — VRIK overrides skeleton IK; bypass with timed Papyrus scripts
5. **Wait() unreliable under 100ms** — merge sub-100ms gaps; use `RegisterForSingleUpdate` when possible
6. **SSE != VR** for: camera, skeleton, collision, UI, input, SKSE addresses, physics (60Hz→90Hz)
7. **ESL FormIDs must be in xx000800-xx000FFF** — exceeding = crash or data corruption
8. **Loose files always override BSAs** — check for loose file conflicts before assuming BSA content wins
9. **Condition OR has precedence over AND** — `A AND B OR C` != what you'd expect
10. **Non-auto properties don't restore from master on save/load** — they stay blank
11. **PreWEAPON/PreSHIELD skeleton nodes cause CTD in VR** — must be removed
12. **ONAM required for ESM temp record overrides** — missing ONAM = game silently ignores overrides
13. **SetVehicle causes HMD desync in VR** — avoid entirely
14. **GoToState("") in OnUnload → Self=None crash** — move to OnLoad instead
15. **Navmesh creation is CK-only** — xEdit can only delete, never recreate

## Key VR Differences

- SKSEVR is a **completely separate build** from SKSE64 — address libraries differ entirely
- `DisablePlayerControls()` does NOT prevent VR thumbstick movement — use `SetDontMove(true)` in addition
- VR physics locked to 60Hz by default (needs INI tweak for 90Hz)
- Player collision uses capsule vs ragdoll in VR (vs capsule vs capsule in SSE)
- SKSE Input API returns -1 for VR controllers — use VRIK API instead
- `Game.ShakeCamera()` is mostly inert in VR

## ESP Editing Workflow

**Default (creating/editing records):** Use Spriggit serialize → edit YAML → deserialize
**Analysis and inspection:** Use xeditlib for programmatic traversal and diffing

## Before Any Change

Always check `KNOWLEDGEBASE.md` for the full context on whatever you're about to touch.
