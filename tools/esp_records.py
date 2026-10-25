"""Minimal READ-ONLY parser for TES5 / Skyrim SE / VR plugin files (.esp/.esm/.esl).

It exists as an INDEPENDENT cross-check next to xEdit/xelib/Spriggit: it reads the bytes directly, so a result both
agree on is not one tool's opinion repeated. It handles nested GRUPs, compressed records and XXXX (oversize)
subrecords. It does not resolve the load order and does not interpret field values: a FormID's high byte is an index
into the plugin's own master list (see Plugin.masters), nothing more.

It never writes, and it never guesses: a structure it cannot walk raises PluginError with the byte offset, and a
record it can read only partly (a compressed body that does not inflate, a FormID that appears twice, a master index
past the master list) is listed in Plugin.problems instead of being silently folded into the answer.

Used by esp-summary.py and esp-record-diff.py.
"""
from __future__ import annotations

import collections
import hashlib
import struct
import zlib

COMPRESSED = 0x00040000
HEADER_FLAGS = ((0x1, "ESM"), (0x80, "localized"), (0x200, "ESL"))

Record = collections.namedtuple("Record", "type formid edid flags sha1 body")


class PluginError(ValueError):
    """The file is not a plugin this parser can walk; the message names the byte offset."""


class Plugin:
    def __init__(self, header, records, problems):
        self.header = header                   # the TES4 Record
        self.records = records                 # every record in file order (duplicates included)
        self.problems = problems               # [str]: what was read only partly; empty for a clean plugin
        self.masters = masters(header.body)
        self.by_formid = {}
        for r in records:
            self.by_formid.setdefault(r.formid, r)

    def is_override(self, r) -> bool:
        return (r.formid >> 24) < len(self.masters)

    def master_of(self, r) -> str:
        return self.masters[r.formid >> 24] if self.is_override(r) else ""

    def header_flag_names(self):
        return [name for bit, name in HEADER_FLAGS if self.header.flags & bit]


def read(path) -> Plugin:
    with open(path, "rb") as f:
        data = f.read()
    return parse(data)


def parse(data: bytes) -> Plugin:
    records, problems = [], []
    header = None

    def walk(off, end):
        nonlocal header
        while off < end:
            if off + 24 > end:
                raise PluginError("truncated record header at offset {}".format(off))
            typ = data[off:off + 4]
            size = struct.unpack_from("<I", data, off + 4)[0]
            if typ == b"GRUP":
                # A GRUP's size includes its own 24-byte header. Smaller than that would walk in place forever.
                if size < 24 or off + size > end:
                    raise PluginError("GRUP at offset {} has an impossible size {}".format(off, size))
                walk(off + 24, off + size)
                off += size
                continue
            if off + 24 + size > end:
                raise PluginError("record {} at offset {} runs past its group or the file".format(
                    typ.decode("latin-1"), off))
            flags, fid = struct.unpack_from("<II", data, off + 8)
            body = data[off + 24:off + 24 + size]
            name = typ.decode("latin-1")
            if flags & COMPRESSED:
                try:
                    body = zlib.decompress(body[4:])
                except zlib.error as e:
                    problems.append("{} {:08X} at offset {}: compressed body does not inflate ({}); its hash is "
                                    "of the raw bytes".format(name, fid, off, e))
            edid = ""
            for tag, value in subrecs(body):
                if tag == "EDID":
                    edid = value.rstrip(b"\0").decode("latin-1")
                    break
            rec = Record(name, fid, edid, flags & ~COMPRESSED, hashlib.sha1(body).hexdigest(), body)
            if typ == b"TES4" and header is None and off == 0:
                header = rec
            else:
                records.append(rec)
            off += 24 + size

    walk(0, len(data))
    if header is None:
        raise PluginError("not a TES4 plugin: the file does not start with a TES4 header record")
    plugin = Plugin(header, records, problems)
    seen = collections.Counter(r.formid for r in records)
    for fid, n in sorted(seen.items()):
        if n > 1:
            problems.append("FormID {:08X} appears {} times (types: {}); indexed by the first".format(
                fid, n, ",".join(r.type for r in records if r.formid == fid)))
    for r in records:
        if (r.formid >> 24) > len(plugin.masters):
            problems.append("{} {:08X} {}: master index {:02X} is past the {} master(s)".format(
                r.type, r.formid, r.edid, r.formid >> 24, len(plugin.masters)))
    return plugin


def masters(header_body: bytes):
    """The plugin's master file names, in order (index = the FormID high byte of an override)."""
    return [v.rstrip(b"\0").decode("latin-1") for t, v in subrecs(header_body) if t == "MAST"]


def subrecs(body: bytes):
    """Split a record body into [(tag, bytes)]. An XXXX subrecord carries the 32-bit size of the one after it."""
    out, i, big = [], 0, None
    while i + 6 <= len(body):
        t = body[i:i + 4].decode("latin-1")
        n = struct.unpack_from("<H", body, i + 4)[0]
        if t == "XXXX" and n == 4 and i + 10 <= len(body):
            big = struct.unpack_from("<I", body, i + 6)[0]
            i += 10
            continue
        if big is not None:
            n, big = big, None
        out.append((t, body[i + 6:i + 6 + n]))
        i += 6 + n
    return out
