"""The write guard: protect-files.sh's rules, ported one for one (verdicts and texts unchanged).

Order is the bash file's order: the plugin-file deny comes first, then the silent whitelist, then the
advisories (which accumulate), then the catch-all, which fires only if nothing more specific did.
"""
from __future__ import annotations

from . import rules
from .grep import grep
from .verdict import Advice, Verdict

HOOK = "protect-files"

NO_PATH_LOG = "no file_path in payload -- hook could not evaluate its rules"
NO_PATH_DENY = rules.RULES["W-INERT-NOPATH"]["template"]

DENY_PLUGIN = rules.RULES["W-PLUGIN"]["template"]

ADV_SKYRIM_CONFIG = rules.RULES["W-INI"]["template"]
ADV_SKSE_CONFIG = rules.RULES["W-SKSE-INI"]["template"]
ADV_LOADORDER = rules.RULES["W-LOADORDER"]["template"]
ADV_PAPYRUS = rules.RULES["W-PAPYRUS"]["template"]
ADV_INSTALL = rules.RULES["W-INSTALL"]["template"]


def decide(file_path: str, audit) -> Verdict:
    if file_path == "":
        audit.rule(HOOK, "W-INERT-NOPATH", "deny", NO_PATH_LOG, level="REFUSE")
        return Verdict("deny", NO_PATH_DENY)

    if grep(r"\.(esp|esm|esl|bsa|ba2)[\s.]*$", file_path):
        audit.rule(HOOK, "W-PLUGIN", "deny", DENY_PLUGIN)
        return Verdict("deny", DENY_PLUGIN)

    if grep(r"(^|(?<![/\\])[/\\]+)\.claude[/\\]+(hooks|plans|backups|memory|projects)[/\\]+", file_path):
        return Verdict()
    if grep(r"(?<![/\\])[/\\]+node_modules[/\\]+", file_path):
        return Verdict()

    advice = Advice()

    def advise(rule_id: str) -> None:
        text = rules.legacy_message(rule_id, p=file_path)
        audit.rule(HOOK, rule_id, "advise", text)
        advice.add(text)

    if grep(r"(Skyrim\.ini|SkyrimVR\.ini|SkyrimPrefs\.ini|SkyrimCustom\.ini)$", file_path):
        advise("W-INI")
    if grep(r"(?<![/\\])[/\\]+Data[/\\]+SKSE[/\\]+Plugins[/\\]+.*\.ini$", file_path):
        advise("W-SKSE-INI")
    if grep(r"(loadorder\.txt|plugins\.txt)$", file_path):
        advise("W-LOADORDER")
    if grep(r"\.(pex|psc)$", file_path):
        advise("W-PAPYRUS")

    if not advice.items and grep(r"(^|(?<![/\\])[/\\]+)Data[/\\]+|My Games[/\\]+Skyrim", file_path):
        advise("W-INSTALL")

    return Verdict("allow", "", advice.joined())
