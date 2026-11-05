#!/usr/bin/env python3
"""Put selected configuration and mutable database files back exactly after a live test.

Contract:
- prepare needs a cold game and takes ownership first; another owner, a running process, a file some process holds open,
  a link, a save in scope, or an incomplete earlier run all refuse before anything changes.
- One context per game: a small marker in the game folder holds that, whichever state folder a caller names.
- Unchanged bytes are stored once, by hash, outside the game folder, however many runs use them. No staging copy, no links.
- The manifest is durable before the first live change, so every later failure can be undone by `restore`.
- restore keeps what the test changed or created as evidence, puts back original bytes, times, attributes, names and folders,
  touches only the declared roots, its own additions and marker, and keeps ownership until a full re-hash passes. It can
  always run again, and once a restore has verified, a later run only finishes the cleanup: it never restores twice.
- This never starts or stops a game. Launch approval belongs to the caller and is never carried by a prepared context.

Saves are not handled here: pair protection has its own route. Not a security boundary against a writer ignoring the slots.
"""
import argparse
import ctypes
import hashlib
import json
import msvcrt
import os
import shutil
import stat
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

ACTIVE, MANIFEST, MANIFEST_HASH, RECEIPT = "active.json", "manifest.json", "manifest.sha256", "receipt.json"
PREPARE_RECEIPT = "prepare-receipt.json"
GAME_MARKER = ".test-context-active.json"
PROFILE_KEYS = {"schema", "roots", "edits", "additions", "cold_processes", "slots"}
SLOT_KEYS = {"file", "keys"}
SAVE_SUFFIXES = {".ess", ".skse"}
DIFFERENCES = ("changed", "missing", "created", "times", "attributes", "renamed", "missing_folders", "created_folders")
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
GENERIC_READ, OPEN_EXISTING, INVALID_HANDLE = 0x80000000, 3, ctypes.c_void_p(-1).value


