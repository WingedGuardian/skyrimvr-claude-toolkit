#!/usr/bin/env python3
"""vr-headless -- boot Skyrim VR with no headset, on SteamVR's null driver, so DevBench can drive the game.

    bash tools/vr-headless.sh status
    bash tools/vr-headless.sh on [--force]     # headless: SteamVR null driver (and Valve's openvr_api.dll)
    bash tools/vr-headless.sh off              # back to normal VR, byte-exact; safe to repeat, safe after a crash
    bash tools/vr-headless.sh emu on|off       # the pose-emulating null driver, only once you have BUILT it

Skyrim VR only: SE/AE has no OpenVR path, and the tool refuses there.

What `on` changes, and nothing else:
  * <Steam>/config/steamvr.vrsettings: the null driver is enabled and forced, the HMD requirement dropped,
    every other driver disabled, and compositor standby turned off (a null HMD never reports a wearer, so SteamVR
    would park the compositor and the game would never present a frame).
  * <game>/openvr_api.dll, ONLY when it is OpenComposite (an OpenXR runtime that bypasses SteamVR): it is replaced
    by SteamVR's own openvr_api.dll. A Valve openvr_api.dll is left alone.
BEFORE changing anything, both files are copied to the backup area, hashed, the copies verified, and a state file
written. If any step fails, everything is put back from those copies before the tool exits.

`off` restores both files byte-exact from the backups and verifies each hash. A file that changed AFTER `on` (an
OpenComposite update, a settings edit) is first copied, hash-verified, into the run's backup folder
(changed-after-on-<stamp>/), so nothing is lost. The closed state file is kept as state-closed-<stamp>.json. With no
state file it has nothing to do and says so, so it is safe to run again, or after a crash or a reboot. A restore that does not verify keeps the
state file and the backups, and exits 3. Run `off` again once the reported problem is resolved.

Close SteamVR first: it rewrites steamvr.vrsettings when it exits. `on` refuses while vrserver.exe runs (--force
overrides).

Where things are found:
  * Steam: $STEAM_ROOT, else the registry (HKCU\\Software\\Valve\\Steam SteamPath, HKLM ...\\Valve\\Steam InstallPath),
    else the default install folder. SteamVR is looked up in every library listed in
    <Steam>/steamapps/libraryfolders.vdf.
  * The game: $SKYRIM_GAME_ROOT, else skyrim-paths.env, else the folder the toolkit is in (tools/skyrim_paths.py).
  * Backups and the state file: <toolkit>/.claude/backups/vr-headless/ (never shipped), or $VR_HEADLESS_DIR.

The emulator driver (tools/vr-emu-driver/) is never shipped as a binary: `python tools/vr-emu-driver/build.py`
fetches a pinned upstream commit and builds it on your machine. `emu on` swaps it in for SteamVR's
driver_null.dll (backed up and verified the same way); `emu off` restores the original.

Headless is for pose-independent tests. It is NOT valid for anything that depends on where the head and hands are
(HIGGS, VRIK, PLANCK, hand collision): the null HMD and the emulated controllers are not a person.

Exit: 0 done | 2 refused or failed (with the reason; anything changed has been put back) | 3 `off` could not verify a
restore (the backups are kept; see the message).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skyrim_paths  # noqa: E402

DEFAULT_DIR = skyrim_paths.TOOLKIT_ROOT / ".claude" / "backups" / "vr-headless"
EMU_BUILD = Path(__file__).resolve().parent / "vr-emu-driver" / "build" / "driver_null.dll"
OTHER_DRIVERS = ("vrlink", "oculus", "oculus_legacy", "lighthouse", "htc", "prism")
STEAM_DEFAULTS = ("C:/Program Files (x86)/Steam", "C:/Program Files/Steam", "~/.steam/steam", "~/.local/share/Steam")


class Refused(Exception):
    pass


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def state_dir() -> Path:
    return Path(os.environ["VR_HEADLESS_DIR"]) if os.environ.get("VR_HEADLESS_DIR") else DEFAULT_DIR


# --------------------------------------------------------------------------- discovery

def registry_steam_paths():
    """Steam's install folder as the registry records it (Windows only; [] elsewhere or on any error)."""
    if os.name != "nt":
        return []
    import winreg
    out = []
    for hive, key, value in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
        try:
            with winreg.OpenKey(hive, key) as k:
                out.append(str(winreg.QueryValueEx(k, value)[0]))
        except OSError:
            continue
    return out


