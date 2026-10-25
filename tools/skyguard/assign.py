r"""assign: same-command LITERAL assignments, for operands written as a variable (Plan 5 Task 7b).

    write_counts(text)                 -> {name (lower case): how many places in `text` may WRITE it}
    bash_value(raw)                    -> the literal value of a bash assignment's right-hand side, or None
    ps_assignment(segment)             -> (name, value) for `$name = 'literal'`, or None
    expand_word(raw, dialect, lookup)  -> the word with each $NAME / ${NAME} replaced, or None
    normalize(text)                    -> text with backslash-newline joined and every quote/backslash removed
    poisoned(text, dialect)            -> the poison class that turns ALL resolution in the command off, or ""

Pure, stdlib only, Python 3.8. Linear: every regex is a bounded run or a single character class.

WHY. `D=/tmp/x; rm -rf "$D"` in a command that also names the install was denied as an unresolved delete: ~100
of ~190 such denies in the history were scratch paths. A variable is resolved only when the answer is CERTAIN;
anything less stays unresolved (the deny it was before). The FLOW half (which assignment reaches which use) is in
shellparse, which knows the statement structure; this module holds the TEXT half.

POLICY (Task 7b fix round 1, controller ruling): BROAD, CONSERVATIVE POISONING over precise modelling. Resolution is
a convenience -- it lifts false positives; a wrong resolution is a fail-open. So when in doubt the WHOLE command's
resolution is off (every variable operand unresolved, the deny it was). poisoned() reads the command after
normalize() -- backslash-newline continuations joined, then every quote and backslash removed from every word,
regardless of quoting (`"source"`, `so''urce`, `\source`, `rea\<newline>d` all read as the word) -- and poisons
when any statement contains:
  - bash: a function definition (`name()`, `function name`); at command position (after separators, `then`/`do`/
    `else`/`!`/`time`/`if`/`while`/`until`, leading assignments, and `builtin`/`command`/`exec` with or without
    options) `trap`, `eval`, `source`, `.`, `read`, `readarray`, `mapfile`, `getopts`, `declare`, `typeset`,
    `local`, `readonly`, `unset`, `let`, or `export` with a `$`-word; `printf` with a `-v` option (also `-vNAME`);
    `wait` with `-p`; `(( ... = ... ))` / `$(( ... = ... ))`; `${NAME:=...}` / `${NAME=...}`; an array assignment
    (`N[i]=`, `N=(...)`); a name written through `$` (`$N=`);
  - PowerShell: any `*-Variable` cmdlet or alias (`sv`, `nv`, `gv`, `clv`, `rv`, `set-variable`, ...); the
    `variable:` drive anywhere (so Set-Item/New-Item/Set-Content/Add-Content/Clear-Item on it); any parameter that is
    a prefix (2+ letters) of -OutVariable/-ErrorVariable/-WarningVariable/-InformationVariable/-PipelineVariable or
    one of -ov/-ev/-wv/-iv/-pv; `.Value =`; `[ref]`; `PSVariable`, `SessionState`, `InvokeScript`; `iex` /
    `Invoke-Expression`; `&` or `.` followed by a variable, a quoted command or a scriptblock; a scriptblock
    assigned (`= {`); a scope prefix `$script:` / `$global:` / `$private:` / `$using:` / `$local:`;
  - cmd: never resolves at all (it expands %K% when the line is read, before `set` runs), which covers delayed
    expansion (`!VAR!`), `for /f ... set` and `call` a fortiori.
shellparse also turns a scanner's resolution off when a closing keyword (`fi`/`done`/`}`) has no opener.

WHAT COUNTS AS ONE LITERAL ASSIGNMENT. shellparse records `NAME=literal` (bash) or `$name = 'literal'`
(PowerShell) only when ALL of these hold, else the name stays unresolved:
  - write_counts(<the whole command>) says the name is written exactly ONCE (case-insensitively) -- any second
    write anywhere (a reassignment before or after the use, a `for`/`read`/`foreach` variable, `export`, `local`,
    `declare`, `unset`, `N+=x`, `N++`, `exec {N}>`, PowerShell `$n =`, Set-Variable, -OutVariable, a function
    parameter, a write inside a string or heredoc body) makes it unresolved: over-counting is safe (the forms
    POLICY lists poison the whole command anyway);
  - the value is a plain literal: no `$`, backtick, command substitution, unquoted `~` or backslash; PowerShell:
    a single- or double-quoted string with no `$` and no backtick;
  - it is a statement of its own in the ROOT command (not inside `bash -c`, eval, a backtick or a heredoc), in
    the top-level statement list (not inside `( )`, `$( )`, `{ }`, if/while/until/for/select/case or a
    function body), not in a pipeline, not backgrounded with `&`, and not after a `||`;
  - poisoned() finds nothing in the whole command (POLICY above), and no closing keyword lacks its opener.
A use resolves when it comes after the assignment: in a later statement (the assignment was FIRST in its own
`&&` list, so it always ran), or later in the same pure `&&` chain. cmd.exe never resolves: `set K=v & rd %K%`
expands %K% when the LINE is read, before `set` runs.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, Optional, Tuple

# ------------------------------------------------------------------------------------------------- write counting
_IDENT = r"[A-Za-z_][A-Za-z0-9_]{0,63}"
# bash/cmd/python-ish `N=`, `N+=`, `N[i]=`, `N = ` (arithmetic, python, PowerShell without `$`): over-counting is safe
_W_PLAIN = re.compile(r"(?<![\w$])(" + _IDENT + r")(?:\[[^\]\n]{0,200}\])?[ \t]{0,64}(?:[-+*/%&|^]|<<|>>|\?\?)?=")
# PowerShell `$n =`, `$script:n +=`, `${n} =`
_W_DOLLAR = re.compile(r"\$\{?(?:[A-Za-z]{1,32}:)?(" + _IDENT + r")\}?[ \t]{0,64}(?:[-+*/%]|\?\?)?=")
_W_FD = re.compile(r"\{(" + _IDENT + r")\}[<>]")                            # exec {N}>f allocates an fd into N
_W_INC = re.compile(r"(?<![\w])(" + _IDENT + r")(?:\+\+|--)|(?:\+\+|--)\$?(" + _IDENT + r")")
_PIECE = re.compile(r"[^;&|\n]+")
# A statement that names one of these may write ANY name it mentions.
_KW_BASH = re.compile(r"(?<![\w$-])(?:read|readarray|mapfile|getopts|unset|declare|typeset|local|export|readonly|"
                      r"let|for|select|coproc)(?![\w-])")
_KW_PS = re.compile(r"(?<![\w$-])(?:set-variable|new-variable|clear-variable|remove-variable|sv|nv|clv|rv|"
                    r"foreach|param|function|filter|variable:|\[ref\]|psvariable|set|tee-object|-variable|-bindingvariable|"
                    r"-outvariable|-ov|-errorvariable|-ev|"
                    r"-warningvariable|-wv|-informationvariable|-iv|-pipelinevariable|-pv)(?![\w-])", re.IGNORECASE)
_ANY_IDENT = re.compile(_IDENT)
_BARE_IDENT = re.compile(r"(?<![\w$])(?<!\$\{)(" + _IDENT + r")")


# Names the shell sets itself, or refuses to assign: `PWD=/tmp; cd Data; rm "$PWD/x"` deletes Data/x (cd rewrote
# PWD), and PowerShell's `$HOME = 'x'` fails (read-only), leaving the real home. Never resolved, either dialect.
SPECIAL = frozenset((
    "pwd", "oldpwd", "home", "ifs", "path", "cdpath", "random", "srandom", "seconds", "lineno", "bashpid",
    "epochseconds", "epochrealtime", "uid", "euid", "ppid", "groups", "funcname", "bash_source", "bash_lineno",
    "bash_argv", "bash_argc", "bash_command", "dirstack", "pipestatus", "histcmd", "shlvl", "reply", "optind",
    "optarg", "opterr", "_",
    "args", "consolefilename", "error", "event", "eventargs", "eventsubscriber", "executioncontext", "false",
    "foreach", "host", "input", "iscoreclr", "islinux", "ismacos", "iswindows", "lastexitcode", "matches",
    "myinvocation", "nestedpromptlevel", "null", "pid", "profile", "psboundparameters", "pscmdlet",
    "pscommandpath", "psculture", "psdebugcontext", "psedition", "pshome", "psitem", "psscriptroot",
    "pssenderinfo", "psuiculture", "psversiontable", "sender", "shellid", "stacktrace", "switch", "this", "true",
    "userprofile", "homedrive", "homepath"))


def write_counts(text: str) -> Dict[str, int]:
    """name (lower case) -> number of places in `text` that may write it. An OVER-count: anything that might be a
    write counts, because a name written more than once is never resolved."""
    out: Dict[str, int] = {}

    def add(name: str) -> None:
        k = name.lower()
        out[k] = out.get(k, 0) + 1
    for rx in (_W_PLAIN, _W_DOLLAR, _W_FD):
        for m in rx.finditer(text):
            add(m.group(1))
    for m in _W_INC.finditer(text):
        add(m.group(1) or m.group(2))
    for p in _PIECE.finditer(text):
        piece = p.group()
        if _KW_PS.search(piece):
            for m in _ANY_IDENT.finditer(piece):                  # PowerShell: `foreach ($x in ...)` writes $x
                add(m.group())
        elif _KW_BASH.search(piece):
            for m in _BARE_IDENT.finditer(piece):                 # bash: `for x in "$SP"/*` writes x, reads SP
                add(m.group(1))
    return out


# ------------------------------------------------------------------------------------------------- poisoning
_CONT = re.compile(r"[\\`]\r?\n")                       # bash / PowerShell line continuation
_DEQUOTE = str.maketrans("", "", "'\"\\`\u2018\u2019\u201a\u201b\u201c\u201d\u201e")


def normalize(text: str) -> str:
    """Continuations joined, then every quote, backtick and backslash removed (regardless of quoting): `"source"`,
    `so''urce`, `\\source` and `rea\\<newline>d` all read as the plain word."""
    return _CONT.sub("", text).translate(_DEQUOTE)


_STMT = re.compile(r"[^;&|\n(){}]+")
_FUNC_DEF = re.compile(r"[\w.:+-]\s{0,64}\(\s{0,64}\)|(?:^|[\s;&|(){}])function\s")
_DEFAULT_SET = re.compile(r"\$\{" + _IDENT + r":?=")
_ARRAY_SET = re.compile(r"(?<![\w$])" + _IDENT + r"(?:\[[^\]\n]{0,200}\]\+?=|\+?=\()")
_DOLLAR_NAME_SET = re.compile(r"\$\{?" + _IDENT + r"\}?\+?=(?!=)")
_ASSIGN_WORD = re.compile(_IDENT + r"(?:\[[^\]]{0,200}\])?\+?=")
_PREFIX_WORDS = frozenset(("then", "do", "else", "elif", "if", "while", "until", "!", "time", "coproc", "builtin",
                           "command", "exec", "nohup", "sudo"))
_OPTION_TAKERS = frozenset(("builtin", "command", "exec", "time"))
_POISON_COMMANDS = frozenset(("trap", "eval", "source", ".", "read", "readarray", "mapfile", "getopts", "declare",
                              "typeset", "local", "readonly", "unset", "let"))
_PRINTF_V = re.compile(r"-[A-Za-z]{0,8}v")
_WAIT_P = re.compile(r"-[A-Za-z]{0,8}p")
_PS_VAR_CMDLET = re.compile(r"(?<![\w-])(?:set|new|get|clear|remove)-variable(?![\w-])|(?<![\w$-])(?:sv|nv|gv|clv|rv)"
                            r"(?![\w-])", re.IGNORECASE)
_PS_WORDS = re.compile(r"variable:|\[ref\]|psvariable|sessionstate|invokescript|\.value\s{0,64}=(?!=)"
                       r"|(?<![\w-])(?:iex|invoke-expression)(?![\w-])|\$\{?(?:script|global|private|using|local):"
                       r"|=\s{0,64}\{|(?:^|[\s;|(){}=])[&.]\s{0,64}[${]", re.IGNORECASE)
_PS_QUOTED_CALL = re.compile("(?:^|[\\s;|(){}=])[&.]\\s{0,64}['\"\u2018\u2019\u201a\u201b\u201c\u201d\u201e]")
_PS_PARAM = re.compile(r"(?<![\w-])-([A-Za-z]{2,32})(?![\w-])")
_PS_VAR_PARAMS = ("outvariable", "errorvariable", "warningvariable", "informationvariable", "pipelinevariable")
_PS_VAR_ALIASES = frozenset(("ov", "ev", "wv", "iv", "pv"))


def _command_word(tokens):
    """(the command word, the words after it) of one statement: leading keywords, prefix words, their options and
    assignment words skipped."""
    k, after_taker = 0, False
    while k < len(tokens):
        t = tokens[k]
        if t in _PREFIX_WORDS:
            after_taker = t in _OPTION_TAKERS
        elif after_taker and t.startswith("-"):
            pass
        elif _ASSIGN_WORD.match(t):
            after_taker = False
        else:
            break
        k += 1
    return (tokens[k], tokens[k + 1:]) if k < len(tokens) else ("", [])


# guard3 (v4.0.0 review LOW-5, user-approved precision): an eval of ONE variable (`eval "$c"`, `iex $c`,
# `Invoke-Expression -Command $c`, `$c | iex`) runs the text that variable holds. When that text is a same-command
# literal, every write it makes is spelled in the command (write_counts counts it), so it is no poison. poisoned()
# with `evals` (a list) DEFERS these statements, appending one name per statement; shellparse then proves each one is
# a certain literal holding no poison itself, or turns resolution off ("eval") exactly as before.
_EVAL_VAR = re.compile(r"\$\{?(" + _IDENT + r")\}?")
_PS_IEX_VAR = re.compile(r"(?:^|(?<=[\s;|(){}]))(?:iex|invoke-expression)[ \t]{1,64}(?:-c[a-z]{0,6}[ \t]{1,64})?"
                         r"\$\{?(" + _IDENT + r")\}?(?=[ \t]{0,64}(?:$|[;\n)}]))", re.IGNORECASE | re.MULTILINE)
_PS_PIPE_IEX = re.compile(r"(?:^|(?<=[\s;(){}]))\$\{?(" + _IDENT + r")\}?[ \t]{0,64}\|[ \t]{0,64}"
                          r"(?:iex|invoke-expression)(?=[ \t]{0,64}(?:$|[;\n)}]))", re.IGNORECASE | re.MULTILINE)


def poisoned(text: str, dialect: str, evals=None) -> str:
    """The first poison class found in the command (see POLICY), or "" when resolution may run. cmd: always.
    evals: a list to defer eval-of-one-variable statements into (see _EVAL_VAR above); None poisons them."""
    if dialect not in ("bash", "powershell"):
        return "cmd"
    t = normalize(text)
    if dialect == "powershell" and evals is not None:
        for rx in (_PS_IEX_VAR, _PS_PIPE_IEX):
            evals += [m.group(1).lower() for m in rx.finditer(t)]
            t = rx.sub(" ", t)
    if dialect == "bash":
        if _FUNC_DEF.search(t):
            return "function"
        for rx, why in ((_DEFAULT_SET, "default-assign"), (_ARRAY_SET, "array"), (_DOLLAR_NAME_SET, "indirect")):
            if rx.search(t):
                return why
    else:
        if _PS_VAR_CMDLET.search(t):
            return "variable-cmdlet"
        if _PS_WORDS.search(t):
            return "powershell-scope"
        if _PS_QUOTED_CALL.search(_CONT.sub("", text)):
            return "quoted-call"
        for m in _PS_PARAM.finditer(t):
            p = m.group(1).lower()
            if p in _PS_VAR_ALIASES or any(v.startswith(p) for v in _PS_VAR_PARAMS):
                return "variable-parameter"
    if dialect == "bash":
        for p in _PIECE.finditer(t):
            piece = p.group()
            i = piece.find("((")
            if i >= 0 and "=" in piece[i:]:
                return "arithmetic"                         # (( D = 1 )), $(( D = 1 )), let-like
    for s in _STMT.finditer(t):
        word, rest = _command_word(s.group().split())
        w = word.lower() if dialect == "powershell" else word
        if w == ".":
            return "dot-source"
        if dialect != "bash":
            continue
        if w == "eval" and evals is not None and len(rest) == 1 and _EVAL_VAR.fullmatch(rest[0]):
            evals.append(_EVAL_VAR.fullmatch(rest[0]).group(1))
            continue
        if w in _POISON_COMMANDS:
            return w
        if w == "export" and any("$" in r for r in rest):
            return "export"
        if w == "printf" and any(_PRINTF_V.match(r) for r in rest):
            return "printf-v"
        if w == "wait" and any(_WAIT_P.match(r) for r in rest):
            return "wait-p"
    return ""


# ------------------------------------------------------------------------------------------------- values
_DQ_ESC = '$`"\\'


def bash_value(raw: str) -> Optional[str]:
    """The literal a bash assignment stores for the right-hand side `raw`; None when it is not a plain literal
    (an expansion, a substitution, an unquoted `~` (tilde expansion) or backslash, a `$'..'` string)."""
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
            j = i + 1
            while True:
                if j >= n:
                    return None
                d = raw[j]
                if d == '"':
                    break
                if d in "$`":
                    return None
                if d == "\\" and j + 1 < n and (raw[j + 1] in _DQ_ESC or raw[j + 1] == "\n"):
                    if raw[j + 1] != "\n":
                        out.append(raw[j + 1])
                    j += 2
                    continue
                out.append(d)
                j += 1
            i = j + 1
        elif c in "$`\\~(){}" or c.isspace():
            return None
        else:
            out.append(c)
            i += 1
    return "".join(out)


_PS_ASSIGN = re.compile(r"""[ \t]*\$(""" + _IDENT + r""")[ \t]*=[ \t]*(?:'((?:[^'‘’‚‛]|'')*)'"""
                        r"""|"([^"`$“”„]*)")[ \t\r]*""")


