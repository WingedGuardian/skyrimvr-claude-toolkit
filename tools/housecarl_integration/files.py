"""Verified preservation and publication. Backups are never pruned."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import uuid

from . import Refusal


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def contained(path, root):
    return Path(path).resolve().is_relative_to(Path(root).resolve())


def plain_plugin(name):
    if not isinstance(name, str) or not name or name != name.strip() or any(
            c in name for c in '/\\:<>"|?*') or any(ord(c) < 32 for c in name):
        raise Refusal("Name one plugin by basename, without a path or wildcard.")
    if Path(name).suffix.lower() not in (".esp", ".esm", ".esl"):
        raise Refusal("Expected an .esp/.esm/.esl plugin filename.")
    return name


def json_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part-" + uuid.uuid4().hex)
    try:
        with part.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)


@contextlib.contextmanager
def exclusive(path):
    """An OS lock survives neither process exit nor crash; no stale lock deletion."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0)
        stream.write(b"0")
        stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise Refusal("Another House Carl transaction is active; retry after it completes.") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def backup(path, store):
    path, store = Path(path), Path(store)
    if path.is_symlink() or not path.is_file():
        raise Refusal("Backup target must be an existing regular file: " + str(path))
    ident = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex
    folder = store / ident
    folder.mkdir(parents=True)
    before = sha(path)
    dest = folder / path.name
    shutil.copyfile(path, dest)
    if sha(dest) != before or sha(path) != before:
        raise Refusal("Backup hash verification failed; publication is prohibited.")
    record = {"id": ident, "source": str(path.resolve()), "backup": str(dest.resolve()),
              "sha256": before, "size": dest.stat().st_size, "created": time.time()}
    json_write(folder / "manifest.json", record)
    return record


def publish(candidate, target, expected):
    """Replace the deployed directory entry, never write through a manager hardlink."""
    target, candidate = Path(target), Path(candidate)
    if target.is_symlink() or target.parent.is_symlink():
        raise Refusal("Publication through a symbolic link is unsupported.")
    current = sha(target) if target.exists() else None
    if current != expected:
        raise Refusal("Target changed after preparation; no file was published.")
    digest = sha(candidate)
    part = target.with_name(target.name + ".housecarl-" + uuid.uuid4().hex)
    try:
        with candidate.open("rb") as src, part.open("xb") as dst:
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
        if sha(part) != digest:
            raise Refusal("Staged publication hash mismatch; target untouched.")
        if (sha(target) if target.exists() else None) != expected:
            raise Refusal("Target changed before commit; target untouched.")
        os.replace(part, target)
        if sha(target) != digest:
            raise Refusal("POST-COMMIT verification failed; preserve the receipt and backup for recovery.")
        return digest
    finally:
        part.unlink(missing_ok=True)
