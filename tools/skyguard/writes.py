r"""Shell writes (Plan 5 Task 8, E21): what a command WRITES or COPIES ONTO, and what that file is.

    scan(command, parsed, roots, cwd, env) -> Scan(deny, advice)

The Write/Edit guard refused a plugin or archive (W-PLUGIN), but a shell could write one freely: `echo x > X.esp`,
`Set-Content X.esp`, `sed -i ... X.esp` all ran with at most a text-matched note (E21). And that note
(ADV_PLUGIN, "command references plugin/archive files") fired on every READ of a plugin -- 3.3% of all calls, pure
noise (R11). Here a write is a PROGRAM that runs (shellparse) and its target is an OPERAND, classified by the
same file classes the Write guard uses (FILE CLASSES below) and, where location matters, resolved against the
payload cwd (targets).

WRITE PRIMITIVES (the target is overwritten or appended to in place):
  - an output redirect (`>`, `>>`, `>|`, `&>`, `2>`, PowerShell `*>`, cmd `>`), except a duplication (`2>&1`) and
    the null devices (/dev/null, NUL, $null);
  - `tee` (bash) and Tee-Object (-FilePath / -LiteralPath / position 0);
  - Set-Content, Add-Content, Clear-Content, New-Item (-Path / -LiteralPath / position 0), Out-File (-FilePath /
    -LiteralPath / position 0);
  - [IO.File]::Write* / Append* / Create / OpenWrite (shellparse's io.file.write / io.file.append);
  - `sed -i` (also `-i.bak`, `--in-place`, clusters such as `-Ei`): its file operands;
  - `dd of=FILE`.
  - a copy FROM a null device (`cp /dev/null X.esp`, `Copy-Item NUL X.esp`): it truncates the destination.
COPY PRIMITIVES (the destination is replaced by another file): cp, mv, rsync, Copy-Item, Move-Item, cmd
copy / move / xcopy, robocopy (its file arguments), [IO.File]::Copy / Move / Replace. A destination is classified
as written AND where it resolves (`cp a.ini x.ini` run inside Data/SKSE/Plugins writes an SKSE config).

RULES:
  - S-WRITE-PLUGIN (deny): a write primitive whose target is a plugin or archive, ANY location (like W-PLUGIN).
  - S-COPY-PLUGIN (advise; controller ruling 2026-10-02: copying the user's own built ESP into Data/ is routine,
    so this is a note, not an ask): a copy whose destination is a plugin or archive where the game loads it --
    directly in the install's Data/ folder, or in MO2 mods/ or overwrite/. A destination that is a folder takes
    the source's name (`cp My.esp Data/`). An UNRESOLVED destination counts when its own words put the plugin
    directly in a Data folder (`"$GAME/Data/My.esp"`).
  - S-WRITE-LOADORDER / S-WRITE-INI / S-WRITE-SKSE-INI (advise): a write primitive into plugins.txt/loadorder.txt,
    a Skyrim*.ini, or Data/SKSE/Plugins/*.ini -- or a copy onto one (`cp x plugins.txt` writes the load order).
  - S-WRITE-INSTALL (advise): any other write primitive into the install's Data/ folder, the config folder, or MO2
    (not the rest of the install: priced, 18 of 23 legacy redirect notes were the user's own scripts and notes).
  - S-WRITE-ARCHIVE (advise; Plan 8 T6): the same area, written by an archive writer (Compress-Archive, 7z, zip,
    tar -c), whose file is replaced or added to -- not emptied by a redirect, as the S-WRITE-INSTALL note says.
  - S-WRITE-ROOT (advise; batch-3 ruling 2026-10-13): a write primitive, a copy or a move onto a FILE DIRECTLY IN the
    install root that the game or a loader reads (ROOT_GAME_EXT: exe, dll, asi, ini, toml, cfg, json, fx: the game's
    exe and dlls, openvr_api.dll, a loader .ini, ENB/ReShade files), or a copy into the root folder itself, which
    takes the source's name. Not Data/ or any subfolder; not scratch output or the toolkit's own files there.
    A file class (an INI, the load order, a save, a plugin) keeps its own rule.
READING a plugin or plugins.txt is silent (R11).

NOT SAFE BY DEFAULT. If the parser could not read the command (ok=False, R4) the whole text is checked per
statement segment instead: a write word followed by a plugin target denies, and the class notes apply. A redirect's
target is the ONE word right after it (`a>b ... 'x.esp'` in interpreter code is a comparison; `=>`/`->` are arrows);
tee's targets are all its non-option words; the other write words take every word after them. If an
exec's program is opaque, its own words get the same segment check, and so -- when it reads a pipe -- do the words
of the nearest upstream program that is not a shell (`printf 'echo x > X.esp' | sh`). Not every exec's words: a
hook-test harness passing a write as DATA to a function beside an opaque `bash "$H"` is not a write (priced: 2).

KNOWN LIMITS (recorded, not handled): `perl -i`, interpreter code (`python -c "open('X.esp','w')"`) and opaque
scripts that write a plugin get no message (R11: the Write-tool deny remains, and Plan 6's read-only lock is the
backstop); a copy whose unresolved destination does not name a Data folder gets no note (`"$D/My.esp"`);
`touch` and `truncate` are not write primitives; a copy or move onto an ordinary (non-plugin, non-config) file in
Data/ gets no note (S-MOVE-INSTALL was removed in Task 9 fix round 1: 107 of 107 hits in history were the user's
deliberate deploys); on a parse failure the S-WRITE-INSTALL note is not attempted;
a target held in a variable that is NOT a same-command literal (unassigned here, assigned in a branch, written
twice; assign.py's rules) stays unresolved -- one that is (`P=X.esp; echo x > "$P"`) is judged as its literal, via
the delete rules' Exec.subst (fix-unclear 4b); `install` (`install -m644 a X.esp`) and `ln -sf a X.esp` are not
copy primitives.
"""
from __future__ import annotations

