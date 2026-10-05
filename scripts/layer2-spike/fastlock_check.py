"""Cross-check fastlock (ctypes) against lockprobe (attrib/icacls) and time both over the full
R1 VR name list. Usage: python fastlock_check.py --out r.json"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sandbox  # noqa: E402
import lockprobe  # noqa: E402
import fastlock  # noqa: E402
from unlock_probe import R1_VR  # noqa: E402

ROOT = "C:/Temp/skyguard-ml2"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.environ.setdefault(sandbox.ENV, ROOT)
    res = {"equivalence": {}, "timing": {}}
    d = ROOT + "/c10/fast"
    os.makedirs(d, exist_ok=True)
    files = []
    for n in R1_VR:
        f = d + "/" + n
        if not os.path.exists(f):
            with open(f, "wb") as fh:
                fh.write(b"\0" * 65536)
        files.append(f)
    for mech in sorted(lockprobe.MECHANISMS):
        f1, f2 = files[0], files[1]
        lockprobe.apply(mech, f1)
        fastlock.apply(mech, f2)
        s1, s2 = lockprobe.state(f1), lockprobe.state(f2)
        strip = lambda s: [x.split(" ", 1)[-1].split(":", 1)[-1] if ":" in x else x for x in s["deny_aces"]]
        res["equivalence"][mech] = {"same_bit": s1["readonly"] == s2["readonly"],
                                    "icacls_lockprobe": s1["deny_aces"], "icacls_fastlock": s2["deny_aces"],
                                    "same_ace": sorted(x.rsplit(" ", 1)[-1] for x in s1["deny_aces"])
                                    == sorted(x.rsplit(" ", 1)[-1] for x in s2["deny_aces"])}
        fastlock.remove(f1)  # cross-remove: fast removes what icacls applied, and vice versa
        lockprobe.remove(f2)
        res["equivalence"][mech]["cross_remove_clean"] = not lockprobe.is_locked(f1) and not lockprobe.is_locked(f2)
        runs = []
        for _ in range(5):
            t0 = time.perf_counter()
            for f in files:
                fastlock.apply(mech, f)
            t1 = time.perf_counter()
            for f in files:
                fastlock.remove(f)
            t2 = time.perf_counter()
            runs.append({"apply_s": round(t1 - t0, 3), "remove_s": round(t2 - t1, 3), "total_s": round(t2 - t0, 3)})
        locked_check = []
        for f in files:
            fastlock.apply(mech, f)
        locked_check = all(lockprobe.is_locked(f) for f in files)
        for f in files:
            fastlock.remove(f)
        res["timing"][mech] = {"files": len(files), "runs": runs, "max_total_s": max(r["total_s"] for r in runs),
                               "all_locked_when_applied": locked_check,
                               "clean_after": all(not lockprobe.is_locked(f) for f in files)}
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