def steam_root() -> Path:
    if os.environ.get("STEAM_ROOT"):
        return Path(os.environ["STEAM_ROOT"])
    for cand in registry_steam_paths() + [os.path.expanduser(d) for d in STEAM_DEFAULTS]:
        if cand and (Path(cand) / "steamapps").is_dir():
            return Path(cand)
    raise Refused("Steam not found: not in the registry or the default folders. Set STEAM_ROOT to the Steam folder.")


def library_folders(steam: Path):
    """Every Steam library: the Steam folder itself, then each "path" in steamapps/libraryfolders.vdf."""
    libs = [steam]
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    try:
        text = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return libs
    for m in re.finditer(r'"path"\s+"((?:[^"\\]|\\.)*)"', text):
        p = Path(m.group(1).replace("\\\\", "\\"))
        if p not in libs:
            libs.append(p)
    return libs


def steamvr_folder(steam: Path) -> Path:
    for lib in library_folders(steam):
        d = lib / "steamapps" / "common" / "SteamVR"
        if (d / "bin" / "win64" / "openvr_api.dll").is_file():
            return d
    raise Refused("SteamVR not found in any Steam library ({}). Install SteamVR from Steam first.".format(
        ", ".join(str(x) for x in library_folders(steam))))


def steamvr_running() -> bool:
    if os.name != "nt":
        return False
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq vrserver.exe", "/NH"], capture_output=True, text=True,
                           timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "vrserver.exe" in r.stdout.lower()


def paths():
    game, source = skyrim_paths.game_root()
    if not (game / "SkyrimVR.exe").is_file():
        if (game / "SkyrimSE.exe").is_file():
            raise Refused("this is Skyrim SE/AE ({}): headless mode is for Skyrim VR only -- SE/AE has no OpenVR path to "
                          "redirect.".format(game))
        raise Refused("no SkyrimVR.exe in {} (game folder from {}). Headless mode is for Skyrim VR only; set "
                      "SKYRIM_GAME_ROOT if this is the wrong folder.".format(game, source))
    steam = steam_root()
    vr = steamvr_folder(steam)
    return {"game": game, "steam": steam, "steamvr": vr,
            "live_dll": game / "openvr_api.dll",
            "valve_dll": vr / "bin" / "win64" / "openvr_api.dll",
            "vrsettings": steam / "config" / "steamvr.vrsettings",
            "null_driver": vr / "drivers" / "null" / "bin" / "win64" / "driver_null.dll"}


def is_opencomposite(p: Path) -> bool:
    """OpenComposite's openvr_api.dll names itself in its strings; Valve's never does."""
    try:
        data = p.read_bytes()
    except OSError:
        return False
    return b"OpenComposite" in data or "OpenComposite".encode("utf-16-le") in data


# --------------------------------------------------------------------------- file plumbing

def replace_file(src: Path, dst: Path) -> None:
    """Write dst from src through a temp file beside it and os.replace: never half-written."""
    fd, tmp = tempfile.mkstemp(dir=str(dst.parent), prefix=".vrheadless_")
    os.close(fd)
    try:
        shutil.copyfile(str(src), tmp)
        os.replace(tmp, str(dst))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)                        # the tool's own temp file


def write_text_atomic(dst: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(dst.parent), prefix=".vrheadless_")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, str(dst))


def backup(src: Path, dst: Path) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dst))
    h = sha(src)
    if sha(dst) != h:
        raise Refused("the backup of {} does not match the original; nothing was changed".format(src))
    return h


def headless_settings(text: str) -> str:
    d = json.loads(text)
    d["driver_null"] = {"enable": True, "id": "Null Driver", "windowWidth": 1920, "windowHeight": 1080,
                        "renderWidth": 1920, "renderHeight": 1080, "secondsFromVsyncToPhotons": 0.01111,
                        "displayFrequency": 90}
    sv = d.setdefault("steamvr", {})
    sv["requireHmd"] = False
    sv["forcedDriver"] = "null"
    # Exactly one driver: with every driver active, another driver's watchdog has killed vrserver mid-session,
    # taking the game with it and leaving no crash dump.
    sv["activateMultipleDrivers"] = False
    for drv in OTHER_DRIVERS:
        d.setdefault("driver_" + drv, {})["enable"] = False
    d.setdefault("power", {})["pauseCompositorOnStandby"] = False
    return json.dumps(d, indent=3) + "\n"


# --------------------------------------------------------------------------- state

def load_state():
    p = state_dir() / "state.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(state) -> None:
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    write_text_atomic(d / "state.json", json.dumps(state, indent=2) + "\n")


