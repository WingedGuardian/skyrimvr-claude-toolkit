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
             bash: a cd inside a pipeline stage or a subshell does not carry out of it. PowerShell: the location
             is session-wide, so a pipeline stage's Set-Location carries, and one inside a block or ( ) makes
             the caller's directory "?" (whether the block ran is not known). cmd: `cd` takes the rest of the
             line as one path, spaces included.
  stdin_from "pipe" | "heredoc:shell" | "heredoc:interpreter" | "heredoc:data" | "".
  segment    the source text of this simple command (for the legacy fallback and messages' basenames only).
  stdin_body the heredoc / here-string body when stdin_from starts with "heredoc:" (an addition to the
             Task 5 interface: R7(b) needs the interpreter's code).
  subst      ((argv word, the same word with its variables replaced), ...) for each word whose every $NAME /
             ${NAME} certainly holds a same-command LITERAL assignment here (Task 7b; assign.py has the rules).
             Words not listed stay as they are -- unresolved. Only the root command's top-level statement list
             is tracked (track_vars / lookup): an assignment in a branch, loop, function, subshell, pipeline, `&`,
             after `||`, written twice anywhere, or followed by source/eval/dot-source never resolves a use; cmd
             never resolves (it expands %K% before `set` runs).

KNOWN LIMITS -- each gives a PARTIAL answer: the rules judge only what the parser could read. Where the program
itself cannot be named, the rules ask the user when the command names a protected path (shell_rules: S-SAVE-OPAQUE
for a save, S-OPAQUE-PROTECTED for the install's Data, the settings or MO2 folders, a plugin or archive file, or a
Bethesda key); on any other path such a command is allowed as read, and the verdict is marked "partial":
  - a program held in a variable (`$R reset-havok`): program "?", argv kept; rules must treat "?" as opaque.
    In bash the words after it are ALSO read as the command (`$R rm x`: $R may be empty or a wrapper, and rm runs);
    so are cmd's (`%R% del x`, `!R! del x`) and a PowerShell `&`/`.` call's (`& $R Remove-Item x`). When a quoted word
    follows, the joined words are also read as code (`$R 'rm x'` with R=eval, `& $R 'Remove-Item x'` with
    Invoke-Expression). A concatenated name offers the programs its literal part can end (`%R%del`, `"$($R)-Item"`).
    It stays "?" beside those readings, and a save in its words asks (S-SAVE-OPAQUE), as does a protected path in
    its words or in the command text (S-OPAQUE-PROTECTED). Not parsed: a name built entirely from variables (`$A$B`),
    and a string that a variable holds and an eval runs (`eval "$CODE"`, `iex $s`) unless that variable is a certain
    same-command literal (guard3: then the literal is parsed as the command it is); both ask only through the
    S-OPAQUE-PROTECTED text check, so a protected path spelled relatively inside such a string is not seen.
    A `cd` behind such a word (`$R cd X`) keeps the directory before it: the cd runs only if $R is empty, and one
    directory cannot hold both readings (marking it unknown loosened `$R cd /tmp && rm -rf Data` in the install).
  - a quote-split token (`--ap""ply`, `r''m`): RESOLVED -- words are unquoted and concatenated as bash does.
  - process substitution `<( )` / `>( )`: parsed as commands; the word itself is dynamic, and it joins the text it
    touches as bash does (`X=<(echo a) rm x` is an assignment prefix, then rm).
  - `$(( ))` arithmetic and `${ }` expansions are skipped; a `$( )` INSIDE them is not parsed.
  - aliases and shell functions defined in the same command are not expanded (a function's BODY is parsed).
  - PowerShell method calls other than [IO.File]/[IO.Directory] are not execs, except the opaque ones
    (.Delete/.MoveTo/.Invoke/::Create ...): a "?" exec whose argv is the receiver+method as written and the call's
    arguments, and for `.Delete()` an io.directory.delete of the receiver (unresolved). A script block or $( )
    passed to any method IS parsed.
  - a here-string `@" "@` is data; `$( )` inside it is not parsed.
  - `env -S STR` and `xargs sh -c` with a dynamic string: program/argv as given, the string is not split.
"""
from __future__ import annotations

import base64
import fnmatch
import functools
import re
from typing import List, NamedTuple, Optional, Tuple

from . import assign

MAX_TOKENS = 2000
MAX_DEPTH = 8
# Every branch is linear, but "linear" is proven only up to the sizes the timing tests run (20/80 KB), and a
# superlinear path small enough to stay under the ratio floor at 80 KB would cost seconds at megabytes. Above this
# cap the parser does not try: ok=False, and the legacy rules (linear) decide. MEASURED 2026-10-03: the largest of
# 55,467 real shell commands is 50,452 characters.
MAX_INPUT_CHARS = 256 * 1024


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
    subst: Tuple[Tuple[str, str], ...] = ()


class Parsed(NamedTuple):
    execs: Tuple[Exec, ...]
    ok: bool
    why: str
    unread: bool = False        # RT-A: DECODED text (base64 -d, xxd -r, printf '\x..', FromBase64String) runs as commands
    # RT-A G1/G2: the command STRING each shell was given with -c / -Command, as (shell program, text). The string's
    # own commands are execs as usual but the shell itself is not one, so a rule about the string reads it here.
    codes: Tuple[Tuple[str, str], ...] = ()
    xargs: Tuple[str, ...] = ()     # RT-A G2: the program each `xargs` runs (its first word after the options)
    # The "?" execs whose program word offered NO candidate (no same-command assignment, default or literal part):
    # the guard has no idea what runs. A save in their words asks the user (shell_rules, S-SAVE-OPAQUE).
    unnamed: Tuple[Exec, ...] = ()


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
    "sal": "set-alias", "nal": "new-alias",
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
    if dynamic and (not b or "$" in b or "`" in b or "(" in b or "%" in b or "!" in b):
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
    __slots__ = ("tokens", "out", "frame_ids", "via_xargs", "root", "counts", "subst", "dialect", "poison", "unread",
                 "aliases", "codes", "xargs", "unnamed", "lits", "evals")

    def __init__(self, root: str = "", dialect: str = ""):
        self.unread = False             # RT-A: decoded text runs as commands (Parsed.unread)
        self.aliases: dict = {}         # RT-A: same-command aliases, name -> body (bash alias, PowerShell Set-Alias)
        self.codes: List[Tuple[str, str]] = []      # RT-A G1/G2: Parsed.codes
        self.xargs: List[str] = []                  # RT-A G2: Parsed.xargs
        self.unnamed: List[Exec] = []               # Parsed.unnamed
        self.lits: dict = {}                        # value -> _raw_literal, for the simple command being resolved
        self.tokens = 0
        self.out: List[Exec] = []
        self.frame_ids = 0
        self.via_xargs = set()          # ids of execs xargs runs: their stdin items arrive as ARGUMENTS
        self.root = root                # the whole command (Task 7b: assignments are counted over all of it)
        self.dialect = dialect
        self.poison: Optional[str] = None
        self.evals: List[str] = []      # guard3: eval-of-one-variable statements poisoned() deferred (_settle_evals)
        self.counts: Optional[dict] = None
        self.subst: Tuple[Tuple[str, str], ...] = ()     # the Exec.subst of the simple command being resolved

    def writes(self, name: str) -> int:
        if self.counts is None:
            self.counts = assign.write_counts(assign.normalize(self.root))
        return self.counts.get(name.lower(), 0)

    def poisoned(self) -> str:
        if self.poison is None:
            self.poison = assign.poisoned(self.root, self.dialect, self.evals)
        return self.poison

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
                 "drop_next", "call", "frame", "after_time", "coproc", "kv", "lst", "seq", "valid", "redir_words")

    def __init__(self, f, start: int):
        self.words: List[_Word] = []
        self.redirs: List[Tuple[str, str]] = []
        self.redir_words: List[_Word] = []      # fix-unclear 4b: each redirect target's word, for Exec.subst
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
        self.kv = self.lst = self.seq = 0       # Task 7b: where this command sits in the top-level statement list
        self.valid = False


class _Frame:
    __slots__ = ("kind", "cmd", "word", "mode", "cd", "pipe", "stage", "case", "dbr", "start", "id", "last_sep",
                 "io", "rec_mark", "ctl", "kv", "lst", "seq", "chain_ok", "pend", "opq")

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
        self.opq = -1               # I7: index in _Scanner.synth of this call's opaque exec
        self.rec_mark = 0
        self.ctl = 0                # if/while/until/for/select/{ depth (bash keywords: not frames)
        self.kv = 0                 # Task 7b, top frame only: promotions so far,
        self.lst = 0                # the current and-or list,
        self.seq = 0                # the pipeline's position in it,
        self.chain_ok = True        # every connector so far in it is &&,
        self.pend: List[str] = []   # assignments FIRST in this list (they hold once it ends)


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
    # Final review I1: bash runs a pipeline stage or a ( ) in a subshell, so a cd there does not carry. PowerShell's
    # location is SESSION-wide: a Set-Location in a pipeline stage, an if/try body, `& { }` or `. { }` moves the caller.
    SESSION_CD = False

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
        self.vars: dict = {}            # Task 7b: name -> [value, known_ver, list, seq] (one literal assignment)
        self.unsure = False             # a closing keyword without its opener: the control depth is not known

    def _frame(self, kind, cd, mode, start):
        self.ctx.frame_ids += 1
        return _Frame(kind, cd, mode, self.ctx.frame_ids, start)

    def _word(self, f: _Frame) -> _Word:
        if f.word is None:
            f.word = _Word(self.i)
        return f.word

    def _cmd(self, f: _Frame, start: int) -> _Cmd:
        if f.cmd is None:
            c = f.cmd = _Cmd(f, start)
            top = self.frames[0]
            c.kv, c.lst, c.seq, c.valid = top.kv, top.lst, top.seq, top.chain_ok
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
                cmd.redir_words.append(w)
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
                if self.SESSION_CD or (sep not in ("|", "|&") and cmd.stage == 0):
                    self.track_cd(f, cmd)
        if self.depth == 0 and self.DIALECT in ("bash", "powershell"):
            self.track_vars(f, cmd, sep)
        if sep in ("|", "|&"):
            f.stage += 1
        elif sep:
            f.pipe += 1
            f.stage = 0
        f.last_sep = sep

    def track_cd(self, f: _Frame, cmd: _Cmd) -> None:
        pass

    def cd_word(self, cmd: _Cmd, w: _Word, lit: bool) -> str:
        """The folder a cd word names (_cd_value). fix-limits 14: a word that is dynamic only through same-command
        literal assignments (`D=/x; cd "$D/sub"`) is that literal, resolved exactly as Exec.subst resolves an argv
        word (the same lookup, poison and IFS rules); any other dynamic word stays "?"."""
        v = _cd_value(w, lit)
        if v != "?" or not w.dyn or not self.vars or self.unsure or self.ctx.poisoned() or self.ctx.evals:
            return v                                # (guard3: an eval not yet settled may write the cd's variable)
        split_safe = self.DIALECT != "bash" or self.ctx.writes("IFS") == 0
        s = assign.expand_word(w.raw, self.DIALECT, lambda name: self.lookup(cmd, name), split_safe)
        return s if s and s != "-" else v

    # -- Task 7b: literal assignments in the ROOT command's top-level statement list (see assign.py)
    def assignments(self, cmd: _Cmd) -> List[Tuple[str, str]]:
        return []

    def track_vars(self, f: _Frame, cmd: Optional[_Cmd], sep: str) -> None:
        top = self.frames[0]
        if f is not top or top.ctl != 0 or top.case:
            return                                  # nested: neither records nor moves the statement list
        # (An assignment after a `||` is recorded too: lookup() refuses every later use in that broken chain, and
        # it is never first in its list. source/eval/dot-source poison the whole command: assign.poisoned.)
        if cmd is not None and not cmd.skip and cmd.stage == 0 and sep not in ("|", "|&"):
            for name, value in self.assignments(cmd):
                if self.ctx.writes(name) == 1 and name.lower() not in assign.SPECIAL:
                    key = name if self.DIALECT == "bash" else name.lower()
                    self.vars[key] = [value, None, cmd.lst, cmd.seq]
                    if cmd.seq == 0:
                        top.pend.append(key)        # first in its list: it always runs
        if sep == "&&":
            top.seq += 1
        elif sep == "||":
            top.seq += 1
            top.chain_ok = False
        elif sep in ("|", "|&"):
            pass
        else:
            if top.pend and sep != "&":             # `A &` ran in a background subshell
                top.kv += 1
                for k in top.pend:
                    self.vars[k][1] = top.kv
            top.pend = []
            top.lst += 1
            top.seq = 0
            top.chain_ok = True

    def lookup(self, cmd: _Cmd, name: str) -> Optional[str]:
        """The literal `name` certainly holds when `cmd` runs, or None."""
        r = self.vars.get(name if self.DIALECT == "bash" else name.lower())
        if r is None:
            return None
        value, kv, lst, seq = r
        if kv is not None and kv <= cmd.kv:
            return value                            # an earlier statement
        if cmd.valid and lst == cmd.lst and seq < cmd.seq:
            return value                            # earlier in the same pure && chain
        return None


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
_BASH_OPEN = frozenset(("if", "while", "until", "{"))          # Task 7b: a statement inside may not run
_BASH_CLOSE = frozenset(("fi", "done", "}"))
_VARNAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,255}")
# `(( ... ))` / `$(( ... ))` whose body reads as `word -opt` or `word word`: bash takes it as arithmetic and runs
# nothing, but another reading (or a typo) runs a command. The parser cannot be sure, so it says so.
_CMDLIKE = re.compile(r"[\s(]*[A-Za-z_./][A-Za-z0-9_./-]{0,255}[ \t]+(?:-[A-Za-z]|[A-Za-z_.])")
_SUBST_START = re.compile(r"\$\(|`|[<>]\(")
_ANSI = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f",
         "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}


def _ansi_utf8(value):
    """Bash's UTF-8 byte encoding, including its legacy non-scalar values."""
    if value >= 0x80000000:
        return b""  # Bash drops out-of-range \U escapes, retaining the suffix.
    if value < 0x80:
        return bytes((value,))
    size = next(size for size, limit in ((2, 0x800), (3, 0x10000), (4, 0x200000),
                                        (5, 0x4000000), (6, 0x80000000)) if value < limit)
    return bytes((((0xff << (8 - size)) & 255) | (value >> (6 * (size - 1))),)) + bytes(
        0x80 | ((value >> (6 * index)) & 63) for index in reversed(range(size - 1)))


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
            if v in _BASH_OPEN:
                f.ctl += 1
            elif v in _BASH_CLOSE:
                f.ctl -= 1
                if f.ctl < 0:
                    self.unsure = True              # a stray fi/done/}: resolution off (fix round 1)
            return True
        if v == "function":
            cmd.drop_next = True
            return True
        if v in ("for", "select"):
            f.ctl += 1                                          # closed by `done`
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

    def assignments(self, cmd):
        out = []
        for w in cmd.words:
            m = _ASSIGN.match(w.raw)
            if not m:
                return []                                       # `K=v cmd`: K is set for cmd only
            lhs = m.group()
            if "[" in lhs or lhs.endswith("+="):
                continue                                        # an element, or an append: not the whole value
            value = assign.bash_value(w.raw[m.end():])
            if value is not None:
                out.append((_VARNAME.match(lhs).group(), value))
        return out

    def track_cd(self, f, cmd):
        ws = cmd.words
        k = 0
        while k < len(ws) and _ASSIGN.match(ws[k].raw):
            k += 1
        while k < len(ws) and not ws[k].quoted and not ws[k].dyn and "".join(ws[k].b) in ("builtin", "command"):
            k += 1                                              # RT-A: `builtin cd X`, `command [-p] cd X` are cd
            while k < len(ws) and "".join(ws[k].b) in ("-p", "--"):
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
            new = self.cd_word(cmd, args[0], args[0].bs)
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
        # RT-A: `\ ` is an escaped SPACE in either reading (`/c/GOG\ Games`, `C:\GOG\ Games\x`): the literal (Windows)
        # reading keeps every other backslash as a separator, never this one.
        w.add(nx, nx if nx in " \t" else "\\" + nx)
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
        # Bash ANSI-C quotes contain bytes. Decode byte escapes together (not
        # individually) so UTF-8 paths and escaped ASCII extensions stay literal.
        out = bytearray()
        truncated = False
        while j < n:
            c = t[j]
            if c == "'":
                w.quoted = True
                w.add(out.decode("utf-8", "surrogateescape"))
                self.i = j + 1
                return
            if truncated:
                # Bash ignores this quoted portion after its first NUL. Only
                # locate the closing quote; invalid discarded escapes cannot
                # erase an already known command/path from the parse.
                j += 2 if c == "\\" and j + 1 < n else 1
                continue
            if c == "\\" and j + 1 < n:
                nx = t[j + 1]
                j += 2
                if nx in _ANSI:
                    data = _ANSI[nx].encode("utf-8")
                elif nx in "01234567xuU":
                    if nx in "01234567":
                        start, maximum, digits, base = j - 1, 3, "01234567", 8
                    else:
                        start, maximum, digits, base = j, {"x": 2, "u": 4, "U": 8}[nx], "0123456789abcdefABCDEF", 16
                    end = start
                    while end < n and end - start < maximum and t[end] in digits:
                        end += 1
                    if end == start:
                        data = ("\\" + nx).encode("utf-8")
                    else:
                        value = int(t[start:end], base)
                        j = end
                        if nx in "uU":
                            data = _ansi_utf8(value)
                        else:
                            data = bytes((value & 255,))
                elif nx == "c" and j < n and t[j] != "'":
                    control = t[j]
                    j += 1
                    doubled = control == "\\" and j < n and t[j] == "\\"
                    if doubled:
                        j += 1
                    raw = control.encode("utf-8", "surrogateescape")
                    data = bytes((127 if raw[0] == 63 else raw[0] & 31,)) + raw[1:]
                    if control == "\\" and not doubled and j < n and t[j] == "'":
                        data += b"'"
                        j += 1
                else:
                    # Unrecognized escapes retain the backslash in Bash.
                    data = ("\\" + nx).encode("utf-8", "surrogateescape")
            else:
                data = c.encode("utf-8", "surrogateescape")
                j += 1
            if not truncated:
                before_nul, separator, _ = data.partition(b"\0")
                out.extend(before_nul)
                truncated = bool(separator)
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
            # It joins the word it touches, as bash does (`X=<(echo a) rm x`: X=/dev/fd/63, and rm is the program;
            # `a<(true)b` is ONE word). Ending the word here made `X=` the assignment and the substitution the
            # program ("?"), so every rule that names a program missed the `rm` after it.
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
_PS_METHOD = re.compile(r"(?:\.|::)([A-Za-z_][A-Za-z0-9_]{0,63})$")
_PS_OPAQUE_METHODS = frozenset(("delete", "moveto", "copyto", "invoke", "invokereturnasis", "addscript", "start",
                                "create"))
_PS_DASHES = "–—―"                                 # PowerShell takes these as a parameter dash


def _ps_quoted(w) -> bool:
    """A PowerShell word is a PARAMETER when its first character is an unquoted dash, whatever is quoted after it:
    `-Path:'C:\\x y'` binds -Path (RT-A). Only a word whose dash is itself quoted (`'-x'`) is a string."""
    return w.quoted and not (w.raw[:1] in "-" + _PS_DASHES and len(w.raw) > 1)


def _ps_string(w) -> bool:
    """Final review I4: a word whose FIRST character opens a string (`'x'`, `"x"`, `@'..'@`) is an expression in
    command position, never a command name. A backtick escape inside a bare word (`Re`move-Item`) does not make it one:
    PowerShell still runs that word as a command."""
    r = w.raw
    return r[:1] in _SQ + _DQ or (r[:1] == "@" and r[1:2] in _SQ + _DQ)


def _io_program(cls: str, method: str) -> str:
    cls, m = cls.lower(), method.lower()
    if cls == "file":
        if m.startswith("write") or m in ("create", "createtext", "openwrite"):
            return "io.file.write"
        if m.startswith("append"):
            return "io.file.append"
        if m in ("delete", "move", "copy", "replace"):
            return "io.file." + m
        if m in _IO_LINK_METHODS:
            return "io.file.link"                       # T8 fix round 2: (link path, target) -- self_rules reads both
        return ""
    if m in ("delete", "move"):
        return "io.directory." + m
    if m in _IO_LINK_METHODS:
        return "io.directory.link"
    return ""


_IO_LINK_METHODS = frozenset(("createsymboliclink", "createhardlink"))


class _Pwsh(_Scanner):
    DIALECT = "powershell"
    SESSION_CD = True

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

    def assignments(self, cmd):
        a = assign.ps_assignment(self.t[cmd.start:cmd.end])
        return [a] if a else []

    def track_cd(self, f, cmd):
        if not cmd.words or _ps_string(cmd.words[0]) or cmd.words[0].dyn:
            return
        p = program_name("".join(cmd.words[0].b), "powershell")
        if p == "pop-location":
            f.cd = "?"
            return
        if p not in _SET_LOCATION:
            return
        ex = _exec_of("powershell", [("".join(w.b), _ps_quoted(w), w.dyn) for w in cmd.words[1:]], p)
        vals = ps_param(ex, "Path", 0) or ps_param(ex, "LiteralPath")
        if not vals:
            new = "?" if cmd.stage > 0 else "~"           # I1: `Get-Item X | Set-Location`: the path is pipe input
        else:
            src = [w for w in cmd.words[1:] if "".join(w.b) == vals[0]]
            new = self.cd_word(cmd, src[0], False) if src else vals[0]
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
        call = False
        if w is not None:
            m = _IO_CALL.fullmatch(self.t, w.start, i)
            tail = _PS_METHOD.search(self.t, max(w.start, i - 80), i)   # bounded: linear
            # Only `.Name(` and `::Name(` are method calls (their arguments are data). Anything else glued to a
            # `(` -- `[void](`, `[int](`, `name(` -- is a cast or a group: its contents are a pipeline that RUNS.
            call = bool(m or tail)
            if m:
                io = _io_program(m.group(1), m.group(2))
            elif tail and tail.group(1).lower() in _PS_OPAQUE_METHODS:
                # .Delete() / .MoveTo() / [scriptblock]::Create().Invoke(): something runs or is deleted,
                # and the parser cannot say what -- an opaque exec, never a silent nothing. Final review I7: it
                # carries the receiver and method as written, and (at the `)`) the call's arguments.
                io = "?"
        g = self._push(f, "call" if call else "paren", i, True)
        g.io = io
        if io == "?":
            g.opq = len(self.synth)
            self.synth.append((len(self.recs), Exec(
                "?", (self.t[w.start:i],), (False,), (), "powershell", f.cd, "", self.t[w.start:i])))
            if tail.group(1).lower() == "delete":
                # `.Delete()` deletes its RECEIVER: an operand the guard cannot place, never nothing.
                self.synth.append((len(self.recs), Exec(
                    "io.directory.delete", (self.t[w.start:tail.start()],), (False,), (), "powershell", f.cd, "",
                    self.t[w.start:i])))
        self.i = i + 1

    def _close(self, f, c):
        want = ("subst", "paren", "call") if c == ")" else ("block", "hash")
        if f.kind not in want:
            raise ParseError("unbalanced " + c)
        self.end_cmd(f, c)
        self.frames.pop()
        p = self.frames[-1]
        if f.cd != p.cd:
            # I1: the location is session-wide, so a Set-Location inside an if/try body, `& { }`, `. { }` or ( ) moves
            # the caller too -- but whether that body ran (a branch, a scriptblock merely defined) is not known.
            p.cd = "?"
        if f.kind in ("hash", "call"):
            mine = [r for r in self.recs[f.rec_mark:] if r.frame == f.id]
            if f.kind == "call" and f.io == "?":
                args = [w for r in mine for w in r.words]   # I7: the opaque call's arguments, as its own words
                at, ex = self.synth[f.opq]
                self.synth[f.opq] = (at, ex._replace(argv=ex.argv + tuple("".join(w.b) for w in args),
                                                     quoted=ex.quoted + tuple(w.quoted for w in args)))
            elif f.kind == "call" and f.io:
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
# Delayed expansion (`cmd /v:on`, setlocal EnableDelayedExpansion): `!R! del x` runs del when R=call (MEASURED). With it
# off the text is literal; reading it as dynamic is the stricter of the two.
_CMD_DELAYED = re.compile(r"![^!\s]{1,256}!")
_CMD_REM = re.compile(r"(?:rem(?=[ \t\r\n]|$)|::)", re.IGNORECASE)
_CMD_REDIR_OPS = (">>", ">&", "<&", ">", "<")
_CMD_REM_END = re.compile(r"[&|\n]")
_CMD_CMP = frozenset(("equ", "neq", "lss", "leq", "gtr", "geq"))
_CMD_CD_GLUED = re.compile(r"@?(cd|chdir|pushd)([\\/.].*)", re.IGNORECASE | re.DOTALL)


class _Cmdexe(_Scanner):
    DIALECT = "cmd"

    def track_cd(self, f, cmd):
        if not cmd.words:
            return
        w0 = cmd.words[0]
        first = "".join(w0.b)
        glued = None if w0.quoted else _CMD_CD_GLUED.fullmatch(first)
        lead = ""
        if glued:                                           # I2: `cd/d X`, `cd\x`, `cd..`: cmd ends the name there
            p, lead = CMD_ALIASES.get(glued.group(1).lower(), glued.group(1).lower()), glued.group(2)
        else:
            p = program_name(first, "cmd")
        if p in ("cd", "pushd"):
            if lead.startswith("/"):
                if lead.lower() != "/d":
                    f.cd = "?"                              # `cd/x`: not a spelling this models
                    return
                lead = ""
            parts = ([(lead, w0)] if lead else []) + [("".join(w.b), w) for w in cmd.words[1:]
                                                      if "".join(w.b).lower() != "/d"]
            if not parts:
                f.cd = _join_cd(f.cd, "?")
                return
            if len(parts) == 1:
                new = parts[0][0] if parts[0][1] is w0 else _cd_value(parts[0][1], False)
            elif p == "pushd" or any(w.dyn for _, w in parts) or any(
                    self.t[a[1].start + len(a[1].raw):b[1].start] != " " for a, b in zip(parts, parts[1:])):
                new = "?"       # pushd's own split, a variable, or a gap that is not one space: not rebuilt
            else:
                # I2: with extensions on (the default) cd/chdir take the WHOLE rest of the line as the path, spaces
                # included: `cd C:\GOG Games\x` is one path, not `C:\GOG` and an extra word.
                new = " ".join(s for s, _ in parts)
            f.cd = _join_cd(f.cd, new)
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
                w = self._word(f)
                w.add(m.group())
                if _CMD_DELAYED.search(m.group()):
                    w.dyn = True
                self.i = m.end()
                continue
            if c == '"':
                j = t.find('"', self.i + 1)
                if j < 0:
                    raise ParseError('unterminated " quote')
                w = self._word(f)
                w.quoted = True
                w.add(t[self.i + 1:j])
                if _CMD_DELAYED.search(t, self.i + 1, j):
                    w.dyn = True
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
_CMD_PRE_SWITCH = re.compile(r"/{1,2}(?:[sqdau]|[efvt]:[A-Za-z0-9]{1,8})$", re.IGNORECASE)   # cmd switches before /c


def _join_words(words, dialect: str) -> str:
    """argv words joined back into one command line for `cmd /c` / `pwsh -Command`. RT-A: when the line arrives as
    SEVERAL words, a word holding a space was one argument (`cmd /c rd /s /q "C:\\GOG Games\\x"`), so it is quoted
    again rather than re-split. A single word is the whole command line, as written."""
    if len(words) < 2:
        return " ".join(words)
    out = []
    for w in words:
        if not re.search(r"\s", w) or (w[:1] in "\"'" and w[-1:] == w[:1]):
            out.append(w)
        elif dialect == "cmd":
            out.append('"' + w.replace('"', "") + '"')
        else:
            out.append("'" + w.replace("'", "''") + "'")
    return " ".join(out)


_CMD_QUOTE_SPECIAL = frozenset("&<>()@^|")


def _cmd_c_lines(line: str, s_flag: bool) -> List[str]:
    """The command line(s) `cmd /c LINE` runs, after cmd's own quote rule (`cmd /?`): with /S, or unless the line
    holds exactly two quotes with no special character and some whitespace between them, the FIRST and the LAST quote
    are removed. When those conditions hold cmd keeps the quotes only if the quoted text names an executable file,
    which the parser cannot know: both readings."""
    s = line.lstrip()
    if not s.startswith('"'):
        return [line]
    last = s.rfind('"')
    stripped = s[1:last] + s[last + 1:] if last > 0 else s[1:]
    if s_flag:
        return [stripped]
    inner = s[1:last] if last > 0 else ""
    if s.count('"') == 2 and inner and not (set(inner) & _CMD_QUOTE_SPECIAL) and re.search(r"\s", inner):
        return [stripped, s]
    return [stripped]


def _cmd_c_follows(v, j) -> bool:
    """Whether v[j:] is cmd's switch list ending in /c or /k (`/s /q /d /e:on /v:off ... /c`)."""
    while j < len(v) and _CMD_PRE_SWITCH.match(v[j]):
        j += 1
    return j < len(v) and v[j].lower().lstrip("/")[:1] in ("c", "k", "r") and v[j].startswith("/") and \
        len(v[j].lstrip("/")) == 1
_PS_VALUE_PARAMS = ("executionpolicy", "ep", "ex", "workingdirectory", "wd", "configurationname", "config",
                    "custompipename", "inputformat", "outputformat", "of", "windowstyle", "version",
                    "settingsfile", "encodedarguments", "psconsolefile")


# ---- RT-A: decoded text run as commands (Parsed.unread). Ported from the install's text rule "decoded text piped into
# a shell": a DECODER's output is text the guard never sees, so a shell (or eval / iex / source) running it is
# unreadable, whatever it holds. An installer piped to sh (`curl ... | sh`) is not decoded text: not this rule.
_ESCAPED = re.compile(r"\\(?:x[0-9A-Fa-f]|u[0-9A-Fa-f]|U[0-9A-Fa-f]|[0-7])")
_DECODE_TEXT = re.compile(
    r"\bbase(?:64|32)\b[^|;&)\n]{0,200}?[ \t]-(?:-decode\b|[A-Za-z]{0,4}[dD])"
    r"|\bxxd\b[^|;&)\n]{0,200}?[ \t]-[A-Za-z]{0,4}r|\buudecode\b|\bcertutil\b[^|;&)\n]{0,200}?-decode"
    r"|\bopenssl\b[^|;&)\n]{0,200}?[ \t]-d\b|FromBase64String|\bprintf[ \t]{1,20}['\"]?[^'\"|;&\n]{0,200}?"
    r"\\(?:x[0-9A-Fa-f]|[0-7])", re.IGNORECASE)


def _is_decoder(e: Exec) -> bool:
    """An exec whose OUTPUT is decoded text: base64/base32 -d, xxd -r, openssl ... -d, uudecode, certutil -decode,
    printf (or echo -e) with \\x / octal / \\u escapes in its words."""
    p, a = e.program, e.argv
    short = [w[1:] for w in a if w.startswith("-") and not w.startswith("--")]
    if p in ("base64", "base32", "basenc"):
        return "--decode" in a or any("d" in s or "D" in s for s in short)
    if p == "xxd":
        return any("r" in s for s in short)
    if p == "openssl":
        return "-d" in a
    if p == "uudecode":
        return True
    if p == "certutil":
        return any(w.lower() == "-decode" for w in a)
    if p == "printf":
        return any(_ESCAPED.search(w) for w in a)
    if p == "echo" and a and _ECHO_OPTS.match(a[0]):
        return any(_ESCAPED.search(w) for w in a[1:])
    return "frombase64string" in e.segment.lower()


# ---- RT-A: a program word the parser cannot prove. A word whose value is CERTAIN (Exec.subst: `R=rm; $R x`) is that
# program; otherwise each program the word CAN be -- a literal assigned to its variable anywhere in the command, a
# `${V:-rm}` default, a program named inside its `$( )`/backticks, a glob or brace word that matches -- is judged too,
# beside the opaque "?" exec. Only these programs are candidates: the delete primitives, the shells and ReSaver.
CANDIDATE_PROGRAMS = ("rm", "rmdir", "unlink", "del", "erase", "rd", "remove-item", "rimraf", "resaver-cli",
                      "resavercli") + tuple(sorted(ALL_SHELLS))
_EXPANSION = re.compile(r"\$\([^()]{0,4096}\)|`[^`]{0,4096}`|\$\{[^{}]{0,256}\}|\$[A-Za-z0-9_]{1,256}")
_WHOLE_VAR = re.compile(r"\$\{?(?:env:)?([A-Za-z_][A-Za-z0-9_]{0,255})\}?", re.IGNORECASE)
_DEFAULT = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]{0,255}):?[-=]([^{}]{0,256})\}")
_TOKEN = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_BRACE1 = re.compile(r"\{([^{}]{0,256},[^{}]{0,256})\}")


