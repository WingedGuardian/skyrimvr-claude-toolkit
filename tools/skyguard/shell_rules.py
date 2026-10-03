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
# and 1540b99, widened in Task 4 fix rounds 1 and 2). Text that merely contains the words (a heredoc to python,
# grep, echo) is not a save write. Every operation below writes a save when given --apply (tools/resaver-cli.sh).
#
# KNOWN LIMITS (unreachable by a static text rule; Minor): a program or operation held in a variable
# (`$R reset-havok ...`); a token split by quoting (`--ap""ply`, `--'apply'`); process substitution; and the
# PowerShell / cmd dialects (`pwsh -c`, `cmd /c`), which Task 6's shellparse owns.
#
# TIMING (a hook that answers after its timeout has its answer DROPPED, so a slow regex fails open): every
# quantifier that can run over an unbounded run of text is bounded ({0,260} for a path, {0,2000} for a quoted
# command), and every repetition is unambiguous (each token ends in its own \s+, no two alternatives start
# alike), so no input makes matching superlinear. tests/test_skyguard_timing.py holds this.
RESAVER_OPS = "set-global|set-var|clean|reset-havok|cleanse-formlists|remove-created"
# A heredoc delimiter is any word; a `<<` is not a heredoc after a `#` comment starts, and `<<<` never is.
RESAVER_HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)[ \t]*[\"']?([^\s\"'<>;&|()]+)")
# A heredoc FED TO A SHELL is commands, not text: its body is kept. (a) `bash <<EOF`, `/bin/bash -s <<EOF`;
RESAVER_SHELL_FED = re.compile(
    r"(^|[;&|( \t])([^\s;&|]{0,260}[/\\])?(bash|sh|zsh)(\.exe)?([ \t]+-[A-Za-z]+)*[ \t]*<<", re.ASCII | re.IGNORECASE)
# (b) `cat <<EOF | bash` (searched after the first `<<`, so it is linear).
RESAVER_PIPED_TO_SHELL = re.compile(
    r"\|\s*([^\s;&|]{0,260}[/\\])?(bash|sh|zsh)(\.exe)?(\s+-[A-Za-z]+)*\s*($|[;&|)])", re.ASCII | re.IGNORECASE)
RESAVER_QPATH = r"(?:\"[^\"]{0,260}|'[^']{0,260}|[^\s;&|\"']{0,160})"   # a program path, quoted (spaces ok) or not
RESAVER_NAME = r"(?:resaver-cli(?:\.sh)?|ResaverCLI(?:\.exe)?)[\"']?"
RESAVER_SHELL = r"(?:(?:[^\s;&|\"']{0,160}[/\\])?(?:bash|sh)(?:\.exe)?(?:\s+-[A-Za-z]+)*\s+)?"
RESAVER_JAVA = (
    r"(?:\"[^\"]{0,260}[/\\]|[^\s;&|\"']{0,160}[/\\])?java(?:\.exe)?[\"']?\s+[^;&|]{0,260}?"
    r"(?:" + RESAVER_QPATH + r"resaver[^\s;&|\"']{0,200}\.jar|ResaverCLI)[\"']?"
)
RESAVER_PROGRAM = r"(?:" + RESAVER_JAVA + r"|" + RESAVER_SHELL + RESAVER_QPATH + RESAVER_NAME + r")"
# Where a command can start: line start, ; & | ( ` $( or after a shell keyword.
RESAVER_ANCHOR = r"(?:^|[;&|(`]|\$\(|(?<![^\s])(?:do|then|else|elif|\{|!)(?=\s))"
RESAVER_ASSIGN = r"[A-Za-z_][A-Za-z0-9_]*=\S*\s+"
RESAVER_WRAP_SIMPLE = r"(?:time|nohup|exec|sudo|source|command|\.)\s+(?:-\S+\s+)*"
RESAVER_WRAP_ENV = r"env\s+(?:-\S+\s+)*"                     # its VAR=x words are RESAVER_ASSIGN, in the loop
RESAVER_WRAP_XARGS = r"xargs\s+(?:(?:-[IndPLsE]\s+[^\s-]\S*|-\S+)\s+)*"
RESAVER_WRAP_TIMEOUT = r"timeout\s+(?:(?:-[sk]\s+[^\s-]\S*|-\S+)\s+)*\d+[smhd]?\s+"
RESAVER_WRAP_NICE = r"nice\s+(?:(?:-n\s+-?\d+|-\d+)\s+)?"
RESAVER_WRAP_FIND = r"find\s+[^;&|]{0,260}?\s-exec(?:dir)?\s+"  # find ... -exec <prog> ... \;
RESAVER_PREFIX = (
    RESAVER_ANCHOR + r"\s*(?:" + RESAVER_ASSIGN + "|" + RESAVER_WRAP_SIMPLE + "|" + RESAVER_WRAP_ENV + "|"
    + RESAVER_WRAP_XARGS + "|" + RESAVER_WRAP_TIMEOUT + "|" + RESAVER_WRAP_NICE + "|" + RESAVER_WRAP_FIND + r")*"
)
RESAVER_EXEC = re.compile(RESAVER_PREFIX + RESAVER_PROGRAM + r"(?:\s[^;&|]*)?", re.ASCII | re.IGNORECASE)
# `bash -c "..."`, `sh -c '...'`, `eval "..."`: the quoted string is itself a command line.
RESAVER_SHELL_C = re.compile(
    RESAVER_PREFIX + r"(?:(?:[^\s;&|\"']{0,160}[/\\])?(?:bash|sh|zsh)(?:\.exe)?(?:\s+-[A-Za-z]+)*\s+-[A-Za-z]*c[A-Za-z]*"
    r"|eval)\s+(\"(?:[^\"\\]|\\.){0,2000}\"|'[^']{0,2000}')", re.ASCII | re.IGNORECASE)
