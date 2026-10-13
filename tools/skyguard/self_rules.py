r"""Agent self-protection (Plan 6 Task 12, R12): the guards' own state, code and config.

    kind_of(abs_path, roots, env)                     -> ("state" | "guard" | "", basename)
    shell_scan(command, parsed, roots, cwd, env)      -> ("state" | "guard", basename, op) | None
    text_kind(path)                                   -> the same answer from the path's words alone (no roots)

Two protected classes, both decided on where a path RESOLVES (targets), never on words in the command:

  STATE  `.skyguard/**` in the install: the on/off state (state.json) and the lock manifest. Only tools/skyguard writes
         it, so any other write is refused (S-SELF-STATE / W-SELF-STATE). The CLI's own run is never a write primitive
         here, so `bash tools/skyguard.sh guards off` is S-LIFT-NOTE, not this.
  GUARD  the guard code and config: `.claude/hooks/**`, `.claude/settings*.json`, `skyrim-paths.env` at the install root
         and its legacy `.claude/skyrim-paths.env` (red team F5: the declared roots; Plan 8 T8), `.codex/**`,
         `tools/skyguard/**`, `agent/guards/**`, the telemetry the canary and shadow mode read (`.claude/backups/.hook-heartbeat/**`, `.claude/backups/AUDIT_LOG.txt`; Task 9 review (e)),
         and the user-level `~/.claude/settings*.json` and `~/.codex/config.toml` (S-SELF-GUARD / W-SELF-GUARD, priced).

A path counts in either install folder the roots know (the hook's own root and a declared game root). A scratch copy
of an install (C:/Temp/x/.skyguard) is not the live guard and is not protected.

THE SHELL ROUTES (shell_scan), each operand resolved against the payload cwd:
  - every write primitive writes.py knows (redirects, tee, Set-Content, Out-File, sed -i, dd of=, [IO.File]::Write...);
  - a copy's destination (cp, Copy-Item, robocopy, cmd copy ...): a folder destination counts as the folder;
  - a MOVE's source as well (mv, Move-Item, Rename-Item, cmd move/ren, [IO.File]::Move): moving a hook away disables it
    as surely as deleting it (red team F2), and a move of a folder that holds guard files (`mv .claude x`) counts;
  - `ln` (its link name), `truncate`, `chmod`/`chown`, an ACL rewrite (icacls with a modifying switch, Set-Acl): an
    unreadable or emptied hook fails open (red team F1);
  - `git checkout`/`git restore` with a pathspec that is, or holds, a guard file (red team F3);
  - T8 fix round 1: a create-without-content program (`touch`, `install`'s destination, cmd `mklink`'s link name), a
    link whose SOURCE is a guard file (`ln`, `mklink`, New-Item -ItemType HardLink/SymbolicLink/Junction), and an NTFS
    stream spelling of either class (`x::$DATA`, read as x like the plugin rules' targets.STREAM);
  - T8 fix round 2: `cp -l/--link/-s/--symbolic-link` (a copy that is an alias), `fsutil hardlink create`, New-Item
    `-Type` (the alias of -ItemType), and the .NET [IO.File]/[IO.Directory]::CreateSymbolicLink/CreateHardLink calls.
    The S-SELF-GUARD note names the verb: "edits", "creates" or "makes a link to".
NOT covered (residue, R6): interpreter code that writes these files (`python -c "open(...).write"`), a branch switch
(`git checkout <branch>` rewrites tracked hooks with no pathspec), and environment tamper (setx, red team F7).
"""
from __future__ import annotations

import ast
import fnmatch
import io
import os
import tokenize
import re
from typing import List, Mapping, Optional, Tuple

from . import paths, targets

# The shell-route helpers are imported where they are used: the write guard (write_rules -> kind_of / text_kind)
# must load without the shell parser, as an old engine tree does (tests/test_price_tool.py).

STATE = "state"
GUARD = "guard"


def _rel(abs_path: str, dirs) -> Optional[List[str]]:
    p, best = (abs_path or "").lower().rstrip("/"), None
    for d in dirs:
        dl = d.lower().rstrip("/")
        if dl and (p == dl or p.startswith(dl + "/")) and (best is None or len(dl) > len(best)):
            best = dl
    if best is None:
        return None
    return [c for c in p[len(best):].split("/") if c]


def _homes(env: Mapping[str, str]) -> List[str]:
    """Where `~` points (HOME and USERPROFILE, both, as either may be the one a shell expands)."""
    out = []
    for n in ("HOME", "USERPROFILE"):
        v = (env or {}).get(n) or ""
        a = paths.norm_abs(v) if v else None
        if a and a not in out:
            out.append(a)
    return out


