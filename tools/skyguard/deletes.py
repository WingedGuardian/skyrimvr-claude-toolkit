r"""Delete rules on the parser (Plan 5 Task 7): what a command DELETES, and where that resolves.

    check(command, parsed, roots, cwd, env, has) -> Hit | None      (the first deny, by clause priority)
    relative_unknown(...)                                            (the S-RELATIVE-UNKNOWN advice)

The legacy rule asked "does the TEXT contain a delete word and a protected path" (E16): `grep -rn "rm -rf"
<install>` was denied, and `rm -rf Data` run IN the install was not. Here a delete is a PROGRAM that runs
(shellparse) and its target is an OPERAND resolved against the payload cwd (targets, R9 branch 1: M5a measured
that cwd tracks the shell's real working directory).

DELETE PRIMITIVES. rm, rmdir, unlink, del/erase/rd (cmd; PowerShell maps them to remove-item), remove-item,
find -delete (find.delete), [IO.File]::Delete / [IO.Directory]::Delete, `git clean` (its target is the cwd, -C /
--work-tree, or its pathspecs; `-n`/`--dry-run` deletes nothing), `git rm` (not --cached: it deletes the working
files), rimraf (also via npx/pnpx/bunx/pnpm dlx), robocopy
/MIR or /PURGE (deletes in the DESTINATION) and /MOV or /MOVE (deletes the SOURCE), and interpreter code
(`python -c`, `node -e`, `perl -e`, a heredoc or pipe fed to an interpreter; R7(b)) checked with the legacy text
regex plus a short list of interpreter delete APIs.

CLAUSES, in priority order (a deny from an earlier clause wins):
  (a) an operand resolves into the install / config / MO2 (zone, or covers_protected for an ancestor or glob that
      reaches one), and is not scratch (R8) -> S-DEL-INSTALL / S-DEL-CONFIG / S-DEL-MO2. Interpreter code that
      matches the delete regex AND names a protected root counts here too.
  (b) an operand is UNRESOLVED (a variable, a glob in a directory component, an 8.3 name, pipeline input to xargs
      rm / Remove-Item, a relative word with no usable cwd) AND either the command's text names a protected root
      or the raw word itself glob/short-name-matches one -> S-DEL-UNRESOLVED. The text is the whole command, not
      the simple command: `G=<install>; rm -rf "$G/Data"` names the root in a different statement.
  (c) the parser could not be sure what runs -> S-DEL-FALLBACK:
      (c1) ok=False (R4) and the legacy DESTROYER + root test over the whole command;
      (c2) an opaque program ("?", `git -c alias.x=!...`) is present and ONE exec's own words (argv + stdin body)
           hold both a delete word (a looser regex: after a quote or `$` too) and a protected root --
           `printf 'rm -rf "<install>"' | sh`, `$SUDO rm -rf <install>`. Per exec, not the whole command: priced,
           a whole-command test on opaque commands only re-denied `"$SPRIGGIT" serialize ...; rm -rf /tmp/x` with
           the install named elsewhere (13 of 13 hand-read: false positives).

SAME-COMMAND ASSIGNMENTS (Task 7b). Before any clause, each operand written with a variable that the parser proved
holds a literal (Exec.subst: `D=/tmp/x; rm -rf "$D"`, `$p = 'C:\x'; Remove-Item $p`) is replaced by that literal and
then judged exactly like a written-out path: into a protected root -> S-DEL-*; outside or scratch -> nothing. Anything
less than certain (assign.py and shellparse list the cases) stays UNRESOLVED and reaches clause (b) as before. Priced
(dc8c59b, N = 64,742, after fix round 1's broad poisoning): of 190 S-DEL-UNRESOLVED denies, 68 no longer deny --
all hand-read, every operand a scratch or temp path -- and 22 now name the protected file they delete (21 S-DEL-INSTALL, 1 S-WRITE-PLUGIN); 0 true positives
lost.

ADVICE (no deny): S-RELATIVE-UNKNOWN -- a relative `Data` operand with no cwd (R9).

KNOWN LIMITS (fix round 1, recorded, not handled):
  - interpreter code with RELATIVE paths (`python -c "shutil.rmtree('Data')"`, node/perl equivalents) is invisible
    unless the code also names a protected root -- the code is matched as text, not resolved;
  - `mv` OUT of the install with a relative source path gets no advice (moves are Task 9's rules);
  - shred, `rsync --delete`, `git checkout -- .`, `git reset --hard`, `git stash -u` are not delete primitives;
  - a per-install user folder such as `_CLAUDE_OUTPUT` is protected like the rest of the install; a configurable
    allowlist (beside SCRATCH_GLOBS) is a follow-up idea, not built;
  - an unresolved delete operand run from inside a protected folder is not flagged (ruling, fix round 1:
    measured 75 occurrences, 0 real, in 61,935 items);
  - `CMD="rm -rf <install>"; $CMD`: an assignment value is not an exec; PS5 Remove-Item -Recurse follows junctions;
  - (Task 7b) a variable built from another variable (`SP=...; Y="$SP/x"; rm -rf "$Y"`) stays unresolved -- only
    a plain literal counts; so does any name the text writes twice, even harmlessly (`echo "D=$D"`). ~80 such
    S-DEL-UNRESOLVED denies remain in the history.

NOT A DELETE: a command that only MENTIONS one (grep, rg, Select-String, echo, git log --grep, a data heredoc).
"""
from __future__ import annotations

