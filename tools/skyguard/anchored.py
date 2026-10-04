r"""Rules anchored to the program that RUNS (Plan 5 Task 9). Each of these used to be a text match: it fired on any
command whose TEXT held the words -- a printf of a ledger note, a commit message, `grep add-weapon docs/automod.md`
-- and so it fired on noise and, for the denies, blocked ordinary work (the registry rule refused a printf twice in
one session, 2026-10-03). Here each asks about an Exec from shellparse: its program, and its OWN arguments.

    registry(command, parsed)                         -> True   S-REGISTRY (deny)
    psc_out(command, parsed)                          -> file   S-PSC-OUT (deny)
    champollion(command, parsed, roots, cwd, env)     -> file   S-CHAMPOLLION (deny)
    automod(parsed)                                   -> [(rule id, file)]   S-AUTOMOD-ESP / -NIF / -ARCHIVE (advise)
    external_out(parsed, roots, cwd, env)             -> op     S-EXTERNAL-OUT (advise)

S-REGISTRY (deny). `reg delete KEY` / `reg.exe delete KEY` run as a program (also via `cmd /c`, `pwsh -Command`,
wrappers), and PowerShell Remove-Item / Remove-ItemProperty / Remove-ItemPropertyValue / Clear-ItemProperty on a
registry path (`HKLM:\`, `HKCU:\`, `Registry::`, `HKEY_...`), when KEY is a Bethesda key or an ANCESTOR of one
(`HKLM\SOFTWARE\WOW6432Node` holds `Bethesda Softworks`), or KEY is not literal (`$K`, `%K%`, a backtick, `$(...)`).
Fix round 1: a non-literal key is first resolved from a simple assignment in the same command (`K='...'; reg delete
"$K"`, PowerShell `$k = '...'`, cmd `set K=...`) and the result judged like a literal; a key that still cannot be
known DENIES (not safe by default -- this re-denies loops such as `for k in ...; do reg delete "HKCU\...\$k"`). A
PowerShell path that cannot be known denies when the command names a hive. A delete of an unrelated LITERAL key
(`reg delete HKCU\Software\SomeTool`) is not this rule's business. Interpreter code (`python -c`, a heredoc to
python, `node -e`; `os.system`, `subprocess.run(['reg', 'delete', ...])`, `execSync`) is read for the key that
follows `reg delete` and judged the same way; a key it does not spell out (a variable, a concatenation) denies.

S-PSC-OUT (deny). An executed program whose OWN arguments aim `-o` / `--output` / `-OutputPath` (also `--output=X`)
at a `.psc`: the output would replace a source. Programs that only print or search text are not writers (`echo`,
`printf`, `grep -o x.psc` -- grep's -o is "only matching").

S-CHAMPOLLION (deny). Champollion runs with a `.pex` operand that RESOLVES (payload cwd, Task 6) under the install's
`Data/Scripts`, or with that folder itself (it decompiles the whole folder). An unresolved operand counts when its
own words put a .pex under a `Data/Scripts` folder. Before Task 9 only an absolute install path matched, so
`Champollion Data/Scripts/x.pex` run from the install was allowed (a tightening).

S-AUTOMOD-* (advise). AutoMod is executed (`automod-cli(.sh)`, `automod`, `spookys-automod`, `dotnet
...spookys-automod.dll`) with module + command among ITS OWN arguments: `esp <write command>` without --dry-run;
`nif replace-textures|rename-strings|fix-eyes|scale`; `archive create|add-files|...`. A --dry-run writes nothing,
so no note for any of the three (the legacy NIF and archive notes fired on dry runs too).

S-EXTERNAL-OUT (advise). A DECOMPILER is executed -- Champollion, or AutoMod `papyrus decompile` -- and its OWN output
flag (Champollion -p/-a/-o, AutoMod -o/--output, also `--flag=value`) resolves into the install's Data/ folder, the
config folder or MO2 (the area of S-WRITE-INSTALL): decompiled .psc written there can replace real sources.
Compilers (Caprica, PapyrusCompiler) and Spriggit deserialize are NOT noted (Ruling, priced: 496 of 501 hits were
those tools writing their product where it belongs; the legacy text rule's 248 were the same).

NOT SAFE BY DEFAULT (R4). On a parse failure the two text-matched DENY rules (registry, psc) fall back to the legacy
regexes over the whole text, and Champollion to the legacy co-occurrence test. For an opaque program ("?") the
legacy regexes read its own ARGUMENTS and, when it reads a pipe, the words of the program feeding it (`"$X" reg delete
KEY`, `printf 'reg delete KEY' | sh`). The advise rules are NOT attempted on a parse failure (as S-WRITE-INSTALL in
Task 8): a note there would be the same text-match noise this task removes.

KNOWN LIMITS (recorded, not handled):
  - an opaque program WORD (`R=reg; $R delete KEY`): its arguments do not hold the word `reg`, so the legacy regex
    cannot match either;
  - PowerShell keys reached without a -Path: piped (`Get-Item HKLM:\...\Bethesda Softworks | Remove-Item`) or relative
    to a registry location (`Set-Location HKLM:\SOFTWARE\WOW6432Node; Remove-Item 'Bethesda Softworks'`);
  - `reg import x.reg` and `regedit /s x.reg` (the .reg file's contents decide; it is not read), and interpreter
    registry APIs (`winreg.DeleteKey`, `Microsoft.Win32.Registry` calls);
  - an output path held in a variable; AutoMod run through an opaque script gets no note; a Caprica output folder
    given only by its project file (`.ppj`) is not read.
"""
from __future__ import annotations

