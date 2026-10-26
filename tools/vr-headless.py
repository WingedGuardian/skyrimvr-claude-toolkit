#!/usr/bin/env python3
"""vr-headless -- boot Skyrim VR with no headset, on SteamVR's null driver, so DevBench can drive the game.

    bash tools/vr-headless.sh status
    bash tools/vr-headless.sh run --desktop background|current [--park] [--emu] [--force]
                                               # the managed session: prepare, launch, wait for the game to
                                               # exit, then restore -- restoration is part of exit and error
    bash tools/vr-headless.sh on [--force] [--park]   # headless: SteamVR null driver (and Valve's openvr_api.dll)
    bash tools/vr-headless.sh launch --desktop background|current   # start SteamVR + the game on `on` files
    bash tools/vr-headless.sh stop             # orderly exit of the owned game, its helpers, then `off`
    bash tools/vr-headless.sh off              # back to normal VR, byte-exact; safe to repeat, safe after a crash
    bash tools/vr-headless.sh emu on|off       # the official SkyrimVR Devkit driver, once you have FETCHED it

Skyrim VR only: SE/AE has no OpenVR path, and the tool refuses there.

ASK THE USER FIRST, every session: this desktop (`--desktop current`) or a private background desktop
(`--desktop background`)? Never reuse the last answer. Either way their mouse, keyboard and window focus are
left alone: no frontend, no input injection, no cursor capture, no foreground activation by this tool. Game
control is DevBench's. `current` asks Windows not to activate the new windows, but SteamVR/Skyrim may still
activate themselves; agree that with the user. A wrapper that starts `run` from another program must start it
fully detached (stdin/stdout/stderr closed or redirected to a file), or the caller hangs until the game exits.

What `on` changes, and nothing else:
  * <Steam>/config/steamvr.vrsettings: the null driver is enabled and forced, the HMD requirement dropped,
    every other driver disabled, and compositor standby turned off (a null HMD never reports a wearer, so SteamVR
    would park the compositor and the game would never present a frame).
  * <game>/openvr_api.dll, ONLY when it is OpenComposite (an OpenXR runtime that bypasses SteamVR): it is replaced
    by SteamVR's own openvr_api.dll. A Valve openvr_api.dll is left alone.
  * --park: OpenComposite's own SKSE plugins (Data/SKSE/Plugins/OCUnleashedSKSE.dll, OpenCompositeInput.dll),
    which expect OpenComposite, are moved into the run's backup folder for the session.
BEFORE changing anything, every file is copied to the backup area, hashed, the copies verified, and a state file
written. If any step fails, everything is put back from those copies before the tool exits. `on` refuses a
steamvr.vrsettings that ALREADY forces the null driver: `off` would faithfully restore a headless setup, and
"restored byte-exact" would not mean "ready for the headset".

`off` restores every file byte-exact from the backups and verifies each hash. A file that changed AFTER `on` (an
OpenComposite update, a settings edit) is first copied, hash-verified, into the run's backup folder
(changed-after-on-<stamp>/), so nothing is lost. The closed state file is kept as state-closed-<stamp>.json. With no
state file it has nothing to do and says so, so it is safe to run again, or after a crash or a reboot. A restore that does not verify keeps the
state file and the backups, and exits 3. Run `off` again once the reported problem is resolved. While a process
that `launch` recorded is still running, `off` refuses: use `stop`.

Close SteamVR first: it rewrites steamvr.vrsettings when it exits. `on` refuses while vrserver.exe runs (--force
overrides).

`launch` (Windows): refuses while any game or SteamVR process runs. `--desktop background` needs the user's Steam
client already OPEN (it stays theirs, on their desktop) and starts SteamVR's vrstartup.exe directly on a new
private desktop; `--desktop current` asks Steam to start SteamVR (app 250820) on the user's desktop. Steam and its
browser helpers are never owned, stopped or moved. Ownership requires a current creation-time-ordered parent
chain to a launched PID, with that launch's path and handle creation time matching the census. Background
processes also need readable threads on the private desktop. A launcher that exits before the first census, an
unreadable census or unproved placement refuses launch without adopting the process. Late helpers need the same
proof. Processes are then recorded by PID + path + start time; legacy sessions without this proof cannot be stopped
automatically (close their processes yourself, then `off`). If
SteamVR's Room Setup appears, the game waits (up to 25 s) until that helper's window is proved to be on the
private desktop and is stopped (or it exits by itself); an unproved, wrong-path or reused-PID helper refuses the
launch, and a window on the user's desktop stops the session. Then sksevr_loader.exe starts, and `launch`
returns once DevBench answers /api/health for the owned game's PID with lifecycle dataLoaded and frame > 100.

How `run` decides the game has EXITED (a wrong "exited" would restore files under a running game): it needs
EXIT_MISSES consecutive process reads without the owned game spanning at least EXIT_WINDOW seconds, and then
neither DevBench (/api/health naming that PID) nor the process handle (same PID, same creation time) may still
see it; either one resets the count. A failed process read is "unknown", never "absent".

How `stop` exits the game: only an owned, identity-verified game, and only when DevBench names its PID and the
frame counter is ADVANCING outside a load (a frozen counter means loading, paused or a console -- DevBench's
health semantics). It never sends the quit command to a game that is loading: it waits up to QUIT_WAIT seconds
for a quit-safe state, then refuses with the session kept (close the game yourself, then `stop` again). After the
game exits (same robust test), the owned SteamVR helpers are terminated (creation time re-checked on the opened
handle), then `off` runs. Only one `stop` at a time (a finish.lock beside the state file).

Where things are found:
  * Steam: $STEAM_ROOT, else the registry (HKCU\\Software\\Valve\\Steam SteamPath, HKLM ...\\Valve\\Steam InstallPath),
    else the default install folder. SteamVR is looked up in every library listed in
    <Steam>/steamapps/libraryfolders.vdf.
  * The game: $SKYRIM_GAME_ROOT, else skyrim-paths.env, else the folder the toolkit is in (tools/skyrim_paths.py).
  * Backups and the state file: <toolkit>/.claude/backups/vr-headless/ (never shipped), or $VR_HEADLESS_DIR.
  * DevBench: $DEVBENCH_PORT, else Data/SKSE/Plugins/devbench/runtime.json, else 8921 (VR).

The emulator driver (tools/vr-emu-driver/) is never shipped: `python tools/vr-emu-driver/fetch.py` downloads the
official SkyrimVR Devkit v2.0.1 package from its pinned release commit and verifies its hashes. `emu on` (Steam and
SteamVR closed: they hold the driver files) swaps it in for SteamVR's null driver files, backed up and verified the
same way, sets the Devkit's mouse look off in an existing %LOCALAPPDATA%/SkyrimVR Devkit/Actions/controls.txt, and
sets aside a stale Valve driver_null.dll.sig; `emu off` (and `off`) restore all of it. `run` adopts a session
prepared by `on` + `emu on`, so the background desktop (which needs Steam open) can still use the emulator.

Headless is for pose-independent tests. It is NOT valid for anything that depends on where the head and hands are
(HIGGS, VRIK, PLANCK, hand collision): the null HMD and the emulated controllers are not a person.

Exit: 0 done | 2 refused or failed (with the reason; anything changed has been put back, or the message says the
session is kept) | 3 `off` could not verify a restore (the backups are kept; see the message).
"""

