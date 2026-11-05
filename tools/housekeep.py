#!/usr/bin/env python3
"""housekeep -- disk retention for agent output: report, plan, then apply exactly the plan.

Agent work piles up bytes that carry no information: copies of game files made as test fixtures, build
intermediates, the same log copied into ten evidence folders, launch logs nobody prunes, snapshot folders
that repeat the one before. The policy this tool implements is docs/RETENTION.md; read that first.

    python tools/housekeep.py [report]            what is where, by class (changes nothing)
    python tools/housekeep.py plan -o plan.json   every action it would take, with the hash it was planned on
            [--only CLASS[,CLASS]]               (skips the scans no listed class needs)
    python tools/housekeep.py apply plan.json     re-check each action, then do exactly those
            [--only CLASS[,CLASS]]               (derived, recreatable, build, link, gzip, launch, snapshot, scratch,
                                                  superseded, archive, expire)
    python tools/housekeep.py sweep-temp          recycle the toolkit's own stale temp dirs (see "temp sweep")
    python tools/housekeep.py session STATE.txt   sweep-temp, recycle settled .claude/retired batches (what the guard
                                                  pruned from its own stores), then write the session note to STATE.txt
    python tools/housekeep.py recycle PATH...     send exactly these paths to the Recycle Bin (OK/FAILED per path)
                                                  (only what retention may remove: see recycle_refusal)
    --config FILE   default <toolkit>/.claude/housekeep.json (.claude/housekeep.json.example ships)

Two passes, never one: `plan` decides, `apply` re-verifies. An action whose file changed since it was planned
(size, hash, a newer write in its task folder) is REFUSED, not re-decided -- run `plan` again.

What it never touches, whatever the config says: saves (.ess/.skse), anything under a folder whose name contains
"backup", known-good* baselines and AUDIT_LOG.txt; git-tracked files; every file a project doc links to; and any task folder written to within
`active_hours` (another session is probably still working there). When git cannot say which files are tracked in a
root that is in a work tree, `report`, `plan` and `apply` refuse (exit 1) instead of planning without that protection.

Cross-run retirement uses the Recycle Bin (Windows: FOF_ALLOWUNDO). Unrecyclable items stay in place;
there is no automatic permanent fallback. Same-run owned staging may be cleaned by its creator.
The bin frees no disk space until it is emptied; that is the user's call. A file the bin refuses is reported
FAILED and left in place; off Windows there is no Recycle Bin implementation, so every removal is reported that way.
The whole-folder classes (launch, snapshot, scratch, superseded) keep a folder whole when any file in it is protected. Duplicates are not removed at all: they become NTFS hard links to one copy, marked
read-only so an in-place edit cannot silently change every path at once. A file that already has another name (a mod
manager's hard-link deployment shares the inode with a live Data/ file) is never linked or made read-only.

Accounting: `report` and `plan` print every scanned byte in exactly one row, the rows sum to the total, and the
row of bytes it leaves alone prints even at zero.

Exit: 0 ok · 1 an action was refused or failed · 2 bad usage or config.
"""

from __future__ import annotations

import fnmatch
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import unquote
from pathlib import Path
import importlib.util
_BACKUP_SPEC = importlib.util.spec_from_file_location('housekeep_cold_helper', Path(__file__).with_name('housekeep_backups.py'))
certainty = importlib.util.module_from_spec(_BACKUP_SPEC)
_BACKUP_SPEC.loader.exec_module(certainty)

TOOLKIT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = TOOLKIT_ROOT / ".claude" / "housekeep.json"

# The guard's own list (tools/skyguard/writes.py SAVE_EXTS). Copied, not imported, so this tool also runs in an
# install that predates the guard package; tests/test_housekeep.py fails if the two drift.
SAVE_EXTS = (".ess", ".skse")

CLASSES = ("derived", "recreatable", "build", "link", "gzip", "launch", "snapshot", "scratch", "superseded", "archive",
           "expire")
