"""tools/toolchain-check.sh -- the Spriggit clause: installed version must equal the pin.

The pin is `.config/dotnet-tools.json` (0.40.0, the version every YAML/serialize command in the
toolkit was written against). A newer Spriggit (0.41.0 ships only tools/net10.0) next to SDK 9
starts, prints a version, and cannot serialize -- the exact false green this script exists to catch.
These tests put a FAKE `spriggit` and a FAKE `dotnet` first on PATH and read the two spriggit lines.
Other tools in the script (python/node/java...) are real, so the overall RESULT is not asserted.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from conftest import BASH, REPO

TOOL = REPO / "tools" / "toolchain-check.sh"
PIN = json.loads((REPO / ".config" / "dotnet-tools.json").read_text(encoding="utf-8"))["tools"]["spriggit.cli"]["version"]


def _fake(tmp_path: Path, spriggit_version: str, sdks: list[str]) -> Path:
    b = tmp_path / "bin"
    b.mkdir()
    (b / "spriggit").write_text("#!/usr/bin/env bash\necho {}\n".format(spriggit_version), encoding="utf-8", newline="\n")
    sdk_lines = "\\n".join("{} [C:/fake/sdk]".format(s) for s in sdks)
    (b / "dotnet").write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "--list-sdks" ]; then printf "{}\\n"; exit 0; fi\n'
        'echo "{}"\n'.format(sdk_lines, sdks[-1] if sdks else "0.0.0"),
        encoding="utf-8", newline="\n")
    return b


def _run(tmp_path: Path, spriggit_version: str, sdks: list[str]):
    b = _fake(tmp_path, spriggit_version, sdks)
    env = dict(os.environ)
    env["PATH"] = str(b).replace("\\", "/") + os.pathsep + env.get("PATH", "")
    env["TOOLCHAIN_DOTNET"] = str(b / "dotnet").replace("\\", "/")  # the harness shadows `dotnet` by name
    p = subprocess.run([BASH, str(TOOL)], capture_output=True, text=True, env=env, timeout=120)
    return p.stdout


def _line(out: str, label: str) -> str:
    return next((ln for ln in out.splitlines() if label in ln), "")


def test_the_pin_is_0_40_0():
    assert PIN == "0.40.0"


def test_pinned_version_with_sdk_9_is_ok(tmp_path):
    out = _run(tmp_path, PIN, ["9.0.100"])
    assert " ok " in _line(out, "spriggit version"), out
    assert " ok " in _line(out, "spriggit entry point"), out
    assert "spriggit-version" not in out and "spriggit-entry-point" not in out


def test_newer_spriggit_on_sdk_9_is_degraded(tmp_path):
    out = _run(tmp_path, "0.41.0", ["9.0.100"])
    assert "MISMATCH" in _line(out, "spriggit version"), out
    assert "0.40.0" in out and "0.41.0" in out
    assert "CANNOT WORK" in _line(out, "spriggit entry point"), out
    assert "RESULT: DEGRADED" in out
    assert "spriggit-version" in out.split("RESULT:")[1]


def test_newer_spriggit_with_sdk_10_still_fails_the_version_pin(tmp_path):
    out = _run(tmp_path, "0.41.0", ["9.0.100", "10.0.100"])
    assert "MISMATCH" in _line(out, "spriggit version"), out
    assert " ok " in _line(out, "spriggit entry point"), out
    assert "spriggit-version" in out.split("RESULT:")[1]


def test_pinned_version_with_only_sdk_8_cannot_work(tmp_path):
    out = _run(tmp_path, PIN, ["8.0.400"])
    assert " ok " in _line(out, "spriggit version"), out
    assert "CANNOT WORK" in _line(out, "spriggit entry point"), out
    assert "spriggit-entry-point" in out.split("RESULT:")[1]


def test_a_version_suffix_does_not_hide_a_mismatch(tmp_path):
    out = _run(tmp_path, "0.40.1+abc123", ["9.0.100"])
    assert "MISMATCH" in _line(out, "spriggit version"), out
