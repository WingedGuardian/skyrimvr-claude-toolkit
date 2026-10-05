r"""Agent self-protection (Plan 6 Task 12, R12): the guards' own state, code and config.

    kind_of(abs_path, roots, env)                     -> ("state" | "guard" | "", basename)
    shell_scan(command, parsed, roots, cwd, env)      -> ("state" | "guard", basename) | None
    text_kind(path)                                   -> the same answer from the path's words alone (no roots)

Two protected classes, both decided on where a path RESOLVES (targets), never on words in the command:

  STATE  `.skyguard/**` in the install: the on/off state (state.json) and the lock manifest. Only tools/skyguard writes
         it, so any other write is refused (S-SELF-STATE / W-SELF-STATE). The CLI's own run is never a write primitive
         here, so `tools/skyguard guards off` is S-LIFT-NOTE, not this.
  GUARD  the guard code and config: `.claude/hooks/**`, `.claude/settings*.json`, `.claude/skyrim-paths.env` (red team
         F5: the declared roots), `.codex/**`, `tools/skyguard/**`, `agent/guards/**`, the telemetry the canary and
         shadow mode read (`.claude/backups/.hook-heartbeat/**`, `.claude/backups/AUDIT_LOG.txt`; Task 9 review (e)),
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
  - `git checkout`/`git restore` with a pathspec that is, or holds, a guard file (red team F3).
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


def _routes(e) -> List[Tuple[str, bool]]:
    """(word, holder) for every operand this exec writes, replaces, moves away or re-permissions."""
    from . import writes
    from .shellparse import ps_param
    out = [(w, False) for w in writes.write_targets(e)]
    for dst, srcs in writes.copy_targets(e):
        if e.program in MOVE_PROGRAMS or (e.program in _CMD_MOVE and e.dialect == "cmd"):
            out += [(s, True) for s in srcs]             # the source first: the message names what was moved away
        out.append((dst, False))
    p, argv = e.program, e.argv
    if p == "rename-item":
        # The new name lands beside the source, in the same folder, so the source decides the class.
        out += [(s, True) for s in list(ps_param(e, "path", 0)) + list(ps_param(e, "literalpath"))]
    elif p in ("ren", "rename") and e.dialect == "cmd":
        words = [w for w in argv if not w.startswith("/")]
        out += [(w, True) for w in words[:1]]
    elif p == "ln":
        words = _plain(argv)
        if len(words) >= 2:
            out.append((words[-1], False))
    elif p == "truncate":
        words, k = [], 0
        while k < len(argv):
            if argv[k] in ("-s", "--size", "-r", "--reference"):
                k += 2
                continue
            if not argv[k].startswith("-"):
                words.append(argv[k])
            k += 1
        out += [(w, False) for w in words]
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
    return out


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
# The Recycle-Bin delete (Microsoft.VisualBasic FileSystem.DeleteFile/DeleteDirectory): no Exec carries it, so it is a
# text construct whose FIRST argument is the path.
_RECYCLE = re.compile(r"FileSystem\]\s*::\s*Delete(?:File|Directory)\s*\(\s*(?:'([^'\n]{1,400})'|\"([^\"\n]{1,400})\")",
                      re.IGNORECASE)


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
    if code and (grep(deletes.DESTROYER, code) or grep(deletes.INTERP_DELETE, code)):
        words += [m.group(1) or m.group(2) for m in _QUOTED.finditer(code)]
    for w in words:
        a = targets.resolve_operand(e, w, cwd, env)
        if (a is not None and _reaches_state(a, roots)) or (a is None and text_kind(w) == STATE):
            return _hit_name(a, w)
    return ""


def _recycle_delete(command: str, cwd, env, roots) -> str:
    for m in _RECYCLE.finditer(command):
        w = m.group(1) or m.group(2)
        a = targets.resolve(w, cwd, "powershell", env)
        if (a is not None and _reaches_state(a, roots)) or (a is None and text_kind(w) == STATE):
            return _hit_name(a, w)
    return ""


def shell_scan(command: str, parsed, roots, cwd: str = "", env: Optional[Mapping[str, str]] = None):
    """(class, basename) of the first protected file a command writes, STATE before GUARD; None when it writes none."""
    from . import deletes
    env = os.environ if env is None else env
    found = {}
    for e in parsed.execs:
        e = deletes.substituted(e)
        for w, holder in _routes(e):
            a = targets.resolve_operand(e, w, cwd, env)
            # Unresolved is never safe (targets.py) -- for the STATE class only, whose deny has no priced cost: a word
            # the resolver cannot place (`"$R/.skyguard/state.json"`) counts when its own words name `.skyguard`.
            k = kind_of(a, roots, env, holder) if a is not None else (STATE if text_kind(w) == STATE else "")
            if k and k not in found:
                found[k] = re.split(r"[/\\]+", (a or w).rstrip("/\\"))[-1] or w
        if STATE not in found:
            hit = _state_delete(e, cwd, env, roots)
            if hit:
                found[STATE] = hit
    if STATE not in found:
        hit = _recycle_delete(command, cwd, env, roots)
        if hit:
            found[STATE] = hit
    for k in (STATE, GUARD):
        if k in found:
            return k, found[k]
    return None
