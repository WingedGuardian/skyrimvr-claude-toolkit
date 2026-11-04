"""Python this guard cannot parse: refused when its text shows a way to change something, let through when it plainly only reads."""
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, paths, shell_rules, writes  # noqa: E402

BROKEN = " !!"
CAN_CHANGE = [
    "print(1); $X", "print($X)", "${X}", "$X",
    "from os import remove; remove('a')", "from os import system; system('a')", "from os import *",
    "from shutil import rmtree", "import shutil; shutil.rmtree('a')", "import os as o; o.remove('a')",
    "import subprocess; subprocess.run(['a'])", "import tempfile", "import zipfile",
    "import argparse; argparse.FileType('w')('a')", "import operator; operator.methodcaller('unlink')(p)",
    "from operator import methodcaller as m",
    "o = open; o('a', 'w')", "print('x', file=open('a', 'w'))", "open('a', 'w')", "open('a', 'ab')", "open('a', 'r+')",
    "open('a', mode='w')", "open(p, m)", "open(f(x), 'w')", "open(\n 'a',\n 'w')",
    "from pathlib import Path; Path('a').replace('b')", "from pathlib import Path; Path('a').open('w')",
    "from pathlib import Path; Path('a').write_text('b')", "from pathlib import Path; Path('a').unlink()",
    "p.write_bytes(b)", "p.mkdir()", "p.rename(q)", "f.write(x)",
    "os.system('a')", "os.remove('a')", "os . replace('a', 'b')", "print(__builtins__)", "sys.modules['os'].remove('a')",
    "exec('a')", "eval('a')", "compile('a', 'b', 'exec')", "getattr(os, 'remove')('a')",
    "print($(rm x))", "print(`rm x`)", "print('$(rm x)')",
]
ONLY_READS = [
    "import sys,json;print(json.load(sys.stdin)['a'] ",
    "import re\nfrom pathlib import Path\ns=Path('a').read_text(encoding='utf-8')\nprint(f'{re.findall(r'x', s)}')",
    "print(f'{'faction':<16}')",
    "b=open('a','rb').read()\nprint(f\"{b[:3]==b'\\xef'}\"",
    "s=open(sys.argv[1], encoding='utf-8').read(); print(s",
    "import pathlib\nb=pathlib.Path('a').read_bytes()\nprint(1",
    "import os\nprint(os.environ['A'], os.path.join('a', 'b')",
    "print('$X' ",
    "print(1",
]


@pytest.mark.parametrize("code", CAN_CHANGE)
def test_unreadable_code_that_can_change_something_is_refused(code):
    with pytest.raises(ValueError, match="could not be parsed"):
        writes.text_write_targets(code + BROKEN, python_literals=True)


@pytest.mark.parametrize("code", ONLY_READS)
def test_unreadable_code_that_only_reads_names_no_target(code):
    assert writes.text_write_targets(code, python_literals=True) == []


def _decide(command):
    a = audit.Audit("")
    a.raw = lambda line: None
    return shell_rules.decide(command, paths.roots("C:/G", "", {}), a, cwd="C:/G", shell="bash")


def test_a_read_only_one_liner_in_a_newer_python_syntax_is_allowed():
    assert _decide("""python -c "print(f'{'a':<16}')" """).decision == "allow"


def test_a_shell_variable_in_the_code_position_stays_refused():
    with pytest.raises(ValueError, match="could not be parsed"):
        _decide('python -c "print(1); $X"')