@functools.lru_cache(maxsize=256)    # one scan of the command per variable, not one per use of it (linear time)
def _assigned_literals(root: str, name: str, dialect: str) -> Tuple[str, ...]:
    """Every literal the command's text assigns to `name` anywhere (`N=v`, `N='v'`, `$N = "v"`): what a program
    variable MAY hold, used only as candidates, never as the certain value."""
    n = re.escape(name)
    if dialect == "powershell":
        rx = r"\$" + n + r"[ \t]{0,64}=[ \t]{0,64}(?:'([^'\n]{1,256})'|\"([^\"$`\n]{1,256})\"|([A-Za-z0-9_.:\\/-]{1,256}))"
        flags = re.IGNORECASE
    else:
        rx = r"(?<![\w$])" + n + r"=(?:'([^'\n]{1,256})'|\"([^\"$`\n]{1,256})\"|([A-Za-z0-9_.:/+-]{1,256}))"
        flags = 0
    return tuple(a or b or c for a, b, c in re.findall(rx, root, flags))


def _brace_alts(word: str) -> List[str]:
    out = [word]
    for _ in range(4):
        nxt = []
        for w in out:
            m = _BRACE1.search(w)
            if not m:
                nxt.append(w)
                continue
            nxt += [w[:m.start()] + alt + w[m.end():] for alt in m.group(1).split(",")]
        out = nxt[:64]
    return out


