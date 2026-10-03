r"""shellparse: what a shell command line EXECUTES -- bash, PowerShell and cmd.

    parse(command, shell="") -> Parsed(execs, ok, why)

Pure (no filesystem, no environment), stdlib only, Python 3.8.

WHY A PARSER. The legacy rules ask "does the TEXT mention rm and a protected path". That over-blocks text (a
commit message, a heredoc to python) and under-blocks executed code the regex never anticipated. Here every
simple command that RUNS becomes an Exec, including the ones hidden in `$( )`, backticks, `<( )`, `bash -c` /
`pwsh -Command` / `cmd /c` strings, `eval` / `Invoke-Expression`, heredocs fed to a shell, `xargs`,
`find -exec` / `-delete`, PowerShell script blocks and `[IO.File]::` calls. Rules then ask about programs and
arguments, not about text.

FAIL CLOSED (R4). ok=False whenever the text cannot be read with confidence: an unterminated quote, heredoc,
`$(`, `(` or `{`; a stray `)`; more than MAX_TOKENS tokens; nesting deeper than MAX_DEPTH; an undecodable
-EncodedCommand. A rule must then apply the legacy co-occurrence checks -- never allow. The execs found before
the failure are still returned (a partial answer), but ok=False means "this list may be incomplete".
parse() NEVER raises: an internal error is reported as ok=False, why="internal error: ...".

UNKNOWN DIALECT (R5). shell="" parses as bash AND PowerShell; the execs are unioned and ok is the AND.

UNQUOTED BACKSLASH (R6). In bash `rm C:\GOG\x` really runs on `C:GOGx`, but the intent (and the next attempt)
is the Windows path. A bash simple command with an unquoted backslash yields TWO Execs: the bash reading and the
literal reading. The stricter verdict wins because rules see both. (The literal reading does not re-parse
`-c` strings or heredocs a second time; that keeps nesting linear rather than 2^depth.)

HEREDOCS (R7). A heredoc's consumer decides what its body is: `stdin_from` is "heredoc:shell" (bash, sh, zsh,
dash, ksh, pwsh, powershell, cmd reading commands from stdin -- the body is PARSED and its commands are
execs), "heredoc:interpreter" (python, node, perl, ...: the body is code the legacy text rules check; it is in
`stdin_body`), or "heredoc:data" (anything else: `cat > f <<EOF` is a write to f, the body is data). A
pipeline into a bare shell (`cat <<EOF | bash`, `echo 'cmd' | sh`) parses the upstream bodies/arguments as
commands too. Unquoted-delimiter bodies are scanned for `$( )` and backticks, which bash executes.

LINEAR. One pass per nesting level with an explicit frame stack (no recursion over `(` / `$(` / `{` nesting);
the only recursion is re-parsing a string that is itself a command line (backticks, -c strings, eval,
shell-fed heredocs, find -exec), bounded by MAX_DEPTH. Every regex is a single character-class run or a
bounded literal. A hook that answers after its timeout has its answer DROPPED (fail open), so
tests/test_skyguard_timing.py holds this at 20 KB and 80 KB.

AN EXEC:
  program    normalised: basename, lower-case, .exe/.cmd/.bat/.ps1/.sh stripped, dialect aliases mapped
             (PowerShell `ri` -> remove-item, cmd `erase` -> del). "?" when the program word is dynamic and
             its last path component is not literal (`$R x`, `$(which rm) x`). "" for a statement that runs
             no program (a redirect-only command `> f`, a PowerShell expression `'x' > f`).
             Synthetic programs: "find.delete" (argv = find's start paths), "io.file.write", "io.file.append",
             "io.file.delete", "io.file.move", "io.file.copy", "io.directory.delete", "io.directory.move"
             (argv = the call's arguments).
  argv       the ARGUMENTS after the program (argv[0] is the first argument, not the program), unquoted.
  quoted     per argv word: True if any part of it was quoted.
  redirects  ((op, target), ...): op keeps its fd prefix ("2>", ">>", "&>", "*>"); "<<<" bodies are in stdin_body.
  dialect    "bash" | "powershell" | "cmd".
  cd_before  the directory an earlier `cd`/`pushd`/`Set-Location` in the same statement chain moved to
             ("" if none, "?" if it cannot be known: `cd -`, `cd $X`, `popd`). Relative cds are joined with "/".
             A cd inside a pipeline stage or a subshell does not carry out of it.
  stdin_from "pipe" | "heredoc:shell" | "heredoc:interpreter" | "heredoc:data" | "".
  segment    the source text of this simple command (for the legacy fallback and messages' basenames only).
  stdin_body the heredoc / here-string body when stdin_from starts with "heredoc:" (an addition to the
             Task 5 interface: R7(b) needs the interpreter's code).

KNOWN LIMITS -- each gives a partial answer, never a "safe" one:
  - a program held in a variable (`$R reset-havok`): program "?", argv kept; rules must treat "?" as opaque.
  - a quote-split token (`--ap""ply`, `r''m`): RESOLVED -- words are unquoted and concatenated as bash does.
  - process substitution `<( )` / `>( )`: parsed as commands; the word itself is dynamic.
  - `$(( ))` arithmetic and `${ }` expansions are skipped; a `$( )` INSIDE them is not parsed.
  - aliases and shell functions defined in the same command are not expanded (a function's BODY is parsed).
  - PowerShell method calls other than [IO.File]/[IO.Directory] (`$item.Delete()`) are not execs; a script
    block or $( ) passed to one IS parsed.
  - a here-string `@" "@` is data; `$( )` inside it is not parsed.
  - `env -S STR` and `xargs sh -c` with a dynamic string: program/argv as given, the string is not split.
"""
from __future__ import annotations

import base64
import re
from typing import List, NamedTuple, Optional, Tuple

MAX_TOKENS = 2000
MAX_DEPTH = 8


class Exec(NamedTuple):
    program: str
    argv: Tuple[str, ...]
    quoted: Tuple[bool, ...]
    redirects: Tuple[Tuple[str, str], ...]
    dialect: str
    cd_before: str
    stdin_from: str
    segment: str
    stdin_body: str = ""


class Parsed(NamedTuple):
    execs: Tuple[Exec, ...]
    ok: bool
    why: str


class ParseError(Exception):
    """The text cannot be read with confidence: parse() returns ok=False with this message as `why`."""


# ----------------------------------------------------------------------------------------------------------
# Program names
# ----------------------------------------------------------------------------------------------------------
_EXTS = (".exe", ".cmd", ".bat", ".ps1", ".sh")
PS_ALIASES = {
    "ri": "remove-item", "rm": "remove-item", "del": "remove-item", "erase": "remove-item",
    "rd": "remove-item", "rmdir": "remove-item",
    "mi": "move-item", "mv": "move-item", "move": "move-item",
    "cpi": "copy-item", "cp": "copy-item", "copy": "copy-item",
    "sc": "set-content", "ac": "add-content", "clc": "clear-content", "ni": "new-item",
    "sl": "set-location", "cd": "set-location", "chdir": "set-location",
    "pushd": "push-location", "popd": "pop-location",
    "iex": "invoke-expression", "icm": "invoke-command", "saps": "start-process", "start": "start-process",
    "%": "foreach-object", "foreach": "foreach-object", "?": "where-object", "where": "where-object",
    "rni": "rename-item", "ren": "rename-item", "rp": "remove-itemproperty", "si": "set-item",
    "sp": "set-itemproperty", "ii": "invoke-item", "tee": "tee-object",
    "gci": "get-childitem", "ls": "get-childitem", "dir": "get-childitem",
    "gc": "get-content", "cat": "get-content", "type": "get-content",
    "echo": "write-output", "write": "write-output",
}
CMD_ALIASES = {"erase": "del", "rd": "rmdir", "md": "mkdir", "ren": "rename", "chdir": "cd"}
POSIX_SHELLS = frozenset(("bash", "sh", "zsh", "dash", "ksh", "ash", "mksh"))
PS_SHELLS = frozenset(("pwsh", "powershell", "pwsh-preview"))
ALL_SHELLS = POSIX_SHELLS | PS_SHELLS | {"cmd"}
INTERPRETERS = frozenset((
    "node", "nodejs", "deno", "bun", "perl", "ruby", "php", "lua", "luajit", "rscript", "julia", "tclsh",
    "wish", "osascript", "jshell", "groovy", "ts-node", "tsx", "jython"))
_PYTHON = re.compile(r"(?:python|pypy|py)[0-9.]{0,8}w?")
_SET_LOCATION = frozenset(("set-location", "push-location"))


def _dialect(shell: str) -> Optional[str]:
    s = (shell or "").strip().lower()
    if s.endswith(".exe"):
        s = s[:-4]
    if s in ("bash", "sh", "zsh", "dash", "ksh", "git-bash", "gitbash", "posix"):
        return "bash"
    if s in ("powershell", "pwsh", "ps", "ps1"):
        return "powershell"
    if s in ("cmd", "batch", "bat"):
        return "cmd"
    return None


def _basename(word: str) -> str:
    cut = max(word.rfind("/"), word.rfind("\\"))
    return word[cut + 1:]


def program_name(word: str, dialect: str, dynamic: bool = False) -> str:
    """The normalised program a command word names (see the module docstring)."""
    b = _basename(word)
    if dynamic and (not b or "$" in b or "`" in b or "(" in b or "%" in b):
        return "?"
    b = b.lower()
    for ext in _EXTS:
        if b.endswith(ext) and len(b) > len(ext):
            b = b[:-len(ext)]
            break
    if dialect == "powershell":
        return PS_ALIASES.get(b, b)
    if dialect == "cmd":
        if b.startswith("@"):
            b = b[1:]
        b = b.rstrip(".")                                       # `del.` runs del
        return CMD_ALIASES.get(b, b)
    return b


def consumer_kind(program: str) -> str:
    """What a heredoc fed to `program` is: "shell" (commands), "interpreter" (code) or "data"."""
    if program in ALL_SHELLS:
        return "shell"
    if program in INTERPRETERS or _PYTHON.fullmatch(program):
        return "interpreter"
    return "data"


