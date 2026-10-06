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

Task 12 (the Task 11 review and its Rulings) added: cacls /G /P /R and takeown /F; interpreter code that clears the bit
or rewrites an ACL and names a lock target (INTERP_UNLOCK); a Set-Acl / Set-ItemProperty whose own path is only a
variable, when the command names the install (unresolved is never safe); a Get-ChildItem listing of Data/ piped into
the unlock (its -Filter decides which targets it reaches); and a construct's target is read from ITS statement and the
statements that feed it a variable, never from every statement of the command.

Not covered (residue): an interpreter's code whose path is built at runtime with no lock name in it, and a console
spawned only to look like a person at a terminal (the CLI refuses `--session user-tty`, and every lift is cleared at
the next session start whoever made it).
"""
from __future__ import annotations

import bisect
import re
from typing import List, Mapping, Optional, Tuple

from . import deletes
from . import lock as _lock
from . import shellparse, targets
from .grep import grep

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


# Creation Club names stand for the cc* pattern when a glob or a -Filter is matched (Task 11 review residue: `*.esp`,
# `cc*.esl` reach a cc plugin the explicit list does not name). One sample per extension the pattern allows.
_CC_SAMPLES = tuple("ccbgssse001-fish." + x for x in ("esm", "esl", "esp", "bsa"))


def pattern_target(pat: str) -> str:
    """The lock name a glob/filter pattern can match: the first explicit name, else the pattern itself when it can
    match a Creation Club name; "" when it matches no lock target."""
    p = (pat or "").lower()
    for n in LOCK_NAMES:
        if targets._comp_matches(p, n.lower()):
            return n
    if any(targets._comp_matches(p, s) for s in _CC_SAMPLES):
        return _base(pat)
    return ""


def _target_in(abs_path: str, roots) -> str:
    """The lock-target name abs_path IS (or, as a glob in Data/, matches first); "" otherwise."""
    rel = _data_rel(abs_path, roots)
    if not rel or len(rel) != 2 or rel[0] != "data":
        return ""
    name = rel[1]
    if is_lock_name(name):
        return _base(abs_path)
    if set("*?[{") & set(name):
        return pattern_target(_base(abs_path))
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


_CACLS_UNLOCK = frozenset(("/g", "/p", "/r"))


def _cacls(e, cwd, env, roots) -> str:
    """cacls (the pre-icacls tool, Ruling): /G grant, /P replace, /R revoke rewrite the ACL; /D adds a deny and no
    switch only displays. Its file operands are the words before the first switch; /T recurses."""
    words = [w.lower() for w in e.argv]
    if not any(w in _CACLS_UNLOCK for w in words):
        return ""
    rec = "/t" in words
    for w in e.argv:
        if w.startswith("/"):
            break
        t = _hit(e, w, cwd, env, roots, rec)
        if t:
            return t
    return ""


def _takeown(e, cwd, env, roots) -> str:
    """takeown /F <file> (Ruling): the owner can rewrite any ACL afterwards, so taking a lock target is an unlock.
    /R recurses."""
    words = [w.lower() for w in e.argv]
    rec = "/r" in words
    for k, w in enumerate(words):
        if w == "/f" and k + 1 < len(e.argv):
            t = _hit(e, e.argv[k + 1], cwd, env, roots, rec)
            if t:
                return t
    return ""


def _upstream(parsed, me) -> List[object]:
    """The execs UPSTREAM of `me` in its own pipeline (Task 11 review: not every other exec of the command -- a
    `; dir <master>` after it, or a statement before it, feeds it nothing)."""
    execs = list(parsed.execs)
    i = next((k for k, x in enumerate(execs) if x is me), -1)
    out = []
    while i > 0 and execs[i].stdin_from == "pipe":
        i -= 1
        out.append(execs[i])
    return out


def _peers(parsed, me) -> List[Tuple[object, str]]:
    """Every operand-looking word of the execs upstream in the same pipeline: the input of a cmdlet given no -Path."""
    return [(x, w) for x in _upstream(parsed, me) if x.program != "?" for w in x.argv
            if not (w.startswith("-") or _SWITCH.match(w))]


def _ps_paths(e, parsed) -> List[Tuple[object, str]]:
    own = list(shellparse.ps_param(e, "path", 0)) + list(shellparse.ps_param(e, "literalpath"))
    return [(e, w) for w in own] if own else _peers(parsed, e)


def _listing_hit(x, w, cwd, env, roots) -> str:
    """A Get-ChildItem upstream of the unlock that lists Data/ (or a folder above it, with -Recurse): the files it
    passes on are the ones its -Filter/-Include can match (none given: every file). Task 11 review residue."""
    if x.program != "get-childitem" or w not in shellparse.ps_param(x, "path", 0) + shellparse.ps_param(x, "literalpath"):
        return ""
    a = targets.resolve_operand(x, dict(x.subst).get(w, w), cwd, env)
    if a is None:
        return ""
    rec = any(v.lower().startswith("-r") and "recurse".startswith(v.lower()[1:]) for v in x.argv)
    if _data_rel(a, roots) != ["data"] and not (rec and _covers_data(a, roots)):
        return ""
    pats = list(shellparse.ps_param(x, "filter", 1)) + list(shellparse.ps_param(x, "include"))
    if not pats:
        return LOCK_NAMES[0]
    for p in pats:
        t = pattern_target(p)
        if t:
            return t
    return ""


def _peer_hits(e, parsed, cwd, env, roots) -> str:
    for x, w in _ps_paths(e, parsed):
        t = _hit(x, w, cwd, env, roots, False) or _listing_hit(x, w, cwd, env, roots)
        if t:
            return t
    return ""


_VAR_ONLY = re.compile(r"\$[\w{:]")


def _names_install(command: str, roots) -> bool:
    return any(grep(rx, command) for kind, rx in deletes._root_regexes(roots) if kind == "install")


def _opaque_path(e, command: str, roots) -> str:
    """Ruling (Task 11 review residue): the cmdlet's OWN path is only a variable (`Set-Acl -Path $f ...`,
    `... | % { Set-Acl $_.FullName $a }`), so where it points cannot be read -- and the command names the install.
    Unresolved is never safe (targets.py): the lock name the text names, else the first one."""
    own = list(shellparse.ps_param(e, "path", 0)) + list(shellparse.ps_param(e, "literalpath"))
    if not own or not all(_VAR_ONLY.match(w) for w in own) or not _names_install(command, roots):
        return ""
    named = [_base(w) for w in _text_words(command) if is_lock_name(_base(w))]
    return named[0] if named else LOCK_NAMES[0]


def _sip_unlocks(e) -> bool:
    name = [n.lower() for n in shellparse.ps_param(e, "name", 1)]
    if not name or name[0] not in ("isreadonly", "attributes"):
        return False
    value = [v.lower() for v in shellparse.ps_param(e, "value", 2)]
    return not (name[0] == "isreadonly" and value and value[0] in _TRUE)    # setting the bit adds protection


def _set_itemproperty(e, parsed, cwd, env, roots) -> str:
    if not _sip_unlocks(e):
        return ""
    return _peer_hits(e, parsed, cwd, env, roots)


def _set_acl(e, parsed, cwd, env, roots) -> str:
    return _peer_hits(e, parsed, cwd, env, roots)


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


def _next_solid(text: str) -> List[str]:
    """For each index, the first character at or after it that is not blank (" \\t\\r\\n"), or "" -- one backward
    pass, so the continuation test below is O(1) per newline (Task 12 re-review N1: a per-newline forward scan was
    quadratic in a run of blank lines)."""
    out, nxt = [""] * (len(text) + 1), ""
    for i in range(len(text) - 1, -1, -1):
        if text[i] not in " \t\r\n":
            nxt = text[i]
        out[i] = nxt
    return out


def _statements(text: str) -> List[Tuple[int, int]]:
    """(start, end) of each top-level statement: split on `;`, newline, `&&` and `||` outside quotes and outside
    ( ) / { } -- a `|` pipeline stays one statement, and so does a `% { ... ; ... }` block. A newline does not end
    a statement when the line before it ends in `|` or a backtick, or the next non-blank line starts with `|`
    (Task 12 review I2: a split there cut a pipeline from its upstream). Linear, one pass."""
    spans, start, depth, quote, i, n = [], 0, 0, "", 0, len(text)
    nxt, last = _next_solid(text), ""                  # last: the latest non-blank character outside a quote
    while i < n:
        c = text[i]
        if c == "\n" and not quote and depth == 0:
            if last in ("|", "`") or nxt[i + 1] == "|":
                i += 1
                continue
        elif c not in " \t\r" and not quote:
            last = c
        if quote:
            if c == quote:
                quote = ""
            elif c in "`\\" and quote == '"':
                i += 1
        elif c in "'\"":
            quote = c
        elif c in "({":
            depth += 1
        elif c in ")}":
            depth = max(0, depth - 1)
        elif depth == 0 and (c in ";\n" or text[i:i + 2] in ("&&", "||")):
            last = ""
            spans.append((start, i))
            i += 1 if c in ";\n" else 2
            start = i
            continue
        i += 1
    spans.append((start, n))
    return spans


_VAR = re.compile(r"\$\{?(?:(?:global|script|local|private):)?([A-Za-z_][A-Za-z0-9_]*)\}?", re.IGNORECASE)
# PowerShell's own variables: never assigned by the command, never a missing feeder.
_AUTOMATIC = {"_", "psitem", "true", "false", "null", "args", "input", "this", "env", "home", "pwd", "host",
              "error", "matches", "lastexitcode", "pscmdlet", "psscriptroot", "pscommandpath", "myinvocation"}


# An assignment of a variable: `$v =`, `[type]$v =`, `$scope:v =`, `$v op=`, `Set-Variable`/`New-Variable [-Name] v`
# (Task 12 review I2: only a bare `$v =` was recognised), and -- for _BINDS only -- a `foreach ($v in ...)` heading.
# Group 1 or 2 is the name. One pattern for every name, so a statement is scanned once however many variables it
# reads (Task 12 re-review N2: a pattern per name was quadratic in the variable count).
_ASSIGN_SRC = (r"(?:\[[^\]\n]{1,200}\]\s*)*\$\{?(?:(?:global|script|local|private):)?([A-Za-z_][A-Za-z0-9_]*)\}?"
               r"\s*(?:[+\-*/]?=)(?!=)"
               r"|(?:set|new)-variable\s+(?:-name\s+)?['\"]?(?:(?:global|script|local|private):)?"
               r"([A-Za-z_][A-Za-z0-9_]*)['\"]?(?![\w])")
_ASSIGN_AT_START = re.compile(r"\s*(?:" + _ASSIGN_SRC + r")", re.IGNORECASE)
_BINDS = re.compile(_ASSIGN_SRC + r"|foreach\s*\(\s*\$([A-Za-z_][A-Za-z0-9_]*)\s+in\b", re.IGNORECASE)


def _assigned_at_start(stmt: str) -> str:
    """The variable a statement assigns, when it opens with an assignment; else ""."""
    m = _ASSIGN_AT_START.match(stmt)
    return (m.group(1) or m.group(2)).lower() if m else ""


def _unfed(text: str, spans, chosen) -> bool:
    """Fail closed (Task 12 review I2): the construct's statements read a variable that nothing in the command is
    seen to assign -- its value came from a shape this reader does not know, so the statement scope cannot be trusted."""
    body = "\n".join(text[spans[j][0]:spans[j][1]] for j in chosen)
    bound = {next(g for g in m.groups() if g).lower() for m in _BINDS.finditer(body)}
    return bool({v.lower() for v in _VAR.findall(body)} - _AUTOMATIC - bound)


def _assigners(text: str, spans) -> dict:
    """name -> the statements that open with its assignment, ascending. Built once per command."""
    out: dict = {}
    for i, (a, b) in enumerate(spans):
        name = _assigned_at_start(text[a:b])
        if name:
            out.setdefault(name, []).append(i)
    return out


def _feeders(text: str, spans, k: int, assigners=None) -> List[int]:
    """The earlier statements that feed statement k through a variable: for every `$var` statement k (or a feeder)
    reads, the LATEST earlier statement that assigns it (`$var = ...`, or `foreach ($var in ...)` heading one).
    Transitive (`$files = gci <master>; foreach ($f in $files) { $f.IsReadOnly = $false }`), each statement taken
    once, so it is bounded by the statement count. A statement that assigns nothing the construct reads is not its
    target (Task 11 review: `Copy-Item <master> C:/Temp/x/;` before it, `; Get-FileHash <master>` after it)."""
    if assigners is None:
        assigners = _assigners(text, spans)
    out, todo, seen = [], [k], set()
    while todo:
        j = todo.pop()
        for name in {v.lower() for v in _VAR.findall(text[spans[j][0]:spans[j][1]])} - seen:
            seen.add(name)
            at = assigners.get(name, ())
            i = bisect.bisect_left(at, j) - 1          # the LATEST assigner before statement j
            if i >= 0 and at[i] not in out:
                out.append(at[i])
                todo.append(at[i])
    return out


def _construct_hit(text: str, parsed, cwd, env, roots) -> str:
    """A PowerShell unlock construct's target, read from the STATEMENT that holds it (Task 11 review: a statement
    before or after it -- `Copy-Item <master> C:/Temp/x/;` or `; Get-FileHash <master>` -- is not its target). A
    `$var.IsReadOnly = $false` receiver also reads the latest earlier statement that assigns `$var`."""
    spans, done = _statements(text), set()
    starts, assigners, whole = [a for a, _ in spans], None, None
    for m in PS_CONSTRUCT.finditer(text):
        k = max(0, bisect.bisect_right(starts, m.start()) - 1)  # the statement holding the construct
        if k in done:                                           # one reading per statement, however many constructs
            continue
        done.add(k)
        if assigners is None:
            assigners = _assigners(text, spans)
        chosen = sorted([k] + _feeders(text, spans, k, assigners))
        if _unfed(text, spans, chosen):
            if whole is None:                                   # the base's whole-text reading: the same for every
                whole = _text_hit(text, parsed, cwd, env, roots)    # statement, so read once (re-review timing)
            if whole:
                return whole
            continue
        stmt = "\n".join(text[spans[j][0]:spans[j][1]] for j in chosen)
        mine = tuple(x for x in parsed.execs if x.segment.strip() and x.segment.strip() in stmt)
        t = _text_hit(stmt, shellparse.Parsed(mine, True, ""), cwd, env, roots)
        if t:
            return t
        for x in mine:                                          # I3: Get-ChildItem Data [-Filter *.esm] | % {...}
            for w in x.argv:
                t = _listing_hit(x, w, cwd, env, roots)
                if t:
                    return t
    return ""


# R4 fallback: the command could not be read (or ran something opaque). An unlock construct of ANY dialect and a
# lock target in the text, together.
_ANY_CONSTRUCT = re.compile(
    r"\battrib(?:\.exe)?\b[^\n;&|]{0,400}\s-r\b"
    r"|\bicacls(?:\.exe)?\b[^\n;&|]{0,400}\s/(?:remove|reset|grant|restore|setowner)"
    r"|\bset-acl\b|\bset-itemproperty\b[^\n;&|]{0,400}\b(?:isreadonly|attributes)\b", re.IGNORECASE)


# Interpreter code (Ruling, the G1-style interpreter rule): `python -c`, `node -e`, a heredoc or pipe fed to an
# interpreter (deletes.interpreter_code) whose code clears the read-only bit or rewrites an ACL, and names a lock
# target. Python's os.chmod on Windows only toggles read-only; the shell tools and cmdlets may also be launched from it.
INTERP_UNLOCK = re.compile(
    r"\bos\.chmod\s*\(|\bchmod(?:Sync)?\s*\(|\bSetFileAttributesW?\s*\(|SetAttributes\s*\(|SetAccessControl\s*\("
    r"|\.IsReadOnly\s*=\s*(?:False|false|\$false|0)\b|\bSet-Acl\b|\btakeown\b"
    r"|\bSet-ItemProperty\b[^\n]{0,400}\b(?:IsReadOnly|Attributes)\b"
    r"|\battrib(?:\.exe)?\b[^\n]{0,400}[\s'\",]-[rR]\b"
    r"|\bi?cacls(?:\.exe)?\b[^\n]{0,400}[\s'\",]/(?:remove|reset|grant|restore|setowner|[GPR]\b)", re.IGNORECASE)


def _interpreter_hit(e, cwd, env, roots) -> str:
    code = deletes.interpreter_code(e)
    if not code or not INTERP_UNLOCK.search(code):
        return ""
    for w in _text_words(code):
        if is_lock_name(_base(w)) or set("*?[") & set(_base(w)):
            t = _hit(e, w, cwd, env, roots, False)
            if t:
                return t
    return ""


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
        elif e.program == "cacls":
            t = _cacls(e, cwd, env, roots)
        elif e.program == "takeown":
            t = _takeown(e, cwd, env, roots)
        elif e.program == "set-itemproperty":
            t = _set_itemproperty(e, parsed, cwd, env, roots) or (_opaque_path(e, command, roots) if _sip_unlocks(e) else "")
        elif e.program == "set-acl":
            t = _set_acl(e, parsed, cwd, env, roots) or _opaque_path(e, command, roots)
        else:
            t = _interpreter_hit(e, cwd, env, roots)
        if t:
            return "S-LIFT-RAW", t
    ps = _ps_text(command, parsed, shell)
    if ps and PS_CONSTRUCT.search(ps):
        t = _construct_hit(ps, parsed, cwd, env, roots)
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


_HELP = frozenset(("-h", "--help"))


def _only_help(args) -> bool:
    """`lock disable --help` (Task 11 review): the CLI's only token besides `lock disable` is -h/--help, which prints
    the usage and disables nothing."""
    rest = list(args)
    for w in ("lock", "disable"):
        if w in rest:
            rest.remove(w)
    return len(rest) == 1 and rest[0] in _HELP


def permanent(parsed, command: str) -> bool:
    for e in parsed.execs:
        args = cli_args(e)
        if args is None:
            continue
        pos = _positionals(args)
        if len(pos) >= 2 and (pos[0], pos[1]) == ("lock", "disable") and not _only_help(args):
            return True
    return any(_CLI_TEXT.search(t) and _DISABLE_TEXT.search(t) for t in _opaque_texts(command, parsed))


def restore_in_place(parsed) -> bool:
    """Task 15 fix round 1 (I3): `skyguard snapshot restore ... --in-place` writes snapshot files over the live load
    order, INIs, saves or scripts -- a write into the install the other write rules never see (the CLI does it)."""
    for e in parsed.execs:
        args = cli_args(e)
        if args is None or _HELP & set(args):
            continue
        # Fix round 2 (N1): the verb is found by position-free search (`snapshot --in-place restore <id>` is accepted by
        # argparse), and the flag in any spelling that could ever mean it -- the parser now refuses abbreviations
        # (allow_abbrev=False), but `--in`, `--in-p` and `--in-place=...` still ask.
        verb = "snapshot" in args and "restore" in args[args.index("snapshot") + 1:]
        if verb and any(_in_place_flag(a) for a in args):
            return True
    return restore_backup(parsed)


# Plan 6 final fix (lane B I1): the pre-edit backup restore writes a backup back over the live file its manifest line
# names -- the same act as an in-place snapshot restore, so the same ask (S-RESTORE-INPLACE). The parser names the
# script `restore-from-backup` however it is launched (bash, ./, a PowerShell `bash scripts\...`); the python entry is
# backup.restore_main with `--restore`, or backup._restore called directly.
_BACKUP_ENTRY = re.compile(r"\brestore_main\b|\b_restore\s*\(")


def restore_backup(parsed) -> bool:
    for e in parsed.execs:
        if e.program == "restore-from-backup" and "--restore" in e.argv:
            return True
        if shellparse._PYTHON.fullmatch(e.program or "") and any("backup" in a and _BACKUP_ENTRY.search(a)
                                                                for a in e.argv):
            code = next(a for a in e.argv if "backup" in a and _BACKUP_ENTRY.search(a))
            if "--restore" in e.argv or re.search(r"\b_restore\s*\(", code) or "--restore" in code:
                return True
    return False


def _in_place_flag(word: str) -> bool:
    head = word.split("=", 1)[0]
    return len(head) >= 4 and "--in-place".startswith(head)


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
