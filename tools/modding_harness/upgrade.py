"""Read-only ownership-aware upgrade previews; no guessed ownership or deletion.

Profiles supply the candidate inventory and any verified previous inventory.
Unknown or modified installed files remain user-owned collisions. A preview is
not authority to replace them and never infers host review/trust from metadata.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import re
import os
import shutil
import stat
import time
import uuid

from . import durable
from .preservation import DEFAULT_RESERVE, contains

REVIEWABLE_SETTINGS = frozenset((".claude/settings.json", ".claude/settings.local.json",
                                ".codex/hooks.json", ".modding-agent-harness/local-policies.json"))
GENERATED_CONFIGS = frozenset((".codex/hooks.json",))
PROJECT_SETTINGS = {".claude/settings.json": "merged-settings.json", ".codex/hooks.json": "merged-codex-hooks.json"}


def json_bytes(value):
    import json
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False) + "\n").encode("ascii")


def policy_proposal(value, rollback, candidate):
    """Bind a profile's explicit settings edits to independently retained policy sources.

    This is proposal validation, never proof that a host enforces the policy.
    The profile must reproduce the proposal at application time.
    """
    import hashlib
    if (not isinstance(value, dict) or set(value) != {"v", "kind", "files", "sources", "requires"}
            or type(value["v"]) is not int or value["v"] != 1
            or not isinstance(value["kind"], str) or not 1 <= len(value["kind"]) <= 128):
        raise ValueError("invalid ordered-policy proposal")
    files, sources = value["files"], inventory({"v": 1, "files": value["sources"]})
    requires = inventory({"v": 1, "files": value["requires"]})
    if (not isinstance(files, dict) or not files or not set(files) <= REVIEWABLE_SETTINGS
            or not sources or set(sources) & set(files)
            or any(rollback.get(name) != digest for name, digest in sources.items())):
        raise ValueError("policy proposal lacks retained source authority or edits unrelated settings")
    if (not requires or set(requires) & (set(sources) | REVIEWABLE_SETTINGS)
            or any(candidate.get(name) != digest for name, digest in requires.items())):
        raise ValueError("policy proposal lacks candidate runtime requirements")
    if any(not isinstance(data, dict) for data in files.values()):
        raise ValueError("reviewed settings must be JSON objects")
    encoded = {name: json_bytes(data) for name, data in files.items()}
    if any(len(data) > 1024 * 1024 for data in encoded.values()):
        raise ValueError("reviewed settings exceed parser budget")
    proof = {"v": 1, "kind": value["kind"], "sources": sources, "requires": requires,
             "files": {name: hashlib.sha256(data).hexdigest() for name, data in encoded.items()}}
    return proof, encoded


def read_settings(base, name):
    import json
    from . import transport
    path = _entry(base, name)
    if not path.exists():
        return {}
    with path.open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("settings exceed review parser limit")
    value = transport.load(raw.decode("utf-8-sig"))
    json.dumps(value, allow_nan=False)  # NaN/Infinity are not JSON settings.
    return value


def validate_ordered_settings(bundle, proposal):
    """No custom pre-tool handler may survive an ordered migration unnoticed.

    Compare complete groups, including matcher, timeout and asynchronous fields.
    Recognizing a command string alone does not establish equivalent behavior.
    """
    import copy
    local = ".claude/settings.local.json"
    def pre(value):
        hooks = value.get("hooks", {})
        if not isinstance(hooks, dict) or not isinstance(hooks.get("PreToolUse", []), list):
            raise ValueError("malformed ordered pre-tool settings")
        return hooks.get("PreToolUse", [])
    def unrelated(settings):
        value = copy.deepcopy(settings)
        hooks = value.get("hooks", {})
        hooks.pop("PreToolUse", None)
        if not hooks:
            value.pop("hooks", None)
        return value
    for name in (*PROJECT_SETTINGS, local):
        settings = proposal["files"].get(name)
        if settings is None:
            settings = read_settings(bundle / "rollback", name)
        candidate = pre(read_settings(bundle / "candidate", name)) if name in PROJECT_SETTINGS else []
        if json_bytes(pre(settings)) != json_bytes(candidate):
            raise ValueError("unresolved custom pre-handlers remain in ordered-policy settings")
        if name in proposal["files"]:
            baseline = (read_settings(bundle, PROJECT_SETTINGS[name]) if name in PROJECT_SETTINGS
                        else read_settings(bundle / "rollback", name))
            if json_bytes(unrelated(settings)) != json_bytes(unrelated(baseline)):
                raise ValueError("ordered-policy edit changes unrelated settings or handlers")


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
        try:
            identity = parent.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(identity.st_mode) or
                getattr(identity, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise ValueError("upgrade entry traverses a symbolic link or redirected reparse point")
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


def pre_group_keys(groups):
    if not isinstance(groups, (list, tuple)) or len(groups) > 1024:
        raise ValueError("complete pre-handler references must be a bounded list")
    encoded = []
    for group in groups:
        if (not isinstance(group, dict) or not isinstance(group.get("hooks"), list) or
                any(not isinstance(handler, dict) for handler in group["hooks"])):
            raise ValueError("malformed complete pre-handler reference")
        encoded.append(json_bytes(group))
    if sum(map(len, encoded)) > 1024 * 1024:
        raise ValueError("complete pre-handler references exceed parser budget")
    return set(encoded)


def merge_settings(existing, candidate, owned_commands=(), *, owned_pre_groups=()):
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
    referenced_pre = pre_group_keys(owned_pre_groups)
    for event in sorted(old_hooks.keys() | new_hooks.keys()):
        additions = new_hooks.get(event, [])
        if not isinstance(additions, list):
            raise ValueError("candidate hook groups must be a list")
        known_pre = referenced_pre | pre_group_keys(additions) if event == "PreToolUse" else set()
        kept = []
        for group in old_hooks.get(event, []):
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError("malformed installed hook group")
            if event == "PreToolUse" and json_bytes(group) in known_pre:
                continue
            rest = []
            for handler in group["hooks"]:
                if not isinstance(handler, dict):
                    raise ValueError("malformed installed hook handler")
                # Each host platform's executable command needs independent
                # ownership. A familiar POSIX command cannot retire a personal
                # commandWindows policy that happens to share its handler.
                known = event != "PreToolUse" and handler.get("command") in owned and (
                    "commandWindows" not in handler or handler["commandWindows"] in owned)
                if not known:
                    rest.append(copy.deepcopy(handler))
                    if event == "PreToolUse":
                        custom_pre.append(copy.deepcopy(handler))
            if rest:
                kept.append({**copy.deepcopy(group), "hooks": rest})
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


def stage_review_bundle(root, source, destination, candidate, previous=None, *, keep_local=(),
                        owned_commands=(), local_files=(), reserve=DEFAULT_RESERVE, deadline=None,
                        review_builder=None, candidate_transform=None, generated_paths=(), owned_pre_groups=None):
    """Prepare independent candidate/rollback bytes without changing an install.

    This is a review artifact, not an installer or an authorization. Conflicting
    instructions remain explicit collisions. Project and local Claude settings
    are reviewed together; a retained custom pre-handler requires ordered-policy
    review even when it comes only from settings.local.json.
    """
    import json
    deadline = time.monotonic() + 60 if deadline is None else deadline
    root, source = Path(root).resolve(), Path(source).resolve()
    destination = Path(destination).absolute()
    if (not source.is_dir() or reserve < 0 or contains(root, destination) or contains(source, destination)
            or contains(destination, root) or contains(destination, source)):
        raise ValueError("review bundle must be separate from installed and candidate roots")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("review bundle destination already exists")
    files = inventory(candidate)
    generated_paths = tuple(generated_paths)
    if set(generated_paths) - GENERATED_CONFIGS or generated_paths and candidate_transform is None:
        raise ValueError("unsupported generated configuration or missing profile renderer")
    plan = preview(root, candidate, previous, keep_local=keep_local, deadline=deadline)
    owned = tuple(owned_commands)
    if owned_pre_groups is not None and (not isinstance(owned_pre_groups, dict) or set(owned_pre_groups) - PROJECT_SETTINGS.keys()):
        raise ValueError("pre-handler references must be scoped to a native settings file")
    references_by_file = owned_pre_groups or {}
    for groups in references_by_file.values():
        pre_group_keys(groups)
    if any(not isinstance(command, str) or not command or len(command) > 32768 for command in owned) or len(owned) > 1024:
        raise ValueError("invalid verified owned command list")
    extra = (*PROJECT_SETTINGS, ".claude/settings.local.json", "KNOWLEDGEBASE.local.md", "X4-NOTES.md",
             ".modding-agent-harness/config.json", ".modding-agent-harness/local-policies.json",
             ".codex/config.toml", ".opencode/opencode.jsonc", *local_files, *generated_paths)
    originals = {row["path"]: row["installed_sha256"] for row in plan["files"] if row["installed_sha256"] is not None}
    inventory({"v": 1, "files": dict.fromkeys((*files, *originals, *extra), "0" * 64)})
    for name in extra:
        path = _entry(root, name)
        if path.exists():
            originals[name] = durable.digest(path, deadline)
    # Validate candidate identity before creating even an incomplete bundle.
    required = 0
    for name, expected in files.items():
        path = _entry(source, name)
        if not path.is_file() or durable.digest(path, deadline) != expected:
            raise ValueError("candidate inventory does not match source: " + name)
        required += path.stat().st_size
    required += sum(_entry(root, name).stat().st_size for name in originals)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(destination.parent).free < required + reserve:
        raise OSError("insufficient space for independent candidate and rollback copies")
    destination.mkdir(exist_ok=False)
    def copy_set(base, entries, folder):
        copied = {}
        for name, expected in sorted(entries.items()):
            durable.check_deadline(deadline)
            target = destination / folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            info = durable.copy_verified(_entry(base, name), target, deadline)
            if info["sha256"] != expected:
                raise OSError("source changed while preparing review bundle: " + name)
            os.chmod(target, _entry(base, name).stat().st_mode & 0o777)
            copied[name] = info["sha256"]
        return copied
    incoming = copy_set(source, files, "candidate")
    rollback = copy_set(root, originals, "rollback")
    rendered, generated = [], []
    if candidate_transform is not None:
        updates = candidate_transform(root, destination / "candidate", dict(incoming))
        if (not isinstance(updates, dict) or set(updates) - (incoming.keys() | set(generated_paths)) or
                any(type(data) is not bytes or len(data) > 8 * 1024 * 1024 for data in updates.values()) or
                sum(map(len, updates.values())) > 16 * 1024 * 1024):
            raise ValueError("candidate rendering requires bounded bytes for inventoried or declared generated files")
        if shutil.disk_usage(destination).free < sum(map(len, updates.values())) + reserve:
            raise OSError("insufficient space for independent rendered candidate bytes")
        for name, data in sorted(updates.items()):
            durable.check_deadline(deadline)
            target = _entry(destination / "candidate", name)
            if name not in incoming:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb", buffering=0) as stream:
                    durable.write_all(stream.fileno(), data)
                    os.fsync(stream.fileno())
                durable.sync_dir(target.parent)
                import hashlib
                expected = hashlib.sha256(data).hexdigest()
                if durable.digest(target, deadline) != expected:
                    raise OSError("generated candidate failed readback")
                incoming[name] = expected
                rendered.append(name)
                generated.append(name)
                continue
            if durable.digest(target, deadline) != incoming[name]:
                raise OSError("candidate changed before target rendering")
            before = durable.identity(target)
            if target.stat().st_nlink != 1:
                raise ValueError("rendered candidates must be independent files")
            mode = target.stat().st_mode & 0o777
            part = target.with_name(".render-" + uuid.uuid4().hex)
            try:
                with part.open("xb", buffering=0) as stream:
                    durable.write_all(stream.fileno(), data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(part, mode)
                if durable.identity(target) != before or durable.digest(target, deadline) != incoming[name]:
                    raise OSError("candidate changed during target rendering")
                # This is our independent private candidate, never an installed
                # target. Preserve a read-only source's mode on the final copy.
                if not mode & 0o222:
                    os.chmod(target, mode | 0o200)
                os.replace(part, target)
                durable.sync_dir(target.parent)
            except BaseException:
                if target.exists() and durable.identity(target)[:2] == before[:2]:
                    os.chmod(target, mode)
                raise
            finally:
                if part.exists():
                    os.chmod(part, part.stat().st_mode | 0o200)
                    part.unlink()
            import hashlib
            expected = hashlib.sha256(data).hexdigest()
            if durable.digest(target, deadline) != expected:
                raise OSError("rendered candidate failed readback")
            incoming[name] = expected
            rendered.append(name)
        plan = preview(root, {"v": 1, "files": incoming}, previous, keep_local=keep_local, deadline=deadline)
    def settings(base, name):
        return read_settings(base, name)
    local_name = ".claude/settings.local.json"
    settings_review = {}
    merged_settings_sha256 = None
    merged_settings_hashes = {}
    for project_name, merged_name in PROJECT_SETTINGS.items():
        if project_name in incoming or project_name in originals:
            installed_settings = settings(destination / "rollback", project_name)
            references = tuple(references_by_file.get(project_name, ()))
            # The entire installed file matches an immutable prior inventory;
            # its complete old groups are qualified without guessing by name.
            if previous is not None and rollback.get(project_name) is not None and inventory(previous).get(project_name) == rollback[project_name]:
                old_hooks = installed_settings.get("hooks", {})
                if not isinstance(old_hooks, dict):
                    raise ValueError("malformed previous hook settings")
                references += tuple(old_hooks.get("PreToolUse", []))
            merged = merge_settings(installed_settings, settings(destination / "candidate", project_name), owned,
                                    owned_pre_groups=references)
            durable.atomic_json(destination / merged_name, merged["settings"])
            merged_settings_hashes[project_name] = durable.digest(destination / merged_name, deadline)
            settings_review[project_name] = {key: value for key, value in merged.items() if key != "settings"}
    merged_settings_sha256 = merged_settings_hashes.get(".claude/settings.json")
    if local_name in originals:
        local = merge_settings(settings(destination / "rollback", local_name), {}, ())
        settings_review[local_name] = {key: value for key, value in local.items() if key != "settings"}
    if plan != preview(root, {"v": 1, "files": incoming}, previous, keep_local=keep_local, deadline=deadline):
        raise OSError("installed files changed during review staging")
    for base, entries in ((root, originals), (source, files)):
        for name, expected in entries.items():
            if durable.digest(_entry(base, name), deadline) != expected:
                raise OSError("review source changed before bundle completion: " + name)
    if shutil.disk_usage(destination).free < reserve:
        raise OSError("free-space reserve changed during review staging")
    result = {"v": 1, "mode": "review bundle only; install unchanged", "plan": plan,
              "candidate_files": incoming, "rollback_files": rollback, "settings_review": settings_review,
              "candidate_modes": {name: (destination / "candidate" / name).stat().st_mode & 0o777 for name in incoming},
              "observed_local_files": {name: originals.get(name) for name in extra},
              "merged_settings_sha256": merged_settings_sha256,
              "merged_settings_hashes": merged_settings_hashes,
              "ordered_dispatch_review_required": any(r["ordered_dispatch_review_required"] for r in settings_review.values()),
              "contains_private_installed_bytes": bool(rollback), "application_authorized": False,
              "limits": ["collisions and instruction merges require explicit review", "no host trust was changed",
                         "rollback bytes are evidence; no automatic deployment or rollback is performed"]}
    if candidate_transform is not None:
        result["source_files"] = files
        result["rendered_files"] = rendered
        result["generated_files"] = generated
    if review_builder is not None:
        proposal = review_builder(destination, result)
        if proposal is not None:
            proof, encoded = policy_proposal(proposal, rollback, incoming)
            validate_ordered_settings(destination, proposal)
            observed = result["observed_local_files"]
            if any(observed.get(name) != digest for name, digest in proof["sources"].items()):
                raise ValueError("ordered policy sources must be captured as observed local files")
            if shutil.disk_usage(destination).free < sum(map(len, encoded.values())) + reserve:
                raise OSError("insufficient space for independent ordered-policy proposals")
            for name, data in encoded.items():
                target = _entry(destination / "reviewed", name)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                durable.sync_dir(target.parent)
                if durable.digest(target, deadline) != proof["files"][name]:
                    raise OSError("ordered-policy proposal failed readback")
                if name not in {row["path"] for row in plan["files"]}:
                    if name not in observed:
                        raise ValueError("reviewed settings were not captured before proposal")
                    plan["files"].append({"path": name, "action": "reviewed_edit",
                        "installed_sha256": observed[name], "candidate_sha256": None, "previous_sha256": None})
            result["ordered_integration"] = proof
            # A profile proposal must not obscure a concurrent policy/config edit.
            for name, expected in originals.items():
                if durable.digest(_entry(root, name), deadline) != expected:
                    raise OSError("installed policy changed during ordered review")
            for name, expected in observed.items():
                path = _entry(root, name)
                if (durable.digest(path, deadline) if path.exists() else None) != expected:
                    raise OSError("local configuration changed during ordered review")
            durable.check_deadline(deadline)
    for name, expected in result["observed_local_files"].items():
        path = _entry(root, name)
        if (durable.digest(path, deadline) if path.exists() else None) != expected:
            raise OSError("local configuration changed before review completion")
    durable.atomic_json(destination / "complete.json", result)
    return result


def preserve_notes(root, candidate_sha256, *, reserve=DEFAULT_RESERVE, deadline=None):
    """Verify an independent legacy KB copy before an installer replaces it.

    Existing local notes are never edited. A new local file links the retained
    evidence under an empty preference section; old prose is not guessed policy.
    """
    deadline = time.monotonic() + 30 if deadline is None else deadline
    if not isinstance(candidate_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", candidate_sha256) is None:
        raise ValueError("candidate knowledgebase needs a full SHA-256")
    root = Path(root).resolve()
    if not root.is_dir() or reserve < 0:
        raise ValueError("notes destination must be an existing directory with a nonnegative reserve")
    source = _entry(root, "KNOWLEDGEBASE.md")
    if not source.exists():
        return {"preserved": False, "reason": "no legacy knowledgebase"}
    before = durable.identity(source)
    digest = durable.digest(source, deadline)
    if durable.identity(source) != before:
        raise OSError("legacy notes changed while preparing preservation")
    if digest == candidate_sha256:
        return {"preserved": False, "reason": "installed knowledgebase already matches candidate"}
    saved = _entry(root, "KNOWLEDGEBASE.pre-4.0-" + digest + ".md")
    if saved.exists():
        if saved.stat().st_nlink != 1 or durable.digest(saved, deadline) != digest:
            raise OSError("retained legacy notes are linked or corrupt; refuse replacement")
    else:
        if shutil.disk_usage(root).free < before[2] + reserve:
            raise OSError("insufficient destination space for independent legacy notes preservation")
        part = saved.with_name(".notes-part-" + uuid.uuid4().hex)
        try:
            copied = durable.copy_verified(source, part, deadline)
            if copied["sha256"] != digest:
                raise OSError("legacy notes changed during preservation")
            durable.publish_new(part, saved)
        finally:
            part.unlink(missing_ok=True)
    if (durable.identity(source) != before or durable.digest(source, deadline) != digest or
            durable.digest(saved, deadline) != digest or shutil.disk_usage(root).free < reserve):
        raise OSError("legacy notes preservation failed final verification")
    local = root / "KNOWLEDGEBASE.local.md"
    created = False
    if not local.exists():
        data = ("# Local knowledge\n\n## My preferences\n\n## Imported legacy notes\n\n"
                "The prior knowledgebase is preserved verbatim in [" + saved.name + "](" + saved.name +
                "). Review it as evidence; unclassified notes are not standing preferences.\n").encode("utf-8")
        part = root / (".notes-part-" + uuid.uuid4().hex)
        try:
            with part.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                durable.publish_new(part, local)
                created = True
            except FileExistsError:
                pass  # A concurrent user's local file remains theirs.
        finally:
            part.unlink(missing_ok=True)
    return {"preserved": True, "legacy_sha256": digest, "snapshot": str(saved),
            "local_notes": str(local), "created_local_notes": created,
            "classification": "unclassified evidence; no preferences inferred"}