import re
from typing import List, Mapping, Optional, Tuple

from . import shellparse, targets
from .deletes import interpreter_code
from .grep import grep
from .shellparse import ps_param

# ---------------------------------------------------------------------------------------------------- registry
REG_DELETE_TEXT = r"reg(\.exe)?\s+delete"                     # the legacy regexes (fallback, interpreter code)
REG_PROPERTY_TEXT = r"Remove-ItemProperty.*Bethesda"
BETHESDA_KEYS = ("hklm\\software\\bethesda softworks", "hklm\\software\\wow6432node\\bethesda softworks",
                 "hkcu\\software\\bethesda softworks")
_HIVES = (("hkey_local_machine", "hklm"), ("hkey_current_user", "hkcu"))
# Every registry form names one of these (reg / reg.exe, a hive, or the Registry:: provider): a cheap prefilter.
_REG_WORDS = ("reg", "hklm", "hkcu", "hkey_")
PS_REG_PROGRAMS = frozenset(("remove-item", "remove-itemproperty", "remove-itempropertyvalue", "clear-itemproperty"))
_HIVE_WORD = re.compile(r"\b(?:hklm|hkcu|hkey_local_machine|hkey_current_user)\b|registry::", re.IGNORECASE)
_REG_ROOT = re.compile(r"(?:registry::)?(hklm|hkcu|hkey_local_machine|hkey_current_user)(?::|\\|$)", re.IGNORECASE)


def _reg_key(word: str) -> str:
    """A registry key spelled any way, as `hklm\\software\\...` (lower case, no trailing separator)."""
    k = word.strip().replace("/", "\\").lower()
    if k.startswith("registry::"):
        k = k[len("registry::"):]
    k = re.sub(r"\\+", r"\\", k).rstrip("\\")
    for long, short in _HIVES:
        if k == long or k.startswith(long + "\\") or k.startswith(long + ":"):
            k = short + k[len(long):]
    if k[4:5] == ":":                                         # hklm:\software -> hklm\software
        k = k[:4] + k[5:]
    return re.sub(r"\\+", r"\\", k)


