"""Derived traces and superseded staging: synthetic trees and a fake Recycle Bin only."""
import json
import os
import subprocess
import stat
import time
from types import SimpleNamespace

import pytest

from test_housekeep import DAY, FakeBin, age, cfg_for, hk, key, planned, write


@pytest.mark.parametrize("relative", ["events.xml", "xml-decoder/trace.xml", "xml-decoder/EVENTS.XML"])
@pytest.mark.parametrize("source_folder", ["same", "parent"])
def test_decoded_trace_with_a_source_is_derived(tmp_path, relative, source_folder):
    trace = write(tmp_path / "_CLAUDE_OUTPUT/task" / relative, b"decoded")
    parent = trace.parent if source_folder == "same" else trace.parent.parent
    source = write(parent / "source.etl", b"measurement")
    doc = hk.plan(cfg_for(tmp_path), only={"derived"})
    assert planned(doc) == {key(trace)}
    assert key(source) not in planned(doc)
    assert sum(doc["rows"].values()) == sum(p.stat().st_size for p in (tmp_path / "_CLAUDE_OUTPUT").rglob("*")
                                            if p.is_file())


@pytest.mark.parametrize("case", ["no-source", "unrelated-xml", "remote-source", "active", "loose", "protected"])
def test_other_xml_is_kept(tmp_path, case):
    relative = "task/events.xml" if case != "unrelated-xml" else "task/ordinary.xml"
    if case == "loose":
        relative = "events.xml"
    trace = write(tmp_path / "_CLAUDE_OUTPUT" / relative, b"decoded", days=0 if case == "active" else 10)
    if case != "no-source":
        parent = tmp_path if case == "remote-source" else trace.parent
        write(parent / "source.etl", b"measurement")
    cfg = cfg_for(tmp_path, protected_globs=["*/events.xml"] if case == "protected" else [])
    assert planned(hk.plan(cfg), "derived") == set()


def test_etl_cannot_be_recreatable_or_a_duplicate(tmp_path):
    write(tmp_path / "Data/source.etl", b"same")
    a = write(tmp_path / "_CLAUDE_OUTPUT/a/source.etl", b"same")
    b = write(tmp_path / "_CLAUDE_OUTPUT/b/source.etl", b"same")
    doc = hk.plan(cfg_for(tmp_path, min_bytes=1, state_exts=[]))
    assert not {key(a), key(b)} & planned(doc)


@pytest.mark.parametrize("change", ["source", "xml", "activity"])
def test_derived_apply_rechecks_the_source_bytes_and_activity(tmp_path, change):
    trace = write(tmp_path / "_CLAUDE_OUTPUT/task/events.xml", b"decoded")
    source = write(trace.parent / "source.etl", b"measurement")
    cfg = cfg_for(tmp_path)
    doc = hk.plan(cfg, only={"derived"})
    assert planned(doc) == {key(trace)}
    trace_stamp, folder_stamp = trace.stat(), trace.parent.stat()
    if change == "source":
        # Rename only a synthetic source, keeping its bytes available for the assertion.
        source.rename(source.with_suffix(".retained"))
        os.utime(trace.parent, ns=(folder_stamp.st_atime_ns, folder_stamp.st_mtime_ns))
    elif change == "xml":
        trace.write_bytes(b"changed")
        os.utime(trace, ns=(trace_stamp.st_atime_ns, trace_stamp.st_mtime_ns))
    else:
        write(trace.parent / "new.txt", b"active", days=0)
    fake = FakeBin(tmp_path)
    assert hk.apply(doc, cfg, recycler=fake) == 1
    assert fake.got == [] and trace.exists()


def stage_tree(tmp_path):
    old = tmp_path / "staging/My Mod-1.2.3"
    new = tmp_path / "staging/My Mod-1.10.0"
    write(old / "payload.txt", b"old", days=50)
    write(new / "payload.txt", b"new", days=50)
    age(old, 50)
    age(new, 50)
    os.utime(old, (time.time() - 50 * DAY,) * 2)
    os.utime(new, (time.time() - 50 * DAY,) * 2)
    return old, new, cfg_for(tmp_path, staging_roots=["staging"])


