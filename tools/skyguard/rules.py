"""The rule registry: every guard rule's id, decision and message template, in one place.

Task 1 of Plan 5 moved the existing texts here unchanged and marked them legacy. Later tasks rewrite them
one by one (dropping the legacy flag). Plan 4 reads this registry to emit Codex rules from the
'prefixes' lists. Stdlib only, Python 3.8.
"""
from __future__ import annotations

import re

# Fields a non-legacy template may use. A file is always rendered as its basename.
FIELDS = frozenset({"file", "op", "save", "n"})

# Ids that existed and were deleted (their frozen legacy text stays in tests/guard_corpus/legacy_rules.json so the
# frozen corpus still maps). Never reused for a new rule.
REMOVED = frozenset({
    "S-DEL-ADVISE",         # R10, Task 7: 0 hits in replay; any delete into the install denies
    "S-PLUGIN-REF",         # R11, Task 8: a text match on plugin extensions, 3.3% of calls, nearly all reads
    "S-LOADORDER-REF",      # Task 8: fired on READS of plugins.txt; a write is S-WRITE-LOADORDER
    "S-REDIRECT-INSTALL",   # Task 8: replaced by the S-WRITE-* rules, which fire on the program that writes
    "S-MOVE-INSTALL",       # Task 9 fix round 1: 107 of 107 hits in history were the user's deliberate deploys
})

# DevBench console commands that are refused (Task 11, R14). DATA, compared on whitespace-split lower-case words, so the
# entry is the WHOLE command: `smp reset` is listed, `smp reset <anything>` is not. The only command MEASURED to crash
# this install (a full reset of a 700+ plugin order); add a command here only once it has been measured.
DEVBENCH_DENY = (("smp", "reset"),)