def _settings(name: str) -> bool:
    return fnmatch.fnmatchcase(name, "settings*.json")


def install_kind(rel: List[str]) -> str:
    """The class of a path given as its components below the install folder (lower-case)."""
    if not rel:
        return ""
    if rel[0] == ".skyguard":
        return STATE
    if rel[0] == ".codex":
        return GUARD
    if rel[:2] in ([".claude", "hooks"], ["tools", "skyguard"], ["agent", "guards"]) and len(rel) >= 2:
        return GUARD
    if rel[0] == ".claude" and len(rel) == 2 and (_settings(rel[1]) or rel[1] == "skyrim-paths.env"):
        return GUARD
    if rel == ["skyrim-paths.env"]:                  # Plan 8 T8: the paths file's own location, the toolkit root
        return GUARD
    if rel[:3] == [".claude", "backups", ".hook-heartbeat"] or rel == [".claude", "backups", "audit_log.txt"]:
        return GUARD
    return ""


def user_kind(rel: List[str]) -> str:
    """The class of a path given as its components below a home folder: the user-level hook switches."""
    if len(rel) == 2 and rel[0] == ".claude" and _settings(rel[1]):
        return GUARD
    if rel == [".codex", "config.toml"]:
        return GUARD
    return ""


# A folder ABOVE guard files: moving or restoring it moves or restores them (`mv .claude x`, `git checkout -- tools`).
_HOLDERS = ([".claude"], [".claude", "backups"], ["tools"], ["agent"])


def kind_of(abs_path: str, roots, env: Mapping[str, str], holder: bool = False) -> str:
    """STATE / GUARD / "" for an absolute path. holder=True also counts a folder that HOLDS guard files (a move's source,
    a git pathspec): the install folder itself, `.claude`, `tools`, `agent`."""
    if not abs_path:
        return ""
    rel = _rel(abs_path, roots.install_dirs) if roots is not None else None
    if rel is not None:
        k = install_kind(rel)
        if k or not holder:
            return k
        return GUARD if (rel == [] or rel in _HOLDERS) else ""
    for h in _homes(env):
        r = _rel(abs_path, [h])
        if r is not None:
            return user_kind(r)
    return ""


# ---- the text fallback (no roots: an old caller, or Codex with an unreadable paths file) ---------------------------
def text_kind(path: str) -> str:
    """The class from the path's own components: `.skyguard` anywhere is STATE; a guard pattern anywhere is GUARD."""
    comps = [c for c in re.split(r"[/\\]+", (path or "").lower()) if c]
    if ".skyguard" in comps[:-1] or (comps and comps[-1] == ".skyguard"):
        return STATE
    for i in range(len(comps)):
        # At most 4 components: install_kind never looks further, and a length of 4 still fails its `== 2` / `== 3`
        # tests as the full tail would. The full tail made this quadratic (test_skyguard_timing, fix round 1).
        k = install_kind(comps[i:i + 4])
        if k == GUARD:
            return k
    return ""


# ---- the shell routes ----------------------------------------------------------------------------------------------
MOVE_PROGRAMS = frozenset(("mv", "move-item", "io.file.move", "io.file.replace"))
_CMD_MOVE = frozenset(("move", "ren", "rename"))
_ICACLS_WRITE = ("/grant", "/deny", "/remove", "/reset", "/setowner", "/inheritance", "/setintegritylevel")
_CHMOD_VALUED = frozenset(("--reference",))


def _plain(argv) -> List[str]:
    return [w for w in argv if not (w.startswith("-") and len(w) > 1)]


# ---- T8 fix round 1 ---------------------------------------------------------------------------------------------------
# I1: programs that CREATE a file with no content (or only stamp it): writes.py has no write primitive for them, so they
# reached no rule. An empty hook fails open (red team F1); an empty paths file declares nothing.
_TOUCH_VALUED = frozenset(("-d", "-t", "-r", "--date", "--reference"))
_INSTALL_VALUED = frozenset(("-m", "--mode", "-o", "--owner", "-g", "--group", "-S", "--suffix", "-t",
                             "--target-directory", "--strip-program"))
# P2: a link whose SOURCE is a guard file is an alias outside the install that edits the guard with no protected path
# in any later command. The plugin rules do not refuse a plugin link, so the priced classes apply (GUARD note,
# STATE deny), never a new refusal.
_PS_LINK_TYPES = ("hardlink", "symboliclink", "junction")
# Review N4 (fix round 2): the note's verb says what the route does to the guard file.
EDITS, CREATES, LINKS = "edits", "creates", "makes a link to"


def _touch_operands(argv) -> List[str]:
    out, j = [], 0
    while j < len(argv):
        w = argv[j]
        if w in _TOUCH_VALUED:
            j += 1                                       # its value (a date, a reference file READ) is no target
        elif not (w.startswith("-") and len(w) > 1):     # `--` and options; no guard file's name starts with `-`
            out.append(w)
        j += 1
    return out


