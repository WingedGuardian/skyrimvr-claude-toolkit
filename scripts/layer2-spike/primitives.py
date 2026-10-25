"""Plan 6 Task 1: run every write/delete/read primitive against a LOCKED decoy and
its UNLOCKED control twin, for each mechanism M-A..M-D.

    python primitives.py [--mech M-A,M-B,...] [--rows P1,P9,...] --out results.json

Each row gets a fresh pair under C:/Temp/skyguard-ml2/probe/<mech>/<row>/:
target.esm (locked) and control.esm (unlocked), 64 KiB random each, plus src.bin
(the source for cp/mv rows). The primitive runs as a CHILD PROCESS, once against
each. A row whose control did not change is UNMEASURED, never "blocked".
Git Bash is called by absolute path (L3: `bash` on PATH may be the WSL stub).
Afterwards every file in the row dir is unlocked, verified clean, and deleted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sandbox  # noqa: E402
import lockprobe  # noqa: E402

ROOT = "C:/Temp/skyguard-ml2"
PROBE = ROOT + "/probe"
GITBASH = "C:/Program Files/Git/bin/bash.exe"
PWSH7 = "C:/Program Files/PowerShell/7/pwsh.exe"
PS51 = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
PY = sys.executable
NODE = shutil.which("node") or "node"


def W(p):
    return os.path.normpath(p)


def bash(script):
    return [GITBASH, "-c", script]


def ps(exe, script):
    return [exe, "-NoProfile", "-NonInteractive", "-Command", script]


def py(code):
    return [PY, "-c", code]


def cmd(line):
    # A STRING, not a list: list2cmdline escapes the inner quotes as \" and cmd then
    # sees a mangled path (MEASURED: the first run had every cmd control fail).
    return "cmd.exe /d /c " + line


def node(code):
    return [NODE, "-e", code]


# kind: write (target must stay byte-identical), delete (target must still exist),
#       read (target must read back correctly), meta (mtime must not change), stat.
def rows():
    r = []
    add = lambda rid, kind, fn: r.append((rid, kind, fn))
    # --- Git Bash (absolute path) ---
    add("P1", "write", lambda f, s: bash("printf x > '%s'" % f))
    add("P2", "write", lambda f, s: bash("printf x >> '%s'" % f))
    add("P3", "write", lambda f, s: bash("cp '%s' '%s'" % (s, f)))
    add("P4", "write", lambda f, s: bash("cp -f '%s' '%s'" % (s, f)))
    add("P5", "write", lambda f, s: bash("printf x | tee '%s' >/dev/null" % f))
    add("P6", "write", lambda f, s: bash("printf xxxx | dd of='%s' conv=notrunc status=none" % f))
    add("P7", "write", lambda f, s: bash("sed -i '1s/^/X/' '%s'" % f))
    add("P8", "write", lambda f, s: bash("mv '%s' '%s'" % (s, f)))
    # --- PowerShell 7 and 5.1 ---
    for tag, exe in (("pwsh7", PWSH7), ("ps51", PS51)):
        q = lambda p: "'" + W(p) + "'"
        add("P9." + tag, "write", lambda f, s, e=exe, q=q: ps(e, "Set-Content -LiteralPath %s -Value x" % q(f)))
        add("P10." + tag, "write", lambda f, s, e=exe, q=q: ps(e, "Set-Content -LiteralPath %s -Value x -Force" % q(f)))
        add("P11." + tag, "write", lambda f, s, e=exe, q=q: ps(e, "'x' | Out-File -LiteralPath %s" % q(f)))
        add("P12." + tag, "write", lambda f, s, e=exe, q=q: ps(e, "'x' | Out-File -LiteralPath %s -Force" % q(f)))
        add("P13." + tag, "write", lambda f, s, e=exe, q=q: ps(e, "Add-Content -LiteralPath %s -Value x" % q(f)))
        add("P14." + tag, "write", lambda f, s, e=exe, q=q: ps(e, "Copy-Item -LiteralPath %s -Destination %s" % (q(s), q(f))))
        add("P15." + tag, "write", lambda f, s, e=exe, q=q: ps(e, "Copy-Item -LiteralPath %s -Destination %s -Force" % (q(s), q(f))))
        add("P16." + tag, "write", lambda f, s, e=exe, q=q: ps(e, "Move-Item -LiteralPath %s -Destination %s -Force" % (q(s), q(f))))
        add("P17." + tag, "write", lambda f, s, e=exe, q=q: ps(e, "[IO.File]::WriteAllBytes(%s, [byte[]](1,2,3))" % q(f)))
        add("D2." + tag, "delete", lambda f, s, e=exe, q=q: ps(e, "Remove-Item -LiteralPath %s -Force" % q(f)))
        add("D3." + tag, "delete", lambda f, s, e=exe, q=q: ps(e, "Remove-Item -LiteralPath %s" % q(f)))
        add("R2." + tag, "read", lambda f, s, e=exe, q=q, t=tag: ps(
            e, ("$b = Get-Content -LiteralPath %s -AsByteStream -Raw; $b.Length" if t == "pwsh7"
                else "(Get-Content -LiteralPath %s -Encoding Byte -ReadCount 0).Length") % q(f)))
    # --- Python ---
    add("P19", "write", lambda f, s: py("open(%r,'wb').write(b'x')" % f))
    add("P20", "write", lambda f, s: py("h=open(%r,'r+b');h.write(b'xxxx');h.close()" % f))
    add("P21", "write", lambda f, s: py("open(%r,'ab').write(b'x')" % f))
    add("P22", "write", lambda f, s: py("import shutil;shutil.copyfile(%r,%r)" % (s, f)))
    add("P23", "write", lambda f, s: py("import shutil;shutil.copy2(%r,%r)" % (s, f)))
    add("P24", "write", lambda f, s: py("import os;os.replace(%r,%r)" % (s, f)))
    add("P25", "write", lambda f, s: py("import pathlib;pathlib.Path(%r).write_bytes(b'x')" % f))
    add("P26", "write", lambda f, s: py("import os;os.truncate(%r,10)" % f))
    # --- cmd ---
    add("P27", "write", lambda f, s: cmd('copy /y "%s" "%s"' % (W(s), W(f))))
    add("P28", "write", lambda f, s: cmd('echo x> "%s"' % W(f)))
    # --- Node ---
    add("P33a", "write", lambda f, s: node("require('fs').writeFileSync(%s,'x')" % json.dumps(f)))
    add("P33b", "write", lambda f, s: node("require('fs').renameSync(%s,%s)" % (json.dumps(s), json.dumps(f))))
    # --- Delete (recorded, never decisive) ---
    add("D1", "delete", lambda f, s: bash("rm -f '%s'" % f))
    add("D4", "delete", lambda f, s: py("import os;os.remove(%r)" % f))
    add("D5", "delete", lambda f, s: cmd('del /f "%s"' % W(f)))
    add("D7", "delete", lambda f, s: bash("mv '%s' '%s.bak'" % (f, f)))
    # --- Metadata ---
    add("T1", "meta", lambda f, s: py("import os;os.utime(%r,(1000000000,1000000000))" % f))
    add("T2", "stat", lambda f, s: py("import os;st=os.stat(%r);print(st.st_file_attributes)" % f))
    # --- Reads ---
    add("R1", "read", lambda f, s: py("print(len(open(%r,'rb').read()))" % f))
    add("R3", "read", lambda f, s: py(
        "import mmap;h=open(%r,'rb');m=mmap.mmap(h.fileno(),0,access=mmap.ACCESS_READ);print(len(m[:1048576]))" % f))
    return r


SIZE = {"read": 2 * 1024 * 1024}


def sha(p):
    try:
        with open(p, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError as e:
        return "ERR:%s" % e.__class__.__name__


def mk(path, n):
    sandbox.assert_inside(path)
    with open(path, "wb") as fh:
        fh.write(os.urandom(n))


def snapshot(d):
    return sorted(os.listdir(d))


def one(kind, fn, f, s, d):
    pre_hash = sha(f)
    pre_mtime = os.stat(f).st_mtime_ns
    pre = set(snapshot(d))
    t = time.time()
    try:
        r = subprocess.run(fn(f, s), capture_output=True, text=True, timeout=90)
        rc, out = r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        rc, out = "TIMEOUT", ""
    dt = time.time() - t
    exists = os.path.exists(f)
    post_hash = sha(f) if exists else None
    res = {"rc": rc, "out": out[-300:], "secs": round(dt, 2), "exists": exists,
           "changed": (not exists) or post_hash != pre_hash,
           "debris": sorted(set(snapshot(d)) - pre)}
    if kind == "meta" and exists:
        res["mtime_changed"] = os.stat(f).st_mtime_ns != pre_mtime
    if kind in ("read", "stat"):
        res["stdout"] = out[:80]
    if exists:
        try:
            st = lockprobe.state(f)
            res["after_readonly"], res["after_deny"] = st["readonly"], bool(st["deny_aces"])
        except OSError:
            pass
    return res


def classify(kind, tgt, ctl, expect_len):
    if kind == "write":
        if not ctl["changed"]:
            return "UNMEASURED (control failed)"
        return "NOT BLOCKED" if tgt["changed"] else "BLOCKED"
    if kind == "delete":
        if ctl["exists"]:
            return "UNMEASURED (control failed)"
        return "BLOCKED" if tgt["exists"] else "NOT BLOCKED"
    if kind == "meta":
        if not ctl.get("mtime_changed"):
            return "UNMEASURED (control failed)"
        return "NOT BLOCKED" if tgt.get("mtime_changed") else "BLOCKED"
    if kind == "read":
        ok = lambda x: x["rc"] == 0 and str(expect_len) in x.get("stdout", "")
        if not ok(ctl):
            return "UNMEASURED (control failed)"
        return "READ OK" if ok(tgt) and not tgt["changed"] else "READ FAILED"
    if kind == "stat":
        return "READ OK" if tgt["rc"] == 0 else "READ FAILED"
    return "?"


def cleanup(d):
    for name in os.listdir(d):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            res = lockprobe.remove(p)
            if not res["clean"]:
                raise SystemExit("STUCK: could not unlock %s: %s" % (p, res))
    sandbox.recycle(d)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mech", default="M-A,M-B,M-C,M-D")
    ap.add_argument("--rows", default="")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.environ.setdefault(sandbox.ENV, ROOT)
    want = set(a.rows.split(",")) if a.rows else None
    results = []
    for mech in a.mech.split(","):
        for rid, kind, fn in rows():
            if want and rid not in want and rid.split(".")[0] not in want:
                continue
            d = "%s/%s/%s" % (PROBE, mech, rid)
            if os.path.exists(d):
                cleanup(d)
            os.makedirs(d)
            n = SIZE.get(kind, 64 * 1024)
            f, c = d + "/target.esm", d + "/control.esm"
            mk(f, n), mk(c, n)
            s_t, s_c = d + "/src_t.bin", d + "/src_c.bin"
            mk(s_t, 4096), mk(s_c, 4096)
            lock = lockprobe.apply(mech, f)
            assert lock["after"]["readonly"] or lock["after"]["deny_aces"], "lock did not apply"
            assert not lockprobe.is_locked(c)
            tgt = one(kind, fn, f, s_t, d)
            ctl = one(kind, fn, c, s_c, d)
            verdict = classify(kind, tgt, ctl, n if kind != "read" or "R3" not in rid else 1048576)
            if rid.startswith("R3"):
                verdict = classify(kind, tgt, ctl, 1048576)
            results.append({"mech": mech, "row": rid, "kind": kind, "verdict": verdict,
                            "target": tgt, "control": ctl})
            print("%-4s %-10s %-7s %-28s rc=%s/%s %s" % (mech, rid, kind, verdict, tgt["rc"], ctl["rc"],
                                                       tgt["out"][:70].replace("\n", " ")), flush=True)
            cleanup(d)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=1)
    print("wrote", a.out, len(results), "cells")


if __name__ == "__main__":
    main()
