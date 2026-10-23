"""The save policy (user ruling 2026-10-13): every save a call CHANGES is backed up first, and a bulk change needs the
user's typed approval.

A save is a `.ess` with its `.skse` co-save (same folder, same stem): ONE save, whichever file a command names. The
saves this judges are the ones the rules already resolve as saves (writes.is_save: a `.ess` / `.skse` in the settings
folder), plus the save ReSaver `--apply` writes and the save a DevBench call overwrites. A call CHANGES a save when it
writes or edits it, copies onto it, moves or renames it, deletes it, saves over it in DevBench, or rewrites it with
ReSaver --apply.

    judge(change, rest, audit, hook) -> Verdict      (pure but for listing folders: the rule modules call it)
    enforce(v, ...) -> Verdict                       (the hooks: the backup itself, and the user's approval)

DECISION (judge), counting the saves that EXIST now (creating a new save changes none):
  * 1 or 2 saves, every one named exactly: allow, with an S-SAVE-BACKUP note. The hook backs both files of each pair
    up and verifies them BEFORE the tool runs (enforce), and denies if it cannot (S-SAVE-BACKUP-FAILED).
  * more than 2, or any wildcard: S-SAVE-APPROVAL denies, naming the line the user types (approvals.py). With that
    approval in the transcript, enforce backs every one of them up and allows.
  * a save the guard cannot place: denied (S-SAVE-UNKNOWN; DevBench's S-DEVBENCH-SAVE and ReSaver's S-RESAVER-APPLY
    say the same for their calls) -- no backup is possible, so nothing is allowed.
Reading, hashing, listing and copying a save OUT of its folder change no save: free. Deleting or moving the Saves
FOLDER stays S-DEL-CONFIG / S-MOVE-ROOT (deletes.py, writes.py). Every save refusal here is NEVER_SHADOW (engine.py):
`guards off` is a lift the agent may take, and the user's typed approval cannot be.

The plan travels on the verdict (Verdict.saves = Plan(change, rest)), `rest` being what the other rules decided, so
enforce can still allow (with rest's own advice, ask or deny) once the backup holds. A call that the other rules deny
still gets its saves backed up: shadow mode (guards off) can let a denied call run.

KNOWN LIMITS: a save written by a script run from a file (`python fix.py`), or by code that builds its path at run
time, is not seen; a loop over saves (`for f in ...; do rm "$f"; done`) cannot be counted, so it is never backed up
and stays whatever the delete rules decide (S-DEL-UNRESOLVED when it names a protected folder).
"""
from __future__ import annotations

import fnmatch
import os
import re
from typing import Dict, List, NamedTuple, Optional, Tuple

from . import approvals, mcp_rules, paths, rules, targets
from .verdict import Verdict

OPS = approvals.OPS                       # write | move | delete
VERB = {"write": "changes", "move": "moves", "delete": "deletes"}
EXTS = (".ess", ".skse")
WILD = re.compile(r"[*?\[]")
LIMIT = 2                                 # saves one call may change without the user's approval
NOTE_BEFORE = "restore: tools/skyguard.sh snapshot list --kind save"
# The refusals a guards-off lift must not loosen (engine.NEVER_SHADOW).
NEVER_SHADOW = frozenset(("shell.S-SAVE-APPROVAL", "shell.S-SAVE-UNKNOWN", "shell.S-SAVE-BACKUP-FAILED",
                          "shell.S-DEVBENCH-SAVE", "shell.S-RESAVER-APPLY", "shell.S-SAVE-OPAQUE"))


class Target(NamedTuple):
    path: str                             # the resolved file (a wildcard stays in its file name)
    op: str                               # write | move | delete
    literal: bool = False                 # concrete file-tool / structured API identity, not shell text


class Change(NamedTuple):
    targets: Tuple[Target, ...] = ()
    unknown: Tuple[Tuple[str, Tuple[Tuple[str, str], ...]], ...] = ()   # (rule id, message fields): cannot back up

    def __bool__(self):
        return bool(self.targets or self.unknown)


class Plan(NamedTuple):
    change: Change
    rest: Verdict                         # the other rules' verdict for the same call


class Pair(NamedTuple):
    ess: str
    skse: str
    stem: str                             # as the files spell it
    ops: Tuple[str, ...]
    exists: bool


def merge(changes) -> Change:
    t, u = [], []
    for c in changes:
        t += [x for x in c.targets if x not in t]
        u += [x for x in c.unknown if x not in u]
    return Change(tuple(t), tuple(u))