def _install_targets(argv) -> List[str]:
    """`install SRC DEST`, `install SRC... -t DIR` (DIR/<basename>), `install -d DIR...` (each one created)."""
    words, tdir, dirs, j = [], None, False, 0
    while j < len(argv):
        w = argv[j]
        if w in _INSTALL_VALUED:
            if w in ("-t", "--target-directory") and j + 1 < len(argv):
                tdir = argv[j + 1]
            j += 2
            continue
        if w.startswith("--target-directory="):
            tdir = w.split("=", 1)[1]
        elif w in ("-d", "--directory"):
            dirs = True
        elif not (w.startswith("-") and len(w) > 1):
            words.append(w)
        j += 1
    if dirs:
        return words
    if tdir is not None:
        return [tdir.rstrip("/\\") + "/" + (re.split(r"[/\\]+", s.rstrip("/\\"))[-1]) for s in words]
    return words[-1:] if len(words) >= 2 else []


# P1: an NTFS stream suffix names the SAME file (`x::$DATA` is x's own data, `x:s` a stream stored in x), as
# targets.STREAM reads it for the plugin rules. A resolved path already drops it (paths._fold); this is for the word the
# resolver cannot place because of the suffix itself (`$DATA` reads as a variable). Not a drive spelling (`C:x`, `C:`).
_STREAM_TAIL = re.compile(r"^(.*[/\\])?([^/\\:]+):[^/\\]*$")


def strip_stream(word: str) -> Optional[str]:
    """`word` without an NTFS stream suffix on its last component, or None when it has none."""
    m = _STREAM_TAIL.match(word or "")
    if not m or (m.group(1) is None and len(m.group(2)) == 1):
        return None
    return (m.group(1) or "") + m.group(2)


def resolve_own(resolve, word: str) -> Optional[str]:
    """resolve(word), else resolve(word without its stream suffix): the path a self-protection check judges."""
    a = resolve(word)
    if a is None:
        s = strip_stream(word)
        if s is not None:
            a = resolve(s)
    return a


def _short(w: str, letters: str) -> bool:
    """A short-option cluster (`-al`, not `--link`) holding any of `letters`."""
    return w.startswith("-") and not w.startswith("--") and len(w) > 1 and any(c in w[1:] for c in letters)


def _cp_links(argv) -> bool:
    """`cp -l` / `--link` (hard links) or `-s` / `--symbolic-link`: the copy is an ALIAS of its source, not a copy."""
    return any(w in ("--link", "--symbolic-link") or _short(w, "ls") for w in argv)


def _routes(e) -> List[Tuple[str, bool, str]]:
    """(word, holder, op) for every operand this exec writes, replaces, moves away or re-permissions -- or, T8 fix
    rounds 1-2, creates empty or makes a link to. `op` is the verb the S-SELF-GUARD note uses (review N4)."""
    from . import writes
    from .shellparse import ps_param
    out = [(w, False) for w in writes.write_targets(e)]
    for dst, srcs in writes.copy_targets(e):
        if e.program in MOVE_PROGRAMS or (e.program in _CMD_MOVE and e.dialect == "cmd"):
            out += [(s, True) for s in srcs]             # the source first: the message names what was moved away
        out.append((dst, False))
    p, argv = e.program, e.argv
    # Merge of master into plan6: `Rename-Item` (emitted by the parser as a move-item since rt-fix-a, its source taken
    # as moved away by the copy_targets loop above) and `truncate` (a writes.write_targets target since rt-fix-b) are
    # read by writes.py; this rule's own branches for them only duplicated it.
    if p in ("ren", "rename") and e.dialect == "cmd":
        words = [w for w in argv if not w.startswith("/")]
        out += [(w, True) for w in words[:1]]
    elif p == "ln":
        words = _plain(argv)
        if len(words) >= 2:
            out.append((words[-1], False))
        out += [(w, False, LINKS) for w in (words[:-1] if len(words) >= 2 else words)]   # P2: the link's source(s)
    elif p == "touch":
        out += [(w, False, CREATES) for w in _touch_operands(argv)]                      # I1
    elif p == "install":
        out += [(w, False) for w in _install_targets(argv)]                              # I1 (a copy: it writes)
    elif p == "mklink":
        words = [w for w in argv if not w.startswith("/")]
        out += [(w, False, CREATES) for w in words[:1]]                                  # I1 the link name
        out += [(w, False, LINKS) for w in words[1:2]]                                   # P2 its target (the source)
    elif p == "new-item" and any(t.lower() in _PS_LINK_TYPES
                                 for t in list(ps_param(e, "itemtype")) + list(ps_param(e, "type"))):
        out += [(w, False, LINKS) for w in list(ps_param(e, "target")) + list(ps_param(e, "value"))]   # P2
    elif p == "fsutil" and [w.lower() for w in argv[:2]] == ["hardlink", "create"]:
        out += [(w, False, CREATES) for w in argv[2:3]]                                  # r2 N2: the new name...
        out += [(w, False, LINKS) for w in argv[3:4]]                                    # ...and the existing file
    elif p in ("io.file.link", "io.directory.link"):
        out += [(w, False, CREATES) for w in argv[:1]] + [(w, False, LINKS) for w in argv[1:2]]   # r2 N2: .NET
    elif p == "cp" and _cp_links(argv):
        rec = any(_short(w, "aRr") or w in ("--recursive", "--archive") for w in argv)
        out += [(s, rec, LINKS) for _, srcs in writes.copy_targets(e) for s in srcs]     # r2 N2: cp -l / -s
    elif p in ("chmod", "chown", "chgrp"):
        words = [w for w in argv if not w.startswith("-") and not w.startswith("--reference")]
        out += [(w, False) for w in words[1:]]
    elif p == "icacls" and argv and not argv[0].startswith("/"):
        if any(w.lower().startswith(_ICACLS_WRITE) for w in argv[1:]):
            out.append((argv[0], False))
    elif p == "set-acl":
        out += [(w, False) for w in list(ps_param(e, "path", 0)) + list(ps_param(e, "literalpath"))]
    elif p == "git":
        out += [(w, True) for w in _git_restore(argv)]
    return [r if len(r) == 3 else (r[0], r[1], EDITS) for r in out]


