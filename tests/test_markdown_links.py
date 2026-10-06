"""Deferred rows 22 and 51 (Plan 8 T6): CI's "Relative links in Markdown resolve" step, run locally.

The step was red on master for three plans (two plan docs at Plan 3, three links counted by Plan 7 Task 10) and nothing
in the ordinary suite noticed, because only CI ran it. This test runs the step's OWN script, read out of ci.yml, so the
two cannot drift: the docs are fixed, never the check.
"""
from __future__ import annotations

import re
import subprocess
import sys

from conftest import REPO

STEP = "- name: Relative links in Markdown resolve"


def _step_script() -> str:
    text = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    i = text.index(STEP)
    m = re.search(r"python - <<'PY'\n(.*?)\n[ \t]*PY\n", text[i:], re.S)
    assert m, "the CI step no longer runs an inline Python script"
    lines = m.group(1).split("\n")
    indent = min(len(ln) - len(ln.lstrip()) for ln in lines if ln.strip())
    return "\n".join(ln[indent:] for ln in lines) + "\n"


def _run(cwd) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", _step_script()], cwd=str(cwd), capture_output=True, text=True,
                          timeout=120)


def test_every_relative_markdown_link_resolves():
    r = _run(REPO)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]


def test_the_check_can_fail(tmp_path):
    (tmp_path / "doc.md").write_text("see [x](missing.md) and [ok](doc.md)\n", encoding="utf-8")
    r = _run(tmp_path)
    assert r.returncode == 1 and "broken link [x](missing.md)" in r.stdout, r.stdout
