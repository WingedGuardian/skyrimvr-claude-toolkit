r"""targets: where a command's operand actually resolves, and which protection zone that is.

    resolve(word, cwd, dialect, env) -> "c:/x/y" | "//server/share/x" | "/posix/x" | None
    zone(abs_path, roots)            -> "install" | "config" | "mo2" | "scratch" | "outside"
    covers_protected(abs_path, roots) -> True when deleting abs_path reaches a protected root
    exec_cwd(e, cwd, env)            -> the directory an Exec runs in (payload cwd + its cd_before), or None
    resolve_operand(e, word, cwd, env)
    file_class(*spellings)            -> "plugin" | "loadorder" | "ini" | "skse-ini" | "" (shared by both guards)

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
  - a drive-relative `C:foo` / `C:` whose drive is not the cwd's (it depends on that drive's own cwd), and bash's
    `C:foo` (Git Bash finds no such file); on the cwd's own drive PowerShell and cmd resolve it against the cwd, and
    bash's bare `C:` is the drive root (final review M5, _drive_relative);
  - a non-filesystem PowerShell drive (`HKLM:`);
  - a relative word with no usable cwd;
  - an 8.3 short name (any component with `~<digit>`: the install's own is C:\GOGGAM~1\THEELD~1) -- expanding one
    needs the filesystem, so the word (or a cwd holding one) is unresolved, never "outside";
  - another host's admin share (`\\other\c$`). A LOOPBACK admin share (`\\localhost\c$`, `\\127.0.0.1\C$`,
    `\\?\UNC\localhost\c$`, `\\<COMPUTERNAME>\c$`) is mapped to its drive.
NTFS stream suffixes (`x.esp:s`, `dir::$INDEX_ALLOCATION`) are cut from each component.

COVERS (fix round 1). zone() says where a path IS; a delete also destroys everything BELOW it. covers_protected()
says whether a protected root lies at or under the path, matching glob and bash-brace components against the
root's own names (`C:/Games`, `C:/`, `C:/GOG*`, `*` run from the install's parent all cover the install).

DIALECTS. bash: `~` and $VAR/${VAR}; `/c/x` is drive c: (Git Bash); `/tmp/x` stays a POSIX path (outside).
PowerShell: `~`, $env:VAR / ${env:VAR}, $HOME, $PWD; `FileSystem::` provider prefixes dropped; `/x` and `\x` are
the root of the cwd's drive. cmd: %VAR%; `~` is a literal name; `\x` is the root of the cwd's drive.

KNOWN LIMITS: junctions, symlinks, subst drives and mapped network drives are not followed (that would need the
filesystem), and PowerShell 5's Remove-Item -Recurse FOLLOWS a junction; a word whose quoting made `$`/`~` literal
is still expanded (argv does not record which PART was quoted); no option stripping -- the caller skips options up
to `--` before resolving operands.
"""
from __future__ import annotations

import re
from typing import Mapping, Optional

from . import paths
from .grep import grep

HOME_FAMILY = ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH")
_BASH_VAR = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_PS_VAR = re.compile(r"\$(?:\{(env:)?([A-Za-z_][A-Za-z0-9_]*)\}|(env:)?([A-Za-z_][A-Za-z0-9_]*))", re.IGNORECASE)
# A `$` that still expands: before a name, digit, `{`, `(` or a special parameter. `c$` (an admin share) and a
# `$` before a separator or at the end are literal in both bash and PowerShell.
_BRACE_GROUP = re.compile(r"\{[^{}]*\}")       # one bash brace group, innermost; linear (no nested repetition)
_LIVE_DOLLAR = re.compile(r"\$[A-Za-z0-9_{(?!#*@$-]")
_CMD_VAR = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")
_PS_PROVIDER = re.compile(r"(?:Microsoft\.PowerShell\.Core\\)?FileSystem::", re.IGNORECASE)
_PS_DRIVE = re.compile(r"[A-Za-z][A-Za-z0-9_]+:")      # HKLM:, Env:, Cert: -- not the filesystem
_PS_SPLAT = re.compile(r"@[A-Za-z_?$^]")                # `@name`: PowerShell splatting, a variable's value
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
    if _LIVE_DOLLAR.search(out) or "`" in out:
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
    if paths.POSIX_HOST and (not cwd or cwd.startswith("/")):
        return "/"                    # a POSIX host has one root, whatever (or whether) the cwd: `/x` is absolute there
    return None


_SHORT_NAME = re.compile(r"~[0-9]")
# Hosts that are THIS machine: an admin share `\\<host>\c$\x` there is drive c: (fix round 1, review item 2).
_LOOPBACK = re.compile(r"(?:localhost|127(?:\.[0-9]{1,3}){3}|\[?::1\]?|0:0:0:0:0:0:0:1|\.|\?)", re.IGNORECASE)
_ADMIN_SHARE = re.compile(r"([A-Za-z])\$")