def test_only_a_lower_numeric_sibling_is_superseded(tmp_path):
    old, new, cfg = stage_tree(tmp_path)
    assert cfg["superseded_after_days"] == 30
    assert hk.stage_version("My Mod-1.2.3") == ("my mod", (1, 2, 3, 0))
    assert hk.stage_version("labelled") is None
    doc = hk.plan(cfg, only={"superseded"})
    assert planned(doc) == {key(old)}
    assert sum(doc["rows"].values()) == 6
    fake = FakeBin(tmp_path)
    assert hk.apply(doc, cfg, recycler=fake) == 0
    assert fake.got == [str(old)] and new.exists()


def test_exact_superseded_list_can_retire_the_only_version(tmp_path):
    old, new, cfg = stage_tree(tmp_path)
    new.rename(new.with_name("Another Mod-2.0.0"))
    write(old.parent / "_superseded", b"# reviewed\nMy Mod-1.2.3\n")
    doc = hk.plan(cfg, only={"superseded"})
    assert planned(doc) == {key(old)}
    assert sum(doc["rows"].values()) == sum(p.stat().st_size for p in old.parent.rglob("*") if p.is_file())


@pytest.mark.parametrize("name", ["../My Mod-1.2.3", "..\\My Mod-1.2.3"])
def test_invalid_superseded_list_keeps_staging(tmp_path, name):
    old, _new, cfg = stage_tree(tmp_path)
    write(old.parent / "_superseded", (name + "\n").encode())
    assert key(old) not in planned(hk.plan(cfg), "superseded")


@pytest.mark.parametrize("case", ["recent", "active", "not-versioned", "other-name", "equal", "nested", "unconfigured"])
def test_staging_eligibility_is_narrow(tmp_path, case):
    old, new, cfg = stage_tree(tmp_path)
    if case == "recent":
        os.utime(old, (time.time() - 20 * DAY,) * 2)
    elif case == "active":
        cfg["superseded_after_days"] = 0
        write(old / "payload.txt", b"old", days=0)
    elif case == "not-versioned":
        old = old.rename(old.with_name("labelled by hand"))
    elif case == "other-name":
        new.rename(new.with_name("Other-2.0.0"))
    elif case == "equal":
        new.rename(new.with_name("My Mod-1.2.3.0"))
    elif case == "nested":
        nested = old.parent / "container"
        nested.mkdir()
        old = old.rename(nested / old.name)
        new.rename(nested / new.name)
    else:
        cfg["staging_roots"] = []
    assert key(old) not in planned(hk.plan(cfg), "superseded")


@pytest.mark.parametrize("inner", ["known-good.txt", "my-backups/file.txt", "AUDIT_LOG.txt", "source.etl"])
def test_protected_staging_content_keeps_the_whole_folder(tmp_path, inner):
    old, _new, cfg = stage_tree(tmp_path)
    write(old / inner, b"keep", days=50)
    age(old, 50)
    os.utime(old, (time.time() - 50 * DAY,) * 2)
    assert key(old) not in planned(hk.plan(cfg), "superseded")


@pytest.mark.parametrize("kind", ["json", "writer-text", "unreadable-json", "relative", "unknown-text"])
def test_manifest_references_or_uncertainty_keep_staging(tmp_path, kind):
    old, _new, cfg = stage_tree(tmp_path)
    folder = tmp_path / "manual-installs"
    folder.mkdir()
    if kind == "json":
        write(folder / "rollback.json", json.dumps({"rollback_sources": [str(old / "payload.txt")]}).encode())
    elif kind == "writer-text":
        write(folder / "mod.files.txt", ("# overwritten originals backed up under: " + str(old) + "\n").encode())
    elif kind == "unreadable-json":
        write(folder / "rollback.json", b"{")
    elif kind == "relative":
        write(folder / "rollback.json", b'{"rollback_sources":["staging/My Mod-1.2.3"]}')
    else:
        write(folder / "mod.files.txt", b"unknown rollback format")
    assert key(old) not in planned(hk.plan(cfg), "superseded")


