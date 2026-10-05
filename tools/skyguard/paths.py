"""Which directories are "the install", "the config dir" and Mod Organizer's mods/overwrite folders.

Two representations live side by side (Plan 5 Task 6):
  - REGEX fields (Roots.game, Roots.config), ported from protect-bash.sh's path_to_ere/add_root: alternations
    matching a path in EITHER dialect (C:/ or /c/) with ONE OR MORE separators of EITHER kind,
    case-insensitively (the grep is -i). The legacy co-occurrence rules and S-DEL-FALLBACK use these.
  - NORMALISED DIRECTORY fields (Roots.install_dirs, config_dirs, mo2_dirs): "c:/x/y" spellings (lower-case
    drive, forward slashes, `..` folded) that targets.zone() compares resolved operands against.

CONFIG AS DATA. skyrim-paths.env is read as data, never sourced, with a strict grammar that is a subset of
what bash reads identically (single quotes incl. setup.sh's '\\'' spelling, double quotes without `$`/backtick,
plain words). A protection key whose value is outside that grammar, or is not an absolute path, is MALFORMED:
it is reported (Roots.malformed -> S-INERT-CONFIG), never skipped, because a skipped root is an unguarded
folder that looks guarded.

R3: guard protections take the UNION of the environment's and the file's values for every root (a stale
source can add a root, never hide one). Tool lookup keeps the Codex precedence instead (plugins_txt():
PLUGINS_TXT > SKYRIM_LOADORDER_DIR env > paths file > probe), matching tools/xelib/active-plugins.js.
"""
from __future__ import annotations

import os
import posixpath
import re
from pathlib import Path
from typing import Dict, List, Mapping, NamedTuple, Optional, Tuple, Union

SEP = r"[/\\]+"
LEAD = r"(?<![/\\])[/\\]+"
_META = set("[]*+?^$(){}|.\\")
CONFIG_LITERAL = r"Documents[/\\]+My Games[/\\]+Skyrim"
ENV_KEYS = ("SKYRIM_GAME_ROOT", "SKYRIM_CONFIG_DIR", "SKYRIM_LOADORDER_DIR", "SKYRIM_MO2_MODS",
            "SKYRIM_MO2_OVERWRITE")
# A malformed value for one of these leaves a protected folder unknown, so it is S-INERT-CONFIG, not a skip.
PROTECTION_KEYS = ("SKYRIM_GAME_ROOT", "SKYRIM_CONFIG_DIR", "SKYRIM_MO2_MODS", "SKYRIM_MO2_OVERWRITE")
PATHS_FILE = "skyrim-paths.env"

# R8: delete protection covers the install EXCEPT these, kept as data and extended only from hand-read replay
# false positives (each extension with its count). Only the `**/<name>/**` shape is supported (checked at import):
# it matches a path with a component <name> anywhere under the install root, the directory itself included.
SCRATCH_GLOBS = ("**/__pycache__/**", "**/node_modules/**", "**/.pytest_cache/**")
# Fix round 1 ruling: nothing under these top-level install folders is scratch. tools/ holds tool RUNTIME
# (tools/zedit/app_src/node_modules, tools/xelib/node_modules with koffi), and Data/ is the game itself.
SCRATCH_EXCLUDED_TOP = ("tools", "data", ".claude")   # .claude: hooks and backups are the safety net (R8)
# Task 7 fix round 1 ruling: under the install's tools/, a __pycache__ IS scratch (Python regenerates it);
# node_modules (and anything else) under tools/ stays protected.
SCRATCH_UNDER_TOOLS = ("__pycache__",)
_SCRATCH_SHAPE = re.compile(r"\*\*/([^*?\[\]/\\]+)/\*\*")


def _scratch_names(globs) -> Tuple[str, ...]:
    out = []
    for g in globs:
        m = _SCRATCH_SHAPE.fullmatch(g)
        if not m:
            raise ValueError("unsupported SCRATCH_GLOBS entry (only **/<name>/**): " + g)
        out.append(m.group(1).lower())
    return tuple(out)


