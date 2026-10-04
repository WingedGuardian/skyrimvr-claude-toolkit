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
a redirect onto a variable assigned in the same command (`P=X.esp; echo x > "$P"`) is unresolved, so it gets no
deny (the parser does not track assignments); `install` (`install -m644 a X.esp`) and `ln -sf a X.esp` are not
copy primitives.
"""
from __future__ import annotations

import os
import re
from typing import List, Mapping, NamedTuple, Optional, Tuple

from . import shellparse, targets
from .deletes import _plain_operands
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
_SED_INPLACE = re.compile(r"-[nrEsuz]*i\S*|--in-place(=\S*)?")
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


def write_targets(e) -> List[str]:
    """The files this exec writes in place (redirects included)."""
    out = _redirect_targets(e)
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
        return [(dst[0] if dst else ".", src)] if src else []
    if p in CMD_COPY_PROGRAMS and e.dialect == "cmd":
        words = [w for w in argv if not _CMD_SWITCH.fullmatch(w)]
        if not words:
            return []
        return [(words[-1], tuple(words[:-1]))] if len(words) > 1 else [(".", tuple(words))]
    if p == "robocopy":
        pos = [w for w in argv if not (w.startswith("/") and re.fullmatch(r"/{1,2}[A-Za-z]+(:\S*)?", w))]
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


ADVICE_ORDER = ("S-COPY-PLUGIN", "S-WRITE-LOADORDER", "S-WRITE-INI", "S-WRITE-SKSE-INI", "S-WRITE-INSTALL",
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
    notes = {k: [] for k in ADVICE_ORDER}

    for e in parsed.execs:
        sed_files = set(_sed_files(e.argv)) if e.program == "sed" else set()
        winpath = bool(sed_files) and any(sed_win_replacement(s) for s in _sed_scripts(e.argv))
        for w in write_targets(e):
            a = targets.resolve_operand(e, w, cwd, env)
            kind = file_class(w, a or "")
            if kind == "plugin":
                plugin.append(_name(w))
                continue
            # S-SED-WINPATH (Task 9): sed -i on an install/config/MO2 file whose replacement holds a backslash path.
            # It REPLACES the S-WRITE-INSTALL note for that file (one note per write, not two).
            sed_note = winpath and w in sed_files and a is not None and targets.zone(a, roots) in SED_ZONES
            if sed_note:
                notes["S-SED-WINPATH"].append(_name(w))
            if kind:
                notes[RULE_OF_CLASS[kind]].append(_name(w))
            elif not sed_note and a is not None and in_install_area(a, roots):
                notes["S-WRITE-INSTALL"].append(_name(w))
        for dst, srcs in copy_targets(e):
            # The destination as written AND where it resolves (`cp a.ini x.ini` run inside Data/SKSE/Plugins).
            a_dst = targets.resolve_operand(e, dst, cwd, env)
            kind = file_class(dst, a_dst or "")
            if kind == "plugin" and any(s.lower() in NULL_TARGETS for s in srcs):
                plugin.append(_name(dst))                           # cp /dev/null X.esp truncates it: a write
                continue
            if kind and kind != "plugin":
                notes[RULE_OF_CLASS[kind]].append(_name(dst))      # cp x plugins.txt: a write of the load order
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

    texts = [command] if not parsed.ok else _opaque_texts(parsed.execs)
    for t in texts:
        for kind, word in _segment_hits(t):
            if kind == "plugin":
                plugin.append(_name(word))
            else:
                notes[RULE_OF_CLASS[kind]].append(_name(word))
    if plugin:
        return Scan(_phrase(plugin), ())
    return Scan("", tuple((r, _phrase(notes[r])) for r in ADVICE_ORDER if notes[r]))