def clear_state() -> None:
    """Close the run: state.json is renamed to a dated state-closed-*.json beside it, never deleted (it was written
    by an EARLIER run, so the user's Recycle Bin rule covers it; review L5 F4). Only state.json is ever read."""
    p = state_dir() / "state.json"
    if p.exists():
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        os.replace(str(p), str(p.with_name("state-closed-{}.json".format(stamp))))


def restore_entries(entries) -> list:
    """Put each recorded file back from its backup and verify it. Returns the problems (empty = all verified)."""
    problems = []
    for e in entries:
        live, bak, want = Path(e["path"]), Path(e["backup"]), e["sha256"]
        try:
            current = sha(live) if live.is_file() else None
            if current == want:
                continue                           # never changed, or already restored
            if not bak.is_file() or sha(bak) != want:
                problems.append("{}: its backup {} is missing or does not match the recorded hash; restore it by "
                                "hand".format(live, bak))
                continue
            if current is not None and current != e.get("applied_sha256"):
                # Changed AFTER `on` (an OpenComposite update, a SteamVR settings change): keep those bytes before
                # the backup goes back over them (review L5 F1). A copy that does not verify stops this restore.
                kept = bak.parent / "changed-after-on-{}".format(datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
                try:
                    backup(live, kept / live.name)
                except Refused as ex:
                    problems.append("{}: it changed after `on` and {}; nothing was restored".format(live, ex))
                    continue
                print("  {} changed after `on`; that version is kept at {}".format(live, kept / live.name))
            replace_file(bak, live)
            if sha(live) != want:
                problems.append("{}: restored, but its hash does not verify".format(live))
        except OSError as ex:
            problems.append("{}: {} (is a program holding it? Close SteamVR/Steam and run off again)".format(live, ex))
    return problems


# --------------------------------------------------------------------------- commands

def cmd_on(force=False) -> int:
    p = paths()
    st = load_state()
    if st and st.get("emu_recovery_pending"):
        raise Refused("emulator recovery is incomplete; run `off` to retry the retained backup first")
    if st and st.get("mode") == "on":
        print("already headless (since {}); `off` restores normal VR".format(st.get("since")))
        return 0
    if st:
        raise Refused("a state file from an unfinished run exists ({}): run `off` first".format(state_dir() / "state.json"))
    for k in ("live_dll", "valve_dll", "vrsettings", "null_driver"):
        if not p[k].is_file():
            raise Refused("{} not found: {}".format(k.replace("_", " "), p[k]))
    if steamvr_running() and not force:
        raise Refused("SteamVR is running; it rewrites steamvr.vrsettings when it exits. Close it, then run `on` again "
                      "(--force to proceed anyway).")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir = state_dir() / stamp
    swap = is_opencomposite(p["live_dll"])
    entries = [{"path": str(p["vrsettings"]), "backup": str(bdir / "steamvr.vrsettings"),
                "sha256": backup(p["vrsettings"], bdir / "steamvr.vrsettings")},
               {"path": str(p["live_dll"]), "backup": str(bdir / "openvr_api.dll"),
                "sha256": backup(p["live_dll"], bdir / "openvr_api.dll")}]
    state = {"mode": "backed-up", "since": stamp, "game": str(p["game"]), "swapped_dll": swap, "files": entries}
    save_state(state)                              # recorded BEFORE the first change
    try:
        write_text_atomic(p["vrsettings"], headless_settings(p["vrsettings"].read_text(encoding="utf-8")))
        entries[0]["applied_sha256"] = sha(p["vrsettings"])     # what `off` may overwrite without keeping a copy
        print("  steamvr.vrsettings: null driver enabled ({})".format(p["vrsettings"]))
        if swap:
            replace_file(p["valve_dll"], p["live_dll"])
            if sha(p["live_dll"]) != sha(p["valve_dll"]):
                raise OSError("the game's openvr_api.dll does not match SteamVR's after the copy")
            entries[1]["applied_sha256"] = sha(p["live_dll"])
            print("  openvr_api.dll: OpenComposite replaced by SteamVR's ({})".format(p["valve_dll"]))
        else:
            print("  openvr_api.dll: not OpenComposite; left alone")
    except (OSError, ValueError) as e:
        problems = restore_entries(entries)
        if problems:
            print("ERROR: `on` failed ({}) and the restore did not verify:".format(e), file=sys.stderr)
            for pr in problems:
                print("  " + pr, file=sys.stderr)
            return 3
        clear_state()
        raise Refused("`on` failed ({}); both files were restored and verified, nothing is changed".format(e))
    state["mode"] = "on"
    save_state(state)
    print("HEADLESS ON. Start SteamVR, wait for it to settle, then launch the game. `off` puts it all back.")
    return 0


def cmd_off() -> int:
    st = load_state()
    if not st:
        print("nothing to restore: no headless state is recorded ({})".format(state_dir() / "state.json"))
        return 0
    entries = list(st.get("files", []))
    emu = st.get("emu")
    if emu:
        entries.append(emu)
    problems = restore_entries(entries)
    if problems:
        print("ERROR: restore did not verify; the state file and backups are kept:", file=sys.stderr)
        for pr in problems:
            print("  " + pr, file=sys.stderr)
        return 3
    clear_state()
    print("HEADLESS OFF. steamvr.vrsettings and openvr_api.dll{} restored, every hash verified.".format(
        " and driver_null.dll" if emu else ""))
    return 0


def cmd_emu(arg) -> int:
    p = paths()
    st = load_state()
    if arg == "on":
        if not EMU_BUILD.is_file():
            raise Refused("the emulator driver is not built ({}). Build it first: python tools/vr-emu-driver/build.py"
                          .format(EMU_BUILD))
        if not st or st.get("mode") != "on":
            raise Refused("turn headless on first (`on`); `off` then restores the emulator swap too")
        if st.get("emu"):
            if st.get("emu_recovery_pending"):
                raise Refused("emulator recovery is incomplete; run `emu off` or `off` to retry it")
            print("emulator driver already live")
            return 0
        bak = state_dir() / st["since"] / "driver_null.dll"
        entry = {"path": str(p["null_driver"]), "backup": str(bak), "sha256": backup(p["null_driver"], bak)}
        st["emu"] = entry
        save_state(st)
        try:
            replace_file(EMU_BUILD, p["null_driver"])
            if sha(p["null_driver"]) != sha(EMU_BUILD):
                raise OSError("driver_null.dll does not match the built emulator after the copy")
        except OSError as e:
            problems = restore_entries([entry])
            if problems:
                st["emu_recovery_pending"] = True
                save_state(st)
                print("ERROR: `emu on` failed ({}); recovery is incomplete; state and backup kept: {}".format(
                    e, "; ".join(problems)), file=sys.stderr)
                return 3
            st.pop("emu", None)
            st.pop("emu_recovery_pending", None)
            save_state(st)
            raise Refused("`emu on` failed ({}); the stock driver is restored".format(e))
        entry["applied_sha256"] = sha(p["null_driver"])
        save_state(st)
        print("emulator driver installed in place of SteamVR's null driver")
        return 0
    if arg == "off":
        if not st or not st.get("emu"):
            print("the emulator driver is not live; nothing to restore")
            return 0
        problems = restore_entries([st["emu"]])
        if problems:
            for pr in problems:
                print("ERROR: " + pr, file=sys.stderr)
            return 3
        st.pop("emu")
        st.pop("emu_recovery_pending", None)
        save_state(st)
        print("stock null driver restored -- hash verified")
        return 0
    print("usage: vr-headless emu on|off", file=sys.stderr)
    return 2


def cmd_status() -> int:
    st = load_state()
    p = paths()
    print("game      : {}".format(p["game"]))
    print("SteamVR   : {}".format(p["steamvr"]))
    live = p["live_dll"]
    kind = "missing" if not live.is_file() else ("OpenComposite" if is_opencomposite(live) else (
        "SteamVR's" if p["valve_dll"].is_file() and sha(live) == sha(p["valve_dll"]) else "a Valve/other build"))
    print("openvr_api: {}".format(kind))
    print("state     : {}".format("none (normal VR)" if not st else "{} since {}{}".format(
        st.get("mode"), st.get("since"), ", emulator recovery pending" if st.get("emu_recovery_pending") else
        (", emulator driver live" if st.get("emu") else ""))))
    print("emulator  : {}".format("built" if EMU_BUILD.is_file() else "not built (python tools/vr-emu-driver/build.py)"))
    return 0


def main(argv) -> int:
    args = list(argv) or ["status"]
    try:
        if args == ["status"]:
            return cmd_status()
        if args[0] == "on" and args[1:] in ([], ["--force"]):
            return cmd_on(force=args[1:] == ["--force"])
        if args == ["off"]:
            return cmd_off()
        if args[0] == "emu" and len(args) == 2:
            return cmd_emu(args[1])
    except Refused as e:
        print("ERROR: {}".format(e), file=sys.stderr)
        return 2
    except ValueError as e:                      # skyrim-paths.env unreadable, a vrsettings that is not JSON
        print("ERROR: {}".format(e), file=sys.stderr)
        return 2
    print("usage: vr-headless {status | on [--force] | off | emu on|off}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
