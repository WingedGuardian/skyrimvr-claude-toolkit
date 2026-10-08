# Layer 2 spike — MO2 VFS (C6) — PENDING BATCH (B5)

**Status: not run in Phase A.** R15 requires MO2 portable to come from its official GitHub release
**with the release's published checksum verified**. MEASURED 2026-10-04 (`gh api
repos/ModOrganizer2/modorganizer/releases`): the latest release `v2.5.2` (2024-08-04) and every
release back to `v2.5.0rc1` publish **no checksum** — no `digest` on any asset, no SHA in the
release notes. Downloading it would break R15, so it was not downloaded. Per Task 3 Step 4, C6 is
recorded as **"pending batch"**: Table A treats it as not failed, and the R3 flip also requires B5.

## B5 steps (C6), for the Final Manual Batch

Run against scratch only. Nothing here touches `C:/Games`.

1. Lock the bed, read-only state check first:
   `python scripts/layer2-spike/lockprobe.py apply M-D "C:/Temp/skyguard-ml2/game/Data/<each vanilla target>"`
   (Phase B: `tools/skyguard lock --sandbox-target C:/Temp/skyguard-ml2/game`).
2. Get MO2 2.5.2 **portable** yourself (GitHub `Mod.Organizer-2.5.2.7z` or Nexus). No published
   checksum exists, so this is your call; extract to `C:/Temp/skyguard-ml2/tools/mo2/`.
3. Start `ModOrganizer.exe`, choose **Create a portable instance**, game **Skyrim VR**, game folder
   `C:\Temp\skyguard-ml2\game` (browse to it; do NOT accept a detected Steam path).
4. In MO2: Executables → add `<your supported Python executable>`
   with arguments `<your toolkit checkout>\scripts\layer2-spike\mo2_vfs_check.py`
   (Phase B writes this collector; until then use `-c "print(open(r'C:\Temp\skyguard-ml2\game\Data\Skyrim.esm','rb').read(16))"`).
5. Run it from MO2. The collector records: a locked master read under the VFS (must work), a write of
   `Data\Out.esp` (must land in MO2's `overwrite/`), and a write of the locked `Update.esm` (record).
6. Close MO2. The collector checks every master is byte-identical and still locked.
