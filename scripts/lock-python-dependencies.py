"""Freeze a pip --dry-run --report resolution using verified PyPI wheel hashes.

Never installs anything. See docs/harness-integrations.md for the deliberate refresh commands.
"""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request


def render(report, inputs):
    lines = ["# Generated from a pip dry-run report; all transitive versions and wheel hashes frozen.",
             "# Python 3.10/3.11; refresh: docs/harness-integrations.md",
             "# input-sha256: " + hashlib.sha256(inputs.replace(b"\r\n", b"\n")).hexdigest(), ""]
    for item in sorted(report["install"], key=lambda x: x["metadata"]["name"].lower()):
        metadata = item["metadata"]
        name, version = metadata["name"], metadata["version"]
        with urllib.request.urlopen("https://pypi.org/pypi/" + name + "/" + version + "/json", timeout=30) as response:
            release = json.load(response)
        hashes = sorted({file["digests"]["sha256"] for file in release["urls"]
                         if file["filename"].endswith(".whl") and not file.get("yanked")})
        if not hashes:
            raise ValueError("No non-yanked wheels for " + name + "==" + version)
        marker = ""
        if name.lower() in ("exceptiongroup", "tomli"):
            marker = '; python_version < "3.11"'
        if name.lower() in ("colorama", "pywin32"):
            marker = '; sys_platform == "win32"'
        lines.append(name + "==" + version + marker + " \\")
        lines.extend("    --hash=sha256:" + digest + (" \\" if i < len(hashes)-1 else "")
                     for i, digest in enumerate(hashes))
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    text = render(json.loads(Path(args.report).read_text(encoding="utf-8")), Path(args.input).read_bytes())
    Path(args.output).write_text(text, encoding="utf-8", newline="\n")
