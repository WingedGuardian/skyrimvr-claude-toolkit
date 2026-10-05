#!/usr/bin/env python3
"""Plan 4 Task 14 -- summarise a Codex TUI session in an install copy. READ-ONLY.

It never writes ~/.codex/config.toml, never edits the install, and never calls Codex. It reads:
the install's Data/ listing, .claude/backups/AUDIT_LOG.txt, the heartbeat latency files, Codex's rollout
files for this folder, and (read-only) ~/.codex/config.toml. It runs the install's own hook canary.

Run it YOURSELF in PowerShell, in the same tab, after /quit:

    python collect.py                      (from the folder that holds this file and "Skyrim Probe (x86)")
    python collect.py "<path to the install>"      (anywhere)

Paste the whole output back. Python 3.8+, standard library only.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARG = sys.argv[1] if len(sys.argv) > 1 else None
COPY = Path(ARG) if ARG else HERE / "Skyrim Probe (x86)"
CODEX_HOME = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
CFG = CODEX_HOME / "config.toml"


def say(s=""):
    sys.stdout.write(s + "\n")


def norm(p) -> str:
    return str(p).replace("\\", "/").rstrip("/").lower()


def short(x, n=300) -> str:
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)
    s = s.replace("\r", " ").replace("\n", " | ")
    return s if len(s) <= n else s[:n] + " ...[+%d]" % (len(s) - n)


say("== 0. what this is reading")
say("   install: %s (exists: %s)" % (COPY, COPY.is_dir()))
say("   codex config: %s (exists: %s)" % (CFG, CFG.is_file()))
if not COPY.is_dir():
    sys.exit("install folder not found; pass its path as the first argument")
codex = shutil.which("codex")
try:
    say("   codex: " + subprocess.run([codex or "codex", "--version"], capture_output=True, text=True,
                                      timeout=60).stdout.strip())
except Exception as e:  # noqa: BLE001
    say("   codex --version failed: %r" % (e,))

say("== 1. files")
data = COPY / "Data"
say("   Data/ : " + ", ".join(sorted(p.name for p in data.iterdir())) if data.is_dir() else "   no Data/")
say("   TuiProbe.esp exists: %s   (EXPECTED: False, the hook should have blocked it)" % (data / "TuiProbe.esp").exists())

say("== 2. audit log (lines since the install was set up that mention a hook verdict)")
audit = COPY / ".claude" / "backups" / "AUDIT_LOG.txt"
if audit.is_file():
    lines = audit.read_text(encoding="utf-8", errors="replace").splitlines()
    keep = [l for l in lines if any(k in l for k in (" DENY", " REFUSE", " ASK", " ADVISE", "BLIND", "INERT"))]
    for l in keep[-25:]:
        say("   " + short(l, 260))
    say("   verdict lines: %d (of %d audit lines)" % (len(keep), len(lines)))
else:
    say("   no AUDIT_LOG.txt")

say("== 3. Codex rollouts whose working folder is this install")
roll = []
for f in glob.glob(str(CODEX_HOME / "sessions" / "**" / "*.jsonl"), recursive=True):
    try:
        with open(f, encoding="utf-8", errors="replace") as fh:
            first = json.loads(fh.readline())
    except (OSError, ValueError):
        continue
    pl = first.get("payload") or {}
    if first.get("type") == "session_meta" and norm(pl.get("cwd", "")) == norm(COPY):
        roll.append((os.path.getmtime(f), f, pl))
roll.sort()
say("   found %d rollout(s)" % len(roll))
for _, f, pl in roll:
    say("   --- %s" % os.path.basename(f))
    say("       originator=%s source=%s cli=%s" % (pl.get("originator"), pl.get("source"), pl.get("cli_version")))
    reads_kb = False
    with open(f, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("type") != "response_item":
                continue
            p = o.get("payload") or {}
            t = p.get("type")
            blob = json.dumps(p, ensure_ascii=False)
            if t == "message" and p.get("role") in ("user", "assistant"):
                txt = " ".join(c.get("text", "") for c in p.get("content", []) if isinstance(c, dict))
                if p["role"] == "user" and txt.startswith("# AGENTS.md"):
                    continue
                say("       %-9s %s" % (p["role"], short(txt, 400)))
            elif t in ("function_call", "custom_tool_call", "local_shell_call"):
                say("       CALL      %s" % short(blob, 260))
                if "KNOWLEDGEBASE.local.md" in blob:
                    reads_kb = True
            elif t in ("function_call_output", "custom_tool_call_output"):
                say("       OUTPUT    %s" % short(p.get("output", ""), 300))
            elif t == "message" and p.get("role") == "developer" and "SKYGUARD" in blob:
                say("       hook ctx  %s" % short(blob[:200], 200))
    say("       rollout shows a read of KNOWLEDGEBASE.local.md: %s" % reads_kb)

say("== 4. codex config.toml entries naming this install (read-only)")
text = CFG.read_text(encoding="utf-8", errors="replace") if CFG.is_file() else ""
needle = norm(COPY).replace("/", "\\").lower()
cur, found = None, {}
for line in text.splitlines():
    s = line.strip()
    if s.startswith("["):
        cur = s if needle in s.lower().replace("\\\\", "\\") else None
        if cur:
            found[cur] = []
        continue
    if cur and s:
        found[cur].append(s)
for k, v in found.items():
    say("   " + k)
    for x in v:
        say("       " + x)
say("   tables found: %d  (EXPECTED 3: one [projects.'...'] trust table + two [hooks.state.'...hooks.json:...'])" % len(found))

say("== 5. do the stored hashes equal what the toolkit computes? (codex_trust.expected_entries)")
try:
    sys.path.insert(0, str(COPY / "tools"))
    from skyguard import codex_trust  # noqa: E402

    exp = codex_trust.expected_entries(COPY / ".codex" / "hooks.json")
    stored = " ".join(" ".join(v) for v in found.values())
    for k, h in exp.items():
        say("   %s\n      expected %s  stored=%s" % (k, h, h in stored))
    rep = codex_trust.trust_report(COPY / ".codex" / "hooks.json", CFG, COPY)
    say("   trust_report overall=%s" % rep.get("overall"))
    for h in rep.get("hooks", []):
        say("      " + short(h, 200))
except Exception as e:  # noqa: BLE001
    say("   could not compute: %r" % (e,))

say("== 6. pre-tool latency (hook wall time, ms) from the install's heartbeat")
lat = COPY / ".claude" / "backups" / ".hook-heartbeat" / "codex" / "pre-tool.latency"
vals = []
if lat.is_file():
    for l in lat.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            a, b = l.split()[-2:]
            vals.append((float(b) - float(a)) * 1000)
        except ValueError:
            pass
vals.sort()
if vals:
    say("   n=%d p50=%.0f p95=%.0f max=%.0f" % (len(vals), vals[len(vals) // 2],
                                                   vals[min(len(vals) - 1, int(len(vals) * 0.95))], vals[-1]))
else:
    say("   no latency records")

say("== 7. hook canary (the install's own; it only reads)")
bash = next((b for b in (os.environ.get("TOOLKIT_BASH"), r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash"))
             if b and Path(b).exists()), None)
if bash:
    r = subprocess.run([bash, "tools/hook-canary.sh"], cwd=str(COPY), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    say(r.stdout.rstrip())
    say("   canary exit code: %d" % r.returncode)
else:
    say("   no bash found; skipping the canary")
say("== done")
