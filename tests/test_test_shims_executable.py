"""The PATH shims under tests/bin must be executable in the git index (Linux CI run 38187755448).

Windows ignores the mode bit, so py/python/python3 sat at 100644 unnoticed. On Linux a non-executable file on
PATH is skipped, so setup.sh found the runner's real Python instead of the shim: every test steering the detected
interpreter (FAKE_PYTHON_EXE -- backslash paths, the Store alias, a too-old Python, the tested-range message,
the Codex entries' interpreter) failed there while testing nothing about setup.sh.
"""
import subprocess

import pytest

from conftest import REPO


def _index_modes():
    r = subprocess.run(["git", "-C", str(REPO), "ls-files", "-s", "--", "tests/bin"], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("COULD NOT CHECK: not a git work tree (%s)" % r.stderr.strip()[:200])
    out = r.stdout
    return {line.split("\t", 1)[1]: line.split()[0] for line in out.splitlines() if "\t" in line}


def test_every_tests_bin_shim_is_executable_in_the_index():
    modes = _index_modes()
    assert {"tests/bin/py", "tests/bin/python", "tests/bin/python3", "tests/bin/which"} <= set(modes), modes
    assert {p: m for p, m in modes.items() if m != "100755"} == {}, modes
