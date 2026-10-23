"""Reproduce an explicit native-policy proposal from independently retained bytes."""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import re

from modding_harness import upgrade
from . import local_policy

WRAPPER = ".claude/hooks/save-access-entry.ps1"
CONTROLLER = ".claude/scripts/guarded-runtime-load.py"
RESTORE = ".claude/hooks/restore-save-backup.py"
POLICY_FILES = (local_policy.LEGACY, WRAPPER, CONTROLLER, RESTORE)
WRAPPER_NORMALIZED_SHA256 = "18c2325a4800b3f1704b5129f370498770aebce710432c6056fdf69482767da9"
RESTORE_SHA256 = "6118fe35ba773cd520727b59821d93ce3fb9746c7f0e6a3295a1921ff4fdc9e6"


def _handler(script, status):
    return {"type": "command", "command": 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/' + script + '"',
            "timeout": 60, "statusMessage": status}


def _claude_pre():
    return [
        {"matcher": "Bash|PowerShell|Monitor", "hooks": [
            _handler("protect-bash.sh", "Checking command safety..."),
            _handler("snapshot-before-tool.sh", "Snapshotting scripts...")]},
        {"matcher": "Edit|Write|NotebookEdit", "hooks": [
            _handler("protect-files.sh", "Checking file protection..."),
            _handler("backup-before-edit.sh", "Backing up file...")]},
        {"matcher": "mcp__.*[Dd][Ee][Vv][-_. ]?[Bb][Ee][Nn][Cc][Hh].*", "hooks": [
            _handler("protect-mcp.sh", "Checking DevBench call...")]},
    ]


def _codex_pre(root):
    from . import codex_definitions
    win = codex_definitions.normalise_win(root)
    posix = "/" + win[0].lower() + win[2:]
    if codex_definitions.root_problem(win):
        raise ValueError("ordered native policy needs a qualified hook root")
    ps = ' -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + win + '/.codex/hooks/skyguard-entry.ps1" pre-tool'
    return [{"matcher": ".*", "hooks": [{"type": "command",
        "command": 'bash "' + posix + '/.codex/hooks/skyguard-entry.sh" pre-tool',
        "commandWindows": "pwsh" + ps + " || powershell" + ps,
        "timeout": 60, "statusMessage": "Skyrim toolkit: checking"}]}]


def _personal(root, codex=False):
    command = 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + root + '/' + WRAPPER + '"'
    handler = {"type": "command", "command": command, "timeout": 30,
               "statusMessage": "Save guard: backing up saves before changes"}
    if codex:
        handler["commandWindows"] = command
    return {"matcher": ".*", "hooks": [handler]}


def _legacy_claude():
    current = _claude_pre()
    published = copy.deepcopy(current[:2])
    published[0]["matcher"], published[1]["matcher"] = "Bash", "Edit|Write"
    installed = {"matcher": "Bash|PowerShell|Monitor", "hooks": [
        _handler("snapshot-before-tool.sh", "Snapshotting source files..."),
        _handler("protect-bash.sh", "Checking command safety...")]}
    return [*published, installed]


def wrapper_identity(raw):
    if len(raw) > 65536:
        raise ValueError("personal wrapper exceeds its byte budget")
    text = raw.decode("utf-8-sig").replace("\r\n", "\n")
    pattern = r"(?m)^    \$start\.FileName = '([^'\r\n]+)'$"
    matches = re.findall(pattern, text)
    if len(matches) != 1 or not Path(matches[0]).is_absolute() or Path(matches[0]).name.casefold() != "python.exe":
        raise ValueError("personal wrapper needs its single literal Python interpreter")
    normalized = re.sub(pattern, "    $start.FileName = '<PYTHON>'", text)
    if hashlib.sha256(normalized.encode("utf-8")).hexdigest() != WRAPPER_NORMALIZED_SHA256:
        raise ValueError("personal wrapper differs from the reviewed version")