class Refused(Exception):
    """A precondition failed; nothing was changed by this call unless the message says so."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_durable(source: Path, target: Path) -> None:
    """On disk when this returns: a verified copy may be the only one left after a power loss."""
    with open(source, "rb") as reader, open(target, "wb") as writer:
        shutil.copyfileobj(reader, writer, 1 << 20)
        writer.flush()
        os.fsync(writer.fileno())


def write_durable(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def write_json(path: Path, value) -> None:
    write_durable(path, (json.dumps(value, indent=1, sort_keys=True) + "\n").encode("utf-8"))


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def create_whole(path: Path, value, refusal: str) -> None:
    """Appears complete or not at all, and never over an existing file: rename refuses an existing target on Windows."""
    temporary = path.with_name("{}.{}.tmp".format(path.name, uuid.uuid4().hex))
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=1, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.rename(temporary, path)
    except FileExistsError as error:
        raise Refused("{}: {}".format(refusal, path)) from error
    finally:
        if temporary.exists():
            temporary.unlink()


def same_path(a, b) -> bool:
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def running_processes(names) -> list:
    """Exact image names now running. Not knowing is a refusal, never an empty answer."""
    try:
        done = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise Refused("cannot tell which processes are running: {}".format(error)) from error
    if done.returncode or not done.stdout.strip():
        raise Refused("cannot tell which processes are running: tasklist exit {}".format(done.returncode))
    wanted = {name.lower() for name in names}
    seen = {line.split('","')[0].strip('"').lower() for line in done.stdout.splitlines() if line.strip()}
    return sorted(wanted & seen)


def require_cold(names) -> None:
    alive = running_processes(names)
    if alive:
        raise Refused("still running: " + ", ".join(alive))


def require_unheld(path: Path) -> None:
    """Asking for no sharing at all fails if any process has the file open, which is what an active writer looks like."""
    create = ctypes.windll.kernel32.CreateFileW
    create.restype = ctypes.c_void_p
    create.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    handle = create(str(path), GENERIC_READ, 0, None, OPEN_EXISTING, 0, None)
    if handle == INVALID_HANDLE or handle is None:
        raise Refused("{} is in use or unreadable".format(path))
    close = ctypes.windll.kernel32.CloseHandle
    close.argtypes = [ctypes.c_void_p]
    close(handle)


def inside(game: Path, relative: str) -> Path:
    pure = PurePosixPath(relative.replace("\\", "/"))
    if not relative or pure.is_absolute() or ":" in relative or ".." in pure.parts:
        raise Refused("{} must stay inside the game folder".format(relative))
    return game / Path(*pure.parts)


def is_link(path: Path) -> bool:
    return bool(os.lstat(path).st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def require_plain_ancestors(game: Path, path: Path) -> None:
    current = path
    while current != game:
        if os.path.lexists(current) and is_link(current):
            raise Refused("{} is a link".format(current))
        current = current.parent


def walk(game: Path, roots) -> tuple:
    """(files, folders) as sorted game-relative paths; any link stops the whole thing. Absent roots add nothing, and a root
    that a test turned into a file is listed as a file, so restore can see it."""
    files, folders = [], []
    stack = [inside(game, root) for root in roots]
    while stack:
        current = stack.pop()
        require_plain_ancestors(game, current)
        if not os.path.lexists(current):
            continue
        if not current.is_dir():
            files.append(current.relative_to(game).as_posix())
            continue
        folders.append(current.relative_to(game).as_posix())
        with os.scandir(current) as entries:
            for entry in entries:
                path = Path(entry.path)
                if is_link(path):
                    raise Refused("{} is a link".format(path))
                if entry.is_dir(follow_symlinks=False):
                    stack.append(path)
                elif os.lstat(path).st_nlink != 1:
                    raise Refused("{} has another hard link".format(path))
                else:
                    files.append(path.relative_to(game).as_posix())
    return sorted(files), sorted(folders)


def reject_saves(files) -> None:
    for relative in files:
        if PurePosixPath(relative).suffix.lower() in SAVE_SUFFIXES:
            raise Refused("{} is a save: pair protection has its own route, so a root may not hold one".format(relative))


# --- ownership -----------------------------------------------------------------------------------------------------------
@contextmanager
def slot_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise Refused("the ownership file is locked by someone else") from error
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def slot_bytes(raw: bytes, slots, owner: str, release: bool) -> bytes:
    """Byte-level so every other field stays exactly as written. Never steals. Taking needs an empty slot, so a slot this
    context did not take is never its to release. Releasing accepts an empty one: a crash around it must not strand recovery."""
    value = json.loads(raw)
    allowed = (None, owner) if release else (None,)
    if any(key not in value or value[key] not in allowed for key in slots["keys"]):
        raise Refused("the runtime is held by another owner, or the ownership file lacks a slot")
    updated = raw
    for key in slots["keys"]:
        needle = '"{}": {}'.format(key, json.dumps(value[key])).encode("utf-8")
        if updated.count(needle) != 1:
            raise Refused("unexpected ownership file layout at {}".format(key))
        updated = updated.replace(needle, '"{}": {}'.format(key, json.dumps(None if release else owner)).encode("utf-8"))
    return updated


def change_slots(slots, owner: str, release: bool = False) -> None:
    if not slots:
        return
    path = Path(slots["file"])
    # The lock every participant in this ownership file already uses; a different one would exclude nobody.
    with slot_lock(path.with_suffix(".gateway.lock")):
        raw = path.read_bytes()
        updated = slot_bytes(raw, slots, owner, release)
        if updated != raw:
            write_durable(path, updated)


def status(state: Path):
    active = Path(state) / ACTIVE
    if not active.is_file():
        return None
    try:
        record = read_json(active)
        if {"owner", "run", "phase", "slots", "game", "store"} - set(record):
            raise ValueError("fields missing")
        return record
    except (ValueError, TypeError) as error:
        raise Refused("the active record {} is unreadable: nothing changes until it is looked at".format(active)) from error


def read_marker(game: Path):
    marker = game / GAME_MARKER
    if not os.path.lexists(marker):
        return None
    try:
        record = read_json(marker)
        if {"owner", "run", "state"} - set(record):
            raise ValueError("fields missing")
        return record
    except (ValueError, TypeError) as error:
        raise Refused("the game marker {} is unreadable: nothing changes until it is looked at".format(marker)) from error


def begin(game: Path, state: Path, store: Path, owner: str, run: str, slots) -> None:
    state.mkdir(parents=True, exist_ok=True)
    active, marker = state / ACTIVE, game / GAME_MARKER
    record = {"owner": owner, "run": run, "phase": "preparing", "slots": slots, "game": str(game), "store": str(store)}
    create_whole(active, record, "a context is already active: restore it first")
    try:
        create_whole(marker, {"owner": owner, "run": run, "state": str(state)}, "a context is already active on this game: restore it first")
        change_slots(slots, owner)
    except Refused:
        # A refusal means the slots were not written. Anything else may have happened after the write, so the records stay
        # and `restore` finishes it: it releases what this owner holds and never what it does not.
        current = read_marker(game)
        if current and same_path(current["state"], state):
            marker.unlink()
        active.unlink()
        raise


def set_phase(state: Path, phase: str) -> None:
    record = read_json(state / ACTIVE)
    record["phase"] = phase
    write_json(state / ACTIVE, record)


def release_clean(game: Path, state: Path, owner: str, slots) -> None:
    """Last step of any finish. The active record goes last, so a crash anywhere here leaves something `restore` can finish."""
    change_slots(slots, owner, release=True)
    current = read_marker(game)
    if current and same_path(current["state"], state):
        (game / GAME_MARKER).unlink()
    for stray in game.glob(GAME_MARKER + ".*.tmp"):     # left if a process died while creating the marker
        stray.unlink()
    (state / ACTIVE).unlink()


# --- capture ------------------------------------------------------------------------------------------------------------
def store_blob(store: Path, source: Path, digest: str) -> None:
    """Once per distinct content. An existing blob is re-hashed: a damaged reference must not be trusted as a backup."""
    blob = store / digest
    if blob.exists():
        if sha256(blob) != digest:
            raise Refused("reference {} is damaged".format(digest))
        return
    temporary = store / (digest + ".incoming")
    if temporary.exists():
        os.chmod(temporary, stat.S_IWRITE)      # left by an interrupted capture; only ever replaced after a hash check
    copy_durable(source, temporary)
    if sha256(temporary) != digest:
        temporary.unlink()
        raise Refused("{} changed while it was being captured".format(source))
    os.chmod(temporary, stat.S_IREAD)
    os.replace(temporary, blob)


def capture(game: Path, store: Path, roots) -> dict:
    files, folders = walk(game, roots)
    reject_saves(files)
    store.mkdir(parents=True, exist_ok=True)
    rows = {}
    for relative in files:
        path = game / relative
        require_unheld(path)
        digest = sha256(path)
        store_blob(store, path, digest)
        info = os.lstat(path)
        if sha256(path) != digest:
            raise Refused("{} changed while it was being captured".format(path))
        rows[relative] = {"sha256": digest, "bytes": info.st_size, "mtime_ns": info.st_mtime_ns, "read_only": not info.st_mode & stat.S_IWRITE}
    return {"files": rows, "folders": folders}


def check_profile(profile) -> None:
    unknown = set(profile) - PROFILE_KEYS
    if unknown:
        raise Refused("unknown profile key: " + ", ".join(sorted(unknown)))
    if profile.get("schema") != 1:
        raise Refused("profile schema must be 1")
    if not profile.get("roots"):
        raise Refused("a profile must name its roots")
    if not profile.get("cold_processes"):
        raise Refused("a profile must name cold_processes: without them a running game cannot be seen")
    slots = profile.get("slots")
    if slots is not None:
        unknown = set(slots) - SLOT_KEYS
        if unknown:
            raise Refused("unknown slots key: " + ", ".join(sorted(unknown)))
        if not slots.get("file") or not slots.get("keys"):
            raise Refused("slots must name a file and at least one of its keys")


def replace_verified(blob_or_source: Path, target: Path, digest: str, mtime_ns=None, read_only=False) -> None:
    """Atomic: the target is the old bytes or the verified new bytes, never a partial write."""
    temporary = target.with_name(target.name + ".context-incoming")
    if os.path.lexists(temporary):
        os.chmod(temporary, stat.S_IWRITE)      # left by an interrupted restore
    copy_durable(blob_or_source, temporary)
    os.chmod(temporary, stat.S_IWRITE)
    if sha256(temporary) != digest:
        temporary.unlink()
        raise Refused("{} does not hash to its reference".format(blob_or_source))
    if mtime_ns is not None:
        os.utime(temporary, ns=(mtime_ns, mtime_ns))
    if read_only:
        os.chmod(temporary, stat.S_IREAD)
    if os.path.lexists(target):
        os.chmod(target, stat.S_IWRITE)         # Windows refuses to replace a read-only file
    os.replace(temporary, target)


def planned_edits(game: Path, profile, captured) -> list:
    """All checked before any is applied; several edits to one file apply in order."""
    contents, order = {}, []
    for edit in profile.get("edits", []):
        relative = inside(game, edit["path"]).relative_to(game).as_posix()
        if relative not in captured["files"]:
            raise Refused("{} is outside the captured roots, so it could not be restored".format(relative))
        data = contents.get(relative)
        if data is None:
            data = (game / relative).read_bytes()
            order.append(relative)
        old, new = edit["old"].encode("utf-8"), edit["new"].encode("utf-8")
        if data.count(old) != 1:
            raise Refused("edit for {} must match exactly once: {!r}".format(relative, edit["old"]))
        contents[relative] = data.replace(old, new)
    return [(relative, contents[relative]) for relative in order]


def planned_additions(game: Path, profile) -> list:
    rows = []
    for addition in profile.get("additions", []):
        target = inside(game, addition["target"])
        require_plain_ancestors(game, target)
        reject_saves([addition["target"]])
        source = Path(addition["source"])
        if os.path.lexists(target):
            raise Refused("{} exists: an addition never replaces a file".format(target))
        if not source.is_file() or sha256(source) != addition["sha256"]:
            raise Refused("addition {} does not match its declared hash".format(source))
        missing, current = [], target.parent
        while current != game and not current.exists():
            missing.append(current.relative_to(game).as_posix())
            current = current.parent
        rows.append({"source": str(source), "sha256": addition["sha256"], "target": target.relative_to(game).as_posix(), "created_folders": missing})
    return rows


def check_request(game: Path, state: Path, store: Path, owner: str, run: str, profile: dict) -> None:
    if not owner:
        raise Refused("a context must name its owner")
    for label, path in (("state", state), ("store", store)):
        if path == game or game in path.parents:
            raise Refused("the {} folder must be outside the game folder".format(label))
    check_profile(profile)
    for root in profile["roots"]:
        folder = inside(game, root)
        if len(folder.relative_to(game).parts) < 2:
            raise Refused("root {} is the whole game or its Data folder: name the folders a test really changes".format(root))
        if not folder.is_dir():
            raise Refused("root {} is not a folder".format(root))
    if (state / run).exists():
        raise Refused("run {} already exists: run names are never reused".format(run))


def check(game, state, store, owner: str, run: str, profile: dict) -> dict:
    """Everything prepare would refuse, without ownership, capture or any write. A pass says only that it would start now."""
    game, state, store = Path(game).resolve(), Path(state).resolve(), Path(store).resolve()
    check_request(game, state, store, owner, run, profile)
    require_cold(profile["cold_processes"])
    if status(state) is not None or read_marker(game) is not None:
        raise Refused("a context is already active: restore it first")
    slots = profile.get("slots")
    if slots:
        slot_bytes(Path(slots["file"]).read_bytes(), slots, owner, False)
    files, _ = walk(game, profile["roots"])
    reject_saves(files)
    for relative in files:
        require_unheld(game / relative)
    edits = planned_edits(game, profile, {"files": dict.fromkeys(files)})
    additions = planned_additions(game, profile)
    return {"files": len(files), "bytes": sum(os.lstat(game / relative).st_size for relative in files),
            "edits": [relative for relative, _ in edits], "additions": [row["target"] for row in additions], "would_start": True}


def prepare(game, state, store, owner: str, run: str, profile: dict) -> dict:
    game, state, store = Path(game).resolve(), Path(state).resolve(), Path(store).resolve()
    check_request(game, state, store, owner, run, profile)
    if status(state) is not None:
        raise Refused("a context is already active: restore it first")
    read_marker(game)       # an unreadable marker refuses here; a readable one refuses in begin
    slots = profile.get("slots")
    require_cold(profile["cold_processes"])
    begin(game, state, store, owner, run, slots)
    try:
        require_cold(profile["cold_processes"])    # after ownership, so a competing launch cannot slip between
        captured = capture(game, store, profile["roots"])
        edits = planned_edits(game, profile, captured)
        additions = planned_additions(game, profile)
    except Refused:
        release_clean(game, state, owner, slots)
        raise
    folder = state / run
    folder.mkdir(parents=True, exist_ok=False)
    manifest = {"schema": 1, "owner": owner, "run": run, "roots": profile["roots"], "cold_processes": profile["cold_processes"],
                "slots": slots, "additions": additions, "game": str(game), "store": str(store), **captured}
    write_json(folder / MANIFEST, manifest)
    write_durable(folder / MANIFEST_HASH, sha256(folder / MANIFEST).encode("ascii"))
    os.chmod(folder / MANIFEST, stat.S_IREAD)
    set_phase(state, "captured")
    try:
        for relative, data in edits:
            temporary = folder / "edit.tmp"
            write_durable(temporary, data)
            replace_verified(temporary, game / relative, sha256_bytes(data), read_only=captured["files"][relative]["read_only"])
            temporary.unlink()
        for row in additions:
            target = game / row["target"]
            target.parent.mkdir(parents=True, exist_ok=True)
            replace_verified(Path(row["source"]), target, row["sha256"])
    except Refused:
        restore(game, state, store, owner)
        raise
    set_phase(state, "prepared")
    write_json(folder / PREPARE_RECEIPT, {"owner": owner, "run": run, "manifest_sha256": sha256(folder / MANIFEST), "files": len(captured["files"]),
                                          "edits": [relative for relative, _ in edits], "additions": [row["target"] for row in additions]})
    return status(state)


def load_manifest(folder: Path) -> dict:
    path, recorded = folder / MANIFEST, folder / MANIFEST_HASH
    if not path.is_file() or not recorded.is_file() or recorded.read_text(encoding="ascii").strip() != sha256(path):
        raise Refused("the manifest does not match its recorded hash: not restoring from it")
    return read_json(path)


# --- restore ------------------------------------------------------------------------------------------------------------
def keep_evidence(source: Path, evidence: Path) -> None:
    """Copy, never move: until the restore replaces the live file, the only copy of the test's result stays where it was."""
    evidence.parent.mkdir(parents=True, exist_ok=True)
    target, n = evidence, 1
    digest = sha256(source)
    while target.exists():
        if target.is_file() and sha256(target) == digest:
            return
        target = evidence.with_name("{}.{}".format(evidence.name, n))
        n += 1
    copy_durable(source, target)
    if sha256(target) != digest:
        raise Refused("evidence copy of {} failed verification".format(source))


