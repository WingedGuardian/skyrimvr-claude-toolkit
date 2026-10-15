# The pose-emulating SteamVR null driver: built here, never shipped

`bash tools/vr-headless.sh emu on` swaps SteamVR's `driver_null.dll` for a build of the
*SkyrimVR Modders Devkit* null driver, which adds two tracked controllers whose poses can be moved,
so the first-person hands move with no headset. This folder ships **no binary and no source** of
that driver: only `build.py`, which fetches one pinned upstream commit and compiles it on your
machine.

## Source and local-build delivery (checked 2026-10-14)

| | |
|---|---|
| Source | [DubiousDuo/VR-Emulator-Driver---SkyrimVR-Devkit](https://github.com/DubiousDuo/VR-Emulator-Driver---SkyrimVR-Devkit), commit `59be19d13287e2088c515b696a964381784a3356` ("null driver testing", 2026-10-05) |
| Forked from | [ar-zadeh/VR-Emulator-Driver](https://github.com/ar-zadeh/VR-Emulator-Driver) |
| Based on | Valve's OpenVR sample driver: the files carry `Copyright (c) Valve Corporation, All rights reserved` and `Changed by r57zone (https://github.com/r57zone)` |
| Licence | Upstream added a BSD-3-Clause `LICENSE.txt` and closed the licensing request on 2026-10-14. The owner separately identifies the AutoHotKey side as GPL-3.0. This builder does not use AutoHotKey. |
| Headers | Valve's [OpenVR](https://github.com/ValveSoftware/openvr) `v1.26.7` (commit `751538d6cbde9d060e5906c1d45c8cedeaf0ee18`), BSD-3-Clause; fetched at build time as well |

The toolkit keeps its existing local-build delivery and tested pin. The licence response is resolved:
[DubiousDuo/VR-Emulator-Driver---SkyrimVR-Devkit#1](https://github.com/DubiousDuo/VR-Emulator-Driver---SkyrimVR-Devkit/issues/1).
The newer upstream head also contains runtime changes; the licensing response is not evidence
that those changes have passed this toolkit's runtime qualification. They are not adopted here.
`UPSTREAM-LICENSE.txt` records the notice supplied in that response, separately from the pin.

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
Existing caches with dirty files or a different HEAD are refused; use a new `--work` folder.
Each build gets fresh marked staging. Existing `work/src` and `work/build.bat` are preserved.
