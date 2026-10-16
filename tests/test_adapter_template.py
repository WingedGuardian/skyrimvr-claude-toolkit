"""The adapter template itself: parses on 3.8, is closed when unfilled, markers are unique."""
from __future__ import annotations

import ast
import subprocess
import sys

import pytest

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


# A profile regex is matched with fullmatch against the WHOLE (stripped) stream, DOTALL: it must describe the
# decision, not mention a word that prose could contain.
JSON_PROFILE = {"block": {"exit": 0, "stdout_regex": "\\{\"toy_decision\": \"block\".*", "stderr_regex": None},
                "allow": {"exit": 0}, "write_tools": ["write_file"], "shell_tools": ["run_shell"]}
EXIT2_PROFILE = {"block": {"exit": 2, "stdout_regex": None, "stderr_regex": None}, "allow": {"exit": 0},
                 "write_tools": ["edit"], "shell_tools": ["sh"]}


def _json_cases(shell=True):
    cs = [
        {"id": "decoy-plugin-write", "tool": "write_file", "expect": "block",
         "payload": {"tool": "write_file", "args": {"path": "{ROOT}/Data/Decoy.esp"}}},
        {"id": "benign-write", "expect": "allow", "payload": {"tool": "write_file", "args": {"path": "{ROOT}/notes.txt"}}},
        {"id": "empty-payload", "expect": "block", "payload": ""},
        {"id": "garbage-payload", "expect": "block", "payload": "}{ not json"},
    ]
    if shell:
        cs += [
            {"id": "decoy-plugin-shell", "tool": "run_shell", "expect": "block",
             "payload": {"tool": "run_shell", "args": {"cmd": "echo x > \"{ROOT}/Data/Decoy.esp\""}}},
            {"id": "benign-shell", "expect": "allow", "payload": {"tool": "run_shell", "args": {"cmd": "echo hello"}}},
        ]
    return {"cases": cs}


def _exit2_cases():
    return {"cases": [
        {"id": "decoy-plugin-write", "tool": "edit", "expect": "block",
         "payload": {"call": {"name": "edit", "file": "{ROOT}/Data/Decoy.esp"}}},
        {"id": "benign-write", "expect": "allow", "payload": {"call": {"name": "edit", "file": "{ROOT}/notes.txt"}}},
        {"id": "empty-payload", "expect": "block", "payload": ""},
        {"id": "garbage-payload", "expect": "block", "payload_hex": "fffe00"},
        {"id": "decoy-plugin-shell", "tool": "sh", "expect": "block",
         "payload": {"call": {"name": "sh", "cmdline": "echo x > \"{ROOT}/Data/Decoy.esp\""}}},
        {"id": "benign-shell", "expect": "allow", "payload": {"call": {"name": "sh", "cmdline": "echo hello"}}},
    ]}


EDIT_DECOY = {"id": "decoy-plugin-write:edit_file", "tool": "edit_file", "expect": "block",
              "payload": {"tool": "edit_file", "args": {"path": "{ROOT}/Data/Decoy.esp"}}}


def test_every_listed_write_tool_needs_its_own_decoy_case(tmp_path):
    """Final review I2: the required cases covered ONE write tool, so an agent with write_file AND edit_file that
    copied the example passed while edit_file went unjudged (mapped to `other`)."""
    c = _conf()
    d = _toy(tmp_path, JSON_SLOTS)
    prof = dict(JSON_PROFILE, write_tools=["write_file", "edit_file"])
    rows = c.run_conformance(d, prof, _json_cases())
    assert any(r.id == "case:decoy-plugin-write:edit_file" and not r.ok and "no decoy" in r.why for r in rows), rows
    prof = dict(JSON_PROFILE, shell_tools=["run_shell", "exec_command"])
    rows = c.run_conformance(d, prof, _json_cases())
    assert any(r.id == "case:decoy-plugin-shell:exec_command" and not r.ok for r in rows), rows


def test_a_listed_write_tool_the_slots_leave_unmapped_goes_red(tmp_path):
    """The reviewer's own input: an edit_file decoy through the shipped example came back allow ('NOT JUDGED')."""
    c = _conf()
    d = _toy(tmp_path, JSON_SLOTS)
    prof = dict(JSON_PROFILE, write_tools=["write_file", "edit_file"])
    cases = _json_cases()
    cases["cases"].append(EDIT_DECOY)
    by = _by(c.run_conformance(d, prof, cases))
    assert not by["case:decoy-plugin-write:edit_file"].ok and "NOT BLOCKED" in by["case:decoy-plugin-write:edit_file"].why
    mapped = JSON_SLOTS.replace('WRITE_TOOLS = ("write_file",)', 'WRITE_TOOLS = ("write_file", "edit_file")')
    assert mapped != JSON_SLOTS
    d2 = _toy(tmp_path, mapped, name="mapped")
    rows = c.run_conformance(d2, prof, cases)
    assert all(r.ok for r in rows), [r for r in rows if not r.ok]


