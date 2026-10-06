"""A deliberately tiny fake coding agent: the test double for ADAPTING.md.

    toy_agent.py --profile json-deny|exit2 --hook '<json argv list>' --script calls.json --cwd DIR
                 [--hook-timeout S]

For each call in the script ({"op":"write","path":..} or {"op":"shell","cmd":..}) it builds ITS OWN native
payload, runs the hook with that payload on stdin, and executes the call unless the hook blocked it in the
one way this profile honours. Everything else -- timeout, crash, wrong exit code, garbage -- lets the call
RUN (fail open), like the Codex CLI. Prints a JSON list of {op, blocked, executed, reason}.
"""
import argparse
import json
import os
import subprocess
import sys


def native(profile, call, cwd):
    if profile == "json-deny":
        if call["op"] == "write":
            return {"toy_event": "before_tool", "tool": "write_file", "args": {"path": call["path"], "content": "x"}, "cwd": cwd}
        return {"toy_event": "before_tool", "tool": "run_shell", "args": {"cmd": call["cmd"]}, "cwd": cwd}
    if call["op"] == "write":
        return {"hook": "pre", "call": {"name": "edit", "file": call["path"]}, "cwd": cwd}
    return {"hook": "pre", "call": {"name": "sh", "cmdline": call["cmd"]}, "cwd": cwd}


def verdict(profile, cp):
    """-> (blocked, reason). cp is None when the hook timed out or could not start: that RUNS the call."""
    if cp is None:
        return False, ""
    if profile == "json-deny":
        if cp.returncode != 0:
            return False, ""
        try:
            d = json.loads(cp.stdout.decode("utf-8"))
        except ValueError:
            return False, ""
        return (d.get("toy_decision") == "block", str(d.get("reason", "")))
    return (cp.returncode == 2, cp.stderr.decode("utf-8", "replace"))


def execute(call, cwd):
    if call["op"] == "write":
        os.makedirs(os.path.dirname(call["path"]), exist_ok=True)
        with open(call["path"], "w") as f:
            f.write("x")
    else:
        subprocess.run(call["cmd"], shell=True, cwd=cwd, capture_output=True, timeout=30)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=["json-deny", "exit2"], required=True)
    ap.add_argument("--hook", required=True)
    ap.add_argument("--script", required=True)
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--hook-timeout", type=float, default=30.0)
    a = ap.parse_args(argv)
    hook = json.loads(a.hook)
    results = []
    with open(a.script, encoding="utf-8") as fh:
        script = json.load(fh)
    for call in script:
        data = json.dumps(native(a.profile, call, a.cwd)).encode("utf-8")
        try:
            cp = subprocess.run(hook, input=data, capture_output=True, timeout=a.hook_timeout, cwd=a.cwd)
        except (subprocess.TimeoutExpired, OSError):
            cp = None
        blocked, reason = verdict(a.profile, cp)
        if not blocked:
            execute(call, a.cwd)
        results.append({"op": call["op"], "blocked": blocked, "executed": not blocked, "reason": reason})
    print(json.dumps(results))


if __name__ == "__main__":
    main()
