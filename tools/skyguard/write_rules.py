"""The write guard: protect-files.sh's rules (Edit/Write).

Order is the bash file's order: the plugin-file deny comes first, then the silent whitelist, then the
advisories (which accumulate), then the catch-all, which fires only if nothing more specific did.

Plan 5 Task 9: the two notes that are about WHERE a file is decide on where it RESOLVES (targets, payload cwd),
not on words in its path:
  - W-PAPYRUS: a .psc/.pex under the install's Data/Scripts, or in MO2 mods/overwrite (a .psc in a toolkit checkout
    or a temp copy is not the game's script);
  - W-INSTALL (the catch-all): a file in the install's Data/ folder, the config folder or MO2 -- the area the shell
    notes use (writes.in_install_area). A relative path with no usable cwd is unresolved: no note (the Write tool
    sends absolute paths).
Without `roots` (an old caller) both fall back to the legacy text tests.
"""
from __future__ import annotations

import os

from . import rules, targets
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

LEGACY_INSTALL_TEXT = r"(^|(?<![/\\])[/\\]+)Data[/\\]+|My Games[/\\]+Skyrim"


def papyrus_zone(abs_path: str, roots) -> bool:
    """A script path the game (or MO2) loads from: under the install's Data/Scripts, or in MO2 mods/overwrite."""
    from .writes import _rel
    z = targets.zone(abs_path, roots)
    if z == "mo2":
        return True
    rel = _rel(abs_path, roots.install_dirs) if z == "install" else None
    return rel is not None and rel[:2] == ["data", "scripts"]


def decide(file_path: str, audit, roots=None, cwd: str = "", env=None) -> Verdict:
    if file_path == "":
        audit.rule(HOOK, "W-INERT-NOPATH", "deny", NO_PATH_LOG, level="REFUSE")
        return Verdict("deny", NO_PATH_DENY)

    if grep(targets.PLUGIN_FILE, file_path):
        audit.rule(HOOK, "W-PLUGIN", "deny", DENY_PLUGIN)
        return Verdict("deny", DENY_PLUGIN)

    if grep(r"(^|(?<![/\\])[/\\]+)\.claude[/\\]+(hooks|plans|backups|memory|projects)[/\\]+", file_path):
        return Verdict()
    if grep(r"(?<![/\\])[/\\]+node_modules[/\\]+", file_path):
        return Verdict()

    advice = Advice()

    def advise(rule_id: str) -> None:
        text = rules.message(rule_id, file=file_path)
        audit.rule(HOOK, rule_id, "advise", text)
        advice.add(text)

    if roots is not None:
        from .writes import in_install_area, is_save
        a = targets.resolve(file_path, cwd, "bash", os.environ if env is None else env)
        if is_save(a, roots):
            # rt-fix-b: a save rewritten through Edit/Write (or a Codex patch) is a binary file replaced by text.
            text = rules.message("W-SAVE", file=file_path)
            audit.rule(HOOK, "W-SAVE", "ask", text)
            return Verdict("ask", text)
        papyrus = a is not None and papyrus_zone(a, roots)
        install = a is not None and in_install_area(a, roots)
    else:
        papyrus, install = True, grep(LEGACY_INSTALL_TEXT, file_path)

    if grep(targets.SKYRIM_INI, file_path):
        advise("W-INI")
    if grep(targets.SKSE_INI, file_path):
        advise("W-SKSE-INI")
    if grep(targets.LOADORDER, file_path):
        advise("W-LOADORDER")
    if papyrus and grep(r"\.(pex|psc)$", file_path):
        advise("W-PAPYRUS")

    if not advice.items and install:
        advise("W-INSTALL")

    return Verdict("allow", "", advice.joined())