def _shell_dialect(program: str) -> str:
    if program in PS_SHELLS:
        return "powershell"
    if program == "cmd":
        return "cmd"
    return "bash"


# PowerShell named parameters (prefix-abbreviated, as PowerShell allows).
PS_SWITCHES = ("recurse", "force", "whatif", "confirm", "verbose", "debug", "passthru", "nonewline", "append",
               "noclobber", "asjob", "wait", "nonewwindow", "raw", "container", "directory", "file", "hidden",
               "readonly", "system", "followsymlink", "usetransaction", "asstring", "asbytestream")


def ps_param(ex: Exec, name: str, position: Optional[int] = None) -> Tuple[str, ...]:
    """Values of PowerShell parameter `name` in `ex.argv`: `-Name v`, `-Na v` (any prefix), `-Name:v`; plus the
    `position`-th positional argument (0-based) when given. A word `-x` that is a prefix of a known switch takes
    no value; any other `-x` is assumed to take one."""
    want = name.lower()
    out, pos, k, argv = [], [], 0, ex.argv
    while k < len(argv):
        w, q = argv[k], ex.quoted[k]
        if w.startswith("-") and len(w) > 1 and not q:
            key, _, attached = w[1:].partition(":")
            key = key.lower()
            if key and want.startswith(key):
                if attached:
                    out.append(attached)
                elif k + 1 < len(argv):
                    out.append(argv[k + 1])
                    k += 1
            elif attached or any(s.startswith(key) for s in PS_SWITCHES):
                pass
            elif k + 1 < len(argv) and not (argv[k + 1].startswith("-") and not ex.quoted[k + 1]):
                k += 1                                    # another parameter's value
            k += 1
            continue
        pos.append(w)
        k += 1
    if position is not None and position < len(pos):
        out.append(pos[position])
    return tuple(out)


# ----------------------------------------------------------------------------------------------------------
# Shared scanner state
# ----------------------------------------------------------------------------------------------------------
class _Ctx:
    __slots__ = ("tokens", "out", "frame_ids")

    def __init__(self):
        self.tokens = 0
        self.out: List[Exec] = []
        self.frame_ids = 0

    def tick(self, k: int = 1) -> None:
        self.tokens += k
        if self.tokens > MAX_TOKENS:
            raise ParseError("more than %d tokens" % MAX_TOKENS)


class _Word:
    __slots__ = ("b", "l", "quoted", "dyn", "bs", "start", "raw")

    def __init__(self, start: int):
        self.b: List[str] = []
        self.l: List[str] = []
        self.quoted = False
        self.dyn = False
        self.bs = False
        self.start = start
        self.raw = ""

    def add(self, s: str, lit: Optional[str] = None) -> None:
        self.b.append(s)
        self.l.append(s if lit is None else lit)


class _Here:
    __slots__ = ("delim", "strip", "quoted", "body")

    def __init__(self, delim: str, strip: bool, quoted: bool, body: Optional[str]):
        self.delim, self.strip, self.quoted, self.body = delim, strip, quoted, body


class _Cmd:
    __slots__ = ("words", "redirs", "heredocs", "start", "end", "cd", "key", "stage", "skip", "redir_op",
                 "drop_next", "call", "frame", "after_time", "coproc")

    def __init__(self, f, start: int):
        self.words: List[_Word] = []
        self.redirs: List[Tuple[str, str]] = []
        self.heredocs: List[_Here] = []
        self.start = start
        self.end = start
        self.cd = f.cd
        self.key = (f.id, f.pipe)
        self.stage = f.stage
        self.skip = False
        self.redir_op: Optional[str] = None
        self.drop_next = False
        self.call = False
        self.frame = f.id
        self.after_time = False
        self.coproc = False


class _Frame:
    __slots__ = ("kind", "cmd", "word", "mode", "cd", "pipe", "stage", "case", "dbr", "start", "id", "last_sep",
                 "io", "rec_mark")

    def __init__(self, kind: str, cd: str, mode: str, fid: int, start: int = 0):
        self.kind = kind
        self.cmd: Optional[_Cmd] = None
        self.word: Optional[_Word] = None
        self.mode = mode
        self.cd = cd
        self.pipe = 0
        self.stage = 0
        self.case: List[str] = []
        self.dbr = False
        self.start = start
        self.id = fid
        self.last_sep = ""
        self.io = ""
        self.rec_mark = 0


_HOME_ONLY = re.compile(r"(?:\$HOME|\$\{HOME\}|\$USERPROFILE|\$\{USERPROFILE\}|\$env:USERPROFILE|\$env:HOME"
                        r"|%USERPROFILE%)(?:[/\\][^$`%]{0,4096})?", re.IGNORECASE)
_DRIVE_ABS = re.compile(r"[A-Za-z]:[/\\]")


def _is_abs(p: str) -> bool:
    return p[:1] in ("/", "\\", "~", "$", "%") or bool(_DRIVE_ABS.match(p))


def _join_cd(prev: str, new: str) -> str:
    if new == "?":
        return "?"
    if not prev or _is_abs(new):
        return new
    if prev == "?":
        return "?"
    return prev.rstrip("/\\") + "/" + new


def _cd_value(w: _Word, lit: bool) -> str:
    v = "".join(w.l if lit else w.b)
    if v == "-":
        return "?"
    if w.dyn and not _HOME_ONLY.fullmatch(v):
        return "?"
    return v


class _Scanner:
    """Common frame/word/command bookkeeping. Subclasses implement run()."""
    DIALECT = ""

    def __init__(self, text: str, depth: int, cd: str, ctx: _Ctx, mode: str = "n"):
        self.t = text
        self.n = len(text)
        self.depth = depth
        self.ctx = ctx
        self.recs: List[_Cmd] = []
        self.synth: List[Tuple[int, Exec]] = []
        self.frames = [self._frame("top", cd, mode, 0)]
        self.pending: List[_Here] = []
        self.i = 0
        self._pmatch: Optional[dict] = None

    def _frame(self, kind, cd, mode, start):
        self.ctx.frame_ids += 1
        return _Frame(kind, cd, mode, self.ctx.frame_ids, start)

    def _word(self, f: _Frame) -> _Word:
        if f.word is None:
            f.word = _Word(self.i)
        return f.word

    def _cmd(self, f: _Frame, start: int) -> _Cmd:
        if f.cmd is None:
            f.cmd = _Cmd(f, start)
        return f.cmd

    def _push(self, f: _Frame, kind: str, start: int, word: bool) -> _Frame:
        if len(self.frames) + self.depth > MAX_DEPTH:
            raise ParseError("nesting deeper than %d" % MAX_DEPTH)
        if word:
            self._word(f)
        g = self._frame(kind, f.cd, "n", start)
        g.rec_mark = len(self.recs)
        self.frames.append(g)
        return g

    def _pairs(self) -> dict:
        """Matching ( ) positions over the whole text, ignoring quotes (one linear pass, computed once)."""
        if self._pmatch is None:
            m, stack, t = {}, [], self.t
            for j, c in enumerate(t):
                if c == "(":
                    stack.append(j)
                elif c == ")" and stack:
                    m[stack.pop()] = j
            self._pmatch = m
        return self._pmatch

    def end_word(self, f: _Frame) -> None:
        w = f.word
        if w is None:
            return
        f.word = None
        w.raw = self.t[w.start:self.i]
        self.ctx.tick()
        cmd = self._cmd(f, w.start)
        if cmd.redir_op is not None:
            op, cmd.redir_op = cmd.redir_op, None
            if op.endswith("<<<"):
                cmd.heredocs.append(_Here("", False, True, "".join(w.b)))
            else:
                cmd.redirs.append((op, "".join(w.b)))
            return
        if self.keyword(f, cmd, w):
            return
        cmd.words.append(w)

    def keyword(self, f: _Frame, cmd: _Cmd, w: _Word) -> bool:
        return False

    def end_cmd(self, f: _Frame, sep: str) -> None:
        self.end_word(f)
        cmd = f.cmd
        if cmd is not None:
            f.cmd = None
            if cmd.redir_op is not None:
                raise ParseError("a redirection without a target")
            cmd.end = self.i
            if not cmd.skip and (cmd.words or cmd.redirs or cmd.heredocs):
                self.recs.append(cmd)
                if sep not in ("|", "|&") and cmd.stage == 0:
                    self.track_cd(f, cmd)
        if sep in ("|", "|&"):
            f.stage += 1
        elif sep:
            f.pipe += 1
            f.stage = 0
        f.last_sep = sep

    def track_cd(self, f: _Frame, cmd: _Cmd) -> None:
        pass


# ----------------------------------------------------------------------------------------------------------
# bash
# ----------------------------------------------------------------------------------------------------------
_BASH_PLAIN = re.compile(r"[^ \t\r\n\f\v'\"\\$`;&|()<>]+")
_DQ_PLAIN = re.compile(r'[^"\\$`]+')
_HD_PLAIN = re.compile(r"[^\\$`]+")
_HDELIM_PLAIN = re.compile(r"[^ \t\r\n;&|()<>'\"\\]+")
_VAR = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,255}|[0-9?$!#*@-]")
_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,255}(?:\[[^\]\n]{0,200}\])?\+?=")
_BASH_BLANK = " \t\r\f\v"
_BASH_DROP = frozenset(("if", "then", "else", "elif", "fi", "do", "done", "while", "until", "{", "}", "!"))
_VARNAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,255}")
# `(( ... ))` / `$(( ... ))` whose body reads as `word -opt` or `word word`: bash takes it as arithmetic and runs
# nothing, but another reading (or a typo) runs a command. The parser cannot be sure, so it says so.
_CMDLIKE = re.compile(r"[\s(]*[A-Za-z_./][A-Za-z0-9_./-]{0,255}[ \t]+(?:-[A-Za-z]|[A-Za-z_.])")
_SUBST_START = re.compile(r"\$\(|`|[<>]\(")
_ANSI = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f",
         "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}
