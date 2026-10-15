"""Explicit, journalled toolkit upgrades from private reviewed bundles.

Callers must stop host/editor writers first. The OS lock coordinates harness
upgraders, not arbitrary foreign writers. Replacements recheck expected bytes,
but ordinary filesystems offer no portable atomic compare-and-swap of a file.
New files use no-clobber publication. Host review/trust is never established.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid

from . import durable, upgrade, workspace
from .locking import exclusive
from .preservation import DEFAULT_RESERVE, protects

LOCAL_ONLY = frozenset((".claude/settings.local.json", ".codex/config.toml", ".opencode/opencode.jsonc",
                        ".modding-agent-harness/config.json", "KNOWLEDGEBASE.local.md", "X4-NOTES.md"))
COMMANDS = frozenset(("upgrade-preview", "upgrade-stage", "upgrade-apply", "upgrade-rollback"))


class UpgradeFailed(OSError):
    pass


def add_arguments(subparsers):
    """The same upgrade CLI contract for every profile; no named-host dependency."""
    preview = subparsers.add_parser("upgrade-preview")
    stage = subparsers.add_parser("upgrade-stage")
    for parser in (preview, stage):
        parser.add_argument("--candidate", required=True, type=Path)
        parser.add_argument("--previous", type=Path)
        parser.add_argument("--installed-root", type=Path)
    stage.add_argument("--source", required=True, type=Path)
    stage.add_argument("--destination", required=True, type=Path)
    stage.add_argument("--owned-command", action="append", default=[])
    stage.add_argument("--local-file", action="append", default=[])
    applying = subparsers.add_parser("upgrade-apply")
    applying.add_argument("--bundle", required=True, type=Path)
    applying.add_argument("--review-sha256", required=True)
    applying.add_argument("--choices", type=Path)
    rolling = subparsers.add_parser("upgrade-rollback")
    rolling.add_argument("operation")
    for parser in (applying, rolling):
        parser.add_argument("--installed-root", type=Path)
        parser.add_argument("--quiescent", action="store_true", required=True,
                            help="host/editor writers have been stopped; this flag does not stop them")


def handle(args, root, *, local_files=()):
    from . import transport
    def read(path):
        with Path(path).open("rb") as stream:
            return transport.load(stream.read(1024 * 1024 + 1))
    root = args.installed_root or root
    if args.command == "upgrade-preview":
        return upgrade.preview(root, read(args.candidate), read(args.previous) if args.previous else None,
                               deadline=time.monotonic() + 20)
    if args.command == "upgrade-stage":
        bundle = upgrade.stage_review_bundle(root, args.source, args.destination, read(args.candidate),
            read(args.previous) if args.previous else None, owned_commands=args.owned_command,
            local_files=(*local_files, *args.local_file))
        return {"bundle": str(args.destination.resolve()), "complete": True, "collisions": bundle["plan"]["collisions"],
                "application_authorized": False, "ordered_dispatch_review_required": bundle["ordered_dispatch_review_required"]}
    if args.command == "upgrade-apply":
        return apply(root, args.bundle, args.review_sha256, read(args.choices) if args.choices else {}, quiescent=args.quiescent)
    if args.command == "upgrade-rollback":
        return rollback(root, args.operation, quiescent=args.quiescent)
    raise ValueError("not a shared upgrade command")


def failure(command, exc, label):
    from .contracts import Result
    status = (1 if isinstance(exc, UpgradeFailed) else 2) if command in ("upgrade-stage", "upgrade-apply", "upgrade-rollback") else 0
    reason = label + ": " + (str(exc) if command in ("upgrade-apply", "upgrade-rollback") else type(exc).__name__)
    return {"api_version": 1, "result": Result("deny", "failed", reason=reason).as_dict(), "transactions": []}, status


def _json(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("upgrade record exceeds parser budget")
    result = json.loads(raw)
    if not isinstance(result, dict) or type(result.get("v")) is not int or result["v"] != 1:
        raise ValueError("unsupported upgrade record")
    return result


def _current(root, name, deadline):
    path = upgrade._entry(root, name)
    return durable.digest(path, deadline) if path.exists() else None


def _writable_target(path):
    # Windows rejects replacing read-only files even with a writable parent.
    # Treat the recorded read-only mode conservatively on every platform; an
    # upgrade must never silently lift an existing file's protection.
    if path.exists() and not path.stat().st_mode & 0o222:
        raise ValueError("read-only installed target requires separate review: " + str(path))


def apply(root, bundle, expected_sha256, choices=None, *, quiescent=False, reserve=DEFAULT_RESERVE, deadline=None):
    """Apply an explicitly selected review; collisions never gain guessed ownership.

    choices maps collision paths to keep/candidate. Project settings may select
    merged_settings only when no custom pre-handler needs ordered integration.
    The review SHA binds the exact complete.json; it is not an approval reader.
    """
    deadline = time.monotonic() + 120 if deadline is None else deadline
    root, bundle = Path(root).resolve(), Path(bundle).resolve()
    if quiescent is not True:
        raise ValueError("stop host/editor writers before applying or rolling back an upgrade")
    if not root.is_dir() or reserve < 0 or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256 or ""):
        raise ValueError("invalid upgrade root, reserve or review SHA")
    if upgrade.contains(root, bundle) or upgrade.contains(bundle, root):
        raise ValueError("upgrade bundle must be separate from installed root")
    complete = upgrade._entry(bundle, "complete.json")
    with complete.open("rb") as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("upgrade review exceeds parser budget")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("review SHA changed; re-review the bundle")
    review = json.loads(raw)
    if not isinstance(review, dict) or type(review.get("v")) is not int or review["v"] != 1:
        raise ValueError("unsupported upgrade review")
    if review.get("ordered_dispatch_review_required"):
        raise ValueError("custom pre-handlers require ordered-policy integration before application")
    plan = review.get("plan", {})
    if (not isinstance(plan, dict) or plan.get("v") != 1 or not isinstance(plan.get("root"), str)
            or Path(plan["root"]).resolve() != root or not isinstance(plan.get("files"), list)):
        raise ValueError("review belongs to a different installed root")
    candidate = upgrade.inventory({"v": 1, "files": review.get("candidate_files")})
    rollback = upgrade.inventory({"v": 1, "files": review.get("rollback_files")})
    modes = review.get("candidate_modes")
    if (not isinstance(modes, dict) or modes.keys() != candidate.keys() or any(
            type(mode) is not int or not 0 <= mode <= 0o777 for mode in modes.values())):
        raise ValueError("candidate modes are not bound to this review")
    local = review.get("observed_local_files")
    if not isinstance(local, dict):
        raise ValueError("local configuration observations are missing")
    upgrade.inventory({"v": 1, "files": dict.fromkeys(local, "0" * 64)})
    for name, expected in local.items():
        if expected is not None and (not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None):
            raise ValueError("invalid local configuration hash")
    critical = {name: expected for name, expected in local.items() if name not in ("KNOWLEDGEBASE.local.md", "X4-NOTES.md")}
    def check_local(published=()):
        applied = {row["path"]: row["after"] for row in changes if row["path"] in published}
        for name, expected in critical.items():
            if _current(root, name, deadline) != applied.get(name, expected):
                raise ValueError("local configuration or policy source changed since review: " + name)
    choices = dict(choices or {})
    rows = {row["path"]: row for row in plan["files"]}
    if len(rows) != len(plan["files"]) or set(choices) - rows.keys() or candidate.keys() - rows.keys():
        raise ValueError("ambiguous review paths or unknown selection")
    upgrade.inventory({"v": 1, "files": dict.fromkeys(rows, "0" * 64)})
    changes = []
    check_local()
    for name, row in rows.items():
        expected = row.get("installed_sha256")
        if _current(root, name, deadline) != expected:
            raise ValueError("installed bytes changed since review: " + name)
        if expected is not None and rollback.get(name) != expected:
            raise ValueError("review lacks independent rollback authority: " + name)
        action = row.get("action")
        default = "candidate" if action in ("create", "replace_verified_owned") else "keep"
        if action == "preserve_collision" and name not in choices:
            raise ValueError("unresolved installed collision: " + name)
        selected = choices.get(name, default)
        if selected not in ("keep", "candidate", "merged_settings"):
            raise ValueError("unknown upgrade selection")
        if selected == "keep":
            continue
        if name in LOCAL_ONLY:
            raise ValueError("local settings/notes must be retained: " + name)
        if selected == "merged_settings":
            if name != ".claude/settings.json" or review.get("ordered_dispatch_review_required"):
                raise ValueError("custom pre-handlers require ordered-policy integration before application")
            incoming = upgrade._entry(bundle, "merged-settings.json")
            digest = review.get("merged_settings_sha256")
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError("merged settings lack reviewed hash authority")
        else:
            if name not in candidate or row.get("candidate_sha256") != candidate[name]:
                raise ValueError("candidate authority missing: " + name)
            if name == ".claude/settings.json" and expected is not None:
                raise ValueError("existing project settings require an explicit merged_settings selection")
            incoming = upgrade._entry(bundle, "candidate/" + name)
            digest = candidate[name]
        target = upgrade._entry(root, name)
        _writable_target(target)
        stores = (workspace.settings(root)["destination"], root / ".claude/backups", root / ".modding-agent-harness/upgrades")
        if protects(target, stores, ancestor=True):
            raise ValueError("upgrade cannot write recovery data or its ancestor")
        if durable.digest(incoming, deadline) != digest:
            raise ValueError("staged candidate bytes changed: " + name)
        if expected is not None and durable.digest(upgrade._entry(bundle, "rollback/" + name), deadline) != expected:
            raise ValueError("rollback bytes changed: " + name)
        changes.append({"path": name, "before": expected, "after": digest, "source": str(incoming),
                        "mode": modes[name], "before_mode": upgrade._entry(root, name).stat().st_mode & 0o777 if expected else None})
    # Hook definitions are published after their runtime. Review is still needed.
    changes.sort(key=lambda row: (row["path"] in (".claude/settings.json", ".codex/hooks.json"), row["path"]))
    state = root / ".modding-agent-harness"
    upgrade._entry(root, ".modding-agent-harness/upgrade.lock")
    state.mkdir(parents=True, exist_ok=True)
    with exclusive(state / "upgrade.lock", max(0, min(5, deadline - time.monotonic()))):
        check_local()
        for name, row in rows.items():
            if _current(root, name, deadline) != row.get("installed_sha256"):
                raise ValueError("installed bytes changed before upgrade lock: " + name)
        for row in changes:
            _writable_target(upgrade._entry(root, row["path"]))
        required = sum(Path(row["source"]).stat().st_size +
                       (upgrade._entry(root, row["path"]).stat().st_size if row["before"] is not None else 0) for row in changes)
        if shutil.disk_usage(root).free < required + reserve:
            raise OSError("insufficient space for permanent upgrade rollback and staging")
        operation = uuid.uuid4().hex
        journal = state / "upgrades" / operation
        upgrade._entry(root, ".modding-agent-harness/upgrades/" + operation + "/plan.json")
        journal.mkdir(parents=True, exist_ok=False)
        durable.atomic_json(journal / "plan.json", {"v": 1, "root": str(root), "review_sha256": expected_sha256, "changes": changes})
        staged, published = [], []
        try:
            # Preserve every old file before replacing any target.
            for row in changes:
                if row["before"] is not None:
                    saved = journal / "rollback" / row["path"]
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    info = durable.copy_verified(upgrade._entry(root, row["path"]), saved, deadline)
                    if info["sha256"] != row["before"]:
                        raise OSError("installed source changed while preserving rollback")
            for row in changes:
                target = upgrade._entry(root, row["path"])
                check_local(published)
                target.parent.mkdir(parents=True, exist_ok=True)
                part = target.with_name(".upgrade-" + operation + "-" + uuid.uuid4().hex)
                staged.append(part)
                info = durable.copy_verified(row["source"], part, deadline)
                if info["sha256"] != row["after"]:
                    raise OSError("candidate changed while staging upgrade")
                os.chmod(part, row["mode"])
                if _current(root, row["path"], deadline) != row["before"]:
                    raise OSError("installed target changed before publication")
                _writable_target(target)
                if row["path"] == "KNOWLEDGEBASE.md" and row["before"] is not None:
                    upgrade.preserve_notes(root, row["after"], reserve=reserve, deadline=deadline)
                check_local(published)
                if shutil.disk_usage(root).free < reserve:
                    raise OSError("free-space reserve changed during upgrade")
                durable.check_deadline(deadline)
                identity = list(durable.identity(part))
                if not identity[1]:
                    raise OSError("filesystem cannot identify an owned publication for rollback")
                durable.atomic_json(journal / ("{:06d}-intent.json".format(len(published) + 1)),
                                    {"v": 1, "path": row["path"], "identity": identity})
                if row["before"] is None:
                    durable.publish_new(part, target)
                else:
                    os.replace(part, target)
                    durable.sync_dir(target.parent)
                published.append(row["path"])
                if durable.digest(target, deadline) != row["after"]:
                    raise OSError("published upgrade file failed readback")
                durable.atomic_json(journal / ("{:06d}-published.json".format(len(published))), {"v": 1, "path": row["path"]})
            result = {"v": 1, "operation": operation, "published": published, "journal": str(journal),
                      "host_review_required": True, "host_enforcement": "unqualified by installation"}
            durable.atomic_json(journal / "complete.json", result)
            return result
        except (OSError, ValueError) as exc:
            try:
                durable.atomic_json(journal / "failed.json", {"v": 1, "published": published, "error": str(exc)})
            except OSError:
                pass
            raise UpgradeFailed("upgrade failed; published={!r}; journal={}: {}".format(published, journal, exc)) from exc
        finally:
            for part in staged:
                if part.exists():
                    os.chmod(part, part.stat().st_mode | 0o200)
                    part.unlink()


def rollback(root, operation, *, quiescent=False, deadline=None):
    """Restore prior bytes only while published files still have this operation's identity.

    New files are retained in the journal, not discarded. A foreign edit/rename
    refuses the rollback before it changes any target. Partial failures remain
    journalled and old/new evidence is retained for explicit recovery.
    """
    deadline = time.monotonic() + 120 if deadline is None else deadline
    root = Path(root).resolve()
    if quiescent is not True or re.fullmatch(r"[0-9a-f]{32}", operation or "") is None:
        raise ValueError("rollback requires a quiescent install and exact upgrade UUID")
    relative = ".modding-agent-harness/upgrades/" + operation
    journal = upgrade._entry(root, relative + "/plan.json").parent
    plan = _json(journal / "plan.json")
    if plan.get("root") != str(root) or not isinstance(plan.get("changes"), list):
        raise ValueError("rollback plan belongs to a different root")
    rows = plan["changes"]
    upgrade.inventory({"v": 1, "files": {row["path"]: row["after"] for row in rows}})
    if len({row["path"] for row in rows}) != len(rows):
        raise ValueError("ambiguous rollback paths")
    intents = {}
    for path in journal.glob("*-intent.json"):
        intent = _json(upgrade._entry(root, relative + "/" + path.name))
        if intent.get("path") in intents:
            raise ValueError("ambiguous publication intent")
        identity = intent.get("identity")
        if not isinstance(identity, list) or len(identity) != 5 or not all(type(v) is int for v in identity) or not identity[1]:
            raise ValueError("invalid publication identity")
        intents[intent["path"]] = identity
    actions = []
    state = root / ".modding-agent-harness"
    with exclusive(upgrade._entry(root, ".modding-agent-harness/upgrade.lock"), max(0, min(5, deadline - time.monotonic()))):
        for row in rows:
            name = row["path"]
            target = upgrade._entry(root, name)
            stores = (workspace.settings(root)["destination"], root / ".claude/backups", root / ".modding-agent-harness/upgrades")
            if protects(target, stores, ancestor=True) or name in LOCAL_ONLY:
                raise ValueError("rollback cannot write protected recovery data or local configuration")
            current = _current(root, name, deadline)
            if current == row["before"]:
                continue
            if (current != row["after"] or name not in intents or
                    list(durable.identity(target)[:2]) != intents[name][:2]):
                raise ValueError("rollback would overwrite a foreign or changed file: " + name)
            _writable_target(target)
            if row["before"] is not None:
                prior = upgrade._entry(root, relative + "/rollback/" + name)
                if (durable.digest(prior, deadline) != row["before"] or type(row.get("before_mode")) is not int
                        or not 0 <= row["before_mode"] <= 0o777):
                    raise ValueError("rollback evidence is damaged")
            actions.append(row)
        receipt = journal / "rollbacks" / uuid.uuid4().hex
        receipt.mkdir(parents=True, exist_ok=False)
        durable.atomic_json(receipt / "plan.json", {"v": 1, "paths": [row["path"] for row in actions]})
        restored, retained, staged = [], [], []
        try:
            for row in reversed(actions):
                durable.check_deadline(deadline)
                target = upgrade._entry(root, row["path"])
                if (_current(root, row["path"], deadline) != row["after"] or
                        list(durable.identity(target)[:2]) != intents[row["path"]][:2]):
                    raise OSError("target changed during rollback")
                _writable_target(target)
                if row["before"] is None:
                    saved = receipt / "retained" / row["path"]
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    durable.publish_new(target, saved)
                    retained.append(row["path"])
                else:
                    part = target.with_name(".rollback-" + uuid.uuid4().hex)
                    staged.append(part)
                    copied = durable.copy_verified(journal / "rollback" / row["path"], part, deadline)
                    if copied["sha256"] != row["before"]:
                        raise OSError("rollback candidate changed")
                    os.chmod(part, row["before_mode"] & 0o777)
                    if (_current(root, row["path"], deadline) != row["after"] or
                            list(durable.identity(target)[:2]) != intents[row["path"]][:2]):
                        raise OSError("target changed before rollback publication")
                    _writable_target(target)
                    os.replace(part, target)
                    durable.sync_dir(target.parent)
                    restored.append(row["path"])
                durable.atomic_json(receipt / ("{:06d}-restored.json".format(len(restored) + len(retained))),
                                    {"v": 1, "path": row["path"]})
            result = {"v": 1, "restored": restored, "retained_new_files": retained,
                      "journal": str(receipt), "host_review_required": True}
            durable.atomic_json(receipt / "complete.json", result)
            return result
        except (OSError, ValueError) as exc:
            try:
                durable.atomic_json(receipt / "failed.json", {"v": 1, "restored": restored,
                                    "retained_new_files": retained, "error": str(exc)})
            except OSError:
                pass
            raise UpgradeFailed("rollback failed; restored={!r}; retained={!r}; journal={}: {}".format(
                restored, retained, receipt, exc)) from exc
        finally:
            for part in staged:
                if part.exists():
                    os.chmod(part, part.stat().st_mode | 0o200)
                    part.unlink()
