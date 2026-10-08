"""Inspection literals are data; launched or unknown code keeps state protection.

All fixture shell strings are judged as data, never executed.
"""
import shlex
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from skyguard import audit, paths, self_rules, shell_rules, shellparse

HISTORICAL = 'cd C:/fixture/scratch && python -B -c "\nfrom skyguard import shellparse\nfor c in [\\"[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile(\'C:/outside/.skyguard/lock.json\',\'OnlyErrorDialogs\',\'SendToRecycleBin\')\\", \\"[IO.File]::Delete(\'C:/outside/a\')\\", \\"[System.IO.Directory]::Delete(\'C:/outside/a\', \\$true)\\"]:\n    p=shellparse.parse(c,\'powershell\'); print(p.ok, [(e.program,e.argv) for e in p.execs])\n"; grep -n "io.file\\.\\|io\\.directory" skyguard/shellparse.py | head'
OUTSIDE = "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile('C:/outside/.skyguard/lock.json','OnlyErrorDialogs','SendToRecycleBin')"
FILE_SAMPLE = "[IO.File]::Delete('C:/outside/a')"
DIR_SAMPLE = "[System.IO.Directory]::Delete('C:/outside/a', $true)"
STATE_API = "[IO.File]::Delete('C:/fixture/install/.skyguard/lock.json')"
PYTHON_DELETE = "File.Delete('C:/fixture/install/.skyguard/lock.json')"
INSPECTION = (
    "from skyguard import shellparse\n"
    "for c in " + repr([OUTSIDE, FILE_SAMPLE, DIR_SAMPLE]) + ":\n"
    "    p=shellparse.parse(c,'powershell'); print(p.ok, [(e.program,e.argv) for e in p.execs])\n"
)

def _wrap(source, quoting):
    if quoting == "posix":
        quoted = shlex.quote(source)
    else:
        quoted = '"' + source.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('`', '\\`') + '"'
    return "python -B -c " + quoted

def _judge(command):
    trail = audit.Audit("")
    trail.raw = lambda _line: None
    roots = paths.roots("C:/fixture/install", "", {"SKYRIM_GAME_ROOT": "C:/fixture/install", "SKYRIM_CONFIG_DIR": "C:/fixture/config"})
    verdict = shell_rules.decide(command, roots, trail,
                                 cwd="C:/fixture/scratch", shell="bash", env={})
    return verdict, trail.fired

CONTROLS = {
    "python-net-file": "from System.IO import File; " + PYTHON_DELETE,
    "python-net-directory": "from System.IO import Directory; Directory.Delete('C:/fixture/install/.skyguard/lock.json')",
    "subprocess": "import subprocess; subprocess.run(['powershell','-Command'," + repr(STATE_API) + "])",
    "subprocess-alias": "from subprocess import run as r; r(['powershell','-Command'," + repr(STATE_API) + "])",
    "exec": "exec(" + repr(PYTHON_DELETE) + ")",
    "eval": "eval(" + repr(PYTHON_DELETE) + ")",
    "exec-alias": "r=exec; r(" + repr(PYTHON_DELETE) + ")",
    "eval-alias": "r=eval; r(" + repr(PYTHON_DELETE) + ")",
    "rebound-print": "print=eval; print(" + repr(PYTHON_DELETE) + ")",
    "unknown-call": "consume(" + repr(STATE_API) + ")",
    "unknown-import": "from custom import print; print(" + repr(STATE_API) + ")",
    "parser-import-alias": "from skyguard import shellparse as print; print(" + repr(STATE_API) + ")",
    "f-string": "print(f" + repr(STATE_API) + ")",
    "print-file": "print(" + repr(STATE_API) + ", file=stream)",
    "function-binding": "def print(x):\n    return x\nprint(" + repr(STATE_API) + ")",
    "attribute-write": "obj.value=" + repr(STATE_API) + "; print(obj.value)",
    "subscript-write": "obj[0]=" + repr(STATE_API) + "; print(obj[0])",
    "unicode": "label='caf\u00e9'; print(" + repr(STATE_API) + ")",
    "syntax-error": "print(" + repr(STATE_API),
    "rebound-shellparse": "from skyguard import shellparse; shellparse=other; shellparse.parse(" + repr(STATE_API) + ",'powershell')",
    "for-print-binding": "for print in [eval]: print(" + repr(PYTHON_DELETE) + ")",
    "comprehension-print-binding": "print([print(" + repr(PYTHON_DELETE) + ") for print in [eval]])",
    "parser-without-import": "shellparse.parse(" + repr(STATE_API) + ",'powershell')",
}