def _git_restore(argv) -> List[str]:
    """The pathspecs of `git checkout [<tree-ish>] -- <paths>` / `git restore <paths>`: the working files they
    overwrite. `git checkout <branch>` (no `--`) is a branch switch, not covered (residue)."""
    k = 0
    while k < len(argv) and argv[k].startswith("-"):
        k += 2 if argv[k] in ("-C", "-c", "--git-dir", "--work-tree") else 1
    if k >= len(argv):
        return []
    sub, rest = argv[k], list(argv[k + 1:])
    if sub == "checkout":
        if "--" not in rest:
            return []
        return [w for w in rest[rest.index("--") + 1:]]
    if sub == "restore":
        if "--staged" in rest and "--worktree" not in rest and "-W" not in rest:
            return []
        out, valued = [], ("-s", "--source", "--pathspec-from-file")
        j = 0
        while j < len(rest):
            w = rest[j]
            if w in valued:
                j += 2
                continue
            if w != "--" and not w.startswith("-"):
                out.append(w)
            j += 1
        return out
    return []


# ---- deletes into the state (fix round 1, I1) ------------------------------------------------------------------------
# S-DEL-INSTALL already refuses these, but guards off shadows it: `rm .skyguard/lock.json` then went through, and with no
# lock manifest the next session start never relocks (a persistent off nobody approved). So a delete that reaches the
# state -- a file in it or the folder itself -- is also S-SELF-STATE, never shadowed. (A delete of the whole install
# stays S-DEL-INSTALL: it takes the game with it.)
_QUOTED = re.compile(r"'([^'\n]{1,400})'|\"([^\"\n]{1,400})\"")
# The Recycle-Bin delete has no Exec, so it is a text construct whose FIRST argument is the path. Merge of master into
# plan6 (2026-10-05): read through deletes.RECYCLE_CALL (Plan 5 final review D1), the ONE definition -- it also knows
# Shell.Application's Namespace(10).MoveHere, escaped quotes and bare arguments, which this rule's own regex did not, so
# such a delete of .skyguard/lock.json reached only S-DEL-INSTALL, which guards off shadows.


def _reaches_state(a: str, roots) -> bool:
    if roots is None or not a:
        return False
    rel = _rel(a, roots.install_dirs)
    return rel is not None and rel[:1] == [".skyguard"]     # the folder or a file in it; not the install itself


def _hit_name(a: str, w: str) -> str:
    rel_name = re.split(r"[/\\]+", (a or w).rstrip("/\\"))[-1] or w
    return rel_name if ".skyguard" in (a or w).lower() else ".skyguard"