SCRATCH_NAMES = _scratch_names(SCRATCH_GLOBS)


def path_to_regex(path: str) -> Optional[str]:
    p = path.replace("\\", "/")
    if re.match(r"^[a-zA-Z]:/", p):
        drive, tail = p[0], p[3:]
    elif re.match(r"^/[a-zA-Z]/", p):
        drive, tail = p[1], p[3:]
    elif p.startswith("/") and len(p) > 1:
        drive, tail = "", p[1:]
    else:
        return None
    if not tail:
        return None
    out = "".join(SEP if c == "/" else ("\\" + c if c in _META else c) for c in tail)
    # LEAD: a separator run starts a match only at its first character (an unanchored `[/\]+` is quadratic on
    # a long run, and a hook past its timeout fails open). Equivalent: a match from mid-run is one from its start.
    return "({d}:|{l}{d}){s}{o}".format(d=drive, s=SEP, l=LEAD, o=out) if drive else LEAD + out


# ---------------------------------------------------------------------------------------------------------
# Normalised absolute paths
# ---------------------------------------------------------------------------------------------------------
_DRIVE_ABS = re.compile(r"([A-Za-z]):/")
_MSYS_DRIVE = re.compile(r"/(?:proc/cygdrive/|cygdrive/|mnt/)?([A-Za-z])(?:/|$)")


def _fold(tail: str) -> str:
    """'/'-joined components with `.`/`..` folded (ntpath-style: `..` above the root stays at the root), and the
    trailing dots and spaces Windows strips from a component removed (stricter: `Data.` IS `Data` there)."""
    parts = []
    for c in tail.split("/"):
        if ":" in c:                                    # NTFS stream: x.esp:s, dir::$INDEX_ALLOCATION, d:$I30:$..
            s = c.split(":", 1)[0]
            c = s if s else c
        if c not in (".", ".."):
            s = c.rstrip(" .")
            c = s if s else c
        parts.append(c)
    return posixpath.normpath("/" + "/".join(parts).lstrip("/")).lstrip("/")


def norm_abs(path: str, msys: bool = True) -> Optional[str]:
    """An ABSOLUTE spelling -> "c:/x/y" (lower-case drive, '/', `..` folded, no trailing '/'), "//server/share/x"
    for UNC, "/x/y" for a POSIX path that names no drive. None if the path is not absolute or is drive-relative
    (`C:foo`, `C:`). msys=True reads `/c/x`, `/cygdrive/c/x` and `/mnt/c/x` as drive c: (Git Bash, the payload
    cwd, the paths file); msys=False leaves `/x` to the caller (PowerShell/cmd: root of the current drive)."""
    if not path:
        return None
    p = path.replace("\\", "/")
    if p[:4] in ("//?/", "//./"):                       # Win32 namespace prefixes: \\?\C:\x, \\?\UNC\srv\share
        p = p[4:]
        if p[:4].lower() == "unc/":
            p = "//" + p[4:]
    if _DRIVE_ABS.match(p):
        return p[0].lower() + ":/" + _fold(p[3:])
    if re.match(r"[A-Za-z]:", p):
        return None                                     # C:foo / C: -- relative to that drive's own cwd
    if p.startswith("//"):
        bits = [b for b in p[2:].split("/") if b]
        if len(bits) < 2:
            return None
        rest = _fold("/".join(bits[2:]))
        return "//" + bits[0] + "/" + bits[1] + ("/" + rest if rest else "")
    if p.startswith("/") and msys:
        m = _MSYS_DRIVE.match(p)
        if m:
            return m.group(1).lower() + ":/" + _fold(p[m.end():])
        return "/" + _fold(p[1:])
    return None


def _join(base: str, rel: str) -> str:
    """base is already normalised; rel uses '/' (callers replace '\\')."""
    if base.endswith("/"):
        return base + rel
    return base + "/" + rel


