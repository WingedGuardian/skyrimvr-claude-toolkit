"""Hook health from staged telemetry: invocation -> receipt -> evaluation -> emission.

A 3.x heartbeat proved only that a payload arrived. The failures that matter happen AFTER receipt: an
engine that crashes, a wrapper the agent's timeout killed (on Codex that lets the command RUN), an
output the agent could not parse. So every invocation leaves a record per stage, one line per file,
overwritten, in `<project>/.claude/backups/.hook-heartbeat/<agent>/`:

    <hook>.invoked    wrapper   "<t> <inv>"
    <hook>.received   wrapper   "<t> <inv> bytes=<n>"
    <hook>.evaluated  engine    "<t> <inv> <status> <decision> <code>"
    <hook>.emitted    wrapper   "<t> <inv> ok"            after it validated the engine's output
    <hook>.failed     either    "<t> <inv> <stage> <text>"  stage: receipt | python | engine | emission
    <hook>.latency    wrapper   "<inv> <t_invoked> <t_emitted>", APPENDED; trimmed by the engine

`<t>` is Unix seconds with a fractional part. A failure clears only when a NEWER COMPLETED evaluation
proves recovery; a later payload, a later invocation or an equal timestamp prove nothing.

This module never raises into a hook's decision path: `record` and `trim_latency` swallow OSError.
The 3.x files (`.hook-heartbeat/<hook>`, `<hook>.blind`) are ignored.

CLI:  python tools/skyguard/health.py report --root <install> [--stale-min N] [--json]
Exit: 1 if any hook is DEGRADED; else 2 if any is UNMEASURABLE or the directory is unreadable; else 0.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path

STATES = ("HEALTHY", "STALE", "DEGRADED", "NOT EXERCISED", "UNMEASURABLE")
STAGES = ("invoked", "received", "evaluated", "emitted", "failed")
STATUS_VALUES = ("evaluated", "partial", "failed")
CLAUDE_HOOKS = ("protect-files", "protect-bash", "backup-before-edit", "snapshot-before-tool", "session-kb-guard")
CODEX_HOOKS = ("pre-tool", "session-start")
LATENCY_MAX_BYTES = 256 * 1024
LATENCY_KEEP_LINES = 2000
MIN_LATENCY_SAMPLES = 5
HB_REL = Path(".claude") / "backups" / ".hook-heartbeat"


class Unmeasurable(Exception):
    """A record exists but cannot be read as one of the formats above."""


# --------------------------------------------------------------------------------------------------
# writing (engine side) -- must never raise into the decision path
# --------------------------------------------------------------------------------------------------
def record(dirpath, hook, stage, inv, detail=""):
    """Overwrite `<hook>.<stage>` with "<now> <inv> <detail>". Returns True when written."""
    try:
        d = Path(dirpath)
        d.mkdir(parents=True, exist_ok=True)
        line = "{:.6f} {}".format(time.time(), inv)
        if detail:
            line += " " + " ".join(str(detail).split())
        (d / "{}.{}".format(hook, stage)).write_text(line + "\n", encoding="utf-8")
        return True
    except (OSError, ValueError):
        return False


def trim_latency(dirpath, hook):
    """Keep the last LATENCY_KEEP_LINES lines once the file passes LATENCY_MAX_BYTES."""
    try:
        p = Path(dirpath) / "{}.latency".format(hook)
        if p.stat().st_size <= LATENCY_MAX_BYTES:
            return
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-LATENCY_KEEP_LINES:]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------------------------------
_TIME = re.compile(r"[0-9]+(\.[0-9]+)?\Z")
_MIN_FIELDS = {"invoked": 2, "received": 3, "evaluated": 5, "emitted": 3, "failed": 3}


def read(path, stage="invoked"):
    """Parse one record -> (t, inv, rest) or None when the file does not exist. Raises Unmeasurable."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    except OSError as e:
        raise Unmeasurable("{}: unreadable ({})".format(path.name, e.__class__.__name__))
    fields = (text.splitlines() or [""])[0].split()
    if len(fields) < _MIN_FIELDS[stage]:
        raise Unmeasurable("{}: too few fields {!r}".format(path.name, text[:60]))
    try:
        # float() alone accepts "20260909_15" (underscores) as 2026090915.0: a 3.x stamp must not parse
        if not _TIME.match(fields[0]):
            raise ValueError(fields[0])
        t = float(fields[0])
    except ValueError:
        raise Unmeasurable("{}: timestamp {!r} is not a number".format(path.name, fields[0][:30]))
    if not math.isfinite(t):
        raise Unmeasurable("{}: timestamp is not finite".format(path.name))
    rest = fields[2:]
    if stage == "evaluated" and rest[0] not in STATUS_VALUES:
        raise Unmeasurable("{}: unknown status {!r}".format(path.name, rest[0][:30]))
    return t, fields[1], rest


