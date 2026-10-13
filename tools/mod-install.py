#!/usr/bin/env python3
"""mod-install -- install one mod archive by hand, recorded and reversible. A DRY RUN unless --apply.

    python tools/mod-install.py <archive> [--name NAME] [--group PREFIX] [--skip GLOB,GLOB]
                                [--overwrite-config] [--game-root-files] [--data DIR] [--apply]

The dry run prints archive-triage's report (every destination, anything outside Data/ flagged), then the plan:
one line per file with its action and its FULL destination. Read it before --apply. Nothing outside Data/ is
refused -- some mods legitimately install into the game folder (the SKSE loader, ENB or ReShade DLLs) -- so the
reader checks those lines.

What --apply guarantees:
  * Extraction happens in a temporary staging folder, never in place, and every planned file must be staged
    before ANY file is written (a name 7-Zip would not extract stops the install with nothing changed).
  * Every file it replaces is BACKED UP first, and a manifest records every file it wrote with its SHA-256:
        <records>/backups/<NAME>-<stamp>/   the originals, by destination
        <records>/<NAME>.files.txt          the manifest (written before the first write, completed after)
    <records> is $SKYRIM_MANUAL_INSTALLS, else <toolkit>/.claude/backups/manual-installs/ (never shipped).
    Uninstall = delete the manifest's files, then copy the backup folder's originals back.
  * An EXISTING config (.ini/.toml/.json) is left alone and the archive's copy is written beside it as
    <file>.new for a merge, unless --overwrite-config.
  * Hardlink-safe: each file is written to a temporary file beside its target and swapped in with os.replace,
    so a file a mod manager hardlinked is replaced, never written THROUGH (which would change its other links).
    Each written file is re-hashed against the staged copy; a mismatch exits 1.
  * Plugins (.esp/.esm/.esl) are copied but NEVER activated: the load order is the mod manager's, a separate
    reviewed change.

Where it installs:
  * Default: the game's Data/ (and the game folder for game-folder files), from $SKYRIM_GAME_ROOT, else
    skyrim-paths.env, else the toolkit folder. --data DIR installs into DIR as the Data folder instead.
  * Mod Organizer 2 (SKYRIM_MO2_MODS configured): a NEW mod folder <MO2 mods>/<NAME>/ with the Data/-relative
    layout inside it. Enable it in MO2 afterwards. An existing folder of that name is refused (pick another
    --name). Game-folder files cannot go through MO2's virtual Data/: they are listed and skipped unless
    --game-root-files, which writes them to the REAL game folder.
  * Vortex (Data/vortex.deployment.json present): a warning that Vortex may later overwrite or purge files it
    does not own. Not a refusal.

FOMOD and option archives: files are grouped by the option folder in front of the Data root. When there is
more than one group, pick ONE with --group (the exact prefix, or "(root)"); they are alternatives.

Exit: 0 done (or dry run) | 1 a written file did not re-hash to its staged copy | 2 bad usage, a refused name,
an ambiguous FOMOD, an existing MO2 mod folder, or a 7-Zip / staging failure (nothing written).
"""

from __future__ import annotations

import datetime
import fnmatch
import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import skyrim_paths  # noqa: E402

_spec = importlib.util.spec_from_file_location("archive_triage", HERE / "archive-triage.py")
triage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(triage)

CFG_EXT = (".ini", ".toml", ".json")
NAME_BAD = set('/\\:*?"<>|')
WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *("com%d" % i for i in range(1, 10)),
                    *("lpt%d" % i for i in range(1, 10))}
VORTEX_MARKER = "vortex.deployment.json"


def sha(p) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def name_problem(name: str) -> str:
    """Why NAME cannot be used (it becomes a folder and file name), or "". Checked before anything is written: a
    '/' in it once made the manifest write fail AFTER the files were installed."""
    if not name or not name.strip():
        return "is empty"
    bad = sorted(c for c in set(name) if c in NAME_BAD or ord(c) < 32 or ord(c) == 127)
    if bad:
        return "must not contain {}".format(" ".join(repr(c) for c in bad))
    if name in (".", "..") or name != name.rstrip(" ."):
        return "must not be '.', '..' or end with a dot or a space"
    if name.split(".")[0].lower() in WINDOWS_RESERVED:
        return "is a reserved Windows device name"
    return ""


def records_dir() -> Path:
    override = os.environ.get("SKYRIM_MANUAL_INSTALLS")
    return Path(override) if override else skyrim_paths.TOOLKIT_ROOT / ".claude" / "backups" / "manual-installs"


def mo2_mods() -> str:
    """SKYRIM_MO2_MODS from the environment or skyrim-paths.env ("" when not configured)."""
    from skyguard import paths as sg_paths
    try:
        return sg_paths.read_install(str(skyrim_paths.TOOLKIT_ROOT), os.environ).first("SKYRIM_MO2_MODS")
    except (OSError, ValueError):
        return ""