# Every child process is started without a console window. 2026-10-29: the daily task runs under pythonw (no
# console), so each PowerShell recycle call opened its own visible window -- one per archived folder, hundreds of
# them flashing on the user's desktop.
NO_WINDOW = {"creationflags": 0x08000000} if os.name == "nt" else {}      # CREATE_NO_WINDOW
RECYCLE_BATCH = 500                                                        # paths per PowerShell call
DEFAULTS = {
    "recycle_bin_warn_gib": 10,       # read-only Windows aggregate; 0 disables warning
    "obsolete_items": {},            # exact bytes + explicit reason/evidence; never age alone
    "backup_archive": {},            # opt-in cold backups; docs/BACKUP-ARCHIVE.md
    "agent_output": [],               # task-folder roots (relative to the toolkit root, or absolute)
    "nested_task_roots": [],          # roots one level down whose children are task folders of their own
    "live_roots": ["Data"],           # a byte-identical copy of a file here is "recreatable"
    "state_exts": [".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".json", ".jsonl", ".ini", ".toml",
                   ".yaml", ".yml", ".xml", ".cfg", ".log", ".txt"],   # live files that change: never a reference
    "launch_logs": [],                # folders of launch-YYYYMMDD-HHMMSS/ log captures
    "legacy_snapshots": [],           # folders of YYYYMMDD_HHMMSS/ snapshot copies (the 3.x snapshot hook)
    "scratch": [],                    # whole folders to recycle, named one by one
    "staging_roots": [],              # direct versioned children only; rollback sources always stay
    "superseded_after_days": 30,
    "evidence_docs": ["CLAUDE.md", "AGENTS.md", "KNOWLEDGEBASE.md", "KNOWLEDGEBASE.local.md"],
    "protected_globs": [],
    "build_dir_names": ["build", "build-sdk", "build-vcpkg", "native-bootstrap", "native-prebuilt",
                        "CMakeFiles", "vcpkg_installed", "buildtrees"],
    "build_exts": [".obj", ".pch", ".ilk", ".iobj", ".ipdb", ".tlog", ".idb"],
    "active_hours": 48,
    # code fingerprint (housekeep_daily.code_sha256: housekeep, housekeep_archive, housekeep_backups, housekeep_daily) whose
    # dry-run output the user has reviewed. housekeep_daily.py APPLIES only
    # when the code still matches; otherwise it dry-runs and writes REVIEW-NEEDED.md (2026-10-28: a new rule acted
    # on its first run, unreviewed, and took a library a live build needed).
    "approved_sha256": "",
    # ---- archive: a DONE task folder is zipped to archive_root, verified there, then its payload recycled.
    # No extra storage is assumed: the default destination is a LOCAL folder on the same drive (text evidence
    # compresses ~5-10x), and archive review never treats age as obsolescence. A NAS is optional.
    "archive_enabled": True,
    "archive_root": "",               # empty = <root>/.claude/archive (local); or another drive / NAS share
    "archive_retention_days": 90,     # legacy setting; age never authorizes archive removal
    "archive_review_days": 30,       # read-only session review, including retention=0; 0 disables reminders
    "archive_review_seconds": 15,    # bounded archive inventory/content proof; incomplete means held
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
    days = cfg["superseded_after_days"]
    if isinstance(days, bool) or not isinstance(days, (int, float)) or not math.isfinite(days) or days < 0:
        raise ValueError("superseded_after_days must be a finite nonnegative number")
    if not isinstance(cfg["staging_roots"], list) or any(not isinstance(p, str) or not p for p in cfg["staging_roots"]):
        raise ValueError("staging_roots must be a list of nonempty paths")
    stage_roots = [_real(_abs(cfg, p)) for p in cfg["staging_roots"]]
    if any(a == b or _strictly_inside(a, b) or _strictly_inside(b, a)
           for i, a in enumerate(stage_roots) for b in stage_roots[i + 1:]):
        raise ValueError("staging_roots must not overlap")
    if not cfg["archive_root"]:
        cfg["archive_root"] = str(cfg["root"] / ".claude" / "archive")
    cfg["archive_root"] = str(_abs(cfg,cfg["archive_root"]))
    return cfg


def _abs(cfg, p) -> Path:
    q = Path(p)
    return q if q.is_absolute() else cfg["root"] / q


def _key(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def blake(path, chunk=1 << 20) -> str:
    """A current content proof; never rewind a concurrent writer's timestamps."""
    return _archive_module()._retirement_hash(Path(path), float('inf'), chunk)



def activity(folder: Path) -> tuple:
    """(newest write, newest read) of any file under `folder`. NTFS keeps last-access times on this machine (fsutil
    disablelastaccess = 2, verified 2026-10-28), so a build linking a library or a session reading its staging shows
    up. 2026-10-28: a write-only signal let a cleanup take a .lib a live build was linking. Reads are noisy too
    (a disk scan reads whole trees), so a read only VETOES for read_veto_hours; idleness is measured on writes."""
    w = r = 0.0
    for p in walk_files(folder):
        st = p.stat()
        w, r = max(w, st.st_mtime), max(r, st.st_atime)
    return w, r


def newest_activity(folder: Path) -> float:
    """Newest write or read of any file (activity), or newest directory write (newest_mtime; uncertain = active)."""
    return max(*activity(folder), newest_mtime(folder))


_READS_RECORDED = {}


def reads_recorded(folder: Path) -> bool:
    """Measure using owned scratch on the same volume; never backdate source files."""
    return _archive_module().reads_recorded(folder, _READS_RECORDED)



def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def newest_mtime(folder: Path) -> float:
    """Uncertain activity is active; directory writes count even when no file was added."""
    try:
        newest = folder.stat().st_mtime
        for dp, dirs, files in os.walk(folder, onerror=walk_error):
            for name in dirs + files:
                newest = max(newest, os.stat(os.path.join(dp, name)).st_mtime)
        return newest
    except OSError:
        return float("inf")


def fingerprint(folder: Path):
    """(file count, total bytes, newest mtime) -- the cheap 'has this folder changed' check for whole-folder
    actions. Hashing 245k snapshot files twice would cost minutes for no extra safety on a folder nothing writes."""
    n = size = 0
    newest = 0.0
    for p in walk_files(folder):
        st = p.stat()
        n += 1
        size += st.st_size
        newest = max(newest, st.st_mtime)
    return [n, size, round(newest, 3)]


class ProtectionUnavailable(OSError):
    """A Forever rule could not be evaluated. Nothing is planned or applied: a rule that silently evaluates to "nothing
    is protected" is the one failure this tool must never have."""


def _in_git_work_tree(root: Path) -> bool:
    p = Path(os.path.abspath(str(root)))
    return any((q / ".git").exists() for q in (p, *p.parents))


def tracked_files(root: Path) -> set:
    """Every git-tracked file under root. Fails CLOSED (ProtectionUnavailable) when git cannot answer for a root that
    is in a work tree -- dubious ownership, a broken index, a timeout, no runnable git -- instead of returning an empty
    set that switches the git-tracked protection off without a word (sweep 2026-10-25; X4 679bc4a)."""
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, timeout=120,
                             **NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        if isinstance(e, OSError) and not _in_git_work_tree(root):
            return set()                         # no git, and no repository either: nothing here is tracked
        raise ProtectionUnavailable("git tracked-file protection unavailable ({}: {})".format(type(e).__name__, e)) from e
    if out.returncode != 0:
        err = out.stderr.decode("utf-8", "replace").strip()
        if "not a git repository" in err.lower():
            return set()                         # a plain folder: nothing here is tracked
        raise ProtectionUnavailable("git tracked-file protection unavailable (git ls-files exit {}: {})".format(
            out.returncode, err[:300] or "no message"))
    return {_key(root / p) for p in out.stdout.decode("utf-8", "replace").split("\0") if p}


def evidence_links(cfg) -> set:
    """Every agent-output path a project doc links to (written relative to the root, as docs link them). A linked
    file is never planned for anything."""
    links = set()
    prefixes = []                                # root-relative spellings (resolved against the root)
    roots = [_abs(cfg, r) for r in [*cfg["agent_output"], *cfg["staging_roots"], *cfg.get("archive_roots", {})]]
    for root in roots:
        try:
            rel = os.path.relpath(os.path.abspath(str(root)), os.path.abspath(str(cfg["root"])))
        except ValueError:                       # another drive: docs cannot link it root-relatively
            continue
        if rel.split(os.sep)[0] != "..":
            prefixes.append(re.escape(rel.replace("\\", "/")))
    if not roots:
        return links
    flags = re.I if os.name == "nt" else 0
    start_re = re.compile("(?:{})/".format("|".join(prefixes)), flags) if prefixes else None
    budget = 8 * 1024 * 1024
    for doc in cfg["evidence_docs"]:
        p = _abs(cfg, doc)
        if not p.is_file():
            continue
        doc_prefixes = []                        # absolute and doc-relative spellings (resolved against the doc)
        for root in roots:
            doc_prefixes.append(re.escape(str(root).replace("\\", "/")))
            try:
                doc_prefixes.append(re.escape(os.path.relpath(root, p.parent).replace("\\", "/")))
            except ValueError:
                pass                             # cross-drive documents can still use the absolute spelling
        doc_re = re.compile("(?:{})/".format("|".join(doc_prefixes)), flags)
        text = p.read_text(encoding="utf-8", errors="replace")
        # Conservative reference discovery, not a Markdown parser (sweep 2026-10-25; X4 34133b7, c6d4760). A link may
        # hold spaces and parentheses (`_CLAUDE_OUTPUT/Mod (v2) notes/x.md`), so every cut of the rest of the line at a
        # delimiter is a candidate: stopping at the FIRST space or ')' silently lost evidence. Literal, %-escaped and
        # backslash-escaped spellings can all name a real file. Too many candidates refuses rather than truncates.
        views = {text, unquote(text)}
        views |= {re.sub(r"\\([!\"#$%&'()*+,./:;<=>?@\[\]^_`{|}~\\-])", r"\1", s) for s in tuple(views)}
        for view in views:
            for line in view.splitlines():
                for pattern, base in ((start_re, cfg["root"]), (doc_re, p.parent)):
                    if pattern is None:
                        continue
                    for start in pattern.finditer(line):
                        tail = line[start.start():]
                        if len(tail) > 32768:
                            raise ProtectionUnavailable(
                                "an evidence reference line in {} is too long to read safely".format(doc))
                        ends = [i for i, ch in enumerate(tail) if ch.isspace() or ch in ")]\"'`<>|.,;:*"]
                        for end in ends + [len(tail)]:
                            candidate = tail[:end]
                            if candidate:
                                budget -= len(candidate)
                                q = Path(candidate)
                                links.add(_key(q if q.is_absolute() else base / q))
                            if budget < 0 or len(links) > 50000:
                                raise ProtectionUnavailable(
                                    "evidence references in {} exceed the safe discovery bound".format(doc))
    return links


class Guard:
    """The Forever class and the per-run exclusions, in one place: plan and apply both ask it."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.tracked = tracked_files(cfg["root"])
        for r in cfg["staging_roots"]:
            root = _abs(cfg, r)
            if not staging_root_refusal(root, cfg) and root.is_dir():
                self.tracked.update(tracked_files(root))
        seen = {_key(cfg["root"])}
        roots = [*cfg["agent_output"], *cfg["launch_logs"], *cfg["legacy_snapshots"],
                 *cfg["scratch"], *cfg.get("archive_roots", {})]
        for value in roots:
            root = _abs(cfg, value)
            if _key(root) in seen or root.is_relative_to(cfg["root"]):
                continue
            seen.add(_key(root))
            try:
                info = root.lstat()
            except FileNotFoundError:
                continue
            if root.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                continue
            if root.is_dir():
                self.tracked.update(tracked_files(root))
        self.links = evidence_links(cfg)
        # A doc line naming the output root itself (`out/*.log`, `out/<task>/README.md`) protects no file under it: every
        # finished task would otherwise read as linked evidence, forever, with nothing said.
        self.output_roots = {_key(_abs(cfg, r)) for r in cfg["agent_output"]}
        self.globs = [g.lower() for g in cfg["protected_globs"]]

    def why_protected(self, path) -> str:
        p = str(path)
        low = p.replace("\\", "/").lower()
        if low.endswith(SAVE_EXTS):
            return "save"
        if low.endswith(".etl"):
            return "source measurement (ETL)"
        # Folder names BELOW the configured root only: a root that itself sits under ".../backups/..." must not
        # make every file look protected (or, read the other way, make the rule meaningless).
        try:
            rel = os.path.relpath(os.path.abspath(p), os.path.abspath(str(self.cfg["root"])))
        except ValueError:                       # another drive: no relative form, judge the whole path
            rel = p
        rel_parts = rel.replace("\\", "/").lower().split("/")
        # docs/RETENTION.md's Forever names, in code: a known-good baseline (a folder or file named known-good*) and the
        # toolkit's audit log. Judged below the root, like "backup", so a root named that way does not protect all.
        named = rel_parts if not rel.startswith("..") else low.split("/")
        if any(part.startswith("known-good") for part in named):
            return "known-good baseline"
        if named[-1] == "audit_log.txt":
            return "audit log"
        if not rel.startswith(".."):
            if any("backup" in part for part in rel_parts[:-1]):
                return "backup folder"
        elif any("backup" in part for part in low.split("/")[:-1]):
            return "backup folder"
        k = _key(p)
        if k in self.tracked:
            return "git-tracked"
        if k in self.links or any(k.startswith(x + os.sep) for x in self.links if x not in self.output_roots):
            return "linked evidence"
        if any(fnmatch.fnmatch(low, g) for g in self.globs):
            return "protected_globs"
        return ""


# --------------------------------------------------------------------------- task folders

def task_folders(cfg, held=None):
    """[(path, state)] under every agent_output root; state is "idle", "active" or "loose".

    A folder directly in a root is a task folder, judged active as a whole. A folder named in nested_task_roots
    is a root of its own (recursively). A LOOSE file -- one sitting directly in a root, not inside a task folder --
    is never a candidate: that is where shared live state lives (a service's events.jsonl, a status JSON a session
    rewrites). Making an append-only log read-only would break its next write, and a log appended once a week
    walks straight through the activity gate."""
    out = []
    held = [] if held is None else held
    cutoff = time.time() - cfg["active_hours"] * 3600
    nested = {_key(_abs(cfg, n)) for n in cfg["nested_task_roots"]}

    def visit(folder: Path):
        for e in sorted(os.scandir(folder), key=lambda e: e.name):
            p = Path(e.path)
            try:
                info = p.lstat()
                if p.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise OSError("symbolic link or reparse point is not a cleanup target: {}".format(p))
                if e.is_dir(follow_symlinks=False) and _key(p) in nested:
                    visit(p)
                elif e.is_dir(follow_symlinks=False):
                    out.append((p, "active" if newest_activity(p) > cutoff else "idle"))
                else:
                    out.append((p, "loose"))
            except OSError as exc:
                held.append({"path": str(p), "reason": str(exc), "bytes": None})

    for r in cfg["agent_output"]:
        root = _abs(cfg, r)
        try:
            info = root.lstat()
            if root.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise OSError("configured root is a symbolic link or reparse point")
            if not root.is_dir():
                raise OSError("configured root is not a directory")
            visit(root)
        except OSError as exc:
            held.append({"path": str(root), "reason": str(exc), "bytes": None})
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
        if m:
            toks |= {t.replace("\\", "/").rstrip("/").lower() for t in re.findall(r"`([^`\n]+)`", m.group(1))}
    # The block's own instructions may name a whole output root as an example; that holds no one task. A session's
    # line need not be a bullet (release review 2026-11-02: the install's block only asks for "one line per session").
    whole = {str(r).replace("\\", "/").rstrip("/").lower() for r in cfg.get("agent_output", ())}
    return toks - whole


def rb_job_cwds() -> list:
    p = Path(os.path.expanduser("~")) / ".claude" / "tools" / "resource-budget" / "ledger.json"
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []                               # no budget tool here: no jobs to hold for
    except OSError:
        return None
    try:
        return [_key(e["cwd"]) for e in json.loads(text).get("entries", []) if e.get("cwd")]
    except (ValueError, AttributeError, TypeError):
        return None                             # unreadable is not "nothing is running": is_done holds the work


def is_done(task: Path, idle_days: float, tokens: set, cwds: list, guard) -> str:
    """'' when `task` is finished work; otherwise why it is still in use or must stay. idle_days is measured by the
    caller. Written-recently is the weakest signal (a read leaves no mtime), so every other gate is independent."""
    if (task / KEEP_PIN).exists():
        return "pinned (.housekeep-keep)"
    if cwds is None:
        return "the rb ledger could not be read, so a running job may work here"
    name = task.name.lower()
    path = str(task).replace("\\", "/").lower()
    if any(t == name or path.endswith("/" + t) or t.endswith("/" + name) or (len(t) > 8 and t in path)
           for t in tokens):
        return "named in the Live state block"
    k = _key(task)
    if any(c == k or c.startswith(k + os.sep) for c in cwds):
        return "a running rb job works here"
    for dp, dirs, files in os.walk(task):
        if ".git" in dirs or ".git" in files:
            return "a git repository"
        for f in files:
            if fnmatch.fnmatch(f.lower(), 'owned-session*.json') or fnmatch.fnmatch(f.lower(), '*ownership*.json'):
                # Legacy receipts have no common release schema. A dead/reused PID, age or an
                # unqualified `released` field cannot prove that the owner's work is finished.
                return "holds an ownership receipt without qualified release proof"
            if f.lower().endswith(SAVE_EXTS):
                return "holds a save"
            if guard.globs and guard.why_protected(os.path.join(dp, f)) == "protected_globs":
                return "holds a protected_globs file"
    return ""


def archive_facts(folder):
    """One bounded-memory metadata pass for fingerprint and write/read activity."""
    n = size = 0
    wrote = read = 0.0
    for p in walk_files(folder):
        st = p.stat()
        n += 1
        size += st.st_size
        wrote, read = max(wrote, st.st_mtime), max(read, st.st_atime)
    return [n, size, round(wrote, 3)], wrote, read


def archive_selection(cfg, guard, tasks, now, held=None, inventory=None) -> tuple:
    """([action], rows) for every done folder due: closed first, then by age; over budget, down to min_idle_days."""
    tokens, cwds = live_state_tokens(cfg), rb_job_cwds()
    held = [] if held is None else held
    inventory = {} if inventory is None else inventory
    inventory.update(roots=[str(_abs(cfg, r)) for r in cfg["archive_roots"]], roots_held=0,
                     candidates_inspected=0, candidates_held=0)
    # from_root: the folder lives under archive_roots, which the file-level loop never scans, so its kept bytes are
    # counted here; agent_output folders that stay are counted by that loop instead (rows must sum to the scan).
    cands = [(t, cfg["archive_after_days"], False) for t, s in tasks if s != "loose"]
    for root, days in cfg["archive_roots"].items():
        r = _abs(cfg, root)
        try:
            st = r.lstat()
            if r.is_symlink() or getattr(st, "st_file_attributes", 0) & 0x400:
                raise OSError("archive root is a symbolic link or reparse point")
            if not r.is_dir():
                raise OSError("archive root is not a directory")
            cands += [(Path(e.path), days, True) for e in os.scandir(r) if e.is_dir(follow_symlinks=False)]
        except OSError as exc:
            inventory["roots_held"] += 1
            held.append({"path": str(r), "reason": str(exc), "bytes": None, "scope": "archive"})
    inventory["candidates_requested"] = len(cands)
    info, total = [], 0
    actions, rows, pins = [], {}, []

    def keep(row, n, from_root):
        if from_root:
            rows[row] = rows.get(row, 0) + n

    for t, days, from_root in cands:
        try:
            fp, wrote, read = archive_facts(t)
        except OSError as exc:
            inventory["candidates_held"] += 1
            held.append({"path": str(t), "reason": str(exc), "bytes": None, "scope": "archive"})
            continue
        inventory["candidates_inspected"] += 1
        total += fp[1]
        if (t / "ARCHIVED.md").exists():        # any size: linked evidence or a pin can keep more than a stub
            keep("kept: already archived", fp[1], from_root)
            continue
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
        action = {"class": "archive", "path": str(t), "bytes": fp[1], "fingerprint": list(fp),
                  "nas": str(Path(cfg["archive_root"]) / label), "idle_days": round(idle, 1),
                  "closed": closed}
        why = _archive_module().archive_scope(action, cfg)
        if why:
            keep("kept: archive scope ("+why+")",fp[1],from_root)
            continue
        actions.append(action)
        if over:
            total -= fp[1]
            over = total > cfg["budget_gib"] * GIB
    return actions, rows, pins, measured


def walk_files(target: Path, strict=False):
    """Every file under target. Fails CLOSED: a symbolic link or reparse point (the target or anything inside it) and
    every walk error raise OSError. strict=True additionally stats every entry, so an unreadable one raises too."""
    def failed(error):
        raise error
    def linked(path):
        info = path.lstat()
        return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400)
    if linked(target):
        raise OSError("symbolic link or reparse point is not a cleanup target: {}".format(target))
    if target.is_file():
        yield target
        return
    for dp, dirs, fn in os.walk(target, onerror=failed):
        if strict:
            os.stat(dp)
        for name in dirs + fn:
            p = Path(dp) / name
            if linked(p):
                raise OSError("symbolic link or reparse point inside cleanup target: {}".format(p))
            if strict:
                os.stat(p)
        for f in fn:
            yield Path(dp) / f


# The 3.x snapshot-before-tool.sh store. User ruling 2026-10-20: its stamps are ROLLING (a stamp repeating the one
# before goes to the Recycle Bin), although the store sits under .claude/backups/. Only this one tool store: saves
# inside it, a backup folder inside a stamp, and every other backup folder stay Forever.
ROLLING_SNAPSHOT_STORE = "auto_snapshots"


def under_pin(path: Path, top: Path) -> bool:
    """A .housekeep-keep in this file's folder or any folder up to and including `top`."""
    for parent in path.parents:
        if (parent / KEEP_PIN).exists():
            return True
        if parent == top:
            return False
    return False


def archive_keeps(task: Path, guard) -> list:
    """What an archive leaves in place (user ruling 2026-11-02): every Forever name, and everything under a nested pin.
    The zip still holds a verified copy; only the local retirement skips them, so a baseline never moves from its path."""
    keep = []
    for dp, ds, fs in os.walk(task):
        pinned = under_pin(Path(dp) / "x", task)
        keep += [_key(os.path.join(dp, f)) for f in fs if pinned or guard.why_protected(os.path.join(dp, f))]
    return keep


def folder_protection(folder: Path, guard) -> str:
    """Master's whole-folder check at the point an action is recorded (launch, scratch): the first protected entry,
    a .housekeep-keep pin, or "". It overlaps protected_inside() on purpose (two layers, user ruling 2026-11-01);
    a named function so each layer can be switched off alone in a test and shown to keep the folder by itself."""
    for dp, ds, fs in os.walk(folder):
        for f in [Path(dp), *(Path(dp) / n for n in ds + fs)]:
            certainty.plain_path(f)
            why = guard.why_protected(f) or ('pinned' if f.name == KEEP_PIN else '')
            if why:
                return why
    return ""


def protected_inside(guard, folder: Path, cls: str = "") -> str:
    """Why a whole-folder action (launch, snapshot, scratch) may not take this folder: the first protected file in
    it, or "". A folder is recycled whole, so one save or one backup inside it keeps all of it, whatever the config
    names -- except the "backup folder" ABOVE a stamp of the rolling snapshot store (see ROLLING_SNAPSHOT_STORE)."""
    store = folder.parent if cls == "snapshot" and folder.parent.name.lower() == ROLLING_SNAPSHOT_STORE else None
    for f in walk_files(folder):
        why = guard.why_protected(f)
        if why == "backup folder" and store is not None \
                and not any("backup" in part.lower() for part in f.relative_to(store).parts[:-1]):
            continue
        if why:
            return why
    return ""


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


def plain_path(path: Path) -> bool:
    """New retention classes never follow a symlink, junction or reparse point, including ancestors."""
    return all(not stat.S_ISLNK(s.st_mode) and not getattr(s, "st_file_attributes", 0) & 0x400
               for s in (os.lstat(p) for p in (path, *path.parents)))


def walk_error(error):
    raise error


def derived_source(path: Path, task: Path | None = None):
    if path.suffix.lower() != ".xml" or not (path.name.lower() == "events.xml"
                                                   or path.parent.name.lower() == "xml-decoder"):
        return None
    if not plain_path(path):
        return None
    boundary = Path(task) if task is not None else path.parent
    for folder in (path.parent, path.parent.parent):
        if folder != boundary and boundary not in folder.parents:
            continue
        for source in sorted(folder.iterdir()):
            if source.suffix.lower() == ".etl" and source.is_file() and plain_path(source):
                return source
    return None


VERSIONED_STAGE = re.compile(r"^(.+)-(\d+(?:\.\d+){1,3})$")


def stage_version(name):
    match = VERSIONED_STAGE.fullmatch(name)
    if match is None:
        return None
    numbers = tuple(int(n) for n in match[2].split("."))
    return match[1].casefold(), numbers + (0,) * (4 - len(numbers))


def manifest_references(cfg):
    """Read rollback manifests, never assume an unreadable/unknown format contains no references.

    The installed mod-install writer uses *.files.txt (including operation manifests in backups/).
    JSON manifests use {"rollback_sources": [absolute paths]}.
    Relative paths and unrecognized text refuse staging cleanup until the manifest can be interpreted.
    """
    roots = [TOOLKIT_ROOT / "manual-installs", TOOLKIT_ROOT / ".claude/backups/manual-installs"]
    if os.environ.get("SKYRIM_MANUAL_INSTALLS"):
        roots.append(Path(os.environ["SKYRIM_MANUAL_INSTALLS"]))
    refs = set()

    def remember(value):
        if not Path(value).is_absolute():
            raise ValueError("relative manifest path")
        refs.add(_key(_real(value)))

    for root in roots:
        if not root.exists():
            continue
        if not root.is_dir() or not plain_path(root):
            raise ValueError("manual-installs is not a plain directory")
        for dp, dirs, files in os.walk(root, onerror=walk_error):
            if not plain_path(Path(dp)) or not all(plain_path(Path(dp) / name) for name in dirs):
                raise ValueError("manifest directory is a link")
            for name in files:
                path = Path(dp) / name
                if not plain_path(path):
                    raise ValueError("manifest is a link")
                # Backup payloads are not manifests. Only manifest files are parsed.
                if not (name.endswith(".files.txt") or path.suffix.lower() == ".json"):
                    if "backups" in Path(dp).relative_to(root).parts:
                        continue
                    raise ValueError("unknown manual-install manifest format: " + name)
                # A whole mod folder kept by `mod-install --update` is payload, whatever its files are called: a mod's
                # own manifest.json, or a config in another encoding, turned staging cleanup off for good.
                if "mod-folder" in Path(dp).relative_to(root).parts:
                    continue
                in_backups = root == TOOLKIT_ROOT / ".claude/backups/manual-installs" or "backups" in Path(dp).relative_to(root).parts
                try:
                    text = path.read_text(encoding="utf-8-sig")
                except UnicodeDecodeError:
                    if in_backups and path.suffix.lower() == ".json":
                        continue
                    raise ValueError("unreadable manual-install manifest: " + name) from None
                if path.suffix.lower() == ".json":
                    # Backup configs are payloads unless their name or top-level shape claims a manifest.
                    named_manifest = name.lower() in ("manifest.json", "rollback.json") or name.lower().endswith(".manifest.json")
                    try:
                        document = json.loads(text)
                    except ValueError:
                        if in_backups and not named_manifest and not re.search(r'"rollback_sources"\s*:', text):
                            continue
                        raise
                    if in_backups and not named_manifest and not (isinstance(document, dict) and "rollback_sources" in document):
                        continue
                    if not isinstance(document, dict) or set(document) != {"rollback_sources"} \
                            or not isinstance(document["rollback_sources"], list):
                        raise ValueError("unknown JSON manifest structure")
                    for value in document["rollback_sources"]:
                        if not isinstance(value, str):
                            raise ValueError("invalid rollback source")
                        remember(value)
                    continue
                if not text.strip():
                    raise ValueError("empty manifest")
                for line in text.splitlines():
                    if not line.strip():
                        continue
                    if line.startswith("# overwritten originals backed up under: "):
                        remember(line.split(": ", 1)[1])
                    elif line.startswith("# UPDATE: the previous mod folder "):
                        remember(line.split(" is kept whole at ", 1)[1])
                    elif line.startswith(("# installed into: ", "# format: ", "# STATUS: ")) \
                            or re.match(r"^# .* -- installed .* from .* \[group .*\]$", line) \
                            or re.match(r"^# UPDATE: \d+ file\(s\) were in the previous mod folder$", line):
                        continue
                    elif re.match(r"^[0-9a-fA-F]{64}  .+", line):
                        remember(line[66:])
                    elif re.fullmatch(r"# KEPT: [0-9a-fA-F]{64}  .+", line):
                        remember(line.split("  ", 1)[1])
                    elif line.startswith("PLANNED  "):
                        remember(line[9:])
                    else:
                        raise ValueError("unknown manual-install manifest row")
    return refs


def staging_activity(path):
    """Extraction/creation is activity even when an archive restores an old modification time."""
    info = path.stat()
    return max(info.st_mtime, info.st_ctime)


def staging_root_refusal(root, cfg):
    real = _real(root)
    if real.parent == real:
        return "unsafe staging root: drive or filesystem root"
    if real == _real(Path.home()):
        return "unsafe staging root: home folder"
    bases = [_real(cfg["root"]), _real(TOOLKIT_ROOT)]
    if os.environ.get("CLAUDE_PROJECT_DIR"):
        bases.append(_real(os.environ["CLAUDE_PROJECT_DIR"]))
    if any(real == base or _strictly_inside(base, real) for base in bases):
        return "unsafe staging root: toolkit/install root or ancestor"
    if real.name.casefold() == "mods":
        return "unsafe staging root: mod-manager mods folder"
    return ""


def superseded_reason(path, root, cfg, guard, now):
    """An empty result proves every staging eligibility clause again at apply time."""
    if path.parent != root or root not in [_abs(cfg, r) for r in cfg["staging_roots"]]:
        return "not a direct child of a configured staging root"
    unsafe = staging_root_refusal(root, cfg)  # also recheck an existing plan
    if unsafe:
        return unsafe
    version = stage_version(path.name)
    if version is None:
        return "folder has no supported numeric version"
    if not plain_path(path):
        return "staging path is a link"
    latest = staging_activity(path)
    for dp, dirs, files in os.walk(path, onerror=walk_error):
        if any(name.casefold() == ".git" for name in dirs + files):
            return "staging tree contains a Git checkout"
        for name in dirs + files:
            entry = Path(dp) / name
            info = entry.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & REPARSE_POINT:
                return "staging tree contains a link"
            latest = max(latest, staging_activity(entry))
    why = guard.why_protected(path) or protected_inside(guard, path)
    if why:
        return "protected ({})".format(why)
    if latest > now - max(cfg["superseded_after_days"] * 86400, cfg["active_hours"] * 3600):
        return "staging folder is recent or active"
    listed = set()
    marker = root / "_superseded"
    if marker.exists():
        if not marker.is_file() or not plain_path(marker):
            return "unreadable _superseded list"
        listed = {line.strip() for line in marker.read_text(encoding="utf-8-sig").splitlines()
                  if line.strip() and not line.lstrip().startswith("#")}
        if any(stage_version(name) is None or "/" in name or "\\" in name for name in listed):
            return "invalid _superseded list"
    higher = any(other.is_dir() and plain_path(other) and (v := stage_version(other.name)) is not None
                 and v[0] == version[0] and v[1] > version[1] for other in root.iterdir())
    if path.name not in listed and not higher:
        return "no higher version or explicit supersession"
    refs = manifest_references(cfg)
    real = _real(path)
    if any(_key(real) == ref or _strictly_inside(Path(ref), real) or _strictly_inside(real, Path(ref)) for ref in refs):
        return "manual-install rollback source"
    return ""


def plan(cfg, guard: Guard | None = None, now: float | None = None, only=None) -> dict:
    """Every action, with the hash it was planned on. `only` (a set of CLASSES) skips the scans no requested class
    needs -- the launch watcher plans gzip/launch without hashing every agent-output file -- and counts a planned
    action of an unrequested class as kept, so the rows still sum to what was scanned."""
    cfg = dict(cfg, archive_root=str(_abs(cfg,cfg['archive_root'] or '.claude/archive')))
    now = time.time() if now is None else now
    guard = guard or Guard(cfg)
    actions, rows = [], {}
    unreadable_paths = []
    wants = (lambda *classes: True) if not only else (lambda *classes: bool(set(classes) & set(only)))

    def count(row, n):
        rows[row] = rows.get(row, 0) + n

    def act(action, row):
        if action['class'] in {'launch', 'scratch'}:
            p = Path(action['path'])
            if newest_activity(p) > now - cfg['active_hours'] * 3600:
                count('kept: folder active', action['bytes'])
                return
            why = folder_protection(p, guard)
            if why:
                count('kept: protected ('+why+')', action['bytes'])
                return
        if action['class'] in {'build', 'launch', 'scratch', 'expire'}:
            why = certainty.obsolete_reason(action['path'], cfg, action.get('hash'))
            if why:
                count('kept: '+why, action['bytes'])
                return
        if only and action["class"] not in only:
            count("kept: class not requested (--only)", action["bytes"])
        else:
            actions.append(action)
            count(row, action["bytes"])

    # ---- agent output: recreatable, build, link
    held = []
    all_tasks = task_folders(cfg, held) if wants("derived", "recreatable", "build", "link", "archive") else []
    # ---- archive first: a folder going to the NAS whole is not also picked over file by file
    archived = set()
    plan_pins, agent_total = [], None
    archive_inventory = {}
    if cfg["archive_enabled"] and wants("archive"):
        # A staging folder is never archived whole: the superseded rules decide it, and a rollback source stays.
        staged = [_real(_abs(cfg, r)) for r in cfg["staging_roots"]]
        archivable = [(t, s) for t, s in all_tasks
                      if not any(_real(t) == r or _strictly_inside(_real(t), r) for r in staged)]
        arch, arch_rows, plan_pins, agent_total = archive_selection(cfg, guard, archivable, now,
                                                                 held=held, inventory=archive_inventory)
        for k, v in arch_rows.items():
            count(k, v)
        for a in arch:
            act(a, "plan: done folder -> zip to NAS, verify, payload to Recycle Bin")
            archived.add(_key(a["path"]))
    held_keys = [_key(item["path"]) for item in held]
    all_tasks = [(t, s) for t, s in all_tasks
                 if not any(_key(t) == k or _key(t).startswith(k + os.sep) for k in held_keys)]
    tasks = ([(t, s) for t, s in all_tasks if _key(t) not in archived]
             if wants("derived", "recreatable", "build", "link") else [])
    live = live_index(cfg) if tasks else {}
    live_hash = {}
    by_size = {}

    def queue_duplicate(f, task, size, digest=None):
        # A chmod changes every name of an inode. A file that already has another name may share it with a live
        # Data/ file a mod manager deployed by hard link (Vortex, MO2): whose file that is, nothing here can tell.
        if f.stat().st_nlink != 1:
            count("kept: already hard-linked elsewhere (ownership unknown)", size)
        else:
            by_size.setdefault(size, []).append((f, task, digest))

    for task, state in tasks:
        for f in walk_files(task):
            if wants("superseded") and any(not staging_root_refusal(_abs(cfg, r), cfg) and
                                           _strictly_inside(_real(f), _real(_abs(cfg, r)))
                                           for r in cfg["staging_roots"]):
                continue                    # staging scan accounts for these bytes exactly once
            try:
                st = f.stat()
                size = st.st_size
            except OSError as exc:
                raise OSError("candidate could not be inspected: {}".format(f)) from exc
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
            if under_pin(f, task):
                count("kept: pinned (.housekeep-keep)", size)
                continue
            if st.st_nlink != 1:
                count("kept: already hard-linked elsewhere (ownership unknown)", size)
                continue
            try:
                source = derived_source(f, task)
            except OSError as error:
                count("kept: trace provenance unavailable ({})".format(type(error).__name__), size)
                unreadable_paths.append(str(f))
                continue
            if source is not None:
                act({"class": "derived", "path": str(f), "bytes": size, "hash": blake(f),
                     "task": str(task), "source": str(source)}, "plan: decoded trace -> Recycle Bin")
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
                queue_duplicate(f, task, size, h)
                continue
            if size >= cfg["min_bytes"]:
                queue_duplicate(f, task, size)
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
            why = protected_inside(guard, p) if age >= cfg["recycle_after_days"] else ""
            if why:
                count("kept: protected ({})".format(why), size)
            elif age >= cfg["recycle_after_days"]:
                act({"class": "launch", "path": str(p), "bytes": size, "fingerprint": fingerprint(p)},
                    "plan: launch folder past recycle_after_days -> Recycle Bin")
            elif age >= cfg["gzip_after_days"]:
                for f in walk_files(p):
                    s = f.stat().st_size
                    if f.suffix.lower() == ".gz" or s < 65536:
                        count("kept: launch log, recent or already compressed", s)
                        continue
                    if not has_recycle_bin():    # the original stays, so the next plan would meet its own .gz and fail
                        count("kept: launch log (no Recycle Bin on this platform)", s)
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
        prev_path = None
        for i, e in enumerate(stamps):
            p = Path(e.path)
            files = sorted(walk_files(p))
            why = next((certainty.rolling_protection(f, guard) for f in [p, *files] if certainty.rolling_protection(f, guard)), '')
            if why:
                count("kept: protected ({})".format(why), sum(f.stat().st_size for f in files))
                continue
            sig = certainty.snapshot_signature(p, blake)
            size = sum(s for _, s, _ in sig)
            why = protected_inside(guard, p, "snapshot") if sig == prev and i != len(stamps) - 1 else ""
            if why:
                count("kept: protected ({})".format(why), size)
            elif sig == prev and i != len(stamps) - 1:
                act({"class": "snapshot", "path": str(p), "bytes": size, "fingerprint": fingerprint(p),
                     "duplicate_of": str(prev_path), "duplicate_signature": certainty.digest(sig)},
                    "plan: snapshot repeating the previous one -> Recycle Bin")
            else:
                count("kept: snapshot with distinct content (or the newest)", size)
                prev = sig
                prev_path = p

    # ---- ordinary archives are reviewed independently of age-based retention settings
    archive_review = review_archives(cfg, guard) if wants('expire') else {}
    for row in archive_review.get('rows', []):
        if row['state']=='review retirement' and archive_review['state']=='complete':
            act({'class':'expire','path':row['path'],'bytes':row['bytes'],'mtime':row['mtime'],
                 'hash':row['content_hash']}, 'plan: archive has exact obsolete decision -> Recycle Bin')
        else:
            count('kept: archive '+row['reason'], row['bytes'])

    # ---- named scratch folders
    for s in (cfg["scratch"] if wants("scratch") else []):
        p = _abs(cfg, s)                  # relative to the toolkit folder, as the example config says
        if p.is_dir():
            fp = fingerprint(p)
            why = protected_inside(guard, p)
            if why:
                count("kept: protected ({})".format(why), fp[1])
                continue
            act({"class": "scratch", "path": str(p), "bytes": fp[1], "fingerprint": fp},
                "plan: named scratch folder -> Recycle Bin")

    # ---- direct versioned staging folders; uncertainty keeps the whole folder
    for r in (cfg["staging_roots"] if wants("superseded") else []):
        root = _abs(cfg, r)
        if staging_root_refusal(root, cfg):  # refuse staging before any root scan, without disabling other classes
            continue
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            try:
                if path.name == "_superseded":
                    count("kept: staging supersession list", path.stat().st_size if path.is_file() else dir_size(path))
                    continue
                size = dir_size(path) if path.is_dir() else path.stat().st_size
            except OSError as error:
                count("kept: staging size unavailable (unknown bytes: {})".format(type(error).__name__), 0)
                unreadable_paths.append(str(path))
                continue
            try:
                why = superseded_reason(path, root, cfg, guard, now) if path.is_dir() else "not a staging folder"
            except (OSError, ValueError, IndexError) as error:
                why = "staging protection unavailable ({}: {})".format(type(error).__name__, error)
            if why:
                count("kept: " + why, size)
            else:
                act({"class": "superseded", "path": str(path), "bytes": size, "fingerprint": fingerprint(path),
                     "staging_root": str(root)}, "plan: superseded staging -> Recycle Bin")

    return {"created": now, "root": str(cfg["root"]), "actions": actions, "rows": rows,
            "unreadable_paths": unreadable_paths,
            "large_pins": [[str(p), b] for p, b in plan_pins], "agent_total": agent_total,
            "coverage": {"state": "accounted_with_holds" if held or archive_review.get("state")=="partial" else "complete",
                         "classes_requested": sorted(only or CLASSES),
                         "agent_output_requested": wants("derived", "recreatable", "build", "link", "archive"),
                         "agent_output_roots": [str(_abs(cfg, r)) for r in cfg["agent_output"]],
                         "targets_inspected": len(all_tasks), "targets_held": len(held),
                         "archive_inventory": archive_inventory,
                         "archive_review": archive_review,
                         "held": held, "unmeasured_held_bytes": bool(held or archive_review.get("state")=="partial")}}


def print_coverage(doc):
    coverage = doc.get("coverage")
    if coverage is None:
        return  # Legacy receipts do not establish an inventory denominator.
    if not coverage["agent_output_requested"]:
        print("Agent-output inspection was not requested (--only); this is a class-limited plan.")
    print("target inventory: {} inspected, {} held; {}".format(
        coverage["targets_inspected"], coverage["targets_held"], coverage["state"]))
    archive = coverage.get("archive_inventory")
    if archive:
        print("archive inventory: {} candidates inspected, {} candidates held, {} roots held".format(
            archive["candidates_inspected"], archive["candidates_held"], archive["roots_held"]))
    review = coverage.get("archive_review", {})
    if review:
        print("ordinary archive review: {}; {} ZIP(s) inspected; {} held branch(es); {}".format(
            review["state"],len(review["rows"]),len(review["held"]),review["root"]))
        for item in review["held"]:
            print("  HELD ARCHIVES {} ({})".format(item["path"],item["reason"]))
    if coverage["held"]:
        print("Byte totals exclude held targets; their sizes are unknown and they have no cleanup actions.")
        for item in coverage["held"]:
            print("  HELD {} ({})".format(item["path"], item["reason"]))


def accounting(rows: dict, actions) -> str:
    """The plan rows against the actions behind them, two totals built separately: `rows sum` used to compare a total with
    itself and could not fail."""
    planned = sum(v for k, v in rows.items() if k.startswith("plan"))
    return "OK" if planned == sum(a["bytes"] for a in actions) else "ACCOUNTING MISMATCH"


def print_rows(rows: dict, title: str, actions=None):
    total = sum(rows.values())
    print("{}: {:.2f} GiB logical file bytes scanned (hard links included)".format(title, total / GIB))
    for k in sorted(rows, key=lambda k: (not k.startswith("plan"), k)):
        print("  {:>9.2f} GiB  {}".format(rows[k] / GIB, k))
    kept = sum(v for k, v in rows.items() if not k.startswith("plan"))
    print("  {:>9.2f} GiB  (left alone, all 'kept' rows)".format(kept / GIB))
    planned = total - kept
    print("  {:>9.2f} GiB  (planned){}".format(planned / GIB, "" if actions is None else
                                               "   rows match the actions: " + accounting(rows, actions)))


# --------------------------------------------------------------------------- applying

def has_recycle_bin() -> bool:
    return os.name == "nt"


def sweep_switched_off() -> bool:
    """SKYGUARD_HOUSEKEEP_SWEEP=off: the USER's switch. It stops the session-start sweep and nothing else: `housekeep
    recycle`, `apply` and kb-guard's rotation still use the Recycle Bin (review2 B-tools L2 / C-docs L7)."""
    return os.environ.get("SKYGUARD_HOUSEKEEP_SWEEP", "").strip().lower() == "off"


def under_test_harness() -> bool:
    """A test run: pytest, or the toolkit's contained launcher without pytest (tests/run_harness_tests.py sets
    HARNESS_FIXTURE_ROOT) -- unless HOUSEKEEP_REAL_BIN_IN_TESTS, the one opt-in, lets a deliberate integration test
    through. tools/skyguard/run.py's session-sweep launch guard uses the same markers and the same opt-in. A test
    that needs a bin injects a fake recycler; the real one would show the user Windows dialogs (2026-10-20)."""
    if os.environ.get("HOUSEKEEP_REAL_BIN_IN_TESTS"):
        return False
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) or bool(os.environ.get("HARNESS_FIXTURE_ROOT"))


def _recycle_batch(recycler, batch) -> dict:
    """A shell call that outlives its timeout is every item's failure to report, never a traceback."""
    try:
        return recycler(batch)
    except subprocess.TimeoutExpired as exc:
        return {str(p): "the Recycle Bin call timed out ({} s): check this item".format(exc.timeout) for p in batch}


def recycle(paths) -> dict:
    """{path: None | error} -- one PowerShell process for the whole batch. Recycle Bin only, never a delete."""
    paths = [str(p) for p in paths]
    if not paths:
        return {}
    # No test may reach the real bin; the guard sits at the one call that touches the shell, so a test that forgets
    # its fake fails loudly instead of prompting the user.
    if under_test_harness():
        raise RuntimeError("housekeep.recycle refused under the test harness (pytest or HARNESS_FIXTURE_ROOT): "
                           "inject a fake recycler (no real Recycle Bin)")
    if not has_recycle_bin():
        # No Recycle Bin implementation here (Windows only). Never a delete instead: every item stays, and says so.
        return {p: "no Recycle Bin on this platform: left in place" for p in paths}
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
    # An item too big for its bin is refused before this call (bin_refusal) and stays in place; the flag is for a race.
    # NOERRORUI suppresses error dialogs. WANTNUKEWARNING is NOT a guarantee: MEASURED 2026-10-31, a file on a
    # network share was destroyed silently (rc 0, no dialog) with these flags AND without 0x400. bin_refusal() above
    # is the only thing keeping no-bin locations out of this call; never loosen it on the strength of this flag.
    # https://learn.microsoft.com/windows/win32/api/shellapi/ns-shellapi-shfileopstructw
    script = (
        "Add-Type -TypeDefinition @'\n"
        "using System; using System.Runtime.InteropServices;\n"
        "public static class HkBin {\n"
        " [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]\n"
        " struct Op { public IntPtr hwnd; public uint wFunc; public string pFrom; public string pTo; public ushort fFlags;\n"
        "  public bool fAnyOperationsAborted; public IntPtr hNameMappings; public string lpszProgressTitle; }\n"
        " [DllImport(\"shell32.dll\", CharSet = CharSet.Unicode)] static extern int SHFileOperationW(ref Op op);\n"
        " public static string Recycle(string p) {\n"
        "  var op = new Op { wFunc = 3, pFrom = p + \"\\0\\0\", fFlags = 0x4000 | 0x40 | 0x10 | 0x200 | 0x4 | 0x400 };\n"
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


# `housekeep recycle PATH...` is a shell door to the Recycle Bin (kb-guard's rotation uses it), so it takes only
# what this toolkit's own retention may remove (docs/RETENTION.md), never a path a mistyped argument could name:
#   allowed  strictly inside one of the rolling tool stores (<root>/.claude/retired, .claude/backups/kb,
#            .claude/backups/auto_snapshots), a configured agent_output / launch_logs / legacy_snapshots root, a
#            named scratch folder (or that folder itself), or the system temp folder; <root> is the toolkit root
#            and $CLAUDE_PROJECT_DIR (kb-guard's own root rule)
#   refused  anything else, and whatever the allow rule says: a drive or filesystem root, the home folder, the toolkit
#            or install root or a folder above it, a link or reparse point, a Saves folder or anything inside one, a
#            save (.ess/.skse) or a folder holding one, and a Forever name (a *backup* folder below the allowed root,
#            known-good*, AUDIT_LOG.txt)
REPARSE_POINT = 0x400                 # FILE_ATTRIBUTE_REPARSE_POINT (a junction is one)
ROLLING_STORES = (Path(".claude") / "retired", Path(".claude") / "backups" / "kb",
                  Path(".claude") / "backups" / ROLLING_SNAPSHOT_STORE)


def _real(p) -> Path:
    return Path(os.path.realpath(os.path.abspath(str(p))))


def _strictly_inside(child: Path, parent: Path) -> bool:
    return os.path.normcase(str(parent)) != os.path.normcase(str(child)) and \
        os.path.normcase(str(parent)) in {os.path.normcase(str(a)) for a in child.parents}


def _temp_root() -> Path:
    return _real(tempfile.gettempdir())


def recycle_refusal(cfg, path) -> str:
    """Why the `recycle` command will not send this path to the Recycle Bin, or "" when it may."""
    p = Path(os.path.abspath(str(path)))
    try:
        st = os.lstat(p)
    except OSError:
        return "does not exist"
    if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & REPARSE_POINT:
        return "a link or reparse point"
    real = _real(p)
    if real.parent == real:
        return "a drive or filesystem root"
    if os.path.normcase(str(real)) == os.path.normcase(str(_real(Path.home()))):
        return "the home folder"
    bases = [_real(cfg["root"]), _real(TOOLKIT_ROOT)]
    if os.environ.get("CLAUDE_PROJECT_DIR"):
        bases.append(_real(os.environ["CLAUDE_PROJECT_DIR"]))
    for b in bases:
        if os.path.normcase(str(b)) == os.path.normcase(str(real)) or _strictly_inside(b, real):
            return "the toolkit or install root, or a folder above it"
    if any(part.lower() == "saves" for part in real.parts):
        return "a Saves folder or inside one"
    try:
        files = tuple(walk_files(real, strict=True))
    except OSError:
        return "unreadable tree or file metadata (inspection incomplete)"
    for f in files:
        if f.suffix.casefold() == ".etl":
            return "a source measurement (ETL) or a folder holding one"
        if str(f).lower().endswith(SAVE_EXTS):
            return "a save or a folder holding one ({})".format(f.name)
    allowed = [(b / s, False) for b in bases for s in ROLLING_STORES]
    allowed += [(_real(_abs(cfg, r)), False) for k in ("agent_output", "launch_logs", "legacy_snapshots")
                for r in cfg[k]]
    allowed += [(_real(_abs(cfg, r)), True) for r in cfg["scratch"]]
    allowed.append((_temp_root(), False))
    home = None
    for root, itself_ok in allowed:
        if _strictly_inside(real, root) or (itself_ok and os.path.normcase(str(real)) == os.path.normcase(str(root))):
            home = root
            break
    if home is None:
        return "outside the folders housekeep may recycle from (docs/RETENTION.md)"
    rel = [x.lower() for x in real.relative_to(home).parts] if real != home else []
    if any("backup" in x for x in rel):
        return "inside a backup folder (Forever)"
    if any(x.startswith("known-good") for x in rel):
        return "a known-good baseline (Forever)"
    for f in files:
        low = f.name.lower()
        if low == "audit_log.txt" or any(x.lower().startswith("known-good") for x in f.relative_to(real).parts) \
                or any("backup" in x.lower() for x in f.relative_to(real).parts[:-1]):
            return "holds a Forever file ({})".format(f.relative_to(real).as_posix())
    return ""


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
    if cap is None:
        return "left in place: Recycle Bin capacity could not be verified"
    if cap <= 0:
        return "left in place: this volume disables recycling"
    if size > cap:
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
    os.chmod(path, stat.S_IMODE(os.stat(path).st_mode) & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


class _Memo:
    """Per-apply caches: a live master is hashed once, not once per copy of it; a task folder is walked once."""

    def __init__(self):
        self.hashes, self.newest = {}, {}
        self.links = {}                   # canonical -> links THIS apply made to it (the only extra names it may have)

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
    if action["class"] in ("derived", "recreatable", "build", "link", "gzip"):
        why = guard.why_protected(p)
        if why:
            return "protected ({})".format(why)
        if p.stat().st_size != action["bytes"] or blake(p) != action["hash"]:
            return "changed since the plan"
    if action["class"] in ("derived", "recreatable", "build", "link"):
        if memo.task_newest(action["task"]) > now - cfg["active_hours"] * 3600:
            return "task folder written to since the plan (active)"
    if action["class"] == "recreatable":
        if not Path(action["live"]).is_file() or memo.hash(action["live"]) != action["hash"]:
            return "live file no longer identical"
    if action["class"] == "derived":
        source = Path(action["source"])
        try:
            if not source.is_file() or not plain_path(source) or derived_source(p, Path(action["task"])) is None:
                return "trace source gone or no longer a decoded trace"
        except OSError as error:
            return "trace source inspection unavailable ({}: {})".format(type(error).__name__, error)
    if action["class"] == "superseded":
        try:
            why = superseded_reason(p, Path(action["staging_root"]), cfg, guard, now)
        except (OSError, ValueError, IndexError) as error:
            return "staging protection unavailable ({}: {})".format(type(error).__name__, error)
        if why:
            return why
    if action["class"] == "link":
        c = Path(action["canonical"])
        if not any(c.resolve().is_relative_to(_abs(cfg, base).resolve()) for base in cfg['agent_output']):
            return 'canonical copy is outside agent output'
        if not c.is_file() or memo.hash(c) != action["hash"]:
            return "canonical copy changed or gone"
        if guard.why_protected(c):
            return "canonical copy is protected"
        if p.stat().st_nlink != 1 or c.stat().st_nlink != 1 + memo.links.get(_key(c), 0):
            return "hard-linked elsewhere since the plan (ownership unknown)"
    if action["class"] == "archive":
        why = _archive_module().archive_scope(action, cfg)
        if why:
            return why
        if list(fingerprint(p)) != list(action["fingerprint"]):
            return "folder changed since the plan"
        wrote, read = activity(p)
        if read > now - cfg["read_veto_hours"] * 3600:
            return "something read here since the plan"
        if (now - wrote) / DAY < cfg["min_idle_days"] and not (p/CLOSED_MARK).is_file():
            return "something wrote here since the plan"
        why = is_done(p, action["idle_days"], live_state_tokens(cfg), rb_job_cwds(), guard)
        if why:
            return "no longer done ({})".format(why)
        if not cfg["archive_enabled"]:
            return "archiving is off"
        return ""
    if action["class"] == "expire":
        root = Path(cfg['archive_root'] or Path(cfg['root'])/'.claude/archive')
        certainty.plain_path(p)
        if not p.resolve().is_relative_to(root.resolve()) or p.suffix.lower()!='.zip':
            return 'archive is outside configured archive root'
        if not root.is_relative_to(Path(cfg['root'])):
            guard.tracked.update(tracked_files(root))
        why = archive_protection(guard, p)
        if why:
            return 'protected ('+why+')'
        st = p.stat()
        if st.st_nlink!=1 or st.st_mtime != action["mtime"] or st.st_size != action["bytes"]:
            return "archive zip changed since the plan"
        actual = _archive_module()._retirement_hash(p,time.monotonic()+cfg['archive_review_seconds'])
        if actual!=action.get('hash'):
            return 'archive bytes changed since the plan; create a new plan'
        return certainty.obsolete_reason(p, cfg, actual)
    if action["class"] in ("launch", "snapshot", "scratch", "superseded"):
        why = protected_inside(guard, p, action["class"])
        if why:
            return "protected ({})".format(why)
        if action['class'] != 'snapshot':
            if newest_activity(p) > now - cfg['active_hours'] * 3600:
                return 'folder is active'
            for dp, ds, fs in os.walk(p):
                for f in [Path(dp), *(Path(dp)/n for n in ds+fs)]:
                    certainty.plain_path(f)
                    why = guard.why_protected(f) or ('pinned' if f.name == KEEP_PIN else '')
                    if why:
                        return 'protected ('+why+')'
        # A whole-folder action must never carry a save into the bin with it, whatever the plan or config says.
        if p.is_dir() and any(f.lower().endswith(SAVE_EXTS) for _, _, fn in os.walk(p) for f in fn):
            return "protected (folder holds a save)"
        if fingerprint(p) != action["fingerprint"]:
            return "folder changed since the plan"
    if action['class'] == 'snapshot':
        try:
            return certainty.snapshot_refusal(action, cfg, guard, blake)
        except (OSError, ValueError) as exc:
            return 'snapshot retained-copy proof failed: '+str(exc)
    if action['class'] in {'build', 'launch', 'scratch', 'expire'}:
        return certainty.obsolete_reason(p, cfg, action.get('hash'))
    return ""


def link(action, canonical_links=1):
    """`canonical_links`: the names the canonical copy may have right now -- itself plus the links this apply made.
    Any other name may be a live file elsewhere, and the read-only attribute below would change it too."""
    dup, canon = Path(action["path"]), Path(action["canonical"])
    tmp = dup.with_name(dup.name + ".housekeep-link")
    if tmp.exists():
        raise OSError("leftover {} -- inspect it by hand".format(tmp))
    if dup.stat().st_nlink != 1 or canon.stat().st_nlink != canonical_links:
        raise OSError("hard-linked elsewhere before linking (ownership unknown)")
    os.link(canon, tmp)
    if (os.stat(tmp).st_ino != os.stat(canon).st_ino or blake(tmp) != action["hash"]
            or dup.stat().st_nlink != 1 or canon.stat().st_nlink != canonical_links + 1):
        recycle([tmp])                                           # our own just-made link -- still the bin, never a delete
        raise OSError("the new link's bytes or link count are not what was planned")
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
    if d.hexdigest() != action["hash"] or blake(src) != action["hash"]:
        recycle([gz])                                             # our own bad output -- still the bin, never a delete
        raise OSError("gzip round trip does not reproduce the original")
    st = src.stat()
    os.utime(gz, (st.st_atime, st.st_mtime))
    return gz


def archive_protection(guard, path):
    """A source-group label 'backups' is not an eternal veto on an explicitly obsolete ZIP.
    Every independent save, baseline, tracked, linked and custom protection still applies."""
    why = guard.why_protected(path)
    if why != 'backup folder':
        return why
    k = _key(path)
    if k in guard.tracked:
        return 'git-tracked'
    if k in guard.links:
        return 'linked evidence'
    if any(fnmatch.fnmatch(str(path).replace('\\','/').lower(), g) for g in guard.globs):
        return 'protected_globs'
    return ''


def review_archives(cfg, guard=None):
    guard = Guard(cfg) if guard is None else guard
    root = Path(cfg['archive_root'] or Path(cfg['root'])/'.claude/archive')
    if not root.is_absolute():
        root = Path(cfg['root'])/root
    try:
        root.lstat()
        certainty.plain_path(root)
        if not root.is_relative_to(Path(cfg['root'])):
            guard.tracked.update(tracked_files(root))
    except FileNotFoundError as exc:
        if not str(root).startswith(('\\\\','//')):
            return dict(state='absent',root=str(root),rows=[],held=[])
        return dict(state='partial',root=str(root),rows=[],held=[dict(path=str(root),reason=str(exc))])
    except (OSError, ValueError) as exc:
        return dict(state='partial',root=str(root),rows=[],held=[dict(path=str(root),reason=str(exc))])
    return _archive_module().review_archives(dict(cfg,archive_root=str(root)),
                                             protect=lambda p:archive_protection(guard,p))


_ARCHIVE_MODULE = None


def _archive_module():
    """The shared archive helper, cached only for this process."""
    global _ARCHIVE_MODULE
    if _ARCHIVE_MODULE is None:
        spec = importlib.util.spec_from_file_location("housekeep_archive", Path(__file__).with_name("housekeep_archive.py"))
        _ARCHIVE_MODULE = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_ARCHIVE_MODULE)
    return _ARCHIVE_MODULE



def cleanup_code_sha256():
    return _daily_module().code_sha256()


def apply(plan_doc: dict, cfg, only=None, recycler=recycle, now: float | None = None, deadline: float | None = None,
          summary: bool = False):
    """Do exactly the plan, re-checking each action. `deadline` (epoch): no new action starts after it; the rest is
    counted as deferred for the next run. summary=True returns {"gib", "failed", "deferred"} instead of an exit code.
    Archive payloads go to the bin in batches at the end of the class, not one shell call per folder."""
    if any(a['class'] in {'archive','expire'} and (not only or a['class'] in only) for a in plan_doc['actions']):
        if cfg.get('approved_sha256') != cleanup_code_sha256():
            print('REFUSED: archive cleanup version has not been approved after first-output review: '+cleanup_code_sha256())
            return {'gib':0,'failed':1,'deferred':0} if summary else 1
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
            try:
                why = check(a, cfg, guard, now, memo)
            except (OSError, ValueError, TimeoutError) as exc:
                why = 'candidate could not be rechecked: '+str(exc)
            except Exception as exc:
                if type(exc).__name__ != 'ArchiveError':
                    raise
                why = 'archive proof failed: '+str(exc)
            if why:
                print("  REFUSED  {:<11} {}  ({})".format(cls, a["path"], why))
                failed += 1
                continue
            try:
                if cls == "link":
                    ck = _key(a["canonical"])
                    link(a, 1 + memo.links.get(ck, 0))
                    memo.links[ck] = memo.links.get(ck, 0) + 1
                    done += a["bytes"]
                elif cls == "gzip":
                    compress(a)
                    to_recycle.append(a)
                elif cls == "archive":
                    ha = _archive_module()
                    k = _key(a["path"]) + os.sep
                    extra = [x for x in guard.tracked | guard.links if x.startswith(k)] + archive_keeps(Path(a["path"]), guard)
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
            ready_archives = []
            for a, rec in archived:
                try:
                    ha.verify_source(Path(a['path']), rec)
                    ready_archives.append((a, rec))
                except (OSError, ValueError, ha.ArchiveError) as exc:
                    print('  REFUSED  archive {} ({})'.format(a['path'], exc))
                    failed += 1
            archived = ready_archives
            victims = [str(v) for _, rec in archived for v in rec["victims"]]
            res = {}
            for i in range(0, len(victims), RECYCLE_BATCH):
                res.update(_recycle_batch(recycler, victims[i:i + RECYCLE_BATCH]))
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
            ready = []
            for a in to_recycle:
                try:
                    if a['class'] == 'gzip':
                        d = hashlib.blake2b(digest_size=16)
                        with gzip.open(a['path']+'.gz', 'rb') as stream:
                            for block in iter(lambda:stream.read(1<<20), b''):
                                d.update(block)
                        why = '' if blake(Path(a['path'])) == a['hash'] == d.hexdigest() else 'gzip replacement/source changed'
                    else:
                        why = check(a, cfg, guard, time.time(), _Memo())
                    if a['class'] == 'snapshot' and any(
                            other['path'] == a.get('duplicate_of') for other in plan_doc['actions']):
                        why = 'retained snapshot is also scheduled for removal'
                except Exception as exc:
                    if not isinstance(exc, (OSError, ValueError)) and type(exc).__name__ != "ArchiveError":
                        raise
                    why = 'final replacement proof failed: '+str(exc)
                if why:
                    print('  REFUSED  {}  ({})'.format(a['path'], why))
                    failed += 1
                else:
                    ready.append(a)
            to_recycle = ready
            paths = [a["path"] for a in to_recycle]
            res = {}
            for i in range(0, len(paths), RECYCLE_BATCH):
                res.update(_recycle_batch(recycler, paths[i:i + RECYCLE_BATCH]))
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
    entries = []
    # House Carl's check keeps its scratch beside the masters when they are not on the temp drive, and a failed check leaves
    # its report there. Same names, same gates; an absent folder is simply none.
    for parent in (temp, Path(cfg["root"]) / ".housecarl-tmp"):
        try:
            entries += list(os.scandir(parent))
        except OSError:
            continue
    for e in entries:
        try:
            if e.is_symlink() or not e.is_dir(follow_symlinks=False):
                continue
            # A junction is not a symlink to os.DirEntry on Python 3.10: skip every reparse point outright.
            if getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400:
                continue
        except OSError:
            continue
        # Only names this toolkit creates. pytest-of-<user>/ is shared by every project on the machine (the X4
        # toolkit's runs, anyone's): an unattended sweep cannot tell this toolkit's runs from theirs, so it never
        # touches it. This toolkit's own test runs live under its skyrim-harness- prefix (tests/run_harness_tests.py).
        if prefixes and e.name.startswith(prefixes):
            yield Path(e.path), "toolkit temp"


def sweep_temp(cfg, temp: Path | None = None, now: float | None = None, recycler=None) -> dict:
    """Recycle every candidate that passes the gates, in one batch. What the bin refuses stays where it is."""
    temp = Path(temp or tempfile.gettempdir())
    now = time.time() if now is None else now
    cutoff = now - cfg["temp_after_hours"] * 3600
    rows = {"recycled": 0, "kept: changed recently": 0, "kept: in use": 0,
            "kept: disposability unverified": 0, "kept: protected": 0, "failed": 0}
    ready = []
    failed = []
    try:
        guard = Guard(cfg)
        candidates = list(temp_candidates(cfg, temp))
    except OSError as exc:
        rows["failed"] = 1
        return {"rows": rows, "failed": [str(exc)], "temp": str(temp), "preflight_error": str(exc)}
    for p, _why in candidates:
        try:
            members = [Path(dp)/n for dp, ds, fs in os.walk(p) for n in ds+fs]
            for f in [p, *members]:
                certainty.plain_path(f)
            if any(certainty.rolling_protection(f, guard) for f in [p, *members]) or any('backup' in f.name.lower() for f in members if f.is_dir()):
                rows['kept: protected'] += 1
                continue
            if not certainty.disposable_temp(p, cfg['root']) and certainty.obsolete_reason(p, cfg):
                rows['kept: disposability unverified'] += 1
                continue
            if _newest_shallow(p) > cutoff:
                rows["kept: changed recently"] += 1
                continue
        except (OSError, ValueError) as exc:
            rows['failed'] += 1
            failed.append('{} ({})'.format(p, exc))
            continue
        if _in_use(p):
            rows["kept: in use"] += 1
        else:
            ready.append(p)
    for p, err in (recycler or recycle)(ready).items():
        if err:
            rows["failed"] += 1
            failed.append("{}  ({})".format(p, err))
        else:
            rows["recycled"] += 1
    return {"rows": rows, "failed": failed, "temp": str(temp)}


# --------------------------------------------------------------------------- retired store batches
# The guard prunes its own stores (script snapshots, recovery blobs, old session logs) by RENAMING each item into a
# batch folder under <project>/.claude/retired/ (tools/skyguard/retire.py): a hook has no time for the Recycle Bin.
# The session sweep moves every settled batch to the bin whole. Off Windows there is no bin to call, so the batches
# wait there and the session note says so; nothing is ever deleted.
RETIRED_REL = Path(".claude") / "retired"
RETIRED_SETTLE_S = 600                # a batch nothing touched for 10 minutes is finished (a prune takes seconds)


def sweep_retired(roots, now: float | None = None, recycler=None) -> dict:
    now = time.time() if now is None else now
    rows = {"recycled": 0, "kept: changed recently": 0, "kept: in use": 0, "waiting: no Recycle Bin here": 0,
            "failed": 0}
    ready, failed, seen = [], [], set()
    for root in roots:
        base = Path(root) / RETIRED_REL
        if not base.is_dir() or _key(base) in seen:
            continue
        seen.add(_key(base))
        try:
            entries = list(os.scandir(base))
        except OSError:
            continue
        for e in entries:
            try:
                reparse = getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400
                if e.is_symlink() or reparse or not e.is_dir(follow_symlinks=False):
                    continue                           # retire.py makes plain folders only; a link is not ours
                p = Path(e.path)
                settling = _newest_shallow(p) > now - RETIRED_SETTLE_S
            except OSError:
                continue
            if settling:
                rows["kept: changed recently"] += 1
            elif _in_use(p):
                rows["kept: in use"] += 1
            else:
                ready.append(p)
    if recycler is None and os.name != "nt":
        rows["waiting: no Recycle Bin here"] += len(ready)
        return {"rows": rows, "failed": failed, "waiting_bytes": sum(dir_size(p) for p in ready)}
    for p, err in (recycler or recycle)(ready).items():
        if err:
            rows["failed"] += 1
            failed.append("{}  ({})".format(p, err))
        else:
            rows["recycled"] += 1
    return {"rows": rows, "failed": failed, "waiting_bytes": 0}


def _project_of(state: Path):
    """The project a session note belongs to: run.py writes it at <project>/.claude/diag/housekeep-session.txt."""
    state = Path(state).absolute()
    if state.parent.name == "diag" and state.parent.parent.name == ".claude":
        return state.parents[2]
    return None


def worktree_count(root: Path) -> int | None:
    try:
        out = subprocess.run(["git", "-C", str(root), "worktree", "list", "--porcelain"], capture_output=True,
                             text=True, timeout=30, **NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError("could not count worktrees") from exc
    if out.returncode:
        if "not a git repository" in out.stderr:
            return None
        raise OSError("git worktree inventory failed (exit {})".format(out.returncode))
    return max(0, sum(1 for line in out.stdout.splitlines() if line.startswith("worktree ")) - 1)


def session(cfg, state: Path) -> int:
    """The session-start job: sweep the toolkit's temp dirs, then write a one-line note for the next banner when
    something needs the user (free space near the floor, Codex sessions or worktrees past their threshold).
    The sweep runs at most once per sweep_every_hours (the state file's age is the clock). The daily run has its own
    clock (housekeep_daily.due) and is announced by the session-start hook before this process starts; its summary
    becomes the next session's note. SKYGUARD_HOUSEKEEP_SWEEP=off: nothing."""
    if sweep_switched_off():
        return 0
    try:
        # A negative age (clock set back, or Windows' coarse time.time() a tick behind the file's mtime) is due.
        sweep_due = not (0 <= time.time() - state.stat().st_mtime < cfg["sweep_every_hours"] * 3600)
    except OSError:
        sweep_due = True
    daily = _daily_module()
    daily_due = daily.due(cfg) == ""
    if not sweep_due and not daily_due:
        return 0
    notes, temp_raised = [], False
    r = {"recycled": 0, "failed": 0}
    res = {"rows": r, "failed": []}
    ret = {"rows": {"recycled": 0, "failed": 0, "waiting: no Recycle Bin here": 0}, "waiting_bytes": 0}
    try:
        housekeep_backups = certainty
        note = housekeep_backups.periodic_note(cfg)
        if note:
            notes.append(note)
    except (OSError, ValueError) as exc:
        notes.append("cold backup review unavailable: {}".format(exc))
    try:
        note = _archive_module().periodic_note(cfg, lambda:review_archives(cfg))
        if note:
            notes.append(note)
    except (OSError, ValueError) as exc:
        notes.append('task archive review unavailable: {}'.format(exc))
    if sweep_due:
        try:
            res = sweep_temp(cfg)
        except Exception as e:                # the state file must still be written, or the throttle never engages
            res, temp_raised = {"rows": {"recycled": 0, "failed": 1}, "failed": []}, True
            notes.append("temp sweep failed ({}: {})".format(type(e).__name__, str(e)[:200]))
        try:
            if not res.get("preflight_error"):    # a protection that could not be evaluated: no recycling at all
                ret = sweep_retired([cfg["root"]] + [p for p in [_project_of(state)] if p is not None])
        except Exception as e:                # same rule: the state file is still written
            notes.append("retired-store sweep failed ({}: {})".format(type(e).__name__, str(e)[:200]))
        r = res["rows"]
    if daily_due:
        try:
            notes.append(daily.run(cfg))
        except Exception as e:                    # never cost the next session its note; say what broke instead
            notes.append("housekeep: today's run FAILED ({}: {}) -- {}".format(
                type(e).__name__, e, daily.f(cfg, "LOG")))
    if res.get("preflight_error"):
        notes.append("temp cleanup skipped; no recycling attempted")
    # Its own flag, not "no note yet": a retired-store failure note must not hide dirs the temp sweep LEFT IN PLACE.
    elif r["recycled"] or (r["failed"] and not temp_raised):
        notes.append("moved {} leftover temp dir(s) to the Recycle Bin{}".format(
            r["recycled"], "; {} could not be recycled and were LEFT IN PLACE (ask the user before any permanent "
                           "delete)".format(r["failed"]) if r["failed"] else ""))
    notes.extend(res.get("failed", [])[:3])
    rr = ret["rows"]
    if rr["recycled"] or rr["failed"]:
        notes.append("moved {} batch(es) of pruned guard-store files to the Recycle Bin{}".format(
            rr["recycled"], "; {} could not be recycled and were LEFT IN PLACE in .claude/retired (ask the user "
                            "before any permanent delete)".format(rr["failed"]) if rr["failed"] else ""))
    if rr["waiting: no Recycle Bin here"]:
        notes.append("{} batch(es) of pruned guard-store files ({:.1f} MiB) wait in .claude/retired: there is no "
                     "Recycle Bin to send them to here, so move them to your trash when you choose".format(
                         rr["waiting: no Recycle Bin here"], ret["waiting_bytes"] / 1048576))
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    codex = dir_size(home / ".codex" / "sessions") / GIB
    if codex > cfg["codex_sessions_warn_gib"]:
        notes.append("~/.codex/sessions is {:.1f} GiB and Codex does not prune it".format(codex))
    try:
        wt = worktree_count(cfg["root"])
        if wt is not None and wt > cfg["worktrees_warn"]:
            notes.append("{} git worktrees (git worktree list)".format(wt))
    except OSError as exc:
        notes.append("worktree inventory unavailable: {}".format(exc))
        r["failed"] += 1
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
    bin_note = certainty.recycle_bin_note(cfg)
    if bin_note:
        notes.append(bin_note)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(("DISK: " + "; ".join(notes) + " -- python tools/housekeep.py report\n") if notes else "",
                     encoding="utf-8")
    return 1 if r["failed"] or rr["failed"] else 0


def _daily_module():
    """tools/housekeep_daily.py, loaded from beside this file."""
    import importlib.util
    # Share THIS module only when it is registered. Loaded by path (spec_from_file_location, as the tests and the
    # session hook do), it is not in sys.modules, and `sys.modules[__name__]` raised KeyError: session() then crashed
    # whenever no other code had imported housekeep first (order-dependent; found 2026-11-01 when single-test mutation
    # twins failed their pristine baseline). housekeep_daily loads its own copy when none is registered.
    me = sys.modules.get(__name__)
    if me is not None:
        sys.modules.setdefault("housekeep", me)
    spec = importlib.util.spec_from_file_location("housekeep_daily", Path(__file__).with_name("housekeep_daily.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- report

def dir_size(p: Path) -> int:
    """Report only, so unlike walk_files it steps over a link or a file another program just removed."""
    def linked(path):
        try:
            st = os.lstat(path)
        except OSError:
            return True
        return stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400    # os.walk follows a junction
    total = 0
    for folder, dirs, files in os.walk(str(p)):
        dirs[:] = [d for d in dirs if not linked(os.path.join(folder, d))]
        for name in files:
            try:
                st = os.lstat(os.path.join(folder, name))
            except OSError:
                continue
            total += st.st_size if stat.S_ISREG(st.st_mode) else 0
    return total


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
    print_coverage(doc)
    print_rows(doc["rows"], "agent output + rolling stores", doc["actions"])
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
    refusals = ["{} ({})".format(reason, root) for root in (_abs(cfg, r) for r in cfg["staging_roots"])
                if (reason := staging_root_refusal(root, cfg))]
    if refusals:
        print("housekeep: staging cleanup REFUSED: " + "; ".join(refusals), file=sys.stderr)
    try:
        return _run(cmd, args, cfg)
    except Exception as e:                           # fail closed: a protection that cannot be evaluated refuses
        if not isinstance(e, (ProtectionUnavailable, OSError, ValueError, KeyError)) \
                and type(e).__name__ != "ArchiveError":
            raise
        print("housekeep: REFUSED, nothing planned or applied: {}".format(e), file=sys.stderr)
        return 1


def _run(cmd, args, cfg) -> int:
    if cmd == "version" and not args:
        print(cleanup_code_sha256())
        return 0
    if cmd == "backups":
        housekeep_backups = certainty
        guard = Guard(cfg)
        def protect_backup(path):
            k = _key(path)
            if k in guard.tracked or k in guard.links:
                return "tracked/linked evidence"
            if any(fnmatch.fnmatch(str(path).replace('\\', '/').lower(), g) for g in guard.globs):
                return "protected_globs"
            why = guard.why_protected(path)
            return "" if why in {"backup folder", "known-good baseline"} else why
        return housekeep_backups.main(args, cfg, recycle, protect_backup)
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
    if cmd == "recycle" and args:                     # for shell callers (kb-guard's rotation): one line per path
        refused = {p: recycle_refusal(cfg, p) for p in args}
        res = recycle([p for p in args if not refused[p]])
        for p in args:
            err = "refused: " + refused[p] if refused[p] else res[p]
            print("OK\t" + p if err is None else "FAILED\t{}\t{}".format(p, err))
        return 1 if any(refused.values()) or any(res.values()) else 0
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
        print_coverage(doc)
        print_rows(doc["rows"], "plan", doc["actions"])
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
