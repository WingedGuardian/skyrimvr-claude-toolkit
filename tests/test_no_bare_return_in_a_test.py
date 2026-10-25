"""A test must never bail with a bare `return`: pytest counts it as a PASS.

Ported from the X4 toolkit (final sweep 2026-10-25). There, two load-order invariants returned early when no reference
tree was available and reported GREEN while asserting nothing, in the CI step that was the release gate. A skip is
counted and named (and this toolkit's skip ceiling watches the count); a return is invisible to both.

So the rule is mechanical: inside a `def test_*`, a `return` with no value is banned. Use `pytest.skip(reason)` or
`pytest.importorskip(...)` when the test cannot run, or an if/else when both branches assert. A `return <value>` is
left alone (pytest warns about those itself), and helper functions nested inside a test are exempt: a bare return is
ordinary control flow there.
"""
from __future__ import annotations

import ast

from conftest import REPO

SKIP_DIRS = {".git", "node_modules", ".pytest_cache", "__pycache__", ".venv"}


def _bare_returns_in_tests(tree: ast.AST):
    """Every bare `return` whose nearest enclosing function is a test."""
    out = []

    def walk(node, in_test):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, child.name.startswith("test_"))
                continue
            if isinstance(child, ast.Lambda):
                continue
            if in_test and isinstance(child, ast.Return) and child.value is None:
                out.append(child.lineno)
            walk(child, in_test)

    walk(tree, False)
    return out


def _test_files():
    for f in sorted(REPO.rglob("test_*.py")):
        if not SKIP_DIRS & set(f.relative_to(REPO).parts):
            yield f


def test_no_test_function_bails_with_a_bare_return():
    files = list(_test_files())
    # A floor, so a moved or renamed tests/ cannot turn this into a scan of nothing (186 files on 2026-10-25).
    assert sum(1 for f in files if f.parent == REPO / "tests") > 150, "the scan no longer sees tests/"
    offenders = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders += ["{}:{}".format(path.relative_to(REPO).as_posix(), n) for n in _bare_returns_in_tests(tree)]
    assert not offenders, ("a bare `return` inside a test is counted as a PASS; use pytest.skip(reason) or an "
                           "if/else so every path asserts:\n  " + "\n  ".join(offenders))


def test_the_guard_itself_can_go_red():
    """Without this, a walker that found nothing would look identical to a clean tree."""
    planted = ast.parse("def test_x():\n    if cond:\n        return\n    assert True\n")
    assert _bare_returns_in_tests(planted) == [3]


def test_a_nested_helper_is_not_a_test():
    exempt = ast.parse("def test_x():\n    def helper(v):\n        if not v:\n            return\n        return v\n"
                       "    assert helper(1) == 1\n")
    assert _bare_returns_in_tests(exempt) == []


def test_a_return_with_a_value_is_not_the_shape_this_bans():
    assert _bare_returns_in_tests(ast.parse("def test_x():\n    return 5\n")) == []