import os
import posixpath
import re
from typing import List, Mapping, NamedTuple, Optional, Tuple

from . import paths, targets
from .grep import _compile, grep

# ------------------------------------------------------------------------------------------- legacy text tests
DESTROYER = (
    r"(^|[;&|(`]|\s)(rm|rmdir|del|erase)\s|Remove-Item|shutil\.rmtree|rmtree\s*\(|\s-delete(\s|$)"
    r"|Remove-ItemProperty"
)
# For an OPAQUE program (`printf 'rm -rf X' | sh`, `$RM -rf X`): the delete word may follow a quote or `$`.
DESTROYER_LOOSE = (
    r"(^|[^A-Za-z0-9_.-])(rm|rmdir|del|erase|rd|unlink|ri|rimraf)\s|Remove-Item|rmtree|\s-delete(\s|$)"
)
# Interpreter code (R7(b)): the legacy regex plus the delete APIs that code actually calls.
INTERP_DELETE = (
    r"\bos\.(remove|unlink|rmdir|removedirs)\s*\(|\.(rm|rmdir|unlink)(Sync)?\s*\(|\bremove_tree\s*\("
    r"|\bunlink\s*[('\"$]"
    # G1 (RT-A): .NET / Ruby / Deno removal calls.
    r"|\bFileUtils\.(rm|remove)\w*|\bDeno\.remove\w*\s*\(|\b(File|Directory)\]?::Delete\s*\(|\.Delete\s*\("
)
# G1 (RT-A): code that LAUNCHES a process is judged like a delete only when the launched text also carries a delete
# word, quoted or space-bounded (`os.system('rd /s /q X')`, `subprocess.run(['cmd','/c','rd',X])`). Priced: launch +
# root alone denied 6 of 68,475 history items, all read-only analysis scripts (`automod archive list`, cosave reads).
INTERP_LAUNCH = (
    r"\bos\.(system|popen|exec\w*|spawn\w*)\s*\(|\bsubprocess\.|\bchild_process\b|\b(exec|spawn)(File)?Sync\s*\("
    r"|\b(system|shell_exec|passthru|proc_open)\s*\("
)
LAUNCH_DELETE_WORD = r"""(^|['"\s,\[(])(rm|rmdir|rd|del|erase|unlink|Remove-Item|ri|rimraf)(['"\s,\])]|$)"""

DELETE_PROGRAMS = frozenset(("rm", "rmdir", "unlink", "del", "erase", "rd", "remove-item", "rimraf"))
CMD_SWITCH_PROGRAMS = frozenset(("del", "erase", "rd", "rmdir"))
_CMD_SWITCH = re.compile(r"/[A-Za-z?](:\S*)?")
NPX = frozenset(("npx", "pnpx", "bunx"))
PKG_RUNNERS = frozenset(("pnpm", "yarn", "bun", "npm"))
_PYTHON = re.compile(r"(?:python|pypy|py)[0-9.]{0,8}w?")
CODE_INTERPRETERS = frozenset(("node", "nodejs", "deno", "bun", "perl", "ruby", "php"))
CODE_FLAGS = frozenset(("-c", "-e", "-E", "--eval", "-p", "--print", "-r", "-pe"))
_PY_CODE_CLUSTER = re.compile(r"-[A-Za-z]{1,4}c")      # python -Bc CODE, -Ic CODE, -IBc CODE (G1)

