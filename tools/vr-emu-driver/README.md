# The pose-emulating SteamVR null driver: built here, never shipped

`bash tools/vr-headless.sh emu on` swaps SteamVR's `driver_null.dll` for a build of the
*SkyrimVR Modders Devkit* null driver, which adds two tracked controllers whose poses can be moved,
so the first-person hands move with no headset. This folder ships **no binary and no source** of
that driver: only `build.py`, which fetches one pinned upstream commit and compiles it on your
machine.

## Where it comes from, and why it is not shipped (checked 2026-10-13; licence update 2026-10-15)

| | |
|---|---|
| Source | [DubiousDuo/VR-Emulator-Driver---SkyrimVR-Devkit](https://github.com/DubiousDuo/VR-Emulator-Driver---SkyrimVR-Devkit), commit `59be19d13287e2088c515b696a964381784a3356` ("null driver testing", 2026-10-05) |
| Forked from | [ar-zadeh/VR-Emulator-Driver](https://github.com/ar-zadeh/VR-Emulator-Driver) |
| Based on | Valve's OpenVR sample driver: the files carry `Copyright (c) Valve Corporation, All rights reserved` and `Changed by r57zone (https://github.com/r57zone)` |
| Licence | **Settled upstream only in part.** The repo owner added `LICENSE.txt` on 2026-10-14 (commit `bbede1e`) and says the repository as a whole is **BSD-3-Clause** (Valve's sample driver) and their AutoHotkey script GPL-3.0 (it ships in their release zip; this toolkit does not use it). The pinned commit above **predates that file**, and the fork it came from, [ar-zadeh/VR-Emulator-Driver](https://github.com/ar-zadeh/VR-Emulator-Driver), still has **no licence**. |
| Headers | Valve's [OpenVR](https://github.com/ValveSoftware/openvr) `v1.26.7` (commit `751538d6cbde9d060e5906c1d45c8cedeaf0ee18`), BSD-3-Clause; fetched at build time as well |

The pinned commit carries no licence file and its parent fork carries none either, so the toolkit still ships
neither source nor a binary. `build.py` fetches it from its own repository at a pinned commit, for your own
use on your own machine. The licence was requested upstream and answered (closed 2026-10-14):
[DubiousDuo/VR-Emulator-Driver---SkyrimVR-Devkit#1](https://github.com/DubiousDuo/VR-Emulator-Driver---SkyrimVR-Devkit/issues/1).
Upstream has since published v2.0.1 (`32ec302`). The pin stays on the tested commit; moving it is a deliberate,
tested bump, and shipping a prebuilt copy would be a separate decision (it would also have to settle the fork's
provenance below).
The r57zone header suggests the code passed through one of r57zone's OpenVR driver projects, some
of which are GPL-3.0; nothing in either fork says which, so this is unresolved provenance, not a
grant.

## The one local change

The pinned `CSampleControllerDriver::GetSerialNumber()` has no `return` after its `switch`, which
is undefined behaviour in C++ for any index other than 1 or 2. `build.py` adds
`return "CTRLUnknownSerial";`. It is the only change, recorded in `build/BUILD-INFO.json`.

## Build

Needs git and Visual Studio 2022 Build Tools with *Desktop development with C++* (installing
those is your call; nothing here installs them).

```
python tools/vr-emu-driver/build.py
```

Each checkout is verified against its pinned commit before anything compiles. The result,
`build/driver_null.dll`, and `build/BUILD-INFO.json` (pins, patch, SHA-256) are ignored by git.
MSVC output is not byte-reproducible, so the hash identifies your build, not a reference.
