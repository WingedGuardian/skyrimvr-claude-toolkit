"""The adapter template itself: parses on 3.8, is closed when unfilled, markers are unique."""
from __future__ import annotations

import ast
import subprocess
import sys

from conftest import REPO

TPL = REPO / "tools" / "adapting" / "adapter_template.py"
EXAMPLES = REPO / "tools" / "adapting" / "examples"


def test_template_and_examples_parse_on_python_3_8_and_are_ascii():
    files = [TPL, *sorted(EXAMPLES.glob("*.py"))]
    assert len(files) == 3, files
    for p in files:
        text = p.read_bytes().decode("ascii")          # raises on any non-ASCII byte
        ast.parse(text, feature_version=(3, 8))


def test_slot_markers_appear_exactly_once_in_the_template_and_every_example():
    files = [TPL, *sorted(EXAMPLES.glob("*.slots.py"))]
    assert len(files) == 3, files
    for p in files:
        t = p.read_text(encoding="ascii")
        assert t.count("# SLOTS-BEGIN") == 1 and t.count("# SLOTS-END") == 1, p.name
        assert t.index("# SLOTS-BEGIN") < t.index("# SLOTS-END"), p.name


def test_an_unfilled_template_refuses_every_call(tmp_path):
    """Closed before correct: no slot is filled, so even a benign call is blocked (exit 2 + stderr)."""
    root = tmp_path / "inst"
    (root / "tools" / "adapters" / "x").mkdir(parents=True)
    (root / "tools" / "skyguard").mkdir(parents=True)
    (root / "tools" / "skyguard" / "run.py").write_text("print('would allow')\n")
    adapter = root / "tools" / "adapters" / "x" / "adapter.py"
    adapter.write_text(TPL.read_text(encoding="ascii"), encoding="ascii", newline="\n")
    cp = subprocess.run([sys.executable, str(adapter)], input=b'{"anything": 1}', capture_output=True, timeout=60)
    assert cp.returncode == 2 and b"GUARD INERT" in cp.stderr and cp.stdout == b""


# ---- Task 3: composed-template tests and the conformance tool ---------------------------------------
import importlib.util
import json
import os
import time

EX = REPO / "tools" / "adapting" / "examples"