def _glob_word(base: str) -> bool:
    """A bash program word that is a glob or a brace expansion (`r?`, `r[m]`, `r{m,m}`), not a literal name."""
    if "*" in base or "?" in base or _BRACE1.search(base):
        return True
    i = base.find("[")
    return i >= 0 and base.find("]", i + 2) > i


# A word's expansions in its RAW text (quotes still in place, so `"$X"reg` keeps its boundary; the value `$Xreg` lost it).
_RAW_EXPANSION = {
    "bash": re.compile(r"\$\{[^{}]{0,256}\}|\$\([^()]{0,4096}\)|`[^`]{0,4096}`|\$[A-Za-z_][A-Za-z0-9_]{0,255}|\$[0-9@*#?$!-]"),
    "cmd": re.compile(r"%[^%\s]{1,256}%|![^!\s]{1,256}!"),
    "powershell": re.compile(r"\$\([^()]{0,4096}\)|\$\{[^{}]{0,256}\}|\$[A-Za-z_][\w:]{0,255}"),
}
_LITERAL_NAME = re.compile(r"[A-Za-z0-9_.+\-/\\:]{1,128}")


def _raw_literal(raw: str, dialect: str) -> str:
    """What a dynamic program word runs when every expansion in it is empty: `"$X"reg` -> reg, `${R}git` -> git,
    `%R%del` -> del (MEASURED in bash and cmd: an empty expansion leaves the literal, and it runs). "" when the word
    has no literal part, or one that is no program name (an unquoted `$Xreg` is ONE variable, so: "")."""
    rx = _RAW_EXPANSION.get(dialect)
    if rx is None:
        return ""
    s = rx.sub("", raw)
    if s == raw:
        return ""
    s = s.replace('"', "").replace("'", "")
    return s if _LITERAL_NAME.fullmatch(s) else ""