def remove_file(path: Path) -> None:
    os.chmod(path, stat.S_IWRITE)       # Windows refuses to delete a read-only file
    path.unlink()


def compare(game: Path, manifest: dict) -> dict:
    current, folders = walk(game, manifest["roots"])
    rows = manifest["files"]
    report = {name: [] for name in DIFFERENCES}
    for relative, row in sorted(rows.items()):
        path = game / relative
        if not path.is_file():
            report["missing"].append(relative)
        elif sha256(path) != row["sha256"]:
            report["changed"].append(relative)
        else:
            info = os.lstat(path)
            if info.st_mtime_ns != row["mtime_ns"]:
                report["times"].append(relative)
            if (not info.st_mode & stat.S_IWRITE) != row["read_only"]:
                report["attributes"].append(relative)
    by_lower = {relative.lower(): relative for relative in rows}
    for relative in current:
        if relative in rows:
            continue
        original = same_file_other_case(game, relative, by_lower)
        if original:
            report["renamed"].append(original)
        else:
            report["created"].append(relative)
    known_folders = {relative.lower() for relative in manifest["folders"]}
    report["missing_folders"] = [relative for relative in manifest["folders"] if not (game / relative).is_dir()]
    report["renamed"] += [relative for relative in manifest["folders"] if (game / relative).is_dir() and actual_name(game, relative) != relative]
    report["renamed"].sort()
    report["created_folders"] = [relative for relative in folders if relative.lower() not in known_folders]
    return report


