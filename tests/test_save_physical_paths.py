"""Synthetic Windows saves: comparison aliases must never select backup bytes."""
import os
from pathlib import Path
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from modding_harness import contracts, preservation
from skyguard import paths, portable, saves, targets

pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual Windows namespace I/O")


def quoted(value):
    return "'" + str(value).replace("'", "''") + "'"


@pytest.fixture
def owned(tmp_path):
    root = tmp_path / "toolkit"
    root.mkdir()
    profile = tmp_path / "profile"
    normal = profile / "Saves"
    normal.mkdir(parents=True)
    (root / "skyrim-paths.env").write_text("SKYRIM_CONFIG_DIR=" + quoted(profile) + "\n", encoding="utf-8")
    (root / ".modding-agent-harness").mkdir()
    (root / ".modding-agent-harness/config.json").write_text('{"v":1,"save_reserve_bytes":0}', encoding="utf-8")
    directories, members = [], []

    def raw_folder(suffix):
        folder = Path("\\\\?\\" + str(normal) + suffix)
        folder.mkdir()
        directories.append(folder)
        return folder

    def pair(folder, stem, content):
        result = tuple(folder / (stem + ext) for ext in (".ess", ".skse"))
        for member in result:
            member.write_bytes(content + member.suffix.encode())
            if str(member).startswith("\\\\?\\"):
                members.append(member)
        return result

    try:
        yield root, profile, normal, raw_folder, pair
    finally:
        # Only the exact owned raw files and empty directories created above.
        for member in reversed(members):
            member.unlink()
        for folder in reversed(directories):
            folder.rmdir()


def fire(root, target, route, *, cwd=None):
    cwd = str(cwd or root)
    q = quoted(target)
    commands = {
        "write": "Set-Content -LiteralPath " + q + " -Value fixture",
        "delete": "Remove-Item -LiteralPath " + q,
        "move": "Move-Item -LiteralPath " + q + " -Destination " + quoted(root / "away.bin"),
        "copy": "Copy-Item -LiteralPath " + quoted(root / "input.bin") + " -Destination " + q,
        "dotnet": "[IO.File]::WriteAllText(" + q + ", 'fixture')",
        "python": 'python -c "open(r' + quoted(target) + ", 'wb').write(b'fixture')\"",
        "python-escaped": "python -c " + quoted("open(" + repr(str(target)) + ", 'wb').write(b'fixture')"),
        "python-path": "python -c " + quoted("from pathlib import Path; Path(" + repr(str(target)) + ").write_bytes(b'fixture')"),
        "recycle": "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile(" + q + ", 'OnlyErrorDialogs', 'SendToRecycleBin')",
    }
    command = commands.get(route, "")
    if route == "cd":
        command = "Set-Location " + quoted(Path(target).parent) + "; Set-Content -LiteralPath " + quoted(Path(target).name) + " -Value fixture"
    action = contracts.Action("generic", "shell" if command else "edit", "shell" if command else "files",
        cwd=cwd, shell="powershell" if command else "", command=command,
        paths=() if command else (str(target),), operations=() if command else ("write",),
        session_id="owned-physical", tool_call_id=route)
    return portable.check(action, root)[0]


def assert_pair(root, outcome, pair):
    assert outcome.result.decision == "allow", outcome.result.as_dict()
    assert outcome.result.preservation == "verified" and len(outcome.transactions) == 1
    store = preservation.SaveStore(root, reserve=0)
    record = store.load(outcome.transactions[0])
    members = [m for g in record["groups"] for m in g["members"] if not m["missing"]]
    assert len(members) == 2
    for member in members:
        expected = next(p for p in pair if p.suffix == Path(member["path"]).suffix)
        assert os.path.samefile(member["path"], expected)
        assert (store.root / "blobs" / member["sha256"]).read_bytes() == expected.read_bytes()