_IPV4_PART = re.compile(r"0[xX][0-9a-fA-F]{1,8}|0[0-7]{0,11}|[1-9][0-9]{0,9}")


def _ipv4_loopback(host: str) -> bool:
    """RT-A: a numeric host in any spelling Windows' inet_aton accepts (`127.1`, `2130706433`, `0x7f.1`) that is in
    127.0.0.0/8. Each of the 1-4 parts is decimal, octal (leading 0) or hex; the last part fills the remaining bytes."""
    parts = host.split(".")
    if not 1 <= len(parts) <= 4 or not all(_IPV4_PART.fullmatch(x) for x in parts):
        return False
    nums = [int(x, 16) if x[:2].lower() == "0x" else int(x, 8) if len(x) > 1 and x[0] == "0" else int(x)
            for x in parts]
    if any(v > 255 for v in nums[:-1]) or nums[-1] >= 256 ** (5 - len(parts)):
        return False
    value = 0
    for v in nums[:-1]:
        value = value * 256 + v
    value = value * 256 ** (5 - len(parts)) + nums[-1]
    return value >> 24 == 127


def _final(p: Optional[str], env: Mapping[str, str]) -> Optional[str]:
    """Last checks on a normalised path: an 8.3 short name cannot be expanded without the filesystem (None, the
    stricter answer); a loopback admin share is its drive; another host's admin share is unknowable (None)."""
    if p is None:
        return None
    if _SHORT_NAME.search(p):
        return None
    if p.startswith("//"):
        bits = p[2:].split("/", 2)
        if len(bits) >= 2 and _ADMIN_SHARE.fullmatch(bits[1]):
            host = bits[0]
            me = (env.get("COMPUTERNAME") or "").lower()
            if _LOOPBACK.fullmatch(host) or _ipv4_loopback(host) or (me and host.lower() == me):
                return paths.norm_abs(bits[1][0] + ":/" + (bits[2] if len(bits) > 2 else ""))
            return None
    return p


def normalise_cwd(cwd: str) -> Optional[str]:
    """The payload cwd (any spelling) as a normalised absolute path, or None."""
    p = paths.norm_abs(cwd.strip(), msys=True) if cwd else None
    return None if p is None or _SHORT_NAME.search(p) else p


def _drive_relative(s: str, base: Optional[str], dialect: str) -> Optional[str]:
    """Final review M5 (coordinator ruling: fail closed, never resolved-outside). A drive-relative `C:name` / `C:`.
    PowerShell and cmd resolve it against that drive's own current directory, which is the process cwd when the cwd is
    on that drive (MEASURED: `Get-Item C:x` and `Set-Location C:` from C:\\Temp both give C:\\Temp); on another drive, or
    with no cwd, it is unknown (None). Git Bash reads a bare `C:` as the drive root (MEASURED: `cd C:` -> /c) and finds
    no `C:name` at all (None)."""
    drive, rest = s[0].lower() + ":", s[2:]
    if dialect == "bash":
        return None if rest else drive + "/"
    if not base or base[:2].lower() != drive:
        return None
    return paths.norm_abs(paths._join(base, rest)) if rest else base


def _resolve(word: str, cwd: str, dialect: str, env: Mapping[str, str]) -> Optional[str]:
    if not word or word == "-":
        return None
    if dialect == "powershell" and word.startswith(("(", "@(")):
        return None             # RT-A: `('C:\x')` is an expression whose VALUE is the path, never a file named `(...)`
    if dialect == "powershell" and _PS_SPLAT.match(word):
        return None             # final review I5: `Remove-Item @p` splats $p; the word is never a file named `@p`
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
    if dialect == "bash" and any("," in g and "/" in g for g in _BRACE_GROUP.findall(s)):
        return None             # fix round 2: `{C:/GOG*,x}` expands to an absolute path; read literally it is not
    comps = (s[4:] if s[:4] in ("//?/", "//./") else s).split("/")     # the \\?\ prefix is not a glob
    if any(_has_glob(c, dialect) for c in comps[:-1]):
        return None
    if s.startswith("//"):
        return paths.norm_abs(s)
    if re.match(r"[A-Za-z]:", s):
        if not re.match(r"[A-Za-z]:/", s):
            return _drive_relative(s, base, dialect)
        return paths.norm_abs(s)                         # C:/x -> absolute
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


def resolve(word: str, cwd: str, dialect: str, env: Mapping[str, str]) -> Optional[str]:
    return _final(_resolve(word, cwd, dialect, env), env)


