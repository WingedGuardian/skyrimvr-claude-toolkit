"""guard3 (v4.0.0 review C-docs MEDIUM-2): a literal name inline interpreter code deletes is resolved where the code
runs, as a shell operand is. `python -c "shutil.rmtree('Data')"` from the install deletes the install's Data folder.

Through run.py in process (record.fire_inprocess) on a scratch install; nothing is executed."""
import importlib.util
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))

from skyguard import deletes  # noqa: E402


def _rec():
    spec = importlib.util.spec_from_file_location("record", REPO / "scripts" / "guard-corpus" / "record.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def inst(tmp_path_factory):
    rec = _rec()
    return rec, rec.make_install(tmp_path_factory.mktemp("idel"), REPO / ".claude" / "hooks", "declared")


def fire(inst, command, cwd="<I>"):
    rec, install = inst
    I = install.as_posix()
    return rec.fire_inprocess(install, "protect-bash.sh", {"tool_name": "Bash", "session_id": "s",
                                                           "cwd": cwd.replace("<I>", I),
                                                           "tool_input": {"command": command.replace("<I>", I)}})


REFUSED = {
    "python-rmtree": "python -c \"import shutil; shutil.rmtree('Data')\"",
    "python-from-import": "python -c \"from shutil import rmtree; rmtree('Data/meshes')\"",
    "python-os-remove": "python -c \"import os; os.remove('Data/x.txt')\"",
    "python-path-unlink": "python -c \"from pathlib import Path; Path('Data/x.txt').unlink()\"",
    "python-after-cd": "cd Data && python -c \"import os; os.remove('x.txt')\"",
    "python-heredoc": "python - <<'EOF'\nimport shutil\nshutil.rmtree('Data')\nEOF",
    "node-rmsync": "node -e \"require('fs').rmSync('Data', {recursive: true})\"",
    "node-unlinksync": "node -e \"require('fs').unlinkSync('Data/x.txt')\"",
    "perl-unlink": "perl -e \"unlink 'Data/x.txt'\"",
}


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_a_relative_interpreter_delete_from_the_install_is_refused(inst, case):
    got = fire(inst, REFUSED[case])
    assert got["decision"] == "deny" and got["rules"] == {"S-DEL-INSTALL"}, got


ALLOWED = {
    "outside-the-install": ("python -c \"import shutil; shutil.rmtree('Data')\"", "C:/work"),
    "scratch-name": ("python -c \"import shutil; shutil.rmtree('/tmp/x')\"", "<I>"),
    "a-read": ("python -c \"print(open('Data/x.txt').read())\"", "<I>"),
    "a-name-built-at-run-time": ("python -c \"import os; os.remove(os.path.join('a', 'b'))\"", "C:/work"),
}


@pytest.mark.parametrize("case", sorted(ALLOWED))
def test_ordinary_interpreter_code_is_not_a_protected_delete(inst, case):
    command, cwd = ALLOWED[case]
    got = fire(inst, command, cwd)
    assert not any(r.startswith("S-DEL") for r in got["rules"]), got


def test_the_python_reader_names_only_literal_delete_arguments():
    code = ("import os, shutil\nfrom pathlib import Path\nshutil.rmtree('a')\nos.unlink(path='b')\n"
            "Path('c', 'd').rmdir()\nos.remove(x)\nprint('rm -rf e')\n")
    assert deletes.interpreter_delete_targets(code, True) == ["a", "b", "c/d"]
    assert deletes.interpreter_delete_targets("print(1", True) == []