def same_file_other_case(game: Path, relative: str, by_lower: dict):
    """The manifest's name for this file if it is one, spelled differently. The filesystem decides, not a folding rule of ours:
    a lookalike under Python's case rules can be a different file on NTFS, and must count as new."""
    original = by_lower.get(relative.lower())
    if original and os.path.exists(game / original) and os.path.samefile(game / relative, game / original):
        return original
    return None


def actual_name(game: Path, relative: str) -> str:
    """How the path is spelled on disk now."""
    current, parts = game, []
    for part in PurePosixPath(relative).parts:
        with os.scandir(current) as entries:
            names = [entry.name for entry in entries]
        match = next((name for name in names if name == part), None) or next((name for name in names if name.lower() == part.lower()), part)
        parts.append(match)
        current = current / match
    return "/".join(parts)


def put_name_back(game: Path, relative: str) -> None:
    """One path segment at a time: a renamed folder and a renamed file are the same repair."""
    current = game
    for wanted, found in zip(PurePosixPath(relative).parts, PurePosixPath(actual_name(game, relative)).parts):
        if wanted != found:
            os.rename(current / found, current / wanted)
        current = current / wanted


def require_this_game(game: Path, record: dict) -> None:
    if not same_path(game, record["game"]):
        raise Refused("this context belongs to game {}, not {}".format(record["game"], game))


