r"""targets: where a command's operand actually resolves, and which protection zone that is.

    resolve(word, cwd, dialect, env) -> "c:/x/y" | "//server/share/x" | "/posix/x" | None
    zone(abs_path, roots)            -> "install" | "config" | "mo2" | "scratch" | "outside"
    exec_cwd(e, cwd, env)            -> the directory an Exec runs in (payload cwd + its cd_before), or None
    resolve_operand(e, word, cwd, env)

Pure (no filesystem), stdlib only, Python 3.8. Plan 5 Task 6.

CWD (R9, branch 1). Task 0's M5a measured that the payload `cwd` equals the shell's real cwd for the Bash and
PowerShell tools, so a RELATIVE operand is resolved against it. The payload cwd is RAW: '/c/Users/x', 'C:\x' and
'C:/x' all occur, so it is normalised here (MSYS spellings included) before use. An empty or non-absolute cwd
makes every relative word unresolvable (None) -- never "outside".

NORMAL FORM. Lower-case drive letter, forward slashes, `.`/`..` folded (ntpath semantics: `..` above the root stays
at the root), duplicate separators collapsed, no trailing separator, and the trailing dots/spaces Windows drops
from a component removed. Everything else keeps its case: zone() compares case-insensitively.

UNRESOLVABLE (None) -- a rule must treat None as "could be anywhere", never as safe:
  - a variable other than the HOME/USERPROFILE family (HOME, USERPROFILE, HOMEDRIVE, HOMEPATH) and the current
    directory ($PWD, %CD%); `~user`, `~+`, `~-`;
  - a glob (`*`, `?`, `[`, bash `{a,b}`) in a DIRECTORY component (a glob in the last component is kept: the
    zone of its parent is still known);
  - "-" (stdin / `cd -`), an empty word;
  - a drive-relative `C:foo` (it depends on that drive's own cwd) and a non-filesystem PowerShell drive (`HKLM:`);
  - a relative word with no usable cwd.

DIALECTS. bash: `~` and $VAR/${VAR}; `/c/x` is drive c: (Git Bash); `/tmp/x` stays a POSIX path (outside).
PowerShell: `~`, $env:VAR / ${env:VAR}, $HOME, $PWD; `FileSystem::` provider prefixes dropped; `/x` and `\x` are
the root of the cwd's drive. cmd: %VAR%; `~` is a literal name; `\x` is the root of the cwd's drive.

KNOWN LIMITS: 8.3 short names (PROGRA~1), junctions/symlinks and subst drives are not followed (that would need the
filesystem); a word whose quoting made `$`/`~` literal is still expanded (argv does not record which PART was quoted).
"""
from __future__ import annotations

import re
from typing import Mapping, Optional

from . import paths

HOME_FAMILY = ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH")
_BASH_VAR = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_PS_VAR = re.compile(r"\$(?:\{(env:)?([A-Za-z_][A-Za-z0-9_]*)\}|(env:)?([A-Za-z_][A-Za-z0-9_]*))", re.IGNORECASE)
_CMD_VAR = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")
_PS_PROVIDER = re.compile(r"(?:Microsoft\.PowerShell\.Core\\)?FileSystem::", re.IGNORECASE)
_PS_DRIVE = re.compile(r"[A-Za-z][A-Za-z0-9_]+:")      # HKLM:, Env:, Cert: -- not the filesystem
_CONFIG_LITERAL = re.compile(r"/documents/my games/skyrim[^/]*")


class _Unresolvable(Exception):
    pass


def _env(env: Mapping[str, str], name: str) -> str:
    v = env.get(name)
    if v is None:
        up = name.upper()
        v = next((env[k] for k in env if k.upper() == up), None)   # Windows names are case-insensitive
    if not v:
        raise _Unresolvable(name)
    return v


def _home(env: Mapping[str, str], dialect: str) -> str:
    order = ("USERPROFILE", "HOME") if dialect == "powershell" else ("HOME", "USERPROFILE")
    for n in order:
        try:
            return _env(env, n)
        except _Unresolvable:
            pass
    raise _Unresolvable("~")


def _var(name: str, env: Mapping[str, str], cwd: Optional[str]) -> str:
    up = name.upper()
    if up in ("PWD", "CD"):
        if not cwd:
            raise _Unresolvable(name)
        return cwd
    if up in HOME_FAMILY:
        return _env(env, up)
    raise _Unresolvable(name)


