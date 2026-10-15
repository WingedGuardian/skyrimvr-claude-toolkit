"""Skyrim bridge to the neutral permanent store. Legacy stores remain readable."""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from modding_harness import profiles, workspace
from modding_harness.preservation import PreservationFailed, protects

PREFIX = "core-"
ID = re.compile(r"^core-[0-9a-f]{32}$")


def store(project, members=()):
    from . import mcp_rules, paths
    cfg = paths.read_install(str(project), os.environ)
    main = paths.main_worktree(str(project))
    if main:
        cfg = paths.merge_configs(cfg, paths.read_install(main, {}))
    roots = paths.roots(str(project), str(project), cfg)
    if roots.malformed:
        raise ValueError("configured save roots could not be read")
    folders = [Path(p) / "Saves" for p in roots.config_dirs]
    fallback = mcp_rules.saves_folder(roots, os.environ)
    if fallback:
        folders.append(Path(fallback))
    target = workspace.save_store(project, profiles.skyrim(folders))
    if any(protects(path, [target.root], ancestor=True) for path in members):
        raise ValueError("save source overlaps the permanent save store")
    return target


def backup_pairs(project, audit, pairs, command, agent="", session_id="", deadline=None):
    profile = profiles.skyrim()
    groups = []
    for ess, skse in pairs:
        members = profile.members(ess)
        if {profiles.canonical(p) for p in members} != {profiles.canonical(ess), profiles.canonical(skse)}:
            raise PreservationFailed("save pair does not identify one complete logical save")
        groups.append(members)
    target = store(project, [p for group in groups for p in group])
    manifest = target.backup(groups, {"profile": "skyrim", "host": agent, "session_id": session_id,
                            "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest()}, deadline)
    if not manifest:
        return []
    audit.log("save-preservation", "SAVE-BACKUP", "{} at {}; {} bytes stored, {} deduplicated; "
              "reserve {} bytes".format(PREFIX + manifest["id"], target.root, manifest["stored_bytes"],
                                       manifest["deduplicated_bytes"], manifest["reserve_bytes"]))
    return [PREFIX + manifest["id"]]


def rows(project):
    target = store(project)
    manifests, bad = workspace.summaries(target)
    result = []
    for manifest in manifests:
        names = []
        for group in manifest["groups"]:
            present = [Path(r["path"]).name for r in group["members"] if not r["missing"]]
            absent = [Path(r["path"]).name for r in group["members"] if r["missing"]]
            names.append(" + ".join(present) + "".join(", no " + p for p in absent))
        result.append((PREFIX + manifest["id"], "; ".join(names) +
                       " (kept forever; blob verification runs on restore)", ""))
    return result, bad


def restore(project, sid, wanted, to, in_place, out):
    from . import mcp_rules, paths, snapcli
    target = store(project)
    try:
        manifest = target.inspect(sid[len(PREFIX):])
        members = [r for g in manifest["groups"] for r in g["members"]]
        selected = []
        for item in wanted:
            hits = [r["path"] for r in members if snapcli._matches(r["path"], item)]
            if not hits:
                raise ValueError("--path matches no member: " + item)
            selected.extend(hits)
        groups = [g for g in manifest["groups"] if not selected or
                  any(r["path"] in selected for r in g["members"])]
        if in_place:
            cfg = paths.read_install(str(project), os.environ)
            roots = paths.roots(str(project), str(project), cfg)
            folder = mcp_rules.saves_folder(roots, os.environ)
            if folder is None or any(profiles.canonical(Path(r["path"]).parent) != profiles.canonical(folder)
                                     for g in groups for r in g["members"]):
                raise ValueError("in-place save restore is limited to the configured Saves folder")
        result = target.restore(sid[len(PREFIX):], to or None, selected, in_place)
    except (OSError, ValueError) as exc:
        from .audit import Audit
        Audit(str(project)).log("save-restore", "FAILED", "restore failed: {}".format(exc))
        print("REFUSED/FAILED: {}".format(exc), file=out)
        return 1 if "restore failed; published=" in str(exc) else 2
    for path in result["published"]:
        print("  restored " + path, file=out)
    print("Restored {} member(s); journal {}".format(len(result["published"]), result["journal"]), file=out)
    for rec in (r for g in groups for r in g["members"]):
        if rec["missing"]:
            print("  {} did not exist when this save was backed up".format(Path(rec["path"]).name), file=out)
    return 0