# ---------------------------------------------------------------------------------------------------------
# skyrim-paths.env, read as data
# ---------------------------------------------------------------------------------------------------------
# `export KEY=`, `declare -x KEY=`, `typeset`, `readonly`: each assigns KEY in a sourced file (fix round 1: a
# `declare -x` line used to be ignored silently). A declare flag other than x/r/g changes the value (-i evaluates it
# as arithmetic, -l/-u fold its case), so such a line is malformed for a protection key.
_DECL = r"\s*(?:(export|declare|typeset|readonly)((?:\s+-[A-Za-z]+)*)\s+)?"
_ASSIGN = re.compile(_DECL + r"([A-Za-z_][A-Za-z0-9_]*)(\s*)=(.*)")
_PROT_MENTION = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(PROTECTION_KEYS) + r")(?![A-Za-z0-9_])")
_SAFE_FLAGS = re.compile(r"[\s\-xrg]*")
# `#` is literal inside a word (`'C:/x'#c` is C:/x#c); it starts a comment only after whitespace.
_BARE = re.compile(r"[A-Za-z0-9_./:,+@%=#-]+")


def parse_value(raw: str) -> Optional[str]:
    """The value bash would assign for `KEY=<raw>`, or None if `raw` is outside the strict grammar (anything bash
    would expand, run or split: `$`/backtick outside single quotes, an unquoted space or metacharacter, a leading
    `~`, an unterminated quote). A trailing ` # comment` is allowed. Linear: one scan, no backtracking."""
    out, i, n = [], 0, len(raw)
    while i < n:
        c = raw[i]
        if c == "'":
            j = raw.find("'", i + 1)
            if j < 0:
                return None
            out.append(raw[i + 1:j])
            i = j + 1
        elif c == '"':
            i += 1
            while True:
                if i >= n:
                    return None
                c = raw[i]
                if c == '"':
                    i += 1
                    break
                if c in "$`":
                    return None
                if c == "\\" and i + 1 < n and raw[i + 1] in '"\\$`':
                    out.append(raw[i + 1])
                    i += 2
                    continue
                if c == "\\" and i + 1 < n and raw[i + 1] == "\n":
                    return None
                out.append(c)
                i += 1
        elif c == "\\":
            if i + 1 >= n:
                return None
            out.append(raw[i + 1])
            i += 2
        elif c in " \t":
            rest = raw[i:].lstrip(" \t")
            if rest == "" or rest.startswith("#"):
                break
            return None
        else:
            m = _BARE.match(raw, i)
            if not m or (i == 0 and c == "~"):
                return None
            out.append(m.group(0))
            i = m.end()
    return "".join(out)


class PathsConfig(NamedTuple):
    env: Dict[str, str]                       # the environment's value per key (ENV_KEYS only, non-empty)
    file: Dict[str, Tuple[str, ...]]          # every value the file assigns per key, in file order
    malformed: Tuple[Tuple[str, str], ...]    # (file name, key) for each protection key the file cannot give

    def values(self, key: str) -> List[str]:
        """R3: the UNION of the environment's and the file's values, environment first, duplicates dropped."""
        out: List[str] = []
        for v in ([self.env[key]] if self.env.get(key) else []) + list(self.file.get(key, ())):
            if v and v not in out:
                out.append(v)
        return out

    def first(self, key: str) -> str:
        """Tool-lookup precedence (Codex / active-plugins.js): the environment wins, then the file's FIRST value
        (active-plugins.js stops at the first assignment; setup.sh writes each key once)."""
        if self.env.get(key):
            return self.env[key]
        vals = self.file.get(key, ())
        return vals[0] if vals else ""


