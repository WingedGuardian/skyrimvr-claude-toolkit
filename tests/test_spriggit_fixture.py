"""fix-limits 4: CI runs the pinned Spriggit on a committed fixture, not only restores it.

The `.NET 10 toolchain` job in .github/workflows/ci.yml restored Spriggit and listed it, so a Spriggit that could
not serialize or deserialize on .NET 10 still went green. It now round-trips tests/fixtures/spriggit/
FixLimitsRoundTrip.esp: serialize -> deserialize -> the same records (the YAML of the rebuilt plugin equals the
first YAML) and, MEASURED 2026-10-13 on Windows with Spriggit 0.41.0 / .NET SDK 10.0.401, the same bytes.

These tests hold the fixture to the generator that documents it, and the CI step to its shape; CI itself runs it.
"""
from __future__ import annotations

import importlib.util
import struct

import pytest

from conftest import REPO

yaml = pytest.importorskip("yaml")
FIX = REPO / "tests" / "fixtures" / "spriggit"
CI = REPO / ".github" / "workflows" / "ci.yml"


def _gen():
    spec = importlib.util.spec_from_file_location("make_spriggit_fixture", FIX / "make_fixture.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_committed_fixture_is_what_its_generator_builds():
    assert (FIX / "FixLimitsRoundTrip.esp").read_bytes() == _gen().build()


def test_the_fixture_is_a_header_and_one_float_global():
    b = _gen().build()
    assert b[:4] == b"TES4" and b"GRUP" in b and b.count(b"GLOB") == 2           # group label + the record
    hedr = b.index(b"HEDR") + 6
    version, records, next_id = struct.unpack("<fiI", b[hedr:hedr + 12])
    assert round(version, 2) == 1.7 and records == 2 and next_id == 0x801
    assert b"FixLimitsRoundTrip\x00" in b and b.endswith(struct.pack("<f", 1.0))


def _dotnet_run_script():
    data = yaml.safe_load(CI.read_text(encoding="utf-8"))
    steps = data["jobs"]["dotnet"]["steps"]
    names = [s.get("name", "") for s in steps]
    restore = next(i for i, n in enumerate(names) if "restore" in n.lower())
    rt = [i for i, s in enumerate(steps) if "FixLimitsRoundTrip.esp" in str(s.get("run", ""))]
    assert len(rt) == 1, names
    return restore, rt[0], steps[rt[0]]["run"]


def test_the_dotnet_job_round_trips_the_fixture_after_restoring_spriggit():
    restore, rt, run = _dotnet_run_script()
    assert rt > restore
    assert "set -euo pipefail" in run
    ser = run.index("dotnet tool run spriggit -- serialize")
    de = run.index("dotnet tool run spriggit -- deserialize")
    again = run.index("dotnet tool run spriggit -- serialize", de)
    assert ser < de < again
    assert "--PackageName Spriggit.Yaml --PackageVersion 0.41.0" in run
    assert 'diff -r "$work/yaml1" "$work/yaml2"' in run                      # the record compare, required
    assert 'cmp -s "$work/FixLimitsRoundTrip.esp" "$work/out/FixLimitsRoundTrip.esp"' in run
    assert "EditorID: FixLimitsRoundTrip" in run                             # the record really was serialized


def test_the_round_trip_pins_the_same_spriggit_as_the_tool_manifest():
    import json
    manifest = json.loads((REPO / ".config" / "dotnet-tools.json").read_text(encoding="utf-8"))
    pinned = manifest["tools"]["spriggit.cli"]["version"]
    _r, _i, run = _dotnet_run_script()
    assert "--PackageVersion " + pinned in run
