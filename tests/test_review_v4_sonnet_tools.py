"""Tool and script defects from the v4.0.0 line-by-line review, each pinned at the behaviour it got wrong. Scratch folders only."""
import asyncio
import importlib.util
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import BASH, REPO
from test_housecarl import gateway, install, plugin  # noqa: F401
from test_housekeep import FakeBin, cfg_for, hk, launch, tree  # noqa: F401
from test_housekeep_backups import archived, b, setup  # noqa: F401

sys.path.insert(0, str(REPO / "tools"))


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---- 1.2
def test_baseline_stdout_prints_and_leaves_the_baseline_alone(tmp_path):
    game = tmp_path / "game"
    (game / ".claude").mkdir(parents=True)
    baseline = game / ".claude/baseline_checksums.txt"
    baseline.write_text("the baseline\n")
    (game / "CLAUDE.md").write_text("x")
    env = dict(os.environ, GAME_DIR=str(game).replace("\\", "/"), CONFIG_DIR=str(tmp_path / "none").replace("\\", "/"))
    r = subprocess.run([BASH, str(REPO / "scripts/generate-baseline.sh"), "--stdout"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and "## Project Files" in r.stdout and "CLAUDE.md" in r.stdout
    assert "Generating" not in r.stdout
    assert baseline.read_text() == "the baseline\n"


# ---- 1.3
def test_the_changelog_draft_finds_a_dated_heading(tmp_path):
    script = (REPO / "scripts/build-nexus-pack.sh").read_text(encoding="utf-8")
    program = re.search(r"awk -v ver=\"## \$TAG\" '(.*?)'\s+\"\$REPO_DIR", script, re.S).group(1)
    log = tmp_path / "CHANGELOG.md"
    log.write_text("# Changelog\n\n## v4.0.0 \u2014 2026-11-05\n\nbody\n\n## v3.9.1 \u2014 2026-09-09\n\nolder\n", encoding="utf-8")
    prog = tmp_path / "p.awk"
    prog.write_text(program, encoding="utf-8")
    out = subprocess.run([BASH, "-c", 'awk -v ver="## v4.0.0" -f "$0" "$1"', str(prog).replace("\\", "/"), str(log).replace("\\", "/")],
                         capture_output=True, text=True, encoding="utf-8").stdout
    assert "body" in out and "older" not in out


# ---- 1.8
def test_deep_grups_are_a_plugin_error_not_a_recursion_error():
    er = _module("esp_records_t", "tools/esp_records.py")
    n = 1500
    body = b"".join(b"GRUP" + struct.pack("<I", 24 * (n - i)) + b"\0" * 16 for i in range(n))
    with pytest.raises(er.PluginError, match="nested deeper"):
        er.parse(b"TES4" + struct.pack("<IIII", 0, 0, 0, 0) + b"\0\0\0\0" + body)


# ---- 2.1, 2.3
def _two_objects(setup):  # noqa: F811
    source, cfg, recycler = setup
    d = source / "old"
    (d / "other.py").write_bytes(b"another rollback source\n")     # different bytes: its own archive object
    past = (d / "script.py").stat().st_mtime
    os.utime(d / "other.py", (past, past))
    os.utime(d, (past, past))
    _, cfg, rows = archived((source, cfg, recycler))
    records = [json.loads(Path(r["receipt"]).read_text()) for r in rows]
    cfg["backup_archive"]["obsolete"] = {r["id"]: dict(basis="operator-confirmed-obsolete", reason="reviewed", evidence="receipt-bound review")
                                         for r in records}
    return cfg, rows


def test_a_partly_recycled_prune_leaves_the_vault_prunable(setup):  # noqa: F811
    cfg, rows = _two_objects(setup)
    doc = b.prune_plan(cfg)
    assert len(doc["objects"]) >= 2

    def partial(paths):
        out = {}
        for i, p in enumerate(paths):
            if i == 0:
                Path(p).unlink()
                out[p] = None
            else:
                out[p] = "too big for the Recycle Bin"
        return out
    with pytest.raises(b.Refused, match="failed/left in place"):
        b.prune(doc, cfg, b.digest(doc), recycler=partial)
    again = b.prune_plan(cfg)
    assert again["objects"], "the object the bin refused can still be planned"
    tombs = {p.stem for p in (Path(cfg["backup_archive"]["destination"]) / "retired").glob("*.json")}
    refused = [o for o in doc["objects"] if Path(o["path"]).exists()]
    assert tombs and not tombs & {i for o in refused for i in o["record_ids"]}, "a refused archive keeps its restore receipts"


def test_a_stale_partial_archive_is_explained(setup):  # noqa: F811
    source, cfg, recycler = setup
    doc = b.plan(cfg)
    assert doc["actions"]
    target = Path(cfg["backup_archive"]["destination"]) / "objects"
    target.mkdir(parents=True, exist_ok=True)
    action = doc["actions"][0]
    oid = next(v for k, v in action.items() if "id" in k and isinstance(v, str) and len(v) >= 16)
    (target / (oid + ".partial")).write_bytes(b"left over")
    with pytest.raises(b.Refused, match="left from an earlier archive run"):
        b.apply(doc, cfg, recycler, b.digest(doc))


# ---- 2.5
def test_the_documented_gql_req_form_works():
    r = subprocess.run([sys.executable, "-B", str(REPO / "tools/nexus-render.py"), "gql-req", "123", "skyrimspecialedition"], capture_output=True, text=True)
    assert r.returncode == 0
    assert "gql-req <modId> <domain>" in (REPO / "tools/nexus-render.py").read_text(encoding="utf-8")


# ---- 3.4
@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_resolve_names_parses():
    assert subprocess.run(["node", "--check", str(REPO / "tools/resaver-resolve-names.js")], capture_output=True).returncode == 0


# ---- 5.3
def test_run_background_emu_refuses_up_front(monkeypatch):
    vr = _module("vr_headless_t", "tools/vr-headless.py")
    monkeypatch.setattr(vr, "IS_WINDOWS", True)
    monkeypatch.setattr(vr, "load_state", lambda: None)
    monkeypatch.setattr(vr, "cmd_on", lambda **_k: pytest.fail("prepared before refusing"))
    with pytest.raises(vr.Refused, match="cannot prepare the driver"):
        vr.cmd_run("background", emu=True)


# ---- 5.11
@pytest.mark.skipif(BASH is None, reason="no bash")
def test_a_gone_file_alarms_once_and_comes_back_clean(tmp_path):
    (tmp_path / "tools").mkdir()
    shutil.copy2(REPO / "tools/kb-guard.sh", tmp_path / "tools/kb-guard.sh")
    (tmp_path / "CLAUDE.md").write_text("instructions " * 40)
    kb = tmp_path / "KNOWLEDGEBASE.md"
    kb.write_text("knowledge " * 40)

    def run():
        return subprocess.run([BASH, str(tmp_path / "tools/kb-guard.sh"), "--verbose"], capture_output=True, text=True, cwd=str(tmp_path))
    assert run().returncode == 0
    text = kb.read_text()
    kb.unlink()
    first, second = run(), run()
    assert first.returncode == 1 and "is GONE" in first.stdout
    assert second.returncode == 0 and "is GONE" not in second.stdout and "still gone" in second.stdout
    kb.write_text(text)
    assert run().returncode == 0
    kb.unlink()
    assert run().returncode == 1, "a second loss alarms again"


# ---- 6.11
@pytest.mark.skipif(BASH is None, reason="no bash")
def test_keep_hand_set_keeps_a_path_with_an_apostrophe(tmp_path):
    src = (REPO / "setup.sh").read_text(encoding="utf-8")
    fn = re.search(r"^keep_hand_set\(\) \{.*?^\}\n", src, re.S | re.M).group(0)
    folder = tmp_path / "O'Brien"
    folder.mkdir()
    p = str(folder).replace("\\", "/")
    env_file = tmp_path / "paths.env"
    env_file.write_bytes(("KEY='" + p.replace("'", "'\\''") + "'\n# detected KEY='C:/elsewhere'\n").encode())
    script = tmp_path / "t.sh"
    script.write_bytes((fn + 'PATHS_ENV="$1"\nkeep_hand_set KEY C:/elsewhere\n').encode())
    r = subprocess.run([BASH, str(script), str(env_file).replace("\\", "/")], capture_output=True, text=True)
    assert r.stdout == p and "keeping the path you set by hand" in r.stderr, (r.stdout, r.stderr)


# ---- 6.10
@pytest.mark.skipif(BASH is None, reason="no bash")
def test_verify_release_refuses_an_unreleased_heading():
    src = (REPO / "tools/verify-release.sh").read_text(encoding="utf-8")
    assert re.search(r'grep -qi "unreleased"; then\n\s+bad "CHANGELOG section"', src)


# ---- 6.12
def test_a_case_with_no_id_is_a_failed_row(tmp_path):
    ac = _module("adapter_conformance_t", "tools/adapter_conformance.py")
    profile = {"block": {"exit": 2, "stdout_regex": None, "stderr_regex": None}, "allow": {"exit": 0},
               "write_tools": ["edit"], "shell_tools": ["sh"]}
    assert ac._profile_problem(profile) is None, "this test is about the case, so the profile must be valid"
    rows = ac.run_conformance(tmp_path, profile, {"cases": [{"expect": "allow", "payload": {}}]})
    assert len(rows) == 1 and not rows[0].ok and "string id" in str(rows[0])


# ---- 6.13
def test_the_daily_run_by_hand_waits_for_the_game(monkeypatch, capsys):
    sys.path.insert(0, str(REPO / "tools"))
    daily = _module("housekeep_daily_t", "tools/housekeep_daily.py")
    monkeypatch.setattr(daily.housekeep, "load_config", lambda _x: {"pause_while_running": ["SkyrimVR.exe"]})
    monkeypatch.setattr(daily, "running_games", lambda _n: ["SkyrimVR.exe"])
    monkeypatch.setattr(daily, "run", lambda *_a, **_k: pytest.fail("ran with the game open"))
    assert daily.main([]) == 1 and "SkyrimVR.exe running" in capsys.readouterr().out
    monkeypatch.setattr(daily, "run", lambda *_a, **_k: "planned")
    assert daily.main(["--dry-run"]) == 0


# ---- 7.6
def test_nested_ids_are_scrubbed():
    scrub = _module("scrub_t", "tools/payload-capture/scrub.py").scrub
    out = scrub({"tool_response": {"agent_id": "a1b2", "deep": [{"task_id": "x"}], "text": "kept"}}, "C:\\Users\\Nobody")
    assert out["tool_response"]["agent_id"] == "<id>" and out["tool_response"]["deep"][0]["task_id"] == "<id>"
    assert out["tool_response"]["text"] == "kept"


# ---- 7.8
def test_a_failed_fetch_install_leaves_no_staging_folder(tmp_path, monkeypatch):
    fetch = _module("fetch_t", "tools/vr-emu-driver/fetch.py")
    monkeypatch.setattr(fetch, "BUILD", tmp_path)
    real = fetch.os.replace
    monkeypatch.setattr(fetch.os, "replace", lambda *_a: (_ for _ in ()).throw(OSError("locked")))
    with pytest.raises(OSError):
        fetch.install({"a/b.txt": b"x"}, tmp_path / "dest")
    monkeypatch.setattr(fetch.os, "replace", real)
    assert not [p.name for p in tmp_path.iterdir()]


# ---- 8.1
def test_housekeep_accounting_can_fail():
    rows = {"plan: a": 5, "kept: b": 7}
    assert hk.accounting(rows, [{"bytes": 5}]) == "OK"
    assert hk.accounting(rows, [{"bytes": 4}]) == "ACCOUNTING MISMATCH"
    assert hk.accounting({"plan: a": 5}, []) == "ACCOUNTING MISMATCH"


def test_a_real_plan_passes_its_accounting(tree):  # noqa: F811
    import time
    launch(tree, time.strftime("launch-%Y%m%d-%H%M%S", time.localtime(time.time() - 10 * 86400)), 10)
    doc = hk.plan(cfg_for(tree))
    assert hk.accounting(doc["rows"], doc["actions"]) == "OK"


# ---- 8.2
def test_no_recycle_bin_no_gzip_plan(tree, monkeypatch):  # noqa: F811
    import time
    week = launch(tree, time.strftime("launch-%Y%m%d-%H%M%S", time.localtime(time.time() - 10 * 86400)), 10)
    monkeypatch.setattr(hk, "has_recycle_bin", lambda: False)
    doc = hk.plan(cfg_for(tree))
    assert not [a for a in doc["actions"] if a["class"] == "gzip"] and (week / "SkyrimNet.log").exists()
    assert any("no Recycle Bin" in k for k in doc["rows"])


# ---- 8.3
def test_a_report_size_steps_over_a_link(tmp_path):
    d = tmp_path / "store"
    (d / "sub").mkdir(parents=True)
    (d / "sub/a.txt").write_bytes(b"12345")
    target = tmp_path / "target"
    target.mkdir()
    (target / "big.bin").write_bytes(b"x" * 1000)
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(d / "link"))
    else:
        os.symlink(target, d / "link")
    try:
        assert hk.dir_size(d) == 5
    finally:
        os.rmdir(d / "link") if os.name == "nt" else (d / "link").unlink()


# ---- 8.8
def test_an_adapter_with_no_root_writes_nothing_in_the_agents_folder(tmp_path):
    d = tmp_path / "adapt"
    d.mkdir()
    shutil.copy2(REPO / "tools/adapting/adapter_template.py", d / "adapter.py")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    r = subprocess.run([sys.executable, "-B", str(d / "adapter.py"), "pre-tool"], input='{"tool_name":"Bash"}', capture_output=True, text=True, cwd=str(cwd))
    assert r.returncode != 0 or r.stdout, "the call still does not pass silently"
    assert not list(cwd.iterdir())


# ---- 8.10
@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
def test_read_only_keeps_the_execute_bit(tmp_path):
    f = tmp_path / "tool"
    f.write_bytes(b"x")
    f.chmod(0o755)
    hk._set_readonly(f)
    assert stat.S_IMODE(f.stat().st_mode) == 0o555


def test_read_only_really_is(tmp_path):
    f = tmp_path / "file"
    f.write_bytes(b"x")
    hk._set_readonly(f)
    assert not os.access(f, os.W_OK)
    f.chmod(stat.S_IWRITE | stat.S_IREAD)


# ---- 8.11
def test_a_dry_run_takes_no_permanent_backup(install, monkeypatch):  # noqa: F811
    calls = []

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
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps({"ok": True, "verify_ran": True}))])

    monkeypatch.setattr(gateway, "Backend", FakeBackend)
    monkeypatch.setattr(gateway, "independent_check", lambda *_a, **_k: {"records": {}, "errors": [], "complete": True})
    real = gateway.backup
    monkeypatch.setattr(gateway, "backup", lambda *a, **k: (calls.append(a), real(*a, **k))[1])
    args = {"in_place": "Sword.esp", "ops": [{"formid": "000800:Sword.esp", "field_path": "Name", "value": "new"}]}
    out = asyncio.run(gateway.Gateway(install).call("housecarl_apply", dict(args, dry_run=True)))
    assert out["dry_run"] is True and calls == []


# ---- 8.12
def test_missing_dotnet_is_named(tmp_path, monkeypatch):
    from housecarl_integration import Refusal, setup as hc_setup
    class _WindowsOs:
        name = "nt"

        def __getattr__(self, attr):
            return getattr(os, attr)

    monkeypatch.setattr(hc_setup, "os", _WindowsOs())    # setup.py's reference only: pathlib reads the real os.name
    monkeypatch.setattr(hc_setup.subprocess, "run", lambda *_a, **_k: (_ for _ in ()).throw(FileNotFoundError("WinError 2")))
    args = SimpleNamespace(root=str(tmp_path), mo2_instance="x", plugins_txt=None, config_dir=None)
    with pytest.raises(Refusal, match="Could not run dotnet"):
        hc_setup.setup(args)