import os
import posixpath
import re
from typing import List, Mapping, NamedTuple, Optional, Tuple

from . import shellparse, targets
from .deletes import _plain_operands, interpreter_code, robocopy_positionals, substituted
from .grep import _compile
from .shellparse import ps_param

# ------------------------------------------------------------------------------------------- file classes
# Shared with write_rules (the Edit/Write guard): targets.CLASS_RULE / targets.file_class.
PLUGIN_FILE, SKSE_INI = targets.PLUGIN_FILE, targets.SKSE_INI
CLASS_RULE = targets.CLASS_RULE
file_class = targets.file_class


# ------------------------------------------------------------------------------------------- primitives
NULL_TARGETS = frozenset(("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty", "nul", "nul:", "$null", "con"))
COPY_PROGRAMS = frozenset(("cp", "mv", "rsync", "copy-item", "move-item"))
CMD_COPY_PROGRAMS = frozenset(("copy", "move", "xcopy"))
_CMD_SWITCH = re.compile(r"/[A-Za-z?\-]+(:\S*)?")
_SED_INPLACE = re.compile(r"-[nrEsuzb]*i\S*|--in-place(=\S*)?")    # P8-SED: `-bi` is in-place too
_SED_VALUED = frozenset(("-e", "-f", "-l", "--expression", "--file", "--line-length"))
_CP_VALUED = frozenset(("-S", "--suffix", "-t", "--target-directory"))


def _redirect_targets(e) -> List[str]:
    out = []
    for op, target in e.redirects:
        if ">" not in op:
            continue                                     # <, <<<
        if op.endswith("&") and re.fullmatch(r"[0-9]+|-", target):
            continue                                     # 2>&1: a duplication, not a file
        if target.lower() in NULL_TARGETS:
            continue
        out.append(target)
    return out


def _sed_files(argv) -> List[str]:
    if not any(_SED_INPLACE.fullmatch(w) for w in argv):
        return []
    words, script_given, k = [], False, 0
    while k < len(argv):
        w = argv[k]
        if w in _SED_VALUED:
            script_given = script_given or w in ("-e", "-f", "--expression", "--file")
            k += 2
            continue
        if w.startswith(("--expression=", "--file=")) or (w.startswith(("-e", "-f")) and len(w) > 2):
            script_given = True
        elif not (w.startswith("-") and len(w) > 1):
            words.append(w)
        k += 1
    return words if script_given else words[1:]


# S-SED-CRLF (P8-SED): Git for Windows' sed (GNU 4.9, MSYS) opens files in TEXT mode, so an in-place edit without
# -b/--binary rewrites EVERY CRLF line ending of the file to LF -- untouched lines, and a run that matches nothing, too
# (MEASURED on scratch files). -b keeps them and is a no-op on an LF file. GNU getopt permutes, so -b counts anywhere
# before `--`. In a short cluster -i and -e/-f/-l take the rest of the word as their value: `-bi` is binary, `-ib` is
# in-place with the backup suffix "b" (MEASURED: CRLF lost, a `...b` backup written), `-es/b/x/` is a script.
_SED_ATTACHED = frozenset("iefl")


def sed_binary(argv) -> bool:
    k = 0
    while k < len(argv):
        w = argv[k]
        if w == "--":
            return False
        if w == "--binary":
            return True
        if w in _SED_VALUED:
            k += 2
            continue
        if w.startswith("-") and not w.startswith("--"):
            for c in w[1:]:
                if c == "b":
                    return True
                if c in _SED_ATTACHED:
                    break
        k += 1
    return False


def _sed_scripts(argv) -> List[str]:
    """The sed scripts given: every -e / --expression value, else the first operand (a -f file is not read)."""
    scripts, given, k = [], False, 0
    while k < len(argv):
        w = argv[k]
        if w in ("-e", "--expression") and k + 1 < len(argv):
            scripts.append(argv[k + 1])
            given = True
            k += 2
            continue
        if w in ("-f", "--file"):
            given = True
            k += 2
            continue
        if w in _SED_VALUED:
            k += 2
            continue
        if w.startswith("--expression="):
            scripts.append(w.split("=", 1)[1])
            given = True
        elif w.startswith("--file=") or (w.startswith("-f") and len(w) > 2):
            given = True
        elif w.startswith("-e") and len(w) > 2:
            scripts.append(w[2:])
            given = True
        elif not (w.startswith("-") and len(w) > 1) and not given:
            return [w]
        k += 1
    return scripts


