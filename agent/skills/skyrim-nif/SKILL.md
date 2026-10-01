---
name: skyrim-nif
description: Inspect and modify NIF mesh files - AutoMod CLI for textures/strings/shaders, PyFFI for LE NiTriShape geometry edits, PyNifly for BSTriShape (SSE) and all animation/controller authoring plus parse-validation. Use when working with meshes, textures, skeleton nodes, collision blocks, mesh animation, or fixing VR mesh issues.
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

---

# PyFFI — LE-format NiTriShape geometry edits

Works on any modern Python with `setuptools` installed alongside `pyffi`. The
`time.clock = time.perf_counter` monkey-patch is required on every interpreter (removed from the
stdlib in 3.8; unrelated to the distutils issue).

> **HARD LIMITS — repeated here deliberately, because this file may be read without the root
> CLAUDE.md in context:**
> 1. PyFFI **cannot read BSTriShape** (`Unknown block type 'BSTriShape'`) — any SSE-format
>    (`user_version_2=100`) NIF. Use PyNifly.
> 2. **NEVER author animations with PyFFI.** It can construct controller blocks and its own
>    readback will pass, but the result **CTDs the engine** — it omits header string-table
>    registrations the engine requires. Use PyNifly.
> 3. Building from a fresh `NifFormat.Data()` corrupts the header string table on write. Always
>    load an existing valid NIF and restructure it.

```python
import time; time.clock = time.perf_counter
from pyffi.formats.nif import NifFormat

with open('path/to/input.nif', 'rb') as f:
    data = NifFormat.Data(); data.read(f)

# ... modify blocks ...

with open('path/to/output.nif', 'wb') as f:
    data.write(f)
```

## Why PyFFI over binary patching / NifSkope re-saves

- Preserves exact NIF format (BSStreamVersion, block types, shader flags, texture slots).
- Handles version differences (83 vs 100) correctly — no format corruption.
- Outputs byte-identical files except for the changed fields.
- NifSkope converts NIFs to BSStreamVersion 100 (SSE format) on save, which can strip texture slots
  and change the BSLightingShaderProperty structure → crashes with Community Shaders / TruePBR in
  VR. Scripted PyFFI edits avoid this entirely.

## Key operations

- **Collision shape editing**: `block.dimensions.y = new_value` on `bhkBoxShape`
- **Transform editing**: `block.transform.m_42 = new_value` on `bhkConvexTransformShape`
- **Block iteration**: `for block in data.blocks:` + `type(block).__name__` to find block types
- **Child traversal**: `block.shape` on a `bhkConvexTransformShape` gets the child shape

# PyNifly — BSTriShape (SSE) + animation/controller authoring

Installed at `tools/pynifly/io_scene_nifly/pyn/` (BadDogSkyrim PyNifly; the prebuilt `NiflyDLL.dll`
ships with it — no Blender, no compile; plain Python 3.10/3.12 x64). It wraps ousnius/nifly, the
library behind BodySlide/Outfit Studio.

```python
import sys, os
sys.path.insert(0, "tools/pynifly/io_scene_nifly")
from pyn import pynifly      # import as the package, NOT `import pynifly`
pynifly.NifFile.Load(os.path.abspath("tools/pynifly/io_scene_nifly/pyn/NiflyDLL.dll"))
nf = pynifly.NifFile(path)               # reads SSE BSTriShape AND LE NiTriShape
[s.name for s in nf.shapes]; list(nf.nodes.keys())
```

**Use PyNifly for** the things PyFFI cannot do: any **BSTriShape (SSE)** NIF, and **all
animation/controller authoring** — it has `.New()` factories for `NiControllerManager /
NiControllerSequence / NiMultiTargetTransformController / NiTransformData /
NiTransformInterpolator / NiDefaultAVObjectPalette / BSXFlags / NiTextKeyExtraData` that register
header strings correctly (the exact thing PyFFI botches → CTD). This is what makes **self-spinning
/ telescoping / keyframe-animated effect meshes** possible (e.g. a `SpecialIdle`-named
`NiControllerSequence` auto-loops on a placed Activator with zero scripting).

**PyNifly is also the validation gate.** After authoring or editing any NIF, cross-read it with
PyNifly — an independent, battle-tested parser — before handing it to in-game testing. A clean
PyNifly read catches malformed files that PyFFI's same-tool readback misses; self-consistent is not
the same as engine-valid. Check the *specific* crashable subsystem (e.g. the controller), because a
geometry-only read can pass while the animation stack is malformed.
