"""tools/esp_records.py, esp-summary.py, esp-record-diff.py: the independent read-only plugin parser.

Every plugin here is synthetic, built byte by byte below: no game file is read. Each refusal or problem the parser
reports has a twin in tests/test_mutations.py.
"""
from __future__ import annotations

import importlib.util
import struct
import subprocess
import sys
import zlib

import pytest

from conftest import REPO

TOOLS = REPO / "tools"
_spec = importlib.util.spec_from_file_location("esp_records", TOOLS / "esp_records.py")
er = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(er)


def sub(tag, data):
    if len(data) > 0xFFFF:                                   # the engine's oversize form: XXXX carries the real size
        return b"XXXX" + struct.pack("<HI", 4, len(data)) + tag.encode() + struct.pack("<H", 0) + data
    return tag.encode() + struct.pack("<H", len(data)) + data


def rec(typ, fid, body, flags=0, compress=False):
    if compress:
        body = struct.pack("<I", len(body)) + zlib.compress(body)
        flags |= er.COMPRESSED
    return typ.encode() + struct.pack("<IIIII", len(body), flags, fid, 0, 0) + body


def grup(label, content):
    return b"GRUP" + struct.pack("<I", 24 + len(content)) + label.encode() + struct.pack("<III", 0, 0, 0) + content


def edid(name):
    return sub("EDID", name.encode() + b"\0")


def plugin(masters=("Skyrim.esm",), records=(), flags=0, extra_header=b""):
    head = sub("HEDR", struct.pack("<fII", 1.71, len(records), 0x800)) + extra_header
    for m in masters:
        head += sub("MAST", m.encode() + b"\0") + sub("DATA", b"\0" * 8)
    groups = {}
    for r in records:
        groups.setdefault(r[:4].decode(), []).append(r)
    return rec("TES4", 0, head, flags) + b"".join(grup(t, b"".join(rs)) for t, rs in groups.items())


WEAP_OVERRIDE = rec("WEAP", 0x00012EB7, edid("IronSword") + sub("DATA", b"\x01" * 10))
WEAP_NEW = rec("WEAP", 0x01000800, edid("MySword") + sub("DATA", b"\x02" * 10))
SPEL_NEW = rec("SPEL", 0x01000801, edid("MySpell"))


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def cli(tool, *args):
    r = subprocess.run([sys.executable, "-B", str(TOOLS / tool), *map(str, args)], capture_output=True, text=True,
                       timeout=60)
    return r.returncode, r.stdout, r.stderr


# --------------------------------------------------------------------------- parser

def test_new_records_and_overrides_are_told_apart_by_the_master_index(tmp_path):
    p = er.read(write(tmp_path, "a.esp", plugin(records=[WEAP_OVERRIDE, WEAP_NEW, SPEL_NEW])))
    assert p.masters == ["Skyrim.esm"]
    assert [(r.edid, p.is_override(r)) for r in p.records] == [("IronSword", True), ("MySword", False),
                                                                ("MySpell", False)]
    assert p.master_of(p.records[0]) == "Skyrim.esm" and p.problems == []


def test_compressed_records_and_oversize_subrecords_are_read(tmp_path):
    big = b"z" * 70000
    data = plugin(records=[rec("NPC_", 0x01000802, edid("Packed") + sub("DATA", big), compress=True)])
    p = er.read(write(tmp_path, "c.esp", data))
    (r,) = p.records
    assert r.edid == "Packed" and r.flags & er.COMPRESSED == 0
    assert dict(er.subrecs(r.body))["DATA"] == big, "an XXXX-sized subrecord was split wrongly"


def test_a_duplicate_formid_is_reported_not_silently_collapsed(tmp_path):
    dup = rec("MISC", 0x01000800, edid("Clash"))
    p = er.read(write(tmp_path, "d.esp", plugin(records=[WEAP_NEW, dup])))
    assert len(p.records) == 2
    assert any("01000800 appears 2 times" in x for x in p.problems), p.problems


def test_a_compressed_record_that_does_not_inflate_is_reported(tmp_path):
    bad = b"MISC" + struct.pack("<IIIII", 12, er.COMPRESSED, 0x01000800, 0, 0) + struct.pack("<I", 50) + b"notzlib!"
    p = er.read(write(tmp_path, "z.esp", plugin(records=[]) + grup("MISC", bad)))
    assert any("does not inflate" in x for x in p.problems), p.problems


def test_a_master_index_past_the_master_list_is_reported(tmp_path):
    stray = rec("MISC", 0x05000800, edid("Stray"))
    p = er.read(write(tmp_path, "m.esp", plugin(records=[stray])))
    assert any("master index 05" in x for x in p.problems), p.problems