def _python_inspection_text(program: str, code: str) -> str:
    """Mask literals only in proven parser inspection; unsupported code keeps the original scan.

    This is deliberately a small grammar, not Python execution analysis. Only
    print and an exactly imported shellparse.parse can run. Store contexts include
    loop/comprehension bindings, so neither accepted callable can be rebound.
    """
    from . import deletes
    if not deletes._PYTHON.fullmatch(program):
        return code
    if not code.isascii():
        return code
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError):
        return code
    permitted = {ast.Module, ast.ImportFrom, ast.Assign, ast.For, ast.Expr,
                 ast.List, ast.Tuple, ast.Constant, ast.Name, ast.Attribute,
                 ast.Call, ast.ListComp, ast.Load, ast.Store, ast.alias, ast.comprehension}
    nodes = list(ast.walk(tree))
    if any(type(node) not in permitted for node in nodes):
        return code
    imported = False
    for node in nodes:
        if isinstance(node, ast.ImportFrom):
            if not (node.level == 0 and node.module == "skyguard" and len(node.names) == 1
                    and node.names[0].name == "shellparse" and node.names[0].asname is None):
                return code
            imported = True
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id in {"print", "shellparse"}:
            return code
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            return code
    for node in nodes:
        if isinstance(node, ast.Call):
            callee = node.func
            if not ((isinstance(callee, ast.Name) and callee.id == "print")
                    or (imported and isinstance(callee, ast.Attribute) and callee.attr == "parse"
                        and isinstance(callee.value, ast.Name) and callee.value.id == "shellparse")):
                return code
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(code).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return code
    offsets = [0]
    for line in code.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    masked = list(code)
    for token in tokens:
        if token.type in (tokenize.STRING, tokenize.COMMENT):
            start = offsets[token.start[0] - 1] + token.start[1]
            end = offsets[token.end[0] - 1] + token.end[1]
            for index in range(start, end):
                if masked[index] not in "\r\n":
                    masked[index] = " "
    return "".join(masked)


# ---- printed text that runs (release review T13 I1) -----------------------------------------------------------------
# c8b365f masked an inspection's printed literals as inert. They are inert only when nothing in the command can RUN
# them: `| powershell -Command -`, `| iex`, `powershell -Command "$(...)"`, `> x.ps1; powershell -File x.ps1` all run the
# printed text as code. The structure of pipes and substitutions is not reliable enough to follow (a substitution's
# exec appears beside the one it feeds, a script block's inside it), so the answer is an allowlist: every OTHER program
# in the command only shows or reads text, the parser read all of it, no shell was handed a command string, nothing goes
# through xargs, and the inspection's stdout is not redirected to a script file. Anything else: not inert.
_INERT_PROGRAMS = frozenset((
    "cd", "pushd", "popd", "set-location", "push-location", "pop-location", "true",
    # fix-limits 12: loop structure that prints nothing and runs nothing (`while false; do P; done` was refused).
    "false", "break", "continue",
    "head", "tail", "grep", "egrep", "fgrep", "rg", "findstr", "select-string", "sort", "uniq", "wc", "cut", "nl",
    "less", "more", "cat", "get-content", "echo", "printf", "write-output", "write-host",
    "select-object", "measure-object", "out-host", "out-null", "out-string", "format-table", "format-list",
))
_STDOUT_REDIRECT = re.compile(r"^(?:1|&)?>>?\|?$")
_SCRIPT_FILE = re.compile(r"\.(?:ps1|psm1|sh|bash|zsh|bat|cmd|py|pyw|js|mjs|cjs|pl|rb|php|vbs|wsf)$", re.IGNORECASE)


def _is_inspection(x) -> bool:
    from . import deletes
    code = deletes.interpreter_code(x)
    return bool(code) and _python_inspection_text(x.program, code) != code


# Re-review F3: the allowlist can only vouch for steps the parser SAW. Every exec's segment is an exact substring of the
# command; whatever is left once they are all removed must be separators only. `pwsh -File <(python ...)` read as
# PowerShell yields the python exec alone and leaves `pwsh -File <( )` unattributed: not inert.
_SEPARATORS_ONLY = re.compile(r"[\s|;&()]*")
# Final review: shell STRUCTURE is glue too, but only where it is structure (`X=1; P`, `if true; then P; fi`,
# `{ P; } | head`, `for i in 1; do P; done`, PowerShell `$o = P; $o` were refused by the separators-only test). Each
# leftover token must be a separator, a brace, a keyword of the dialect, a bash assignment of a plain value, a
# PowerShell variable shown, a for-loop's plain list word, or a case subject/pattern in its position. Any other word
# (`pwsh`, `-File`, `<`) is a step the parser did not see. cmd (and an unknown dialect) keeps separators only.
_GLUE_TOKEN = re.compile(r";;|&&|\|\||[|;&(){}\n]|[^\s|;&(){}]+")
_GLUE_PUNCT = frozenset((";;", "&&", "||", "|", ";", "&", "(", ")", "{", "}", "\n"))
_KEYWORDS = {
    "bash": frozenset("if then else elif fi for while until do done case esac in function select time !".split()),
    "powershell": frozenset("if elseif else foreach for while do until switch in function".split()),
}
_GLUE_NAME = re.compile(r"[A-Za-z_]\w*")
_GLUE_PLAIN = re.compile(r"[\w.\-/:,+@%]+")
_GLUE_PATTERN = re.compile(r"[\w.\-/:,+@%*?\[\]]+")
_BASH_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=[\w.\-/:,+@%]*")
_PS_VARIABLE = re.compile(r"\$[A-Za-z_]\w*")