# G2 (RT-A): programs that run a command line they are HANDED. What they run is text the parser does not follow, so
# they are treated as opaque: with a delete word AND a protected root in their own words (_loose_hit) they deny via
# S-DEL-FALLBACK; a wrapper that merely names a root without a delete word is not refused.
_WRAPPER_PROGRAMS = frozenset(("schtasks", "wmic", "docker", "podman", "at", "register-scheduledtask",
                               "register-scheduledjob", "start-job", "invoke-command", "start-process", "psexec",
                               "runas"))

# Remove-Item parameters that take a value which is NOT a path (prefix-abbreviated); everything else that is not
# -Path/-LiteralPath is treated as a switch, so the next word stays an operand (stricter).
_PS_PATH = ("path", "literalpath", "pspath", "lp")
_PS_VALUED = ("filter", "include", "exclude", "credential", "stream", "erroraction", "warningaction",
              "informationaction", "errorvariable", "warningvariable", "informationvariable", "outvariable",
              "outbuffer", "pipelinevariable", "ea", "wa", "ev", "wv", "ov", "ob", "pv", "iv", "infa")

UNRESOLVED = None           # an operand the guard cannot place (pipeline input, a variable, ...)
_NOCWD = "c:/__skyguard_no_cwd__"


class Hit(NamedTuple):
    rule: str               # S-DEL-INSTALL | S-DEL-CONFIG | S-DEL-MO2 | S-DEL-UNRESOLVED | S-DEL-FALLBACK
    file: str               # the basename to name in the message ("" for the unresolved/fallback rules)


# ------------------------------------------------------------------------------------------- operands
def _plain_operands(argv) -> List[str]:
    """Words after the options: `-x` words are options until `--` (rm -rf -- -x)."""
    out, opts = [], True
    for w in argv:
        if opts and w == "--":
            opts = False
            continue
        if opts and w.startswith("-") and len(w) > 1:
            continue
        out.append(w)
    return out


def _ps_operands(argv, quoted) -> List[str]:
    out, k = [], 0
    while k < len(argv):
        w, q = argv[k], quoted[k]
        if w.startswith("-") and len(w) > 1 and not q:
            key, _, attached = w[1:].partition(":")
            key = key.lower()
            if key and any(p.startswith(key) for p in _PS_PATH):
                if attached:
                    out.append(attached)
                elif k + 1 < len(argv):
                    out.append(argv[k + 1])
                    k += 1
            elif key and not attached and any(p.startswith(key) for p in _PS_VALUED) and k + 1 < len(argv):
                k += 1                                    # its value is a pattern / option, not a path
            k += 1
            continue
        out.append(w)
        k += 1
    return out


def _is_abs(w: str) -> bool:
    return bool(paths.norm_abs(w.replace(chr(92), "/"))) or w.startswith(("/", chr(92)))


def _git_targets(argv) -> Optional[List[str]]:
    """None when this git command deletes nothing; else the target words. `git clean`: the base dir (cwd, -C,
    --work-tree) or base/pathspec. `git rm` without --cached: its pathspecs (it deletes the working files)."""
    k, base = 0, []
    while k < len(argv):
        w = argv[k]
        if w in ("-C", "--work-tree") and k + 1 < len(argv):
            base.append(argv[k + 1])
            k += 2
            continue
        if w.startswith("--work-tree="):
            base.append(w.split("=", 1)[1])
        elif w in ("-c", "--git-dir", "--namespace", "--exec-path", "--config-env") and k + 1 < len(argv):
            k += 1
        elif not w.startswith("-"):
            break
        k += 1
    if k >= len(argv) or argv[k] not in ("clean", "rm"):
        return None
    sub = argv[k]
    specs, k, opts = [], k + 1, True
    while k < len(argv):
        w = argv[k]
        if opts and w == "--":
            opts = False
        elif opts and w in ("--dry-run", "--cached"):
            return None                                   # a dry run; `git rm --cached` touches only the index
        elif opts and sub == "clean" and (w == "--exclude" or w == "-e"):
            k += 1
        elif opts and w.startswith("--pathspec-from-file"):
            specs.append(UNRESOLVED)                      # the paths are in a file the guard cannot read
        elif opts and w.startswith("--"):
            pass
        elif opts and w.startswith("-") and len(w) > 1:
            cluster = w[1:]
            e = cluster.find("e") if sub == "clean" else -1
            if "n" in (cluster if e < 0 else cluster[:e]):
                return None
            if e >= 0 and e == len(cluster) - 1:
                k += 1                                    # -fe PATTERN
        else:
            specs.append(w)
        k += 1
    if sub == "rm" and not specs:
        return None
    root = "."
    for b in base:
        root = b if _is_abs(b) else root + "/" + b
    return [s if s is UNRESOLVED or _is_abs(s) else root + "/" + s for s in specs] if specs else [root]


