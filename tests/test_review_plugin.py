"""P1 retained malformed subrecord probes, entirely synthetic and read only."""
import struct

import pytest
from test_esp_records import er, rec, edid, plugin, write, cli


XXXX = b"XXXX" + struct.pack("<HI", 4, 100)
BAD = {
    "overrun": b"EDID" + struct.pack("<H", 12) + b"x",
    "trailing-header": edid("ValidPrefix") + b"DATA",
    "dangling-xxxx": XXXX,
    "short-xxxx": b"XXXX" + struct.pack("<H", 4) + b"x",
    "wrong-xxxx-size": b"XXXX" + struct.pack("<HI", 0, 1) + b"DATA" + struct.pack("<H", 0) + b"x",
    "double-xxxx": XXXX + b"XXXX" + struct.pack("<HI", 4, 1) + b"DATA" + struct.pack("<H", 0) + b"x",
    "extended-overrun": XXXX + b"DATA" + struct.pack("<H", 0) + b"x",
    "after-edid": edid("ValidPrefix") + b"DATA" + struct.pack("<H", 12) + b"x",
}


@pytest.mark.parametrize("case", sorted(BAD))
def test_malformed_subrecord_is_never_a_clean_record(case):
    parsed = er.parse(plugin(masters=(), records=[rec("STAT", 0x800, BAD[case])]))
    assert parsed.problems, "truncated subrecord or unresolved XXXX was accepted as clean"
    assert any("offset" in problem for problem in parsed.problems)


@pytest.mark.parametrize("case", ["overrun", "dangling-xxxx"])
def test_standalone_subrecord_reader_refuses_malformed_bytes(case):
    with pytest.raises(er.PluginError, match="offset"):
        er.subrecs(BAD[case])


@pytest.mark.parametrize("where", ["header", "record", "compressed"])
def test_both_plugin_reports_keep_malformed_input_inconclusive(tmp_path, where):
    def contents(suffix):
        body = BAD["overrun"] + suffix
        if where == "header":
            return plugin(masters=(), extra_header=body)
        return plugin(masters=(), records=[rec("STAT", 0x800, body, compress=where == "compressed")])
    a, b = write(tmp_path, "a.esp", contents(b"")), write(tmp_path, "b.esp", contents(b"y"))
    before = a.read_bytes(), b.read_bytes()
    rc, out, err = cli("esp-summary.py", a)
    assert rc == 1 and "PROBLEM" in out, out + err
    for other in (a, b):
        rc, out, err = cli("esp-record-diff.py", a, other)
        assert rc == 1 and "INCONCLUSIVE" in out and "PROBLEM" in out, out + err
        if other != a:
            assert "subrecords: (malformed subrecords)" in out
    assert (a.read_bytes(), b.read_bytes()) == before