_NONLITERAL = re.compile(r"[$%`]")
# Simple assignments earlier in the same command (fix round 1): bash `K='v'` / `K="v"` / `K=v`, PowerShell `$K = 'v'`,
# cmd `set K=v`. A name assigned twice is ambiguous and stays unresolved. Linear: one finditer per form.
_BASH_ASSIGN = re.compile(r"""(?:^|[\s;&|(])([A-Za-z_][A-Za-z0-9_]{0,63})=(?:'([^'\n]*)'|"([^"$`\n]*)"|([^\s;&|'"$`()]+))""")
_PS_ASSIGN = re.compile(r"""\$([A-Za-z_][A-Za-z0-9_]{0,63})[ \t]*=[ \t]*(?:'([^'\n]*)'|"([^"$`\n]*)")""")
_CMD_ASSIGN = re.compile(r"""(?i:\bset)[ \t]+"?([A-Za-z_][A-Za-z0-9_]{0,63})=([^&|"\r\n]*)""")
_VAR_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)|%([A-Za-z_][A-Za-z0-9_]*)%")


def assignments(command: str) -> dict:
    """name (upper case) -> value for the simple assignments in `command`; an ambiguous name maps to None."""
    out = {}
    # cmd's `set K=v w` first: the bash form would also read `K=v` inside it, so matches inside a `set` are skipped.
    spans = [m.span() for m in _CMD_ASSIGN.finditer(command)]
    for rx in (_CMD_ASSIGN, _BASH_ASSIGN, _PS_ASSIGN):
        for m in rx.finditer(command):
            if rx is not _CMD_ASSIGN and any(a <= m.start(1) < b for a, b in spans):
                continue
            name = m.group(1).upper()
            value = next((g for g in m.groups()[1:] if g is not None), "").rstrip()
            out[name] = value if out.get(name, value) == value else None
    return out


def _substitute(word: str, assigned: dict) -> Optional[str]:
    """`word` with each $NAME / ${NAME} / %NAME% replaced by its assignment; None when any cannot be."""
    def one(m):
        v = assigned.get((m.group(1) or m.group(2) or m.group(3)).upper())
        if v is None:
            raise KeyError
        return v
    try:
        out = _VAR_REF.sub(one, word)
    except KeyError:
        return None
    return None if _NONLITERAL.search(out) else out


def bethesda_key(word: str, command: str, assigned: Optional[dict] = None) -> bool:
    """True when deleting `word` may delete a Bethesda key: it IS one or lies under one ("bethesda" in it), it is an
    ANCESTOR of one, or it is not literal and cannot be resolved from a simple assignment in the same command
    (fix round 1: an unknown key is not safe by default -- `reg delete "$KEY"` denies)."""
    if _NONLITERAL.search(word):
        word = _substitute(word, assignments(command) if assigned is None else assigned)
        if word is None:
            return True
    k = _reg_key(word)
    if not k:
        return False
    if "bethesda" in k:
        return True
    return any(b.startswith(k + "\\") for b in BETHESDA_KEYS)


# The key that follows `reg delete` in INTERPRETER code (fix round 1): `os.system('reg delete KEY /f')`,
# `subprocess.run(['reg', 'delete', r'KEY', '/f'])`, `execSync("reg.exe delete \"KEY\"")`. A quoted key keeps its
# spaces; anything else (a variable, a concatenation, an f-string) is not literal.
_CODE_REG_DELETE = re.compile(
    r"""\breg(?:\.exe)?\b['"]?(?:\s*,\s*|\s+)[rbu]?['"]?delete\b['"]?(\s*,\s*|\s+)"""
    r"""(?:[rbu]?\\?(['"])([^'"\n]{0,512})\2|([^\s'",)\]]{1,512}))""", re.IGNORECASE)
# `'reg delete ' + key`, `"reg delete " % key`, `"reg delete ".format(key)`: the string ENDS at delete and the key is
# joined on from a variable.
_CODE_REG_DELETE_JOINED = re.compile(r"""\breg(?:\.exe)?\s+delete\s*['"]\s*(?:\+|%|\.format\b)""", re.IGNORECASE)