# ------------------------------------------------------------------------------------------- what a call changes
def resaver_change(op, words, cwd, shell, roots, env) -> Change:
    """The saves ReSaver --apply WRITES (shell_rules.resaver_writes: the output .ess, never the input it only reads),
    as written: absolute; relative to the payload cwd, where ReSaver resolves it; else a bare name in the Saves folder.
    A target that exists nowhere is a NEW file: nothing to back up. One the guard cannot place is unknown."""
    if not words:
        return Change(unknown=(("S-RESAVER-APPLY", (("op", op), ("save", "a save it does not name"))),))
    out, unknown = [], []
    env = os.environ if env is None else env
    folder = mcp_rules.saves_folder(roots, env) if roots is not None else None
    for word in words:
        name = re.split(r"[/\\]", word)[-1] or word
        if WILD.search(os.path.splitdrive(word)[1]):
            unknown.append(("S-RESAVER-APPLY", (("op", op), ("save", name))))
            continue
        n = paths.physical_abs(word, msys=(shell != "powershell"))
        if n is None and paths.norm_abs(word, msys=(shell != "powershell")) is not None:
            unknown.append(("S-RESAVER-APPLY", (("op", op), ("save", name))))
            continue
        cands = [n] if n else []
        if not n:
            base = paths.physical_abs(cwd) if cwd else None
            if base is None:
                unknown.append(("S-RESAVER-APPLY", (("op", op), ("save", name))))   # relative, and no cwd to place it
                continue
            j = paths.physical_abs(base + "/" + word.replace("\\", "/"))
            if j:
                cands.append(j)
            if folder:
                cands.append(_join(folder, name))
        hit = next((c for c in cands if _listed(c)), None)
        if hit is None and roots is None and not n:
            unknown.append(("S-RESAVER-APPLY", (("op", op), ("save", name))))       # the Saves folder is unknown
            continue
        if hit is not None or cands:
            out.append(Target(hit or cands[0], "write"))
    return Change(tuple(out), tuple(unknown))


def devbench_change(found, roots, env) -> Change:
    """mcp_rules.judge's save hits: (hit, clean name). An existing save is a target; a hit it could not resolve (no
    plain name, no Saves folder) is unknown: S-DEVBENCH-SAVE, which now refuses."""
    out, unknown = [], []
    env = os.environ if env is None else env
    folder = mcp_rules.saves_folder(roots, env) if roots is not None else None
    for (rid, fields), clean in found:
        if clean is not None and folder is not None and fields.get("op", "").startswith("Overwrites save"):
            out.append(Target(_join(folder, clean + EXTS[0]), "write", True))
        else:
            unknown.append((rid, tuple(sorted(fields.items()))))
    return Change(tuple(dict.fromkeys(out)), tuple(dict.fromkeys(unknown)))


def shell_change(dels, wr, resaver, dev_found, cwd, shell, roots, env) -> Change:
    """Every save a shell command changes: deletes.scan's, writes.scan's, ReSaver's and DevBench's."""
    t = [Target(p, "delete") for p in dels.saves] + [Target(p, op) for p, op in wr.save_targets]
    c = Change(tuple(t))
    for op, words, execution in resaver or ():
        base = cwd if execution is None else (targets.exec_cwd(execution, cwd, env) or "")
        c = merge([c, resaver_change(op, words, base, shell, roots, env)])
    if dev_found:
        c = merge([c, devbench_change(dev_found, roots, env)])
    return c


# ------------------------------------------------------------------------------------------- counting
def _listed(path: str) -> Optional[str]:
    """`path` spelled as its folder lists it (case-insensitive, as Windows), or None when no such file exists."""
    folder, name = os.path.split(path)
    try:
        for n in os.listdir(folder or "."):
            if n.casefold() == name.casefold():
                p = _join(folder, n)
                return p if os.path.isfile(p) else None
    except OSError:
        pass
    return None


def _join(folder: str, name: str) -> str:
    """folder/name in the folder's own separator (a resolved path is spelled with `/`)."""
    return folder.rstrip("/") + "/" + name if "/" in folder and "\\" not in folder else os.path.join(folder, name)


def _split(path: str) -> Tuple[str, str]:
    folder, name = os.path.split(path)
    low = name.lower()
    for ext in EXTS:
        if low.endswith(ext):
            return folder, name[:-len(ext)]
    return folder, name


def _key(folder: str, stem: str) -> Tuple[str, str]:
    return os.path.normcase(os.path.normpath(folder or ".")), stem.casefold()


class Count(NamedTuple):
    pairs: Tuple[Pair, ...]                                  # every save the call touches, existing or not
    patterns: Dict[Tuple[str, str], Tuple[Tuple[str, str], ...]]   # (file-name pattern as written, op) -> pair keys
    unknown: Tuple[str, ...]                                 # wildcard words the guard could not list


