#!/usr/bin/env python3
"""housekeep -- disk retention for agent output: report, plan, then apply exactly the plan.

Agent work piles up bytes that carry no information: copies of game files made as test fixtures, build
intermediates, the same log copied into ten evidence folders, launch logs nobody prunes, snapshot folders
that repeat the one before. The policy this tool implements is docs/RETENTION.md; read that first.

    python tools/housekeep.py [report]            what is where, by class (changes nothing)
    python tools/housekeep.py plan -o plan.json   every action it would take, with the hash it was planned on
            [--only CLASS[,CLASS]]               (skips the scans no listed class needs)
    python tools/housekeep.py apply plan.json     re-check each action, then do exactly those
            [--only CLASS[,CLASS]]               (recreatable, build, link, gzip, launch, snapshot, scratch)
    python tools/housekeep.py sweep-temp          recycle the toolkit's own stale temp dirs (see "temp sweep")
    python tools/housekeep.py session STATE.txt   sweep-temp, then write the session-start disk note to STATE.txt
    --config FILE   default <toolkit>/.claude/housekeep.json (.claude/housekeep.json.example ships)

Two passes, never one: `plan` decides, `apply` re-verifies. An action whose file changed since it was planned
(size, hash, a newer write in its task folder) is REFUSED, not re-decided -- run `plan` again.

What it never touches, whatever the config says: saves (.ess/.skse) and anything under a folder whose name
contains "backup"; git-tracked files; every file a project doc links to; and any task folder written to within
`active_hours` (another session is probably still working there).

Removal goes to the Recycle Bin, never a permanent delete, with no exceptions (Windows: the shell's own delete with
FOF_ALLOWUNDO). An item too big for the bin gets Windows' own "delete permanently?" prompt; "No" leaves it in place.
The bin frees no disk space until it is emptied; that is the user's call. A file the bin refuses is reported
FAILED and left in place. Duplicates are not removed at all: they become NTFS hard links to one copy, marked
read-only so an in-place edit cannot silently change every path at once.

Accounting: `report` and `plan` print every scanned byte in exactly one row, the rows sum to the total, and the
row of bytes it leaves alone prints even at zero.

Exit: 0 ok · 1 an action was refused or failed · 2 bad usage or config.
"""

from __future__ import annotations

import fnmatch
import gzip
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TOOLKIT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = TOOLKIT_ROOT / ".claude" / "housekeep.json"

# The guard's own list (tools/skyguard/writes.py SAVE_EXTS). Copied, not imported, so this tool also runs in an
# install that predates the guard package; tests/test_housekeep.py fails if the two drift.
SAVE_EXTS = (".ess", ".skse")

CLASSES = ("recreatable", "build", "link", "gzip", "launch", "snapshot", "scratch", "archive", "expire")
# Every child process is started without a console window. 2026-10-29: the daily task runs under pythonw (no
# console), so each PowerShell recycle call opened its own visible window -- one per archived folder, hundreds of
# them flashing on the user's desktop.
NO_WINDOW = {"creationflags": 0x08000000} if os.name == "nt" else {}      # CREATE_NO_WINDOW
RECYCLE_BATCH = 500                                                        # paths per PowerShell call
DEFAULTS = {
    "agent_output": [],               # task-folder roots (relative to the toolkit root, or absolute)
    "nested_task_roots": [],          # roots one level down whose children are task folders of their own
    "live_roots": ["Data"],           # a byte-identical copy of a file here is "recreatable"
    "state_exts": [".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".json", ".jsonl", ".ini", ".toml",
                   ".yaml", ".yml", ".xml", ".cfg", ".log", ".txt"],   # live files that change: never a reference
    "launch_logs": [],                # folders of launch-YYYYMMDD-HHMMSS/ log captures
    "legacy_snapshots": [],           # folders of YYYYMMDD_HHMMSS/ snapshot copies (the 3.x snapshot hook)
    "scratch": [],                    # whole folders to recycle, named one by one
    "evidence_docs": ["CLAUDE.md", "AGENTS.md", "KNOWLEDGEBASE.md", "KNOWLEDGEBASE.local.md"],
    "protected_globs": [],
    "build_dir_names": ["build", "build-sdk", "build-vcpkg", "native-bootstrap", "native-prebuilt",
                        "CMakeFiles", "vcpkg_installed", "buildtrees"],
    "build_exts": [".obj", ".pch", ".ilk", ".iobj", ".ipdb", ".tlog", ".idb"],
    "active_hours": 48,
    # code fingerprint (housekeep_daily.code_sha256: housekeep, housekeep_archive, housekeep_daily) whose
    # dry-run output the user has reviewed. housekeep_daily.py APPLIES only
    # when the code still matches; otherwise it dry-runs and writes REVIEW-NEEDED.md (2026-10-28: a new rule acted
    # on its first run, unreviewed, and took a library a live build needed).
    "approved_sha256": "",
    # ---- archive: a DONE task folder is zipped to archive_root, verified there, then its payload recycled.
    # No extra storage is assumed: the default destination is a LOCAL folder on the same drive (text evidence
    # compresses ~5-10x), and local zips expire after archive_retention_days. A NAS is an option, never a crutch.
    "archive_enabled": True,
    "archive_root": "",               # empty = <root>/.claude/archive (local); or another drive / NAS share
    "archive_retention_days": 90,     # zips under archive_root older than this -> Recycle Bin; 0 = keep forever
    "archive_roots": {},              # {folder: idle_days}: its child folders are archived once done
    "archive_after_days": 14,         # agent_output task folders: done and idle this long
    "min_idle_days": 7,               # no WRITE for this long; sooner only via .housekeep-closed
    "read_veto_hours": 48,            # any READ this recent blocks archiving (a build linking, a session reading)
    "budget_gib": 0,                  # agent output above this: done folders idle >= min_idle_days go, oldest first
    "pin_warn_gib": 1,                # a .housekeep-keep folder this big is named in the session note
    "growth_warn_gib": 5,             # agent output grew more than this since the previous daily run: session note
    # The daily run (tools/housekeep_daily.py) is started only by a Claude Code session, announced in it first.
    "daily_enabled": True,
    "daily_every_hours": 20,          # at most one run per this many hours
    "daily_minutes": 10,              # time box: no new action starts after this; the rest waits for the next run
    "pause_while_running": ["SkyrimVR.exe", "SkyrimSE.exe", "vrserver.exe", "vrcompositor.exe"],
    "min_bytes": 1048576,             # recreatable / link candidates below this are not worth a hash
    "gzip_after_days": 7,
    "recycle_after_days": 90,
    "free_floor_gib": 0,              # report warns within 20 GiB of it; 0 = no floor
    # The toolkit's OWN temp dirs: immediate children of the system temp dir whose name starts with one of these.
    # tests/test_temp_hygiene.py fails when a prefix the code creates is missing here.
    "temp_prefixes": ["skyrim-harness-", "python-free-path-", "toolkit-nojq-", "toolkit-plugins-",
                      "toolkit-localappdata-", "toolkit-loadorder-", "toolkit-precedence-", "t11-nohome-",
                      "p6t11-nohome-", "p6t12-nohome-", "p6fixA-nohome-", "p6fixA-i1-", "p6fixA-b1-",
                      "hc-xedit-", "modinst_"],
    "temp_after_hours": 24,           # nothing in the dir or its direct children newer than this
    "sweep_every_hours": 6,           # session start launches a sweep at most this often
    "codex_sessions_warn_gib": 5,     # session note when ~/.codex/sessions passes this
    "worktrees_warn": 10,             # session note when the toolkit repo has more worktrees than this
}
LAUNCH_RE = re.compile(r"^launch-(\d{8})-(\d{6})$")
STAMP_RE = re.compile(r"^(\d{8})_(\d{6})$")
LINK_TAIL = r"/[^)\s\]\"'`<>|]+"
GIB = 1024 ** 3


