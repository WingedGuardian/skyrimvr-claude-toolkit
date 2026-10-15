"""Cheap release preflight for stale mutation metadata before running its children.

Symbol checks do not qualify parameter IDs or mutant behavior; the mutation
runner still requires an actual baseline and a pytest assertion failure.
"""
import ast
import io
from pathlib import Path

from conftest import REPO
from test_mutations import MUTATIONS


def test_all_mutation_source_anchors_are_unique_and_python_mutants_compile():
    cache, problems = {}, []
    for row in MUTATIONS:
        relative, find, replace, _test = row.values
        if relative not in cache:
            path = REPO / relative
            if path.is_file():
                with io.open(path, encoding="utf-8", newline="") as stream:
                    cache[relative] = stream.read()
            else:
                cache[relative] = None
        source = cache[relative]
        count = source.count(find) if source is not None else 0
        if count != 1:
            problems.append((row.id, relative, "anchor count", count))
        elif relative.endswith(".py"):
            try:
                compile(source.replace(find, replace), relative, "exec")
            except SyntaxError as exc:
                problems.append((row.id, relative, "mutant syntax", str(exc)))
    assert not problems, problems


def test_mutation_anchor_test_files_and_base_symbols_exist():
    cache, problems = {}, []
    for row in MUTATIONS:
        selector = row.values[3]
        parts = selector.split("::")
        path = REPO / parts[0]
        if not path.is_file():
            problems.append((row.id, selector, "missing test module"))
            continue
        if len(parts) == 1:
            continue
        if path not in cache:
            tree = ast.parse(path.read_bytes())
            cache[path] = {node.name for node in ast.walk(tree)
                           if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        for part in parts[1:]:
            name = part.split("[", 1)[0]
            if name and name not in cache[path]:
                problems.append((row.id, selector, "missing base symbol", name))
    assert not problems, problems