@pytest.mark.parametrize("bad,why", [
    ({"write_tools": []}, "write_tools"),
    ({"write_tools": "write_file"}, "write_tools"),
    ({"write_tools": ["write_file", "write_file"]}, "write_tools"),
    ({"shell_tools": [3]}, "shell_tools"),
    ({"shell_tools": ["write_file"]}, "both"),
])
def test_the_profile_tool_lists_are_validated(tmp_path, bad, why):
    c = _conf()
    d = _toy(tmp_path, JSON_SLOTS)
    rows = c.run_conformance(d, dict(JSON_PROFILE, **bad), _json_cases())
    assert [r.id for r in rows] == ["profile"] and why in rows[0].why, rows
    missing = {k: v for k, v in JSON_PROFILE.items() if k != "write_tools"}
    assert [r.id for r in c.run_conformance(d, missing, _json_cases())] == ["profile"]


def test_a_decoy_case_must_name_a_listed_tool_and_expect_block(tmp_path):
    c = _conf()
    d = _toy(tmp_path, JSON_SLOTS)
    cases = _json_cases()
    del cases["cases"][0]["tool"]
    assert any(r.id == "case:decoy-plugin-write" and not r.ok and "tool" in r.why for r in c.run_conformance(d, JSON_PROFILE, cases))
    cases = _json_cases()
    cases["cases"].append(dict(EDIT_DECOY, expect="allow"))
    prof = dict(JSON_PROFILE, write_tools=["write_file", "edit_file"])
    assert any(r.id == "case:decoy-plugin-write:edit_file" and not r.ok and "block" in r.why
               for r in c.run_conformance(d, prof, cases))
    cases = _json_cases()
    cases["cases"].append(EDIT_DECOY)                  # edit_file is not in the profile's lists
    assert any(r.id == "case:decoy-plugin-write:edit_file" and not r.ok and "not listed" in r.why
               for r in c.run_conformance(d, JSON_PROFILE, cases))


def test_the_cli_says_out_loud_that_unlisted_tools_are_not_guarded(tmp_path):
    d = _toy(tmp_path, JSON_SLOTS)
    (tmp_path / "p.json").write_text(json.dumps(JSON_PROFILE))
    (tmp_path / "c.json").write_text(json.dumps(_json_cases()))
    cp = subprocess.run([sys.executable, str(REPO / "tools" / "adapter_conformance.py"), "run", "--adapter-dir", str(d),
                         "--profile", str(tmp_path / "p.json"), "--cases", str(tmp_path / "c.json")],
                        capture_output=True, timeout=300, text=True)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    note = [l for l in cp.stdout.splitlines() if l.startswith("UNMAPPED TOOLS ARE NOT GUARDED")]
    assert note and "write_file" in note[0] and "run_shell" in note[0]


FAULT_IDS = ["fault:" + name for name in (*_conf().VERDICT_FAULTS, *_conf().FAULTS)]


@pytest.mark.parametrize("name,field", [("engine-wrong-version", "v"), ("engine-unknown-decision", "decision"),
                                      ("engine-no-marker", None)])
def test_protocol_fault_fixture_has_only_its_named_defect(name, field):
    source = ast.parse(_conf().FAULTS[name])
    writes = [node for node in ast.walk(source) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "w"]
    assert len(writes) == 1
    payload = ast.literal_eval(writes[0].args[0])
    marker = b"SKYGUARD-OK\n"
    if field is None:
        assert not payload.startswith(marker)
    else:
        assert payload.startswith(marker)
        payload = payload[len(marker):]
    verdict = json.loads(payload)
    valid = dict(v=1, decision="allow", reason="", context="", status="evaluated", code="allow")
    assert verdict.keys() == valid.keys()
    for key, value in valid.items():
        if key != field:
            assert type(verdict[key]) is type(value) and verdict[key] == value
    if field is not None:
        assert verdict[field] != valid[field]