_BASH_REDIR_OPS = ("<<<", "<<-", "<<", "<>", "<&", "<", ">>", ">&", ">|", ">")
_FD_WORD = re.compile(r"[0-9]{1,3}|\{[A-Za-z_][A-Za-z0-9_]{0,63}\}")
_FUNC_NAME = re.compile(r"[A-Za-z0-9_:.+-]{1,256}")


class _Bash(_Scanner):
    DIALECT = "bash"

    def keyword(self, f, cmd, w):
        plain = not w.quoted and not w.dyn and not w.bs
        v = "".join(w.b)
        if f.case and f.case[-1] in ("head", "pattern"):
            if plain and f.case[-1] == "head" and v == "in":
                f.case[-1] = "pattern"
            elif plain and f.case[-1] == "pattern" and v == "esac":
                f.case.pop()
            cmd.skip = True
            return True
        if f.dbr:
            if plain and v == "]]":
                f.dbr = False
            cmd.skip = True
            return True
        if cmd.words or cmd.skip:
            return False
        if cmd.drop_next:
            cmd.drop_next = False
            return True
        after_time, cmd.after_time = cmd.after_time, False
        coproc, cmd.coproc = cmd.coproc, False
        if not plain:
            return False
        if after_time and v in ("-p", "--"):
            cmd.after_time = True
            return True
        if coproc and _VARNAME.fullmatch(v):                    # coproc NAME { ... } / coproc NAME ( ... )
            j = self.i
            while j < self.n and self.t[j] in " \t":
                j += 1
            if j < self.n and self.t[j] in "{(":
                return True
        if v == "time":                                         # time [-p] <pipeline>, incl. { } and ( )
            cmd.after_time = True
            return True
        if v == "coproc":
            cmd.coproc = True
            return True
        if v in _BASH_DROP:
            return True
        if v == "function":
            cmd.drop_next = True
            return True
        if v in ("for", "select"):
            cmd.skip = True
            return True
        if v == "case":
            f.case.append("head")
            cmd.skip = True
            return True
        if v == "esac":
            if f.case:
                f.case.pop()
            return True
        if v == "[[":
            f.dbr = True
            cmd.skip = True
            return True
        return False

    def track_cd(self, f, cmd):
        ws = cmd.words
        k = 0
        while k < len(ws) and _ASSIGN.match(ws[k].raw):
            k += 1
        if k >= len(ws) or ws[k].quoted or ws[k].dyn:
            return
        p = "".join(ws[k].b)
        if p not in ("cd", "pushd", "popd"):
            return
        args = [w for w in ws[k + 1:] if not ("".join(w.b).startswith("-") and len("".join(w.b)) > 1)]
        if p == "popd":
            new = "?"
        elif not args:
            new = "~"
        else:
            new = _cd_value(args[0], args[0].bs)
        f.cd = _join_cd(f.cd, new)

    # -- the main loop
    def run(self) -> None:
        t, n = self.t, self.n
        if self.frames[0].mode == "hd":
            self._word(self.frames[0])
        while self.i < n:
            f = self.frames[-1]
            if f.mode != "n":
                self._quoted(f)
                continue
            c = t[self.i]
            if c in _BASH_BLANK:
                self.end_word(f)
                self.i += 1
                continue
            if c == "\n":
                self._newline(f)
                continue
            if c == "#" and f.word is None:                     # bash comment
                j = t.find("\n", self.i)
                self.i = n if j < 0 else j
                continue
            if f.dbr and c in "&|<>()":
                self.end_word(f)
                self.i += 1
                continue
            m = _BASH_PLAIN.match(t, self.i)
            if m:
                self._word(f).add(m.group())
                self.i = m.end()
                continue
            if c == "\\":
                self._backslash(f)
            elif c == "'":
                j = t.find("'", self.i + 1)
                if j < 0:
                    raise ParseError("unterminated ' quote")    # bash single quote
                w = self._word(f)
                w.quoted = True
                w.add(t[self.i + 1:j])
                self.i = j + 1
            elif c == '"':
                self._word(f).quoted = True
                f.mode = "dq"
                self.i += 1
            elif c == "$":
                self._dollar(f)
            elif c == "`":
                self._backtick(f)
            elif c in ";&|":
                self._operator(f, c)
            elif c == "(":
                self._open(f)
            elif c == ")":
                self._close(f)
            else:  # < >
                self._redirect(f)
        self._finish()

    def _backslash(self, f):
        t, i = self.t, self.i
        if i + 1 >= self.n:
            self._word(f).add("\\")
            self.i += 1
            return
        nx = t[i + 1]
        if nx == "\n":
            self.i += 2
            return
        if nx == "\r" and t.startswith("\n", i + 2):
            self.i += 3
            return
        w = self._word(f)
        w.add(nx, "\\" + nx)
        w.bs = True
        self.i += 2

    def _quoted(self, f):
        """One step inside a double-quoted part ("dq") or an unquoted-delimiter heredoc body ("hd")."""
        t, i = self.t, self.i
        w = self._word(f)
        m = (_DQ_PLAIN if f.mode == "dq" else _HD_PLAIN).match(t, i)
        if m:
            w.add(m.group())
            self.i = m.end()
            return
        c = t[i]
        if c == '"':  # only reachable in dq
            f.mode = "n"
            self.i += 1
        elif c == "\\":
            nx = t[i + 1] if i + 1 < self.n else ""
            if nx == "\n":
                self.i += 2
            elif nx and nx in ('$`"\\' if f.mode == "dq" else "$`\\"):
                w.add(nx)
                self.i += 2
            else:
                w.add("\\")
                self.i += 1
        elif c == "$":
            self._dollar(f)
        else:
            self._backtick(f)

    def _dollar(self, f):
        t, i, n = self.t, self.i, self.n
        nx = t[i + 1] if i + 1 < n else ""
        w = self._word(f)
        if nx == "(":
            if t.startswith("((", i + 1):
                pm = self._pairs()
                k = pm.get(i + 1)
                if k is not None and pm.get(i + 2) == k - 1:        # $(( ... )) arithmetic
                    self._skipped(f, i + 3, k - 1, True)
                    w.add(t[i:k + 1])
                    w.dyn = True
                    self.i = k + 1
                    return
            self._push(f, "subst", i, True)                     # bash $( ... ): commands, parsed in place
            self.i = i + 2
            return
        if nx == "{":
            end = self._brace_end(i + 2)
            self._skipped(f, i + 2, end - 1, False)
            w.add(t[i:end])
            w.dyn = True
            self.i = end
            return
        if f.mode == "n" and nx == "'":
            self._ansi(f, w)
            return
        if f.mode == "n" and nx == '"':
            w.quoted = True
            f.mode = "dq"
            self.i = i + 2
            return
        m = _VAR.match(t, i + 1)
        if m:
            w.add(t[i:m.end()])
            w.dyn = True
            self.i = m.end()
            return
        w.add("$")
        self.i = i + 1

    def _skipped(self, f, a, b, arith):
        """t[a:b] is a span the scanner does not read as commands (`$(( ))`, `(( ))`, `${ }`, `a=( )`, `@( )`).
        A `$( )`, backtick or `<( )` inside it still RUNS, so each one is parsed (only those: the rest is an
        expression, not a command line); an arithmetic body that reads as a command is reported as unsure.
        Never a silent skip. Linear: each substitution is visited once and skipped over."""
        t = self.t
        j, found = a, False
        while True:
            m = _SUBST_START.search(t, j, b)                    # skipped-span substitutions are parsed
            if m is None:
                break
            found = True
            s = m.start()
            if t[s] == "`":
                e = t.find("`", s + 1, b)
                if e < 0:
                    raise ParseError("an unterminated ` inside a $(( )), (( )), ${ } or ( ) span")
                _sub(t[s + 1:e], "bash", self.depth + 1, f.cd, self.ctx)
            else:
                e = self._pairs().get(s + 1)
                if e is None or e >= b:
                    raise ParseError("an unbalanced $( inside a $(( )), (( )), ${ } or ( ) span")
                _sub(t[s + 2:e], "bash", self.depth + 1, f.cd, self.ctx)
            j = e + 1
        if not found and arith and _CMDLIKE.match(t, a, b):
            raise ParseError("a (( )) whose body reads as a command")

    def _brace_end(self, j):
        """End (exclusive) of a ${ ... } expansion starting after `${`, nested braces counted."""
        t, n, depth = self.t, self.n, 1
        while j < n:
            c = t[j]
            if c == "\\":
                j += 2
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        raise ParseError("unterminated ${")

    def _ansi(self, f, w):
        t, n = self.t, self.n
        j = self.i + 2
        out = []
        while j < n:
            c = t[j]
            if c == "'":
                w.quoted = True
                w.add("".join(out))
                self.i = j + 1
                return
            if c == "\\" and j + 1 < n:
                out.append(_ANSI.get(t[j + 1], t[j + 1]))
                j += 2
                continue
            out.append(c)
            j += 1
        raise ParseError("unterminated $' quote")

    def _backtick(self, f):
        t, n = self.t, self.n
        j = self.i + 1
        parts = []
        while j < n:
            c = t[j]
            if c == "\\" and j + 1 < n:
                nx = t[j + 1]
                parts.append(nx if nx in "$`\\" else "\\" + nx)
                j += 2
                continue
            if c == "`":
                break
            parts.append(c)
            j += 1
        else:
            raise ParseError("unterminated ` substitution")
        w = self._word(f)
        w.add(t[self.i:j + 1])
        w.dyn = True
        self.i = j + 1
        _sub("".join(parts), "bash", self.depth + 1, f.cd, self.ctx)

    def _operator(self, f, c):
        t, i = self.t, self.i
        if c == ";":
            op = ";;&" if t.startswith(";;&", i) else ";;" if t.startswith(";;", i) else ";&" if t.startswith(";&", i) else ";"
            self.end_cmd(f, op)
            self.ctx.tick()
            self.i += len(op)
            if op != ";":
                if f.case and f.case[-1] == "body":
                    f.case[-1] = "pattern"
                else:
                    raise ParseError("unexpected " + op)
            return
        if c == "&":
            if t.startswith("&>", i):
                op = "&>>" if t.startswith("&>>", i) else "&>"
                self.end_word(f)
                self.ctx.tick()
                cmd = self._cmd(f, i)
                if cmd.redir_op is not None:
                    raise ParseError("two redirections in a row")
                cmd.redir_op = op
                self.i += len(op)
                return
            op = "&&" if t.startswith("&&", i) else "&"
        else:
            if f.case and f.case[-1] == "pattern":
                self.end_word(f)
                self.i += 1
                return
            op = "||" if t.startswith("||", i) else "|&" if t.startswith("|&", i) else "|"
        self.end_cmd(f, op)
        self.ctx.tick()
        self.i += len(op)

    def _newline(self, f):
        if f.cmd is None and f.word is None and f.last_sep in ("|", "|&", "&&", "||"):
            pass                                                # the list continues on the next line
        else:
            self.end_cmd(f, "\n")
        self.i += 1
        if self.pending:
            self._read_bodies()

    def _open(self, f):
        t, i = self.t, self.i
        if f.case and f.case[-1] == "pattern":
            self.end_word(f)
            self.i += 1
            return
        w = f.word
        if w is None:
            cmd = f.cmd
            if cmd is not None and cmd.words:
                j = i + 1
                while j < self.n and t[j] in " \t":
                    j += 1
                if len(cmd.words) == 1 and not cmd.redirs and j < self.n and t[j] == ")":
                    cmd.words = []                              # name () -- a function definition
                    self.i = j + 1
                    return
                raise ParseError("unexpected (")
            if t.startswith("((", i):
                pm = self._pairs()
                k = pm.get(i)
                if k is not None and pm.get(i + 1) == k - 1:   # (( ... )) arithmetic command
                    self._skipped(f, i + 2, k - 1, True)
                    self._cmd(f, i).skip = True
                    self.i = k + 1
                    return
            self._push(f, "paren", i, False)
            self.i = i + 1
            return
        cmd = f.cmd
        if (cmd is None or not cmd.words) and not w.quoted and not w.dyn and _FUNC_NAME.fullmatch(t, w.start, i):
            j = i + 1
            while j < self.n and t[j] in " \t":
                j += 1
            if j < self.n and t[j] == ")":                      # name() -- a function definition
                f.word = None
                self.i = j + 1
                return
        if _ASSIGN.fullmatch(t, w.start, i) or t[i - 1] in "@!+*?":   # a=( ... ) array / extglob @( ... )
            k = self._pairs().get(i)
            if k is None:
                raise ParseError("unbalanced (")
            self._skipped(f, i + 1, k, False)
            w.add(t[i:k + 1])
            w.dyn = True
            self.i = k + 1
            return
        raise ParseError("unexpected (")

    def _close(self, f):
        if f.case and f.case[-1] == "pattern":
            self.end_word(f)                                    # `esac)` ends the case HERE: then ) closes a frame
            if f.case and f.case[-1] == "pattern":
                f.cmd = None
                f.case[-1] = "body"
                self.i += 1
                return
        if f.kind == "top":
            raise ParseError("unbalanced )")
        self.end_cmd(f, ")")
        if f.case or f.dbr:
            raise ParseError("unterminated case or [[ inside ( )")
        self.frames.pop()
        p = self.frames[-1]
        if f.kind in ("subst", "proc"):
            w = self._word(p)
            w.add(self.t[f.start:self.i + 1])
            w.dyn = True
        self.i += 1

    def _redirect(self, f):
        t, i = self.t, self.i
        if t.startswith("(", i + 1):                            # <( ... ) / >( ... ) process substitution
            self.end_word(f)
            self._push(f, "proc", i, True)
            self.i = i + 2
            return
        fd = ""
        w = f.word
        if w is not None:
            if not w.quoted and not w.dyn and not w.bs and _FD_WORD.fullmatch(t, w.start, i):
                fd = t[w.start:i]
                f.word = None
            else:
                self.end_word(f)
        op = next(o for o in _BASH_REDIR_OPS if t.startswith(o, i))
        self.i = i + len(op)
        self.ctx.tick()
        cmd = self._cmd(f, i)
        if cmd.redir_op is not None:
            raise ParseError("two redirections in a row")
        if op in ("<<", "<<-"):
            self._heredoc(cmd, op == "<<-")
            return
        cmd.redir_op = fd + op

    def _heredoc(self, cmd, strip):
        t, n = self.t, self.n
        j = self.i
        while j < n and t[j] in " \t":
            j += 1
        parts, quoted = [], False
        while j < n:
            c = t[j]
            if c in " \t\r\n;&|()<>":
                break
            if c in "'\"":
                k = t.find(c, j + 1)
                if k < 0:
                    raise ParseError("unterminated heredoc delimiter quote")
                parts.append(t[j + 1:k])
                quoted = True
                j = k + 1
                continue
            if c == "\\":
                quoted = True
                if j + 1 < n:
                    parts.append(t[j + 1])
                j += 2
                continue
            m = _HDELIM_PLAIN.match(t, j)
            parts.append(m.group())
            j = m.end()
        delim = "".join(parts)
        if not delim and not quoted:
            raise ParseError("a heredoc without a delimiter")
        h = _Here(delim, strip, quoted, None)
        cmd.heredocs.append(h)
        self.pending.append(h)
        self.i = j

    def _read_bodies(self):
        t, n = self.t, self.n
        pos = self.i
        for h in self.pending:
            start = pos
            while True:
                if pos >= n:
                    raise ParseError("unterminated heredoc (no line '%s')" % h.delim[:40])
                e = t.find("\n", pos)
                end = n if e < 0 else e
                line = t[pos:end].rstrip("\r")
                if h.strip:
                    line = line.lstrip("\t")
                if line == h.delim:
                    h.body = t[start:pos]
                    pos = end + 1
                    break
                pos = end + 1
            if not h.quoted and ("$(" in h.body or "`" in h.body):
                _sub(h.body, "bash", self.depth + 1, self.frames[-1].cd, self.ctx, mode="hd")
        self.pending = []
        self.i = min(pos, n)

    def _finish(self):
        f = self.frames[-1]
        if f.mode == "dq":
            raise ParseError('unterminated " quote')            # bash double quote
        if len(self.frames) > 1:
            raise ParseError("unbalanced ( or $( or <(")
        if f.mode == "hd":
            f.word = None
            f.cmd = None
            return
        self.end_cmd(f, "")
        if self.pending:
            raise ParseError("unterminated heredoc")
        if f.case:
            raise ParseError("unterminated case")
        if f.dbr:
            raise ParseError("unterminated [[")