def exec_cwd(e, cwd: str, env: Mapping[str, str]) -> Optional[str]:
    """The directory an Exec runs in: the payload cwd moved by its cd_before. None when unknowable."""
    base = normalise_cwd(cwd)
    if e.cd_before == "?":
        return None
    if not e.cd_before:
        return base
    r = resolve(e.cd_before, base or "", e.dialect, env)
    if r is not None and any(_has_glob(c, e.dialect) for c in (r[4:] if r[:4] in ("//?/", "//./") else r).split("/")):
        # Final review I3: `cd C:/x/Sky*` keeps its glob in the LAST component (resolve() does, by design), and a
        # relative word joined onto it would read as a resolved path "outside" every root. The directory is not known;
        # deletes.raw_reaches reads the literal cd (lit_base) instead.
        return None
    return r


def resolve_operand(e, word: str, cwd: str, env: Mapping[str, str]) -> Optional[str]:
    return resolve(word, exec_cwd(e, cwd, env) or "", e.dialect, env)


def _under(path: str, root: str) -> bool:
    if path == root:
        return True
    return path.startswith(root if root.endswith("/") else root + "/")


def zone(abs_path: str, roots) -> str:
    """The protection zone of a normalised absolute path; the most specific (longest) matching root wins, ties
    going to mo2, then config, then install. Case-insensitive. `scratch` is an install path with a SCRATCH_GLOBS
    component (R8), except under the install's tools/ or Data/ (paths.SCRATCH_EXCLUDED_TOP: tool runtime and game). The config zone also covers any `Documents/My Games/Skyrim*` folder (the legacy literal)."""
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
        rel = p[len(best_root):].strip("/").split("/")
        tools_cache = rel[0] == "tools" and any(c in paths.SCRATCH_UNDER_TOOLS for c in rel[1:])   # fix round 1
        if rel[0] and (tools_cache or
                       rel[0] not in paths.SCRATCH_EXCLUDED_TOP and any(c in paths.SCRATCH_NAMES for c in rel)):
            return "scratch"
    return best


# --------------------------------------------------------------------------------------------- covers_protected
# Fix round 1 (CRITICAL): zone() answers "where is this path". A delete also destroys everything BELOW its operand,
# so `rm -rf C:/Games`, `rm -rf C:/`, `rm -rf C:/GOG*` and bash `rm -rf *` run from the install's parent all
# delete the install while zone() says "outside". covers_protected() answers "does deleting this path reach a
# protected root": a root lies at or under it, or its glob components match the root's components.
_GLOB_CHARS = frozenset("*?[{")
MAX_BRACE_ALTS = 64
MAX_GLOB_COMPONENT = 1024


def _braces(comp: str) -> list:
    """`a{b,c}d` -> [abd, acd] for non-nested groups; capped (an over-cap group set becomes `*`). Linear: the next
    `}` and `{` after every position are found in one backward pass, and pieces are joined once at the end."""
    n = len(comp)
    next_close, next_open = [n] * (n + 1), [n] * (n + 1)
    for i in range(n - 1, -1, -1):
        next_close[i] = i if comp[i] == "}" else next_close[i + 1]
        next_open[i] = i if comp[i] == "{" else next_open[i + 1]
    groups = []                     # list of alternatives lists; a literal run is a one-element list
    i, start, count = 0, 0, 1
    while i < n:
        if comp[i] == "{":
            j = next_close[i + 1]
            if j < n and next_open[i + 1] > j and "," in comp[i + 1:j]:
                alts = comp[i + 1:j].split(",")
                count *= len(alts)
                if count > MAX_BRACE_ALTS:
                    return ["*"]
                groups.append([comp[start:i]])
                groups.append(alts)
                i = start = j + 1
                continue
        i += 1
    groups.append([comp[start:]])
    out = [[]]
    for g in groups:
        out = [o + [a] for o in out for a in g]
    return ["".join(o) for o in out]


def _class_end(pat: str, i: int) -> int:
    """Index after the `[...]` starting at i, or -1 when unterminated (then `[` is literal)."""
    j = i + 1
    if j < len(pat) and pat[j] in "!^":
        j += 1
    if j < len(pat) and pat[j] == "]":
        j += 1
    k = pat.find("]", j)
    return k + 1 if k >= 0 else -1


def _class_match(cls: str, ch: str) -> bool:
    neg = cls[:1] in ("!", "^")
    body = cls[1:] if neg else cls
    hit, i = False, 0
    while i < len(body):
        if i + 2 < len(body) and body[i + 1] == "-":
            hit = hit or body[i] <= ch <= body[i + 2]
            i += 3
        else:
            hit = hit or body[i] == ch
            i += 1
    return hit != neg


