#!/usr/bin/env python3
"""Read-only ReSaver Plus reports through the same pinned local build the native backend uses.

Examples:
  python tools/resaver-plus.py report <file.ess> --top 20
  python tools/resaver-plus.py compare <before.ess> <after.ess>
  python tools/resaver-plus.py decompile <script.pex>
  python tools/resaver-plus.py disassemble <script.pex>

Needs a build from `python tools/resaver-plus/build.py` (its BUILD-INFO.json pins the executable's SHA-256). This
adapter exposes no edits, cleaning, output-file switch or GUI. Use `bash tools/resaver-cli.sh` with
RESAVER_BACKEND=native for protected new-output edits.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import resaver_native  # noqa: E402


def input_file(value: str, extension: str) -> str:
    path = Path(value).resolve(strict=True)
    if not path.is_file() or path.suffix.lower() != extension:
        raise ValueError(f"Expected an existing {extension} file")
    return str(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report", help="Read the analysis report", allow_abbrev=False)
    report.add_argument("input")
    report.add_argument("--top", type=int, default=20)
    compare = commands.add_parser("compare", help="Read differences between two files", allow_abbrev=False)
    compare.add_argument("before")
    compare.add_argument("after")
    for name in ("decompile", "disassemble"):
        command = commands.add_parser(name, help="Read compiled Papyrus as text", allow_abbrev=False)
        command.add_argument("input")
    args = parser.parse_args(argv)
    try:
        if args.command == "report":
            if not 1 <= args.top <= 1000:
                raise ValueError("--top must be between 1 and 1000")
            native = [input_file(args.input, ".ess"), "--top", str(args.top)]
        elif args.command == "compare":
            native = [input_file(args.before, ".ess"), "--compare", input_file(args.after, ".ess")]
        else:
            native = [input_file(args.input, ".pex")]
            if args.command == "decompile":
                native.append("--psc")
        binary, _sha = resaver_native.executable()
        return subprocess.run([*resaver_native._launcher(), str(binary), *native], check=False,
                              timeout=resaver_native.TIMEOUT).returncode
    except (OSError, ValueError, KeyError, resaver_native.Refused, subprocess.SubprocessError) as exc:
        print(f"ReSaver Plus: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