def code_deletes_bethesda(code: str) -> bool:
    """Interpreter code that runs `reg delete` on a Bethesda key, an ancestor of one, or a key it does not spell out:
    a key joined on from a variable, an f-string `{key}`, `%s`, or -- in an argument LIST (`['reg', 'delete', key]`)
    -- a bare name, which is a variable there. Words after `reg delete` inside a string are the key as written, so
    `print('reg delete is blocked')` names the key `is` and is not a Bethesda delete."""
    if _CODE_REG_DELETE_JOINED.search(code):
        return True
    for m in _CODE_REG_DELETE.finditer(code):
        listed, quoted = "," in m.group(1), m.group(2) is not None
        key = m.group(3) if quoted else m.group(4)
        if (listed and not quoted) or not key or "{" in key or bethesda_key(key, code, {}):
            return True
    return False


def _is_reg_path(word: str) -> bool:
    return bool(_REG_ROOT.match(word.strip()))


def _legacy_registry(text: str) -> bool:
    return grep(REG_DELETE_TEXT, text) or grep(REG_PROPERTY_TEXT, text)


def registry(command: str, parsed) -> bool:
    low = command.lower()
    if not any(w in low for w in _REG_WORDS):
        return False                                          # every form names one of these; skip the rest
    if not parsed.ok:
        return _legacy_registry(command)
    # An opaque program ("?") may run anything it is given: its own words and, when it reads a pipe, the words of the
    # program feeding it (`printf 'reg delete ...' | sh`) get the legacy text test (writes._opaque_texts, Task 8).
    from .writes import _opaque_texts
    if any(_legacy_registry(t) for t in _opaque_texts(parsed.execs)):
        return True
    assigned = assignments(command)
    names_hive = bool(_HIVE_WORD.search(command))
    for e in parsed.execs:
        if e.program == "reg" and e.argv and e.argv[0].lower() == "delete":
            if len(e.argv) < 2 or bethesda_key(e.argv[1], command, assigned):
                return True
        elif e.program in PS_REG_PROGRAMS:
            for w in ps_param(e, "path", 0) + ps_param(e, "literalpath"):
                if _NONLITERAL.search(w):
                    v = _substitute(w, assigned)
                    if v is None:
                        if names_hive:
                            return True                       # an unknown path in a command that names a hive
                        continue
                    w = v
                if _is_reg_path(w) and bethesda_key(w, command, assigned):
                    return True
        else:
            code = interpreter_code(e)
            if code and code_deletes_bethesda(code):
                return True
    return False


# ---------------------------------------------------------------------------------------------------- .psc output
PSC_OUT_TEXT = r"""-(o|-output|-OutputPath)\s+["']?[^"' ]*\.psc(\b|"|')"""      # the legacy regex (fallback)
OUT_FLAGS = frozenset(("-o", "--output", "-outputpath", "--outputpath"))
TEXT_PROGRAMS = frozenset(("echo", "printf", "write-output", "write-host", "grep", "egrep", "fgrep", "rg",
                           "select-string", "findstr", "git", ""))


def _flag_values(argv, flags) -> List[str]:
    """Values of any of `flags` among argv: `-o X`, `--output=X` (flags compared case-insensitively)."""
    out = []
    for i, w in enumerate(argv):
        lw = w.lower()
        if lw in flags and i + 1 < len(argv):
            out.append(argv[i + 1])
        elif "=" in lw and lw.split("=", 1)[0] in flags:
            out.append(w.split("=", 1)[1])
    return out


def _base(word: str) -> str:
    return re.split(r"[/\\]+", word.rstrip("/\\"))[-1] or word


