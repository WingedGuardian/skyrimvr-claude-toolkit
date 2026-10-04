"""Plan 6 Task 0/5: record and verify the REAL install's files the spike reads from.

    python baseline.py record <game_root>   -> C:/Temp/skyguard-ml2/real-baseline.json
    python baseline.py verify               -> exit 0 identical, 1 any difference

Read-only against the real install: os.stat, a streamed sha256, and `icacls <file>`
with no switch (which only prints). Nothing here writes outside C:/Temp/skyguard-ml2.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time

OUT = "C:/Temp/skyguard-ml2/real-baseline.json"

COPY_SET = [
    "Data/Skyrim.esm",
    "Data/Update.esm",
    "Data/SkyrimVR.esm",
    "Data/Dawnguard.esm",
    "Data/Skyrim_VR - Main.bsa",
    "Data/Skyrim - Voices_ru0.bsa",
    "Data/Skyrim - Interface.bsa",
    "Data/Skyrim - Shaders.bsa",
    "SkyrimVR.exe",
]


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(4 * 1024 * 1024)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def icacls_text(path: str) -> str:
    # `icacls <path>` with no other argument only DISPLAYS the DACL.
    r = subprocess.run(["icacls", os.path.normpath(path)], capture_output=True, text=True)
    lines = [ln.rstrip() for ln in r.stdout.splitlines() if ln.strip()]
    # Drop the trailing "Successfully processed 1 files" summary and the path prefix.
    lines = [ln for ln in lines if not ln.startswith("Successfully processed")]
    if lines:
        lines[0] = lines[0][len(os.path.normpath(path)):].strip()
    return "\n".join(ln.strip() for ln in lines)


def describe(path: str, with_hash: bool = True) -> dict:
    st = os.stat(path)
    return {
        "path": path.replace("\\", "/"),
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "st_dev": st.st_dev,
        "st_ino": st.st_ino,
        "st_nlink": st.st_nlink,
        "attrs": getattr(st, "st_file_attributes", None),
        "acl": icacls_text(path),
        "sha256": sha256(path) if with_hash else None,
    }


def record(game_root: str) -> int:
    files = []
    for rel in COPY_SET:
        p = os.path.join(game_root, rel).replace("\\", "/")
        if not os.path.isfile(p):
            print("MISSING", p)
            continue
        t = time.time()
        files.append(describe(p))
        print("recorded %-45s %12d bytes  %.1fs" % (rel, files[-1]["size"], time.time() - t))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"game_root": game_root, "taken": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "files": files}, fh, indent=1)
    print("wrote", OUT, len(files), "files")
    return 0


def verify() -> int:
    with open(OUT, encoding="utf-8") as fh:
        base = json.load(fh)
    bad = 0
    for old in base["files"]:
        new = describe(old["path"])
        diffs = [k for k in ("size", "mtime_ns", "st_dev", "st_ino", "st_nlink", "attrs", "acl", "sha256")
                 if old[k] != new[k]]
        if diffs:
            bad += 1
            print("DIFFERENT", old["path"], diffs)
            for k in diffs:
                print("   was:", old[k], "\n   now:", new[k])
        else:
            print("identical", old["path"])
    print("RESULT: %d of %d real files changed" % (bad, len(base["files"])))
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "record":
        sys.exit(record(sys.argv[2]))
    if len(sys.argv) == 2 and sys.argv[1] == "verify":
        sys.exit(verify())
    print(__doc__)
    sys.exit(2)
