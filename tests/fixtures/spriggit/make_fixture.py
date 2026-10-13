"""Writes tests/fixtures/spriggit/FixLimitsRoundTrip.esp: the smallest Skyrim SE plugin with one real record.

The CI `.NET 10 toolchain` job round-trips it through the pinned Spriggit (serialize, deserialize, serialize again)
and requires the same records back. It is synthetic and built byte by byte here, so its content is known exactly:
a TES4 header (HEDR 1.7, 2 records, next object 0x801, author `fix-limits`) and one GLOB group holding one float
global, FormID 0x000800, EditorID `FixLimitsRoundTrip`, value 1.0. Re-run this script to regenerate it; the test
`tests/test_spriggit_fixture.py` asserts the committed bytes equal what this builds.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "FixLimitsRoundTrip.esp"
FORM_VERSION = 44


def sub(sig: bytes, data: bytes) -> bytes:
    return sig + struct.pack("<H", len(data)) + data


def record(sig: bytes, form_id: int, data: bytes, flags: int = 0) -> bytes:
    return sig + struct.pack("<IIIIHH", len(data), flags, form_id, 0, FORM_VERSION, 0) + data


def group(label: bytes, body: bytes) -> bytes:
    return b"GRUP" + struct.pack("<I", 24 + len(body)) + label + struct.pack("<iII", 0, 0, 0) + body


def build() -> bytes:
    glob = record(b"GLOB", 0x000800, sub(b"EDID", b"FixLimitsRoundTrip\x00") + sub(b"FNAM", b"f")
                  + sub(b"FLTV", struct.pack("<f", 1.0)))
    tes4 = record(b"TES4", 0, sub(b"HEDR", struct.pack("<fiI", 1.7, 2, 0x801)) + sub(b"CNAM", b"fix-limits\x00"))
    return tes4 + group(b"GLOB", glob)


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / NAME
    out.write_bytes(build())
    print(out)