# ----------------------------------------------------------------------------------------------------------
# PowerShell
# ----------------------------------------------------------------------------------------------------------
_SQ = "'\u2018\u2019\u201a\u201b"
_DQ = '"\u201c\u201d\u201e'
_PS_BLANK = " \t\r\f\v\u00a0"
_PS_PLAIN = re.compile("[^ \\t\\r\\n\\f\\v\u00a0'\"`$#(){};|&<>,@\u2018\u2019\u201a\u201b\u201c\u201d\u201e]+")
_PS_DQ_PLAIN = re.compile('[^"`$\u201c\u201d\u201e]+')
_PS_SQ_RUN = re.compile("[^'\u2018\u2019\u201a\u201b]*")
_PS_VAR = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,255}(?::[A-Za-z_][A-Za-z0-9_]{0,255})?|[$?^_]")
_PS_DROP = frozenset(("if", "elseif", "else", "foreach", "for", "while", "do", "until", "switch", "try",
                      "catch", "finally", "trap", "return", "throw", "exit", "break", "continue", "begin",
                      "process", "end", "dynamicparam", "data", "param", "using", "{", "}"))
_PS_NAMED = frozenset(("function", "filter", "workflow", "class", "enum", "configuration"))
_PS_ESC = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "a": "\a", "b": "\b", "f": "\f", "v": "\v", "e": "\x1b"}
_PS_REDIR_OPS = (">>", ">&", ">", "<")
_PS_FD = re.compile(r"[0-9*]")
_IO_CALL = re.compile(r"\[(?:System\.)?IO\.(File|Directory)\]::([A-Za-z]{1,40})", re.IGNORECASE)
_PS_ASSIGN_OPS = frozenset(("=", "+=", "-=", "*=", "/=", "%=", "??="))
_PS_METHOD = re.compile(r"(?:\.|::)([A-Za-z]{1,40})$")
_PS_OPAQUE_METHODS = frozenset(("delete", "moveto", "copyto", "invoke", "invokereturnasis", "addscript", "start",
                                "create"))
_PS_DASHES = "–—―"                                 # PowerShell takes these as a parameter dash


def _io_program(cls: str, method: str) -> str:
    cls, m = cls.lower(), method.lower()
    if cls == "file":
        if m.startswith("write") or m in ("create", "createtext", "openwrite"):
            return "io.file.write"
        if m.startswith("append"):
            return "io.file.append"
        if m in ("delete", "move", "copy", "replace"):
            return "io.file." + m
        return ""
    if m in ("delete", "move"):
        return "io.directory." + m
    return ""


