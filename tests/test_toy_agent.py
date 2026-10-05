"""ADAPTING.md, executed: a fake agent follows the document's steps and must end with a working block.

Steps mirrored from ADAPTING.md section 6: copy the template, fill the slots, run conformance, then run a
decoy .esp write through the AGENT (not through the adapter) and require that the file does not exist.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO

TOY = REPO / "tests" / "toyagent" / "toy_agent.py"
EX = REPO / "tools" / "adapting" / "examples"
TPL = REPO / "tools" / "adapting" / "adapter_template.py"
# profile -> (slots file, adapter profile). A profile regex is fully anchored over the whole stream.
PROFILES = {
    "json-deny": ("json_deny.slots.py", {"block": {"exit": 0, "stdout_regex": '\\{"toy_decision": "block".*', "stderr_regex": None},
                                         "allow": {"exit": 0}, "write_tools": ["write_file"], "shell_tools": ["run_shell"]}),
    "exit2": ("exit2.slots.py", {"block": {"exit": 2, "stdout_regex": None, "stderr_regex": None}, "allow": {"exit": 0},
                                 "write_tools": ["edit"], "shell_tools": ["sh"]}),
}
# The tool names the toy agent uses for each op, per profile (tests/toyagent/toy_agent.py native()).
TOOL = {"json-deny": {"write": "write_file", "shell": "run_shell"}, "exit2": {"write": "edit", "shell": "sh"}}
# The pinned engine wording (Task 0 measurement note, section 4), asserted by text and not by a guess.
PLUGIN_WRITE_TEXT = "plugin and archive files (.esp .esm .esl .bsa .ba2) cannot be written directly"
PLUGIN_SHELL_TEXT = "writes Decoy.esp as raw text"


def _conf():
    spec = importlib.util.spec_from_file_location("adapter_conformance", REPO / "tools" / "adapter_conformance.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


conf = _conf()


def cases_for(profile: str) -> dict:
    def n(op, **kw):
        if profile == "json-deny":
            return ({"toy_event": "before_tool", "tool": "write_file", "args": {"path": kw["path"], "content": "x"}, "cwd": "{ROOT}"}
                    if op == "write" else {"toy_event": "before_tool", "tool": "run_shell", "args": {"cmd": kw["cmd"]}, "cwd": "{ROOT}"})
        return ({"hook": "pre", "call": {"name": "edit", "file": kw["path"]}, "cwd": "{ROOT}"}
                if op == "write" else {"hook": "pre", "call": {"name": "sh", "cmdline": kw["cmd"]}, "cwd": "{ROOT}"})
    return {"cases": [
        {"id": "decoy-plugin-write", "tool": TOOL[profile]["write"], "expect": "block",
         "payload": n("write", path="{ROOT}/Data/Decoy.esp")},
        {"id": "benign-write", "expect": "allow", "payload": n("write", path="{ROOT}/notes.txt")},
        # quoted: an install path may contain a space, and an unquoted redirect then targets the wrong file
        {"id": "decoy-plugin-shell", "tool": TOOL[profile]["shell"], "expect": "block",
         "payload": n("shell", cmd='echo x > "{ROOT}/Data/Decoy.esp"')},
        {"id": "benign-shell", "expect": "allow", "payload": n("shell", cmd="echo hello")},
        {"id": "empty-payload", "expect": "block", "payload": ""},
        {"id": "garbage-payload", "expect": "block", "payload_hex": "6e6f74206a736f6e00ff"},
    ]}


def staged(tmp_path: Path, profile: str, template_text: str = None):
    slots = (EX / PROFILES[profile][0]).read_text(encoding="ascii")
    d = tmp_path / "src" / "toy"
    d.mkdir(parents=True)
    (d / "adapter.py").write_text(conf.compose(template_text or TPL.read_text(encoding="ascii"), slots),
                                  encoding="ascii", newline="\n")
    root = conf.make_install(d, tmp_path / "install")
    return root, root / "tools" / "adapters" / "toy" / "adapter.py"


def drive(root: Path, adapter: Path, profile: str, calls: list, hook_timeout: float = 30, env: dict = None):
    script = root / "calls.json"
    script.write_text(json.dumps(calls), encoding="utf-8")
    e = dict(os.environ, **(env or {}))
    cp = subprocess.run([sys.executable, str(TOY), "--profile", profile, "--hook", json.dumps([sys.executable, str(adapter)]),
                         "--script", str(script), "--cwd", str(root), "--hook-timeout", str(hook_timeout)],
                        capture_output=True, env=e, timeout=300)
    assert cp.returncode == 0, cp.stderr.decode()
    return json.loads(cp.stdout)


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_conformance_passes_on_the_adapter_a_fake_agent_builds_from_the_document(tmp_path, profile):
    root, adapter = staged(tmp_path, profile)
    rows = conf.run_conformance(adapter.parent, PROFILES[profile][1], cases_for(profile))
    assert all(r.ok for r in rows), [r for r in rows if not r.ok]
    assert {r.id for r in rows} >= {"template-identical", "case:decoy-plugin-write", "case:decoy-plugin-shell",
                                    "fault:engine-hang", "fault:engine-allow-but-exit-1", "fault:engine-missing",
                                    "fault:engine-deny-nonce", "fault:engine-allow-valid"}


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_end_to_end_a_decoy_plugin_write_is_blocked_by_the_agent(tmp_path, profile):
    root, adapter = staged(tmp_path, profile)
    decoy, ok = root / "Data" / "Decoy.esp", root / "notes.txt"
    r = drive(root, adapter, profile, [{"op": "write", "path": str(decoy)}, {"op": "write", "path": str(ok)}])
    assert r[0]["blocked"] and not decoy.exists() and PLUGIN_WRITE_TEXT in r[0]["reason"]
    assert not r[1]["blocked"] and ok.exists()


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_end_to_end_a_shell_redirect_into_a_plugin_is_blocked(tmp_path, profile):
    root, adapter = staged(tmp_path, profile)
    decoy = root / "Data" / "Decoy.esp"
    (root / "Data").mkdir()
    r = drive(root, adapter, profile, [{"op": "shell", "cmd": 'echo x > "{}"'.format(decoy)}])
    assert r[0]["blocked"] and not decoy.exists() and PLUGIN_SHELL_TEXT in r[0]["reason"]


FAULT_SOURCES = {k: v for k, v in conf.FAULTS.items()}


@pytest.mark.parametrize("profile", sorted(PROFILES))
@pytest.mark.parametrize("fault", sorted(FAULT_SOURCES))
def test_a_broken_engine_fails_closed_through_the_agent(tmp_path, profile, fault):
    root, adapter = staged(tmp_path, profile)
    run_py = root / "tools" / "skyguard" / "run.py"
    if FAULT_SOURCES[fault] is None:
        run_py.unlink()
    else:
        run_py.write_text(FAULT_SOURCES[fault], encoding="utf-8", newline="\n")
    decoy = root / "Data" / "Decoy.esp"
    r = drive(root, adapter, profile, [{"op": "write", "path": str(decoy)}], env={"SKYGUARD_ADAPTER_TIMEOUT_S": "2"})
    assert r[0]["blocked"] and not decoy.exists()
    # For exit2 the reason IS stderr, so this also stops a missing adapter file passing as a block (exit 2 alone).
    assert "GUARD INERT" in r[0]["reason"]


def test_control_a_naive_hook_lets_the_toy_write_the_decoy(tmp_path):
    """The toy must be able to fail open, or every block above proves nothing."""
    root, adapter = staged(tmp_path, "json-deny")
    naive = root / "naive.py"
    naive.write_text("import subprocess, sys\n"
                     "cp = subprocess.run([sys.executable, %r], input=sys.stdin.buffer.read(), capture_output=True)\n"
                     "sys.exit(0)\n" % str(adapter.with_name("nonexistent.py")))
    decoy = root / "Data" / "Decoy.esp"
    script = root / "calls.json"
    script.write_text(json.dumps([{"op": "write", "path": str(decoy)}]))
    cp = subprocess.run([sys.executable, str(TOY), "--profile", "json-deny", "--hook", json.dumps([sys.executable, str(naive)]),
                         "--script", str(script), "--cwd", str(root)], capture_output=True, timeout=120)
    assert json.loads(cp.stdout)[0]["executed"] and decoy.exists()


def test_hook_timeout_shorter_than_the_adapter_fails_open(tmp_path):
    """ADAPTING.md section 6 step 5: the agent's hook timeout must exceed the adapter's, or a hung engine runs the call."""
    root, adapter = staged(tmp_path, "json-deny")
    (root / "tools" / "skyguard" / "run.py").write_text("import time; time.sleep(60)\n")
    decoy = root / "Data" / "Decoy.esp"
    r = drive(root, adapter, "json-deny", [{"op": "write", "path": str(decoy)}], hook_timeout=1,
              env={"SKYGUARD_ADAPTER_TIMEOUT_S": "5"})
    assert r[0]["executed"] and decoy.exists()
    root2, adapter2 = staged(tmp_path / "ok", "json-deny")
    (root2 / "tools" / "skyguard" / "run.py").write_text("import time; time.sleep(60)\n")
    decoy2 = root2 / "Data" / "Decoy.esp"
    r2 = drive(root2, adapter2, "json-deny", [{"op": "write", "path": str(decoy2)}], hook_timeout=30,
               env={"SKYGUARD_ADAPTER_TIMEOUT_S": "2"})
    assert r2[0]["blocked"] and not decoy2.exists()


# ---- the check must be able to fail: each mutant of the machinery has to turn a NAMED row red --------------
MUTANTS = {  # id -> (exact text in the template, replacement, the row that must go red)
    "fail-open": ("out, code, err = _block_for_failure(e.__class__.__name__ + \" \" + str(e))",
                  "out, code, err = render_allow(\"\")", "fault:engine-crash"),
    "ignore-exit-code": ("    if cp.returncode != 0:\n        raise RuntimeError(\"engine exited {}\".format(cp.returncode))\n",
                         "", "fault:engine-allow-but-exit-1"),
    "ignore-version": ("v.get(\"v\") != 1 or ", "", "fault:engine-wrong-version"),
    "deny-to-allow": ("    if decision == \"deny\":\n        return _checked(render_block(reason))",
                      "    if decision == \"deny\":\n        return _checked(render_allow(context))", "case:decoy-plugin-write"),
    "ignore-marker": ("    if not text.startswith(MARKER):\n        raise RuntimeError(\"engine answered without the marker\")\n",
                      "", "fault:engine-no-marker"),
    "ignore-decision": ("v.get(\"decision\") not in (\"allow\", \"ask\", \"deny\")", "False", "fault:engine-unknown-decision"),
    "no-timeout": ("        timeout=float(os.environ.get(\"SKYGUARD_ADAPTER_TIMEOUT_S\", ADAPTER_TIMEOUT_S)), env=env)",
                   "        env=env)", "fault:engine-hang"),
}


@pytest.mark.parametrize("mutant", sorted(MUTANTS))
def test_each_mutant_of_the_machinery_turns_the_named_row_red(tmp_path, mutant, monkeypatch):
    old, new, row = MUTANTS[mutant]
    tpl = TPL.read_text(encoding="ascii")
    assert tpl.count(old) == 1, "mutation anchor drifted: edit MUTANTS, do not weaken the template"
    monkeypatch.setattr(conf, "PROBE_TIMEOUT_S", 6.0)
    root, adapter = staged(tmp_path, "json-deny", tpl.replace(old, new))
    rows = conf.run_conformance(adapter.parent, PROFILES["json-deny"][1], cases_for("json-deny"), port=True)
    red = {r.id for r in rows if not r.ok}
    assert row in red, "mutant {} was not caught by {} (red rows: {})".format(mutant, row, sorted(red))


def test_a_template_edit_outside_the_slots_is_reported_by_conformance(tmp_path):
    tpl = TPL.read_text(encoding="ascii").replace("ENGINE_BUDGET_S = 20", "ENGINE_BUDGET_S = 21")
    root, adapter = staged(tmp_path, "json-deny", tpl)
    rows = conf.run_conformance(adapter.parent, PROFILES["json-deny"][1], cases_for("json-deny"))
    assert [r for r in rows if r.id == "template-identical" and not r.ok]


def test_conformance_rejects_an_adapter_that_drops_the_paths(tmp_path):
    slots = (EX / "json_deny.slots.py").read_text(encoding="ascii")
    assert slots.count('paths=[args["path"]]') == 1
    slots = slots.replace('paths=[args["path"]]', "paths=[]")
    d = tmp_path / "src" / "toy"
    d.mkdir(parents=True)
    (d / "adapter.py").write_text(conf.compose(TPL.read_text(encoding="ascii"), slots), encoding="ascii", newline="\n")
    rows = conf.run_conformance(d, PROFILES["json-deny"][1], cases_for("json-deny"))
    # The engine refuses a write that names no file (NO_PATHS, fail closed), so the decoy stays blocked and the
    # row that goes red is the benign write the adapter can no longer let through.
    red = {r.id for r in rows if not r.ok}
    assert "case:benign-write" in red, sorted(red)


def test_this_module_has_no_skips():
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("skip", "skipif", "xfail"):
            raise AssertionError("a skipped toy-agent test is a green that examined nothing")
