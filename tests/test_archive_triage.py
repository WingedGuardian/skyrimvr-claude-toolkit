"""tools/archive-triage.py: what a mod archive would change in the game folder, before anything is installed.

Most tests drive the tool through a fake 7-Zip (tests/fake_7z.py) so every listing shape can be authored: a
missing CRC, a file whose hex attribute word contains a D, a CR or an escape sequence inside an entry name. One
test runs the real 7-Zip when it is installed, to hold the parser to the format 7-Zip actually prints. The
archives, mods and DLLs here are made up.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import zipfile
import zlib

import pytest

from conftest import REPO

TOOL = REPO / "tools" / "archive-triage.py"
FAKE = REPO / "tests" / "fake_7z.py"

_spec = importlib.util.spec_from_file_location("archive_triage", TOOL)
triage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(triage)


@pytest.mark.parametrize("path, expected", [
    # A FOMOD option folder named like a Data root ("mcm") is a prefix, not Data/mcm.
    ("mcm/ExampleMod.esp", ("mcm", "ExampleMod.esp", "data")),
    ("mcm/MCM/Config/ExampleMod/config.json", ("mcm", "MCM/Config/ExampleMod/config.json", "data")),
    ("mcm/Scripts/EXM_Actions.pex", ("mcm", "Scripts/EXM_Actions.pex", "data")),
    ("mcm/SEQ/ExampleMod.seq", ("mcm", "SEQ/ExampleMod.seq", "data")),
    ("mcmlog_on/MCM/Settings/ExampleMod.ini", ("mcmlog_on", "MCM/Settings/ExampleMod.ini", "data")),
    # Real Data roots.
    ("MCM/Config/OtherMod/settings.ini", ("", "MCM/Config/OtherMod/settings.ini", "data")),
    ("Scripts/Source/foo.psc", ("", "Scripts/Source/foo.psc", "data")),
    ("Source/Scripts/foo.psc", ("", "Source/Scripts/foo.psc", "data")),
    ("core/SKSE/Plugins/ExampleFramework.dll", ("core", "SKSE/Plugins/ExampleFramework.dll", "data")),
    ("Core/Sound/FX/EXM/splash.wav", ("Core", "Sound/FX/EXM/splash.wav", "data")),
    ("Optional-MCM/ExampleMod.esp", ("Optional-MCM", "ExampleMod.esp", "data")),
    ("Data/MCM/Config/x/config.json", ("", "MCM/Config/x/config.json", "data")),
    ("ExampleMod.esp", ("", "ExampleMod.esp", "data")),
    # PBR patcher instructions are Data roots (PGPatcher reads them from Data/); dropping them silently
    # installs PBR textures that no mesh ever uses (TomatoRim PBR, 2026-10-19).
    ("Pack 2k/PBRNifPatcher/landscape/rocks.json", ("Pack 2k", "PBRNifPatcher/landscape/rocks.json", "data")),
    ("Pack 2k/PBRTextureSets/TXSTRoad.json", ("Pack 2k", "PBRTextureSets/TXSTRoad.json", "data")),
    ("PBRMaterialObjects/Snow.json", ("", "PBRMaterialObjects/Snow.json", "data")),
    ("Output/LightPlacer/cs light/clutter.json", ("Output", "LightPlacer/cs light/clutter.json", "data")),
    # Vanilla nests root names under asset roots: meshes/interface is Data/meshes/interface, not an option
    # folder called "meshes" (Faultier's PBR Clutter, PGPatcher output, 2026-10-19).
    ("Pack 2k/meshes/interface/lockpicking/lockpick01.nif",
     ("Pack 2k", "meshes/interface/lockpicking/lockpick01.nif", "data")),
    ("textures/interface/exm/icon.dds", ("", "textures/interface/exm/icon.dds", "data")),
    # Binaries outside any Data root land in the game folder when installed as packaged.
    ("skse64_loader.exe", ("", "skse64_loader.exe", "game")),
    ("Main/d3d11.dll", ("Main", "d3d11.dll", "game")),
    # Not installed content.
    ("fomod/ModuleConfig.xml", (None, None, None)),
    ("readme.txt", (None, None, None)),
    # Game-folder content: ENB / ReShade folders and files, and the Root/ folder convention.
    ("Main/enbseries/effect.fx", ("Main", "enbseries/effect.fx", "game")),
    ("reshade-shaders/Shaders/x.fx", ("", "reshade-shaders/Shaders/x.fx", "game")),
    ("Preset A/enblocal.ini", ("Preset A", "enblocal.ini", "game")),
    ("Root/skse64_loader.exe", ("", "skse64_loader.exe", "game")),
    ("Core/Root/Tools/thing.cfg", ("Core", "Tools/thing.cfg", "game")),
])
def test_map_entry(path, expected):
    assert triage.map_entry(path) == expected


# --------------------------------------------------------------------------- the fake-7z harness

def crc(text):
    return "{:08X}".format(zlib.crc32(text.encode("utf-8")) & 0xFFFFFFFF)


@pytest.fixture
def sevenz(tmp_path):
    """A launcher for tests/fake_7z.py that subprocess can execute on Windows (a .cmd) and POSIX (a sh script)."""
    d = tmp_path / "fake7z"
    d.mkdir()
    if os.name == "nt":
        p = d / "7z.cmd"
        p.write_text('@"{}" "{}" %*\r\n'.format(sys.executable, FAKE), encoding="utf-8")
    else:
        p = d / "7z"
        p.write_text("#!/bin/sh\nexec '{}' '{}' \"$@\"\n".format(sys.executable, FAKE), encoding="utf-8")
        p.chmod(0o755)
    return p


def make_game(tmp_path, exe="SkyrimVR.exe", files=None):
    game = tmp_path / "game"
    (game / "Data").mkdir(parents=True)
    (game / exe).write_text("", encoding="utf-8")
    for rel, text in (files or {}).items():
        p = game / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="")
    return game


def make_archive(tmp_path, name, entries):
    p = tmp_path / name
    p.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    return p


def run(*args, sevenz=None, game=None, env=None):
    e = dict(os.environ)
    for k in ("SEVENZIP", "SKYRIM_GAME_ROOT"):
        e.pop(k, None)
    if sevenz is not None:
        e["SEVENZIP"] = str(sevenz)
    if game is not None:
        e["SKYRIM_GAME_ROOT"] = str(game)
    e.update(env or {})
    r = subprocess.run([sys.executable, "-B", str(TOOL), *map(str, args)], capture_output=True, timeout=120, env=e)
    return subprocess.CompletedProcess(r.args, r.returncode, r.stdout.decode("utf-8", "replace"),
                                       r.stderr.decode("utf-8", "replace"))


def row(out, needle):
    return next((ln for ln in out.splitlines() if needle in ln), "")


INSTALLED = {"Data/ExampleMod.esp": "plugin v1", "Data/SKSE/Plugins/ExampleFramework.ini": "user=1",
             "Data/textures/exm/a.dds": "tex"}
ENTRIES = [
    {"path": "ExampleMod/Data/ExampleMod.esp", "data": "plugin v2"},                         # CHANGED
    {"path": "ExampleMod/Data/SKSE/Plugins/ExampleFramework.ini", "data": "user=1"},         # SAME
    {"path": "ExampleMod/Data/textures/exm/a.dds", "data": "tex"},                           # SAME
    {"path": "ExampleMod/Data/textures/exm/b.dds", "data": "tex2"},                          # NEW
    {"path": "ExampleMod/Data/SKSE/Plugins/ExampleFramework.dll", "data": "MZ SKSEPlugin_Load CommonLibVR"},  # NEW
    {"path": "ExampleMod/readme.txt", "data": "read me"},                                    # not installed
    {"path": "ExampleMod/Data", "folder": True, "attributes": "D 00000010"},                 # a folder
]


def test_new_same_changed_and_the_rows_sum(tmp_path, sevenz):
    game = make_game(tmp_path, files=INSTALLED)
    r = run(make_archive(tmp_path, "ExampleMod.7z", ENTRIES), sevenz=sevenz, game=game)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[ExampleMod]  NEW 2 | SAME 2 | CHANGED 1 | NO CRC 0" in r.stdout, r.stdout
    assert "accounting: 6 listed = 2 NEW + 2 SAME + 1 CHANGED + 0 NO CRC + 1 not installed" in r.stdout, r.stdout
    assert "accounting OK" in r.stdout
    assert "CHANGED Data/ExampleMod.esp" in r.stdout and "! plugin:" in row(r.stdout, "CHANGED Data/ExampleMod.esp")
    assert "NEW     Data/SKSE/Plugins/ExampleFramework.dll" in r.stdout, r.stdout
    assert "NEW     1 file(s) -> Data/textures/exm/" in r.stdout, r.stdout


def test_all_lists_every_file_and_every_skipped_entry(tmp_path, sevenz):
    game = make_game(tmp_path, files=INSTALLED)
    r = run(make_archive(tmp_path, "ExampleMod.7z", ENTRIES), "--all", sevenz=sevenz, game=game)
    for shown in ("SAME    Data/textures/exm/a.dds", "NEW     Data/textures/exm/b.dds",
                  "SAME    Data/SKSE/Plugins/ExampleFramework.ini"):
        assert shown in r.stdout, r.stdout


def test_everything_outside_data_is_listed_by_default(tmp_path, sevenz):
    """Outside Data/ is never summarised: ENB folders, a Root/ folder and entries under no Data root are each listed
    and flagged. Only the FOMOD installer's own files are summarised (and listed with --all)."""
    game = make_game(tmp_path)
    enb = [{"path": "Main/enbseries/fx{}.fx".format(i), "data": str(i)} for i in range(12)]
    arc = make_archive(tmp_path, "x.7z", enb + [
        {"path": "Main/Root/Tools/thing.cfg", "data": "c"},
        {"path": "Main/readme.txt", "data": "r"},
        {"path": "Main/odd-folder/file.bin", "data": "b"},
        {"path": "fomod/ModuleConfig.xml", "data": "<config/>"},
        {"path": "Main/Data/textures/a.dds", "data": "t1"},
        {"path": "Main/Data/textures/b.dds", "data": "t2"},
    ])
    r = run(arc, sevenz=sevenz, game=game)
    assert r.returncode == 0, r.stdout + r.stderr
    for i in range(12):
        line = row(r.stdout, "fx{}.fx".format(i) + "  !!")
        assert str(game / "enbseries" / "fx{}.fx".format(i)) in line and "outside Data/ -- check this is intended" in line
    assert "outside Data/" in row(r.stdout, "thing.cfg") and str(game / "Tools" / "thing.cfg") in r.stdout
    for name in ("Main/readme.txt", "Main/odd-folder/file.bin"):
        line = row(r.stdout, name)
        assert line.lstrip().startswith("?") and "outside Data/ -- check this is intended" in line, r.stdout
    assert "ModuleConfig.xml" not in r.stdout
    assert "not under a Data root: 3 (1 of them the FOMOD installer's own files)" in r.stdout, r.stdout
    assert "NEW     2 file(s) -> Data/textures/" in r.stdout, "in-Data assets stay summarised"
    assert "-       fomod/ModuleConfig.xml" in run(arc, "--all", sevenz=sevenz, game=game).stdout