def read_config(path: Path, environ: Mapping[str, str]) -> PathsConfig:
    """The environment's values plus the paths file's, as data. An absent file declares nothing; a present but
    unreadable or undecodable one RAISES (OSError/ValueError): the caller fails closed (run.ENV_UNREADABLE)."""
    env = {k: environ[k] for k in ENV_KEYS if environ.get(k)}
    file: Dict[str, List[str]] = {}
    bad: List[Tuple[str, str]] = []
    path = Path(path)
    if path.is_file():
        # utf-8-sig: a BOM would otherwise glue itself to the first key and drop it silently (fix round 1). Bytes,
        # not read_text: universal newlines would split a lone CR, which bash does not.
        text = path.read_bytes().decode("utf-8-sig")
        for line in text.split("\n"):
            line = line[:-1] if line.endswith("\r") else line
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            m = _ASSIGN.fullmatch(line)
            key = m.group(3) if m else ""
            if not m:
                # Names a protection key but assigns nothing bash reads (`KEY`, `local KEY=x`, `KEY+=x`, ...).
                k = _PROT_MENTION.search(line)
                if k and (path.name, k.group(1)) not in bad:
                    bad.append((path.name, k.group(1)))
                continue
            if key not in ENV_KEYS:
                continue
            flags_ok = m.group(1) in (None, "export") or _SAFE_FLAGS.fullmatch(m.group(2) or "")
            # `KEY ='x'` is a command, not an assignment; `declare -i KEY=` evaluates the value
            value = None if (m.group(4) or not flags_ok) else parse_value(m.group(5))
            if value is not None and value != "" and norm_abs(value) is None:
                value = None                                          # a relative root protects nothing
            if value is None:
                if key in PROTECTION_KEYS and (path.name, key) not in bad:
                    bad.append((path.name, key))
                continue
            file.setdefault(key, []).append(value)
    return PathsConfig(env, {k: tuple(v) for k, v in file.items()}, tuple(bad))


def read_env(path: Path, environ: Mapping[str, str]) -> PathsConfig:
    """Kept name for callers: same as read_config."""
    return read_config(path, environ)


# ---------------------------------------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------------------------------------
class Roots(NamedTuple):
    game: str
    config: str
    install_dirs: Tuple[str, ...] = ()
    config_dirs: Tuple[str, ...] = ()
    mo2_dirs: Tuple[str, ...] = ()
    malformed: Tuple[Tuple[str, str], ...] = ()


def _add(acc: List[str], value: str) -> None:
    if not value:
        return
    rx = path_to_regex(value)
    if rx and rx not in acc:
        acc.append(rx)


def _add_dir(acc: List[str], value: str) -> None:
    d = norm_abs(value) if value else None
    if d and d not in acc:
        acc.append(d)


EnvLike = Union[PathsConfig, Mapping[str, str]]


def _values(env: EnvLike, key: str) -> List[str]:
    if isinstance(env, PathsConfig):
        return env.values(key)
    v = env.get(key, "")
    return [v] if v else []


_GITDIR = re.compile(r"gitdir:\s*(.+?)\s*$")


def main_worktree(root: str) -> str:
    """When `root` is a git LINKED worktree -- its `.git` is a file `gitdir: <main>/.git/worktrees/<name>`, which is
    what `git worktree add` and Claude Code's EnterWorktree (`.claude/worktrees/<name>`) create -- the main working
    tree's path, else "". rt-fix-b (routes): hooks run from a worktree's copy derive their root from their own
    location, i.e. the worktree, so without this the real install beside it is not protected. Reads one small file;
    any error is "" (the worktree's own root is still guarded)."""
    if not root:
        return ""
    try:
        dotgit = Path(root) / ".git"
        if not dotgit.is_file():
            return ""
        first = dotgit.read_bytes()[:4096].decode("utf-8", "replace").splitlines()[0]
    except (OSError, IndexError):
        return ""
    m = _GITDIR.match(first)
    if not m:
        return ""
    gd = m.group(1)
    gdp = os.path.normpath(gd if os.path.isabs(gd) else os.path.join(root, gd))
    worktrees = os.path.dirname(gdp)
    git_dir = os.path.dirname(worktrees)
    if os.path.basename(worktrees).lower() != "worktrees" or os.path.basename(git_dir).lower() != ".git":
        return ""
    return os.path.dirname(git_dir).replace("\\", "/")