def _sed_part(s: str, i: int, d: str) -> int:
    """Index of the next unescaped delimiter d at or after i, or -1."""
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == d:
            return i
        i += 1
    return -1


def sed_replacements(script: str) -> List[str]:
    """The replacement of every `s<d>pattern<d>replacement<d>` command in a sed script. Linear: each command is
    consumed once, and an unterminated one ends the scan."""
    out, i, n = [], 0, len(script)
    while i < n - 1:
        if script[i] == "s" and (i == 0 or not (script[i - 1].isalpha() or script[i - 1] == "\\")):
            d = script[i + 1]
            if d not in "\\\n" and not d.isalnum() and not d.isspace():
                j = _sed_part(script, i + 2, d)
                k = _sed_part(script, j + 1, d) if j >= 0 else -1
                if k < 0:
                    break
                out.append(script[j + 1:k])
                i = k + 1
                continue
        i += 1
    return out


# A backslash PATH as sed receives it: a drive path (`C:\x`) or two backslash separators between names
# (`Data\Scripts\x`). Each backslash is SINGLE: sed reads `\\` as a literal backslash (correct), and `\G`, `\U`, `\x..`
# as escapes (the mangling). Matched on a shape where each single-backslash escape is \x00 and `\\` is `/`.
_SED_WIN = re.compile(r"[A-Za-z]:\x00|(?<![A-Za-z0-9_])[A-Za-z0-9_]{2,}\x00[A-Za-z0-9_][A-Za-z0-9_ .-]*\x00[A-Za-z0-9_]")
SED_ZONES = ("install", "config", "mo2")


def sed_win_replacement(script: str) -> bool:
    for r in sed_replacements(script):
        shape, i = [], 0
        while i < len(r):
            if r[i] == "\\" and i + 1 < len(r):
                shape.append("/" if r[i + 1] == "\\" else "\x00" + r[i + 1])
                i += 2
                continue
            shape.append(r[i])
            i += 1
        if _SED_WIN.search("".join(shape)):
            return True
    return False


# ------------------------------------------------------------------------------- other byte writers (routes red team)
# Programs that put bytes into a file NAMED on their command line without being a text-write primitive: downloaders,
# decoders, archivers, truncators, in-place editors. Each writes its OWN format (an HTTP body, a zip, a tar, zeros)
# into the name it is given, so onto a plugin or archive name it is the same corruption S-WRITE-PLUGIN denies.
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s]+")
WEB_GET = frozenset(("invoke-webrequest", "iwr", "invoke-restmethod", "irm"))
_CERTUTIL_WRITES = ("-urlcache", "-decode", "-decodehex", "-encode", "-encodehex")


def _url_name(url: str) -> str:
    """The file name curl -O / wget give a download: the URL path's last component."""
    path = re.sub(r"[?#].*$", "", url.split("://", 1)[-1])
    return path.rsplit("/", 1)[-1] if "/" in path else ""


def _opt_values(argv, longs, shorts) -> List[str]:
    """Values of `--long X`, `--long=X`, `-s X` and a short cluster ending in s (`-sLo X`, `-oX`)."""
    out, k = [], 0
    while k < len(argv):
        w = argv[k]
        if w in longs or w in shorts:
            if k + 1 < len(argv):
                out.append(argv[k + 1])
            k += 2
            continue
        if any(w.startswith(lg + "=") for lg in longs):
            out.append(w.split("=", 1)[1])
        elif w.startswith("-") and not w.startswith("--") and len(w) > 2:
            for s in shorts:
                c = s[1:]
                if w.endswith(c) and k + 1 < len(argv):
                    out.append(argv[k + 1])
                    k += 1
                    break
                if w[1:2] == c:
                    out.append(w[2:])
                    break
        k += 1
    return out


def _has_flag(argv, longs, short_letter, valued_letters="") -> bool:
    """`--long`, or `short_letter` in a short cluster before any letter that takes a value (`-XPOST` is -X POST)."""
    for w in argv:
        if w in longs:
            return True
        if w.startswith("-") and not w.startswith("--"):
            for c in w[1:]:
                if c == short_letter:
                    return True
                if c in valued_letters:
                    break
    return False


_CURL_VALUED = "XHdubceAFTwxmrEKoCYyzQPtUD"


def _download_names(argv, outdir_longs, outdir_shorts) -> List[str]:
    """curl -O / wget without -O: each URL's own name, under the output directory when one is given."""
    names = [n for n in (_url_name(u) for u in argv if _URL.fullmatch(u)) if n]
    dirs = _opt_values(argv, outdir_longs, outdir_shorts)
    return [(dirs[-1].rstrip("/\\") + "/" + n) if dirs else n for n in names]


def _first_operand(argv, valued=()) -> List[str]:
    k = 0
    while k < len(argv):
        w = argv[k]
        if w in valued:
            k += 2
            continue
        if not (w.startswith("-") and len(w) > 1):
            return [w]
        k += 1
    return []


