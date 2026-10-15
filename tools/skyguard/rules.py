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
# (Not legacy ids, so not listed above: S-WRITE-SAVE and W-SAVE, the asks about a save write, went with the save
# policy, user ruling 2026-10-13 -- saves.py's S-SAVE-* rules decide every save change, by any route. Never reused.)

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
    # Batch-3 ruling (2026-10-13): a write, copy or move onto a FILE DIRECTLY IN the install root (the game's own exe and
    # dlls, openvr_api.dll, a loader .ini, an ENB/ReShade dll) was silent; v3 asked about any copy into the game folder.
    # An advisory, never an ask or a deny.
    'S-WRITE-ROOT': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Note: this writes {file} in the game folder, outside Data/; no mod manager tracks it, so copy it first.\n'
        "For a mod's own files, use tools/mod-install.py (backups and a manifest)."
    ),
    },
    # Plan 8 T6 (rt-fix-b concern 3): an archive writer (Compress-Archive, 7z, zip, tar -c) into the same area. It got
    # the S-WRITE-INSTALL note, whose redirect sentence is false for it.
    'S-WRITE-ARCHIVE': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Note: this writes the archive {file} in the Skyrim install.\n'
        'If that file exists, tar and -Force replace it; zip, 7z and -Update change what is inside it.'
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
    'S-SED-CRLF': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Blocked: sed -i without -b rewrites every CRLF line ending in {file} to LF on Windows.\n'
        'Use sed -i -b; with -b, a pattern ending in $ must also match the CR in front of it.'
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
    # The save policy (2026-10-13): ReSaver --apply on a save the guard can place is backed up and allowed (saves.py);
    # one it cannot place (a bare name with no cwd, a wildcard, no .ess at all) cannot be backed up, so it is refused.
    'S-RESAVER-APPLY': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Blocked: ReSaver {op} --apply writes {save}, which the guard cannot place to back it up first.\n'
        'Give the save as a full path.'
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
    # The save policy (user ruling 2026-10-13, saves.py): any call that changes saves -- a write, copy onto, edit,
    # move, rename, delete, DevBench save or ReSaver --apply -- by any route (shell, edit tools, patch, MCP).
    # 1 or 2 saves: each pair (.ess + .skse) is backed up and verified by the hook first, then the call is allowed.
    'S-SAVE-BACKUP': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Note: the guard backs up {file} before this runs ({op}).'
    ),
    },
    # More than 2, or a wildcard: the user's typed SAVE_ACCESS_APPROVAL line (approvals.py) is required.
    'S-SAVE-APPROVAL': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        "Blocked: this {op} {n} saves ({file}); more than 2, or a wildcard, needs the user's typed approval.\n"
        'If they agree, the user types this line: SAVE_ACCESS_APPROVAL {save}'
    ),
    },
    # A wildcard the guard cannot list (in a folder name, or an unreadable folder): no backup is possible.
    'S-SAVE-UNKNOWN': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Blocked: the guard cannot list the saves this {op} ({file}), so it cannot back them up first.\n'
        'Name each save, or put the wildcard in the file name only.'
    ),
    },
    # The backup itself failed (no space, unreadable, a copy that does not verify, the store busy): fail closed.
    'S-SAVE-BACKUP-FAILED': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Blocked: the backup of {file} failed ({op}), so the command did not run.\n'
        'The save is unchanged; fix the cause, then retry.'
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
    # The save policy (2026-10-13): a DevBench save over an EXISTING save is backed up and allowed (saves.py); one
    # whose save the guard cannot tell (no plain name, no Saves folder) cannot be backed up, so it is refused.
    'S-DEVBENCH-SAVE': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        '{op}\n'
        'The guard cannot back that save up first, so the call is refused; use a plain save name.'
    ),
    },
    # Plan 6 Task 11 (D6, R7): the lift rules (lift_rules.py). The CLI lifts are D6's: allowed, with a note. A
    # persistent off is the user's (ask; the Codex adapter makes it a deny). An unlock outside the CLI is refused.
    'S-LIFT-NOTE': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Note: this lifts {op} until the next session starts. Tell the user why, and restore it when done.'
    ),
    },
    'S-LIFT-RAW': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Blocked: this unlocks {file} outside skyguard, so the lift is not logged and never expires.\n'
        'Use: bash tools/skyguard.sh lock unlock {file}'
    ),
    },
    'S-LIFT-UNLOCKCMD': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Blocked: skyguard-unlock.cmd lifts the whole lock with no log and no expiry; it is for the user to run.\n'
        'Use: bash tools/skyguard.sh lock unlock <file>, or ask the user to run the .cmd themselves.'
    ),
    },
    'S-LIFT-PERMANENT': {
        "decision": 'ask',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'This turns the vanilla-file lock off for every future session, not only this one.\n'
        'Yes disables it until bash tools/skyguard.sh lock enable.'
    ),
    },
    # Plan 6 Task 15 fix round 1 (I3): `skyguard snapshot restore --in-place` (lift_rules.restore_in_place).
    'S-RESTORE-INPLACE': {
        "decision": 'ask',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        'This writes a snapshot or a pre-edit backup back over the live files (load order, INIs, saves, scripts).\n'
        'Yes restores them; each file it replaces is backed up first.'
    ),
    },
    # Plan 6 Task 12 (R12): self-protection (self_rules.py). The state is the CLI's alone (deny); the guard code and
    # config are priced (ask or advise). The W- twins say the same in different words: one text, one code.
    'S-SAVE-STORE': {
        "decision": "deny", "hook": "protect-bash", "legacy": False, "prefixes": [],
        "template": "Blocked: {file} is a permanent save backup store or its ancestor. Recovery data cannot be overwritten, deleted, moved or aliased by this call."
    },
    'W-SAVE-STORE': {
        "decision": "deny", "hook": "protect-files", "legacy": False, "prefixes": [],
        "template": "Blocked: this write targets {file}, a permanent save backup store or its ancestor. Recovery data cannot be overwritten, deleted, moved or aliased by this call."
    },
    'S-SELF-STATE': {
        "decision": 'deny',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        "Blocked: {file} holds the guards' on/off state and is written only by tools/skyguard.\n"
        'Use bash tools/skyguard.sh guards off, or lock unlock.'
    ),
    },
    # PRICED (pricing doc, Task 12 Ruling): advise. 24 newly asked over 70,342 items, and 24 of 24 were maintenance the
    # user asked for, against a pre-committed bar of <= 25 %. The ask text, if a later pricing flips it back:
    #   'This edits {file}, part of the safety guards themselves.' / 'Yes applies it; the next session start lists
    #   guard files that differ from the release.' (W-: 'Yes saves it; ...'). The session-start banner ships either way.
    'S-SELF-GUARD': {
        "decision": 'advise',
        "hook": 'protect-bash',
        "legacy": False,
        "prefixes": [],
        "template": (
        # op: self_rules EDITS/CREATES/LINKS (T8 fix round 2, N4). "this command" keeps the text apart from
        # W-SELF-GUARD's "Note: this changes {file}" -- a bare "this {op}" would match both (codes.matches).
        'Note: this command {op} {file}, part of the safety guards themselves.'
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
    'W-SELF-STATE': {
        "decision": 'deny',
        "hook": 'protect-files',
        "legacy": False,
        "prefixes": [],
        "template": (
        "Blocked: {file} holds the guards' on/off state and is written only by tools/skyguard.\n"
        'Use bash tools/skyguard.sh guards off, or lock unlock, not an edit.'
    ),
    },
    'W-SELF-GUARD': {
        "decision": 'advise',
        "hook": 'protect-files',
        "legacy": False,
        "prefixes": [],
        "template": (
        'Note: this changes {file}, part of the safety guards themselves.'
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