RESAVER_APPLY = re.compile(r"--apply", re.ASCII | re.IGNORECASE)
# A whole word: `reset-havok.ess` is a file name, not the operation.
RESAVER_OP = re.compile(r"(?<![^\s\"'])(" + RESAVER_OPS + r")(?![^\s\"'])", re.ASCII | re.IGNORECASE)
RESAVER_SAVE = re.compile(
    r"\"[^\"]{0,300}\.ess\"|'[^']{0,300}\.ess'|(?<![^\s\"'])[^\s\"']{1,300}\.ess", re.ASCII | re.IGNORECASE)
RESAVER_CONTINUATION = re.compile(r"\\\r?\n")


def _piped_to_shell(line: str) -> bool:
    at = line.find("<<")
    return at >= 0 and RESAVER_PIPED_TO_SHELL.search(line, at) is not None


def drop_heredoc_bodies(command: str) -> list:
    """The command's lines without the bodies of heredocs that are text (not fed to a shell)."""
    out, term, strip = [], "", False
    for line in RESAVER_CONTINUATION.sub("", command).split("\n"):
        line = line.rstrip("\r")
        if term:
            if (line.lstrip("\t") if strip else line) == term:
                term = ""
            continue
        out.append(line)
        m = RESAVER_HEREDOC.search(line)
        if not m or re.search(r"(?:^|\s)#", line[:m.start()]):
            continue
        if RESAVER_SHELL_FED.search(line) or _piped_to_shell(line):
            continue
        strip, term = m.group(1) == "-", m.group(2)
    return out


def _quoted_commands(lines: list) -> list:
    """`lines` plus the command lines hidden inside `bash -c "..."` / `eval '...'` (nested up to three deep)."""
    out, todo = list(lines), list(lines)
    for _ in range(3):
        found = []
        for line in todo:
            for m in RESAVER_SHELL_C.finditer(line):
                body = m.group(1)[1:-1]
                if m.group(1)[0] == '"':
                    body = re.sub(r"\\([\"\\$`])", r"\1", body)
                found += body.split("\n")
        out += found
        todo = found
    return out


def resaver_apply(command: str):
    """(operation, save basename or "") when ReSaver is executed with --apply and a write operation."""
    if "resaver" not in command.lower():        # every form below names resaver; skip the regexes entirely
        return None
    lines = _quoted_commands(drop_heredoc_bodies(command))
    executed = [m.group(0) for line in lines for m in RESAVER_EXEC.finditer(line)]
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