def _robocopy_targets(argv) -> List[str]:
    pos = [w for w in argv if not (w.startswith("/") and re.fullmatch(r"/{1,2}[A-Za-z]+(:\S*)?", w))]
    flags = {w.lstrip("/").split(":")[0].upper() for w in argv if w not in pos}
    out = []
    if flags & {"MIR", "PURGE"} and len(pos) > 1:
        out.append(pos[1])
    if flags & {"MOV", "MOVE"} and pos:
        out.append(pos[0])
    return out


def _rimraf_after_runner(e) -> Optional[List[str]]:
    words = [w for w in e.argv if not w.startswith("-")]
    if e.program in PKG_RUNNERS:
        if not words or words[0] not in ("dlx", "exec", "x"):
            return None
        words = words[1:]
    if words and re.fullmatch(r"rimraf(@\S*)?", words[0].lower()):
        return _plain_operands(e.argv[e.argv.index(words[0]) + 1:])
    return None


def operands(e) -> Optional[List[Optional[str]]]:
    """None: e deletes nothing. Otherwise its target words; UNRESOLVED stands for input it reads from a pipe."""
    p = e.program
    if p == "find.delete":
        return list(e.argv)
    if p in ("io.file.delete", "io.directory.delete"):
        return list(e.argv[:1]) or [UNRESOLVED]
    if p == "git":
        return _git_targets(e.argv)
    if p == "robocopy":
        return _robocopy_targets(e.argv) or None
    if p in NPX or p in PKG_RUNNERS:
        return _rimraf_after_runner(e)
    if p not in DELETE_PROGRAMS:
        return None
    if p == "remove-item":
        words = _ps_operands(e.argv, e.quoted)
    else:
        words = _plain_operands(e.argv)
        if p in CMD_SWITCH_PROGRAMS:
            words = [w for w in words if not _CMD_SWITCH.fullmatch(w)]
    if not words and e.stdin_from == "pipe":
        return [UNRESOLVED]                              # xargs rm, ... | Remove-Item: the targets arrive on stdin
    return words


def interpreter_code(e) -> str:
    """The code an interpreter exec runs from its arguments or stdin (R7(b)); "" for a script file."""
    p = e.program
    if not (_PYTHON.fullmatch(p) or p in CODE_INTERPRETERS):
        return ""
    code = []
    for i, w in enumerate(e.argv):
        nxt = e.argv[i + 1] if i + 1 < len(e.argv) else ""
        if w in CODE_FLAGS or (re.fullmatch(r"-[A-Za-z]{1,4}[eE]", w) and p not in CODE_INTERPRETERS - {"perl",
                                                                                                         "ruby"}):
            code.append(nxt)                              # -c CODE, -e CODE, perl/ruby clusters: -pe / -ne CODE
        elif w.startswith("-c") and len(w) > 2 and _PYTHON.fullmatch(p):
            code.append(w[2:])                            # python -cCODE
        elif _PYTHON.fullmatch(p) and _PY_CODE_CLUSTER.fullmatch(w):
            code.append(nxt)                              # python -Bc CODE (G1)
    if e.stdin_body and (e.stdin_from.startswith("heredoc:interpreter") or e.stdin_from == "pipe"):
        code.append(e.stdin_body)
    return "\n".join(code)


def substituted(e):
    """e with each argv word that uses a same-command LITERAL assignment replaced by its value (Task 7b; the parser
    decides which assignment certainly reaches the word, see assign.py). Other words are unchanged."""
    if not e.subst:
        return e
    sub = dict(e.subst)
    return e._replace(argv=tuple(sub.get(w, w) for w in e.argv))


def is_opaque(e) -> bool:
    """The parser cannot know what this runs: a dynamic program, a git alias that runs a shell (`!...`), or a
    program that runs a command line it is handed (G2)."""
    if e.program == "?" or os.path.splitext(e.program)[0] in _WRAPPER_PROGRAMS:
        return True
    return e.program == "git" and any(re.match(r"alias\.[^=]*=\s*!", w) for w in e.argv)