class _Pwsh(_Scanner):
    DIALECT = "powershell"

    def keyword(self, f, cmd, w):
        if cmd.words or cmd.skip:
            return False
        if cmd.drop_next:
            cmd.drop_next = False
            return True
        if w.quoted or w.dyn:
            return False
        v = "".join(w.b).lower()
        if v == "." and not cmd.call:
            cmd.call = True                                       # dot-source: the next word is the script
            return True
        if v in _PS_DROP:
            return True
        if v in _PS_NAMED:
            cmd.drop_next = True
            return True
        return False

    def track_cd(self, f, cmd):
        if not cmd.words or cmd.words[0].quoted or cmd.words[0].dyn:
            return
        p = program_name("".join(cmd.words[0].b), "powershell")
        if p == "pop-location":
            f.cd = "?"
            return
        if p not in _SET_LOCATION:
            return
        ex = _exec_of("powershell", [("".join(w.b), w.quoted, w.dyn) for w in cmd.words[1:]], p)
        vals = ps_param(ex, "Path", 0) or ps_param(ex, "LiteralPath")
        if not vals:
            new = "~"
        else:
            src = [w for w in cmd.words[1:] if "".join(w.b) == vals[0]]
            new = _cd_value(src[0], False) if src else vals[0]
        f.cd = _join_cd(f.cd, new)

    def run(self) -> None:
        t, n = self.t, self.n
        while self.i < n:
            f = self.frames[-1]
            if f.mode == "dq":
                self._dq(f)
                continue
            c = t[self.i]
            if c in _PS_BLANK:
                self.end_word(f)
                self.i += 1
                continue
            if c == "\n":
                if f.cmd is None and f.word is None and f.last_sep in ("|", "&&", "||"):
                    pass
                else:
                    self.end_cmd(f, "\n")
                self.i += 1
                continue
            if c == "#" and f.word is None:
                j = t.find("\n", self.i)
                self.i = n if j < 0 else j
                continue
            if c == "<" and t.startswith("<#", self.i):
                j = t.find("#>", self.i + 2)
                if j < 0:
                    raise ParseError("unterminated <# comment")
                self.i = j + 2
                continue
            m = _PS_PLAIN.match(t, self.i)
            if m:
                self._word(f).add(m.group())
                self.i = m.end()
                continue
            if c == "`":
                self._tick_escape(f)
            elif c in _SQ:
                self._single(f)
            elif c in _DQ:
                self._word(f).quoted = True
                f.mode = "dq"
                self.i += 1
            elif c == "$":
                self._dollar(f)
            elif c == "@":
                self._at(f)
            elif c == "(":
                self._open(f)
            elif c == "{":
                self.end_word(f)
                self._push(f, "block", self.i, True)
                self.i += 1
            elif c in ")}":
                self._close(f, c)
            elif c in ";|&":
                self._operator(f, c)
            elif c in "<>":
                self._redirect(f)
            elif c == ",":
                self.end_word(f)
                self.i += 1
            else:                                               # '#' inside a word
                self._word(f).add(c)
                self.i += 1
        f = self.frames[-1]
        if f.mode == "dq":
            raise ParseError('unterminated " quote')
        if len(self.frames) > 1:
            raise ParseError("unbalanced ( $( @( { or @{")
        self.end_cmd(f, "")

    def _tick_escape(self, f):
        t, i = self.t, self.i
        nx = t[i + 1] if i + 1 < self.n else ""
        if nx == "\n":
            self.i += 2
        elif nx == "\r" and t.startswith("\n", i + 2):
            self.i += 3
        elif nx:
            w = self._word(f)
            w.add(nx)
            w.quoted = True
            self.i += 2
        else:
            self.i += 1

    def _single(self, f):
        t, n = self.t, self.n
        j = self.i + 1
        parts = []
        while True:
            m = _PS_SQ_RUN.match(t, j)
            parts.append(m.group())
            j = m.end()
            if j >= n:
                raise ParseError("unterminated ' quote")
            if j + 1 < n and t[j + 1] in _SQ:                   # '' is a literal quote
                parts.append("'")
                j += 2
                continue
            break
        w = self._word(f)
        w.quoted = True
        w.add("".join(parts))
        self.i = j + 1

    def _dq(self, f):
        t, i = self.t, self.i
        w = self._word(f)
        m = _PS_DQ_PLAIN.match(t, i)
        if m:
            w.add(m.group())
            self.i = m.end()
            return
        c = t[i]
        if c in _DQ:
            if i + 1 < self.n and t[i + 1] in _DQ:
                w.add('"')
                self.i += 2
            else:
                f.mode = "n"
                self.i += 1
        elif c == "`":
            nx = t[i + 1] if i + 1 < self.n else ""
            w.add(_PS_ESC.get(nx, nx))
            self.i += 2 if nx else 1
        else:
            self._dollar(f)

    def _dollar(self, f):
        t, i, n = self.t, self.i, self.n
        nx = t[i + 1] if i + 1 < n else ""
        w = self._word(f)
        if nx == "(":
            self._push(f, "subst", i, True)                     # PowerShell $( ... ) subexpression
            self.i = i + 2
            return
        if nx == "{":
            j = t.find("}", i + 2)
            if j < 0:
                raise ParseError("unterminated ${")
            w.add(t[i:j + 1])
            w.dyn = True
            self.i = j + 1
            return
        m = _PS_VAR.match(t, i + 1)
        if m:
            w.add(t[i:m.end()])
            w.dyn = True
            self.i = m.end()
            return
        w.add("$")
        self.i = i + 1

    def _at(self, f):
        t, i, n = self.t, self.i, self.n
        nx = t[i + 1] if i + 1 < n else ""
        if nx == "(":
            self._push(f, "subst", i, True)
            self.i = i + 2
            return
        if nx == "{":
            self._push(f, "hash", i, True)
            self.i = i + 2
            return
        if nx and (nx in _SQ or nx in _DQ):
            j = i + 2
            while j < n and t[j] in " \t":
                j += 1
            if j < n and t[j] in "\r\n":                         # @' ... '@ here-string
                close = "\n" + ("'" if nx in _SQ else '"') + "@"
                k = t.find(close, j)
                if k < 0:
                    raise ParseError("unterminated here-string")
                if nx in _DQ and "$(" in t[j:k]:                # a @" "@ subexpression runs; not parsed here
                    raise ParseError('a $( ) inside a @" "@ here-string')
                w = self._word(f)
                w.quoted = True
                w.add(t[j + 1:k].lstrip("\n"))
                self.i = k + len(close)
                return
        self._word(f).add("@")
        self.i = i + 1

    def _open(self, f):
        i = self.i
        w = f.word
        io = ""
        if w is not None:
            m = _IO_CALL.fullmatch(self.t, w.start, i)
            if m:
                io = _io_program(m.group(1), m.group(2))
            else:
                tail = _PS_METHOD.search(self.t, max(w.start, i - 64), i)   # bounded: linear
                if tail and tail.group(1).lower() in _PS_OPAQUE_METHODS:
                    # .Delete() / .MoveTo() / [scriptblock]::Create().Invoke(): something runs or is deleted,
                    # and the parser cannot say what -- an opaque exec, never a silent nothing.
                    self.synth.append((len(self.recs), Exec(
                        "?", (tail.group(1),), (False,), (), "powershell", f.cd, "", self.t[w.start:i])))
        g = self._push(f, "call" if w is not None else "paren", i, True)
        g.io = io
        self.i = i + 1

    def _close(self, f, c):
        want = ("subst", "paren", "call") if c == ")" else ("block", "hash")
        if f.kind not in want:
            raise ParseError("unbalanced " + c)
        self.end_cmd(f, c)
        self.frames.pop()
        p = self.frames[-1]
        if f.kind in ("hash", "call"):
            mine = [r for r in self.recs[f.rec_mark:] if r.frame == f.id]
            if f.kind == "call" and f.io:
                args = [w for r in mine for w in r.words]
                vals = tuple("".join(w.b) for w in args)
                self.synth.append((len(self.recs), Exec(
                    f.io, vals, tuple(w.quoted for w in args), (), "powershell", f.cd, "",
                    self.t[p.word.start if p.word else f.start:self.i + 1])))
            # A hashtable's entries and a method call's arguments are data, never commands; a command INSIDE one
            # (`$( )`, `( )`, `{ }`) belongs to its own nested frame and is kept.
            drop = set(id(r) for r in mine)
            self.recs[f.rec_mark:] = [r for r in self.recs[f.rec_mark:] if id(r) not in drop]
        w = self._word(p)
        w.add(self.t[f.start:self.i + 1])
        if f.kind != "block":
            w.dyn = True
        self.i += 1

    def _operator(self, f, c):
        t, i = self.t, self.i
        if c == ";":
            op = ";"
        elif c == "|":
            op = "||" if t.startswith("||", i) else "|"
        else:
            if t.startswith("&&", i):
                op = "&&"
            elif f.word is None and (f.cmd is None or not f.cmd.words):
                self._cmd(f, i).call = True                    # & call operator
                self.ctx.tick()
                self.i += 1
                return
            else:
                op = "&"
        self.end_cmd(f, op)
        self.ctx.tick()
        self.i += len(op)

    def _redirect(self, f):
        t, i = self.t, self.i
        fd = ""
        w = f.word
        if w is not None:
            if not w.quoted and not w.dyn and _PS_FD.fullmatch(t, w.start, i):
                fd = t[w.start:i]
                f.word = None
            else:
                self.end_word(f)
        op = next(o for o in _PS_REDIR_OPS if t.startswith(o, i))
        self.i = i + len(op)
        self.ctx.tick()
        cmd = self._cmd(f, i)
        if cmd.redir_op is not None:
            raise ParseError("two redirections in a row")
        cmd.redir_op = fd + op


# ----------------------------------------------------------------------------------------------------------
# cmd
# ----------------------------------------------------------------------------------------------------------
_CMD_PLAIN = re.compile(r'[^ \t\r\n\f\v",;=^&|<>()%]+')
_CMD_BLANK = " \t\r\f\v,;="
_CMD_VAR = re.compile(r"%%|%[^%\s]{1,256}%|%~?[A-Za-z0-9*]")
_CMD_REM = re.compile(r"(?:rem(?=[ \t\r\n]|$)|::)", re.IGNORECASE)
_CMD_REDIR_OPS = (">>", ">&", "<&", ">", "<")
_CMD_REM_END = re.compile(r"[&|\n]")
_CMD_CMP = frozenset(("equ", "neq", "lss", "leq", "gtr", "geq"))