def count(change: Change) -> Count:
    found: Dict[Tuple[str, str], dict] = {}
    patterns: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}
    unknown: List[str] = []

    def add(folder, stem, op):
        k = _key(folder, stem)
        d = found.setdefault(k, {"folder": folder, "stem": stem, "ops": []})
        if op not in d["ops"]:
            d["ops"].append(op)
        return k

    for t in change.targets:
        name = os.path.split(t.path)[1] if t.path else ""
        if not name or (not WILD.search(name) and not name.lower().endswith(EXTS)):
            unknown.append("unqualified save path")
            continue
        folder, name = os.path.split(t.path)
        if not t.literal and WILD.search(os.path.splitdrive(folder)[1]):
            unknown.append(name)
            continue
        if t.literal or not WILD.search(name):
            f, stem = _split(t.path)
            add(f, stem, t.op)
            continue
        try:
            names = os.listdir(folder)
        except OSError:
            unknown.append(name)
            continue
        keys = patterns.setdefault((name, t.op), [])
        for n in sorted(names):
            if n.lower().endswith(EXTS) and fnmatch.fnmatchcase(n.casefold(), name.casefold()):
                k = add(folder, _split(n)[1], t.op)
                if k not in keys:
                    keys.append(k)
    pairs = []
    for d in found.values():
        ess = _join(d["folder"], d["stem"] + EXTS[0])
        skse = _join(d["folder"], d["stem"] + EXTS[1])
        le, ls = _listed(ess), _listed(skse)
        stem = _split(le or ls)[1] if (le or ls) else d["stem"]
        pairs.append(Pair(le or ess, ls or skse, stem, tuple(d["ops"]), bool(le or ls)))
    return Count(tuple(pairs), {k: tuple(v) for k, v in patterns.items()}, tuple(unknown))


def _existing(c: Count) -> List[Pair]:
    return [p for p in c.pairs if p.exists]


def needs_approval(c: Count) -> bool:
    return bool(c.patterns) or len(_existing(c)) > LIMIT


# ------------------------------------------------------------------------------------------- the decision
def _phrase(stems: List[str]) -> str:
    uniq = list(dict.fromkeys(stems))
    return ", ".join(uniq[:3]) + (" and %d more" % (len(uniq) - 3) if len(uniq) > 3 else "")


def _ops_verb(items) -> str:
    """The verb for what a call does to these saves (Pairs carry `ops`, Targets one `op`)."""
    ops = {o for x in items for o in (x.ops if isinstance(x, Pair) else (x.op,))}
    return VERB[ops.pop()] if len(ops) == 1 else "changes"


def _render(rid: str, fields) -> str:
    return rules.message(rid, **dict(fields))


def judge(change: Change, rest: Verdict, audit=None, hook: str = "protect-bash") -> Verdict:
    """The call's verdict once its saves are counted (see the module docstring). `rest`: the other rules' verdict."""
    if not change:
        return rest
    plan = Plan(change, rest._replace(saves=None))

    def refuse(rid, fields):
        reason = _render(rid, fields)
        if audit is not None:
            audit.rule(hook, rid, "deny", reason)
        from . import codes                     # codes reads the rule modules, which import this one
        return Verdict("deny", reason, "", rest.status, code=codes.rule_code(rid), never=rest.never, saves=plan)

    if change.unknown:
        return refuse(*change.unknown[0])
    c = count(change)
    if c.unknown:
        return refuse("S-SAVE-UNKNOWN", (("op", _ops_verb(change.targets)), ("file", c.unknown[0])))
    ex = _existing(c)
    if needs_approval(c):
        names = [p.stem for p in ex] or [pat for pat, _op in c.patterns]
        return refuse("S-SAVE-APPROVAL", (("op", _ops_verb(c.pairs)), ("n", str(len(ex))),
                                          ("file", _phrase(names))))
    if not ex or rest.decision in ("deny", "ask"):
        return rest._replace(saves=plan)
    note = _render("S-SAVE-BACKUP", (("file", _phrase([p.stem for p in ex])), ("op", NOTE_BEFORE)))
    if audit is not None:
        audit.rule(hook, "S-SAVE-BACKUP", "advise", note)
    return _with_note(rest, note)._replace(saves=plan)


def _with_note(rest: Verdict, note: str) -> Verdict:
    if rest.decision in ("deny", "ask"):
        return rest
    ctx = " | ".join(x for x in (rest.context, note) if x)
    return Verdict("allow", "", ctx, rest.status, "", rest.never)


