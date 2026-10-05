"""Harness: feeds payloads to the real run.py entry. usage: python h.py cases.json"""
import json, subprocess, sys, os
REPO = os.environ.get("RT_REPO", "")
if not REPO:
    sys.exit("set RT_REPO to the toolkit checkout to test (README.md)")
GAME = "C:/Temp/rt-routes/Game"
# The fake home the engine is given (USERPROFILE/HOME below). A case names it as <HOME>, the token
# tools/payload-capture/scrub.py uses, so no case carries a real user's home.
FAKE_HOME = "C:/Temp/rt-routes/home"
RUN = REPO + "/tools/skyguard/run.py"
ROLE = {"Bash": "protect-bash", "PowerShell": "protect-bash", "Monitor": "protect-bash",
        "Edit": "protect-files", "Write": "protect-files", "NotebookEdit": "protect-files"}


def fire(agent, tool, ti, cwd=GAME):
    ti = json.loads(json.dumps(ti).replace("<HOME>", FAKE_HOME))
    d = {"cwd": cwd, "hook_event_name": "PreToolUse", "session_id": "s", "tool_name": tool, "tool_input": ti,
         "permission_mode": "bypassPermissions"}
    if agent == "claude":
        role = ROLE.get(tool, "protect-mcp" if tool.startswith("mcp__devbench") else None)
        if role is None:
            return "NOHOOK (no matcher)"
    else:
        role = "pre-tool"
        d["turn_id"] = "t"
        d["model"] = "m"
    env = dict(os.environ)
    env["USERPROFILE"] = FAKE_HOME
    env["HOME"] = FAKE_HOME
    env["PLUGINS_TXT"] = ""
    p = subprocess.run([sys.executable, RUN, agent, role, "--root", GAME, "--root-win", GAME, "--project", GAME],
                       input=json.dumps(d).encode(), capture_output=True, env=env, timeout=60)
    out = p.stdout.decode("utf-8", "replace").strip()
    if p.returncode != 0:
        return "ENGINE-EXIT-%d" % p.returncode
    if agent == "codex":
        out = out.replace("SKYGUARD-OK", "").strip()
    if not out or out == "{}":
        return "allow"
    try:
        h = json.loads(out)["hookSpecificOutput"]
    except Exception:
        return "RAW:" + out[:100]
    if "permissionDecision" in h:
        return h["permissionDecision"] + " :: " + h["permissionDecisionReason"][:90].replace("\n", " ")
    return "advise :: " + h.get("additionalContext", "")[:90].replace("\n", " ")


if __name__ == "__main__":
    cases = json.load(open(sys.argv[1], encoding="utf-8"))
    for c in cases:
        agent = c.get("agent", "claude")
        r = fire(agent, c["tool"], c["ti"], c.get("cwd", GAME))
        mark = "" if r.split(" ")[0] in c.get("expect", "deny ask").split() else "   <<<<<< HOLE?"
        print("%-4s %-22s %s => %s%s" % (c["id"], agent + "/" + c["tool"][:16], json.dumps(c["ti"])[:110], r, mark))