@pytest.mark.parametrize("change", [{"v": True}, {"v": 1.0}, {"reason": []}, {"context": {}},
                                   {"status": []}, {"status": "unknown"}, {"code": None}, None])
def test_incomplete_or_wrongly_typed_engine_verdict_is_a_visible_refusal(tmp_path, change):
    c = _conf()
    root = c.make_install(_toy(tmp_path, JSON_SLOTS), tmp_path / "inst")
    verdict = dict(v=1, decision="allow", reason="", context="", status="evaluated", code="allow")
    if change is None:
        verdict = {"v": 1, "decision": "allow"}
    else:
        verdict.update(change)
    data = ("SKYGUARD-OK\n" + json.dumps(verdict) + "\n").encode("utf-8")
    (root / "tools/skyguard/run.py").write_text("import sys\nsys.stdout.buffer.write(" + repr(data) + ")\n")
    result = _run_adapter(root, "toy", b'{"tool":"write_file","args":{"path":"notes.txt"}}')
    out = json.loads(result.stdout)
    assert out["toy_decision"] == "block" and "GUARD INERT" in result.stdout.decode()


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
    rows = c.run_conformance(d, JSON_PROFILE, _json_cases(), command=["{PY}", "{DIR}/other.py"], port=True)
    assert "template-identical" not in [r.id for r in rows]
    assert "UNVERIFIED STRUCTURE" in capsys.readouterr().err
    assert all(r.ok for r in rows), [r for r in rows if not r.ok]


@pytest.mark.parametrize("command", ["{PY} {DIR}/other.py", ["{PY}", 3], [], "not json ["])
def test_a_command_must_be_an_argv_list_never_a_string_to_split(tmp_path, command):
    """Final review I4: a command STRING was split with shlex, so a space in a path became two argv words."""
    c = _conf()
    d = _toy(tmp_path, JSON_SLOTS, name="portd")
    rows = c.run_conformance(d, JSON_PROFILE, _json_cases(), command=command, port=True)
    assert [r.id for r in rows] == ["command"] and not rows[0].ok and "list" in rows[0].why


@pytest.mark.parametrize("dirname", ["has space", "o'neil has space"])
def test_a_temp_or_interpreter_path_with_a_space_or_apostrophe_still_conforms(tmp_path, monkeypatch, dirname):
    """Final review I4: with TMP under 'C:/Users/John Smith' every fault row printed FAILED OPEN (a false red), and a
    POSIX apostrophe made shlex raise. The argv is built as a list, so neither can split a path."""
    import tempfile
    c = _conf()
    spaced = tmp_path / dirname
    spaced.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(spaced))
    d = _toy(spaced, JSON_SLOTS, name="toy dir")
    rows = c.run_conformance(d, JSON_PROFILE, _json_cases())
    assert all(r.ok for r in rows), [r for r in rows if not r.ok]
    (d / "adapter.py").rename(d / "other.py")
    rows = c.run_conformance(d, JSON_PROFILE, _json_cases(), command=["{PY}", "{DIR}/other.py"], port=True)
    assert all(r.ok for r in rows), [r for r in rows if not r.ok]