# ------------------------------------------------------------------------------------------- classification
def _root_regexes(roots) -> List[Tuple[str, str]]:
    """(kind, regex) in the legacy priority order: config first, then MO2, then the install."""
    out = [("config", roots.config)] if roots.config else []
    mo2 = [r for r in (paths.path_to_regex(d) for d in roots.mo2_dirs) if r]
    if mo2:
        out.append(("mo2", "|".join(mo2)))
    if roots.game:
        out.append(("install", roots.game))
    return out


def names_root(text: str, roots, has=grep) -> bool:
    return any(has(rx, text) for _, rx in _root_regexes(roots))


def _match_basename(rx: str, text: str) -> str:
    c = _compile(rx, True)
    for line in text.split("\n"):
        m = c.search(line.rstrip("\r"))
        if m:
            return re.split(r"[/\\]+", m.group(0).rstrip("/\\"))[-1]
    return ""


RULE_OF = {"install": "S-DEL-INSTALL", "config": "S-DEL-CONFIG", "mo2": "S-DEL-MO2"}


def _covers_kind(abs_path: str, roots) -> str:
    empty = roots._replace(install_dirs=(), config_dirs=(), mo2_dirs=())
    if targets.covers_protected(abs_path, empty):
        return "config"                                   # a Documents / My Games folder (the legacy literal)
    for kind, field in (("mo2", "mo2_dirs"), ("config", "config_dirs"), ("install", "install_dirs")):
        if targets.covers_protected(abs_path, empty._replace(**{field: getattr(roots, field)})):
            return kind
    return ""


def protected_kind(abs_path: str, roots) -> str:
    """install | config | mo2 when deleting abs_path destroys protected files; "" otherwise (scratch: R8)."""
    z = targets.zone(abs_path, roots)
    if z in RULE_OF:
        return z
    if z == "scratch":
        return ""
    return _covers_kind(abs_path, roots)


_SHORT = re.compile(r"([^~/]{1,6})~[0-9]+(\.[^/]*)?")


def _comp_reaches(pat: str, comp: str) -> bool:
    if targets._comp_matches(pat, comp):
        return True
    m = _SHORT.fullmatch(pat)                             # an 8.3 short name: GOGGAM~1 for "GOG Games"
    return bool(m) and re.sub(r"[\s.]", "", comp).startswith(m.group(1))


def literal_abs(word: str, base: Optional[str], dialect: str) -> Optional[str]:
    """The word as a LITERAL absolute path: no variable expansion, glob characters kept as characters. None for a
    word with an expansion (`$`, `%`, backtick, a leading `~`) or a relative word with no base."""
    if targets._LIVE_DOLLAR.search(word) or "`" in word or word.startswith("~") or (dialect == "cmd" and "%" in word):
        return None
    s = word.replace("\\", "/")
    p = paths.norm_abs(s, msys=(dialect == "bash"))
    if p is None and base and not re.match(r"[A-Za-z]:|/", s):
        p = paths.norm_abs(base + "/" + s)
    return p


# RT-A: the protection keys of skyrim-paths.env, read as variables (`$SKYRIM_GAME_ROOT`, `$env:SKYRIM_CONFIG_DIR`,
# `%SKYRIM_MO2_MODS%`): their value IS a protected root, so an operand built on one reaches it.
_ROOT_VAR = re.compile(r"(?:\$\{?(?:env:)?|%)(?:" + "|".join(paths.PROTECTION_KEYS) + r")(?![A-Za-z0-9_])",
                       re.IGNORECASE)
# A variable spelled where a DRIVE goes (`$env:SystemDrive\GOG Games\...`, `%SYSTEMDRIVE%\...`, `${D}/...`).
_LEAD_VAR = re.compile(r"(?:\$\{?(?:env:)?[A-Za-z_][A-Za-z0-9_]{0,255}\}?|%[A-Za-z_][A-Za-z0-9_]{0,255}%)(?=[/\\])",
                       re.IGNORECASE)
_VOLUME = re.compile(r"//[?.]/volume\{[^{}/]{1,64}\}(?=/|$)", re.IGNORECASE)
_ANY_DRIVE = "*:"


