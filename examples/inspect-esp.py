#!/usr/bin/env python3
# ESP Inspection Script -- CONTAINER SIDE, READ-ONLY (Python)
#
# Serializes a plugin to YAML with Spriggit and summarizes its record groups.
# Works entirely from the read-only SKYRIM_MODS_DIR mount -- no Windows DLL,
# no MO2 VFS, no running game required.
#
# Usage (inside the devcontainer):
#   python examples/inspect-esp.py /skyrim/mods/MyMod/MyMod.esp
#
# The SKYRIM_MODS_DIR env var is set automatically in the devcontainer.
# Requires: spriggit (dotnet tool restore -- see .config/dotnet-tools.json)
#
# There is no "esplugin" package on PyPI (esplugin is a Rust crate with no
# Python bindings) -- Spriggit is the container-side ESP tool for this
# toolkit; see docs/container-vs-windows.md.

import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Must match .config/dotnet-tools.json and the version CLAUDE.md's Spriggit workflow uses --
# --PackageVersion is REQUIRED whenever --PackageName is set, or Spriggit exits non-zero.
SPRIGGIT_PACKAGE_VERSION = "0.40.0"


def inspect_plugin(esp_path: Path) -> int:
    print(f"=== ESP Inspection: {esp_path.name} ===\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            [
                "dotnet", "tool", "run", "spriggit", "serialize",
                "--InputPath", str(esp_path),
                "--OutputPath", tmpdir,
                "--GameRelease", "SkyrimSE",
                "--PackageName", "Spriggit.Yaml",
                "--PackageVersion", SPRIGGIT_PACKAGE_VERSION,
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            print(f"Spriggit failed:\n{result.stderr.strip()}")
            print("(Has 'dotnet tool restore' been run? Is spriggit.cli in .config/dotnet-tools.json?)")
            return 1

        yaml_root = Path(tmpdir)
        record_dirs = sorted(d for d in yaml_root.iterdir() if d.is_dir())

        print("--- Record Groups (Spriggit) ---")
        if not record_dirs:
            print("  No record groups found.")
            return 0

        for group_path in record_dirs:
            yaml_files = list(group_path.glob("**/*.yaml"))
            print(f"  {group_path.name:<12} {len(yaml_files)} record(s)")

        first = next((d for d in record_dirs if list(d.glob("**/*.yaml"))), None)
        if first is not None:
            samples = sorted(first.glob("**/*.yaml"))[:3]
            print(f"\n  Sample from [{first.name}]:")
            for f in samples:
                print(f"    {f.relative_to(yaml_root)}")
            remaining = len(list(first.glob("**/*.yaml"))) - len(samples)
            if remaining > 0:
                print(f"    ... and {remaining} more")

    print()
    print("Tip: to inspect the full record YAML, run Spriggit directly:")
    print(f'  dotnet tool run spriggit serialize \\')
    print(f'    --InputPath "{esp_path}" \\')
    print(f'    --OutputPath ./output/{esp_path.stem} \\')
    print(f'    --GameRelease SkyrimSE \\')
    print(f'    --PackageName Spriggit.Yaml \\')
    print(f'    --PackageVersion {SPRIGGIT_PACKAGE_VERSION}')
    print()
    print("=== Inspection complete ===")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        mods_dir = os.environ.get("SKYRIM_MODS_DIR", "/skyrim/mods")
        print("Usage: python examples/inspect-esp.py <path-to-esp>")
        print(f"  Mod plugins are mounted at: {mods_dir}/<mod-name>/<ModName>.esp")
        sys.exit(1)

    esp = Path(sys.argv[1])
    if not esp.exists():
        print(f"ERROR: File not found: {esp}")
        sys.exit(1)
    if esp.suffix.lower() not in {".esp", ".esm", ".esl"}:
        print(f"WARNING: {esp.name} doesn't look like a plugin file -- continuing anyway")

    sys.exit(inspect_plugin(esp))
