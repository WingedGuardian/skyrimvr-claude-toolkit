"""Python strings are read as Python data; no interpreter code executes."""
import sys
from pathlib import Path

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import targets, writes


@pytest.mark.parametrize("template", [
    "open({path}, 'wb')", "open({path}, mode='a')", "io.open({path}, 'r+')",
    "builtins.open({path}, 'x')", "os.truncate({path}, 0)",
    "open(file={path}, mode='wb')", "os.truncate(path={path}, length=0)",
    "Path({path}).write_bytes(b'x')", "pathlib.Path({path}).write_text('x')",
    "Path({path}).open('w')", "Path({path}).open(mode='a')",
])
@pytest.mark.parametrize("path", ["C:\\profile\\Saves\\A.ess", "\\\\?\\C:\\profile\\Saves.\\A'B.ess", "C:/profile/Saves/é.ess"])
def test_python_write_literals_decode_to_the_filename_value(template, path):
    code = template.format(path=repr(path))
    expected = str(Path(path)) if 'Path(' in code else path
    assert writes.text_write_targets(code, python_literals=True) == [expected]


@pytest.mark.parametrize("code", [
    "open('C:/Saves/A.ess')", "open('C:/Saves/A.ess', 'rb')",
    "Path('C:/Saves/A.ess').read_bytes()", "Path('C:/Saves/A.ess').open('r')",
    "print(\"open('C:/Saves/A.ess', 'wb')\")", "# open('C:/Saves/A.ess', 'wb')",
    "open(variable, 'wb')", "open(f'C:/Saves/{name}.ess', 'wb')",
    "open('C:/Saves/A.ess', mode)",
])
def test_reads_data_and_dynamic_expressions_do_not_invent_a_literal_write(code):
    assert writes.text_write_targets(code, python_literals=True) == []


def test_raw_python_and_node_literals_keep_their_own_language_rules():
    assert writes.text_write_targets(r"open(r'C:\profile\A.ess', 'wb')", python_literals=True) == [r'C:\profile\A.ess']
    assert writes.text_write_targets(r"fs.writeFileSync('C:\\profile\\A.ess', x)") == [r'C:\\profile\\A.ess']


@pytest.mark.parametrize('parts', [('profile', 'Saves', 'A.ess'), ('/first/A.ess', '/actual/B.ess'), ('C:/first/A.ess', 'C:/actual/B.ess')])
def test_python_path_constructor_uses_its_complete_literal_arguments(parts):
    code = 'Path(' + ', '.join(repr(part) for part in parts) + ").write_bytes(b'x')"
    assert writes.text_write_targets(code, python_literals=True) == [str(Path(*parts))]


def test_dynamic_path_component_does_not_invent_first_argument_as_the_target():
    assert writes.text_write_targets("Path('C:/Saves/A.ess', filename).write_bytes(b'x')", python_literals=True) == []


def test_oversized_python_analysis_refuses_instead_of_guessing_an_identity():
    with pytest.raises(ValueError, match='analysis limit'):
        writes.text_write_targets('#' * (1024 * 1024 + 1), python_literals=True)


@pytest.mark.parametrize('code', ["open('C:/Saves/A.ess', 'wb'", "print 'another Python version'; open('C:/Saves/A.ess', 'wb')"])
def test_unparsed_python_cannot_claim_no_literal_writes(code):
    with pytest.raises(ValueError, match='scope could not be parsed'):
        writes.text_write_targets(code, python_literals=True)


def test_file_drive_relative_operand_does_not_guess_another_drives_cwd():
    assert targets.resolve_literal('C:A.ess', 'D:/profile/Saves', {}) is None