def _reach_comps(word: str, base: Optional[str], dialect: str) -> Optional[List[str]]:
    """The word's components as raw_reaches compares them, lower-case. RT-A: a volume GUID (`\\\\?\\Volume{..}\\x`) and a
    leading variable (`$env:SystemDrive\\x`) stand for ANY drive; an admin share on ANY host (`\\\\h\\c$\\x`) for its
    drive -- whether that host is this machine cannot be proven, so it is not ruled out."""
    s = word.replace("\\", "/")
    m = _VOLUME.match(s)
    if m:
        return [_ANY_DRIVE] + [c for c in s[m.end():].lower().split("/") if c]
    m = _LEAD_VAR.match(word)
    if m:
        return [_ANY_DRIVE] + [c for c in s[m.end():].lower().split("/") if c]
    p = literal_abs(word, base, dialect)
    if p is None:
        return None
    pc = targets._split(p.lower())
    if pc and pc[0].startswith("//"):
        share = pc[0][2:].split("/")
        if len(share) == 2 and targets._ADMIN_SHARE.fullmatch(share[1]):
            pc = [share[1][0] + ":"] + pc[1:]
    return pc


def raw_reaches(word: str, base: Optional[str], dialect: str, roots) -> bool:
    """An UNRESOLVED word read literally (globs in any component, 8.3 names) reaches a protected root: it names a
    root, an ancestor of one, or a path inside one (Task 6 carry: `C:/*/The Elder*/Data` never names the root)."""
    if _ROOT_VAR.search(word):
        return True
    pc = _reach_comps(word, base, dialect)
    if not pc:
        return False
    for root in tuple(roots.install_dirs) + tuple(roots.config_dirs) + tuple(roots.mo2_dirs):
        rc = targets._split(root.lower())
        n = min(len(pc), len(rc))
        if n > (pc[0] == _ANY_DRIVE) and all(
                (a == _ANY_DRIVE and b.endswith(":")) or _comp_reaches(a, b) for a, b in zip(pc[:n], rc[:n])):
            return True
    return False


# ------------------------------------------------------------------------------------------- the rules
class Scan(NamedTuple):
    hit: Optional[Hit]
    relative_unknown: str           # basename for S-RELATIVE-UNKNOWN advice ("" for none)


def _is_data_word(word: str) -> bool:
    return any(c.lower() == "data" for c in re.split(r"[/\\]+", word))


LINK_PROGRAMS = frozenset(("ln", "new-item", "mklink", "subst"))
MOVE_PROGRAMS = frozenset(("mv", "move-item", "io.file.move", "io.directory.move"))
_LINK_TYPES = ("junction", "symboliclink")


def _links(execs, cwd: str, env) -> List[Tuple[str, str]]:
    """RT-A: (link, target) for each link the command itself makes -- `ln -s T L`, `New-Item -ItemType Junction|
    SymbolicLink -Path L -Target T`, `mklink [/D|/J] L T`, `subst X: T` -- both resolved. A delete through the link
    deletes inside the target (the filesystem is never read; only links made in the same command are known)."""
    out = []
    for e in execs:
        p, pair = e.program, None
        if p == "ln" and any(w in ("-s", "--symbolic") or (w[:1] == "-" and w[1:2] != "-" and "s" in w[1:])
                             for w in e.argv):
            ops = [w for w in _plain_operands(e.argv)]
            if len(ops) >= 2:
                pair = (ops[-1], ops[-2])
        elif p == "new-item":
            from .shellparse import ps_param
            kind = (ps_param(e, "ItemType") or ("",))[0].lower()
            link = ps_param(e, "Path", 0) or ps_param(e, "LiteralPath")
            target = ps_param(e, "Target") or ps_param(e, "Value")
            if kind in _LINK_TYPES and link and target:
                pair = (link[0], target[0])
        elif p == "mklink" and e.dialect == "cmd":
            ops = [w for w in e.argv if not re.fullmatch(r"/[A-Za-z]", w)]
            if len(ops) >= 2 and not any(w.lower() == "/h" for w in e.argv):
                pair = (ops[0], ops[1])
        elif p == "subst":
            ops = [w for w in e.argv if not w.startswith("/")]
            if len(ops) >= 2 and re.fullmatch(r"[A-Za-z]:", ops[0]):
                t = targets.resolve_operand(e, ops[1], cwd, env)
                if t:
                    out.append((ops[0][0].lower() + ":", t))
                continue
        if pair:
            link, target = (targets.resolve_operand(e, w, cwd, env) for w in pair)
            if link and target:
                out.append((link.lower().rstrip("/"), target))
    return out