def verify(game, state, record: bool = False) -> dict:
    """Read-only for the game. Differences are expected while a test is active: this says what a restore would have to do."""
    game, state = Path(game).resolve(), Path(state).resolve()
    active = status(state)
    if active is None:
        raise Refused("no active context")
    require_this_game(game, active)
    folder = state / active["run"]
    manifest = load_manifest(folder)
    report = compare(game, manifest)
    report["additions_present"] = [row["target"] for row in manifest["additions"] if os.path.lexists(game / row["target"])]
    if record:
        n = 1
        while (folder / "verify-{}.json".format(n)).exists():
            n += 1
        write_json(folder / "verify-{}.json".format(n), report)
    return report


def remove_empty_new_folders(game: Path, manifest: dict) -> None:
    """One that cannot go stays, and the comparison after restoring reports it: never a reason to stop putting files back."""
    known = {relative.lower() for relative in manifest["folders"]}
    candidates = []
    for root in manifest["roots"]:
        base = inside(game, root)
        if base.is_dir():
            candidates += [p for p in base.rglob("*") if p.is_dir() and p.relative_to(game).as_posix().lower() not in known]
    for path in sorted(candidates, key=lambda p: len(p.parts), reverse=True):
        try:
            if not any(path.iterdir()):
                os.chmod(path, stat.S_IWRITE)
                path.rmdir()
        except OSError:
            pass