def tool_write_targets(e) -> List[str]:
    p, argv = e.program, e.argv
    if p in WEB_GET or (p in ("curl", "wget") and e.dialect == "powershell" and ps_param(e, "outfile")):
        return list(ps_param(e, "outfile"))            # PowerShell 5's curl/wget are Invoke-WebRequest
    if p == "curl":
        out = _opt_values(argv, ("--output",), ("-o",))
        if _has_flag(argv, ("--remote-name", "--remote-name-all"), "O", _CURL_VALUED):
            out += _download_names(argv, ("--output-dir",), ())
        return out
    if p == "wget":
        out = _opt_values(argv, ("--output-document",), ("-O",))
        if out:
            return [w for w in out if w != "-"]
        return _download_names(argv, ("--directory-prefix",), ("-P",))
    if p == "start-bitstransfer":
        return list(ps_param(e, "destination", 1))
    if p == "certutil" and any(w.lower() in _CERTUTIL_WRITES for w in argv):
        ops = [w for w in argv if not w.startswith("-")]
        return ops[-1:]
    if p == "bitsadmin" and any(w.lower() in ("/transfer", "/addfile") for w in argv):
        return list(argv[-1:])
    if p == "truncate":
        return _plain_operands_valued(argv, ("-s", "--size", "-r", "--reference"))
    if p == "perl" and any(re.fullmatch(r"-[a-hj-zA-Z]*i\S*", w) for w in argv):
        return _plain_operands_valued(argv, ("-e", "-E", "-M", "-I", "-m"))[0 if _perl_code_given(argv) else 1:]
    if p == "fsutil" and argv[:1] == ("file",):
        return [w for w in argv[2:] if not w.startswith("-")]
    if p == "openssl":
        return _opt_values(argv, ("-out",), ())
    if p in ("7z", "7za", "7zr", "7z.exe") and argv and argv[0].lower() in ("a", "u", "d", "rn"):
        return _first_operand(argv[1:])
    if p == "zip":
        return _first_operand(argv, ("-b", "-t", "-tt", "-n", "-x", "-i"))
    if p == "tar" and _tar_creates(argv):
        return _opt_values(argv, ("--file",), ("-f",)) or _tar_bundled_file(argv)
    if p == "compress-archive":
        return list(ps_param(e, "destinationpath", 1))
    if p in ("export-csv", "export-clixml"):
        return list(ps_param(e, "path", 0)) + list(ps_param(e, "literalpath"))
    return []


def _plain_operands_valued(argv, valued) -> List[str]:
    out, k = [], 0
    while k < len(argv):
        w = argv[k]
        if w in valued:
            k += 2
            continue
        if not (w.startswith("-") and len(w) > 1):
            out.append(w)
        k += 1
    return out


def _perl_code_given(argv) -> bool:
    return any(w in ("-e", "-E") for w in argv)


def _tar_creates(argv) -> bool:
    first = argv[0] if argv else ""
    if first and not first.startswith("-") and re.fullmatch(r"[A-Za-z]+", first):
        return bool(set(first) & set("cruA"))                # old-style bundled `tar cf x.tar ...`
    return any(w in ("--create", "--append", "--update", "--concatenate") or
               (w.startswith("-") and not w.startswith("--") and set(w[1:]) & set("cruA")) for w in argv)


def _tar_bundled_file(argv) -> List[str]:
    first = argv[0] if argv else ""
    if first and not first.startswith("-") and "f" in first and len(argv) > 1:
        return [argv[1]]
    return []


# PowerShell .NET calls that write a file named in their FIRST string argument... (parsed as text: shellparse does not
# model method calls): WebClient.DownloadFile(url, PATH), FileStream(PATH, ...), [IO.File]::Open/Create/OpenWrite.
_PS_METHOD_WRITE = re.compile(
    r"""(?:\.DownloadFile(?:Async)?\(\s*[^,()]+,\s*|FileStream\s*\(?\s*|::(?:Open|Create|OpenWrite)\(\s*)"""
    r"""['"]([^'"\n]{1,400})['"]""", re.IGNORECASE)
# Interpreter code that writes a file NAMED by a string literal: Python open(..., 'w'/'a'/'x'/'+'), Path(...).write_*,
# os.truncate; Node fs.writeFile*/appendFile*/truncate*/createWriteStream. Only a literal name is read.
_LIT = r"""\s*[rRbBuUfF]{0,2}(['"])([^'"\n]{1,400})\1"""
_INTERP_WRITE = (
    re.compile(r"\bopen\(" + _LIT + r"""\s*,\s*(?:mode\s*=\s*)?[rRbBuU]{0,2}['"][rbt]*[wax+]""", re.IGNORECASE),
    re.compile(r"\bPath\(" + _LIT + r"\s*\)\s*\.\s*(?:write_bytes|write_text|open\(\s*['\"][rbt]*[wax+])"),
    re.compile(r"\bos\.truncate\(" + _LIT),
    re.compile(r"\b(?:writeFile|appendFile|truncate|createWriteStream)(?:Sync)?\(" + _LIT),
)


def text_write_targets_ps(text: str) -> List[str]:
    return [m.group(1) for m in _PS_METHOD_WRITE.finditer(text)]


def text_write_targets(code: str) -> List[str]:
    out = []
    for rx in _INTERP_WRITE:
        out += [m.group(2) for m in rx.finditer(code)]
    return out