def _through(a: str, links) -> str:
    """a with a same-command link prefix replaced by its target (the first that applies), else a."""
    al = a.lower()
    for link, target in links:
        if al == link or al.startswith(link + "/"):
            return target.rstrip("/") + a[len(link):]
    return a


def _move_pairs(e) -> List[Tuple[str, str]]:
    """(source, destination) words for each move this exec makes: mv, Move-Item, cmd move, [IO.File]/[IO.Directory]
    ::Move."""
    p, argv = e.program, e.argv
    if p in ("io.file.move", "io.directory.move"):
        return [(argv[0], argv[1])] if len(argv) > 1 else []
    if p == "move-item":
        from .shellparse import ps_param
        src = list(ps_param(e, "Path", 0)) + list(ps_param(e, "LiteralPath"))
        dst = ps_param(e, "Destination", 1)
        return [(s, dst[0] if dst else ".") for s in src]
    if p == "move" and e.dialect == "cmd":
        words = [w for w in argv if not re.fullmatch(r"/[A-Za-z?\-]+(:\S*)?", w)]
        return [(s, words[-1]) for s in words[:-1]]
    if p == "mv":
        target, words, k = None, [], 0
        while k < len(argv):
            w = argv[k]
            if w in ("-t", "--target-directory") and k + 1 < len(argv):
                target, k = argv[k + 1], k + 2
                continue
            if w.startswith("--target-directory="):
                target = w.split("=", 1)[1]
            elif w in ("-S", "--suffix") and k + 1 < len(argv):
                k += 1
            elif w == "--":
                words += list(argv[k + 1:])
                break
            elif not (w.startswith("-") and len(w) > 1):
                words.append(w)
            k += 1
        if target is not None:
            return [(s, target) for s in words]
        return [(s, words[-1]) for s in words[:-1]]
    return []


def _everything(word: str) -> bool:
    """`.`, `*`, `./*`, `..`, `.*`: the whole of a folder (S-DEL-UNKNOWN-DIR when that folder is unknown)."""
    comps = [c for c in re.split(r"[/\\]+", word.strip()) if c]
    return bool(comps) and all(c in (".", "..", "*", ".*", "*.*") for c in comps)


def _upstream(execs, i: int) -> list:
    """The execs earlier in the same pipeline as execs[i] (which reads a pipe): back to the first stage."""
    out = []
    j = i - 1
    while j >= 0:
        out.append(execs[j])
        if execs[j].stdin_from != "pipe":
            break
        j -= 1
    return out


