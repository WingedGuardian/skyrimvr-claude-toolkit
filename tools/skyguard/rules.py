"""The rule registry: every guard rule's id, decision and message template, in one place.

Task 1 of Plan 5 moved the existing texts here unchanged and marked them legacy. Later tasks rewrite them
one by one (dropping the legacy flag). Plan 4 reads this registry to emit Codex rules from the
'prefixes' lists. Stdlib only, Python 3.8.
"""
from __future__ import annotations

import re

# Fields a non-legacy template may use. A file is always rendered as its basename.
FIELDS = frozenset({"file", "op", "save", "n"})

# S-PLUGIN-REF and S-LOADORDER-REF are placeholder ids for the legacy advice that later Plan 5 tasks remove or
# replace (R11); they are not part of the final rule set. S-DEL-ADVISE was removed in Task 7 (R10).
# Ids that existed and were deleted (their frozen legacy text stays in tests/guard_corpus/legacy_rules.json so the
# frozen corpus still maps). Never reused for a new rule.
REMOVED = frozenset({"S-DEL-ADVISE"})     # R10, Task 7: 0 hits in replay; any delete into the install denies

RULES = {
    'S-INERT-NOCMD': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": True,
        "prefixes": [],
        "template": (
        'GUARD INERT: protect-bash.sh received no command and therefore checked '
        'NOTHING. Allowing silently would be indistinguishable from deciding '
        'this is fine. Investigate the hook payload before retrying.'
    ),
    },
    'S-INERT-NOROOT': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": True,
        "prefixes": [],
        "template": (
        'GUARD INERT: protect-bash.sh could not work out which directory is the '
        'Skyrim install, so its delete rules checked NOTHING. Allowing silently '
        'would be indistinguishable from having no guard at all. Re-run '
        'setup.sh from your Skyrim folder.'
    ),
    },
    'S-INERT-CONFIG': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'GUARD INERT: {file} has an unreadable value for {op}, so a protected folder is unknown.\n'
        'Re-run setup.sh to rewrite it.'
    ),
    },
    'S-DEL-CONFIG': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Blocked: this deletes {file} in the Skyrim settings folder.\n'
        'Your INIs and controlmap there cannot be restored by a mod manager.'
    ),
    },
    'S-DEL-INSTALL': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Blocked: this deletes {file} inside the Skyrim install.\n'
        'A mod manager cannot restore your own files there; if you mean it, run it yourself.'
    ),
    },
    'S-DEL-MO2': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        "Blocked: this deletes {file} inside Mod Organizer's mods or overwrite folder.\n"
        'If you mean it, remove the mod in MO2 or run it yourself.'
    ),
    },
    'S-DEL-UNRESOLVED': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Blocked: this deletes a path the guard cannot work out, in a command that names a protected Skyrim folder.\n'
        'Write the full path, or run it yourself.'
    ),
    },
    'S-DEL-FALLBACK': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Blocked: the guard could not read this command, and it contains a deletion and a protected Skyrim folder.\n'
        'Split it into simpler commands, or run it yourself.'
    ),
    },
    'S-DEL-UNRESOLVED-CWD': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Note: this deletes a path the guard cannot work out, from inside a protected Skyrim folder; use a full path.'
    ),
    },
    'S-REGISTRY': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": True,
        "prefixes": [],
        "template": (
        'BLOCKED: Cannot delete Bethesda registry keys.'
    ),
    },
    'S-PSC-OUT': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": True,
        "prefixes": [],
        "template": (
        'BLOCKED: Tool output cannot target a .psc file directly. Use a '
        'separate output directory.'
    ),
    },
    'S-CHAMPOLLION': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": True,
        "prefixes": [],
        "template": (
        'BLOCKED: Champollion against a PEX in Data/Scripts/ has destroyed a '
        '.psc twice. Copy the .pex to a temp directory first, then run '
        'Champollion there.'
    ),
    },
    'S-RELATIVE-UNKNOWN': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Note: this deletes {file} by a relative path, and the guard was not told the working folder.'
    ),
    },
    'S-MOVE-INSTALL': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": True,
        "prefixes": [],
        "template": (
        'Moving/copying inside the live game install: {c}. A stray overwrite '
        'here is silent -- confirm the destination before relying on it.'
    ),
    },
    'S-REDIRECT-INSTALL': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": True,
        "prefixes": [],
        "template": (
        'Redirecting output into the game/config directory: {c}. A redirect '
        'TRUNCATES its target before anything is written.'
    ),
    },
    'S-SED-WINPATH': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": True,
        "prefixes": [],
        "template": (
        "In-place sed edit in the game directory: {c}. A Windows path in sed's "
        'REPLACEMENT is destroyed by escape handling -- normalise the path '
        'first.'
    ),
    },
    'S-PLUGIN-REF': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": True,
        "prefixes": [],
        "template": (
        'Command references plugin/archive files: {c}. Reading one is fine; '
        'writing one directly corrupts it -- use xelib, Spriggit or AutoMod.'
    ),
    },
    'S-LOADORDER-REF': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": True,
        "prefixes": [],
        "template": (
        'Command references load order: {c}. Your mod manager owns these files, '
        'and the Special Edition copy under AppData is a DIFFERENT file from '
        'the VR one and is routinely stale.'
    ),
    },
    'S-AUTOMOD-ESP': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": True,
        "prefixes": [],
        "template": (
        'AutoMod ESP write WITHOUT --dry-run: {c}. The standing rule is dry-run '
        'first, review, then write.'
    ),
    },
    'S-AUTOMOD-NIF': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": True,
        "prefixes": [],
        "template": (
        'AutoMod NIF write: {c}. Cross-read the result with an independent '
        'parser before handing it to the engine -- same-tool readback misses '
        'malformed files.'
    ),
    },
    'S-AUTOMOD-ARCHIVE': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": True,
        "prefixes": [],
        "template": (
        'AutoMod archive write: {c}. Loose files always override a BSA, so '
        'check for loose copies before assuming this took effect.'
    ),
    },
    'S-RESAVER-APPLY': {
        "decision": 'ask',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'ReSaver will MODIFY a save file ({op} on {save}). '
        'Approve only if you asked for this save to be changed.'
    ),
    },
    'S-EXTERNAL-OUT': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": True,
        "prefixes": [],
        "template": (
        'External Papyrus/ESP tool writing into the game/data directory: {c}. '
        'Verify the output landed where you intended -- several of these have '
        'destructive defaults.'
    ),
    },
    'W-INERT-NOPATH': {
        "decision": 'deny',
        "hook": 'protect-files',
        "legacy": True,
        "prefixes": [],
        "template": (
        'GUARD INERT: protect-files.sh received no file_path and therefore '
        'checked NOTHING. Allowing silently would be indistinguishable from '
        'deciding this is fine. Investigate the hook payload before retrying.'
    ),
    },
    'W-PLUGIN': {
        "decision": 'deny',
        "hook": 'protect-files',
        "legacy": True,
        "prefixes": [],
        "template": (
        'BLOCKED: Cannot directly write to plugin/archive files. Use xelib, '
        'Spriggit or AutoMod.'
    ),
    },
    'W-INI': {
        "decision": 'advise',
        "hook": 'protect-files',
        "legacy": True,
        "prefixes": [],
        "template": (
        'SKYRIM CONFIG: {p}. Load order is Skyrim.ini then SkyrimVR.ini then '
        'SkyrimPrefs.ini (last wins), so a setting here can be overridden by a '
        'later file. Diff before and after rather than trusting the edit.'
    ),
    },
    'W-SKSE-INI': {
        "decision": 'advise',
        "hook": 'protect-files',
        "legacy": True,
        "prefixes": [],
        "template": (
        'SKSE PLUGIN CONFIG: {p}. Not captured by any snapshot hook -- copy it '
        'yourself before changing it.'
    ),
    },
    'W-LOADORDER': {
        "decision": 'advise',
        "hook": 'protect-files',
        "legacy": True,
        "prefixes": [],
        "template": (
        'LOAD ORDER: {p}. Your mod manager owns these and may overwrite a '
        'direct edit on its next deploy, so make sure a direct edit is really '
        'what is wanted. Note the Special Edition copy under AppData is a '
        'DIFFERENT file from the VR one and is routinely stale.'
    ),
    },
    'W-PAPYRUS': {
        "decision": 'advise',
        "hook": 'protect-files',
        "legacy": True,
        "prefixes": [],
        "template": (
        'PAPYRUS SCRIPT: {p}. A .pex loads at game startup ONLY, so a running '
        'session will not see this change. Snapshot to .claude/backups/<name>/ '
        'before experimenting.'
    ),
    },
    'W-INSTALL': {
        "decision": 'advise',
        "hook": 'protect-files',
        "legacy": True,
        "prefixes": [],
        "template": (
        'Editing inside the live game/config install: {p}'
    ),
    },
}


def _basename(value) -> str:
    return re.split(r"[/\\]+", str(value).rstrip("/\\"))[-1]


def message(rule_id: str, **fields) -> str:
    """Render a NON-legacy template. Only FIELDS are accepted; a file is reduced to its basename."""
    rule = RULES[rule_id]
    if rule["legacy"]:
        raise ValueError(rule_id + " is a legacy rule; use legacy_message")
    bad = set(fields) - FIELDS
    if bad:
        raise ValueError("unsupported message fields: " + ", ".join(sorted(bad)))
    if "file" in fields:
        fields["file"] = _basename(fields["file"])
    return rule["template"].format(**fields)


def legacy_message(rule_id: str, **fields) -> str:
    """Render a legacy template, which still echoes the command ({c}) or the path ({p})."""
    rule = RULES[rule_id]
    if not rule["legacy"]:
        raise ValueError(rule_id + " is not a legacy rule; use message")
    return rule["template"].format(**fields)