def psc_out(command: str, parsed) -> str:
    """The .psc basename an executed program's own output flag targets ("" for none)."""
    if ".psc" not in command.lower():
        return ""
    if not parsed.ok:
        m = re.search(r"[^\s\"'/\\]{0,255}\.psc", command, re.IGNORECASE) if grep(PSC_OUT_TEXT, command) else None
        return m.group(0) if m else ""
    for e in parsed.execs:
        if e.program == "?":
            if grep(PSC_OUT_TEXT, e.segment):
                m = re.search(r"[^\s\"'/\\]{0,255}\.psc", e.segment, re.IGNORECASE)
                return m.group(0) if m else "a .psc"
            continue
        if e.program in TEXT_PROGRAMS:
            continue
        for v in _flag_values(e.argv, OUT_FLAGS):
            if v.lower().rstrip("\"' ").endswith(".psc"):
                return _base(v)
    return ""


# ---------------------------------------------------------------------------------------------------- Champollion
CHAMPOLLION_TEXT = r"Champollion\b"
_DATA_SCRIPTS_PEX = re.compile(r"(^|[/\\])data[/\\]+scripts[/\\]+(.+[/\\]+)?[^/\\]+\.pex$", re.IGNORECASE)
_CHAMP_VALUED = frozenset(("-p", "--psc", "-a", "--asm", "-t", "--threads", "--comment"))


def _data_scripts(abs_path: str, roots) -> Optional[List[str]]:
    """The components of abs_path below the install's Data/Scripts (None when it is not there)."""
    if targets.zone(abs_path, roots) != "install":
        return None
    p = abs_path.lower().rstrip("/")
    best = None
    for d in roots.install_dirs:
        dl = d.lower().rstrip("/")
        if (p == dl or p.startswith(dl + "/")) and (best is None or len(dl) > len(best)):
            best = dl
    if best is None:
        return None
    rel = [c for c in p[len(best):].split("/") if c]
    return rel[2:] if rel[:2] == ["data", "scripts"] else None


def _champollion_operands(argv) -> List[str]:
    out, k = [], 0
    while k < len(argv):
        w = argv[k]
        if w.lower() in _CHAMP_VALUED:
            k += 2
            continue
        if not (w.startswith("-") and len(w) > 1):
            out.append(w)
        k += 1
    return out


def champollion(command: str, parsed, roots, cwd: str, env: Mapping[str, str]) -> str:
    """The .pex (or folder) basename Champollion would decompile inside the install's Data/Scripts ("" for none)."""
    if "champollion" not in command.lower():
        return ""
    g = "|".join(re.escape(d) for d in roots.install_dirs) or "(?!)"
    legacy = r"(" + g + r")[/\\]+Data[/\\]+Scripts[/\\]+[^\"' /\\]+[.]pex"

    def text_hit(text: str) -> str:
        if grep(CHAMPOLLION_TEXT, text) and grep(legacy, text):
            m = re.search(r"[^\s\"'/\\]{1,255}\.pex", text, re.IGNORECASE)
            return m.group(0) if m else "a .pex"
        return ""
    if not parsed.ok:
        return text_hit(command)
    for e in parsed.execs:
        if e.program == "?":
            hit = text_hit(e.segment)
            if hit:
                return hit
            continue
        if e.program != "champollion":
            continue
        for w in _champollion_operands(e.argv):
            a = targets.resolve_operand(e, w, cwd, env)
            if a is None:
                if _DATA_SCRIPTS_PEX.search(w):
                    return _base(w)
                continue
            rel = _data_scripts(a, roots)
            if rel is not None and (not rel or rel[-1].endswith(".pex")):
                return _base(w) if rel else "Scripts"
    return ""


# ---------------------------------------------------------------------------------------------------- AutoMod
AUTOMOD_PROGRAMS = frozenset(("automod-cli", "automod", "spookys-automod", "spookysautomod", "spookys-automod-cli"))
ESP_WRITES = frozenset(("add-weapon", "add-spell", "add-armor", "add-npc", "add-quest", "add-perk", "add-book",
                        "add-global", "add-faction", "add-leveled-item", "add-form-list", "add-encounter-zone",
                        "add-location", "add-outfit", "attach-script", "set-property", "auto-fill", "merge",
                        "generate-seq"))
