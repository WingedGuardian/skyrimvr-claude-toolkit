"""Explicit optional installation. No system runtime/package/registry changes."""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import urllib.request
import uuid
import zipfile

from . import ARCHIVE_SHA256, ARCHIVE_URL, Refusal, UPSTREAM_VERSION
from .files import backup, contained, json_write, sha


def install_archive(archive, dest):
    if sha(archive) != ARCHIVE_SHA256:
        raise Refusal("House Carl archive SHA256 does not match pinned 2.0.5; nothing extracted.")
    dest = Path(dest)
    if dest.exists():
        raise Refusal("Install destination already exists; use a new versioned installation directory.")
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            p = PurePosixPath(info.filename.replace("\\", "/"))
            if p.is_absolute() or ".." in p.parts or ":" in str(p) or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise Refusal("Archive contains an unsafe path/link.")
        z.extractall(dest)
    found = list(dest.rglob("housecarl-mcp.exe"))
    if len(found) != 1:
        raise Refusal("Pinned archive did not contain exactly one server executable.")
    server = found[0]
    required = [server.name, "housecarl-mcp.dll", "housecarl-core.dll", "corpus.json"]
    if any(not (server.parent / name).is_file() for name in required):
        raise Refusal("Pinned archive is missing required backend components.")
    return server, {p.name: sha(p) for p in server.parent.iterdir() if p.is_file()}


def download_archive(archive, opener=None):
    """Fetch the pinned archive to a unique part file, check its SHA256, and only then rename it to `archive`.
    Review L5 F6: it was written straight to the final name, so an interrupted download stayed there, the next run
    skipped the download, and every later setup failed on the hash. The part file is this run's own scratch, so it
    is removed when the download fails; a complete download with the wrong hash is kept, renamed, and named."""
    part = archive.with_name(archive.name + ".part-" + uuid.uuid4().hex)
    try:
        with (opener or urllib.request.urlopen)(ARCHIVE_URL, timeout=60) as src, part.open("xb") as dst:
            shutil.copyfileobj(src, dst)
    except BaseException:
        if part.exists():
            part.unlink()                                  # this run's own partial download
        raise
    if sha(part) != ARCHIVE_SHA256:
        rejected = archive.with_name(archive.name + ".rejected-" + uuid.uuid4().hex)
        os.replace(part, rejected)
        raise Refusal("Downloaded House Carl archive does not match pinned 2.0.5; kept as " + str(rejected)
                      + " for inspection, nothing extracted.")
    os.replace(part, archive)


def register(config, agents):
    import tomlkit
    root = Path(config["root"])
    state = Path(config["state_dir"])
    py = state / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    entry = {"command": str(py), "args": [str(root / "tools/housecarl.py"), "--root", str(root), "serve"]}
    changes = []
    for agent in agents:
        if agent == "claude":
            path = root / ".mcp.json"
            old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            servers = old.setdefault("mcpServers", {})
            prior = servers.get("housecarl")
            if prior == {"type": "stdio", **entry}:
                changes.append(str(path))
                continue
            if prior:
                raise Refusal("Project already has a different housecarl server; reconcile it explicitly.")
            servers["housecarl"] = {"type": "stdio", **entry}
            if path.exists():
                backup(path, state / "config-backups")
            json_write(path, old)
        else:
            path = root / ".codex/config.toml"
            old = tomlkit.parse(path.read_text(encoding="utf-8")) if path.exists() else tomlkit.document()
            servers = old.setdefault("mcp_servers", tomlkit.table())
            prior = servers.get("housecarl")
            desired = {**entry, "startup_timeout_sec": 30, "tool_timeout_sec": 240}
            if prior and dict(prior) == desired:
                changes.append(str(path))
                continue
            if prior:
                raise Refusal("Project already has a different housecarl server; reconcile it explicitly.")
            servers["housecarl"] = desired
            if path.exists():
                backup(path, state / "config-backups")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(tomlkit.dumps(old), encoding="utf-8", newline="\n")
        changes.append(str(path))
    return changes


def unregister(config, agents):
    import tomlkit
    root, state = Path(config["root"]), Path(config["state_dir"])
    removed = []
    for agent in agents:
        path = root / (".mcp.json" if agent == "claude" else ".codex/config.toml")
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        document = json.loads(text) if agent == "claude" else tomlkit.parse(text)
        servers = document.get("mcpServers" if agent == "claude" else "mcp_servers", {})
        entry = servers.get("housecarl")
        if not entry:
            continue
        if str(root / "tools/housecarl.py") not in entry.get("args", []):
            raise Refusal("House Carl registration is not owned by this integration; not removed.")
        backup(path, state / "config-backups")
        del servers["housecarl"]
        if agent == "claude":
            json_write(path, document)
        else:
            path.write_text(tomlkit.dumps(document), encoding="utf-8", newline="\n")
        removed.append(str(path))
    return removed


