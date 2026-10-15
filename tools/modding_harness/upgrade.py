"""Read-only ownership-aware upgrade previews; no guessed ownership or deletion.

Profiles supply the candidate inventory and any verified previous inventory.
Unknown or modified installed files remain user-owned collisions. A preview is
not authority to replace them and never infers host review/trust from metadata.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import re

from . import durable


def inventory(value):
    if not isinstance(value, dict) or type(value.get("v")) is not int or value["v"] != 1:
        raise ValueError("expected version 1 ownership inventory")
    files = value.get("files")
    if not isinstance(files, dict) or len(files) > 100000:
        raise ValueError("ownership inventory is missing or too large")
    result, folded = {}, set()
    for name, digest in files.items():
        if (not isinstance(name, str) or not name or "\\" in name or ":" in name or
                name.startswith("/") or str(PurePosixPath(name)) != name or
                any(part in ("", ".", "..") for part in name.split("/")) or
                name.casefold() in folded or not isinstance(digest, str) or
                re.fullmatch(r"[0-9a-f]{64}", digest) is None):
            raise ValueError("unsafe or ambiguous ownership inventory entry")
        folded.add(name.casefold())
        result[name] = digest
    return result


def _entry(root, name):
    path = root.joinpath(*name.split("/"))
    # Never traverse links even when they happen to point back into this root.
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError("upgrade entry traverses a symbolic link")
    if not path.resolve().is_relative_to(root):
        raise ValueError("upgrade entry resolves outside the install")
    if path.exists() and not path.is_file():
        raise ValueError("upgrade entry collides with a non-file")
    return path


def preview(root, candidate, previous=None, *, keep_local=(), deadline=None):
    """Return a complete conservative plan; this function writes nothing.

    A previous hash qualifies ownership only for those exact current bytes.
    Modified files and files with no baseline are never called replaceable.
    Retired files are retained, including previously owned bytes.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("installed root must be an existing directory")
    new = inventory(candidate)
    old = inventory(previous) if previous is not None else {}
    keep = set(keep_local)
    if not keep <= new.keys() | old.keys():
        raise ValueError("keep-local entries must belong to the compared inventory")
    rows = []
    for name in sorted(new.keys() | old.keys()):
        durable.check_deadline(deadline)
        path = _entry(root, name)
        before = durable.identity(path) if path.exists() else None
        current = durable.digest(path, deadline) if before is not None else None
        after = durable.identity(path) if path.exists() else None
        if before != after:
            raise OSError("installed file changed during upgrade preview")
        if name not in new:
            action = "retain_retired" if current is not None else "absent_retired"
        elif name in keep and current is not None:
            action = "preserve_local"
        elif current is None:
            action = "create"
        elif current == new[name]:
            action = "unchanged"
        elif name in old and current == old[name]:
            action = "replace_verified_owned"
        else:
            action = "preserve_collision"
        rows.append({"path": name, "action": action, "installed_sha256": current,
                     "candidate_sha256": new.get(name), "previous_sha256": old.get(name)})
    return {"v": 1, "root": str(root), "mode": "read-only preview", "files": rows,
            "collisions": sum(row["action"] == "preserve_collision" for row in rows),
            "limits": ["no file was written or deleted", "unknown ownership never authorizes replacement",
                       "host trust and hook review must be qualified separately",
                       "revalidate installed bytes before applying a separately approved upgrade"]}


def merge_settings(existing, candidate, owned_commands=()):
    """Preserve unrelated settings and handlers, replacing only exact owned commands.

    Host hook ordering is not synchronization. Returned custom pre-handlers must
    be reviewed for ordered-dispatch integration before replacing save guards.
    """
    import copy
    if not isinstance(existing, dict) or not isinstance(candidate, dict):
        raise ValueError("hook settings must be objects")
    merged = copy.deepcopy(existing)
    for key, value in candidate.items():
        if key != "hooks" and key not in merged:
            merged[key] = copy.deepcopy(value)
    owned = set(owned_commands)
    old_hooks, new_hooks = existing.get("hooks", {}), candidate.get("hooks", {})
    if not isinstance(old_hooks, dict) or not isinstance(new_hooks, dict):
        raise ValueError("hooks must be an object")
    hooks, custom_pre = {}, []
    for event in sorted(old_hooks.keys() | new_hooks.keys()):
        kept = []
        for group in old_hooks.get(event, []):
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError("malformed installed hook group")
            rest = []
            for handler in group["hooks"]:
                if not isinstance(handler, dict):
                    raise ValueError("malformed installed hook handler")
                if handler.get("command") not in owned:
                    rest.append(copy.deepcopy(handler))
                    if event == "PreToolUse":
                        custom_pre.append(copy.deepcopy(handler))
            if rest:
                kept.append({**copy.deepcopy(group), "hooks": rest})
        additions = new_hooks.get(event, [])
        if not isinstance(additions, list):
            raise ValueError("candidate hook groups must be a list")
        for group in additions:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError("malformed candidate hook group")
            for handler in group["hooks"]:
                if not isinstance(handler, dict) or (handler.get("type") == "command" and
                                                     not isinstance(handler.get("command"), str)):
                    raise ValueError("malformed candidate hook handler")
            if group not in kept:
                kept.append(copy.deepcopy(group))
        hooks[event] = kept
    merged["hooks"] = hooks
    return {"settings": merged, "custom_pre_handlers": custom_pre,
            "ordered_dispatch_review_required": bool(custom_pre),
            "host_enforcement": "unqualified by this merge"}
