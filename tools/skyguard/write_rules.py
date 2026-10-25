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

from . import rules, self_rules, targets
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


def decide(file_path: str, audit, roots=None, cwd: str = "", env=None, op: str = "write",
           transcript: str = "") -> Verdict:
    """op: what the call does to the file, for the save policy (saves.py): `write` (an edit, a write, a patch that
    adds or updates it), `delete` or `move` (a patch that deletes it or moves it away)."""
    if file_path == "":
        audit.rule(HOOK, "W-INERT-NOPATH", "deny", NO_PATH_LOG, level="REFUSE")
        return Verdict("deny", NO_PATH_DENY)

    if grep(targets.PLUGIN_FILE, file_path):
        audit.rule(HOOK, "W-PLUGIN", "deny", DENY_PLUGIN)
        return Verdict("deny", DENY_PLUGIN)

    # Plan 6 Task 12 (R12): the guards' state (deny) and their code and config (priced), decided on where the file
    # RESOLVES -- before the silent whitelist below, which used to cover .claude/hooks and .claude/backups. With no
    # roots (an old caller, an unreadable paths file on Codex) the path's own words decide (self_rules.text_kind).
    env_ = os.environ if env is None else env
    placed = targets.resolve_literal(file_path, cwd, env_) if roots is not None else None
    if placed and self_rules.save_store_target(placed, roots, holder=op in ("delete", "move")):
        reason = rules.message("W-SAVE-STORE", file=file_path)
        audit.rule(HOOK, "W-SAVE-STORE", "deny", reason)
        return Verdict("deny", reason)
    # User ruling 2026-10-20: the agent's own session transcripts (save approvals are read there) are the host's alone.
    spoken = placed if roots is not None else targets.resolve_literal(file_path, cwd, env_)
    if spoken and self_rules.transcript_target(spoken, env_, transcript, holder=op in ("delete", "move")):
        reason = rules.message("W-SELF-TRANSCRIPT", file=file_path)
        audit.rule(HOOK, "W-SELF-TRANSCRIPT", "deny", reason)
        return Verdict("deny", reason)
    if roots is not None:
        a = self_rules.resolve_own(lambda x: targets.resolve_literal(x, cwd, env_), file_path)   # T8 r1 P1: streams
        # An unresolved path (relative, no cwd) fails closed for the STATE class only, as on the shell.
        own = self_rules.kind_of(a, roots, env_, holder=op in ("delete", "move")) if a is not None else (
            self_rules.STATE if self_rules.text_kind(file_path) == self_rules.STATE else "")
    else:
        own = self_rules.text_kind(file_path)
    if own == self_rules.STATE:
        reason = rules.message("W-SELF-STATE", file=file_path)
        audit.rule(HOOK, "W-SELF-STATE", "deny", reason)
        return Verdict("deny", reason)
    if own == self_rules.GUARD and rules.RULES["W-SELF-GUARD"]["decision"] == "ask":
        reason = rules.message("W-SELF-GUARD", file=file_path)
        audit.rule(HOOK, "W-SELF-GUARD", "ask", reason)
        return Verdict("ask", reason)
    if own == self_rules.GUARD:
        text = rules.message("W-SELF-GUARD", file=file_path)
        audit.rule(HOOK, "W-SELF-GUARD", "advise", text)
        return Verdict("allow", "", text)

    if grep(r"(^|(?<![/\\])[/\\]+)\.claude[/\\]+(hooks|plans|backups|memory|projects)[/\\]+", file_path):
        return Verdict()
    if grep(r"(?<![/\\])[/\\]+node_modules[/\\]+", file_path):
        return Verdict()

    advice = Advice()

    def advise(rule_id: str) -> None:
        text = rules.message(rule_id, file=file_path)       # rules._basename cuts a stream suffix (RT-A)
        audit.rule(HOOK, rule_id, "advise", text)
        advice.add(text)

    if roots is not None:
        from .writes import in_install_area, is_save
        a = targets.resolve_literal(file_path, cwd, os.environ if env is None else env)
        if is_save(a, roots):
            # The save policy (user ruling 2026-10-13): a save changed through Edit/Write or a patch is backed up
            # first (portable.check); saves.judge decides it with every other save the call changes.
            from . import saves
            actual = targets.physical(file_path, cwd, "cmd", env_, literal=True)
            return saves.judge(saves.Change((saves.Target(actual or "", op if op in saves.OPS else "write", True),)), Verdict(),
                               audit, HOOK)
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
    if papyrus and grep(r"\.(pex|psc)" + targets.STREAM + "$", file_path):
        advise("W-PAPYRUS")

    if not advice.items and install:
        advise("W-INSTALL")

    return Verdict("allow", "", advice.joined())