def merge_configs(a: "PathsConfig", b: "PathsConfig") -> "PathsConfig":
    """R3 union of two paths files' values (the worktree's own and its main tree's)."""
    file: Dict[str, Tuple[str, ...]] = dict(a.file)
    for k, vals in b.file.items():
        file[k] = tuple(dict.fromkeys(tuple(file.get(k, ())) + tuple(vals)))
    return PathsConfig(a.env, file, tuple(dict.fromkeys(a.malformed + b.malformed)))


def roots(root_posix: str, root_win: str, env: EnvLike) -> Roots:
    main = main_worktree(root_win or root_posix)
    game: List[str] = []
    _add(game, root_posix)
    _add(game, root_win)
    _add(game, main)
    for v in _values(env, "SKYRIM_GAME_ROOT"):
        _add(game, v)
    config = [CONFIG_LITERAL]
    for v in _values(env, "SKYRIM_CONFIG_DIR"):
        _add(config, v)
    install: List[str] = []
    for v in [root_posix, root_win, main] + _values(env, "SKYRIM_GAME_ROOT"):
        _add_dir(install, v)
    cfg: List[str] = []
    for v in _values(env, "SKYRIM_CONFIG_DIR"):
        _add_dir(cfg, v)
    mo2: List[str] = []
    for v in _values(env, "SKYRIM_MO2_MODS") + _values(env, "SKYRIM_MO2_OVERWRITE"):
        _add_dir(mo2, v)
    bad = env.malformed if isinstance(env, PathsConfig) else ()
    return Roots("|".join(game), "|".join(config), tuple(install), tuple(cfg), tuple(mo2), bad)


# ---------------------------------------------------------------------------------------------------------
# plugins.txt lookup (tools; the Codex precedence, kept in step with tools/xelib/active-plugins.js)
# ---------------------------------------------------------------------------------------------------------
GAME_FOLDERS = ("Skyrim VR", "Skyrim Special Edition", "Skyrim")


def plugins_txt(environ: Mapping[str, str], default_paths_files=()) -> str:
    """Which plugins.txt a tool should read: PLUGINS_TXT > SKYRIM_LOADORDER_DIR (env) > the paths file
    (SKYRIM_PATHS_FILE, else the first of default_paths_files that exists) > probe LOCALAPPDATA for the first
    existing GAME_FOLDERS entry (VR when none exists). A malformed SKYRIM_LOADORDER_DIR in the file raises, as
    active-plugins.js does. tests/fixtures/paths_precedence.json holds both implementations to the same answers."""
    if environ.get("PLUGINS_TXT"):
        return environ["PLUGINS_TXT"]
    configured = environ.get("SKYRIM_LOADORDER_DIR", "")
    if not configured:
        explicit = environ.get("SKYRIM_PATHS_FILE", "")
        files = [explicit] if explicit else [str(f) for f in default_paths_files]
        found = next((f for f in files if f and os.path.isfile(f)), "")
        if explicit and not found:
            raise ValueError("Configured paths file is missing: " + explicit)
        if found:
            vals = read_config(Path(found), {}).file.get("SKYRIM_LOADORDER_DIR", ())
            if not vals and _names_key(found):
                raise ValueError("Malformed SKYRIM_LOADORDER_DIR in " + found)
            configured = vals[0] if vals else ""          # active-plugins.js reads the FIRST assignment
    if configured:
        return os.path.join(configured, "plugins.txt")
    base = environ.get("LOCALAPPDATA") or os.path.join(environ.get("USERPROFILE", ""), "AppData", "Local")
    for game in GAME_FOLDERS:
        p = os.path.join(base, game, "plugins.txt")
        if os.path.exists(p):
            return p
    return os.path.join(base, GAME_FOLDERS[0], "plugins.txt")


def _names_key(file: str) -> bool:
    text = Path(file).read_text(encoding="utf-8")
    return any(re.match(r"\s*SKYRIM_LOADORDER_DIR\s*=", ln) for ln in text.split("\n"))