def _glob_match(pat: str, s: str) -> bool:
    """fnmatch for one component (`*`, `?`, `[...]`), iterative with one backtrack point: O(len(pat) * len(s)),
    never exponential. Both are already lower-cased."""
    pi = si = 0
    star_pi, star_si = -1, 0
    while si < len(s):
        if pi < len(pat):
            c = pat[pi]
            if c == "*":
                star_pi, star_si = pi, si
                pi += 1
                continue
            if c == "?":
                pi += 1
                si += 1
                continue
            if c == "[":
                e = _class_end(pat, pi)
                if e > 0:
                    if _class_match(pat[pi + 1:e - 1], s[si]):
                        pi, si = e, si + 1
                        continue
                elif s[si] == "[":
                    pi += 1
                    si += 1
                    continue
            elif c == s[si]:
                pi += 1
                si += 1
                continue
        if star_pi < 0:
            return False
        pi, star_si = star_pi + 1, star_si + 1
        si = star_si
    while pi < len(pat) and pat[pi] == "*":
        pi += 1
    return pi == len(pat)


def _comp_matches(pat: str, comp: str) -> bool:
    if pat == comp:
        return True             # fix round 2: a root NAMED `Mods [MO2]` or `M{a,b}` matches itself literally
    if not (_GLOB_CHARS & set(pat)):
        return False
    if len(pat) > MAX_GLOB_COMPONENT:
        return True             # no real name is this long (NTFS: 255); a glob this long is treated as `*` (stricter)
    return any(_glob_match(alt, comp) if (_GLOB_CHARS & set(alt)) else alt == comp for alt in _braces(pat))


def _split(p: str) -> list:
    if p.startswith("//"):
        bits = p[2:].split("/")
        return ["//" + "/".join(bits[:2])] + [b for b in bits[2:] if b]
    head, _, rest = p.partition("/")
    return [head] + [b for b in rest.split("/") if b]


def covers_protected(abs_path: Optional[str], roots) -> bool:
    """True when deleting abs_path (globs and bash braces in ANY component expanded against the root's own
    names) reaches a protected root: install, config or MO2 at or below it. Also true for a `Documents` or
    `Documents/My Games` folder, which holds the legacy `My Games/Skyrim*` config. Case-insensitive.
    None is not a path: the caller treats it as unresolved, so this returns False for it."""
    if not abs_path:
        return False
    pc = _split(abs_path.lower().rstrip("/") or "/")
    for root in tuple(roots.install_dirs) + tuple(roots.config_dirs) + tuple(roots.mo2_dirs):
        rc = _split(root.lower().rstrip("/") or root.lower())
        if len(pc) <= len(rc) and all(_comp_matches(a, b) for a, b in zip(pc, rc)):
            return True
    if len(pc) >= 2 and pc[-1] == "documents":
        return True
    return len(pc) >= 3 and pc[-1] == "my games" and pc[-2] == "documents"


# --------------------------------------------------------------------------------------------- file classes (Task 8)
# One classifier for both guards: write_rules (Edit/Write) and this module (shell) test a path with these same
# patterns, through grep (line-based, case-insensitive, trailing CRs dropped).
# RT-A: an NTFS stream suffix names the SAME file -- `a.esp::$DATA` is a.esp's own data, `a.esp:s` a stream stored in
# a.esp -- so each class allows one after its name. A colon occurs in no Windows file name otherwise.
STREAM = r"(?::[^/\\]*)?"
PLUGIN_FILE = r"\.(esp|esm|esl|bsa|ba2)[\s.]*" + STREAM + "$"
SKYRIM_INI = r"(Skyrim\.ini|SkyrimVR\.ini|SkyrimPrefs\.ini|SkyrimCustom\.ini)" + STREAM + "$"
SKSE_INI = r"(?<![/\\])[/\\]+Data[/\\]+SKSE[/\\]+Plugins[/\\]+.*\.ini" + STREAM + "$"
LOADORDER = r"(loadorder\.txt|plugins\.txt)" + STREAM + "$"

CLASS_RULE = (("plugin", PLUGIN_FILE, "S-WRITE-PLUGIN"), ("loadorder", LOADORDER, "S-WRITE-LOADORDER"),
              ("ini", SKYRIM_INI, "S-WRITE-INI"), ("skse-ini", SKSE_INI, "S-WRITE-SKSE-INI"))


def file_class(*spellings: str) -> str:
    """plugin | loadorder | ini | skse-ini | "" -- the first class any spelling of one file (the word as written,
    its resolved path) matches."""
    for kind, rx, _ in CLASS_RULE:
        if any(s and grep(rx, s) for s in spellings):
            return kind
    return ""
