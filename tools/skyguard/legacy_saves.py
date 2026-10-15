"""Read the installed 3.x save-backups format; preserve it unchanged on recovery."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from modding_harness.profiles import canonical
from modding_harness.preservation import contains

ID = re.compile(r"^local-([A-Za-z0-9_-]{1,120})$")


def root(project):
    return Path(project) / ".claude/backups/save-backups"


def records(project):
    path = root(project) / "manifest.jsonl"
    if not path.exists():
        return [], 0
    good, bad, seen, duplicate = [], 0, set(), set()
    with open(path, "rb") as stream:
        for line in stream:
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not ID.fullmatch("local-" + row["id"]):
                    raise ValueError("invalid legacy record")
                if row["id"] in seen:
                    duplicate.add(row["id"])
                    raise ValueError("ambiguous legacy record id")
                seen.add(row["id"])
                group(project, row)
                good.append(row)
            except (ValueError, TypeError, OSError):
                bad += 1
    bad += sum(row["id"] in duplicate for row in good)
    return [row for row in good if row["id"] not in duplicate], bad


def group(project, row):
    originals = row.get("originals")
    if ("ess" not in row or "skse" not in row or not isinstance(originals, list) or len(originals) != 2
            or not all(isinstance(p, str) and os.path.isabs(p) for p in originals)
            or Path(originals[0]).suffix.lower() != ".ess"
            or Path(originals[1]).suffix.lower() != ".skse"
            or canonical(Path(originals[0]).with_suffix(".skse")) != canonical(originals[1])):
        raise ValueError("legacy record does not describe a complete logical pair")
    result = []
    for kind, original in zip(("ess", "skse"), originals):
        item = row.get(kind)
        if item is None:
            result.append({"original": original, "source": str((root(project) / ".recorded-missing" /
                           row["id"] / Path(original).name).absolute()), "missing": True})
            continue
        if (not isinstance(item, dict) or not isinstance(item.get("original"), str)
                or canonical(item["original"]) != canonical(original)
                or not isinstance(item.get("backup"), str) or not os.path.isabs(item["backup"])
                or not contains(root(project), item["backup"]) or Path(item["backup"]).is_symlink()
                or not isinstance(item.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                or type(item.get("size")) is not int or item["size"] < 0):
            raise ValueError("invalid legacy member authority")
        result.append({"original": original, "source": item["backup"], "missing": False,
                       "sha256": item["sha256"], "size": item["size"]})
    if all(r["missing"] for r in result):
        raise ValueError("legacy record holds no saved bytes")
    return result


def restore(project, sid, wanted, to, in_place, out):
    from . import save_store
    try:
        match = ID.fullmatch(sid)
        rows, _bad = records(project)
        selected = next((row for row in rows if match and row["id"] == match.group(1)), None)
        if selected is None:
            raise ValueError("no unique usable legacy save record " + sid)
        members = group(project, selected)
        target = save_store.store(project, [r["original"] for r in members])
        manifest = target.import_records([members], {"profile": "skyrim", "legacy_store": "save-backups",
                                                    "legacy_id": selected["id"]})
    except (OSError, ValueError) as exc:
        print("REFUSED: legacy save evidence could not be preserved: {}".format(exc), file=out)
        return 2
    print("Imported verified legacy evidence as core-{}; legacy store preserved.".format(manifest["id"]), file=out)
    return save_store.restore(project, "core-" + manifest["id"], wanted, to, in_place, out)


def recovery_restore(project, sid, wanted, to, in_place, out):
    """Import the other 3.x save format, then use the same guarded recovery path.

    The legacy manifest and blobs remain unchanged. Recorded absent members are
    authority too: do not infer an absent co-save merely from an omitted line.
    """
    from . import recovery, save_store, snapcli
    try:
        legacy = recovery.store_dir(project)
        state = recovery.resolve(legacy, sid)
        if in_place:
            for original in state:
                why = snapcli._allowed(project, sid, "", Path(original))
                if why:
                    raise ValueError(why)
        rows = [row for row in recovery.read_manifest(legacy)[0] if row["snap"] == sid]
        names = set(state)
        if (not rows or len(rows) != len(state) or any(row["kind"] != "save" or
                row["lines"] != len(rows) or len(row["state"]) != len(names) or
                set(row["state"]) != names for row in rows)):
            raise ValueError("ambiguous or incomplete legacy save snapshot")
        groups = {}
        for original, row in state.items():
            path = Path(original)
            missing = row.get("missing")
            if (not path.is_absolute() or path.suffix.lower() not in (".ess", ".skse") or
                    not isinstance(missing, list) or not all(isinstance(p, str) and Path(p).is_absolute() for p in missing)):
                raise ValueError("legacy snapshot is not complete save-pair authority")
            stem = canonical(path.with_suffix(""))
            group = groups.setdefault(stem, {})
            for name, absent in [(original, False), *((name, True) for name in missing)]:
                if canonical(Path(name).with_suffix("")) != stem or Path(name).suffix.lower() not in (".ess", ".skse"):
                    raise ValueError("legacy missing-member authority names a different save")
                key = canonical(name)
                entry = {"original": name, "source": str((legacy / ".recorded-missing" / sid / Path(name).name).absolute()),
                         "missing": absent}
                if not absent:
                    entry.update(source=str((legacy / "blobs" / row["sha256"]).absolute()),
                                 sha256=row["sha256"], size=row["size"])
                if key in group and group[key] != entry:
                    raise ValueError("contradictory legacy save member authority")
                group[key] = entry
        complete = []
        for group in groups.values():
            members = list(group.values())
            if len(members) != 2 or {Path(r["original"]).suffix.lower() for r in members} != {".ess", ".skse"}:
                raise ValueError("legacy snapshot omits save counterpart authority")
            complete.append(members)
        if in_place:
            for member in (r for group in complete for r in group):
                why = snapcli._allowed(project, sid, "", Path(member["original"]))
                if why:
                    raise ValueError(why)
        target = save_store.store(project, [r["original"] for group in complete for r in group])
        manifest = target.import_records(complete, {"profile": "skyrim", "legacy_store": "recovery_snapshots", "legacy_id": sid})
    except (OSError, ValueError) as exc:
        print("REFUSED: legacy save evidence could not be preserved: {}".format(exc), file=out)
        return 2
    print("Imported verified legacy recovery evidence as core-{}; legacy store preserved.".format(manifest["id"]), file=out)
    return save_store.restore(project, "core-" + manifest["id"], wanted, to, in_place, out)