def setup(args):
    root = Path(args.root).resolve()
    state = root / ".skyguard/housecarl"
    if not args.mo2_instance and (not args.plugins_txt or not args.config_dir):
        raise Refusal("Deployed/Wrye setup requires --plugins-txt and --config-dir.")
    if os.name != "nt":
        raise Refusal("The pinned House Carl server supports Windows x64 only.")
    runtimes = subprocess.run(["dotnet", "--list-runtimes"], capture_output=True, text=True, check=True).stdout
    for runtime in ("Microsoft.NETCore.App 9.", "Microsoft.AspNetCore.App 9."):
        if runtime not in runtimes:
            raise Refusal("Missing " + runtime + "x runtime; install it manually, then rerun setup.")
    state.mkdir(parents=True, exist_ok=True)
    archive = Path(args.archive).resolve() if args.archive else state / "houseCARL-2.0.5.zip"
    if not args.archive and not archive.exists():
        download_archive(archive)
    elif not args.archive and sha(archive) != ARCHIVE_SHA256:
        raise Refusal(str(archive) + " does not match pinned 2.0.5 (an interrupted earlier download?). Move it to "
                      "the Recycle Bin and rerun setup to download it again; nothing extracted.")
    dest = state / "upstream" / (UPSTREAM_VERSION + "-" + uuid.uuid4().hex)
    server, hashes = install_archive(archive, dest)
    config = {"schema": 1, "root": str(root), "state_dir": str(state), "edition": args.edition,
              "authority": "wrye-bash", "asset_provider": "deployed-data",
              "data_dir": str(Path(args.data_dir or root / "Data").resolve()),
              "plugins_txt": str(Path(args.plugins_txt or root / "plugins.txt").resolve()),
              "config_dir": str(Path(args.config_dir or root).resolve()), "server": str(server),
              "server_hashes": hashes, "xelib_root": str(Path(args.xelib_root or root).resolve()),
              "node": shutil.which("node") or "node"}
    if args.mo2_instance:
        import configparser
        ini = configparser.ConfigParser(interpolation=None, strict=False)
        instance = Path(args.mo2_instance).resolve()
        ini.read_string((instance / "ModOrganizer.ini").read_text(encoding="utf-8-sig"))
        values = {}
        for section in ini.values():
            for key, value in section.items():
                value = value.strip().strip('"')
                if value.startswith("@ByteArray(") and value.endswith(")"):
                    value = value[len("@ByteArray("):-1]
                if value.startswith("@") or "\\\\" in value or "\\x" in value:
                    raise Refusal("Unsupported MO2 QSettings encoding for " + key + "; resolve the path explicitly.")
                values[key.casefold()] = value
        if not values.get("selected_profile") or not values.get("gamepath"):
            raise Refusal("MO2 instance lacks selected_profile or gamePath; no fallback profile is chosen.")
        base = Path(values.get("base_directory", str(instance)))
        base = (instance / base).resolve() if not base.is_absolute() else base.resolve()
        selected = values["selected_profile"]
        if selected in (".", "..") or any(c in selected for c in '/\\:'):
            raise Refusal("Unsupported selected MO2 profile name.")
        def directory(key, fallback):
            value = values.get(key, fallback).replace("%BASE_DIR%", str(base)).replace("%base_dir%", str(base))
            if "%" in value:
                raise Refusal("Unsupported MO2 path variable for " + key)
            path = Path(value)
            return (base / path).resolve() if not path.is_absolute() else path.resolve()
        profile = directory("profiles_directory", "profiles") / selected
        config.update(authority="mo2-profile", asset_provider="mo2", mods_dir=str(directory("mod_directory", "mods")),
                      overwrite_dir=str(directory("overwrite_directory", "overwrite")), plugins_txt=str(profile / "plugins.txt"),
                      data_dir=str(Path(values["gamepath"]) / "Data"), config_dir=str(profile))
    from .order import capture
    snapshot = capture(config)
    envdir = state / "venv"
    py = envdir / "Scripts/python.exe"
    if not py.exists():
        subprocess.run([sys.executable, "-m", "venv", str(envdir)], check=True)
    subprocess.run([str(py), "-m", "pip", "install", "--only-binary=:all:", "--require-hashes",
                    "--disable-pip-version-check", "-r", str(root / "tools/housecarl-requirements.lock")], check=True)
    config_path = state / "config.json"
    if config_path.exists():
        backup(config_path, state / "config-backups")
    json_write(config_path, config)
    # Registration is run in the optional environment, never by adding dependencies to the invoking Python.
    if args.agents != "none":
        subprocess.run([str(py), str(root / "tools/housecarl.py"), "--root", str(root), "register",
                        "--agents", args.agents], check=True)
    return {"installed": UPSTREAM_VERSION, "plugins": len(snapshot.names), "config": str(config_path),
            "server": str(server), "next": "Run doctor and a real call; restart hosts to discover MCP tools."}