def write_replacing(src: Path, target: Path) -> None:
    """Copy src to a temporary file beside target, then swap it in: a hardlinked target is replaced, not written
    through."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".modinst_")
    os.close(fd)
    try:
        shutil.copyfile(str(src), tmp)
        os.replace(tmp, str(target))
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)                       # the tool's own temp file
        raise


def take_opt(args, flag):
    if flag not in args:
        return None
    i = args.index(flag)
    if i + 1 >= len(args) or args[i + 1].startswith("--"):
        raise ValueError("{} needs a value".format(flag))
    v = args[i + 1]
    del args[i:i + 2]
    return v


def plan_files(entries, data: Path, game: Path, mo2: bool, root_files: bool, overwrite_cfg: bool):
    """[(inner, destination, action, notes)] for the chosen group. Actions: NEW, OVERWRITE, CONFIG-NEW (written as
    <file>.new beside an existing config), SKIP-GAME-ROOT (MO2 without --game-root-files)."""
    plan = []
    for inner, rel, base in entries:
        dest, inside = triage.destination(rel, base, data, game)
        notes = []
        if base == "game" and mo2 and not root_files:
            plan.append((inner, dest, "SKIP-GAME-ROOT", ["game-folder file: MO2 cannot deploy it; "
                                                         "--game-root-files writes it to the real game folder"]))
            continue
        if not inside:
            notes.append("!! " + triage.OUTSIDE)
        if dest.is_file():
            if dest.name.lower().endswith(CFG_EXT) and not overwrite_cfg:
                action = "CONFIG-NEW"
                notes.append("your config is kept; the archive's copy goes to {}.new".format(dest.name))
            else:
                action = "OVERWRITE"
                if os.stat(dest).st_nlink > 1:
                    notes.append("hardlinked: replaced, not written through")
        else:
            action = "NEW"
        if dest.name.lower().endswith(triage.PLUGIN_EXT):
            notes.append("plugin: copied, NOT activated")
        plan.append((inner, dest, action, notes))
    return plan


def main(argv) -> int:
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
    args = list(argv)
    flags = {f: f in args for f in ("--apply", "--overwrite-config", "--game-root-files")}
    args = [a for a in args if a not in flags]
    try:
        name, group, skip_s, data_arg = (take_opt(args, f) for f in ("--name", "--group", "--skip", "--data"))
    except ValueError as e:
        print("usage: {}".format(e), file=sys.stderr)
        return 2
    unknown = [a for a in args if a.startswith("-")]
    if unknown or len(args) != 1:
        print(__doc__ if not args else "usage: mod-install.py <archive> [options]   (got {})".format(args),
              file=sys.stderr)
        return 2
    apply_, overwrite_cfg, root_files = flags["--apply"], flags["--overwrite-config"], flags["--game-root-files"]
    skip = [s for s in (skip_s or "").split(",") if s]
    arc = os.path.abspath(args[0].strip())
    name = name if name is not None else os.path.splitext(os.path.basename(arc))[0]
    why = name_problem(name)
    if why:
        print("ERROR: --name {!r} {} (it becomes a folder and file name)".format(triage.clean(name), why),
              file=sys.stderr)
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
    mods = "" if data_arg else mo2_mods()
    if data_arg:
        data = Path(data_arg)
    elif mods:
        data = Path(mods) / name
        if data.exists() and any(data.iterdir()):
            print("ERROR: the MO2 mod folder {} already exists and is not empty. This installs a NEW mod: pick "
                  "another --name, or remove that mod in MO2 first.".format(data), file=sys.stderr)
            return 2
    else:
        data = game / "Data"
        if not data.is_dir():
            print("ERROR: no Data folder at {} (game folder from {}). Set SKYRIM_GAME_ROOT or pass --data DIR."
                  .format(data, source), file=sys.stderr)
            return 2
    runtime = triage.detect_runtime(game)

    print("7-Zip: {} ({})".format(sevenz, how))
    print("game:  {} ({}; runtime {})".format(game, source, runtime))
    if mods:
        print("Mod Organizer 2: installing as a NEW mod into {} (enable it in MO2 afterwards)".format(data))
    else:
        print("installing into: {}".format(data))
    if (game / "Data" / VORTEX_MARKER).is_file():
        print("WARNING: Vortex manages this game ({} exists). Vortex may later overwrite or purge files it did not"
              " deploy itself, including these.".format(game / "Data" / VORTEX_MARKER))

    # The whole archive, as archive-triage reports it: every destination, outside-Data flagged.
    report = triage.triage(sevenz, arc, data, game, runtime)
    triage.show(report, runtime, False)
    if report["error"]:
        print("ERROR: cannot list {}: {}".format(triage.clean(arc), triage.clean(report["error"])), file=sys.stderr)
        return 2
    files, _ = triage.listing(sevenz, arc)
    groups = {}
    for f in files:
        prefix, rel, base = triage.map_entry(f["Path"])
        if rel is not None:
            groups.setdefault(prefix or "(root)", []).append((f["Path"], rel, base))
    if not groups:
        print("ERROR: nothing in the archive maps to an install destination", file=sys.stderr)
        return 2
    if group is None:
        if len(groups) > 1:
            print("ERROR: option archive (FOMOD or similar) -- pick ONE group with --group:", file=sys.stderr)
            for g, v in groups.items():
                print("   {!r}: {} file(s)".format(triage.clean(g), len(v)), file=sys.stderr)
            return 2
        group = next(iter(groups))
    if group not in groups:
        print("ERROR: group {!r} is not in the archive; it has {}".format(group, list(groups)), file=sys.stderr)
        return 2
    chosen = [e for e in groups[group] if not any(fnmatch.fnmatch(e[1].lower(), s.lower()) for s in skip)]
    plan = plan_files(chosen, data, game, bool(mods), root_files, overwrite_cfg)

    print("=" * 78)
    print("{}  [{}]  {} file(s)  {}".format(triage.clean(name), triage.clean(group), len(plan),
                                            "APPLY" if apply_ else "DRY RUN"))
    for inner, dest, action, notes in plan:
        shown = str(dest) + (".new" if action == "CONFIG-NEW" else "")
        print("  {:15} {}{}".format(action, triage.clean(shown), "  " + "; ".join(notes) if notes else ""))
    outside = sum(1 for p in plan if p[2] != "SKIP-GAME-ROOT" and any(n.startswith("!!") for n in p[3]))
    if outside:
        print("  !! {} file(s) would be written {}".format(outside, triage.OUTSIDE))
    if not apply_:
        print("DRY RUN -- nothing written. Re-run with --apply.")
        return 0
    return install(sevenz, arc, name, group, data, plan)


def install(sevenz, arc, name, group, data: Path, plan) -> int:
    todo = [p for p in plan if p[2] != "SKIP-GAME-ROOT"]
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    records = records_dir()
    backup = records / "backups" / "{}-{}".format(name, stamp)
    manifest = records / "{}.files.txt".format(name)
    stage = Path(tempfile.mkdtemp(prefix="modinst_"))           # the tool's own temp; removed below
    try:
        # Names go through a list file, never argv: a large archive overflows Windows' 32K command line.
        # -spd: names are literal, not wildcards; -scsUTF-8: the list file's encoding.
        listfile = stage / "_files.txt"
        listfile.write_text("\n".join(p[0] for p in todo) + "\n", encoding="utf-8", newline="\n")
        out = stage / "x"
        try:
            r = subprocess.run([sevenz, "x", "-y", "-spd", "-scsUTF-8", "-o" + str(out), arc, "@" + str(listfile)],
                               capture_output=True, stdin=subprocess.DEVNULL, timeout=3600)
        except (OSError, subprocess.TimeoutExpired) as e:
            print("ERROR: 7-Zip did not run: {}".format(e), file=sys.stderr)
            return 2
        if r.returncode != 0:
            print("ERROR: 7-Zip extract failed: {}".format(
                triage.clean((r.stderr or r.stdout).decode("utf-8", "replace")[-300:])), file=sys.stderr)
            return 2
        staged = {}
        for inner, dest, action, notes in todo:
            src = out.joinpath(*[s for s in re.split(r"[\\/]", inner) if s])
            if not src.is_file():
                print("ERROR: {} was not staged by 7-Zip; nothing was written".format(triage.clean(inner)),
                      file=sys.stderr)
                return 2
            staged[inner] = src

        records.mkdir(parents=True, exist_ok=True)
        header = ["# {} -- installed {} from {} [group {}]".format(name, stamp, os.path.basename(arc), group),
                  "# installed into: {}".format(data),
                  "# overwritten originals backed up under: {}".format(backup),
                  "# format: <sha256>  <file written>   (uninstall: delete these, restore the backups)"]
        planned = ["PLANNED  {}".format(target_of(p)) for p in todo]
        manifest.write_text("\n".join(header + ["# STATUS: in progress"] + planned) + "\n", encoding="utf-8")

        written, bad = [], 0
        for p in todo:
            inner, dest, action, notes = p
            target = target_of(p)
            if target.is_file():                     # OVERWRITE, or an older <file>.new: keep the original first
                b = backup / backup_rel(target)
                b.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(target), str(b))
            write_replacing(staged[inner], target)
            got, want = sha(target), sha(staged[inner])
            if got != want:
                bad += 1
                print("  VERIFY MISMATCH: {}".format(triage.clean(str(target))), file=sys.stderr)
            written.append("{}  {}".format(got, target))
        status = "# STATUS: complete, {} file(s), {} verify mismatch(es)".format(len(written), bad)
        manifest.write_text("\n".join(header + [status] + written) + "\n", encoding="utf-8")
        print("INSTALLED {} file(s), verify mismatches={}; manifest {}".format(len(written), bad, manifest))
        if backup.is_dir():
            print("originals backed up to {}".format(backup))
        print("Plugins were NOT activated. Enable any new plugin in your mod manager.")
        return 1 if bad else 0
    finally:
        shutil.rmtree(str(stage), ignore_errors=True)


def target_of(p) -> Path:
    inner, dest, action, notes = p
    return dest.with_name(dest.name + ".new") if action == "CONFIG-NEW" else dest


def backup_rel(target: Path) -> Path:
    """A backup path that keeps the destination's full location (drive folded into a folder name)."""
    parts = [x for x in target.parts]
    head = parts[0].replace(":", "").replace("\\", "").replace("/", "") or "root"
    return Path(head, *parts[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
