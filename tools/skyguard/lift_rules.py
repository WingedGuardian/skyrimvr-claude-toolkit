r"""The lift rules (Plan 6 Task 11; D6, R7): who may lift the guards and the Layer 2 lock, and how.

    note(parsed)                                -> op text for S-LIFT-NOTE, or ""
    permanent(parsed, command)                  -> True for S-LIFT-PERMANENT (`lock disable`)
    raw_unlock(command, parsed, roots, cwd, env) -> ("S-LIFT-UNLOCKCMD", "") | ("S-LIFT-RAW", file) | None

Every rule asks about the program that RUNS (shellparse) and where its operand RESOLVES (targets), never about the
command's text, except in two narrow places, each written out below: a PowerShell member assignment or static call
the parser does not turn into an Exec (`.IsReadOnly = $false`, `.Attributes = ...`, `[IO.File]::SetAttributes(...)`),
and the R4 fallback when the parser could not read the command (or ran something opaque). Both need an unlock
CONSTRUCT and a lock-target name together, so a read that merely mentions them (`grep "attrib -R"`) stays allowed.

A CURRENT LOCK TARGET is a file directly inside the install's Data/ whose name is on the lock's own per-edition list
(lock._names, R1, every edition at once: the rule must not need the filesystem to know the edition) or is a Creation
Club plugin/archive (cc*.esm/.esl/.esp/.bsa, the same rule tools/skyguard-unlock.cmd applies). A glob in Data/ that
matches one, and a RECURSIVE icacls/attrib over Data/ or a folder above it, reach one too. A vanilla name anywhere
else (a scratch copy) is not a target. An operand the resolver cannot place (a variable) counts when its own
basename is a lock name: unresolved is never "safe" (targets.py).

Not covered (residue, written into the report): an interpreter's own code (`python -c "os.chmod(...)"`), cacls/
takeown, and a console spawned only to look like a person at a terminal (the CLI refuses `--session user-tty`, and
every lift is cleared at the next session start whoever made it).
"""
from __future__ import annotations

import re
from typing import List, Mapping, Optional, Tuple

from . import lock as _lock
from . import shellparse, targets

# ---- the lock targets --------------------------------------------------------------------------------------------
_ORDERED = []
for _ed in ("vr", "se"):
    for _n in _lock._names(_ed):
        if _n.lower() not in [x.lower() for x in _ORDERED]:
            _ORDERED.append(_n)
LOCK_NAMES = tuple(_ORDERED)
_LOWER = frozenset(n.lower() for n in LOCK_NAMES)
_CC = re.compile(r"cc[a-z0-9][a-z0-9_\-]{0,120}\.(?:esm|esl|esp|bsa)", re.IGNORECASE)


def is_lock_name(name: str) -> bool:
    n = (name or "").lower()
    return n in _LOWER or bool(_CC.fullmatch(n))


def _base(word: str) -> str:
    return re.split(r"[/\\]+", (word or "").rstrip("/\\"))[-1]


def _data_rel(abs_path: str, roots) -> Optional[List[str]]:
    """Components of abs_path below the install folder that holds it (the longest), else None."""
    if not abs_path:
        return None
    p, best = abs_path.lower().rstrip("/"), None
    for d in roots.install_dirs:
        dl = d.lower().rstrip("/")
        if (p == dl or p.startswith(dl + "/")) and (best is None or len(dl) > len(best)):
            best = dl
    if best is None:
        return None
    return [c for c in p[len(best):].split("/") if c]


def _target_in(abs_path: str, roots) -> str:
    """The lock-target name abs_path IS (or, as a glob in Data/, matches first); "" otherwise."""
    rel = _data_rel(abs_path, roots)
    if not rel or len(rel) != 2 or rel[0] != "data":
        return ""
    name = rel[1]
    if is_lock_name(name):
        return _base(abs_path)
    if set("*?[{") & set(name):
        for n in LOCK_NAMES:
            if targets._comp_matches(name, n.lower()):
                return n
    return ""


def _covers_data(abs_path: str, roots) -> bool:
    """abs_path is the install's Data/ folder or a folder above it (globs in any component matched)."""
    if not abs_path:
        return False
    pc = targets._split(abs_path.lower().rstrip("/") or "/")
    for d in roots.install_dirs:
        rc = targets._split((d.lower().rstrip("/") or d.lower()) + "/data")
        if len(pc) <= len(rc) and all(targets._comp_matches(a, b) for a, b in zip(pc, rc)):
            return True
    return False


