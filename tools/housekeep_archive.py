"""Archive a finished task folder: zip it to the NAS, prove the NAS copy, leave a stub, recycle the local payload.

Order is the safety: nothing local is touched until the zip on the NAS has been read back and every member's CRC
matches what was written from the local bytes. Any failure leaves the local folder exactly as it was.

    python tools/housekeep_archive.py restore <task>/ARCHIVED.md   # put an archived folder back
"""
import fnmatch
import json
import os
import sys
import time
import zipfile
from pathlib import Path

STUB = "ARCHIVED.md"
SAVE_EXTS = (".ess", ".skse")
KEEP_PATTERNS = ("README*", "*.md")      # top-level only, and only under KEEP_MAX_BYTES
KEEP_MAX_BYTES = 1 << 20


class ArchiveError(Exception):
    pass


def _members(src: Path):
    out = []
    for dp, dirs, files in os.walk(src):
        if ".git" in dirs:
            raise ArchiveError("contains a .git folder: a repository is never archived")
        for f in files:
            p = Path(dp) / f
            if f.lower().endswith(SAVE_EXTS):
                raise ArchiveError("holds a save ({}): saves are never archived".format(p))
            out.append(p)
    return out


def keep_local(src: Path, extra=()) -> set:
    """Top-level README/markdown under 1 MiB, the stub, and any path in `extra` (git-tracked, linked evidence)."""
    keep = {os.path.normcase(str(p)) for p in extra}
    for e in os.scandir(src):
        if e.is_file() and e.stat().st_size <= KEEP_MAX_BYTES and any(
                fnmatch.fnmatch(e.name, pat) for pat in KEEP_PATTERNS):
            keep.add(os.path.normcase(e.path))
    return keep


def _victims(folder: Path, keep: set) -> list:
    """What to recycle: a whole subfolder when nothing kept lives under it, otherwise descend into it, so a kept
    file deep inside (a git-tracked receipt, linked evidence) is never carried into the bin with its folder."""
    out = []
    for e in os.scandir(folder):
        k = os.path.normcase(e.path)
        if e.name == STUB and Path(e.path).parent == folder:
            continue
        if e.is_dir(follow_symlinks=False):
            if any(x.startswith(k + os.sep) for x in keep):
                out += _victims(Path(e.path), keep)
            else:
                out.append(Path(e.path))
        elif k not in keep:
            out.append(Path(e.path))
    return out


def nas_target(nas_dir: Path, name: str) -> Path:
    t = nas_dir / (name + ".zip")
    n = 2
    while t.exists() or t.with_name(t.name + ".partial").exists():
        t = nas_dir / "{}-{}.zip".format(name, n)
        n += 1
    return t


def archive_folder(src: Path, nas_dir: Path, recycler, extra_keep=()) -> dict:
    """Zip `src` into `nas_dir`, verify, write the stub, recycle everything not kept locally. Returns the record."""
    src = Path(src)
    files = _members(src)
    if not files:
        raise ArchiveError("empty folder")
    nas_dir.mkdir(parents=True, exist_ok=True)
    target = nas_target(nas_dir, src.name)
    partial = target.with_name(target.name + ".partial")
    total = 0
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
            for p in files:
                z.write(p, p.relative_to(src).as_posix())
                total += p.stat().st_size
        with zipfile.ZipFile(partial) as z:                  # read back FROM THE NAS: every CRC, every member
            bad = z.testzip()
            if bad is not None:
                raise ArchiveError("NAS copy failed its CRC check at {}".format(bad))
            names = {i.filename: i.file_size for i in z.infolist()}
        for p in files:
            if names.get(p.relative_to(src).as_posix()) != p.stat().st_size:
                raise ArchiveError("NAS copy is missing or short for {}".format(p))
        os.replace(partial, target)
    except Exception as e:
        try:                       # this run's own half-written zip: same-run scratch, and a NAS share has no bin
            if partial.exists():
                partial.unlink()
        except OSError:
            pass
        if isinstance(e, ArchiveError):
            raise
        raise ArchiveError("{}: {}".format(type(e).__name__, e)) from e
    keep = keep_local(src, extra_keep)
    record = {"zip": str(target), "files": len(files), "bytes": total, "date": time.strftime("%Y-%m-%d %H:%M"),
              "kept_local": sorted(os.path.relpath(k, os.path.normcase(str(src))) for k in keep)}
    (src / STUB).write_text(
        "# Archived\n\nThis folder's contents were zipped to the NAS by housekeep and verified (every member's CRC "
        "read back from the NAS copy) before the local payload went to the Recycle Bin.\n\n"
        "- zip: `{zip}`\n- files: {files}\n- bytes: {bytes}\n- date: {date}\n\n"
        "Restore: `python tools/housekeep_archive.py restore \"{stub}\"`\n\n<!-- {js} -->\n".format(
            stub=src / STUB, js=json.dumps(record), **record), encoding="utf-8")
    res = recycler(_victims(src, keep))
    failed = {str(p): err for p, err in res.items() if err}
    record["recycle_failed"] = failed
    if failed:                             # the stub must not claim a payload is gone when part of it is still here
        with open(src / STUB, "a", encoding="utf-8") as f:
            f.write("\n**Still here:** {} item(s) could not be recycled (open in another program?). The zip is "
                    "complete; these local copies are duplicates of it:\n\n".format(len(failed)))
            f.writelines("- `{}`\n".format(p) for p in sorted(failed))
    return record


def restore_archive(stub: Path) -> Path:
    """Extract the zip named in the stub back into its folder. Refuses if any member's path exists with other bytes."""
    stub = Path(stub)
    text = stub.read_text(encoding="utf-8")
    record = json.loads(text.rsplit("<!-- ", 1)[1].split(" -->", 1)[0])
    dest = stub.parent
    with zipfile.ZipFile(record["zip"]) as z:
        clash = [i.filename for i in z.infolist()
                 if (dest / i.filename).exists() and (dest / i.filename).stat().st_size != i.file_size]
        if clash:
            raise ArchiveError("refusing to overwrite {} existing file(s), e.g. {}".format(len(clash), clash[0]))
        for i in z.infolist():
            if not (dest / i.filename).exists():
                z.extract(i, dest)
    return dest


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "restore":
        print("restored into", restore_archive(Path(sys.argv[2])))
    else:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        sys.exit(2)
