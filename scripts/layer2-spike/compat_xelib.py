"""Plan 6 Task 3 Step 1: drive xelib_probe.js for C1-C4 / P32, one run per mechanism.

    python compat_xelib.py <M-A|M-B|M-C|M-D> --out r.json

Locks every vanilla target in the bed, then: C1 load; C2 save own Spike.esp and cross-read it
with Spriggit (an independent parser); C3/P32 SaveFile Update.esm while locked (must fail, and
Update.esm must stay byte-identical); C4 unlock Update.esm only -> the same save succeeds ->
restore pristine bytes -> relock -> state re-read. Everything is unlocked at the end.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sandbox  # noqa: E402
import lockprobe  # noqa: E402

ROOT = "C:/Temp/skyguard-ml2"
DATA = ROOT + "/game/Data"
PRISTINE = ROOT + "/pristine"
TARGETS = ["Skyrim.esm", "Update.esm", "SkyrimVR.esm", "Dawnguard.esm", "Skyrim_VR - Main.bsa",
           "Skyrim - Voices_ru0.bsa", "Skyrim - Interface.bsa", "Skyrim - Shaders.bsa"]


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(4 << 20), b""):
            h.update(b)
    return h.hexdigest()


def xe(mode):
    r = subprocess.run(["node", os.path.join(HERE, "xelib_probe.js"), mode], capture_output=True, text=True,
                       timeout=900, cwd=ROOT)  # xelib writes xelib_log.txt into its cwd
    for ln in r.stdout.splitlines():
        if ln.startswith("RESULT: "):
            return json.loads(ln[8:])
    return {"fatal": "no RESULT line", "rc": r.returncode, "tail": (r.stdout + r.stderr)[-800:]}


def restore_update():
    p = DATA + "/Update.esm"
    sandbox.assert_inside(p)
    shutil.copyfile(PRISTINE + "/Update.esm", p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mech")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.environ.setdefault(sandbox.ENV, ROOT)
    os.makedirs(PRISTINE, exist_ok=True)
    if not os.path.exists(PRISTINE + "/Update.esm"):
        shutil.copyfile(DATA + "/Update.esm", PRISTINE + "/Update.esm")
    if os.path.exists(DATA + "/Spike.esp"):
        lockprobe.remove(DATA + "/Spike.esp")
        os.remove(DATA + "/Spike.esp")
    if sha(DATA + "/Update.esm") != sha(PRISTINE + "/Update.esm"):
        restore_update()
    before = {t: sha(DATA + "/" + t) for t in TARGETS}
    for t in TARGETS:
        lockprobe.apply(a.mech, DATA + "/" + t)
    res = {"mech": a.mech}
    # C1
    c1 = xe("load")
    res["C1"] = {"pass": len(c1.get("loaded", [])) == 4 and all(isinstance(n, int) for _, n in c1["loaded"]),
                 "raw": c1}
    # C2
    c2 = xe("spike")
    spr = ROOT + "/spr/" + a.mech
    if os.path.exists(spr):
        shutil.rmtree(spr)
    sp = subprocess.run(["spriggit", "serialize", "--InputPath", DATA + "/Spike.esp", "--OutputPath", spr,
                         "--GameRelease", "SkyrimSE", "--PackageName", "Spriggit.Yaml", "--PackageVersion",
                         "0.40.0"], capture_output=True, text=True, timeout=600)
    yamls = []
    for dp, _, fs in os.walk(spr) if os.path.exists(spr) else []:
        yamls += [os.path.join(dp, f) for f in fs if f.endswith(".yaml")]
    glob_seen = any("skyguard-spike" in open(y, encoding="utf-8", errors="replace").read() for y in yamls)
    res["C2"] = {"pass": c2.get("saved") == "Spike.esp" and os.path.exists(DATA + "/Spike.esp") and glob_seen,
                 "spriggit_rc": sp.returncode, "spriggit_yaml_files": len(yamls), "spriggit_sees_record": glob_seen,
                 "raw": c2}
    # C3 / P32
    c3 = xe("modify-update")
    after3 = sha(DATA + "/Update.esm")
    res["C3"] = {"pass_means_refused": after3 == before["Update.esm"], "raw": c3,
                 "update_identical": after3 == before["Update.esm"]}
    # C4: unlock one command -> save succeeds -> restore -> relock
    rm = lockprobe.remove(DATA + "/Update.esm")
    c4 = xe("modify-update")
    after4 = sha(DATA + "/Update.esm")
    saved4 = after4 != before["Update.esm"]
    restore_update()
    restored = sha(DATA + "/Update.esm") == before["Update.esm"]
    lockprobe.apply(a.mech, DATA + "/Update.esm")
    relocked = lockprobe.is_locked(DATA + "/Update.esm")
    res["C4"] = {"pass": rm["clean"] and saved4 and restored and relocked, "unlock_clean": rm["clean"],
                 "save_after_unlock_changed_file": saved4, "pristine_restored": restored, "relocked": relocked,
                 "raw": c4}
    # Other masters untouched throughout
    res["other_masters_identical"] = all(sha(DATA + "/" + t) == before[t] for t in TARGETS)
    # xEdit renames the original into "zEdit Backups/<name>.<stamp>.bak" -- the LOCKED object
    # moves there with its bit/ACE (MEASURED). Sweep the whole Data tree, not just the targets.
    stuck = []
    res["zedit_backups"] = []
    for dp, _, fs in os.walk(DATA):
        for f in fs:
            p = os.path.join(dp, f)
            if dp != DATA:
                res["zedit_backups"].append([os.path.relpath(p, DATA), lockprobe.state(p)["readonly"],
                                             bool(lockprobe.state(p)["deny_aces"])])
            if lockprobe.is_locked(p) and not lockprobe.remove(p)["clean"]:
                stuck.append(p)
    bk = DATA + "/zEdit Backups"
    if os.path.isdir(bk) and not stuck:
        sandbox.assert_inside(bk)
        shutil.rmtree(bk)
    res["stuck"] = stuck
    res["data_listing"] = sorted(os.listdir(DATA))
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps({k: (v if not isinstance(v, dict) else {kk: vv for kk, vv in v.items() if kk != "raw"})
                      for k, v in res.items() if k != "data_listing"}))


if __name__ == "__main__":
    main()