def _expand(word: str, dialect: str, env: Mapping[str, str], cwd: Optional[str]) -> str:
    """The word with its home/cwd variables substituted; _Unresolvable for any other expansion."""
    if dialect == "cmd":
        out = _CMD_VAR.sub(lambda m: _var(m.group(1), env, cwd), word)
        if "%" in out:
            raise _Unresolvable(word)                    # %i (for loop), %~dp0, %1: a variable, not a name
        return out
    if word == "~" or word.startswith(("~/", "~\\")):
        word = _home(env, dialect) + word[1:]
    elif word.startswith("~"):
        raise _Unresolvable(word)                        # ~user, ~+, ~-
    if dialect == "powershell":
        def ps(m):
            name = m.group(2) or m.group(4)
            is_env = bool(m.group(1) or m.group(3))
            up = name.upper()
            if is_env:
                if up in HOME_FAMILY:
                    return _env(env, up)
                raise _Unresolvable(name)
            if up == "HOME":
                return _home(env, dialect)
            if up == "PWD":
                return _var("PWD", env, cwd)
            raise _Unresolvable(name)
        out = _PS_VAR.sub(ps, word)
    else:
        out = _BASH_VAR.sub(lambda m: _var(m.group(1) or m.group(2), env, cwd), word)
    if "$" in out or "`" in out:
        raise _Unresolvable(word)                        # $(...), $1, ${x:-y}, a stray backtick
    return out


def _has_glob(component: str, dialect: str) -> bool:
    if "*" in component or "?" in component:
        return True
    if dialect != "cmd" and "[" in component:
        return True
    return dialect == "bash" and "{" in component and "," in component


def _drive_root(cwd: Optional[str]) -> Optional[str]:
    if cwd and re.match(r"[a-z]:/", cwd):
        return cwd[:3]
    if cwd and cwd.startswith("//"):
        bits = cwd[2:].split("/")
        return "//" + bits[0] + "/" + bits[1] + "/" if len(bits) >= 2 else None
    return None


def normalise_cwd(cwd: str) -> Optional[str]:
    """The payload cwd (any spelling) as a normalised absolute path, or None."""
    return paths.norm_abs(cwd.strip(), msys=True) if cwd else None


def resolve(word: str, cwd: str, dialect: str, env: Mapping[str, str]) -> Optional[str]:
    if not word or word == "-":
        return None
    base = normalise_cwd(cwd)
    try:
        w = word
        if dialect == "powershell":
            w = _PS_PROVIDER.sub("", w, count=1) if "::" in w else w
        w = _expand(w, dialect, env, base)
    except _Unresolvable:
        return None
    if not w:
        return None
    s = w.replace("\\", "/")
    comps = (s[4:] if s[:4] in ("//?/", "//./") else s).split("/")     # the \\?\ prefix is not a glob
    if any(_has_glob(c, dialect) for c in comps[:-1]):
        return None
    if s.startswith("//"):
        return paths.norm_abs(s)
    if re.match(r"[A-Za-z]:", s):
        return paths.norm_abs(s)                         # C:/x -> absolute; C:foo / C: -> None
    if dialect == "powershell" and _PS_DRIVE.match(s):
        return None                                      # HKLM:\..., Env:\... -- not a file
    if s.startswith("/"):
        if dialect == "bash" and not w.startswith("\\"):
            return paths.norm_abs(s, msys=True)          # /c/x (Git Bash drive), /tmp/x (POSIX)
        root = _drive_root(base)                         # PowerShell/cmd `\x` `/x`; bash's literal `\x` reading
        return paths.norm_abs(root + s.lstrip("/")) if root else None
    if not base:
        return None
    return paths.norm_abs(paths._join(base, s))


def exec_cwd(e, cwd: str, env: Mapping[str, str]) -> Optional[str]:
    """The directory an Exec runs in: the payload cwd moved by its cd_before. None when unknowable."""
    base = normalise_cwd(cwd)
    if e.cd_before == "?":
        return None
    if not e.cd_before:
        return base
    return resolve(e.cd_before, base or "", e.dialect, env)


def resolve_operand(e, word: str, cwd: str, env: Mapping[str, str]) -> Optional[str]:
    return resolve(word, exec_cwd(e, cwd, env) or "", e.dialect, env)


def _under(path: str, root: str) -> bool:
    if path == root:
        return True
    return path.startswith(root if root.endswith("/") else root + "/")


def zone(abs_path: str, roots) -> str:
    """The protection zone of a normalised absolute path; the most specific (longest) matching root wins, ties
    going to mo2, then config, then install. Case-insensitive. `scratch` is an install path with a SCRATCH_GLOBS
    component (R8). The config zone also covers any `Documents/My Games/Skyrim*` folder (the legacy literal)."""
    p = (abs_path or "").lower().rstrip("/") or "/"
    best, best_len, best_root = "outside", -1, ""
    for kind, dirs in (("mo2", roots.mo2_dirs), ("config", roots.config_dirs), ("install", roots.install_dirs)):
        for d in dirs:
            dl = d.lower()
            if _under(p, dl.rstrip("/") or dl) and len(dl) > best_len:
                best, best_len, best_root = kind, len(dl), dl
    m = _CONFIG_LITERAL.search(p)
    if m and (p[m.end():m.end() + 1] in ("", "/")) and m.end() > best_len:
        best, best_len, best_root = "config", m.end(), p[:m.end()]
    if best == "install":
        rel = p[len(best_root):].strip("/")
        if rel and any(c in paths.SCRATCH_NAMES for c in rel.split("/")):
            return "scratch"
    return best
