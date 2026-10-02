"""The rule registry: every guard rule's id, decision and message template, in one place.

Task 1 of Plan 5 moved the existing texts here unchanged and marked them legacy. Later tasks rewrite them
one by one (dropping the legacy flag). Plan 4 reads this registry to emit Codex rules from the
'prefixes' lists. Stdlib only, Python 3.8.
"""
from __future__ import annotations

import re

# Fields a non-legacy template may use. A file is always rendered as its basename.
FIELDS = frozenset({"file", "op", "save", "n"})

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
    'S-DEL-CONFIG': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": True,
        "prefixes": [],
        "template": (
        'BLOCKED: this command would delete inside the Skyrim config directory '
        '(Documents/My Games/Skyrim). Your INIs and controlmap live there and '
        'are not recoverable from a mod manager.'
    ),
    },
    'S-DEL-INSTALL': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": True,
        "prefixes": [],
        "template": (
        'BLOCKED: this command names both a deletion and a path inside the '
        'Skyrim install. If you meant to delete something elsewhere, run it as '
        'a separate command that does not also mention the game directory.'
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
    'S-DEL-ADVISE': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": True,
        "prefixes": [],
        "template": (
        'Deleting files inside the live game install: {c}. Deleting the game '
        'ROOT is denied outright; this is a delete further in, which a mod '
        'manager can usually redeploy but your own mod files cannot be. Check '
        'the path is what you meant.'
    ),
    },
    'S-RELATIVE-UNKNOWN': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": True,
        "prefixes": [],
        "template": (
        'Deleting inside Data/ by a RELATIVE path: {c}. The hook is never told '
        "the caller's working directory, so it cannot tell whether this "
        'resolves into the live install -- check the path is what you meant.'
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
    'S-WRITE-INSTALL': {
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
        "legacy": True,
        "prefixes": [],
        "template": (
        'DESTRUCTIVE ReSaver save-write with --apply '
        '(reset-havok/cleanse-formlists/remove-created) MUTATES a .ess save and '
        'is never part of normal work. Explicit approval required: {c}'
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
    return re.split(r"[/\]+", str(value).rstrip("/\\"))[-1]


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