# --------------------------------------------------------------------------- config and paths

def load_config(path: Path | None) -> dict:
    cfg = dict(DEFAULTS)
    p = Path(path) if path else DEFAULT_CONFIG
    if p.is_file():
        data = json.loads(p.read_text(encoding="utf-8"))
        unknown = sorted(set(data) - set(DEFAULTS) - {"root", "_comment"})
        if unknown:
            raise ValueError("unknown config key(s) in {}: {}".format(p, ", ".join(unknown)))
        cfg.update(data)
    elif path:
        raise ValueError("config not found: {}".format(p))
    cfg["root"] = Path(cfg.get("root") or TOOLKIT_ROOT)
    if not cfg["archive_root"]:
        cfg["archive_root"] = str(cfg["root"] / ".claude" / "archive")
    return cfg


def _abs(cfg, p) -> Path:
    q = Path(p)
    return q if q.is_absolute() else cfg["root"] / q


def _key(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def blake(path, chunk=1 << 20) -> str:
    """Content hash that leaves the file's access time as it found it: housekeep's own reads must not make a folder
    look in use (activity, below, counts reads)."""
    try:
        st = os.stat(path)
    except OSError:
        st = None
    d = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            d.update(b)
    if st is not None:
        try:
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        except OSError:                  # read-only (a housekeep hard link): its access time may move; safe direction
            pass
    return d.hexdigest()


def activity(folder: Path) -> tuple:
    """(newest write, newest read) of any file under `folder`. NTFS keeps last-access times on this machine (fsutil
    disablelastaccess = 2, verified 2026-10-28), so a build linking a library or a session reading its staging shows
    up. 2026-10-28: a write-only signal let a cleanup take a .lib a live build was linking. Reads are noisy too
    (a disk scan reads whole trees), so a read only VETOES for read_veto_hours; idleness is measured on writes."""
    w = r = 0.0
    for dp, _, fn in os.walk(folder):
        for f in fn:
            try:
                st = os.stat(os.path.join(dp, f))
            except OSError:
                continue
            w, r = max(w, st.st_mtime), max(r, st.st_atime)
    return w, r


def newest_activity(folder: Path) -> float:
    return max(activity(folder))


_READS_RECORDED = {}


def reads_recorded(folder: Path) -> bool:
    """Does this volume update last-access times when a file is read? Probed, not assumed: NTFS can switch it off
    (fsutil disablelastaccess) and Linux may keep atime coarsely. Without it the read veto silently does nothing, so
    archiving falls back to folders their session marked .housekeep-closed. The probe creates and deletes nothing:
    it backdates one existing small file's access time, reads it, looks, and restores both times exactly."""
    try:
        dev = os.stat(folder).st_dev
    except OSError:
        return False
    if dev not in _READS_RECORDED:
        ok = False
        probe = next((Path(dp) / f for dp, _, fn in os.walk(folder) for f in fn
                      if 0 < _size(Path(dp) / f) <= 1 << 20), None)
        if probe is not None:
            try:
                st = os.stat(probe)
                old = time.time() - 3 * DAY
                try:
                    os.utime(probe, (old, st.st_mtime))
                    with open(probe, "rb") as fh:
                        fh.read(1)
                    ok = os.stat(probe).st_atime > old + 3600
                finally:
                    os.utime(probe, ns=(st.st_atime_ns, st.st_mtime_ns))
            except OSError:
                ok = False
        _READS_RECORDED[dev] = ok
    return _READS_RECORDED[dev]


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def newest_mtime(folder: Path) -> float:
    newest = 0.0
    for dp, _, fn in os.walk(folder):
        for f in fn:
            try:
                newest = max(newest, os.stat(os.path.join(dp, f)).st_mtime)
            except OSError:
                pass
    return newest


def fingerprint(folder: Path):
    """(file count, total bytes, newest mtime) -- the cheap 'has this folder changed' check for whole-folder
    actions. Hashing 245k snapshot files twice would cost minutes for no extra safety on a folder nothing writes."""
    n = size = 0
    newest = 0.0
    for dp, _, fn in os.walk(folder):
        for f in fn:
            try:
                st = os.stat(os.path.join(dp, f))
            except OSError:
                continue
            n += 1
            size += st.st_size
            newest = max(newest, st.st_mtime)
    return [n, size, round(newest, 3)]


def tracked_files(root: Path) -> set:
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, timeout=120,
                             **NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return set()
    if out.returncode != 0:
        return set()
    return {_key(root / p) for p in out.stdout.decode("utf-8", "replace").split("\0") if p}


def evidence_links(cfg) -> set:
    """Every agent-output path a project doc links to (written relative to the root, as docs link them). A linked
    file is never planned for anything."""
    links = set()
    prefixes = []
    for r in cfg["agent_output"]:
        try:
            rel = os.path.relpath(os.path.abspath(str(_abs(cfg, r))), os.path.abspath(str(cfg["root"])))
        except ValueError:                       # another drive: docs cannot link it relatively
            continue
        if rel.split(os.sep)[0] != "..":
            prefixes.append(re.escape(rel.replace("\\", "/")))
    if not prefixes:
        return links
    link_re = re.compile("(?:{})".format("|".join(prefixes)) + LINK_TAIL)
    for doc in cfg["evidence_docs"]:
        p = _abs(cfg, doc)
        if not p.is_file():
            continue
        for m in link_re.findall(p.read_text(encoding="utf-8", errors="replace")):
            m = m.rstrip(".,;:*")
            links.add(_key(cfg["root"] / m))
    return links


class Guard:
    """The Forever class and the per-run exclusions, in one place: plan and apply both ask it."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.tracked = tracked_files(cfg["root"])
        self.links = evidence_links(cfg)
        self.globs = [g.lower() for g in cfg["protected_globs"]]

    def why_protected(self, path) -> str:
        p = str(path)
        low = p.replace("\\", "/").lower()
        if low.endswith(SAVE_EXTS):
            return "save"
        # Folder names BELOW the configured root only: a root that itself sits under ".../backups/..." must not
        # make every file look protected (or, read the other way, make the rule meaningless).
        try:
            rel = os.path.relpath(os.path.abspath(p), os.path.abspath(str(self.cfg["root"])))
        except ValueError:                       # another drive: no relative form, judge the whole path
            rel = p
        rel_parts = rel.replace("\\", "/").lower().split("/")
        if not rel.startswith(".."):
            if any("backup" in part for part in rel_parts[:-1]):
                return "backup folder"
        elif any("backup" in part for part in low.split("/")[:-1]):
            return "backup folder"
        k = _key(p)
        if k in self.tracked:
            return "git-tracked"
        if k in self.links:
            return "linked evidence"
        if any(fnmatch.fnmatch(low, g) for g in self.globs):
            return "protected_globs"
        return ""


# --------------------------------------------------------------------------- task folders

def task_folders(cfg):
    """[(path, state)] under every agent_output root; state is "idle", "active" or "loose".

    A folder directly in a root is a task folder, judged active as a whole. A folder named in nested_task_roots
    is a root of its own (recursively). A LOOSE file -- one sitting directly in a root, not inside a task folder --
    is never a candidate: that is where shared live state lives (a service's events.jsonl, a status JSON a session
    rewrites). Making an append-only log read-only would break its next write, and a log appended once a week
    walks straight through the activity gate."""
    out = []
    cutoff = time.time() - cfg["active_hours"] * 3600
    nested = {_key(_abs(cfg, n)) for n in cfg["nested_task_roots"]}

    def visit(folder: Path):
        for e in sorted(os.scandir(folder), key=lambda e: e.name):
            p = Path(e.path)
            if e.is_dir(follow_symlinks=False) and _key(p) in nested:
                visit(p)
            elif e.is_dir(follow_symlinks=False):
                out.append((p, "active" if newest_activity(p) > cutoff else "idle"))
            else:
                out.append((p, "loose"))

    for r in cfg["agent_output"]:
        root = _abs(cfg, r)
        if root.is_dir():
            visit(root)
    return out


# --------------------------------------------------------------------------- archive: is this work DONE?
DAY = 86400
KEEP_PIN, CLOSED_MARK = ".housekeep-keep", ".housekeep-closed"


def live_state_tokens(cfg) -> set:
    """Backticked names in the `## Live state` block of the instruction files: work a session is still holding."""
    toks = set()
    for doc in ("CLAUDE.md", "AGENTS.md"):
        try:
            text = (cfg["root"] / doc).read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.search(r"^## Live state.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
        if m:                                   # entry lines only: the block's own instructions name example paths
            entries = "\n".join(ln for ln in m.group(1).splitlines() if ln.lstrip().startswith(("- ", "* ")))
            toks |= {t.replace("\\", "/").rstrip("/").lower() for t in re.findall(r"`([^`\n]+)`", entries)}
    return toks


def rb_job_cwds() -> list:
    p = Path(os.path.expanduser("~")) / ".claude" / "tools" / "resource-budget" / "ledger.json"
    try:
        return [_key(e["cwd"]) for e in json.loads(p.read_text(encoding="utf-8")).get("entries", []) if e.get("cwd")]
    except (OSError, ValueError, AttributeError, TypeError):
        return []


def is_done(task: Path, idle_days: float, tokens: set, cwds: list, guard) -> str:
    """'' when `task` is finished work; otherwise why it is still in use or must stay. idle_days is measured by the
    caller. Written-recently is the weakest signal (a read leaves no mtime), so every other gate is independent."""
    if (task / KEEP_PIN).exists():
        return "pinned (.housekeep-keep)"
    name = task.name.lower()
    path = str(task).replace("\\", "/").lower()
    if any(t == name or path.endswith("/" + t) or t.endswith("/" + name) or (len(t) > 8 and t in path)
           for t in tokens):
        return "named in the Live state block"
    k = _key(task)
    if any(c == k or c.startswith(k + os.sep) for c in cwds):
        return "a running rb job works here"
    for dp, dirs, files in os.walk(task):
        if ".git" in dirs:
            return "a git repository"
        for f in files:
            if f.lower().endswith(SAVE_EXTS):
                return "holds a save"
            if guard.globs and guard.why_protected(os.path.join(dp, f)) == "protected_globs":
                return "holds a protected_globs file"
    return ""


def archive_selection(cfg, guard, tasks, now) -> tuple:
    """([action], rows) for every done folder due: closed first, then by age; over budget, down to min_idle_days."""
    tokens, cwds = live_state_tokens(cfg), rb_job_cwds()
    # from_root: the folder lives under archive_roots, which the file-level loop never scans, so its kept bytes are
    # counted here; agent_output folders that stay are counted by that loop instead (rows must sum to the scan).
    cands = [(t, cfg["archive_after_days"], False) for t, s in tasks if s != "loose"]
    for root, days in cfg["archive_roots"].items():
        r = _abs(cfg, root)
        if r.is_dir():
            cands += [(Path(e.path), days, True) for e in os.scandir(r) if e.is_dir(follow_symlinks=False)]
    info, total = [], 0
    actions, rows, pins = [], {}, []

    def keep(row, n, from_root):
        if from_root:
            rows[row] = rows.get(row, 0) + n

    for t, days, from_root in cands:
        fp = fingerprint(t)
        total += fp[1]
        if (t / "ARCHIVED.md").exists() and fp[1] < 1 << 20:
            keep("kept: already archived (stub only)", fp[1], from_root)
            continue
        wrote, read = activity(t)
        idle = (now - wrote) / DAY if wrote else 0.0           # idleness: writes
        if read > now - cfg["read_veto_hours"] * 3600:         # but anything reading here right now blocks it
            keep("kept: archive folder read within read_veto_hours", fp[1], from_root)
            continue
        closed = (t / CLOSED_MARK).exists()
        if not closed and not reads_recorded(t.parent):        # no read signal on this drive: only explicit "done"
            keep("kept: archive needs .housekeep-closed (this drive does not record reads)", fp[1], from_root)
            continue
        info.append((not closed, -idle, t, days, fp, idle, closed, from_root))
    info.sort(key=lambda x: (x[0], x[1]))
    measured = total
    over = cfg["budget_gib"] and total > cfg["budget_gib"] * GIB
    for _, _, t, days, fp, idle, closed, from_root in info:
        if (t / KEEP_PIN).exists() and fp[1] >= cfg["pin_warn_gib"] * GIB:
            pins.append((t, fp[1]))
        # The budget lowers the bar for agent_output only; an archive_roots entry's own age is the user's number.
        due = closed or idle >= days or (over and not from_root and idle >= cfg["min_idle_days"])
        if not due or idle < cfg["min_idle_days"] and not closed:
            keep("kept: archive folder not due yet", fp[1], from_root)
            continue
        why = is_done(t, idle, tokens, cwds, guard)
        if why:
            keep("kept: archive folder not done ({})".format(why), fp[1], from_root)
            continue
        label = re.sub(r"[^\w.-]+", "_", t.parent.name) or "root"
        actions.append({"class": "archive", "path": str(t), "bytes": fp[1], "fingerprint": list(fp),
                        "nas": str(Path(cfg["archive_root"]) / label), "idle_days": round(idle, 1),
                        "closed": closed})
        if over:
            total -= fp[1]
            over = total > cfg["budget_gib"] * GIB
    return actions, rows, pins, measured


def walk_files(target: Path):
    if target.is_file():
        yield target
        return
    for dp, _, fn in os.walk(target):
        for f in fn:
            yield Path(dp) / f


# --------------------------------------------------------------------------- planning

def live_index(cfg):
    """{size: [paths]} of every CONTENT file under the live roots at or above min_bytes -- stat only, hashed lazily.

    A state file (a database, a config, a log) is never a reference copy: it equals its snapshot only until its
    owner next writes, and from then on the snapshot is the only record of that state. MEASURED 2026-10-18: 15
    snapshots of the live SkyrimNet memory database were byte-identical to it at plan time."""
    idx = {}
    state = {e.lower() for e in cfg["state_exts"]}
    for r in cfg["live_roots"]:
        root = _abs(cfg, r)
        if not root.is_dir():
            continue
        for dp, _, fn in os.walk(root):
            for f in fn:
                if os.path.splitext(f)[1].lower() in state:
                    continue
                p = os.path.join(dp, f)
                try:
                    s = os.path.getsize(p)
                except OSError:
                    continue
                if s >= cfg["min_bytes"]:
                    idx.setdefault(s, []).append(p)
    return idx


_CMAKE_DIRS = {}
BUILD_PRODUCTS = {".lib", ".dll", ".exe", ".pdb", ".exp", ".a", ".so", ".pex", ".esp", ".esm", ".esl"}


def _cmake_tree(folder: Path) -> bool:
    k = _key(folder)
    if k not in _CMAKE_DIRS:
        _CMAKE_DIRS[k] = (folder / "CMakeCache.txt").is_file()
    return _CMAKE_DIRS[k]


def is_build(path: Path, cfg) -> bool:
    """A build intermediate (build_exts) inside a build tree: a folder named in build_dir_names, or -- whatever its
    name -- one holding a CMakeCache.txt (2026-10-28: `build-vr`, `build-ae`, `sdk14-build` held 3.7 GiB unseen).
    Build PRODUCTS are never intermediates, whatever build_exts says: another build links a .lib, a test loads a
    .dll (2026-10-28: a recycled CommonLibSSE.lib broke a live build)."""
    if path.suffix.lower() in BUILD_PRODUCTS:
        return False
    if path.suffix.lower() not in {e.lower() for e in cfg["build_exts"]}:
        return False
    names = {n.lower() for n in cfg["build_dir_names"]}
    if any(x.lower() in names for x in path.parts[:-1]):
        return True
    return any(_cmake_tree(p) for p in path.parents)


def plan(cfg, guard: Guard | None = None, now: float | None = None, only=None) -> dict:
    """Every action, with the hash it was planned on. `only` (a set of CLASSES) skips the scans no requested class
    needs -- the launch watcher plans gzip/launch without hashing every agent-output file -- and counts a planned
    action of an unrequested class as kept, so the rows still sum to what was scanned."""
    now = time.time() if now is None else now
    guard = guard or Guard(cfg)
    actions, rows = [], {}
    wants = (lambda *classes: True) if not only else (lambda *classes: bool(set(classes) & set(only)))

    def count(row, n):
        rows[row] = rows.get(row, 0) + n

    def act(action, row):
        if only and action["class"] not in only:
            count("kept: class not requested (--only)", action["bytes"])
        else:
            actions.append(action)
            count(row, action["bytes"])

    # ---- agent output: recreatable, build, link
    all_tasks = task_folders(cfg) if wants("recreatable", "build", "link", "archive") else []
    # ---- archive first: a folder going to the NAS whole is not also picked over file by file
    archived = set()
    plan_pins, agent_total = [], None
    if cfg["archive_enabled"] and wants("archive"):
        arch, arch_rows, plan_pins, agent_total = archive_selection(cfg, guard, all_tasks, now)
        for k, v in arch_rows.items():
            count(k, v)
        for a in arch:
            act(a, "plan: done folder -> zip to NAS, verify, payload to Recycle Bin")
            archived.add(_key(a["path"]))
    tasks = [(t, s) for t, s in all_tasks if _key(t) not in archived] if wants("recreatable", "build", "link") else []
    live = live_index(cfg) if tasks else {}
    live_hash = {}
    by_size = {}
    for task, state in tasks:
        for f in walk_files(task):
            try:
                size = f.stat().st_size
            except OSError:
                continue
            if state == "loose":
                count("kept: loose file in a root (shared live state)", size)
                continue
            # Nothing inside an active task is a candidate, build output included. 2026-10-28: a rule that took build
            # output from a subfolder idle 12 h recycled a CommonLibSSE.lib another build was still linking -- a read
            # leaves no mtime, so "not written lately" never meant "done". Done = the whole task idle, or closed.
            if state == "active":
                count("kept: task folder active (written within active_hours)", size)
                continue
            why = guard.why_protected(f)
            if why:
                count("kept: protected ({})".format(why), size)
                continue
            if is_build(f, cfg):
                act({"class": "build", "path": str(f), "bytes": size, "hash": blake(f),
                                "task": str(task)}, "plan: build output -> Recycle Bin")
                continue
            if size >= cfg["min_bytes"] and size in live:
                h = blake(f)
                match = None
                for lp in live[size]:
                    if lp not in live_hash:
                        live_hash[lp] = blake(lp)
                    if live_hash[lp] == h:
                        match = lp
                        break
                if match:
                    act({"class": "recreatable", "path": str(f), "bytes": size, "hash": h,
                                    "live": match, "task": str(task)}, "plan: copy of a live file -> Recycle Bin")
                    continue
                by_size.setdefault(size, []).append((f, task, h))
                continue
            if size >= cfg["min_bytes"]:
                by_size.setdefault(size, []).append((f, task, None))
                continue
            count("kept: below min_bytes / not duplicated", size)

    for size, group in sorted(by_size.items()):
        if len(group) < 2:
            count("kept: below min_bytes / not duplicated", size * len(group))
            continue
        hashed = {}
        for f, task, h in group:
            hashed.setdefault(h or blake(f), []).append((f, task))
        for h, files in hashed.items():
            files.sort(key=lambda x: str(x[0]).lower())
            canon = files[0][0]
            count("kept: canonical copy of a duplicate set" if len(files) > 1
                  else "kept: below min_bytes / not duplicated", size)
            for f, task in files[1:]:
                try:
                    same = os.stat(f).st_ino == os.stat(canon).st_ino and os.stat(f).st_ino != 0
                except OSError:
                    same = False
                if same:
                    count("kept: already hard-linked", size)
                    continue
                act({"class": "link", "path": str(f), "bytes": size, "hash": h,
                     "canonical": str(canon), "task": str(task)},
                    "plan: duplicate -> hard link (bytes freed)")

    # ---- launch logs
    for r in (cfg["launch_logs"] if wants("gzip", "launch") else []):
        root = _abs(cfg, r)
        if not root.is_dir():
            continue
        for e in sorted(os.scandir(root), key=lambda e: e.name):
            p = Path(e.path)
            size = sum(f.stat().st_size for f in walk_files(p)) if e.is_dir() else e.stat().st_size
            m = LAUNCH_RE.match(e.name) if e.is_dir() else None
            if not m:
                count("kept: launch log, labelled or not a launch folder (Forever)", size)
                continue
            age = (now - time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S"))) / 86400
            if age >= cfg["recycle_after_days"]:
                act({"class": "launch", "path": str(p), "bytes": size, "fingerprint": fingerprint(p)},
                    "plan: launch folder past recycle_after_days -> Recycle Bin")
            elif age >= cfg["gzip_after_days"]:
                for f in walk_files(p):
                    s = f.stat().st_size
                    if f.suffix.lower() == ".gz" or s < 65536:
                        count("kept: launch log, recent or already compressed", s)
                        continue
                    act({"class": "gzip", "path": str(f), "bytes": s, "hash": blake(f)},
                        "plan: launch log -> gzip (original to Recycle Bin)")
            else:
                count("kept: launch log, recent or already compressed", size)

    # ---- legacy snapshot stamps: recycle a stamp whose content repeats the last one kept
    for r in (cfg["legacy_snapshots"] if wants("snapshot") else []):
        root = _abs(cfg, r)
        if not root.is_dir():
            continue
        stamps = sorted((e for e in os.scandir(root) if e.is_dir() and STAMP_RE.match(e.name)),
                        key=lambda e: e.name)
        prev = None
        for i, e in enumerate(stamps):
            p = Path(e.path)
            files = sorted(walk_files(p))
            sig = sorted((str(f.relative_to(p)).lower(), f.stat().st_size, blake(f)) for f in files)
            size = sum(s for _, s, _ in sig)
            if sig == prev and i != len(stamps) - 1:
                act({"class": "snapshot", "path": str(p), "bytes": size, "fingerprint": fingerprint(p)},
                    "plan: snapshot repeating the previous one -> Recycle Bin")
            else:
                count("kept: snapshot with distinct content (or the newest)", size)
                prev = sig

    # ---- archive retention: the archive must not become the next pile
    days = cfg["archive_retention_days"]
    aroot = Path(cfg["archive_root"])
    if days and wants("expire") and aroot.is_dir():
        for dp, _, fn in os.walk(aroot):
            for f in fn:
                p = Path(dp) / f
                if not f.lower().endswith(".zip"):
                    continue
                st = p.stat()
                if now - st.st_mtime >= days * DAY:
                    act({"class": "expire", "path": str(p), "bytes": st.st_size, "mtime": st.st_mtime},
                        "plan: archive zip past archive_retention_days -> Recycle Bin")
                else:
                    count("kept: archive zip within archive_retention_days", st.st_size)

    # ---- named scratch folders
    for s in (cfg["scratch"] if wants("scratch") else []):
        p = Path(s)
        if p.is_dir():
            fp = fingerprint(p)
            act({"class": "scratch", "path": str(p), "bytes": fp[1], "fingerprint": fp},
                "plan: named scratch folder -> Recycle Bin")

    return {"created": now, "root": str(cfg["root"]), "actions": actions, "rows": rows,
            "large_pins": [[str(p), b] for p, b in plan_pins], "agent_total": agent_total}


def print_rows(rows: dict, title: str):
    total = sum(rows.values())
    print("{}: {:.2f} GiB scanned".format(title, total / GIB))
    for k in sorted(rows, key=lambda k: (not k.startswith("plan"), k)):
        print("  {:>9.2f} GiB  {}".format(rows[k] / GIB, k))
    kept = sum(v for k, v in rows.items() if not k.startswith("plan"))
    print("  {:>9.2f} GiB  (left alone, all 'kept' rows)".format(kept / GIB))
    planned = total - kept
    print("  {:>9.2f} GiB  (planned)   rows sum: {}".format(planned / GIB, "OK" if planned + kept == total
                                                              else "ACCOUNTING MISMATCH"))


# --------------------------------------------------------------------------- applying

def recycle(paths) -> dict:
    """{path: None | error} -- one PowerShell process for the whole batch. Recycle Bin only, never a delete."""
    paths = [str(p) for p in paths]
    if not paths:
        return {}
    if os.name != "nt":
        raise RuntimeError("Recycle Bin removal is implemented for Windows only")
    # 2026-10-29: items the bin cannot take made Windows pop "delete permanently?" dialogs on the user's desktop during
    # an unattended run. Those items are never handed to the shell: they stay where they are and are reported.
    refused = {p: bin_refusal(p) for p in paths}
    refused = {p: why for p, why in refused.items() if why}
    paths = [p for p in paths if p not in refused]
    if not paths:
        return refused
    # The shell's own delete, NOT Microsoft.VisualBasic's DeleteDirectory(..., 'OnlyErrorDialogs', ...): that UI option
    # suppresses confirmations, and the confirmation it suppresses is the one Windows shows when an item is too big for
    # the bin and would be destroyed instead. FOF_WANTNUKEWARNING (0x4000) brings exactly that prompt back while
    # FOF_NOCONFIRMATION (0x10) still skips "move to the Recycle Bin?". FOF_ALLOWUNDO (0x40) is the Recycle Bin.
    # The user's answer is the only way anything is ever permanently deleted; "No" leaves the item in place.
    script = (
        "Add-Type -TypeDefinition @'\n"
        "using System; using System.Runtime.InteropServices;\n"
        "public static class HkBin {\n"
        " [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]\n"
        " struct Op { public IntPtr hwnd; public uint wFunc; public string pFrom; public string pTo; public ushort fFlags;\n"
        "  public bool fAnyOperationsAborted; public IntPtr hNameMappings; public string lpszProgressTitle; }\n"
        " [DllImport(\"shell32.dll\", CharSet = CharSet.Unicode)] static extern int SHFileOperationW(ref Op op);\n"
        " public static string Recycle(string p) {\n"
        "  var op = new Op { wFunc = 3, pFrom = p + \"\\0\\0\", fFlags = 0x4000 | 0x40 | 0x10 | 0x200 | 0x4 };\n"
        "  int rc = SHFileOperationW(ref op);\n"
        "  if (op.fAnyOperationsAborted) return \"declined: left in place\";\n"
        "  return rc == 0 ? \"\" : \"shell error 0x\" + rc.ToString(\"X\"); } }\n"
        "'@;"
        "[Console]::InputEncoding = [Text.Encoding]::UTF8; [Console]::OutputEncoding = [Text.Encoding]::UTF8;"
        "foreach ($p in [Console]::In.ReadToEnd().Split([char]10)) { $p = $p.TrimEnd([char]13); if ($p -eq '') { continue };"
        " $e = [HkBin]::Recycle($p);"
        " if ($e -eq '') { [Console]::Out.WriteLine('OK' + [char]9 + $p) }"
        " else { [Console]::Out.WriteLine('ERR' + [char]9 + $p + [char]9 + $e) } }")
    # The list goes on stdin: under -Command, trailing arguments are NOT $args -- PowerShell appends them to the
    # command text and runs them (measured: $args.Count == 0). A list file would be one more thing to delete.
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], input="\n".join(paths),
                         capture_output=True, text=True, encoding="utf-8", timeout=3600, **NO_WINDOW)
    silent = "no result reported" + (" (powershell: {})".format(out.stderr.strip()[:300]) if out.stderr.strip() else "")
    result = {p: silent for p in paths}
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1] in result:
            result[parts[1]] = None if parts[0] == "OK" else (parts[2] if len(parts) > 2 else "error")
    for p in paths:                     # the bin's word is not the proof: the path must actually be gone
        if result[p] is None and os.path.exists(p):
            result[p] = "still present after a reported recycle"
    result.update(refused)
    return result


MAX_BIN_PATH = 259                      # Explorer's recycle path limit; longer paths are offered "delete permanently"


def bin_refusal(p) -> str:
    """'' when the Recycle Bin can take `p` without asking; otherwise why it would prompt (and so is left alone)."""
    s = os.path.abspath(str(p))
    if s.startswith("\\\\") or _drive_type(s) == 4:                    # DRIVE_REMOTE: a share has no Recycle Bin
        return "left in place: network location (no Recycle Bin)"
    longest = len(s)
    size = 0
    if os.path.isdir(s):
        for dp, _, fn in os.walk(s):
            for f in fn:
                q = os.path.join(dp, f)
                longest = max(longest, len(q))
                try:
                    size += os.stat(q).st_size
                except OSError:
                    pass
    else:
        try:
            size = os.stat(s).st_size
        except OSError:
            pass
    if longest > MAX_BIN_PATH:
        return "left in place: a path is longer than {} characters (the bin would ask to delete it)".format(MAX_BIN_PATH)
    cap = bin_capacity(s)
    if cap is not None and size > cap:
        return "left in place: {:.1f} GiB is bigger than this drive's Recycle Bin ({:.1f} GiB)".format(
            size / GIB, cap / GIB)
    return ""


def _drive_type(path: str) -> int:
    try:
        import ctypes
        return ctypes.windll.kernel32.GetDriveTypeW(os.path.splitdrive(path)[0] + "\\")
    except (OSError, AttributeError):
        return 0


def bin_capacity(path: str):
    """This volume's Recycle Bin size limit in bytes (0 if it deletes immediately); None when it cannot be read."""
    try:
        import ctypes
        import winreg
        buf = ctypes.create_unicode_buffer(64)
        if not ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(os.path.splitdrive(path)[0] + "\\", buf, 64):
            return None
        guid = buf.value[buf.value.index("{"):buf.value.index("}") + 1]
        key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume" + "\\" + guid
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            try:
                if winreg.QueryValueEx(k, "NukeOnDelete")[0]:
                    return 0
            except OSError:
                pass
            return int(winreg.QueryValueEx(k, "MaxCapacity")[0]) * 1024 * 1024
    except (OSError, ValueError, AttributeError, ImportError):
        return None


def _set_readonly(path):
    os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)


