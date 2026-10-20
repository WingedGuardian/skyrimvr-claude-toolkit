"""Behavioral controls for authoritative inputs and the publication boundary; no real game access."""
import asyncio
import json
import os
from pathlib import Path
import struct
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from housecarl_integration import Refusal
from housecarl_integration import files, gateway, order


def plugin(path, flags=0, masters=()):
    body = b""
    for name in masters:
        value = name.encode("cp1252") + b"\0"
        body += b"MAST" + struct.pack("<H", len(value)) + value
    path.write_bytes(b"TES4" + struct.pack("<II", len(body), flags) + b"\0" * 12 + body)


class _Source:
    """A stand-in for the urlopen response: `chunks` are returned in turn; an exception instance is raised."""
    def __init__(self, *chunks):
        self.chunks = list(chunks)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        if not self.chunks:
            return b""
        c = self.chunks.pop(0)
        if isinstance(c, BaseException):
            raise c
        return c


def test_an_interrupted_download_never_leaves_a_file_at_the_archive_name(tmp_path, monkeypatch):
    """Review L5 F6: a partial zip at the final name blocked every later setup with a hash error."""
    from housecarl_integration import setup as hc_setup
    archive = tmp_path / "houseCARL-2.0.5.zip"
    with pytest.raises(OSError):
        hc_setup.download_archive(archive, opener=lambda *a, **k: _Source(b"PK partial", OSError("connection reset")))
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(Refusal, match="does not match pinned"):
        hc_setup.download_archive(archive, opener=lambda *a, **k: _Source(b"PK the wrong bytes"))
    assert not archive.exists() and [p.name.split(".rejected-")[0] for p in tmp_path.iterdir()] == [archive.name]
    good = b"PK the pinned bytes"
    import hashlib
    monkeypatch.setattr(hc_setup, "ARCHIVE_SHA256", hashlib.sha256(good).hexdigest())
    hc_setup.download_archive(archive, opener=lambda *a, **k: _Source(good))    # twin: a good download lands
    assert archive.read_bytes() == good


def test_a_staged_patch_name_with_brackets_is_a_name_not_a_glob(tmp_path):
    """Review L5 F3: `glob("*/Foo [Fix].esp")` matched `Foo F.esp` and not the literal file."""
    for folder, name in (("abc", "Foo [Fix].esp"), ("def", "Foo F.esp")):
        (tmp_path / "patches" / folder).mkdir(parents=True)
        plugin(tmp_path / "patches" / folder / name)
    assert gateway.staged_patches(tmp_path, "Foo [Fix].esp") == [tmp_path / "patches" / "abc" / "Foo [Fix].esp"]
    assert gateway.staged_patches(tmp_path, "foo f.ESP") == [tmp_path / "patches" / "def" / "Foo F.esp"]
    assert gateway.staged_patches(tmp_path, "Foo [Gix].esp") == []
    assert gateway.staged_patches(tmp_path / "none", "Foo F.esp") == []


@pytest.fixture
def install(tmp_path):
    data = tmp_path / "Data"
    data.mkdir()
    ini = tmp_path / "ini"
    ini.mkdir()
    plugins = tmp_path / "plugins.txt"
    plugins.write_text("*Sword.esp\n*LateMaster.esp\nDisabled.esp\n", encoding="cp1252")
    plugin(data / "Skyrim.esm", 1)
    plugin(data / "LateMaster.esp", 1, ["Skyrim.esm"])
    plugin(data / "Sword.esp", 0, ["Skyrim.esm"])
    config = {"root": str(tmp_path), "state_dir": str(tmp_path / ".skyguard/housecarl"),
              "data_dir": str(data), "plugins_txt": str(plugins), "edition": "SE", "config_dir": str(ini),
              "authority": "wrye-bash", "asset_provider": "deployed-data"}
    return config