def _program_candidates(word: str, dialect: str, root: str) -> List[str]:
    """The CANDIDATE_PROGRAMS an unproven program word can run (see above), in a stable order."""
    if len(word) > 8192:
        return list(CANDIDATE_PROGRAMS)             # no real program word is this long: any of them (stricter)
    found: List[str] = []
    m = _WHOLE_VAR.fullmatch(word)
    names = [m.group(1)] if m else []
    d = _DEFAULT.fullmatch(word)
    if d:
        names.append(d.group(1))
        found.append(d.group(2))
    for nm in names:
        found += _assigned_literals(root, nm, dialect)
    for sub in re.findall(r"\$\(([^()]{0,4096})\)|`([^`]{0,4096})`", word):
        found += _TOKEN.findall(sub[0] or sub[1])
    expanded = _EXPANSION.sub("*", word)
    if dialect == "cmd":                            # `%R%del` / `!R!del`: R="call " runs del (MEASURED)
        expanded = _CMD_DELAYED.sub("*", re.sub(r"%[^%\s]{1,256}%", "*", expanded))
    base = _basename(expanded).lower()
    # bash also globs a literal pattern word (`/???/r?`); cmd and PowerShell only an expansion's text, which may be
    # anything (`& "$($R)-Item"` with R=Remove runs Remove-Item, MEASURED).
    if re.search(r"[a-z0-9]", base) and ("*" in base or dialect == "bash" and _glob_word(base)):
        for alt in _brace_alts(base):
            found += [p for p in CANDIDATE_PROGRAMS if fnmatch.fnmatchcase(p, alt)]
    out: List[str] = []
    for w in found:
        p = program_name(w.strip(), dialect)
        if p in CANDIDATE_PROGRAMS and p not in out:
            out.append(p)
    return out


# fd's value-taking options (fd 10): their value is never the pattern or a search path.
_FD_VALUED = frozenset(("-e", "--extension", "-t", "--type", "-E", "--exclude", "-d", "--max-depth", "--min-depth",
                        "--exact-depth", "-S", "--size", "--changed-within", "--changed-before", "--change-newer-than",
                        "--change-older-than", "--newer", "--older", "-o", "--owner", "-c", "--color", "-j",
                        "--threads", "--max-results", "--base-directory", "--path-separator", "--ignore-file",
                        "--format", "--batch-size", "--max-buffer-time", "--and", "--hyperlink"))