def write_targets(e) -> List[str]:
    """The files this exec writes in place (redirects included)."""
    out = _redirect_targets(e) + list(tool_write_targets(e))
    p = e.program
    if p == "tee":
        out += _plain_operands(e.argv)
    elif p in ("tee-object", "out-file"):
        out += list(ps_param(e, "filepath", 0)) + list(ps_param(e, "literalpath"))
    elif p in ("set-content", "add-content", "clear-content", "new-item"):
        out += list(ps_param(e, "path", 0)) + list(ps_param(e, "literalpath"))
    elif p in ("io.file.write", "io.file.append"):
        out += list(e.argv[:1])
    elif p == "sed":
        out += _sed_files(e.argv)
    elif p == "dd":
        out += [w[3:] for w in e.argv if w.startswith("of=")]
    return [w for w in out if w]


def copy_targets(e) -> List[Tuple[str, Tuple[str, ...]]]:
    """(destination word, source words) for each copy this exec makes."""
    p, argv = e.program, e.argv
    if p in ("io.file.copy", "io.file.move", "io.file.replace"):
        return [(argv[1], (argv[0],))] if len(argv) > 1 else []
    if p in ("copy-item", "move-item"):
        src = tuple(ps_param(e, "path", 0)) + tuple(ps_param(e, "literalpath"))
        dst = ps_param(e, "destination", 1)
        # B-I1 (final review): `Get-Item X | Copy-Item -Destination D` still writes D; its source arrives on the pipe.
        return [(dst[0] if dst else ".", src)] if src or e.stdin_from == "pipe" else []
    if p in CMD_COPY_PROGRAMS and e.dialect == "cmd":
        words = [w for w in argv if not _CMD_SWITCH.fullmatch(w)]
        if not words:
            return []
        return [(words[-1], tuple(words[:-1]))] if len(words) > 1 else [(".", tuple(words))]
    if p == "robocopy":
        pos = robocopy_positionals(argv)        # fix-limits 14: /XF and /XD lists are exclusions, not files
        return [(pos[1].rstrip("/\\") + "/" + f, (f,)) for f in pos[2:]] if len(pos) > 2 else []
    if p in COPY_PROGRAMS:
        target, words, k = None, [], 0
        while k < len(argv):
            w = argv[k]
            if w in ("-t", "--target-directory") and k + 1 < len(argv):
                target = argv[k + 1]
                k += 2
                continue
            if w.startswith("--target-directory="):
                target = w.split("=", 1)[1]
            elif w in _CP_VALUED:
                k += 2
                continue
            elif w == "--":
                words += list(argv[k + 1:])
                break
            elif not (w.startswith("-") and len(w) > 1):
                words.append(w)
            k += 1
        if target is not None:
            return [(target + "/", tuple(words))] if words else []
        return [(words[-1], tuple(words[:-1]))] if len(words) > 1 else []
    return []


# ------------------------------------------------------------------------------------------- where it lands
def _rel(abs_path: str, dirs) -> Optional[List[str]]:
    """The components of abs_path below the longest of `dirs` that holds it, or None."""
    p, best = abs_path.lower().rstrip("/"), None
    for d in dirs:
        dl = d.lower().rstrip("/")
        if (p == dl or p.startswith(dl + "/")) and (best is None or len(dl) > len(best)):
            best = dl
    if best is None:
        return None
    return [c for c in p[len(best):].split("/") if c]


def game_loads(abs_path: str, roots) -> bool:
    """A plugin/archive path where the game (or MO2) loads it: <install>/Data/<file>, or under MO2 mods/overwrite."""
    if targets.zone(abs_path, roots) == "mo2":
        return True
    rel = _rel(abs_path, roots.install_dirs)
    return rel is not None and len(rel) == 2 and rel[0] == "data" and targets.zone(abs_path, roots) == "install"


def in_install_area(abs_path: str, roots) -> bool:
    """The S-WRITE-INSTALL area: the install's Data/ folder, the config folder, or MO2."""
    z = targets.zone(abs_path, roots)
    if z in ("config", "mo2"):
        return True
    if z != "install":
        return False
    rel = _rel(abs_path, roots.install_dirs)
    return bool(rel) and rel[0] == "data"


# S-WRITE-ROOT: the kinds of file the game and its loaders read from the install root -- executables, dlls (the
# OpenVR runtime, ENB/ReShade proxies, the SKSE loader's), .asi, their configs and shader files. Anything else written
# there is the agent's own scratch or the toolkit's (out.txt, a trace, CLAUDE.md, KNOWLEDGEBASE.local.md, .gitignore,
# skyrim-paths.env, which S-SELF-GUARD already notes): four earlier rulings keep those silent (an inspection's
# `> out.txt`, `echo hi > notes.txt` in the root, a captured Codex payload, the guard's own config).
ROOT_GAME_EXT = (".exe", ".dll", ".asi", ".ini", ".toml", ".cfg", ".json", ".fx", ".fxh")


def root_game_file(name: str) -> bool:
    return not name.startswith(".") and name.lower().endswith(ROOT_GAME_EXT)


def in_install_root(abs_path: str, roots) -> bool:
    """S-WRITE-ROOT: a game or loader file directly in the install's root folder (not Data/, not any subfolder)."""
    if targets.zone(abs_path, roots) != "install":
        return False
    rel = _rel(abs_path, roots.install_dirs)
    return rel is not None and len(rel) == 1 and root_game_file(rel[-1])


