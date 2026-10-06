"""No tracked file carries a committed merge-conflict marker.

On 2026-10-06 a Plan 6 integration merge committed `.gitattributes` with its conflict markers still in it, and three
commits carried them before a later merge noticed: git itself treats the marker lines as (unmatchable) patterns, so
nothing failed. This reads every tracked text file once; it costs well under a second.
"""
import re
import subprocess

from conftest import REPO

MARKER = re.compile(rb"^(?:<{7}|>{7})(?: |$)|^={7}$", re.MULTILINE)


def _offenders(root):
    names = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True).stdout.split(b"\0")
    bad = []
    for raw in names:
        if not raw:
            continue
        path = root / raw.decode("utf-8")
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8000]:
            continue
        data = data.replace(b"\r\n", b"\n")
        if MARKER.search(data):
            bad.append(raw.decode("utf-8"))
    return bad


def test_no_tracked_file_has_a_conflict_marker():
    bad = _offenders(REPO)
    assert not bad, "committed merge-conflict markers in:\n" + "\n".join(bad)


def test_the_check_can_fail(tmp_path):
    """A planted marker in a tracked file is reported (the scan reads real git output, not a fixed list)."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_bytes(b"x\n<<<<<<< HEAD\ny\n=======\nz\n>>>>>>> other\n")
    (tmp_path / "b.txt").write_bytes(b"heading\n=======\n")      # 7 '=' alone is also a marker line
    (tmp_path / "c.txt").write_bytes(b"===== a five-equals rule is not one\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    assert sorted(_offenders(tmp_path)) == ["a.txt", "b.txt"]