class _Memo:
    """Per-apply caches: a live master is hashed once, not once per copy of it; a task folder is walked once."""

    def __init__(self):
        self.hashes, self.newest = {}, {}

    def hash(self, path):
        st = os.stat(path)
        k = (_key(path), st.st_size, st.st_mtime_ns)
        if k not in self.hashes:
            self.hashes[k] = blake(path)
        return self.hashes[k]

    def task_newest(self, task):
        k = _key(task)
        if k not in self.newest:
            self.newest[k] = newest_activity(Path(task))
        return self.newest[k]


def check(action, cfg, guard: Guard, now: float, memo: _Memo | None = None) -> str:
    """'' when the action may run; otherwise why it is refused. Everything plan() decided is re-proved here."""
    memo = memo or _Memo()
    p = Path(action["path"])
    if not p.exists():
        return "gone since the plan"
    if action["class"] in ("recreatable", "build", "link", "gzip"):
        why = guard.why_protected(p)
        if why:
            return "protected ({})".format(why)
        if p.stat().st_size != action["bytes"] or blake(p) != action["hash"]:
            return "changed since the plan"
    if action["class"] in ("recreatable", "build", "link"):
        if memo.task_newest(action["task"]) > now - cfg["active_hours"] * 3600:
            return "task folder written to since the plan (active)"
    if action["class"] == "recreatable":
        if not Path(action["live"]).is_file() or memo.hash(action["live"]) != action["hash"]:
            return "live file no longer identical"
    if action["class"] == "link":
        c = Path(action["canonical"])
        if not c.is_file() or memo.hash(c) != action["hash"]:
            return "canonical copy changed or gone"
        if guard.why_protected(c) in ("save", "git-tracked"):
            return "canonical copy is protected"
    if action["class"] == "archive":
        if list(fingerprint(p)) != list(action["fingerprint"]):
            return "folder changed since the plan"
        wrote, read = activity(p)
        if read > now - cfg["read_veto_hours"] * 3600:
            return "something read here since the plan"
        if (now - wrote) / DAY < cfg["min_idle_days"] and not action.get("closed"):
            return "something wrote here since the plan"
        why = is_done(p, action["idle_days"], live_state_tokens(cfg), rb_job_cwds(), guard)
        if why:
            return "no longer done ({})".format(why)
        if not cfg["archive_enabled"]:
            return "archiving is off"
        return ""
    if action["class"] == "expire":
        st = p.stat()
        if st.st_mtime != action["mtime"] or st.st_size != action["bytes"]:
            return "archive zip changed since the plan"
        return ""
    if action["class"] in ("launch", "snapshot", "scratch"):
        # A whole-folder action must never carry a save into the bin with it, whatever the plan or config says.
        if p.is_dir() and any(f.lower().endswith(SAVE_EXTS) for _, _, fn in os.walk(p) for f in fn):
            return "protected (folder holds a save)"
        if fingerprint(p) != action["fingerprint"]:
            return "folder changed since the plan"
    return ""