def test_duplicate_case_ids_are_a_failed_row_not_a_silent_shadow(tmp_path):
    """Final review M8: the case dict kept the LAST of two same-id cases, so a failing case could be hidden."""
    c = _conf()
    d = _toy(tmp_path, JSON_SLOTS)
    cases = _json_cases()
    cases["cases"].insert(0, {"id": "benign-write", "expect": "allow",
                              "payload": {"tool": "write_file", "args": {"path": "{ROOT}/Data/Decoy.esp"}}})
    rows = c.run_conformance(d, JSON_PROFILE, cases)
    assert any(r.id == "case:benign-write" and not r.ok and "duplicate" in r.why for r in rows), rows


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
    assert not _by(rows)["case:benign-write"].ok      # the engine refuses a request of no kind; the adapter relays it
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
        assert rows["case:decoy-plugin-write"].ok, body          # closed: it blocks the decoy ...
        # ... but it blocks everything: the benign call is refused and the verdict-fidelity faults go red.
        assert not rows["case:benign-write"].ok and not rows["fault:engine-allow-valid"].ok, body
        assert not rows["fault:engine-deny-nonce"].ok, body      # the engine's reason never reached the agent


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
    # The slot hangs AND leaves a grandchild (an "engine") holding the inherited pipes: killing only the adapter
    # would orphan it and, on Windows, block the pipe read for as long as the grandchild lives.
    hang = ("import os, time, subprocess\n    g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            "    open(%r, 'a').write(str(os.getpid()) + ' ' + str(g.pid) + '\\n')\n    time.sleep(120)\n    " % pidfile.as_posix())
    d = _toy(tmp_path, JSON_SLOTS.replace(MAP_HEAD, hang + MAP_HEAD, 1))
    t0 = time.time()
    try:
        rows = c.run_conformance(d, JSON_PROFILE, _json_cases())
        assert time.time() - t0 < 60
        by = _by(rows)
        assert not by["case:decoy-plugin-write"].ok and "TIMEOUT" in by["case:decoy-plugin-write"].why
        assert not by["faults"].ok
        assert c.summary(rows).startswith("CONFORMANCE FAIL")
        pids = [int(x) for x in pidfile.read_text().split()]
        assert len(pids) >= 2 and all(_gone(p) for p in pids), pids      # adapter AND its grandchild
    finally:                                   # only PIDs this test's own slot spawned
        if pidfile.exists() and sys.platform == "win32":
            for p in pidfile.read_text().split():
                subprocess.run(["taskkill", "/T", "/F", "/PID", p], capture_output=True)


def test_stderr_crlf_on_windows_does_not_break_an_exit2_block_check():
    c = _conf()
    prof = {"block": {"exit": 2, "stdout_regex": None, "stderr_regex": "GUARD INERT: adapter failed"}, "allow": {"exit": 0}}
    assert c.is_block(prof, "", 2, "GUARD INERT: adapter failed\r\n")
    assert not c.is_block(prof, "", 2, "something else\n")
    assert not c.is_block(prof, "", 1, "GUARD INERT: adapter failed")


def test_a_profile_regex_must_match_the_whole_stream_so_prose_cannot_pose_as_a_block():
    c = _conf()
    assert c.is_block(JSON_PROFILE, '{"toy_decision": "block", "reason": "r"}\n', 0, "")
    assert not c.is_block(JSON_PROFILE, '{"toy_decision": "allow", "context": "the word block"}', 0, "")
    assert not c.is_block(JSON_PROFILE, 'junk {"toy_decision": "block", "reason": "r"}', 0, "")
    loose = {"block": {"exit": 0, "stdout_regex": "block", "stderr_regex": None}, "allow": {"exit": 0}}
    assert not c.is_block(loose, '{"toy_decision": "allow", "context": "block"}', 0, "")


def test_command_without_port_is_refused_so_the_hashed_file_is_the_one_that_runs(tmp_path):
    c = _conf()
    live = c.compose(TPL.read_text(encoding="ascii"), EXIT2_SLOTS).replace(
        "out, code, err = _render(v)",
        "out, code, err = (('', 2, 'plugin refused') if b'.esp' in raw else ('', 0, ''))")
    assert "b'.esp'" in live
    d = _toy(tmp_path, EXIT2_SLOTS)
    (d / "evil.py").write_text(live, newline="\n")
    rows = c.run_conformance(d, EXIT2_PROFILE, _exit2_cases(), command=["{PY}", "{DIR}/evil.py"])
    assert [r.id for r in rows] == ["command"] and not rows[0].ok


def test_a_port_that_checks_liveness_but_ignores_the_verdict_goes_red(tmp_path):
    """Reviewer's cheat A: block on '.esp' in the raw payload, never read the engine's decision."""
    c = _conf()
    live = c.compose(TPL.read_text(encoding="ascii"), EXIT2_SLOTS).replace(
        "out, code, err = _render(v)",
        "out, code, err = (('', 2, 'plugin refused') if b'.esp' in raw else ('', 0, ''))")
    d = _toy(tmp_path, EXIT2_SLOTS)
    (d / "adapter.py").write_text(live, newline="\n")
    rows = _by(c.run_conformance(d, EXIT2_PROFILE, _exit2_cases(), port=True))
    assert not rows["fault:engine-allow-valid"].ok and not rows["fault:engine-deny-nonce"].ok


def test_a_slot_that_maps_everything_to_other_and_decides_for_itself_goes_red(tmp_path):
    """Reviewer's cheat F: the slot never asks the engine anything real; render_allow blocks on '.esp'."""
    c = _conf()
    f = JSON_SLOTS.replace("def map_payload(raw):\n", "def map_payload(raw):\n    global RAW; RAW = raw; json.loads(raw.decode('utf-8'))\n"
                           "    return {\"v\": 1, \"agent\": \"toy-json\", \"event\": \"pre_tool\", \"cwd\": \"\", \"kind\": \"other\", \"tool_name\": \"x\"}\n", 1)
    f = f.replace("def render_allow(context):\n", "def render_allow(context):\n    if b'.esp' in RAW: return render_block('plugin refused')\n", 1)
    assert "RAW" in f
    d = _toy(tmp_path, f)
    rows = _by(c.run_conformance(d, JSON_PROFILE, _json_cases()))
    assert rows["case:decoy-plugin-write"].ok          # the old checks all pass this cheat ...
    assert not rows["fault:engine-allow-valid"].ok     # ... the verdict-fidelity fault does not


def test_a_render_block_that_drops_the_engine_reason_goes_red(tmp_path):
    c = _conf()
    b = EXIT2_SLOTS.replace('return ("", 2, reason)', 'return ("", 2, "GUARD INERT: refused a plugin/archive op")')
    assert b != EXIT2_SLOTS
    d = _toy(tmp_path, b)
    rows = _by(c.run_conformance(d, EXIT2_PROFILE, _exit2_cases()))
    assert not rows["fault:engine-deny-nonce"].ok and "nonce" in rows["fault:engine-deny-nonce"].why.lower()


def test_the_shell_pair_is_mandatory_unless_the_profile_lists_no_shell_tool(tmp_path):
    c = _conf()
    d = _toy(tmp_path, JSON_SLOTS)
    no_shell = _json_cases(shell=False)
    rows = c.run_conformance(d, JSON_PROFILE, no_shell)
    assert {"case:decoy-plugin-shell", "case:benign-shell"} <= {r.id for r in rows if not r.ok}
    prof = dict(JSON_PROFILE, shell_tools=[])
    rows = c.run_conformance(d, prof, no_shell)
    assert all(r.ok for r in rows), [r for r in rows if not r.ok]
    assert "SHELL UNGUARDED (profile lists no shell tool)" in c.summary(rows)
    assert "SHELL UNGUARDED" not in c.summary(c.run_conformance(d, JSON_PROFILE, _json_cases()))
    assert [r.id for r in c.run_conformance(d, dict(JSON_PROFILE, shell_tools="no"), no_shell)] == ["profile"]


def test_a_non_cp1252_reason_on_a_piped_windows_stdout_still_blocks(tmp_path):
    """Reviewer's E: a text-stdout block shape with a reason the console codepage cannot encode used to crash
    in _emit (outside the try) and exit 1, which a fail-open agent reads as allow."""
    c = _conf()
    e = EXIT2_SLOTS.replace('return ("", 2, reason)', 'return (reason + " \\u65e5\\u672c", 2, "")')
    assert "u65e5" in e
    d = _toy(tmp_path, e, name="textout")
    root = c.make_install(d, tmp_path / "inst")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    p = json.dumps({"call": {"name": "edit", "file": (root / "Data" / "x.esp").as_posix()}}).encode()
    cp = subprocess.run([sys.executable, str(root / "tools" / "adapters" / "textout" / "adapter.py")],
                        input=p, capture_output=True, env=env, timeout=60)
    assert cp.returncode == 2, (cp.returncode, cp.stdout, cp.stderr[-300:])
    assert "日本" in cp.stdout.decode("utf-8")


def test_emit_falls_back_to_the_static_block_when_the_streams_are_broken(tmp_path, monkeypatch):
    c = _conf()
    ns = {"__name__": "adapter_under_test"}
    exec(compile(c.compose(TPL.read_text(encoding="ascii"), EXIT2_SLOTS), "adapter.py", "exec"), ns)

    class Broken:
        class buffer:
            @staticmethod
            def write(b):
                raise OSError("stream is broken")
            flush = write
    wrote = []
    monkeypatch.setattr(sys, "stdout", Broken)
    monkeypatch.setattr(sys, "stderr", Broken)
    monkeypatch.setattr(os, "write", lambda fd, data: wrote.append((fd, data)) or len(data))
    with pytest.raises(SystemExit) as ex:
        ns["_emit"]("allow text", 0, "")
    assert ex.value.code == 2 and (2, b"GUARD INERT: adapter failed") in wrote


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
    for fid in FAULT_IDS[2:]:                  # engine-break faults; the first two check verdict fidelity
        assert not rows[fid].ok, fid
    assert not rows["case:garbage-payload"].ok and not rows["case:empty-payload"].ok
    assert not rows["template-identical"].ok