class _Cmdexe(_Scanner):
    DIALECT = "cmd"

    def track_cd(self, f, cmd):
        if not cmd.words:
            return
        p = program_name("".join(cmd.words[0].b), "cmd")
        if p in ("cd", "pushd"):
            args = [w for w in cmd.words[1:] if "".join(w.b).lower() != "/d"]
            f.cd = _join_cd(f.cd, _cd_value(args[0], False) if args else "?")
        elif p == "popd":
            f.cd = "?"

    def run(self) -> None:
        t, n = self.t, self.n
        while self.i < n:
            f = self.frames[-1]
            c = t[self.i]
            if f.word is None and (f.cmd is None or not f.cmd.words):
                m = _CMD_REM.match(t, self.i)
                if m:                       # a comment; whether `&` ends it is not certain, so assume it does
                    e = _CMD_REM_END.search(t, self.i)
                    self.i = n if e is None else e.start()
                    continue
            if c == "=" and t.startswith("==", self.i):       # if a==b cmd: `==` is a token of its own
                self.end_word(f)
                self._word(f).add("==")
                self.i += 2
                self.end_word(f)
                continue
            if c in _CMD_BLANK:
                self.end_word(f)
                self.i += 1
                continue
            if c == "\n":
                self.end_cmd(f, "\n")
                self.i += 1
                continue
            m = _CMD_PLAIN.match(t, self.i)
            if m:
                self._word(f).add(m.group())
                self.i = m.end()
                continue
            if c == '"':
                j = t.find('"', self.i + 1)
                if j < 0:
                    raise ParseError('unterminated " quote')
                w = self._word(f)
                w.quoted = True
                w.add(t[self.i + 1:j])
                self.i = j + 1
            elif c == "^":
                nx = t[self.i + 1] if self.i + 1 < n else ""
                if nx not in ("\n", ""):
                    self._word(f).add(nx)
                self.i += 2
            elif c == "%":
                m = _CMD_VAR.match(t, self.i)
                w = self._word(f)
                if m:
                    w.add(m.group())
                    w.dyn = m.group() != "%%"
                    self.i = m.end()
                else:
                    w.add("%")
                    self.i += 1
            elif c == "(":
                cmd = f.cmd
                first = "".join(cmd.words[0].b).lower() if cmd is not None and cmd.words else ""
                last = "".join(cmd.words[-1].b).lower() if first else ""
                if first == "for" and last == "in" and f.word is None:   # for %f in ( set ) do ( body )
                    k = self._pairs().get(self.i)
                    if k is None:
                        raise ParseError("unbalanced (")
                    self._word(f).add(t[self.i:k + 1])
                    self.i = k + 1
                elif f.word is None:
                    self.end_cmd(f, "(")
                    self._push(f, "paren", self.i, False)
                    self.i += 1
                else:
                    self._word(f).add("(")
                    self.i += 1
            elif c == ")":
                if f.kind == "paren":
                    self.end_cmd(f, ")")
                    self.frames.pop()
                else:
                    self._word(f).add(")")
                self.i += 1
            elif c in "&|":
                op = t[self.i:self.i + 2] if t.startswith(c * 2, self.i) else c
                self.end_cmd(f, op)
                self.ctx.tick()
                self.i += len(op)
            else:
                fd = ""
                w = f.word
                if w is not None:
                    if not w.quoted and not w.dyn and _FD_WORD.fullmatch(t, w.start, self.i):
                        fd = t[w.start:self.i]
                        f.word = None
                    else:
                        self.end_word(f)
                op = next(o for o in _CMD_REDIR_OPS if t.startswith(o, self.i))
                self.i += len(op)
                self.ctx.tick()
                cmd = self._cmd(f, self.i)
                if cmd.redir_op is not None:
                    raise ParseError("two redirections in a row")
                cmd.redir_op = fd + op
        if len(self.frames) > 1:
            raise ParseError("unbalanced (")
        self.end_cmd(self.frames[-1], "")


# ----------------------------------------------------------------------------------------------------------
# From simple commands to Execs
# ----------------------------------------------------------------------------------------------------------
def _exec_of(dialect: str, words, program: str) -> Exec:
    return Exec(program, tuple(w[0] for w in words), tuple(w[1] for w in words), (), dialect, "", "", "")


_SUDO_ARG = frozenset(("-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T", "--user", "--group"))
_XARGS_ARG = frozenset(("-I", "-n", "-P", "-L", "-l", "-s", "-E", "-d", "-a", "--arg-file", "--delimiter",
                        "--max-args", "--max-procs", "--max-lines", "--max-chars", "--replace", "--eof"))
_TIMEOUT_ARG = frozenset(("-s", "-k", "--signal", "--kill-after"))
# Programs that run the rest of their words as a command: name -> (options that take a value, positional words
# before the command). `watch` and `wsl` run the words through a shell, so those are re-parsed as one string.
_WRAPPERS = {
    "ionice": (frozenset(("-c", "-n", "-p", "-P", "-u", "--class", "--classdata", "--pid", "--pgid", "--uid")), 0),
    "chroot": (frozenset(("--userspec", "--groups")), 1),
    "busybox": (frozenset(), 0),
    "strace": (frozenset(("-o", "-e", "-p", "-s", "-u", "-E", "-a", "-b", "-I", "-O", "-S", "-X", "-P")), 0),
    "ltrace": (frozenset(("-o", "-e", "-p", "-s", "-u", "-a", "-n")), 0),
    "unbuffer": (frozenset(), 0),
    "setsid": (frozenset(), 0),
    "taskset": (frozenset(), 1),
    "flock": (frozenset(("-w", "--timeout", "-E", "--conflict-exit-code")), 1),
    "watch": (frozenset(("-n", "--interval", "-q", "--equexit")), 0),
}
_WSL_ARG = frozenset(("-d", "--distribution", "-u", "--user", "--cd", "--shell-type"))
_PARALLEL_SEP = frozenset((":::", "::::", ":::+", "::::+"))
_PARALLEL_ARG = frozenset(("-j", "--jobs", "-n", "-N", "-S", "--sshlogin", "-a", "--arg-file", "-d", "--delimiter",
                           "--colsep", "-C", "--joblog", "--results", "--timeout", "-I", "--replace", "-E"))
_SAPS_SWITCHES = ("wait", "passthru", "nonewwindow", "usenewenvironment", "loaduserprofile", "whatif", "confirm")
_CMD_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\.?")
_PS_VALUE_PARAMS = ("executionpolicy", "ep", "ex", "workingdirectory", "wd", "configurationname", "config",
                    "custompipename", "inputformat", "outputformat", "of", "windowstyle", "version",
                    "settingsfile", "encodedarguments", "psconsolefile")