_FD_PLACEHOLDERS = frozenset(("{}", "{/}", "{//}", "{.}", "{/.}"))


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
        self.xargs = False
        self.alt_done = False         # RT-A: this resolver already IS one reading of an unproven program word
        self.after: List[int] = []    # the words after an unnamed program word, read as the command AFTER this one
        self.no_candidate = False     # the "?" program word offered no candidate at all (Parsed.unnamed)
        self.strq: Optional[List[bool]] = None    # I4: PowerShell, per word: opens with a string quote (_ps_string)
        self.made: List[Exec] = []

    # -- RT-A: unproven program words, aliases, carried redirects, decoded text
    def _sub_run(self, words: List[str], k: int, alt: bool = True) -> None:
        """Resolve `words` + the arguments after the program word as another reading of this command. alt=False: the
        reading may itself read an unproven program word (the words after an unnamed program word)."""
        if self.depth + 1 > MAX_DEPTH:
            raise ParseError("nesting deeper than %d" % MAX_DEPTH)
        n = len(words)
        r = _Resolver(self.d, list(words) + list(self.v[k + 1:]), [False] * n + list(self.q[k + 1:]),
                      [False] * n + list(self.y[k + 1:]), self.redirs, self.cd, self.stdin, self.seg, self.bodies,
                      self.depth + 1, self.ctx, self.recurse, False)
        r.alt_done = alt
        self.made += r.run()

    def _certain_program(self, k: int) -> Optional[List[str]]:
        """The words a dynamic program word CERTAINLY expands to (a same-command literal, Exec.subst), else None."""
        val = dict(self.ctx.subst).get(self.v[k])
        if not val or "$" in val or "`" in val:
            return None
        words = val.split()
        if len(words) != 1 and self.q[k]:
            # guard3 (v4.0.0 review LOW-5): "$CAP" holding `C:/GOG Games/tools/Caprica.exe` is ONE word, the path of
            # the program it runs; its last component names it. "$R" holding `rm -rf` names no program: unknown.
            tail = re.split(r"[/\\]", val)[-1]
            if ("/" in val or "\\" in val) and tail and not any(c.isspace() for c in tail):
                return [val]
            return None
        return words or None

    def _code(self, j: int, end: Optional[int] = None) -> Tuple[str, bool]:
        """guard3: the text words j..end hand an eval-like reader, each word through its same-command literal
        (Exec.subst), and whether every word was certain (no expansion left unresolved)."""
        sub = dict(self.ctx.subst)
        out, certain = [], True
        for w, dyn in zip(self.v[j:end], self.y[j:end]):
            if dyn and (w not in sub or "$" in sub[w] or "`" in sub[w]):
                certain = False
            out.append(sub.get(w, w) if dyn else w)
        return " ".join(out), certain

    def _carry(self, p: str, k: int) -> None:
        """A shell that re-parses a string still owns its OWN redirects: `bash -c 'echo x' > X.esp`, `cmd /c echo x >
        X.esp` write X.esp from the outer command. Its stdin is not the pipe's reader (that is the string's job)."""
        if any(">" in op for op, _ in self.redirs):
            e = Exec(p, tuple(self.v[k:]), tuple(self.q[k:]), self.redirs, self.d, self.cd, "", self.seg, "",
                     self.ctx.subst)
            self.ctx.out.append(e)
            self.made.append(e)

    def _decoded(self, text: str) -> None:
        if _DECODE_TEXT.search(text):
            self.ctx.unread = True

    def _procsub(self, word: str) -> None:
        """`source <(...)`, `bash <(...)`: the substitution's OUTPUT runs as commands. Echoed literal text is re-parsed
        as those commands; decoded text is unreadable (Parsed.unread); anything else stays the opaque exec."""
        if not (word.startswith("<(") and word.endswith(")")):
            return
        inner = word[2:-1]
        if self.depth + 1 > MAX_DEPTH:
            raise ParseError("nesting deeper than %d" % MAX_DEPTH)
        sub = _Ctx(inner, "bash")
        try:
            _sub(inner, "bash", self.depth + 1, self.cd, sub)
        except ParseError:
            self._decoded(inner)
            return
        if any(_is_decoder(e) for e in sub.out) or sub.unread:
            self.ctx.unread = True
            return
        if sub.out and all(e.program in _TEXT_SOURCES and e.program and not (e.argv and _ECHO_OPTS.match(e.argv[0]))
                           for e in sub.out):
            for e in sub.out:
                if e.argv:
                    self.reparse(" ".join(e.argv), "bash")

    def emit(self, program: str, k: int) -> Exec:
        stdin, body = self.stdin, ""
        if self.bodies:
            stdin = "heredoc:" + consumer_kind(program)
            body = self.bodies[-1][0]
        e = Exec(program, tuple(self.v[k:]), tuple(self.q[k:]), self.redirs, self.d, self.cd, stdin, self.seg,
                 body, self.ctx.subst)
        self.ctx.out.append(e)
        self.made.append(e)
        if program == "?" and self.no_candidate:
            self.ctx.unnamed.append(e)
        if self.xargs:                      # `... | xargs bash`, `xargs python`: the code arrives from stdin as args
            self.ctx.via_xargs.add(id(e))
            if program in ALL_SHELLS or (consumer_kind(program) == "interpreter" and _reads_code(e.argv)):
                self.opaque(k)
        if self.bodies and program in ALL_SHELLS and self.recurse:
            for b, _ in self.bodies:
                _sub(b, _shell_dialect(program), self.depth + 1, self.cd, self.ctx)
        elif program in ALL_SHELLS and any(op in ("<", "0<") for op, _ in self.redirs):
            self.opaque(k)                              # `sh < s.sh`, `sh < <(...)`: commands never seen
        return e

    def opaque(self, k: int) -> Exec:
        """Something runs and the parser cannot say what: an explicit "?" exec, never a silent nothing."""
        e = Exec("?", tuple(self.v[k:]), tuple(self.q[k:]), self.redirs, self.d, self.cd, self.stdin, self.seg,
                 "", self.ctx.subst)
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
        # RT-A: cmd's string is re-read in the R6 literal (Windows) reading too -- `cmd /c mklink /J C:\w\j ...` names
        # C:\w\j, which bash would have mangled. One extra reading per bash-to-cmd hop; MAX_DEPTH bounds the hops.
        if self.recurse or dialect == "cmd":
            _sub(text, dialect, self.depth + 1, self.cd, self.ctx)

    def run(self, k: int = 0) -> List[Exec]:
        self._run(k)
        for j in self.after:                            # after the main reading, so its execs keep their order
            self._sub_run([], j, alt=False)
            # ...and as CODE: the unnamed word may be eval / Invoke-Expression / `cmd /c`, which join their arguments
            # and run the result (MEASURED: `& $R 'Remove-Item -Recurse Data'` with R=Invoke-Expression deletes; bash
            # `$R 'rm -rf Data'` with R=eval does too). Only when a quoted word groups text the word reading cannot.
            if any(self.q[j + 1:]):
                _sub_speculative(" ".join(self.v[j + 1:]), self.d, self.depth + 1, self.cd, self.ctx)
        return self.made

    def _run(self, k: int = 0) -> List[Exec]:
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
            if p == "?" and _cmd_c_follows(v, k + 1):
                self.opaque(k)                                  # %COMSPEC% [/s /q ...] /c ..., & $env:ComSpec /c
                return self._cmd_shell(k)
            if first and self.d == "powershell":
                first = False
                if not self.call and k + 1 < n and self.y[k] and v[k + 1] in _PS_ASSIGN_OPS:
                    k += 2                                     # $x = <statement>
                    if k >= n:
                        continue
                    p = program_name(v[k], self.d, self.y[k])
                strq = self.q[k] if self.strq is None else self.strq[k]
                if v[k][:1] == "{" or not self.call and (
                        strq or (self.y[k] and v[k][:1] in "$(@") or v[k][:1] in "(["):
                    if self.redirs:                            # an expression or a script block, not a command
                        self.emit("", n)                       # (a block's commands were parsed in place)
                    return self.made
            first = False
            if not self.alt_done:
                if self.d == "bash" and p != "?" and not self.q[k] and _glob_word(_basename(v[k])):
                    p = "?"                                     # RT-A: `/???/r?`, `r[m]`, `r{m,m}`: not a literal name
                if p == "?":
                    certain = self._certain_program(k)
                    if certain is not None:                     # RT-A: `R=rm; $R x` runs rm; `R=echo; $R rm x` prints
                        self.v = v = list(v[:k]) + certain + list(v[k + 1:])
                        self.q = list(self.q[:k]) + [False] * len(certain) + list(self.q[k + 1:])
                        self.y = list(self.y[:k]) + [False] * len(certain) + list(self.y[k + 1:])
                        n = len(v)
                        continue
                    cands = _program_candidates(v[k], self.d, self.ctx.root)
                    lit = self.ctx.lits.get(v[k], "")
                    if lit and program_name(lit, self.d) not in cands:      # every expansion empty: the literal runs
                        cands = cands + [program_name(lit, self.d)]
                    for cand in cands:
                        self._sub_run([cand], k)                # each program it CAN be; the "?" exec stays too
                    self.no_candidate = not cands
                    # An unnamed program word may expand to NOTHING (`$R` unset, `$(true)`) or be a wrapper
                    # (`$SUDO`): either way the NEXT word runs (MEASURED, Git Bash: `$R rm x` runs rm). Read the
                    # words after it as the command too, with every reading a program word gets (candidates, and
                    # this one again for `$A $B rm`); each hop is one nesting level, so MAX_DEPTH bounds the chain.
                    # A glob word (not dynamic) never expands to nothing. cmd and a PowerShell `&`/`.` call
                    # read the same way (MEASURED, Windows: `%R% del` with R=call or `cmd /c`, and `& $R Remove-Item`
                    # with R=powershell, delete). PowerShell's `$R Remove-Item` without a call operator is a parse
                    # error there and runs nothing.
                    if self.y[k] and k + 1 < n and (self.d in ("bash", "cmd") or self.call):
                        self.after.append(k)
                elif not self.y[k]:
                    body = self.ctx.aliases.get(v[k] if self.d == "bash" else v[k].lower())
                    if body:
                        self._sub_run(body.split(), k)          # RT-A: an alias defined in the same command
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
                    if len(w) > 2 and w[0] == "-" and w[1] != "-":  # clustered: -iS 'cmd', -iuFOO, -C/tmp
                        idx = 1
                        while idx < len(w) and w[idx] not in "uCS":
                            idx += 1
                        if idx >= len(w):
                            k += 1
                            continue
                        val, own = w[idx + 1:], bool(w[idx + 1:])
                        if not own:
                            val = v[k + 1] if k + 1 < n else ""
                        nxt = k + 1 if own else k + 2
                        if w[idx] == "S":
                            self.reparse(" ".join([val] + list(v[nxt:])), "bash")
                            return self.made
                        if w[idx] == "C":
                            self.cd = _join_cd(self.cd, val)
                        k = nxt
                        continue
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
                if k < n and v[k] in ("-n", "--adjustment"):
                    k += 2
                elif k < n and (re.fullmatch(r"-[0-9]{1,3}|-n.+", v[k]) or v[k].startswith("--adjustment=")):
                    k += 1                                      # M2: `-n10` carries its value
                if k < n and v[k] == "--":
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
                        name, body = w.split("=", 1)
                        self.ctx.aliases[name] = body           # RT-A: later uses run the body with their arguments
                        self.reparse(body, "bash")              # an alias body runs where the alias is used
                return self.made
            if p in ("set-alias", "new-alias") and self.d == "powershell":
                e = self.emit(p, k + 1)
                name, value = ps_param(e, "Name", 0), ps_param(e, "Value", 1)
                if name and value:
                    self.ctx.aliases[name[0].lower()] = value[0]
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
                self.xargs = True
                repl = ""
                while k < n and v[k].startswith("-"):
                    w = v[k]
                    if w == "-I" and k + 1 < n:
                        repl = v[k + 1]
                    elif w.startswith("-I") and len(w) > 2:
                        repl = w[2:]
                    elif w == "-i" or w == "--replace":
                        repl = "{}"
                    elif w.startswith(("-i", "--replace=")):
                        repl = w.split("=", 1)[1] if w.startswith("--") else w[2:]
                    k += 2 if w in _XARGS_ARG else 1
                if k >= n:
                    self.emit("echo", n)
                    return self.made
                self.ctx.xargs.append(program_name(v[k], self.d, self.y[k]))
                if repl:                                       # RT-A: `-I {}`: the placeholder is a stdin item
                    old = list(v[k:])
                    self.v = v = list(v[:k]) + [w.replace(repl, "${XARGS_ITEM}") for w in old]
                    self.y = list(self.y[:k]) + [y or repl in w for w, y in zip(old, self.y[k:])]
                continue
            if p in ("source", ".") and self.d == "bash" or (p == "." and self.d == "powershell"):
                k += 1
                if k >= n:
                    self.emit(p, k)
                    return self.made
                if self.d == "bash" and v[k] in _STDIN_SCRIPTS:
                    return self._posix_shell("bash", k - 1)    # M4: `source /dev/stdin <<EOF`: the body runs
                self.emit(program_name(v[k], self.d, self.y[k]), k + 1)
                self._procsub(v[k])                             # RT-A: source <(echo ...) runs the echoed text
                return self.made
            if p == "eval" and self.d == "bash":
                self.emit(p, k + 1)
                if k + 1 < n:
                    code = self._code(k + 1)[0]                 # guard3: `c="rm x"; eval "$c"` runs rm x
                    self._decoded(code)
                    self.reparse(code, "bash")
                return self.made
            if p in POSIX_SHELLS:
                return self._posix_shell(p, k)
            if p in PS_SHELLS:
                return self._ps_shell(p, k)
            if p == "cmd":
                return self._cmd_shell(k)
            # Plan 8 T6 (T4 audit K6): `find` is ambiguous on Windows -- System32's find.exe searches text, Git's
            # usr/bin find deletes (-delete, -exec rm), and PATH order picks one. An ambiguous program is judged as
            # its destructive binary, on every dialect (it was bash only, so Codex's PowerShell let -delete through).
            if p == "find":
                return self._find(k)
            if p in ("fd", "fdfind"):
                return self._fd(k)
            if self.d == "powershell":
                if p == "invoke-expression":
                    self.emit(p, k + 1)
                    ex = self.made[-1]
                    sub = dict(self.ctx.subst)                  # guard3: `$c = 'x'; iex $c` runs x
                    lits = [sub.get(w, w) for w in ps_param(ex, "Command", 0)]
                    self._decoded(" ".join(v[k + 1:]))         # RT-A: iex ([Text.Encoding]::...FromBase64String(..))
                    if not ex.argv and self.stdin == "pipe" and "frombase64string" in self.ctx.root.lower():
                        self.ctx.unread = True                  # ... | iex, fed by a FromBase64String expression
                    for s in lits:
                        self.reparse(s, "powershell")
                    if not lits or not self._code(k + 1)[1]:   # iex $cmd, iex ('a' + 'b'): unknown text runs
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
            e = self.emit(p, k + 1)
            if p == "rename-item" and self.d == "powershell" or p == "rename" and self.d == "cmd":
                self._rename_as_move(e)
            return self.made

    def _rename_as_move(self, e: Exec) -> None:
        """RT-A: `Rename-Item b.txt a.esp` / cmd `ren b.txt a.esp` is a MOVE inside the source's own folder (the new
        name is a name, never a path): also emitted as `move-item <src> <folder>/<new name>`, so the copy and move rules
        judge it as one."""
        if e.program == "rename":
            words = [w for w in e.argv if not w.startswith("/")]
            src, new = (words[0], words[1]) if len(words) > 1 else ("", "")
        else:
            s = ps_param(e, "Path", 0) or ps_param(e, "LiteralPath")
            nn = ps_param(e, "NewName", 1)
            src, new = (s[0] if s else ""), (nn[0] if nn else "")
        if not src or not new:
            return
        head = re.split(r"[/\\]+", src.rstrip("/\\"))[:-1]
        dst = "/".join(head + [_basename(new)]) if head else _basename(new)
        m = Exec("move-item", (src, dst), (True, True), (), e.dialect, e.cd_before, e.stdin_from, e.segment, "",
                 e.subst)
        self.ctx.out.append(m)
        self.made.append(m)

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
                # Final review C1: every `o` / `O` in a cluster takes the NEXT word as its value, wherever it sits in
                # the cluster (bash and dash read `-euo pipefail`, `-oo a b` that way); zsh alone takes the rest of the
                # word when the `o` is not last (`-oerrexit`). `c` anywhere in a cluster, `-` or `+`, selects the first
                # operand after ALL the option words as the command string (bash and dash ignore the sign for c).
                opts = w[1:]
                if p == "zsh":
                    o = opts.find("o")
                    takes = 0 if o < 0 else 1 if o == len(opts) - 1 else 0
                    opts = opts if o < 0 else opts[:o]
                else:
                    takes = opts.count("o") + opts.count("O")
                if "c" in opts:
                    cflag = True
                if "s" in opts:
                    sflag = True
                j += 1 + takes
                continue
            break
        if not cflag and j < n and v[j] in _STDIN_SCRIPTS:
            sflag, j = True, j + 1                              # M4: `bash /dev/stdin`: commands from stdin
        if cflag:
            if j < n:
                code, certain = self._code(j, j + 1)        # guard3: `c="rm x"; bash -c "$c"` runs rm x
                self.ctx.codes.append((p, code))
                self._decoded(code)
                self.reparse(code, "bash")
                if not certain:
                    self.opaque(j)                          # guard3: `bash -c "$c" <operands>`: an unnamed program
            else:
                self.opaque(k)                      # `xargs -0 sh -c`: the command string comes from elsewhere
            self._carry(p, k + 1)
            return self.made
        if j < n and not sflag:
            self.emit(program_name(v[j], self.d, self.y[j]), j + 1)    # bash FILE args: FILE runs
            self._procsub(v[j])                                        # RT-A: bash <(echo ...)
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
                        self.ctx.codes.append((p, " ".join(rest)))
                        self._decoded(" ".join(rest))
                        self.reparse(_join_words(rest, "powershell"), "powershell")
                        self._carry(p, k + 1)
                    return self.made
                if name == "ec" or (name and "encodedcommand".startswith(name)):    # M1: -e -en -enc ... -ec
                    if j + 1 < n:
                        try:
                            text = base64.b64decode(v[j + 1], validate=True).decode("utf-16-le")
                        except Exception:
                            raise ParseError("an undecodable -EncodedCommand")
                        self.reparse(text, "powershell")
                        self._carry(p, k + 1)
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
                self.reparse(_join_words(v[j:], "powershell"), "powershell")
                self._carry(p, k + 1)
            else:
                self.emit(program_name(w, "powershell", self.y[j]), j + 1)
            return self.made
        self.emit(p, k + 1)
        return self.made

    def _cmd_shell(self, k):
        v, n = self.v, len(self.v)
        j = k + 1
        s_flag = False
        while j < n:
            lw = v[j].lower()
            head = lw[1:] if lw.startswith("//") else lw
            if head[:2] in ("/c", "/k", "/r"):
                rest = ([v[j][len(lw) - len(head) + 2:]] if len(head) > 2 else []) + list(v[j + 1:])
                line = _join_words(rest, "cmd")
                if len(rest) > 1 and line.lstrip().startswith('"'):
                    # Coordinator ruling on the lane-A probe: `cmd /s /c "rd /s /q "C:\x y\Data""`. The caller split the
                    # nested quotes into SEVERAL words; the line cmd really receives was rebuilt by the runtime (MSYS /
                    # PowerShell re-quote each word, MSYS also rewrites backslashes -- MEASURED) and cmd then strips
                    # its outer quotes. Not modelled exactly: an opaque exec holding the stripped line, so a delete
                    # word and a protected root in it refuse (S-DEL-FALLBACK), beside the modelled readings below.
                    e = self.opaque(k)
                    self.ctx.out[-1] = self.made[-1] = e._replace(argv=(_cmd_c_lines(line, True)[0],), quoted=(False,))
                for text in _cmd_c_lines(line, s_flag):
                    self.reparse(text, "cmd")
                self._carry("cmd", k + 1)
                return self.made
            if head == "/s":
                s_flag = True
            if lw.startswith("/"):
                j += 1
                continue
            break
        self.emit("cmd", k + 1)
        return self.made

    def _fd(self, k):
        """fix-unclear: `fd [opts] [pattern] [path...] -x|-X CMD`. fd always walks each search path recursively, and the
        pattern only narrows the matches, so the command it runs reaches every path's whole subtree: each {}-style
        placeholder (or, with none, the end of CMD) becomes the search paths themselves. Unknown flags take no value;
        a value-taking flag must be in _FD_VALUED, or its value is read as the pattern/a path (stricter, never looser)."""
        v, n = self.v, len(self.v)
        self.emit(program_name(v[k], self.d, self.y[k]), k + 1)
        j, pos, posq, sp, spq, cmds = k + 1, [], [], [], [], []
        while j < n:
            w = v[j]
            if w in ("-x", "--exec", "-X", "--exec-batch"):
                a = j = j + 1
                while j < n and v[j] not in (";", "\\;"):
                    j += 1
                cmds.append((a, j))
                j += 1
                continue
            if w == "--search-path" and j + 1 < n:              # a search path, never the pattern
                sp.append(v[j + 1])
                spq.append(self.q[j + 1])
                j += 2
                continue
            if w in _FD_VALUED:
                j += 2
                continue
            if w.startswith("-") and w != "-":
                j += 1
                continue
            pos.append(w)
            posq.append(self.q[j])
            j += 1
        paths, pq = pos[1:] + sp, posq[1:] + spq
        if not paths:
            paths, pq = ["."], [False]
        for a, b in cmds:
            iv, iq, iy, hit = [], [], [], False
            for x in range(a, b):
                if v[x] in _FD_PLACEHOLDERS:
                    iv, iq, iy, hit = iv + paths, iq + pq, iy + [False] * len(paths), True
                else:
                    iv.append(v[x])
                    iq.append(self.q[x])
                    iy.append(self.y[x])
            if iv and not hit:
                iv, iq, iy = iv + paths, iq + pq, iy + [False] * len(paths)
            if iv:
                if self.depth + 1 > MAX_DEPTH:
                    raise ParseError("nesting deeper than %d" % MAX_DEPTH)
                r = _Resolver("bash", iv, iq, iy, (), self.cd, "", self.seg, [], self.depth + 1, self.ctx,
                              self.recurse, False)
                self.made += r.run()
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
        items, negated = starts, False   # RT-A: what {} / -delete reach: the start, or start/NAME under a -name filter
        # fix-unclear (find above a root): a -name filter only NARROWS which files match; a recursive find still reaches
        # the start's whole subtree. So a DELETE (or a move away) also names the start itself, which the delete rules
        # judge as covering every protected root below it -- the same verdict as the filterless `find P -delete`.
        # Only `-maxdepth 0|1` provably stays at start/NAME (the start itself is then still a match candidate).
        deep = not any(v[x] == "-maxdepth" and v[x + 1] in ("0", "1") for x in range(j, n - 1))

        def reach(names, names_q):
            if names is starts or not deep:
                return list(names), list(names_q)
            # A relative start after a cd the parser could not follow (cd "?") is judged as start/NAME alone, like
            # `rm NAME` there (priced: `cd "$S/x"; find . -name '*.rej' -delete` would read as a whole unknown folder).
            keep = [i for i, s in enumerate(starts) if self.cd != "?" or _is_abs(s)]
            return list(names) + [starts[i] for i in keep], list(names_q) + [sq[i] for i in keep]
        while j < n:
            w = v[j]
            if w in ("!", "\\!", "-not", "-o", "-or", ","):
                negated, items = True, starts       # a negated or alternative filter: back to the whole start (stricter)
            elif w in ("-name", "-iname") and j + 1 < n and not negated and "/" not in v[j + 1]:
                items = [s.rstrip("/\\") + "/" + v[j + 1] for s in starts]
                j += 2
                continue
            if w == "-delete":
                ri, rq = reach(items, sq)
                e = Exec("find.delete", tuple(ri), tuple(rq), self.redirs, self.d, self.cd, self.stdin,
                         self.seg, "", self.ctx.subst)
                self.ctx.out.append(e)
                self.made.append(e)
                j += 1
                continue
            if w in ("-exec", "-execdir", "-ok", "-okdir"):
                j += 1
                iv, iq, iy = [], [], []
                from . import deletes                       # deferred: deletes imports this module
                runs = program_name(v[j], "bash") if j < n else ""
                ri, rq = (reach(items, sq) if runs in deletes.DELETE_PROGRAMS | deletes.MOVE_PROGRAMS
                          else (items, sq))
                while j < n and v[j] not in (";", "\\;") and not (v[j] == "+" and v[j - 1] == "{}"):
                    if v[j] == "{}":
                        iv += ri
                        iq += rq
                        iy += [False] * len(ri)
                    else:
                        iv.append(v[j])
                        iq.append(self.q[j])
                        iy.append(self.y[j])
                    j += 1
                j += 1
                if iv:
                    if self.depth + 1 > MAX_DEPTH:
                        raise ParseError("nesting deeper than %d" % MAX_DEPTH)
                    # Plan 8 T6: find runs the -exec program itself, by argv, never through the calling shell, so
                    # `rm` there is rm, not PowerShell's Remove-Item alias (whose `-f` would bind as -Filter).
                    r = _Resolver("bash", iv, iq, iy, (), self.cd, "", self.seg, [], self.depth + 1, self.ctx,
                                  self.recurse, False)
                    self.made += r.run()
                continue
            j += 1
        return self.made

    def _start_process(self, p, k):
        """Start-Process [-FilePath] F [-ArgumentList|-Args] a,b,...: F runs with every element joined by spaces
        (as Start-Process does), re-parsed as a Windows command line so `cmd /c` / `pwsh -c` inside it are seen."""
        e = self.emit(p, k + 1)
        v, q, y = e.argv, e.quoted, self.y[k + 1:]
        fp, args, pos, j = None, None, [], 0
        while j < len(v):
            w = v[j]
            if w.startswith("-") and len(w) > 1 and not q[j]:
                name = w[1:].split(":", 1)[0].lower()
                if name and ("filepath".startswith(name) or name in ("path", "pspath")):
                    fp = (v[j + 1], y[j + 1]) if j + 1 < len(v) else ("", False)
                    j += 2
                    continue
                if name and ("argumentlist".startswith(name) or name == "args"):
                    args = []
                    j += 1
                    while j < len(v) and not (v[j][:1] == "-" and len(v[j]) > 1 and not q[j]):
                        args.append((v[j], y[j]))               # every comma-separated element
                        j += 1
                    continue
                j += 1 if any(s.startswith(name) for s in _SAPS_SWITCHES) else 2
                continue
            pos.append((w, y[j]))
            j += 1
        if fp is None and pos:
            fp, pos = pos[0], pos[1:]
        if args is None:
            args = pos[:1]
        if fp is None:
            return self.made
        words = []
        for a, dyn in args:
            if a.startswith("@(") and a.endswith(")"):          # -ArgumentList @('/c', 'rd /s /q X')
                elems = self._ps_array(a[2:-1])
                if elems is None:
                    self.opaque(k + 1)
                    return self.made
                words += elems
            elif dyn:                                           # -ArgumentList $args: not known
                self.opaque(k + 1)
                return self.made
            else:
                words.append(a)
        sub = dict(self.ctx.subst)                              # guard3: `$g = 'C:\x'; Start-Process "$g\a.exe"`
        if fp[1] and fp[0] in sub and "$" not in sub[fp[0]] and "`" not in sub[fp[0]]:
            fp = (sub[fp[0]], False)
        if fp[1] or "$" in fp[0] or "(" in fp[0]:
            self.opaque(k + 1)                                  # the program itself is not known
            return self.made
        self.reparse('"' + fp[0].replace('"', "") + '" ' + " ".join(words), "cmd")
        return self.made

    def _ps_array(self, inner: str) -> Optional[List[str]]:
        """The elements of a literal PowerShell array `@( 'a', 'b' )`, or None if any element is not a literal."""
        if self.depth + 1 > MAX_DEPTH:
            raise ParseError("nesting deeper than %d" % MAX_DEPTH)
        sc = _Pwsh(inner, self.depth + 1, self.cd, self.ctx)
        sc.run()
        words = [w for r in sc.recs for w in r.words]
        if sc.synth or any(w.dyn for w in words) or any(r.redirs or r.heredocs for r in sc.recs):
            return None
        return ["".join(w.b) for w in words]


