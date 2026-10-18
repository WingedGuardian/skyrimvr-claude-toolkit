"""Python interface selection: later script/module arguments are data, not inline source."""
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / 'tools'))
from skyguard import deletes, shellparse, writes


def source(command):
    parsed = shellparse.parse(command, 'bash')
    assert parsed.ok and len(parsed.execs) == 1
    return deletes.interpreter_code(parsed.execs[0])


@pytest.mark.parametrize('command', [
    "python -m pytest -c 'not Python source !'",
    "python -mpytest -r 'not Python source !'",
    "python owned.py -c 'not Python source !'",
    "python -- owned.py -c 'not Python source !'",
])
def test_module_and_script_arguments_are_not_inline_code(command):
    assert source(command) == ''


@pytest.mark.parametrize('options', ['-c', '-Bc', '-IBc', '-W ignore -c', '-X utf8 -c',
                                   '-BW ignore -c', '-E -c', '--check-hash-based-pycs always -c', '-BBBBBc'])
def test_first_command_is_the_only_executable_source(options):
    code = source("python " + options + " 'print(1)' -c 'not Python source !'")
    assert code == 'print(1)'
    assert writes.text_write_targets(code, python_literals=True) == []


@pytest.mark.parametrize('command', [
    "python -c'print(1)'", "python -Bc'print(1)'", "py -3.13 -c 'print(1)'",
    "py -V:PythonCore/3.13 -c 'print(1)'",
])
def test_attached_code_and_launcher_selectors_keep_the_actual_source(command):
    assert source(command) == 'print(1)'


@pytest.mark.parametrize('head', ['python', 'python -', 'python -- -'])
def test_stdin_source_is_retained(head):
    assert source(head + " <<'EOF'\nprint(1)\nEOF") == 'print(1)\n'


@pytest.mark.parametrize('head', ['python -c "print(1)"', 'python -i -c "print(1)"',
                                  'python -i -m owned', 'python -i owned.py',
                                  'python -c "exec(input())"'])
def test_supplied_interpreter_stdin_write_remains_visible(head):
    code = source(head + " <<'EOF'\nopen('Owned.esp', 'wb').write(b'x')\nEOF")
    assert writes.text_write_targets(code, python_literals=True) == ['Owned.esp']


def test_selected_source_is_not_rescanned_as_another_flag():
    assert source("python -c '-compile; print(1)'") == '-compile; print(1)'


def test_real_inline_plugin_write_is_still_selected_and_rejected():
    code = source('python -Bc "open(\'Owned.esp\', \'wb\').write(b\'x\')" -c ignored')
    assert writes.text_write_targets(code, python_literals=True) == ['Owned.esp']


def test_real_unknown_inline_syntax_still_refuses():
    with pytest.raises(ValueError, match='Python literal write scope could not be parsed'):
        writes.text_write_targets(source("python -c 'not Python source !'"), python_literals=True)
