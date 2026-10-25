"""The Codex entry wrappers -- the fail-closed boundary (Plan 4, Task 7).

Codex blocks a tool call on exactly ONE thing: a parsed JSON permissionDecision "deny", exit 0. An ask, a
non-zero exit, a crash, bad JSON, a timeout and a missing script all let the command RUN (MEASURED,
codex-cli 0.160.0). So every path out of `skyguard-entry.ps1` / `skyguard-entry.sh` must be the engine's
own validated answer or a deny (pre-tool) / a NOT LIVE line (session-start), with exit 0.

Every row of the fault matrix below replaces the installed `run.py` with a broken one and asserts the
EXACT failure text -- not merely "not allow" -- so a check that denies for the wrong reason is visible
(Review Focus 1). The install root contains a space and `(x86)` on purpose.

The PowerShell entry runs under `pwsh` and Windows PowerShell 5.1 on Windows (the only place Plan 4 claims
it; R2). The POSIX entry runs under the Git Bash / system bash the rest of the suite uses.
"""

from __future__ import annotations

import json
import re
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import BASH, REPO, BS, python_free_path

sys.path.insert(0, str(REPO / "tools"))
from skyguard import health  # noqa: E402

SRC = REPO / "agent" / "guards" / "adapters" / "codex"
FIX = REPO / "tests" / "fixtures" / "payloads" / "codex" / "0.160.0"
REAL_PY = sys.executable.replace(BS, "/")
ON_WINDOWS = sys.platform == "win32"


def _ps_exe(name: str):
    return shutil.which(name) if ON_WINDOWS else None


ENTRIES = [
    pytest.param("pwsh", marks=pytest.mark.skipif(not _ps_exe("pwsh"), reason="pwsh not installed (Windows only)")),
    pytest.param("powershell", marks=pytest.mark.skipif(not _ps_exe("powershell"),
                                                        reason="Windows PowerShell 5.1 not present (Windows only)")),
    pytest.param("bash"),
]
ENTRY_NAMES = ("pwsh", "powershell", "bash")

DENY = ('{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny",'
        '"permissionDecisionReason":"GUARD INERT: the Codex hook could not get a verdict from the toolkit engine '
        '(%s). NOTHING was checked: this is a refusal, not a decision. Tell the user to run: '
        'bash tools/hook-canary.sh"}}')
NOT_LIVE = ("SKYGUARD: NOT LIVE -- the Codex session hook could not get a result from the toolkit engine (%s). "
            "The toolkit guards may not be running: tell the user, and ask them to run: bash tools/hook-canary.sh")
ROLES = ("pre-tool", "session-start")