def _conf():
    spec = importlib.util.spec_from_file_location("adapter_conformance", REPO / "tools" / "adapter_conformance.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_compose_replaces_only_the_slots_region():
    c = _conf()
    t = TPL.read_text(encoding="ascii")
    s = (EX / "json_deny.slots.py").read_text(encoding="ascii")
    out = c.compose(t, s)
    assert c.outside_hash(out) == c.outside_hash(t)
    assert 'AGENT_NAME = "toy-json"' in out and out.count("# SLOTS-BEGIN") == 1
    ast.parse(out, feature_version=(3, 8))


def test_outside_hash_sees_a_one_byte_change_in_the_machinery_but_not_in_the_slots():
    c = _conf()
    t = TPL.read_text(encoding="ascii")
    assert c.outside_hash(t.replace("ADAPTER_TIMEOUT_S = 25", "ADAPTER_TIMEOUT_S = 26")) != c.outside_hash(t)
    assert c.outside_hash(t.replace('AGENT_NAME = "unnamed"', 'AGENT_NAME = "other"')) == c.outside_hash(t)
    assert c.outside_hash(t.replace("\n", "\r\n")) == c.outside_hash(t)


def _toy(tmp_path, slots_text, name="toy"):
    c = _conf()
    d = tmp_path / name
    d.mkdir()
    (d / "adapter.py").write_text(c.compose(TPL.read_text(encoding="ascii"), slots_text), encoding="ascii", newline="\n")
    return d


JSON_SLOTS = (EX / "json_deny.slots.py").read_text(encoding="ascii")
EXIT2_SLOTS = (EX / "exit2.slots.py").read_text(encoding="ascii")
MAP_HEAD = "d = json.loads(raw.decode(\"utf-8\"))\n    tool,"


def _run_adapter(root, name, payload):
    return subprocess.run([sys.executable, str(root / "tools" / "adapters" / name / "adapter.py")],
                          input=payload, capture_output=True, timeout=60)


def test_hostile_slots_cannot_exit_zero_or_print_an_allow(tmp_path):
    """A slot that calls sys.exit(0) must still end in the block: the machinery catches BaseException."""
    c = _conf()
    hostile = JSON_SLOTS.replace(MAP_HEAD, "sys.exit(0)\n    tool,", 1)
    assert "sys.exit(0)" in hostile
    d = _toy(tmp_path, hostile)
    root = c.make_install(d, tmp_path / "inst")
    cp = _run_adapter(root, "toy", b'{"tool":"write_file","args":{"path":"x"}}')
    assert json.loads(cp.stdout)["toy_decision"] == "block" and b"GUARD INERT" in cp.stdout


def test_a_slot_that_prints_an_allow_to_stdout_cannot_corrupt_the_answer(tmp_path):
    """The agent reads stdout: anything a slot prints there must never reach it."""
    c = _conf()
    line = "print('{\"toy_decision\":\"allow\"}')\n    "
    hostile = JSON_SLOTS.replace(MAP_HEAD, line + MAP_HEAD, 1)
    assert line in hostile
    d = _toy(tmp_path, hostile)
    root = c.make_install(d, tmp_path / "inst")
    payload = json.dumps({"tool": "write_file", "args": {"path": (root / "Data" / "Decoy.esp").as_posix()}})
    cp = _run_adapter(root, "toy", payload.encode())
    assert json.loads(cp.stdout)["toy_decision"] == "block", cp.stdout


def test_ask_becomes_a_block_unless_the_author_set_render_ask(tmp_path):
    """The engine cannot be made to emit ask by a conformance case, so the template's _render is unit-tested."""
    c = _conf()
    text = c.compose(TPL.read_text(encoding="ascii"), JSON_SLOTS)
    ns = {"__name__": "adapter_under_test"}
    exec(compile(text, "adapter.py", "exec"), ns)
    out, code, err = ns["_render"]({"decision": "ask", "reason": "delete save?", "context": ""})
    assert json.loads(out)["toy_decision"] == "block" and "NEEDS YOUR CONFIRMATION" in out and "delete save?" in out
    ns["render_ask"] = lambda reason: ("ASKED " + reason, 0, "")
    assert ns["_render"]({"decision": "ask", "reason": "r", "context": ""})[0] == "ASKED r"


JSON_PROFILE = {"block": {"exit": 0, "stdout_regex": "\"toy_decision\": ?\"block\"", "stderr_regex": None},
                "allow": {"exit": 0}}
EXIT2_PROFILE = {"block": {"exit": 2, "stdout_regex": None, "stderr_regex": None}, "allow": {"exit": 0}}


def _json_cases(shell=True):
    cs = [
        {"id": "decoy-plugin-write", "expect": "block", "payload": {"tool": "write_file", "args": {"path": "{ROOT}/Data/Decoy.esp"}}},
        {"id": "benign-write", "expect": "allow", "payload": {"tool": "write_file", "args": {"path": "{ROOT}/notes.txt"}}},
        {"id": "empty-payload", "expect": "block", "payload": ""},
        {"id": "garbage-payload", "expect": "block", "payload": "}{ not json"},
    ]
    if shell:
        cs += [
            {"id": "decoy-plugin-shell", "expect": "block", "payload": {"tool": "run_shell", "args": {"cmd": "echo x > \"{ROOT}/Data/Decoy.esp\""}}},
            {"id": "benign-shell", "expect": "allow", "payload": {"tool": "run_shell", "args": {"cmd": "echo hello"}}},
        ]
    return {"cases": cs}


def _exit2_cases():
    return {"cases": [
        {"id": "decoy-plugin-write", "expect": "block", "payload": {"call": {"name": "edit", "file": "{ROOT}/Data/Decoy.esp"}}},
        {"id": "benign-write", "expect": "allow", "payload": {"call": {"name": "edit", "file": "{ROOT}/notes.txt"}}},
        {"id": "empty-payload", "expect": "block", "payload": ""},
        {"id": "garbage-payload", "expect": "block", "payload_hex": "fffe00"},
    ]}


FAULT_IDS = ["fault:engine-crash", "fault:engine-silent", "fault:engine-garbage", "fault:engine-marker-garbage",
             "fault:engine-wrong-version", "fault:engine-allow-but-exit-1", "fault:engine-hang", "fault:engine-missing"]


def _by(rows):
    return {r.id: r for r in rows}


def test_both_worked_examples_pass_conformance_with_every_row_present(tmp_path):
    c = _conf()
    for n, (slots, prof, cases) in enumerate(((JSON_SLOTS, JSON_PROFILE, _json_cases()),
                                              (EXIT2_SLOTS, EXIT2_PROFILE, _exit2_cases()))):
        d = _toy(tmp_path, slots, name="ex" + str(n))
        rows = c.run_conformance(d, prof, cases)
        bad = [r for r in rows if not r.ok]
        assert not bad, bad
        ids = [r.id for r in rows]
        assert "template-identical" in ids and all(f in ids for f in FAULT_IDS), ids
        assert c.summary(rows).startswith("CONFORMANCE PASS")


def test_the_cli_prints_a_row_per_check_and_exits_by_the_summary(tmp_path):
    d = _toy(tmp_path, JSON_SLOTS)
    (tmp_path / "p.json").write_text(json.dumps(JSON_PROFILE))
    (tmp_path / "c.json").write_text(json.dumps(_json_cases()))

    def run():
        return subprocess.run([sys.executable, str(REPO / "tools" / "adapter_conformance.py"), "run", "--adapter-dir", str(d),
                               "--profile", str(tmp_path / "p.json"), "--cases", str(tmp_path / "c.json")],
                              capture_output=True, timeout=300, text=True)
    cp = run()
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "PASS case:decoy-plugin-write" in cp.stdout and "CONFORMANCE PASS (" in cp.stdout.splitlines()[-1]
    a = d / "adapter.py"
    a.write_text(a.read_text().replace("ADAPTER_TIMEOUT_S = 25", "ADAPTER_TIMEOUT_S = 26"), newline="\n")
    cp = run()
    assert cp.returncode == 1 and "FAIL template-identical" in cp.stdout and "CONFORMANCE FAIL (" in cp.stdout


def test_a_divergent_machinery_fails_the_template_identity_row(tmp_path):
    c = _conf()
    d = _toy(tmp_path, JSON_SLOTS)
    p = d / "adapter.py"
    p.write_text(p.read_text().replace('raise RuntimeError("empty payload")', "pass"), newline="\n")
    assert 'raise RuntimeError("empty payload")' not in p.read_text()
    assert not _by(c.run_conformance(d, JSON_PROFILE, _json_cases()))["template-identical"].ok


def test_an_adapter_with_no_adapter_py_is_a_failed_row_not_a_silent_skip(tmp_path):
    c = _conf()
    d = tmp_path / "empty"
    d.mkdir()
    assert not _by(c.run_conformance(d, JSON_PROFILE, _json_cases()))["template-identical"].ok


def test_a_port_skips_identity_with_a_printed_note_and_is_still_probed(tmp_path, capsys):
    c = _conf()
    d = _toy(tmp_path, JSON_SLOTS, name="portd")
    (d / "adapter.py").rename(d / "other.py")
    rows = c.run_conformance(d, JSON_PROFILE, _json_cases(), command="{PY} {DIR}/other.py", port=True)
    assert "template-identical" not in [r.id for r in rows]
    assert "UNVERIFIED STRUCTURE" in capsys.readouterr().err
    assert all(r.ok for r in rows), [r for r in rows if not r.ok]


def test_profile_and_case_shape_problems_are_failed_rows(tmp_path):
    c = _conf()
    d = _toy(tmp_path, JSON_SLOTS)
    same = {"block": {"exit": 0, "stdout_regex": None, "stderr_regex": None}, "allow": {"exit": 0}}
    assert [r.id for r in c.run_conformance(d, same, _json_cases())] == ["profile"]
    assert not c.run_conformance(d, {"block": {}}, _json_cases())[0].ok
    no_empty = _json_cases()
    no_empty["cases"] = [x for x in no_empty["cases"] if x["id"] != "empty-payload"]
    assert any(r.id == "case:empty-payload" and not r.ok for r in c.run_conformance(d, JSON_PROFILE, no_empty))
    half = _json_cases()
    half["cases"] = [x for x in half["cases"] if x["id"] != "benign-shell"]
    assert any(r.id == "case:shell-pair" and not r.ok for r in c.run_conformance(d, JSON_PROFILE, half))
    flipped = _json_cases()
    flipped["cases"][0]["expect"] = "allow"
    assert any(r.id == "case:decoy-plugin-write" and not r.ok for r in c.run_conformance(d, JSON_PROFILE, flipped))


def test_a_slot_that_allows_everything_is_caught_by_the_decoy_case(tmp_path):
    """The classic bad adapter: map_payload returns a benign request whatever the agent sent."""
    c = _conf()
    evil = JSON_SLOTS.replace(
        MAP_HEAD, 'return {"v": 1, "agent": "toy-json", "event": "pre_tool", "kind": "none"}\n    tool,', 1)
    assert 'kind": "none"' in evil
    d = _toy(tmp_path, evil)
    rows = c.run_conformance(d, JSON_PROFILE, _json_cases())
    assert not _by(rows)["case:decoy-plugin-write"].ok
    assert c.summary(rows).startswith("CONFORMANCE FAIL")


def test_a_slot_that_hard_exits_zero_is_caught(tmp_path):
    """os._exit(0) bypasses every except: only the probe can see it, and it must go red."""
    c = _conf()
    hostile = JSON_SLOTS.replace(MAP_HEAD, "import os\n    os._exit(0)\n    tool,", 1)
    assert "os._exit(0)" in hostile
    d = _toy(tmp_path, hostile)
    rows = _by(c.run_conformance(d, JSON_PROFILE, _json_cases()))
    assert not rows["case:decoy-plugin-write"].ok and not rows["fault:engine-crash"].ok


def test_a_slot_that_raises_or_exits_is_a_block_so_the_fail_closed_rows_pass(tmp_path):
    c = _conf()
    for n, body in enumerate(("raise ValueError('boom')", "sys.exit(0)", "sys.exit(3)", "raise SystemExit")):
        hostile = JSON_SLOTS.replace(MAP_HEAD, body + "\n    tool,", 1)
        assert body in hostile
        d = _toy(tmp_path, hostile, name="h" + str(n))
        rows = _by(c.run_conformance(d, JSON_PROFILE, _json_cases()))
        assert rows["case:garbage-payload"].ok and rows["case:empty-payload"].ok, (body, rows)
        # It refuses everything: closed (never "NOT BLOCKED"), and visibly useless (the decoy's own reason is
        # missing and the benign call is refused), so conformance still goes red.
        assert rows["case:decoy-plugin-write"].why.startswith("blocked, but"), (body, rows["case:decoy-plugin-write"])
        assert not rows["case:benign-write"].ok, body


def _gone(pid):
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"], capture_output=True, text=True).stdout
        return str(pid) not in out
    try:
        os.kill(pid, 0)
    except OSError:
        return True
    return False


def test_a_slot_that_hangs_ends_in_a_failed_row_and_the_child_is_killed(tmp_path):
    c = _conf()
    c.PROBE_TIMEOUT_S = 2
    pidfile = tmp_path / "pid.txt"
    hang = "import os, time\n    open(%r, 'w').write(str(os.getpid()))\n    time.sleep(120)\n    " % pidfile.as_posix()
    d = _toy(tmp_path, JSON_SLOTS.replace(MAP_HEAD, hang + MAP_HEAD, 1))
    t0 = time.time()
    rows = c.run_conformance(d, JSON_PROFILE, _json_cases())
    assert time.time() - t0 < 90
    by = _by(rows)
    assert not by["case:decoy-plugin-write"].ok and "TIMEOUT" in by["case:decoy-plugin-write"].why
    assert not by["faults"].ok
    assert c.summary(rows).startswith("CONFORMANCE FAIL")
    assert pidfile.exists() and _gone(int(pidfile.read_text()))


def test_stderr_crlf_on_windows_does_not_break_an_exit2_block_check():
    c = _conf()
    prof = {"block": {"exit": 2, "stdout_regex": None, "stderr_regex": "GUARD INERT: adapter failed$"}, "allow": {"exit": 0}}
    assert c.is_block(prof, "", 2, "GUARD INERT: adapter failed\r\n")
    assert not c.is_block(prof, "", 2, "something else\n")
    assert not c.is_block(prof, "", 1, "GUARD INERT: adapter failed")


def test_every_fault_row_goes_red_when_the_machinery_fails_open(tmp_path):
    """Mutation: a machinery that answers 'allow' on any failure must turn every fault row (and the garbage and
    empty rows) red, and the identity row too. Without this the fault matrix could be decoration."""
    c = _conf()
    d = _toy(tmp_path, JSON_SLOTS)
    p = d / "adapter.py"
    src = p.read_text()
    old = "        return _checked(render_block(reason))\n    except BaseException:"
    assert old in src
    p.write_text(src.replace(old, '        return _checked(render_allow("x"))\n    except BaseException:'), newline="\n")
    rows = _by(c.run_conformance(d, JSON_PROFILE, _json_cases()))
    for fid in FAULT_IDS:
        assert not rows[fid].ok, fid
    assert not rows["case:garbage-payload"].ok and not rows["case:empty-payload"].ok
    assert not rows["template-identical"].ok
