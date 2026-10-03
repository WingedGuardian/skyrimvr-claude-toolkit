"""The shell guard: protect-bash.sh's rules, ported one for one (verdicts and texts unchanged).

Rule order is the bash file's order and is load-bearing: the first deny wins, advice accumulates, and an
ask drops whatever advice came before it. Each grep is evaluated separately over all lines, exactly
as the bash pipelines are (see grep.grep).
"""
from __future__ import annotations

import re

from . import audit as _audit
from . import paths
from . import rules
from . import shellparse
from .grep import grep
from .verdict import Advice, Verdict

HOOK = "protect-bash"

NO_COMMAND_LOG = "no command in payload -- hook could not evaluate its rules"
NO_COMMAND_DENY = rules.RULES["S-INERT-NOCMD"]["template"]
NO_ROOT_LOG = "could not derive the install root -- the delete guard evaluated NOTHING"
NO_ROOT_DENY = rules.RULES["S-INERT-NOROOT"]["template"]

DENY_CONFIG = rules.RULES["S-DEL-CONFIG"]["template"]
DENY_INSTALL = rules.RULES["S-DEL-INSTALL"]["template"]
DENY_REGISTRY = rules.RULES["S-REGISTRY"]["template"]
DENY_PSC = rules.RULES["S-PSC-OUT"]["template"]
DENY_CHAMPOLLION = rules.RULES["S-CHAMPOLLION"]["template"]

ADV_DELETE = rules.RULES["S-DEL-ADVISE"]["template"]
ADV_RELATIVE = rules.RULES["S-RELATIVE-UNKNOWN"]["template"]
ADV_MOVE = rules.RULES["S-MOVE-INSTALL"]["template"]
ADV_REDIRECT = rules.RULES["S-REDIRECT-INSTALL"]["template"]
ADV_SED = rules.RULES["S-SED-WINPATH"]["template"]
ADV_PLUGIN = rules.RULES["S-PLUGIN-REF"]["template"]
ADV_LOADORDER = rules.RULES["S-LOADORDER-REF"]["template"]
ADV_AUTOMOD_ESP = rules.RULES["S-AUTOMOD-ESP"]["template"]
ADV_AUTOMOD_NIF = rules.RULES["S-AUTOMOD-NIF"]["template"]
ADV_AUTOMOD_ARCHIVE = rules.RULES["S-AUTOMOD-ARCHIVE"]["template"]
ADV_EXTERNAL = rules.RULES["S-EXTERNAL-OUT"]["template"]

DESTROYER = (
    r"(^|[;&|(`]|\s)(rm|rmdir|del|erase)\s|Remove-Item|shutil\.rmtree|rmtree\s*\(|\s-delete(\s|$)"
    r"|Remove-ItemProperty"
)
RELATIVE_DATA_DELETE = r"(^|[;&|(`]|\s)(rm|rmdir|del|erase)(\s+-\S*)*\s+(\S*[/\\])?Data[/\\]+"  # one separator before Data: `\S*` already spans a run, so `[/\\]+` was quadratic
AUTOMOD_ESP_WRITE = (
    r"(automod|SpookysAutomod).*\b(add-weapon|add-spell|add-armor|add-npc|add-quest|add-perk|add-book"
    r"|add-global|add-faction|add-leveled-item|add-form-list|add-encounter-zone|add-location|add-outfit"
    r"|attach-script|set-property|auto-fill|merge|generate-seq)\b"
)


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
# instead: `resaver`, `--apply` and an operation word all present asks (Task 5 carry: "?" is unresolved, never safe).
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


def _save_of(words) -> str:
    for a in words:
        if a.lower().endswith(".ess"):
            return re.split(r"[/\\]", a)[-1]
    return ""


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


def resaver_apply(command: str, shell: str = ""):
    """(operation, save basename or "") when ReSaver is executed with --apply and a write operation."""
    if "resaver" not in command.lower():        # every form names resaver; skip the parse entirely
        return None
    parsed = shellparse.parse(command, shell)
    opaque = False
    for e in parsed.execs:
        if e.program == "?":
            opaque = True
            continue
        if _is_resaver(e):
            op = _resaver_op(e)
            if op:
                # No .ess among its own arguments (`find -name "*.ess" -exec ... {}`): name the one its segment selects.
                s = _save_of(e.argv) or (lambda m: m.group(0) if m else "")(RESAVER_TEXT_SAVE.search(e.segment))
                return op, s
    if not parsed.ok or opaque:
        return _text_fallback(command)
    return None


