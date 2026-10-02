"""The shell guard: protect-bash.sh's rules, ported one for one (verdicts and texts unchanged).

Rule order is the bash file's order and is load-bearing: the first deny wins, advice accumulates, and an
ask drops whatever advice came before it. Each grep is evaluated separately over all lines, exactly
as the bash pipelines are (see grep.grep).
"""
from __future__ import annotations

from . import audit as _audit
from . import paths
from .grep import grep
from .verdict import Advice, Verdict

HOOK = "protect-bash"

NO_COMMAND_LOG = "no command in payload -- hook could not evaluate its rules"
NO_COMMAND_DENY = (
    "GUARD INERT: protect-bash.sh received no command and therefore checked NOTHING. Allowing silently "
    "would be indistinguishable from deciding this is fine. Investigate the hook payload before retrying."
)
NO_ROOT_LOG = "could not derive the install root -- the delete guard evaluated NOTHING"
NO_ROOT_DENY = (
    "GUARD INERT: protect-bash.sh could not work out which directory is the Skyrim install, so its delete "
    "rules checked NOTHING. Allowing silently would be indistinguishable from having no guard at all. "
    "Re-run setup.sh from your Skyrim folder."
)

DENY_CONFIG = (
    "BLOCKED: this command would delete inside the Skyrim config directory (Documents/My Games/Skyrim). "
    "Your INIs and controlmap live there and are not recoverable from a mod manager."
)
DENY_INSTALL = (
    "BLOCKED: this command names both a deletion and a path inside the Skyrim install. If you meant to "
    "delete something elsewhere, run it as a separate command that does not also mention the game directory."
)
DENY_REGISTRY = "BLOCKED: Cannot delete Bethesda registry keys."
DENY_PSC = "BLOCKED: Tool output cannot target a .psc file directly. Use a separate output directory."
DENY_CHAMPOLLION = (
    "BLOCKED: Champollion against a PEX in Data/Scripts/ has destroyed a .psc twice. Copy the .pex to a "
    "temp directory first, then run Champollion there."
)

ADV_DELETE = (
    "Deleting files inside the live game install: {c}. Deleting the game ROOT is denied outright; this is a "
    "delete further in, which a mod manager can usually redeploy but your own mod files cannot be. Check "
    "the path is what you meant."
)
ADV_RELATIVE = (
    "Deleting inside Data/ by a RELATIVE path: {c}. The hook is never told the caller's working directory, "
    "so it cannot tell whether this resolves into the live install -- check the path is what you meant."
)
ADV_MOVE = (
    "Moving/copying inside the live game install: {c}. A stray overwrite here is silent -- confirm the "
    "destination before relying on it."
)
ADV_REDIRECT = (
    "Redirecting output into the game/config directory: {c}. A redirect TRUNCATES its target before "
    "anything is written."
)
ADV_SED = (
    "In-place sed edit in the game directory: {c}. A Windows path in sed's REPLACEMENT is destroyed by "
    "escape handling -- normalise the path first."
)
ADV_PLUGIN = (
    "Command references plugin/archive files: {c}. Reading one is fine; writing one directly corrupts it "
    "-- use xelib, Spriggit or AutoMod."
)
ADV_LOADORDER = (
    "Command references load order: {c}. Your mod manager owns these files, and the Special Edition copy "
    "under AppData is a DIFFERENT file from the VR one and is routinely stale."
)
ADV_AUTOMOD_ESP = (
    "AutoMod ESP write WITHOUT --dry-run: {c}. The standing rule is dry-run first, review, then write."
)
ADV_AUTOMOD_NIF = (
    "AutoMod NIF write: {c}. Cross-read the result with an independent parser before handing it to the "
    "engine -- same-tool readback misses malformed files."
)
ADV_AUTOMOD_ARCHIVE = (
    "AutoMod archive write: {c}. Loose files always override a BSA, so check for loose copies before "
    "assuming this took effect."
)
ASK_RESAVER = (
    "DESTRUCTIVE ReSaver save-write with --apply (reset-havok/cleanse-formlists/remove-created) MUTATES a "
    ".ess save and is never part of normal work. Explicit approval required: {c}"
)
ADV_EXTERNAL = (
    "External Papyrus/ESP tool writing into the game/data directory: {c}. Verify the output landed where "
    "you intended -- several of these have destructive defaults."
)

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