def combine(vs, fold):
    """engine.combine for verdicts that carry save plans: the other rules' verdicts are folded by `fold`, and the
    saves of ALL of them are judged together (two one-save edits in one patch are two saves). None when no verdict
    carries a plan."""
    plans = [v.saves for v in vs if v.saves is not None]
    if not plans:
        return None
    rest = fold([v.saves.rest if v.saves is not None else v for v in vs])
    return judge(merge([p.change for p in plans]), rest)


# ------------------------------------------------------------------------------------------- the hooks
def enforce(v: Verdict, project: str, audit, hook: str, command: str = "", transcript: str = "", agent: str = "",
            session_id: str = "", deadline: Optional[float] = None) -> Verdict:
    """Run in the deciding PreToolUse hook, BEFORE the tool can run. Backs up every save the call changes, verified,
    and only then lets judge's allow (or the user-approved bulk change) stand. Any failure DENIES (fail closed).
    A verdict with no save plan is returned unchanged."""
    from . import codes, save_store
    from modding_harness import approval as scoped_approval, profiles
    plan = v.saves
    if plan is None:
        return v
    change, rest = plan
    if change.unknown:
        return v
    c = count(change)
    if c.unknown:
        return v
    ex = _existing(c)
    approved = False
    if needs_approval(c):
        grants, why = scoped_approval.read(transcript, agent, session_id)
        wanted = {op: [p for pair in ex if op in pair.ops for p in profiles.skyrim().members(pair.ess)]
                  for op in {op for pair in ex for op in pair.ops}}
        if not wanted or not all(any(grant.matches(session_id, op, members, command, bool(c.patterns),
                                                  len([p for p in ex if op in p.ops])) for grant in grants)
                                 for op, members in wanted.items()):
            examples = [scoped_approval.example(session_id, op, members, command, bool(c.patterns),
                                               len([p for p in ex if op in p.ops]))
                        for op, members in sorted(wanted.items())]
            audit.log(hook, "SAVE-APPROVAL-MISSING", why or "scope changed")
            reason = v.reason + "\nIf approved, the user types:\n" + "\n".join(examples)
            if agent not in ("claude", "codex") or not session_id:
                reason += "\nThis host/session has no qualified approval reader; bulk changes remain refused."
            return v._replace(reason=reason)
        approved = True
    if not ex:
        return codes.classify(rest) if approved else v
    try:
        ids = save_store.backup_pairs(project, audit, [(p.ess, p.skse) for p in ex], command or hook,
                                    agent=agent, session_id=session_id, deadline=deadline)
    except (OSError, ValueError) as e:
        reason = _render("S-SAVE-BACKUP-FAILED", (("file", _phrase([p.stem for p in ex])), ("op", str(e))))
        audit.rule(hook, "S-SAVE-BACKUP-FAILED", "deny", reason)
        return Verdict("deny", reason, "", rest.status, code=codes.rule_code("S-SAVE-BACKUP-FAILED"))
    # Recount after preservation so aliases, wildcard membership and absent
    # counterparts appearing during a multi-save transaction cannot use old scope.
    if count(change) != c:
        reason = "save membership changed during preservation; run the guard again"
        return Verdict("deny", reason, "", rest.status, code=codes.rule_code("S-SAVE-BACKUP-FAILED"))
    note = _render("S-SAVE-BACKUP", (("file", _phrase([p.stem for p in ex])), ("op", NOTE_BEFORE)))
    if approved:
        audit.log(hook, "SAVE-APPROVED", "{} save(s) the user approved, backed up first: {}".format(
            len(ex), _phrase([p.stem for p in ex])))
        audit.rule(hook, "S-SAVE-BACKUP", "advise", note)
    audit.log(hook, "SAVE-BACKUP", "{} backed up and verified ({}) before the call".format(
        _phrase([p.stem for p in ex]), ", ".join(ids)))
    return codes.classify(_with_note(rest, note))


def _covered(c: Count, g) -> bool:
    """Every existing save the call changes is approved for its op: by file, or by a pattern grant of the same
    file-name pattern and op whose count is exactly what the pattern matches now."""
    if g.why:
        return False
    by_pattern = {}
    for (pat, op), keys in c.patterns.items():
        live = [k for k in keys if k in {_key(os.path.dirname(p.ess), p.stem) for p in _existing(c)}]
        if (pat.casefold(), op, len(live)) not in g.patterns:
            return False                       # a wildcard is approved only by its own pattern grant, count and all
        for k in live:
            by_pattern.setdefault(k, set()).add(op)
    for p in _existing(c):
        k = _key(os.path.dirname(p.ess), p.stem)
        for op in p.ops:
            if op not in by_pattern.get(k, set()) and (p.stem.casefold(), op) not in g.files:
                return False
    return True
