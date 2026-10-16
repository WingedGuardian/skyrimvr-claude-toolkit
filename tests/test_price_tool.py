"""scripts/guard-corpus/price.py: per-rule old-vs-new engine pricing over (synthetic) history.

Every check here has a twin that goes red: an identical pair reads all zeros, a pair that differs in ONE
rule reads non-zero for that rule and zero for every other, and an out-dir inside the repo is refused.
"""
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REPO

SKYGUARD = REPO / "tools" / "skyguard"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / "guard-corpus" / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


price = _mod("price")
replay = _mod("replay")


def _line(name, inp, cwd="C:/work"):
    return json.dumps({"cwd": cwd, "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}})


@pytest.fixture
def history(tmp_path):
    h = tmp_path / "hist"
    h.mkdir()
    lines = [
        _line("Bash", {"command": "ls"}),
        _line("Bash", {"command": "ls"}),                                  # exact duplicate: deduplicated
        _line("Bash", {"command": "ls"}, cwd="C:/sentinel"),               # same command, other cwd: kept
        # old: WRITE-LOADORDER; broadened: + WRITE-INI, which fires AFTER it (so first-rule attribution is wrong)
        _line("Bash", {"command": "echo x >> plugins.txt 2> notes.md"}),
        _line("PowerShell", {"command": "Add-Content loadorder.txt x"}),   # WRITE-LOADORDER
        _line("Edit", {"file_path": "C:/work/a.py"}),
        _line("mcp__devbench-vr__console", {"action": "exec", "command": "help"}),
        _line("Read", {"file_path": "C:/work/a.py"}),                      # not a priced tool
    ]
    (h / "s.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return h


def _scratch_engine(tmp_path, name, edits):
    dest = tmp_path / name / "skyguard"
    shutil.copytree(SKYGUARD, dest, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(SKYGUARD.parent / "modding_harness", dest.parent / "modding_harness",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(SKYGUARD.parent / "modding-harness.lock.json", dest.parent / "modding-harness.lock.json")
    for rel, old, new in edits:
        p = dest / rel
        text = p.read_bytes().decode("utf-8")
        assert text.count(old) == 1, (rel, old)
        p.write_bytes(text.replace(old, new).encode("utf-8"))
    return dest


def _run(tmp_path, base_dir, new_dir, history, only_rule=None):
    out = tmp_path / "out"
    return price.price(price.stage_tree(base_dir, tmp_path / "b"), price.stage_tree(new_dir, tmp_path / "n"),
                       history, out, only_rule=only_rule), out


def test_extract_wide_takes_powershell_mcp_and_cwd_and_dedupes_on_cwd(history):
    items, counts = replay.extract(history, wide=True)
    assert [(i["tool_name"], i["cwd"]) for i in items] == [
        ("Bash", "C:/work"), ("Bash", "C:/sentinel"), ("Bash", "C:/work"), ("PowerShell", "C:/work"),
        ("Edit", "C:/work"), ("mcp__devbench-vr__console", "C:/work")]
    assert counts["Bash"] == 4 and counts["PowerShell"] == 1 and "Read" not in counts
    assert items[-1]["tool_input"] == {"action": "exec", "command": "help"}


def test_extract_wide_takes_every_devbench_spelling(tmp_path):
    """Plan 8 T6 fix round 1: the pricing extractor selects DevBench tools as protect-mcp's matcher and judge do --
    any tool name containing devbench, in any case -- not by the old `mcp__devbench` prefix."""
    h = tmp_path / "hist"
    h.mkdir()
    names = ["mcp__skyrim-devbench__console", "mcp__DevBench-vr__game", "mcp__my__devbench__console",
             "mcp__other__console"]
    (h / "s.jsonl").write_text("\n".join(_line(n, {"command": "help"}) for n in names) + "\n", encoding="utf-8")
    items, _ = replay.extract(h, wide=True)
    assert [i["tool_name"] for i in items] == names[:3]


def test_extract_default_is_unchanged_for_replay(history):
    items, counts = replay.extract(history)
    assert {i["hook"] for i in items} == {"protect-bash.sh", "protect-files.sh"}
    assert counts["PowerShell_not_wired"] == 1


def test_identical_engines_price_to_all_zeros(tmp_path, history):
    s, _ = _run(tmp_path, SKYGUARD, SKYGUARD, history)
    assert s["N"] == 6
    assert all(v == 0 for v in s["totals"].values()), s["totals"]
    assert s["rules"] == {} and s["reason_moved"] == 0
    assert set(s["matrix"]) <= {"allow>allow", "advise>advise"}
    assert s["N_per_tool"] == {"Bash": 3, "PowerShell": 1, "Edit": 1, "mcp__*": 1}


def test_a_broadened_rule_moves_only_that_rule(tmp_path, history):
    new = _scratch_engine(tmp_path, "scratch", [
        ("targets.py", r'(Skyrim\.ini|SkyrimVR\.ini|SkyrimPrefs\.ini|SkyrimCustom\.ini)"', r'(Skyrim\.ini|SkyrimVR\.ini|SkyrimPrefs\.ini|SkyrimCustom\.ini|notes\.md)"')])
    s, out = _run(tmp_path, SKYGUARD, new, history)
    # The plugins.txt/notes.md write newly fires WRITE-INI while WRITE-LOADORDER (fired first) fires in both:
    # attribution must name the rule that is new, not the first rule the item fires.
    assert s["rules"] == {"S-WRITE-INI": {"newly-advise": 1}}
    assert s["totals"]["newly-advise"] == 1 and s["matrix"].get("advise>advise", 0) >= 1
    rows = [json.loads(x) for x in (out / "S-WRITE-INI__newly-advise.jsonl").read_text("utf-8").splitlines()]
    assert [r["item"]["tool_input"]["command"] for r in rows] == ["echo x >> plugins.txt 2> notes.md"]


def test_a_rule_that_stops_firing_reads_no_longer(tmp_path, history):
    new = _scratch_engine(tmp_path, "scratch", [
        ("targets.py", r'(loadorder\.txt|plugins\.txt)"', r'(plugins\.txt)"')])
    s, _ = _run(tmp_path, SKYGUARD, new, history)
    assert s["rules"] == {"S-WRITE-LOADORDER": {"no-longer-advise": 1}}    # the PowerShell loadorder.txt write
    assert s["matrix"].get("advise>allow") == 1


def test_only_rule_filters_the_report(tmp_path, history):
    new = _scratch_engine(tmp_path, "scratch", [
        ("targets.py", r'(Skyrim\.ini|SkyrimVR\.ini|SkyrimPrefs\.ini|SkyrimCustom\.ini)"', r'(Skyrim\.ini|SkyrimVR\.ini|SkyrimPrefs\.ini|SkyrimCustom\.ini|notes\.md)"')])
    s, _ = _run(tmp_path, SKYGUARD, new, history, only_rule="S-WRITE-LOADORDER")
    assert s["rules"] == {}


def test_the_driver_hands_the_item_cwd_to_an_engine_that_accepts_it(tmp_path, history):
    new = _scratch_engine(tmp_path, "scratch", [(
        "shell_rules.py",
        # The engine takes cwd since Plan 5 Task 6; the sentinel proves the driver hands it the item's cwd.
        "    if command.strip() == \"\":\n        audit.rule(HOOK, \"S-INERT-NOCMD\"",
        "    if cwd == \"C:/sentinel\":\n"
        "        audit.rule(HOOK, \"S-LOADORDER-REF\", \"advise\", \"cwd seen\")\n"
        "    if command.strip() == \"\":\n        audit.rule(HOOK, \"S-INERT-NOCMD\"")])
    s, _ = _run(tmp_path, SKYGUARD, new, history)
    # Only the one `ls` that ran in C:/sentinel; the old engine, which has no cwd parameter, still priced.
    assert s["rules"] == {"S-LOADORDER-REF": {"newly-advise": 1}}


def test_out_dir_inside_the_repo_is_refused(tmp_path, history):
    target = REPO / "scripts" / "guard-corpus" / "_price_out_should_not_exist"
    with pytest.raises(SystemExit) as e:
        price.main(["--base-dir", str(SKYGUARD), "--history", str(history), "--out-dir", str(target)])
    assert "outside the repo" in str(e.value)
    assert not target.exists()


def test_cli_prices_the_working_tree_against_itself(tmp_path, history, capsys):
    rc = price.main(["--base-dir", str(SKYGUARD), "--history", str(history), "--out-dir", str(tmp_path / "o")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "N=6" in out and "'newly-deny': 0" in out


def test_a_decision_change_on_the_same_rule_is_priced(tmp_path, history):
    """advise -> deny on a rule that fires in both engines: same id, so only the decision differs."""
    new = _scratch_engine(tmp_path, "scratch", [(
        "shell_rules.py",
        '    for rid, file in list(wr.advice) + anchored.automod(parsed):\n',
        '    for rid, file in list(wr.advice) + anchored.automod(parsed):\n'
        '        if rid == "S-WRITE-LOADORDER" and file == "loadorder.txt":\n'
        '            audit.rule(HOOK, rid, "deny", "x")\n'
        '            return Verdict("deny", "x")\n')])
    s, _ = _run(tmp_path, SKYGUARD, new, history)
    assert s["rules"] == {"S-WRITE-LOADORDER": {"newly-deny": 1, "no-longer-advise": 1}}
    assert s["totals"]["newly-deny"] == 1 and s["totals"]["no-longer-advise"] == 1
    assert s["matrix"].get("advise>deny") == 1


def test_a_crash_in_the_new_engine_is_flagged_not_clean(tmp_path, history):
    new = _scratch_engine(tmp_path, "scratch", [(
        "shell_rules.py",
        '    advice = Advice()\n',
        '    advice = Advice()\n    if command == "ls":\n        raise RuntimeError("boom")\n')])
    s, _ = _run(tmp_path, SKYGUARD, new, history)
    assert s["errors"] == {"old": 0, "new": 2}                      # the two `ls` items (cwd differs)
    assert s["rules"] == {"<engine-error>": {"newly-error": 2}}
    assert s["totals"]["newly-error"] == 2


def test_parse_failures_are_reported_per_tool_when_the_engine_has_the_parser(tmp_path):
    """Plan 5 Task 5 (R4 gate): the driver asks the NEW tree's shellparse whether it can read each command. An old
    tree without shellparse reads 0; an unterminated quote in Bash and PowerShell each count once."""
    h = tmp_path / "hist"
    h.mkdir()
    lines = [_line("Bash", {"command": "ls"}), _line("Bash", {"command": "echo 'oops"}),
             _line("PowerShell", {"command": "Get-Item 'oops"}), _line("PowerShell", {"command": "Get-Item a"})]
    (h / "s.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # An engine from before the parser: no shellparse.py, and (since Tasks 6-7, where the ReSaver and delete rules
    # moved onto the parser) a shell rule table that does not use one.
    old = _scratch_engine(tmp_path, "old", [])
    (old / "shellparse.py").unlink()
    (old / "deletes.py").unlink()
    (old / "shell_rules.py").write_text(
        "from .verdict import Verdict\n\n\ndef decide(command, roots, audit, cwd='', shell=''):\n    return Verdict()\n",
        encoding="utf-8")
    s, _ = _run(tmp_path, old, SKYGUARD, h)
    assert s["parse_failures"] == {"old": 0, "new": 2}
    assert s["parse_failures_new_per_tool"] == {"Bash": 1, "PowerShell": 1}
    assert s["totals"]["newly-error"] == 0


def test_two_engine_dirs_load_separately(tmp_path):
    edits = [("shell_rules.py", "    advice = Advice()\n",
              '    advice = Advice()\n    return Verdict("deny", __file__ + "|" + __import__("modding_harness").PRICING_ENGINE_SENTINEL)\n')]
    a = _scratch_engine(tmp_path, "engA", edits)
    b = _scratch_engine(tmp_path, "engB", edits)
    for engine, marker in ((a, "engA"), (b, "engB")):
        with (engine.parent / "modding_harness/__init__.py").open("a", encoding="utf-8") as stream:
            stream.write('\nPRICING_ENGINE_SENTINEL = ' + repr(marker) + '\n')
    items = tmp_path / "items.jsonl"
    items.write_text(json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": ""}) + "\n")
    ra = price.run_engine(price.stage_tree(a, tmp_path / "ta"), items, tmp_path / "ra.jsonl", "C:/g", "C:/g")[0]
    rb = price.run_engine(price.stage_tree(b, tmp_path / "tb"), items, tmp_path / "rb.jsonl", "C:/g", "C:/g")[0]
    assert "ta" in Path(ra["text"]).parts and "tb" in Path(rb["text"]).parts
    assert ra["text"] != rb["text"]
    assert ra["text"].endswith("|engA") and rb["text"].endswith("|engB")


def _committed_engine(tmp_path, monkeypatch, module, with_core=True):
    repository = tmp_path / "repository"
    tools = repository / "tools"
    tools.mkdir(parents=True)
    for name in (("skyguard", "modding_harness") if with_core else ("skyguard",)):
        shutil.copytree(SKYGUARD.parent / name, tools / name, ignore=shutil.ignore_patterns("__pycache__"))
    if with_core:
        shutil.copy2(SKYGUARD.parent / "modding-harness.lock.json", tools / "modding-harness.lock.json")
    def git(*args):
        subprocess.run(["git", "-c", "core.autocrlf=false", "-C", str(repository), *args],
                       check=True, capture_output=True)
    git("init", "-q")
    git("add", "tools")
    git("-c", "user.name=Pricing Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
    monkeypatch.setattr(module, "REPO", repository)
    return tools


@pytest.mark.parametrize("with_core", [True, False])
def test_export_uses_its_committed_optional_runtime_not_working_or_current_bytes(tmp_path, monkeypatch, with_core):
    tools = _committed_engine(tmp_path, monkeypatch, price, with_core)
    if with_core:
        original = (tools / "modding_harness/__init__.py").read_bytes()
        lock = (tools / "modding-harness.lock.json").read_bytes()
        (tools / "modding_harness/__init__.py").write_text("DIRTY = True\n")
    staged = price.export_base("HEAD", tmp_path / "export")
    assert (staged / "skyguard/shell_rules.py").is_file()
    if with_core:
        assert (staged / "modding_harness/__init__.py").read_bytes() == original
        assert (staged / "modding-harness.lock.json").read_bytes() == lock
    else:
        assert not (staged / "modding_harness").exists()
        assert not (staged / "modding-harness.lock.json").exists()


@pytest.mark.parametrize("route", ["working", "committed"])
def test_actual_runtime_omission_regressions_produce_engine_errors(tmp_path, monkeypatch, route):
    """Run the real driver before/after dropping the dependency in an owned script copy."""
    folder = tmp_path / "pricing-script"
    folder.mkdir()
    source = (REPO / "scripts/guard-corpus/price.py").read_text(encoding="utf-8")
    path = folder / "price.py"
    shutil.copy2(REPO / "scripts/guard-corpus/engine_eval.py", folder / "engine_eval.py")
    def load(text):
        path.write_text(text, encoding="utf-8")
        spec = importlib.util.spec_from_file_location("owned_pricing_control", path)
        module = importlib.util.module_from_spec(spec)
        # -B disables bytecode; a same-length, same-second mutant cannot reuse it.
        spec.loader.exec_module(module)
        return module
    baseline = load(source)
    tools = _committed_engine(tmp_path, monkeypatch, baseline)
    items = tmp_path / "items.jsonl"
    items.write_text(json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "untouched.py")},
                                "cwd": str(tmp_path)}) + "\n")
    def evaluate(module, label):
        staged = (module.stage_tree(tools / "skyguard", tmp_path / label) if route == "working"
                  else module.export_base("HEAD", tmp_path / label))
        return module.run_engine(staged, items, tmp_path / (label + ".jsonl"), str(tmp_path / "fake-game"), "")[0]
    assert evaluate(baseline, "baseline")["decision"] == "allow"
    before, after = ((
        '    for name in ("modding_harness", "modding-harness.lock.json"):', '    for name in ():')
        if route == "working" else ('f"{rev}:tools", *names]', 'f"{rev}:tools", "skyguard"]'))
    assert source.count(before) == 1
    mutant = load(source.replace(before, after))
    monkeypatch.setattr(mutant, "REPO", tools.parent)
    result = evaluate(mutant, "mutant")
    assert result["decision"] == "error" and "modding_harness" in result["text"]


def test_only_this_tools_stale_files_are_cleared(tmp_path, history):
    out = tmp_path / "out"
    out.mkdir()
    (out / "S-OLD__newly-deny.jsonl").write_text("stale")
    (out / "mine__notes.jsonl").write_text("keep")
    _run(tmp_path, SKYGUARD, SKYGUARD, history)
    assert (out / "S-OLD__newly-deny.jsonl").read_text() == "stale"
    assert (out / "mine__notes.jsonl").read_text() == "keep"


def test_previous_owned_samples_are_archived_and_current_manifest_is_exact(tmp_path, history):
    new = _scratch_engine(tmp_path, "scratch", [
        ("targets.py", r'(loadorder\.txt|plugins\.txt)"', r'(plugins\.txt)"')])
    _, out = _run(tmp_path, SKYGUARD, new, history)
    key = "S-WRITE-LOADORDER__no-longer-advise.jsonl"
    original = (out / key).read_bytes()
    ownership = json.loads((out / ".price-owned.json").read_text())
    assert set(ownership["files"]) == {key}
    assert ownership["files"][key]["sha256"] == __import__("hashlib").sha256(original).hexdigest()
    price.price(tmp_path / "b", tmp_path / "b", history, out)
    assert not (out / key).exists()
    assert [p.read_bytes() for p in out.glob(".price-previous-*/" + key)] == [original]
    assert json.loads((out / ".price-owned.json").read_text())["files"] == {}


def test_modified_owned_samples_are_preserved_and_cannot_be_overwritten(tmp_path, history):
    new = _scratch_engine(tmp_path, "scratch", [
        ("targets.py", r'(loadorder\.txt|plugins\.txt)"', r'(plugins\.txt)"')])
    _, out = _run(tmp_path, SKYGUARD, new, history)
    path = out / "S-WRITE-LOADORDER__no-longer-advise.jsonl"
    path.write_text("foreign edit")
    with pytest.raises((SystemExit, FileExistsError)):
        price.price(tmp_path / "b", tmp_path / "n", history, out)
    assert path.read_text() == "foreign edit"


def test_unowned_sample_collision_is_preserved(tmp_path, history):
    out = tmp_path / "out"
    out.mkdir()
    target = out / "S-WRITE-LOADORDER__no-longer-advise.jsonl"
    target.write_text("foreign sample")
    new = _scratch_engine(tmp_path, "scratch", [
        ("targets.py", r'(loadorder\.txt|plugins\.txt)"', r'(plugins\.txt)"')])
    with pytest.raises((SystemExit, FileExistsError)):
        _run(tmp_path, SKYGUARD, new, history)
    assert target.read_text() == "foreign sample"


@pytest.mark.parametrize("files", [{"../foreign.jsonl": {"sha256": "a" * 64, "size": 1}},
                                  {"S-A__newly-deny.jsonl": {"sha256": "bad", "size": 1}}])
def test_invalid_sample_ownership_is_refused_before_writing_outputs(tmp_path, history, files):
    out = tmp_path / "out"
    out.mkdir()
    (out / ".price-owned.json").write_text(json.dumps({"version": 1, "tool": "guard-corpus-price", "files": files}))
    with pytest.raises(SystemExit, match="ownership"):
        _run(tmp_path, SKYGUARD, SKYGUARD, history)
    assert not (out / "items.jsonl").exists()