# --------------------------------------------------------------------------------------------------
# the fixture install
# --------------------------------------------------------------------------------------------------
def _install(tmp_path: Path, py: str = REAL_PY) -> Path:
    root = tmp_path / "Program Files (x86)" / "Skyrim VR"
    hooks = root / ".codex" / "hooks"
    hooks.mkdir(parents=True)
    for name in ("skyguard-entry.ps1", "skyguard-entry.sh"):
        text = (SRC / name).read_text(encoding="ascii")
        text = text.replace("{{PYTHON_PATH}}", py).replace("{{BASH_PATH}}", BASH.replace(BS, "/"))
        (hooks / name).write_text(text, encoding="ascii", newline="\n")
    shutil.copytree(REPO / "tools" / "skyguard", root / "tools" / "skyguard",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(REPO / "tools/modding_harness", root / "tools/modding_harness",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(REPO / "tools" / "kb-guard.sh", root / "tools" / "kb-guard.sh")
    return root


def _cmd(entry: str, root: Path, role: str) -> list:
    hooks = root / ".codex" / "hooks"
    if entry == "bash":
        return [BASH, str(hooks / "skyguard-entry.sh"), role]
    return [_ps_exe(entry), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(hooks / "skyguard-entry.ps1"), role]


def _run(entry: str, root: Path, role: str, data: bytes, env: dict | None = None,
         timeout: float = 90) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    e.pop("SKYGUARD_WRAPPER_TIMEOUT_S", None)
    e.pop("SKYGUARD_NO_PYTHON_FALLBACK", None)
    e.update(env or {})
    return subprocess.run(_cmd(entry, root, role), input=data, capture_output=True, env=e, timeout=timeout)


def _payload(name: str, root: Path, **changes) -> bytes:
    d = json.loads((FIX / name).read_text(encoding="utf-8"))
    d["cwd"] = root.as_posix()
    if "command" in changes:
        d["tool_input"]["command"] = changes["command"]
    return json.dumps(d).encode("utf-8")


def _benign(root: Path) -> bytes:
    return _payload("codex-PreToolUse-Bash-1.json", root, command="Get-Date")


def _session(root: Path) -> bytes:
    return _payload("codex-SessionStart-none-1.json", root)


def _plugin_write(root: Path) -> bytes:
    return _payload("codex-PreToolUse-apply_patch-1.json", root,
                    command="*** Begin Patch\n*** Add File: Data/Decoy.esp\n+one\n*** End Patch")


def _hb(root: Path) -> Path:
    return root / ".claude" / "backups" / ".hook-heartbeat" / "codex"


def _rec(root: Path, role: str, stage: str) -> str:
    p = _hb(root) / f"{role}.{stage}"
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def _replace_run_py(root: Path, source: str) -> None:
    (root / "tools" / "skyguard" / "run.py").write_text(source, encoding="utf-8", newline="\n")


def _prints(data: bytes) -> str:
    return "import sys\nsys.stdout.buffer.write({!r})\nsys.stdout.buffer.flush()\n".format(data)


BSB = BS.encode("ascii")   # one backslash byte

_GOOD_DENY = (b'{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", '
              b'"permissionDecisionReason": "r"}}')

# row -> (how to break the install, {role: the cause the wrapper must name})
#   kind "run.py": replace run.py with this source; "env": extra environment; "stdin": the payload bytes
BANNER_CAUSE = "engine session text lacks the SKYGUARD banner"
MATRIX = {
    "exit3": ({"run.py": "raise SystemExit(3)\n"},
              {"pre-tool": "engine exited 3", "session-start": "engine exited 3"}),
    "hi": ({"run.py": 'print("hi")\n'},
           {"pre-tool": "engine output lacks the SKYGUARD-OK line",
            "session-start": "engine output lacks the SKYGUARD-OK line"}),
    "two-json": ({"run.py": _prints(b"SKYGUARD-OK\n" + _GOOD_DENY + b"\n" + _GOOD_DENY + b"\n")},
                 {"pre-tool": "engine printed more than one body line", "session-start": BANNER_CAUSE}),
    "ask": ({"run.py": _prints(b'SKYGUARD-OK\n{"hookSpecificOutput":{"permissionDecision":"ask"}}')},
            {"pre-tool": "engine answered a permissionDecision other than deny", "session-start": BANNER_CAUSE}),
    "allow": ({"run.py": _prints(b'SKYGUARD-OK\n{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
                                 b'"permissionDecision":"allow"}}')},
              {"pre-tool": "engine answered a permissionDecision other than deny", "session-start": BANNER_CAUSE}),
    "x1": ({"run.py": _prints(b'SKYGUARD-OK\n{"x":1}')},
           {"pre-tool": "engine output is not a verdict JSON", "session-start": BANNER_CAUSE}),
    # JSON that PowerShell's lenient parsers accept and Codex's strict one would reject (= fail OPEN)
    "single-quotes": ({"run.py": _prints(b"SKYGUARD-OK\n{'hookSpecificOutput':{'hookEventName':'PreToolUse',"
                                         b"'permissionDecision':'deny'}}")},
                      {"pre-tool": "engine output is not a verdict JSON", "session-start": BANNER_CAUSE}),
    "dup-key": ({"run.py": _prints(b'SKYGUARD-OK\n{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
                                   b'"permissionDecision":"deny","permissionDecision":"deny"}}')},
                {"pre-tool": "engine output repeats a key", "session-start": BANNER_CAUSE}),
    # a raw control character inside a string: invalid JSON for Codex, so it must not pass
    "raw-tab": ({"run.py": _prints(b'SKYGUARD-OK\n{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
                                   b'"additionalContext":"a\tb"}}')},
                {"pre-tool": "engine output is not a verdict JSON", "session-start": BANNER_CAUSE}),
    "bom": ({"run.py": _prints(b"\xef\xbb\xbfSKYGUARD-OK\n" + _GOOD_DENY)},
            {"pre-tool": "engine output lacks the SKYGUARD-OK line",
             "session-start": "engine output lacks the SKYGUARD-OK line"}),
    "hang": ({"run.py": "import time\ntime.sleep(15)\n", "env": {"SKYGUARD_WRAPPER_TIMEOUT_S": "2"}},
             {"pre-tool": "engine did not answer within 2 s", "session-start": "engine did not answer within 2 s"}),
    "deleted": ({"delete": True},
                {"pre-tool": "engine missing (tools/skyguard/run.py)",
                 "session-start": "engine missing (tools/skyguard/run.py)"}),
    "no-python": ({"py": "{{PYTHON_PATH}}", "env": {"SKYGUARD_NO_PYTHON_FALLBACK": "1"}},
                  {"pre-tool": "cannot run Python -- install Python 3 and run setup.sh",
                   "session-start": "cannot run Python -- install Python 3 and run setup.sh"}),
    # a body without hookEventName: Codex cannot tell which event it answers (Task 8 item 0)
    "no-event": ({"run.py": _prints(b'SKYGUARD-OK\n{"hookSpecificOutput":{"permissionDecision":"deny",'
                                    b'"permissionDecisionReason":"r"}}')},
                 {"pre-tool": "engine output names no event", "session-start": BANNER_CAUSE}),
    # a lone surrogate escape: PowerShell accepts it, Codex's parser rejects the body (= fail OPEN)
    "surrogate": ({"run.py": _prints(b'SKYGUARD-OK\n{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
                                     b'"permissionDecision":"deny","permissionDecisionReason":"a' + BSB + b'ud83d"}}')},
                  {"pre-tool": "engine output has a surrogate escape", "session-start": BANNER_CAUSE}),
    # three backslashes = an escaped backslash, then a surrogate escape: still a surrogate
    "surrogate-after-escape": ({"run.py": _prints(b'SKYGUARD-OK\n{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
                                                  b'"permissionDecision":"deny","permissionDecisionReason":"a'
                                                  + BSB * 3 + b'ud83d"}}')},
                               {"pre-tool": "engine output has a surrogate escape", "session-start": BANNER_CAUSE}),
    # Task 0 MEASURED: the `||` in the definition reruns the entry under 5.1 with an EMPTY stdin
    "empty-stdin": ({"stdin": b""},
                    {"pre-tool": "NO PAYLOAD on stdin", "session-start": "NO PAYLOAD on stdin"}),
}


def _expected(role: str, cause: str) -> bytes:
    return ((DENY if role == "pre-tool" else NOT_LIVE) % cause + "\n").encode("ascii")


def _fire_row(tmp_path: Path, entry: str, role: str, row: str) -> subprocess.CompletedProcess:
    how, _ = MATRIX[row]
    root = _install(tmp_path, how.get("py", REAL_PY))
    if "run.py" in how:
        _replace_run_py(root, how["run.py"])
    if how.get("delete"):
        (root / "tools" / "skyguard" / "run.py").unlink()
    data = how["stdin"] if "stdin" in how else (_benign(root) if role == "pre-tool" else _session(root))
    r = _run(entry, root, role, data, how.get("env"))
    r.root = root
    return r


# --------------------------------------------------------------------------------------------------
# the fault matrix and the controls: every path exits 0 with the engine's verdict or a deny
# --------------------------------------------------------------------------------------------------
CASES = ([pytest.param(e, role, row, id=f"{e}-{role}-{row}", marks=p.marks)
          for p, e in zip(ENTRIES, ENTRY_NAMES) for role in ROLES for row in MATRIX]
         + [pytest.param(e, "control", c, id=f"{e}-control-{c}", marks=p.marks)
            for p, e in zip(ENTRIES, ENTRY_NAMES) for c in ("benign", "plugin-write", "session")])


@pytest.mark.parametrize("entry,role,row", CASES)
def test_every_path_exits_zero(tmp_path, entry, role, row):
    if role != "control":
        _assert_failure_row(tmp_path, entry, role, row)
    else:
        _assert_control_row(tmp_path, entry, row)


def _assert_failure_row(tmp_path, entry, role, row):
    r = _fire_row(tmp_path, entry, role, row)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert r.stdout == _expected(role, MATRIX[row][1][role]), r.stdout
    if role == "pre-tool":
        body = json.loads(r.stdout)["hookSpecificOutput"]
        assert body["permissionDecision"] == "deny"
    failed = _rec(r.root, role, "failed")
    assert MATRIX[row][1][role] in failed, failed
    assert not _rec(r.root, role, "emitted"), "a failure was recorded as emitted"


def _assert_control_row(tmp_path, entry, row):
    root = _install(tmp_path)
    if row == "benign":
        r = _run(entry, root, "pre-tool", _benign(root))
        assert r.returncode == 0 and r.stdout == b"", (r.stdout, r.stderr)
    elif row == "plugin-write":
        r = _run(entry, root, "pre-tool", _plugin_write(root))
        assert r.returncode == 0
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny", r.stdout
    else:
        r = _run(entry, root, "session-start", _session(root))
        assert r.returncode == 0 and r.stdout.startswith(b"SKYGUARD: Codex hooks live"), r.stdout
    role = "session-start" if row == "session" else "pre-tool"
    inv = _rec(root, role, "invoked").split()[1]
    assert _rec(root, role, "emitted").split()[1] == inv
    assert _rec(root, role, "evaluated").split()[1] == inv, "the engine's record names another call"
    assert _rec(root, role, "latency").split()[0] == inv


@pytest.mark.parametrize("entry", ENTRIES)
def test_plugin_write_is_denied(tmp_path, entry):
    """Through the real engine: the RULE's text comes back, not a wrapper refusal."""
    root = _install(tmp_path)
    r = _run(entry, root, "pre-tool", _plugin_write(root))
    assert r.returncode == 0, r.stderr
    body = json.loads(r.stdout)["hookSpecificOutput"]
    assert body["permissionDecision"] == "deny"
    assert body["permissionDecisionReason"].startswith(
        "Blocked: plugin and archive files (.esp .esm .esl .bsa .ba2) cannot be written directly."), body
    assert "GUARD INERT" not in body["permissionDecisionReason"]


@pytest.mark.parametrize("entry", ENTRIES)
def test_engine_crash_is_a_deny(tmp_path, entry):
    r = _fire_row(tmp_path, entry, "pre-tool", "exit3")
    assert r.returncode == 0
    assert r.stdout == _expected("pre-tool", "engine exited 3"), r.stdout


@pytest.mark.parametrize("entry", ENTRIES)
def test_a_hung_engine_is_a_deny(tmp_path, entry):
    t = time.time()
    r = _fire_row(tmp_path, entry, "pre-tool", "hang")
    took = time.time() - t
    assert r.returncode == 0
    assert r.stdout == _expected("pre-tool", "engine did not answer within 2 s"), r.stdout
    assert took < 10, f"the wrapper's deadline did not end the call ({took:.1f} s)"


@pytest.mark.parametrize("entry", [p for p in ENTRIES if p.values[0] != "bash"])
def test_the_deadline_clock_starts_when_powershell_starts_not_when_the_script_does(tmp_path, entry):
    # Codex's timeout runs from the spawn. Under load Windows PowerShell 5.1 spent seconds starting before the
    # script's first line, and a clock taken there let the call end past definition timeout - 10 s (T14b,
    # 2026-10-14: 51.3 s against 50). A delay inside the same process stands in for that start-up time.
    root = _install(tmp_path)
    script = root / ".codex" / "hooks" / "skyguard-entry.ps1"
    e = dict(os.environ)
    e.pop("SKYGUARD_WRAPPER_TIMEOUT_S", None)
    spawned = time.time()
    subprocess.run([_ps_exe(entry), "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass", "-Command", f"Start-Sleep -Seconds 8; & '{script}' pre-tool"],
                   input=_benign(root), capture_output=True, env=e, timeout=90)
    t0 = float(_rec(root, "pre-tool", "invoked").split()[0])
    assert t0 - spawned < 4, f"the clock started {t0 - spawned:.1f} s after the spawn"


def _alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


@pytest.mark.skipif(not ON_WINDOWS, reason="the process-tree kill is a Windows one (taskkill /T, by pid)")
@pytest.mark.parametrize("entry", ENTRIES)
def test_a_hung_engine_is_killed_with_its_children(tmp_path, entry):
    root = _install(tmp_path)
    pidfile = tmp_path / "child.pid"
    _replace_run_py(root, (
        "import subprocess, sys, time\n"
        # 40 s: longer than any wait the entry itself may do, so only a KILL can end them in time
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(40)'])\n"
        "open({!r}, 'w').write(str(c.pid))\n"
        "time.sleep(40)\n").format(str(pidfile)))
    r = _run(entry, root, "pre-tool", _benign(root), {"SKYGUARD_WRAPPER_TIMEOUT_S": "3"})
    assert r.stdout == _expected("pre-tool", "engine did not answer within 3 s"), r.stdout
    pid = int(pidfile.read_text())
    deadline = time.time() + 3
    while _alive(pid) and time.time() < deadline:
        time.sleep(0.2)
    assert not _alive(pid), "the engine's child outlived the wrapper's deadline"


@pytest.mark.parametrize("entry", ENTRIES)
def test_a_wrapper_exception_is_a_deny(tmp_path, entry):
    root = _install(tmp_path)
    r = _run(entry, root, "pre-tool", _benign(root), {"SKYGUARD_WRAPPER_TIMEOUT_S": "abc"})
    cause = ("SKYGUARD_WRAPPER_TIMEOUT_S is not a whole number" if entry == "bash"
             else "wrapper error: MethodInvocationException")
    assert r.returncode == 0 and r.stdout == _expected("pre-tool", cause), r.stdout


@pytest.mark.parametrize("entry", ENTRIES)
def test_an_unknown_role_is_a_deny(tmp_path, entry):
    root = _install(tmp_path)
    r = _run(entry, root, "post-tool", _benign(root))
    assert r.returncode == 0 and r.stdout == _expected("pre-tool", "unknown role"), r.stdout
    assert not _hb(root).exists() or not any(_hb(root).iterdir()), "an unknown role wrote telemetry"


@pytest.mark.skipif(not ON_WINDOWS, reason="a .cmd interpreter shim is a Windows PATH entry")
@pytest.mark.parametrize("entry", [p for p in ENTRIES if p.values[0] != "bash"])
def test_a_slow_but_healthy_fallback_interpreter_is_accepted(tmp_path, entry):
    """The PowerShell chain cut every probe at 3 s and stopped probing 8 s after the entry started. A HEALTHY
    selftest was MEASURED at up to 3.8 s native and ~9 s through a shim at 2x load (the Claude chain's notes in
    protect-bash.sh, which took its budget to 15 s for exactly that), and windows-latest's PowerShell 5.1 leg failed
    this way on every Python (CI runs 38178385999, 38201545601). Every start of this interpreter costs ~4 s."""
    root = _install(tmp_path, "C:/definitely/not/here/python.exe")
    shims = tmp_path / "shims"
    shims.mkdir()
    real = str(Path(sys.executable))
    (shims / "python.cmd").write_text(
        '@"%s" -c "import time; time.sleep(4)"\r\n@"%s" %%*\r\n' % (real, real), encoding="ascii")
    path = os.pathsep.join([str(shims), python_free_path()])
    r = _run(entry, root, "pre-tool", _plugin_write(root), {"PATH": path}, timeout=120)
    reason = json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert reason.startswith("Blocked: plugin and archive files"), \
        (reason, _rec(root, "pre-tool", "failed"), _rec(root, "pre-tool", "probes"))
    assert "used " + str(shims / "python.cmd").replace(BS, "/").lower() in _rec(root, "pre-tool", "failed").replace(
        BS, "/").lower(), _rec(root, "pre-tool", "failed")
    # The lookups ahead of the probes must be cheap: on windows-latest PowerShell 5.1 they once cost ~24 s of a 30 s
    # probing window (CI run 38277736359), and the slow probe then had no time left. Every stage is in the record.
    probes = _rec(root, "pre-tool", "probes")
    looks = [(m.group(1), int(m.group(2))) for m in re.finditer(r"lookup (\S+) (?:found|absent) (\d+)ms", probes)]
    assert [n for n, _ in looks][:2] == ["py", "python"], probes
    assert all(ms < 2000 for _, ms in looks), probes


def test_the_fallback_chain_never_asks_get_command():
    """Get-Command, not the probes, ate the PowerShell budget on windows-latest 5.1 (~22-25 s per fallback call that
    looked up an absent name; CI runs 38201545601, 38277736359), and that is not reproducible on a machine whose
    command discovery is fast, so the chain is held to its PATH search structurally. Comments may name it."""
    chain = _chain((SRC / "skyguard-entry.ps1").read_text(encoding="ascii"))
    code = [ln for ln in chain.splitlines() if not ln.lstrip().startswith("#")]
    assert not [ln for ln in code if re.search(r"\bGet-Command\b", ln, re.IGNORECASE)], "the chain calls Get-Command"
    assert any("Find-OnPath $cand[0]" in ln for ln in code)


@pytest.mark.parametrize("entry", ENTRIES)
def test_a_missing_configured_python_falls_back_and_still_decides(tmp_path, entry):
    root = _install(tmp_path, "C:/definitely/not/here/python.exe")
    path = os.pathsep.join([str(Path(sys.executable).parent), python_free_path()])
    t0 = time.monotonic()
    r = _run(entry, root, "pre-tool", _plugin_write(root), {"PATH": path})
    took = time.monotonic() - t0
    reason = json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    # windows-latest, PowerShell 5.1 (CI runs 38178385999, 38201545601: 25.1 s, "cannot run Python") with a working
    # python first on PATH. The 3 s / 8 s probe budget that refused it is fixed (see the slow-interpreter test); the
    # duration and the per-probe record are printed so a failure here says which probe ran out and how long it took.
    assert reason.startswith("Blocked: plugin and archive files (.esp .esm .esl .bsa .ba2) cannot be written directly."), \
        (reason, "call took {:.1f} s".format(took), _rec(root, "pre-tool", "failed"), _rec(root, "pre-tool", "probes"))
    failed = _rec(root, "pre-tool", "failed")
    assert " python configured C:/definitely/not/here/python.exe unusable; used " in failed, failed
    # recorded BEFORE the engine ran, so the canary reads DEGRADED until the configured one works again
    state, _ = health.hook_state(_hb(root), "pre-tool", time.time())
    assert state == "DEGRADED", state


@pytest.mark.parametrize("entry", ENTRIES)
def test_telemetry_reads_healthy_after_a_clean_call_and_degraded_after_a_fault(tmp_path, entry):
    root = _install(tmp_path)
    r = _run(entry, root, "pre-tool", _benign(root))
    assert r.stdout == b""
    assert health.hook_state(_hb(root), "pre-tool", time.time())[0] == "HEALTHY"
    assert _rec(root, "pre-tool", "received").split()[2].startswith("bytes=")
    _replace_run_py(root, "raise SystemExit(3)\n")
    _run(entry, root, "pre-tool", _benign(root))
    assert health.hook_state(_hb(root), "pre-tool", time.time())[0] == "DEGRADED"
    seqs = [int(_rec(root, "pre-tool", "invoked").split()[1].split(".")[0])]
    assert seqs == [2], seqs


@pytest.mark.parametrize("entry", ENTRIES)
def test_a_previous_call_that_never_finished_is_recorded_as_killed(tmp_path, entry):
    root = _install(tmp_path)
    _hb(root).mkdir(parents=True)
    (_hb(root) / "pre-tool.invoked").write_text("{:.3f} 41.99-1\n".format(time.time() - 600), encoding="utf-8")
    r = _run(entry, root, "pre-tool", _benign(root))
    assert r.stdout == b""
    failed = _rec(root, "pre-tool", "failed")
    assert " killed " in failed and "41.99-1" in failed, failed
    assert _rec(root, "pre-tool", "invoked").split()[1].startswith("42."), "the sequence did not continue"
    assert health.hook_state(_hb(root), "pre-tool", time.time())[0] == "DEGRADED"


@pytest.mark.parametrize("entry", ENTRIES)
def test_non_ascii_context_survives_byte_for_byte(tmp_path, entry):
    """The engine's answer is passed through as UTF-8 bytes, not through a console code page."""
    root = _install(tmp_path)
    body = ('{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", '
            '"permissionDecisionReason": "Skyrim \u2014 \u00e9t\u00e9 \\"quoted\\" \\\\ ok"}}').encode("utf-8")
    _replace_run_py(root, _prints(b"SKYGUARD-OK\r\n" + body))
    r = _run(entry, root, "pre-tool", _benign(root))
    assert r.stdout == body + b"\n", r.stdout
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("entry", ENTRIES)
def test_an_escaped_backslash_before_u_is_not_a_surrogate(tmp_path, entry):
    """`C:\\\\udata` in JSON is the text `C:\\udata`: an escaped backslash, then the letters `ud8...`.
    Only an ODD run of backslashes before `u` starts an escape; denying the even case refuses a real
    Windows path in a rule's own reason."""
    root = _install(tmp_path)
    for n in (2, 4):
        body = (b'{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", '
                b'"permissionDecisionReason": "C:' + BSB * n + b'ud83dfolder"}}')
        _replace_run_py(root, _prints(b"SKYGUARD-OK\n" + body))
        r = _run(entry, root, "pre-tool", _benign(root))
        assert r.returncode == 0 and r.stdout == body + b"\n", (n, r.stdout)
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"].startswith("C:" + BS)


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("role", ROLES)
def test_invalid_utf8_from_the_engine_never_reaches_codex(tmp_path, entry, role):
    """Codex parses the hook's stdout as UTF-8 JSON; a body it cannot decode lets the command RUN.
    The POSIX entry refuses it; the PowerShell entry decodes with replacement, so what it emits is
    valid UTF-8 by construction."""
    root = _install(tmp_path)
    if role == "pre-tool":
        body = (b'{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", '
                b'"permissionDecisionReason": "bad \xff\xfe byte"}}')
    else:
        body = b"SKYGUARD: Codex hooks live \xc3"
    _replace_run_py(root, _prints(b"SKYGUARD-OK\n" + body))
    r = _run(entry, root, role, _benign(root) if role == "pre-tool" else _session(root))
    assert r.returncode == 0
    r.stdout.decode("utf-8")   # never invalid
    if entry == "bash":
        assert r.stdout == _expected(role, "engine output is not valid UTF-8"), r.stdout
    elif role == "pre-tool":
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny", r.stdout


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("role", ROLES)
def test_a_nul_byte_in_the_engine_output_is_refused(tmp_path, entry, role):
    """Everything after a NUL must not vanish: `SKYGUARD-OK` + NUL + an allow would otherwise read as
    an empty body, i.e. ALLOW."""
    root = _install(tmp_path)
    _replace_run_py(root, _prints(b'SKYGUARD-OK\n\x00{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
                                  b'"permissionDecision":"allow"}}'))
    r = _run(entry, root, role, _benign(root) if role == "pre-tool" else _session(root))
    if entry == "bash":
        cause = "engine output has a NUL byte"
    else:
        cause = "engine output is not a verdict JSON" if role == "pre-tool" else BANNER_CAUSE
    assert r.returncode == 0 and r.stdout == _expected(role, cause), r.stdout


def _kill_pid(pid: int) -> None:
    if ON_WINDOWS:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


@pytest.mark.parametrize("entry", ENTRIES)
def test_a_grandchild_holding_stdout_does_not_outlast_the_deadline(tmp_path, entry):
    """The engine answers and exits, but a process it started keeps the stdout pipe open. Reading to
    EOF would wait for that grandchild -- past Codex's 60 s, which then RUNS the command. The entry
    must end within its own deadline: the PowerShell one bounds its drain (and denies), the POSIX one
    reads the engine's output from a file, so the engine's own answer arrives."""
    root = _install(tmp_path)
    pidfile = tmp_path / "grandchild.pid"
    _replace_run_py(root, (
        "import subprocess, sys\n"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(25)'], stdout=sys.stdout.fileno())\n"
        "open({!r}, 'w').write(str(c.pid))\n"
        "sys.stdout.buffer.write({!r})\n"
        "sys.stdout.buffer.flush()\n").format(str(pidfile), b"SKYGUARD-OK\n" + _GOOD_DENY))
    t = time.time()
    try:
        r = _run(entry, root, "pre-tool", _benign(root), {"SKYGUARD_WRAPPER_TIMEOUT_S": "3"}, timeout=60)
        took = time.time() - t
    finally:
        if pidfile.is_file():
            _kill_pid(int(pidfile.read_text()))
    assert r.returncode == 0
    assert took < 15, f"the entry waited for the grandchild ({took:.1f} s)"
    if entry == "bash":
        assert r.stdout == _GOOD_DENY + b"\n", r.stdout
    else:
        assert r.stdout == _expected("pre-tool", "engine did not answer within 3 s"), r.stdout


# --------------------------------------------------------------------------------------------------
# static properties
# --------------------------------------------------------------------------------------------------
def test_entry_scripts_are_ascii():
    for name in ("skyguard-entry.ps1", "skyguard-entry.sh"):
        data = (SRC / name).read_bytes()
        assert not data.startswith(b"\xef\xbb\xbf"), name + " has a BOM"
        bad = sorted({b for b in data if b > 0x7E or (b < 0x20 and b not in (0x09, 0x0A, 0x0D))})
        assert not bad, f"{name} has non-ASCII bytes {bad}"


def test_entry_deadlines_are_below_the_definition_timeouts():
    """Task 8 freezes timeouts 60 (pre-tool) and 120 (session-start); the wrapper must decide first.
    The definitions test (test_codex_definitions.py) checks the margin against hooks.json itself."""
    ps = (SRC / "skyguard-entry.ps1").read_text(encoding="ascii")
    sh = (SRC / "skyguard-entry.sh").read_text(encoding="ascii")
    assert "$limit = 40" in ps and "{ $limit = 95 }" in ps
    assert "LIMIT=40;" in sh and "&& LIMIT=95" in sh


CHAIN_BEGIN, CHAIN_END = "# PY-CHAIN-BEGIN", "# PY-CHAIN-END"


def _chain(text: str) -> str:
    assert text.count(CHAIN_BEGIN) == 1 and text.count(CHAIN_END) == 1
    return text[text.index(CHAIN_BEGIN):text.index(CHAIN_END)]


def test_fallback_chain_matches_the_claude_wrappers():
    import re
    claude = _chain((REPO / "agent" / "guards" / "adapters" / "claude" / "protect-bash.sh").read_text(encoding="utf-8"))
    # the POSIX entry carries the Claude chain verbatim
    assert _chain((SRC / "skyguard-entry.sh").read_text(encoding="ascii")) == claude
    # the PowerShell entry: the same candidates in the same order, after normalising syntax
    ps = _chain((SRC / "skyguard-entry.ps1").read_text(encoding="ascii"))
    sh_names = re.search(r'for c in ((?:"[^"]+"|\S+)(?: (?:"[^"]+"|\S+))*); do', claude).group(1)
    sh_names = [n.strip('"') for n in re.findall(r'"[^"]+"|\S+', sh_names)]
    ps_names = re.search(r"\$script:PyCandidates = @\((.*)\)\r?\n", ps).group(1)
    ps_names = [" ".join(re.findall(r"'([^']*)'", g)) for g in re.findall(r"@\(([^()]*)\)", ps_names)]
    assert ps_names == sh_names == ["py -3", "python", "python3"], (ps_names, sh_names)
    sh_ver = re.search(r"for \(\(m = (\d+); m >= (\d+); m--\)\)", claude).groups()
    ps_ver = re.search(r"\$script:PyVersions = (\d+)\.\.(\d+)", ps).groups()
    assert sh_ver == ps_ver, (sh_ver, ps_ver)
    sh_posix = re.search(r"for d in (/usr/bin/python3 /usr/local/bin/python3); do", claude).group(1).split()
    ps_posix = re.findall(r"'(/usr/[^']+)'", ps)
    assert sh_posix == ps_posix, (sh_posix, ps_posix)
    # the known-location bases, in order: LOCALAPPDATA/Programs/Python, PROGRAMFILES, C: (/c)
    assert ps.index("$env:LOCALAPPDATA") < ps.index("$env:ProgramFiles") < ps.index("$bases += 'C:'")
    assert claude.index("LOCALAPPDATA") < claude.index("PROGRAMFILES") < claude.index("bases+=(/c)")
    # and the Store alias is tried last in both
    assert ps.index("if ($null -ne $alias -and") > ps.index("$script:PyPosix)")
    assert claude.index('if [ -n "$PY_ALIAS" ] && py_good') > claude.index("/usr/local/bin/python3")
    # the same acceptance test: >= 3.10 and the engine's own --selftest nonce. The PowerShell entry checks
    # the version in its own process; the bash chain in ONE (run.py --selftest refuses < 3.10 itself).
    assert "sys.version_info[:2] >= (3, 10)" in ps and "--selftest" in ps
    assert "--selftest" in claude and "version_info" not in claude