NIF_WRITES = frozenset(("replace-textures", "rename-strings", "fix-eyes", "scale"))
ARCHIVE_WRITES = frozenset(("create", "add-files", "remove-files", "replace-files", "update-file", "merge",
                            "optimize"))
_EXT_ESP = (".esp", ".esm", ".esl")
_EXT_ARCHIVE = (".bsa", ".ba2")


def _automod_args(e) -> Optional[Tuple[str, ...]]:
    """AutoMod's own arguments when this exec runs AutoMod, else None."""
    if e.program in AUTOMOD_PROGRAMS:
        return e.argv
    if e.program == "dotnet":
        for i, w in enumerate(e.argv):
            if w.startswith("-"):
                continue
            name = shellparse.program_name(w, "bash")
            name = name[:-4] if name.endswith(".dll") else name
            return e.argv[i + 1:] if name in AUTOMOD_PROGRAMS else None
    return None


def _first(words, exts, default: str) -> str:
    hit = next((w for w in words if w.lower().rstrip("\"' ").endswith(exts)), None)
    return _base(hit) if hit else default


def automod(parsed) -> List[Tuple[str, str]]:
    """(rule id, file) for each AutoMod write this command runs (not on a parse failure; see the docstring)."""
    out = []
    if not parsed.ok:
        return out
    for e in parsed.execs:
        args = _automod_args(e)
        if args is None:
            continue
        words = [w for w in args if not w.startswith("-")]
        if len(words) < 2 or any(w.lower() == "--dry-run" for w in args):
            continue
        module, cmd = words[0].lower(), words[1].lower()
        rest = list(args[args.index(words[1]) + 1:])
        if module == "esp" and cmd in ESP_WRITES:
            out.append(("S-AUTOMOD-ESP", _first(rest, _EXT_ESP, "the plugin")))
        elif module == "nif" and cmd in NIF_WRITES:
            out.append(("S-AUTOMOD-NIF", _first(rest, (".nif",), "the mesh")))
        elif module == "archive" and cmd in ARCHIVE_WRITES:
            out.append(("S-AUTOMOD-ARCHIVE", _first(rest, _EXT_ARCHIVE, "the archive")))
    return out


# ---------------------------------------------------------------------------------------------------- external output
# DECOMPILERS only (priced, Task 9): of 501 output-into-the-install hits in 62,458 real calls, 369 were Caprica compiling
# into Data/Scripts and 127 Spriggit deserializing the user's own plugin into Data/ -- each tool writing its product to
# the place it belongs, every time. A decompiler writing .psc INTO the install is the case that has destroyed sources.
CHAMPOLLION_OUT = frozenset(("-p", "--psc", "-a", "--asm", "-o", "--output"))
AUTOMOD_OUT = frozenset(("-o", "--output"))


def _decompiler(e) -> Tuple[str, Tuple[str, ...], frozenset]:
    """(display name, the tool's own arguments, its output flags) when this exec runs a decompiler, else empty."""
    if e.program == "champollion":
        return "Champollion", e.argv, CHAMPOLLION_OUT
    args = _automod_args(e)
    if args is not None:
        words = [w.lower() for w in args if not w.startswith("-")]
        if words[:2] == ["papyrus", "decompile"]:
            return "AutoMod", args, AUTOMOD_OUT
    return "", (), frozenset()


def external_out(parsed, roots, cwd: str, env: Mapping[str, str]) -> str:
    """The decompiler whose own output flag resolves into the install's Data/, the config folder or MO2 ("")."""
    from .writes import in_install_area
    if not parsed.ok:
        return ""
    for e in parsed.execs:
        name, args, flags = _decompiler(e)
        if not name:
            continue
        for v in _flag_values(args, flags):
            a = targets.resolve_operand(e, v, cwd, env)
            if a is not None and in_install_area(a, roots):
                return name
    return ""