def test_wrye_order_does_not_consult_stale_loadorder(install):
    p = Path(install["plugins_txt"])
    p.with_name("loadorder.txt").write_text("NotInstalled.esp\n")
    before = p.read_bytes()
    snap = order.capture(install)
    assert snap.names == ["Skyrim.esm", "LateMaster.esp", "Sword.esp"]
    assert snap.inactive == ["Disabled.esp"]
    assert p.read_bytes() == before


def test_membership_not_in_old_loadorder_is_kept(install):
    p = Path(install["plugins_txt"])
    plugin(Path(install["data_dir"]) / "New.esp")
    with p.open("a") as stream:
        stream.write("*New.esp\n")
    assert "New.esp" in order.capture(install).names


@pytest.mark.parametrize("text,reason", [("*Missing.esp\n", "missing"),
                                        ("*Sword.esp\n*Sword.esp\n", "Duplicate"),
                                        ("*../Other.esp\n", "basename")])
def test_invalid_authority_refuses_instead_of_dropping(install, text, reason):
    Path(install["plugins_txt"]).write_text(text)
    with pytest.raises(Refusal, match=reason):
        order.capture(install)


def test_missing_dependency_does_not_auto_activate_it(install):
    plugin(Path(install["data_dir"]) / "Sword.esp", masters=["Missing.esm"])
    with pytest.raises(Refusal, match="inactive/missing"):
        order.capture(install)


def test_extended_header_payload_is_read_without_truncation(tmp_path):
    p = tmp_path / "Extended.esp"
    payload = b"z" * 70000
    body = b"XXXX" + struct.pack("<HI", 4, len(payload)) + b"ONAM\0\0" + payload
    p.write_bytes(b"TES4" + struct.pack("<II", len(body), 0) + b"\0" * 12 + body)
    assert order.plugin_header(p)["masters"] == []


def test_cp1252_and_ghosted_names(install):
    plugin(Path(install["data_dir"]) / "Café.esp")
    Path(install["plugins_txt"]).write_bytes("*Café.esp.ghost\n".encode("cp1252"))
    assert order.capture(install).names[-1] == "Café.esp"


def test_vr_requires_esl_support(install):
    install["edition"] = "VR"
    plugin(Path(install["data_dir"]) / "Sword.esp", flags=0x200)
    with pytest.raises(Refusal, match="ESL Support"):
        order.capture(install)


def test_fingerprint_changes_for_order_or_plugin_changes(install):
    a = order.capture(install).fingerprint
    Path(install["plugins_txt"]).write_text("*LateMaster.esp\n*Sword.esp\n")
    b = order.capture(install).fingerprint
    assert a != b
    plugin(Path(install["data_dir"]) / "Sword.esp", masters=["LateMaster.esp"])
    assert order.capture(install).fingerprint != b


def test_fingerprint_changes_for_equal_size_body_change_with_preserved_mtime(install):
    target = Path(install["data_dir"]) / "Sword.esp"
    with target.open("ab") as stream:
        stream.write(b"synthetic record body A")
    before = order.capture(install).fingerprint
    stamp = target.stat()
    with target.open("r+b") as stream:
        stream.seek(-1, 2)
        stream.write(b"B")
    os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert order.capture(install).fingerprint != before


def test_new_publication_does_not_clobber_a_concurrently_created_target(tmp_path, monkeypatch):
    candidate, target = tmp_path / "candidate.esp", tmp_path / "new.esp"
    candidate.write_bytes(b"prepared")
    original = {name: getattr(files.os, name) for name in ("replace", "rename", "link")}

    def racing(name):
        def invoke(source, destination, *args, **kwargs):
            if Path(destination) == target:
                target.write_bytes(b"concurrent unrelated content")
            return original[name](source, destination, *args, **kwargs)
        return invoke

    for name in original:
        monkeypatch.setattr(files.os, name, racing(name))
    with pytest.raises((Refusal, FileExistsError)):
        files.publish(candidate, target, None)
    assert target.read_bytes() == b"concurrent unrelated content"
    assert candidate.read_bytes() == b"prepared"