def _required_runtime():
    # The guard inventory enumerates shipped runtime, including modules loaded
    # dynamically. Do not infer completeness from a deliberately partial review.
    root = Path(__file__).resolve().parents[2]
    with (root / "GUARD-FILES.sha256").open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("guard inventory exceeds its byte budget")
    names = set()
    for line in raw.decode("utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        digest, name = line.split("  ", 1)
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None or name in names:
            raise ValueError("invalid complete guard inventory")
        names.add(name)
    selected = {name for name in names if name.startswith(("tools/skyguard/", "tools/modding_harness/",
                ".claude/hooks/", ".codex/hooks/", ".opencode/hooks/", ".opencode/plugins/"))}
    selected.update(("GUARD-FILES.sha256", "tools/agent-harness.py", "tools/skyguard/ordered_upgrade.py"))
    if "tools/skyguard/local_policy.py" not in selected or "tools/modding_harness/dispatch.py" not in selected:
        raise ValueError("guard inventory omits the ordered runtime")
    return selected


def proposal(bundle, review):
    """Settings preview only; application still requires every explicit choice.

    Complete group semantics and private sources are qualified independently.
    Neither this function nor application establishes native host trust.
    """
    bundle = Path(bundle)
    rollback, candidate = review["rollback_files"], review["candidate_files"]
    if local_policy.LEGACY not in rollback:
        return None
    if os.name != "nt":
        raise ValueError("this reviewed Windows personal policy needs Windows qualification")
    if rollback[local_policy.LEGACY] not in local_policy.QUALIFIED_POLICY_SHA256:
        raise ValueError("personal policy source has not been qualified")
    if WRAPPER not in rollback:
        raise ValueError("personal wrapper must be independently retained")
    with upgrade._entry(bundle / "rollback", WRAPPER).open("rb") as stream:
        wrapper_identity(stream.read(65537))
    sources = {name: rollback[name] for name in POLICY_FILES if name in rollback}
    for name, expected in ((CONTROLLER, local_policy.CONTROLLED_LOAD_SHA256), (RESTORE, RESTORE_SHA256)):
        if name in sources and sources[name] != expected:
            raise ValueError("retained personal helper has not been qualified: " + name)
    if any(review["observed_local_files"].get(name) != digest for name, digest in sources.items()):
        raise ValueError("personal sources were not captured as local policy")
    required = _required_runtime()
    if not required <= candidate.keys():
        raise ValueError("ordered policy needs the complete candidate guard runtime")
    root = Path(review["plan"]["root"]).as_posix()
    expected = {".claude/settings.json": _claude_pre(), ".codex/hooks.json": _codex_pre(root)}
    files = {}
    for name, wanted in expected.items():
        if name not in candidate:
            raise ValueError("ordered policy needs both native candidate definitions")
        value = upgrade.read_settings(bundle / "candidate", name)
        if upgrade.json_bytes(value.get("hooks", {}).get("PreToolUse")) != upgrade.json_bytes(wanted):
            raise ValueError("candidate native pre-groups differ from the qualified definitions")
        merged = upgrade.read_settings(bundle, upgrade.PROJECT_SETTINGS[name])
        allowed = [*wanted, _personal(root, name.startswith(".codex"))]
        if name.startswith(".claude"):
            allowed += _legacy_claude()
        keys = {upgrade.json_bytes(group) for group in allowed}
        if any(upgrade.json_bytes(group) not in keys for group in merged.get("hooks", {}).get("PreToolUse", [])):
            raise ValueError("unqualified complete native pre-group: " + name)
        merged.setdefault("hooks", {})["PreToolUse"] = copy.deepcopy(wanted)
        files[name] = merged
    local = upgrade.read_settings(bundle / "rollback", ".claude/settings.local.json")
    groups = local.get("hooks", {}).get("PreToolUse", [])
    if any(upgrade.json_bytes(group) != upgrade.json_bytes(_personal(root)) for group in groups):
        raise ValueError("unqualified complete local pre-group")
    if groups:
        local["hooks"]["PreToolUse"] = []
        files[".claude/settings.local.json"] = local
    registration = {"v": 1, "kind": local_policy.KIND, "path": local_policy.LEGACY,
                    "sha256": rollback[local_policy.LEGACY]}
    existing = upgrade.read_settings(bundle / "rollback", local_policy.CONFIG)
    if review["observed_local_files"].get(local_policy.CONFIG) is not None and upgrade.json_bytes(existing) != upgrade.json_bytes(registration):
        raise ValueError("existing personal-policy registration requires separate review")
    files[local_policy.CONFIG] = registration
    return {"v": 1, "kind": "skyrim-reviewed-local-save-policy-v1", "files": files,
            "sources": sources, "requires": {name: candidate[name] for name in sorted(required)}}