def test_a_missing_crc_is_no_crc_never_changed(tmp_path, sevenz):
    game = make_game(tmp_path, files=INSTALLED)
    arc = make_archive(tmp_path, "x.7z", [{"path": "Data/ExampleMod.esp", "data": "plugin v1", "crc": ""}])
    r = run(arc, sevenz=sevenz, game=game)
    assert "NEW 0 | SAME 0 | CHANGED 0 | NO CRC 1" in r.stdout, r.stdout
    assert "accounting OK" in r.stdout


def test_a_file_whose_hex_attributes_contain_d_is_not_dropped_as_a_folder(tmp_path, sevenz):
    game = make_game(tmp_path)
    arc = make_archive(tmp_path, "x.7z", [{"path": "Data/ExampleMod.esp", "data": "p", "attributes": " 01D00000"},
                                          {"path": "Data/Sub", "attributes": "DvPUM 41ED0000", "folder": False}])
    r = run(arc, sevenz=sevenz, game=game)
    assert "NEW     Data/ExampleMod.esp" in r.stdout, r.stdout
    assert "accounting: 1 listed" in r.stdout, r.stdout


def test_destinations_outside_data_are_shown_and_flagged_not_refused(tmp_path, sevenz):
    game = make_game(tmp_path)
    arc = make_archive(tmp_path, "x.7z", [
        {"path": "Main/d3d11.dll", "data": "wrapper"},
        {"path": "Main/Data/../../escape.dll", "data": "e"},
        {"path": "Main/Data/Inside.esp", "data": "p"},
    ])
    r = run(arc, sevenz=sevenz, game=game)
    assert r.returncode == 0, r.stdout + r.stderr
    d3d = row(r.stdout, "d3d11.dll  !!")
    assert str(game / "d3d11.dll") in d3d and "outside Data/ -- check this is intended" in d3d, r.stdout
    esc = row(r.stdout, "escape.dll  !!")
    assert str(tmp_path / "escape.dll") in esc and "outside Data/" in esc, r.stdout
    assert "outside Data/" not in row(r.stdout, "Data/Inside.esp")
    assert "!! 2 destination(s) outside Data/" in r.stdout