class _Resolver:
    """Turns one simple command's words into Execs: wrappers unwrapped, -c strings and heredocs re-parsed."""

    def __init__(self, dialect, vals, quoted, dyn, redirs, cd, stdin, seg, bodies, depth, ctx, recurse, call):
        self.d = dialect
        self.v = vals
        self.q = quoted
        self.y = dyn
        self.redirs = tuple(redirs)
        self.cd = cd
        self.stdin = stdin
        self.seg = seg
        self.bodies = bodies          # [(body, delimiter_quoted)] in order; the last one is stdin
        self.depth = depth
        self.ctx = ctx
        self.recurse = recurse
        self.call = call
        self.made: List[Exec] = []

    def emit(self, program: str, k: int) -> Exec:
        stdin, body = self.stdin, ""
        if self.bodies:
            stdin = "heredoc:" + consumer_kind(program)
            body = self.bodies[-1][0]
        e = Exec(program, tuple(self.v[k:]), tuple(self.q[k:]), self.redirs, self.d, self.cd, stdin, self.seg,
                 body)
        self.ctx.out.append(e)
        self.made.append(e)
        if self.bodies and program in ALL_SHELLS and self.recurse:
            for b, _ in self.bodies:
                _sub(b, _shell_dialect(program), self.depth + 1, self.cd, self.ctx)
        elif program in ALL_SHELLS and any(op in ("<", "0<") for op, _ in self.redirs):
            self.opaque(k)                              # `sh < s.sh`, `sh < <(...)`: commands never seen
        return e

    def opaque(self, k: int) -> Exec:
        """Something runs and the parser cannot say what: an explicit "?" exec, never a silent nothing."""
        e = Exec("?", tuple(self.v[k:]), tuple(self.q[k:]), self.redirs, self.d, self.cd, self.stdin, self.seg)
        self.ctx.out.append(e)
        self.made.append(e)
        return e

    def _reparse_after_c(self, k: int) -> bool:
        """`su ... -c CMD`, `script -c CMD`, `--command=CMD`: re-parse CMD; True if one was found."""
        v, n = self.v, len(self.v)
        for j in range(k, n):
            if v[j] in ("-c", "--command") and j + 1 < n:
                self.reparse(v[j + 1], "bash")
                return True
            if v[j].startswith("--command="):
                self.reparse(v[j][len("--command="):], "bash")
                return True
        return False

    def reparse(self, text: str, dialect: str) -> None:
        if self.recurse:
            _sub(text, dialect, self.depth + 1, self.cd, self.ctx)

    def run(self, k: int = 0) -> List[Exec]:
        v, n = self.v, len(self.v)
        first = True
        while True:
            if k >= n:
                if self.redirs or self.bodies:
                    self.emit("", n)
                return self.made
            if self.d == "cmd" and not self.q[k] and "/" in v[k][1:]:
                head, _, rest = v[k].partition("/")
                if _CMD_NAME.fullmatch(head):                    # rd/s/q X: cmd ends a command name at `/`
                    sw = ["/" + s for s in rest.split("/") if s]
                    self.v = v = list(v[:k]) + [head] + sw + list(v[k + 1:])
                    self.q = list(self.q[:k]) + [False] * (1 + len(sw)) + list(self.q[k + 1:])
                    self.y = list(self.y[:k]) + [False] * (1 + len(sw)) + list(self.y[k + 1:])
                    n = len(v)
            p = program_name(v[k], self.d, self.y[k])
            if p == "?" and k + 1 < n and v[k + 1].lower() in ("/c", "/k", "/r", "//c", "//k"):
                self.opaque(k)                                  # %COMSPEC% /c ..., & $env:ComSpec /c ...
                return self._cmd_shell(k)
            if first and self.d == "powershell":
                first = False
                if not self.call and k + 1 < n and self.y[k] and v[k + 1] in _PS_ASSIGN_OPS:
                    k += 2                                     # $x = <statement>
                    if k >= n:
                        continue
                    p = program_name(v[k], self.d, self.y[k])
                if v[k][:1] == "{" or not self.call and (
                        self.q[k] or (self.y[k] and v[k][:1] in "$(@") or v[k][:1] in "(["):
                    if self.redirs:                            # an expression or a script block, not a command
                        self.emit("", n)                       # (a block's commands were parsed in place)
                    return self.made
            first = False
            if p in ("sudo", "doas"):
                k += 1
                while k < n and v[k].startswith("-") and v[k] != "--":
                    k += 2 if v[k] in _SUDO_ARG else 1
                if k < n and v[k] == "--":
                    k += 1
                continue
            if p == "env" and self.d == "bash":
                k += 1
                while k < n:
                    w = v[k]
                    split = (v[k + 1] if k + 1 < n else "") if w in ("-S", "--split-string") else \
                        w[len("--split-string="):] if w.startswith("--split-string=") else \
                        w[2:] if w.startswith("-S") and len(w) > 2 else None
                    if split is not None:                       # env -S 'cmd args': the string IS the command
                        rest = v[k + 2:] if w in ("-S", "--split-string") else v[k + 1:]
                        self.reparse(" ".join([split] + list(rest)), "bash")
                        return self.made
                    if w in ("-u", "--unset", "-C", "--chdir"):
                        if w in ("-C", "--chdir") and k + 1 < n:
                            self.cd = _join_cd(self.cd, v[k + 1])
                        k += 2
                    elif w.startswith("-") or _ASSIGN.match(w):
                        k += 1
                    else:
                        break
                continue
            if p in ("command", "builtin") and self.d == "bash":
                j = k + 1
                while j < n and v[j] in ("-p", "--"):
                    j += 1
                if j < n and v[j] in ("-v", "-V"):
                    self.emit(p, k + 1)                         # a lookup, not an execution
                    return self.made
                k = j
                continue
            if p == "exec" and self.d == "bash":
                k += 1
                while k < n and v[k].startswith("-"):
                    k += 2 if v[k] == "-a" else 1
                continue
            if p in ("nohup", "winpty", "chronic") or (p == "time" and self.d == "bash"):
                k += 1
                while k < n and v[k] in ("-p", "--"):
                    k += 1
                continue
            if p == "stdbuf":
                k += 1
                while k < n and v[k].startswith("-"):
                    k += 2 if v[k] in ("-i", "-o", "-e") else 1
                continue
            if p == "nice":
                k += 1
                if k < n and v[k] == "-n":
                    k += 2
                elif k < n and (re.fullmatch(r"-[0-9]{1,3}", v[k]) or v[k].startswith("--adjustment")):
                    k += 1
                continue
            if p in _WRAPPERS:
                argopts, positional = _WRAPPERS[p]
                k += 1
                while k < n and v[k].startswith("-") and v[k] != "--":
                    if p == "flock" and v[k] in ("-c", "--command"):
                        break
                    k += 2 if v[k] in argopts else 1
                if k < n and v[k] == "--":
                    k += 1
                k += positional
                if p == "flock" and self._reparse_after_c(k):  # flock FILE -c CMD
                    return self.made
                if p == "watch":                                # watch joins its words and runs `sh -c` on them
                    if k < n:
                        self.reparse(" ".join(v[k:]), "bash")
                    return self.made
                continue
            if p == "wsl":                                      # the words run in Linux: bash, whatever the caller
                k0 = k
                k += 1
                while k < n and v[k].startswith("-") and v[k] != "--":
                    if v[k] in ("-e", "--exec", "--system"):
                        k += 1
                    elif v[k] in _WSL_ARG:
                        k += 2
                    else:                                       # --import, --list, --shutdown ...: no command
                        self.emit(p, k0 + 1)
                        return self.made
                if k < n and v[k] == "--":
                    k += 1
                if k >= n:
                    self.emit(p, k0 + 1)
                    return self.made
                if self.depth + 1 > MAX_DEPTH:
                    raise ParseError("nesting deeper than %d" % MAX_DEPTH)
                self.made += _Resolver("bash", list(v[k:]), list(self.q[k:]), list(self.y[k:]), self.redirs,
                                       self.cd, self.stdin, self.seg, self.bodies, self.depth + 1, self.ctx,
                                       self.recurse, False).run()
                return self.made
            if p in ("su", "script"):
                if not self._reparse_after_c(k + 1):
                    self.emit(p, k + 1)
                return self.made
            if p == "trap" and self.d == "bash":
                self.emit(p, k + 1)
                h = k + 1 + (k + 1 < n and v[k + 1] == "--")
                if h < n and not v[h].startswith("-"):
                    self.reparse(v[h], "bash")                  # the handler runs on the signal
                return self.made
            if p == "alias" and self.d == "bash":
                self.emit(p, k + 1)
                for w in v[k + 1:]:
                    if "=" in w:
                        self.reparse(w.split("=", 1)[1], "bash")  # an alias body runs where the alias is used
                return self.made
            if p == "parallel":
                k += 1
                while k < n and v[k].startswith("-") and v[k] not in _PARALLEL_SEP:
                    k += 2 if v[k] in _PARALLEL_ARG else 1
                end = k
                while end < n and v[end] not in _PARALLEL_SEP:
                    end += 1
                if end == k:                                    # commands come from stdin or a file
                    self.opaque(k)
                    return self.made
                if self.depth + 1 > MAX_DEPTH:
                    raise ParseError("nesting deeper than %d" % MAX_DEPTH)
                tail = [j for j in range(end, n) if v[j] not in _PARALLEL_SEP]
                r = _Resolver(self.d, list(v[k:end]) + [v[j] for j in tail], list(self.q[k:end]) +
                              [self.q[j] for j in tail], list(self.y[k:end]) + [self.y[j] for j in tail], (),
                              self.cd, "pipe", self.seg, [], self.depth + 1, self.ctx, self.recurse, False)
                self.made += r.run()
                return self.made
            if p == "timeout" and self.d == "bash":
                k += 1
                while k < n and v[k].startswith("-"):
                    k += 2 if v[k] in _TIMEOUT_ARG else 1
                k += 1                                          # the duration
                continue
            if p == "xargs":
                k += 1
                self.stdin = "pipe"
                while k < n and v[k].startswith("-"):
                    k += 2 if v[k] in _XARGS_ARG else 1
                if k >= n:
                    self.emit("echo", n)
                    return self.made
                continue
            if p in ("source", ".") and self.d == "bash" or (p == "." and self.d == "powershell"):
                k += 1
                if k >= n:
                    self.emit(p, k)
                    return self.made
                self.emit(program_name(v[k], self.d, self.y[k]), k + 1)
                return self.made
            if p == "eval" and self.d == "bash":
                self.emit(p, k + 1)
                if k + 1 < n:
                    self.reparse(" ".join(v[k + 1:]), "bash")
                return self.made
            if p in POSIX_SHELLS:
                return self._posix_shell(p, k)
            if p in PS_SHELLS:
                return self._ps_shell(p, k)
            if p == "cmd":
                return self._cmd_shell(k)
            if p == "find" and self.d == "bash":
                return self._find(k)
            if self.d == "powershell":
                if p == "invoke-expression":
                    self.emit(p, k + 1)
                    ex = self.made[-1]
                    lits = ps_param(ex, "Command", 0)
                    for s in lits:
                        self.reparse(s, "powershell")
                    if not lits or any(self.y[k + 1:]):        # iex $cmd, iex ('a' + 'b'): unknown text runs
                        self.opaque(k + 1)
                    return self.made
                if p == "start-process":
                    return self._start_process(p, k)
            if self.d == "cmd":
                if p in ("call", "start"):
                    k += 1
                    if p == "start":
                        if k < n and self.q[k]:
                            k += 1                              # the window title
                        while k < n and v[k].startswith("/"):
                            k += 2 if v[k].lower() == "/d" else 1
                    continue
                if p == "if":
                    k += 1
                    while k < n and v[k].lower() in ("/i", "not"):
                        k += 1
                    if k < n and v[k].lower() in ("exist", "defined", "errorlevel", "cmdextversion"):
                        k += 2
                    elif k + 1 < n and v[k + 1].lower() in _CMD_CMP:
                        k += 3
                    elif k + 1 < n and v[k + 1] == "==":
                        k += 3
                    else:
                        k += 1                                  # a==b
                    continue
                if p == "else":
                    k += 1
                    continue
                if p == "for":
                    j = k + 1
                    while j < n and v[j].lower() != "do":
                        inner = v[j][1:-1].strip() if v[j][:1] == "(" and v[j][-1:] == ")" else ""
                        if len(inner) > 1 and inner[0] in "'`" and inner[-1] == inner[0]:
                            self.reparse(inner[1:-1], "cmd")    # for /f ... in ('command') runs the command
                        j += 1
                    k = j + 1
                    continue
            self.emit(p, k + 1)
            return self.made

    def _posix_shell(self, p, k):
        v, n = self.v, len(self.v)
        j, cflag, sflag = k + 1, False, False
        while j < n:
            w = v[j]
            if w in ("--", "-"):
                sflag = sflag or w == "-"                       # `bash -`: commands from stdin
                j += 1
                break
            if w.startswith("--"):
                j += 2 if w in ("--rcfile", "--init-file") else 1
                continue
            if len(w) > 1 and w[0] in "-+":
                if w[0] == "-" and "c" in w[1:]:
                    cflag = True
                if "s" in w[1:]:
                    sflag = True
                j += 2 if w in ("-o", "+o", "-O", "+O") else 1
                continue
            break
        if cflag:
            if j < n:
                self.reparse(v[j], "bash")
            return self.made
        if j < n and not sflag:
            self.emit(program_name(v[j], self.d, self.y[j]), j + 1)    # bash FILE args: FILE runs
            return self.made
        self.emit(p, k + 1)                                            # reads commands from stdin
        return self.made

    def _ps_shell(self, p, k):
        v, n = self.v, len(self.v)
        j = k + 1
        while j < n:
            w = v[j]
            if w == "-":                                        # `pwsh -`: commands from stdin
                self.emit(p, k + 1)
                return self.made
            if w[:1] in ("-", "/") and len(w) > 1:
                name = w[1:].split(":", 1)[0].lower()
                if name == "c" or (len(name) >= 2 and "command".startswith(name)):   # -c -co -com ... -command
                    rest = list(v[j + 1:])
                    if rest == ["-"] or not rest:
                        self.emit(p, k + 1)
                    else:
                        self.reparse(" ".join(rest), "powershell")
                    return self.made
                if name in ("e", "ec") or (len(name) >= 3 and "encodedcommand".startswith(name)):
                    if j + 1 < n:
                        try:
                            text = base64.b64decode(v[j + 1], validate=True).decode("utf-16-le")
                        except Exception:
                            raise ParseError("an undecodable -EncodedCommand")
                        self.reparse(text, "powershell")
                    return self.made
                if name == "f" or (len(name) >= 2 and "file".startswith(name)):
                    if j + 1 < n:
                        self.emit(program_name(v[j + 1], "powershell", self.y[j + 1]), j + 2)
                    return self.made
                if name in ("workingdirectory", "wd") and j + 1 < n:
                    self.cd = _join_cd(self.cd, v[j + 1])
                j += 2 if any(name and s.startswith(name) for s in _PS_VALUE_PARAMS) else 1
                continue
            if p == "powershell":
                self.reparse(" ".join(v[j:]), "powershell")
            else:
                self.emit(program_name(w, "powershell", self.y[j]), j + 1)
            return self.made
        self.emit(p, k + 1)
        return self.made

    def _cmd_shell(self, k):
        v, n = self.v, len(self.v)
        j = k + 1
        while j < n:
            lw = v[j].lower()
            head = lw[1:] if lw.startswith("//") else lw
            if head[:2] in ("/c", "/k", "/r"):
                rest = ([v[j][len(lw) - len(head) + 2:]] if len(head) > 2 else []) + list(v[j + 1:])
                self.reparse(" ".join(rest), "cmd")
                return self.made
            if lw.startswith("/"):
                j += 1
                continue
            break
        self.emit("cmd", k + 1)
        return self.made

    def _find(self, k):
        v, n = self.v, len(self.v)
        self.emit("find", k + 1)
        j = k + 1
        while j < n and (v[j] in ("-H", "-L", "-P") or v[j].startswith("-O") or v[j] == "-D"):
            j += 2 if v[j] == "-D" else 1
        starts, sq = [], []
        while j < n and not (v[j].startswith("-") or v[j] in ("(", "!", ")", ",", "\\(", "\\!")):
            starts.append(v[j])
            sq.append(self.q[j])
            j += 1
        if not starts:
            starts, sq = ["."], [False]
        while j < n:
            w = v[j]
            if w == "-delete":
                e = Exec("find.delete", tuple(starts), tuple(sq), self.redirs, self.d, self.cd, self.stdin,
                         self.seg)
                self.ctx.out.append(e)
                self.made.append(e)
                j += 1
                continue
            if w in ("-exec", "-execdir", "-ok", "-okdir"):
                j += 1
                iv, iq, iy = [], [], []
                while j < n and v[j] not in (";", "\\;") and not (v[j] == "+" and v[j - 1] == "{}"):
                    if v[j] == "{}":
                        iv += starts
                        iq += sq
                        iy += [False] * len(starts)
                    else:
                        iv.append(v[j])
                        iq.append(self.q[j])
                        iy.append(self.y[j])
                    j += 1
                j += 1
                if iv:
                    if self.depth + 1 > MAX_DEPTH:
                        raise ParseError("nesting deeper than %d" % MAX_DEPTH)
                    r = _Resolver(self.d, iv, iq, iy, (), self.cd, "", self.seg, [], self.depth + 1, self.ctx,
                                  self.recurse, False)
                    self.made += r.run()
                continue
            j += 1
        return self.made

    def _start_process(self, p, k):
        """Start-Process [-FilePath] F [-ArgumentList|-Args] a,b,...: F runs with every element joined by spaces
        (as Start-Process does), re-parsed as a Windows command line so `cmd /c` / `pwsh -c` inside it are seen."""
        e = self.emit(p, k + 1)
        v, q = e.argv, e.quoted
        fp, args, pos, j = None, None, [], 0
        while j < len(v):
            w = v[j]
            if w.startswith("-") and len(w) > 1 and not q[j]:
                name = w[1:].split(":", 1)[0].lower()
                if name and ("filepath".startswith(name) or name in ("path", "pspath")):
                    fp = v[j + 1] if j + 1 < len(v) else ""
                    j += 2
                    continue
                if name and ("argumentlist".startswith(name) or name == "args"):
                    args = []
                    j += 1
                    while j < len(v) and not (v[j][:1] == "-" and len(v[j]) > 1 and not q[j]):
                        args.append(v[j])                       # every comma-separated element
                        j += 1
                    continue
                j += 1 if any(s.startswith(name) for s in _SAPS_SWITCHES) else 2
                continue
            pos.append(w)
            j += 1
        if fp is None and pos:
            fp, pos = pos[0], pos[1:]
        if args is None:
            args = pos[:1]
        if fp is None:
            return self.made
        if "$" in fp or "(" in fp:
            self.opaque(k + 1)                                  # the program itself is not known
            return self.made
        self.reparse('"' + fp.replace('"', "") + '" ' + " ".join(args), "cmd")
        return self.made


