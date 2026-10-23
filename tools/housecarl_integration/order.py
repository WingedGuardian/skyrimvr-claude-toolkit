"""Read persisted Wrye/asterisk order and TES4 headers; never rewrite an authority file."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct

from . import Refusal
from .files import contained, plain_plugin, sha
from modding_harness.durable import identity

BASE = ("Skyrim.esm", "Update.esm", "Dawnguard.esm", "HearthFires.esm", "Dragonborn.esm")


def plugin_header(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(24)
        if len(raw) != 24 or raw[:4] != b"TES4":
            raise Refusal("Not a readable TES4 plugin: " + str(path))
        size, flags = struct.unpack_from("<II", raw, 4)
        if size > 16 * 1024 * 1024:
            raise Refusal("Unreasonable TES4 header size: " + str(path))
        body = stream.read(size)
    if len(body) != size:
        raise Refusal("Truncated TES4 header: " + str(path))
    masters, pos, extended = [], 0, None
    while pos < len(body):
        if pos + 6 > len(body):
            raise Refusal("Truncated TES4 subrecord: " + str(path))
        sig, length = body[pos:pos + 4], struct.unpack_from("<H", body, pos + 4)[0]
        pos += 6
        if sig == b"XXXX":
            if length != 4 or pos + 4 > len(body) or extended is not None:
                raise Refusal("Malformed extended TES4 subrecord: " + str(path))
            extended = struct.unpack_from("<I", body, pos)[0]
            pos += 4
            continue
        if extended is not None:
            length, extended = extended, None
        if pos + length > len(body):
            raise Refusal("Truncated TES4 subrecord payload: " + str(path))
        if sig == b"MAST":
            masters.append(plain_plugin(body[pos:pos + length].rstrip(b"\0").decode("cp1252")))
        pos += length
    if extended is not None:
        raise Refusal("Dangling extended TES4 subrecord: " + str(path))
    return {"flags": flags, "masters": masters,
            "master": bool(flags & 1) or Path(path).suffix.lower() in (".esm", ".esl"),
            "light": bool(flags & 0x200) or Path(path).suffix.lower() == ".esl"}


def parse_plugins(raw):
    text = raw.decode("utf-8-sig") if raw.startswith(b"\xef\xbb\xbf") else raw.decode("cp1252")
    rows, seen = [], set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        active = line.startswith("*")
        name = line[1:] if active else line
        if name.lower().endswith(".ghost"):
            name = name[:-6]
        name = plain_plugin(name)
        if name.casefold() in seen:
            raise Refusal("Duplicate plugin in authoritative plugins.txt: " + name)
        seen.add(name.casefold())
        rows.append((name, active))
    return rows


@dataclass
class Snapshot:
    names: list
    paths: dict
    headers: dict
    fingerprint: str
    source: str
    inactive: list
    stamps: list


def capture(config):
    data = Path(config["data_dir"])
    source = Path(config["plugins_txt"])
    raw = source.read_bytes()
    rows = parse_plugins(raw)
    priority = config.get("asset_roots", [])
    disk = {p.name.casefold(): p for p in data.iterdir()
            if p.is_file() and p.suffix.lower() in (".esp", ".esm", ".esl")}
    extra = b""
    if config.get("asset_provider") == "mo2":
        profile = source.parent
        mods_raw = (profile / "modlist.txt").read_bytes()
        order_raw = (profile / "loadorder.txt").read_bytes()
        extra += mods_raw + order_raw
        priority = [str(Path(config["mods_dir"]) / line[1:].strip())
                    for line in mods_raw.decode("utf-8-sig").splitlines()
                    if line.startswith("+") and not line.strip().endswith("_separator")]
        if any(not contained(p, config["mods_dir"]) or not Path(p).is_dir() for p in priority):
            raise Refusal("Enabled MO2 mod is missing or outside the configured mods directory.")
        if config.get("overwrite_dir"):
            priority.insert(0, config["overwrite_dir"])
        for directory in reversed(priority):
            if Path(directory).is_dir():
                for p in Path(directory).iterdir():
                    if p.is_file() and p.suffix.lower() in (".esp", ".esm", ".esl"):
                        disk[p.name.casefold()] = p
        active_set = {n.casefold() for n, enabled in rows if enabled}
        order_names = [n for n, _ in parse_plugins(order_raw)]
        missing = active_set - {n.casefold() for n in order_names}
        if missing:
            raise Refusal("MO2 profile order omits checked plugins: " + ", ".join(sorted(missing)))
        rows = [(n, n.casefold() in active_set) for n in order_names]
        config = {**config, "asset_roots": priority}
    fixed = list(BASE) + (["SkyrimVR.esm"] if config["edition"] == "VR" else [])
    ccc = data.parent / "Skyrim.ccc"
    if ccc.exists():
        fixed += [n for n, _active in parse_plugins(ccc.read_bytes())]
    fixed = list(dict.fromkeys(disk[n.casefold()].name for n in fixed if n.casefold() in disk))
    if not fixed or fixed[0].casefold() != "skyrim.esm":
        raise Refusal("Skyrim.esm is missing from the configured Data folder.")
    active = fixed + [n for n, enabled in rows if enabled and n.casefold() not in {x.casefold() for x in fixed}]
    paths, headers, content = {}, {}, {}
    for name in active:
        if name.casefold() not in disk:
            raise Refusal("Enabled plugin missing from deployed Data: " + name)
        path = disk[name.casefold()]
        before = identity(path)
        paths[path.name] = path
        headers[path.name] = plugin_header(path)
        content[path.name] = sha(path)
        if identity(path) != before:
            raise Refusal("Plugin changed during capture; retry: " + path.name)
    if config["edition"] == "VR" and any(h["light"] for h in headers.values()):
        if not any((Path(p) / "SKSE/Plugins/skyrimvresl.dll").is_file() for p in [data, *priority]):
            raise Refusal("Light plugins on VR require installed Skyrim VR ESL Support; not found.")
    # Wrye displays stable master/non-master blocks, with forced masters first.
    fixed_set = {n.casefold() for n in fixed}
    names = fixed + sorted([n for n in paths if n.casefold() not in fixed_set],
                           key=lambda n: not headers[n]["master"])
    enabled = {n.casefold() for n in names}
    positions = {n.casefold(): i for i, n in enumerate(names)}
    for name, h in headers.items():
        missing = [m for m in h["masters"] if m.casefold() not in enabled]
        if missing:
            raise Refusal(name + " has inactive/missing masters: " + ", ".join(missing))
        if any(positions[m.casefold()] >= positions[name.casefold()] for m in h["masters"]):
            raise Refusal(name + " loads before one of its masters; resolve the order in its authority.")
    stamps = [(n, paths[n].stat().st_size, paths[n].stat().st_mtime_ns) for n in names]
    for name in ("Skyrim.ini", "SkyrimVR.ini", "SkyrimPrefs.ini"):
        p = Path(config["config_dir"]) / name
        extra += p.read_bytes() if p.is_file() else b""
    if ccc.is_file():
        extra += ccc.read_bytes()
    digest = hashlib.sha256(raw + extra + json.dumps([config["edition"], stamps, headers, content], sort_keys=True).encode()).hexdigest()
    for name, path in paths.items():
        before = identity(path)
        if sha(path) != content[name] or identity(path) != before:
            raise Refusal("Plugin bytes changed during capture; retry: " + name)
    if source.read_bytes() != raw:
        raise Refusal("Wrye-managed plugins.txt changed during capture; retry.")
    snap = Snapshot(names, paths, headers, digest, str(source), [n for n, a in rows if not a], stamps)
    snap.asset_roots = priority
    return snap


def write_profile(snapshot, folder, config):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    texts = {"loadorder.txt": "\n".join(snapshot.names) + "\n",
             "plugins.txt": "\n".join("*" + n for n in snapshot.names) + "\n",
             "modlist.txt": "\n".join("+" + str(p) for p in getattr(snapshot, "asset_roots", [])) + "\n"}
    # House Carl reads Skyrim.ini only; fold the VR archive override into its synthetic INI.
    import configparser
    ini = configparser.ConfigParser(strict=False, interpolation=None)
    ini.read([str(Path(config["config_dir"]) / n) for n in
              ("Skyrim.ini", "SkyrimVR.ini", "SkyrimPrefs.ini")], encoding="utf-8-sig")
    if config["edition"] == "VR":
        if not ini.has_section("Archive"):
            ini.add_section("Archive")
        vr = ini.get("Archive", "sVrResourceArchiveList", fallback="Skyrim_VR - Main.bsa")
        combined = ini.get("Archive", "sResourceArchiveList2", fallback="")
        ini.set("Archive", "sResourceArchiveList2", ",".join(s for s in (combined, vr) if s))
    import io
    out = io.StringIO()
    ini.write(out)
    texts["Skyrim.ini"] = out.getvalue()
    for name, text in texts.items():
        p = folder / name
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8", newline="\n")
    return folder