def _hit(e, word: str, cwd: str, env, roots, recursive: bool) -> str:
    """The lock-target name `word` reaches as an operand of `e` ("" for none)."""
    word = dict(e.subst).get(word, word)          # a same-command literal assignment (`R=...; icacls "$R/..."`)
    a = targets.resolve_operand(e, word, cwd, env)
    if a is None:
        b = _base(word)
        return b if is_lock_name(b) else ""
    t = _target_in(a, roots)
    if t:
        return t
    return LOCK_NAMES[0] if recursive and _covers_data(a, roots) else ""


# ---- S-LIFT-RAW: the unlock primitives, per program ---------------------------------------------------------------
_SWITCH = re.compile(r"/[A-Za-z?]{1,2}\Z")
_ICACLS_UNLOCK = ("/remove", "/reset", "/grant", "/restore", "/setowner")
_TRUE = frozenset(("$true", "1", "true"))


def _attrib(e, cwd, env, roots) -> str:
    words = [w.lower() for w in e.argv]
    if "-r" not in words:
        return ""
    rec = "/s" in words
    for w in e.argv:
        if w[:1] in "+-" or _SWITCH.match(w):
            continue
        t = _hit(e, w, cwd, env, roots, rec)
        if t:
            return t
    return ""


def _icacls(e, cwd, env, roots) -> str:
    words = [w.lower() for w in e.argv]
    if not e.argv or not any(w.startswith(_ICACLS_UNLOCK) for w in words):
        return ""
    if e.argv[0].startswith("/"):
        return ""
    rec = "/t" in words or "/restore" in words
    return _hit(e, e.argv[0], cwd, env, roots, rec)


def _peers(parsed, me) -> List[Tuple[object, str]]:
    """Every operand-looking word of every OTHER exec: the pipeline input of a cmdlet given no -Path."""
    return [(x, w) for x in parsed.execs if x is not me and x.program != "?" for w in x.argv
            if not (w.startswith("-") or _SWITCH.match(w))]


def _ps_paths(e, parsed) -> List[Tuple[object, str]]:
    own = list(shellparse.ps_param(e, "path", 0)) + list(shellparse.ps_param(e, "literalpath"))
    return [(e, w) for w in own] if own else _peers(parsed, e)


def _set_itemproperty(e, parsed, cwd, env, roots) -> str:
    name = [n.lower() for n in shellparse.ps_param(e, "name", 1)]
    if not name or name[0] not in ("isreadonly", "attributes"):
        return ""
    value = [v.lower() for v in shellparse.ps_param(e, "value", 2)]
    if name[0] == "isreadonly" and value and value[0] in _TRUE:
        return ""                                          # setting the bit adds protection
    for x, w in _ps_paths(e, parsed):
        t = _hit(x, w, cwd, env, roots, False)
        if t:
            return t
    return ""


def _set_acl(e, parsed, cwd, env, roots) -> str:
    for x, w in _ps_paths(e, parsed):
        t = _hit(x, w, cwd, env, roots, False)
        if t:
            return t
    return ""


# A PowerShell member assignment or static call: no Exec carries it, so the CONSTRUCT is matched in the text and the
# target must still come from a resolved operand (an exec's argument, or a quoted/bare word of the text).
PS_CONSTRUCT = re.compile(
    r"\.IsReadOnly\s*=\s*(?:\$false|\$null|0|\$0)(?![\w])"
    r"|\.Attributes\s*(?:=|\+=|-=)(?!=)"
    r"|::\s*(?:SetAttributes|SetAccessControl)\s*\("
    r"|\.SetAccessControl\s*\(", re.IGNORECASE)
# A quote left open (an unreadable command) runs to the end of its line: the path inside it is still read.
_TEXT_WORD = re.compile(r"'([^'\n]{1,400})'?|\"([^\"\n]{1,400})\"?|([^\s'\"(),;|=+]{1,400})")
_NOT_A_PATH = shellparse.Exec("", (), (), (), "powershell", "", "", "")


