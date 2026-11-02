"""v4.0.0 release review, 2026-11-02: what archiving and the file classes kept. User ruling that day: a protected item
in an archived folder stays where it is and the rest of the folder is archived. Real plan/apply, fake recycler."""
import json
import os
import time
from pathlib import Path

import pytest

from conftest import REPO  # noqa: F401
from test_housekeep import hk

OLD = time.time() - 40 * 86400


def put(path, data=b"x" * 100):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def age(root):
    for dp, ds, fs in os.walk(root):
        for n in fs + ds:
            os.utime(os.path.join(dp, n), (OLD, OLD))


def config(root, **over):
    base = {"root": str(root), "agent_output": ["out"], "live_roots": ["Data"], "evidence_docs": ["CLAUDE.md"],
            "approved_sha256": hk.cleanup_code_sha256(), "daily_enabled": False}
    base.update(over)
    (root.parent / "cfg.json").write_text(json.dumps(base), encoding="utf-8")
    return hk.load_config(root.parent / "cfg.json")


def archive(cfg):
    seen = []

    def fake(paths):
        seen.extend(str(p) for p in paths)
        return {str(p): None for p in paths}
    doc = hk.plan(cfg, only={"archive"})
    assert hk.apply(doc, cfg, recycler=fake) == 0
    return doc, [os.path.relpath(s, cfg["root"]).replace("\\", "/") for s in seen]


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "root"
    r.mkdir()
    (r / "CLAUDE.md").write_text("", encoding="utf-8")
    return r


@pytest.mark.parametrize("member", ["known-good-2026/base.psc", "backups/manifest.jsonl", "AUDIT_LOG.txt", "trace.etl",
                                    "sub/.housekeep-keep", "sub/pinned.bin"])
def test_a_protected_member_stays_in_place_and_the_rest_is_archived(root, member):
    put(root / "out/task/.housekeep-closed", b"")
    put(root / "out/task/payload.bin")
    put(root / "out/task" / member)
    if member == "sub/pinned.bin":
        put(root / "out/task/sub/.housekeep-keep", b"")
    age(root)
    doc, recycled = archive(config(root))
    assert [a["class"] for a in doc["actions"]] == ["archive"]
    assert "out/task/payload.bin" in recycled
    assert not any(member.split("/")[0] in r for r in recycled), recycled
    assert (root / "out/task" / member).exists()


def test_a_file_linked_from_a_doc_inside_an_archive_root_is_protected(root):
    put(root / "ext/done/.housekeep-closed", b"")
    put(root / "ext/done/cited.txt")
    put(root / "ext/done/other.txt")
    (root / "CLAUDE.md").write_text("see ext/done/cited.txt\n", encoding="utf-8")
    age(root)
    cfg = config(root, archive_roots={"ext": 1})
    assert hk.Guard(cfg).why_protected(root / "ext/done/cited.txt") == "linked evidence"
    assert "ext/done/cited.txt" not in archive(cfg)[1]


def test_everything_inside_a_linked_folder_is_protected(root):
    put(root / "out/task/.housekeep-closed", b"")
    put(root / "out/task/linked/inside.txt")
    put(root / "out/task/other.txt")
    (root / "CLAUDE.md").write_text("folder out/task/linked/ holds it\n", encoding="utf-8")
    age(root)
    cfg = config(root)
    assert hk.Guard(cfg).why_protected(root / "out/task/linked/inside.txt") == "linked evidence"
    assert not any("linked" in r for r in archive(cfg)[1])


def test_a_pin_stops_the_file_classes_for_its_folder(root):
    master = b"m" * (2 << 20)
    put(root / "Data/master.esm", master)
    put(root / "out/pinned/.housekeep-keep", b"")
    put(root / "out/pinned/fixture.esm", master)
    put(root / "out/open/deep/.housekeep-keep", b"")
    put(root / "out/open/deep/fixture2.esm", master)
    age(root)
    doc = hk.plan(config(root))
    assert not [a for a in doc["actions"] if a["class"] != "archive" and "pinned" in a["path"] or "deep" in a["path"]]


def test_a_control_copy_outside_a_pin_is_still_recreatable(root):
    master = b"m" * (2 << 20)
    put(root / "Data/master.esm", master)
    put(root / "out/plain/fixture.esm", master)
    put(root / "out/plain/note.txt")
    age(root)
    doc = hk.plan(config(root, archive_root=str(root / "nas")), only={"recreatable"})
    assert [a["class"] for a in doc["actions"]] == ["recreatable"]


def test_a_plain_line_in_the_live_state_block_holds_its_folder(root):
    put(root / "out/held/.housekeep-closed", b"")
    put(root / "out/held/w.txt")
    (root / "CLAUDE.md").write_text("## Live state\n\nOne session holds `out/held` still\n", encoding="utf-8")
    age(root)
    doc, recycled = archive(config(root))
    assert not doc["actions"] and not recycled


def test_a_live_state_line_naming_the_output_root_itself_holds_nothing(root):
    (root / "CLAUDE.md").write_text("## Live state\n\nInstructions mention `out` as an example.\n", encoding="utf-8")
    assert hk.live_state_tokens(config(root)) == set()


def test_a_stub_folder_keeping_a_large_file_is_not_archived_again(root):
    put(root / "out/big/.housekeep-closed", b"")
    put(root / "out/big/evid.bin", b"y" * (2 << 20))
    put(root / "out/big/other.bin")
    (root / "CLAUDE.md").write_text("see out/big/evid.bin\n", encoding="utf-8")
    age(root)
    cfg = config(root)
    archive(cfg)
    assert (root / "out/big/ARCHIVED.md").exists()
    age(root)
    assert not hk.plan(cfg, only={"archive"})["actions"]


