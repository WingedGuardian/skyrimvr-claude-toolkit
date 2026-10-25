# The pose-emulating SteamVR null driver: fetched on your machine, never shipped

`bash tools/vr-headless.sh emu on` swaps SteamVR's null driver for the official *SkyrimVR Modders
Devkit* null driver, which adds two tracked controllers whose poses can be moved, so the first-person
hands move with no headset. This folder ships **no binary and no source** of that driver: only
`fetch.py`, which downloads one pinned upstream package and verifies its hashes on your machine.

## Source and pin (checked 2026-10-25)

| | |
|---|---|
| Upstream | [DubiousDuo/VR-Emulator-Driver---SkyrimVR-Devkit](https://github.com/DubiousDuo/VR-Emulator-Driver---SkyrimVR-Devkit) |
| Release | `Release_v2.0.1` = commit `32ec30294a871683254b944b4129cbdec2591e6a` (2026-10-14); driver build `2.0.1` |
| Package | `null driver.zip` at that commit, SHA-256 `391d4dfc30f1a9884e849ea2cc2299af6893e5bb54a76734f5d96f1408d1c434` |
| Driver DLL | `null/bin/win64/driver_null.dll`, SHA-256 `fc689623914c6643c6e8629dc6bcb130193888b6835d902ed6411ab476121454` (upstream's own `.sha256` sidecar says the same) |
| Licence | BSD-3-Clause, the repository's `LICENSE.txt` at that commit (Valve's OpenVR sample-driver notice; recorded in `UPSTREAM-LICENSE.txt`). The package carries the same notice as `VALVE-OPENVR-LICENSE.txt`. The Devkit's AutoHotKey frontend is GPL-3.0 and is not used here. |
| Forked from | [ar-zadeh/VR-Emulator-Driver](https://github.com/ar-zadeh/VR-Emulator-Driver), itself based on Valve's OpenVR sample driver |

## Delivery decision: a pinned download of the official binary

Earlier toolkit releases built the driver locally from commit `59be19d` (2026-10-05), because that
commit carried no licence and nothing could be redistributed. Version 4.0 moves to the official
v2.0.1 build, for three reasons:

1. **It is an official released binary.** Upstream publishes the driver as the prebuilt
   `null driver.zip` committed at the `Release_v2.0.1` tag; the Devkit's own frontend downloads
   exactly that file. The GitHub release itself carries only the frontend zip.
2. **The licence allows it, and hash pinning makes it exact.** The repository is BSD-3-Clause as a
   whole (upstream README, "License"), which permits binary redistribution with the notice; the zip
   carries the notice. `fetch.py` pins the commit, the zip hash and the DLL hash, so every user gets
   byte-for-byte the build that was qualified, from upstream's own address. The toolkit still
   ships nothing: the download happens on your machine.
3. **It is the build that was measured.** The v2.0.1 DLL with this hash is the one the
   maintainer's headless sessions ran (2026-10-15 to 10-18); a local MSVC build is not
   byte-reproducible and was never the tested artefact. The toolkit's own swap of it is qualified
   in the 4.0 manual test batch.

Open question: the parent fork (ar-zadeh) still has no licence, and the files credit r57zone,
some of whose OpenVR drivers are GPL-3.0. Upstream asserts BSD-3 for the repository. The toolkit
does not redistribute the binary, so this is upstream's provenance question, not a shipping one.

## What `emu on` changes

With Steam and SteamVR closed (they hold the driver files), after `on`:

- `<SteamVR>/drivers/null/bin/win64/driver_null.dll`, `driver.vrdrivermanifest` and
  `resources/settings/default.vrsettings` are replaced by the fetched files;
- Valve's `bin/win64/driver_null.dll.sig`, which signs the stock DLL only, is moved into the
  session's backup folder (never deleted);
- an existing `%LOCALAPPDATA%/SkyrimVR Devkit/Actions/controls.txt` gets `mouse_enabled 0`. The
  driver only reads the mouse while upstream's frontend sends its heartbeat (`devkit_input.h`), and
  the toolkit never starts that frontend; this is the second lock. A missing file is left missing.

Every file is backed up and hash-verified first; `emu off` and `off` restore all of them
byte-exact. The package's other files (`devkit_version.txt`, the `.sha256` sidecar, licence
copies) are for upstream's frontend and are not installed.

## Fetch

```
python tools/vr-emu-driver/fetch.py
```

Output: `build/null/` and `build/BUILD-INFO.json`, ignored by git and never in a release. A
different package already in `build/null/` is refused; move it aside and fetch again.