def _text_words(text: str) -> List[str]:
    out = []
    for m in _TEXT_WORD.finditer(text):
        w = m.group(1) or m.group(2) or m.group(3) or ""
        out.append(w)
        if m.group(2):
            out += [x for x in re.findall(r"'([^'\n]{1,400})'", w)]
    return out


def _text_hit(text: str, parsed, cwd, env, roots) -> str:
    """A target reached by a text-level construct: exec operands first (they carry cd_before), then the text's words."""
    for x in parsed.execs:
        if x.program == "?":
            continue
        for w in x.argv:
            if w.startswith("-"):
                continue
            t = _hit(x, w, cwd, env, roots, False)
            if t:
                return t
    for w in _text_words(text):
        if not is_lock_name(_base(w)) and not (set("*?[") & set(_base(w))):
            continue
        t = _hit(_NOT_A_PATH, w, cwd, env, roots, False)
        if t:
            return t
    return ""


# R4 fallback: the command could not be read (or ran something opaque). An unlock construct of ANY dialect and a
# lock target in the text, together.
_ANY_CONSTRUCT = re.compile(
    r"\battrib(?:\.exe)?\b[^\n;&|]{0,400}\s-r\b"
    r"|\bicacls(?:\.exe)?\b[^\n;&|]{0,400}\s/(?:remove|reset|grant|restore|setowner)"
    r"|\bset-acl\b|\bset-itemproperty\b[^\n;&|]{0,400}\b(?:isreadonly|attributes)\b", re.IGNORECASE)


_UNLOCK_NAMES = ("skyguard-unlock.cmd", "skyguard-unlock")


def _unlock_cmd(e, cwd, env, roots) -> bool:
    """The install's own skyguard-unlock.cmd runs. A copy elsewhere unlocks ITS OWN ../Data (a scratch install), so
    it counts only when the word that names it resolves into the install, or cannot be resolved (never "safe")."""
    if e.program != "skyguard-unlock":
        return False
    named = [w for w in _text_words(e.segment) if _base(w).lower() in _UNLOCK_NAMES]
    for w in named or [""]:                                # no naming word found: "" never resolves -> counts
        a = targets.resolve_operand(e, w, cwd, env)
        if a is None or targets.zone(a, roots) == "install":
            return True
    return False


_STMT = re.compile(r"[;&|\n]+")
_LEAD = re.compile(r"\s*(?:@|&\s*|call\s+|start\s+(?:\"[^\"]*\"\s+)?|cmd(?:\.exe)?\s+/[ck]\s+)*[\"']?", re.IGNORECASE)
_UNLOCK_WORD = re.compile(r"(?:[^\s\"']*[/\\])?skyguard-unlock(?:\.cmd)?(?=[\s\"']|\Z)", re.IGNORECASE)


def _text_unlock_cmd(text: str) -> bool:
    for stmt in _STMT.split(text):
        m = _LEAD.match(stmt)
        if _UNLOCK_WORD.match(stmt, m.end() if m else 0):
            return True
    return False


_PS_WORD = re.compile(r"(?<![\w-])(?:pwsh|powershell)(?![\w-])", re.IGNORECASE)


def _ps_text(command: str, parsed, shell: str) -> str:
    """The text a PowerShell construct could RUN in: the command minus its heredoc data/interpreter bodies, and only
    when PowerShell can run it at all (the dialect is PowerShell or unknown, or a PowerShell exec or a pwsh/powershell
    word is left). PRICED: a heredoc writing a test script that contains `.IsReadOnly = $false` is data."""
    text = command
    for e in parsed.execs:
        if e.stdin_body and e.stdin_from in ("heredoc:data", "heredoc:interpreter"):
            text = text.replace(e.stdin_body, " ")
    if (shell or "").lower() in ("", "powershell", "pwsh") or _PS_WORD.search(text) \
            or any(e.dialect == "powershell" for e in parsed.execs):
        return text
    return ""


