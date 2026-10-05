"""The adapter template itself: parses on 3.8, is closed when unfilled, markers are unique."""
from __future__ import annotations

import ast
import subprocess
import sys

from conftest import REPO

TPL = REPO / "tools" / "adapting" / "adapter_template.py"
EXAMPLES = REPO / "tools" / "adapting" / "examples"


def test_template_and_examples_parse_on_python_3_8_and_are_ascii():
    files = [TPL, *sorted(EXAMPLES.glob("*.py"))]
    assert len(files) == 3, files
    for p in files:
        text = p.read_bytes().decode("ascii")          # raises on any non-ASCII byte
        ast.parse(text, feature_version=(3, 8))


def test_slot_markers_appear_exactly_once_in_the_template_and_every_example():
    files = [TPL, *sorted(EXAMPLES.glob("*.slots.py"))]
    assert len(files) == 3, files
    for p in files:
        t = p.read_text(encoding="ascii")
        assert t.count("# SLOTS-BEGIN") == 1 and t.count("# SLOTS-END") == 1, p.name
        assert t.index("# SLOTS-BEGIN") < t.index("# SLOTS-END"), p.name


def test_an_unfilled_template_refuses_every_call(tmp_path):
    """Closed before correct: no slot is filled, so even a benign call is blocked (exit 2 + stderr)."""
    root = tmp_path / "inst"
    (root / "tools" / "adapters" / "x").mkdir(parents=True)
    (root / "tools" / "skyguard").mkdir(parents=True)
    (root / "tools" / "skyguard" / "run.py").write_text("print('would allow')\n")
    adapter = root / "tools" / "adapters" / "x" / "adapter.py"
    adapter.write_text(TPL.read_text(encoding="ascii"), encoding="ascii", newline="\n")
    cp = subprocess.run([sys.executable, str(adapter)], input=b'{"anything": 1}', capture_output=True, timeout=60)
    assert cp.returncode == 2 and b"GUARD INERT" in cp.stderr and cp.stdout == b""
