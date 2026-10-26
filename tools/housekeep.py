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
                                                  superseded)
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

Removal goes to the Recycle Bin, never a permanent delete, with no exceptions (Windows: the shell's own delete with
FOF_ALLOWUNDO). An item too big for the bin gets Windows' own "delete permanently?" prompt; "No" leaves it in place.
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
from pathlib import Path
from urllib.parse import unquote

TOOLKIT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = TOOLKIT_ROOT / ".claude" / "housekeep.json"

# The guard's own list (tools/skyguard/writes.py SAVE_EXTS). Copied, not imported, so this tool also runs in an
# install that predates the guard package; tests/test_housekeep.py fails if the two drift.
SAVE_EXTS = (".ess", ".skse")

CLASSES = ("derived", "recreatable", "build", "link", "gzip", "launch", "snapshot", "scratch", "superseded")
DEFAULTS = {
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
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        if isinstance(e, OSError) and not _in_git_work_tree(root):
            return set()                         # no git, and no repository either: nothing here is tracked
        raise ProtectionUnavailable("git-tracked protection unavailable ({}: {})".format(type(e).__name__, e)) from e
    if out.returncode != 0:
        err = out.stderr.decode("utf-8", "replace").strip()
        if "not a git repository" in err.lower():
            return set()                         # a plain folder: nothing here is tracked
        raise ProtectionUnavailable("git-tracked protection unavailable (git ls-files exit {}: {})".format(
            out.returncode, err[:300] or "no message"))
    return {_key(root / p) for p in out.stdout.decode("utf-8", "replace").split("\0") if p}


def evidence_links(cfg) -> set:
    """Every agent-output path a project doc links to (written relative to the root, as docs link them). A linked
    file is never planned for anything."""
    links = set()
    prefixes = []
    for r in cfg["agent_output"] + cfg["staging_roots"]:
        try:
            rel = os.path.relpath(os.path.abspath(str(_abs(cfg, r))), os.path.abspath(str(cfg["root"])))
        except ValueError:                       # another drive: docs cannot link it relatively
            continue
        if rel.split(os.sep)[0] != "..":
            prefixes.append(re.escape(rel.replace("\\", "/")))
    if not prefixes:
        return links
    start_re = re.compile("(?:{})/".format("|".join(prefixes)), re.I if os.name == "nt" else 0)
    budget = 8 * 1024 * 1024
    for doc in cfg["evidence_docs"]:
        p = _abs(cfg, doc)
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        # Conservative reference discovery, not a Markdown parser (sweep 2026-10-25; X4 34133b7, c6d4760). A link may
        # hold spaces and parentheses (`_CLAUDE_OUTPUT/Mod (v2) notes/x.md`), so every cut of the rest of the line at a
        # delimiter is a candidate: stopping at the FIRST space or ')' silently lost evidence. Literal, %-escaped and
        # backslash-escaped spellings can all name a real file. Too many candidates refuses rather than truncates.
        views = {text, unquote(text)}
        views |= {re.sub(r"\\([!\"#$%&'()*+,./:;<=>?@\[\]^_`{|}~\\-])", r"\1", s) for s in tuple(views)}
        for view in views:
            for line in view.splitlines():
                for start in start_re.finditer(line):
                    tail = line[start.start():]
                    if len(tail) > 32768:
                        raise ProtectionUnavailable("an evidence reference line in {} is too long to read safely".format(
                            doc))
                    ends = [i for i, ch in enumerate(tail) if ch.isspace() or ch in ")]\"'`<>|.,;:*"]
                    for end in ends + [len(tail)]:
                        candidate = tail[:end]
                        if candidate:
                            budget -= len(candidate)
                            links.add(_key(cfg["root"] / candidate))
                    if budget < 0 or len(links) > 50000:
                        raise ProtectionUnavailable("evidence references in {} exceed the safe discovery bound".format(
                            doc))
    return links


class Guard:
    """The Forever class and the per-run exclusions, in one place: plan and apply both ask it."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.tracked = tracked_files(cfg["root"])
        for r in cfg["staging_roots"]:
            root = _abs(cfg, r)
            if root.is_dir():
                self.tracked.update(tracked_files(root))
        self.links = evidence_links(cfg)
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


def walk_files(target: Path, strict=False):
    if target.is_file():
        yield target
        return
    for dp, dirs, fn in os.walk(target, onerror=walk_error if strict else None):
        if strict:
            os.stat(dp)
            for name in dirs + fn:
                os.stat(os.path.join(dp, name))
        for f in fn:
            yield Path(dp) / f


# The 3.x snapshot-before-tool.sh store. User ruling 2026-10-20: its stamps are ROLLING (a stamp repeating the one
# before goes to the Recycle Bin), although the store sits under .claude/backups/. Only this one tool store: saves
# inside it, a backup folder inside a stamp, and every other backup folder stay Forever.
ROLLING_SNAPSHOT_STORE = "auto_snapshots"


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


def is_build(path: Path, cfg) -> bool:
    names = {n.lower() for n in cfg["build_dir_names"]}
    parts = [x.lower() for x in path.parts[:-1]]
    return path.suffix.lower() in {e.lower() for e in cfg["build_exts"]} and any(x in names for x in parts)


def plain_path(path: Path) -> bool:
    """New retention classes never follow a symlink, junction or reparse point, including ancestors."""
    return all(not stat.S_ISLNK(s.st_mode) and not getattr(s, "st_file_attributes", 0) & 0x400
               for s in (os.lstat(p) for p in (path, *path.parents)))


def walk_error(error):
    raise error


def derived_source(path: Path):
    if path.suffix.lower() != ".xml" or not (path.name.lower() == "events.xml"
                                                   or path.parent.name.lower() == "xml-decoder"):
        return None
    if not plain_path(path):
        return None
    for folder in (path.parent, path.parent.parent):
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
    roots = [cfg["root"] / "manual-installs", cfg["root"] / ".claude/backups/manual-installs"]
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
                if "backups" in Path(dp).relative_to(root).parts and not name.endswith(".files.txt"):
                    continue
                if not (name.endswith(".files.txt") or path.suffix.lower() == ".json"):
                    if "backups" in Path(dp).relative_to(root).parts:
                        continue
                    raise ValueError("unknown manual-install manifest format: " + name)
                text = path.read_text(encoding="utf-8-sig")
                if not text.strip():
                    raise ValueError("empty manifest")
                if path.suffix.lower() == ".json":
                    document = json.loads(text)
                    if not isinstance(document, dict) or set(document) != {"rollback_sources"} \
                            or not isinstance(document["rollback_sources"], list):
                        raise ValueError("unknown JSON manifest structure")
                    for value in document["rollback_sources"]:
                        if not isinstance(value, str):
                            raise ValueError("invalid rollback source")
                        remember(value)
                    continue
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


def superseded_reason(path, root, cfg, guard, now):
    """An empty result proves every staging eligibility clause again at apply time."""
    if path.parent != root or root not in [_abs(cfg, r) for r in cfg["staging_roots"]]:
        return "not a direct child of a configured staging root"
    version = stage_version(path.name)
    if version is None:
        return "folder has no supported numeric version"
    if not plain_path(path):
        return "staging path is a link"
    latest = path.stat().st_mtime
    for dp, dirs, files in os.walk(path, onerror=walk_error):
        if any(name.casefold() == ".git" for name in dirs + files):
            return "staging tree contains a Git checkout"
        for name in dirs + files:
            entry = Path(dp) / name
            if not plain_path(entry):
                return "staging tree contains a link"
            latest = max(latest, entry.stat().st_mtime)
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
    tasks = task_folders(cfg) if wants("derived", "recreatable", "build", "link") else []
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
            if wants("superseded") and any(_strictly_inside(_real(f), _real(_abs(cfg, r)))
                                           for r in cfg["staging_roots"]):
                continue                    # staging scan accounts for these bytes exactly once
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
            source = derived_source(f)
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
            why = protected_inside(guard, p, "snapshot") if sig == prev and i != len(stamps) - 1 else ""
            if why:
                count("kept: protected ({})".format(why), size)
            elif sig == prev and i != len(stamps) - 1:
                act({"class": "snapshot", "path": str(p), "bytes": size, "fingerprint": fingerprint(p)},
                    "plan: snapshot repeating the previous one -> Recycle Bin")
            else:
                count("kept: snapshot with distinct content (or the newest)", size)
                prev = sig

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
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if path.name == "_superseded":
                count("kept: staging supersession list", path.stat().st_size if path.is_file() else dir_size(path))
                continue
            size = dir_size(path) if path.is_dir() else path.stat().st_size
            try:
                why = superseded_reason(path, root, cfg, guard, now) if path.is_dir() else "not a staging folder"
            except (OSError, ValueError, IndexError) as error:
                why = "staging protection unavailable ({}: {})".format(type(error).__name__, error)
            if why:
                count("kept: " + why, size)
            else:
                act({"class": "superseded", "path": str(path), "bytes": size, "fingerprint": fingerprint(path),
                     "staging_root": str(root)}, "plan: superseded staging -> Recycle Bin")

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


def _set_readonly(path):
    os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)


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
            self.newest[k] = newest_mtime(Path(task))
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
        if not source.is_file() or not plain_path(source) or derived_source(p) is None:
            return "trace source gone or no longer a decoded trace"
    if action["class"] == "superseded":
        try:
            why = superseded_reason(p, Path(action["staging_root"]), cfg, guard, now)
        except (OSError, ValueError, IndexError) as error:
            return "staging protection unavailable ({}: {})".format(type(error).__name__, error)
        if why:
            return why
    if action["class"] == "link":
        c = Path(action["canonical"])
        if not c.is_file() or memo.hash(c) != action["hash"]:
            return "canonical copy changed or gone"
        if guard.why_protected(c) in ("save", "git-tracked"):
            return "canonical copy is protected"
        if p.stat().st_nlink != 1 or c.stat().st_nlink != 1 + memo.links.get(_key(c), 0):
            return "hard-linked elsewhere since the plan (ownership unknown)"
    if action["class"] in ("launch", "snapshot", "scratch", "superseded"):
        why = protected_inside(guard, p, action["class"])
        if why:
            return "protected ({})".format(why)
        if fingerprint(p) != action["fingerprint"]:
            return "folder changed since the plan"
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
                    ck = _key(a["canonical"])
                    link(a, 1 + memo.links.get(ck, 0))
                    memo.links[ck] = memo.links.get(ck, 0) + 1
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
    Runs at most once per sweep_every_hours: the state file's age is the clock. SKYGUARD_HOUSEKEEP_SWEEP=off: nothing."""
    if sweep_switched_off():
        return 0
    try:
        if time.time() - state.stat().st_mtime < cfg["sweep_every_hours"] * 3600:
            return 0
    except OSError:
        pass
    notes, temp_raised = [], False
    try:
        res = sweep_temp(cfg)
    except Exception as e:                    # the state file must still be written, or the throttle never engages
        res, temp_raised = {"rows": {"recycled": 0, "failed": 1}}, True
        notes.append("temp sweep failed ({}: {})".format(type(e).__name__, str(e)[:200]))
    try:
        ret = sweep_retired([cfg["root"]] + [p for p in [_project_of(state)] if p is not None])
    except Exception as e:                    # same rule: the state file is still written
        ret = {"rows": {"recycled": 0, "failed": 0, "waiting: no Recycle Bin here": 0}, "waiting_bytes": 0}
        notes.append("retired-store sweep failed ({}: {})".format(type(e).__name__, str(e)[:200]))
    r = res["rows"]
    # Its own flag, not "no note yet": a retired-store failure note must not hide dirs the temp sweep LEFT IN PLACE.
    if r["recycled"] or (r["failed"] and not temp_raised):
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
    try:
        return _run(cmd, args, cfg)
    except ProtectionUnavailable as e:
        print("housekeep: REFUSED, nothing planned or applied: {}".format(e), file=sys.stderr)
        return 1


def _run(cmd, args, cfg) -> int:
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
