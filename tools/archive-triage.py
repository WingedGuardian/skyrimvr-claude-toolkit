#!/usr/bin/env python3
"""archive-triage -- what would this mod archive change in the game folder, before anything is installed?

For each archive it lists every entry with 7-Zip (path, size, CRC; nothing is extracted to disk), works out
where each entry would land, and compares the CRC with the file already there:

    NEW      nothing installed at that destination
    SAME     identical bytes are already installed
    CHANGED  a different file is installed there
    NO CRC   the listing carries no CRC for the entry, so it cannot be compared (never guessed as CHANGED)

Destinations are shown plainly, and every destination OUTSIDE Data/ is flagged
"outside Data/ -- check this is intended". That is visibility, not refusal: some mods legitimately install
outside Data/ (the SKSE loader, ENB or ReShade DLLs in the game folder), and the reader decides.

CHANGED on a plugin (.esp/.esm/.esl) means EITHER the update changed it OR the installed copy was edited by
hand (in xEdit, say) -- this tool cannot tell those apart. Compare against the installed version's ORIGINAL
archive before overwriting it.

FOMOD archives hold option folders. Files are grouped by the folder prefix in front of the Data root, so
alternative options show as separate groups -- they are NOT all installed.

DLLs are read (into memory, never to disk) for their SKSE exports. On Skyrim VR they are also searched for
VR build markers -- a heuristic for VR only, because SKSE silently refuses a DLL built for another runtime.
On SE/AE the VR check is skipped and the output says so.

Usage:
    python tools/archive-triage.py <archive|dir> [more ...] [--json OUT.json] [--data DIR] [--all]

    --data DIR   compare against DIR instead of <game>/Data (e.g. one mod's folder under Mod Organizer 2's
                 mods/, which is where MO2 keeps installed files)
    --all        list every file; by default code and config files are listed one by one and other assets
                 are summarised per destination folder

7-Zip: $SEVENZIP, else 7z / 7zz / 7za on PATH, else Program Files (tools/skyrim_paths.py; pinned version in
docs/installing-tools.md). The game folder: $SKYRIM_GAME_ROOT, else skyrim-paths.env, else the toolkit folder.

Exit: 0 printed | 1 an accounting mismatch (the rows do not sum to the entries listed) | 2 bad usage,
7-Zip or the Data folder missing, or an archive that could not be listed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unicodedata
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skyrim_paths  # noqa: E402

# Top-level folders of Data/. An archive path is mapped onto Data/ at the first of these it contains.
ROOTS = {"skse", "scripts", "source", "meshes", "textures", "interface", "sound", "music", "seq", "strings",
         "mcm", "nemesis_engine", "pandora_engine", "video", "lodsettings", "grass", "shadersfx", "facegen",
         "dialogueviews", "calientetools", "netscriptframework", "platform", "skypatcher", "tools"}
PLUGIN_EXT = (".esp", ".esm", ".esl")
# Loose files at an option folder's root that belong in Data/. NOT .txt/.md/.json: a root readme.txt would map
# to Data/readme.txt and overwrite another mod's readme.
ROOT_FILE_EXT = PLUGIN_EXT + (".bsa", ".ini")
# Binaries at an option folder's root that are not under a Data root land in the GAME folder when installed as
# packaged (the SKSE loader, ENB / ReShade DLLs) -- shown, and flagged as outside Data/.
GAME_FILE_EXT = (".exe", ".dll", ".asi")
CODE_EXT = PLUGIN_EXT + GAME_FILE_EXT + (".pex", ".ini", ".toml", ".json", ".bsa", ".ba2")
ARCHIVE_EXT = (".7z", ".zip", ".rar")
KINDS = ("NEW", "SAME", "CHANGED", "NO CRC")
VR_MARKERS = [b"CommonLibVR", b"SkyrimVR", b"version-1-4-15", b"1-4-15", b"1.4.15", b"VR Address Library",
              b"openvr", b"SKSEVR"]
SKSE_EXPORTS = ("SKSEPlugin_Version", "SKSEPlugin_Query", "SKSEPlugin_Load")
OUTSIDE = "outside Data/ -- check this is intended"
HEX8 = re.compile(r"[0-9A-Fa-f]{8}")


def clean(s: str) -> str:
    """A name from an archive, safe to print: control and format characters (a CR that rewinds the line, an
    escape sequence, a right-to-left override) are shown as escapes instead of acting on the terminal."""
    out = []
    for ch in str(s):
        if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp"):
            out.append("\\x{:02x}".format(ord(ch)) if ord(ch) < 0x100 else "\\u{:04x}".format(ord(ch)))
        else:
            out.append(ch)
    return "".join(out)


def is_dir_entry(entry) -> bool:
    if entry.get("Folder") == "+":
        return True
    # "Attributes = DvPUM 41A40000" for a folder, " 01800000" for a file. Only the LETTER token says D: the hex
    # word of a file can contain a D (01D00000) and must not make it vanish as a "folder".
    letters = [t for t in entry.get("Attributes", "").split() if not HEX8.fullmatch(t)]
    return any("D" in t for t in letters)


def run_7z(sevenz, args, timeout=600):
    """Bytes out, so a CR inside an entry name stays inside it (text mode would split the line there)."""
    extra = ["-sccUTF-8"] if os.name == "nt" else []   # console output in UTF-8, not the OEM code page
    return subprocess.run([sevenz, *args[:1], *extra, *args[1:]], capture_output=True,
                          stdin=subprocess.DEVNULL, timeout=timeout)


def listing(sevenz, arc):
    """(entries, None) or (None, why). Entries are the -slt blocks of files (folders dropped)."""
    try:
        r = run_7z(sevenz, ["l", "-slt", "-ba", arc])
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, "7-Zip did not run: {}".format(e)
    out = r.stdout.decode("utf-8", "replace")
    if r.returncode != 0:
        return None, (r.stderr.decode("utf-8", "replace") or out).strip()[:300] or "7-Zip exit {}".format(r.returncode)
    files, cur = [], {}
    for ln in out.split("\n") + [""]:
        ln = ln[:-1] if ln.endswith("\r") else ln
        if not ln.strip():
            if cur.get("Path") and not is_dir_entry(cur):
                files.append(cur)
            cur = {}
            continue
        if " = " in ln:
            k, v = ln.split(" = ", 1)
            cur[k] = v
    return files, None


def map_entry(path):
    """(option prefix, path relative to the base, base) with base "data" or "game"; (None, None, None) when the
    entry is not installed content (docs, fomod/, screenshots)."""
    segs = [s for s in re.split(r"[\\/]", path) if s]
    for i, s in enumerate(segs):
        sl = s.lower()
        if sl == "data" and i + 1 < len(segs):
            return "/".join(segs[:i]), "/".join(segs[i + 1:]), "data"
        if sl in ROOTS and i + 1 < len(segs):
            # A FOMOD option folder can share a Data root's name ("mcm/" holding MCM/, Scripts/ and the .esp). It
            # is a prefix, not Data/mcm, when the next segment is itself a root (bar the real Scripts<->Source
            # nesting) or a plugin sits directly inside it.
            nxt = segs[i + 1].lower()
            nested_root = nxt in ROOTS and {sl, nxt} != {"scripts", "source"}
            plugin_inside = i + 2 == len(segs) and nxt.endswith(PLUGIN_EXT + (".bsa",))
            if not (nested_root or plugin_inside):
                return "/".join(segs[:i]), "/".join(segs[i:]), "data"
        if i == len(segs) - 1 and sl.endswith(ROOT_FILE_EXT):
            return "/".join(segs[:i]), s, "data"
        if i == len(segs) - 1 and sl.endswith(GAME_FILE_EXT):
            return "/".join(segs[:i]), s, "game"
    return None, None, None


def destination(rel, base, data: Path, game: Path):
    """(absolute normalised destination, inside Data/?). A `..` or a drive in an entry name resolves where it
    resolves; it is shown, not hidden."""
    root = data if base == "data" else game
    dest = Path(os.path.normpath(os.path.join(str(root), rel.replace("/", os.sep))))
    try:
        inside = os.path.commonpath([str(dest).lower(), str(data).lower()]) == os.path.normpath(str(data)).lower()
    except ValueError:                          # a different drive
        inside = False
    return dest, inside


def crc_of(p: Path) -> str:
    c = 0
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            c = zlib.crc32(chunk, c)
    return "{:08X}".format(c & 0xFFFFFFFF)


def dll_markers(sevenz, arc, inner, runtime):
    """(size or None, VR markers or None when the VR check does not apply, exports)."""
    try:
        r = run_7z(sevenz, ["e", "-so", "-spd", arc, "--", inner])
    except (OSError, subprocess.TimeoutExpired):
        return None, None, []
    b = r.stdout
    if not b:
        return None, None, []
    exports = [e for e in SKSE_EXPORTS if e.encode() in b]
    markers = None if runtime == "SE/AE" else [m.decode() for m in VR_MARKERS if m in b]
    return len(b), markers, exports


def detect_runtime(game: Path) -> str:
    if (game / "SkyrimVR.exe").is_file():
        return "VR"
    if (game / "SkyrimSE.exe").is_file():
        return "SE/AE"
    return "unknown"


def triage(sevenz, arc, data: Path, game: Path, runtime):
    files, err = listing(sevenz, arc)
    out = {"archive": os.path.basename(arc), "path": str(arc), "error": err, "groups": {}, "dlls": [],
           "unmapped": [], "listed": 0, "tally": {k: 0 for k in KINDS}}
    if files is None:
        return out
    out["listed"] = len(files)
    for f in files:
        prefix, rel, base = map_entry(f["Path"])
        if rel is None:
            out["unmapped"].append(f["Path"])
            continue
        dest, inside = destination(rel, base, data, game)
        if not dest.is_file():
            kind = "NEW"
        else:
            crc = f.get("CRC", "").strip().upper()
            kind = "NO CRC" if not HEX8.fullmatch(crc) else ("SAME" if crc == crc_of(dest) else "CHANGED")
        out["tally"][kind] += 1
        g = out["groups"].setdefault(prefix or "(root)", {k: [] for k in KINDS})
        g[kind].append({"entry": f["Path"], "dest": str(dest), "inside": inside,
                        "shown": ("Data/" + os.path.relpath(dest, data).replace(os.sep, "/")) if inside else str(dest)})
        if rel.lower().endswith(".dll"):
            size, markers, exports = dll_markers(sevenz, arc, f["Path"], runtime)
            out["dlls"].append({"entry": f["Path"], "group": prefix or "(root)", "size": size, "vr_markers": markers,
                                "exports": exports})
    return out


def show(t, runtime, show_all):
    """Print one archive; return True when its rows sum to the entries listed."""
    print("=" * 78)
    print(clean(t["archive"]))
    if t["error"]:
        print("  ERROR listing archive: {}".format(clean(t["error"])))
        return True
    outside = []
    for g, d in t["groups"].items():
        print("  [{}]  ".format(clean(g)) + " | ".join("{} {}".format(k, len(d[k])) for k in KINDS))
        for kind in ("CHANGED", "NEW", "NO CRC", "SAME"):
            items = d[kind]
            listed = [x for x in items if show_all or not x["inside"] or x["shown"].lower().endswith(CODE_EXT)]
            if kind == "SAME" and not show_all:
                listed = [x for x in listed if not x["inside"]]
            for x in listed:
                flag = ""
                low = x["shown"].lower()
                if not x["inside"]:
                    flag = "  !! " + OUTSIDE
                    outside.append(x)
                elif kind == "CHANGED" and low.endswith(PLUGIN_EXT):
                    flag = "  ! plugin: an update's change OR your own edit -> compare with the original archive"
                elif kind == "CHANGED" and low.endswith((".ini", ".toml", ".json")):
                    flag = "  ! config: would overwrite YOUR settings -> diff, keep yours"
                print("     {:7} {}{}".format(kind, clean(x["shown"]), flag))
            ids = {id(x) for x in listed}
            rest = [x for x in items if id(x) not in ids]
            folders = {}
            for x in rest:
                folders.setdefault(x["shown"].rsplit("/", 1)[0] + "/", []).append(x)
            for folder, xs in sorted(folders.items()):
                print("     {:7} {} file(s) -> {}".format(kind, len(xs), clean(folder)))
    for d in t["dlls"]:
        ex = ",".join(d["exports"]) or "none found"
        if d["vr_markers"] is None:
            v = "VR-marker check skipped: this install is SE/AE (the check is a VR-only heuristic)"
        elif d["vr_markers"]:
            v = "VR markers: " + ", ".join(d["vr_markers"])
        else:
            v = ("no VR markers -> UNPROVEN, not proof of an SE-only build (on the dev install 12 of 126 "
                 "working VR DLLs carried none)")
        if runtime == "unknown" and d["vr_markers"] is not None:
            v += " [VR-only heuristic; the game runtime was not identified]"
        print("  DLL {} [{}]  {}  (SKSE exports: {})".format(clean(d["entry"]), clean(d["group"]), v, ex))
    if t["unmapped"]:
        print("  not installed by this mapping (docs, fomod/, images): {}".format(len(t["unmapped"])))
        if show_all:
            for u in t["unmapped"]:
                print("     -       {}".format(clean(u)))
    if outside:
        print("  !! {} destination(s) {}".format(len(outside), OUTSIDE))
    tally = t["tally"]
    rows = {k: sum(len(d[k]) for d in t["groups"].values()) for k in KINDS}
    total = sum(tally[k] for k in KINDS) + len(t["unmapped"])
    print("  accounting: {} listed = {} + {} not installed".format(
        t["listed"], " + ".join("{} {}".format(tally[k], k) for k in KINDS), len(t["unmapped"])))
    # tally is counted where each entry is classified; rows are what was printed from. Per kind, so a counter that
    # stops being independent (or an entry filed under one kind and counted under another) shows here.
    if total != t["listed"] or any(rows[k] != tally[k] for k in KINDS):
        print("  ACCOUNTING MISMATCH: {} classified for {} listed; rows {} vs counted {}".format(
            total, t["listed"], rows, tally))
        return False
    print("  accounting OK")
    return True


def mo2_mods():
    try:
        from skyguard import paths as sg_paths
        return sg_paths.read_install(str(skyrim_paths.TOOLKIT_ROOT), os.environ).first("SKYRIM_MO2_MODS")
    except (OSError, ValueError, ImportError):
        return ""


def main(argv) -> int:
    try:                                        # an archive name the console cannot encode must not crash the run
        sys.stdout.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
    args, jout, data_arg, show_all = list(argv), None, None, False
    for flag in ("--json", "--data"):
        if flag in args:
            i = args.index(flag)
            if i + 1 >= len(args):
                print("usage: {} needs a path".format(flag), file=sys.stderr)
                return 2
            if flag == "--json":
                jout = args[i + 1]
            else:
                data_arg = args[i + 1]
            del args[i:i + 2]
    if "--all" in args:
        show_all = True
        args.remove("--all")
    bad = [a for a in args if a.startswith("-")]
    if bad or not args:
        print(__doc__ if not args else "usage: unknown option(s) {}".format(bad), file=sys.stderr)
        return 2

    sevenz, how = skyrim_paths.find_7zip()
    if not sevenz:
        print("ERROR: " + how, file=sys.stderr)
        return 2
    try:
        game, source = skyrim_paths.game_root()
    except ValueError as e:
        print("ERROR: {}".format(e), file=sys.stderr)
        return 2
    data = Path(data_arg) if data_arg else game / "Data"
    if not data.is_dir():
        print("ERROR: no Data folder at {} (game folder from {}). Set SKYRIM_GAME_ROOT or pass --data DIR."
              .format(data, source), file=sys.stderr)
        return 2
    runtime = detect_runtime(game)
    print("7-Zip: {} ({})".format(sevenz, how))
    print("game:  {} ({}; runtime {})".format(game, source, runtime))
    print("compared against: {}".format(data))
    if not data_arg and mo2_mods():
        print("  note: Mod Organizer 2 is configured. Data/ holds only files installed there directly; to compare"
              " with a mod MO2 manages, pass --data <MO2 mods folder>/<mod name>.")

    arcs = []
    # Paths piped from a Windows-written list arrive with a trailing CR: strip it, or every one is "not found".
    for a in (x.strip() for x in args if x.strip()):
        if os.path.isdir(a):
            arcs += sorted(os.path.join(a, f) for f in os.listdir(a) if f.lower().endswith(ARCHIVE_EXT))
        else:
            arcs.append(a)
    results, ok, unreadable = [], True, 0
    for a in arcs:
        t = triage(sevenz, os.path.abspath(a), data, game, runtime)
        results.append(t)
        ok = show(t, runtime, show_all) and ok
        unreadable += bool(t["error"])
    print("=" * 78)
    print("{} archive(s); {} could not be listed".format(len(arcs), unreadable))
    if jout:
        with open(jout, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=1)
    if not ok:
        return 1
    return 2 if unreadable else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
