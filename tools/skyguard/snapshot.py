"""Snapshot .psc/.pex before every non-read-only shell command (port of snapshot-before-tool.sh).

find's -mtime counts whole 24h periods (truncated); -newer is strictly newer; `ls | wc -l` skips
dotfiles; `-name "*.psc"` is case-sensitive. Each is reproduced below."""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from .grep import grep

SKIP = (r"^\s*(ls|cat|head|tail|grep|find|wc|file|stat|pwd|echo|date|whoami|which|type|env|printenv)\s",
        r"^\s*git\s+(status|log|diff|show|branch|remote|config\s+--get)")
DAY = 86400


def _files(d, suffix):
    if not d.is_dir():
        return []
    return [d / n for n in os.listdir(str(d)) if n.endswith(suffix) and (d / n).is_file()]


def _age_days(path, now):
    return int((now - path.stat().st_mtime) // DAY)


def _visible_count(d):
    return len([n for n in os.listdir(str(d)) if not n.startswith(".")]) if d.is_dir() else 0


def run(project, command, payload_empty, audit, now=None):
    if payload_empty:
        audit.raw("[{}] snapshot-before-tool BLIND: EMPTY payload -- no snapshot taken\n".format(audit.stamp()))
        return
    if any(grep(p, command, icase=False) for p in SKIP):
        return
    now = time.time() if now is None else now
    ts = audit.stamp()
    root = Path(project)
    base = root / ".claude" / "backups" / "auto_snapshots"
    src, dep, rl = root / "Data" / "Scripts" / "Source", root / "Data" / "Scripts", base / ".last_snapshot"
    try:
        base.mkdir(parents=True, exist_ok=True)         # bash: mkdir -p ... 2>/dev/null
    except OSError:
        pass
    if not src.is_dir() and not dep.is_dir():
        return
    if rl.is_file():
        try:
            last = int(rl.read_bytes().decode("utf-8", "replace").strip() or "0")
        except (OSError, ValueError):
            last = 0
        if int(now) - last < 60:
            rl_mtime = rl.stat().st_mtime_ns
            if not any(f.stat().st_mtime_ns > rl_mtime for f in _files(src, ".psc")):
                return
    snap = base / ts
    for sub in ("source", "deploy"):
        try:
            (snap / sub).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    # bash: each cp runs under `find -exec ... 2>/dev/null`, so one failed copy is ignored
    for d, suffix, out in ((src, ".psc", "source"), (dep, ".pex", "deploy")):
        for f in _files(d, suffix):
            if _age_days(f, now) < 7:
                try:
                    shutil.copyfile(str(f), str(snap / out / f.name))
                except OSError:
                    pass
    psc, pex = _visible_count(snap / "source"), _visible_count(snap / "deploy")
    if psc == 0 and pex == 0:
        shutil.rmtree(str(snap), ignore_errors=True)
        return
    try:
        rl.write_bytes("{}\n".format(int(now)).encode("ascii"))     # bash: date +%s > file (LF, not CRLF)
    except OSError:
        pass
    short = (command + "\n").encode("utf-8")[:120].rstrip(b"\n")  # $(...) strips trailing newlines
    audit.raw(b"[" + ts.encode() + b"] AUTO-SNAPSHOT (psc:" + str(psc).encode() + b" pex:" + str(pex).encode()
              + b") before: " + short + b"\n")
    try:
        for d in base.iterdir():
            if d.is_dir() and _age_days(d, now) > 14:
                shutil.rmtree(str(d), ignore_errors=True)
    except OSError:
        pass                                            # bash: find ... 2>/dev/null