from __future__ import annotations

import datetime
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skyrim_paths  # noqa: E402

DEFAULT_DIR = skyrim_paths.TOOLKIT_ROOT / ".claude" / "backups" / "vr-headless"
EMU_DIR = Path(__file__).resolve().parent / "vr-emu-driver"
EMU_PACKAGE = EMU_DIR / "build" / "null"
OTHER_DRIVERS = ("vrlink", "oculus", "oculus_legacy", "lighthouse", "htc", "prism")
STEAM_DEFAULTS = ("C:/Program Files (x86)/Steam", "C:/Program Files/Steam", "~/.steam/steam", "~/.local/share/Steam")
OC_PLUGINS = ("OCUnleashedSKSE.dll", "OpenCompositeInput.dll")
GAME_NAMES = ("SkyrimVR", "sksevr_loader")
VR_NAMES = GAME_NAMES + ("vrserver", "vrmonitor", "vrcompositor", "steamvr_room_setup", "vrwebhelper", "vrdashboard",
                         "vrstartup")
STEAM_NAMES = ("steam", "steamwebhelper")
IDENTITY = ("Id", "ProcessName", "Path", "StartTime")
STEAMVR_APPID = "250820"
EXIT_MISSES = 3           # consecutive process reads without the owned game ...
EXIT_WINDOW = 5.0         # ... spanning at least this many seconds, before DevBench and the handle are asked
QUIT_WAIT = 120.0         # how long `stop` waits for a quit-safe (advancing, not loading) game
LOADING_LIFECYCLES = ("preLoadGame",)
IS_WINDOWS = os.name == "nt"


class Refused(Exception):
    pass


class ProcessReadError(Exception):
    """The process list could not be read: unknown, never evidence that a process is gone."""


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
            "null_driver": vr / "drivers" / "null" / "bin" / "win64" / "driver_null.dll",
            "vrstartup": vr / "bin" / "win64" / "vrstartup.exe",
            "room_setup": vr / "tools" / "steamvr_room_setup" / "win64" / "steamvr_room_setup.exe",
            "loader": game / "sksevr_loader.exe",
            "plugins": game / "Data" / "SKSE" / "Plugins"}


def is_opencomposite(p: Path) -> bool:
    """OpenComposite's openvr_api.dll names itself in its strings; Valve's never does."""
    try:
        data = p.read_bytes()
    except OSError:
        return False
    return b"OpenComposite" in data or "OpenComposite".encode("utf-16-le") in data


def already_headless(settings: dict) -> bool:
    """A steamvr.vrsettings that already forces/enables the null driver: restoring it would restore headless."""
    return (settings.get("steamvr", {}).get("forcedDriver") == "null" or
            settings.get("driver_null", {}).get("enable") is True)


def devkit_controls() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return Path(base) / "SkyrimVR Devkit" / "Actions" / "controls.txt"


