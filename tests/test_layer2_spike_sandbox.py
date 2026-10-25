"""Plan 6 Task 0: the Layer 2 spike sandbox guard must refuse anything outside scratch.

Includes the in-file mutation `l2-sandbox-realpath`: comparing unresolved strings
instead of resolved paths must turn the junction row red. (Phase A adds only new
files, so the twin lives here rather than in test_mutations.py; Task 18 moves it.)
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parent.parent / "scripts" / "layer2-spike" / "sandbox.py"


def _load(path=SPIKE, name="l2_sandbox"):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def box(tmp_path, monkeypatch):
    root = tmp_path / "box"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("SKYGUARD_LOCK_SANDBOX", str(root))
    return root, outside


def _junction(link: Path, target: Path) -> bool:
    if os.name != "nt":
        try:
            os.symlink(str(target), str(link), target_is_directory=True)
            return True
        except OSError:
            return False
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       capture_output=True, text=True)
    return r.returncode == 0 and link.exists()


def test_inside_is_ok(box):
    sb = _load()
    root, _ = box
    f = root / "a.esm"
    f.write_bytes(b"x")
    assert sb.assert_inside(f) == sb.resolve(f)


def test_root_itself_is_a_violation(box):
    sb = _load()
    root, _ = box
    with pytest.raises(sb.SandboxViolation):
        sb.assert_inside(root)


def test_dotdot_escape_is_a_violation(box):
    sb = _load()
    root, outside = box
    with pytest.raises(sb.SandboxViolation):
        sb.assert_inside(str(root) + os.sep + ".." + os.sep + "outside" + os.sep + "x.esm")


def test_junction_pointing_outside_is_a_violation(box):
    sb = _load()
    root, outside = box
    (outside / "Skyrim.esm").write_bytes(b"real")
    link = root / "j"
    if not _junction(link, outside):
        pytest.skip("cannot create a junction here")
    with pytest.raises(sb.SandboxViolation):
        sb.assert_inside(link / "Skyrim.esm")


def test_unset_variable_is_a_violation(box, monkeypatch):
    sb = _load()
    root, _ = box
    monkeypatch.delenv("SKYGUARD_LOCK_SANDBOX")
    with pytest.raises(sb.SandboxViolation):
        sb.assert_inside(root / "a.esm")


def test_violation_is_not_an_exception(box):
    sb = _load()
    assert issubclass(sb.SandboxViolation, BaseException)
    assert not issubclass(sb.SandboxViolation, Exception)


def test_hardlink_is_not_lockable(box):
    sb = _load()
    root, outside = box
    src = outside / "Skyrim.esm"
    src.write_bytes(b"real")
    link = root / "Skyrim.esm"
    os.link(str(src), str(link))
    with pytest.raises(sb.SandboxViolation):
        sb.assert_lockable(link)
    copy = root / "Copy.esm"
    shutil.copyfile(str(src), str(copy))
    assert sb.assert_lockable(copy)


def test_mutation_l2_sandbox_realpath_turns_the_junction_row_red(box, tmp_path):
    """Mutation l2-sandbox-realpath: resolve() without realpath must let the junction through."""
    sb = _load()
    root, outside = box
    (outside / "Skyrim.esm").write_bytes(b"real")
    link = root / "j"
    if not _junction(link, outside):
        pytest.skip("cannot create a junction here")
    text = SPIKE.read_text(encoding="utf-8")
    needle = "real = os.path.realpath(os.fspath(path))"
    assert needle in text, "mutation anchor moved"
    mutant_path = tmp_path / "sandbox_mutant.py"
    mutant_path.write_text(text.replace(needle, "real = os.path.abspath(os.fspath(path))"),
                           encoding="utf-8")
    mutant = _load(mutant_path, "l2_sandbox_mutant")
    # The mutant ACCEPTS the junction path: the junction row would go red under it.
    assert mutant.assert_inside(link / "Skyrim.esm")
    # ...while the real module refuses it.
    with pytest.raises(sb.SandboxViolation):
        sb.assert_inside(link / "Skyrim.esm")


# --------------------------------------------------------------------- leftovers go to the bin (review L4b L6)

def test_a_leftover_goes_to_the_injected_bin_and_never_outside_the_box(box):
    root, outside = box
    sb = _load()
    left = root / "rows"
    left.mkdir()
    sent = []
    sb.recycle(str(left), recycler=lambda ps: {p: sent.append(p) for p in ps})
    assert sent == [sb.resolve(str(left))]
    with pytest.raises(sb.SandboxViolation):
        sb.recycle(str(outside), recycler=lambda ps: pytest.fail("the bin was offered a path outside the box"))


def test_a_leftover_the_bin_refuses_stays_and_stops_the_probe(box):
    root, _ = box
    sb = _load()
    left = root / "rows"
    left.mkdir()
    with pytest.raises(SystemExit, match="left in place"):
        sb.recycle(str(left), recycler=lambda ps: {p: "too big" for p in ps})
    assert left.is_dir()


def test_no_spike_script_deletes_a_folder_outright():
    for p in SPIKE.parent.glob("*.py"):
        assert "shutil.rmtree(" not in p.read_text(encoding="utf-8"), p.name