def is_install_root(abs_path: str, roots) -> bool:
    """The install root folder itself (a copy destination that takes the source's name)."""
    return targets.zone(abs_path, roots) == "install" and _rel(abs_path, roots.install_dirs) == []


def _looks_like_dir(word: str) -> bool:
    if word.endswith(("/", "\\")):
        return True
    base = re.split(r"[/\\]+", word)[-1]
    return base in ("", ".", "..") or "." not in base


def _name(word: str) -> str:
    return re.split(r"[/\\]+", word.rstrip("/\\"))[-1] or word


# ------------------------------------------------------------------------------------------- fallback (R4, opaque)
# A redirect's target is the ONE word right after it (Task 9 item 0: `"$PY" -c "print(1 if a>b else 'x.esp')"` is a
# comparison in interpreter code, not a write onto x.esp; `=>` and `->` are arrows). A quoted target keeps its spaces. Non-overlapping finditer,
# and each alternative stops at its own delimiter, so the scan is linear.
_REDIRECT_TARGET = re.compile(r"""[0-9&*]?(?<![=\-])>[>|]?[ \t]*(?:"([^"\n]*)"|'([^'\n]*)'|([^\s"'<>|;&()]+))""")
# tee writes EVERY operand it is given (`tee a.log X.esp`), so its words after the options are all targets.
_TEE = re.compile(r"\btee\b", re.IGNORECASE)
# The other write words name their target somewhere after them (`Set-Content -Value 1 -Path X.esp`, `sed -i s/a/b/ F`).
_WRITE_WORD = (r"Set-Content|Add-Content|Out-File|Clear-Content|Tee-Object|::(Write|Append)All"
               r"|\bsed\s+-[A-Za-z]*i|\bsed\s+--in-place|\bof=")
_SEGMENT = re.compile(r"[\n;&|]")


def _words(rest: str) -> List[str]:
    return rest.replace('"', " ").replace("'", " ").replace(",", " ").replace(")", " ").split()


def _segment_hits(text: str) -> List[Tuple[str, str]]:
    """(kind, word) for each classified write target in each statement segment. Linear: one finditer for redirects,
    one search each for tee and the other write words, then one pass over the rest of the segment."""
    out = []
    ww = _compile(_WRITE_WORD, True)
    for seg in _SEGMENT.split(text):
        words = []
        if ">" in seg:
            words += [m.group(1) or m.group(2) or m.group(3) or "" for m in _REDIRECT_TARGET.finditer(seg)]
        m = _TEE.search(seg)
        if m:
            words += [w for w in _words(seg[m.end():]) if not w.startswith("-")]
        m = ww.search(seg)
        if m:
            words += _words(seg[m.end():])
        for word in words:
            kind = file_class(word)
            if kind:
                out.append((kind, word))
    return out


# ------------------------------------------------------------------------------------------- the scan
class Scan(NamedTuple):
    deny: str                               # basename of a plugin a write primitive targets ("" for none)
    advice: Tuple[Tuple[str, str], ...]     # (rule id, file phrase) in rule order, one per rule
    move_root: str = ""                     # S-MOVE-ROOT: basename of a protected folder a move takes away
    # The save policy (saves.py): (resolved path, op) for each save in the settings folder a write, copy onto, move
    # or rename CHANGES -- op "write" (written or replaced) or "move" (taken from where it is). Not a decision here:
    # saves.judge decides the call on every save it changes, together.
    save_targets: Tuple[Tuple[str, str], ...] = ()
    sed_crlf: str = ""                      # S-SED-CRLF: basename of a file sed -i rewrites without -b


# ------------------------------------------------------------------------- saves and protected folders (rt-fix-b)
SAVE_EXTS = (".ess", ".skse")
_CONFIG_SAVES = re.compile(r"/documents/my games/skyrim[^/]*/saves$")


def is_save(abs_path: Optional[str], roots) -> bool:
    """A save (or its SKSE co-save) in the settings folder, where the game reads it."""
    return bool(abs_path) and abs_path.lower().endswith(SAVE_EXTS) and targets.zone(abs_path, roots) == "config"


def is_protected_folder(abs_path: Optional[str], roots) -> bool:
    """A folder whose MOVE takes the game's files away: a protected root itself or an ancestor of one
    (targets.covers_protected), the install's Data folder, or the settings folder's Saves folder."""
    if not abs_path:
        return False
    if targets.covers_protected(abs_path, roots):
        return True
    if _rel(abs_path, roots.install_dirs) == ["data"] and targets.zone(abs_path, roots) == "install":
        return True
    return _rel(abs_path, roots.config_dirs) == ["saves"] or bool(_CONFIG_SAVES.search(abs_path.lower()))


MOVE_PROGRAMS = frozenset(("mv", "move-item"))


