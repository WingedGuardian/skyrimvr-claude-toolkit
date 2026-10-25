"""The shell guard: protect-bash.sh's rules, now decided on what a command EXECUTES (Plan 5).

Rule order is the bash file's order and is load-bearing: the first deny wins, advice accumulates, and an
ask drops whatever advice came before it. Deletes (deletes.py), writes and copies (writes.py) and the registry,
.psc-output, Champollion, AutoMod and external-tool rules (anchored.py) ask about the program that runs and where
its operands resolve; none of them matches the command's text except as the parse-failure fallback (R4).
"""
from __future__ import annotations

import os
import re

from . import anchored
from . import audit as _audit
from . import deletes
from . import lift_rules
from . import mcp_rules
from . import paths
from . import rules
from . import saves
from . import self_rules
from . import shellparse
from . import targets
from . import writes
from .verdict import Advice, Verdict

HOOK = "protect-bash"

NO_COMMAND_LOG = "no command in payload -- hook could not evaluate its rules"
NO_COMMAND_DENY = rules.RULES["S-INERT-NOCMD"]["template"]
NO_ROOT_LOG = "could not derive the install root -- the delete guard evaluated NOTHING"
NO_ROOT_DENY = rules.RULES["S-INERT-NOROOT"]["template"]

DENY_REGISTRY = rules.RULES["S-REGISTRY"]["template"]
DENY_PSC = rules.RULES["S-PSC-OUT"]["template"]
DENY_CHAMPOLLION = rules.RULES["S-CHAMPOLLION"]["template"]
DENY_WRITE_PLUGIN = rules.RULES["S-WRITE-PLUGIN"]["template"]     # Task 8 (E21); rendered with {file}

ADV_SED = rules.RULES["S-SED-WINPATH"]["template"]
ADV_AUTOMOD_ESP = rules.RULES["S-AUTOMOD-ESP"]["template"]
ADV_AUTOMOD_NIF = rules.RULES["S-AUTOMOD-NIF"]["template"]
ADV_AUTOMOD_ARCHIVE = rules.RULES["S-AUTOMOD-ARCHIVE"]["template"]
ADV_EXTERNAL = rules.RULES["S-EXTERNAL-OUT"]["template"]

DESTROYER = deletes.DESTROYER   # legacy delete-word regex; now used only by S-DEL-FALLBACK and on interpreter code


# --- S-RESAVER-APPLY: ReSaver is the PROGRAM RUN, with --apply and a save-writing operation among ITS OWN arguments
# (port of install 4a429bd/1540b99; Task 4 widened it with regexes, Task 6 re-based it on shellparse). Text that
# merely contains the words (a heredoc to python, grep, echo, a commit message) is not a save write. Every
# operation below writes a save when given --apply (tools/resaver-cli.sh, ResaverCLI.java).
#
# The parser supplies what the Task 4 regexes approximated: wrappers (sudo, env, timeout, xargs, find -exec ...),
# `bash -c` / `eval` / `pwsh -Command` / `cmd /c` strings, shell-fed heredocs (R7), `$( )`, backticks,
# line continuations, quoted and quote-split tokens, and the PowerShell and cmd dialects.
#
# NOT SAFE BY DEFAULT. Where the parser cannot be sure what runs -- the text could not be parsed (ok=False, R4) or an
# exec's program is opaque ("?": `$R reset-havok ...`, `printf ... | sh`) -- the statement's TEXT is checked
# instead: `resaver`, `--apply` and an operation word all present is a save write (Task 5 carry: "?" is unresolved,
# never safe), judged by the save policy (saves.py): backed up first, or refused when the save cannot be placed.
RESAVER_OPS = ("set-global", "set-var", "clean", "reset-havok", "cleanse-formlists", "remove-created")
RESAVER_PROGRAMS = ("resaver-cli", "resavercli")
RESAVER_APPLY = "--apply"
# Text fallback: whole-word op (`reset-havok.ess` is a file name, not the operation). Linear: no nested repetition.
RESAVER_TEXT_OP = re.compile(r"(?<![^\s\"'])(" + "|".join(RESAVER_OPS) + r")(?![^\s\"'])", re.ASCII | re.IGNORECASE)
RESAVER_TEXT_SAVE = re.compile(r"[^\s\"'/\\]{1,300}\.ess(?![^\s\"'])", re.ASCII | re.IGNORECASE)


