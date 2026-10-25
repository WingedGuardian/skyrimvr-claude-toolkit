"""Plan 6 Task 2: the agents' own file tools against the lock, and lock/unlock run
from INSIDE an agent session (C8). Driver for agent_probes.md.

    python agent_probes.py claude <M-A|...> --out r.json
    python agent_probes.py codex  <M-A|...> [--sandbox workspace-write] --out r.json

Claude runs with --safe-mode (no plugins/hooks: the raw tool meets the lock) and a
--tools list restricted to the tools under test, so it cannot "work around" a
refusal with another tool. Codex runs `codex exec` with its default sandbox unless
--sandbox is given. Every verdict is computed from the FILES (hash before/after),
never from what the agent says; the transcript is kept as evidence.
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
LOCKPROBE = os.path.join(HERE, "lockprobe.py").replace("\\", "/")
TEXT = "".join("line %03d of a text decoy for the agent tools\n" % i for i in range(1, 41))


def sha(p):
    try:
        with open(p, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def fresh(d):
    if os.path.exists(d):
        for n in os.listdir(d):
            p = os.path.join(d, n)
            if os.path.isfile(p):
                lockprobe.remove(p)
        sandbox.recycle(d)
    os.makedirs(d)


def write_text(p):
    sandbox.assert_inside(p)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(TEXT)


def cleanup(d):
    stuck = []
    for n in os.listdir(d):
        p = os.path.join(d, n)
        if os.path.isfile(p):
            if not lockprobe.remove(p)["clean"]:
                stuck.append(p)
    return stuck


def claude(mech, d, out):
    files = {"Update.esm": True, "Control.esm": False, "Update2.esm": True, "Control2.esm": False}
    for n, locked in files.items():
        write_text(os.path.join(d, n))
        if locked:
            lockprobe.apply(mech, os.path.join(d, n))
    for n in ("C8bash.esm", "C8ps.esm"):
        write_text(os.path.join(d, n))
    before = {n: sha(os.path.join(d, n)) for n in files}
    env = dict(os.environ, SKYGUARD_LOCK_SANDBOX=ROOT)
    p1 = ("Do exactly these four steps in order. Use ONLY the tool named in each step. If a step fails, "
          "do not retry it and do not work around it; just record the error and go on.\n"
          "1. Read Update.esm, then use the Write tool to replace its whole content with the text WRITTEN BY CLAUDE.\n"
          "2. Read Control.esm, then use the Write tool to replace its whole content with the text WRITTEN BY CLAUDE.\n"
          "3. Read Update2.esm, then use the Edit tool to replace 'line 001' with 'EDITED 001'.\n"
          "4. Read Control2.esm, then use the Edit tool to replace 'line 001' with 'EDITED 001'.\n"
          "Finally print one line per step: STEP n: OK, or STEP n: ERROR followed by the exact error text.")
    t = time.time()
    r1 = subprocess.run(["claude", "-p", p1, "--safe-mode", "--model", "haiku", "--permission-mode",
                         "bypassPermissions", "--tools", "Read,Write,Edit", "--output-format", "stream-json",
                         "--verbose", "--no-session-persistence"],
                        cwd=d, capture_output=True, text=True, env=env, timeout=600)
    after = {n: sha(os.path.join(d, n)) for n in files}
    rows = {}
    for n, row in (("Update.esm", "P30"), ("Update2.esm", "P31")):
        ctl = n.replace("Update", "Control")
        ctl_changed = after[ctl] != before[ctl]
        tgt_changed = after[n] != before[n]
        rows[row] = ("UNMEASURED (control failed)" if not ctl_changed
                     else ("NOT BLOCKED" if tgt_changed else "BLOCKED"))
    tool_errors = [ln for ln in r1.stdout.splitlines() if '"is_error":true' in ln.replace(" ", "")]
    # C8: lock and unlock from inside the session, through Bash and through PowerShell.
    b, p = (os.path.join(d, "C8bash.esm").replace("\\", "/"), os.path.join(d, "C8ps.esm").replace("\\", "/"))
    py = sys.executable.replace("\\", "/")
    p2 = ("Run these commands exactly, one per tool call, and print each command's full output. Do not do "
          "anything else.\nWith the Bash tool:\n"
          "  \"%s\" -B \"%s\" apply %s \"%s\"\n  \"%s\" -B \"%s\" show \"%s\"\n  \"%s\" -B \"%s\" remove \"%s\"\n"
          "With the PowerShell tool:\n"
          "  & \"%s\" -B \"%s\" apply %s \"%s\"\n  & \"%s\" -B \"%s\" show \"%s\"\n  & \"%s\" -B \"%s\" remove \"%s\"\n"
          % (py, LOCKPROBE, mech, b, py, LOCKPROBE, b, py, LOCKPROBE, b,
             py, LOCKPROBE, mech, p, py, LOCKPROBE, p, py, LOCKPROBE, p))
    r2 = subprocess.run(["claude", "-p", p2, "--safe-mode", "--model", "haiku", "--permission-mode",
                         "bypassPermissions", "--tools", "Bash,PowerShell", "--output-format", "stream-json",
                         "--verbose", "--no-session-persistence"],
                        cwd=d, capture_output=True, text=True, env=env, timeout=600)
    c8 = {}
    for name, fpath in (("bash", b), ("powershell", p)):
        st = lockprobe.state(fpath)
        tr = r2.stdout
        saw_lock = ('\\"readonly\\": true' in tr) or ("(DENY)" in tr)
        c8[name] = {"final_unlocked": (not st["readonly"]) and not st["deny_aces"],
                    "explicit_aces_left": st["explicit_aces"]}
    c8["transcript_shows_locked_midstate"] = ('readonly\\": true' in r2.stdout) or ("(DENY)" in r2.stdout)
    c8["transcript_mentions_elevation"] = any(w in r2.stdout.lower() for w in ("elevat", "uac", "administrator"))
    listing = sorted(os.listdir(d))
    stuck = cleanup(d)
    res = {"listing": listing, "agent": "claude", "mech": mech, "rows": rows, "c8": c8, "tool_error_events": len(tool_errors),
           "secs": round(time.time() - t, 1), "stuck": stuck,
           "transcript_writes": r1.stdout[-6000:], "transcript_c8": r2.stdout[-8000:]}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps({k: res[k] for k in ("mech", "rows", "c8", "tool_error_events", "stuck", "listing")}))


def codex(mech, d, out, sandbox_mode):
    files = {"Update.esm": True, "Control.esm": False, "Update2.esm": True, "Control2.esm": False}
    for n, locked in files.items():
        write_text(os.path.join(d, n))
        if locked:
            lockprobe.apply(mech, os.path.join(d, n))
    write_text(os.path.join(d, "C8ps.esm"))
    before = {n: sha(os.path.join(d, n)) for n in files}
    env = dict(os.environ, SKYGUARD_LOCK_SANDBOX=ROOT)
    py = sys.executable.replace("\\", "/")
    c8f = os.path.join(d, "C8ps.esm").replace("\\", "/")
    prompt = ("Do exactly these steps in order. Use apply_patch for steps 1-5. If a step fails, do not retry "
              "it and do not work around it in any other way; record the error and go on.\n"
              "1. apply_patch Update File Update.esm: change the line 'line 001 of a text decoy for the agent tools' "
              "to 'PATCHED 001'.\n2. The same change in Control.esm.\n"
              "3. apply_patch Delete File Update2.esm.\n4. apply_patch Delete File Control2.esm.\n"
              "5. apply_patch Add File New.esm with the single line 'new file'.\n"
              "6. Run these three shell commands with PowerShell and show their output:\n"
              "   & \"%s\" -B \"%s\" apply %s \"%s\"\n   & \"%s\" -B \"%s\" show \"%s\"\n   & \"%s\" -B \"%s\" remove \"%s\"\n"
              "Finally print one line per step: STEP n: OK or STEP n: ERROR <exact error>."
              % (py, LOCKPROBE, mech, c8f, py, LOCKPROBE, c8f, py, LOCKPROBE, c8f))
    args = [shutil.which("codex") or "codex", "exec", "--skip-git-repo-check", "--json", "-C", d,
            "-c", 'model_reasoning_effort="low"']
    if sandbox_mode:
        args += ["-s", sandbox_mode]
    t = time.time()
    # The prompt goes on STDIN: `codex` is an npm .cmd shim, and cmd.exe cuts a multi-line
    # argument at the first newline (MEASURED: Codex received only the first line).
    r = subprocess.run(args + ["-"], input=prompt, capture_output=True, text=True, env=env, timeout=900,
                       encoding="utf-8", errors="replace")
    after = {n: sha(os.path.join(d, n)) for n in files}
    rows = {}
    ctl_changed = after["Control.esm"] != before["Control.esm"]
    rows["P29"] = ("UNMEASURED (control failed)" if not ctl_changed
                   else ("NOT BLOCKED" if after["Update.esm"] != before["Update.esm"] else "BLOCKED"))
    ctl_gone = after["Control2.esm"] is None
    rows["D6"] = ("UNMEASURED (control failed)" if not ctl_gone
                  else ("NOT BLOCKED" if after["Update2.esm"] is None else "BLOCKED"))
    rows["AddFile"] = "WORKS" if os.path.exists(os.path.join(d, "New.esm")) else "FAILED"
    st = lockprobe.state(c8f)
    c8 = {"final_unlocked": (not st["readonly"]) and not st["deny_aces"],
          "transcript_shows_locked_midstate": ('readonly\\": true' in r.stdout) or ("(DENY)" in r.stdout)
          or ('"readonly": true' in r.stdout)}
    listing = sorted(os.listdir(d))
    stuck = cleanup(d)
    res = {"listing": listing, "agent": "codex", "mech": mech, "sandbox": sandbox_mode or "default", "rc": r.returncode,
           "rows": rows, "c8": c8, "secs": round(time.time() - t, 1), "stuck": stuck,
           "transcript": r.stdout[-12000:], "stderr": r.stderr[-3000:]}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps({k: res[k] for k in ("mech", "sandbox", "rc", "rows", "c8", "stuck", "secs", "listing")}))


def codex_unlock(d, out, sandbox_mode):
    """C8/U-row twin: files locked OUTSIDE Codex (one per mechanism), unlocked from INSIDE it."""
    names = {}
    for mech in sorted(lockprobe.MECHANISMS):
        f = os.path.join(d, "U-%s.esm" % mech).replace("\\", "/")
        write_text(f)
        lockprobe.apply(mech, f)
        names[mech] = f
    py = sys.executable.replace("\\", "/")
    cmds = ["whoami /user", "whoami /groups /fo csv"]
    cmds += ['& "%s" -B "%s" remove "%s"' % (py, LOCKPROBE, f) for f in names.values()]
    prompt = ("Run each of these PowerShell commands, one per tool call, exactly as written, and show the full "
              "output of each. Do nothing else and do not try alternatives.\n" + "\n".join("  " + c for c in cmds))
    args = [shutil.which("codex") or "codex", "exec", "--skip-git-repo-check", "--json", "-C", d,
            "-c", 'model_reasoning_effort="low"']
    if sandbox_mode:
        args += ["-s", sandbox_mode]
    env = dict(os.environ, SKYGUARD_LOCK_SANDBOX=ROOT)
    r = subprocess.run(args + ["-"], input=prompt, capture_output=True, text=True, env=env, timeout=900,
                       encoding="utf-8", errors="replace")
    res = {"agent": "codex", "mode": "unlock-inside", "sandbox": sandbox_mode or "default", "rc": r.returncode}
    for mech, f in names.items():
        st = lockprobe.state(f)
        res[mech] = {"unlocked_inside": (not st["readonly"]) and not st["deny_aces"],
                     "left": {"readonly": st["readonly"], "deny": st["deny_aces"]}}
    res["stuck_after_outside_cleanup"] = cleanup(d)
    res["transcript"] = r.stdout[-15000:]
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "transcript"}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("agent", choices=["claude", "codex", "codex-unlock"])
    ap.add_argument("mech", choices=sorted(lockprobe.MECHANISMS))
    ap.add_argument("--sandbox", default="")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.environ.setdefault(sandbox.ENV, ROOT)
    d = "%s/agent-%s/%s%s" % (ROOT, a.agent, a.mech, ("-" + a.sandbox) if a.sandbox else "")
    fresh(d)
    if a.agent == "claude":
        claude(a.mech, d, a.out)
    elif a.agent == "codex-unlock":
        codex_unlock(d, a.out, a.sandbox)
    else:
        codex(a.mech, d, a.out, a.sandbox)