def _records_to_execs(sc: _Scanner, recurse: bool = True) -> None:
    d, t, ctx = sc.DIALECT, sc.t, sc.ctx
    synth = sorted(sc.synth, key=lambda x: x[0])
    si = 0
    groups = {}
    for idx, rec in enumerate(sc.recs):
        while si < len(synth) and synth[si][0] <= idx:
            ctx.out.append(synth[si][1])
            si += 1
        ws = rec.words
        k = 0
        if d == "bash":
            while k < len(ws) and _ASSIGN.match(ws[k].raw):
                k += 1
        ws = ws[k:]
        vals = ["".join(w.b) for w in ws]
        if d == "powershell":
            keep = [j for j, w in enumerate(ws) if not (vals[j] == "--%" and not w.quoted)]   # stop-parsing token
            ws, vals = [ws[j] for j in keep], [vals[j] for j in keep]
            vals = ["-" + v[1:] if v[:1] in _PS_DASHES and not w.quoted else v for v, w in zip(vals, ws)]
        seg = t[rec.start:rec.end]
        stdin = "pipe" if rec.stage > 0 else ""
        bodies = [(h.body or "", h.quoted) for h in rec.heredocs]
        quoted = [w.quoted for w in ws]
        dyn = [w.dyn for w in ws]
        made = _Resolver(d, vals, quoted, dyn, rec.redirs, rec.cd, stdin, seg, bodies,
                         sc.depth, ctx, recurse, rec.call).run()
        if d == "bash" and any(w.bs for w in ws):                     # R6: the literal reading too
            made = made + _Resolver(d, ["".join(w.l) for w in ws], quoted, dyn, rec.redirs, rec.cd, stdin, seg,
                                    bodies, sc.depth, ctx, False, rec.call).run()
        groups.setdefault(rec.key, []).append((rec, made))
    for _, e in synth[si:]:
        ctx.out.append(e)
    if not recurse:
        return
    for items in groups.values():                                    # cat <<EOF | bash, echo cmd | sh
        if len(items) < 2:
            continue
        for ci, (crec, cmade) in enumerate(items):
            shells = [e for e in cmade if e.program in ALL_SHELLS and e.stdin_from == "pipe"]
            if not shells or ci == 0:
                continue
            dial = _shell_dialect(shells[0].program)
            known = True
            for urec, umade in items[:ci]:
                for h in urec.heredocs:
                    _sub(h.body or "", dial, sc.depth + 1, crec.cd, ctx)
                for e in umade:
                    if e.program in _TEXT_SOURCES:
                        if e.argv:
                            _sub(" ".join(e.argv), dial, sc.depth + 1, crec.cd, ctx)
                    elif not urec.heredocs:
                        known = False
            if not known:                    # `cat s.sh | bash`, `curl ... | sh`: commands the parser never saw
                ctx.out.append(shells[0]._replace(program="?", argv=(shells[0].program,), quoted=(False,)))
            break        # only the FIRST shell reads the upstream text; a later one reads that shell's output


_TEXT_SOURCES = frozenset(("echo", "printf", "write-output", "write-host", ""))
_SCANNERS = {"bash": _Bash, "powershell": _Pwsh, "cmd": _Cmdexe}


def _sub(text: str, dialect: str, depth: int, cd: str, ctx: _Ctx, mode: str = "n") -> None:
    """Parse `text` (itself a command line) at nesting `depth`; its execs go to ctx.out."""
    if depth > MAX_DEPTH:
        raise ParseError("nesting deeper than %d" % MAX_DEPTH)
    sc = _SCANNERS[dialect](text, depth, cd, ctx, mode)
    sc.run()
    _records_to_execs(sc)


def _parse_one(command: str, dialect: str) -> Parsed:
    ctx = _Ctx()
    try:
        _sub(command, dialect, 0, "", ctx)
        return Parsed(tuple(ctx.out), True, "")
    except ParseError as e:
        return Parsed(tuple(ctx.out), False, "%s: %s" % (dialect, e))
    except Exception as e:  # a bug here must read as "could not parse", never as an allow
        return Parsed(tuple(ctx.out), False, "internal error: %s: %s" % (type(e).__name__, e))


def parse(command: str, shell: str = "") -> Parsed:
    """The commands `command` executes. Never raises. See the module docstring."""
    try:
        if not isinstance(command, str):
            return Parsed((), False, "the command is not a string")
        d = _dialect(shell)
        if d is not None:
            return _parse_one(command, d)
        a, b = _parse_one(command, "bash"), _parse_one(command, "powershell")
        return Parsed(a.execs + b.execs, a.ok and b.ok, "; ".join(x for x in (a.why, b.why) if x))
    except Exception as e:
        return Parsed((), False, "internal error: %s: %s" % (type(e).__name__, e))