@pytest.mark.parametrize("quoting", ["posix", "double"])
@pytest.mark.parametrize("control", sorted(CONTROLS))
def test_inspection_literals_allow_but_executed_or_unknown_code_keeps_state_refusal(quoting, control):
    # Every parameter first watches the candidate's false refusal RED.
    verdict, fired = _judge(_wrap(INSPECTION, quoting))
    assert verdict.decision == "allow", (verdict, fired)
    verdict, fired = _judge(_wrap(CONTROLS[control], quoting))
    assert verdict.decision == "deny", (control, verdict, fired)
    general_delete = {"exec", "eval", "exec-alias", "eval-alias", "rebound-print",
                      "for-print-binding", "comprehension-print-binding"}
    expected_rule = "S-DEL-INSTALL" if control in general_delete else "S-SELF-STATE"
    assert (expected_rule, "deny") in fired, (control, verdict, fired)
    # The real Python API controls above already reached general install deletion
    # before this fix. Also witness the state scanner itself with its quoted .NET
    # marker, so a permissive helper cannot survive via the general delete rule.
    witness = _wrap(CONTROLS[control].replace(PYTHON_DELETE, STATE_API), quoting)
    roots = paths.roots("C:/fixture/install", "", {"SKYRIM_GAME_ROOT": "C:/fixture/install", "SKYRIM_CONFIG_DIR": "C:/fixture/config"})
    hit = self_rules.shell_scan(witness, shellparse.parse(witness, "bash"), roots, "C:/fixture/scratch", {})
    assert hit and hit[0] == self_rules.STATE, (control, hit)

def test_exact_historical_k18699_shell_quoting_is_inspection_data():
    verdict, fired = _judge(HISTORICAL)
    assert verdict.decision == "allow", (verdict, fired)

@pytest.mark.parametrize("quoting", ["posix", "double"])
def test_inspection_comments_are_data_and_other_interpreters_stay_conservative(quoting):
    comment = "# " + repr(OUTSIDE) + "; " + FILE_SAMPLE + "\nprint('ready')"
    verdict, fired = _judge(_wrap(comment, quoting))
    assert verdict.decision == "allow", (verdict, fired)
    # A Python-shaped read does not establish an inspection allowlist for Node.
    node = _wrap("print(" + repr(STATE_API) + ")", quoting).replace("python -B -c ", "node -e ", 1)
    verdict, fired = _judge(node)
    assert verdict.decision == "deny", (verdict, fired)
    assert ("S-SELF-STATE", "deny") in fired, (verdict, fired)

@pytest.mark.parametrize("failure", ["ast", "ast-recursion", "tokenize"])
def test_inspection_parser_failure_preserves_original_state_refusal(monkeypatch, failure):
    verdict, fired = _judge(_wrap(INSPECTION, "posix"))
    assert verdict.decision == "allow", (verdict, fired)
    def broken(*_args, **_kwargs):
        if failure == "ast-recursion":
            raise RecursionError("controlled deep Python source")
        if failure.startswith("ast"):
            raise SyntaxError("controlled unavailable Python parser")
        raise self_rules.tokenize.TokenError("controlled unavailable tokenizer")
    with monkeypatch.context() as patch:
        if failure.startswith("ast"):
            patch.setattr(self_rules.ast, "parse", broken)
        else:
            patch.setattr(self_rules.tokenize, "generate_tokens", broken)
        verdict, fired = _judge(_wrap(INSPECTION, "posix"))
    assert verdict.decision == "deny", (verdict, fired)
    assert ("S-SELF-STATE", "deny") in fired, (verdict, fired)