def test_control_characters_in_entry_names_are_escaped(tmp_path, sevenz):
    """A CR inside a name would rewind the line and overwrite what the reader sees; an ESC would recolour it."""
    game = make_game(tmp_path)
    arc = make_archive(tmp_path, "x.7z", [{"path": "Data/Fine\rEvil.esp", "data": "a"},
                                          {"path": "Data/Red\x1b[31mText.esp", "data": "b"}])
    r = run(arc, sevenz=sevenz, game=game)
    assert "\x1b" not in r.stdout and "\r" not in r.stdout.replace("\r\n", "\n"), repr(r.stdout)
    assert "Data/Fine\\x0dEvil.esp" in r.stdout, r.stdout
    assert "Data/Red\\x1b[31mText.esp" in r.stdout, r.stdout
    assert "accounting: 2 listed" in r.stdout, "a CR inside a name split one entry into two"


def test_an_archive_argument_with_a_trailing_cr_is_found(tmp_path, sevenz):
    game = make_game(tmp_path)
    arc = make_archive(tmp_path, "x.7z", [{"path": "Data/ExampleMod.esp", "data": "p"}])
    r = run(str(arc) + "\r", sevenz=sevenz, game=game)
    assert r.returncode == 0 and "NEW     Data/ExampleMod.esp" in r.stdout, r.stdout + r.stderr
    # The name itself, not only the result: cmd.exe (the fake's .cmd launcher) drops a CR on its own, so on
    # Windows the listing alone would pass with the CR still attached.
    assert "x.7z" in r.stdout.splitlines() and "x.7z\\x0d" not in r.stdout, r.stdout