def link(action):
    dup, canon = Path(action["path"]), Path(action["canonical"])
    tmp = dup.with_name(dup.name + ".housekeep-link")
    if tmp.exists():
        raise OSError("leftover {} -- inspect it by hand".format(tmp))
    os.link(canon, tmp)
    if os.stat(tmp).st_ino != os.stat(canon).st_ino or blake(tmp) != action["hash"]:
        recycle([tmp])                                           # our own just-made link -- still the bin, never a delete
        raise OSError("the new link does not resolve to the canonical bytes")
    if not os.access(dup, os.W_OK):
        os.chmod(dup, stat.S_IWRITE | stat.S_IREAD)
    os.replace(tmp, dup)
    _set_readonly(canon)                                          # one attribute, shared by every link


def compress(action) -> Path:
    src = Path(action["path"])
    gz = src.with_name(src.name + ".gz")
    if gz.exists():
        raise OSError("{} already exists".format(gz.name))
    with open(src, "rb") as fi, gzip.open(gz, "wb", compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo, 1 << 20)
    d = hashlib.blake2b(digest_size=16)
    with gzip.open(gz, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            d.update(b)
    if d.hexdigest() != action["hash"]:
        recycle([gz])                                             # our own bad output -- still the bin, never a delete
        raise OSError("gzip round trip does not reproduce the original")
    st = src.stat()
    os.utime(gz, (st.st_atime, st.st_mtime))
    return gz


def _archive_module():
    """tools/housekeep_archive.py, loaded from beside this file: housekeep is often run (and tested) by path."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("housekeep_archive", Path(__file__).with_name("housekeep_archive.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def apply(plan_doc: dict, cfg, only=None, recycler=recycle, now: float | None = None, deadline: float | None = None,
          summary: bool = False):
    """Do exactly the plan, re-checking each action. `deadline` (epoch): no new action starts after it; the rest is
    counted as deferred for the next run. summary=True returns {"gib", "failed", "deferred"} instead of an exit code.
    Archive payloads go to the bin in batches at the end of the class, not one shell call per folder."""
    now = time.time() if now is None else now
    guard = Guard(cfg)
    memo = _Memo()
    order = [c for c in CLASSES if not only or c in only]
    failed = deferred = 0
    totals = {}
    for cls in order:
        acts = [a for a in plan_doc["actions"] if a["class"] == cls]
        if not acts:
            continue
        to_recycle, done = [], 0
        archived = []                       # (action, record) awaiting the batched recycle of their payload
        for a in acts:
            if deadline is not None and time.time() > deadline:
                deferred += 1
                continue
            why = check(a, cfg, guard, now, memo)
            if why:
                print("  REFUSED  {:<11} {}  ({})".format(cls, a["path"], why))
                failed += 1
                continue
            try:
                if cls == "link":
                    link(a)
                    done += a["bytes"]
                elif cls == "gzip":
                    compress(a)
                    to_recycle.append(a)
                elif cls == "archive":
                    ha = _archive_module()
                    k = _key(a["path"]) + os.sep
                    extra = [x for x in guard.tracked | guard.links if x.startswith(k)]
                    archived.append((a, ha.archive_folder(Path(a["path"]), Path(a["nas"]), None, extra)))
                else:
                    to_recycle.append(a)
            except Exception as e:          # OSError, or the archive module's ArchiveError: local folder untouched
                if not isinstance(e, OSError) and type(e).__name__ != "ArchiveError":
                    raise
                print("  FAILED   {:<11} {}  ({})".format(cls, a["path"], e))
                failed += 1
        if archived:
            ha = _archive_module()
            victims = [str(v) for _, rec in archived for v in rec["victims"]]
            res = {}
            for i in range(0, len(victims), RECYCLE_BATCH):
                res.update(recycler(victims[i:i + RECYCLE_BATCH]))
            for a, rec in archived:
                left = {v: res.get(str(v), "no result reported") for v in rec["victims"]}
                left = {str(v): e for v, e in left.items() if e}
                if left:
                    ha.note_leftovers(Path(a["path"]), left)
                    print("  PARTIAL  {:<11} {}  ({} item(s) left in place: {}; zip is complete: {})".format(
                        cls, a["path"], len(left), next(iter(left.values())), rec["zip"]))
                    failed += 1
                else:
                    done += a["bytes"]
        if to_recycle:
            paths = [a["path"] for a in to_recycle]
            res = {}
            for i in range(0, len(paths), RECYCLE_BATCH):
                res.update(recycler(paths[i:i + RECYCLE_BATCH]))
            for a in to_recycle:
                err = res.get(a["path"], "no result reported")
                if err:
                    print("  FAILED   {:<11} {}  (Recycle Bin: {})".format(cls, a["path"], err))
                    failed += 1
                else:
                    done += a["bytes"]
        totals[cls] = done
        print("  {:<11} {:>4} action(s), {:.2f} GiB done".format(cls, len(acts), done / GIB))
    print("applied: {:.2f} GiB; {} refused/failed; {} deferred (deadline)".format(
        sum(totals.values()) / GIB, failed, deferred))
    if summary:
        return {"gib": sum(totals.values()) / GIB, "failed": failed, "deferred": deferred}
    return 1 if failed else 0


# --------------------------------------------------------------------------- temp sweep
# The toolkit's own leftover temp dirs go to the Recycle Bin like everything else -- never a permanent delete (user
# ruling 2026-10-18: "There's never a good reason to just outright delete. Never."). Three gates stand between a dir
# and the bin: the name is one the toolkit creates, nothing in it or its direct children changed for
# temp_after_hours, and the dir can be renamed (Windows refuses that while any process holds a file open inside it).
# A dir the bin will not take stays where it is and is named in the session note.

def _newest_shallow(p: Path) -> float:
    newest = p.stat().st_mtime
    try:
        with os.scandir(p) as it:
            for e in it:
                try:
                    newest = max(newest, e.stat(follow_symlinks=False).st_mtime)
                except OSError:
                    pass
    except OSError:
        pass
    return newest


def _in_use(p: Path) -> bool:
    probe = p.with_name(p.name + ".housekeep-probe")
    try:
        os.rename(p, probe)
    except OSError:
        return True
    os.rename(probe, p)
    return False


def temp_candidates(cfg, temp: Path):
    """(path, reason) for every dir the sweep may consider: our prefixes, plus pytest's own leftovers."""
    prefixes = tuple(cfg["temp_prefixes"])
    try:
        entries = list(os.scandir(temp))
    except OSError:
        return
    for e in entries:
        try:
            if e.is_symlink() or not e.is_dir(follow_symlinks=False):
                continue
            # A junction is not a symlink to os.DirEntry on Python 3.10: skip every reparse point outright.
            if getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400:
                continue
        except OSError:
            continue
        if prefixes and e.name.startswith(prefixes):
            yield Path(e.path), "toolkit temp"
        elif e.name.startswith("pytest-of-"):
            # pytest keeps the newest three runs itself and leaves garbage-* behind when it cannot delete one.
            try:
                kids = [k for k in os.scandir(e.path) if k.is_dir(follow_symlinks=False)]
            except OSError:
                continue
            runs = sorted((k for k in kids if re.fullmatch(r"pytest-\d+", k.name)),
                          key=lambda k: int(k.name.split("-")[1]))
            for k in runs[:-1]:                        # never the newest: it may belong to a run starting now
                yield Path(k.path), "old pytest run"
            for k in kids:
                if k.name.startswith("garbage-"):
                    yield Path(k.path), "pytest garbage"


def sweep_temp(cfg, temp: Path | None = None, now: float | None = None, recycler=None) -> dict:
    """Recycle every candidate that passes the gates, in one batch. What the bin refuses stays where it is."""
    temp = Path(temp or tempfile.gettempdir())
    now = time.time() if now is None else now
    cutoff = now - cfg["temp_after_hours"] * 3600
    rows = {"recycled": 0, "kept: changed recently": 0, "kept: in use": 0, "failed": 0}
    ready = []
    for p, _why in temp_candidates(cfg, temp):
        try:
            if _newest_shallow(p) > cutoff:
                rows["kept: changed recently"] += 1
                continue
        except OSError:
            continue
        if _in_use(p):
            rows["kept: in use"] += 1
        else:
            ready.append(p)
    failed = []
    for p, err in (recycler or recycle)(ready).items():
        if err:
            rows["failed"] += 1
            failed.append("{}  ({})".format(p, err))
        else:
            rows["recycled"] += 1
    return {"rows": rows, "failed": failed, "temp": str(temp)}


def worktree_count(root: Path) -> int:
    try:
        out = subprocess.run(["git", "-C", str(root), "worktree", "list", "--porcelain"], capture_output=True,
                             text=True, timeout=30, **NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    return sum(1 for line in out.splitlines() if line.startswith("worktree ")) - 1


def session(cfg, state: Path) -> int:
    """The session-start job: sweep the toolkit's temp dirs, then write a one-line note for the next banner when
    something needs the user (free space near the floor, Codex sessions or worktrees past their threshold).
    The sweep runs at most once per sweep_every_hours (the state file's age is the clock). The daily run has its own
    clock (housekeep_daily.due) and is announced by the session-start hook before this process starts; its summary
    becomes the next session's note."""
    try:
        sweep_due = time.time() - state.stat().st_mtime >= cfg["sweep_every_hours"] * 3600
    except OSError:
        sweep_due = True
    daily = _daily_module()
    daily_due = daily.due(cfg) == ""
    if not sweep_due and not daily_due:
        return 0
    notes = []
    r = {"recycled": 0, "failed": 0}
    if sweep_due:
        r = sweep_temp(cfg)["rows"]
    if daily_due:
        try:
            notes.append(daily.run(cfg))
        except Exception as e:                    # never cost the next session its note; say what broke instead
            notes.append("housekeep: today's run FAILED ({}: {}) -- {}".format(
                type(e).__name__, e, daily.f(cfg, "LOG")))
    if r["recycled"] or r["failed"]:
        notes.append("moved {} leftover temp dir(s) to the Recycle Bin{}".format(
            r["recycled"], "; {} could not be recycled and were LEFT IN PLACE (ask the user before any permanent "
                           "delete)".format(r["failed"]) if r["failed"] else ""))
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    codex = dir_size(home / ".codex" / "sessions") / GIB
    if codex > cfg["codex_sessions_warn_gib"]:
        notes.append("~/.codex/sessions is {:.1f} GiB and Codex does not prune it".format(codex))
    wt = worktree_count(cfg["root"])
    if wt > cfg["worktrees_warn"]:
        notes.append("{} git worktrees (git worktree list)".format(wt))
    review = cfg["root"] / ".claude" / "diag" / "housekeep" / "REVIEW-NEEDED.md"
    if review.is_file() and not daily_due:          # a run that just happened already said so in its summary
        notes.append("housekeep code changed: the daily run only PLANNED; read {} and approve it".format(review))
    alerts = cfg["root"] / ".claude" / "diag" / "housekeep" / "alerts.txt"
    try:
        if time.time() - alerts.stat().st_mtime < 36 * 3600:
            notes += [a for a in alerts.read_text(encoding="utf-8").splitlines() if a.strip()]
    except OSError:
        pass
    floor = cfg["free_floor_gib"]
    free = shutil.disk_usage(str(cfg["root"])).free / GIB
    if floor and free - floor < 20:
        notes.append("{:.0f} GiB free, floor {} GiB".format(free, floor))
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(("DISK: " + "; ".join(notes) + " -- python tools/housekeep.py report\n") if notes else "",
                     encoding="utf-8")
    return 1 if r["failed"] else 0


def _daily_module():
    """tools/housekeep_daily.py, loaded from beside this file."""
    import importlib.util
    sys.modules.setdefault("housekeep", sys.modules[__name__])
    spec = importlib.util.spec_from_file_location("housekeep_daily", Path(__file__).with_name("housekeep_daily.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- report

def dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in walk_files(p) if f.exists()) if p.exists() else 0


def user_level(cfg):
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    temp = Path(tempfile.gettempdir())
    rows = [("~/.claude/projects (transcripts)", home / ".claude" / "projects"),
            ("~/.codex/sessions", home / ".codex" / "sessions"),
            ("<temp>/claude (scratchpads)", temp / "claude")]
    print("user level (report only -- these are the agents' own stores):")
    for label, p in rows:
        print("  {:>9.2f} GiB  {}".format(dir_size(p) / GIB, label))
    for label, sp in (("user", home / ".claude" / "settings.json"),
                      ("project", cfg["root"] / ".claude" / "settings.json"),
                      ("project local", cfg["root"] / ".claude" / "settings.local.json")):
        try:
            v = json.loads(sp.read_text(encoding="utf-8")).get("cleanupPeriodDays")
        except (OSError, ValueError):
            continue
        if v is not None:
            note = "" if label == "user" else \
                "  <- WARNING: the sweep deletes EVERY project's transcripts at this value (docs/RETENTION.md)"
            print("  cleanupPeriodDays = {} ({} settings){}".format(v, label, note))


def report(cfg) -> int:
    doc = plan(cfg)
    print_rows(doc["rows"], "agent output + rolling stores")
    by = {}
    for a in doc["actions"]:
        by[a["class"]] = by.get(a["class"], 0) + a["bytes"]
    print("planned by class: " + ", ".join("{} {:.2f} GiB".format(c, by.get(c, 0) / GIB) for c in CLASSES))
    user_level(cfg)
    floor = cfg["free_floor_gib"]
    free = shutil.disk_usage(str(cfg["root"])).free / GIB
    if floor:
        print("free space: {:.1f} GiB, floor {} GiB{}".format(
            free, floor, "  <- within 20 GiB of the floor" if free - floor < 20 else ""))
    return 0


def main(argv) -> int:
    args = list(argv)
    cfg_path = None
    if "--config" in args:
        i = args.index("--config")
        if i + 1 >= len(args):
            print("--config needs a file", file=sys.stderr)
            return 2
        cfg_path = args[i + 1]
        del args[i:i + 2]
    try:
        cfg = load_config(cfg_path)
    except (OSError, ValueError) as e:
        print("housekeep: {}".format(e), file=sys.stderr)
        return 2
    cmd = args.pop(0) if args else "report"
    if cmd == "report" and not args:
        return report(cfg)
    if cmd == "sweep-temp" and not args:
        res = sweep_temp(cfg)
        rows = res["rows"]
        print("toolkit temp dirs under {}: {} considered".format(res["temp"], sum(rows.values())))
        for k, v in rows.items():                     # every row prints, zeros included; they sum to the total
            print("  {:>6}  {}".format(v, k))
        for p in res["failed"]:
            print("  FAILED   " + p)
        return 1 if res["failed"] else 0
    if cmd == "session" and len(args) == 1:
        return session(cfg, Path(args[0]))
    if cmd == "announce" and not args:            # the session-start hook prints this BEFORE launching `session`
        line = _daily_module().announcement(cfg)
        if line:
            print(line)
        return 0
    only = None
    if "--only" in args:
        i = args.index("--only")
        only = set(args[i + 1].split(",")) if i + 1 < len(args) else set()
        del args[i:i + 2]
        if not only or only - set(CLASSES):
            print("--only takes: {}".format(",".join(CLASSES)), file=sys.stderr)
            return 2
    if cmd == "plan" and len(args) == 2 and args[0] == "-o":
        doc = plan(cfg, only=only)
        Path(args[1]).write_text(json.dumps(doc, indent=1), encoding="utf-8")
        print_rows(doc["rows"], "plan")
        print("{} action(s) written to {}".format(len(doc["actions"]), args[1]))
        return 0
    if cmd == "apply" and args:
        if len(args) != 1:
            print("usage: housekeep.py apply PLAN.json [--only CLASS,...]", file=sys.stderr)
            return 2
        return apply(json.loads(Path(args[0]).read_text(encoding="utf-8")), cfg, only)
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
