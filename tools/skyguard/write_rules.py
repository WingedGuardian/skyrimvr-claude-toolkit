"""The write guard: protect-files.sh's rules, ported one for one (verdicts and texts unchanged).

Order is the bash file's order: the plugin-file deny comes first, then the silent whitelist, then the
advisories (which accumulate), then the catch-all, which fires only if nothing more specific did.
"""
from __future__ import annotations

from .grep import grep
from .verdict import Advice, Verdict

HOOK = "protect-files"

NO_PATH_LOG = "no file_path in payload -- hook could not evaluate its rules"
NO_PATH_DENY = (
    "GUARD INERT: protect-files.sh received no file_path and therefore checked NOTHING. Allowing silently "
    "would be indistinguishable from deciding this is fine. Investigate the hook payload before retrying."
)

DENY_PLUGIN = "BLOCKED: Cannot directly write to plugin/archive files. Use xelib, Spriggit or AutoMod."

ADV_SKYRIM_CONFIG = (
    "SKYRIM CONFIG: {p}. Load order is Skyrim.ini then SkyrimVR.ini then SkyrimPrefs.ini (last wins), so a "
    "setting here can be overridden by a later file. Diff before and after rather than trusting the edit."
)
ADV_SKSE_CONFIG = (
    "SKSE PLUGIN CONFIG: {p}. Not captured by any snapshot hook -- copy it yourself before changing it."
)
ADV_LOADORDER = (
    "LOAD ORDER: {p}. Your mod manager owns these and may overwrite a direct edit on its next deploy, so make "
    "sure a direct edit is really what is wanted. Note the Special Edition copy under AppData is a DIFFERENT "
    "file from the VR one and is routinely stale."
)
ADV_PAPYRUS = (
    "PAPYRUS SCRIPT: {p}. A .pex loads at game startup ONLY, so a running session will not see this change. "
    "Snapshot to .claude/backups/<name>/ before experimenting."
)
ADV_INSTALL = "Editing inside the live game/config install: {p}"


def decide(file_path: str, audit) -> Verdict:
    if file_path == "":
        audit.log(HOOK, "REFUSE", NO_PATH_LOG)
        return Verdict("deny", NO_PATH_DENY)

    if grep(r"\.(esp|esm|esl|bsa|ba2)[\s.]*$", file_path):
        audit.log(HOOK, "DENY", DENY_PLUGIN)
        return Verdict("deny", DENY_PLUGIN)

    if grep(r"(^|[/\\]+)\.claude[/\\]+(hooks|plans|backups|memory|projects)[/\\]+", file_path):
        return Verdict()
    if grep(r"[/\\]+node_modules[/\\]+", file_path):
        return Verdict()

    advice = Advice()

    def advise(template: str) -> None:
        text = template.format(p=file_path)
        audit.log(HOOK, "ADVISE", text)
        advice.add(text)

    if grep(r"(Skyrim\.ini|SkyrimVR\.ini|SkyrimPrefs\.ini|SkyrimCustom\.ini)$", file_path):
        advise(ADV_SKYRIM_CONFIG)
    if grep(r"[/\\]+Data[/\\]+SKSE[/\\]+Plugins[/\\]+.*\.ini$", file_path):
        advise(ADV_SKSE_CONFIG)
    if grep(r"(loadorder\.txt|plugins\.txt)$", file_path):
        advise(ADV_LOADORDER)
    if grep(r"\.(pex|psc)$", file_path):
        advise(ADV_PAPYRUS)

    if not advice.items and grep(r"(^|[/\\]+)Data[/\\]+|My Games[/\\]+Skyrim", file_path):
        advise(ADV_INSTALL)

    return Verdict("allow", "", advice.joined())