def test_private_profile_does_not_rewrite_source_and_carries_vr_archive(install):
    install["edition"] = "VR"
    source = Path(install["plugins_txt"])
    before = source.read_bytes()
    folder = Path(install["state_dir"]) / "profile"
    order.write_profile(order.capture(install), folder, install)
    assert source.read_bytes() == before
    assert "Skyrim_VR - Main.bsa" in (folder / "Skyrim.ini").read_text()


def test_backup_is_independent_and_verified(tmp_path):
    target = tmp_path / "A.esp"
    target.write_bytes(b"old bytes")
    saved = files.backup(target, tmp_path / "backups")
    target.write_bytes(b"new bytes")
    assert Path(saved["backup"]).read_bytes() == b"old bytes"
    assert files.sha(saved["backup"]) == saved["sha256"]


def test_backup_copy_corruption_refuses(tmp_path, monkeypatch):
    target = tmp_path / "A.esp"
    target.write_bytes(b"old bytes")
    original = files.durable.digest
    def corrupt_readback(path, deadline=None):
        if Path(path) != target:
            Path(path).write_bytes(b"corrupt")
        return original(path, deadline)
    monkeypatch.setattr(files.durable, "digest", corrupt_readback)
    with pytest.raises(Refusal, match="hash verification"):
        files.backup(target, tmp_path / "backups")
    assert target.read_bytes() == b"old bytes"


def test_publication_breaks_hardlink_without_editing_source(tmp_path):
    source, deployed, candidate = [tmp_path / n for n in ("source.esp", "deployed.esp", "candidate.esp")]
    source.write_bytes(b"old")
    os.link(source, deployed)
    candidate.write_bytes(b"new")
    files.publish(candidate, deployed, files.sha(deployed))
    assert source.read_bytes() == b"old"
    assert deployed.read_bytes() == b"new"


def test_stale_target_and_commit_failure_keep_original(tmp_path, monkeypatch):
    target, candidate = tmp_path / "A.esp", tmp_path / "candidate.esp"
    target.write_bytes(b"old")
    before = files.sha(target)
    candidate.write_bytes(b"new")
    target.write_bytes(b"user edit")
    with pytest.raises(Refusal, match="changed"):
        files.publish(candidate, target, before)
    assert target.read_bytes() == b"user edit"
    monkeypatch.setattr(files.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("locked")))
    with pytest.raises(OSError, match="locked"):
        files.publish(candidate, target, files.sha(target))
    assert target.read_bytes() == b"user edit"


def test_concurrent_transaction_refused(tmp_path):
    with files.exclusive(tmp_path / "lock"):
        with pytest.raises(Refusal, match="Another"):
            with files.exclusive(tmp_path / "lock"):
                pytest.fail("second writer entered")


@pytest.mark.parametrize("tool,args", [("housecarl_compact_plugin", {}),
                                      ("housecarl_check", {"findings": ["facegen"]}),
                                      ("housecarl_skse", {"findings": "pairing"}),
                                      ("housecarl_records", {"formids": ["FE001234"]}),
                                      ("housecarl_records", {"to_file": "C:/outside.jsonl"})])
def test_unqualified_operations_cannot_bypass_gateway(install, tool, args):
    with pytest.raises(Refusal):
        gateway.check_arguments(tool, args, install)


def test_status_is_explicit_about_unqualified_runtime(install):
    obj = asyncio.run(gateway.Gateway(install).call("toolkit_housecarl_status", {}))
    assert obj["context"]["authority"] == "wrye-bash"
    assert obj["context"]["capabilities"]["runtime_addresses"] is False
    assert obj["active_plugins"] == ["Skyrim.esm", "LateMaster.esp", "Sword.esp"]
    filtered = asyncio.run(gateway.Gateway(install).call("housecarl_load_order_status", {"filter": "sword.esp"}))
    assert filtered["active_plugins"] == ["Sword.esp"]
    with pytest.raises(Refusal, match="alternate-profile"):
        asyncio.run(gateway.Gateway(install).call("housecarl_load_order_status", {"profile": "Unrelated"}))


