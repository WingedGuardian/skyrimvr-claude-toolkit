"""Permanent save transactions. A committed immutable manifest is the authority.

No index is needed for recovery. Orphaned candidates/blobs are retained, never
treated as committed backups and never automatically pruned. This is verified
copying, not an atomic filesystem snapshot of a concurrent native writer.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import durable
from .profiles import canonical, comparison_key
from .locking import exclusive

DEFAULT_RESERVE = 2 * 1024 ** 3


class PreservationFailed(OSError):
    pass


def contains(parent, child):
    try:
        parent, child = canonical(parent), canonical(child)
        return os.path.commonpath((parent, child)) == parent
    except ValueError:
        return False


def protects(path, stores, ancestor=False):
    """A recognized mutation may neither change a store nor move/delete its ancestor."""
    resolved = None
    for store in stores:
        try:
            store = canonical(store)
            if resolved is None:
                resolved = canonical(path)
            shared = os.path.commonpath((store, resolved))
            if shared == store or (ancestor and shared == resolved):
                return True
        except ValueError:
            continue
    return False


def _state(members):
    result = []
    for path in members:
        path = Path(path)
        if path.is_symlink():
            raise PreservationFailed("save members cannot be symbolic links: {}".format(path))
        if path.exists() and not path.is_file():
            raise PreservationFailed("save member is not a regular file: {}".format(path))
        result.append((str(path.absolute()), canonical(path), durable.identity(path) if path.exists() else None))
    if len({real for _, real, _ in result}) != len(result):
        raise PreservationFailed("ambiguous save member identities")
    return result


class SaveStore:
    def __init__(self, workspace, destination=None, save_roots=(), reserve=DEFAULT_RESERVE):
        self.root = Path(destination) if destination else Path(workspace) / ".modding-agent-harness/backups/saves"
        self.root = self.root.resolve()
        if reserve < 0:
            raise ValueError("reserve cannot be negative")
        self.reserve = reserve
        for save_root in save_roots:
            if contains(save_root, self.root) or contains(self.root, save_root):
                raise ValueError("backup store must be separate from save roots")

    def backup(self, groups, metadata=None, deadline=None, known_writer_active=False):
        """groups contains complete, profile-resolved logical saves, including absent members."""
        if known_writer_active:
            raise PreservationFailed("active native save writer requires a qualified quiescent route")
        return self._preserve(groups, metadata, deadline)

    def import_records(self, groups, metadata=None, deadline=None):
        """Preserve legacy recovery evidence without reading or authorizing live saves.

        Each member supplies original/source/missing and, when present, expected
        SHA-256/size from the legacy authority. The complete copied source set is
        revalidated before those original recovery destinations are recorded.
        """
        imported, sources = {}, []
        for group in groups:
            members = []
            for record in group:
                if (not isinstance(record, dict) or not isinstance(record.get("original"), str)
                        or not os.path.isabs(record["original"]) or not isinstance(record.get("source"), str)
                        or not os.path.isabs(record["source"]) or type(record.get("missing")) is not bool):
                    raise ValueError("invalid legacy recovery member")
                if not record["missing"] and (type(record.get("size")) is not int or record["size"] < 0
                        or not isinstance(record.get("sha256"), str) or len(record["sha256"]) != 64
                        or any(c not in "0123456789abcdef" for c in record["sha256"])):
                    raise ValueError("invalid legacy hash/size evidence")
                source = str(Path(record["source"]).absolute())
                if source in imported:
                    raise ValueError("duplicate legacy recovery source")
                imported[source] = dict(record)
                members.append(source)
            sources.append(members)
        return self._preserve(sources, metadata, deadline, imported)

    def _preserve(self, groups, metadata, deadline, imported=None):
        try:
            durable.check_deadline(deadline)
            self.root.mkdir(parents=True, exist_ok=True)
            wait = 5.0 if deadline is None else max(0, min(5.0, deadline - time.monotonic()))
            with exclusive(self.root / ".lock", wait):
                return self._backup_locked(groups, metadata, deadline, imported)
        except (OSError, ValueError) as exc:
            if isinstance(exc, PreservationFailed):
                raise
            raise PreservationFailed(str(exc)) from exc

    def _backup_locked(self, groups, metadata, deadline, imported=None):
        transaction = uuid.uuid4().hex
        staged = []
        try:
            durable.check_deadline(deadline)
            states = [_state(group) for group in groups]
            states = [state for state in states if any(ident is not None for _, _, ident in state)]
            if not states:
                return None
            identities = [real for state in states for _, real, _ in state]
            if len(identities) != len(set(identities)):
                raise PreservationFailed("duplicate logical save membership")
            for name in ("blobs", "transactions", "staging"):
                (self.root / name).mkdir(parents=True, exist_ok=True)
            total = sum(ident[2] for state in states for _, _, ident in state if ident is not None)
            source_hashes, needed = {}, {}
            for state in states:
                for path, _real, ident in state:
                    if ident is None:
                        continue
                    sha = durable.digest(path, deadline)
                    if durable.identity(path) != ident:
                        raise PreservationFailed("source changed during capacity planning")
                    source_hashes[path] = sha
                    blob = self.root / "blobs" / sha
                    if blob.exists():
                        if durable.digest(blob, deadline) != sha:
                            raise PreservationFailed("reused backup blob is corrupt: {}".format(blob))
                    else:
                        needed[sha] = ident[2]
            required = sum(needed.values())
            if shutil.disk_usage(self.root).free - required < self.reserve:
                raise PreservationFailed("insufficient destination space for {} bytes plus {} reserve at {}".format(
                    required, self.reserve, self.root))
            records, used, deduplicated = [], 0, 0
            for group_index, state in enumerate(states):
                members = []
                for path, real, ident in state:
                    rec = {"path": path, "canonical": real, "missing": ident is None}
                    if ident is not None:
                        sha = source_hashes[path]
                        blob = self.root / "blobs" / sha
                        if blob.exists():
                            if durable.digest(blob, deadline) != sha:
                                raise PreservationFailed("reused backup blob is corrupt: {}".format(blob))
                            rec.update(sha256=sha, size=ident[2], identity=list(ident))
                            deduplicated += ident[2]
                            members.append(rec)
                            continue
                        candidate = self.root / "staging" / (uuid.uuid4().hex + ".part")
                        staged.append(candidate)
                        info = durable.copy_verified(path, candidate, deadline)
                        if info["sha256"] != sha or info["identity"] != list(ident):
                            raise PreservationFailed("source changed after capacity planning")
                        blob = self.root / "blobs" / info["sha256"]
                        try:
                            durable.publish_new(candidate, blob)
                            used += info["size"]
                        except FileExistsError:
                            if durable.digest(blob, deadline) != info["sha256"]:
                                raise PreservationFailed("reused backup blob is corrupt: {}".format(blob))
                            deduplicated += info["size"]
                        rec.update(info)
                    members.append(rec)
                records.append({"logical_save": group_index, "members": members})
            # Recheck the COMPLETE set after all members are copied, including
            # missing members appearing and same-size/mtime rewrites of earlier files.
            self.revalidate(records, deadline)
            if imported is not None:
                destinations = set()
                for group in records:
                    for rec in group["members"]:
                        expected = imported[rec["path"]]
                        if (rec["missing"] != expected["missing"] or not rec["missing"] and
                                (rec["sha256"] != expected.get("sha256") or rec["size"] != expected.get("size"))):
                            raise PreservationFailed("legacy evidence does not match preserved source")
                        original = canonical(expected["original"])
                        if original in destinations:
                            raise PreservationFailed("ambiguous legacy recovery destinations")
                        destinations.add(original)
                        rec.update(preserved_from=rec["path"], path=expected["original"], canonical=original)
            if shutil.disk_usage(self.root).free < self.reserve:
                raise PreservationFailed("destination free-space reserve changed during preservation")
            manifest = {"v": 1, "id": transaction, "utc": datetime.now(timezone.utc).isoformat(),
                        "purpose": "recovery-import" if imported is not None else "pre-mutation",
                        "groups": records, "metadata": metadata or {}, "destination": str(self.root),
                        "logical_bytes": total, "required_bytes": required, "stored_bytes": used, "deduplicated_bytes": deduplicated,
                        "reserve_bytes": self.reserve, "complete": True}
            durable.check_deadline(deadline)
            durable.atomic_json(self.root / "transactions" / (transaction + ".json"), manifest)
            if shutil.disk_usage(self.root).free < self.reserve:
                raise PreservationFailed("destination free-space reserve unavailable after commit")
            if self.load(transaction, deadline) != manifest:
                raise PreservationFailed("committed transaction failed readback")
            return manifest
        except (OSError, ValueError) as exc:
            if isinstance(exc, PreservationFailed):
                raise
            raise PreservationFailed(str(exc)) from exc
        finally:
            for candidate in staged:
                candidate.unlink(missing_ok=True)

    @staticmethod
    def revalidate(groups, deadline=None):
        for group in groups:
            for rec in group["members"]:
                path = rec["path"]
                if canonical(path) != comparison_key(rec["canonical"]):
                    raise PreservationFailed("save path resolution changed: " + path)
                if rec["missing"]:
                    if os.path.lexists(path):
                        raise PreservationFailed("missing save counterpart appeared: " + path)
                elif (list(durable.identity(path)) != rec["identity"]
                      or durable.digest(path, deadline) != rec["sha256"]
                      or list(durable.identity(path)) != rec["identity"]):
                    raise PreservationFailed("save changed before transaction committed: " + path)

    def load(self, transaction, deadline=None):
        manifest = self.inspect(transaction)
        self.verify(manifest["groups"], deadline)
        return manifest

    def verify(self, groups, deadline=None):
        # Bound by the destination store rather than any manifest-supplied path.
        for group in groups:
            for rec in group["members"]:
                if not rec["missing"]:
                    sha = rec["sha256"]
                    blob = self.root / "blobs" / sha
                    if blob.is_symlink() or blob.stat().st_size != rec["size"] or durable.digest(blob, deadline) != sha:
                        raise PreservationFailed("preserved member failed verification")

    def inspect(self, transaction):
        """Validate record structure without claiming current blob verification."""
        if not isinstance(transaction, str) or len(transaction) != 32:
            raise ValueError("invalid transaction id")
        try:
            uuid.UUID(hex=transaction)
        except ValueError:
            raise ValueError("invalid transaction id") from None
        manifest = json.loads((self.root / "transactions" / (transaction + ".json")).read_text(encoding="ascii"))
        if (not isinstance(manifest, dict) or type(manifest.get("v")) is not int
                or manifest.get("v") != 1 or manifest.get("id") != transaction
                or manifest.get("complete") is not True or not isinstance(manifest.get("groups"), list)
                or not manifest["groups"]):
            raise ValueError("incomplete preservation transaction")
        seen, logical = set(), set()
        for group in manifest["groups"]:
            if (not isinstance(group, dict) or type(group.get("logical_save")) is not int
                    or group["logical_save"] in logical or not isinstance(group.get("members"), list)
                    or not group["members"]):
                raise ValueError("empty logical save")
            logical.add(group["logical_save"])
            for rec in group["members"]:
                if (not isinstance(rec, dict) or not isinstance(rec.get("path"), str)
                        or not os.path.isabs(rec["path"]) or not isinstance(rec.get("canonical"), str)
                        or not os.path.isabs(rec["canonical"]) or comparison_key(rec["canonical"]) in seen):
                    raise ValueError("invalid member path")
                seen.add(comparison_key(rec["canonical"]))
                if rec.get("missing") is False:
                    sha = rec.get("sha256", "")
                    if (not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha)
                            or type(rec.get("size")) is not int or rec["size"] < 0
                            or not isinstance(rec.get("identity"), list) or len(rec["identity"]) != 5
                            or not all(type(value) is int for value in rec["identity"])):
                        raise ValueError("invalid blob identity")
                elif rec.get("missing") is not True:
                    raise ValueError("missing member status")
        return manifest

    def restore(self, transaction, destination=None, selected=(), in_place=False, deadline=None):
        """Restore complete logical groups, with immutable progress events.

        Previously absent destinations use no-clobber publication in both modes.
        In-place replacement preserves and revalidates complete current groups,
        including groups without any existing member; callers must quiesce native writers.
        Multi-file publication is journalled, not claimed to be atomic.
        """
        if in_place and destination is not None:
            raise ValueError("choose in-place or a destination")
        manifest = self.inspect(transaction)
        wanted = {canonical(path) for path in selected}
        groups = [g for g in manifest["groups"] if not wanted or
                  any(comparison_key(rec["canonical"]) in wanted for rec in g["members"])]
        matched = {comparison_key(rec["canonical"]) for g in groups for rec in g["members"]}
        if wanted - matched:
            raise ValueError("selected member is not in the transaction")
        self.verify(groups, deadline)
        operation = uuid.uuid4().hex
        journal = self.root / "restores" / operation
        base = Path(destination) if destination else self.root / "restored" / operation
        current = [_state([rec["path"] for rec in group["members"]]) for group in groups] if in_place else []
        initially_present = {path for state in current for path, _real, ident in state if ident is not None}
        targets, missing_targets = [], []
        for gi, group in enumerate(groups):
            for rec in group["members"]:
                # Separate groups avoid collapsing equal basenames from distinct roots.
                target = Path(rec["path"]) if in_place else base / str(gi) / Path(rec["path"]).name
                if rec["missing"]:
                    if os.path.lexists(target):
                        raise PreservationFailed("unexpected counterpart at restore destination: {}".format(target))
                    missing_targets.append(target)
                else:
                    if not in_place and os.path.lexists(target):
                        raise FileExistsError(str(target))
                    targets.append((rec, target))
        before = None
        if in_place:
            before = self.backup([[r["path"] for r in g["members"]] for g in groups],
                                 {"operation": "pre-restore", "restoring": transaction}, deadline)
        journal.mkdir(parents=True, exist_ok=False)
        durable.atomic_json(journal / "0000-plan.json", {
            "transaction": transaction, "prebackup": before["id"] if before else None,
            "targets": [str(target) for _, target in targets], "in_place": in_place,
            "initially_missing": [path for state in current for path, _real, ident in state if ident is None],
            "missing_targets": [str(target) for target in missing_targets]})
        candidates, published = [], []
        try:
            for rec, target in targets:
                target.parent.mkdir(parents=True, exist_ok=True)
                part = target.with_name(".restore-" + operation + "-" + uuid.uuid4().hex)
                candidates.append((part, target))
                info = durable.copy_verified(self.root / "blobs" / rec["sha256"], part, deadline)
                if info["sha256"] != rec["sha256"]:
                    raise PreservationFailed("restore candidate hash mismatch")
            if before:
                self.revalidate(before["groups"], deadline)
            if current != [_state([path for path, _real, _ident in state]) for state in current]:
                raise PreservationFailed("restore destination membership or identity changed during staging")
            for index, (part, target) in enumerate(candidates, 1):
                durable.check_deadline(deadline)
                if in_place and str(target.absolute()) in initially_present:
                    os.replace(part, target)
                    durable.sync_dir(target.parent)
                else:
                    durable.publish_new(part, target)
                published.append(str(target))
                durable.atomic_json(journal / ("{:04d}-published.json".format(index)), {"path": str(target)})
            for target in missing_targets:
                if os.path.lexists(target):
                    raise PreservationFailed("unexpected counterpart appeared during restore: {}".format(target))
            durable.atomic_json(journal / "complete.json", {"published": published})
            return {"operation": operation, "destination": str(base) if not in_place else None,
                    "published": published, "journal": str(journal)}
        except OSError as exc:
            # Reporting a partial write is required even if writing its failure event
            # also fails. The plan and earlier progress events remain recoverable.
            try:
                durable.atomic_json(journal / "failed.json", {"published": published, "error": str(exc)})
            except OSError:
                pass
            raise PreservationFailed("restore failed; published={!r}; journal={}: {}".format(
                published, journal, exc)) from exc
        finally:
            for part, _ in candidates:
                part.unlink(missing_ok=True)
