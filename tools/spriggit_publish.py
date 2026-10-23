"""Validate and transactionally publish Spriggit output; no binary-format editing."""
import argparse
import ctypes
import errno
import hashlib
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import uuid


def validate(source):
    source = Path(source)
    if source.is_symlink() or not source.exists():
        raise ValueError("missing or linked output")
    if source.is_dir():
        entries = list(source.rglob("*"))
        if any(p.is_symlink() for p in entries):
            raise ValueError("linked output artifact")
        if not any(p.is_file() and p.suffix.lower() == ".yaml" and p.stat().st_size
                   for p in entries):
            raise ValueError("no nonempty YAML artifacts")
    elif not source.is_file() or not source.stat().st_size:
        raise ValueError("empty output artifact")


# What a real `spriggit serialize` writes (measured 2026-10-02 on Spriggit.CLI 0.40.0, re-measured 2026-10-05 on 0.41.0):
# top-level spriggit-meta.json and RecordData.yaml, plus record-group directories (Weapons/, ...)
# holding only record files. Anything else in a destination directory -- .git, notes, a README --
# was put there by someone else, and replacing the directory would delete it.
RECORD_SUFFIXES = (".yaml", ".yml", ".json")


def _spriggit_shaped(entry):
    if entry.name.startswith(".") or entry.is_symlink():
        return False
    if entry.is_file():
        return entry.suffix.lower() in RECORD_SUFFIXES
    if entry.is_dir():
        return all(_spriggit_shaped(child) for child in entry.iterdir())
    return False


def foreign_entries(target):
    """Top-level entries of an existing destination directory Spriggit did not produce."""
    target = Path(target)
    if not target.is_dir() or target.is_symlink():
        return []
    return sorted(entry.name for entry in target.iterdir() if not _spriggit_shaped(entry))


def refuse_foreign(target):
    foreign = foreign_entries(target)
    if foreign:
        raise ValueError("refusing to replace " + str(target) + ": it holds entries Spriggit did not produce ("
                         + ", ".join(foreign[:5]) + (", ..." if len(foreign) > 5 else "")
                         + "); serialize into an empty or Spriggit-only directory")


def _key(target):
    return hashlib.sha256(os.path.normcase(str(target)).encode("utf-8")).hexdigest()[:16]


def _rename_new(source, target):
    """Rename without replacing even an empty directory created by another writer."""
    if os.name == "nt":
        os.rename(source, target)  # Windows rename refuses an existing target.
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        rename = getattr(libc, "renameat2", None)
        args = (-100, os.fsencode(source), -100, os.fsencode(target), 1)  # AT_FDCWD, RENAME_NOREPLACE
        types = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    elif sys.platform == "darwin":
        rename = getattr(libc, "renamex_np", None)
        args = (os.fsencode(source), os.fsencode(target), 4)  # RENAME_EXCL
        types = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    else:
        rename = None
    if rename is None:
        raise OSError(errno.ENOTSUP, "exclusive publication rename unavailable", str(target))
    rename.argtypes, rename.restype = types, ctypes.c_int
    if rename(*args) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(target))


def _state(target):
    """Identity and bytes of a regular artifact tree; a changing read is not evidence."""
    target = Path(target)
    try:
        root = target.lstat()
    except FileNotFoundError:
        return None
    rows = []

    def visit(path, before):
        if stat.S_ISLNK(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400:
            raise ValueError("linked destination artifact: " + str(path))
        digest = None
        if stat.S_ISREG(before.st_mode):
            h = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(block)
            digest = h.hexdigest()
        elif stat.S_ISDIR(before.st_mode):
            for child in sorted(path.iterdir()):
                visit(child, child.lstat())
        else:
            raise ValueError("nonregular destination artifact: " + str(path))
        after = path.lstat()
        fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in fields):
            raise ValueError("destination changed while reading: " + str(path))
        # Renaming changes ctime on POSIX; identities, names, mtimes and content must agree.
        rows.append((path.relative_to(target).as_posix(), before.st_dev, before.st_ino,
                     before.st_mode, before.st_size, before.st_mtime_ns, digest))

    visit(target, root)
    return tuple(rows)