def _subst(sc: _Scanner, rec: _Cmd, ws, vals) -> Tuple[Tuple[str, str], ...]:
    """Exec.subst (Task 7b): (word as in argv, the same word with its variables replaced by the literal each one
    certainly holds here). A word value spelled twice with different readings (`'$D'` and `$D`) is left out."""
    if sc.unsure or sc.ctx.poisoned():
        return ()                                   # fix round 1: when in doubt, nothing in the command resolves
    got: dict = {}
    split_safe = sc.DIALECT != "bash" or sc.ctx.writes("IFS") == 0
    # fix-unclear 4b: a redirect target is a word too (`P=X.esp; echo x > "$P"`), resolved by the same rules.
    for w, val in list(zip(ws, vals)) + [(w, "".join(w.b)) for w in rec.redir_words]:
        if "$" not in val:
            continue
        s = assign.expand_word(w.raw, sc.DIALECT, lambda name: sc.lookup(rec, name), split_safe)
        got[val] = s if got.get(val, s) == s else None
    return tuple((k, v) for k, v in got.items() if v is not None and v != k)


_EVAL_WORD = re.compile(r"""["']?\$\{?[A-Za-z_][A-Za-z0-9_]{0,255}\}?["']?""")
_PS_EVALS = frozenset(("iex", "invoke-expression"))


