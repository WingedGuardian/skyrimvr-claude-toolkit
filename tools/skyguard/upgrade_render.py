"""Render private upgrade candidates; never discover or write a live installation."""
from __future__ import annotations

from pathlib import Path
import os
import re
import sys

sys.dont_write_bytecode = True
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skyguard import codex_definitions, guard_hash, paths

DOC_TOKENS = ("{{GAME_ROOT}}", "{{CONFIG_DIR}}", "{{LOADORDER_DIR}}", "{{SKYRIM_FOLDER}}", "{{MOD_MANAGER_PATHS}}")
BOM = b"\xef\xbb\xbf"
STOCK = ("- **Mod manager**: stock layout (Vortex or manual) — mods deploy their files directly into `Data/`,\n"
         "  so `Data/` is the real, merged view of everything installed.")
MO2 = """- **Mod manager**: **Mod Organizer 2** — instance `{instance}/`, profile `{profile}`
- **MO2 mods**: `{mods}/<mod-name>/` — the REAL location of every installed mod's files
- **MO2 overwrite**: `{overwrite}/` — catches files written to Data/ during a session
- **MO2 profile**: `{profile_dir}/` — this profile's INIs, `loadorder.txt`, `plugins.txt`

> **⚠ MO2 has no real `Data/` folder.** It builds a *virtual* one at launch by merging the stock
> game folder, each enabled mod's folder, and `overwrite/` (highest priority). The `Data/` path
> above is the **stock game only** — it does NOT contain the user's mods. To inspect an installed
> mod's files, read `{mods}/<mod-name>/`, not `Data/`.
>
> **This breaks load-order-aware tooling.** xelib/XEditLib resolves plugins from the game path, so
> launched outside MO2 it sees only the plugins physically present in the stock `Data/` — it will
> silently return a *wrong but plausible* answer for anything involving the override chain or the
> full load order. For those, run the script through MO2 (add it to MO2's executables list) so the
> virtual filesystem is mounted. Single-file reads (Spriggit on a specific `.esp` by path) work
> fine outside MO2, because they never consult the load order."""


def _text(value):
    if (not isinstance(value, str) or not value or len(value.encode("utf-8")) > 8192 or
            any(ord(c) < 32 or ord(c) == 127 for c in value) or "{{" in value or "}}" in value):
        raise ValueError("render values must be bounded, nonempty single-line text")
    return value


def _path(value):
    value = _text(value).replace("\\", "/")
    if not (value.startswith("/") or re.match(r"^[A-Za-z]:/", value)):
        raise ValueError("render paths must be absolute")
    return value.rstrip("/") or "/"


def _path_key(value):
    value = _path(value)
    return value.casefold() if re.match(r"^(?:[A-Za-z]:/|/[A-Za-z]/)", value) else value


def _config(root, config):
    required = {"v", "config_dir", "loadorder_dir", "skyrim_folder", "python", "bash", "mod_manager"}
    if (not isinstance(config, dict) or set(config) not in (required, required | {"game_root"}) or
            type(config["v"]) is not int or config["v"] != 1):
        raise ValueError("expected complete Skyrim render configuration version 1")
    data = {name: _path(config[name]) for name in ("config_dir", "loadorder_dir", "python", "bash")}
    data["skyrim_folder"] = _text(config["skyrim_folder"])
    manager = config["mod_manager"]
    if not isinstance(manager, dict):
        raise ValueError("explicit mod-manager configuration required")
    if manager == {"kind": "stock"}:
        data["block"] = STOCK
    elif (manager.get("kind") == "mo2" and
          set(manager) == {"kind", "instance", "profile", "mods", "overwrite", "profile_dir"}):
        manager = {**manager, **{name: _path(manager[name]) for name in ("instance", "mods", "overwrite", "profile_dir")},
                   "profile": _text(manager["profile"])}
        if any(_path_key(data[name]) != _path_key(manager["profile_dir"]) for name in ("config_dir", "loadorder_dir")):
            raise ValueError("MO2 instructions must use the named profile for configs and load order")
        data["block"] = MO2.format(**manager)
    else:
        raise ValueError("unsupported or incomplete mod-manager configuration")
    installed = paths.read_install(str(root), {})
    if installed.malformed:
        raise ValueError("installed protection paths are malformed; review before rendering")
    for key, name in (("SKYRIM_CONFIG_DIR", "config_dir"), ("SKYRIM_LOADORDER_DIR", "loadorder_dir")):
        if installed.first(key) and _path_key(installed.first(key)) != _path_key(data[name]):
            raise ValueError("render configuration differs from retained installed paths: " + key)
    for key, name in (("SKYRIM_MO2_MODS", "mods"), ("SKYRIM_MO2_OVERWRITE", "overwrite")):
        if installed.first(key) and (manager.get("kind") != "mo2" or
                _path_key(installed.first(key)) != _path_key(manager[name])):
            raise ValueError("render configuration differs from retained mod-manager paths")
    data["root"] = _path(Path(root).as_posix())
    data["game_root"] = _path(config.get("game_root", installed.first("SKYRIM_GAME_ROOT") or data["root"]))
    if installed.first("SKYRIM_GAME_ROOT") and _path_key(installed.first("SKYRIM_GAME_ROOT")) != _path_key(data["game_root"]):
        raise ValueError("render configuration differs from retained game root")
    data["posix"] = ("/" + data["root"][0].lower() + data["root"][2:]
                     if re.match(r"^[A-Za-z]:/", data["root"]) else data["root"])
    return data


