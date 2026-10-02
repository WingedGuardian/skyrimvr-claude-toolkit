"""Validate and transactionally publish Spriggit output; no binary-format editing."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
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


def check_paths(input_path, output_path):
    source = Path(input_path).resolve()
    target = Path(output_path)
    if target.is_symlink():
        raise ValueError("linked destination")
    target = target.resolve()
    if target == Path(target.anchor) or source == target or source in target.parents or target in source.parents:
        raise ValueError("input/output overlap or root destination")


def publish(source, target):
    source, target = Path(source), Path(target)
    validate(source)
    if target.is_symlink():
        raise ValueError("linked destination")
    target = target.resolve()
    if target == Path(target.anchor):
        raise ValueError("root destination")
    if target.exists() and source.is_dir() != target.is_dir():
        raise ValueError("output/destination artifact types differ")
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.parent / (".spriggit-lock-" + hashlib.sha256(
        os.path.normcase(str(target)).encode("utf-8")).hexdigest()[:16])
    try:
        lock.mkdir()
    except FileExistsError:
        raise ValueError("publication already in progress; inspect " + str(lock))
    try:
        _publish_locked(source, target)
    finally:
        try:
            lock.rmdir()
        except OSError as error:
            print("[spriggit-cli] cleanup warning; inspect retained lock " + str(lock) + ": " + str(error), file=sys.stderr)


def _publish_locked(source, target):
    holder = Path(tempfile.mkdtemp(prefix=".spriggit-stage-", dir=target.parent))
    stage = holder / target.name
    backup = target.parent / (".spriggit-previous-" + uuid.uuid4().hex)
    moved = False
    try:
        if source.is_dir():
            shutil.copytree(source, stage)
        else:
            shutil.copy2(source, stage)
        validate(stage)
        if target.exists():
            os.replace(target, backup)
            moved = True
        try:
            os.replace(stage, target)
        except BaseException:
            if moved:
                os.replace(backup, target)
                moved = False
            raise
        if moved:
            try:
                if backup.is_dir():
                    shutil.rmtree(backup)
                else:
                    backup.unlink()
            except OSError as error:
                print("[spriggit-cli] cleanup warning; publication committed, retained " +
                      str(backup) + ": " + str(error), file=sys.stderr)
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
