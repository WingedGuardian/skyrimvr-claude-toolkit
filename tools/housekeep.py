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
    python tools/housekeep.py session STATE.txt   sweep-temp, recycle settled .claude/retired batches (what the guard
                                                  pruned from its own stores), then write the session note to STATE.txt
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

CLASSES = ("recreatable", "build", "link", "gzip", "launch", "snapshot", "scratch")
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
    "build_exts": [".obj", ".pch", ".lib", ".ilk", ".iobj", ".ipdb", ".tlog", ".idb"],
    "active_hours": 48,
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
    return cfg


def _abs(cfg, p) -> Path:
    q = Path(p)
    return q if q.is_absolute() else cfg["root"] / q


def _key(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def blake(path, chunk=1 << 20) -> str:
    d = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            d.update(b)
    return d.hexdigest()


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
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, timeout=120)
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
                out.append((p, "active" if newest_mtime(p) > cutoff else "idle"))
            else:
                out.append((p, "loose"))

    for r in cfg["agent_output"]:
        root = _abs(cfg, r)
        if root.is_dir():
            visit(root)
    return out


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


def is_build(path: Path, cfg) -> bool:
    names = {n.lower() for n in cfg["build_dir_names"]}
    parts = [x.lower() for x in path.parts[:-1]]
    return path.suffix.lower() in {e.lower() for e in cfg["build_exts"]} and any(x in names for x in parts)


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
    tasks = task_folders(cfg) if wants("recreatable", "build", "link") else []
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

    # ---- named scratch folders
    for s in (cfg["scratch"] if wants("scratch") else []):
        p = Path(s)
        if p.is_dir():
            fp = fingerprint(p)
            act({"class": "scratch", "path": str(p), "bytes": fp[1], "fingerprint": fp},
                "plan: named scratch folder -> Recycle Bin")

    return {"created": now, "root": str(cfg["root"]), "actions": actions, "rows": rows}


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
                         capture_output=True, text=True, encoding="utf-8", timeout=3600)
    silent = "no result reported" + (" (powershell: {})".format(out.stderr.strip()[:300]) if out.stderr.strip() else "")
    result = {p: silent for p in paths}
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1] in result:
            result[parts[1]] = None if parts[0] == "OK" else (parts[2] if len(parts) > 2 else "error")
    for p in paths:                     # the bin's word is not the proof: the path must actually be gone
        if result[p] is None and os.path.exists(p):
            result[p] = "still present after a reported recycle"
    return result


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
            self.newest[k] = newest_mtime(Path(task))
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
    if action["class"] in ("launch", "snapshot", "scratch"):
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


def apply(plan_doc: dict, cfg, only=None, recycler=recycle, now: float | None = None) -> int:
    now = time.time() if now is None else now
    guard = Guard(cfg)
    memo = _Memo()
    order = [c for c in CLASSES if not only or c in only]
    failed = 0
    totals = {}
    for cls in order:
        acts = [a for a in plan_doc["actions"] if a["class"] == cls]
        if not acts:
            continue
        to_recycle, done = [], 0
        for a in acts:
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
                else:
                    to_recycle.append(a)
            except OSError as e:
                print("  FAILED   {:<11} {}  ({})".format(cls, a["path"], e))
                failed += 1
        if to_recycle:
            res = recycler([a["path"] for a in to_recycle])
            for a in to_recycle:
                err = res.get(a["path"], "no result reported")
                if err:
                    print("  FAILED   {:<11} {}  (Recycle Bin: {})".format(cls, a["path"], err))
                    failed += 1
                else:
                    done += a["bytes"]
        totals[cls] = done
        print("  {:<11} {:>4} action(s), {:.2f} GiB done".format(cls, len(acts), done / GIB))
    print("applied: {:.2f} GiB; {} refused/failed".format(sum(totals.values()) / GIB, failed))
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


def worktree_count(root: Path) -> int:
    try:
        out = subprocess.run(["git", "-C", str(root), "worktree", "list", "--porcelain"], capture_output=True,
                             text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    return sum(1 for line in out.splitlines() if line.startswith("worktree ")) - 1


def session(cfg, state: Path) -> int:
    """The session-start job: sweep the toolkit's temp dirs, then write a one-line note for the next banner when
    something needs the user (free space near the floor, Codex sessions or worktrees past their threshold).
    Runs at most once per sweep_every_hours: the state file's age is the clock."""
    try:
        if time.time() - state.stat().st_mtime < cfg["sweep_every_hours"] * 3600:
            return 0
    except OSError:
        pass
    res = sweep_temp(cfg)
    ret = sweep_retired([cfg["root"]] + [p for p in [_project_of(state)] if p is not None])
    notes = []
    r = res["rows"]
    if r["recycled"] or r["failed"]:
        notes.append("moved {} leftover temp dir(s) to the Recycle Bin{}".format(
            r["recycled"], "; {} could not be recycled and were LEFT IN PLACE (ask the user before any permanent "
                           "delete)".format(r["failed"]) if r["failed"] else ""))
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
    wt = worktree_count(cfg["root"])
    if wt > cfg["worktrees_warn"]:
        notes.append("{} git worktrees (git worktree list)".format(wt))
    floor = cfg["free_floor_gib"]
    free = shutil.disk_usage(str(cfg["root"])).free / GIB
    if floor and free - floor < 20:
        notes.append("{:.0f} GiB free, floor {} GiB".format(free, floor))
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(("DISK: " + "; ".join(notes) + " -- python tools/housekeep.py report\n") if notes else "",
                     encoding="utf-8")
    return 1 if r["failed"] or rr["failed"] else 0


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