def _is_resaver(e) -> bool:
    if e.program in RESAVER_PROGRAMS:
        return True
    return e.program == "java" and any("resaver" in a.lower() for a in e.argv)


def _resaver_op(e) -> str:
    words = [a.lower() for a in e.argv]
    if RESAVER_APPLY not in words:
        return ""
    return next((a for a in words if a in RESAVER_OPS), "")


def _save_word(words) -> str:
    """The first .ess argument AS WRITTEN (a path or a bare name), "" when there is none."""
    for a in words:
        if a.lower().endswith(".ess"):
            return a
    return ""


def _save_of(words) -> str:
    return re.split(r"[/\\]", _save_word(words))[-1]


def _text_fallback(command: str):
    """(op, save) when the raw text names ReSaver, --apply and an operation; None otherwise."""
    low = command.lower()
    if "resaver" not in low or RESAVER_APPLY not in low:
        return None
    m = RESAVER_TEXT_OP.search(command)
    if not m:
        return None
    s = RESAVER_TEXT_SAVE.search(command)
    return m.group(1).lower(), (s.group(0) if s else "")


def resaver_target(command: str, shell: str = "", parsed=None):
    """(operation, the save as written -- a path or a bare name -- or "") when ReSaver is executed with --apply and a
    write operation. resaver_writes reads the saves it writes from the same decision (the save policy, saves.py)."""
    if "resaver" not in command.lower():        # every form names resaver; skip the parse entirely
        return None
    parsed = shellparse.parse(command, shell) if parsed is None else parsed
    opaque = False
    for e in parsed.execs:
        if e.program == "?":
            opaque = True
            continue
        if _is_resaver(e):
            e = deletes.substituted(e)             # RT-A: `OP=reset-havok; ... $OP x.ess --apply`
            if any("$" in a or "`" in a for a in e.argv):
                opaque = True                       # RT-A: an argument not proven literal (`$(echo --apply)`): the text
            op = _resaver_op(e)
            if op:
                # No .ess among its own arguments (`find -name "*.ess" -exec ... {}`): name the one its segment selects.
                s = _save_word(e.argv) or (lambda m: m.group(0) if m else "")(RESAVER_TEXT_SAVE.search(e.segment))
                return op, s
    if not parsed.ok or opaque:
        return _text_fallback(command)
    return None


def resaver_writes(command: str, shell: str = "", parsed=None):
    """(first operation, [ALL saves WRITTEN, as written]) for compatibility callers.

    the toolkit's ReSaver CLI writes `<out.ess>` -- the .ess after the input -- and never the input
    (tools/resaver-cli/ResaverCLI.java:49); set-global, set-var and clean write it with no existence check. A ReSaver
    given ONE .ess is taken to write that one (another front end writing in place). The text fallback reads the same
    shape from the raw text."""
    calls = resaver_write_calls(command, shell, parsed)
    return (calls[0][0], [w for _, words, _ in calls for w in words]) if calls else None


def resaver_write_calls(command: str, shell: str = "", parsed=None):
    """Every write call, retaining its execution context so relative outputs follow that call's cd."""
    if "resaver" not in command.lower():
        return []
    parsed = shellparse.parse(command, shell) if parsed is None else parsed
    t = resaver_target(command, shell, parsed)
    if t is None:
        return []
    calls = []
    for e in parsed.execs:
        e = deletes.substituted(e)
        # An opaque program may be the ReSaver named elsewhere in the command.
        # Do not let one known call suppress another call's conservative scope.
        if (_is_resaver(e) or e.program == "?") and _resaver_op(e):
            if any("$" in a or "`" in a for a in e.argv):
                # A resolved input is not proof of an unresolved output. Keep
                # this call explicitly unknown so it refuses before backup.
                calls.append((_resaver_op(e), [], e))
                continue
            ws = [a for a in e.argv if a.lower().endswith(".ess")] or RESAVER_TEXT_SAVE.findall(e.segment)
            calls.append((_resaver_op(e), ws[1:] or ws, e))
    if calls:
        return calls
    ws = RESAVER_TEXT_SAVE.findall(command)
    return [(t[0], ws[1:] or ws, None)]