def _structure_only(rest: str, dialect: str) -> bool:
    """True when `rest` (a command with every parsed step removed) is shell structure only, read as `dialect`."""
    keywords = _KEYWORDS.get(dialect)
    if keywords is None:
        return _SEPARATORS_ONLY.fullmatch(rest) is not None
    toks = _GLUE_TOKEN.findall(rest)
    want_name = False       # after for / select / function: the loop variable or function name
    loop_list = False       # after `for NAME in`: plain words until a separator
    case = 0                # 1: subject next, 2: `in` next, 3: in the body
    pattern = False         # at a case pattern position (after `in`, `;;`, or a pattern's `|` / leading `(`)
    for i, tok in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if want_name:
            if not _GLUE_NAME.fullmatch(tok):
                return False
            want_name = False
        elif case == 1:
            if not _GLUE_PLAIN.fullmatch(tok):
                return False
            case = 2
        elif case == 2:
            if tok != "in":
                return False
            case, pattern = 3, True
        elif case == 3 and pattern and tok in ("(", "|"):
            pass
        elif case == 3 and pattern and tok == ")":
            pattern = False
        elif case == 3 and pattern and nxt in (")", "|"):
            if not _GLUE_PATTERN.fullmatch(tok):
                return False
        elif tok in _GLUE_PUNCT:
            loop_list = loop_list and tok not in (";", "\n")
            pattern = case == 3 and tok == ";;"
        elif loop_list:
            if not _GLUE_PLAIN.fullmatch(tok):
                return False
        elif tok in keywords:
            if dialect == "bash":
                want_name = tok in ("for", "select", "function")
                loop_list = tok == "in" and i >= 2 and toks[i - 2] in ("for", "select")
                if tok == "case":
                    case = 1
                if tok == "esac":
                    case, pattern = 0, False
        elif dialect == "bash" and _BASH_ASSIGNMENT.fullmatch(tok):
            pass
        elif dialect == "powershell" and _PS_VARIABLE.fullmatch(tok):
            pass
        else:
            return False
    return True


def _unattributed(command: str, parsed) -> bool:
    rest = command
    for seg in sorted({x.segment.strip() for x in parsed.execs if x.segment.strip()}, key=len, reverse=True):
        rest = rest.replace(seg, " ")
    # Read as every dialect the parse used (shell="" unions bash and PowerShell): glue must be glue in each.
    return not all(_structure_only(rest, d) for d in {x.dialect for x in parsed.execs} or {""})


def _output_inert(parsed, e, command=None) -> bool:
    """True only when nothing in this command can run what exec `e` prints (the allowlist above)."""
    if parsed is None or not parsed.ok:
        return False
    if command is None or _unattributed(command, parsed):
        return False
    if parsed.codes or parsed.xargs or parsed.unread:
        return False
    if any(_STDOUT_REDIRECT.match(op) and _SCRIPT_FILE.search(target.strip("'\"")) for op, target in e.redirects):
        return False
    for x in parsed.execs:
        if (x.program, x.segment) == (e.program, e.segment) or x.program in _INERT_PROGRAMS or _is_inspection(x):
            continue
        return False
    return True


# An interpreter whose output may run is read like the code it prints: a `.skyguard` path anywhere in its text, beside
# a delete construct, is a candidate operand -- nested quotes (`print('[IO.File]::Delete(".skyguard/x")')`) and Node's
# `console.log` never reached the quoted-string scan, before Plan 8 either. Each candidate is RESOLVED, so a scratch
# copy's `.skyguard` is not the live state; only a word the resolver cannot place falls back to its own components.
_STATE_WORD = re.compile(r"\.skyguard(?:[/\\]+[\w.\-]{1,120})?(?![\w.\-])", re.IGNORECASE)
_WORD_START = set(" \t\n'\"`()[]{}=,;|&<>")