def _eval_word(sc: _Scanner, idx: int, rec: _Cmd) -> Optional[_Word]:
    """guard3: the one variable word an eval-like statement runs (`eval "$c"`, `iex $c`, `Invoke-Expression -Command
    $c`, `$c | iex`), or None: the shapes assign.poisoned() defers."""
    ws = rec.words
    if sc.DIALECT == "bash":
        if len(ws) == 2 and not ws[0].dyn and ws[0].raw == "eval" and ws[1].dyn and _EVAL_WORD.fullmatch(ws[1].raw):
            return ws[1]
        return None
    if not ws or ws[0].dyn or ws[0].quoted or "".join(ws[0].b).lower() not in _PS_EVALS:
        return None
    rest = ws[1:]
    if rest and not rest[0].dyn and len("".join(rest[0].b)) >= 2 and "-command".startswith("".join(rest[0].b).lower()):
        rest = rest[1:]
    if len(rest) == 1 and rest[0].dyn and _EVAL_WORD.fullmatch(rest[0].raw):
        return rest[0]
    if not rest and rec.stage == 1 and idx > 0:
        up = sc.recs[idx - 1]
        if up.key == rec.key and up.stage == 0 and len(up.words) == 1 and up.words[0].dyn and \
                _EVAL_WORD.fullmatch(up.words[0].raw):
            return up.words[0]
    return None


def _settle_evals(sc: _Scanner) -> None:
    """guard3: lift the eval poison only when EVERY deferred eval-of-one-variable statement is a record of this root
    command whose variable certainly holds a literal (lookup, the same rules as Exec.subst) that is itself no poison.
    Anything less turns all resolution off, as an eval always did (assign.POLICY)."""
    ctx = sc.ctx
    if ctx.poisoned() or not ctx.evals:
        return
    split_safe = sc.DIALECT != "bash" or ctx.writes("IFS") == 0
    found = 0
    for idx, rec in enumerate(sc.recs):
        w = _eval_word(sc, idx, rec)
        if w is None:
            continue
        found += 1
        val = None if sc.unsure or not sc.vars else assign.expand_word(
            w.raw, sc.DIALECT, lambda name, rec=rec: sc.lookup(rec, name), split_safe)
        if val is None or "$" in val or "`" in val or assign.poisoned(val, sc.DIALECT):
            ctx.poison = "eval"
            return
    if found != len(ctx.evals):
        ctx.poison = "eval"                         # an eval the records do not show (in a string, a nested shell)