@pytest.mark.parametrize("suffix", [".", " "])
@pytest.mark.parametrize("route", ["files", "write", "delete", "move", "copy", "dotnet", "python", "python-escaped", "python-path", "recycle", "cd"])
def test_raw_namespace_preserves_requested_pair_not_folded_peer(owned, suffix, route):
    root, _, normal, raw_folder, make_pair = owned
    peer = make_pair(normal, "Distinct", b"NORMAL PEER")
    requested = make_pair(raw_folder(suffix), "Distinct", b"RAW REQUESTED")
    assert not os.path.samefile(peer[0], requested[0])
    before = {str(p): p.read_bytes() for p in (*peer, *requested)}
    outcome = fire(root, requested[0], route)
    assert_pair(root, outcome, requested)
    assert before == {str(p): p.read_bytes() for p in (*peer, *requested)}


@pytest.mark.parametrize("route", ["files", "write"])
def test_relative_file_and_shell_keep_raw_namespace_cwd(owned, route):
    root, _, normal, raw_folder, make_pair = owned
    make_pair(normal, "Distinct", b"PEER")
    requested = make_pair(raw_folder("."), "Distinct", b"RAW")
    assert_pair(root, fire(root, "Distinct.ess", route, cwd=requested[0].parent), requested)


def test_copy_to_raw_folder_preserves_destination_pair(owned):
    root, _, normal, raw_folder, make_pair = owned
    make_pair(normal, "Distinct", b"PEER")
    requested = make_pair(raw_folder("."), "Distinct", b"RAW")
    command = "Copy-Item -LiteralPath " + quoted(root / "Distinct.ess") + " -Destination " + quoted(str(requested[0].parent) + "\\")
    action = contracts.Action("generic", "shell", "shell", shell="powershell", cwd=str(root), command=command)
    assert_pair(root, portable.check(action, root)[0], requested)


def test_missing_raw_target_does_not_preserve_existing_folded_peer(owned):
    root, _, normal, raw_folder, make_pair = owned
    peer = make_pair(normal, "Distinct", b"PEER")
    missing = raw_folder(".") / "Distinct.ess"
    outcome = fire(root, missing, "files")
    assert outcome.result.decision == "allow" and not outcome.transactions
    assert not missing.exists() and peer[0].read_bytes() == b"PEER.ess"


@pytest.mark.parametrize("tail", ["Distinct.ess.", "Distinct.ess ", "Distinct.ess:stream", "..\\Saves\\Distinct.ess"])
def test_unqualified_raw_member_or_dot_segment_refuses_without_peer_backup(owned, tail):
    root, _, normal, _, make_pair = owned
    make_pair(normal, "Distinct", b"PEER")
    raw = "\\\\?\\" + str(normal) + "\\" + tail
    outcome = fire(root, raw, "files")
    assert outcome.result.decision == "deny" and not outcome.transactions


def test_resaver_retains_raw_io_target_and_namespace_prefix_is_not_a_wildcard(owned):
    _, _, normal, raw_folder, make_pair = owned
    make_pair(normal, "Distinct", b"PEER")
    requested = make_pair(raw_folder("."), "Distinct", b"RAW")
    change = saves.resaver_change("clean", [str(requested[0])], "", "powershell", paths.Roots("", ""), {})
    counted = saves.count(change)
    assert not change.unknown and not counted.unknown and not counted.patterns
    assert len(counted.pairs) == 1 and os.path.samefile(counted.pairs[0].ess, requested[0])