def resaver_apply(command: str, shell: str = "", parsed=None):
    """(operation, save basename or "") when ReSaver is executed with --apply and a write operation."""
    t = resaver_target(command, shell, parsed)
    return None if t is None else (t[0], re.split(r"[/\\]", t[1])[-1])


def decide(command: str, roots: paths.Roots, audit: _audit.Audit, cwd: str = "", shell: str = "",
           env=None, parsed=None, transcript: str = "") -> Verdict:
    """cwd: the payload's working directory, RAW (any spelling; targets normalises it). shell: "bash",
    "powershell", "cmd", or "" for unknown (parsed as bash AND PowerShell, R5). Both default so every existing
    caller stays valid (Plan 4 handshake). env: where `~`, $HOME and %USERPROFILE% resolve from (os.environ).
    parsed: the command's shellparse.parse() when the caller already holds it (engine.evaluate_shell does, to read
    whether the parse was complete); None parses here."""
    # Task 12: a whitespace-only command is no command either.
    if command.strip() == "":
        audit.rule(HOOK, "S-INERT-NOCMD", "deny", NO_COMMAND_LOG, level="REFUSE")
        return Verdict("deny", NO_COMMAND_DENY)
    if roots.game == "":
        audit.rule(HOOK, "S-INERT-NOROOT", "deny", NO_ROOT_LOG, level="REFUSE")
        return Verdict("deny", NO_ROOT_DENY)
    if roots.malformed:
        file, key = roots.malformed[0]
        reason = rules.message("S-INERT-CONFIG", file=file, op=key)
        audit.rule(HOOK, "S-INERT-CONFIG", "deny", reason, level="REFUSE")
        return Verdict("deny", reason)
    env = os.environ if env is None else env

    def deny(rule_id: str, file: str = "") -> Verdict:
        reason = rules.message(rule_id, file=file) if file else rules.message(rule_id)
        audit.rule(HOOK, rule_id, "deny", reason)
        return Verdict("deny", reason)

    # --- hard blocks
    # Deletes (Task 7): decided on the commands that RUN and where their operands RESOLVE (deletes.py, E16).
    parsed = shellparse.parse(command, shell) if parsed is None else parsed
    dels = deletes.scan(command, parsed, roots, cwd, env)
    # Plan 6 Task 12 (R12): a write -- or a delete (fix round 1, I1) -- into .skyguard/** is the CLI's alone. Checked before
    # the delete deny, because S-DEL-INSTALL is shadowed by guards off and S-SELF-STATE is not.
    store_hit = self_rules.shell_save_store_hit(command, parsed, roots, cwd, env)
    if store_hit:
        return deny("S-SAVE-STORE", store_hit)
    # User ruling 2026-10-20: an agent session transcript (where save approvals are read) is never an agent's to change.
    spoken = self_rules.shell_transcript_hit(command, parsed, cwd, env, transcript)
    if spoken:
        return deny("S-SELF-TRANSCRIPT", os.path.basename(spoken.rstrip("/")) or spoken)
    own = self_rules.shell_scan(command, parsed, roots, cwd, env)
    if own and own[0] == self_rules.STATE:
        return deny("S-SELF-STATE", own[1])
    # Plan 6 final fix (lane A C1): the lift rules are NEVER_SHADOW too, so they are read BEFORE any shadowable rule. A
    # raw unlock is a deny, so it simply goes first (deny for deny). `lock disable` is an ask: returned first it would
    # loosen a decoy's deny when no guards lift is honoured, so it is carried BESIDE whatever refusal outranks it
    # (Verdict.never) and engine.shadow returns it instead of converting the call.
    raw = lift_rules.raw_unlock(command, parsed, roots, cwd, env, shell)
    if raw:
        return deny(raw[0], raw[1])
    wr = writes.scan(command, parsed, roots, cwd, env)
    v = _ordered(command, parsed, dels, wr, own, roots, audit, cwd, shell, env)
    perm = rules.message("S-LIFT-PERMANENT")
    if v.decision in ("deny", "ask") and v.reason != perm and lift_rules.permanent(parsed, command):
        v = v._replace(never=Verdict("ask", perm))
    # The save policy (user ruling 2026-10-13): every save the command changes -- deleted, written, copied onto,
    # moved, rewritten by ReSaver --apply or saved over by DevBench -- is judged together, after the other rules.
    resaver = resaver_write_calls(command, shell, parsed)
    dev: list = []
    mcp_rules.scan_shell(command, parsed, roots, env, found=dev)      # its own prefilter skips most commands
    change = saves.shell_change(dels, wr, resaver, dev, cwd, shell, roots, env)
    return saves.judge(change, v, audit, HOOK)