def load_fetch():
    spec = importlib.util.spec_from_file_location("vr_emu_fetch", EMU_DIR / "fetch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- processes and DevBench (Windows)

def read_processes():
    """The game, SteamVR and Steam processes with Id/ProcessName/Path/StartTime (UTC). [] off Windows.

    A failed read is retried twice (briefly); a persistent failure raises ProcessReadError -- unknown, never
    "the game is gone"."""
    if not IS_WINDOWS:
        return []
    names = ",".join("'{}'".format(n) for n in VR_NAMES + STEAM_NAMES)
    command = ("Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -in @(" + names + ") } | "
               "Select-Object Id,ProcessName,Path,@{Name='StartTime';Expression={$_.StartTime.ToUniversalTime()"
               ".ToString('o')}} | ConvertTo-Json -Compress")
    last = None
    for attempt in range(3):
        try:
            r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], capture_output=True, text=True,
                               check=True, timeout=8)
            value = json.loads(r.stdout) if r.stdout.strip() else []
            return value if isinstance(value, list) else [value]
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as e:
            last = e
            time.sleep(0.25 * (attempt + 1))
    raise ProcessReadError("the process list could not be read ({}: {})".format(type(last).__name__, last))


def same_process(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in IDENTITY)


def make_desktop(kind: str, name=None):
    import vr_background_desktop as d
    return (d.CurrentDesktop if kind == "current" else d.BackgroundDesktop)(name)


def process_alive(pid, start) -> bool:
    """Same PID AND creation time; an unreadable handle is unknown, never an exit."""
    if not start:
        raise ProcessReadError("the owned process has no creation time")
    import vr_background_desktop as d
    try:
        return d.process_alive(pid, start)
    except (OSError, ValueError) as e:
        raise ProcessReadError("the owned process handle could not be read: {}".format(e)) from e


def terminate_verified(pid, start) -> None:
    import vr_background_desktop as d
    d.terminate_verified(pid, start)


def parent_pid(pid):
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                        "(Get-CimInstance Win32_Process -Filter 'ProcessId = {}').ParentProcessId".format(int(pid))],
                       capture_output=True, text=True, timeout=8)
    return int(r.stdout.strip()) if r.stdout.strip().isdigit() else None


def read_process_tree():
    """A creation-time-bound parent census, including non-VR intermediates. Failure proves no ownership."""
    command = ("Get-CimInstance Win32_Process -ErrorAction Stop | Select-Object "
               "@{n='Id';e={$_.ProcessId}},@{n='ParentId';e={$_.ParentProcessId}},"
               "@{n='Path';e={$_.ExecutablePath}},"
               "@{n='StartTime';e={$_.CreationDate.ToUniversalTime().ToString('o')}} | ConvertTo-Json -Compress")
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], capture_output=True,
                           text=True, check=True, timeout=8)
        value = json.loads(r.stdout)
        return value if isinstance(value, list) else [value]
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        raise ProcessReadError("process ancestry is unreadable: {}".format(e)) from e


def proven_process(x, session, census, desk):
    """Only a current, time-ordered chain to an identity recorded at CreateProcess can establish ownership.

    A launcher that vanished before its first census cannot be proved; refuse rather than adopt another run.
    Previously proved descendants can anchor later helpers, but their generation must still match the census.
    """
    from vr_background_desktop import parse_start
    def identity(a, b):
        def key(p):
            return (p["Id"], Path(p["Path"]).as_posix().casefold(), parse_start(p["StartTime"]))
        return key(a) == key(b)
    nodes = {p.get("Id"): p for p in census}
    anchors = list(session["processes"])
    for key in ("runtime_launch", "steam_launch", "game_launch"):
        child = session.get(key)
        if child:
            anchors.append({"Id": child["pid"], "Path": child.get("executable"),
                            "StartTime": child.get("start_time")})
    try:
        node = nodes.get(x["Id"], {})
        if not identity(node, x):
            return False
        seen = set()
        while node["Id"] not in seen:
            seen.add(node["Id"])
            created = parse_start(node["StartTime"])
            if any(identity(node, a) for a in anchors):
                break
            parent = nodes.get(node.get("ParentId"))
            if not parent:
                return False
            if created < parse_start(parent["StartTime"]):
                return False
            node = parent
        else:
            return False
        if session["desktop_mode"] == "background" and not desk.process_on_desktop(x["Id"]):
            return False
        return True
    except (KeyError, TypeError, ValueError, OSError):
        return False


