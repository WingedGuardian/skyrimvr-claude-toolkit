"""The CI ruff hard gate (`ruff check --select=E9,F63,F7,F82`, ci.yml) skips template slot fragments, and only those.

A slot fragment (`*.slots.py`, see tests/test_adapter_template.py) is spliced INTO tools/adapting/adapter_template.py,
which supplies the names the fragment uses (`json`). Linted alone it reports F821 on every one of them, so the gate was
red on master with every ruff version. The rule is principled, not a per-file list: ruff.toml excludes the naming
convention, and `force-exclude` makes it apply to the explicit file paths CI passes (ruff ignores excludes for explicit
paths otherwise). The twin proves the gate still catches a real undefined name in any other file.
"""
import shutil
import subprocess
import sys

import pytest
from conftest import REPO

GATE = ["check", "--no-cache", "--select=E9,F63,F7,F82", "--output-format", "concise"]
UNDEFINED = "def f(raw):\n    return json.loads(raw)\n"


def _ruff():
    for cmd in ([shutil.which("ruff")] if shutil.which("ruff") else [], [sys.executable, "-m", "ruff"]):
        if not cmd:
            continue
        try:
            if subprocess.run(cmd + ["--version"], capture_output=True, timeout=60).returncode == 0:
                return cmd
        except (OSError, subprocess.TimeoutExpired):
            continue
    pytest.skip("COULD NOT CHECK: ruff is not installed in the test runtime (requirements.lock installs it)")


def _gate(cwd, *paths):
    return subprocess.run(_ruff() + GATE + list(paths), cwd=str(cwd), capture_output=True, text=True, timeout=120)


def test_the_shipped_slot_fragments_pass_the_gate_as_ci_passes_them():
    fragments = sorted(p.relative_to(REPO).as_posix() for p in (REPO / "tools" / "adapting" / "examples").glob("*.slots.py"))
    assert len(fragments) >= 2, fragments   # the gate must have something to skip, or this proves nothing
    r = _gate(REPO, *fragments)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_gate_skips_a_slot_fragment_but_still_catches_a_real_undefined_name(tmp_path):
    shutil.copy(REPO / "ruff.toml", tmp_path / "ruff.toml")
    (tmp_path / "fragment.slots.py").write_text(UNDEFINED)
    (tmp_path / "real.py").write_text(UNDEFINED)
    r = _gate(tmp_path, "fragment.slots.py", "real.py")
    assert r.returncode == 1, r.stdout + r.stderr          # the twin: the gate can still go red
    assert "real.py" in r.stdout and "F821" in r.stdout, r.stdout
    assert "fragment.slots.py" not in r.stdout, r.stdout   # and the fragment alone is skipped


def test_the_gate_is_red_on_the_fragment_without_the_config(tmp_path):
    """Control: with no ruff.toml the same fragment IS reported, so the green above is the config's doing."""
    (tmp_path / "fragment.slots.py").write_text(UNDEFINED)
    r = _gate(tmp_path, "fragment.slots.py")
    assert r.returncode == 1 and "F821" in r.stdout, r.stdout