def require_slots_usable(slots, owner: str) -> None:
    """Before anything is restored: if someone else now holds the runtime, the live files may be theirs."""
    if slots:
        slot_bytes(Path(slots["file"]).read_bytes(), slots, owner, True)


def remove_addition(game: Path, row: dict, evidence: Path) -> None:
    path = game / row["target"]
    temporary = path.with_name(path.name + ".context-incoming")     # outside the roots a stray one would otherwise be unseen
    if os.path.lexists(temporary):
        remove_file(temporary)
    if path.is_dir():
        destination = evidence / "altered-additions" / row["target"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(destination))
    elif path.exists():
        if sha256(path) != row["sha256"]:
            keep_evidence(path, evidence / "altered-additions" / row["target"])
        remove_file(path)
    for created in row["created_folders"]:
        folder_path = game / created
        if folder_path.is_dir() and not any(folder_path.iterdir()):
            folder_path.rmdir()


def restore(game, state, store, owner: str) -> dict:
    game, state, store = Path(game).resolve(), Path(state).resolve(), Path(store).resolve()
    record = status(state)
    if record is None:
        raise Refused("no active context")
    if record["owner"] != owner:
        raise Refused("this context belongs to owner {}".format(record["owner"]))
    require_this_game(game, record)
    if not same_path(store, record["store"]):
        raise Refused("this context keeps its references in store {}, not {}".format(record["store"], store))
    marker = read_marker(game)
    if marker and not same_path(marker["state"], state):
        raise Refused("the game's active marker names another state folder: {}".format(marker["state"]))
    phase, run, slots = record["phase"], record["run"], record["slots"]
    require_slots_usable(slots, owner)
    if phase == "preparing":
        # Stopped before the phase moved to captured, and nothing live changes before it does.
        release_clean(game, state, owner, slots)
        return {"phase_at_restore": phase, "verified": True, "live_changes": 0}
    folder = state / run
    manifest = load_manifest(folder)
    finished = read_json(folder / RECEIPT) if (folder / RECEIPT).is_file() else None
    if finished and finished["verified"]:
        # It verified and stopped before the cleanup. What is live now may be later work: finish, never restore twice.
        release_clean(game, state, owner, slots)
        return finished
    require_cold(manifest["cold_processes"])
    current, _ = walk(game, manifest["roots"])
    for relative in current:
        require_unheld(game / relative)
    for row in manifest["additions"]:
        require_plain_ancestors(game, game / row["target"])
        if (game / row["target"]).is_file():
            require_unheld(game / row["target"])
    set_phase(state, "restoring")
    before = compare(game, manifest)
    before["altered_additions"] = [row["target"] for row in manifest["additions"] if os.path.lexists(game / row["target"])
                                   and (not (game / row["target"]).is_file() or sha256(game / row["target"]) != row["sha256"])]
    attempt = 1
    while (folder / "restore-attempt-{}.json".format(attempt)).exists():
        attempt += 1
    write_json(folder / "restore-attempt-{}.json".format(attempt), before)     # a later run must not forget what this one saw
    failed = []
    evidence = folder / "evidence"
    # New things first: one of them may sit where an original folder or file has to go back.
    for relative in before["created"]:
        path = game / relative
        try:
            keep_evidence(path, evidence / "created" / relative)
            remove_file(path)
        except (Refused, OSError):
            failed.append(relative)
    remove_empty_new_folders(game, manifest)
    for relative in before["renamed"]:
        try:
            put_name_back(game, relative)
        except OSError:
            failed.append(relative)
    for relative in before["missing_folders"]:
        try:
            (game / relative).mkdir(parents=True, exist_ok=True)
        except OSError:
            failed.append(relative)
    for relative in before["changed"] + before["missing"]:
        row = manifest["files"][relative]
        target, blob = game / relative, store / row["sha256"]
        try:
            if not blob.is_file() or sha256(blob) != row["sha256"]:
                raise Refused("reference damaged")
            if relative in before["changed"]:
                keep_evidence(target, evidence / "changed" / relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            replace_verified(blob, target, row["sha256"], row["mtime_ns"], row["read_only"])
        except (Refused, OSError):
            failed.append(relative)
    for relative in sorted(set(before["times"] + before["attributes"])):
        row = manifest["files"][relative]
        target = game / relative
        try:
            os.chmod(target, stat.S_IWRITE)
            os.utime(target, ns=(row["mtime_ns"], row["mtime_ns"]))
            if row["read_only"]:
                os.chmod(target, stat.S_IREAD)
        except OSError:
            failed.append(relative)
    for row in manifest["additions"]:
        try:
            remove_addition(game, row, evidence)
        except (Refused, OSError):
            failed.append(row["target"])
    after = compare(game, manifest)
    leftovers = [row["target"] for row in manifest["additions"] if os.path.lexists(game / row["target"])]
    clean = not (failed or leftovers or any(after.values()))
    seen = {name: set() for name in DIFFERENCES + ("altered_additions",)}
    for n in range(1, attempt + 1):
        earlier = read_json(folder / "restore-attempt-{}.json".format(n))
        for name in seen:
            seen[name].update(earlier[name])
    receipt = {"owner": owner, "run": run, "phase_at_restore": phase, "manifest_sha256": sha256(folder / MANIFEST), "verified": clean,
               "attempts": attempt, **{name: sorted(values) for name, values in seen.items()}, "failed": sorted(set(failed)), "after": after, "leftover_additions": leftovers,
               "files_checked": len(manifest["files"])}
    write_json(folder / RECEIPT, receipt)
    if not clean:
        set_phase(state, "restore-incomplete")
        return receipt
    release_clean(game, state, owner, slots)
    return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "prepare", "restore", "verify", "status"):
        sub = commands.add_parser(name)
        sub.add_argument("--state", type=Path, required=True)
        if name != "status":
            sub.add_argument("--game", type=Path, required=True)
        if name in ("check", "prepare", "restore"):
            sub.add_argument("--store", type=Path, required=True)
            sub.add_argument("--owner", required=True)
        if name in ("check", "prepare"):
            sub.add_argument("--run", required=True)
            sub.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            result = check(args.game, args.state, args.store, args.owner, args.run, read_json(args.profile))
        elif args.command == "prepare":
            result = prepare(args.game, args.state, args.store, args.owner, args.run, read_json(args.profile))
        elif args.command == "restore":
            result = restore(args.game, args.state, args.store, args.owner)
        elif args.command == "verify":
            result = verify(args.game, args.state, record=True)
        else:
            result = status(args.state)
    except Refused as error:
        print("refused: {}".format(error), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=1, sort_keys=True))
    if args.command == "verify":
        return 1 if any(result[name] for name in DIFFERENCES) else 0
    return 1 if isinstance(result, dict) and result.get("verified") is False else 0


if __name__ == "__main__":
    sys.exit(main())