def _records_to_execs(sc: _Scanner, recurse: bool = True) -> None:
    d, t, ctx = sc.DIALECT, sc.t, sc.ctx
    if sc.depth == 0:
        _settle_evals(sc)
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
        quoted = [_ps_quoted(w) for w in ws] if d == "powershell" else [w.quoted for w in ws]
        dyn = [w.dyn for w in ws]
        saved, ctx.subst = ctx.subst, (_subst(sc, rec, ws, vals) if sc.vars else ())
        saved_lits, ctx.lits = ctx.lits, {vals[j]: _raw_literal(w.raw, d) for j, w in enumerate(ws) if w.dyn}
        res = _Resolver(d, vals, quoted, dyn, rec.redirs, rec.cd, stdin, seg, bodies, sc.depth, ctx, recurse, rec.call)
        if d == "powershell":
            res.strq = [_ps_string(w) for w in ws]
        made = res.run()
        if d == "bash" and any(w.bs for w in ws):                     # R6: the literal reading too
            made = made + _Resolver(d, ["".join(w.l) for w in ws], quoted, dyn, rec.redirs, rec.cd, stdin, seg,
                                    bodies, sc.depth, ctx, False, rec.call).run()
        ctx.subst, ctx.lits = saved, saved_lits
        groups.setdefault(rec.key, []).append((rec, made))
    for _, e in synth[si:]:
        ctx.out.append(e)
    if not recurse:
        return
    for items in groups.values():                                    # cat <<EOF | bash, echo cmd | sh
        if len(items) < 2:
            continue
        for ci, (crec, cmade) in enumerate(items):
            if ci == 0:
                continue
            readers = [e for e in cmade if e.stdin_from == "pipe" and id(e) not in ctx.via_xargs and (
                e.program in ALL_SHELLS or (consumer_kind(e.program) == "interpreter" and _reads_code(e.argv)) or
                _ps_reads_commands(e))]
            if not readers:
                continue
            reader = readers[0]
            if (reader.program in ALL_SHELLS or _ps_reads_commands(reader)) and \
                    any(_is_decoder(e) for _, umade in items[:ci] for e in umade):
                ctx.unread = True                   # RT-A: `echo .. | base64 -d | sh`, `printf '\x72\x6d ..' | sh`
            texts, known = [], True                 # the upstream text, if it can be rebuilt statically
            strings = []                            # RT-A G1: a PowerShell string stage, `@'..'@ | python -`
            partial = False                         # guard3: rebuilt text still holding an unresolved variable
            for urec, umade in items[:ci]:
                texts += [h.body or "" for h in urec.heredocs]
                if d == "powershell" and not umade and len(urec.words) == 1 and _ps_quoted(urec.words[0]):
                    strings.append("".join(urec.words[0].b))
                    if urec.words[0].dyn:
                        known = False               # I6: `"Remove-Item $x" | iex`: the text holds a variable's value
                elif d == "powershell" and not umade and len(urec.words) == 1 and urec.words[0].dyn:
                    # guard3 (v4.0.0 review MEDIUM-2): `$c | iex` runs the text $c holds: its literal when certain
                    # (Exec.subst), else text the parser never saw.
                    val = dict(_subst(sc, urec, urec.words, ["".join(urec.words[0].b)]) if sc.vars else ()).get(
                        "".join(urec.words[0].b))
                    if val is None or "$" in val or "`" in val:
                        known = False
                    else:
                        strings.append(val)
                for e in umade:
                    if e.program in _TEXT_SOURCES and not (e.program == "echo" and e.argv and _ECHO_OPTS.match(e.argv[0])):
                        if e.argv:
                            sub = dict(e.subst)             # guard3: `Write-Output $c | iex` with $c a literal
                            words = [sub.get(a, a) for a in e.argv]
                            partial = partial or any("$" in a or "`" in a for a in words)
                            texts.append(" ".join(words))
                    elif not urec.heredocs:          # printf, echo -e, cat FILE, curl ...: not reconstructable
                        known = False
            if known and partial and (reader.program in ALL_SHELLS or _ps_reads_commands(reader)):
                # guard3: `Write-Output $c | iex`, `echo "$c" | sh`: the text is read as far as it goes (below), and the
                # variable's part of it is a program the guard cannot name.
                unknown = reader._replace(program="?", argv=(reader.program,), quoted=(False,))
                ctx.out.append(unknown)
                ctx.unnamed.append(unknown)
            if not known:                    # `cat s.sh | bash`, `printf ... | sh`: code the parser never saw
                unknown = reader._replace(program="?", argv=(reader.program,), quoted=(False,))
                ctx.out.append(unknown)
                ctx.unnamed.append(unknown)     # guard3: no candidate either (S-OPAQUE-PROTECTED, S-SAVE-OPAQUE)
            elif reader.program in ALL_SHELLS or _ps_reads_commands(reader):
                # I6: a PowerShell string stage is text too (`'Remove-Item X' | pwsh -Command -`, `'...' | iex`).
                dialect = "powershell" if reader.program == "invoke-expression" else _shell_dialect(reader.program)
                for text in texts + strings:
                    _sub(text, dialect, sc.depth + 1, crec.cd, ctx)
            else:                            # literal code into python/node/...: R7(b), the legacy text rules
                for j in range(len(ctx.out) - 1, -1, -1):
                    if ctx.out[j] is reader:
                        ctx.out[j] = reader._replace(stdin_body="\n".join(texts + strings))
                        break
            break        # only the FIRST reader reads the upstream text; a later one reads that reader's output


_TEXT_SOURCES = frozenset(("echo", "write-output", "write-host", ""))   # printf is NOT emulated
_STDIN_SCRIPTS = frozenset(("/dev/stdin", "/proc/self/fd/0", "/dev/fd/0"))   # M4: a script file that IS stdin
_ECHO_OPTS = re.compile(r"-[neE]*[eE][neE]*$")                       # echo -e / -ne: escapes, not literal text
_CODE_OPTS = frozenset(("-c", "-e", "-E", "-m", "-p", "-r", "--eval", "--print", "--command"))


def _ps_reads_commands(e: Exec) -> bool:
    """I6: `... | Invoke-Expression` with no command of its own runs its pipeline input as PowerShell commands."""
    return e.program == "invoke-expression" and e.dialect == "powershell" and not e.argv


def _reads_code(argv) -> bool:
    """Whether an interpreter takes its PROGRAM from stdin (`python`, `python -`, `node`), rather than from -c/-e
    or a script file (then stdin is data)."""
    for a in argv:
        if a in _CODE_OPTS:
            return False
        if a == "-":
            return True
        if not a.startswith("-"):
            return False
    return True
_SCANNERS = {"bash": _Bash, "powershell": _Pwsh, "cmd": _Cmdexe}


def _sub(text: str, dialect: str, depth: int, cd: str, ctx: _Ctx, mode: str = "n") -> None:
    """Parse `text` (itself a command line) at nesting `depth`; its execs go to ctx.out."""
    if depth > MAX_DEPTH:
        raise ParseError("nesting deeper than %d" % MAX_DEPTH)
    sc = _SCANNERS[dialect](text, depth, cd, ctx, mode)
    sc.run()
    _records_to_execs(sc)


def _sub_speculative(text: str, dialect: str, depth: int, cd: str, ctx: _Ctx) -> None:
    """_sub for text that MAY be code (what an eval-like wrapper would run), and usually is not: `"$PY" -c "<python>"`.
    It never ends the parse and never marks it incomplete -- an incomplete parse sends many rules to their stricter text
    fallbacks, so every such python one-liner would pay for a reading that is only a possibility. The commands it
    completed before a parse error are still judged (`$R 'rm -rf x; ((('` keeps the rm)."""
    if MAX_DEPTH < depth:                       # the same nesting bound as _sub (spelled apart: _sub's line is an anchor)
        raise ParseError("nesting deeper than %d" % MAX_DEPTH)
    sc = _SCANNERS[dialect](text, depth, cd, ctx, "n")
    try:
        sc.run()
    except ParseError:
        pass
    try:
        _records_to_execs(sc)
    except ParseError:
        pass


def _parse_one(command: str, dialect: str) -> Parsed:
    ctx = _Ctx(command, dialect)
    try:
        _sub(command, dialect, 0, "", ctx)
        return Parsed(tuple(ctx.out), True, "", ctx.unread, tuple(ctx.codes), tuple(ctx.xargs), tuple(ctx.unnamed))
    except ParseError as e:
        return Parsed(tuple(ctx.out), False, "%s: %s" % (dialect, e), ctx.unread, tuple(ctx.codes), tuple(ctx.xargs),
                      tuple(ctx.unnamed))
    except Exception as e:  # a bug here must read as "could not parse", never as an allow
        return Parsed(tuple(ctx.out), False, "internal error: %s: %s" % (type(e).__name__, e), ctx.unread,
                      tuple(ctx.codes), tuple(ctx.xargs), tuple(ctx.unnamed))


def parse(command: str, shell: str = "") -> Parsed:
    """The commands `command` executes. Never raises. See the module docstring."""
    try:
        if not isinstance(command, str):
            return Parsed((), False, "the command is not a string")
        if len(command) > MAX_INPUT_CHARS:      # input cap: the legacy rules (linear) take over
            return Parsed((), False, "the command is larger than %d characters" % MAX_INPUT_CHARS)
        d = _dialect(shell)
        if d is not None:
            return _parse_one(command, d)
        a, b = _parse_one(command, "bash"), _parse_one(command, "powershell")
        return Parsed(a.execs + b.execs, a.ok and b.ok, "; ".join(x for x in (a.why, b.why) if x),
                      a.unread or b.unread, a.codes + b.codes, a.xargs + b.xargs, a.unnamed + b.unnamed)
    except Exception as e:
        return Parsed((), False, "internal error: %s: %s" % (type(e).__name__, e))