def _opaque_save(parsed, roots, cwd, env, command="") -> str:
    """The first save (by name) an exec carries in its words or redirects when the guard has no idea what its program is
    (Parsed.unnamed: "?" with no candidate), or "". A word with candidates (`R=$(echo resaver-cli.sh); $R ...`) is read
    as each of them, and the save rules count and back up what those readings change."""
    for e in parsed.unnamed:
        if _resaver_op(e):                  # read as ReSaver by its operation (resaver_write_calls): counted, backed up
            continue
        e = deletes.substituted(e)
        for w in list(e.argv) + [t for _, t in e.redirects]:
            a = targets.resolve_operand(e, w, cwd, env)
            if writes.is_save(a, roots):
                return os.path.basename(a)
    if _unfinished(parsed, command):
        m = RESAVER_TEXT_SAVE.search(command)
        if m:
            return re.split(r"[/\\]", m.group(0))[-1]
    return ""


def _unfinished(parsed, command) -> bool:
    """guard3 (v4.0.0 review LOW-4): the parser stopped before the end (its token or nesting cap, R4) and the text holds
    an expansion, so a program it never reached may be one it cannot name. The two "ask first" rules then read the TEXT,
    as S-DEL-FALLBACK does for deletes."""
    return not parsed.ok and bool(_DYNAMIC.search(command))


# User ruling 2026-10-20 (v4.0.0 review L1 MEDIUM-1/2, LOWs, INFO): when the guard cannot name the program that runs
# AND the command names a path the other rules protect, the user is asked (S-OPAQUE-PROTECTED). "Cannot name": an
# unnamed program word (Parsed.unnamed: `$R`, `%R%`, `!R!`, `${!R}`, `"${A[@]}"`, `& (Get-Command ...)`, a variable
# set by printf -v or read), an eval-like call whose code is not literal (`eval "$c"`, `bash -c "$c"`, `iex $s`,
# `Invoke-Expression $s`, `source "$f"`), and awk's system(). "Names a protected path": one of those execs' own words
# resolves to the install folder itself, anything under its Data folder, the settings folder or MO2's folders, or is
# a plugin or archive file (a whole word: `'x.esp'` inside interpreter code is not one); or the command TEXT spells one
# of those folders absolutely (a string a variable holds, a here-string) or a Bethesda registry key. A save keeps S-SAVE-OPAQUE, which is checked first. Ordinary commands that run a
# named program, and an opaque program on anything else (`"$PY" tools/x.py`, `$EDITOR notes.txt`), stay as they were.
_EVAL_PROGRAMS = frozenset(("eval", "source", ".", "invoke-expression", "iex"))
_AWK_PROGRAMS = frozenset(("awk", "gawk", "mawk", "nawk", "busybox-awk"))
_DYNAMIC = re.compile(r"[$%`!]")
_PLUGIN_WORD = re.compile(r"\.(?:esp|esm|esl|bsa|ba2)$", re.IGNORECASE)
# The ruling does not require a write/delete verb: the unknown program may have been set earlier.
_TERMINATOR = "\"' \t\r\n|&;<>)`"
# The pipeline readers whose unknown input shellparse turns into a "?" exec (argv = the reader): they run TEXT.
_TEXT_READERS = frozenset(shellparse.ALL_SHELLS) | {"invoke-expression"}
_CODE_VARIABLE = re.compile(r"""\s*["']?\$\{?[A-Za-z_][A-Za-z0-9_]{0,255}\}?["']?\s*""")
# guard3 (v4.0.0 review MEDIUM-1): an MSYS spelling (`/c/Users/...`) is the drive path it names.
_MSYS = re.compile(r"(?<![\w:.~/-])/([a-z])/")
# A value the command assigns (`c="rm -rf <install>"`, `$c = '...'`): the text an eval-like call may run.
_ASSIGNED = re.compile(r"""(?<![\w$-])\$?[A-Za-z_][A-Za-z0-9_]{0,255}[ \t]{0,64}=[ \t]{0,64}"""
                       r"""(?:'([^'\n]{0,4096})'|"([^"\n]{0,4096})"|([^\s;&|'"]{1,4096}))""")


