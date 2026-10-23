"""The native hook's session cwd must not authorize an unplaced mutation."""
import json
import sys

import pytest
from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import engine, paths, portable
from skyguard.adapters import codex
from skyguard.audit import Audit
from modding_harness.transport import normalize

ROOTS = paths.Roots("C:/Game", "C:/Config", install_dirs=("c:/game",), config_dirs=("c:/config",))


CASES = [
    ("powershell", "Set-Content -LiteralPath 'Owned.ess' -Value x", True),
    ("powershell", "Remove-Item -LiteralPath 'Owned.ess'", True),
    ("powershell", "Copy-Item -LiteralPath 'C:/Source' -Destination 'Owned.ess'", True),
    ("powershell", "Move-Item -LiteralPath 'Owned.ess' -Destination 'C:/Elsewhere/a'", True),
    ("powershell", "git reset --hard", True),
    ("powershell", "git clean -fdx", True),
    ("powershell", "Get-Content -LiteralPath 'Owned.ess'", False),
    ("powershell", "git status", False),
    ("powershell", "Set-Content -LiteralPath 'C:/Scratch/notes.txt' -Value x", False),
    ("powershell", "Set-Location -LiteralPath 'C:/Scratch'; Set-Content -LiteralPath 'notes.txt' -Value x", True),
    ("bash", "printf x > Owned.ess", True),
    ("bash", "rm -- Owned.ess", True),
    ("bash", "cp /source Owned.ess", True),
    ("bash", "mv Owned.ess /elsewhere", True),
    ("bash", "cat Owned.ess", False),
    ("bash", "printf x > C:/scratch/notes.txt", False),
    ("bash", "cd /scratch && printf x > notes.txt", True),
    ("bash", "git -C C:/scratch reset --hard", False),
    ("powershell", "Rename-Item -LiteralPath 'C:/Scratch/old.txt' -NewName 'new.txt'", False),
    ("powershell", "Rename-Item -LiteralPath 'Owned.ess' -NewName 'new.ess'", True),
    ("powershell", "Set-Location -LiteralPath 'C:/Scratch'; [IO.File]::WriteAllText('Owned.ess', 'x')", True),
    ("bash", "git -C relative reset --hard", True),
    ("bash", "git clean -ndx", False),
    ("powershell", "Set-Location -LiteralPath 'C:/Owned-Missing'; Set-Content -LiteralPath 'Owned.save' -Value x", True),
    ("bash", "cd /owned-missing; printf x > Owned.save", True),
    ("powershell", "Set-Content -LiteralPath '/Scratch/Owned.ess' -Value x", True),
    ("powershell", "Set-Content -LiteralPath '\\Scratch\\Owned.ess' -Value x", True),
    ("powershell", "Set-Content -LiteralPath '/c/Scratch/Owned.ess' -Value x", True),
    ("bash", "apply_patch <<'PATCH'\n*** Begin Patch\n*** Add File: /Scratch/Owned.ess\n+x\n*** End Patch\nPATCH", True),
    ("bash", "apply_patch <<'PATCH'\n*** Begin Patch\n*** Add File: /c/Scratch/Owned.ess\n+x\n*** End Patch\nPATCH", True),
    ("bash", "pwsh -NoProfile -Command \"Set-Content -LiteralPath '/Scratch/Owned.ess' -Value x\"", True),
    ("bash", "apply_patch <<'PATCH'\n*** Begin Patch\n*** Add File: Owned.ess\n+x\n*** End Patch\nPATCH", True),
    ("bash", "cd /scratch && apply_patch <<'PATCH'\n*** Begin Patch\n*** Add File: notes.txt\n+x\n*** End Patch\nPATCH", True),
]


@pytest.mark.parametrize("shell,command,refused", CASES)
@pytest.mark.skipif(sys.platform != "win32", reason="Windows native cwd/drive cases; portable Bash cases below")
def test_missing_execution_cwd_checks_mutations_not_reads(tmp_path, shell, command, refused):
    req = codex.to_request(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
        "cwd": "C:/Session" if shell == "powershell" else "/session", "tool_input": {"command": command}}),
        platform="win32" if shell == "powershell" else "linux")
    value = engine.evaluate_shell(req, ROOTS, Audit(str(tmp_path)))
    assert (value.decision == "deny") is refused, (command, value)
    if refused:
        assert value.status == "failed"
        assert "execution workdir" in value.reason


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native explicit workdir case")
def test_absolute_workdir_keeps_relative_mutation_qualified(tmp_path):
    req = codex.to_request(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
        "cwd": "C:/Session", "tool_input": {"command": "Set-Content notes.txt x", "workdir": "C:/Scratch"}}),
        platform="win32")
    assert engine.evaluate_shell(req, ROOTS, Audit(str(tmp_path))).decision == "allow"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native normalized bridge case")
def test_normalized_bridge_keeps_context_restriction(tmp_path):
    action = normalize("codex", {"tool_name": "Bash", "cwd": "C:/Session",
        "tool_input": {"command": "Set-Content Owned.ess x"}}, platform="win32", default_shell="powershell")
    req = portable.request(action)
    assert engine.evaluate_shell(req, ROOTS, Audit(str(tmp_path))).decision == "deny"


@pytest.mark.parametrize("case,refused", [("write", True), ("read", False), ("cd-write", True),
                                        ("absolute-write", False), ("copy", False), ("git", False)])
def test_portable_bash_context(tmp_path, case, refused):
    absolute = tmp_path.as_posix()
    commands = {"write": "printf x > Owned.save", "read": "cat Owned.save",
                "cd-write": "cd '" + absolute + "'; printf x > Owned.save",
                "absolute-write": "printf x > '" + absolute + "/notes.txt'",
                "copy": "cp relative-source '" + absolute + "/notes.txt'",
                "git": "git -C '" + absolute + "' reset --hard"}
    req = codex.to_request(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
        "cwd": str(tmp_path), "tool_input": {"command": commands[case]}}))._replace(shell="bash")
    result = engine.evaluate_shell(req, ROOTS, Audit(str(tmp_path)))
    assert (result.decision == "deny") is refused, result
