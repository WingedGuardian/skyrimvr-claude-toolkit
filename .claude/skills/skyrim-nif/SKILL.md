---
name: skyrim-nif
description: Inspect and modify NIF mesh files using AutoMod CLI. Use when working with meshes, textures, skeleton nodes, or fixing VR mesh issues.
paths: "**/*.nif,Data/meshes/**"
---

# NIF Mesh Operations

Use the AutoMod CLI for all mesh work. Always use `--json` for output.

```bash
bash tools/automod-cli.sh nif <command> [args] --json
```

## Read-Only Commands

- `nif info <path>` — file version, size, structure
- `nif list-textures <path>` — all texture references (supports recursive folder)
- `nif list-strings <path>` — all string entries / node names
- `nif shader-info <path>` — shader property details
- `nif verify <path>` — integrity check

## Write Commands (always confirm with user first)

- `nif replace-textures <path> --old <str> --new <str> [--dry-run] [--backup]` — batch retexture
- `nif rename-strings <path> --old <str> --new <str> [--dry-run] [--backup]` — rename nodes
- `nif fix-eyes <path> [--dry-run] [--backup]` — fix FaceGen eye ghosting
- `nif scale <path> <factor> [--output <path>]` — resize mesh
- `nif restore <path>` — restore from .nif.bak backup

## VR-Critical Mesh Issues

- **PreWEAPON and PreSHIELD skeleton nodes cause CTD in VR** — use `nif list-strings` to check for these, then `nif rename-strings` to remove the "Pre" prefix or delete the nodes
- VR does a text-contains search for WEAPON/SHIELD nodes and gets confused by Pre* prefixes
- XP32 First Person Skeleton CTD Bugfix is critical for custom skeleton users in VR

## Geometry & Animation Authoring (PyFFI / PyNifly)

AutoMod handles textures/strings/shaders/scale but NOT geometry rewrites or animation. For those:

- **PyFFI (any modern Python + setuptools)** — LE-format `NiTriShape` geometry edits: vertex shifts, bounds, collision/
  shader tweaks, and mesh split/subdivision. `tools/pyffi-geometry-split.py` splits one shape into two
  so each half can carry its own shader (e.g. partial-mesh glow without UV overlap).
  **Limits**: cannot read SSE `BSTriShape`; never author animation controllers with PyFFI — the
  written file passes PyFFI's own readback but CTDs the engine.
- **PyNifly** — reads/writes SSE `BSTriShape` AND authors animation/controller blocks correctly
  (`NiControllerManager` / `NiControllerSequence` / `NiTransformData`, etc.). Use it for:
  - Self-spinning effect meshes — a `SpecialIdle`-named `NiControllerSequence` auto-loops on a placed
    Activator with **zero scripting**.
  - Telescoping / transforming geometry and any keyframed mesh motion.
  - As the **independent parse gate** for any authored/edited NIF (see below).

## Validate & Render Before In-Game Testing

A crash-to-desktop must be caught in tooling, not the headset. Run these gates in order:

1. **PyNifly parse** — `tools/blender-nif-validate.py` imports the NIF via PyNifly (an independent,
   stricter parser). A malformed file errors here instead of CTD-ing the game. Check the SPECIFIC
   crashable subsystem (e.g. the animation controller) — a geometry-only read passes even when the
   animation stack is malformed.
   ```bash
   blender.exe --background --python tools/blender-nif-validate.py -- <path.nif>
   ```
2. **Render to PNG** — `tools/blender-nif-render.py` produces an image so a mesh/glow/shape fix is
   verifiable in chat without a game launch. Post the PNG for the user to confirm.
   ```bash
   blender.exe --background --python tools/blender-nif-render.py -- <path.nif> <out.png>
   ```
3. **NifSkope (GUI)** — the independent visual validator for anything the headless render can't show.

> Blender shares the nifly library, so it is NOT an independent *parser* — use it for repair and
> rendering, and PyNifly/NifSkope for validation. PyFFI's own readback is NOT a valid gate (same tool
> that wrote the file).

Blender (headless) + the PyNifly Blender addon, plus NifSkope, are optional installs — see `setup.sh`.