def test_an_unreadable_rb_ledger_holds_the_work(monkeypatch, tmp_path):
    (tmp_path / ".claude/tools/resource-budget").mkdir(parents=True)
    (tmp_path / ".claude/tools/resource-budget/ledger.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(hk.os.path, "expanduser", lambda p: str(tmp_path))
    cwds = hk.rb_job_cwds()
    assert cwds is None
    assert "ledger" in hk.is_done(tmp_path, 40, set(), cwds, hk.Guard.__new__(hk.Guard))


def test_a_missing_rb_ledger_holds_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(hk.os.path, "expanduser", lambda p: str(tmp_path))
    assert hk.rb_job_cwds() == []


def test_a_recycle_timeout_is_a_reported_failure_not_a_crash(root):
    put(root / "out/task/.housekeep-closed", b"")
    put(root / "out/task/payload.bin")
    age(root)
    cfg = config(root)

    def slow(paths):
        raise hk.subprocess.TimeoutExpired("powershell", 3600)
    assert hk.apply(hk.plan(cfg, only={"archive"}), cfg, recycler=slow) == 1
    assert (root / "out/task/payload.bin").exists()


def test_the_temp_producers_declare_their_own_workspaces(tmp_path):
    """Finding F3: nothing outside the tests wrote the declaration, so the sweep never recycled anything real."""
    import ast
    for rel, needle in (("tools/mod-install.py", "_declare_disposable(stage)"),
                        ("tools/housecarl_integration/gateway.py", "mark_disposable_temp(work")):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert needle in text, rel
        names = {a.name for n in ast.walk(ast.parse(text)) if isinstance(n, ast.Import) for a in n.names}
        assert "sys" in names, rel + " would swallow a NameError in its best-effort declaration"
    work = tmp_path / "modinst_x"
    work.mkdir()
    hk.certainty.mark_disposable_temp(work, tmp_path)
    assert hk.certainty.disposable_temp(work, tmp_path)


@pytest.mark.parametrize("fail", ["refused", "timeout"])
def test_house_carl_leaves_no_linked_view_when_its_check_fails(tmp_path, monkeypatch, fail):
    """User ruling 2026-10-29: a hard-linked view of game files never outlives the run that made it."""
    import importlib
    import sys
    from types import SimpleNamespace
    sys.path.insert(0, str(REPO / "tools"))
    gw = importlib.import_module("housecarl_integration.gateway")
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(gw.tempfile, "tempdir", str(temp))
    master = tmp_path / "Skyrim.esm"
    master.write_bytes(b"m")
    candidate = tmp_path / "cand.esp"
    candidate.write_bytes(b"c")
    seen = []

    def run(cmd, **kw):
        seen.extend(p.name for p in (Path(cmd[2]) / "Data").iterdir())
        if fail == "timeout":
            raise gw.subprocess.TimeoutExpired(cmd, 120)
        return SimpleNamespace(stdout="", stderr="no", returncode=1)
    monkeypatch.setattr(gw.subprocess, "run", run)
    with pytest.raises((gw.Refusal, gw.subprocess.TimeoutExpired)):
        gw.independent_check({"root": str(tmp_path)}, candidate, SimpleNamespace(paths={"Skyrim.esm": master}))
    assert "Skyrim.esm" in seen, "the verifier saw the view"
    assert not list(temp.glob("hc-xedit-*/Data")), "a view of the real plugin outlived the check"
    assert master.read_bytes() == b"m"


def _check(tmp_path, monkeypatch, run=None, link=None):
    import importlib
    import sys
    from types import SimpleNamespace
    sys.path.insert(0, str(REPO / "tools"))
    gw = importlib.import_module("housecarl_integration.gateway")
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(gw.tempfile, "tempdir", str(temp))
    master = tmp_path / "Skyrim.esm"
    master.write_bytes(b"m")
    candidate = tmp_path / "cand.esp"
    candidate.write_bytes(b"c")
    if run:
        monkeypatch.setattr(gw.subprocess, "run", lambda cmd, **kw: run(cmd, master))
    if link:
        monkeypatch.setattr(gw.os, "link", link)
    return gw, lambda: gw.independent_check({"root": str(tmp_path)}, candidate, SimpleNamespace(paths={"Skyrim.esm": master}))


def test_house_carl_refuses_rather_than_copying_a_master(tmp_path, monkeypatch):
    def no_link(a, b):
        raise OSError("cross-device")
    gw, go = _check(tmp_path, monkeypatch, link=no_link)
    copied = []
    real = gw.shutil.copyfile
    monkeypatch.setattr(gw.shutil, "copyfile", lambda a, b: copied.append(str(a)) or real(a, b))
    with pytest.raises(gw.Refusal, match="never copied"):
        go()
    assert not [c for c in copied if c.endswith("Skyrim.esm")]


def test_house_carl_notices_a_master_that_changed_during_its_check(tmp_path, monkeypatch):
    from types import SimpleNamespace

    def run(cmd, master):
        master.write_bytes(b"changed through the link")
        return SimpleNamespace(stdout="", stderr="", returncode=1)
    gw, go = _check(tmp_path, monkeypatch, run=run)
    with pytest.raises(gw.Refusal, match="changed during the read-only check"):
        go()