def decide(command: str, roots: paths.Roots, audit: _audit.Audit) -> Verdict:
    if command == "":
        audit.log(HOOK, "REFUSE", NO_COMMAND_LOG)
        return Verdict("deny", NO_COMMAND_DENY)
    g, c = roots.game, roots.config
    if g == "":
        audit.log(HOOK, "REFUSE", NO_ROOT_LOG)
        return Verdict("deny", NO_ROOT_DENY)
    a = g + "|" + c
    advice = Advice()

    def deny(reason: str) -> Verdict:
        audit.log(HOOK, "DENY", reason)
        return Verdict("deny", reason)

    def advise(template: str) -> None:
        msg = template.format(c=command)
        audit.log(HOOK, "ADVISE", msg)
        advice.add(msg)

    def has(pattern: str) -> bool:
        return grep(pattern, command)

    # --- hard blocks
    if has(DESTROYER):
        if has(c):
            return deny(DENY_CONFIG)
        if has(g):
            return deny(DENY_INSTALL)
    if has(r"(reg(\.exe)?\s+delete|Remove-ItemProperty.*Bethesda)"):
        return deny(DENY_REGISTRY)
    if has(r"""-(o|-output|-OutputPath)\s+["']?[^"' ]*\.psc(\b|"|')"""):
        return deny(DENY_PSC)
    if has(r"Champollion\b"):
        if has(r"(" + g + r")[/\\]+Data[/\\]+Scripts[/\\]+[^\"' /\\]+[.]pex"):
            return deny(DENY_CHAMPOLLION)

    # --- advise
    if has(r"(^|[;&|(`]|\s)(rm|rmdir|del|erase)\s.*(" + a + ")"):
        advise(ADV_DELETE)
    if has(RELATIVE_DATA_DELETE):
        advise(ADV_RELATIVE)
    if has(r"(^|[;&|(`]|\s)(mv|cp|move|copy)\s.*(" + a + ")"):
        advise(ADV_MOVE)
    if has(r""">\s*["']?(""" + g + "|" + c + ")"):
        advise(ADV_REDIRECT)
    if has(r"sed\s+-i.*(" + g + "|" + c + ")"):
        advise(ADV_SED)
    if has(r"\.(esp|esm|esl|bsa|ba2)\b"):
        advise(ADV_PLUGIN)
    if has(r"(loadorder\.txt|plugins\.txt)"):
        advise(ADV_LOADORDER)
    if has(AUTOMOD_ESP_WRITE):
        if not has(r"--dry-run"):
            advise(ADV_AUTOMOD_ESP)
    if has(r"(automod|SpookysAutomod).*\b(replace-textures|rename-strings|fix-eyes|scale)\b"):
        advise(ADV_AUTOMOD_NIF)
    if has(r"(automod|SpookysAutomod).*\b(archive\s+(create|add-files|remove-files|replace-files"
           r"|update-file|merge|optimize))\b"):
        advise(ADV_AUTOMOD_ARCHIVE)

    # --- ask (drops accumulated advice, as the bash exit does)
    if has(r"(resaver-cli|ResaverCLI)\b.*\b(reset-havok|cleanse-formlists|remove-created)\b"):
        if has(r"--apply"):
            reason = ASK_RESAVER.format(c=command)
            audit.log(HOOK, "ASK", reason)
            return Verdict("ask", reason)

    if has(r"\b(automod|SpookysAutomod|spookys-automod|automod-cli|PapyrusAssembler|PapyrusCompiler"
           r"|Champollion|Caprica|spriggit)\b"):
        if has(r"""-(o|-output|-OutputPath|-p|-psc|-asm|-a)\s+["']?(""" + g + "|" + c + ")"):
            advise(ADV_EXTERNAL)

    if advice.items:
        return Verdict("allow", "", advice.joined())
    return Verdict()
