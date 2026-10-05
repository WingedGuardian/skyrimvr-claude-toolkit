"""Plan 6 Task 0: build the scratch bed C:/Temp/skyguard-ml2/ from REAL BYTE COPIES.

    python mkbed.py <real_game_root>

Every copy is `shutil.copyfile` (data only: never copy2, never a link). After each
copy: st_ino must differ from the source, st_nlink == 1 on both, and the copy's
sha256 must equal the baseline's. Any failure deletes nothing and stops.
Requires baseline.py record to have run first.
"""

from __future__ import annotations

import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sandbox  # noqa: E402
from baseline import COPY_SET, OUT as BASELINE, sha256  # noqa: E402

ROOT = "C:/Temp/skyguard-ml2"
GAME = ROOT + "/game"
TEXTBED = ROOT + "/textbed"


def fail(msg):
    print("STOP:", msg)
    sys.exit(1)


def main(real_root: str) -> int:
    os.environ.setdefault(sandbox.ENV, ROOT)
    with open(BASELINE, encoding="utf-8") as fh:
        base = {f["path"]: f for f in json.load(fh)["files"]}
    total = sum(f["size"] for f in base.values())
    free = shutil.disk_usage("C:/").free
    print("copy set %.2f GB, need x1.3 = %.2f GB, free %.2f GB" % (total / 1e9, total * 1.3 / 1e9, free / 1e9))
    if free < total * 1.3:
        fail("not enough free space")
    os.makedirs(GAME + "/Data", exist_ok=True)
    for rel in COPY_SET:
        src = os.path.join(real_root, rel).replace("\\", "/")
        dst = os.path.join(GAME, rel).replace("\\", "/")
        sandbox.assert_inside(dst)
        if os.path.exists(dst):
            os.chmod(dst, 0o666)  # bed only: clear a leftover read-only bit
        shutil.copyfile(src, dst)
        s, d = os.stat(src), os.stat(dst)
        if (s.st_dev, s.st_ino) == (d.st_dev, d.st_ino):
            fail("copy shares the source's file ID: " + dst)
        if s.st_nlink != 1 or d.st_nlink != 1:
            fail("nlink src=%d dst=%d for %s" % (s.st_nlink, d.st_nlink, dst))
        h = sha256(dst)
        if h != base[src]["sha256"]:
            fail("copy hash differs from baseline: " + dst)
        sandbox.assert_lockable(dst)
        print("copied %-40s ino %d != %d, nlink 1/1, sha ok" % (rel, d.st_ino, s.st_ino))
    with open(GAME + "/plugins.txt", "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write("# scratch bed\n*Skyrim.esm\n*Update.esm\n*Dawnguard.esm\n*SkyrimVR.esm\n")
    # Decoy pool + unlocked control twins (64 KiB random each).
    for n in range(1, 5):
        for name in ("Decoy-%d.esm" % n, "Control-%d.esm" % n):
            with open(GAME + "/Data/" + name, "wb") as fh:
                fh.write(os.urandom(64 * 1024))
    # Separate text bed for apply_patch / Claude Edit (they need text).
    os.makedirs(TEXTBED + "/Data", exist_ok=True)
    body = "".join("line %03d of a text decoy for apply_patch\n" % i for i in range(1, 41))
    for name in ("Update.esm", "Control.esm"):
        with open(TEXTBED + "/Data/" + name, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
    print("bed ready:", GAME, "and", TEXTBED)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
