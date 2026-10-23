"""Bounded I/O and durable publication shared by preservation and installers."""
from __future__ import annotations

import errno
import hashlib
import os
import time
import uuid
from pathlib import Path

CHUNK = 1024 * 1024


def check_deadline(deadline=None):
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("preservation deadline reached")


def write_all(fd, data):
    view = memoryview(data)
    while view:
        try:
            count = os.write(fd, view)
        except InterruptedError:
            continue
        if count <= 0:
            raise OSError("write made no progress")
        view = view[count:]


def sync_dir(path):
    """fsync directories on supported POSIX filesystems; Windows has no CRT equivalent.

    Other I/O errors propagate. File data is always fsynced on both platforms.
    """
    if os.name == "nt":
        return False
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        try:
            os.fsync(fd)
        except OSError as exc:
            if exc.errno in (errno.EINVAL, errno.ENOTSUP):
                return False
            raise
    finally:
        os.close(fd)
    return True


def digest(path, deadline=None):
    value = hashlib.sha256()
    with open(path, "rb") as stream:
        while True:
            check_deadline(deadline)
            block = stream.read(CHUNK)
            if not block:
                return value.hexdigest()
            value.update(block)


def identity(path):
    st = os.stat(path)
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def copy_verified(source, target, deadline=None):
    """Create a new owned candidate, hash its readback and re-hash its source.

    Callers must additionally revalidate the entire logical save after copying all
    members. Ordinary filesystems cannot provide an atomic multi-file snapshot.
    """
    before = identity(source)
    h = hashlib.sha256()
    count = 0
    with open(source, "rb") as incoming, open(target, "xb") as outgoing:
        while True:
            check_deadline(deadline)
            block = incoming.read(CHUNK)
            if not block:
                break
            outgoing.write(block)
            count += len(block)
            h.update(block)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    value = h.hexdigest()
    if count != before[2] or digest(target, deadline) != value:
        raise OSError("stored copy failed size/hash verification")
    if identity(source) != before or digest(source, deadline) != value or identity(source) != before:
        raise OSError("source changed during preservation")
    return {"sha256": value, "size": count, "identity": list(before)}


def publish_new(candidate, target):
    """Publish without replacing an existing destination; never claim a placeholder.

    Unsupported hardlinks fail safely on POSIX. Windows rename is no-clobber.
    """
    candidate, target = Path(candidate), Path(target)
    if os.name == "nt":
        os.rename(candidate, target)
    else:
        os.link(candidate, target)
        candidate.unlink()
    sync_dir(target.parent)


def atomic_json(path, data):
    import json
    path = Path(path)
    part = path.with_name(".part-" + uuid.uuid4().hex)
    encoded = (json.dumps(data, ensure_ascii=True, sort_keys=True, allow_nan=False) + "\n").encode("ascii")
    try:
        with open(part, "xb", buffering=0) as stream:
            write_all(stream.fileno(), encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if part.stat().st_size != len(encoded) or digest(part) != hashlib.sha256(encoded).hexdigest():
            raise OSError("metadata staging failed size/hash verification")
        publish_new(part, path)
    finally:
        part.unlink(missing_ok=True)