def _opaque_execs(parsed):
    """(the execs whose program the guard cannot name (see above), whether one of them runs TEXT a variable holds).
    guard3 (v4.0.0 review MEDIUM-2): every "?" exec counts, not only the candidate-less program words -- `source "$f"`,
    `. "$f"`, `$c | iex`, `Write-Output $c | iex`, `[scriptblock]::Create($c)` handed to Invoke-Command, `bash -c "$c"`
    -- and an eval-like call is judged after its same-command literals are substituted (Exec.subst): `c="..."; eval
    "$c"` is read as the text it runs (shellparse) and is no unknown program."""
    out = list(parsed.unnamed)
    evals = False
    for e in parsed.execs:
        if e.program == "?":
            if e not in out:
                out.append(e)
            evals = evals or (len(e.argv) == 1 and e.argv[0] in _TEXT_READERS)
            continue
        s = deletes.substituted(e)
        if e.program in _EVAL_PROGRAMS and any(_DYNAMIC.search(w) for w in s.argv):
            out.append(e)
            evals = True
        elif e.program in _AWK_PROGRAMS and any("system(" in w.replace(" ", "") for w in e.argv):
            out.append(e)
    dynamic = [text for _shell, text in parsed.codes if _DYNAMIC.search(text or "")]
    if dynamic:
        # Priced (guard3): a -Command string holding its own PowerShell variables is code the parser READ; only a
        # string that is wholly one variable (`bash -c "$c"`) runs text the variable holds.
        return out or [None], evals or any(_CODE_VARIABLE.fullmatch(t) for t in dynamic)
    return out, evals


def _path_like(word) -> bool:
    """A word an unnamed program may take as a path: not an option, and a path separator, a drive, a dot, or the
    install's own folder name. Priced (v4.0.0 review): with a cwd inside Data, bare words (`list`, `-r`, `l`) were
    read as Data files and asked 15 times in 97,323 history items with no path in them at all."""
    if not word or word.startswith("-"):
        return False
    return any(c in word for c in "/\\:.") or word.lower() == "data"


def _protected_word(a, roots) -> str:
    """The name to show when the resolved path `a` is the install itself, under its Data folder, in the settings
    folder (not a save: S-SAVE-OPAQUE's) or MO2's folders; "" otherwise."""
    if not a:
        return ""
    z = targets.zone(a, roots)
    if z in ("config", "mo2"):
        return "" if writes.is_save(a, roots) else (os.path.basename(a.rstrip("/")) or a)
    if z != "install":
        return ""
    rel = writes._rel(a, roots.install_dirs)
    if rel is not None and (not rel or rel[0] == "data"):
        return os.path.basename(a.rstrip("/")) or a
    return ""


def _protected_text(command, roots, root_alone=False) -> str:
    """A protected path the command TEXT spells absolutely, or a Bethesda key; "" if none. root_alone: the install
    folder itself counts too (text an eval-like call runs); elsewhere it is the priced `cd "<install>"` exemption."""
    text = command.replace("\\", "/").lower()
    text = re.sub(r"/{2,}", "/", text)
    text = _MSYS.sub(r"\1:/", text)
    for kind, dirs in (("install", roots.install_dirs), ("config", roots.config_dirs), ("mo2", roots.mo2_dirs)):
        for d in dirs:
            root = (d or "").lower().rstrip("/")
            if not root:
                continue
            start = 0
            while True:
                at = text.find(root, start)
                if at < 0:
                    break
                start = at + 1
                rest = text[at + len(root):]
                if at and text[at - 1] not in _TERMINATOR + "=:(":
                    continue
                if (not rest or rest[0] in _TERMINATOR) and kind != "install":
                    return os.path.basename(root) or root
                if (not rest or rest[0] in _TERMINATOR) and root_alone:
                    return os.path.basename(d.rstrip("/")) or d
                if not rest or rest[0] != "/":
                    continue
                first = re.split(r"[/" + re.escape(_TERMINATOR) + r"]", rest[1:], 1)[0]
                if kind == "install" and first == "data":
                    return "Data"
                if kind == "config" and first != "saves":
                    return os.path.basename(root)
                if kind == "mo2":
                    return os.path.basename(root)
    low = command.replace("/", "\\").lower()
    for key in anchored.BETHESDA_KEYS:
        if key.split("\\", 1)[1] in low:
            return "the Bethesda registry key"
    return ""