def test_a_manifest_for_another_folder_does_not_block_staging(tmp_path):
    old, _new, cfg = stage_tree(tmp_path)
    write(tmp_path / "manual-installs/rollback.json",
          json.dumps({"rollback_sources": [str(tmp_path / "elsewhere/payload.txt")]}).encode())
    assert planned(hk.plan(cfg), "superseded") == {key(old)}


@pytest.mark.parametrize("rollback", [False, True])
def test_operation_manifests_are_read_without_misreading_backed_up_configs(tmp_path, rollback):
    old, _new, cfg = stage_tree(tmp_path)
    operation = tmp_path / "manual-installs/backups/operation"
    reference = old if rollback else operation
    text = ("# Example -- installed 20261025-000000 from example.zip [group default]\n"
            "# installed into: " + str(tmp_path / "Data") + "\n"
            "# overwritten originals backed up under: " + str(reference) + "\n"
            "# format: <sha256>  <file written>   (uninstall: delete these, restore the backups)\n"
            "# STATUS: complete, 1 file(s), 0 verify mismatch(es)\n" + "0" * 64 + "  "
            + str(tmp_path / "Data/settings.json") + "\n")
    write(operation / "manifest.files.txt", text.encode())
    write(operation / "Data/settings.json", b'{"mod_configuration":true}')
    assert (key(old) in planned(hk.plan(cfg), "superseded")) is not rollback


@pytest.mark.parametrize("kind", ["tracked", "linked"])
def test_staging_preserves_tracked_files_and_linked_evidence(tmp_path, kind):
    old, _new, cfg = stage_tree(tmp_path)
    if kind == "tracked":
        subprocess.run(["git", "init", "-q", str(old.parent)], check=True)
        subprocess.run(["git", "-C", str(old.parent), "add", "My Mod-1.2.3/payload.txt"], check=True)
    else:
        (tmp_path / "CLAUDE.md").write_text("[keep](staging/My Mod-1.2.3/payload.txt)", encoding="utf-8")
    assert key(old) not in planned(hk.plan(cfg), "superseded")


def test_a_new_empty_subdirectory_keeps_staging_active(tmp_path):
    old, _new, cfg = stage_tree(tmp_path)
    (old / "nested/new-empty").mkdir(parents=True)
    os.utime(old, (time.time() - 50 * DAY,) * 2)
    assert key(old) not in planned(hk.plan(cfg), "superseded")


def test_a_nested_git_checkout_is_never_staging_cleanup(tmp_path):
    old, _new, cfg = stage_tree(tmp_path)
    subprocess.run(["git", "init", "-q", str(old)], check=True)
    subprocess.run(["git", "-C", str(old), "add", "payload.txt"], check=True)
    age(old, 50)
    os.utime(old, (time.time() - 50 * DAY,) * 2)
    assert key(old) not in planned(hk.plan(cfg), "superseded")


@pytest.mark.parametrize("change", ["manifest", "higher", "activity", "contents", "config", "marker"])
def test_staging_apply_rechecks_eligibility(tmp_path, change):
    old, new, cfg = stage_tree(tmp_path)
    if change == "marker":
        new.rename(new.with_name("Other-2.0.0"))
        write(old.parent / "_superseded", b"My Mod-1.2.3\n")
    doc = hk.plan(cfg, only={"superseded"})
    assert planned(doc) == {key(old)}
    if change == "manifest":
        write(tmp_path / "manual-installs/rollback.json", json.dumps({"rollback_sources": [str(old)]}).encode())
    elif change == "higher":
        new.rename(new.with_name("Other-2.0.0"))
    elif change == "activity":
        write(old / "recent.txt", b"recent", days=0)
    elif change == "contents":
        write(old / "payload.txt", b"changed", days=50)
        os.utime(old, (time.time() - 50 * DAY,) * 2)
    elif change == "config":
        cfg["staging_roots"] = []
    else:
        write(old.parent / "_superseded", b"# no supersession\n")
    fake = FakeBin(tmp_path)
    assert hk.apply(doc, cfg, recycler=fake) == 1
    assert fake.got == [] and old.exists()