def test_restore_refuses_concurrent_change_and_keeps_permanent_backups(install):
    state = Path(install["state_dir"])
    target = Path(install["data_dir"]) / "Sword.esp"
    saved = files.backup(target, state / "backups")
    original = target.read_bytes()
    target.write_bytes(b"published")
    ident = "a" * 32
    receipt = {"state": "published", "target": str(target), "backup": saved,
               "after_sha256": files.sha(target)}
    files.json_write(state / "transactions" / ident / "receipt.json", receipt)
    target.write_bytes(b"changed since")
    with pytest.raises(Refusal, match="no longer matches"):
        gateway.Gateway(install).restore(ident)
    target.write_bytes(b"published")
    gateway.Gateway(install).restore(ident)
    assert target.read_bytes() == original
    assert Path(saved["backup"]).is_file()
    assert len(list((state / "backups").glob("*/manifest.json"))) == 2


def test_mo2_uses_checked_membership_and_highest_supplier(install):
    root = Path(install["root"])
    mods = root / "mods"
    for name in ("High", "Low"):
        (mods / name).mkdir(parents=True)
        plugin(mods / name / "Sword.esp", masters=["Skyrim.esm"])
    profile = Path(install["plugins_txt"]).parent
    (profile / "modlist.txt").write_text("+High\n+Low\n")
    (profile / "loadorder.txt").write_text("Skyrim.esm\nLateMaster.esp\nSword.esp\nDisabled.esp\n")
    install.update(asset_provider="mo2", authority="mo2-profile", mods_dir=str(mods))
    snapshot = order.capture(install)
    assert snapshot.paths["Sword.esp"] == mods / "High/Sword.esp"
    assert "Disabled.esp" not in snapshot.names
    (profile / "loadorder.txt").write_text("Skyrim.esm\n")
    with pytest.raises(Refusal, match="omits checked"):
        order.capture(install)


def test_dependency_after_dependent_refuses_instead_of_sorting(install):
    data = Path(install["data_dir"])
    plugin(data / "Sword.esp", masters=["After.esp"])
    plugin(data / "After.esp")
    Path(install["plugins_txt"]).write_text("*Sword.esp\n*After.esp\n")
    with pytest.raises(Refusal, match="before one of its masters"):
        order.capture(install)


def test_manifest_expansion_is_in_receipt_edit_set(install):
    request = Path(install["root"]) / "request.json"
    request.write_text(json.dumps([{"target": "012EB7:Skyrim.esm", "from": "012EB8:Skyrim.esm"}]))
    args = gateway.check_arguments("housecarl_apply", {"assignments": "@" + str(request)}, install)
    assert gateway.allowed_records(args) == {"12eb7:skyrim.esm"}


@pytest.mark.parametrize("stage", ["backup", "baseline", "backend", "candidate", "stale", "publication"])
def test_transaction_failures_never_publish(install, monkeypatch, stage):
    from types import SimpleNamespace
    target = Path(install["data_dir"]) / "Sword.esp"
    original = target.read_bytes()
    calls = []

    def fail(*_args, **_kwargs):
        raise Refusal("injected " + stage)

    def check(_config, _candidate, _snapshot, before=None, allowed=None):
        if stage == "baseline" or (stage == "candidate" and before is not None):
            fail()
        return {"records": {}, "errors": [], "complete": True}

    class FakeBackend:
        def __init__(self, config, snapshot, work):
            self.work = work

        async def __aenter__(self):
            (self.work / "profile").mkdir()
            (self.work / "profile/modlist.txt").write_text("")
            return self

        async def __aexit__(self, *_exc):
            return False

        async def call(self, _name, _args):
            calls.append("writer")
            if stage == "backend":
                fail()
            plugin(self.work / "mods/Target/Sword.esp", flags=0x800)
            if stage == "stale":
                Path(install["plugins_txt"]).write_text("*Sword.esp\n")
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps({"ok": True, "verify_ran": True}))])

    monkeypatch.setattr(gateway, "Backend", FakeBackend)
    monkeypatch.setattr(gateway, "independent_check", check)
    if stage == "backup":
        monkeypatch.setattr(gateway, "backup", fail)
    if stage == "publication":
        monkeypatch.setattr(gateway, "publish", fail)
    with pytest.raises(Refusal):
        asyncio.run(gateway.Gateway(install).call("housecarl_apply", {
            "in_place": "Sword.esp", "ops": [{"formid": "000800:Sword.esp", "field_path": "Name", "value": "new"}]}))
    assert target.read_bytes() == original
    if stage in ("backup", "baseline"):
        assert calls == []