RULES = {
    'S-INERT-NOCMD': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'GUARD INERT: protect-bash.sh received no command, so it checked nothing.\n'
        'Investigate the hook payload before retrying.'
    ),
    },
    'S-INERT-NOROOT': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'GUARD INERT: protect-bash.sh cannot tell which folder is the Skyrim install, so its delete rules checked nothing.\n'
        'Re-run setup.sh from your Skyrim folder.'
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
    'S-DEL-UNKNOWN-DIR': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Blocked: this deletes everything in a folder the guard cannot work out.\n'
        'Write the full path, or run it yourself.'
    ),
    },
    'S-SHELL-UNREAD': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Blocked: decoded text is run as shell commands, so the guard cannot read what it does.\n'
        'Decode it to a file, read it, then run that file; or run it yourself.'
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
    'S-REGISTRY': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Blocked: this deletes Bethesda registry keys, which the game and tools use to find the install.'
    ),
    },
    'S-PSC-OUT': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Blocked: this tool would write its output straight over {file}.\n'
        'Send output to a separate folder and copy back what you want.'
    ),
    },
    'S-CHAMPOLLION': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Blocked: Champollion on {file} inside Data/Scripts has destroyed .psc sources before.\n'
        'Copy the .pex to a temp folder and run it there.'
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
    'S-WRITE-PLUGIN': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Blocked: this writes {file} as raw text, which corrupts a plugin or archive.\n'
        'Use xelib, Spriggit or AutoMod to change it.'
    ),
    },
    'S-COPY-PLUGIN': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Note: this copies onto {file} where the game loads it; a copy already there is replaced, not kept.'
    ),
    },
    'S-WRITE-INSTALL': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Note: this writes {file} in the Skyrim install; a > redirect empties it first.'
    ),
    },
    'S-WRITE-LOADORDER': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Note: this writes {file}; your mod manager owns it and may undo the change.'
    ),
    },
    'S-WRITE-INI': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Note: this writes {file}; a later INI in the load chain can override it.'
    ),
    },
    'S-WRITE-SKSE-INI': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Note: this writes {file}; no snapshot covers SKSE configs, so copy it first.'
    ),
    },
    'S-SED-WINPATH': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        "Note: sed's replacement contains a backslash path, which sed mangles; use forward slashes."
    ),
    },
    'S-AUTOMOD-ESP': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Note: AutoMod is writing {file} without --dry-run; the rule is dry-run first.'
    ),
    },
    'S-AUTOMOD-NIF': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Note: cross-read {file} with an independent parser before testing it in game.'
    ),
    },
    'S-AUTOMOD-ARCHIVE': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Note: loose files override {file}; check for loose copies before trusting it.'
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
        "legacy": False,
        "prefixes": [],
        "template": (
        'Note: {op} writes its output into the Skyrim install; check it landed where you meant.'
    ),
    },
    # routes red team (rt-fix-b): a shell write, copy or move ONTO a save in the settings folder replaces it; the old
    # save is not kept (as S-DEVBENCH-SAVE and S-RESAVER-APPLY, a change to a save asks).
    'S-WRITE-SAVE': {
        "decision": 'ask',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'This replaces the save {file}; the old save is not kept.\n'
        'Approve only if you asked for this save to be changed.'
    ),
    },
    # routes red team (rt-fix-b): moving or renaming the install, its Data folder, the settings folder, its Saves
    # folder or MO2's folders takes them away from where the game and the tools look for them.
    'S-MOVE-ROOT': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Blocked: this moves {file} away from where the game and its tools look for it.\n'
        'If you mean it, move it yourself.'
    ),
    },
    # routes red team (rt-fix-b): git commands that overwrite the working tree in a protected folder throw away
    # uncommitted changes there, which no backup holds.
    'S-GIT-DISCARD': {
        "decision": 'ask',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'This git command throws away uncommitted changes in {file}; no backup keeps them.\n'
        'Approve only if you asked for those changes to be discarded.'
    ),
    },
    'S-DEVBENCH-CRASH': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        "Blocked: '{op}' crashes a large load order in the live game.\n"
        'Use a targeted command instead, or run it yourself.'
    ),
    },
    'S-DEVBENCH-SAVE': {
        "decision": 'ask',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        '{op}\n'
        'Yes goes ahead; an overwritten save is not kept.'
    ),
    },
    'S-INERT-NOINPUT': {
        "decision": 'deny',
        "hook": 'protect-mcp',
        "legacy": False,
        "prefixes": [],
        "template": (
        'GUARD INERT: the DevBench guard received no tool call, so it checked nothing.\n'
        'Investigate the hook payload before retrying.'
    ),
    },
    'W-INERT-NOPATH': {
        "decision": 'deny',
        "hook": 'protect-files',
        "legacy": False,
        "prefixes": [],
        "template": (
        'GUARD INERT: protect-files.sh received no file path, so it checked nothing.\n'
        'Investigate the hook payload before retrying.'
    ),
    },
    'W-PLUGIN': {
        "decision": 'deny',
        "hook": 'protect-files',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Blocked: plugin and archive files (.esp .esm .esl .bsa .ba2) cannot be written directly.\n'
        'Use xelib, Spriggit or AutoMod to change them instead.'
    ),
    },
    # routes red team (rt-fix-b): an Edit/Write (or apply_patch) onto a save in the settings folder rewrites a binary
    # save as text; the old save is not kept.
    'W-SAVE': {
        "decision": 'ask',
        "hook": 'protect-files',
        "legacy": False,
        "prefixes": [],
        "template": (
        'This edit replaces the save {file}; the old save is not kept.\n'
        'Approve only if you asked for this save to be changed.'
    ),
    },
    'W-INI': {
        "decision": 'advise',
        "hook": 'protect-files',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Note: a later INI in the load chain can override {file}; diff before and after.'
    ),
    },
    'W-SKSE-INI': {
        "decision": 'advise',
        "hook": 'protect-files',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Note: no snapshot covers {file}; copy it before changing it.'
    ),
    },
    'W-LOADORDER': {
        "decision": 'advise',
        "hook": 'protect-files',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Note: your mod manager owns {file} and may undo a direct edit.'
    ),
    },
    'W-PAPYRUS': {
        "decision": 'advise',
        "hook": 'protect-files',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Note: a script change in {file} reaches the game only at its next start; snapshot it first.'
    ),
    },
    'W-INSTALL': {
        "decision": 'advise',
        "hook": 'protect-files',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Note: editing {file} inside the live Skyrim install.'
    ),
    },
}


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
FIELD_MAX = 96              # characters a rendered field may take, at most
LINE_MAX = 200              # the message contract (tests/test_rule_messages.py)


def _basename(value) -> str:
    """The file name a message shows. RT-A: an NTFS stream suffix is cut (`a.esp::$DATA` names a.esp); a bare drive
    (`C:`) is kept."""
    b = re.split(r"[/\\]+", str(value).rstrip("/\\"))[-1]
    m = re.match(r"([^:]{2,}):\S*(.*)\Z", b, re.S)          # `a.esp::$DATA and 1 more` -> `a.esp and 1 more`
    return m.group(1) + m.group(2) if m else b


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
    # Final review B-M1: the guard judges command TEXT, so a field can hold a newline (`> "a<LF>b.esp"`) or be any
    # length; either broke the two-line, 200-character message contract. Control characters become spaces and the
    # fields are cut, shorter while a line is still too long (two fields can share one line).
    clean = {k: _CONTROL.sub(" ", str(v)) for k, v in fields.items()}
    cap = FIELD_MAX
    while True:
        out = rule["template"].format(**{k: v if len(v) <= cap else v[:cap - 3] + "..." for k, v in clean.items()})
        if cap <= 16 or all(len(ln) <= LINE_MAX for ln in out.split("\n")):
            return out
        cap -= 8


def legacy_message(rule_id: str, **fields) -> str:
    """Render a legacy template, which still echoes the command ({c}) or the path ({p})."""
    rule = RULES[rule_id]
    if not rule["legacy"]:
        raise ValueError(rule_id + " is not a legacy rule; use message")
    return rule["template"].format(**fields)