def _printed_state_delete(e, parsed, cwd, env, roots, command=None) -> str:
    """The state a delete PRINTED by this interpreter would remove when its output runs ("" for none)."""
    from . import deletes
    from .grep import grep
    code = deletes.interpreter_code(e)
    if not code or ".skyguard" not in code.lower():
        return ""                                         # cost only: no code, or no state word for the loop below
    if not (grep(deletes.INTERP_DELETE, code) or grep(deletes.DESTROYER_LOOSE, code) or parsed.xargs):
        return ""
    if _output_inert(parsed, e, command):
        return ""
    for m in _STATE_WORD.finditer(code):
        # The word may start after any delimiter on its line (a quoted path holds spaces): try each, nearest first.
        # Never after a `/` or `\`: `C:/Temp/copy/.skyguard` is not a relative `.skyguard` in the cwd.
        # Only when NO start resolves is the word judged by its own components (`$R/.skyguard/x`); a wider start the
        # resolver cannot place (`\'C:/Temp/copy/.skyguard`) must not outvote the exact path beside it.
        line_start = code.rfind("\n", 0, m.start()) + 1
        placed = False
        for i in range(m.start(), max(line_start, m.start() - 400) - 1, -1):
            if i != line_start and code[i - 1] not in _WORD_START:
                continue
            w = code[i:m.end()]
            a = targets.resolve_operand(e, w, cwd, env)
            if a is not None and _reaches_state(a, roots):
                return _hit_name(a, w)
            placed = placed or a is not None
        if not placed:
            return _hit_name(None, m.group(0))
    return ""


def _state_delete(e, cwd, env, roots, parsed=None, command=None) -> str:
    """The name of what a delete by this exec removes from the state ("" for none): rm / Remove-Item / del / rd /
    [IO.File]::Delete (deletes.operands), and interpreter code with a delete API and a quoted path into the state.
    Parser inspection's literals are masked only when its output provably runs nowhere (_output_inert)."""
    from . import deletes
    from .grep import grep
    words = [w for w in (deletes.operands(e) or []) if isinstance(w, str)]
    code = deletes.interpreter_code(e)
    delete_api = bool(code and grep(deletes.INTERP_DELETE, code))
    if delete_api and _output_inert(parsed, e, command):
        delete_api = grep(deletes.INTERP_DELETE, _python_inspection_text(e.program, code))
    if code and (grep(deletes.INTERP_DESTROYER, code) or delete_api):
        words += [m.group(1) or m.group(2) for m in _QUOTED.finditer(code)]
    for w in words:
        a = targets.resolve_operand(e, w, cwd, env)
        if (a is not None and _reaches_state(a, roots)) or (a is None and text_kind(w) == STATE):
            return _hit_name(a, w)
    return ""


def _recycle_delete(command: str, parsed, cwd, env, roots) -> str:
    """The name of what a Recycle-Bin delete removes from the state ("" for none); each argument is resolved as
    deletes.scan resolves it (single-quoted: literal; double-quoted: expanded; bare: never resolved, matched raw)."""
    from . import deletes
    base = targets.normalise_cwd(cwd)
    placed = True
    for w, how in deletes.recycle_targets(deletes.recycle_text(command, parsed)):
        a = _recycle_abs(w, how, base, env)
        if (a is not None and _reaches_state(a, roots)) or (a is None and text_kind(w) == STATE):
            return _hit_name(a, w)
        # Re-review F1: a literal that is a format hole (`"{p}"`, `"{}"`, `"%s"`, `"%(p)s"`, `${p}`) or empty (`'' + p
        # + ''`) only LOOKS placed -- the path is filled in elsewhere -- so it counts as unplaced.
        placed = placed and a is not None and bool(w.strip()) and not _FORMAT_HOLE.search(w)
    if placed:
        return ""
    # Release review T13 I2: an argument the guard cannot place (`$env:P`, `$p`, `"'+p+'"`) is the path the command
    # builds elsewhere, so every quoted word of the command -- and the quoted words inside those, the code a launcher
    # hands on -- is a candidate target, as this rule read interpreter code before 7570b28. A placed literal outside
    # the install (`DeleteFile('C:/outside/...')`, the outside examples 7570b28 fixed) never reaches this fallback.
    for w, how in _quoted_words(deletes.recycle_text(command, parsed)):
        a = _recycle_abs(w, how, base, env)
        if a is not None and _reaches_state(a, roots):
            return _hit_name(a, w)
        if a is None and text_kind(w) == STATE:
            return _hit_name(None, w)
    return ""


# printf: `%` [`(name)`] [flags] [width: digits or `*`] [`.` precision: digits or `*`] conversion s / r / a
# (final review: `%.80s`, `%-10.5s`, `%*s`, `%.*s`, `%(p).10s`, `%.80a`). A brace hole is any `{...}`, so `{0:>20}`
# and `{p!r:10}` count.
_FORMAT_HOLE = re.compile(r"\{[^{}\n]{0,80}\}|%(?:\([^)\n]{0,80}\))?[-#0 +]*(?:\*|\d+)?(?:\.(?:\*|\d*))?[sra]")