REFUSALS = {
    "not-a-plugin": (lambda good: b"PK\x03\x04" + b"\0" * 40, "offset"),
    "no-tes4-header": (lambda good: b"TES3" + good[4:], "not a TES4 plugin"),
    "truncated": (lambda good: good[:-5], "offset"),
    # a GRUP smaller than its own header would walk in place forever
    "grup-too-small": (lambda good: good + b"GRUP" + struct.pack("<I", 8) + b"\0" * 16, "impossible size"),
    # the group fits in the file, the record inside it does not fit in the group
    "record-past-its-group": (lambda good: good + grup("MISC", b"MISC" + struct.pack("<IIIII", 60, 0, 0x01000900, 0, 0)
                                                       + edid("Short")), "runs past its group"),
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_a_file_that_cannot_be_walked_is_refused_with_its_offset(tmp_path, case):
    make, why = REFUSALS[case]
    with pytest.raises(er.PluginError, match=why):
        er.read(write(tmp_path, "x.esp", make(plugin(records=[WEAP_NEW]))))


# --------------------------------------------------------------------------- esp-summary

def test_summary_counts_new_records_and_overrides_by_type_and_master(tmp_path):
    p = write(tmp_path, "My Mod.esp", plugin(records=[WEAP_OVERRIDE, WEAP_NEW, SPEL_NEW], flags=0x200))
    rc, out, err = cli("esp-summary.py", p, "--list-overrides")
    assert rc == 0, out + err
    assert "header flags: 00000200 (ESL)" in out
    assert "new: SPEL 1, WEAP 1" in out and "overrides: WEAP 1" in out
    assert "overrides by master: Skyrim.esm 1" in out
    assert "OVERRIDE 00012EB7 WEAP IronSword (Skyrim.esm)" in out
    assert sorted(x.name for x in tmp_path.iterdir()) == ["My Mod.esp"], "a read-only tool wrote a file"


def test_summary_exits_1_on_a_problem_and_2_on_a_file_it_cannot_walk(tmp_path):
    dup = write(tmp_path, "d.esp", plugin(records=[WEAP_NEW, rec("MISC", 0x01000800, edid("Clash"))]))
    rc, out, _ = cli("esp-summary.py", dup)
    assert rc == 1 and "PROBLEM FormID 01000800" in out
    rc, _, err = cli("esp-summary.py", write(tmp_path, "x.esp", b"nope" * 10))
    assert rc == 2 and "esp-summary:" in err and "offset" in err


# --------------------------------------------------------------------------- esp-record-diff

def test_identical_plugins_diff_clean(tmp_path):
    data = plugin(records=[WEAP_OVERRIDE, WEAP_NEW])
    rc, out, err = cli("esp-record-diff.py", write(tmp_path, "a.esp", data), write(tmp_path, "b.esp", data))
    assert rc == 0, out + err
    assert "byte-identical: yes" in out and "differences: 0" in out


def test_a_recompressed_but_otherwise_equal_plugin_is_not_a_change(tmp_path):
    body = edid("MySword") + sub("DATA", b"\x02" * 10)
    a = plugin(records=[rec("WEAP", 0x01000800, body)])
    b = plugin(records=[rec("WEAP", 0x01000800, body, compress=True)])
    rc, out, err = cli("esp-record-diff.py", write(tmp_path, "a.esp", a), write(tmp_path, "b.esp", b))
    assert rc == 0, out + err
    assert "byte-identical: no" in out and "differences: 0" in out


def test_diff_names_added_removed_and_changed_records_and_subrecords(tmp_path):
    edited = rec("WEAP", 0x00012EB7, edid("IronSword") + sub("DATA", b"\x09" * 10))
    a = plugin(records=[WEAP_OVERRIDE, WEAP_NEW])
    b = plugin(records=[edited, SPEL_NEW])
    rc, out, err = cli("esp-record-diff.py", write(tmp_path, "a.esp", a), write(tmp_path, "b.esp", b))
    assert rc == 1, out + err
    assert "REMOVED 01000800 WEAP MySword" in out
    assert "ADDED   01000801 SPEL MySpell" in out
    assert "CHANGED 00012EB7 WEAP IronSword  subrecords: DATA" in out


def test_diff_sees_a_header_change_and_a_reordered_record(tmp_path):
    a = plugin(records=[rec("WEAP", 0x01000800, edid("S") + sub("DATA", b"1") + sub("DESC", b"2"))])
    b = plugin(masters=("Skyrim.esm", "Update.esm"),
               records=[rec("WEAP", 0x01000800, edid("S") + sub("DESC", b"2") + sub("DATA", b"1"))])
    rc, out, _ = cli("esp-record-diff.py", write(tmp_path, "a.esp", a), write(tmp_path, "b.esp", b))
    assert rc == 1
    assert "HEADER  TES4 changed  subrecords: DATA,MAST" in out
    assert "subrecords: (same subrecords, different order)" in out


def test_diff_with_a_duplicate_formid_is_inconclusive_not_clean(tmp_path):
    data = plugin(records=[WEAP_NEW, rec("MISC", 0x01000800, edid("Clash"))])
    rc, out, _ = cli("esp-record-diff.py", write(tmp_path, "a.esp", data), write(tmp_path, "b.esp", data))
    assert rc == 1 and "INCONCLUSIVE" in out


def test_the_tools_never_write():
    for name in ("esp_records.py", "esp-summary.py", "esp-record-diff.py"):
        src = (TOOLS / name).read_text(encoding="utf-8")
        for call in ('"w"', "'w'", '"wb"', "'wb'", '"a"', "write_text", "write_bytes", ".write("):
            assert call not in src, (name, call)
