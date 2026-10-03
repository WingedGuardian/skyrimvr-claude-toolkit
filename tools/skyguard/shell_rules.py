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
RELATIVE_DATA_DELETE = r"(^|[;&|(`]|\s)(rm|rmdir|del|erase)(\s+-\S*)*\s+(\S*[/\\]+)?Data[/\\]+"
AUTOMOD_ESP_WRITE = (
    r"(automod|SpookysAutomod).*\b(add-weapon|add-spell|add-armor|add-npc|add-quest|add-perk|add-book"
    r"|add-global|add-faction|add-leveled-item|add-form-list|add-encounter-zone|add-location|add-outfit"
    r"|attach-script|set-property|auto-fill|merge|generate-seq)\b"
)


# --- S-RESAVER-APPLY: ReSaver is the program RUN, with --apply in the same command (port of install 4a429bd
# and 1540b99). Text that merely contains the words (a heredoc to python, grep, echo) is not a save write.
RESAVER_HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)[ \t]*[\"']?([A-Za-z_][A-Za-z0-9_]*)")
# A heredoc fed to a shell (bash <<EOF, sh -s <<EOF) is commands, not text: its body is kept.
RESAVER_SHELL_FED = re.compile(r"(^|[;&|( \t])(bash|sh)([ \t]+-[A-Za-z]+)*[ \t]*<<")
RESAVER_PROGRAM = (
    r"(?:java(?:\.exe)?[ \t]+(?:-[^\s;&|]+[ \t]+)*[^\s;&|]*resaver[^\s;&|]*\.jar"
    r"|(?:(?:bash|sh)\s+)?[^\s;&|]*(?:resaver-cli(?:\.sh)?|ResaverCLI))"
)
RESAVER_EXEC = re.compile(
    r"(?:^|[;&|(]|\$\()\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*" + RESAVER_PROGRAM + r"(?:\s[^;&|]*)?",
    re.ASCII | re.IGNORECASE)
RESAVER_APPLY = re.compile(r"--apply", re.ASCII | re.IGNORECASE)
RESAVER_OP = re.compile(r"\b(reset-havok|cleanse-formlists|remove-created)\b", re.ASCII | re.IGNORECASE)
RESAVER_SAVE = re.compile(r"\"[^\"]*\.ess\"|'[^']*\.ess'|[^\s\"']+\.ess", re.ASCII | re.IGNORECASE)


def drop_heredoc_bodies(command: str) -> list:
    """The command's lines without the bodies of heredocs that are text (not fed to a shell)."""
    out, term, strip = [], "", False
    for line in command.split("\n"):
        line = line.rstrip("\r")
        if term:
            if (line.lstrip("\t") if strip else line) == term:
                term = ""
            continue
        out.append(line)
        if RESAVER_SHELL_FED.search(line):
            continue
        m = RESAVER_HEREDOC.search(line)
        if m:
            strip, term = m.group(1) == "-", m.group(2)
    return out


def resaver_apply(command: str):
    """(operation, save basename or "") when ReSaver is executed with --apply and a write operation."""
    executed = [m.group(0) for line in drop_heredoc_bodies(command) for m in RESAVER_EXEC.finditer(line)]
    op = ""
    for seg in executed:
        if RESAVER_APPLY.search(seg):
            found = RESAVER_OP.search(seg)
            if found:
                op = found.group(1)
                break
    if not op:
        return None
    save = ""
    for seg in executed:
        found = RESAVER_SAVE.search(seg)
        if found:
            save = re.split(r"[/\\]", found.group(0).replace('"', "").replace("'", ""))[-1]
            break
    return op, save


def decide(command: str, roots: paths.Roots, audit: _audit.Audit) -> Verdict:
    if command == "":
        audit.rule(HOOK, "S-INERT-NOCMD", "deny", NO_COMMAND_LOG, level="REFUSE")
        return Verdict("deny", NO_COMMAND_DENY)
    g, c = roots.game, roots.config
    if g == "":
        audit.rule(HOOK, "S-INERT-NOROOT", "deny", NO_ROOT_LOG, level="REFUSE")
        return Verdict("deny", NO_ROOT_DENY)
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
    if has(r"(reg(\.exe)?\s+delete|Remove-ItemProperty.*Bethesda)"):
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
    resaver = resaver_apply(command)
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