def devbench_port(game: Path) -> int:
    if os.environ.get("DEVBENCH_PORT", "").isdigit():
        return int(os.environ["DEVBENCH_PORT"])
    try:
        data = json.loads((game / "Data" / "SKSE" / "Plugins" / "devbench" / "runtime.json").read_text(encoding="utf-8"))
        return int(data["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return 8921


def devbench_health(port: int):
    """DevBench's off-thread /api/health, or None when it does not answer."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:{}/api/health".format(port), timeout=3) as r:
            value = json.load(r)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def devbench_quit(port: int):
    request = urllib.request.Request("http://127.0.0.1:{}/api/tool/console".format(port),
                                     data=json.dumps({"action": "exec", "command": "qqq"}).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=8) as r:
        return json.load(r)


def still_alive(game: dict, port: int) -> bool:
    """Second opinions after the process list stopped showing the game: DevBench, then the process handle."""
    health = devbench_health(port)
    if health and health.get("pid") == game.get("Id"):
        return True
    return process_alive(game.get("Id"), game.get("StartTime"))


class ExitWatch:
    """Decides, one process read at a time, whether the owned games have robustly exited (module docstring)."""

    def __init__(self, games, port: int):
        self.games, self.port, self.misses, self.first_miss = games, port, 0, None

    def gone(self) -> bool:
        try:
            running = read_processes()
        except ProcessReadError as e:
            print("  process list unreadable ({}); the game counts as still running".format(e), file=sys.stderr)
            return False                                        # unknown is never a miss
        if any(same_process(p, g) for g in self.games for p in running):
            self.misses, self.first_miss = 0, None              # seen: start counting again
            return False
        now = time.monotonic()
        self.misses += 1
        self.first_miss = now if self.first_miss is None else self.first_miss
        if self.misses < EXIT_MISSES or now - self.first_miss < EXIT_WINDOW:
            return False
        try:
            alive = any(still_alive(g, self.port) for g in self.games)
        except ProcessReadError:
            self.misses, self.first_miss = 0, None
            return False
        if alive:
            print("  the process list missed the game, but it is still alive; waiting on", file=sys.stderr)
            self.misses, self.first_miss = 0, None              # the list was wrong
            return False
        return True


def wait_until_exited(games, port: int, deadline=None) -> bool:
    """True once every game in `games` is robustly gone; False at `deadline` (None = wait as long as it runs)."""
    watch = ExitWatch(games, port)
    while not watch.gone():
        if deadline is not None and time.monotonic() > deadline:
            return False
        time.sleep(1)
    return True


def quit_safe(pid: int, port: int) -> bool:
    """DevBench names this PID, the frame counter advances, and no load is in progress."""
    h1 = devbench_health(port)
    if not h1:
        return False
    time.sleep(1)
    h2 = devbench_health(port)
    if not h2:
        return False
    if any(h.get("pid") != pid for h in (h1, h2)):
        return False                                    # DevBench is answering for some other process
    if h2.get("lastLifecycle") in LOADING_LIFECYCLES:
        return False
    f1, f2 = h1.get("frame"), h2.get("frame")
    return isinstance(f1, int) and isinstance(f2, int) and 0 <= f1 < f2


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


def write_bytes_atomic(dst: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(dst.parent), prefix=".vrheadless_")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, str(dst))


def backup(src: Path, dst: Path) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dst))
    h = sha(src)
    if sha(dst) != h:
        raise Refused("the backup of {} does not match the original; nothing was changed".format(src))
    return h


def set_aside(live: Path, folder: Path) -> None:
    """Take a file out of play by MOVING it into the run's backup folder (never deleting it)."""
    folder.mkdir(parents=True, exist_ok=True)
    os.replace(str(live), str(folder / "{}-{}".format(datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f"),
                                                       live.name)))


def mouse_look_off(data: bytes) -> bytes:
    """The Devkit controls file with `mouse_enabled 0`, every other line and its line ending kept."""
    lines = data.splitlines(keepends=True)
    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith(b"mouse_enabled"):
            lines[i] = b"mouse_enabled 0" + (b"\r\n" if line.endswith(b"\r\n") else b"\n")
            found = True
    if not found:
        lines.append((b"" if not lines or lines[-1].endswith(b"\n") else b"\n") + b"mouse_enabled 0\n")
    return b"".join(lines)


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


def emu_entries(st) -> list:
    emu = st.get("emu") or []
    return [emu] if isinstance(emu, dict) else list(emu)       # a pre-4.0 state recorded one entry


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


def owned_running(st) -> list:
    """Recorded session processes that are still running (ProcessReadError when that cannot be known)."""
    owned = (st.get("session") or {}).get("processes") or []
    if not owned:
        return []
    running = read_processes()
    port = st["session"].get("port", 8921)
    return [o for o in owned if any(same_process(p, o) for p in running) or still_alive(o, port)]


# --------------------------------------------------------------------------- commands

def cmd_on(force=False, park=False) -> int:
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
    if already_headless(json.loads(p["vrsettings"].read_text(encoding="utf-8"))):
        raise Refused("{} already forces or enables the null driver, so `off` would restore a headless setup. Put "
                      "SteamVR back on your headset's driver first (SteamVR settings, or remove the driver_null / "
                      "forcedDriver entries), then run `on`.".format(p["vrsettings"]))
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir = state_dir() / stamp
    swap = is_opencomposite(p["live_dll"])
    entries = [{"path": str(p["vrsettings"]), "backup": str(bdir / "steamvr.vrsettings"),
                "sha256": backup(p["vrsettings"], bdir / "steamvr.vrsettings")},
               {"path": str(p["live_dll"]), "backup": str(bdir / "openvr_api.dll"),
                "sha256": backup(p["live_dll"], bdir / "openvr_api.dll")}]
    for name in (OC_PLUGINS if park else ()):
        plugin = p["plugins"] / name
        if plugin.is_file():
            entries.append({"path": str(plugin), "backup": str(bdir / "parked" / name),
                            "sha256": backup(plugin, bdir / "parked" / name), "applied_sha256": None, "park": True})
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
        for e in entries:
            if e.get("park"):
                set_aside(Path(e["path"]), bdir / "set-aside")
                print("  parked for the session: {}".format(e["path"]))
        if not park and any((p["plugins"] / n).is_file() for n in OC_PLUGINS):
            print("  note: OpenComposite SKSE plugins are installed; --park takes them out of play for the session")
    except (OSError, ValueError) as e:
        problems = restore_entries(entries)
        if problems:
            print("ERROR: `on` failed ({}) and the restore did not verify:".format(e), file=sys.stderr)
            for pr in problems:
                print("  " + pr, file=sys.stderr)
            return 3
        clear_state()
        raise Refused("`on` failed ({}); every file was restored and verified, nothing is changed".format(e))
    state["mode"] = "on"
    save_state(state)
    print("HEADLESS ON. `run`/`launch` start SteamVR and the game (or start them yourself). `off` puts it all back.")
    return 0


def cmd_off() -> int:
    st = load_state()
    if not st:
        print("nothing to restore: no headless state is recorded ({})".format(state_dir() / "state.json"))
        return 0
    try:
        alive = owned_running(st)
    except ProcessReadError as e:
        raise Refused("{}; cannot tell whether the session's game or SteamVR still runs, so nothing is restored. Try "
                      "again, or use `stop`".format(e))
    if alive:
        raise Refused("the session's {} still running; restoring files under it would break it. Use `stop` (or close "
                      "them), then `off`".format(", ".join(sorted({x["ProcessName"] for x in alive})) + " is"))
    entries = list(st.get("files", [])) + emu_entries(st)
    problems = restore_entries(entries)
    if problems:
        print("ERROR: restore did not verify; the state file and backups are kept:", file=sys.stderr)
        for pr in problems:
            print("  " + pr, file=sys.stderr)
        return 3
    clear_state()
    print("HEADLESS OFF. {} file(s) restored, every hash verified (steamvr.vrsettings, openvr_api.dll{}{}).".format(
        len(entries), ", parked plugins" if any(e.get("park") for e in entries) else "",
        ", the emulator swap" if emu_entries(st) else ""))
    return 0


def emu_package():
    """{relative path: fetched file} for a fetched package whose DLL is the pinned build; Refused otherwise."""
    fetch = load_fetch()
    files = {rel: EMU_PACKAGE.joinpath(*rel.split("/")) for rel in fetch.SWAPPED}
    missing = [str(f) for f in files.values() if not f.is_file()]
    if missing:
        raise Refused("the emulator driver is not fetched ({}). Fetch it first: python tools/vr-emu-driver/fetch.py"
                      .format(", ".join(missing)))
    if sha(files[fetch.DLL]) != fetch.DRIVER_DLL_SHA256:
        raise Refused("{} is not the pinned Devkit v2.0.1 build; fetch it again (python tools/vr-emu-driver/fetch.py)"
                      .format(files[fetch.DLL]))
    return files


def cmd_emu(arg) -> int:
    p = paths()
    st = load_state()
    if arg == "on":
        files = emu_package()
        if not st or st.get("mode") != "on":
            raise Refused("turn headless on first (`on`); `off` then restores the emulator swap too")
        if st.get("emu"):
            if st.get("emu_recovery_pending"):
                raise Refused("emulator recovery is incomplete; run `emu off` or `off` to retry it")
            print("emulator driver already live")
            return 0
        try:
            busy = sorted({x["ProcessName"] for x in read_processes() if x["ProcessName"] in VR_NAMES + STEAM_NAMES})
        except ProcessReadError as e:
            raise Refused("{}; cannot confirm Steam and SteamVR are closed".format(e))
        if busy:
            raise Refused("close Steam and SteamVR first ({} running): they hold the driver files".format(", ".join(busy)))
        null_dir = p["null_driver"].parents[2]
        bak = state_dir() / st["since"] / "emu-{}".format(datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        entries = []
        for rel, src in files.items():
            live = null_dir.joinpath(*rel.split("/"))
            if not live.is_file():
                raise Refused("SteamVR's null driver has no {}: not the layout this tool expects; nothing changed"
                              .format(live))
            entries.append({"path": str(live), "backup": str(bak / rel), "sha256": backup(live, bak / rel),
                            "source": str(src)})
        sig = p["null_driver"].with_name("driver_null.dll.sig")
        if sig.is_file():          # Valve's signature of the stock DLL; it does not describe the swapped one
            entries.append({"path": str(sig), "backup": str(bak / "bin/win64/driver_null.dll.sig"),
                            "sha256": backup(sig, bak / "bin/win64/driver_null.dll.sig"), "applied_sha256": None,
                            "set_aside": True})
        controls = devkit_controls()
        if controls.is_file():
            entries.append({"path": str(controls), "backup": str(bak / "controls.txt"),
                            "sha256": backup(controls, bak / "controls.txt"), "controls": True})
        st["emu"] = entries
        save_state(st)                             # every emulator backup recorded before the first swap
        try:
            for e in entries:
                live = Path(e["path"])
                if e.get("set_aside"):
                    set_aside(live, bak / "set-aside")
                elif e.get("controls"):
                    write_bytes_atomic(live, mouse_look_off(live.read_bytes()))
                    e["applied_sha256"] = sha(live)
                else:
                    replace_file(Path(e["source"]), live)
                    if sha(live) != sha(Path(e["source"])):
                        raise OSError("{} does not match the fetched driver after the copy".format(live))
                    e["applied_sha256"] = sha(live)
        except OSError as e:
            problems = restore_entries(entries)
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
        save_state(st)
        print("emulator driver (SkyrimVR Devkit v2.0.1) installed in place of SteamVR's null driver")
        return 0
    if arg == "off":
        if not st or not st.get("emu"):
            print("the emulator driver is not live; nothing to restore")
            return 0
        try:
            busy = owned_running(st)
        except ProcessReadError as e:
            raise Refused("{}; cannot establish that the emulator is idle".format(e))
        if busy:
            raise Refused("the session still runs; use `stop` before restoring the emulator")
        problems = restore_entries(emu_entries(st))
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


def cmd_launch(desktop) -> dict:
    """Start SteamVR and the game for a prepared (`on`) session and record what this run owns."""
    if not IS_WINDOWS:
        raise Refused("`launch`/`run` start processes on a Windows desktop; this is not Windows")
    p = paths()
    st = load_state()
    if not st or st.get("mode") != "on":
        raise Refused("enable a headless session first (`on`)")
    if st.get("session"):
        raise Refused("this headless session already launched once; `stop` it, then start a new one")
    if not p["loader"].is_file():
        raise Refused("no sksevr_loader.exe in {}: SKSE VR is required to launch the game".format(p["game"]))
    try:
        running = read_processes()
    except ProcessReadError as e:
        raise Refused("{}; cannot confirm that no game or SteamVR process is running".format(e))
    busy = [x for x in running if x["ProcessName"] in VR_NAMES]
    if busy:
        raise Refused("close the game and SteamVR before launching: {}".format(
            ", ".join(sorted({x["ProcessName"] for x in busy}))))
    prior_steam = [x for x in running if x["ProcessName"] in STEAM_NAMES]
    if desktop == "background":
        if not any(x["ProcessName"] == "steam" for x in prior_steam):
            raise Refused("open Steam normally on your desktop first and leave it running; the background session "
                          "uses it but never starts, moves or closes it")
        if not p["vrstartup"].is_file():
            raise Refused("SteamVR's runtime launcher is missing: {}".format(p["vrstartup"]))
    elif not (p["steam"] / "steam.exe").is_file():
        raise Refused("steam.exe not found in {}".format(p["steam"]))
    desk = make_desktop(desktop)
    session = {"ownership_version": 1, "creator_pid": os.getpid(), "desktop_mode": desktop, "desktop": desk.name, "processes": [],
               "steam_policy": "keep-running", "preexisting_steam": prior_steam, "port": devbench_port(p["game"])}
    st["session"] = session
    save_state(st)

    def record():
        now = read_processes()
        candidates = [x for x in now if x["ProcessName"] in VR_NAMES]
        session["boundary"] = desk.boundary([x["Id"] for x in candidates])
        save_state(st)
        if desktop == "background" and not session["boundary"].get("isolated"):
            raise Refused("a test window escaped onto your desktop; stopping this owned session")
        census = read_process_tree() if candidates else []
        for x in candidates:
            if not any(same_process(x, o) for o in session["processes"]):
                if not proven_process(x, session, census, desk):
                    raise Refused("a game or SteamVR process has unproved launch ownership or desktop placement; "
                                  "it was not recorded and will not be stopped")
                session["processes"].append(x)
                save_state(st)
        return now

    try:
        record()
        if desktop == "background":
            _, child = desk.start([p["vrstartup"]], p["vrstartup"].parent)
            session["runtime_launch"] = child
        else:
            _, child = desk.start([p["steam"] / "steam.exe", "-silent", "-applaunch", STEAMVR_APPID], p["steam"])
            session["steam_launch"] = child
        save_state(st)
        deadline = time.monotonic() + 90
        while not any(x["ProcessName"] == "vrserver" for x in record()):
            if time.monotonic() > deadline:
                raise Refused("SteamVR did not start within 90 s; the session is kept for `stop`")
            time.sleep(1)
        wait_for_room_setup(p, desktop, session, record, st)
        _, child = desk.start([p["loader"]], p["game"])
        session["game_launch"] = child
        save_state(st)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            games = [x for x in record() if x["ProcessName"] == "SkyrimVR"]
            health = devbench_health(session["port"])
            if (len(games) == 1 and health and health.get("ok") and health.get("pid") == games[0]["Id"] and
                    health.get("lastLifecycle") == "dataLoaded" and isinstance(health.get("frame"), int) and
                    health["frame"] > 100):
                session["startup_health"] = health
                save_state(st)
                return {"started": True, "health": health, "save_loaded": False}
            time.sleep(2)
        raise Refused("the main menu was not reached (DevBench health for the owned game) within 180 s; the session "
                      "is kept for `stop`")
    finally:
        desk.close()


def wait_for_room_setup(p, desktop, session, record, st) -> None:
    """Room Setup may appear before its window exists. Pending proof waits within 25 s; it never launches around an
    unproved helper and never terminates one that is not proved to be the owned, privately windowed process."""
    deadline = time.monotonic() + 25
    seen = {}
    while True:
        rooms = [x for x in record() if x["ProcessName"] == "steamvr_room_setup"]
        if rooms:
            if desktop == "background":
                for x in rooms:
                    if not x.get("StartTime") or not x.get("Path") or Path(x["Path"]).resolve() != p["room_setup"].resolve():
                        raise Refused("Room Setup's executable or start time is unproved; the game was not launched")
                    prior = seen.get(x["Id"])
                    if prior and not same_process(prior, x):
                        raise Refused("Room Setup's PID was reused; the game was not launched")
                    seen[x["Id"]] = dict(x)
                pending = [x["Id"] for x in rooms if x["Id"] not in session["boundary"].get("private_window_pids", [])]
                if pending:
                    session["room_setup_pending"] = pending
                    save_state(st)
                    if time.monotonic() >= deadline:
                        raise Refused("Room Setup's private window was not proved within 25 s; the game was not "
                                      "launched")
                    time.sleep(0.25)
                    continue
                for x in rooms:
                    terminate_verified(x["Id"], x["StartTime"])
                session["room_setup_cleanup"] = rooms
                save_state(st)
            exit_deadline = time.monotonic() + 120
            while any(x["ProcessName"] == "steamvr_room_setup" for x in record()):
                if time.monotonic() > exit_deadline:
                    raise Refused("Room Setup still holds the scene; the game was not launched")
                time.sleep(1)
            return
        if seen:
            session["room_setup_natural_exit"] = list(seen.values())
            save_state(st)
            return
        if time.monotonic() >= deadline:
            return
        time.sleep(1)


def stop_owned(st) -> dict:
    """Exit the owned game in order, then the owned SteamVR helpers. Steam and its helpers are never touched."""
    session = st["session"]
    if session.get("ownership_version") != 1:
        raise Refused("this legacy session has no launch ownership proof; close its processes yourself, then `off`")
    owned = session["processes"]
    port = session.get("port", 8921)
    running = read_processes()
    games = [x for x in running if x["ProcessName"] in GAME_NAMES]
    if any(not any(same_process(g, o) for o in owned) for g in games):
        raise Refused("a game process this session did not start is running; stop refused, nothing sent")
    skyrim = [g for g in games if g["ProcessName"] == "SkyrimVR"]
    if skyrim:
        watch, deadline = ExitWatch(skyrim, port), time.monotonic() + QUIT_WAIT
        while True:
            if quit_safe(skyrim[0]["Id"], port):
                session["exit_response"] = devbench_quit(port)
                save_state(st)
                if not wait_until_exited(skyrim, port, deadline=time.monotonic() + 90):
                    raise Refused("the owned game has not exited after the quit command; the session is kept and no "
                                  "live file was restored")
                break
            if watch.gone():
                break                                           # it exited by itself while we waited
            if time.monotonic() > deadline:
                raise Refused("the owned game is running but not ready for an orderly exit (loading, paused, or "
                              "DevBench not answering for its PID); nothing was sent and the session is kept. Close "
                              "the game yourself, then run `stop` again")
            time.sleep(1)                                       # paced: DevBench may answer at once, or not at all
    elif games and not wait_until_exited(games, port, deadline=time.monotonic() + 60):
        raise Refused("the owned SKSE loader is still running; the session is kept")
    stopped = []
    deadline = time.monotonic() + 15
    while True:
        running = read_processes()
        for x in running:                     # late helpers need the same ownership proof as launch
            if (x["ProcessName"] in VR_NAMES and x["ProcessName"] not in GAME_NAMES and x.get("Path") and
                    not any(same_process(x, o) for o in owned)):
                desk = make_desktop(session["desktop_mode"], session["desktop"])
                try:
                    proved = proven_process(x, session, read_process_tree(), desk)
                finally:
                    desk.close()
                if proved:
                    owned.append(x)
                    session.setdefault("late_helpers", []).append({"process": x})
                    save_state(st)
        helpers = [x for x in running if x["ProcessName"] in VR_NAMES and x["ProcessName"] not in GAME_NAMES and
                   any(same_process(x, o) for o in owned)]
        if not helpers:
            break
        if time.monotonic() > deadline:
            raise Refused("the owned SteamVR helpers have not stopped; the session is kept")
        for x in helpers:
            terminate_verified(x["Id"], x["StartTime"])
            stopped.append(x["Id"])
        time.sleep(0.5)
    session["cleanup"] = {"game_exited": True, "helpers_stopped": stopped, "steam_left_running": True}
    save_state(st)
    return session["cleanup"]


class _FinishLock:
    """One `stop` at a time. The lock is this run's own scratch, removed when this run finishes."""

    def __enter__(self):
        state_dir().mkdir(parents=True, exist_ok=True)
        self.path = state_dir() / "finish.lock"
        deadline = time.monotonic() + 90
        while True:
            try:
                with open(self.path, "x") as fh:
                    fh.write(str(os.getpid()))
                return self
            except FileExistsError:
                if time.monotonic() > deadline:
                    raise Refused("another `stop` holds {}; if none is running (a crash left it), move it to the "
                                  "Recycle Bin and retry".format(self.path))
                time.sleep(0.2)

    def __exit__(self, *exc):
        os.remove(str(self.path))          # created by this run, above


def cmd_stop() -> int:
    with _FinishLock():
        st = load_state()
        if st and st.get("session", {}).get("processes"):
            print("stopped: {}".format(json.dumps(stop_owned(st))))
        return cmd_off()


def cmd_run(desktop, park=False, emu=False, force=False) -> int:
    """The managed lifecycle: prepare, launch, wait for the game to exit, restore -- restoration is in `finally`."""
    if not IS_WINDOWS:
        raise Refused("`run` starts processes on a Windows desktop; this is not Windows")
    st = load_state()
    if st and (st.get("mode") != "on" or st.get("session")):
        raise Refused("`run` needs a fresh session or one prepared by `on`; finish the existing one first (`stop`)")
    error = None
    try:
        if not st:
            rc = cmd_on(force=force, park=park)
            if rc:
                return rc
        else:
            print("adopting the headless session prepared since {}".format(st.get("since")))
        if emu and not load_state().get("emu"):
            rc = cmd_emu("on")
            if rc:
                return rc
        print(json.dumps({"managed_session_started": cmd_launch(desktop)}), flush=True)
        session = load_state()["session"]
        games = [x for x in session["processes"] if x["ProcessName"] == "SkyrimVR"]
        if not games:
            raise Refused("the launch did not record the game's identity")
        wait_until_exited(games, session.get("port", 8921))
        print("the owned game has exited; restoring")
    except BaseException as e:
        error = e
        raise
    finally:
        if error is not None:
            print("ERROR during the managed session ({}: {}); cleaning up".format(type(error).__name__, error),
                  file=sys.stderr)
        st = load_state()
        if st and st.get("session") and st["session"].get("creator_pid") != os.getpid():
            raise Refused("this session belongs to another run (pid {}); not cleaning it up".format(
                st["session"].get("creator_pid")))
        if st and st.get("session", {}).get("processes"):
            rc = cmd_stop()                      # owned processes exist: orderly exit, then restore
        elif st:
            rc = cmd_off()                       # nothing was started: restore only
        else:
            rc = 0
    return rc


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
    if st and st.get("session"):
        s = st["session"]
        print("session   : {} desktop, {} owned process(es), started by pid {}".format(
            s.get("desktop_mode"), len(s.get("processes", [])), s.get("creator_pid")))
    try:
        emu_package()
        fetched = "fetched, pinned v2.0.1 verified"
    except Refused:
        fetched = "not fetched (python tools/vr-emu-driver/fetch.py)"
    print("emulator  : {}".format(fetched))
    return 0


USAGE = ("usage: vr-headless {status | run --desktop background|current [--park] [--emu] [--force] | "
         "on [--force] [--park] | launch --desktop background|current | stop | off | emu on|off}")


def main(argv) -> int:
    args = list(argv) or ["status"]
    try:
        if args == ["status"]:
            return cmd_status()
        if args[0] == "on" and set(args[1:]) <= {"--force", "--park"} and len(set(args[1:])) == len(args[1:]):
            return cmd_on(force="--force" in args, park="--park" in args)
        if args == ["off"]:
            return cmd_off()
        if args == ["stop"]:
            return cmd_stop()
        if args[0] == "emu" and len(args) == 2:
            return cmd_emu(args[1])
        if args[0] in ("run", "launch") and "--desktop" in args:
            i = args.index("--desktop")
            desktop = args[i + 1] if i + 1 < len(args) else None
            flags = args[1:i] + args[i + 2:]
            allowed = {"--park", "--emu", "--force"} if args[0] == "run" else set()
            if desktop in ("background", "current") and set(flags) <= allowed and len(set(flags)) == len(flags):
                if args[0] == "launch":
                    print(json.dumps(cmd_launch(desktop), indent=2))
                    return 0
                return cmd_run(desktop, park="--park" in flags, emu="--emu" in flags, force="--force" in flags)
    except Refused as e:
        print("ERROR: {}".format(e), file=sys.stderr)
        return 2
    except ProcessReadError as e:
        print("ERROR: {}; nothing was assumed about running processes".format(e), file=sys.stderr)
        return 2
    except (ValueError, RuntimeError, OSError) as e:   # skyrim-paths.env unreadable, a vrsettings that is not
        print("ERROR: {}".format(e), file=sys.stderr)  # JSON, a desktop/process call the OS refused
        return 2
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