def ps_assignment(segment: str) -> Optional[Tuple[str, str]]:
    """(name, value) when the statement is exactly `$name = 'literal'` / `$name = "literal"`; else None."""
    m = _PS_ASSIGN.fullmatch(segment)
    if not m:
        return None
    if m.group(2) is not None:
        return m.group(1), m.group(2).replace("''", "'")
    return m.group(1), m.group(3)


# ------------------------------------------------------------------------------------------------- uses
_NAME_AT = re.compile(_IDENT)
_UNSAFE_UNQUOTED = re.compile(r"[\s*?\[\]{}]")          # word splitting / globbing would change an unquoted use


def _var_at(raw: str, i: int) -> Tuple[Optional[str], int]:
    """`raw[i] == "$"`: the plain name of `$NAME` or `${NAME}` and the index after it; (None, _) otherwise."""
    if raw.startswith("{", i + 1):
        m = _NAME_AT.match(raw, i + 2)
        if m and raw.startswith("}", m.end()):
            return m.group(), m.end() + 1
        return None, i
    m = _NAME_AT.match(raw, i + 1)
    if m:
        return m.group(), m.end()
    return None, i


def _bash_word(raw: str, lookup: Callable[[str], Optional[str]], split_safe: bool) -> Optional[str]:
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
            j = i + 1
            while True:
                if j >= n:
                    return None
                d = raw[j]
                if d == '"':
                    break
                if d == "`":
                    return None
                if d == "$":
                    name, k = _var_at(raw, j)
                    v = lookup(name) if name else None
                    if v is None:
                        return None
                    out.append(v)
                    j = k
                    continue
                if d == "\\" and j + 1 < n and (raw[j + 1] in _DQ_ESC or raw[j + 1] == "\n"):
                    if raw[j + 1] != "\n":
                        out.append(raw[j + 1])
                    j += 2
                    continue
                out.append(d)
                j += 1
            i = j + 1
        elif c == "$":
            name, k = _var_at(raw, i)
            v = lookup(name) if name else None
            if v is None or v == "" or _UNSAFE_UNQUOTED.search(v) or not split_safe:
                return None                                     # split or globbed: not the one path it reads as
            out.append(v)
            i = k
        elif c in "`\\" or (c == "~" and i == 0):
            return None
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _ps_word(raw: str, lookup: Callable[[str], Optional[str]]) -> Optional[str]:
    if any(q in raw for q in "‘’‚‛“”„`"):
        return None
    out, i, n = [], 0, len(raw)
    while i < n:
        c = raw[i]
        if c == "'":
            j = i + 1
            while True:
                k = raw.find("'", j)
                if k < 0:
                    return None
                if raw.startswith("'", k + 1):
                    out.append(raw[j:k] + "'")
                    j = k + 2
                    continue
                out.append(raw[j:k])
                i = k + 1
                break
        elif c == '"':
            j = i + 1
            while True:
                if j >= n:
                    return None
                d = raw[j]
                if d == '"':
                    if raw.startswith('"', j + 1):
                        out.append('"')
                        j += 2
                        continue
                    break
                if d == "$":
                    name, k = _var_at(raw, j)
                    v = lookup(name) if name and not raw.startswith(":", k) else None
                    if v is None:
                        return None
                    out.append(v)
                    j = k
                    continue
                out.append(d)
                j += 1
            i = j + 1
        elif c == "$":
            name, k = _var_at(raw, i)
            v = lookup(name) if name and not raw.startswith(":", k) else None
            if v is None:
                return None
            out.append(v)
            i = k
        elif c in "(){}@[":
            return None
        else:
            out.append(c)
            i += 1
    return "".join(out)


def expand_word(raw: str, dialect: str, lookup: Callable[[str], Optional[str]], split_safe: bool = True) -> Optional[str]:
    """`raw` (a word's source text) with every $NAME / ${NAME} replaced by lookup(NAME); None when any expansion
    is not a plain resolvable name, or the dialect never resolves (cmd). split_safe=False (bash: the command
    writes IFS) refuses every UNQUOTED expansion, whose word splitting the guard can no longer predict."""
    if dialect == "bash":
        return _bash_word(raw, lookup, split_safe)
    if dialect == "powershell":
        return _ps_word(raw, lookup)
    return None