def _hook(name, raw, config):
    text = raw.decode("utf-8-sig")
    lines = text.split("\n")
    slots = guard_hash.CONFIGURED[name]
    for slot, expression, token, _escape in slots:
        matched = []
        for index, line in enumerate(lines):
            if expression.fullmatch(line.removesuffix("\r")):
                matched.append(index)
        if len(matched) != 1:
            raise ValueError("hook has no unique qualified interpreter slot: " + name)
        value = config[slot]
        value = value.replace("'", "''" if name.endswith(".ps1") else "'\\''")
        index = matched[0]
        replacement = token.decode().replace("{{PYTHON_PATH}}", value).replace("{{BASH_PATH}}", value)
        lines[index] = replacement + ("\r" if lines[index].endswith("\r") else "")
    result = "\n".join(lines).encode("utf-8")
    # Windows PowerShell 5.1 reads BOM-less scripts using its legacy code page.
    if name.endswith(".ps1") and (raw.startswith(BOM) or not result.isascii()):
        result = BOM + result
    return result


def prepare(root, staged, files, config):
    data = _config(root, config)
    replacements = dict(zip(DOC_TOKENS, (data["game_root"], data["config_dir"], data["loadorder_dir"],
                                        data["skyrim_folder"], data["block"])))
    result = {}
    for name in ("AGENTS.md", "CLAUDE.md"):
        if name in files:
            text = (staged / name).read_bytes().decode("utf-8-sig")
            for token, value in replacements.items():
                text = text.replace(token, value)
            result[name] = text.encode("utf-8")
    if ".codex/hooks.json" in files:
        text = (staged / ".codex/hooks.json").read_bytes().decode("utf-8-sig")
        # The existing canonical renderer retains its frozen command normalization.
        result[".codex/hooks.json"] = codex_definitions.render(text, data["root"], data["posix"]).encode("utf-8")
    for name in guard_hash.CONFIGURED:
        if name in files:
            result[name] = _hook(name, (staged / name).read_bytes(), data)
    validate(root, tuple({"path": name, "source": staged / name, "data": raw} for name, raw in result.items()))
    return result


def validate(root, changes):
    for row in changes:
        name = row["path"]
        if name not in ("AGENTS.md", "CLAUDE.md", ".codex/hooks.json", *guard_hash.CONFIGURED):
            continue
        raw = row["data"] if "data" in row else Path(row["source"]).read_bytes()
        text = raw.decode("utf-8-sig")
        tokens = (*DOC_TOKENS, *codex_definitions.PLACEHOLDERS, "{{PYTHON_PATH}}", "{{BASH_PATH}}")
        if any(token in text for token in tokens):
            raise ValueError("candidate needs destination rendering before application: " + name)
        if name == "AGENTS.md" and len(raw) > 32768:
            raise ValueError("rendered agent instructions exceed the 32768-byte budget")
        if name == ".codex/hooks.json":
            from modding_harness import transport
            transport.load(raw)
        if name in guard_hash.CONFIGURED:
            _body, values = guard_hash.normalised(name, raw)
            if set(values) != {slot[0] for slot in guard_hash.CONFIGURED[name]}:
                raise ValueError("candidate hook is missing a qualified interpreter slot")
            for value in values.values():
                _path(value)
            if name.endswith(".ps1") and not raw.isascii() and not raw.startswith(BOM):
                raise ValueError("non-ASCII PowerShell hook requires an explicit UTF-8 BOM")


def encode_powershell(path):
    """Make setup's UTF-8 path substitutions readable by Windows PowerShell 5.1."""
    import hashlib
    import time
    import uuid
    from modding_harness import durable, upgrade
    path = Path(path)
    if path.parts[-3:] != (".codex", "hooks", "skyguard-entry.ps1"):
        raise ValueError("only the toolkit's named PowerShell hook can be normalized")
    path = upgrade._entry(path.absolute().parents[2].resolve(), ".codex/hooks/skyguard-entry.ps1")
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError("hook encoding requires an independent regular file")
    before = durable.identity(path)
    with path.open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("PowerShell hook exceeds its encoding budget")
    text = raw.decode("utf-8-sig").encode("utf-8")
    wanted = (BOM if raw.startswith(BOM) or not text.isascii() else b"") + text
    if wanted == raw:
        return False
    mode = path.stat().st_mode & 0o777
    if not mode & 0o222:
        raise ValueError("read-only PowerShell hook requires separate review")
    part = path.with_name(".encoding-" + uuid.uuid4().hex)
    deadline = time.monotonic() + 10
    try:
        with part.open("xb", buffering=0) as stream:
            durable.write_all(stream.fileno(), wanted)
            os.fsync(stream.fileno())
        os.chmod(part, mode)
        if durable.digest(part, deadline) != hashlib.sha256(wanted).hexdigest():
            raise OSError("PowerShell encoding failed readback")
        if (durable.identity(path) != before or durable.digest(path, deadline) != hashlib.sha256(raw).hexdigest()
                or durable.identity(path) != before):
            raise OSError("PowerShell hook changed during encoding")
        durable.check_deadline(deadline)
        os.replace(part, path)
        durable.sync_dir(path.parent)
    finally:
        if part.exists():
            os.chmod(part, part.stat().st_mode | 0o200)
            part.unlink()
    return True


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("encode-powershell",))
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        encode_powershell(args.path)
    except (ValueError, OSError) as exc:
        parser.exit(2, "hook encoding refused: " + str(exc) + "\n")
