"""Plan 6 Task 3 Step 3 (C5, R10): replay Vortex's own file operations on the scratch bed.

    python vortex_ops.py <M-A|M-B|M-C|M-D> --out r.json

What is replayed is what the installed bundle does (READ, `resources/app.asar`,
LinkingActivator + util/fs): deploy = if the target exists and is not already our link,
`fs.rename(target, target + ".vortex_backup")`, then `fs.link(staging, target)`; purge =
`fs.unlink(target)` then `fs.rename(target + ".vortex_backup", target)`. The ops run through
NODE's fs (as Vortex does -- libuv's unlink differs from Python's on read-only files), one
child process per op. Vortex itself is never pointed at the bed (R10).
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
STAGING = ROOT + "/vortex-staging/ModA"
TARGETS = ["Skyrim.esm", "Update.esm", "SkyrimVR.esm", "Dawnguard.esm", "Skyrim_VR - Main.bsa",
           "Skyrim - Voices_ru0.bsa", "Skyrim - Interface.bsa", "Skyrim - Shaders.bsa"]
BAK = ".vortex_backup"


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(4 << 20), b""):
            h.update(b)
    return h.hexdigest()


def node(op, *args):
    for a in args:
        sandbox.assert_inside(a)
    code = "const fs=require('fs');const a=JSON.parse(process.argv[1]);fs[%s](...a);console.log('OK')" % json.dumps(op)
    r = subprocess.run(["node", "-e", code, json.dumps(list(args) + (["file"] if op == "symlinkSync" else []))],
                       capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    return {"op": op, "args": [os.path.basename(x) for x in args], "ok": r.returncode == 0 and out.endswith("OK"),
            "err": "" if r.returncode == 0 else out.splitlines()[-1][:200] if out else "rc=%d" % r.returncode}


def st(p):
    if not os.path.exists(p):
        return None
    s = os.stat(p)
    l = lockprobe.state(p)
    return {"nlink": s.st_nlink, "readonly": l["readonly"], "deny": bool(l["deny_aces"])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mech")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.environ.setdefault(sandbox.ENV, ROOT)
    if os.path.exists(STAGING):
        sandbox.recycle(STAGING)
    os.makedirs(STAGING)
    for n, size in (("NewFile.esp", 4096), ("NewFile2.esp", 4096), ("Skyrim - Shaders.bsa", 65536)):
        with open(os.path.join(STAGING, n), "wb") as fh:
            fh.write(os.urandom(size))
    shader = DATA + "/Skyrim - Shaders.bsa"
    orig = sha(shader)
    for t in TARGETS:
        lockprobe.apply(a.mech, DATA + "/" + t)
    res = {"mech": a.mech, "steps": []}
    S = res["steps"]
    # (a) hardlink deploy of a NEW name
    S.append(dict(step="a deploy new name", **node("linkSync", STAGING + "/NewFile.esp", DATA + "/NewFile.esp")))
    S[-1]["after"] = st(DATA + "/NewFile.esp")
    # (b) purge it
    S.append(dict(step="b purge new name", **node("unlinkSync", DATA + "/NewFile.esp")))
    S[-1]["after"] = st(DATA + "/NewFile.esp")
    S[-1]["staging_intact"] = os.path.exists(STAGING + "/NewFile.esp")
    # (c) game-file collision on the LOCKED vanilla Skyrim - Shaders.bsa
    S.append(dict(step="c1 backup vanilla (rename to .vortex_backup)", **node("renameSync", shader, shader + BAK)))
    S[-1]["backup_state"] = st(shader + BAK)
    S.append(dict(step="c2 link mod copy over vanilla name", **node("linkSync", STAGING + "/Skyrim - Shaders.bsa", shader)))
    S[-1]["after"] = st(shader)
    try:
        sandbox.assert_lockable(shader)
        S[-1]["lockable_now"] = True
    except sandbox.SandboxViolation as e:  # expected: multi-link -> report, never relock
        S[-1]["lockable_now"] = "refused: %s" % str(e)[:80]
    S.append(dict(step="c3 purge: unlink the mod link", **node("unlinkSync", shader)))
    S.append(dict(step="c4 purge: restore backup (rename back)", **node("renameSync", shader + BAK, shader)))
    S[-1]["after"] = st(shader)
    S[-1]["restored_identical"] = os.path.exists(shader) and sha(shader) == orig
    S[-1]["staging_intact"] = os.path.exists(STAGING + "/Skyrim - Shaders.bsa")
    # (d) symlink deploy (needs Developer Mode or the privilege)
    d = node("symlinkSync", STAGING + "/NewFile2.esp", DATA + "/NewFile2.esp")
    S.append(dict(step="d symlink deploy", **d))
    if d["ok"]:
        S.append(dict(step="d purge symlink", **node("unlinkSync", DATA + "/NewFile2.esp")))
    else:
        S[-1]["verdict"] = "UNMEASURED (symlink not permitted)" if "EPERM" in d["err"] else "FAILED"
    res["all_ok"] = all(s["ok"] for s in S if not s["step"].startswith("d "))
    res["shader_locked_after"] = lockprobe.is_locked(shader) if os.path.exists(shader) else None
    stuck = []
    for n in os.listdir(DATA):
        p = os.path.join(DATA, n)
        if os.path.isfile(p) and lockprobe.is_locked(p) and not lockprobe.remove(p)["clean"]:
            stuck.append(p)
    res["stuck"] = stuck
    res["leftovers"] = [n for n in os.listdir(DATA) if n.endswith(BAK) or n.startswith("NewFile")]
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps(res))


if __name__ == "__main__":
    main()