def move_sources(e) -> List[str]:
    """The words a move or rename takes away from where they are."""
    p, argv = e.program, e.argv
    if p in MOVE_PROGRAMS or (p == "move" and e.dialect == "cmd") or p in ("io.file.move", "io.directory.move"):
        return [w for _, srcs in copy_targets(e) for w in srcs] if p not in ("io.file.move", "io.directory.move") \
            else list(argv[:1])
    # `rename-item`: since the merge with rt-fix-a (2026-10-05) the parser also emits every Rename-Item that names a
    # -NewName as a `move-item` (shellparse._rename_as_move), judged by the branch above. A Rename-Item without one
    # cannot run (NewName is mandatory), so a branch here only duplicated it.
    if p == "rename":
        words = [w for w in argv if not _CMD_SWITCH.fullmatch(w)]
        return words[:1]
    return []


# Plan 8 T6: the programs whose tool_write_targets are archives they create or update (S-WRITE-ARCHIVE, not the
# redirect note). A redirect on the same exec (`7z a x.7z d > log.txt`) is still S-WRITE-INSTALL.
ARCHIVE_PROGRAMS = frozenset(("compress-archive", "7z", "7za", "7zr", "7z.exe", "zip", "tar"))
ADVICE_ORDER = ("S-COPY-PLUGIN", "S-WRITE-LOADORDER", "S-WRITE-INI", "S-WRITE-SKSE-INI", "S-WRITE-ARCHIVE",
                "S-WRITE-INSTALL", "S-WRITE-ROOT",
                "S-SED-WINPATH")


def _phrase(names: List[str]) -> str:
    uniq = list(dict.fromkeys(names))
    return uniq[0] + (" and %d more" % (len(uniq) - 1) if len(uniq) > 1 else "")


RULE_OF_CLASS = dict((k, r) for k, _, r in CLASS_RULE)
# An UNRESOLVED destination (`"$GAME/Data/My.esp"`) whose own words put a plugin directly in a Data folder: priced,
# 17 real deploys of this shape were silent when an unresolved destination got no note at all.
_DATA_CHILD = re.compile(r"(^|[/\\])data[/\\]+[^/\\]+$", re.IGNORECASE)


def _opaque_texts(execs) -> List[str]:
    """The words an opaque program may run (fix: per program, not per command). Its own argv and stdin body, and --
    when it reads a pipe -- the nearest upstream exec that is not itself a shell: `printf 'echo x > X.esp' | sh`.
    A harness that only PASSES a write as data to a function (`run "printf x > X.esp"`) is not upstream of it."""
    out = []
    for i, e in enumerate(execs):
        if e.program != "?":
            continue
        out.append(" ".join(e.argv) + ("\n" + e.stdin_body if e.stdin_body else ""))
        if e.stdin_from == "pipe":
            for up in reversed(execs[:i]):
                if up.program == "?" or up.program in shellparse.ALL_SHELLS:
                    continue
                out.append(" ".join(up.argv) + ("\n" + up.stdin_body if up.stdin_body else ""))
                break
    return out


