"""The daily housekeep run: only ever started by a Claude Code session, and always announced in it first.

Governance (user ruling 2026-10-29): cleanup never runs unattended in the background. The session-start hook asks
`announcement()` -- a fast check that prints one line into the session BEFORE anything starts -- and then
`housekeep.py session` (detached, no window, low priority) calls `run()`. `run()` is time-boxed (daily_minutes),
at most once every daily_every_hours, skipped while a process in pause_while_running runs, and it APPLIES only when
the housekeep code matches approved_sha256; otherwise it only plans and writes REVIEW-NEEDED.md. Its summary is the
next session's start note. Policy: docs/RETENTION.md.

    python tools/housekeep.py announce      # the line the hook prints (empty when nothing is due)
    python tools/housekeep_daily.py --dry-run   # plan and report only, by hand
"""
import collections
import ctypes
import datetime
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("housekeep", Path(__file__).with_name("housekeep.py"))
housekeep = sys.modules.get("housekeep") or importlib.util.module_from_spec(_spec)
if "housekeep" not in sys.modules:
    sys.modules["housekeep"] = housekeep
    _spec.loader.exec_module(housekeep)

# Every file lives under the CONFIG's root, never this module's: a test with its own config can never touch -- or
# trigger a run against -- the real install's state. last-plan.json is overwritten each run, never a series.
NAMES = {"LOG": "daily.log", "PLAN": "last-plan.json", "REVIEW": "REVIEW-NEEDED.md", "ALERTS": "alerts.txt",
         "SIZES": "agent-output-size.json", "STAMP": "last-daily.json"}


def f(cfg, name) -> Path:
    return Path(cfg["root"]) / ".claude" / "diag" / "housekeep" / NAMES[name]
GIB = 1024 ** 3
LOG_KEEP_LINES = 3000
CODE_FILES = ("housekeep.py", "housekeep_archive.py", "housekeep_daily.py")


def running_games(names):
    if sys.platform != "win32":
        return []
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                         **housekeep.NO_WINDOW).stdout.lower()
    return [g for g in names if '"{}"'.format(g.lower()) in out]


def low_priority():
    if sys.platform == "win32":                       # idle CPU class + background I/O mode for this process
        k = ctypes.windll.kernel32
        k.SetPriorityClass(k.GetCurrentProcess(), 0x00000040)
        k.SetPriorityClass(k.GetCurrentProcess(), 0x00100000)


def log(cfg, text):
    p = f(cfg, "LOG")
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    lines += text.rstrip("\n").splitlines()
    p.write_text("\n".join(lines[-LOG_KEEP_LINES:]) + "\n", encoding="utf-8")


def code_sha256() -> str:
    """One fingerprint over every file whose logic decides what gets removed or archived."""
    d = hashlib.sha256()
    here = Path(__file__).resolve().parent
    for name in CODE_FILES:
        d.update(name.encode() + b"\0" + (here / name).read_bytes())
    return d.hexdigest()


def approved(cfg) -> bool:
    return bool(cfg["approved_sha256"]) and cfg["approved_sha256"] == code_sha256()


def due(cfg, now=None) -> str:
    """'' when a run should start now; otherwise why not. Cheap: no planning, no hashing of agent output."""
    now = time.time() if now is None else now
    if not cfg["daily_enabled"]:
        return "daily run disabled"
    try:
        last = json.loads(f(cfg, "STAMP").read_text(encoding="utf-8")).get("started", 0)
    except (OSError, ValueError, AttributeError):
        last = 0
    if now - last < cfg["daily_every_hours"] * 3600:
        return "ran {:.1f} h ago".format((now - last) / 3600)
    games = running_games(cfg["pause_while_running"])
    if games:
        return "waiting: {} running".format(", ".join(games))
    return ""


def announcement(cfg, now=None) -> str:
    """The line the session-start hook prints BEFORE the run starts, so nobody is blindsided. Empty = nothing runs."""
    if due(cfg, now):
        return ""
    if not approved(cfg):
        return ("HOUSEKEEP: housekeep's code changed since you approved it, so today's run only PLANS (nothing is "
                "removed or archived). When it finishes, review {} and approve it.".format(f(cfg, "REVIEW")))
    return ("HOUSEKEEP: today's cleanup (toolkit housekeep, approved version {}) starts now in the background -- low "
            "priority, no windows, at most {} min. It archives finished work to {} and recycles build leftovers; "
            "nothing in use, no saves. Summary at the next session start; log: {}.".format(
                cfg["approved_sha256"][:8], cfg["daily_minutes"], cfg["archive_root"], f(cfg, "LOG")))


