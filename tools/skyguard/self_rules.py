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

import fnmatch
import os
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


def _state_delete(e, cwd, env, roots) -> str:
    """The name of what a delete by this exec removes from the state ("" for none): rm / Remove-Item / del / rd /
    [IO.File]::Delete (deletes.operands), and interpreter code with a delete API and a quoted path into the state."""
    from . import deletes
    from .grep import grep
    words = [w for w in (deletes.operands(e) or []) if isinstance(w, str)]
    code = deletes.interpreter_code(e)
    if code and (grep(deletes.INTERP_DESTROYER, code) or grep(deletes.INTERP_DELETE, code)):
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
    for w, how in deletes.recycle_targets(deletes.recycle_text(command, parsed)):
        a = None
        if how == "single":
            a = deletes.literal_abs(w, base, "powershell")
        elif how == "double":
            a = targets.resolve(w, base or "", "powershell", env)
        if (a is not None and _reaches_state(a, roots)) or (a is None and text_kind(w) == STATE):
            return _hit_name(a, w)
    return ""


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
            hit = _state_delete(e, cwd, env, roots)
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