def check_paths(input_path, output_path):
    source = Path(input_path).resolve()
    target = Path(output_path)
    if target.is_symlink():
        raise ValueError("linked destination")
    target = target.resolve()
    if target == Path(target.anchor) or source == target or source in target.parents or target in source.parents:
        raise ValueError("input/output overlap or root destination")
    # Publication renames beside the destination, so its parent must be writable. Check BEFORE
    # serializing: in the dev container /workspace is a read-only bind, and failing only after a
    # long serialize run (with EROFS) hides the real cause.
    existing = target.parent
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    if not os.access(existing, os.W_OK):
        raise ValueError("destination is not writable: " + str(existing) +
                         " (in the dev container /workspace is read-only; write to /tmp and copy out)")
    refuse_foreign(target)


def publish(source, target):
    source, target = Path(source), Path(target)
    validate(source)
    _state(target)  # Reject linked/reparse destinations before resolving their paths.
    target = target.resolve()
    if target == Path(target.anchor):
        raise ValueError("root destination")
    if target.exists() and source.is_dir() != target.is_dir():
        raise ValueError("output/destination artifact types differ")
    target.parent.mkdir(parents=True, exist_ok=True)
    key = _key(target)
    lock = target.parent / (".spriggit-lock-" + key)
    try:
        lock.mkdir()
    except FileExistsError:
        debris = sorted(str(p) for pattern in (".spriggit-previous-" + key + "-*", ".spriggit-stage-" + key + "-*")
                        for p in target.parent.glob(pattern))
        raise ValueError("publication already in progress or crashed; inspect lock " + str(lock)
                         + ("; crash debris (a previous-* entry holds the OLD output): " + ", ".join(debris)
                            if debris else "; no crash debris found"))
    try:
        refuse_foreign(target)
        _publish_locked(source, target, key)
    finally:
        try:
            lock.rmdir()
        except OSError as error:
            print("[spriggit-cli] cleanup warning; inspect retained lock " + str(lock) + ": " + str(error), file=sys.stderr)


def _publish_locked(source, target, key):
    observed = _state(target)
    holder = Path(tempfile.mkdtemp(prefix=".spriggit-stage-" + key + "-", dir=target.parent))
    stage = holder / target.name
    backup = target.parent / (".spriggit-previous-" + key + "-" + uuid.uuid4().hex)
    moved = False
    try:
        if source.is_dir():
            shutil.copytree(source, stage)
        else:
            shutil.copy2(source, stage)
        validate(stage)
        if _state(target) != observed:
            raise ValueError("destination changed during staging; publication refused")
        if observed is not None:
            _rename_new(target, backup)
            moved = True
        try:
            if moved and _state(backup) != observed:
                raise ValueError("destination changed before publication; restoring previous output")
            _rename_new(stage, target)
        except BaseException as error:
            if moved:
                try:
                    _rename_new(backup, target)
                except OSError as recovery:
                    raise ValueError("publication failed; rollback could not replace a new destination; previous output retained at "
                                     + str(backup) + ": " + str(recovery)) from error
                moved = False
            raise
        if moved:
            # A writer may still hold an old file or directory handle. Never delete
            # its bytes by cleaning up this previous destination after publication.
            print("[spriggit-cli] publication committed; previous output retained at " + str(backup), file=sys.stderr)
    finally:
        try:
            shutil.rmtree(holder)
        except OSError as error:
            print("[spriggit-cli] cleanup warning; retained staging " + str(holder) + ": " + str(error), file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("check", "publish"))
    parser.add_argument("source")
    parser.add_argument("target")
    args = parser.parse_args()
    try:
        (check_paths if args.action == "check" else publish)(args.source, args.target)
    except (ValueError, OSError) as error:
        parser.exit(1, "[spriggit-cli] publication failed: " + str(error) + "\n")