def test_an_unreadable_archive_is_exit_2_and_the_others_still_run(tmp_path, sevenz):
    game = make_game(tmp_path)
    bad = tmp_path / "broken.7z"
    bad.write_bytes(b"not an archive")
    good = make_archive(tmp_path, "good.7z", [{"path": "Data/ExampleMod.esp", "data": "p"}])
    r = run(bad, good, sevenz=sevenz, game=game)
    assert r.returncode == 2, r.stdout
    assert "ERROR listing archive" in row(r.stdout, "ERROR") and "Cannot open the file as archive" in r.stdout
    assert "NEW     Data/ExampleMod.esp" in r.stdout
    assert "2 archive(s); 1 could not be listed" in r.stdout


def test_a_folder_argument_collects_its_archives(tmp_path, sevenz):
    game = make_game(tmp_path)
    folder = tmp_path / "downloads"
    folder.mkdir()
    for n in ("a.7z", "b.zip", "c.rar"):
        make_archive(folder, n, [{"path": "Data/" + n + ".esp", "data": "p"}])
    (folder / "notes.txt").write_text("x", encoding="utf-8")
    r = run(folder, sevenz=sevenz, game=game)
    assert r.returncode == 0 and "3 archive(s); 0 could not be listed" in r.stdout, r.stdout


def test_json_output_carries_every_entry(tmp_path, sevenz):
    game = make_game(tmp_path, files=INSTALLED)
    out = tmp_path / "out.json"
    run(make_archive(tmp_path, "ExampleMod.7z", ENTRIES), "--json", out, sevenz=sevenz, game=game)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data[0]["tally"] == {"NEW": 2, "SAME": 2, "CHANGED": 1, "NO CRC": 0}
    assert data[0]["unmapped"] == ["ExampleMod/readme.txt"]