def _opaque_protected(command, parsed, roots, cwd, env) -> str:
    """The protected path (as shown) an unnamed program runs on, or "" (see the ruling above)."""
    opaque, evals = _opaque_execs(parsed)
    if not opaque:
        return _protected_text(command, roots) if _unfinished(parsed, command) else ""
    for e in opaque:
        if e is None:
            continue
        e = deletes.substituted(e)
        for w in list(e.argv) + [t for _, t in e.redirects]:
            if not _path_like(w):
                continue
            hit = _protected_word(targets.resolve_operand(e, w, cwd, env), roots)
            if hit:
                return hit
            name = re.split(r"[/\\]", w)[-1]
            if _PLUGIN_WORD.search(name):                 # a plugin or archive file, wherever it is
                return name
    hit = _protected_text(command, roots)
    if hit or not evals:
        return hit
    # guard3 (v4.0.0 review MEDIUM-1): `c="rm -rf <install>"; eval "$c"` that the parser could not read as its rm (the
    # value is not certain): the install folder itself inside a value an eval-like call may run asks too.
    for m in _ASSIGNED.finditer(command):
        hit = _protected_text(m.group(1) or m.group(2) or m.group(3) or "", roots, root_alone=True)
        if hit:
            return hit
    return ""


def _ordered(command, parsed, dels, wr, own, roots, audit, cwd, shell, env) -> Verdict:
    """decide()'s rules after the NEVER_SHADOW denies, in the bash file's order (the first refusal wins)."""
    advice = Advice()

    def deny(rule_id: str, file: str = "") -> Verdict:
        reason = rules.message(rule_id, file=file) if file else rules.message(rule_id)
        audit.rule(HOOK, rule_id, "deny", reason)
        return Verdict("deny", reason)

    def advise(rule_id: str, **fields) -> None:
        msg = rules.message(rule_id, **fields)
        audit.rule(HOOK, rule_id, "advise", msg)
        advice.add(msg)

    if dels.hit:
        # Merge of rt-fix-a + rt-fix-b (2026-10-05): rt-fix-a reads a move out of a protected folder as a delete of the
        # source; rt-fix-b's S-MOVE-ROOT names a move that takes a protected ROOT folder (the install, Data, the
        # settings folder, Saves) away. Both deny; for a root, S-MOVE-ROOT's text is the true one ("moves X away", not
        # "deletes X inside the install"), so it wins. Any other file moved out stays rt-fix-a's delete.
        if wr.move_root:
            return deny("S-MOVE-ROOT", wr.move_root)
        rid, file = dels.hit
        reason = rules.message(rid, file=file) if "{file}" in rules.RULES[rid]["template"] else rules.message(rid)
        audit.rule(HOOK, rid, "deny", reason)
        return Verdict("deny", reason)
    # Task 9: the registry, .psc-output and Champollion denies ask about the program that RUNS (anchored.py).
    if anchored.registry(command, parsed):
        return deny("S-REGISTRY")
    psc = anchored.psc_out(command, parsed)
    if psc:
        return deny("S-PSC-OUT", psc)
    # Shell writes (Task 8, E21): decided on the program that WRITES and the file it targets (writes.py).
    if wr.deny:
        reason = rules.message("S-WRITE-PLUGIN", file=wr.deny)
        audit.rule(HOOK, "S-WRITE-PLUGIN", "deny", reason)
        return Verdict("deny", reason)
    if wr.move_root:                                         # rt-fix-b: moving the install or its folders away
        return deny("S-MOVE-ROOT", wr.move_root)
    if wr.sed_crlf:                                          # P8-SED: sed -i without -b turns CRLF into LF
        return deny("S-SED-CRLF", wr.sed_crlf)
    champ = anchored.champollion(command, parsed, roots, cwd, env)
    if champ:
        return deny("S-CHAMPOLLION", champ)
    # Task 11: the DevBench console reached by curl or devbench-cli.sh (mcp_rules.py judges the same calls as the MCP hook).
    dev = mcp_rules.scan_shell(command, parsed, roots, env)
    if dev and dev[0] == "S-DEVBENCH-CRASH":
        reason = rules.message(dev[0], **dev[1])
        audit.rule(HOOK, dev[0], "deny", reason)
        return Verdict("deny", reason)
    # RT-A (port of the install's text rule): decoded text -- base64 -d, xxd -r, printf '\x..', FromBase64String --
    # run by a shell, eval, iex or source. Whatever it holds, no rule above could read it.
    if parsed.unread:
        return deny("S-SHELL-UNREAD")

    # --- advise
    if dels.relative_unknown:
        msg = rules.message("S-RELATIVE-UNKNOWN", file=dels.relative_unknown)
        audit.rule(HOOK, "S-RELATIVE-UNKNOWN", "advise", msg)
        advice.add(msg)
    # writes.py: the copy-onto-a-plugin and write-target notes, and sed -i with a backslash path (S-SED-WINPATH).
    # anchored.py: AutoMod writes, on AutoMod's own arguments.
    for rid, file in list(wr.advice) + anchored.automod(parsed):
        advise(rid, file=file)

    # --- ask (drops accumulated advice, as the bash exit does). A save change (ReSaver --apply, a write, copy, move
    # or delete of a save, a DevBench save) is no ask any more: decide() hands every one to saves.judge.
    git = anchored.git_discard(parsed, roots, cwd, env)      # rt-fix-b: git overwriting a protected working tree
    if git:
        reason = rules.message("S-GIT-DISCARD", file=git)
        audit.rule(HOOK, "S-GIT-DISCARD", "ask", reason)
        return Verdict("ask", reason)
    # User ruling 2026-10-19 ("ask first"): an unnamed program word naming a save. A save another reading changes is
    # still counted (saves.judge), and portable.check holds this ask until that save is approved and backed up.
    opaque = _opaque_save(parsed, roots, cwd, env, command)
    if opaque:
        reason = rules.message("S-SAVE-OPAQUE", file=opaque)
        audit.rule(HOOK, "S-SAVE-OPAQUE", "ask", reason)
        return Verdict("ask", reason)
    # User ruling 2026-10-20: the same "ask first" for a protected path that is not a save (_opaque_protected).
    hidden = _opaque_protected(command, parsed, roots, cwd, env)
    if hidden:
        reason = rules.message("S-OPAQUE-PROTECTED", file=hidden)
        audit.rule(HOOK, "S-OPAQUE-PROTECTED", "ask", reason)
        return Verdict("ask", reason)

    # Plan 6 Task 11 (R7): a persistent `lock disable` is the user's to approve (deny on Codex, via the adapter).
    if lift_rules.permanent(parsed, command):
        reason = rules.message("S-LIFT-PERMANENT")
        audit.rule(HOOK, "S-LIFT-PERMANENT", "ask", reason)
        return Verdict("ask", reason)
    # Plan 6 Task 15 fix round 1 (I3): an in-place snapshot restore writes over live files; the user approves it.
    if lift_rules.restore_in_place(parsed):
        reason = rules.message("S-RESTORE-INPLACE")
        audit.rule(HOOK, "S-RESTORE-INPLACE", "ask", reason)
        return Verdict("ask", reason)
    # Plan 6 Task 12 (R12, priced): a write into the guard code or config. The registry holds the priced decision.
    if own and rules.RULES["S-SELF-GUARD"]["decision"] == "ask":
        reason = rules.message("S-SELF-GUARD", file=own[1], op=own[2])
        audit.rule(HOOK, "S-SELF-GUARD", "ask", reason)
        return Verdict("ask", reason)
    if own:
        advise("S-SELF-GUARD", file=own[1], op=own[2])     # T8 fix round 2 (N4): edits / creates / makes a link to
    # D6: `lock unlock` / `guards off` are lifts the agent may take; it is told what that means.
    lifted = lift_rules.note(parsed)
    if lifted:
        advise("S-LIFT-NOTE", op=lifted)

    tool = anchored.external_out(parsed, roots, cwd, env)
    if tool:
        advise("S-EXTERNAL-OUT", op=tool)

    if advice.items:
        return Verdict("allow", "", advice.joined())
    return Verdict()
