"""Unattended housekeep: plan, then apply every class, once a day. Policy and setup: docs/RETENTION.md.

Meant for a daily scheduled task (Windows Task Scheduler; the command is in docs/RETENTION.md). Everything housekeep
removes goes to the Recycle Bin; saves and folders holding saves are refused by housekeep's own apply-time checks,
independent of the plan. Skips the run while a process in `pause_while_running` runs (the game, SteamVR): a frame
drop costs more than a day's delay.

It APPLIES only when the housekeep code matches `approved_sha256` in the config. Otherwise -- first run, or any
code change, including a toolkit update -- it plans, writes .claude/diag/housekeep/REVIEW-NEEDED.md, and the
session-start note says so. A rule nobody has seen the output of never acts.

    python tools/housekeep_scheduled.py            # what the task runs
    python tools/housekeep_scheduled.py --dry-run  # plan and report only
"""
import collections
import ctypes
import datetime
import hashlib
import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import housekeep  # noqa: E402

DIAG = ROOT / ".claude" / "diag" / "housekeep"
LOG = DIAG / "scheduled.log"
PLAN = DIAG / "last-plan.json"            # overwritten each run: one file, never an accumulating series
REVIEW = DIAG / "REVIEW-NEEDED.md"
ALERTS = DIAG / "alerts.txt"
SIZES = DIAG / "agent-output-size.json"
GIB = 1024 ** 3
LOG_KEEP_LINES = 3000
def running_games(names):
    if sys.platform != "win32":
        return []
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout.lower()
    return [g for g in names if '"{}"'.format(g.lower()) in out]


def low_priority():
    if sys.platform == "win32":                       # idle CPU class + background I/O mode for this process
        k = ctypes.windll.kernel32
        k.SetPriorityClass(k.GetCurrentProcess(), 0x00000040)
        k.SetPriorityClass(k.GetCurrentProcess(), 0x00100000)


def log(text):
    DIAG.mkdir(parents=True, exist_ok=True)
    lines = LOG.read_text(encoding="utf-8").splitlines() if LOG.exists() else []
    lines += text.rstrip("\n").splitlines()
    LOG.write_text("\n".join(lines[-LOG_KEEP_LINES:]) + "\n", encoding="utf-8")


CODE_FILES = ("housekeep.py", "housekeep_archive.py", "housekeep_scheduled.py")


def code_sha256() -> str:
    """One fingerprint over every file whose logic decides what gets removed or archived."""
    d = hashlib.sha256()
    here = Path(housekeep.__file__).resolve().parent
    for name in CODE_FILES:
        d.update(name.encode() + b"\0" + (here / name).read_bytes())
    return d.hexdigest()


def write_review(doc, sha):
    """The list a person reads before unreviewed code may apply: per folder and per extension, products called out."""
    by_task, by_ext = collections.Counter(), collections.Counter()
    for a in doc["actions"]:
        by_task[(a["class"], a.get("task") or a["path"])] += a["bytes"]
        by_ext[Path(a["path"]).suffix.lower() or "(folder)"] += a["bytes"]
    lines = ["# housekeep: review needed", "",
             "housekeep code ({}) changed since the last approved version, so the scheduled run planned "
             "only.".format(", ".join(CODE_FILES)),
             "Code sha256: `{}`. Approve by setting `approved_sha256` to it in `.claude/housekeep.json`.".format(sha),
             "Plan: `{}` -- {} action(s), {:.2f} GiB.".format(PLAN, len(doc["actions"]),
                                                             sum(a["bytes"] for a in doc["actions"]) / GIB),
             "", "## By folder", ""]
    lines += ["- {:.2f} GiB  {}  {}".format(b / GIB, c, t) for (c, t), b in by_task.most_common()]
    lines += ["", "## By extension", ""]
    lines += ["- {:.2f} GiB  {}".format(b / GIB, e) for e, b in by_ext.most_common()]
    REVIEW.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_alerts(doc, cfg):
    """Same-day signals for the session note: agent output grew past growth_warn_gib since the last run, or a pinned
    (.housekeep-keep) folder is big. Rewritten every run, so a quiet day leaves an empty file."""
    lines = []
    total = doc.get("agent_total")
    try:
        prev = json.loads(SIZES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = {}
    if total is not None:
        if prev.get("total") is not None and total - prev["total"] > cfg["growth_warn_gib"] * GIB:
            lines.append("agent output grew {:.1f} GiB since {} (now {:.1f} GiB) -- see {}".format(
                (total - prev["total"]) / GIB, prev.get("date", "the last run"), total / GIB, LOG))
        SIZES.write_text(json.dumps({"total": total, "date": datetime.datetime.now().isoformat(timespec="minutes")}),
                         encoding="utf-8")
    for p, b in doc.get("large_pins", []):
        lines.append("pinned (.housekeep-keep) {:.1f} GiB: {} -- archive it or say why in its README".format(b / GIB, p))
    ALERTS.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def main(argv) -> int:
    dry = "--dry-run" in argv
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    cfg = housekeep.load_config(None)
    games = running_games(cfg["pause_while_running"])
    if games:
        log("{} skipped: {} running".format(stamp, ", ".join(games)))
        return 0
    low_priority()
    sha = code_sha256()
    approved = bool(cfg["approved_sha256"]) and cfg["approved_sha256"] == sha
    buf = io.StringIO()
    with redirect_stdout(buf):
        doc = housekeep.plan(cfg)
        housekeep.print_rows(doc["rows"], "plan")
        DIAG.mkdir(parents=True, exist_ok=True)
        PLAN.write_text(json.dumps(doc, indent=1), encoding="utf-8")
        rc = 0
        if not approved:
            write_review(doc, sha)
        else:
            if REVIEW.exists():                       # an earlier run's notice, now answered: the bin, never a delete
                housekeep.recycle([REVIEW])
            if not dry and doc["actions"]:
                rc = housekeep.apply(doc, cfg)
    write_alerts(doc, cfg)
    mode = "dry run" if dry else ("applied" if approved else "UNREVIEWED CODE: planned only, see REVIEW-NEEDED.md")
    log("{} {} ({} action(s))\n{}".format(stamp, mode, len(doc["actions"]), buf.getvalue()))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