def scan(command: str, parsed, roots, cwd: str = "", env: Optional[Mapping[str, str]] = None) -> Scan:
    env = os.environ if env is None else env
    plugin: List[str] = []
    moved: List[str] = []
    saves: List[Tuple[str, str]] = []
    crlf: List[str] = []
    notes = {k: [] for k in ADVICE_ORDER}

    for e in parsed.execs:
        # fix-unclear 4b: a target held in a same-command literal (`P=X.esp; echo x > "$P"`, `$p = ...; Set-Content
        # $p`) is judged as its literal -- the delete rules' own resolver (Exec.subst), argv and redirects alike.
        e = substituted(e)
        sed_files = set(_sed_files(e.argv)) if e.program == "sed" else set()
        winpath = bool(sed_files) and any(sed_win_replacement(s) for s in _sed_scripts(e.argv))
        text_mode = bool(sed_files) and not sed_binary(e.argv)
        archives = set(tool_write_targets(e)) if e.program in ARCHIVE_PROGRAMS else set()
        for w in write_targets(e):
            a = targets.resolve_operand(e, w, cwd, env)
            kind = file_class(w, a or "")
            if kind == "plugin":
                plugin.append(_name(w))
                continue
            # S-SED-WINPATH (Task 9): sed -i on an install/config/MO2 file whose replacement holds a backslash path.
            # It REPLACES the S-WRITE-INSTALL note for that file (one note per write, not two).
            if is_save(a, roots):
                saves.append((a, "write"))                          # replaces the S-WRITE-INSTALL note (saves.py)
                continue
            # S-SED-CRLF (P8-SED): a game file by name, or any file in the install / config / MO2 area.
            in_zone = targets.zone(a, roots) in SED_ZONES if a is not None else False
            if text_mode and w in sed_files and (kind or in_zone):
                crlf.append(_name(w))
            sed_note = winpath and w in sed_files and a is not None and targets.zone(a, roots) in SED_ZONES
            if sed_note:
                notes["S-SED-WINPATH"].append(_name(w))
            if kind:
                notes[RULE_OF_CLASS[kind]].append(_name(w))
            elif not sed_note and a is not None and in_install_area(a, roots):
                notes["S-WRITE-ARCHIVE" if w in archives else "S-WRITE-INSTALL"].append(_name(w))
            elif not sed_note and a is not None and in_install_root(a, roots):
                notes["S-WRITE-ROOT"].append(_name(w))
        for w in move_sources(e):
            a = targets.resolve_operand(e, w, cwd, env)
            if is_protected_folder(a, roots):
                moved.append(posixpath.basename(a.rstrip("/")) or a)
            elif is_save(a, roots):
                saves.append((a, "move"))                           # a save moved or renamed away (saves.py)
        for dst, srcs in copy_targets(e):
            # The destination as written AND where it resolves (`cp a.ini x.ini` run inside Data/SKSE/Plugins).
            a_dst = targets.resolve_operand(e, dst, cwd, env)
            # A save replaced: the destination itself, or a folder destination that takes a save's name.
            if is_save(a_dst, roots):
                saves.append((a_dst, "write"))
                continue
            if a_dst is not None and _looks_like_dir(dst):
                hit = next((s for s in srcs if s.lower().endswith(SAVE_EXTS)), None)
                if hit is not None and is_save(a_dst.rstrip("/") + "/" + _name(hit), roots):
                    saves.append((a_dst.rstrip("/") + "/" + _name(hit), "write"))
                    continue
            kind = file_class(dst, a_dst or "")
            if kind == "plugin" and any(s.lower() in NULL_TARGETS for s in srcs):
                plugin.append(_name(dst))                           # cp /dev/null X.esp truncates it: a write
                continue
            if kind and kind != "plugin":
                notes[RULE_OF_CLASS[kind]].append(_name(dst))      # cp x plugins.txt: a write of the load order
                continue
            # S-WRITE-ROOT: a file copied or moved directly into the install root, named or taking its source's name.
            # A destination that names a folder (`Data`, `bindings`) is not a game file (priced: 3 of 8 first-draft
            # hits; ROOT_GAME_EXT excludes it). A bare new name in a move made from Rename-Item lives in the SOURCE's folder, not
            # the cwd (priced: 1 of 8, a .disabled rename inside Data/SKSE/Plugins).
            root_dst = a_dst
            if e.program == "move-item" and srcs and not re.search(r"[/\\]", dst):
                a_src = targets.resolve_operand(e, srcs[0], cwd, env)
                root_dst = posixpath.join(posixpath.dirname(a_src), dst) if a_src else None
            if not kind and root_dst is not None:
                if in_install_root(root_dst, roots):
                    notes["S-WRITE-ROOT"].append(_name(dst))
                    continue
                if is_install_root(root_dst.rstrip("/"), roots):
                    names = [_name(x) for x in srcs if root_game_file(_name(x)) and file_class(x) != "plugin"]
                    notes["S-WRITE-ROOT"].extend(names)
                    if names:
                        continue
            if kind == "plugin":
                cand, name = dst, _name(dst)
            elif _looks_like_dir(dst):
                hit = next((s for s in srcs if file_class(s) == "plugin"), None)
                if hit is None:
                    continue
                cand, name = dst.rstrip("/\\") + "/" + _name(hit), _name(hit)
            else:
                continue
            a = targets.resolve_operand(e, cand, cwd, env)
            if (a is not None and game_loads(a, roots)) or (a is None and _DATA_CHILD.search(cand)):
                notes["S-COPY-PLUGIN"].append(name)

    # routes red team: a file written by name in interpreter code (`python -c "open('X.esp','wb')"`, node
    # writeFileSync) or by a .NET call in PowerShell text (WebClient.DownloadFile, FileStream, [IO.File]::Open).
    # Only literal names. fix-limits 9: each is resolved where the code runs (the exec's cwd, after any cd before it)
    # and judged exactly as a shell write to that place is: a plugin's raw write anywhere, a save, a file class by
    # where it lands (an SKSE INI), any other file in the install area. It was judged by its name alone.
    lit = [(None, w) for w in text_write_targets_ps(command)]
    for e in parsed.execs:
        code = interpreter_code(e)
        if code:
            lit += [(e, w) for w in text_write_targets(code)]
    for e, word in lit:
        base = cwd if e is None else (targets.exec_cwd(e, cwd, env) or "")
        a = targets.resolve(word, base, "cmd", env)                     # a literal: nothing in it expands
        kind = file_class(word, a or "")
        if kind == "plugin":
            plugin.append(_name(word))
        elif is_save(a, roots):
            saves.append((a, "write"))
        elif kind:
            notes[RULE_OF_CLASS[kind]].append(_name(word))
        elif a is not None and in_install_area(a, roots):
            notes["S-WRITE-INSTALL"].append(_name(word))
        elif a is not None and in_install_root(a, roots):
            notes["S-WRITE-ROOT"].append(_name(word))

    texts = [command] if not parsed.ok else _opaque_texts(parsed.execs)
    for t in texts:
        for kind, word in _segment_hits(t):
            if kind == "plugin":
                plugin.append(_name(word))
            else:
                notes[RULE_OF_CLASS[kind]].append(_name(word))
    extra = dict(move_root=_phrase(moved) if moved else "", save_targets=tuple(dict.fromkeys(saves)),
                 sed_crlf=_phrase(crlf) if crlf else "")
    if plugin:
        return Scan(_phrase(plugin), (), **extra)
    return Scan("", tuple((r, _phrase(notes[r])) for r in ADVICE_ORDER if notes[r]), **extra)