def test_independent_checker_rejects_unrequested_new_records(install, monkeypatch):
    from types import SimpleNamespace
    target = Path(install["data_dir"]) / "Sword.esp"

    def inspect(command, **_kwargs):
        Path(command[-1]).write_text(json.dumps({"complete": True, "records": {"800:sword.esp": "unexpected"}, "errors": []}))
        return SimpleNamespace(stdout="RESULT: OK", stderr="")

    monkeypatch.setattr(gateway.subprocess, "run", inspect)
    with pytest.raises(Refusal, match="outside requested records"):
        gateway.independent_check(install, target, order.capture(install), {"records": {}, "errors": []}, set())


def test_optional_doctor_does_not_claim_host_discovery(install, monkeypatch):
    from skyguard.doctor import _housecarl
    from housecarl_integration import runtime
    state = Path(install["state_dir"])
    files.json_write(state / "config.json", install)
    monkeypatch.setattr(runtime, "verify_installation", lambda _config: 4)
    row, = _housecarl(install["root"])
    assert row.status == "unknown" and not row.counted
    assert "host discovery" in row.reason


@pytest.mark.parametrize("text", ["resolver: 2 plugins resolved to real files", "missing resolver report",
                                  "resolver: 3 plugins resolved to real files\n[!] EXCLUDED plugins (1)"])
def test_partial_backend_index_is_never_accepted(text):
    from types import SimpleNamespace
    from housecarl_integration.runtime import check_index
    result = SimpleNamespace(content=[SimpleNamespace(text=text)])
    with pytest.raises(Refusal, match="complete captured order"):
        check_index(result, 3)
    check_index(SimpleNamespace(content=[SimpleNamespace(text="resolver: 3 plugins resolved to real files")]), 3)


def test_backend_cleanup_keeps_tool_refusal_as_a_refusal(install):
    from housecarl_integration.runtime import Backend
    calls = []

    class Resources:
        async def aclose(self):
            calls.append("closed")

    async def run():
        backend = Backend(install, order.capture(install))
        backend.stack = Resources()
        # Exercise exit directly without opening a process or importing the optional SDK.
        error = Refusal("bad request")
        assert await backend.__aexit__(Refusal, error, None) is False

    asyncio.run(run())
    assert calls == ["closed"]


def test_xedit_error_reports_preserve_itm_warnings_and_block_references():
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("Node not installed")
    verifier = Path(__file__).resolve().parents[1] / "tools/housecarl-verify.js"
    code = """
const assert = require('assert');
const {parseErrorReports: parse} = require(process.argv[1]);
assert.deepEqual(parse(['{"errors":[]}']), {errors:[], warnings:[]});
const result = parse([JSON.stringify({errors:[{group:0},{group:1},{group:4},{group:999}]})]);
assert.equal(result.warnings.length, 2);
assert.equal(result.errors.length, 2);
assert.throws(() => parse(['{}']));
assert.throws(() => parse(['broken']));
"""
    subprocess.run(["node", "-e", code, str(verifier)], check=True, capture_output=True)
