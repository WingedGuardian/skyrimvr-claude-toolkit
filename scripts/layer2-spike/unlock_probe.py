"""Plan 6 Task 3 Step 5: unlock rows U1-U5 and timing C10, per mechanism.

    python unlock_probe.py <M-A|M-B|M-C|M-D> --out r.json

U1  lockprobe remove (the spike's `lock unlock`), state re-read.
U2  Python ABSENT: unlock_nopy.cmd under PATH=System32 only (proved: `where python` fails there).
U3  manifest gone: a filesystem-only `--scan` must remove only the toolkit's marks; a target the user
    had made read-only BEFORE the lock must stay read-only (R5). For ACE mechanisms the spike tags a
    pre-existing-RO file by a whitelisted mask VARIANT (M-C (WD,AD,WEA); M-D (WD,AD,WA)); M-A has
    no channel to carry the tag -- that is part of what is measured.
U4  0 residual deny ACEs, read with PowerShell Get-Acl (a reader independent of icacls).
U5  the same unelevated user (Administrators deny-only, Medium integrity).
C10 apply + remove over the FULL R1 VR name list (26 stand-ins), 5 runs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sandbox  # noqa: E402
import lockprobe  # noqa: E402

ROOT = "C:/Temp/skyguard-ml2"
DATA = ROOT + "/game/Data"
TARGETS = ["Skyrim.esm", "Update.esm", "SkyrimVR.esm", "Dawnguard.esm", "Skyrim_VR - Main.bsa",
           "Skyrim - Voices_ru0.bsa", "Skyrim - Interface.bsa", "Skyrim - Shaders.bsa"]
PRE_RO = "Skyrim - Interface.bsa"
R1_VR = (["Skyrim.esm", "Update.esm", "Dawnguard.esm", "HearthFires.esm", "Dragonborn.esm", "SkyrimVR.esm",
          "Skyrim_VR - Main.bsa"]
         + ["Skyrim - %s.bsa" % n for n in ("Animations", "Interface", "Meshes0", "Meshes1", "Misc", "Patch",
                                            "Shaders", "Sounds")]
         + ["Skyrim - Textures%d.bsa" % i for i in range(9)]
         + ["Skyrim - Voices_en0.bsa", "Skyrim - Voices_ru0.bsa"])
VARIANT = {"M-C": ("WD", "AD", "WEA"), "M-D": ("WD", "AD", "WA")}
STD = {"M-B": {"WD", "AD"}, "M-C": {"WD", "AD"}, "M-D": {"WD", "AD", "WA", "WEA"}}


def P(n):
    return DATA + "/" + n


def deny_masks(path):
    out = []
    for ln in lockprobe.state(path)["deny_aces"]:
        m = re.search(r"\(DENY\)\(([^)]*)\)", ln)
        if m:
            out.append(set(m.group(1).split(",")))
    return out


def get_acl_denies(paths):
    ps = ";".join("(Get-Acl -LiteralPath '%s').Access | ? { $_.AccessControlType -eq 'Deny' } | %% { '%s|' + $_.IdentityReference }"
                  % (os.path.normpath(p), os.path.basename(p)) for p in paths)
    r = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True)
    return [ln for ln in r.stdout.splitlines() if ln.strip()], r.returncode


def clean(paths):
    return all(not lockprobe.is_locked(p) for p in paths)


def lock_all(mech, paths, tag_pre_ro=False):
    for p in paths:
        pre = lockprobe.state(p)["readonly"]
        if tag_pre_ro and pre and mech in VARIANT:
            sandbox.assert_lockable(p)
            m = lockprobe.check_mask(VARIANT[mech])
            subprocess.run(["icacls", os.path.normpath(p), "/deny", "*%s:(%s)" % (lockprobe.user_sid(), m)],
                           capture_output=True)
        else:
            lockprobe.apply(mech, p)


def scan_unlock(mech, paths):
    """Manifest-less unlock: only what the filesystem itself says."""
    report = []
    for p in paths:
        sandbox.assert_inside(p)
        masks = deny_masks(p)
        ro = lockprobe.state(p)["readonly"]
        if mech == "M-A":
            report.append((os.path.basename(p), "cannot attribute read-only bit; left" if ro else "no mark"))
            continue
        if not masks:
            report.append((os.path.basename(p), "no toolkit ACE; left as is"))
            continue
        variant = any(m == set(VARIANT.get(mech, ())) for m in masks)
        subprocess.run(["icacls", os.path.normpath(p), "/remove:d", "*" + lockprobe.user_sid()], capture_output=True)
        if mech in ("M-C", "M-D") and not variant:
            subprocess.run(["attrib", "-R", os.path.normpath(p)], capture_output=True)
        report.append((os.path.basename(p), "ACE removed" + ("; bit kept (pre-existing tag)" if variant else
                                                             ("; bit cleared" if mech != "M-B" else ""))))
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mech")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.environ.setdefault(sandbox.ENV, ROOT)
    mech = a.mech
    paths = [P(t) for t in TARGETS]
    res = {"mech": mech}
    assert clean(paths), "bed not clean at start"
    # U1
    lock_all(mech, paths)
    locked = all(lockprobe.is_locked(p) for p in paths)
    for p in paths:
        lockprobe.remove(p)
    res["U1"] = {"pass": locked and clean(paths), "locked_first": locked}
    # U2 -- no Python on PATH
    lock_all(mech, paths)
    env = {"PATH": r"C:\Windows\System32;C:\Windows", "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
           "SYSTEMDRIVE": os.environ.get("SYSTEMDRIVE", "C:"), "COMSPEC": r"C:\Windows\System32\cmd.exe"}
    where = subprocess.run("cmd.exe /d /c where python", capture_output=True, text=True, env=env)
    # The script path is NOT quoted: with /c, a command line that STARTS with a quote and holds
    # more than two has its first and last quote stripped (MEASURED: rc 1, no output).
    script = os.path.join(HERE, "unlock_nopy.cmd")
    assert " " not in script, "unquoted script path must not contain spaces"
    line = 'cmd.exe /d /c %s %s' % (script,
                                      " ".join('"%s"' % os.path.normpath(p) for p in paths))
    for p in paths:
        sandbox.assert_inside(p)
    r = subprocess.run(line, capture_output=True, text=True, env=env)
    res["U2"] = {"pass": r.returncode == 0 and clean(paths), "python_resolvable": where.returncode == 0,
                 "rc": r.returncode, "out_tail": r.stdout[-300:]}
    if not clean(paths):
        for p in paths:
            lockprobe.remove(p)
    # U4 (after U2) -- independent reader
    denies, prc = get_acl_denies(paths)
    res["U4"] = {"pass": prc == 0 and not denies, "deny_entries": denies}
    # U3 -- manifest gone, one target read-only BEFORE the lock
    pre = P(PRE_RO)
    subprocess.run(["attrib", "+R", os.path.normpath(sandbox.assert_inside(pre))], capture_output=True)
    lock_all(mech, paths, tag_pre_ro=True)
    report = scan_unlock(mech, paths)
    others = [p for p in paths if p != pre]
    pre_state = lockprobe.state(pre)
    res["U3"] = {"pass": clean(others) and pre_state["readonly"] and not pre_state["deny_aces"],
                 "toolkit_files_unlocked": clean(others), "pre_ro_still_ro": pre_state["readonly"],
                 "pre_ro_deny_left": bool(pre_state["deny_aces"]), "report": report}
    for p in paths:  # bed hygiene, outside the measurement
        lockprobe.remove(p)
    res["bed_clean_after"] = clean(paths)
    # U5
    g = subprocess.run(["whoami", "/groups", "/fo", "csv", "/nh"], capture_output=True, text=True).stdout
    adm = [ln for ln in g.splitlines() if "S-1-5-32-544" in ln]
    res["U5"] = {"pass": bool(adm) and "deny only" in adm[0].lower() and "S-1-16-8192" in g,
                 "administrators": adm, "medium_integrity": "S-1-16-8192" in g}
    # C10 -- full R1 VR set, stand-ins
    c10 = ROOT + "/c10/" + mech
    os.makedirs(c10, exist_ok=True)
    stand = []
    for n in R1_VR:
        f = c10 + "/" + n
        if not os.path.exists(f):
            with open(f, "wb") as fh:
                fh.write(b"\0" * 65536)
        stand.append(f)
    ro, mask = lockprobe.MECHANISMS[mech]
    sid = lockprobe.user_sid()
    runs = []
    for _ in range(5):
        for f in stand:
            sandbox.assert_lockable(f)
        t0 = time.perf_counter()
        for f in stand:
            w = os.path.normpath(f)
            if ro:
                subprocess.run(["attrib", "+R", w], capture_output=True)
            if mask:
                subprocess.run(["icacls", w, "/deny", "*%s:(%s)" % (sid, lockprobe.check_mask(mask))],
                               capture_output=True)
        t1 = time.perf_counter()
        for f in stand:
            w = os.path.normpath(f)
            if mask:
                subprocess.run(["icacls", w, "/remove:d", "*" + sid], capture_output=True)
            if ro:
                subprocess.run(["attrib", "-R", w], capture_output=True)
        t2 = time.perf_counter()
        runs.append({"apply_s": round(t1 - t0, 3), "remove_s": round(t2 - t1, 3), "total_s": round(t2 - t0, 3),
                     "all_locked_mid": None})
    res["C10"] = {"files": len(stand), "runs": runs, "max_total_s": max(r["total_s"] for r in runs),
                  "pass": max(r["total_s"] for r in runs) <= 2.0, "clean_after": clean(stand)}
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps({k: ({kk: vv for kk, vv in v.items() if kk not in ("runs", "report", "out_tail")}
                          if isinstance(v, dict) else v) for k, v in res.items()}))


if __name__ == "__main__":
    main()