def scan(command: str, parsed, roots, cwd: str = "", env: Optional[Mapping[str, str]] = None, has=grep) -> Scan:
    env = os.environ if env is None else env
    resolved: Optional[Hit] = None
    unresolved_reach: Optional[Hit] = None
    unknown_dir: Optional[Hit] = None
    unresolved_any = False
    relative = ""
    opaque = False
    links = _links(parsed.execs, cwd, env) if any(e.program in LINK_PROGRAMS for e in parsed.execs) else []

    def place(a: str) -> Tuple[str, str]:
        """(protected kind, the path it is) for a resolved operand, through any same-command link."""
        b = _through(a, links) if links else a
        return protected_kind(b, roots), b

    for i, e in enumerate(parsed.execs):
        if is_opaque(e):
            opaque = True
        e = substituted(e)
        code = interpreter_code(e)
        if code and resolved is None and (has(DESTROYER, code) or has(INTERP_DELETE, code) or (
                has(INTERP_LAUNCH, code) and has(LAUNCH_DELETE_WORD, code))):
            for kind, rx in _root_regexes(roots):
                if has(rx, code):
                    resolved = Hit(RULE_OF[kind], _match_basename(rx, code))
                    break
        if e.program in MOVE_PROGRAMS and resolved is None:
            # RT-A: a move whose source is protected and whose destination is NOT in that same protected area
            # takes the files out of it -- a delete of the source. An unresolved destination proves nothing.
            for src, dst in _move_pairs(e):
                a = targets.resolve_operand(e, src, cwd, env)
                kind, b = place(a) if a else ("", "")
                d = targets.resolve_operand(e, dst, cwd, env)
                z = targets.zone(b, roots)
                stays = z in RULE_OF and d is not None and targets.zone(place(d)[1], roots) == z
                if kind and d is not None and not stays:
                    resolved = Hit(RULE_OF[kind], posixpath.basename(b.rstrip("/")) or b)
                    break
        ops = operands(e)
        if not ops:
            continue
        ecwd = targets.exec_cwd(e, cwd, env)
        cd_lost = e.cd_before == "?" or (e.cd_before != "" and targets.exec_cwd(e, _NOCWD, env) is None)
        nocwd = ecwd is None and (targets.exec_cwd(e, _NOCWD, env) is not None or e.cd_before == "?")
        # RT-A: a literal cd the guard cannot expand (`cd C:/GOGGAM~1/THEELD~1`) still says where a relative word is.
        lit_base = ecwd or (e.cd_before if e.cd_before not in ("", "?") and _is_abs(e.cd_before) else None)
        for w in ops:
            a = None if w is UNRESOLVED else targets.resolve(w, ecwd or "", e.dialect, env)
            if a is not None:
                kind, b = place(a)
                if kind and resolved is None:
                    resolved = Hit(RULE_OF[kind], posixpath.basename(b.rstrip("/")) or b)
                continue
            if w is not UNRESOLVED:
                # Glob characters in a QUOTED word are literal (`"<install with [x] in its name>/Data"`): a word
                # whose literal reading is a protected path is that path, whatever the glob reading would be.
                lit = literal_abs(w, ecwd, e.dialect)
                kind = protected_kind(lit, roots) if lit else ""
                if kind:
                    if resolved is None:
                        resolved = Hit(RULE_OF[kind], posixpath.basename(lit.rstrip("/")) or lit)
                    continue
            unresolved_any = True
            if (w is UNRESOLVED or e.stdin_from == "pipe") and unresolved_reach is None:
                # RT-A: pipeline input (`Get-ChildItem 'C:\GOG Games' | Remove-Item`, `echo X | xargs -I {} rm {}`)
                # is whatever the earlier stages list: it reaches a root when one of THEIR words is a protected root
                # or an ancestor of one. A listing INSIDE one (`ls *.bak | xargs rm` run in the install) is the
                # fix-round-1 known limit: priced 75 occurrences, 0 real.
                for up in _upstream(parsed.execs, i):
                    for uw in up.argv:
                        ua = targets.resolve_operand(up, uw, cwd, env)
                        if (ua is not None and targets.covers_protected(place(ua)[1], roots)) or (
                                ua is None and raw_reaches(uw, None, up.dialect, roots)):
                            unresolved_reach = Hit("S-DEL-UNRESOLVED", "")
                            break
                    if unresolved_reach:
                        break
            if w is UNRESOLVED:
                continue
            if raw_reaches(w, lit_base, e.dialect, roots) and unresolved_reach is None:
                unresolved_reach = Hit("S-DEL-UNRESOLVED", "")
            if cd_lost and unknown_dir is None and _everything(w):
                unknown_dir = Hit("S-DEL-UNKNOWN-DIR", "")
            if nocwd and not relative and _is_data_word(w) and \
                    targets.resolve(w, targets.exec_cwd(e, _NOCWD, env) or _NOCWD, e.dialect, env) is not None:
                relative = re.split(r"[/\\]+", w.rstrip("/\\"))[-1] or w
    if resolved:
        return Scan(resolved, relative)
    if unresolved_reach:
        return Scan(unresolved_reach, relative)
    if unknown_dir:
        return Scan(unknown_dir, relative)
    if unresolved_any and names_root(command, roots, has):
        return Scan(Hit("S-DEL-UNRESOLVED", ""), relative)
    if not parsed.ok and has(DESTROYER, command) and names_root(command, roots, has):
        return Scan(Hit("S-DEL-FALLBACK", ""), relative)
    if opaque and any(_loose_hit(e, roots, has) for e in parsed.execs):
        return Scan(Hit("S-DEL-FALLBACK", ""), relative)
    return Scan(None, relative)


def _loose_hit(e, roots, has) -> bool:
    """One exec's own words carry a delete word AND a protected root (`printf 'rm -rf "<install>"' | sh`,
    `$SUDO rm -rf <install>`). Per exec, so a delete word in one string and a root in another do not combine."""
    text = " ".join(e.argv) + ("\n" + e.stdin_body if e.stdin_body else "")
    return has(DESTROYER_LOOSE, text) and names_root(text, roots, has)