def write_review(cfg, doc, sha):
    """The list a person reads before unreviewed code may apply: per folder and per extension."""
    by_task, by_ext = collections.Counter(), collections.Counter()
    for a in doc["actions"]:
        by_task[(a["class"], a.get("task") or a["path"])] += a["bytes"]
        by_ext[Path(a["path"]).suffix.lower() or "(folder)"] += a["bytes"]
    lines = ["# housekeep: review needed", "",
             "housekeep code ({}) changed since the last approved version, so the daily run planned "
             "only.".format(", ".join(CODE_FILES)),
             "Code sha256: `{}`. Approve by setting `approved_sha256` to it in `.claude/housekeep.json`.".format(sha),
             "Plan: `{}` -- {} action(s), {:.2f} GiB.".format(f(cfg, "PLAN"), len(doc["actions"]),
                                                             sum(a["bytes"] for a in doc["actions"]) / GIB),
             "", "## By folder", ""]
    lines += ["- {:.2f} GiB  {}  {}".format(b / GIB, c, t) for (c, t), b in by_task.most_common()]
    lines += ["", "## By extension", ""]
    lines += ["- {:.2f} GiB  {}".format(b / GIB, e) for e, b in by_ext.most_common()]
    f(cfg, "REVIEW").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_alerts(doc, cfg):
    """Same-day signals for the session note: agent output grew past growth_warn_gib since the last run, or a pinned
    (.housekeep-keep) folder is big. Rewritten every run, so a quiet day leaves an empty file."""
    lines = []
    total = doc.get("agent_total")
    try:
        prev = json.loads(f(cfg, "SIZES").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = {}
    if total is not None:
        if prev.get("total") is not None and total - prev["total"] > cfg["growth_warn_gib"] * GIB:
            lines.append("agent output grew {:.1f} GiB since {} (now {:.1f} GiB) -- see {}".format(
                (total - prev["total"]) / GIB, prev.get("date", "the last run"), total / GIB, f(cfg, "LOG")))
        f(cfg, "SIZES").write_text(json.dumps({"total": total, "date": datetime.datetime.now().isoformat(timespec="minutes")}),
                         encoding="utf-8")
    for p, b in doc.get("large_pins", []):
        lines.append("pinned (.housekeep-keep) {:.1f} GiB: {} -- archive it or say why in its README".format(b / GIB, p))
    f(cfg, "ALERTS").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def run(cfg, dry=False, now=None) -> str:
    """Plan, then (approved code only) apply within daily_minutes. Returns the one-line summary for the next session."""
    now = time.time() if now is None else now
    stamp = datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M")
    f(cfg, "STAMP").parent.mkdir(parents=True, exist_ok=True)
    if not dry:
        f(cfg, "STAMP").write_text(json.dumps({"started": now}), encoding="utf-8")
    low_priority()
    sha = code_sha256()
    ok = approved(cfg)
    buf = io.StringIO()
    done = None
    with redirect_stdout(buf):
        doc = housekeep.plan(cfg)
        housekeep.print_rows(doc["rows"], "plan")
        f(cfg, "PLAN").write_text(json.dumps(doc, indent=1), encoding="utf-8")
        if not ok:
            write_review(cfg, doc, sha)
        else:
            if f(cfg, "REVIEW").exists():             # an earlier run's notice, now answered: the bin, never a delete
                housekeep.recycle([f(cfg, "REVIEW")])
            if not dry and doc["actions"]:
                done = housekeep.apply(doc, cfg, deadline=now + cfg["daily_minutes"] * 60, summary=True)
    write_alerts(doc, cfg)
    if not ok:
        summary = "today's run only PLANNED ({} actions, {:.1f} GiB) because the code changed; review {}".format(
            len(doc["actions"]), sum(a["bytes"] for a in doc["actions"]) / GIB, f(cfg, "REVIEW"))
    elif dry or done is None:
        summary = "today's run planned {} action(s), {:.1f} GiB{}".format(
            len(doc["actions"]), sum(a["bytes"] for a in doc["actions"]) / GIB, " (dry run)" if dry else "")
    else:
        summary = "today's run ({}): {:.2f} GiB done; {} refused/failed; {} left for the next run".format(
            stamp, done["gib"], done["failed"], done["deferred"])
    log(cfg, "{} {}\n{}".format(stamp, summary, buf.getvalue()))
    return "housekeep: " + summary + " -- " + str(f(cfg, "LOG"))


if __name__ == "__main__":
    print(run(housekeep.load_config(None), dry="--dry-run" in sys.argv[1:]))
