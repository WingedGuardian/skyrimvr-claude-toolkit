"""Tests must not leave directories in the system temp dir.

On 2026-10-18 one machine held 14,018 leaked test directories and a single local run held ~25 GiB of mutation copies.
Those came from module-level `tempfile.mkdtemp` calls (one per pytest process, and the mutation gate starts one per
mutation) and from Node tests calling `fs.mkdtempSync` with no cleanup. This catches both shapes before they reach
anyone's disk: a module-level temp dir goes through conftest.scratch_dir, and a Node test temp dir goes through
that file's scratch().
"""
from __future__ import annotations

import ast
import re

from conftest import REPO

TEMP_CALLS = {"mkdtemp", "mkstemp"}


def _module_level_temp_calls(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    for node in tree.body:
        for sub in ast.walk(node) if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Expr)) else ():
            if isinstance(sub, ast.Call):
                fn = sub.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if name in TEMP_CALLS:
                    yield node.lineno


def test_no_python_test_makes_an_uncleaned_temp_dir_at_import():
    bad = [f"{p.relative_to(REPO)}:{line}" for p in sorted((REPO / "tests").glob("*.py"))
           for line in _module_level_temp_calls(p)]
    assert not bad, ("module-level temp dirs leak one directory per pytest process; use conftest.scratch_dir(prefix):\n"
                     + "\n".join(bad))


def test_module_level_check_can_fail(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text('import tempfile\nNOHOME = tempfile.mkdtemp(prefix="x-")\n', encoding="utf-8")
    assert list(_module_level_temp_calls(probe)) == [2]
    probe.write_text('def f():\n    return tempfile.mkdtemp()\n', encoding="utf-8")
    assert list(_module_level_temp_calls(probe)) == []


def test_no_node_test_makes_a_temp_dir_outside_its_cleanup_helper():
    bad = []
    for p in sorted(REPO.glob("tools/**/*.test.js")):
        text = p.read_text(encoding="utf-8")
        calls = len(re.findall(r"mkdtempSync\(", text))
        helper = re.search(r"function scratch\(prefix\) \{\s*const d = fs\.mkdtempSync\(", text)
        allowed = 1 if helper and "process.on('exit'" in text else 0
        if calls > allowed:
            bad.append(f"{p.relative_to(REPO)}: {calls - allowed} mkdtempSync call(s) outside scratch()")
    assert not bad, "\n".join(bad)


def _created_prefixes():
    """Every temp-dir prefix the toolkit creates directly under the system temp dir."""
    pats = [r"""scratch_dir\(\s*["']([^"']+)["']""", r"""scratch\(\s*'([^']+)'""",
            r"""mkdtemp\(\s*prefix=["']([^"']+)["']\s*(?:,\s*dir=os\.environ\.get\("RUNNER_TEMP"\)[^)]*\))?\)"""]
    found = {}
    files = list(REPO.glob("tools/**/*.py")) + list(REPO.glob("tests/*.py")) + list(REPO.glob("tools/**/*.js"))
    for p in sorted(files):
        if p.name == "test_temp_hygiene.py" or "node_modules" in p.parts:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for pat in pats:
            for m in re.finditer(pat, text):
                found.setdefault(m.group(1), str(p.relative_to(REPO)))
    return found


def test_every_temp_prefix_the_toolkit_creates_is_one_the_sweep_knows():
    import importlib.util
    spec = importlib.util.spec_from_file_location("housekeep_for_hygiene", REPO / "tools" / "housekeep.py")
    hk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hk)
    created = _created_prefixes()
    assert "skyrim-harness-" in created and "t11-nohome-" in created, "the prefix scan found nothing: fix the scan"
    missing = {k: v for k, v in created.items() if k not in hk.DEFAULTS["temp_prefixes"]}
    assert not missing, ("a leftover of these is never swept; add each to housekeep.DEFAULTS['temp_prefixes']:\n"
                         + "\n".join(f"  {k}  ({v})" for k, v in sorted(missing.items())))