def test_vr_markers_are_checked_on_vr(tmp_path, sevenz):
    game = make_game(tmp_path, exe="SkyrimVR.exe")
    r = run(make_archive(tmp_path, "x.7z", ENTRIES), sevenz=sevenz, game=game)
    dll = row(r.stdout, "DLL ExampleMod/Data/SKSE/Plugins/ExampleFramework.dll")
    assert "VR markers: CommonLibVR" in dll and "SKSEPlugin_Load" in dll, r.stdout


def test_the_vr_heuristic_is_skipped_on_se_and_says_so(tmp_path, sevenz):
    game = make_game(tmp_path, exe="SkyrimSE.exe")
    r = run(make_archive(tmp_path, "x.7z", ENTRIES), sevenz=sevenz, game=game)
    dll = row(r.stdout, "DLL ExampleMod/Data/SKSE/Plugins/ExampleFramework.dll")
    assert "VR-marker check skipped: this install is SE/AE" in dll, r.stdout
    assert "no VR markers" not in r.stdout and "VR markers:" not in r.stdout


def test_an_unidentified_runtime_labels_the_heuristic(tmp_path, sevenz):
    game = make_game(tmp_path, exe="Other.exe")
    r = run(make_archive(tmp_path, "x.7z", ENTRIES), sevenz=sevenz, game=game)
    assert "[VR-only heuristic; the game runtime was not identified]" in r.stdout, r.stdout


@pytest.mark.parametrize("args", [[], ["--bogus", "x.7z"], ["--json"], ["--data"]])
def test_bad_usage_is_exit_2(tmp_path, sevenz, args):
    r = run(*args, sevenz=sevenz, game=make_game(tmp_path))
    assert r.returncode == 2, r.stdout + r.stderr


def test_a_missing_7zip_is_exit_2_naming_the_override(tmp_path):
    r = run("x.7z", sevenz=tmp_path / "no-such-7z.exe", game=make_game(tmp_path))
    assert r.returncode == 2 and "SEVENZIP" in r.stderr, r.stderr


def test_a_missing_data_folder_is_exit_2_naming_the_source(tmp_path, sevenz):
    r = run("x.7z", sevenz=sevenz, game=tmp_path / "nowhere")
    assert r.returncode == 2 and "SKYRIM_GAME_ROOT" in r.stderr and "--data" in r.stderr, r.stderr


def test_data_override_compares_against_another_folder(tmp_path, sevenz):
    game = make_game(tmp_path)
    other = tmp_path / "mods" / "ExampleMod"
    other.mkdir(parents=True)
    (other / "ExampleMod.esp").write_text("plugin v1", encoding="utf-8")
    arc = make_archive(tmp_path, "x.7z", [{"path": "Data/ExampleMod.esp", "data": "plugin v1"}])
    r = run(arc, "--data", other, sevenz=sevenz, game=game)
    assert "NEW 0 | SAME 1" in r.stdout and "compared against: " + str(other) in r.stdout, r.stdout


# --------------------------------------------------------------------------- the real 7-Zip, when present

def test_the_parser_reads_what_real_7zip_prints(tmp_path):
    sys.path.insert(0, str(REPO / "tools"))
    import skyrim_paths
    real, _ = skyrim_paths.find_7zip()
    if not real:
        pytest.skip("7-Zip is not installed here; the fake-7z tests above still cover the logic")
    game = make_game(tmp_path, files={"Data/ExampleMod.esp": "old", "Data/Same.esp": "same"})
    arc = tmp_path / "real.zip"
    with zipfile.ZipFile(arc, "w") as z:
        z.writestr("Pack/Data/ExampleMod.esp", "new")
        z.writestr("Pack/Data/Same.esp", "same")
        z.writestr("Pack/Data/Fresh.esp", "fresh")
        z.writestr("Pack/Data/", "")
    r = run(arc, sevenz=real, game=game)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[Pack]  NEW 1 | SAME 1 | CHANGED 1 | NO CRC 0" in r.stdout, r.stdout
    assert "accounting OK" in r.stdout