def _recycle_abs(w: str, how: str, base, env) -> Optional[str]:
    from . import deletes
    if how == "single":
        return deletes.literal_abs(w, base, "powershell")
    if how == "double":
        return targets.resolve(w, base or "", "powershell", env)
    return None


def _quoted_words(text: str) -> List[Tuple[str, str]]:
    """(body, "single" | "double") for every quoted word in the text and every quoted word one level inside one."""
    out = []
    for m in _QUOTED.finditer(text):
        body = m.group(1) if m.group(1) is not None else m.group(2)
        out.append((body, "single" if m.group(1) is not None else "double"))
        out += [(n.group(1) if n.group(1) is not None else n.group(2), "single" if n.group(1) is not None else "double")
                for n in _QUOTED.finditer(body)]
    return out


# ---- the text fallback for an unreadable command (Plan 6 final fix, lane A I1) --------------------------------------
# bash runs the lines before a syntax error, so a command the parser cannot read (ok=False: an unclosed quote or heredoc)
# or a program it cannot name ("?") is read as TEXT, as the plugin-write and lift rules already are: one statement (a
# line or a `;` segment) naming a `.skyguard` path component AND holding a write or delete construct is S-SELF-STATE.
# Not resolved against the roots (an unreadable command has no reliable operands): a scratch `.skyguard` counts too --
# the fallback's priced cost, a refusal only on an unreadable command that mentions `.skyguard` beside a write.
_STATE_TEXT = re.compile(r"""(?:^|[\s"'=/\\:(])\.skyguard(?:[/\\]+([\w.\-]{1,120}))?(?![\w.\-])""", re.IGNORECASE)
_STATE_TEXT_WRITE = re.compile(
    r">|\b(?:tee|cp|mv|ln|install|rsync|dd|truncate|touch|rm|rmdir|unlink|shred|del|erase|rd|copy|move|ren|rename"
    r"|xcopy|robocopy|mklink|curl|wget|tar|unzip|7z|sed|chmod|icacls|git)\b"
    r"|Set-Content|Add-Content|Out-File|Clear-Content|Tee-Object|Copy-Item|Move-Item|Rename-Item|Remove-Item"
    r"|New-Item|Invoke-WebRequest|Expand-Archive|::\s*(?:Write|Append|Copy|Move|Delete|Replace|Create)"
    r"|\bof=|\bopen\s*\(|write_text|write_bytes|writeFile",
    re.IGNORECASE)
_STATEMENT = re.compile(r"[\n;]")


def _state_text(command: str, parsed) -> str:
    """The name a write into the state has in an unreadable command's text ("" for none)."""
    if ".skyguard" not in command.lower():
        return ""
    texts = [command] if not parsed.ok else [e.segment for e in parsed.execs if e.program == "?"]
    for text in texts:
        for stmt in _STATEMENT.split(text):
            m = _STATE_TEXT.search(stmt)
            if m and _STATE_TEXT_WRITE.search(stmt):
                return m.group(1) or ".skyguard"
    return ""


def shell_scan(command: str, parsed, roots, cwd: str = "", env: Optional[Mapping[str, str]] = None):
    """(class, basename, op) of the first protected file a command writes, STATE before GUARD; None when it writes none.
    op: EDITS / CREATES / LINKS, the verb of the S-SELF-GUARD note (T8 fix round 2, review N4)."""
    from . import deletes
    env = os.environ if env is None else env
    found = {}
    for e in parsed.execs:
        e = deletes.substituted(e)
        for w, holder, op in _routes(e):
            a = resolve_own(lambda x: targets.resolve_operand(e, x, cwd, env), w)
            # Unresolved is never safe (targets.py) -- for the STATE class only, whose deny has no priced cost: a word
            # the resolver cannot place (`"$R/.skyguard/state.json"`) counts when its own words name `.skyguard`.
            k = kind_of(a, roots, env, holder) if a is not None else (STATE if text_kind(w) == STATE else "")
            if k and k not in found:
                found[k] = (re.split(r"[/\\]+", (a or w).rstrip("/\\"))[-1] or w, op)
        if STATE not in found:
            hit = (_state_delete(e, cwd, env, roots, parsed, command)
                   or _printed_state_delete(e, parsed, cwd, env, roots, command))
            if hit:
                found[STATE] = (hit, EDITS)
    if STATE not in found:
        hit = _recycle_delete(command, parsed, cwd, env, roots)
        if hit:
            found[STATE] = (hit, EDITS)
    if STATE not in found:
        hit = _state_text(command, parsed)
        if hit:
            found[STATE] = (hit, EDITS)
    for k in (STATE, GUARD):
        if k in found:
            return (k,) + found[k]
    return None