def test_raw_config_root_is_retained_for_devbench_and_profile_membership(owned):
    root, profile, normal, _, make_pair = owned
    make_pair(normal, "Distinct", b"PEER")
    raw_profile = Path("\\\\?\\" + str(profile) + ".")
    raw_profile.mkdir()
    folder = raw_profile / "Saves"
    folder.mkdir()
    requested = tuple(folder / ("Distinct" + ext) for ext in (".ess", ".skse"))
    for member in requested:
        member.write_bytes(b"RAW" + member.suffix.encode())
    try:
        (root / "skyrim-paths.env").write_text("SKYRIM_CONFIG_DIR=" + quoted(raw_profile) + "\n", encoding="utf-8")
        action = contracts.Action("generic", "mcp__devbench-vr__game", "mcp", cwd=str(root),
            provenance={"arguments": {"action": "save", "name": "Distinct"}})
        assert_pair(root, portable.check(action, root)[0], requested)
    finally:
        for member in requested:
            member.unlink()
        folder.rmdir()
        raw_profile.rmdir()


@pytest.mark.parametrize("prefix", ["", "\\\\?\\", "\\\\.\\"])
@pytest.mark.parametrize("route", ["files", "write"])
def test_ordinary_namespace_aliases_still_preserve_the_same_pair(owned, prefix, route):
    root, _, normal, _, make_pair = owned
    requested = make_pair(normal, "Ordinary", b"ORDINARY")
    assert_pair(root, fire(root, prefix + str(requested[0]), route), requested)


def test_raw_reserved_filename_preserves_real_file_instead_of_device_alias(owned):
    root, _, normal, _, make_pair = owned
    requested = make_pair(Path("\\\\?\\" + str(normal)), "NUL", b"RAW RESERVED")
    assert_pair(root, fire(root, requested[0], "files"), requested)


@pytest.mark.parametrize("raw_cwd", [False, True])
def test_drive_relative_file_operand_uses_known_same_drive_cwd(owned, raw_cwd):
    root, _, normal, raw_folder, make_pair = owned
    folder = raw_folder(".") if raw_cwd else normal
    requested = make_pair(folder, "Distinct", b"REQUESTED")
    spelling = normal.drive + "Distinct.ess"
    assert_pair(root, fire(root, spelling, "files", cwd=folder), requested)


@pytest.mark.parametrize("route", ["python-escaped", "python-path"])
@pytest.mark.parametrize("stem", ["A'B", "100%HOME%", "Literal$HOME"])
def test_python_literal_quoting_does_not_hide_an_owned_save_name(owned, route, stem):
    root, _, _, raw_folder, make_pair = owned
    requested = make_pair(raw_folder("."), stem, b"QUOTED REQUESTED")
    assert_pair(root, fire(root, requested[0], route), requested)


def test_unknown_python_syntax_refuses_before_any_save_permission(owned):
    root, _, normal, _, make_pair = owned
    requested = make_pair(normal, 'Version', b'UNCHANGED')
    code = "print 'another syntax version'; open(" + repr(str(requested[0])) + ", 'wb').write(b'x')"
    action = contracts.Action('generic', 'shell', 'shell', shell='powershell', cwd=str(root),
        command='python2 -c ' + quoted(code))
    result = portable.check(action, root)[0]
    assert result.result.decision == 'deny' and result.result.evaluation == 'failed' and not result.transactions
    assert requested[0].read_bytes() == b'UNCHANGED.ess'


def test_python_path_absolute_later_argument_selects_the_actual_save(owned):
    root, _, normal, raw_folder, make_pair = owned
    first = make_pair(normal, 'First', b'FIRST PEER')
    requested = make_pair(raw_folder('.'), 'Requested', b'ACTUAL REQUESTED')
    constructed = Path(str(first[0]), str(requested[0]))
    assert os.path.samefile(constructed, requested[0]) and not os.path.samefile(constructed, first[0])
    code = 'from pathlib import Path; Path(' + repr(str(first[0])) + ', ' + repr(str(requested[0])) + ").write_bytes(b'x')"
    action = contracts.Action('generic', 'shell', 'shell', shell='powershell', cwd=str(root),
        command='python -c ' + quoted(code))
    assert_pair(root, portable.check(action, root)[0], requested)
    assert first[0].read_bytes() == b'FIRST PEER.ess'