def decide(command: str, roots: paths.Roots, audit: _audit.Audit, cwd: str = "", shell: str = "") -> Verdict:
    """cwd: the payload's working directory, RAW (any spelling; targets normalises it). shell: "bash",
    "powershell", "cmd", or "" for unknown (parsed as bash AND PowerShell, R5). Both default so every existing
    caller stays valid (Plan 4 handshake); no rule reads cwd yet -- Task 7's delete rules are its first user."""
    if command == "":
        audit.rule(HOOK, "S-INERT-NOCMD", "deny", NO_COMMAND_LOG, level="REFUSE")
        return Verdict("deny", NO_COMMAND_DENY)
    g, c = roots.game, roots.config
    if g == "":
        audit.rule(HOOK, "S-INERT-NOROOT", "deny", NO_ROOT_LOG, level="REFUSE")
        return Verdict("deny", NO_ROOT_DENY)
    if roots.malformed:
        file, key = roots.malformed[0]
        reason = rules.message("S-INERT-CONFIG", file=file, op=key)
        audit.rule(HOOK, "S-INERT-CONFIG", "deny", reason, level="REFUSE")
        return Verdict("deny", reason)
    a = g + "|" + c
    advice = Advice()

    def deny(rule_id: str) -> Verdict:
        reason = rules.legacy_message(rule_id, c=command)
        audit.rule(HOOK, rule_id, "deny", reason)
        return Verdict("deny", reason)

    def advise(rule_id: str) -> None:
        msg = rules.legacy_message(rule_id, c=command)
        audit.rule(HOOK, rule_id, "advise", msg)
        advice.add(msg)

    def has(pattern: str) -> bool:
        return grep(pattern, command)

    # --- hard blocks
    if has(DESTROYER):
        if has(c):
            return deny("S-DEL-CONFIG")
        if has(g):
            return deny("S-DEL-INSTALL")
    # Two greps, not one alternation: a `.*` inside a group is not split (grep.split_dotstar) and was quadratic.
    if has(r"reg(\.exe)?\s+delete") or has(r"Remove-ItemProperty.*Bethesda"):
        return deny("S-REGISTRY")
    if has(r"""-(o|-output|-OutputPath)\s+["']?[^"' ]*\.psc(\b|"|')"""):
        return deny("S-PSC-OUT")
    if has(r"Champollion\b"):
        if has(r"(" + g + r")[/\\]+Data[/\\]+Scripts[/\\]+[^\"' /\\]+[.]pex"):
            return deny("S-CHAMPOLLION")

    # --- advise
    if has(r"(^|[;&|(`]|\s)(rm|rmdir|del|erase)\s.*(" + a + ")"):
        advise("S-DEL-ADVISE")
    if has(RELATIVE_DATA_DELETE):
        advise("S-RELATIVE-UNKNOWN")
    if has(r"(^|[;&|(`]|\s)(mv|cp|move|copy)\s.*(" + a + ")"):
        advise("S-MOVE-INSTALL")
    if has(r""">\s*["']?(""" + g + "|" + c + ")"):
        advise("S-REDIRECT-INSTALL")
    if has(r"sed\s+-i.*(" + g + "|" + c + ")"):
        advise("S-SED-WINPATH")
    if has(r"\.(esp|esm|esl|bsa|ba2)\b"):
        advise("S-PLUGIN-REF")
    if has(r"(loadorder\.txt|plugins\.txt)"):
        advise("S-LOADORDER-REF")
    if has(AUTOMOD_ESP_WRITE):
        if not has(r"--dry-run"):
            advise("S-AUTOMOD-ESP")
    if has(r"(automod|SpookysAutomod).*\b(replace-textures|rename-strings|fix-eyes|scale)\b"):
        advise("S-AUTOMOD-NIF")
    if has(r"(automod|SpookysAutomod).*\b(archive\s+(create|add-files|remove-files|replace-files"
           r"|update-file|merge|optimize))\b"):
        advise("S-AUTOMOD-ARCHIVE")

    # --- ask (drops accumulated advice, as the bash exit does)
    resaver = resaver_apply(command, shell)
    if resaver:
        op, save = resaver
        reason = rules.message("S-RESAVER-APPLY", op=op, save=save or "the save")
        audit.rule(HOOK, "S-RESAVER-APPLY", "ask", reason)
        return Verdict("ask", reason)

    if has(r"\b(automod|SpookysAutomod|spookys-automod|automod-cli|PapyrusAssembler|PapyrusCompiler"
           r"|Champollion|Caprica|spriggit)\b"):
        if has(r"""-(o|-output|-OutputPath|-p|-psc|-asm|-a)\s+["']?(""" + g + "|" + c + ")"):
            advise("S-EXTERNAL-OUT")

    if advice.items:
        return Verdict("allow", "", advice.joined())
    return Verdict()
