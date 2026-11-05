#!/usr/bin/env python3
"""A read-only, immutable, local reference to a native game image, with provenance.

    python native/provenance/native_image.py register --store S --source EXE --image IMG --owner NAME \\
        --tool-name T --tool-commit C --tool-sha256 H [--tool-source-sha256 H] [--note TEXT]
    python native/provenance/native_image.py verify   --store S --sha256 H [--installed EXE]
    python native/provenance/native_image.py inspect  --store S --sha256 H --rva 0xA25B60 [--bytes 16]
    python native/provenance/native_image.py check    --store S --sha256 H --descriptor sites.json

This tool never derives an image and never runs a game or the derivation tool. You make the derived image yourself;
`register` checks it against its source, records what you declare about how it was made, and stores one read-only copy
per image hash, so consumers share it instead of copying it per run. Game bytes stay on your machine.
What a declaration claims about the tool is recorded as declared: this tool cannot prove it.

The store is an explicit argument (or SKYRIM_NATIVE_IMAGE_STORE). There is no default location.

Exit codes: 0 verified, 1 failed or drifted, 2 refused or unusable input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import struct
import sys
from pathlib import Path

MANIFEST = "manifest.json"
IMAGE = "image.bin"
MAX_READ = 4096


class Refused(Exception):
    """The input cannot be used; nothing was changed."""


def sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class PE:
    """Just enough of a PE32+ file. Offsets are bounds-checked: the input is data, not trusted."""

    def __init__(self, data: bytes):
        self.data = data
        if data[:2] != b"MZ" or len(data) < 0x40:
            raise Refused("not a PE file")
        offset = self._u32(0x3C)
        if data[offset:offset + 4] != b"PE\0\0":
            raise Refused("not a PE file")
        self.machine, self.section_count = struct.unpack_from("<HH", data, offset + 4)
        optional_size = struct.unpack_from("<H", data, offset + 20)[0]
        optional = offset + 24
        if self.machine != 0x8664 or struct.unpack_from("<H", data, optional)[0] != 0x20B:
            raise Refused("not an x64 PE32+ image")
        self.entry_rva = self._u32(optional + 16)
        self.image_base = struct.unpack_from("<Q", data, optional + 24)[0]
        self.size_of_image = self._u32(optional + 56)
        directories = self._u32(optional + 108)
        self.pdata = (0, 0)
        if directories > 3:
            self.pdata = struct.unpack_from("<II", data, optional + 112 + 3 * 8)
        self.sections = []
        table = optional + optional_size
        if table + 40 * self.section_count > len(data):
            raise Refused("section table runs past the end of the file")
        for index in range(self.section_count):
            raw = data[table + 40 * index:table + 40 * index + 40]
            virtual_size, rva, raw_size, raw_pointer = struct.unpack_from("<IIII", raw, 8)
            self.sections.append({"name": raw[:8].rstrip(b"\0").decode("latin-1"), "rva": rva,
                                  "virtual_size": virtual_size, "raw_size": raw_size, "raw_pointer": raw_pointer,
                                  "characteristics": struct.unpack_from("<I", raw, 36)[0]})

    def _u32(self, at: int) -> int:
        if at + 4 > len(self.data):
            raise Refused("truncated PE header")
        return struct.unpack_from("<I", self.data, at)[0]

    def section_at(self, rva: int):
        for section in self.sections:
            if section["rva"] <= rva < section["rva"] + max(section["virtual_size"], section["raw_size"]):
                return section
        return None

    def read(self, rva: int, count: int) -> bytes:
        section = self.section_at(rva)
        if section is None:
            raise Refused("RVA {:#x} is in no section".format(rva))
        delta = rva - section["rva"]
        if delta + count > section["raw_size"]:
            raise Refused("RVA {:#x}+{} runs past the section's file data".format(rva, count))
        return self.data[section["raw_pointer"] + delta:section["raw_pointer"] + delta + count]

    def runtime_function(self, rva: int):
        """The .pdata entry whose range covers `rva`, with unwind facts; None when there is none (leaf or data)."""
        start, size = self.pdata
        low, high = 0, size // 12 - 1
        while low <= high:
            middle = (low + high) // 2
            begin, end, unwind = struct.unpack("<III", self.read(start + middle * 12, 12))
            if rva < begin:
                high = middle - 1
            elif rva >= end:
                low = middle + 1
            else:
                header = self.read(unwind, 4)
                flags = header[0] >> 3
                return {"begin": begin, "end": end, "unwind_rva": unwind, "prolog_size": header[1],
                        "unwind_code_count": header[2], "chained": bool(flags & 4),
                        "has_handler": bool(flags & 3)}
        return None


def check_derivation(source: PE, image: PE) -> list:
    """Reasons `image` is not a derivation of `source` with the same layout and a real entry point. Passing does not
    prove the code bytes are right; that needs the tool provenance and a comparison at known sites."""
    problems = []
    for field in ("image_base", "size_of_image", "section_count"):
        if getattr(source, field) != getattr(image, field):
            problems.append("{} differs".format(field))
    layout = lambda pe: [(s["name"], s["rva"], s["virtual_size"]) for s in pe.sections]
    if layout(source) != layout(image):
        problems.append("section layout differs")
    entry = image.section_at(image.entry_rva)
    if entry is None or not entry["characteristics"] & 0x20:
        problems.append("the derived entry point is not in a code section")
    elif entry["name"] == ".bind":
        problems.append("the derived entry point is still in the SteamStub .bind section")
    if source.entry_rva == image.entry_rva:
        problems.append("the entry point did not change, so this does not look like an unwrapped image")
    return problems


def store_path(arg) -> Path:
    value = arg or os.environ.get("SKYRIM_NATIVE_IMAGE_STORE")
    if not value:
        raise Refused("give --store or set SKYRIM_NATIVE_IMAGE_STORE; there is no default location")
    return Path(value)


def refuse_game_root(path: Path) -> None:
    resolved = path.resolve()
    for folder in [resolved, *resolved.parents]:
        if (folder / "SkyrimVR.exe").exists() or (folder / "SkyrimSE.exe").exists():
            raise Refused("the store cannot be inside a game folder ({})".format(folder))


def register(store: Path, source: Path, image: Path, owner: str, tool: dict, note: str = "") -> dict:
    refuse_game_root(store)
    for path in (source, image):
        if not path.is_file():
            raise Refused("{} is not a file".format(path))
    source_pe, image_pe = PE(source.read_bytes()), PE(image.read_bytes())
    problems = check_derivation(source_pe, image_pe)
    if problems:
        raise Refused("this image does not derive from that source:\n  " + "\n  ".join(problems))
    digest, source_digest = sha256(image), sha256(source)
    folder = store / digest
    entry = {
        "schema": 1,
        "image_sha256": digest,
        "image_bytes": image.stat().st_size,
        "source_sha256": source_digest,
        "source_bytes": source.stat().st_size,
        "owner": owner,
        "tool_declared": tool,
        "retention": "kept until its owner removes it; housekeep must leave it alone (.housekeep-keep)",
        "note": note,
        "checks": {"layout_and_entry_point": "passed"},
    }
    if folder.exists():
        existing = load(store, digest)
        if existing["source_sha256"] != source_digest:
            raise Refused("this image is already registered from a different source")
        return existing
    temporary = store / (".incoming-" + digest)
    temporary.mkdir(parents=True)
    copied = temporary / IMAGE
    shutil.copyfile(image, copied)
    if sha256(copied) != digest:
        shutil.rmtree(temporary)      # this run's own scratch
        raise Refused("the copy did not hash to its source")
    (temporary / MANIFEST).write_text(json.dumps(entry, indent=1), encoding="utf-8", newline="\n")
    (temporary / ".housekeep-keep").write_text("Immutable native image reference; see manifest.json.\n", encoding="utf-8")
    for name in (IMAGE, MANIFEST):
        os.chmod(temporary / name, stat.S_IREAD)
    os.replace(temporary, folder)
    return entry


def load(store: Path, digest: str) -> dict:
    path = store / digest / MANIFEST
    if not path.is_file():
        raise Refused("no image {} in {}".format(digest, store))
    return json.loads(path.read_text(encoding="utf-8"))


def verify(store: Path, digest: str, installed: Path = None) -> list:
    entry = load(store, digest)
    problems = []
    image = store / digest / IMAGE
    if not image.is_file():
        problems.append("the image file is missing")
    elif sha256(image) != digest or entry.get("image_sha256") != digest:
        problems.append("the image no longer hashes to its reference")
    elif os.access(image, os.W_OK):
        problems.append("the image is writable, so it is not immutable")
    if installed is not None and sha256(installed) != entry.get("source_sha256"):
        problems.append("the installed executable is not this image's source: the game changed")
    return problems


def open_image(store: Path, digest: str) -> PE:
    problems = verify(store, digest)
    if problems:
        raise Refused("; ".join(problems))
    return PE((store / digest / IMAGE).read_bytes())


def inspect(store: Path, digest: str, rva: int, count: int) -> dict:
    if not 0 < count <= MAX_READ:
        raise Refused("--bytes must be 1 to {}".format(MAX_READ))
    pe = open_image(store, digest)
    return {"rva": hex(rva), "section": (pe.section_at(rva) or {}).get("name"), "bytes": pe.read(rva, count).hex(),
            "runtime_function": pe.runtime_function(rva)}


def check_sites(store: Path, digest: str, descriptor: dict) -> list:
    """Every site whose bytes differ from the descriptor. A descriptor names its sites; none is assumed."""
    pe = open_image(store, digest)
    sites = descriptor.get("sites") or []
    if not sites:
        raise Refused("the descriptor names no sites")
    problems = []
    for site in sites:
        expected = bytes.fromhex(site["expected_hex"])
        actual = pe.read(int(site["rva"], 16), len(expected))
        if actual != expected:
            problems.append("{} at {}: expected {} found {}".format(site.get("name"), site["rva"], expected.hex(), actual.hex()))
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    reg = sub.add_parser("register")
    reg.add_argument("--source", type=Path, required=True)
    reg.add_argument("--image", type=Path, required=True)
    reg.add_argument("--owner", required=True)
    reg.add_argument("--tool-name", required=True)
    reg.add_argument("--tool-commit", required=True)
    reg.add_argument("--tool-sha256", required=True)
    reg.add_argument("--tool-source-sha256", default="")
    reg.add_argument("--note", default="")
    for name in ("verify", "inspect", "check"):
        command = sub.add_parser(name)
        command.add_argument("--sha256", required=True)
        if name == "verify":
            command.add_argument("--installed", type=Path)
        if name == "inspect":
            command.add_argument("--rva", required=True)
            command.add_argument("--bytes", type=int, default=16)
        if name == "check":
            command.add_argument("--descriptor", type=Path, required=True)
    for command in sub.choices.values():
        command.add_argument("--store")
    args = parser.parse_args(argv)
    try:
        store = store_path(args.store)
        if args.command == "register":
            tool = {"name": args.tool_name, "commit": args.tool_commit, "sha256": args.tool_sha256,
                    "source_sha256": args.tool_source_sha256}
            print(json.dumps(register(store, args.source, args.image, args.owner, tool, args.note), indent=1))
            return 0
        if args.command == "verify":
            problems = verify(store, args.sha256, args.installed)
            print(json.dumps({"verified": not problems, "problems": problems, **load(store, args.sha256)}, indent=1))
            return 1 if problems else 0
        if args.command == "inspect":
            print(json.dumps(inspect(store, args.sha256, int(args.rva, 16), args.bytes), indent=1))
            return 0
        problems = check_sites(store, args.sha256, json.loads(args.descriptor.read_text(encoding="utf-8")))
        print(json.dumps({"verified": not problems, "problems": problems}, indent=1))
        return 1 if problems else 0
    except Refused as e:
        print("REFUSED: {}".format(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
