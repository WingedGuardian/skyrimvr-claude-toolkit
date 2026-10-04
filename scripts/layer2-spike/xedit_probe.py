"""Plan 6 Task 3 Step 2 (C7): headless xEdit QuickAutoClean against the scratch bed.

    python xedit_probe.py <plugin> [--timeout 300]

Every path xEdit could write to is pointed into C:/Temp/skyguard-ml2/xe/ (-D data, -P plugins.txt,
-I ini, -B backups, -R log, -C cache, -T temp, -O output), and the exe itself is a COPY in
C:/Temp/skyguard-ml2/tools/xedit/. Run a canary on a scratch-only plugin (Spike.esp) first: if the
-D path were ignored, xEdit would not find it in the real install, and would clean nothing there.
Prints the log tail, the data-path line from the log, and the before/after hash of the plugin.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sandbox  # noqa: E402

ROOT = "C:\\Temp\\skyguard-ml2"
# 32-bit on purpose: the 64-bit exe opens a modal "You have started the 64bit version ... continue?"
# dialog that -autoexit does not answer (MEASURED: screenshot in xe/log/stuck-*.png).
EXE = ROOT + "\\tools\\xedit\\xTESEdit.exe"
XE = ROOT + "\\xe"


def sha(p):
    try:
        return hashlib.sha256(open(p, "rb").read()).hexdigest()
    except OSError as e:
        return "ERR " + e.__class__.__name__


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plugin")
    ap.add_argument("--timeout", type=int, default=300)
    a = ap.parse_args()
    os.environ.setdefault(sandbox.ENV, "C:/Temp/skyguard-ml2")
    target = ROOT + "\\game\\Data\\" + a.plugin
    sandbox.assert_inside(target)
    sandbox.assert_inside(EXE)
    log = XE + "\\log\\xedit-%s-%d.log" % (a.plugin.replace(".", "_"), int(time.time()))
    args = [EXE, "-tes5vr", "-D:" + ROOT + "\\game\\Data\\", "-P:" + XE + "\\plugins.txt",
            "-I:" + XE + "\\mygames\\SkyrimVR.ini", "-B:" + XE + "\\backup\\", "-R:" + log,
            "-C:" + XE + "\\cache\\", "-T:" + XE + "\\temp\\", "-O:" + XE + "\\out\\",
            "-qac", "-autoexit", "-autoload", a.plugin]
    before = sha(target)
    data_before = set(os.listdir(ROOT + "\\game\\Data"))
    t = time.time()
    p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shot = None
    try:
        rc = p.wait(timeout=a.timeout)
    except subprocess.TimeoutExpired:
        rc = "TIMEOUT"
        # Evidence of what it is waiting on (a dialog), then kill THIS process only -- never by
        # image name, which would also kill a user's own xEdit.
        shot = XE + "\\log\\stuck-%d.png" % int(time.time())
        subprocess.run(["powershell", "-NoProfile", "-File", ROOT + "\\tools\\shot.ps1", "-Out", shot],
                       capture_output=True)
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
    secs = round(time.time() - t, 1)
    text = open(log, encoding="utf-8", errors="replace").read() if os.path.exists(log) else ""
    out = {"plugin": a.plugin, "rc": rc, "screenshot": shot, "secs": secs, "changed": sha(target) != before,
           "new_in_data": sorted(set(os.listdir(ROOT + "\\game\\Data")) - data_before),
           "backups": sorted(os.listdir(XE + "\\backup")),
           "log_path_lines": [ln for ln in text.splitlines() if "Data Path" in ln or "Using" in ln][:6],
           "log_tail": text.splitlines()[-25:]}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