def _completed(recs):
    """Time of the newest COMPLETED evaluation: status 'evaluated' AND emitted for the same invocation."""
    ev, em = recs.get("evaluated"), recs.get("emitted")
    if ev and em and ev[2][0] == "evaluated" and em[1] == ev[1]:
        return ev[0]
    return None


def _never_finished(recs, now, kill_s):
    """Invoked, never emitted, and older than any hook timeout: the agent killed it (Codex then RAN the call)."""
    inv, em = recs.get("invoked"), recs.get("emitted")
    return bool(inv) and (not em or (em[1] != inv[1] and em[0] < inv[0])) and now - inv[0] > kill_s


def _stamp(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


def hook_state(d, hook, now, stale_s=7200, kill_s=150):
    """-> (state, detail). The rules run in the order they are numbered."""
    d = Path(d)
    recs = {}
    try:
        for stage in STAGES:
            r = read(d / "{}.{}".format(hook, stage), stage)
            if r is not None:
                recs[stage] = r
    except Unmeasurable as e:
        # 2. a record that does not parse is never green (a truncated stamp once sorted low and cleared a failure)
        return "UNMEASURABLE", str(e)

    # 1. nothing at all: the hook has proven nothing either way
    if not recs:
        return "NOT EXERCISED", "no telemetry yet -- proves nothing either way"

    done = _completed(recs)
    failed = recs.get("failed")

    # 3. a failure is current unless a completed evaluation is STRICTLY newer (equal stamps prove no order)
    if failed and (done is None or done <= failed[0]):
        stage = failed[2][0]
        text = " ".join(failed[2][1:])
        return "DEGRADED", "{} failed at {}{}".format(stage, _stamp(failed[0]), (": " + text) if text else "")

    # 4. invoked and never finished
    if _never_finished(recs, now, kill_s):
        t = recs["invoked"][0]
        return "DEGRADED", ("invoked at {} and never finished -- killed by the agent's timeout? "
                            "On Codex that lets the command RUN".format(_stamp(t)))

    # 5. the newest evaluation did not finish its job
    ev = recs.get("evaluated")
    if ev and ev[2][0] != "evaluated":
        return "DEGRADED", "{}: {}".format(ev[2][0], ev[2][2] if len(ev[2]) > 2 else "?")

    if done is None:
        return "NOT EXERCISED", "no completed evaluation yet (in flight, or no verdict was ever emitted)"

    # 6. the last completed evaluation is old
    if now - done > stale_s:
        return "STALE", "last completed evaluation {} min ago".format(int((now - done) // 60))

    # 7. healthy; an older failure stays visible so intermittent trouble has somewhere to show up
    detail = "last completed evaluation {} s ago".format(max(0, int(now - done)))
    if failed:
        detail = "recovered; was DEGRADED at {} ({})".format(_stamp(failed[0]), detail)
    return "HEALTHY", detail


def latency(d, hook, last=200):
    """-> (n, p50_ms, p95_ms) over the newest `last` valid samples; (0, None, None) when there are none."""
    ms = []
    try:
        lines = (Path(d) / "{}.latency".format(hook)).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0, None, None
    for line in lines[-last:]:
        f = line.split()
        try:
            a, b = float(f[1]), float(f[2])
        except (IndexError, ValueError):
            continue
        if math.isfinite(a) and math.isfinite(b) and b >= a:
            ms.append((b - a) * 1000.0)
    if not ms:
        return 0, None, None
    ms.sort()
    return len(ms), ms[len(ms) // 2], ms[min(len(ms) - 1, int(0.95 * len(ms)))]


# --------------------------------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------------------------------
def _installed(root):
    """-> {agent: [hook, ...]} for every agent whose hook set is installed under `root`."""
    out = {}
    hooks = root / ".claude" / "hooks"
    if hooks.is_dir():
        have = [h for h in CLAUDE_HOOKS if (hooks / (h + ".sh")).is_file()]
        if have:
            out["claude"] = have
    if (root / ".codex" / "hooks.json").is_file():
        out["codex"] = list(CODEX_HOOKS)
    return out


def report(root, stale_min=120, as_json=False, now=None, out=None):
    """Print the report; return the exit code."""
    out = out or sys.stdout
    now = time.time() if now is None else now
    root = Path(root)
    installed = _installed(root)
    if not installed:
        print("no installed hooks under {} (.claude/hooks, .codex/hooks.json) -- cannot check".format(root),
              file=sys.stderr)
        return 2
    hb = root / HB_REL
    rows, unreadable = [], False
    if hb.exists() and not os.access(str(hb), os.R_OK | os.X_OK):
        unreadable = True
    for agent, hooks in installed.items():
        d = hb / agent
        for h in hooks:
            if unreadable:
                state, detail = "UNMEASURABLE", "telemetry directory is not readable"
            else:
                state, detail = hook_state(d, h, now, stale_s=stale_min * 60)
            n, p50, p95 = latency(d, h)
            rows.append({"agent": agent, "hook": h, "state": state, "detail": detail,
                         "latency_n": n, "p50_ms": p50, "p95_ms": p95})
    legacy = []
    if hb.is_dir():
        try:
            legacy = sorted(p.name for p in hb.iterdir()
                            if p.is_file() and (p.name in CLAUDE_HOOKS or p.name.endswith(".blind")))
        except OSError:
            unreadable = True
    counts = {s: sum(1 for r in rows if r["state"] == s) for s in STATES}
    code = 1 if counts["DEGRADED"] else 2 if (counts["UNMEASURABLE"] or unreadable) else 0
    if as_json:
        print(json.dumps({"rows": rows, "counts": counts, "legacy_ignored": legacy, "exit": code}, indent=1), file=out)
        return code
    print("=" * 62, file=out)
    print("HOOK CANARY -- did each guard see its payload, reach a verdict, and deliver it?", file=out)
    print("=" * 62, file=out)
    for r in rows:
        lat = ""
        if r["latency_n"] >= MIN_LATENCY_SAMPLES:
            lat = "  [latency p50 {:.0f} / p95 {:.0f} ms, n={}]".format(r["p50_ms"], r["p95_ms"], r["latency_n"])
        print("  {:<8} {:<22} {:<13} {}{}".format(r["agent"], r["hook"], r["state"], r["detail"], lat), file=out)
    if legacy:
        print("  (ignored {} legacy 3.x heartbeat file(s): {})".format(len(legacy), ", ".join(legacy)), file=out)
    print("-" * 62, file=out)
    print("healthy: {HEALTHY}   stale: {STALE}   degraded: {DEGRADED}   unmeasurable: {UNMEASURABLE}   "
          "not-exercised: {NOT EXERCISED}".format(**counts), file=out)
    print(file=out)
    if counts["DEGRADED"]:
        print("RESULT: DEGRADED HOOK(S) -- a guard ran and did NOT complete a delivered verdict. Common causes:", file=out)
        print("        Python missing or its path unconfigured (re-run setup.sh), an engine crash, or the agent", file=out)
        print("        killing a slow hook. An unfinished hook can let the command RUN.", file=out)
    elif code == 2:
        print("RESULT: UNMEASURABLE -- a telemetry record is unreadable or malformed; nothing can be proven.", file=out)
    elif counts["HEALTHY"] == 0:
        print("RESULT: UNPROVEN -- no hook has completed an evaluation yet. Trigger one tool call of each", file=out)
        print("        kind (a shell command and a file edit) and re-run.", file=out)
    else:
        print("RESULT: OK -- {} hook(s) proven to receive, evaluate and deliver".format(counts["HEALTHY"]), file=out)
    return code


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("report")
    rp.add_argument("--root", required=True)
    rp.add_argument("--stale-min", type=int, default=int(os.environ.get("HOOK_CANARY_STALE_MIN", "120") or 120))
    rp.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    return report(a.root, a.stale_min, a.json)


if __name__ == "__main__":
    sys.exit(main())
