"""Health probes must preserve the engine result in every Bash entry."""

from __future__ import annotations

import re
import subprocess

import pytest

from conftest import BASH, REPO
from test_python_fallback import REAL_PY, _failed, _fire, _install, _reason
import test_codex_entry as codex


CLAUDE = (
    "backup-before-edit", "protect-bash", "protect-files", "protect-mcp",
    "session-kb-guard", "snapshot-before-tool",
)
SOURCES = tuple(f"claude/{name}.sh" for name in CLAUDE) + ("codex/skyguard-entry.sh",)
FAULT = '''import sys
if "--selftest" in sys.argv:
    print("skyguard-selftest:" + sys.argv[sys.argv.index("--selftest") + 1])
    raise SystemExit(0)
print("FIXTURE_ENGINE_DIAGNOSTIC")
raise SystemExit(7)
'''


@pytest.mark.parametrize("hook", CLAUDE)
def test_healthy_probe_preserves_failed_claude_engine(tmp_path, hook):
    root = _install(tmp_path, REAL_PY)
    codex._replace_run_py(root, FAULT)
    result = _fire(root, [], hook + ".sh", {
        "tool_name": "Bash", "tool_input": {"command": "printf hello"},
        "cwd": root.as_posix(),
    })
    assert result.returncode == 0, result.stderr
    assert "skyguard-selftest:" not in result.stdout, result.stdout
    assert " engine exit 7" in _failed(root, hook), _failed(root, hook)
    heartbeat = root / ".claude/backups/.hook-heartbeat/claude"
    assert not (heartbeat / f"{hook}.emitted").exists()
    if hook.startswith("protect-"):
        assert "engine failed (exit 7)" in _reason(result), result.stdout
    else:
        audit = (root / ".claude/backups/AUDIT_LOG.txt").read_text(encoding="utf-8")
        assert "FAILED: engine error (exit 7)" in audit, audit
        if hook == "session-kb-guard":
            assert "KB GUARD: NOT RUN -- the engine failed (exit 7)" in result.stdout
            assert "knowledgebase NOT snapshotted" in audit
        else:
            assert result.stdout == ""
            assert "NO backup/snapshot was taken" in audit


@pytest.mark.parametrize("role", codex.ROLES)
def test_healthy_probe_preserves_failed_codex_engine(tmp_path, role):
    root = codex._install(tmp_path)
    codex._replace_run_py(root, FAULT)
    payload = codex._benign(root) if role == "pre-tool" else codex._session(root)
    result = codex._run("bash", root, role, payload)
    assert result.returncode == 0, result.stderr
    assert "engine exited 7" in result.stdout.decode(), result.stdout
    assert " engine engine exited 7" in codex._rec(root, role, "failed")
    assert not (codex._hb(root) / f"{role}.emitted").exists()


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("field", ("OUT", "RC", "NUL", "WD"))
@pytest.mark.parametrize("healthy", (True, False), ids=("healthy", "unhealthy"))
def test_py_good_preserves_caller_field(tmp_path, source, field, healthy):
    text = (REPO / "agent/guards/adapters" / source).read_text(encoding="ascii")
    functions = re.findall(r"(?ms)^py_good\(\) \{\n.*?^\}", text)
    assert len(functions) == 1
    # A deterministic bounded-process witness writes all four real output fields.
    # Bash dynamic scope must keep those writes inside the production probe function.
    bounded_rc = 0 if healthy else 9
    script = tmp_path / "probe.sh"
    script.write_text(
        "_now() { MS=0; }\n"
        "PROBE_END=1000; PROBE_MS=1000; RUNPY=unused\n"
        f'_bounded() {{ OUT="skyguard-selftest:${{@: -1}}"; RC={bounded_rc}; NUL=probe-nul; WD=probe-wd; }}\n'
        + functions[0] + "\n"
        'OUT=original-output; RC=7; NUL=original-nul; WD=original-wd\n'
        'py_good unused; status=$?\n'
        f'printf "%s\\n%s\\n" "$status" "${{{field}}}"\n',
        encoding="ascii", newline="\n",
    )
    result = subprocess.run([BASH, str(script)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    status, value = result.stdout.splitlines()
    assert status == ("0" if healthy else "1"), result.stdout
    expected = {"OUT": "original-output", "RC": "7", "NUL": "original-nul", "WD": "original-wd"}
    assert value == expected[field], result.stdout
