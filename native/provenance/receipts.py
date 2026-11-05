#!/usr/bin/env python3
"""Bind a consumer's source, config and generated inputs to its built outputs.

A test result counts only for the exact inputs and outputs it ran against. Promoted from the Delirium probe's
`receipts.py`, then extended: outputs are bound at build time and re-checked after the run.

    from receipts import inputs_digest, record_configure, record_build, require_current, result_code
"""
from __future__ import annotations

import hashlib
from pathlib import Path


def sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inputs_digest(paths, root) -> dict:
    """{relative path: sha256} over the given files. A missing input is an error, never an omission."""
    root = Path(root)
    return {Path(p).resolve().relative_to(root.resolve()).as_posix(): sha256(p) for p in sorted(map(str, paths))}


def record_configure(inputs: dict, generated_folder, generated_names) -> dict:
    folder = Path(generated_folder)
    return {"passed": True, "inputs": dict(inputs), "generated": {n: sha256(folder / n) for n in generated_names}}


def record_build(configured: dict, build_folder, output_names) -> dict:
    if not output_names:
        raise ValueError("a build with no named outputs proves nothing")
    folder = Path(build_folder)
    return {"passed": True, "inputs": dict(configured["inputs"]), "generated": dict(configured["generated"]),
            "outputs": {n: sha256(folder / n) for n in output_names}}


def require_current(current: dict, configured: dict, built: dict, folder) -> None:
    """Raise ValueError unless the current inputs, the configure and the build all agree with the files on disk."""
    folder = Path(folder)
    if not current:
        raise ValueError("no inputs: nothing binds this result")
    if not configured.get("passed") or configured.get("inputs") != current:
        raise ValueError("configure inputs differ")
    if not built.get("passed") or built.get("inputs") != current or built.get("generated") != configured.get("generated"):
        raise ValueError("matching successful build required")
    for name, digest in (configured.get("generated") or {}).items():
        if not (folder / name).is_file() or sha256(folder / name) != digest:
            raise ValueError("generated input changed: " + name)
    outputs = built.get("outputs") or {}
    if not outputs:
        raise ValueError("build outputs missing")
    for name, digest in outputs.items():
        if not (folder / name).is_file() or sha256(folder / name) != digest:
            raise ValueError("output changed: " + name)


def result_code(command_code: int, stable: bool) -> int:
    """A passing run whose inputs drifted while it ran still fails: post-run drift voids the result."""
    return command_code if command_code else (0 if stable else 1)