def raw_unlock(command: str, parsed, roots, cwd: str = "", env: Optional[Mapping[str, str]] = None, shell: str = ""):
    env = {} if env is None else env
    for e in parsed.execs:
        if _unlock_cmd(e, cwd, env, roots):
            return "S-LIFT-UNLOCKCMD", ""
    for e in parsed.execs:
        t = ""
        if e.program == "attrib":
            t = _attrib(e, cwd, env, roots)
        elif e.program == "icacls":
            t = _icacls(e, cwd, env, roots)
        elif e.program == "set-itemproperty":
            t = _set_itemproperty(e, parsed, cwd, env, roots)
        elif e.program == "set-acl":
            t = _set_acl(e, parsed, cwd, env, roots)
        if t:
            return "S-LIFT-RAW", t
    ps = _ps_text(command, parsed, shell)
    if ps and PS_CONSTRUCT.search(ps):
        t = _text_hit(ps, parsed, cwd, env, roots)
        if t:
            return "S-LIFT-RAW", t
    for text in _opaque_texts(command, parsed):
        if _text_unlock_cmd(text):
            return "S-LIFT-UNLOCKCMD", ""
        if _ANY_CONSTRUCT.search(text):
            t = _text_hit(text, shellparse.Parsed((), True, ""), cwd, env, roots)
            if t:
                return "S-LIFT-RAW", t
    return None


def _opaque_texts(command: str, parsed) -> List[str]:
    if not parsed.ok:
        return [command]
    return [e.segment for e in parsed.execs if e.program == "?"]


# ---- the skyguard CLI: S-LIFT-NOTE and S-LIFT-PERMANENT ------------------------------------------------------------
_MODULES = frozenset(("skyguard", "skyguard.cli", "skyguard.__main__", "tools.skyguard", "tools.skyguard.__main__"))
_PY_VALUED = frozenset(("-X", "-W", "--check-hash-based-pycs"))


def _is_cli_path(word: str) -> bool:
    n = word.replace("\\", "/").rstrip("/").lower()
    return (n == "skyguard" or n.endswith("/skyguard") or n.endswith("skyguard/__main__.py")
            or n.endswith("skyguard/cli.py"))


def cli_args(e) -> Optional[Tuple[str, ...]]:
    """The arguments the skyguard CLI receives when `e` runs it (any launcher), else None."""
    if e.program == "skyguard":
        return e.argv
    if not shellparse._PYTHON.fullmatch(e.program or ""):
        return None
    argv, k = e.argv, 0
    while k < len(argv):
        w = argv[k]
        if w == "-m":
            return argv[k + 2:] if k + 1 < len(argv) and argv[k + 1].lower() in _MODULES else None
        if w in _PY_VALUED:
            k += 2
            continue
        if w.startswith("-") and len(w) > 1:
            k += 1
            continue
        return argv[k + 1:] if _is_cli_path(w) else None
    return None


def _positionals(args) -> List[str]:
    out, k = [], 0
    while k < len(args):
        w = args[k]
        if w.startswith("-") and len(w) > 1:
            if "=" in w or not w.startswith("--"):
                k += 1
            else:
                k += 2                                     # --root R, --session S, --project P (and abbreviations)
            continue
        out.append(w)
        k += 1
    return out


def _cli_actions(parsed):
    for e in parsed.execs:
        args = cli_args(e)
        if args is None:
            continue
        pos = _positionals(args)
        if len(pos) >= 2:
            yield pos[0], pos[1], pos[2:]                 # argparse is case-sensitive: `lock Disable` is an error


_CLI_TEXT = re.compile(r"skyguard", re.IGNORECASE)
_DISABLE_TEXT = re.compile(r"\block\s+disable\b", re.IGNORECASE)


def permanent(parsed, command: str) -> bool:
    if any(a == ("lock", "disable") for a in ((c, x) for c, x, _ in _cli_actions(parsed))):
        return True
    return any(_CLI_TEXT.search(t) and _DISABLE_TEXT.search(t) for t in _opaque_texts(command, parsed))


def _clean(name: str) -> str:
    b = re.sub(r"[^\w .\-]", "", _base(name))[:60]
    return b or "a file"


def _files_phrase(files) -> str:
    names = [_clean(f) for f in files]
    if not names:
        return "every locked file"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return names[0] + " and " + names[1]
    return "%s, %s and %d more" % (names[0], names[1], len(names) - 2)


def note(parsed) -> str:
    """The {op} of S-LIFT-NOTE for the first D6 lift the command runs ("" for none)."""
    for cmd, action, rest in _cli_actions(parsed):
        if cmd == "lock" and action == "unlock":
            return "the lock on " + _files_phrase(rest)
        if cmd == "guards" and action == "off":
            return "the guards"
    return ""