def test_overlapping_agent_and_staging_roots_are_counted_once(tmp_path):
    old, _new, cfg = stage_tree(tmp_path)
    cfg["agent_output"] = ["staging"]
    doc = hk.plan(cfg)
    assert planned(doc) == {key(old)}
    assert sum(doc["rows"].values()) == 6


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, "30"])
def test_invalid_staging_age_is_refused(tmp_path, value):
    with pytest.raises(ValueError, match="superseded_after_days"):
        cfg_for(tmp_path, superseded_after_days=value)


@pytest.mark.parametrize("roots", ["staging", [""], [3], ["staging", "staging"], ["staging", "staging/child"]])
def test_invalid_or_overlapping_staging_roots_are_refused(tmp_path, roots):
    with pytest.raises(ValueError, match="staging_roots"):
        cfg_for(tmp_path, staging_roots=roots)


@pytest.mark.parametrize("kind", ["empty", "unknown-json", "unknown-comment", "not-directory"])
def test_unknown_manifest_layout_refuses_staging(tmp_path, kind):
    old, _new, cfg = stage_tree(tmp_path)
    root = tmp_path / "manual-installs"
    if kind == "not-directory":
        write(root, b"not a directory")
    else:
        data = {"empty": b"", "unknown-json": b'{"source":"relative"}',
                "unknown-comment": b"# custom rollback reference"}[kind]
        write(root / ("x.json" if kind == "unknown-json" else "x.files.txt"), data)
    assert key(old) not in planned(hk.plan(cfg), "superseded")


@pytest.mark.parametrize("kind", ["derived-file", "derived-source", "staging-folder", "staging-child", "manifest"])
def test_new_classes_never_follow_links(tmp_path, kind):
    old, _new, cfg = stage_tree(tmp_path)
    trace = write(tmp_path / "_CLAUDE_OUTPUT/task/events.xml", b"decoded")
    source = write(trace.parent / "source.etl", b"measurement")
    if kind == "derived-file":
        path = trace
    elif kind == "derived-source":
        path = source
    elif kind == "staging-folder":
        path = old
    elif kind == "staging-child":
        path = old / "payload.txt"
    else:
        path = write(tmp_path / "manual-installs/x.json", b'{"rollback_sources":[]}')
    target = path.rename(path.with_name(path.name + ".retained"))
    try:
        path.symlink_to(target, target_is_directory=target.is_dir())
    except OSError as error:
        pytest.skip("symlink creation unavailable: " + str(error))
    if kind == "staging-child":
        os.utime(old, (time.time() - 50 * DAY,) * 2)
    doc = hk.plan(cfg)
    assert (key(trace) if kind.startswith("derived") else key(old)) not in planned(doc)


@pytest.mark.parametrize("kind", ["symlink", "reparse"])
def test_nonplain_paths_are_refused_without_link_privileges(tmp_path, monkeypatch, kind):
    trace = write(tmp_path / "events.xml", b"decoded")
    write(tmp_path / "source.etl", b"source")
    original = os.lstat

    def lstat(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if key(path) == key(trace):
            return SimpleNamespace(st_mode=stat.S_IFLNK if kind == "symlink" else result.st_mode,
                                   st_file_attributes=0x400 if kind == "reparse" else 0)
        return result

    monkeypatch.setattr(hk.os, "lstat", lstat)
    assert hk.derived_source(trace) is None


@pytest.mark.parametrize("kind", ["valid", "bad-hash", "relative"])
def test_kept_install_records_remain_understood_protection(tmp_path, kind):
    old, _new, cfg = stage_tree(tmp_path)
    target = old / "payload.txt"
    digest = "0" * (64 if kind != "bad-hash" else 63)
    value = str(target) if kind != "relative" else "relative/payload.txt"
    write(tmp_path / "manual-installs/kept.files.txt", ("# KEPT: " + digest + "  " + value + "\n").encode())
    if kind == "valid":
        assert key(hk._real(target)) in hk.manifest_references(cfg)
    else:
        with pytest.raises(ValueError):
            hk.manifest_references(cfg)
